"""RC6 PROVENANCE -- THE THREE DEFECTS, CLOSED: no verified success survives
a change to a vector under its stored identity (D1) or a change to the
training set behind a session's cached context (D2), and one session's
concurrent decisions verify a cold context once (D3).

The owner's directive (2026-10-09): "never cache a successful verification
across changed training records or settlements; ... changed feature vectors
still refuse; ... verify CPU offloading and bounded concurrency under
parallel PAPER decisions". test_rc6_provenance_refuses_after_change proves
the check itself refuses every change it covers, on the same model object,
recomputed each call; these three pin what it did not cover. D1 and D2 were
not introduced by the offload: both failed on production's RC6 source
(dfb474de), on the uploaded offload patch applied to it, and on the
successor (72714028) alike. D3 was introduced by moving the check off the
loop: it passed on dfb474de and failed on the patch and on 72714028. All
three failed on ec7b715f (RC6.1 + these tests) and pass on the fix.
test_rc6_provenance_cache_and_flight pins the fixes' contracts beyond these
three (every kind of change through the cached context, every bypass of the
change counter, the flight's failure, deadline, bound and instant rules,
the stopped lane).

D1 · A FEATURE VECTOR REWRITTEN UNDER A STALE IDENTITY REFUSES.
    A training record carries the decision's `feature_sha` AS STORED and the
    vector's KEY schema -- never the vector's values -- so a vector whose
    values changed while its stored identity did not (the research table is
    append-only by trigger, so: a corruption, a restore, an operator's
    repair with the trigger off) re-hashes to the same digest. THE CONTRACT:
    `bettor_funded_model._reproduce` (the lane) checks every vector against
    its own stored identity BEFORE the digest and refuses
    R_TRAINING_RECORDS_DO_NOT_REPRODUCE naming the decisions
    (`changed_records`), so the check a PAPER decision runs
    (`verify_provenance`, no refit) refuses, the refit (register / promote)
    refuses, and Derek's model read refuses RESEARCH_MODEL_PROVENANCE_NOT_
    VERIFIED. No digest and no stored model changes. Before deploying it, a
    production read-only count of vectors whose stored identity does not
    recompute (none expected: every writer stores bettor_funded_model.
    feature_sha of the vector it stores).

D2 · THE PAPER DECISION'S CACHED CONTEXT DOES NOT OUTLIVE A CORRECTION.
    paper_derek._context keeps a session's context (CONTEXT_TTL_S = 300 s,
    unchanged), research model and its `provenance_verified` included. THE
    CONTRACT: the training set's change stamp (migration 365: a counter
    moved in the writer's own transaction by every UPDATE / DELETE /
    TRUNCATE that can alter a verified training record, a label, a
    settlement or the stored digest, plus the catalog identity of the
    triggers that move it and of the tables they watch) is read BEFORE the
    verification and stored with the context; a later decision is served
    the cached verification only when the stamp, read after it arrived, is
    the same. A label corrected one second after a verified context makes
    the session's next decision recompute -- and refuse, as the ledger does.

D3 · ONE SESSION'S CONCURRENT DECISIONS VERIFY A COLD CONTEXT ONCE.
    Described at the test (no database: the stand-in ledger of
    test_rc6_provenance_lane_bounds).

ALL DATA SYNTHETIC; D1 and D2 run in one rolled-back transaction each (the
ledger and helpers are test_rc6_provenance_refuses_after_change's).
"""
from __future__ import annotations

import asyncio
import json
import time

from tests import test_rc6_provenance_refuses_after_change as L


def _run(monkeypatch, body):
    import asyncpg

    from sportsassets.agents import paper_derek as PD
    PD._CONTEXT_CACHE.clear()

    async def go():
        conn = await asyncpg.connect(L._dsn())
        tr = conn.transaction()
        await tr.start()
        try:
            await L._isolate(conn)
            made = await L._ledger(conn)
            await L._registered(conn, model_id=L.SYN + "model")
            return await body(conn, made)
        finally:
            await tr.rollback()
            await conn.close()
    try:
        return asyncio.run(go())
    finally:
        PD._CONTEXT_CACHE.clear()


# ═════════════════════════════════════════════════════════════════════
# D1 · A VECTOR REWRITTEN UNDER ITS OLD IDENTITY
# ═════════════════════════════════════════════════════════════════════

def test_d1_a_vector_rewritten_under_its_old_identity_refuses(monkeypatch):
    from sportsassets import bettor_funded_model as FM
    from sportsassets.agents import paper_derek as PD

    async def body(conn, made):
        first = await PD.research_model(conn, at=time.time() + 1.0)
        assert first["ok"] is True and first["provenance_verified"] is True
        model = first["model"]
        vid, oid, _won = made[17]
        feats = json.loads(await conn.fetchval(
            "SELECT features::text FROM derek_research_observations "
            " WHERE valuation_id = $1", vid))
        stored_sha = await conn.fetchval(
            "SELECT feature_sha FROM derek_research_observations "
            " WHERE valuation_id = $1", vid)
        assert stored_sha == FM.feature_sha(feats)
        feats["acquisition_price"] = round(
            1.0 - float(feats["acquisition_price"]), 9)
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "UPDATE derek_research_observations SET features = $2::jsonb "
            " WHERE valuation_id = $1", vid, json.dumps(feats))
        await conn.execute("SET LOCAL session_replication_role = origin")
        refit = await FM.verify_provenance(conn, model, check_params=True)
        plain = await FM.verify_provenance(conn, model)
        paper = await PD.research_model(conn, at=time.time() + 1.0)
        return oid, refit, plain, paper

    oid, refit, plain, paper = _run(monkeypatch, body)
    from sportsassets import bettor_funded_model as FM
    from sportsassets.agents import paper_derek as PD
    # the refit (register / promote) already refuses it (today: the refit
    # differs; with the fix: the vector's identity, checked first)
    assert refit["ok"] is False
    assert refit["refusal"] in (FM.R_PARAMS_NOT_FROM_THE_RECORDS,
                                FM.R_TRAINING_RECORDS_DO_NOT_REPRODUCE)
    # the check a PAPER decision runs must refuse it too
    assert plain["ok"] is False, (
        "D1: decision %s's vector changed under its stored identity and the "
        "provenance check still verified the model" % oid)
    assert plain["refusal"] == FM.R_TRAINING_RECORDS_DO_NOT_REPRODUCE
    assert oid in (plain.get("changed_records") or [])
    assert paper["ok"] is False and paper["refusal"] == PD.R_MODEL_UNVERIFIED


# ═════════════════════════════════════════════════════════════════════
# D2 · THE CONTEXT CACHE
# ═════════════════════════════════════════════════════════════════════

def test_d2_a_paper_decisions_cached_context_does_not_outlive_a_correction(
        monkeypatch):
    from sportsassets.agents import paper_derek as PD

    async def body(conn, made):
        t0 = time.time() + 1.0
        key = L.SYN + "session"
        first = await PD._context(conn, {"now": t0, "context_cache_key": key})
        assert first["model"]["ok"] is True
        assert first["model"]["provenance_verified"] is True
        vid, oid, _won = made[5]
        await conn.execute("UPDATE external_valuations SET outcome = "
                           "1 - outcome WHERE id = $1", vid)
        # the next valuation of the same session, one second later
        again = await PD._context(conn, {"now": t0 + 1.0,
                                         "context_cache_key": key})
        # a fresh context (no cache key) for comparison: the ledger's answer
        fresh = await PD._context(conn, {"now": t0 + 1.0})
        return oid, again, fresh

    oid, again, fresh = _run(monkeypatch, body)
    assert fresh["model"]["ok"] is False
    assert fresh["model"]["refusal"] == PD.R_MODEL_UNVERIFIED
    assert again["model"]["ok"] is False, (
        "D2: decision %s's label was corrected, the ledger refuses the model "
        "(%s), and the session's cached context still served it as verified"
        % (oid, fresh["model"]["refusal"]))
    assert again["model"]["refusal"] == PD.R_MODEL_UNVERIFIED



# ═════════════════════════════════════════════════════════════════════
# D3 · ONE SESSION'S CONCURRENT DECISIONS EACH RE-VERIFY A COLD CONTEXT
# ═════════════════════════════════════════════════════════════════════
#
# INTRODUCED BY THE OFFLOAD (passed on production's RC6 source dfb474de,
# failed on 72714028, on the uploaded patch and on ec7b715f). On the loop,
# the first decision's verification held the loop until its context was
# cached, so the decisions arriving meanwhile found it cached: the blocking
# loop was an accidental single flight. Off the loop, every decision of the
# session that arrived while the first verification ran missed the cache
# too and queued ITS OWN full verification on the one lane. LOCAL bench
# (tests/_provenance_offload_bench.py arrivals, 28,303 records, decisions
# every 0.5 s from a cold context): 2 verifications per expiry on
# dfb474de, 5-6 on 72714028; decision p95 2.1-2.4 s -> 4.9-8.1 s, and 2 of
# 28 decisions past the 8 s decision deadline (cut: no decision).
# THE CONTRACT: one flight per context key (paper_derek._lead). Concurrent
# misses await the computation in flight, shielded: a waiter's own deadline
# cuts only that waiter; a flight that fails or is cut resolves to nothing
# -- nothing cached -- and its waiters try again (one leads a new flight);
# a waiter is served the flight's context only when the flight read the
# ledger after the waiter arrived or the change stamp (D2) is unchanged
# since the flight's read; at most MAX_CONTEXT_FLIGHTS keys hold a flight.

def test_d3_one_sessions_concurrent_decisions_verify_a_cold_context_once():
    from sportsassets.agents import paper_derek as PD
    from tests import test_rc6_provenance_lane_bounds as LB

    w = LB._world(LB.RECORDS)
    hashes = LB._Hashes()
    PD._CONTEXT_CACHE.clear()
    key = L.SYN + "session-d3"
    at = time.time()

    async def go():
        return await asyncio.gather(*(
            PD._context(LB._Conn(w["rows"], w["model_row"], w["prov"]),
                        {"now": at, "context_cache_key": key})
            for _ in range(8)))
    try:
        got = asyncio.run(go())
    finally:
        hashes.undo()
        PD._CONTEXT_CACHE.clear()
    assert all(d["model"]["ok"] and d["model"]["provenance_verified"]
               for d in got)
    assert len(hashes.spans) == 1, (
        "D3: 8 concurrent decisions of one session on a cold context ran "
        "%d full verifications (one is needed)" % len(hashes.spans))
