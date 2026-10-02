"""INSTITUTIONAL REPORTS ON THE PAPER ACCOUNT: /api/command/paper/reports/*.

Read-only, COMMAND auth (`agents_core.require_read`, exactly as
/api/command/paper/experiment and /api/command/paper/operations), registered
in api/app.py beside api/command_paper. The computation lives in
`sportsassets.paper_institutional`; every response carries `as_of`,
`account_id` and `basis` ("Paper account — simulated execution on live market
data; reported to institutional standards"), and every section is
{"status": "OK" | "UNAVAILABLE", "reason", "data"} -- unavailable is never a
zero.

ROUTES
  GET /api/command/paper/reports/summary
  GET /api/command/paper/reports/pnl?period=1D|WTD|MTD|YTD|ALL&start=&end=
  GET /api/command/paper/reports/performance
  GET /api/command/paper/reports/positions
  GET /api/command/paper/reports/blotter?kind=trades|orders|both&limit&offset
  GET /api/command/paper/reports/attribution
  GET /api/command/paper/reports/risk
  GET /api/command/paper/reports/reconciliation
  GET /api/command/paper/reports/export/{name}.csv   (text/csv download)
      name: blotter_trades | blotter_orders | positions | daily_pnl
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .agents_core import require_read

router = APIRouter()

PREFIX = "/api/command/paper/reports"
DATE = r"^\d{4}-\d{2}-\d{2}$"


async def _pool():
    from ..db import get_pool
    try:
        return await get_pool()
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "NO_DATABASE_POOL", "detail": type(exc).__name__})


def _pi():
    from .. import paper_institutional as PI
    return PI


@router.get(PREFIX + "/summary", dependencies=[Depends(require_read)])
async def reports_summary() -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        return await _pi().summary_report(conn)


@router.get(PREFIX + "/pnl", dependencies=[Depends(require_read)])
async def reports_pnl(period: str = Query(
                          "ALL", pattern="^(1D|DTD|1W|WTD|7D|MTD|30D|YTD|ALL)$"),
                      start: str | None = Query(None, pattern=DATE),
                      end: str | None = Query(None, pattern=DATE)) -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        return await _pi().pnl_report(conn, period=period, start=start,
                                      end=end)


@router.get(PREFIX + "/performance", dependencies=[Depends(require_read)])
async def reports_performance() -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        return await _pi().performance_report(conn)


@router.get(PREFIX + "/positions", dependencies=[Depends(require_read)])
async def reports_positions() -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        return await _pi().positions_report(conn)


@router.get(PREFIX + "/blotter", dependencies=[Depends(require_read)])
async def reports_blotter(kind: str = Query(
                              "both", pattern="^(both|trades|orders)$"),
                          limit: int = Query(50, ge=1, le=500),
                          offset: int = Query(0, ge=0, le=10_000_000)) -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        return await _pi().blotter_report(conn, kind=kind, limit=limit,
                                          offset=offset)


@router.get(PREFIX + "/attribution", dependencies=[Depends(require_read)])
async def reports_attribution() -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        return await _pi().attribution_report(conn)


@router.get(PREFIX + "/risk", dependencies=[Depends(require_read)])
async def reports_risk() -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        return await _pi().risk_report(conn)


@router.get(PREFIX + "/reconciliation", dependencies=[Depends(require_read)])
async def reports_reconciliation() -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        return await _pi().reconciliation_report(conn)


@router.get(PREFIX + "/export/{name}.csv",
            dependencies=[Depends(require_read)])
async def reports_export(name: str):
    PI = _pi()
    if name not in PI.EXPORTS:
        raise HTTPException(status_code=404, detail={
            "reason": "UNKNOWN_EXPORT", "exports": list(PI.EXPORTS)})
    pool = await _pool()
    async with pool.acquire() as conn:
        try:
            fname, text = await PI.export_csv(conn, name)
        except RuntimeError as exc:
            # never an empty file in place of data that cannot be read
            raise HTTPException(status_code=503, detail={
                "reason": "EXPORT_UNAVAILABLE", "detail": str(exc)[:200]})
    return Response(content=text.encode("utf-8"),
                    media_type="text/csv; charset=utf-8",
                    headers={"content-disposition":
                             'attachment; filename="%s"' % fname,
                             "cache-control": "no-store"})
