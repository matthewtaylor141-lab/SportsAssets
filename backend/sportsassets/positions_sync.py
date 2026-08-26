"""Live position snapshots from the Data API.

`/positions?user={wallet}` returns the wallet's current holdings — including
positions opened before we started tracking — so profiles and the Markets
view always show each whale's full live book. The snapshot is replaced
atomically per whale on every sync cycle.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import settings
from .db import get_pool
from .ratelimit import polite_get

log = logging.getLogger(__name__)

# 2000 -> 6000 (2026-08-26), in lockstep with
# whale_exits.POSITIONS_MAX. See the note there.
MAX_POSITIONS_PER_WALLET = 6000


def parse_api_position(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one Data-API position record; None if unusable/empty."""
    asset = raw.get("asset") or raw.get("tokenId") or raw.get("token_id")
    try:
        size = float(raw.get("size") or 0)
    except (TypeError, ValueError):
        return None
    if not asset or size <= 0:
        return None

    def num(*keys: str) -> float | None:
        for k in keys:
            v = raw.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return None

    return {
        "asset": str(asset),
        "condition_id": raw.get("conditionId") or raw.get("condition_id"),
        "outcome": raw.get("outcome"),
        "outcome_index": raw.get("outcomeIndex") if isinstance(raw.get("outcomeIndex"), int) else None,
        "size": size,
        "avg_price": num("avgPrice", "avg_price"),
        "cur_price": num("curPrice", "currentPrice", "cur_price"),
        "initial_value": num("initialValue"),
        "current_value": num("currentValue"),
        "cash_pnl": num("cashPnl"),
        "percent_pnl": num("percentPnl"),
        "redeemable": bool(raw.get("redeemable")),
        "title": raw.get("title"),
        "slug": raw.get("slug"),
        "event_slug": raw.get("eventSlug") or raw.get("event_slug"),
    }


async def sync_whale_positions(http: httpx.AsyncClient, whale: dict) -> int:
    """Fetch + replace one whale's snapshot. Returns position count."""
    parsed: list[dict] = []
    offset = 0
    while offset < MAX_POSITIONS_PER_WALLET:
        resp = await polite_get(
            http, "/positions", params={"user": whale["address"], "limit": 100, "offset": offset}
        )
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        for raw in batch:
            p = parse_api_position(raw)
            if p:
                parsed.append(p)
        if len(batch) < 100:
            break
        offset += 100

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM api_positions WHERE whale_id=$1", whale["id"])
            for p in parsed:
                await conn.execute(
                    """
                    INSERT INTO api_positions
                        (whale_id, asset, condition_id, outcome, outcome_index, size, avg_price,
                         cur_price, initial_value, current_value, cash_pnl, percent_pnl,
                         redeemable, title, slug, event_slug, fetched_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,now())
                    ON CONFLICT (whale_id, asset) DO UPDATE SET
                        size=EXCLUDED.size, avg_price=EXCLUDED.avg_price,
                        cur_price=EXCLUDED.cur_price, current_value=EXCLUDED.current_value,
                        cash_pnl=EXCLUDED.cash_pnl, percent_pnl=EXCLUDED.percent_pnl,
                        redeemable=EXCLUDED.redeemable, fetched_at=now()
                    """,
                    whale["id"], p["asset"], p["condition_id"], p["outcome"], p["outcome_index"],
                    p["size"], p["avg_price"], p["cur_price"], p["initial_value"],
                    p["current_value"], p["cash_pnl"], p["percent_pnl"], p["redeemable"],
                    p["title"], p["slug"], p["event_slug"],
                )
    return len(parsed)


_last_sync: float = 0.0


async def sync_all_positions(force: bool = False) -> dict[str, int]:
    """One snapshot pass over every tracked whale.

    Rate-limited to every POSITIONS_SYNC_INTERVAL_SECONDS (default 5 min) —
    open-position freshness is worth minutes, not a rate-limit ban.
    """
    global _last_sync
    import time as _time

    now = _time.monotonic()
    if not force and _last_sync and now - _last_sync < settings().positions_sync_interval_seconds:
        return {}
    _last_sync = now
    pool = await get_pool()
    whales = await pool.fetch("SELECT id, address, username FROM whales WHERE active AND NOT banned")
    counts: dict[str, int] = {}
    async with httpx.AsyncClient(base_url=settings().data_api_base, timeout=20) as http:
        for whale in whales:
            try:
                counts[whale["address"]] = await sync_whale_positions(http, dict(whale))
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("position sync failed for %s: %s", whale["address"], exc)
    return counts
