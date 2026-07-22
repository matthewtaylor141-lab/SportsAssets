"""Average-cost position lifecycle math (spec §5 / §8 test requirements)."""

import pytest

from sportsassets.analytics.positions import Fill, Position, build_position, market_result


def test_buy_then_win_resolution():
    pos = build_position([Fill("BUY", 1000, 0.61)], payout=1.0)
    assert pos.resolved
    assert pos.shares == 0
    assert pos.realized_pnl == pytest.approx(1000 * (1.0 - 0.61))


def test_buy_then_loss_resolution():
    pos = build_position([Fill("BUY", 500, 0.30)], payout=0.0)
    assert pos.realized_pnl == pytest.approx(-150.0)


def test_average_cost_across_buys():
    pos = Position()
    pos.apply(Fill("BUY", 100, 0.40))
    pos.apply(Fill("BUY", 300, 0.60))
    assert pos.avg_cost == pytest.approx(0.55)
    pos.apply(Fill("SELL", 200, 0.70))
    assert pos.realized_pnl == pytest.approx(200 * (0.70 - 0.55))
    assert pos.shares == pytest.approx(200)
    # Selling does not change average cost of the remainder.
    assert pos.avg_cost == pytest.approx(0.55)


def test_partial_sell_then_resolution():
    pos = build_position(
        [Fill("BUY", 1000, 0.50, ts=1), Fill("SELL", 400, 0.80, ts=2)], payout=1.0
    )
    assert pos.realized_pnl == pytest.approx(400 * 0.30 + 600 * 0.50)


def test_oversell_is_clamped_not_shorted():
    pos = Position()
    pos.apply(Fill("BUY", 100, 0.50))
    pos.apply(Fill("SELL", 250, 0.60))
    assert pos.shares == 0
    assert pos.untracked_sells == pytest.approx(150)
    assert pos.realized_pnl == pytest.approx(100 * 0.10)


def test_sell_everything_resets_basis():
    pos = Position()
    pos.apply(Fill("BUY", 100, 0.50))
    pos.apply(Fill("SELL", 100, 0.55))
    pos.apply(Fill("BUY", 50, 0.20))
    assert pos.avg_cost == pytest.approx(0.20)


def test_resolution_idempotent():
    pos = build_position([Fill("BUY", 100, 0.40)], payout=1.0)
    pnl = pos.realized_pnl
    pos.resolve(1.0)
    pos.resolve(0.0)
    assert pos.realized_pnl == pnl


def test_open_exposure():
    pos = build_position([Fill("BUY", 1000, 0.61)])
    assert pos.open_exposure == pytest.approx(610.0)
    assert not pos.resolved


def test_hedged_multi_leg_market_is_scored_once():
    """Hedger buys YES and NO; W/L must come from the market total, not per leg."""
    yes = build_position([Fill("BUY", 1000, 0.60)], payout=1.0)  # +400
    no = build_position([Fill("BUY", 500, 0.35)], payout=0.0)  # -175
    total = yes.realized_pnl + no.realized_pnl
    assert market_result(total) == "win"  # package profitable despite losing leg
    assert market_result(yes.realized_pnl) == "win"
    assert market_result(no.realized_pnl) == "loss"  # per-leg would miscount — we never use it


def test_perfect_hedge_is_scratch():
    yes = build_position([Fill("BUY", 100, 0.50)], payout=1.0)
    no = build_position([Fill("BUY", 100, 0.50)], payout=0.0)
    assert market_result(yes.realized_pnl + no.realized_pnl) == "scratch"
