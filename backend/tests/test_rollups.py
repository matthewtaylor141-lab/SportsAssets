"""Windowed rollup aggregation (pure part of the analytics engine)."""

from datetime import datetime, timedelta, timezone

import pytest

from sportsassets.analytics.engine import PositionState, Realization, compute_rollups
from sportsassets.analytics.positions import Fill, Position, build_position

NOW = datetime(2026, 7, 22, tzinfo=timezone.utc)


def state(whale_id, cid, sport, fills, payout=None, realized_ts=None, token="t1"):
    pos = build_position(fills, payout=payout)
    st = PositionState(
        whale_id=whale_id, condition_id=cid, token_id=token, outcome="Yes",
        outcome_index=0, sport=sport, position=pos,
    )
    ts = realized_ts or NOW
    if abs(pos.realized_pnl) > 1e-9:
        st.realizations = [Realization(ts=ts, amount=pos.realized_pnl)]
    st.buys = [(ts, f.size * f.price) for f in fills if f.side == "BUY"]
    st.first_ts = st.last_ts = ts
    return st


def _row(rollups, whale_id, sport, window):
    for r in rollups:
        if (r["whale_id"], r["sport"], r["window"]) == (whale_id, sport, window):
            return r
    return None


def test_win_loss_and_roi():
    states = [
        state(1, "c1", "NFL", [Fill("BUY", 1000, 0.60)], payout=1.0),  # +400 on 600
        state(1, "c2", "NFL", [Fill("BUY", 500, 0.40)], payout=0.0, token="t2"),  # -200 on 200
    ]
    r = _row(compute_rollups(states, now=NOW), 1, "NFL", "all")
    assert r["markets_traded"] == 2
    assert r["wins"] == 1 and r["losses"] == 1
    assert r["win_pct"] == pytest.approx(0.5)
    assert r["realized_pnl"] == pytest.approx(200.0)
    assert r["roi"] == pytest.approx(200.0 / 800.0)


def test_hedged_market_counts_once_from_package_total():
    yes = state(1, "c1", "NBA", [Fill("BUY", 1000, 0.60)], payout=1.0, token="yes")
    no = state(1, "c1", "NBA", [Fill("BUY", 500, 0.35)], payout=0.0, token="no")
    r = _row(compute_rollups([yes, no], now=NOW), 1, "NBA", "all")
    assert r["markets_traded"] == 1
    assert r["wins"] == 1 and r["losses"] == 0  # net +225 → one Win, not one of each


def test_window_excludes_old_realizations():
    old = NOW - timedelta(days=45)
    states = [state(1, "c1", "MLB", [Fill("BUY", 100, 0.50)], payout=1.0, realized_ts=old)]
    all_row = _row(compute_rollups(states, now=NOW), 1, "MLB", "all")
    assert all_row["wins"] == 1
    assert _row(compute_rollups(states, now=NOW), 1, "MLB", "30d") is None


def test_open_position_counts_exposure_not_wl():
    states = [state(1, "c1", "NHL", [Fill("BUY", 200, 0.50)])]
    r = _row(compute_rollups(states, now=NOW), 1, "NHL", "all")
    assert r["wins"] == 0 and r["losses"] == 0
    assert r["open_exposure"] == pytest.approx(100.0)
    assert r["markets_traded"] == 1
