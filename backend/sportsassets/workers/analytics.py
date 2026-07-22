"""Worker: analytics engine — resolution sweep + rollup recompute + drift alerts."""

import asyncio
import logging

from .. import gamma
from ..analytics.engine import run_cycle
from ..analytics.resolution import sweep_resolutions
from ..db import heartbeat
from ..notifications import telegram

log = logging.getLogger(__name__)

CYCLE_SECONDS = 60


async def main() -> None:
    client = gamma.GammaClient()
    alerted_drift = False
    while True:
        try:
            newly_resolved = await sweep_resolutions(client)
            result = await run_cycle()
            await heartbeat("analytics", "ok", {**result, "newly_resolved": newly_resolved,
                                                "drift_alerts": len(result["drift_alerts"])})
            if result["drift_alerts"] and not alerted_drift:
                alerted_drift = True
                lines = [
                    f"{a['username'] or a['whale_id']}: ours {a['ours']:,} vs board "
                    f"{a['leaderboard']:,} ({a['drift_pct']}%)"
                    for a in result["drift_alerts"]
                ]
                await telegram.alert_admins(
                    "⚠️ P&L drift >10% vs leaderboard (classification/lifecycle bug?):\n"
                    + "\n".join(lines)
                )
            elif not result["drift_alerts"]:
                alerted_drift = False
        except Exception as exc:  # noqa: BLE001
            log.exception("analytics cycle failed")
            await heartbeat("analytics", "error", {"error": str(exc)})
        await asyncio.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
