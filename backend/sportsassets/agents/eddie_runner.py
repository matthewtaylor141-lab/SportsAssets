"""EDDIE'S SCHEDULED RUNNER: HEARTBEAT, SHADOW ESTIMATES, OUTCOMES, REVIEWS.

Started in the API process beside the other agent loops (api/app.py
lifespan). Every pass:

  1. heartbeats EDDIE (registry: agent_status, agent_runs) as EVALUATING;
  2. reads the recorded execution history once (fill rates, fill times,
     markouts, latency -- bounded);
  3. estimates each new Derek candidate (paper ENTER decisions in the
     window, oldest first, at most MAX_ESTIMATES_PER_PASS) and persists the
     SHADOW estimate (idempotent per decision and estimator version);
  4. measures PREDICTED vs REALIZED execution loss for estimated candidates
     that filled on paper;
  5. assembles the candidate-review workflow (pos_workflow) for the
     estimated candidates and attaches results;
  6. heartbeats the outcome (DECISION_RECORDED when it wrote an estimate,
     IDLE when there was nothing, FAILED when every phase failed) and
     finishes the run, with a service heartbeat `agent_eddie`.

BOUNDED: per-pass caps, a lookback window, a per-phase timeout and a pass
timeout. FAILURE-ISOLATED: a phase that raises is recorded by name and the
others run; nothing here raises into the API process.

NO AUTHORITY: it writes only Eddie's estimate / outcome / workflow records
and his heartbeat, each write transaction declared as EDDIE (the database
then refuses any order, intent, fill, approval or control write). It imports
no order, venue or execution path.

Kill switch: EDDIE_RUNNER_ENABLED=0 (default on).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

from . import eddie as E
from . import pos_workflow as W
from . import registry as R

log = logging.getLogger(__name__)

INTERVAL_S = 300
FIRST_DELAY_S = 75
PASS_TIMEOUT_S = 90
PHASE_TIMEOUT_S = 25
LOOKBACK_S = 2 * 86400
MAX_ESTIMATES_PER_PASS = 25
MAX_REVIEWS_PER_PASS = W.MAX_REVIEWS_PER_PASS
SERVICE = "agent_eddie"


def enabled() -> bool:
    return os.getenv("EDDIE_RUNNER_ENABLED", "1").strip().lower() not in (
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


async def pass_once(conn, *, now: float | None = None) -> dict:
    """ONE BOUNDED PASS. Never raises (a failure is recorded and returned)."""
    at = float(now if now is not None else time.time())
    t0 = time.monotonic()
    run_id = "eddie-run:%s" % uuid.uuid4().hex
    summary: dict = {"run_id": run_id, "estimated": [], "refused": {},
                     "outcomes": [], "reviews": [], "phase_errors": {},
                     "authority": E.AUTHORITY}
    if not await E.schema(conn):
        summary["status"] = E.R_NO_SCHEMA
        return summary
    await R.heartbeat(conn, R.EDDIE, state=R.S_EVALUATING,
                      activity="ESTIMATING_EXECUTION_OF_DEREK_CANDIDATES",
                      now=at, run={"started_at": at},
                      cadence={"target_interval_s": INTERVAL_S})
    await R.start_run(conn, R.EDDIE, run_id, now=at,
                      summary={"phases": ["history", "estimates", "outcomes",
                                          "reviews"]})
    hist = await _phase(summary, "history", E.history_stats(conn, now=at))
    cands = await _phase(summary, "candidates", E.candidates(
        conn, now=at, lookback_s=LOOKBACK_S, limit=MAX_ESTIMATES_PER_PASS))
    for c in (cands or []) if hist is not None else []:
        async def one(c=c):
            est = E.estimate(c, await E.book_for(conn, c), hist, now=at)
            return await E.record_estimate(conn, est)
        got = await _phase(summary, "estimate:%s" % c["decision_id"], one())
        if got and got.get("ok") and got.get("created"):
            summary["estimated"].append({"estimate_id": got["estimate_id"],
                                         "recommendation":
                                             got["recommendation"]})
        elif got and not got.get("ok"):
            summary["refused"][c["decision_id"]] = got.get("refusal")
    outs = await _phase(summary, "outcomes", E.record_outcomes(conn, now=at))
    summary["outcomes"] = (outs or {}).get("recorded", [])
    ids = await _phase(summary, "review_candidates", conn.fetch(
        "SELECT e.decision_id FROM eddie_execution_estimates e "
        " LEFT JOIN pos_candidate_reviews v ON v.decision_id = e.decision_id"
        " WHERE coalesce(v.steps_recorded, 0) < 7 "
        " ORDER BY e.estimated_at DESC LIMIT $1", MAX_REVIEWS_PER_PASS))
    for r in ids or []:
        got = await _phase(summary, "review:%s" % r["decision_id"],
                           W.assemble(conn, r["decision_id"], now=at))
        if got and got.get("ok") and got.get("written"):
            summary["reviews"].append(got["review_id"])
    await _phase(summary, "results", W.attach_results(conn, now=at))
    elapsed = round(time.monotonic() - t0, 3)
    failed_all = hist is None and outs is None and ids is None
    if failed_all:
        state, outcome = R.S_FAILED, "FAILED"
    elif summary["estimated"]:
        state, outcome = R.S_DECISION_RECORDED, "ESTIMATES_RECORDED"
    else:
        state, outcome = R.S_IDLE, "NO_NEW_CANDIDATE"
    activity = ("ESTIMATED %d CANDIDATE(S) (SHADOW)" % len(summary["estimated"])
                if summary["estimated"] else outcome)
    summary.update(status=outcome, elapsed_s=elapsed)
    finished = at + elapsed
    await R.finish_run(conn, R.EDDIE, run_id, outcome=outcome,
                       summary=summary, now=finished)
    await R.heartbeat(
        conn, R.EDDIE, state=state, activity=activity, now=finished,
        run={"finished_at": finished, "elapsed_s": elapsed,
             "error": ",".join("%s:%s" % kv for kv in
                               summary["phase_errors"].items()) or None},
        cadence={"target_interval_s": INTERVAL_S})
    try:
        from .. import db as _db
        await _db.heartbeat(SERVICE, "error" if failed_all else "ok",
                            {"run_id": run_id, "status": outcome,
                             "estimated": len(summary["estimated"]),
                             "phase_errors": summary["phase_errors"],
                             "elapsed_s": elapsed}, con=conn)
    except Exception:                                           # noqa: BLE001
        pass
    return summary


async def run(get_pool, *, interval_s: float = INTERVAL_S,
              first_delay_s: float = FIRST_DELAY_S) -> None:
    """THE LOOP, armed from the API lifespan; never raises except to be
    cancelled."""
    if not enabled():
        log.info("eddie: runner disabled (EDDIE_RUNNER_ENABLED)")
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
            log.warning("eddie: pass failed (%s)", type(exc).__name__)
        await asyncio.sleep(interval_s)
