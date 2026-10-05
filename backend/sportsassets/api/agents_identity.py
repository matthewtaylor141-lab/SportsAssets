"""THE SEVEN AGENTS' IDENTITY, MEMORY, EXPERIENCE AND EVENTS -- read only.

    GET /api/command/agents/stream                     durable event stream
    GET /api/command/agents/{agent}/identity           the HQ2 identity card
    GET /api/command/agents/{agent}/identity/versions  every identity version
    GET /api/command/agents/{agent}/memories           paginated memories
    GET /api/command/agents/{agent}/experience         derived counts
    GET /api/command/agents/{agent}/relationships      evidence-backed ties
    GET /api/command/agents/{agent}/events             the agent's events
    GET /api/command/agents/{agent}/evaluation         evaluation matrix
    GET /api/command/agents/{agent}/context            role bundle + context

`{agent}` is a slug (derek, xavier, audrey, karen, allocator, archer, scout,
adriana) or an agent id (CHIEF_ALLOCATOR ...); anything else is 404
NOT_AN_AGENT. A historical alias (eddie, migration 266) reads the agent it
now names (archer) and the answer says so: alias_of + historical_alias.

Read-only, COMMAND session auth (agents_core.require_read: 401 without a
session), GET only. Every request runs inside ONE `BEGIN READ ONLY`
transaction with a bounded `statement_timeout`; each section runs in its own
savepoint and reports OK / EMPTY / ABSENT / UNAVAILABLE with its reason. A
missing value is null with a reason -- never a manufactured zero. Memory is
read by the human OPERATOR (the owner's Command session), which may see
every agent's memory; an AGENT never reads another agent's memory
(agents.agent_memory.private_memories enforces it).

WHAT THIS MODULE CANNOT DO, BY CONSTRUCTION. It imports no order, venue,
execution, ledger, paper or funded module (tests/test_agent_identity_api_
authority.py walks its imports and SQL), issues only SELECTs inside a READ
ONLY transaction and holds no write, approval, activation or capital path.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..agents import agent_activity as AA
from ..agents import agent_context as AC
from ..agents import agent_memory as M
from ..agents import identity as I
from ..agents import registry as R
from ..agent_work_state import read_work_states as _read_work_states
from .agents_core import _pool, require_read

router = APIRouter()

VERSION = "AGENT_IDENTITY_API_V1"
SCHEMA = "bettor.agent.identity.v1"
STATEMENT_TIMEOUT_MS = 5000


def _agent(agent: str) -> str:
    a = I.agent_of(agent)
    if a is None and R.historical_alias(agent):
        a = R.canonical_agent_id(agent)
    if a is None:
        raise HTTPException(status_code=404, detail={
            "reason": "NOT_AN_AGENT", "agents": sorted(I.BY_SLUG)})
    return a


def _tag(agent, got):
    """(266) an answer for a historical alias (/agents/eddie/...) is the
    canonical agent's, and says so: alias_of + historical_alias."""
    alias = R.historical_alias(agent) if agent else None
    if alias and isinstance(got, dict):
        return dict(got, alias_of=I.SLUGS[R.canonical_agent_id(alias)],
                    historical_alias=alias)
    return got


async def _read_only(fn):
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = %d"
                               % STATEMENT_TIMEOUT_MS)
            return await fn(conn)


async def _serve(response: Response, fn, reason: str):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _read_only(fn)
    except HTTPException:
        raise
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": reason, "detail": type(exc).__name__})


async def _section(conn, sections: dict, name: str, coro_fn, *,
                   default=None):
    try:
        async with conn.transaction():
            v = await coro_fn()
    except M.PrivateMemoryRefused:
        raise
    except Exception as exc:                                    # noqa: BLE001
        sections[name] = {"status": AA.UNAVAILABLE,
                          "why": "READ_FAILED:%s" % type(exc).__name__}
        return default
    sections[name] = {"status": AA.OK if v not in (None, [], {})
                      else AA.EMPTY, "why": None}
    return v


async def _state(conn, a: str, now: float) -> dict:
    """The agent's recorded state: its heartbeat row (agent_status), or for
    the Chief Allocator its latest intel run. Unknown is UNAVAILABLE."""
    if a == I.CHIEF_ALLOCATOR:
        if not await conn.fetchval(
                "SELECT to_regclass('intel_runs') IS NOT NULL"):
            return {"status": AA.ABSENT, "why": "TABLE_NOT_DEPLOYED:"
                    "intel_runs", "source": "intel_runs"}
        r = await conn.fetchrow(
            "SELECT run_id, status, started_at, finished_at FROM intel_runs "
            " WHERE component='ALLOCATOR' ORDER BY started_at DESC LIMIT 1")
        if r is None:
            return {"status": AA.UNAVAILABLE, "why": "NO_ALLOCATOR_RUN",
                    "source": "intel_runs"}
        at = M._ep(r["finished_at"] or r["started_at"])
        return {"status": AA.OK, "state": "RUNNING" if r["finished_at"]
                is None else "LAST_RUN_%s" % r["status"],
                "activity": "allocation run %s" % r["run_id"],
                "at": at, "age_s": round(now - at, 1),
                "source": "intel_runs",
                "evidence": {"kind": "intel_runs", "id": r["run_id"]}}
    if not await conn.fetchval(
            "SELECT to_regclass('agent_status') IS NOT NULL"):
        return {"status": AA.ABSENT, "why": "TABLE_NOT_DEPLOYED:agent_status",
                "source": "agent_status"}
    r = await conn.fetchrow(
        "SELECT state, activity, last_heartbeat_at, last_run_started_at, "
        "       last_run_finished_at, runs, errors FROM agent_status "
        " WHERE agent_id=$1", a)
    if r is None:
        return {"status": AA.UNAVAILABLE, "why": "NO_STATUS_ROW_RECORDED",
                "source": "agent_status"}
    hb = M._ep(r["last_heartbeat_at"])
    return {"status": AA.OK, "state": r["state"], "activity": r["activity"],
            "at": hb, "age_s": None if hb is None else round(now - hb, 1),
            "runs": int(r["runs"]), "errors": int(r["errors"]),
            "last_run_started_at": M._ep(r["last_run_started_at"]),
            "last_run_finished_at": M._ep(r["last_run_finished_at"]),
            "source": "agent_status",
            "note": "the recorded heartbeat state; the floor's derived desk "
                    "state is at /api/command/floor/%s" % I.SLUGS[a]}


async def _work_state(conn, a: str, now: float) -> dict:
    """agent_work_state's derivation for this agent (read only)."""
    got = await _read_work_states(conn, now=now)
    return dict(got["states"].get(a) or {}, sections={
        k: v for k, v in got["sections"].items()
        if v.get("status") not in ("OK", "EMPTY")})


async def _memory_summary(conn, a: str, sections: dict) -> dict:
    if not await M.has_schema(conn):
        sections["memory"] = {"status": AA.ABSENT, "why": M.R_NO_SCHEMA}
        return {"status": AA.UNAVAILABLE, "why": M.R_NO_SCHEMA,
                "count": None, "latest": [], "lessons": None,
                "self_corrections": None}
    counts = await _section(conn, sections, "memory.counts",
                            lambda: M.memory_counts(conn, a))
    if counts is None:
        return {"status": AA.UNAVAILABLE,
                "why": sections["memory.counts"]["why"], "count": None,
                "latest": [], "lessons": None, "self_corrections": None}
    latest = await _section(conn, sections, "memory.latest", lambda:
                            M.private_memories(conn, reader=M.OPERATOR,
                                               owner=a, limit=5)) or []
    lessons = await _section(conn, sections, "memory.lessons", lambda:
                             M.private_memories(conn, reader=M.OPERATOR,
                                                owner=a, kind=M.LESSON,
                                                limit=3)) or []
    legacy = await _section(conn, sections, "memory.paper_agent_lessons",
                            lambda: M.legacy_lessons(conn, reader=M.OPERATOR,
                                                     owner=a, limit=3)) or []
    corr = await _section(conn, sections, "memory.self_corrections", lambda:
                          M.private_memories(conn, reader=M.OPERATOR,
                                             owner=a,
                                             kind=M.SELF_CORRECTION,
                                             limit=3)) or []
    legacy_n = counts.get("paper_agent_lessons")
    total = counts["agent_memory_events"] + (legacy_n or 0)
    return {"status": AA.OK if total else AA.EMPTY,
            "why": None if total else "NO_MEMORY_PROMOTED_YET",
            "count": total,
            "by_source": {"agent_memory_events":
                          counts["agent_memory_events"],
                          "paper_agent_lessons": legacy_n},
            "active": counts["agent_memory_events"] - counts["superseded"],
            "superseded": counts["superseded"],
            "last_learned_at": counts["last_at"],
            "latest": latest,
            "lessons": {"count": counts["lessons"] + (legacy_n or 0),
                        "latest": lessons + legacy},
            "self_corrections": {"count": counts["self_corrections"],
                                 "latest": corr}}


async def build_identity(conn, agent: str, *, now: float | None = None
                         ) -> dict:
    a = I.agent_of(agent)
    at = float(now if now is not None else time.time())
    sections: dict = {}
    ident = None
    nver = None
    if await I.has_schema(conn):
        ident = await _section(conn, sections, "identity",
                               lambda: I.current_identity(conn, a))
        vers = await _section(conn, sections, "identity.versions",
                              lambda: I.identity_versions(conn, a))
        nver = None if vers is None else len(vers)
    else:
        sections["identity"] = {"status": AA.ABSENT, "why": I.R_NO_SCHEMA}
    if ident is None:
        ident = I.code_identity(a, why=sections.get("identity", {}).get(
            "why") or "NO_IDENTITY_ROW")
    voice = await _section(conn, sections, "voice",
                           lambda: I.read_voice(conn, a))
    if voice is None:
        voice = {"voice_profile_id": I.VOICE_SPEC[a]["voice_profile_id"],
                 "status": I.V_UNASSIGNED, "display_name": "UNASSIGNED",
                 "audio": {"status": I.V_UNAVAILABLE,
                           "reason": "VOICE_READ_FAILED"}}
    state = await _section(conn, sections, "state",
                           lambda: _state(conn, a, at)) or {
        "status": AA.UNAVAILABLE, "why": sections["state"]["why"]}
    # THE WORK STATE (owner R30): from recorded open work and blockers --
    # the heartbeat row alone said IDLE for Xavier while he owned positions
    work = await _section(conn, sections, "work_state",
                          lambda: _work_state(conn, a, at))
    state = dict(state, work_state=(work or {}).get("state"),
                 work_detail=(work or {}).get("detail") if work else
                 "UNAVAILABLE: %s" % sections["work_state"]["why"])
    memory = await _memory_summary(conn, a, sections)
    exp = await _section(conn, sections, "experience",
                         lambda: AA.experience(conn, a, now=at))
    rel = await _section(conn, sections, "relationships",
                         lambda: AA.relationships(conn, a, now=at))
    ev = await _section(conn, sections, "evaluation",
                        lambda: AA.evaluation(conn, a, now=at))
    b = AC.bundle(a)
    return {
        "schema": SCHEMA, "version": VERSION, "read_only": True,
        "agent": {"agent_id": a, "slug": I.SLUGS[a],
                  "display_name": ident["display_name"]},
        "identity": ident, "identity_versions": nver,
        "voice": voice, "state": state, "work": work, "memory": memory,
        "experience": exp if exp is not None else {
            "events": None, "status": AA.UNAVAILABLE,
            "why": sections["experience"]["why"]},
        "relationships": (rel or {}).get("relationships") if rel else None,
        "evaluation": (ev or {}).get("matrix") if ev else None,
        "authority": {"status": ident["authority_status"],
                      "may": ident["may"], "may_not": ident["may_not"],
                      "tools_allowed": b["tools"], "tools_denied":
                      b["denied"], "order_path": b["order_path"],
                      "grants_financial_authority": False,
                      "personality_grants_permission": False},
        "context_bundle": {k: b[k] for k in (
            "tools", "context_sources", "memory_scope", "requires",
            "focus")},
        "provenance": {
            "identity_source": ident.get("source"),
            "identity_version": ident.get("identity_version"),
            "content_sha": ident.get("content_sha"),
            "approved_by": ident.get("approved_by"),
            "approved_at": ident.get("approved_at"),
            "source_directive": ident.get("source_directive"),
            "source_ref": ident.get("source_ref"),
            "memory": "derived from durable records by agents.agent_memory "
                      "(never hand-entered); paper_agent_lessons read as "
                      "LESSON memories",
            "experience": "counts reproducible from the named tables",
            "migration": 224, "code": I.VERSION},
        "sections": sections, "computed_at": at,
        "disclosure": ("Identity, memory and experience grant no authority. "
                       "Unavailable values are null with a reason, never "
                       "zero. PAPER and ACTUAL are never summed.")}


# ── routes ─────────────────────────────────────────────────────────────

@router.get("/api/command/agents/stream",
            dependencies=[Depends(require_read)])
async def agents_stream(response: Response,
                        since: str | None = Query(default=None,
                                                  max_length=200),
                        agent: str | None = Query(default=None,
                                                  max_length=40),
                        limit: int = Query(default=50, ge=1, le=200)) -> dict:
    a = _agent(agent) if agent else None
    return await _serve(response, lambda conn: AA.events(
        conn, agent=a, since=since, limit=limit), "AGENT_EVENTS_READ_FAILED")


@router.get("/api/command/agents/{agent}/identity",
            dependencies=[Depends(require_read)])
async def agent_identity(agent: str, response: Response) -> dict:
    a = _agent(agent)
    got = await _serve(response, lambda conn: build_identity(conn, a),
                       "AGENT_IDENTITY_READ_FAILED")
    return _tag(agent, got)


@router.get("/api/command/agents/{agent}/identity/versions",
            dependencies=[Depends(require_read)])
async def agent_identity_versions(agent: str, response: Response) -> dict:
    a = _agent(agent)

    async def fn(conn):
        if not await I.has_schema(conn):
            return {"schema": "bettor.agent.identity_versions.v1",
                    "agent": a, "status": AA.UNAVAILABLE,
                    "why": I.R_NO_SCHEMA, "versions": None,
                    "code_spec": I.code_identity(a, why=I.R_NO_SCHEMA)}
        vers = await I.identity_versions(conn, a)
        return {"schema": "bettor.agent.identity_versions.v1", "agent": a,
                "status": AA.OK if vers else AA.EMPTY, "versions": vers,
                "immutable": True}
    got = await _serve(response, fn, "AGENT_IDENTITY_READ_FAILED")
    return _tag(agent, got)


@router.get("/api/command/agents/{agent}/memories",
            dependencies=[Depends(require_read)])
async def agent_memories(agent: str, response: Response,
                         kind: str | None = Query(default=None,
                                                  max_length=40),
                         before: float | None = Query(default=None),
                         limit: int = Query(default=50, ge=1, le=200),
                         include_superseded: bool = Query(default=True)
                         ) -> dict:
    a = _agent(agent)
    if kind is not None and kind not in M.KINDS:
        raise HTTPException(status_code=422, detail={
            "reason": M.R_UNKNOWN_KIND, "kinds": list(M.KINDS)})

    async def fn(conn):
        if not await M.has_schema(conn):
            return {"schema": "bettor.agent.memories.v1", "agent": a,
                    "status": AA.UNAVAILABLE, "why": M.R_NO_SCHEMA,
                    "memories": None, "count": None, "next_before": None}
        rows = await M.private_memories(
            conn, reader=M.OPERATOR, owner=a, kind=kind, limit=limit,
            before=before, include_superseded=include_superseded)
        legacy = [] if kind not in (None, M.LESSON) else \
            await M.legacy_lessons(conn, reader=M.OPERATOR, owner=a,
                                   limit=limit, before=before)
        allm = sorted(rows + legacy, key=lambda m: (
            -(m.get("learned_at") or 0), str(m["memory_id"])))[:limit]
        return {"schema": "bettor.agent.memories.v1", "agent": a,
                "status": AA.OK if allm else AA.EMPTY,
                "why": None if allm else "NO_MEMORY_PROMOTED_YET",
                "memories": allm, "count": len(allm),
                "next_before": allm[-1]["learned_at"]
                if len(allm) == limit else None,
                "reader": M.OPERATOR,
                "privacy": "the owner's Command session reads every agent's "
                           "memory; an agent reads only its own"}
    got = await _serve(response, fn, "AGENT_MEMORY_READ_FAILED")
    return _tag(agent, got)


@router.get("/api/command/agents/{agent}/experience",
            dependencies=[Depends(require_read)])
async def agent_experience(agent: str, response: Response) -> dict:
    a = _agent(agent)
    got = await _serve(response, lambda conn: AA.experience(conn, a),
                       "AGENT_EXPERIENCE_READ_FAILED")
    return _tag(agent, got)


@router.get("/api/command/agents/{agent}/relationships",
            dependencies=[Depends(require_read)])
async def agent_relationships(agent: str, response: Response) -> dict:
    a = _agent(agent)
    got = await _serve(response, lambda conn: AA.relationships(conn, a),
                       "AGENT_RELATIONSHIPS_READ_FAILED")
    return _tag(agent, got)


@router.get("/api/command/agents/{agent}/events",
            dependencies=[Depends(require_read)])
async def agent_events(agent: str, response: Response,
                       since: str | None = Query(default=None,
                                                 max_length=200),
                       limit: int = Query(default=50, ge=1, le=200)) -> dict:
    a = _agent(agent)
    got = await _serve(response, lambda conn: AA.events(
        conn, agent=a, since=since, limit=limit), "AGENT_EVENTS_READ_FAILED")
    return _tag(agent, got)


@router.get("/api/command/agents/{agent}/evaluation",
            dependencies=[Depends(require_read)])
async def agent_evaluation(agent: str, response: Response) -> dict:
    a = _agent(agent)
    got = await _serve(response, lambda conn: AA.evaluation(conn, a),
                       "AGENT_EVALUATION_READ_FAILED")
    return _tag(agent, got)


@router.get("/api/command/agents/{agent}/context",
            dependencies=[Depends(require_read)])
async def agent_context(agent: str, response: Response) -> dict:
    a = _agent(agent)

    async def fn(conn):
        got = await AC.build_context(conn, a)
        return dict(got, schema="bettor.agent.context.v1", agent=a,
                    read_only=True)
    got = await _serve(response, fn, "AGENT_CONTEXT_READ_FAILED")
    return _tag(agent, got)
