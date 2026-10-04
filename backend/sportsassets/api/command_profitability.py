"""THE PROFITABILITY READS: /api/command/profitability/* (GET only, COMMAND
auth via agents_core.require_read -> api.app.require_command).

EVERY RESPONSE IS RESEARCH WITH NO AUTHORITY and says so:
    {"label": "RESEARCH", "authority": "SHADOW_NO_AUTHORITY",
     "status": "OK" | "EMPTY" | "UNAVAILABLE", "why": <reason or null>,
     "computed_at": <epoch or null>, "data": ..., "disclosure": "..."}
EMPTY names why (no run yet, migration 216 absent, unknown position);
UNAVAILABLE names the failed read. A failed read is never shown as zeros.
PAPER, ACTUAL and COUNTERFACTUAL are separate keys, never summed.

PRODUCTION CONFIDENCE IS INVESTMENT-ONLY (migration 227). `data.PAPER` /
`data.ACTUAL` of north-star and forecast are the INVESTMENT sleeve's rows;
every sleeve is in `by_sleeve` (TRAINING / BENCHMARK / UNCLASSIFIED labelled
research), every strategy in `by_strategy`; rows written before 227 pooled
every strategy and are shown under `legacy_book_wide`, never as INVESTMENT.
Every row carries book, sleeve, strategy and policy_versions. The capacity
snapshot's top level is the PRODUCTION capacity (INVESTMENT, executable
freshness); its `research` key is the 300 s, every-strategy aggregate.

ROUTES (the logic is sportsassets/profitability/*; these read pos_* only):
  GET /api/command/profitability                    latest run per component
  GET /api/command/profitability/north-star         the five metrics per book
                                                    and sleeve
  GET /api/command/profitability/capital            portfolio capital per book
                                                    + positions (?book=&limit=)
  GET /api/command/profitability/capacity           aggregate + candidates
                                                    (?limit=)
  GET /api/command/profitability/forecast           latest forecast per book,
                                                    scores, validation
  GET /api/command/profitability/warehouse/{key}    one position's lineage and
                                                    economics, every revision
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Query

from .agents_core import _pool, require_read

router = APIRouter()
BASE = "/api/command/profitability"
JSON_COLS = ("payload", "summary", "unmeasured", "detail", "stages", "gaps",
             "edge_at_size", "quantiles", "validation")


def _env(status, why=None, **kw) -> dict:
    from ..profitability import common as C
    return C.envelope(status, why, **kw)


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
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif k in JSON_COLS:
            out[k] = _j(v)
        elif v is not None and type(v).__name__ == "Decimal":
            out[k] = float(v)
        else:
            out[k] = v
    return out


async def _ready(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass('pos_runs') IS NOT NULL "
        "   AND to_regclass('pos_economics_latest') IS NOT NULL"))


async def _read(fn):
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            if not await _ready(conn):
                return _env("EMPTY", "MIGRATION_216_NOT_APPLIED", data=None)
            return await fn(conn)
    except Exception as exc:                                    # noqa: BLE001
        return _env("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                               str(exc)[:160]), data=None)


async def _snapshot(conn, component, book):
    r = await conn.fetchrow(
        "SELECT run_id, payload, extract(epoch FROM computed_at)::float8 "
        "       AS computed_at FROM pos_snapshots "
        " WHERE component = $1 AND book = $2 "
        " ORDER BY computed_at DESC LIMIT 1", component, book)
    return None if r is None else _row(r)


async def _last_ok(conn, component):
    return await conn.fetchval(
        "SELECT extract(epoch FROM finished_at)::float8 FROM pos_runs "
        " WHERE component = $1 AND status = 'OK' "
        " ORDER BY finished_at DESC LIMIT 1", component)


@router.get(BASE, dependencies=[Depends(require_read)])
async def profitability_index() -> dict:
    async def fn(conn):
        rows = await conn.fetch(
            "SELECT DISTINCT ON (component) run_id, component, status, "
            "       error, duration_ms, summary, version, "
            "       extract(epoch FROM started_at)::float8 AS started_at "
            "  FROM pos_runs ORDER BY component, started_at DESC")
        data = {r["component"]: _row(r) for r in rows}
        return _env("OK" if data else "EMPTY",
                    None if data else "NO_RUN_YET",
                    computed_at=max((d["started_at"] for d in data.values()),
                                    default=None),
                    data=data or None, routes=[BASE + s for s in (
                        "/north-star", "/capital", "/capacity", "/forecast",
                        "/warehouse/{position_key}")])
    return await _read(fn)


SLEEVES = ("INVESTMENT", "TRAINING", "BENCHMARK", "UNCLASSIFIED")
SCOPE_RULE = ("data.{PAPER,ACTUAL} is PRODUCTION CONFIDENCE: the INVESTMENT "
              "sleeve only (migration 227). TRAINING, BENCHMARK and "
              "UNCLASSIFIED are in by_sleeve, labelled research, and are "
              "never pooled into it; by_strategy splits every sleeve by "
              "strategy. Pre-227 rows pooled every strategy and are listed "
              "under legacy_book_wide, never as INVESTMENT.")


def _metric_row(r, now) -> dict:
    d = _row(r)
    det = d.pop("detail") or {}
    d["trend"] = det.get("trend")
    d["unit"] = det.get("unit")
    d["ci_level"] = det.get("ci_level")
    d["ci_why"] = det.get("ci_why")
    d["period"] = det.get("period")
    d["metric_detail"] = det.get("detail")
    d["policy_version"] = det.get("policy_version")
    d["policy_version_why"] = det.get("policy_version_why")
    d["sleeve_role"] = det.get("sleeve_role")
    if d.get("sleeve") is None:
        d["scope"] = "BOOK_WIDE_PRE_227"
        d["confidence_scope"] = "RESEARCH_NOT_PRODUCTION_CONFIDENCE"
    d["freshness"] = {
        "data_as_of": d["data_as_of"],
        "data_age_s": (None if d["data_as_of"] is None
                       else round(now - d["data_as_of"], 1)),
        "observation_written_at": d["computed_at"],
        "why": None if d["data_as_of"] is not None
        else "NO_SOURCE_EVENT_FOR_THIS_SCOPE"}
    return d


@router.get(BASE + "/north-star", dependencies=[Depends(require_read)])
async def profitability_north_star() -> dict:
    async def fn(conn):
        rows = await conn.fetch(
            "SELECT DISTINCT ON (book, sleeve, strategy, metric) book, "
            "       sleeve, strategy, policy_versions, classifier_version, "
            "       confidence_scope, metric, value, sample_n,"
            "       ci_low, ci_high, status, why, detail, run_id, "
            "       extract(epoch FROM period_start)::float8 AS period_start,"
            "       extract(epoch FROM period_end)::float8 AS period_end, "
            "       extract(epoch FROM data_as_of)::float8 AS data_as_of, "
            "       extract(epoch FROM computed_at)::float8 AS computed_at "
            "  FROM pos_metric_observations "
            " ORDER BY book, sleeve, strategy, metric, computed_at DESC")
        if not rows:
            return _env("EMPTY", "NO_RUN_YET", data=None)
        now = time.time()
        data = {"PAPER": {}, "ACTUAL": {}}
        by_sleeve = {b: {s: {} for s in SLEEVES} for b in data}
        by_strategy = {b: {} for b in data}
        legacy = {b: {} for b in data}
        for r in rows:
            d = _metric_row(r, now)
            b, s, st = r["book"], r["sleeve"], r["strategy"]
            if s is None:
                legacy[b][r["metric"]] = d
            elif st in (None, "ALL"):
                by_sleeve[b][s][r["metric"]] = d
                if s == "INVESTMENT":
                    data[b][r["metric"]] = d
            else:
                by_strategy[b].setdefault(st, {})[r["metric"]] = d
        last = await _last_ok(conn, "NORTH_STAR")
        return _env("OK", None, computed_at=last, data=data,
                    by_sleeve=by_sleeve, by_strategy=by_strategy,
                    legacy_book_wide=legacy,
                    production_confidence_scope={
                        "sleeve": "INVESTMENT", "strategy": "ALL",
                        "rule": SCOPE_RULE},
                    summed_across_books=False, summed_across_sleeves=False,
                    last_confirmed_run_at=last)
    return await _read(fn)


@router.get(BASE + "/capital", dependencies=[Depends(require_read)])
async def profitability_capital(
        book: str = Query(default="", pattern="^(|PAPER|ACTUAL|COUNTERFACTUAL)$"),
        limit: int = Query(default=100, ge=1, le=1000)) -> dict:
    async def fn(conn):
        snaps = {b: await _snapshot(conn, "CAPITAL", b)
                 for b in ("PAPER", "ACTUAL", "COUNTERFACTUAL")}
        rows = [_row(r) for r in await conn.fetch(
            "SELECT book, position_key, revision, counterfactual_kind, "
            "       basis_book, basis_position_key, lineage_id, venue, "
            "       group_id, us_market_slug, holding_side, strategy, state, "
            "       capital_committed_usd, open_cost_basis_usd, "
            "       capital_hours, time_committed_h, net_profit_usd, "
            "       expected_net_profit_usd, expected_capital_hours, "
            "       realized_profit_per_capital_hour, "
            "       expected_profit_per_capital_hour, release_basis, "
            "       unmeasured, "
            "       extract(epoch FROM opened_at)::float8 AS opened_at, "
            "       extract(epoch FROM last_event_at)::float8 "
            "       AS last_event_at, "
            "       extract(epoch FROM released_at)::float8 AS released_at, "
            "       extract(epoch FROM expected_release_at)::float8 "
            "       AS expected_release_at "
            "  FROM pos_economics_latest WHERE ($1 = '' OR book = $1) "
            " ORDER BY (state = 'OPEN') DESC, last_event_at DESC NULLS LAST "
            " LIMIT $2", book, int(limit))]
        now = time.time()
        for r in rows:
            r["REALIZED_PROFIT_PER_CAPITAL_HOUR"] = r[
                "realized_profit_per_capital_hour"]
            r["EXPECTED_PROFIT_PER_CAPITAL_HOUR"] = r[
                "expected_profit_per_capital_hour"]
            if r["state"] == "OPEN" and r.get("last_event_at") is not None:
                r["capital_hours_to_date"] = round(
                    (r["capital_hours"] or 0.0)
                    + (r["open_cost_basis_usd"] or 0.0)
                    * max(0.0, now - r["last_event_at"]) / 3600.0, 6)
            else:
                r["capital_hours_to_date"] = r["capital_hours"]
        ok = any(snaps.values())
        comp = max((s["computed_at"] for s in snaps.values() if s),
                   default=None)
        return _env("OK" if ok else "EMPTY", None if ok else "NO_RUN_YET",
                    computed_at=comp,
                    data={b: (None if s is None else s["payload"])
                          for b, s in snaps.items()},
                    positions=rows, summed_across_books=False)
    return await _read(fn)


@router.get(BASE + "/capacity", dependencies=[Depends(require_read)])
async def profitability_capacity(
        limit: int = Query(default=100, ge=1, le=1000)) -> dict:
    async def fn(conn):
        snap = await _snapshot(conn, "CAPACITY", "NONE")
        rows = [_row(r) for r in await conn.fetch(
            "SELECT capacity_id, candidate_id, us_market_slug, holding_side, "
            "       strategy, status, why, probability, best_price, "
            "       book_obs_id, book_age_s, visible_depth_usd, "
            "       visible_depth_contracts, max_executable_contracts, "
            "       executable_capacity_usd, capacity_ceiling_usd, "
            "       capacity_ceiling_depth_bound, "
            "       theoretical_opportunity_dollars, "
            "       executable_opportunity_dollars, expected_price_impact, "
            "       edge_decay_per_1000_usd, exit_liquidity_usd, "
            "       exit_unabsorbed_contracts, edge_at_size, unmeasured, "
            "       fee_basis, "
            "       extract(epoch FROM decided_at)::float8 AS decided_at "
            "  FROM pos_capacity_latest ORDER BY decided_at DESC NULLS LAST "
            " LIMIT $1", int(limit))]
        for r in rows:
            r["THEORETICAL_OPPORTUNITY_DOLLARS"] = r[
                "theoretical_opportunity_dollars"]
            r["EXECUTABLE_OPPORTUNITY_DOLLARS"] = r[
                "executable_opportunity_dollars"]
            r["EXPECTED_EDGE_AT_SIZE"] = r["edge_at_size"]
            r["CAPACITY_CEILING_USD"] = r["capacity_ceiling_usd"]
        if snap is None:
            return _env("EMPTY", "NO_RUN_YET", data=None, candidates=rows)
        return _env("OK", None, computed_at=snap["computed_at"],
                    data=snap["payload"], candidates=rows)
    return await _read(fn)


@router.get(BASE + "/forecast", dependencies=[Depends(require_read)])
async def profitability_forecast(
        history: int = Query(default=30, ge=0, le=500)) -> dict:
    async def fn(conn):
        latest = {}
        by_sleeve = {}
        legacy = {}
        for b in ("PAPER", "ACTUAL"):
            by_sleeve[b] = {}
            for s in SLEEVES:
                r = await conn.fetchrow(
                    "SELECT * FROM pos_forecasts WHERE book = $1 "
                    "   AND sleeve = $2 AND strategy = 'ALL' "
                    " ORDER BY issued_at DESC LIMIT 1", b, s)
                by_sleeve[b][s] = None if r is None else _row(r)
            # THE PRODUCTION-CONFIDENCE FORECAST: the INVESTMENT sleeve's
            latest[b] = by_sleeve[b]["INVESTMENT"]
            r = await conn.fetchrow(
                "SELECT * FROM pos_forecasts WHERE book = $1 "
                "   AND sleeve IS NULL ORDER BY issued_at DESC LIMIT 1", b)
            legacy[b] = None if r is None else dict(
                _row(r), scope="BOOK_WIDE_PRE_227",
                confidence_scope="RESEARCH_NOT_PRODUCTION_CONFIDENCE")
        scores = [_row(r) for r in await conn.fetch(
            "SELECT * FROM pos_forecast_scores ORDER BY scored_at DESC "
            " LIMIT $1", int(history))] if history else []
        if not any(v for bs in by_sleeve.values() for v in bs.values()):
            return _env("EMPTY", "NO_FORECAST_ISSUED_YET", data=None,
                        scores=scores, legacy_book_wide=legacy)
        comp = max((v["issued_at"] for bs in by_sleeve.values()
                    for v in bs.values() if v), default=None)
        return _env("OK", None, computed_at=comp, data=latest,
                    by_sleeve=by_sleeve, legacy_book_wide=legacy,
                    scores=scores,
                    production_confidence_scope={
                        "sleeve": "INVESTMENT", "strategy": "ALL",
                        "rule": SCOPE_RULE},
                    summed_across_books=False, summed_across_sleeves=False,
                    forecast_label=("UNPROVEN until the forecasts' own forward "
                                    "calibration validates them"))
    return await _read(fn)


@router.get(BASE + "/warehouse/{position_key:path}",
            dependencies=[Depends(require_read)])
async def profitability_warehouse(position_key: str) -> dict:
    async def fn(conn):
        lin = [_row(r) for r in await conn.fetch(
            "SELECT * FROM pos_lineage WHERE position_key = $1 "
            " ORDER BY book, revision DESC LIMIT 50", position_key)]
        econ = [_row(r) for r in await conn.fetch(
            "SELECT * FROM pos_position_economics "
            " WHERE position_key = $1 OR basis_position_key = $1 "
            " ORDER BY book, revision DESC LIMIT 100", position_key)]
        if not lin and not econ:
            return _env("EMPTY", "NO_LINEAGE_FOR_POSITION_KEY", data=None)
        by_book: dict = {}
        for e in econ:
            by_book.setdefault(e["book"], []).append(e)
        comp = max([r["built_at"] for r in lin]
                   + [e["computed_at"] for e in econ], default=None)
        return _env("OK", None, computed_at=comp, data={
            "position_key": position_key,
            "lineage": lin[0] if lin else None,
            "lineage_revisions": lin,
            "economics": {b: rows[0] for b, rows in by_book.items()},
            "economics_revisions": by_book}, summed_across_books=False)
    return await _read(fn)
