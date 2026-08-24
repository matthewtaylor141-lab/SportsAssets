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

from . import analytics, chain_listener, copy_sweep, dispatcher, metadata_refresher, poller, premap, reconciler, roster, underdog

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


async def main() -> None:
    await asyncio.gather(*(supervise(name, fn) for name, fn in LOOPS))


if __name__ == "__main__":
    asyncio.run(main())
