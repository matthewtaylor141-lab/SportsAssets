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
    """Each stage runs independently — a Gamma hiccup must never block the
    position snapshots (which come from a different API), and vice versa."""
    cfg = settings()
    client = gamma.GammaClient()
    while True:
        detail: dict = {}
        errors: dict = {}

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
            detail["active_sports_markets"] = kept
        except Exception as exc:  # noqa: BLE001
            log.warning("gamma markets refresh failed: %s", exc)
            errors["markets"] = str(exc)[:180]

        try:
            detail["re_enriched"] = await backfill_unenriched()
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
        except Exception as exc:  # noqa: BLE001
            log.warning("enrichment/reclassify failed: %s", exc)
            errors["enrich"] = str(exc)[:180]

        try:
            # Live book snapshots: every open position each whale holds right
            # now, including ones opened before we started tracking.
            pos_counts = await sync_all_positions()
            detail["open_positions"] = sum(pos_counts.values())
        except Exception as exc:  # noqa: BLE001
            log.warning("position snapshot sync failed: %s", exc)
            errors["positions"] = str(exc)[:180]

        status = "ok" if not errors else ("degraded" if detail else "error")
        await heartbeat("metadata", status, {**detail, **({"errors": errors} if errors else {})})
        log.info("metadata cycle %s: %s%s", status, detail, f" errors={errors}" if errors else "")
        await asyncio.sleep(cfg.metadata_refresh_seconds)


if __name__ == "__main__":
    asyncio.run(main())
