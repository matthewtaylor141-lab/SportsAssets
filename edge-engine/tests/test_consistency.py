"""Model-free arbitrage: buying a complete outcome set for under $1.

The profit here is arithmetic, not prediction — which is exactly why the
safety property matters more than the detection. Buying "Arsenal" and
"Over 2.5" for 90c is not an arbitrage, it is two unrelated bets, and a
detector that cannot tell the difference will lose money confidently.
"""

import pytest

from edge.analysis.consistency import (
    DEFAULT_MIN_PROFIT,
    Leg,
    find_dutch_book,
    ladder_violations,
)


def _legs(*prices, size=100):
    return [Leg(f"o{i}", f"t{i}", p, size) for i, p in enumerate(prices)]


# ── detection ───────────────────────────────────────────────────────────

def test_a_two_way_book_summing_under_a_dollar_is_riskless():
    ab = find_dutch_book("evt", "moneyline", _legs(0.47, 0.48),
                         expected_outcomes=2)
    assert ab is not None
    assert ab.cost == pytest.approx(0.95)
    assert ab.profit_per_set == pytest.approx(0.05)
    assert ab.roi == pytest.approx(0.0526, abs=0.001)
    assert ab.sets == 100 and ab.stake == pytest.approx(95.0)


def test_a_three_way_soccer_book_needs_all_three_sides():
    prices = (0.50, 0.25, 0.22)          # home / draw / away = 0.97
    assert find_dutch_book("evt", "moneyline", _legs(*prices),
                           expected_outcomes=3) is not None
    # The SAME two cheap legs, with the third missing, is not an arbitrage —
    # it is a directional bet that happens to cost less than a dollar.
    assert find_dutch_book("evt", "moneyline", _legs(0.25, 0.22),
                           expected_outcomes=3) is None


def test_a_book_at_or_over_a_dollar_is_not_an_opportunity():
    assert find_dutch_book("evt", "moneyline", _legs(0.52, 0.49),
                           expected_outcomes=2) is None
    assert find_dutch_book("evt", "moneyline", _legs(0.50, 0.50),
                           expected_outcomes=2) is None


def test_a_margin_thinner_than_a_tick_is_refused():
    """0.5c of edge is erased by one tick of movement before the last leg
    fills, and a half-filled arbitrage is a naked position."""
    assert find_dutch_book("evt", "moneyline", _legs(0.50, 0.495),
                           expected_outcomes=2) is None
    assert find_dutch_book("evt", "moneyline", _legs(0.50, 0.495),
                           expected_outcomes=2, min_profit=0.004) is not None


def test_fees_are_charged_per_leg_not_per_set():
    """Every leg is its own purchase, so a two-leg set pays the fee twice."""
    assert find_dutch_book("evt", "moneyline", _legs(0.47, 0.51),
                           expected_outcomes=2,
                           fee_per_contract=0.02) is None    # 0.98 + 0.04
    ab = find_dutch_book("evt", "moneyline", _legs(0.47, 0.48),
                         expected_outcomes=2, fee_per_contract=0.01)
    assert ab.cost == pytest.approx(0.97)


def test_size_is_bounded_by_the_thinnest_leg():
    legs = [Leg("a", "ta", 0.47, 500), Leg("b", "tb", 0.48, 7)]
    ab = find_dutch_book("evt", "moneyline", legs, expected_outcomes=2)
    assert ab.sets == 7          # a complete set needs one of EVERY leg


def test_fractional_depth_cannot_buy_a_set():
    legs = [Leg("a", "ta", 0.47, 500), Leg("b", "tb", 0.48, 0.6)]
    assert find_dutch_book("evt", "moneyline", legs, expected_outcomes=2) is None


# ── the ways this could lose money ──────────────────────────────────────

def test_the_same_market_twice_is_not_a_complete_set():
    """Two legs pointing at one token is one bet bought twice — it pays out
    once or not at all, and 'both sides' never happened."""
    dup = [Leg("a", "SAME", 0.47, 100), Leg("a again", "SAME", 0.48, 100)]
    assert find_dutch_book("evt", "moneyline", dup, expected_outcomes=2) is None


def test_extra_legs_are_refused_as_firmly_as_missing_ones():
    """More legs than the partition means something is being double-counted;
    the arithmetic guarantee does not survive it."""
    assert find_dutch_book("evt", "moneyline", _legs(0.30, 0.30, 0.30),
                           expected_outcomes=2) is None


def test_a_nonsense_partition_is_never_an_arbitrage():
    assert find_dutch_book("evt", "x", _legs(0.40), expected_outcomes=1) is None
    assert find_dutch_book("evt", "x", [], expected_outcomes=0) is None


@pytest.mark.parametrize("price", [0.0, 1.0, -0.1, 1.5])
def test_impossible_prices_are_refused(price):
    legs = [Leg("a", "ta", price, 100), Leg("b", "tb", 0.10, 100)]
    assert find_dutch_book("evt", "moneyline", legs, expected_outcomes=2) is None


def test_the_default_margin_requires_a_full_cent():
    assert DEFAULT_MIN_PROFIT >= 0.01


# ── ladder monotonicity ─────────────────────────────────────────────────

def test_a_backwards_ladder_is_reported():
    """P(margin > 2.5) can never exceed P(margin > 1.5). When it does, one
    rung is mispriced relative to another market on the same venue."""
    assert ladder_violations({0.5: 0.60, 1.5: 0.45, 2.5: 0.30}) == []
    assert ladder_violations({0.5: 0.60, 1.5: 0.45, 2.5: 0.52}) == [(1.5, 2.5)]


def test_every_contradicting_pair_is_reported():
    bad = ladder_violations({0.5: 0.40, 1.5: 0.55, 2.5: 0.60})
    assert bad == [(0.5, 1.5), (1.5, 2.5)]


def test_equal_rungs_are_not_a_violation():
    assert ladder_violations({0.5: 0.50, 1.5: 0.50}) == []


def test_a_single_rung_cannot_contradict_itself():
    assert ladder_violations({0.5: 0.50}) == []
    assert ladder_violations({}) == []
