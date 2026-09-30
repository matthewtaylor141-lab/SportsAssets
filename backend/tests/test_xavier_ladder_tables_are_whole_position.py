"""XAVIER'S PAYOUT TABLES: EVERY STATE, THE COMBINED MINIMUM, NOTHING ASSUMED.

Pure checks of `agents.xavier_ladder` on stated inputs (no database, no
clock):
  * a void whose payout the settlement rules do NOT establish is shown
    UNESTABLISHED with its bounded range -- never assumed to refund or to
    pay 50 cents -- and an expectation that would need its mass is refused;
  * the position minimum is the minimum over the COMBINED per-state payouts:
    on the study's Bears moneyline (10) + Panthers +4.5 (6) it is the
    Bears-lose state, not the impossible "both lose" sum of per-leg minima;
    the 4 unpaired moneyline contracts are in every row;
  * a void the rules DO establish (50-50) is valued at what they say.
"""
from __future__ import annotations

import dataclasses

import pytest

from sportsassets import bettor_indirect_structures as IS
from sportsassets.agents import xavier_ladder as XL

VOID_MEASURED = {"ok": True, "rate": 0.04, "upper_95": 0.12}


def test_an_unestablished_void_payout_is_shown_unestablished_and_never_assumed():
    inputs = {"held_leg": None, "sport_permits_tie": None,
              "fixture_can_void": True, "p_win": 0.55,
              "void_read": VOID_MEASURED}
    t = XL.held_table(inputs, kept=10, cash=0.0, total_basis=5.0)
    assert t["states_source"] == \
        "BINARY_PAYOUTS_VOID_PAYOUT_NOT_ESTABLISHED"
    void = [r for r in t["rows"] if r["state"] == IS.STATE_VOID]
    assert len(void) == 1
    v = void[0]
    assert v["established"] is False
    assert v["net_pnl_usd"] is None and v["combined_payout_usd"] is None
    assert v["net_pnl_range_usd"] == [-5.0, 5.0]          # [0, 100] cents
    # the established minimum is the losing state; the low end of the
    # unestablished range is reported beside it, not folded in
    assert t["position_minimum_usd"] == -5.0
    assert t["unestablished_states"][0]["state"] == IS.STATE_VOID
    # an expectation with void mass on an unknown payout is refused
    probs = {c["payout_class"]: c["probability"]
             for c in t["probability_classes"]}
    assert probs["VOID"] == pytest.approx(0.04)
    assert XL._expect(t["probability_classes"]) is None
    sens = XL._held_void_sensitivity(inputs, 10, 0.0, 5.0)
    assert sens["expected_at_void_zero_usd"] == pytest.approx(0.5)
    assert sens["expected_at_void_point_usd"] is None
    assert "not established" in sens["why"]


def test_an_unmeasured_void_rate_gives_no_expectation_rather_than_zero_mass():
    inputs = {"held_leg": None, "sport_permits_tie": None,
              "fixture_can_void": True, "p_win": 0.55,
              "void_read": {"ok": False}}
    t = XL.held_table(inputs, kept=10, cash=0.0, total_basis=5.0)
    probs = {c["payout_class"]: c["probability"]
             for c in t["probability_classes"]}
    assert probs["VOID"] is None
    assert XL._expect(t["probability_classes"]) is None
    assert XL.measure_of(inputs)["void_status"] == XL.R_VOID_UNMEASURED


def _legs():
    ml = dataclasses.replace(IS.BEARS_MONEYLINE, quantity=10,
                             cost_cents_per_unit=55)
    pan = dataclasses.replace(IS.PANTHERS_PLUS_4_5, quantity=6,
                              cost_cents_per_unit=40)
    return ml, pan


def test_the_position_minimum_is_over_combined_states_not_a_sum_of_minima():
    ml, pan = _legs()
    inputs = {"held_leg": ml, "hedge_held_leg": pan,
              "sport_permits_tie": True, "fixture_can_void": True}
    basis = 10 * 0.55 + 6 * 0.40
    t = XL.group_table(inputs, kept_p=10, kept_h=6, cash=0.0,
                       total_basis=basis)
    assert t["ok"] is True and t["kind"] == "GROUP"
    est = [r for r in t["rows"] if r.get("established")]
    # EVERY ROW is 10 moneyline + 6 run line
    for r in est:
        c = r["per_leg_cents_per_unit"]
        assert r["combined_payout_usd"] == pytest.approx(
            (10 * c[0] + 6 * c[1]) / 100.0)
    by = {tuple(r["per_leg_cents_per_unit"]): r["net_pnl_usd"] for r in est
          if r["state"] == IS.STATE_REGULAR}
    assert by[(0, 100)] == pytest.approx(6.0 - basis)      # Bears lose
    assert by[(100, 100)] == pytest.approx(16.0 - basis)   # Bears by 1-4
    assert by[(100, 0)] == pytest.approx(10.0 - basis)     # Bears by 5+
    # THE 50-50 VOID AND TIE ARE ESTABLISHED BY THESE RULES, and valued so
    void = [r for r in est if r["state"] == IS.STATE_VOID][0]
    assert void["combined_payout_usd"] == pytest.approx(5.0 + 3.0)
    tie = [r for r in est if r["state"] == IS.STATE_TIE][0]
    assert tie["combined_payout_usd"] == pytest.approx(5.0 + 6.0)
    # THE MINIMUM: the Bears-lose state -- the combined payout's minimum
    assert t["position_minimum_usd"] == pytest.approx(6.0 - basis)
    # a sum of per-leg minima from different states (both legs losing)
    # describes an outcome that cannot happen, and is not the minimum
    sum_of_minima = (0 - 10 * 0.55) + (0 - 6 * 0.40)
    assert t["position_minimum_usd"] > sum_of_minima + 1.0
    assert t["minimum_rule"] == XL.MINIMUM_RULE
    assert t["probabilities"] == XL.NOT_ESTABLISHED


def test_a_partial_exit_of_one_leg_values_what_is_kept_and_what_is_realised():
    ml, pan = _legs()
    inputs = {"held_leg": ml, "hedge_held_leg": pan,
              "sport_permits_tie": True, "fixture_can_void": True}
    basis = 10 * 0.55 + 6 * 0.40
    # sell 4 moneyline contracts for 2.40 net: 6 and 6 kept
    t = XL.group_table(inputs, kept_p=6, kept_h=6, cash=2.40,
                       total_basis=basis)
    est = {tuple(r["per_leg_cents_per_unit"]): r for r in t["rows"]
           if r.get("established") and r["state"] == IS.STATE_REGULAR}
    assert est[(0, 100)]["net_pnl_usd"] == pytest.approx(6.0 + 2.40 - basis)
    assert est[(100, 0)]["net_pnl_usd"] == pytest.approx(6.0 + 2.40 - basis)
    assert t["quantities"]["kept_primary"] == 6


def test_a_budget_limited_search_says_so_and_never_concludes_absence():
    alts = [{"action": "ACQUIRE_INDIRECT_HEDGE", "candidate_id": "c1"}]
    step = {"search_order": ["c1", "c2", "c3"],
            "discovery": {"examined": 3, "rejected": []},
            "hedge_candidate_ranking": {
                "ranked": [{"condition_id": "c1"}],
                "not_rankable": [{"condition_id": "c2"}]}}
    trunc = {"candidate_legs_read": {"examined": 40, "built": 3,
                                     "refused": [], "truncated_at_limit": True,
                                     "limit": 40,
                                     "fixture_candidate_pairs": 156}}
    got = XL.search_completeness(facts=trunc, step=step, alts=alts,
                                 option_refusals=[{"candidate_id": "c3",
                                                   "refusal": "X"}])
    assert got["budget_statement"].startswith("THE_SEARCH_WAS_BUDGET_LIMITED")
    assert got["every_admitted_reached_the_comparison"] is True
    # an admitted contract neither compared nor refused by name is a defect
    got = XL.search_completeness(facts=trunc, step=step, alts=alts)
    assert got["every_admitted_reached_the_comparison"] is False
    assert got["admitted_missing_from_the_comparison"] == ["c3"]
    # the supplier's truncation not carried: said, and absence not concluded
    got = XL.search_completeness(facts={"candidate_legs_read": {}},
                                 step=step, alts=alts)
    assert got["budget_statement"].startswith("NOT_REPORTED_BY_THE_SUPPLIER")


def _facts(**clr):
    base = {"examined": 4, "built": 4, "refused": [],
            "truncated_at_limit": False, "limit": 40,
            "fixture_candidate_pairs": 4,
            "search_order": {"catalogue_rows_read": 4}}
    base.update(clr)
    return {"held_leg_read": {"ok": True}, "candidate_legs_read": base}


def test_the_search_account_names_how_the_search_ended():
    got = XL.search_account(_facts())
    assert got["complete"] is True and got["stop_reason"] == XL.STOP_COMPLETE
    assert got["comparison_scope"] == "COMPLETE: every sibling examined (4 of 4)"
    # a quote refused for the pass deadline was NOT examined
    got = XL.search_account(_facts(refused=[
        {"stage": "QUOTE", "refusal": "THIS_CANDIDATES_OWN_PRICE_WAS_NOT_"
         "ESTABLISHED", "quote_refusal": "DECISION_DEADLINE_PASSED_BEFORE_"
         "DISPATCH"},
        {"stage": "BUILD", "refusal": "THE_LEG_IS_MISSING_A_FACT"}]))
    assert got["stop_reason"] == XL.STOP_DEADLINE and got["complete"] is False
    assert got["examined"] == 3 and got["unexamined"] == 1
    assert got["excluded"] == {"BUILD:THE_LEG_IS_MISSING_A_FACT": 1}
    assert got["comparison_scope"] == \
        "BEST_AMONG_EXAMINED (3 of 4; 1 unexamined: DEADLINE)"
    # the catalogue read's own limit
    got = XL.search_account(_facts(fixture_candidate_pairs=450,
                                   examined=40, limit=40,
                                   search_order={"catalogue_rows_read": 400}))
    assert got["stop_reason"] == XL.STOP_LIMIT
    got = XL.search_account(_facts(fixture_candidate_pairs=450,
                                   examined=40, limit=40,
                                   truncated_at_limit=True))
    assert got["stop_reason"] == XL.STOP_BUDGET
    assert got["unexamined"] == 410
    # no held leg: the search never ran, and nothing is concluded
    got = XL.search_account({"held_leg_read": {"ok": False,
                                               "refusal": "NO_ROW"},
                             "candidate_legs_read": {}})
    assert got["stop_reason"] == XL.STOP_NOT_RUN and got["complete"] is False
    got = XL.search_account({"candidate_legs_read": {"examined": 2}})
    assert got["stop_reason"] == XL.STOP_NOT_REPORTED
    assert got["complete"] is None
