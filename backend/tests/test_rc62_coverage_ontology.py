"""RC6.2 LANE p-coverage: THE ONTOLOGY'S LABELS, ON PRODUCTION'S OWN ROWS.

Every case below is a market type, a venue code or a rule text production
holds (tests/fixtures/pmus_market_types_and_segment_rules_2026_10_09.json:
research-sql runs 37945671144, 37939739782, 37946677103). Three defects:

  1  A SEGMENT OF THE EVENT READ AS THE WHOLE EVENT. hockey_team_regulation_
     winner, baseball_team_inningN_winner, *_first_five_*, table_tennis /
     tennis set N, esports map N / game N carried period FULL_EVENT. A
     FULL_EVENT WINNER is what market_plane.settlement.h2h_family takes for
     the game's money line, so the 60-minute regulation result -- whose own
     text says "Overtime and any shootout are not included if played" -- was
     compared with the book's money-line terms (which include both) and read
     INCOMPATIBLE_NOT_PRICEABLE on the postponement clause alone; inning and
     first-five winners read the baseball money line's scope refusal.
  2  A MEASURE READ AS THE SHORTER MEASURE ITS NAME CONTAINS. Team statistic
     totals (first downs, field goals made, pass yards, ...) were TEAM_SCORE;
     fantasy points, race-to-points, both-teams-score-points were POINTS;
     hits+runs+RBIs and home runs were RUNS -- and POINTS / RUNS are core
     metrics, so those props took the core subscription priority.
  3  VENUE CODES: `wbc` (145 boxing title futures) was LEAGUE_CODE_AMBIGUOUS
     although its slug names the weight class; `pdc` (darts futures AND
     Chilean soccer) was unlisted; `vkl` / `btla` (soccer only) read
     efootball; eleven residual codes had no sport.

Labels only: nothing here can make a contract PROVEN or PRICEABLE, and no
contract leaves the population (the per-type counts are summed back below).
"""
from __future__ import annotations

import json
import pathlib
import time

import pytest

from sportsassets.market_plane import ontology as O
from sportsassets.market_plane import populate as POP
from sportsassets.market_plane import settlement as S

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
PROD = json.loads((FIX / "pmus_market_types_and_segment_rules_2026_10_09.json")
                  .read_text())
TYPES = {r[0]: r for r in PROD["market_types"]}
NOW = time.time()


def _row(event_id, market_type, slug=None, start=None):
    return {"market_slug": slug or ("aec-%s-%s" % (event_id, market_type)),
            "event_slug": event_id, "sports_type": market_type,
            "listing_state": "PREGAME", "updated_at": NOW,
            "game_start": start, "sides": []}


def _contract(event_id, market_type, **kw):
    return POP.contract_row(_row(event_id, market_type, **kw), now=NOW)


# ═════════════════════════════════════════════════════════════════════
# 1. A SEGMENT IS NEVER THE WHOLE EVENT
# ═════════════════════════════════════════════════════════════════════

#: (production market type, sport, family, period it names)
SEGMENTS = (
    ("hockey_team_regulation_winner", "hockey", "WINNER", "REGULATION_TIME"),
    ("baseball_team_first_five_winner", "baseball", "WINNER",
     "FIRST_FIVE_INNINGS"),
    ("baseball_team_first_five_total", "baseball", "TOTAL",
     "FIRST_FIVE_INNINGS"),
    ("baseball_team_first_five_spread", "baseball", "MARGIN",
     "FIRST_FIVE_INNINGS"),
    ("baseball_team_inning1_winner", "baseball", "WINNER", "INNING_1"),
    ("baseball_team_inning9_winner", "baseball", "WINNER", "INNING_9"),
    ("baseball_team_inning1_total", "baseball", "TOTAL", "INNING_1"),
    ("baseball_team_inning7_total", "baseball", "TOTAL", "INNING_7"),
    ("table_tennis_set_2_winner", "table_tennis", "WINNER", "SET_2"),
    ("table_tennis_set_3_winner", "table_tennis", "WINNER", "SET_3"),
    ("table_tennis_set_4_winner", "table_tennis", "WINNER", "SET_4"),
    ("tennis_set_2_winner", "tennis", "WINNER", "SET_2"),
    ("esports_map_winner_1", "esports", "WINNER", "MAP_1"),
    ("esports_map_winner_2", "esports", "WINNER", "MAP_2"),
    ("esports_map_total_rounds_2", "esports", "TOTAL_ROUNDS", "MAP_2"),
    ("esports_map_rounds_handicap_1", "esports", "ROUNDS_HANDICAP", "MAP_1"),
    ("esports_game_winner_3", "esports", "WINNER", "GAME_3"),
    ("esports_game_total_kills_1", "esports", "TOTAL_KILLS", "GAME_1"),
    ("esports_game_first_blood_4", "esports", "BLOOD", "GAME_4"),
    ("esports_game_kills_odd_even_2", "esports", "KILLS_ODD_EVEN", "GAME_2"),
)


@pytest.mark.parametrize("mt,sport,family,period", SEGMENTS)
def test_a_segment_market_names_its_segment(mt, sport, family, period):
    assert mt in TYPES, "a production market type"
    assert TYPES[mt][2] == "FULL_EVENT", "production read it as the event"
    c = _contract("x-a-b-2026-10-10", mt)
    assert (c["sport"], c["family"], c["period"]) == (sport, family, period)
    assert c["ontology"]["gaps"] == []
    assert S.h2h_family(c) is None, "a segment is never the money line"


#: the whole event keeps its label, and the money line stays a money line
WHOLE = (
    ("soccer_team_full_time_winner", "soccer"),
    ("hockey_team_full_game_winner", "hockey"),
    ("basketball_team_full_game_winner", "basketball"),
    ("football_team_full_game_winner", "football"),
    ("baseball_team_full_game_winner", "baseball"),
)


@pytest.mark.parametrize("mt,sport", WHOLE)
def test_a_full_game_winner_is_still_the_money_line(mt, sport):
    c = _contract("x-a-b-2026-10-10", mt)
    assert (c["family"], c["period"]) == ("WINNER", "FULL_EVENT")
    assert S.h2h_family(c) == sport
    for other in ("esports_series_total_maps", "esports_match_winner",
                  "esports_series_map_handicap", "table_tennis_match_winner",
                  "tennis_match_winner", "baseball_team_first_inning_run"):
        o = _contract("x-a-b-2026-10-10", other)
        assert o["period"] == TYPES[other][2], other


def _rules_row(r):
    return {"rules_sha256": r["rules_sha256"], "rules_text": r["rules_text"],
            "rules_published": True, "venue": "POLYMARKET_US",
            "parse_status": "ESTABLISHED", "evidence": {}}


@pytest.mark.parametrize("r", [r for r in PROD["rules_rows"]
                               if r["market_type"] in (
                                   "hockey_team_regulation_winner",
                                   "baseball_team_inning3_winner",
                                   "baseball_team_first_five_winner")],
                         ids=lambda r: r["contract_id"])
def test_a_segment_winners_text_is_never_read_as_the_money_line(r):
    """The venue's own text, verbatim. Production compared these with the
    book's MONEY-LINE terms (production_settlement_why); they are now named
    for what they are: a segment market whose book terms are not held."""
    assert r["production_settlement_why"].startswith((
        "INCOMPATIBLE_NOT_PRICEABLE:", "BOOKMAKER_TERMS_NOT_HELD:QUOTE_"))
    c = _contract(r["event_id"], r["market_type"], slug=r["contract_id"])
    st = S.state_for(c, rules=_rules_row(r), rules_looked_up=True,
                     derivative_terms=True)
    assert st["state"] == S.NOT_PROVEN and not st["proven"]
    assert st["why"] == "%s:%s/WINNER/%s" % (
        S.R_BOOKMAKER_TERMS_NOT_HELD, c["sport"], c["period"])
    assert "terms" not in st["evidence"], "no money-line comparison was run"
    if r["market_type"] == "hockey_team_regulation_winner":
        assert "overtime and any shootout are not included" in \
            r["rules_text"].lower()


def test_a_segment_no_longer_takes_the_core_full_event_priority():
    soon = NOW + 3600
    reg = _contract("nhl-sea-det-2026-10-09", "hockey_team_regulation_winner",
                    start=soon)
    ml = _contract("nhl-sea-det-2026-10-09", "hockey_team_full_game_winner",
                   start=soon)
    assert ml["priority"] == POP.P_CORE_SOON
    assert reg["priority"] == POP.P_OTHER_SOON


# ═════════════════════════════════════════════════════════════════════
# 2. THE MEASURE THE VENUE NAMES
# ═════════════════════════════════════════════════════════════════════

#: (production type, metric, operator, subject)
MEASURES = (
    ("football_team_total_field_goals_made", "FIELD_GOALS_MADE", "TOTAL",
     "TEAM"),
    ("football_team_total_first_downs", "FIRST_DOWNS", "TOTAL", "TEAM"),
    ("football_team_total_pass_touchdowns", "PASS_TOUCHDOWNS", "TOTAL",
     "TEAM"),
    ("football_team_total_pass_yards", "PASS_YARDS", "TOTAL", "TEAM"),
    ("football_team_total_receptions", "RECEPTIONS", "TOTAL", "TEAM"),
    ("football_team_total_rush_touchdowns", "RUSH_TOUCHDOWNS", "TOTAL",
     "TEAM"),
    ("football_team_total_touchdowns", "TOUCHDOWNS", "TOTAL", "TEAM"),
    ("football_team_total_offensive_yards", "OFFENSIVE_YARDS", "TOTAL",
     "TEAM"),
    ("football_team_total_rush_yards", "RUSH_YARDS", "TOTAL", "TEAM"),
    ("football_team_total_defensive_special_teams_touchdowns",
     "DEFENSIVE_SPECIAL_TEAMS_TOUCHDOWNS", "TOTAL", "TEAM"),
    ("football_player_fantasy_points_ppr", "FANTASY_POINTS", "TOTAL",
     "PLAYER"),
    ("football_game_race_to_points", "RACE_TO_POINTS", "EQ", "EVENT"),
    ("football_game_first_quarter_both_teams_score_points",
     "BOTH_TEAMS_SCORE_POINTS", "YES_NO", "EVENT"),
    ("baseball_player_hits_runs_rbis", "HITS_RUNS_RBIS", "TOTAL", "PLAYER"),
    ("baseball_player_home_runs", "HOME_RUNS", "TOTAL", "PLAYER"),
    ("football_player_most_passing_yards", "MOST_PASSING_YARDS", "EQ",
     "PLAYER"),
)


@pytest.mark.parametrize("mt,metric,op,subject", MEASURES)
def test_the_metric_is_the_measure_the_venue_names(mt, metric, op, subject):
    assert mt in TYPES
    p = O.parse_market_type(venue="POLYMARKET_US", contract_id="x",
                            sports_market_type=mt)
    m = p["meaning"]
    assert p["ok"]
    assert (m["metric"], m["operator"], m["subject_type"]) == \
        (metric, op, subject)
    assert m["metric"] != TYPES[mt][1], "production carried the shorter one"
    assert not (m["metric"] in POP.CORE_METRICS
                and m["period"] == "FULL_EVENT"), "a prop is not core"


@pytest.mark.parametrize("mt,metric", (
    ("football_team_points_full_game_total", "POINTS"),
    ("hockey_team_total_goals", "GOALS"),
    ("baseball_team_total_runs", "RUNS"),
    ("football_player_passing_yards", "PASSING_YARDS"),
    ("football_game_total_touchdowns", "TOUCHDOWNS"),
    ("soccer_game_total_corners", "TOTAL_CORNERS"),
    ("football_game_double_result", "DOUBLE_RESULT"),
))
def test_a_score_total_and_every_other_type_keep_their_label(mt, metric):
    p = O.parse_market_type(venue="POLYMARKET_US", contract_id="x",
                            sports_market_type=mt)
    assert p["meaning"]["metric"] == metric == TYPES[mt][1]


def test_every_production_type_keeps_its_sport_and_none_leaves_the_registry():
    """No contract is dropped or loses its sport: every production market
    type still yields a registry row with the same sport, and the summed
    count of contracts per type is unchanged (the labels move, the
    population does not)."""
    n_before = n_after = 0
    for mt, fam, per, why, n in PROD["market_types"]:
        n_before += n
        c = _contract("x-a-b-2026-10-10", mt)
        assert c is not None, mt
        n_after += n
        if mt not in ("futures", "moneyline", "pickleball_match_winner"):
            assert c["sport"] is not None, mt
    assert n_after == n_before


# ═════════════════════════════════════════════════════════════════════
# 3. VENUE CODES, READ WITH THE VENUE'S OWN SLUG
# ═════════════════════════════════════════════════════════════════════

def test_every_wbc_title_future_is_boxing_by_its_weight_class():
    rows = PROD["wbc_rows"]
    assert len(rows) == 145
    for cid, ev, mt, title in rows:
        c = POP.contract_row(_row(ev, mt, slug=cid), now=NOW)
        assert c["sport"] == "boxing", ev
        assert c["ontology"]["gaps"] == []
        assert c["ontology"]["sport_basis"] == \
            "VENUE_LEAGUE_CODE_AND_EVENT_SLUG"
        assert "weight" in title.lower()


@pytest.mark.parametrize("ev", (
    "wbc-usa-jpn-2026-03-17",            # a World Baseball Classic game
    "wbc-2026-03-31-w",                  # a WBC outright, no weight class
    "pdc-worldchamp-2027-01-03-w",       # darts, under a code soccer shares
))
def test_an_ambiguous_code_without_the_grammar_stays_a_named_gap(ev):
    c = _contract(ev, "futures")
    assert c["sport"] is None
    assert c["ontology"]["gaps"] == ["LEAGUE_CODE_AMBIGUOUS"]


def test_a_typed_row_under_an_ambiguous_code_keeps_its_own_sport():
    c = _contract("pdc-unc-nub-2026-10-12", "soccer_team_full_time_winner")
    assert c["sport"] == "soccer" and c["ontology"]["gaps"] == []


@pytest.mark.parametrize("code,sport", (
    ("ppa", "pickleball"), ("powerslap", "power_slap"), ("dfb", "soccer"),
    ("uefa", "soccer"), ("motogp", "motorsport"), ("cdb", "soccer"),
    ("lib", "soccer"), ("fide", "chess"), ("boxing", "boxing"),
    ("football", "football"),
))
def test_the_residual_codes_read_their_sport(code, sport):
    row = next(r for r in PROD["unmapped_codes"] if r[0] == code)
    assert row[1] == "ONTOLOGY_GAPS:SPORT_NOT_NORMALIZED"
    mt = "moneyline" if code in ("powerslap", "boxing") else "futures"
    c = _contract(row[3], mt)
    assert c["sport"] == sport and c["ontology"]["gaps"] == []


def test_vkl_and_btla_are_soccer_codes():
    for ev in ("btla-zem-husa-2026-10-08", "vkl-vps-gni-2026-10-12"):
        assert _contract(ev.split("-")[0] + "-title-2027-05-30-w",
                         "futures")["sport"] == "soccer"


def test_the_non_sports_codes_are_still_excluded_by_name_only():
    for ev in ("btc-updown-1h-2026-10-09-1400z", "nobel-peace-2026-10-09",
               "uscpi-september-yoy-2026-10-14", "temp-sfohigh-2026-10-10"):
        assert POP.contract_row(_row(ev, "futures"), now=NOW) is None
    assert not (set(O.LEAGUE_SPORT) & O.NON_SPORTS_LEAGUES)
    assert not (set(O.LEAGUE_SPORT) & set(O.AMBIGUOUS_LEAGUE_CODES))
