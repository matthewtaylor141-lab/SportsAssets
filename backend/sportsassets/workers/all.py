"""Worker: ALL background loops in one process (cheap single-instance deploys).

Deploy marker 2026-08-07: the worker must be running >= 0e358f0 (RN1
reinstated in copy_sports) and 460425e (hourly sweep). If the copy_sweep
heartbeat lags more than ~70 minutes, this service is on stale code —
check the host dashboard for a stuck deploy.

Runs the poller, chain listener, metadata refresher, analytics, dispatcher,
roster, and reconciler as supervised asyncio tasks. Each loop is restarted
with a delay if it ever crashes, so one bad loop can't take down the rest.

Use this on hosts where each background service is billed separately
(e.g. one Render worker). docker-compose keeps them as separate containers.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from . import (analytics, chain_listener, copy_sweep, dispatcher, edge_marks,
               metadata_refresher, poller, premap, price_path, reconciler,
               roster, roster_auto, underdog, whale_exits)

log = logging.getLogger(__name__)

RESTART_DELAY_SECONDS = 5

LOOPS: list[tuple[str, Callable[[], Awaitable[None]]]] = [
    ("poller", poller.main),
    ("chain_listener", chain_listener.main),
    ("metadata", metadata_refresher.main),
    ("analytics", analytics.main),
    ("dispatcher", dispatcher.main),
    ("roster", roster.main),
    ("reconciler", reconciler.main),
    ("copy_sweep", copy_sweep.main),
    ("underdog", underdog.main),
    ("premap", premap.main),
    # Exit detection from POSITIONS (2026-08-25). These whales close
    # by merging, not selling: 860k buys and 0 sells for swisstony,
    # while 62 of his 75 held positions sit below what he bought. No
    # trade listener can see that, so this diffs holdings and feeds
    # mirror_exit the fraction it measured.
    ("whale_exits", whale_exits.main),
    # MEASUREMENT ONLY: samples the ask after every attempted copy so the
    # post-fill price curve -- the thing that decides the fill rule --
    # exists as data instead of an argument. Never places or cancels.
    ("price_path", price_path.main),
    # THE ROSTER BY EVIDENCE (owner order 2026-09-01, evening): whales
    # enter, are promoted and are demoted by two numbers -- the edge
    # gate's verdict on HIS book and the proof cohort's interval on OUR
    # copies of him. Hourly; fails closed; every decision is written to
    # roster_decisions with the numbers that made it.
    ("roster_auto", roster_auto.main),
    # MEASUREMENT ONLY: marks every whale buy at the prices that split
    # his edge into selection and timing (owner question 2026-09-01).
    # Public CLOB reads, paced; never touches an order.
    ("edge_marks", edge_marks.main),
]


async def supervise(name: str, factory: Callable[[], Awaitable[None]]) -> None:
    while True:
        try:
            log.info("starting loop: %s", name)
            await factory()
            log.warning("loop %s exited cleanly; restarting in %ss", name, RESTART_DELAY_SECONDS)
        except Exception:  # noqa: BLE001
            log.exception("loop %s crashed; restarting in %ss", name, RESTART_DELAY_SECONDS)
        await asyncio.sleep(RESTART_DELAY_SECONDS)


async def _record_boot() -> None:
    """Workers-side /healthz (2026-08-24): three probes in a row read a
    silent premap sweep and the diagnosis stalled on 'is the worker even
    running the new code?'. The API answers that with /healthz commit;
    the workers now answer it with this state row, written at every
    boot and surfaced by the probe."""
    import json
    import os
    from datetime import datetime, timezone

    from ..db import get_pool

    try:
        pool = await get_pool()
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) "
            "VALUES ('workers_boot', $1::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=$1::jsonb",
            json.dumps({
                "commit": (os.environ.get("RENDER_GIT_COMMIT") or "?")[:7],
                "at": datetime.now(tz=timezone.utc)
                .isoformat(timespec="seconds")}))
    except Exception:  # noqa: BLE001 — the marker must not block boot
        log.exception("workers_boot marker write failed")


async def main() -> None:
    # The marker runs CONCURRENTLY with the loops (leak-hunt find
    # 2026-08-24): awaiting it first serialized a DB connect retry in
    # front of every worker — a slow DB would have delayed the chain
    # listener itself for a status row.
    await asyncio.gather(_record_boot(),
                         *(supervise(name, fn) for name, fn in LOOPS))


if __name__ == "__main__":
    asyncio.run(main())
