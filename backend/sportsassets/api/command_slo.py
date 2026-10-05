"""RUNTIME SLOs: eight service levels with target, measured value, window and
status OK / BREACH / UNAVAILABLE(reason).

    GET /api/command/slo

Read-only, COMMAND session auth (agents_core.require_read), GET only. ONE
`BEGIN READ ONLY` transaction under a bounded statement_timeout
(runtime_slo.STATEMENT_TIMEOUT_MS); each SLO in its own savepoint, so a
missing table, an unrecorded cutover or an empty window is that SLO's
UNAVAILABLE with its reason and never aborts the others (runtime_slo).

The SLOs: feed freshness (Pinnacle age at decision p50 / p90 vs 30 s),
decision latency (provider change -> reactive evaluation finished vs the
scheduler's own 12 s deadline), open positions without a fresh Xavier
review, agent task age (agent_work_requests past expiry), reconciliation age
(open ACTUAL positions), Opportunity Score age (the SCORES component), parity
divergence (LOGIC_DIVERGENCE since the cutover) and release state (API SHA ==
workers SHA == the latest release receipt).

WHAT THIS MODULE CANNOT DO. It imports no order, venue, execution, ledger,
paper or funded module (tests walk its imports), issues SELECTs only inside a
READ ONLY transaction and writes nothing. No target here is a trading
threshold; each is the system's own recorded bound.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from .. import runtime_slo as SLO
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
                               % SLO.STATEMENT_TIMEOUT_MS)
            return await fn(conn)


@router.get("/api/command/slo", dependencies=[Depends(require_read)])
async def runtime_slos(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _read_only(lambda c: SLO.read_slos(c))
    except HTTPException:
        raise
    except TimeoutError:
        raise HTTPException(status_code=503, detail={
            "reason": "POOL_UNAVAILABLE",
            "detail": "no database connection within %ss"
                      % POOL_ACQUIRE_TIMEOUT_S})
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "SLO_READ_FAILED", "detail": type(exc).__name__})
