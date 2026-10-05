"""THE IMPROVEMENT PIPELINE'S RULES, PURE (migration 221).

One improvement item walks the canonical collaboration workflow, forward
only, none skipped:

  1 EVIDENCE                RUNNER (a real signal) or the OWNER agent
  2 HYPOTHESIS              the OWNER agent, once
  3 PEER_CHALLENGE          KAREN (CHALLENGER) and another agent
                            (PEER_AGENT) -- both are required
  4 OWNER_RESPONSE          the OWNER agent (or a HUMAN owner)
  5 EXPERIMENT              OWNER / ENGINEERING / HUMAN: experiment ids,
                            candidate patch reference (branch / commit SHA /
                            PR URL, TEXT ONLY) and a tests reference
  6 INDEPENDENT_EVALUATION  an evaluator who is not the owner and authored
                            neither the hypothesis nor the experiment
  7 ELIGIBLE_CHANGE         enough distinct independent PASSes (1, or 2 when
                            a protected area is touched) and no FAIL; a
                            HUMAN records it for a protected area
  8 CONTROLLED_RELEASE      a HUMAN approver (never an agent / system name)
                            with an exact-SHA gate receipt
  9 FORWARD_RESULT          the monitoring window and its forward result
 98 ROLLED_BACK / 99 CLOSED terminal

This module holds no I/O. The runner (agents/improvement_pipeline.py), the
read endpoints (api/command_improvements.py) and the Slack digest
(slack_bridge.publish_improvement_posts) share it. EVERY RULE HERE IS ALSO
ENFORCED BY THE DATABASE (migration 221's triggers and CHECKs): this copy
lets the runner refuse by name first and lets the page say who must act
next. Nothing here pushes, merges, deploys, approves or places anything.
"""
from __future__ import annotations

import hashlib
import json
import re

VERSION = "IMPROVEMENT_PIPELINE_V1"
RUNNER_ACTOR = "IMPROVEMENT_PIPELINE"
CC = "https://command.bettortoken.com"

EVIDENCE = "EVIDENCE"
HYPOTHESIS = "HYPOTHESIS"
PEER_CHALLENGE = "PEER_CHALLENGE"
OWNER_RESPONSE = "OWNER_RESPONSE"
EXPERIMENT = "EXPERIMENT"
INDEPENDENT_EVALUATION = "INDEPENDENT_EVALUATION"
ELIGIBLE_CHANGE = "ELIGIBLE_CHANGE"
CONTROLLED_RELEASE = "CONTROLLED_RELEASE"
FORWARD_RESULT = "FORWARD_RESULT"
ROLLED_BACK = "ROLLED_BACK"
CLOSED = "CLOSED"
STAGES = (EVIDENCE, HYPOTHESIS, PEER_CHALLENGE, OWNER_RESPONSE, EXPERIMENT,
          INDEPENDENT_EVALUATION, ELIGIBLE_CHANGE, CONTROLLED_RELEASE,
          FORWARD_RESULT)
SEQ = {s: i + 1 for i, s in enumerate(STAGES)}
SEQ[ROLLED_BACK] = 98
SEQ[CLOSED] = 99
TERMINAL = (ROLLED_BACK, CLOSED)
REPEATABLE = (EVIDENCE, PEER_CHALLENGE, OWNER_RESPONSE, EXPERIMENT,
              INDEPENDENT_EVALUATION, FORWARD_RESULT)

RUNNER = "RUNNER"
OWNER_AGENT = "OWNER_AGENT"
PEER_AGENT = "PEER_AGENT"
CHALLENGER = "CHALLENGER"
INDEPENDENT_EVALUATOR = "INDEPENDENT_EVALUATOR"
HUMAN = "HUMAN"
ENGINEERING = "ENGINEERING"
ACTOR_CLASSES = (RUNNER, OWNER_AGENT, PEER_AGENT, CHALLENGER,
                 INDEPENDENT_EVALUATOR, HUMAN, ENGINEERING)
#: who may write each stage (the database's guard says the same)
PERMITTED = {
    EVIDENCE: (RUNNER, OWNER_AGENT),
    HYPOTHESIS: (OWNER_AGENT,),
    PEER_CHALLENGE: (CHALLENGER, PEER_AGENT),
    OWNER_RESPONSE: (OWNER_AGENT, HUMAN),
    EXPERIMENT: (OWNER_AGENT, ENGINEERING, HUMAN),
    INDEPENDENT_EVALUATION: (INDEPENDENT_EVALUATOR, HUMAN),
    ELIGIBLE_CHANGE: (INDEPENDENT_EVALUATOR, HUMAN),
    CONTROLLED_RELEASE: (HUMAN,),
    FORWARD_RESULT: (INDEPENDENT_EVALUATOR, HUMAN, ENGINEERING),
    ROLLED_BACK: (HUMAN, ENGINEERING),
    CLOSED: ACTOR_CLASSES,
}
#: the stages the runner itself may ever write (the database refuses it a
#: HUMAN / ENGINEERING row; this is the list it uses)
RUNNER_STAGES = (EVIDENCE, HYPOTHESIS, PEER_CHALLENGE, OWNER_RESPONSE,
                 EXPERIMENT, INDEPENDENT_EVALUATION, ELIGIBLE_CHANGE, CLOSED)

KAREN = "KAREN"
AGENTS = ("DEREK", "XAVIER", "AUDREY", "KAREN", "EDDIE", "SCOUT",
          "CHIEF_ALLOCATOR", "ADRIANA")
OWNER_AGENTS = ("DEREK", "XAVIER", "AUDREY", "EDDIE", "SCOUT",
                "CHIEF_ALLOCATOR", "ADRIANA")
EVALUATOR_AGENTS = ("DEREK", "XAVIER", "AUDREY")
#: the default independent evaluator per owner (karen.EVALUATOR_FOR,
#: extended to Eddie and Scout: Audrey evaluates both)
EVALUATOR_FOR = {"DEREK": "AUDREY", "XAVIER": "AUDREY", "AUDREY": "XAVIER",
                 "EDDIE": "AUDREY", "SCOUT": "AUDREY",
                 "CHIEF_ALLOCATOR": "AUDREY", "ADRIANA": "AUDREY"}
#: the default (non-Karen) peer challenger per owner
#: (collaboration_loop.PEER_ROUTING for Eddie / Scout: Derek first)
PEER_FOR = {"DEREK": "XAVIER", "XAVIER": "DEREK", "AUDREY": "XAVIER",
            "EDDIE": "DEREK", "SCOUT": "DEREK", "CHIEF_ALLOCATOR": "DEREK",
            "ADRIANA": "EDDIE"}

SOURCE_KINDS = ("KAREN_UPHELD_CHALLENGE", "AUDREY_FINDING",
                "COVERAGE_INCIDENT", "EDDIE_SKIP_EXECUTION", "FALSE_REFUSAL",
                "TOURNAMENT_VERDICT", "AGENT_FINDING", "HUMAN_REPORTED")

PROTECTED_AREAS = ("RISK", "ACCOUNTING", "SETTLEMENT",
                   "EXECUTION_AUTHORIZATION", "FINANCIAL_CONTROLS",
                   "CREDENTIALS", "LIVE_CAPITAL_LIMITS")
#: CONSERVATIVE KEYWORD CLASSIFICATION: a match escalates to a protected
#: area (over-classifying costs a human review; under-classifying would let
#: a protected change through one review). Matched on whole-word stems in
#: the item's own source fields (detector, target kind, kind, reason ...),
#: underscores read as spaces (ENTRY_WITHOUT_PROBABILITY is three words).
PROTECTED_RULES = (
    ("RISK", r"\brisk|\bthreshold|\bdrawdown|\bexposure|\bcap\b|\bmin edge|"
             r"\bmin net ev|\bfreshness"),
    ("ACCOUNTING", r"\bledger|\breconcil|\baccounting|\bpnl\b|\bp&l|"
                   r"\bbalance|\bcash\b|\bfee"),
    ("SETTLEMENT", r"\bsettle"),
    ("EXECUTION_AUTHORIZATION", r"\border|\bsubmi|\bexecution|\bintent|"
                                r"\badmission|\bentry decision|\bfill"),
    ("FINANCIAL_CONTROLS", r"\bcontrol|\bstop\b|\bkill|\bswitch|\bapproval|"
                           r"\bgate\b|\bentries"),
    ("CREDENTIALS", r"\bcredential|\btoken\b|\bapi[_ ]?key|\bsecret|"
                    r"\bsigning"),
    ("LIVE_CAPITAL_LIMITS", r"\bcapital|\bsmall ?live|\bsmalllive|\bfunded|"
                            r"\blive (order|book|position|lane|mirror)|"
                            r"\bactual\b|\blimit"),
)

# refusals (names; the database raises its own text)
R_TERMINAL = "THE_ITEM_IS_CLOSED_OR_ROLLED_BACK"
R_BACKWARDS = "STAGES_ONLY_MOVE_FORWARD"
R_SKIPPED = "NO_STAGE_IS_SKIPPED"
R_ONCE = "THAT_STAGE_IS_RECORDED_ONCE"
R_NOT_PERMITTED = "THAT_ACTOR_CLASS_MAY_NOT_WRITE_THIS_STAGE"
R_NOT_OWNER = "THE_OWNER_AGENT_WRITES_THIS"
R_SELF_REVIEW = "NO_PROPOSER_OR_AUTHOR_EVALUATES_OR_MARKS_ITS_OWN_CHANGE"
R_NEEDS_BOTH = "THE_OWNER_RESPONDS_AFTER_KAREN_AND_A_PEER_CHALLENGED"
R_NEEDS_PASS = "ELIGIBLE_CHANGE_NEEDS_ENOUGH_INDEPENDENT_PASSES"
R_HAS_FAIL = "A_REVIEWERS_LATEST_OUTCOME_IS_FAIL"
R_NEEDS_HUMAN = "A_PROTECTED_AREA_NEEDS_A_HUMAN"
R_NO_SOURCE = "AN_AGENT_STAGE_CITES_THE_RECORD_IT_WAS_TAKEN_FROM"
R_BAD_CLASS = "THE_ACTOR_IS_NOT_OF_THAT_CLASS"
R_RUNNER_HUMAN = "THE_RUNNER_NEVER_WRITES_A_HUMAN_OR_ENGINEERING_STEP"


def _u(v) -> str:
    return str(v or "").strip().upper()


def item_id_for(source_kind: str, source_key: str) -> str:
    raw = json.dumps([source_kind, source_key], sort_keys=True)
    return "impr:%s" % hashlib.sha256(raw.encode()).hexdigest()[:24]


def disagreement_id_for(item_id: str, stage: str, *keys) -> str:
    raw = json.dumps([item_id, stage, [str(k) for k in keys]])
    return "dis:%s" % hashlib.sha256(raw.encode()).hexdigest()[:24]


def classify_protected(*texts) -> tuple:
    """(areas, basis): every protected area whose rule matches any of the
    item's own source fields, with the matched term. Pure, deterministic."""
    hay = " ".join(str(t) for t in texts if t).lower().replace("_", " ")
    areas, basis = [], {}
    for area, rx in PROTECTED_RULES:
        m = re.search(rx, hay)
        if m:
            areas.append(area)
            basis[area] = m.group(0).strip()
    return areas, basis


def required_reviews(areas) -> int:
    return 2 if areas else 1


# ═════════════════════════════════════════════════════════════════════
# THE GUARD, PURE (mirrors improve_events_guard)
# ═════════════════════════════════════════════════════════════════════

def actor_class_ok(item: dict, actor: str, cls: str) -> bool:
    who, owner = _u(actor), _u(item.get("owner_agent"))
    if cls == OWNER_AGENT:
        return who == owner
    if cls == CHALLENGER:
        return who == KAREN
    if cls == PEER_AGENT:
        return who in AGENTS and who not in (KAREN, owner)
    if cls == INDEPENDENT_EVALUATOR:
        return who in EVALUATOR_AGENTS and who != owner
    if cls == RUNNER:
        return who == RUNNER_ACTOR
    if cls in (HUMAN, ENGINEERING):
        return bool(who) and not is_machine_actor(actor)
    return False


_MACHINE_EXACT = {"DEREK", "XAVIER", "AUDREY", "KAREN", "EDDIE", "SCOUT",
                  "ALLOCATOR", "CHIEF_ALLOCATOR", "CALIBRATION_ENGINE",
                  "MODEL_TOURNAMENT", "CLAUDE", "SYSTEM", "RUNNER",
                  "MIGRATION", "ROOT", "POSTGRES", "BOT", "AGENT", "CI",
                  "GITHUB", "GITHUB_ACTIONS", "RENDER", "NETLIFY", "DEPENDABOT",
                  "IMPROVEMENT_PIPELINE", "POS_LEARN_RUNNER", "POS_WORKFLOW",
                  "UNKNOWN", "ANONYMOUS", "SERVICE", "AUTOMATION", "CRON"}
_MACHINE_PREFIX = re.compile(
    r"^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE|CLAUDE|MIGRATION|POS_LEARN|POSLEARN"
    r"|RUNNER|INTEL|AUTOMATION|SERVICE|IMPROVEMENT|PIPELINE|GITHUB|CI|CRON"
    r"|WORKER|DEPLOY)([:/ ._-]|$)")
_MACHINE_NAMED = re.compile(
    r"(DEREK|XAVIER|AUDREY|KAREN|EDDIE|SCOUT|ALLOCATOR)[ ._:-]*(AGENT|BOT|V[0-9]"
    r"|CHALLENGER|RED[ ._-]*TEAM|EXECUTION|RESEARCH|INTEL)")


def is_machine_actor(v) -> bool:
    """The same predicate as improve_is_machine_actor (migration 221)."""
    s = str(v or "").strip()
    if not s:
        return True
    u = s.upper()
    return (u in _MACHINE_EXACT or bool(_MACHINE_PREFIX.search(u))
            or bool(_MACHINE_NAMED.search(u)) or "[bot]" in s.lower()
            or bool(re.search(r"(^|[^a-z])(bot|runner|daemon|scheduler)$",
                              s.lower())))


def latest_reviews(events: list) -> dict:
    """reviewer -> its LATEST independent-evaluation outcome."""
    out: dict = {}
    for e in sorted(events or [], key=lambda x: int(x.get("event_id") or 0)):
        if e.get("stage") == INDEPENDENT_EVALUATION:
            out[_u(e.get("actor"))] = e.get("outcome")
    return out


def check_event(item: dict, events: list, new: dict, *,
                runner: bool = False) -> str | None:
    """May `new` be appended to `item` whose events are `events`? None when
    it may, else the refusal name. The database enforces the same."""
    stage, cls, who = new.get("stage"), new.get("actor_class"), \
        _u(new.get("actor"))
    cur = item.get("stage")
    cur_seq = SEQ.get(cur, 0) if cur else 0
    if cur in TERMINAL:
        return R_TERMINAL
    if runner and cls in (HUMAN, ENGINEERING):
        return R_RUNNER_HUMAN
    if cls not in ACTOR_CLASSES or not actor_class_ok(item, who, cls):
        return R_BAD_CLASS
    if cls not in (HUMAN, ENGINEERING) and not (
            isinstance(new.get("source_ref"), dict)
            and new["source_ref"].get("kind") and new["source_ref"].get("id")):
        return R_NO_SOURCE
    if stage not in SEQ:
        return R_SKIPPED
    seq = SEQ[stage]
    if stage == CLOSED:
        if cur_seq == 0:
            return R_SKIPPED
    elif stage == ROLLED_BACK:
        if cur_seq not in (8, 9):
            return R_SKIPPED
    elif seq < cur_seq:
        return R_BACKWARDS
    elif seq == cur_seq:
        if stage not in REPEATABLE:
            return R_ONCE
    elif seq != cur_seq + 1:
        return R_SKIPPED
    if cls not in PERMITTED[stage]:
        return R_NOT_OWNER if stage == HYPOTHESIS else R_NOT_PERMITTED
    owner = _u(item.get("owner_agent"))
    if stage == OWNER_RESPONSE:
        chal = [e for e in events or [] if e.get("stage") == PEER_CHALLENGE]
        if not any(e.get("actor_class") == CHALLENGER for e in chal) or \
                not any(e.get("actor_class") == PEER_AGENT for e in chal):
            return R_NEEDS_BOTH
    if stage in (INDEPENDENT_EVALUATION, ELIGIBLE_CHANGE):
        authors = {_u(e.get("actor")) for e in events or []
                   if e.get("stage") in (HYPOTHESIS, EXPERIMENT)}
        if who == owner or who in authors:
            return R_SELF_REVIEW
    if stage == ELIGIBLE_CHANGE:
        rev = latest_reviews(events)
        if sum(1 for o in rev.values() if o == "PASS") < int(
                item.get("required_independent_reviews") or 1):
            return R_NEEDS_PASS
        if any(o == "FAIL" for o in rev.values()):
            return R_HAS_FAIL
        if item.get("requires_human_review") and cls != HUMAN:
            return R_NEEDS_HUMAN
    return None


# ═════════════════════════════════════════════════════════════════════
# WHO MUST ACT NEXT
# ═════════════════════════════════════════════════════════════════════

def _title(a) -> str:
    a = _u(a)
    return "CHIEF ALLOCATOR" if a == "CHIEF_ALLOCATOR" else a


def next_required(item: dict, events: list) -> dict:
    """{code, label, actor_class, actors}: the step the item waits for and
    who may take it -- derived only from the recorded events. Pure."""
    stage = item.get("stage")
    owner = _u(item.get("owner_agent"))
    protected = bool(item.get("requires_human_review"))
    need = int(item.get("required_independent_reviews") or 1)
    ev = list(events or [])

    def out(code, label, cls, actors):
        return {"code": code, "label": label, "actor_class": cls,
                "actors": list(actors)}

    if stage is None:
        return out("AWAITING_EVIDENCE", "AWAITING EVIDENCE", RUNNER,
                   [RUNNER_ACTOR])
    if stage == CLOSED:
        return out("NONE_CLOSED", "CLOSED · NO FURTHER STEP", None, [])
    if stage == ROLLED_BACK:
        return out("NONE_ROLLED_BACK", "ROLLED BACK · NO FURTHER STEP", None,
                   [])
    if stage == EVIDENCE:
        return out("AWAITING_HYPOTHESIS",
                   "AWAITING HYPOTHESIS (%s)" % _title(owner), OWNER_AGENT,
                   [owner])
    if stage in (HYPOTHESIS, PEER_CHALLENGE):
        chal = [e for e in ev if e.get("stage") == PEER_CHALLENGE]
        has_k = any(e.get("actor_class") == CHALLENGER for e in chal)
        has_p = any(e.get("actor_class") == PEER_AGENT for e in chal)
        peer = PEER_FOR.get(owner, "XAVIER")
        if not has_k and not has_p:
            return out("AWAITING_PEER_CHALLENGE",
                       "AWAITING PEER CHALLENGE (KAREN + %s)" % _title(peer),
                       "CHALLENGER+PEER_AGENT", [KAREN, peer])
        if not has_k:
            return out("AWAITING_PEER_CHALLENGE",
                       "AWAITING PEER CHALLENGE (KAREN)", CHALLENGER, [KAREN])
        if not has_p:
            return out("AWAITING_PEER_CHALLENGE",
                       "AWAITING PEER CHALLENGE (%s)" % _title(peer),
                       PEER_AGENT, [peer])
        return out("AWAITING_OWNER_RESPONSE",
                   "AWAITING OWNER RESPONSE (%s)" % _title(owner),
                   OWNER_AGENT, [owner])
    if stage == OWNER_RESPONSE:
        return out("AWAITING_EXPERIMENT",
                   "AWAITING EXPERIMENT / CANDIDATE PATCH (%s OR ENGINEERING)"
                   % _title(owner), "OWNER_AGENT|ENGINEERING",
                   [owner, ENGINEERING])
    evaluator = EVALUATOR_FOR.get(owner, "AUDREY")
    if stage in (EXPERIMENT, INDEPENDENT_EVALUATION):
        rev = latest_reviews(ev)
        passes = sum(1 for o in rev.values() if o == "PASS")
        if any(o == "FAIL" for o in rev.values()):
            return out("EVALUATION_FAILED",
                       "EVALUATION FAILED · AWAITING CLOSE OR A NEW CANDIDATE",
                       OWNER_AGENT, [owner])
        if passes < need:
            if protected and passes >= 1:
                return out("AWAITING_SECOND_REVIEW",
                           "AWAITING SECOND INDEPENDENT REVIEW (%d OF %d)"
                           % (passes, need), "INDEPENDENT_EVALUATOR|HUMAN",
                           ["HUMAN REVIEWER"])
            who = ("%s + HUMAN REVIEWER" % _title(evaluator)) if protected \
                else _title(evaluator)
            return out("AWAITING_INDEPENDENT_EVALUATION",
                       "AWAITING INDEPENDENT EVALUATION (%s)" % who,
                       INDEPENDENT_EVALUATOR, [evaluator])
        if protected:
            return out("AWAITING_HUMAN_REVIEW",
                       "AWAITING HUMAN REVIEW (PROTECTED AREA)", HUMAN,
                       ["HUMAN REVIEWER"])
        return out("AWAITING_ELIGIBILITY",
                   "AWAITING ELIGIBILITY MARK (%s)" % _title(evaluator),
                   INDEPENDENT_EVALUATOR, [evaluator])
    if stage == ELIGIBLE_CHANGE:
        return out("AWAITING_HUMAN_APPROVAL",
                   "AWAITING HUMAN APPROVAL (CONTROLLED RELEASE · EXACT-SHA "
                   "GATE RECEIPT)", HUMAN, ["HUMAN APPROVER"])
    if stage == CONTROLLED_RELEASE:
        return out("AWAITING_FORWARD_RESULT",
                   "AWAITING FORWARD RESULT (MONITORING WINDOW · %s)"
                   % _title(evaluator), "INDEPENDENT_EVALUATOR|HUMAN",
                   [evaluator, "HUMAN"])
    if stage == FORWARD_RESULT:
        last = [e for e in ev if e.get("stage") == FORWARD_RESULT]
        if last and last[-1].get("outcome") == "DEGRADED":
            return out("AWAITING_ROLLBACK_DECISION",
                       "AWAITING ROLLBACK DECISION (HUMAN)", HUMAN, ["HUMAN"])
        if last and last[-1].get("outcome") == "HELD":
            return out("COMPLETE_MONITORING",
                       "FORWARD RESULT HELD · MONITORING", None, [])
        return out("AWAITING_FORWARD_RESULT",
                   "AWAITING CONCLUSIVE FORWARD RESULT", HUMAN, ["HUMAN"])
    return out("UNKNOWN", "UNKNOWN STAGE", None, [])


def disagreement_summary(rows: list) -> dict:
    """Counts by state; OPEN is a live disagreement, DECIDED a recorded
    dissent overruled by a third party (never called agreement)."""
    s = {"open": 0, "decided": 0, "conceded": 0, "withdrawn": 0,
         "total": 0}
    for r in rows or []:
        st = str(r.get("state") or "").lower()
        if st in s:
            s[st] += 1
        s["total"] += 1
    s["dissent_preserved"] = s["decided"]
    return s


# ═════════════════════════════════════════════════════════════════════
# EVIDENCE LINKS
# ═════════════════════════════════════════════════════════════════════

LINKS = {
    "karen_challenges": ("/karen", "/api/command/karen/challenges/%s"),
    "agent_findings": ("/audrey", "/api/command/agents/findings/%s"),
    "eddie_execution_estimates": ("/eddie",
                                  "/api/command/eddie/estimates/%s"),
    "paper_audrey_findings": ("/audrey", None),
    "audrey_audit_reports": ("/audrey", None),
    "coverage_collapse_alerts": ("/audrey", "/api/command/coverage"),
    "scout_feature_tournaments": ("/scout", None),
    "scout_features": ("/scout", None),
    "poslearn_registrations": ("/floor", None),
    "poslearn_promotion_steps": ("/floor", None),
    "poslearn_experiments": ("/floor", None),
    "lol_ledger": ("/", None),
    "improvement_deficits": ("/audrey", None),
    "agent_finding_stages": ("/audrey", None),
    "derek_entry_decisions": ("/derek", None),
    "paper_decisions": ("/derek", None),
    "execution_intents": ("/derek", None),
}


def link(ref: dict) -> dict:
    kind, rid = str(ref.get("kind") or ""), str(ref.get("id") or "")
    page, api = LINKS.get(kind, (None, None))
    return {"kind": kind, "id": rid, "page": page,
            "api": (api % rid if api and "%s" in api else api)}


def item_href(item_id: str) -> str:
    return "%s/improvements?item=%s" % (CC, item_id)


# ═════════════════════════════════════════════════════════════════════
# SLACK: ONE EVIDENCE-LINKED LINE PER STAGE TRANSITION
# ═════════════════════════════════════════════════════════════════════

#: who speaks a transition: the agent that recorded it (its own bot);
#: a runner / human / engineering row is reported by Audrey, the auditor,
#: naming who recorded it.
def slack_agent(event: dict) -> str:
    who = _u(event.get("actor"))
    if event.get("actor_class") in (OWNER_AGENT, PEER_AGENT, CHALLENGER,
                                    INDEPENDENT_EVALUATOR) and who in (
            "DEREK", "XAVIER", "AUDREY", "KAREN", "EDDIE", "SCOUT",
            "ADRIANA"):
        return who.lower()
    return "audrey"


def _first_text(body: dict) -> str:
    for k in ("hypothesis", "challenge", "response", "reason", "statement",
              "design", "note"):
        v = (body or {}).get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def slack_post(item: dict, event: dict, nxt: dict) -> str:
    """The workroom text of one transition. Ids and links only from the
    records; no language model. Pure."""
    body = event.get("body") or {}
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = {}
    refs = list(event.get("evidence_refs") or [])
    if isinstance(event.get("source_ref"), dict):
        refs = [event["source_ref"]] + refs
    if not refs:
        refs = list(item.get("evidence_refs") or [])
    ref_txt = ", ".join("%s %s" % (r.get("kind"), r.get("id"))
                        for r in refs[:5]) or "none"
    lines = ["Improvement %s · %s -> %s · owner %s%s" % (
        item.get("item_id"), event.get("from_stage") or "NEW",
        event.get("stage"), _title(item.get("owner_agent")),
        " · PROTECTED: %s" % ", ".join(item.get("protected_areas") or [])
        if item.get("protected_areas") else "")]
    lines.append("%s" % str(item.get("title") or "")[:300])
    detail = []
    if event.get("stance"):
        detail.append("stance %s" % event["stance"])
    if event.get("outcome"):
        detail.append("outcome %s" % event["outcome"])
    if event.get("patch_commit_sha"):
        detail.append("patch %s" % event["patch_commit_sha"])
    if event.get("gate_receipt_sha"):
        detail.append("gate receipt %s" % event["gate_receipt_sha"])
    lines.append("Recorded by %s (%s)%s." % (
        event.get("actor"), event.get("actor_class"),
        "; " + "; ".join(detail) if detail else ""))
    txt = _first_text(body)
    if txt:
        lines.append('"%s"' % txt[:900])
    lines.append("Evidence: %s" % ref_txt)
    lines.append("Next: %s" % (nxt or {}).get("label", "unknown"))
    lines.append("Trail: %s" % item_href(str(item.get("item_id"))))
    lines.append("Stage record only: nothing is approved, merged, deployed "
                 "or activated by this message.")
    return "\n".join(lines)
