"""ONE STATUS PER LEAGUE, AND AN ALERT FOR EVERY COVERAGE INCIDENT (cand24).

The funnel (migration 209) counted provider events per league per day, but a
reader still had to infer from a column of zeros whether a league was healthy,
refusing by policy, out of scope, broken, or simply not listed -- and the NFL
showed how that fails: 14 venue games, zero provider events, and nothing that
said so. Each league now carries exactly one of

    HEALTHY | REFUSING_BY_POLICY | EXPLICITLY_UNSUPPORTED |
    COVERAGE_INCIDENT | UNAVAILABLE

with its reason, on GET /api/command/coverage (`league_status`), beside a
game-by-game reconciliation of today's NFL slate (`nfl_reconciliation`).

A COVERAGE_INCIDENT (provider events > 0 and an expected stage zero with
nothing after it) ALWAYS raises an alert through coverage_collapse_alerts ->
paper_audrey_findings, even below the collapse detector's three-event floor.
NULL IS NOT ABSENCE, and a zero followed by downstream flow is a ledger gap,
not an incident -- the false settlement-stage alert of 718b532 stays dead.

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
NFL = "americanfootball_nfl"
NCAAF = "americanfootball_ncaaf"
MLB = "baseball_mlb"
UNL = "soccer_uefa_nations_league"


def _row(league, **kw):
    r = {C.COLUMN[s]: 0 for s in C.STAGES}
    r.update({c: 0 for c in C.EXTRA_COLUMNS})
    r.update(league=league, unavailable={})
    r.update(kw)
    return r


def _flow(league, n=10, **over):
    base = dict(provider_events=n, normalized_events=n, venue_discovered=n,
                mapped_events=n, settlement_supported=n, evaluated_events=n,
                decided_events=n, entered_events=n // 2,
                venue_catalogue_events=n)
    base.update(over)
    return _row(league, **base)


def _st(row, **kw):
    return C.classify_status(row, scope=C.lane_scope(row["league"]), **kw)


# ═════════════════════════════════════════════════════════════════════
# PURE: exactly one status, with its reason
# ═════════════════════════════════════════════════════════════════════

def test_each_status_is_reached_by_its_own_facts():
    assert _st(_flow(MLB))["status"] == C.S_HEALTHY
    # football today: every valuation decided, every decision refused
    st = _st(_flow(NFL, n=14, entered_events=0, settlement_supported=None))
    assert st["status"] == C.S_REFUSING and "REFUSED" in st["reason"]
    st = _st(_flow(NCAAF, n=3, normalized_events=3, venue_discovered=0,
                   mapped_events=0, settlement_supported=0,
                   evaluated_events=0, decided_events=0, entered_events=0))
    assert (st["status"], st["stage"]) == (C.S_INCIDENT, "venue_discovered")
    st = _st(_row(NFL, provider_events=0, venue_catalogue_events=14),
             collector={"fresh": True, "requested": [MLB],
                        "budget_dropped": [NFL], "rejected": {}, "budget": 4})
    assert st["status"] == C.S_UNAVAILABLE
    assert "NOT_REQUESTED_METERED_BUDGET_SPENT" in st["reason"]
    assert "venue lists 14" in st["reason"]
    st = _st(_row("soccer_usa_nwsl", provider_events=0),
             collector={"fresh": True, "requested": [], "budget_dropped": [],
                        "rejected": {"soccer_usa_nwsl":
                                     "PROVIDER_DOES_NOT_LIST_THIS_COMPETITION"}})
    assert st["status"] == C.S_UNAVAILABLE
    assert "PROVIDER_DOES_NOT_LIST_THIS_COMPETITION" in st["reason"]
    # the provider lists games the venue does not: nothing is missing
    st = _st(_row(UNL, provider_events=6, normalized_events=6,
                  venue_catalogue_events=0))
    assert st["status"] == C.S_UNAVAILABLE and "VENUE_LISTS_NO" in st["reason"]
    assert set(C.LEAGUE_STATUSES) == {
        "HEALTHY", "REFUSING_BY_POLICY", "EXPLICITLY_UNSUPPORTED",
        "COVERAGE_INCIDENT", "UNAVAILABLE"}


def test_out_of_scope_leagues_are_named_with_the_declared_reason():
    for league, tok, fam, frag in (
            ("basketball_nba", "nba", "basketball", "FAMILY_NOT_IN"),
            ("basketball_wnba", "wnba", "basketball", "FAMILY_NOT_IN"),
            ("basketball_ncaab", "cbb", "basketball", "FAMILY_NOT_IN"),
            ("icehockey_nhl", "nhl", "hockey", "FAMILY_NOT_IN"),
            ("venue:atp", "atp", "tennis", "FAMILY_NOT_IN"),
            ("venue:intf", "intf", "soccer", "DELIBERATELY_EXCLUDED"),
            ("venue:engnl", "engnl", "soccer", "MAPPING_REFUTED"),
            ("venue:kbo", "kbo", "baseball", "VENUE_TOKEN_NOT_MAPPED"),
            ("venue:ncaaws", "ncaaws", "soccer", "VENUE_TOKEN_NOT_MAPPED")):
        sc = C.lane_scope(league, token=tok, family=fam)
        assert sc["in_scope"] is False and frag in sc["why"], (league, sc)
        st = C.classify_status(_flow(league), scope=sc)
        assert st["status"] == C.S_UNSUPPORTED and st["reason"] == sc["why"]
    for league in (MLB, NFL, NCAAF, UNL, "soccer_brazil_serie_b"):
        assert C.lane_scope(league)["in_scope"] is True, league


def test_null_is_not_absence_and_a_ledger_gap_is_not_an_incident():
    # settlement unmeasured (NULL): never the incident stage
    st = _st(_flow(MLB, settlement_supported=None, evaluated_events=0,
                   decided_events=0, entered_events=0))
    assert (st["status"], st["stage"]) == (C.S_INCIDENT, "evaluated")
    # mapping read 0 while valuations and decisions flowed: the events got
    # past it -- a gap in what the ledger recorded, named, never an alert
    r = _flow(MLB, mapped_events=0, settlement_supported=0, entered_events=0)
    st = _st(r)
    assert st["status"] == C.S_REFUSING
    assert st["measurement_gaps"] == ["mapped", "settlement_supported"]
    r["ratios"] = C.ratios(r)
    assert C.detect(r, []) == []
    assert C.incident_alert(r, [], []) is None


def test_decided_is_expected_only_when_the_decision_path_is_live():
    r = _flow(NFL, n=14, decided_events=0, entered_events=0)
    st = _st(r)
    assert st["status"] == C.S_UNAVAILABLE
    assert "NO_PAPER_DECISIONS_RECORDED" in st["reason"]
    st = _st(r, decisions_live=True)
    assert (st["status"], st["stage"]) == (C.S_INCIDENT, "decided")


def test_an_incident_below_the_collapse_floor_still_alerts_once():
    r = _row(NFL, provider_events=2, normalized_events=2, venue_discovered=2,
             venue_catalogue_events=14)
    r["ratios"] = C.ratios(r)
    assert C.detect(r, []) == [], "two events: below absent_min_provider"
    a = C.incident_alert(r, [], [])
    assert a["kind"] == "ABSENT_DOWNSTREAM" and a["stage_to"] == "mapped"
    assert a["detail"]["coverage_status"] == C.S_INCIDENT
    assert a["severity"] == "WARNING" and "NFL" in a["detail"]["statement"]
    # with three or more the detector raises it; the status does not repeat
    r3 = dict(r, provider_events=3, normalized_events=3, venue_discovered=3)
    r3["ratios"] = C.ratios(r3)
    found = C.detect(r3, [])
    assert [x["stage_to"] for x in found] == ["mapped"]
    assert C.incident_alert(r3, [], found) is None
    assert found[0]["detail"]["coverage_status"] == C.S_INCIDENT


# ═════════════════════════════════════════════════════════════════════
# FROM THE TABLES: the alert, the endpoint payload, the reconciliation
# ═════════════════════════════════════════════════════════════════════

async def _ledger(conn, *, at, league, events, outcome, stage=None,
                  refusal=None, slugs=None, homes=None, tag=""):
    cid = "cyc24-%s-%d%s" % (league, int(at), tag)
    for i, ev in enumerate(events):
        h, a, ct = (homes or {}).get(ev, (None, None, None))
        await conn.execute(
            "INSERT INTO ext_candidate_outcomes (cycle_id, cycle_at, sport_key,"
            " family, queue_position, provider_event_id, home, away, "
            " commence_time, us_market_slug, stage, outcome, first_refusal) "
            "VALUES ($1,to_timestamp($2),$3,'football',$4,$5,$6,$7,$8,$9,$10,"
            " $11,$12)", cid, at, league, i, ev, h, a, ct,
            (slugs or {}).get(ev), stage, outcome, refusal)


@pg
@pytest.mark.asyncio
async def test_a_coverage_incident_reaches_audrey_automatically():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await _ledger(conn, at=NOW - 3600, league=NFL,
                      events=["nfl-a", "nfl-b"], outcome="REFUSED",
                      stage="3_IDENTITY",
                      refusal="VENUE_NATIVE_COMPETITION_NOT_ESTABLISHED")
        acct = await H.new_account(conn, "cov24", now=NOW - 86400)
        ctx = {"session_id": acct["session_id"],
               "account_id": acct["account_id"], "now": NOW}
        res = await C.run(conn, now=NOW, ctx=ctx, days=1)
        assert res["ran"] and not res["errors"], res
        nfl = [a for a in res["alerts"] if a["league"] == NFL]
        assert [(a["kind"], a["stage_to"]) for a in nfl] == [
            ("ABSENT_DOWNSTREAM", "mapped")], res["alerts"]
        fid = nfl[0]["audrey_finding_id"]
        assert fid, nfl
        f = await conn.fetchrow(
            "SELECT * FROM paper_audrey_findings WHERE finding_id=$1", fid)
        assert f["kind"] == "COVERAGE_COLLAPSE" and f["subject"] == NFL
        assert json.loads(f["detail"])["coverage_status"] == C.S_INCIDENT
        # idempotent
        await C.run(conn, now=NOW + 60, ctx=ctx, days=1)
        assert await conn.fetchval(
            "SELECT count(*) FROM coverage_collapse_alerts WHERE league=$1",
            NFL) == 1
    finally:
        await tx.rollback()
        await conn.close()


async def _premap(conn, ev, title, start, sides, *, token="nfl",
                  stype="football_team_full_game_winner"):
    for team, nick, abbr, intent in sides:
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, event_title, "
            " market_slug, question, kind, line, side_norm, intent, team_abbr,"
            " team_name, team_league, game_start, sports_type, updated_at) "
            "VALUES ($1,$2,$3,$1,$3,'side','00',$4,$5,$6,$7,$8,"
            " to_timestamp($9),$10, now()) ON CONFLICT (identifier, side_norm)"
            " DO UPDATE SET game_start = EXCLUDED.game_start",
            "aec-" + ev, ev, title, nick, intent, abbr, team, token, start,
            stype)


@pg
@pytest.mark.asyncio
async def test_the_endpoint_carries_a_status_per_league_and_the_nfl_games():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           C.COLLECTOR_KEY)
        k1 = NOW + 3600
        k2 = NOW + 4 * 3600
        await _premap(conn, "nfl-lar-phi-2026-09-20", "LA Rams vs. PHI Eagles",
                      k1, [("los angeles rams", "rams", "lar",
                            "ORDER_INTENT_BUY_LONG"),
                           ("philadelphia eagles", "eagles", "phi",
                            "ORDER_INTENT_BUY_SHORT")])
        await _premap(conn, "nfl-nyj-chi-2026-09-20", "NY Jets vs. CHI Bears",
                      k2, [("new york jets", "jets", "nyj",
                            "ORDER_INTENT_BUY_LONG"),
                           ("chicago bears", "bears", "chi",
                            "ORDER_INTENT_BUY_SHORT")])
        await _premap(conn, "kbo-lg-kt-2026-09-20", "LG vs. KT", NOW + 600,
                      [("lg twins", "twins", "lg", "ORDER_INTENT_BUY_LONG"),
                       ("kt wiz", "wiz", "kt", "ORDER_INTENT_BUY_SHORT")],
                      token="kbo", stype="baseball_team_full_game_winner")
        # the Rams game: seen by the collector, mapped, valued, decided
        ct = _dt.datetime.fromtimestamp(k1, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        await _ledger(conn, at=NOW - 600, league=NFL, events=["p-lar-phi"],
                      outcome="REFUSED", stage="2_FRESHNESS",
                      refusal="VENUE_BOOK_CURRENCY_NOT_ESTABLISHED",
                      slugs={"p-lar-phi": "aec-nfl-lar-phi-2026-09-20"},
                      homes={"p-lar-phi": ("Philadelphia Eagles",
                                           "Los Angeles Rams", ct)})
        vid = await conn.fetchval(
            "INSERT INTO external_valuations (experiment_id, version, "
            " source_class, provider, book, devig_method, venue, "
            " contract_selection, sport_family, market, raw_odds, "
            " outcomes_priced, expected_outcomes, decision, admissible, "
            " record_purpose, event_key, us_market_slug, decided_at, "
            " refusals, calibration_only_evidence) VALUES ('TEST_COV24','v',"
            " 'EXTERNAL_BOOKMAKER_VALUATION','test','pinnacle','power',"
            " 'polymarket_us','home','football','h2h','{}'::jsonb,2,2,"
            " 'NO_TRADE',false,'CALIBRATION_ONLY','p-lar-phi',"
            " 'aec-nfl-lar-phi-2026-09-20',to_timestamp($1),$2::text[],"
            " '{\"usable_for_orders\": false, \"basis\": \"TEST\"}'::jsonb) "
            "RETURNING id", NOW - 590,
            ["VENUE_BOOK_CURRENCY_NOT_ESTABLISHED",
             "MARKET_NOT_IN_SUPPORTED_SET",
             "VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE",
             "DRAW_HANDLING_NOT_RECONCILED"])
        acct = await H.new_account(conn, "cov24r", now=NOW - 86400)
        await conn.execute(
            "INSERT INTO paper_decisions (decision_id, session_id, account_id,"
            " decided_at, valuation_id, us_market_slug, verdict, refusal, "
            " internal_model, pinnacle, qualification_gaps, policy_version, "
            " simulator_version, strategy) VALUES ('paper-dec-cov24',$1,$2,"
            " to_timestamp($3),$4,'aec-nfl-lar-phi-2026-09-20','REFUSE',"
            " 'SETTLEMENT_NOT_SUPPORTED','{}'::jsonb,'{}'::jsonb,'[]'::jsonb,"
            " 'v','v',$5)", acct["session_id"], acct["account_id"],
            NOW - 580, vid, C.DEREK_STRATEGY)

        body = await C.coverage_payload(conn, tz=C.ALERT_TIMEZONE, days=1,
                                        now=NOW)
        # the existing fields are untouched
        for k in ("version", "days", "alerts", "stages", "thresholds"):
            assert k in body
        ls = {s["league"]: s for s in body["league_status"]["statuses"]}
        assert ls[NFL]["status"] == C.S_REFUSING, ls[NFL]
        assert ls[NFL]["league_name"] == "NFL"
        assert ls[NFL]["counts"]["venue_catalogue_events"] == 2
        assert ls["basketball_nba"]["status"] == C.S_UNSUPPORTED
        assert ls["icehockey_nhl"]["status"] == C.S_UNSUPPORTED
        assert ls["venue:kbo"]["status"] == C.S_UNSUPPORTED
        assert "VENUE_TOKEN_NOT_MAPPED" in ls["venue:kbo"]["reason"]
        assert ls[MLB]["status"] == C.S_UNAVAILABLE
        for s in body["league_status"]["statuses"]:
            assert s["status"] in C.LEAGUE_STATUSES and s["reason"], s
        assert sum(body["league_status"]["summary"].values()) == len(ls)

        rec = body["nfl_reconciliation"]
        assert rec["status"] == "OK" and rec["provider_key"] == NFL
        assert rec["expected"] == 2
        g = {x["market_slug"]: x for x in rec["games"]}
        rams = g["aec-nfl-lar-phi-2026-09-20"]
        assert rams["stages"]["PINNAPI"] == {"provider_event_id": "p-lar-phi"}
        assert rams["stages"]["EXACT_MAP"] is True
        assert rams["stages"]["SETTLEMENT"] == [
            "VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE",
            "DRAW_HANDLING_NOT_RECONCILED"]
        assert rams["stages"]["PROBABILITY"] == ["MARKET_NOT_IN_SUPPORTED_SET"]
        assert rams["stages"]["DEREK_EVALUATED"] is True
        assert rams["stages"]["VERDICT"] == "REFUSE:SETTLEMENT_NOT_SUPPORTED"
        assert rams["stopped_at"] == "VERDICT"
        jets = g["aec-nfl-nyj-chi-2026-09-20"]
        assert jets["stopped_at"] == "PINNAPI"
        assert jets["reason"] == "COLLECTOR_HEARTBEAT_NOT_CURRENT"
        assert [m["market_slug"] for m in rec["missing"]] == [
            "aec-nfl-nyj-chi-2026-09-20"]
        assert rec["reached"]["EXPECTED"] == 2 and \
            rec["reached"]["VERDICT"] == 1
    finally:
        await tx.rollback()
        await conn.close()
