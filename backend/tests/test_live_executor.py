"""LIVE beta order planning: price protection and sizing caps (pure math)."""

import pytest

from sportsassets.live_executor import plan_order


def test_limit_is_his_price_plus_slippage_cap():
    limit, usd, shares = plan_order(0.52, 41600, 0.10, 25.0, 1.0)
    assert limit == pytest.approx(0.53)
    assert usd == 25.0  # 10% of 41600 = 4160, capped at per-fill 25
    assert shares == pytest.approx(round(25.0 / 0.53, 2))


def test_small_whale_trade_uses_ratio():
    limit, usd, shares = plan_order(0.50, 100, 0.10, 25.0, 1.0)
    assert usd == 10.0  # 10% of $100, below the cap
    assert limit == pytest.approx(0.51)


def test_limit_never_exceeds_99c():
    limit, _, _ = plan_order(0.985, 1000, 0.10, 25.0, 2.0)
    assert limit == 0.99
