"""THE SHADOW INTELLIGENCE READS: /api/command/intel/* (GET only, COMMAND
auth via agents_core.require_read -> api.app.require_command).

EVERY RESPONSE IS SHADOW and says so:
    {"label": "SHADOW", "authority": "SHADOW_NO_AUTHORITY",
     "disclosure": "...", "status": "OK" | "EMPTY" | "UNAVAILABLE",
     "why": <reason or null>, "run_id": ..., "computed_at": <epoch>,
     "data": ...}
EMPTY names why (no run yet, migration 208 absent); UNAVAILABLE names the
failed read. A failed read is never shown as zeros.

ROUTES (the logic is sportsassets/intel/*; these only read intel_* tables):
  GET /api/command/intel              latest run per component
  GET /api/command/intel/allocator    A: ranked shadow allocation of the
                                      $1,000 sleeve, reasons per candidate
  GET /api/command/intel/calibration  D: segments (Brier, log loss, CIs,
                                      reliability), drift, overlay register
                                      and the frozen OOS protocol
  GET /api/command/intel/attribution  E: per-position attribution rows
                                      (?book=PAPER|ACTUAL&limit=) + totals
  GET /api/command/intel/sizing       I: shadow size beside paper/actual
                                      size per decision (?limit=)
  GET /api/command/intel/risk         J: PAPER and ACTUAL reports side by
                                      side (never summed) + Audrey's
                                      independent recompute checks
  GET /api/command/intel/regime       K: NORMAL / REDUCE / NO_TRADE with
                                      reasons and signals (?history=)
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query

from .agents_core import _pool, require_read

router = APIRouter()
BASE = "/api/command/intel"


def _env(status, why=None, **kw) -> dict:
    from ..intel import common as C
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


def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if hasattr(v, "timestamp"):
            out[k] = v.timestamp()
        elif k in ("payload", "summary", "reasons", "signals", "inputs",
                   "unmeasured", "detail", "factors", "reliability",
                   "params", "protocol", "oos_result"):
            out[k] = _j(v)
        elif v is not None and type(v).__name__ == "Decimal":
            out[k] = float(v)
        else:
            out[k] = v
    return out


async def _ready(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass('intel_snapshots') IS NOT NULL"))


async def _latest(conn, component, book="NONE"):
    return await conn.fetchrow(
        "SELECT run_id, payload, extract(epoch FROM computed_at)::float8 "
        "       AS computed_at FROM intel_snapshots "
        " WHERE component = $1 AND book = $2 "
        " ORDER BY computed_at DESC LIMIT 1", component, book)


async def _read(fn):
    pool = await _pool()
    try:
        async with pool.acquire() as conn:
            if not await _ready(conn):
                return _env("EMPTY", "MIGRATION_208_NOT_APPLIED", data=None)
            return await fn(conn)
    except Exception as exc:                                    # noqa: BLE001
        return _env("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                               str(exc)[:160]), data=None)


def _snap(row, extra=None, empty_why="NO_SHADOW_RUN_YET"):
    if row is None:
        return _env("EMPTY", empty_why, run_id=None, computed_at=None,
                    data=None, **(extra or {}))
    return _env("OK", None, run_id=row["run_id"],
                computed_at=row["computed_at"], data=_j(row["payload"]),
                **(extra or {}))


@router.get(BASE, dependencies=[Depends(require_read)])
async def intel_index() -> dict:
    async def fn(conn):
        rows = await conn.fetch(
            "SELECT DISTINCT ON (component) run_id, component, status, "
            "       error, duration_ms, summary, version, "
            "       extract(epoch FROM started_at)::float8 AS started_at "
            "  FROM intel_runs ORDER BY component, started_at DESC")
        data = {r["component"]: _row(r) for r in rows}
        return _env("OK" if data else "EMPTY",
                    None if data else "NO_SHADOW_RUN_YET", data=data,
                    routes=[BASE + s for s in (
                        "/allocator", "/calibration", "/attribution",
                        "/sizing", "/risk", "/regime")])
    return await _read(fn)


@router.get(BASE + "/allocator", dependencies=[Depends(require_read)])
async def intel_allocator() -> dict:
    async def fn(conn):
        return _snap(await _latest(conn, "ALLOCATOR"))
    return await _read(fn)


@router.get(BASE + "/calibration", dependencies=[Depends(require_read)])
async def intel_calibration() -> dict:
    async def fn(conn):
        ovs = [_row(r) for r in await conn.fetch(
            "SELECT * FROM intel_calibration_overlays "
            " ORDER BY frozen_at DESC LIMIT 20")]
        return _snap(await _latest(conn, "CALIBRATION"),
                     extra={"overlays": ovs,
                            "production_probabilities_modified": False})
    return await _read(fn)


@router.get(BASE + "/attribution", dependencies=[Depends(require_read)])
async def intel_attribution(book: str = Query(default="",
                                              pattern="^(|PAPER|ACTUAL)$"),
                            limit: int = Query(default=100, ge=1,
                                               le=1000)) -> dict:
    async def fn(conn):
        rows = [_row(r) for r in await conn.fetch(
            "SELECT * FROM intel_attribution "
            " WHERE ($1 = '' OR book = $1) "
            " ORDER BY computed_at DESC, subject_id LIMIT $2",
            book, int(limit))]
        snap = await _latest(conn, "ATTRIBUTION")
        out = _snap(snap, extra={"rows": rows})
        if snap is not None and not rows:
            out["status"], out["why"] = "EMPTY", "NO_ATTRIBUTED_POSITION"
        return out
    return await _read(fn)


@router.get(BASE + "/sizing", dependencies=[Depends(require_read)])
async def intel_sizing(limit: int = Query(default=100, ge=1,
                                          le=1000)) -> dict:
    async def fn(conn):
        rows = [_row(r) for r in await conn.fetch(
            "SELECT * FROM intel_sizing ORDER BY computed_at DESC, "
            " decision_id LIMIT $1", int(limit))]
        snap = await _latest(conn, "SIZING")
        out = _snap(snap, extra={"rows": rows, "applied": False})
        if snap is not None and not rows:
            out["status"], out["why"] = "EMPTY", "NO_ENTER_DECISION_TO_SIZE"
        return out
    return await _read(fn)


@router.get(BASE + "/risk", dependencies=[Depends(require_read)])
async def intel_risk() -> dict:
    async def fn(conn):
        paper = await _latest(conn, "RISK", "PAPER")
        actual = await _latest(conn, "RISK", "ACTUAL")
        checks = []
        rid = paper["run_id"] if paper is not None else None
        if rid:
            checks = [_row(r) for r in await conn.fetch(
                "SELECT * FROM intel_audrey_risk_checks WHERE run_id = $1 "
                " ORDER BY book, metric", rid)]
        books = {"PAPER": _snap(paper), "ACTUAL": _snap(actual)}
        ok = paper is not None or actual is not None
        return _env("OK" if ok else "EMPTY",
                    None if ok else "NO_SHADOW_RUN_YET",
                    run_id=rid, data=books, audrey_checks=checks,
                    summed_across_books=False)
    return await _read(fn)


@router.get(BASE + "/regime", dependencies=[Depends(require_read)])
async def intel_regime(history: int = Query(default=24, ge=0,
                                            le=500)) -> dict:
    async def fn(conn):
        hist = [_row(r) for r in await conn.fetch(
            "SELECT run_id, recommendation, reasons, authority, applied, "
            "       extract(epoch FROM computed_at)::float8 AS computed_at "
            "  FROM intel_regime_states ORDER BY computed_at DESC LIMIT $1",
            int(history))] if history else []
        return _snap(await _latest(conn, "REGIME"),
                     extra={"history": hist, "applied": False})
    return await _read(fn)
