"""LAB-B: AGENT CITATION / CLAIM INTEGRITY -- the read-only surface.

    GET /api/command/lab/citation-integrity
        ?days=7           trailing window, 1..30 days
        &agent=XAVIER     one agent (default: every chat agent)
        &retrospective=1  also re-verify the stored answers in the window
                          (agent_chat_messages, migration 180) against the
                          facts they cited
        &limit=500        bound on the stored answers re-verified

Returns each agent's six citation metrics (lab/citation_integrity_store
.citation_metrics -- the same function r30b's economic scorecards read), the
most recent failing verdicts, the verdict and action vocabulary and, when
asked, the retrospective tally. COMMAND auth, one READ ONLY transaction under
a statement timeout. Nothing here writes, and nothing here is an input to an
order, a size, a limit, a threshold, a policy or an approval: authority
SHADOW_RESEARCH_ONLY.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query

from ..lab import citation_integrity as CI
from ..lab import citation_integrity_store as CIS
from .agents_core import _pool, require_read

router = APIRouter()
STATEMENT_TIMEOUT_MS = 8000
AGENTS = ("DEREK", "XAVIER", "AUDREY", "KAREN", "EDDIE", "SCOUT")
#: the routes use this clock; tests substitute a controlled one
_clock = time.time


@router.get("/api/command/lab/citation-integrity")
async def citation_integrity(_auth: str = Depends(require_read),
                             days: int = Query(default=7, ge=1, le=30),
                             agent: str | None = Query(default=None,
                                                       max_length=32),
                             retrospective: bool = Query(default=False),
                             limit: int = Query(default=500, ge=1,
                                                le=2000)) -> dict:
    ag = None
    if agent:
        ag = agent.strip().upper()
        if ag not in AGENTS:
            raise HTTPException(404, detail={"reason": "UNKNOWN_AGENT",
                                             "agents": list(AGENTS)})
    now = float(_clock())
    window_s = days * 86400.0
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = %d"
                               % STATEMENT_TIMEOUT_MS)
            present = await CIS.schema(conn)
            cards = await CIS.scorecards(conn, agents=[ag] if ag else AGENTS,
                                         window_s=window_s, now=now)
            failures = await CIS.recent_failures(conn, now=now,
                                                 window_s=window_s)
            retro = None
            if retrospective:
                retro = await CIS.retrospective(
                    conn, clock=now, since=now - window_s, agent=ag,
                    limit=limit)
    return {"schema": CIS.SCHEMA, "version": CI.VERSION,
            "authority": CI.AUTHORITY,
            "window": CIS.window(now, window_s),
            "ledger": {"present": present,
                       "why": None if present else CIS.R_NO_SCHEMA},
            "metrics": list(CIS.CITATION_METRICS),
            "agents": cards, "recent_failures": failures,
            "verdicts": list(CI.VERDICTS), "actions": list(CI.ACTIONS),
            "retrospective": retro,
            "disclosure": ("Agent-quality measurement only: a verdict "
                           "changes the chat prose it verified (re-cited, "
                           "discarded for the records-only answer, or "
                           "followed by an integrity statement) and nothing "
                           "else. No order, capital, limit, threshold, "
                           "policy or approval reads it.")}
