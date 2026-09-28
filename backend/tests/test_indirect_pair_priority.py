"""A smaller indirect hedge must not lose priority to a full direct pair."""

from dataclasses import replace
from fractions import Fraction
from itertools import permutations

import pytest

from sportsassets import bettor_indirect_structures as IS


def legs(indirect_quantity):
    held = replace(IS.BEARS_MONEYLINE, condition_id="held", quantity=10)
    indirect = replace(IS.PANTHERS_PLUS_4_5, condition_id="indirect",
                       quantity=indirect_quantity)
    direct = replace(held, condition_id="direct", backs="B")
    return (held, indirect, direct)


@pytest.mark.parametrize("indirect_quantity", [4, 5, 10, 20])
@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_indirect_middle_has_priority_even_with_unequal_inventory(
        indirect_quantity, order):
    supplied = legs(indirect_quantity)
    result = IS.allocate([supplied[i] for i in order],
                         sport_permits_tie=True, fixture_can_void=True,
                         fixture_can_postpone=True)

    first = result.structures[0]
    assert first.taxonomy == IS.MIDDLE
    assert set(first.legs) == {"held", "indirect"}
    assert first.units == min(10, indirect_quantity)
    assert first.both_win_regions
    assert first.both_lose_regions == ()

    # Preference must not allocate the held contracts twice or discard a
    # remainder. Each allocated unit consumes one unit from EACH leg.
    for leg in supplied:
        used = sum(s.units for s in result.structures
                   if leg.condition_id in s.legs)
        assert used + result.unallocated.get(leg.condition_id, 0) == leg.quantity


def test_explicit_preference_is_still_respected():
    result = IS.allocate(list(legs(5)), sport_permits_tie=True,
                         prefer=(IS.DIRECT_COMPLEMENT, IS.MIDDLE, IS.GAP))
    assert result.structures[0].taxonomy == IS.DIRECT_COMPLEMENT
    assert set(result.structures[0].legs) == {"held", "direct"}


def test_indirect_preference_does_not_override_incompatible_periods():
    held, indirect, direct = legs(5)
    indirect = replace(indirect, period=IS.PERIOD_H1)
    result = IS.allocate([held, indirect, direct], sport_permits_tie=True)
    assert result.structures[0].taxonomy == IS.DIRECT_COMPLEMENT
    assert result.unallocated["indirect"] == 5
    assert any(set(s.legs) == {"held", "indirect"} for s in result.refused)


def test_captured_prose_alone_does_not_establish_an_integer_push_payout():
    held, indirect, _ = legs(1)
    indirect = replace(indirect, line=Fraction(-4))
    result = IS.classify(held, indirect, sport_permits_tie=True)
    assert result.taxonomy == IS.UNESTABLISHABLE
    assert any("push" in reason for reason in result.missing_facts)
    assert IS._leg_payout_cents(
        indirect, IS.Region("push at four", lo=4, hi=4)) is None


def test_refund_prose_does_not_mean_a_fifty_cent_payout():
    held, indirect, _ = legs(1)
    held = replace(held, cost_cents_per_unit=80, void_rule="refund stake")
    result = IS.classify(held, indirect, sport_permits_tie=True)
    assert result.taxonomy == IS.UNESTABLISHABLE
    assert result.locks_gross_surplus is None
    assert IS._leg_payout_cents(
        held, IS.Region("cancelled", state=IS.STATE_VOID)) is None
