"""Windowed rollup aggregation (pure part of the analytics engine).

States are built the way rebuild_positions builds them since 2026-09-05:
events are bucketed into per-window scalars (PositionState.windows) at a
fixed reference time (`as_of`), not kept as per-fill lists.
"""

from datetime import datetime, timedelta, timezone

import pytest

from sportsassets.analytics.engine import WINDOWS, PositionState, WindowAgg, compute_rollups
from sportsassets.analytics.positions import Fill, build_position

NOW = datetime(2026, 7, 22, tzinfo=timezone.utc)


def state(whale_id, cid, sport, fills, payout=None, realized_ts=None, token="t1"):
    pos = build_position(fills, payout=payout)
    st = PositionState(
        whale_id=whale_id, condition_id=cid, token_id=token, outcome="Yes",
        outcome_index=0, sport=sport, position=pos, as_of=NOW,
    )
    ts = realized_ts or NOW
    st.record_realization(ts, pos.realized_pnl)  # dust-gated inside, as the replay is
    for f in fills:
        if f.side == "BUY":
            st.record_buy(ts, f.size * f.price)
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


def test_event_exactly_on_a_cutoff_is_inside_that_window():
    """The window test is `ts >= cutoff` — an event AT the 7d edge belongs
    to 7d, 30d and all; one a microsecond earlier only to 30d and all."""
    on_edge = NOW - WINDOWS["7d"]
    st = state(1, "c1", "NFL", [Fill("BUY", 100, 0.50)], payout=1.0, realized_ts=on_edge)
    rollups = compute_rollups([st], now=NOW)
    assert {r["window"] for r in rollups} == {"7d", "30d", "all"}
    assert _row(rollups, 1, "NFL", "7d")["wins"] == 1
    assert _row(rollups, 1, "NFL", "7d")["notional"] == pytest.approx(50.0)

    just_before = on_edge - timedelta(microseconds=1)
    st = state(1, "c1", "NFL", [Fill("BUY", 100, 0.50)], payout=1.0, realized_ts=just_before)
    rollups = compute_rollups([st], now=NOW)
    assert {r["window"] for r in rollups} == {"30d", "all"}


def test_rollup_refuses_a_now_that_is_not_the_replays():
    """The buckets were cut at as_of; a rollup at any other instant would
    mix two cutoffs in one row (buckets at one time, traded_in_window at
    another). Loud, not subtly wrong."""
    st = state(1, "c1", "NFL", [Fill("BUY", 100, 0.50)], payout=1.0)
    with pytest.raises(ValueError, match="bucketed"):
        compute_rollups([st], now=NOW + timedelta(minutes=1))


def test_now_defaults_to_the_states_reference_time():
    old = NOW - timedelta(days=10)
    st = state(1, "c1", "NFL", [Fill("BUY", 100, 0.50)], payout=1.0, realized_ts=old)
    rollups = compute_rollups([st])  # no `now`: as_of is the clock
    assert {r["window"] for r in rollups} == {"30d", "all"}


def test_a_state_holds_no_per_event_list():
    """The retention that OOM-killed the workers (2026-09-05): the state
    kept one object per fill. Now it keeps a few scalars per window,
    however many events it books; `windows` reads them back."""
    st = PositionState(whale_id=1, condition_id="c1", token_id="t1", outcome="Yes",
                       outcome_index=0, sport="NFL", position=build_position([]), as_of=NOW)
    for i in range(10_000):
        st.record_buy(NOW - timedelta(seconds=i), 1.0)
        st.record_realization(NOW - timedelta(seconds=i), 0.5)
    assert not hasattr(st, "realizations") and not hasattr(st, "buys")
    assert set(st.windows) == set(WINDOWS)
    assert all(isinstance(w, WindowAgg) for w in st.windows.values())
    assert st.windows["all"].notional == pytest.approx(10_000.0)
    assert st.windows["all"].realized == pytest.approx(5_000.0)
    assert st.windows["7d"].events is True
    assert st.window("7d") == st.windows["7d"]


def test_window_sums_like_builtin_sum():
    """The accumulator mirrors CPython's sum() (Neumaier-compensated from
    3.12, the production image): 10 × 0.1 lands on 1.0 exactly, which a
    naive running total does not."""
    st = PositionState(whale_id=1, condition_id="c1", token_id="t1", outcome="Yes",
                       outcome_index=0, sport="NFL", position=build_position([]), as_of=NOW)
    for _ in range(10):
        st.record_buy(NOW, 0.1)
        st.record_realization(NOW, 0.1)
    assert st.window("all") == WindowAgg(realized=1.0, events=True, notional=1.0)
    assert st.window("7d").notional == 1.0 and st.window("7d").realized == 1.0


def test_dust_is_not_an_event():
    st = PositionState(whale_id=1, condition_id="c1", token_id="t1", outcome="Yes",
                       outcome_index=0, sport="NFL", position=build_position([]), as_of=NOW)
    st.record_realization(NOW, 1e-12)
    assert st.windows["all"].events is False and st.windows["all"].realized == 0.0
