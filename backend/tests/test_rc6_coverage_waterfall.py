"""RC6 LANE D2: THE COVERAGE WATERFALL AND THE FULL-GAME / PERIOD LINE
TERMS, ON PRODUCTION ROWS.

Two measurement defects, each shown against rows production holds
(tests/fixtures/pmus_line_terms_rows_2026_10_09.json, research-sql runs
37871400151 / 37872672403; the slug grammar is
tests/test_rc6_coverage_slug_grammar.py):

  1 a never-valued full-game or period spread / total / team total was
    reported BOOKMAKER_TERMS_NOT_HELD although the book's grading of those
    families is captured and cited (bettor_market_family.EQUIVALENCE; the
    general period rule) -- 22,683 contracts read established on ordinary
    completion by the after-projection (research-sql run 37872888350).
  2 nothing in the packet said which contracts are the target universe or
    at which stage each stopped: market_plane.waterfall.

Evidence only throughout: no state here can become PROVEN or PRICEABLE that
was not before, and no gate, threshold or policy is read or written.
"""
from __future__ import annotations

import ast
import asyncio
import datetime as _dt
import json
import os
import pathlib
import time

import asyncpg
import pytest

from sportsassets import bettor_market_family as MF
from sportsassets import bettor_settlement_difference_policy as SDP
from sportsassets.market_plane import populate as POP
from sportsassets.market_plane import rules as RULES
from sportsassets.market_plane import settlement as S
from sportsassets.market_plane import waterfall as WF
from sportsassets.workers import universal_market_plane as W

HERE = pathlib.Path(__file__).resolve().parent
FIX = HERE / "fixtures"
ROWS = json.loads((FIX / "pmus_line_terms_rows_2026_10_09.json")
                  .read_text())["rows"]
BY_ID = {r["contract_id"]: r for r in ROWS}
MLB_H2H = json.loads((FIX / "pmus_live_description_mlb_h2h_2026_09_25.json")
                     .read_text())["markets"][0]
DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


def run(coro):
    return asyncio.run(coro)


def _rules(text, *, sha=True):
    return {"venue": "POLYMARKET_US", "rules_published": True,
            "rules_field": "description",
            "rules_sha256": RULES.SRR.fingerprint(text) if sha else None,
            "parse_status": "ESTABLISHED", "evidence": {},
            "rules_text": text}


def _contract(row, **over):
    c = {"contract_id": row["contract_id"], "venue": "POLYMARKET_US",
         "sport": row.get("sport"), "family": row.get("family"),
         "period": row.get("period"), "market_type": row["market_type"],
         "event_id": row["event_id"],
         "competition": POP.league_of(row["event_id"])}
    c.update(over)
    return c


@pytest.fixture(autouse=True)
def _cold_caches():
    getattr(S, "_LINE_CACHE", {}).clear()
    S._TERMS_CACHE.clear()
    yield
    getattr(S, "_LINE_CACHE", {}).clear()
    S._TERMS_CACHE.clear()


# ═════════════════════════════════════════════════════════════════════
# 2. FULL-GAME LINE TERMS
# ═════════════════════════════════════════════════════════════════════

X_FB = ("POSTPONED_OR_SUSPENDED_AND_NOT_COMPLETED,SUSPENDED_AFTER_55_MINUTES,"
        "FEWER_THAN_55_MINUTES_PLAYED")
X_HK = "POSTPONED_OR_SUSPENDED_AND_NOT_COMPLETED,FEWER_THAN_55_MINUTES_PLAYED"
X_BK = ("POSTPONED_OR_SUSPENDED_AND_NOT_COMPLETED,"
        "NBA_FEWER_THAN_43_MINUTES_COMPLETED")
X_BB = ("POSTPONED_OR_SUSPENDED_AND_NOT_COMPLETED,"
        "SHORTENED_GAME_DECLARED_OFFICIAL")
DIFF = "LINE_EXCEPTIONAL_SETTLEMENT_TERMS_DIFFER"
POL = SDP.R_LINE

#: every full-game line row production holds in the fixture -> its reading
EXPECTED = {
    "asc-cfb-airf-nill-2026-10-10-neg-10pt5":
        "%s:football/spread:%s:%s" % (DIFF, POL, X_FB),
    "tsc-cfb-airf-nill-2026-10-10-total-19pt5":
        "%s:football/total:%s:%s" % (DIFF, POL, X_FB),
    "tsc-cfb-airf-nill-2026-10-10-tt-airf-16pt5":
        "%s:football/team_total:%s:%s" % (DIFF, POL, X_FB),
    "asc-nhl-ana-cgy-2026-10-10-neg-1pt5":
        "%s:hockey/spread:%s:%s" % (DIFF, POL, X_HK),
    "tsc-nhl-ana-cgy-2026-10-10-2pt5":
        "%s:hockey/total:%s:%s" % (DIFF, POL, X_HK),
    "tsc-nhl-ana-cgy-2026-10-10-tt-ana-0pt5":
        "%s:hockey/team_total:%s:%s" % (DIFF, POL, X_HK),
    "asc-nba-atl-orl-2026-10-21-pos-2pt5":
        "%s:basketball/spread:%s:%s" % (DIFF, POL, X_BK),
    "tsc-nba-atl-orl-2026-10-21-233pt5":
        "%s:basketball/total:%s:%s" % (DIFF, POL, X_BK),
    "asc-mlb-cle-cws-2026-10-08-neg-1pt5":
        "%s:baseball/spread:%s:%s" % (DIFF, POL, X_BB),
    "tsc-mlb-cle-cws-2026-10-08-6pt5":
        "%s:baseball/total:%s:%s" % (DIFF, POL, X_BB),
    "tsc-mlb-cle-cws-2026-10-08-tt-cle-2pt5":
        "%s:baseball/team_total:%s:%s" % (DIFF, POL, X_BB),
    # the book side genuinely not held: named, with the module's own code
    "asc-bun-aug-fcb-2026-10-10-neg-1pt5":
        "BOOKMAKER_TERMS_NOT_HELD:BOOK_MARKET_RULES_SECTION_NOT_CAPTURED:"
        "soccer/spread",
    "asc-atp-abeshe-dangli-2026-10-09-gs-neg-1pt5":
        "BOOKMAKER_TERMS_NOT_HELD:BOOK_RULES_FOR_THIS_SPORT_NOT_CAPTURED:"
        "tennis/spread",
}


@pytest.mark.parametrize("cid", sorted(EXPECTED))
def test_a_full_game_line_contract_is_read_against_its_family(cid):
    r = BY_ID[cid]
    st = S.state_for(_contract(r), rules=_rules(r["rules_text"]),
                     rules_looked_up=True, derivative_terms=True)
    assert st["why"] == EXPECTED[cid]
    assert st["state"] == S.NOT_PROVEN and not st["proven"]
    assert st["basis"] == S.BASIS_RULES_TERMS
    lt = st["evidence"]["line_terms"]
    if cid.startswith(("asc-bun", "asc-atp")):
        assert lt["established"] is False and lt["side"] == "BOOK"
    else:
        assert lt["established"] is True
        assert lt["period"] == MF.EQUIVALENCE[(lt["sport"],
                                               lt["family"])]["period"]
        assert lt["venue_exceptional_clause"] in (
            "LAST_FAIR_MARKET_PRICE",
            "LAST_FAIR_MARKET_PRICE_FLAGGED_FOR_MANUAL_SETTLEMENT")
        assert st["evidence"]["difference_policy"]["refusal"] == SDP.R_LINE


@pytest.mark.parametrize("cid", sorted(EXPECTED))
def test_without_the_switch_the_reading_is_rc5s(cid):
    """derivative_terms=False is the RC5 reading, byte for byte -- the label
    production carried (pm-acceptance 37836393458 top_reasons)."""
    r = BY_ID[cid]
    c = _contract(r)
    st = S.state_for(c, rules=_rules(r["rules_text"]), rules_looked_up=True)
    assert st["why"] == "BOOKMAKER_TERMS_NOT_HELD:%s/%s/%s" % (
        c.get("sport") or "UNKNOWN_SPORT", c.get("family") or
        "UNKNOWN_FAMILY", c.get("period") or "UNKNOWN_PERIOD")
    assert "line_terms" not in st["evidence"]


def test_the_team_total_wording_flagged_for_manual_settlement_is_read():
    r = BY_ID["tsc-cfb-airf-nill-2026-10-10-tt-airf-16pt5"]
    st = S.state_for(_contract(r), rules=_rules(r["rules_text"]),
                     rules_looked_up=True, derivative_terms=True)
    assert st["evidence"]["line_terms"]["venue_exceptional_clause"] == \
        "LAST_FAIR_MARKET_PRICE_FLAGGED_FOR_MANUAL_SETTLEMENT"


def _variant(cid, text=None, slug=None):
    r = dict(BY_ID[cid])
    t = r["rules_text"] if text is None else text
    c = _contract(r, contract_id=slug or r["contract_id"])
    return S.state_for(c, rules=_rules(t), rules_looked_up=True,
                       derivative_terms=True)


FB_SPREAD = "asc-cfb-airf-nill-2026-10-10-neg-10pt5"


def test_a_contradicting_overtime_sentence_refuses():
    t = BY_ID[FB_SPREAD]["rules_text"] + " Overtime is not included."
    assert _variant(FB_SPREAD, t)["why"] == \
        "VENUE_LINE_TEXT_CONFLICTS_WITH_THE_BOOK_GRADING:football/spread"


def test_a_text_whose_line_is_not_the_slugs_refuses():
    t = BY_ID[FB_SPREAD]["rules_text"].replace("-10.5", "-9.5")
    assert _variant(FB_SPREAD, t)["why"] == \
        "VENUE_LINE_TEXT_LINE_DIFFERS_FROM_THE_CONTRACT_LINE:football/spread"


def test_a_missing_required_phrase_refuses():
    t = BY_ID[FB_SPREAD]["rules_text"].replace(
        "Overtime is included if played. ", "")
    assert _variant(FB_SPREAD, t)["why"] == \
        "VENUE_LINE_TEXT_DOES_NOT_STATE_THE_CAPTURED_TERMS:football/spread"


def test_a_whole_line_is_a_push_question_and_refuses():
    st = _variant(FB_SPREAD, slug="asc-cfb-airf-nill-2026-10-10-neg-10pt0")
    assert st["why"] == ("LINE_NOT_A_HALF_POINT_PUSH_HANDLING_NOT_PROVEN_"
                         "EQUIVALENT:football/spread")


def test_a_slug_without_a_line_refuses():
    st = _variant(FB_SPREAD, slug="asc-cfb-airf-nill-2026-10-10")
    assert st["why"] == "VENUE_LINE_CONTRACT_LINE_NOT_READABLE:football/spread"


def test_a_text_silent_on_the_exceptional_states_says_so():
    t = BY_ID[FB_SPREAD]["rules_text"]
    t = t.split(" If the game is delayed")[0] + " Outcome sourced from NCAA."
    st = _variant(FB_SPREAD, t)
    assert st["why"] == ("LINE_VENUE_TEXT_STATES_NO_EXCEPTIONAL_RULE:"
                         "football/spread:" + X_FB)
    assert not st["proven"]


def test_a_text_not_loaded_is_not_captured():
    r = BY_ID[FB_SPREAD]
    rules = _rules(r["rules_text"])
    rules["rules_text"] = None
    st = S.state_for(_contract(r), rules=rules, rules_looked_up=True,
                     derivative_terms=True)
    assert st["why"] == S.R_VENUE_RULES_NOT_CAPTURED


def test_a_valued_line_contract_keeps_its_decision_record():
    r = BY_ID[FB_SPREAD]
    st = S.state_for(_contract(r), rules=_rules(r["rules_text"]),
                     rules_looked_up=True, derivative_terms=True,
                     valuation={"settlement_verdict":
                                "INCOMPATIBLE_EXCEPTIONAL_TERMS",
                                "refusals": [MF.R_EXCEPTIONAL_DIFFER]})
    assert st["why"] == "SETTLEMENT_VERDICT:INCOMPATIBLE_EXCEPTIONAL_TERMS"
    assert st["basis"] == S.BASIS_DECISION_ATTEST


PX = "PERIOD_NOT_COMPLETED_OR_POSTPONED_BEFORE_IT_IS"
EXPECTED_PERIOD = {
    "asc-cfb-airf-nill-2026-10-10-1h-neg-4pt5":
        "%s:football/spread@FIRST_HALF:%s:%s" % (DIFF, POL, PX),
    "asc-cfb-airf-nill-2026-10-10-2h-neg-4pt5":
        "%s:football/spread@SECOND_HALF:%s:%s" % (DIFF, POL, PX),
    "tsc-cfb-airf-nill-2026-10-10-1q-10pt5":
        "%s:football/total@Q1:%s:%s" % (DIFF, POL, PX),
    "tsc-cfb-airf-nill-2026-10-10-2h-21pt5":
        "%s:football/total@SECOND_HALF:%s:%s" % (DIFF, POL, PX),
    "tsc-cfb-airf-nill-2026-10-10-tt1h-airf-11pt5":
        "%s:football/team_total@FIRST_HALF:%s:%s" % (DIFF, POL, PX),
    "asc-nhl-ana-cgy-2026-10-10-p3-neg-0pt5":
        "%s:hockey/spread@P3:%s:%s" % (DIFF, POL, PX),
    "tsc-nhl-ana-cgy-2026-10-10-p1-0pt5":
        "%s:hockey/total@P1:%s:%s" % (DIFF, POL, PX),
    # the NFL board's period-total wording (37872672403 W2)
    "tsc-nfl-ari-lar-2026-10-18-1h-16pt5":
        "%s:football/total@FIRST_HALF:%s:%s" % (DIFF, POL, PX),
    "tsc-nfl-ari-lar-2026-10-18-1q-0pt5":
        "%s:football/total@Q1:%s:%s" % (DIFF, POL, PX),
    "tsc-nfl-was-sf-2026-10-19-4q-8pt5":
        "%s:football/total@Q4:%s:%s" % (DIFF, POL, PX),
    "tsc-nfl-was-sf-2026-10-19-2h-32pt5":
        "%s:football/total@SECOND_HALF:%s:%s" % (DIFF, POL, PX),
}


@pytest.mark.parametrize("cid", sorted(EXPECTED_PERIOD))
def test_a_period_line_is_read_against_the_cited_period_rule(cid):
    r = BY_ID[cid]
    st = S.state_for(_contract(r), rules=_rules(r["rules_text"]),
                     rules_looked_up=True, derivative_terms=True)
    assert st["why"] == EXPECTED_PERIOD[cid]
    assert not st["proven"]
    lt = st["evidence"]["line_terms"]
    assert lt["established"] is True and lt["segment"] == r["period"]
    assert lt["book"][:2] == ["general_period", "general_period_action"]


def _period_variant(text, slug, market_type, period):
    c = {"contract_id": slug, "venue": "POLYMARKET_US", "sport": "football",
         "family": "MARGIN" if "spread" in market_type else "TOTAL",
         "period": period, "market_type": market_type, "competition": "cfb"}
    return S.state_for(c, rules=_rules(text), rules_looked_up=True,
                       derivative_terms=True)


def test_a_fourth_quarter_spread_silent_on_overtime_is_not_read_as_excluding_it():
    """The production Q4 spread wording (research-sql 37870767576 T2) states
    no overtime rule; the book's period-only rule excludes overtime, so the
    venue's silence is refused, never filled in. Built from the real 1st-half
    spread text, the period words swapped as the venue's own Q4 text reads."""
    t = BY_ID["asc-cfb-airf-nill-2026-10-10-1h-neg-4pt5"]["rules_text"] \
        .replace("first half", "fourth quarter")
    st = _period_variant(t, "asc-cfb-airf-nill-2026-10-10-4q-neg-4pt5",
                         "football_team_fourth_quarter_spread", "Q4")
    assert st["why"] == ("VENUE_LINE_TEXT_DOES_NOT_STATE_THE_CAPTURED_TERMS:"
                         "football/spread@Q4")


def test_a_fourth_quarter_total_that_excludes_overtime_is_read():
    t = BY_ID["tsc-cfb-airf-nill-2026-10-10-1q-10pt5"]["rules_text"] \
        .replace("1st Quarter", "4th Quarter") \
        .replace("first quarter", "fourth quarter")
    st = _period_variant(t, "tsc-cfb-airf-nill-2026-10-10-4q-10pt5",
                         "football_game_fourth_quarter_total", "Q4")
    assert st["why"] == "%s:football/total@Q4:%s:%s" % (DIFF, POL, PX)


def test_a_second_half_text_that_excludes_overtime_conflicts():
    t = BY_ID["tsc-cfb-airf-nill-2026-10-10-2h-21pt5"]["rules_text"] \
        .replace("Overtime is included if played.",
                 "Only points recorded in regulation during that period "
                 "count; overtime is not included.")
    st = _period_variant(t, "tsc-cfb-airf-nill-2026-10-10-2h-21pt5",
                         "football_game_second_half_total", "SECOND_HALF")
    assert st["why"] == ("VENUE_LINE_TEXT_CONFLICTS_WITH_THE_BOOK_GRADING:"
                         "football/total@SECOND_HALF")


def test_a_soccer_half_line_names_the_uncaptured_book_section():
    r = BY_ID["asc-bun-aug-fcb-2026-10-10-neg-1pt5"]
    c = _contract(r, contract_id="asc-bun-aug-fcb-2026-10-10-fh-neg-1pt5",
                  market_type="soccer_team_first_half_spread",
                  period="FIRST_HALF")
    st = S.state_for(c, rules=_rules(r["rules_text"]), rules_looked_up=True,
                     derivative_terms=True)
    assert st["why"] == ("BOOKMAKER_TERMS_NOT_HELD:BOOK_MARKET_RULES_SECTION_"
                         "NOT_CAPTURED:soccer/spread@FIRST_HALF")


def test_period_winners_are_not_claimed():
    """The book's tie payout depends on whether its own market offers a
    draw (General Rules); no rule text establishes it: the RC5 label."""
    c = {"contract_id": "atc-cfb-airf-nill-2026-10-10-winner-1h-airf",
         "venue": "POLYMARKET_US", "sport": "football", "family": "WINNER",
         "period": "FIRST_HALF",
         "market_type": "football_team_first_half_winner",
         "competition": "cfb"}
    st = S.state_for(c, rules=_rules("1st Half Winner settles Yes if Air "
                                      "Force outscores Northern Illinois in "
                                      "the first half."),
                     rules_looked_up=True, derivative_terms=True)
    assert st["why"] == "BOOKMAKER_TERMS_NOT_HELD:football/WINNER/FIRST_HALF"
    assert "line_terms" not in st["evidence"]


def test_the_period_book_sentences_are_verbatim_in_the_capture():
    cap = json.loads((FIX / "pinnacle_line_rules_2026_10_04.json")
                     .read_text())["sections"]
    for key, (section, quote) in S.PERIOD_BOOK.items():
        assert any(quote in line for line in cap[section]), key


def test_the_reading_is_cached_by_text_family_and_line():
    r = BY_ID[FB_SPREAD]
    sha = RULES.SRR.fingerprint(r["rules_text"])
    a = S.state_for(_contract(r), rules=_rules(r["rules_text"]),
                    rules_looked_up=True, derivative_terms=True)
    rules = _rules(r["rules_text"])
    rules["rules_text"] = ""                   # the pass does not re-read it
    b = S.state_for(_contract(r), rules=rules, rules_looked_up=True,
                    derivative_terms=True)
    assert a["why"] == b["why"]
    assert (sha, "football", "spread", -10.5, None) in S._LINE_CACHE


def test_a_money_line_withheld_for_scope_says_scope_not_capture():
    """The book's baseball money-line terms ARE captured (BOOK_TERMS), but
    the comparison is never told the fixture's context, phase or format;
    book_terms() withholds them by design."""
    c = {"contract_id": MLB_H2H["slug"], "venue": "POLYMARKET_US",
         "sport": "baseball", "family": "WINNER", "period": "FULL_EVENT",
         "market_type": "baseball_team_full_game_winner",
         "competition": "mlb"}
    rules = _rules(MLB_H2H["description"])
    old = S.state_for(c, rules=rules, rules_looked_up=True)
    new = S.state_for(c, rules=rules, rules_looked_up=True,
                      derivative_terms=True)
    refs = ("QUOTE_CONTEXT_NOT_ESTABLISHED,COMPETITION_PHASE_NOT_ESTABLISHED,"
            "GAME_FORMAT_NOT_ESTABLISHED")
    assert old["why"] == "BOOKMAKER_TERMS_NOT_HELD:" + refs
    assert new["why"] == "BOOK_TERMS_SCOPE_NOT_ESTABLISHED:" + refs
    assert not new["proven"]


def test_a_family_with_no_capture_keeps_not_held():
    c = {"contract_id": "aec-atp-a-b-2026-10-09", "venue": "POLYMARKET_US",
         "sport": "tennis", "family": "WINNER", "period": "FULL_EVENT",
         "market_type": "tennis_match_winner", "competition": "atp"}
    st = S.state_for(c, rules=_rules("This market will settle to the "
                                      "winner."), rules_looked_up=True,
                     derivative_terms=True)
    assert st["why"] == "BOOKMAKER_TERMS_NOT_HELD:tennis/WINNER/FULL_EVENT"


def test_nothing_with_the_switch_reaches_a_proven_state():
    for r in ROWS:
        st = S.state_for(_contract(r), rules=_rules(r["rules_text"]),
                         rules_looked_up=True, derivative_terms=True)
        assert st["state"] not in S.PROVEN_STATES, r["contract_id"]


# ═════════════════════════════════════════════════════════════════════
# 3. THE WATERFALL
# ═════════════════════════════════════════════════════════════════════

#: production market types (research-sql 37869809674 B) -> tier, family
TYPES = (
    ("football_team_full_game_winner", "aec-nfl-a-b", WF.TARGET_A,
     WF.MONEYLINE),
    ("soccer_team_full_time_winner", "atc-epl-a-b-x", WF.TARGET_A,
     WF.MONEYLINE),
    ("football_team_full_game_spread", FB_SPREAD, WF.TARGET_A, WF.SPREAD),
    ("tennis_match_games_spread", "asc-atp-a-b-gs-neg-1pt5", WF.TARGET_A,
     WF.SPREAD),
    ("football_team_full_game_total", "tsc-cfb-a-b-total-19pt5",
     WF.TARGET_A, WF.TOTAL),
    ("esports_series_total_maps", "tsc-cs2-a-b-tot-2pt5", WF.TARGET_A,
     WF.TOTAL),
    ("football_team_points_full_game_total",
     "tsc-cfb-airf-nill-2026-10-10-tt-airf-16pt5", WF.TARGET_A,
     WF.TEAM_TOTAL),
    ("hockey_team_total_goals", "tsc-nhl-ana-cgy-2026-10-10-tt-ana-0pt5",
     WF.TARGET_A, WF.TEAM_TOTAL),
    ("baseball_team_total_runs", "tsc-mlb-cle-cws-2026-10-08-tt-cle-2pt5",
     WF.TARGET_A, WF.TEAM_TOTAL),
    ("football_team_first_half_spread",
     "asc-cfb-airf-nill-2026-10-10-1h-neg-4pt5", WF.TARGET_B, WF.SPREAD),
    ("football_team_first_half_total",
     "tsc-cfb-airf-nill-2026-10-10-tt1h-airf-11pt5", WF.TARGET_B,
     WF.TEAM_TOTAL),
    ("football_game_first_quarter_total",
     "tsc-cfb-airf-nill-2026-10-10-1q-10pt5", WF.TARGET_B, WF.TOTAL),
    ("hockey_team_regulation_winner", "atc-nhl-a-b-reg-a", WF.TARGET_B,
     WF.MONEYLINE),
    ("table_tennis_set_2_winner", "x", WF.TARGET_B, WF.MONEYLINE),
    ("esports_map_winner_1", "astatc-cs2-a-b-map1", WF.TARGET_B,
     WF.MONEYLINE),
    ("baseball_team_inning3_winner", "atc-mlb-a-b-i3-a", WF.TARGET_B,
     WF.MONEYLINE),
    ("hockey_player_points", "x", WF.EXCLUDED, WF.X_PLAYER_PROP),
    ("football_team_total_touchdowns", "x", WF.EXCLUDED, WF.X_OTHER_PROP),
    ("soccer_game_total_corners", "x", WF.EXCLUDED, WF.X_OTHER_PROP),
    ("soccer_game_exact_score", "x", WF.EXCLUDED, WF.X_OTHER_PROP),
    ("futures", "x", WF.EXCLUDED, WF.X_OUTRIGHT),
)


@pytest.mark.parametrize("mt,cid,tier,fam", TYPES)
def test_a_pmus_market_type_has_one_tier(mt, cid, tier, fam):
    k = WF.classify({"venue": "POLYMARKET_US", "market_type": mt,
                     "contract_id": cid, "event_id": "nfl-a-b-2026"})
    assert (k["tier"], k["family"]) == (tier, fam)


def test_non_sports_and_untyped_rows_are_named():
    k = WF.classify({"venue": "POLYMARKET_US", "market_type": None,
                     "event_id": "btc-range-hr-2026-10-09-0200z",
                     "competition": "range"})
    assert (k["tier"], k["family"]) == (WF.EXCLUDED, WF.X_NON_SPORTS)
    k = WF.classify({"venue": "POLYMARKET_US", "market_type": None,
                     "event_id": "nfl-a-b-2026"})
    assert (k["tier"], k["family"]) == (WF.UNCLASSIFIED, WF.NO_MARKET_TYPE)


@pytest.mark.parametrize("series,tier,fam", (
    ("KXNCAAFGAME", WF.TARGET_A, WF.MONEYLINE),
    ("KXNFLSPREAD", WF.TARGET_A, WF.SPREAD),
    ("KXNCAAFTOTAL", WF.TARGET_A, WF.TOTAL),
    ("KXNFLTEAMTOTAL", WF.TARGET_A, WF.TEAM_TOTAL),
    ("KXNCAAF1HSPREAD", WF.TARGET_B, WF.SPREAD),
    ("KXNFL4QTOTAL", WF.TARGET_B, WF.TOTAL),
    ("KXNFL1HTEAMTOTAL", WF.TARGET_B, WF.TEAM_TOTAL),
    ("KXNFLRECYDS", WF.EXCLUDED, WF.X_KALSHI_SERIES),
    ("KXNCAAFAWARD", WF.EXCLUDED, WF.X_KALSHI_SERIES)))
def test_a_kalshi_series_is_tiered_by_its_ticker_only(series, tier, fam):
    k = WF.classify({"venue": "KALSHI", "competition": series,
                     "market_type": "binary"})
    assert (k["tier"], k["family"], k["basis"]) == (tier, fam,
                                                   WF.BASIS_KALSHI)


def test_the_stage_is_terminals_own():
    E = {"mapped": True}
    assert WF.stage_of("PRICEABLE") == WF.PRICEABLE
    assert WF.stage_of("EXTERNAL_DATA_UNAVAILABLE") == WF.LOST_EXTERNAL
    assert WF.stage_of("MAPPED_BUT_SETTLEMENT_NOT_PROVEN") == \
        WF.LOST_SETTLEMENT
    assert WF.stage_of("MAPPED_BUT_NO_FAIR_VALUE_SOURCE") == \
        WF.LOST_FAIR_VALUE
    assert WF.stage_of("CODE_CONTROLLED_GAP", "ONTOLOGY_GAPS:X",
                       {"mapped": False}) == WF.LOST_MAPPING
    assert WF.stage_of("CODE_CONTROLLED_GAP", "NO_CURRENT_CANONICAL_BOOK",
                       E) == WF.LOST_FRESH_BOOK
    assert WF.stage_of("CODE_CONTROLLED_GAP",
                       "NO_CURRENT_CANONICAL_BOOK") == WF.LOST_FRESH_BOOK
    assert WF.stage_of(None) == WF.LOST_MAPPING
    assert WF.stage_of("SOMETHING_NEW") == WF.LOST_MAPPING


def test_the_waterfall_sums_exactly_and_is_bounded():
    from sportsassets.market_plane.populate import classify
    w = WF.Waterfall(now=1_800_000_000.0)
    n = 0
    for i, (mt, cid, _t, _f) in enumerate(TYPES * 40):
        c = {"contract_id": "%s-%d" % (cid, i), "venue": "POLYMARKET_US",
             "sport": "football", "competition": "nfl",
             "event_id": "nfl-a-b-2026", "family": "WINNER",
             "period": "FULL_EVENT", "market_type": mt,
             "ontology": {"gaps": ["SPORT_NOT_NORMALIZED"] if i % 7 == 0
                          else []},
             "event_start": 1_800_000_000.0 + (i % 5) * 20 * 3600.0}
        val = {"has_probability": i % 3 == 0, "settlement_verdict":
               "COMPATIBLE" if i % 4 == 0 else None}
        t = classify(c, valuation=val, fresh_book=i % 2 == 0,
                     settlement={"state": S.COMPATIBLE if i % 4 == 0
                                 else S.NOT_PROVEN, "why": None if i % 4
                                 == 0 else "X%d" % (i % 50),
                                 "basis": "B", "proven": i % 4 == 0})
        w.add(c, t, valued=val["has_probability"])
        n += 1
    for i in range(5):
        w.add({"venue": "KALSHI", "competition": "KXNFLGAME",
               "market_type": "binary"},
              {"state": "CODE_CONTROLLED_GAP",
               "why": "ONTOLOGY_GAPS:KALSHI_ONTOLOGY_NOT_MAPPED",
               "evidence": {"mapped": False}})
        n += 1
    out = w.result()
    assert out["sums_exact"] is True and out["catalogue"] == n
    for t, row in out["tiers"].items():
        assert row["catalogue"] == sum(row[s] for s in WF.STAGES)
        r = row["reached"]
        assert r["CATALOGUE"] >= r["MAPPED"] >= r["SETTLEMENT_SUPPORTED"] \
            >= r["FAIR_VALUE_SUPPORTED"] >= r["FRESH_PRICEABLE"] == \
            row[WF.PRICEABLE]
    assert out["tiers"][WF.TARGET_A]["catalogue"] + \
        out["tiers"][WF.TARGET_B]["catalogue"] + \
        out["tiers"][WF.EXCLUDED]["catalogue"] + \
        out["tiers"][WF.UNCLASSIFIED]["catalogue"] == n
    assert out["by_venue_tier"]["KALSHI|TARGET_A"]["catalogue"] == 5
    assert len(out["reasons"]) <= WF.MAX_REASON_KEYS
    assert len(out["by_target_sport_family"]) <= WF.MAX_SPORT_FAMILY_KEYS
    assert out["universe"]["source"]["file"] == \
        "research/codex_continuous_coverage_readiness.md"
    assert len(json.dumps(out)) < 40_000


def test_the_decision_horizon_is_read_and_only_reported():
    """collector_coverage's horizon (-6 h .. +24 h) is reported beside the
    48 h window; a contract outside it stays in every denominator."""
    from sportsassets import collector_coverage as CC
    now = 1_800_000_000.0
    w = WF.Waterfall(now=now)
    t = {"state": "MAPPED_BUT_SETTLEMENT_NOT_PROVEN", "why": "X",
         "evidence": {"mapped": True}}
    for h in (-7.0, -5.0, 12.0, 23.9, 30.0, 47.0, 60.0):
        w.add({"venue": "POLYMARKET_US", "market_type":
               "football_team_full_game_winner", "event_id": "nfl-a-b",
               "event_start": now + h * 3600.0}, t)
    a = w.result()["tiers"][WF.TARGET_A]
    assert a["catalogue"] == 7 and a[WF.LOST_SETTLEMENT] == 7
    assert a["in_decision_horizon"] == 3            # -5, 12, 23.9
    assert a["within_48h"] == 5                     # -5 .. 47
    assert w.result()["decision_horizon_s"] == {
        "behind": CC.HORIZON_BEHIND_S, "ahead": CC.HORIZON_AHEAD_S,
        "source": ("collector_coverage.HORIZON_BEHIND_S / HORIZON_AHEAD_S "
                   "(read)")}


def test_the_universe_source_is_quoted_verbatim():
    text = (HERE.parents[1] / WF.SOURCE["file"]).read_text()
    flat = " ".join(text.split()).replace("**", "")
    assert WF.SOURCE["quote"] in flat


# ═════════════════════════════════════════════════════════════════════
# 4. THE PASS AND THE PLANE
# ═════════════════════════════════════════════════════════════════════

class _Rec:
    """A connection that records the SQL coverage_pass sends and serves an
    empty registry (the switch's effect on the reads, nothing else)."""

    def __init__(self):
        self.sql = []

    def is_in_transaction(self):
        return False

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if sql.strip().startswith("SELECT contract_id FROM "
                                  "market_plane_registry"):
            return [{"contract_id": "k"}]          # one key, no row behind it
        return []

    async def fetchval(self, sql, *a):
        return 1


def test_without_the_switches_the_pass_reads_and_returns_rc5s():
    c = _Rec()
    out = run(POP.coverage_pass(c, now=1_800_000_000.0))
    assert "waterfall" not in out
    assert POP.COVERAGE_ROWS_SQL in c.sql
    assert not any("market_type" in s for s in c.sql)
    c2 = _Rec()
    out2 = run(POP.coverage_pass(c2, now=1_800_000_000.0,
                                 derivative_terms=True, waterfall=True))
    assert POP.COVERAGE_ROWS_SQL_RC6 in c2.sql
    assert out2["waterfall"]["catalogue"] == 0
    assert out2["waterfall"]["sums_exact"] is True


def test_the_plane_passes_both_switches_and_can_turn_them_off(monkeypatch):
    src = pathlib.Path(W.__file__).read_text()
    call = [n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "")
            == "coverage_pass"]
    assert len(call) == 1
    kws = {k.arg for k in call[0].keywords}
    assert {"derivative_terms", "waterfall"} <= kws
    monkeypatch.delenv(W.COVERAGE_WATERFALL_ENV, raising=False)
    assert W.coverage_waterfall_on() is True
    for v in ("off", "0", "false", "OFF"):
        monkeypatch.setenv(W.COVERAGE_WATERFALL_ENV, v)
        assert W.coverage_waterfall_on() is False


async def _seed(c, slug, *, event, sports_type, league):
    now = _dt.datetime.now(_dt.timezone.utc)
    for intent, side in (("ORDER_INTENT_BUY_LONG", "a"),
                         ("ORDER_INTENT_BUY_SHORT", "b")):
        await c.execute(
            "INSERT INTO us_premap (identifier, event_slug, market_slug, kind, "
            " side_norm, intent, sports_type, listing_state, "
            " listing_state_source, updated_at, game_start, team_id, "
            " team_league) VALUES ($1,$2,$3,'side',$4,$5,$6,'PREGAME', "
            " 'VENUE_LIVE_FLAG', $7, $8, $9, $10)",
            "%s-%s" % (slug, side), event, slug, side, intent, sports_type,
            now, now + _dt.timedelta(hours=20), 11 if side == "a" else 12,
            league)


@pg
def test_on_postgres_the_pass_writes_the_new_readings_and_the_waterfall():
    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await c.execute("DELETE FROM us_premap")
            want = dict(EXPECTED, **EXPECTED_PERIOD)
            seeded = [r for r in ROWS if r["contract_id"] in want]
            assert len(seeded) == len(want)
            for r in seeded:
                await _seed(c, r["contract_id"], event=r["event_id"],
                            sports_type=r["market_type"],
                            league=r["event_id"].split("-")[0])
            await _seed(c, "cpc-btc-range-hr-2026-10-09-0200z-1",
                        event="btc-range-hr-2026-10-09-0200z",
                        sports_type=None, league=None)
            await _seed(c, "aachc-nfl-wins-ou-2027-01-10-o",
                        event="nfl-wins-ou-2027-01-10",
                        sports_type="futures", league=None)
            await _seed(c, MLB_H2H["slug"], event="mlb-az-col-2026-09-24",
                        sports_type="baseball_team_full_game_winner",
                        league="mlb")
            now = time.time()
            pop = await POP.populate(c, since=0.0, now=now, full=True)
            assert pop["excluded"].get("btc") == 1
            await RULES.upsert(c, [RULES.pmus_row(
                {"slug": r["contract_id"], "description": r["rules_text"],
                 "sportsMarketType": r["market_type"]}) for r in seeded]
                + [RULES.pmus_row({"slug": MLB_H2H["slug"],
                                   "description": MLB_H2H["description"],
                                   "sportsMarketType":
                                       "baseball_team_full_game_winner"})],
                now=now)
            reg = {r["contract_id"]: dict(r) for r in await c.fetch(
                "SELECT contract_id, competition, sport, ontology "
                "  FROM market_plane_registry WHERE active")}
            assert "cpc-btc-range-hr-2026-10-09-0200z-1" not in reg
            assert reg[FB_SPREAD]["competition"] == "cfb"
            assert reg["aachc-nfl-wins-ou-2027-01-10-o"]["sport"] == \
                "football"
            # the venue count excludes the BTC market by its own code now
            assert await W.venue_active_count(c, now=now) == len(seeded) + 2
            sp = c.transaction()
            await sp.start()
            old = await POP.coverage_pass(c, now=now)
            olds = {r["contract_id"]: r["settlement_why"] for r in
                    await c.fetch("SELECT contract_id, settlement_why FROM "
                                  "market_plane_registry WHERE active")}
            await sp.rollback()
            assert "waterfall" not in old
            for cid in want:
                assert olds[cid].startswith("BOOKMAKER_TERMS_NOT_HELD:")
            S._LINE_CACHE.clear()
            S._TERMS_CACHE.clear()
            new = await POP.coverage_pass(c, now=now, derivative_terms=True,
                                          waterfall=True)
            got = {r["contract_id"]: dict(r) for r in await c.fetch(
                "SELECT contract_id, settlement_why, settlement_state, "
                "       coverage_state FROM market_plane_registry "
                " WHERE active")}
            for cid, why in want.items():
                assert got[cid]["settlement_why"] == why, cid
                assert got[cid]["settlement_state"] == S.NOT_PROVEN
                assert got[cid]["coverage_state"] == \
                    "MAPPED_BUT_SETTLEMENT_NOT_PROVEN"
            assert got[MLB_H2H["slug"]]["settlement_why"].startswith(
                "BOOK_TERMS_SCOPE_NOT_ESTABLISHED:")
            wf = new["waterfall"]
            assert wf["sums_exact"] is True
            assert wf["catalogue"] == new["active"] == len(got)
            a = wf["tiers"][WF.TARGET_A]
            assert a["catalogue"] == len(EXPECTED) + 1
            assert a[WF.LOST_SETTLEMENT] == len(EXPECTED) + 1
            assert a[WF.PRICEABLE] == 0
            b = wf["tiers"][WF.TARGET_B]
            assert b["catalogue"] == b[WF.LOST_SETTLEMENT] == \
                len(EXPECTED_PERIOD)
            assert wf["excluded"] == {"POLYMARKET_US|OUTRIGHT|FULL": 1}
            assert new["by_state"] == old["by_state"]
        finally:
            await tr.rollback()
            await c.close()
    run(go())
