"""THE ONLY WRITER OF THE PROFITABILITY LAYER: migration 216's pos_* tables,
and nothing else (pinned by tests/test_profitability_is_research_only.py,
which parses every SQL statement in sportsassets/profitability and refuses a
write to any other table, and any update or delete statement at all).

APPEND-ONLY. Every statement here is an insert; the database refuses
updates and deletes on every pos_* table. A changed fact is a new revision, written
only when its content differs from the latest one (no churn when nothing
changed), so the tables grow with events, not with cycles.
"""
from __future__ import annotations

import json
import uuid

from . import common as C

VERSION = "POS_STORE_V1"


def _j(v) -> str:
    return json.dumps(v, default=str)


def _ts(v):
    return None if v is None else float(v)


def new_run_id() -> str:
    return "posrun:%s" % uuid.uuid4().hex[:20]


def _id(prefix) -> str:
    return "%s:%s" % (prefix, uuid.uuid4().hex[:24])


async def tables_ready(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass('pos_runs') IS NOT NULL "
        "   AND to_regclass('pos_forecast_scores') IS NOT NULL "
        "   AND to_regclass('pos_economics_latest') IS NOT NULL"))


async def record_component(conn, *, run_id, component, started_at,
                           finished_at, status, error=None, summary=None,
                           version=VERSION):
    await conn.execute(
        "INSERT INTO pos_runs (run_id, component, started_at, finished_at, "
        " status, error, duration_ms, summary, version) VALUES "
        " ($1,$2,to_timestamp($3),to_timestamp($4),$5,$6,$7,$8::jsonb,$9) "
        "ON CONFLICT (run_id, component) DO NOTHING",
        run_id, component, float(started_at), float(finished_at), status,
        None if error is None else str(error)[:500],
        int(round((float(finished_at) - float(started_at)) * 1000)),
        _j(summary or {}), version)


LINEAGE_COLS = (
    "venue", "account_id", "group_id", "us_market_slug", "holding_side",
    "strategy") + ("valuation_ids", "decision_ids", "intent_ids", "order_ids",
                   "fill_ids", "book_obs_ids", "settlement_ids",
                   "ledger_seqs", "review_ids", "handoff_ids", "thesis_ids",
                   "assessment_ids", "value_add_ids", "postmortem_keys",
                   "challenge_ids", "audit_finding_ids",
                   "agent_decision_refs", "attribution_refs", "sizing_refs",
                   "allocation_refs", "regime_run_ids", "capacity_ids",
                   "model_versions", "policy_versions", "experiment_ids",
                   "simulator_versions")
_LTYPES = {"valuation_ids": "bigint[]", "book_obs_ids": "bigint[]",
           "ledger_seqs": "bigint[]"}


def _lineage_sql():
    cols = ["lineage_id", "book", "position_key", "run_id", "built_at",
            "opened_at", "closed_at", "state"] + list(LINEAGE_COLS) + [
        "stages", "gaps", "content_sha256", "version"]
    ph = []
    for i, c in enumerate(cols, start=1):
        if c in ("built_at", "opened_at", "closed_at"):
            ph.append("to_timestamp($%d)" % i)
        elif c in ("stages", "gaps"):
            ph.append("$%d::jsonb" % i)
        elif c.endswith("_ids") or c.endswith("_refs") or c.endswith(
                "_keys") or c.endswith("_versions") or c == "ledger_seqs":
            ph.append("$%d::%s" % (i, _LTYPES.get(c, "text[]")))
        else:
            ph.append("$%d" % i)
    n = len(cols)
    return cols, (
        "INSERT INTO pos_lineage (%s, revision) SELECT %s, "
        " coalesce((SELECT max(revision) FROM pos_lineage "
        "            WHERE book = $2 AND position_key = $3), 0) + 1 "
        " WHERE (SELECT content_sha256 FROM pos_lineage "
        "         WHERE book = $2 AND position_key = $3 "
        "         ORDER BY revision DESC LIMIT 1) IS DISTINCT FROM $%d" % (
            ", ".join(cols), ", ".join(ph), n - 1))


LINEAGE_COLUMNS, LINEAGE_SQL = _lineage_sql()


async def save_lineage(conn, *, run_id, now, records) -> int:
    """Insert a new revision for each record whose content changed.
    Returns how many revisions were written."""
    wrote = 0
    for r in records:
        args = []
        for c in LINEAGE_COLUMNS:
            if c == "lineage_id":
                args.append(_id("poslin"))
            elif c == "run_id":
                args.append(run_id)
            elif c == "built_at":
                args.append(float(now))
            elif c in ("opened_at", "closed_at"):
                args.append(_ts(r.get(c)))
            elif c in ("stages", "gaps"):
                args.append(_j(r.get(c)))
            else:
                args.append(r.get(c))
        res = await conn.execute(LINEAGE_SQL, *args)
        wrote += int(res.split()[-1]) if res.startswith("INSERT") else 0
    return wrote


ECON_COLS = (
    "econ_id", "book", "position_key", "counterfactual_kind", "basis_book",
    "basis_position_key", "lineage_id", "run_id", "computed_at", "venue",
    "group_id", "us_market_slug", "holding_side", "strategy", "state",
    "opened_at", "first_fill_at", "last_event_at", "released_at",
    "event_start_at", "expected_release_at", "release_basis", "bought_qty",
    "capital_committed_usd", "peak_capital_usd", "open_cost_basis_usd",
    "time_committed_h", "capital_hours", "net_profit_usd",
    "expected_net_profit_usd", "expected_capital_hours",
    "realized_profit_per_capital_hour", "expected_profit_per_capital_hour",
    "predicted_edge_per_dollar", "realized_edge_per_dollar", "probability",
    "probability_basis", "unmeasured", "detail", "content_sha256", "version")
_ETS = {"computed_at", "opened_at", "first_fill_at", "last_event_at",
        "released_at", "event_start_at", "expected_release_at"}


def _econ_sql():
    ph = []
    for i, c in enumerate(ECON_COLS, start=1):
        if c in _ETS:
            ph.append("to_timestamp($%d)" % i)
        elif c in ("unmeasured", "detail"):
            ph.append("$%d::jsonb" % i)
        else:
            ph.append("$%d" % i)
    sha_i = ECON_COLS.index("content_sha256") + 1
    return (
        "INSERT INTO pos_position_economics (%s, revision) SELECT %s, "
        " coalesce((SELECT max(revision) FROM pos_position_economics "
        "            WHERE book = $2 AND position_key = $3), 0) + 1 "
        " WHERE (SELECT content_sha256 FROM pos_position_economics "
        "         WHERE book = $2 AND position_key = $3 "
        "         ORDER BY revision DESC LIMIT 1) IS DISTINCT FROM $%d" % (
            ", ".join(ECON_COLS), ", ".join(ph), sha_i))


ECON_SQL = _econ_sql()
#: the content an economics revision is keyed on (not run ids or times of
#: computation, not the lineage id, which is a pointer)
ECON_CONTENT = tuple(c for c in ECON_COLS if c not in (
    "econ_id", "lineage_id", "run_id", "computed_at", "content_sha256"))


def econ_sha(e: dict) -> str:
    return C.sha({k: e.get(k) for k in ECON_CONTENT})


async def save_economics(conn, *, run_id, now, rows, lineage_ids) -> int:
    wrote = 0
    for e in rows:
        e = dict(e)
        e["content_sha256"] = econ_sha(e)
        e["lineage_id"] = lineage_ids.get((
            e.get("basis_book") or e["book"],
            e.get("basis_position_key") or e["position_key"]))
        args = []
        for c in ECON_COLS:
            if c == "econ_id":
                args.append(_id("posecon"))
            elif c == "run_id":
                args.append(run_id)
            elif c == "computed_at":
                args.append(float(now))
            elif c in _ETS:
                args.append(_ts(e.get(c)))
            elif c in ("unmeasured", "detail"):
                args.append(_j(e.get(c) or {}))
            else:
                args.append(e.get(c))
        res = await conn.execute(ECON_SQL, *args)
        wrote += int(res.split()[-1]) if res.startswith("INSERT") else 0
    return wrote


async def latest_lineage_ids(conn, keys) -> dict:
    rows = await conn.fetch(
        "SELECT book, position_key, lineage_id FROM pos_lineage_latest "
        " WHERE position_key = ANY($1::text[])", sorted(set(keys)))
    return {(r["book"], r["position_key"]): r["lineage_id"] for r in rows}


CAPACITY_SQL = (
    "INSERT INTO pos_capacity (capacity_id, candidate_id, run_id, "
    " computed_at, decided_at, us_market_slug, holding_side, strategy, "
    " book_obs_id, book_observed_at, book_age_s, probability, status, why, "
    " best_price, fee_basis, visible_depth_usd, visible_depth_contracts, "
    " max_executable_contracts, executable_capacity_usd, "
    " capacity_ceiling_usd, capacity_ceiling_depth_bound, "
    " theoretical_opportunity_dollars, executable_opportunity_dollars, "
    " expected_price_impact, edge_decay_per_1000_usd, exit_liquidity_usd, "
    " exit_unabsorbed_contracts, edge_at_size, unmeasured, detail, "
    " content_sha256, version) VALUES ($1,$2,$3,to_timestamp($4),"
    " to_timestamp($5),$6,$7,$8,$9,to_timestamp($10),$11,$12,$13,$14,$15,"
    " $16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29::jsonb,"
    " $30::jsonb,$31::jsonb,$32,$33) "
    "ON CONFLICT (candidate_id, content_sha256) DO NOTHING")


async def save_capacity(conn, *, run_id, now, rows, fee_basis=None) -> int:
    args = []
    for r in rows:
        content = {k: v for k, v in r.items() if k not in ("label",)}
        args.append((
            _id("poscap"), r["candidate_id"], run_id, float(now),
            _ts(r.get("decided_at")), r.get("us_market_slug"),
            r.get("holding_side"), r.get("strategy"),
            None if r.get("book_obs_id") is None else int(r["book_obs_id"]),
            _ts(r.get("book_observed_at")), r.get("book_age_s"),
            r.get("probability"), r["status"], r.get("why"),
            r.get("best_price"), (r.get("detail") or {}).get("fee_basis")
            or fee_basis, r.get("visible_depth_usd"),
            r.get("visible_depth_contracts"),
            r.get("max_executable_contracts"),
            r.get("executable_capacity_usd"), r.get("capacity_ceiling_usd"),
            r.get("capacity_ceiling_depth_bound"),
            r.get("theoretical_opportunity_dollars"),
            r.get("executable_opportunity_dollars"),
            r.get("expected_price_impact"), r.get("edge_decay_per_1000_usd"),
            r.get("exit_liquidity_usd"), r.get("exit_unabsorbed_contracts"),
            None if r.get("edge_at_size") is None else _j(r["edge_at_size"]),
            _j(r.get("unmeasured") or {}), _j(r.get("detail") or {}),
            C.sha(content), r.get("version") or VERSION))
    if args:
        await conn.executemany(CAPACITY_SQL, args)
    return len(args)


METRIC_SQL = (
    "INSERT INTO pos_metric_observations (observation_id, run_id, book, "
    " metric, computed_at, period_start, period_end, value, sample_n, "
    " ci_low, ci_high, status, why, data_as_of, detail, content_sha256, "
    " version) VALUES ($1,$2,$3,$4,to_timestamp($5),to_timestamp($6),"
    " to_timestamp($7),$8,$9,$10,$11,$12,$13,to_timestamp($14),$15::jsonb,"
    " $16,$17)")


def metric_sha(m: dict) -> str:
    return C.sha({k: m.get(k) for k in (
        "book", "metric", "value", "sample_n", "ci_low", "ci_high", "status",
        "why", "data_as_of")})


async def save_metrics(conn, *, run_id, now, metrics, latest_shas,
                       version) -> int:
    """Insert each metric whose content differs from its latest row."""
    args = []
    for m in metrics:
        sh = metric_sha(m)
        if latest_shas.get((m["book"], m["metric"])) == sh:
            continue
        per = m.get("period") or {}
        args.append((
            _id("posmet"), run_id, m["book"], m["metric"], float(now),
            _ts(per.get("start")), _ts(per.get("end")), m.get("value"),
            int(m.get("sample_n") or 0), m.get("ci_low"), m.get("ci_high"),
            m["status"], m.get("why"), _ts(m.get("data_as_of")),
            _j({"period": per, "detail": m.get("detail"),
                "trend": m.get("trend"), "unit": m.get("unit"),
                "ci_why": m.get("ci_why"), "ci_level": m.get("ci_level"),
                "higher_is_better": m.get("higher_is_better")}),
            sh, version))
    if args:
        await conn.executemany(METRIC_SQL, args)
    return len(args)


async def save_snapshot(conn, *, run_id, component, book, payload, now,
                        version) -> bool:
    """Append a snapshot only when its data differs from the latest one."""
    sh = C.sha(payload)
    res = await conn.execute(
        "INSERT INTO pos_snapshots (snapshot_id, run_id, component, book, "
        " computed_at, payload, data_sha256, version) "
        "SELECT $1,$2,$3,$4,to_timestamp($5),$6::jsonb,$7,$8 "
        " WHERE (SELECT data_sha256 FROM pos_snapshots "
        "         WHERE component = $3 AND book = $4 "
        "         ORDER BY computed_at DESC LIMIT 1) IS DISTINCT FROM $7",
        _id("possnap"), run_id, component, book, float(now), _j(payload), sh,
        version)
    return res.endswith(" 1")


FORECAST_SQL = (
    "INSERT INTO pos_forecasts (forecast_id, book, run_id, issued_at, "
    " issued_day, horizon_start, horizon_end, horizon_days, method, seed, "
    " resamples, block_days, sample_days, sample_positions, "
    " expected_pnl_usd, p10_pnl_usd, p50_pnl_usd, p90_pnl_usd, "
    " prob_positive, expected_max_drawdown_usd, p95_max_drawdown_usd, "
    " worst_modelled_drawdown_usd, capital_required_usd, "
    " capital_hours_required, turnover_required_usd, capacity_ceiling_usd, "
    " quantiles, status, why, validation, inputs_sha256, unmeasured, "
    " version) VALUES ($1,$2,$3,to_timestamp($4),"
    " (to_timestamp($4) AT TIME ZONE 'UTC')::date,to_timestamp($5),"
    " to_timestamp($6),$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,"
    " $20,$21,$22,$23,$24,$25,$26::jsonb,$27,$28,$29::jsonb,$30,$31::jsonb,"
    " $32) ON CONFLICT (book, issued_day) DO NOTHING")


async def save_forecast(conn, *, run_id, fc) -> str | None:
    """One forecast per book per UTC day (the first one issued stands)."""
    fid = _id("posfc")
    res = await conn.execute(
        FORECAST_SQL, fid, fc["book"], run_id, float(fc["issued_at"]),
        float(fc["horizon_start"]), float(fc["horizon_end"]),
        int(fc["horizon_days"]), fc["method"], fc.get("seed"),
        fc.get("resamples"), fc.get("block_days"), int(fc["sample_days"]),
        int(fc["sample_positions"]), fc.get("expected_pnl_usd"),
        fc.get("p10_pnl_usd"), fc.get("p50_pnl_usd"), fc.get("p90_pnl_usd"),
        fc.get("prob_positive"), fc.get("expected_max_drawdown_usd"),
        fc.get("p95_max_drawdown_usd"), fc.get("worst_modelled_drawdown_usd"),
        fc.get("capital_required_usd"), fc.get("capital_hours_required"),
        fc.get("turnover_required_usd"), fc.get("capacity_ceiling_usd"),
        None if fc.get("quantiles") is None else _j(fc["quantiles"]),
        fc["status"], fc.get("why"), _j(fc.get("validation") or {}),
        fc["inputs_sha256"], _j(fc.get("unmeasured") or {}), fc["version"])
    return fid if res.endswith(" 1") else None


async def save_score(conn, sc: dict) -> None:
    await conn.execute(
        "INSERT INTO pos_forecast_scores (forecast_id, book, scored_at, "
        " realized_pnl_usd, realized_positions, pit, inside_p10_p90, "
        " realized_positive, brier_positive, abs_error_vs_p50_usd, detail, "
        " version) VALUES ($1,$2,to_timestamp($3),$4,$5,$6,$7,$8,$9,$10,"
        " $11::jsonb,$12) ON CONFLICT (forecast_id) DO NOTHING",
        sc["forecast_id"], sc["book"], float(sc["scored_at"]),
        float(sc["realized_pnl_usd"]), int(sc["realized_positions"]),
        sc.get("pit"), bool(sc["inside_p10_p90"]),
        bool(sc["realized_positive"]), float(sc["brier_positive"]),
        float(sc["abs_error_vs_p50_usd"]), _j(sc.get("detail") or {}),
        sc["version"])
