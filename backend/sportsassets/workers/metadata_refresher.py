"""Worker: hot metadata cache refresher.

Continuously upserts active sports markets from Gamma so Path A enrichment is
a cache hit, and retries enrichment for any trade that missed the cache.
"""

import asyncio
import logging

from .. import gamma
from ..config import settings
from ..db import get_pool, heartbeat
from ..ingestion.pipeline import backfill_unenriched
from ..positions_sync import sync_all_positions
from ..sports import is_sport

log = logging.getLogger(__name__)


async def main() -> None:
    cfg = settings()
    client = gamma.GammaClient()
    while True:
        try:
            markets = await client.fetch_active_sports_markets()
            kept = 0
            for raw in markets:
                meta = gamma.parse_market(raw)
                if meta is None:
                    continue
                # Persist sports markets (plus anything a whale already traded
                # gets picked up by the resolution sweep regardless of sport).
                if is_sport(meta["sport"]):
                    await gamma.upsert_market(meta)
                    kept += 1
            fixed = await backfill_unenriched()
            # Backfilled historical trades arrive before their markets are in
            # our metadata store; stamp their sport once the market lands.
            pool = await get_pool()
            await pool.execute(
                """
                UPDATE trades t SET sport = m.sport
                FROM markets m
                WHERE t.condition_id = m.condition_id
                  AND t.sport = 'unclassified' AND m.sport <> 'unclassified'
                """
            )
            # Live book snapshots: every open position each whale holds right
            # now, including ones opened before we started tracking.
            pos_counts = await sync_all_positions()
            await heartbeat(
                "metadata",
                "ok",
                {"active_sports_markets": kept, "re_enriched": fixed,
                 "open_positions": sum(pos_counts.values())},
            )
            log.info(
                "metadata refresh: %s sports markets cached, %s trades re-enriched, "
                "%s open positions snapshotted", kept, fixed, sum(pos_counts.values()),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("metadata refresh failed: %s", exc)
            await heartbeat("metadata", "error", {"error": str(exc)})
        await asyncio.sleep(cfg.metadata_refresh_seconds)


if __name__ == "__main__":
    asyncio.run(main())
