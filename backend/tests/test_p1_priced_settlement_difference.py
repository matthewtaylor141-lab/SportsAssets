"""P1 FIRST-LOSS CENSUS: THE PRICED SETTLEMENT-DIFFERENCE POLICY.

SETTLEMENT_NOT_SUPPORTED was the first loss of 29 events in the 1 h census;
every one was written by DEREK_ENTRY_POLICY_V2 (research-sql run
37479304426 section 4) on an INCOMPATIBLE comparison whose only mismatch is
the exceptional state -- book: stake back on a game not played / not
completed; venue: "If the game is delayed, postponed, or suspended and not
rescheduled to a date within two weeks of the originally scheduled date, the
market will settle to the last fair market price."

Pinned here, for each sport family the census named (basketball, hockey,
baseball, NFL, NCAAF) and soccer:
  * the rate behind q_hi: the venue's own measured settlements, floored at
    the conservative prior, never cheaper for an unmeasured family;
  * the price p_venue = max(0, p_completed - q_hi) and its derivation;
  * eligibility only when every difference is an exceptional-state one and
    the completed-game match proves the ordinary grading -- and the exact
    refusal codes otherwise (an ordinary-completion mismatch, an unpriced
    lane code, an NCAAF clause that is not the cited one, a line market, a
    family with no rate row);
  * the declaration re-derived by gross_edge_inputs (tampering refuses),
    the capital gate resolving ONLY the policy's own marker and version, and
    the policy id / version / basis carried on the decision's pin;
  * paper_derek admitting such a contract only through the policy.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from sportsassets import bettor_capital_eligibility as CE
from sportsassets import bettor_settlement_difference_policy as SDP
from sportsassets import bettor_settlement_terms as ST
from sportsassets import bettor_venue_settlement as vset
from sportsassets import gross_edge_inputs as GEI
from sportsassets import refusal_taxonomy as RT
from sportsassets import settlement_exception_risk as SER
from sportsassets.agents import derek_policy as DP
from sportsassets.agents import paper_derek as PD
from sportsassets.workers import ext_pinnacle_loop as L

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text())


BH = _load("pmus_basketball_hockey_winner_listings_2026_10_06.json")
NFL_LISTING = _load("pmus_nfl_listing_2026_10_04.json")
CFB_LISTING = _load("pmus_cfb_listing_2026_10_03.json")
MLB = _load("pmus_live_description_mlb_h2h_2026_09_25.json")


def _listing(slug_prefix):
    return next(m for m in BH["markets"] if m["slug"].startswith(slug_prefix))


# ═════════════════════════════════════════════════════════════════════
# 1 · THE RATE: MEASURED, THEN FLOORED; NEVER CHEAPER WHEN UNMEASURED
# ═════════════════════════════════════════════════════════════════════

def test_every_family_rate_is_the_conservative_bound_with_its_basis():
    floor = SER.PRIOR_FLOOR_UPPER
    assert math.isclose(floor, 1 - 0.025 ** (1 / 40), rel_tol=1e-9)
    for fam, m in SDP.MEASURED.items():
        r = SDP.rate(fam)
        assert r["held"] is True
        assert r["q_hi"] >= floor
        # never below the measured upper bound of a family with enough
        # fixtures, never below any evidenced family's when unmeasured
        iv = SER.interval(m["k"], m["n"])
        if m["n"] >= SER.MIN_FIXTURES:
            assert r["q_hi"] >= iv["upper_95"]
        else:
            assert r["q_hi_is"] == "UNMEASURED_CONSERVATIVE_PRIOR"
            for x in SDP.MEASURED.values():
                if x["n"] >= SER.MIN_FIXTURES:
                    assert r["q_hi"] >= SER.interval(x["k"],
                                                     x["n"])["upper_95"]
        assert "37479304426" in r["basis"]["read"]
    # the production measurement: one MLB fixture settled at a price
    assert SDP.MEASURED["baseball"] == {
        "k": 1, "n": 65,
        "k_fixtures": ["aec-mlb-bal-nyy-2026-09-27 (0.485)"]}
    assert SDP.rate("baseball")["measured"]["upper_95"] < floor
    assert SDP.rate("curling")["held"] is False
    assert SDP.rate("curling")["refusal"] == SDP.R_NO_RATE


# ═════════════════════════════════════════════════════════════════════
# 2 · THE PRICE AND ITS RE-DERIVATION
# ═════════════════════════════════════════════════════════════════════

def test_the_price_is_p_completed_minus_q_hi_at_its_worst_end():
    q = SDP.rate("basketball")["q_hi"]
    got = SDP.price(0.62, sport_family="basketball", p_book=0.62)
    assert got["refusal"] is None and got["applies"] is True
    assert math.isclose(got["p"], 0.62 - q, abs_tol=1e-12)
    assert got["policy_id"] == SDP.POLICY_ID
    assert got["version"] == SDP.VERSION and got["formula"] == SDP.FORMULA
    assert "whatever either side pays" in got["derivation"]
    assert got["rate"]["q_hi"] == q
    # the bound can reach zero, never below it
    assert SDP.price(0.05, sport_family="basketball")["p"] == 0.0
    for bad in (None, 0.0, 1.0, float("nan"), "x"):
        assert SDP.price(bad, sport_family="basketball")["refusal"] == \
            SDP.R_NO_P
    assert SDP.price(0.5, sport_family="curling")["refusal"] == SDP.R_NO_RATE


def test_the_bound_holds_whatever_the_exceptional_payouts_are():
    """A brute check of the derivation: for any split of q into void and
    action states and ANY venue / book payouts there, the venue contract's
    expected payout is >= p_book - q."""
    import itertools
    for p_win_o, q_void, q_act, pay_x, p_book_x in itertools.product(
            (0.1, 0.4, 0.7, 0.95), (0.0, 0.02, 0.05), (0.0, 0.01, 0.03),
            (0.0, 0.5, 1.0), (0.0, 0.5, 1.0)):
        q = q_void + q_act
        p_o = 1.0 - q                                  # P(ordinary)
        # the book: action on O and on the action-states; void otherwise
        p_book = (p_win_o * p_o + p_book_x * q_act) / (p_o + q_act)
        venue = p_win_o * p_o + pay_x * q              # S in [0, 1] on X
        assert venue >= p_book - q - 1e-12, (p_win_o, q_void, q_act)


def test_gross_edge_inputs_re_derive_the_declaration_and_refuse_tampering():
    q = SDP.rate("hockey")["q_hi"]
    conv = {"applies": True, "version": SDP.VERSION,
            "sport_family": "hockey", "q_hi": q, "p": 0.55 - q,
            "formula": SDP.FORMULA, "completed_conversion": None}
    ok = GEI.conversion(p=0.55 - q, p_book=0.55, conv=conv, league="nhl")
    assert ok["passed"] is True, ok
    for bad in (dict(conv, q_hi=0.01), dict(conv, sport_family="curling")):
        assert GEI.conversion(p=0.55 - q, p_book=0.55, conv=bad,
                              league="nhl")["passed"] is False
    assert GEI.conversion(p=0.55, p_book=0.55, conv=conv,
                          league="nhl")["passed"] is False
    # an NFL tie conversion inside: its own worst end, re-derived
    inner = {"tie_rate_interval": [0.001, 0.02],
             "tie_payout_per_contract": 0.5}
    pb = 0.7
    pc = min((1 - 0.001) * pb + 0.5 * 0.001, (1 - 0.02) * pb + 0.5 * 0.02)
    qf = SDP.rate("football")["q_hi"]
    conv = {"applies": True, "version": SDP.VERSION,
            "sport_family": "football", "q_hi": qf, "p": pc - qf,
            "completed_conversion": inner}
    assert GEI.conversion(p=pc - qf, p_book=pb, conv=conv,
                          league="nfl")["passed"] is True
    assert GEI.conversion(p=pb - qf, p_book=pb, conv=conv,
                          league="nfl")["passed"] is False


def test_the_capital_gate_resolves_only_the_policys_own_marker():
    priced = SDP.price(0.6, sport_family="baseball", p_book=0.6)
    s = PD._priced_settlement(priced)
    assert s["compatibility"] == SDP.SETTLEMENT_PRICED != "COMPATIBLE"
    assert CE.settlement_resolved(s) is True
    for bad in (dict(s, version="PRICED_SETTLEMENT_DIFFERENCE_V0"),
                dict(s, policy_id="SOMETHING_ELSE"), dict(s, p=None),
                {"compatibility": "INCOMPATIBLE"},
                {"compatibility": "UNKNOWN"}, None):
        assert CE.settlement_resolved(bad) is False
    assert CE.settlement_resolved({"compatibility": "COMPATIBLE"}) is True


# ═════════════════════════════════════════════════════════════════════
# 3 · ELIGIBILITY, FAMILY BY FAMILY, ON THE VENUE'S OWN TEXTS
# ═════════════════════════════════════════════════════════════════════

def _row(slug, family, text, odds, *, sel="A", purpose="ENTRY_DECISION"):
    # baseball's book terms are read per phase (bettor_settlement_terms.
    # PHASE_BOOK_TERMS): the fixture's regular season, as the lane reads it
    kw = ({"phase": ST.PHASE_REGULAR, "book_context": ST.CTX_PRE_GAME,
           "game_format": ST.FMT_NINE} if family == "baseball" else {})
    a = vset.attest(sport_family=family, market="h2h",
                    venue_evidence={"rules_text": text},
                    book_evidence={"outcome_names": list(odds)},
                    observed_at=1.0, **kw)
    scmp = dict(L._settlement_compatibility(a), venue_rules_text=text)
    return {"id": 1, "us_market_slug": slug, "sport_family": family,
            "market": "h2h", "line": None, "period": "FULL_GAME",
            "buy_intent": "ORDER_INTENT_BUY_LONG", "contract_selection": sel,
            "payout_event": sel, "payout_is_complement": False,
            "event_key": "pinnapi:1", "record_purpose": purpose,
            "settlement_comparison": scmp, "raw_odds": odds,
            "refusals": [], "probability": 0.6, "venue": "PMUS"}


CASES = (
    # (family, venue league slug, text) -- every family the census named
    ("basketball", "aec-nba-atl-ind-2026-10-10",
     _listing("aec-nba-")["description"]),
    ("basketball", _listing("aec-kbl-")["slug"],
     _listing("aec-kbl-")["description"]),
    ("basketball", _listing("aec-eurocup-")["slug"],
     _listing("aec-eurocup-")["description"]),
    ("basketball", _listing("aec-wnba-")["slug"],
     _listing("aec-wnba-")["description"]),
    ("hockey", _listing("aec-nhl-")["slug"],
     _listing("aec-nhl-")["description"]),
    ("hockey", _listing("aec-khl-")["slug"],
     _listing("aec-khl-")["description"]),
    ("baseball", MLB["markets"][0]["slug"], MLB["markets"][0]["description"]),
)


@pytest.mark.parametrize("fam,slug,text", CASES)
def test_each_family_is_eligible_only_through_the_exceptional_state(
        fam, slug, text):
    row = _row(slug, fam, text, {"A": 1.7, "B": 2.2})
    cand = DP.candidate_from_row(row)
    assert cand["settlement"]["compatibility"] == "INCOMPATIBLE"
    per = row["settlement_comparison"]["per_condition"]
    mism = [c for c, r in per.items() if r["verdict"] == ST.V_MISMATCH]
    assert mism and set(mism) <= {ST.C_NOT_PLAYED, ST.C_SUSPENDED_BEYOND,
                                  ST.C_STOPPED_EARLY, ST.C_CALLED_FINAL}, mism
    sd = PD.settlement_difference(cand, row)
    assert sd["eligible"] is True, (slug, sd.get("refusals"), sd.get("why"))
    assert sd["ordinary_completion"]["established"] is True
    pin = {"p": 0.6, "observed_at": 1.0}
    assert PD.apply_settlement_difference(pin, sd, cand=cand) is None
    q = SDP.rate(fam)["q_hi"]
    assert math.isclose(pin["p"], 0.6 - q, abs_tol=1e-12)
    assert pin["p_book_conditional_no_tie"] == 0.6
    vc = pin["venue_conversion"]
    assert vc["version"] == SDP.VERSION and vc["policy_id"] == SDP.POLICY_ID
    assert vc["rate_basis"]["basis"]["read"].startswith("research-sql")
    assert pin["settlement_difference_policy"]["formula"] == SDP.FORMULA
    # the declaration gross_edge_inputs reads re-derives
    assert GEI.declared_conversion(pin) is vc
    assert GEI.conversion(p=pin["p"], p_book=0.6, conv=vc,
                          league=slug.split("-")[1])["passed"] is True


def test_the_nfl_tie_is_converted_first_and_then_the_exceptional_bound():
    m = NFL_LISTING["markets"][0]
    odds = {"Indianapolis Colts": 2.2, "Washington Commanders": 1.72}
    row = _row(m["slug"], "football", m["description"], odds,
               sel="Washington Commanders")
    cand = DP.candidate_from_row(row)
    sd = PD.settlement_difference(
        cand, row, catalogue={"game_start_epoch": None})
    assert sd["eligible"] is True, (sd.get("refusals"), sd.get("why"))
    pin = {"p": 0.6}
    assert PD.apply_settlement_difference(pin, sd, cand=cand) is None
    inner = pin["venue_conversion"]["completed_conversion"]
    assert inner and inner["tie_payout_per_contract"] == 0.5
    lo, hi = inner["tie_rate_interval"]
    pc = min((1 - lo) * 0.6 + 0.5 * lo, (1 - hi) * 0.6 + 0.5 * hi)
    assert math.isclose(pin["venue_conversion"]["p_completed"], pc,
                        abs_tol=1e-12)
    assert math.isclose(pin["p"], pc - SDP.rate("football")["q_hi"],
                        abs_tol=1e-12)
    assert GEI.conversion(p=pin["p"], p_book=0.6,
                          conv=pin["venue_conversion"],
                          league="nfl")["passed"] is True


def test_ncaaf_cited_clauses_are_priced_and_an_uncited_clause_is_not():
    m = CFB_LISTING["markets"][0]
    odds = {"Fresno State Bulldogs": 2.06, "Washington State Cougars": 1.84}
    row = _row(m["slug"], "football", m["description"], odds,
               sel="Fresno State Bulldogs")
    cand = DP.candidate_from_row(row)
    precise = DP.strict_settlement_reasons(cand)
    assert precise and set(precise) <= SDP.NCAAF_X_CODES
    sd = PD.settlement_difference(
        cand, row, catalogue={"game_start_epoch": 1791077400.0},
        precise=precise)
    assert sd["eligible"] is True, (sd.get("refusals"), sd.get("why"))
    pin = {"p": 0.48}
    assert PD.apply_settlement_difference(pin, sd, cand=cand) is None
    # the college identity conversion: p_completed is the book's own number
    assert pin["venue_conversion"]["completed_conversion"] is None
    assert math.isclose(pin["p"], 0.48 - SDP.rate("football")["q_hi"],
                        abs_tol=1e-12)
    # an appended clause the citation does not hold: refused by its code
    text = m["description"] + " Ties resolve to No."
    row2 = _row(m["slug"], "football", text, odds,
                sel="Fresno State Bulldogs")
    cand2 = DP.candidate_from_row(row2)
    sd2 = PD.settlement_difference(cand2, row2,
                                   precise=DP.strict_settlement_reasons(cand2))
    assert sd2["eligible"] is False and sd2["refusal"] == SDP.R_CLAUSE


def test_what_the_bound_cannot_price_refuses_by_its_exact_code():
    base = dict(sport_family="basketball", market="h2h", league="nba",
                lane_codes=(), precise_codes=(),
                match={"checks": [{"check": "ordinary_completion_grading_"
                                            "period", "passed": True}],
                       "policy": "X"})
    ok_st = {"per_condition": {ST.C_NOT_PLAYED: {
        "verdict": ST.V_MISMATCH}}}
    assert SDP.eligibility(settlement=ok_st, **base)["eligible"] is True
    # a mismatch in an ORDINARILY COMPLETED game
    st = {"per_condition": {ST.C_FULL: {"verdict": ST.V_MISMATCH}}}
    assert SDP.eligibility(settlement=st, **base)["refusal"] == \
        SDP.R_ORDINARY_DIFFERS
    # a lane code that is not an exceptional-state difference
    got = SDP.eligibility(settlement=ok_st,
                          **dict(base, lane_codes=["OVERTIME_RULE_"
                                                   "CONFLICTS_WITH_BOOK_RULE"]))
    assert got["refusal"] == SDP.R_LANE_CODE
    assert "OVERTIME_RULE_CONFLICTS_WITH_BOOK_RULE" in got["refusals"]
    # the NFL tie code is priced for the NFL only
    assert SDP.eligibility(settlement=ok_st, **dict(
        base, lane_codes=[SDP.NFL_TIE_LANE_CODE]))["refusal"] == \
        SDP.R_LANE_CODE
    assert SDP.eligibility(settlement=ok_st, **dict(
        base, sport_family="football", league="nfl",
        lane_codes=[SDP.NFL_TIE_LANE_CODE]))["eligible"] is True
    # a line market
    assert SDP.eligibility(settlement=ok_st, **dict(
        base, market="spreads"))["refusal"] == SDP.R_LINE
    # the ordinary grading not proved by the match (its own code behind)
    got = SDP.eligibility(settlement=ok_st, **dict(base, match={
        "checks": [{"check": "ordinary_completion_grading_period",
                    "passed": False,
                    "refusal": "ORDINARY_GRADING_PERIOD_MISMATCH"}]}))
    assert got["refusal"] == SDP.R_ORDINARY_UNPROVEN
    assert "ORDINARY_GRADING_PERIOD_MISMATCH" in got["refusals"]
    assert SDP.eligibility(settlement=ok_st, **dict(base, match=None))[
        "refusal"] == SDP.R_ORDINARY_UNPROVEN
    # the match's identity / probability checks are the caller's, not ours
    got = SDP.eligibility(settlement=ok_st, **dict(base, match={
        "checks": [{"check": "ordinary_completion_grading_period",
                    "passed": True},
                   {"check": "probability_qualified_by_the_lane",
                    "passed": False,
                    "refusal": "PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_"
                               "THE_LANE"}]}))
    assert got["eligible"] is True


def test_a_contract_priced_at_zero_is_an_economic_verdict():
    m = _listing("aec-nhl-")
    row = _row(m["slug"], "hockey", m["description"], {"A": 1.05, "B": 12.0})
    cand = DP.candidate_from_row(row)
    sd = PD.settlement_difference(cand, row)
    pin = {"p": 0.05}
    assert PD.apply_settlement_difference(pin, sd, cand=cand) == \
        SDP.R_PRICED_AT_ZERO
    assert pin["p"] == 0.05                 # never a 0 handed on as p
    k = RT.classify(SDP.R_PRICED_AT_ZERO)
    assert k["classified"] and k["class"] == RT.ECONOMIC


def test_every_policy_code_is_classified_and_the_module_is_pinned():
    for c in SDP.REFUSALS:
        k = RT.classify(c)
        assert k["classified"] and k["class"] == RT.SOFTWARE, c
    from sportsassets import decision_logic as DL
    assert "bettor_settlement_difference_policy.py" in \
        DL.DECISION_LOGIC_FILES
    assert "pinnapi_names.py" in DL.DECISION_LOGIC_FILES
    from sportsassets import bettor_ncaaf_settlement as NC
    assert SDP.NCAAF_X_CODES == frozenset(NC.STRICT_CODES)
