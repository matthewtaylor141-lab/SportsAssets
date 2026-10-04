"""RUNTIME LOOP HEALTH: every recurring loop, its lease, and whether it is
succeeding.

    GET /api/command/loop-health

Read-only, COMMAND session auth (agents_core.require_read), GET only. One
`BEGIN READ ONLY` transaction under a bounded statement_timeout; each source
in its own savepoint, so an absent table is named in `sources_missing` and
never aborts the rest (loop_health.read).

Per loop (loop_health.INVENTORY -- the API lifespan's loops and the workers
service's): process, cadence, CAPITAL-CRITICAL or not, the LEASE that makes
it a single writer (with the backends that hold its advisory key right now,
read from pg_locks), last start / success / error, lag, and the verdict:
HEALTHY / UNHEALTHY (no success within 3 x cadence; none since a start more
than 3 x cadence ago; the newest record a failed pass; or a critical armed
loop's writer lock held by no backend) / STARTING (started < 3 x cadence
ago, no success yet) / DISABLED (named) / EVENT_DRIVEN / UNAVAILABLE (named:
sources unreadable or absent). A heartbeat counts as a success only when its
status is in its writer's success vocabulary; no source ever reads as a
manufactured success.

WHAT THIS MODULE CANNOT DO. It imports no order, venue, execution, ledger,
paper or funded module (tests walk its imports), issues SELECTs only inside a
READ ONLY transaction and writes nothing.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from .. import loop_health as LH
from .agents_core import _pool, require_read

router = APIRouter()


#: THE DIAGNOSTIC MUST ANSWER UNDER THE STARVATION IT DIAGNOSES (R30A
#: review): a bounded wait for a pool connection, a 503 POOL_UNAVAILABLE when
#: none comes -- never a request hung behind a dry pool.
POOL_ACQUIRE_TIMEOUT_S = 2.0


async def _read_only(fn):
    pool = await _pool()
    async with pool.acquire(timeout=POOL_ACQUIRE_TIMEOUT_S) as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = %d"
                               % LH.STATEMENT_TIMEOUT_MS)
            return await fn(conn)


@router.get("/api/command/loop-health", dependencies=[Depends(require_read)])
async def loop_health(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        body = await _read_only(lambda c: LH.read(c))
    except HTTPException:
        raise
    except TimeoutError:
        raise HTTPException(status_code=503, detail={
            "reason": "POOL_UNAVAILABLE",
            "detail": "no database connection within %ss"
                      % POOL_ACQUIRE_TIMEOUT_S})
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "LOOP_HEALTH_READ_FAILED",
            "detail": type(exc).__name__})
    body["read_only"] = True
    return body
