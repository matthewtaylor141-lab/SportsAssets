"""Underdog cash-out sleeve: dog selection, sizing, the +20% trigger,
and the non-interference contract's pure pieces."""

from sportsassets.workers.underdog import (
    cash_out_threshold,
    pick_underdog,
    shares_for,
)


def test_the_dog_is_the_cheaper_side_inside_the_band():
    assert pick_underdog([("fav", 0.62), ("dog", 0.36)]) == ("dog", 0.36)
    # Order of sides must not matter.
    assert pick_underdog([("dog", 0.36), ("fav", 0.62)]) == ("dog", 0.36)


def test_coin_flips_and_lottery_tickets_are_refused():
    assert pick_underdog([("a", 0.50), ("b", 0.52)]) is None   # 50c is no dog
    assert pick_underdog([("a", 0.49), ("b", 0.49)]) is None   # nobody cheaper
    assert pick_underdog([("a", 0.03), ("b", 0.96)]) is None   # sub-5c junk
    assert pick_underdog([("a", None), ("b", 0.40)]) is None   # unpriced side
    assert pick_underdog([("a", 0.40)]) is None                # one-sided


def test_band_edges_are_inclusive_where_they_should_be():
    assert pick_underdog([("a", 0.48), ("b", 0.55)]) == ("a", 0.48)
    assert pick_underdog([("a", 0.05), ("b", 0.94)]) == ("a", 0.05)


def test_cash_out_threshold_is_twenty_percent_on_entry():
    assert cash_out_threshold(0.30) == 0.36
    assert cash_out_threshold(0.45) == 0.54
    # $1 at 0.25 -> 4 contracts; selling at 0.30 realizes $0.20 on $1.00.
    assert cash_out_threshold(0.25) == 0.30


def test_sizing_never_exceeds_the_dollar():
    assert shares_for(1.00, 0.25) == 4     # exactly $1.00
    assert shares_for(1.00, 0.30) == 3     # $0.90, never $1.20
    assert shares_for(1.00, 0.48) == 2     # $0.96
    assert shares_for(1.00, 0.0) == 0


def test_one_fill_index_ignores_the_underdog_sleeve():
    """The migration's partial index must scope around 'underdog' exactly
    as it does 'manual' — the sleeve neither blocks nor is blocked."""
    sql = open("migrations/016_underdog_sleeve.sql").read()
    assert "NOT IN ('manual', 'underdog')" in sql
    assert "'cashed_out'" in sql
