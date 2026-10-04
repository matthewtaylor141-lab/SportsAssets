"""LAB-F DRIFT SENTINEL: THE OPERATOR CLI (SHADOW; no loop, no schedule).

    # the report as of now (or --as-of EPOCH), READ ONLY, printed as JSON
    python -m sportsassets.lab.drift_runner [--as-of EPOCH]

    # the same, then recorded append-only into migration 244's tables
    python -m sportsassets.lab.drift_runner --record --recorded-by NAME

    # the production export: the reader's SELECTs rendered with literal
    # arguments into research SQL files (research-sql.yml runs them read-
    # only); --split-decisions cuts the decisions rows across files by t
    python -m sportsassets.lab.drift_runner --export-sql PREFIX --as-of EPOCH \\
        --since EPOCH [--absent canonical_executions,...] [--split-decisions T1,T2]

    # the production retrospective: parse those runs' job logs and compute
    # the report with the SAME accessor and sentinel the endpoint uses
    python -m sportsassets.lab.drift_runner --from-log LOG [LOG ...] [--summary]

DATABASE_URL names the database for the first two forms. Nothing here is
started by the API or a worker; it reads (and with --record writes only the
lab_drift_* tables) when an operator runs it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from . import drift_reads as R
from . import drift_sentinel as DS


def report_from_log(text: str, *, params: dict | None = None) -> dict:
    parsed = R.parse_export(text)
    meta = parsed.get("meta")
    if not meta:
        raise ValueError("no LABDRIFT|meta line in the log")
    p = DS._params(params)
    for k in ("ref_max_s", "cmp_max_s"):
        if abs(float(meta[k]) - float(p[k])) > 1e-6:
            raise ValueError("export %s %s differs from the sentinel's %s"
                             % (k, meta[k], p[k]))
    rel = parsed.get("relations") or {}
    present = {k for k, v in rel.items() if v}
    data = R.assemble(parsed, clock=float(meta["clock"]),
                      since=float(meta["since"]), present=present)
    return DS.compute(data, params=params)


def summary(rep: dict) -> dict:
    return {
        "as_of": rep["as_of_iso"], "question_h": {
            k: rep["question_h"][k] for k in (
                "answer", "material", "watch", "normal", "unavailable",
                "inactive")},
        "strategies": [{k: s[k] for k in (
            "strategy", "version_key", "active", "window_status",
            "window_why", "status", "material_metrics", "watch_metrics",
            "rows_measured", "rows_total",
            "rollup_decision_metric_coverage")} | {
            "confidence_modifier": s["confidence_modifier"]["value"],
            "modifier_why": s["confidence_modifier"]["why"]}
            for s in rep["strategies"]],
        "windows": {s: {k: w.get(k) for k in (
            "version_key", "first_decision_at", "life_h", "ref_iso",
            "cmp_iso", "status", "why")} for s, w in rep["windows"].items()},
        "tested": [{k: f.get(k) for k in (
            "strategy", "league", "metric", "evidence_class", "status",
            "n_ref", "n_cmp", "rows_ref", "rows_cmp", "p", "q")} | {
            "psi": (f.get("statistic") or {}).get("psi"),
            "psi_null": (f.get("statistic") or {}).get(
                "psi_null_expectation"),
            "ks_d": (f.get("statistic") or {}).get("ks_d"),
            "tvd": (f.get("statistic") or {}).get("tvd"),
            "ref": (f.get("statistic") or {}).get("ref_summary"),
            "cmp": (f.get("statistic") or {}).get("cmp_summary")}
            for f in rep["findings"] if f["status"] != DS.UNAVAILABLE],
        "unavailable_rollups": [{k: f.get(k) for k in (
            "strategy", "metric", "evidence_class", "n_ref", "n_cmp",
            "why")} for f in rep["findings"]
            if f["status"] == DS.UNAVAILABLE and f["league"] == DS.ALL],
        "work_items": len(rep["work_items"]),
        "research_tasks": len(rep["research_tasks"]),
        "multiple_testing": rep["multiple_testing"],
        "context": {k: rep["context"][k] for k in (
            "unresolved_orders_excluded", "adverse_selection_unmeasured",
            "positions_unattributed", "shadow", "actual")},
    }


async def _live(as_of: float | None, record: bool, recorded_by: str) -> dict:
    import asyncpg

    from . import drift_store as ST
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")
    clock = float(as_of) if as_of is not None else time.time()
    conn = await asyncpg.connect(dsn)
    try:
        tr = conn.transaction(readonly=True)
        await tr.start()
        try:
            await conn.execute("SET LOCAL statement_timeout = 60000")
            data = await R.gather(conn, clock=clock, since=None,
                                  ref_max_s=DS.DEFAULTS["ref_max_s"],
                                  cmp_max_s=DS.DEFAULTS["cmp_max_s"])
        finally:
            await tr.rollback()
        rep = DS.compute(data)
        if record:
            rep["recorded"] = await ST.record_run(conn, rep,
                                                  recorded_by=recorded_by)
        return rep
    finally:
        await conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="drift_runner")
    ap.add_argument("--as-of", type=float, default=None)
    ap.add_argument("--since", type=float, default=None)
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--recorded-by", default="lab-drift-runner (operator)")
    ap.add_argument("--export-sql", default=None)
    ap.add_argument("--absent", default="")
    ap.add_argument("--split-decisions", default="")
    ap.add_argument("--from-log", nargs="*", default=None)
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args(argv)
    if a.export_sql:
        if a.as_of is None or a.since is None:
            raise SystemExit("--export-sql needs --as-of and --since")
        absent = {x for x in a.absent.split(",") if x}
        present = set(R.RELATIONS) - absent
        common = dict(clock=a.as_of, since=a.since,
                      ref_max_s=DS.DEFAULTS["ref_max_s"],
                      cmp_max_s=DS.DEFAULTS["cmp_max_s"], present=present)
        files = {}
        if not a.split_decisions:
            files[a.export_sql + ".sql"] = R.export_sql(**common)
        else:
            cuts = [float(x) for x in a.split_decisions.split(",") if x]
            edges = [-1e18] + sorted(cuts) + [1e18]
            files[a.export_sql + "_base.sql"] = R.export_sql(
                sources=[s for s, _q, _a in R.query_plan(
                    clock=a.as_of, since=a.since,
                    ref_max_s=DS.DEFAULTS["ref_max_s"],
                    cmp_max_s=DS.DEFAULTS["cmp_max_s"], present=present)
                    if s != "decisions"], **common)
            for i in range(len(edges) - 1):
                files["%s_d%d.sql" % (a.export_sql, i + 1)] = R.export_sql(
                    sources=["decisions"], between=(edges[i], edges[i + 1]),
                    **common)
        for path, sql in files.items():
            with open(path, "w") as fh:
                fh.write(sql)
            print(path)
        return 0
    if a.from_log:
        text = ""
        for path in a.from_log:
            with open(path) as fh:
                text += fh.read() + "\n"
        rep = report_from_log(text)
    else:
        rep = asyncio.run(_live(a.as_of, a.record, a.recorded_by))
    json.dump(summary(rep) if a.summary else rep, sys.stdout, indent=1,
              default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":                                      # pragma: no cover
    raise SystemExit(main())
