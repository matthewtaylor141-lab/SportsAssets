"""THE TWIN AND PROFITABILITY-EVIDENCE READS (migration 219). GET only,
COMMAND auth via agents_core.require_read -> api.app.require_command.

EVERY RESPONSE IS RESEARCH and says so:
    {"label": "RESEARCH", "authority": "RESEARCH_NO_AUTHORITY",
     "disclosure": "...", "status": "OK" | "EMPTY" | "UNAVAILABLE",
     "why": <reason or null>, "run_id": ..., "computed_at": <epoch>, ...}
EMPTY names why (no run yet, migration 219 absent); UNAVAILABLE names the
failed read. A failed read is never shown as zeros. These routes read only
twin_* tables (pinned by tests/test_twin_authority.py) and write nothing.

ROUTES
  GET /api/command/twin                       runs per component + the latest
                                              scenario summaries (baseline
                                              PAPER/ACTUAL vs COUNTERFACTUAL
                                              world, paired difference)
  GET /api/command/twin/scenarios/{id}        one frozen scenario: its spec,
                                              latest result per basis book,
                                              its decision traces (?limit=)
  GET /api/command/research/transfer          preregistered cross-sport tests
                                              with their latest forward
                                              classification
  GET /api/command/profitability/scorecards   the latest agent financial
                                              scorecards (?agent=)
  GET /api/command/profitability/evidence-ladder
                                              the evidence level, every
                                              level's criterion and
                                              evidence, profitability
                                              confidence
  GET /api/command/profitability/kill-switches
                                              the latest criteria
                                              evaluations + RECOMMEND_PAUSE
                                              records (never actions)
  GET /api/command/evals                      agent / handoff / macro evals
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Path, Query

from .agents_core import _pool, require_read

router = APIRouter()
BASE = "/api/command"
JSON_COLS = ("payload", "summary", "spec", "baseline", "world", "comparison",
             "counts", "unmeasured", "criteria", "levels", "confidence",
             "evidence", "failures", "detail")


def _env(status, why=None, **kw) -> dict:
    from ..twin import common as C
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
        elif k in JSON_COLS:
            out[k] = _j(v)
        elif v is not None and type(v).__name__ == "Decimal":
            out[k] = float(v)
        elif isinstance(v, list):
            out[k] = list(v)
        else:
            out[k] = v
    return out


async def _ready(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass('twin_snapshots') IS NOT NULL"))


async def _read(fn):
    pool = await _pool()
    try:
        async with pool.acquire() as conn:
            if not await _ready(conn):
                return _env("EMPTY", "MIGRATION_219_NOT_APPLIED", data=None)
            return await fn(conn)
    except Exception as exc:                                    # noqa: BLE001
        return _env("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                               str(exc)[:160]), data=None)


async def _latest(conn, component):
    return await conn.fetchrow(
        "SELECT run_id, payload, extract(epoch FROM computed_at)::float8 "
        "       AS computed_at FROM twin_snapshots WHERE component = $1 "
        " ORDER BY computed_at DESC, snapshot_id DESC LIMIT 1", component)


def _snap(row, extra=None, empty_why="NO_TWIN_RUN_YET"):
    if row is None:
        return _env("EMPTY", empty_why, run_id=None, computed_at=None,
                    data=None, **(extra or {}))
    return _env("OK", None, run_id=row["run_id"],
                computed_at=row["computed_at"], data=_j(row["payload"]),
                **(extra or {}))


@router.get(BASE + "/twin", dependencies=[Depends(require_read)])
async def twin_index() -> dict:
    async def fn(conn):
        runs = [_row(r) for r in await conn.fetch(
            "SELECT DISTINCT ON (component) run_id, component, status, "
            "       error, duration_ms, summary, version, "
            "       extract(epoch FROM started_at)::float8 AS started_at "
            "  FROM twin_runs ORDER BY component, started_at DESC")]
        scen = [_row(r) for r in await conn.fetch(
            "SELECT scenario_id, scenario_key, version, world, spec_sha256, "
            "       engine_version, extract(epoch FROM frozen_at)::float8 "
            "       AS frozen_at, label FROM twin_scenarios "
            " ORDER BY scenario_key, version")]
        snap = await _latest(conn, "TWIN")
        out = _snap(snap, extra={
            "runs": {r["component"]: r for r in runs}, "scenarios": scen,
            "research_only": True, "production_truth": False,
            "summed_across_books": False,
            "routes": [BASE + s for s in (
                "/twin/scenarios/{scenario_id}", "/research/transfer",
                "/profitability/scorecards",
                "/profitability/evidence-ladder",
                "/profitability/kill-switches", "/evals")]})
        return out
    return await _read(fn)


@router.get(BASE + "/twin/scenarios/{scenario_id}",
            dependencies=[Depends(require_read)])
async def twin_scenario(scenario_id: str = Path(..., max_length=80),
                        limit: int = Query(default=200, ge=0, le=2000)
                        ) -> dict:
    async def fn(conn):
        sc = await conn.fetchrow(
            "SELECT scenario_id, scenario_key, version, world, spec, "
            "       spec_sha256, engine_version, label, authority, "
            "       extract(epoch FROM frozen_at)::float8 AS frozen_at "
            "  FROM twin_scenarios WHERE scenario_id = $1", scenario_id)
        if sc is None:
            return _env("EMPTY", "UNKNOWN_SCENARIO", data=None)
        results = [_row(r) for r in await conn.fetch(
            "SELECT DISTINCT ON (basis_book) result_id, run_id, basis_book,"
            "       status, unavailable_reason, input_sha256, output_sha256, "
            "       engine_version, baseline, world, comparison, counts, "
            "       unmeasured, label, research_only, production_truth, "
            "       summed_across_books, "
            "       extract(epoch FROM window_start)::float8 AS window_start,"
            "       extract(epoch FROM window_end)::float8 AS window_end, "
            "       extract(epoch FROM computed_at)::float8 AS computed_at "
            "  FROM twin_scenario_results WHERE scenario_id = $1 "
            " ORDER BY basis_book, computed_at DESC", scenario_id)]
        traces = {}
        for r in results:
            traces[r["basis_book"]] = [_row(t) for t in await conn.fetch(
                "SELECT seq, subject_id, decision_kind, "
                "       extract(epoch FROM decision_at)::float8 "
                "       AS decision_at, "
                "       extract(epoch FROM max_input_at)::float8 "
                "       AS max_input_at, inputs_read, recorded_action, "
                "       world_action, pnl_usd, pnl_basis, unmeasured, label "
                "  FROM twin_decision_traces WHERE result_id = $1 "
                " ORDER BY seq LIMIT $2", r["result_id"], int(limit))]
        return _env("OK" if results else "EMPTY",
                    None if results else "SCENARIO_NOT_YET_RUN",
                    scenario=_row(sc), results=results, traces=traces,
                    summed_across_books=False, production_truth=False)
    return await _read(fn)


@router.get(BASE + "/research/transfer", dependencies=[Depends(require_read)])
async def research_transfer() -> dict:
    async def fn(conn):
        tests = [_row(r) for r in await conn.fetch(
            "SELECT t.test_id, t.dimension, t.source_sport, t.target_sport, "
            "       t.hypothesis, t.metric, t.criteria, t.criteria_spec_id, "
            "       extract(epoch FROM t.declared_at)::float8 AS declared_at,"
            "       t.source_n, t.source_value, t.source_ci_low, "
            "       t.source_ci_high, e.forward_n, e.target_value, "
            "       e.target_ci_low, e.target_ci_high, e.diff, e.diff_ci_low,"
            "       e.diff_ci_high, coalesce(e.classification, 'UNKNOWN') "
            "       AS classification, coalesce(e.reason, "
            "       'NOT_YET_EVALUATED') AS reason, "
            "       extract(epoch FROM e.evaluated_at)::float8 "
            "       AS evaluated_at "
            "  FROM twin_transfer_tests t LEFT JOIN LATERAL ("
            "       SELECT * FROM twin_transfer_evaluations x "
            "        WHERE x.test_id = t.test_id "
            "        ORDER BY x.evaluated_at DESC, x.evaluation_id DESC "
            "        LIMIT 1) e ON true "
            " ORDER BY t.dimension, t.target_sport")]
        return _snap(await _latest(conn, "TRANSFER"),
                     extra={"tests": tests, "transfer_assumed": False})
    return await _read(fn)


#: (266) historical agent id -> the agent it names now; pinned equal to
#: registry.HISTORICAL_ALIASES by a test (this module imports no registry)
TWIN_ALIASES = {"EDDIE": "ARCHER"}


@router.get(BASE + "/profitability/scorecards",
            dependencies=[Depends(require_read)])
async def profitability_scorecards(
        agent: str = Query(default="", pattern="^(|DEREK|XAVIER|ARCHER|EDDIE|"
                           "SCOUT|KAREN|ALLOCATOR|AUDREY)$")) -> dict:
    # (266) EDDIE is ARCHER's historical alias: either name reads both, and a
    # run scored before the rename shows its EDDIE rows as ARCHER's, labelled
    canon = TWIN_ALIASES.get(agent, agent)
    want = ([canon] + sorted(a for a, c in TWIN_ALIASES.items()
                             if c == canon)) if agent else []

    async def fn(conn):
        rid = await conn.fetchval(
            "SELECT run_id FROM twin_agent_scorecards "
            " ORDER BY computed_at DESC LIMIT 1")
        if rid is None:
            return _env("EMPTY", "NO_TWIN_RUN_YET", run_id=None, agents={})
        rows = [_row(r) for r in await conn.fetch(
            "SELECT agent, metric, book, value, numerator, denominator, "
            "       sample_n, ci_low, ci_high, ci_method, status, reason, "
            "       basis, unit, label, "
            "       extract(epoch FROM computed_at)::float8 AS computed_at "
            "  FROM twin_agent_scorecards WHERE run_id = $1 "
            "   AND (cardinality($2::text[]) = 0 OR agent = ANY($2::text[])) "
            " ORDER BY agent, metric, book",
            rid, want)]
        by: dict = {}
        for r in rows:
            if r["agent"] in TWIN_ALIASES:
                r = dict(r, historical_alias=r["agent"],
                         agent=TWIN_ALIASES[r["agent"]])
            by.setdefault(r["agent"], []).append(r)
        return _env("OK", None, run_id=rid,
                    computed_at=rows[0]["computed_at"] if rows else None,
                    agents=by, economic_contribution_only=True,
                    summed_across_books=False)
    return await _read(fn)


@router.get(BASE + "/profitability/evidence-ladder",
            dependencies=[Depends(require_read)])
async def profitability_ladder() -> dict:
    async def fn(conn):
        r = await conn.fetchrow(
            "SELECT run_id, level, passes, levels, criteria_spec_id, "
            "       criteria_sha256, confidence_status, confidence, "
            "       confidence_spec_id, label, "
            "       extract(epoch FROM computed_at)::float8 AS computed_at "
            "  FROM twin_evidence_ladder ORDER BY computed_at DESC LIMIT 1")
        if r is None:
            return _env("EMPTY", "NO_TWIN_RUN_YET", data=None)
        d = _row(r)
        crit = await conn.fetchrow(
            "SELECT spec FROM twin_frozen_specs WHERE spec_id = $1",
            d["criteria_spec_id"])
        return _env("OK", None, run_id=d["run_id"],
                    computed_at=d["computed_at"], level=d["level"],
                    confidence_status=d["confidence_status"], data=d,
                    criteria=_j(crit["spec"]) if crit else None,
                    never_skips_a_level=True)
    return await _read(fn)


@router.get(BASE + "/profitability/kill-switches",
            dependencies=[Depends(require_read)])
async def profitability_kill_switches(
        limit: int = Query(default=100, ge=1, le=1000)) -> dict:
    async def fn(conn):
        recs = [_row(r) for r in await conn.fetch(
            "SELECT recommendation_id, run_id, criterion, book, strategy, "
            "       recommendation, evidence, criteria_spec_id, authority, "
            "       applied, stops_capital, activates_capital, label, "
            "       extract(epoch FROM created_at)::float8 AS created_at "
            "  FROM twin_kill_switch_recommendations "
            " ORDER BY created_at DESC LIMIT $1", int(limit))]
        return _snap(await _latest(conn, "KILL_SWITCHES"),
                     extra={"recommendations": recs,
                            "effect": "RECOMMEND_PAUSE_RECORD_ONLY",
                            "stops_capital": False,
                            "activates_capital": False})
    return await _read(fn)


@router.get(BASE + "/evals", dependencies=[Depends(require_read)])
async def evals_index(scope: str = Query(default="",
                                         pattern="^(|AGENT|HANDOFF|MACRO)$")
                      ) -> dict:
    async def fn(conn):
        rid = await conn.fetchval(
            "SELECT run_id FROM twin_evals ORDER BY computed_at DESC LIMIT 1")
        if rid is None:
            return _env("EMPTY", "NO_TWIN_RUN_YET", run_id=None, rows=[])
        rows = [_row(r) for r in await conn.fetch(
            "SELECT scope, subject, dimension, n_evaluated, n_passed, "
            "       pass_rate, ci_low, ci_high, status, reason, failures, "
            "       detail, label, "
            "       extract(epoch FROM computed_at)::float8 AS computed_at "
            "  FROM twin_evals WHERE run_id = $1 AND ($2 = '' OR scope = $2)"
            " ORDER BY scope, subject, dimension", rid, scope)]
        snap = await _latest(conn, "EVALS")
        macro = (_j(snap["payload"]) or {}).get("macro") if snap else None
        return _env("OK", None, run_id=rid,
                    computed_at=rows[0]["computed_at"] if rows else None,
                    rows=rows, macro=macro,
                    judges="persisted records, never prose")
    return await _read(fn)
