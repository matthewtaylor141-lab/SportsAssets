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
    got = CV.value_actions(held_cents=FIFTY, p_win=P, void_rate=0.10,
                           void_lower=0.0, void_upper=0.10, qty=Q,
                           basis_usd=BASIS, candidates=_cands()[:2])
    assert got["selection_basis"] == CV.BASIS_RESEARCH
    assert got["funded_dispatch_permitted"] is False
    assert got["funded_dispatch_refusal"] == CV.R_SELECTION_DEPENDS_ON_VOID
    # with the refund payout the same range leaves HOLD on top throughout
    got_r = CV.value_actions(held_cents=REFUND, p_win=P, void_rate=0.10,
                             void_lower=0.0, void_upper=0.10,
                             qty=Q, basis_usd=BASIS, candidates=_cands()[:2])
    assert got_r["selection_basis"] == CV.BASIS_ROBUST
    assert got_r["funded_dispatch_permitted"] is True
    # A MEASURED RATE WITH NO STATED UPPER BOUND covers [0, 1], not [0, point]:
    # at v = 1 the refund pays 0.62 and the exit (0.685) wins, so not robust.
    got_n = CV.value_actions(held_cents=REFUND, p_win=P, void_rate=0.10,
                             qty=Q, basis_usd=BASIS, candidates=_cands()[:2])
    assert got_n["void_range"] == [0.0, 1.0]
    assert got_n["funded_dispatch_permitted"] is False


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


# ════════════════════════════════════════════════════════════════════
# THE ENDPOINT-ROBUSTNESS CLAIM: FIXED ACTIONS, THE EXACT MODEL IT COVERS
# ════════════════════════════════════════════════════════════════════

def test_two_reductions_of_different_size_are_different_actions():
    """REDUCE 2 wins at v = 0 and REDUCE 9 at v = 1. Without ids they used to
    share one key, so 'the same action won at both ends' was claimed for two
    different orders. The fixed action now includes its quantity."""
    cands = [{"action": "HOLD"},
             {"action": "REDUCE", "qty": 2, "net_proceeds_usd": 1.46},
             {"action": "REDUCE", "qty": 9, "net_proceeds_usd": 6.20}]
    got = CV.value_actions(held_cents=FIFTY, p_win=P, void_rate=None, qty=Q,
                           basis_usd=BASIS, candidates=cands)
    lo, hi = got["winner_at_range_low"], got["winner_at_range_high"]
    assert lo[0] == hi[0] == "REDUCE"
    assert lo[2] != hi[2]                     # different quantities
    assert got["funded_dispatch_permitted"] is False
    assert got["selection_basis"] == CV.BASIS_RESEARCH


def test_a_candidate_unrankable_at_one_end_voids_the_robustness_claim():
    """An acquisition priced at the point and the upper end but not at 0 cannot
    be compared there, so no winner can be called robust over [0, hi]."""
    held, hedge, hc = _plain_pair()
    acq, _, _ = _acquire(held, hedge, v=0.05, q_win=0.9, also=(0.20,))
    cands = [{"action": "HOLD"},
             {"action": "DIRECT_EXIT", "qty": Q, "net_proceeds_usd": 9.0},
             acq]
    got = CV.value_actions(held_cents=hc, p_win=P, void_rate=0.05,
                           void_lower=0.0, void_upper=0.20, qty=Q,
                           basis_usd=BASIS, candidates=cands)
    assert got["funded_dispatch_permitted"] is False
    assert got["funded_dispatch_refusal"] == CV.R_RANKABLE_SET_CHANGES


def test_tables_that_are_not_a_normal_plus_void_mixture_are_refused():
    """The affine claim holds for an acquisition only if its tables ARE
    (1 - v) N + v V. A table at the upper end that moves regular mass between
    regular regions is not, and the acquisition is refused by name."""
    held, hedge, hc = _plain_pair()
    acq, _, _ = _acquire(held, hedge, v=0.05, q_win=0.9, also=(0.0, 0.20))
    bad = dict(acq["region_probabilities_by_void"][0.20])
    ks = [k for k in bad if not k.startswith("VOID")]
    bad[ks[0]] += 0.05
    bad[ks[1]] -= 0.05
    by = dict(acq["region_probabilities_by_void"])
    by[0.20] = bad
    acq["region_probabilities_by_void"] = by
    got = CV.value_actions(held_cents=hc, p_win=P, void_rate=0.05,
                           void_lower=0.0, void_upper=0.20, qty=Q,
                           basis_usd=BASIS, candidates=[{"action": "HOLD"},
                                                        acq])
    a = [r for r in got["valued"] if r["action"] != "HOLD"][0]
    assert a["refusal"] == CV.R_ACQ_NOT_THE_VOID_MIXTURE
    assert a["rankable"] is False


@pytest.mark.parametrize("seed", range(40))
def test_a_robust_verdict_holds_at_every_rate_in_its_range(seed):
    """THE CLAIM ITSELF, checked by brute force: whenever the verdict is
    ROBUST, re-valuing the SAME fixed candidates at 101 rates across the
    stated range (each as a degenerate measured range) never changes the
    winner. Candidates include an acquisition built as a genuine mixture."""
    import random
    rnd = random.Random(seed)
    held, hedge, hc = _plain_pair()
    measured = seed % 2 == 0
    v_pt = round(rnd.uniform(0.01, 0.15), 4) if measured else None
    hi_m = round(min(1.0, (v_pt or 0) + rnd.uniform(0.02, 0.3)), 4)
    grid_ends = (0.0, hi_m) if measured else (0.0, 1.0)
    pts = [grid_ends[0] + (grid_ends[1] - grid_ends[0]) * i / 100.0
           for i in range(101)]
    cands = [{"action": "HOLD"},
             {"action": "DIRECT_EXIT", "qty": Q,
              "net_proceeds_usd": round(rnd.uniform(3.0, 9.0), 2)},
             {"action": "REDUCE", "qty": rnd.randint(1, 9),
              "net_proceeds_usd": round(rnd.uniform(0.5, 6.0), 2)}]
    acq = None
    if measured:
        acq, _, _ = _acquire(held, hedge, v=v_pt,
                             q_win=round(rnd.uniform(0.2, 0.95), 3),
                             also=tuple([0.0, hi_m] + pts))
        cands.append(acq)
    got = CV.value_actions(held_cents=hc, p_win=P, void_rate=v_pt,
                           void_lower=0.0 if measured else None,
                           void_upper=hi_m if measured else None,
                           qty=Q, basis_usd=BASIS, candidates=cands)
    assert got["ok"] is True
    if got["selection_basis"] != CV.BASIS_ROBUST:
        return
    want = got["winner"]["fixed_action"]
    for v in pts:
        cs = cands
        if acq is not None:
            a2 = dict(acq, distribution_void_rate=v,
                      region_probabilities=acq[
                          "region_probabilities_by_void"][v])
            cs = cands[:-1] + [a2]
        one = CV.value_actions(held_cents=hc, p_win=P, void_rate=v,
                               void_lower=v, void_upper=v, qty=Q,
                               basis_usd=BASIS, candidates=cs)
        assert one["winner"]["fixed_action"] == want, (v, one["winner"])
