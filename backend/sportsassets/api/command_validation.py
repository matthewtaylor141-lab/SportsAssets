"""PROFITABILITY VALIDATION: GET /api/command/profitability/validation
(GET only, COMMAND auth via agents_core.require_read). READ ONLY.

    ?since=<epoch>   the forward cutover (default: the R30 cutover,
                     profitability.validation.cutover_epoch -- overridable by
                     BETTOR_R30_CUTOVER_EPOCH)

ANSWERS (the profitability envelope):
    {label: RESEARCH, authority: SHADOW_NO_AUTHORITY, status, why,
     computed_at, disclosure,
     data: {since, since_source, cutover, forward_rule,
            sleeves: {INVESTMENT|TRAINING|BENCHMARK|UNCLASSIFIED:
                      {role, activation_evidence,
                       windows: {ALL_TIME|FORWARD: {window, positions,
                                 metrics: {METRIC: {value, unit, basis,
                                           sample_n, window, status, why,
                                           detail}}}}}},
            book_wide: {FALSE_REFUSAL_RATE: {ALL_TIME, FORWARD}},
            profitability_verdict: {verdict, why, checks, failed_checks,
                                    rule, evidence, claim},
            activation_evidence, sources_unavailable, metric_units}}

Every metric is computed by the PURE profitability.validation module over
rows read here: the paper ledger (bettor_paper_ledger.positions / balances,
exactly as the equity wall reads it), the durable sleeve classifications
(migration 223), pos_economics_latest (216), intel_attribution (208),
lol_ledger / lol_opportunity_scores_latest (220), eddie_execution_outcomes
(217) and xavier_value_add (206). A source whose migration is absent makes
its metrics UNAVAILABLE with that reason; nothing is zero-filled.

The verdict is for the INVESTMENT sleeve's FORWARD window ONLY: TRAINING,
BENCHMARK and UNCLASSIFIED are never pooled into it.

Everything runs inside a READ ONLY transaction under a statement timeout.
This module imports no order, venue, execution or funded module and writes
nothing.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Query

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/profitability/validation"
STATEMENT_TIMEOUT_MS = 8000
CACHE_S = 15.0
_CACHE: dict = {}

#: {source: (relation that must exist, reason when it does not)}
SOURCES = {
    "econ": ("pos_economics_latest", "MIGRATION_216_NOT_APPLIED"),
    "attribution": ("intel_attribution", "MIGRATION_208_NOT_APPLIED"),
    "scores": ("lol_opportunity_scores_latest", "MIGRATION_220_NOT_APPLIED"),
    "refusals": ("lol_ledger", "MIGRATION_220_NOT_APPLIED"),
    "exec_outcomes": ("eddie_execution_outcomes",
                      "MIGRATION_217_NOT_APPLIED"),
    "value_add": ("xavier_value_add", "MIGRATION_206_NOT_APPLIED"),
}

ENTRY_MAP = (
    "SELECT DISTINCT ON (decision_id) decision_id, group_id "
    "  FROM paper_orders "
    " WHERE account_id = $1 AND role = 'ENTRY' AND decision_id IS NOT NULL "
    " ORDER BY decision_id, created_at")


def _f(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def position_rows(allp: list, open_views: list, classes: dict) -> list:
    """The ledger's positions (open AND closed) + the open positions' marks
    + the durable classifications -> the rows profitability.validation
    reads. A group without a durable classification is UNCLASSIFIED. Pure."""
    views = {v.get("position_key"): v for v in open_views or []}
    out = []
    for p in allp:
        open_qty = _f(p.get("open_qty")) or 0.0
        v = views.get(p.get("position_key")) or {}
        mark = v.get("mark") or {}
        marked = open_qty > 1e-9 and mark.get("price") is not None
        settle = p.get("settlement") or {}
        ends = [t for t in (_f(p.get("last_fill_at")),
                            _f(settle.get("settled_at"))) if t is not None]
        acq = _f(p.get("acquisition_cost_usd"))
        bf, sf = _f(p.get("buy_fees_usd")), _f(p.get("sale_fees_usd"))
        sp = _f(p.get("sale_proceeds_net_usd"))
        gross = None
        if acq is not None:
            gross = (acq - (bf or 0.0)) + ((sp or 0.0) + (sf or 0.0))
        c = classes.get(p.get("group_id")) or {}
        out.append({
            "position_key": p.get("position_key"),
            "group_id": p.get("group_id"),
            "sleeve": c.get("sleeve") or "UNCLASSIFIED",
            "strategy": c.get("strategy") or p.get("strategy"),
            "first_fill_at": _f(p.get("first_fill_at")),
            "released_at": (max(ends) if ends and open_qty <= 1e-9
                            else None),
            "open_qty": open_qty,
            "realized_pnl_usd": _f(p.get("realized_pnl_usd")),
            "unrealized_pnl_usd": (_f(v.get("unrealized_pnl_usd"))
                                   if marked else None),
            "marked": marked,
            "cost_basis_usd": _f(p.get("cost_basis_usd")),
            "buy_fees_usd": bf, "sale_fees_usd": sf,
            "acquisition_cost_usd": acq, "gross_traded_usd": gross})
    return out


async def gather(conn, account_id: str, *, now: float) -> tuple:
    """(data, sources_unavailable) for profitability.validation.compute, or
    (None, reason) when the paper book or the sleeves cannot be read.
    SELECTs only; the caller holds the READ ONLY transaction."""
    from .. import bettor_paper_ledger as L
    from .. import bettor_paper_sleeves as SL
    if not await SL.schema(conn):
        return None, SL.R_NO_SCHEMA
    bal = await L.balances(conn, account_id, now=now)
    if not bal or not bal.get("ok"):
        return None, (bal or {}).get("refusal") or "PAPER_NOT_READ"
    allp = await L.positions(conn, account_id, include_closed=True)
    classes = await SL.classifications(conn, account_id)
    positions = position_rows(allp, bal.get("open_positions") or [],
                              classes)
    groups = sorted({p["group_id"] for p in positions
                     if p.get("group_id")})
    present = {}
    for name, (rel, _why) in SOURCES.items():
        present[name] = bool(await conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", rel))
    sources = {n: SOURCES[n][1] for n, ok in present.items() if not ok}
    data = {"positions": positions, "econ": [], "attribution": [],
            "scores": [], "refusals": [], "exec_outcomes": [],
            "value_add": []}
    if present["econ"]:
        data["econ"] = [{
            "position_key": r["position_key"], "group_id": r["group_id"],
            "state": r["state"], "net_profit_usd": _f(r["net_profit_usd"]),
            "capital_hours": _f(r["capital_hours"]),
            "capital_committed_usd": _f(r["capital_committed_usd"])}
            for r in await conn.fetch(
                "SELECT position_key, group_id, state, net_profit_usd, "
                "       capital_hours, capital_committed_usd "
                "  FROM pos_economics_latest "
                " WHERE book = 'PAPER' AND group_id = ANY($1::text[])",
                groups)]
    if present["attribution"]:
        data["attribution"] = [{
            "group_id": r["group_id"], "slippage_usd": _f(r["slippage_usd"]),
            "slippage_pc": _f(r["slippage_pc"])}
            for r in await conn.fetch(
                "SELECT group_id, slippage_usd, slippage_pc "
                "  FROM intel_attribution "
                " WHERE book = 'PAPER' AND group_id = ANY($1::text[])",
                groups)]
    if present["scores"]:
        data["scores"] = [{
            "decision_id": r["candidate_id"], "group_id": r["group_id"],
            "opportunity_score": _f(r["opportunity_score"]),
            "version": r["version"]}
            for r in await conn.fetch(
                "WITH e AS (" + ENTRY_MAP + ") "
                "SELECT s.candidate_id, s.opportunity_score, s.version, "
                "       e.group_id "
                "  FROM lol_opportunity_scores_latest s "
                "  JOIN e ON e.decision_id = s.candidate_id "
                " WHERE s.status = 'MEASURED'", account_id)]
    if present["refusals"]:
        rows = await conn.fetch(
            "SELECT DISTINCT ON (decision_ref) decision_ref, classification,"
            "       strategy, classifier_version, hypothetical_pnl_usd, "
            "       extract(epoch FROM decided_at)::float8 AS decided_at "
            "  FROM lol_ledger "
            " ORDER BY decision_ref, classified_at DESC, ledger_id DESC")
        data["refusals"] = [{
            "decision_ref": r["decision_ref"],
            "classification": r["classification"],
            "strategy": r["strategy"],
            # by the refused decision's strategy; none recorded -> not
            # attributable (book-wide only)
            "sleeve": (SL.classify(r["strategy"], None)[0]
                       if r["strategy"] else None),
            "classifier_version": r["classifier_version"],
            "decided_at": _f(r["decided_at"]),
            "hypothetical_pnl_usd": _f(r["hypothetical_pnl_usd"])}
            for r in rows]
    if present["exec_outcomes"]:
        data["exec_outcomes"] = [{
            "decision_id": r["decision_id"], "group_id": r["group_id"],
            "realized_execution_loss_pp":
                _f(r["realized_execution_loss_pp"]),
            "predicted_execution_loss_pp":
                _f(r["predicted_execution_loss_pp"]),
            "naive_execution_loss_pp": _f(r["naive_execution_loss_pp"]),
            "filled_qty": _f(r["filled_qty"])}
            for r in await conn.fetch(
                "WITH e AS (" + ENTRY_MAP + ") "
                "SELECT o.decision_id, e.group_id, "
                "       o.realized_execution_loss_pp, "
                "       o.predicted_execution_loss_pp, "
                "       o.naive_execution_loss_pp, o.filled_qty "
                "  FROM eddie_execution_outcomes o "
                "  JOIN e ON e.decision_id = o.decision_id "
                " WHERE o.source = 'PAPER'", account_id)]
    if present["value_add"]:
        data["value_add"] = [{
            "thesis_id": r["thesis_id"], "group_id": r["group_id"],
            "status": r["status"], "incremental": _j(r["incremental"]) or {}}
            for r in await conn.fetch(
                "SELECT DISTINCT ON (thesis_id) thesis_id, group_id, status,"
                "       incremental "
                "  FROM xavier_value_add "
                " WHERE position_kind = 'PAPER' "
                "   AND group_id = ANY($1::text[]) "
                " ORDER BY thesis_id, computed_at DESC, value_add_id DESC",
                groups)]
    return data, sources


async def _read(conn, *, since, since_source: str, now: float) -> dict:
    import asyncio

    from .. import bettor_paper_ledger as L
    from ..profitability import validation as V
    nested = conn.is_in_transaction()
    tr = conn.transaction(readonly=not nested)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % STATEMENT_TIMEOUT_MS)
        data, sources = await gather(conn, L.ACCOUNT_ID, now=now)
    finally:
        await tr.rollback()
    if data is None:
        return {"status": "UNAVAILABLE", "why": sources, "data": None}
    cutover, _src = V.cutover_epoch()
    out = await asyncio.to_thread(
        V.compute, data, now=now, since=since, cutover=cutover,
        since_source=since_source, sources=sources)
    return {"status": "OK", "why": None, "data": out}


@router.get(PATH, dependencies=[Depends(require_read)])
async def profitability_validation(
        since: float | None = Query(default=None, ge=0)) -> dict:
    from ..profitability import common as C
    from ..profitability import validation as V
    now = time.time()
    if since is None:
        since, src = V.cutover_epoch()
    else:
        src = "QUERY"
    key = (round(float(since), 3), src)
    hit = _CACHE.get(key)
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            got = await _read(conn, since=float(since), since_source=src,
                              now=now)
    except Exception as exc:                                    # noqa: BLE001
        return C.envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                                     str(exc)[:160]),
                          data=None, since=since)
    out = C.envelope(got["status"], got.get("why"), computed_at=now,
                     data=got.get("data"), since=since,
                     summed_across_books=False,
                     summed_across_sleeves=False)
    _CACHE[key] = (now, out)
    return out
