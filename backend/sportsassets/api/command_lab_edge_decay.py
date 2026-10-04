"""LAB-A EDGE DECAY & LATENCY ECONOMICS (SHADOW / RESEARCH ONLY).

    GET /api/command/lab/edge-decay      the qualified opportunities of the
                                         INVESTMENT completed-game strategy in
                                         the window, their executable edge at
                                         0..+600 s, 75% retention / half-life
                                         / time to zero (Kaplan-Meier with
                                         censoring, cluster-bootstrap
                                         intervals), the latency chain and its
                                         dominant stages, the EV lost between
                                         detection and execution, segments
                                         (Bonferroni intervals, n >= 10), the
                                         fast-lane SHADOW design and the PM
                                         answers A and B
        ?days=<float, default 7, max 14>
        ?limit=<int, default 600, max 1500>  decisions read
        ?detail=1                            per-opportunity records too

READ ONLY transaction under a statement timeout; every historical row passes
the lab's one point-in-time accessor (sportsassets.lab.pit). Nothing here
sends an order, changes capital, limits, credentials, thresholds or a policy,
and nothing on a decision path imports this module or the lab.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Query

from ..lab import edge_decay as ED
from ..lab import edge_decay_reads as R
from ..lab import fastlane as FL
from ..lab import pit as PIT
from .agents_core import _pool, require_read

router = APIRouter()
STATEMENT_TIMEOUT_MS = 15000


async def latest_snapshot(conn, kind: str) -> dict | None:
    """The newest SHADOW snapshot of `kind` (migration 242), or None."""
    if not await conn.fetchval(
            "SELECT to_regclass('lab_edge_decay_snapshots') IS NOT NULL"):
        return None
    r = await conn.fetchrow(
        "SELECT snapshot_id, kind, version, authority, evidence_status, "
        "       source, code_sha, payload, recorded_by, recorded_at "
        "  FROM lab_edge_decay_snapshots WHERE kind = $1 "
        " ORDER BY recorded_at DESC, snapshot_id DESC LIMIT 1", kind)
    if r is None:
        return None
    out = dict(r)
    out["payload"] = json.loads(out["payload"]) if isinstance(
        out["payload"], str) else out["payload"]
    out["recorded_at"] = out["recorded_at"].isoformat()
    return out


def fast_lane(summary: dict, comp: dict | None) -> dict:
    """The fast-lane SHADOW design: the derived dependency graph, the
    measured component latency (if a measurement was recorded), the
    concurrency estimate, urgency classes and the tournament."""
    graph = FL.derive_graph()
    lat = ((comp or {}).get("payload") or {}).get("components") or {}
    p50 = {k: (v or {}).get("cold_p50_s") for k, v in lat.items()}
    cp = (FL.critical_path(graph.get("depends_on") or {}, p50)
          if comp else {"status": "UNAVAILABLE",
                        "why": "NO_COMPONENT_LATENCY_MEASUREMENT_RECORDED"})
    return {"graph": graph, "intent_assembly": FL.intent_assembly(),
            "component_latency": comp or {
                "status": "UNAVAILABLE",
                "why": ("NO_COMPONENT_LATENCY_MEASUREMENT_RECORDED: run "
                        "sportsassets.scripts.lab_fastlane_measure")},
            "concurrency_estimate": cp,
            "urgency_classes": FL.urgency_classes(
                summary, sequential_s=cp.get("sequential_s"),
                concurrent_s=cp.get("concurrent_s")),
            "tournament": FL.tournament_spec()}


async def build(conn, *, now: float, days: float, limit: int,
                detail: bool = False) -> dict:
    data = await R.load_records(conn, now=now, since=now - days * 86400.0,
                                limit=limit)
    results = ED.evaluate_all(data["records"])
    summary = ED.summarize(results)
    realized = ED.realized_vs_latency(results)
    comp = await latest_snapshot(conn, "COMPONENT_LATENCY")
    out = {"version": ED.VERSION, "authority": ED.AUTHORITY,
           "evidence_status": "RETROSPECTIVE_ONLY",
           "as_of": now, "window_days": days,
           "qualified_definition": {
               "strategy": ED.CG_STRATEGY,
               "enter": "verdict ENTER (the strategy read a book; every "
                        "check passed)",
               "near_miss": ("verdict REFUSE with a book read, every refusal "
                             "in %s and a positive best-level gross edge"
                             % sorted(ED.ECONOMIC_REFUSALS)),
               "derek_entry_policy_v2_rows_in_window": data["derek_v2_rows"],
               "derek_entry_policy_v2": "NOT_MEASURABLE_BLENDED_FAIR_VALUE"},
           "bounded": data["bounded"],
           "horizons_s": list(ED.HORIZONS_S),
           "summary": summary, "faster_processing_evidence": realized,
           "pm_answers": ED.pm_answers(summary, realized),
           "fast_lane": fast_lane(summary, comp),
           "anti_lookahead": PIT.describe(),
           "never": ["places, cancels or sizes an order",
                     "changes a threshold, limit, allowlist or policy",
                     "is read by a decision path",
                     "labels retrospective evidence FORWARD_VALIDATED"]}
    if detail:
        out["opportunities"] = results
    return out


@router.get("/api/command/lab/edge-decay")
async def lab_edge_decay(_auth: str = Depends(require_read),
                         days: float = Query(default=7.0, gt=0, le=14),
                         limit: int = Query(default=600, ge=1, le=1500),
                         detail: int = Query(default=0, ge=0, le=1)) -> dict:
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = %d"
                               % STATEMENT_TIMEOUT_MS)
            return await build(conn, now=time.time(), days=float(days),
                               limit=int(limit), detail=bool(detail))
