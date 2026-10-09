"""PAPER TURNAROUND: GET /api/command/paper/turnaround (GET only, COMMAND auth
via agents_core.require_read). READ ONLY.

Per paper strategy on the main paper account: its lifecycle state
(ACTIVE_CHAMPION / ACTIVE_CHALLENGER / REDUCED_SIZE / SHADOW_ONLY /
QUARANTINED / RETIRED), the predeclared rules firing, rolling realized P&L,
$/capital-hour, drawdown and drawdown rate, execution cost, stale-management
rate, forward evidence, and capital shifted after a demotion; the advisory
capital-hour reallocation plan; the append-only transition log (migration
290); and the Kalshi read-path status (routing is Polymarket-only).

ANSWERS {label, authority: PAPER_ONLY_NO_CAPITAL_AUTHORITY, status, why,
computed_at, data}. A read that fails, or a missing migration, is
status UNAVAILABLE with data None -- never zeros.

One READ ONLY transaction under a statement timeout; this module holds no SQL
write and imports no order, venue, execution or funded module.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/paper/turnaround"
STATEMENT_TIMEOUT_MS = 8000
CACHE_S = 15.0
LABEL = "PAPER"
AUTHORITY = "PAPER_ONLY_NO_CAPITAL_AUTHORITY"
_CACHE: dict = {}


def envelope(status: str, why=None, *, data=None, computed_at=None) -> dict:
    return {"label": LABEL, "authority": AUTHORITY, "status": status,
            "why": why, "computed_at": computed_at, "data": data}


async def read(conn, *, account_id: str, now: float) -> dict:
    from .. import bettor_capital_eligibility as CE
    from .. import bettor_strategy_lifecycle as LC
    tr = conn.transaction() if conn.is_in_transaction() else \
        conn.transaction(readonly=True)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % STATEMENT_TIMEOUT_MS)
        got = await LC.turnaround_view(conn, account_id=account_id, now=now)
    finally:
        await tr.rollback()
    if got.get("status") != "OK":
        return envelope("UNAVAILABLE", got.get("why"), computed_at=now)
    got["kalshi_read_path"] = CE.kalshi_read_path_status()
    got["capital_eligibility_version"] = CE.VERSION
    return envelope("OK", None, data=got, computed_at=now)


@router.get(PATH, dependencies=[Depends(require_read)])
async def paper_turnaround() -> dict:
    from .. import bettor_paper_ledger as L
    now = time.time()
    hit = _CACHE.get("main")
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            out = await read(conn, account_id=await L.selected_account(conn), now=now)
    except Exception as exc:                                    # noqa: BLE001
        return envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:160]))
    _CACHE["main"] = (now, out)
    return out
