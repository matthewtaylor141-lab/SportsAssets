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

import json
import time
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


# ═════════════════════════════════════════════════════════════════════
# §5 MIGRATION 261: ONE VENUE FIXTURE, ONE EVENT KEY
# ═════════════════════════════════════════════════════════════════════

import pathlib  # noqa: E402

_MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"


@pg
async def test_the_first_key_is_fixed_once_and_a_second_discovery_reads_it():
    import asyncpg
    from sportsassets.workers import ext_pinnacle_loop as loop
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        slug = "nfl-det-car-r30a-sticky-%d" % int(time.time())
        got = await loop.sticky_event_key(conn, venue_event_slug=slug,
                                          proposed="odds-abc")
        assert got["event_key"] == "odds-abc"
        assert got["fixed_in"] == "venue_fixture_event_keys"
        # the SAME venue event found later by PinnAPI-native discovery keeps
        # the first key, so the fixture rails see one fixture
        again = await loop.sticky_event_key(conn, venue_event_slug=slug,
                                            proposed="pinnapi:1637712345")
        assert again["event_key"] == "odds-abc"
        assert again["basis"].startswith("FIXED_FOR_THIS_VENUE_EVENT")
        # a cycle's own cache answers without a read
        cache = {}
        await loop.sticky_event_key(conn, venue_event_slug=slug,
                                    proposed="x", cache=cache)
        assert cache[slug]["event_key"] == "odds-abc"
        # no venue event: the proposal stands, said so
        none = await loop.sticky_event_key(conn, venue_event_slug=None,
                                           proposed="odds-q")
        assert none["event_key"] == "odds-q"
        assert none["basis"] == "PROVIDER_EVENT_ID_NO_VENUE_EVENT_KEY"
        # append-only: a fixed key is never rewritten or removed
        for sql in ("UPDATE venue_fixture_event_keys SET event_key='y' "
                    " WHERE venue_event_slug=$1",
                    "DELETE FROM venue_fixture_event_keys "
                    " WHERE venue_event_slug=$1"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.exceptions.IntegrityConstraintViolationError):
                await conn.execute(sql, slug)
            await sp.rollback()
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_migration_261_is_idempotent_and_its_rollback_refuses_a_fixed_key():
    import asyncpg
    up = (_MIG / "261_venue_fixture_event_keys.sql").read_text()
    down = (_MIG / "rollback" / "261_venue_fixture_event_keys.down.sql") \
        .read_text()
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(up)                       # a re-run changes nothing
        await conn.execute(
            "INSERT INTO venue_fixture_event_keys (venue_event_slug, "
            " event_key, basis) VALUES ('r30a-rollback-probe', 'k', 'b')")
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(down)                 # rows exist: refused
        await sp.rollback()
        # emptied (inside this transaction only), the rollback applies and
        # the migration re-applies cleanly
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("DELETE FROM venue_fixture_event_keys")
        await conn.execute("SET LOCAL session_replication_role = origin")
        await conn.execute(down)
        assert await conn.fetchval(
            "SELECT to_regclass('venue_fixture_event_keys')") is None
        # a SECOND rollback is a no-op (fix stage 2026-10-05: its guard read
        # the dropped table and raised UndefinedTableError -- a PL/pgSQL
        # `IF a AND b` does not short-circuit the planning of `b`)
        await conn.execute(down)
        await conn.execute(up)
        assert await conn.fetchval(
            "SELECT to_regclass('venue_fixture_event_keys')") is not None
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §5 THE ADVERSARIAL VERIFIER'S FINDING 1 (fix stage, 2026-10-05)
# ═════════════════════════════════════════════════════════════════════
#
# Discovery was CPU-bound and ran on the API's event loop (beside the WS
# owner, the scheduled collector and HTTP), and its asyncio timeout could
# not stop synchronous work: every unmatched fixture ran the controlled
# fallback against EVERY venue event of its sport (team_profile / fold per
# pair), and each reactive registration rebuilt the fixture view over the
# whole cache. Measured (verif/bench_discover.py): 1,300 fixtures x 400
# venue events = 29.67 s of blocking CPU per 60 s pass; cProfile 13.8 of
# 13.9 s in _assign -> _fallback -> team_profile.

import random as _random                                         # noqa: E402

_PLACES = ["arsenal", "valencia", "porto", "lyon", "bremen", "malmo",
           "bergen", "celta", "lazio", "genoa", "braga", "gent", "basel",
           "zurich", "lille", "nantes", "metz", "brest", "lens", "nice",
           "rennes", "reims", "caen", "sion", "thun", "aarau", "koln",
           "mainz", "bochum", "fulham", "leeds", "derby", "wigan", "luton",
           "exeter", "barnet", "manchester", "madrid", "milan", "sevilla"]
_WORDS = ["united", "city", "athletic", "real", "sporting", "rovers",
          "wanderers", "dynamo", "olympic", "racing", "inter", "atletico",
          "north", "royal", "union", "nacional", "deportivo", "town"]


def _reference_match(fx, venue, *, family):
    """THE PRE-FIX ALGORITHM, verbatim (3f6bd43 match_fixture): every
    receipt the indexed implementation writes must equal this one's."""
    out = {"fixture_id": fx.get("id"), "state": None}
    home, away = str(fx.get("home") or ""), str(fx.get("away") or "")
    if not home.strip() or not away.strip() or \
            PD._fold(home) == PD._fold(away):
        out.update(state=PD.NORMALIZATION_FAILURE, reason=PD.N_PARTICIPANTS)
        return out
    if F._derived_suffix(home) or F._derived_suffix(away):
        out.update(state=PD.NORMALIZATION_FAILURE, reason=PD.N_DERIVED)
        return out
    start = PD._epoch(fx.get("startTime"))
    if start is None:
        out.update(state=PD.NORMALIZATION_FAILURE, reason=PD.N_START)
        return out
    inside = [e for e in venue if e["start"] is not None
              and abs(e["start"] - start) <= PD.START_TOLERANCE_S]
    hits = [(e, a) for e in inside for a in [PD._assign(
        home, away, e, family, allow_fallback=False)] if a is not None]
    if not hits:
        hits = [(e, a) for e in inside for a in [PD._assign(
            home, away, e, family, allow_fallback=True)] if a is not None]
    if len(hits) > 1:
        out.update(state=PD.AMBIGUOUS,
                   candidates=[e["slug"] for e, _ in hits][:6])
        return out
    if len(hits) == 1:
        ev, a = hits[0]
        if ev["problems"]:
            out.update(state=PD.AMBIGUOUS, venue_event_slug=ev["slug"])
            return out
        fb = any(h["by"] == PD.MATCHED_BY_FALLBACK for h in a["how"])
        out.update(state=PD.MATCHED, venue_event_slug=ev["slug"],
                   matched_by=PD.MATCHED_BY_FALLBACK if fb
                   else PD.MATCHED_BY_EXACT, orientation=a["orientation"],
                   assignment=a["how"])
        return out
    elsewhere = [e for e in venue if e not in inside and e["start"] is not
                 None and PD._assign(home, away, e, family,
                                     allow_fallback=True)]
    if elsewhere:
        near = min(elsewhere, key=lambda e: abs(e["start"] - start))
        out.update(state=PD.TIME_MISMATCH, venue_event_slug=near["slug"],
                   start_offset_s=round(near["start"] - start, 1))
        return out
    partial = [e for e in inside if PD._touches(home, e, family)
               or PD._touches(away, e, family)]
    if partial:
        out.update(state=PD.PARTICIPANT_MISMATCH,
                   candidates=[e["slug"] for e in partial][:6])
        return out
    out.update(state=PD.NO_VENUE_COUNTERPART)
    return out


def _slate(seed, n_fixtures, n_venue, *, sport=1):
    """A production-shaped slate with every receipt state represented:
    exact names, the venue's own shorter / affiliation-marked renderings
    (the controlled fallback), a squad qualifier, a city with two clubs,
    the same pair a day apart, one participant only, and noise."""
    rnd = _random.Random(seed)
    stype = {1: "soccer_team_full_time_winner",
             5: "football_team_full_game_winner"}[sport]
    base = 1_790_000_000

    def nm():
        return "%s %s %d" % (rnd.choice(_PLACES).title(),
                             rnd.choice(_WORDS).title(),
                             rnd.randint(1, 60))
    events, rows = {}, []
    fixtures = []
    for i in range(n_fixtures):
        st = base + rnd.randint(0, 4 * 86400)
        h, a = nm(), nm()
        fixtures.append((h, a, st))
        events[10_000_000 + i] = {
            "id": 10_000_000 + i, "sport_id": sport, "startTime": st,
            "isLive": False, "units": "Regular",
            "league": {"name": "L%d" % (i % 40)},
            "participants": [{"name": h, "alignment": "home"},
                             {"name": a, "alignment": "away"}]}
    for j in range(n_venue):
        slug = "v%d-x-y-2026" % j
        kind = j % 8
        h, a, st = fixtures[j % n_fixtures] if j < n_fixtures else (
            nm(), nm(), base + rnd.randint(0, 4 * 86400))
        if kind == 0:
            vh, va = h.lower(), a.lower()                       # exact
        elif kind == 1:
            vh, va = h.lower() + " fc", a.lower()               # affiliation
        elif kind == 2:
            vh, va = " ".join(h.lower().split()[:1] +
                              h.lower().split()[2:]), a.lower()  # shorter
        elif kind == 3:
            vh, va = h.lower() + " women", a.lower()            # squad
        elif kind == 4:
            vh, va, st = h.lower(), a.lower(), st + 86400       # a day off
        elif kind == 5:
            vh, va = h.lower(), nm().lower()                    # one only
        elif kind == 6:
            vh, va = a.lower(), h.lower()                       # swapped
        else:
            vh, va, st = nm().lower(), nm().lower(), st         # noise
        for k, t in enumerate((vh, va)):
            rows.append({"event_slug": slug, "team_name": t,
                         "team_safe_name": None, "team_abbr": None,
                         "team_id": j * 2 + k, "team_league": "x",
                         "sports_type": stype, "game_start": st, "rows": 2})
    # a duplicated venue event (AMBIGUOUS) and a duplicated fixture
    # (DUPLICATE_CANDIDATES), as the real listings sometimes carry
    if n_venue > 2:
        rows += [dict(r, event_slug="dup-" + r["event_slug"],
                      team_id=900_000 + r["team_id"])
                 for r in rows if r["event_slug"] == "v0-x-y-2026"]
    if n_fixtures > 9:
        events[19_999_999] = dict(events[10_000_008], id=19_999_999)
    return events, rows


@pytest.mark.parametrize("sport,seed", [(1, 7), (1, 11), (5, 3)])
def test_the_indexed_discovery_writes_exactly_the_receipts_it_did_before(
        sport, seed):
    """THE FIX CHANGES NO RECEIPT. Every fixture's state, venue event,
    candidates, assignment and offset equal the pre-fix algorithm's, on
    slates that exercise every state and both the exact and the fallback
    rule (football: token equality; other sports: containment with a shared
    distinctive token)."""
    events, rows = _slate(seed, 160, 120, sport=sport)
    got = PD.discover(events, rows, sport_ids=[sport])
    assert got["pass_state"] == PD.PASS_COMPLETE
    venue = PD.venue_events(rows).get(sport, [])
    view, _ = F.fixture_view(events)
    want = {fx["id"]: _reference_match(
        fx, venue, family=PD.FAMILY_OF_SPORT[sport]) for fx in view}
    states = set()
    for r in got["receipts"]:
        w = want[r["fixture_id"]]
        states.add(w["state"])
        if r["state"] == PD.DUPLICATE_CANDIDATES:
            assert w["state"] == PD.MATCHED
            continue
        for k, v in w.items():
            assert r.get(k) == v, (k, r, w)
    assert {PD.MATCHED, PD.TIME_MISMATCH, PD.PARTICIPANT_MISMATCH,
            PD.NO_VENUE_COUNTERPART, PD.AMBIGUOUS} <= states, states
    assert got["states"][PD.DUPLICATE_CANDIDATES] >= 2


def test_a_production_scale_pass_is_bounded_cpu(monkeypatch):
    """1,300 soccer fixtures x 400 venue events (production's soccer scale):
    29.67 s before the fix. The venue renderings are profiled ONCE per pass
    (not once per fixture x venue event) and only the candidates that share
    a name or a token are compared."""
    from sportsassets import bettor_venue_native_identity as vnat
    calls = []
    real = vnat.team_profile

    def counted(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(vnat, "team_profile", counted)
    events, rows = _slate(5, 1300, 400, sport=1)
    t0 = time.monotonic()
    got = PD.discover(events, rows, sport_ids=[1])
    took = time.monotonic() - t0
    assert got["pass_state"] == PD.PASS_COMPLETE
    assert got["fixtures"] == 1301
    # each distinct text (a fixture's two names, a venue record's
    # renderings) is profiled at most once per pass
    texts = {p["name"] for e in events.values() for p in e["participants"]}
    texts |= {r["team_name"] for r in rows} | {r["team_name"] + " " for r in
                                               rows}
    assert len(calls) <= 2 * len(texts), (len(calls), len(texts))
    assert took < 5.0, "a bounded pass (it was 29.67 s), took %.2f s" % took


def test_a_pass_over_its_cpu_budget_stops_and_seeds_nothing():
    """The pass checks its own deadline (asyncio.timeout cannot interrupt a
    synchronous loop or a worker thread): over budget it STOPS, names it,
    and seeds nothing -- a DUPLICATE_CANDIDATES check over a partial pass
    could not be trusted, so no partial identity is published."""
    events, rows = _slate(9, 200, 100, sport=1)
    got = PD.discover(events, rows, sport_ids=[1],
                      deadline=time.monotonic() - 1.0)
    assert got["pass_state"] == PD.PASS_OVER_BUDGET
    assert got["seeds"] == [] and got["by_venue_event"] == {}
    assert got["fixtures_not_examined"] == 201
    assert PD.digest(got)["pass_state"] == PD.PASS_OVER_BUDGET
    full = PD.discover(events, rows, sport_ids=[1],
                       deadline=time.monotonic() + 60.0)
    assert full["pass_state"] == PD.PASS_COMPLETE and full["seeds"]


def test_one_fixture_index_registers_every_seed_without_a_rebuild(
        monkeypatch):
    """Each registration used to rebuild `fixture_view` over the whole cache
    (~7 ms per seed on 2,600 events). One index per pass, same answers."""
    c = cache_of(rec(70, "Carolina Panthers", "Detroit Lions", NFL_START),
                 rec(71, "Detroit Lions", "Carolina Panthers", NFL_START,
                     league="NFL2"),
                 rec(72, "Green Bay Packers", "Chicago Bears", NFL_START))
    idx = P.fixture_index(c)
    for ev in ({"home_team": "Carolina Panthers",
                "away_team": "Detroit Lions", "commence_time": NFL_START},
               {"home_team": "Chicago Bears",
                "away_team": "Green Bay Packers", "commence_time": NFL_START},
               {"home_team": "Chicago Bears",
                "away_team": "Green Bay Packers",
                "commence_time": "2026-10-06T00:20:00Z"},
               {"home_team": "Nobody", "away_team": "Nowhere",
                "commence_time": NFL_START}):
        assert P.match_event(c, ev, "football", index=idx) == \
            P.match_event(c, ev, "football")
    views = []
    real = F.fixture_view

    def counted(events):
        views.append(1)
        return real(events)
    monkeypatch.setattr(F, "fixture_view", counted)
    s = RX.Scheduler(c, _noop, _noop, clock=time.time, held=_NoHeld())
    seed = {"id": "pinnapi:72", "home_team": "Green Bay Packers",
            "away_team": "Chicago Bears", "commence_time": NFL_START,
            "pinnapi_native": {"fixture_id": 72, "family": "football"}}
    for _ in range(5):
        assert s.register(seed, sport_key="pinnapi_football",
                          family="football", received_at=time.time(),
                          native=True, index=idx) == "SEEDED"
    assert views == [], "no rebuild of the fixture view per registration"


class _RowsConn:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *args):
        return self.rows if "GROUP BY event_slug" in sql else []


class _RowsPool:
    def __init__(self, rows):
        self.conn = _RowsConn(rows)

    def acquire(self):
        conn = self.conn

        class Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_):
                return False
        return Ctx()


def test_the_runtime_runs_the_pass_off_the_event_loop(monkeypatch):
    """`_discovery_once` hands the CPU-bound pass to a worker thread over a
    SNAPSHOT of the cache's events (the thread never reads the live cache
    the WS owner writes), under its own CPU deadline, and registers the
    seeds back on the loop through one fixture index of the current cache."""
    import asyncio
    import threading
    import types as _t
    from sportsassets import pinnapi_feed_runtime as FR
    c = cache_of(rec(70, "Carolina Panthers", "Detroit Lions", NFL_START))
    seen = {}
    real = PD.discover

    def spy(events, rows, *, sport_ids, deadline=None):
        seen["thread"] = threading.get_ident()
        seen["snapshot_is_live_cache"] = events is c.events
        seen["deadline"] = deadline
        return real(events, rows, sport_ids=sport_ids, deadline=deadline)
    monkeypatch.setattr(PD, "discover", spy)
    regs = []

    def register(ev, **kw):
        regs.append(kw)
        return "SEEDED"
    monkeypatch.setattr(RX, "register", register)
    monkeypatch.setitem(FR._STATE, "owner", _t.SimpleNamespace(
        cache=c, sport_ids=[5]))

    async def go():
        seen["loop"] = threading.get_ident()
        t0 = time.monotonic()
        out = await FR._discovery_once(_RowsPool(NFL_ROWS))
        return out, t0
    out, t0 = asyncio.run(go())
    assert seen["thread"] != seen["loop"], "the pass ran on the event loop"
    assert seen["snapshot_is_live_cache"] is False
    assert seen["deadline"] is not None and \
        seen["deadline"] - t0 <= FR.DISCOVERY_CPU_BUDGET_S + 1.0
    assert FR.DISCOVERY_CPU_BUDGET_S < FR.DISCOVERY_TIMEOUT_S
    assert out["states"][PD.MATCHED] == 1
    assert out["registered"] == {"SEEDED": 1}
    assert regs and isinstance(regs[0].get("index"), dict)


def test_a_family_with_no_priceable_market_is_matched_but_not_seeded(
        monkeypatch):
    """ADVERSARIAL VERIFICATION P2 (fix stage): tennis fixtures were seeded
    although no tennis market can be priced (no tennis money line in the
    de-vig set; tennis spreads / totals NOT_PROVEN), so every in-play tennis
    change took a single-worker evaluation and wrote two audit rows for
    nothing. The receipt stays MATCHED; the seed is counted by name."""
    import asyncio
    import types as _t
    from sportsassets import pinnapi_feed_runtime as FR
    fams = FR.families_with_a_priced_market()
    assert "tennis" not in fams
    assert {"soccer", "baseball", "football", "basketball",
            "hockey"} <= fams
    start = "2026-10-04T15:00:00Z"
    c = cache_of(rec(81, "Holger Rune", "Kyrian Jacquet", start,
                     league="ATP"), sport=2)
    rows = [vrow("atp-rune-jac-2026-10-04", "holger rune", 1, start,
                 stype="tennis_match_winner"),
            vrow("atp-rune-jac-2026-10-04", "kyrian jacquet", 2, start,
                 stype="tennis_match_winner")]
    regs = []
    monkeypatch.setattr(RX, "register",
                        lambda ev, **kw: regs.append(kw) or "SEEDED")
    monkeypatch.setitem(FR._STATE, "owner", _t.SimpleNamespace(
        cache=c, sport_ids=[2]))
    out = asyncio.run(FR._discovery_once(_RowsPool(rows)))
    assert out["states"][PD.MATCHED] == 1, out["receipt_sample"]
    assert regs == []
    assert out["registered"] == {FR.R_NOT_SEEDED_NO_PRICED_MARKET: 1}
