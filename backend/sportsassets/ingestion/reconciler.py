"""Hourly reconciliation: Path B (Data API) is the source of truth.

Sweeps recent trades per tracked wallet from the Data API and re-runs them
through the pipeline. Anything already ingested is a dedupe no-op; anything
missed (e.g. during a WS outage that outlived the poller's 100-trade window)
is ingested late and counted as `missed`.
"""

from __future__ import annotations

import json
import logging

import httpx

from ..config import settings
from ..db import get_pool, heartbeat
from ..ratelimit import polite_get
from .pipeline import ingest_trade_result
from .poller import _sport_for_condition, parse_data_api_trade

log = logging.getLogger(__name__)


async def reconcile_once(depth: int = 500) -> dict:
    cfg = settings()
    pool = await get_pool()
    run_id = await pool.fetchval(
        "INSERT INTO reconciliation_runs DEFAULT VALUES RETURNING id"
    )
    whales = await pool.fetch("SELECT id, address, username FROM whales WHERE active AND NOT banned")
    missed = 0
    per_wallet: dict = {}
    async with httpx.AsyncClient(base_url=cfg.data_api_base, timeout=20) as http:
        for whale in whales:
            wallet_missed = 0
            offset = 0
            # COVERAGE EVIDENCE for the S1 corroboration sweep (fleet
            # round 6): a run that fetched only the newest `depth` rows
            # of a busy wallet never had its chance at an older fill,
            # and no later run reaches deeper — treating it as covering
            # falsely tripped the emitter on a venue-visible fill. The
            # sweep now requires either complete=true (feed exhausted
            # inside the depth) or oldest (the oldest venue ts this run
            # actually reached) at or before the fill's own time.
            complete = False
            oldest_ts: float | None = None
            try:
                while offset < depth:
                    resp = await polite_get(
                        http, "/trades",
                        params={
                            "user": whale["address"],
                            "limit": 100,
                            "offset": offset,
                            "takerOnly": "false",
                        },
                    )
                    resp.raise_for_status()
                    batch = resp.json()
                    if not batch:
                        # An empty FIRST page is not "feed exhausted"
                        # (fleet round 7): a degraded venue serving
                        # 200-[] would brand a correct emission
                        # uncorroborated with the strongest possible
                        # evidence. Only an end reached AFTER real
                        # rows proves the feed was walked; an empty
                        # start defers (no cov claim of completeness).
                        complete = offset > 0
                        break
                    for raw in batch:
                        ev = parse_data_api_trade(raw, whale["id"], whale["username"])
                        if not ev.tx_hash or ev.size <= 0:
                            # a row unusable for ingest must not
                            # testify for coverage either (fleet round
                            # 9, major: a degraded-index stub with
                            # ts=1 set oldest=1.0 and faked full-
                            # history span coverage — a universal
                            # waiver that false-tripped STICKY on a
                            # correct emission)
                            continue
                        if (ev.ts_epoch and ev.ts_epoch > 1e9
                                and (oldest_ts is None
                                     or ev.ts_epoch < oldest_ts)):
                            # the 1e9 floor (2001) rejects sentinel
                            # timestamps a degraded venue might emit
                            oldest_ts = float(ev.ts_epoch)
                        sport = await _sport_for_condition(ev.condition_id)
                        if sport:
                            ev.sport = sport
                        # WAS IT NEW? `is not None` stopped meaning that
                        # when ingest_trade switched to ON CONFLICT DO
                        # UPDATE — it returns the id for duplicates too, so
                        # this counted the ENTIRE 500-row-per-wallet
                        # re-sweep as missed fills and reported permanent
                        # drift on every run.
                        _tid, was_new = await ingest_trade_result(ev)
                        if was_new:
                            wallet_missed += 1
                    offset += 100
                    if len(batch) < 100:
                        complete = True
                        break
                per_wallet["cov:" + whale["address"]] = {
                    "complete": complete, "oldest": oldest_ts}
            except Exception as exc:  # noqa: BLE001 — one wallet must
                # never abort the whole run: this sweep is the sole
                # backstop for the S1 corroboration stamp, and a single
                # wallet's HTTP error was doubling the backstop gap for
                # every wallet AFTER it in the roster (fleet round 4).
                # Whatever this wallet ingested before failing stands.
                # No cov: key on failure — a partial sweep proves
                # nothing about depth (round 6).
                log.warning("reconcile: wallet %s failed, continuing: %s",
                            whale["address"][:10], exc)
                per_wallet["failed:" + whale["address"]] = 1
            per_wallet[whale["address"]] = wallet_missed
            missed += wallet_missed

    await pool.execute(
        "UPDATE reconciliation_runs SET finished_at=now(), missed=$2, details=$3::jsonb WHERE id=$1",
        run_id,
        missed,
        json.dumps({"per_wallet": per_wallet}),
    )
    status = "ok" if missed == 0 else "drift"
    await heartbeat("reconciler", status, {"missed": missed})
    if missed:
        log.warning("reconciliation ingested %s missed fills: %s", missed, per_wallet)
    return {"run_id": run_id, "missed": missed, "per_wallet": per_wallet}
