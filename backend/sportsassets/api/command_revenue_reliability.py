"""GET /api/command/revenue-readiness -- BETTOR's economic self-awareness.

One authenticated, READ-ONLY readback (Revenue Reliability Stack V1): agent
economic certification, CASH-incumbent strategy tournament, sport x family x
regime matrix, reliability / capacity / correlation plan, counterfactual
agent value-add, contribution waterfall and the daily revenue-readiness
brief. It changes nothing: no order, no lifecycle row, no limit, no
authority. Certification never grants authority; CASH is a valid champion.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Response

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/revenue-readiness"
CACHE_S = 60.0
LABEL = "PAPER_SHADOW"
AUTHORITY = "READBACK_ONLY_NO_ORDER_NO_CAPITAL_AUTHORITY"
_CACHE: dict = {}


def envelope(status: str, why=None, *, data=None, computed_at=None, **extra) -> dict:
    return {"label": LABEL, "authority": AUTHORITY, "status": status, "why": why,
            "computed_at": computed_at, "data": data, **extra}


@router.get(PATH, dependencies=[Depends(require_read)])
async def revenue_readiness(response: Response) -> dict:
    from .. import bettor_paper_ledger as L
    from ..revenue_reliability import read as RR
    response.headers["Cache-Control"] = "private, no-store"
    now = time.time()
    hit = _CACHE.get("main")
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            got = await RR.read(conn, account_id=await L.selected_account(conn), now=now)
    except Exception as exc:                                        # noqa: BLE001
        return envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__, str(exc)[:160]), computed_at=now)
    if got.get("status") != "OK":
        return envelope("UNAVAILABLE", got.get("why"), computed_at=now, missing=got.get("missing"))
    out = envelope("OK", None, data=got["data"], computed_at=now)
    _CACHE.clear()
    _CACHE["main"] = (now, out)
    return out
