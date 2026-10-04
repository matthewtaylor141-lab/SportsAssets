"""THE SEVEN AGENTS' DURABLE WORK QUEUES (owner R30 program section 17,
migration 234) -- BUILT ON XAVIER'S 226 QUEUE, NOT BESIDE IT.

THE DEFECT. Only Xavier had durable work (agents/work_queue.py: his
fresh-evidence requests). Everything else an agent owed was a COUNT read at
display time (production 2026-10-04, research-sql run 37226555657): Derek's
22 stale-probability refusals in 24 h, Eddie's estimates without an outcome,
Scout's two features under test, Karen's detector candidates deferred by her
open-challenge cap, every challenge waiting for an answer or an evaluation,
Audrey's open discrepancies -- no owner, no SLA, no last or next attempt,
nothing an overdue item could be seen by. "Xavier is idle" was the visible
symptom; the cause was that no agent's work was a record.

WHAT THIS IS. One queue for all seven agents: the SAME tables (agent_work_
requests / agent_work_request_events / agent_work_open, one open item per
(agent, subject, kind) by the database's primary key), the SAME lifecycle
(ENQUEUED -> attempts -> exactly one COMPLETED naming its evidence row, or
FAILED naming why), the same writers for completion and failure
(work_queue.complete / fail). Migration 234 adds the owner's terms to every
item -- blocker, SLA (due_at, counted from when the work AROSE), dependency,
last / next attempt, evidence needed, collaborator -- and the repeatable
ATTEMPTED event.

THE PRODUCERS (each agent's runner calls `sync` with its kinds at the end of
its pass; every backlog is a bounded SELECT over records the agent's own
work writes):

  DEREK            CANDIDATE_FRESH_EVIDENCE  his latest paper decision on a
                   strategy x market x side REFUSED on stale / unknown-
                   freshness probability evidence (derek_policy.R_STALE /
                   R_FRESHNESS_UNKNOWN). COMPLETED by a later decision on
                   that candidate that was not refused on freshness; a later
                   decision still refused on it is an ATTEMPT (WAITING).
                   The open items are the hot-candidate list the freshness
                   service reads (`hot_candidate_slugs`).
  XAVIER           (226, unchanged) PROBABILITY / VENUE_BOOK / GAME_STATE /
                   MANAGEMENT_REASSESSMENT through work_queue.after_review /
                   drain; plus his challenge answers / evaluations below.
  DEREK, XAVIER,   CHALLENGE_RESPONSE  a karen_challenges row OPEN against
  AUDREY, ALLIE    the agent. COMPLETED by its recorded peer response;
                   FAILED if withdrawn. Collaborator KAREN.
  AUDREY, XAVIER   CHALLENGE_EVALUATION  a RESPONDED challenge the agent
                   evaluates (karen.EVALUATOR_FOR). COMPLETED by the outcome;
                   depends on the target's CHALLENGE_RESPONSE item.
  KAREN            CHALLENGE_INVESTIGATION  a candidate her detector found
                   that the pass did not open (the open-challenge cap, the
                   per-pass budget). PUSHED by karen_runner. COMPLETED by the
                   challenge that opened it; FAILED when the same rule no
                   longer holds for the record.
  CHIEF_ALLOCATOR  ALLOCATION_REVIEW  a paper ENTER decision recorded after
                   her latest allocation run started. COMPLETED by the first
                   OK allocation run that started after it (its allocation
                   row named when one exists).
  EDDIE            EXECUTION_ESTIMATE  an ENTER decision in his lookback with
                   no estimate (this estimator version). COMPLETED by the
                   estimate; FAILED when it leaves the window. Refusals are
                   BLOCKED attempts.
                   OUTCOME_CALIBRATION  an estimate whose decision filled on
                   paper with no PAPER outcome. COMPLETED by the outcome.
  SCOUT            RESEARCH_QUESTION  a feature UNDER_TEST with an open
                   tournament (awaiting prospective samples). Each pass is an
                   attempt (PROGRESSED when samples grew, else WAITING with
                   the sample count against the predeclared minimum).
                   COMPLETED by the evaluator's verdict.
  AUDREY           AUDIT_RECONCILIATION  a small-live reconciliation in
                   DISCREPANCY. COMPLETED when it reconciles.
                   ROOT_CAUSE_TRIAGE  an open root-cause cluster
                   (agents/improvement_clusters.py, refreshed on the same
                   paper pass) with no effective fix: BLOCKED on the
                   engineering fix, WAITING on post-fix evidence once one is
                   linked; COMPLETED when the fix is measured effective or a
                   person closes the cluster.

An item stays open past its SLA (OVERDUE, visible) until its hard horizon
(expires_at); then it is FAILED (EXPIRED_UNRESOLVED) and, while its subject
is still pending, enqueued again on the next pass with a fresh horizon (its
SLA still counted from when the work arose).

BOUNDED: per-kind backlog limits, at most MAX_ENQUEUE_PER_KIND new items and
MAX_ATTEMPTS_PER_KIND attempts per pass, the open-slot primary key, the
database's per-request attempt bound.

NO AUTHORITY. It writes only the three agent_work_* tables (through the
work_queue writers for terminal events). It imports no order, venue,
execution, funded or paper module; it places, cancels and sizes nothing and
changes no threshold. NEVER RAISES into a runner: a failure is returned by
name.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from .. import agent_work_state as AWS
from . import work_queue as WQ

log = logging.getLogger(__name__)

VERSION = "AGENT_WORK_QUEUES_V1"
AGENTS = AWS.AGENTS

O_PROGRESSED, O_BLOCKED, O_WAITING, O_NO_CHANGE = (
    "PROGRESSED", "BLOCKED", "WAITING_FOR_FRESH_EVIDENCE", "NO_CHANGE")
OUTCOMES = (O_PROGRESSED, O_BLOCKED, O_WAITING, O_NO_CHANGE)

K_CANDIDATE = "CANDIDATE_FRESH_EVIDENCE"
K_RESPONSE = "CHALLENGE_RESPONSE"
K_EVALUATION = "CHALLENGE_EVALUATION"
K_INVESTIGATION = "CHALLENGE_INVESTIGATION"
K_ALLOCATION = "ALLOCATION_REVIEW"
K_ESTIMATE = "EXECUTION_ESTIMATE"
K_CALIBRATION = "OUTCOME_CALIBRATION"
K_RESEARCH = "RESEARCH_QUESTION"
K_RECONCILIATION = "AUDIT_RECONCILIATION"
K_ROOT_CAUSE = "ROOT_CAUSE_TRIAGE"

#: an open item of one of these IS an enqueued reacquisition (= agent_work_
#: state.ACQUISITION_KINDS; a test pins both)
ACQUISITION_KINDS = WQ.EVIDENCE_KINDS + (WQ.K_REASSESS, K_CANDIDATE,
                                         K_RESEARCH)

F_EXPIRED = "EXPIRED_UNRESOLVED"
F_WITHDRAWN = "CHALLENGE_WITHDRAWN"
F_RULE_GONE = "RULE_NO_LONGER_HOLDS"
F_LEFT_WINDOW = "LEFT_THE_ESTIMATION_WINDOW"
F_LEFT_BACKLOG = "SUBJECT_LEFT_THE_BACKLOG_WITHOUT_EVIDENCE"

R_NO_SCHEMA = "MIGRATION_234_NOT_APPLIED"
R_NOT_OWNER = "AGENT_DOES_NOT_OWN_THIS_KIND"

#: derek_policy.R_STALE / R_FRESHNESS_UNKNOWN (copied: importing the policy
#: module would pull the decision path into this one; a test pins them)
STALE_REFUSALS = ("PROBABILITY_EVIDENCE_STALE",
                  "PROBABILITY_EVIDENCE_FRESHNESS_UNKNOWN")
#: = agents.karen.EVALUATOR_FOR (agent_work_state reads the same; pinned)
EVALUATOR_FOR = AWS.EVALUATOR_FOR
CHALLENGE_TARGETS = ("DEREK", "XAVIER", "AUDREY", "CHIEF_ALLOCATOR")
#: = agents.eddie.VERSION and eddie_runner.LOOKBACK_S (pinned by a test)
EDDIE_ESTIMATOR_VERSION = "EDDIE_EXECUTION_ESTIMATOR_V1"
EDDIE_LOOKBACK_S = AWS.EDDIE_LOOKBACK_S

DAY = 86400.0
#: THE TERMS PER KIND: owners, subject kind, what raised it, SLA (seconds
#: after the work arose), hard horizon (seconds after enqueue), the evidence
#: that completes it, the default next attempt, and the collaborator.
KINDS: dict = {
    K_CANDIDATE: {
        "agents": ("DEREK",), "scope": "MARKET",
        "reason": "REFUSED_ON_STALE_PROBABILITY", "sla_s": 900.0,
        "ttl_s": 6 * 3600.0, "retry_s": 60.0,
        "evidence": ["FRESH_PINNACLE_PROBABILITY",
                     "DECISION_ON_FRESH_EVIDENCE"],
        "collaborator": None, "backlog_limit": 200},
    K_RESPONSE: {
        "agents": CHALLENGE_TARGETS, "scope": "CHALLENGE",
        "reason": "KAREN_CHALLENGE_OPEN", "sla_s": 3600.0,
        "ttl_s": 7 * DAY, "retry_s": 120.0,
        "evidence": ["PEER_RESPONSE_UNDER_THE_SAME_RULE"],
        "collaborator": "KAREN", "backlog_limit": 300},
    K_EVALUATION: {
        "agents": ("AUDREY", "XAVIER"), "scope": "CHALLENGE",
        "reason": "CHALLENGE_RESPONDED", "sla_s": 3600.0,
        "ttl_s": 7 * DAY, "retry_s": 120.0,
        "evidence": ["INDEPENDENT_EVALUATION_UNDER_THE_SAME_RULE"],
        "collaborator": None, "backlog_limit": 300},
    K_INVESTIGATION: {
        "agents": ("KAREN",), "scope": "RECORD",
        "reason": "DETECTOR_CANDIDATE_DEFERRED", "sla_s": 3600.0,
        "ttl_s": 7 * DAY, "retry_s": 300.0,
        "evidence": ["GROUNDED_CHALLENGE_RECORD"],
        "collaborator": None, "backlog_limit": 0},
    K_ALLOCATION: {
        "agents": ("CHIEF_ALLOCATOR",), "scope": "DECISION",
        "reason": "ENTER_AFTER_LAST_ALLOCATION_RUN", "sla_s": 1200.0,
        "ttl_s": 2 * DAY, "retry_s": 600.0,
        "evidence": ["SHADOW_ALLOCATION_RUN"],
        "collaborator": "DEREK", "backlog_limit": 200},
    K_ESTIMATE: {
        "agents": ("EDDIE",), "scope": "DECISION",
        "reason": "ENTER_WITHOUT_ESTIMATE", "sla_s": 600.0,
        "ttl_s": 2 * DAY, "retry_s": 300.0,
        "evidence": ["EXECUTION_ESTIMATE", "DECISION_TIME_BOOK"],
        "collaborator": "DEREK", "backlog_limit": 200},
    K_CALIBRATION: {
        "agents": ("EDDIE",), "scope": "ESTIMATE",
        "reason": "ESTIMATE_FILLED_WITHOUT_OUTCOME", "sla_s": 3600.0,
        "ttl_s": 7 * DAY, "retry_s": 300.0,
        "evidence": ["PAPER_FILLS", "EXECUTION_OUTCOME"],
        "collaborator": "DEREK", "backlog_limit": 200},
    K_RESEARCH: {
        "agents": ("SCOUT",), "scope": "FEATURE",
        "reason": "FEATURE_UNDER_TEST", "sla_s": 14 * DAY,
        "ttl_s": 30 * DAY, "retry_s": 600.0,
        "evidence": ["PROSPECTIVE_SAMPLES", "SETTLED_OUTCOMES",
                     "EVALUATOR_VERDICT"],
        "collaborator": "KAREN", "backlog_limit": 100},
    K_RECONCILIATION: {
        "agents": ("AUDREY",), "scope": "RECONCILIATION",
        "reason": "RECONCILIATION_DISCREPANCY", "sla_s": 3600.0,
        "ttl_s": 7 * DAY, "retry_s": 600.0,
        "evidence": ["MATCHED_RECONCILIATION"],
        "collaborator": "XAVIER", "backlog_limit": 100},
    K_ROOT_CAUSE: {
        "agents": ("AUDREY",), "scope": "CLUSTER",
        "reason": "ROOT_CAUSE_CLUSTER_OPEN", "sla_s": 3 * DAY,
        "ttl_s": 30 * DAY, "retry_s": 3600.0,
        "evidence": ["LINKED_FIX_COMMIT", "MEASURED_EFFECT_AFTER_THE_FIX"],
        "collaborator": None, "backlog_limit": 100},
}
#: which kinds each runner syncs
RUNNER_KINDS = {
    # Allie's allocation runs are the shadow intelligence runner's, whose
    # import closure is held to the standard library and its own package
    # (tests/test_intel_is_shadow_only.py); her queue is synced on the paper
    # pass that records the ENTER decisions she reviews, and her runs'
    # outcomes (intel_runs) complete or block each item
    "paper_pass": (K_CANDIDATE, K_RECONCILIATION, K_ALLOCATION,
                   K_ROOT_CAUSE),
    "karen_runner": (K_INVESTIGATION,),
    "peer_responder": (K_RESPONSE, K_EVALUATION),
    "eddie_runner": (K_ESTIMATE, K_CALIBRATION),
    "scout_runner": (K_RESEARCH,),
}
MAX_ENQUEUE_PER_KIND = 50
MAX_ATTEMPTS_PER_KIND = 100
MAX_OPEN_READ = 500
MAX_SUBJECT = 200


class _Deduped(Exception):
    """An open item of this (agent, subject, kind) exists: roll back."""


def _ep(v):
    return AWS._ep(v)


def _j(v):
    return AWS._j(v)


def _h(*parts) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()
                          ).hexdigest()[:24]


def subject_key(*parts) -> str:
    """The subject key of an item (the 226 group_id column): its parts
    joined by '|', shortened with a digest past MAX_SUBJECT characters (the
    column's CHECK). Pure and stable."""
    k = "|".join(str(p) for p in parts)
    if len(k) <= MAX_SUBJECT:
        return k
    return k[:MAX_SUBJECT - 26] + "#" + _h(k)


def terms(kind: str, *, at: float, arose_at: float | None) -> dict:
    """THE SLA AND HORIZON of an item enqueued at `at` for work that arose
    at `arose_at`. Pure. The SLA is counted from when the work arose (so a
    backlog older than the queue is overdue at once, never re-dated); the
    horizon from the enqueue; the SLA never exceeds the horizon and never
    reaches back further than the database allows (30 days)."""
    spec = KINDS[kind]
    expires = at + float(spec["ttl_s"])
    a = float(arose_at) if arose_at is not None else at
    due = min(max(a + float(spec["sla_s"]), at - 30 * DAY + 1.0), expires)
    return {"enqueued_at": at, "due_at": due, "expires_at": expires}


async def has_schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            " WHERE table_name = 'agent_work_request_events' "
            "   AND column_name = 'next_attempt_at')"))
    except Exception:                                           # noqa: BLE001
        return False


async def _exists(conn, table: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        table))
    except Exception:                                           # noqa: BLE001
        return False


# ═════════════════════════════════════════════════════════════════════
# THE WRITES (each in its own savepoint: a refusal never aborts a caller)
# ═════════════════════════════════════════════════════════════════════

async def enqueue_item(conn, *, kind: str, agent: str, subject: str,
                       at: float, arose_at: float | None,
                       source_table: str | None, source_id,
                       batch_id: str, slug: str | None = None,
                       blocker: str | None = None,
                       depends_on: str | None = None,
                       collaborator: str | None = None,
                       detail: dict | None = None) -> dict:
    """ONE ITEM, deduplicated by the database (one open per agent, subject,
    kind), with every term. Never raises."""
    out: dict[str, Any] = {"kind": kind, "agent": agent, "subject": subject,
                           "enqueued": False}
    spec = KINDS.get(kind)
    if spec is None or agent not in spec["agents"]:
        return dict(out, why=R_NOT_OWNER)
    t = terms(kind, at=at, arose_at=arose_at)
    collab = collaborator if collaborator is not None else spec[
        "collaborator"]
    if collab == agent:
        collab = None
    rid = "awr:" + _h(agent, spec["scope"], subject, kind, at)
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO agent_work_requests (request_id, agent_id, kind,"
                " position_kind, group_id, us_market_slug, reason, "
                " source_table, source_id, batch_id, enqueued_at, expires_at,"
                " detail, due_at, blocker, depends_on, collaborator, "
                " evidence_needed, next_attempt_at) VALUES ($1,$2,$3,$4,$5,"
                " $6,$7,$8,$9,$10,to_timestamp($11),to_timestamp($12),"
                " $13::jsonb,to_timestamp($14),$15,$16,$17,$18::jsonb,"
                " to_timestamp($11))",
                rid, agent, kind, spec["scope"], subject, slug,
                spec["reason"], source_table,
                None if source_id is None else str(source_id), batch_id,
                t["enqueued_at"], t["expires_at"],
                json.dumps(dict(detail or {}, version=VERSION,
                                arose_at=arose_at), default=str),
                t["due_at"], blocker, depends_on, collab,
                json.dumps(list(spec["evidence"])))
            got = await conn.fetchval(
                "INSERT INTO agent_work_open (agent_id, position_kind, "
                " group_id, kind, request_id, opened_at) VALUES "
                " ($1,$2,$3,$4,$5,to_timestamp($6)) "
                "ON CONFLICT DO NOTHING RETURNING request_id",
                agent, spec["scope"], subject, kind, rid, at)
            if got is None:
                raise _Deduped()
            await conn.execute(
                "INSERT INTO agent_work_request_events (request_id, state, "
                " at, detail) VALUES ($1,'ENQUEUED',to_timestamp($2),"
                " $3::jsonb)", rid, at,
                json.dumps({"reason": spec["reason"], "batch_id": batch_id,
                            "due_at": t["due_at"]}))
        return dict(out, enqueued=True, request_id=rid, due_at=t["due_at"])
    except _Deduped:
        return dict(out, why=WQ.R_DEDUPED)
    except Exception as exc:                                    # noqa: BLE001
        log.info("agent_work: enqueue %s/%s refused (%s)", agent, kind,
                 type(exc).__name__)
        return dict(out, why="ENQUEUE_FAILED", error=type(exc).__name__)


async def attempt(conn, request_id: str, *, at: float, outcome: str,
                  next_in_s: float, blocker: str | None = None,
                  detail: dict | None = None) -> bool:
    """THE CONSUMER'S ATTEMPT on an open item and its outcome, with the next
    attempt scheduled. Never raises (False when refused)."""
    if outcome not in OUTCOMES:
        return False
    if outcome == O_BLOCKED and not blocker:
        blocker = "UNSPECIFIED_BLOCKER"
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO agent_work_request_events (request_id, state, "
                " at, outcome, blocker, next_attempt_at, detail) VALUES "
                " ($1,'ATTEMPTED',to_timestamp($2),$3,$4,to_timestamp($5),"
                " $6::jsonb)", request_id, at, outcome,
                None if blocker is None else str(blocker)[:200],
                at + max(1.0, float(next_in_s)),
                json.dumps(detail or {}, default=str))
        return True
    except Exception as exc:                                    # noqa: BLE001
        log.info("agent_work: attempt on %s refused (%s)", request_id,
                 type(exc).__name__)
        return False


complete = WQ.complete
fail = WQ.fail


async def open_items(conn, *, agent: str | None = None, kinds=None,
                     now: float | None = None,
                     limit: int = MAX_OPEN_READ) -> list:
    """Open items with their terms (agent_work_state.queue_item views)."""
    at = float(now if now is not None else time.time())
    rows = await conn.fetch(AWS.OPEN_ITEMS_SQL, agent,
                            None if kinds is None else list(kinds),
                            int(limit))
    return [AWS.queue_item(dict(r), at) for r in rows]


async def _latest_request(conn, *, kind: str, subject: str) -> str | None:
    return await conn.fetchval(
        "SELECT request_id FROM agent_work_requests WHERE kind = $1 "
        "   AND group_id = $2 ORDER BY enqueued_at DESC LIMIT 1",
        kind, subject)


# ═════════════════════════════════════════════════════════════════════
# THE PRODUCERS: backlog (what is pending now) and resolution (what an open
# item's own records say about it). Each returns plain dicts; SELECT only.
# ═════════════════════════════════════════════════════════════════════

def _subj(row: dict, **kw) -> dict:
    return dict(row, **kw)


async def _backlog_candidate(conn, now: float, limit: int) -> list:
    if not await _exists(conn, "paper_decisions"):
        return []
    rows = await conn.fetch(
        "SELECT * FROM (SELECT DISTINCT ON (coalesce(strategy, ''), "
        "       us_market_slug, holding_side) decision_id, strategy, "
        "       us_market_slug, holding_side, decided_at, refusal, refusals "
        "  FROM paper_decisions WHERE us_market_slug IS NOT NULL "
        "   AND decided_at BETWEEN to_timestamp($1) AND to_timestamp($2) "
        " ORDER BY coalesce(strategy, ''), us_market_slug, holding_side, "
        "          decided_at DESC, decision_id DESC) q "
        " WHERE q.refusals && $3::text[] "
        " ORDER BY q.decided_at LIMIT $4",
        now - KINDS[K_CANDIDATE]["ttl_s"], now, list(STALE_REFUSALS),
        limit)
    out = []
    for r in rows:
        stale = [x for x in (r["refusals"] or []) if x in STALE_REFUSALS]
        out.append({"agent": "DEREK",
                    "subject": subject_key(r["strategy"] or "-",
                                           r["us_market_slug"],
                                           r["holding_side"] or "-"),
                    "arose_at": _ep(r["decided_at"]),
                    "source_table": "paper_decisions",
                    "source_id": r["decision_id"],
                    "slug": r["us_market_slug"], "blocker": stale[0],
                    "detail": {"strategy": r["strategy"],
                               "holding_side": r["holding_side"],
                               "refusals": stale}})
    return out


async def _resolve_candidate(conn, items: list, now: float) -> dict:
    out: dict = {}
    for it in items:
        d = it.get("detail") or {}
        since = _ep(d.get("arose_at"))
        rows = await conn.fetch(
            "SELECT decision_id, decided_at, refusals FROM paper_decisions "
            " WHERE us_market_slug = $1 "
            "   AND coalesce(strategy, '-') = $2 "
            "   AND coalesce(holding_side, '-') = $3 "
            "   AND decided_at >= to_timestamp($4) "
            " ORDER BY decided_at DESC, decision_id DESC LIMIT 1",
            it.get("slug"), d.get("strategy") or "-",
            d.get("holding_side") or "-",
            since if since is not None else float(it["enqueued_at"]))
        if not rows:
            continue
        r = rows[0]
        # the item's own decision, or the one its last attempt recorded:
        # nothing new
        if r["decision_id"] in (it.get("source_id"), (it.get(
                "last_attempt_detail") or {}).get("decision_id")):
            continue
        stale = [x for x in (r["refusals"] or []) if x in STALE_REFUSALS]
        if stale:
            out[it["request_id"]] = ("ATTEMPT", O_WAITING, stale[0], {
                "decision_id": r["decision_id"],
                "decided_at": _ep(r["decided_at"]),
                "via": "the candidate was re-decided and refused on "
                       "freshness again"})
        else:
            out[it["request_id"]] = ("COMPLETED", "paper_decisions",
                                     r["decision_id"], {
                                         "decided_at": _ep(r["decided_at"]),
                                         "refusals": list(r["refusals"]
                                                          or [])})
    return out


async def _backlog_response(conn, now: float, limit: int) -> list:
    if not await _exists(conn, "karen_challenges"):
        return []
    rows = await conn.fetch(
        "SELECT challenge_id, target_agent, detector, target_kind, "
        "       target_id, challenged_at FROM karen_challenges "
        " WHERE state = 'OPEN' AND target_agent = ANY($1::text[]) "
        " ORDER BY challenged_at, challenge_id LIMIT $2",
        list(CHALLENGE_TARGETS), limit)
    return [{"agent": r["target_agent"], "subject": r["challenge_id"],
             "arose_at": _ep(r["challenged_at"]),
             "source_table": "karen_challenges",
             "source_id": r["challenge_id"],
             "detail": {"detector": r["detector"],
                        "target_kind": r["target_kind"],
                        "target_id": r["target_id"]}} for r in rows]


async def _resolve_challenge(conn, items: list, now: float, *,
                             done_states: tuple) -> dict:
    out: dict = {}
    ids = [it["subject"] for it in items]
    if not ids:
        return out
    rows = {r["challenge_id"]: r for r in await conn.fetch(
        "SELECT challenge_id, state, response_stance, responded_at, "
        "       outcome, resolved_by, resolved_at FROM karen_challenges "
        " WHERE challenge_id = ANY($1::text[])", ids)}
    for it in items:
        r = rows.get(it["subject"])
        if r is None:
            out[it["request_id"]] = ("FAILED", F_LEFT_BACKLOG,
                                     {"why": "CHALLENGE_NOT_FOUND"})
        elif r["state"] == "WITHDRAWN":
            out[it["request_id"]] = ("FAILED", F_WITHDRAWN, {})
        elif r["state"] in done_states:
            out[it["request_id"]] = ("COMPLETED", "karen_challenges",
                                     r["challenge_id"], {
                                         "state": r["state"],
                                         "stance": r["response_stance"],
                                         "outcome": r["outcome"],
                                         "resolved_by": r["resolved_by"]})
    return out


async def _backlog_evaluation(conn, now: float, limit: int) -> list:
    if not await _exists(conn, "karen_challenges"):
        return []
    rows = await conn.fetch(
        "SELECT challenge_id, target_agent, detector, responded_at "
        "  FROM karen_challenges WHERE state = 'RESPONDED' "
        "   AND target_agent = ANY($1::text[]) "
        " ORDER BY responded_at, challenge_id LIMIT $2",
        list(EVALUATOR_FOR), limit)
    out = []
    for r in rows:
        ev = EVALUATOR_FOR.get(r["target_agent"])
        if ev not in KINDS[K_EVALUATION]["agents"]:
            continue
        out.append({"agent": ev, "subject": r["challenge_id"],
                    "arose_at": _ep(r["responded_at"]),
                    "source_table": "karen_challenges",
                    "source_id": r["challenge_id"],
                    "collaborator": r["target_agent"],
                    "depends_on": await _latest_request(
                        conn, kind=K_RESPONSE, subject=r["challenge_id"]),
                    "detail": {"detector": r["detector"],
                               "target_agent": r["target_agent"],
                               "challenger": "KAREN"}})
    return out


async def _resolve_investigation(conn, items: list, now: float) -> dict:
    out: dict = {}
    for it in items:
        d = it.get("detail") or {}
        cid = await conn.fetchval(
            "SELECT challenge_id FROM karen_challenges WHERE detector = $1 "
            "   AND target_kind = $2 AND target_id = $3",
            d.get("detector"), d.get("target_kind"), str(d.get("target_id")))
        if cid is not None:
            out[it["request_id"]] = ("COMPLETED", "karen_challenges", cid, {
                "detector": d.get("detector")})
            continue
        try:
            from . import karen_runner as KR
            holds = await KR.rule_holds(conn, d.get("detector"),
                                        d.get("target_kind"),
                                        str(d.get("target_id")))
        except Exception:                                       # noqa: BLE001
            holds = None
        if holds is False:
            out[it["request_id"]] = ("FAILED", F_RULE_GONE, {
                "detector": d.get("detector")})
    return out


async def _backlog_allocation(conn, now: float, limit: int) -> list:
    if not (await _exists(conn, "intel_runs")
            and await _exists(conn, "paper_decisions")):
        return []
    last = await conn.fetchrow(
        "SELECT run_id, started_at FROM intel_runs "
        " WHERE component = 'ALLOCATOR' ORDER BY started_at DESC LIMIT 1")
    if last is None:
        return []
    rows = await conn.fetch(
        "SELECT decision_id, decided_at, us_market_slug, strategy "
        "  FROM paper_decisions WHERE verdict = 'ENTER' "
        "   AND decided_at > $1 AND decided_at <= to_timestamp($2) "
        "   AND decided_at >= to_timestamp($3) "
        " ORDER BY decided_at, decision_id LIMIT $4",
        last["started_at"], now, now - KINDS[K_ALLOCATION]["ttl_s"], limit)
    return [{"agent": "CHIEF_ALLOCATOR", "subject": r["decision_id"],
             "arose_at": _ep(r["decided_at"]),
             "source_table": "paper_decisions",
             "source_id": r["decision_id"], "slug": r["us_market_slug"],
             "detail": {"strategy": r["strategy"],
                        "last_allocation_run": last["run_id"]}}
            for r in rows]


async def _resolve_allocation(conn, items: list, now: float) -> dict:
    out: dict = {}
    for it in items:
        decided = await conn.fetchval(
            "SELECT decided_at FROM paper_decisions WHERE decision_id = $1",
            it["subject"])
        if decided is None:
            continue
        run = await conn.fetchrow(
            "SELECT run_id, status FROM intel_runs "
            " WHERE component = 'ALLOCATOR' AND started_at > $1 "
            "   AND finished_at IS NOT NULL AND status = 'OK' "
            " ORDER BY started_at LIMIT 1", decided)
        if run is None:
            bad = await conn.fetchrow(
                "SELECT run_id, status FROM intel_runs "
                " WHERE component = 'ALLOCATOR' AND started_at > $1 "
                "   AND finished_at IS NOT NULL AND status <> 'OK' "
                "   AND started_at > to_timestamp($2) "
                " ORDER BY started_at DESC LIMIT 1", decided,
                float(it.get("last_attempt_at") or 0.0))
            if bad is not None:
                out[it["request_id"]] = ("ATTEMPT", O_BLOCKED,
                                         "ALLOCATOR_RUN_%s" % bad["status"],
                                         {"run_id": bad["run_id"]})
            continue
        alloc = None
        if await _exists(conn, "intel_allocations"):
            alloc = await conn.fetchrow(
                "SELECT run_id, candidate_id, shadow_weight, "
                "       binding_constraint FROM intel_allocations "
                " WHERE run_id = $1 AND decision_id = $2 LIMIT 1",
                run["run_id"], it["subject"])
        if alloc is not None:
            out[it["request_id"]] = ("COMPLETED", "intel_allocations",
                                     "%s|%s" % (alloc["run_id"],
                                                alloc["candidate_id"]), {
                                         "shadow_weight": alloc[
                                             "shadow_weight"],
                                         "binding_constraint": alloc[
                                             "binding_constraint"]})
        else:
            out[it["request_id"]] = ("COMPLETED", "intel_runs",
                                     run["run_id"], {
                                         "allocated": False,
                                         "why": "THE_RUN_THAT_STARTED_AFTER_"
                                                "THE_DECISION_RANKED_IT_"
                                                "WITHOUT_AN_ALLOCATION_ROW"})
    return out


async def _backlog_estimate(conn, now: float, limit: int) -> list:
    if not (await _exists(conn, "paper_decisions")
            and await _exists(conn, "eddie_execution_estimates")):
        return []
    rows = await conn.fetch(
        "SELECT d.decision_id, d.decided_at, d.us_market_slug, d.strategy "
        "  FROM paper_decisions d WHERE d.verdict = 'ENTER' "
        "   AND d.decided_at BETWEEN to_timestamp($1) AND to_timestamp($2) "
        "   AND NOT EXISTS (SELECT 1 FROM eddie_execution_estimates e "
        "        WHERE e.decision_id = d.decision_id "
        "          AND e.estimator_version = $3) "
        " ORDER BY d.decided_at, d.decision_id LIMIT $4",
        now - EDDIE_LOOKBACK_S, now, EDDIE_ESTIMATOR_VERSION, limit)
    return [{"agent": "EDDIE", "subject": r["decision_id"],
             "arose_at": _ep(r["decided_at"]),
             "source_table": "paper_decisions",
             "source_id": r["decision_id"], "slug": r["us_market_slug"],
             "detail": {"strategy": r["strategy"]}} for r in rows]


async def _resolve_estimate(conn, items: list, now: float) -> dict:
    out: dict = {}
    for it in items:
        r = await conn.fetchrow(
            "SELECT e.estimate_id, e.recommendation, d.decided_at "
            "  FROM paper_decisions d LEFT JOIN eddie_execution_estimates e "
            "    ON e.decision_id = d.decision_id "
            "   AND e.estimator_version = $2 "
            " WHERE d.decision_id = $1 LIMIT 1", it["subject"],
            EDDIE_ESTIMATOR_VERSION)
        if r is None:
            continue
        if r["estimate_id"] is not None:
            out[it["request_id"]] = ("COMPLETED",
                                     "eddie_execution_estimates",
                                     r["estimate_id"], {
                                         "recommendation":
                                             r["recommendation"]})
        elif _ep(r["decided_at"]) is not None and \
                now - _ep(r["decided_at"]) > EDDIE_LOOKBACK_S:
            out[it["request_id"]] = ("FAILED", F_LEFT_WINDOW, {
                "lookback_s": EDDIE_LOOKBACK_S})
    return out


async def _backlog_calibration(conn, now: float, limit: int) -> list:
    if not (await _exists(conn, "eddie_execution_estimates")
            and await _exists(conn, "eddie_execution_outcomes")):
        return []
    rows = await conn.fetch(
        "SELECT e.estimate_id, e.decision_id, "
        "       (SELECT min(f.filled_at) FROM paper_orders o JOIN "
        "        paper_fills f USING (order_id) WHERE o.decision_id = "
        "        e.decision_id) AS first_fill_at "
        "  FROM eddie_execution_estimates e WHERE NOT EXISTS ("
        "       SELECT 1 FROM eddie_execution_outcomes x "
        "        WHERE x.estimate_id = e.estimate_id AND x.source = 'PAPER')"
        "   AND EXISTS (SELECT 1 FROM paper_orders o JOIN paper_fills f "
        "        USING (order_id) WHERE o.decision_id = e.decision_id) "
        " ORDER BY e.estimated_at LIMIT $1", limit)
    out = []
    for r in rows:
        out.append({"agent": "EDDIE", "subject": r["estimate_id"],
                    "arose_at": _ep(r["first_fill_at"]),
                    "source_table": "eddie_execution_estimates",
                    "source_id": r["estimate_id"],
                    "depends_on": await _latest_request(
                        conn, kind=K_ESTIMATE, subject=r["decision_id"]),
                    "detail": {"decision_id": r["decision_id"]}})
    return out


async def _resolve_calibration(conn, items: list, now: float) -> dict:
    out: dict = {}
    for it in items:
        oid = await conn.fetchval(
            "SELECT outcome_id FROM eddie_execution_outcomes "
            " WHERE estimate_id = $1 AND source = 'PAPER' LIMIT 1",
            it["subject"])
        if oid is not None:
            out[it["request_id"]] = ("COMPLETED", "eddie_execution_outcomes",
                                     oid, {})
    return out


async def _backlog_research(conn, now: float, limit: int) -> list:
    if not (await _exists(conn, "scout_features")
            and await _exists(conn, "scout_feature_tournaments")):
        return []
    rows = await conn.fetch(
        "SELECT f.feature_id, f.feature, f.proposed_at, f.state_set_at, "
        "       t.tournament_id, t.min_sample, t.frozen_at "
        "  FROM scout_features f JOIN scout_feature_tournaments t "
        "    USING (feature_id) "
        " WHERE f.state = 'UNDER_TEST' AND t.verdict IS NULL "
        " ORDER BY t.frozen_at, f.feature_id LIMIT $1", limit)
    return [{"agent": "SCOUT", "subject": r["feature_id"],
             "arose_at": _ep(r["frozen_at"]),
             "source_table": "scout_feature_tournaments",
             "source_id": r["tournament_id"],
             "blocker": "AWAITING_PROSPECTIVE_SAMPLES",
             "detail": {"feature": r["feature"],
                        "tournament_id": r["tournament_id"],
                        "min_sample": r["min_sample"]}} for r in rows]


async def research_progress(conn, tournament_id: str) -> dict:
    """The frozen tournament's sample count (all / with outcome) against its
    predeclared minimum. SELECT only."""
    r = await conn.fetchrow(
        "SELECT t.min_sample, (SELECT count(*) FROM scout_tournament_samples"
        "       s WHERE s.tournament_id = t.tournament_id) AS n, "
        "       (SELECT count(*) FROM scout_tournament_samples s "
        "         WHERE s.tournament_id = t.tournament_id "
        "           AND s.outcome IS NOT NULL) AS n_settled "
        "  FROM scout_feature_tournaments t WHERE t.tournament_id = $1",
        tournament_id)
    if r is None:
        return {}
    return {"samples": int(r["n"]), "settled": int(r["n_settled"]),
            "min_sample": int(r["min_sample"])}


async def _resolve_research(conn, items: list, now: float) -> dict:
    out: dict = {}
    for it in items:
        r = await conn.fetchrow(
            "SELECT t.tournament_id, t.verdict, t.verdict_reason, f.state "
            "  FROM scout_feature_tournaments t JOIN scout_features f "
            "    USING (feature_id) WHERE t.feature_id = $1", it["subject"])
        if r is None:
            continue
        if r["verdict"] is not None:
            out[it["request_id"]] = ("COMPLETED",
                                     "scout_feature_tournaments",
                                     r["tournament_id"], {
                                         "verdict": r["verdict"],
                                         "reason": r["verdict_reason"]})
        elif r["state"] != "UNDER_TEST":
            out[it["request_id"]] = ("FAILED", F_LEFT_BACKLOG, {
                "feature_state": r["state"]})
    return out


async def _backlog_reconciliation(conn, now: float, limit: int) -> list:
    if not await _exists(conn, "smalllive_reconciliations"):
        return []
    rows = await conn.fetch(
        "SELECT group_id, venue, reconciled_at, discrepancies "
        "  FROM smalllive_reconciliations WHERE status = 'DISCREPANCY' "
        " ORDER BY reconciled_at, group_id LIMIT $1", limit)
    return [{"agent": "AUDREY", "subject": r["group_id"],
             "arose_at": _ep(r["reconciled_at"]),
             "source_table": "smalllive_reconciliations",
             "source_id": r["group_id"],
             "detail": {"venue": r["venue"],
                        "discrepancies": len(_j(r["discrepancies"]) or [])}}
            for r in rows]


async def _resolve_reconciliation(conn, items: list, now: float) -> dict:
    out: dict = {}
    for it in items:
        r = await conn.fetchrow(
            "SELECT status, reconciled_at FROM smalllive_reconciliations "
            " WHERE group_id = $1", it["subject"])
        if r is not None and r["status"] == "MATCHED":
            out[it["request_id"]] = ("COMPLETED",
                                     "smalllive_reconciliations",
                                     "%s@%s" % (it["subject"],
                                                _ep(r["reconciled_at"])), {})
    return out


async def _backlog_root_cause(conn, now: float, limit: int) -> list:
    if not await _exists(conn, "improvement_clusters"):
        return []
    rows = await conn.fetch(
        "SELECT c.cluster_id, c.cluster_key, c.owner_agent, c.first_seen_at,"
        "       c.opened_at, s.status_to FROM improvement_clusters c "
        "  LEFT JOIN LATERAL (SELECT status_to FROM "
        "       improvement_cluster_events e WHERE e.cluster_id = "
        "       c.cluster_id ORDER BY e.at DESC, e.event_id DESC LIMIT 1) s "
        "    ON true "
        " WHERE coalesce(s.status_to, 'OPEN') IN ('OPEN', 'FIX_LINKED', "
        "       'FIX_NOT_EFFECTIVE') "
        " ORDER BY c.first_seen_at, c.cluster_id LIMIT $1", limit)
    return [{"agent": "AUDREY", "subject": r["cluster_id"],
             "arose_at": _ep(r["opened_at"]),
             "source_table": "improvement_clusters",
             "source_id": r["cluster_id"],
             "collaborator": r["owner_agent"]
             if r["owner_agent"] != "AUDREY" else "KAREN",
             "blocker": ("AWAITING_POST_FIX_EVIDENCE"
                         if r["status_to"] == "FIX_LINKED"
                         else "AWAITING_ENGINEERING_FIX"),
             "detail": {"cluster_key": r["cluster_key"],
                        "status": r["status_to"] or "OPEN"}} for r in rows]


async def _resolve_root_cause(conn, items: list, now: float) -> dict:
    out: dict = {}
    for it in items:
        r = await conn.fetchrow(
            "SELECT event_id, kind, status_to FROM improvement_cluster_events"
            " WHERE cluster_id = $1 ORDER BY at DESC, event_id DESC LIMIT 1",
            it["subject"])
        if r is None:
            continue
        if r["status_to"] in ("FIX_EFFECTIVE", "CLOSED"):
            out[it["request_id"]] = ("COMPLETED",
                                     "improvement_cluster_events",
                                     r["event_id"], {
                                         "status": r["status_to"]})
        elif r["status_to"] == "FIX_LINKED":
            out[it["request_id"]] = ("ATTEMPT", O_WAITING,
                                     "AWAITING_POST_FIX_EVIDENCE",
                                     {"event_id": r["event_id"]})
        else:
            out[it["request_id"]] = ("ATTEMPT", O_BLOCKED,
                                     "AWAITING_ENGINEERING_FIX" if r[
                                         "status_to"] == "OPEN" else
                                     "FIX_NOT_EFFECTIVE",
                                     {"event_id": r["event_id"]})
    return out


async def _resolve_response(conn, items, now):
    return await _resolve_challenge(conn, items, now, done_states=(
        "RESPONDED", "UPHELD", "REJECTED"))


async def _resolve_evaluation(conn, items, now):
    return await _resolve_challenge(conn, items, now, done_states=(
        "UPHELD", "REJECTED"))


async def _no_backlog(conn, now, limit):
    return []


PRODUCERS = {
    K_CANDIDATE: (_backlog_candidate, _resolve_candidate),
    K_RESPONSE: (_backlog_response, _resolve_response),
    K_EVALUATION: (_backlog_evaluation, _resolve_evaluation),
    K_INVESTIGATION: (_no_backlog, _resolve_investigation),
    K_ALLOCATION: (_backlog_allocation, _resolve_allocation),
    K_ESTIMATE: (_backlog_estimate, _resolve_estimate),
    K_CALIBRATION: (_backlog_calibration, _resolve_calibration),
    K_RESEARCH: (_backlog_research, _resolve_research),
    K_RECONCILIATION: (_backlog_reconciliation, _resolve_reconciliation),
    K_ROOT_CAUSE: (_backlog_root_cause, _resolve_root_cause),
}


# ═════════════════════════════════════════════════════════════════════
# THE PASS: expire -> resolve -> enqueue -> record the runner's attempts
# ═════════════════════════════════════════════════════════════════════

async def sync(conn, kinds, *, now: float | None = None,
               attempts: dict | None = None,
               pushed: dict | None = None) -> dict:
    """ONE BOUNDED PASS OVER THE NAMED KINDS. Never raises.

    `attempts` {kind: {subject: {"outcome", "blocker", "detail",
    "next_in_s"}}} -- what the runner just tried on an item's subject.
    `pushed` {kind: [backlog subjects]} -- a backlog only the runner knows
    (Karen's deferred detector candidates)."""
    t = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "at": t, "kinds": {}}
    try:
        if not await has_schema(conn):
            return dict(out, refusal=R_NO_SCHEMA)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, error=type(exc).__name__)
    batch = "awq:%s" % _h("sync", tuple(kinds), t)
    for kind in kinds:
        rep = {"expired": 0, "completed": 0, "failed": 0, "attempted": 0,
               "enqueued": 0, "deduped": 0, "refused": {}}
        out["kinds"][kind] = rep
        try:
            await _sync_kind(conn, kind, t, batch, rep,
                             (attempts or {}).get(kind) or {},
                             (pushed or {}).get(kind))
        except Exception as exc:                                # noqa: BLE001
            rep["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    return out


async def _sync_kind(conn, kind, t, batch, rep, runner_attempts, pushed):
    spec = KINDS[kind]
    backlog_fn, resolve_fn = PRODUCERS[kind]
    items = await open_items(conn, kinds=[kind], now=t)
    live = []
    # 1 · past the hard horizon: FAILED (it was overdue since due_at)
    for it in items:
        if it.get("expires_at") is not None and t > it["expires_at"]:
            if await fail(conn, it["request_id"], at=t, failure=F_EXPIRED,
                          detail={"due_at": it.get("due_at"),
                                  "overdue_s": it.get("overdue_s"),
                                  "attempts": it.get("attempts")}):
                rep["expired"] += 1
            continue
        live.append(it)
    # 2 · what the items' own records say
    res = await resolve_fn(conn, live, t) if live else {}
    attempted_now: set = set()
    still = []
    for it in live:
        r = res.get(it["request_id"])
        if r is None:
            still.append(it)
            continue
        if r[0] == "COMPLETED":
            if await complete(conn, it["request_id"], at=t,
                              evidence_table=r[1], evidence_id=r[2],
                              detail=r[3]):
                rep["completed"] += 1
        elif r[0] == "FAILED":
            if await fail(conn, it["request_id"], at=t, failure=r[1],
                          detail=r[2]):
                rep["failed"] += 1
        else:                                   # ("ATTEMPT", outcome, ...)
            still.append(it)
            if rep["attempted"] < MAX_ATTEMPTS_PER_KIND and await attempt(
                    conn, it["request_id"], at=t, outcome=r[1],
                    blocker=r[2], next_in_s=spec["retry_s"], detail=r[3]):
                rep["attempted"] += 1
                attempted_now.add(it["request_id"])
    # 3 · what is pending and not yet enqueued
    open_keys = {(it["owner"], it["subject"]) for it in still}
    backlog = list(pushed or []) if pushed is not None else \
        await backlog_fn(conn, t, int(spec["backlog_limit"]))
    n_new = 0
    for s in backlog:
        if n_new >= MAX_ENQUEUE_PER_KIND:
            break
        key = (s["agent"], s["subject"])
        if key in open_keys:
            continue
        got = await enqueue_item(
            conn, kind=kind, agent=s["agent"], subject=s["subject"], at=t,
            arose_at=s.get("arose_at"), source_table=s.get("source_table"),
            source_id=s.get("source_id"), batch_id=batch,
            slug=s.get("slug"), blocker=s.get("blocker"),
            depends_on=s.get("depends_on"),
            collaborator=s.get("collaborator"), detail=s.get("detail"))
        if got["enqueued"]:
            n_new += 1
            rep["enqueued"] += 1
            open_keys.add(key)
            still.append({"request_id": got["request_id"],
                          "owner": s["agent"], "subject": s["subject"]})
        elif got.get("why") == WQ.R_DEDUPED:
            rep["deduped"] += 1
        else:
            rep["refused"][got.get("why") or "UNKNOWN"] = rep["refused"].get(
                got.get("why") or "UNKNOWN", 0) + 1
    # 4 · what the runner just tried (once per item per pass); "*" is an
    # attempt on every open item of the kind (a pass that could not run)
    by_subject = {it["subject"]: it for it in still}
    if "*" in runner_attempts:
        runner_attempts = dict({k: runner_attempts["*"] for k in by_subject},
                               **{k: v for k, v in runner_attempts.items()
                                  if k != "*"})
    for subj, a in runner_attempts.items():
        it = by_subject.get(subj)
        if it is None or it["request_id"] in attempted_now or \
                rep["attempted"] >= MAX_ATTEMPTS_PER_KIND:
            continue
        if await attempt(conn, it["request_id"], at=t,
                         outcome=a.get("outcome") or O_PROGRESSED,
                         blocker=a.get("blocker"),
                         next_in_s=float(a.get("next_in_s")
                                         or spec["retry_s"]),
                         detail=a.get("detail")):
            rep["attempted"] += 1
            attempted_now.add(it["request_id"])


async def sync_for(conn, runner: str, **kw) -> dict:
    """`sync` with the kinds a named runner owns (RUNNER_KINDS)."""
    return await sync(conn, RUNNER_KINDS[runner], **kw)


#: = bettor_paper_ledger.ACCOUNT_ID (copied, not imported: this module
#: imports no paper module; a test pins equality)
MAIN_PAPER_ACCOUNT = "paper_acct_main"
#: the paper pass runs every few seconds; its queue step at most this often
STEP_EVERY_S = 60.0
_LAST_STEP: dict = {}


async def step(conn, ctx: dict) -> dict:
    """THE PAPER-PASS HOOK (Derek's candidates, Audrey's reconciliations),
    on the main paper account's pass only, at most every STEP_EVERY_S in
    this process. Never raises."""
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    at = float(clock())
    if ctx.get("account_id") != MAIN_PAPER_ACCOUNT and \
            not ctx.get("agent_work_any_account"):
        return {"ran": False, "why": "NOT_THE_MAIN_PAPER_ACCOUNT"}
    last = _LAST_STEP.get("paper_pass")
    if last is not None and 0 <= at - last < STEP_EVERY_S:
        return {"ran": False, "why": "NOT_DUE", "last_at": last}
    _LAST_STEP["paper_pass"] = at
    got = await sync_for(conn, "paper_pass", now=at)
    return dict(got, ran=True)


async def research_attempts(conn, *, now: float) -> dict:
    """SCOUT'S ATTEMPT ON EACH RESEARCH QUESTION this pass: the frozen
    tournament's samples against its predeclared minimum -- PROGRESSED when
    the sample or settled count grew since the item's last attempt, else
    WAITING_FOR_FRESH_EVIDENCE (awaiting prospective samples). SELECT only.
    {feature_id: attempt}."""
    out: dict = {}
    try:
        backlog = await _backlog_research(conn, now, int(
            KINDS[K_RESEARCH]["backlog_limit"]))
        last = {i["subject"]: i.get("last_attempt_detail") or {}
                for i in await open_items(conn, agent="SCOUT",
                                          kinds=[K_RESEARCH], now=now)}
    except Exception:                                           # noqa: BLE001
        return out
    for b in backlog:
        prog = await research_progress(conn, b["detail"]["tournament_id"])
        if not prog:
            continue
        prev = last.get(b["subject"]) or {}
        grew = (prog["samples"] > int(prev.get("samples") or 0)
                or prog["settled"] > int(prev.get("settled") or 0))
        out[b["subject"]] = {
            "outcome": O_PROGRESSED if grew else O_WAITING,
            "blocker": None if grew else "AWAITING_PROSPECTIVE_SAMPLES",
            "detail": prog}
    return out


# ═════════════════════════════════════════════════════════════════════
# CONSUMER READS
# ═════════════════════════════════════════════════════════════════════

async def hot_candidate_slugs(conn, *, limit: int = 20) -> list:
    """THE HOT-CANDIDATE LIST for the freshness service: the slugs of
    Derek's open CANDIDATE_FRESH_EVIDENCE items, most overdue first, at most
    `limit` distinct. Never raises ([] without the schema)."""
    try:
        if not await has_schema(conn):
            return []
        items = await open_items(conn, agent="DEREK", kinds=[K_CANDIDATE])
    except Exception:                                           # noqa: BLE001
        return []
    out: list = []
    for it in sorted(items, key=lambda i: (i.get("due_at") or 0,
                                           i.get("request_id") or "")):
        s = it.get("slug")
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


async def queue_view(conn, *, now: float | None = None) -> dict:
    """EVERY AGENT'S OPEN QUEUE with its terms and overdue counts (read
    only): {agent: {"items": [...], "summary": {...}}}."""
    t = float(now if now is not None else time.time())
    items = await open_items(conn, now=t, limit=AWS.MAX_QUEUE_ITEMS)
    out = {}
    for a in AGENTS:
        mine = [i for i in items if i.get("owner") == a]
        out[a] = {"items": mine,
                  "summary": AWS.queue_classes(a, mine, t)["summary"]}
    return out
