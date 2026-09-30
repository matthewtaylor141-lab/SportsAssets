"""AUDREY'S WORKSPACE: GET /api/command/agents/audrey (+ report and
candidate readers) and ONE write -- a named person approving an
APPROVAL_READY improvement candidate.

Every section follows the shared workspace contract:
    {"status": "OK"|"EMPTY"|"UNAVAILABLE", "why": str|None, "data": ...,
     "evidence": [{"kind", "id", "href"}...]}
EMPTY names its reason; UNAVAILABLE names the failed read's exception type.
An empty table is never shown as success. The provider section says
whether an AI provider key is configured -- presence only, never a value.
"""
from __future__ import annotations

import contextlib
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..agents import audrey_audit as AA
from ..agents import improvement as IMP

router = APIRouter()

OK, EMPTY, UNAVAILABLE = "OK", "EMPTY", "UNAVAILABLE"
CHAT_NOT_MERGED = "chat stream not merged"
PROVIDER_KEY_ENV = "ANTHROPIC_API_KEY"


async def require_read(request: Request) -> str:
    from . import app as A
    return A.require_command(
        bt_command=request.cookies.get("bt_command", ""),
        x_desk_token=request.headers.get("x-desk-token", ""),
        x_admin_token=request.headers.get("x-admin-token", ""))


async def require_write(request: Request) -> str:
    from . import app as A
    return A.require_command_control(
        x_admin_token=request.headers.get("x-admin-token", ""),
        x_desk_token=request.headers.get("x-desk-token", ""),
        bt_control=request.cookies.get("bt_control", ""),
        bt_command=request.cookies.get("bt_command", ""))


@contextlib.asynccontextmanager
async def _connection():
    from ..db import get_pool
    try:
        pool = await get_pool()
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "NO_DATABASE_POOL", "detail": type(exc).__name__})
    async with pool.acquire() as conn:
        yield conn


def _section(status: str, data: Any = None, *, why: str | None = None,
             evidence: list | None = None) -> dict:
    return {"status": status, "why": why, "data": data,
            "evidence": evidence or []}


async def _guard(fn, *a, **k) -> dict:
    try:
        return await fn(*a, **k)
    except Exception as exc:                                    # noqa: BLE001
        return _section(UNAVAILABLE, why=type(exc).__name__)


def _ev(kind: str, id_: str, href: str) -> dict:
    return {"kind": kind, "id": str(id_), "href": href}


REPORT_HREF = "/api/command/agents/audrey/reports/%s"
CAND_HREF = "/api/command/agents/audrey/candidates/%s"


# ═════════════════════════════════════════════════════════════════════
# SECTIONS
# ═════════════════════════════════════════════════════════════════════

async def s_status(conn) -> dict:
    row = None
    if await IMP._regclass(conn, "agent_status"):
        r = await conn.fetchrow(
            "SELECT * FROM agent_status WHERE agent_id='AUDREY'")
        row = dict(r) if r is not None else None
    wm = None
    if await AA.has_schema(conn):
        r = await conn.fetchrow(
            "SELECT * FROM audrey_audit_watermarks WHERE scope=$1", AA.SCOPE)
        wm = dict(r) if r is not None else None
    name, _, note = AA.audit_timezone()
    data = {"agent_status": row, "watermark": wm, "timezone": name,
            "day_boundary": AA.DAY_BOUNDARY, "timezone_note": note}
    if row is None and wm is None:
        return _section(EMPTY, data, why=(
            "Audrey has not run: no agent_status row and no audit "
            "watermark"))
    return _section(OK, data)


async def s_versions(conn) -> dict:
    data = {"audit_version": AA.VERSION, "improvement_version": IMP.VERSION,
            "code_version": os.getenv("RENDER_GIT_COMMIT") or "UNKNOWN",
            "change_classes": IMP.describe_registry()}
    if await IMP._regclass(conn, "agent_policy_versions"):
        data["policies"] = [dict(r) for r in await conn.fetch(
            "SELECT agent_id, policy_key, version, state, created_by, "
            " approved_by FROM agent_policy_versions "
            " ORDER BY agent_id, policy_key, created_at DESC")]
    else:
        data["policies"] = {"status": UNAVAILABLE,
                            "why": "agent_policy_versions is absent"}
    return _section(OK, data)


async def s_reports(conn) -> dict:
    if not await AA.has_schema(conn):
        return _section(UNAVAILABLE, why=AA.R_SCHEMA)
    rows = await AA.reports(conn, limit=14)
    if not rows:
        return _section(EMPTY, [], why="no daily report has been written")
    return _section(OK, rows, evidence=[
        _ev("audrey_audit_reports", r["report_id"],
            REPORT_HREF % r["report_id"]) for r in rows])


async def _latest(conn) -> dict | None:
    rows = await AA.reports(conn, limit=1)
    if not rows:
        return None
    return await AA.report(conn, rows[0]["report_id"])


async def s_findings(conn) -> dict:
    if not await AA.has_schema(conn):
        return _section(UNAVAILABLE, why=AA.R_SCHEMA)
    rep = await _latest(conn)
    if rep is None:
        return _section(EMPTY, why="no daily report has been written")
    body = rep["report"] or {}
    data = {"report_id": rep["report_id"], "version": rep["version"],
            "data_quality": body.get("data_quality"),
            "improvements": body.get("improvements"),
            "derek_quality": ((body.get("derek") or {}).get("entries")
                              or {}).get("quality"),
            "xavier_quality": (body.get("xavier") or {}).get("quality")}
    return _section(OK, data, evidence=[_ev(
        "audrey_audit_reports", rep["report_id"],
        REPORT_HREF % rep["report_id"])])


async def s_outcomes(conn) -> dict:
    if not await AA.has_schema(conn):
        return _section(UNAVAILABLE, why=AA.R_SCHEMA)
    rep = await _latest(conn)
    if rep is None:
        return _section(EMPTY, why="no daily report has been written")
    body = rep["report"] or {}
    ev = body.get("evidence") or {}
    data = {"states": (body.get("outcomes") or {}).get("states"),
            "evidence_categories": {
                c: {k: (ev.get(c) or {}).get(k) for k in (
                    "count", "fixtures", "usd", "per_contract", "what",
                    "could_have_filled", "reasons")}
                for c in AA.CATEGORIES},
            "never_merged": True,
            "attribution": (body.get("positions") or {}).get("attribution"),
            "book": {k: (body.get("book") or {}).get(k) for k in (
                "realized", "provisional", "unrealized", "fees_and_costs",
                "drawdown", "exposure", "residual_inventory")}}
    return _section(OK, data, evidence=[_ev(
        "audrey_audit_reports", rep["report_id"],
        REPORT_HREF % rep["report_id"])])


async def s_cohort_quality(conn) -> dict:
    if not await AA.has_schema(conn):
        return _section(UNAVAILABLE, why=AA.R_SCHEMA)
    rep = await _latest(conn)
    if rep is None:
        return _section(EMPTY, why="no daily report has been written")
    body = rep["report"] or {}
    ent = (body.get("derek") or {}).get("entries") or {}
    data = {"derek_calibration": ent.get("calibration"),
            "derek_selected_economics": ent.get("selected_economics"),
            "xavier_benchmarks": (body.get("xavier") or {}).get(
                "benchmarks"),
            "weighting": "ONE_WEIGHT_PER_FIXTURE"}
    return _section(OK, data)


async def s_tasks(conn) -> dict:
    if not await IMP.tasks_available(conn):
        return _section(UNAVAILABLE, why=IMP.R_TASKS_UNAVAILABLE)
    rows = await IMP.read_tasks(conn, kind=IMP.TASK_KIND, limit=50)
    if not rows:
        return _section(EMPTY, [], why="no improvement task has been filed")
    for t in rows:
        t["evidence"] = [_ev("agent_tasks", t["task_id"],
                             "/api/command/agents/tasks/%s" % t["task_id"])]
    return _section(OK, rows, evidence=[e for t in rows
                                        for e in t["evidence"]])


async def s_candidates(conn) -> dict:
    if not await IMP.has_schema(conn):
        return _section(UNAVAILABLE, why=IMP.R_SCHEMA)
    rows = await IMP.candidates(conn, limit=50)
    if not rows:
        return _section(EMPTY, [], why="no improvement candidate yet")
    for c in rows:
        c["evidence"] = [_ev("improvement_candidates", c["candidate_id"],
                             CAND_HREF % c["candidate_id"])]
    return _section(OK, rows, evidence=[e for c in rows
                                        for e in c["evidence"]])


async def s_evaluations(conn) -> dict:
    if not await IMP.has_schema(conn):
        return _section(UNAVAILABLE, why=IMP.R_SCHEMA)
    rows = await IMP.trials(conn, limit=100)
    hold = await IMP.holdouts(conn)
    if not rows:
        return _section(EMPTY, {"trials": [], "holdouts": hold},
                        why="no trial has been run")
    return _section(OK, {"trials": rows, "holdouts": hold}, evidence=[
        _ev("improvement_trials", t["trial_id"],
            CAND_HREF % t["candidate_id"]) for t in rows])


async def s_releases(conn) -> dict:
    if not await IMP.has_schema(conn):
        return _section(UNAVAILABLE, why=IMP.R_SCHEMA)
    rel = await IMP.releases(conn, limit=50)
    approved = [dict(r) for r in await conn.fetch(
        "SELECT candidate_id, change_class, state, approved_by, "
        " extract(epoch FROM approved_at)::float8 AS approved_at "
        "  FROM improvement_candidates WHERE approved_by IS NOT NULL "
        " ORDER BY approved_at DESC LIMIT 50")]
    data = {"releases": rel, "approvals": approved,
            "states": {"approval": "improvement_candidates.approved_by",
                       "deployment": ("a pre-authorized policy version is "
                                      "activated by its class rule; "
                                      "anything else is not deployed by an "
                                      "agent"),
                       "canary": "improvement_releases.state = CANARY",
                       "rollback": "improvement_releases.state = "
                                   "ROLLED_BACK"}}
    if not rel and not approved:
        return _section(EMPTY, data, why="nothing has been approved or "
                                         "released")
    return _section(OK, data, evidence=[
        _ev("improvement_releases", r["release_id"],
            CAND_HREF % r["candidate_id"]) for r in rel])


async def s_directives(conn) -> dict:
    if not await IMP._regclass(conn, "management_directives"):
        return _section(UNAVAILABLE, why=CHAT_NOT_MERGED)
    rows = [dict(r) for r in await conn.fetch(
        "SELECT * FROM management_directives ORDER BY 1 DESC LIMIT 50")]
    if not rows:
        return _section(EMPTY, [], why="no management directive recorded")
    return _section(OK, rows)


async def s_conversations(conn) -> dict:
    for t in ("audrey_conversations", "agent_conversations"):
        if await IMP._regclass(conn, t):
            rows = [dict(r) for r in await conn.fetch(
                "SELECT * FROM %s ORDER BY 1 DESC LIMIT 50" % t)]
            if not rows:
                return _section(EMPTY, [], why="no conversation recorded")
            return _section(OK, rows)
    return _section(UNAVAILABLE, why=CHAT_NOT_MERGED)


def s_provider() -> dict:
    present = bool((os.getenv(PROVIDER_KEY_ENV) or "").strip())
    return _section(OK, {"configured": present,
                         "variable": PROVIDER_KEY_ENV,
                         "value_disclosed": False},
                    why=None)


async def workspace(conn) -> dict:
    sections = {
        "status": await _guard(s_status, conn),
        "versions": await _guard(s_versions, conn),
        "daily_reports": await _guard(s_reports, conn),
        "findings": await _guard(s_findings, conn),
        "outcomes": await _guard(s_outcomes, conn),
        "cohort_quality": await _guard(s_cohort_quality, conn),
        "tasks": await _guard(s_tasks, conn),
        "candidates": await _guard(s_candidates, conn),
        "evaluations": await _guard(s_evaluations, conn),
        "releases": await _guard(s_releases, conn),
        "directives": await _guard(s_directives, conn),
        "conversations": await _guard(s_conversations, conn),
        "provider": s_provider()}
    st = (sections["status"].get("data") or {}).get("agent_status") or {}
    return {"agent": {"agent_id": "AUDREY", "display_name": "Audrey",
                      "mandate": ("daily audit of Derek and Xavier; governed "
                                  "self-improvement"),
                      "state": st.get("state"),
                      "last_heartbeat_at": st.get("last_heartbeat_at")},
            "read_at": time.time(), "read_only": True, "sections": sections}


# ═════════════════════════════════════════════════════════════════════
# ROUTES
# ═════════════════════════════════════════════════════════════════════

@router.get("/api/command/agents/audrey",
            dependencies=[Depends(require_read)])
async def audrey_workspace(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    async with _connection() as conn:
        return await workspace(conn)


@router.get("/api/command/agents/audrey/reports/{report_id:path}",
            dependencies=[Depends(require_read)])
async def audrey_report(report_id: str, response: Response,
                        version: int | None = None) -> dict:
    response.headers["Cache-Control"] = "no-store"
    async with _connection() as conn:
        got = await AA.report(conn, report_id, version=version)
    if got is None:
        raise HTTPException(status_code=404, detail={
            "reason": "NO_SUCH_REPORT", "report_id": report_id})
    return got


@router.get("/api/command/agents/audrey/candidates/{candidate_id}",
            dependencies=[Depends(require_read)])
async def audrey_candidate(candidate_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    async with _connection() as conn:
        got = await IMP.candidate_detail(conn, candidate_id)
    if got is None:
        raise HTTPException(status_code=404, detail={
            "reason": IMP.R_NO_SUCH_CANDIDATE, "candidate_id": candidate_id})
    return got


_STATUS_FOR = {IMP.R_NO_SUCH_CANDIDATE: 404,
               IMP.R_PROTECTED_CLASS: 403, IMP.R_PROTECTED_KEY: 403,
               IMP.R_UNKNOWN_CLASS: 403, IMP.R_SELF_APPROVAL: 403,
               IMP.R_AGENT_APPROVER: 403, IMP.R_NO_APPROVER: 400,
               IMP.R_NOT_PASSED: 409, IMP.R_NOT_APPROVAL_READY: 409}


@router.post("/api/command/agents/audrey/candidates/{candidate_id}/approve")
async def audrey_approve(candidate_id: str, request: Request,
                         role: str = Depends(require_write)) -> dict:
    """A NAMED PERSON APPROVES. The control credential is required; the
    approver's name and the credential's role are both recorded."""
    try:
        body = await request.json()
    except Exception:                                           # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    async with _connection() as conn:
        got = await IMP.approve(conn, candidate_id,
                                approver=str(body.get("approver") or ""),
                                credential_role=role,
                                statement=str(body.get("statement") or ""),
                                now=time.time())
    if not got.get("ok"):
        raise HTTPException(status_code=_STATUS_FOR.get(
            got.get("refusal"), 409), detail=got)
    return got
