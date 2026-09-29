"""THE OWNER'S ACCEPTANCE REQUIREMENTS FOR THE PAYOUT-STATE DISTRIBUTION.

  1. The factorization is complete: P(primary), P(hedge | primary WIN) and
     P(hedge | primary LOSE) give all four joint outcomes, each stated; a cell
     the table cannot produce is STRUCTURAL and names the rows implying it.
     Every probability is finite and nonnegative, and the whole sums to one.
  2. Every component describes the same event -- fixture, side, period,
     overtime, settlement -- or the pricing refuses by name. Probabilities
     conditional on normal settlement are labelled apart from unconditional
     ones, and the void rate enters as (1 - v) x conditional + v, never added
     to an already-normalized distribution. A sparse or one-sided conditioning
     cohort is reported with its count and interval and refused; a learned 0
     or 1 is never read as structural.
  3. Merged regions preserve every compared action's value: HOLD, DIRECT_EXIT,
     REDUCE and ACQUIRE have exactly the same expected value and worst case on
     the merged and unmerged tables, for random quantities (residual, partial
     hedge) and random within-class splits, on three fixtures and a void cell
     whose refund depends on basis.
  4. HOLD and ACQUIRE are compared under one measure: the acquisition's
     increment over HOLD is computed from the same distribution and table.

Every probability and price below is a CHOSEN INPUT for arithmetic, not an
estimate of any fixture.
"""
from __future__ import annotations

import dataclasses
import math
import random

import pytest

from sportsassets import bettor_funded_indirect_pair as FIP
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_indirect_structures as IS
from sportsassets import bettor_payout_states as PS
from sportsassets import bettor_settlement_clauses as SETTLE
from tests import test_indirect_structures as T

LABEL = "CHOSEN INPUTS FOR ARITHMETIC -- NOT ESTIMATES OF ANY FIXTURE"
VOID_IN = {"rate": 0.02, "n_fixtures": 100, "n_void_fixtures": 2,
           "upper_95": 0.07, "source": "CHOSEN_FOR_THIS_TEST"}


def _void_refunds(leg, cost):
    """An established CANCELLED -> REFUND clause, stated for this test: a
    cancelled fixture pays this contract its own cost back (synthetic)."""
    return dataclasses.replace(
        leg, cost_cents_per_unit=cost,
        settlement_rules={SETTLE.CANCELLED: {
            "established": True, "resolution": SETTLE.RES_REFUND}})


def _spread_pair():
    """A -2.5 held with A -6.5 as the hedge: given the primary WINS the hedge
    may win or lose (learned); given it LOSES the hedge loses (structural)."""
    return T.clean(T.spread("A", "-5/2"), T.spread("A", "-13/2"))


# ═════════════════════════════════════════════════════════════════════
# 1 · THE FOUR JOINT OUTCOMES
# ═════════════════════════════════════════════════════════════════════

def test_all_four_joint_outcomes_are_stated_and_sum_with_the_rest_to_one():
    cls = PS.payout_classes(_spread_pair())
    assert cls["ok"] is True, cls
    d = PS.distribution(cls, primary={"p_win": 0.55, "source": LABEL},
                        conditional={"WIN": 0.35}, void=None)
    assert d["ok"] is True, d
    cells = {(c["primary"], c["hedge"]): c for c in d["joint_outcomes"]}
    assert set(cells) == {("WIN", "WIN"), ("WIN", "LOSE"),
                          ("LOSE", "WIN"), ("LOSE", "LOSE")}
    assert cells[("WIN", "WIN")]["probability"] == pytest.approx(0.55 * 0.35)
    assert cells[("WIN", "LOSE")]["probability"] == pytest.approx(0.55 * 0.65)
    assert cells[("WIN", "WIN")]["conditional"] == "LEARNED"
    # GIVEN THE PRIMARY LOSES THE HEDGE LOSES: structural, from the table
    assert cells[("LOSE", "LOSE")]["probability"] == pytest.approx(0.45)
    assert cells[("LOSE", "LOSE")]["conditional"] == "STRUCTURAL"
    impossible = cells[("LOSE", "WIN")]
    assert impossible["probability"] == 0.0
    assert impossible["structurally_impossible"] is True
    assert impossible["implied_by_rows"], impossible
    total = sum(c["probability"] for c in d["joint_outcomes"])
    assert total == pytest.approx(1.0, abs=1e-12)
    for c in d["joint_outcomes"]:
        assert math.isfinite(c["probability"]) and c["probability"] >= 0.0


def _classes_with_two_learned():
    """A table (stated for this test) where the hedge is uncertain given
    EITHER primary outcome -- the case a margin-and-total pair would give --
    so both conditionals are learned and all four cells carry mass."""
    def c(label, pc, hc):
        o = {100: "WIN", 0: "LOSE"}
        return {"label": label, "per_leg_cents": [pc, hc], "void": False,
                "primary": {"outcome": o[pc], "cents": pc},
                "hedge": {"outcome": o[hc], "cents": hc},
                "merged_regions": [label + " region"]}
    return {"ok": True, "classes": [c("PAYOUT[100,100]", 100, 100),
                                    c("PAYOUT[100,0]", 100, 0),
                                    c("PAYOUT[0,100]", 0, 100),
                                    c("PAYOUT[0,0]", 0, 0)],
            "unresolved": [], "by_region": {}}


def test_both_conditionals_learned_give_four_cells_from_the_factorization():
    cls = _classes_with_two_learned()
    assert PS.learned_outcomes(cls) == ["WIN", "LOSE"]
    p, qw, ql = 0.6, 0.3, 0.8
    d = PS.distribution(cls, primary={"p_win": p, "source": LABEL},
                        conditional={"WIN": qw, "LOSE": ql}, void=None)
    assert d["ok"] is True, d
    cells = {(c["primary"], c["hedge"]): c["probability"]
             for c in d["joint_outcomes"]}
    assert cells[("WIN", "WIN")] == pytest.approx(p * qw)
    assert cells[("WIN", "LOSE")] == pytest.approx(p * (1 - qw))
    assert cells[("LOSE", "WIN")] == pytest.approx((1 - p) * ql)
    assert cells[("LOSE", "LOSE")] == pytest.approx((1 - p) * (1 - ql))
    # MISSING EITHER CONDITIONAL REFUSES -- it is never filled in
    for missing in ("WIN", "LOSE"):
        cond = {"WIN": qw, "LOSE": ql}
        cond.pop(missing)
        bad = PS.distribution(cls, primary={"p_win": p}, conditional=cond,
                              void=None)
        assert bad["refusal"] == PS.R_CONDITIONAL_NOT_ESTIMATED
        assert bad["outcome"] == missing


# ═════════════════════════════════════════════════════════════════════
# 2 · CONDITIONAL VS UNCONDITIONAL; THE VOID RATE
# ═════════════════════════════════════════════════════════════════════

def test_the_void_rate_scales_the_conditional_and_takes_its_own_mass():
    held = dataclasses.replace(IS.BEARS_MONEYLINE, cost_cents_per_unit=62)
    hedge = dataclasses.replace(IS.PANTHERS_PLUS_4_5, cost_cents_per_unit=41)
    st = IS.classify(held, hedge, sport_permits_tie=False,
                     fixture_can_void=True, fixture_can_postpone=True)
    cls = PS.payout_classes(st)
    assert cls["ok"] is True, cls
    p, q, v = 0.6, 0.3, VOID_IN["rate"]
    d = PS.distribution(cls, primary={"p_win": p, "source": LABEL},
                        conditional={"WIN": q}, void=VOID_IN)
    assert d["ok"] is True, d
    voids = [c for c in cls["classes"] if c["void"]]
    assert len(voids) == 1
    assert d["probabilities"][voids[0]["label"]] == pytest.approx(v)
    cells = {(c["primary"], c["hedge"]): c["probability"]
             for c in d["joint_outcomes"]}
    assert cells[("WIN", "WIN")] == pytest.approx((1 - v) * p * q)
    assert cells[("WIN", "LOSE")] == pytest.approx((1 - v) * p * (1 - q))
    assert sum(d["probabilities"].values()) == pytest.approx(1.0, abs=1e-12)
    # THE LABELS: the bookmaker's number is conditional on normal settlement;
    # the class probabilities are unconditional
    k = d["probability_kinds"]
    assert k["basis.primary.p_win"] == PS.CONDITIONAL_ON_NORMAL_SETTLEMENT
    assert k["probabilities"] == PS.UNCONDITIONAL
    assert k["basis.conditional.*.p_hedge_wins"] == \
        PS.CONDITIONAL_ON_NORMAL_SETTLEMENT_AND_PRIMARY_OUTCOME
    assert d["basis"]["primary"]["is"] == \
        "P(primary outcome | the fixture is not void)"
    assert d["implied_primary_marginal"] == pytest.approx((1 - v) * p)


# ═════════════════════════════════════════════════════════════════════
# 2 · ONE EVENT UNDER EVERY COMPONENT
# ═════════════════════════════════════════════════════════════════════

GOOD_SOURCE = {"probability_event": "A", "payout_event_held": "A",
               "record_checked": True, "valuation_row_id": 7}


def test_the_same_event_check_records_every_field_when_they_agree():
    got = PS.same_event(_spread_pair(), primary_source=GOOD_SOURCE,
                        overtime_included=True)
    assert got["ok"] is True, got
    assert set(got["agreements"]) == {"fixture", "side", "period",
                                      "overtime", "settlement"}
    assert all(a["agrees"] for a in got["agreements"].values())


@pytest.mark.parametrize("field,mutate", [
    ("side", lambda st, src, ot: (st, dict(src, payout_event_held="B"), ot)),
    ("side", lambda st, src, ot: (st, dict(src, probability_event=None), ot)),
    ("settlement", lambda st, src, ot: (st, dict(src, record_checked=False),
                                        ot)),
    ("overtime", lambda st, src, ot: (st, src, False)),
    ("fixture", lambda st, src, ot: (dict(st, leg_grading=[
        dict(st["leg_grading"][0]),
        dict(st["leg_grading"][1], fixture_id="another-fixture")]), src, ot)),
    ("period", lambda st, src, ot: (dict(st, leg_grading=[
        dict(st["leg_grading"][0]),
        dict(st["leg_grading"][1], period="FIRST_HALF")]), src, ot)),
])
def test_a_component_describing_another_event_is_refused_by_name(field,
                                                                 mutate):
    st = _spread_pair().to_dict()
    st, src, ot = mutate(st, dict(GOOD_SOURCE), True)
    got = PS.same_event(st, primary_source=src, overtime_included=ot)
    assert got["ok"] is False
    assert got["refusal"] == PS.R_COMPONENTS_DESCRIBE_DIFFERENT_EVENTS
    assert field in got["mismatched"], got


# ═════════════════════════════════════════════════════════════════════
# 2 · SPARSE COHORTS REPORT UNCERTAINTY AND REFUSE
# ═════════════════════════════════════════════════════════════════════

def _records(n_fixtures, *, primary_won=True, hedge_wins=None, repeats=1):
    hw = set(range(n_fixtures // 3)) if hedge_wins is None else hedge_wins
    out = []
    for i in range(n_fixtures):
        for _ in range(repeats):
            out.append({"fixture": "fx-%d" % i,
                        "label": 1.0 if i in hw else 0.0,
                        "leg_outcomes": [{"won": primary_won},
                                         {"won": i in hw}]})
    return out


def test_a_thin_cohort_is_refused_with_its_count():
    got = PS.cohort_evidence(_records(10), learned_outcomes=["WIN"],
                             predictions={"WIN": 0.3})
    assert got["refusal"] == PS.R_CONDITIONAL_EVIDENCE_INSUFFICIENT
    assert got["cohorts"]["WIN"]["fixtures"] == 10
    assert got["cohorts"]["WIN"]["min_fixtures"] == \
        PS.MIN_CONDITIONAL_COHORT_FIXTURES


def test_an_unobserved_hedge_outcome_is_not_an_impossible_one():
    got = PS.cohort_evidence(_records(30, hedge_wins=set()),
                             learned_outcomes=["WIN"],
                             predictions={"WIN": 0.02})
    assert got["refusal"] == PS.R_CONDITIONAL_EVIDENCE_INSUFFICIENT
    assert "never shown a hedge win" in got["why"]


def test_repeated_observations_of_one_fixture_count_once():
    got = PS.cohort_evidence(_records(24, repeats=5),
                             learned_outcomes=["WIN"],
                             predictions={"WIN": 0.33})
    assert got["ok"] is True, got
    c = got["cohorts"]["WIN"]
    assert c["fixtures"] == 24 and c["records"] == 120
    assert c["event_weighted_hedge_win_rate"] == pytest.approx(8 / 24)
    lo, hi = c["wilson_95"]
    assert lo < 8 / 24 < hi
    assert c["weighting"] == "ONE_WEIGHT_PER_FIXTURE"


def test_a_learned_certainty_is_not_structural():
    got = PS.cohort_evidence(_records(30), learned_outcomes=["WIN"],
                             predictions={"WIN": 1.0})
    assert got["refusal"] == PS.R_LEARNED_CERTAINTY_IS_NOT_STRUCTURAL


def test_a_cohort_for_the_other_outcome_does_not_stand_in():
    recs = _records(30, primary_won=True)
    got = PS.cohort_evidence(recs, learned_outcomes=["LOSE"],
                             predictions={"LOSE": 0.4})
    assert got["refusal"] == PS.R_CONDITIONAL_EVIDENCE_INSUFFICIENT
    assert got["cohorts"]["LOSE"]["fixtures"] == 0


# ═════════════════════════════════════════════════════════════════════
# 3 · MERGED REGIONS PRESERVE EVERY ACTION'S VALUE
# ═════════════════════════════════════════════════════════════════════

def _fixtures():
    """(held leg, hedge leg, tie, void, conditional) on three fixtures plus the
    void-with-basis case: Bears ML + Panthers +4.5 with a VOID cell refunding
    each leg's basis."""
    bears_h = dataclasses.replace(IS.BEARS_MONEYLINE, cost_cents_per_unit=62)
    bears_g = dataclasses.replace(IS.PANTHERS_PLUS_4_5,
                                  cost_cents_per_unit=41)
    return {
        "bears_panthers_void_refunds_basis": (
            _void_refunds(IS.BEARS_MONEYLINE, 62),
            _void_refunds(IS.PANTHERS_PLUS_4_5, 41), False, True),
        "bears_panthers_void_fifty_fifty": (bears_h, bears_g, False, True),
        "bears_panthers_no_void": (bears_h, bears_g, False, False),
        "a_minus_2_5_b_plus_6_5": (
            dataclasses.replace(T.spread("A", "-5/2"),
                                cost_cents_per_unit=55),
            dataclasses.replace(T.spread("B", "13/2"),
                                cost_cents_per_unit=38), False, False),
        "over_20_5_under_23_5": (
            dataclasses.replace(T.total("OVER", "41/2"),
                                cost_cents_per_unit=48),
            dataclasses.replace(T.total("UNDER", "47/2"),
                                cost_cents_per_unit=52), False, False),
    }


def _split(cls, class_probs, rng):
    out = {}
    for c in cls["classes"]:
        w = [rng.random() + 1e-3 for _ in c["merged_regions"]]
        tot = sum(w)
        for r, wi in zip(c["merged_regions"], w):
            out[r] = class_probs[c["label"]] * wi / tot
    return out


def _action_values(pv, probs, *, q, r, h, exit_px, fee):
    """EV and worst case of HOLD, DIRECT_EXIT, REDUCE(r) and ACQUIRE(h) over
    one position table and one measure. Region-by-region payoffs."""
    basis = pv["held_basis_usd_per_unit"] * q
    rows = [(probs[x["region"]], x) for x in pv["regions"]]

    def ev_wc(f):
        vals = [f(x) for _, x in rows]
        return (sum(p * f(x) for p, x in rows), min(vals))
    held = lambda x: x["per_leg_cents"][0] / 100.0            # noqa: E731
    return {
        "HOLD": ev_wc(lambda x: q * held(x) - basis),
        "DIRECT_EXIT": ev_wc(lambda x: q * exit_px - fee - basis),
        "REDUCE": ev_wc(lambda x: r * exit_px + (q - r) * held(x) - fee
                        - basis),
        "ACQUIRE": ev_wc(lambda x: x["payout_usd"] - pv["cost_usd"] - fee),
    }


@pytest.mark.parametrize("name", sorted(_fixtures()))
def test_every_actions_value_and_worst_case_survive_the_merge(name):
    held0, hedge0, tie, void = _fixtures()[name]
    rng = random.Random(hash(name) & 0xFFFF)
    for trial in range(30):
        q = rng.randint(1, 40)
        h = rng.randint(0, q)                    # partial hedges included
        r = rng.randint(0, q)
        held = dataclasses.replace(held0, quantity=q)
        hedge = dataclasses.replace(hedge0, quantity=max(h, 1))
        st = IS.classify(held, hedge, sport_permits_tie=tie,
                         fixture_can_void=void, fixture_can_postpone=True)
        cls = PS.payout_classes(st)
        assert cls["ok"] is True, (name, cls)
        learned = PS.learned_outcomes(cls)
        d = PS.distribution(
            cls, primary={"p_win": rng.uniform(0.2, 0.8), "source": LABEL},
            conditional={o: rng.uniform(0.05, 0.95) for o in learned},
            void=VOID_IN if void else None)
        assert d["ok"] is True, (name, d)
        pv = FIP.position_worst_case(held_leg=held, hedge_leg=hedge,
                                     hedge_qty=h, sport_permits_tie=tie,
                                     fee_usd=0.25, fixture_can_void=void)
        assert pv["ok"] is True, (name, pv)
        mpv = PS.merged_position_value(pv, cls)
        assert mpv["ok"] is True, (name, mpv)
        kw = dict(q=q, r=r, h=h, exit_px=rng.uniform(0.2, 0.9), fee=0.25)
        merged = _action_values(mpv, d["probabilities"], **kw)
        unmerged = _action_values(pv, _split(cls, d["probabilities"], rng),
                                  **kw)
        for action in ("HOLD", "DIRECT_EXIT", "REDUCE", "ACQUIRE"):
            assert merged[action][0] == pytest.approx(
                unmerged[action][0], abs=1e-9), (name, trial, action)
            assert merged[action][1] == pytest.approx(
                unmerged[action][1], abs=1e-12), (name, trial, action)


def test_a_void_refund_that_depends_on_basis_is_merged_on_its_payout():
    """Two bases give two different VOID payouts; the class keys on the
    payout at THIS basis, so a label alone never merges them."""
    held = _void_refunds(IS.BEARS_MONEYLINE, 62)
    a = _void_refunds(IS.PANTHERS_PLUS_4_5, 41)
    b = _void_refunds(IS.PANTHERS_PLUS_4_5, 33)
    ca = PS.payout_classes(IS.classify(held, a, sport_permits_tie=False))
    cb = PS.payout_classes(IS.classify(held, b, sport_permits_tie=False))
    va = [c for c in ca["classes"] if c["void"]]
    vb = [c for c in cb["classes"] if c["void"]]
    assert len(va) == len(vb) == 1
    assert va[0]["per_leg_cents"] == [62, 41]
    assert vb[0]["per_leg_cents"] == [62, 33]
    assert va[0]["label"] != vb[0]["label"]


# ═════════════════════════════════════════════════════════════════════
# 4 · HOLD AND ACQUIRE UNDER ONE MEASURE
# ═════════════════════════════════════════════════════════════════════

def test_the_increment_over_hold_is_the_hedge_legs_own_value_under_one_measure():
    held0, hedge0, tie, void = _fixtures()["bears_panthers_void_refunds_basis"]
    held = dataclasses.replace(held0, quantity=10)
    hedge = dataclasses.replace(hedge0, quantity=6)
    st = IS.classify(held, hedge, sport_permits_tie=tie,
                     fixture_can_void=void)
    cls = PS.payout_classes(st)
    d = PS.distribution(cls, primary={"p_win": 0.58, "source": LABEL},
                        conditional={o: 0.4 for o in
                                     PS.learned_outcomes(cls)},
                        void=VOID_IN)
    pv = PS.merged_position_value(
        FIP.position_worst_case(held_leg=held, hedge_leg=hedge, hedge_qty=6,
                                sport_permits_tie=tie, fee_usd=0.25,
                                fixture_can_void=void), cls)
    probs = d["probabilities"]
    hold_same = PC._hold_value_under(pv, probs)
    acquire = sum(probs[x["region"]] * x["payout_usd"]
                  for x in pv["regions"]) - pv["cost_usd"] - 0.25
    hedge_only = sum(probs[x["region"]] * 6 * x["per_leg_cents"][1] / 100.0
                     for x in pv["regions"]) - 6 * 0.41 - 0.25
    assert acquire - hold_same == pytest.approx(hedge_only, abs=1e-9)
    # AND HOLD UNDER THE MEASURE INCLUDES THE VOID REFUND of the basis
    v = VOID_IN["rate"]
    p_primary_pays = sum(probs[x["region"]] for x in pv["regions"]
                         if x["per_leg_cents"][0] == 100)
    assert hold_same == pytest.approx(
        10 * (p_primary_pays + v * 0.62) - 10 * 0.62, abs=1e-9)
    # A MISSING PROBABILITY IS NOT A ZERO
    assert PC._hold_value_under(pv, {}) is None
