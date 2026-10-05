"""A PINNAPI-NATIVE FIXTURE IS REPORTED UNDER ITS OWN LEAGUE (incident release,
verifier finding 1).

THE DEFECT (verified on the integrated candidate 9387b69, scratchpad
iv_native_cov.py). PinnAPI-native discovery keyed every seed `pinnapi_<family>`
(`pinnapi_discovery.sport_key_for`) and the reactive cycle files its ledger
rows (`ext_candidate_outcomes.sport_key`) under the seed's key. So twelve
admitted college games served natively read, in the coverage report:

    pinnapi_football        EXPLICITLY_UNSUPPORTED  VENUE_TOKEN_NOT_MAPPED  12 12
    americanfootball_ncaaf  UNAVAILABLE             NO_PROVIDER_EVENTS ...

-- the league PinnAPI covered was reported as having no provider events (and,
with a fresh heartbeat and deferral receipts, "NOT_REQUESTED_METERED_BUDGET_
SPENT"), the served rows as an unmapped venue token, `reconcile_league("nfl")`
read only the metered key, and FAMILY_SCOPE_NOTE still said basketball /
hockey / tennis are outside the de-vig set although inc-pinnapi subscribes all
six sports and prices basketball and hockey lines. Worse, which seeder ran
last decided the key: the collector's unmetered `/events` refresh registers
book-less seeds as metered (native=False), and those displaced native seeds on
the premise that a metered seed carries corroborating books.

THE REPAIR, proved here:
  1. a native seed is keyed by THE LANE'S OWN LEAGUE IDENTITY
     (`ext_pinnacle_loop.provider_key_for_venue_token` of the venue league
     token discovery confirmed) -- the key a metered seed of the same fixture
     carries -- and `pinnapi_<family>` only when the lane maps no single key;
  2. the metered-beats-native rule holds only for a metered seed that
     carries books: a book-less one neither displaces a live native seed nor
     survives one, whichever registers first;
  3. the report: a `pinnapi_<family>` row is in scope (native discovery), a
     league of a natively seeded family the lane maps no key for is
     UNAVAILABLE "MEASURED_UNDER_THE_PINNAPI_NATIVE_ROW" naming that row (not
     the stale scope note), tennis says why it is not seeded, and
     `reconcile_league` reads the native key beside the metered one.
Nothing here admits, prices or refuses a candidate differently: the seed's
key is the one the metered path already uses for the same league.
"""
from __future__ import annotations

import asyncio
import time
import types as _t

import pytest

from sportsassets import pinnapi_discovery as PD
from sportsassets import pinnapi_reactive as RX
from sportsassets.agents import coverage_integrity as CI
from tests import paper_harness as H
from tests import test_r30a_pinnapi_native_discovery as N

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


# ═════════════════════════════════════════════════════════════════════
# 1 · THE SEED'S KEY IS THE LANE'S LEAGUE IDENTITY
# ═════════════════════════════════════════════════════════════════════

def test_a_native_seed_is_keyed_by_the_lanes_own_league_identity():
    assert PD.sport_key_for("football", ["cfb"]) == "americanfootball_ncaaf"
    assert PD.sport_key_for("football", ["nfl"]) == "americanfootball_nfl"
    assert PD.sport_key_for("baseball", ["mlb"]) == "baseball_mlb"
    assert PD.sport_key_for("soccer", ["mls"]) == "soccer_usa_mls"
    # no lane key for the token: the native key, by family
    assert PD.sport_key_for("basketball", ["nba"]) == "pinnapi_basketball"
    assert PD.sport_key_for("hockey", ["nhl"]) == "pinnapi_hockey"
    # never a guess: two keys, a family the key is not priced under, or no
    # token at all leave the native key
    assert PD.sport_key_for("football", ["cfb", "nfl"]) == "pinnapi_football"
    assert PD.sport_key_for("soccer", ["cfb"]) == "pinnapi_soccer"
    assert PD.sport_key_for("football") == "pinnapi_football"
    assert PD.sport_key_for("football", []) == "pinnapi_football"


def test_the_runtime_registers_a_native_nfl_seed_under_the_nfl_key(
        monkeypatch):
    from sportsassets import pinnapi_feed_runtime as FR
    c = N.cache_of(N.rec(70, "Carolina Panthers", "Detroit Lions",
                         N.NFL_START))
    regs = []
    monkeypatch.setattr(RX, "register",
                        lambda ev, **kw: regs.append(kw) or "SEEDED")
    monkeypatch.setitem(FR._STATE, "owner", _t.SimpleNamespace(
        cache=c, sport_ids=[5]))
    out = asyncio.run(FR._discovery_once(N._RowsPool(N.NFL_ROWS)))
    assert out["registered"] == {"SEEDED": 1}
    assert [r["sport_key"] for r in regs] == ["americanfootball_nfl"]
    assert regs[0]["native"] is True and regs[0]["family"] == "football"


# ═════════════════════════════════════════════════════════════════════
# 2 · A BOOK-LESS "METERED" SEED IS NOT THE CORROBORATED ONE
# ═════════════════════════════════════════════════════════════════════

def _scheduler():
    c = N.cache_of(N.rec(70, "Carolina Panthers", "Detroit Lions",
                         N.NFL_START))
    (seed,) = PD.discover(c.events, N.NFL_ROWS, sport_ids=[5])["seeds"]
    clock = [time.time()]
    s = RX.Scheduler(c, N._noop, N._noop, clock=lambda: clock[0],
                     held=N._NoHeld())
    events_seed = {"id": "odds-abc", "home_team": "Carolina Panthers",
                   "away_team": "Detroit Lions",
                   "commence_time": N.NFL_START, "bookmakers": []}
    return s, seed, events_seed, clock


def test_a_bookless_metered_seed_never_displaces_a_live_native_seed():
    s, seed, events_seed, clock = _scheduler()
    key = PD.sport_key_for("football", ["nfl"])
    assert s.register(seed, sport_key=key, family="football",
                      received_at=clock[0], native=True) == "SEEDED"
    got = s.register(events_seed, sport_key="americanfootball_nfl",
                     family="football", received_at=clock[0])
    assert got == "BOOKLESS_SEED_KEPT_NATIVE_SEED"
    assert s.seeds[70]["event"]["id"] == "pinnapi:70"
    assert s.seeds[70]["sport_key"] == "americanfootball_nfl"


def test_a_native_seed_replaces_a_live_bookless_metered_seed():
    """The other order gives the same seed: which seeder ran last no longer
    decides the fixture's identity."""
    s, seed, events_seed, clock = _scheduler()
    assert s.register(events_seed, sport_key="americanfootball_nfl",
                      family="football", received_at=clock[0]) == "SEEDED"
    assert s.register(seed, sport_key=PD.sport_key_for("football", ["nfl"]),
                      family="football", received_at=clock[0],
                      native=True) == "SEEDED"
    assert s.seeds[70]["event"]["id"] == "pinnapi:70"


def test_a_metered_seed_with_books_still_wins():
    s, seed, events_seed, clock = _scheduler()
    booked = dict(events_seed, bookmakers=[{"key": "pinnacle"}])
    assert s.register(seed, sport_key="americanfootball_nfl",
                      family="football", received_at=clock[0],
                      native=True) == "SEEDED"
    assert s.register(booked, sport_key="americanfootball_nfl",
                      family="football", received_at=clock[0]) == "SEEDED"
    assert s.seeds[70]["event"]["id"] == "odds-abc"
    assert s.register(seed, sport_key="americanfootball_nfl",
                      family="football", received_at=clock[0],
                      native=True) == "NATIVE_KEPT_METERED_SEED"


# ═════════════════════════════════════════════════════════════════════
# 3 · THE REPORT SAYS WHAT NATIVE DISCOVERY SERVES
# ═════════════════════════════════════════════════════════════════════

def test_the_scope_of_a_natively_seeded_family_is_stated_truthfully():
    # a native row is in scope: native discovery seeded it
    assert CI.lane_scope("pinnapi_basketball", token="pinnapi_basketball",
                         family="basketball") == {
        "in_scope": True, "why": None,
        "via": "PINNAPI_NATIVE_DISCOVERY"}
    # a league of a natively seeded family with no lane key: measured under
    # the native row, never "not in the collector's scope"
    nba = CI.lane_scope("basketball_nba", token="nba", family="basketball")
    assert nba["in_scope"] is True
    assert nba["measured_under"] == "pinnapi_basketball"
    nhl = CI.lane_scope("icehockey_nhl", token="nhl", family="hockey")
    assert nhl["measured_under"] == "pinnapi_hockey"
    # tennis is matched but not seeded: no tennis market family is priced
    ten = CI.lane_scope("venue:atp", token="atp", family="tennis")
    assert ten["in_scope"] is False
    assert ten["why"].startswith("FAMILY_NOT_SEEDED_NO_PRICED_MARKET")
    # the stale notes are gone
    for note in CI.FAMILY_SCOPE_NOTE.values():
        assert "not in the measured de-vig set" not in note
    # a lane-mapped league is unchanged
    assert CI.lane_scope("americanfootball_ncaaf") == {"in_scope": True,
                                                      "why": None}


def test_a_league_measured_under_the_native_row_is_unavailable_by_name():
    row = {"league": "basketball_nba", "provider_events": 0,
           "venue_catalogue_events": 9}
    st = CI.classify_status(row, scope=CI.lane_scope(
        "basketball_nba", token="nba", family="basketball"),
        collector={"fresh": True, "budget_dropped": ["basketball_nba"]})
    assert st["status"] == CI.S_UNAVAILABLE
    assert st["reason"].startswith("MEASURED_UNDER_THE_PINNAPI_NATIVE_ROW")
    assert "pinnapi_basketball" in st["reason"]
    assert "BUDGET" not in st["reason"]


async def _rows(conn, sport_key, slugs, *, now):
    for i, slug in enumerate(slugs):
        await conn.execute(
            "INSERT INTO ext_candidate_outcomes (cycle_id, cycle_at, "
            "sport_key, family, queue_position, provider_event_id, home, "
            "away, commence_time, us_market_slug, stage, outcome, "
            "first_refusal, mapped_by) VALUES ($1, to_timestamp($2), $3, "
            "'football', $4, $5, $6, $7, $8, $9, '9_ADMITTED', 'ADMITTED', "
            "NULL, 'VENUE_NATIVE')",
            "nat-%s-%d" % (sport_key, i), now - 60, sport_key, i,
            "pinnapi:%d" % (5000 + i + (100 if "nfl" in slug else 0)),
            "Home %d" % i, "Away %d" % i, None, slug)


@pg
@pytest.mark.asyncio
async def test_natively_served_college_games_are_reported_as_ncaaf():
    """The verifier's reproduction, through the real collector keying: the
    rows the reactive cycle writes for native cfb seeds carry the seed's key,
    which is now americanfootball_ncaaf."""
    import asyncpg
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("DELETE FROM ext_candidate_outcomes")
        now = time.time()
        key = PD.sport_key_for("football", ["cfb"])
        await _rows(conn, key, ["aec-cfb-a%d-b%d-2026-10-05" % (i, i)
                                for i in range(12)], now=now)
        # the rows are written at now - 60: their own local day, so a run in
        # the first minute after local midnight reads the day they sit in
        day = CI.local_day(now - 60, CI.ALERT_TIMEZONE)
        f = await CI.funnel_for_day(conn, day, CI.ALERT_TIMEZONE)
        rows = (list(f["leagues"].values())
                if isinstance(f.get("leagues"), dict) else f.get("leagues"))
        t = await CI.league_status_table(conn, rows=rows, day=day,
                                         tz=CI.ALERT_TIMEZONE, now=now)
        by = {r["league"]: r for r in t["statuses"]}
        assert "pinnapi_football" not in by or \
            by["pinnapi_football"]["counts"]["provider_events"] == 0
        ncaaf = by["americanfootball_ncaaf"]
        assert ncaaf["counts"]["provider_events"] == 12
        assert ncaaf["counts"]["settlement_supported"] == 12
        assert "NO_PROVIDER_EVENTS" not in (ncaaf["reason"] or "")
        assert ncaaf["status"] != CI.S_UNSUPPORTED
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_reconcile_reads_the_native_key_beside_the_metered_one():
    """A game whose ledger rows sit under the native key (a token the lane
    maps no single key for, or rows written before this repair) is still the
    league's game: matched by its exact venue slug, as before."""
    import asyncpg
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = time.time()
        # the rows are written at now - 60: their own local day, so a run in
        # the first minute after local midnight reads the day they sit in
        day = CI.local_day(now - 60, CI.ALERT_TIMEZONE)
        start, end = CI.day_window(day, CI.ALERT_TIMEZONE)
        kick = start + 3600 * 20
        await conn.execute("DELETE FROM ext_candidate_outcomes")
        await conn.execute("DELETE FROM us_premap WHERE event_slug LIKE "
                           "'nfl-zzh-zza-%'")
        ev = "nfl-zzh-zza-%s" % day.isoformat()
        mk = "aec-%s" % ev
        for intent, team, nick in (("1", "Zed Homers", "homers"),
                                   ("2", "Zed Aways", "aways")):
            await conn.execute(
                "INSERT INTO us_premap (identifier, market_slug, event_slug, "
                "intent, team_name, side_norm, sports_type, game_start, "
                "event_title, updated_at) VALUES ($1 || ':' || $3, $1, "
                "$2, $3, $4, $5, 'football_team_full_game_winner', "
                "to_timestamp($6), 'Zed Aways at Zed Homers', now())",
                mk, ev, intent, team, nick, kick)
        await _rows(conn, "pinnapi_football", [mk], now=now)
        got = await CI.reconcile_league(conn, token="nfl", day=day,
                                        tz=CI.ALERT_TIMEZONE, now=now)
        assert got["status"] == "OK", got
        (g,) = [g for g in got["games"] if g["market_slug"] == mk]
        assert g["stages"]["PINNAPI"] is not None, g
        assert g["stopped_at"] != "PINNAPI"
    finally:
        await tx.rollback()
        await conn.close()
