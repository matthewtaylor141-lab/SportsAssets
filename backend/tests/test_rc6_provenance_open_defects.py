"""RC6 PROVENANCE -- OPEN DEFECTS, kept failing on purpose until fixed
(nothing is skipped or xfailed): two ways a verified success survives a
change today, and one way the offload multiplies the verification.

The owner's directive (2026-10-09): "never cache a successful verification
across changed training records or settlements; ... changed feature vectors
still refuse; ... verify CPU offloading and bounded concurrency under
parallel PAPER decisions". test_rc6_provenance_refuses_after_change proves
the check itself refuses every change it covers, on the same model object,
recomputed each call. D1 and D2 name what it does not cover; neither was
introduced by the offload: both FAIL on production's RC6 source (dfb474de),
on the uploaded offload patch applied to it, and on the successor
(72714028) alike. D3 WAS introduced by moving the check off the loop: it
passes on dfb474de and fails on the patch and on 72714028. The fixes belong
to bettor_funded_model.py and agents/paper_derek.py (the api-responsive
lane's files); each test states the fix it expects.

D1 · A FEATURE VECTOR REWRITTEN UNDER A STALE IDENTITY STILL VERIFIES.
    A training record carries the decision's `feature_sha` AS STORED and the
    vector's KEY schema -- never the vector's values. A vector whose values
    change while its stored identity does not (the research table is
    append-only by trigger, so: a corruption, a restore, an operator's
    repair with the trigger off) re-hashes to the same digest, and the check
    the PAPER decision runs (`verify_provenance`, no refit) PASSES. The refit
    (`check_params=True`, used by register and promote) does catch it -- this
    test shows both. EXPECTED FIX: in `_reproduce` (the lane), refuse
    R_TRAINING_RECORDS_DO_NOT_REPRODUCE when `feature_sha(rows[i])` differs
    from `feature_shas[i]` for any record, naming the decisions. That compares
    the vector with its own recorded identity, changes no digest and no
    stored model; deploy it only after a production read-only count of
    vectors whose stored identity does not recompute (none expected: every
    writer uses bettor_funded_model.feature_sha).

D2 · THE PAPER DECISION'S CONTEXT SERVES A CACHED SUCCESS FOR UP TO 300 s.
    paper_derek._context keeps a session's context (CONTEXT_TTL_S = 300 s),
    research model and its `provenance_verified` included. A label corrected
    one second after a verified context is not seen by any PAPER decision of
    that session until the TTL lapses: each decision uses a model whose
    training set no longer reproduces. EXPECTED FIX (no TTL or bound
    changed): a cache hit is served only while a cheap server-side
    fingerprint of the model's training rows is unchanged -- read before the
    verification and stored with it, e.g. md5 over (valuation id, outcome,
    outcome_basis, outcome_at, outcome_known, record_purpose, features,
    feature_sha) of the observations the model names, joined in SQL from
    the registry's own decision ids (no id list crosses the loop) -- and a
    changed fingerprint recomputes the context, re-verifying.

D3 · ONE SESSION'S CONCURRENT DECISIONS EACH RE-VERIFY A COLD CONTEXT.
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
# INTRODUCED BY THE OFFLOAD (passes on production's RC6 source dfb474de,
# fails on 72714028 and on the uploaded patch). On the loop, the first
# decision's verification held the loop until its context was cached, so
# the decisions arriving meanwhile found it cached: the blocking loop was an
# accidental single flight. Off the loop, every decision of the session
# that arrives while the first verification runs misses the cache too and
# queues ITS OWN full verification on the one lane. LOCAL bench
# (tests/_provenance_offload_bench.py arrivals, 28,303 records, decisions
# every 0.5 s from a cold context): 2 verifications per expiry on
# dfb474de, 5-6 on 72714028; decision p95 2.1-2.4 s -> 4.9-8.1 s, and 2 of
# 28 decisions past the 8 s decision deadline (cut: no decision). Production
# verifies in 2.3-4.2 s (the RC6 stall ring), so each 300 s expiry would
# cut several. EXPECTED FIX: one flight per context key -- concurrent
# misses await the computation in flight (a waiter whose flight is
# cancelled or fails computes its own); nothing is served after the flight
# completes except through the cache (with D2's fingerprint).

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
