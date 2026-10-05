"""AGENT SCORECARDS (owner R30 program section 18): GET
/api/command/agent-scorecards. GET only, COMMAND auth
(agents_core.require_read). READ ONLY.

QUERY: ?agent=DEREK|XAVIER|AUDREY|KAREN|CHIEF_ALLOCATOR|EDDIE|SCOUT (one
card; default all seven) and ?window_days=1..30 (default 7; outside the
range is a 422, never a silent clamp).

ANSWERS:
    {status, why, schema, version, computed_at, window {start, end,
     seconds, basis}, min_n, metric_order, statuses, single_score: null,
     never_scored, authority, disclosure,
     agents: [{agent,
               metrics: [{agent, metric, value, unit, n, window, status,
                          reason, direction, definition, source, detail}
                         x 10, in metric_order],
               memory_usefulness: {status, reason, decisions_with_lessons,
                                   lessons_retrieved, evaluations[...]}}]}

Every figure comes from agents.agent_scorecards over the recorded rows; an
unmeasurable figure is UNAVAILABLE with its reason, never a zero; a figure
on fewer than min_n samples is SMALL_SAMPLE. One READ ONLY transaction under
a statement timeout. Nothing here writes, changes a weight or a threshold,
or approves anything; it imports no order, venue, execution, funded or paper
module.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/agent-scorecards"
STATEMENT_TIMEOUT_MS = 5000
CACHE_S = 30.0
_CACHE: dict = {}
DISCLOSURE = (
    "DECISION AND ECONOMIC QUALITY, NEVER ACTIVITY: no heartbeat, run, "
    "message or review count is a score. Each metric stands alone with its "
    "own n and window; SMALL_SAMPLE is shown and concludes nothing; "
    "UNAVAILABLE carries its reason and is never a zero. False approvals "
    "are a LOWER BOUND (only defects a rule names are counted). The twin's "
    "value rows are counterfactual research over the twin's own window. "
    "No authority: nothing here trades, approves, sizes or changes a "
    "threshold.")


def _clean(v):
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if v.__class__.__name__ == "Decimal":
        return float(v)
    return v


async def read(conn, *, now: float, window_days: int = 7,
               agent: str | None = None) -> dict:
    """The cards inside a READ ONLY transaction (nested: a savepoint)."""
    from ..agents import agent_scorecards as S
    nested = conn.is_in_transaction()
    tr = conn.transaction(readonly=not nested)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % STATEMENT_TIMEOUT_MS)
        got = await S.scorecards(conn, now=now, window_days=window_days,
                                 agents=None if agent is None else (agent,))
    finally:
        await tr.rollback()
    return _clean(got)


def _envelope(got: dict) -> dict:
    return dict(got, status="OK", why=None, disclosure=DISCLOSURE)


@router.get(PATH, dependencies=[Depends(require_read)])
async def agent_scorecards(
        agent: str | None = Query(None, max_length=32),
        window_days: int = Query(7, ge=1, le=30)) -> dict:
    from ..agents import agent_scorecards as S
    if agent is not None:
        agent = agent.strip().upper()
        if agent not in S.AGENTS:
            raise HTTPException(400, detail={"reason": "UNKNOWN_AGENT",
                                             "allowed": list(S.AGENTS)})
    now = time.time()
    key = (agent, int(window_days))
    hit = _CACHE.get(key)
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            got = await read(conn, now=now, window_days=window_days,
                             agent=agent)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE",
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                "computed_at": now, "agents": [], "disclosure": DISCLOSURE,
                "authority": "NONE_RECORDS_ONLY"}
    out = _envelope(got)
    _CACHE[key] = (now, out)
    return out
