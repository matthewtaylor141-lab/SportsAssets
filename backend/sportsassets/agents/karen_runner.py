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
  4. enqueues what it found and did not open (the open-challenge cap, the
     per-pass budget) as CHALLENGE_INVESTIGATION items in her DURABLE WORK
     QUEUE (agents/agent_work.py, migration 301), completed by the challenge
     that later opens them or failed when the same rule no longer holds;
  5. heartbeats the outcome (DECISION_RECORDED when it opened a challenge,
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
from . import karen_evidence as KE
from . import registry as R

log = logging.getLogger(__name__)

INTERVAL_S = 300
FIRST_DELAY_S = 60
PASS_TIMEOUT_S = 90
DETECTOR_TIMEOUT_S = 10
LOOKBACK_S = 7 * 86400
MAX_NEW_PER_DETECTOR = 3
MAX_NEW_PER_PASS = 12
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
            "k.detector=$1 AND k.target_kind=$2 AND k.target_id=(%s)::text)")

_OPERATING = "ARRAY['DEREK','XAVIER','AUDREY']"


# ═════════════════════════════════════════════════════════════════════
# THE RULES. Each detector's defect is ONE predicate over one table (alias
# t). The detector selects the records the predicate holds for; the peer
# responder and the independent evaluator re-apply THE SAME predicate to
# the challenged record (`rule_holds`), so "the record really is defective
# under the same rule" is literally the same SQL.
# ═════════════════════════════════════════════════════════════════════

STALE_SUBMIT_REFUSALS = ("DECISION_STALE_AT_ACTUAL_SUBMIT",
                         "EXECUTABLE_BOOK_STALE_AT_ACTUAL_SUBMIT",
                         "ADMISSION_IDENTITY_NOT_EXACT")

_NO_REFS = ("NOT coalesce(jsonb_typeof(%(c)s->'evidence_refs') = 'array' "
            "AND jsonb_array_length(%(c)s->'evidence_refs') > 0, false)")

RULES = {
    # detector: (table, key expression, predicate)
    "DECISION_WITHOUT_EVIDENCE": (
        "agent_decisions", "t.decision_ref",
        "t.agent_id = ANY(%s) AND jsonb_array_length(t.evidence_refs) = 0"
        % _OPERATING),
    "ENTRY_WITHOUT_PROBABILITY": (
        "paper_decisions", "t.decision_id",
        "t.verdict = 'ENTER' AND t.p_pinnacle IS NULL "
        "AND t.p_internal IS NULL AND t.p_blended IS NULL"),
    "HOLD_ON_STALE_PROBABILITY": (
        "paper_xavier_reviews", "t.review_id",
        "t.recommendation = 'HOLD' AND (t.measure->>'stale' = 'true' OR ("
        "jsonb_typeof(t.measure->'probability_age_s') = 'number' "
        "AND jsonb_typeof(t.measure->'probability_limit_s') = 'number' "
        "AND (t.measure->>'probability_age_s')::numeric > "
        "(t.measure->>'probability_limit_s')::numeric))"),
    "AUDIT_DISCREPANCY_LEFT_OPEN": (
        "paper_audrey_findings", "t.finding_id",
        "upper(t.severity) IN ('WARNING', 'ERROR', 'CRITICAL', 'HIGH') "
        "AND t.improvement_task_id IS NULL"),
    "RECONCILIATION_DISCREPANCY_OPEN": (
        "smalllive_reconciliations", "t.group_id",
        "t.status = 'DISCREPANCY'"),
    "ADMISSION_REFUSED_DECISION": (
        "execution_intents", "t.intent_id",
        "t.actual_state = 'REFUSED' AND t.actual_refusal = ANY(ARRAY[%s])"
        % ", ".join("'%s'" % r for r in STALE_SUBMIT_REFUSALS)),
    "POLICY_CANDIDATE_WITHOUT_EVIDENCE": (
        "agent_policy_versions",
        "(t.agent_id || '|' || t.policy_key || '|' || t.version)",
        "t.state = 'CANDIDATE' AND " + _NO_REFS % {"c": "t.params"}),
    "POLICY_ARTIFACT_READY_WITHOUT_EVIDENCE": (
        "agent_policy_artifacts", "(t.policy_id || '@' || t.version)",
        "t.status = 'READY_FOR_OWNER_APPROVAL' AND "
        + _NO_REFS % {"c": "t.document"}),
    "LIVE_RULE_READY_WITHOUT_EVIDENCE": (
        "live_rule_artifacts", "(t.rule_id || '@' || t.version)",
        "t.status = 'READY_FOR_OWNER_APPROVAL' AND "
        + _NO_REFS % {"c": "t.document"}),
    "STRATEGY_CANDIDATE_WITHOUT_EVIDENCE": (
        "improvement_candidates", "t.candidate_id",
        "t.state = 'APPROVAL_READY' AND (t.evidence IS NULL OR t.evidence "
        "IN ('{}'::jsonb, '[]'::jsonb, 'null'::jsonb))"),
}


async def _exists(conn, table: str) -> bool:
    return await conn.fetchval("SELECT to_regclass($1)", table) is not None


async def _rule_rows(conn, det: str, select: str, *, window: str = "",
                     order: str, now: float, lo: float, hi: float,
                     limit: int):
    """The records `det`'s predicate holds for, not yet challenged by it,
    inside the window. `select` and `window` are fixed SQL over alias t."""
    table, key, pred = RULES[det]
    if not await _exists(conn, table):
        return []
    return await conn.fetch(
        "SELECT %s, (%s)::text AS _key FROM %s t WHERE (%s) %s AND %s "
        "ORDER BY %s LIMIT $5" % (select, key, table, pred,
                                  ("AND " + window) if window else
                                  "AND $3::float8 IS NOT NULL AND "
                                  "$4::float8 IS NOT NULL",
                                  _NOT_YET % key, order),
        det, table, lo, hi, limit)


# ═════════════════════════════════════════════════════════════════════
# DETECTORS: one bounded read each; every candidate cites its evidence
# ═════════════════════════════════════════════════════════════════════

_WIN = "%s BETWEEN to_timestamp($3) AND to_timestamp($4)"


async def detect_decision_without_evidence(conn, now: float, limit: int):
    """A Derek / Xavier / Audrey decision indexed with NO evidence
    reference: the decision index promises links to the authoritative
    records and this one carries none."""
    det, kind = "DECISION_WITHOUT_EVIDENCE", "agent_decisions"
    rows = await _rule_rows(
        conn, det, "t.decision_ref, t.agent_id, t.kind, t.subject, "
        "t.verdict, t.decided_at", window=_WIN % "t.decided_at",
        order="t.decided_at, t.decision_ref", now=now, lo=now - LOOKBACK_S,
        hi=now, limit=limit)
    return [{
        "detector": det, "target_agent": r["agent_id"], "target_kind": kind,
        "target_id": r["decision_ref"], "severity": "MEDIUM",
        "record_at": _ep(r["decided_at"]),
        "claim": ("Decision %s (%s, verdict %s, subject %s) is indexed with "
                  "no evidence reference: nothing links it to the "
                  "authoritative records it rests on. Prove it."
                  % (r["decision_ref"], r["kind"], r["verdict"] or "none",
                     r["subject"] or "none")),
        "evidence_refs": [{"kind": kind, "id": r["decision_ref"]}],
        "body": {"rule": RULES[det][2]},
    } for r in rows]


async def detect_entry_without_probability(conn, now: float, limit: int):
    """A paper ENTER verdict recorded with neither a Pinnacle nor an
    internal probability: an entry with no probability evidence at all."""
    det, kind = "ENTRY_WITHOUT_PROBABILITY", "paper_decisions"
    rows = await _rule_rows(
        conn, det, "t.decision_id, t.account_id, t.decided_at, "
        "t.us_market_slug, t.holding_side, t.strategy",
        window=_WIN % "t.decided_at", order="t.decided_at, t.decision_id",
        now=now, lo=now - LOOKBACK_S, hi=now, limit=limit)
    return [{
        "detector": det, "target_agent": R.DEREK, "target_kind": kind,
        "target_id": r["decision_id"], "severity": "HIGH",
        "record_at": _ep(r["decided_at"]), "account_id": r["account_id"],
        "claim": ("Paper decision %s entered %s %s (strategy %s) with no "
                  "Pinnacle, internal or blended probability recorded. What "
                  "are we missing?"
                  % (r["decision_id"], r["us_market_slug"],
                     r["holding_side"], r["strategy"])),
        "evidence_refs": [{"kind": kind, "id": r["decision_id"]}],
        "body": {"rule": RULES[det][2]},
    } for r in rows]


async def detect_hold_on_stale_probability(conn, now: float, limit: int):
    """A Xavier review that recommended HOLD while its own recorded
    probability was stale (measure.stale = true, or its age beyond its own
    recorded limit)."""
    det, kind = "HOLD_ON_STALE_PROBABILITY", "paper_xavier_reviews"
    rows = await _rule_rows(
        conn, det, "t.review_id, t.account_id, t.group_id, t.reviewed_at, "
        "t.measure", window=_WIN % "t.reviewed_at",
        order="t.reviewed_at, t.review_id", now=now, lo=now - LOOKBACK_S,
        hi=now, limit=limit)
    out = []
    for r in rows:
        m = _j(r["measure"])
        out.append({
            "detector": det, "target_agent": R.XAVIER, "target_kind": kind,
            "target_id": r["review_id"], "severity": "HIGH",
            "record_at": _ep(r["reviewed_at"]), "account_id": r["account_id"],
            "claim": ("Review %s of group %s recommended HOLD on a stale "
                      "probability (stale=%s, age %s s against a %s s "
                      "limit, as the review itself records). Prove it."
                      % (r["review_id"], r["group_id"], m.get("stale"),
                         m.get("probability_age_s"),
                         m.get("probability_limit_s"))),
            "evidence_refs": [{"kind": kind, "id": r["review_id"]}],
            "body": {"rule": RULES[det][2]},
        })
    return out


async def detect_audit_discrepancy_left_open(conn, now: float, limit: int):
    """An Audrey WARNING / ERROR / CRITICAL finding with no improvement task
    after AUDIT_OPEN_AFTER_S: a discrepancy found and left open."""
    det, kind = "AUDIT_DISCREPANCY_LEFT_OPEN", "paper_audrey_findings"
    rows = await _rule_rows(
        conn, det, "t.finding_id, t.account_id, t.found_at, t.kind, "
        "t.severity, t.subject", window=_WIN % "t.found_at",
        order="t.found_at, t.finding_id", now=now, lo=now - LOOKBACK_S,
        hi=now - AUDIT_OPEN_AFTER_S, limit=limit)
    return [{
        "detector": det, "target_agent": R.AUDREY, "target_kind": kind,
        "target_id": r["finding_id"],
        "severity": "HIGH" if str(r["severity"]).upper() in (
            "CRITICAL", "ERROR") else "MEDIUM",
        "record_at": _ep(r["found_at"]), "account_id": r["account_id"],
        "claim": ("Audit finding %s (%s %s on %s) has had no improvement "
                  "task for more than %d h: the discrepancy is recorded but "
                  "nothing is assigned to resolve it. What are we missing?"
                  % (r["finding_id"], r["severity"], r["kind"],
                     r["subject"] or "no subject",
                     AUDIT_OPEN_AFTER_S // 3600)),
        "evidence_refs": [{"kind": kind, "id": r["finding_id"]}],
        "body": {"rule": RULES[det][2],
                 "open_after_s": AUDIT_OPEN_AFTER_S},
    } for r in rows]


async def detect_reconciliation_discrepancy_open(conn, now: float,
                                                 limit: int):
    """A small-live reconciliation still in DISCREPANCY: Audrey audits the
    book against the venue and this discrepancy is unresolved."""
    det, kind = "RECONCILIATION_DISCREPANCY_OPEN", "smalllive_reconciliations"
    rows = await _rule_rows(
        conn, det, "t.group_id, t.venue, t.reconciled_at, t.discrepancies",
        window=_WIN % "t.reconciled_at", order="t.reconciled_at, t.group_id",
        now=now, lo=now - LOOKBACK_S, hi=now - 3600, limit=limit)
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
        "body": {"rule": RULES[det][2], "open_after_s": 3600},
    } for r in rows]


async def detect_admission_refusal(conn, now: float, limit: int):
    """An execution intent refused at admission because Derek's decision
    went stale before submission, or named an inexact contract identity:
    the admission rail caught what the decision should not have sent."""
    det, kind = "ADMISSION_REFUSED_DECISION", "execution_intents"
    rows = await _rule_rows(
        conn, det, "t.intent_id, t.decision_id, t.decided_at, t.created_at,"
        " t.actual_refusal, t.us_market_slug", window=_WIN % "t.created_at",
        order="t.created_at, t.intent_id", now=now, lo=now - LOOKBACK_S,
        hi=now, limit=limit)
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
            "body": {"rule": RULES[det][2], "decision_to_intent_s": lag},
        })
    return out


def _operating(v) -> str | None:
    s = str(v or "").strip().upper()
    return s if s in R.OPERATING_AGENTS else None


async def detect_policy_candidate_without_evidence(conn, now: float,
                                                   limit: int):
    """A NEW policy candidate (agent_policy_versions, state CANDIDATE) whose
    parameters cite no evidence reference: a candidate nobody has proven."""
    det, kind = "POLICY_CANDIDATE_WITHOUT_EVIDENCE", "agent_policy_versions"
    rows = await _rule_rows(
        conn, det, "t.agent_id, t.policy_key, t.version, t.created_by, "
        "t.created_at", window=_WIN % "t.created_at",
        order="t.created_at, t.agent_id, t.policy_key, t.version", now=now,
        lo=now - LOOKBACK_S, hi=now, limit=limit)
    return [{
        "detector": det,
        "target_agent": _operating(r["created_by"]) or r["agent_id"],
        "target_kind": kind, "target_id": r["_key"], "severity": "MEDIUM",
        "record_at": _ep(r["created_at"]),
        "claim": ("Policy candidate %s %s v%s (created by %s) cites no "
                  "evidence reference in its parameters. A new policy with "
                  "no evidence is an opinion. Prove it."
                  % (r["agent_id"], r["policy_key"], r["version"],
                     r["created_by"])),
        "evidence_refs": [{"kind": kind, "id": r["_key"]}],
        "body": {"rule": RULES[det][2]},
    } for r in rows if (_operating(r["created_by"]) or r["agent_id"])
        in K.TARGETS]


async def detect_policy_artifact_ready_without_evidence(conn, now: float,
                                                        limit: int):
    """A policy artifact stored READY_FOR_OWNER_APPROVAL whose document
    carries no evidence references: it asks the owner to approve what no
    record supports. (No time window: an artifact waits until decided.)"""
    det, kind = ("POLICY_ARTIFACT_READY_WITHOUT_EVIDENCE",
                 "agent_policy_artifacts")
    rows = await _rule_rows(
        conn, det, "t.policy_id, t.version, t.agent_id, t.title, "
        "t.created_at", order="t.created_at, t.policy_id", now=now,
        lo=0.0, hi=now, limit=limit)
    return [{
        "detector": det, "target_agent": r["agent_id"], "target_kind": kind,
        "target_id": r["_key"], "severity": "HIGH",
        "record_at": min(_ep(r["created_at"]), now),
        "claim": ("Policy artifact %s v%s (%s) is READY_FOR_OWNER_APPROVAL "
                  "and its document cites no evidence reference. Approval "
                  "on prose alone? What are we missing?"
                  % (r["policy_id"], r["version"], r["title"])),
        "evidence_refs": [{"kind": kind, "id": r["_key"]}],
        "body": {"rule": RULES[det][2]},
    } for r in rows if r["agent_id"] in K.TARGETS]


async def detect_live_rule_ready_without_evidence(conn, now: float,
                                                  limit: int):
    """A live rule artifact READY_FOR_OWNER_APPROVAL with no evidence
    references. A live rule governs the entry decision (Derek's lane) unless
    its document names another agent."""
    det, kind = "LIVE_RULE_READY_WITHOUT_EVIDENCE", "live_rule_artifacts"
    rows = await _rule_rows(
        conn, det, "t.rule_id, t.version, t.title, t.created_at, "
        "t.document->>'agent_id' AS agent_id", order="t.created_at, t.rule_id",
        now=now, lo=0.0, hi=now, limit=limit)
    return [{
        "detector": det, "target_agent": _operating(r["agent_id"]) or R.DEREK,
        "target_kind": kind, "target_id": r["_key"], "severity": "HIGH",
        "record_at": min(_ep(r["created_at"]), now),
        "claim": ("Live rule %s v%s (%s) is READY_FOR_OWNER_APPROVAL and its "
                  "document cites no evidence reference; documentation quotes "
                  "are not records. Prove it with recorded evidence."
                  % (r["rule_id"], r["version"], r["title"])),
        "evidence_refs": [{"kind": kind, "id": r["_key"]}],
        "body": {"rule": RULES[det][2],
                 "attribution": "document agent_id" if _operating(
                     r["agent_id"]) else "entry lane (Derek) by default"},
    } for r in rows]


async def detect_strategy_candidate_without_evidence(conn, now: float,
                                                     limit: int):
    """A strategy / improvement candidate in APPROVAL_READY with an empty
    evidence record."""
    det, kind = "STRATEGY_CANDIDATE_WITHOUT_EVIDENCE", "improvement_candidates"
    rows = await _rule_rows(
        conn, det, "t.candidate_id, t.assigned_agent, t.proposed_by, "
        "t.change_kind, t.created_at", window=_WIN % "t.created_at",
        order="t.created_at, t.candidate_id", now=now, lo=now - LOOKBACK_S,
        hi=now, limit=limit)
    return [{
        "detector": det,
        "target_agent": _operating(r["assigned_agent"]) or _operating(
            r["proposed_by"]) or R.AUDREY,
        "target_kind": kind, "target_id": r["candidate_id"],
        "severity": "HIGH", "record_at": _ep(r["created_at"]),
        "claim": ("Candidate %s (%s) is APPROVAL_READY with an empty "
                  "evidence record. Ready for approval on what evidence?"
                  % (r["candidate_id"], r["change_kind"])),
        "evidence_refs": [{"kind": kind, "id": r["candidate_id"]}],
        "body": {"rule": RULES[det][2]},
    } for r in rows]


# ── THE CHIEF ALLOCATOR: intel_allocations (migration 208) ───────────
#
# One row per (run_id, candidate_id); a new run rewrites EVERY candidate
# every ~600 s. A challenge is therefore keyed on the STABLE candidate
# identity (candidate_id: "decision:<decision_id>" / "position:<group_id>"),
# never on run_id, and the rule is applied to the candidate's LATEST row --
# so one allocation is challenged once, not once per run.
ALLOCATOR_TABLE = KE.ALLOCATOR_TABLE

#: evidence = a decision / valuation id carried anywhere in `reasons` or
#: `inputs`: a key decision_id / valuation_id / evidence_refs, or a string
#: such as "decision:<id>" / "valuation:<id>"
_ALLOC_EV_PATH = ('$.** ? (exists(@.decision_id) || exists(@.valuation_id) '
                  '|| exists(@.evidence_refs) || (@.type() == "string" && @ '
                  'like_regex "^(decision|valuation)[_:= ]+[A-Za-z0-9]"))')
#: ...or the row's own decision_id / group_id column naming a decision or a
#: held paper group that EXISTS (the allocator's candidate IS that record)
_ALLOC_HAS_EVIDENCE = (
    "(jsonb_path_exists(t.reasons, '%(p)s') OR "
    "jsonb_path_exists(t.inputs, '%(p)s') OR "
    "(t.decision_id IS NOT NULL AND EXISTS (SELECT 1 FROM paper_decisions d "
    "  WHERE d.decision_id = t.decision_id)) OR "
    "(t.group_id IS NOT NULL AND EXISTS (SELECT 1 FROM paper_fills f "
    "  WHERE f.group_id = t.group_id)))" % {"p": _ALLOC_EV_PATH})
_ALLOC_LATEST = (
    "SELECT DISTINCT ON (a.candidate_id) a.* FROM intel_allocations a "
    " WHERE a.label = 'SHADOW' %s "
    " ORDER BY a.candidate_id, a.computed_at DESC, a.run_id DESC")


def large_allocation_usd() -> float:
    try:
        v = float(os.getenv("KAREN_LARGE_ALLOCATION_USD", "250"))
        return v if v > 0 else 250.0
    except ValueError:
        return 250.0


def _allocator_predicate(detector: str) -> str:
    """THE RULE over one (latest) intel_allocations row, alias t. Only a
    positive shadow allocation is an allocation."""
    if detector == "ALLOCATION_WITHOUT_EVIDENCE":
        return "t.shadow_usd > 0 AND NOT %s" % _ALLOC_HAS_EVIDENCE
    return "t.shadow_usd > 0 AND t.shadow_usd >= %r" % float(
        large_allocation_usd())


async def _allocator_candidates(conn, now: float, limit: int, detector: str):
    if not await _exists(conn, ALLOCATOR_TABLE):
        return []                     # migration 208 absent: skip cleanly
    pred = _allocator_predicate(detector)
    rows = await conn.fetch(
        "SELECT t.candidate_id AS _key, t.run_id, t.candidate_kind, "
        "       t.decision_id, t.group_id, t.us_market_slug, t.shadow_usd, "
        "       t.computed_at "
        "  FROM (" + _ALLOC_LATEST % (
            "AND a.computed_at BETWEEN to_timestamp($3) AND "
            "to_timestamp($4)") + ") t "
        " WHERE (" + pred + ") AND " + _NOT_YET % "t.candidate_id" +
        " ORDER BY t.computed_at, t.candidate_id LIMIT $5",
        detector, ALLOCATOR_TABLE, now - LOOKBACK_S, now, limit)
    out = []
    for r in rows:
        amt = float(r["shadow_usd"])
        large = amt >= large_allocation_usd()
        subject = ("decision %s" % r["decision_id"] if r["decision_id"]
                   else "position %s" % r["group_id"] if r["group_id"]
                   else r["_key"])
        if detector == "ALLOCATION_WITHOUT_EVIDENCE":
            claim = ("Shadow allocation %s (%s, %s USD, latest run %s) "
                     "carries no decision or valuation id in its reasons or "
                     "inputs. Capital moved on what? Prove it."
                     % (r["_key"], subject, amt, r["run_id"]))
            sev = "HIGH" if large else "MEDIUM"
        else:
            claim = ("Shadow allocation %s (%s) is large: %s USD against the "
                     "%s USD threshold (latest run %s). What are we missing? "
                     "Show the evidence that justifies the size."
                     % (r["_key"], subject, amt, large_allocation_usd(),
                        r["run_id"]))
            sev = "MEDIUM"
        out.append({
            "detector": detector, "target_agent": K.CHIEF_ALLOCATOR,
            "target_kind": ALLOCATOR_TABLE, "target_id": r["_key"],
            "severity": sev,
            "record_at": min(_ep(r["computed_at"]), now),
            "claim": claim,
            "evidence_refs": [{"kind": ALLOCATOR_TABLE, "id": r["_key"]}],
            "body": {"rule": pred, "amount_usd": amt,
                     "threshold_usd": large_allocation_usd(),
                     "candidate_kind": r["candidate_kind"],
                     "decision_id": r["decision_id"],
                     "group_id": r["group_id"],
                     "latest_run_id": r["run_id"],
                     "label": "SHADOW"}})
    return out


async def detect_allocation_without_evidence(conn, now: float, limit: int):
    """A Chief Allocator shadow allocation (intel_allocations, latest row
    per candidate) whose reasons / inputs carry no decision or valuation id.
    Skips cleanly when migration 208 is absent."""
    return await _allocator_candidates(conn, now, limit,
                                       "ALLOCATION_WITHOUT_EVIDENCE")


async def detect_large_allocation(conn, now: float, limit: int):
    """A Chief Allocator allocation at or above KAREN_LARGE_ALLOCATION_USD:
    challenged to show its evidence. Once per candidate, never per run.
    Skips cleanly when migration 208 is absent."""
    return await _allocator_candidates(conn, now, limit, "LARGE_ALLOCATION")


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
        list(R.OPERATING_AGENTS), limit)
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
    ("POLICY_CANDIDATE_WITHOUT_EVIDENCE",
     detect_policy_candidate_without_evidence),
    ("POLICY_ARTIFACT_READY_WITHOUT_EVIDENCE",
     detect_policy_artifact_ready_without_evidence),
    ("LIVE_RULE_READY_WITHOUT_EVIDENCE",
     detect_live_rule_ready_without_evidence),
    ("STRATEGY_CANDIDATE_WITHOUT_EVIDENCE",
     detect_strategy_candidate_without_evidence),
    ("ALLOCATION_WITHOUT_EVIDENCE", detect_allocation_without_evidence),
    ("LARGE_ALLOCATION", detect_large_allocation),
    ("FINDING_RESTS_ON_UPHELD_DEFECT", detect_finding_on_upheld_defect),
)


# ═════════════════════════════════════════════════════════════════════
# THE SAME RULE, RE-APPLIED TO ONE CHALLENGED RECORD
# ═════════════════════════════════════════════════════════════════════

async def rule_holds(conn, detector: str, target_kind: str,
                     target_id: str) -> bool | None:
    """Does `detector`'s predicate STILL hold for the challenged record?
    True / False, or None when it cannot be re-applied (an unknown or
    manual detector, an absent table, a vanished record). Read only."""
    if detector in RULES:
        table, key, pred = RULES[detector]
        if table != target_kind or not await _exists(conn, table):
            return None
        row = await conn.fetchrow(
            "SELECT (%s) AS holds FROM %s t WHERE (%s)::text = $1 LIMIT 1"
            % (pred, table, key), str(target_id))
        return None if row is None else bool(row["holds"])
    if detector in ("ALLOCATION_WITHOUT_EVIDENCE", "LARGE_ALLOCATION"):
        if target_kind != ALLOCATOR_TABLE or not await _exists(
                conn, ALLOCATOR_TABLE):
            return None
        # the same rule, on the candidate's LATEST row (runs rewrite it)
        row = await conn.fetchrow(
            "SELECT (" + _allocator_predicate(detector) + ") AS holds FROM ("
            + _ALLOC_LATEST % "AND a.candidate_id = $1" + ") t",
            str(target_id))
        return None if row is None else bool(row["holds"])
    if detector == "FINDING_RESTS_ON_UPHELD_DEFECT":
        row = await conn.fetchrow(
            "SELECT EXISTS (SELECT 1 FROM agent_findings f "
            "  JOIN agent_finding_stages h ON h.finding_id=f.finding_id "
            "   AND h.seq=2 JOIN karen_challenges k ON k.state='UPHELD' "
            "   AND EXISTS (SELECT 1 FROM jsonb_array_elements("
            "       f.evidence_refs || h.evidence_refs) e "
            "        WHERE e->>'kind'=k.target_kind AND e->>'id'=k.target_id)"
            " WHERE f.finding_id=$1) AS holds", str(target_id))
        return bool(row["holds"]) if row is not None else None
    return None


# ═════════════════════════════════════════════════════════════════════
# ONE PASS
# ═════════════════════════════════════════════════════════════════════

async def _open_count(conn, detector: str) -> int:
    return int(await conn.fetchval(
        "SELECT count(*) FROM karen_challenges WHERE detector=$1 "
        "   AND state IN ('OPEN', 'RESPONDED')", detector) or 0)


def _deferred(c: dict, why: str) -> dict | None:
    """A detector candidate the pass did not open, as a CHALLENGE_
    INVESTIGATION backlog subject (agents/agent_work.py). A loop-finding
    candidate (its own stage machinery) is not deferred here."""
    if c.get("loop") or not c.get("target_kind") or \
            c.get("target_id") is None:
        return None
    from . import agent_work as AW
    return {"agent": K.KAREN,
            "subject": AW.subject_key(c["detector"], c["target_kind"],
                                      c["target_id"]),
            "arose_at": c.get("record_at"),
            "source_table": c["target_kind"],
            "source_id": str(c["target_id"]),
            "collaborator": (c.get("target_agent")
                             if c.get("target_agent") != K.KAREN else None),
            "blocker": why,
            "detail": {"detector": c["detector"],
                       "target_kind": c["target_kind"],
                       "target_id": str(c["target_id"]),
                       "target_agent": c.get("target_agent"),
                       "severity": c.get("severity")}}


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
    deferred: list = []
    for name, fn in detectors:
        if budget <= 0:
            break
        try:
            async with asyncio.timeout(DETECTOR_TIMEOUT_S):
                if await _open_count(conn, name) >= MAX_OPEN_PER_DETECTOR:
                    summary["refused"][name] = "OPEN_CHALLENGE_CAP_REACHED"
                    # what the capped detector would raise is owed work,
                    # not silence: it is enqueued, nothing is opened
                    deferred += [_deferred(c, "OPEN_CHALLENGE_CAP_REACHED")
                                 for c in await fn(conn, at,
                                                   MAX_NEW_PER_DETECTOR)]
                    continue
                cands = await fn(conn, at, MAX_NEW_PER_DETECTOR)
                summary["candidates"] += len(cands)
                deferred += [_deferred(c, "PASS_BUDGET_EXHAUSTED") for c in
                             cands[min(MAX_NEW_PER_DETECTOR, budget):]]
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
    # THE DURABLE QUEUE (migration 301): the deferred candidates
    try:
        from . import agent_work as AW
        async with asyncio.timeout(DETECTOR_TIMEOUT_S):
            summary["work_queue"] = await AW.sync_for(
                conn, "karen_runner", now=at,
                pushed={AW.K_INVESTIGATION: [d for d in deferred if d]})
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        summary["work_queue"] = {"error": type(exc).__name__}
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

