"""EACH AGENT'S OWN MEMORY: EVIDENCE-GROUNDED, PRIVATE, APPEND-ONLY (224).

WHAT A MEMORY IS. A row of `agent_memory_events` that one agent learned from
DURABLE records -- a settlement, a review, a challenge outcome, an execution
outcome, a tournament verdict -- with at least one evidence reference that
EXISTS, a recorded confidence and the identity version that learned it.
Nothing is hand-entered and no LLM output becomes memory because it sounds
useful: the only writer is `promote()`, and the only callers are the
deterministic derivers below (run by the scheduled `step`).

PROMOTION (section 3 of the directive). A candidate becomes memory only when
  * every evidence reference names an allow-listed record kind and the
    record exists (`verify_evidence`);
  * it is relevant to the agent's mandate (`MANDATE`: the subject types each
    agent may remember);
  * its confidence is recorded (0 < c <= 1);
  * it does not contradict a NEWER durable memory of the same claim; an
    OLDER contradicted memory is never deleted: a SELF_CORRECTION row is
    written and the old row's `superseded_by` is set (the table permits
    exactly that one update);
  * for Xavier, it honours the Candidate 28 current-state rule: an action
    word only on a CURRENT review (else WAITING_FOR_FRESH_EVIDENCE), and
    protection only from FILLED quantity -- a resting order never reduces
    unprotected inventory (`xavier_fact_guard`, over xavier_freshness and
    order_state_truth).

PRIVACY (section 2). An agent reads shared objective facts, ITS OWN memory
and hand-offs addressed to it. `private_memories(reader=..., owner=...)`
refuses any agent reading another agent's memory (`PrivateMemoryRefused`);
the human operator's Command session reads as OPERATOR. If one agent's
lesson matters to another, `share_memory` writes a MEMORY_HANDOFF message
(the database refuses sharing a memory the sender does not own).

LEARNING IS NOT POLICY (section 13). A memory may inform a hypothesis, a
research prompt, a tournament or a challenge. Nothing here reads a memory
into a threshold, a model, capital, a strategy or Small Live, and the only
tables written are agent_memory_events, agent_conversation_messages and
this learner's own ingestion_state watermark (tests pin the SQL).

The same outcome teaches each agent something different (section 4): Derek
the entry he priced, Xavier the management state and filled protection at
settlement, Audrey whether the books reconciled, Karen whether her
challenge was upheld, the Allocator what his SHADOW ranking did, Eddie the
realized vs predicted execution loss, Scout whether his feature validated.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from typing import Any

from .. import order_state_truth as OST
from .. import xavier_freshness as XF
from . import identity as I

log = logging.getLogger(__name__)

VERSION = "AGENT_MEMORY_V1"
OPERATOR = "OPERATOR"

EPISODIC, LESSON, RELATIONSHIP = "EPISODIC", "LESSON", "RELATIONSHIP"
PREFERENCE, CASE, SELF_CORRECTION = "PREFERENCE", "CASE", "SELF_CORRECTION"
KINDS = (EPISODIC, LESSON, RELATIONSHIP, PREFERENCE, CASE, SELF_CORRECTION)

R_NO_SCHEMA = "MIGRATION_224_NOT_APPLIED"
R_UNKNOWN_AGENT = I.R_UNKNOWN_AGENT
R_UNKNOWN_KIND = "NOT_A_MEMORY_KIND"
R_NO_EVIDENCE = "MEMORY_REQUIRES_EVIDENCE"
R_BAD_EVIDENCE = "EVIDENCE_REF_MUST_NAME_AN_ALLOWED_KIND_AND_ID"
R_EVIDENCE_MISSING = "EVIDENCE_RECORD_DOES_NOT_EXIST"
R_NO_CONFIDENCE = "CONFIDENCE_MUST_BE_RECORDED_IN_(0,1]"
R_OUT_OF_MANDATE = "SUBJECT_IS_NOT_IN_THIS_AGENTS_MANDATE"
R_NO_SUMMARY = "SUMMARY_REQUIRED"
R_AUTHORITY = "MEMORY_CANNOT_CARRY_AUTHORITY"
R_CONTRADICTED = "CONTRADICTED_BY_A_NEWER_DURABLE_MEMORY"
R_ALREADY_KNOWN = "ALREADY_KNOWN"
R_XAVIER_STALE_ACTION = "XAVIER_ACTION_WORD_ONLY_ON_A_CURRENT_REVIEW"
R_RESTING_IS_NOT_PROTECTION = "RESTING_QUANTITY_COUNTED_AS_PROTECTION"
R_PRIVATE = "ANOTHER_AGENTS_PRIVATE_MEMORY"
R_NO_SUCH_MEMORY = "NO_SUCH_MEMORY_FOR_THIS_AGENT"
R_ALREADY_SUPERSEDED = "MEMORY_ALREADY_SUPERSEDED"
R_WRITE_FAILED = "MEMORY_WRITE_FAILED"


class PrivateMemoryRefused(PermissionError):
    """An agent asked for another agent's private memory."""


#: Every record kind a memory or message may cite: kind -> (table, key).
#: A composite key is written "a/b" in the reference id.
EVIDENCE_TABLES: dict[str, tuple] = {
    "paper_decisions": ("paper_decisions", ("decision_id",)),
    "paper_handoffs": ("paper_handoffs", ("handoff_id",)),
    "paper_settlements": ("paper_settlements", ("settlement_id",)),
    "paper_xavier_reviews": ("paper_xavier_reviews", ("review_id",)),
    "paper_orders": ("paper_orders", ("order_id",)),
    "paper_fills": ("paper_fills", ("fill_id",)),
    "paper_audrey_findings": ("paper_audrey_findings", ("finding_id",)),
    "paper_audrey_reports": ("paper_audrey_reports", ("report_id",)),
    "paper_agent_lessons": ("paper_agent_lessons", ("lesson_id",)),
    "audrey_audit_reports": ("audrey_audit_reports", ("report_id",)),
    "xavier_management_assessments": ("xavier_management_assessments",
                                      ("assessment_id",)),
    "xavier_value_add": ("xavier_value_add", ("value_add_id",)),
    "karen_challenges": ("karen_challenges", ("challenge_id",)),
    "eddie_execution_estimates": ("eddie_execution_estimates",
                                  ("estimate_id",)),
    "eddie_execution_outcomes": ("eddie_execution_outcomes",
                                 ("outcome_id",)),
    "scout_features": ("scout_features", ("feature_id",)),
    "scout_feature_tournaments": ("scout_feature_tournaments",
                                  ("tournament_id",)),
    "intel_runs": ("intel_runs", ("run_id",)),
    "intel_allocations": ("intel_allocations", ("run_id", "candidate_id")),
    "agent_decisions": ("agent_decisions", ("decision_ref",)),
    "agent_findings": ("agent_findings", ("finding_id",)),
    "agent_memory_events": ("agent_memory_events", ("memory_id",)),
    "agent_conversation_messages": ("agent_conversation_messages",
                                    ("message_id",)),
}

#: WHAT EACH AGENT MAY REMEMBER: the subject types inside its mandate.
MANDATE: dict[str, tuple] = {
    "DEREK": ("paper_decisions", "entry_calibration", "karen_challenges"),
    "XAVIER": ("paper_position", "paper_xavier_reviews",
               "xavier_management_assessments", "karen_challenges"),
    "AUDREY": ("paper_audrey_reports", "paper_audrey_findings",
               "karen_challenges", "audrey_audit_reports"),
    "KAREN": ("karen_challenges", "karen_detector"),
    "CHIEF_ALLOCATOR": ("intel_allocations", "karen_challenges"),
    "EDDIE": ("eddie_execution_estimates", "execution_calibration"),
    "SCOUT": ("scout_feature_tournaments", "scout_features"),
}

#: Keys a memory's facts may never carry: a memory is not a setting.
AUTHORITY_KEYS = frozenset({
    "threshold", "new_threshold", "set_threshold", "limit_change",
    "new_limit", "activate", "activation", "promote", "promotion",
    "approve", "approval", "approved", "capital_change", "allocate_capital",
    "deploy", "credential", "place_order", "cancel_order", "submit",
    "policy_activation", "model_promotion", "small_live"})

LESSON_MIN_SAMPLE = 30
DERIVE_LIMIT = 50
RUN_EVERY_S = 600.0
WATERMARK_KEY = "agent_memory_learner"


# ═════════════════════════════════════════════════════════════════════
# 1 · PURE HELPERS AND VALIDATION
# ═════════════════════════════════════════════════════════════════════

def _ep(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return v.timestamp()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _f(v):
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def memory_id_for(c: dict) -> str:
    raw = "|".join(str(c.get(k) or "") for k in (
        "agent_id", "memory_kind", "subject_type", "subject_id", "claim_key",
        "claim_value", "deriver"))
    return "mem-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def wilson(k: int, n: int, z: float = 1.959964) -> tuple | None:
    """The 95% Wilson score interval of k successes in n trials."""
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _find_authority_keys(v, path="") -> list:
    out = []
    if isinstance(v, dict):
        for k, x in v.items():
            if str(k).lower() in AUTHORITY_KEYS:
                out.append(path + str(k))
            out += _find_authority_keys(x, path + str(k) + ".")
    elif isinstance(v, list):
        for x in v:
            out += _find_authority_keys(x, path)
    return out


def xavier_fact_guard(facts: dict) -> str | None:
    """THE C28 RULES INSIDE XAVIER'S MEMORY. None when the facts honour
    them, else the named refusal:
      * an action word (HOLD / EXIT / REDUCE / ...) only with
        recommendation_state CURRENT -- a stale review is
        WAITING_FOR_FRESH_EVIDENCE, never HOLD;
      * protection is FILLED quantity only: when the orders and held
        quantity are given, `order_state_truth.protection_summary` is
        recomputed and the stated filled protection / unprotected quantity
        must equal it (a resting order never reduces unprotected inventory);
        a bare `protected_qty` claim must equal the filled quantity."""
    f = facts or {}
    rec = f.get("recommendation")
    if rec is not None and str(rec).upper() not in XF.NON_ACTIONS \
            and str(rec).upper() not in (XF.S_NONE, XF.S_STALE,
                                         XF.S_INVALID, XF.S_SUPERSEDED):
        if f.get("recommendation_state") != XF.S_CURRENT:
            return R_XAVIER_STALE_ACTION
    if f.get("orders") is not None and f.get("held_qty") is not None:
        s = OST.protection_summary(held_qty=f["held_qty"],
                                   orders=list(f["orders"]))
        if (_f(f.get("filled_protection_qty")) != s["filled_protection_qty"]
                or _f(f.get("unprotected_qty")) != s["unprotected_qty"]):
            return R_RESTING_IS_NOT_PROTECTION
    if "protected_qty" in f and f.get("filled_protection_qty") is not None:
        if _f(f["protected_qty"]) != _f(f["filled_protection_qty"]):
            return R_RESTING_IS_NOT_PROTECTION
    elif "protected_qty" in f:
        return R_RESTING_IS_NOT_PROTECTION
    return None


def validate_candidate(c: dict) -> str | None:
    """None when the candidate may be checked against the database, else
    the named refusal. Pure."""
    a = I.agent_of(c.get("agent_id"))
    if a is None or a != c.get("agent_id"):
        return R_UNKNOWN_AGENT
    if c.get("memory_kind") not in KINDS:
        return R_UNKNOWN_KIND
    if c.get("subject_type") not in MANDATE[a] or not str(
            c.get("subject_id") or "").strip():
        return R_OUT_OF_MANDATE
    if not str(c.get("summary") or "").strip():
        return R_NO_SUMMARY
    refs = c.get("evidence_refs")
    if not isinstance(refs, (list, tuple)) or not refs:
        return R_NO_EVIDENCE
    for r in refs:
        if not isinstance(r, dict) or r.get("kind") not in EVIDENCE_TABLES \
                or not str(r.get("id") or "").strip():
            return R_BAD_EVIDENCE
    conf = c.get("confidence")
    if isinstance(conf, bool) or _f(conf) is None or not (
            0 < float(conf) <= 1):
        return R_NO_CONFIDENCE
    if _find_authority_keys(c.get("facts") or {}):
        return R_AUTHORITY
    from . import directives as D
    if D.screen_authority(str(c.get("summary")),
                          questions_exempt=False)["categories"]:
        return R_AUTHORITY
    if a == "XAVIER":
        why = xavier_fact_guard(c.get("facts") or {})
        if why:
            return why
    if c.get("memory_kind") == SELF_CORRECTION and not c.get("supersedes"):
        return "SELF_CORRECTION_MUST_NAME_THE_MEMORY_IT_CORRECTS"
    return None


# ═════════════════════════════════════════════════════════════════════
# 2 · DATABASE CHECKS AND THE ONE WRITE PATH
# ═════════════════════════════════════════════════════════════════════

async def has_schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('agent_memory_events') IS NOT NULL "
            "   AND to_regclass('agent_conversation_messages') IS NOT NULL"))
    except Exception:                                           # noqa: BLE001
        return False


async def _exists(conn, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    table))


async def verify_evidence(conn, refs) -> list:
    """The references whose record does not exist (empty = all exist)."""
    missing = []
    for r in refs or []:
        spec = EVIDENCE_TABLES.get((r or {}).get("kind"))
        if spec is None:
            missing.append(r)
            continue
        table, cols = spec
        parts = (str(r.get("id")).split("/", len(cols) - 1)
                 if len(cols) > 1 else [str(r.get("id"))])
        if len(parts) != len(cols) or not await _exists(conn, table):
            missing.append(r)
            continue
        where = " AND ".join("%s::text = $%d" % (c, i + 1)
                             for i, c in enumerate(cols))
        if not await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM %s WHERE %s)" % (table, where),
                *parts):
            missing.append(r)
    return missing


async def current_identity_version(conn, agent: str) -> int | None:
    return await conn.fetchval(
        "SELECT max(identity_version) FROM agent_identity_versions "
        " WHERE agent_id=$1", agent)


async def _active_claim(conn, agent: str, claim_key: str):
    return await conn.fetchrow(
        "SELECT memory_id, claim_value, source_event_at, learned_at "
        "  FROM agent_memory_events WHERE agent_id=$1 AND claim_key=$2 "
        "   AND superseded_by IS NULL "
        " ORDER BY learned_at DESC, memory_id DESC LIMIT 1",
        agent, claim_key)


async def _insert(conn, c: dict, *, now: float, version: int) -> bool:
    res = await conn.execute(
        "INSERT INTO agent_memory_events (memory_id, agent_id, memory_kind, "
        " subject_type, subject_id, summary, evidence_refs, confidence, "
        " learned_at, source_event_at, supersedes, expires_at, "
        " identity_version, claim_key, claim_value, deriver, facts) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,to_timestamp($9),"
        " CASE WHEN $10::float8 IS NULL THEN NULL "
        "      ELSE to_timestamp($10::float8) END, $11,"
        " CASE WHEN $12::float8 IS NULL THEN NULL "
        "      ELSE to_timestamp($12::float8) END, $13,$14,$15,$16,"
        " $17::jsonb) ON CONFLICT (memory_id) DO NOTHING",
        c["memory_id"], c["agent_id"], c["memory_kind"], c["subject_type"],
        str(c["subject_id"]), str(c["summary"])[:4000],
        json.dumps(list(c["evidence_refs"]), default=str),
        float(c["confidence"]), float(now), _ep(c.get("source_event_at")),
        c.get("supersedes"), _ep(c.get("expires_at")), int(version),
        c.get("claim_key"), None if c.get("claim_value") is None
        else str(c["claim_value"]), str(c.get("deriver") or "manual"),
        json.dumps(c.get("facts") or {}, default=str))
    return res.endswith("1")


async def promote(conn, candidate: dict, *, now: float | None = None) -> dict:
    """PROMOTE ONE CANDIDATE TO DURABLE MEMORY, or refuse it with a reason.
    Never raises. Writes only agent_memory_events (one new row; for a
    correction, also `superseded_by` on the corrected row)."""
    at = float(now if now is not None else time.time())
    c = dict(candidate)
    why = validate_candidate(c)
    if why:
        return {"ok": False, "refusal": why}
    try:
        if not await has_schema(conn):
            return {"ok": False, "refusal": R_NO_SCHEMA}
        missing = await verify_evidence(conn, c["evidence_refs"])
        if missing:
            return {"ok": False, "refusal": R_EVIDENCE_MISSING,
                    "missing": missing[:5]}
        version = await current_identity_version(conn, c["agent_id"])
        if version is None:
            return {"ok": False, "refusal": R_NO_SCHEMA}
        corrected = None
        if c.get("claim_key"):
            cur = await _active_claim(conn, c["agent_id"], c["claim_key"])
            if cur is not None:
                if str(cur["claim_value"]) == str(c.get("claim_value")):
                    return {"ok": True, "created": False,
                            "refusal": R_ALREADY_KNOWN,
                            "memory_id": cur["memory_id"]}
                newer = _ep(cur["source_event_at"])
                mine = _ep(c.get("source_event_at"))
                if newer is not None and mine is not None and newer > mine:
                    return {"ok": False, "refusal": R_CONTRADICTED,
                            "newer_memory_id": cur["memory_id"]}
                corrected = cur["memory_id"]
                c["memory_kind"] = SELF_CORRECTION
                c["supersedes"] = corrected
                c["evidence_refs"] = list(c["evidence_refs"]) + [
                    {"kind": "agent_memory_events", "id": corrected}]
                c["summary"] = "Correction of my earlier memory %s: %s" % (
                    corrected, c["summary"])
        c["memory_id"] = c.get("memory_id") or memory_id_for(c)
        async with conn.transaction():
            created = await _insert(conn, c, now=at, version=version)
            if created and corrected:
                await conn.execute(
                    "UPDATE agent_memory_events SET superseded_by=$1 "
                    " WHERE memory_id=$2 AND superseded_by IS NULL",
                    c["memory_id"], corrected)
        return {"ok": True, "created": created, "memory_id": c["memory_id"],
                "memory_kind": c["memory_kind"], "corrected": corrected}
    except Exception as exc:                                    # noqa: BLE001
        log.warning("agent memory: promote failed (%s)", type(exc).__name__)
        return {"ok": False, "refusal": R_WRITE_FAILED,
                "error": type(exc).__name__}


async def self_correct(conn, agent: str, old_memory_id: str, *, summary: str,
                       evidence_refs: list, confidence: float,
                       claim_value=None, deriver: str = "self_correction",
                       source_event_at=None, facts: dict | None = None,
                       now: float | None = None) -> dict:
    """WRITE A SELF_CORRECTION of one of the agent's OWN memories: a new row
    that cites new evidence and supersedes the old one, which stays in
    history (superseded_by set). Never deletes; never raises."""
    a = I.agent_of(agent)
    try:
        old = await conn.fetchrow(
            "SELECT memory_id, agent_id, subject_type, subject_id, "
            "       claim_key, superseded_by FROM agent_memory_events "
            " WHERE memory_id=$1", old_memory_id)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_WRITE_FAILED,
                "error": type(exc).__name__}
    if old is None or old["agent_id"] != a:
        return {"ok": False, "refusal": R_NO_SUCH_MEMORY}
    if old["superseded_by"] is not None:
        return {"ok": False, "refusal": R_ALREADY_SUPERSEDED,
                "superseded_by": old["superseded_by"]}
    c = {"agent_id": a, "memory_kind": SELF_CORRECTION,
         "subject_type": old["subject_type"], "subject_id": old["subject_id"],
         "summary": summary, "confidence": confidence,
         "evidence_refs": list(evidence_refs or []) + [
             {"kind": "agent_memory_events", "id": old_memory_id}],
         "claim_key": old["claim_key"], "claim_value": claim_value,
         "supersedes": old_memory_id, "deriver": deriver,
         "source_event_at": source_event_at, "facts": facts or {}}
    if not evidence_refs:
        return {"ok": False, "refusal": R_NO_EVIDENCE}
    why = validate_candidate(c)
    if why:
        return {"ok": False, "refusal": why}
    try:
        missing = await verify_evidence(conn, c["evidence_refs"])
        if missing:
            return {"ok": False, "refusal": R_EVIDENCE_MISSING,
                    "missing": missing[:5]}
        version = await current_identity_version(conn, a)
        c["memory_id"] = memory_id_for(dict(c, claim_value="%s->%s" % (
            old_memory_id, claim_value)))
        at = float(now if now is not None else time.time())
        async with conn.transaction():
            created = await _insert(conn, c, now=at, version=version)
            await conn.execute(
                "UPDATE agent_memory_events SET superseded_by=$1 "
                " WHERE memory_id=$2 AND superseded_by IS NULL",
                c["memory_id"], old_memory_id)
        return {"ok": True, "created": created, "memory_id": c["memory_id"],
                "supersedes": old_memory_id}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_WRITE_FAILED,
                "error": type(exc).__name__}


# ═════════════════════════════════════════════════════════════════════
# 3 · CONVERSATIONS AND HAND-OFFS (the only way a lesson crosses agents)
# ═════════════════════════════════════════════════════════════════════

MESSAGE_KINDS = ("HANDOFF", "MEMORY_HANDOFF", "QUESTION", "ANSWER",
                 "REVIEW_REQUEST", "CHALLENGE_NOTE")


async def record_message(conn, *, from_agent: str, to_agent: str,
                         message_kind: str, subject_type: str,
                         subject_id: str, summary: str, evidence_refs: list,
                         status: str = "INFORMATION", body: dict | None = None,
                         response_to: str | None = None,
                         shared_memory_id: str | None = None,
                         created_at: float | None = None,
                         message_id: str | None = None,
                         conversation_id: str | None = None) -> dict:
    """APPEND one agent-to-agent message with evidence that exists. Never
    raises; idempotent on the (deterministic) message id."""
    fa, ta = I.agent_of(from_agent), I.agent_of(to_agent)
    if fa is None or ta is None or fa == ta:
        return {"ok": False, "refusal": R_UNKNOWN_AGENT}
    if message_kind not in MESSAGE_KINDS:
        return {"ok": False, "refusal": "NOT_A_MESSAGE_KIND"}
    if not evidence_refs:
        return {"ok": False, "refusal": R_NO_EVIDENCE}
    for r in evidence_refs:
        if not isinstance(r, dict) or r.get("kind") not in EVIDENCE_TABLES:
            return {"ok": False, "refusal": R_BAD_EVIDENCE}
    if _find_authority_keys(body or {}):
        return {"ok": False, "refusal": R_AUTHORITY}
    at = float(created_at if created_at is not None else time.time())
    mid = message_id or "msg-" + hashlib.sha256("|".join(
        (fa, ta, message_kind, str(subject_type), str(subject_id),
         str(shared_memory_id or ""), str(response_to or ""))).encode()
    ).hexdigest()[:24]
    cid = conversation_id or "conv-" + hashlib.sha256("|".join(
        sorted((fa, ta)) + [str(subject_type), str(subject_id)]).encode()
    ).hexdigest()[:16]
    try:
        missing = await verify_evidence(conn, evidence_refs)
        if missing:
            return {"ok": False, "refusal": R_EVIDENCE_MISSING,
                    "missing": missing[:5]}
        async with conn.transaction():
            res = await conn.execute(
                "INSERT INTO agent_conversation_messages (message_id, "
                " conversation_id, from_agent, to_agent, message_kind, "
                " subject_type, subject_id, summary, body, evidence_refs, "
                " response_to, shared_memory_id, status, created_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11,"
                " $12,$13,to_timestamp($14)) "
                "ON CONFLICT (message_id) DO NOTHING",
                mid, cid, fa, ta, message_kind, str(subject_type),
                str(subject_id), str(summary)[:4000],
                json.dumps(body or {}, default=str),
                json.dumps(list(evidence_refs), default=str), response_to,
                shared_memory_id, status, at)
        return {"ok": True, "created": res.endswith("1"), "message_id": mid,
                "conversation_id": cid}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_WRITE_FAILED,
                "error": type(exc).__name__}


async def share_memory(conn, *, from_agent: str, to_agent: str,
                       memory_id: str, note: str | None = None,
                       created_at: float | None = None) -> dict:
    """HAND ONE OF MY OWN MEMORIES TO ANOTHER AGENT, explicitly: a
    MEMORY_HANDOFF message citing the memory and its evidence. The recipient
    then reads the hand-off, never the sender's private table."""
    fa = I.agent_of(from_agent)
    m = await conn.fetchrow(
        "SELECT memory_id, agent_id, subject_type, subject_id, summary, "
        "       evidence_refs FROM agent_memory_events WHERE memory_id=$1",
        memory_id)
    if m is None or m["agent_id"] != fa:
        return {"ok": False, "refusal": R_NO_SUCH_MEMORY}
    refs = [{"kind": "agent_memory_events", "id": memory_id}] + [
        r for r in (_j(m["evidence_refs"]) or [])
        if isinstance(r, dict) and r.get("kind") in EVIDENCE_TABLES][:10]
    return await record_message(
        conn, from_agent=fa, to_agent=to_agent,
        message_kind="MEMORY_HANDOFF", subject_type=m["subject_type"],
        subject_id=m["subject_id"],
        summary=(note + " -- " if note else "") + m["summary"],
        evidence_refs=refs, status="INFORMATION",
        shared_memory_id=memory_id, created_at=created_at)


# ═════════════════════════════════════════════════════════════════════
# 4 · READERS (SELECT only; the privacy boundary lives here)
# ═════════════════════════════════════════════════════════════════════

_MEM_COLS = ("memory_id, agent_id, memory_kind, subject_type, subject_id, "
             "summary, evidence_refs, confidence, learned_at, "
             "source_event_at, superseded_by, supersedes, expires_at, "
             "identity_version, claim_key, claim_value, deriver, facts, "
             "created_at")


def _mem_row(r) -> dict:
    d = dict(r)
    for k in ("learned_at", "source_event_at", "expires_at", "created_at"):
        d[k] = _ep(d.get(k))
    d["evidence_refs"] = _j(d.get("evidence_refs")) or []
    d["facts"] = _j(d.get("facts")) or {}
    d["confidence"] = _f(d.get("confidence"))
    d["source"] = "agent_memory_events"
    d["historical"] = d.get("superseded_by") is not None
    return d


def _check_reader(reader: str, owner: str) -> str:
    o = I.agent_of(owner)
    if o is None:
        raise ValueError(R_UNKNOWN_AGENT)
    if str(reader or "").upper() == OPERATOR:
        return o
    r = I.agent_of(reader)
    if r != o:
        raise PrivateMemoryRefused("%s: %s may not read %s's memory" % (
            R_PRIVATE, reader, o))
    return o


async def private_memories(conn, *, reader: str, owner: str,
                           kind: str | None = None, limit: int = 50,
                           before: float | None = None,
                           include_superseded: bool = True) -> list:
    """THE OWNER'S MEMORY, for the owner itself or the human OPERATOR only.
    Another agent is refused (PrivateMemoryRefused) before any read."""
    o = _check_reader(reader, owner)
    rows = await conn.fetch(
        "SELECT %s FROM agent_memory_events WHERE agent_id=$1 "
        "   AND ($2::text IS NULL OR memory_kind=$2) "
        "   AND ($3::float8 IS NULL OR learned_at < to_timestamp($3)) "
        "   AND ($4 OR superseded_by IS NULL) "
        " ORDER BY learned_at DESC, memory_id DESC LIMIT $5" % _MEM_COLS,
        o, kind, before, bool(include_superseded),
        max(1, min(int(limit or 50), 500)))
    return [_mem_row(r) for r in rows]


async def legacy_lessons(conn, *, reader: str, owner: str,
                         limit: int = 50, before: float | None = None
                         ) -> list:
    """The paper learning record's existing per-agent lessons (migration
    185, Derek / Xavier / Audrey), read as LESSON memories with their own
    supersession chain. Confidence was never recorded there: null with the
    reason, never invented."""
    o = _check_reader(reader, owner)
    if o not in ("DEREK", "XAVIER", "AUDREY") or not await _exists(
            conn, "paper_agent_lessons"):
        return []
    rows = await conn.fetch(
        "SELECT l.lesson_id, l.agent_id, l.kind, l.series_key, l.version, "
        "       l.supersedes, l.learned_at, l.window_end, l.statement, "
        "       l.provenance, l.evidence_category, "
        "       (SELECT n.lesson_id FROM paper_agent_lessons n "
        "         WHERE n.supersedes = l.lesson_id "
        "         ORDER BY n.version LIMIT 1) AS superseded_by "
        "  FROM paper_agent_lessons l WHERE l.agent_id=$1 "
        "   AND ($2::float8 IS NULL OR l.learned_at < to_timestamp($2)) "
        " ORDER BY l.learned_at DESC, l.lesson_id DESC LIMIT $3",
        o, before, max(1, min(int(limit or 50), 500)))
    out = []
    for r in rows:
        prov = _j(r["provenance"]) or {}
        out.append({
            "memory_id": r["lesson_id"], "agent_id": r["agent_id"],
            "memory_kind": LESSON, "subject_type": "paper_agent_lessons",
            "subject_id": r["series_key"], "summary": r["statement"],
            "evidence_refs": [{"kind": "paper_agent_lessons",
                               "id": r["lesson_id"],
                               "record_count": prov.get("record_count"),
                               "ids_sha256": prov.get("ids_sha256")}],
            "confidence": None,
            "confidence_why": "NOT_RECORDED_IN_PAPER_AGENT_LESSONS_185",
            "learned_at": _ep(r["learned_at"]),
            "source_event_at": _ep(r["window_end"]),
            "superseded_by": r["superseded_by"],
            "supersedes": r["supersedes"], "identity_version": None,
            "deriver": "paper_learning:%s" % r["kind"],
            "source": "paper_agent_lessons",
            "historical": r["superseded_by"] is not None})
    return out


async def handoffs_to(conn, agent: str, *, limit: int = 50) -> list:
    """Messages ADDRESSED TO the agent (what it may read of another's work)."""
    rows = await conn.fetch(
        "SELECT message_id, conversation_id, from_agent, to_agent, "
        "       message_kind, subject_type, subject_id, summary, "
        "       evidence_refs, response_to, shared_memory_id, status, "
        "       created_at FROM agent_conversation_messages "
        " WHERE to_agent=$1 ORDER BY created_at DESC, message_id DESC "
        " LIMIT $2", I.agent_of(agent), max(1, min(int(limit), 500)))
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = _ep(d["created_at"])
        d["evidence_refs"] = _j(d["evidence_refs"]) or []
        out.append(d)
    return out


async def memory_counts(conn, agent: str) -> dict:
    a = I.agent_of(agent)
    r = await conn.fetchrow(
        "SELECT count(*) AS n, "
        "       count(*) FILTER (WHERE memory_kind='LESSON') AS lessons, "
        "       count(*) FILTER (WHERE memory_kind='SELF_CORRECTION') "
        "       AS corrections, "
        "       count(*) FILTER (WHERE superseded_by IS NOT NULL) "
        "       AS superseded, max(learned_at) AS last "
        "  FROM agent_memory_events WHERE agent_id=$1", a)
    out = {"agent_memory_events": int(r["n"]), "lessons": int(r["lessons"]),
           "self_corrections": int(r["corrections"]),
           "superseded": int(r["superseded"]), "last_at": _ep(r["last"]),
           "paper_agent_lessons": None}
    if a in ("DEREK", "XAVIER", "AUDREY") and await _exists(
            conn, "paper_agent_lessons"):
        out["paper_agent_lessons"] = int(await conn.fetchval(
            "SELECT count(*) FROM paper_agent_lessons WHERE agent_id=$1", a))
    return out


# ═════════════════════════════════════════════════════════════════════
# 5 · THE DERIVERS: what each agent learns from durable outcomes
# ═════════════════════════════════════════════════════════════════════
#
# Each deriver reads rows recorded in (since, until], oldest first, at most
# DERIVE_LIMIT, and returns (candidates, cursor). Facts are quoted from the
# records; no figure is computed that the records do not support.

def _cand(agent, kind, subject_type, subject_id, summary, refs, *,
          confidence=1.0, claim_key=None, claim_value=None, deriver,
          source_event_at=None, facts=None, share_with=None) -> dict:
    c = {"agent_id": agent, "memory_kind": kind,
         "subject_type": subject_type, "subject_id": str(subject_id),
         "summary": summary, "evidence_refs": refs,
         "confidence": confidence, "claim_key": claim_key,
         "claim_value": claim_value, "deriver": deriver,
         "source_event_at": _ep(source_event_at), "facts": facts or {}}
    if share_with:
        c["share_with"] = share_with
    return c


def _money(v) -> str:
    x = _f(v)
    return "UNAVAILABLE" if x is None else ("-$" if x < 0 else "$") + \
        "{:,.2f}".format(abs(x))


def _num(v, fmt="%.3f") -> str:
    x = _f(v)
    return "UNRECORDED" if x is None else fmt % x


def _cursor(rows, col, until):
    """The next cursor. A full batch stops just BEFORE its last timestamp,
    so rows sharing that instant (one transaction's settlements) are read
    again next time (memory ids make the replay a no-op); only a batch
    whose rows ALL share one instant advances past it."""
    if len(rows) >= DERIVE_LIMIT:
        first, last = _ep(rows[0][col]), _ep(rows[-1][col])
        return last if first == last else last - 1e-6
    return until


SETTLED_ENTRIES_SQL = """
SELECT d.decision_id, d.us_market_slug, d.holding_side, d.p_blended,
       d.limit_price, d.policy_version, h.handoff_id, h.group_id,
       s.settlement_id, s.version, s.outcome, s.qty, s.payout_usd,
       s.settled_at, s.recorded_at
  FROM paper_settlements s
  JOIN paper_handoffs h ON h.group_id = s.group_id
  JOIN paper_decisions d ON d.decision_id = h.decision_id
 WHERE d.verdict = 'ENTER' AND s.holding_side = d.holding_side
   AND s.recorded_at > to_timestamp($1) AND s.recorded_at <= to_timestamp($2)
 ORDER BY s.recorded_at, s.settlement_id LIMIT $3"""


async def derive_derek_settled(conn, since, until):
    rows = await conn.fetch(SETTLED_ENTRIES_SQL, since, until, DERIVE_LIMIT)
    out = []
    for r in rows:
        out.append(_cand(
            "DEREK", CASE, "paper_decisions", r["decision_id"],
            "Entry %s (%s %s): I recorded p_blended %s against a limit of "
            "%s under policy %s. It settled %s (settlement %s v%s, payout "
            "%s on %s contracts). One outcome does not establish whether "
            "the edge was misestimated." % (
                r["decision_id"], r["us_market_slug"], r["holding_side"],
                _num(r["p_blended"]), _money(r["limit_price"]),
                r["policy_version"], r["outcome"], r["settlement_id"],
                r["version"], _money(r["payout_usd"]), _num(r["qty"],
                                                            "%.2f")),
            [{"kind": "paper_decisions", "id": r["decision_id"]},
             {"kind": "paper_handoffs", "id": r["handoff_id"]},
             {"kind": "paper_settlements", "id": r["settlement_id"]}],
            claim_key="settlement:%s" % r["decision_id"],
            claim_value=r["outcome"], deriver="derek_settled_entries",
            source_event_at=r["settled_at"],
            facts={"p_blended": _f(r["p_blended"]),
                   "limit_price": _f(r["limit_price"]),
                   "outcome": r["outcome"],
                   "settlement_version": r["version"]}))
    return out, _cursor(rows, "recorded_at", until)


CALIBRATION_SQL = """
SELECT count(*) AS n, avg(p) AS mean_p,
       count(*) FILTER (WHERE outcome = 'WON') AS won,
       max(settled_at) AS last_at,
       array_agg(settlement_id ORDER BY settled_at DESC) AS ids
  FROM (SELECT DISTINCT ON (d.decision_id) d.decision_id, d.p_blended AS p,
               s.outcome, s.settled_at, s.settlement_id
          FROM paper_settlements s
          JOIN paper_handoffs h ON h.group_id = s.group_id
          JOIN paper_decisions d ON d.decision_id = h.decision_id
         WHERE d.verdict = 'ENTER' AND s.holding_side = d.holding_side
           AND d.p_blended IS NOT NULL AND s.outcome IN ('WON', 'LOST')
           AND NOT EXISTS (SELECT 1 FROM paper_settlements n
                            WHERE n.supersedes = s.settlement_id)
         ORDER BY d.decision_id, s.version DESC) q"""


async def derive_derek_calibration(conn, since, until):
    r = await conn.fetchrow(CALIBRATION_SQL)
    n = int(r["n"] or 0)
    if n < LESSON_MIN_SAMPLE:
        return [], until
    won, mean_p = int(r["won"]), float(r["mean_p"])
    lo, hi = wilson(won, n)
    verdict = ("OVERCONFIDENT" if mean_p > hi else
               "UNDERCONFIDENT" if mean_p < lo else "WITHIN_INTERVAL")
    return [_cand(
        "DEREK", LESSON, "entry_calibration", "paper_enter_settled",
        "Across %d settled paper entries my mean recorded p_blended was "
        "%.3f; the realized win rate was %.3f (95%% Wilson interval "
        "%.3f-%.3f). Calibration verdict: %s. A research input, not a "
        "threshold." % (n, mean_p, won / n, lo, hi, verdict),
        [{"kind": "paper_settlements", "id": i} for i in r["ids"][:20]],
        confidence=0.95, claim_key="calibration:derek:paper",
        claim_value=verdict, deriver="derek_calibration",
        source_event_at=r["last_at"],
        facts={"n": n, "mean_p": round(mean_p, 6), "won": won,
               "interval_95": [round(lo, 6), round(hi, 6)],
               "verdict": verdict})], until


SETTLED_GROUPS_SQL = """
SELECT * FROM (
SELECT DISTINCT ON (s.group_id) s.group_id, s.settlement_id, s.outcome,
       s.settled_at, s.recorded_at, h.handoff_id, h.decision_id
  FROM paper_settlements s
  JOIN paper_handoffs h ON h.group_id = s.group_id
 WHERE s.recorded_at > to_timestamp($1) AND s.recorded_at <= to_timestamp($2)
   AND EXISTS (SELECT 1 FROM paper_xavier_reviews r
                WHERE r.group_id = s.group_id)
 ORDER BY s.group_id, s.recorded_at DESC) q
 ORDER BY q.recorded_at, q.group_id LIMIT $3"""


async def _protection_facts(conn, group_id, at):
    """The group's protection at `at`, by order_state_truth: held quantity
    from fills, every protective order with its FILLED quantity."""
    entry = await conn.fetchrow(
        "SELECT holding_side FROM paper_orders WHERE group_id=$1 "
        "   AND role='ENTRY' ORDER BY created_at LIMIT 1", group_id)
    if entry is None:
        return None
    side = entry["holding_side"]
    held = await conn.fetchval(
        "SELECT coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) "
        "     - coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) "
        "  FROM paper_fills WHERE group_id=$1 AND holding_side=$2 "
        "   AND filled_at <= to_timestamp($3)", group_id, side, at)
    orders = [{"order_ref": o["order_id"], "role": o["role"],
               "direction": o["direction"], "qty": _f(o["qty"]),
               "filled_qty": _f(o["filled_qty"]),
               "limit": _f(o["limit_price"]), "raw_state": o["state"],
               "source": OST.SRC_PAPER}
              for o in await conn.fetch(
                  "SELECT order_id, role, direction, qty, filled_qty, "
                  "       limit_price, state FROM paper_orders "
                  " WHERE group_id=$1 AND role = ANY($2::text[]) "
                  "   AND decided_at <= to_timestamp($3) "
                  " ORDER BY decided_at, order_id", group_id,
                  list(OST.PROTECTIVE_ROLES), at)]
    if not orders:
        return None
    s = OST.protection_summary(held_qty=_f(held), orders=orders)
    return {"held_qty": _f(held), "orders": orders,
            "filled_protection_qty": s["filled_protection_qty"],
            "standing_order_qty": s["standing_order_qty"],
            "unprotected_qty": s["unprotected_qty"], "line": s["line"]}


async def derive_xavier_settled(conn, since, until):
    groups = await conn.fetch(SETTLED_GROUPS_SQL, since, until,
                              DERIVE_LIMIT)
    groups = sorted(groups, key=lambda g: (_ep(g["recorded_at"]),
                                           g["group_id"]))
    out = []
    # the limit each review recorded is used first; this is only the
    # fallback (None -> STALE by NO_FRESHNESS_LIMIT_KNOWN, never current)
    limit_s = XF.default_limit_s()
    for g in groups:
        at = _ep(g["settled_at"])
        reviews = [dict(r) for r in await conn.fetch(
            "SELECT review_id, group_id, reviewed_at, recommendation, "
            "       refusal, measure, selection FROM paper_xavier_reviews "
            " WHERE group_id=$1 AND reviewed_at <= to_timestamp($2) "
            " ORDER BY reviewed_at DESC, review_id DESC LIMIT 20",
            g["group_id"], at)]
        for r in reviews:
            r["reviewed_at"] = _ep(r["reviewed_at"])
        if reviews:
            cur = XF.current_decisions(reviews, now=at, limit_s=limit_s)[
                g["group_id"]]
            dec = cur["current"]["decision"]
            state = dec["management_state"]
            rec = dec["current_recommendation"]
            out.append(_cand(
                "XAVIER", CASE, "paper_position", g["group_id"],
                "Position %s settled %s. At settlement my one CURRENT "
                "review was %s (valuation %s v%s): management state %s%s. "
                "%d older review(s) were superseded by it." % (
                    g["group_id"], g["outcome"], dec["review_id"],
                    dec["valuation_id"] or "UNRECORDED",
                    dec["valuation_version"] or "?", state,
                    (" -- action %s" % rec) if rec else
                    " -- no current action (stale or missing evidence is "
                    "WAITING_FOR_FRESH_EVIDENCE, never HOLD)",
                    len(cur["superseded"])),
                [{"kind": "paper_xavier_reviews", "id": dec["review_id"]},
                 {"kind": "paper_settlements", "id": g["settlement_id"]},
                 {"kind": "paper_handoffs", "id": g["handoff_id"]}],
                claim_key="management_at_settlement:%s" % g["group_id"],
                claim_value="%s:%s" % (state, g["outcome"]),
                deriver="xavier_settled_positions",
                source_event_at=g["settled_at"],
                facts={"review_id": dec["review_id"],
                       "valuation_id": dec["valuation_id"],
                       "valuation_version": dec["valuation_version"],
                       "recommendation": rec or state,
                       "recommendation_state": dec["recommendation_state"],
                       "management_state": state,
                       "superseded_review_ids": [
                           o["review_id"] for o in cur["superseded"]][:20],
                       "outcome": g["outcome"]}))
        prot = await _protection_facts(conn, g["group_id"], at)
        if prot is not None:
            share = None
            if prot["standing_order_qty"] > 0 and (prot["unprotected_qty"]
                                                   or 0) > 0:
                share = "AUDREY"
            out.append(_cand(
                "XAVIER", CASE, "paper_position", g["group_id"],
                "Protection of %s at settlement: FILLED protection %s, "
                "standing (resting / unfilled) %s, unprotected %s of a "
                "position of %s. Resting quantity never reduced unprotected "
                "inventory; only filled quantity is protection." % (
                    g["group_id"], OST.fmt_qty(prot["filled_protection_qty"]),
                    OST.fmt_qty(prot["standing_order_qty"]),
                    OST.fmt_qty(prot["unprotected_qty"]),
                    OST.fmt_qty((prot["held_qty"] or 0)
                                + sum(o["filled_qty"] or 0
                                      for o in prot["orders"]
                                      if o["direction"] == "SELL"))),
                [{"kind": "paper_orders", "id": o["order_ref"]}
                 for o in prot["orders"]][:20]
                + [{"kind": "paper_settlements", "id": g["settlement_id"]}],
                claim_key="protection_at_settlement:%s" % g["group_id"],
                claim_value="%s/%s" % (prot["filled_protection_qty"],
                                       prot["unprotected_qty"]),
                deriver="xavier_protection_at_settlement",
                source_event_at=g["settled_at"],
                facts={k: prot[k] for k in (
                    "held_qty", "orders", "filled_protection_qty",
                    "standing_order_qty", "unprotected_qty")},
                share_with=share))
    return out, _cursor(groups, "recorded_at", until)


async def derive_xavier_stale_hold(conn, since, until):
    """A review that RECORDED an action word on non-fresh evidence: Xavier
    remembers, per position, that it was never a current recommendation."""
    rows = await conn.fetch(
        "SELECT review_id, group_id, reviewed_at, recommendation, refusal, "
        "       measure, selection, recorded_at FROM paper_xavier_reviews "
        " WHERE recommendation IS NOT NULL "
        "   AND recommendation <> ALL($4::text[]) "
        "   AND recorded_at > to_timestamp($1) "
        "   AND recorded_at <= to_timestamp($2) "
        " ORDER BY recorded_at, review_id LIMIT $3", since, until,
        DERIVE_LIMIT, list(XF.NON_ACTIONS))
    out = []
    for r in rows:
        d = dict(r, reviewed_at=_ep(r["reviewed_at"]))
        m = _j(r["measure"]) or {}
        m = m if isinstance(m, dict) else {}
        # only a review whose own measure SAYS its evidence was not fresh
        # (an unrecorded state is not claimed to have been stale)
        if not (m.get("stale") is True or m.get("evidence_state") in (
                XF.E_STALE, XF.E_NONE)):
            continue
        blk = XF.of_review(d, now=_ep(r["reviewed_at"]))
        if XF.I_RECORDED_STALE not in (blk.get("reasons") or []):
            continue
        out.append(_cand(
            "XAVIER", CASE, "paper_xavier_reviews", r["group_id"],
            "Review %s of %s recorded %s on non-fresh evidence. Under the "
            "current-state rule that was never a current recommendation: "
            "the state was %s." % (
                r["review_id"], r["group_id"], r["recommendation"],
                blk["management_state"]),
            [{"kind": "paper_xavier_reviews", "id": r["review_id"]}],
            claim_key="stale_action_recorded:%s" % r["group_id"],
            claim_value=XF.I_RECORDED_STALE,
            deriver="xavier_stale_action_recorded",
            source_event_at=r["reviewed_at"],
            facts={"review_id": r["review_id"],
                   "recorded_recommendation": r["recommendation"],
                   "recommendation": blk["management_state"],
                   "recommendation_state": blk["recommendation_state"],
                   "management_state": blk["management_state"]}))
    return out, _cursor(rows, "recorded_at", until)


async def derive_audrey_reconciliations(conn, since, until):
    rows = await conn.fetch(
        "SELECT report_id, account_id, report_day, version, final, "
        "       reconciles, generated_at, recorded_at "
        "  FROM paper_audrey_reports WHERE final "
        "   AND recorded_at > to_timestamp($1) "
        "   AND recorded_at <= to_timestamp($2) "
        " ORDER BY recorded_at, report_id LIMIT $3", since, until,
        DERIVE_LIMIT)
    out = []
    for r in rows:
        out.append(_cand(
            "AUDREY", CASE, "paper_audrey_reports",
            "%s:%s" % (r["account_id"], r["report_day"]),
            "Final paper report %s for %s (v%s): the books %s." % (
                r["report_id"], r["report_day"], r["version"],
                "reconciled" if r["reconciles"] else
                "did NOT reconcile -- unresolved until reconciled"),
            [{"kind": "paper_audrey_reports", "id": r["report_id"]}],
            claim_key="reconciles:%s:%s" % (r["account_id"],
                                            r["report_day"]),
            claim_value=str(bool(r["reconciles"])),
            deriver="audrey_final_reconciliations",
            source_event_at=r["generated_at"],
            facts={"reconciles": bool(r["reconciles"]),
                   "version": r["version"]}))
    return out, _cursor(rows, "recorded_at", until)


RESOLVED_CHALLENGES_SQL = """
SELECT challenge_id, target_agent, target_kind, target_id, detector,
       severity, state, outcome, outcome_reason, resolved_by, resolved_at
  FROM karen_challenges
 WHERE state IN ('UPHELD', 'REJECTED', 'WITHDRAWN')
   AND resolved_at > to_timestamp($1) AND resolved_at <= to_timestamp($2)
 ORDER BY resolved_at, challenge_id LIMIT $3"""


async def derive_challenge_outcomes(conn, since, until):
    """One resolved challenge, three lessons: Karen's (was it upheld?), the
    target's RELATIONSHIP memory, and Audrey's when she evaluated it."""
    rows = await conn.fetch(RESOLVED_CHALLENGES_SQL, since, until,
                            DERIVE_LIMIT)
    out = []
    for r in rows:
        ref = [{"kind": "karen_challenges", "id": r["challenge_id"]}]
        out.append(_cand(
            "KAREN", CASE, "karen_challenges", r["challenge_id"],
            "My %s challenge %s of %s's %s %s (detector %s) was %s by %s: "
            "%s" % (r["severity"], r["challenge_id"], r["target_agent"],
                    r["target_kind"], r["target_id"], r["detector"],
                    r["outcome"], r["resolved_by"],
                    str(r["outcome_reason"] or "")[:300]),
            ref, claim_key="challenge:%s" % r["challenge_id"],
            claim_value=r["outcome"], deriver="karen_challenge_outcomes",
            source_event_at=r["resolved_at"],
            facts={"outcome": r["outcome"], "detector": r["detector"],
                   "target_agent": r["target_agent"]}))
        tgt = I.agent_of(r["target_agent"])
        if tgt and r["outcome"] in ("UPHELD", "REJECTED"):
            out.append(_cand(
                tgt, RELATIONSHIP, "karen_challenges", r["challenge_id"],
                "Karen challenged my %s %s (%s); %s resolved it %s." % (
                    r["target_kind"], r["target_id"], r["detector"],
                    r["resolved_by"], r["outcome"]),
                ref, claim_key="challenged_by_karen:%s" % r["challenge_id"],
                claim_value=r["outcome"], deriver="challenge_against_me",
                source_event_at=r["resolved_at"],
                facts={"outcome": r["outcome"], "counterpart": "KAREN"}))
        if I.agent_of(r["resolved_by"]) == "AUDREY" and tgt != "AUDREY":
            out.append(_cand(
                "AUDREY", CASE, "karen_challenges", r["challenge_id"],
                "I evaluated Karen's challenge %s of %s against the "
                "records and recorded it %s." % (
                    r["challenge_id"], r["target_agent"], r["outcome"]),
                ref, claim_key="evaluated:%s" % r["challenge_id"],
                claim_value=r["outcome"], deriver="audrey_evaluations",
                source_event_at=r["resolved_at"],
                facts={"outcome": r["outcome"]}))
    return out, _cursor(rows, "resolved_at", until)


async def derive_karen_detector_lessons(conn, since, until):
    out = []
    for r in await conn.fetch(
            "SELECT detector, count(*) AS n, "
            "       count(*) FILTER (WHERE state='UPHELD') AS upheld, "
            "       max(resolved_at) AS last_at, "
            "       array_agg(challenge_id ORDER BY resolved_at DESC) AS ids"
            "  FROM karen_challenges WHERE state IN ('UPHELD','REJECTED') "
            " GROUP BY detector HAVING count(*) >= $1", LESSON_MIN_SAMPLE):
        n, k = int(r["n"]), int(r["upheld"])
        lo, hi = wilson(k, n)
        verdict = ("MOSTLY_UPHELD" if lo > 0.5 else
                   "MOSTLY_REJECTED" if hi < 0.5 else "UNDECIDED")
        out.append(_cand(
            "KAREN", LESSON, "karen_detector", r["detector"],
            "Detector %s: %d of %d resolved challenges upheld (%.3f; 95%% "
            "Wilson interval %.3f-%.3f): %s." % (r["detector"], k, n, k / n,
                                                 lo, hi, verdict),
            [{"kind": "karen_challenges", "id": i} for i in r["ids"][:20]],
            confidence=0.95, claim_key="detector:%s" % r["detector"],
            claim_value=verdict, deriver="karen_detector_lessons",
            source_event_at=r["last_at"],
            facts={"n": n, "upheld": k, "interval_95": [round(lo, 6),
                                                        round(hi, 6)]}))
    return out, until


async def derive_allocator_settled(conn, since, until):
    rows = await conn.fetch(
        "SELECT * FROM (SELECT DISTINCT ON (a.run_id, a.candidate_id) "
        "       a.run_id, "
        "       a.candidate_id, a.rank, a.shadow_usd, a.decision_id, "
        "       a.binding_constraint, s.settlement_id, s.outcome, "
        "       s.settled_at, s.recorded_at "
        "  FROM intel_allocations a "
        "  JOIN paper_handoffs h ON h.decision_id = a.decision_id "
        "  JOIN paper_settlements s ON s.group_id = h.group_id "
        " WHERE a.shadow_usd > 0 AND s.recorded_at > to_timestamp($1) "
        "   AND s.recorded_at <= to_timestamp($2) "
        " ORDER BY a.run_id, a.candidate_id, s.recorded_at DESC) q "
        " ORDER BY q.recorded_at, q.run_id, q.candidate_id LIMIT $3",
        since, until, DERIVE_LIMIT)
    rows = sorted(rows, key=lambda r: (_ep(r["recorded_at"]), r["run_id"],
                                       r["candidate_id"]))
    out = []
    for r in rows:
        key = "%s/%s" % (r["run_id"], r["candidate_id"])
        out.append(_cand(
            "CHIEF_ALLOCATOR", CASE, "intel_allocations", key,
            "My SHADOW ranking %s put decision %s at rank %s with %s of "
            "the notional sleeve (binding constraint %s). It settled %s. "
            "SHADOW weights moved no capital." % (
                key, r["decision_id"], r["rank"], _money(r["shadow_usd"]),
                r["binding_constraint"] or "none recorded", r["outcome"]),
            [{"kind": "intel_allocations", "id": key},
             {"kind": "paper_settlements", "id": r["settlement_id"]}],
            claim_key="allocation_outcome:%s" % key,
            claim_value=r["outcome"], deriver="allocator_settled",
            source_event_at=r["settled_at"],
            facts={"rank": r["rank"], "shadow_usd": _f(r["shadow_usd"]),
                   "outcome": r["outcome"]}))
    return out, _cursor(rows, "recorded_at", until)


async def derive_eddie_outcomes(conn, since, until):
    rows = await conn.fetch(
        "SELECT o.outcome_id, o.estimate_id, o.decision_id, o.source, "
        "       o.measured_at, o.realized_execution_loss_pp, "
        "       o.predicted_execution_loss_pp, o.created_at "
        "  FROM eddie_execution_outcomes o "
        " WHERE o.realized_execution_loss_pp IS NOT NULL "
        "   AND o.predicted_execution_loss_pp IS NOT NULL "
        "   AND o.created_at > to_timestamp($1) "
        "   AND o.created_at <= to_timestamp($2) "
        " ORDER BY o.created_at, o.outcome_id LIMIT $3", since, until,
        DERIVE_LIMIT)
    out = []
    for r in rows:
        real, pred = _f(r["realized_execution_loss_pp"]), _f(
            r["predicted_execution_loss_pp"])
        out.append(_cand(
            "EDDIE", CASE, "eddie_execution_estimates", r["estimate_id"],
            "%s fill of decision %s: I predicted %.2f pp of execution loss; "
            "%.2f pp was realized (error %+.2f pp). SHADOW -- nothing "
            "executed on my estimate." % (r["source"], r["decision_id"],
                                          pred, real, real - pred),
            [{"kind": "eddie_execution_estimates", "id": r["estimate_id"]},
             {"kind": "eddie_execution_outcomes", "id": r["outcome_id"]}],
            claim_key="execution_outcome:%s:%s" % (r["estimate_id"],
                                                   r["source"]),
            claim_value="%.4f" % real, deriver="eddie_execution_outcomes",
            source_event_at=r["measured_at"],
            facts={"source": r["source"], "predicted_pp": pred,
                   "realized_pp": real},
            share_with="DEREK"))
    return out, _cursor(rows, "created_at", until)


async def derive_eddie_calibration(conn, since, until):
    r = await conn.fetchrow(
        "SELECT count(*) AS n, avg(realized_execution_loss_pp - "
        "       predicted_execution_loss_pp) AS bias, "
        "       count(*) FILTER (WHERE realized_execution_loss_pp > "
        "             predicted_execution_loss_pp) AS worse, "
        "       max(measured_at) AS last_at, "
        "       array_agg(outcome_id ORDER BY measured_at DESC) AS ids "
        "  FROM eddie_execution_outcomes "
        " WHERE realized_execution_loss_pp IS NOT NULL "
        "   AND predicted_execution_loss_pp IS NOT NULL")
    n = int(r["n"] or 0)
    if n < LESSON_MIN_SAMPLE:
        return [], until
    k = int(r["worse"])
    lo, hi = wilson(k, n)
    verdict = ("UNDERESTIMATES_LOSS" if lo > 0.5 else
               "OVERESTIMATES_LOSS" if hi < 0.5 else "UNBIASED_WITHIN_CI")
    return [_cand(
        "EDDIE", LESSON, "execution_calibration", "all_measured_outcomes",
        "Across %d measured fills the realized execution loss exceeded my "
        "prediction in %d (%.3f; 95%% Wilson interval %.3f-%.3f); mean "
        "error %+.3f pp: %s." % (n, k, k / n, lo, hi, float(r["bias"]),
                                 verdict),
        [{"kind": "eddie_execution_outcomes", "id": i}
         for i in r["ids"][:20]],
        confidence=0.95, claim_key="calibration:eddie", claim_value=verdict,
        deriver="eddie_calibration", source_event_at=r["last_at"],
        facts={"n": n, "worse": k, "mean_error_pp": float(r["bias"])})], \
        until


async def derive_scout_tournaments(conn, since, until):
    rows = await conn.fetch(
        "SELECT t.tournament_id, t.feature_id, f.feature, t.metric, t.n, "
        "       t.improvement, t.min_improvement, t.verdict, "
        "       t.verdict_reason, t.evaluated_by, t.evaluated_at "
        "  FROM scout_feature_tournaments t JOIN scout_features f "
        "       USING (feature_id) WHERE t.verdict IS NOT NULL "
        "   AND t.evaluated_at > to_timestamp($1) "
        "   AND t.evaluated_at <= to_timestamp($2) "
        " ORDER BY t.evaluated_at, t.tournament_id LIMIT $3", since, until,
        DERIVE_LIMIT)
    out = []
    for r in rows:
        out.append(_cand(
            "SCOUT", CASE, "scout_feature_tournaments", r["tournament_id"],
            "OBSERVATION: my feature %s (hypothesis %s) was %s by %s on n=%s "
            "(%s improvement %s vs required %s). I did not judge it; the "
            "evaluator did." % (
                r["feature"], r["feature_id"], r["verdict"],
                r["evaluated_by"], r["n"], r["metric"],
                _num(r["improvement"], "%.5f"),
                _num(r["min_improvement"], "%.5f")),
            [{"kind": "scout_feature_tournaments", "id": r["tournament_id"]},
             {"kind": "scout_features", "id": r["feature_id"]}],
            claim_key="tournament:%s" % r["tournament_id"],
            claim_value=r["verdict"], deriver="scout_tournament_verdicts",
            source_event_at=r["evaluated_at"],
            facts={"verdict": r["verdict"], "n": r["n"],
                   "improvement": _f(r["improvement"])}))
    return out, _cursor(rows, "evaluated_at", until)


#: name -> (deriver, tables it needs)
DERIVERS: dict[str, tuple] = {
    "derek_settled_entries": (derive_derek_settled, (
        "paper_settlements", "paper_handoffs", "paper_decisions")),
    "derek_calibration": (derive_derek_calibration, (
        "paper_settlements", "paper_handoffs", "paper_decisions")),
    "xavier_settled_positions": (derive_xavier_settled, (
        "paper_settlements", "paper_handoffs", "paper_xavier_reviews",
        "paper_orders", "paper_fills")),
    "xavier_stale_action_recorded": (derive_xavier_stale_hold, (
        "paper_xavier_reviews",)),
    "audrey_final_reconciliations": (derive_audrey_reconciliations, (
        "paper_audrey_reports",)),
    "challenge_outcomes": (derive_challenge_outcomes, ("karen_challenges",)),
    "karen_detector_lessons": (derive_karen_detector_lessons, (
        "karen_challenges",)),
    "allocator_settled": (derive_allocator_settled, (
        "intel_allocations", "paper_handoffs", "paper_settlements")),
    "eddie_execution_outcomes": (derive_eddie_outcomes, (
        "eddie_execution_outcomes",)),
    "eddie_calibration": (derive_eddie_calibration, (
        "eddie_execution_outcomes",)),
    "scout_tournament_verdicts": (derive_scout_tournaments, (
        "scout_feature_tournaments", "scout_features")),
}


async def learn_once(conn, *, now: float | None = None,
                     cursors: dict | None = None,
                     only: tuple | None = None) -> dict:
    """RUN EVERY DERIVER ONCE (each in its own savepoint) and promote its
    candidates; share the ones marked for a hand-off. Returns the new
    cursors. Never raises."""
    at = float(now if now is not None else time.time())
    cur = dict(cursors or {})
    out: dict[str, Any] = {"version": VERSION, "at": at, "derivers": {},
                           "cursors": cur}
    if not await has_schema(conn):
        out["refusal"] = R_NO_SCHEMA
        return out
    for name, (fn, tables) in DERIVERS.items():
        if only and name not in only:
            continue
        rep: dict[str, Any] = {"candidates": 0, "created": 0, "corrected": 0,
                               "known": 0, "refused": {}, "shared": 0}
        out["derivers"][name] = rep
        try:
            for t in tables:
                if not await _exists(conn, t):
                    rep["absent"] = "TABLE_NOT_DEPLOYED:%s" % t
                    raise LookupError
            async with conn.transaction():
                cands, nxt = await fn(conn, float(cur.get(name) or 0.0), at)
        except LookupError:
            continue
        except Exception as exc:                                # noqa: BLE001
            rep["error"] = type(exc).__name__
            continue
        rep["candidates"] = len(cands)
        for c in cands:
            share = c.pop("share_with", None)
            got = await promote(conn, c, now=at)
            if got.get("created"):
                rep["created"] += 1
                rep["corrected"] += 1 if got.get("corrected") else 0
                if share:
                    s = await share_memory(
                        conn, from_agent=c["agent_id"], to_agent=share,
                        memory_id=got["memory_id"], created_at=at)
                    rep["shared"] += 1 if s.get("created") else 0
            elif got.get("refusal") == R_ALREADY_KNOWN:
                rep["known"] += 1
            else:
                k = got.get("refusal") or "UNKNOWN"
                rep["refused"][k] = rep["refused"].get(k, 0) + 1
        cur[name] = nxt
    return out


async def step(conn, ctx: dict) -> dict:
    """THE SCHEDULED HOOK (on the paper pass): at most every RUN_EVERY_S,
    run the learner from its per-deriver cursors and save them in
    ingestion_state. Writes only memory, hand-off messages and its own
    watermark. Never raises."""
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    at = float(clock())
    try:
        if not await has_schema(conn):
            return {"ran": False, "why": R_NO_SCHEMA}
        last = _j(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            WATERMARK_KEY)) or {}
        if last.get("at") is not None and at - float(last["at"]) \
                < RUN_EVERY_S:
            return {"ran": False, "why": "NOT_DUE", "last_at": last["at"]}
        res = await learn_once(conn, now=at, cursors=last.get("cursors"))
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            WATERMARK_KEY, json.dumps({"at": at, "version": VERSION,
                                       "cursors": res.get("cursors")}))
        created = sum(d.get("created", 0)
                      for d in res["derivers"].values())
        return {"ran": True, "created": created,
                "derivers": len(res["derivers"])}
    except Exception as exc:                                    # noqa: BLE001
        return {"ran": False, "error": "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200])}
