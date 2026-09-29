"""THE PAYOUT-STATE DISTRIBUTION, PURE: classes, merged tables, the measure.

What is pinned here, all without a database:

  * payout classes merge ONLY payout-identical rows, on the classifier's own
    fixtures (Bears ML + Panthers +4.5: 11 rows -> 5 classes + postponed;
    A-2.5 + B+6.5; O20.5 + U23.5), with each leg's semantic outcome named;
  * the merged tables price EXACTLY what the originals price: the same
    expected value under ANY within-class split, and the same worst case --
    through `position_worst_case` and `indirect_candidate(position_value=...)`
    and through the structure-only path;
  * the distribution is (1 - void) x P(primary outcome) x P(class | outcome),
    sums to one, uses structural 1s where the table admits one hedge payout,
    and refuses every input it does not have -- by name, never by a fill;
  * push-capable structures are refused rather than folded into a loss, and
    integer-total structures are refused rather than priced on a partition
    that misgrades their push.

Every probability below is a CHOSEN INPUT for arithmetic, not an estimate of
any fixture.
"""
from __future__ import annotations

import dataclasses
import random
from fractions import Fraction

import pytest

from sportsassets import bettor_funded_decision as FD
from sportsassets import bettor_funded_indirect_pair as FIP
from sportsassets import bettor_funded_model as FMD
from sportsassets import bettor_indirect_structures as IS
from sportsassets import bettor_pair_observations as PO
from sportsassets import bettor_payout_states as PS
from sportsassets import bettor_settlement_clauses as SETTLE
from tests import test_indirect_structures as T

LABEL = "CHOSEN INPUTS FOR ARITHMETIC -- NOT ESTIMATES OF ANY FIXTURE"


def _bears(*, tie=True, void=True, postpone=True, q_a=1, q_b=1,
           cost_a=None, cost_b=None):
    a = dataclasses.replace(IS.BEARS_MONEYLINE, quantity=q_a,
                            cost_cents_per_unit=cost_a)
    b = dataclasses.replace(IS.PANTHERS_PLUS_4_5, quantity=q_b,
                            cost_cents_per_unit=cost_b)
    return a, b, IS.classify(a, b, sport_permits_tie=tie,
                             fixture_can_void=void,
                             fixture_can_postpone=postpone)


def _push_refund(leg, cost):
    """An established REFUND push clause, stated for this test (synthetic)."""
    return dataclasses.replace(
        leg, cost_cents_per_unit=cost,
        settlement_rules={SETTLE.PUSH: {"established": True,
                                        "resolution": SETTLE.RES_REFUND}})


PRIMARY = {"p_win": 0.60, "p_partial": 0.02, "source": "CHOSEN_FOR_THIS_TEST",
           "basis": LABEL}
VOID_IN = {"rate": 0.01, "n_fixtures": 50, "n_void_fixtures": 1,
           "upper_95": 0.05, "source": "CHOSEN_FOR_THIS_TEST"}


# ═════════════════════════════════════════════════════════════════════
# 1 · PAYOUT CLASSES
# ═════════════════════════════════════════════════════════════════════

def test_bears_panthers_eleven_rows_are_five_payout_classes_and_postponed():
    _, _, st = _bears()
    assert st.taxonomy == IS.MIDDLE and len(st.table) == 11
    got = PS.payout_classes(st)
    assert got["ok"] is True, got
    by = {c["label"]: c for c in got["classes"]}
    assert set(by) == {"PAYOUT[0,100]", "PAYOUT[100,100]", "PAYOUT[100,0]",
                       "PAYOUT[50,100]", "VOID[50,50]"}
    assert by["PAYOUT[0,100]"]["merged_regions"] == [
        "margin in (-inf, -5)", "margin = -5", "margin = -4",
        "margin in (-4, 0)"]
    assert by["PAYOUT[100,100]"]["merged_regions"] == ["margin in (0, 4)",
                                                       "margin = 4"]
    assert by["PAYOUT[100,0]"]["merged_regions"] == ["margin = 5",
                                                     "margin > 5"]
    # EACH LEG'S SEMANTIC OUTCOME, cents kept
    assert by["PAYOUT[0,100]"]["primary"] == {"outcome": PS.LOSE, "cents": 0}
    assert by["PAYOUT[0,100]"]["hedge"] == {"outcome": PS.WIN, "cents": 100}
    assert by["PAYOUT[50,100]"]["primary"] == {"outcome": PS.PARTIAL,
                                               "cents": 50}
    assert by["PAYOUT[50,100]"]["states"] == [IS.STATE_TIE]
    assert by["VOID[50,50]"]["void"] is True
    assert by["VOID[50,50]"]["primary"]["outcome"] == PS.VOID
    # POSTPONED IS NOT A CLASS: unresolved, zero terminal mass, basis stated
    assert [u["region"] for u in got["unresolved"]] == [
        "fixture postponed; market stays open"]
    assert got["unresolved"][0]["probability"] == 0.0
    assert "market open" in got["unresolved"][0]["basis"]
    # every terminal region belongs to exactly one class
    assert set(got["by_region"]) == {r["region"] for r in st.table
                                     if r["state"] != IS.STATE_POSTPONED}


def test_the_primary_is_index_zero_as_discover_builds_it():
    """`discover` classifies `classify(held_leg, candidate)`, so index 0 of
    `per_leg_cents` is the HELD leg. Pinned against the pair cycle itself."""
    from sportsassets import bettor_funded_pair_cycle as PC
    a, b, _ = _bears(tie=False, postpone=False)
    held = dataclasses.replace(a, condition_id="held#ORDER_INTENT_BUY_LONG")
    cand = dataclasses.replace(b, condition_id="hedge#ORDER_INTENT_BUY_LONG")
    got = PC.discover(held_leg=held, candidate_legs=[cand],
                      sport_permits_tie=False, fixture_can_postpone=False)
    st = got["admitted"][0]["structure"]
    assert st["legs"][0] == held.condition_id
    assert st["leg_grading"][0]["kind"] == IS.KIND_MONEYLINE
    cls = PS.payout_classes(st)
    # the moneyline (held) leg pays 0 when the Bears lose
    lose = [c for c in cls["classes"] if c["primary"]["outcome"] == PS.LOSE]
    assert [c["per_leg_cents"] for c in lose] == [[0, 100]]


def test_a_minus_2_5_plus_b_plus_6_5_has_three_classes():
    st = T.clean(T.spread("A", "-5/2"), T.spread("B", "-13/2"))
    got = PS.payout_classes(st)
    assert got["ok"] is True, got
    assert len(st.table) == 15
    assert [c["per_leg_cents"] for c in got["classes"]] == [
        [0, 100], [100, 100], [100, 0]]
    assert got["unresolved"] == []


def test_over_20_5_plus_under_23_5_has_three_classes():
    st = T.clean(T.total("OVER", "41/2"), T.total("UNDER", "47/2"))
    got = PS.payout_classes(st)
    assert got["ok"] is True, got
    assert len(st.table) == 3
    assert sorted(tuple(c["per_leg_cents"]) for c in got["classes"]) == [
        (0, 100), (100, 0), (100, 100)]


def test_an_unestablishable_or_empty_structure_is_refused():
    st = IS.classify(IS.BEARS_MONEYLINE,
                     dataclasses.replace(IS.PANTHERS_PLUS_4_5,
                                         overtime=IS.OT_UNKNOWN),
                     sport_permits_tie=True)
    assert PS.payout_classes(st)["refusal"] == \
        PS.R_STRUCTURE_IS_UNESTABLISHABLE
    assert PS.payout_classes({"taxonomy": "MIDDLE", "table": []})[
        "refusal"] == PS.R_NO_TABLE
    assert PS.payout_classes(None)["refusal"] == PS.R_NO_TABLE


def test_an_undetermined_terminal_row_is_refused_not_dropped():
    table = [{"region": "a", "state": "REGULAR", "per_leg_cents": [100, 0],
              "joint_cents": 100, "determined": True},
             {"region": "b", "state": "REGULAR", "per_leg_cents": [None, 0],
              "joint_cents": None, "determined": False}]
    got = PS.payout_classes({"taxonomy": "MIDDLE", "table": table})
    assert got["ok"] is False and got["refusal"] == PS.R_ROW_UNDETERMINED
    three = [{"region": "a", "state": "REGULAR",
              "per_leg_cents": [100, 0, 0], "joint_cents": 100,
              "determined": True}]
    assert PS.payout_classes({"table": three})["refusal"] == PS.R_NOT_TWO_LEGS


# ═════════════════════════════════════════════════════════════════════
# 2 · THE DEFECTS REFUSED BY NAME
# ═════════════════════════════════════════════════════════════════════

def test_integer_total_structures_are_refused_rather_than_priced_wrong():
    """THE REGRESSION: OVER 20 + UNDER 23 with an established refund push.
    The total partition breaks only at floor(line), so totals 20 and 23 --
    the pushes -- are folded into neighbouring cells. Measured here, then
    refused at every place a table would be priced from it."""
    over = _push_refund(T.total("OVER", "20"), 55)
    under = _push_refund(T.total("UNDER", "23"), 40)
    table = IS.payoff_table((over, under), sport_permits_tie=False,
                            fixture_can_void=False,
                            fixture_can_postpone=False)
    cells = {r["region"]: r["per_leg_cents"] for r in table}
    # THE DEFECT ITSELF: no cell for total = 20 or total = 23
    assert cells["total in [0, 20]"] == [0, 100]
    assert cells["total in [21, 23]"] == [100, 100]
    assert not any(v[0] == 55 or v[1] == 40 for v in cells.values()), cells
    # 1 · the classifier refuses it by name
    st = T.clean(over, under)
    assert st.taxonomy == IS.UNESTABLISHABLE
    assert any(IS.R_INTEGER_TOTAL_PUSH_NOT_ISOLATED in g
               for g in st.missing_facts), st.missing_facts
    # 2 · payout classes refuse the table even when handed it directly
    raw = {"taxonomy": "MIDDLE", "table": table,
           "leg_grading": IS.leg_grading((over, under))}
    got = PS.payout_classes(raw)
    assert got["refusal"] == PS.R_INTEGER_TOTAL_PUSH_NOT_ISOLATED, got
    # 3 · and a TOTAL table that does not record its lines cannot be shown
    #     free of the defect, so it is refused too
    got = PS.payout_classes({"taxonomy": "MIDDLE", "table": table})
    assert got["refusal"] == PS.R_TOTAL_LINES_NOT_RECORDED, got
    # 4 · the whole-position floor refuses it as well
    pv = FIP.position_worst_case(
        held_leg=dataclasses.replace(over, quantity=10),
        hedge_leg=under, hedge_qty=10, sport_permits_tie=False,
        fee_usd=0.1, fixture_can_void=False, fixture_can_postpone=False)
    assert pv["ok"] is False
    assert any(IS.R_INTEGER_TOTAL_PUSH_NOT_ISOLATED in g
               for g in pv["missing_facts"]), pv
    # ...while a half-integer total is untouched
    ok = T.clean(T.total("OVER", "41/2"), T.total("UNDER", "47/2"))
    assert ok.taxonomy == IS.MIDDLE and PS.payout_classes(ok)["ok"]


def test_push_capable_structures_are_refused_not_folded_into_a_loss():
    """A-3 + B+6 with established refund pushes: the push cells are isolated
    and PRICED by the table -- (55,100) at margin 3, (100,40) at margin 6 --
    and the distribution refuses both rather than approximate them."""
    a = _push_refund(T.spread("A", "-3"), 55)
    b = _push_refund(T.spread("B", "-6"), 40)
    st = T.clean(a, b)
    assert st.taxonomy != IS.UNESTABLISHABLE, st.missing_facts
    cls = PS.payout_classes(st)
    assert cls["ok"] is True, cls
    pairs = {tuple(c["per_leg_cents"]) for c in cls["classes"]}
    assert (55, 100) in pairs and (100, 40) in pairs
    # the primary's push is a PARTIAL outcome and has no stated probability
    got = PS.distribution(cls, primary={"p_win": 0.5}, conditional={"WIN": .5},
                          void=None)
    assert got["refusal"] == PS.R_PRIMARY_PARTIAL_OUTCOME_NOT_PRICED, got
    # stated -> the hedge's push among three hedge payouts is not binary
    got = PS.distribution(cls, primary={"p_win": 0.5, "p_partial": 0.05},
                          conditional={"WIN": .5, "LOSE": .5}, void=None)
    assert got["refusal"] == PS.R_HEDGE_OUTCOME_NOT_BINARY, got
    assert got["outcome"] == PS.WIN
    assert got["hedge_payouts"] == [0, 40, 100]


# ═════════════════════════════════════════════════════════════════════
# 3 · THE MERGED TABLES PRICE EXACTLY WHAT THE ORIGINALS PRICE
# ═════════════════════════════════════════════════════════════════════

def test_the_merged_structure_has_one_row_per_class_plus_postponed():
    _, _, st = _bears(cost_a=62, cost_b=41)
    cls = PS.payout_classes(st)
    m = PS.merged_structure(st, cls)
    labels = [r["region"] for r in m["table"]]
    assert labels[:5] == [c["label"] for c in cls["classes"]]
    assert labels[5] == "fixture postponed; market stays open"
    assert len(m["table"]) == 6
    assert m["both_win_regions"] == ("PAYOUT[100,100]",)
    assert m["both_lose_regions"] == ()
    assert (m["min_payout_cents"], m["max_payout_cents"]) == (
        st.min_payout_cents, st.max_payout_cents)
    assert m["merge_rule"] == PS.MERGE_RULE
    assert m["merged_from_rows"] == 10
    row = next(r for r in m["table"] if r["region"] == "PAYOUT[0,100]")
    assert row["merged_regions"][0] == "margin in (-inf, -5)"
    assert row["determined"] is True and row["joint_cents"] == 100


def _class_probs():
    _, _, st = _bears(cost_a=62, cost_b=41)
    cls = PS.payout_classes(st)
    d = PS.distribution(cls, primary=PRIMARY, conditional={"WIN": 0.3},
                        void=VOID_IN)
    assert d["ok"] is True, d
    return cls, d


def _split(cls, class_probs, rng):
    """ANY within-class split: random weights over each class's regions."""
    out = {}
    for c in cls["classes"]:
        w = [rng.random() + 1e-3 for _ in c["merged_regions"]]
        tot = sum(w)
        for r, wi in zip(c["merged_regions"], w):
            out[r] = class_probs[c["label"]] * wi / tot
    for u in cls["unresolved"]:
        out[u["region"]] = 0.0
    return out


@pytest.mark.parametrize("hedge_qty", [10, 6])
def test_ev_and_worst_case_are_invariant_on_the_merged_position_table(
        hedge_qty):
    """THROUGH `position_worst_case` AND `indirect_candidate(position_value)`,
    the production pricing path: the same expected value under any
    within-class split, and the same floor."""
    held, hedge, st = _bears(q_a=10, q_b=10, cost_a=62, cost_b=41)
    cls, d = _class_probs()
    pv = FIP.position_worst_case(held_leg=held, hedge_leg=hedge,
                                 hedge_qty=hedge_qty, sport_permits_tie=True,
                                 fee_usd=0.30)
    assert pv["ok"] is True, pv
    mpv = PS.merged_position_value(pv, cls)
    assert mpv["ok"] is True, mpv
    assert len(mpv["regions"]) == 5 and mpv["regions_before_merge"] == 10
    # THE FLOOR: a minimum over identical payouts cannot move
    assert min(r["net_usd"] for r in mpv["regions"]) == \
        pv["gross_worst_case_usd"]
    assert mpv["whole_position_usd"] == pv["whole_position_usd"]
    assert mpv["binding_class"] == cls["by_region"][pv["binding_region"]]
    kw = dict(evidence_quality=FD.EVIDENCE_EXTERNAL_LABELLED, fee_usd=0.30,
              depth=FIP.depth_supports(wanted_qty=hedge_qty,
                                       depth_qty_at_price=500),
              incremental=FIP.incremental_capital_usd(
                  hedge_qty=hedge_qty, hedge_price=0.41, hedge_fee_usd=0.30))
    probs = dict(d["probabilities"], **d["unresolved_probabilities"])
    merged = FD.indirect_candidate(structure=PS.merged_structure(st, cls),
                                   region_probabilities=probs,
                                   position_value=mpv, **kw)
    assert merged["rankable"] is True, merged
    rng = random.Random(20260929)
    for _ in range(25):
        orig = FD.indirect_candidate(structure=st,
                                     region_probabilities=_split(
                                         cls, d["probabilities"], rng),
                                     position_value=pv, **kw)
        assert orig["rankable"] is True, orig
        assert orig["expected_net_usd"] == pytest.approx(
            merged["expected_net_usd"], abs=1e-6)
        assert orig["downside_usd"] == merged["downside_usd"]
        assert orig["cost_usd"] == merged["cost_usd"]


def test_ev_is_invariant_on_the_merged_structure_table_too():
    """The structure-only path (no position value), on a table without the
    postponed row, which that path cannot price at any split."""
    _, _, st = _bears(postpone=False, q_a=10, q_b=10, cost_a=62, cost_b=41)
    cls = PS.payout_classes(st)
    d = PS.distribution(cls, primary=PRIMARY, conditional={"WIN": 0.3},
                        void=VOID_IN)
    wc = FIP.net_worst_case(st, fee_usd=0.30, fee_basis="CHOSEN")
    kw = dict(evidence_quality=FD.EVIDENCE_EXTERNAL_LABELLED, fee_usd=0.30,
              depth=FIP.depth_supports(wanted_qty=10, depth_qty_at_price=50),
              incremental=FIP.incremental_capital_usd(
                  hedge_qty=10, hedge_price=0.41, hedge_fee_usd=0.30),
              worst_case=wc)
    merged = FD.indirect_candidate(structure=PS.merged_structure(st, cls),
                                   region_probabilities=d["probabilities"],
                                   **kw)
    assert merged["rankable"] is True, merged
    rng = random.Random(7)
    for _ in range(25):
        orig = FD.indirect_candidate(
            structure=st, region_probabilities=_split(
                cls, d["probabilities"], rng), **kw)
        assert orig["expected_net_usd"] == pytest.approx(
            merged["expected_net_usd"], abs=1e-6)
        assert orig["downside_usd"] == merged["downside_usd"]


def test_a_position_table_that_does_not_match_the_classes_is_refused():
    held, hedge, st = _bears(q_a=10, q_b=10, cost_a=62, cost_b=41)
    cls = PS.payout_classes(st)
    pv = FIP.position_worst_case(held_leg=held, hedge_leg=hedge, hedge_qty=10,
                                 sport_permits_tie=True, fee_usd=0.30)
    bad = dict(pv, regions=[dict(r, per_leg_cents=[1, 2])
                            if i == 0 else r
                            for i, r in enumerate(pv["regions"])])
    got = PS.merged_position_value(bad, cls)
    assert got["ok"] is False
    assert got["refusal"] == PS.R_POSITION_TABLE_DOES_NOT_MATCH
    short = dict(pv, regions=[r for r in pv["regions"]
                              if r["state"] != IS.STATE_VOID])
    got = PS.merged_position_value(short, cls)
    assert got["refusal"] == PS.R_POSITION_TABLE_DOES_NOT_MATCH
    assert got["classes_not_in_the_position"] == ["VOID[50,50]"]


# ═════════════════════════════════════════════════════════════════════
# 4 · THE DISTRIBUTION
# ═════════════════════════════════════════════════════════════════════

def test_the_distribution_sums_to_one_with_structural_ones_and_one_learned():
    cls, d = _class_probs()
    p = d["probabilities"]
    v, pw, pp = VOID_IN["rate"], PRIMARY["p_win"], PRIMARY["p_partial"]
    assert sum(p.values()) == pytest.approx(1.0, abs=1e-12)
    # STRUCTURAL: the table admits one hedge payout given LOSE and PARTIAL
    assert p["PAYOUT[0,100]"] == pytest.approx((1 - v) * (1 - pw - pp))
    assert p["PAYOUT[50,100]"] == pytest.approx((1 - v) * pp)
    assert d["identified"]["PAYOUT[0,100]"] == PS.IDENTIFIED_STRUCTURAL
    assert d["basis"]["conditional"][PS.LOSE]["classes"] == {
        "PAYOUT[0,100]": 1.0}
    assert d["basis"]["conditional"][PS.LOSE]["basis"] == PS.STRUCTURAL_BASIS
    # LEARNED: exactly two hedge payouts {0, 100} given WIN
    assert p["PAYOUT[100,100]"] == pytest.approx((1 - v) * pw * 0.3)
    assert p["PAYOUT[100,0]"] == pytest.approx((1 - v) * pw * 0.7)
    assert d["identified"]["PAYOUT[100,100]"] == PS.IDENTIFIED_LEARNED
    # MEASURED VOID
    assert p["VOID[50,50]"] == v
    assert d["identified"]["VOID[50,50]"] == PS.IDENTIFIED_VOID
    # THE IMPLIED PRIMARY MARGINAL IS (1 - void) x p_win, recorded
    assert d["implied_primary_marginal"] == pytest.approx((1 - v) * pw,
                                                          abs=1e-12)
    # postponed at zero, and nothing keyed by a raw region
    assert d["unresolved_probabilities"] == {
        "fixture postponed; market stays open": 0.0}
    assert set(p) == {c["label"] for c in cls["classes"]}
    assert d["indistinguishable"]["PAYOUT[100,0]"] == ["margin = 5",
                                                       "margin > 5"]


@pytest.mark.parametrize("legs", [
    (T.spread("A", "-5/2"), T.spread("B", "-13/2")),
    (T.total("OVER", "41/2"), T.total("UNDER", "47/2")),
])
def test_the_other_fixtures_are_priced_with_no_void_class(legs):
    st = T.clean(*legs)
    cls = PS.payout_classes(st)
    d = PS.distribution(cls, primary={"p_win": 0.55}, conditional={"WIN": .4},
                        void=None)
    assert d["ok"] is True, d
    assert sum(d["probabilities"].values()) == pytest.approx(1.0)
    assert d["implied_primary_marginal"] == pytest.approx(0.55)
    assert d["basis"]["void"] == {"used": False}


def test_each_missing_input_is_refused_by_name_and_never_filled():
    _, _, st = _bears()
    cls = PS.payout_classes(st)
    # the tie cell's primary probability is not stated
    got = PS.distribution(cls, primary={"p_win": .6},
                          conditional={"WIN": .3}, void=VOID_IN)
    assert got["refusal"] == PS.R_PRIMARY_PARTIAL_OUTCOME_NOT_PRICED
    # the void class exists and no rate was measured
    got = PS.distribution(cls, primary=PRIMARY, conditional={"WIN": .3},
                          void=None)
    assert got["refusal"] == PS.R_VOID_RATE_NOT_MEASURED
    # the binary conditional is not estimated -- and names the outcome
    got = PS.distribution(cls, primary=PRIMARY, conditional={}, void=VOID_IN)
    assert got["refusal"] == PS.R_CONDITIONAL_NOT_ESTIMATED
    assert got["outcome"] == PS.WIN
    assert "probabilities" not in got, "no fill on a refusal"
    got = PS.distribution(cls, primary=PRIMARY, conditional={"WIN": 1.5},
                          void=VOID_IN)
    assert got["refusal"] == PS.R_CONDITIONAL_INVALID
    # the primary probability is not a probability
    for bad in (1.2, -0.1, True, None, float("nan"), "x"):
        got = PS.distribution(cls, primary=dict(PRIMARY, p_win=bad),
                              conditional={"WIN": .3}, void=VOID_IN)
        assert got["refusal"] == PS.R_PRIMARY_PROBABILITY_INVALID, bad
    got = PS.distribution(cls, primary=dict(PRIMARY, p_win=.9, p_partial=.2),
                          conditional={"WIN": .3}, void=VOID_IN)
    assert got["refusal"] == PS.R_PRIMARY_PROBABILITY_INVALID
    # a partial probability for a table with no partial outcome
    st2 = T.clean(T.spread("A", "-5/2"), T.spread("B", "-13/2"))
    got = PS.distribution(PS.payout_classes(st2),
                          primary={"p_win": .5, "p_partial": .1},
                          conditional={"WIN": .3}, void=None)
    assert got["refusal"] == PS.R_PRIMARY_OUTCOME_NOT_IN_TABLE
    # a refused class set is refused, not priced
    got = PS.distribution({"ok": False, "refusal": PS.R_NO_TABLE},
                          primary=PRIMARY, conditional={}, void=None)
    assert got["ok"] is False and got["refusal"] == PS.R_NO_TABLE


def test_a_primary_outcome_the_table_cannot_produce_is_refused():
    """P(primary loses) > 0 against a table in which the primary never loses
    means the probability and the table describe different contracts."""
    table = [{"region": "w1", "state": "REGULAR", "per_leg_cents": [100, 100],
              "joint_cents": 200, "determined": True},
             {"region": "w2", "state": "REGULAR", "per_leg_cents": [100, 0],
              "joint_cents": 100, "determined": True}]
    cls = PS.payout_classes({"taxonomy": "MIDDLE", "table": table})
    got = PS.distribution(cls, primary={"p_win": 0.7},
                          conditional={"WIN": .5}, void=None)
    assert got["refusal"] == PS.R_PRIMARY_OUTCOME_NOT_IN_TABLE
    assert got["outcome"] == PS.LOSE
    ok = PS.distribution(cls, primary={"p_win": 1.0},
                         conditional={"WIN": .5}, void=None)
    assert ok["ok"] is True, ok


def test_a_measured_rate_is_recorded_not_applied_when_the_table_has_no_void():
    st = T.clean(T.spread("A", "-5/2"), T.spread("B", "-13/2"))
    d = PS.distribution(PS.payout_classes(st), primary={"p_win": .5},
                        conditional={"WIN": .3}, void=VOID_IN)
    assert d["ok"] is True
    assert d["basis"]["void"]["used"] is False
    assert d["basis"]["void"]["rate_supplied"] == VOID_IN["rate"]
    assert sum(d["probabilities"].values()) == pytest.approx(1.0)


# ═════════════════════════════════════════════════════════════════════
# 5 · THE CONDITIONAL'S RECORDS, AS A PURE RULE
# ═════════════════════════════════════════════════════════════════════

def _obs(st, *, pw, hw, pp=None, hp=None):
    return {"structure": (st if isinstance(st, dict) else st.to_dict()),
            "features": {"cost_cents": 103.0}, "primary_won": pw,
            "hedge_won": hw,
            "primary_settlement_price": (1.0 if pw else 0.0) if pp is None
            else pp,
            "hedge_settlement_price": (1.0 if hw else 0.0) if hp is None
            else hp}


def test_conditional_rows_are_read_against_the_observations_own_table():
    _, _, st = _bears()
    win = PO.conditional_row(_obs(st, pw=True, hw=False))
    assert win["include"] is True and win["label"] == 0.0
    assert win["primary_outcome"] == PS.WIN
    assert win["features"]["primary_won"] == 1.0
    # given LOSE the middle's table admits one hedge payout: structural
    lose = PO.conditional_row(_obs(st, pw=False, hw=True))
    assert lose == {"include": False, "exclusion": PO.X_C_STRUCTURAL,
                    "primary_outcome": PS.LOSE}
    # a pushed primary is excluded, never read as a loss
    push = PO.conditional_row(_obs(st, pw=False, hw=True, pp=0.5))
    assert push["exclusion"] == PO.X_C_PRIMARY_PARTIAL
    # a hedge push where the table admits only win or lose is counted
    hpush = PO.conditional_row(_obs(st, pw=True, hw=False, hp=0.5))
    assert hpush["exclusion"] == PO.X_C_HEDGE_PUSHED
    # a synthetic structure with no table is excluded, by its refusal
    none = PO.conditional_row(_obs({"table": []}, pw=True, hw=True))
    assert none["exclusion"] == "%s:%s" % (PO.X_C_CLASSES_REFUSED,
                                           PS.R_NO_TABLE)
    # given LOSE, spread A-2.5 + moneyline B admits two hedge payouts
    st2 = T.clean(T.spread("A", "-5/2"), T.ml("B"))
    got = PO.conditional_row(_obs(st2, pw=False, hw=True))
    assert got["include"] is True and got["primary_outcome"] == PS.LOSE
    assert got["features"]["primary_won"] == 0.0
    got = PO.conditional_row(_obs(st2, pw=True, hw=False))
    assert got["exclusion"] == PO.X_C_STRUCTURAL


# ═════════════════════════════════════════════════════════════════════
# 6 · THE KEY, ITS FEATURES, AND THE CORRECTED CLAIMS
# ═════════════════════════════════════════════════════════════════════

def test_the_conditional_key_has_its_own_features_and_schema():
    assert FMD.KEY_HEDGE_GIVEN_PRIMARY == "funded_pair_hedge_given_primary"
    assert FMD.TARGET_HEDGE_GIVEN_PRIMARY == "HEDGE_WON_GIVEN_PRIMARY_OUTCOME"
    assert FMD.features_for(FMD.KEY_HEDGE_GIVEN_PRIMARY) == \
        FMD.FEATURES + ("primary_won",)
    assert FMD.features_for(FMD.KEY_MIDDLE) == FMD.FEATURES
    assert FMD.feature_schema_sha_for(FMD.KEY_HEDGE_GIVEN_PRIMARY) != \
        FMD.FEATURE_SCHEMA_SHA
    _, _, st = _bears(cost_a=62, cost_b=41)
    base = FMD.features_of(st, primary_cost_cents=62, hedge_cost_cents=41,
                           overtime_included=True)
    w = FMD.conditional_features_of(st, primary_cost_cents=62,
                                    hedge_cost_cents=41,
                                    overtime_included=True, primary_won=True)
    lo = FMD.conditional_features_of(st, primary_cost_cents=62,
                                     hedge_cost_cents=41,
                                     overtime_included=True,
                                     primary_won=False)
    assert len({FMD.feature_sha(base), FMD.feature_sha(w),
                FMD.feature_sha(lo)}) == 3
    assert PO.OBSERVATION_MODEL_KEYS == (FMD.KEY_MIDDLE,
                                         FMD.KEY_HEDGE_GIVEN_PRIMARY)


def test_a_fit_on_the_conditional_list_is_scored_on_it():
    rows = [dict({f: 1.0 for f in FMD.FEATURES}, primary_won=float(i % 2))
            for i in range(60)]
    labels = [1.0 if i % 2 == 0 else (1.0 if i % 5 == 0 else 0.0)
              for i in range(60)]
    got = FMD.fit(rows, labels, decided_at=list(range(60)),
                  features=FMD.features_for(FMD.KEY_HEDGE_GIVEN_PRIMARY))
    assert got["ok"] and got["features"][-1] == "primary_won"
    obj = FMD.load(got["params"])
    assert obj.predict(dict(rows[0], primary_won=0.0)) > \
        obj.predict(dict(rows[0], primary_won=1.0))


def test_the_false_claim_about_observation_labels_is_corrected():
    doc = PO.__doc__
    assert 'These labels are "both won / not"' not in doc
    for field in ("primary_won", "hedge_won", "middle_occurred"):
        assert field in doc, field
    assert "BOTH settlement prices" in doc
    assert "full region distribution" in \
        FMD.OUTSIDE_SPLIT_HAS_NO_ADMISSIBLE_SOURCE
    assert "KEY_HEDGE_GIVEN_PRIMARY" in FMD.__doc__


def test_the_void_rate_upper_bound_is_wilson():
    assert PO.wilson_upper_95(0, 0) is None
    assert PO.wilson_upper_95(0, 40) == pytest.approx(0.0876, abs=5e-4)
    assert PO.wilson_upper_95(3, 98) == pytest.approx(0.0862, abs=5e-4)
    assert PO.wilson_upper_95(3, 98) > 3 / 98
    assert PO.MIN_VOID_RATE_FIXTURES == 40
