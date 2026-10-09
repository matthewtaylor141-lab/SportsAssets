"""THE CONFIDENCE LADDER: GET /api/command/confidence-ladder (GET only,
COMMAND auth via agents_core.require_read). READ ONLY.

For each strategy and overall (the INVESTMENT sleeve), the highest level of
the owner's confidence ladder whose evidence is met, and the blockers of the
next level (profitability/confidence_ladder.py):

  0 RESEARCH  1 INVESTMENT PAPER ONLY  2 PAPER STATISTICALLY SUPPORTED
  3 LIVE SHADOW PARITY  4 TINY LIVE EXECUTION VALIDATION
  5 LIVE ECONOMIC VALIDATION  6 SCALE

READS (SELECT only, one READ ONLY transaction under a statement timeout):
  * the paper ledger's positions + the durable sleeves, exactly as the
    profitability validation reads them (command_validation.gather);
  * the production cutover (live_parity_cutover);
  * the parity ledger: live_parity.readiness_report overall, and
    live_parity.readiness over each strategy's rows of the same forward
    window;
  * the SMALL LIVE control and the live venue records (levels 4-6 are
    NOT_REACHED while SMALL LIVE is SHADOW; level 4 also lists
    live_parity's tiny-live readiness blockers, with the scope's own
    validation verdict in place of readiness_report's NOT_EVALUATED).

IMPORTS: live_parity is imported only for its READ functions
(readiness_report, readiness); its module top imports execmirror, and no
venue, order, submit or control function of either is called here.

ANSWERS (the profitability envelope): data = {version, levels_spec,
cutover, overall, strategies: {STRATEGY: {...}}, power_rule}; every scope
carries book, sleeve, strategy, policy_versions, confidence_scope, level,
next_level, blockers_for_next_level and every level's evidence.

No route here writes, sends an order or changes capital, limits, controls
or thresholds. An unreadable source is UNAVAILABLE with its reason.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/confidence-ladder"
STATEMENT_TIMEOUT_MS = 8000
CACHE_S = 15.0
#: level 0's "exists in the records" window for paper decisions
RECENT_DECISION_DAYS = 30.0
_CACHE: dict = {}


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


async def _has(conn, rel: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    rel))


async def parity_inputs(conn) -> tuple:
    """(overall readiness, {strategy: readiness}, why-unavailable) from the
    parity ledger -- the same forward window readiness_report uses."""
    if not await _has(conn, "live_parity_ledger"):
        return None, {}, "MIGRATION_225_NOT_APPLIED"
    from .. import live_parity as LP
    overall = await LP.readiness_report(conn)
    since = overall.get("since")
    by: dict = {}
    if overall.get("cutover") is not None:
        rows = []
        for r in await conn.fetch(
                "SELECT intent_kind, sleeve, strategy, parity_state, "
                "       divergence_fields, comparison, created_at "
                "  FROM live_parity_ledger "
                " WHERE ($1::double precision IS NULL "
                "        OR created_at >= to_timestamp($1)) "
                " ORDER BY created_at, parity_id LIMIT 200000", since):
            d = dict(r)
            d["comparison"] = _j(d["comparison"]) or {}
            rows.append(d)
        halted = bool((overall.get("control") or {}).get("halted"))
        strategies = {r["strategy"] for r in rows if r.get("strategy")}
        for s in strategies:
            rep = LP.readiness([r for r in rows if r.get("strategy") == s],
                               halted=halted)
            rep["since"] = since
            by[s] = rep
    return overall, by, None


async def live_state(conn) -> dict:
    """The recorded live state for levels 4-6 (read only)."""
    out = {"small_live_mode": None, "small_live_halted": None,
           "live_venue_order_events": None,
           "canonical_live_executions_by_mode": None,
           "execmirror_enabled": None, "execmirror_stopped": None,
           "why": None}
    if await _has(conn, "small_live_control"):
        r = await conn.fetchrow(
            "SELECT mode, halted FROM small_live_control WHERE id = 1")
        if r is not None:
            out.update(small_live_mode=r["mode"],
                       small_live_halted=bool(r["halted"]))
        out["live_venue_order_events"] = await conn.fetchval(
            "SELECT count(*) FROM small_live_order_events") \
            if await _has(conn, "small_live_order_events") else None
        if await _has(conn, "canonical_intent_executions"):
            out["canonical_live_executions_by_mode"] = {
                r["mode"]: r["n"] for r in await conn.fetch(
                    "SELECT mode, count(*) AS n FROM "
                    " canonical_intent_executions WHERE adapter = "
                    " 'SMALL_LIVE' GROUP BY mode")}
    else:
        out["why"] = "MIGRATION_225_NOT_APPLIED"
    if await _has(conn, "execmirror_control"):
        r = await conn.fetchrow(
            "SELECT enabled, stopped FROM execmirror_control LIMIT 1")
        if r is not None:
            out.update(execmirror_enabled=r["enabled"],
                       execmirror_stopped=r["stopped"])
    # the legacy execution mirror's venue orders are REPORTED beside the
    # canonical path (never hidden), by strategy: a mirror order with a venue
    # order id reached the venue. The ladder does not count them as level-4
    # validation (see confidence_ladder.live_levels).
    out["execmirror_venue_orders_by_strategy"] = None
    if await _has(conn, "execmirror_orders"):
        out["execmirror_venue_orders_by_strategy"] = {
            (r["strategy"] or "UNRECORDED"): {
                "with_venue_order_id": int(r["sent"]),
                "filled_qty": float(r["filled"] or 0)}
            for r in await conn.fetch(
                "SELECT strategy, count(*) AS sent, sum(cum_qty) AS filled "
                "  FROM execmirror_orders WHERE venue_order_id IS NOT NULL "
                " GROUP BY strategy")}
    return out


async def _read(conn, now: float) -> dict:
    from .. import bettor_paper_ledger as L
    from ..profitability import confidence_ladder as CL
    from . import command_validation as CV
    nested = conn.is_in_transaction()
    tr = conn.transaction(readonly=not nested)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % STATEMENT_TIMEOUT_MS)
        data, why = await CV.gather(conn, await L.selected_account(conn), now=now)
        cutover = await CV.production_cutover_epoch(conn)
        overall, by, pwhy = await parity_inputs(conn)
        live = await live_state(conn)
        # recent decisions per strategy (bounded by the decided_at index)
        recent = {r["strategy"]: int(r["n"]) for r in await conn.fetch(
            "SELECT strategy, count(*) AS n FROM paper_decisions "
            " WHERE account_id = $1 AND decided_at >= to_timestamp($2) "
            " GROUP BY strategy", await L.selected_account(conn),
            now - RECENT_DECISION_DAYS * 86400.0)}
    finally:
        await tr.rollback()
    if data is None:
        return {"status": "UNAVAILABLE", "why": why, "data": None}
    positions = data.get("positions") or []
    from ..profitability import common as C
    # every strategy the sleeve classifier knows, plus any the records name
    strategies = set(C.STRATEGY_SLEEVE) | set(recent) | set(by) | {
        p.get("strategy") for p in positions if p.get("strategy")}
    presence = {s: {"decisions_recent": recent.get(s, 0),
                    "decisions_recent_days": RECENT_DECISION_DAYS,
                    "parity_rows": (by.get(s) or {}).get("candidate_count",
                                                         0)
                    + (by.get(s) or {}).get("management_count", 0)}
                for s in strategies}
    from .. import live_parity as LP
    if pwhy is None:
        halted = bool(((overall or {}).get("control") or {}).get("halted"))
        for s in strategies:
            if s not in by:
                # no parity row of this strategy in the forward window: the
                # gate over an empty sample (its blockers name the shortfall)
                rep = LP.readiness([], halted=halted)
                if (overall or {}).get("cutover") is None:
                    rep["blockers"].insert(0, "NO_PRODUCTION_CUTOVER_RECORDED")
                by[s] = rep
    out = CL.compute(positions=positions, strategies=strategies,
                     cutover=cutover, parity_overall=overall,
                     parity_by_strategy=by, parity_why=pwhy, live=live,
                     now=now, presence=presence)
    return {"status": "OK", "why": None, "data": out}


@router.get(PATH, dependencies=[Depends(require_read)])
async def confidence_ladder() -> dict:
    from ..profitability import common as C
    now = time.time()
    hit = _CACHE.get("ladder")
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            got = await _read(conn, now)
    except Exception as exc:                                    # noqa: BLE001
        return C.envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                                     str(exc)[:160]),
                          data=None)
    out = C.envelope(got["status"], got.get("why"), computed_at=now,
                     data=got.get("data"), summed_across_sleeves=False,
                     production_confidence_scope={
                         "sleeve": "INVESTMENT",
                         "rule": ("levels 1-3 count INVESTMENT-sleeve "
                                  "evidence only; TRAINING / BENCHMARK / "
                                  "UNCLASSIFIED strategies stay at level 0 "
                                  "with their reason")})
    _CACHE["ladder"] = (now, out)
    return out
