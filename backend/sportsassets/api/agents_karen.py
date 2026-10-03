"""KAREN'S WORKSPACE AND CHALLENGE RECORD: GET /api/command/karen.

Reads (the COMMAND read credential):
  GET /api/command/karen                     profile, current challenges,
                                             metrics (+ the workspace
                                             contract's `sections` for the
                                             agent page)
  GET /api/command/agents/karen              the same (route parity with
                                             /api/command/agents/derek ...)
  GET /api/command/karen/challenges          filter by state / target
  GET /api/command/karen/challenges/{id}     one challenge + its history

Writes (the COMMAND CONTROL credential) -- the peer-response workflow. None
of them is Karen's and none can be made AS Karen (agents/karen.py refuses,
and so does the database):
  POST .../challenges/{id}/respond       the TARGET agent's response
  POST .../challenges/{id}/resolve       UPHELD / REJECTED by a non-Karen
  POST .../challenges/{id}/false-block   the false-block result
  POST .../challenges/{id}/improvement   the improvement it led to

Every section follows the shared workspace contract: OK / EMPTY (with the
reason) / UNAVAILABLE (the failed read's exception type). Nothing here
touches an order, a limit, an approval or a policy.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from .agents_core import _pool, _read_section, require_read, require_write

router = APIRouter()


async def _workspace() -> dict:
    from ..agents import karen as K
    from ..agents import registry as R
    from ..agents import karen_runner as KR

    pool = await _pool()
    async with pool.acquire() as conn:
        if not await K.schema(conn):
            raise HTTPException(status_code=503, detail={
                "reason": K.R_NO_SCHEMA})
        try:
            st = await R.status_of(conn, K.KAREN)
        except Exception as exc:                                # noqa: BLE001
            st = {"agent_id": K.KAREN, "state": None,
                  "status": "UNAVAILABLE", "why": type(exc).__name__}
        if st is None:
            st = {"agent_id": K.KAREN, "state": None,
                  "why": "NOT_REGISTERED_THE_RUNNER_HAS_NOT_STARTED"}
        current = await _read_section(
            K.challenges(conn, state=None, limit=200),
            empty_why="KAREN_HAS_RAISED_NO_CHALLENGE",
            evidence_of=lambda r: list(r.get("evidence") or []))
        if current["status"] == "OK":
            rows = [r for r in current["data"]
                    if r.get("state") in (K.OPEN, K.RESPONDED)]
            current = dict(current, data=rows, evidence=[
                e for r in rows for e in (r.get("evidence") or [])][:200])
            if not rows:
                current.update(status="EMPTY", why="NO_CHALLENGE_IS_OPEN")
        recent = await _read_section(
            K.challenges(conn, limit=50),
            empty_why="KAREN_HAS_RAISED_NO_CHALLENGE",
            evidence_of=lambda r: list(r.get("evidence") or []))
        try:
            met = await K.metrics(conn)
            metrics = {"status": "OK", "why": None, "data": met,
                       "evidence": []}
        except Exception as exc:                                # noqa: BLE001
            met = None
            metrics = {"status": "UNAVAILABLE", "why": type(exc).__name__,
                       "data": None, "evidence": []}
    ident = R.IDENTITIES[K.KAREN]
    profile = {"agent_id": K.KAREN, "display_name": ident["display_name"],
               "role": ident["role"], "mandate": ident["mandate"],
               "tool_permissions": ident["tool_permissions"],
               "forbidden_actions": list(K.FORBIDDEN_ACTIONS),
               "authority": "NONE",
               "runner": {"enabled": KR.enabled(),
                          "interval_s": KR.INTERVAL_S,
                          "detectors": [d for d, _ in KR.DETECTORS],
                          "max_new_per_pass": KR.MAX_NEW_PER_PASS}}
    status_sec = {"status": "OK" if st.get("state") else "EMPTY",
                  "why": st.get("why"), "data": st, "evidence": []}
    return {
        "agent": dict(st, role=ident["role"]),
        "profile": profile,
        "challenges": current, "metrics": met,
        "sections": {
            "status": status_sec,
            "authority": {"status": "OK", "why": None, "evidence": [],
                          "data": {"authority": "NONE",
                                   "allowed": ident["tool_permissions"][
                                       "allowed"],
                                   "denied": ident["tool_permissions"][
                                       "denied"],
                                   "enforced_by": [
                                       "agents/registry.permits",
                                       "agents/karen.may / refuse_authority",
                                       "migration 207 CHECKs and triggers"]}},
            "current_challenges": current,
            "recent_challenges": recent,
            "metrics": metrics,
            "detectors": {"status": "OK", "why": None, "evidence": [],
                          "data": profile["runner"]},
        },
        "production_effect": "NONE", "read_at": time.time(),
        "read_only": True}


@router.get("/api/command/karen", dependencies=[Depends(require_read)])
async def karen_workspace(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await _workspace()


@router.get("/api/command/agents/karen", dependencies=[Depends(require_read)])
async def karen_workspace_agents(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await _workspace()


@router.get("/api/command/karen/challenges",
            dependencies=[Depends(require_read)])
async def karen_challenges(response: Response,
                           state: str | None = Query(default=None),
                           target: str | None = Query(default=None),
                           limit: int = Query(default=50, ge=1, le=500)
                           ) -> dict:
    from ..agents import karen as K

    response.headers["Cache-Control"] = "no-store"
    if state is not None and state not in K.STATES:
        raise HTTPException(status_code=400, detail={
            "reason": "THAT_IS_NOT_A_CHALLENGE_STATE", "state": state})
    if target is not None and target.upper() not in K.TARGETS:
        raise HTTPException(status_code=400, detail={
            "reason": K.R_UNKNOWN_TARGET, "target": target})
    pool = await _pool()
    async with pool.acquire() as conn:
        sec = await _read_section(
            K.challenges(conn, state=state, target_agent=target, limit=limit),
            empty_why="NO_CHALLENGE_MATCHES",
            evidence_of=lambda r: list(r.get("evidence") or []))
    return {"challenges": sec, "states": list(K.STATES),
            "production_effect": "NONE", "read_at": time.time(),
            "read_only": True}


@router.get("/api/command/karen/challenges/{challenge_id}",
            dependencies=[Depends(require_read)])
async def karen_challenge(challenge_id: str, response: Response) -> dict:
    from ..agents import karen as K

    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        try:
            got = await K.challenge(conn, challenge_id)
        except Exception as exc:                                # noqa: BLE001
            raise HTTPException(status_code=503, detail={
                "reason": "KAREN_READ_FAILED", "detail": type(exc).__name__})
    if got is None:
        raise HTTPException(status_code=404, detail={
            "reason": K.R_NO_SUCH_CHALLENGE, "challenge_id": challenge_id})
    return dict(got, read_at=time.time(), read_only=True)


async def _body(request: Request, allowed: set) -> dict:
    origin = request.headers.get("origin")
    if origin and origin not in (str(request.base_url).rstrip("/"),
                                 "https://command.bettortoken.com"):
        raise HTTPException(403, "CROSS_SITE_WRITE_REFUSED")
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > 12000:
            raise HTTPException(413, "REQUEST_TOO_LARGE")
    try:
        b = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "JSON_OBJECT_REQUIRED")
    if not isinstance(b, dict) or set(b) - allowed:
        raise HTTPException(400, "UNKNOWN_REQUEST_FIELDS")
    return b


async def _write(fn) -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        got = await fn(conn)
    if not got.get("ok"):
        raise HTTPException(status_code=409, detail=got)
    return dict(got, production_effect="NONE")


@router.post("/api/command/karen/challenges/{challenge_id}/respond",
             dependencies=[Depends(require_write)])
async def karen_respond(challenge_id: str, request: Request) -> dict:
    """The TARGET agent's peer response. `agent` must be the challenged
    agent; Karen is refused."""
    from ..agents import karen as K
    b = await _body(request, {"agent", "stance", "response",
                              "evidence_refs"})
    return await _write(lambda c: K.respond(
        c, challenge_id, agent=b.get("agent"), stance=b.get("stance"),
        response=b.get("response"), evidence_refs=b.get("evidence_refs"),
        at=time.time()))


@router.post("/api/command/karen/challenges/{challenge_id}/resolve",
             dependencies=[Depends(require_write)])
async def karen_resolve(challenge_id: str, request: Request) -> dict:
    from ..agents import karen as K
    b = await _body(request, {"actor", "outcome", "reason"})
    return await _write(lambda c: K.resolve(
        c, challenge_id, resolver=b.get("actor"), outcome=b.get("outcome"),
        reason=b.get("reason"), at=time.time()))


@router.post("/api/command/karen/challenges/{challenge_id}/false-block",
             dependencies=[Depends(require_write)])
async def karen_false_block(challenge_id: str, request: Request) -> dict:
    from ..agents import karen as K
    b = await _body(request, {"actor", "false_block", "evidence_refs"})
    return await _write(lambda c: K.assess_false_block(
        c, challenge_id, assessor=b.get("actor"),
        false_block=b.get("false_block"),
        evidence_refs=b.get("evidence_refs"), at=time.time()))


@router.post("/api/command/karen/challenges/{challenge_id}/improvement",
             dependencies=[Depends(require_write)])
async def karen_improvement(challenge_id: str, request: Request) -> dict:
    from ..agents import karen as K
    b = await _body(request, {"actor", "finding_id", "proposal_id",
                              "impact"})
    return await _write(lambda c: K.link_improvement(
        c, challenge_id, actor=b.get("actor"), finding_id=b.get("finding_id"),
        proposal_id=b.get("proposal_id"), impact=b.get("impact"),
        at=time.time()))
