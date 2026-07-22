"""Deep trade-history backfill.

When a whale joins the roster, import their ENTIRE past trade ledger from the
Data API (paged, bounded) so settled performance metrics — per-sport P&L,
W-L records, equity curve, trade/price history — are complete immediately,
not just from the moment tracking began.

Inserts are silent (notify=False): importing history must never fire pushes.
Idempotent via the standard dedupe key; safe to re-run.
"""

from __future__ import annotations

import logging

import httpx

from ..config import settings
from ..db import get_pool
from .pipeline import ingest_trade
from .poller import _sport_for_condition, parse_data_api_trade

log = logging.getLogger(__name__)

MAX_HISTORY_TRADES = 20_000  # per wallet, safety bound


async def backfill_whale_history(http: httpx.AsyncClient, whale: dict) -> int:
    """Page the wallet's full trade history into the ledger. Returns new-row count."""
    imported = 0
    offset = 0
    while offset < MAX_HISTORY_TRADES:
        resp = await http.get(
            "/trades",
            params={"user": whale["address"], "limit": 100, "offset": offset, "takerOnly": "false"},
        )
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        for raw in batch:
            ev = parse_data_api_trade(raw, whale["id"], whale["username"])
            if not ev.tx_hash or ev.size <= 0:
                continue
            sport = await _sport_for_condition(ev.condition_id)
            if sport:
                ev.sport = sport
            if await ingest_trade(ev, notify=False) is not None:
                imported += 1
        if len(batch) < 100:
            break
        offset += 100

    pool = await get_pool()
    await pool.execute("UPDATE whales SET history_backfilled=TRUE WHERE id=$1", whale["id"])
    log.info(
        "history backfill for %s: %s trades imported (scanned to offset %s)",
        whale["username"] or whale["address"], imported, offset,
    )
    return imported


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
    return total
