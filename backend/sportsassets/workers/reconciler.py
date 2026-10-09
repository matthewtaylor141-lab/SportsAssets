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
            # bounded (RC6 identity lane): the run's own row holds the
            # per-wallet coverage and every late fill by trade id
            cov = result.get("coverage") or {}
            log.info("reconciliation: run %s missed %s, wallets %s, "
                     "with a coverage hole %s (%s s unswept)",
                     result.get("run_id"), result.get("missed"),
                     cov.get("wallets"), cov.get("wallets_with_hole"),
                     cov.get("hole_seconds"))
        except Exception:  # noqa: BLE001
            log.exception("reconciliation failed")
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(main())
