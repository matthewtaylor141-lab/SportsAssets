"""COVERAGE, POSTMORTEMS AND QUALITY: /api/command/{coverage,postmortems,quality}.

Read-only, COMMAND auth (agents_core.require_read). Each route reads what
Audrey's scheduled steps persisted (migration 209) or computes a read-only
figure from production tables; none writes, and none can place, cancel or
change an order, a limit or a policy.

  GET /api/command/coverage      the provider -> fill funnel per league per
                                 day (?tz=UTC|America/New_York, ?days=1..60)
                                 and the collapse alerts Audrey raised; plus
                                 (cand24, additive) `league_status` -- one of
                                 HEALTHY / REFUSING_BY_POLICY /
                                 EXPLICITLY_UNSUPPORTED / COVERAGE_INCIDENT /
                                 UNAVAILABLE per league, with its reason --
                                 and `nfl_reconciliation`, today's NFL games
                                 expected (venue listing) vs observed, stage
                                 by stage, with the MISSING list
  GET /api/command/postmortems   every closed position's decomposition,
                                 PAPER and ACTUAL separately (?book=, ?limit=)
  GET /api/command/quality       the five-domain quality scorecard

A failed read is HTTP 503 with a named reason -- never a page of zeros.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .agents_core import require_read

router = APIRouter()


async def _pool():
    from ..db import get_pool
    try:
        return await get_pool()
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "NO_DATABASE_POOL", "detail": type(exc).__name__})


async def _read(fn, **kw) -> dict:
    pool = await _pool()
    try:
        async with pool.acquire() as conn:
            return await fn(conn, **kw)
    except HTTPException:
        raise
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "READ_FAILED",
            "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200])})


@router.get("/api/command/coverage", dependencies=[Depends(require_read)])
async def command_coverage(
        response: Response, tz: str = Query("America/New_York"),
        days: int = Query(7, ge=1, le=60)) -> dict:
    from ..agents import coverage_integrity as COV
    if tz not in COV.TIMEZONES:
        raise HTTPException(status_code=400, detail={
            "reason": "UNSUPPORTED_TIMEZONE", "allowed": list(COV.TIMEZONES)})
    response.headers["Cache-Control"] = "no-store"
    return await _read(COV.coverage_payload, tz=tz, days=days)


@router.get("/api/command/postmortems", dependencies=[Depends(require_read)])
async def command_postmortems(
        response: Response, book: str | None = Query(None),
        limit: int = Query(100, ge=1, le=1000)) -> dict:
    from ..agents import postmortems as PM
    if book is not None and book.upper() not in ("PAPER", "ACTUAL"):
        raise HTTPException(status_code=400, detail={
            "reason": "UNKNOWN_BOOK", "allowed": ["paper", "actual"]})
    response.headers["Cache-Control"] = "no-store"
    return await _read(PM.postmortems_payload, book=book, limit=limit)


@router.get("/api/command/quality", dependencies=[Depends(require_read)])
async def command_quality(response: Response) -> dict:
    from ..agents import quality_scorecard as Q
    response.headers["Cache-Control"] = "no-store"
    return await _read(Q.scorecard)
