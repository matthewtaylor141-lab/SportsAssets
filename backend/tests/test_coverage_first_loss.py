"""A COVERAGE INCIDENT NAMES ITS FIRST LOSS AT A MEASURED STAGE (c28).

Production, 2026-10-04 (research-sql c28_coverage_receipt run 37198700093):
NCAAF on the America/New_York day read provider 39, normalized 5,
venue_discovered 5, mapped 5, settlement_supported NULL (the collection
ledger records no row past stage 4, so the stage is UNMEASURED), evaluated 0.
The incident was raised and reached Audrey -- but its alert row
(covalert:41b73a6967ed9bc42f739952) said

    stage_from = settlement_supported, previous_stage_count = null

i.e. it named an UNMEASURED stage as the place the events were last seen and
carried no count for it. The events were last counted at `mapped` (5); the
FIRST LOSS is mapped (5) -> evaluated (0), with settlement_supported skipped
as unmeasured. NULL IS NOT A COUNT: it can never be the stage a loss is
measured from.

The alert identity (tz, day, league, kind, stage_to), the statuses, the
thresholds and the severities are unchanged; only stage_from /
previous_stage_count and the new detail.first_loss are corrected.

ALL DATA IS SYNTHETIC TEST DATA, inside a transaction that is rolled back.
"""
from __future__ import annotations

import datetime as _dt
import json

import asyncpg
import pytest

from sportsassets.agents import coverage_integrity as C
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
UTC = _dt.timezone.utc
NOW = _dt.datetime(2026, 9, 20, 18, 0, tzinfo=UTC).timestamp()
NCAAF = "americanfootball_ncaaf"


def _row(**kw):
    r = {C.COLUMN[s]: 0 for s in C.STAGES}
    r.update({c: 0 for c in C.EXTRA_COLUMNS})
    r.update(league=NCAAF, unavailable={})
    r.update(kw)
    r["ratios"] = C.ratios(r)
    return r


#: the production shape, 2026-10-04 ET
PROD = dict(provider_events=39, normalized_events=5, venue_discovered=5,
            mapped_events=5, settlement_supported=None, evaluated_events=0,
            decided_events=0, entered_events=0, venue_catalogue_events=1)


def test_first_loss_skips_an_unmeasured_stage():
    fl = C.first_loss(_row(**PROD), "evaluated")
    assert fl == {"stage": "evaluated", "stage_count": 0,
                  "after_stage": "mapped", "after_count": 5, "lost": 5,
                  "skipped_unmeasured": ["settlement_supported"]}
    # a measured previous stage is used as-is (the pre-c28 behaviour)
    fl = C.first_loss(_row(**dict(PROD, settlement_supported=4)), "evaluated")
    assert (fl["after_stage"], fl["after_count"], fl["lost"],
            fl["skipped_unmeasured"]) == ("settlement_supported", 4, 4, [])


def test_the_collapse_detector_names_the_measured_first_loss():
    alerts = C.detect(_row(**PROD), [])
    assert [(a["kind"], a["stage_to"]) for a in alerts] == [
        ("ABSENT_DOWNSTREAM", "evaluated")]
    a = alerts[0]
    # THE PRODUCTION DEFECT: this was settlement_supported / None
    assert a["stage_from"] == "mapped"
    assert a["detail"]["previous_stage_count"] == 5
    assert a["detail"]["first_loss"]["after_stage"] == "mapped"
    assert a["detail"]["first_loss"]["skipped_unmeasured"] == [
        "settlement_supported"]
    assert "FIRST LOSS NCAAF: mapped (5) -> evaluated (0)" in \
        a["detail"]["statement"]
    # unchanged: identity, severity and the stage the zero is at
    assert a["severity"] == "WARNING"
    assert C.alert_id_for("America/New_York", "2026-10-04", a) == \
        C.alert_id_for("America/New_York", "2026-10-04",
                       dict(a, stage_from="settlement_supported"))


def test_the_status_incident_below_the_floor_names_it_too():
    r = _row(**dict(PROD, provider_events=2, normalized_events=2,
                    venue_discovered=2, mapped_events=2))
    assert C.detect(r, []) == [], "two events: below absent_min_provider"
    a = C.incident_alert(r, [], [])
    assert (a["stage_from"], a["stage_to"]) == ("mapped", "evaluated")
    assert a["detail"]["previous_stage_count"] == 2
    assert a["detail"]["first_loss"]["lost"] == 2
    assert a["detail"]["coverage_status"] == C.S_INCIDENT
    assert "FIRST LOSS NCAAF: mapped (2) -> evaluated (0)" in \
        a["detail"]["statement"]


def test_a_first_stage_zero_still_names_the_provider():
    r = _row(provider_events=4, normalized_events=0, venue_discovered=0,
             mapped_events=0, settlement_supported=None, evaluated_events=0)
    a = C.detect(r, [])[0]
    assert (a["stage_from"], a["stage_to"]) == ("provider", "normalized")
    assert a["detail"]["first_loss"]["after_count"] == 4


async def _ledger(conn, *, at, events):
    cid = "cyc28-%d" % int(at)
    for i, ev in enumerate(events):
        await conn.execute(
            "INSERT INTO ext_candidate_outcomes (cycle_id, cycle_at, sport_key,"
            " family, queue_position, provider_event_id, us_market_slug, "
            " stage, outcome, first_refusal) VALUES ($1,to_timestamp($2),$3,"
            " 'football',$4,$5,$6,'2_FRESHNESS','REFUSED',"
            " 'QUOTE_STALE_ON_ARRIVAL')",
            cid, at, NCAAF, i, ev, "aec-cfb-%s-2026-09-20" % ev)


@pg
@pytest.mark.asyncio
async def test_the_persisted_alert_and_audrey_finding_carry_the_first_loss():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        # the production shape: mapped (a venue contract on the row), refused
        # at freshness, no valuation -> settlement unmeasured, evaluated 0
        await _ledger(conn, at=NOW - 3600, events=["a", "b", "c"])
        acct = await H.new_account(conn, "cov28", now=NOW - 86400)
        ctx = {"session_id": acct["session_id"],
               "account_id": acct["account_id"], "now": NOW}
        res = await C.run(conn, now=NOW, ctx=ctx, days=1)
        assert res["ran"] and not res["errors"], res
        mine = [a for a in res["alerts"] if a["league"] == NCAAF]
        assert [(a["kind"], a["stage_to"]) for a in mine] == [
            ("ABSENT_DOWNSTREAM", "evaluated")], res["alerts"]
        row = await conn.fetchrow(
            "SELECT * FROM coverage_collapse_alerts WHERE alert_id=$1",
            mine[0]["alert_id"])
        detail = json.loads(row["detail"])
        assert row["stage_from"] == "mapped"
        assert detail["previous_stage_count"] == 3
        assert detail["first_loss"]["skipped_unmeasured"] == [
            "settlement_supported"]
        f = await conn.fetchrow(
            "SELECT * FROM paper_audrey_findings WHERE finding_id=$1",
            row["audrey_finding_id"])
        fd = json.loads(f["detail"])
        assert f["kind"] == "COVERAGE_COLLAPSE"
        assert fd["stage_from"] == "mapped"
        assert fd["first_loss"]["after_stage"] == "mapped"
        assert fd["first_loss"]["after_count"] == 3
    finally:
        await tx.rollback()
        await conn.close()
