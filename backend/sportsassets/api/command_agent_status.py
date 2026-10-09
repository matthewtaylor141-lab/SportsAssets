"""EVERY AGENT'S STATUS CONTRACT: latest action, status, input freshness,
next cycle, refusals and failures, per agent, loop and dedicated service.

    GET /api/command/agent-status            (?window_s=, default 3600)

Read-only, COMMAND session auth (agents_core.require_read), GET only. One
`BEGIN READ ONLY` transaction under a bounded statement_timeout; each source
in its own savepoint, so an absent table or a failed read is named in
`sections` and never aborts the rest (agent_status_contract.read). The whole
read is bounded (READ_BUDGET_S) and a dry pool answers 503 at once, as the
loop-health diagnostic does.

A SHORT SINGLE-FLIGHT CACHE (CACHE_S): concurrent and repeated reads inside
CACHE_S share one aggregate; the body keeps the instant it was MEASURED
(`now`, every age relative to it) and says how old it is (`cached`,
`cache_age_s`, `served_at`) -- a cached body is never restamped as fresh.

THE RESPONSE (see agent_status_contract for every field's rule):
  version, authority, now, window_s, statuses, rules,
  summary {subjects, by_status, by_kind, green, missing_fields,
           agents_not_green},
  subjects [{id, kind, name, role, process, capital_critical,
             heartbeat_sources, status, status_reasons, latest_action,
             input_freshness, next_cycle_at, next_cycle_why, next_cycle,
             refusals, failures, missing_fields, ...}],
  sections, loop_health_sources_missing, historical_aliases,
  read_only, cached, cache_age_s, served_at

WHAT THIS MODULE CANNOT DO. It imports no order, venue, execution, ledger,
paper or funded module (tests walk its imports), issues SELECTs only inside a
READ ONLY transaction and writes nothing. A GREEN here grants nothing.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import agent_status_contract as ASC
from .agents_core import _pool, require_read

router = APIRouter()

#: THE DIAGNOSTIC MUST ANSWER UNDER THE STARVATION IT DIAGNOSES (as
#: /api/command/loop-health): a bounded wait for a pool connection, then 503
POOL_ACQUIRE_TIMEOUT_S = 2.0
#: the whole read, every section included
READ_BUDGET_S = 15.0
CACHE_S = 10.0

_CACHE: dict = {}
_LOCK: dict = {"lock": None, "loop": None}


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if _LOCK["lock"] is None or _LOCK["loop"] is not loop:
        _LOCK["lock"] = asyncio.Lock()
        _LOCK["loop"] = loop
    return _LOCK["lock"]


async def _read_only(window_s: float) -> dict:
    pool = await _pool()
    async with pool.acquire(timeout=POOL_ACQUIRE_TIMEOUT_S) as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = %d"
                               % ASC.STATEMENT_TIMEOUT_MS)
            return await ASC.read(conn, window_s=window_s)


def _served(body: dict, measured_mono: float, cached: bool) -> dict:
    out = dict(body)
    out.update(read_only=True, cached=cached,
               cache_age_s=round(time.monotonic() - measured_mono, 3),
               served_at=time.time())
    return out


async def build(window_s: float) -> dict:
    """The body for one window: the cache when it is younger than CACHE_S,
    else ONE read (concurrent callers wait for it)."""
    w = ASC.bound_window(window_s)
    hit = _CACHE.get(w)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return _served(hit[1], hit[0], True)
    async with _lock():
        hit = _CACHE.get(w)
        if hit and time.monotonic() - hit[0] < CACHE_S:
            return _served(hit[1], hit[0], True)
        async with asyncio.timeout(READ_BUDGET_S):
            body = await _read_only(w)
        t = time.monotonic()
        _CACHE.clear()
        _CACHE[w] = (t, body)
        return _served(body, t, False)


@router.get("/api/command/agent-status", dependencies=[Depends(require_read)])
async def agent_status(response: Response,
                       window_s: float = Query(ASC.DEFAULT_WINDOW_S,
                                               ge=ASC.MIN_WINDOW_S,
                                               le=ASC.MAX_WINDOW_S)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await build(window_s)
    except HTTPException:
        raise
    except TimeoutError:
        raise HTTPException(status_code=503, detail={
            "reason": "POOL_UNAVAILABLE_OR_READ_OVER_BUDGET",
            "detail": "no database connection within %ss, or the read took "
                      "longer than %ss" % (POOL_ACQUIRE_TIMEOUT_S,
                                           READ_BUDGET_S)})
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "AGENT_STATUS_READ_FAILED",
            "detail": type(exc).__name__})
