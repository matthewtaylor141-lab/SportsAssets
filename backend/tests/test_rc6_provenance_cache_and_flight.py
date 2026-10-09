"""RC6 PROVENANCE -- THE CACHED CONTEXT NEVER OUTLIVES A CHANGE, ONE SESSION
SHARES ONE VERIFICATION, AND A STOPPED LANE IS A NAMED REFUSAL.

The owner's directive (2026-10-09): "never cache a successful verification
across changed training records or settlements. Add behavioural tests
showing that corrected labels, missing decisions and changed feature vectors
still refuse. Verify CPU offloading and bounded concurrency under parallel
PAPER decisions." test_rc6_provenance_refuses_after_change proves the CHECK
refuses each change; test_rc6_provenance_open_defects pins the three defects
closed (D1 a vector under a stale identity, D2 the cached context, D3 one
verification per cold context). This file pins the contracts of the fixes:

A · THE CACHE (a real Postgres, migration 365's triggers, the PAPER
    decision's own keyed context). A session's cached verification is
    served while the ledger is unchanged -- no re-verification -- and the
    NEXT decision after any change refuses: a corrected label, a voided or
    deleted settlement, a deleted observation, a vector rewritten with or
    without its identity, a vector that gained a feature, a moved fixture
    (a field the md5 prototype did not hash), a settlement re-based or
    re-read, the registry's stored digest rewritten. Every bypass of the
    counter still reads as a change: the append-only trigger off
    (session_replication_role = replica), the counter's own trigger
    disabled and re-enabled around the write, a table rewrite that changes
    values without row triggers, the counter row edited back to its old
    value. A trigger left disabled makes the stamp unreadable and nothing is
    served from the cache. The production writers (an outcome join, a new
    observation) do not move the stamp. The stamp read is cheap.
B · THE FLIGHT (the stand-in ledger of test_rc6_provenance_lane_bounds, the
    decision deadline of paper_derek.bounded_decision, the CPU lane held by
    a gate so the order of events is fixed, not timed). One verification
    serves a session's concurrent decisions; a waiter whose own deadline
    passes is cut alone; a cut or failed leader caches nothing and one
    waiter leads again; a change while the flight runs sends the decisions
    that arrived after its read to a fresh verification (which refuses);
    an unreadable stamp never serves a shared or cached success; a decision
    of an earlier instant shares the model only when it was registered by
    that instant and reads its own void measure; the flight table is
    bounded and empties.
C · THE STOPPED LANE (a fresh interpreter). research_model never raises
    when the CPU lane is shut down under its check: a named refusal, no hash
    ran. Its own cancellation (a decision's deadline) still propagates.

ALL DATA SYNTHETIC; part A runs in one rolled-back transaction per test.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import sys
import threading
import time

import pytest

from tests import test_rc6_provenance_lane_bounds as LB
from tests import test_rc6_provenance_refuses_after_change as L

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _clear():
    from sportsassets.agents import paper_derek as PD
    PD._CONTEXT_CACHE.clear()
    PD._CONTEXT_FLIGHTS.clear()


# ═════════════════════════════════════════════════════════════════════
# A · THE CACHE NEVER OUTLIVES A CHANGE (real Postgres)
# ═════════════════════════════════════════════════════════════════════

def _ledger_run(monkeypatch, body):
    """Build, register and verify the research model through production's
    own writers in ONE rolled-back transaction; `body(conn, made, spy)`."""
    import asyncpg
    spy = L._Spy(monkeypatch)
    _clear()

    async def go():
        conn = await asyncpg.connect(L._dsn())
        tr = conn.transaction()
        await tr.start()
        try:
            await L._isolate(conn)
            made = await L._ledger(conn)
            await L._registered(conn, model_id=L.SYN + "model")
            # count the hashes of the PAPER path only (the fit and the
            # registration hash the records too)
            del spy.runs[:]
            return await body(conn, made, spy)
        finally:
            await tr.rollback()
            await conn.close()
    try:
        return asyncio.run(go())
    finally:
        _clear()


async def _keyed(conn, at: float, key: str = L.SYN + "session") -> dict:
    from sportsassets.agents import paper_derek as PD
    return await PD._context(conn, {"now": at, "context_cache_key": key})


async def _stamp(conn):
    from sportsassets.agents import paper_derek as PD
    return await PD._training_set_stamp(conn)


async def _counter(conn) -> int:
    return await conn.fetchval(
        "SELECT changes FROM research_training_set_changes "
        " WHERE scope = 'DEREK_RESEARCH_TRAINING_SET'")


def test_an_unchanged_ledger_is_served_from_the_cache_without_re_verifying(
        monkeypatch):
    async def body(conn, made, spy):
        t0 = time.time() + 1.0
        first = await _keyed(conn, t0)
        n_first = len(spy.runs)
        again = await _keyed(conn, t0 + 1.0)
        later = await _keyed(conn, t0 + 30.0)
        return first, again, later, n_first, len(spy.runs)

    first, again, later, n_first, n_all = _ledger_run(monkeypatch, body)
    assert first["model"]["ok"] is True
    assert first["model"]["provenance_verified"] is True
    # the cache still does its job: one verification, then served as is
    assert again is first and later is first
    assert n_first == 1 and n_all == 1


async def _vid_oid(made, i):
    vid, oid, _won = made[i]
    return vid, oid


async def _replica(conn, sql, *args):
    """A repair made with the research table's append-only trigger off."""
    await conn.execute("SET LOCAL session_replication_role = replica")
    await conn.execute(sql, *args)
    await conn.execute("SET LOCAL session_replication_role = origin")


async def _features(conn, vid) -> dict:
    return json.loads(await conn.fetchval(
        "SELECT features::text FROM derek_research_observations "
        " WHERE valuation_id = $1", vid))


async def _corrected_label(conn, made):
    vid, _ = await _vid_oid(made, 5)
    await conn.execute("UPDATE external_valuations SET outcome = 1 - outcome"
                       " WHERE id = $1", vid)


async def _settlement_voided(conn, made):
    vid, _ = await _vid_oid(made, 6)
    await conn.execute(
        "UPDATE external_valuations SET outcome_known = FALSE, outcome = NULL,"
        " outcome_at = NULL, outcome_basis = 'CONFIRMED_VOID' WHERE id = $1",
        vid)


async def _settlement_rebased(conn, made):
    vid, _ = await _vid_oid(made, 7)
    await conn.execute("UPDATE external_valuations SET outcome_basis = "
                       "'VENUE_REPORTED_OUTCOME' WHERE id = $1", vid)


async def _settlement_reread(conn, made):
    vid, _ = await _vid_oid(made, 8)
    await conn.execute("UPDATE external_valuations SET outcome_at = "
                       "outcome_at + interval '1 second' WHERE id = $1", vid)


async def _valuation_deleted(conn, made):
    vid, _ = await _vid_oid(made, 9)
    await _replica(conn, "DELETE FROM external_valuations WHERE id = $1", vid)


async def _observation_deleted(conn, made):
    _, oid = await _vid_oid(made, 10)
    await _replica(conn, "DELETE FROM derek_research_observations "
                         " WHERE observation_id = $1", oid)


async def _vector_rewritten_with_identity(conn, made):
    from sportsassets import bettor_funded_model as FM
    vid, _ = await _vid_oid(made, 11)
    f = await _features(conn, vid)
    f["acquisition_price"] = round(1.0 - float(f["acquisition_price"]), 9)
    await _replica(conn, "UPDATE derek_research_observations SET features = "
                         "$2::jsonb, feature_sha = $3 WHERE valuation_id = $1",
                   vid, json.dumps(f), FM.feature_sha(f))


async def _vector_rewritten_under_its_old_identity(conn, made):
    vid, _ = await _vid_oid(made, 12)
    f = await _features(conn, vid)
    f["acquisition_price"] = round(1.0 - float(f["acquisition_price"]), 9)
    await _replica(conn, "UPDATE derek_research_observations SET features = "
                         "$2::jsonb WHERE valuation_id = $1",
                   vid, json.dumps(f))


async def _vector_gained_a_feature(conn, made):
    from sportsassets import bettor_funded_model as FM
    vid, _ = await _vid_oid(made, 13)
    f = await _features(conn, vid)
    f["late_addition"] = 1.0
    await _replica(conn, "UPDATE derek_research_observations SET features = "
                         "$2::jsonb, feature_sha = $3 WHERE valuation_id = $1",
                   vid, json.dumps(f), FM.feature_sha(f))


async def _fixture_moved(conn, made):
    vid, _ = await _vid_oid(made, 14)
    await _replica(conn, "UPDATE derek_research_observations SET fixture = "
                         "fixture || '-moved' WHERE valuation_id = $1", vid)


async def _registry_digest_rewritten(conn, made):
    # the registry refuses this edit by trigger (a fitted object is not
    # edited): a repair with that trigger off
    await _replica(
        conn, "UPDATE bettor_funded_models SET training_provenance = "
              "jsonb_set(training_provenance, '{records_sha}', "
              "to_jsonb('0'::text)) WHERE model_id = $1", L.SYN + "model")


CHANGES = {
    "corrected_label": _corrected_label,
    "settlement_voided": _settlement_voided,
    "settlement_rebased": _settlement_rebased,
    "settlement_reread": _settlement_reread,
    "valuation_deleted": _valuation_deleted,
    "observation_deleted": _observation_deleted,
    "vector_rewritten_with_identity": _vector_rewritten_with_identity,
    "vector_rewritten_under_its_old_identity":
        _vector_rewritten_under_its_old_identity,
    "vector_gained_a_feature": _vector_gained_a_feature,
    "fixture_moved": _fixture_moved,
    "registry_digest_rewritten": _registry_digest_rewritten,
}


def _refuses(ctx: dict) -> None:
    from sportsassets import bettor_funded_model as FM
    from sportsassets.agents import paper_derek as PD
    m = ctx["model"]
    assert m["ok"] is False, m
    assert m["refusal"] == PD.R_MODEL_UNVERIFIED, m
    assert m["why"] == FM.R_TRAINING_RECORDS_DO_NOT_REPRODUCE, m
    assert m["provenance_verified"] is False


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_the_next_decision_after_a_change_refuses(monkeypatch, change):
    """A verified, cached context; ONE change; the session's next decision
    one second later is not served the cached success: the stamp moved, the
    context was recomputed, and it refuses -- as a context with no cache
    does."""
    async def body(conn, made, spy):
        t0 = time.time() + 1.0
        first = await _keyed(conn, t0)
        before = await _stamp(conn)
        await CHANGES[change](conn, made)
        after = await _stamp(conn)
        again = await _keyed(conn, t0 + 1.0)
        fresh = await _keyed(conn, t0 + 1.0, key=None)
        return first, before, after, again, fresh

    first, before, after, again, fresh = _ledger_run(monkeypatch, body)
    assert first["model"]["provenance_verified"] is True
    assert before is not None and after is not None and after != before
    assert again is not first
    _refuses(again)
    _refuses(fresh)


def test_a_change_with_the_counter_trigger_disabled_and_restored_refuses(
        monkeypatch):
    """The counter's own trigger switched off around the correction and
    back on: the counter did not move, the trigger's catalog row did."""
    trg = "research_training_set_valuation_changed"

    async def body(conn, made, spy):
        t0 = time.time() + 1.0
        first = await _keyed(conn, t0)
        n0, s0 = await _counter(conn), await _stamp(conn)
        vid, _ = await _vid_oid(made, 15)
        await conn.execute("ALTER TABLE external_valuations DISABLE TRIGGER "
                           + trg)
        await conn.execute("UPDATE external_valuations SET outcome = "
                           "1 - outcome WHERE id = $1", vid)
        await conn.execute("ALTER TABLE external_valuations ENABLE ALWAYS "
                           "TRIGGER " + trg)
        n1, s1 = await _counter(conn), await _stamp(conn)
        again = await _keyed(conn, t0 + 1.0)
        return first, n0, n1, s0, s1, again

    first, n0, n1, s0, s1, again = _ledger_run(monkeypatch, body)
    assert first["model"]["provenance_verified"] is True
    assert n1 == n0                      # the write itself was not counted
    assert s1 is not None and s1 != s0   # and the stamp still moved
    _refuses(again)


def test_a_table_rewrite_that_changes_values_without_row_triggers_refuses(
        monkeypatch):
    """ALTER COLUMN TYPE ... USING rewrites values and fires no row trigger:
    the table's storage identity is in the stamp."""
    async def body(conn, made, spy):
        t0 = time.time() + 1.0
        first = await _keyed(conn, t0)
        n0, s0 = await _counter(conn), await _stamp(conn)
        _, oid = await _vid_oid(made, 16)
        assert "'" not in oid
        await conn.execute(
            "ALTER TABLE derek_research_observations ALTER COLUMN fixture "
            "TYPE text USING (CASE WHEN observation_id = '%s' "
            "THEN fixture || '-rewritten' ELSE fixture END)" % oid)
        n1, s1 = await _counter(conn), await _stamp(conn)
        again = await _keyed(conn, t0 + 1.0)
        return first, n0, n1, s0, s1, again

    first, n0, n1, s0, s1, again = _ledger_run(monkeypatch, body)
    assert first["model"]["provenance_verified"] is True
    assert n1 == n0
    assert s1 is not None and s1 != s0
    _refuses(again)


def test_the_counter_row_edited_back_or_removed_is_not_the_same_stamp(
        monkeypatch):
    async def body(conn, made, spy):
        s0 = await _stamp(conn)
        await conn.execute("UPDATE research_training_set_changes "
                           "   SET changes = changes")
        s1 = await _stamp(conn)
        await conn.execute("DELETE FROM research_training_set_changes")
        s2 = await _stamp(conn)
        return s0, s1, s2

    s0, s1, s2 = _ledger_run(monkeypatch, body)
    assert s0 is not None and s1 is not None and s1 != s0
    assert s2 is None


def test_a_trigger_left_disabled_serves_nothing_from_the_cache(monkeypatch):
    """No proof, no cached success: with a counter trigger off, the stamp is
    unreadable and every decision re-verifies (still verified: nothing
    changed)."""
    async def body(conn, made, spy):
        await conn.execute("ALTER TABLE derek_research_observations DISABLE "
                           "TRIGGER research_training_set_observation_changed")
        stamp = await _stamp(conn)
        t0 = time.time() + 1.0
        first = await _keyed(conn, t0)
        again = await _keyed(conn, t0 + 1.0)
        return stamp, first, again, len(spy.runs)

    stamp, first, again, hashes = _ledger_run(monkeypatch, body)
    assert stamp is None
    assert first["model"]["provenance_verified"] is True
    assert again["model"]["provenance_verified"] is True
    assert again is not first and hashes == 2


def test_the_production_writers_do_not_move_the_stamp(monkeypatch):
    """The trigger does nothing on the hot path: an outcome join on an
    unsettled valuation (the only kind of valuation UPDATE production makes)
    and a new research observation leave the stamp as it was, so the cache
    keeps serving between corrections."""
    from sportsassets import bettor_external_shadow as ext
    from sportsassets.agents import derek_research as DR

    async def body(conn, made, spy):
        t0 = time.time() + 1.0
        first = await _keyed(conn, t0)
        s0 = await _stamp(conn)
        vid = await L._valuation(conn, cid=L.SYN + "late", price=0.41,
                                 decided=time.time() - 7200.0)
        row = await conn.fetchrow(DR._SELECT + " AND v.id = $2",
                                  ext.EXPERIMENT_ID, vid)
        obs, why = DR.observation_from_row(dict(row))
        assert obs is not None, why
        assert await conn.fetchval(DR.INSERT_SQL, *DR._insert_args(obs))
        await conn.execute(
            "UPDATE external_valuations SET outcome_known = TRUE, outcome = 1,"
            " outcome_at = now(), outcome_basis = 'VENUE_SETTLEMENT_PRICE' "
            " WHERE id = $1 AND outcome_known = FALSE", vid)
        s1 = await _stamp(conn)
        again = await _keyed(conn, t0 + 1.0)
        return first, s0, s1, again, len(spy.runs)

    first, s0, s1, again, hashes = _ledger_run(monkeypatch, body)
    assert s0 is not None and s1 == s0
    assert again is first and hashes == 1


def test_the_counter_moves_once_per_statement_and_a_rolled_back_move_returns(
        monkeypatch):
    """A correction of many rows moves the counter once (not once per row);
    every later statement that changes a training record moves it again,
    in the same transaction too; a savepoint rolled back takes its move
    with it and the next change moves it again."""
    async def body(conn, made, spy):
        vids = [made[i][0] for i in (20, 21, 22)]
        n = [await _counter(conn)]
        await conn.execute("UPDATE external_valuations SET outcome = "
                           "1 - outcome WHERE id = ANY($1::bigint[])", vids)
        n.append(await _counter(conn))
        await conn.execute("UPDATE external_valuations SET outcome = "
                           "1 - outcome WHERE id = $1", vids[0])
        n.append(await _counter(conn))
        await conn.execute("SAVEPOINT sp")
        await conn.execute("UPDATE external_valuations SET outcome = "
                           "1 - outcome WHERE id = $1", vids[1])
        n.append(await _counter(conn))
        await conn.execute("ROLLBACK TO SAVEPOINT sp")
        n.append(await _counter(conn))
        await conn.execute("UPDATE external_valuations SET outcome = "
                           "1 - outcome WHERE id = $1", vids[2])
        n.append(await _counter(conn))
        return n

    n = _ledger_run(monkeypatch, body)
    assert [b - n[0] for b in n] == [0, 1, 2, 3, 2, 3], n


def test_the_stamp_read_is_cheap(monkeypatch):
    """What a cache hit costs (the md5 prototype: ~257 ms of Postgres at
    production's 28,303 records). Bound generous for a loaded runner."""
    async def body(conn, made, spy):
        took = []
        for _ in range(100):
            t = time.perf_counter()
            assert await _stamp(conn) is not None
            took.append(time.perf_counter() - t)
        return took

    took = _ledger_run(monkeypatch, body)
    assert statistics.median(took) < 0.025, statistics.median(took)


# ═════════════════════════════════════════════════════════════════════
# B · ONE FLIGHT PER CONTEXT KEY (the stand-in ledger, no database)
# ═════════════════════════════════════════════════════════════════════

class _Ledger(LB._Conn):
    """LB's stand-in connection over a SHARED ledger state a test can change
    while a flight runs: its label rows and its change stamp."""

    def __init__(self, w, state):
        super().__init__(w["rows"], w["model_row"], w["prov"])
        self.state = state

    @property
    def labels(self):
        return self.state["rows"]

    @labels.setter
    def labels(self, value):
        pass

    async def fetchval(self, sql, *a):
        if "research_training_set_changes" in sql:
            self.state["stamp_reads"] = self.state.get("stamp_reads", 0) + 1
            if self.state.get("yield_on_stamp"):
                await asyncio.sleep(0)
            return self.state["stamp"]
        return await super().fetchval(sql, *a)


def _keyed_decision(conn, key):
    """One PAPER decision's model step with the hook's context key, as
    decide_one takes it (paper_derek._context under bounded_decision)."""
    from sportsassets.agents import paper_derek as PD

    async def make(ctx):
        d = await PD._context(conn, dict(ctx, context_cache_key=key))
        return {"model": d["model"]}
    return make


def _gated(body):
    """Run `body(gate)` with the CPU lane held until `gate` is set (so a
    decision's lane jobs queue and the order of events is fixed), counting
    every training-set hash."""
    from sportsassets import cpu_lane as CPU
    hashes = LB._Hashes()
    gate = threading.Event()
    _clear()

    async def go():
        holding = asyncio.ensure_future(CPU.run(gate.wait, 30))
        await asyncio.sleep(0.05)
        try:
            return await body(gate)
        finally:
            gate.set()
            await holding
    try:
        got = asyncio.run(go())
        return got, len(hashes.spans)
    finally:
        gate.set()
        hashes.undo()
        _clear()


async def _until(cond, limit_s: float = 10.0):
    t = time.monotonic()
    while not cond():
        assert time.monotonic() - t < limit_s, "condition never held"
        await asyncio.sleep(0.005)


def test_one_verification_serves_concurrent_decisions_and_the_next_is_cached():
    from sportsassets.agents import paper_derek as PD
    w = LB._world(LB.RECORDS)
    state = {"rows": w["rows"], "stamp": "S0"}
    key = "flight-1"
    at = time.time()

    async def body(gate):
        conns = [_Ledger(w, state) for _ in range(24)]
        tasks = [asyncio.ensure_future(LB._decide(
            _keyed_decision(c, key), at=at, timeout_s=600.0)) for c in conns]
        await _until(lambda: key in PD._CONTEXT_FLIGHTS)
        await asyncio.sleep(0.05)
        gate.set()
        got = await asyncio.gather(*tasks)
        later = await LB._decide(_keyed_decision(_Ledger(w, state), key),
                                 at=at + 1.0, timeout_s=600.0)
        return got, later, dict(PD._CONTEXT_FLIGHTS)

    (got, later, flights), hashes = _gated(body)
    assert [g["outcome"] for g in got] == ["VERIFIED"] * 24
    assert later["outcome"] == "VERIFIED"
    assert hashes == 1
    assert flights == {}


def test_a_waiter_whose_deadline_passes_is_cut_alone():
    from sportsassets.agents import paper_derek as PD
    w = LB._world(LB.RECORDS)
    state = {"rows": w["rows"], "stamp": "S0"}
    key = "flight-2"
    at = time.time()

    async def body(gate):
        lead = asyncio.ensure_future(LB._decide(
            _keyed_decision(_Ledger(w, state), key), at=at, timeout_s=600.0))
        await _until(lambda: key in PD._CONTEXT_FLIGHTS)
        cut = await LB._decide(_keyed_decision(_Ledger(w, state), key),
                               at=at, timeout_s=0.2)
        flight_after_cut = key in PD._CONTEXT_FLIGHTS
        rest = [asyncio.ensure_future(LB._decide(
            _keyed_decision(_Ledger(w, state), key), at=at, timeout_s=600.0))
            for _ in range(4)]
        await asyncio.sleep(0.05)
        gate.set()
        return cut, flight_after_cut, await lead, await asyncio.gather(*rest)

    (cut, flight_after_cut, lead, rest), hashes = _gated(body)
    assert cut["outcome"] == "DEADLINE" and cut["verified"] is False
    assert flight_after_cut is True       # the shared job ran on
    assert lead["outcome"] == "VERIFIED"
    assert [r["outcome"] for r in rest] == ["VERIFIED"] * 4
    assert hashes == 1


def test_a_cut_leader_caches_nothing_and_one_waiter_leads_again():
    from sportsassets.agents import paper_derek as PD
    w = LB._world(LB.RECORDS)
    state = {"rows": w["rows"], "stamp": "S0"}
    key = "flight-3"
    at = time.time()

    async def body(gate):
        lead = asyncio.ensure_future(LB._decide(
            _keyed_decision(_Ledger(w, state), key), at=at, timeout_s=0.3))
        await _until(lambda: key in PD._CONTEXT_FLIGHTS)
        waiters = [asyncio.ensure_future(LB._decide(
            _keyed_decision(_Ledger(w, state), key), at=at, timeout_s=600.0))
            for _ in range(6)]
        lead_r = await lead
        await asyncio.sleep(0.05)
        cached_after_cut = PD._CONTEXT_CACHE.get(key)
        gate.set()
        return lead_r, cached_after_cut, await asyncio.gather(*waiters)

    (lead, cached_after_cut, waiters), hashes = _gated(body)
    assert lead["outcome"] == "DEADLINE" and lead["verified"] is False
    assert cached_after_cut is None
    assert [r["outcome"] for r in waiters] == ["VERIFIED"] * 6
    # the cut leader's queued job never ran; ONE new flight served the rest
    assert hashes == 1


def test_a_failed_flight_is_never_cached_and_its_waiters_retry(monkeypatch):
    from sportsassets.agents import derek_policy as DP
    from sportsassets.agents import paper_derek as PD
    w = LB._world(LB.RECORDS)
    state = {"rows": w["rows"], "stamp": "S0"}
    key = "flight-4"
    at = time.time()
    calls = []

    async def void_measure(conn, *, through):
        calls.append(through)
        if len(calls) == 1:
            raise RuntimeError("the leader's void read failed")
        return {"ok": True, "attempt": len(calls)}
    monkeypatch.setattr(DP, "void_measure", void_measure)

    async def body(gate):
        lead = asyncio.ensure_future(LB._decide(
            _keyed_decision(_Ledger(w, state), key), at=at, timeout_s=600.0))
        await _until(lambda: key in PD._CONTEXT_FLIGHTS)
        waiters = [asyncio.ensure_future(LB._decide(
            _keyed_decision(_Ledger(w, state), key), at=at, timeout_s=600.0))
            for _ in range(5)]
        await asyncio.sleep(0.05)
        gate.set()
        got = await lead, await asyncio.gather(*waiters)
        return got, PD._CONTEXT_CACHE.get(key)

    ((lead, waiters), cached), hashes = _gated(body)
    assert lead["outcome"] == "RAISED:RuntimeError"
    assert [r["outcome"] for r in waiters] == ["VERIFIED"] * 5
    assert cached["derek"]["void"] == {"ok": True, "attempt": 2}
    assert hashes == 2 and len(calls) == 2


def test_a_change_while_the_flight_runs_sends_late_arrivals_to_re_verify():
    """The leader read the ledger, then a label was corrected (the stamp
    moved) before its verification finished: the leader's own decision
    stands on its read; a decision that arrived after that read is not
    served it -- it re-verifies and refuses."""
    from sportsassets import bettor_funded_model as FM
    from sportsassets.agents import paper_derek as PD
    w = LB._world(LB.RECORDS)
    state = {"rows": w["rows"], "stamp": "S0"}
    key = "flight-5"
    at = time.time()

    async def body(gate):
        lead = asyncio.ensure_future(LB._decide(
            _keyed_decision(_Ledger(w, state), key), at=at, timeout_s=600.0))
        await _until(lambda: key in PD._CONTEXT_FLIGHTS)
        late = asyncio.ensure_future(LB._decide(
            _keyed_decision(_Ledger(w, state), key), at=at, timeout_s=600.0))
        await asyncio.sleep(0.05)
        state["rows"], state["stamp"] = w["changed"], "S1"
        gate.set()
        return await lead, await late

    (lead, late), hashes = _gated(body)
    assert lead["outcome"] == "VERIFIED"
    assert late["outcome"] == "REFUSED"
    assert late["refusal"] == PD.R_MODEL_UNVERIFIED
    assert late["why"] == FM.R_TRAINING_RECORDS_DO_NOT_REPRODUCE
    assert hashes == 2


def test_decisions_arriving_before_a_flights_read_need_no_stamp_proof():
    """After a change, decisions already waiting on the cache's proof join
    the re-verification that starts after they arrived: it read the ledger
    after them, so it is served without another stamp read, once."""
    from sportsassets.agents import paper_derek as PD
    w = LB._world(LB.RECORDS)
    state = {"rows": w["rows"], "stamp": "S0", "yield_on_stamp": True}
    key = "flight-6"
    at = time.time()

    async def body(gate):
        gate.set()
        warm = await LB._decide(_keyed_decision(_Ledger(w, state), key),
                                at=at, timeout_s=600.0)
        state["stamp"] = "S1"            # a change (the ledger re-verifies)
        conns = [_Ledger(w, dict(state)) for _ in range(6)]
        for c in conns:
            c.state["stamp_reads"] = 0
        got = await asyncio.gather(*(LB._decide(
            _keyed_decision(c, key), at=at + 1.0, timeout_s=600.0)
            for c in conns))
        return warm, got, [c.state["stamp_reads"] for c in conns]

    (warm, got, reads), hashes = _gated(body)
    assert warm["outcome"] == "VERIFIED"
    assert [g["outcome"] for g in got] == ["VERIFIED"] * 6
    # one re-verification for the six; the leader read the stamp twice
    # (the cache's proof, then before its own read); every other decision
    # once (the cache's proof) -- it was served the flight it preceded
    assert hashes == 2
    assert sorted(reads) == [1, 1, 1, 1, 1, 2], reads


def test_an_unreadable_stamp_never_serves_a_shared_or_cached_success():
    from sportsassets.agents import paper_derek as PD
    w = LB._world(LB.RECORDS)
    state = {"rows": w["rows"], "stamp": None}
    key = "flight-7"
    at = time.time()

    async def body(gate):
        tasks = [asyncio.ensure_future(LB._decide(
            _keyed_decision(_Ledger(w, state), key), at=at, timeout_s=600.0))
            for _ in range(8)]
        await _until(lambda: key in PD._CONTEXT_FLIGHTS)
        await asyncio.sleep(0.05)
        gate.set()
        got = await asyncio.gather(*tasks)
        later = await LB._decide(_keyed_decision(_Ledger(w, state), key),
                                 at=at + 1.0, timeout_s=600.0)
        return got, later

    (got, later), hashes = _gated(body)
    assert [g["outcome"] for g in got] == ["VERIFIED"] * 8
    assert later["outcome"] == "VERIFIED"
    # the leader's verification served only the leader (the others arrived
    # after its read and nothing proves the ledger unchanged since); ONE
    # more flight, read after they arrived, served the other seven; the
    # later decision found no provable cache entry
    assert hashes == 3


def test_an_earlier_instant_shares_the_model_and_reads_its_own_void(
        monkeypatch):
    from sportsassets.agents import derek_policy as DP
    from sportsassets.agents import paper_derek as PD
    w = LB._world(LB.RECORDS)
    state = {"rows": w["rows"], "stamp": "S0"}
    at = time.time()
    created = PD.L._epoch(w["model_row"]["created_at"])
    assert created < at - 1.0
    throughs = []

    async def void_measure(conn, *, through):
        throughs.append(float(through))
        return {"ok": True, "through": float(through)}
    monkeypatch.setattr(DP, "void_measure", void_measure)

    def run(key, earlier):
        async def body(gate):
            lead = asyncio.ensure_future(LB._decide(
                _keyed_decision(_Ledger(w, state), key), at=at,
                timeout_s=600.0))
            await _until(lambda: key in PD._CONTEXT_FLIGHTS)
            early = asyncio.ensure_future(_early(key, earlier))
            await asyncio.sleep(0.05)
            gate.set()
            return await lead, await early
        return _gated(body)

    async def _early(key, earlier):
        from sportsassets.agents import paper_derek as PD2
        d = await PD2._context(_Ledger(w, state),
                               {"now": earlier, "context_cache_key": key})
        return d

    # the model was registered before the earlier instant: shared
    (lead, early), hashes = run("flight-8", at - 1.0)
    assert lead["outcome"] == "VERIFIED"
    assert early["model"]["provenance_verified"] is True
    assert early["void"] == {"ok": True, "through": at - 1.0}
    assert hashes == 1
    assert throughs == [at, at - 1.0]
    # registered AFTER the earlier instant: not this decision's model
    throughs.clear()
    (lead, early), hashes = run("flight-9", created - 1.0)
    assert lead["outcome"] == "VERIFIED"
    assert hashes == 2
    assert throughs == [at, created - 1.0]


def test_the_flight_table_is_bounded_and_empties():
    from sportsassets.agents import paper_derek as PD
    w = LB._world(300)
    state = {"rows": w["rows"], "stamp": "S0"}
    at = time.time()
    n = PD.MAX_CONTEXT_FLIGHTS + 24
    seen = [0]

    async def body(gate):
        done = asyncio.Event()

        async def sample():
            while not done.is_set():
                seen[0] = max(seen[0], len(PD._CONTEXT_FLIGHTS))
                await asyncio.sleep(0.002)
        s = asyncio.ensure_future(sample())
        tasks = [asyncio.ensure_future(LB._decide(
            _keyed_decision(_Ledger(w, state), "flight-key-%d" % i), at=at,
            timeout_s=600.0)) for i in range(n)]
        await asyncio.sleep(0.2)
        gate.set()
        got = await asyncio.gather(*tasks)
        done.set()
        await s
        return got, dict(PD._CONTEXT_FLIGHTS)

    (got, flights), hashes = _gated(body)
    assert [g["outcome"] for g in got] == ["VERIFIED"] * n
    assert seen[0] == PD.MAX_CONTEXT_FLIGHTS
    assert flights == {}
    assert hashes == n                    # one per key: nothing to share


# ═════════════════════════════════════════════════════════════════════
# C · THE STOPPED LANE: A NAMED REFUSAL, NEVER A RAISE (fresh interpreter)
# ═════════════════════════════════════════════════════════════════════

def scenario_lane_stops() -> dict:
    from sportsassets import cpu_lane as CPU
    from sportsassets.agents import paper_derek as PD
    w = LB._world(LB.RECORDS)
    hashes = LB._Hashes()
    gate = threading.Event()
    at = time.time()

    async def go():
        holding = asyncio.ensure_future(CPU.run(gate.wait, 30))
        await asyncio.sleep(0.05)
        # 1 · the lane is shut down with the check's job queued
        stopped = asyncio.ensure_future(PD.research_model(
            LB._Conn(w["rows"], w["model_row"], w["prov"]), at=at))
        await asyncio.sleep(0.2)
        CPU.shutdown()
        try:
            got = await stopped
            out = {"returned": True, "ok": got.get("ok"),
                   "refusal": got.get("refusal"), "why": got.get("why"),
                   "verified": got.get("provenance_verified")}
        except BaseException as exc:                            # noqa: BLE001
            out = {"returned": False, "raised": type(exc).__name__}
        gate.set()
        await holding
        # 2 · the decision's own cancellation still propagates
        gate.clear()
        holding = asyncio.ensure_future(CPU.run(gate.wait, 30))
        await asyncio.sleep(0.05)
        mine = asyncio.ensure_future(PD.research_model(
            LB._Conn(w["rows"], w["model_row"], w["prov"]), at=at))
        await asyncio.sleep(0.2)
        mine.cancel()
        try:
            await mine
            own = "RETURNED"
        except asyncio.CancelledError:
            own = "CANCELLED"
        gate.set()
        await holding
        return dict(out, own_cancel=own)
    try:
        got = LB._uvloop_run(go())
    finally:
        gate.set()
        hashes.undo()
    return dict(got, hashed=len(hashes.spans))


def _fresh(name: str) -> dict:
    code = ("import json, sys; sys.path[:0] = [%r, %r]; "
            "import test_rc6_provenance_cache_and_flight as T; "
            "print('RESULT ' + json.dumps(T.%s()))"
            % (BACKEND, os.path.join(BACKEND, "tests"), name))
    proc = subprocess.run([sys.executable, "-c", code], cwd=BACKEND,
                          capture_output=True, text=True, timeout=600,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    got = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert got, proc.stderr[-3000:]
    return json.loads(got[-1][len("RESULT "):])


def test_research_model_refuses_by_name_when_the_lane_stops_under_it():
    from sportsassets.agents import paper_derek as PD
    got = _fresh("scenario_lane_stops")
    assert got["returned"] is True, got
    assert got["ok"] is False and got["verified"] is False, got
    assert got["refusal"] == PD.R_MODEL_UNVERIFIED, got
    assert got["why"] == PD.R_PROVENANCE_CHECK_NOT_RUN, got
    assert got["hashed"] == 0, got
    assert got["own_cancel"] == "CANCELLED", got
