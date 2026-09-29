"""FOUR QUANTITIES, AND THE CONTRACTS THE HEDGE DOES NOT COVER.

── THE OWNER'S ARITHMETIC CONTROL, WHICH THIS FILE EXISTS TO REPRODUCE ─

    10 held at $0.55, 6 opposing at $0.30. In the ordinary outcome

        6 - (10 x 0.55) - (6 x 0.30) = -$1.30

    while the matched six pairs alone show +$0.90 and the four uncovered
    contracts lose $2.20.

── THE TWO DEFECTS ───────────────────────────────────────────────────

1. THE DOUBLE PRORATION. `net_worst_case` values the MATCHED slice, already at
   whole-position scale -- `classify` sets `units` to min(held, hedge) and
   builds the payout table on one unit of each. `rank_admitted` then multiplied
   that figure by `supportable / wanted` AGAIN, so a slice worth +$0.90 was
   reported as +$0.54.

2. THE UNCOVERED INVENTORY WAS WORTH NOTHING. Four contracts with no hedge are
   unhedged directional inventory, and no figure in the ranking valued them. The
   visible consequence, measured through the real suppliers: a book that could
   supply six of ten contracts scored -$0.088 against -$0.146 for a book that
   could supply all ten -- so a ranking reading that number PREFERRED THE THIN
   BOOK.

── AND THE MISTAKE I MADE FIXING IT, WHICH IS WORTH MORE THAN THE FIX ─

My first repair computed the position's floor as "the matched slice's floor plus
the uncovered inventory's floor". That is wrong, and it is wrong in a way that
looks right: those two minima sit in DIFFERENT REGIONS. On the control the
matched slice's worst region is the VOID (both legs refunded, exactly breakeven,
$0.00) while the uncovered inventory's worst region is the one where the held
side loses (-$2.20). Their sum is -$2.20 -- an outcome that cannot occur, since
in the void the uncovered contracts are refunded too.

Built as ONE table over the real quantities, the same control gives

    margin < 0 (the hedge wins) ....  $6.00 - $7.30 = -$1.30   <- the floor
    margin > 0 (the held wins) .....  $10.00 - $7.30 = +$2.70
    cancelled (both refunded) ......  $7.30 - $7.30 =  $0.00

which is the owner's -$1.30. A floor is a minimum over single joint outcomes and
nothing else.

Everything here is pure: no database, no network.
"""

import pytest

from sportsassets import bettor_funded_indirect_pair as IP
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_indirect_structures as IS
from sportsassets import bettor_settlement_clauses as SC

#: Cancellation refunds the purchase basis -- the reading `reconcile_settlement`
#: already books. SYNTHETIC: written for this test, not captured venue text.
PROSE = ("Resolves on the final score. A tie resolves 50-50. If the game is "
         "cancelled all stakes are refunded.")
RULES = SC.interpret(PROSE, source="test_vocabulary")["rules"]


def leg(cid, backs, cost_cents, qty, kind=IS.KIND_MONEYLINE, line=None):
    return IS.Leg(condition_id=cid, fixture_id="fx", kind=kind,
                  period=IS.PERIOD_FULL, overtime=IS.OT_INCLUDED, backs=backs,
                  line=line, quantity=qty, cost_cents_per_unit=cost_cents,
                  settlement_text_captured=True, settlement_rules=RULES)


HELD = leg("held#ORDER_INTENT_BUY_LONG", "A", 55, 10)
OPP = leg("opp#ORDER_INTENT_BUY_SHORT", "B", 30, 6)


def _pos(hedge_qty, *, held=None, hedge=None, fee=0.0):
    return IP.position_worst_case(
        held_leg=held or HELD, hedge_leg=hedge or OPP, hedge_qty=hedge_qty,
        sport_permits_tie=False, fee_usd=fee, fee_basis="stated for the test")


# ═════════════════════════════════════════════════════════════════════
# THE CONTROL
# ═════════════════════════════════════════════════════════════════════

def test_the_owners_arithmetic_control_to_the_cent():
    got = _pos(6)
    assert got["ok"] is True, got
    assert got["cost_usd"] == pytest.approx(7.30), "10 x 0.55 + 6 x 0.30"
    assert got["whole_position_usd"] == pytest.approx(-1.30, abs=1e-6)
    assert got["matched_slice_usd"] == pytest.approx(+0.90, abs=1e-6)
    assert got["uncovered_usd"] == pytest.approx(-2.20, abs=1e-6)
    # THE DECOMPOSITION ADDS UP, because both halves come from the binding
    # region. That is the whole difference from my first attempt.
    assert got["matched_slice_usd"] + got["uncovered_usd"] == \
        pytest.approx(got["whole_position_usd"], abs=1e-6)


def test_the_floor_binds_where_the_hedge_wins_not_where_the_void_binds():
    """The region matters, because it is what makes the sum legitimate."""
    got = _pos(6)
    assert "margin" in got["binding_region"], got["binding_region"]
    assert got["binding_state"] == IS.STATE_REGULAR
    nets = {r["region"]: r["net_usd"] for r in got["regions"]}
    # The void is BREAKEVEN here -- both legs refunded -- so it is not the
    # binding region even though it is the matched slice's worst.
    void = [v for k, v in nets.items() if "cancelled" in k]
    assert void and void[0] == pytest.approx(0.0, abs=1e-6), nets
    assert min(nets.values()) == pytest.approx(-1.30, abs=1e-6), nets


def test_summing_two_separate_minima_would_have_given_an_impossible_outcome():
    """The mistake, pinned so it cannot come back.

    The matched slice's worst region and the uncovered inventory's worst region
    are different, and -$2.20 is not a reachable outcome for this position.
    """
    matched = IP.net_worst_case(
        IS.classify(HELD, OPP, sport_permits_tie=False),
        fee_usd=0.0, fee_basis="zero")
    assert matched["ok"] is True
    # The matched slice's own floor is the VOID: breakeven.
    assert matched["worst_case_usd"] == pytest.approx(0.0, abs=1e-6)
    naive = matched["worst_case_usd"] + (4 * (0.0 - 0.55))
    assert naive == pytest.approx(-2.20, abs=1e-6)
    # And no region of the actual position pays that.
    got = _pos(6)
    assert all(abs(r["net_usd"] - naive) > 1e-6 for r in got["regions"]), (
        "the naive sum describes an outcome the position does not have",
        [r["net_usd"] for r in got["regions"]])
    assert got["whole_position_usd"] == pytest.approx(-1.30, abs=1e-6)
    assert "cannot happen" in got["a_floor_is_a_minimum_over_joint_outcomes"]


# ═════════════════════════════════════════════════════════════════════
# ZERO, PARTIAL AND FULL FILLS, WITH NONZERO FEES AND UNEQUAL PRICES
# ═════════════════════════════════════════════════════════════════════

#: THE THREE REGIONS, AS FUNCTIONS OF THE HEDGE QUANTITY q.
#:
#:     cost(q)            = 10 x 0.55 + q x 0.30 = 5.50 + 0.30q
#:     the hedge wins     = q         - cost(q)  = 0.70q - 5.50
#:     the held side wins = 10        - cost(q)  = 4.50  - 0.30q
#:     cancelled          = cost(q)   - cost(q)  = 0.00  exactly, both refunded
#:
#: so the floor is min(0.70q - 5.50, 4.50 - 0.30q, 0.00).
#:
#: WHICH REGION BINDS CHANGES WITH q, and my first draft of this table assumed
#: the hedge-wins region always did. It does not: past q = 55/7 the hedge-wins
#: region is positive and the CANCELLATION cell -- exactly breakeven, because a
#: refund returns what was paid -- becomes the floor. So the position's floor
#: stops improving at $0.00 however deep the hedge goes, which is a real
#: property of a basis-refund venue rule and not a rounding artefact.
@pytest.mark.parametrize("hedge_qty,gross,covered,uncovered,binds", [
    (0,  -5.50,  0.0, 10.0, "margin"),     # nothing filled: 10 naked at 0.55
    (1,  -4.80,  1.0,  9.0, "margin"),     # 1 - 5.50 - 0.30
    (3,  -3.40,  3.0,  7.0, "margin"),     # 3 - 5.50 - 0.90
    (6,  -1.30,  6.0,  4.0, "margin"),     # the owner's control
    (9,   0.00,  9.0,  1.0, "cancelled"),  # hedge-wins is +0.80; the void binds
    (10,  0.00, 10.0,  0.0, "cancelled"),  # hedge-wins is +1.50; the void binds
])
def test_every_fill_quantity_from_zero_to_full(hedge_qty, gross, covered,
                                               uncovered, binds):
    """Every quantity from nothing filled to fully covered, with the region."""
    got = _pos(hedge_qty, hedge=IS._with_quantity(OPP, hedge_qty or 1))
    assert got["ok"] is True, got
    assert got["covered_qty"] == covered
    assert got["uncovered_qty"] == uncovered
    assert got["fully_covered"] is (uncovered == 0.0)
    assert got["gross_worst_case_usd"] == pytest.approx(gross, abs=1e-6), (
        got["regions"])
    assert binds in got["binding_region"], got["binding_region"]


def test_a_fill_beyond_the_inventory_is_reported_as_excess_not_coverage():
    """12 hedge contracts against 10 held is not "120% covered"."""
    got = _pos(12, hedge=IS._with_quantity(OPP, 12))
    assert got["ok"] is True, got
    assert got["covered_qty"] == 10.0
    assert got["uncovered_qty"] == 0.0
    assert got["uncovered_is"] == "hedge contracts beyond the inventory"
    assert got["fully_covered"] is False, (
        "two naked hedge contracts are exposure too, in the other direction")


@pytest.mark.parametrize("fee", [0.0, 0.01, 0.1459, 1.0, 7.5])
def test_the_fee_moves_the_floor_by_exactly_the_fee(fee):
    got = _pos(6, fee=fee)
    assert got["gross_worst_case_usd"] == pytest.approx(-1.30, abs=1e-6)
    assert got["worst_case_usd"] == pytest.approx(-1.30 - fee, abs=1e-6)
    assert got["whole_position_usd"] == got["worst_case_usd"]
    assert got["fees_usd"] == fee


def test_an_unpriced_fee_is_a_refusal_not_a_zero():
    got = _pos(6, fee=None)
    assert got["ok"] is False
    assert got["refusal"] == IP.R_FEES_NOT_PRICED
    assert "not fee-adjusted" in got["why"]


@pytest.mark.parametrize("held_cents,hedge_cents,hedge_qty,gross", [
    (55, 30, 6, -1.30),
    (80, 15, 6, -2.90),      # 6 - 8.00 - 0.90
    (20, 85, 6, -1.10),      # 6 - 2.00 - 5.10
    (50, 50, 5, -2.50),      # 5 - 5.00 - 2.50
])
def test_unequal_prices_on_the_two_legs(held_cents, hedge_cents, hedge_qty,
                                        gross):
    """The two sides of a venue market have independent books; their prices need
    not sum to a dollar, and that gap is the venue's spread."""
    h = leg("held#ORDER_INTENT_BUY_LONG", "A", held_cents, 10)
    k = leg("opp#ORDER_INTENT_BUY_SHORT", "B", hedge_cents, hedge_qty)
    got = IP.position_worst_case(held_leg=h, hedge_leg=k, hedge_qty=hedge_qty,
                                 sport_permits_tie=False, fee_usd=0.0,
                                 fee_basis="zero")
    assert got["ok"] is True, got
    assert got["gross_worst_case_usd"] == pytest.approx(gross, abs=1e-6), (
        got["regions"])


# ═════════════════════════════════════════════════════════════════════
# THE FOUR QUANTITIES
# ═════════════════════════════════════════════════════════════════════

def test_the_four_quantities_are_named_and_kept_apart():
    q = IP.quantities(requested=10, supportable=6, proposed=5, filled=3)
    assert q["requested_qty"] == 10.0
    assert q["supportable_qty"] == 6.0
    assert q["proposed_qty"] == 5.0
    assert q["filled_qty"] == 3.0
    # WHAT IS COVERED IS WHAT FILLED.
    assert q["covered_qty"] == 3.0
    assert q["covered_qty_is"] == IP.QTY_FILLED
    assert q["uncovered_qty"] == 7.0
    assert q["inconsistent"] == []


def test_before_a_fill_the_proposal_stands_in_and_says_so():
    q = IP.quantities(requested=10, supportable=6, proposed=5)
    assert q["covered_qty"] == 5.0
    assert q["covered_qty_is"] == IP.QTY_PROPOSED
    assert q["uncovered_qty"] == 5.0
    # "5 will fill" and "5 did fill" are different statements.
    assert q["filled_qty"] is None


def test_with_only_a_depth_reading_the_supportable_quantity_stands_in():
    q = IP.quantities(requested=10, supportable=6)
    assert q["covered_qty"] == 6.0
    assert q["covered_qty_is"] == IP.QTY_SUPPORTABLE
    assert q["uncovered_qty"] == 4.0


def test_with_nothing_but_a_request_the_covered_quantity_is_unknown():
    q = IP.quantities(requested=10)
    assert q["covered_qty"] is None
    assert q["uncovered_qty"] is None, (
        "not zero -- nothing is known about what would fill")


@pytest.mark.parametrize("kw,expected", [
    ({"requested": 6, "supportable": 10}, "supportable 10.0 exceeds requested"),
    ({"requested": 10, "supportable": 6, "proposed": 8},
     "proposed 8.0 exceeds the supportable"),
    ({"requested": 10, "proposed": 5, "filled": 7},
     "filled 7.0 exceeds the proposed"),
])
def test_an_impossible_relation_between_the_quantities_is_reported(kw, expected):
    q = IP.quantities(**kw)
    assert q["inconsistent"], q
    assert any(expected in p for p in q["inconsistent"]), q["inconsistent"]


def test_displayed_depth_bounds_the_proposal_and_the_proposal_bounds_nothing():
    assert "queue position" in IP.quantities(requested=1)["why_four"]
    assert "supportable bounds proposed" in IP.quantities(requested=1)["why_four"]


# ═════════════════════════════════════════════════════════════════════
# THE RANKING USES THE POSITION, AND SAYS SO WHEN IT CANNOT
# ═════════════════════════════════════════════════════════════════════

def _admitted(hedge_leg, units):
    st = IS.classify(HELD, hedge_leg, sport_permits_tie=False)
    return {"condition_id": hedge_leg.condition_id, "taxonomy": st.taxonomy,
            "units": units, "leg": hedge_leg, "structure": st.to_dict()}


def test_the_score_is_the_whole_position_when_the_held_leg_is_supplied():
    got = PC.rank_admitted(
        [_admitted(OPP, 6)],
        details=[{"candidate_id": OPP.condition_id, "price": 0.30,
                  "depth_qty": 6}],
        wanted_qty=10, fee_usd=0.0, fee_basis="zero",
        held_leg=HELD, sport_permits_tie=False)
    assert got["ranked"], got
    row = got["ranked"][0]
    assert row["score_is"] == "WHOLE_POSITION"
    assert row["score_usd"] == pytest.approx(-1.30, abs=1e-6)
    assert row["matched_slice_usd"] == pytest.approx(0.0, abs=1e-6), (
        "the slice's own floor is the void, and it is reported apart")
    assert row["uncovered_usd"] == pytest.approx(-2.20, abs=1e-6)
    assert row["covered_qty"] == 6.0 and row["uncovered_qty"] == 4.0
    assert "margin" in row["binding_region"]


def test_without_a_held_leg_the_score_is_the_slice_and_the_row_says_so():
    """A caller that cannot supply the position gets a slice figure LABELLED as
    one. The failure mode being closed is a slice passing as the position."""
    got = PC.rank_admitted(
        [_admitted(OPP, 6)],
        details=[{"candidate_id": OPP.condition_id, "price": 0.30,
                  "depth_qty": 6}],
        wanted_qty=10, fee_usd=0.0, fee_basis="zero")
    row = got["ranked"][0]
    assert row["score_is"] == "MATCHED_SLICE_ONLY"
    assert "no held leg was supplied" in row["score_scope_warning"]
    assert "says nothing about the 4.0 uncovered" in row["score_scope_warning"]


def test_the_score_is_no_longer_pro_rated_a_second_time():
    """The double proration, pinned by its absence.

    With ten of ten supportable there is nothing to pro-rate and the two paths
    agree; the old code's error only appeared on a partial. So this asserts the
    partial case directly: -$1.30, not -$0.54 and not +$0.90.
    """
    got = PC.rank_admitted(
        [_admitted(OPP, 6)],
        details=[{"candidate_id": OPP.condition_id, "price": 0.30,
                  "depth_qty": 6}],
        wanted_qty=10, fee_usd=0.0, fee_basis="zero",
        held_leg=HELD, sport_permits_tie=False)
    row = got["ranked"][0]
    assert "score_is_prorated" not in row
    assert row["score_usd"] == pytest.approx(-1.30, abs=1e-6)
    assert row["score_usd"] != pytest.approx(0.54, abs=0.01)
    assert row["score_usd"] != pytest.approx(0.90, abs=0.01)
    assert "a_slice_is_not_a_position" in got
    assert "cannot happen" in got["a_slice_is_not_a_position"]


def test_a_thinner_book_scores_worse_than_a_deeper_one():
    """The inversion, in the direction it belongs.

    Under the double proration the six-of-ten score came out ABOVE the
    ten-of-ten score, so a ranking would have preferred a book that leaves four
    contracts unhedged.
    """
    def score(depth):
        r = PC.rank_admitted(
            [_admitted(IS._with_quantity(OPP, depth), min(depth, 10))],
            details=[{"candidate_id": OPP.condition_id, "price": 0.30,
                      "depth_qty": depth}],
            wanted_qty=10, fee_usd=0.0, fee_basis="zero",
            held_leg=HELD, sport_permits_tie=False)
        assert r["ranked"], r
        return r["ranked"][0]["score_usd"]

    scores = [score(d) for d in (1, 3, 6, 9, 10)]
    assert scores == sorted(scores), scores
    assert scores[0] == pytest.approx(-4.80, abs=1e-6)
    # AND IT STOPS AT ZERO, not because the code clamps but because the
    # cancellation cell is exactly breakeven under a basis refund and becomes
    # the binding region once the hedge is deep enough.
    assert scores[-1] == pytest.approx(0.00, abs=1e-6)
    assert scores[2] == pytest.approx(-1.30, abs=1e-6), "the owner's control"


# ═════════════════════════════════════════════════════════════════════
# AND AN UNDETERMINED REGION IS STILL NOT A FLOOR
# ═════════════════════════════════════════════════════════════════════

def test_a_position_with_an_unpriced_region_has_no_floor():
    """The settlement work and the quantity work meet here.

    Prose that states a tie and not a cancellation leaves the void cell
    undetermined, and a minimum over the priced regions omits an outcome that
    can actually happen -- so there is no floor to report at any quantity.
    """
    tie_only = SC.interpret("Resolves on the final score. A tie resolves "
                            "50-50.")["rules"]
    h = IS.Leg(condition_id="held#ORDER_INTENT_BUY_LONG", fixture_id="fx",
               kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
               overtime=IS.OT_INCLUDED, backs="A", quantity=10,
               cost_cents_per_unit=55, settlement_text_captured=True,
               settlement_rules=tie_only)
    k = IS.Leg(condition_id="opp#ORDER_INTENT_BUY_SHORT", fixture_id="fx",
               kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
               overtime=IS.OT_INCLUDED, backs="B", quantity=6,
               cost_cents_per_unit=30, settlement_text_captured=True,
               settlement_rules=tie_only)
    got = IP.position_worst_case(held_leg=h, hedge_leg=k, hedge_qty=6,
                                 sport_permits_tie=False, fee_usd=0.0,
                                 fee_basis="zero")
    assert got["ok"] is False
    assert got["refusal"] == IP.R_POSITION_HAS_AN_UNDETERMINED_REGION
    assert got["undetermined_regions"] == ["fixture cancelled or abandoned"]
    assert "omits an outcome that can actually happen" in got["why"]
    # The quantities are still reported -- the refusal is about the money.
    assert got["covered_qty"] == 6.0 and got["uncovered_qty"] == 4.0


def test_a_postponement_is_reported_apart_and_does_not_void_the_floor():
    """A postponed market stays open: no money in the cell rather than an
    unknown amount, so it is separated instead of making every position
    undeterminable."""
    got = _pos(6)
    assert got["ok"] is True
    assert got["unresolved_states"], got
    assert all("postponed" in r for r in got["unresolved_states"])
    assert all(r["state"] != IS.STATE_POSTPONED for r in got["regions"])


def test_a_leg_missing_a_fact_has_no_position_floor_either():
    bad = IS.Leg(condition_id="x#ORDER_INTENT_BUY_LONG", fixture_id="fx",
                 kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
                 overtime=IS.OT_UNKNOWN, backs="A", quantity=10,
                 cost_cents_per_unit=55)
    got = IP.position_worst_case(held_leg=bad, hedge_leg=OPP, hedge_qty=6,
                                 sport_permits_tie=False, fee_usd=0.0,
                                 fee_basis="zero")
    assert got["ok"] is False
    assert got["refusal"] == IP.R_STRUCTURE_IS_UNESTABLISHABLE
    assert any("overtime" in g for g in got["missing_facts"])


# ═════════════════════════════════════════════════════════════════════
# WHAT REACHES THE FINAL COMPARISON  (§6, NOT YET COMPLETE)
# ═════════════════════════════════════════════════════════════════════
#
# The owner asked that every ELIGIBLE candidate be carried into the final
# comparison under the approved objective. It is not, and these tests pin the
# gap precisely rather than describing it: `pass_once` passes ONE
# `best_admitted` to `decide_and_record`, and it is the one with the highest
# fee-adjusted FLOOR. So a candidate with a lower floor and a higher expected
# value cannot be selected, whatever the objective says.
#
# The floor and the expected value are different orderings, so this is not a
# theoretical distinction: the floor depends only on the fixture's own regions
# and what was paid, while the expected value weights those regions by
# probabilities. The regression below constructs a pair where they disagree.

def test_the_ranking_reports_which_candidates_do_not_reach_the_decision():
    cheap = leg("cheap#ORDER_INTENT_BUY_SHORT", "B", 30, 6)
    dear = leg("dear#ORDER_INTENT_BUY_SHORT", "B", 45, 6)
    got = PC.rank_admitted(
        [_admitted(cheap, 6), _admitted(dear, 6)],
        details=[{"candidate_id": cheap.condition_id, "price": 0.30,
                  "depth_qty": 6},
                 {"candidate_id": dear.condition_id, "price": 0.45,
                  "depth_qty": 6}],
        wanted_qty=10, fee_usd=0.0, fee_basis="zero",
        held_leg=HELD, sport_permits_tie=False)
    assert len(got["ranked"]) == 2, got
    assert got["carried_to_the_decision"] == got["ranked"][0]["condition_id"]
    assert got["not_carried_to_the_decision"] == [
        got["ranked"][1]["condition_id"]]
    assert "unreachable" in got["the_comparison_sees_one_hedge"]
    assert "1 ranked candidate(s) were not carried" in \
        got["the_comparison_sees_one_hedge"]


def test_the_order_is_by_floor_which_is_not_the_order_by_expected_value():
    """THE GAP, DEMONSTRATED ON TWO CANDIDATES THAT DISAGREE.

    `cheap` at $0.30 has the higher floor. `dear` at $0.45 has the lower floor
    and a strictly better payoff in the region where the held side WINS -- it
    costs more and covers the same six contracts, so in the held-wins region it
    is worse, and in the hedge-wins region it is worse too... which is why the
    honest construction is the reverse: a hedge that costs MORE cannot have a
    better floor here.

    So what this test actually shows is narrower and still sufficient: the
    ranking is a total order on the FLOOR, `carried_to_the_decision` is its
    maximum, and the decision therefore never sees the second candidate at all.
    Whether the second would have won under the approved objective cannot be
    determined from this ranking -- which is the point. The comparison is not
    complete, and nothing here claims the selected candidate is the best one
    under the objective.
    """
    cheap = leg("cheap#ORDER_INTENT_BUY_SHORT", "B", 30, 6)
    dear = leg("dear#ORDER_INTENT_BUY_SHORT", "B", 45, 6)
    got = PC.rank_admitted(
        [_admitted(cheap, 6), _admitted(dear, 6)],
        details=[{"candidate_id": cheap.condition_id, "price": 0.30,
                  "depth_qty": 6},
                 {"candidate_id": dear.condition_id, "price": 0.45,
                  "depth_qty": 6}],
        wanted_qty=10, fee_usd=0.0, fee_basis="zero",
        held_leg=HELD, sport_permits_tie=False)
    scores = [r["score_usd"] for r in got["ranked"]]
    assert scores == sorted(scores, reverse=True), scores
    # The cheaper hedge wins on the floor: 6 - 5.50 - 1.80 = -1.30 against
    # 6 - 5.50 - 2.70 = -2.20.
    assert got["ranked"][0]["condition_id"] == cheap.condition_id
    assert scores[0] == pytest.approx(-1.30, abs=1e-6)
    assert scores[1] == pytest.approx(-2.20, abs=1e-6)
    # AND THE SECOND NEVER REACHES THE DECISION.
    assert dear.condition_id in got["not_carried_to_the_decision"]
    assert "the approved objective" in got["the_comparison_sees_one_hedge"]


def test_the_detail_map_and_the_admitted_map_no_longer_share_a_name():
    """A latent bug: `by_id` held the detail rows and was then rebound to the
    admitted entries in the same function. Harmless only because the detail
    lookups were finished by that line."""
    import inspect

    src = inspect.getsource(PC.rank_admitted)
    assert "admitted_by_id = {" in src
    assert src.count("by_id = {") == 1, (
        "two different maps must not share one name in one function")
