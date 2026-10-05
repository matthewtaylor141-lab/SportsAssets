"""WHAT EACH AGENT HAS DONE, DERIVED FROM DURABLE RECORDS (reads only).

  experience()     counts that are reproducible from the records -- each
                   metric names its table and definition; an absent table
                   or a failed read is UNAVAILABLE with the reason, never 0.
                   Not an "XP score".
  relationships()  each counterpart, with every interaction kind (Karen's
                   challenges and their outcomes, Audrey's evaluations,
                   Derek -> Xavier hand-offs, Eddie's estimates of Derek's
                   decisions, the candidate-review chain, collaboration-loop
                   stages, agent-to-agent messages, relationship memories)
                   and its evidence.
  events()         the agent event stream: ONLY events whose basis is a
                   durable row (`DURABLE_SOURCES`), each with its table and
                   id. agent_status (a mutable heartbeat) is not a source:
                   no heartbeat-only, synthetic or animated "activity".
  evaluation()     the per-agent evaluation matrix (section 14): factual
                   accuracy, citation coverage, stale-evidence handling,
                   authority compliance, role adherence, calibration,
                   economic contribution, challenge quality, self-correction
                   rate -- each MEASURED, STRUCTURAL, NOT_APPLICABLE or
                   UNAVAILABLE with its reason. No single vanity score.

PAPER and ACTUAL are separate keys and never summed; actual samples are
venue by venue. Every function only SELECTs.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .. import xavier_freshness as XF
from . import agent_context as AC
from . import agent_memory as M
from . import identity as I

VERSION = "AGENT_ACTIVITY_V1"

OK, EMPTY, UNAVAILABLE, ABSENT = "OK", "EMPTY", "UNAVAILABLE", "ABSENT"
MEASURED, STRUCTURAL, NOT_APPLICABLE = ("MEASURED", "STRUCTURAL",
                                        "NOT_APPLICABLE")


def _ep(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return v.timestamp()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


async def _exists(conn, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    table))


async def _guarded(conn, tables, fn):
    """(status, value, why): ABSENT when a table is missing, UNAVAILABLE on
    a failed read (savepoint, so the caller's transaction survives)."""
    for t in tables:
        if not await _exists(conn, t):
            return ABSENT, None, "TABLE_NOT_DEPLOYED:%s" % t
    try:
        async with conn.transaction():
            v = await fn()
    except Exception as exc:                                    # noqa: BLE001
        return UNAVAILABLE, None, "READ_FAILED:%s" % type(exc).__name__
    return OK, v, None


# ═════════════════════════════════════════════════════════════════════
# 1 · EXPERIENCE: reproducible counts
# ═════════════════════════════════════════════════════════════════════
#
# (key, label, book, tables, SQL returning one integer or rows of
# (venue, n), definition). `book` is PAPER / ACTUAL / SHADOW / RECORDS.

SETTLED_ENTERS = (
    "SELECT count(DISTINCT d.decision_id) FROM paper_settlements s "
    "  JOIN paper_handoffs h ON h.group_id = s.group_id "
    "  JOIN paper_decisions d ON d.decision_id = h.decision_id "
    " WHERE d.verdict = 'ENTER'")

EXPERIENCE: dict[str, list] = {
    "DEREK": [
        ("decisions_reviewed", "Decisions recorded", "PAPER",
         ("paper_decisions",), "SELECT count(*) FROM paper_decisions",
         "every paper_decisions row (ENTER and REFUSE)"),
        ("enter_verdicts", "ENTER verdicts", "PAPER", ("paper_decisions",),
         "SELECT count(*) FROM paper_decisions WHERE verdict='ENTER'",
         "paper_decisions with verdict ENTER (a recommendation, not a fill)"),
        ("refusals", "REFUSE verdicts", "PAPER", ("paper_decisions",),
         "SELECT count(*) FROM paper_decisions WHERE verdict='REFUSE'",
         "paper_decisions with verdict REFUSE"),
        ("forward_paper_sample", "ENTER decisions that filled", "PAPER",
         ("paper_handoffs", "paper_decisions"),
         "SELECT count(DISTINCT h.decision_id) FROM paper_handoffs h "
         "  JOIN paper_decisions d ON d.decision_id = h.decision_id",
         "distinct ENTER decisions with a Derek -> Xavier paper hand-off "
         "(a confirmed first fill)"),
        ("realized_outcomes_observed", "Settled entries", "PAPER",
         ("paper_settlements", "paper_handoffs", "paper_decisions"),
         SETTLED_ENTERS, "distinct ENTER decisions whose position settled"),
        ("actual_live_sample", "Actual entry intents", "ACTUAL",
         ("bettor_funded_intents",),
         "VENUE:SELECT venue, count(*) FROM bettor_funded_intents "
         " WHERE kind='ENTRY' GROUP BY venue",
         "funded-lane ENTRY intents per venue (requests, not fills)"),
    ],
    "XAVIER": [
        ("positions_managed", "Positions reviewed", "PAPER",
         ("paper_xavier_reviews",),
         "SELECT count(DISTINCT group_id) FROM paper_xavier_reviews",
         "distinct paper groups with at least one Xavier review"),
        ("reviews_recorded", "Reviews recorded", "PAPER",
         ("paper_xavier_reviews",),
         "SELECT count(*) FROM paper_xavier_reviews",
         "every paper_xavier_reviews row"),
        ("waiting_for_fresh_evidence", "Reviews that waited for evidence",
         "PAPER", ("paper_xavier_reviews",),
         "SELECT count(*) FROM paper_xavier_reviews WHERE recommendation "
         " IN ('WAITING_FOR_FRESH_EVIDENCE', "
         "     'MANAGEMENT_UNAVAILABLE_STALE_INPUT')",
         "reviews that recorded a non-action on non-fresh evidence"),
        ("management_assessments", "Management assessments", "RECORDS",
         ("xavier_management_assessments",),
         "SELECT count(*) FROM xavier_management_assessments",
         "xavier_management_assessments rows (paper and actual)"),
        ("realized_outcomes_observed", "Managed positions settled", "PAPER",
         ("paper_settlements", "paper_xavier_reviews"),
         "SELECT count(DISTINCT s.group_id) FROM paper_settlements s "
         " WHERE EXISTS (SELECT 1 FROM paper_xavier_reviews r "
         "                WHERE r.group_id = s.group_id)",
         "distinct reviewed paper groups that settled"),
        ("actual_live_sample", "Actual positions handed over", "ACTUAL",
         ("smalllive_handoffs",),
         "VENUE:SELECT venue, count(*) FROM smalllive_handoffs "
         " GROUP BY venue",
         "smalllive_handoffs per venue"),
    ],
    "AUDREY": [
        ("findings_recorded", "Audit findings", "PAPER",
         ("paper_audrey_findings",),
         "SELECT count(*) FROM paper_audrey_findings",
         "paper_audrey_findings rows"),
        ("reconciliations_performed", "Final daily reconciliations",
         "PAPER", ("paper_audrey_reports",),
         "SELECT count(*) FROM paper_audrey_reports WHERE final",
         "final paper_audrey_reports (each states whether it reconciles)"),
        ("reconciliations_failed", "Final reports that did not reconcile",
         "PAPER", ("paper_audrey_reports",),
         "SELECT count(*) FROM paper_audrey_reports WHERE final "
         "   AND NOT reconciles", "final reports with reconciles = false"),
        ("audit_reports", "Audit reports", "RECORDS",
         ("audrey_audit_reports",),
         "SELECT count(*) FROM audrey_audit_reports",
         "audrey_audit_reports rows (all versions)"),
        ("challenges_evaluated", "Challenges evaluated", "RECORDS",
         ("karen_challenges",),
         "SELECT count(*) FROM karen_challenges WHERE "
         " upper(btrim(resolved_by))='AUDREY' "
         "   AND state IN ('UPHELD','REJECTED')",
         "karen_challenges Audrey resolved UPHELD or REJECTED"),
        ("realized_outcomes_observed", "Resolved audits", "RECORDS",
         ("paper_audrey_reports", "karen_challenges"),
         "SELECT (SELECT count(*) FROM paper_audrey_reports WHERE final) + "
         " (SELECT count(*) FROM karen_challenges WHERE "
         "   upper(btrim(resolved_by))='AUDREY' "
         "   AND state IN ('UPHELD','REJECTED'))",
         "final reconciliations + challenges she evaluated"),
    ],
    "KAREN": [
        ("challenges_raised", "Challenges raised", "RECORDS",
         ("karen_challenges",), "SELECT count(*) FROM karen_challenges",
         "every karen_challenges row"),
        ("challenges_upheld", "Upheld", "RECORDS", ("karen_challenges",),
         "SELECT count(*) FROM karen_challenges WHERE state='UPHELD'",
         "resolved UPHELD by someone other than Karen"),
        ("challenges_rejected", "Rejected", "RECORDS", ("karen_challenges",),
         "SELECT count(*) FROM karen_challenges WHERE state='REJECTED'",
         "resolved REJECTED by an independent evaluator"),
        ("challenges_open", "Open or answered", "RECORDS",
         ("karen_challenges",),
         "SELECT count(*) FROM karen_challenges "
         " WHERE state IN ('OPEN','RESPONDED')",
         "not yet resolved"),
        ("realized_outcomes_observed", "Resolved challenges", "RECORDS",
         ("karen_challenges",),
         "SELECT count(*) FROM karen_challenges "
         " WHERE state IN ('UPHELD','REJECTED','WITHDRAWN')",
         "UPHELD + REJECTED + WITHDRAWN"),
    ],
    "CHIEF_ALLOCATOR": [
        ("allocation_runs", "Allocation runs", "SHADOW", ("intel_runs",),
         "SELECT count(*) FROM intel_runs WHERE component='ALLOCATOR'",
         "intel_runs of the ALLOCATOR component"),
        ("candidates_ranked", "Candidates ranked", "SHADOW",
         ("intel_allocations",), "SELECT count(*) FROM intel_allocations",
         "intel_allocations rows (all runs)"),
        ("shadow_funded", "Shadow-funded candidates", "SHADOW",
         ("intel_allocations",),
         "SELECT count(*) FROM intel_allocations WHERE shadow_usd > 0",
         "intel_allocations with a positive SHADOW weight (no capital)"),
        ("realized_outcomes_observed", "Shadow allocations settled",
         "SHADOW", ("intel_allocations", "paper_handoffs",
                    "paper_settlements"),
         "SELECT count(DISTINCT (a.run_id, a.candidate_id)) "
         "  FROM intel_allocations a "
         "  JOIN paper_handoffs h ON h.decision_id = a.decision_id "
         "  JOIN paper_settlements s ON s.group_id = h.group_id "
         " WHERE a.shadow_usd > 0",
         "shadow-funded candidates whose paper position settled"),
    ],
    "EDDIE": [
        ("estimates_recorded", "Execution estimates", "SHADOW",
         ("eddie_execution_estimates",),
         "SELECT count(*) FROM eddie_execution_estimates",
         "eddie_execution_estimates rows (SHADOW)"),
        ("fills_evaluated_paper", "Paper fills evaluated", "PAPER",
         ("eddie_execution_outcomes",),
         "SELECT count(*) FROM eddie_execution_outcomes "
         " WHERE source='PAPER'", "eddie_execution_outcomes source PAPER"),
        ("fills_evaluated_actual", "Actual fills evaluated", "ACTUAL",
         ("eddie_execution_outcomes",),
         "SELECT count(*) FROM eddie_execution_outcomes "
         " WHERE source='ACTUAL'", "eddie_execution_outcomes source ACTUAL"),
        ("realized_outcomes_observed", "Fills evaluated", "RECORDS",
         ("eddie_execution_outcomes",),
         "SELECT count(*) FROM eddie_execution_outcomes "
         " WHERE realized_execution_loss_pp IS NOT NULL",
         "outcomes with a measured realized execution loss"),
    ],
    "SCOUT": [
        ("features_registered", "Feature hypotheses", "RECORDS",
         ("scout_features",), "SELECT count(*) FROM scout_features",
         "scout_features rows"),
        ("observations", "Observations", "RECORDS",
         ("scout_feature_observations",),
         "SELECT count(*) FROM scout_feature_observations",
         "scout_feature_observations rows"),
        ("hypotheses_tested", "Tournaments evaluated", "RECORDS",
         ("scout_feature_tournaments",),
         "SELECT count(*) FROM scout_feature_tournaments "
         " WHERE verdict IS NOT NULL",
         "tournaments with an evaluator's verdict"),
        ("hypotheses_validated", "Validated", "RECORDS",
         ("scout_feature_tournaments",),
         "SELECT count(*) FROM scout_feature_tournaments "
         " WHERE verdict='VALIDATED'", "verdict VALIDATED (evaluator)"),
        ("realized_outcomes_observed", "Tournaments evaluated", "RECORDS",
         ("scout_feature_tournaments",),
         "SELECT count(*) FROM scout_feature_tournaments "
         " WHERE verdict IS NOT NULL", "tournaments with a verdict"),
    ],
    "ADRIANA": [
        ("census_passes", "Census passes", "RECORDS",
         ("adriana_arb_scans",), "SELECT count(*) FROM adriana_arb_scans",
         "adriana_arb_scans rows"),
        ("structures_evaluated", "Structures evaluated", "RECORDS",
         ("adriana_arb_scans",),
         "SELECT coalesce(sum(structures_considered), 0) "
         "  FROM adriana_arb_scans",
         "sum of structures_considered over her passes"),
        ("proven_after_costs", "Proven after costs", "RECORDS",
         ("adriana_arb_opportunities",),
         "SELECT count(*) FROM adriana_arb_opportunities",
         "adriana_arb_opportunities rows (GUARANTEED_AFTER_COSTS only)"),
        ("realized_outcomes_observed", "Refusals recorded", "RECORDS",
         ("adriana_arb_refusals",),
         "SELECT count(*) FROM adriana_arb_refusals",
         "adriana_arb_refusals rows, each with its codes"),
    ],
}
EVENTS_KEY = "realized_outcomes_observed"


async def experience(conn, agent: str, *, now: float | None = None) -> dict:
    a = I.agent_of(agent)
    if a is None:
        raise ValueError(I.R_UNKNOWN_AGENT)
    metrics = []
    for key, label, book, tables, sql, definition in EXPERIENCE[a]:
        venue = sql.startswith("VENUE:")
        q = sql[6:] if venue else sql

        async def fn(q=q, venue=venue):
            if venue:
                return {str(r[0]): int(r[1]) for r in await conn.fetch(q)}
            return int(await conn.fetchval(q) or 0)
        st, val, why = await _guarded(conn, tables, fn)
        metrics.append({"key": key, "label": label, "book": book,
                        "value": val, "status": st, "why": why,
                        "by_venue": venue, "source": list(tables),
                        "definition": definition,
                        "sql": q})
    ev = next((m for m in metrics if m["key"] == EVENTS_KEY), None)
    return {"schema": "bettor.agent.experience.v1", "agent": a,
            "metrics": metrics,
            "events": None if ev is None or ev["status"] != OK
            else ev["value"],
            "events_status": None if ev is None else ev["status"],
            "events_why": None if ev is None else ev["why"],
            "events_definition": None if ev is None else ev["definition"],
            "derived": True, "hand_entered": False,
            "computed_at": float(now if now is not None else time.time())}


# ═════════════════════════════════════════════════════════════════════
# 2 · THE EVENT STREAM: durable rows only
# ═════════════════════════════════════════════════════════════════════

EVENT_KINDS = ("DECISION_STARTED", "DECISION_RECORDED", "REVIEW_STARTED",
               "REVIEW_RECORDED", "CHALLENGE_RAISED", "CHALLENGE_ANSWERED",
               "HANDOFF", "EXECUTION_ESTIMATE", "AUDIT_FINDING",
               "MEMORY_LEARNED", "SELF_CORRECTION", "WAITING_FOR_EVIDENCE")
#: Every table an event may stand on. agent_status is deliberately absent:
#: a heartbeat row is overwritten in place and is not a lifecycle record.
DURABLE_SOURCES = frozenset({
    "agent_runs", "paper_decisions", "agent_decisions",
    "paper_xavier_reviews", "xavier_management_assessments",
    "karen_challenges", "paper_handoffs", "agent_conversation_messages",
    "eddie_execution_estimates", "paper_audrey_findings",
    "agent_memory_events", "intel_runs", "scout_features",
    "pos_candidate_review_steps"})
R_NO_BASIS = "EVENT_HAS_NO_DURABLE_BASIS"


def validate_event(e: dict) -> str | None:
    """None when the event stands on a durable row, else the refusal."""
    b = (e or {}).get("basis") or {}
    if e.get("kind") not in EVENT_KINDS:
        return "NOT_AN_EVENT_KIND"
    if b.get("table") not in DURABLE_SOURCES or not str(
            b.get("id") or "").strip():
        return R_NO_BASIS
    if _ep(e.get("at")) is None:
        return "EVENT_HAS_NO_RECORDED_TIME"
    if I.agent_of(e.get("agent")) is None:
        return I.R_UNKNOWN_AGENT
    return None


def _ev(kind, agent, at, table, rid, summary, *, counterpart=None,
        extra=None) -> dict:
    e = {"event_id": "%s:%s:%s" % (kind, table, rid), "kind": kind,
         "agent": I.agent_of(agent), "counterpart": I.agent_of(counterpart),
         "at": _ep(at), "summary": summary,
         "basis": {"table": table, "id": str(rid)}}
    if extra:
        e.update(extra)
    return e


def _win(col: str, asc: bool) -> str:
    return ("%s > to_timestamp($1) AND %s <= to_timestamp($2) ORDER BY %s %s "
            "LIMIT $3" % (col, col, col, "ASC" if asc else "DESC"))


async def _src_runs(conn, lo, hi, n, asc):
    out = []
    for r in await conn.fetch(
            "SELECT run_id, agent_id, started_at, outcome FROM agent_runs "
            "WHERE " + _win("started_at", asc), lo, hi, n):
        kind = ("DECISION_STARTED" if r["agent_id"] == "DEREK"
                else "REVIEW_STARTED")
        out.append(_ev(kind, r["agent_id"], r["started_at"], "agent_runs",
                       r["run_id"], "Run %s started" % r["run_id"]))
    return out


async def _src_paper_decisions(conn, lo, hi, n, asc):
    return [_ev("DECISION_RECORDED", "DEREK", r["decided_at"],
                "paper_decisions", r["decision_id"],
                "%s %s %s%s" % (r["verdict"], r["us_market_slug"] or "",
                                r["holding_side"] or "",
                                (" · " + r["refusal"]) if r["refusal"]
                                else ""), extra={"book": "PAPER"})
            for r in await conn.fetch(
                "SELECT decision_id, decided_at, verdict, refusal, "
                "       us_market_slug, holding_side FROM paper_decisions "
                "WHERE " + _win("decided_at", asc), lo, hi, n)]


async def _src_agent_decisions(conn, lo, hi, n, asc):
    return [_ev("DECISION_RECORDED", r["agent_id"], r["decided_at"],
                "agent_decisions", r["decision_ref"],
                "%s %s%s" % (r["kind"], r["subject"] or "",
                             (" · " + r["verdict"]) if r["verdict"] else ""))
            for r in await conn.fetch(
                "SELECT decision_ref, agent_id, kind, subject, verdict, "
                "       decided_at FROM agent_decisions "
                "WHERE " + _win("decided_at", asc), lo, hi, n)]


async def _src_xavier_reviews(conn, lo, hi, n, asc):
    out = []
    for r in await conn.fetch(
            "SELECT r.review_id, r.group_id, r.reviewed_at, "
            "       r.recommendation, r.refusal, r.measure, r.selection, "
            "       (SELECT x.review_id FROM paper_xavier_reviews x "
            "         WHERE x.group_id = r.group_id "
            "           AND (x.reviewed_at, x.review_id) > "
            "               (r.reviewed_at, r.review_id) "
            "         ORDER BY x.reviewed_at DESC, x.review_id DESC "
            "         LIMIT 1) AS newer_id "
            "  FROM paper_xavier_reviews r WHERE "
            + _win("r.reviewed_at", asc), lo, hi, n):
        d = dict(r, reviewed_at=_ep(r["reviewed_at"]))
        blk = XF.of_review(d, now=hi, newer_assessment_id=r["newer_id"])
        rec = r["recommendation"]
        kind = ("WAITING_FOR_EVIDENCE" if rec in XF.NON_ACTIONS
                else "REVIEW_RECORDED")
        out.append(_ev(
            kind, "XAVIER", r["reviewed_at"], "paper_xavier_reviews",
            r["review_id"], "Reviewed %s: %s" % (
                r["group_id"], blk["display_recommendation"]
                if blk["is_current"] else "%s (recorded %s)" % (
                    blk["recommendation_state"], rec or "nothing")),
            extra={"book": "PAPER",
                   "recommendation_state": blk["recommendation_state"],
                   "management_state": blk["management_state"],
                   "superseded_by": blk.get("superseded_by")}))
    return out


async def _src_assessments(conn, lo, hi, n, asc):
    out = []
    for r in await conn.fetch(
            "SELECT a.assessment_id, a.position_kind, a.group_id, "
            "       a.assessed_at, a.recommendation, "
            "       to_jsonb(a) ->> 'recommendation_state' AS rec_state "
            "  FROM xavier_management_assessments a WHERE "
            + _win("a.assessed_at", asc), lo, hi, n):
        waiting = (r["recommendation"] in XF.NON_ACTIONS
                   or r["rec_state"] in XF.NON_ACTIONS)
        out.append(_ev(
            "WAITING_FOR_EVIDENCE" if waiting else "REVIEW_RECORDED",
            "XAVIER", r["assessed_at"], "xavier_management_assessments",
            r["assessment_id"], "Assessed %s %s: %s" % (
                r["position_kind"], r["group_id"],
                r["rec_state"] or "STATE_NOT_RECORDED (pre-222 row; "
                "not shown as current)"),
            extra={"book": r["position_kind"]}))
    return out


async def _src_challenges(conn, lo, hi, n, asc):
    out = []
    for r in await conn.fetch(
            "SELECT challenge_id, target_agent, severity, claim, "
            "       challenged_at FROM karen_challenges WHERE "
            + _win("challenged_at", asc), lo, hi, n):
        out.append(_ev("CHALLENGE_RAISED", "KAREN", r["challenged_at"],
                       "karen_challenges", r["challenge_id"],
                       "%s challenge of %s: %s" % (
                           r["severity"], r["target_agent"],
                           str(r["claim"])[:140]),
                       counterpart=r["target_agent"]))
    for r in await conn.fetch(
            "SELECT challenge_id, target_agent, responded_by, "
            "       response_stance, responded_at FROM karen_challenges "
            " WHERE responded_at IS NOT NULL AND "
            + _win("responded_at", asc), lo, hi, n):
        e = _ev("CHALLENGE_ANSWERED", r["responded_by"], r["responded_at"],
                "karen_challenges", r["challenge_id"],
                "%s Karen's challenge" % (str(r["response_stance"]).title()),
                counterpart="KAREN")
        e["event_id"] += ":answer"
        if e["agent"]:
            out.append(e)
    for r in await conn.fetch(
            "SELECT challenge_id, target_agent, resolved_by, outcome, "
            "       resolved_at FROM karen_challenges "
            " WHERE resolved_at IS NOT NULL AND state <> 'WITHDRAWN' AND "
            + _win("resolved_at", asc), lo, hi, n):
        e = _ev("REVIEW_RECORDED", r["resolved_by"], r["resolved_at"],
                "karen_challenges", r["challenge_id"],
                "Evaluated Karen's challenge of %s: %s" % (
                    r["target_agent"], r["outcome"]),
                counterpart=r["target_agent"])
        e["event_id"] += ":resolution"
        if e["agent"]:
            out.append(e)
    return out


async def _src_paper_handoffs(conn, lo, hi, n, asc):
    return [_ev("HANDOFF", "DEREK", r["created_at"], "paper_handoffs",
                r["handoff_id"], "Handed group %s to Xavier" % r["group_id"],
                counterpart="XAVIER", extra={"book": "PAPER"})
            for r in await conn.fetch(
                "SELECT handoff_id, group_id, created_at FROM paper_handoffs "
                "WHERE " + _win("created_at", asc), lo, hi, n)]


async def _src_messages(conn, lo, hi, n, asc):
    return [_ev("HANDOFF", r["from_agent"], r["created_at"],
                "agent_conversation_messages", r["message_id"],
                "%s: %s" % (r["message_kind"], str(r["summary"])[:160]),
                counterpart=r["to_agent"],
                extra={"message_kind": r["message_kind"]})
            for r in await conn.fetch(
                "SELECT message_id, from_agent, to_agent, message_kind, "
                "       summary, created_at FROM agent_conversation_messages "
                "WHERE " + _win("created_at", asc), lo, hi, n)]


async def _src_estimates(conn, lo, hi, n, asc):
    return [_ev("EXECUTION_ESTIMATE", "EDDIE", r["estimated_at"],
                "eddie_execution_estimates", r["estimate_id"],
                "Estimated decision %s: %s (SHADOW)" % (
                    r["decision_id"], r["recommendation"]),
                counterpart="DEREK")
            for r in await conn.fetch(
                "SELECT estimate_id, decision_id, recommendation, "
                "       estimated_at FROM eddie_execution_estimates "
                "WHERE " + _win("estimated_at", asc), lo, hi, n)]


async def _src_findings(conn, lo, hi, n, asc):
    return [_ev("AUDIT_FINDING", "AUDREY", r["found_at"],
                "paper_audrey_findings", r["finding_id"],
                "%s %s%s" % (r["severity"], r["kind"],
                             (" · " + r["subject"]) if r["subject"] else ""),
                extra={"book": "PAPER"})
            for r in await conn.fetch(
                "SELECT finding_id, found_at, kind, severity, subject "
                "  FROM paper_audrey_findings WHERE "
                + _win("found_at", asc), lo, hi, n)]


async def _src_memory(conn, lo, hi, n, asc):
    return [_ev("SELF_CORRECTION" if r["memory_kind"] == M.SELF_CORRECTION
                else "MEMORY_LEARNED", r["agent_id"], r["learned_at"],
                "agent_memory_events", r["memory_id"],
                "%s: %s" % (r["memory_kind"], str(r["summary"])[:160]),
                extra={"memory_kind": r["memory_kind"]})
            for r in await conn.fetch(
                "SELECT memory_id, agent_id, memory_kind, summary, "
                "       learned_at FROM agent_memory_events WHERE "
                + _win("learned_at", asc), lo, hi, n)]


async def _src_intel(conn, lo, hi, n, asc):
    out = []
    for r in await conn.fetch(
            "SELECT run_id, status, started_at FROM intel_runs "
            " WHERE component='ALLOCATOR' AND " + _win("started_at", asc),
            lo, hi, n):
        out.append(_ev("REVIEW_STARTED", "CHIEF_ALLOCATOR", r["started_at"],
                       "intel_runs", r["run_id"],
                       "Allocation run %s started (SHADOW)" % r["run_id"]))
    for r in await conn.fetch(
            "SELECT run_id, status, finished_at FROM intel_runs "
            " WHERE component='ALLOCATOR' AND status='OK' "
            "   AND finished_at IS NOT NULL AND "
            + _win("finished_at", asc), lo, hi, n):
        e = _ev("DECISION_RECORDED", "CHIEF_ALLOCATOR", r["finished_at"],
                "intel_runs", r["run_id"],
                "Recorded SHADOW allocation run %s" % r["run_id"])
        e["event_id"] += ":finished"
        out.append(e)
    return out


async def _src_features(conn, lo, hi, n, asc):
    return [_ev("DECISION_RECORDED", "SCOUT", r["proposed_at"],
                "scout_features", r["feature_id"],
                "Registered feature HYPOTHESIS %s (%s)" % (r["feature"],
                                                         r["state"]))
            for r in await conn.fetch(
                "SELECT feature_id, feature, state, proposed_at "
                "  FROM scout_features WHERE " + _win("proposed_at", asc),
                lo, hi, n)]


async def _src_pos_steps(conn, lo, hi, n, asc):
    return [_ev("REVIEW_RECORDED", r["agent"], r["at"],
                "pos_candidate_review_steps",
                "%s#%s" % (r["review_id"], r["seq"]),
                "%s (%s)" % (str(r["step"]).replace("_", " ").title(),
                             r["status"]))
            for r in await conn.fetch(
                "SELECT review_id, seq, step, agent, status, at "
                "  FROM pos_candidate_review_steps WHERE "
                + _win("at", asc), lo, hi, n)
            if I.agent_of(r["agent"])]


EVENT_SOURCES = (
    ("agent_runs", ("agent_runs",), _src_runs),
    ("paper_decisions", ("paper_decisions",), _src_paper_decisions),
    ("agent_decisions", ("agent_decisions",), _src_agent_decisions),
    ("paper_xavier_reviews", ("paper_xavier_reviews",), _src_xavier_reviews),
    ("xavier_management_assessments", ("xavier_management_assessments",),
     _src_assessments),
    ("karen_challenges", ("karen_challenges",), _src_challenges),
    ("paper_handoffs", ("paper_handoffs",), _src_paper_handoffs),
    ("agent_conversation_messages", ("agent_conversation_messages",),
     _src_messages),
    ("eddie_execution_estimates", ("eddie_execution_estimates",),
     _src_estimates),
    ("paper_audrey_findings", ("paper_audrey_findings",), _src_findings),
    ("agent_memory_events", ("agent_memory_events",), _src_memory),
    ("intel_runs", ("intel_runs",), _src_intel),
    ("scout_features", ("scout_features",), _src_features),
    ("pos_candidate_review_steps", ("pos_candidate_review_steps",),
     _src_pos_steps),
)
DEFAULT_LOOKBACK_S = 86400.0


def parse_cursor(cursor: str | None) -> tuple:
    """'<epoch>|<event_id>' -> (epoch, event_id), or (None, None)."""
    if not cursor:
        return None, None
    s = str(cursor)
    at, _, eid = s.partition("|")
    try:
        return float(at), eid or None
    except ValueError:
        return None, None


async def events(conn, *, agent: str | None = None, since: str | None = None,
                 limit: int = 50, now: float | None = None) -> dict:
    """DURABLE EVENTS. With a cursor (`since` = '<epoch>|<event_id>' from a
    previous `next_cursor`): the events after it, oldest first. Without:
    the newest `limit` events of the last day, oldest first. Every event is
    validated against DURABLE_SOURCES; anything else is dropped and counted
    (never expected)."""
    hi = float(now if now is not None else time.time())
    lim = max(1, min(int(limit or 50), 200))
    c_at, c_id = parse_cursor(since)
    asc = c_at is not None
    lo = (c_at - 1e-6) if asc else hi - DEFAULT_LOOKBACK_S
    a = I.agent_of(agent) if agent else None
    raw, sources = [], {}
    for name, tables, fn in EVENT_SOURCES:
        st, got, why = await _guarded(conn, tables,
                                      lambda fn=fn: fn(conn, lo, hi, lim * 2,
                                                       asc))
        sources[name] = {"status": st if st != OK else (
            OK if got else EMPTY), "why": why}
        raw.extend(got or [])
    rejected = 0
    out = []
    for e in raw:
        if validate_event(e):
            rejected += 1
            continue
        if a and a not in (e["agent"], e["counterpart"]):
            continue
        if asc and (e["at"], e["event_id"]) <= (c_at, c_id or ""):
            continue
        out.append(e)
    out.sort(key=lambda e: (e["at"], e["event_id"]))
    out = out[:lim] if asc else out[-lim:]
    nxt = ("%r|%s" % (out[-1]["at"], out[-1]["event_id"]) if out
           else since)
    return {"schema": "bettor.agent.events.v1", "agent": a,
            "events": out, "count": len(out), "next_cursor": nxt,
            "window": {"from": lo, "to": hi, "mode": "AFTER_CURSOR" if asc
                       else "LATEST"},
            "sources": sources, "rejected_without_durable_basis": rejected,
            "durable_sources": sorted(DURABLE_SOURCES),
            "rule": ("every event is a durable row (table + id); a heartbeat "
                     "is not activity and nothing is synthesized"),
            "computed_at": hi}


# ═════════════════════════════════════════════════════════════════════
# 3 · RELATIONSHIPS
# ═════════════════════════════════════════════════════════════════════

async def relationships(conn, agent: str, *, now: float | None = None
                        ) -> dict:
    a = I.agent_of(agent)
    if a is None:
        raise ValueError(I.R_UNKNOWN_AGENT)
    acc: dict = {}
    sections: dict = {}

    def add(cp, kind, direction, n, latest, evidence, source, **kw):
        cp = I.agent_of(cp)
        if not cp or cp == a or not n:
            return
        rel = acc.setdefault(cp, {"counterpart": cp, "interactions": [],
                                  "total": 0})
        rel["interactions"].append(dict(
            {"kind": kind, "direction": direction, "count": int(n),
             "latest_at": _ep(latest), "evidence": evidence or [],
             "source": source}, **kw))
        rel["total"] += int(n)

    async def karen():
        if a == "KAREN":
            rows = await conn.fetch(
                "SELECT target_agent AS cp, count(*) AS n, "
                "  count(*) FILTER (WHERE state='UPHELD') AS upheld, "
                "  count(*) FILTER (WHERE state='REJECTED') AS rejected, "
                "  max(challenged_at) AS last, "
                "  (array_agg(challenge_id ORDER BY challenged_at DESC))"
                "  [1:3] AS ids FROM karen_challenges GROUP BY target_agent")
            direction = "GIVEN"
        else:
            rows = await conn.fetch(
                "SELECT 'KAREN' AS cp, count(*) AS n, "
                "  count(*) FILTER (WHERE state='UPHELD') AS upheld, "
                "  count(*) FILTER (WHERE state='REJECTED') AS rejected, "
                "  max(challenged_at) AS last, "
                "  (array_agg(challenge_id ORDER BY challenged_at DESC))"
                "  [1:3] AS ids FROM karen_challenges WHERE target_agent=$1 "
                " HAVING count(*) > 0", a)
            direction = "RECEIVED"
        for r in rows:
            add(r["cp"], "CHALLENGE", direction, r["n"], r["last"],
                [{"kind": "karen_challenges", "id": i} for i in r["ids"]],
                "karen_challenges", upheld=int(r["upheld"]),
                rejected=int(r["rejected"]))
        ev = await conn.fetch(
            "SELECT CASE WHEN upper(btrim(resolved_by))=$1 THEN target_agent "
            "            ELSE upper(btrim(resolved_by)) END AS cp, "
            "       CASE WHEN upper(btrim(resolved_by))=$1 THEN 'GIVEN' "
            "            ELSE 'RECEIVED' END AS dir, count(*) AS n, "
            "       max(resolved_at) AS last, "
            "       (array_agg(challenge_id ORDER BY resolved_at DESC))[1:3] "
            "       AS ids FROM karen_challenges "
            " WHERE state IN ('UPHELD','REJECTED') "
            "   AND (upper(btrim(resolved_by))=$1 OR target_agent=$1) "
            " GROUP BY 1, 2", a)
        for r in ev:
            add(r["cp"], "CHALLENGE_EVALUATION", r["dir"], r["n"], r["last"],
                [{"kind": "karen_challenges", "id": i} for i in r["ids"]],
                "karen_challenges.resolved_by")
        return True

    async def handoffs():
        if a not in ("DEREK", "XAVIER"):
            return True
        r = await conn.fetchrow(
            "SELECT count(*) AS n, max(created_at) AS last, "
            "  (array_agg(handoff_id ORDER BY created_at DESC))[1:3] AS ids "
            "  FROM paper_handoffs")
        add("XAVIER" if a == "DEREK" else "DEREK", "POSITION_HANDOFF",
            "GIVEN" if a == "DEREK" else "RECEIVED", r["n"], r["last"],
            [{"kind": "paper_handoffs", "id": i} for i in (r["ids"] or [])],
            "paper_handoffs", book="PAPER")
        return True

    async def estimates():
        if a not in ("DEREK", "EDDIE"):
            return True
        r = await conn.fetchrow(
            "SELECT count(*) AS n, max(estimated_at) AS last, "
            "  (array_agg(estimate_id ORDER BY estimated_at DESC))[1:3] "
            "  AS ids FROM eddie_execution_estimates")
        add("EDDIE" if a == "DEREK" else "DEREK", "EXECUTION_ESTIMATE",
            "RECEIVED" if a == "DEREK" else "GIVEN", r["n"], r["last"],
            [{"kind": "eddie_execution_estimates", "id": i}
             for i in (r["ids"] or [])], "eddie_execution_estimates")
        return True

    async def pos_chain():
        rows = await conn.fetch(
            "SELECT p.agent AS prev, s.agent AS cur, count(*) AS n, "
            "       max(s.at) AS last "
            "  FROM pos_candidate_review_steps s "
            "  JOIN pos_candidate_review_steps p "
            "    ON p.review_id = s.review_id AND p.seq = s.seq - 1 "
            " WHERE s.agent=$1 OR p.agent=$1 GROUP BY 1, 2", a)
        for r in rows:
            mine = r["cur"] == a
            add(r["prev"] if mine else r["cur"], "CANDIDATE_REVIEW",
                "RECEIVED" if mine else "GIVEN", r["n"], r["last"], [],
                "pos_candidate_review_steps")
        return True

    async def loop():
        rows = await conn.fetch(
            "SELECT CASE WHEN upper(s.actor)=$1 THEN upper(f.proposer) "
            "            ELSE upper(s.actor) END AS cp, "
            "       CASE WHEN upper(s.actor)=$1 THEN 'GIVEN' "
            "            ELSE 'RECEIVED' END AS dir, count(*) AS n, "
            "       max(s.at) AS last FROM agent_finding_stages s "
            "  JOIN agent_findings f USING (finding_id) "
            " WHERE upper(s.actor) <> upper(f.proposer) "
            "   AND (upper(s.actor)=$1 OR upper(f.proposer)=$1) "
            " GROUP BY 1, 2", a)
        for r in rows:
            add(r["cp"], "COLLABORATION_LOOP", r["dir"], r["n"], r["last"],
                [], "agent_finding_stages")
        return True

    async def messages():
        rows = await conn.fetch(
            "SELECT CASE WHEN from_agent=$1 THEN to_agent ELSE from_agent "
            "       END AS cp, CASE WHEN from_agent=$1 THEN 'GIVEN' "
            "       ELSE 'RECEIVED' END AS dir, message_kind, count(*) AS n,"
            "       max(created_at) AS last, "
            "       (array_agg(message_id ORDER BY created_at DESC))[1:3] "
            "       AS ids FROM agent_conversation_messages "
            " WHERE from_agent=$1 OR to_agent=$1 GROUP BY 1, 2, 3", a)
        for r in rows:
            add(r["cp"], r["message_kind"], r["dir"], r["n"], r["last"],
                [{"kind": "agent_conversation_messages", "id": i}
                 for i in r["ids"]], "agent_conversation_messages")
        return True

    async def memories():
        rows = await conn.fetch(
            "SELECT facts ->> 'counterpart' AS cp, count(*) AS n, "
            "       max(learned_at) AS last, "
            "       (array_agg(memory_id ORDER BY learned_at DESC))[1:3] "
            "       AS ids FROM agent_memory_events "
            " WHERE agent_id=$1 AND memory_kind='RELATIONSHIP' "
            "   AND facts ? 'counterpart' GROUP BY 1", a)
        for r in rows:
            add(r["cp"], "RELATIONSHIP_MEMORY", "OWN", r["n"], r["last"],
                [{"kind": "agent_memory_events", "id": i}
                 for i in r["ids"]], "agent_memory_events")
        return True

    for name, tables, fn in (
            ("karen_challenges", ("karen_challenges",), karen),
            ("paper_handoffs", ("paper_handoffs",), handoffs),
            ("eddie_execution_estimates", ("eddie_execution_estimates",),
             estimates),
            ("pos_candidate_review_steps", ("pos_candidate_review_steps",),
             pos_chain),
            ("agent_finding_stages", ("agent_finding_stages",
                                      "agent_findings"), loop),
            ("agent_conversation_messages", ("agent_conversation_messages",),
             messages),
            ("agent_memory_events", ("agent_memory_events",), memories)):
        st, _v, why = await _guarded(conn, tables, fn)
        sections[name] = {"status": st, "why": why}
    rels = sorted(acc.values(), key=lambda r: (-r["total"],
                                               r["counterpart"]))
    return {"schema": "bettor.agent.relationships.v1", "agent": a,
            "relationships": rels, "sources": sections,
            "derived_from": "durable interaction records only",
            "computed_at": float(now if now is not None else time.time())}


# ═════════════════════════════════════════════════════════════════════
# 4 · THE EVALUATION MATRIX
# ═════════════════════════════════════════════════════════════════════

DIMENSIONS = ("factual_accuracy", "citation_evidence_coverage",
              "stale_evidence_handling", "authority_compliance",
              "role_adherence", "calibration", "economic_contribution",
              "challenge_quality", "self_correction_rate")


def _dim(name, status, value=None, why=None, source=None, definition=None,
         **kw) -> dict:
    return dict({"dimension": name, "status": status, "value": value,
                 "why": why, "source": source, "definition": definition},
                **kw)


async def evaluation(conn, agent: str, *, now: float | None = None) -> dict:
    a = I.agent_of(agent)
    if a is None:
        raise ValueError(I.R_UNKNOWN_AGENT)
    b = AC.bundle(a)
    rows: list = []
    rows.append(_dim(
        "factual_accuracy", UNAVAILABLE,
        why="NO_GRADED_ANSWER_RECORD: no durable record grades this agent's "
            "statements against ground truth",
        definition="share of graded statements that were correct"))

    st, mem, why = await _guarded(conn, ("agent_memory_events",),
                                  lambda: conn.fetchrow(
        "SELECT count(*) AS n, "
        "  count(*) FILTER (WHERE jsonb_array_length(evidence_refs) >= 1) "
        "  AS cited, "
        "  count(*) FILTER (WHERE memory_kind='SELF_CORRECTION') AS sc, "
        "  count(*) FILTER (WHERE subject_type = ANY($2::text[])) AS inm "
        "  FROM agent_memory_events WHERE agent_id=$1", a,
        list(M.MANDATE[a])))
    n = int(mem["n"]) if mem else 0
    if st != OK:
        rows.append(_dim("citation_evidence_coverage", st, why=why))
    elif n == 0:
        rows.append(_dim("citation_evidence_coverage", UNAVAILABLE,
                         why="NO_MEMORY_RECORDED",
                         source="agent_memory_events"))
    else:
        rows.append(_dim("citation_evidence_coverage", MEASURED,
                         round(int(mem["cited"]) / n, 6),
                         source="agent_memory_events", n=n,
                         definition="share of the agent's memories citing "
                                    ">= 1 existing evidence record"))

    if a == "XAVIER":
        st2, x, why2 = await _guarded(
            conn, ("xavier_management_assessments",), lambda: conn.fetchrow(
                "SELECT count(*) AS n, count(*) FILTER (WHERE "
                "  recommendation IS NULL OR recommendation = ANY($1)) AS ok "
                "  FROM xavier_management_assessments "
                " WHERE evidence_state <> 'FRESH_CURRENT_PROBABILITY' "
                "   AND to_jsonb(xavier_management_assessments) "
                "       ->> 'recommendation_state' IS NOT NULL",
                list(XF.NON_ACTIONS)))
        if st2 != OK:
            rows.append(_dim("stale_evidence_handling", st2, why=why2))
        elif not int(x["n"]):
            rows.append(_dim("stale_evidence_handling", UNAVAILABLE,
                             why="NO_NON_FRESH_ASSESSMENT_SINCE_222",
                             source="xavier_management_assessments"))
        else:
            rows.append(_dim(
                "stale_evidence_handling", MEASURED,
                round(int(x["ok"]) / int(x["n"]), 6), n=int(x["n"]),
                source="xavier_management_assessments",
                definition="share of non-fresh assessments (recorded since "
                           "migration 222) that recorded no management "
                           "action (WAITING_FOR_FRESH_EVIDENCE / "
                           "MANAGEMENT_UNAVAILABLE_STALE_INPUT)"))
    else:
        rows.append(_dim("stale_evidence_handling", NOT_APPLICABLE,
                         why="NOT_A_MANAGEMENT_ROLE"))

    forbidden = sorted(set(b["tools"]) & set(AC.AUTHORITY_TOOLS)
                       - {"request.funded_entry", "dispatch.xavier_claim",
                          "write.policy_candidates", "write.directives"})
    rows.append(_dim(
        "authority_compliance", STRUCTURAL,
        {"forbidden_tools_granted": forbidden,
         "authority_status": b["authority_status"],
         "order_path": b["order_path"]},
        source="registry.IDENTITIES / identity.ALLOCATOR_TOOLS",
        definition="no NEVER_GRANTED or capital / approval / promotion tool "
                   "in the agent's bundle (structural, not a measured rate)"))
    if st != OK:
        rows.append(_dim("role_adherence", st, why=why))
    elif n == 0:
        rows.append(_dim("role_adherence", UNAVAILABLE,
                         why="NO_MEMORY_RECORDED"))
    else:
        rows.append(_dim("role_adherence", MEASURED,
                         round(int(mem["inm"]) / n, 6), n=n,
                         source="agent_memory_events",
                         definition="share of memories whose subject is in "
                                    "the agent's mandate"))

    if a == "DEREK":
        st3, c, why3 = await _guarded(
            conn, ("paper_settlements", "paper_handoffs", "paper_decisions"),
            lambda: conn.fetchrow(
                "SELECT count(*) AS n, avg((p - won) ^ 2) AS brier FROM ("
                " SELECT DISTINCT ON (d.decision_id) d.p_blended AS p, "
                "   CASE WHEN s.outcome='WON' THEN 1.0 ELSE 0.0 END AS won "
                "  FROM paper_settlements s JOIN paper_handoffs h "
                "    ON h.group_id = s.group_id JOIN paper_decisions d "
                "    ON d.decision_id = h.decision_id "
                " WHERE d.verdict='ENTER' AND s.holding_side=d.holding_side "
                "   AND d.p_blended IS NOT NULL "
                "   AND s.outcome IN ('WON','LOST') "
                " ORDER BY d.decision_id, s.version DESC) q"))
        if st3 != OK:
            rows.append(_dim("calibration", st3, why=why3))
        elif int(c["n"]) < M.LESSON_MIN_SAMPLE:
            rows.append(_dim("calibration", UNAVAILABLE,
                             why="INSUFFICIENT_SAMPLE:n=%d<%d" % (
                                 int(c["n"]), M.LESSON_MIN_SAMPLE),
                             source="paper_settlements"))
        else:
            rows.append(_dim("calibration", MEASURED,
                             round(float(c["brier"]), 6), n=int(c["n"]),
                             source="paper_settlements + paper_decisions",
                             definition="Brier score of p_blended over "
                                        "settled ENTER decisions (lower is "
                                        "better)"))
    elif a == "EDDIE":
        st3, c, why3 = await _guarded(
            conn, ("eddie_execution_outcomes",), lambda: conn.fetchrow(
                "SELECT count(*) AS n, avg(abs(realized_execution_loss_pp - "
                "  predicted_execution_loss_pp)) AS mae "
                "  FROM eddie_execution_outcomes "
                " WHERE realized_execution_loss_pp IS NOT NULL "
                "   AND predicted_execution_loss_pp IS NOT NULL"))
        if st3 != OK:
            rows.append(_dim("calibration", st3, why=why3))
        elif int(c["n"]) < M.LESSON_MIN_SAMPLE:
            rows.append(_dim("calibration", UNAVAILABLE,
                             why="INSUFFICIENT_SAMPLE:n=%d<%d" % (
                                 int(c["n"]), M.LESSON_MIN_SAMPLE)))
        else:
            rows.append(_dim("calibration", MEASURED,
                             round(float(c["mae"]), 6), n=int(c["n"]),
                             source="eddie_execution_outcomes",
                             definition="mean absolute error of predicted vs "
                                        "realized execution loss (pp)"))
    elif a == "SCOUT":
        st3, c, why3 = await _guarded(
            conn, ("scout_feature_tournaments",), lambda: conn.fetchrow(
                "SELECT count(*) AS n, count(*) FILTER (WHERE "
                "  verdict='VALIDATED') AS v FROM scout_feature_tournaments "
                " WHERE verdict IS NOT NULL"))
        if st3 != OK:
            rows.append(_dim("calibration", st3, why=why3))
        elif not int(c["n"]):
            rows.append(_dim("calibration", UNAVAILABLE,
                             why="NO_EVALUATED_TOURNAMENT"))
        else:
            rows.append(_dim("calibration", MEASURED,
                             round(int(c["v"]) / int(c["n"]), 6),
                             n=int(c["n"]),
                             source="scout_feature_tournaments",
                             definition="share of evaluated hypotheses the "
                                        "evaluator VALIDATED"))
    else:
        rows.append(_dim("calibration", NOT_APPLICABLE,
                         why="NO_PROBABILISTIC_OUTPUT_IN_THIS_ROLE"))

    detail = None
    if a == "XAVIER":
        st4, nv, _w = await _guarded(conn, ("xavier_value_add",),
                                     lambda: conn.fetchval(
            "SELECT count(*) FROM xavier_value_add WHERE status='FINAL'"))
        detail = {"xavier_value_add_final_records": nv if st4 == OK
                  else None}
    rows.append(_dim(
        "economic_contribution", UNAVAILABLE,
        why="NO_PER_AGENT_ECONOMIC_ATTRIBUTION_RECORD: no durable record "
            "attributes realized P&L to this agent's own contribution",
        detail=detail))

    if a == "KAREN":
        st5, k, why5 = await _guarded(conn, ("karen_challenges",),
                                      lambda: conn.fetchrow(
            "SELECT count(*) FILTER (WHERE state='UPHELD') AS up, "
            "  count(*) FILTER (WHERE state IN ('UPHELD','REJECTED')) AS n "
            "  FROM karen_challenges"))
        if st5 != OK:
            rows.append(_dim("challenge_quality", st5, why=why5))
        elif not int(k["n"]):
            rows.append(_dim("challenge_quality", UNAVAILABLE,
                             why="NO_RESOLVED_CHALLENGE"))
        else:
            rows.append(_dim("challenge_quality", MEASURED,
                             round(int(k["up"]) / int(k["n"]), 6),
                             n=int(k["n"]), source="karen_challenges",
                             definition="UPHELD / (UPHELD + REJECTED), "
                                        "resolved by someone other than "
                                        "Karen"))
    else:
        rows.append(_dim("challenge_quality", NOT_APPLICABLE,
                         why="NOT_THE_RED_TEAM"))

    if st != OK:
        rows.append(_dim("self_correction_rate", st, why=why))
    elif n == 0:
        rows.append(_dim("self_correction_rate", UNAVAILABLE,
                         why="NO_MEMORY_RECORDED"))
    else:
        rows.append(_dim("self_correction_rate", MEASURED,
                         round(int(mem["sc"]) / n, 6), n=n,
                         source="agent_memory_events",
                         definition="SELF_CORRECTION memories / all "
                                    "memories"))
    return {"schema": "bettor.agent.evaluation.v1", "agent": a,
            "matrix": rows, "single_score": None,
            "single_score_why": "NO_VANITY_SCORE: dimensions are reported "
                                "separately",
            "computed_at": float(now if now is not None else time.time())}
