"""THE LEARNING-LAYER READS (migration 218): GET only, COMMAND auth via
agents_core.require_read -> api.app.require_command. SHADOW / RESEARCH.

EVERY RESPONSE:
    {"label": "SHADOW", "authority": "SHADOW_RESEARCH_NO_AUTHORITY",
     "disclosure": "...", "status": "OK" | "EMPTY" | "UNAVAILABLE",
     "why": <reason or null>, "run_id", "computed_at", "data": ...}
EMPTY names why (no run yet, migration 218 absent); UNAVAILABLE names the
failed read. A failed read is never shown as zeros.

ROUTES (logic in sportsassets/poslearn/*; these read poslearn_* only):
  GET /api/command/tournament/models            champion, every registered
      model (hash, criteria, forward scores, verdict vs champion), the
      promotion ladder and any human decision, unregistered plans
  GET /api/command/tournament/agents            every agent variant, its
      metrics, verdict vs its V1, the ladder
  GET /api/command/profitability/edge-confidence  the meta-model, forward
      evaluation, latest outputs, feature availability, the NOT-active
      future sizing formula
  GET /api/command/profitability/avoidance      levels, flagged segments,
      forward losses avoided / false blocks / precision / recall
  GET /api/command/experiments                  every experiment's design,
      status, assignment counts, reviews and (only after analysis) result
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends

from .agents_core import _pool, require_read

router = APIRouter()
BASE = "/api/command"


def _env(status, why=None, **kw) -> dict:
    from ..poslearn import common as C
    out = C.envelope(status=status, why=why)
    out.update(kw)
    return out


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


async def _ready(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass('poslearn_snapshots') IS NOT NULL"))


async def _latest(conn, component):
    return await conn.fetchrow(
        "SELECT run_id, payload, extract(epoch FROM computed_at)::float8 "
        "       AS computed_at FROM poslearn_snapshots "
        " WHERE component = $1 ORDER BY computed_at DESC LIMIT 1",
        component)


async def _ladder(conn) -> dict:
    out: dict = {}
    for r in await conn.fetch(
            "SELECT registration_id, step, actor, outcome, evidence, "
            "       extract(epoch FROM at)::float8 AS at "
            "  FROM poslearn_promotion_steps ORDER BY step_id"):
        out.setdefault(r["registration_id"], []).append(
            {"step": r["step"], "actor": r["actor"], "outcome": r["outcome"],
             "at": r["at"], "evidence": _j(r["evidence"])})
    for r in await conn.fetch(
            "SELECT registration_id, decision, approver, statement, "
            "       extract(epoch FROM approved_at)::float8 AS at "
            "  FROM poslearn_human_approvals"):
        out.setdefault(r["registration_id"], []).append(
            {"step": "HUMAN_APPROVAL", "actor": r["approver"],
             "outcome": r["decision"], "at": r["at"],
             "statement": r["statement"], "production_effect": "NONE"})
    return out


def _stage(ladder: list) -> str:
    steps = {s["step"]: s["outcome"] for s in ladder}
    if "HUMAN_APPROVAL" in steps:
        return ("HUMAN_APPROVED_AWAITING_SEPARATE_OWNER_RELEASE"
                if steps["HUMAN_APPROVAL"] == "APPROVE_PROMOTION"
                else "HUMAN_REJECTED")
    if steps.get("AUDREY_EVALUATION") == "PASS":
        return "AWAITING_HUMAN_APPROVAL"
    if steps.get("AUDREY_EVALUATION") == "FAIL":
        return "AUDREY_FAILED"
    if steps.get("KAREN_CHALLENGE") == "BLOCKED":
        return "KAREN_BLOCKED"
    if steps.get("KAREN_CHALLENGE") == "NOT_BLOCKED":
        return "AWAITING_AUDREY"
    if "CRITERIA_MET" in steps:
        return "AWAITING_KAREN"
    return "NOT_A_CANDIDATE"


async def _read(fn):
    pool = await _pool()
    try:
        async with pool.acquire() as conn:
            if not await _ready(conn):
                return _env("EMPTY", "MIGRATION_218_NOT_APPLIED", data=None)
            return await fn(conn)
    except Exception as exc:                                    # noqa: BLE001
        return _env("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                               str(exc)[:160]), data=None)


def _snap(row, extra=None):
    if row is None:
        return _env("EMPTY", "NO_LEARNING_RUN_YET", run_id=None,
                    computed_at=None, data=None, **(extra or {}))
    return _env("OK", None, run_id=row["run_id"],
                computed_at=row["computed_at"], data=_j(row["payload"]),
                **(extra or {}))


async def _tournament(conn, component, key):
    row = await _latest(conn, component)
    out = _snap(row)
    if row is None:
        return out
    ladder = await _ladder(conn)
    for item in (out["data"] or {}).get(key) or []:
        lad = ladder.get(item["registration_id"], [])
        item["promotion_ladder"] = lad
        item["promotion_stage"] = _stage(lad)
    out["production_effect"] = "NONE"
    return out


@router.get(BASE + "/tournament/models", dependencies=[Depends(require_read)])
async def tournament_models() -> dict:
    return await _read(lambda conn: _tournament(conn, "MODEL_TOURNAMENT",
                                                "models"))


@router.get(BASE + "/tournament/agents", dependencies=[Depends(require_read)])
async def tournament_agents() -> dict:
    return await _read(lambda conn: _tournament(conn, "AGENT_TOURNAMENT",
                                                "variants"))


@router.get(BASE + "/profitability/edge-confidence",
            dependencies=[Depends(require_read)])
async def profitability_edge_confidence() -> dict:
    async def fn(conn):
        return _snap(await _latest(conn, "EDGE_CONFIDENCE"),
                     extra={"live_sizing_effect": "NONE",
                            "future_sizing_formula_status": "NOT_ACTIVE"})
    return await _read(fn)


@router.get(BASE + "/profitability/avoidance",
            dependencies=[Depends(require_read)])
async def profitability_avoidance() -> dict:
    async def fn(conn):
        return _snap(await _latest(conn, "AVOIDANCE"),
                     extra={"blocks_production": False})
    return await _read(fn)


@router.get(BASE + "/experiments", dependencies=[Depends(require_read)])
async def experiments() -> dict:
    async def fn(conn):
        return _snap(await _latest(conn, "EXPERIMENTS"),
                     extra={"assignment_effect": "PAPER_SHADOW_ONLY"})
    return await _read(fn)
