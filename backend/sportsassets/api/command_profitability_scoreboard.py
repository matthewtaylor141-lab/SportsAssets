"""THE FORWARD PAPER / SHADOW PROFITABILITY SCOREBOARD:
GET /api/command/paper/profitability-scoreboard (GET only, COMMAND auth via
agents_core.require_read). READ ONLY.

On the main paper account since the profitability bind's cutover (migration
309's applied instant, or `?cutover=<epoch>`): the independent opportunity /
trade sample (distinct contract-sides / events), the expected after-cost EV,
realized PAPER P&L since the cutover (historical losses shown separately and
unchanged), EV per capital-hour, Brier / ECE on settled contracts, the
expected-vs-realized residual, subsystem attribution, max drawdown,
turnover, capital deployed, the p5 / p50 / p95 P&L forecast, the
counterfactual variant and shadow results (NOT_REALIZED_PNL), and the
verdict -- DEFERRED_FORWARD_EVIDENCE below the stated required sample
(bettor_paper_profitability_stack.scoreboard). The verdict promotes nothing.

ANSWERS {label, authority: PAPER_ONLY_NO_CAPITAL_AUTHORITY, status, why,
computed_at, data}. A failed read is status UNAVAILABLE with data None --
never zeros. One READ ONLY transaction under a statement timeout; this
module holds no SQL write and imports no order, venue, execution or funded
module.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/paper/profitability-scoreboard"
STATEMENT_TIMEOUT_MS = 8000
CACHE_S = 15.0
LABEL = "PAPER"
AUTHORITY = "PAPER_ONLY_NO_CAPITAL_AUTHORITY"
_CACHE: dict = {}


def envelope(status: str, why=None, *, data=None, computed_at=None) -> dict:
    return {"label": LABEL, "authority": AUTHORITY, "status": status,
            "why": why, "computed_at": computed_at, "data": data}


async def read(conn, *, account_id: str, now: float,
               cutover: float | None = None) -> dict:
    from .. import bettor_paper_profitability_stack as PSTACK
    tr = conn.transaction() if conn.is_in_transaction() else \
        conn.transaction(readonly=True)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % STATEMENT_TIMEOUT_MS)
        if not await conn.fetchval(
                "SELECT to_regclass('paper_profitability_evaluations') "
                "IS NOT NULL"):
            return envelope("UNAVAILABLE", "MIGRATION_309_NOT_APPLIED",
                            computed_at=now)
        try:
            got = await PSTACK.scoreboard_read(conn, account_id=account_id,
                                               now=now, cutover=cutover)
        except Exception as exc:                                # noqa: BLE001
            return envelope("UNAVAILABLE", "%s: %s" % (
                type(exc).__name__, str(exc)[:160]), computed_at=now)
    finally:
        await tr.rollback()
    return envelope("OK", None, data=got, computed_at=now)


@router.get(PATH, dependencies=[Depends(require_read)])
async def paper_profitability_scoreboard(cutover: float | None = None
                                         ) -> dict:
    from .. import bettor_paper_ledger as L
    now = time.time()
    key = ("main", cutover)
    hit = _CACHE.get(key)
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            out = await read(conn, account_id=L.ACCOUNT_ID, now=now,
                             cutover=cutover)
    except Exception as exc:                                    # noqa: BLE001
        return envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:160]))
    _CACHE.clear()
    _CACHE[key] = (now, out)
    return out
