"""Resolution tracking.

Primary signal: Gamma `closed` + collapsed outcome prices (0/1), polled for
every market a tracked whale has touched that isn't resolved yet. The
metadata refresher keeps active markets fresh; this sweep specifically chases
markets that left the active set (i.e. just closed/resolved).

On new resolutions the analytics cycle re-runs, realizing remaining shares
at $1/$0 and refreshing rollups — this is the "recompute on resolution
events" path from the spec.
"""

from __future__ import annotations

import logging

from .. import gamma
from ..db import get_pool

log = logging.getLogger(__name__)


async def unresolved_traded_condition_ids(limit: int = 200) -> list[str]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT DISTINCT t.condition_id
        FROM trades t
        LEFT JOIN markets m ON m.condition_id = t.condition_id
        WHERE t.condition_id IS NOT NULL AND (m.condition_id IS NULL OR NOT m.resolved)
        LIMIT $1
        """,
        limit,
    )
    return [r["condition_id"] for r in rows]


async def sweep_resolutions(client: gamma.GammaClient) -> int:
    """Fetch metadata for unresolved traded markets; return count newly resolved."""
    condition_ids = await unresolved_traded_condition_ids()
    if not condition_ids:
        return 0
    pool = await get_pool()
    before = await pool.fetchval("SELECT count(*) FROM markets WHERE resolved")
    raws = await client.fetch_by_condition_ids(condition_ids)
    for raw in raws:
        meta = gamma.parse_market(raw)
        if meta:
            await gamma.upsert_market(meta)
    after = await pool.fetchval("SELECT count(*) FROM markets WHERE resolved")
    newly = after - before
    if newly:
        log.info("%s market(s) newly resolved", newly)
    return newly
