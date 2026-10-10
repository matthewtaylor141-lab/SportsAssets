"""CAPITAL-CRITICAL: THE PAPER PASS ALWAYS RECORDS ITSELF, WHATEVER A STEP DOES
(RC6.3b pass-stall).

Production, 2026-10-10 02:17Z onward (read-only readbacks): every paper pass
ended `ran=false, PAPER_PASS_RAISED_OR_TIMED_OUT, "TimeoutError: "` -- one
pass every ~2 minutes -- while paper_session_health's heartbeat stood at
02:17:23Z for hours. A 2-second trace of the pass connection showed each pass
running its steps for about 60 s, then ONE step (the coverage step's first
league statement) running 50+ s until run_once's HARD_TIMEOUT_S = 90 cancelled
the WHOLE pass. The steps after it never ran, `S.record_pass` and the
heartbeat digest never ran, and nothing said which step it was.

Proved here, on a real Postgres through run_once (the production path):

  * a step that hangs past the pass time left for steps is cancelled; the pass
    STILL writes paper_session_health and the heartbeat (ran=true, the hung
    step named PAPER_STEP_EXCEEDED_PASS_TIME, every later step named
    PAPER_STEP_SKIPPED_PASS_TIME_SPENT, elapsed_s present) and releases the
    pass advisory lock;
  * the server-side query a cancelled step was running is cancelled (no
    pg_sleep left active), the connection is usable for the record, and a
    transaction the step opened and did not close is rolled back (its rows
    are not committed by the record that follows);
  * the bound is derived from HARD_TIMEOUT_S less a fixed reserve for the
    record (HARD_TIMEOUT_S is not raised), the step order is unchanged, and a
    pass whose steps all end in time is recorded exactly as before;
  * the between-steps held checkpoint cannot run into the time kept for the
    record;
  * a pass the last-resort timeout still cuts names the step it was in.

SYNTHETIC steps on a scratch test database; no venue, no order authority.
"""
from __future__ import annotations

import asyncio
import json
import time

import asyncpg
import pytest

from sportsassets import bettor_paper_session as S
from sportsassets.agents import paper_runtime as PRT

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

#: the production step order at the RC6.3b candidate (PAPER_BENCHMARK unset);
#: the fix bounds the steps, it does not reorder them
PRODUCTION_STEP_ORDER = [
    "books", "simulate", "turnaround", "shadow_settlement",
    "profitability_fit", "profitability_quarantine",
    "counterfactual_settlement", "derek", "cash_fallback",
    "simulate_after_delay", "enter_backstop", "settle", "handoff", "xavier",
    "xavier_value_add", "xavier_work_queue", "equity", "audrey",
    "audrey_operations", "audrey_events", "learning", "audrey_coverage",
    "audrey_postmortems", "improvement_driver", "root_cause_clusters",
    "agent_work_queues", "lesson_usage", "agent_memory"]


@pytest.fixture
def tight_pass(monkeypatch):
    """A small pass: 4 s hard timeout, 1.5 s kept for the record -> steps
    must end within 2.5 s of the pass start."""
    monkeypatch.setattr(PRT, "HARD_TIMEOUT_S", 4.0)
    # (raising=False: the same fixture runs against the candidate that has
    # neither constant, so the behavioural tests fail there on behaviour)
    monkeypatch.setattr(PRT, "PASS_RECORD_RESERVE_S", 1.5, raising=False)
    monkeypatch.setattr(PRT, "CONNECTION_RESET_TIMEOUT_S", 2.0, raising=False)
    return 4.0, 1.5


async def _ok(conn, ctx):
    return {"n": 1}


async def _hang(conn, ctx):
    await asyncio.sleep(3600)


async def _pool_and_getter():
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=3)

    async def get_pool():
        return pool
    return pool, get_pool


async def _heartbeat(conn) -> dict:
    v = await conn.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                            PRT.HEARTBEAT_KEY)
    return json.loads(v) if isinstance(v, str) else dict(v or {})


async def _lock_is_free(dsn) -> bool:
    other = await asyncpg.connect(dsn)
    try:
        got = await other.fetchval("SELECT pg_try_advisory_lock($1)",
                                   PRT.ADVISORY_LOCK_KEY)
        if got:
            await other.execute("SELECT pg_advisory_unlock($1)",
                                PRT.ADVISORY_LOCK_KEY)
        return bool(got)
    finally:
        await other.close()


#: a guard so a candidate that cannot bound a step FAILS these tests (the pass
#: never returns on its own there) instead of hanging the suite
GUARD_S = 30.0


async def _run_once(get_pool, a, steps, **kw):
    return await asyncio.wait_for(
        PRT.run_once(get_pool, trigger="TEST_RC63B", force=True,
                     account_id=a["account_id"], config=a["config"],
                     fee_fn=H.zero_fee, steps=steps, **kw), GUARD_S)


# ═════════════════════════════════════════════════════════════════════
# THE PINS
# ═════════════════════════════════════════════════════════════════════

def test_the_step_order_is_unchanged(monkeypatch):
    monkeypatch.delenv("PAPER_BENCHMARK", raising=False)
    assert [n for n, _ in PRT.default_steps()] == PRODUCTION_STEP_ORDER


def test_the_bound_is_derived_from_the_hard_timeout_and_a_fixed_reserve():
    # HARD_TIMEOUT_S is not raised; the reserve is kept out of the steps' time
    assert PRT.HARD_TIMEOUT_S == 90.0
    assert 0 < PRT.PASS_RECORD_RESERVE_S < PRT.HARD_TIMEOUT_S / 2
    assert PRT.pass_steps_deadline(100.0) == \
        100.0 + PRT.HARD_TIMEOUT_S - PRT.PASS_RECORD_RESERVE_S
    d = PRT.describe()
    assert d["hard_timeout_s"] == PRT.HARD_TIMEOUT_S
    assert d["steps_bound_s"] == PRT.HARD_TIMEOUT_S - PRT.PASS_RECORD_RESERVE_S
    # the xavier in-pass worst case still fits inside the steps' time
    budget = float(S.default_config()["cadence"]["pass_budget_s"])
    assert budget + PRT.HELD_IN_PASS_MAX_S < \
        PRT.HARD_TIMEOUT_S - PRT.PASS_RECORD_RESERVE_S


def test_the_two_codes_are_named():
    assert PRT.R_STEP_EXCEEDED == "PAPER_STEP_EXCEEDED_PASS_TIME"
    assert PRT.R_STEP_SKIPPED == "PAPER_STEP_SKIPPED_PASS_TIME_SPENT"


# ═════════════════════════════════════════════════════════════════════
# A HUNG STEP STILL LEAVES THE PASS RECORDED (THE REGRESSION)
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_hung_step_still_writes_health_and_heartbeat_and_frees_the_lock(
        tight_pass):
    """FAILS on af40bea2: the hung step is cut by the 4 s hard timeout, the
    whole pass with it -- heartbeat `ran=false, TimeoutError`, no
    paper_session_health row, no step named, the later steps unrecorded."""
    hard, reserve = tight_pass
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "stall1", now=H.T0)
        steps = [("first", _ok), ("hang", _hang), ("later_a", _ok),
                 ("later_b", _ok)]
        t0 = time.time()
        res = await _run_once(get_pool, a, steps)
        took = time.time() - t0

        # the pass RAN and says exactly what happened
        assert res["ran"] is True, res
        assert res.get("refusal") is None
        assert took < hard, "the pass ended inside the hard timeout"
        assert res["exceeded_step"] == "hang"
        assert res["errors"]["hang"].startswith(PRT.R_STEP_EXCEEDED), \
            res["errors"]
        assert res["skipped_steps"] == {"later_a": PRT.R_STEP_SKIPPED,
                                        "later_b": PRT.R_STEP_SKIPPED}
        for name in ("later_a", "later_b"):
            assert res["errors"][name].startswith(PRT.R_STEP_SKIPPED)
        assert list(res["steps"]) == ["first"]
        assert res["elapsed_s"] is not None and res["elapsed_s"] < hard
        # the hung step took the time it had (2.5 s), not the pass
        assert 2.0 <= res["step_elapsed_s"]["hang"] < 3.4
        assert "later_a" not in res["step_elapsed_s"]
        assert res["pass_time"]["steps_bound_s"] == hard - reserve

        # THE HEARTBEAT (ingestion_state), written by run_once: ran=true
        hb = await _heartbeat(conn)
        assert hb["ran"] is True and hb["refusal"] is None
        assert hb["elapsed_s"] == res["elapsed_s"]
        assert hb["exceeded_step"] == "hang"
        assert hb["skipped_steps"] == res["skipped_steps"]
        assert hb["errors"]["hang"].startswith(PRT.R_STEP_EXCEEDED)
        assert time.time() - hb["written_at"] < 30

        # PAPER_SESSION_HEALTH: written by the pass, fresh, errors visible
        h = await S.health(conn, a["session_id"])
        assert h is not None and h["passes"] == 1 and h["errors"] == 1
        assert abs(h["heartbeat_at"] - res["at"]) < 1.0
        assert time.time() - h["heartbeat_at"] < 30
        assert PRT.R_STEP_EXCEEDED in (h["last_error"] or "")
        assert h["last_pass"]["exceeded_step"] == "hang"
        assert h["last_pass"]["skipped_steps"] == res["skipped_steps"]
        beat = h["recent_heartbeats"][-1]
        assert beat["ok"] is False and beat["elapsed_s"] == res["elapsed_s"]

        # the advisory lock is released
        assert await _lock_is_free(H.DSN)
    finally:
        await pool.close()
        await conn.close()


@pg
async def test_the_cancelled_query_is_cancelled_on_the_server_and_the_connection_is_usable(
        tight_pass):
    """The step's statement is a server-side pg_sleep: asyncpg cancels it, no
    active backend is left running it, and the connection takes the record."""
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "stall2", now=H.T0)

        async def hung_statement(c, ctx):
            await c.execute("SELECT pg_sleep(3600) /* rc63b-hung-step */")

        res = await _run_once(get_pool, a, [("first", _ok),
                                            ("slow_query", hung_statement),
                                            ("later", _ok)])
        assert res["ran"] is True and res["exceeded_step"] == "slow_query"
        assert "CONNECTION_RESET" not in res["errors"], res["errors"]
        assert "HEALTH" not in res["errors"], res["errors"]
        # the query is not running on the server
        active = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE state = 'active' "
            " AND query LIKE '%rc63b-hung-step%' AND pid <> pg_backend_pid()")
        assert active == 0
        h = await S.health(conn, a["session_id"])
        assert h["passes"] == 1
        assert await _lock_is_free(H.DSN)
        # the pooled connection is clean and usable afterwards
        async with pool.acquire() as c2:
            assert not c2.is_in_transaction()
            assert await c2.fetchval("SELECT 1") == 1
    finally:
        await pool.close()
        await conn.close()


@pg
async def test_a_transaction_the_cut_step_left_open_is_rolled_back_not_committed(
        tight_pass):
    """A step that opens a transaction by hand, writes, and hangs: the cut must
    not leave the transaction open (the record would run inside it, and the
    advisory unlock would fail on an aborted transaction) and must not commit
    the step's write."""
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "stall3", now=H.T0)
        leak = "rc63b_leak_%d" % int(time.time() * 1000)

        async def leaks(c, ctx):
            tx = c.transaction()
            await tx.start()
            await c.execute("INSERT INTO ingestion_state (key, value) "
                            "VALUES ($1, '{}'::jsonb)", leak)
            await asyncio.sleep(3600)

        res = await _run_once(get_pool, a, [("leaks", leaks), ("later", _ok)])
        assert res["ran"] is True and res["exceeded_step"] == "leaks"
        assert "CONNECTION_RESET" not in res["errors"], res["errors"]
        assert "HEALTH" not in res["errors"], res["errors"]
        # not committed
        assert await conn.fetchval(
            "SELECT count(*) FROM ingestion_state WHERE key=$1", leak) == 0
        # the record IS committed (visible from another connection)
        h = await S.health(conn, a["session_id"])
        assert h["passes"] == 1 and h["last_pass"]["exceeded_step"] == "leaks"
        # the lock is free, the connection is not left in a transaction
        assert await _lock_is_free(H.DSN)
        async with pool.acquire() as c2:
            assert not c2.is_in_transaction()
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key LIKE "
                           "'rc63b_leak_%'")
        await pool.close()
        await conn.close()


@pg
async def test_a_step_inside_a_transaction_manager_is_rolled_back_by_its_own_exit(
        tight_pass):
    """The ordinary shape: `async with conn.transaction()` around a hung
    statement -- cancelled, rolled back by the context manager, the record
    follows on the same connection."""
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "stall4", now=H.T0)
        key = "rc63b_tx_%d" % int(time.time() * 1000)

        async def in_tx(c, ctx):
            async with c.transaction():
                await c.execute("INSERT INTO ingestion_state (key, value) "
                                "VALUES ($1, '{}'::jsonb)", key)
                await c.execute("SELECT pg_sleep(3600)")

        res = await _run_once(get_pool, a, [("in_tx", in_tx)])
        assert res["ran"] is True and res["exceeded_step"] == "in_tx"
        assert await conn.fetchval(
            "SELECT count(*) FROM ingestion_state WHERE key=$1", key) == 0
        assert (await S.health(conn, a["session_id"]))["passes"] == 1
        assert await _lock_is_free(H.DSN)
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key LIKE "
                           "'rc63b_tx_%'")
        await pool.close()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# NOTHING CHANGES FOR A PASS THAT ENDS IN TIME
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_pass_whose_steps_end_in_time_is_recorded_as_before(
        tight_pass):
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "stall5", now=H.T0)
        order = []

        async def mk(name):
            async def step(c, ctx):
                order.append(name)
                assert ctx["step_deadline"] > time.monotonic()
                return {"n": len(order)}
            return name, step
        steps = [await mk(n) for n in ("a", "b", "c")]

        async def boom(c, ctx):
            raise RuntimeError("an ordinary failing step")
        steps.insert(1, ("boom", boom))
        res = await _run_once(get_pool, a, steps)
        assert order == ["a", "b", "c"]            # the given order
        assert res["ran"] is True
        assert res["exceeded_step"] is None and res["skipped_steps"] == {}
        # an ordinary failure is recorded exactly as before
        assert res["errors"] == {"boom": "RuntimeError: an ordinary failing "
                                         "step"}
        assert sorted(res["steps"]) == ["a", "b", "c"]
        assert set(res["step_elapsed_s"]) == {"a", "boom", "b", "c"}
        h = await S.health(conn, a["session_id"])
        assert h["passes"] == 1 and h["errors"] == 1
        hb = await _heartbeat(conn)
        assert hb["ran"] is True and hb["skipped_steps"] == {}
        assert await _lock_is_free(H.DSN)
    finally:
        await pool.close()
        await conn.close()


@pg
async def test_a_step_that_ends_with_no_time_left_makes_the_later_ones_skipped_by_name(
        tight_pass):
    """No step hangs: one simply uses the time (2.0 s of the 2.5 s), so the
    ones after it are skipped by name -- not silently absent."""
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "stall6", now=H.T0)

        async def slow(c, ctx):
            await asyncio.sleep(1.8)
            return {"done": True}
        res = await _run_once(get_pool, a, [("slow", slow), ("next", _ok),
                                            ("last", _ok)])
        assert res["ran"] is True
        assert res["exceeded_step"] is None       # it ended in time
        assert res["steps"]["slow"] == {"done": True}
        assert res["skipped_steps"] == {"next": PRT.R_STEP_SKIPPED,
                                        "last": PRT.R_STEP_SKIPPED}
        assert (await S.health(conn, a["session_id"]))["passes"] == 1
    finally:
        await pool.close()
        await conn.close()


@pg
async def test_direct_calls_are_bounded_the_same_way(tight_pass):
    """paper_pass itself (no run_once around it) bounds each step and
    returns a recorded pass: the bound is the pass's, not the wrapper's."""
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "stall7", now=H.T0)
        t0 = time.monotonic()
        out = await asyncio.wait_for(
            PRT.paper_pass(conn, account_id=a["account_id"],
                           config=a["config"], force=True,
                           fee_fn=H.zero_fee,
                           steps=[("hang", _hang), ("after", _ok)]), GUARD_S)
        assert time.monotonic() - t0 < 4.0
        assert out["ran"] and out["exceeded_step"] == "hang"
        assert out["skipped_steps"] == {"after": PRT.R_STEP_SKIPPED}
        assert (await S.health(conn, a["session_id"]))["passes"] == 1
        assert not conn.is_in_transaction()
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# THE HELD CHECKPOINT CANNOT RUN INTO THE RECORD'S RESERVE
# ═════════════════════════════════════════════════════════════════════

@pytest.fixture
def held_pending(monkeypatch):
    saved = {k: (set(v) if isinstance(v, set) else
                 dict(v) if isinstance(v, dict) else
                 list(v) if isinstance(v, list) else v)
             for k, v in PRT._HELD.items()}
    PRT._HELD.update(task=None, pending={"rc63b-slug"}, queued_at={},
                     attempts={}, latency=[])
    PRT._HELD.pop("clock", None)
    PRT._HELD.pop("respawn", None)
    monkeypatch.setattr(PRT, "_ensure_scheduler", lambda: False)
    yield PRT._HELD
    PRT._HELD.clear()
    PRT._HELD.update(saved)


@pg
async def test_a_hung_held_checkpoint_is_cut_and_the_pass_still_records(
        tight_pass, held_pending, monkeypatch):
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "stall8", now=H.T0)

        async def hung_checkpoint(c, ctx, *, limit_s=0.0):
            await asyncio.sleep(3600)
        monkeypatch.setattr(PRT, "service_held_in_pass", hung_checkpoint)
        # pending slugs exist when the first step ends -> a checkpoint runs
        res = await _run_once(get_pool, a, [("first", _ok), ("second", _ok)])
        assert res["ran"] is True, res
        rec = res["held_in_pass"]
        assert rec["capped"] is True
        assert any(PRT.R_STEP_EXCEEDED in e for e in rec["errors"]), rec
        assert res["elapsed_s"] < 4.0
        assert (await S.health(conn, a["session_id"]))["passes"] == 1
        hb = await _heartbeat(conn)
        assert hb["ran"] is True
        assert await _lock_is_free(H.DSN)
    finally:
        await pool.close()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# THE LAST RESORT NAMES WHERE IT WAS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_pass_the_hard_timeout_still_cuts_names_the_step_it_was_in(
        tight_pass):
    """A step that swallows its first cancellation outlives the per-step
    bound; the hard timeout (the last resort) then cuts the pass. Its
    heartbeat names the step in flight and the steps that had ended, instead
    of an empty "TimeoutError: ", and the lock is still released."""
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "stall9", now=H.T0)

        async def stubborn(c, ctx):
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                await asyncio.sleep(3600)     # ignores the first cancel

        res = await _run_once(get_pool, a, [("first", _ok),
                                            ("stubborn", stubborn)])
        assert res["ran"] is False
        assert res["refusal"] == "PAPER_PASS_RAISED_OR_TIMED_OUT"
        assert res["in_step"] == "stubborn"
        assert res["steps_ended"] == ["first"]
        assert "in step stubborn" in res["why"]
        assert res["why"].startswith("TimeoutError: ")
        hb = await _heartbeat(conn)
        assert hb["refusal"] == "PAPER_PASS_RAISED_OR_TIMED_OUT"
        assert hb["in_step"] == "stubborn" and hb["steps_ended"] == ["first"]
        assert await _lock_is_free(H.DSN)
    finally:
        await pool.close()
        await conn.close()
