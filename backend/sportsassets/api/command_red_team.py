"""RED TEAM CLOSEOUT + PM EVIDENCE PACK -- THE COMMAND READBACKS.

  GET  /api/command/red-team         the final readiness interlock (status,
                                     checks, blockers, every control with
                                     its evidence) and the latest receipts
  GET  /api/command/pm-acceptance    the golden production receipt, the
                                     forward scoreboard and the PM harness
                                     RED / YELLOW / GREEN from machine
                                     evidence (with provenance)
  POST /api/admin/red-team/release-receipt
                                     ADMIN: the pm-acceptance workflow's
                                     release receipt (GitHub exact-SHA gates
                                     + ancestry). Accepted ONLY when its
                                     deployed_sha is this API's own
                                     RENDER_GIT_COMMIT and tested == release
                                     == deployed; appended (315), never
                                     edited. A receipt is evidence, never
                                     authority.

The GETs run in READ ONLY transactions; nothing here can place, cancel,
size, fund or authorize anything.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response

from .agents_core import _pool, require_read

router = APIRouter()
LABEL = "SHADOW"
AUTHORITY = "READBACK_ONLY_NO_ORDER_NO_CAPITAL_AUTHORITY"
CACHE_S = 120.0
STATEMENT_TIMEOUT_MS = 90000
_CACHE: dict = {}
_HEX = re.compile(r"^[0-9a-f]{40}$")


def envelope(status, why=None, *, data=None, computed_at=None) -> dict:
    return {"label": LABEL, "authority": AUTHORITY, "status": status,
            "why": why, "computed_at": computed_at, "data": data}


def _admin(x_admin_token: str = Header(default="")) -> None:
    from ..config import settings
    supplied = (x_admin_token or "").strip()
    expected = (settings().admin_token or "").strip()
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="admin token required")


async def _ro(fn):
    pool = await _pool()
    async with pool.acquire(timeout=5.0) as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = %d"
                               % STATEMENT_TIMEOUT_MS)
            return await fn(conn)


async def latest_receipts(conn) -> dict:
    from ..redteam import controls as C
    if not await C._has(conn, "red_team_readiness_receipts"):
        return {"status": "MIGRATION_315_NOT_APPLIED"}
    r = await conn.fetchrow(
        "SELECT receipt_id, computed_at, implementation_sha, status, "
        " blockers FROM red_team_readiness_receipts ORDER BY computed_at "
        " DESC LIMIT 1")
    counts = {t: int(await conn.fetchval("SELECT count(*) FROM %s" % t))
              for t in ("red_team_readiness_receipts",
                        "red_team_control_receipts",
                        "red_team_profit_breaker_receipts",
                        "red_team_pair_execution_receipts",
                        "red_team_claim_exposure_receipts",
                        "red_team_release_receipts",
                        "red_team_settlement_certificates")}
    pairs = [dict(x) for x in await conn.fetch(
        "SELECT state, execution_label, economics_label, reason, count(*) n "
        "  FROM red_team_pair_execution_receipts GROUP BY 1, 2, 3, 4 "
        " ORDER BY 5 DESC LIMIT 20")]
    return {"latest_readiness": None if r is None else {
        "receipt_id": r["receipt_id"],
        "computed_at": r["computed_at"].timestamp(),
        "implementation_sha": r["implementation_sha"],
        "status": r["status"], "blockers": C._j(r["blockers"])},
        "counts": counts, "pair_receipts_by_state": pairs}


def _strip(res: dict) -> dict:
    out = {k: v for k, v in res.items()
           if k not in ("completion", "exposure_receipts")}
    out["completion_as_of"] = (res.get("completion") or {}).get("as_of")
    return out


def cached_completion(now: float) -> dict | None:
    """The completion readback this API process computed within its own
    cache window (GET /api/command/completion-readiness), reused so one
    request never pays for the whole completion read twice; older or absent
    -> None (evaluate reads it afresh)."""
    from . import command_completion_readiness as CCR
    hit = CCR._CACHE.get("main")
    if not hit or now - hit[0] >= CCR.CACHE_S:
        return None
    env = hit[1] or {}
    return env.get("data") if env.get("status") == "OK" else None


async def build(conn, *, now: float) -> dict:
    from ..redteam import readiness as R
    res = await R.evaluate(conn, now=now, completion=cached_completion(now))
    return {"readiness": _strip(res),
            "receipts": await latest_receipts(conn)}, res


@router.get("/api/command/red-team", dependencies=[Depends(require_read)])
async def red_team(response: Response) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    now = time.time()
    hit = _CACHE.get("red")
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        async def fn(conn):
            return (await build(conn, now=now))[0]
        data = await _ro(fn)
    except Exception as exc:                                    # noqa: BLE001
        return envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200]),
                        computed_at=now)
    out = envelope("OK", data=data, computed_at=now)
    _CACHE["red"] = (now, out)
    return out


async def pm_read(conn, *, now: float) -> dict:
    from ..pm_bind import acceptance as PA
    from ..pm_bind import golden as G
    from ..pm_bind import scoreboard as SB
    from ..redteam import controls as C
    from ..redteam import readiness as R
    from ..completion import read as CMP
    data, res = await build(conn, now=now)
    sec = CMP._Sections(conn)
    attributed, fixtures = await sec.run(
        "attribution", lambda: C.attributed_positions(conn, now=now),
        ([], {}))
    sb = await sec.run("scoreboard", lambda: SB.read(
        conn, now=now, attributed=attributed, fixtures=fixtures),
        {"status": "UNAVAILABLE"})
    if not sec.timings.get("attribution", {}).get("ok", True):
        # a scoreboard over no positions is not a scoreboard
        sb = {"status": "UNAVAILABLE", "why": "ATTRIBUTION_READ_FAILED"}
    rel = await sec.run("release_receipt", lambda: R.release_receipt(
        conn, res["implementation_sha"]), None)
    acc = PA.evaluate(red=res, scoreboard=sb, release=rel, now=now)
    return {"golden": G.receipt(), "scoreboard": sb, "pm_acceptance": acc,
            "release_receipt": None if rel is None else {
                k: (v.timestamp() if hasattr(v, "timestamp") else
                    (C._j(v) if k == "blockers" else v))
                for k, v in rel.items()},
            "red_team_status": res["status"],
            "read_timings": sec.timings,
            "authority": {"small_live": "SHADOW",
                          "kalshi_live_money": "NOT_ACTIVATED",
                          "adriana": "SHADOW_ONLY",
                          "historical_paper": "UNCHANGED",
                          "authority_expanded": False}}


@router.get("/api/command/pm-acceptance",
            dependencies=[Depends(require_read)])
async def pm_acceptance(response: Response) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    now = time.time()
    hit = _CACHE.get("pm")
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        async def fn(conn):
            return await pm_read(conn, now=now)
        data = await _ro(fn)
    except Exception as exc:                                    # noqa: BLE001
        return envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200]),
                        computed_at=now)
    out = envelope("OK", data=data, computed_at=now)
    _CACHE["pm"] = (now, out)
    return out


REQUIRED = ("accepted_base_sha", "tested_sha", "release_sha", "deployed_sha",
            "descendant_of_base", "backend_tests_green",
            "capital_critical_green", "commit_guard_green",
            "engine_diagnostic_green")


def validate_release(body: dict, *, running_sha: str) -> list:
    """The API's own checks before a CI release receipt is appended."""
    bad = [k for k in REQUIRED if k not in body]
    if bad:
        return ["MISSING:%s" % k for k in bad]
    out = []
    for k in ("accepted_base_sha", "tested_sha", "release_sha",
              "deployed_sha"):
        if not _HEX.match(str(body[k]).lower()):
            out.append("NOT_A_FULL_SHA:%s" % k)
    if str(body["deployed_sha"]).lower() != (running_sha or "").lower():
        out.append("DEPLOYED_SHA_IS_NOT_THIS_API")
    return out


@router.post("/api/admin/red-team/release-receipt",
             dependencies=[Depends(_admin)])
async def release_receipt(body: dict = Body(...)) -> dict:
    from ..red_team import release_guard as RG
    from ..red_team.models import ReleaseEvidence
    from ..redteam import controls as C
    running = (os.environ.get("RENDER_GIT_COMMIT") or "").strip().lower()
    bad = validate_release(body, running_sha=running)
    if bad:
        raise HTTPException(status_code=422, detail={"refused": bad})
    ev = ReleaseEvidence(
        tested_sha=str(body["tested_sha"]).lower(),
        release_sha=str(body["release_sha"]).lower(),
        deployed_sha=running, accepted_base_sha=str(
            body["accepted_base_sha"]).lower(),
        is_descendant_of_base=bool(body["descendant_of_base"]),
        backend_tests_green=bool(body["backend_tests_green"]),
        capital_critical_green=bool(body["capital_critical_green"]),
        commit_guard_green=bool(body["commit_guard_green"]),
        engine_diagnostic_green=bool(body["engine_diagnostic_green"]),
        migration_fingerprint_match=bool(body.get(
            "migration_fingerprint_match", False)))
    g = RG.release_gate(ev)
    blockers = list(g["blockers"])
    # the workers run the same release: a different live workers commit is
    # a release blocker (absent = not established, also a blocker)
    wk = str(body.get("workers_deployed_sha") or "").lower()
    if wk != running:
        blockers.append("WORKERS_NOT_ON_RELEASE_SHA:%s" % (wk[:12] or
                                                           "UNKNOWN"))
    green = bool(g["green"]) and not blockers
    rid = "rel:%s:%d" % (running[:12], int(time.time()))
    pool = await _pool()
    async with pool.acquire(timeout=5.0) as conn:
        if not await C._has(conn, "red_team_release_receipts"):
            raise HTTPException(status_code=503,
                                detail="MIGRATION_315_NOT_APPLIED")
        await conn.execute(
            "INSERT INTO red_team_release_receipts (receipt_id, "
            " accepted_base_sha, tested_sha, release_sha, deployed_sha, "
            " descendant_of_base, backend_tests_green, "
            " capital_critical_green, commit_guard_green, "
            " engine_diagnostic_green, migration_fingerprint_match, "
            " blockers) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,"
            " $12::jsonb) ON CONFLICT (receipt_id) DO NOTHING",
            rid, ev.accepted_base_sha, ev.tested_sha, ev.release_sha,
            ev.deployed_sha, ev.is_descendant_of_base,
            ev.backend_tests_green, ev.capital_critical_green,
            ev.commit_guard_green, ev.engine_diagnostic_green,
            ev.migration_fingerprint_match, json.dumps(blockers))
        if body.get("pm_acceptance"):
            pa = body["pm_acceptance"]
            h = hashlib.sha256(json.dumps(pa, sort_keys=True, default=str)
                               .encode()).hexdigest()
            await conn.execute(
                "INSERT INTO red_team_control_receipts (receipt_id, "
                " implementation_sha, control, status, blockers, evidence, "
                " evidence_hash) VALUES ($1,$2,'PM_ACCEPTANCE_CI',$3,"
                " $4::jsonb,$5::jsonb,$6) ON CONFLICT (receipt_id) DO NOTHING",
                "pmci:%s:%d" % (running[:12], int(time.time())), running,
                {"GREEN": "GREEN", "YELLOW": "RED", "RED": "RED"}.get(
                    str(pa.get("pm_state")), "UNKNOWN"),
                json.dumps(pa.get("critical_failures", []) + pa.get(
                    "economic_or_evidence_gaps", [])),
                json.dumps(pa, default=str), h)
    _CACHE.clear()
    return {"receipt_id": rid, "release_gate": {
        "green": green, "blockers": blockers},
        "authority": "EVIDENCE_ONLY_NO_AUTHORITY"}
