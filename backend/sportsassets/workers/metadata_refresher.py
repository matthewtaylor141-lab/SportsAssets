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
from ..sports import classify, is_sport

log = logging.getLogger(__name__)


async def reclassify_markets(pool, batch: int = 5000) -> int:
    """Re-run classification for markets stuck without a sport. Returns changes."""
    import json as _json

    rows = await pool.fetch(
        """
        SELECT condition_id, tags, slug, event_slug, title
        FROM markets WHERE sport IN ('unclassified', 'Non-Sports') LIMIT $1
        """,
        batch,
    )
    changed = 0
    for r in rows:
        tags = r["tags"]
        if isinstance(tags, str):
            tags = _json.loads(tags)
        new_sport = classify(tags or [], r["slug"] or "", r["title"] or "",
                             event_slug=r["event_slug"] or "")
        if new_sport not in ("unclassified", "Non-Sports"):
            await pool.execute(
                "UPDATE markets SET sport=$2, updated_at=now() WHERE condition_id=$1",
                r["condition_id"], new_sport,
            )
            changed += 1
    return changed


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
            # The '(no slug)' diagnosis rides the heartbeat: how many of
            # the newest 1k trades are unenriched, and how many tokens
            # the backfill has written off as Gamma-unknowable.
            from ..ingestion.pipeline import enrich_stats
            detail.update(enrich_stats)
            pool = await get_pool()
            # Retroactive market reclassification: classification is
            # deterministic and re-runnable, so classifier improvements apply
            # to already-stored markets without re-fetching anything.
            detail["markets_reclassified"] = await reclassify_markets(pool)
            # outcomeIndex sentinel repair (API drift: 999/missing) — rebuild
            # from the market's outcome labels; unrepaired sentinels never
            # match a winning index, so they stay excluded rather than being
            # silently scored as $0 losers (audited-methodology rule).
            repaired = await pool.execute(
                """
                UPDATE trades t SET outcome_index = mt.outcome_index
                FROM market_tokens mt
                WHERE t.condition_id = mt.condition_id
                  AND t.outcome IS NOT NULL
                  AND lower(t.outcome) = lower(mt.outcome)
                  AND t.outcome_index IS DISTINCT FROM mt.outcome_index
                """
            )
            detail["outcome_idx_repaired"] = int(repaired.split()[-1]) if repaired else 0
            # Trades always follow their market's current classification.
            status_msg = await pool.execute(
                """
                UPDATE trades t SET sport = m.sport
                FROM markets m
                WHERE t.condition_id = m.condition_id
                  AND m.sport <> 'unclassified'
                  AND t.sport IS DISTINCT FROM m.sport
                """
            )
            detail["trades_resynced"] = int(status_msg.split()[-1]) if status_msg else 0
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
