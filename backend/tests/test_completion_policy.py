"""§4: the deterministic example, and the identity that makes it work.

The directive states the example and its three answers. These tests
check the module reproduces all three numbers exactly, ranks them
correctly, and -- the part that matters beyond the example -- that the
ranking is provably independent of the historical basis rather than
merely happening to agree with it on this one input.
"""

import pytest

from sportsassets import bettor_completion_policy as CP


# ═════════════════════════════════════════════════════════════════════
# THE DETERMINISTIC EXAMPLE, EXACTLY AS §4 STATES IT
# ═════════════════════════════════════════════════════════════════════

POS = CP.Position(contracts=100.0, basis_per_contract=0.60, leg="YES")
Q = CP.Quotes(complement_ask=0.55, own_bid=0.44)
P_WIN = 0.35


def _by_action(vals):
    return {v.action: v for v in vals}


def test_completion_locks_a_15_cent_loss_before_costs():
    v = _by_action(CP.forward_values(POS, Q, P_WIN, gross=True))
    assert v[CP.COMPLETE_PAIR].status == CP.IDENTIFIED
    assert v[CP.COMPLETE_PAIR].accounting_result == pytest.approx(-0.15)


def test_selling_realizes_a_16_cent_loss_before_costs():
    v = _by_action(CP.forward_values(POS, Q, P_WIN, gross=True))
    assert v[CP.DIRECT_EXIT].accounting_result == pytest.approx(-0.16)


def test_holding_has_an_expected_25_cent_loss_before_costs():
    v = _by_action(CP.forward_values(POS, Q, P_WIN, gross=True))
    assert v[CP.HOLD_TO_SETTLEMENT].accounting_result == pytest.approx(-0.25)


def test_the_engine_compares_them_correctly_and_completes():
    r = CP.rank(POS, Q, P_WIN, gross=True)
    assert r["best"] == CP.COMPLETE_PAIR
    assert [a["action"] for a in r["ranked"]] == list(
        CP.WORKED_EXAMPLE["correct_order"])


def test_the_forward_values_are_the_loss_framing_plus_the_basis():
    """The identity, checked on the directive's own numbers."""
    vals = CP.forward_values(POS, Q, P_WIN, gross=True)
    for v in vals:
        if v.status != CP.IDENTIFIED:
            continue
        assert v.forward_value == pytest.approx(
            v.accounting_result + POS.basis_per_contract), v.action


def test_the_forward_values_are_45_44_and_35_cents():
    v = _by_action(CP.forward_values(POS, Q, P_WIN, gross=True))
    assert v[CP.COMPLETE_PAIR].forward_value == pytest.approx(0.45)
    assert v[CP.DIRECT_EXIT].forward_value == pytest.approx(0.44)
    assert v[CP.HOLD_TO_SETTLEMENT].forward_value == pytest.approx(0.35)


def test_the_module_and_the_directives_example_have_not_drifted():
    e = CP.WORKED_EXAMPLE
    v = _by_action(CP.forward_values(
        CP.Position(1.0, e["held_yes_basis"]),
        CP.Quotes(complement_ask=e["no_executable_at"],
                  own_bid=e["yes_sellable_at"]),
        e["qualified_win_probability"], gross=True))
    b = e["before_costs"]
    assert v[CP.COMPLETE_PAIR].accounting_result == pytest.approx(
        b["complete_locks"])
    assert v[CP.DIRECT_EXIT].accounting_result == pytest.approx(
        b["sell_realizes"])
    assert v[CP.HOLD_TO_SETTLEMENT].accounting_result == pytest.approx(
        b["hold_expected"])


# ═════════════════════════════════════════════════════════════════════
# SUNK COST CANNOT CHANGE THE RANKING (§3)
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("basis", [0.0, 0.01, 0.44, 0.60, 0.99, 1.0, 5.0])
def test_the_ranking_is_identical_at_every_possible_basis(basis):
    """Not 'happens to agree here' -- invariant across the whole range."""
    r = CP.rank(CP.Position(100.0, basis), Q, P_WIN, gross=True)
    assert [a["action"] for a in r["ranked"]] == list(
        CP.WORKED_EXAMPLE["correct_order"]), "basis %.2f reordered them" % basis


@pytest.mark.parametrize("basis", [0.0, 0.60, 5.0])
def test_forward_values_do_not_move_with_basis_at_all(basis):
    vals = _by_action(CP.forward_values(CP.Position(1.0, basis), Q, P_WIN,
                                        gross=True))
    assert vals[CP.COMPLETE_PAIR].forward_value == pytest.approx(0.45)
    assert vals[CP.DIRECT_EXIT].forward_value == pytest.approx(0.44)


def test_a_huge_basis_does_not_make_the_system_refuse_to_realize_a_loss():
    """§3's failure mode, stated as a test.

    At a basis of $5.00 every action is a catastrophic accounting loss.
    A system that will not book a loss would hold. The right answer is
    still to complete -- and it is still the largest forward value.
    """
    r = CP.rank(CP.Position(100.0, 5.00), Q, P_WIN, gross=True)
    assert r["best"] == CP.COMPLETE_PAIR
    acct = CP.accounting_view(CP.Position(100.0, 5.00), Q, P_WIN, gross=True)
    assert all(v < 0 for v in acct["results"].values())


def test_the_accounting_view_refuses_to_be_a_ranking_input():
    acct = CP.accounting_view(POS, Q, P_WIN, gross=True)
    assert acct["this_is_not"] == "A_RANKING_INPUT"


def test_rank_states_why_basis_is_excluded():
    r = CP.rank(POS, Q, P_WIN, gross=True)
    assert "constant" in r["basis_excluded_because"]
    assert r["ranked_on"] == "FORWARD_CASH_PER_CONTRACT"


# ═════════════════════════════════════════════════════════════════════
# THE ORDERING IS NOT HARDCODED: IT MOVES WITH THE PRICES
# ═════════════════════════════════════════════════════════════════════

def test_a_better_bid_makes_selling_the_right_action():
    r = CP.rank(POS, CP.Quotes(complement_ask=0.55, own_bid=0.50), P_WIN,
                gross=True)
    assert r["best"] == CP.DIRECT_EXIT


def test_a_high_qualified_probability_makes_holding_the_right_action():
    r = CP.rank(POS, Q, 0.80, gross=True)
    assert r["best"] == CP.HOLD_TO_SETTLEMENT


def test_an_expensive_complement_is_not_completed():
    r = CP.rank(POS, CP.Quotes(complement_ask=0.90, own_bid=0.44), P_WIN,
                gross=True)
    assert r["best"] == CP.DIRECT_EXIT
    done = [a for a in r["ranked"] if a["action"] == CP.COMPLETE_PAIR][0]
    assert done["forward_value"] == pytest.approx(0.10)


# ═════════════════════════════════════════════════════════════════════
# COSTS ARE APPLIED AFTER THE GROSS COMPARISON, AND CAN FLIP IT
# ═════════════════════════════════════════════════════════════════════

def test_fees_and_carry_are_applied_and_reported():
    costs = CP.Costs(fee_per_contract_taker=0.01,
                     capital_charge_per_contract=0.02,
                     slippage_per_contract=0.005)
    v = _by_action(CP.forward_values(POS, Q, P_WIN, costs=costs))
    # complete: -(0.55 + 0.01 + 0.005) + (1 - 0.02) = 0.415
    assert v[CP.COMPLETE_PAIR].forward_value == pytest.approx(0.415)
    # sell: 0.44 - 0.01 - 0.005 = 0.425
    assert v[CP.DIRECT_EXIT].forward_value == pytest.approx(0.425)
    assert "taker fee" in v[CP.COMPLETE_PAIR].net_of


def test_costs_can_reverse_the_gross_ordering():
    """The gross comparison is the starting point, not the decision."""
    costs = CP.Costs(fee_per_contract_taker=0.01,
                     capital_charge_per_contract=0.02,
                     slippage_per_contract=0.005)
    assert CP.rank(POS, Q, P_WIN, gross=True)["best"] == CP.COMPLETE_PAIR
    assert CP.rank(POS, Q, P_WIN, costs=costs)["best"] == CP.DIRECT_EXIT


# ═════════════════════════════════════════════════════════════════════
# ABSENT INPUTS REFUSE; THEY NEVER DEFAULT TO ZERO
# ═════════════════════════════════════════════════════════════════════

def test_no_complement_ask_refuses_completion_rather_than_pricing_it_free():
    v = _by_action(CP.forward_values(POS, CP.Quotes(own_bid=0.44), P_WIN,
                                     gross=True))
    assert v[CP.COMPLETE_PAIR].status == CP.NOT_IDENTIFIED
    assert v[CP.COMPLETE_PAIR].forward_value is None
    assert "not a free completion" in v[CP.COMPLETE_PAIR].why


def test_no_bid_refuses_the_exit_rather_than_valuing_it_at_zero():
    v = _by_action(CP.forward_values(POS, CP.Quotes(complement_ask=0.55),
                                     P_WIN, gross=True))
    assert v[CP.DIRECT_EXIT].status == CP.NOT_IDENTIFIED
    assert v[CP.DIRECT_EXIT].forward_value is None


def test_no_qualified_probability_refuses_the_hold():
    v = _by_action(CP.forward_values(POS, Q, None, gross=True))
    assert v[CP.HOLD_TO_SETTLEMENT].status == CP.NOT_IDENTIFIED
    assert "0 is not the" in v[CP.HOLD_TO_SETTLEMENT].why


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_a_non_probability_is_refused(bad):
    v = _by_action(CP.forward_values(POS, Q, bad, gross=True))
    assert v[CP.HOLD_TO_SETTLEMENT].status == CP.NOT_IDENTIFIED


def test_refused_actions_are_reported_not_dropped():
    r = CP.rank(POS, CP.Quotes(own_bid=0.44), None, gross=True)
    assert len(r["refused"]) == 2
    assert r["best"] == CP.DIRECT_EXIT


# ═════════════════════════════════════════════════════════════════════
# BEFORE THE FIRST LEG: BOTH BRANCHES ARE PRICED (§4)
# ═════════════════════════════════════════════════════════════════════

FORECAST = CP.CompletionForecast(
    p_completion=0.70,
    expected_complement_cost=0.44,
    expected_minutes_to_complete=20.0,
    p_win_if_unpaired=0.50,
    expected_unpaired_exit_price=0.48,
    unpaired_disposition="SELL",
    adverse_selection_markup=0.02,
    p_partial_fill=0.15,
    expected_partial_fraction=0.5)


def test_initiation_prices_the_completed_and_the_one_leg_only_branch():
    r = CP.initiation_value(0.52, FORECAST)
    assert r["status"] == CP.IDENTIFIED
    assert r["both_branches_priced"] is True
    assert r["completed_branch"]["p"] == 0.70
    assert r["one_leg_only_branch"]["p"] == pytest.approx(0.30)


def test_the_completed_branch_pays_the_adversely_selected_price():
    r = CP.initiation_value(0.52, FORECAST)
    assert r["completed_branch"]["complement_cost_used"] == pytest.approx(0.46)
    assert r["completed_branch"]["includes_adverse_selection"] == 0.02


def test_the_initiation_ev_is_the_two_branch_average_less_entry():
    r = CP.initiation_value(0.52, FORECAST)
    expect = 0.70 * (1.0 - 0.46) + 0.30 * 0.48 - 0.52
    assert r["ev_per_contract"] == pytest.approx(expect)


@pytest.mark.parametrize("field_name", [
    "p_completion", "expected_complement_cost",
    "expected_minutes_to_complete", "p_win_if_unpaired",
    "adverse_selection_markup"])
def test_every_required_forecast_term_is_required(field_name):
    kw = {k: getattr(FORECAST, k) for k in FORECAST.__dataclass_fields__}
    kw[field_name] = None
    r = CP.initiation_value(0.52, CP.CompletionForecast(**kw))
    assert r["status"] == CP.NOT_IDENTIFIED
    assert field_name in r["missing"]


def test_an_undecided_residual_disposition_refuses_the_initiation():
    kw = {k: getattr(FORECAST, k) for k in FORECAST.__dataclass_fields__}
    kw["unpaired_disposition"] = "NOT_DECIDED"
    r = CP.initiation_value(0.52, CP.CompletionForecast(**kw))
    assert r["status"] == CP.NOT_IDENTIFIED
    assert "dominated both case-study accounts" in r["why"]


def test_carrying_the_residual_uses_its_win_probability_not_its_bid():
    kw = {k: getattr(FORECAST, k) for k in FORECAST.__dataclass_fields__}
    kw["unpaired_disposition"] = "CARRY"
    r = CP.initiation_value(0.52, CP.CompletionForecast(**kw))
    assert r["one_leg_only_branch"]["value"] == pytest.approx(0.50)


def test_partial_fills_are_reported_and_not_averaged_into_a_branch():
    r = CP.initiation_value(0.52, FORECAST)
    assert r["partial_fill"]["p"] == 0.15
    assert "neither branch above describes it" in r["partial_fill"]["note"]


def test_a_high_completion_probability_does_not_hide_a_bad_residual():
    """The residual term must still move the EV when p_completion is high."""
    good = CP.CompletionForecast(
        p_completion=0.95, expected_complement_cost=0.44,
        expected_minutes_to_complete=20.0, p_win_if_unpaired=0.50,
        expected_unpaired_exit_price=0.48, unpaired_disposition="SELL",
        adverse_selection_markup=0.02)
    bad_residual = CP.CompletionForecast(
        p_completion=0.95, expected_complement_cost=0.44,
        expected_minutes_to_complete=20.0, p_win_if_unpaired=0.05,
        expected_unpaired_exit_price=0.05, unpaired_disposition="SELL",
        adverse_selection_markup=0.02)
    assert (CP.initiation_value(0.52, good)["ev_per_contract"]
            > CP.initiation_value(0.52, bad_residual)["ev_per_contract"])


def test_capital_charge_makes_a_slow_completion_worse():
    costs = CP.Costs(capital_charge_per_contract=0.03)
    fast = CP.initiation_value(0.52, FORECAST)
    slow = CP.initiation_value(0.52, FORECAST, costs=costs)
    assert slow["ev_per_contract"] < fast["ev_per_contract"]


# ═════════════════════════════════════════════════════════════════════
# EVERY INITIATED POSITION ENTERS THE RESULTS (§4)
# ═════════════════════════════════════════════════════════════════════

def test_the_cohort_identity_catches_an_unaccounted_initiation():
    c = CP.Cohort()
    c.initiate(10)
    c.close(CP.D_COMPLETED, 1.50, n=7)
    with pytest.raises(AssertionError) as e:
        c.assert_identity()
    assert "survivor-selected subset" in str(e.value)


def test_a_complete_cohort_holds_its_identity():
    c = CP.Cohort()
    c.initiate(10)
    c.close(CP.D_COMPLETED, 1.50, n=7)
    c.close(CP.D_SOLD_UNPAIRED, -0.80, n=2)
    c.close(CP.D_CARRIED_UNPAIRED, -1.10, n=1)
    c.assert_identity()
    assert c.report()["identity_holds"] is True


def test_completed_pairs_only_reports_a_different_number():
    """The Ferrari shape: good pairs, worse total."""
    c = CP.Cohort()
    c.initiate(100)
    c.close(CP.D_COMPLETED, 10.8, n=59)
    c.close(CP.D_CARRIED_UNPAIRED, -9.5, n=41)
    r = c.report()
    assert r["result_on_completed_pairs_only"] == pytest.approx(10.8)
    assert r["net_result_all_initiations"] == pytest.approx(1.3)
    assert r["difference"] == pytest.approx(-9.5)
    assert "dominates the account's total" in r["why_the_difference_matters"]


def test_an_unknown_disposition_is_rejected():
    c = CP.Cohort()
    c.initiate(1)
    with pytest.raises(ValueError):
        c.close("MERGED_SOMEHOW", 1.0)


def test_open_positions_are_a_disposition_not_an_omission():
    c = CP.Cohort()
    c.initiate(5)
    c.close(CP.D_COMPLETED, 1.0, n=3)
    c.close(CP.D_OPEN, 0.0, n=2)
    c.assert_identity()
    assert c.report()["by_disposition"][CP.D_OPEN] == 2


# ═════════════════════════════════════════════════════════════════════
# THE CASE-STUDY AVERAGES ARE NOT THRESHOLDS (§4)
# ═════════════════════════════════════════════════════════════════════

def test_no_case_study_average_is_used_as_a_decision_rule():
    """$0.84 and $1.15 must not appear as a comparison in the module."""
    import inspect
    src = inspect.getsource(CP)
    body = src.split("OBSERVED_NOT_A_RULE = {", 1)[1].split("}", 1)[1]
    for lit in ("0.84", "0.8484", "1.15", "1.1529", "0.8328", "1.1541"):
        assert lit not in body, (
            "%s appears outside OBSERVED_NOT_A_RULE; a case-study average "
            "used anywhere else is a hardcoded threshold" % lit)


def test_the_averages_are_labelled_as_measurements_of_another_account():
    o = CP.OBSERVED_NOT_A_RULE
    assert "another account's fills" in o["what_these_are_not"]
    assert "FIFO" in o["what_these_are"]


def test_the_sensitivity_case_is_recorded_beside_the_fifo_number():
    """They move with the lot convention, so they cannot be a threshold."""
    o = CP.OBSERVED_NOT_A_RULE
    assert "0.8682" in o["what_these_are"]
    assert "0.8526" in o["what_these_are"]


def test_describe_exposes_the_identity_and_the_example():
    d = CP.describe()
    assert d["ranks_on"] == "FORWARD_CASH_PER_CONTRACT"
    assert "constant" in d["sunk_cost_identity"]
    assert d["worked_example"]["correct_order"][0] == CP.COMPLETE_PAIR
