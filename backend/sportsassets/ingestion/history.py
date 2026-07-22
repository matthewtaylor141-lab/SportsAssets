"""Deep trade-history backfill via /activity time-window cursor pagination.

Method ported from the audited edge-engine reference pipeline (July 2026 API
constraints, measured on a 5.35M-fill account):
  - /trades caps offset at ~10,500 and ignores time params — unusable for
    full history. /activity (type=TRADE) honors start/end unix-second
    filters with limit up to 500.
  - History is split into ~4-day windows; within each window we cursor-
    paginate newest→oldest (end = oldest seen; when a full page yields no
    fresh rows, end = oldest - 1). Boundary duplicates collapse on the DB
    dedupe key.

Operational constraints (unchanged):
  - Background task, never blocks live polling; single-flight lock.
  - Bulk page inserts; progress heartbeats; rows marked source='backfill'
    (no notifications, excluded from latency metrics).
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import datetime, timezone

import httpx

from ..config import settings
from ..db import get_pool, heartbeat
from ..ratelimit import polite_get
from .poller import parse_data_api_trade

log = logging.getLogger(__name__)

PAGE_SIZE = 500
WINDOW_SECONDS = 4 * 86400

_INSERT = """
INSERT INTO trades (whale_id, tx_hash, asset, condition_id, side, outcome, outcome_index,
                    size, price, notional, market_title, market_slug, event_slug, sport,
                    ts, source, detected_at, enriched_at, dedupe_key)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,'backfill',$16,$16,$17)
ON CONFLICT (dedupe_key) DO NOTHING
"""


async def _sport_map() -> dict[str, str]:
    pool = await get_pool()
    rows = await pool.fetch("SELECT condition_id, sport FROM markets WHERE sport <> 'unclassified'")
    return {r["condition_id"]: r["sport"] for r in rows}


def _history_start_epoch() -> int:
    raw = settings().history_start_date
    try:
        return int(datetime.fromisoformat(raw).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        log.warning("bad HISTORY_START_DATE %r; defaulting to 2025-07-01", raw)
        return int(datetime(2025, 7, 1, tzinfo=timezone.utc).timestamp())


def _fill_key(ev) -> tuple:
    return (ev.tx_hash, ev.asset, ev.side, ev.price, ev.size, ev.ts_epoch)


async def _insert_page(pool, rows: list[tuple]) -> None:
    if rows:
        async with pool.acquire() as conn:
            await conn.executemany(_INSERT, rows)


async def backfill_whale_history(http: httpx.AsyncClient, whale: dict) -> int:
    """Import the wallet's full history over time windows. Returns fills scanned."""
    pool = await get_pool()
    sports = await _sport_map()
    now_dt = datetime.now(tz=timezone.utc)
    max_trades = settings().history_max_trades
    start_epoch = _history_start_epoch()
    now_epoch = int(_time.time()) + 3600

    scanned = 0
    window_start = start_epoch
    while window_start < now_epoch and scanned < max_trades:
        window_end = min(window_start + WINDOW_SECONDS, now_epoch)
        end_cursor = window_end
        boundary: set[tuple] = set()
        while scanned < max_trades:
            resp = await polite_get(
                http, "/activity",
                params={"user": whale["address"], "type": "TRADE", "limit": PAGE_SIZE,
                        "start": window_start, "end": end_cursor},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break

            rows: list[tuple] = []
            fresh = 0
            oldest = None
            for raw in batch:
                ev = parse_data_api_trade(raw, whale["id"], whale["username"])
                if not ev.tx_hash or ev.size <= 0:
                    continue
                oldest = ev.ts_epoch if oldest is None else min(oldest, ev.ts_epoch)
                if _fill_key(ev) in boundary:
                    continue
                fresh += 1
                rows.append((
                    ev.whale_id, ev.tx_hash, str(ev.asset), ev.condition_id, ev.side,
                    ev.outcome, ev.outcome_index, ev.size, ev.price, ev.notional,
                    ev.market_title, ev.market_slug, ev.event_slug,
                    sports.get(ev.condition_id or "", "unclassified"),
                    datetime.fromtimestamp(ev.ts_epoch, tz=timezone.utc), now_dt, ev.dedupe_key,
                ))
            await _insert_page(pool, rows)
            scanned += fresh
            await heartbeat(
                "backfill", "running",
                {"whale": whale["username"] or whale["address"], "scanned": scanned,
                 "window": datetime.fromtimestamp(window_start, tz=timezone.utc).date().isoformat()},
            )
            if len(batch) < PAGE_SIZE or oldest is None:
                break
            if fresh == 0:
                end_cursor = oldest - 1  # >500 fills in one second — step past it
                boundary = set()
            else:
                end_cursor = oldest
                boundary = {
                    _fill_key(parse_data_api_trade(r, whale["id"], whale["username"]))
                    for r in batch
                    if int(r.get("timestamp", 0)) == oldest
                }
            if end_cursor <= window_start:
                break
        window_start += WINDOW_SECONDS

    if scanned >= max_trades:
        log.warning("backfill for %s hit HISTORY_MAX_TRADES=%s — raise it (and the DB plan) "
                    "for complete history", whale["address"], max_trades)
        await heartbeat("backfill", "capped",
                        {"whale": whale["username"] or whale["address"], "scanned": scanned,
                         "hint": f"HISTORY_MAX_TRADES={max_trades} reached"})

    await pool.execute("UPDATE whales SET history_backfilled=TRUE WHERE id=$1", whale["id"])
    log.info("history backfill for %s: %s fills imported",
             whale["username"] or whale["address"], scanned)
    return scanned


_BACKFILL_LOCK = asyncio.Lock()


async def backfill_pending() -> int:
    """Backfill every active whale that hasn't had a history import yet.

    Single-flight: if a pass is already running in this process, additional
    callers return immediately instead of doubling API traffic.
    """
    if _BACKFILL_LOCK.locked():
        return 0
    async with _BACKFILL_LOCK:
        return await _backfill_pending_inner()


async def _backfill_pending_inner() -> int:
    pool = await get_pool()
    whales = await pool.fetch(
        "SELECT id, address, username FROM whales "
        "WHERE active AND NOT banned AND NOT history_backfilled"
    )
    if not whales:
        return 0
    total = 0
    async with httpx.AsyncClient(base_url=settings().data_api_base, timeout=30) as http:
        for whale in whales:
            try:
                total += await backfill_whale_history(http, dict(whale))
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("history backfill failed for %s: %s (will retry next cycle)",
                            whale["address"], exc)
                await heartbeat("backfill", "error", {"whale": whale["address"], "error": str(exc)})
    await heartbeat("backfill", "ok", {"scanned": total})
    return total
