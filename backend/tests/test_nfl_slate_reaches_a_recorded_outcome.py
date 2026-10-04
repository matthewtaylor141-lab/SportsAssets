"""THE NFL IS VISIBLE: EVERY GAME OF THE 2026-10-04 SLATE ENDS IN A RECORDED,
NAMED OUTCOME (cand24).

THE PRODUCTION GAP (research-sql cand24_nfl_discover.sql, 2026-10-04T04:26Z).
The venue listed 14 `nfl` games with a `football_team_full_game_winner`
contract on the America/New_York day -- IND Colts vs. WAS Commanders in London
at 13:30Z (09:30 ET) through DET Lions vs. CAR Panthers at 00:20Z on 10-05
(20:20 ET on 10-04) -- and the collector had never requested
`americanfootball_nfl`: the football board's `nfl` token was deliberately
unmapped, so no provider fetch, identity, valuation, decision or refusal
existed for any of them. Zero visibility, not a refusal.

WHAT THIS FILE PINS:

  1 ONE LEAGUE IDENTITY: `nfl` <-> americanfootball_nfl <-> "NFL", read from
    ext_pinnacle_loop.provider_key_for_venue_token by the cycle, the census
    and coverage_integrity.
  2 THE BUDGET IS UNCHANGED (4 metered keys) and spent by venue coverage; on
    today's board MLB (confirmed) + NCAAF + UNL + NFL are requested and Brazil
    Serie B -- 8 events, none in the next 24 h -- is the one dropped, by name.
  3 IDENTITY = CITY/REGION + NICKNAME (bettor_venue_native_identity.NFL_TEAMS):
    LA Rams / LA Chargers and NY Giants / NY Jets never cross-map; TB, NE,
    WAS, LV, SF/49ers renderings resolve to their own team only; home/away and
    the YES (LONG) / NO (SHORT) row are read off the venue's own rows; the
    London game's UTC/ET dating; a game in progress keeps its identity.
  4 SETTLEMENT from the two captured documents: overtime agrees (established);
    the venue's postponement rule CONFLICTS with the book's void rule, and the
    venue's $0.50 tie payout has no counterpart in the book's captured terms --
    both stay refused BY NAME. Football stays out of the de-vig set.
  5 THE REAL CYCLE on a 14-game fixture built from today's production rows:
    every game ends in a persisted valuation and a recorded paper decision
    with a named refusal; an unlisted provider game stops at identity by name;
    the ledger reconciles to the fetch. No threshold, gate or cap is changed.

Substituted only at transport boundaries (provider catalogue/odds, venue book
and rules reads, paper market data). Prices and books are SYNTHETIC; the venue
rows and the rules prose are production's.
"""
from __future__ import annotations

import datetime as _dt
import json
import time
from pathlib import Path

import pytest

from sportsassets import bettor_paper_session as S
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import bettor_settlement_terms as T
from sportsassets import bettor_venue_native_identity as V
from sportsassets import bettor_venue_settlement as vset
from sportsassets.agents import coverage as COV
from sportsassets.agents import coverage_integrity as CI
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from sportsassets.agents import runtime as RT
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

NFL = "americanfootball_nfl"
NCAAF = "americanfootball_ncaaf"
LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"
FIX = Path(__file__).parent / "fixtures"
SLATE = json.loads((FIX / "nfl_slate_2026_10_04.json").read_text())
LISTING = json.loads((FIX / "pmus_nfl_listing_2026_10_04.json").read_text())
LONDON = _dt.datetime(2026, 10, 4, 13, 30, tzinfo=_dt.timezone.utc)


def _iso(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(iso: str) -> float:
    return _dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _rows(game, kickoff, *, day="2026-10-04"):
    """The two production us_premap rows of one game (away LONG, home SHORT),
    with `kickoff` as game_start and the slug dated `day`."""
    ev = "nfl-%s-%s-%s" % (game["away_venue"]["team_abbr"],
                           game["home_venue"]["team_abbr"], day)
    out = []
    for side in ("away_venue", "home_venue"):
        v = game[side]
        out.append({"market_slug": "aec-" + ev, "identifier": "aec-" + ev,
                    "intent": v["intent"], "event_slug": ev,
                    "event_title": game["venue_title"],
                    "question": game["venue_title"], "kind": "side",
                    "sports_type": "football_team_full_game_winner",
                    "team_abbr": v["team_abbr"], "team_name": v["team_name"],
                    "side_norm": v["side_norm"], "line": game["line"],
                    "game_start": kickoff})
    return out


def _all_rows(base: _dt.datetime):
    rows = []
    for g in SLATE["games"]:
        k = base + _dt.timedelta(seconds=_epoch(g["kickoff_utc"])
                                 - LONDON.timestamp())
        rows.extend(_rows(g, k))
    return rows


def _match(home, away, kickoff, rows=None, *, tokens=("nfl",)):
    return V.match_event(home=home, away=away,
                         commence_epoch=kickoff.timestamp(), family="football",
                         rows=_all_rows(LONDON) if rows is None else rows,
                         competition=NFL, league_tokens=tokens)


def _kick(slug_pair):
    g = next(g for g in SLATE["games"]
             if g["event_slug"].startswith("nfl-%s-" % slug_pair))
    return g, _dt.datetime.fromisoformat(g["kickoff_utc"].replace("Z", "+00:00"))


# ═════════════════════════════════════════════════════════════════════
# 1 · THE EXPECTED UNIVERSE AND THE ONE LEAGUE IDENTITY
# ═════════════════════════════════════════════════════════════════════

def test_the_expected_slate_is_fourteen_games_on_the_et_day():
    games = SLATE["games"]
    assert SLATE["count"] == len(games) == 14
    assert len({g["market_slug"] for g in games}) == 14
    assert {g["et_day"] for g in games} == {"2026-10-04"}
    # the Sunday-night game is on the ET day and NOT on the UTC day
    utc = [g for g in games if g["utc_day"] == "2026-10-04"]
    assert len(utc) == 13
    snf = next(g for g in games if g["utc_day"] != "2026-10-04")
    assert snf["market_slug"] == "aec-nfl-det-car-2026-10-04"
    assert snf["kickoff_utc"] == "2026-10-05T00:20:00Z"
    assert snf["kickoff_et"] == "2026-10-04T20:20:00-0400"
    # the London game
    ldn = games[0]
    assert ldn["market_slug"] == "aec-nfl-ind-was-2026-10-04"
    assert (ldn["kickoff_utc"], ldn["kickoff_et"]) == (
        "2026-10-04T13:30:00Z", "2026-10-04T09:30:00-0400")
    assert (ldn["home"], ldn["away"]) == ("Washington Commanders",
                                          "Indianapolis Colts")


def test_nfl_is_one_league_everywhere():
    assert loop.provider_key_for_venue_token("nfl") == NFL
    assert loop.provider_key_for_venue_token("NFL") == NFL
    assert loop.venue_league_tokens(NFL) == ("nfl",)
    assert loop.venue_league_tokens(NCAAF) == ("cfb",)
    assert loop.family_for_provider_key(NFL) == "football"
    assert NFL in loop.provider_keys_for_family("football")
    assert CI.catalogue_token_map()["nfl"] == NFL
    assert CI.league_name(NFL) == "NFL"
    lg = COV.league_of_row({"event_slug": "nfl-ind-was-2026-10-04"})
    assert lg == {"token": "nfl", "provider_key": NFL, "lane_maps": True,
                  "league_name": "NFL"}
    # the census's requested-league mandate holds football to BOTH tokens
    m = COV.mandate([NCAAF, NFL])
    assert m["league_tokens_by_family"]["football"] == ["cfb", "nfl"]


# ═════════════════════════════════════════════════════════════════════
# 2 · THE BUDGET, UNCHANGED, ON TODAY'S MEASURED BOARD
# ═════════════════════════════════════════════════════════════════════

#: The boards the collector read at 2026-10-04T04:26Z (heartbeat
#: venue_football_board tokens and the N9 counts), and the provider
#: catalogue's verdicts recorded on the same heartbeat.
FOOTBALL_BOARD = {"board": [("cfb", 34), ("nfl", 15)],
                  "titles": {"cfb": ["Fresno State vs. Washington State"],
                             "nfl": [g["venue_title"]
                                     for g in SLATE["games"]]},
                  "title_days": {}}
SOCCER_BOARD = [("ncaaws", 95), ("ncaams", 46), ("unl", 26), ("intf", 18),
                ("arg2", 11), ("uslc", 10), ("brb", 8), ("lco", 7),
                ("cnl", 6), ("uru1", 5), ("nwsl", 5)]
CATALOGUE = {"ok": True, "sports": [{"key": k, "active": True} for k in (
    "baseball_mlb", NCAAF, NFL, "soccer_uefa_nations_league",
    "soccer_brazil_serie_b")]}


def test_the_budget_ranks_nfl_by_venue_coverage_and_drops_serie_b_by_name():
    assert loop.MAX_METERED_SPORTS_PER_CYCLE == 4
    fb = loop.football_candidates(FOOTBALL_BOARD)
    assert [(c["key"], c["our_token"]) for c in fb] == [(NCAAF, "cfb"),
                                                        (NFL, "nfl")]
    merged = loop.merge_candidates(loop.candidates_from_board(SOCCER_BOARD),
                                   fb)
    sel = loop.select_sports(CATALOGUE, candidates=merged)
    assert sel["sports"] == [("baseball_mlb", "baseball"), (NCAAF, "football"),
                             ("soccer_uefa_nations_league", "soccer"),
                             (NFL, "football")]
    assert [d["key"] for d in sel["budget_dropped"]] == \
        ["soccer_brazil_serie_b"]
    assert "budget" in sel["budget_dropped"][0]["why"]
    # MLB (the postseason) is the confirmed key: it can never be ranked out
    assert sel["sports"][0] == ("baseball_mlb", "baseball")
    # once the Saturday college slate has aged off the board (game_start
    # older than 6 h), the NFL outranks NCAAF's few listings
    later = dict(FOOTBALL_BOARD, board=[("nfl", 14), ("cfb", 3)])
    sel2 = loop.select_sports(CATALOGUE, candidates=loop.merge_candidates(
        loop.candidates_from_board(SOCCER_BOARD),
        loop.football_candidates(later)))
    assert [k for k, _ in sel2["sports"]] == [
        "baseball_mlb", "soccer_uefa_nations_league", NFL,
        "soccer_brazil_serie_b"]
    assert [d["key"] for d in sel2["budget_dropped"]] == [NCAAF]


# ═════════════════════════════════════════════════════════════════════
# 3 · IDENTITY: CITY/REGION + NICKNAME
# ═════════════════════════════════════════════════════════════════════

def test_shared_regions_never_cross_map():
    for pair, home, away, wrong in (
            ("lar-phi", "Philadelphia Eagles", "Los Angeles Rams",
             "Los Angeles Chargers"),
            ("lac-sea", "Seattle Seahawks", "Los Angeles Chargers",
             "Los Angeles Rams"),
            ("ari-nyg", "New York Giants", "Arizona Cardinals",
             "New York Jets"),
            ("nyj-chi", "Chicago Bears", "New York Jets", "New York Giants")):
        g, k = _kick(pair)
        m = _match(home, away, k)
        assert m["ok"], m
        assert m["us_market_slug"] == g["market_slug"]
        # the other team of the same region does not reach this contract
        bad = _match(home, wrong, k)
        assert not bad["ok"] or bad["us_market_slug"] != g["market_slug"]
        assert not bad["ok"], bad
    # the same-instant LA pair, side by side in ONE window: each price
    # finds its own contract and only its own
    t = LONDON
    rows = []
    for g in SLATE["games"]:
        if g["event_slug"].startswith(("nfl-lar-", "nfl-lac-")):
            rows.extend(_rows(g, t))
    a = _match("Philadelphia Eagles", "Los Angeles Rams", t, rows)
    b = _match("Seattle Seahawks", "Los Angeles Chargers", t, rows)
    assert a["ok"] and b["ok"], (a, b)
    assert a["us_market_slug"] == "aec-nfl-lar-phi-2026-10-04"
    assert b["us_market_slug"] == "aec-nfl-lac-sea-2026-10-04"
    assert V.nfl_team("LA Rams") == ("rams", "los angeles")
    assert V.nfl_team("LA Chargers") == ("chargers", "los angeles")
    assert V.nfl_team("NY Giants") == ("giants", "new york")
    assert V.nfl_team("NY Jets") == ("jets", "new york")


def test_abbreviated_and_full_renderings_name_one_team_only():
    cases = {
        "Tampa Bay Buccaneers": "buccaneers", "TB Buccaneers": "buccaneers",
        "New England Patriots": "patriots", "NE Patriots": "patriots",
        "Washington Commanders": "commanders", "WAS Commanders": "commanders",
        "Las Vegas Raiders": "raiders", "LV Raiders": "raiders",
        "San Francisco 49ers": "49ers", "SF 49ers": "49ers", "49ers": "49ers",
        "JAC Jaguars": "jaguars", "Jacksonville Jaguars": "jaguars",
    }
    for name, nick in cases.items():
        got = V.nfl_team(name)
        assert got is not None and got[0] == nick, (name, got)
    # a region that is another team's is nobody, never a guess
    for name in ("Oakland Raiders", "Louisville Cardinals", "Los Angeles",
                 "New York", "Tampa Bay", "Rams Chargers"):
        assert V.nfl_team(name) is None, name
    # the venue's abbreviated TITLES confirm the provider's full names
    titles = [g["venue_title"] for g in SLATE["games"]]
    days = {g["venue_title"]: g["utc_day"] for g in SLATE["games"]}
    prov = [{"home_team": g["home"], "away_team": g["away"],
             "commence_time": g["kickoff_utc"]} for g in SLATE["games"]]
    conf = loop.confirm_mapping_by_fixtures(
        provider_events=prov, venue_event_titles=titles,
        venue_event_days=days, family="football")
    assert conf["ok"] and conf["matches"] == 14, conf
    # each of the five named renderings maps to its own contract
    for pair, home, away in (("gb-tb", "Tampa Bay Buccaneers",
                              "Green Bay Packers"),
                             ("ne-buf", "Buffalo Bills",
                              "New England Patriots"),
                             ("ind-was", "Washington Commanders",
                              "Indianapolis Colts"),
                             ("kc-lv", "Las Vegas Raiders",
                              "Kansas City Chiefs"),
                             ("den-sf", "San Francisco 49ers",
                              "Denver Broncos")):
        g, k = _kick(pair)
        m = _match(home, away, k)
        assert m["ok"] and m["us_market_slug"] == g["market_slug"], m
        assert m["partial_matches"] == []


def test_home_away_and_the_yes_no_row_come_from_the_venue_rows():
    g, k = _kick("ind-was")
    # provider: Washington at home (as the venue orders it). Home is the
    # priced outcome; its venue row is the SHORT (NO) side of the contract.
    m = _match("Washington Commanders", "Indianapolis Colts", k)
    assert m["ok"], m
    assert (m["us_market_slug"], m["intent"]) == (g["market_slug"], SHORT)
    assert m["assignment"] == {"home": "washington commanders",
                               "away": "indianapolis colts"}
    # a provider that lists the Colts at home (a neutral-site rendering)
    # prices the Colts: the LONG (YES) row of the SAME contract
    m2 = _match("Indianapolis Colts", "Washington Commanders", k)
    assert m2["ok"], m2
    assert (m2["us_market_slug"], m2["intent"]) == (g["market_slug"], LONG)
    idn = V.identity_from_match(m2, priced_outcome="Indianapolis Colts")
    assert idn["payout_event"] == "Indianapolis Colts"
    assert idn["payout_is_complement"] is False
    assert idn["ladder_side"] == "ASK"
    idn = V.identity_from_match(m, priced_outcome="Washington Commanders")
    assert idn["ladder_side"] == "BID" and idn["intent"] == SHORT
    # the gateway's own ordering agrees: the LONG side is the away team
    for mk in LISTING["markets"]:
        sides = {s["long"]: s["team"]["ordering"] for s in mk["marketSides"]}
        assert sides == {True: "away", False: "home"}


def test_the_london_game_is_dated_by_instant_not_by_slug():
    g, k = _kick("ind-was")
    assert k.isoformat() == "2026-10-04T13:30:00+00:00"
    # the provider's commence time arrives as UTC ISO; 13:30Z is 09:30 ET
    et = k.astimezone(_dt.timezone(_dt.timedelta(hours=-4)))
    assert et.strftime("%Y-%m-%d %H:%M") == "2026-10-04 09:30"
    m = V.match_event(home="Washington Commanders", away="Indianapolis Colts",
                      commence_epoch=V._epoch("2026-10-04T13:30:00Z"),
                      family="football", rows=_all_rows(LONDON),
                      competition=NFL, league_tokens=("nfl",))
    assert m["ok"] and m["offset_s"] == 0.0
    # the Sunday-night game: slug dated 10-04, instant on 10-05 UTC
    g2, k2 = _kick("det-car")
    m2 = V.match_event(home="Carolina Panthers", away="Detroit Lions",
                       commence_epoch=V._epoch("2026-10-05T00:20:00Z"),
                       family="football", rows=_all_rows(LONDON),
                       competition=NFL, league_tokens=("nfl",))
    assert m2["ok"] and m2["us_market_slug"] == "aec-nfl-det-car-2026-10-04"
    # a provider time read in the wrong zone (09:30 as if UTC) is 4 h off:
    # outside the 90-minute window, refused by name -- never a nearby game
    bad = V.match_event(home="Washington Commanders",
                        away="Indianapolis Colts",
                        commence_epoch=V._epoch("2026-10-04T09:30:00Z"),
                        family="football", rows=_all_rows(LONDON),
                        competition=NFL, league_tokens=("nfl",))
    assert not bad["ok"] and bad["refusal"] == V.R_NO_EVENT
    # and a naive time is not an instant at all
    assert V._epoch("2026-10-04T09:30:00") is None
    # the fixture confirmation compares the venue's own game_start day
    day = loop._same_fixture_day("2026-10-05T00:20:00Z", "2026-10-05")
    assert day["same"] is True


def test_a_game_in_progress_keeps_its_identity():
    """The pregame -> live transition: the provider keeps listing the game
    after kickoff (its commence time unchanged) and the venue's row keeps
    its scheduled game_start, so the SAME contract resolves; nothing about
    the start time is re-derived from the clock. Whether a live price may be
    used is decided downstream by the settlement context and freshness
    gates, by name -- not by the identity disappearing."""
    g, k = _kick("ind-was")
    pre = _match("Washington Commanders", "Indianapolis Colts", k)
    live = V.match_event(home="Washington Commanders",
                         away="Indianapolis Colts",
                         commence_epoch=k.timestamp(), family="football",
                         rows=_all_rows(LONDON), competition=NFL,
                         league_tokens=("nfl",))
    assert pre["ok"] and live["ok"]
    assert (pre["us_market_slug"], pre["intent"]) == \
        (live["us_market_slug"], live["intent"])
    # the football book terms are the same in both quote contexts, so the
    # transition does not change which rules apply
    assert T.BOOK_TERMS[("football", "h2h", T.CTX_PRE_GAME)] == \
        T.BOOK_TERMS[("football", "h2h", T.CTX_LIVE)]


def test_nfl_and_college_never_share_a_search():
    g, k = _kick("lar-phi")
    rows = _rows(g, k)
    # an NCAAF price restricted to `cfb` never sees an `nfl` contract
    m = V.match_event(home="Philadelphia Eagles", away="Los Angeles Rams",
                      commence_epoch=k.timestamp(), family="football",
                      rows=rows, competition=NCAAF, league_tokens=("cfb",))
    assert not m["ok"] and m["refusal"] == V.R_NO_EVENT
    # college names are untouched by the NFL table
    assert V.participant_name({"team_name": "ohio state",
                               "side_norm": "buckeyes"}, "football") == \
        "ohio state buckeyes"
    assert V.participant_name({"team_name": "los angeles rams",
                               "side_norm": "rams"}, "football") == \
        "los angeles rams"
    assert loop._team_tokens("Louisville Cardinals", "football")[0] == \
        frozenset({"louisville", "cardinals"})


# ═════════════════════════════════════════════════════════════════════
# 4 · SETTLEMENT AND PROBABILITY
# ═════════════════════════════════════════════════════════════════════

def test_nfl_settlement_claims_only_what_the_documents_support():
    assert LISTING["_evidence"]["response_sha256"].startswith("1a9d1c2f")
    # the captured sentence names the NFL; the scope says so, and the Pro
    # Bowl (the one NFL exception) stays outside it
    assert "NFL" in T._Q_AF_LEAGUES
    scope = T.CAPTURED_SCOPE[("football", "h2h")]
    assert "NFL" in scope["leagues_covered"]
    assert scope["not_covered"] == ("NFL Pro Bowl",)
    assert T.admit_scope(sport_family="football")["covers"]["leagues"] == \
        ["NCAA", "NFL"]
    for mk in LISTING["markets"]:
        assert mk["sportsMarketType"] == "football_team_full_game_winner"
        a = vset.attest(sport_family="football", market="h2h",
                        venue_evidence={"rules_text": mk["description"],
                                        "rules_field": "description",
                                        "rules_source": LISTING["_source"]},
                        book_evidence={"outcome_names": ["H", "A"]},
                        observed_at=1.0)
        r = a["rules"]
        # overtime: both documents include it -> established
        assert r["overtime"]["established"] is True, r["overtime"]
        # postponed/suspended beyond two weeks: last fair price vs void
        assert r["void"]["refusal"] == vset.R_VOID_CONFLICTS
        assert r["void"]["mismatched_conditions"] == [T.C_NOT_PLAYED]
        # a tie settles at $0.50 at the venue; the book's captured section
        # states no money-line tie rule -> not reconciled, by name
        assert r["draw"]["established"] is False
        assert r["draw"]["refusal"] == vset.R_DRAW_ASYMMETRIC
        assert sorted(a["unmet"]) == sorted([vset.R_DRAW_ASYMMETRIC,
                                             vset.R_VOID_CONFLICTS])
        assert a["overall_established"] is False
    # the college listing (no tie payout) is unchanged
    cfb = json.loads((FIX / "pmus_cfb_listing_2026_10_03.json").read_text())
    a = vset.attest(sport_family="football", market="h2h",
                    venue_evidence={"rules_text":
                                    cfb["markets"][0]["description"]},
                    book_evidence={"outcome_names": ["H", "A"]},
                    observed_at=1.0)
    assert a["unmet"] == [vset.R_VOID_CONFLICTS]


def test_football_stays_out_of_the_de_vig_set():
    assert ("football", "h2h") not in devig.SUPPORTED
    val = devig.valuation(
        contract={"sport_family": "football", "market": "h2h",
                  "selection": "Washington Commanders"},
        quote={"book": "pinnacle",
               "outcomes": {"Washington Commanders": 2.8,
                            "Indianapolis Colts": 1.48}},
        now=0.0)
    assert val["probability"] is None
    assert devig.R_UNSUPPORTED_MARKET in val["refusals"]


# ═════════════════════════════════════════════════════════════════════
# 5 · THE REAL CYCLE ON THE 14-GAME SLATE
# ═════════════════════════════════════════════════════════════════════

def _day(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc) \
        .strftime("%Y-%m-%d")


async def _seed(conn, base: float) -> dict:
    """The slate's production rows, kickoffs shifted so the London game is
    at `base` (offsets preserved), slugs dated by `base`'s UTC day."""
    d = _day(base)
    slugs = {}
    for g in SLATE["games"]:
        k = base + _epoch(g["kickoff_utc"]) - LONDON.timestamp()
        for r in _rows(g, None, day=d):
            await conn.execute(
                "INSERT INTO us_premap (identifier, event_slug, event_title, "
                " market_slug, question, kind, line, side_norm, intent, "
                " team_abbr, team_name, team_league, game_start, sports_type, "
                " updated_at) VALUES ($1,$2,$3,$1,$3,'side',$4,$5,$6,$7,$8,"
                " 'nfl', to_timestamp($9), 'football_team_full_game_winner', "
                " now()) ON CONFLICT (identifier, side_norm) DO UPDATE SET "
                " updated_at = now(), game_start = EXCLUDED.game_start",
                r["market_slug"], r["event_slug"], r["event_title"],
                r["line"], r["side_norm"], r["intent"], r["team_abbr"],
                r["team_name"], float(k))
        slug = "aec-nfl-%s-%s-%s" % (g["away_venue"]["team_abbr"],
                                     g["home_venue"]["team_abbr"], d)
        slugs[slug] = {"game": g, "kickoff": k}
    # a SEGMENT winner on the London game, as production lists it: it must
    # never be read as the full-game contract
    await conn.execute(
        "INSERT INTO us_premap (identifier, event_slug, event_title, "
        " market_slug, question, kind, side_norm, intent, team_abbr, "
        " team_name, team_league, game_start, sports_type, updated_at) "
        "VALUES ($1,$2,$3,$1,$3,'side','yes',$4,'ind','indianapolis colts',"
        " 'nfl', to_timestamp($5),'football_team_first_half_winner', now()) "
        "ON CONFLICT (identifier, side_norm) DO NOTHING",
        "atc-nfl-ind-was-%s-winner-1h-ind" % d, "nfl-ind-was-%s" % d,
        "IND Colts vs. WAS Commanders", LONG, float(base))
    return slugs


async def _cleanup(conn, base: float) -> None:
    d = _day(base)
    pat = "%%-nfl-%%-%s%%" % d
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        for t in ("paper_fills", "paper_orders", "execution_intents",
                  "paper_decisions", "external_valuations"):
            try:
                await conn.execute(
                    "DELETE FROM %s WHERE us_market_slug LIKE $1" % t, pat)
            except Exception:                                  # noqa: BLE001
                pass
        await conn.execute("DELETE FROM us_premap WHERE event_slug LIKE $1",
                           "nfl-%%-%s" % d)


def _provider_event(eid, home, away, kickoff, p_home, stamp):
    prices = [{"name": home, "price": round(1.0 / p_home, 4)},
              {"name": away, "price": round(1.0 / (1.0 - p_home), 4)}]
    return {"id": eid, "sport_key": NFL, "sport_title": "NFL",
            "commence_time": _iso(kickoff), "home_team": home,
            "away_team": away,
            "bookmakers": [
                {"key": "pinnacle", "title": "Pinnacle", "last_update": stamp,
                 "markets": [{"key": "h2h", "last_update": stamp,
                              "outcomes": prices}]},
                {"key": "betfair_ex_eu", "title": "Betfair", "last_update":
                 stamp, "markets": [{"key": "h2h", "last_update": stamp,
                                     "outcomes": prices}]}]}


def _book():
    lvl = lambda p, q: {"px": {"value": "%.4f" % p, "currency": "USD"},
                        "qty": str(q)}
    return {"marketData": {"offers": [lvl(0.55, 400), lvl(0.57, 300)],
                           "bids": [lvl(0.53, 400)],
                           "transactTime": _iso(time.time() - 2.0)}}


def _stub(monkeypatch, slugs: dict):
    monkeypatch.setenv("EDGE_ODDS_API_KEY", "x" * 32)
    calls = {"odds": []}

    async def fake_catalogue(*, api_key, timeout=20.0):
        return {"ok": True, "status": 200, "metered": False,
                "sports": [{"key": "baseball_mlb", "group": "Baseball",
                            "title": "MLB", "active": True},
                           {"key": NFL, "group": "American Football",
                            "title": "NFL", "active": True}],
                "received_at": time.time()}

    async def fake_odds(sport_key, *, api_key, timeout=20.0):
        calls["odds"].append(sport_key)
        t = time.time()
        if sport_key != NFL:
            return {"ok": True, "events": [], "received_at": t,
                    "credits_used": "1", "credits_remaining": "9"}
        stamp = _iso(t - 2.0)
        evs = [_provider_event("nfl-%s" % s.split("-", 2)[2], v["game"]["home"],
                               v["game"]["away"], v["kickoff"], 0.55, stamp)
               for s, v in slugs.items()]
        # NOT LISTED BY THE VENUE: must stop by NAME, not vanish
        first = min(v["kickoff"] for v in slugs.values())
        evs.append(_provider_event("nfl-pit-cle", "Cleveland Browns",
                                   "Pittsburgh Steelers", first + 12600,
                                   0.40, stamp))
        return {"ok": True, "received_at": t, "credits_used": "2",
                "credits_remaining": "8", "events": evs}

    monkeypatch.setattr(loop, "fetch_sport_catalogue", fake_catalogue)
    monkeypatch.setattr(loop, "fetch_odds", fake_odds)
    monkeypatch.setattr(loop, "_read_book_blocking",
                        lambda _slug, **_k: _book())
    prose = {mk["slug"].rsplit("-", 3)[0]: mk["description"]
             for mk in LISTING["markets"]}
    generic = LISTING["markets"][0]["description"]
    monkeypatch.setattr(loop, "_read_venue_rules_blocking",
                        lambda slug, **_k: {
                            "ok": True, "slug": slug,
                            "rules_text": prose.get(slug.rsplit("-", 3)[0],
                                                    generic),
                            "rules_field": "description",
                            "tick_size": "0.0025",
                            "tick_field": "orderPriceMinTickSize",
                            "read_at": time.time(), "from_cache": False,
                            "source": "pmus:/markets?slug=<slug>:rules_text"})
    loop.rules_cache_reset()
    return calls


def _wire_paper(monkeypatch, acct, transport):
    monkeypatch.setattr(PR, "DEFAULT_ACCOUNT_ID", acct["account_id"])
    monkeypatch.setitem(PR._CLIENT, "client", PL.client(transport))
    monkeypatch.setattr(RT, "paper_pass_hook",
                        lambda **kw: {"scheduled": False,
                                      "why": "RECORDED_BY_THE_TEST"})
    PD._CONTEXT_CACHE.clear()


@pg
async def test_every_nfl_game_ends_in_a_recorded_named_outcome(monkeypatch):
    conn = await H.connect()
    t0 = time.time()
    base = t0 + 3 * 3600.0
    try:
        await _cleanup(conn, base)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
            "ON CONFLICT (key) DO UPDATE SET value = 'true'",
            loop.CONTROL_KEY)
        slugs = await _seed(conn, base)
        assert len(slugs) == 14
        acct = await PL.new_account(conn, "nfl24", now=t0)
        tr = PL.Transport(t0)
        for s in slugs:
            tr.set(s, offers=[(0.55, 400)], bids=[(0.53, 400)])
        _wire_paper(monkeypatch, acct, tr)
        monkeypatch.setenv(S.ENV_FLAG, "on")
        calls = _stub(monkeypatch, slugs)

        out = await loop.cycle(conn)

        # 1 · requested within the unchanged budget, and fetched
        requested = [k for k, _ in out["sports_selection"]["sports"]]
        assert NFL in requested, out["sports_selection"]
        assert len(requested) <= loop.MAX_METERED_SPORTS_PER_CYCLE
        assert NFL in calls["odds"]
        step = out["funnel_by_provider_sport"][NFL]
        assert step["family"] == "football"
        assert step["provider_events"] == 15
        assert step["mapping_confirmation"]["ok"], step["mapping_confirmation"]
        assert step["identity_resolved"] == 14, step

        # 2 · every game: a persisted valuation on ITS contract, the priced
        # (home) team as the payout event, refused by name
        vals = {r["us_market_slug"]: dict(r) for r in await conn.fetch(
            "SELECT * FROM external_valuations WHERE us_market_slug = "
            " ANY($1::text[]) AND decided_at >= to_timestamp($2)",
            list(slugs), t0 - 1)}
        missing = sorted(set(slugs) - set(vals))
        assert not missing, (missing, step)
        for s, v in vals.items():
            g = slugs[s]["game"]
            assert v["sport_family"] == "football"
            assert v["payout_event"] == g["home"], (s, v["payout_event"])
            # the home team is the venue's SHORT row on every NFL contract
            assert v["buy_intent"] == SHORT, (s, v["buy_intent"])
            assert v["admissible"] is False
            assert v["probability"] is None
            for code in ("MARKET_NOT_IN_SUPPORTED_SET",
                         vset.R_VOID_CONFLICTS, vset.R_DRAW_ASYMMETRIC):
                assert code in v["refusals"], (s, code, v["refusals"])
            assert "OVERTIME_RULE_NOT_ESTABLISHED" not in v["refusals"]

        # 3 · Derek recorded a decision for every game, ENTER or NAMED REFUSE
        decs = [dict(r) for r in await conn.fetch(
            "SELECT * FROM paper_decisions WHERE session_id = $1 AND "
            " valuation_id = ANY($2::bigint[])", acct["session_id"],
            [v["id"] for v in vals.values()])]
        derek = {x["us_market_slug"] for x in decs
                 if x["strategy"] == PD.STRATEGY}
        assert derek == set(slugs), sorted(set(slugs) - derek)
        for x in decs:
            assert x["verdict"] in ("ENTER", "REFUSE"), x
            if x["verdict"] == "REFUSE":
                assert x["refusal"], x
        print("NFL_PAPER_DECISIONS", sorted(
            (x["us_market_slug"], x["verdict"], x["refusal"]) for x in decs
            if x["strategy"] == PD.STRATEGY))

        # 4 · NO SILENT DROP: one ledger row per provider event, each named
        rows = [r for r in out["event_ledger"] if r["sport_key"] == NFL]
        assert len(rows) == 15
        for r in rows:
            assert r["outcome"] and r["first_refusal"], r
        unlisted = next(r for r in rows
                        if r["provider_event_id"] == "nfl-pit-cle")
        assert V.R_NO_EVENT in unlisted["codes"], unlisted
        per = out["candidate_outcomes"]["per_sport"][NFL]
        assert per == {"provider_events": 15, "rows": 15, "reconciles": True}
    finally:
        await PL.drop_today_run(conn, t0)
        await _cleanup(conn, base)
        await conn.close()
