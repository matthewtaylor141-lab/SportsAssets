"""GET-only Command readout for the BETTOR Capital Readiness Lab.

No route in this module writes, trades, sizes, promotes, or changes authority.
"""
from __future__ import annotations

import json
from fastapi import APIRouter, Depends, Query
from .agents_core import _pool, require_read

router = APIRouter()
BASE = "/api/command/capital-readiness"


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _row(r):
    out = {}
    for k, v in dict(r).items():
        if hasattr(v, "timestamp"):
            out[k] = v.timestamp()
        elif type(v).__name__ == "Decimal":
            out[k] = float(v)
        elif k in ("payload", "hard_gates", "blocking_gates", "alternatives", "result", "detail", "inputs"):
            out[k] = _j(v)
        else:
            out[k] = v
    return out


async def _exists(conn, table):
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table))


@router.get(BASE, dependencies=[Depends(require_read)])
async def capital_readiness_latest():
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            if not await _exists(conn, "capital_readiness_runs"):
                return {"label": "RESEARCH", "authority": "SHADOW_NO_AUTHORITY",
                        "status": "EMPTY", "why": "MIGRATION_310_NOT_APPLIED", "data": None}
            r = await conn.fetchrow("SELECT * FROM capital_readiness_runs ORDER BY computed_at DESC, run_id DESC LIMIT 1")
            if not r:
                return {"label": "RESEARCH", "authority": "SHADOW_NO_AUTHORITY",
                        "status": "EMPTY", "why": "NO_CAPITAL_READINESS_RUN_YET", "data": None}
            return {"label": "RESEARCH", "authority": "SHADOW_NO_AUTHORITY",
                    "status": "OK", "why": None, "data": _row(r)}
    except Exception as exc:  # noqa: BLE001
        return {"label": "RESEARCH", "authority": "SHADOW_NO_AUTHORITY",
                "status": "UNAVAILABLE", "why": f"{type(exc).__name__}: {str(exc)[:160]}", "data": None}


@router.get(BASE + "/agents", dependencies=[Depends(require_read)])
async def capital_readiness_agents(limit: int = Query(default=500, ge=1, le=5000)):
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _exists(conn, "capital_readiness_agent_economics"):
            return {"label": "RESEARCH", "authority": "SHADOW_NO_AUTHORITY",
                    "status": "EMPTY", "why": "MIGRATION_310_NOT_APPLIED", "data": []}
        rows = await conn.fetch("SELECT * FROM capital_readiness_agent_economics ORDER BY observed_at DESC, observation_id DESC LIMIT $1", int(limit))
        return {"label": "RESEARCH", "authority": "SHADOW_NO_AUTHORITY",
                "status": "OK" if rows else "EMPTY", "why": None if rows else "NO_AGENT_ECONOMIC_EVIDENCE", "data": [_row(r) for r in rows]}


@router.get(BASE + "/shadow-court", dependencies=[Depends(require_read)])
async def capital_readiness_court(limit: int = Query(default=100, ge=1, le=1000)):
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _exists(conn, "capital_readiness_shadow_court"):
            return {"label": "RESEARCH", "authority": "SHADOW_NO_AUTHORITY",
                    "status": "EMPTY", "why": "MIGRATION_310_NOT_APPLIED", "data": []}
        rows = await conn.fetch("SELECT * FROM capital_readiness_shadow_court ORDER BY recorded_at DESC, court_id DESC LIMIT $1", int(limit))
        return {"label": "RESEARCH", "authority": "SHADOW_NO_AUTHORITY",
                "status": "OK" if rows else "EMPTY", "why": None if rows else "NO_SHADOW_COURT_EVIDENCE", "data": [_row(r) for r in rows]}


@router.get(BASE + "/scale", dependencies=[Depends(require_read)])
async def capital_readiness_scale(limit: int = Query(default=100, ge=1, le=1000)):
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _exists(conn, "capital_readiness_scale_trials"):
            return {"label": "RESEARCH", "authority": "SHADOW_NO_AUTHORITY",
                    "status": "EMPTY", "why": "MIGRATION_310_NOT_APPLIED", "data": []}
        rows = await conn.fetch("SELECT * FROM capital_readiness_scale_trials ORDER BY computed_at DESC, trial_id DESC LIMIT $1", int(limit))
        return {"label": "RESEARCH", "authority": "SHADOW_NO_AUTHORITY",
                "status": "OK" if rows else "EMPTY", "why": None if rows else "NO_SCALE_TRIALS", "data": [_row(r) for r in rows]}
