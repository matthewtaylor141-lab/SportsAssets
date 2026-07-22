"""Deep trade-history backfill.

When a whale joins the roster, import their ENTIRE past trade ledger from the
Data API (paged, bounded) so settled performance metrics — per-sport P&L,
W-L records, equity curve, trade/price history — are complete immediately.

Design constraints (learned in production):
- Runs as a BACKGROUND task — must never block live polling.
- Bulk inserts (one executemany per page) — 100k rows can't be row-at-a-time.
- Heartbeats progress per page so the admin panel shows it working.
- Rows are marked source='backfill': excluded from latency metrics, and no
  notifications are emitted. Idempotent via the standard dedupe key.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from ..config import settings
from ..db import get_pool, heartbeat
from ..ratelimit import polite_get
from .dedupe import make_dedupe_key
from .poller import parse_data_api_trade

log = logging.getLogger(__name__)


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


async def backfill_whale_history(http: httpx.AsyncClient, whale: dict) -> int:
    """Page the wallet's full trade history into the ledger. Returns rows scanned."""
    pool = await get_pool()
    sports = await _sport_map()
    now = datetime.now(tz=timezone.utc)
    scanned = 0
    offset = 0
    max_trades = settings().history_max_trades
    while offset < max_trades:
        resp = await polite_get(
            http, "/trades",
            params={"user": whale["address"], "limit": 100, "offset": offset, "takerOnly": "false"},
        )
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break

        rows = []
        for raw in batch:
            ev = parse_data_api_trade(raw, whale["id"], whale["username"])
            if not ev.tx_hash or ev.size <= 0:
                continue
            rows.append((
                ev.whale_id, ev.tx_hash, str(ev.asset), ev.condition_id, ev.side,
                ev.outcome, ev.outcome_index, ev.size, ev.price, ev.notional,
                ev.market_title, ev.market_slug, ev.event_slug,
                sports.get(ev.condition_id or "", "unclassified"),
                datetime.fromtimestamp(ev.ts_epoch, tz=timezone.utc), now, ev.dedupe_key,
            ))
        if rows:
            async with pool.acquire() as conn:
                await conn.executemany(_INSERT, rows)
        scanned += len(batch)
        offset += 100
        await heartbeat(
            "backfill", "running",
            {"whale": whale["username"] or whale["address"], "scanned": scanned},
        )
        if len(batch) < 100:
            break

    # Some Data API deployments cap offset paging (empty results past ~10k).
    # If a time-filter param is configured, keep walking back in time from the
    # oldest trade we have; otherwise flag the suspected cap for the admin.
    time_param = settings().history_time_param
    hit_cap = scanned > 0 and scanned % 100 == 0 and offset >= 9_900
    if hit_cap and time_param:
        oldest = await pool.fetchval(
            "SELECT EXTRACT(EPOCH FROM min(ts))::bigint FROM trades WHERE whale_id=$1",
            whale["id"],
        )
        while oldest and scanned < max_trades:
            resp = await polite_get(
                http, "/trades",
                params={"user": whale["address"], "limit": 100, "takerOnly": "false",
                        time_param: int(oldest) - 1},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            rows = []
            batch_oldest = oldest
            for raw in batch:
                ev = parse_data_api_trade(raw, whale["id"], whale["username"])
                if not ev.tx_hash or ev.size <= 0:
                    continue
                batch_oldest = min(batch_oldest, ev.ts_epoch)
                rows.append((
                    ev.whale_id, ev.tx_hash, str(ev.asset), ev.condition_id, ev.side,
                    ev.outcome, ev.outcome_index, ev.size, ev.price, ev.notional,
                    ev.market_title, ev.market_slug, ev.event_slug,
                    sports.get(ev.condition_id or "", "unclassified"),
                    datetime.fromtimestamp(ev.ts_epoch, tz=timezone.utc), now, ev.dedupe_key,
                ))
            if rows:
                async with pool.acquire() as conn:
                    await conn.executemany(_INSERT, rows)
            scanned += len(batch)
            await heartbeat("backfill", "running",
                            {"whale": whale["username"] or whale["address"],
                             "scanned": scanned, "mode": "time-cursor"})
            if batch_oldest >= oldest:
                break  # no progress — stop rather than loop
            oldest = batch_oldest
    elif hit_cap:
        log.warning("backfill for %s stopped at offset %s with full batches — likely an "
                    "API offset cap; set HISTORY_TIME_PARAM (see /api/admin/diag)",
                    whale["address"], offset)
        await heartbeat("backfill", "capped",
                        {"whale": whale["username"] or whale["address"], "scanned": scanned,
                         "hint": "probable offset cap — run /api/admin/diag, set HISTORY_TIME_PARAM, "
                                 "then reset: UPDATE whales SET history_backfilled=false"})

    await pool.execute("UPDATE whales SET history_backfilled=TRUE WHERE id=$1", whale["id"])
    log.info("history backfill for %s: %s trades scanned",
             whale["username"] or whale["address"], scanned)
    return scanned


_BACKFILL_LOCK = asyncio.Lock()


async def backfill_pending() -> int:
    """Backfill every active whale that hasn't had a history import yet.

    Single-flight: if a pass is already running in this process (e.g. a
    supervisor restarted the poller and spawned a second history loop),
    additional callers return immediately instead of doubling API traffic.
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
