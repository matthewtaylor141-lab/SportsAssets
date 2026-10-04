"""THE HISTORICAL R30 DECISION REPLAY: GET /api/command/r30-replay (GET only,
COMMAND auth via agents_core.require_read). READ ONLY, BOUNDED.

    ?run_id=<r30rp_...>     a recorded run (default: the latest)
    ?limit=<1..100>         independent events returned (default 25)
    ?investment_only=true   only events whose decisions are all INVESTMENT
    ?decision_id=<id>       one replayed decision's full payload

ANSWERS {label: REPLAY_NOT_FORWARD_EVIDENCE, authority, status, why,
disclosure, runs: [the latest 20 runs], run: {run id, code sha, parameters,
params sha, clock range, summary}, events: [per independent event], decision}.
No run recorded -> status EMPTY with NO_REPLAY_RUN_RECORDED; migration 237
absent -> UNAVAILABLE with MIGRATION_237_NOT_APPLIED. Nothing is zero-filled.

Runs are recorded by `python -m sportsassets.scripts.r30_replay` (the replay
is computed outside the API process; this route only reads). Everything runs
in a READ ONLY transaction under a statement timeout and reads the
r30_replay_* tables only. This module imports no order, venue, execution or
funded module, writes nothing, and no replay result can promote production
policy.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..replay import AUTHORITY, DISCLOSURE, LABEL
from ..replay import store as ST
from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/r30-replay"
STATEMENT_TIMEOUT_MS = 6000


def envelope(status, why=None, **extra) -> dict:
    out = {"label": LABEL, "authority": AUTHORITY, "status": status,
           "why": why, "disclosure": DISCLOSURE,
           "promotion": "NONE: no replay result can promote production "
                        "policy"}
    out.update(extra)
    return out


@router.get(PATH, dependencies=[Depends(require_read)])
async def r30_replay(run_id: str | None = Query(default=None, max_length=40),
                     limit: int = Query(default=25, ge=1, le=100),
                     investment_only: bool = Query(default=False),
                     decision_id: str | None = Query(default=None,
                                                     max_length=200)) -> dict:
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                await conn.execute("SET LOCAL statement_timeout = %d"
                                   % STATEMENT_TIMEOUT_MS)
                if not await ST.schema(conn):
                    return envelope("UNAVAILABLE", ST.R_NO_SCHEMA, runs=[],
                                    run=None, events=[])
                got = await ST.read(conn, run_id=run_id, limit=limit,
                                    decision_id=decision_id,
                                    investment_only=investment_only)
    except Exception as exc:                                    # noqa: BLE001
        return envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:160]),
                        runs=[], run=None, events=[])
    status, why = got.pop("status"), got.pop("why")
    return envelope(status, why, **got)
