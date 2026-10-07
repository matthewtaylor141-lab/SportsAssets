"""GET /api/command/paper/loss-attribution (GET only, COMMAND auth): the
PAPER book's realized loss attributed by cause (paper_loss_attribution).
History is immutable: a READ ONLY transaction with a statement timeout;
nothing here writes, sizes, orders or promotes."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/paper/loss-attribution"
STATEMENT_TIMEOUT_MS = 30000
CACHE_S = 300.0
_CACHE: dict = {}


@router.get(PATH, dependencies=[Depends(require_read)])
async def paper_loss_attribution():
    from .. import paper_loss_attribution as PLA
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
                out = await PLA.read(c, now=now)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE", "version": PLA.VERSION,
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    out = dict(out, status="OK", computed_at=now)
    _CACHE["v"] = (now, out)
    return out
