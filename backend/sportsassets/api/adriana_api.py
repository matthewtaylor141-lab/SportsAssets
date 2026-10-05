"""ADRIANA'S WORKSPACE (migration 265): READ-ONLY.

  GET /api/command/adriana                    profile, status, latest census
                                              pass, venues, opportunities,
                                              refusals, open blockers (+ the
                                              workspace contract `sections`)
  GET /api/command/agents/adriana             the same (route parity)
  GET /api/command/adriana/scans              recent census passes
  GET /api/command/adriana/opportunities/{id} one proven opportunity

All reads need the COMMAND read credential. There is NO write route here:
Adriana cannot be asked to act through this API, and no route touches an
order, a limit, an approval or a policy. Every section follows the shared
workspace contract: OK / EMPTY (with the reason) / UNAVAILABLE (the failed
read's exception type) -- an unread figure is never a zero.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .agents_core import _pool, _read_section, require_read
from .agents_pos import _authority_section, _ok, _ro, _status_section

router = APIRouter()


def _j(v):
    if v is None or isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except Exception:                                           # noqa: BLE001
        return v


def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if hasattr(v, "timestamp"):
            v = v.timestamp()
        elif hasattr(v, "as_tuple"):
            v = float(v)
        out[k] = _j(v) if isinstance(v, str) and v[:1] in "[{" else v
    return out


async def _scans(conn, limit: int) -> list:
    return [_row(r) for r in await conn.fetch(
        "SELECT scan_id, started_at, finished_at, status, why, engine_version,"
        "       venues, markets_read, books_fresh, structures_considered, "
        "       opportunities, refusals_total, refusals_recorded, by_verdict, "
        "       by_kind, by_code, limits, authority, mode, production_effect "
        "  FROM adriana_arb_scans ORDER BY finished_at DESC LIMIT $1", limit)]


async def _opportunities(conn, limit: int) -> list:
    return [_row(r) for r in await conn.fetch(
        "SELECT opportunity_id, scan_id, structure_kind, event_key, venues, "
        "       legs, verdict, max_qty, min_payout_usd, total_cost_usd, "
        "       net_profit_usd, edge_per_set_usd, leg_plan, decided_at, mode "
        "  FROM adriana_arb_opportunities ORDER BY decided_at DESC LIMIT $1",
        limit)]


async def _refusals(conn, limit: int) -> list:
    return [_row(r) for r in await conn.fetch(
        "SELECT refusal_id, scan_id, structure_kind, event_key, venues, legs, "
        "       codes, primary_code, detail, decided_at, mode "
        "  FROM adriana_arb_refusals ORDER BY decided_at DESC, refusal_id "
        " LIMIT $1", limit)]


async def _blockers(conn) -> list:
    return [_row(r) for r in await conn.fetch(
        "SELECT task_id, kind, title, status, spec, updated_at "
        "  FROM agent_tasks WHERE assignee = 'ADRIANA' "
        "   AND status IN ('OPEN', 'IN_PROGRESS', 'WAITING') "
        " ORDER BY updated_at DESC LIMIT 20")]


async def _workspace() -> dict:
    from ..agents import adriana as AD
    from ..agents import adriana_runner as AR
    from ..agents import collaboration_loop as CL
    from ..agents import pos_authority as PA
    from ..agents import registry as R

    pool = await _pool()
    async with _ro(pool) as conn:
        if not await AD.schema(conn):
            raise HTTPException(status_code=503, detail={
                "reason": AD.R_NO_SCHEMA})
        st, status_sec = await _status_section(conn, R.ADRIANA)
        scans = await _read_section(
            _scans(conn, 12), empty_why="NO_CENSUS_PASS_RECORDED_YET",
            evidence_of=lambda r: [{"kind": "adriana_arb_scans",
                                    "id": r["scan_id"]}])
        opps = await _read_section(
            _opportunities(conn, 25),
            empty_why=("NO_STRUCTURE_PROVEN_AFTER_COSTS: every evaluated "
                       "structure was refused with its reason"),
            evidence_of=lambda r: [{"kind": "adriana_arb_opportunities",
                                    "id": r["opportunity_id"]}])
        refs = await _read_section(
            _refusals(conn, 40), empty_why="NO_REFUSAL_RECORDED",
            evidence_of=lambda r: [{"kind": "adriana_arb_refusals",
                                    "id": r["refusal_id"]}])
        blockers = await _read_section(
            _blockers(conn), empty_why="NO_OPEN_BLOCKER")
    last = (scans.get("data") or [None])[0]
    profile = dict(PA.profile(R.ADRIANA),
                   engine=AD.A.VERSION, census=AD.VERSION,
                   verdicts=list(AD.A.VERDICTS),
                   structure_kinds=list(AD.A.STRUCTURE_KINDS),
                   refusal_codes=list(AD.A.REFUSAL_CODES)
                   + [AD.VOID_TERMS_NOT_ESTABLISHED],
                   hard_rule=("GUARANTEED_AFTER_COSTS only when every "
                              "outcome, the settlement terms, the executable "
                              "size and every cost reconcile; anything else "
                              "is REFUSED with its reason"),
                   hypothesis=AD.HYPOTHESIS["label"],
                   peer_routing=list(CL.PEER_ROUTING["ADRIANA"]),
                   runner={"enabled": AR.enabled(),
                           "interval_s": AR.INTERVAL_S,
                           "book_window_s": AD.BOOK_WINDOW_S,
                           "max_recorded_refusals": AD.MAX_RECORDED_REFUSALS,
                           "kill_switch": "ADRIANA_RUNNER_ENABLED"},
                   authority_declaration=AD.AUTHORITY)
    return {
        "agent": dict(st, role=R.IDENTITIES[R.ADRIANA]["role"]),
        "profile": profile,
        "census": last,
        "venues": (last or {}).get("venues"),
        "sections": {
            "status": status_sec,
            "authority": _authority_section(R.ADRIANA),
            "census_passes": scans,
            "opportunities": opps,
            "refusals": refs,
            "blockers": blockers,
            "runner": _ok(profile["runner"]),
        },
        "production_effect": "NONE", "mode": "SHADOW",
        "read_at": time.time(), "read_only": True}


@router.get("/api/command/adriana", dependencies=[Depends(require_read)])
async def adriana_workspace(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await _workspace()


@router.get("/api/command/agents/adriana",
            dependencies=[Depends(require_read)])
async def adriana_workspace_agents(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await _workspace()


@router.get("/api/command/adriana/scans", dependencies=[Depends(require_read)])
async def adriana_scans(response: Response,
                        limit: int = Query(default=50, ge=1, le=500)) -> dict:
    from ..agents import adriana as AD
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with _ro(pool) as conn:
        if not await AD.schema(conn):
            raise HTTPException(status_code=503, detail={
                "reason": AD.R_NO_SCHEMA})
        return {"scans": await _scans(conn, limit), "read_only": True,
                "read_at": time.time()}


@router.get("/api/command/adriana/opportunities/{opportunity_id}",
            dependencies=[Depends(require_read)])
async def adriana_opportunity(opportunity_id: str, response: Response) -> dict:
    from ..agents import adriana as AD
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with _ro(pool) as conn:
        if not await AD.schema(conn):
            raise HTTPException(status_code=503, detail={
                "reason": AD.R_NO_SCHEMA})
        r = await conn.fetchrow(
            "SELECT * FROM adriana_arb_opportunities WHERE opportunity_id=$1",
            opportunity_id)
    if r is None:
        raise HTTPException(status_code=404, detail={
            "reason": "NO_SUCH_OPPORTUNITY"})
    return {"opportunity": _row(r), "read_only": True, "read_at": time.time()}
