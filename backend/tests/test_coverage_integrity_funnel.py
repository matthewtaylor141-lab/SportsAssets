"""COVERAGE INTEGRITY (migration 209): the provider -> fill funnel per league
per day, in UTC and America/New_York, persisted daily; a collapse writes an
Audrey finding automatically.

THE NAMED REGRESSION: NCAAF present at the provider, flowing through mapping
for days, then disappearing at mapping (the venue lists the games; the
competition is not mapped) -> an ABSENT_DOWNSTREAM alert at `mapped`,
CRITICAL, routed to Audrey as a COVERAGE_COLLAPSE finding. MLB, flowing
normally beside it, raises nothing.

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
#: 2026-09-20 18:00 UTC = 14:00 in New York: the same local date in both
NOW = _dt.datetime(2026, 9, 20, 18, 0, tzinfo=UTC).timestamp()
DAY = 86400.0
NCAAF = "americanfootball_ncaaf"
MLB = "baseball_mlb"


# ════════════════════════════════════════════════════════════════════
# PURE: ratios, baselines, detection
# ════════════════════════════════════════════════════════════════════

def _row(**kw):
    r = {C.COLUMN[s]: None for s in C.STAGES}
    r.update(league=kw.pop("league", NCAAF))
    r.update(kw)
    r["ratios"] = C.ratios(r)
    return r


def _flow(n=10, **over):
    base = dict(provider_events=n, normalized_events=n, venue_discovered=n,
                mapped_events=n, settlement_supported=n, evaluated_events=n,
                decided_events=n, entered_events=n // 2, ordered_events=n // 2,
                filled_events=n // 2)
    base.update(over)
    return base


def test_an_unmeasured_stage_is_null_never_zero():
    r = _row(**_flow(filled_events=None))
    assert r["ratios"]["ordered->filled"] == {"ratio": None,
                                              "why": C.R_NUM_NULL}
    r = _row(**_flow(ordered_events=0, filled_events=0))
    assert r["ratios"]["ordered->filled"]["why"] == C.R_DENOM_ZERO
    # null is not absence: no ABSENT_DOWNSTREAM for an unmeasured stage
    r = _row(**_flow(evaluated_events=None))
    assert C.detect(r, []) == []


def test_ncaaf_vanishing_at_mapping_is_named_at_mapping():
    hist = [_row(**_flow()) for _ in range(5)]
    today = _row(**_flow(mapped_events=0, settlement_supported=0,
                         evaluated_events=0, decided_events=0,
                         entered_events=0, ordered_events=0, filled_events=0))
    alerts = C.detect(today, hist)
    assert [(a["kind"], a["stage_to"], a["severity"]) for a in alerts] == [
        ("ABSENT_DOWNSTREAM", "mapped", "CRITICAL")]
    assert alerts[0]["stage_from"] == "venue_discovered"
    assert "NCAAF" in alerts[0]["detail"]["statement"]
    # a league that never flowed at that stage is a WARNING, not CRITICAL
    assert C.detect(today, [])[0]["severity"] == "WARNING"


def test_a_ratio_below_half_its_baseline_collapses():
    hist = [_row(**_flow(n=20)) for _ in range(5)]
    today = _row(**_flow(n=20, mapped_events=6, settlement_supported=6,
                         evaluated_events=6, decided_events=6))
    alerts = C.detect(today, hist)
    assert [(a["kind"], a["stage_to"]) for a in alerts] == [
        ("RATIO_COLLAPSE", "mapped")]
    a = alerts[0]
    assert a["baseline"] == 1.0 and a["ratio"] == 0.3
    assert a["detail"]["floor"] == 0.5
    # too few baseline days: no ratio alert (but absence still counts)
    assert C.detect(today, hist[:2]) == []
    # a small denominator is not evidence of a collapse
    small = _row(**_flow(n=4, mapped_events=1, settlement_supported=1,
                         evaluated_events=1, decided_events=1))
    assert C.detect(small, hist) == []


def test_day_windows_are_explicit_and_dst_safe():
    s, e = C.day_window(_dt.date(2026, 11, 1), "America/New_York")
    assert e - s == 25 * 3600          # the fall-back day is 25 hours
    s, e = C.day_window(_dt.date(2026, 9, 20), "UTC")
    assert (s, e - s) == (_dt.datetime(2026, 9, 20, tzinfo=UTC).timestamp(),
                          DAY)


# ════════════════════════════════════════════════════════════════════
# FROM THE PRODUCTION TABLES, PERSISTED, ROUTED TO AUDREY
# ════════════════════════════════════════════════════════════════════

async def _cycle(conn, *, at, league, family, events, outcome, stage=None,
                 refusal=None, slug=True, valued=False, tag="",
                 purpose="ENTRY_DECISION"):
    cid = "cyc-%s-%s-%d%s" % (league, outcome, int(at), tag)
    for i, ev in enumerate(events):
        await conn.execute(
            "INSERT INTO ext_candidate_outcomes (cycle_id, cycle_at, sport_key,"
            " family, queue_position, provider_event_id, us_market_slug, "
            " stage, outcome, first_refusal) VALUES ($1,to_timestamp($2),$3,"
            " $4,$5,$6,$7,$8,$9,$10)", cid, at, league, family, i, ev,
            ("us-%s" % ev) if slug else None, stage, outcome, refusal)
        if valued:
            await conn.execute(
                "INSERT INTO external_valuations (experiment_id, version, "
                " source_class, provider, book, devig_method, venue, "
                " contract_selection, sport_family, market, raw_odds, "
                " outcomes_priced, expected_outcomes, decision, admissible, "
                " record_purpose, event_key, us_market_slug, decided_at, "
                " calibration_only_evidence) "
                "VALUES ('TEST_COVERAGE','v','EXTERNAL_BOOKMAKER_VALUATION',"
                " 'test','pinnacle','power','polymarket_us','home',$1,'h2h',"
                " '{}'::jsonb,2,2,'NO_TRADE',false,$5,$2,$3,"
                " to_timestamp($4), $6::jsonb)", family, ev, "us-%s" % ev,
                at + 1, purpose,
                None if purpose == "ENTRY_DECISION" else
                '{"usable_for_orders": false, "basis": "TEST_COVERAGE"}')


async def _seed(conn):
    for back in range(4, 0, -1):
        at = NOW - back * DAY - 3600
        evs = ["ncaaf-%d-%d" % (back, i) for i in range(6)]
        await _cycle(conn, at=at, league=NCAAF, family="americanfootball",
                     events=evs, outcome="ADMITTED", valued=True)
        mlb = ["mlb-%d-%d" % (back, i) for i in range(8)]
        await _cycle(conn, at=at, league=MLB, family="baseball", events=mlb,
                     outcome="ADMITTED", valued=True)
    # TODAY: the provider still lists NCAAF, the venue lists the games, and
    # the competition is not mapped -- every event stops at identity
    evs = ["ncaaf-today-%d" % i for i in range(6)]
    await _cycle(conn, at=NOW - 3600, league=NCAAF, family="americanfootball",
                 events=evs, outcome="REFUSED", stage="3_IDENTITY",
                 refusal="VENUE_NATIVE_COMPETITION_NOT_ESTABLISHED",
                 slug=False)
    mlb = ["mlb-today-%d" % i for i in range(8)]
    await _cycle(conn, at=NOW - 3600, league=MLB, family="baseball",
                 events=mlb, outcome="ADMITTED", valued=True)


@pg
@pytest.mark.asyncio
async def test_ncaaf_disappearing_at_mapping_fires_an_audrey_alert():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await _seed(conn)
        acct = await H.new_account(conn, "cov", now=NOW - 5 * DAY)
        ctx = {"session_id": acct["session_id"],
               "account_id": acct["account_id"], "now": NOW}
        res = await C.run(conn, now=NOW, ctx=ctx, days=5)
        assert res["ran"] and not res["errors"], res
        # SNAPSHOTS IN BOTH TIMEZONES, one per league per day
        for tz in C.TIMEZONES:
            rows = await conn.fetch(
                "SELECT * FROM coverage_funnel_snapshots WHERE tz=$1 AND "
                " league = ANY($2::text[]) AND day BETWEEN '2026-09-16' AND "
                " '2026-09-20' ORDER BY day, league", tz, [NCAAF, MLB])
            assert len(rows) == 10, (tz, len(rows))
        today = await conn.fetchrow(
            "SELECT * FROM coverage_funnel_snapshots WHERE tz=$1 AND "
            " league=$2 AND day='2026-09-20'", C.ALERT_TIMEZONE, NCAAF)
        assert (today["provider_events"], today["normalized_events"],
                today["venue_discovered"], today["mapped_events"]) == \
            (6, 6, 6, 0)
        assert today["final"] is False
        past = await conn.fetchrow(
            "SELECT * FROM coverage_funnel_snapshots WHERE tz=$1 AND "
            " league=$2 AND day='2026-09-17'", C.ALERT_TIMEZONE, NCAAF)
        assert past["mapped_events"] == 6 and past["evaluated_events"] == 6
        assert past["final"] is True
        # THE ALERT: NCAAF, at mapping, CRITICAL, with its Audrey finding
        alerts = [a for a in res["alerts"] if a["ok"]]
        nc = [a for a in alerts if a["league"] == NCAAF]
        assert [(a["kind"], a["stage_to"], a["severity"]) for a in nc] == [
            ("ABSENT_DOWNSTREAM", "mapped", "CRITICAL")]
        assert not [a for a in alerts if a["league"] == MLB]
        fid = nc[0]["audrey_finding_id"]
        assert fid and nc[0]["audrey_refusal"] is None
        f = await conn.fetchrow(
            "SELECT * FROM paper_audrey_findings WHERE finding_id=$1", fid)
        assert f["kind"] == "COVERAGE_COLLAPSE" and f["subject"] == NCAAF
        assert f["severity"] == "CRITICAL"
        detail = json.loads(f["detail"])
        assert detail["stage_to"] == "mapped" and \
            detail["league_name"] == "NCAAF"
        row = await conn.fetchrow(
            "SELECT * FROM coverage_collapse_alerts WHERE alert_id=$1",
            nc[0]["alert_id"])
        assert row["audrey_finding_id"] == fid
        # IDEMPOTENT: a second pass writes no second alert or finding
        again = await C.run(conn, now=NOW + 60, ctx=ctx, days=1)
        assert again["ran"]
        assert await conn.fetchval(
            "SELECT count(*) FROM coverage_collapse_alerts WHERE league=$1 "
            " AND day='2026-09-20'", NCAAF) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_audrey_findings WHERE kind="
            " 'COVERAGE_COLLAPSE' AND subject=$1 AND session_id=$2", NCAAF,
            acct["session_id"]) == 1
        # THE READ: today's NCAAF row and the alert, by name
        body = await C.coverage_payload(conn, tz=C.ALERT_TIMEZONE, days=7,
                                        now=NOW)
        assert body["status"] == "OK" and body["unit"] == "provider events"
        d0 = body["days"][0]
        assert d0["day"] == "2026-09-20"
        ncr = next(r for r in d0["leagues"] if r["league"] == NCAAF)
        assert ncr["league_name"] == "NCAAF" and ncr["mapped_events"] == 0
        assert any(a["league"] == NCAAF and a["stage_to"] == "mapped"
                   for a in body["alerts"])
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_without_a_session_the_alert_says_why_no_finding_exists():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await _seed(conn)
        res = await C.run(conn, now=NOW, ctx=None, days=5)
        nc = [a for a in res["alerts"] if a["league"] == NCAAF]
        assert nc and nc[0]["audrey_finding_id"] is None
        assert nc[0]["audrey_refusal"] == C.R_NO_SESSION
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_unreadable_source_is_null_with_its_reason():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await _seed(conn)
        await conn.execute("ALTER TABLE paper_fills RENAME TO "
                           "paper_fills_hidden_by_the_test")
        f = await C.funnel_for_day(conn, _dt.date(2026, 9, 20), "UTC")
        r = f["leagues"][MLB]
        assert r["filled_events"] is None
        assert r["unavailable"]["filled_events"].startswith(C.R_TABLE_ABSENT)
        assert r["ratios"]["ordered->filled"]["why"] == C.R_NUM_NULL
        # the stages that WERE read are measured (zero is a measurement here)
        assert r["provider_events"] == 8 and r["ordered_events"] == 0
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_209_is_idempotent_and_its_rollback_refuses_over_alerts():
    import pathlib
    mig = pathlib.Path(__file__).resolve().parents[1] / "migrations"
    up = (mig / "209_coverage_postmortems_quality.sql").read_text()
    down = (mig / "rollback" /
            "209_coverage_postmortems_quality.down.sql").read_text()
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(up)
        await conn.execute(up)
        await _seed(conn)
        res = await C.run(conn, now=NOW, ctx=None, days=5)
        assert res["alerts"]
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(down)
        await sp.rollback()
        await conn.execute("DELETE FROM coverage_collapse_alerts")
        await conn.execute("DELETE FROM improvement_deficits")
        await conn.execute("DELETE FROM position_postmortems")
        await conn.execute(down)
        assert await conn.fetchval(
            "SELECT to_regclass('coverage_funnel_snapshots')") is None
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_refusals_only_ledger_leaves_settlement_unmeasured_and_raises_no_false_alert():
    """THE PRODUCTION SHAPE (2026-10-03/04, 718b532): the collection ledger held
    only REFUSED rows (stage NULL or 2_FRESHNESS, contract slug present), every
    valuation was sealed CALIBRATION_ONLY, and the paper strategies decided on
    them. settlement_supported read 0 and evaluated read 0 for every league,
    raising ABSENT_DOWNSTREAM WARNINGs. Settlement is not measured by such a
    ledger: it is NULL with its reason, evaluated counts the sealed valuations,
    and no alert is raised."""
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        mlb = ["mlbp-%d" % i for i in range(7)]
        await _cycle(conn, at=NOW - 3600, league=MLB, family="baseball",
                     events=mlb, outcome="REFUSED", stage=None,
                     refusal="NO_PINNACLE_ON_EVENT", slug=True, valued=True,
                     purpose="CALIBRATION_ONLY")
        nc = ["ncaafp-%d" % i for i in range(5)]
        await _cycle(conn, at=NOW - 3500, league=NCAAF,
                     family="americanfootball", events=nc, outcome="REFUSED",
                     stage="2_FRESHNESS",
                     refusal="VENUE_BOOK_CURRENCY_NOT_ESTABLISHED",
                     slug=True, valued=True, purpose="CALIBRATION_ONLY")
        snap = await C.funnel_for_day(conn, _dt.date(2026, 9, 20),
                                   C.ALERT_TIMEZONE)
        for league, n in ((MLB, 7), (NCAAF, 5)):
            r = snap["leagues"][league]
            assert r["provider_events"] == n and r["mapped_events"] == n
            assert r["settlement_supported"] is None
            assert r["unavailable"]["settlement_supported"] == \
                C.R_SETTLEMENT_UNMEASURED
            assert r["evaluated_events"] == n
            assert C.detect(r, []) == []
    finally:
        await tx.rollback()
        await conn.close()
