"""THE PRODUCTION CANARY, SERVED: checkpoint / restart / boot identity /
cursor non-regression / retention / no-order.

    GET /api/command/canary
    GET /api/command/canary?before_max_id=..&before_rows=..&before_cursors=..
                            &before_boots=a,b,c

scripts/bettor_canary.py answers these with a DSN for the production
database, which nobody outside Render holds. This route answers them from
inside the API, with the SAME logic (sportsassets.ops_canary, which the
script also imports).

Read-only, COMMAND session auth (agents_core.require_read -> 401 without a
session), GET only. One `BEGIN READ ONLY` transaction with a bounded
`statement_timeout` and a bounded pool acquire; every source in its own
savepoint, so an absent table is NOT_ESTABLISHED with its reason and never
aborts the rest.

A CHECKPOINT-COMPARED RESTART WITHOUT A DSN. Read the route before the
restart; `checkpoint.compare_after_restart` carries four scalars. Pass them
back after the restart and `restart` applies the script's own rule
(ops_canary.restart_verdict): a NEW boot, every checkpointed journal row
still present, neither rows nor cursors going backwards.

WHAT THIS MODULE CANNOT DO, BY CONSTRUCTION. It imports no order, venue,
execution, ledger, paper or funded module (tests/test_ops_canary.py walks its
imports), issues only SELECTs inside a READ ONLY transaction and writes
nothing.
"""
from __future__ import annotations

import os
import re
import sys
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import ops_canary as OC
from .agents_core import _pool, require_read

router = APIRouter()

STATEMENT_TIMEOUT_MS = 5000
POOL_ACQUIRE_TIMEOUT_S = 2.0
_BOOT = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


async def _read_only(fn):
    pool = await _pool()
    async with pool.acquire(timeout=POOL_ACQUIRE_TIMEOUT_S) as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = %d"
                               % STATEMENT_TIMEOUT_MS)
            return await fn(conn)


def api_identity() -> dict:
    """This API process's own boot identity (/healthz's boot_id), read off
    the already-imported app module -- never imported from here."""
    a = sys.modules.get("sportsassets.api.app")
    boot = getattr(a, "_BOOT_ID", None)
    ts = getattr(a, "_BOOT_TS", None)
    return {"boot_id": boot,
            "uptime_s": round(max(0.0, time.time() - ts), 1)
            if isinstance(ts, (int, float)) else None,
            "commit_sha": (os.environ.get("RENDER_GIT_COMMIT") or None),
            "source": "the serving API process (/healthz boot_id)"}


def parse_before(max_id, rows, cursors, boots) -> dict | None:
    given = [x is not None for x in (max_id, rows, cursors)]
    if not any(given) and not boots:
        return None
    if not all(given):
        raise HTTPException(status_code=400, detail={
            "reason": "BEFORE_INCOMPLETE",
            "detail": "before_max_id, before_rows and before_cursors are "
                      "required together"})
    names = [b for b in (boots or "").split(",") if b]
    if any(not _BOOT.match(b) for b in names) or len(names) > 200:
        raise HTTPException(status_code=400, detail={
            "reason": "BEFORE_BOOTS_INVALID"})
    return {"max_id": max_id, "rows": rows, "cursors": cursors,
            "boots": names}


@router.get("/api/command/canary", dependencies=[Depends(require_read)])
async def canary(response: Response,
                 before_max_id: int | None = Query(None, ge=0),
                 before_rows: int | None = Query(None, ge=0),
                 before_cursors: int | None = Query(None, ge=0),
                 before_boots: str | None = Query(None, max_length=4000)
                 ) -> dict:
    response.headers["Cache-Control"] = "no-store"
    before = parse_before(before_max_id, before_rows, before_cursors,
                          before_boots)
    try:
        return await _read_only(lambda c: OC.build(c, before=before,
                                                   api=api_identity()))
    except HTTPException:
        raise
    except TimeoutError:
        raise HTTPException(status_code=503, detail={
            "reason": "POOL_UNAVAILABLE",
            "detail": "no database connection within %.0fs"
                      % POOL_ACQUIRE_TIMEOUT_S})
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "CANARY_READ_FAILED", "detail": type(exc).__name__})
