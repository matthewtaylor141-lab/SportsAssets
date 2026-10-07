"""ADRIANA'S SCHEDULED RUNNER: HEARTBEAT, CENSUS, RECORD, COLLABORATE.

Every pass:

  1. heartbeats ADRIANA as EVALUATING and starts a run (agent_runs);
  2. reads the newest recorded book of every market in the window
     (adriana.read_rows -- read only, no venue call);
  3. runs the pure census (adriana.census -> adriana_arb);
  4. records the pass, its opportunities and its ranked refusals in one
     transaction declared as ADRIANA (the database refuses any order,
     intent, fill, approval or control write in it);
  5. collaborates through records (hand-offs, review requests, blocker
     tasks);
  6. heartbeats the outcome and finishes the run (service `agent_adriana`).

FAIL CLOSED: no recorded book in the window -> WAITING_FOR_EVIDENCE with
the reason and a NO_EVIDENCE scan row (counts zero, why named); a phase that
raises -> its exception type in the run summary and the heartbeat; the
schema absent -> nothing written but the heartbeat. Bounded per phase and
per pass; never raises into the API process.

Kill switch: ADRIANA_RUNNER_ENABLED=0 (default on).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from . import adriana as AD
from . import registry as R
from ..redteam import sentinel as SENT

log = logging.getLogger(__name__)

INTERVAL_S = 300
FIRST_DELAY_S = 135
PASS_TIMEOUT_S = 120
PHASE_TIMEOUT_S = 45
SERVICE = "agent_adriana"


def enabled() -> bool:
    return os.getenv("ADRIANA_RUNNER_ENABLED", "1").strip().lower() not in (
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


async def _claims_census(conn, at: float) -> dict:
    from .. import canonical_claims_db as KCDB
    return await KCDB.claims_census(conn, now=at)


async def _sentinel(conn, result: dict, scan_id: str, at: float) -> dict:
    """THE TWO-LEG SENTINEL (red team), SHADOW: every opportunity
    revalidated immediately -- books, rules fingerprint, economics, venue
    capital, both venues' health -- and its pair receipt appended. Adriana
    has no submit authority: a pair is never ARMED, never execution-locked."""
    vh = await SENT.latest_venue_health(conn, now=at)
    return await SENT.shadow_pass(conn, result, scan_id=scan_id, now=at,
                                  venue_health=vh,
                                  sha=SENT.running_sha())


async def pass_once(conn, *, now: float | None = None) -> dict:
    at = float(now if now is not None else time.time())
    t0 = time.monotonic()
    run_id = "adriana-run:%s" % uuid.uuid4().hex
    summary: dict = {"run_id": run_id, "phase_errors": {},
                     "authority": AD.AUTHORITY, "version": AD.VERSION}
    await R.heartbeat(conn, R.ADRIANA, state=R.S_EVALUATING,
                      activity="ARBITRAGE_CENSUS_OVER_RECORDED_BOOKS", now=at,
                      run={"started_at": at},
                      cadence={"target_interval_s": INTERVAL_S})
    if not await AD.schema(conn):
        summary["status"] = AD.R_NO_SCHEMA
        await R.heartbeat(conn, R.ADRIANA, state=R.S_BLOCKED,
                          activity=AD.R_NO_SCHEMA, now=at,
                          cadence={"target_interval_s": INTERVAL_S})
        return summary
    await R.start_run(conn, R.ADRIANA, run_id, now=at,
                      summary={"window_s": AD.BOOK_WINDOW_S})
    rows = await _phase(summary, "read", AD.read_rows(conn, now=at))
    result = None
    if rows is not None:
        try:
            result = AD.census(rows, datetime.fromtimestamp(at, timezone.utc))
        except Exception as exc:                                # noqa: BLE001
            summary["phase_errors"]["census"] = type(exc).__name__
    scan_id = "adr-scan-%d" % int(at * 1000)
    rec = None
    if result is not None:
        status, why = "OK", None
        if not result["census"].get("markets_read"):
            status = "NO_EVIDENCE"
            why = ("NO_RECORDED_BOOK_OF_A_SUPPORTED_FAMILY_IN_THE_LAST_%dS"
                   % AD.BOOK_WINDOW_S)
        elif summary["phase_errors"]:
            status, why = "PARTIAL", ",".join(summary["phase_errors"])
        # the rules fingerprint each opportunity was decided against
        # (red team settlement guard), stamped BEFORE it is recorded
        await _phase(summary, "certify_rules", SENT.certify_rules(conn,
                                                                  result))
        rec = await _phase(summary, "record", AD.record(
            conn, result, started=at, finished=at + (time.monotonic() - t0),
            scan_id=scan_id, status=status, why=why))
        if rec and rec.get("created") and result.get("opportunities"):
            summary["sentinel"] = await _phase(
                summary, "sentinel", _sentinel(conn, result, scan_id, at))
        if rec and rec.get("created"):
            summary["collaboration"] = await _phase(
                summary, "collaborate", AD.collaborate(
                    conn, scan_id=scan_id,
                    opportunity_ids=rec.get("opportunity_ids") or [],
                    result=result, now=at))
        c = result["census"]
        summary.update(scan_id=scan_id, markets_read=c.get("markets_read"),
                       structures=len(result["opportunities"])
                       + len(result["refusals"]),
                       opportunities=len(result["opportunities"]),
                       refusals=len(result["refusals"]),
                       conditional=c.get("conditional_candidates"))
    # THE CLAIM-FIRST SCAN (Kalshi Canonical Venue V1): canonical claim
    # classes over the persisted Kalshi / PMUS evidence, every complementary
    # pair -- same venue or cross venue -- through the same engine, recorded
    # as her own second scan (SHADOW, 265). A failure here is a named phase
    # error; the recorded-books census above is unaffected.
    claims = await _phase(summary, "claims", _claims_census(conn, at))
    cn = (len(claims["opportunities"]) + len(claims["refusals"])
          if claims is not None else 0)
    if cn:
        # recorded only when a complementary claim pair was evaluated: an
        # empty claim pass is in the summary, never an empty scan row
        cscan = "adr-claims-%d" % int(at * 1000)
        crec = await _phase(summary, "claims_record", AD.record(
            conn, claims, started=at, finished=at + (time.monotonic() - t0),
            scan_id=cscan, status="OK", why=None))
        if crec and crec.get("created") and claims.get("opportunities"):
            summary["claims_sentinel"] = await _phase(
                summary, "claims_sentinel", _sentinel(conn, claims, cscan,
                                                      at))
        cc = claims["census"]
        summary["claims"] = {
            "scan": (crec or {}).get("scan_id"),
            "structures": cn, "opportunities": len(claims["opportunities"]),
            "refusals": len(claims["refusals"]),
            "by_topology": cc.get("by_topology"),
            "pairs_not_complementary": cc.get("pairs_not_complementary")}
    elapsed = round(time.monotonic() - t0, 3)
    if result is None or (rec is None and "record" in summary["phase_errors"]):
        state, outcome = R.S_FAILED, "FAILED"
    elif not result["census"].get("markets_read"):
        state, outcome = R.S_WAITING_FOR_EVIDENCE, "NO_RECORDED_BOOK_IN_WINDOW"
    else:
        state, outcome = R.S_DECISION_RECORDED, "CENSUS_RECORDED"
    summary.update(status=outcome, elapsed_s=elapsed)
    finished = at + elapsed
    await R.finish_run(conn, R.ADRIANA, run_id, outcome=outcome,
                       summary=summary, now=finished)
    await R.heartbeat(
        conn, R.ADRIANA, state=state, activity=outcome, now=finished,
        run={"finished_at": finished, "elapsed_s": elapsed,
             "error": ",".join("%s:%s" % kv for kv in
                               summary["phase_errors"].items()) or None},
        cadence={"target_interval_s": INTERVAL_S})
    try:
        from .. import db as _db
        await _db.heartbeat(SERVICE, "error" if state == R.S_FAILED else "ok",
                            {"run_id": run_id, "status": outcome,
                             "scan_id": summary.get("scan_id"),
                             "phase_errors": summary["phase_errors"],
                             "elapsed_s": elapsed}, con=conn)
    except Exception:                                           # noqa: BLE001
        pass
    return summary


async def run(get_pool, *, interval_s: float = INTERVAL_S,
              first_delay_s: float = FIRST_DELAY_S) -> None:
    if not enabled():
        log.info("adriana: runner disabled (ADRIANA_RUNNER_ENABLED)")
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
            log.warning("adriana: pass failed (%s)", type(exc).__name__)
        await asyncio.sleep(interval_s)
