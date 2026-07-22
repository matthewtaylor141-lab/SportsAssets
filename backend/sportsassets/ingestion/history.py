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

import logging
from datetime import datetime, timezone

import httpx

from ..config import settings
from ..db import get_pool, heartbeat
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
        resp = await http.get(
            "/trades",
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

    await pool.execute("UPDATE whales SET history_backfilled=TRUE WHERE id=$1", whale["id"])
    log.info("history backfill for %s: %s trades scanned",
             whale["username"] or whale["address"], scanned)
    return scanned


async def backfill_pending() -> int:
    """Backfill every active whale that hasn't had a history import yet."""
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
