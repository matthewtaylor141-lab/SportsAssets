"""THE INDIRECT PAIR'S VALUATION, AND THE THREE-OUTCOME FIXTURE THAT BREAKS IT.

WHY THIS FILE EXISTS. `bettor_pair_engine` values the YES/NO pair -- which on
this venue is netting on ONE book, so it has no second settling holding.
Migration 131's structure is different: two DISTINCT contracts on the same
fixture, settling independently. Nothing valued that, so "indirect middles" was a
schema and a name.

THE ONE MISTAKE EVERYTHING HERE IS BUILT AROUND. Two moneyline legs covering
"both teams" are a locked position on a two-outcome fixture -- whichever side
wins, one leg pays its quantity -- and a TOTAL LOSS on a fixture that can be
drawn, because two moneylines pay nothing on a draw. The legs are identical in
both cases. Only the FIXTURE'S OUTCOME SPACE distinguishes them, so an
unestablished outcome space must be a refusal and never a default.

These tests are pure: no database, no venue, no network. The arithmetic is the
thing under test and it needs none of those.
"""

from __future__ import annotations

import pytest

from sportsassets import bettor_funded_indirect_pair as IP

TWO_WAY = ("HOME", "AWAY")
THREE_WAY = ("HOME", "DRAW", "AWAY")


def _space(outcomes, *, fixture="nba-bos-mia-2026-11-02",
           by="VENUE_SETTLEMENT_TERMS"):
    return IP.outcome_space(fixture=fixture, outcomes=outcomes,
                            established_by=by,
                            evidence={"captured": "test"})


# ═════════════════════════════════════════════════════════════════════
# 1 · THE OUTCOME SPACE IS EVIDENCE OR IT IS A REFUSAL
# ═════════════════════════════════════════════════════════════════════

def test_an_unestablished_outcome_space_is_refused_not_defaulted():
    """THE DEFAULT IS THE BUG. Assuming two outcomes is exactly how a strategy
    values a Premier League double stake as a locked NBA hedge."""
    got = IP.outcome_space(fixture="epl-x", outcomes=TWO_WAY,
                           established_by="I_ASSUMED_IT")
    assert got["ok"] is False
    assert got["refusal"] == IP.R_OUTCOME_SOURCE_NOT_RECOGNISED, got
    assert "VENUE_SETTLEMENT_TERMS" in got["recognised"]


def test_a_single_outcome_is_not_an_outcome_space():
    got = _space(("HOME",))
    assert got["ok"] is False
    assert got["refusal"] == IP.R_OUTCOME_SPACE_NOT_ESTABLISHED, got


def test_valuation_refuses_outright_when_the_space_is_not_established():
    """THE REFUSAL PROPAGATES. A valuation that quietly proceeded on a rejected
    space would make the refusal decorative."""
    bad = IP.outcome_space(fixture="x", outcomes=TWO_WAY,
                           established_by="GUESSED")
    got = IP.value_the_structure(
        space=bad,
        legs=[IP.leg("PRIMARY", slug="a", qty=10, price_paid=0.5,
                     pays_on=["HOME"])],
        fee_usd=0.1)
    assert got["ok"] is False
    assert got["refusal"] == IP.R_OUTCOME_SOURCE_NOT_RECOGNISED, got


def test_a_void_is_not_listed_as_a_settling_outcome():
    """A VOID RETURNS COLLATERAL ON WHAT IS STILL HELD; it is not a result the
    market settles to, and listing it would put a payout of zero in the worst
    case for every structure, making every pair look unhedged."""
    got = _space(TWO_WAY)
    assert got["ok"] is True
    assert "VOID" not in got["outcomes"]
    assert "void is not a settling outcome" in got["void_is_not_listed"]


# ═════════════════════════════════════════════════════════════════════
# 2 · TWO OUTCOMES: THE STRUCTURE THAT IS ACTUALLY LOCKED
# ═════════════════════════════════════════════════════════════════════

def test_equal_legs_on_a_two_way_fixture_cannot_lose_when_the_margin_is_positive():
    """THE CASE THE STRATEGY EXISTS FOR. 10 at 0.48 and 10 at 0.49 costs 9.70
    plus fees; whichever side wins, one leg pays 10. The worst case is positive
    and depends on no probability at all."""
    got = IP.value_the_structure(
        space=_space(TWO_WAY),
        legs=[IP.leg("PRIMARY", slug="aec-bos-ml", qty=10, price_paid=0.48,
                     pays_on=["HOME"]),
              IP.leg("HEDGE", slug="aec-mia-ml", qty=10, price_paid=0.49,
                     pays_on=["AWAY"])],
        fee_usd=0.20, fee_basis="calibration_fees.order_fees")
    assert got["ok"] is True, got
    assert got["acquisition_usd"] == pytest.approx(9.70)
    assert got["total_cost_usd"] == pytest.approx(9.90)
    assert got["payout_by_outcome"] == {"HOME": 10.0, "AWAY": 10.0}
    assert got["worst_payout_usd"] == pytest.approx(10.0)
    assert got["worst_case_usd"] == pytest.approx(0.10)
    assert got["coverage"] == IP.COVER_ALL_OUTCOMES_PAY
    assert got["cannot_lose"] is True
    assert got["outcomes_paying_nothing"] == []
    # AND THE BUCKETS ARE NOT SUMMED INTO ONE FIGURE.
    assert "buckets_are_not_summed" in got


def test_the_fee_is_what_decides_the_sign_at_these_margins():
    """THE SAME LEGS, A LARGER FEE. A structure whose gross margin is ten cents
    is loss-making at a twenty-five cent fee, and nothing about the legs changed.
    This is why the worst case is withheld until fees are priced."""
    legs = [IP.leg("PRIMARY", slug="aec-bos-ml", qty=10, price_paid=0.48,
                   pays_on=["HOME"]),
            IP.leg("HEDGE", slug="aec-mia-ml", qty=10, price_paid=0.49,
                   pays_on=["AWAY"])]
    cheap = IP.value_the_structure(space=_space(TWO_WAY), legs=legs,
                                   fee_usd=0.20)
    dear = IP.value_the_structure(space=_space(TWO_WAY), legs=legs,
                                  fee_usd=0.45)
    assert cheap["worst_case_usd"] > 0
    assert dear["worst_case_usd"] < 0
    assert dear["cannot_lose"] is False


def test_unequal_quantities_are_valued_on_the_smaller_leg():
    """THE WORST OUTCOME IS THE ONE THAT PAYS LEAST, which is the short leg's
    side. A structure valued on the average quantity would report a worst case it
    cannot achieve."""
    got = IP.value_the_structure(
        space=_space(TWO_WAY),
        legs=[IP.leg("PRIMARY", slug="a", qty=10, price_paid=0.48,
                     pays_on=["HOME"]),
              IP.leg("HEDGE", slug="b", qty=6, price_paid=0.49,
                     pays_on=["AWAY"])],
        fee_usd=0.0)
    assert got["payout_by_outcome"] == {"HOME": 10.0, "AWAY": 6.0}
    assert got["worst_outcome"] == "AWAY"
    assert got["worst_payout_usd"] == pytest.approx(6.0)
    # cost = 4.80 + 2.94 = 7.74; worst payout 6.00
    assert got["worst_case_usd"] == pytest.approx(6.0 - 7.74)
    assert got["cannot_lose"] is False


def test_the_worst_case_is_withheld_when_fees_are_not_priced():
    """AN UNPRICED FEE IS NOT A ZERO FEE. A worst case computed without fees is
    optimistic by exactly the fees, at the margins where the fee decides the
    sign."""
    got = IP.value_the_structure(
        space=_space(TWO_WAY),
        legs=[IP.leg("PRIMARY", slug="a", qty=10, price_paid=0.48,
                     pays_on=["HOME"]),
              IP.leg("HEDGE", slug="b", qty=10, price_paid=0.49,
                     pays_on=["AWAY"])],
        fee_usd=None)
    assert got["ok"] is True
    assert got["worst_case_usd"] is None
    assert got["verdict_is_withheld"] == IP.R_FEES_NOT_PRICED
    assert got["total_cost_usd"] is None
    # THE OBSERVABLE PARTS ARE STILL REPORTED -- withholding the verdict is not
    # withholding the facts.
    assert got["acquisition_usd"] == pytest.approx(9.70)
    assert got["payout_by_outcome"] == {"HOME": 10.0, "AWAY": 10.0}


# ═════════════════════════════════════════════════════════════════════
# 3 · THREE OUTCOMES: THE SAME LEGS, AND A TOTAL LOSS
# ═════════════════════════════════════════════════════════════════════

def test_two_moneylines_on_a_drawn_fixture_pay_nothing_and_are_not_a_hedge():
    """THE COUNTEREXAMPLE THIS MODULE EXISTS FOR.

    Identical legs to the locked NBA case. On a fixture that admits a DRAW,
    neither leg pays, so the worst case is minus the entire cost -- and the
    coverage says so rather than the structure reading as hedged.
    """
    got = IP.value_the_structure(
        space=_space(THREE_WAY, fixture="epl-ars-che-2026-11-02"),
        legs=[IP.leg("PRIMARY", slug="aec-ars-ml", qty=10, price_paid=0.48,
                     pays_on=["HOME"]),
              IP.leg("HEDGE", slug="aec-che-ml", qty=10, price_paid=0.49,
                     pays_on=["AWAY"])],
        fee_usd=0.20)
    assert got["ok"] is True, got
    assert got["payout_by_outcome"] == {"HOME": 10.0, "DRAW": 0.0, "AWAY": 10.0}
    assert got["worst_outcome"] == "DRAW"
    assert got["worst_payout_usd"] == pytest.approx(0.0)
    assert got["worst_case_usd"] == pytest.approx(-9.90)
    assert got["coverage"] == IP.COVER_SOME_OUTCOMES_PAY_NOTHING
    assert got["outcomes_paying_nothing"] == ["DRAW"]
    assert got["cannot_lose"] is False
    assert "unhedged double stake" in got["warning"]


def test_covering_the_draw_too_restores_the_lock():
    """AND THE FIX IS A THIRD LEG, not a different opinion about the fixture. A
    structure that covers every settling outcome is locked again -- which is the
    same rule, applied to the real outcome space."""
    got = IP.value_the_structure(
        space=_space(THREE_WAY, fixture="epl-ars-che-2026-11-02"),
        legs=[IP.leg("PRIMARY", slug="aec-ars-ml", qty=10, price_paid=0.31,
                     pays_on=["HOME"]),
              IP.leg("HEDGE", slug="aec-che-ml", qty=10, price_paid=0.32,
                     pays_on=["AWAY"]),
              IP.leg("HEDGE", slug="aec-draw", qty=10, price_paid=0.33,
                     pays_on=["DRAW"])],
        fee_usd=0.30)
    assert got["payout_by_outcome"] == {"HOME": 10.0, "DRAW": 10.0,
                                        "AWAY": 10.0}
    assert got["coverage"] == IP.COVER_ALL_OUTCOMES_PAY
    # cost = 3.10 + 3.20 + 3.30 + 0.30 = 9.90
    assert got["worst_case_usd"] == pytest.approx(0.10)
    assert got["cannot_lose"] is True


def test_a_leg_that_pays_on_an_outcome_the_fixture_does_not_admit_is_refused():
    """A LEG DESCRIBED AGAINST THE WRONG FIXTURE is not a valuation input. If
    `pays_on` names an outcome that does not exist, either the leg or the space is
    wrong, and computing a payout from the intersection would silently treat the
    leg as paying in nothing."""
    got = IP.value_the_structure(
        space=_space(TWO_WAY),
        legs=[IP.leg("PRIMARY", slug="a", qty=10, price_paid=0.5,
                     pays_on=["DRAW"])],
        fee_usd=0.0)
    assert got["ok"] is False
    assert got["refusal"] == IP.R_LEG_PAYS_ON_NO_OUTCOME, got
    assert got["unknown_outcomes"] == ["DRAW"]


def test_two_legs_on_one_contract_are_refused_as_netting():
    """THE SAME RULE THE DATABASE ENFORCES, at the valuation. On this venue
    buying the opposite side of one market reduces the same book, so there is no
    second settling holding to value."""
    got = IP.value_the_structure(
        space=_space(TWO_WAY),
        legs=[IP.leg("PRIMARY", slug="aec-bos-ml", qty=10, price_paid=0.48,
                     pays_on=["HOME"]),
              IP.leg("HEDGE", slug="aec-bos-ml", qty=10, price_paid=0.52,
                     pays_on=["AWAY"])],
        fee_usd=0.0)
    assert got["ok"] is False
    assert got["refusal"] == IP.R_LEGS_ARE_THE_SAME_CONTRACT, got


# ═════════════════════════════════════════════════════════════════════
# 4 · INCREMENTAL CAPITAL AND DEPTH
# ═════════════════════════════════════════════════════════════════════

def test_only_the_new_money_counts_as_the_decision():
    """THE PRIMARY LEG IS ALREADY FUNDED. Comparing the pair's TOTAL against
    headroom that has already absorbed the first leg counts it twice, and the
    direction of that error is toward believing there is less room than there is
    -- or, with the sign the other way round, spending money that is not there."""
    got = IP.incremental_capital_usd(hedge_qty=10, hedge_price=0.49,
                                     hedge_fee_usd=0.10)
    assert got["cash_usd"] == pytest.approx(4.90)
    assert got["incremental_capital_usd"] == pytest.approx(5.00)
    assert "already funded" in got["why"]


def test_incremental_capital_is_unknown_without_a_priced_fee():
    got = IP.incremental_capital_usd(hedge_qty=10, hedge_price=0.49)
    assert got["incremental_capital_usd"] is None
    assert got["fee_usd"] is None


def test_absent_depth_is_refused_rather_than_treated_as_unlimited():
    """THE FAILURE THIS PREVENTS. A hedge sized against a depth never read fills
    partly, leaving the primary leg naked for the remainder while the accounting
    records a complete pair."""
    got = IP.depth_supports(wanted_qty=10)
    assert got["ok"] is False
    assert got["refusal"] == IP.R_DEPTH_NOT_ESTABLISHED, got
    assert "not an unlimited one" in got["why"]


def test_a_partial_depth_reports_the_shortfall_and_does_not_round_it_away():
    got = IP.depth_supports(wanted_qty=10, depth_qty_at_price=6)
    assert got["ok"] is True
    assert got["fully_supported"] is False
    assert got["supportable_qty"] == pytest.approx(6.0)
    assert got["shortfall_qty"] == pytest.approx(4.0)
    assert "not be counted as separately executable" in \
        got["shared_depth_is_not_multiple_quantities"]


# ═════════════════════════════════════════════════════════════════════
# 5 · THE RANKING
# ═════════════════════════════════════════════════════════════════════

def _held_naked(fee=0.10):
    return IP.value_the_structure(
        space=_space(TWO_WAY),
        legs=[IP.leg("PRIMARY", slug="aec-bos-ml", qty=10, price_paid=0.48,
                     pays_on=["HOME"])],
        fee_usd=fee)


def _candidate(*, hedge_price=0.49, depth=10, fee=0.10):
    paired = IP.value_the_structure(
        space=_space(TWO_WAY),
        legs=[IP.leg("PRIMARY", slug="aec-bos-ml", qty=10, price_paid=0.48,
                     pays_on=["HOME"]),
              IP.leg("HEDGE", slug="aec-mia-ml", qty=10,
                     price_paid=hedge_price, pays_on=["AWAY"])],
        fee_usd=0.20)
    return {"paired_structure": paired,
            "depth": IP.depth_supports(wanted_qty=10,
                                       depth_qty_at_price=depth),
            "incremental": IP.incremental_capital_usd(
                hedge_qty=10, hedge_price=hedge_price, hedge_fee_usd=fee)}


def test_a_naked_leg_is_ranked_below_the_pair_that_covers_it():
    """THE DECISION THE STRATEGY IS FOR. Holding one leg risks the whole cost;
    completing the pair at these prices cannot lose. The ranking says so on worst
    case alone, with no probability anywhere in it."""
    got = IP.rank_actions(held=_held_naked(), hedge_candidate=_candidate(),
                          exit_proceeds_usd=4.70)
    assert got["ok"] is True, got
    assert got["best"]["action"] == IP.ACTION_ACQUIRE_HEDGE, got["ranked"]
    assert got["best"]["cannot_lose"] is True
    assert got["best"]["incremental_capital_usd"] == pytest.approx(5.00)
    hold = [a for a in got["ranked"] if a["action"] == IP.ACTION_HOLD][0]
    assert hold["worst_case_usd"] == pytest.approx(-4.90)
    assert "No probability enters the ranking" in got["ranking_rule"]


def test_the_ranking_is_never_an_authorisation():
    """ACQUIRE_HEDGE FIRST MEANS THE ARITHMETIC IS BEST, not that capital may be
    committed. Stated in the output, because a consumer reading only `best` is
    exactly who needs to be told."""
    got = IP.rank_actions(held=_held_naked(), hedge_candidate=_candidate(),
                          exit_proceeds_usd=4.70)
    assert "not that capital may be committed" in got["what_this_is_not"]
    assert IP.describe()["acquire_hedge_is"].startswith("a RECOMMENDATION")


def test_an_expensive_hedge_loses_to_exiting():
    """THE PAIR IS NOT ALWAYS THE ANSWER. At a hedge price that makes the
    structure loss-making, selling what is held scores better -- and the ranking
    must be able to say so, or it is an argument for pairing rather than a
    comparison."""
    got = IP.rank_actions(held=_held_naked(),
                          hedge_candidate=_candidate(hedge_price=0.62),
                          exit_proceeds_usd=4.85)
    acq = [a for a in got["ranked"]
           if a["action"] == IP.ACTION_ACQUIRE_HEDGE][0]
    assert acq["cannot_lose"] is False
    assert got["best"]["action"] == IP.ACTION_EXIT, got["ranked"]


def test_an_unreadable_price_makes_exit_unrankable_rather_than_worthless():
    """AN ABSENT PRICE IS NOT A ZERO PRICE. Scoring EXIT at nothing would rank it
    last and the system would hold by default -- a decision made by a missing
    input rather than by the comparison."""
    got = IP.rank_actions(held=_held_naked(), hedge_candidate=_candidate(),
                          exit_proceeds_usd=None)
    actions = [a["action"] for a in got["ranked"]]
    assert IP.ACTION_EXIT not in actions
    unrankable = {u["action"]: u for u in got["unrankable"]}
    assert IP.ACTION_EXIT in unrankable
    assert unrankable[IP.ACTION_EXIT]["refusal"] == IP.R_PRICE_NOT_ESTABLISHED
    assert IP.ACTION_REDUCE in unrankable
    assert "decision made by omission" in got["unrankable_are_not_zero"]


def test_a_hedge_the_book_cannot_fully_supply_is_not_ranked_on_the_full_pair():
    """THE PARTIAL-FILL TRAP. Ranking a half-available hedge on the complete
    pair's worst case scores a position the venue cannot supply, and the
    difference is exactly the naked remainder."""
    got = IP.rank_actions(held=_held_naked(),
                          hedge_candidate=_candidate(depth=4),
                          exit_proceeds_usd=4.70)
    actions = [a["action"] for a in got["ranked"]]
    assert IP.ACTION_ACQUIRE_HEDGE not in actions
    unrankable = {u["action"]: u for u in got["unrankable"]}
    assert unrankable[IP.ACTION_ACQUIRE_HEDGE]["refusal"] == \
        IP.R_DEPTH_NOT_ESTABLISHED
    assert unrankable[IP.ACTION_ACQUIRE_HEDGE]["shortfall_qty"] == \
        pytest.approx(6.0)
    assert "leaves the primary leg naked" in \
        unrankable[IP.ACTION_ACQUIRE_HEDGE]["why"]


def test_an_unpriced_hedge_fee_makes_the_acquisition_unrankable():
    """AT THESE MARGINS THE FEE IS THE DECISION. An acquisition ranked without
    its own fee is ranked on a number that omits the term most likely to change
    its sign."""
    cand = _candidate()
    cand["incremental"] = IP.incremental_capital_usd(hedge_qty=10,
                                                     hedge_price=0.49)
    got = IP.rank_actions(held=_held_naked(), hedge_candidate=cand,
                          exit_proceeds_usd=4.70)
    unrankable = {u["action"]: u for u in got["unrankable"]}
    assert unrankable[IP.ACTION_ACQUIRE_HEDGE]["refusal"] == \
        IP.R_FEES_NOT_PRICED


def test_a_drawn_fixture_ranks_the_pair_on_its_real_worst_case():
    """END TO END ON THE COUNTEREXAMPLE. The same two-leg acquisition that wins
    the ranking on a two-outcome fixture must lose it on a three-outcome one,
    because its worst case is minus the whole cost."""
    held = IP.value_the_structure(
        space=_space(THREE_WAY, fixture="epl-ars-che-2026-11-02"),
        legs=[IP.leg("PRIMARY", slug="aec-ars-ml", qty=10, price_paid=0.48,
                     pays_on=["HOME"])],
        fee_usd=0.10)
    paired = IP.value_the_structure(
        space=_space(THREE_WAY, fixture="epl-ars-che-2026-11-02"),
        legs=[IP.leg("PRIMARY", slug="aec-ars-ml", qty=10, price_paid=0.48,
                     pays_on=["HOME"]),
              IP.leg("HEDGE", slug="aec-che-ml", qty=10, price_paid=0.49,
                     pays_on=["AWAY"])],
        fee_usd=0.20)
    got = IP.rank_actions(
        held=held,
        hedge_candidate={
            "paired_structure": paired,
            "depth": IP.depth_supports(wanted_qty=10, depth_qty_at_price=10),
            "incremental": IP.incremental_capital_usd(
                hedge_qty=10, hedge_price=0.49, hedge_fee_usd=0.10)},
        exit_proceeds_usd=4.70)
    acq = [a for a in got["ranked"]
           if a["action"] == IP.ACTION_ACQUIRE_HEDGE][0]
    assert acq["outcomes_paying_nothing"] == ["DRAW"]
    assert acq["cannot_lose"] is False
    assert acq["worst_case_usd"] == pytest.approx(-9.90)
    # SPENDING NEW MONEY TO MAKE THE WORST CASE WORSE MUST NOT WIN.
    assert got["best"]["action"] != IP.ACTION_ACQUIRE_HEDGE, got["ranked"]


def test_the_module_cannot_reach_a_venue_or_a_database():
    """STRUCTURAL. This module is arithmetic. It must not acquire the ability to
    read a book or write a row without that being visible in a diff."""
    import pathlib
    src = pathlib.Path(IP.__file__).read_text()
    for forbidden in ("import httpx", "asyncpg", "from . import pmus",
                      "submit_fok", "close_position", "async def"):
        assert forbidden not in src, (
            "%r appears in the indirect-pair valuation, which is pure "
            "arithmetic" % forbidden)
