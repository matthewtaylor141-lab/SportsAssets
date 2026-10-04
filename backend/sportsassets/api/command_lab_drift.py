"""LAB-F POLICY / MARKET DRIFT SENTINEL: GET /api/command/lab/drift
(GET only, COMMAND auth via agents_core.require_read). READ ONLY.

    ?as_of=<epoch>     the replay clock (default now; never in the future):
                       every row is read through the reader's one point-in-
                       time accessor (its own record stamp <= as_of)
    ?strategy=<key>    narrow the findings to one strategy (the multiple-
                       testing family is still the whole run)
    ?findings=all|tested|none   how much of the per-segment table to return

ANSWERS (the lab envelope):
    {label: RESEARCH, authority: SHADOW_RESEARCH_ONLY, status, why,
     computed_at, data: {as_of, question_h, strategies (each with its
     confidence_modifier, INFORMATION ONLY), windows, findings, work_items,
     research_tasks, methods, reference_rule, multiple_testing, context}}

Every number is computed by the PURE sportsassets.lab.drift_sentinel over
rows read by sportsassets.lab.drift_reads (SELECT only). Work items and the
research task are RETURNED, never enqueued or written here (the 226 queue
admits only Xavier's fresh-evidence kinds); the operator CLI records runs
into migration 244's append-only tables.

Everything runs inside a READ ONLY transaction under a statement timeout.
This module imports no order, venue, execution or funded module and writes
nothing.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, Query

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/lab/drift"
STATEMENT_TIMEOUT_MS = 8000
CACHE_S = 60.0
_CACHE: dict = {}


def _envelope(status: str, why, **kw) -> dict:
    return dict({"label": "RESEARCH", "authority": "SHADOW_RESEARCH_ONLY",
                 "production_effect": "NONE", "status": status, "why": why},
                **kw)


async def _read(conn, *, clock: float) -> dict:
    from ..lab import drift_reads as R
    from ..lab import drift_sentinel as DS
    nested = conn.is_in_transaction()
    tr = conn.transaction(readonly=not nested)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % STATEMENT_TIMEOUT_MS)
        data = await R.gather(conn, clock=clock, since=None,
                              ref_max_s=DS.DEFAULTS["ref_max_s"],
                              cmp_max_s=DS.DEFAULTS["cmp_max_s"])
    finally:
        await tr.rollback()
    return await asyncio.to_thread(DS.compute, data)


def shape(rep: dict, *, strategy: str | None, findings: str) -> dict:
    out = dict(rep)
    fs = rep["findings"]
    if strategy:
        fs = [f for f in fs if f["strategy"] == strategy]
        out["strategies"] = [s for s in rep["strategies"]
                             if s["strategy"] == strategy]
        out["work_items"] = [w for w in rep["work_items"]
                             if w["detail"]["strategy"] == strategy]
        out["research_tasks"] = [t for t in rep["research_tasks"]
                                 if t["strategy"] == strategy]
    if findings == "none":
        fs = []
    elif findings == "tested":
        fs = [f for f in fs if f["status"] != "UNAVAILABLE"]
    out["findings"] = fs
    return out


@router.get(PATH, dependencies=[Depends(require_read)])
async def lab_drift(
        as_of: float | None = Query(default=None, ge=0),
        strategy: str | None = Query(default=None, max_length=120),
        findings: str = Query(default="all",
                              pattern="^(all|tested|none)$")) -> dict:
    now = time.time()
    if as_of is not None and float(as_of) > now:
        return _envelope("UNAVAILABLE", "AS_OF_IS_IN_THE_FUTURE", data=None,
                         computed_at=now)
    clock = float(as_of) if as_of is not None else now
    key = round(clock, 0) if as_of is not None else "NOW"
    hit = _CACHE.get(key)
    if hit and now - hit[0] < CACHE_S:
        rep = hit[1]
    else:
        try:
            pool = await _pool()
            async with pool.acquire() as conn:
                rep = await _read(conn, clock=clock)
        except Exception as exc:                                # noqa: BLE001
            return _envelope("UNAVAILABLE", "%s: %s" % (
                type(exc).__name__, str(exc)[:160]), data=None,
                computed_at=now)
        _CACHE[key] = (now, rep)
    return _envelope("OK", None, computed_at=now,
                     data=shape(rep, strategy=strategy, findings=findings))
