"""AUDREY'S MANAGEMENT CHAT AND DIRECTIVES -- the HTTP surface.

  POST /api/command/agents/audrey/chat                  read credential
       {conversation_id?, message}. Questions are answered from records with
       citations. A message that would CHANGE something (create / clarify /
       confirm / assign / cancel a directive) is carried out only when the
       caller holds the CONTROL credential; a read caller receives a
       structured REQUIRES_OPERATOR_CREDENTIAL response. The role is resolved
       HERE, from the request's credentials, and handed to the service -- the
       message text can never claim one.
  GET  /api/command/agents/audrey/conversations         read
  GET  /api/command/agents/audrey/conversations/{id}    read
  GET  /api/command/agents/audrey/directives            read
  GET  /api/command/agents/audrey/directives/{id}       read
  POST /api/command/agents/audrey/directives            control (form create)
  POST /api/command/agents/audrey/directives/{id}/confirm   control
  POST /api/command/agents/audrey/directives/{id}/cancel    control
  GET  /api/command/agents/audrey/chat/describe         read (tools, provider
       mode; never the credential)
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..agents import audrey_chat as AC
from ..agents import directives as D
from ..db import get_pool

router = APIRouter()

#: the clock the routes use; tests substitute a controlled one
_clock = time.time


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


async def resolve_role(request: Request) -> str:
    """The strongest role the request's credentials prove: a control role
    when it holds one, else the read role (401 when it holds neither)."""
    try:
        return await require_write(request)
    except HTTPException:
        return await require_read(request)


async def _pool():
    try:
        return await get_pool()
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "NO_DATABASE_POOL", "error": type(exc).__name__})


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


_RID = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][\w:.\-]+$",
             description="client-supplied idempotency key; a retry or a "
                         "reconnect with the same key returns the original "
                         "result instead of acting again")


class ChatBody(BaseModel):
    request_id: str = _RID
    conversation_id: str | None = Field(default=None, max_length=100)
    message: str = Field(min_length=1, max_length=AC.MAX_MESSAGE_CHARS)


class DirectiveBody(BaseModel):
    request_id: str = _RID
    objective: str = Field(min_length=1, max_length=4000)
    accounts: list[str] | None = None
    agents: list[Literal["DEREK", "XAVIER"]] | None = None
    markets: list[str] | None = None
    constraints: dict[str, float] | None = None
    acceptance_criteria: list[str] | None = None
    review_at: str | None = None
    expires_at: str | None = None
    assigned_agent: Literal["DEREK", "XAVIER", "DEREK_AND_XAVIER"] | None = None


class ConfirmBody(BaseModel):
    request_id: str = _RID
    answer: str | None = Field(default=None, max_length=2000)
    review_at: str | None = None
    accounts: list[str] | None = None


class CancelBody(BaseModel):
    request_id: str | None = Field(default=None, min_length=8,
                                   max_length=128)
    reason: str = Field(default="cancelled by management", max_length=500)


def _refusal_status(got: dict, response: Response | None = None) -> None:
    r = got.get("refusal")
    if got.get("ok"):
        return
    if r == D.R_REQUEST_IN_FLIGHT:
        if response is not None:
            response.status_code = 202
        return
    if r in (D.R_IDEMPOTENCY_MISMATCH, D.R_BAD_REQUEST_ID):
        raise HTTPException(status_code=409, detail=got)
    if r == D.R_NOT_FOUND:
        raise HTTPException(status_code=404, detail=got)
    if r == D.R_NO_SCHEMA:
        raise HTTPException(status_code=503, detail=got)
    if r in (D.R_NOT_OPEN, D.R_EMPTY, D.R_BAD_AGENT):
        raise HTTPException(status_code=409, detail=got)


@router.post("/api/command/agents/audrey/chat")
async def audrey_chat(body: ChatBody, response: Response,
                      role: str = Depends(resolve_role)) -> dict:
    _no_store(response)
    pool = await _pool()
    # the POOL, not a held connection: the service acquires a connection per
    # read / write and holds none across the provider call
    got = await AC.handle_message(
        pool, role=role, label=AC.ROLE_LABELS.get(role),
        message=body.message, conversation_id=body.conversation_id,
        now=_clock(), request_id=body.request_id)
    if got.get("status") == AC.S_ERROR:
        code = 409 if got.get("error") == D.R_IDEMPOTENCY_MISMATCH else 422
        raise HTTPException(status_code=code, detail=got)
    if got.get("status") == AC.S_PENDING:
        response.status_code = 202
    return got


@router.get("/api/command/agents/audrey/chat/describe",
            dependencies=[Depends(require_read)])
async def audrey_chat_describe(response: Response) -> dict:
    _no_store(response)
    return {"chat": AC.describe(), "provider": AC.provider_status(),
            "directives": D.describe(),
            "tools": AC.tool_catalog(), "read_only": True}


@router.get("/api/command/agents/audrey/conversations",
            dependencies=[Depends(require_read)])
async def audrey_conversations(response: Response, limit: int = 50) -> dict:
    _no_store(response)
    pool = await _pool()
    async with pool.acquire() as conn:
        present = await AC.has_schema(conn)
        rows = await AC.conversations(conn, limit=max(1, min(200, limit)))
    return {"read_at": _clock(), "read_only": True,
            "status": "OK" if rows else ("EMPTY" if present
                                         else "UNAVAILABLE"),
            "why": None if rows else ("NO_CONVERSATION_YET" if present
                                      else D.R_NO_SCHEMA),
            "conversations": rows}


@router.get("/api/command/agents/audrey/conversations/{conversation_id}",
            dependencies=[Depends(require_read)])
async def audrey_conversation(conversation_id: str,
                              response: Response) -> dict:
    _no_store(response)
    pool = await _pool()
    async with pool.acquire() as conn:
        got = await AC.conversation(conn, conversation_id)
    if got is None:
        raise HTTPException(status_code=404, detail={
            "reason": "NO_CONVERSATION_WITH_THAT_ID"})
    return dict(got, read_only=True)


@router.get("/api/command/agents/audrey/directives",
            dependencies=[Depends(require_read)])
async def audrey_directives(response: Response, status: str | None = None,
                            limit: int = 50) -> dict:
    _no_store(response)
    if status and status not in D.STATUSES:
        raise HTTPException(status_code=422, detail={
            "reason": "UNKNOWN_STATUS", "statuses": list(D.STATUSES)})
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await D.has_schema(conn):
            return {"read_at": _clock(), "status": "UNAVAILABLE",
                    "why": D.R_NO_SCHEMA, "directives": []}
        monitor = await D.monitor(conn, now=_clock())
        rows = await D.list_directives(conn, status=status,
                                       limit=max(1, min(200, limit)))
        out = []
        for d in rows:
            out.append(dict(d, tasks=await D.tasks_of(conn, d),
                            evidence_links=_links(d)))
    return {"read_at": _clock(), "status": "OK" if out else "EMPTY",
            "why": None if out else "NO_DIRECTIVE_RECORDED",
            "monitor": {k: monitor.get(k) for k in ("checked",
                                                    "transitions")},
            "directives": out}


def _links(d: dict) -> list:
    out = [{"kind": "management_directives", "id": d["directive_id"],
            "href": "/api/command/agents/audrey/directives/%s"
                    % d["directive_id"]}]
    if d.get("conversation_id"):
        out.append({"kind": "audrey_conversations",
                    "id": d["conversation_id"],
                    "href": "/api/command/agents/audrey/conversations/%s"
                            % d["conversation_id"]})
    for t in d.get("task_ids") or []:
        out.append({"kind": "agent_tasks", "id": t,
                    "href": "/api/command/agents/audrey/directives/%s"
                            % d["directive_id"]})
    return out


@router.get("/api/command/agents/audrey/directives/{directive_id}",
            dependencies=[Depends(require_read)])
async def audrey_directive(directive_id: str, response: Response) -> dict:
    _no_store(response)
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await D.has_schema(conn):
            raise HTTPException(status_code=503,
                                detail={"reason": D.R_NO_SCHEMA})
        await D.monitor(conn, now=_clock())
        d = await D.get(conn, directive_id)
        if d is None:
            raise HTTPException(status_code=404,
                                detail={"reason": D.R_NOT_FOUND})
        return {"directive": d, "tasks": await D.tasks_of(conn, d),
                "events": await D.events(conn, directive_id),
                "evidence": _links(d), "read_only": True}


@router.post("/api/command/agents/audrey/directives")
async def audrey_directive_create(body: DirectiveBody, response: Response,
                                  role: str = Depends(require_write)) -> dict:
    fields = body.model_dump(exclude_none=True)
    request_id = fields.pop("request_id")
    objective = fields.pop("objective")
    pool = await _pool()
    now = _clock()

    async def _run():
        async with pool.acquire() as conn:
            return await D.create(conn, instruction=objective,
                                  requester_role=role,
                                  requester_label=AC.ROLE_LABELS.get(role),
                                  now=now, fields=fields or None,
                                  request_id=request_id)
    got = await D.idempotent_call(
        pool, request_id=request_id, kind="directive_create",
        requester_role=role, payload={"objective": objective,
                                      "fields": fields}, now=now, run=_run)
    _refusal_status(got, response)
    return got


@router.post("/api/command/agents/audrey/directives/{directive_id}/confirm")
async def audrey_directive_confirm(directive_id: str, body: ConfirmBody,
                                   response: Response,
                                   role: str = Depends(require_write)) -> dict:
    fields = {k: v for k, v in (("review_at", body.review_at),
                                ("accounts", body.accounts)) if v}
    pool = await _pool()
    now = _clock()

    async def _run():
        async with pool.acquire() as conn:
            return await D.confirm(conn, directive_id=directive_id,
                                   requester_role=role,
                                   requester_label=AC.ROLE_LABELS.get(role),
                                   now=now, answer=body.answer,
                                   fields=fields or None)
    got = await D.idempotent_call(
        pool, request_id=body.request_id, kind="directive_confirm",
        requester_role=role, payload={"directive_id": directive_id,
                                      "answer": body.answer,
                                      "fields": fields}, now=now, run=_run)
    _refusal_status(got, response)
    return got


@router.post("/api/command/agents/audrey/directives/{directive_id}/cancel")
async def audrey_directive_cancel(directive_id: str, body: CancelBody,
                                  response: Response,
                                  role: str = Depends(require_write)) -> dict:
    pool = await _pool()
    now = _clock()

    async def _run():
        async with pool.acquire() as conn:
            return await D.cancel(conn, directive_id=directive_id,
                                  reason=body.reason, requester_role=role,
                                  requester_label=AC.ROLE_LABELS.get(role),
                                  now=now)
    if body.request_id:
        got = await D.idempotent_call(
            pool, request_id=body.request_id, kind="directive_cancel",
            requester_role=role, payload={"directive_id": directive_id,
                                          "reason": body.reason}, now=now,
            run=_run)
    else:
        got = await _run()
    _refusal_status(got, response)
    return got
