"""LIVE GAME STATE: THE HELD FIXTURE IS THE VENUE'S OWN TWO TEAMS (RC6 lane G2).

Requirement register F3: the Live Game State census was expected to show 0
established fixtures because the canonical adapter read premap keys
(home_team / away_team / league / sport) that us_premap has never carried.
Production (research-sql run 37880110851): us_premap has 22 columns and none
of those four; what every catalogue row DOES carry is the venue team object
of its side -- team_id, team_name, team_safe_name, team_abbr, team_league --
plus the market's game_start and sports_type. Every candidate event of the
next 48 h in a score league, and every event a PAPER account ever held, names
exactly two team ids with one league code and one start (runs 37880110851
section 4, 37880299272 section 5).

The fixed adapter establishes a provider-neutral fixture identity from those
rows: (venue, event slug, league from the venue's own code, the two venue
participants, the one start), with home/away left UNSTATED because the venue
does not state it -- the score provider's own homeAway is what a binding
records. These tests pin, with the production row shapes:

  * the shapes now ESTABLISH (they refused CANONICAL_SCORE_FIXTURE_FIELDS_
    MISSING on the base), and every way they can be wrong is a named refusal
  * league codes are read only where production shows that sport: NCAA
    soccer (`ncaams` / `ncaaws`) is NOT basketball, Indonesian soccer is not
    a score league, a code on the wrong sport family refuses
  * matching a provider game stays FULL-NAME equality against a name the
    venue states for that team (team_name or team_safe_name), in either
    provider orientation, with a unique assignment; a city or a nickname
    alone still matches nothing
  * the census SQL reaches the collector's verdict on every new shape, and
    the real collector binds a provider game whose home is the venue's
    second team without settling, ordering or authorizing anything
  * nothing reads a title, slug or question; flags stay OFF

Synthetic provider bodies here are TEST DATA, never live scores.
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest

from sportsassets.live_game_state import core as C
from sportsassets.live_game_state import storage as S
from sportsassets.live_game_state.collector import Collector
from sportsassets.live_game_state.providers import Reply, ScoreClient
from sportsassets.live_game_state.storage import PostgresStore, fixture_from_row
from tests import paper_harness as H
from tests.test_trader_live_game_real_db import (CENSUS_SQL, _census_statements,
                                                 _read)
from tests.test_trader_mode_real_db import _fill

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
T = H.T0
START = 1792800000.0   # 2026-10-23T00:00:00Z (a fixed synthetic kickoff)


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


# The production NFL event shape (research-sql run 37880299272 section 3:
# team 53 'chicago bears' / safe 'chi bears' / 'chi'; team 69 'new england
# patriots' / 'ne patriots' / 'ne'; team_league 'nfl'; football_* types).
BEARS = {"team_id": 53, "team_name": "chicago bears", "team_safe_name": "chi bears",
         "team_abbr": "chi", "team_league": "nfl"}
PATS = {"team_id": 69, "team_name": "new england patriots",
        "team_safe_name": "ne patriots", "team_abbr": "ne", "team_league": "nfl"}


def _tuple(team, *, start=START, family="football"):
    return dict(team, game_start=_iso(start), sport_family=family)


def _row(event="nfl-chi-ne-2026-10-23", teams=None, *, start=START, fm=None,
         market="asc-nfl-chi-ne-2026-10-23-pos-3pt5", extra_premap=None, tuples=None):
    teams = [_tuple(BEARS, start=start), _tuple(PATS, start=start)] if teams is None else teams
    premap = {"identifier": market, "market_slug": market, "event_slug": event,
              "event_title": "Bears vs. Patriots", "question": "Will the Bears cover -3.5?",
              "kind": "side", "side_norm": "yes", "team_id": 53, "team_name": "chicago bears",
              "team_safe_name": "chi bears", "team_abbr": "chi", "team_league": "nfl",
              "game_start": _iso(start), "sports_type": "football_team_full_game_spread",
              "listing_state": "PREGAME"}
    premap.update(extra_premap or {})
    return {"us_market_slug": market, "premap": premap, "fixture_metadata": fm,
            "event_teams": teams, "event_team_tuples": len(teams) if tuples is None else tuples}


def _refusal(row):
    with pytest.raises(C.ScoreError) as e:
        fixture_from_row(row)
    return str(e.value)


# ------------------------------------------------- the adapter, pure

def test_the_production_nfl_shape_establishes_a_provider_neutral_identity():
    f = fixture_from_row(_row())
    assert (f.venue, f.event_id, f.league) == ("POLYMARKET_US", "nfl-chi-ne-2026-10-23", "NFL")
    assert f.orientation == C.ORIENTATION_PROVIDER
    # participants in venue team-id order (53 before 69); NOT a home claim
    assert (f.home, f.away) == ("chicago bears", "new england patriots")
    assert f.home_names == ("chi bears",) and f.away_names == ("ne patriots",)
    assert f.start_at == START
    # the identity does not depend on which held market of the event it came from
    other = fixture_from_row(_row(market="aec-nfl-chi-ne-2026-10-23"))
    assert other.fingerprint == f.fingerprint and other.evidence_id != f.evidence_id


def test_participant_order_is_the_venue_team_id_order_not_the_row_order():
    a = fixture_from_row(_row(teams=[_tuple(PATS), _tuple(BEARS)]))
    b = fixture_from_row(_row())
    assert (a.home, a.away, a.fingerprint) == (b.home, b.away, b.fingerprint)


def test_nothing_is_read_from_a_title_slug_or_question():
    # an event whose rows carry no team object: the title, the slug and the
    # question all name two teams, and nothing is established from them
    row = _row(teams=[], extra_premap={"team_id": None, "team_name": None,
                                       "team_league": None})
    assert _refusal(row) == "CANONICAL_SCORE_FIXTURE_FIELDS_MISSING"


@pytest.mark.parametrize("code,family,league", [
    ("nfl", "football", "NFL"), ("cfb", "football", "NCAAF"), ("mlb", "baseball", "MLB"),
    ("nba", "basketball", "NBA"), ("wnba", "basketball", "WNBA"), ("nhl", "hockey", "NHL"),
    ("epl", "soccer", "EPL"), ("mls", "soccer", "MLS"), ("ucl", "soccer", "UCL")])
def test_every_listed_venue_code_names_its_league_on_its_own_sport(code, family, league):
    teams = [_tuple(dict(BEARS, team_league=code), family=family),
             _tuple(dict(PATS, team_league=code), family=family)]
    assert fixture_from_row(_row(teams=teams)).league == league


@pytest.mark.parametrize("code", ["ncaams", "ncaaws", "idnsl", "cbb", "ufc", "atp"])
def test_a_code_production_does_not_show_as_a_score_league_is_unsupported(code):
    # ncaams / ncaaws are NCAA men's / women's SOCCER on the venue (run
    # 37880299272 section 1: soccer rows only), never NCAAB / NCAAW
    teams = [_tuple(dict(BEARS, team_league=code), family="soccer"),
             _tuple(dict(PATS, team_league=code), family="soccer")]
    assert _refusal(_row(teams=teams)) == "SCORE_LEAGUE_UNSUPPORTED"


def test_the_production_held_market_today_is_an_unsupported_league():
    # research-sql run 37880110851 section 2/3: the one held market,
    # idnsl-pke-mau-2026-10-09 (Indonesian Liga 1), two team ids, one start
    t0 = 1791534600.0
    mau = {"team_id": 36224, "team_name": "madura united", "team_safe_name": "madura united",
           "team_abbr": "mau", "team_league": "idnsl"}
    pke = {"team_id": 36226, "team_name": "persik kediri", "team_safe_name": "persik kediri",
           "team_abbr": "pke", "team_league": "idnsl"}
    row = _row("idnsl-pke-mau-2026-10-09",
               [_tuple(mau, start=t0, family="soccer"), _tuple(pke, start=t0, family="soccer")],
               start=t0, market="atc-idnsl-pke-mau-2026-10-09-pke",
               extra_premap={"team_league": "idnsl", "sports_type": "soccer_team_full_time_winner"})
    assert _refusal(row) == "SCORE_LEAGUE_UNSUPPORTED"


def test_a_league_code_on_another_sports_rows_is_refused():
    teams = [_tuple(dict(BEARS, team_league="nba"), family="soccer"),
             _tuple(dict(PATS, team_league="nba"), family="soccer")]
    assert _refusal(_row(teams=teams)) == "CANONICAL_VENUE_SPORT_FAMILY_MISMATCH"
    # a team row with no sportsMarketType states no family: alone it cannot
    # establish one
    teams = [_tuple(BEARS, family=""), _tuple(PATS, family="")]
    assert _refusal(_row(teams=teams)) == "CANONICAL_VENUE_SPORT_FAMILY_MISMATCH"
    # beside a stated family it does not contradict it
    teams = [_tuple(BEARS), _tuple(PATS), _tuple(BEARS, family="")]
    assert fixture_from_row(_row(teams=teams)).league == "NFL"


def test_not_two_participants_is_refused_never_truncated_into_a_pair():
    futures = [_tuple(dict(BEARS, team_id=50 + i, team_name="team %d" % i,
                           team_safe_name="team %d" % i)) for i in range(12)]
    assert _refusal(_row(teams=futures)) == "CANONICAL_VENUE_PARTICIPANTS_NOT_TWO"
    assert _refusal(_row(teams=[_tuple(BEARS)])) == "CANONICAL_VENUE_PARTICIPANTS_NOT_TWO"
    # more tuples than the adapter reads: refused, even if the read ones are two
    assert _refusal(_row(tuples=C.MAX_EVENT_TEAM_TUPLES + 1)) == \
        "CANONICAL_VENUE_PARTICIPANTS_NOT_TWO"


def test_one_team_under_two_names_or_no_name_is_refused():
    teams = [_tuple(BEARS), _tuple(PATS), _tuple(dict(BEARS, team_name="chicago cubs"))]
    assert _refusal(_row(teams=teams)) == "CANONICAL_VENUE_PARTICIPANT_NAME_UNPROVEN"
    teams = [_tuple(dict(BEARS, team_name=None, team_safe_name="  ")), _tuple(PATS)]
    assert _refusal(_row(teams=teams)) == "CANONICAL_VENUE_PARTICIPANT_NAME_UNPROVEN"


def test_the_team_rows_must_state_one_league_and_one_start():
    teams = [_tuple(BEARS), _tuple(dict(PATS, team_league="cfb"))]
    assert _refusal(_row(teams=teams)) == "CANONICAL_VENUE_LEAGUE_NOT_ONE"
    teams = [_tuple(BEARS), _tuple(PATS, start=START + 3600)]
    assert _refusal(_row(teams=teams)) == "CANONICAL_EVENT_START_NOT_ONE_INSTANT"
    # the held row's own game_start must be the event's
    assert _refusal(_row(extra_premap={"game_start": _iso(START + 60)})) == \
        "CANONICAL_EVENT_START_NOT_ONE_INSTANT"


def test_one_participants_venue_name_naming_the_other_is_refused():
    teams = [_tuple(dict(BEARS, team_safe_name="new england patriots")), _tuple(PATS)]
    assert _refusal(_row(teams=teams)) == "CANONICAL_PARTICIPANTS_INVALID"


def test_an_explicit_fixture_row_keeps_its_home_away_and_its_old_identity():
    fm = {"venue": "PMUS", "venue_fixture_key": "event:nfl-chi-ne-2026-10-23",
          "competition": "NFL", "home_team": "New England Patriots",
          "away_team": "Chicago Bears", "orientation": "HOME_AWAY", "source": "TEST"}
    f = fixture_from_row(_row(fm=fm))
    assert f.orientation == C.ORIENTATION_VENUE
    assert (f.home, f.away, f.home_names, f.away_names) == (
        "New England Patriots", "Chicago Bears", (), ())
    # the fingerprint formula of a venue-oriented fixture is the package's
    assert f.fingerprint == C.digest({
        "venue": f.venue, "event_id": f.event_id, "league": "NFL",
        "home": "new england patriots", "away": "chicago bears",
        "start_at": START, "game_number": None})


# ------------------------------------------------- matching, pure

def _espn_nfl(home, away, *, start=START, eid="401777001", state="STATUS_IN_PROGRESS",
              home_score="17", away_score="10"):
    """A synthetic ESPN-shaped NFL scoreboard (TEST DATA, not a live game)."""
    iso = _iso(start)

    def team(tid, name):
        return {"id": tid, "displayName": name, "abbreviation": name[:3].upper()}
    return {"leagues": [{"slug": "nfl"}], "events": [{
        "id": eid, "date": iso, "competitions": [{
            "id": eid, "date": iso, "competitors": [
                {"homeAway": "home", "score": home_score, "team": team("17", home)},
                {"homeAway": "away", "score": away_score, "team": team("3", away)}],
            "status": {"period": 3, "clock": 312, "displayClock": "5:12",
                       "type": {"name": state, "state": "in", "shortDetail": "5:12 - 3rd"}}}]}]}


def _games(body, league="NFL", at=START + 3600):
    return C.parse_espn(body, league=league, received_at=at, observed_at=at,
                        payload_hash="TEST_PAYLOAD_HASH")


def test_a_venue_pair_binds_the_provider_game_in_either_orientation():
    f = fixture_from_row(_row())
    for home, away, venue_home in (("New England Patriots", "Chicago Bears", "new england patriots"),
                                   ("Chicago Bears", "New England Patriots", "chicago bears")):
        [g] = _games(_espn_nfl(home, away))
        got, why, _ = C.match_fixture(f, [g])
        assert got is g, why
        raw = C.bind_game(f, g)
        ident = raw["identity"]
        # home / away recorded are the PROVIDER's, on the venue's names
        assert ident["home"] == venue_home
        assert ident["venue_orientation"] == C.ORIENTATION_PROVIDER
        assert ident["home_away_source"] == "ESPN"
        assert ident["basis"] == "EXACT_LEAGUE_PARTICIPANT_PAIR_START"
        d = C.display(raw, venue=f.venue, event_id=f.event_id, now=START + 3600)
        assert d["status"] == "CURRENT" and d["home"] == home
        assert (d["home_score"], d["away_score"]) == (17, 10)
        assert d["execution_authority"] is False


def test_a_city_or_a_nickname_alone_still_matches_nothing():
    # production NBA naming (run 37880299272 section 3): team_name 'thunder',
    # team_safe_name 'oklahoma city' -- neither is the provider's full name
    thunder = {"team_id": 27, "team_name": "thunder", "team_safe_name": "oklahoma city",
               "team_abbr": "okc", "team_league": "nba"}
    nuggets = {"team_id": 40, "team_name": "nuggets", "team_safe_name": "denver",
               "team_abbr": "den", "team_league": "nba"}
    f = fixture_from_row(_row("nba-den-okc-2026-10-23",
                              [_tuple(thunder, family="basketball"),
                               _tuple(nuggets, family="basketball")]))
    assert f.league == "NBA"
    body = _espn_nfl("Oklahoma City Thunder", "Denver Nuggets")
    body["leagues"] = [{"slug": "nba"}]
    [g] = _games(body, league="NBA")
    assert C.match_fixture(f, [g]) == (None, "SCORE_FIXTURE_NOT_MATCHED", [])
    # an explicit, evidence-backed alias is the only bridge, and it is cited
    a = C.Aliases([
        {"league": "NBA", "canonical": "Oklahoma City Thunder", "alias": "oklahoma city",
         "evidence_id": "TEST_ALIAS_OKC"},
        {"league": "NBA", "canonical": "Denver Nuggets", "alias": "denver",
         "evidence_id": "TEST_ALIAS_DEN"}])
    got, _, refs = C.match_fixture(f, [g], a)
    assert got is g and refs == ["TEST_ALIAS_DEN", "TEST_ALIAS_OKC"]


def test_the_venue_safe_name_is_a_full_name_the_provider_can_equal():
    # production NHL naming: team_name 'wild', team_safe_name 'minnesota wild'
    wild = {"team_id": 1506, "team_name": "wild", "team_safe_name": "minnesota wild",
            "team_abbr": "min", "team_league": "nhl"}
    sens = {"team_id": 1488, "team_name": "senators", "team_safe_name": "ottawa senators",
            "team_abbr": "ott", "team_league": "nhl"}
    f = fixture_from_row(_row("nhl-ott-min-2026-10-23",
                              [_tuple(wild, family="hockey"), _tuple(sens, family="hockey")]))
    body = _espn_nfl("Minnesota Wild", "Ottawa Senators")
    body["leagues"] = [{"slug": "nhl"}]
    [g] = _games(body, league="NHL")
    raw = C.bind_game(f, g)
    assert raw["identity"]["home"] == "wild" and raw["identity"]["away"] == "senators"


def test_one_team_matching_or_a_wrong_start_binds_nothing():
    f = fixture_from_row(_row())
    [g] = _games(_espn_nfl("New England Patriots", "Chicago Bulls"))
    assert C.match_fixture(f, [g])[0] is None
    [g] = _games(_espn_nfl("New England Patriots", "Chicago Bears", start=START + 3 * 3600))
    assert C.match_fixture(f, [g])[0] is None
    # two provider games with the same pair at the same start: ambiguous, not nearest
    [g1] = _games(_espn_nfl("New England Patriots", "Chicago Bears"))
    [g2] = _games(_espn_nfl("Chicago Bears", "New England Patriots", eid="401777002"))
    assert C.match_fixture(f, [g1, g2])[1] == "AMBIGUOUS_SCORE_FIXTURE"


def test_a_venue_oriented_fixture_still_refuses_the_reversed_game():
    f = C.Fixture("POLYMARKET_US", "e", "NFL", "New England Patriots", "Chicago Bears",
                  START, "TEST")
    [g] = _games(_espn_nfl("Chicago Bears", "New England Patriots"))
    assert C.match_fixture(f, [g])[0] is None


def test_the_binding_cannot_be_reoriented_after_the_fact():
    f = fixture_from_row(_row())
    [g] = _games(_espn_nfl("New England Patriots", "Chicago Bears"))
    raw = C.bind_game(f, g)
    raw["identity"]["home"], raw["identity"]["away"] = raw["identity"]["away"], raw["identity"]["home"]
    d = C.display(raw, venue=f.venue, event_id=f.event_id, now=START + 3600)
    assert d["status"] == "UNAVAILABLE"
    assert d["why"] == "SCORE_BINDING_DIGEST_OR_DIMENSION_MISMATCH"


def test_a_fixture_refuses_an_unknown_orientation():
    with pytest.raises(C.ScoreError):
        C.Fixture("POLYMARKET_US", "e", "NFL", "a", "b", START, "TEST", orientation="GUESSED")


# ------------------------------------------------- real database

async def _venue_rows(conn, event, teams, *, start, held=(), family="football"):
    """Production-shaped us_premap rows for one venue event: per team a
    moneyline side and a spread side, a team-less total, and the held
    markets (slug, team or None)."""
    rows = []
    for t in teams:
        rows.append(("aec-%s" % event, t["team_abbr"], t, "%s_team_full_game_winner" % family))
        rows.append(("asc-%s-%s" % (event, t["team_abbr"]), "yes", t,
                     "%s_team_full_game_spread" % family))
    rows.append(("tsc-%s-total" % event, "over", None, "%s_game_full_game_total" % family))
    for slug, t in held:
        rows.append((slug, "yes", t, "%s_team_full_game_spread" % family))
    for ident, side, t, stype in rows:
        t = t or {}
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, event_title, market_slug,"
            " question, kind, side_norm, team_id, team_name, team_safe_name, team_abbr,"
            " team_league, game_start, sports_type, listing_state, listing_state_source,"
            " listing_pass) VALUES ($1, $2, $3, $1, $4, 'side', $5, $6, $7, $8, $9, $10,"
            " to_timestamp($11), $12, 'PREGAME', 'SCHEDULE_ESTIMATE', 'WINDOW')",
            ident, event, "TEST TITLE NAMING NOBODY", "TEST QUESTION", side,
            t.get("team_id"), t.get("team_name"), t.get("team_safe_name"),
            t.get("team_abbr"), t.get("team_league"), start, stype)


class _Board:
    """A provider transport serving one synthetic ESPN scoreboard. No network."""

    def __init__(self, body, clock):
        self.body, self.clock, self.calls = body, clock, []

    async def get(self, url, *, params=None):
        self.calls.append((url, dict(params or {})))
        now = self.clock()
        return Reply(self.body, 200, now, now, "TEST_HASH", {})

    async def close(self):
        pass


@pg
async def test_the_real_collector_establishes_and_binds_a_venue_pair():
    conn = await H.connect()
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)
    client = None
    try:
        a = await H.new_account(conn, "lgsg2a")
        acct, tag = a["account_id"], uuid.uuid4().hex[:10]
        event = "nfl-g2-%s" % tag
        s1, s2 = "%s:spread" % acct, "%s:ml" % acct
        await _fill(conn, a, slug=s1, group="paper_g_%s_1" % tag, key="e1", at=T + 1)
        await _fill(conn, a, slug=s2, group="paper_g_%s_2" % tag, key="e2", at=T + 2)
        start = T + 86400
        await _venue_rows(conn, event, [BEARS, PATS], start=start,
                          held=((s1, BEARS), (s2, PATS)))

        store = PostgresStore(pool)
        fixtures, census = await store.fixtures(account_id=acct)
        assert census["missing_by_reason"] == {}, census
        assert census["established_fixtures"] == 1
        assert census["established_by_orientation"] == {
            C.ORIENTATION_VENUE: 0, C.ORIENTATION_PROVIDER: 1}
        [f] = fixtures
        assert (f.event_id, f.league, f.home, f.away, f.start_at) == (
            event, "NFL", "chicago bears", "new england patriots", start)

        # the census file reaches the same verdict, identity and orientation
        stmts = _census_statements(acct)
        detail = await conn.fetch(stmts[1])
        assert {r["us_market_slug"]: r["collector_verdict"] for r in detail} == {
            s1: "ESTABLISHED", s2: "ESTABLISHED"}
        for r in detail:
            assert (r["collector_league"], r["collector_home"], r["collector_away"],
                    r["collector_orientation"], r["collector_league_basis"]) == (
                "NFL", "chicago bears", "new england patriots", "PROVIDER_HOME_AWAY",
                "VENUE_TEAM_LEAGUE_CODE")
            assert r["collector_start"].timestamp() == start
            assert list(r["collector_home_names"]) == ["chi bears", "chicago bears"]
            assert r["primary_source_request"].startswith("ESPN football/nfl scoreboard dates=")

        # the REAL collector: the provider's home is the venue's SECOND team
        clock = lambda: start + 3600                                 # noqa: E731
        board = _Board(_espn_nfl("New England Patriots", "Chicago Bears", start=start), clock)
        client = ScoreClient(board, clock=clock)
        report = await Collector(client, store, clock=clock).cycle(fixtures, census=census)
        assert report["successful"] == 1, report
        assert report["execution_authority"] is False
        assert [u for u, _ in board.calls] == [
            "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"]
        payload = json.loads(await conn.fetchval(
            "SELECT payload::text FROM trader_display_score_latest WHERE venue=$1 AND event_id=$2",
            "POLYMARKET_US", event))
        assert payload["identity"]["home"] == "new england patriots"
        assert payload["identity"]["venue_orientation"] == "PROVIDER_HOME_AWAY"
        snap = await _read(pool, acct, start + 3600, display="on")
        games = {p["market_id"]: p["game"] for p in snap["positions"]}
        assert set(games) == {s1, s2}
        for g in games.values():
            assert g["status"] == "CURRENT" and g["home"] == "New England Patriots"
            assert (g["home_score"], g["away_score"]) == (17, 10)
        # display only: the positions are as open as before, nothing settled
        assert all(p["state"] == "ACTIVE" for p in snap["positions"])
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_settlements WHERE position_key LIKE $1",
            "paperpos:" + acct + ":%") == 0
    finally:
        if client:
            await client.close()
        await pool.close()
        await conn.close()


@pg
async def test_the_census_reaches_the_collectors_verdict_on_every_venue_shape():
    conn = await H.connect()
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)
    try:
        a = await H.new_account(conn, "lgsg2c")
        acct, tag = a["account_id"], uuid.uuid4().hex[:10]
        start = T + 86400
        shapes, n = {}, 0

        async def held(name, event, teams, *, verdict, family="football", t0=start,
                       held_team="first"):
            nonlocal n
            n += 1
            slug = "%s:%s" % (acct, name)
            await _fill(conn, a, slug=slug, group="paper_g_%s_%s" % (tag, name),
                        key="e%d" % n, at=T + n)
            ev = "g2-%s-%s" % (tag, event)
            team = teams[0] if (teams and held_team == "first") else None
            await _venue_rows(conn, ev, teams, start=t0, held=((slug, team),), family=family)
            shapes[slug] = verdict
            return slug, ev

        def team(i, name, safe, code):
            return {"team_id": 9000 + i, "team_name": name, "team_safe_name": safe,
                    "team_abbr": "t%d" % i, "team_league": code}
        # established: NFL, NCAAF (school names), WNBA (city names), NBA
        # (nickname + city: an identity, whatever a provider later matches)
        await held("nfl", "nfl", [BEARS, PATS], verdict="ESTABLISHED")
        await held("cfb", "cfb", [team(1, "sam houston", "sam houston", "cfb"),
                                  team(2, "liberty", "liberty", "cfb")], verdict="ESTABLISHED")
        await held("wnba", "wnba", [team(3, "las vegas", "las vegas", "wnba"),
                                    team(4, "golden state", "golden state", "wnba")],
                   family="basketball", verdict="ESTABLISHED")
        await held("nba", "nba", [team(5, "thunder", "oklahoma city", "nba"),
                                  team(6, "nuggets", "denver", "nba")],
                   family="basketball", verdict="ESTABLISHED")
        # a held market whose own row has no team (a total): the event's teams
        await held("tot", "tot", [team(7, "boston bruins", "boston bruins", "nhl"),
                                  team(8, "new york rangers", "new york rangers", "nhl")],
                   family="hockey", held_team=None, verdict="ESTABLISHED")
        # unsupported codes, including NCAA soccer, which is not basketball
        await held("idnsl", "idnsl", [team(9, "madura united", "madura united", "idnsl"),
                                      team(10, "persik kediri", "persik kediri", "idnsl")],
                   family="soccer", verdict="SCORE_LEAGUE_UNSUPPORTED")
        await held("ncaaws", "ncaaws", [team(11, "yale bulldogs", "yale bulldogs", "ncaaws"),
                                        team(12, "brown bears", "brown bears", "ncaaws")],
                   family="soccer", verdict="SCORE_LEAGUE_UNSUPPORTED")
        await held("fam", "fam", [team(13, "a fc", "a fc", "nba"), team(14, "b fc", "b fc", "nba")],
                   family="soccer", verdict="CANONICAL_VENUE_SPORT_FAMILY_MISMATCH")
        await held("three", "three", [team(15, "x one", "x one", "nfl"),
                                      team(16, "x two", "x two", "nfl"),
                                      team(17, "x three", "x three", "nfl")],
                   verdict="CANONICAL_VENUE_PARTICIPANTS_NOT_TWO")
        await held("lg", "lg", [team(18, "y one", "y one", "nfl"),
                                team(19, "y two", "y two", "cfb")],
                   verdict="CANONICAL_VENUE_LEAGUE_NOT_ONE")
        await held("dup", "dup", [team(20, "z one", "z two", "nfl"),
                                  team(21, "z two", "z three", "nfl")],
                   verdict="CANONICAL_PARTICIPANTS_INVALID")
        # one team id under two names (two rows of the same id disagree)
        s, ev = await held("nm", "nm", [team(22, "w one", "w one", "nfl"),
                                        team(23, "w two", "w two", "nfl")],
                           verdict="CANONICAL_VENUE_PARTICIPANT_NAME_UNPROVEN")
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, market_slug, side_norm, team_id,"
            " team_name, team_safe_name, team_league, game_start, sports_type)"
            " VALUES ($1, $2, $1, 'no', 9022, 'w renamed', 'w one', 'nfl',"
            " to_timestamp($3), 'football_team_full_game_spread')",
            "asc-%s-renamed" % ev, ev, start)
        # a held row whose own start is not the event's
        s, ev = await held("st", "st", [team(24, "v one", "v one", "nfl"),
                                        team(25, "v two", "v two", "nfl")],
                           verdict="CANONICAL_EVENT_START_NOT_ONE_INSTANT")
        await conn.execute("UPDATE us_premap SET game_start = to_timestamp($2) "
                           "WHERE identifier = $1", s, start + 7200)
        # the base's production shape: a premap row with no team object
        slug = "%s:bare" % acct
        n += 1
        await _fill(conn, a, slug=slug, group="paper_g_%s_bare" % tag, key="e%d" % n, at=T + n)
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, market_slug, side_norm,"
            " game_start, team_league) VALUES ($1, $2, $1, 'yes', to_timestamp($3), 'nfl')",
            slug, "g2-%s-bare" % tag, start)
        shapes[slug] = "CANONICAL_SCORE_FIXTURE_FIELDS_MISSING"

        fixtures, census = await PostgresStore(pool).fixtures(account_id=acct)
        stmts = _census_statements(acct)
        assert len(stmts) == 5
        results = [await conn.fetch(st) for st in stmts]
        detail, by_league, avail = results[1], results[2], results[3][0]
        got = {r["us_market_slug"]: r["collector_verdict"] for r in detail}
        assert got == shapes
        # the collector: the same refusals, by name and count
        want = collections.Counter(v for v in shapes.values() if v != "ESTABLISHED")
        assert census["missing_by_reason"] == dict(want)
        established = sorted(r["event_id"] for r in detail if r["collector_verdict"] == "ESTABLISHED")
        assert sorted(f.event_id for f in fixtures) == established
        assert census["established_fixtures"] == len(established) == 5
        by_event = {r["event_id"]: r for r in detail}
        for f in fixtures:
            r = by_event[f.event_id]
            assert (r["collector_league"], r["collector_home"], r["collector_away"]) == (
                f.league, f.home, f.away)
            assert sorted(r["collector_home_names"]) == sorted(f.names("home"))
        assert {r["league"] for r in by_league if r["collector_verdict"] == "SCORE_LEAGUE_UNSUPPORTED"} \
            == {"UNSUPPORTED (venue team_league=idnsl)", "UNSUPPORTED (venue team_league=ncaaws)"}
        assert sum(r["markets"] for r in by_league) == len(shapes)
        assert avail["event_names_two_venue_team_ids"] >= 5
        assert avail["premap_has_home_away_keys"] == 0
    finally:
        await pool.close()
        await conn.close()


def test_the_census_file_names_every_refusal_the_adapter_can_raise():
    src = CENSUS_SQL.read_text()
    adapter = pathlib.Path(S.__file__).read_text()
    codes = set(re.findall(r'ScoreError\("([A-Z_]+)"\)', adapter))
    codes |= {"CANONICAL_PARTICIPANTS_INVALID", "CANONICAL_FIXTURE_EVIDENCE_MISSING",
              "GAME_NUMBER_INVALID", "CONFLICTING_CANONICAL_FIXTURE_ROWS"}
    fixture_codes = {"CANONICAL_VENUE_EVENT_MISSING", "CANONICAL_SCORE_FIXTURE_FIELDS_MISSING",
                     "CANONICAL_FIXTURE_JOIN_MISMATCH", "CANONICAL_HOME_AWAY_UNPROVEN",
                     "CANONICAL_VENUE_PARTICIPANTS_NOT_TWO",
                     "CANONICAL_VENUE_PARTICIPANT_NAME_UNPROVEN",
                     "CANONICAL_VENUE_LEAGUE_NOT_ONE", "CANONICAL_EVENT_START_NOT_ONE_INSTANT",
                     "SCORE_LEAGUE_UNSUPPORTED", "CANONICAL_VENUE_SPORT_FAMILY_MISMATCH"}
    assert fixture_codes <= codes
    body = "\n".join(re.sub(r"--.*$", "", ln) for ln in src.splitlines())
    for code in fixture_codes | {"CANONICAL_PARTICIPANTS_INVALID", "GAME_NUMBER_INVALID",
                                 "CONFLICTING_CANONICAL_FIXTURE_ROWS",
                                 "CANONICAL_FIXTURE_EVIDENCE_MISSING"}:
        assert "'%s'" % code in body, code
    # the SQL's venue code table is the adapter's
    sql_codes = dict(re.findall(r"\('([a-z0-9]+)', '([A-Z]+)'\)",
                                body.split("venue_codes (code, league) AS (VALUES")[1].split(")\n")[0] + ")"))
    assert sql_codes == C.VENUE_LEAGUE_CODES["POLYMARKET_US"]
