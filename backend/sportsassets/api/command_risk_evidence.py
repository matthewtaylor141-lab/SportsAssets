"""R30C RISK EVIDENCE: the measured settlement-exception table and the
evidence-backed correlation graph. SHADOW INFORMATION ONLY.

    GET /api/command/settlement-exception-risk
        the MEASURED exception-risk table by sport / league / market type
        (postponement or suspension settled at a price, declared void, a tie
        the book does not price, venue-vs-book rule divergence), every cell's
        rate with its 95% upper bound, the cited external base rates, the
        conservative prior, and the expected exception cost the most recent
        canonical decisions carried (settlement_exception_risk)
        ?limit=<n>   recent canonical decisions shown (default 25)
    GET /api/command/correlation-graph
        the PAPER book's open positions, working orders and candidate
        opportunities (ENTERs not yet ordered, and the opportunities the
        correlation / concentration caps REFUSED, each with Allie's current
        treatment beside its shadow marginal) as a graph of shared
        settlement dependence, with the WORST-CASE (the caps' treatment) and
        EVIDENCED-CASE portfolio exposure side by side; the read runs in the
        read-only transaction, the CPU work after it in a worker thread

GET only, COMMAND read auth (agents_core.require_read). Each read runs in a
READ ONLY transaction under a statement timeout; nothing here writes, sends
an order, or changes a cap, haircut, threshold, size or the ENTER / REFUSE
rule. A failed read is HTTP 503 with a named reason -- never a page of zeros.
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .agents_core import require_read

router = APIRouter()
STATEMENT_TIMEOUT_MS = 8000


async def _pool():
    from ..db import get_pool
    try:
        return await get_pool()
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "NO_DATABASE_POOL", "detail": type(exc).__name__})


async def _read_only(fn, *, reason: str, **kw):
    pool = await _pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                await conn.execute("SET LOCAL statement_timeout = %d"
                                   % STATEMENT_TIMEOUT_MS)
                return await fn(conn, **kw)
    except HTTPException:
        raise
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": reason,
            "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200])})


async def _exception_payload(conn, *, limit: int) -> dict:
    from .. import settlement_exception_risk as SER
    now = time.time()
    table = await SER.measure(conn, now=now)
    recent = []
    for r in await conn.fetch(
            """SELECT intent_id, decision_id, strategy, sleeve, us_market_slug,
                      created_at, evidence->'settlement_exception_risk' AS c
                 FROM canonical_decision_intents
                ORDER BY created_at DESC LIMIT $1""", int(limit)):
        c = r["c"]
        if isinstance(c, str):
            try:
                c = json.loads(c)
            except ValueError:
                c = None
        recent.append({
            "intent_id": r["intent_id"], "decision_id": r["decision_id"],
            "strategy": r["strategy"], "sleeve": r["sleeve"],
            "us_market_slug": r["us_market_slug"],
            "created_at": r["created_at"].isoformat(),
            "settlement_exception_risk": c if c is not None else {
                "status": "NOT_RECORDED",
                "why": ("recorded before R30C: the intent carries no "
                        "settlement-exception component")}})
    return {"table": table, "recent_decisions": recent,
            "method": SER.describe(), "authority": SER.AUTHORITY,
            "gates_the_decision": False,
            "disclosure": (
                "Completed-game economics stay CONDITIONAL on ordinary "
                "completion. This table measures how often the venue settled "
                "otherwise and what that costs against the completed-game "
                "assumption; it is recorded on each canonical decision as "
                "shadow evidence and changes no ENTER / REFUSE rule.")}


@router.get("/api/command/settlement-exception-risk",
            dependencies=[Depends(require_read)])
async def settlement_exception_risk(
        response: Response,
        limit: int = Query(default=25, ge=1, le=200)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await _read_only(_exception_payload,
                            reason="SETTLEMENT_EXCEPTION_READ_FAILED",
                            limit=limit)


async def _graph_inputs(conn) -> dict:
    from .. import correlation_graph as CG
    return await CG.read(conn, now=time.time())


@router.get("/api/command/correlation-graph",
            dependencies=[Depends(require_read)])
async def correlation_graph(response: Response) -> dict:
    """The READ runs inside the read-only transaction; the graph's CPU work
    (pairwise edges, the Monte Carlo portfolios) runs AFTER it, in a worker
    thread, so it neither holds the connection nor blocks the event loop --
    the statement timeout bounds SQL only, never Python (review, R30C)."""
    from .. import correlation_graph as CG
    response.headers["Cache-Control"] = "no-store"
    inputs = await _read_only(_graph_inputs,
                              reason="CORRELATION_GRAPH_READ_FAILED")
    try:
        got = await asyncio.to_thread(CG.compute, inputs)
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "CORRELATION_GRAPH_COMPUTE_FAILED",
            "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
    if not got.get("ok"):
        raise HTTPException(status_code=503, detail={
            "reason": got.get("refusal") or "CORRELATION_GRAPH_UNAVAILABLE"})
    return got
