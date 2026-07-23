"""Copy-trade feasibility math: residual book → achievable prices → residual ROI."""

import pytest

from sportsassets.copy_probe import compute_book_metrics

EDGE = 0.023


def test_no_slippage_preserves_full_edge():
    # $10k available at exactly his price → residual ROI == the assumed edge.
    m = compute_book_metrics([(0.50, 20000)], his_price=0.50, assumed_edge=EDGE)
    assert m["slippage_cents"] == 0
    assert m["fillable_5k"] and m["vwap_5k"] == 0.50
    assert m["residual_roi_5k"] == pytest.approx(EDGE, abs=1e-6)


def test_one_cent_slippage_halves_the_edge_at_midprice():
    # Buy at 51c what he got at 50c: residual = 1.023*50/51 - 1 ≈ +0.3%.
    m = compute_book_metrics([(0.51, 20000)], his_price=0.50, assumed_edge=EDGE)
    assert m["slippage_cents"] == pytest.approx(1.0)
    assert m["residual_roi_1k"] == pytest.approx(1.023 * 0.50 / 0.51 - 1, abs=1e-6)
    assert 0 < m["residual_roi_1k"] < EDGE


def test_two_cents_slippage_kills_the_edge_at_midprice():
    m = compute_book_metrics([(0.52, 20000)], his_price=0.50, assumed_edge=EDGE)
    assert m["residual_roi_1k"] < 0  # 1.023*50/52-1 ≈ -1.6%


def test_depth_walk_vwap():
    # $1k: 500 at 50c + 500 at 52c → vwap ≈ 50.98c.
    asks = [(0.50, 1000), (0.52, 5000)]  # $500 then $2600 available
    m = compute_book_metrics(asks, his_price=0.50, assumed_edge=EDGE)
    assert m["fillable_1k"]
    expected_shares = 500 / 0.50 + 500 / 0.52
    assert m["vwap_1k"] == pytest.approx(1000 / expected_shares, abs=1e-4)
    assert not m["fillable_5k"]  # only $3.1k total on the book
    assert m["residual_roi_5k"] is None


def test_empty_book():
    m = compute_book_metrics([], his_price=0.50, assumed_edge=EDGE)
    assert m["best_ask"] is None and m["slippage_cents"] is None
