"""THE PAPER LEARNING RECORD, FOR MANAGEMENT: /api/command/paper/learning*
(read-only, COMMAND auth, registered in api/app.py beside command_paper).

PAPER ONLY. Every figure is LIVE MARKET DATA / SIMULATED EXECUTION on the
fictional paper account; nothing here writes, and nothing here reads a funded
table. The pages consume these JSON shapes (the logic is
`agents/paper_learning.py`):

  GET /api/command/paper/learning
        per agent (DEREK / XAVIER / AUDREY): what was learned (latest lesson
        per series, with its provenance hash and record count), the proposed
        change, its evaluation (or INSUFFICIENT_FORWARD_DATA with counts),
        active or not; Audrey's event-audit counts; recent chain summaries;
        the activation control's state. Each section OK / EMPTY /
        UNAVAILABLE -- a failed read is never shown as a zero.
  GET /api/command/paper/learning/chain/{fill_id}
        the complete linked chain of one simulated fill: decision -> order
        -> fill(s) -> ledger debit -> handoff -> reviews / actions -> exit or
        settlement -> ledger result -> Audrey's findings, each with ids and
        timestamps; every absent link is listed as MISSING (pending or a
        defect).
  GET /api/command/paper/learning/chains?limit=&strategy=
        the chain summary (complete / missing / pending / defects) of the
        most recently filled groups.
  GET /api/command/paper/learning/decision/{decision_id}
        one decision record and what it retains, field by field.
  GET /api/command/paper/learning/lessons?agent=&all_versions=
  GET /api/command/paper/learning/proposals?agent=
  GET /api/command/paper/learning/events?kind=&limit=
        Audrey's event audits (one finding per event).

  GET /api/command/paper/learning/policy
        the completed-game paper policy's parameters (migration 186): the
        ACTIVE version with its provenance, every version, the candidates
        with their evaluation status (INSUFFICIENT_FORWARD_DATA stated as
        such) and failed activation checks, and the activation history.

THE TWO WRITES (POST, the existing command CONTROL credential, audited):
  POST /api/command/paper/learning/proposals/{proposal_id}/activate
        body {"approver": "<named human>"}. Refused unless the
        PAPER_LEARNING_PROPOSAL_ACTIVATION control row is on and every
        activation check passes (`paper_learning.activate_proposal`).
  POST /api/command/paper/learning/policy/rollback
        body {"actor": "<named human>", "reason": "<why>"}: restores the
        previous version atomically (`rollback_policy_parameters`).
Nothing activates automatically. PAPER ONLY: no funded module reads these
parameters.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .agents_core import require_read, require_write

router = APIRouter()


def _acct(account_id: str | None) -> str:
    from .. import bettor_paper_ledger as L
    a = account_id or L.ACCOUNT_ID
    if not L.is_paper_id(a):
        raise HTTPException(status_code=400, detail={
            "reason": "NOT_A_PAPER_ACCOUNT_ID"})
    return a


async def _conn_pool():
    from ..db import get_pool
    try:
        return await get_pool()
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "NO_DATABASE_POOL", "detail": type(exc).__name__})


def _labels() -> dict:
    from .. import bettor_paper_ledger as L
    from ..agents import paper_learning as PLRN
    return {"data_label": L.DATA_LABEL, "labels": dict(L.LABELS),
            "counterfactual_label": PLRN.COUNTERFACTUAL}


async def _read(fn, *, why_empty: str, empty=None, **kw) -> dict:
    """ONE READ AS A SECTION: OK / EMPTY (named) / UNAVAILABLE (named)."""
    from ..agents import paper_learning as PLRN
    pool = await _conn_pool()
    async with pool.acquire() as conn:
        if not await PLRN.has_schema(conn):
            return dict(_labels(), result={
                "status": "UNAVAILABLE",
                "why": "MIGRATION_185_IS_NOT_APPLIED", "data": None})
        return dict(_labels(), as_of=time.time(),
                    result=await PLRN._safe(fn(conn, **kw),
                                            why_empty=why_empty,
                                            empty=empty))


@router.get("/api/command/paper/learning",
            dependencies=[Depends(require_read)])
async def paper_learning(account_id: str | None = Query(None)) -> dict:
    from ..agents import paper_learning as PLRN
    acct = _acct(account_id)
    pool = await _conn_pool()
    async with pool.acquire() as conn:
        return await PLRN.learning_summary(conn, account_id=acct)


@router.get("/api/command/paper/learning/chain/{fill_id}",
            dependencies=[Depends(require_read)])
async def paper_learning_chain(fill_id: str) -> dict:
    from ..agents import paper_learning as PLRN
    return await _read(lambda conn: PLRN.fill_chain(conn, fill_id),
                       why_empty="NO_SUCH_PAPER_FILL",
                       empty=lambda d: not (d or {}).get("found"))


@router.get("/api/command/paper/learning/chains",
            dependencies=[Depends(require_read)])
async def paper_learning_chains(limit: int = Query(50, ge=1, le=500),
                                strategy: str | None = Query(None),
                                account_id: str | None = Query(None)
                                ) -> dict:
    from ..agents import paper_learning as PLRN
    acct = _acct(account_id)
    return await _read(lambda conn: PLRN.recent_chains(
        conn, account_id=acct, limit=limit, strategy=strategy),
        why_empty="NO_SIMULATED_FILL_YET")


@router.get("/api/command/paper/learning/decision/{decision_id}",
            dependencies=[Depends(require_read)])
async def paper_learning_decision(decision_id: str) -> dict:
    from ..agents import paper_learning as PLRN
    return await _read(lambda conn: PLRN.decision_record(conn, decision_id),
                       why_empty="NO_SUCH_PAPER_DECISION",
                       empty=lambda d: not (d or {}).get("found"))


@router.get("/api/command/paper/learning/lessons",
            dependencies=[Depends(require_read)])
async def paper_learning_lessons(agent: str | None = Query(None),
                                 all_versions: bool = Query(False),
                                 limit: int = Query(200, ge=1, le=1000),
                                 account_id: str | None = Query(None)
                                 ) -> dict:
    from ..agents import paper_learning as PLRN
    acct = _acct(account_id)
    return await _read(lambda conn: PLRN.lessons(
        conn, account_id=acct, agent=agent, latest_only=not all_versions,
        limit=limit), why_empty="NO_LESSON_RECORDED_YET")


@router.get("/api/command/paper/learning/proposals",
            dependencies=[Depends(require_read)])
async def paper_learning_proposals(agent: str | None = Query(None),
                                   limit: int = Query(100, ge=1, le=500),
                                   account_id: str | None = Query(None)
                                   ) -> dict:
    from ..agents import paper_learning as PLRN
    acct = _acct(account_id)
    return await _read(lambda conn: PLRN.proposals(
        conn, account_id=acct, agent=agent, limit=limit),
        why_empty="NO_PROPOSAL_RECORDED_YET")


@router.get("/api/command/paper/learning/events",
            dependencies=[Depends(require_read)])
async def paper_learning_events(kind: str | None = Query(None),
                                limit: int = Query(100, ge=1, le=1000),
                                account_id: str | None = Query(None)
                                ) -> dict:
    from ..agents import paper_learning as PLRN
    acct = _acct(account_id)
    return await _read(lambda conn: PLRN.event_audits(
        conn, account_id=acct, kind=kind, limit=limit),
        why_empty="NO_EVENT_AUDITED_YET")


@router.get("/api/command/paper/learning/policy",
            dependencies=[Depends(require_read)])
async def paper_learning_policy() -> dict:
    from ..agents import paper_learning as PLRN
    return await _read(lambda conn: PLRN.policy_parameter_state(conn),
                       why_empty="NO_PARAMETER_STATE",
                       empty=lambda d: (d or {}).get("status")
                       == "UNAVAILABLE")


async def _body(request) -> dict:
    try:
        b = await request.json()
    except Exception:                                           # noqa: BLE001
        b = None
    if not isinstance(b, dict):
        raise HTTPException(status_code=400, detail={
            "reason": "A_JSON_OBJECT_BODY_IS_REQUIRED"})
    return b


async def _write(fn) -> dict:
    from ..agents import paper_learning as PLRN
    pool = await _conn_pool()
    async with pool.acquire() as conn:
        if not await PLRN.has_schema(conn):
            raise HTTPException(status_code=503, detail={
                "reason": "MIGRATION_185_IS_NOT_APPLIED"})
        got = await fn(conn)
    if not got.get("ok"):
        raise HTTPException(status_code=409, detail=got)
    return dict(_labels(), result=got)


@router.post("/api/command/paper/learning/proposals/{proposal_id}/activate",
             dependencies=[Depends(require_write)])
async def paper_learning_activate(proposal_id: str, request: Request
                                  ) -> dict:
    from ..agents import paper_learning as PLRN
    b = await _body(request)
    return await _write(lambda conn: PLRN.activate_proposal(
        conn, proposal_id, approver=b.get("approver")))


@router.post("/api/command/paper/learning/policy/rollback",
             dependencies=[Depends(require_write)])
async def paper_learning_rollback(request: Request) -> dict:
    from ..agents import paper_learning as PLRN
    b = await _body(request)
    return await _write(lambda conn: PLRN.rollback_policy_parameters(
        conn, actor=b.get("actor"), reason=b.get("reason")))
