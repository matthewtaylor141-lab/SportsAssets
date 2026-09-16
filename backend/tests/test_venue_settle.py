"""Venue-truth settlement allocation (owner emergency 2026-08-23).

The settlement sweep and the restatement both split one market's
venue-reported realized P&L across our rows on that market. The split
must sum EXACTLY to the venue figure (the whole incident was the record
disagreeing with the venue), be cost-weighted, and survive zero-cost
rows.
"""

from sportsassets.analytics.engine import allocate_venue_pnl


def _rows(*pairs):
    return [{"id": i, "filled_usd": usd} for i, usd in pairs]


def test_single_row_takes_full_target():
    out = allocate_venue_pnl(-87.71, _rows((1, 65.2)))
    assert out == {1: -87.71}


def test_pro_rata_by_cost_sums_exact():
    out = allocate_venue_pnl(100.0, _rows((1, 75.0), (2, 25.0)))
    assert out[1] == 75.0 and out[2] == 25.0
    assert round(sum(out.values()), 4) == 100.0


def test_rounding_remainder_lands_on_last_row():
    out = allocate_venue_pnl(10.0, _rows((1, 1.0), (2, 1.0), (3, 1.0)))
    assert round(sum(out.values()), 4) == 10.0
    assert out[1] == round(10.0 / 3, 4)


def test_zero_cost_rows_split_equally():
    out = allocate_venue_pnl(-9.0, _rows((1, 0.0), (2, 0.0)))
    assert round(sum(out.values()), 4) == -9.0
    assert out[1] == -4.5 and out[2] == -4.5


def test_negative_target_pro_rata():
    out = allocate_venue_pnl(-206.09, _rows((1, 50.0), (2, 150.0)))
    assert round(sum(out.values()), 4) == -206.09
    assert out[1] == round(-206.09 * 0.25, 4)


def test_venue_cent_precision_preserved():
    # 3-way split of an awkward cent amount must not drift.
    out = allocate_venue_pnl(0.01, _rows((1, 33.0), (2, 33.0), (3, 34.0)))
    assert round(sum(out.values()), 4) == 0.01
