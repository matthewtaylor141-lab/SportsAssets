"""Desk price-history proxy (wave-2 contract 2026-08-22).

One read path for the desk's charts on both venues, normalized to one
shape: {venue, id, hours, points: [{t: epoch_int, p: float 0..1}]},
ascending, at most MAX_POINTS points (evenly downsampled when the venue
returns more). Charts are decoration on top of the desk — a venue error
degrades to empty points (HTTP 200 with an error field), never a broken
desk. A 60s in-process TTL cache per (venue, id, hours) keeps chart
polling off the venues. Venue HTTP is sync httpx run in a thread, 8s
timeout — the same discipline as every other desk read.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from ..config import settings

log = logging.getLogger(__name__)

# Same default as app.py's public Kalshi base (module kept import-free of
# app.py — the route there imports us, never the other way around).
KALSHI_PUBLIC_API = os.environ.get(
    "KALSHI_PUBLIC_API", "https://api.elections.kalshi.com/trade-api/v2")

VENUES = ("polymarket-us", "kalshi")
MAX_POINTS = 500
CACHE_TTL_S = 60.0
_cache: dict[tuple[str, str, int], tuple[float, dict]] = {}


def downsample(points: list[dict], cap: int = MAX_POINTS) -> list[dict]:
    """Evenly thin an ascending series to <= cap points, keeping both
    endpoints (a chart missing its live edge reads as a stale chart)."""
    n = len(points)
    if n <= cap:
        return points
    step = (n - 1) / (cap - 1)
    return [points[round(i * step)] for i in range(cap)]


# ── Polymarket (global CLOB public prices-history) ───────────────────


def _pm_interval(hours: int) -> tuple[str, int]:
    """CLOB (interval, fidelity-minutes) for a lookback. NOTE the venue's
    '1m' means one MONTH, not one minute."""
    if hours <= 1:
        return "1h", 1
    if hours <= 6:
        return "6h", 1
    if hours <= 24:
        return "1d", 5
    if hours <= 168:
        return "1w", 30
    return "1m", 60


def _parse_pm_history(raw: dict, cutoff: float) -> list[dict]:
    """CLOB history rows -> points. Prices arrive already in dollars
    0-1 — passthrough, dropping malformed or out-of-range rows and
    anything older than the requested window (the venue's interval
    buckets are coarser than our hours)."""
    pts: list[dict] = []
    for row in (raw or {}).get("history") or []:
        try:
            t, p = int(row["t"]), float(row["p"])
        except (KeyError, TypeError, ValueError):
            continue
        if t >= cutoff and 0.0 <= p <= 1.0:
            pts.append({"t": t, "p": p})
    pts.sort(key=lambda x: x["t"])
    return pts


def fetch_pm_history(token_id: str, hours: int) -> list[dict]:
    """Sync (callers run it in a thread): the public CLOB price series
    for one token."""
    import httpx

    interval, fidelity = _pm_interval(hours)
    with httpx.Client(base_url=settings().clob_api_base,
                      timeout=8) as client:
        resp = client.get("/prices-history", params={
            "market": token_id, "interval": interval,
            "fidelity": fidelity})
        resp.raise_for_status()
        raw = resp.json() or {}
    return _parse_pm_history(raw, cutoff=time.time() - hours * 3600)


# ── Kalshi (public candlesticks) ─────────────────────────────────────


def _kalshi_period_minutes(hours: int) -> int:
    """1m candles inside a day (1440 max rows, downsampled to 500),
    1h beyond — the venue only offers 1/60/1440."""
    return 1 if hours <= 24 else 60


def _kalshi_close(candle: dict) -> float | None:
    """Candlestick close yes-price -> dollars 0-1. Same tolerance as
    _kcents in app.py: the venue migrated int-cent fields to
    string-dollar '*_dollars' twins — accept either, cents /100."""
    for key in ("price", "yes_bid", "yes_ask"):
        blk = candle.get(key)
        if not isinstance(blk, dict):
            continue
        v = blk.get("close_dollars")
        try:
            if v is not None and str(v).strip():
                f = float(v)
                return f if 0 <= f <= 1 else None
        except (TypeError, ValueError):
            pass
        try:
            c = blk.get("close")
            if c is not None:
                f = float(c) / 100.0
                return f if 0 <= f <= 1 else None
        except (TypeError, ValueError):
            pass
    return None


def _parse_kalshi_candles(raw: dict) -> list[dict]:
    pts: list[dict] = []
    for c in (raw or {}).get("candlesticks") or []:
        try:
            t = int(c.get("end_period_ts"))
        except (TypeError, ValueError):
            continue
        p = _kalshi_close(c)
        if p is not None:
            pts.append({"t": t, "p": p})
    pts.sort(key=lambda x: x["t"])
    return pts


def fetch_kalshi_history(ticker: str, hours: int) -> list[dict]:
    """Sync (callers run it in a thread): public candlesticks for one
    ticker. The series is the ticker prefix before the first '-'."""
    import httpx

    series = ticker.split("-", 1)[0]
    end_ts = int(time.time())
    with httpx.Client(base_url=KALSHI_PUBLIC_API, timeout=8) as client:
        resp = client.get(
            f"/series/{series}/markets/{ticker}/candlesticks",
            params={"start_ts": end_ts - hours * 3600, "end_ts": end_ts,
                    "period_interval": _kalshi_period_minutes(hours)})
        resp.raise_for_status()
        raw = resp.json() or {}
    return _parse_kalshi_candles(raw)


# ── Entry point (the app.py route is a thin wrapper over this) ───────


async def _pm_token_for_slug(slug: str) -> str | None:
    """us_slug -> CLOB yes-token, from data we already hold.

    1. gamma metadata (markets.slug -> market_tokens, outcome_index 0 —
       the venue's boards mirror the global markets for sports, so the
       slugs usually align);
    2. the order ledger (anything the desk or the copy engine ever
       traded on the slug carries its token).
    None when neither knows — the chart stays honestly empty."""
    from ..db import get_pool

    try:
        pool = await get_pool()
        tok = await pool.fetchval(
            """
            SELECT mt.token_id
            FROM markets m JOIN market_tokens mt USING (condition_id)
            WHERE m.slug = $1
            ORDER BY mt.outcome_index ASC LIMIT 1
            """, slug)
        if tok:
            return str(tok)
        tok = await pool.fetchval(
            """
            SELECT asset FROM live_orders
            WHERE us_market_slug = $1 AND asset IS NOT NULL
              AND asset ~ '^[0-9]+$'
            ORDER BY id DESC LIMIT 1
            """, slug)
        return str(tok) if tok else None
    except Exception:  # noqa: BLE001 — resolution is best-effort
        return None


async def history(venue: str, id: str, hours: int,
                  now: float | None = None) -> dict:
    """The contract payload for one (venue, id, hours). Never raises:
    a venue error is an empty chart, not a broken desk. Errors are
    cached too — a down venue must not eat an 8s timeout per poll."""
    hours = max(1, min(336, int(hours)))
    ts = time.time() if now is None else now
    base = {"venue": venue, "id": id, "hours": hours}
    if venue not in VENUES:
        return {**base, "points": [], "error": "unknown venue"}
    key = (venue, id, hours)
    hit = _cache.get(key)
    if hit is not None and ts - hit[0] < CACHE_TTL_S:
        return hit[1]
    fetch = (fetch_pm_history if venue == "polymarket-us"
             else fetch_kalshi_history)
    try:
        chart_id = id
        if venue == "polymarket-us" and not id.isdigit():
            # SLUG -> CLOB TOKEN BRIDGE (owner 2026-08-22: "no chart on
            # any per-market view"): venue-native PM boards address
            # markets by us_slug, but prices-history wants the global
            # CLOB token. Resolve through our own data — gamma metadata
            # first, then any order we ever placed on the slug. A slug
            # neither source knows keeps the quiet empty state.
            token = await _pm_token_for_slug(id)
            if token is None:
                payload = {**base, "points": [],
                           "error": "no price history for this market"}
                _cache[key] = (ts, payload)
                return payload
            chart_id = token
        pts = await asyncio.to_thread(fetch, chart_id, hours)
        payload = {**base, "points": downsample(pts)}
    except Exception as exc:  # noqa: BLE001 — charts degrade, desks never break
        log.warning("desk history %s %s (%sh) failed: %s",
                    venue, id, hours, exc)
        payload = {**base, "points": [],
                   "error": "venue history unavailable"}
    _cache[key] = (ts, payload)
    if len(_cache) > 512:  # bounded: sweep expired entries
        dead = [k for k, (t0, _) in _cache.items()
                if ts - t0 >= CACHE_TTL_S]
        for k in dead:
            _cache.pop(k, None)
    return payload
