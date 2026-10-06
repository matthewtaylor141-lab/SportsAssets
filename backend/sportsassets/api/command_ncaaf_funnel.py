"""GET /api/command/ncaaf-funnel (GET only, COMMAND auth): the NCAAF funnel
stage by stage with the exact reason for every loss (ncaaf_funnel). A READ
ONLY transaction with a statement timeout; nothing here writes, sizes,
orders or promotes."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/ncaaf-funnel"
STATEMENT_TIMEOUT_MS = 15000
CACHE_S = 60.0
_CACHE: dict = {}


@router.get(PATH, dependencies=[Depends(require_read)])
async def ncaaf_funnel():
    from .. import ncaaf_funnel as NF
    now = time.time()
    hit = _CACHE.get("v")
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    pool = await _pool()
    try:
        async with pool.acquire() as c:
            async with c.transaction(readonly=True):
                await c.execute("SET LOCAL statement_timeout = %d"
                                % STATEMENT_TIMEOUT_MS)
                out = await NF.read(c, now=now)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE", "version": NF.VERSION,
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    out = dict(out, status="OK")
    _CACHE["v"] = (now, out)
    return out
