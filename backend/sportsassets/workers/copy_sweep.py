"""Worker: copy the source whales' OPEN positions, not just fresh fills.

Owner instruction 2026-08-03: "any open trade is copied and actually traded
on our account." The live executor's freshness gate (<5 min) only covers
detections made while everything is healthy; the six-day ingestion outage
and today's catch-up mean both whales are sitting on open positions that
were never candidates. This sweep closes that gap and then keeps closing
it on a slow clock.

Mechanics and why they're safe:
- Candidates: each whale's most recent BUY per outcome token in the last
  7 days, at <= $0.50, in a market not known to be resolved, that the live
  sleeve has never touched (any live_orders row for the asset disqualifies).
- Execution goes through maybe_execute — the SAME caps as fresh copies
  (one contract, per-fill ceiling, $100/day, kill switch, dedupe). The
  sweep adds no new authority; it only widens the candidate stream.
- Staleness protection is the ORDER TYPE, not a heuristic: a FOK limit at
  his price + slippage fills only where the market still offers roughly
  his entry. A market that already moved away simply kills — we never pay
  the post-move price.
- Idempotent per asset, so the periodic re-run only picks up whales'
  NEW open positions that fresh-detection copying somehow missed.
"""

import asyncio
import logging

from ..config import settings
from ..db import get_pool, heartbeat
from ..live_executor import maybe_execute

log = logging.getLogger(__name__)

BOOT_DELAY_S = 120       # let the poller/executor settle before sweeping
SWEEP_EVERY_S = 6 * 3600
PRICE_CEILING = 0.50     # mirrors the per-fill ceiling; cheap pre-filter


async def sweep_once() -> dict:
    pool = await get_pool()
    whales = sorted(settings().source_whales())
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (t.asset)
               t.id, t.whale_id, w.username AS whale_username, t.tx_hash, t.asset,
               t.condition_id, t.side, t.outcome, t.outcome_index,
               t.size::float8 AS size, t.price::float8 AS price,
               t.notional::float8 AS notional, t.market_title, t.market_slug,
               t.event_slug, t.sport,
               extract(epoch FROM t.ts)::float8 AS ts_epoch
        FROM trades t
        JOIN whales w ON w.id = t.whale_id
        LEFT JOIN markets m ON m.condition_id = t.condition_id
        WHERE t.side = 'BUY'
          AND t.price <= $2
          AND t.ts > now() - interval '7 days'
          AND lower(w.username) = ANY($1)
          AND COALESCE(m.resolved, false) = false
          -- An asset is off the table once an order was actually PLACED
          -- for it (filled/unfilled/submitting/error). Mapping rejections
          -- are retryable: a mapper fix must be able to revisit the same
          -- open positions, so only this trade-id's own audit row blocks
          -- (maybe_execute's ON CONFLICT would no-op it silently).
          AND NOT EXISTS (SELECT 1 FROM live_orders lo
                          WHERE lo.asset = t.asset
                            AND lo.status <> 'rejected')
          AND NOT EXISTS (SELECT 1 FROM live_orders lo2
                          WHERE lo2.trade_id = t.id)
        ORDER BY t.asset, t.ts DESC
        """,
        whales, PRICE_CEILING,
    )
    attempted = 0
    for r in rows:
        payload = {
            "id": r["id"],
            "whale_id": r["whale_id"],
            "whale_username": r["whale_username"],
            "asset": r["asset"],
            "condition_id": r["condition_id"],
            "side": r["side"],
            "outcome": r["outcome"],
            "outcome_index": r["outcome_index"],
            "size": r["size"],
            "price": r["price"],
            "notional": r["notional"],
            "market_title": r["market_title"],
            "market_slug": r["market_slug"],
            "event_slug": r["event_slug"],
            "sport": r["sport"],
            "ts_epoch": r["ts_epoch"],
        }
        try:
            await maybe_execute(payload, None)
            attempted += 1
        except Exception:  # noqa: BLE001 — one bad market must not stop the sweep
            log.exception("sweep copy failed for trade %s", r["id"])
        await asyncio.sleep(1.0)   # gentle on the venue API
    return {"candidates": len(rows), "attempted": attempted}


async def main() -> None:
    await asyncio.sleep(BOOT_DELAY_S)
    while True:
        try:
            result = await sweep_once()
            log.info("copy sweep: %s", result)
            await heartbeat("copy_sweep", "ok", result)
        except Exception as exc:  # noqa: BLE001
            log.exception("copy sweep failed")
            await heartbeat("copy_sweep", "error", {"error": str(exc)})
        await asyncio.sleep(SWEEP_EVERY_S)


if __name__ == "__main__":
    asyncio.run(main())
