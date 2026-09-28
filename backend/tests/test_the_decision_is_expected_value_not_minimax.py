"""THE POLICY IS EXPECTED VALUE. WORST CASE IS A CONSTRAINT.

WHAT WAS WRONG. `bettor_funded_indirect_pair.rank_actions` sorted by highest net
worst case and contained no probability at all. That is a MINIMAX policy, and this
system's policy is expected value -- `bettor_mgmt_select.rank_with_hold` has been
deciding that way, and states the reason:

    "Live, HOLD has no value and DIRECT_EXIT does. Ranking by whatever number is
     available selects the exit every time -- not because exiting is good but
     because it is the only action carrying a figure. That is an engine that
     liquidates the book for want of a settlement model."

A minimax ranking is that failure pointed the other way: a holding with POSITIVE
EXPECTED VALUE whose worst case is losing the stake loses to an immediate sale
every time, so liquidation wins for the wrong reason. The counterexample below is
exactly that position, and it is the first test in the file because it is the whole
argument.

WHAT THIS FILE PINS:
  1 The counterexample: minimax picks the sale, expected value picks the hold.
  2 One comparison -- the indirect acquisition is a candidate inside
    `rank_with_hold`'s own candidate set, scored in the same unit.
  3 Worst case as a CONSTRAINT applied BEFORE the choice, so a breach is removed
    rather than out-ranked.
  4 The inherited HOLD guard: unpriced HOLD selects nothing, including an
    acquisition.
  5 No preference manufactured by omission -- an alternative missing its fee,
    depth, capital or region probabilities is NOT RANKABLE, never scored at zero,
    and REDUCE is not a placeholder.
  6 The five quantities stay separate and are never blended into one score.
"""

from __future__ import annotations

import dataclasses

import pytest

from sportsassets import bettor_funded_decision as FD
from sportsassets import bettor_funded_indirect_pair as IP
from sportsassets import bettor_indirect_structures as IS

#: A two-outcome fixture's regions, from the classifier's own table, with a
#: probability on each. Labelled as an EXTERNAL source: the module reports the
#: label and never lets it change the arithmetic.
def _probs(table, p_a_wins):
    """Put `p_a_wins` on every region where leg A pays, the rest on the others."""
    a_regions = [r["region"] for r in table if r["per_leg_cents"][0] > 0]
    others = [r["region"] for r in table if r["region"] not in a_regions]
    out = {}
    for r in a_regions:
        out[r] = p_a_wins / len(a_regions)
    for r in others:
        out[r] = (1.0 - p_a_wins) / len(others)
    return out


def _structure(*, cost_a=49, cost_b=50):
    a = dataclasses.replace(IS.BEARS_MONEYLINE, kind=IS.KIND_MONEYLINE,
                            cost_cents_per_unit=cost_a)
    b = IS.Leg(condition_id="ml-B", fixture_id=a.fixture_id,
               kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
               overtime=IS.OT_INCLUDED, backs="B", quantity=1,
               cost_cents_per_unit=cost_b, tie_rule="tie resolves 50-50",
               void_rule="void: 50-50", settlement_text_captured=True)
    return IS.classify(a, b, sport_permits_tie=False, fixture_can_void=False,
                       fixture_can_postpone=False)


def _hold_ranking(*, hold_value_usd, exit_value_usd, reduce_value_usd=None,
                  hold_downside_usd=None):
    """A `rank_with_hold`-shaped result. Hand-built so this file tests the
    DECISION RULE rather than re-testing the selector, which has its own suite.
    The shape is the selector's: `candidates` with `action`/`value_usd`, and
    `not_rankable` with `action`/`blocker`."""
    cands = []
    if hold_value_usd is not None:
        cands.append({"action": "HOLD", "qty": 10,
                      "value_usd": hold_value_usd,
                      "expected_net_usd": hold_value_usd,
                      "downside_usd": hold_downside_usd,
                      "incremental_capital_usd": 0.0,
                      "capital_duration_h": 24.0,
                      "evidence_quality": FD.EVIDENCE_EXTERNAL_LABELLED,
                      "execution_secured": True})
    if exit_value_usd is not None:
        cands.append({"action": "DIRECT_EXIT", "qty": 10,
                      "value_usd": exit_value_usd,
                      "expected_net_usd": exit_value_usd,
                      "downside_usd": exit_value_usd,
                      "incremental_capital_usd": 0.0,
                      "capital_duration_h": 0.0,
                      "evidence_quality": FD.EVIDENCE_VENUE_IMPLIED,
                      "execution_secured": False})
    if reduce_value_usd is not None:
        cands.append({"action": "REDUCE", "qty": 5,
                      "value_usd": reduce_value_usd,
                      "expected_net_usd": reduce_value_usd,
                      "downside_usd": reduce_value_usd,
                      "incremental_capital_usd": 0.0,
                      "capital_duration_h": 12.0,
                      "evidence_quality": FD.EVIDENCE_EXTERNAL_LABELLED,
                      "execution_secured": False})
    out = {"version": "MGMT_SELECT_SHAPE", "candidates": cands,
           "not_rankable": []}
    if hold_value_usd is None:
        out["not_rankable"].append({"action": "HOLD",
                                    "blocker": "HOLD_NOT_PRICED",
                                    "value_usd": None,
                                    "is_not_zero": "NOT_IDENTIFIED"})
    return out


def _indirect(*, p_a_wins=0.5, cost_b=50, fee=0.0, depth_qty=1,
              hedge_fee=0.0, capital_duration_h=24.0):
    s = _structure(cost_b=cost_b)
    wc = IP.net_worst_case(s, fee_usd=fee, fee_basis="test")
    return FD.indirect_candidate(
        structure=s,
        region_probabilities=_probs(s.table, p_a_wins),
        evidence_quality=FD.EVIDENCE_EXTERNAL_LABELLED,
        fee_usd=fee,
        depth=IP.depth_supports(wanted_qty=1, depth_qty_at_price=depth_qty),
        incremental=IP.incremental_capital_usd(hedge_qty=1,
                                               hedge_price=cost_b / 100.0,
                                               hedge_fee_usd=hedge_fee),
        capital_duration_h=capital_duration_h,
        worst_case=wc)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE COUNTEREXAMPLE
# ═════════════════════════════════════════════════════════════════════

def test_a_positive_ev_hold_whose_worst_case_loses_the_stake_is_not_liquidated():
    """THE WHOLE ARGUMENT, AS ONE POSITION.

    10 contracts bought at 0.50. The hold is worth +$2.00 in expectation. Its
    worst case is losing the entire $5.00 stake, because a losing moneyline pays
    nothing. Selling now nets -$0.20.

    MINIMAX PICKS THE SALE: -0.20 beats -5.00. EXPECTED VALUE PICKS THE HOLD:
    +2.00 beats -0.20. The second is the policy, and the first is how a book gets
    liquidated for want of a settlement model.
    """
    hr = _hold_ranking(hold_value_usd=2.00, exit_value_usd=-0.20,
                       hold_downside_usd=-5.00)

    # WHAT THE DOWNSIDE VIEW WOULD HAVE SAID, shown rather than asserted about.
    by_downside = sorted(hr["candidates"],
                         key=lambda c: -c["downside_usd"])
    assert by_downside[0]["action"] == "DIRECT_EXIT", (
        "the premise of the counterexample: on downside alone the sale wins")

    got = FD.decide(hold_ranking=hr)
    assert got["selected"] == "HOLD", got["selection_reason"]
    assert got["policy"] == "EXPECTED_NET_VALUE"
    assert got["worst_case_is"] == "A_CONSTRAINT_NOT_THE_SORT_KEY"
    assert got["margin_over_runner_up"] == pytest.approx(2.20)


def test_the_same_position_is_removed_only_when_a_real_limit_forbids_it():
    """AND THE CONSTRAINT STILL BITES. With an owner-approved downside limit of
    $3, the hold's -$5 worst case breaches it, so the hold is REMOVED -- not
    out-ranked -- and the decision falls to the next admissible action. That is
    the difference between a risk limit and a scoring penalty."""
    hr = _hold_ranking(hold_value_usd=2.00, exit_value_usd=-0.20,
                       hold_downside_usd=-5.00)
    got = FD.decide(hold_ranking=hr, limits={"max_downside_usd": 3.00})
    assert got["selected"] == "DIRECT_EXIT", got["selection_reason"]
    blocked = {b["action"]: b for b in got["not_rankable"]}
    assert blocked["HOLD"]["blocker"] == FD.R_DOWNSIDE_LIMIT_BREACHED
    assert "not out-ranked" in blocked["HOLD"]["why"]
    # AND THE BREACHING CANDIDATE CARRIES NO SCORE, so it cannot be read as a
    # ranked runner-up.
    assert blocked["HOLD"]["value_usd"] is None


def test_with_no_approved_limit_nothing_is_filtered_and_that_is_stated():
    """INVENTING A LIMIT WOULD BE INVENTING RISK AUTHORITY. Applying none
    silently would let an unbounded candidate rank without the reader knowing."""
    got = FD.decide(hold_ranking=_hold_ranking(hold_value_usd=2.00,
                                               exit_value_usd=-0.20,
                                               hold_downside_usd=-5.00))
    assert got["limits_applied"]["max_downside_usd"] is None
    assert "NOTHING IS FILTERED" in got["limits_applied"]["no_limit_means"]
    assert got["selected"] == "HOLD"


# ═════════════════════════════════════════════════════════════════════
# 2 · ONE COMPARISON, INCLUDING THE INDIRECT ACQUISITION
# ═════════════════════════════════════════════════════════════════════

def test_the_indirect_acquisition_is_scored_in_the_same_unit_as_hold():
    """NOT A SEPARATE RANKING. The acquisition joins `rank_with_hold`'s own
    candidate list and is compared on expected net value like everything else."""
    cand = _indirect(p_a_wins=0.5, cost_b=50, fee=0.02, hedge_fee=0.01)
    assert cand["rankable"] is True, cand
    # complement pays 100c in every region, cost 99c, fee 2c -> -1c expected
    assert cand["expected_payout_usd"] == pytest.approx(1.00)
    assert cand["expected_net_usd"] == pytest.approx(-0.01)
    got = FD.decide(hold_ranking=_hold_ranking(hold_value_usd=0.50,
                                               exit_value_usd=-0.20),
                    indirect=cand)
    actions = [c["action"] for c in got["candidates"]]
    assert FD.ACTION_ACQUIRE_INDIRECT_HEDGE in actions
    assert got["selected"] == "HOLD", got["selection_reason"]


def test_an_eligible_indirect_middle_is_preferred_when_the_economics_justify_it():
    """AND IT MUST BE ABLE TO WIN. A structure whose expected net value beats
    both holding and selling is selected -- otherwise the comparison is an
    argument against pairing rather than a comparison."""
    cand = _indirect(p_a_wins=0.5, cost_b=40, fee=0.0, hedge_fee=0.0)
    # complement pays 100c in every region; cost 49 + 40 = 89c -> +11c
    assert cand["expected_net_usd"] == pytest.approx(0.11)
    got = FD.decide(hold_ranking=_hold_ranking(hold_value_usd=0.05,
                                               exit_value_usd=-0.20,
                                               reduce_value_usd=-0.05),
                    indirect=cand)
    assert got["selected"] == FD.ACTION_ACQUIRE_INDIRECT_HEDGE, \
        got["selection_reason"]
    assert got["selected_candidate"]["taxonomy"] == IS.DIRECT_COMPLEMENT
    assert got["selected_candidate"]["execution_secured"] is False, (
        "an acquisition needs a fill, and the candidate must say so")


def test_reduce_is_scored_and_is_not_a_placeholder():
    """THE OLD VIEW LEFT REDUCE UNSCORED, which meant it could never be chosen
    and its presence was decoration. Here it carries a figure and can win."""
    got = FD.decide(hold_ranking=_hold_ranking(hold_value_usd=-0.40,
                                               exit_value_usd=-0.60,
                                               reduce_value_usd=-0.10))
    scored = {c["action"]: c["value_usd"] for c in got["candidates"]}
    assert scored["REDUCE"] == pytest.approx(-0.10)
    assert got["selected"] == "REDUCE", got["selection_reason"]


# ═════════════════════════════════════════════════════════════════════
# 3 · NO PREFERENCE MANUFACTURED BY OMISSION
# ═════════════════════════════════════════════════════════════════════

def test_an_unpriced_hold_selects_nothing_including_an_acquisition():
    """THE INHERITED GUARD. A comparison missing the value of continuing to hold
    cannot show that spending NEW money is better than doing nothing, so adding
    an acquisition to that comparison makes it worse rather than better."""
    cand = _indirect(p_a_wins=0.5, cost_b=40)
    assert cand["rankable"] is True
    got = FD.decide(hold_ranking=_hold_ranking(hold_value_usd=None,
                                               exit_value_usd=-0.20),
                    indirect=cand)
    assert got["selected"] is None, got
    assert got["refusal"] == FD.R_HOLD_NOT_PRICED
    assert "including the indirect acquisition" in got["selection_reason"]
    assert got["hold_is_priced"] is False


def test_an_acquisition_without_its_fee_is_not_rankable():
    """AN ALTERNATIVE SCORED WITHOUT ITS FEE IS A MANUFACTURED PREFERENCE. At
    these margins the fee decides the sign."""
    s = _structure()
    cand = FD.indirect_candidate(
        structure=s, region_probabilities=_probs(s.table, 0.5),
        evidence_quality=FD.EVIDENCE_EXTERNAL_LABELLED,
        fee_usd=None,
        depth=IP.depth_supports(wanted_qty=1, depth_qty_at_price=1),
        incremental=IP.incremental_capital_usd(hedge_qty=1, hedge_price=0.5,
                                               hedge_fee_usd=0.0))
    assert cand["rankable"] is False
    assert cand["blocker"] == FD.R_FEES_NOT_PRICED
    got = FD.decide(hold_ranking=_hold_ranking(hold_value_usd=0.05,
                                               exit_value_usd=-0.20),
                    indirect=cand)
    assert FD.ACTION_ACQUIRE_INDIRECT_HEDGE not in [
        c["action"] for c in got["candidates"]]
    assert {b["action"]: b["blocker"] for b in got["not_rankable"]}[
        FD.ACTION_ACQUIRE_INDIRECT_HEDGE] == FD.R_FEES_NOT_PRICED


def test_an_acquisition_the_book_cannot_supply_is_not_rankable():
    cand = _indirect(depth_qty=0)
    assert cand["rankable"] is False
    assert cand["blocker"] == FD.R_DEPTH_NOT_ESTABLISHED
    assert "different position from the one valued" in cand["why"]


def test_unpriced_regions_are_not_treated_as_probability_zero():
    """TREATING AN UNPRICED REGION AS ZERO ASSUMES THAT OUTCOME CANNOT HAPPEN,
    which turns an expectation into an assertion -- and it biases the expectation
    upward exactly when the unpriced region is the one that pays nothing."""
    s = _structure()
    partial = _probs(s.table, 0.5)
    dropped = sorted(partial)[0]
    partial.pop(dropped)
    cand = FD.indirect_candidate(
        structure=s, region_probabilities=partial,
        evidence_quality=FD.EVIDENCE_EXTERNAL_LABELLED, fee_usd=0.0,
        depth=IP.depth_supports(wanted_qty=1, depth_qty_at_price=1),
        incremental=IP.incremental_capital_usd(hedge_qty=1, hedge_price=0.5,
                                               hedge_fee_usd=0.0))
    assert cand["rankable"] is False
    assert cand["blocker"] == FD.R_NO_REGION_PROBABILITIES
    assert dropped in cand["unpriced_regions"]
    assert "cannot happen" in cand["why"]


def test_probabilities_that_do_not_sum_to_one_are_refused():
    """THE REGIONS PARTITION THE FIXTURE'S OUTCOMES. An expectation over a
    mis-normalised measure is not an expectation, and the error is silent."""
    s = _structure()
    bad = {r: 0.9 for r in (x["region"] for x in s.table)}
    cand = FD.indirect_candidate(
        structure=s, region_probabilities=bad,
        evidence_quality=FD.EVIDENCE_EXTERNAL_LABELLED, fee_usd=0.0,
        depth=IP.depth_supports(wanted_qty=1, depth_qty_at_price=1),
        incremental=IP.incremental_capital_usd(hedge_qty=1, hedge_price=0.5,
                                               hedge_fee_usd=0.0))
    assert cand["rankable"] is False
    assert cand["blocker"] == FD.R_PROBABILITIES_DO_NOT_SUM
    assert cand["sums_to"] != pytest.approx(1.0)


def test_an_unestablishable_structure_is_not_rankable_with_its_missing_facts():
    a = IS.Leg(condition_id="ml-unstated-ot", fixture_id="fx",
               kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
               overtime=IS.OT_UNKNOWN, backs="A", quantity=1,
               cost_cents_per_unit=49, tie_rule="t", void_rule="v",
               settlement_text_captured=True)
    b = IS.Leg(condition_id="ml-B", fixture_id="fx", kind=IS.KIND_MONEYLINE,
               period=IS.PERIOD_FULL, overtime=IS.OT_INCLUDED, backs="B",
               quantity=1, cost_cents_per_unit=50, tie_rule="t",
               void_rule="v", settlement_text_captured=True)
    s = IS.classify(a, b, sport_permits_tie=False, fixture_can_void=False,
                    fixture_can_postpone=False)
    cand = FD.indirect_candidate(
        structure=s, region_probabilities={},
        evidence_quality=FD.EVIDENCE_EXTERNAL_LABELLED, fee_usd=0.0,
        depth=IP.depth_supports(wanted_qty=1, depth_qty_at_price=1),
        incremental=IP.incremental_capital_usd(hedge_qty=1, hedge_price=0.5,
                                               hedge_fee_usd=0.0))
    assert cand["rankable"] is False
    assert cand["blocker"] == FD.R_STRUCTURE_IS_UNESTABLISHABLE
    assert cand["missing_facts"], cand


# ═════════════════════════════════════════════════════════════════════
# 4 · THE FIVE QUANTITIES STAY SEPARATE
# ═════════════════════════════════════════════════════════════════════

def test_the_five_quantities_are_reported_apart_and_never_blended():
    """FERRARI'S NUMBERS ARE THE REASON. Merge economics of +$3.84M against a
    -$4.44M settled residual; one blended figure hides both terms."""
    got = FD.decide(hold_ranking=_hold_ranking(hold_value_usd=0.50,
                                               exit_value_usd=-0.20,
                                               hold_downside_usd=-5.00),
                    indirect=_indirect(cost_b=40, fee=0.0))
    five = got["the_five_quantities"]
    for action, q in five.items():
        assert set(q) == {"expected_net_usd", "downside_usd",
                          "incremental_capital_usd", "capital_duration_h",
                          "evidence_quality"}, (action, q)
    assert five["HOLD"]["downside_usd"] == pytest.approx(-5.00)
    assert five["HOLD"]["expected_net_usd"] == pytest.approx(0.50)
    assert five[FD.ACTION_ACQUIRE_INDIRECT_HEDGE][
        "incremental_capital_usd"] == pytest.approx(0.40)
    assert five[FD.ACTION_ACQUIRE_INDIRECT_HEDGE][
        "evidence_quality"] == FD.EVIDENCE_EXTERNAL_LABELLED
    assert "never blended" not in got, "the claim belongs in describe(), not here"
    assert "hides both terms" in FD.describe()["never_blended"]


def test_the_capital_limit_is_a_constraint_too():
    got = FD.decide(hold_ranking=_hold_ranking(hold_value_usd=0.05,
                                               exit_value_usd=-0.20),
                    indirect=_indirect(cost_b=40, fee=0.0),
                    limits={"max_incremental_capital_usd": 0.10})
    blocked = {b["action"]: b for b in got["not_rankable"]}
    assert blocked[FD.ACTION_ACQUIRE_INDIRECT_HEDGE]["blocker"] == \
        FD.R_INCREMENTAL_CAPITAL_LIMIT_BREACHED
    assert got["selected"] == "HOLD"


def test_the_decision_is_never_an_authorisation():
    got = FD.decide(hold_ranking=_hold_ranking(hold_value_usd=0.05,
                                               exit_value_usd=-0.20),
                    indirect=_indirect(cost_b=40, fee=0.0))
    assert got["selected"] == FD.ACTION_ACQUIRE_INDIRECT_HEDGE
    assert "not that capital may be committed" in got["what_this_is_not"]


def test_the_module_states_that_it_calls_rather_than_replaces_the_selector():
    d = FD.describe()
    assert d["existing_policy_is_called_not_replaced"] == \
        "sportsassets.bettor_mgmt_select.rank_with_hold"
    assert d["ranks_on"] == "expected_net_usd"
    assert "liquidation win for the wrong reason" in d["why_not_minimax"]
