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
               metadata_refresher, mirror_live, mirror_shadow, poller, premap,
               price_path, reconciler, retention, roster, roster_auto, underdog,
               whale_exits)

log = logging.getLogger(__name__)

RESTART_DELAY_SECONDS = 5

# THE BOOT STAMPEDE (outage review 2026-09-05): gather() started every
# loop in the same instant, so the loops' opening database reads hit
# this process's ten-connection pool together on every boot. get_pool is
# single-flight now (db.py) -- one pool built once, not one per caller --
# but the reads behind it still queue on it. The API's own /healthz read
# its pool at size == max, idle == 0 at 01:06Z with nothing of note in
# flight; this process, with more loops on the same pool size, is
# inferred to do the same at every boot. Each loop's FIRST start is
# offset by its LOOPS index times this, so the poller (index 0) starts
# at once and the tail of the list about twelve seconds later.
BOOT_STAGGER_S = 0.75

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
    # POSITION MIRRORING, PHASE P0 (owner order 2026-09-02): reads each
    # mirrored whale's net position per market from his fills, computes
    # the target we would hold and the one order we would place, and
    # writes it to mirror_shadow. NO ORDERS. Thirty games of shadow gate
    # phase P1 (long-only live) by the numbers.
    ("mirror_shadow", mirror_shadow.main),
    # MONEY. POSITION MIRRORING, PHASE P1 (owner order 2026-09-02, "go for
    # it, let's get this working"): the live reconciler behind
    # mirror_shadow's plan -- long-only, post-only rests at his level,
    # one standing live_orders row per book, every existing breaker
    # honoured. Registered whatever PMUS_MIRROR says: with the flag off
    # (the default) this is a CANCEL-ONLY loop that places nothing and
    # reconciles whatever a previous process left resting, so a deploy
    # that drops the flag cannot orphan a bid (spec section 3.7). The
    # DB switch 'mirror_live' must read true before it increases.
    ("mirror_live", mirror_live.main),
    # RETENTION (2026-09-05, the full-disk outage): the paper trader's
    # two measurement tables, ai_trades and copy_probes, filled the
    # 15 GB disk and took the database's hostname with it for ~15
    # hours. Hourly, bounded batches, a per-cycle cap, windows derived
    # from what /api/ai-trader and /api/copy-report can ask for; touches
    # exactly those two tables and nothing else. RETENTION=off stops it.
    ("retention", retention.main),
]


async def supervise(name: str, factory: Callable[[], Awaitable[None]], *,
                    boot_delay: float = 0.0) -> None:
    # The stagger belongs before the FIRST start and nowhere else
    # (2026-09-05): a restart is one loop alone, its neighbours long
    # since spread out, so re-applying boot_delay there would only hold
    # a crashed loop -- the poller's Path B included -- out of service
    # for nothing. RESTART_DELAY_SECONDS is the restart's whole wait.
    if boot_delay > 0:
        await asyncio.sleep(boot_delay)
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
    # listener itself for a status row. The marker is not staggered
    # either: it is the one write the probe waits on to learn which
    # commit is booting.
    await asyncio.gather(_record_boot(),
                         *(supervise(name, fn, boot_delay=i * BOOT_STAGGER_S)
                           for i, (name, fn) in enumerate(LOOPS)))


if __name__ == "__main__":
    asyncio.run(main())
