"""Worker: weekly roster refresh (cron-in-a-loop; interval is config)."""

import asyncio
import logging

from ..config import settings
from ..db import get_pool, heartbeat
from ..roster import refresh_roster

log = logging.getLogger(__name__)


async def main() -> None:
    cfg = settings()
    interval = cfg.roster_refresh_interval_hours * 3600
    pool = await get_pool()
    while True:
        empty = (await pool.fetchval("SELECT count(*) FROM whales WHERE active")) == 0
        try:
            if empty:
                log.info("roster empty — running initial selection now")
            result = await refresh_roster()
            await heartbeat("roster", "ok", result)
        except Exception as exc:  # noqa: BLE001
            log.exception("roster refresh failed")
            await heartbeat("roster", "error", {"error": str(exc)})
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(main())
