"""BETTOR_DECISION_PIPELINE describes NOW; its history stays in the readback.

PRODUCTION (pm-acceptance 37836393458, release 69a8a07e, 2026-10-08T20:10Z):
DEGRADED on 75,107 orphans (oldest 2026-09-19T18:23:33Z) and 169 decision
write failures (last 2026-09-19T19:33:19Z) while the last decision and the
last opportunity were the same second. An hour later (research-sql
37845454453): +217 opportunities, +217 decisions, orphans still 75,107; the
newest orphan observed 2026-10-08T05:17:18Z, 64 s after V6 froze. The state
was all-time history and could never read anything else again.

These tests rebuild that shape in a real Postgres -- an incident with its
write failures and the incident label, a long drift window of withheld
decisions, then a clean present -- through the real writers
(`record_opportunity`, `write_decision`, `annotate_orphans`), and read it
through the real readers (COMMAND `_pipeline`, the heartbeat's
`pipeline_health`). Each test runs inside a transaction that is rolled
back, so nothing is left behind and the append-only triggers are never
asked to delete anything.

  * a clean last 24 h reads LIVE, with every all-time count, the incident
    label and its window still in the same payload (fails on 1c874c1f:
    DEGRADED);
  * ONE orphan or ONE write failure inside the window is still DEGRADED --
    the zero tolerance did not move; an opportunity inside its 180 s
    allowance is still not an orphan;
  * the window edge is the stated 24 h, on both sides;
  * COMMAND and the heartbeat give the same state for the same rows, and
    the heartbeat keeps its all-time keys, labelled, and serializes
    strictly;
  * the readback writes nothing.
"""
from __future__ import annotations

import os
import pathlib
import re
from datetime import timedelta

import pytest

from sportsassets import db as DB
from sportsassets import shadow_bettor as bettor
from sportsassets import shadow_bettor_ops as ops
from sportsassets import shadow_bettor_pipeline as pipe
from sportsassets import shadow_bettor_policy as bpol
from sportsassets import shadow_store as store
from sportsassets.api import command_shadow as CS

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

SRC = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"

_TABLES = ("bettor_opportunity_annotations, bettor_decision_failures, "
           "shadow_decisions, bettor_opportunities")


async def _conn():
    import asyncpg
    c = await asyncpg.connect(DSN)
    tr = c.transaction()
    await tr.start()
    # An empty ledger for this test only. TRUNCATE fires no row trigger and
    # the transaction is rolled back, so no append-only row is ever lost.
    await c.execute("TRUNCATE %s CASCADE" % _TABLES)
    got = await store.freeze_policy(c, policy=bpol.frozen_policy())
    assert got["status"] in ("FROZEN", "ALREADY_FROZEN"), got
    return c, tr


async def _close(c, tr):
    try:
        await tr.rollback()
    finally:
        await c.close()


async def _now(c):
    return await c.fetchval("SELECT now()")


async def _opportunity(c, symbol, at, *, decide=True):
    opp = bettor.opportunity_record(
        symbol=symbol, observed_at=at, outcome_leg="yes",
        evidence_source="PMUS_BBO", cadence_s=300)
    _oid, new = await bettor.record_opportunity(opp, pool=c)
    assert new
    if decide:
        _did, decided = await bettor.write_decision(opp, None, pool=c,
                                                    decision_ts=at)
        assert decided
    return opp["bettorOpportunityId"]


async def _failure(c, opportunity_id, at, *, stage="DECISION_WRITE",
                   cls="ForeignKeyViolationError"):
    """A failure at a stated instant. record_failure stamps now(); the
    incident's failures are history, so the instant is written here."""
    await c.execute(
        "INSERT INTO bettor_decision_failures (failure_id, "
        "bettor_opportunity_id, symbol, stage, error_class, error_text, "
        "failed_at) VALUES ($1, $2, 'sym', $3, $4, $5, $6)",
        "bfail_test_%s_%s" % (opportunity_id, at.timestamp()),
        opportunity_id, stage, cls,
        "%s: insert or update on table \"shadow_decisions\" violates "
        "foreign key constraint \"shadow_decisions_policy_frozen\"" % cls,
        at)


async def _label(c, opportunity_id, observed_at, kind, incident):
    """The note annotate_orphans writes, with the label production's sweep
    gave each era. Written here rather than through the sweep because the
    sweep's era boundary is the first decision's created_at, which is
    now() for every row inside this test's one transaction."""
    await c.execute(
        "INSERT INTO bettor_opportunity_annotations (annotation_id, "
        "bettor_opportunity_id, annotation_kind, incident, detail, "
        "observed_at) VALUES ($1, $2, $3, $4, 'test', $5)",
        "bann_test_%s" % opportunity_id, opportunity_id, kind, incident,
        observed_at)


async def _production_shape(c):
    """The 2026-10-08 ledger in miniature: an incident 19 days ago (orphans,
    their write failures and the incident label), the first decision after
    it, a drift window of withheld decisions that ended more than a day
    ago, and a clean present."""
    now = await _now(c)
    incident = now - timedelta(days=19)
    for i in range(3):
        at = incident + timedelta(minutes=i)
        oid = await _opportunity(c, "inc-%d" % i, at, decide=False)
        await _failure(c, oid, at + timedelta(seconds=5))
        await _label(c, oid, at, ops.ANNOTATION_WRITER_INCIDENT,
                     ops.WRITER_INCIDENT)
    await _opportunity(c, "first-decision", incident + timedelta(hours=1))
    for h in (17 * 24, 5 * 24, 30, 25):          # withheld, never decided
        at = now - timedelta(hours=h)
        oid = await _opportunity(c, "drift-%d" % h, at, decide=False)
        await _label(c, oid, at, ops.ANNOTATION_UNEXPLAINED,
                     store.NOT_IDENTIFIED)
    for m in (600, 240, 61, 10):                 # the present: all decided
        await _opportunity(c, "now-%d" % m, now - timedelta(minutes=m))
    return now


# ── 1. the present is clean: LIVE, with the history beside it ────────


@pg
async def test_a_clean_last_day_reads_live_with_its_history_in_the_payload():
    c, tr = await _conn()
    try:
        now = await _production_shape(c)
        got = await CS._pipeline(c)

        assert got["state"] == "LIVE", got
        w = got["stateWindow"]
        assert w["basis"] == pipe.WINDOW_BASIS == "ROLLING_24H"
        assert w["seconds"] == pipe.PIPELINE_WINDOW_S == 86400
        assert w["until"] == now.isoformat()
        assert w["since"] == (now - timedelta(days=1)).isoformat()
        # the counters that set the state are the window's
        assert got["countsScope"] == "WINDOW"
        assert got["orphanOpportunities"] == 0
        assert got["decisionWriteFailures"] == 0
        assert got["opportunitiesObserved"] == 4
        assert got["opportunityToDecisionSuccessRate"] == 1.0

        # EVERY ALL-TIME COUNT, unchanged and labelled
        a = got["allTime"]
        assert a["affectsState"] is False
        assert a["orphanOpportunities"] == 7          # 3 incident + 4 drift
        assert a["decisionWriteFailures"] == 3
        assert a["opportunitiesObserved"] == 12
        assert a["decisionsRecorded"] == 5
        assert a["opportunityToDecisionSuccessRate"] == round(5 / 12, 4)
        assert a["oldestOrphanAt"] < a["newestOrphanAt"]
        assert a["newestOrphanAt"] == (now - timedelta(hours=25)).isoformat()

        # THE INCIDENT AND ITS WINDOW, by the label it was given
        labels = {l["incident"]: l for l in got["history"]["orphanLabels"]}
        inc = labels[ops.WRITER_INCIDENT]
        assert inc["annotationKind"] == ops.ANNOTATION_WRITER_INCIDENT
        assert inc["opportunities"] == 3
        assert inc["firstObservedAt"] < inc["lastObservedAt"]
        assert labels["NOT_IDENTIFIED"]["opportunities"] == 4
        fails = got["history"]["failures"]
        assert [(f["stage"], f["errorClass"], f["failures"]) for f in fails] \
            == [("DECISION_WRITE", "ForeignKeyViolationError", 3)]
        assert got["history"]["affectsState"] is False

        # the last failure keeps its own instant: history, read as history
        assert got["lastFailure"] == a["lastFailure"]
        assert got["lastSuccessfulDecision"] is not None
        # and the operator's sentence names both
        assert "STATE = the last 24h" in got["detail"]
        assert "ALL TIME" in got["detail"] and "7 orphans" in got["detail"]
    finally:
        await _close(c, tr)


# ── 2. the zero tolerance did not move ───────────────────────────────


@pg
async def test_one_orphan_inside_the_window_is_degraded():
    c, tr = await _conn()
    try:
        now = await _production_shape(c)
        await _opportunity(c, "gap-now", now - timedelta(minutes=30),
                           decide=False)
        got = await CS._pipeline(c)
        assert got["state"] == "DEGRADED"
        assert got["orphanOpportunities"] == 1
        assert got["oldestOrphanAt"] == got["newestOrphanAt"] \
            == (now - timedelta(minutes=30)).isoformat()
        assert got["opportunityToDecisionSuccessRate"] == 0.8   # 4 of 5
        assert got["allTime"]["orphanOpportunities"] == 8
    finally:
        await _close(c, tr)


@pg
async def test_production_now_is_still_degraded_until_its_last_orphan_ages_out():
    """Production's newest orphan was observed 2026-10-08T05:17:18Z. Read
    at 21:15Z that is 16 h old -- inside the window -- so the honest state
    THEN is still DEGRADED, and it names that one orphan rather than
    75,107. It turns LIVE only once a full clean day has passed."""
    c, tr = await _conn()
    try:
        now = await _production_shape(c)
        tail = now - timedelta(hours=15, minutes=58)
        oid = await _opportunity(c, "drift-tail", tail, decide=False)
        await _label(c, oid, tail, ops.ANNOTATION_UNEXPLAINED,
                     store.NOT_IDENTIFIED)
        got = await CS._pipeline(c)
        assert got["state"] == "DEGRADED"
        assert got["orphanOpportunities"] == 1
        assert got["newestOrphanAt"] == tail.isoformat()
        assert got["allTime"]["orphanOpportunities"] == 8
    finally:
        await _close(c, tr)


@pg
async def test_one_write_failure_inside_the_window_is_degraded():
    c, tr = await _conn()
    try:
        now = await _production_shape(c)
        await _failure(c, None, now - timedelta(hours=2),
                       stage="UNIVERSE_READ", cls="TimeoutError")
        got = await CS._pipeline(c)
        assert got["state"] == "DEGRADED"
        assert got["orphanOpportunities"] == 0
        assert got["decisionWriteFailures"] == 1
        assert got["allTime"]["decisionWriteFailures"] == 4
    finally:
        await _close(c, tr)


@pg
async def test_an_opportunity_inside_its_allowance_is_not_an_orphan():
    """Migration 073's 180 s allowance is the definition, unchanged: one
    still being decided is neither a success nor a miss."""
    c, tr = await _conn()
    try:
        now = await _production_shape(c)
        await _opportunity(c, "in-flight", now - timedelta(seconds=60),
                           decide=False)
        got = await CS._pipeline(c)
        assert got["state"] == "LIVE"
        assert got["orphanOpportunities"] == 0
        assert got["opportunitiesObserved"] == 5
        assert got["opportunityToDecisionSuccessRate"] == 1.0   # settled 4/4
    finally:
        await _close(c, tr)


@pg
async def test_the_window_edge_is_the_stated_24_hours():
    c, tr = await _conn()
    try:
        now = await _now(c)
        await _opportunity(c, "decided", now - timedelta(hours=1))
        await _opportunity(c, "outside", now - timedelta(hours=24, minutes=5),
                           decide=False)
        assert (await CS._pipeline(c))["state"] == "LIVE"
        await _opportunity(c, "inside", now - timedelta(hours=23, minutes=55),
                           decide=False)
        got = await CS._pipeline(c)
        assert got["state"] == "DEGRADED"
        assert got["orphanOpportunities"] == 1
        assert got["allTime"]["orphanOpportunities"] == 2
    finally:
        await _close(c, tr)


@pg
async def test_nothing_settled_in_the_window_is_listening_not_live():
    """History alone never makes the pipeline LIVE: with no decided
    opportunity in the window there is no evidence decisions are landing."""
    c, tr = await _conn()
    try:
        now = await _now(c)
        await _opportunity(c, "old", now - timedelta(days=3))
        got = await CS._pipeline(c)
        assert got["state"] == "LISTENING"
        assert got["allTime"]["decisionsRecorded"] == 1
    finally:
        await _close(c, tr)


# ── 3. two readers, one answer; the heartbeat stays strict ───────────


@pg
async def test_command_and_the_heartbeat_agree_and_the_beat_keeps_its_keys():
    c, tr = await _conn()
    try:
        now = await _production_shape(c)
        beat = await ops.pipeline_health(c)
        assert beat["state"] == (await CS._pipeline(c))["state"] == "LIVE"
        # the flat counts keep their keys and their all-time meaning
        assert beat["countsScope"] == "ALL_TIME"
        assert beat["orphans"] == 7 and beat["failures"] == 3
        assert beat["window"]["orphanOpportunities"] == 0
        assert beat["window"]["basis"] == "ROLLING_24H"
        # serializable by the strict heartbeat encoder (no default=)
        DB.heartbeat_json({"pipeline": beat})

        await _opportunity(c, "gap", now - timedelta(minutes=20),
                           decide=False)
        beat = await ops.pipeline_health(c)
        assert beat["state"] == (await CS._pipeline(c))["state"] \
            == "DEGRADED"
    finally:
        await _close(c, tr)


async def test_a_missing_store_is_still_named_store_not_ready():
    class Broken:
        async def fetchrow(self, *a):
            raise RuntimeError("relation does not exist")

        async def fetch(self, *a):
            raise RuntimeError("relation does not exist")

    got = await CS._pipeline(Broken())
    assert got["state"] == "STORE_NOT_READY"
    assert "migration 073" in got["detail"]
    assert got["allTime"] is None and got["stateWindow"] is None


def test_the_state_rule_is_the_same_in_both_readers():
    for w, want in (
            ({"orphanOpportunities": 1, "decisionWriteFailures": 0,
              "opportunitiesDecided": 9}, "DEGRADED"),
            ({"orphanOpportunities": 0, "decisionWriteFailures": 1,
              "opportunitiesDecided": 9}, "DEGRADED"),
            ({"orphanOpportunities": 0, "decisionWriteFailures": 0,
              "opportunitiesDecided": 9}, "LIVE"),
            ({"orphanOpportunities": 0, "decisionWriteFailures": 0,
              "opportunitiesDecided": 0}, "LISTENING")):
        assert pipe.state_of(w) == want


def test_the_readback_writes_nothing_and_decides_nothing():
    """Annotations are notes about gaps; reading them never makes one a
    decision, and this module issues no write of any kind."""
    src = (SRC / "shadow_bettor_pipeline.py").read_text()
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    sql = " ".join(re.findall(r'"""(.*?)"""', code, re.S)[1:])
    assert sql.strip(), "the module's SQL was not found"
    for verb in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALTER",
                 "CREATE", "DROP"):
        assert not re.search(r"\b%s\b" % verb, sql), verb
    # and the decision path still cannot reach it
    assert "shadow_bettor_pipeline" not in \
        (SRC / "shadow_bettor.py").read_text()
