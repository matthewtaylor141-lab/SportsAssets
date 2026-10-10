"""CAPITAL-CRITICAL: THE PAPER PASS BOUND, STATED EXACTLY (RC6.3c pass-hardening,
N2 / N3 / N4).

RC6.3b bounded every step by the pass time left (HARD_TIMEOUT_S less a reserve
for the record) so that a hung step could not erase the pass. Independent
reviewers of that fix found three gaps; this file proves each closed on a real
Postgres through run_once / paper_pass, and states what is guaranteed -- and
what is not.

WHAT IS GUARANTEED: a step that ends when it is cancelled is cut at the steps'
bound; the pass then records paper_session_health and the heartbeat, names
every cut and skipped step, and releases its advisory lock. A step that can owe
an ENTER (Derek's order sequence is shielded, so a cut does not return at once;
paper_derek.owed_enter_overrun_bound_s() = 32 s with the defaults) is cut that
much EARLIER and is not STARTED with less than that left: it ends by the steps'
bound, so the record keeps its whole reserve.

WHAT IS NOT: a step that does not end on cancellation (it swallows the cancel,
keeps work running on the pass connection in a shielded task, or blocks the
event loop), an owed ENTER whose sequence outlives even Derek's published bound
(the connection is terminated), and a record that hangs. HARD_TIMEOUT_S, in
run_once, is the last resort for those: it cuts the whole pass and the heartbeat
names the step in flight. test_the_claim_is_stated_exactly pins the words.

  N2  An owed ENTER near the step bound overran the 10 s record reserve by up
      to ~32 s, so run_once's last-resort timeout cut the WHOLE pass: no
      paper_session_health row (reviewer probe test_p2b: the completed-game
      decision owed 4 s before the bound, pass cut at 40 s, `ran=false`).
      A step that can owe an ENTER is not started without time for it, is cut
      earlier by the overrun bound, and is named
      PAPER_STEP_NOT_STARTED_ENTER_OVERRUN_WOULD_EXCEED_RESERVE when skipped;
      HARD_TIMEOUT_S is not raised and an ENTER that is owed still ends in an
      order or a named abandonment (ENTER_ORDER_ABANDONED).
  N3  Only a CUT step had its connection looked at. A step that raised after
      BEGIN left an aborted transaction (the record then failed with
      InFailedSQLTransactionError, `HEALTH`), and one that returned with a
      transaction open ran the next steps, and the record, inside it: all of
      it silently rolled back at the final unlock. After ANY step the pass now
      rolls back what the step left and names it (PAPER_STEP_LEFT_A_
      TRANSACTION_OPEN) under the step's own name.
  N4  The coverage step never raises: a run cut at its budget came back as
      {"error": COVERAGE_RUN_EXCEEDED_ITS_BUDGET}, and paper_session_health
      counted no error. It is now also named in the pass `errors` (the step's
      own result is unchanged).

Tests marked FAILS ON THE BASE fail on 5979416f / f971d665 (behaviourally);
the others pin what is kept. SYNTHETIC steps and rows on a scratch test
database; no venue, no order authority.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import pathlib
import textwrap
import time
import warnings

import asyncpg
import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets import canonical_components as CC
from sportsassets import open_position_canon as OPC
from sportsassets.agents import coverage_integrity as COV
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_explore as PEX
from sportsassets.agents import paper_maker as PMK
from sportsassets.agents import paper_runtime as PRT

from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests.rc63b_cov_support import DAY, NOW, clean
from tests.test_rc63_one_paper_decision_through_every_duty import (  # noqa: F401
    CG, LEVELS, P_PIN, _clean_allie_inputs, _nosleep, _seed_allie_inputs,
    cg_with_canonical_hooks)

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
#: a guard so a candidate that cannot bound a pass FAILS instead of hanging
GUARD_S = 60.0

NEW_CODE = "PAPER_STEP_NOT_STARTED_ENTER_OVERRUN_WOULD_EXCEED_RESERVE"
LEFT_TX = "PAPER_STEP_LEFT_A_TRANSACTION_OPEN"
RETURNED_ERROR = "PAPER_STEP_RETURNED_AN_ERROR"


async def _ok(conn, ctx):
    return {"n": 1}


async def _pool_and_getter():
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=3)

    async def get_pool():
        return pool
    return pool, get_pool


async def _heartbeat(conn) -> dict:
    v = await conn.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                            PRT.HEARTBEAT_KEY)
    return json.loads(v) if isinstance(v, str) else dict(v or {})


async def _lock_is_free() -> bool:
    other = await asyncpg.connect(H.DSN)
    try:
        got = await other.fetchval("SELECT pg_try_advisory_lock($1)",
                                   PRT.ADVISORY_LOCK_KEY)
        if got:
            await other.execute("SELECT pg_advisory_unlock($1)",
                                PRT.ADVISORY_LOCK_KEY)
        return bool(got)
    finally:
        await other.close()


async def _run_once(get_pool, a, steps, **kw):
    return await asyncio.wait_for(
        PRT.run_once(get_pool, trigger="TEST_RC63C", force=True,
                     account_id=a["account_id"], config=a["config"],
                     fee_fn=H.zero_fee, steps=steps, **kw), GUARD_S)


@pytest.fixture
def scaled_pass(monkeypatch):
    """A 14 s pass with 2 s kept for the record (steps' bound 12 s), and
    Derek's owed-ENTER bounds scaled to match: grace 6 s, abandon wait 0.5 s,
    terminate wait 0.25 s -> owed_enter_overrun_bound_s() = 6 + 3 x 0.5 +
    2 x 0.25 = 8 s, so an ENTER-owing step is cut at 4 s of the 12."""
    monkeypatch.setattr(PRT, "HARD_TIMEOUT_S", 14.0)
    monkeypatch.setattr(PRT, "PASS_RECORD_RESERVE_S", 2.0)
    monkeypatch.setattr(PRT, "CONNECTION_RESET_TIMEOUT_S", 2.0)
    monkeypatch.setattr(PD, "ENTER_ORDER_GRACE_S", 6.0)
    monkeypatch.setattr(PD, "ENTER_ABANDON_WAIT_S", 0.5)
    monkeypatch.setattr(PD, "ENTER_TERMINATED_WAIT_S", 0.25)
    assert PD.owed_enter_overrun_bound_s() == 8.0
    return 14.0, 2.0, 8.0


# ═════════════════════════════════════════════════════════════════════
# THE PINS (no database)
# ═════════════════════════════════════════════════════════════════════

def _called_names(fn) -> set:
    """The names a function CALLS (comments and strings do not count)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def test_the_new_codes_are_named():
    assert PRT.R_STEP_NOT_STARTED_ENTER_OVERRUN == NEW_CODE
    assert PRT.R_STEP_LEFT_TRANSACTION == "PAPER_STEP_LEFT_A_TRANSACTION_OPEN"
    assert PRT.R_STEP_RETURNED_ERROR == "PAPER_STEP_RETURNED_AN_ERROR"


def test_hard_timeout_is_not_raised_and_the_holdback_is_derek_s_published_bound():
    assert PRT.HARD_TIMEOUT_S == 90.0
    assert PRT.PASS_RECORD_RESERVE_S == 10.0
    assert PD.owed_enter_overrun_bound_s() == 32.0
    assert PRT.enter_overrun_holdback_s() == PD.owed_enter_overrun_bound_s()
    d = PRT.describe()
    assert d["enter_overrun_holdback_s"] == 32.0
    assert d["steps_bound_s"] == 80.0
    assert d["enter_owing_steps_bound_s"] == 48.0
    assert d["enter_owing_steps"] == sorted(PRT.ENTER_OWING_STEPS)
    # the pass budget (Derek's candidate loops stop at it) plus the most the
    # held checkpoint can move it still fit inside the EARLIER cut of an
    # ENTER-owing step: in the default configuration the holdback does not
    # shorten any decision loop
    budget = float(S.default_config()["cadence"]["pass_budget_s"])
    assert budget + PRT.HELD_IN_PASS_MAX_S < d["enter_owing_steps_bound_s"]


def test_the_enter_owing_steps_are_the_five_decision_steps(monkeypatch):
    """EVERY default step that can owe an ENTER is in ENTER_OWING_STEPS, and
    the others are not: found by what they do, not by their names."""
    monkeypatch.setenv("PAPER_BENCHMARK", "on")
    steps = dict(PRT.default_steps())
    assert PRT.ENTER_OWING_STEPS == frozenset({
        "derek", "benchmark", "benchmark_completed_game", "maker_entry",
        "exploration"})
    # each is the module's decision step (PB.step on a policy / decide fn)
    assert steps["derek"] is PD.step
    assert steps["benchmark"] is PB.step
    assert steps["benchmark_completed_game"] is PB.step_completed_game
    assert steps["maker_entry"] is PMK.step
    assert steps["exploration"] is PEX.step
    for name in PRT.ENTER_OWING_STEPS:
        assert PRT.owes_enter(name)
    # no other default step calls the owed-ENTER machinery or a decision fn
    machinery = {"owed_order", "bounded_decision", "decide_one",
                 "decide_valuation", "_decide_paper_strategies"}
    for name, fn in steps.items():
        if name in PRT.ENTER_OWING_STEPS:
            continue
        assert not PRT.owes_enter(name)
        assert not (_called_names(fn) & machinery), (name, fn)
    # cash_fallback records CASH rows for strategies that entered nothing: it
    # opens no ENTER (the lane's brief named it; what it does decides)
    assert not PRT.owes_enter("cash_fallback")
    assert "record_cash" in _called_names(steps["cash_fallback"])


def test_only_the_known_modules_call_the_owed_enter_machinery():
    """A new module that runs an owed ENTER fails here until its step is
    added to ENTER_OWING_STEPS (and this list)."""
    callers = {}
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            nm = f.id if isinstance(f, ast.Name) else (
                f.attr if isinstance(f, ast.Attribute) else None)
            if nm in ("owed_order", "bounded_decision"):
                callers.setdefault(path.relative_to(ROOT).as_posix(),
                                   set()).add(nm)
    assert set(callers) == {
        "agents/paper_benchmark.py",   # PB.step (+ completed game), hook
        "agents/paper_derek.py",       # PD.decide_one
        "agents/paper_explore.py",     # exploration
        "agents/paper_maker.py",       # maker entry
        "agents/paper_runtime.py",     # the per-valuation hook (not a step)
    }, callers


def test_the_claim_is_stated_exactly():
    """The module's words say what is guaranteed AND what is not: the RC6.3b
    wording ("no single step can now prevent the pass from recording itself")
    was wider than the code."""
    src = inspect.getsource(PRT)
    assert "NO SINGLE STEP CAN NOW PREVENT" not in src
    assert "WHAT IS GUARANTEED, AND WHAT IS NOT" in src
    for phrase in ("THAT HOLDS FOR A STEP THAT ENDS WHEN IT IS CANCELLED",
                   "AN OWED ENTER", "A STEP THAT DOES NOT END ON CANCELLATION",
                   "THE RECORD ITSELF", "AFTER EVERY STEP"):
        assert phrase in src, phrase


def test_returned_error_is_the_coverage_steps_only_and_changes_nothing():
    f = PRT._returned_error
    timed_out = {"ran": False, "timed_out": True,
                 "error": COV.R_RUN_TIMED_OUT,
                 "why": COV.R_RUN_TIMED_OUT + ": run() was cut at its 1.5s"}
    got = f("audrey_coverage", timed_out)
    assert got.startswith(RETURNED_ERROR + ": ")
    assert COV.R_RUN_TIMED_OUT in got
    assert "run() was cut at its 1.5s" in got         # the step's own words
    # a failed run, an unreadable watermark (any exception the step caught)
    assert COV.R_RUN_FAILED in f("audrey_coverage", {
        "ran": False, "error": COV.R_RUN_FAILED, "why": "x"})
    assert "InterfaceError" in f("audrey_coverage", {
        "ran": False, "error": "InterfaceError: connection is closed"})
    # a window that could not be written
    w = f("audrey_coverage", {"ran": True, "errors": {
        "UTC:2026-03-13": "UniqueViolationError: x"}})
    assert "window errors" in w and "UTC:2026-03-13" in w
    # not errors: healthy, not due (even backed off), no pass time
    assert f("audrey_coverage", {"ran": True, "snapshots": 4, "alerts": 0,
                                 "errors": {}}) is None
    assert f("audrey_coverage", {"ran": False, "why": "NOT_DUE",
                                 "backed_off": COV.R_RUN_TIMED_OUT}) is None
    assert f("audrey_coverage", {"ran": False,
                                 "why": COV.R_NO_PASS_TIME}) is None
    assert f("audrey_coverage", None) is None
    # only the steps registered: another step's "error" key is its own
    assert f("derek", timed_out) is None


# ═════════════════════════════════════════════════════════════════════
# N2 · AN ENTER-OWING STEP IS NOT STARTED WITHOUT TIME FOR ITS ENTER
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_step_that_can_owe_an_enter_is_not_started_with_too_little_time_and_is_named(
        monkeypatch):
    """THE PRODUCTION CONSTANTS (90 s, 10 s reserve, 32 s overrun): a pass that
    reaches `derek` with less than 32 s (+ 1 s) left for steps does not start
    it; the other steps run, and the pass records."""
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "ph_n2a", now=H.T0)
        monkeypatch.setattr(PRT, "HARD_TIMEOUT_S", 40.0)   # steps' bound 30 s
        started = []

        async def derek(c, ctx):
            started.append("derek")
            return {"decisions_recorded": 1}

        async def benchmark(c, ctx):
            started.append("benchmark")
            return {}

        async def settle(c, ctx):
            started.append("settle")
            return {"n": 1}

        res = await _run_once(get_pool, a, [("first", _ok), ("derek", derek),
                                            ("benchmark", benchmark),
                                            ("settle", settle)])
        assert started == ["settle"], "ENTER-owing steps are not started"
        assert res["ran"] is True
        assert res["skipped_steps"] == {"derek": NEW_CODE,
                                        "benchmark": NEW_CODE}
        for name in ("derek", "benchmark"):
            assert res["errors"][name].startswith(NEW_CODE + ": ")
            assert "32.0s of it held back" in res["errors"][name]
        assert sorted(res["steps"]) == ["first", "settle"]
        assert res["exceeded_step"] is None
        assert res["pass_time"]["enter_owing_steps_bound_s"] == -2.0
        h = await S.health(conn, a["session_id"])
        assert h["passes"] == 1 and h["errors"] == 1
        assert NEW_CODE in (h["last_error"] or "")
        assert h["last_pass"]["skipped_steps"] == res["skipped_steps"]
        hb = await _heartbeat(conn)
        assert hb["ran"] is True and hb["skipped_steps"] == res["skipped_steps"]
        assert await _lock_is_free()
    finally:
        await pool.close()
        await conn.close()


@pg
async def test_an_enter_owing_step_with_the_time_starts_and_is_given_the_steps_bound_less_the_holdback(
        scaled_pass):
    hard, reserve, holdback = scaled_pass
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "ph_n2b", now=H.T0)
        room = {}

        def probe(name):
            async def step(c, ctx):
                room[name] = ctx["step_deadline"] - time.monotonic()
                return {"n": 1}
            return step

        res = await _run_once(get_pool, a, [
            ("books", probe("books")), ("derek", probe("derek")),
            ("cash_fallback", probe("cash_fallback")),
            ("exploration", probe("exploration")),
            ("xavier", probe("xavier"))])
        assert res["ran"] is True and res["errors"] == {}
        steps_bound = hard - reserve
        # the others keep the whole steps' bound ...
        for name in ("books", "cash_fallback", "xavier"):
            assert steps_bound - 0.5 < room[name] <= steps_bound, (name, room)
        # ... an ENTER-owing step is cut that much earlier
        for name in ("derek", "exploration"):
            assert steps_bound - holdback - 0.5 < room[name] <= \
                steps_bound - holdback, (name, room)
        assert res["pass_time"]["steps_bound_s"] == steps_bound
        assert res["pass_time"]["enter_owing_steps_bound_s"] == \
            steps_bound - holdback
    finally:
        await pool.close()
        await conn.close()


@pg
async def test_an_enter_owed_near_the_bound_no_longer_takes_the_whole_pass_with_it(
        scaled_pass):
    """THE REVIEWER'S SHAPE, scaled (probe test_p2b: an ENTER owed 4 s before
    the step bound; the pass was cut at the hard timeout and `ran=false`).
    FAILS ON THE BASE: `derek` owes its ENTER 3 s before the base's cut
    (12 s), whose order sequence runs on for the rest of its 6 s grace -- to
    15 s, past the 14 s hard timeout -- and the whole pass is cut: no
    paper_session_health row. Here the step is cut at 4 s, before its ENTER is
    owed; the pass records."""
    hard, reserve, holdback = scaled_pass
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "ph_n2c", now=H.T0)

        async def hang(progress):
            progress["stage"] = "HUNG_IN_THE_ALLIE_READ"
            await asyncio.sleep(3600)

        async def derek(c, ctx):
            await asyncio.sleep(9.0)          # owes its ENTER at 9 s of 12
            return await PD.owed_order(
                c, ctx, decision_id="ph-n2c-d1", strategy="PH_TEST",
                sequence=hang)

        t0 = time.time()
        res = await _run_once(get_pool, a, [("first", _ok), ("derek", derek),
                                            ("later", _ok)])
        took = time.time() - t0
        assert res["ran"] is True, res
        assert res.get("refusal") is None
        assert took < hard
        assert res["exceeded_step"] == "derek"
        assert res["errors"]["derek"].startswith(PRT.R_STEP_EXCEEDED)
        assert "held back for an owed ENTER" in res["errors"]["derek"]
        assert 3.5 <= res["step_elapsed_s"]["derek"] < 5.0
        assert "later" in res["steps"]           # the pass went on
        h = await S.health(conn, a["session_id"])
        assert h is not None and h["passes"] == 1
        hb = await _heartbeat(conn)
        assert hb["ran"] is True and hb["exceeded_step"] == "derek"
        assert await _lock_is_free()
    finally:
        await pool.close()
        await conn.close()


@pg
async def test_an_enter_owed_just_before_the_earlier_cut_still_ends_in_a_named_abandonment_inside_the_bound(
        scaled_pass):
    """THE ENTER GUARANTEE IS KEPT. `derek` owes its ENTER 0.8 s before its
    cut at 4 s; the sequence hangs. The cut does not stop it: it runs on for
    the rest of its grace, is cancelled and NAMED (ENTER_ORDER_ABANDONED), and
    the step has returned by the steps' bound -- with the whole reserve left
    for the record. (On the base the same ENTER also ends in a named
    abandonment, but the step is cut at 12 s, not 4: the bound assertions
    below fail there.)"""
    hard, reserve, holdback = scaled_pass
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "ph_n2d", now=H.T0)

        async def hang(progress):
            progress["stage"] = "HUNG_IN_THE_ALLIE_READ"
            await asyncio.sleep(3600)

        async def derek(c, ctx):
            await asyncio.sleep(3.2)
            return await PD.owed_order(
                c, ctx, decision_id="ph-n2d-d1", strategy="PH_TEST",
                sequence=hang)

        res = await _run_once(get_pool, a, [("first", _ok), ("derek", derek),
                                            ("later", _ok)])
        assert res["ran"] is True, res
        assert res["exceeded_step"] == "derek"
        # the step was held for the rest of the grace after its cut, no more:
        # cut at ~4 s, grace over at 3.2 + 6 = 9.2 s, back by the steps' bound
        assert 8.5 <= res["step_elapsed_s"]["derek"] <= hard - reserve
        assert res["elapsed_s"] < hard - 1.0
        # the ENTER ended in a NAMED abandonment, not in nothing
        f = await conn.fetchrow(
            "SELECT detail FROM paper_audrey_findings WHERE account_id=$1 "
            " AND kind=$2 AND subject=$3", a["account_id"],
            PD.F_ENTER_ORDER_ABANDONED, "ph-n2d-d1")
        assert f is not None
        d = json.loads(f["detail"]) if isinstance(f["detail"], str) \
            else dict(f["detail"])
        assert d["cause"] == PD.C_GRACE_EXCEEDED
        assert d["stage_reached"] == "HUNG_IN_THE_ALLIE_READ"
        h = await S.health(conn, a["session_id"])
        assert h is not None and h["passes"] == 1
        assert "later" in res["steps"], "the steps after it still ran"
        assert await _lock_is_free()
    finally:
        await pool.close()
        await conn.close()


# ── the same through the REAL decision steps (the reviewer's own probe) ──

async def _setup(conn, tag):
    await PL.purge_everything(conn)
    await PL.purge_research_models(conn)
    await _clean_allie_inputs(conn)
    if await conn.fetchval("SELECT count(*) FROM execmirror_control") == 0:
        await conn.execute("INSERT INTO execmirror_control DEFAULT VALUES")
    now = time.time() + 5.0
    acct = await PL.new_account(conn, tag, now=now)
    t = PL.Transport(now)
    v = await PL.valuation(conn, decided_at=now - 10, p_pin=P_PIN,
                           compatibility="INCOMPATIBLE")
    slug = v["slug"]
    await _seed_allie_inputs(conn, slug=slug, now=now)
    t.set(slug, offers=LEVELS, bids=[(0.48, 2000)])
    return now, acct, t, v, PL.client(t)


def _bounded_with_timeout(orig, timeout):
    async def bounded(conn, fn, *, timeout=timeout):
        return await orig(conn, fn, timeout=timeout)
    return bounded


async def _real_pass_with_a_hung_allie_read(monkeypatch, *, tag, hard,
                                            reserve, pre_sleep):
    """A full DEFAULT-steps pass on the completed-game policy whose decision
    hangs in Allie's exposure read (the reviewer's probe), after `pre_sleep`
    seconds of earlier steps."""
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        now, acct, t, v, client = await _setup(conn, tag)
        monkeypatch.setattr(CC, "COMPONENT_TIMEOUT_S", 3600.0)
        monkeypatch.setattr(CC, "_bounded", _bounded_with_timeout(
            CC._bounded, 3600.0), raising=True)
        monkeypatch.setattr(
            OPC, "OPEN_EXPOSURE_BOOK_SQL",
            "SELECT 0::numeric AS usd FROM (SELECT pg_sleep(3600)) s "
            "WHERE $1::text IS NULL OR true /* ph-allie-hang */")
        monkeypatch.setattr(PRT, "HARD_TIMEOUT_S", float(hard))
        monkeypatch.setattr(PRT, "PASS_RECORD_RESERVE_S", float(reserve))
        steps = list(PRT.default_steps())

        async def slow(c, ctx):
            await asyncio.sleep(pre_sleep)
            return {"slept": pre_sleep}
        k = [n for n, _ in steps].index("derek")
        steps.insert(k, ("slow_earlier_steps", slow))
        CC.reset_cache()
        t.t = max(t.t, float(now))
        t0 = time.time()
        res = await asyncio.wait_for(PRT.run_once(
            get_pool, trigger="TEST_RC63C_REAL", now=now,
            account_id=acct["account_id"], market_data=client,
            config=acct["config"], force=True, fee_fn=None, sleep=_nosleep,
            steps=steps), GUARD_S)
        out = {"took": time.time() - t0, "res": res, "acct": acct,
               "health": await S.health(conn, acct["session_id"]),
               "hb": await _heartbeat(conn),
               "lock_free": await _lock_is_free(),
               "enters": await conn.fetch(
                   "SELECT decision_id FROM paper_decisions WHERE "
                   "account_id=$1 AND verdict='ENTER'", acct["account_id"]),
               "orders": await conn.fetchval(
                   "SELECT count(*) FROM paper_orders WHERE account_id=$1",
                   acct["account_id"]),
               "abandoned": await conn.fetch(
                   "SELECT subject, detail FROM paper_audrey_findings WHERE "
                   "account_id=$1 AND kind=$2", acct["account_id"],
                   PD.F_ENTER_ORDER_ABANDONED),
               "active_hang": await conn.fetchval(
                   "SELECT count(*) FROM pg_stat_activity WHERE "
                   "state='active' AND query LIKE '%ph-allie-hang%' AND "
                   "pid <> pg_backend_pid()")}
        return out
    finally:
        await _clean_allie_inputs(conn)
        await pool.close()
        await conn.close()


@pg
async def test_the_reviewers_p2b_shape_through_the_real_steps_now_records_the_pass(
        cg_with_canonical_hooks, monkeypatch):
    """probe test_p2b: the PRODUCTION reserve (10 s of a 40 s pass); earlier
    steps use 26 s, so the completed-game decision would start 4 s before the
    steps' bound and hang in Allie's exposure read. FAILS ON THE BASE: the
    pass is cut at 40 s in `benchmark_completed_game` (`ran=false`, no
    paper_session_health row, no step finished). Now no ENTER-owing step is
    started with 4 s left: all five are named, the pass records, and nothing
    is left half-done (no ENTER row, no order, no hung statement)."""
    out = await _real_pass_with_a_hung_allie_read(
        monkeypatch, tag="phn2e", hard=40.0, reserve=10.0, pre_sleep=26.0)
    res = out["res"]
    assert res["ran"] is True, res
    assert res.get("refusal") is None
    assert out["took"] < 40.0
    assert sorted(res["skipped_steps"]) == sorted(PRT.ENTER_OWING_STEPS)
    assert set(res["skipped_steps"].values()) == {NEW_CODE}
    assert res["exceeded_step"] is None
    assert out["health"] is not None and out["health"]["passes"] == 1
    assert out["hb"]["ran"] is True
    assert out["lock_free"] is True
    # nothing half-done
    assert out["enters"] == [] and out["orders"] == 0
    assert out["abandoned"] == [] and out["active_hang"] == 0


@pg
async def test_a_real_completed_game_decision_that_has_the_time_still_ends_in_a_named_abandonment(
        cg_with_canonical_hooks, monkeypatch):
    """THE ENTER GUARANTEE THROUGH THE REAL STEPS (pins what is kept): scaled
    constants (30 s pass, 4 s reserve, Derek's bounds 5 / 0.5 / 0.25 s ->
    overrun bound 7 s) and 6 s of earlier steps: the completed-game decision
    STARTS, owes its ENTER and hangs in Allie's read; the step is cut at
    26 - 7 = 19 s and the ENTER -- recorded, without an order -- is NAMED
    (ENTER_ORDER_ABANDONED) at once. The pass records and the statement is
    cancelled on the server."""
    monkeypatch.setattr(PD, "ENTER_ORDER_GRACE_S", 5.0)
    monkeypatch.setattr(PD, "ENTER_ABANDON_WAIT_S", 0.5)
    monkeypatch.setattr(PD, "ENTER_TERMINATED_WAIT_S", 0.25)
    assert PD.owed_enter_overrun_bound_s() == 7.0
    out = await _real_pass_with_a_hung_allie_read(
        monkeypatch, tag="phn2f", hard=30.0, reserve=4.0, pre_sleep=6.0)
    res = out["res"]
    assert res["ran"] is True, res
    # the step is cut 7 s early; the ENTER-owing steps after it have less than
    # the overrun bound left and are named, not started
    assert set(res["skipped_steps"].values()) <= {NEW_CODE,
                                                  PRT.R_STEP_SKIPPED}
    assert res["skipped_steps"]["maker_entry"] == NEW_CODE
    assert res["exceeded_step"] == "benchmark_completed_game", res["errors"]
    assert "held back for an owed ENTER" in \
        res["errors"]["benchmark_completed_game"]
    assert out["health"]["passes"] == 1 and out["hb"]["ran"] is True
    assert out["lock_free"] is True
    assert len(out["enters"]) == 1 and out["orders"] == 0
    assert [r["subject"] for r in out["abandoned"]] == [
        out["enters"][0]["decision_id"]]
    assert out["active_hang"] == 0


# ═════════════════════════════════════════════════════════════════════
# N3 · AFTER ANY STEP, THE CONNECTION IS LOOKED AT
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_step_that_raises_inside_an_aborted_transaction_is_rolled_back_and_named_and_the_record_is_written():
    """FAILS ON THE BASE: the record fails with InFailedSQLTransactionError
    (`HEALTH`) and paper_session_health gets no row."""
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "ph_n3a", now=H.T0)
        saw = []

        async def aborts(c, ctx):
            await c.execute("BEGIN")
            try:
                await c.execute("SELECT 1/0")
            except Exception:                              # noqa: BLE001
                pass
            raise ValueError("left an aborted transaction")

        async def later(c, ctx):
            saw.append(c.is_in_transaction())
            return {"in_tx": c.is_in_transaction()}

        res = await _run_once(get_pool, a, [("first", _ok),
                                            ("aborts", aborts),
                                            ("later", later)])
        assert res["ran"] is True
        assert "HEALTH" not in res["errors"], res["errors"]
        assert res["errors"]["aborts"].startswith(
            "ValueError: left an aborted transaction | "
            + LEFT_TX + ": ")
        assert saw == [False], "the next step does not run inside it"
        h = await S.health(conn, a["session_id"])
        assert h is not None and h["passes"] == 1
        assert LEFT_TX in (h["last_error"] or "")
        assert await _lock_is_free()
    finally:
        await pool.close()
        await conn.close()


@pg
async def test_a_step_that_returns_with_a_transaction_open_is_rolled_back_and_named_never_silent():
    """FAILS ON THE BASE: no error at all -- the step's rows, the next step's
    rows and the pass record are all inside the leaked transaction and are
    rolled back at the final unlock (paper_session_health stays at 0)."""
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "ph_n3b", now=H.T0)
        key = "ph_n3b_%d" % int(time.time() * 1000)

        async def leaks(c, ctx):
            await c.execute("BEGIN")
            await c.execute("INSERT INTO ingestion_state (key, value) "
                            "VALUES ($1, '{}'::jsonb)", key + "_leaked")
            return {"leaked": True}

        async def later(c, ctx):
            # an ordinary autocommit write of the next step
            await c.execute("INSERT INTO ingestion_state (key, value) "
                            "VALUES ($1, '{}'::jsonb)", key + "_later")
            return {"in_tx": c.is_in_transaction()}

        res = await _run_once(get_pool, a, [("leaks", leaks),
                                            ("later", later)])
        assert res["ran"] is True
        assert res["steps"]["leaks"] == {"leaked": True}   # its result stands
        assert res["steps"]["later"] == {"in_tx": False}
        assert res["errors"] == {"leaks": res["errors"]["leaks"]}
        assert res["errors"]["leaks"].startswith(
            LEFT_TX + ": ")
        assert "rolled back" in res["errors"]["leaks"]
        # the leaked write was NOT kept; the next step's write IS
        assert await conn.fetchval(
            "SELECT count(*) FROM ingestion_state WHERE key=$1",
            key + "_leaked") == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM ingestion_state WHERE key=$1",
            key + "_later") == 1
        h = await S.health(conn, a["session_id"])
        assert h is not None and h["passes"] == 1 and h["errors"] == 1
        assert LEFT_TX in (h["last_error"] or "")
        assert await _lock_is_free()
        async with pool.acquire() as c2:
            assert not c2.is_in_transaction()
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key LIKE "
                           "'ph_n3b_%'")
        await pool.close()
        await conn.close()


@pg
async def test_a_step_that_closes_its_own_transaction_is_not_named():
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "ph_n3c", now=H.T0)
        key = "ph_n3c_%d" % int(time.time() * 1000)

        async def tidy(c, ctx):
            async with c.transaction():
                await c.execute("INSERT INTO ingestion_state (key, value) "
                                "VALUES ($1, '{}'::jsonb)", key)
            await c.execute("BEGIN")
            await c.execute("COMMIT")
            return {"ok": True}

        async def raises_in_manager(c, ctx):
            async with c.transaction():
                await c.execute("SELECT 1")
                raise RuntimeError("an ordinary failing step")

        res = await _run_once(get_pool, a, [("tidy", tidy),
                                            ("boom", raises_in_manager)])
        assert res["errors"] == {"boom": "RuntimeError: an ordinary failing "
                                         "step"}
        assert await conn.fetchval(
            "SELECT count(*) FROM ingestion_state WHERE key=$1", key) == 1
        h = await S.health(conn, a["session_id"])
        assert h["passes"] == 1 and h["errors"] == 1
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key LIKE "
                           "'ph_n3c_%'")
        await pool.close()
        await conn.close()


@pg
async def test_a_transaction_the_caller_held_before_the_pass_is_not_the_passs_to_end():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "ph_n3d", now=H.T0)
        tx = conn.transaction()
        await tx.start()
        try:
            res = await asyncio.wait_for(PRT.paper_pass(
                conn, account_id=a["account_id"], config=a["config"],
                force=True, fee_fn=H.zero_fee,
                steps=[("first", _ok), ("second", _ok)]), GUARD_S)
            assert res["ran"] is True and res["errors"] == {}, res["errors"]
            assert conn.is_in_transaction(), "the caller's transaction stands"
        finally:
            await tx.rollback()
    finally:
        await conn.close()


@pg
async def test_a_cut_step_is_still_named_by_the_cut_and_not_twice(monkeypatch):
    """The cut path is unchanged (RC6.3b): one name, PAPER_STEP_EXCEEDED_PASS_
    TIME, no second 'left a transaction' note on top of it."""
    monkeypatch.setattr(PRT, "HARD_TIMEOUT_S", 4.0)
    monkeypatch.setattr(PRT, "PASS_RECORD_RESERVE_S", 1.5)
    monkeypatch.setattr(PRT, "CONNECTION_RESET_TIMEOUT_S", 2.0)
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "ph_n3e", now=H.T0)

        async def leaks_and_hangs(c, ctx):
            await c.execute("BEGIN")
            await asyncio.sleep(3600)

        res = await _run_once(get_pool, a, [("leaks", leaks_and_hangs),
                                            ("later", _ok)])
        assert res["exceeded_step"] == "leaks"
        assert res["errors"]["leaks"].startswith(PRT.R_STEP_EXCEEDED)
        assert LEFT_TX not in res["errors"]["leaks"]
        assert "CONNECTION_RESET" not in res["errors"]
        assert (await S.health(conn, a["session_id"]))["passes"] == 1
    finally:
        await pool.close()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# N4 · A COVERAGE STEP THAT RETURNS AN ERROR IS NAMED IN THE PASS ERRORS
# ═════════════════════════════════════════════════════════════════════

@pytest.fixture
def cov_account(monkeypatch):
    """The coverage step runs on the MAIN paper account only: a scratch account
    stands in for it -- its id AND its key, so that the pass's own
    ensure_session finds the same account (rc63b's main_account patches only
    the id, which is enough for a direct step call but not for a pass)."""
    monkeypatch.setattr(COV, "MIN_RUN_BUDGET_S", 0.1, raising=False)

    async def make(conn):
        a = await H.new_account(conn, "phcov", now=NOW - 5 * DAY)
        monkeypatch.setattr(L, "ACCOUNT_ID", a["account_id"])
        monkeypatch.setattr(L, "ACCOUNT_KEY", a["account_id"].upper())
        return a
    return make


WEDGED = ("SELECT pg_sleep(3600), 'x'::text AS league, 1 AS n "
          "FROM (SELECT $1::float8 AS a, $2::float8 AS b) p")


async def _cov_pass(conn, a, at):
    return await asyncio.wait_for(PRT.paper_pass(
        conn, now=at, account_id=a["account_id"], config=a["config"],
        force=True, fee_fn=H.zero_fee,
        steps=[("audrey_coverage", COV.step)]), GUARD_S)


@pg
async def test_a_coverage_run_cut_at_its_budget_is_named_in_the_pass_errors_and_counted(
        cov_account, monkeypatch):
    """FAILS ON THE BASE: the step returns error=COVERAGE_RUN_EXCEEDED_ITS_
    BUDGET, the pass `errors` stays {} and paper_session_health.errors stays
    0 -- a pass whose coverage step timed out looked clean."""
    conn = await H.connect()
    try:
        await clean(conn)
        a = await cov_account(conn)
        monkeypatch.setattr(COV, "EVALUATED_SQL", WEDGED)
        monkeypatch.setattr(COV, "RUN_BUDGET_S", 1.5)
        monkeypatch.setattr(COV, "READ_STATEMENT_TIMEOUT_MS", 60_000)
        res = await _cov_pass(conn, a, NOW)
        assert res["ran"] is True
        own = res["steps"]["audrey_coverage"]
        # the step's OWN result is unchanged
        assert own["error"] == COV.R_RUN_TIMED_OUT
        assert own["ran"] is False and own["timed_out"] is True
        assert set(own) == {"ran", "timed_out", "error", "why", "snapshots",
                            "in_flight", "budget_s", "next_due_in_s",
                            "audrey_finding", "audrey_refusal"}
        # ... and the pass names it
        named = res["errors"]["audrey_coverage"]
        assert named.startswith(RETURNED_ERROR + ": "), named
        assert COV.R_RUN_TIMED_OUT in named
        h = await S.health(conn, a["session_id"])
        assert h["passes"] == 1 and h["errors"] == 1
        assert COV.R_RUN_TIMED_OUT in (h["last_error"] or "")

        # the next pass is NOT_DUE (backed off): not an error again
        res2 = await _cov_pass(conn, a, NOW + 60.0)
        assert res2["steps"]["audrey_coverage"]["why"] == "NOT_DUE"
        assert res2["steps"]["audrey_coverage"]["backed_off"] == \
            COV.R_RUN_TIMED_OUT
        assert res2["errors"] == {}
    finally:
        await clean(conn)
        await conn.close()


@pg
async def test_a_coverage_run_that_fails_or_cannot_write_a_window_is_named_too(
        cov_account, monkeypatch):
    conn = await H.connect()
    try:
        await clean(conn)
        a = await cov_account(conn)

        async def boom(c, **kw):
            raise ValueError("the funnel read failed")
        monkeypatch.setattr(COV, "run", boom)
        res = await _cov_pass(conn, a, NOW)
        assert res["steps"]["audrey_coverage"]["error"] == COV.R_RUN_FAILED
        assert res["errors"]["audrey_coverage"].startswith(
            RETURNED_ERROR + ": ")
        assert COV.R_RUN_FAILED in res["errors"]["audrey_coverage"]
        assert (await S.health(conn, a["session_id"]))["errors"] == 1

        await clean(conn)                       # watermark back to "due"

        async def window_errors(c, **kw):
            return {"ran": True, "snapshots": 2, "alerts": [],
                    "errors": {"UTC:2026-03-13": "UniqueViolationError: x"}}
        monkeypatch.setattr(COV, "run", window_errors)
        res = await _cov_pass(conn, a, NOW + 7200.0)
        assert res["steps"]["audrey_coverage"]["ran"] is True
        assert "window errors" in res["errors"]["audrey_coverage"]
        assert "UTC:2026-03-13" in res["errors"]["audrey_coverage"]
    finally:
        await clean(conn)
        await conn.close()


@pg
async def test_a_healthy_coverage_step_is_not_named(cov_account):
    conn = await H.connect()
    try:
        await clean(conn)
        a = await cov_account(conn)
        res = await _cov_pass(conn, a, NOW)
        assert res["steps"]["audrey_coverage"]["ran"] is True
        assert res["errors"] == {}
        assert (await S.health(conn, a["session_id"]))["errors"] == 0
    finally:
        await clean(conn)
        await conn.close()
