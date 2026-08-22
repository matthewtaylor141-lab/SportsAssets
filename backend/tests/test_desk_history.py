"""Desk price-history proxy (wave-2 2026-08-22): normalization to the
contract shape (kalshi cents -> dollars, PM passthrough), even
downsample to <= 500 points, 60s TTL cache, and degrade-to-empty on
venue error — charts degrade, desks never break."""

import pytest

from sportsassets.api import desk_history as dh


@pytest.fixture(autouse=True)
def _clean_cache():
    dh._cache.clear()
    yield
    dh._cache.clear()


# ── Normalization ────────────────────────────────────────────────────


def test_kalshi_cents_close_maps_to_float():
    raw = {"candlesticks": [
        {"end_period_ts": 200, "price": {"close": 55}},
        {"end_period_ts": 100, "price": {"close": 7}},
    ]}
    pts = dh._parse_kalshi_candles(raw)
    assert pts == [{"t": 100, "p": 0.07}, {"t": 200, "p": 0.55}]


def test_kalshi_dollars_twin_wins_over_cents():
    raw = {"candlesticks": [
        {"end_period_ts": 100,
         "price": {"close": 55, "close_dollars": "0.56"}},
    ]}
    assert dh._parse_kalshi_candles(raw) == [{"t": 100, "p": 0.56}]


def test_kalshi_falls_back_to_yes_bid_and_drops_junk():
    raw = {"candlesticks": [
        {"end_period_ts": 100, "yes_bid": {"close": 40}},
        {"end_period_ts": 200},                      # no price at all
        {"end_period_ts": "nope", "price": {"close": 50}},  # bad ts
        {"end_period_ts": 300, "price": {"close": 250}},    # out of range
    ]}
    assert dh._parse_kalshi_candles(raw) == [{"t": 100, "p": 0.40}]


def test_pm_prices_pass_through_sorted_and_clamped():
    raw = {"history": [
        {"t": 20, "p": 0.42},
        {"t": 10, "p": 0.40},
        {"t": 30, "p": 1.7},          # out of range -> dropped
        {"t": 40, "p": "bad"},        # malformed -> dropped
        {"t": 5, "p": 0.5},           # before cutoff -> dropped
    ]}
    pts = dh._parse_pm_history(raw, cutoff=10)
    assert pts == [{"t": 10, "p": 0.40}, {"t": 20, "p": 0.42}]


# ── Downsampling ─────────────────────────────────────────────────────


def test_downsample_caps_at_500_and_keeps_endpoints():
    pts = [{"t": i, "p": 0.5} for i in range(1440)]
    out = dh.downsample(pts)
    assert len(out) == 500
    assert out[0]["t"] == 0 and out[-1]["t"] == 1439
    ts = [x["t"] for x in out]
    assert ts == sorted(set(ts))  # ascending, no duplicates


def test_downsample_leaves_small_series_alone():
    pts = [{"t": i, "p": 0.5} for i in range(500)]
    assert dh.downsample(pts) is pts


# ── history(): cache TTL and error degradation ───────────────────────


async def test_history_caches_for_60s_then_refetches(monkeypatch):
    calls = []

    def fake_fetch(ticker, hours):
        calls.append(ticker)
        return [{"t": 1, "p": 0.5}]

    monkeypatch.setattr(dh, "fetch_kalshi_history", fake_fetch)
    r1 = await dh.history("kalshi", "KXMLBGAME-X-Y", 24, now=1000.0)
    r2 = await dh.history("kalshi", "KXMLBGAME-X-Y", 24, now=1059.0)
    assert r1 == r2 == {"venue": "kalshi", "id": "KXMLBGAME-X-Y",
                        "hours": 24, "points": [{"t": 1, "p": 0.5}]}
    assert len(calls) == 1
    await dh.history("kalshi", "KXMLBGAME-X-Y", 24, now=1061.0)
    assert len(calls) == 2


async def test_history_cache_is_per_venue_id_hours(monkeypatch):
    calls = []

    def fake_fetch(ident, hours):
        calls.append((ident, hours))
        return []

    monkeypatch.setattr(dh, "fetch_kalshi_history", fake_fetch)
    await dh.history("kalshi", "T-1", 24, now=1000.0)
    await dh.history("kalshi", "T-1", 48, now=1000.0)
    await dh.history("kalshi", "T-2", 24, now=1000.0)
    assert calls == [("T-1", 24), ("T-1", 48), ("T-2", 24)]


async def test_history_venue_error_degrades_to_empty(monkeypatch):
    def boom(token, hours):
        raise RuntimeError("venue down")

    monkeypatch.setattr(dh, "fetch_pm_history", boom)
    r = await dh.history("polymarket-us", "12345", 24, now=1000.0)
    assert r["points"] == [] and r["error"]
    assert r["venue"] == "polymarket-us" and r["hours"] == 24


async def test_history_unknown_venue_is_empty_not_500():
    r = await dh.history("bovada", "x", 24)
    assert r["points"] == [] and r["error"] == "unknown venue"


async def test_history_clamps_hours(monkeypatch):
    seen = {}

    def fake_fetch(token, hours):
        seen["hours"] = hours
        return []

    monkeypatch.setattr(dh, "fetch_pm_history", fake_fetch)
    r = await dh.history("polymarket-us", "12345", 9999, now=1000.0)
    assert seen["hours"] == 336 and r["hours"] == 336
