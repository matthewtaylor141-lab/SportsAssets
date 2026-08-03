"""Worker: analytics engine — resolution sweep + rollup recompute + drift alerts."""

import asyncio
import logging

from .. import gamma
from ..analytics.engine import run_cycle
from ..analytics.resolution import sweep_resolutions
from ..db import heartbeat
from ..notifications import telegram

log = logging.getLogger(__name__)

# 300, was 60: the full-ledger replay is the heaviest query the shared
# Postgres sees, and running it back-to-back starved the API's own reads
# at boot (the track-record archive hydrate failed twice on 2026-08-03,
# wiping history off the site until a lucky retry). Settlements landing
# within five minutes is still "automatic" for a settlement-paced record.
CYCLE_SECONDS = 300


async def main() -> None:
    client = gamma.GammaClient()
    alerted_drift = False
    while True:
        try:
            # Beat BEFORE the cycle too: this loop once sat wedged for 12
            # days (full-table fetch OOM-looping the process) with its last
            # heartbeat frozen at "ok" — a hang must show as a fresh
            # "running" beat with a start time, not as ancient success.
            await heartbeat("analytics", "running", {})
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
