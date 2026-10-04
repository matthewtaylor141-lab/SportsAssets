"""LINE MARKETS -- SPREADS, TOTALS AND TEAM TOTALS -- PRICED ONLY WHERE THE
PAYOFF IS PROVEN EQUIVALENT (R30A P0 incident "coverage -> trade starvation",
market-family root cause RC1; owner decisions 2026-10-04).

THE DEFECT. Every layer priced the full-game MONEY LINE only. The collector
asked the metered provider for `markets=h2h`, the primary selector read only
`s;0;m`, the venue-native resolver accepted only `*_full_game_winner` rows,
and the de-vig's supported set held two money lines. Yet the venue lists line
contracts on every game it lists -- per day, measured 2026-10-04 (research-sql
run 37235155757): NFL 574 spread / 406 total / 476 team-total contracts, NCAAF
4,034 / 3,312 / 2,852, every one a half-point line -- and the leased PinnAPI
WebSocket already carries Pinnacle's spread, total and team-total markets
(`pinnapi_feed.Quote.points`, per designation). None of it was ever read.

THE OWNER'S RULE, AND HOW IT IS KEPT. "Only compare prices when the payoff
functions are genuinely equivalent." A line market is priced here ONLY when
BOTH sides' published words grade the ordinarily completed game the same way,
each side cited:

  book   Pinnacle's published betting rules, verbatim (the General Rules and
         the sport sections; tests/fixtures/pinnacle_line_rules_2026_10_04.json,
         fetch-docs run 37234815185, page sha256 63d64321...303fd -- the same
         bytes as the 2026-10-01 and 2026-10-03 captures);
  venue  the contract's OWN description, read per contract at decision time
         (`ext_pinnacle_loop.venue_settlement_evidence`), matched against the
         venue's standard wording captured for each family
         (tests/fixtures/pmus_line_listings_2026_10_04.json, fetch-docs runs
         37235387633 / 37235391116). An unrecognised text establishes NOTHING.

and then only:

  * on a HALF-POINT line. A whole line can land exactly (a push); the book
    refunds it, the venue's captured text states no push rule at all, so
    push handling is not proven equivalent and the line is refused by name
    (LINE_NOT_A_HALF_POINT...). A quarter line splits the stake at the book
    and is refused the same way;
  * against Pinnacle's market at the IDENTICAL line and side
    (LINE_DOES_NOT_MATCH otherwise -- the de-vig's own code): never the
    nearest line, never an interpolation;
  * de-vigged on THAT market's own two-way pair (home/away for a spread,
    over/under for a total or a team total), never off the money line and
    never off a neighbouring line.

WHAT IS PROVEN, AND WHAT IS NOT (each refusal is precise, never a generic
"unsupported"):

  PROVEN on the ordinary-completion branch -- the same basis the completed-game
  paper policy prices money lines on (agents.paper_benchmark):
    football   spread / total / team total   overtime included both sides
                                             (NFL and NCAA: "All American
                                             Football rules apply to NFL,
                                             NCAA, UFL, and CFL")
    hockey     spread / total / team total   overtime included and a
                                             shootout counts one goal to the
                                             winner, both sides
    basketball spread / total                every overtime included both
                                             sides
    baseball   spread / total / team total   the completed game's final
                                             score, extra innings included

  NOT PROVEN, refused by name:
    soccer     the book's "Soccer Market Rules" section, which by its General
               Rules TAKES PRECEDENCE over the sport section, lies outside the
               capture's reader window (BOOK_MARKET_RULES_SECTION_NOT_CAPTURED)
    tennis     the book's Tennis section was not read at all
               (BOOK_RULES_FOR_THIS_SPORT_NOT_CAPTURED)
    basketball team total: no venue listing of that family was captured, so
               its wording is unknown (VENUE_TERMS_FOR_THIS_FAMILY_NOT_CAPTURED)

THE EXCEPTIONAL STATES ARE NOT EQUIVALENT, AND ARE SAID TO BE. Postponement,
suspension, a short game and (baseball) a shortened official game are settled
differently by the two sides -- the venue at its last fair market price, the
book by voiding or by its own minimums. Exactly as for the money line, those
are DISCLOSED on every proof (`exceptional`) and never reported as proven
compatibility; the strict lane keeps refusing them
(LINE_EXCEPTIONAL_SETTLEMENT_TERMS_DIFFER), and only the completed-game
policy, which prices conditional on ordinary completion by design, may act.

NOTHING HERE PLACES, SIZES OR GATES AN ORDER, and no threshold is read or
written. Pure: no socket, no database, no clock of its own; the feed cache is
read through `pinnapi_feed.FeedCache.read`, whose 30 s rule is unchanged.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Optional

from . import bettor_pinnacle_devig as devig
from . import pinnapi_feed as F

VERSION = "BETTOR_LINE_MARKET_FAMILY_V1"

#: Pinnacle's own market types (pinnapi_feed.Quote.market_type), which are
#: also the `market` a line valuation is recorded under.
SPREAD = "spread"
TOTAL = "total"
TEAM_TOTAL = "team_total"
LINE_FAMILIES = (SPREAD, TOTAL, TEAM_TOTAL)

INTENT_LONG = "ORDER_INTENT_BUY_LONG"
INTENT_SHORT = "ORDER_INTENT_BUY_SHORT"

# ── refusals, one per way a line contract can fail to be priced ─────────
R_NOT_A_LINE_TYPE = "VENUE_CONTRACT_TYPE_IS_NOT_A_LINE_FAMILY"
R_LINE_TYPE_UNKNOWN = "VENUE_LINE_TYPE_NOT_IN_THE_FAMILY_TABLE"
R_BOOK_SPORT_NOT_CAPTURED = "BOOK_RULES_FOR_THIS_SPORT_NOT_CAPTURED"
R_BOOK_MARKET_RULES_NOT_CAPTURED = "BOOK_MARKET_RULES_SECTION_NOT_CAPTURED"
R_VENUE_FAMILY_NOT_CAPTURED = "VENUE_TERMS_FOR_THIS_FAMILY_NOT_CAPTURED"
R_VENUE_TEXT_ABSENT = "VENUE_LINE_RULES_TEXT_NOT_READ"
R_VENUE_TEXT_UNRECOGNISED = "VENUE_LINE_TEXT_DOES_NOT_STATE_THE_CAPTURED_TERMS"
R_VENUE_TEXT_CONFLICTS = "VENUE_LINE_TEXT_CONFLICTS_WITH_THE_BOOK_GRADING"
R_VENUE_TEXT_LINE = "VENUE_LINE_TEXT_LINE_DIFFERS_FROM_THE_CONTRACT_LINE"
R_VENUE_TEXT_TEAM = "VENUE_LINE_TEXT_TEAM_DIFFERS_FROM_THE_CONTRACT_TEAM"
R_CONTRACT_SIDES = "VENUE_LINE_CONTRACT_SIDES_NOT_ESTABLISHED"
R_CONTRACT_LINE = "VENUE_LINE_CONTRACT_LINE_NOT_READABLE"
R_CONTRACT_SLUG_LINE = "VENUE_LINE_SLUG_DISAGREES_WITH_THE_CONTRACT_LINE"
R_CONTRACT_TEAM = "VENUE_LINE_TEAM_NOT_ONE_OF_THE_FIXTURE_PARTICIPANTS"
R_CONTRACT_TYPE_MIXED = "VENUE_LINE_MARKET_ROWS_DISAGREE_ON_THE_TYPE"
R_NOT_HALF_POINT = ("LINE_NOT_A_HALF_POINT_PUSH_HANDLING_NOT_PROVEN_"
                    "EQUIVALENT")
R_LINE_MISMATCH = devig.R_LINE_MISMATCH          # "LINE_DOES_NOT_MATCH"
R_NO_PINNACLE_FAMILY = "PINNACLE_QUOTES_NO_MARKET_OF_THIS_FAMILY_FOR_THE_FIXTURE"
R_PAIR_INCOMPLETE = "PINNACLE_LINE_PAIR_INCOMPLETE"
R_PAIR_NOT_MIRRORED = "PINNACLE_LINE_PAIR_POINTS_NOT_MIRRORED"
R_LINE_AMBIGUOUS = "PINNACLE_LINE_MATCHES_MORE_THAN_ONE_MARKET"
R_TEAM_TOTAL_SIDE = "PINNACLE_TEAM_TOTAL_STATES_NO_TEAM"
R_NO_FIXTURE = "PINNACLE_LINE_FIXTURE_NOT_HELD"
R_INPUT_CHANGED = "PINNAPI_LINE_INPUT_CHANGED"
#: the strict lane's refusal of every line valuation: the exceptional
#: settlement terms differ (see the module docstring), so no strict or
#: funded path may ever read a line valuation as settlement-compatible.
R_EXCEPTIONAL_DIFFER = "LINE_EXCEPTIONAL_SETTLEMENT_TERMS_DIFFER"

REFUSALS = (R_NOT_A_LINE_TYPE, R_LINE_TYPE_UNKNOWN, R_BOOK_SPORT_NOT_CAPTURED,
            R_BOOK_MARKET_RULES_NOT_CAPTURED, R_VENUE_FAMILY_NOT_CAPTURED,
            R_VENUE_TEXT_ABSENT, R_VENUE_TEXT_UNRECOGNISED,
            R_VENUE_TEXT_CONFLICTS, R_VENUE_TEXT_LINE, R_VENUE_TEXT_TEAM,
            R_CONTRACT_SIDES, R_CONTRACT_LINE, R_CONTRACT_SLUG_LINE,
            R_CONTRACT_TEAM, R_CONTRACT_TYPE_MIXED, R_NOT_HALF_POINT,
            R_LINE_MISMATCH, R_NO_PINNACLE_FAMILY, R_PAIR_INCOMPLETE,
            R_PAIR_NOT_MIRRORED, R_LINE_AMBIGUOUS, R_TEAM_TOTAL_SIDE,
            R_NO_FIXTURE, R_INPUT_CHANGED, R_EXCEPTIONAL_DIFFER)

# ═════════════════════════════════════════════════════════════════════
# THE VENUE'S LINE TYPES (sportsMarketType, as the listing states it)
# ═════════════════════════════════════════════════════════════════════
#
# Every entry is a type the venue's own listing was READ carrying
# (pmus_line_listings_2026_10_04.json); a type not here is refused as
# VENUE_LINE_TYPE_NOT_IN_THE_FAMILY_TABLE, never guessed from its name.
VENUE_LINE_TYPES = {
    "football_team_full_game_spread": ("football", SPREAD),
    "football_team_full_game_total": ("football", TOTAL),
    "football_team_points_full_game_total": ("football", TEAM_TOTAL),
    "hockey_team_full_game_spread": ("hockey", SPREAD),
    "hockey_team_full_game_total": ("hockey", TOTAL),
    "hockey_team_total_goals": ("hockey", TEAM_TOTAL),
    "basketball_team_full_game_spread": ("basketball", SPREAD),
    "basketball_team_full_game_total": ("basketball", TOTAL),
    "baseball_team_full_game_spread": ("baseball", SPREAD),
    "baseball_team_full_game_total": ("baseball", TOTAL),
    "baseball_team_total_runs": ("baseball", TEAM_TOTAL),
    "soccer_team_full_game_spread": ("soccer", SPREAD),
    "soccer_team_full_game_total": ("soccer", TOTAL),
    "tennis_match_games_spread": ("tennis", SPREAD),
    "tennis_match_total_games": ("tennis", TOTAL),
}
#: the venue's winner types, named so a winner row reaching this module is a
#: precise refusal (it is the money line's), not an unknown line type
WINNER_SUFFIXES = ("_full_game_winner", "_full_time_winner", "_match_winner")

#: The venue reads behind the captured wording (fixture `_reads`).
VENUE_CAPTURE = {
    "source": ("GET https://gateway.polymarket.us/v1/markets?slug=..."
               " (public listing, no credential)"),
    "reads": {"R1": {"run_id": 37235387633, "retrieved_at":
                     "2026-10-04T21:16:00Z", "response_sha256":
                     "dc07809277a9e75fae7a2e302609b81e"
                     "90b52cb4f59656f56e8f12ac76428590"},
              "R2": {"run_id": 37235391116, "retrieved_at":
                     "2026-10-04T21:16:07Z", "response_sha256":
                     "4c267eae58a2fe39823886a24b885f12"
                     "aeade71d9cb380b94b70e37e2bd50e35"}},
    "fixture": "tests/fixtures/pmus_line_listings_2026_10_04.json",
}

# ═════════════════════════════════════════════════════════════════════
# THE BOOK'S WORDS, VERBATIM (Pinnacle's published betting rules)
# ═════════════════════════════════════════════════════════════════════
BOOK_CAPTURE = {
    "source": "Pinnacle betting rules (published rules page)",
    "source_url": "https://www.pinnacle.com/en/future/betting-rules",
    "retrieved_at": "2026-10-04T21:07:42Z",
    "run_id": 37234815185,
    "page_sha256": ("63d6432114be131dfbab98baf91f8777"
                    "a98549221a59c288fa76916c3d8303fd"),
    "fixture": "tests/fixtures/pinnacle_line_rules_2026_10_04.json",
    "reader_window": ("the first 60,000 characters of the page's text: every "
                      "section through Soccer is inside it; the Soccer "
                      "Market Rules section and everything after it "
                      "(Tennis included) is not"),
}

#: (section, verbatim sentence) -- a test asserts each is a line, or part of
#: a line, of that section in the capture, so no quote here is a paraphrase.
BOOK = {
    "general_result": ("general_rules",
                       "The result of a fixture will be the final "
                       "determination by the fixture’s governing body on "
                       "the date of the fixture’s completion."),
    "general_not_started": ("general_rules",
                            "If a fixture isn't started 12 hours after its "
                            "scheduled starting time all bets on that "
                            "fixture will be voided."),
    "general_precedence": ("general_rules",
                           "In case of any contradictions: Market Rules take "
                           "precedence over Sport Rules; which take "
                           "precedence over General Rules."),
    "af_overtime": ("american_football",
                    "Bets on the Game and 2nd Half-periods include points "
                    "scored in overtime."),
    "af_leagues": ("american_football",
                   "All American Football rules apply to NFL, NCAA, UFL, and "
                   "CFL unless a specific league is mentioned within the "
                   "rule."),
    "af_not_started": ("american_football",
                       "If a game is not started within 12 hours of its "
                       "originally scheduled time all bets will be voided."),
    "af_short_game": ("american_football",
                      "In addition, if the cumulative playing time of all "
                      "quarters is less than 55 minutes, Full-game markets "
                      "will have no action and bets will be deemed void."),
    "af_suspended": ("american_football",
                     "If a game is suspended after 55 minutes of play and not "
                     "resumed within 12 hours of suspension, then regardless "
                     "of whether the game is completed at a later date or "
                     "not, all bets will have action and the score when the "
                     "game was suspended will be considered final."),
    "hk_overtime": ("hockey",
                    "Unless otherwise specified, Game-period bets include "
                    "overtime and penalty shootouts."),
    "hk_shootout": ("hockey",
                    "For markets that include overtime, penalty shootouts are "
                    "considered part of overtime. If a penalty shootout "
                    "occurs, the winning team is credited with one goal."),
    "hk_minimum": ("hockey",
                   "Bets on Match markets require a minimum of 55 minutes to "
                   "be played for action."),
    "bk_overtime": ("basketball",
                    "Bets on the Game and 2 nd -Half periods include all "
                    "overtimes played in their result."),
    "bk_minimum": ("basketball",
                   "In the NBA, all bets on the Game-period will be voided "
                   "if fewer than 43 minutes are completed."),
    "bb_nine": ("baseball",
                "All Game-period markets, other than the Money Line, have "
                "action only once 9 innings (or 8.5 if the Home team wins) "
                "are completed."),
    "bb_playoff": ("baseball",
                   "MLB Playoff and Play-In games, which will have action "
                   "whenever the game is completed."),
    "bb_suspended": ("baseball",
                     "If a game is suspended in order to be resumed more "
                     "than 12 hours from the first pitch, all pre-game bets "
                     "on the Game-period markets will be deemed void and "
                     "bets on completed periods will have action."),
}


def cite(key) -> dict:
    """One book citation, with the page identity it was read from."""
    section, quote = BOOK[key]
    return {"section": section, "quote": quote,
            "source_url": BOOK_CAPTURE["source_url"],
            "retrieved_at": BOOK_CAPTURE["retrieved_at"],
            "page_sha256": BOOK_CAPTURE["page_sha256"]}


# ═════════════════════════════════════════════════════════════════════
# THE EQUIVALENCE TABLE: (sport, family) -> both sides' grading, cited
# ═════════════════════════════════════════════════════════════════════
GP_FOOTBALL = "COMPLETED_GAME_INCLUDING_OVERTIME"
GP_HOCKEY = ("COMPLETED_GAME_INCLUDING_OVERTIME_SHOOTOUT_COUNTS_ONE_GOAL_"
             "TO_THE_WINNER")
GP_BASKETBALL = "COMPLETED_GAME_INCLUDING_ALL_OVERTIMES"
GP_BASEBALL = "COMPLETED_GAME_FINAL_SCORE_INCLUDING_EXTRA_INNINGS"

_NUM = r"(?P<line>[-+]?\d+(?:\.\d+)?)"
_OT = r"\bovertime is included if played\b"
_SO = (r"\bif a shootout determines the winner, the shootout will count as "
       r"one goal for the winning team\b")
_EI = r"\bextra innings are included if played\b"
_OT_CONFLICT = (r"\bovertime (?:is|will be) (?:not included|excluded)\b",
                r"\bexclud\w* (?:any )?overtime\b",
                r"\bregulation (?:time )?only\b")
_SO_CONFLICT = (r"\bshootout (?:does|will) not count\b",
                r"\bexclud\w* (?:the |any )?shootout\b")
_EI_CONFLICT = (r"\bextra innings (?:are|will be) (?:not included|excluded)\b",
                r"\bexclud\w* (?:any )?extra innings\b",
                r"\bafter (?:nine|9) innings only\b")


def _spread_stmt(unit):
    return (r"\bwill settle to yes if (?:the )?(?P<team>.+?) covers? an? "
            + _NUM + r" " + unit + r" spread in the\b")


def _total_stmt(unit):
    return (r"\bwill settle to yes if .+? combine for over "
            + _NUM + r" " + unit + r" in the\b")


#: The exceptional conditions, both sides' words, disclosed on every proof.
_X_POSTPONED_2W = {
    "condition": "POSTPONED_OR_SUSPENDED_AND_NOT_COMPLETED",
    "venue": ("If the game is delayed, postponed, or suspended and not "
              "rescheduled to a date within two weeks of the originally "
              "scheduled date, the market will settle to the last fair "
              "market price."),
    "book": "general_not_started"}
_X_POSTPONED_2D = {
    "condition": "POSTPONED_OR_SUSPENDED_AND_NOT_COMPLETED",
    "venue": ("If the game is delayed, postponed, suspended, or otherwise "
              "rescheduled and is not rescheduled to start within two "
              "calendar days of the originally scheduled date and time, the "
              "market will settle to the last fair market price."),
    "book": "general_not_started"}

EQUIVALENCE = {
    ("football", SPREAD): {
        "period": GP_FOOTBALL,
        "statement": _spread_stmt("point"), "all_of": (_OT,),
        "none_of": _OT_CONFLICT,
        "book": ("af_overtime", "af_leagues", "general_result"),
        "venue_examples": ("asc-nfl-det-car-2026-10-04-neg-10pt5",
                           "asc-nfl-det-car-2026-10-04-neg-13pt5",
                           "asc-cfb-soumis-troy-2026-10-06-neg-10pt5"),
        "exceptional": (
            dict(_X_POSTPONED_2W, book="af_not_started"),
            {"condition": "SUSPENDED_AFTER_55_MINUTES",
             "venue": _X_POSTPONED_2W["venue"], "book": "af_suspended"},
            {"condition": "FEWER_THAN_55_MINUTES_PLAYED",
             "venue": _X_POSTPONED_2W["venue"], "book": "af_short_game"})},
    ("football", TOTAL): {
        "period": GP_FOOTBALL,
        "statement": _total_stmt("points"), "all_of": (_OT,),
        "none_of": _OT_CONFLICT,
        "book": ("af_overtime", "af_leagues", "general_result"),
        "venue_examples": ("tsc-nfl-det-car-2026-10-04-total-22pt5",
                           "tsc-cfb-soumis-troy-2026-10-06-total-22pt5"),
        "exceptional": None},           # football's (filled below)
    ("football", TEAM_TOTAL): {
        "period": GP_FOOTBALL,
        "statement": (r"\bsettles over if (?:the )?(?P<team>.+?) scores? "
                      r"more than " + _NUM + r" points in the full game of "
                      r"the\b"),
        "all_of": (_OT,), "none_of": _OT_CONFLICT,
        "book": ("af_overtime", "af_leagues", "general_result"),
        "venue_examples": ("tsc-nfl-det-car-2026-10-04-tt-car-10pt5",
                           "tsc-cfb-soumis-troy-2026-10-06-tt-soumis-10pt5"),
        "exceptional": None},
    ("hockey", SPREAD): {
        "period": GP_HOCKEY,
        "statement": _spread_stmt("goal"), "all_of": (_OT, _SO),
        "none_of": _OT_CONFLICT + _SO_CONFLICT,
        "book": ("hk_overtime", "hk_shootout", "general_result"),
        "venue_examples": ("asc-nhl-uta-nyr-2026-10-04-neg-1pt5",
                           "asc-nhl-uta-nyr-2026-10-04-neg-2pt5"),
        "exceptional": (
            _X_POSTPONED_2D,
            {"condition": "FEWER_THAN_55_MINUTES_PLAYED",
             "venue": _X_POSTPONED_2D["venue"], "book": "hk_minimum"})},
    ("hockey", TOTAL): {
        "period": GP_HOCKEY,
        "statement": _total_stmt("goals"), "all_of": (_OT, _SO),
        "none_of": _OT_CONFLICT + _SO_CONFLICT,
        "book": ("hk_overtime", "hk_shootout", "general_result"),
        "venue_examples": ("tsc-nhl-uta-nyr-2026-10-04-2pt5",),
        "exceptional": None},
    ("hockey", TEAM_TOTAL): {
        "period": GP_HOCKEY,
        "statement": (r"\bwill settle to yes if (?:the )?(?P<team>.+?) "
                      r"scores? over " + _NUM + r" goals in the\b"),
        "all_of": (_OT, _SO), "none_of": _OT_CONFLICT + _SO_CONFLICT,
        "book": ("hk_overtime", "hk_shootout", "general_result"),
        "venue_examples": ("tsc-nhl-uta-nyr-2026-10-04-tt-nyr-0pt5",),
        "exceptional": None},
    ("basketball", SPREAD): {
        "period": GP_BASKETBALL,
        "statement": _spread_stmt("point"), "all_of": (_OT,),
        "none_of": _OT_CONFLICT,
        "book": ("bk_overtime", "general_result"),
        "venue_examples": ("asc-nba-gs-lac-2026-10-04-neg-2pt5",),
        "exceptional": (
            _X_POSTPONED_2D,
            {"condition": "NBA_FEWER_THAN_43_MINUTES_COMPLETED",
             "venue": _X_POSTPONED_2D["venue"], "book": "bk_minimum"})},
    ("basketball", TOTAL): {
        "period": GP_BASKETBALL,
        "statement": _total_stmt("points"), "all_of": (_OT,),
        "none_of": _OT_CONFLICT,
        "book": ("bk_overtime", "general_result"),
        "venue_examples": ("tsc-nba-gs-lac-2026-10-04-221pt5",),
        "exceptional": None},
    ("baseball", SPREAD): {
        "period": GP_BASEBALL,
        "statement": _spread_stmt("run"), "all_of": (_EI,),
        "none_of": _EI_CONFLICT,
        "book": ("general_result", "bb_nine", "bb_playoff"),
        "venue_examples": ("asc-mlb-atl-lad-2026-10-04-neg-1pt5",),
        "exceptional": (
            dict(_X_POSTPONED_2W, book="bb_suspended"),
            {"condition": "SHORTENED_GAME_DECLARED_OFFICIAL",
             "venue": ("If the game is shortened but an official final "
                       "result is declared, the market will settle based on "
                       "that result."),
             "book": "bb_nine",
             "note": ("a REGULAR-SEASON game called before 9 innings (8.5) "
                      "has no action at the book and is settled by the "
                      "venue; MLB Playoff and Play-In games have action "
                      "whenever completed (bb_playoff), so in the postseason "
                      "this divergence does not arise")})},
    ("baseball", TOTAL): {
        "period": GP_BASEBALL,
        "statement": _total_stmt("runs"), "all_of": (_EI,),
        "none_of": _EI_CONFLICT,
        "book": ("general_result", "bb_nine", "bb_playoff"),
        "venue_examples": ("tsc-mlb-atl-lad-2026-10-04-6pt5",),
        "exceptional": None},
    ("baseball", TEAM_TOTAL): {
        "period": GP_BASEBALL,
        "statement": (r"\bwill settle to yes if (?:the )?(?P<team>.+?) "
                      r"scores? over " + _NUM + r" runs in the\b"),
        "all_of": (_EI,), "none_of": _EI_CONFLICT,
        "book": ("general_result", "bb_nine", "bb_playoff"),
        "venue_examples": ("tsc-mlb-atl-lad-2026-10-04-tt-atl-1pt5",),
        "exceptional": None},
}
# one exceptional list per sport, shared by its families
for (_s, _f), _spec in EQUIVALENCE.items():
    if _spec["exceptional"] is None:
        _spec["exceptional"] = EQUIVALENCE[(_s, SPREAD)]["exceptional"]

#: (sport, family) pairs the venue lists but whose equivalence is NOT
#: proven, each with its precise reason and what would establish it.
NOT_PROVEN = {
    ("soccer", SPREAD): {
        "refusal": R_BOOK_MARKET_RULES_NOT_CAPTURED,
        "why": ("the book's General Rules give its Market Rules precedence "
                "over its sport rules ('In case of any contradictions: Market "
                "Rules take precedence over Sport Rules'), and the 'Soccer "
                "Market Rules' section lies outside the capture's reader "
                "window -- the handicap's grading is therefore not read"),
        "establish_by": ("capture the Soccer Market Rules section with its "
                         "page hash (fetch-docs) and add the family here")},
    ("soccer", TOTAL): {
        "refusal": R_BOOK_MARKET_RULES_NOT_CAPTURED,
        "why": ("as for the soccer spread: the Soccer Market Rules section, "
                "which takes precedence, was not read"),
        "establish_by": ("capture the Soccer Market Rules section")},
    ("tennis", SPREAD): {
        "refusal": R_BOOK_SPORT_NOT_CAPTURED,
        "why": ("the book's Tennis section is outside the capture's reader "
                "window; no tennis rule (retirement, games handicap) is "
                "claimed from it"),
        "establish_by": "capture the Tennis section with its page hash"},
    ("tennis", TOTAL): {
        "refusal": R_BOOK_SPORT_NOT_CAPTURED,
        "why": "the book's Tennis section was not read",
        "establish_by": "capture the Tennis section with its page hash"},
    ("basketball", TEAM_TOTAL): {
        "refusal": R_VENUE_FAMILY_NOT_CAPTURED,
        "why": ("no venue listing of a basketball team-total contract was "
                "captured, so its wording is unknown"),
        "establish_by": ("read one listing of the family and add its "
                         "standard wording here")},
    ("soccer", TEAM_TOTAL): {
        "refusal": R_BOOK_MARKET_RULES_NOT_CAPTURED,
        "why": ("the Soccer Market Rules section, which takes precedence, "
                "was not read; no venue listing of the family was captured"),
        "establish_by": "capture the Soccer Market Rules section"},
}

#: (sport, family) pairs proven: the de-vig's supported set holds exactly
#: these line families, two outcomes each (a test pins the two equal).
PROVEN = tuple(sorted(EQUIVALENCE))


# ═════════════════════════════════════════════════════════════════════
# SMALL PURE HELPERS
# ═════════════════════════════════════════════════════════════════════

def _num(x) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(str(x).strip().replace("−", "-"))
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def half_point(line) -> bool:
    """True exactly when |line| is n + 0.5 (no push possible)."""
    v = _num(line)
    if v is None:
        return False
    twice = abs(v) * 2.0
    return abs(twice - round(twice)) < 1e-9 and int(round(twice)) % 2 == 1


def fmt_line(v, *, signed: bool) -> str:
    """'-10.5' / '+10.5' for a spread, '22.5' for a total."""
    v = float(v)
    s = ("%.4f" % abs(v)).rstrip("0").rstrip(".")
    if not signed:
        return s
    return ("-" if v < 0 else "+") + s


def fold(text) -> str:
    """Lower-cased, accent-folded, punctuation-free, single-spaced."""
    t = unicodedata.normalize("NFKD", str(text or "")).casefold()
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", t))


def flat_text(text) -> str:
    """The venue prose the templates are matched on: lower-cased, typographic
    quotes and dashes normalised, whitespace collapsed."""
    t = str(text or "").replace("’", "'").replace("−", "-")
    t = t.replace("–", "-").replace("—", "-")
    return " ".join(t.split()).lower()


def rules_sha256(text) -> Optional[str]:
    return (hashlib.sha256(str(text).encode("utf-8")).hexdigest()
            if text else None)


def renderings(rec) -> set:
    """A venue team record's own renderings, folded: its name, its safe name,
    safe name + name ('golden state warriors'), and the abbreviation + name
    the venue's own titles use ('nyr rangers'). Built only from the record's
    fields; never an invented alias."""
    rec = rec or {}
    name, safe = fold(rec.get("team_name")), fold(rec.get("team_safe_name"))
    abbr = fold(rec.get("team_abbr"))
    out = {x for x in (name, safe) if x}
    if name and safe and safe not in name:
        out.add("%s %s" % (safe, name))
    if name and abbr:
        out.add("%s %s" % (abbr, name))
    return out


def other(designation) -> Optional[str]:
    return {"home": "away", "away": "home"}.get(designation)


def designation_of_team(row, participants) -> Optional[str]:
    """Which Pinnacle designation ('home'/'away') a venue row's team record
    is, from the fixture's matched participants: by the venue's team id
    where both carry one, else by exact folded name -- exactly one, or
    None."""
    tid = (row or {}).get("team_id")
    hits = set()
    for des in ("home", "away"):
        rec = (participants or {}).get(des) or {}
        if tid is not None and rec.get("team_id") is not None:
            if str(rec["team_id"]) == str(tid):
                hits.add(des)
            continue
        if renderings({"team_name": (row or {}).get("team_name"),
                       "team_safe_name": (row or {}).get("team_safe_name")}) \
                & renderings(rec):
            hits.add(des)
    return hits.pop() if len(hits) == 1 else None


def venue_line_family(sports_type) -> dict:
    """{'sport', 'family'} for a venue line type, or {'refusal'}."""
    st = str(sports_type or "").strip()
    hit = VENUE_LINE_TYPES.get(st)
    if hit is not None:
        return {"sport": hit[0], "family": hit[1], "sports_type": st}
    if any(st.endswith(s) for s in WINNER_SUFFIXES):
        return {"refusal": R_NOT_A_LINE_TYPE, "sports_type": st}
    return {"refusal": R_LINE_TYPE_UNKNOWN, "sports_type": st}


def family_status(sport, family) -> dict:
    """PROVEN (with its grading period) or the precise reason it is not."""
    if (sport, family) in EQUIVALENCE:
        return {"proven": True,
                "period": EQUIVALENCE[(sport, family)]["period"]}
    np = NOT_PROVEN.get((sport, family))
    if np is not None:
        return dict(np, proven=False)
    return {"proven": False, "refusal": R_LINE_TYPE_UNKNOWN,
            "why": "no equivalence entry for %s/%s" % (sport, family)}


_SLUG_SPREAD = re.compile(r"-(neg|pos)-(\d+)pt(\d+)$")
_SLUG_TOTAL = re.compile(r"-(\d+)pt(\d+)$")
_SLUG_TT = re.compile(r"-tt-([a-z0-9]+)-\d+pt\d+$")


def _slug_line(slug, family) -> Optional[float]:
    s = str(slug or "").lower()
    if family == SPREAD:
        m = _SLUG_SPREAD.search(s)
        if m is None:
            return None
        v = float("%s.%s" % (m.group(2), m.group(3)))
        return -v if m.group(1) == "neg" else v
    m = _SLUG_TOTAL.search(s)
    return None if m is None else float("%s.%s" % (m.group(1), m.group(2)))


# ═════════════════════════════════════════════════════════════════════
# THE VENUE CONTRACT: one line market's catalogue rows -> its proposition
# ═════════════════════════════════════════════════════════════════════

def market_contract(rows, *, participants) -> dict:
    """ONE venue line market (all its `us_premap` rows, one market slug) ->
    its proposition and its two instruments, or the precise refusal.

    The PROPOSITION is what the market's YES (BUY_LONG) side pays on: a team
    covering its signed line (spread), the game total exceeding the line
    (total), one team's score exceeding the line (team total). The SHORT
    side pays on its complement -- on a half-point line exactly the other
    outcome of the pair: the other team covering the opposite line, or the
    under. Sides are read only from
    the venue's own explicit intent markers (premap.side_intent); a market
    whose rows do not carry exactly one LONG and one SHORT refuses.

    `participants` is the fixture's matched venue team records keyed by
    PINNACLE designation ({'home': rec, 'away': rec}, pinnapi_discovery)."""
    rows = [dict(r) for r in (rows or ())]
    out = {"version": VERSION, "ok": False, "refusal": None}
    if not rows:
        out["refusal"] = R_CONTRACT_SIDES
        return out
    slug = str(rows[0].get("market_slug") or rows[0].get("identifier")
               or "")
    out.update(market_slug=slug, event_slug=rows[0].get("event_slug"))
    types = {str(r.get("sports_type") or "") for r in rows}
    if len(types) != 1:
        out["refusal"] = R_CONTRACT_TYPE_MIXED
        return out
    fam = venue_line_family(types.pop())
    out["sports_type"] = fam.get("sports_type")
    if fam.get("refusal"):
        out["refusal"] = fam["refusal"]
        return out
    sport, family = fam["sport"], fam["family"]
    out.update(sport=sport, family=family)
    longs = [r for r in rows if r.get("intent") == INTENT_LONG]
    shorts = [r for r in rows if r.get("intent") == INTENT_SHORT]
    if len(longs) != 1 or len(shorts) != 1:
        out.update(refusal=R_CONTRACT_SIDES,
                   why="%d long / %d short rows" % (len(longs), len(shorts)))
        return out
    yes, no = longs[0], shorts[0]
    designation = None
    if family == SPREAD:
        line = _num(str(yes.get("signed") or "").replace(" ", ""))
        if line is None or line == 0:
            out["refusal"] = R_CONTRACT_LINE
            return out
        no_line = _num(str(no.get("signed") or "").replace(" ", ""))
        if no_line is not None and abs(no_line + line) > 1e-9:
            out.update(refusal=R_CONTRACT_SIDES,
                       why="sides state %s and %s, not opposite lines"
                       % (line, no_line))
            return out
        designation = designation_of_team(yes, participants)
        no_des = designation_of_team(no, participants)
        if designation is None or (no_des is not None
                                   and no_des != other(designation)):
            out.update(refusal=R_CONTRACT_TEAM,
                       why="long-side team %r is not exactly one fixture "
                       "participant" % yes.get("team_name"))
            return out
        team = yes.get("team_name")
    else:
        line = _num(yes.get("line")) or _num(no.get("line"))
        if line is None or line <= 0:
            out["refusal"] = R_CONTRACT_LINE
            return out
        if fold(yes.get("side_norm")) not in ("over", "yes") or \
                fold(no.get("side_norm")) not in ("under", "no"):
            out.update(refusal=R_CONTRACT_SIDES,
                       why="sides %r / %r are not over / under"
                       % (yes.get("side_norm"), no.get("side_norm")))
            return out
        team = None
        if family == TEAM_TOTAL:
            # the venue's own slug token names the team ('-tt-car-'); it is
            # bound to a participant only through that record's abbreviation,
            # and the contract's text must name the same team (see `prove`)
            m = _SLUG_TT.search(slug.lower())
            if m is not None:
                hits = [d for d in ("home", "away")
                        if fold(((participants or {}).get(d) or {})
                                .get("team_abbr")) == m.group(1)]
                if len(hits) == 1:
                    designation = hits[0]
                    team = ((participants or {}).get(designation)
                            or {}).get("team_name")
    slug_line = _slug_line(slug, family)
    if slug_line is not None and abs(slug_line - line) > 1e-9:
        out.update(refusal=R_CONTRACT_SLUG_LINE,
                   why="slug states %s, the contract %s" % (slug_line, line))
        return out
    # EACH SIDE PAYS ON ITS OWN NAMED OUTCOME. On a half-point line the
    # short side's payout -- NOT(the proposition) -- is EXACTLY the other
    # outcome of the two-way pair (the other team covering the opposite line,
    # or the under), so each instrument is priced on its own outcome's
    # de-vigged probability with no complement inversion, the same way the
    # venue-native money line prices each team's row.
    out.update(line=line, designation=designation, team=team,
               half_point=half_point(line),
               instruments=[{"intent": INTENT_LONG, "pays_on": "selection",
                             "side_norm": yes.get("side_norm")},
                            {"intent": INTENT_SHORT, "pays_on": "other",
                             "side_norm": no.get("side_norm")}])
    if not out["half_point"]:
        out["refusal"] = R_NOT_HALF_POINT
        return out
    st = family_status(sport, family)
    if not st["proven"]:
        out.update(refusal=st["refusal"], why=st.get("why"))
        return out
    out.update(ok=True, period=st["period"])
    return out


# ═════════════════════════════════════════════════════════════════════
# THE PAYOFF EQUIVALENCE, FROM BOTH SIDES' WORDS
# ═════════════════════════════════════════════════════════════════════

def prove(*, contract, venue_text, participants) -> dict:
    """IS THIS VENUE CONTRACT'S PAYOFF THE BOOK'S, ON THE ORDINARILY
    COMPLETED GAME? From the contract's OWN text, read now, against the
    captured wording of its family -- every required phrase present, no
    contradicting phrase, the text's own line equal to the contract's line,
    and the text's own team the contract's team. Returns the proof with both
    sides' citations and the exceptional divergences DISCLOSED, or the
    precise refusal. Pure; never raises."""
    c = dict(contract or {})
    sport, family = c.get("sport"), c.get("family")
    out = {"version": VERSION, "established": False, "refusal": None,
           "sport": sport, "family": family,
           "venue_rules_sha256": rules_sha256(venue_text)}
    st = family_status(sport, family)
    if not st["proven"]:
        out.update(refusal=st["refusal"], why=st.get("why"),
                   establish_by=st.get("establish_by"))
        return out
    spec = EQUIVALENCE[(sport, family)]
    out["period"] = spec["period"]
    out["book"] = [cite(k) for k in spec["book"]]
    out["exceptional"] = {
        "status": "DISCLOSED_NOT_EQUIVALENT",
        "divergences": [dict(x, book=cite(x["book"]))
                        for x in spec["exceptional"]],
        "why": ("postponement, suspension and short-game terms differ "
                "between the two sides; they are disclosed research risks, "
                "never proven compatibility")}
    flat = flat_text(venue_text)
    if not flat:
        out["refusal"] = R_VENUE_TEXT_ABSENT
        return out
    bad = [p for p in spec["none_of"] if re.search(p, flat)]
    if bad:
        out.update(refusal=R_VENUE_TEXT_CONFLICTS, matched_conflicts=bad)
        return out
    m = re.search(spec["statement"], flat)
    missing = [p for p in spec["all_of"] if not re.search(p, flat)]
    if m is None or missing:
        out.update(refusal=R_VENUE_TEXT_UNRECOGNISED,
                   missing=([spec["statement"]] if m is None else [])
                   + missing)
        return out
    text_line = _num(m.group("line"))
    want = c.get("line")
    if text_line is None or want is None or (
            abs(text_line - float(want)) > 1e-9 if family == SPREAD
            else abs(abs(text_line) - abs(float(want))) > 1e-9):
        out.update(refusal=R_VENUE_TEXT_LINE, text_line=text_line,
                   contract_line=want)
        return out
    designation = c.get("designation")
    if "team" in m.groupdict() and m.group("team"):
        named = fold(m.group("team"))
        hits = [d for d in ("home", "away")
                if named in renderings((participants or {}).get(d))]
        text_des = hits[0] if len(hits) == 1 else None
        if text_des is None or (designation is not None
                                and text_des != designation):
            out.update(refusal=R_VENUE_TEXT_TEAM, text_team=m.group("team"),
                       contract_designation=designation)
            return out
        designation = text_des
    out.update(established=True, designation=designation,
               venue={"statement": m.group(0), "required": list(
                   spec["all_of"]), "capture": VENUE_CAPTURE,
                   "examples": list(spec["venue_examples"])},
               basis=("both sides grade the ordinarily completed game as %s;"
                      " the half-point line admits no push" % spec["period"]))
    return out


def book_grading(sport, family) -> Optional[dict]:
    """The book's completed-game grading for a proven line family, cited."""
    spec = EQUIVALENCE.get((sport, family))
    if spec is None:
        return None
    return {"period": spec["period"], "book": [cite(k) for k in spec["book"]]}


# ═════════════════════════════════════════════════════════════════════
# THE PINNACLE SIDE: the market at the IDENTICAL line, from the WS cache
# ═════════════════════════════════════════════════════════════════════

def outcome_names(contract, labels) -> tuple:
    """(selection, other) -- the two outcomes' names, built from Pinnacle's
    own participant names, so the de-vig maps the selection exactly."""
    fam, line, des = (contract.get("family"), float(contract["line"]),
                      contract.get("designation"))
    if fam == SPREAD:
        return ("%s %s" % (labels[des], fmt_line(line, signed=True)),
                "%s %s" % (labels[other(des)], fmt_line(-line, signed=True)))
    if fam == TEAM_TOTAL:
        return ("%s Over %s" % (labels[des], fmt_line(line, signed=False)),
                "%s Under %s" % (labels[des], fmt_line(line, signed=False)))
    return ("Over %s" % fmt_line(line, signed=False),
            "Under %s" % fmt_line(line, signed=False))


def _close(a, b) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9


def pinnacle_pair(cache, *, fixture_id, contract, evaluated_ms,
                  max_age_s=30.0) -> dict:
    """Pinnacle's two-way market for THIS contract: same family, full game
    (period 0), the contract's team where it names one, and the IDENTICAL
    line on the IDENTICAL side -- read through the cache's one read path, so
    the 30 s rule (measured from the last observed change, unchanged) and
    every authority refusal apply. Exactly one market, or the precise
    refusal. Pure; never raises."""
    out = {"version": VERSION, "ok": False, "refusal": None,
           "fixture_id": fixture_id}
    try:
        return _pinnacle_pair(cache, out, fixture_id=fixture_id,
                              contract=dict(contract or {}),
                              evaluated_ms=evaluated_ms, max_age_s=max_age_s)
    except Exception as exc:                                   # noqa: BLE001
        out.update(refusal="PINNACLE_LINE_READ_RAISED:%s"
                   % type(exc).__name__)
        return out


def _pinnacle_pair(cache, out, *, fixture_id, contract, evaluated_ms,
                   max_age_s) -> dict:
    if cache is None:
        out["refusal"] = F.R_NO_AUTHORITY
        return out
    ev = cache.events.get(fixture_id)
    labels = F.participants(ev or {})
    if not ev or set(labels) != {"home", "away"}:
        out["refusal"] = R_NO_FIXTURE
        return out
    qid, why = cache.fixture_quote_id(fixture_id)
    if why:
        out["refusal"] = why
        return out
    fam, line = contract.get("family"), _num(contract.get("line"))
    des = contract.get("designation")
    if fam not in LINE_FAMILIES or line is None or (
            fam in (SPREAD, TEAM_TOTAL) and des not in ("home", "away")):
        out["refusal"] = R_CONTRACT_SIDES
        return out
    hits, family_lines = [], []
    for k in cache._keys_of(qid):
        q = cache.quotes.get(k)
        if q is None or q.market_type != fam or (q.period or 0) != 0:
            continue
        pts = dict(q.points or {})
        if fam == SPREAD:
            mine, theirs = pts.get(des), pts.get(other(des))
            family_lines.append(mine)
            if _close(mine, line):
                hits.append((q, theirs))
            continue
        if fam == TEAM_TOTAL:
            side = q.side if q.side in ("home", "away") else None
            if side is None:
                family_lines.append(None)
                continue
            if side != des:
                continue
        o, u = pts.get("over"), pts.get("under")
        family_lines.append(o)
        if _close(o, line):
            hits.append((q, u))
    out["pinnacle_lines_of_this_family"] = sorted(
        {x for x in family_lines if x is not None})[:24]
    if not hits:
        if fam == TEAM_TOTAL and None in family_lines and \
                not out["pinnacle_lines_of_this_family"]:
            out["refusal"] = R_TEAM_TOTAL_SIDE
        else:
            out["refusal"] = (R_LINE_MISMATCH if family_lines
                              else R_NO_PINNACLE_FAMILY)
        return out
    if len(hits) > 1:
        out.update(refusal=R_LINE_AMBIGUOUS,
                   keys=sorted(q.key for q, _ in hits)[:6])
        return out
    q, partner = hits[0]
    mirrored = (_close(partner, -line) if fam == SPREAD
                else _close(partner, line))
    if not mirrored:
        out.update(refusal=R_PAIR_NOT_MIRRORED, key=q.key,
                   points=dict(q.points or {}))
        return out
    want = {"home", "away"} if fam == SPREAD else {"over", "under"}
    dec = q.decimal_prices()
    if set(dec) != want or any(v is None or v <= 1.0 for v in dec.values()):
        out.update(refusal=R_PAIR_INCOMPLETE, key=q.key,
                   designations=sorted(dec))
        return out
    got = cache.read(qid, q.key, evaluated_ms=evaluated_ms,
                     max_age_s=max_age_s)
    out.update(key=q.key, quote_event_id=qid, alternate=bool(q.alternate),
               stream=q.stream, provenance=got.get("provenance"))
    if not got.get("ok"):
        out["refusal"] = got.get("reason")
        return out
    q = got["quote"]
    sel, oth = outcome_names(contract, labels)
    if fam == SPREAD:
        sel_d, oth_d = des, other(des)
    else:
        sel_d, oth_d = "over", "under"
    dec = q.decimal_prices()
    out.update(ok=True, labels=labels, selection=sel, other=oth,
               outcomes={sel: dec[sel_d], oth: dec[oth_d]},
               designations={sel: sel_d, oth: oth_d},
               raw_odds=dict(q.prices), points=dict(q.points or {}),
               line=line, epoch=q.epoch,
               change_ms=q.change_ms,
               source_change_ms=q.source_change_ms,
               observed_at=q.change_ms / 1000.0,
               received_at=q.received_ms / 1000.0)
    return out


def validate_pair(cache, pair, *, evaluated_ms, max_age_s=30.0) -> dict:
    """THE RECHECK AT THE DECISION INSTANT, after the awaited venue reads:
    the same record still prices the fixture, the same market is still held
    on the same epoch with the same change instant, prices and points, and it
    is still inside the unchanged 30 s rule. Anything else refuses by name;
    the caller then removes the probability (never values an old price)."""
    p = dict(pair or {})
    if not p.get("ok"):
        return {"ok": False, "reason": p.get("refusal") or R_INPUT_CHANGED}
    if cache is None:
        return {"ok": False, "reason": F.R_NO_AUTHORITY}
    qid, why = cache.fixture_quote_id(p.get("fixture_id"))
    if why or qid != p.get("quote_event_id"):
        return {"ok": False, "reason": why or R_INPUT_CHANGED}
    got = cache.read(qid, p.get("key"), evaluated_ms=evaluated_ms,
                     max_age_s=max_age_s)
    if not got.get("ok"):
        return {"ok": False, "reason": got.get("reason"),
                "provenance": got.get("provenance")}
    q = got["quote"]
    if (q.epoch != p.get("epoch") or q.change_ms != p.get("change_ms")
            or dict(q.prices) != p.get("raw_odds")
            or dict(q.points or {}) != p.get("points")):
        return {"ok": False, "reason": R_INPUT_CHANGED}
    return {"ok": True, "provenance": got["provenance"]}


def validate_reference(cache, ref, *, at, max_age_s=30.0,
                       runtime_id=None) -> dict:
    """`validate_pair` from a line valuation's PERSISTED reference (the
    paper policies' recheck at their own decision instant, as
    `pinnapi_primary.validate` is for a money line): the same runtime (a
    reconnect or a restart is a new epoch, never served), the same record,
    market, epoch, change instant, prices and points, inside the 30 s rule."""
    r = dict(ref or {})
    if r.get("version") != VERSION:
        return {"ok": False, "reason": "PINNAPI_LINE_PROVENANCE_INVALID"}
    if cache is None or not runtime_id or runtime_id != r.get("runtime_id"):
        return {"ok": False, "reason": F.R_NO_AUTHORITY}
    pair = {"ok": True, "fixture_id": r.get("feed_event_id"),
            "quote_event_id": r.get("quote_event_id"),
            "key": r.get("market_key"), "epoch": r.get("epoch"),
            "change_ms": r.get("change_ms"), "raw_odds": r.get("raw_odds"),
            "points": r.get("points")}
    return validate_pair(cache, pair, evaluated_ms=float(at) * 1000.0,
                         max_age_s=max_age_s)


def instrument_order(item) -> tuple:
    """The order line instruments are evaluated in: Pinnacle's MAIN lines
    before its alternates, then spread / total / team total, then the long
    side before the short -- a property of the data, never of the edge."""
    pair, contract, inst = item
    return (bool((pair or {}).get("alternate")),
            LINE_FAMILIES.index(contract.get("family"))
            if contract.get("family") in LINE_FAMILIES else 9,
            str(contract.get("market_slug") or ""),
            0 if inst.get("intent") == INTENT_LONG else 1)


def describe() -> dict:
    return {"version": VERSION, "families": list(LINE_FAMILIES),
            "proven": ["%s/%s" % k for k in PROVEN],
            "not_proven": {"%s/%s" % k: v["refusal"]
                           for k, v in sorted(NOT_PROVEN.items())},
            "venue_line_types": dict(sorted(
                (k, "%s/%s" % v) for k, v in VENUE_LINE_TYPES.items())),
            "half_point_only": True, "exact_line_only": True,
            "book_capture": dict(BOOK_CAPTURE),
            "venue_capture": dict(VENUE_CAPTURE),
            "refusals": list(REFUSALS)}
