"""THE ONLY WRITER OF THE LEARNING LAYER: migration 218's poslearn_* tables
and nothing else (pinned by tests/test_poslearn_authority.py, which parses
every SQL statement in sportsassets/poslearn and refuses a write to any
other table). It NEVER writes poslearn_human_approvals: that record is an
operator action by a human (pinned too).

Every append-only rule is the database's (triggers); this module just
inserts. Only runs and snapshots are pruned (RETENTION_DAYS); the forward
record (registrations, opportunities, forecasts, outcomes, steps,
experiments) is kept.
"""
from __future__ import annotations

import json
import uuid

from . import common as C
from .reads import ts

VERSION = "POSLEARN_STORE_V1"
RETENTION_DAYS = 30


def _j(v) -> str:
    return json.dumps(v, default=str)


def new_run_id() -> str:
    return "poslearnrun:%s" % uuid.uuid4().hex[:20]


async def record_component(conn, *, run_id, component, started_at,
                           finished_at, status, error=None, summary=None,
                           version=VERSION):
    await conn.execute(
        "INSERT INTO poslearn_runs (run_id, component, started_at, "
        " finished_at, status, error, duration_ms, summary, version) VALUES "
        " ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9) "
        "ON CONFLICT (run_id, component) DO UPDATE SET "
        " finished_at=EXCLUDED.finished_at, status=EXCLUDED.status, "
        " error=EXCLUDED.error, duration_ms=EXCLUDED.duration_ms, "
        " summary=EXCLUDED.summary",
        run_id, component, ts(started_at), ts(finished_at), status,
        None if error is None else str(error)[:500],
        int(round((float(finished_at) - float(started_at)) * 1000)),
        _j(summary or {}), version)


async def save_snapshot(conn, *, run_id, component, payload, now,
                        version=VERSION):
    await conn.execute(
        "INSERT INTO poslearn_snapshots (run_id, component, computed_at, "
        " payload, version) VALUES ($1,$2,$3,$4::jsonb,$5) "
        "ON CONFLICT (run_id, component) DO UPDATE SET "
        " payload=EXCLUDED.payload, computed_at=EXCLUDED.computed_at",
        run_id, component, ts(now), _j(payload), version)


async def insert_registration(conn, doc: dict, *, status, registered_at,
                              status_reason=None) -> str:
    text = C.canonical(doc)
    win = doc.get("training_window")
    start = end = None
    if isinstance(win, dict) and win.get("start") is not None:
        start, end = ts(win["start"]), ts(win["end"])
    rid = "%s@%d" % (doc["subject_id"], int(doc["version"]))
    await conn.execute(
        "INSERT INTO poslearn_registrations (registration_id, kind, "
        " subject_id, version, family, role, canonical_json, document, "
        " sha256, training_window_start, training_window_end, min_sample, "
        " family_size, registered_at, status, status_reason) VALUES "
        " ($1,$2,$3,$4,$5,$6,$7::text,$7::text::jsonb,$8,$9,$10,$11,$12,$13,$14,$15)",
        rid, doc["kind"], doc["subject_id"], int(doc["version"]),
        doc["family"], doc["role"], text, C.sha256_text(text), start, end,
        int(doc["minimum_sample"]), int(doc["family_size"]),
        ts(registered_at), status, status_reason)
    return rid


async def set_registration_status(conn, registration_id, status, reason):
    await conn.execute(
        "UPDATE poslearn_registrations SET status = $2, status_reason = $3 "
        " WHERE registration_id = $1 AND status <> $2",
        registration_id, status, reason)


async def insert_opportunity(conn, o: dict) -> bool:
    got = await conn.fetchval(
        "INSERT INTO poslearn_opportunities (opportunity_id, source_kind, "
        " source_id, unit, opportunity_at, features_as_of, captured_at, "
        " us_market_slug, sport, league, market, live_state, record_purpose,"
        " p_reference, price, price_basis, fee, features, unavailable) "
        "VALUES ($1,'EXTERNAL_VALUATION',$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,"
        " $12,$13,$14,$15,$16,$17::jsonb,$18::jsonb) "
        "ON CONFLICT DO NOTHING RETURNING opportunity_id",
        o["opportunity_id"], int(o["source_id"]), o["unit"],
        ts(o["opportunity_at"]), ts(o["features_as_of"]),
        ts(o["captured_at"]), o.get("us_market_slug"), o["sport"],
        o["league"], o["market"], o["live_state"], o.get("record_purpose"),
        o.get("p_reference"), o.get("price"), o.get("price_basis"),
        o.get("fee"), _j(o["features"]), _j(o.get("unavailable") or {}))
    return got is not None


async def insert_outcome(conn, opportunity_id, *, outcome_class, outcome,
                         basis, outcome_at):
    await conn.execute(
        "INSERT INTO poslearn_outcomes (opportunity_id, outcome_class, "
        " outcome, basis, outcome_at) VALUES ($1,$2,$3,$4,$5) "
        "ON CONFLICT DO NOTHING", opportunity_id, outcome_class, outcome,
        basis, None if outcome_at is None else ts(outcome_at))


FORECAST_SQL = (
    "INSERT INTO poslearn_forecasts (registration_id, opportunity_id, "
    " probability, predicted_net_edge, action, abstain_reason, shadow_usd, "
    " edge_confidence, expected_net_edge, expected_net_edge_ci_low, "
    " expected_net_edge_ci_high, avoidance_risk, avoidance_level, reasons, "
    " output, unmeasured, predicted_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,"
    " $10,$11,$12,$13,$14::jsonb,$15::jsonb,$16::jsonb,$17) "
    "ON CONFLICT DO NOTHING")


async def insert_forecast(conn, registration_id, opportunity_id, fc: dict,
                          *, predicted_at):
    await conn.execute(
        FORECAST_SQL, registration_id, opportunity_id,
        C.num(fc.get("probability")), C.num(fc.get("predicted_net_edge")),
        fc["action"], fc.get("abstain_reason"), C.num(fc.get("shadow_usd")),
        C.num(fc.get("edge_confidence")), C.num(fc.get("expected_net_edge")),
        C.num(fc.get("expected_net_edge_ci_low")),
        C.num(fc.get("expected_net_edge_ci_high")),
        C.num(fc.get("avoidance_risk")), fc.get("avoidance_level"),
        _j(fc.get("reasons") or []), _j(fc.get("output") or {}),
        _j(fc.get("unmeasured") or {}), ts(predicted_at))


async def insert_step(conn, registration_id, *, step, actor, outcome,
                      evidence):
    await conn.execute(
        "INSERT INTO poslearn_promotion_steps (registration_id, step, actor,"
        " outcome, evidence) VALUES ($1,$2,$3,$4,$5::jsonb) "
        "ON CONFLICT (registration_id, step) DO NOTHING",
        registration_id, step, actor, outcome, _j(evidence))


async def insert_experiment(conn, d: dict, *, registered_at):
    await conn.execute(
        "INSERT INTO poslearn_experiments (experiment_id, hypothesis, "
        " primary_metric, secondary_metrics, assignment_unit, randomization,"
        " seed, arms, policy_versions, start_at, stop_at, min_sample, "
        " power_target, alpha, family, family_size, stopping_rule, "
        " failure_criteria, design_sha256, registered_at) VALUES ($1,$2,"
        " $3::jsonb,$4::jsonb,$5,$6::jsonb,$7,$8::jsonb,$9::jsonb,$10,$11,"
        " $12,$13,$14,$15,$16,$17::jsonb,$18::jsonb,$19,$20) "
        "ON CONFLICT DO NOTHING",
        d["experiment_id"], d["hypothesis"], _j(d["primary_metric"]),
        _j(d["secondary_metrics"]), d["assignment_unit"],
        _j(d["randomization"]), d["seed"], _j(d["arms"]),
        _j(d["policy_versions"]), ts(d["start_at"]), ts(d["stop_at"]),
        int(d["min_sample"]), float(d["power_target"]), float(d["alpha"]),
        d["family"], int(d["family_size"]), _j(d["stopping_rule"]),
        _j(d["failure_criteria"]), d["design_sha256"], ts(registered_at))


async def set_experiment_status(conn, experiment_id, status, reason,
                                result=None):
    await conn.execute(
        "UPDATE poslearn_experiments SET status = $2, status_reason = $3, "
        " result = coalesce($4::jsonb, result) WHERE experiment_id = $1",
        experiment_id, status, reason,
        None if result is None else _j(result))


async def insert_review(conn, experiment_id, *, kind, actor, outcome,
                        findings):
    await conn.execute(
        "INSERT INTO poslearn_experiment_reviews (experiment_id, kind, actor,"
        " outcome, findings) VALUES ($1,$2,$3,$4,$5::jsonb)",
        experiment_id, kind, actor, outcome, _j(findings))


async def insert_assignment(conn, experiment_id, *, unit_id, opportunity_id,
                            arm, draw, assigned_at):
    await conn.execute(
        "INSERT INTO poslearn_experiment_assignments (experiment_id, "
        " unit_id, opportunity_id, arm, draw, assigned_at) VALUES "
        " ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING",
        experiment_id, unit_id, opportunity_id, arm, float(draw),
        ts(assigned_at))


async def insert_experiment_outcome(conn, experiment_id, unit_id, metrics,
                                    *, outcome_at):
    await conn.execute(
        "INSERT INTO poslearn_experiment_outcomes (experiment_id, unit_id, "
        " metrics, outcome_at) VALUES ($1,$2,$3::jsonb,$4) "
        "ON CONFLICT DO NOTHING", experiment_id, unit_id, _j(metrics),
        None if outcome_at is None else ts(outcome_at))


async def prune(conn, *, now, days=RETENTION_DAYS):
    cut = ts(float(now) - days * 86400.0)
    await conn.execute(
        "DELETE FROM poslearn_snapshots WHERE computed_at < $1", cut)
    await conn.execute(
        "DELETE FROM poslearn_runs WHERE started_at < $1", cut)
