"""P0 INCIDENT · THE NCAAF MONEY LINE: EVERY CITATION CHECKED AGAINST WHAT WAS
RETRIEVED, AND EVERY UNPROVEN CLAUSE REFUSED BY NAME.

bettor_ncaaf_settlement compares the venue's college-football contract with
Pinnacle's two-way money line payout by payout. This file holds it to its
evidence, which was read (never recalled) and is kept verbatim in fixtures:

  * Pinnacle's rules page, re-read by the NCAAF stream (fetch-docs run
    37241517150, 119,592 bytes, sha256 63d64321...03fd -- byte-identical to
    the NFL stream's two captures):
    tests/fixtures/pinnacle_american_football_rules_2026_10_04.json
  * the venue's own cfb listings (public gateway, fetch-docs run 37161115118):
    tests/fixtures/pmus_cfb_listing_2026_10_03.json
  * the text on EVERY production cfb valuation row, masked and grouped with no
    limit (research-sql run 37241503567, N1-N8): one wording, 9 of 9 rows:
    tests/fixtures/ncaaf_production_wording_2026_10_04.json
  * production's current cfb money-line catalogue rows (research-sql run
    37241920748, C1-C3): tests/fixtures/pmus_cfb_catalogue_rows_2026_10_04.json
  * the college overtime rule (a game tied after regulation is played to a
    winner), SECONDARY source stated as such (Wikipedia 'Overtime (sports)'
    revision 1377048726, section 10, fetch-docs runs 37241519094 /
    37241934626), after the NCAA's own book was sought and is not publicly
    fetchable (runs 37241681952 / 37242123927):
    tests/fixtures/ncaaf_no_tie_rule_2026_10_04.json

and proves the reading: the venue text must be EXACTLY the five cited clauses
(each missing, varied, contradicted or extra clause is its own refusal), the
book's line must be the two-way game line, the no-tie rule and the book
capture must be held -- then and only then is the book's number the
contract's value, unchanged. The strict policy keeps refusing, with each
payout difference named.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sportsassets import bettor_ncaaf_settlement as NC
from sportsassets import bettor_nfl_settlement as NFL
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import pinnapi_census as CEN
from sportsassets.agents import derek_policy as DP
from sportsassets.agents import paper_benchmark as PB

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text())


PIN = _load("pinnacle_american_football_rules_2026_10_04.json")
LISTING = _load("pmus_cfb_listing_2026_10_03.json")
WORDING = _load("ncaaf_production_wording_2026_10_04.json")
CATALOGUE = _load("pmus_cfb_catalogue_rows_2026_10_04.json")
NO_TIE = _load("ncaaf_no_tie_rule_2026_10_04.json")
TEXT = LISTING["markets"][0]["description"]
SLUG = LISTING["markets"][0]["slug"]


def _flat(s):
    return " ".join(str(s).split())


def _page_sentences():
    return [_flat(x) for sec in ("general_rules_section",
                                 "american_football_section",
                                 "american_football_market_rules")
            for x in PIN[sec]]


# ═════════════════════════════════════════════════════════════════════
# 1 · THE BOOK'S CITATIONS ARE THE CAPTURED PAGE'S OWN SENTENCES
# ═════════════════════════════════════════════════════════════════════

def test_every_book_sentence_is_verbatim_in_the_capture_it_cites():
    page = " ".join(_page_sentences())
    for q in NC.BOOK_QUOTES + NC.Q_BOOK_NCAA_MARKET_RULES:
        assert _flat(q) in page, q
    cap = NC.PINNACLE_CAPTURES[-1]
    assert PIN["captured_in"]["run_id"] == cap["run_id"] == 37241517150
    assert PIN["captured_in"]["job_id"] == cap["job_id"]
    assert PIN["retrieved_at"] == cap["retrieved_at"]
    assert PIN["page_bytes"] == cap["bytes"] == 119592
    # byte-identical to the page the NFL module cites
    assert PIN["page_sha256"] == NC.PINNACLE_PAGE_SHA256 == \
        NFL.PINNACLE_PAGE_SHA256
    assert PIN["source_url"] == NC.PINNACLE_RULES_URL
    assert NC.book_rules_held()["held"] is True


def test_the_ncaa_market_rules_hold_no_game_money_line():
    """Market Rules take precedence over Sport Rules (cited). Read in full,
    the American Football market rules name NCAA exactly twice -- season wins
    and conference-championship futures -- so no market rule overrides the
    sport rules for an NCAA game's money line."""
    ncaa = [x for x in PIN["american_football_market_rules"]
            if "NCAA" in x]
    assert len(ncaa) == 2, ncaa
    assert ncaa[0].startswith("NCAA Football Season Wins")
    assert ncaa[1].startswith("NCAA Football Conference Championship Futures")
    assert not any("money line" in x.lower() or "moneyline" in x.lower()
                   for x in ncaa)
    sport = [_flat(x) for x in PIN["american_football_section"]]
    assert _flat(NC.Q_BOOK_LEAGUES) in sport          # NFL, NCAA, UFL, CFL
    assert _flat(NC.Q_BOOK_PRECEDENCE) in _page_sentences()


# ═════════════════════════════════════════════════════════════════════
# 2 · THE VENUE'S CITATIONS: ITS LISTINGS AND EVERY PRODUCTION ROW
# ═════════════════════════════════════════════════════════════════════

def test_every_captured_cfb_listing_is_exactly_the_cited_clauses():
    assert LISTING["_evidence"]["run_id"] == NC.VENUE_CAPTURES[0]["run_id"]
    assert LISTING["_evidence"]["response_sha256"] == \
        NC.VENUE_CAPTURES[0]["sha256"]
    assert {m["slug"] for m in LISTING["markets"]} == \
        set(NC.VENUE_CAPTURES[0]["slugs"])
    for m in LISTING["markets"]:
        assert m["sportsMarketType"] == "football_team_full_game_winner"
        for q in (NC.Q_VENUE_OVERTIME, NC.Q_VENUE_TIE_REVIEW,
                  NC.Q_VENUE_POSTPONED, NC.Q_VENUE_SOURCE):
            assert m["description"].count(q) == 1, (m["slug"], q)
        got = NC.venue_clauses(m["description"])
        assert got["ok"] and got["refusals"] == [], (m["slug"], got)
        # the college listing is NOT the NFL contract: no $0.50 tie
        assert NFL.Q_VENUE_TIE not in m["description"]


def test_every_production_cfb_row_carries_the_one_cited_wording():
    ev = WORDING["_evidence"]
    cap = NC.VENUE_CAPTURES[-1]
    assert ev["run_id"] == cap["run_id"] == 37241503567
    assert ev["file_sha256"] == cap["sql_sha256"]
    assert WORDING["N1_rules_text_presence"]["rows"] == cap["rows"] == 9
    assert WORDING["N1_rules_text_presence"]["without_text"] == 0
    groups = WORDING["N2_masked_wordings"]
    assert len(groups) == cap["wordings"] == 1
    g = groups[0]
    assert g["rows"] == 9 and g["contracts"] == 9
    # the fixture's text is byte-identical to what production grouped: its
    # md5 is the one Postgres computed on the masked text
    assert hashlib.md5(g["masked_text"].encode()).hexdigest() == \
        g["masked_md5"] == cap["masked_md5"]
    assert sorted(r["id"] for r in WORDING["N5_rows"]) == \
        list(cap["row_ids"])
    # unmasked with one captured game sentence, it is the cited text
    sentence = TEXT.split(". Overtime")[0] + "."
    assert NC.venue_clauses(
        g["masked_text"].replace("<WINNER-OF-NAMED-GAME-ON-DATE>.",
                                 sentence))["ok"]
    sb = WORDING["N2b_game_sentence"][0]
    assert sb["says_college_football_game_on_date"] is True
    assert sb["says_nfl_game"] is False and sb["rows"] == 9


def test_the_production_outcome_set_is_what_the_de_vig_admission_cites():
    n3 = WORDING["N3_outcome_set"]
    assert n3["rows"] == n3["priced_2"] == n3["book_pinnacle"] == 9
    assert n3["priced_3"] == n3["with_draw_key"] == 0
    assert devig.expected_outcomes("football", "h2h", league="cfb") == 2
    assert ("football", "h2h") not in devig.SUPPORTED     # never family-wide
    assert devig.expected_outcomes("football", "h2h", league="cfl") is None
    # every recorded row's priced set is the two teams
    for r in WORDING["N5_rows"]:
        assert len(r["raw_odds"]) == 2
        assert not NC._draw_named(r["raw_odds"])


def test_the_production_catalogue_rows_are_admitted_by_the_census():
    """Production's cfb money-line rows (research-sql run 37241920748) carry
    the question's clock minutes as their line, like the NFL rows; the
    census's side-aware clock proof admits them, and only for a league the
    de-vig admits (structured league and slug agreeing)."""
    assert CATALOGUE["_evidence"]["run_id"] == 37241920748
    rows = CATALOGUE["rows"]
    assert len(rows) == 6 and CATALOGUE["C1_shape"][0]["rows"] == 6
    for r in rows:
        assert r["team_league"] == "cfb" and r["line"] not in (None, "")
        got = CEN.family_of(r["kind"], r["line"], r["sports_type"], r)
        assert got == ("MONEYLINE", True, "PROVED_CLOCK_ARTIFACT"), (r, got)
    r = rows[0]
    for over in ({"team_league": "nfl"}, {"team_league": None,
                                          "identifier": None}):
        assert CEN.family_of(r["kind"], r["line"], r["sports_type"],
                             dict(r, **over)) == (
            "MONEYLINE", False, CEN.R_FOOTBALL_LEAGUE_NOT_ADMITTED), over
    assert CEN.family_of(r["kind"], "3.5", r["sports_type"], r)[1] is False


def test_the_venue_dates_a_cfb_contract_by_the_et_day():
    """N7: the slug date equals the kickoff's America/New_York day on 171 of
    171 production rows (the UTC day on only 147). The captured Fresno State
    game kicks off 01:30Z on Oct 4 = 21:30 ET on Oct 3, and its slug and text
    say Oct 3."""
    n7 = WORDING["N7_catalogue_dates"]
    assert n7["rows"] == n7["slug_date_equals_et_day"] == 171
    assert n7["slug_date_equals_utc_day"] < n7["rows"]
    kick = 1791077400.0                       # 2026-10-04T01:30:00Z
    ok = NC.fixture_date(slug=SLUG, venue_rules_text=TEXT,
                         kickoff_epoch=kick)
    assert ok["refusal"] is None and ok["event_date"] == "2026-10-03"
    utc = NC.fixture_date(slug=SLUG.replace("2026-10-03", "2026-10-04"),
                          venue_rules_text=TEXT, kickoff_epoch=kick)
    assert utc["refusal"] == NC.R_DATE_INCONSISTENT
    assert NC.fixture_date(slug="aec-cfb-frest-washst",
                           venue_rules_text=TEXT)["refusal"] == \
        NC.R_DATE_UNREADABLE


# ═════════════════════════════════════════════════════════════════════
# 3 · THE NO-TIE RULE: QUOTED, SOURCED, AND REQUIRED
# ═════════════════════════════════════════════════════════════════════

def test_the_no_tie_rule_is_quoted_verbatim_from_the_read_revision():
    sec = NO_TIE["section_college"]
    ev = NC.NO_TIE_RULE_EVIDENCE
    assert ev["rule_quote"] == sec["until_winner_sentence"]
    assert ev["repeat_quote"] == sec["repeat_sentence"]
    assert ev["scope_quote"] == sec["scope_sentence"]
    assert ev["ncaa_quote"] == sec["ncaa_two_point_sentence"]
    assert ev["points_quote"] == sec["points_count_sentence"]
    assert ev["run_id"] == sec["read_by"]["run_id"] == 37241934626
    assert ev["sha256"] == sec["read_by"]["sha256"]
    assert ev["bytes"] == sec["read_by"]["bytes"]
    assert ev["revision_read"]["run_id"] == \
        NO_TIE["revision"]["read_by"]["run_id"]
    assert str(NO_TIE["revision"]["revid"]) in ev["source_url"]
    # stated as secondary, with the primary-source attempts on the record
    assert ev["source_class"] == "SECONDARY"
    assert [a["run_id"] for a in ev["primary_source_attempts"]] == \
        [a["run_id"] for a in NO_TIE["primary_source_attempts"]]
    # the section names the leagues whose overtime CAN end level -- and
    # college football is not one of them
    assert "CFL" in sec["contrast_cfl_sentence"]
    assert NC.no_tie_rule()["held"] is True


@pytest.mark.parametrize("drop", ["rule_quote", "source_url", "retrieved_at",
                                  "run_id", "sha256"])
def test_without_the_no_tie_rule_every_conversion_refuses_by_name(drop):
    ev = dict(NC.NO_TIE_RULE_EVIDENCE)
    ev.pop(drop)
    assert NC.no_tie_rule(ev) == {
        "held": False, "refusal": NC.R_NO_TIE_RULE,
        "why": "the no-tie rule evidence lacks %s" % [drop]}
    got = NC.convert(0.61, sport_family="football", league="cfb",
                     venue_rules_text=TEXT, book_outcome_names=["A", "B"],
                     evidence=ev)
    assert got["p"] is None and got["refusal"] == NC.R_NO_TIE_RULE


def test_without_the_book_capture_the_conversion_refuses_by_name(
        monkeypatch):
    assert NC.book_rules_held(captures=())["refusal"] == NC.R_BOOK_RULES
    assert NC.book_rules_held(quotes=("x", ""))["refusal"] == NC.R_BOOK_RULES
    monkeypatch.setattr(NC, "PINNACLE_CAPTURES", ())
    got = NC.convert(0.61, sport_family="football", league="cfb",
                     venue_rules_text=TEXT, book_outcome_names=["A", "B"])
    assert got["p"] is None and got["refusal"] == NC.R_BOOK_RULES


# ═════════════════════════════════════════════════════════════════════
# 4 · THE VENUE TEXT, CLAUSE BY CLAUSE: EACH UNPROVEN CLAUSE BY NAME
# ═════════════════════════════════════════════════════════════════════

_GAME = ("This market will settle to the winner of the Fresno State vs "
         "Washington State College Football game scheduled for Oct 3, 2026.")
CLAUSE_CASES = [
    # (what is wrong, the text, the FIRST refusal)
    ("no text", "", NC.R_VENUE_TEXT_ABSENT),
    ("winner clause missing", TEXT.replace(_GAME + " ", ""),
     NC.R_VENUE_WINNER),
    ("winner clause names an NFL game",
     TEXT.replace("College Football game", "NFL game"), NC.R_VENUE_WINNER),
    ("overtime clause missing",
     TEXT.replace(NC.Q_VENUE_OVERTIME + " ", ""), NC.R_VENUE_OVERTIME),
    ("overtime excluded",
     TEXT.replace(NC.Q_VENUE_OVERTIME, "Overtime is not included."),
     NC.R_VENUE_OVERTIME),
    ("tie review clause missing",
     TEXT.replace(NC.Q_VENUE_TIE_REVIEW + " ", ""), NC.R_VENUE_TIE),
    ("tie settled at $0.50 instead (the NFL clause)",
     TEXT.replace(NC.Q_VENUE_TIE_REVIEW, NFL.Q_VENUE_TIE), NC.R_VENUE_TIE),
    ("a contradicting tie clause appended (the NFL review's case)",
     TEXT + " If the game ends in a tie after overtime, all positions "
     "resolve to No.", NC.R_VENUE_TIE),
    ("postponement clause missing",
     TEXT.replace(NC.Q_VENUE_POSTPONED + " ", ""), NC.R_VENUE_POSTPONED),
    ("postponement window varied",
     TEXT.replace("within two weeks", "within one week"),
     NC.R_VENUE_POSTPONED),
    ("result source missing",
     TEXT.replace(" " + NC.Q_VENUE_SOURCE, ""), NC.R_VENUE_SOURCE),
    ("result source varied",
     TEXT.replace(NC.Q_VENUE_SOURCE, "Outcome sourced from ESPN."),
     NC.R_VENUE_SOURCE),
    ("an extra uncited clause",
     TEXT + " Forfeited games resolve to the team awarded the win.",
     NC.R_VENUE_UNCITED),
    ("the overtime clause stated twice",
     TEXT + " " + NC.Q_VENUE_OVERTIME, NC.R_VENUE_OVERTIME),
]


@pytest.mark.parametrize("what,text,code", CLAUSE_CASES,
                         ids=[c[0] for c in CLAUSE_CASES])
def test_each_unproven_venue_clause_is_refused_by_its_own_code(what, text,
                                                              code):
    got = NC.venue_clauses(text)
    assert got["ok"] is False and got["refusals"][0] == code, (what, got)
    conv = NC.convert(0.61, sport_family="football", league="cfb",
                      venue_rules_text=text, book_outcome_names=["A", "B"])
    assert conv["applies"] and conv["p"] is None
    assert conv["refusal"] == code, (what, conv)
    # the completed-game policy's match names the same clause
    m = PB.completed_game_match(_cand(SLUG), _row(SLUG, text))
    assert code in m["refusals"], (what, m["refusals"])
    assert m["established"] is False


def test_a_school_name_with_a_period_does_not_split_the_winner_clause():
    t = TEXT.replace("Fresno State vs Washington State",
                     "St. Thomas (MN) vs Washington State")
    got = NC.venue_clauses(t)
    assert got["ok"], got
    assert got["clauses"]["winner"]["game"] == \
        "st. thomas (mn) vs washington state"


# ═════════════════════════════════════════════════════════════════════
# 5 · THE CONVERSION: THE BOOK'S NUMBER, UNCHANGED, ONLY WHEN ALL HOLDS
# ═════════════════════════════════════════════════════════════════════

def test_the_conversion_is_the_identity_when_every_premise_holds():
    for p in (0.07, 0.5, 0.6123, 0.93):
        got = NC.convert(p, sport_family="football", league="cfb",
                         venue_rules_text=TEXT,
                         book_outcome_names=["Fresno State Bulldogs",
                                             "Washington State Cougars"])
        assert got["refusal"] is None and got["p"] == p
        assert got["p_is"] == NC.P_IS_EQUIVALENT
        assert got["tie_probability_completed"] == 0.0
        assert got["p_book_conditional_no_tie"] == p
        assert got["no_tie_rule"]["run_id"] == 37241934626
        assert got["book_tie_rule"]["quote"] == NC.Q_BOOK_TIE


def test_a_draw_priced_line_is_never_the_game_line():
    for names in (["A", "B", "Draw"], ["A", "Tie"], ["A", "B", "X"]):
        got = NC.convert(0.6, sport_family="football", league="cfb",
                         venue_rules_text=TEXT, book_outcome_names=names)
        assert got["p"] is None and got["refusal"] == NC.R_BOOK_PRICES_DRAW


def test_no_conversion_outside_the_college_money_line():
    for fam, lg in (("football", "nfl"), ("baseball", "cfb"),
                    ("football", None), ("soccer", "unl")):
        got = NC.convert(0.6, sport_family=fam, league=lg,
                         venue_rules_text=TEXT, book_outcome_names=["A", "B"])
        assert got == {"applies": False, "p": 0.6, "refusal": None,
                       "why": "no NCAAF conversion outside the college "
                              "money line"}
    assert NC.convert(None, sport_family="football", league="cfb",
                      venue_rules_text=TEXT)["p"] is None


def test_the_apply_and_held_conversions_share_the_one_reading():
    pin = {"p": 0.62}
    m = PB.completed_game_match(_cand(SLUG), _row(SLUG, TEXT))
    assert PB.apply_venue_conversion(pin, m) is None
    assert pin["p"] == 0.62 and pin["p_book_conditional_no_tie"] == 0.62
    assert pin["p_is"] == NC.P_IS_EQUIVALENT
    held = PB.held_venue_conversion({
        "sport_family": "football", "us_market_slug": SLUG,
        "raw_odds": {"A": 1.6, "B": 2.4}, "venue_rules_text": TEXT})
    assert held["venue_conversion"]["league"] == "cfb"
    assert PB.held_nfl_conversion({"sport_family": "football",
                                   "us_market_slug": SLUG}) is None
    pin2 = {"p": 0.62}
    assert PB.apply_venue_conversion(pin2, held) is None and pin2["p"] == 0.62
    bad = dict(held["venue_conversion"], venue_rules_text=TEXT.replace(
        NC.Q_VENUE_OVERTIME, "Overtime is not included."))
    pin3 = {"p": 0.62}
    assert PB.apply_venue_conversion(pin3, {"venue_conversion": bad}) == \
        NC.R_VENUE_OVERTIME
    assert pin3["p"] == 0.62                   # never silently used...
    assert pin3["p_is"] == "BOOK_CONDITIONAL_NO_TIE_UNCONVERTED"
    # ...and other sports are untouched
    assert PB.held_venue_conversion({"sport_family": "baseball",
                                     "us_market_slug": "aec-mlb-a-b-2026-10-04"
                                     }) is None


# ═════════════════════════════════════════════════════════════════════
# 6 · THE STATES, AND THE STRICT POLICY'S PRECISE REFUSAL
# ═════════════════════════════════════════════════════════════════════

def test_every_state_is_paid_and_cited_on_both_sides():
    rec = NC.states_record()
    states = {s["state"]: s for s in rec["states"]}
    assert rec["no_tie_rule_held"] is True
    assert rec["exceptional_probability"] == "UNMEASURED"
    for name in ("REGULATION_WIN", "OVERTIME_WIN"):
        s = states[name]
        assert s["class"] == NC.ORDINARY
        assert s["verdict"] == "SAME_EVENT_SAME_DIRECTION"
        assert s["venue_cite"]["quote"] and s["book_cite"]["quote"]
        assert s["book_cite"]["run_id"] == 37241517150
    tie = states["TIE_AFTER_OVERTIME_IN_A_COMPLETED_GAME"]
    assert tie["class"] == NC.UNREACHABLE
    assert tie["rule_cite"]["rule_quote"] == NC.NO_TIE_RULE_EVIDENCE[
        "rule_quote"]
    for s in rec["states"]:
        if s["class"] == NC.EXCEPTIONAL:
            assert s["verdict"] in ("DIFFERENT_PAYOUT", "VENUE_PAYOUT_NOT_"
                                    "STATED", "VENUE_SILENT"), s
            assert s.get("book_cite", {}).get("quote"), s
    # the strict policy's missing clauses: each with both payouts and quotes
    for m in rec["strict_policy_missing"]:
        assert m["code"] in NC.STRICT_CODES
        assert m["book_payout"] and m["venue_payout"] and m["book_quote"]
        assert "venue" not in m                 # a payout is never "venue"


def test_the_strict_policy_names_each_payout_difference():
    assert NC.strict_policy_codes(TEXT) == list(NC.STRICT_CODES)
    # a clause of the text that is not the cited one comes first
    varied = TEXT.replace(NC.Q_VENUE_OVERTIME, "Overtime is not included.")
    assert NC.strict_policy_codes(varied)[0] == NC.R_VENUE_OVERTIME
    cand = {"sport_family": "football", "us_market_slug": SLUG,
            "settlement": {"venue_rules_text": TEXT}}
    assert DP.strict_settlement_reasons(cand) == list(NC.STRICT_CODES)
    # every other contract: nothing changes
    for slug in ("aec-nfl-ind-was-2026-10-04", "aec-mlb-phi-atl-2026-10-04"):
        assert DP.strict_settlement_reasons(dict(
            cand, us_market_slug=slug)) == []
    assert DP.strict_settlement_reasons(dict(
        cand, sport_family="baseball")) == []


def test_the_strict_candidate_carries_the_rows_own_text_to_its_clauses():
    """Derek's candidate is built from the valuation row; it now carries the
    contract's own rules text, so the precise clauses come from the row
    itself -- including a row that recorded COMPATIBLE, which a cited payout
    difference never overrides (paper_derek refuses it with the category
    and the clauses behind it)."""
    row = {"id": 1, "us_market_slug": SLUG, "sport_family": "football",
           "settlement_comparison": {"compatibility": "COMPATIBLE",
                                     "overall_established": True,
                                     "venue_rules_text": TEXT},
           "refusals": []}
    cand = DP.candidate_from_row(row)
    assert cand["settlement"]["venue_rules_text"] == TEXT
    assert DP.strict_settlement_reasons(cand) == list(NC.STRICT_CODES)
    row["settlement_comparison"].pop("venue_rules_text")
    assert DP.strict_settlement_reasons(DP.candidate_from_row(row))[0] == \
        NC.R_VENUE_TEXT_ABSENT


# ═════════════════════════════════════════════════════════════════════
# 7 · THE COMPLETED-GAME MATCH ON THE CAPTURED CONTRACT
# ═════════════════════════════════════════════════════════════════════

def _row(slug, text, odds=None):
    return {"us_market_slug": slug, "sport_family": "football",
            "settlement_comparison": {"venue_rules_text": text},
            "raw_odds": odds or {"Fresno State Bulldogs": 2.06,
                                 "Washington State Cougars": 1.84}}


def _cand(slug):
    return {"us_market_slug": slug, "sport_family": "football",
            "market": "h2h", "line": None, "period": "FULL_GAME",
            "payout_event": "A", "side": "ORDER_INTENT_BUY_LONG",
            "fixture": "fx", "refusals": [], "settlement": {},
            "pinnacle": {}}


def test_the_captured_cfb_contract_establishes_the_college_terms():
    m = PB.completed_game_match(_cand(SLUG), _row(SLUG, TEXT),
                                catalogue={"game_start_epoch": 1791077400.0})
    names = {c["check"]: c for c in m["checks"]}
    gp = names["ordinary_completion_grading_period"]
    assert gp["passed"], gp
    assert gp["book"]["period"] == gp["venue"]["period"] == \
        PB.GP_FOOTBALL_NCAAF
    assert gp["book"]["run_id"] == 37241517150
    for c in ("ncaaf_venue_text_is_exactly_the_cited_clauses",
              "ncaaf_fixture_date_matches_the_venue_slug",
              "ncaaf_no_tie_state_by_the_cited_rule"):
        assert names[c]["passed"], names[c]
    assert PB.R_FAMILY not in m["refusals"]
    assert not set(NC.REFUSALS) & set(m["refusals"]), m["refusals"]
    vc = m["venue_conversion"]
    assert vc["league"] == "cfb" and vc["venue_rules_text"] == TEXT
    ex = m["exceptional_terms"]["ncaaf_settlement_states"]
    assert ex["exceptional_probability"] == "UNMEASURED"
    # a draw-priced book line is refused by name in the match too
    drawn = PB.completed_game_match(
        _cand(SLUG), _row(SLUG, TEXT, odds={"A": 2.5, "B": 2.9,
                                            "Draw": 9.0}))
    assert NC.R_BOOK_PRICES_DRAW in drawn["refusals"]


def test_the_college_text_never_establishes_the_nfl_contract_or_reverse():
    nfl_slug = "aec-nfl-ind-was-2026-10-04"
    on_nfl = PB.completed_game_match(_cand(nfl_slug), _row(nfl_slug, TEXT))
    assert PB.R_GP_UNKNOWN in on_nfl["refusals"]
    nfl_text = NFL.Q_VENUE_TIE.join(TEXT.split(NC.Q_VENUE_TIE_REVIEW))
    on_cfb = PB.completed_game_match(_cand(SLUG), _row(SLUG, nfl_text))
    assert NC.R_VENUE_TIE in on_cfb["refusals"]
    assert PB.venue_grading_period("football", TEXT, "cfb")["period"] == \
        PB.GP_FOOTBALL_NCAAF
    assert PB.venue_grading_period("football", TEXT)["period"] is None
    excl = TEXT.replace(NC.Q_VENUE_OVERTIME, "Overtime is not included.")
    assert PB.venue_grading_period("football", excl, "cfb")["refusal"] == \
        PB.R_GP_MISMATCH
    assert PB.venue_grading_period("football", "", "cfb")["refusal"] == \
        PB.R_GP_TEXT_ABSENT


def test_the_research_ledgers_spelled_out_codes_are_the_modules_own():
    """lost_opportunity.classify spells the NCAAF codes out (its import
    closure stays its own); they must be exactly the module's codes, so a
    renamed clause can never fall into the unrecognised bucket."""
    from sportsassets.lost_opportunity import classify as LC
    assert LC.NCAAF_SETTLEMENT_CODES | LC.NCAAF_IDENTITY_CODES == \
        set(NC.REFUSALS) | set(NC.STRICT_CODES)
    assert LC.NCAAF_IDENTITY_CODES == {NC.R_DATE_INCONSISTENT,
                                       NC.R_DATE_UNREADABLE}
    for c in set(NC.REFUSALS) | set(NC.STRICT_CODES):
        assert c in LC.CONTROL_CODES
        assert LC.ATTRIBUTION_OF[c] in ("SETTLEMENT", "IDENTITY_MAPPING")
    from sportsassets import bettor_paper_ops as OPS
    for c in set(NC.REFUSALS) | set(NC.STRICT_CODES):
        assert OPS.REFUSAL_WORDS.get(c), c
