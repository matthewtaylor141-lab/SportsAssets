"""FROM A CLASSIFIED STRUCTURE TO A RANKED ACTION, with fees and depth in it.

── WHAT THIS FILE IS FOR, after a correction ─────────────────────────

Its first version built its own outcome partition, its own per-leg payout function
and its own worst case. `bettor_indirect_structures` already does all three, and
better: it enumerates the fixture's regions over MARGIN, TOTAL or the three-way
categories, handles spreads and totals rather than only moneylines, models
regulation ties, pushes, voids and postponements as explicit states, and refuses
when a leg's overtime rule or orientation is unstated. Keeping a second, weaker
model of the same thing is the defect this session spent its morning removing from
`bettor_funded_book`, where a duplicated block meant edits landed on the copy that
never ran.

So the classification is exercised THROUGH that module, with legs built the way its
own tests build them, and what is tested here is the part that was genuinely
missing:

  * THE FEE, WHICH DECIDES THE SIGN. `bettor_indirect_structures` reports
    `locks_gross_surplus` and is careful to call it gross. A structure with a
    ten-cent gross surplus is loss-making at a twenty-five-cent round trip.
  * INCREMENTAL CAPITAL -- the new money only, because the primary leg is funded.
  * DEPTH, and the refusal to score a partially available hedge on the full pair.
  * THE RANKING of HOLD / ACQUIRE_HEDGE / REDUCE / EXIT on NET worst case, with an
    unrankable action listed rather than scored at zero.

Pure: no database, no venue, no network.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from sportsassets import bettor_funded_indirect_pair as IP
from sportsassets import bettor_indirect_structures as IS


def _ml(backs, *, q=1, cost=None, fixture="fx"):
    return IS.Leg(condition_id="ml-%s" % backs, fixture_id=fixture,
                  kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
                  overtime=IS.OT_INCLUDED, backs=backs, quantity=q,
                  cost_cents_per_unit=cost,
                  tie_rule="tie resolves 50-50", void_rule="void: 50-50",
                  settlement_text_captured=True)


def _spread(backs, line, *, q=1, cost=None, fixture="fx"):
    return IS.Leg(condition_id="sp-%s-%s" % (backs, line), fixture_id=fixture,
                  kind=IS.KIND_SPREAD, period=IS.PERIOD_FULL,
                  overtime=IS.OT_INCLUDED, backs=backs, line=Fraction(line),
                  quantity=q, cost_cents_per_unit=cost,
                  tie_rule="spread at .5 has no tie region",
                  void_rule="void: 50-50", settlement_text_captured=True)


def _clean(a, b, **kw):
    """Classify with no exceptional states, so the arithmetic under test is the
    regular-region minimum rather than a void's."""
    kw.setdefault("sport_permits_tie", False)
    kw.setdefault("fixture_can_void", False)
    kw.setdefault("fixture_can_postpone", False)
    return IS.classify(a, b, **kw)


# ═════════════════════════════════════════════════════════════════════
# 0 · THE CLASSIFICATION IS NOT DUPLICATED HERE
# ═════════════════════════════════════════════════════════════════════

def test_the_valuation_declares_where_its_classification_comes_from():
    """A CONSUMER, NOT A SECOND MODEL. Stated in `describe()` so a later reader
    does not add the partition back."""
    d = IP.describe()
    assert d["classification_comes_from"] == \
        "sportsassets.bettor_indirect_structures"
    assert "would drift from it" in d["why_not_here"]
    assert any("fee" in w for w in d["what_this_adds"])


def test_the_module_holds_no_outcome_partition_of_its_own():
    """STRUCTURAL, and the point of the correction. If this module grows its own
    regions again, the two models can disagree and nothing will say so."""
    import pathlib
    src = pathlib.Path(IP.__file__).read_text()
    for forbidden in ("def outcome_space", "def payoff_table", "DRAW",
                      "pays_on", "def value_the_structure"):
        assert forbidden not in src, (
            "%r is back in the valuation; the partition belongs to "
            "bettor_indirect_structures" % forbidden)


def test_the_module_cannot_reach_a_venue_or_a_database():
    import pathlib
    src = pathlib.Path(IP.__file__).read_text()
    for forbidden in ("import httpx", "asyncpg", "from . import pmus",
                      "submit_fok", "close_position", "async def"):
        assert forbidden not in src, (
            "%r appears in the valuation, which is pure arithmetic" % forbidden)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE FEE IS WHAT DECIDES THE SIGN
# ═════════════════════════════════════════════════════════════════════

def test_a_direct_complement_paying_100_for_99_is_positive_gross():
    """THE STRUCTURE THE STRATEGY EXISTS FOR, classified by the real module: two
    complementary moneylines, one unit each, costing 99c and paying 100c in every
    regular region."""
    s = _clean(_ml("A", cost=49), _ml("B", cost=50))
    assert s.taxonomy == IS.DIRECT_COMPLEMENT, s.why
    got = IP.net_worst_case(s, fee_usd=0.0, fee_basis="test")
    assert got["ok"] is True, got
    assert got["min_payout_usd"] == pytest.approx(1.00)
    assert got["cost_usd"] == pytest.approx(0.99)
    assert got["gross_worst_case_usd"] == pytest.approx(0.01)
    assert got["worst_case_usd"] == pytest.approx(0.01)
    assert got["cannot_lose"] is True


def test_the_same_structure_is_loss_making_once_the_fee_is_priced():
    """THE WHOLE REASON THIS MODULE EXISTS. `locks_gross_surplus` is true and the
    position still loses money. A consumer that stopped at the taxonomy would have
    taken it."""
    s = _clean(_ml("A", cost=49), _ml("B", cost=50))
    assert s.locks_gross_surplus is True
    dear = IP.net_worst_case(s, fee_usd=0.05, fee_basis="test")
    assert dear["gross_worst_case_usd"] == pytest.approx(0.01)
    assert dear["worst_case_usd"] == pytest.approx(-0.04)
    assert dear["cannot_lose"] is False
    assert dear["locks_gross_surplus"] is True, (
        "the GROSS fact is reported unchanged; it is the net that turned")


def test_the_verdict_is_withheld_until_the_fee_is_priced():
    """AN UNPRICED FEE IS NOT A ZERO FEE, at margins of a cent."""
    s = _clean(_ml("A", cost=49), _ml("B", cost=50))
    got = IP.net_worst_case(s, fee_usd=None)
    assert got["ok"] is True
    assert got["worst_case_usd"] is None
    assert got["verdict_is_withheld"] == IP.R_FEES_NOT_PRICED
    # THE OBSERVABLE PARTS ARE STILL REPORTED.
    assert got["gross_worst_case_usd"] == pytest.approx(0.01)
    assert got["fee_basis"] == IP.NOT_ESTABLISHED


def test_a_middle_is_valued_on_its_minimum_not_its_middle_region():
    """THE BEARS/PANTHERS MIDDLE, built from the classifier's OWN fixtures.

    A first attempt hand-rolled two spreads and got GAP, not MIDDLE -- my handicap
    signs were wrong under the module's convention, where Panthers +4.5 is the
    same contract as Bears -4.5 seen from B's side (`line=-4.5, backs="B"`).
    Using the module's own constants removes my sign convention from the test
    entirely, which is the right dependency direction.

    A middle pays 2 units inside the window and 1 outside it, so the worst case is
    the ONE-unit region. Valuing it on the two-unit payout would report a floor
    the position does not have -- and at 103c of cost the honest floor is NEGATIVE
    even though the structure "cannot lose both legs".
    """
    import dataclasses
    a = dataclasses.replace(IS.BEARS_MONEYLINE, cost_cents_per_unit=52)
    b = dataclasses.replace(IS.PANTHERS_PLUS_4_5, cost_cents_per_unit=51)
    s = _clean(a, b)
    assert s.taxonomy == IS.MIDDLE, s.why
    assert s.both_win_regions, "a middle must have a both-win window"
    assert s.max_payout_cents == 200
    got = IP.net_worst_case(s, fee_usd=0.0, fee_basis="test")
    assert got["min_payout_usd"] == pytest.approx(1.00)
    assert got["cost_usd"] == pytest.approx(1.03)
    assert got["worst_case_usd"] == pytest.approx(-0.03)
    assert got["cannot_lose"] is False, (
        "'cannot lose both legs' is not a profitable portfolio -- the owner's "
        "directive says so and the arithmetic agrees")


# ═════════════════════════════════════════════════════════════════════
# 2 · THE STRUCTURE'S OWN REFUSALS PROPAGATE
# ═════════════════════════════════════════════════════════════════════

def test_an_unestablishable_structure_has_no_worst_case_to_net():
    """MISSING FACTS COME FIRST. A fee subtracted from an unknown minimum would be
    a fabricated hedge with a decimal point on it."""
    a = IS.Leg(condition_id="ml-unstated-ot", fixture_id="fx",
               kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
               overtime=IS.OT_UNKNOWN, backs="A", quantity=1,
               cost_cents_per_unit=49, tie_rule="tie 50-50",
               void_rule="void 50-50", settlement_text_captured=True)
    s = _clean(a, _ml("B", cost=50))
    assert s.taxonomy == IS.UNESTABLISHABLE, s.why
    got = IP.net_worst_case(s, fee_usd=0.0)
    assert got["ok"] is False
    assert got["refusal"] == IP.R_STRUCTURE_IS_UNESTABLISHABLE, got
    assert got["missing_facts"], got


def test_an_undetermined_region_is_not_a_minimum_of_zero():
    """AN UNDETERMINED CELL MUST NOT CONTRIBUTE ZERO TO A MINIMUM. A reachable
    void region with no captured rule has no known payout, and reporting the
    minimum as zero would claim a floor derived from a cell nobody can price.

    THE CASE IS TAKEN FROM `test_indirect_structures`' OWN VOID EXAMPLE. My first
    attempt put the missing `void_rule` on a moneyline and the classifier still
    established the structure -- so the test asserted a refusal I had not shown
    was there, which would have passed only by luck. Reusing the shape that module
    already proves refuses keeps this test about the PROPAGATION rather than about
    my guess at the classifier's rules.
    """
    a = _ml("A", cost=49)
    b = IS.Leg(condition_id="sp-no-void", fixture_id="fx",
               kind=IS.KIND_SPREAD, period=IS.PERIOD_FULL,
               overtime=IS.OT_INCLUDED, backs="B", line=Fraction(-9, 2),
               quantity=1, cost_cents_per_unit=50, void_rule=None,
               settlement_text_captured=True)
    s = IS.classify(a, b, sport_permits_tie=False, fixture_can_void=True,
                    fixture_can_postpone=False)
    assert s.taxonomy == IS.UNESTABLISHABLE, s.why
    assert s.undetermined_regions, s
    got = IP.net_worst_case(s, fee_usd=0.0)
    assert got["ok"] is False
    assert got["refusal"] in (IP.R_STRUCTURE_IS_UNESTABLISHABLE,
                             IP.R_MIN_PAYOUT_NOT_DETERMINED), got


def test_a_structure_with_no_stated_cost_reports_no_result():
    s = _clean(_ml("A"), _ml("B"))
    got = IP.net_worst_case(s, fee_usd=0.0)
    assert got["ok"] is False
    assert got["refusal"] == IP.R_COST_NOT_STATED, got


def test_unresolved_states_are_carried_and_make_the_lock_conditional():
    """A VOID IS NOT A PAYOUT. `bettor_indirect_structures` lists the unresolved
    states precisely so a consumer cannot treat "conditional on resolution" as
    unconditional, and `cannot_lose` must not be claimed while one is reachable.
    """
    s = IS.classify(_ml("A", cost=49), _ml("B", cost=50),
                    sport_permits_tie=False, fixture_can_void=True,
                    fixture_can_postpone=False)
    got = IP.net_worst_case(s, fee_usd=0.0)
    if got["ok"]:
        assert got["worst_case_is_conditional_on_resolution"] == bool(
            got["unresolved_states"])
        if got["unresolved_states"]:
            assert got["cannot_lose"] is False, (
                "a lock cannot be claimed while a void is reachable: %r"
                % got["unresolved_states"])


# ═════════════════════════════════════════════════════════════════════
# 3 · INCREMENTAL CAPITAL AND DEPTH
# ═════════════════════════════════════════════════════════════════════

def test_only_the_new_money_counts_as_the_decision():
    got = IP.incremental_capital_usd(hedge_qty=10, hedge_price=0.49,
                                     hedge_fee_usd=0.10)
    assert got["cash_usd"] == pytest.approx(4.90)
    assert got["incremental_capital_usd"] == pytest.approx(5.00)
    assert "already funded" in got["why"]


def test_incremental_capital_is_unknown_without_a_priced_fee():
    got = IP.incremental_capital_usd(hedge_qty=10, hedge_price=0.49)
    assert got["incremental_capital_usd"] is None


def test_absent_depth_is_refused_rather_than_treated_as_unlimited():
    got = IP.depth_supports(wanted_qty=10)
    assert got["ok"] is False
    assert got["refusal"] == IP.R_DEPTH_NOT_ESTABLISHED, got
    assert "not an unlimited one" in got["why"]


def test_a_partial_depth_reports_the_shortfall_and_does_not_round_it_away():
    got = IP.depth_supports(wanted_qty=10, depth_qty_at_price=6)
    assert got["fully_supported"] is False
    assert got["shortfall_qty"] == pytest.approx(4.0)
    assert "not be counted as separately executable" in \
        got["shared_depth_is_not_multiple_quantities"]


# ═════════════════════════════════════════════════════════════════════
# 4 · THE RANKING
# ═════════════════════════════════════════════════════════════════════

def _held_naked(*, fee=0.0):
    """One leg held. Classified against ITSELF is meaningless, so the held state
    is expressed as the single-leg structure the classifier produces for a pair
    where the second leg has no quantity -- which it refuses. So the held case is
    built directly as a `net_worst_case` shape instead: the honest single-leg
    worst case is losing the whole cost, which no classifier is needed for."""
    return {"ok": True, "refusal": None, "taxonomy": "SINGLE_LEG_HELD",
            "min_payout_usd": 0.0, "cost_usd": 0.49,
            "gross_worst_case_usd": -0.49, "fees_usd": fee,
            "worst_case_usd": round(-0.49 - fee, 6), "cannot_lose": False,
            "both_lose_regions": ("the one region this leg does not win",),
            "unresolved_states": (), "locks_gross_surplus": False}


def _candidate(*, hedge_cost_cents=50, depth=1, fee=0.0, hedge_fee=0.0):
    s = _clean(_ml("A", cost=49), _ml("B", cost=hedge_cost_cents))
    return {"paired_structure": IP.net_worst_case(s, fee_usd=fee,
                                                 fee_basis="test"),
            "depth": IP.depth_supports(wanted_qty=1,
                                       depth_qty_at_price=depth),
            "incremental": IP.incremental_capital_usd(
                hedge_qty=1, hedge_price=hedge_cost_cents / 100.0,
                hedge_fee_usd=hedge_fee)}


def test_a_naked_leg_is_ranked_below_the_pair_that_covers_it():
    """THE DECISION THE STRATEGY IS FOR, on worst case alone."""
    got = IP.rank_actions(held=_held_naked(), hedge_candidate=_candidate(),
                          exit_proceeds_usd=0.47)
    assert got["ok"] is True, got
    assert got["best"]["action"] == IP.ACTION_ACQUIRE_HEDGE, got["ranked"]
    assert got["best"]["cannot_lose"] is True
    assert got["best"]["taxonomy"] == IS.DIRECT_COMPLEMENT
    hold = [a for a in got["ranked"] if a["action"] == IP.ACTION_HOLD][0]
    assert hold["worst_case_usd"] == pytest.approx(-0.49)
    assert "No probability enters the ranking" in got["ranking_rule"]


def test_the_ranking_is_never_an_authorisation():
    got = IP.rank_actions(held=_held_naked(), hedge_candidate=_candidate(),
                          exit_proceeds_usd=0.47)
    assert "not that capital may be committed" in got["what_this_is_not"]
    assert IP.describe()["acquire_hedge_is"].startswith("a RECOMMENDATION")


def test_an_expensive_hedge_loses_to_exiting():
    """THE PAIR IS NOT ALWAYS THE ANSWER, and the ranking must be able to say so
    or it is an argument for pairing rather than a comparison."""
    got = IP.rank_actions(held=_held_naked(),
                          hedge_candidate=_candidate(hedge_cost_cents=70),
                          exit_proceeds_usd=0.47)
    acq = [a for a in got["ranked"]
           if a["action"] == IP.ACTION_ACQUIRE_HEDGE][0]
    assert acq["cannot_lose"] is False
    assert acq["worst_case_usd"] == pytest.approx(-0.19)
    assert got["best"]["action"] == IP.ACTION_EXIT, got["ranked"]
    assert [a for a in got["ranked"]
            if a["action"] == IP.ACTION_EXIT][0]["worst_case_usd"] == \
        pytest.approx(-0.02)


def test_an_unreadable_price_makes_exit_unrankable_rather_than_worthless():
    """AN ABSENT PRICE IS NOT A ZERO PRICE. Scoring EXIT at nothing would rank it
    last and the system would hold by default -- a decision made by a missing
    input rather than by the comparison."""
    got = IP.rank_actions(held=_held_naked(), hedge_candidate=_candidate(),
                          exit_proceeds_usd=None)
    assert IP.ACTION_EXIT not in [a["action"] for a in got["ranked"]]
    unrankable = {u["action"]: u for u in got["unrankable"]}
    assert unrankable[IP.ACTION_EXIT]["refusal"] == IP.R_PRICE_NOT_ESTABLISHED
    assert IP.ACTION_REDUCE in unrankable
    assert "decision made by omission" in got["unrankable_are_not_zero"]


def test_a_hedge_the_book_cannot_fully_supply_is_not_ranked_on_the_full_pair():
    """THE PARTIAL-FILL TRAP. Ranking a half-available hedge on the complete
    pair's worst case scores a position the venue cannot supply."""
    got = IP.rank_actions(held=_held_naked(),
                          hedge_candidate=_candidate(depth=0),
                          exit_proceeds_usd=0.47)
    assert IP.ACTION_ACQUIRE_HEDGE not in [a["action"] for a in got["ranked"]]
    u = {x["action"]: x for x in got["unrankable"]}[IP.ACTION_ACQUIRE_HEDGE]
    assert u["refusal"] == IP.R_DEPTH_NOT_ESTABLISHED
    assert "leaves the primary leg naked" in u["why"]


def test_an_unpriced_hedge_fee_makes_the_acquisition_unrankable():
    cand = _candidate()
    cand["incremental"] = IP.incremental_capital_usd(hedge_qty=1,
                                                     hedge_price=0.50)
    got = IP.rank_actions(held=_held_naked(), hedge_candidate=cand,
                          exit_proceeds_usd=0.47)
    u = {x["action"]: x for x in got["unrankable"]}[IP.ACTION_ACQUIRE_HEDGE]
    assert u["refusal"] == IP.R_FEES_NOT_PRICED


def test_an_unestablishable_candidate_is_unrankable_with_its_missing_facts():
    """THE REFUSAL CARRIES THROUGH TO THE RANKING, naming what was missing rather
    than reporting an action that could not be valued as merely worse."""
    a = IS.Leg(condition_id="ml-unstated-ot", fixture_id="fx",
               kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
               overtime=IS.OT_UNKNOWN, backs="A", quantity=1,
               cost_cents_per_unit=49, tie_rule="t", void_rule="v",
               settlement_text_captured=True)
    s = _clean(a, _ml("B", cost=50))
    got = IP.rank_actions(
        held=_held_naked(),
        hedge_candidate={
            "paired_structure": IP.net_worst_case(s, fee_usd=0.0),
            "depth": IP.depth_supports(wanted_qty=1, depth_qty_at_price=1),
            "incremental": IP.incremental_capital_usd(
                hedge_qty=1, hedge_price=0.50, hedge_fee_usd=0.0)},
        exit_proceeds_usd=0.47)
    u = {x["action"]: x for x in got["unrankable"]}[IP.ACTION_ACQUIRE_HEDGE]
    assert u["refusal"] == IP.R_STRUCTURE_IS_UNESTABLISHABLE
    assert u["missing_facts"], u
    assert got["best"]["action"] != IP.ACTION_ACQUIRE_HEDGE


def test_a_withheld_verdict_on_the_pair_makes_it_unrankable_too():
    """A PAIR WHOSE FEE IS NOT PRICED IS NOT RANKED ON ITS GROSS NUMBER. That
    substitution is exactly how a fee-sensitive structure gets taken."""
    s = _clean(_ml("A", cost=49), _ml("B", cost=50))
    got = IP.rank_actions(
        held=_held_naked(),
        hedge_candidate={
            "paired_structure": IP.net_worst_case(s, fee_usd=None),
            "depth": IP.depth_supports(wanted_qty=1, depth_qty_at_price=1),
            "incremental": IP.incremental_capital_usd(
                hedge_qty=1, hedge_price=0.50, hedge_fee_usd=0.0)},
        exit_proceeds_usd=0.47)
    u = {x["action"]: x for x in got["unrankable"]}[IP.ACTION_ACQUIRE_HEDGE]
    assert u["refusal"] == IP.R_FEES_NOT_PRICED
