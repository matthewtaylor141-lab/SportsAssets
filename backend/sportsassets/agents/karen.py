"""KAREN: THE RED TEAM / CHALLENGE AGENT -- GROUNDED CHALLENGES, NO AUTHORITY.

Karen is the fourth named agent (migration 207) beside Derek (entry), Xavier
(management) and Audrey (audit). Her one job is to challenge their records
with evidence:

  * a CHALLENGE names its target agent and target record, cites at least one
    evidence record that EXISTS (the grounding rule: no ungrounded
    challenge), carries a severity, and is persisted in `karen_challenges`
    with an append-only history (`karen_challenge_events`);
  * the TARGET agent records the peer response (CONCEDE or DISPUTE, with
    optional evidence) -- nobody else may answer for it;
  * the outcome (UPHELD / REJECTED) is recorded by someone who is NOT Karen;
    the target may concede (UPHELD) but never reject a challenge against
    itself, and nothing is resolved before the target has answered. Karen
    may only WITHDRAW her own challenge;
  * a challenge that blocked or delayed something (a collaboration-loop
    PEER_CHALLENGE recorded REFUTED) is later assessed for FALSE BLOCK -- did
    it stop something that proved fine? -- by someone other than Karen;
  * an UPHELD challenge may be linked to the improvement it led to (an
    `agent_findings` row or a `paper_improvement_proposals` row), never by
    Karen.

In the collaboration loop (migration 203) Karen acts ONLY as a challenger:
`challenge_finding` records the PEER_CHALLENGE stage of another agent's
finding (through collaboration_loop.record_challenge, so every loop guard
applies) together with the karen_challenges row, in one transaction.

WHAT KAREN CANNOT DO, BY CONSTRUCTION. Send, cancel or request an order;
dispatch; write a risk limit, credential, account authority, approval or
submission switch; propose, approve or activate a policy; promote or release
anything; deploy. This module imports no order, venue or execution path and
writes only `karen_challenges`, `karen_challenge_events` and (through the
loop module) the loop's PEER_CHALLENGE stage. Every write checks
`registry.permits(KAREN, "write.challenges")`, `refuse_authority` names
every forbidden action, and the database refuses the same things on its own
(207: CHECKs, the lifecycle trigger, and a trigger on every approval /
activation / promotion / control table that refuses Karen as the actor).

METRICS (`metrics`) each carry a numerator and a denominator; a metric with
nothing to measure is null with the reason, never 0.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from datetime import datetime
from typing import Any

from . import collaboration_loop as CL
from . import karen_evidence as KE  # noqa: F401  (registers kinds)
from . import registry as R

KAREN = R.KAREN
#: The Chief Allocator (its shadow allocations: intel_allocations, migration
#: 208) is a challenge TARGET, not an agent identity: it holds no tool and no
#: authority. Its rule-based responder answers for it.
CHIEF_ALLOCATOR = "CHIEF_ALLOCATOR"
TARGETS = R.OPERATING_AGENTS + (CHIEF_ALLOCATOR,)
#: Who evaluates a disputed challenge: never Karen, never the target.
EVALUATOR_FOR = {R.DEREK: R.AUDREY, R.XAVIER: R.AUDREY,
                 R.AUDREY: R.XAVIER, CHIEF_ALLOCATOR: R.AUDREY}

CATEGORIES = ("EVIDENCE_GAP", "STALE_INPUT", "OPEN_DISCREPANCY",
              "EXECUTION_REFUSAL", "ALLOCATION_RISK", "GOVERNANCE_GAP",
              "LOOP_DEFECT", "OTHER")
DETECTOR_CATEGORY = {
    "DECISION_WITHOUT_EVIDENCE": "EVIDENCE_GAP",
    "ENTRY_WITHOUT_PROBABILITY": "EVIDENCE_GAP",
    "HOLD_ON_STALE_PROBABILITY": "STALE_INPUT",
    "AUDIT_DISCREPANCY_LEFT_OPEN": "OPEN_DISCREPANCY",
    "RECONCILIATION_DISCREPANCY_OPEN": "OPEN_DISCREPANCY",
    "ADMISSION_REFUSED_DECISION": "EXECUTION_REFUSAL",
    "FINDING_RESTS_ON_UPHELD_DEFECT": "LOOP_DEFECT",
    "LOOP_PEER_CHALLENGE": "LOOP_DEFECT",
    "ALLOCATION_WITHOUT_EVIDENCE": "ALLOCATION_RISK",
    "LARGE_ALLOCATION": "ALLOCATION_RISK",
    "POLICY_CANDIDATE_WITHOUT_EVIDENCE": "GOVERNANCE_GAP",
    "POLICY_ARTIFACT_READY_WITHOUT_EVIDENCE": "GOVERNANCE_GAP",
    "LIVE_RULE_READY_WITHOUT_EVIDENCE": "GOVERNANCE_GAP",
    "STRATEGY_CANDIDATE_WITHOUT_EVIDENCE": "GOVERNANCE_GAP",
}


def category_of(detector: str) -> str:
    return DETECTOR_CATEGORY.get(str(detector or ""), "OTHER")

OPEN, RESPONDED, UPHELD, REJECTED, WITHDRAWN = (
    "OPEN", "RESPONDED", "UPHELD", "REJECTED", "WITHDRAWN")
STATES = (OPEN, RESPONDED, UPHELD, REJECTED, WITHDRAWN)
TERMINAL = (UPHELD, REJECTED, WITHDRAWN)
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
STANCES = ("CONCEDE", "DISPUTE")
LOOP_DETECTOR = "LOOP_PEER_CHALLENGE"

#: What Karen may never do -- each is also on her registry DENY list, and
#: the database refuses her as the actor of every approval / activation /
#: promotion / control write.
FORBIDDEN_ACTIONS = (
    "order.submit_direct", "order.cancel_direct", "request.funded_entry",
    "dispatch.xavier_claim", "write.risk_limits", "write.credentials",
    "write.account_authority", "write.approvals",
    "write.submission_switches", "deploy", "write.policy_candidates",
    "write.policy_activation", "promotion", "write.entry_decisions",
    "write.management_decisions", "write.directives")
#: Prefixes denied wholesale ("dispatch.*", "order.*").
FORBIDDEN_PREFIXES = ("order.", "dispatch.", "request.")

R_NO_AUTHORITY = "KAREN_HAS_NO_AUTHORITY"
R_UNKNOWN_TARGET = "KAREN_CHALLENGES_ONLY_DEREK_XAVIER_AUDREY_OR_THE_ALLOCATOR"
R_BAD_CATEGORY = "THAT_IS_NOT_A_CHALLENGE_CATEGORY"
R_NOT_INDEPENDENT = "A_DISPUTE_IS_RESOLVED_BY_NEITHER_THE_TARGET_NOR_KAREN"
R_BAD_SEVERITY = "THAT_IS_NOT_A_CHALLENGE_SEVERITY"
R_TEXT = "A_NON_EMPTY_CLAIM_AND_DETECTOR_ARE_REQUIRED"
R_TIME = "A_CHALLENGE_CANNOT_PREDATE_THE_RECORD_IT_CHALLENGES"
R_UNGROUNDED = "A_CHALLENGE_NEEDS_AT_LEAST_ONE_RESOLVABLE_EVIDENCE_REFERENCE"
R_TARGET_MISSING = "THE_CHALLENGED_RECORD_DOES_NOT_EXIST"
R_NO_SUCH_CHALLENGE = "NO_SUCH_CHALLENGE"
R_NOT_THE_TARGET = "ONLY_THE_CHALLENGED_AGENT_RECORDS_THE_PEER_RESPONSE"
R_KAREN_CANNOT_RESPOND = "KAREN_CANNOT_ANSWER_HER_OWN_CHALLENGE"
R_SELF_RESOLUTION = "KAREN_CANNOT_RESOLVE_HER_OWN_CHALLENGE"
R_TARGET_CANNOT_REJECT = "THE_CHALLENGED_AGENT_MAY_CONCEDE_BUT_NEVER_REJECT"
R_NEEDS_RESPONSE = "A_CHALLENGE_IS_RESOLVED_ONLY_AFTER_THE_PEER_RESPONSE"
R_WRONG_STATE = "THAT_TRANSITION_IS_NOT_PERMITTED_FROM_THIS_STATE"
R_BAD_STANCE = "THE_RESPONSE_STANCE_IS_CONCEDE_OR_DISPUTE"
R_BAD_OUTCOME = "THE_OUTCOME_IS_UPHELD_OR_REJECTED"
R_BAD_ACTOR = "A_NAMED_ACTOR_IS_REQUIRED"
R_ONLY_KAREN_WITHDRAWS = "ONLY_KAREN_WITHDRAWS_HER_CHALLENGE"
R_NOT_BLOCKING = "ONLY_A_CHALLENGE_THAT_BLOCKED_SOMETHING_HAS_A_FALSE_BLOCK_RESULT"
R_NOT_FINISHED = "THE_CHALLENGE_IS_NOT_FINISHED"
R_NOT_UPHELD = "ONLY_AN_UPHELD_CHALLENGE_LINKS_AN_IMPROVEMENT"
R_ALREADY = "ALREADY_RECORDED"
R_NO_IMPROVEMENT = "THE_LINKED_IMPROVEMENT_DOES_NOT_EXIST"
R_AUTHORITY_KEYS = "A_CHALLENGE_CANNOT_CARRY_A_LIMIT_APPROVAL_OR_ACTIVATION"
R_NO_SCHEMA = "MIGRATION_207_IS_NOT_APPLIED"
R_DB_REFUSED = "THE_DATABASE_REFUSED_THE_CHALLENGE_WRITE"
R_NOT_AT_HYPOTHESIS = "KAREN_CHALLENGES_A_FINDING_AT_ITS_HYPOTHESIS_STAGE"


#: Keys no challenge body / impact may carry, at any depth (the database's
#: karen_no_authority() refuses the same at the top level).
AUTHORITY_KEYS = CL.AUTHORITY_KEYS | frozenset((
    "promote", "promotion", "release", "order", "submit"))


def authority_keys(obj, _path="") -> list:
    """Every key, at any depth, that would make a challenge carry a limit,
    approval, activation, promotion or order. Pure."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (_path, k) if _path else str(k)
            if str(k) in AUTHORITY_KEYS:
                out.append(p)
            out.extend(authority_keys(v, p))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out.extend(authority_keys(v, "%s[%d]" % (_path, i)))
    return out


class KarenHasNoAuthority(PermissionError):
    """Raised by `assert_may` for anything Karen may not do."""


def _ok(**kw) -> dict:
    return dict(ok=True, refusal=None, **kw)


def _no(refusal: str, **kw) -> dict:
    return dict(ok=False, refusal=refusal, **kw)


# ═════════════════════════════════════════════════════════════════════
# AUTHORITY: NONE
# ═════════════════════════════════════════════════════════════════════

_ACTOR_RX = (re.compile(r"^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE)[:/ ._-]+KAREN([^A-Z]|$)"),
             re.compile(r"KAREN[ ._:-]*(AGENT|BOT|RED[ ._-]*TEAM)"))


def is_karen(actor) -> bool:
    """Is `actor` Karen as an acting identity? Mirrors the database's
    karen_is_actor(): the agent id, or a machine-style label for her
    (agent:karen, slack:karen, karen-bot). A person named Karen is not."""
    if not isinstance(actor, str):
        return False
    a = actor.strip().upper()
    return a == KAREN or any(rx.search(a) for rx in _ACTOR_RX)


def may(tool: str) -> bool:
    """Karen's permission for `tool`: on her allow list, and not forbidden
    (exactly, or by a forbidden prefix such as dispatch.*). Pure."""
    t = str(tool or "")
    if t in FORBIDDEN_ACTIONS or t.startswith(FORBIDDEN_PREFIXES):
        return False
    return R.permits(KAREN, t)


def assert_may(tool: str) -> None:
    if not may(tool):
        raise KarenHasNoAuthority("%s: %s" % (R_NO_AUTHORITY, tool))


def refuse_authority(action: str) -> dict:
    """The one answer to any order / approval / activation / promotion /
    limit / control request made of Karen: a named refusal. Pure."""
    return _no(R_NO_AUTHORITY, action=str(action),
               why="Karen challenges records with evidence; she holds no "
                   "order path, venue submission, capital, risk-limit, "
                   "approval, activation or promotion authority")


# ═════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════

def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(float(v)) else None


def _text(v, n=4000) -> bool:
    return isinstance(v, str) and 0 < len(v.strip()) <= n


def _ep(v):
    return v.timestamp() if isinstance(v, datetime) else v


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if k in ("evidence_refs", "body", "response_evidence_refs",
                 "false_block_evidence_refs", "downstream_impact", "detail",
                 "resolution_evidence_refs"):
            v = _j(v)
        out[k] = _ep(v)
    return out


def challenge_id_for(detector: str, target_kind: str, target_id: str) -> str:
    raw = json.dumps([str(detector), str(target_kind), str(target_id)])
    return "kch:%s" % hashlib.sha256(raw.encode()).hexdigest()[:24]


def _actor(v) -> str | None:
    if not isinstance(v, str) or not 2 <= len(v.strip()) <= 100:
        return None
    return v.strip()


async def schema(conn) -> bool:
    """Migrations 207 AND 212 (the category column) are applied."""
    try:
        return await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            " WHERE table_name='karen_challenges' AND column_name='category')"
        ) is True
    except Exception:                                           # noqa: BLE001
        return False


async def _event(conn, cid: str, kind: str, actor: str, at: float,
                 detail: dict | None = None) -> None:
    await conn.execute(
        "INSERT INTO karen_challenge_events (challenge_id, at, kind, actor, "
        " detail) VALUES ($1, to_timestamp($2), $3, $4, $5::jsonb)",
        cid, float(at), kind, actor, json.dumps(detail or {}, default=str))


def check_open(rec: dict) -> str | None:
    """THE PURE GUARDS FOR A NEW CHALLENGE (the database enforces the same):
    None when `rec` may be opened, else the refusal."""
    if not may("write.challenges"):
        return R_NO_AUTHORITY
    if rec.get("target_agent") not in TARGETS:
        return R_UNKNOWN_TARGET
    if rec.get("severity") not in SEVERITIES:
        return R_BAD_SEVERITY
    if rec.get("category") is not None and rec["category"] not in CATEGORIES:
        return R_BAD_CATEGORY
    if not _text(rec.get("claim")) or not _text(rec.get("detector"), 100) \
            or not _text(rec.get("target_kind"), 100) \
            or rec.get("target_id") is None \
            or not _text(str(rec.get("target_id")), 300):
        return R_TEXT
    if rec.get("target_kind") not in CL.EVIDENCE_KINDS:
        return CL.R_UNKNOWN_KIND
    ra, at = _num(rec.get("record_at")), _num(rec.get("at"))
    if ra is None or at is None or ra > at:
        return R_TIME
    if authority_keys(rec.get("body") or {}):
        return R_AUTHORITY_KEYS
    n = CL.normalise_refs(rec.get("evidence_refs"))
    if not n["ok"]:
        return R_UNGROUNDED if n["refusal"] == CL.R_UNGROUNDED \
            else n["refusal"]
    return None


async def _insert(conn, rec: dict, refs: list) -> bool:
    """Insert one challenge + its OPENED event (raises on refusal). Returns
    False when the challenge already exists (idempotent replay)."""
    cid = rec["challenge_id"]
    res = await conn.execute(
        "INSERT INTO karen_challenges (challenge_id, target_agent, "
        " target_kind, target_id, finding_id, detector, claim, severity, "
        " evidence_refs, body, account_id, record_at, challenged_at, blocked,"
        " category) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11,"
        " to_timestamp($12),to_timestamp($13),$14,$15) ON CONFLICT DO NOTHING",
        cid, rec["target_agent"], rec["target_kind"], str(rec["target_id"]),
        rec.get("finding_id"), rec["detector"], rec["claim"].strip(),
        rec["severity"], json.dumps(refs),
        json.dumps(rec.get("body") or {}, default=str), rec.get("account_id"),
        float(rec["record_at"]), float(rec["at"]),
        bool(rec.get("blocked")),
        rec.get("category") or category_of(rec["detector"]))
    if not res.endswith("1"):
        return False
    await _event(conn, cid, "OPENED", KAREN, rec["at"], {
        "target_agent": rec["target_agent"],
        "target": {"kind": rec["target_kind"], "id": str(rec["target_id"])},
        "severity": rec["severity"], "detector": rec["detector"],
        "category": rec.get("category") or category_of(rec["detector"]),
        "evidence_refs": refs})
    return True


# ═════════════════════════════════════════════════════════════════════
# OPENING A CHALLENGE (KAREN)
# ═════════════════════════════════════════════════════════════════════

async def open_challenge(conn, *, target_agent: str, target_kind: str,
                         target_id: str, detector: str, claim: str,
                         severity: str, evidence_refs: list,
                         record_at: float, at: float,
                         account_id: str | None = None,
                         body: dict | None = None,
                         challenge_id: str | None = None,
                         category: str | None = None) -> dict:
    """OPEN A GROUNDED CHALLENGE, idempotently on (detector, target).

    The challenged record and EVERY cited evidence record must exist; at
    least one evidence reference is required. Never raises."""
    rec = {"target_agent": str(target_agent or "").upper(),
           "target_kind": target_kind, "target_id": target_id,
           "detector": detector, "claim": claim, "severity": severity,
           "evidence_refs": evidence_refs, "record_at": record_at, "at": at,
           "account_id": account_id, "body": body or {},
           "category": category or category_of(detector)}
    why = check_open(rec)
    if why:
        return _no(why)
    if target_kind == "agent_findings":
        return _no(CL.R_BAD_REF, why="a finding is challenged through "
                                     "challenge_finding (the loop stage)")
    if not await schema(conn):
        return _no(R_NO_SCHEMA)
    refs = CL.normalise_refs(evidence_refs)["refs"]
    cid = challenge_id or challenge_id_for(detector, target_kind, target_id)
    rec["challenge_id"] = cid
    try:
        async with conn.transaction():
            have = await conn.fetchval(
                "SELECT challenge_id FROM karen_challenges WHERE "
                " challenge_id=$1 OR (detector=$2 AND target_kind=$3 AND "
                " target_id=$4)", cid, detector, target_kind, str(target_id))
            if have is not None:
                return _ok(challenge_id=have, created=False)
            t = await CL.verify_refs_exist(
                conn, [{"kind": target_kind, "id": str(target_id)}])
            if not t["ok"]:
                return _no(R_TARGET_MISSING, target={"kind": target_kind,
                                                     "id": str(target_id)})
            v = await CL.verify_refs_exist(conn, refs)
            if not v["ok"]:
                return _no(R_UNGROUNDED, ref=v.get("ref"))
            created = await _insert(conn, rec, refs)
    except Exception as exc:                                    # noqa: BLE001
        return _no(R_DB_REFUSED, error="%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200]))
    return _ok(challenge_id=cid, created=created, state=OPEN,
               production_effect="NONE")


async def challenge_finding(conn, finding_id: str, *, claim: str,
                            outcome: str, evidence_refs: list, at: float,
                            severity: str = "MEDIUM",
                            detector: str = LOOP_DETECTOR) -> dict:
    """KAREN AS THE LOOP'S CHALLENGER: record the PEER_CHALLENGE stage of
    another agent's finding (collaboration_loop.record_challenge, so every
    loop guard applies -- and the database refuses a self-challenge) AND
    the karen_challenges row, in one transaction. A REFUTED challenge stops
    the finding (it can only close), so it is recorded as blocking and is
    later assessed for false block. Never raises."""
    if not may("write.challenges"):
        return _no(R_NO_AUTHORITY)
    if outcome not in CL.CHALLENGE_OUTCOMES:
        return _no(CL.R_BAD_OUTCOME)
    if not await schema(conn):
        return _no(R_NO_SCHEMA)
    n = CL.normalise_refs(evidence_refs)
    if not n["ok"]:
        return _no(R_UNGROUNDED if n["refusal"] == CL.R_UNGROUNDED
                   else n["refusal"])
    refs = n["refs"]
    try:
        async with conn.transaction():
            f = await conn.fetchrow(
                "SELECT f.finding_id, f.proposer, f.stage, s.at AS hyp_at "
                "  FROM agent_findings f LEFT JOIN agent_finding_stages s "
                "    ON s.finding_id=f.finding_id AND s.seq=2 "
                " WHERE f.finding_id=$1 FOR UPDATE OF f", str(finding_id))
            if f is None:
                return _no(CL.R_NO_SUCH_FINDING, finding_id=finding_id)
            if f["stage"] != CL.HYPOTHESIS:
                return _no(R_NOT_AT_HYPOTHESIS, stage=f["stage"])
            rec = {"target_agent": f["proposer"],
                   "target_kind": "agent_findings",
                   "target_id": f["finding_id"],
                   "finding_id": f["finding_id"], "detector": detector,
                   "claim": claim, "severity": severity,
                   "evidence_refs": refs, "record_at": _ep(f["hyp_at"]),
                   "at": at, "blocked": outcome == "REFUTED",
                   "body": {"loop_outcome": outcome}}
            why = check_open(rec)
            if why:
                return _no(why)
            v = await CL.verify_refs_exist(conn, refs)
            if not v["ok"]:
                return _no(R_UNGROUNDED, ref=v.get("ref"))
            got = await CL.record_challenge(
                conn, f["finding_id"], actor=KAREN, challenge=claim,
                outcome=outcome, evidence_refs=refs, at=at)
            if not got["ok"]:
                return got
            rec["challenge_id"] = challenge_id_for(detector, "agent_findings",
                                                   f["finding_id"])
            created = await _insert(conn, rec, refs)
            if not created:
                raise RuntimeError("challenge already recorded")
    except Exception as exc:                                    # noqa: BLE001
        return _no(R_DB_REFUSED, error="%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200]))
    return _ok(challenge_id=rec["challenge_id"], created=True,
               finding_id=f["finding_id"], loop_outcome=outcome,
               blocked=outcome == "REFUTED", production_effect="NONE")


# ═════════════════════════════════════════════════════════════════════
# THE PEER RESPONSE, THE OUTCOME, AND WHAT FOLLOWS
# ═════════════════════════════════════════════════════════════════════

async def _locked(conn, cid: str):
    return await conn.fetchrow(
        "SELECT * FROM karen_challenges WHERE challenge_id=$1 FOR UPDATE",
        str(cid))


async def respond(conn, challenge_id: str, *, agent: str, stance: str,
                  response: str, at: float,
                  evidence_refs: list | None = None) -> dict:
    """THE TARGET AGENT'S PEER RESPONSE (OPEN -> RESPONDED). Only the
    challenged agent may answer; Karen never answers for anyone."""
    who = str(agent or "").strip().upper()
    if is_karen(who):
        return _no(R_KAREN_CANNOT_RESPOND)
    if stance not in STANCES:
        return _no(R_BAD_STANCE)
    if not _text(response) or _num(at) is None:
        return _no(R_TEXT)
    refs = None
    if evidence_refs:
        n = CL.normalise_refs(evidence_refs)
        if not n["ok"]:
            return n
        refs = n["refs"]
    if not await schema(conn):
        return _no(R_NO_SCHEMA)
    try:
        async with conn.transaction():
            c = await _locked(conn, challenge_id)
            if c is None:
                return _no(R_NO_SUCH_CHALLENGE, challenge_id=challenge_id)
            if who != c["target_agent"]:
                return _no(R_NOT_THE_TARGET, target_agent=c["target_agent"])
            if c["state"] != OPEN:
                return _no(R_WRONG_STATE, state=c["state"])
            if float(at) < _ep(c["challenged_at"]):
                return _no(R_TIME)
            if refs:
                v = await CL.verify_refs_exist(conn, refs)
                if not v["ok"]:
                    return v
            await conn.execute(
                "UPDATE karen_challenges SET state='RESPONDED', "
                " response_stance=$2, response=$3, response_evidence_refs="
                " $4::jsonb, responded_by=$5, responded_at=to_timestamp($6)"
                " WHERE challenge_id=$1", c["challenge_id"], stance,
                response.strip(), None if refs is None else json.dumps(refs),
                who, float(at))
            await _event(conn, c["challenge_id"], "RESPONDED", who, at,
                         {"stance": stance, "evidence_refs": refs or []})
    except Exception as exc:                                    # noqa: BLE001
        return _no(R_DB_REFUSED, error="%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200]))
    return _ok(challenge_id=challenge_id, state=RESPONDED, stance=stance)


async def resolve(conn, challenge_id: str, *, resolver: str, outcome: str,
                  reason: str, at: float,
                  evidence_refs: list | None = None) -> dict:
    """RECORD THE OUTCOME (RESPONDED -> UPHELD | REJECTED), with the
    independent evaluation's evidence. Never by Karen; the target may
    concede (UPHELD) but never reject, and a DISPUTED challenge is resolved
    by neither the target nor Karen; only after the peer response."""
    refs = None
    if evidence_refs:
        n = CL.normalise_refs(evidence_refs)
        if not n["ok"]:
            return n
        refs = n["refs"]
    who = _actor(resolver)
    if who is None:
        return _no(R_BAD_ACTOR)
    if is_karen(who):
        return _no(R_SELF_RESOLUTION)
    if outcome not in (UPHELD, REJECTED):
        return _no(R_BAD_OUTCOME)
    if not _text(reason, 2000) or _num(at) is None:
        return _no(R_TEXT)
    if not await schema(conn):
        return _no(R_NO_SCHEMA)
    try:
        async with conn.transaction():
            c = await _locked(conn, challenge_id)
            if c is None:
                return _no(R_NO_SUCH_CHALLENGE, challenge_id=challenge_id)
            if c["state"] == OPEN:
                return _no(R_NEEDS_RESPONSE)
            if c["state"] != RESPONDED:
                return _no(R_WRONG_STATE, state=c["state"])
            if outcome == REJECTED and who.upper() == c["target_agent"]:
                return _no(R_TARGET_CANNOT_REJECT)
            if c["response_stance"] == "DISPUTE" and \
                    who.upper() == c["target_agent"]:
                return _no(R_NOT_INDEPENDENT)
            if float(at) < _ep(c["responded_at"]):
                return _no(R_TIME)
            if refs:
                v = await CL.verify_refs_exist(conn, refs)
                if not v["ok"]:
                    return v
            await conn.execute(
                "UPDATE karen_challenges SET state=$2, outcome=$2, "
                " outcome_reason=$3, resolved_by=$4, "
                " resolved_at=to_timestamp($5), "
                " resolution_evidence_refs=$6::jsonb WHERE challenge_id=$1",
                c["challenge_id"], outcome, reason.strip(), who, float(at),
                None if refs is None else json.dumps(refs))
            await _event(conn, c["challenge_id"], outcome, who, at,
                         {"reason": reason.strip()[:2000],
                          "evidence_refs": refs or []})
    except Exception as exc:                                    # noqa: BLE001
        return _no(R_DB_REFUSED, error="%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200]))
    return _ok(challenge_id=challenge_id, state=outcome, resolved_by=who)


async def withdraw(conn, challenge_id: str, *, reason: str, at: float,
                   actor: str = KAREN) -> dict:
    """KAREN RETRACTS HER OWN CHALLENGE (OPEN | RESPONDED -> WITHDRAWN).
    Not a resolution: a withdrawn challenge counts AGAINST her precision."""
    if str(actor or "").strip().upper() != KAREN:
        return _no(R_ONLY_KAREN_WITHDRAWS)
    if not _text(reason, 2000) or _num(at) is None:
        return _no(R_TEXT)
    if not await schema(conn):
        return _no(R_NO_SCHEMA)
    try:
        async with conn.transaction():
            c = await _locked(conn, challenge_id)
            if c is None:
                return _no(R_NO_SUCH_CHALLENGE, challenge_id=challenge_id)
            if c["state"] not in (OPEN, RESPONDED):
                return _no(R_WRONG_STATE, state=c["state"])
            await conn.execute(
                "UPDATE karen_challenges SET state='WITHDRAWN', "
                " outcome='WITHDRAWN', outcome_reason=$2, resolved_by='KAREN',"
                " resolved_at=to_timestamp($3) WHERE challenge_id=$1",
                c["challenge_id"], reason.strip(), float(at))
            await _event(conn, c["challenge_id"], "WITHDRAWN", KAREN, at,
                         {"reason": reason.strip()[:2000]})
    except Exception as exc:                                    # noqa: BLE001
        return _no(R_DB_REFUSED, error="%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200]))
    return _ok(challenge_id=challenge_id, state=WITHDRAWN)


async def assess_false_block(conn, challenge_id: str, *, assessor: str,
                             false_block: bool, evidence_refs: list,
                             at: float) -> dict:
    """THE FALSE-BLOCK RESULT of a finished challenge that blocked or
    delayed something: did what it stopped later prove fine? Grounded,
    once, never by Karen."""
    who = _actor(assessor)
    if who is None:
        return _no(R_BAD_ACTOR)
    if is_karen(who):
        return _no(R_SELF_RESOLUTION)
    if not isinstance(false_block, bool) or _num(at) is None:
        return _no(R_TEXT)
    n = CL.normalise_refs(evidence_refs)
    if not n["ok"]:
        return _no(R_UNGROUNDED if n["refusal"] == CL.R_UNGROUNDED
                   else n["refusal"])
    if not await schema(conn):
        return _no(R_NO_SCHEMA)
    try:
        async with conn.transaction():
            c = await _locked(conn, challenge_id)
            if c is None:
                return _no(R_NO_SUCH_CHALLENGE, challenge_id=challenge_id)
            if not c["blocked"]:
                return _no(R_NOT_BLOCKING)
            if c["state"] not in TERMINAL:
                return _no(R_NOT_FINISHED, state=c["state"])
            if c["false_block"] is not None:
                return _no(R_ALREADY)
            v = await CL.verify_refs_exist(conn, n["refs"])
            if not v["ok"]:
                return v
            await conn.execute(
                "UPDATE karen_challenges SET false_block=$2, "
                " false_block_assessed_by=$3, false_block_assessed_at="
                " to_timestamp($4), false_block_evidence_refs=$5::jsonb "
                " WHERE challenge_id=$1", c["challenge_id"], false_block,
                who, float(at), json.dumps(n["refs"]))
            await _event(conn, c["challenge_id"], "FALSE_BLOCK_ASSESSED",
                         who, at, {"false_block": false_block,
                                   "evidence_refs": n["refs"]})
    except Exception as exc:                                    # noqa: BLE001
        return _no(R_DB_REFUSED, error="%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200]))
    return _ok(challenge_id=challenge_id, false_block=false_block)


async def link_improvement(conn, challenge_id: str, *, actor: str, at: float,
                           finding_id: str | None = None,
                           proposal_id: str | None = None,
                           impact: dict | None = None) -> dict:
    """DOWNSTREAM: the improvement an UPHELD challenge led to (a loop
    finding and/or a paper improvement proposal). Once, never by Karen."""
    who = _actor(actor)
    if who is None:
        return _no(R_BAD_ACTOR)
    if is_karen(who):
        return _no(R_SELF_RESOLUTION)
    if not finding_id and not proposal_id:
        return _no(R_NO_IMPROVEMENT)
    if authority_keys(impact or {}):
        return _no(R_AUTHORITY_KEYS)
    if not await schema(conn):
        return _no(R_NO_SCHEMA)
    try:
        async with conn.transaction():
            c = await _locked(conn, challenge_id)
            if c is None:
                return _no(R_NO_SUCH_CHALLENGE, challenge_id=challenge_id)
            if c["state"] != UPHELD:
                return _no(R_NOT_UPHELD, state=c["state"])
            if c["improvement_linked_by"] is not None:
                return _no(R_ALREADY)
            refs = ([{"kind": "agent_findings", "id": str(finding_id)}]
                    if finding_id else []) + (
                [{"kind": "paper_improvement_proposals",
                  "id": str(proposal_id)}] if proposal_id else [])
            v = await CL.verify_refs_exist(conn, refs)
            if not v["ok"]:
                return _no(R_NO_IMPROVEMENT, ref=v.get("ref"))
            await conn.execute(
                "UPDATE karen_challenges SET improvement_finding_id=$2, "
                " improvement_proposal_id=$3, improvement_linked_by=$4, "
                " improvement_linked_at=to_timestamp($5), "
                " downstream_impact=$6::jsonb WHERE challenge_id=$1",
                c["challenge_id"], finding_id, proposal_id, who, float(at),
                None if impact is None else json.dumps(impact, default=str))
            await _event(conn, c["challenge_id"], "IMPROVEMENT_LINKED", who,
                         at, {"finding_id": finding_id,
                              "proposal_id": proposal_id})
    except Exception as exc:                                    # noqa: BLE001
        return _no(R_DB_REFUSED, error="%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200]))
    return _ok(challenge_id=challenge_id, finding_id=finding_id,
               proposal_id=proposal_id)


# ═════════════════════════════════════════════════════════════════════
# READS
# ═════════════════════════════════════════════════════════════════════

def _with_links(c: dict) -> dict:
    c["evidence"] = [dict(r, href=None) for r in (c.get("evidence_refs")
                                                   or [])]
    c["evidence"].append({"kind": "karen_challenges",
                          "id": c["challenge_id"],
                          "href": "/api/command/karen/challenges/%s"
                                  % c["challenge_id"]})
    rec_at, ch_at = _num(c.get("record_at")), _num(c.get("challenged_at"))
    c["time_to_challenge_s"] = (None if rec_at is None or ch_at is None
                                else round(ch_at - rec_at, 3))
    c["target_decision"] = {"kind": c.get("target_kind"),
                            "id": c.get("target_id")}
    c["peer_response"] = None if not c.get("responded_by") else {
        "by": c["responded_by"], "stance": c.get("response_stance"),
        "response": c.get("response"), "at": c.get("responded_at"),
        "evidence_refs": c.get("response_evidence_refs") or []}
    ev = EVALUATOR_FOR.get(c.get("target_agent"))
    if c.get("state") in (UPHELD, REJECTED):
        c["independent_evaluation"] = {
            "status": "RECORDED", "outcome": c.get("outcome"),
            "by": c.get("resolved_by"), "reason": c.get("outcome_reason"),
            "at": c.get("resolved_at"),
            "evidence_refs": c.get("resolution_evidence_refs") or [],
            "independent": str(c.get("resolved_by") or "").upper() not in (
                KAREN, c.get("target_agent"))}
    elif c.get("state") == WITHDRAWN:
        c["independent_evaluation"] = {"status": "NOT_NEEDED_WITHDRAWN",
                                       "by": None}
    else:
        c["independent_evaluation"] = {
            "status": "AWAITING_PEER_RESPONSE" if c.get("state") == OPEN
            else "AWAITING_EVALUATION", "evaluator": ev, "by": None}
    if not c.get("blocked"):
        c["false_block_outcome"] = "NOT_BLOCKING"
    elif c.get("false_block") is None:
        c["false_block_outcome"] = "BLOCKING_NOT_YET_ASSESSED"
    else:
        c["false_block_outcome"] = ("FALSE_BLOCK" if c["false_block"]
                                    else "BLOCK_JUSTIFIED")
    if c.get("improvement_linked_by"):
        c["downstream"] = {
            "status": "IMPROVEMENT_LINKED",
            "finding_id": c.get("improvement_finding_id"),
            "proposal_id": c.get("improvement_proposal_id"),
            "linked_by": c.get("improvement_linked_by"),
            "impact": c.get("downstream_impact")}
    else:
        c["downstream"] = {"status": "NONE_RECORDED" if c.get("state") ==
                           UPHELD else "NOT_APPLICABLE_UNTIL_UPHELD"}
    return c


async def challenges(conn, *, state: str | None = None,
                     target_agent: str | None = None,
                     limit: int = 50) -> list:
    rows = await conn.fetch(
        "SELECT * FROM karen_challenges WHERE ($1::text IS NULL OR state=$1)"
        "   AND ($2::text IS NULL OR target_agent=$2) "
        " ORDER BY challenged_at DESC, challenge_id LIMIT $3", state,
        None if target_agent is None else str(target_agent).upper(),
        max(1, min(int(limit or 50), 500)))
    return [_with_links(_row(r)) for r in rows]


async def challenge(conn, challenge_id: str) -> dict | None:
    r = await conn.fetchrow(
        "SELECT * FROM karen_challenges WHERE challenge_id=$1",
        str(challenge_id))
    if r is None:
        return None
    ev = await conn.fetch(
        "SELECT event_id, challenge_id, at, kind, actor, detail "
        "  FROM karen_challenge_events WHERE challenge_id=$1 "
        " ORDER BY event_id", str(challenge_id))
    return {"challenge": _with_links(_row(r)),
            "events": [_row(e) for e in ev], "production_effect": "NONE"}


# ═════════════════════════════════════════════════════════════════════
# METRICS: numerator / denominator, null when unmeasurable (never 0)
# ═════════════════════════════════════════════════════════════════════

METRIC_SAMPLE = 5000

DEFINITIONS = {
    "evidence_grounding": (
        "challenges with at least one evidence reference that resolves to "
        "an existing record NOW / challenges raised"),
    "valid_defect_discovery": (
        "UPHELD challenges (value = the count) / challenges raised; "
        "measurable once any challenge has an outcome"),
    "challenge_precision": (
        "UPHELD / (UPHELD + REJECTED + WITHDRAWN): a withdrawn challenge "
        "counts against Karen, so withdrawing cannot inflate precision"),
    "false_block_rate": (
        "blocking challenges later assessed as a false block / blocking "
        "challenges with a false-block assessment"),
    "time_to_challenge": (
        "seconds from the challenged record's own time (record_at) to the "
        "challenge (challenged_at): value = mean = total seconds / "
        "challenges; median and p90 alongside"),
    "downstream_improvement": (
        "UPHELD challenges linked to an ADOPTED improvement (a linked "
        "finding marked ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW, or a linked "
        "paper proposal EVALUATED with verdict PASS) / UPHELD challenges"),
}


def _metric(name: str, num, den, *, value=None, why=None, **extra) -> dict:
    """One metric. Unmeasurable (nothing to divide by, or a named reason):
    numerator and value are null -- never 0 -- and `why` says why."""
    measurable = why is None and bool(den)
    if measurable and value is None:
        value = round(num / den, 6)
    return dict({"name": name, "definition": DEFINITIONS[name],
                 "numerator": num if measurable else None,
                 "denominator": den,
                 "value": value if measurable else None,
                 "measurable": measurable,
                 "why": None if measurable else (
                     why or "NOTHING_TO_MEASURE_YET")}, **extra)


async def _resolvable(conn, refs_by_kind: dict) -> set:
    """{(kind, id)} that resolve to an existing record. Fixed table/column
    per kind; an unreadable kind resolves nothing (never assumed)."""
    out = set()
    for kind, ids in refs_by_kind.items():
        if kind not in CL.EVIDENCE_KINDS or not ids:
            continue
        table, col = CL.EVIDENCE_KINDS[kind]
        try:
            if await conn.fetchval("SELECT to_regclass($1)", table) is None:
                continue
            col = await CL.key_column(conn, table, col)
            if col is None:
                continue
            rows = await conn.fetch(
                "SELECT %s::text AS k FROM %s WHERE %s::text = ANY($1::text[])"
                % (col, table, col), sorted(ids))
        except Exception:                                       # noqa: BLE001
            continue
        out.update((kind, r["k"]) for r in rows)
    return out


def summarise_metrics(rows: list, resolvable: set, adopted: set, *,
                      truncated: bool = False) -> dict:
    """THE SIX METRICS from challenge rows (pure). `resolvable` is the set
    of (kind, id) that exist; `adopted` the set of challenge ids whose
    linked improvement was adopted."""
    total = len(rows)
    st = {s: sum(1 for r in rows if r.get("state") == s) for s in STATES}
    grounded = sum(1 for r in rows if any(
        (e.get("kind"), str(e.get("id"))) in resolvable
        for e in (r.get("evidence_refs") or []) if isinstance(e, dict)))
    finished = st[UPHELD] + st[REJECTED] + st[WITHDRAWN]
    blocked_assessed = [r for r in rows if r.get("blocked")
                        and r.get("false_block") is not None]
    secs = [float(r["challenged_at"]) - float(r["record_at"]) for r in rows
            if _num(r.get("challenged_at")) is not None
            and _num(r.get("record_at")) is not None]
    upheld_ids = {r["challenge_id"] for r in rows if r.get("state") == UPHELD}
    out = {
        "evidence_grounding": _metric(
            "evidence_grounding", grounded, total,
            why=None if total else "NO_CHALLENGE_HAS_BEEN_RAISED"),
        "valid_defect_discovery": _metric(
            "valid_defect_discovery", st[UPHELD], total,
            value=st[UPHELD] if finished else None,
            why=None if finished else (
                "NO_CHALLENGE_HAS_AN_OUTCOME_YET" if total
                else "NO_CHALLENGE_HAS_BEEN_RAISED"),
            rate=round(st[UPHELD] / total, 6) if finished and total
            else None),
        "challenge_precision": _metric(
            "challenge_precision", st[UPHELD], finished,
            why=None if finished else "NO_CHALLENGE_HAS_AN_OUTCOME_YET"),
        "false_block_rate": _metric(
            "false_block_rate",
            sum(1 for r in blocked_assessed if r.get("false_block") is True),
            len(blocked_assessed),
            why=None if blocked_assessed else (
                "NO_BLOCKING_CHALLENGE_HAS_BEEN_ASSESSED"),
            blocking_challenges=sum(1 for r in rows if r.get("blocked")),
            blocking_unassessed=sum(1 for r in rows if r.get("blocked")
                                    and r.get("false_block") is None)),
        "time_to_challenge": _metric(
            "time_to_challenge", round(sum(secs), 3) if secs else None,
            len(secs),
            value=round(sum(secs) / len(secs), 3) if secs else None,
            why=None if secs else "NO_CHALLENGE_HAS_BEEN_RAISED",
            unit="seconds",
            median_s=round(statistics.median(secs), 3) if secs else None,
            p90_s=round(sorted(secs)[min(len(secs) - 1,
                                         int(0.9 * len(secs)))], 3)
            if secs else None),
        "downstream_improvement": _metric(
            "downstream_improvement", len(upheld_ids & adopted),
            len(upheld_ids),
            why=None if upheld_ids else "NO_CHALLENGE_HAS_BEEN_UPHELD"),
    }
    return {"metrics": out, "challenges_counted": total,
            "by_state": st, "sample_limit": METRIC_SAMPLE,
            "truncated": truncated,
            "rule": "null means unmeasurable, never zero"}


async def metrics(conn) -> dict:
    """The six metrics over Karen's challenges (the most recent
    METRIC_SAMPLE). Raises on a failed read (the API says UNAVAILABLE)."""
    raw = await conn.fetch(
        "SELECT challenge_id, state, evidence_refs, blocked, false_block, "
        "       record_at, challenged_at, improvement_finding_id, "
        "       improvement_proposal_id "
        "  FROM karen_challenges ORDER BY challenged_at DESC LIMIT $1",
        METRIC_SAMPLE + 1)
    rows = [_row(r) for r in raw[:METRIC_SAMPLE]]
    by_kind: dict[str, set] = {}
    for r in rows:
        for e in r.get("evidence_refs") or []:
            if isinstance(e, dict) and e.get("kind") and e.get("id"):
                by_kind.setdefault(e["kind"], set()).add(str(e["id"]))
    resolvable = await _resolvable(conn, by_kind)
    adopted: set = set()
    fids = {r["improvement_finding_id"]: r["challenge_id"] for r in rows
            if r.get("improvement_finding_id")}
    pids = {r["improvement_proposal_id"]: r["challenge_id"] for r in rows
            if r.get("improvement_proposal_id")}
    if fids:
        for x in await conn.fetch(
                "SELECT DISTINCT finding_id FROM agent_finding_stages "
                " WHERE finding_id = ANY($1::text[]) AND stage = "
                " 'RELEASE_ELIGIBILITY'", list(fids)):
            adopted.add(fids[x["finding_id"]])
    if pids:
        for x in await conn.fetch(
                "SELECT proposal_id FROM paper_improvement_proposals "
                " WHERE proposal_id = ANY($1::text[]) AND status='EVALUATED'"
                "   AND verdict='PASS'", list(pids)):
            adopted.add(pids[x["proposal_id"]])
    return summarise_metrics(rows, resolvable, adopted,
                             truncated=len(raw) > METRIC_SAMPLE)


async def profile(conn) -> dict:
    """Identity + status (registry), and what Karen may and may not do."""
    st = await R.status_of(conn, KAREN)
    ident = R.IDENTITIES[KAREN]
    return {"agent_id": KAREN, "display_name": ident["display_name"],
            "role": ident["role"], "mandate": ident["mandate"],
            "tool_permissions": ident["tool_permissions"],
            "forbidden_actions": list(FORBIDDEN_ACTIONS),
            "authority": "NONE",
            "registered": st is not None, "status": st}


# ═════════════════════════════════════════════════════════════════════
# SLACK: WHAT KAREN SAYS, FROM HER RECORDS ONLY
# ═════════════════════════════════════════════════════════════════════

def challenge_post(c: dict) -> str:
    """The text of one challenge for the workroom: the record ids, the
    claim, and the next step. Pure; no language model."""
    refs = ", ".join("%s %s" % (e.get("kind"), e.get("id"))
                     for e in (c.get("evidence_refs") or [])[:5])
    return ("Karen · red-team challenge %s · %s\n"
            "To %s about %s %s: %s\nEvidence: %s\n"
            "Next: %s records a response (CONCEDE or DISPUTE). Karen cannot "
            "resolve her own challenge. Stage: grounded challenge; nothing is "
            "blocked, approved or activated by this message."
            % (c.get("challenge_id"), c.get("severity"),
               str(c.get("target_agent") or "").title(),
               c.get("target_kind"), c.get("target_id"),
               str(c.get("claim") or "")[:1500], refs or "none",
               str(c.get("target_agent") or "").title()))


async def slack_answer(conn, *, limit: int = 5) -> str:
    """Karen's answer to a Slack mention: her open challenges and metrics,
    read from the records (no language model, no instruction followed from
    the question)."""
    rows = [c for c in await challenges(conn, limit=200)
            if c.get("state") in (OPEN, RESPONDED)][:max(1, int(limit))]
    met = (await metrics(conn))["metrics"]
    lines = ["Karen · red team · answered from records only (no language "
             "model); I hold no order, approval, activation or promotion "
             "authority."]
    if rows:
        lines.append("Open challenges (%d shown):" % len(rows))
        for c in rows:
            lines.append("- %s · %s · %s %s %s · %s" % (
                c["challenge_id"], c["severity"], c["target_agent"].title(),
                c["target_kind"], c["target_id"], c["state"]))
    else:
        lines.append("No challenge is open.")
    p = met["challenge_precision"]
    g = met["evidence_grounding"]
    lines.append("Precision: %s · Grounding: %s" % (
        "unmeasurable (%s)" % p["why"] if p["value"] is None
        else "%s/%s" % (p["numerator"], p["denominator"]),
        "unmeasurable (%s)" % g["why"] if g["value"] is None
        else "%s/%s" % (g["numerator"], g["denominator"])))
    lines.append("Record: https://command.bettortoken.com/karen")
    return "\n".join(lines)


def outcome_post(c: dict) -> str:
    """The workroom text of a resolved challenge: the peer response and the
    independent evaluation, with their record ids. Pure."""
    pr = c.get("peer_response") or {}
    ie = c.get("independent_evaluation") or {}
    refs = ", ".join("%s %s" % (e.get("kind"), e.get("id"))
                     for e in (ie.get("evidence_refs") or [])[:5])
    return ("Karen · challenge %s · %s by %s (independent evaluation)\n"
            "Target: %s, %s %s. Peer response: %s by %s.\nEvaluation: %s\n"
            "Evidence: %s\nStage: recorded outcome; nothing is approved, "
            "activated or changed by this message."
            % (c.get("challenge_id"), c.get("outcome"), ie.get("by"),
               str(c.get("target_agent") or "").title(), c.get("target_kind"),
               c.get("target_id"), pr.get("stance") or "none",
               pr.get("by") or "nobody", str(ie.get("reason") or "")[:1200],
               refs or "none"))
