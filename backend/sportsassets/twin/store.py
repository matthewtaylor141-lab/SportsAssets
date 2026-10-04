"""THE ONLY WRITER OF THE RESEARCH TWIN LAYER: migration 219's twin_* tables
and nothing else (pinned by tests/test_twin_authority.py, which parses
every SQL statement in sportsassets/twin and refuses a write to any other
table, and any write outside this module).

APPEND-ONLY. Every statement here is an INSERT; the tables' triggers
refuse any update or delete of a stored row. Growth is bounded by content de-duplication: a scenario result,
a transfer evaluation, a kill-switch recommendation and a snapshot are not
re-inserted while their inputs are unchanged.
"""
from __future__ import annotations

import json
import uuid

from . import common as C

VERSION = "TWIN_STORE_V1"

TABLES = ("twin_runs", "twin_snapshots", "twin_frozen_specs",
          "twin_scenarios", "twin_scenario_results", "twin_decision_traces",
          "twin_transfer_tests", "twin_transfer_evaluations",
          "twin_agent_scorecards", "twin_evidence_ladder",
          "twin_kill_switch_recommendations", "twin_evals")


class FrozenSpecChanged(RuntimeError):
    """A frozen spec's (kind/key, version) now hashes differently."""


def _j(v) -> str:
    return json.dumps(v, default=str, sort_keys=True)


def new_run_id() -> str:
    return "twinrun:%s" % uuid.uuid4().hex[:20]


async def tables_ready(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass('twin_runs') IS NOT NULL "
        "   AND to_regclass('twin_evals') IS NOT NULL"))


async def record_component(conn, *, run_id, component, started_at,
                           finished_at, status, error=None, summary=None,
                           version=VERSION):
    await conn.execute(
        "INSERT INTO twin_runs (run_id, component, started_at, finished_at,"
        " status, error, duration_ms, summary, version) VALUES "
        " ($1,$2,to_timestamp($3),to_timestamp($4),$5,$6,$7,$8::jsonb,$9) "
        "ON CONFLICT (run_id, component) DO NOTHING",
        run_id, component, float(started_at), float(finished_at), status,
        None if error is None else str(error)[:500],
        int(round((float(finished_at) - float(started_at)) * 1000)),
        _j(summary or {}), version)


def content_sha(payload: dict) -> str:
    return C.sha({k: v for k, v in payload.items()
                  if k not in ("computed_at", "run_id")})


async def save_snapshot(conn, *, run_id, component, payload, now,
                        version=VERSION) -> bool:
    """Insert unless the latest snapshot of the component has the same
    content (then the read keeps serving it, with its own instant)."""
    digest = content_sha(payload)
    last = await conn.fetchval(
        "SELECT content_sha256 FROM twin_snapshots WHERE component = $1 "
        " ORDER BY computed_at DESC, snapshot_id DESC LIMIT 1", component)
    if last == digest:
        return False
    await conn.execute(
        "INSERT INTO twin_snapshots (run_id, component, computed_at, "
        " payload, content_sha256, version) VALUES ($1,$2,to_timestamp($3),"
        " $4::jsonb,$5,$6) ON CONFLICT (run_id, component) DO NOTHING",
        run_id, component, float(now), _j(payload), digest, version)
    return True


async def register_spec(conn, *, kind: str, version: int, spec: dict,
                        now: float) -> dict:
    digest = C.sha(spec)
    sid = "%s:v%d:%s" % (kind, int(version), digest[:16])
    await conn.execute(
        "INSERT INTO twin_frozen_specs (spec_id, kind, version, spec, "
        " spec_sha256, frozen_at) VALUES ($1,$2,$3,$4::jsonb,$5,"
        " to_timestamp($6)) ON CONFLICT DO NOTHING",
        sid, kind, int(version), _j(spec), digest, float(now))
    got = await conn.fetchrow(
        "SELECT spec_id, spec_sha256, extract(epoch FROM frozen_at)::float8 "
        "       AS frozen_at FROM twin_frozen_specs "
        " WHERE kind = $1 AND version = $2", kind, int(version))
    if got["spec_sha256"] != digest:
        raise FrozenSpecChanged(
            "%s v%d is frozen with sha %s; the code now hashes %s -- bump "
            "the version instead of editing a frozen spec"
            % (kind, version, got["spec_sha256"][:12], digest[:12]))
    return {"spec_id": got["spec_id"], "spec_sha256": digest,
            "frozen_at": got["frozen_at"], "spec": spec}


async def register_scenarios(conn, scenarios: list, *, now: float,
                             engine_version: str) -> list:
    for s in scenarios:
        await conn.execute(
            "INSERT INTO twin_scenarios (scenario_id, scenario_key, version,"
            " world, spec, spec_sha256, engine_version, frozen_at) VALUES "
            " ($1,$2,$3,$4,$5::jsonb,$6,$7,to_timestamp($8)) "
            "ON CONFLICT DO NOTHING",
            s["scenario_id"], s["scenario_key"], s["version"], s["world"],
            _j(s["spec"]), s["spec_sha256"], engine_version, float(now))
        got = await conn.fetchval(
            "SELECT spec_sha256 FROM twin_scenarios WHERE scenario_key = $1 "
            "   AND version = $2", s["scenario_key"], s["version"])
        if got != s["spec_sha256"]:
            raise FrozenSpecChanged(
                "scenario %s v%d is frozen with sha %s; the catalog now "
                "hashes %s" % (s["scenario_key"], s["version"],
                               (got or "")[:12], s["spec_sha256"][:12]))
    return scenarios


RESULT_SQL = (
    "INSERT INTO twin_scenario_results (result_id, run_id, scenario_id, "
    " spec_sha256, basis_book, status, unavailable_reason, window_start, "
    " window_end, input_sha256, output_sha256, engine_version, computed_at,"
    " baseline, world, comparison, counts, unmeasured) VALUES ($1,$2,$3,$4,"
    " $5,$6,$7,to_timestamp($8),to_timestamp($9),$10,$11,$12,"
    " to_timestamp($13),$14::jsonb,$15::jsonb,$16::jsonb,$17::jsonb,"
    " $18::jsonb) ON CONFLICT (scenario_id, basis_book, input_sha256) "
    " DO NOTHING RETURNING result_id")

TRACE_SQL = (
    "INSERT INTO twin_decision_traces (result_id, seq, subject_id, "
    " decision_kind, decision_at, max_input_at, inputs_read, "
    " recorded_action, world_action, pnl_usd, pnl_basis, unmeasured) "
    "VALUES ($1,$2,$3,$4,to_timestamp($5),to_timestamp($6),$7,$8,$9,$10,"
    " $11,$12::jsonb)")


def result_id(body: dict) -> str:
    return "twinres:" + C.sha([body["scenario_id"], body["basis_book"],
                               body["input_sha256"]])[:24]


async def save_result(conn, *, run_id, now, body, traces) -> str | None:
    """The result id when newly inserted, None when this exact input set
    was already evaluated for the scenario (de-duplicated)."""
    rid = result_id(body)
    got = await conn.fetchval(
        RESULT_SQL, rid, run_id, body["scenario_id"], body["spec_sha256"],
        body["basis_book"], body["status"], body["unavailable_reason"],
        float(body["window_start"]), float(body["window_end"]),
        body["input_sha256"], body["output_sha256"], body["engine_version"],
        float(now),
        None if body["baseline"] is None else _j(body["baseline"]),
        None if body["world"] is None else _j(body["world"]),
        None if body["comparison"] is None else _j(body["comparison"]),
        _j(body["counts"]), _j(body["unmeasured"]))
    if got is None:
        return None
    args = [(rid, i, str(t["subject_id"]), t["decision_kind"],
             float(t["decision_at"]),
             None if t["max_input_at"] is None else float(t["max_input_at"]),
             int(t["inputs_read"]), t["recorded_action"], t["world_action"],
             t["pnl_usd"], t["pnl_basis"], _j(t["unmeasured"]))
            for i, t in enumerate(traces)]
    if args:
        await conn.executemany(TRACE_SQL, args)
    return rid


async def register_transfer_test(conn, t: dict) -> bool:
    got = await conn.fetchval(
        "INSERT INTO twin_transfer_tests (test_id, dimension, source_sport, "
        " target_sport, hypothesis, metric, criteria, criteria_sha256, "
        " criteria_spec_id, declared_at, source_window_end, source_n, "
        " source_value, source_ci_low, source_ci_high) VALUES ($1,$2,$3,$4,"
        " $5,$6,$7::jsonb,$8,$9,to_timestamp($10),to_timestamp($11),$12,$13,"
        " $14,$15) ON CONFLICT DO NOTHING RETURNING test_id",
        t["test_id"], t["dimension"], t["source_sport"], t["target_sport"],
        t["hypothesis"], t["metric"], _j(t["criteria"]),
        t["criteria_sha256"], t["criteria_spec_id"], float(t["declared_at"]),
        float(t["source_window_end"]), int(t["source_n"]),
        t["source_value"], t["source_ci_low"], t["source_ci_high"])
    return got is not None


async def save_transfer_evaluation(conn, *, run_id, now, ev) -> bool:
    got = await conn.fetchval(
        "INSERT INTO twin_transfer_evaluations (test_id, run_id, "
        " evaluated_at, forward_start, forward_n, target_value, "
        " target_ci_low, target_ci_high, diff, diff_ci_low, diff_ci_high, "
        " classification, reason, input_sha256) VALUES ($1,$2,"
        " to_timestamp($3),to_timestamp($4),$5,$6,$7,$8,$9,$10,$11,$12,$13,"
        " $14) ON CONFLICT (test_id, input_sha256) DO NOTHING "
        " RETURNING evaluation_id",
        ev["test_id"], run_id, float(now), float(ev["forward_start"]),
        int(ev["forward_n"]), ev["target_value"], ev["target_ci_low"],
        ev["target_ci_high"], ev["diff"], ev["diff_ci_low"],
        ev["diff_ci_high"], ev["classification"], ev["reason"],
        ev["input_sha256"])
    return got is not None


SCORECARD_SQL = (
    "INSERT INTO twin_agent_scorecards (run_id, agent, metric, book, "
    " computed_at, value, numerator, denominator, sample_n, ci_low, "
    " ci_high, ci_method, status, reason, basis, unit) VALUES ($1,$2,$3,$4,"
    " to_timestamp($5),$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16) "
    "ON CONFLICT (run_id, agent, metric, book) DO NOTHING")


async def save_scorecards(conn, *, run_id, now, rows):
    args = [(run_id, r["agent"], r["metric"], r["book"], float(now),
             r["value"], r["numerator"], r["denominator"], r["sample_n"],
             r["ci_low"], r["ci_high"], r["ci_method"], r["status"],
             r["reason"], r["basis"], r["unit"]) for r in rows]
    if args:
        await conn.executemany(SCORECARD_SQL, args)


async def save_ladder(conn, *, run_id, now, ladder):
    await conn.execute(
        "INSERT INTO twin_evidence_ladder (run_id, computed_at, level, "
        " passes, levels, criteria_spec_id, criteria_sha256, "
        " confidence_status, confidence, confidence_spec_id) VALUES ($1,"
        " to_timestamp($2),$3,$4::boolean[],$5::jsonb,$6,$7,$8,$9::jsonb,"
        " $10) ON CONFLICT (run_id) DO NOTHING",
        run_id, float(now), int(ladder["level"]), list(ladder["passes"]),
        _j(ladder["levels"]), ladder["criteria_spec_id"],
        ladder["criteria_sha256"], ladder["confidence"]["status"],
        _j(ladder["confidence"]), ladder["confidence_spec_id"])


async def save_kill_recommendations(conn, *, run_id, now, recs) -> list:
    made = []
    for r in recs:
        rid = "twinkill:" + C.sha([r["criterion"], r["book"], r["strategy"],
                                   r["evidence_sha256"]])[:24]
        got = await conn.fetchval(
            "INSERT INTO twin_kill_switch_recommendations ("
            " recommendation_id, run_id, criterion, book, strategy, "
            " evidence, evidence_sha256, criteria_spec_id, criteria_sha256,"
            " created_at) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,"
            " to_timestamp($10)) ON CONFLICT DO NOTHING "
            " RETURNING recommendation_id",
            rid, run_id, r["criterion"], r["book"], r["strategy"],
            _j(r["evidence"]), r["evidence_sha256"], r["criteria_spec_id"],
            r["criteria_sha256"], float(now))
        if got:
            made.append(got)
    return made


EVAL_SQL = (
    "INSERT INTO twin_evals (run_id, scope, subject, dimension, "
    " computed_at, n_evaluated, n_passed, pass_rate, ci_low, ci_high, "
    " status, reason, failures, detail) VALUES ($1,$2,$3,$4,"
    " to_timestamp($5),$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb) "
    "ON CONFLICT (run_id, scope, subject, dimension) DO NOTHING")


async def save_evals(conn, *, run_id, now, rows):
    args = [(run_id, r["scope"], r["subject"], r["dimension"], float(now),
             int(r["n_evaluated"]), r["n_passed"], r["pass_rate"],
             r["ci_low"], r["ci_high"], r["status"], r["reason"],
             _j(r.get("failures") or []), _j(r.get("detail") or {}))
            for r in rows]
    if args:
        await conn.executemany(EVAL_SQL, args)
