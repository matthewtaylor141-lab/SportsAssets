"""EVERY ACTION ON ONE DISTRIBUTION AND ONE ACCOUNTING BASIS.

The owner's requirement: HOLD includes the held contracts' actual exceptional-
settlement payouts; REDUCE is immediate net proceeds plus the correctly valued
residual; EXIT is executable net proceeds with no settlement exposure after;
ACQUIRE is added cost, fees and both legs' actual payouts. "HOLD plus the hedge
increment" is valid only when the two reconcile under the same measure. Shown
here with a NONZERO void rate and UNEQUAL refund / settlement payouts, and any
omitted component is shown to cancel across every compared action.

Every number is a CHOSEN INPUT for arithmetic, not an estimate of any fixture.
"""
from __future__ import annotations

import dataclasses

import pytest

from sportsassets import bettor_common_valuation as CV
from sportsassets import bettor_funded_indirect_pair as FIP
from sportsassets import bettor_indirect_structures as IS
from sportsassets import bettor_payout_states as PS
from sportsassets import bettor_settlement_clauses as SETTLE

Q, BASIS = 10, 6.20                         # 10 held, bought at 0.62
P = 0.70                                    # P(held wins | normal settlement)
EXIT_NET = 6.85                             # full exit, net of fees
REDUCE_QTY, REDUCE_NET = 4, 2.88            # 4 at 0.72 net
FIFTY = {"WIN": 100, "LOSE": 0, "VOID": 50}     # void resolves 50-50
REFUND = {"WIN": 100, "LOSE": 0, "VOID": 62}    # void refunds the basis


def _cands():
    return [{"action": "HOLD"},
            {"action": "DIRECT_EXIT", "qty": Q, "net_proceeds_usd": EXIT_NET},
            {"action": "REDUCE", "qty": REDUCE_QTY,
             "net_proceeds_usd": REDUCE_NET}]


def _vals(held, v):
    got = CV.value_actions(held_cents=held, p_win=P, void_rate=v, qty=Q,
                           basis_usd=BASIS, candidates=_cands())
    assert got["ok"] is True, got
    return got, {r["action"]: r["value_usd"] for r in got["valued"]}


def test_each_action_is_its_own_payoff_under_the_one_measure():
    v = 0.10
    got, vals = _vals(FIFTY, v)
    e = (1 - v) * P * 1.0 + v * 0.50                 # E[held payout] = 0.68
    assert vals["HOLD"] == pytest.approx(Q * e - BASIS)            # 0.60
    # EXIT: net proceeds, nothing held, NO settlement term
    assert vals["DIRECT_EXIT"] == pytest.approx(EXIT_NET - BASIS)  # 0.65
    ex = next(r for r in got["valued"] if r["action"] == "DIRECT_EXIT")
    assert ex["settlement_exposure_after"] is False
    assert ex["value_at_range_low"] == ex["value_at_range_high"]
    # REDUCE: net proceeds + the residual valued on the same measure
    assert vals["REDUCE"] == pytest.approx(
        REDUCE_NET + (Q - REDUCE_QTY) * e - BASIS)                 # 0.76
    assert got["measure"]["void_rate_status"] == CV.VOID_RATE_MEASURED


def test_a_nonzero_void_rate_and_unequal_payouts_change_the_ranking():
    """At v = 0 HOLD beats EXIT. With v = 0.10 and a 50-50 void it no longer
    does, because the held contracts pay 0.50 in the void and 0.70 in
    expectation otherwise. With a basis REFUND (0.62) at the same rate it
    still does. The same numbers, one measure, three orderings of HOLD
    against EXIT -- which a HOLD valued without its void payout could not
    show."""
    _, zero = _vals(FIFTY, 0.0)
    assert zero["HOLD"] > zero["DIRECT_EXIT"]
    _, fifty = _vals(FIFTY, 0.10)
    assert fifty["HOLD"] < fifty["DIRECT_EXIT"]
    _, refund = _vals(REFUND, 0.10)
    assert refund["HOLD"] > refund["DIRECT_EXIT"]
    # WITH ITS UNCERTAINTY [0, 0.10] the HOLD/EXIT choice is not robust:
    # it is a research valuation and may not be dispatched with real money
    got = CV.value_actions(held_cents=FIFTY, p_win=P, void_rate=0.10, qty=Q,
                           basis_usd=BASIS, candidates=_cands()[:2])
    assert got["selection_basis"] == CV.BASIS_RESEARCH
    assert got["funded_dispatch_permitted"] is False
    assert got["funded_dispatch_refusal"] == CV.R_SELECTION_DEPENDS_ON_VOID
    # with the refund payout the same range leaves HOLD on top throughout
    got_r = CV.value_actions(held_cents=REFUND, p_win=P, void_rate=0.10,
                             qty=Q, basis_usd=BASIS, candidates=_cands()[:2])
    assert got_r["selection_basis"] == CV.BASIS_ROBUST
    assert got_r["funded_dispatch_permitted"] is True


def test_an_unknown_void_rate_is_not_zero():
    """No measured rate: every action is valued at both ends of [0, 1]. HOLD
    wins at 0 and loses at 1 (the void pays 50c), so the ranking is a
    conditional research valuation and funded dispatch is refused."""
    got = CV.value_actions(held_cents=FIFTY, p_win=P, void_rate=None, qty=Q,
                           basis_usd=BASIS, candidates=_cands())
    assert got["void_rate_status"] == CV.VOID_RATE_UNMEASURED
    assert got["void_range"] == [0.0, 1.0]
    assert got["selection_basis"] == CV.BASIS_RESEARCH
    assert got["funded_dispatch_permitted"] is False
    hold = next(r for r in got["valued"] if r["action"] == "HOLD")
    assert hold["value_at_range_low"] == pytest.approx(Q * 0.70 - BASIS)
    assert hold["value_at_range_high"] == pytest.approx(Q * 0.50 - BASIS)


def test_a_selection_robust_across_every_void_rate_is_permitted():
    """An exit that beats holding whether or not the fixture voids stands
    without a measured rate: it wins at both ends of [0, 1]."""
    cands = [{"action": "HOLD"},
             {"action": "DIRECT_EXIT", "qty": Q, "net_proceeds_usd": 7.50}]
    got = CV.value_actions(held_cents=FIFTY, p_win=P, void_rate=None, qty=Q,
                           basis_usd=BASIS, candidates=cands)
    assert got["selection_basis"] == CV.BASIS_ROBUST
    assert got["funded_dispatch_permitted"] is True
    assert got["winner"]["action"] == "DIRECT_EXIT"


def test_a_fixture_that_cannot_void_is_valued_exactly():
    got = CV.value_actions(held_cents={"WIN": 100, "LOSE": 0}, p_win=P,
                           void_rate=None, qty=Q, basis_usd=BASIS,
                           candidates=_cands())
    assert got["selection_basis"] == CV.BASIS_EXACT
    assert got["funded_dispatch_permitted"] is True


def test_the_omitted_basis_cancels_across_every_action_and_only_then():
    """Every value subtracts the same remaining basis. Dropping it from ALL
    actions leaves the order unchanged; dropping it from ONE does not --
    which is why it is carried in every value rather than omitted."""
    _, with_b = _vals(FIFTY, 0.10)
    got0 = CV.value_actions(held_cents=FIFTY, p_win=P, void_rate=0.10, qty=Q,
                            basis_usd=0.0, candidates=_cands())
    no_b = {r["action"]: r["value_usd"] for r in got0["valued"]}
    for a in with_b:
        assert no_b[a] - with_b[a] == pytest.approx(BASIS)
    order = sorted(with_b, key=lambda a: -with_b[a])
    assert sorted(no_b, key=lambda a: -no_b[a]) == order
    mixed = dict(with_b, HOLD=no_b["HOLD"])
    assert sorted(mixed, key=lambda a: -mixed[a]) != order


def _refund_leg(leg, cost):
    return dataclasses.replace(
        leg, cost_cents_per_unit=cost,
        settlement_rules={SETTLE.CANCELLED: {
            "established": True, "resolution": SETTLE.RES_REFUND}})


def _acquire(held, hedge, *, v, p=P, q_win=0.3, h=6, fee=0.25, also=()):
    st = IS.classify(held, hedge, sport_permits_tie=False,
                     fixture_can_void=True)
    cls = PS.payout_classes(st)

    def dist(rate):
        got = PS.distribution(cls, primary={"p_win": p, "source": "CHOSEN"},
                              conditional={o: q_win for o in
                                           PS.learned_outcomes(cls)},
                              void={"rate": rate, "n_fixtures": 100,
                                    "upper_95": max(rate, v),
                                    "source": "CHOSEN"})
        assert got["ok"] is True, got
        return got
    d = dist(v)
    by_void = {v: d["probabilities"]}
    for rate in also:
        by_void[rate] = dist(rate)["probabilities"]
    pv = PS.merged_position_value(
        FIP.position_worst_case(held_leg=held, hedge_leg=hedge, hedge_qty=h,
                                sport_permits_tie=False, fee_usd=fee), cls)
    assert pv["ok"] is True, pv
    return {"action": "ACQUIRE_INDIRECT_HEDGE", "candidate_id": "hedge",
            "regions": pv["regions"], "region_probabilities":
                d["probabilities"], "hedge_qty": h,
            "hedge_cost_usd": h * hedge.cost_cents_per_unit / 100.0,
            "fees_usd": fee, "distribution_void_rate": v,
            "region_probabilities_by_void": by_void}, d, pv


@pytest.mark.parametrize("refund", [True, False])
def test_acquire_reconciles_with_hold_under_the_same_measure(refund):
    """Held Bears ML x10 at 0.62 and a Panthers +4.5 hedge x6 at 0.41, v = 0.08,
    with the void either refunding each leg's basis or resolving 50-50. ACQUIRE
    is valued from both legs' actual payouts in the joint distribution; the
    held leg's marginal of that distribution must equal HOLD's measure, and
    ACQUIRE - HOLD is exactly the hedge leg's own expected payout less its
    cost and fees."""
    v = 0.08
    if refund:
        held = _refund_leg(dataclasses.replace(IS.BEARS_MONEYLINE,
                                               quantity=Q), 62)
        hedge = _refund_leg(dataclasses.replace(IS.PANTHERS_PLUS_4_5,
                                                quantity=6), 41)
    else:
        held = dataclasses.replace(IS.BEARS_MONEYLINE, quantity=Q,
                                   cost_cents_per_unit=62)
        hedge = dataclasses.replace(IS.PANTHERS_PLUS_4_5, quantity=6,
                                    cost_cents_per_unit=41)
    hc = CV.held_outcome_cents(held, sport_permits_tie=False,
                               fixture_can_void=True)
    assert hc["ok"] is True, hc
    assert hc["held_cents"]["VOID"] == (62 if refund else 50)
    acq, d, pv = _acquire(held, hedge, v=v)
    got = CV.value_actions(held_cents=hc["held_cents"], p_win=P,
                           void_rate=v, void_lower=v, void_upper=v, qty=Q,
                           basis_usd=BASIS, candidates=_cands() + [acq])
    assert got["ok"] is True, got
    rows = {r["action"]: r for r in got["valued"]}
    a = rows["ACQUIRE_INDIRECT_HEDGE"]
    assert a["rankable"] is True, a
    # BOTH LEGS' ACTUAL PAYOUTS, the hedge's cost and fees, the same basis
    joint = sum(d["probabilities"][r["region"]] * r["payout_usd"]
                for r in pv["regions"])
    assert a["value_usd"] == pytest.approx(
        joint - 6 * 0.41 - 0.25 - BASIS, abs=1e-9)
    # and the baseline inside it IS the HOLD on this list
    assert a["hold_value_same_measure_usd"] == pytest.approx(
        rows["HOLD"]["value_usd"], abs=1e-12)
    hedge_only = sum(d["probabilities"][r["region"]] * 6
                     * r["per_leg_cents"][1] / 100.0
                     for r in pv["regions"]) - 6 * 0.41 - 0.25
    assert a["increment_vs_hold_usd"] == pytest.approx(hedge_only, abs=1e-9)


def test_an_acquisition_priced_on_another_measure_is_refused():
    held = dataclasses.replace(IS.BEARS_MONEYLINE, quantity=Q,
                               cost_cents_per_unit=62)
    hedge = dataclasses.replace(IS.PANTHERS_PLUS_4_5, quantity=6,
                                cost_cents_per_unit=41)
    hc = CV.held_outcome_cents(held, sport_permits_tie=False,
                               fixture_can_void=True)["held_cents"]
    # a different void rate
    acq, _, _ = _acquire(held, hedge, v=0.02)
    got = CV.value_actions(held_cents=hc, p_win=P, void_rate=0.08, qty=Q,
                           basis_usd=BASIS, candidates=[{"action": "HOLD"},
                                                        acq])
    a = [r for r in got["valued"] if r["action"] != "HOLD"][0]
    assert a["refusal"] == CV.R_MEASURES_DO_NOT_RECONCILE
    # a different primary probability
    acq2, _, _ = _acquire(held, hedge, v=0.08, p=0.65)
    got2 = CV.value_actions(held_cents=hc, p_win=P, void_rate=0.08, qty=Q,
                            basis_usd=BASIS, candidates=[{"action": "HOLD"},
                                                         acq2])
    a2 = [r for r in got2["valued"] if r["action"] != "HOLD"][0]
    assert a2["refusal"] == CV.R_MEASURES_DO_NOT_RECONCILE


def test_the_ranking_with_all_four_actions_under_void():
    """One ranking, all four actions, nonzero void, unequal payouts: the
    winner is the highest value under the one measure, and each value is
    recomputable by hand from its components."""
    held = dataclasses.replace(IS.BEARS_MONEYLINE, quantity=Q,
                               cost_cents_per_unit=62)
    hedge = dataclasses.replace(IS.PANTHERS_PLUS_4_5, quantity=6,
                                cost_cents_per_unit=41)
    hc = CV.held_outcome_cents(held, sport_permits_tie=False,
                               fixture_can_void=True)["held_cents"]
    acq, _, _ = _acquire(held, hedge, v=0.10)
    got = CV.value_actions(held_cents=hc, p_win=P, void_rate=0.10,
                           void_lower=0.10, void_upper=0.10, qty=Q,
                           basis_usd=BASIS, candidates=_cands() + [acq])
    for r in got["valued"]:
        assert r["rankable"], r
        assert sum(r["components"].values()) == pytest.approx(
            r["value_usd"], abs=1e-9)
    best = max(got["valued"], key=lambda r: r["value_usd"])
    assert got["winner"]["action"] == best["action"]


def _plain_pair():
    held = dataclasses.replace(IS.BEARS_MONEYLINE, quantity=Q,
                               cost_cents_per_unit=62)
    hedge = dataclasses.replace(IS.PANTHERS_PLUS_4_5, quantity=6,
                                cost_cents_per_unit=41)
    hc = CV.held_outcome_cents(held, sport_permits_tie=False,
                               fixture_can_void=True)["held_cents"]
    return held, hedge, hc


def test_an_acquisition_is_never_admitted_on_an_unmeasured_void_rate():
    held, hedge, hc = _plain_pair()
    acq, _, _ = _acquire(held, hedge, v=0.05)
    got = CV.value_actions(held_cents=hc, p_win=P, void_rate=None, qty=Q,
                           basis_usd=BASIS, candidates=[{"action": "HOLD"},
                                                        acq])
    a = [r for r in got["valued"] if r["action"] != "HOLD"][0]
    assert a["refusal"] == CV.R_ACQ_NEEDS_MEASURED_VOID
    assert a["rankable"] is False


def test_an_acquisition_must_win_at_both_ends_of_the_measured_range():
    """Measured 0.05 with an upper bound of 0.20: the acquisition must be
    priced, and must still win, at 0 and at 0.20 for funded dispatch."""
    held, hedge, hc = _plain_pair()
    acq, _, _ = _acquire(held, hedge, v=0.05, q_win=0.9, also=(0.0, 0.20))
    got = CV.value_actions(held_cents=hc, p_win=P, void_rate=0.05,
                           void_lower=0.0, void_upper=0.20, qty=Q,
                           basis_usd=BASIS, candidates=[{"action": "HOLD"},
                                                        acq])
    a = [r for r in got["valued"] if r["action"] != "HOLD"][0]
    assert a["value_at_range_low"] is not None
    assert a["value_at_range_high"] is not None
    if got["winner"]["action"] == "ACQUIRE_INDIRECT_HEDGE":
        assert got["funded_dispatch_permitted"] == (
            got["winner_at_range_low"] == got["winner_at_range_high"])
    # without the range ends priced it cannot qualify
    acq2, _, _ = _acquire(held, hedge, v=0.05, q_win=0.9)
    got2 = CV.value_actions(held_cents=hc, p_win=P, void_rate=0.05,
                            void_lower=0.0, void_upper=0.20, qty=Q,
                            basis_usd=BASIS, candidates=[{"action": "HOLD"},
                                                         acq2])
    a2 = [r for r in got2["valued"] if r["action"] != "HOLD"][0]
    assert a2["value_at_range_low"] is None
    if got2["winner"]["action"] == "ACQUIRE_INDIRECT_HEDGE":
        assert got2["funded_dispatch_permitted"] is False
