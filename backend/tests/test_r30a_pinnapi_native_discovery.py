"""R30A P0 INCIDENT -- PINNAPI-NATIVE DISCOVERY (root cause RC2).

Every subscribed Pinnacle fixture is matched to the venue's own events with
a receipt -- MATCHED / AMBIGUOUS / NO_VENUE_COUNTERPART /
NORMALIZATION_FAILURE / TIME_MISMATCH / PARTICIPANT_MISMATCH /
DUPLICATE_CANDIDATES -- stable structured identities first, the fuzzy layer
only as a labelled, controlled fallback; every MATCHED fixture seeds the
reactive scheduler, so a PinnAPI price change is evaluated with no metered
discovery at all.

PRODUCTION-SHAPED INPUTS. The venue rows are the catalogue's own records of
2026-10-04 (research-sql run 37235155757, sections L3/L4, and the venue
listing fixture tests/fixtures/pmus_line_listings_2026_10_04.json): NFL
Detroit Lions @ Carolina Panthers (team ids 58 / 52), NBA Warriors @ Clippers
(team_name 'warriors' / 'clippers', safe names 'golden state' / 'los angeles
c'), NHL Mammoth @ Rangers (safe names 'Utah Mammoth' / 'New York Rangers'),
NCAAF Southern Miss @ Troy (ids 1166 / 1307), MLB Braves @ Dodgers. The
Pinnacle side uses the raw record shape the ws_sample captured and the names
the PinnAPI REST probe (run 37232918224) listed ("Carolina Panthers" home v
"Detroit Lions", league NFL, 2026-10-05T00:20:00Z).

  §1 the receipts, one per state, on those rows
  §2 seeds: the event the cycle evaluates, the reactive registration, a
     metered seed is never displaced
  §3 the bounded catalogue read on real Postgres
  §4 END TO END: discovery seeds the fixture, a WS change evaluates it
     through the REAL cycle (venue-native identity confined to the
     discovered venue event, the global catalogue not consulted, the fixture
     key fixed once) and the completed-game paper policy ENTERs and orders
     -- no metered request made.
"""
from __future__ import annotations

import asyncio
import json
import time
import types
from datetime import datetime, timezone

import pytest

from sportsassets import pinnapi_discovery as PD
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_primary as P
from sportsassets import pinnapi_reactive as RX

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


def _ep(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


ML = {"key": "s;0;m", "type": "moneyline", "period": 0, "status": "open",
      "prices": [{"designation": "home", "price": 160},
                 {"designation": "away", "price": -180}]}


def rec(eid, home, away, start, *, league="NFL", live=False, parent=None,
        units="Regular", special=None):
    r = {"id": eid, "type": "matchup", "startTime": start, "isLive": live,
         "units": units, "league": {"id": 889, "name": league},
         "participants": [{"name": home, "alignment": "home"},
                          {"name": away, "alignment": "away"}],
         "markets": [ML]}
    if parent is not None:
        r["parentId"] = parent
    if special is not None:
        r["special"] = special
    return r


def cache_of(*records, sport=5):
    c = F.FeedCache()
    ep = c.new_connection([("prematch", sport), ("live", sport)])
    pre = [r for r in records if not r.get("isLive")]
    live = [r for r in records if r.get("isLive")]
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": sport,
             "ts": 1_000, "events": pre}, epoch=ep, received_ms=1_000)
    c.apply({"type": "snapshot", "stream": "live", "sport_id": sport,
             "ts": 1_000, "events": live}, epoch=ep, received_ms=1_000)
    return c


def vrow(slug, team, tid, start, *, safe=None, league=None, stype=None,
         abbr=None):
    league = league or slug.split("-", 1)[0]
    return {"event_slug": slug, "team_name": team, "team_safe_name": safe,
            "team_abbr": abbr, "team_id": tid, "team_league": league,
            "sports_type": stype or "football_team_full_game_spread",
            "game_start": _ep(start), "rows": 2}


NFL_START = "2026-10-05T00:20:00Z"
NFL_ROWS = [vrow("nfl-det-car-2026-10-04", "detroit lions", 58, NFL_START,
                 safe="det lions", abbr="det"),
            vrow("nfl-det-car-2026-10-04", "carolina panthers", 52,
                 NFL_START, safe="car panthers", abbr="car"),
            # the winner contract's rows of the SAME team records
            vrow("nfl-det-car-2026-10-04", "detroit lions", 58, NFL_START,
                 stype="football_team_full_game_winner"),
            vrow("nfl-det-car-2026-10-04", "carolina panthers", 52,
                 NFL_START, stype="football_team_full_game_winner")]
NBA_START = "2026-10-04T23:00:00Z"
NBA_ROWS = [vrow("nba-gs-lac-2026-10-04", "warriors", 47, NBA_START,
                 safe="golden state", stype="basketball_team_full_game_spread"),
            vrow("nba-gs-lac-2026-10-04", "clippers", 20, NBA_START,
                 safe="los angeles c",
                 stype="basketball_team_full_game_spread")]
NHL_START = "2026-10-04T22:00:00Z"
NHL_ROWS = [vrow("nhl-uta-nyr-2026-10-04", "mammoth", 1515, NHL_START,
                 safe="utah mammoth", stype="hockey_team_full_game_spread"),
            vrow("nhl-uta-nyr-2026-10-04", "rangers", 1495, NHL_START,
                 safe="new york rangers",
                 stype="hockey_team_full_game_spread")]
CFB_START = "2026-10-07T00:00:00Z"
CFB_ROWS = [vrow("cfb-soumis-troy-2026-10-06", "southern miss", 1166,
                 CFB_START, safe="southern miss"),
            vrow("cfb-soumis-troy-2026-10-06", "troy", 1307, CFB_START,
                 safe="troy")]


def one(result, fixture_id):
    got = [r for r in result["receipts"] if r["fixture_id"] == fixture_id]
    assert len(got) == 1, result["receipts"]
    return got[0]


# ═════════════════════════════════════════════════════════════════════
# §1 THE RECEIPTS
# ═════════════════════════════════════════════════════════════════════

def test_an_nfl_fixture_matches_its_venue_event_by_the_team_records():
    c = cache_of(rec(1637432188, "Carolina Panthers", "Detroit Lions",
                     NFL_START))
    out = PD.discover(c.events, NFL_ROWS, sport_ids=[5])
    r = one(out, 1637432188)
    assert r["state"] == PD.MATCHED
    assert r["matched_by"] == PD.MATCHED_BY_EXACT
    assert r["venue_event_slug"] == "nfl-det-car-2026-10-04"
    assert r["venue_participants"] == {"home": "carolina panthers",
                                       "away": "detroit lions"}
    assert r["venue_league_tokens"] == ["nfl"]
    assert r["start_offset_s"] == 0.0 and r["family"] == "football"
    assert out["states"][PD.MATCHED] == 1
    assert out["by_sport_league_state"] == {"football|NFL|MATCHED": 1}


def test_ncaaf_matches_on_the_venue_school_never_by_containment():
    c = cache_of(rec(1, "Troy", "Southern Miss", CFB_START, league="NCAA"),
                 # "Ohio" against the venue's "ohio state" (Kent State
                 # present): containment would cross-map them
                 rec(2, "Ohio", "Kent State", "2026-10-04T19:30:00Z",
                     league="NCAA"))
    rows = CFB_ROWS + [
        vrow("cfb-ohiost-kentst-2026-10-04", "ohio state", 900,
             "2026-10-04T19:30:00Z"),
        vrow("cfb-ohiost-kentst-2026-10-04", "kent state", 901,
             "2026-10-04T19:30:00Z")]
    out = PD.discover(c.events, rows, sport_ids=[5])
    assert one(out, 1)["state"] == PD.MATCHED
    r2 = one(out, 2)
    assert r2["state"] == PD.PARTICIPANT_MISMATCH
    assert r2["candidates"] == ["cfb-ohiost-kentst-2026-10-04"]


def test_nba_and_nhl_names_resolve_through_the_records_own_fields():
    nba = cache_of(rec(10, "Los Angeles Clippers", "Golden State Warriors",
                       NBA_START, league="NBA"), sport=3)
    out = PD.discover(nba.events, NBA_ROWS, sport_ids=[3])
    r = one(out, 10)
    assert r["state"] == PD.MATCHED
    # "golden state" + "warriors" is the record's own exact rendering; the
    # venue's "los angeles c" safe name is not a name, so the Clippers are
    # confirmed by the CONTROLLED fallback -- and the receipt says so
    assert r["matched_by"] == PD.MATCHED_BY_FALLBACK
    how = {h["venue"]: h for h in r["assignment"]}
    assert how["warriors"]["by"] == PD.MATCHED_BY_EXACT
    assert how["clippers"]["by"] == PD.MATCHED_BY_FALLBACK
    assert how["clippers"]["shared"] == ["clippers"]
    nhl = cache_of(rec(11, "New York Rangers", "Utah Mammoth", NHL_START,
                       league="NHL"), sport=4)
    out = PD.discover(nhl.events, NHL_ROWS, sport_ids=[4])
    r = one(out, 11)
    assert r["state"] == PD.MATCHED and r["matched_by"] == PD.MATCHED_BY_EXACT


def test_time_mismatch_ambiguous_and_no_counterpart_are_named():
    day1, day2 = "2026-10-05T00:00:00Z", "2026-10-06T00:00:00Z"
    mlb = lambda slug, start: [
        vrow(slug, "atlanta braves", 3001, start, safe="braves",
             stype="baseball_team_full_game_winner"),
        vrow(slug, "los angeles dodgers", 3014, start, safe="dodgers",
             stype="baseball_team_full_game_winner")]
    c = cache_of(rec(20, "Los Angeles Dodgers", "Atlanta Braves", day1,
                     league="MLB"), sport=6)
    # the venue lists only tomorrow's game of the series
    out = PD.discover(c.events, mlb("mlb-atl-lad-2026-10-05", day2),
                      sport_ids=[6])
    r = one(out, 20)
    assert r["state"] == PD.TIME_MISMATCH
    assert r["start_offset_s"] == 86400.0
    # the venue lists the same game twice inside the tolerance
    out = PD.discover(c.events, mlb("mlb-atl-lad-2026-10-04", day1)
                      + mlb("mlb-atl-lad-2026-10-04-g2",
                            "2026-10-05T00:45:00Z"), sport_ids=[6])
    assert one(out, 20)["state"] == PD.AMBIGUOUS
    # a fixture whose teams the venue does not list at all
    out = PD.discover(c.events, mlb("mlb-nyy-bos-2026-10-04", day1)[:0] + [
        vrow("mlb-nyy-bos-2026-10-04", "new york yankees", 1, day1,
             stype="baseball_team_full_game_winner"),
        vrow("mlb-nyy-bos-2026-10-04", "boston red sox", 2, day1,
             stype="baseball_team_full_game_winner")], sport_ids=[6])
    r = one(out, 20)
    assert r["state"] == PD.NO_VENUE_COUNTERPART
    assert r["venue_events_in_sport"] == 1


def test_two_fixtures_claiming_one_venue_event_are_duplicates():
    c = cache_of(rec(30, "Carolina Panthers", "Detroit Lions", NFL_START),
                 rec(31, "Carolina Panthers", "Detroit Lions",
                     "2026-10-05T01:00:00Z"))
    out = PD.discover(c.events, NFL_ROWS, sport_ids=[5])
    for fid in (30, 31):
        r = one(out, fid)
        assert r["state"] == PD.DUPLICATE_CANDIDATES
        assert sorted(r["claimed_by"]) == [30, 31]
    assert out["seeds"] == []


def test_an_unreadable_fixture_is_a_normalization_failure():
    c = cache_of(rec(40, "Carolina Panthers", "Detroit Lions", "soon"),
                 rec(41, "Same Team", "Same Team", NFL_START))
    out = PD.discover(c.events, NFL_ROWS, sport_ids=[5])
    assert one(out, 40)["reason"] == PD.N_START
    assert one(out, 41)["reason"] == PD.N_PARTICIPANTS
    assert out["states"][PD.NORMALIZATION_FAILURE] == 2


def test_an_in_play_fixture_is_discovered_through_its_live_child():
    parent = rec(50, "Carolina Panthers", "Detroit Lions", NFL_START)
    child = rec(51, "Carolina Panthers", "Detroit Lions", NFL_START,
                live=True, parent=50)
    prop = rec(52, "Carolina Panthers", "Detroit Lions", NFL_START,
               live=True, parent=51, special="Will there be overtime?")
    c = cache_of(parent, child, prop)
    out = PD.discover(c.events, NFL_ROWS, sport_ids=[5])
    r = one(out, 50)
    assert r["state"] == PD.MATCHED and r["quote_id"] == 51 and r["live"]
    assert out["fixtures"] == 1
    assert out["records_pricing_no_fixture"] == {F.R_CHILD_OF_A_CHILD: 1}


def test_unsubscribed_sports_and_venue_events_without_a_fixture_are_counted():
    c = cache_of(rec(60, "Carolina Panthers", "Detroit Lions", NFL_START))
    out = PD.discover(c.events, NFL_ROWS + NBA_ROWS, sport_ids=[5, 3])
    assert out["fixtures"] == 1
    assert out["venue_events"] == 2
    assert out["venue_events_without_a_fixture"] == 1     # the NBA game
    out = PD.discover(c.events, NFL_ROWS, sport_ids=[6])
    assert out["fixtures"] == 0


# ═════════════════════════════════════════════════════════════════════
# §2 SEEDS
# ═════════════════════════════════════════════════════════════════════

def test_the_seed_is_the_event_the_cycle_reads_with_the_discovery_identity():
    c = cache_of(rec(1637432188, "Carolina Panthers", "Detroit Lions",
                     NFL_START))
    out = PD.discover(c.events, NFL_ROWS, sport_ids=[5])
    (seed,) = out["seeds"]
    assert seed["id"] == "pinnapi:1637432188"
    assert seed["home_team"] == "Carolina Panthers"
    assert seed["away_team"] == "Detroit Lions"
    assert seed["commence_time"] == NFL_START
    assert seed["bookmakers"] == []
    n = seed["pinnapi_native"]
    assert n["venue_event_slug"] == "nfl-det-car-2026-10-04"
    assert n["venue_league_tokens"] == ["nfl"] and n["family"] == "football"
    # the primary selector's exact name match finds THIS fixture
    hit, why = P.match_event(c, seed, "football")
    assert why is None and hit[0] == 1637432188
    assert len(json.dumps(seed)) < 16384
    assert PD.sport_key_for("football") == "pinnapi_football"
    dg = PD.digest(dict(out, registered={"SEEDED": 1}))
    assert "receipts" not in dg and dg["registered"] == {"SEEDED": 1}


class _NoHeld:
    def is_held(self, _eid):
        return False


async def _noop(*_a, **_k):
    return None


def test_a_native_seed_never_displaces_a_metered_seed():
    c = cache_of(rec(70, "Carolina Panthers", "Detroit Lions", NFL_START))
    (seed,) = PD.discover(c.events, NFL_ROWS, sport_ids=[5])["seeds"]
    clock = [time.time()]
    s = RX.Scheduler(c, _noop, _noop, clock=lambda: clock[0],
                     held=_NoHeld())
    metered = {"id": "odds-abc", "home_team": "Carolina Panthers",
               "away_team": "Detroit Lions", "commence_time": NFL_START,
               "bookmakers": [{"key": "pinnacle"}]}
    assert s.register(metered, sport_key="americanfootball_nfl",
                      family="football", received_at=clock[0]) == "SEEDED"
    assert s.register(seed, sport_key="pinnapi_football", family="football",
                      received_at=clock[0], native=True) == \
        "NATIVE_KEPT_METERED_SEED"
    assert s.seeds[70]["event"]["id"] == "odds-abc"
    # once the metered seed expires, the native one takes the fixture
    clock[0] += s.seed_ttl + 1
    assert s.register(seed, sport_key="pinnapi_football", family="football",
                      received_at=clock[0], native=True) == "SEEDED"
    assert s.seeds[70]["event"]["id"] == "pinnapi:70"
    # a sport the primary selector does not know is refused by name
    assert s.register(dict(seed), sport_key="x", family="curling",
                      received_at=clock[0], native=True) == \
        "PINNAPI_PRIMARY_SPORT_UNSUPPORTED"


# ═════════════════════════════════════════════════════════════════════
# §3 THE CATALOGUE READ ON POSTGRES
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_bounded_venue_read_feeds_the_same_receipts():
    import asyncpg
    from sportsassets.workers import premap as pm
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await pm._ensure_table(conn)
        start = time.time() + 6 * 3600
        dt = datetime.fromtimestamp(start, timezone.utc)
        iso = dt.isoformat()
        slug = "nba-gs-lac-r30a-%d" % int(start)
        for ident, side, team, tid, safe in (
                ("asc-%s-neg-2pt5" % slug, "yes", "warriors", 47,
                 "golden state"),
                ("asc-%s-neg-2pt5" % slug, "no", "clippers", 20,
                 "los angeles c"),
                ("tsc-%s-221pt5" % slug, "over", None, None, None)):
            await conn.execute(
                "INSERT INTO us_premap (identifier, event_slug, event_title,"
                " market_slug, kind, side_norm, team_name, team_safe_name, "
                " team_id, team_league, game_start, sports_type) VALUES "
                "($1,$2,'GS Warriors vs. LA Clippers',$1,'side',$3,$4,$5,$6,"
                " 'nba', $7, $8)", ident, slug, side, team, safe,
                tid, dt, "basketball_team_full_game_spread")
        # a row the venue stopped listing: not current, not read
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, side_norm, "
            " team_name, team_id, team_league, game_start, sports_type, "
            " updated_at) VALUES ('asc-stale', $1, 'yes', 'lakers', 13, "
            " 'nba', $2, 'basketball_team_full_game_spread', "
            " now() - interval '3 hours')", slug + "-x", dt)
        rows = [dict(r) for r in await conn.fetch(PD.venue_events_sql([3]))
                if r["event_slug"].startswith(slug)]
        assert sorted(r["team_name"] for r in rows) == ["clippers",
                                                        "warriors"]
        c = cache_of(rec(80, "Los Angeles Clippers", "Golden State Warriors",
                         iso, league="NBA"), sport=3)
        out = PD.discover(c.events, rows, sport_ids=[3])
        r = one(out, 80)
        assert r["state"] == PD.MATCHED and r["venue_event_slug"] == slug
        # scoped: no sport, no rows
        assert "AND false" in PD.venue_events_sql([])
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §4 END TO END: DISCOVERY -> WS CHANGE -> REAL CYCLE -> PAPER ENTER
# ═════════════════════════════════════════════════════════════════════

from tests import test_pinnapi_reactive_integration as RI  # noqa: E402

env = RI.env


@pg
async def test_a_natively_discovered_fixture_enters_with_no_metered_request(
        env):
    import asyncpg
    from sportsassets.workers import ext_pinnacle_loop as loop
    e = env
    e.pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=4)
    e.task = RX.start(e.pool, cycle=loop.cycle)
    assert e.task is not None
    await e.conn.execute(
        "SET session_replication_role = replica; "
        "DELETE FROM venue_fixture_event_keys WHERE venue_event_slug = '%s';"
        "SET session_replication_role = origin" % e.game.event_slug)
    try:
        rows = [dict(r) for r in await e.conn.fetch(
            PD.venue_events_sql([RI.SPORT_ID]))]
        out = PD.discover(e.cache.events, rows, sport_ids=[RI.SPORT_ID])
        r = one(out, e.eid)
        assert r["state"] == PD.MATCHED, r
        assert r["venue_event_slug"] == e.game.event_slug
        (seed,) = [s for s in out["seeds"]
                   if s["pinnapi_native"]["fixture_id"] == e.eid]
        got = RX.register(seed, sport_key=PD.sport_key_for("baseball"),
                          family="baseball", received_at=time.time(),
                          native=True)
        assert got == "SEEDED", dict(RX.ACTIVE.counts)
        assert e.fetches == [], "no metered discovery request"
        assert not RX.ACTIVE.pending

        RI.tick(e.cache, e.eid, RI.WS_EDGE)          # the WS change
        assert len(RX.ACTIVE.pending) == 1
        attempts = await RI._wait_terminal(e)
        assert [a["state"] for a in attempts] == ["COMPLETED"], attempts
        a = attempts[0]["detail"]
        vids = a["valuation_ids"]
        assert len(vids) == 1, a
        v = await RI._valuation(e, vids[0])
        assert v["us_market_slug"] == e.game.us_slug
        assert v["provider"] == P.PROVIDER
        mapping = H.j(v["mapping"]) if "mapping" in v.keys() else None
        scmp = H.j(v["settlement_comparison"])
        ref = scmp["reference_input"]
        assert ref["feed_event_id"] == e.eid
        assert ref["discovery_event_id"] == "pinnapi:%d" % e.eid
        assert ref["decision_check"]["ok"] is True
        # the fixture's event key, fixed once for the venue event
        assert v["event_key"] == "pinnapi:%d" % e.eid
        fixed = await e.conn.fetchrow(
            "SELECT event_key, basis FROM venue_fixture_event_keys "
            " WHERE venue_event_slug = $1", e.game.event_slug)
        assert fixed["event_key"] == "pinnapi:%d" % e.eid
        if mapping is not None:
            assert "GLOBAL_CATALOGUE_NOT_CONSULTED" in json.dumps(mapping)
        # the completed-game paper policy decides THAT valuation and enters
        ds = RI._cg(await RI._decisions(e, vids))
        assert len(ds) == 1
        assert ds[0]["verdict"] == "ENTER", (ds[0]["refusal"],
                                             ds[0]["refusals"])
        orders = await e.conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE decision_id = $1",
            ds[0]["decision_id"])
        assert orders == 1
        assert e.fetches == [], "still no metered request"
    finally:
        await e.conn.execute(
            "SET session_replication_role = replica; "
            "DELETE FROM venue_fixture_event_keys WHERE venue_event_slug = "
            "'%s'; SET session_replication_role = origin"
            % e.game.event_slug)
