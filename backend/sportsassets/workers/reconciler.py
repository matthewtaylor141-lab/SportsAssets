"""Worker: hourly Path B reconciliation sweep."""

import asyncio
import logging

from ..config import settings
from ..ingestion.reconciler import reconcile_once

log = logging.getLogger(__name__)


async def main() -> None:
    interval = settings().reconcile_interval_seconds
    while True:
        try:
            result = await reconcile_once()
            log.info("reconciliation: %s", result)
        except Exception:  # noqa: BLE001
            log.exception("reconciliation failed")
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(main())
