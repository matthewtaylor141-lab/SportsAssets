"""SCOUT'S SCHEDULED RUNNER: HEARTBEAT, COMPLIANT INGESTION, FROZEN TESTS.

Every pass:

  1. heartbeats SCOUT as EVALUATING;
  2. records the declared sources with their compliance result (once);
  3. registers each declared feature of a COMPLIANT source, freezes its
     forward test and moves it UNDER_TEST (once);
  4. observes the features from the already-ingested fixture_metadata rows
     (no network call; bounded);
  5. freezes new prospective samples (PinnAPI valuations made after the
     freeze and after the feature value was known, outcome unknown);
  6. attaches settled outcomes;
  7. asks the calibration engine (feature_tournament.evaluate -- NOT Scout)
     to evaluate any tournament that reached its predeclared minimum sample;
  8. opens a collaboration-loop finding (EVIDENCE + HYPOTHESIS, routed to
     his default peers) for each feature that has observations;
  9. syncs his DURABLE WORK QUEUE (agents/agent_work.py, migration 234):
     each feature under test is a RESEARCH_QUESTION item, attempted every
     pass (PROGRESSED when its frozen samples grew, else WAITING with the
     count against the predeclared minimum) and completed by the
     evaluator's verdict;
 10. heartbeats the outcome and finishes the run (service `agent_scout`).

Bounded and failure-isolated like Eddie's runner. Writes only Scout's
records (declared as SCOUT, so the database refuses any order / approval /
control write) plus the evaluator's verdict.

Kill switch: SCOUT_RUNNER_ENABLED=0 (default on).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

from . import collaboration_loop as CL
from . import feature_tournament as FT
from . import registry as R
from . import scout as S

log = logging.getLogger(__name__)

INTERVAL_S = 600
FIRST_DELAY_S = 105
PASS_TIMEOUT_S = 90
PHASE_TIMEOUT_S = 25
SERVICE = "agent_scout"


def enabled() -> bool:
    return os.getenv("SCOUT_RUNNER_ENABLED", "1").strip().lower() not in (
        "0", "false", "off", "no")


async def _phase(summary: dict, name: str, coro):
    try:
        async with asyncio.timeout(PHASE_TIMEOUT_S):
            return await coro
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        summary["phase_errors"][name] = type(exc).__name__
        return None


async def open_feature_findings(conn, *, now: float) -> list:
    """Scout's loop findings: one per feature with observations, EVIDENCE
    (the feature + an observation) and HYPOTHESIS (its predeclared
    hypothesis, citing the frozen tournament). Idempotent."""
    out = []
    for f in await conn.fetch(
            "SELECT f.feature_id, f.feature, f.predeclared_hypothesis, "
            "       f.proposed_at, t.tournament_id, (SELECT observation_id "
            "       FROM scout_feature_observations o WHERE o.feature_id = "
            "       f.feature_id ORDER BY source_timestamp LIMIT 1) AS obs "
            "  FROM scout_features f JOIN scout_feature_tournaments t USING "
            "       (feature_id) WHERE f.state = 'UNDER_TEST'"):
        if not f["obs"]:
            continue
        refs = [{"kind": "scout_features", "id": f["feature_id"]},
                {"kind": "scout_feature_observations", "id": f["obs"]}]
        got = await CL.open_finding(
            conn, proposer=R.SCOUT, title=("Feature under test: %s"
                                           % f["feature"])[:300],
            statement=("Scout registered %s from a compliant source and "
                       "froze its forward test %s. Routed to %s." % (
                           f["feature"], f["tournament_id"],
                           ", ".join(CL.PEER_ROUTING["SCOUT"]))),
            evidence_refs=refs, evidence_window_end=now, at=now)
        if got.get("ok") and await conn.fetchval(
                "SELECT stage FROM agent_findings WHERE finding_id=$1",
                got["finding_id"]) == CL.EVIDENCE:
            # 1 ms after the evidence stage: timestamps are stored to the
            # microsecond, and a stage may not predate the one before it
            h = await CL.record_hypothesis(
                conn, got["finding_id"], actor=R.SCOUT,
                hypothesis=f["predeclared_hypothesis"],
                evidence_refs=refs + [{"kind": "scout_feature_tournaments",
                                       "id": f["tournament_id"]}],
                at=now + 0.001)
            if h.get("ok"):
                out.append(got["finding_id"])
    return out


async def pass_once(conn, *, now: float | None = None) -> dict:
    at = float(now if now is not None else time.time())
    t0 = time.monotonic()
    run_id = "scout-run:%s" % uuid.uuid4().hex
    summary: dict = {"run_id": run_id, "phase_errors": {},
                     "authority": S.AUTHORITY}
    if not await S.schema(conn):
        summary["status"] = S.R_NO_SCHEMA
        return summary
    await R.heartbeat(conn, R.SCOUT, state=R.S_EVALUATING,
                      activity="OBSERVING_COMPLIANT_SOURCES", now=at,
                      run={"started_at": at},
                      cadence={"target_interval_s": INTERVAL_S})
    await R.start_run(conn, R.SCOUT, run_id, now=at,
                      summary={"sources": sorted(S.SOURCES),
                               "features": sorted(S.FEATURES)})
    summary["sources"] = await _phase(summary, "sources",
                                      S.register_sources(conn, now=at))
    summary["features_registered"] = await _phase(
        summary, "features", S.register_features(conn, now=at))
    summary["ingest"] = await _phase(summary, "ingest", S.ingest(conn,
                                                                 now=at))
    summary["samples"] = await _phase(summary, "samples",
                                      S.freeze_samples(conn, now=at))
    summary["outcomes"] = await _phase(summary, "outcomes",
                                       S.attach_outcomes(conn))
    verdicts = {}
    for t in await _phase(summary, "tournaments", conn.fetch(
            "SELECT tournament_id FROM scout_feature_tournaments WHERE "
            " verdict IS NULL")) or []:
        got = await _phase(summary, "evaluate:%s" % t["tournament_id"],
                           FT.evaluate(conn, t["tournament_id"], now=at))
        if got:
            verdicts[t["tournament_id"]] = got.get("verdict") or got.get(
                "reason")
    summary["evaluations"] = verdicts
    summary["findings"] = await _phase(summary, "findings",
                                       open_feature_findings(conn, now=at))
    from . import agent_work as AW

    async def queue():
        return await AW.sync_for(conn, "scout_runner", now=at, attempts={
            AW.K_RESEARCH: await AW.research_attempts(conn, now=at)})
    summary["work_queue"] = await _phase(summary, "work_queue", queue())
    elapsed = round(time.monotonic() - t0, 3)
    wrote = bool((summary.get("ingest") or {}).get("observed")) or bool(
        (summary.get("samples") or {}).get("frozen"))
    failed_all = summary.get("sources") is None and summary.get(
        "ingest") is None
    if failed_all:
        state, outcome = R.S_FAILED, "FAILED"
    elif wrote:
        state, outcome = R.S_DECISION_RECORDED, "OBSERVATIONS_RECORDED"
    else:
        state, outcome = R.S_WAITING_FOR_EVIDENCE, "NO_NEW_COMPLIANT_EVIDENCE"
    summary.update(status=outcome, elapsed_s=elapsed)
    finished = at + elapsed
    await R.finish_run(conn, R.SCOUT, run_id, outcome=outcome,
                       summary=summary, now=finished)
    await R.heartbeat(
        conn, R.SCOUT, state=state, activity=outcome, now=finished,
        run={"finished_at": finished, "elapsed_s": elapsed,
             "error": ",".join("%s:%s" % kv for kv in
                               summary["phase_errors"].items()) or None},
        cadence={"target_interval_s": INTERVAL_S})
    try:
        from .. import db as _db
        await _db.heartbeat(SERVICE, "error" if failed_all else "ok",
                            {"run_id": run_id, "status": outcome,
                             "phase_errors": summary["phase_errors"],
                             "elapsed_s": elapsed}, con=conn)
    except Exception:                                           # noqa: BLE001
        pass
    return summary


async def run(get_pool, *, interval_s: float = INTERVAL_S,
              first_delay_s: float = FIRST_DELAY_S) -> None:
    if not enabled():
        log.info("scout: runner disabled (SCOUT_RUNNER_ENABLED)")
        return
    await asyncio.sleep(first_delay_s)
    while True:
        try:
            pool = await get_pool()
            async with asyncio.timeout(PASS_TIMEOUT_S):
                async with pool.acquire() as conn:
                    await pass_once(conn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            log.warning("scout: pass failed (%s)", type(exc).__name__)
        await asyncio.sleep(interval_s)
