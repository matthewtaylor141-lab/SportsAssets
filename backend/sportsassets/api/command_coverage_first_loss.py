"""THE FIRST-LOSS CENSUS: GET /api/command/coverage/first-loss.

    GET /api/command/coverage/first-loss            the last 24 h
        ?hours=1..168                               another window

For every provider event the collector recorded in the window, the FIRST
stage of PROVIDER -> NORMALIZED -> MAPPED -> SETTLEMENT -> MODEL ->
FAIR_VALUE -> BOOK -> EV -> ENTER_PASS at which it was lost, with the
refusal code and its class (SOFTWARE / ECONOMIC / EXTERNAL / UNCLASSIFIED),
per competition and per sport (`coverage_first_loss`). Every count names its
source table; an unread source is UNAVAILABLE, never zero.

COMMAND session (require_read). One READ ONLY transaction under a statement
timeout; every statement a bounded SELECT. No route here writes, sends an
order, or changes capital, limits, credentials or thresholds.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query, Response

from .. import coverage_first_loss as FL
from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/coverage/first-loss"
STATEMENT_TIMEOUT_MS = 15000


@router.get(PATH, dependencies=[Depends(require_read)])
async def coverage_first_loss(
        response: Response,
        hours: int = Query(FL.DEFAULT_HOURS, ge=1, le=FL.MAX_HOURS)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    now = time.time()
    since, until = now - float(hours) * 3600.0, now + 1.0
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                await conn.execute("SET LOCAL statement_timeout = %d"
                                   % STATEMENT_TIMEOUT_MS)
                data = await FL.read(conn, since=since, until=until)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE",
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                "data": None, "since": since, "until": until,
                "hours": hours, "version": FL.VERSION}
    return {"status": data.get("status"), "why": data.get("why"),
            "data": data, "since": since, "until": until, "hours": hours,
            "computed_at": now, "version": FL.VERSION,
            "authority": "READ_ONLY_NO_AUTHORITY"}
