"""THE ONLY WRITER OF THE LOST OPPORTUNITY LEDGER: migration 220's lol_*
tables, and nothing else (pinned by tests/test_lost_opportunity_is_research_
only.py, which parses every SQL statement in sportsassets/lost_opportunity
and refuses a write to any other table, and any verb but INSERT).

APPEND-ONLY and IDEMPOTENT: one ledger row per (decision_ref,
classifier_version) and one score row per (candidate, content) -- ON
CONFLICT DO NOTHING; the database refuses UPDATE / DELETE / TRUNCATE.
"""
from __future__ import annotations

import json
import uuid

from ..profitability import common as C

VERSION = "LOL_STORE_V1"


def _j(v) -> str:
    return json.dumps(v, default=str)


def new_run_id() -> str:
    return "lolrun:%s" % uuid.uuid4().hex[:20]


def _id(prefix) -> str:
    return "%s:%s" % (prefix, uuid.uuid4().hex[:24])


async def tables_ready(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass('lol_ledger') IS NOT NULL "
        "   AND to_regclass('lol_runs') IS NOT NULL "
        "   AND to_regclass('lol_opportunity_scores_latest') IS NOT NULL "
        "   AND to_regclass('pos_capacity_latest') IS NOT NULL"))


async def record_component(conn, *, run_id, component, started_at,
                           finished_at, status, error=None, summary=None,
                           pos_run_id=None, version=VERSION):
    await conn.execute(
        "INSERT INTO lol_runs (run_id, component, pos_run_id, started_at, "
        " finished_at, status, error, duration_ms, summary, version) VALUES "
        " ($1,$2,$3,to_timestamp($4),to_timestamp($5),$6,$7,$8,$9::jsonb,$10) "
        "ON CONFLICT (run_id, component) DO NOTHING",
        run_id, component, pos_run_id, float(started_at), float(finished_at),
        status, None if error is None else str(error)[:500],
        int(round((float(finished_at) - float(started_at)) * 1000)),
        _j(summary or {}), version)


LEDGER_COLS = (
    "ledger_id", "decision_ref", "source", "classifier_version", "run_id",
    "classified_at", "decided_at", "strategy", "league", "league_basis",
    "us_market_slug", "holding_side", "classification", "reason", "defect",
    "refusal", "refusals", "refusal_category", "attribution",
    "attribution_code", "decision_evidence_ids",
    "decision_time_net_ev_usd", "decision_time_net_ev_basis",
    "decision_time_executable_price", "decision_time_qty",
    "decision_time_fees_usd", "decision_time_cost_usd",
    "policy_min_gross_edge", "policy_min_net_ev_usd",
    "settlement_evidence_id", "settlement_basis", "settled_at",
    "settlement_outcome", "payout_per_contract", "hypothetical_pnl_usd",
    "hypothetical_pnl_why", "detail", "content_sha256")
_TS = ("classified_at", "decided_at", "settled_at")
_ARR = ("refusals", "decision_evidence_ids")


def _ph(cols):
    out = []
    for i, c in enumerate(cols, start=1):
        if c in _TS:
            out.append("to_timestamp($%d)" % i)
        elif c in _ARR:
            out.append("$%d::text[]" % i)
        elif c in ("detail", "unmeasured"):
            out.append("$%d::jsonb" % i)
        else:
            out.append("$%d" % i)
    return ", ".join(out)


LEDGER_SQL = ("INSERT INTO lol_ledger (%s) VALUES (%s) "
              "ON CONFLICT (decision_ref, classifier_version) DO NOTHING "
              "RETURNING ledger_id" % (", ".join(LEDGER_COLS),
                                       _ph(LEDGER_COLS)))


async def save_ledger(conn, *, run_id, now, rows) -> int:
    """Insert each classified row once. Returns how many were new."""
    wrote = 0
    for r in rows:
        r = dict(r, ledger_id=_id("lol"), run_id=run_id,
                 classified_at=float(now))
        r["content_sha256"] = C.sha({k: r.get(k) for k in LEDGER_COLS
                                     if k not in ("ledger_id", "run_id",
                                                  "classified_at",
                                                  "content_sha256")})
        args = []
        for c in LEDGER_COLS:
            v = r.get(c)
            if c == "detail":
                v = _j(v or {})
            elif c in _ARR:
                v = [str(x) for x in (v or [])]
            elif c in _TS:
                v = None if v is None else float(v)
            args.append(v)
        if await conn.fetchval(LEDGER_SQL, *args) is not None:
            wrote += 1
    return wrote


SCORE_COLS = (
    "score_id", "candidate_id", "run_id", "computed_at", "decided_at",
    "strategy", "verdict", "league", "us_market_slug", "holding_side",
    "capacity_id", "expected_net_executable_ev_usd", "fill_probability",
    "fill_probability_basis", "capacity_factor", "capacity_factor_basis",
    "executable_capacity_usd", "expected_hold_h", "expected_hold_basis",
    "capital_hours", "opportunity_score", "components", "score_unit",
    "status", "why", "unmeasured", "detail", "content_sha256", "version")
_STS = ("computed_at", "decided_at")


def _sph(cols):
    out = []
    for i, c in enumerate(cols, start=1):
        if c in _STS:
            out.append("to_timestamp($%d)" % i)
        elif c in ("detail", "unmeasured", "components"):
            out.append("$%d::jsonb" % i)
        else:
            out.append("$%d" % i)
    return ", ".join(out)


SCORE_SQL = ("INSERT INTO lol_opportunity_scores (%s) VALUES (%s) "
             "ON CONFLICT (candidate_id, content_sha256) DO NOTHING "
             "RETURNING score_id" % (", ".join(SCORE_COLS), _sph(SCORE_COLS)))


async def save_scores(conn, *, run_id, now, rows, version) -> int:
    wrote = 0
    for r in rows:
        r = dict(r, score_id=_id("lolscore"), run_id=run_id,
                 computed_at=float(now), version=version)
        r["content_sha256"] = C.sha({k: r.get(k) for k in SCORE_COLS
                                     if k not in ("score_id", "run_id",
                                                  "computed_at",
                                                  "content_sha256")})
        args = []
        for c in SCORE_COLS:
            v = r.get(c)
            if c in ("detail", "unmeasured", "components"):
                v = _j(v or {})
            elif c in _STS:
                v = None if v is None else float(v)
            args.append(v)
        if await conn.fetchval(SCORE_SQL, *args) is not None:
            wrote += 1
    return wrote


HORIZON_COLS = (
    "forecast_id", "book", "horizon", "horizon_days", "run_id", "issued_at",
    "issued_day", "horizon_start", "horizon_end", "method", "seed",
    "sample_days", "sample_positions", "expected_opportunities",
    "expected_qualified_opportunities", "expected_turnover_usd",
    "deployable_capital_usd", "expected_capital_hours", "expected_pnl_usd",
    "p10_pnl_usd", "p50_pnl_usd", "p90_pnl_usd", "prob_positive",
    "expected_max_drawdown_usd", "capacity_utilization",
    "expected_capacity_usd", "trailing_30d_committed_usd",
    "trailing_30d_capital_turnover", "quantiles", "status", "why",
    "validation",
    "unmeasured", "basis", "inputs_sha256", "version",
    # the scope (migration 227): every new horizon forecast names it
    "sleeve", "strategy", "policy_versions", "classifier_version",
    "confidence_scope")
_HTS = ("issued_at", "horizon_start", "horizon_end")
_HJ = ("quantiles", "validation", "unmeasured", "basis")


def _hph():
    out = []
    for i, c in enumerate(HORIZON_COLS, start=1):
        if c in _HTS:
            out.append("to_timestamp($%d)" % i)
        elif c == "issued_day":
            out.append("(to_timestamp($%d) AT TIME ZONE 'UTC')::date" % i)
        elif c in _HJ:
            out.append("$%d::jsonb" % i)
        elif c == "policy_versions":
            out.append("$%d::text[]" % i)
        else:
            out.append("$%d" % i)
    return ", ".join(out)


HORIZON_SQL = ("INSERT INTO lol_horizon_forecasts (%s) VALUES (%s) "
               "ON CONFLICT (book, sleeve, strategy, horizon, issued_day) "
               "DO NOTHING RETURNING forecast_id"
               % (", ".join(HORIZON_COLS), _hph()))


async def save_horizon(conn, *, run_id, fc) -> str | None:
    """One horizon forecast per book, sleeve, strategy and horizon per UTC
    day (the first one issued stands)."""
    r = dict(fc, forecast_id=_id("lolhfc"), run_id=run_id,
             issued_day=fc["issued_at"])
    r["strategy"] = r.get("strategy") or C.ALL_STRATEGIES
    r["policy_versions"] = list(r.get("policy_versions") or [])
    args = []
    for c in HORIZON_COLS:
        v = r.get(c)
        if c in _HJ:
            v = None if (c == "quantiles" and v is None) else _j(v or {})
        elif c in _HTS or c == "issued_day":
            v = float(v)
        args.append(v)
    return await conn.fetchval(HORIZON_SQL, *args)


async def save_horizon_score(conn, sc) -> None:
    """A score copies its forecast's scope (NULL for a pre-227 one)."""
    await conn.execute(
        "INSERT INTO lol_horizon_forecast_scores (forecast_id, book, "
        " horizon, scored_at, realized_pnl_usd, realized_positions, pit, "
        " inside_p10_p90, realized_positive, brier_positive, "
        " abs_error_vs_p50_usd, version, sleeve, strategy, confidence_scope)"
        " VALUES ($1,$2,$3,to_timestamp($4),$5,$6,$7,$8,$9,$10,$11,$12,$13,"
        " $14,$15) ON CONFLICT (forecast_id) DO NOTHING",
        sc["forecast_id"], sc["book"], sc["horizon"], float(sc["scored_at"]),
        sc["realized_pnl_usd"], sc["realized_positions"], sc["pit"],
        sc["inside_p10_p90"], sc["realized_positive"], sc["brier_positive"],
        sc["abs_error_vs_p50_usd"], sc["version"], sc.get("sleeve"),
        sc.get("strategy"), sc.get("confidence_scope"))
