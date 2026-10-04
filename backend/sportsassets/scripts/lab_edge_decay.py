"""LAB-A EDGE DECAY: offline analysis and the forward SHADOW snapshot.

    python -m sportsassets.scripts.lab_edge_decay analyze-extract <log>
        Replays a research-sql run of research/lab_a_edge_decay_extract.sql
        (the job log, one JSON object per qualified opportunity) through the
        SAME pure core the endpoint uses -- every row through the lab's
        point-in-time accessor -- and prints the summary, the PM answers and
        the faster-processing evidence as JSON. Read only; touches nothing.

    python -m sportsassets.scripts.lab_edge_decay record-summary --dsn <dsn>
        --recorded-by <name> [--days 7]
        Computes the summary from a database (READ ONLY transaction) and
        appends ONE EDGE_DECAY_SUMMARY row to lab_edge_decay_snapshots
        (migration 242, SHADOW_RESEARCH_ONLY, evidence_status
        RETROSPECTIVE_ONLY) -- the forward SHADOW plan's dated record. The
        insert is the only write, in its own transaction.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from ..lab import edge_decay as ED
from ..lab import edge_decay_reads as R


def analyze_text(text: str, *, detail: bool = False) -> dict:
    ex = R.parse_extract(text)
    results = ED.evaluate_all(ex["records"])
    summary = ED.summarize(results)
    realized = ED.realized_vs_latency(results)
    out = {"source": "research-sql extraction",
           "extract_version": R.EXTRACT_VERSION,
           "sql_sha256": ex["sql_sha256"],
           "rows_declared": ex["rows_declared"],
           "rows_parsed": len(ex["records"]),
           "summary": summary, "faster_processing_evidence": realized,
           "pm_answers": ED.pm_answers(summary, realized),
           "evidence_status": "RETROSPECTIVE_ONLY"}
    if detail:
        out["opportunities"] = results
    return out


async def record_summary(dsn: str, *, recorded_by: str, days: float) -> dict:
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        now = time.time()
        async with conn.transaction(readonly=True):
            data = await R.load_records(conn, now=now,
                                        since=now - days * 86400.0)
        results = ED.evaluate_all(data["records"])
        summary = ED.summarize(results)
        payload = {"summary": summary,
                   "faster_processing_evidence": ED.realized_vs_latency(
                       results),
                   "derek_v2_rows": data["derek_v2_rows"],
                   "bounded": data["bounded"]}
        sid = await conn.fetchval(
            "INSERT INTO lab_edge_decay_snapshots (kind, version, "
            " evidence_status, window_start, window_end, source, code_sha, "
            " payload, recorded_by) VALUES ('EDGE_DECAY_SUMMARY', $1, "
            " 'RETROSPECTIVE_ONLY', to_timestamp($2), to_timestamp($3), "
            " 'DATABASE_POINT_IN_TIME_READ', $4, $5::jsonb, $6) "
            " RETURNING snapshot_id", ED.VERSION, now - days * 86400.0, now,
            os.environ.get("GIT_SHA"), json.dumps(payload, default=str),
            recorded_by)
        return {"snapshot_id": sid, "qualified": summary["qualified"]}
    finally:
        await conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("analyze-extract")
    a1.add_argument("log")
    a1.add_argument("--detail", action="store_true")
    a2 = sub.add_parser("record-summary")
    a2.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    a2.add_argument("--recorded-by", required=True)
    a2.add_argument("--days", type=float, default=7.0)
    a = ap.parse_args(argv)
    if a.cmd == "analyze-extract":
        with open(a.log, encoding="utf-8") as fh:
            out = analyze_text(fh.read(), detail=a.detail)
        json.dump(out, sys.stdout, indent=1, default=str)
        print()
        return 0
    out = asyncio.run(record_summary(a.dsn, recorded_by=a.recorded_by,
                                     days=a.days))
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
