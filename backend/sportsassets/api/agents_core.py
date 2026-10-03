"""THE THREE AGENTS' SHARED READS (core): index, tasks, handoffs, decisions.

Read-only. Every route requires the command read credential (the contract's
`require_read` shim over `app.require_command`). A failed read is 503 (or an
UNAVAILABLE section naming the exception type), never an empty success; an
empty list is labelled EMPTY with the reason, never styled as success.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

router = APIRouter()


async def require_read(request: Request) -> str:
    from . import app as A
    return A.require_command(bt_command=request.cookies.get("bt_command", ""),
                             x_desk_token=request.headers.get("x-desk-token",
                                                              ""),
                             x_admin_token=request.headers.get(
                                 "x-admin-token", ""))


async def require_write(request: Request) -> str:
    from . import app as A
    return A.require_command_control(
        x_admin_token=request.headers.get("x-admin-token", ""),
        x_desk_token=request.headers.get("x-desk-token", ""),
        bt_control=request.cookies.get("bt_control", ""),
        bt_command=request.cookies.get("bt_command", ""))


async def _pool():
    from ..db import get_pool
    try:
        return await get_pool()
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "NO_DATABASE_POOL", "detail": type(exc).__name__})


async def _read_section(coro, *, empty_why: str, evidence_of=None) -> dict:
    """{"status","why","data","evidence"} for one read: OK, EMPTY (with the
    named reason) or UNAVAILABLE (the failed read's exception type)."""
    try:
        data = await coro
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE", "why": type(exc).__name__,
                "data": None, "evidence": []}
    if not data:
        return {"status": "EMPTY", "why": empty_why, "data": data,
                "evidence": []}
    ev = []
    if evidence_of is not None:
        for row in (data if isinstance(data, list) else [data]):
            ev.extend(evidence_of(row) or [])
    return {"status": "OK", "why": None, "data": data, "evidence": ev[:200]}


def _row_evidence(r: dict) -> list:
    return list(r.get("evidence") or r.get("evidence_links") or [])


async def _annotate_policy(conn, agents: list) -> None:
    """SAY WHAT `policy_version` MEANS, without changing it. CODE_DEFAULT is
    the code's fallback, never a management-approved policy; Xavier's row
    also carries its small-live management policy ARTIFACT (id, version,
    sha256, status -- READY_FOR_OWNER_APPROVAL until an owner approval
    record exists), which governs nothing at runtime. Never raises."""
    from ..agents import registry as R
    from ..agents import xavier_small_live_policy as XSP

    for a in agents:
        pv = a.get("policy_version")
        a["policy_version_approved"] = bool(
            pv and pv != R.SOURCE_CODE_DEFAULT)
        if pv == R.SOURCE_CODE_DEFAULT:
            a["policy_version_meaning"] = XSP.CODE_DEFAULT_MEANING
        if a.get("agent_id") == R.XAVIER:
            a["management_policy"] = await XSP.load_view(conn)


@router.get("/api/command/agents", dependencies=[Depends(require_read)])
async def agents_index(response: Response) -> dict:
    from ..agents import handoff as AH
    from ..agents import registry as R

    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        agents = []
        for aid in R.AGENTS:
            try:
                st = await R.status_of(conn, aid)
                agents.append(st if st is not None else {
                    "agent_id": aid, "state": None,
                    "why": "NOT_REGISTERED_THE_WRITER_HAS_NOT_STARTED"})
            except Exception as exc:                            # noqa: BLE001
                agents.append({"agent_id": aid, "state": None,
                               "status": "UNAVAILABLE",
                               "why": type(exc).__name__})
        await _annotate_policy(conn, agents)
        handoffs = await _read_section(
            AH.handoffs(conn, limit=20),
            empty_why="NO_CONFIRMED_ENTRY_FILL_HAS_BEEN_HANDED_TO_XAVIER",
            evidence_of=_row_evidence)
        tasks = await _read_section(
            R.tasks(conn, limit=20), empty_why="NO_AGENT_TASK_EXISTS",
            evidence_of=_row_evidence)
    return {"agents": agents, "handoffs": handoffs, "tasks": tasks,
            "read_at": time.time(), "read_only": True}


@router.get("/api/command/agents/tasks", dependencies=[Depends(require_read)])
async def agents_tasks(response: Response,
                       assignee: str | None = Query(default=None),
                       status: str | None = Query(default=None),
                       limit: int = Query(default=50, ge=1, le=500)) -> dict:
    from ..agents import registry as R

    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        sec = await _read_section(
            R.tasks(conn, assignee=assignee, status=status, limit=limit),
            empty_why="NO_AGENT_TASK_MATCHES", evidence_of=_row_evidence)
    return {"tasks": sec, "read_at": time.time(), "read_only": True}


@router.get("/api/command/agents/tasks/{task_id}",
            dependencies=[Depends(require_read)])
async def agents_task(task_id: str, response: Response) -> dict:
    from ..agents import registry as R

    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        try:
            got = await R.task(conn, task_id)
        except Exception as exc:                                # noqa: BLE001
            raise HTTPException(status_code=503, detail={
                "reason": R.R_READ_FAILED, "detail": type(exc).__name__})
    if got is None:
        raise HTTPException(status_code=404, detail={
            "reason": R.R_NO_SUCH_TASK, "task_id": task_id})
    return dict(got, read_at=time.time(), read_only=True)


@router.get("/api/command/agents/handoffs",
            dependencies=[Depends(require_read)])
async def agents_handoffs(response: Response,
                          entry_intent_id: str | None = Query(default=None),
                          limit: int = Query(default=50, ge=1, le=500)
                          ) -> dict:
    from ..agents import handoff as AH

    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        sec = await _read_section(
            AH.handoffs(conn, limit=limit, entry_intent_id=entry_intent_id),
            empty_why="NO_CONFIRMED_ENTRY_FILL_HAS_BEEN_HANDED_TO_XAVIER",
            evidence_of=_row_evidence)
    return {"handoffs": sec, "read_at": time.time(), "read_only": True,
            "rule": ("ownership transfers only on confirmed filled quantity "
                     "in bettor_funded_fills; outstanding_qty is the entry "
                     "order's remaining open obligation")}


@router.get("/api/command/agents/decisions",
            dependencies=[Depends(require_read)])
async def agents_decisions(response: Response,
                           agent: str | None = Query(default=None),
                           limit: int = Query(default=50, ge=1, le=500)
                           ) -> dict:
    from ..agents import registry as R

    response.headers["Cache-Control"] = "no-store"
    if agent is not None and agent.upper() not in R.AGENTS:
        raise HTTPException(status_code=400, detail={
            "reason": R.R_UNKNOWN_AGENT, "agent": agent})
    pool = await _pool()
    async with pool.acquire() as conn:
        sec = await _read_section(
            R.decisions(conn, agent_id=agent, limit=limit),
            empty_why="NO_AGENT_DECISION_IS_INDEXED",
            evidence_of=_row_evidence)
    return {"agent": None if agent is None else agent.upper(),
            "decisions": sec, "read_at": time.time(), "read_only": True}


@router.get("/api/command/agents/findings",
            dependencies=[Depends(require_read)])
async def agents_findings(response: Response,
                          stage: str | None = Query(default=None),
                          limit: int = Query(default=50, ge=1, le=500)
                          ) -> dict:
    """THE COLLABORATION LOOP (migration 203): each finding and its current
    stage. Read only; production_effect is NONE by construction."""
    from ..agents import collaboration_loop as CL

    response.headers["Cache-Control"] = "no-store"
    if stage is not None and stage not in CL.SEQ:
        raise HTTPException(status_code=400, detail={
            "reason": "THAT_IS_NOT_A_LOOP_STAGE", "stage": stage})
    pool = await _pool()
    async with pool.acquire() as conn:
        sec = await _read_section(
            CL.findings(conn, stage=stage, limit=limit),
            empty_why="NO_FINDING_HAS_ENTERED_THE_COLLABORATION_LOOP",
            evidence_of=lambda r: list(r.get("evidence_refs") or []))
    return {"findings": sec, "stages": list(CL.STAGES) + [CL.CLOSED],
            "production_effect": "NONE", "read_at": time.time(),
            "read_only": True}


@router.get("/api/command/agents/findings/{finding_id}",
            dependencies=[Depends(require_read)])
async def agents_finding(finding_id: str, response: Response) -> dict:
    from ..agents import collaboration_loop as CL

    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        try:
            got = await CL.finding(conn, finding_id)
        except Exception as exc:                                # noqa: BLE001
            raise HTTPException(status_code=503, detail={
                "reason": "FINDING_READ_FAILED",
                "detail": type(exc).__name__})
    if got is None:
        raise HTTPException(status_code=404, detail={
            "reason": CL.R_NO_SUCH_FINDING, "finding_id": finding_id})
    return dict(got, read_at=time.time(), read_only=True)
