"""THE PER-AGENT FUNNEL RECEIPT (P0 incident): GET /api/command/agent-funnel.

    GET /api/command/agent-funnel             the last 24 h
        ?since=<epoch>&until=<epoch>          a window (at most 7 days)
        ?account_id=<paper account>           one paper account

Per agent strategy: RECEIVED / NOT_DECIDED / REJECTED_SOFTWARE / ELIGIBLE /
REJECTED_ECONOMIC / REJECTED_UNCLASSIFIED / ENTER / ORDER /
ENTER_WITHOUT_ORDER / FILL, every refusal code classified SOFTWARE or ECONOMIC
with its stage by the one taxonomy (unknown = UNCLASSIFIED, never economic),
plus the collector's stage before any agent (`agent_funnel`).

COMMAND session (require_read). One READ ONLY transaction under a statement
timeout; every statement a bounded SELECT. No route here writes, sends an
order, or changes capital, limits, credentials or thresholds. Orders and
fills are the PAPER account's.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query

from .. import agent_funnel as AF
from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/agent-funnel"
STATEMENT_TIMEOUT_MS = 8000


@router.get(PATH, dependencies=[Depends(require_read)])
async def agent_funnel(since: float | None = Query(default=None, ge=0),
                       until: float | None = Query(default=None, ge=0),
                       account_id: str | None = Query(default=None,
                                                      max_length=120)
                       ) -> dict:
    now = time.time()
    s, u = AF.window(since=since, until=until, now=now)
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                await conn.execute("SET LOCAL statement_timeout = %d"
                                   % STATEMENT_TIMEOUT_MS)
                data = await AF.read(conn, since=s, until=u,
                                     account_id=account_id)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE",
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                "data": None, "since": s, "until": u,
                "version": AF.VERSION}
    return {"status": "OK", "why": None, "data": data, "since": s,
            "until": u, "computed_at": now, "version": AF.VERSION,
            "authority": "READ_ONLY_NO_AUTHORITY",
            "orders_and_fills_are": "PAPER"}
