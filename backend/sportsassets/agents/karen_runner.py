"""KAREN'S SCHEDULED RUNNER: HEARTBEAT + GROUNDED CHALLENGES FROM REAL RECORDS.

Started in the API process beside the other agent loops (api/app.py
lifespan). Every pass:

  1. heartbeats KAREN (registry: agent_status, agent_runs) as EVALUATING;
  2. runs each rule-based DETECTOR over the authoritative records -- each
     detector is one bounded, read-only query that yields candidate
     challenges, every one citing the evidence record(s) it rests on;
  3. opens each candidate through karen.open_challenge, which re-checks the
     grounding rule (every cited record must exist) and is idempotent on
     (detector, target record) -- a restart or a second process writes
     nothing new;
  4. heartbeats the outcome (DECISION_RECORDED when it opened a challenge,
     IDLE when it found nothing, FAILED when every detector failed) and
     finishes the run, with a service heartbeat `agent_karen`.

BOUNDED. At most MAX_NEW_PER_DETECTOR new challenges per detector and
MAX_NEW_PER_PASS per pass; a detector holding MAX_OPEN_PER_DETECTOR
unanswered challenges raises no more until they are answered; each query
looks back LOOKBACK_S only; each detector runs under its own timeout.

FAILURE-ISOLATED. A detector that raises is recorded by name and the others
run; a failed pass is logged and the loop sleeps and tries again; nothing
here can raise into the API process.

NO LLM AND NO UNGROUNDED CHALLENGE. The detectors are rules over records.
Their claim text quotes the record's own fields; nothing is inferred beyond
them. The runner writes only Karen's challenge records and her heartbeat;
it imports no order, venue or execution path.

Off switch: KAREN_RUNNER_ENABLED=0 (default on).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

from . import karen as K
from . import registry as R

log = logging.getLogger(__name__)

INTERVAL_S = 300
FIRST_DELAY_S = 60
PASS_TIMEOUT_S = 90
DETECTOR_TIMEOUT_S = 10
LOOKBACK_S = 7 * 86400
MAX_NEW_PER_DETECTOR = 3
MAX_NEW_PER_PASS = 10
MAX_OPEN_PER_DETECTOR = 25
#: An Audrey discrepancy with no improvement task after this long is "left
#: open".
AUDIT_OPEN_AFTER_S = 86400
SERVICE = "agent_karen"


def enabled() -> bool:
    return os.getenv("KAREN_RUNNER_ENABLED", "1").strip().lower() not in (
        "0", "false", "off", "no")


def _ep(v):
    return v.timestamp() if hasattr(v, "timestamp") else v


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v if v is not None else {}


_NOT_YET = ("NOT EXISTS (SELECT 1 FROM karen_challenges k WHERE "
            "k.detector=$1 AND k.target_kind=$2 AND k.target_id=%s)")


# ═════════════════════════════════════════════════════════════════════
# DETECTORS: one bounded read each; every candidate cites its evidence
# ═════════════════════════════════════════════════════════════════════

async def detect_decision_without_evidence(conn, now: float, limit: int):
    """A Derek / Xavier / Audrey decision indexed with NO evidence
    reference: the decision index promises links to the authoritative
    records and this one carries none."""
    det, kind = "DECISION_WITHOUT_EVIDENCE", "agent_decisions"
    rows = await conn.fetch(
        "SELECT decision_ref, agent_id, kind, subject, verdict, decided_at "
        "  FROM agent_decisions d "
        " WHERE d.agent_id = ANY($3::text[]) "
        "   AND jsonb_array_length(d.evidence_refs) = 0 "
        "   AND d.decided_at BETWEEN to_timestamp($4) AND to_timestamp($5) "
        "   AND " + _NOT_YET % "d.decision_ref" +
        " ORDER BY d.decided_at, d.decision_ref LIMIT $6",
        det, kind, list(K.TARGETS), now - LOOKBACK_S, now, limit)
    return [{
        "detector": det, "target_agent": r["agent_id"], "target_kind": kind,
        "target_id": r["decision_ref"], "severity": "MEDIUM",
        "record_at": _ep(r["decided_at"]),
        "claim": ("Decision %s (%s, verdict %s, subject %s) is indexed with "
                  "no evidence reference: nothing links it to the "
                  "authoritative records it rests on."
                  % (r["decision_ref"], r["kind"], r["verdict"] or "none",
                     r["subject"] or "none")),
        "evidence_refs": [{"kind": kind, "id": r["decision_ref"]}],
        "body": {"rule": "agent_decisions.evidence_refs is an empty array"},
    } for r in rows]


async def detect_entry_without_probability(conn, now: float, limit: int):
    """A paper ENTER verdict recorded with neither a Pinnacle nor an
    internal probability: an entry with no probability evidence at all."""
    det, kind = "ENTRY_WITHOUT_PROBABILITY", "paper_decisions"
    rows = await conn.fetch(
        "SELECT decision_id, account_id, decided_at, us_market_slug, "
        "       holding_side, strategy "
        "  FROM paper_decisions p "
        " WHERE p.verdict = 'ENTER' AND p.p_pinnacle IS NULL "
        "   AND p.p_internal IS NULL AND p.p_blended IS NULL "
        "   AND p.decided_at BETWEEN to_timestamp($3) AND to_timestamp($4) "
        "   AND " + _NOT_YET % "p.decision_id" +
        " ORDER BY p.decided_at, p.decision_id LIMIT $5",
        det, kind, now - LOOKBACK_S, now, limit)
    return [{
        "detector": det, "target_agent": R.DEREK, "target_kind": kind,
        "target_id": r["decision_id"], "severity": "HIGH",
        "record_at": _ep(r["decided_at"]), "account_id": r["account_id"],
        "claim": ("Paper decision %s entered %s %s (strategy %s) with no "
                  "Pinnacle, internal or blended probability recorded."
                  % (r["decision_id"], r["us_market_slug"],
                     r["holding_side"], r["strategy"])),
        "evidence_refs": [{"kind": kind, "id": r["decision_id"]}],
        "body": {"rule": "verdict ENTER and p_pinnacle, p_internal, "
                         "p_blended all NULL"},
    } for r in rows]


async def detect_hold_on_stale_probability(conn, now: float, limit: int):
    """A Xavier review that recommended HOLD while its own recorded
    probability was stale (measure.stale = true, or its age beyond its own
    recorded limit)."""
    det, kind = "HOLD_ON_STALE_PROBABILITY", "paper_xavier_reviews"
    rows = await conn.fetch(
        "SELECT review_id, account_id, group_id, reviewed_at, "
        "       recommendation, measure "
        "  FROM paper_xavier_reviews x "
        " WHERE x.recommendation = 'HOLD' "
        "   AND (x.measure->>'stale' = 'true' OR ("
        "        jsonb_typeof(x.measure->'probability_age_s') = 'number' "
        "    AND jsonb_typeof(x.measure->'probability_limit_s') = 'number' "
        "    AND (x.measure->>'probability_age_s')::numeric > "
        "        (x.measure->>'probability_limit_s')::numeric)) "
        "   AND x.reviewed_at BETWEEN to_timestamp($3) AND to_timestamp($4) "
        "   AND " + _NOT_YET % "x.review_id" +
        " ORDER BY x.reviewed_at, x.review_id LIMIT $5",
        det, kind, now - LOOKBACK_S, now, limit)
    out = []
    for r in rows:
        m = _j(r["measure"])
        out.append({
            "detector": det, "target_agent": R.XAVIER, "target_kind": kind,
            "target_id": r["review_id"], "severity": "HIGH",
            "record_at": _ep(r["reviewed_at"]), "account_id": r["account_id"],
            "claim": ("Review %s of group %s recommended HOLD on a stale "
                      "probability (stale=%s, age %s s against a %s s "
                      "limit, as the review itself records)."
                      % (r["review_id"], r["group_id"], m.get("stale"),
                         m.get("probability_age_s"),
                         m.get("probability_limit_s"))),
            "evidence_refs": [{"kind": kind, "id": r["review_id"]}],
            "body": {"rule": "recommendation HOLD with measure.stale true "
                             "or probability_age_s > probability_limit_s"},
        })
    return out


async def detect_audit_discrepancy_left_open(conn, now: float, limit: int):
    """An Audrey WARNING / ERROR / CRITICAL finding with no improvement task
    after AUDIT_OPEN_AFTER_S: a discrepancy found and left open."""
    det, kind = "AUDIT_DISCREPANCY_LEFT_OPEN", "paper_audrey_findings"
    rows = await conn.fetch(
        "SELECT finding_id, account_id, found_at, kind, severity, subject "
        "  FROM paper_audrey_findings a "
        " WHERE upper(a.severity) IN ('WARNING', 'ERROR', 'CRITICAL', "
        "                             'HIGH') "
        "   AND a.improvement_task_id IS NULL "
        "   AND a.found_at BETWEEN to_timestamp($3) AND to_timestamp($4) "
        "   AND " + _NOT_YET % "a.finding_id" +
        " ORDER BY a.found_at, a.finding_id LIMIT $5",
        det, kind, now - LOOKBACK_S, now - AUDIT_OPEN_AFTER_S, limit)
    return [{
        "detector": det, "target_agent": R.AUDREY, "target_kind": kind,
        "target_id": r["finding_id"],
        "severity": "HIGH" if str(r["severity"]).upper() in (
            "CRITICAL", "ERROR") else "MEDIUM",
        "record_at": _ep(r["found_at"]), "account_id": r["account_id"],
        "claim": ("Audit finding %s (%s %s on %s) has had no improvement "
                  "task for more than %d h: the discrepancy is recorded but "
                  "nothing is assigned to resolve it."
                  % (r["finding_id"], r["severity"], r["kind"],
                     r["subject"] or "no subject",
                     AUDIT_OPEN_AFTER_S // 3600)),
        "evidence_refs": [{"kind": kind, "id": r["finding_id"]}],
        "body": {"rule": "severity WARNING+ and improvement_task_id NULL "
                         "after %d s" % AUDIT_OPEN_AFTER_S},
    } for r in rows]


async def detect_reconciliation_discrepancy_open(conn, now: float,
                                                 limit: int):
    """A small-live reconciliation still in DISCREPANCY: Audrey audits the
    book against the venue and this discrepancy is unresolved."""
    det, kind = "RECONCILIATION_DISCREPANCY_OPEN", "smalllive_reconciliations"
    if await conn.fetchval(
            "SELECT to_regclass('smalllive_reconciliations')") is None:
        return []
    rows = await conn.fetch(
        "SELECT group_id, venue, reconciled_at, discrepancies "
        "  FROM smalllive_reconciliations s "
        " WHERE s.status = 'DISCREPANCY' "
        "   AND s.reconciled_at BETWEEN to_timestamp($3) "
        "                           AND to_timestamp($4) "
        "   AND " + _NOT_YET % "s.group_id" +
        " ORDER BY s.reconciled_at, s.group_id LIMIT $5",
        det, kind, now - LOOKBACK_S, now - 3600, limit)
    return [{
        "detector": det, "target_agent": R.AUDREY, "target_kind": kind,
        "target_id": r["group_id"], "severity": "HIGH",
        "record_at": _ep(r["reconciled_at"]),
        "claim": ("Reconciliation of group %s on %s has stood in "
                  "DISCREPANCY for over an hour (%d discrepancy item(s) "
                  "recorded) with no matched reconciliation since."
                  % (r["group_id"], r["venue"],
                     len(_j(r["discrepancies"]) or []))),
        "evidence_refs": [{"kind": kind, "id": r["group_id"]}],
        "body": {"rule": "status DISCREPANCY for more than 3600 s"},
    } for r in rows]


STALE_SUBMIT_REFUSALS = ("DECISION_STALE_AT_ACTUAL_SUBMIT",
                         "EXECUTABLE_BOOK_STALE_AT_ACTUAL_SUBMIT",
                         "ADMISSION_IDENTITY_NOT_EXACT")


async def detect_admission_refusal(conn, now: float, limit: int):
    """An execution intent refused at admission because Derek's decision
    went stale before submission, or named an inexact contract identity:
    the admission rail caught what the decision should not have sent."""
    det, kind = "ADMISSION_REFUSED_DECISION", "execution_intents"
    rows = await conn.fetch(
        "SELECT intent_id, decision_id, decided_at, created_at, "
        "       actual_refusal, us_market_slug "
        "  FROM execution_intents e "
        " WHERE e.actual_state = 'REFUSED' "
        "   AND e.actual_refusal = ANY($3::text[]) "
        "   AND e.created_at BETWEEN to_timestamp($4) AND to_timestamp($5) "
        "   AND " + _NOT_YET % "e.intent_id" +
        " ORDER BY e.created_at, e.intent_id LIMIT $6",
        det, kind, list(STALE_SUBMIT_REFUSALS), now - LOOKBACK_S, now, limit)
    out = []
    for r in rows:
        lag = None
        if r["decided_at"] is not None and r["created_at"] is not None:
            lag = round(_ep(r["created_at"]) - _ep(r["decided_at"]), 3)
        out.append({
            "detector": det, "target_agent": R.DEREK, "target_kind": kind,
            "target_id": r["intent_id"], "severity": "MEDIUM",
            "record_at": _ep(r["decided_at"] or r["created_at"]),
            "claim": ("Intent %s (decision %s, %s) was refused at admission "
                      "with %s; %s s passed between the decision and the "
                      "intent." % (r["intent_id"], r["decision_id"],
                                   r["us_market_slug"], r["actual_refusal"],
                                   "unknown" if lag is None else lag)),
            "evidence_refs": [{"kind": kind, "id": r["intent_id"]}],
            "body": {"rule": "actual_state REFUSED with refusal in %s"
                             % ", ".join(STALE_SUBMIT_REFUSALS),
                     "decision_to_intent_s": lag},
        })
    return out


async def detect_finding_on_upheld_defect(conn, now: float, limit: int):
    """THE LOOP. A finding at its HYPOTHESIS stage that rests on a record
    Karen has already had UPHELD as defective: Karen records the loop's
    PEER_CHALLENGE as REFUTED (blocking -- later assessed for false block),
    citing the upheld challenge and the record. Candidates carry
    `loop: True` and go through karen.challenge_finding."""
    if await conn.fetchval("SELECT to_regclass('agent_findings')") is None:
        return []
    rows = await conn.fetch(
        "SELECT f.finding_id, f.proposer, k.challenge_id, k.target_kind, "
        "       k.target_id, k.claim "
        "  FROM agent_findings f "
        "  JOIN agent_finding_stages h ON h.finding_id = f.finding_id "
        "                             AND h.seq = 2 "
        "  JOIN karen_challenges k ON k.state = 'UPHELD' "
        "   AND EXISTS (SELECT 1 FROM jsonb_array_elements("
        "                   f.evidence_refs || h.evidence_refs) e "
        "                WHERE e->>'kind' = k.target_kind "
        "                  AND e->>'id' = k.target_id) "
        " WHERE f.stage = 'HYPOTHESIS' AND f.proposer = ANY($1::text[]) "
        "   AND NOT EXISTS (SELECT 1 FROM karen_challenges c "
        "                    WHERE c.finding_id = f.finding_id) "
        " ORDER BY f.updated_at, f.finding_id LIMIT $2",
        list(K.TARGETS), limit)
    seen, out = set(), []
    for r in rows:
        if r["finding_id"] in seen:
            continue
        seen.add(r["finding_id"])
        out.append({
            "loop": True, "finding_id": r["finding_id"],
            "detector": "FINDING_RESTS_ON_UPHELD_DEFECT", "outcome": "REFUTED",
            "severity": "HIGH",
            "claim": ("Finding %s rests on %s %s, which challenge %s showed "
                      "to be defective (UPHELD): the hypothesis cannot stand "
                      "on that record." % (r["finding_id"], r["target_kind"],
                                           r["target_id"],
                                           r["challenge_id"])),
            "evidence_refs": [{"kind": "karen_challenges",
                               "id": r["challenge_id"]},
                              {"kind": r["target_kind"],
                               "id": r["target_id"]}],
        })
    return out


DETECTORS = (
    ("DECISION_WITHOUT_EVIDENCE", detect_decision_without_evidence),
    ("ENTRY_WITHOUT_PROBABILITY", detect_entry_without_probability),
    ("HOLD_ON_STALE_PROBABILITY", detect_hold_on_stale_probability),
    ("AUDIT_DISCREPANCY_LEFT_OPEN", detect_audit_discrepancy_left_open),
    ("RECONCILIATION_DISCREPANCY_OPEN",
     detect_reconciliation_discrepancy_open),
    ("ADMISSION_REFUSED_DECISION", detect_admission_refusal),
    ("FINDING_RESTS_ON_UPHELD_DEFECT", detect_finding_on_upheld_defect),
)


# ═════════════════════════════════════════════════════════════════════
# ONE PASS
# ═════════════════════════════════════════════════════════════════════

async def _open_count(conn, detector: str) -> int:
    return int(await conn.fetchval(
        "SELECT count(*) FROM karen_challenges WHERE detector=$1 "
        "   AND state IN ('OPEN', 'RESPONDED')", detector) or 0)


async def _open_candidate(conn, c: dict, at: float) -> dict:
    if c.get("loop"):
        return await K.challenge_finding(
            conn, c["finding_id"], claim=c["claim"], outcome=c["outcome"],
            evidence_refs=c["evidence_refs"], at=at,
            severity=c.get("severity", "HIGH"), detector=c["detector"])
    return await K.open_challenge(
        conn, target_agent=c["target_agent"], target_kind=c["target_kind"],
        target_id=c["target_id"], detector=c["detector"], claim=c["claim"],
        severity=c["severity"], evidence_refs=c["evidence_refs"],
        record_at=min(float(c["record_at"]), at), at=at,
        account_id=c.get("account_id"), body=c.get("body"))


async def pass_once(conn, *, now: float | None = None,
                    detectors=DETECTORS) -> dict:
    """ONE BOUNDED PASS. Never raises (a failure is recorded and returned).
    """
    at = float(now if now is not None else time.time())
    t0 = time.monotonic()
    run_id = "karen-run:%s" % uuid.uuid4().hex
    summary: dict = {"run_id": run_id, "opened": [], "refused": {},
                     "detector_errors": {}, "candidates": 0,
                     "authority": "NONE"}
    if not await K.schema(conn):
        summary["status"] = K.R_NO_SCHEMA
        return summary
    await R.heartbeat(conn, K.KAREN, state=R.S_EVALUATING,
                      activity="RUNNING_CHALLENGE_DETECTORS", now=at,
                      run={"started_at": at},
                      cadence={"target_interval_s": INTERVAL_S})
    await R.start_run(conn, K.KAREN, run_id, now=at,
                      summary={"detectors": [d for d, _ in detectors]})
    budget = MAX_NEW_PER_PASS
    for name, fn in detectors:
        if budget <= 0:
            break
        try:
            async with asyncio.timeout(DETECTOR_TIMEOUT_S):
                if await _open_count(conn, name) >= MAX_OPEN_PER_DETECTOR:
                    summary["refused"][name] = "OPEN_CHALLENGE_CAP_REACHED"
                    continue
                cands = await fn(conn, at, MAX_NEW_PER_DETECTOR)
                summary["candidates"] += len(cands)
                for c in cands[:min(MAX_NEW_PER_DETECTOR, budget)]:
                    got = await _open_candidate(conn, c, at)
                    if got.get("ok") and got.get("created"):
                        budget -= 1
                        summary["opened"].append(got["challenge_id"])
                    elif not got.get("ok"):
                        summary["refused"].setdefault(name, got["refusal"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            summary["detector_errors"][name] = type(exc).__name__
    elapsed = round(time.monotonic() - t0, 3)
    failed_all = len(summary["detector_errors"]) == len(detectors) > 0
    if failed_all:
        state, outcome = R.S_FAILED, "FAILED"
    elif summary["opened"]:
        state, outcome = R.S_DECISION_RECORDED, "CHALLENGES_OPENED"
    else:
        state, outcome = R.S_IDLE, "NOTHING_TO_CHALLENGE"
    activity = ("OPENED %d CHALLENGE(S)" % len(summary["opened"])
                if summary["opened"] else outcome)
    summary.update(status=outcome, elapsed_s=elapsed)
    finished = at + elapsed
    await R.finish_run(conn, K.KAREN, run_id, outcome=outcome,
                       summary=summary, now=finished)
    await R.heartbeat(
        conn, K.KAREN, state=state, activity=activity, now=finished,
        run={"finished_at": finished, "elapsed_s": elapsed,
             "error": ",".join("%s:%s" % kv for kv in
                               summary["detector_errors"].items()) or None},
        cadence={"target_interval_s": INTERVAL_S})
    try:
        from .. import db as _db
        await _db.heartbeat(SERVICE, "error" if failed_all else "ok",
                            {"run_id": run_id, "status": outcome,
                             "opened": len(summary["opened"]),
                             "detector_errors": summary["detector_errors"],
                             "elapsed_s": elapsed}, con=conn)
    except Exception:                                           # noqa: BLE001
        pass
    return summary


async def run(get_pool, *, interval_s: float = INTERVAL_S,
              first_delay_s: float = FIRST_DELAY_S) -> None:
    """THE LOOP, armed from the API lifespan. Bounded per pass, isolated
    from its caller: it never raises except to be cancelled."""
    if not enabled():
        log.info("karen: runner disabled (KAREN_RUNNER_ENABLED)")
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
            log.warning("karen: pass failed (%s)", type(exc).__name__)
        await asyncio.sleep(interval_s)

