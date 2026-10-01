"""Synthetic wording regressions; no live-market or execution claims."""
import pytest

from sportsassets.agents import paper_benchmark as PB


SOCCER = (
    "This market will settle to the winner at the end of 90 minutes plus "
    "stoppage time. If tied following 90 minutes plus stoppage time, "
    "the market will settle to Tie."
)


@pytest.mark.parametrize("clause", [
    "Extra time is not included.",
    "Extra time is excluded.",
    "Penalties do not count.",
    "Penalties are not included.",
    "Extra time and penalties are not included.",
    "Extra time and penalties will not count.",
])
def test_explicit_exclusion_is_consistent_with_regulation(clause):
    got = PB.venue_grading_period("soccer", SOCCER + " " + clause)
    assert got["refusal"] is None
    assert got["period"] == PB.GP_SOCCER_90


@pytest.mark.parametrize("clause", [
    "Extra time is included.",
    "Penalties count.",
    "Including extra time.",
    "Extra time is not included, but penalties count.",
    "Extra time is not included unless the game is tied.",
    "Extra time is not included. Extra time is included.",
    "Penalties do not count. Penalties count.",
])
def test_inclusion_conflict_or_qualified_exclusion_still_refuses(clause):
    got = PB.venue_grading_period("soccer", SOCCER + " " + clause)
    assert got["refusal"] == PB.R_GP_MISMATCH


def test_exclusion_alone_never_establishes_a_grading_period():
    got = PB.venue_grading_period("soccer", "Extra time is not included.")
    assert got["refusal"] == PB.R_GP_UNKNOWN
