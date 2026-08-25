"""Worker: analytics engine — resolution sweep + rollup recompute + drift alerts."""

import asyncio
import datetime as _dt
import json
import logging
import os
import time

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
            await publish_whale_benchmark()
        except Exception as exc:  # noqa: BLE001
            log.exception("analytics cycle failed")
            await heartbeat("analytics", "error", {"error": str(exc)})
        await asyncio.sleep(CYCLE_SECONDS)


# HOW OFTEN THE BENCHMARK IS RE-MEASURED. Deliberately much slower than
# the analytics cycle: the merge replay walks every fill of every
# copied whale — swisstony alone has 283,748 — and whale edge measured
# over a month does not move in an hour. Running it more often would
# buy nothing and cost the same memory that 502'd the API endpoint that
# used to compute it inline.
BENCHMARK_EVERY_S = float(os.environ.get("WHALE_BENCHMARK_EVERY_S",
                                         "3600"))
_last_benchmark = 0.0


async def publish_whale_benchmark() -> None:
    """Publish the roster's merge-inclusive edge for /api/admin/proof.

    IT LIVES HERE AND NOT IN THE API for a reason with a receipt. The
    proof endpoint originally computed this inline and inherited the
    heaviest query in the system; the 2026-08-25 probe read
    MERGEHTTP code=502 and PROOF unavailable together. The instrument
    that answers "are we profitable" became unavailable exactly when it
    mattered, because I hung it off the most expensive thing the API
    does.

    A worker can afford the walk, on its own cadence, in a process that
    is not serving requests. A stale benchmark is a fine benchmark. A
    missing one degrades to "no target", which costs the sample-size
    projection and leaves the verdict untouched.
    """
    global _last_benchmark
    now = time.monotonic()
    if now - _last_benchmark < BENCHMARK_EVERY_S:
        return
    _last_benchmark = now
    try:
        from ..analytics.merge_pnl import whale_merge_pnl
        from ..api.copies_record import COPY_WHALES
        from ..db import get_pool
        from ..live_executor import COPY_CUT_WHALES, _whale_set

        pool = await get_pool()
        # Whole book — a windowed replay misbooks every pre-window
        # position's exit as an entry, understating realised P&L and
        # inflating the ROI denominator.
        graded = await whale_merge_pnl(pool, list(COPY_WHALES), None)

        # THE BENCHMARK IS THE EDGE WE ARE TRYING TO INHERIT, SO IT MUST
        # BE THE WHALES WE ACTUALLY COPY.
        #
        # Averaging every graded whale produced a headline of -0.08% on
        # $878,764,744 of entries — dominated by 0x2c33 at -18.97% and
        # swisstony at -3.29%, both of whom are CUT and neither of whom
        # we place a dollar behind. /api/admin/proof then sized the
        # sample against a negative target and reported that we needed
        # 19,787,471 settled copies, which is absurd on its face and is
        # the only reason I caught it.
        #
        # A benchmark that includes the books we deliberately do not
        # trade is not this strategy's benchmark. Rostered whales only,
        # and the response names which.
        _kept = {w for w in graded
                 if w.lower() in _whale_set("LIVE_VERIFIED_WHALES")
                 and w.lower() not in COPY_CUT_WHALES}
        _rost = {w: g for w, g in graded.items() if w in _kept}
        ent = sum(float(g.get("entry_notional") or 0)
                  for g in _rost.values())
        rea = sum(float(g.get("realized_total") or 0)
                  for g in _rost.values())
        if ent <= 0:
            return
        payload = {
            "whale_roi_on_entries": round(rea / ent, 6),
            "whale_entry_notional": round(ent, 2),
            "whale_realized": round(rea, 2),
            "rostered": sorted(_kept),
            "excluded_as_cut": sorted(set(graded) - _kept),
            # Per whale too, so a roster question can be answered from
            # the published value instead of re-running the walk.
            "per_whale": {
                w: {"edge_roi": g.get("edge_roi"),
                    "edge_ci95": g.get("edge_ci95"),
                    "edge_lots": g.get("edge_lots"),
                    "exit_value": g.get("exit_value")}
                for w, g in graded.items()},
            "measured_at": _dt.datetime.now(
                _dt.timezone.utc).isoformat(timespec="seconds"),
        }
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            "whale_edge_benchmark", json.dumps(payload))
        log.info("whale benchmark published: %s on $%s of entries",
                 payload["whale_roi_on_entries"],
                 payload["whale_entry_notional"])
    except Exception:  # noqa: BLE001 — a benchmark never kills the loop
        log.warning("whale benchmark publish failed", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
