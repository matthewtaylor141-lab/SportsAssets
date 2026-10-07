"""GET /api/command/completion-readiness -- the completion readiness gate.

One authenticated, READ-ONLY readback (Completion Readiness Patch V1):
runtime (shared-worker RSS / high-water / process age; the dedicated market
plane's state, mode and streams), market data (subscription mode, stream
count, priority freshness numerator / denominator, refdata coverage),
held-position freshness, the capital-readiness hard gates, the probability
and executable-EV authorities, the repaired digital twin's fresh agreement,
agent licences, the CASH-incumbent tournament, regimes, daily revenue
readiness, Adriana, PMUS position confirmation, and the readiness verdict
-- PAPER_SHADOW_ONLY until every input is green, never automatic, never
while a governor says CASH. It changes nothing.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Response

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/completion-readiness"
CACHE_S = 120.0
STATEMENT_TIMEOUT_MS = 45000
LABEL = "PAPER_SHADOW"
AUTHORITY = "READBACK_ONLY_NO_ORDER_NO_CAPITAL_AUTHORITY"
_CACHE: dict = {}


def envelope(status: str, why=None, *, data=None, computed_at=None) -> dict:
    return {"label": LABEL, "authority": AUTHORITY, "status": status,
            "why": why, "computed_at": computed_at, "data": data}


@router.get(PATH, dependencies=[Depends(require_read)])
async def completion_readiness(response: Response) -> dict:
    from ..completion import read as CR
    response.headers["Cache-Control"] = "private, no-store"
    now = time.time()
    hit = _CACHE.get("main")
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        pool = await _pool()
        async with pool.acquire(timeout=5.0) as conn:
            async with conn.transaction(readonly=True):
                await conn.execute("SET LOCAL statement_timeout = %d"
                                   % STATEMENT_TIMEOUT_MS)
                data = await CR.read(conn, now=now)
    except Exception as exc:                                    # noqa: BLE001
        return envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:160]),
                        computed_at=now)
    out = envelope("OK", None, data=data, computed_at=now)
    _CACHE.clear()
    _CACHE["main"] = (now, out)
    return out
