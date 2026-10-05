"""THE POSITION ROOMS: /api/command/positions/rooms and /room/{group_key}.

GET only, COMMAND read auth (agents_core.require_read: 401 without a
session). Each read runs inside a READ ONLY transaction with a bounded
statement timeout, so nothing on this path can write; the assembly lives in
`sportsassets.position_rooms`, which imports no order, venue-submit, funded
or paper-writer module (tests/test_position_rooms_authority.py).

  GET /api/command/positions/rooms?book=PAPER|ACTUAL
      every active position room of that book; ACTUAL is split by venue
      (POLYMARKET, KALSHI) and never summed, nor summed with PAPER
  GET /api/command/positions/room/{group_key}
      one room in full: legs, orders, if-it-fills, scenarios, Xavier,
      Archer, Audrey, Karen, game state

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


async def _read_only(fn, **kw):
    from .. import position_rooms as PR
    pool = await _pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                await conn.execute("SET LOCAL statement_timeout = %d"
                                   % int(PR.STATEMENT_TIMEOUT_MS))
                return await fn(conn, **kw)
    except HTTPException:
        raise
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "POSITION_ROOM_READ_FAILED",
            "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200])})


@router.get("/api/command/positions/rooms",
            dependencies=[Depends(require_read)])
async def position_rooms(response: Response,
                         book: str = Query("PAPER")) -> dict:
    from .. import position_rooms as PR
    b = str(book or "").upper()
    if b not in PR.BOOK_VENUES:
        raise HTTPException(status_code=400, detail={
            "reason": "UNKNOWN_BOOK", "allowed": sorted(PR.BOOK_VENUES)})
    response.headers["Cache-Control"] = "no-store"
    return await _read_only(PR.rooms_payload, book=b)


@router.get("/api/command/positions/room/{group_key}",
            dependencies=[Depends(require_read)])
async def position_room(group_key: str, response: Response) -> dict:
    from .. import position_rooms as PR
    if PR.parse_group_key(group_key) is None:
        raise HTTPException(status_code=400, detail={
            "reason": "MALFORMED_GROUP_KEY",
            "form": "<PAPER|ACTUAL-POLYMARKET|ACTUAL-KALSHI>:<EVT|MKT>:<id>"})
    response.headers["Cache-Control"] = "no-store"
    got = await _read_only(PR.room_payload, key=group_key)
    if got is None:
        raise HTTPException(status_code=404, detail={
            "reason": "NO_SUCH_POSITION_ROOM",
            "why": ("no position, order or fill of that book currently "
                    "resolves to this room key")})
    return got
