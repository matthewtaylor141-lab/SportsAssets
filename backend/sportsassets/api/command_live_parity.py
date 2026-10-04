"""BETTOR LIVE PARITY (R30): the canonical intents, both adapters, the parity
ledger and the live readiness gate.

    GET  /api/command/live-parity            the readiness gate (INVESTMENT
                                             sleeve, forward sample), the
                                             SMALL LIVE control (mode SHADOW,
                                             halt), ledger counts by state and
                                             kind, the most recent rows
         ?since=<epoch>                      a forward window
    GET  /api/command/live-parity/intent/{intent_id}
                                             one canonical intent (decision or
                                             management) with both adapter
                                             records and its parity row
    POST /api/admin/live-parity/clear-halt   a NAMED HUMAN clears a
                                             LOGIC_DIVERGENCE halt (admin
                                             token; the database refuses an
                                             agent or system actor). The
                                             divergence stays in the ledger.

READ routes run in a READ ONLY transaction under a statement timeout. No route
here sends an order or changes capital, limits, credentials or thresholds.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import live_parity as LP
from .agents_core import _pool, require_read

router = APIRouter()
STATEMENT_TIMEOUT_MS = 6000


def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        elif isinstance(v, str) and k in (
                "evidence", "opportunity_score", "derek", "karen", "allie",
                "eddie", "contract", "sizing_basis", "target_limit",
                "alternatives", "freshness", "reason", "requested",
                "venue_params", "refs", "comparison"):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        elif v.__class__.__name__ == "Decimal":
            v = str(v)
        out[k] = v
    return out


async def _profitability(conn) -> dict | None:
    """The INVESTMENT sleeve's FORWARD profitability verdict (R30
    profitability validation), since the R30 cutover."""
    import time
    try:
        from . import command_validation as CV
    except ImportError:
        return None
    try:
        rep = await CV._read(conn, since=None,
                             since_source="PRODUCTION_CUTOVER",
                             now=time.time())
        v = (rep.get("data") or {}).get("profitability_verdict")
        if isinstance(v, dict):
            return {"profitability_verdict": v.get("verdict"), "detail": v}
        return {"profitability_verdict": v,
                "why": rep.get("why") if v is None else None}
    except Exception as exc:                                  # noqa: BLE001
        return {"profitability_verdict": None,
                "why": "VALIDATION_UNREADABLE:%s" % type(exc).__name__}


@router.get("/api/command/live-parity")
async def live_parity(_auth: str = Depends(require_read),
                      since: float | None = Query(default=None),
                      limit: int = Query(default=25, ge=1, le=200)) -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = %d"
                               % STATEMENT_TIMEOUT_MS)
            prof = await _profitability(conn)
            gate = await LP.readiness_report(conn, since=since,
                                             profitability=prof)
            counts = [_row(r) for r in await conn.fetch(
                """SELECT intent_kind, sleeve, parity_state, count(*) AS n
                     FROM live_parity_ledger GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""")]
            recent = [_row(r) for r in await conn.fetch(
                """SELECT parity_id, intent_kind, intent_id, strategy, sleeve,
                          parity_state, divergence_fields, capital_scale,
                          created_at FROM live_parity_ledger
                    ORDER BY created_at DESC LIMIT $1""", limit)]
            intents = await conn.fetchrow(
                """SELECT (SELECT count(*) FROM canonical_decision_intents) AS decisions,
                          (SELECT count(*) FROM canonical_management_intents) AS management,
                          (SELECT count(*) FROM canonical_intent_executions
                            WHERE adapter = 'SMALL_LIVE') AS live_shadow,
                          (SELECT count(*) FROM small_live_order_events) AS live_venue_events""")
    return {"version": LP.READINESS_VERSION, "mode": LP.SMALL_LIVE_MODE,
            "readiness": gate, "counts": counts, "recent": recent,
            "intents": dict(intents or {}),
            "authority": "SHADOW_NO_CAPITAL",
            "disclosure": ("SMALL LIVE is SHADOW: venue orders are constructed "
                           "from the canonical intent and recorded, never "
                           "sent. Activation needs the parity gate AND the "
                           "owner's explicit approval.")}


@router.get("/api/command/live-parity/intent/{intent_id}")
async def live_parity_intent(intent_id: str,
                             _auth: str = Depends(require_read)) -> dict:
    if not (intent_id.startswith("cdi_") or intent_id.startswith("cmi_")):
        raise HTTPException(404, detail={"reason": "NOT_A_CANONICAL_INTENT"})
    table = ("canonical_decision_intents" if intent_id.startswith("cdi_")
             else "canonical_management_intents")
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = %d"
                               % STATEMENT_TIMEOUT_MS)
            it = await conn.fetchrow(
                "SELECT * FROM %s WHERE intent_id = $1" % table, intent_id)
            if it is None:
                raise HTTPException(404, detail={"reason": "NO_SUCH_INTENT"})
            ex = await conn.fetch(
                """SELECT * FROM canonical_intent_executions WHERE intent_id = $1
                    ORDER BY adapter""", intent_id)
            par = await conn.fetchrow(
                "SELECT * FROM live_parity_ledger WHERE intent_id = $1",
                intent_id)
    body = _row(it)
    verified = None
    if intent_id.startswith("cdi_"):
        try:
            verified = LP.verify_intent(dict(it))
        except Exception:                                     # noqa: BLE001
            verified = False
    return {"intent": body, "sha_verified": verified,
            "executions": [_row(r) for r in ex],
            "parity": None if par is None else _row(par)}


async def _require_admin(request: Request) -> None:
    from . import app as A
    A.require_admin(x_admin_token=request.headers.get("x-admin-token", ""))


@router.post("/api/admin/live-parity/clear-halt",
             dependencies=[Depends(_require_admin)])
async def clear_halt(body: dict | None = None) -> dict:
    """A NAMED HUMAN clears a LOGIC_DIVERGENCE halt. `actor` (the person) and
    `reason` are required; the database refuses a system or agent actor."""
    b = body or {}
    actor, reason = str(b.get("actor") or "").strip(), str(
        b.get("reason") or "").strip()
    if not actor or not reason:
        raise HTTPException(400, detail={"reason": "ACTOR_AND_REASON_REQUIRED"})
    pool = await _pool()
    async with pool.acquire() as conn:
        try:
            ctl = await LP.clear_halt(conn, actor=actor, reason=reason)
        except Exception as exc:                              # noqa: BLE001
            raise HTTPException(409, detail={
                "reason": "HALT_NOT_CLEARED", "detail": str(exc)[:300]})
    return {"control": {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                        for k, v in ctl.items()}, "mode": LP.SMALL_LIVE_MODE}
