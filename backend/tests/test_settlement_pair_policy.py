"""THE PAIR SETTLEMENT-DIFFERENCE POLICY (settlement_pair_policy) and its
binding into Adriana's claim-first scan (agents/adriana_claims).

Owner directive (RC5): "Implement the explicit priced settlement-difference
policy; do not assume cancelled-game fair prices across separate markets sum
to $1. Unknown payout compatibility stays refused."

Each outcome class -- normal, postponed, cancelled / void, partial -- is
priced or refused here, from canonical_claims' own payoff tokens (the
production MLB shape: Kalshi's rulebook settles a game not completed in its
window to the market's last fair price, golden source record KT01).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from sportsassets import canonical_claims as CC
from sportsassets import kalshi_fees as KF
from sportsassets import refusal_taxonomy_table as TT
from sportsassets import settlement_pair_policy as SPP
from sportsassets.agents import adriana_arb as A
from sportsassets.agents import adriana_claims as AC

D = Decimal
NOW = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc).timestamp()
START = NOW + 4 * 3600
FX = CC.Fixture(event_key="MLB:2026-10-08T00:00Z:TB@NYY", sport="BASEBALL",
                league="MLB", start_epoch=START, outcome_kind="TWO_WAY",
                home="NYY", away="TB")
#: production-shaped: a game not completed inside 48 h, or never completed,
#: settles to the MARKET'S last fair price; a tie cannot happen
FAIR = {"overtime_included": True, "draw_rule": "IMPOSSIBLE",
        "void_rule": "LAST_FAIR_PRICE", "postponement_window_hours": 48.0,
        "postponement_payout": "LAST_FAIR_PRICE",
        "verification_sources": ["MLB"]}
SCALAR = dict(FAIR, void_rule="SCALAR_0_50",
              postponement_payout="SCALAR_0_50")
KTERMS = KF.effective_terms(
    series_ticker="KXMLBGAME", event_ticker="KXMLBGAME-26OCT072000TBNYY",
    at=NOW, event_changes=[], series_changes=[{
        "id": "test-change-x1", "fee_type": "quadratic_with_maker_fees",
        "fee_multiplier": 1, "scheduled_ts": "2025-10-04T07:00:00Z",
        "series_ticker": "KXMLBGAME"}])


def inst(venue, market, side, subject, asks=((D("0.44"), 100),), *,
         terms=FAIR):
    return CC.Instrument(venue=venue, market_id=market, side=side,
                         subject=subject, settlement=dict(terms),
                         settlement_status="PROVEN",
                         mapping_status="ESTABLISHED", asks=tuple(asks),
                         observed_at=NOW, book_basis="TEST_BOOK",
                         sport="BASEBALL",
                         fee_terms=KTERMS if venue == "KALSHI" else None)


def legs(*instruments):
    b = CC.build_claims(FX, list(instruments))
    return b, [AC.policy_leg(i) for i in instruments]


def never(pol):
    (row,) = [r for r in pol["by_state"] if r["state"] == CC.S_NEVER]
    return row


# ── 1. EACH OUTCOME CLASS, PRICED OR REFUSED ─────────────────────────────

def test_the_states_fall_into_the_four_outcome_classes():
    b, _ = legs(inst("KALSHI", "K-NYY", "YES", "HOME"))
    cls = {s: SPP.outcome_class(s) for s in b["states"]}
    assert cls["HOME_WIN"] == SPP.C_NORMAL
    assert cls["AWAY_WIN@COMPLETED_AFTER_DELAY_0H_TO_48H"] == SPP.C_POSTPONED
    assert cls["HOME_WIN@COMPLETED_AFTER_DELAY_OVER_48H"] == SPP.C_POSTPONED
    assert cls[CC.S_NEVER] == SPP.C_CANCELLED
    assert SPP.outcome_class("HOME_WIN@PARTIAL_PLAY_OFFICIAL") == \
        SPP.C_PARTIAL
    assert SPP.outcome_class("VOID") == SPP.C_CANCELLED
    assert SPP.outcome_class("POSTPONED") == SPP.C_POSTPONED


def test_fixed_payouts_that_complement_everywhere_are_exact():
    b, (y, t) = legs(inst("KALSHI", "K-NYY", "YES", "HOME", terms=SCALAR),
                     inst("POLYMARKET_US", "aec-mlb-tb-nyy", "YES", "AWAY",
                          terms=SCALAR))
    pol = SPP.evaluate_pair(y, t, b["states"])
    assert pol["verdict"] == SPP.EXACT and pol["refusals"] == []
    assert pol["guaranteed_floor"] == "1"
    for c in (SPP.C_NORMAL, SPP.C_POSTPONED, SPP.C_CANCELLED):
        assert pol["classes"][c]["exact"] is True
    assert pol["classes"][SPP.C_PARTIAL]["folded"] is True
    assert pol["classes"][SPP.C_PARTIAL]["basis"] == SPP.B_PARTIAL_FOLDED


def test_one_markets_fair_price_and_its_own_no_complement_exactly():
    b, (y, n) = legs(inst("KALSHI", "K-NYY", "YES", "HOME"),
                     inst("KALSHI", "K-NYY", "NO", "HOME"))
    pol = SPP.evaluate_pair(y, n, b["states"])
    assert pol["verdict"] == SPP.EXACT
    row = never(pol)
    assert row["a"]["token"] == "FP[K-NYY]"
    assert row["b"]["token"] == "1-FP[K-NYY]"
    assert row["basis"] == SPP.B_SAME_MARKET and row["pair_lo"] == "1"


def test_separate_markets_fair_prices_are_never_assumed_to_sum_to_one():
    """THE DIRECTIVE'S CASE: Kalshi's Yankees market YES + its Rays market
    YES. In every ordinary result they pay exactly $1; in a cancelled game
    (and a game completed beyond the 48 h window) each pays ITS OWN market's
    last fair price -- two numbers, each in [0, 1]."""
    b, (y, t) = legs(inst("KALSHI", "K-NYY", "YES", "HOME"),
                     inst("KALSHI", "K-TB", "YES", "AWAY"))
    pol = SPP.evaluate_pair(y, t, b["states"])
    assert pol["verdict"] == SPP.PRICED
    assert pol["assumes_separate_market_fair_prices_sum_to_one"] is False
    row = never(pol)
    assert row["basis"] == SPP.B_SEPARATE
    assert (row["pair_lo"], row["pair_hi"]) == ("0", "2")
    assert pol["guaranteed_floor"] == "0"
    assert pol["classes"][SPP.C_NORMAL]["exact"] is True
    assert pol["classes"][SPP.C_CANCELLED] == {
        "states": [CC.S_NEVER], "exact": False, "pair_lo": "0",
        "pair_hi": "2", "bases": [SPP.B_SEPARATE]}
    post = pol["classes"][SPP.C_POSTPONED]
    assert post["exact"] is False and post["pair_lo"] == "0"
    # every difference is itemised by state, beyond-window states included
    diff_states = {d["state"] for d in pol["differences"]}
    assert CC.S_NEVER in diff_states
    assert "HOME_WIN@COMPLETED_AFTER_DELAY_OVER_48H" in diff_states
    assert all(d["difference_lo"] == "-1" for d in pol["differences"])
    # inside the window the delayed game follows its result: exact
    assert "HOME_WIN@COMPLETED_AFTER_DELAY_0H_TO_48H" not in diff_states


def test_a_cross_venue_fair_price_pair_is_priced_the_same_way():
    b, (y, t) = legs(inst("KALSHI", "K-NYY", "YES", "HOME"),
                     inst("POLYMARKET_US", "aec-mlb-tb-nyy", "YES", "AWAY"))
    pol = SPP.evaluate_pair(y, t, b["states"])
    assert pol["verdict"] == SPP.PRICED and pol["guaranteed_floor"] == "0"


def test_a_fair_price_against_a_fixed_payout_is_bounded_by_it():
    b, (y, t) = legs(inst("KALSHI", "K-NYY", "YES", "HOME"),
                     inst("POLYMARKET_US", "aec-mlb-tb-nyy", "YES", "AWAY",
                          terms=SCALAR))
    pol = SPP.evaluate_pair(y, t, b["states"])
    row = never(pol)
    assert (row["pair_lo"], row["pair_hi"]) == ("0.5", "1.5")
    assert pol["verdict"] == SPP.PRICED and pol["guaranteed_floor"] == "0.5"


def test_two_fixed_but_different_void_rules_are_an_exact_priced_difference():
    b, (y, t) = legs(
        inst("KALSHI", "K-NYY", "YES", "HOME", terms=SCALAR),
        inst("POLYMARKET_US", "aec-mlb-tb-nyy", "YES", "AWAY",
             terms=dict(SCALAR, void_rule="RESOLVES_NO")))
    pol = SPP.evaluate_pair(y, t, b["states"])
    row = never(pol)
    assert row["exact"] is True and row["pair_lo"] == "0.5"
    assert pol["verdict"] == SPP.PRICED
    assert pol["guaranteed_floor"] == "0.5"


def test_an_unknown_cancellation_rule_is_refused_by_name():
    y = SPP.Leg("KALSHI", "K-NYY", "YES",
                {"HOME_WIN": "1", "AWAY_WIN": "0", CC.S_NEVER: None,
                 "HOME_WIN@COMPLETED_AFTER_DELAY_OVER_48H": "FP[K-NYY]"},
                resolution_source="MLB")
    t = SPP.Leg("KALSHI", "K-TB", "YES",
                {"HOME_WIN": "0", "AWAY_WIN": "1", CC.S_NEVER: "FP[K-TB]",
                 "HOME_WIN@COMPLETED_AFTER_DELAY_OVER_48H": "FP[K-TB]"},
                resolution_source="MLB")
    pol = SPP.evaluate_pair(y, t, list(y.vector))
    assert pol["verdict"] == SPP.REFUSED
    assert pol["refusal"] == SPP.R_RULE_UNKNOWN
    (r,) = [x for x in pol["refusals"] if x["code"] == SPP.R_RULE_UNKNOWN]
    assert r["leg"] == "a" and r["state"] == CC.S_NEVER
    assert pol["guaranteed_floor"] == "0"   # what IS priced is still stated


def test_an_unknown_postponement_rule_is_refused():
    b, (y, t) = legs(
        inst("KALSHI", "K-NYY", "YES", "HOME", terms=SCALAR),
        inst("POLYMARKET_US", "aec-mlb-tb-nyy", "YES", "AWAY",
             terms=dict(SCALAR, postponement_window_hours=None)))
    pol = SPP.evaluate_pair(y, t, b["states"])
    assert pol["verdict"] == SPP.REFUSED
    bad = {x["state"] for x in pol["refusals"]
           if x["code"] == SPP.R_RULE_UNKNOWN}
    assert bad and all(SPP.outcome_class(s) == SPP.C_POSTPONED for s in bad)


def test_a_stake_back_needs_the_entry_price_and_is_then_exact():
    vec = {"HOME_WIN": "1", "AWAY_WIN": "0", "X@COMPLETED_AFTER_DELAY_OVER_"
           "1H": "SB[P-1]", CC.S_NEVER: "SB[P-1]"}
    other = {"HOME_WIN": "0", "AWAY_WIN": "1", "X@COMPLETED_AFTER_DELAY_OVER_"
             "1H": "0.5", CC.S_NEVER: "0.5"}
    a = SPP.Leg("POLYMARKET_US", "P-1", "YES", vec, resolution_source="MLB")
    b = SPP.Leg("KALSHI", "K-2", "YES", other, resolution_source="MLB")
    pol = SPP.evaluate_pair(a, b, list(vec))
    assert pol["verdict"] == SPP.REFUSED
    assert pol["refusal"] == SPP.R_STAKE_BACK_UNPRICED
    a2 = SPP.Leg("POLYMARKET_US", "P-1", "YES", vec, resolution_source="MLB",
                 entry_price=D("0.44"))
    pol = SPP.evaluate_pair(a2, b, list(vec))
    assert pol["verdict"] == SPP.PRICED
    assert never(pol)["pair_lo"] == "0.94" and never(pol)["exact"] is True


def test_impossible_on_one_leg_only_is_refused():
    a = SPP.Leg("KALSHI", "K-1", "YES", {"HOME_WIN": "1", "AWAY_WIN": "0",
                                        "DRAW": "IMPOSSIBLE",
                                        "H@COMPLETED_AFTER_DELAY_OVER_1H":
                                            "0.5", CC.S_NEVER: "0.5"},
                resolution_source="X")
    b = SPP.Leg("KALSHI", "K-2", "YES", {"HOME_WIN": "0", "AWAY_WIN": "1",
                                        "DRAW": "0.5",
                                        "H@COMPLETED_AFTER_DELAY_OVER_1H":
                                            "0.5", CC.S_NEVER: "0.5"},
                resolution_source="X")
    pol = SPP.evaluate_pair(a, b, list(a.vector))
    assert SPP.R_IMPOSSIBLE_ONE_LEG in [r["code"] for r in pol["refusals"]]
    assert pol["verdict"] == SPP.REFUSED


def test_a_three_way_no_is_not_a_complement_of_the_other_yes():
    fx3 = CC.Fixture(event_key="EPL:2026-10-08T14:00Z:CHE@ARS",
                     sport="SOCCER", league="EPL", start_epoch=START,
                     outcome_kind="THREE_WAY", home="ARS", away="CHE")
    a = inst("KALSHI", "K-ARS", "YES", "HOME", terms=SCALAR)
    c = inst("KALSHI", "K-CHE", "NO", "AWAY", terms=SCALAR)
    b = CC.build_claims(fx3, [a, c])
    pol = SPP.evaluate_pair(AC.policy_leg(a), AC.policy_leg(c), b["states"])
    assert pol["verdict"] == SPP.REFUSED
    assert pol["refusal"] == SPP.R_NOT_COMPLEMENT


def test_a_state_space_without_a_cancelled_state_is_refused():
    a = SPP.Leg("K", "1", "YES", {"HOME_WIN": "1", "AWAY_WIN": "0",
                                  "H@COMPLETED_AFTER_DELAY_OVER_1H": "0.5"},
                resolution_source="X")
    b = SPP.Leg("K", "2", "YES", {"HOME_WIN": "0", "AWAY_WIN": "1",
                                  "H@COMPLETED_AFTER_DELAY_OVER_1H": "0.5"},
                resolution_source="X")
    pol = SPP.evaluate_pair(a, b, list(a.vector))
    assert pol["verdict"] == SPP.REFUSED
    (r,) = pol["refusals"]
    assert r["code"] == SPP.R_CLASS_NOT_ENUMERATED
    assert r["class"] == SPP.C_CANCELLED


def test_partial_play_is_enumerated_or_folded_on_one_source_else_refused():
    b, (y, t) = legs(inst("KALSHI", "K-NYY", "YES", "HOME", terms=SCALAR),
                     inst("POLYMARKET_US", "aec-mlb-tb-nyy", "YES", "AWAY",
                          terms=dict(SCALAR, verification_sources=None)))
    pol = SPP.evaluate_pair(y, t, b["states"])
    # every enumerated payout is an exact complement...
    assert pol["payout_verdict"] == SPP.EXACT
    # ...but a shortened game may be official on one leg and not the other
    assert pol["verdict"] == SPP.REFUSED
    assert pol["refusal"] == SPP.R_PARTIAL_UNKNOWN
    assert pol["classes"][SPP.C_PARTIAL]["folded"] is False
    # an explicit partial-play state is priced like any other
    s = "HOME_WIN@PARTIAL_PLAY_OFFICIAL"
    ya = SPP.Leg(y.venue, y.market_id, y.side, dict(y.vector, **{s: "1"}),
                 resolution_source=y.resolution_source)
    ta = SPP.Leg(t.venue, t.market_id, t.side, dict(t.vector, **{s: "0.5"}),
                 resolution_source=t.resolution_source)
    pol = SPP.evaluate_pair(ya, ta, b["states"] + [s])
    assert pol["classes"][SPP.C_PARTIAL]["pair_lo"] == "1.5"
    assert pol["verdict"] == SPP.PRICED


def test_a_priced_difference_carries_an_expected_floor_never_a_guarantee():
    from sportsassets import bettor_settlement_difference_policy as SDP
    b, (y, t) = legs(inst("KALSHI", "K-NYY", "YES", "HOME"),
                     inst("KALSHI", "K-TB", "YES", "AWAY"))
    pol = SPP.evaluate_pair(y, t, b["states"], sport_family="baseball")
    ef = pol["expected_floor"]
    q = D(str(SDP.rate("baseball")["q_hi"]))
    assert ef["is"] == "EXPECTED_LOWER_BOUND_NOT_A_GUARANTEE"
    assert D(ef["value"]) == 1 - q * (1 - 0)
    assert pol["guaranteed_floor"] == "0"      # the guarantee is untouched
    assert SPP.evaluate_pair(y, t, b["states"], sport_family="curling")[
        "expected_floor"]["held"] is False


# ── 2. BOUND INTO ADRIANA'S CLAIM-FIRST SCAN ─────────────────────────────

def test_the_scan_prices_non_complementary_pairs_instead_of_only_counting():
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.30"), 100)])
    t = inst("KALSHI", "K-TB", "YES", "AWAY", [(D("0.30"), 100)])
    b = CC.build_claims(FX, [y, t])
    assert y.fingerprint and t.fingerprint and len(b["classes"]) == 2
    res = AC.scan_fixture(FX, b, now=NOW)
    assert res["pairs_not_complementary"] == 1 and res["records"] == []
    tl = res["settlement_pair_policy"]
    assert tl["by_verdict"] == {SPP.PRICED: 1}
    assert tl["priced_floors"] == {"0": 1}
    (ex,) = tl["priced_examples"]
    assert ex["complementary"] is False and ex["guaranteed_floor"] == "0"
    # the census carries it, with the assumption it does NOT make
    cen = AC.census_result([res], markets_read=2, books_fresh=2,
                           skipped={})["census"]["settlement_pair_policy"]
    assert cen["by_verdict"] == {SPP.PRICED: 1}
    assert cen["assumes_separate_market_fair_prices_sum_to_one"] is False


def test_a_fair_price_pair_never_reaches_the_engine_as_a_complement(
        monkeypatch):
    """THE ENGINE BOUNDARY. If the claim layer ever let a separate-market
    fair-price pair through as 'complementary', the engine would run on the
    0.5 fair-price substitution (0.5 + 0.5 = $1) and, at asks summing to
    0.60, call it GUARANTEED_AFTER_COSTS. The policy refuses it first."""
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.30"), 100)])
    t = inst("KALSHI", "K-TB", "YES", "AWAY", [(D("0.30"), 100)])
    b = CC.build_claims(FX, [y, t])
    monkeypatch.setattr(CC, "complement_states",
                        lambda a, bb, states, **kw: (True, []))
    res = AC.scan_fixture(FX, b, now=NOW)
    (rec,) = res["records"]
    assert rec["verdict"] == A.REFUSED
    assert rec["prices_compared"] is False
    assert SPP.R_PRICED_NOT_A_COMPLEMENT in A.reason_codes(rec)
    assert rec["settlement_pair_policy"]["guaranteed_floor"] == "0"
    assert not [r for r in res["records"]
                if r["verdict"] == A.GUARANTEED_AFTER_COSTS]


def test_an_exact_complement_still_reaches_the_engine_unchanged():
    y = inst("KALSHI", "K-NYY", "YES", "HOME", [(D("0.44"), 100)],
             terms=SCALAR)
    t = inst("KALSHI", "K-TB", "YES", "AWAY", [(D("0.45"), 100)],
             terms=SCALAR)
    res = AC.scan_fixture(FX, CC.build_claims(FX, [y, t]), now=NOW)
    (rec,) = res["records"]
    assert rec["verdict"] == A.GUARANTEED_AFTER_COSTS, rec["reasons"]
    assert rec["claim_pair"]["settlement_pair_policy"] == SPP.VERSION
    assert res["settlement_pair_policy"]["by_verdict"] == {SPP.EXACT: 1}


# ── 3. TAXONOMY AND PURITY ───────────────────────────────────────────────

@pytest.mark.parametrize("code", list(SPP.REFUSALS)
                         + [SPP.R_PRICED_NOT_A_COMPLEMENT])
def test_every_policy_code_is_a_settlement_compatibility_refusal(code):
    assert TT.TABLE[code] == ("SOFTWARE", "SETTLEMENT",
                              "SETTLEMENT_COMPATIBILITY")


def test_the_policy_is_pure_and_holds_no_authority():
    import ast
    import pathlib
    src = pathlib.Path(SPP.__file__).read_text()
    mods = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.ImportFrom):
            mods.add(n.module or "")
        elif isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
    assert mods <= {"__future__", "dataclasses", "decimal", "",
                    "bettor_settlement_difference_policy"}, mods
    for word in ("submit", "cancel_order", "credential", "INSERT", "httpx",
                 "requests"):
        assert word not in src, word
