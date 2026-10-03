"""THE AGENTS' COLLABORATION LOOP: EVIDENCE -> RELEASE ELIGIBILITY, PEER-CHECKED.

An ordinary finding by Derek, Xavier or Audrey moves through explicit,
persisted stages (migration 203: `agent_findings` + the append-only
`agent_finding_stages`), in this order and no other:

  1 EVIDENCE                the proposer's grounded evidence references
  2 HYPOTHESIS              the proposer's hypothesis, grounded
  3 PEER_CHALLENGE          recorded by a DIFFERENT agent, grounded;
                            SUSTAINED goes on, REFUTED can only close
  4 BOUNDED_EXPERIMENT      PAPER_ONLY, a pre-registered metric (name,
                            direction, threshold) and a stopping rule
                            (max_duration_s and/or max_samples)
  5 CANDIDATE_IMPROVEMENT   the candidate change (optionally linking the
                            existing paper_improvement_proposals row)
  6 INDEPENDENT_EVALUATION  by an agent that is neither the proposer nor the
                            candidate's author, on data starting at/after the
                            evidence window AND the experiment's registration,
                            citing none of the hypothesis's evidence, inside
                            the stopping rule; a PASS meets the registered
                            metric
  7 RELEASE_ELIGIBILITY     only after a PASS, by a non-proposer:
                            ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW -- a mark for a
                            person, never a change
 99 CLOSED                  from any stage, with a reason

WHAT IT REUSES. The evidence is the agents' existing records (lessons,
decisions, reviews, audit findings, tasks, ...), referenced by {kind, id} and
checked to exist; a finding can open from a `paper_agent_lessons` row
(`open_from_lesson`); a candidate can name its `paper_improvement_proposals`
row (read, never written).

KAREN (migration 207, the red-team agent) takes part ONLY as a challenger:
she may record the PEER_CHALLENGE stage of another agent's finding and no
other stage -- she proposes nothing, evaluates nothing and marks nothing
release-eligible. The database's actor CHECK says the same.

WHAT IT CANNOT DO, BY CONSTRUCTION. Activate a policy, approve anything, or
change a risk limit, capital, credential or submission switch: this module
writes only the two loop tables, refuses any such key in what it records
(`authority_keys`, recursively), and the tables CHECK production_effect =
'NONE'. It sends no message. Every guard here is ALSO enforced by the
database (trigger + CHECKs), so a caller that skips this module is refused
there.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

AGENTS = ("DEREK", "XAVIER", "AUDREY")
#: Agents that may act in the loop ONLY by recording a PEER_CHALLENGE.
CHALLENGERS_ONLY = ("KAREN",)

EVIDENCE = "EVIDENCE"
HYPOTHESIS = "HYPOTHESIS"
PEER_CHALLENGE = "PEER_CHALLENGE"
BOUNDED_EXPERIMENT = "BOUNDED_EXPERIMENT"
CANDIDATE_IMPROVEMENT = "CANDIDATE_IMPROVEMENT"
INDEPENDENT_EVALUATION = "INDEPENDENT_EVALUATION"
RELEASE_ELIGIBILITY = "RELEASE_ELIGIBILITY"
CLOSED = "CLOSED"
STAGES = (EVIDENCE, HYPOTHESIS, PEER_CHALLENGE, BOUNDED_EXPERIMENT,
          CANDIDATE_IMPROVEMENT, INDEPENDENT_EVALUATION, RELEASE_ELIGIBILITY)
SEQ = {s: i + 1 for i, s in enumerate(STAGES)}
SEQ[CLOSED] = 99

ELIGIBLE = "ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW"
CHALLENGE_OUTCOMES = ("SUSTAINED", "REFUTED")
EVALUATION_OUTCOMES = ("PASS", "FAIL", "INCONCLUSIVE")
SCOPE = "PAPER_ONLY"
MAX_DURATION_S = 90 * 86400
MAX_SAMPLES = 100000

#: Evidence a finding may cite: kind -> (table, key column). Fixed
#: identifiers, never supplied by a caller.
EVIDENCE_KINDS = {
    "paper_agent_lessons": ("paper_agent_lessons", "lesson_id"),
    "agent_decisions": ("agent_decisions", "decision_ref"),
    "agent_tasks": ("agent_tasks", "task_id"),
    "agent_runs": ("agent_runs", "run_id"),
    "paper_xavier_reviews": ("paper_xavier_reviews", "review_id"),
    "smalllive_reviews": ("smalllive_reviews", "review_id"),
    "paper_decisions": ("paper_decisions", "decision_id"),
    "paper_orders": ("paper_orders", "order_id"),
    "paper_settlements": ("paper_settlements", "settlement_id"),
    "paper_audrey_findings": ("paper_audrey_findings", "finding_id"),
    "paper_recommendations": ("paper_recommendations", "recommendation_id"),
    "paper_improvement_proposals": ("paper_improvement_proposals",
                                    "proposal_id"),
    "external_valuations": ("external_valuations", "id"),
    # (207) the records Karen challenges, and her challenges themselves
    "execution_intents": ("execution_intents", "intent_id"),
    "derek_entry_decisions": ("derek_entry_decisions", "decision_id"),
    "bettor_xavier_reviews": ("bettor_xavier_reviews", "review_id"),
    "smalllive_reconciliations": ("smalllive_reconciliations", "group_id"),
    "kalshi_account_reconciliations": ("kalshi_account_reconciliations",
                                       "reconciliation_id"),
    "bettor_account_reconciliation_reports": (
        "bettor_account_reconciliation_reports", "report_id"),
    "audrey_audit_reports": ("audrey_audit_reports", "report_id"),
    "agent_findings": ("agent_findings", "finding_id"),
    "karen_challenges": ("karen_challenges", "challenge_id"),
}


async def key_column(conn, table: str, col):
    """The fixed key expression of an evidence kind, or -- for a kind whose
    key is None -- the table's single-column primary key from the catalogue
    (None when the table has no such key). Never a caller-supplied name."""
    if col is not None:
        return col
    rows = await conn.fetch(
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON "
        " a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
        " WHERE i.indrelid=to_regclass($1) AND i.indisprimary", table)
    return '"%s"' % rows[0]["attname"].replace('"', '') if len(rows) == 1 \
        else None

#: Keys no stage may carry, at any depth: the loop is not an approval or
#: activation path and touches no limit or authority.
AUTHORITY_KEYS = frozenset((
    "risk_limits", "limits", "capital_usd", "max_downside_usd",
    "max_incremental_capital_usd", "per_order_usd", "max_order_usd",
    "daily_loss_stop_usd", "credentials", "api_key", "account_authority",
    "submission_enabled", "approved", "approved_by", "approval", "activate",
    "activation", "active", "policy_activation"))

R_UNKNOWN_AGENT = "THAT_IS_NOT_ONE_OF_THE_THREE_AGENTS"
R_CHALLENGER_ONLY = "KAREN_RECORDS_ONLY_THE_PEER_CHALLENGE_STAGE"
R_UNGROUNDED = "A_FINDING_NEEDS_AT_LEAST_ONE_EVIDENCE_REFERENCE"
R_BAD_REF = "AN_EVIDENCE_REFERENCE_NEEDS_A_KIND_AND_AN_ID"
R_UNKNOWN_KIND = "THAT_EVIDENCE_KIND_IS_NOT_A_RECORD_THE_AGENTS_KEEP"
R_REF_NOT_FOUND = "THE_REFERENCED_EVIDENCE_RECORD_DOES_NOT_EXIST"
R_NO_SUCH_FINDING = "NO_SUCH_FINDING"
R_CLOSED = "THE_FINDING_IS_CLOSED"
R_SKIPPED = "NO_STAGE_CAN_BE_SKIPPED"
R_PROPOSER_ONLY = "EVIDENCE_AND_HYPOTHESIS_ARE_THE_PROPOSERS"
R_SELF_CHALLENGE = "NO_AGENT_CAN_CHALLENGE_ITS_OWN_FINDING"
R_SELF_EVALUATION = "NO_AGENT_CAN_EVALUATE_ITS_OWN_FINDING_OR_CANDIDATE"
R_SELF_RELEASE = "THE_PROPOSER_CANNOT_MARK_ITS_OWN_FINDING_ELIGIBLE"
R_REFUTED = "A_REFUTED_HYPOTHESIS_CAN_ONLY_BE_CLOSED"
R_NOT_PASSED = "ONLY_A_PASSED_INDEPENDENT_EVALUATION_IS_RELEASE_ELIGIBLE"
R_NOT_PAPER = "AN_EXPERIMENT_IS_PAPER_ONLY"
R_NO_METRIC = "AN_EXPERIMENT_PRE_REGISTERS_A_METRIC_NAME_DIRECTION_THRESHOLD"
R_NO_STOP = "AN_EXPERIMENT_PRE_REGISTERS_A_BOUNDED_STOPPING_RULE"
R_REUSED_DATA = "THE_EVALUATION_MUST_USE_DATA_NOT_USED_TO_FORM_THE_HYPOTHESIS"
R_BEFORE_REGISTRATION = "THE_EVALUATION_DATA_MUST_START_AFTER_PRE_REGISTRATION"
R_OUTSIDE_STOP = "THE_EVALUATION_EXCEEDS_THE_PRE_REGISTERED_STOPPING_RULE"
R_PASS_MISSES_METRIC = "A_PASS_MUST_MEET_THE_PRE_REGISTERED_METRIC"
R_BAD_WINDOW = "THE_EVALUATION_WINDOW_IS_NOT_A_PAST_INTERVAL"
R_BAD_OUTCOME = "THAT_OUTCOME_IS_NOT_PERMITTED_AT_THIS_STAGE"
R_AUTHORITY = "THE_LOOP_CANNOT_CARRY_A_LIMIT_APPROVAL_OR_ACTIVATION"
R_TEXT = "A_NON_EMPTY_STATEMENT_IS_REQUIRED"
R_TIME = "A_STAGE_CANNOT_PREDATE_THE_STAGE_BEFORE_IT"
R_PROPOSAL = "THE_LINKED_PROPOSAL_MUST_EXIST_PAPER_ONLY_AND_INACTIVE"
R_NO_SCHEMA = "MIGRATION_203_IS_NOT_APPLIED"
R_DB_REFUSED = "THE_DATABASE_REFUSED_THE_STAGE"


def _ok(**kw) -> dict:
    return dict(ok=True, refusal=None, **kw)


def _no(refusal: str, **kw) -> dict:
    return dict(ok=False, refusal=refusal, **kw)


def _dt(epoch) -> datetime:
    return datetime.fromtimestamp(float(epoch), timezone.utc)


def _ep(v):
    return v.timestamp() if isinstance(v, datetime) else v


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(float(v)) else None


def _text(v, n=4000) -> bool:
    return isinstance(v, str) and 0 < len(v.strip()) <= n


def authority_keys(obj, _path="") -> list:
    """Every key, at any depth, that would make a stage carry a limit,
    approval or activation. Pure."""
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


def normalise_refs(refs, *, required: bool = True) -> dict:
    """[{kind, id}] checked for shape and kind. Pure."""
    if refs is None:
        refs = []
    if not isinstance(refs, (list, tuple)):
        return _no(R_BAD_REF)
    if required and not refs:
        return _no(R_UNGROUNDED)
    out = []
    for r in refs:
        if not isinstance(r, dict):
            return _no(R_BAD_REF)
        kind, rid = r.get("kind"), r.get("id")
        if not _text(kind, 100) or rid is None or not str(rid).strip():
            return _no(R_BAD_REF, ref=r)
        if kind not in EVIDENCE_KINDS:
            return _no(R_UNKNOWN_KIND, kind=kind)
        out.append({"kind": kind, "id": str(rid).strip()})
    if len(out) > 50:
        return _no(R_BAD_REF, why="at most 50 references")
    return _ok(refs=out)


async def verify_refs_exist(conn, refs: list) -> dict:
    """Each reference names a record that exists (fixed table/column per
    kind). A failed read refuses; nothing is assumed."""
    for r in refs:
        table, col = EVIDENCE_KINDS[r["kind"]]
        try:
            if await conn.fetchval("SELECT to_regclass($1)", table) is None:
                return _no(R_REF_NOT_FOUND, ref=r, why="table absent")
            col = await key_column(conn, table, col)
            if col is None:
                return _no(R_REF_NOT_FOUND, ref=r, why="no single key")
            hit = await conn.fetchval(
                "SELECT 1 FROM %s WHERE %s::text = $1 LIMIT 1" % (table, col),
                r["id"])
        except Exception as exc:                                # noqa: BLE001
            return _no(R_REF_NOT_FOUND, ref=r, error=type(exc).__name__)
        if not hit:
            return _no(R_REF_NOT_FOUND, ref=r)
    return _ok()


def _keyset(refs) -> set:
    return {(r.get("kind"), str(r.get("id"))) for r in refs or []}


def check_metric(metric) -> str | None:
    m = metric if isinstance(metric, dict) else None
    if (m is None or not _text(m.get("name"), 200)
            or m.get("direction") not in ("INCREASE", "DECREASE")
            or _num(m.get("threshold")) is None):
        return R_NO_METRIC
    return None


def check_stopping_rule(rule) -> str | None:
    s = rule if isinstance(rule, dict) else None
    if s is None or not ({"max_duration_s", "max_samples"} & set(s)):
        return R_NO_STOP
    if "max_duration_s" in s:
        d = _num(s["max_duration_s"])
        if d is None or not 1 <= d <= MAX_DURATION_S:
            return R_NO_STOP
    if "max_samples" in s:
        n = _num(s["max_samples"])
        if n is None or not 1 <= n <= MAX_SAMPLES:
            return R_NO_STOP
    return None


def check_advance(finding: dict, stages: list, new: dict) -> str | None:
    """THE GUARDS, PURE: may `new` (a stage dict) follow `stages` of
    `finding`? None when it may, else the refusal. The database enforces
    the same rules."""
    f = dict(finding or {})
    by_seq = {int(s["seq"]): s for s in stages or []}
    stage, actor = new.get("stage"), new.get("actor")
    if actor in CHALLENGERS_ONLY:
        if stage != PEER_CHALLENGE:
            return R_CHALLENGER_ONLY
    elif actor not in AGENTS:
        return R_UNKNOWN_AGENT
    if f.get("stage") == CLOSED:
        return R_CLOSED
    body = new.get("body") or {}
    if authority_keys(body):
        return R_AUTHORITY
    if stage == CLOSED:
        if not by_seq:
            return R_SKIPPED
        return None if _text(body.get("reason"), 2000) else R_TEXT
    if stage not in SEQ:
        return R_SKIPPED
    cur = max(by_seq) if by_seq else 0
    if SEQ[stage] != cur + 1:
        return R_SKIPPED
    if not normalise_refs(f.get("evidence_refs")).get("ok"):
        return R_UNGROUNDED
    prev = by_seq.get(cur) or {}
    at = _num(new.get("at"))
    if at is None or (prev and at < float(_ep(prev["at"]))) or \
            at < float(_ep(f.get("created_at")) or 0):
        return R_TIME
    proposer = f.get("proposer")
    refs = new.get("evidence_refs") or []
    if stage in (EVIDENCE, HYPOTHESIS, PEER_CHALLENGE,
                 INDEPENDENT_EVALUATION):
        if not normalise_refs(refs).get("ok"):
            return R_UNGROUNDED
    if stage in (EVIDENCE, HYPOTHESIS):
        if actor != proposer:
            return R_PROPOSER_ONLY
        if stage == HYPOTHESIS and not _text(body.get("hypothesis")):
            return R_TEXT
        return None
    if stage == PEER_CHALLENGE:
        if actor == proposer:
            return R_SELF_CHALLENGE
        if new.get("outcome") not in CHALLENGE_OUTCOMES:
            return R_BAD_OUTCOME
        return None if _text(body.get("challenge")) else R_TEXT
    if stage == BOUNDED_EXPERIMENT:
        if prev.get("outcome") != "SUSTAINED":
            return R_REFUTED
        if new.get("scope") != SCOPE:
            return R_NOT_PAPER
        return check_metric(new.get("metric")) or check_stopping_rule(
            new.get("stopping_rule"))
    if stage == CANDIDATE_IMPROVEMENT:
        if not isinstance(body.get("change"), dict) or \
                not _text(body.get("description")):
            return R_TEXT
        return None
    if stage == INDEPENDENT_EVALUATION:
        cand, exp = by_seq.get(5) or {}, by_seq.get(4) or {}
        hyp = by_seq.get(2) or {}
        if actor in (proposer, cand.get("actor")):
            return R_SELF_EVALUATION
        if new.get("outcome") not in EVALUATION_OUTCOMES:
            return R_BAD_OUTCOME
        ds, de = _num(new.get("data_start")), _num(new.get("data_end"))
        if ds is None or de is None or not ds < de <= at:
            return R_BAD_WINDOW
        if ds < float(_ep(f.get("evidence_window_end"))):
            return R_REUSED_DATA
        if ds < float(_ep(exp.get("at"))):
            return R_BEFORE_REGISTRATION
        if _keyset(refs) & (_keyset(f.get("evidence_refs"))
                            | _keyset(hyp.get("evidence_refs"))):
            return R_REUSED_DATA
        rule = _obj(exp.get("stopping_rule"))
        if "max_duration_s" in rule and de - ds > float(
                rule["max_duration_s"]):
            return R_OUTSIDE_STOP
        if "max_samples" in rule:
            n = _num(body.get("samples"))
            if n is None or n > float(rule["max_samples"]):
                return R_OUTSIDE_STOP
        if new.get("outcome") == "PASS":
            m = _obj(exp.get("metric"))
            v = _num(body.get("metric_value"))
            t = float(m.get("threshold"))
            if v is None or (m.get("direction") == "INCREASE" and v < t) or \
                    (m.get("direction") == "DECREASE" and v > t):
                return R_PASS_MISSES_METRIC
        return None
    if stage == RELEASE_ELIGIBILITY:
        if prev.get("outcome") != "PASS":
            return R_NOT_PASSED
        if actor == proposer:
            return R_SELF_RELEASE
        return None
    return R_SKIPPED


def _obj(v) -> dict:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return dict(v) if isinstance(v, dict) else {}


def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if k in ("evidence_refs", "body", "metric", "stopping_rule"):
            v = _obj(v) if k != "evidence_refs" else (
                json.loads(v) if isinstance(v, str) else list(v or []))
        out[k] = _ep(v)
    return out


async def _schema(conn) -> bool:
    try:
        return await conn.fetchval(
            "SELECT to_regclass('agent_finding_stages') IS NOT NULL") is True
    except Exception:                                           # noqa: BLE001
        return False


async def finding(conn, finding_id: str) -> dict | None:
    """One finding with every stage, in order (read only)."""
    f = await conn.fetchrow("SELECT * FROM agent_findings WHERE finding_id=$1",
                            str(finding_id))
    if f is None:
        return None
    st = await conn.fetch(
        "SELECT * FROM agent_finding_stages WHERE finding_id=$1 ORDER BY seq",
        str(finding_id))
    return {"finding": _row(f), "stages": [_row(s) for s in st],
            "production_effect": "NONE"}


async def findings(conn, *, stage: str | None = None, limit: int = 50
                   ) -> list:
    rows = await conn.fetch(
        "SELECT * FROM agent_findings WHERE ($1::text IS NULL OR stage=$1) "
        " ORDER BY updated_at DESC, finding_id LIMIT $2", stage,
        max(1, min(int(limit or 50), 500)))
    return [_row(r) for r in rows]


def finding_id_for(proposer: str, refs: list, title: str) -> str:
    raw = json.dumps([proposer, sorted(_keyset(refs)), title],
                     sort_keys=True, default=str)
    return "afnd:%s" % hashlib.sha256(raw.encode()).hexdigest()[:24]


async def open_finding(conn, *, proposer: str, title: str, statement: str,
                       evidence_refs: list, evidence_window_end: float,
                       at: float, finding_id: str | None = None,
                       source_lesson_id: str | None = None) -> dict:
    """OPEN A GROUNDED FINDING AND RECORD ITS EVIDENCE STAGE, in one
    transaction. Idempotent on the finding id (a replay writes nothing).
    Never raises."""
    if proposer not in AGENTS:
        return _no(R_UNKNOWN_AGENT)
    if not _text(title, 300) or not _text(statement):
        return _no(R_TEXT)
    n = normalise_refs(evidence_refs)
    if not n["ok"]:
        return n
    refs = n["refs"]
    if _num(evidence_window_end) is None or _num(at) is None or \
            float(evidence_window_end) > float(at):
        return _no(R_BAD_WINDOW)
    if not await _schema(conn):
        return _no(R_NO_SCHEMA)
    fid = finding_id or finding_id_for(proposer, refs, title)
    try:
        async with conn.transaction():
            have = await conn.fetchrow(
                "SELECT finding_id FROM agent_findings WHERE finding_id=$1",
                fid)
            if have is not None:
                return _ok(finding_id=fid, created=False)
            v = await verify_refs_exist(conn, refs)
            if not v["ok"]:
                return v
            await conn.execute(
                "INSERT INTO agent_findings (finding_id, proposer, title, "
                " statement, evidence_refs, evidence_window_end, "
                " source_lesson_id, created_at, updated_at) VALUES "
                " ($1,$2,$3,$4,$5::jsonb,to_timestamp($6),$7,"
                "  to_timestamp($8),to_timestamp($8))",
                fid, proposer, title.strip(), statement.strip(),
                json.dumps(refs), float(evidence_window_end),
                source_lesson_id, float(at))
            await _insert_stage(conn, fid, {
                "stage": EVIDENCE, "actor": proposer, "at": float(at),
                "body": {"statement": statement.strip()},
                "evidence_refs": refs})
    except Exception as exc:                                    # noqa: BLE001
        return _no(R_DB_REFUSED, error="%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200]))
    return _ok(finding_id=fid, created=True, stage=EVIDENCE)


async def open_from_lesson(conn, lesson_id: str, *, at: float,
                           title: str | None = None) -> dict:
    """A finding from an existing `paper_agent_lessons` row: the lesson's
    agent is the proposer, the lesson is the evidence, its window end is
    the evidence window."""
    try:
        r = await conn.fetchrow(
            "SELECT lesson_id, agent_id, statement, window_end "
            "  FROM paper_agent_lessons WHERE lesson_id=$1", str(lesson_id))
    except Exception as exc:                                    # noqa: BLE001
        return _no(R_REF_NOT_FOUND, error=type(exc).__name__)
    if r is None:
        return _no(R_REF_NOT_FOUND, ref={"kind": "paper_agent_lessons",
                                         "id": lesson_id})
    return await open_finding(
        conn, proposer=r["agent_id"], title=(title or r["statement"])[:300],
        statement=r["statement"],
        evidence_refs=[{"kind": "paper_agent_lessons", "id": r["lesson_id"]}],
        evidence_window_end=_ep(r["window_end"]), at=at,
        source_lesson_id=r["lesson_id"])


async def _insert_stage(conn, fid: str, s: dict) -> None:
    await conn.execute(
        "INSERT INTO agent_finding_stages (finding_id, seq, stage, actor, at,"
        " body, evidence_refs, outcome, scope, metric, stopping_rule, "
        " data_start, data_end, proposal_id) VALUES ($1,$2,$3,$4,"
        " to_timestamp($5),$6::jsonb,$7::jsonb,$8,$9,$10::jsonb,$11::jsonb,"
        " CASE WHEN $12::float8 IS NULL THEN NULL ELSE to_timestamp($12) END,"
        " CASE WHEN $13::float8 IS NULL THEN NULL ELSE to_timestamp($13) END,"
        " $14)",
        fid, SEQ[s["stage"]], s["stage"], s["actor"], float(s["at"]),
        json.dumps(s.get("body") or {}, default=str),
        json.dumps(s.get("evidence_refs") or [], default=str),
        s.get("outcome"), s.get("scope"),
        None if s.get("metric") is None else json.dumps(s["metric"]),
        None if s.get("stopping_rule") is None
        else json.dumps(s["stopping_rule"]),
        _num(s.get("data_start")), _num(s.get("data_end")),
        s.get("proposal_id"))


async def _advance(conn, finding_id: str, new: dict) -> dict:
    """Check, then append, one stage -- under the finding's row lock."""
    if not await _schema(conn):
        return _no(R_NO_SCHEMA)
    if new.get("evidence_refs"):
        n = normalise_refs(new["evidence_refs"])
        if not n["ok"]:
            return n
        new = dict(new, evidence_refs=n["refs"])
    try:
        async with conn.transaction():
            f = await conn.fetchrow(
                "SELECT * FROM agent_findings WHERE finding_id=$1 FOR UPDATE",
                str(finding_id))
            if f is None:
                return _no(R_NO_SUCH_FINDING, finding_id=finding_id)
            st = await conn.fetch(
                "SELECT * FROM agent_finding_stages WHERE finding_id=$1 "
                " ORDER BY seq", str(finding_id))
            why = check_advance(_row(f), [_row(s) for s in st], new)
            if why:
                return _no(why, finding_id=finding_id, stage=new.get("stage"))
            if new.get("evidence_refs"):
                v = await verify_refs_exist(conn, new["evidence_refs"])
                if not v["ok"]:
                    return v
            if new.get("proposal_id"):
                p = await conn.fetchrow(
                    "SELECT scope, active FROM paper_improvement_proposals "
                    " WHERE proposal_id=$1", new["proposal_id"])
                if p is None or p["scope"] != "PAPER_ONLY" or p["active"]:
                    return _no(R_PROPOSAL, proposal_id=new["proposal_id"])
            await _insert_stage(conn, str(finding_id), new)
    except Exception as exc:                                    # noqa: BLE001
        return _no(R_DB_REFUSED, error="%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200]))
    return _ok(finding_id=finding_id, stage=new["stage"],
               outcome=new.get("outcome"), production_effect="NONE")


async def record_hypothesis(conn, finding_id, *, actor, hypothesis,
                            evidence_refs, at) -> dict:
    return await _advance(conn, finding_id, {
        "stage": HYPOTHESIS, "actor": actor, "at": at,
        "body": {"hypothesis": hypothesis}, "evidence_refs": evidence_refs})


async def record_challenge(conn, finding_id, *, actor, challenge, outcome,
                           evidence_refs, at) -> dict:
    return await _advance(conn, finding_id, {
        "stage": PEER_CHALLENGE, "actor": actor, "at": at,
        "outcome": outcome, "body": {"challenge": challenge},
        "evidence_refs": evidence_refs})


async def register_experiment(conn, finding_id, *, actor, design, metric,
                              stopping_rule, at,
                              scope: str = SCOPE) -> dict:
    return await _advance(conn, finding_id, {
        "stage": BOUNDED_EXPERIMENT, "actor": actor, "at": at,
        "scope": scope, "metric": metric, "stopping_rule": stopping_rule,
        "body": {"design": design, "pre_registered_at": at}})


async def record_candidate(conn, finding_id, *, actor, description, change,
                           at, proposal_id: str | None = None) -> dict:
    return await _advance(conn, finding_id, {
        "stage": CANDIDATE_IMPROVEMENT, "actor": actor, "at": at,
        "body": {"description": description, "change": change},
        "proposal_id": proposal_id})


async def record_evaluation(conn, finding_id, *, actor, outcome, data_start,
                            data_end, evidence_refs, at,
                            metric_value=None, samples=None,
                            result: dict | None = None) -> dict:
    body: dict[str, Any] = {"result": dict(result or {})}
    if metric_value is not None:
        body["metric_value"] = metric_value
    if samples is not None:
        body["samples"] = samples
    return await _advance(conn, finding_id, {
        "stage": INDEPENDENT_EVALUATION, "actor": actor, "at": at,
        "outcome": outcome, "data_start": data_start, "data_end": data_end,
        "evidence_refs": evidence_refs, "body": body})


async def mark_release_eligible(conn, finding_id, *, actor, at,
                                note: str = "") -> dict:
    """ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW: a mark for a person. It changes no
    policy, limit or switch; releasing is a separate, human change."""
    return await _advance(conn, finding_id, {
        "stage": RELEASE_ELIGIBILITY, "actor": actor, "at": at,
        "outcome": ELIGIBLE,
        "body": {"note": str(note or "")[:2000],
                 "meaning": "a person may review this for release; nothing "
                            "is released or activated by this mark"}})


async def close(conn, finding_id, *, actor, reason, at) -> dict:
    return await _advance(conn, finding_id, {
        "stage": CLOSED, "actor": actor, "at": at,
        "body": {"reason": reason}})
