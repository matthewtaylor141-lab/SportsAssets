"""THE ONLY WRITER OF THE SHADOW INTELLIGENCE LAYER: migration 208's
intel_* tables, and nothing else (pinned by
tests/test_intel_is_shadow_only.py, which parses every SQL statement in
sportsassets/intel and refuses a write to any other table).

Bounded history: snapshots, segments, allocations and checks older than
RETENTION_DAYS are pruned from these tables by the runner.
"""
from __future__ import annotations

import json
import uuid

from . import common as C

VERSION = "INTEL_STORE_V1"
RETENTION_DAYS = 30


def _j(v) -> str:
    return json.dumps(v, default=str)


def new_run_id() -> str:
    return "intelrun:%s" % uuid.uuid4().hex[:20]


async def tables_ready(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass('intel_runs') IS NOT NULL "
        "   AND to_regclass('intel_regime_states') IS NOT NULL"))


async def record_component(conn, *, run_id, component, started_at,
                           finished_at, status, error=None, summary=None,
                           version=VERSION):
    await conn.execute(
        "INSERT INTO intel_runs (run_id, component, started_at, finished_at,"
        " status, error, duration_ms, summary, version) VALUES "
        " ($1,$2,to_timestamp($3),to_timestamp($4),$5,$6,$7,$8::jsonb,$9) "
        "ON CONFLICT (run_id, component) DO UPDATE SET "
        " finished_at=EXCLUDED.finished_at, status=EXCLUDED.status, "
        " error=EXCLUDED.error, duration_ms=EXCLUDED.duration_ms, "
        " summary=EXCLUDED.summary",
        run_id, component, float(started_at), float(finished_at), status,
        None if error is None else str(error)[:500],
        int(round((float(finished_at) - float(started_at)) * 1000)),
        _j(summary or {}), version)


async def save_snapshot(conn, *, run_id, component, payload, now,
                        book="NONE", version=VERSION):
    await conn.execute(
        "INSERT INTO intel_snapshots (run_id, component, book, computed_at, "
        " payload, version) VALUES ($1,$2,$3,to_timestamp($4),$5::jsonb,$6) "
        "ON CONFLICT (run_id, component, book) DO UPDATE SET "
        " payload=EXCLUDED.payload, computed_at=EXCLUDED.computed_at",
        run_id, component, book, float(now), _j(payload), version)


SEGMENT_SQL = (
    "INSERT INTO intel_calibration_segments (run_id, computed_at, "
    " source, segment_kind, segment_value, n, brier, brier_ci_low, "
    " brier_ci_high, log_loss, log_loss_ci_low, log_loss_ci_high, "
    " mean_probability, observed_frequency, frequency_ci_low, "
    " frequency_ci_high, reliability, unmeasured) VALUES "
    " ($1,to_timestamp($2),$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,"
    "  $15,$16,$17::jsonb,$18::jsonb) "
    "ON CONFLICT (run_id, source, segment_kind, segment_value) DO NOTHING")


async def save_calibration_segments(conn, *, run_id, now, segments):
    args = [(run_id, float(now), s["source"], s["segment_kind"],
         str(s["segment_value"])[:200], int(s["n"]), s.get("brier"),
         s.get("brier_ci_low"), s.get("brier_ci_high"), s.get("log_loss"),
         s.get("log_loss_ci_low"), s.get("log_loss_ci_high"),
         s.get("mean_probability"), s.get("observed_frequency"),
         s.get("frequency_ci_low"), s.get("frequency_ci_high"),
         None if s.get("reliability") is None else _j(s["reliability"]),
         _j(s.get("unmeasured") or {})) for s in segments]
    if args:
        await conn.executemany(SEGMENT_SQL, args)


async def overlays(conn) -> list:
    rows = await conn.fetch(
        "SELECT overlay_id, source, method, params, params_sha256, fit_n, "
        "       extract(epoch FROM fit_window_end)::float8 AS fit_window_end,"
        "       extract(epoch FROM frozen_at)::float8 AS frozen_at, status, "
        "       oos_n, oos_result, "
        "       extract(epoch FROM evaluated_at)::float8 AS evaluated_at, "
        "       production_applied "
        "  FROM intel_calibration_overlays ORDER BY frozen_at")
    return [dict(r) for r in rows]


async def apply_overlay_actions(conn, actions, *, now, protocol, method):
    for a in actions:
        if a["op"] == "FIT":
            oid = "intelovl:%s" % uuid.uuid4().hex[:20]
            await conn.execute(
                "INSERT INTO intel_calibration_overlays (overlay_id, source, "
                " method, params, params_sha256, fit_n, fit_window_end, "
                " frozen_at, protocol) VALUES ($1,$2,$3,$4::jsonb,$5,$6,"
                " to_timestamp($7),to_timestamp($8),$9::jsonb)",
                oid, a["source"], method, _j(a["params"]),
                C.sha(a["params"]), int(a["fit_n"]),
                float(min(a["fit_window_end"], now)), float(now),
                _j(protocol))
            a["overlay_id"] = oid
        elif a["op"] == "EVALUATE":
            r = a["result"]
            await conn.execute(
                "UPDATE intel_calibration_overlays SET status=$2, oos_n=$3, "
                " oos_result=$4::jsonb, evaluated_at=to_timestamp($5) "
                " WHERE overlay_id=$1 AND status='NOT_VALIDATED'",
                a["overlay_id"], r["status"], int(r["oos_n"]), _j(r),
                float(now))


ATTRIBUTION_SQL = (
    "INSERT INTO intel_attribution (book, subject_id, decision_id, "
    " group_id, run_id, computed_at, strategy, us_market_slug, "
    " holding_side, p_decision, decision_price, fill_vwap, entry_qty,"
    " model_edge_pc, executable_edge_pc, slippage_pc, fees_usd, "
    " model_edge_usd, executable_edge_usd, slippage_usd, "
    " execution_edge_usd, management_usd, settlement_usd, "
    " outcome_variance_usd, realized_pnl_usd, cash_pnl_usd, "
    " reconciles, settlement_class, unmeasured, detail) VALUES "
    " ($1,$2,$3,$4,$5,to_timestamp($6),$7,$8,$9,$10,$11,$12,$13,$14,"
    "  $15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,"
    "  $29::jsonb,$30::jsonb) "
    "ON CONFLICT (book, subject_id) DO UPDATE SET "
    " decision_id=EXCLUDED.decision_id, group_id=EXCLUDED.group_id, "
    " run_id=EXCLUDED.run_id, computed_at=EXCLUDED.computed_at, "
    " strategy=EXCLUDED.strategy, "
    " us_market_slug=EXCLUDED.us_market_slug, "
    " holding_side=EXCLUDED.holding_side, "
    " p_decision=EXCLUDED.p_decision, "
    " decision_price=EXCLUDED.decision_price, "
    " fill_vwap=EXCLUDED.fill_vwap, entry_qty=EXCLUDED.entry_qty, "
    " model_edge_pc=EXCLUDED.model_edge_pc, "
    " executable_edge_pc=EXCLUDED.executable_edge_pc, "
    " slippage_pc=EXCLUDED.slippage_pc, fees_usd=EXCLUDED.fees_usd, "
    " model_edge_usd=EXCLUDED.model_edge_usd, "
    " executable_edge_usd=EXCLUDED.executable_edge_usd, "
    " slippage_usd=EXCLUDED.slippage_usd, "
    " execution_edge_usd=EXCLUDED.execution_edge_usd, "
    " management_usd=EXCLUDED.management_usd, "
    " settlement_usd=EXCLUDED.settlement_usd, "
    " outcome_variance_usd=EXCLUDED.outcome_variance_usd, "
    " realized_pnl_usd=EXCLUDED.realized_pnl_usd, "
    " cash_pnl_usd=EXCLUDED.cash_pnl_usd, "
    " reconciles=EXCLUDED.reconciles, "
    " settlement_class=EXCLUDED.settlement_class, "
    " unmeasured=EXCLUDED.unmeasured, detail=EXCLUDED.detail")


async def upsert_attribution(conn, *, run_id, now, rows):
    args = [(
        r["book"], r["subject_id"], r.get("decision_id"),
        r.get("group_id"), run_id, float(now), r.get("strategy"),
        r.get("us_market_slug"), r.get("holding_side"),
        r.get("p_decision"), r.get("decision_price"), r.get("fill_vwap"),
        r.get("entry_qty"), r.get("model_edge_pc"),
        r.get("executable_edge_pc"), r.get("slippage_pc"),
        r.get("fees_usd"), r.get("model_edge_usd"),
        r.get("executable_edge_usd"), r.get("slippage_usd"),
        r.get("execution_edge_usd"), r.get("management_usd"),
        r.get("settlement_usd"), r.get("outcome_variance_usd"),
        r.get("realized_pnl_usd"), r.get("cash_pnl_usd"),
        r.get("reconciles"), r.get("settlement_class"),
        _j(r.get("unmeasured") or {}),
        _j({k: r.get(k) for k in (
            "p_basis", "decision_price_basis", "fee_pc", "payoff_basis",
            "payoff_per_contract", "settlement_outcome",
            "management_basis", "management_actions", "sell_fills",
            "hedge_legs", "identity_claimed", "fill_price_convention",
            "decided_at", "version")})) for r in rows]
    if args:
        await conn.executemany(ATTRIBUTION_SQL, args)


SIZING_SQL = (
    "INSERT INTO intel_sizing (decision_id, run_id, computed_at, "
    " strategy, us_market_slug, paper_qty, paper_usd, actual_qty, "
    " actual_usd, shadow_usd, shadow_qty, binding_constraint, "
    " factors, unmeasured) VALUES ($1,$2,to_timestamp($3),$4,$5,$6,"
    " $7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb) "
    "ON CONFLICT (decision_id) DO UPDATE SET run_id=EXCLUDED.run_id,"
    " computed_at=EXCLUDED.computed_at, paper_qty=EXCLUDED.paper_qty,"
    " paper_usd=EXCLUDED.paper_usd, actual_qty=EXCLUDED.actual_qty, "
    " actual_usd=EXCLUDED.actual_usd, shadow_usd=EXCLUDED.shadow_usd,"
    " shadow_qty=EXCLUDED.shadow_qty, "
    " binding_constraint=EXCLUDED.binding_constraint, "
    " factors=EXCLUDED.factors, unmeasured=EXCLUDED.unmeasured")


async def upsert_sizing(conn, *, run_id, now, rows):
    args = [(
        r["decision_id"], run_id, float(now), r.get("strategy"),
        r.get("us_market_slug"), r.get("paper_qty"), r.get("paper_usd"),
        r.get("actual_qty"), r.get("actual_usd"), r.get("shadow_usd"),
        r.get("shadow_qty"), r.get("binding_constraint"),
        _j(r.get("factors") or {}), _j(r.get("unmeasured") or {})) for r in rows]
    if args:
        await conn.executemany(SIZING_SQL, args)


ALLOCATION_SQL = (
    "INSERT INTO intel_allocations (run_id, candidate_id, "
    " computed_at, rank, candidate_kind, decision_id, group_id, "
    " us_market_slug, score, net_ev_per_dollar, shadow_weight, "
    " shadow_usd, binding_constraint, reasons, inputs, unmeasured) "
    "VALUES ($1,$2,to_timestamp($3),$4,$5,$6,$7,$8,$9,$10,$11,$12,"
    " $13,$14::jsonb,$15::jsonb,$16::jsonb) "
    "ON CONFLICT (run_id, candidate_id) DO NOTHING")


async def save_allocations(conn, *, run_id, now, ranked):
    args = [(
        run_id, c["candidate_id"], float(now), int(c["rank"]),
        c["candidate_kind"], c.get("decision_id"), c.get("group_id"),
        c.get("us_market_slug"), c.get("score"), c.get("ev"),
        float(c.get("shadow_weight") or 0.0),
        float(c.get("shadow_usd") or 0.0), c.get("binding_constraint"),
        _j(c.get("reasons") or []),
        _j({k: c.get(k) for k in (
            "game", "sport", "capacity_usd", "calibration_uncertainty",
            "liquidity_cap_usd", "same_game_open", "same_team_open",
            "opportunity_cost_per_dollar")}),
        _j(c.get("unmeasured") or {})) for c in ranked]
    if args:
        await conn.executemany(ALLOCATION_SQL, args)


async def save_regime(conn, *, run_id, now, regime):
    await conn.execute(
        "INSERT INTO intel_regime_states (run_id, computed_at, "
        " recommendation, reasons, signals) VALUES ($1,to_timestamp($2),$3,"
        " $4::jsonb,$5::jsonb) ON CONFLICT (run_id) DO NOTHING",
        run_id, float(now), regime["recommendation"],
        _j(regime["reasons"]), _j(regime["signals"]))


async def prune(conn, *, now, days=RETENTION_DAYS):
    cut = float(now) - days * 86400.0
    for sql in (
            "DELETE FROM intel_snapshots WHERE computed_at < to_timestamp($1)",
            "DELETE FROM intel_calibration_segments "
            " WHERE computed_at < to_timestamp($1)",
            "DELETE FROM intel_allocations "
            " WHERE computed_at < to_timestamp($1)",
            "DELETE FROM intel_audrey_risk_checks "
            " WHERE computed_at < to_timestamp($1)",
            "DELETE FROM intel_regime_states "
            " WHERE computed_at < to_timestamp($1)",
            "DELETE FROM intel_runs WHERE started_at < to_timestamp($1)"):
        await conn.execute(sql, cut)
