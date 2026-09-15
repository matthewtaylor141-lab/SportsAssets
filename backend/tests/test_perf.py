"""Daily P&L grouping, drawdown, and summary stats."""

from datetime import datetime, timezone

import pytest

from sportsassets.analytics.perf import group_daily, max_drawdown, summarize


def ts(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=timezone.utc)


def test_group_daily():
    days = group_daily(
        realizations=[(ts(1), 100.0), (ts(1, 20), -30.0), (ts(3), 50.0)],
        trades=[(ts(1), 1000.0), (ts(2), 500.0), (ts(3), 200.0)],
    )
    assert [d["date"] for d in days] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert days[0] == {"date": "2026-07-01", "pnl": 70.0, "volume": 1000.0, "trades": 1}
    assert days[1]["pnl"] == 0.0 and days[1]["trades"] == 1
    assert days[2]["pnl"] == 50.0


def test_max_drawdown():
    # +100, +200 (peak 300), -250 (trough 50), +100
    events = [(ts(1), 100.0), (ts(2), 200.0), (ts(3), -250.0), (ts(4), 100.0)]
    dd = max_drawdown(events)
    assert dd["max_drawdown"] == pytest.approx(250.0)
    assert dd["peak"] == pytest.approx(300.0)
    assert dd["trough"] == pytest.approx(50.0)
    assert dd["trough_ts"].startswith("2026-07-03")


def test_max_drawdown_monotonic_up_is_zero():
    assert max_drawdown([(ts(1), 10.0), (ts(2), 20.0)])["max_drawdown"] == 0.0
    assert max_drawdown([])["max_drawdown"] == 0.0


def test_summarize():
    s = summarize(
        realizations=[(ts(1), 400.0), (ts(2), -100.0)],
        trades=[(ts(1), 1000.0), (ts(2), 500.0)],
        buy_notional=1200.0,
    )
    assert s["realized_pnl"] == 300.0
    assert s["volume_traded"] == 1500.0
    assert s["pct_earned"] == pytest.approx(0.25)
    assert s["trade_count"] == 2
    assert s["max_drawdown"] == pytest.approx(100.0)
