"""PAPER CAPITAL AUTHORITY -- THE ACCEPTANCE READ:
GET /api/command/paper/capital-authority (GET only, COMMAND auth via
agents_core.require_read). READ ONLY.

Per paper strategy on the main paper account (bettor_capital_authority.
acceptance_view): its lifecycle state; whether a PAPER entry is allowed now
and the exact refusal; new PAPER entries since the cutover (migration 305's
applied_at, or `?cutover=<epoch>`); the assertion counts (entries in
SHADOW_ONLY / QUARANTINED / RETIRED, entries without POSITIVE forward
economics -- both must be zero); the forward SHADOW sample and its results
(labelled SHADOW_COUNTERFACTUAL / NOT_REALIZED_PNL, never summed with PAPER);
realized and unrealized PAPER P&L; open exposure; settled forward results
since the cutover; the expected executable EV at the decision; and the
distinct contract / side blocker census. Actual profitability is
DEFERRED_FORWARD_EVIDENCE.

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
PATH = "/api/command/paper/capital-authority"
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
    from .. import bettor_capital_authority as CA
    tr = conn.transaction() if conn.is_in_transaction() else \
        conn.transaction(readonly=True)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % STATEMENT_TIMEOUT_MS)
        got = await CA.acceptance_view(conn, account_id=account_id, now=now,
                                       cutover=cutover)
    finally:
        await tr.rollback()
    if got.get("status") != "OK":
        return envelope("UNAVAILABLE", got.get("why"), computed_at=now)
    return envelope("OK", None, data=got, computed_at=now)


@router.get(PATH, dependencies=[Depends(require_read)])
async def paper_capital_authority(cutover: float | None = None) -> dict:
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
