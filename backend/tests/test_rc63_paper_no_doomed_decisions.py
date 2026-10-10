"""NO DOOMED PAPER DECISION (RC6.3 root-cause audit, lane PAPER-1).

THE DEFECT (RC6.3b packet hour, research-sql 38056211192 Q3b / 38056380348
G5, G6): 36 REFUSE PROBABILITY_EVIDENCE_STALE paper decisions whose only
content was "the reading had expired before I decided" -- 14 by the paper-
pass BACKSTOP (PINNACLE_EXPLORATION_PAPER on readings 32-134 s old that were
fresh at the valuation instant; every one of their valuations had been
decided in cycle by the other strategies, the exploration hook attempt
having timed out) and 22 IN CYCLE (the three strategies deciding one
valuation in sequence, each under its own 8 s deadline, so a hung venue read
pushed the next decision instants past the 30 s rule -- DEREK 8.1-8.3 s after
the valuation at 33-38 s -- or the valuation itself was persisted with a
reading already 30.0-37.9 s old). The coverage census takes an event's
earliest-stage decision, so one such row made 10 events SOFTWARE first losses
at FAIR_VALUE (77 in 6 h) while every one of the 10 also reached EV by
another strategy's decision.

THE RULE (paper_derek.doomed_by_expiry, shared_book_read, book_read_bound;
paper_runtime.decide_valuation): the 30 s rule, the clock and the decision-
instant re-age are unchanged; a decision that could only refuse an expired
reading is not RECORDED AS A DECISION.

  (a) THE BACKSTOP selects with the reading's freshness at selection and
      whether the in-cycle hook attempted the valuation: an expired candidate
      the hook attempted is a DEFERRED paper_hook_failures row,
      EXPIRED_BEFORE_DECISION (reading age, limit), and no REFUSE row; a
      fresh one is decided; one the hook never saw keeps the pass's own
      first decision by name (the safeguard record the NCAAF / NFL / chaos /
      funnel / exploration / vertical-slice proofs pin). A candidate selected
      fresh whose reading expires before its decision instant takes the
      same route.
  (b) IN CYCLE the strategies decide CONCURRENTLY under one deadline on ONE
      shared venue-book read, bounded by the reading's own probability
      deadline; a read that bound cut is a deferral by name,
      DEFERRED_PAST_THE_PROBABILITY_DEADLINE, never a REFUSE row.
  (c) A read that cannot finish before the reading's deadline is NOT STARTED.
  (d) THE CENSUS REPLAY: the 20 production valuations that carried the 36
      doomed decisions, re-decided through the real hook and the real pass
      steps on real Postgres at their production instants, put into the
      packet-hour fixture in place of their production decisions: every one
      of the 10 events is ECONOMIC at EV, SOFTWARE 74 / ECONOMIC 24 /
      EXTERNAL 6 / UNCLASSIFIED 0 -- the H0 projection, now produced by the
      code -- and the readback capture the H0 expectation case reads is this
      replay's table (regenerated and compared here, never edited).
  (e) The readers of the paper evaluation and hook records tolerate the two
      new outcomes.

Every case here that proves the repair FAILS ON THE BASE (dd25c588): the base
writes the REFUSE PROBABILITY_EVIDENCE_STALE rows, reads the venue twice and
crosses the rule on the later strategies. SYNTHETIC valuations and books on
scratch paper_test_* accounts; no venue is contacted; nothing here is
evidence about any market.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import pathlib
import sys
import time

import pytest

from sportsassets import agent_funnel as AF
from sportsassets import agent_status_contract as ASC
from sportsassets import bettor_paper_experiment as EXP
from sportsassets import bettor_paper_session as S
from sportsassets import coverage_first_loss as FL
from sportsassets import refusal_taxonomy as RT
from sportsassets import refusal_taxonomy_table as TT
from sportsassets.agents import audrey_audit as AA
from sportsassets.agents import derek_policy as DP
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_explore as PEX
from sportsassets.agents import paper_ops_audit as POA
from sportsassets.agents import paper_runtime as PR
from sportsassets.workers import ext_pinnacle_loop as X
from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_rc63_software_reds_replay as H0

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "tools"))
import sw_reds_hourly_census as T  # noqa: E402

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
FEE = H.flat_fee(0.01)
CG, EXPLORE, MAKER = PB.CG_STRATEGY, PB.EXPLORE_STRATEGY, PB.MAKER_STRATEGY
DEREK = "DEREK_ENTRY_POLICY_V2"
STALE = DP.R_STALE
#: the two names as the lane plan words them (literals, so this module
#: imports on the base and every case fails there by itself; the first pure
#: test pins them to the code's constants)
EXPIRED = "EXPIRED_BEFORE_DECISION"
PAST = "DEFERRED_PAST_THE_PROBABILITY_DEADLINE"
IN_CYCLE = "IN_CYCLE_VALUATION_HOOK"
GEI_AGE = "GROSS_EDGE_INPUT_PINNACLE_AGE_NOT_WITHIN_LIMIT"
CAPTURE = H0.CAPTURES["PAPER-1"]
ENTRY_STEPS = [("derek", PD.step),
               ("benchmark_completed_game", PB.step_completed_game),
               ("exploration", PEX.step)]


# ═════════════════════════════════════════════════════════════════════
# THE RULE'S PIECES, PURE
# ═════════════════════════════════════════════════════════════════════

def test_the_two_outcomes_are_hook_outcomes_not_refusal_codes():
    assert (PD.R_EXPIRED_BEFORE_DECISION,
            PD.R_DEFERRED_PAST_PROBABILITY_DEADLINE) == (EXPIRED, PAST)
    for code in (EXPIRED, PAST):
        assert code in TT.NOT_REFUSAL and code not in TT.TABLE
        assert "paper_hook_failures" in TT.NOT_REFUSAL[code]
        # the backstop never selects a valuation again once it carries one
        assert code in PD.CANDIDATES_SQL and code in PB.CANDIDATES_SQL
        assert code in PD.BACKSTOP_SKIPS
    assert set(PD.NOT_DECIDED_BY_NAME) == {EXPIRED, PAST}
    for col in PD.CANDIDATE_COLUMNS:
        assert col in PD.CANDIDATES_SQL and col in PB.CANDIDATES_SQL
    # the selection predicate, as the lane plan words it
    for sql in (PD.CANDIDATES_SQL, PB.CANDIDATES_SQL):
        assert "v.observed_at + make_interval(secs =>" in sql
        assert "attempted_in_cycle" in sql and "IN_CYCLE_VALUATION_HOOK" in sql
    # the census never reads a hook row (ledger events, valuations and
    # decisions only): neither name is a decision refusal code
    for sql in (FL.DECISIONS_SQL, FL.VALUATIONS_SQL):
        assert "paper_hook_failures" not in sql


def test_a_deferral_by_name_keeps_its_detail_through_the_hooks_result_shape():
    """PB.decide_for_hook returns a fixed key set; a deferral by name carries
    the reading's age, limit and deadline and whether the read was started
    into the hook-failure row through PD.deferral_detail (and nothing else
    does)."""
    past = PD.deferred_past_probability_deadline(
        {"error": PD.G.R_BOOK_READ_DEADLINE,
         "probability_deadline_binds": True,
         "not_started": True, "read_bound_s": 0.1})
    d = PD.deferral_detail(past)
    assert d["not_started"] is True and d["read_bound_s"] == 0.1
    assert d["read_error"] == PD.G.R_BOOK_READ_DEADLINE and "basis" in d
    assert set(d) <= set(PD.DEFERRAL_DETAIL_KEYS)
    exp = PD.doomed_by_expiry(
        {"decided_via": PD.DECIDED_VIA_PASS,
         "backstop_attempted_in_cycle": True},
        {"qualification": "STALE", "age_s": 40.1, "limit_s": 30.0,
         "at": 100.0, "decided_at": 140.1})
    d = PD.deferral_detail(exp)
    assert d["reading_age_s"] == 40.1 and d["limit_s"] == 30.0
    assert d["probability_deadline_epoch"] == 130.0
    assert d["decision_instant"] == 140.1 and d["decided_via"] == \
        PD.DECIDED_VIA_PASS
    assert "not_started" not in d                   # None is not carried
    # every other result: nothing
    assert PD.deferral_detail({"deferred": True, "why": "BOOK_RETRY",
                               "read_bound_s": 1.0}) == {}
    assert PD.deferral_detail({"decided": True, "verdict": "REFUSE"}) == {}
    assert PD.deferral_detail(None) == {}
    assert PD.deferral_detail(RuntimeError("x")) == {}


def test_the_readings_deadline_is_the_collectors_own_rule():
    obs = 1_791_622_728.677614
    assert PD.reading_deadline(obs, 30.0) == X.probability_deadline(obs)
    assert S.default_config()["entry"]["pinnacle_max_age_s"] == \
        X.PINNACLE_MAX_AGE_S == 30.0
    assert PD.max_age_of({}) == X.PINNACLE_MAX_AGE_S
    assert PD.max_age_of({"entry": {"pinnacle_max_age_s": 30.0}}) == 30.0
    assert PD.reading_deadline(None, 30.0) is None
    assert PR._probability_deadline_of(
        {"observed_at": obs},
        S.default_config()) == X.probability_deadline(obs)
    assert PR._probability_deadline_of({"observed_at": None}, {}) is None
    assert PD.MIN_BOOK_READ_S == 0.25


def test_the_book_read_bound_takes_the_earlier_deadline():
    mono = 1000.0
    at = 5000.0
    base = {"clock": lambda: at, "now": at}
    # no bound of either kind
    assert PD.book_read_bound(dict(base), now_mono=mono)["remaining_s"] is None
    # the decision's own budget binds (8 s less the 1.5 s reserve)
    b = PD.book_read_bound(dict(base, deadline=mono + 8.0,
                                probability_deadline_epoch=at + 20.0),
                           now_mono=mono)
    assert b["probability_binds"] is False and b["start"] is True
    assert b["remaining_s"] == pytest.approx(8.0 - PD.BOOK_READ_RESERVE_S)
    # the reading's deadline binds: 5 s left of the 30 s rule
    b = PD.book_read_bound(dict(base, deadline=mono + 8.0,
                                probability_deadline_epoch=at + 5.0),
                           now_mono=mono)
    assert b["probability_binds"] is True and b["start"] is True
    assert b["remaining_s"] == pytest.approx(5.0)
    # ...and with less than MIN_BOOK_READ_S left the read is not started
    b = PD.book_read_bound(dict(base, deadline=mono + 8.0,
                                probability_deadline_epoch=at + 0.1),
                           now_mono=mono)
    assert b["probability_binds"] is True and b["start"] is False
    b = PD.book_read_bound(dict(base, deadline=mono + 8.0,
                                probability_deadline_epoch=at - 3.0),
                           now_mono=mono)
    assert b["start"] is False and b["remaining_s"] == pytest.approx(-3.0)
    # the pass's deadline alone, spent
    b = PD.book_read_bound(dict(base, deadline=mono + 1.0), now_mono=mono)
    assert b["start"] is False and b["probability_binds"] is False


def test_doomed_by_expiry_routes_by_where_the_decision_is_formed():
    stale = {"qualification": "STALE", "age_s": 40.0, "limit_s": 30.0,
             "at": 100.0, "decided_at": 140.0}
    fresh = dict(stale, qualification="FRESH", age_s=10.0)
    # in cycle: the reading expired before the hook could decide
    d = PD.doomed_by_expiry({"decided_via": PD.DECIDED_VIA_CYCLE}, stale)
    assert d["deferred"] is True and d["why"] == PAST
    assert d["reading_age_s"] == 40.0 and d["limit_s"] == 30.0
    assert d["probability_deadline_epoch"] == 130.0
    # the book retry is an in-cycle attempt too
    assert PD.doomed_by_expiry({"decided_via": "BOOK_RETRY"}, stale)["why"] \
        == PAST
    # the backstop on a valuation the hook attempted: expired before the pass
    d = PD.doomed_by_expiry({"decided_via": PD.DECIDED_VIA_PASS,
                             "backstop_attempted_in_cycle": True}, stale)
    assert d["why"] == EXPIRED and d["decided_via"] == PD.DECIDED_VIA_PASS
    # the backstop on a valuation the hook never saw: its own first decision,
    # recorded by name as before (None: the caller refuses STALE)
    assert PD.doomed_by_expiry({"decided_via": PD.DECIDED_VIA_PASS}, stale) \
        is None
    assert PD.doomed_by_expiry({}, stale) is None
    # a fresh reading is never doomed
    for ctx in ({"decided_via": PD.DECIDED_VIA_CYCLE},
                {"decided_via": PD.DECIDED_VIA_PASS,
                 "backstop_attempted_in_cycle": True}):
        assert PD.doomed_by_expiry(ctx, fresh) is None
    # the deferral for a read the reading's deadline bound
    assert PD.deferred_past_probability_deadline(
        {"error": PD.G.R_BOOK_READ_DEADLINE}) is None      # the decision's
    got = PD.deferred_past_probability_deadline(
        {"error": PD.G.R_BOOK_READ_DEADLINE,
         "probability_deadline_binds": True,
         "not_started": True, "read_bound_s": 0.1})
    assert got["why"] == PAST and got["not_started"] is True
    assert PD.deferred_past_probability_deadline(
        {"marketData": {}, "probability_deadline_binds": True}) is None


def test_split_expired_candidates_routes_only_the_attempted_expired_rows():
    rows = [{"id": 1, "reading_fresh_at_selection": True,
             "attempted_in_cycle": True},
            {"id": 2, "reading_fresh_at_selection": False,
             "attempted_in_cycle": True},
            {"id": 3, "reading_fresh_at_selection": False,
             "attempted_in_cycle": False},
            {"id": 4}]
    decide, expired = PD.split_expired_candidates(rows)
    assert [r["id"] for r in expired] == [2]
    assert [r["id"] for r in decide] == [1, 3, 4]
    ctx = {"clock": lambda: 50.0, "now": 50.0}
    row = {"id": 2, "observed_at": 10.0, "reading_fresh_at_selection": False,
           "attempted_in_cycle": True}
    prev = PD.candidate_context(ctx, row, max_age_s=30.0)
    assert "reading_fresh_at_selection" not in row
    assert ctx["probability_deadline_epoch"] == 40.0
    assert ctx["backstop_attempted_in_cycle"] is True
    assert ctx["backstop_reading_fresh_at_selection"] is False
    PD.restore_candidate_context(ctx, prev)
    assert "probability_deadline_epoch" not in ctx
    assert "backstop_attempted_in_cycle" not in ctx


class _OverlapConn:
    """A connection that fails if a second statement starts while one runs
    (what asyncpg does: 'another operation is in progress'), and records
    the statements and transactions in order."""

    def __init__(self):
        self.busy = False
        self.log = []
        self.in_tx = 0

    async def _run(self, what, dt=0.005):
        if self.busy:
            raise RuntimeError("another operation is in progress: %s" % what)
        self.busy = True
        try:
            self.log.append(what)
            await asyncio.sleep(dt)
        finally:
            self.busy = False
        return what

    async def execute(self, sql, *a):
        return await self._run(("execute", sql))

    async def fetch(self, sql, *a):
        return await self._run(("fetch", sql))

    async def fetchrow(self, sql, *a):
        return await self._run(("fetchrow", sql))

    async def fetchval(self, sql, *a):
        return await self._run(("fetchval", sql))

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self_):
                await conn._run(("BEGIN", conn.in_tx))
                conn.in_tx += 1
                return self_

            async def __aexit__(self_, et, ev, tb):
                conn.in_tx -= 1
                await conn._run(("COMMIT" if et is None else "ROLLBACK",
                                 conn.in_tx))

            async def start(self_):
                return await self_.__aenter__()

            async def commit(self_):
                return await self_.__aexit__(None, None, None)

            async def rollback(self_):
                return await self_.__aexit__(RuntimeError, None, None)
        return _Tx()

    def is_in_transaction(self):
        return self.in_tx > 0


def test_one_statement_at_a_time_serializes_the_strategies_on_one_connection():
    """Three coroutines issue statements and transactions on ONE connection
    through _OneStatementAtATime: no statement overlaps another, a
    transaction's statements are never interleaved with another coroutine's,
    a nested transaction of the same task rides on the outer's hold, the
    manual start / commit / rollback form holds the lock too, and the
    connection's other attributes are its own."""
    raw = _OverlapConn()
    conn = PR._OneStatementAtATime(raw)
    order = []

    async def strategy(name):
        await conn.fetchrow("SELECT %s-1" % name)
        async with conn.transaction():
            order.append((name, "tx-open"))
            await conn.execute("INSERT %s-a" % name)
            async with conn.transaction():              # a savepoint: nested
                await conn.execute("INSERT %s-b" % name)
            await conn.fetchval("SELECT %s-c" % name)
            order.append((name, "tx-close"))
        await conn.fetch("SELECT %s-2" % name)
        tx = conn.transaction()
        await tx.start()
        await conn.execute("INSERT %s-manual" % name)
        await tx.rollback()
        return name

    got = asyncio.run(PR._run_all([strategy("A"), strategy("B"),
                                   strategy("C")]))
    assert got == ["A", "B", "C"]
    # every statement ran, none overlapped (the stand-in would have raised)
    stmts = [w for w in raw.log if w[0] in ("execute", "fetch", "fetchrow",
                                             "fetchval")]
    assert len(stmts) == 3 * 6
    # inside a transaction nothing of another coroutine ran
    opened = [i for i, w in enumerate(raw.log) if w == ("BEGIN", 0)]
    assert len(opened) == 6                     # a managed and a manual each
    for start in opened:
        j = start + 1
        owner = None
        while raw.log[j] != ("COMMIT", 0) and raw.log[j] != ("ROLLBACK", 0):
            w = raw.log[j]
            if w[0] in ("execute", "fetchval"):
                who = w[1].split()[1][0]
                owner = owner or who
                assert who == owner, raw.log[start:j + 1]
            j += 1
    assert raw.is_in_transaction() is False
    assert conn.is_in_transaction() is False          # the connection's own
    assert conn.wrapped is raw


def test_run_all_returns_every_result_and_an_error_as_a_value():
    async def ok(n):
        await asyncio.sleep(0.001 * n)
        return n

    async def bad():
        raise ValueError("synthetic")

    got = asyncio.run(PR._run_all([ok(3), bad(), ok(1)]))
    assert got[0] == 3 and got[2] == 1 and isinstance(got[1], ValueError)
    assert asyncio.run(PR._run_all([])) == []
    assert PR._as_result("S", got[1])["error"].startswith("ValueError")


# ═════════════════════════════════════════════════════════════════════
# THE PROOFS ON REAL POSTGRES
# ═════════════════════════════════════════════════════════════════════

def _only(*on):
    for k in (CG, MAKER, EXPLORE, PB.CONTROL_KEY):
        PL.set_policy_control(k, k in on)


@pytest.fixture
def paper_env(monkeypatch):
    """The paper session and the benchmark family on; the three production
    writers of the 10 -- the completed-game policy, exploration and Derek --
    deciding (the maker and the strict benchmark off); back to the migrated
    launch selection afterwards."""
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    _only(CG, EXPLORE)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    _only(CG, EXPLORE)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()


async def _nosleep(_):
    return None


class Book(PL.Transport):
    """The venue side behind the real PaperMarketDataClient: answers after
    `delay_s` (a blocking read in the client's thread, as the venue's is),
    observed at the real clock when `live`; every call counted."""

    def __init__(self, t0, *, delay_s=0.0, live=False):
        super().__init__(t0)
        self.delay_s = float(delay_s)
        self.live = live

    def __call__(self, slug):
        got = super().__call__(slug)        # counted when the read STARTS
        if self.delay_s:
            time.sleep(self.delay_s)        # the venue answers after delay_s
        if self.live:
            got["observed_at"] = time.time()
        return got


async def _hook(conn, acct, client, vid, *, now=None, strategies=None):
    return await PR.decide_valuation(
        conn, valuation_id=vid, now=now, market_data=client,
        account_id=acct["account_id"], fee_fn=FEE,
        schedule_fill=lambda: {"scheduled": False}, book_retry=False,
        strategies=strategies)


async def _pass(conn, acct, client, *, now, steps=None):
    return await PR.paper_pass(
        conn, now=now, account_id=acct["account_id"], market_data=client,
        config=acct["config"], force=True, fee_fn=FEE, sleep=_nosleep,
        steps=steps if steps is not None else ENTRY_STEPS)


async def _decisions(conn, acct, vid) -> dict:
    return {r["strategy"]: dict(r) for r in await conn.fetch(
        "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
        " valuation_id=$2", acct["session_id"], vid)}


async def _hook_rows(conn, acct, vid) -> list:
    out = []
    for r in await conn.fetch(
            "SELECT * FROM paper_hook_failures WHERE session_id=$1 AND "
            " valuation_id=$2 ORDER BY failure_id", acct["session_id"], vid):
        d = dict(r)
        d["detail"] = H.j(d["detail"]) if d.get("detail") else {}
        out.append(d)
    return out


async def _attempted_in_cycle(conn, acct, vid, strategy,
                              outcome="TIMEOUT") -> None:
    """The in-cycle hook's record of an attempt that did not decide (the
    production shape behind the 14 backstop rows: the exploration hook
    attempt timed out)."""
    await conn.execute(
        "INSERT INTO paper_hook_failures (session_id, account_id, "
        " valuation_id, strategy, stage, outcome, elapsed_s, error, detail) "
        "VALUES ($1,$2,$3,$4,$5,$6,8.0,$7,'{}'::jsonb)",
        acct["session_id"], acct["account_id"], int(vid), strategy, IN_CYCLE,
        outcome, "TimeoutError: the in-cycle decision exceeded its 8.0 s "
                 "budget")


async def _cleanup(conn, acct=None, now=None):
    try:
        if now is not None:
            await PL.drop_today_run(conn, now)
    except Exception:                                           # noqa: BLE001
        pass
    await PL.purge_everything(conn)
    await conn.execute("DELETE FROM paper_hook_failures WHERE account_id "
                       " LIKE 'paper_test_%'")


def _codes(row) -> list:
    return list(row.get("refusals") or []) + ([row["refusal"]]
                                              if row.get("refusal") else [])


# ── (a) THE BACKSTOP ────────────────────────────────────────────────────

@pg
async def test_a_the_backstop_records_not_decides_an_attempted_valuation_whose_reading_expired(  # noqa: E501
        paper_env):
    """Production's backstop shape: a reading fresh at the valuation instant
    (20 s), 40 s old at the pass, on a valuation whose in-cycle attempt did
    not decide it. BASE: a REFUSE PROBABILITY_EVIDENCE_STALE row per strategy
    (three SOFTWARE first losses at FAIR_VALUE). HEAD: no decision row, one
    EXPIRED_BEFORE_DECISION hook row per strategy with the reading's age and
    limit, no book read, and the next pass does not select it again."""
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await _cleanup(conn)
        acct = await PL.new_account(conn, "nd-a1", now=now)
        v = await PL.valuation(conn, decided_at=now - 20, pin_age_s=20.0,
                               compatibility="INCOMPATIBLE")
        t = Book(now)
        t.set(v["slug"], offers=[(0.62, 2000)], bids=[(0.60, 2000)])
        await _attempted_in_cycle(conn, acct, v["valuation_id"], EXPLORE)
        p = await _pass(conn, acct, PL.client(t), now=now)
        assert p["ran"] and not p["errors"], p["errors"]
        assert t.calls == [], "no book is read for an expired reading"
        decided = await _decisions(conn, acct, v["valuation_id"])
        assert decided == {}, {k: _codes(r) for k, r in decided.items()}
        rows = await _hook_rows(conn, acct, v["valuation_id"])
        exp = {r["strategy"]: r for r in rows if r["error"] == EXPIRED}
        assert set(exp) == {DEREK, CG, EXPLORE}, [r["error"] for r in rows]
        for r in exp.values():
            assert (r["stage"], r["outcome"]) == ("PAPER_PASS", "DEFERRED")
            d = r["detail"]
            assert d["why"] == EXPIRED and d["limit_s"] == 30.0
            assert 39.0 <= d["reading_age_s"] <= 41.5, d
            assert d["attempted_in_cycle"] is True
            assert d["decided_via"] == PD.DECIDED_VIA_PASS
        for name in ("derek", "benchmark_completed_game", "exploration"):
            st = p["steps"][name]
            assert st["candidates"] == 1 and st["decisions_recorded"] == 0
            assert st["expired_before_decision"] == 1 and st["deferred"] == 1
        # recorded once: the candidates queries skip it from now on
        p2 = await _pass(conn, acct, PL.client(t), now=now + 60)
        assert not p2["errors"]
        assert all(p2["steps"][n]["candidates"] == 0 for n in (
            "derek", "benchmark_completed_game", "exploration"))
        assert len(await _hook_rows(conn, acct, v["valuation_id"])) == 4
        assert await _decisions(conn, acct, v["valuation_id"]) == {}
    finally:
        await _cleanup(conn, now=now)
        await conn.close()


@pg
async def test_a_the_backstop_keeps_its_own_first_decision_by_name_on_a_valuation_the_hook_never_saw(  # noqa: E501
        paper_env):
    """THE BOUNDARY, PINNED: the same 40 s reading on a valuation with NO
    in-cycle attempt (the paper session off in the collector, a restart)
    is the pass's own first and only decision on it: recorded REFUSE
    PROBABILITY_EVIDENCE_STALE by name with its age and limit, no book
    read, no order -- exactly as the strategy safeguard proofs pin -- and
    no EXPIRED row."""
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await _cleanup(conn)
        acct = await PL.new_account(conn, "nd-a2", now=now)
        v = await PL.valuation(conn, decided_at=now - 20, pin_age_s=20.0,
                               compatibility="INCOMPATIBLE")
        t = Book(now)
        t.set(v["slug"], offers=[(0.62, 2000)], bids=[(0.60, 2000)])
        p = await _pass(conn, acct, PL.client(t), now=now)
        assert p["ran"] and not p["errors"], p["errors"]
        assert t.calls == []
        decided = await _decisions(conn, acct, v["valuation_id"])
        assert set(decided) == {DEREK, CG, EXPLORE}
        for r in decided.values():
            assert r["verdict"] == "REFUSE" and STALE in _codes(r)
            pin = H.j(r["pinnacle"])
            assert pin["decided_via"] == PD.DECIDED_VIA_PASS
            assert pin["age_s"] > 30.0 and pin["limit_s"] == 30.0
            assert r["book_obs_id"] is None
        rows = await _hook_rows(conn, acct, v["valuation_id"])
        assert [r for r in rows if r["error"] in (EXPIRED, PAST)] == []
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE account_id=$1",
            acct["account_id"]) == 0
    finally:
        await _cleanup(conn, now=now)
        await conn.close()


@pg
async def test_a_a_candidate_selected_fresh_that_expires_before_its_decision_instant_is_recorded_not_decided(  # noqa: E501
        paper_env, monkeypatch):
    """On the pass's own (real) clock: two candidates, the newer decided
    first (the candidates order). The older's reading is 29.4 s old when
    the pass selects it -- fresh -- and the newer's decision takes 1.2 s (a
    slow catalogue read), so the older's decision instant finds its reading
    past the rule. BASE: a REFUSE PROBABILITY_EVIDENCE_STALE row on the
    older. HEAD: EXPIRED_BEFORE_DECISION on the older (no decision row, no
    book read for it); the newer is decided as before."""
    conn = await H.connect()
    now0 = time.time()
    try:
        await _cleanup(conn)
        acct = await PL.new_account(conn, "nd-a3", now=now0)
        real = DP.catalogue_row

        async def slow_catalogue(conn_, slug):
            await asyncio.sleep(1.2)
            return await real(conn_, slug)
        monkeypatch.setattr(DP, "catalogue_row", slow_catalogue)
        t = Book(now0, live=True)
        now1 = time.time()                  # the readings' instants: NOW
        older = await PL.valuation(conn, decided_at=now1 - 3, pin_age_s=26.4,
                                   compatibility="INCOMPATIBLE")
        newer = await PL.valuation(conn, decided_at=now1 - 1, pin_age_s=5.0,
                                   compatibility="INCOMPATIBLE")
        assert older["slug"] != newer["slug"]
        await _attempted_in_cycle(conn, acct, older["valuation_id"], EXPLORE)
        for v in (older, newer):
            t.set(v["slug"], offers=[(0.62, 2000)], bids=[(0.60, 2000)])
        p = await PR.paper_pass(
            conn, now=None, account_id=acct["account_id"],
            market_data=PL.client(t), config=acct["config"], force=True,
            fee_fn=FEE, sleep=_nosleep,
            steps=[("benchmark_completed_game", PB.step_completed_game)])
        assert p["ran"] and not p["errors"], p["errors"]
        assert p["steps"]["benchmark_completed_game"]["candidates"] == 2
        assert older["slug"] not in t.calls, t.calls
        decided = await _decisions(conn, acct, older["valuation_id"])
        assert decided == {}, {k: _codes(r) for k, r in decided.items()}
        rows = [r for r in await _hook_rows(conn, acct, older["valuation_id"])
                if r["error"] == EXPIRED]
        assert [r["strategy"] for r in rows] == [CG]
        d = rows[0]["detail"]
        assert d["reading_age_s"] > 30.0 and d["limit_s"] == 30.0
        assert d["decided_via"] == PD.DECIDED_VIA_PASS
        # selected fresh and expired at the decision instant (after the
        # newer's slow decision), or -- on a loaded machine -- already
        # expired at the selection instant: both routes record, neither
        # decides
        assert "decision_instant" in d or "selection_instant" in d
        # the newer, fresh, is decided as before (never STALE)
        got = await _decisions(conn, acct, newer["valuation_id"])
        assert set(got) == {CG}, got
        assert STALE not in _codes(got[CG])
        assert H.j(got[CG]["pinnacle"])["age_s"] <= 30.0
        assert [r for r in await _hook_rows(conn, acct, newer["valuation_id"])
                if r["error"] in (EXPIRED, PAST)] == []
    finally:
        await _cleanup(conn, now=now0)
        await conn.close()


# ── (b) IN CYCLE: CONCURRENT, ONE SHARED READ, THE READING'S DEADLINE ──

@pg
async def test_b_three_strategies_decide_one_valuation_concurrently_on_one_read_bounded_by_the_readings_deadline(  # noqa: E501
        paper_env, monkeypatch):
    """A reading 25 s old when the hook starts; the venue answers a book
    read after 6 s. BASE (in sequence, each read bounded only by the 8 s
    decision budget): the completed-game read completes at 31 s of reading
    age and its edge is refused on the Pinnacle age, exploration and Derek
    decide at 31 s -- PROBABILITY_EVIDENCE_STALE -- and the venue is read
    twice. HEAD: the three start together, share ONE read bounded by the
    reading's own deadline (5 s), the read is cut inside it, and every
    strategy is deferred by name DEFERRED_PAST_THE_PROBABILITY_DEADLINE: no
    decision row crosses the rule, one venue read, the hook ends inside its
    8 s budget. Derek decides with a research model (as production did), so
    its decision too reaches the book."""
    conn = await H.connect()
    now0 = time.time()
    try:
        await _cleanup(conn)
        await PL.purge_research_models(conn)
        await PL.train_model(conn, monkeypatch,
                             model_id="derek-research-model-paper1-b1")
        acct = await PL.new_account(conn, "nd-b1", now=now0)
        t = Book(now0, delay_s=6.0, live=True)
        v = await PL.valuation(conn, decided_at=now0 - 0.5, pin_age_s=24.5,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.62, 2000)], bids=[(0.60, 2000)])
        t0 = time.monotonic()
        g = await _hook(conn, acct, PL.client(t), v["valuation_id"])
        took = time.monotonic() - t0
        assert took < 8.0, took
        assert len(t.calls) == 1, t.calls
        decided = await _decisions(conn, acct, v["valuation_id"])
        for strat, r in decided.items():
            codes = _codes(r)
            assert STALE not in codes and GEI_AGE not in codes, (strat, codes)
            assert H.j(r["pinnacle"])["age_s"] <= 30.0, (strat, r["pinnacle"])
        rows = await _hook_rows(conn, acct, v["valuation_id"])
        past = {r["strategy"]: r for r in rows if r["error"] == PAST}
        assert set(past) == {CG, EXPLORE, DEREK}, [
            (r["strategy"], r["error"]) for r in rows]
        for r in past.values():
            assert (r["stage"], r["outcome"]) == (IN_CYCLE, "DEFERRED")
            d = r["detail"]
            assert d["not_started"] is False
            assert 4.0 <= d["read_bound_s"] <= 5.1, d
            assert d["read_error"] == PD.G.R_BOOK_READ_DEADLINE
        assert g["decided"] is False and g["deferred"] is True
        assert g["benchmark_completed_game"]["why"] == PAST
        assert g["paper_family"][EXPLORE]["why"] == PAST
    finally:
        await _cleanup(conn, now=now0)
        await PL.purge_research_models(conn)
        await conn.close()


@pg
async def test_b_a_shared_read_that_finishes_inside_the_rule_decides_every_strategy_at_the_hook_instant(  # noqa: E501
        paper_env):
    """A reading 20 s old, a 3 s venue read: every strategy decides on the
    one read at the hook's instant (decision instants within a second of
    each other, all inside the rule). BASE: in sequence -- Derek's decision
    instant comes after the completed-game read, 3 s later."""
    conn = await H.connect()
    now0 = time.time()
    try:
        await _cleanup(conn)
        acct = await PL.new_account(conn, "nd-b2", now=now0)
        t = Book(now0, delay_s=3.0, live=True)
        v = await PL.valuation(conn, decided_at=now0 - 0.5, pin_age_s=19.5,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.62, 2000)], bids=[(0.60, 2000)])
        g = await _hook(conn, acct, PL.client(t), v["valuation_id"])
        assert len(t.calls) == 1, t.calls
        decided = await _decisions(conn, acct, v["valuation_id"])
        assert set(decided) >= {CG, EXPLORE, DEREK}, (
            set(decided), g, await _hook_rows(conn, acct, v["valuation_id"]))
        instants = {}
        for strat, r in decided.items():
            pin = H.j(r["pinnacle"])
            assert pin["decided_via"] == PD.DECIDED_VIA_CYCLE
            assert STALE not in _codes(r) and GEI_AGE not in _codes(r)
            assert pin["age_s"] <= 30.0
            instants[strat] = float(pin["decided_at"])
        spread = max(instants.values()) - min(instants.values())
        assert spread < 1.0, instants
        assert decided[CG]["refusal"] == PB.R_EDGE
        assert decided[CG]["book_obs_id"] is not None
        # the investment policy's decision instant is the earliest
        assert instants[CG] <= min(instants[DEREK], instants[EXPLORE]) + 1e-6
    finally:
        await _cleanup(conn, now=now0)
        await conn.close()


# ── (c) A READ THAT CANNOT FINISH IS NOT STARTED ───────────────────────

@pg
async def test_c_a_read_that_cannot_finish_before_the_readings_deadline_is_not_started(  # noqa: E501
        paper_env):
    """A reading 29.9 s old at the hook's (fixed) instant: 0.1 s to the
    deadline, less than MIN_BOOK_READ_S. BASE: the book is read and the
    strategies decide. HEAD: no read is started; the completed-game policy
    and exploration are deferred by name with not_started."""
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await _cleanup(conn)
        acct = await PL.new_account(conn, "nd-c", now=now)
        t = Book(now)
        v = await PL.valuation(conn, decided_at=now - 1.0, pin_age_s=28.9,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.62, 2000)], bids=[(0.60, 2000)])
        g = await _hook(conn, acct, PL.client(t), v["valuation_id"], now=now)
        assert t.calls == [], t.calls
        decided = await _decisions(conn, acct, v["valuation_id"])
        assert CG not in decided and EXPLORE not in decided, {
            k: _codes(r) for k, r in decided.items()}
        for r in decided.values():                  # Derek, without a model
            assert STALE not in _codes(r)
            assert H.j(r["pinnacle"])["age_s"] <= 30.0
        rows = await _hook_rows(conn, acct, v["valuation_id"])
        past = {r["strategy"]: r for r in rows if r["error"] == PAST}
        assert set(past) >= {CG, EXPLORE}, [(r["strategy"], r["error"])
                                           for r in rows]
        for r in past.values():
            assert r["detail"]["not_started"] is True
            assert r["detail"]["read_bound_s"] == pytest.approx(0.1, abs=0.02)
        assert g["benchmark_completed_game"]["why"] == PAST
    finally:
        await _cleanup(conn, now=now)
        await conn.close()


# ── (d) THE CENSUS REPLAY OF THE PRODUCTION SHAPES ─────────────────────

def _epoch(hms: str) -> float:
    """A packet-hour instant, 2026-10-10 UTC (research-sql 38056211192)."""
    h, m, s = hms.split(":")
    return _dt.datetime(2026, 10, 10, int(h), int(m),
                        tzinfo=_dt.timezone.utc).timestamp() + float(s)


#: THE 20 PRODUCTION VALUATIONS THAT CARRIED THE 36 DOOMED DECISIONS
#: (research-sql 38056211192 Q3b, matched to the packet-hour fixture's
#: valuation ids by contract and decision pattern): the fixture valuation
#: id, its contract, the valuation instant, the reading's age AT THE
#: VALUATION INSTANT (the decision's age_s less its lag after the valuation),
#: whether the in-cycle exploration attempt failed (the shape behind every
#: backstop row), and the production pass instant that then decided it.
REPLAY = [
    # fixture vid, slug, valuation instant, reading age at it, explore
    # attempt timed out in cycle, backstop pass instant (None: none)
    (67457, "atc-lmx-ju-tij-2026-10-10-ju", "08:58:48.677614", 27.68, False,
     None),
    (67458, "atc-lmx-ju-tij-2026-10-10-ju", "08:58:56.580675", 35.58, False,
     None),
    (67464, "aec-cfb-boise-frest-2026-10-10", "08:59:04.065704", 27.07, True,
     "09:00:51.323691"),
    (67465, "aec-mlb-lad-mil-2026-10-12", "08:59:28.609222", 16.61, True,
     "09:00:51.172940"),
    (67466, "aec-mlb-lad-mil-2026-10-12", "08:59:37.028757", 25.03, True,
     "09:00:50.978940"),
    (67472, "aec-cfb-tenn-ark-2026-10-10", "09:13:59.359384", 21.36, True,
     "09:14:56.273435"),
    (67473, "atc-mls-orl-clb-2026-10-10-orl", "09:14:14.163332", 25.16, True,
     "09:14:56.018140"),
    (67474, "atc-mls-orl-clb-2026-10-10-orl", "09:14:22.379819", 33.38, False,
     None),
    (67475, "atc-lmx-asl-san-2026-10-11-asl", "09:14:26.469166", 3.47, True,
     "09:14:55.807779"),
    (67476, "atc-lmx-asl-san-2026-10-11-asl", "09:14:34.785252", 11.79, True,
     "09:14:55.518236"),
    (67477, "atc-lmx-pum-caz-2026-10-11-pum", "09:14:43.604641", 20.60, True,
     "09:14:55.196464"),
    (67478, "atc-lmx-pum-caz-2026-10-11-pum", "09:14:52.024628", 29.03, False,
     None),
    (67486, "atc-mls-ner-sea-2026-10-10-ner", "09:29:09.025221", 30.03, False,
     None),
    (67487, "atc-lmx-pac-nec-2026-10-11-pac", "09:29:12.629010", 29.63, True,
     "09:30:51.752458"),
    (67488, "atc-lmx-pac-nec-2026-10-11-pac", "09:29:20.866026", 37.87, False,
     None),
    (67489, "aec-mlb-lad-mil-2026-10-12", "09:29:24.130047", 20.13, True,
     "09:30:51.619278"),
    (67490, "aec-mlb-lad-mil-2026-10-12", "09:29:32.564091", 28.56, True,
     "09:30:51.455708"),
    (67496, "atc-mls-nyr-sdg-2026-10-10-nyr", "09:44:04.536288", 25.54, True,
     "09:44:51.817881"),
    (67497, "aec-mlb-lad-mil-2026-10-12", "09:44:16.465636", 17.47, True,
     "09:44:51.675820"),
    (67498, "aec-mlb-lad-mil-2026-10-12", "09:44:24.983525", 25.98, True,
     "09:44:51.429119"),
]
#: the completed-game policy's lag after the valuation in production (Q3b:
#: 0.019-0.023 s): the hook's instant
HOOK_LAG_S = 0.02
#: the four production passes that wrote the 14 backstop rows, at the
#: instant of the last of their rows
PASSES = ["09:00:51.5", "09:14:56.5", "09:30:52.0", "09:44:52.0"]
#: the fixture's events whose first loss was the doomed decision
TEN = ["0dd06b09", "c4db3412", "c43e98d4", "0d24bc05", "310785f8", "496f7bb4",
       "b2f5462f", "09a2b919", "585d4356", "99f6f6c4"]


def _reading_fresh(age_at_val: float, val_at: float, at: float) -> bool:
    return (at - (val_at - age_at_val)) <= 30.0


def test_the_replay_table_is_the_production_shape():
    """Pins the table above to the audit's numbers: 36 doomed decisions on
    20 valuations of the 10 events; 4 valuations persisted with a reading
    already past the rule (12 in-cycle rows), 14 backstop rows each on a
    reading that was fresh at its valuation instant and expired in the wait
    for the pass; every one of the 20 is in the fixture under its slug."""
    events, vals, decs = H0._inputs("A")
    by_id = {v["id"]: v for v in vals}
    stale_by_vid = {}
    for d in decs:
        if STALE in (d["refusals"] or []):
            stale_by_vid.setdefault(d["valuation_id"],
                                    []).append(d["strategy"])
    assert sum(len(v) for v in stale_by_vid.values()) == 36
    assert set(stale_by_vid) == {r[0] for r in REPLAY}
    for vid, slug, val_at, age, timeout, pass_at in REPLAY:
        assert by_id[vid]["us_market_slug"] == slug
        if pass_at is not None:
            assert timeout and EXPLORE in stale_by_vid[vid]
            # fresh at the valuation instant, expired at the pass
            assert age <= 30.0
            assert not _reading_fresh(age, _epoch(val_at), _epoch(pass_at))
    expired_at_hook = [r for r in REPLAY if r[3] + HOOK_LAG_S > 30.0]
    assert [r[0] for r in expired_at_hook] == [67458, 67474, 67486, 67488]
    assert all(set(stale_by_vid[r[0]]) == {CG, EXPLORE, DEREK}
               for r in expired_at_hook)
    assert sum(1 for r in REPLAY if r[5] is not None) == 14
    fl = H0._first_losses(events, vals, decs)
    ten = [pid for pid, (f, _e, _v, _d) in fl.items() if f["code"] == STALE]
    assert sorted(p[:8] for p in ten) == sorted(TEN)
    assert len(ten) == 10


def _replayed_census(events, vals, decs, replayed: dict, rows: list):
    """The packet hour with the replayed valuations' production decisions
    replaced by the lane's (rows: paper_decisions of the replay session,
    keyed back to the fixture valuation ids)."""
    keep = [d for d in decs if d["valuation_id"] not in replayed]
    db_to_fix = {db: fix for fix, db in replayed.items()}
    order = {CG: 0, DEREK: 1, EXPLORE: 2}
    mine = sorted(
        ({"valuation_id": db_to_fix[r["valuation_id"]],
          "verdict": r["verdict"], "refusal": r["refusal"],
          "refusals": list(r["refusals"] or []), "strategy": r["strategy"]}
         for r in rows if r["valuation_id"] in db_to_fix),
        key=lambda d: (d["valuation_id"], order.get(d["strategy"], 9)))
    return keep + mine


def _capture_doc(row: dict, *, session_note: str) -> dict:
    return {
        "version": T.VERSION, "census_version": FL.VERSION,
        "taxonomy_version": RT.VERSION,
        "source": {
            "kind": "PAPER-1 replay of the packet-hour fixture through the "
                    "lane's decision path on real Postgres -- NOT a "
                    "production readback",
            "lane": "rc6/paper-no-doomed-decisions",
            "fixture": H0.FIX.name,
            "replayed_valuations": [r[0] for r in REPLAY],
            "replayed_through": ("paper_runtime.decide_valuation at each "
                                 "valuation's production instant (fixed "
                                 "clock; every instant shifted forward by "
                                 "one constant so the replay's research "
                                 "model precedes it), then paper_pass with "
                                 "the derek, benchmark_completed_game and "
                                 "exploration steps at the four production "
                                 "pass instants"),
            "note": session_note,
            "decision_order": ("a replayed valuation's decisions in the "
                               "plan order (completed-game, Derek, "
                               "exploration); the gate's own tie-break "
                               "between same-instant decisions is named in "
                               "order_ambiguity")},
        "order_ambiguous_rows": [row["k"]] if row["order_ambiguous_events"]
        else [],
        "rows": [row]}


def _comparable(row: dict) -> dict:
    """The capture's cells the replay pins (the ECONOMIC per-code split of
    same-instant decisions is the gate's tie-break, named, not pinned)."""
    return {
        "k": row["k"], "events": row["events"], "entered": row["entered"],
        "unavailable": row["unavailable"], "by_class": row["by_class"],
        "software": row["software"], "unclassified": row["unclassified"],
        "software_by_code": row["software_by_code"],
        "software_codes_truncated": row["software_codes_truncated"],
        "software_rows": [r for r in row["by_code"]
                          if r["class"] == RT.SOFTWARE],
        "first_loss": row["first_loss"],
        "paper_decision_rows": sorted(
            (r["stage"], r["class"]) for r in row["by_code"]
            if r["source"] == "paper_decisions"),
        "by_competition": row["by_competition"]}


@pg
async def test_d_the_ten_production_events_replayed_through_the_lane_are_economic_at_ev(  # noqa: E501
        paper_env, monkeypatch):
    """THE CENSUS REPLAY. The 20 valuations are written with their production
    readings and decided by the real hook at their production instants (the
    three strategies, Derek with a research model as production had) and by
    the real pass steps at the four production pass instants; the packet
    hour's census is then run with the lane's decisions in place of the
    production ones. BASE: the 4 valuations persisted past the rule get 12
    REFUSE STALE rows in cycle and the 14 backstop candidates 14 more, and
    the 10 events stay SOFTWARE at FAIR_VALUE. HEAD: 12 DEFERRED_PAST_THE_
    PROBABILITY_DEADLINE and 14 EXPIRED_BEFORE_DECISION hook rows, no STALE
    decision, every one of the 10 ECONOMIC at EV, by_class 74 / 24 / 6 / 0
    -- and the readback capture the H0 expectation reads is this table.

    THE CLOCK: every production instant is shifted forward by ONE constant
    (the hour's shape -- every reading age, hook lag and pass lag -- is
    preserved) so that the research model Derek decides with, frozen at the
    test's own instant and immutable (bettor_funded_models.created_at), is
    registered before the first decision, as production's was."""
    conn = await H.connect()
    shift = (time.time() + 30.0) - _epoch("08:50:00.0")

    def _at(hms: str) -> float:
        return _epoch(hms) + shift
    t_start = _at("08:50:00.0")
    model_id = "derek-research-model-paper1-replay"
    try:
        await _cleanup(conn)
        await PL.purge_research_models(conn)
        await PL.train_model(conn, monkeypatch, model_id=model_id)
        assert (await PD.research_model(conn, at=t_start))["ok"]
        acct = await PL.new_account(conn, "nd-d", now=t_start)
        t = Book(t_start)
        client = PL.client(t)
        replayed: dict = {}                     # fixture vid -> db vid
        plan = sorted(REPLAY, key=lambda r: _epoch(r[2]))
        passes = [_at(p) for p in PASSES]
        done_passes = 0
        for fix_vid, slug, val_at, age, timeout, _pass_at in plan:
            at = _at(val_at)
            # the production passes that fall before this valuation
            while done_passes < len(passes) and passes[done_passes] < at:
                t.t = passes[done_passes]
                p = await _pass(conn, acct, client, now=passes[done_passes])
                assert p["ran"] and not p["errors"], p["errors"]
                done_passes += 1
            v = await PL.valuation(conn, slug=PL.SYN + "p1-" + slug,
                                   decided_at=at, pin_age_s=age,
                                   compatibility="INCOMPATIBLE")
            replayed[fix_vid] = v["valuation_id"]
            # a book with no edge for any probability the strategies hold
            t.set(v["slug"], offers=[(0.90, 5000)], bids=[(0.88, 5000)])
            t.t = at + HOOK_LAG_S
            if timeout:
                # production: the exploration hook attempt timed out; the
                # completed-game policy and Derek decided
                await _attempted_in_cycle(conn, acct, v["valuation_id"],
                                          EXPLORE)
                strategies = [CG]
            else:
                strategies = None
            g = await _hook(conn, acct, client, v["valuation_id"],
                            now=at + HOOK_LAG_S, strategies=strategies)
            assert g.get("error") is None, g
        while done_passes < len(passes):
            t.t = passes[done_passes]
            p = await _pass(conn, acct, client, now=passes[done_passes])
            assert p["ran"] and not p["errors"], p["errors"]
            done_passes += 1
        # ── THE RECORDS ──────────────────────────────────────────────
        rows = [dict(r) for r in await conn.fetch(
            "SELECT valuation_id, strategy, verdict, refusal, refusals, "
            "       pinnacle FROM paper_decisions WHERE session_id=$1 "
            " ORDER BY decided_at, decision_id", acct["session_id"])]
        hook = [dict(r) for r in await conn.fetch(
            "SELECT valuation_id, strategy, stage, outcome, error FROM "
            " paper_hook_failures WHERE session_id=$1", acct["session_id"])]
        stale_rows = [r for r in rows if STALE in _codes(r)]
        assert stale_rows == [], [(r["valuation_id"], r["strategy"])
                                  for r in stale_rows]
        for r in rows:
            assert H.j(r["pinnacle"])["age_s"] <= 30.0, r
        by_error = {}
        for h in hook:
            by_error.setdefault(h["error"], []).append(h)
        assert len(by_error.get(PAST, [])) == 12
        assert {h["strategy"] for h in by_error[PAST]} == {CG, EXPLORE, DEREK}
        assert {h["valuation_id"] for h in by_error[PAST]} == {
            replayed[v] for v in (67458, 67474, 67486, 67488)}
        assert len(by_error.get(EXPIRED, [])) == 14
        assert {h["strategy"] for h in by_error[EXPIRED]} == {EXPLORE}
        assert {h["valuation_id"] for h in by_error[EXPIRED]} == {
            replayed[r[0]] for r in REPLAY if r[5] is not None}
        assert all(h["stage"] == "PAPER_PASS" for h in by_error[EXPIRED])
        # the model Derek decided with
        derek_rows = [r for r in rows if r["strategy"] == DEREK]
        assert derek_rows and all(
            PD.R_NO_RESEARCH_MODEL not in _codes(r) for r in derek_rows)
        # ── THE CENSUS ───────────────────────────────────────────────
        events, vals, decs = H0._inputs("A")
        decs2 = _replayed_census(events, vals, decs, replayed, rows)
        got = FL.census(events, vals, decs2)
        tot = got["totals"]
        assert tot["by_class"] == {"SOFTWARE": 74, "ECONOMIC": 24,
                                   "EXTERNAL": 6, "UNCLASSIFIED": 0}, tot
        assert H0._by_code(got, "SOFTWARE") == [
            ("NORMALIZED", H0.NO_EXACT, 35), ("NORMALIZED", H0.AGE_UNKNOWN, 1),
            ("FAIR_VALUE", H0.QUOTE_STALE, 38)]
        assert all(st == "EV" for st, _c, _n in H0._by_code(got, "ECONOMIC"))
        assert sum(n for _s, _c, n in H0._by_code(got, "ECONOMIC")) == 24
        assert STALE not in {r["code"] for r in tot["by_code"]}
        assert H0._reconciles(tot)
        fl = H0._first_losses(events, vals, decs2)
        for pid, (f, _ev, _vs, _ds) in fl.items():
            if pid[:8] in TEN:
                assert (f["class"], f["stage"], f["source"]) == (
                    RT.ECONOMIC, "EV", "paper_decisions"), (pid, f)
        # the projection H0 made by arithmetic, produced by the code
        proj = FL.census(events, vals, H0._without_stale_decisions(decs))
        assert proj["totals"]["by_class"] == tot["by_class"]
        # ── THE READBACK CAPTURE ─────────────────────────────────────
        now = H0.PACKET_NOW
        row = T.census_row(events, vals, decs2, k=0, now=now,
                           since=now - 3600.0, until=now + 1.0)
        assert row["unclassified"] == 0
        for r in row["by_code"]:
            assert r["code"] != STALE
            if r["source"] == "paper_decisions":
                assert r["stage"] in ("EV", "ENTER_PASS") or \
                    r["class"] != RT.SOFTWARE, r
        doc = _capture_doc(row, session_note=(
            "generated by tests/test_rc63_paper_no_doomed_decisions.py "
            "(RC63_PAPER1_WRITE_CAPTURE=1); the test compares the committed "
            "file with the table it recomputes"))
        if os.environ.get("RC63_PAPER1_WRITE_CAPTURE"):
            CAPTURE.write_text(json.dumps(doc, indent=1) + "\n")
        assert CAPTURE.exists(), "the H0 expectation case reads this file"
        committed = json.loads(CAPTURE.read_text())
        assert committed["source"]["kind"].startswith("PAPER-1 replay")
        assert committed["source"]["replayed_valuations"] == \
            [r[0] for r in REPLAY]
        assert len(committed["rows"]) == 1
        assert _comparable(committed["rows"][0]) == _comparable(row), \
            "the committed capture is not this replay's table: regenerate " \
            "it with RC63_PAPER1_WRITE_CAPTURE=1, never edit it"
    finally:
        await _cleanup(conn, now=time.time())
        await PL.purge_research_models(conn)
        await conn.close()


# ── (e) THE READERS TOLERATE THE TWO OUTCOMES ──────────────────────────

@pg
async def test_e_the_readers_of_the_paper_records_tolerate_the_new_outcomes(
        paper_env):
    """The agents' status contract (Derek's failures from the evaluation
    attempts), the funnel receipt, Audrey's paper operations audit, the paper
    experiment read and Audrey's daily audit all run over records carrying
    EXPIRED_BEFORE_DECISION and DEFERRED_PAST_THE_PROBABILITY_DEADLINE rows
    written by the real writers, and count neither as a failure."""
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await _cleanup(conn)
        acct = await PL.new_account(conn, "nd-e", now=now)
        v = await PL.valuation(conn, decided_at=now - 40, pin_age_s=5.0,
                               compatibility="INCOMPATIBLE")
        ctx = {"session_id": acct["session_id"],
               "account_id": acct["account_id"], "now": now,
               "clock": lambda: now}

        async def derek_failures():
            tx = conn.transaction()
            await tx.start()
            try:
                body = await ASC.read(conn, now=now + 1,
                                      env={"EXT_PINNACLE_SHADOW": "on"})
            finally:
                await tx.rollback()
            by = {s["id"]: s for s in body["subjects"]}
            return by["DEREK"]["failures"]["count"], body
        before, _ = await derek_failures()
        n = await PD.record_expired_candidates(
            conn, ctx=ctx, strategy=EXPLORE, at=now, max_age_s=30.0,
            rows=[{"id": v["valuation_id"], "observed_at": now - 45,
                   "decided_at": now - 40, "us_market_slug": v["slug"]}])
        assert n == 1
        await PD.record_deferred_by_name(
            conn, ctx=ctx, valuation_id=v["valuation_id"], strategy=CG,
            res=PD.doomed_by_expiry(
                {"decided_via": PD.DECIDED_VIA_CYCLE},
                {"qualification": "STALE", "age_s": 31.0, "limit_s": 30.0,
                 "at": now - 31, "decided_at": now}),
            stage=IN_CYCLE)
        rows = await _hook_rows(conn, acct, v["valuation_id"])
        assert sorted(r["error"] for r in rows) == sorted([EXPIRED, PAST])
        assert all(r["outcome"] == "DEFERRED" for r in rows)
        after, body = await derek_failures()
        assert after == before            # a deferral by name is no failure
        assert "DEREK" in {s["id"] for s in body["subjects"]}
        got = await AF.read(conn, since=now - 3600, until=now + 60,
                            account_id=acct["account_id"])
        assert isinstance(got.get("agents"), list)
        audit = await POA.audit(conn, ctx, now=now + 1)
        assert isinstance(audit.get("findings"), list)
        x = await EXP.experiment(conn, now=now + 1,
                                 account_id=acct["account_id"])
        assert isinstance(x, dict)
        aud = await AA.run_due(conn, now=now + 1)
        assert isinstance(aud, dict) and "at" in aud
        # and the attempts table carries no mislabelled outcome for them
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_evaluation_attempts WHERE "
            " valuation_id=$1", v["valuation_id"]) == 0
    finally:
        await _cleanup(conn, now=now)
        await conn.close()
