"""THE FIRST-LOSS CENSUS, AND THE TWO LEDGER DEFECTS IT EXPOSED (2026-10-05).

Production's Command Center: NCAAF 58 provider events -> 0 evaluated;
PINNAPI_NATIVE:BASKETBALL "normalization failure"; Brazil Serie B ratio
collapse; UEFA Nations League normalization / entry collapse.

1 · THE LEDGER STAGE. The collector's venue-native identity refusal and its
    WS-unusable refusal wrote their `ext_candidate_outcomes` row with NO
    stage; coverage_integrity ranked such a row 0, so an event that HAD a
    Pinnacle price and stopped at venue identity was counted as never
    normalized: every PinnAPI-native basketball seed
    (VENUE_NATIVE_FAMILY_NOT_SUPPORTED), every Serie B fixture the venue does
    not list (NO_VENUE_CONTRACT_FOR_EVENT, 9 of 11 on the recorded
    2026-09-29 capture). The writer now stages them, and the reader derives
    the stage for the rows already written.
2 · THE VALUATION'S LEAGUE. A venue-native valuation carries the fixture's
    sticky event key (migration 261), which need not be any ledger row's
    provider id, so it fell to UNATTRIBUTED; it is now attributed by the
    ledger rows that recorded its venue contract.
3 · THE CENSUS: per provider event, the first chain stage it was lost at,
    its code and class (SOFTWARE / ECONOMIC / EXTERNAL), per competition and
    sport; a read-only route.

ALL DATABASE DATA IS SYNTHETIC TEST DATA, inside transactions that are rolled
back, except the recorded capture (tests/fixtures/venue_native_2026-09-29.
json), which is read as it was captured.
"""
from __future__ import annotations

import ast
import json
import pathlib
import time

import asyncpg
import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_venue_mapping as vmap
from sportsassets.bettor_venue_native_identity import FAMILY_WINNER_TYPES
from sportsassets import coverage_first_loss as FL
from sportsassets import refusal_taxonomy as RT
from sportsassets.agents import coverage_integrity as C
from sportsassets.api import command_coverage_first_loss as API

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
FIX = pathlib.Path(__file__).parent / "fixtures"
PKG = pathlib.Path(FL.__file__).parent


def _captured_events():
    """The 2026-09-29 production capture's provider events, as the ledger
    held them: a no-Pinnacle row staged 1_PROBABILITY (no_pinnacle_stage), a
    venue-book refusal past identity staged 2_FRESHNESS with its contract,
    and every venue-identity refusal with NO stage (the defect)."""
    d = json.loads((FIX / "venue_native_2026-09-29.json").read_text())
    out = []
    for e in d["provider_events"]:
        fr = e["first_refusal"]
        st = ("1_PROBABILITY" if fr == "NO_PINNACLE_ON_EVENT" else
              "2_FRESHNESS" if e["us_market_slug"] else None)
        out.append(dict(e, outcome="REFUSED", stage=st, codes=[fr],
                        reach=4 if e["us_market_slug"] else 0, rows=1))
    return out


# ═════════════════════════════════════════════════════════════════════
# 1 · THE LEDGER STAGE (pure)
# ═════════════════════════════════════════════════════════════════════

def test_the_unstaged_identity_and_ws_codes_have_a_lane_stage():
    assert ext.ledger_stage_of("NO_VENUE_CONTRACT_FOR_EVENT") == "3_IDENTITY"
    assert ext.ledger_stage_of("VENUE_NATIVE_FAMILY_NOT_SUPPORTED") == \
        "3_IDENTITY"
    assert ext.ledger_stage_of("NO_VENUE_NATIVE_EVENT_FOR_FIXTURE") == \
        "3_IDENTITY"
    # the WS wrapper is staged as the reason it wraps
    assert ext.ledger_stage_of(
        "WS_REFERENCE_NOT_USABLE:FEED_QUOTE_OLDER_THAN_LIMIT") == "2_FRESHNESS"
    assert ext.ledger_stage_of(
        "WS_REFERENCE_NOT_USABLE:PINNAPI_PRIMARY_NO_EXACT_FIXTURE") == \
        "1_PROBABILITY"
    # a finding about the PROVIDER's own record stages nothing
    assert ext.ledger_stage_of(vmap.R_NO_TEAMS) is None
    assert ext.ledger_stage_of(vmap.R_COLLIDE) is None
    assert ext.ledger_stage_of(None) is None


def test_the_ledger_identity_codes_are_the_global_catalogues_own():
    assert set(ext.LEDGER_IDENTITY_CODES) == {
        vmap.R_NO_CONTRACT, vmap.R_AMBIGUOUS, vmap.R_CLOSED, vmap.R_SEGMENT,
        vmap.R_LINE, "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP"}
    from sportsassets.workers import ext_pinnacle_loop as L
    assert L.R_NO_PREMAP == "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP"
    # STAGE_OF (the census's own attribution) is unchanged by the addition
    for c in ext.LEDGER_IDENTITY_CODES:
        assert ext.LEDGER_STAGE_OF[c] == ext.STAGE_OF.get(c, "3_IDENTITY")


def test_the_cycle_stamps_both_previously_unstaged_refusals():
    """The writer: both refusal sites now stamp the row's stage (the e2e
    NCAAF test drives the identity site and asserts the stamped row)."""
    src = (PKG / "workers" / "ext_pinnacle_loop.py").read_text()
    assert '_event_fields({"stage": ext.ledger_stage_of(_ws_code)})' in src
    assert "_id_stage = (ext.ledger_stage_of(_id_codes[0])" in src


# ═════════════════════════════════════════════════════════════════════
# 2 · THE CENSUS (pure, over recorded rows)
# ═════════════════════════════════════════════════════════════════════

def _reconciles(agg):
    lost = sum(v for v in agg["first_loss"].values() if v)
    return lost + (agg["entered"] or 0) + agg["unavailable"] == \
        agg["provider_events"]


def test_the_recorded_serie_b_loss_is_external_at_mapping_not_normalization():
    got = FL.census(_captured_events(), [], [])
    sb = got["by_competition"]["soccer_brazil_serie_b"]
    assert sb["league_name"] == "BRAZIL_SERIE_B"
    assert sb["provider_events"] == 11
    assert sb["first_loss"]["MAPPED"] == 9
    assert sb["first_loss"]["NORMALIZED"] == 2
    assert sb["reached"]["MAPPED"] == 9 and sb["reached"]["SETTLEMENT"] == 0
    assert sb["by_class"] == {"SOFTWARE": 0, "ECONOMIC": 0, "EXTERNAL": 11,
                              "UNCLASSIFIED": 0}
    assert sb["earliest_loss"]["stage"] == "NORMALIZED"
    assert sb["largest_loss"]["stage"] == "MAPPED"
    assert sb["largest_loss"]["top_code"]["code"] == \
        "NO_VENUE_CONTRACT_FOR_EVENT"
    (m,) = [r for r in sb["by_code"] if r["stage"] == "MAPPED"]
    assert (m["code"], m["class"], m["events"], m["source"]) == (
        "NO_VENUE_CONTRACT_FOR_EVENT", "EXTERNAL", 9,
        "ext_candidate_outcomes")
    assert "EXTERNAL_DEPENDENCY" in m["evidence"]
    unl = got["by_competition"]["soccer_uefa_nations_league"]
    assert unl["first_loss"]["NORMALIZED"] == 19
    assert {r["code"] for r in unl["by_code"] if r["stage"] == "NORMALIZED"} \
        == {"NO_PINNACLE_ON_EVENT"}
    for agg in list(got["by_competition"].values()) + [got["totals"]]:
        assert _reconciles(agg), agg
    assert got["totals"]["provider_events"] == 49
    assert got["by_sport"]["soccer"]["provider_events"] == 45
    # every count names its source
    assert got["totals"]["count_sources"]["provider_events"] == \
        "ext_candidate_outcomes"
    assert set(got["stage_sources"]) == set(FL.CHAIN)


def test_a_family_with_no_venue_winner_type_is_lost_at_mapping():
    """A native seed of a family the venue-native resolver reads no winner
    type for (tennis today) still stops at MAPPED as a capability. PIN
    MOVED (P0 coverage, 2026-10-06): this was the PinnAPI-native basketball
    seed, which no longer stops here -- see the next test."""
    ev = {"sport_key": "pinnapi_tennis", "family": "tennis",
          "provider_event_id": "pinnapi:77", "outcome": "REFUSED",
          "stage": None, "reach": 3,
          "first_refusal": "VENUE_NATIVE_FAMILY_NOT_SUPPORTED",
          "codes": ["VENUE_NATIVE_FAMILY_NOT_SUPPORTED"]}
    got = FL.census([ev], [], [])
    b = got["by_competition"]["pinnapi_tennis"]
    assert b["first_loss"]["MAPPED"] == 1 and b["first_loss"]["NORMALIZED"] == 0
    assert b["provider_event_sources"] == {"metered": 0, "pinnapi_native": 1}
    (r,) = b["by_code"]
    assert (r["class"], r["family"]) == ("SOFTWARE", "CAPABILITY")


@pytest.mark.parametrize("family,slug", [
    ("basketball", "aec-nba-gs-lac-2026-10-04"),
    ("hockey", "aec-nhl-uta-nyr-2026-10-04")])
def test_a_native_nba_or_nhl_seed_now_maps_and_is_lost_at_settlement(
        family, slug):
    """P0 COVERAGE (2026-10-06). The NBA / NHL full-game winners are venue
    family winner types, so a PinnAPI-native seed is no longer refused
    VENUE_NATIVE_FAMILY_NOT_SUPPORTED at MAPPED: it resolves its contract and
    is VALUED. The strict policy then refuses it at SETTLEMENT -- the venue
    pays the LAST FAIR MARKET PRICE on a postponed game the book VOIDS
    (VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE on the valuation;
    SETTLEMENT_NOT_SUPPORTED on the decision), the shape every NFL / NCAAF
    strict decision has. The completed-game policy's own codes ride beside
    it; the census files the EARLIEST stage."""
    assert family in FAMILY_WINNER_TYPES
    key = "pinnapi_%s" % family
    ev = {"sport_key": key, "family": family,
          "provider_event_id": "pinnapi:88", "outcome": "ADMITTED",
          "stage": None, "reach": 6, "first_refusal": None, "codes": [],
          "us_market_slug": slug, "slugs": [slug]}
    vals = [{"id": 1, "event_key": "pinnapi:88", "us_market_slug": slug,
             "sport_family": family, "record_purpose": "CALIBRATION_ONLY",
             "refusals": ["VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE"]}]
    decs = [{"valuation_id": 1, "verdict": "REFUSE", "strategy": "S1",
             "refusal": "SETTLEMENT_NOT_SUPPORTED",
             "refusals": ["SETTLEMENT_NOT_SUPPORTED"]},
            {"valuation_id": 1, "verdict": "REFUSE", "strategy": "S2",
             "refusal": "NO_ACTION_HAS_POSITIVE_NET_EDGE",
             "refusals": ["NO_ACTION_HAS_POSITIVE_NET_EDGE"]}]
    got = FL.census([ev], vals, decs)
    b = got["by_competition"][key]
    assert b["league_name"] == "PINNAPI_NATIVE:%s" % family.upper()
    assert b["first_loss"]["MAPPED"] == 0
    assert b["first_loss"]["SETTLEMENT"] == 1
    assert b["reached"]["SETTLEMENT"] == 1
    (r,) = b["by_code"]
    assert (r["stage"], r["code"], r["class"], r["family"]) == (
        "SETTLEMENT", "SETTLEMENT_NOT_SUPPORTED", "SOFTWARE", "SETTLEMENT")
    # the valuation's own code is the same stage and class
    k = FL.classify("VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE")
    assert (k["class"], k["family"]) == ("SOFTWARE", "SETTLEMENT")
    assert FL.chain_stage("VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE",
                          mapped=True) == "SETTLEMENT"
    assert _reconciles(b)


def test_a_ws_refusal_is_read_through_its_wrapper():
    ev = {"sport_key": "pinnapi_basketball", "family": "basketball",
          "provider_event_id": "pinnapi:78", "outcome": "REFUSED",
          "stage": None, "reach": 0,
          "first_refusal": "WS_REFERENCE_NOT_USABLE",
          "codes": ["WS_REFERENCE_NOT_USABLE",
                    "WS_REFERENCE_NOT_USABLE:FEED_QUOTE_OLDER_THAN_LIMIT",
                    "FEED_QUOTE_OLDER_THAN_LIMIT"]}
    fl = FL.first_loss_of_event(ev, [], [], valuations_read=True,
                                decisions_read=True)
    assert fl["stage"] == "NORMALIZED"
    assert fl["code"] == "FEED_QUOTE_OLDER_THAN_LIMIT"
    assert fl["class"] == "SOFTWARE"


def _val(i, *, key, slug, refusals=(), purpose="CALIBRATION_ONLY"):
    return {"id": i, "event_key": key, "us_market_slug": slug,
            "sport_family": "football", "record_purpose": purpose,
            "refusals": list(refusals)}


def _ledger(eid, slug, *, key="americanfootball_ncaaf", reach=4,
            first="QUOTE_STALE_ON_ARRIVAL", stage="2_FRESHNESS"):
    return {"sport_key": key, "family": "football", "provider_event_id": eid,
            "outcome": "REFUSED", "stage": stage, "reach": reach,
            "first_refusal": first, "codes": [first],
            "us_market_slug": slug, "slugs": [slug] if slug else []}


def test_decisions_valuations_and_the_ledger_in_that_order():
    evs = [_ledger("a", "aec-cfb-a"), _ledger("b", "aec-cfb-b"),
           _ledger("c", "aec-cfb-c"), _ledger("d", "aec-cfb-d"),
           _ledger("e", "aec-cfb-e")]
    vals = [
        # linked by event key
        _val(1, key="a", slug="aec-cfb-a"),
        # THE STICKY KEY: another discovery's key, linked by its contract
        _val(2, key="pinnapi:900", slug="aec-cfb-b"),
        _val(3, key="c", slug="aec-cfb-c",
             refusals=["VENUE_BOOK_CURRENCY_NOT_ESTABLISHED",
                       "SETTLEMENT_NOT_SUPPORTED"]),
        _val(4, key="d", slug="aec-cfb-d")]
    decs = [
        {"valuation_id": 1, "verdict": "ENTER", "strategy": "S1"},
        {"valuation_id": 1, "verdict": "REFUSE", "strategy": "S2",
         "refusal": "NO_ACTION_HAS_POSITIVE_NET_EDGE"},
        {"valuation_id": 2, "verdict": "REFUSE", "strategy": "S1",
         "refusal": "SETTLEMENT_NOT_SUPPORTED",
         "refusals": ["SETTLEMENT_NOT_SUPPORTED"]},
        {"valuation_id": 4, "verdict": "REFUSE", "strategy": "S1",
         "refusal": "NO_ACTION_HAS_POSITIVE_NET_EDGE",
         "refusals": ["NO_ACTION_HAS_POSITIVE_NET_EDGE"]}]
    got = FL.census(evs, vals, decs)
    n = got["by_competition"]["americanfootball_ncaaf"]
    assert n["league_name"] == "NCAAF"
    assert n["entered"] == 1
    # b (by its contract) and c: valued, no decision, its EARLIEST-stage
    # code -- settlement before the book -- from the valuation
    assert n["first_loss"]["SETTLEMENT"] == 2
    assert n["first_loss"]["BOOK"] == 0
    assert n["first_loss"]["EV"] == 1                  # d
    # e: the ledger's own stale-on-arrival refusal past the contract
    assert n["first_loss"]["FAIR_VALUE"] == 1
    by = {(r["stage"], r["code"]): r for r in n["by_code"]}
    assert by[("EV", "NO_ACTION_HAS_POSITIVE_NET_EDGE")]["class"] == \
        "ECONOMIC"
    assert by[("FAIR_VALUE", "QUOTE_STALE_ON_ARRIVAL")]["class"] == \
        "SOFTWARE"
    assert got["valuations_linked"] == {"by_event_key": 3,
                                        "by_venue_contract": 1}
    assert _reconciles(n)


def test_an_unread_downstream_source_is_unavailable_never_zero():
    evs = [_ledger("a", "aec-cfb-a"),
           _ledger("z", None, reach=1, stage="1_PROBABILITY",
                   first="NO_PINNACLE_ON_EVENT")]
    got = FL.census(evs, [], [], reads={"valuations": False,
                                         "decisions": False})
    n = got["by_competition"]["americanfootball_ncaaf"]
    assert n["unavailable"] == 1                       # past mapping: unknown
    assert n["first_loss"]["NORMALIZED"] == 1          # ledger-measured
    for s in FL.DOWNSTREAM:
        assert n["first_loss"][s] is None and n["reached"][s] is None
    assert n["entered"] is None
    assert got["unavailable"]["EV"] == "EXTERNAL_VALUATIONS_UNREAD"


def test_the_censuss_own_codes_are_classified():
    for c in (FL.R_DEFERRED, FL.R_LEDGER_UNCLASSIFIED,
              FL.R_ADMITTED_NO_VALUATION, FL.R_NO_DECISION,
              FL.R_DECISION_NAMES_NO_CODE):
        k = RT.classify(c)
        assert k["classified"] and k["class"] == RT.SOFTWARE, c
    ev = {"sport_key": "baseball_mlb", "provider_event_id": "q",
          "outcome": "DEFERRED", "reach": 0}
    fl = FL.first_loss_of_event(ev, [], [], valuations_read=True,
                                decisions_read=True)
    assert (fl["stage"], fl["code"]) == ("PROVIDER", FL.R_DEFERRED)


# ═════════════════════════════════════════════════════════════════════
# 3 · THE FUNNEL AND THE CENSUS OVER THE DATABASE (rolled back)
# ═════════════════════════════════════════════════════════════════════

async def _ledger_row(conn, *, cyc, at, key, family, pos, eid, slug, stage,
                      outcome, first):
    await conn.execute(
        "INSERT INTO ext_candidate_outcomes (cycle_id, cycle_at, sport_key, "
        " family, queue_position, provider_event_id, home, away, "
        " us_market_slug, stage, outcome, first_refusal, codes) VALUES "
        " ($1, to_timestamp($2), $3, $4, $5, $6, 'H', 'A', $7, $8, $9, $10,"
        " $11::jsonb)", cyc, at, key, family, pos, eid, slug, stage, outcome,
        first, json.dumps([first] if first else []))


async def _valuation(conn, *, family, key, slug, at):
    return await conn.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, "
        " contract_selection, sport_family, market, raw_odds, "
        " outcomes_priced, expected_outcomes, decision, admissible, "
        " record_purpose, event_key, us_market_slug, decided_at, "
        " calibration_only_evidence) "
        "VALUES ('TEST_COVERAGE','v','EXTERNAL_BOOKMAKER_VALUATION',"
        " 'test','pinnacle','power','polymarket_us','home',$1,'h2h',"
        " '{}'::jsonb,2,2,'NO_TRADE',false,'CALIBRATION_ONLY',$2,$3,"
        " to_timestamp($4), $5::jsonb) RETURNING id", family, key, slug, at,
        '{"usable_for_orders": false, "basis": "TEST_COVERAGE"}')


async def _seed_incident(conn, at):
    """Serie B as captured (9 venue-absent with NO stage, 2 no-Pinnacle),
    six native basketball seeds refused at venue identity with NO stage, and
    one NCAAF contract valued under a sticky key no ledger row carries."""
    await conn.execute("DELETE FROM ext_candidate_outcomes")
    for i in range(9):
        await _ledger_row(conn, cyc="cov-c1", at=at, key="soccer_brazil_"
                          "serie_b", family="soccer", pos=i, eid="sb%d" % i,
                          slug=None, stage=None, outcome="REFUSED",
                          first="NO_VENUE_CONTRACT_FOR_EVENT")
    for i in range(2):
        await _ledger_row(conn, cyc="cov-c1", at=at, key="soccer_brazil_"
                          "serie_b", family="soccer", pos=20 + i,
                          eid="sbp%d" % i, slug=None, stage="1_PROBABILITY",
                          outcome="REFUSED", first="NO_PINNACLE_ON_EVENT")
    for i in range(6):
        await _ledger_row(conn, cyc="cov-b%d" % i, at=at,
                          key="pinnapi_basketball", family="basketball",
                          pos=0, eid="pinnapi:%d" % (700 + i), slug=None,
                          stage=None, outcome="REFUSED",
                          first="VENUE_NATIVE_FAMILY_NOT_SUPPORTED")
    await _ledger_row(conn, cyc="cov-n1", at=at, key="americanfootball_ncaaf",
                      family="football", pos=0, eid="odds-osu",
                      slug="aec-cfb-zzosu-zziowa", stage="2_FRESHNESS",
                      outcome="REFUSED", first="QUOTE_STALE_ON_ARRIVAL")
    await _valuation(conn, family="football", key="pinnapi:424242",
                     slug="aec-cfb-zzosu-zziowa", at=at + 5)


@pg
async def test_the_funnel_reads_unstaged_rows_by_their_first_refusal():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = time.time()
        at = now - 120
        await _seed_incident(conn, at)
        day = C.local_day(at, "UTC")
        f = await C.funnel_for_day(conn, day, "UTC")
        sb = f["leagues"]["soccer_brazil_serie_b"]
        # 9 HAD a price and stopped at venue identity: normalized, not lost
        # there; the venue does not list them: not discovered (VENUE_ABSENT)
        assert (sb["provider_events"], sb["normalized_events"],
                sb["venue_discovered"], sb["mapped_events"]) == (11, 9, 0, 0)
        bb = f["leagues"]["pinnapi_basketball"]
        assert (bb["provider_events"], bb["normalized_events"],
                bb["venue_discovered"], bb["mapped_events"]) == (6, 6, 6, 0)
        # the alert is no longer a "normalization failure"
        alerts = C.detect(dict(bb, league="pinnapi_basketball"), [])
        assert [a["stage_to"] for a in alerts
                if a["kind"] == "ABSENT_DOWNSTREAM"] == ["mapped"]
        # the sticky-key valuation is NCAAF's, by its venue contract
        # (the ledger holds only this test's rows, so any other valuation in
        # a shared test database is UNATTRIBUTED, never NCAAF's)
        assert f["leagues"]["americanfootball_ncaaf"]["evaluated_events"] == 1
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_the_census_read_over_the_database():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = time.time()
        await _seed_incident(conn, now - 120)
        got = await FL.read(conn, since=now - 3600, until=now + 1)
        assert got["status"] == "OK", got
        assert got["reads"]["ext_candidate_outcomes"]["read"] is True
        assert got["reads"]["external_valuations"]["read"] is True
        assert got["reads"]["paper_decisions"]["read"] is True
        by = got["by_competition"]
        assert by["soccer_brazil_serie_b"]["first_loss"]["MAPPED"] == 9
        assert by["pinnapi_basketball"]["first_loss"]["MAPPED"] == 6
        n = by["americanfootball_ncaaf"]
        # valued (calibration only), no paper decision in this database
        assert n["first_loss"]["ENTER_PASS"] == 1
        assert n["by_code"][0]["code"] == FL.R_NO_DECISION
        assert got["valuations_linked"]["by_venue_contract"] == 1
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_an_absent_ledger_is_unavailable_not_an_empty_census():
    class _C:
        async def fetchval(self, *_a):
            return False
    got = await FL.read(_C(), since=0.0, until=1.0)
    assert got["status"] == "UNAVAILABLE"
    assert got["why"] == "SOURCE_TABLE_ABSENT:ext_candidate_outcomes"
    assert "by_competition" not in got


# ═════════════════════════════════════════════════════════════════════
# 4 · THE ROUTE: GET ONLY, COMMAND SESSION, READ ONLY, TIMEOUT
# ═════════════════════════════════════════════════════════════════════

class _Acq:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acq(self.conn)


async def _ret(v):
    return v


@pg
async def test_the_route_reads_in_a_read_only_transaction_with_a_timeout(
        monkeypatch):
    from fastapi import Response
    conn = await H.connect()
    seen = {}
    try:
        async def spy(c, **kw):
            seen["ro"] = await c.fetchval("SHOW transaction_read_only")
            seen["timeout"] = await c.fetchval("SHOW statement_timeout")
            seen.update(kw)
            return {"status": "OK", "why": None}
        monkeypatch.setattr(API, "_pool", lambda: _ret(_Pool(conn)))
        monkeypatch.setattr(API.FL, "read", spy)
        got = await API.coverage_first_loss(Response(), hours=6)
        assert got["status"] == "OK" and got["hours"] == 6
        assert got["authority"] == "READ_ONLY_NO_AUTHORITY"
        assert seen["ro"] == "on"
        assert seen["timeout"] == "%ds" % (API.STATEMENT_TIMEOUT_MS // 1000)
        assert abs((seen["until"] - seen["since"]) - 6 * 3600 - 1) < 1e-6
    finally:
        await conn.close()


def test_the_route_is_get_only_and_requires_a_command_session():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") == API.PATH:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert paths and all(m <= {"GET", "HEAD"} for m in paths.values()), paths
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(API.PATH).status_code == 401
    assert client.post(API.PATH).status_code in (401, 405)
    assert client.delete(API.PATH).status_code in (401, 405)


def test_the_census_writes_nothing_and_imports_no_trading_module():
    forbidden = ("paper", "funded", "execution", "pmus", "execmirror",
                 "live_parity", "order")
    for rel in ("coverage_first_loss.py",
                "api/command_coverage_first_loss.py"):
        tree = ast.parse((PKG / rel).read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.ImportFrom):
                mods = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for m in mods:
                assert not any(f in (m or "") for f in forbidden), (rel, m)
    sqls = [FL.ledger_events_sql(), FL.VALUATIONS_SQL, FL.DECISIONS_SQL]
    for sql in sqls:
        s = sql.strip().upper()
        assert s.startswith("SELECT") or s.startswith("WITH"), s[:40]
        for verb in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "DROP ",
                     "TRUNCATE ", "CREATE ", "GRANT "):
            assert verb not in s, verb


def test_the_hours_are_bounded():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    from sportsassets.api import agents_core as AC
    APP.app.dependency_overrides[AC.require_read] = lambda: None
    try:
        client = TestClient(APP.app, raise_server_exceptions=False)
        assert client.get(API.PATH + "?hours=0").status_code == 422
        assert client.get(API.PATH + "?hours=%d"
                          % (FL.MAX_HOURS + 1)).status_code == 422
    finally:
        APP.app.dependency_overrides.pop(AC.require_read, None)

