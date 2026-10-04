"""LAB-F DRIFT SENTINEL: THE SHADOW RECORD WRITER (migration 244).

The ONLY writer of lab_drift_runs / lab_drift_findings / lab_drift_tasks,
called only by the operator CLI (`python -m sportsassets.lab.drift_runner
--record`). Append-only INSERTs; a work item or research task is recorded
once per drift episode (its id is deterministic) and later runs that see the
same episode add nothing. authority = SHADOW_RESEARCH_ONLY and
production_effect = NONE are the database's defaults and CHECKs.

It never writes agent_work_requests (the 226 queue refuses a revalidation
kind; the record says NOT_ENQUEUED) and touches no order, decision, policy,
threshold, limit or capital table.
"""
from __future__ import annotations

import hashlib
import json
import time

from . import drift_sentinel as DS

VERSION = "LAB_DRIFT_STORE_V1"


def canonical_sha256(report: dict) -> str:
    return hashlib.sha256(json.dumps(report, sort_keys=True, default=str,
                                     separators=(",", ":")).encode()
                          ).hexdigest()


def run_id_for(report: dict) -> str:
    return "labdriftrun:%s" % canonical_sha256(report)[:24]


async def has_schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('lab_drift_runs') IS NOT NULL"))
    except Exception:                                           # noqa: BLE001
        return False


async def record_run(conn, report: dict, *, recorded_by: str,
                     computed_at: float | None = None) -> dict:
    """One transaction: the run, its findings, the episode's tasks. Returns
    {recorded, run_id, findings, tasks_new} or {recorded: False, why}."""
    if not await has_schema(conn):
        return {"recorded": False, "why": "MIGRATION_244_NOT_APPLIED"}
    if report.get("authority") != DS.AUTHORITY:
        return {"recorded": False, "why": "NOT_A_SHADOW_RESEARCH_REPORT"}
    sha = canonical_sha256(report)
    rid = "labdriftrun:%s" % sha[:24]
    at = float(computed_at if computed_at is not None else time.time())
    tested = [f for f in report["findings"] if f["status"] != DS.UNAVAILABLE]
    async with conn.transaction():
        got = await conn.fetchval(
            "INSERT INTO lab_drift_runs (run_id, as_of, computed_at, "
            " sentinel_version, stats_version, params, question_h, "
            " strategies, findings_n, tests_n, report_sha256, recorded_by) "
            "VALUES ($1, to_timestamp($2), to_timestamp($3), $4, $5, "
            " $6::jsonb, $7::jsonb, $8::jsonb, $9, $10, $11, $12) "
            "ON CONFLICT (run_id) DO NOTHING RETURNING run_id",
            rid, float(report["as_of"]), at, report["version"],
            report["stats_version"], json.dumps(report["params"]),
            json.dumps(report["question_h"], default=str),
            json.dumps(report["strategies"], default=str),
            len(report["findings"]), len(tested), sha, recorded_by)
        if got is None:
            return {"recorded": False, "why": "IDENTICAL_REPORT_ALREADY_"
                    "RECORDED", "run_id": rid}
        for i, f in enumerate(report["findings"]):
            await conn.execute(
                "INSERT INTO lab_drift_findings (run_id, finding_no, "
                " strategy, version_key, league, metric, evidence_class, "
                " status, why, p, q, n_ref, n_cmp, statistic, windows, "
                " source) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,"
                " $14::jsonb,$15::jsonb,$16)",
                rid, i, f["strategy"], f["version_key"], f["league"],
                f["metric"], f["evidence_class"], f["status"], f.get("why"),
                f.get("p") if f["status"] != DS.UNAVAILABLE else None,
                f.get("q") if f["status"] != DS.UNAVAILABLE else None,
                int(f.get("n_ref") or 0), int(f.get("n_cmp") or 0),
                (None if f.get("statistic") is None
                 else json.dumps(f["statistic"], default=str)),
                json.dumps(f["windows"]), f["source"])
        new = 0
        for w in report.get("work_items") or []:
            new += int(bool(await conn.fetchval(
                "INSERT INTO lab_drift_tasks (task_id, run_id, task_class, "
                " agent_id, kind, strategy, version_key, record, "
                " integration_status) VALUES ($1,$2,'WORK_ITEM',$3,$4,$5,$6,"
                " $7::jsonb,$8) ON CONFLICT (task_id) DO NOTHING "
                "RETURNING task_id",
                w["request_id"], rid, w["agent_id"], w["kind"],
                w["detail"]["strategy"], w["detail"]["version_key"],
                json.dumps(w, default=str), w["integration"]["status"])))
        for t in report.get("research_tasks") or []:
            new += int(bool(await conn.fetchval(
                "INSERT INTO lab_drift_tasks (task_id, run_id, task_class, "
                " kind, strategy, version_key, record) VALUES ($1,$2,"
                " 'RESEARCH_TASK',$3,$4,$5,$6::jsonb) "
                "ON CONFLICT (task_id) DO NOTHING RETURNING task_id",
                t["task_id"], rid, t["kind"], t["strategy"],
                t["version_key"], json.dumps(t, default=str))))
    return {"recorded": True, "run_id": rid,
            "findings": len(report["findings"]), "tasks_new": new}
