"""LIVE beta order planning: price protection and sizing caps (pure math)."""

import pytest

from sportsassets.live_executor import plan_order


def test_limit_is_his_price_plus_slippage_cap():
    limit, usd, shares = plan_order(0.52, 41600, 0.001, 25.0, 1.0)
    assert limit == pytest.approx(0.53)
    assert usd == pytest.approx(41.6 if 41.6 < 25 else 25.0)  # $1 per $1k, capped
    assert usd == 25.0
    assert shares == pytest.approx(round(25.0 / 0.53, 2))


def test_dollar_per_thousand_ratio():
    limit, usd, shares = plan_order(0.50, 4000, 0.001, 25.0, 1.0)
    assert usd == 4.0  # $4 on his $4k fill
    assert limit == pytest.approx(0.51)


def test_sub_dollar_orders_produce_zero_intent():
    # His $500 fill -> $0.50 clip; executor skips below $1 minimum.
    _, usd, _ = plan_order(0.50, 500, 0.001, 25.0, 1.0)
    assert usd == 0.5


def test_limit_never_exceeds_99c():
    limit, _, _ = plan_order(0.985, 1000, 0.10, 25.0, 2.0)
    assert limit == 0.99
