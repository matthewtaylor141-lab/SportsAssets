"""AUDREY'S SELF-IMPROVEMENT WORKFLOW: EVIDENCE -> CANDIDATE -> TRIAL ->
EVALUATION -> (APPROVAL | PRE-AUTHORIZED CANARY) -> RELEASE -> ROLLBACK.

WHAT THIS IS. The in-process half of the improvement loop. An improvement
task (agent_tasks, migration 152, kind IMPROVEMENT) names a CHANGE CLASS.
This module turns the task into candidate records (migration 155), evaluates
them by DETERMINISTIC REPLAY over recorded evidence, records every trial
(the rejected ones too), and -- only for a PRE-AUTHORIZED bounded class
whose acceptance rule is met -- writes a new `agent_policy_versions` row and
activates it under a canary, with rollback to the prior version. Everything
else stops at APPROVAL_READY for a named person.

WHAT IT NEVER DOES.
  * It never modifies the serving process or its code. A policy release is
    a new ROW; a consumer reads it through `registry.active_policy`. A code
    change is produced by `tools/improvement_sandbox.py` in a dev/CI
    checkout, on a branch, and never merged or deployed from here.
  * It never touches a PROTECTED KEY (risk limits, credentials, account
    authority, approval controls, submission switches, the evaluation
    criteria themselves). A candidate naming one is refused by name here,
    and the database refuses it again (migration 155).
  * It never lets a proposer evaluate or approve its own change, never
    approves without a PASS, and never re-scores a failed candidate (the
    evaluation is write-once in the database).
  * It never searches variants against one holdout until one wins: a
    holdout carries a trial budget and the database refuses the trial past
    it.

EVIDENCE CATEGORIES (shared with the audit): a replay over settled
outcomes is KNOWN_SETTLEMENT_HYPOTHETICAL_EXECUTION -- an unplaced order is
not proven to have filled; a throughput projection is
SIMULATED_WITH_DISCLOSED_ASSUMPTIONS and says which assumptions.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any

VERSION = "AUDREY_IMPROVEMENT_V1"
TASK_KIND = "IMPROVEMENT"

DEREK, XAVIER, AUDREY = "DEREK", "XAVIER", "AUDREY"
AGENT_IDS = (DEREK, XAVIER, AUDREY)

#: WHO EVALUATES. Never an agent that proposes: the database refuses
#: evaluated_by = proposed_by.
EVALUATOR_REPLAY = "EVALUATOR:DETERMINISTIC_REPLAY"
EVALUATOR_SANDBOX_REVIEW = "EVALUATOR:SANDBOX_REPORT_REVIEW"
PREAUTH_PREFIX = "PRE_AUTHORIZED:"

# ── EVIDENCE CATEGORIES (never merged) ────────────────────────────────
ACTUAL = "ACTUAL_EXECUTED_PNL"
KNOWN_SETTLEMENT = "KNOWN_SETTLEMENT_HYPOTHETICAL_EXECUTION"
SIMULATED = "SIMULATED_WITH_DISCLOSED_ASSUMPTIONS"
UNEVALUABLE = "NOT_OBSERVED_OR_UNEVALUABLE"
EVIDENCE_CATEGORIES = (ACTUAL, KNOWN_SETTLEMENT, SIMULATED, UNEVALUABLE)

# ── KINDS AND VERDICTS ────────────────────────────────────────────────
K_POLICY = "POLICY_PARAMETER"
K_CODE = "CODE"
K_MODEL = "MODEL"
K_REPORT = "REPORT_THRESHOLD"
V_PASS = "PASS"
V_HARM = "FAIL_HARM"
V_NO_GAIN = "FAIL_NO_IMPROVEMENT"
V_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
V_SELECTED = "SELECTED"
V_NOT_SELECTED = "NOT_SELECTED"
SCOPE_PREAUTH = "PRE_AUTHORIZED_UNATTENDED"
SCOPE_APPROVAL = "REQUIRES_APPROVAL"
NEEDS_SANDBOX = "NEEDS_SANDBOX"

# ── REFUSALS ──────────────────────────────────────────────────────────
R_SCHEMA = "THE_IMPROVEMENT_TABLES_ARE_NOT_IN_THIS_DATABASE"
R_TASKS_UNAVAILABLE = "THE_AGENT_TASK_TABLES_ARE_NOT_IN_THIS_DATABASE"
R_UNKNOWN_CLASS = "THAT_IS_NOT_A_REGISTERED_CHANGE_CLASS"
R_PROTECTED_CLASS = "THAT_CHANGE_CLASS_IS_PROTECTED"
R_PROTECTED_KEY = "A_CANDIDATE_MAY_NOT_TOUCH_A_PROTECTED_KEY"
R_OUT_OF_BOUNDS = "THE_PARAMETER_IS_OUTSIDE_THE_CLASS_BOUNDS"
R_UNKNOWN_PARAM = "THAT_PARAMETER_IS_NOT_IN_THE_CLASS"
R_NO_SUCH_CANDIDATE = "NO_SUCH_CANDIDATE"
R_NOT_APPROVAL_READY = "THE_CANDIDATE_IS_NOT_APPROVAL_READY"
R_NOT_PASSED = "THE_CANDIDATE_HAS_NO_PASSING_EVALUATION"
R_NO_APPROVER = "AN_APPROVAL_NAMES_ITS_APPROVER"
R_SELF_APPROVAL = "THE_APPROVER_PROPOSED_OR_EVALUATED_THIS_CANDIDATE"
R_AGENT_APPROVER = "AN_AGENT_OR_EVALUATOR_CANNOT_APPROVE"
R_HOLDOUT_BUDGET = "HOLDOUT_BUDGET_EXHAUSTED"
R_NOT_PREAUTHORIZED = "THE_CLASS_IS_NOT_PRE_AUTHORIZED_FOR_UNATTENDED_RELEASE"
R_ACCEPTANCE = "THE_CLASS_ACCEPTANCE_RULE_IS_NOT_MET"
R_POLICY_TABLE = "AGENT_POLICY_VERSIONS_IS_NOT_IN_THIS_DATABASE"
R_NO_PRIOR_VERSION = "NO_PRIOR_POLICY_VERSION_TO_REACTIVATE"
R_NO_SUCH_RELEASE = "NO_SUCH_LIVE_RELEASE"

# ═════════════════════════════════════════════════════════════════════
# 0 · PROTECTED KEYS AND THE CHANGE-CLASS REGISTRY
# ═════════════════════════════════════════════════════════════════════

#: NO CANDIDATE MAY NAME ONE OF THESE. The same list is a CHECK in
#: migration 155 (improvement_no_protected_key_ck); a test pins the two
#: equal.
PROTECTED_KEYS = (
    "risk_limits", "max_position_usd", "max_exposure_usd",
    "account_exposure_limit", "daily_loss_limit", "credentials",
    "api_key", "admin_token", "operator_password",
    "funded_resolution_key", "account_authority", "owner_authorization",
    "approval_controls", "approved_by", "FUNDED_SUBMISSION_ENABLED",
    "REAL_ORDER_SUBMISSION_ENABLED", "FUNDED_EXIT_SUBMISSION_ENABLED",
    "evaluation_criteria", "acceptance_criteria", "harm_metrics",
    "success_metrics", "holdout_budget", "change_class_registry",
    "protected_keys", "capital_critical_tests")

#: CLASS NAMES THAT ARE NEVER REGISTERED AND ARE REFUSED BY NAME: the
#: categories of change no agent makes, whatever the evidence.
PROTECTED_CLASSES = (
    "RISK_LIMIT", "CREDENTIAL", "ACCOUNT_AUTHORITY", "APPROVAL_CONTROL",
    "SUBMISSION_SWITCH", "EVALUATION_CRITERIA", "GATE_TOOLS")


@dataclass(frozen=True)
class ChangeClass:
    name: str
    kind: str
    agent: str
    description: str
    policy_key: str | None = None
    #: parameter -> (min, max, integer?)
    bounds: dict = field(default_factory=dict)
    pre_authorized: bool = False
    evaluator: str | None = None
    success_metrics: dict = field(default_factory=dict)
    harm_metrics: dict = field(default_factory=dict)
    acceptance: dict = field(default_factory=dict)
    canary: dict = field(default_factory=dict)
    rollback: str = ""
    #: where the versioned default lives, for the sandbox (path, constant)
    code_default: tuple | None = None
    read_only: bool = False


CHANGE_CLASSES: dict[str, ChangeClass] = {c.name: c for c in (
    ChangeClass(
        name="COLLECTION_PASS_LIMIT", kind=K_POLICY, agent=DEREK,
        description=("how many pair-collection candidates one pass attempts "
                     "(bettor_pair_observations.CANDIDATES_PER_PASS)"),
        policy_key="collection.pass_limit",
        bounds={"candidates_per_pass": (1, 10, True)},
        pre_authorized=True, evaluator="collection_pass_limit_replay",
        success_metrics={"mean_additional_attempts_per_pass": {">=": 0.5},
                         "min_passes": 8},
        harm_metrics={"projected_deadline_breach_fraction": {"<=": 0.05},
                      "projected_p90_elapsed_over_budget": {"<=": 0.9}},
        acceptance={"holdout_verdict": V_PASS, "max_step": 2,
                    "within_bounds": True},
        canary={"min_samples": 6, "max_deadline_stop_fraction": 0.10,
                "early_rollback_stops": 2},
        rollback=("reactivate the prior agent_policy_versions row for "
                  "(DEREK, collection.pass_limit) and mark the released "
                  "version REJECTED (improvement.rollback)"),
        code_default=("backend/sportsassets/bettor_pair_observations.py",
                      "CANDIDATES_PER_PASS")),
    ChangeClass(
        name="REPORT_THRESHOLD", kind=K_REPORT, agent=AUDREY,
        description=("read-only thresholds of Audrey's own report (when a "
                     "finding is raised); changes nothing any agent acts on"),
        policy_key="audrey.report_thresholds",
        bounds={"min_fixtures_for_statistic": (10, 200, True),
                "collection_alert_passes": (1, 96, True),
                "evidence_gap_alert_fraction": (0.05, 0.9, False)},
        pre_authorized=True, evaluator="report_threshold_check",
        success_metrics={"within_bounds": True},
        harm_metrics={"affects_orders": {"==": False}},
        acceptance={"holdout_verdict": V_PASS, "within_bounds": True},
        canary={"min_samples": 0},
        rollback=("reactivate the prior agent_policy_versions row for "
                  "(AUDREY, audrey.report_thresholds)"),
        read_only=True),
    ChangeClass(
        name="DEREK_ENTRY_THRESHOLD", kind=K_POLICY, agent=DEREK,
        description=("the shadow evaluator's minimum NET edge per contract "
                     "(bettor_external_shadow.MIN_NET_EDGE_PER_CONTRACT) -- "
                     "an existing admission filter; NOT Derek's binding "
                     "entry policy, which is DEREK_ENTRY_POLICY_THRESHOLD"),
        policy_key="derek.entry_threshold",
        bounds={"min_net_edge_per_contract": (0.0, 0.10, False)},
        pre_authorized=False, evaluator="derek_threshold_replay",
        success_metrics={"fixture_mean_pnl_delta_per_contract": {">=": 0.0},
                         "min_fixtures": 30},
        harm_metrics={"fixture_mean_pnl_per_contract": {">=": 0.0},
                      "negative_fixture_fraction": {"<=": 0.6}},
        acceptance={"holdout_verdict": V_PASS},
        rollback=("reactivate the prior agent_policy_versions row for "
                  "(DEREK, derek.entry_threshold)"),
        code_default=("backend/sportsassets/bettor_external_shadow.py",
                      "MIN_NET_EDGE_PER_CONTRACT")),
    ChangeClass(
        name="DEREK_ENTRY_POLICY_THRESHOLD", kind=K_POLICY, agent=DEREK,
        description=("Derek's binding entry policy threshold: the minimum "
                     "GROSS probability edge in probability points "
                     "(qualified probability - executable price), "
                     "agents.derek_policy DEFAULT_PARAMS['min_gross_edge_pp']"),
        policy_key="DEREK_ENTRY_POLICY",
        bounds={"min_gross_edge_pp": (0.0, 0.20, False)},
        pre_authorized=False, evaluator="derek_gross_edge_replay",
        success_metrics={"fixture_mean_pnl_delta_per_contract": {">=": 0.0},
                         "min_fixtures": 30},
        harm_metrics={"fixture_mean_pnl_per_contract": {">=": 0.0},
                      "negative_fixture_fraction": {"<=": 0.6}},
        acceptance={"holdout_verdict": V_PASS},
        rollback=("reactivate the prior agent_policy_versions row for "
                  "(DEREK, DEREK_ENTRY_POLICY)"),
        code_default=("backend/sportsassets/agents/derek_policy.py",
                      "DEFAULT_PARAMS['min_gross_edge_pp']")),
    ChangeClass(
        name="XAVIER_POLICY_PARAMETER", kind=K_POLICY, agent=XAVIER,
        description="a bounded Xavier policy parameter; no replay registered",
        policy_key="XAVIER_MANAGEMENT_POLICY", pre_authorized=False, evaluator=None,
        rollback="reactivate the prior agent_policy_versions row"),
    ChangeClass(
        name="COLLECTION_REFUSAL_MEMORY", kind=K_CODE, agent=DEREK,
        description=("how long a refused fixture is skipped by the pair "
                     "collector (code; needs the sandbox)"),
        rollback="revert the improve/<task_id> branch commit"),
    ChangeClass(
        name="XAVIER_EVIDENCE_CAPTURE", kind=K_CODE, agent=XAVIER,
        description=("freeze more decision-time evidence on Xavier's "
                     "alternatives (code; needs the sandbox)"),
        rollback="revert the improve/<task_id> branch commit"),
    ChangeClass(
        name="XAVIER_SEARCH_BUDGET", kind=K_CODE, agent=XAVIER,
        description=("how much of the discovered contract set Xavier's "
                     "ladder examines before deciding (code; needs the "
                     "sandbox)"),
        rollback="revert the improve/<task_id> branch commit"),
    ChangeClass(
        name="CODE_CHANGE", kind=K_CODE, agent=AUDREY,
        description="a code change supplied as a unified diff",
        rollback="revert the improve/<task_id> branch commit"),
    ChangeClass(
        name="MODEL_CANDIDATE", kind=K_MODEL, agent=XAVIER,
        description=("a registry model; promoted only by "
                     "bettor_funded_model.promote with a named approver"),
        rollback="bettor_funded_model.rollback to the prior approved model"),
)}


def class_of(name: str | None) -> ChangeClass | None:
    return CHANGE_CLASSES.get(str(name or ""))


def protected_touched(keys) -> list:
    """Every protected key among `keys`, by name (case-sensitive and
    case-folded: `funded_submission_enabled` is still the switch)."""
    folded = {k.lower(): k for k in PROTECTED_KEYS}
    hit = []
    for k in keys or ():
        s = str(k)
        if s in PROTECTED_KEYS:
            hit.append(s)
        elif s.lower() in folded:
            hit.append(folded[s.lower()])
    return sorted(set(hit))


def check_class(name: str | None) -> dict:
    n = str(name or "")
    if n.upper() in PROTECTED_CLASSES:
        return {"ok": False, "refusal": R_PROTECTED_CLASS, "change_class": n}
    cls = class_of(n)
    if cls is None:
        return {"ok": False, "refusal": R_UNKNOWN_CLASS, "change_class": n}
    return {"ok": True, "cls": cls}


def check_params(cls: ChangeClass, params: dict | None) -> dict:
    """Every parameter known to the class and within its bounds."""
    for k, v in (params or {}).items():
        if protected_touched([k]):
            return {"ok": False, "refusal": R_PROTECTED_KEY,
                    "protected": protected_touched([k])}
        if k not in cls.bounds:
            return {"ok": False, "refusal": R_UNKNOWN_PARAM, "param": k}
        lo, hi, integer = cls.bounds[k]
        x = _num(v)
        if x is None or x < lo or x > hi or (integer and x != int(x)):
            return {"ok": False, "refusal": R_OUT_OF_BOUNDS, "param": k,
                    "value": v, "bounds": [lo, hi]}
    return {"ok": True}


def describe_registry() -> dict:
    return {"version": VERSION,
            "classes": {n: {"kind": c.kind, "agent": c.agent,
                            "policy_key": c.policy_key,
                            "bounds": {k: list(v) for k, v in
                                       c.bounds.items()},
                            "pre_authorized": c.pre_authorized,
                            "unattended_release": (
                                "ONLY_BY_ACCEPTANCE_AND_CANARY"
                                if c.pre_authorized else
                                "NEVER: STOPS_AT_APPROVAL_READY"),
                            "evaluator": c.evaluator,
                            "success_metrics": c.success_metrics,
                            "harm_metrics": c.harm_metrics,
                            "acceptance": c.acceptance,
                            "canary": c.canary, "rollback": c.rollback,
                            "read_only": c.read_only,
                            "description": c.description}
                        for n, c in CHANGE_CLASSES.items()},
            "protected_keys": list(PROTECTED_KEYS),
            "protected_classes": list(PROTECTED_CLASSES)}


# ═════════════════════════════════════════════════════════════════════
# 1 · SMALL HELPERS
# ═════════════════════════════════════════════════════════════════════

def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _obj(v):
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return None
    return v


def _ts(epoch: float | None):
    if epoch is None:
        return None
    return _dt.datetime.fromtimestamp(float(epoch), _dt.timezone.utc)


def _epoch(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    return _num(v)


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode()).hexdigest()


def _j(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


def _row(r) -> dict | None:
    if r is None:
        return None
    out = dict(r)
    for k, v in list(out.items()):
        if isinstance(v, str) and k in (
                "evidence", "training_boundary", "evaluation_boundary",
                "success_metrics", "harm_metrics", "params", "test_results",
                "evaluation", "variant", "metrics", "acceptance", "canary",
                "detail", "spec", "outcome", "fixture_rule"):
            out[k] = _obj(v)
    for k, v in list(out.items()):
        if isinstance(v, _dt.datetime):
            out[k] = v.timestamp()
    return out


def candidate_id_for(task_id: str, change_class: str, variant) -> str:
    return "cand:%s:%s" % (task_id, _sha([change_class, variant])[:12])


def percentile(xs, q: float):
    v = sorted(x for x in xs if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return v[lo] if lo == hi else v[lo] + (v[hi] - v[lo]) * (k - lo)


async def _regclass(conn, name: str) -> bool:
    try:
        return await conn.fetchval("SELECT to_regclass($1)", name) is not None
    except Exception:                                           # noqa: BLE001
        return False


async def has_schema(conn) -> bool:
    return await _regclass(conn, "improvement_candidates")


# ═════════════════════════════════════════════════════════════════════
# 2 · THE CORE REGISTRY, GUARDED (migration 152 / agents.registry)
# ═════════════════════════════════════════════════════════════════════

def _registry():
    """The core stream's registry when it is present, else None. Every call
    below falls back to the contract's own tables, and to a named
    unavailability when those are absent too."""
    try:
        from . import registry as R                              # noqa: PLC0415
        return R
    except Exception:                                           # noqa: BLE001
        return None


async def tasks_available(conn) -> bool:
    return (await _regclass(conn, "agent_tasks")
            and await _regclass(conn, "agent_task_events"))


async def _ensure_identity(conn, agent_id: str) -> None:
    """The agent's identity row, written by the registry's own writer (the
    full mandate/permissions row migration 152 requires). A SAVEPOINT keeps
    a failure here from aborting the caller's transaction."""
    try:
        async with conn.transaction():
            R = _registry()
            if R is not None and hasattr(R, "ensure_identities"):
                await R.ensure_identities(conn, code_version=None)
                return
            await conn.execute(
                "INSERT INTO agent_identities (agent_id, display_name) "
                "VALUES ($1, $2) ON CONFLICT (agent_id) DO NOTHING",
                agent_id, agent_id.title())
    except Exception:                                           # noqa: BLE001
        pass


async def create_task(conn, *, assignee, created_by, kind, title, spec,
                      directive_id=None, evidence=None, task_id=None,
                      now=None) -> dict:
    """registry.create_task when present (idempotent on task_id), else the
    same write against agent_tasks directly."""
    R = _registry()
    if R is not None and hasattr(R, "create_task"):
        return await R.create_task(
            conn, assignee=assignee, created_by=created_by, kind=kind,
            title=title, spec=spec, directive_id=directive_id,
            evidence=evidence, task_id=task_id, now=now)
    if not await tasks_available(conn):
        return {"ok": False, "refusal": R_TASKS_UNAVAILABLE}
    at = _ts(now if now is not None else time.time())
    tid = task_id or "task:%s" % _sha([assignee, kind, title, spec])[:16]
    await _ensure_identity(conn, assignee)
    got = await conn.fetchval(
        "INSERT INTO agent_tasks (task_id, assignee, created_by, kind, title, "
        " spec, status, directive_id, evidence, outcome, created_at, "
        " updated_at) VALUES ($1,$2,$3,$4,$5,$6::jsonb,'OPEN',$7,$8::jsonb,"
        " '{}'::jsonb,$9,$9) ON CONFLICT (task_id) DO NOTHING "
        "RETURNING task_id", tid, assignee, created_by, kind, title,
        _j(spec or {}), directive_id, _j(evidence or []), at)
    if got is not None:
        await conn.execute(
            "INSERT INTO agent_task_events (task_id, at, kind, actor, detail) "
            "VALUES ($1,$2,'CREATED',$3,$4::jsonb)", tid, at, created_by,
            _j({"title": title}))
    row = await read_task(conn, tid)
    return {"ok": True, "created": got is not None, "task_id": tid,
            "task": row, "source": "FALLBACK_SQL_REGISTRY_NOT_PRESENT"}


async def task_event(conn, task_id, *, kind, actor, detail, status=None,
                     now=None) -> dict:
    R = _registry()
    if R is not None and hasattr(R, "task_event"):
        return await R.task_event(conn, task_id, kind=kind, actor=actor,
                                  detail=detail, status=status, now=now)
    if not await tasks_available(conn):
        return {"ok": False, "refusal": R_TASKS_UNAVAILABLE}
    at = _ts(now if now is not None else time.time())
    await conn.execute(
        "INSERT INTO agent_task_events (task_id, at, kind, actor, detail) "
        "VALUES ($1,$2,$3,$4,$5::jsonb)", task_id, at, kind, actor,
        _j(detail or {}))
    if status:
        await conn.execute(
            "UPDATE agent_tasks SET status=$2, updated_at=$3 WHERE task_id=$1",
            task_id, status, at)
    return {"ok": True, "task_id": task_id, "status": status}


async def read_task(conn, task_id: str) -> dict | None:
    if not await tasks_available(conn):
        return None
    return _row(await conn.fetchrow(
        "SELECT * FROM agent_tasks WHERE task_id=$1", task_id))


async def read_tasks(conn, *, kind: str | None = TASK_KIND,
                     statuses=None, limit: int = 200) -> list:
    if not await tasks_available(conn):
        return []
    args: list = []
    where = []
    if kind:
        args.append(kind)
        where.append("kind=$%d" % len(args))
    if statuses:
        args.append(list(statuses))
        where.append("status = ANY($%d::text[])" % len(args))
    args.append(int(limit))
    sql = ("SELECT * FROM agent_tasks "
           + ("WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY created_at, task_id LIMIT $%d" % len(args))
    return [_row(r) for r in await conn.fetch(sql, *args)]


async def task_events(conn, task_id: str, limit: int = 50) -> list:
    if not await tasks_available(conn):
        return []
    return [_row(r) for r in await conn.fetch(
        "SELECT * FROM agent_task_events WHERE task_id=$1 "
        " ORDER BY event_id DESC LIMIT $2", task_id, int(limit))]


async def _event(conn, *, candidate_id, task_id, kind, actor, detail,
                 now) -> None:
    await conn.execute(
        "INSERT INTO improvement_events (candidate_id, task_id, at, kind, "
        " actor, detail) VALUES ($1,$2,$3,$4,$5,$6::jsonb)", candidate_id,
        task_id, _ts(now), kind, actor, _j(detail or {}))


# ═════════════════════════════════════════════════════════════════════
# 3 · CANDIDATES, TRIALS, EVALUATIONS
# ═════════════════════════════════════════════════════════════════════

async def propose(conn, *, task_id: str, change_class: str, proposed_by: str,
                  hypothesis: str, evidence: dict, affected_behavior: str,
                  params: dict | None = None, diff: str | None = None,
                  touched_keys=None, training_boundary=None,
                  evaluation_boundary=None, artifact_ref=None,
                  base_commit=None, test_results=None, variant=None,
                  candidate_id: str | None = None, now: float) -> dict:
    """RECORD ONE CANDIDATE. Idempotent on its deterministic id. The
    success and harm criteria are the CLASS's, never the proposer's."""
    chk = check_class(change_class)
    if not chk["ok"]:
        return chk
    cls: ChangeClass = chk["cls"]
    keys = sorted(set(list(touched_keys or []) + list((params or {}).keys())
                      + ([cls.policy_key] if cls.policy_key else [])))
    hit = protected_touched(keys)
    if hit:
        return {"ok": False, "refusal": R_PROTECTED_KEY, "protected": hit}
    pc = check_params(cls, params)
    if not pc["ok"]:
        return pc
    cid = candidate_id or candidate_id_for(
        task_id, change_class, variant if variant is not None else
        (params if params is not None else _sha(diff or "")[:16]))
    scope = SCOPE_PREAUTH if cls.pre_authorized else SCOPE_APPROVAL
    await conn.execute(
        "INSERT INTO improvement_candidates (candidate_id, task_id, "
        " assigned_agent, change_class, change_kind, hypothesis, evidence, "
        " affected_behavior, training_boundary, evaluation_boundary, "
        " success_metrics, harm_metrics, policy_key, params, touched_keys, "
        " diff, base_commit, artifact_ref, test_results, release_scope, "
        " rollback_procedure, state, proposed_by, created_at, updated_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb,$10::jsonb,"
        " $11::jsonb,$12::jsonb,$13,$14::jsonb,$15::text[],$16,$17,$18,"
        " $19::jsonb,$20,$21,'PROPOSED',$22,$23,$23) "
        "ON CONFLICT (candidate_id) DO NOTHING",
        cid, task_id, cls.agent, cls.name, cls.kind, hypothesis,
        _j(evidence or {}), affected_behavior, _j(training_boundary or {}),
        _j(evaluation_boundary or {}), _j(cls.success_metrics),
        _j(cls.harm_metrics), cls.policy_key,
        None if params is None else _j(params), keys, diff, base_commit,
        artifact_ref, None if test_results is None else _j(test_results),
        scope, cls.rollback or "revert", proposed_by, _ts(now))
    await _event(conn, candidate_id=cid, task_id=task_id, kind="PROPOSED",
                 actor=proposed_by, detail={"params": params,
                                            "artifact_ref": artifact_ref},
                 now=now)
    return {"ok": True, "candidate_id": cid,
            "candidate": await candidate(conn, cid)}


async def candidate(conn, candidate_id: str) -> dict | None:
    if not await has_schema(conn):
        return None
    return _row(await conn.fetchrow(
        "SELECT * FROM improvement_candidates WHERE candidate_id=$1",
        candidate_id))


async def ensure_holdout(conn, *, holdout_id: str, description: str,
                         rule: dict, budget: int | None = None) -> dict:
    b = int(budget if budget is not None else
            os.getenv("IMPROVEMENT_HOLDOUT_TRIAL_BUDGET", "3") or 3)
    await conn.execute(
        "INSERT INTO improvement_holdouts (holdout_id, description, "
        " fixture_rule, trial_budget) VALUES ($1,$2,$3::jsonb,$4) "
        "ON CONFLICT (holdout_id) DO NOTHING", holdout_id, description,
        _j(rule), max(1, b))
    return await holdout_usage(conn, holdout_id)


async def holdout_usage(conn, holdout_id: str) -> dict:
    r = await conn.fetchrow(
        "SELECT h.trial_budget, (SELECT count(*) FROM improvement_trials t "
        "  WHERE t.holdout_id=h.holdout_id) AS used "
        "  FROM improvement_holdouts h WHERE h.holdout_id=$1", holdout_id)
    if r is None:
        return {"holdout_id": holdout_id, "exists": False}
    return {"holdout_id": holdout_id, "exists": True,
            "budget": int(r["trial_budget"]), "used": int(r["used"]),
            "remaining": max(0, int(r["trial_budget"]) - int(r["used"]))}


async def record_trial(conn, *, candidate_id: str, task_id: str,
                       segment: str, variant, metrics: dict, verdict: str,
                       evidence_category: str, holdout_id: str | None = None,
                       training_boundary=None, evaluation_boundary=None,
                       evaluated_by: str = EVALUATOR_REPLAY,
                       now: float) -> dict:
    """ONE TRIAL, WHATEVER ITS VERDICT. A holdout past its budget is
    refused by the database; that refusal is returned by name."""
    tid = "trial:%s:%s:%s" % (candidate_id, segment,
                              _sha([holdout_id, training_boundary,
                                    evaluation_boundary])[:10])
    if holdout_id is not None:
        use = await holdout_usage(conn, holdout_id)
        if use.get("exists") and use["remaining"] <= 0:
            return {"ok": False, "refusal": R_HOLDOUT_BUDGET, "usage": use}
    try:
        async with conn.transaction():
            wrote = await conn.fetchval(
                "INSERT INTO improvement_trials (trial_id, candidate_id, "
                " task_id, segment, holdout_id, variant, training_boundary, "
                " evaluation_boundary, evidence_category, metrics, verdict, "
                " evaluated_by, created_at) VALUES ($1,$2,$3,$4,$5,$6::jsonb,"
                " $7,$8,$9,$10::jsonb,$11,$12,$13) "
                "ON CONFLICT (trial_id) DO NOTHING RETURNING trial_id",
                tid, candidate_id, task_id, segment, holdout_id, _j(variant),
                _ts(training_boundary), _ts(evaluation_boundary),
                evidence_category, _j(metrics), verdict, evaluated_by,
                _ts(now))
    except Exception as exc:                                    # noqa: BLE001
        if "HOLDOUT_BUDGET_EXHAUSTED" in str(exc):
            return {"ok": False, "refusal": R_HOLDOUT_BUDGET,
                    "usage": await holdout_usage(conn, holdout_id)}
        raise
    return {"ok": True, "trial_id": tid, "written": wrote is not None}


async def record_evaluation(conn, *, candidate_id: str, evaluation: dict,
                            evaluated_by: str, state: str, now: float) -> dict:
    await conn.execute(
        "UPDATE improvement_candidates SET evaluation=$2::jsonb, "
        " evaluated_by=$3, evaluated_at=$4, state=$5 "
        " WHERE candidate_id=$1 AND evaluation IS NULL",
        candidate_id, _j(evaluation), evaluated_by, _ts(now), state)
    c = await candidate(conn, candidate_id)
    await _event(conn, candidate_id=candidate_id, task_id=c["task_id"],
                 kind="EVALUATED", actor=evaluated_by,
                 detail={"verdict": evaluation.get("verdict"),
                         "state": state}, now=now)
    return c


def judge(metrics: dict, *, success: dict, harm: dict,
          min_key: str | None = None, n_key: str | None = None) -> dict:
    """THE CLASS'S RULE, applied: harm first (a breach rejects whatever
    the gain), then evidence volume, then the success criteria."""
    def ok(v, rule):
        if v is None:
            return None
        op, lim = next(iter(rule.items()))
        return {"<=": v <= lim, ">=": v >= lim, "==": v == lim,
                "<": v < lim, ">": v > lim}[op]
    breaches = []
    for k, rule in (harm or {}).items():
        if not isinstance(rule, dict):
            continue
        r = ok(metrics.get(k), rule)
        if r is False:
            breaches.append({"metric": k, "value": metrics.get(k),
                             "rule": rule})
    if min_key and n_key and (metrics.get(n_key) or 0) < (
            success.get(min_key) or 0):
        return {"verdict": V_INSUFFICIENT, "harm_breaches": breaches,
                "why": "%s=%s below %s=%s" % (
                    n_key, metrics.get(n_key), min_key, success.get(min_key))}
    if breaches:
        return {"verdict": V_HARM, "harm_breaches": breaches}
    misses = []
    for k, rule in (success or {}).items():
        if not isinstance(rule, dict):
            continue
        r = ok(metrics.get(k), rule)
        if r is not True:
            misses.append({"metric": k, "value": metrics.get(k),
                           "rule": rule})
    if misses:
        return {"verdict": V_NO_GAIN, "harm_breaches": [],
                "success_misses": misses}
    return {"verdict": V_PASS, "harm_breaches": [], "success_misses": []}


# ═════════════════════════════════════════════════════════════════════
# 4 · DETERMINISTIC REPLAYS (pure)
# ═════════════════════════════════════════════════════════════════════

PASS_LIMIT_ASSUMPTIONS = (
    "each extra candidate a pass attempts costs the p90 of the recorded "
    "per-attempt elapsed time (bettor_pair_observation_attempts); a pass "
    "still stops at its recorded budget; candidates offered are what the "
    "pass recorded; nothing about what an extra attempt would have "
    "observed is claimed")


def replay_pass_limit(samples: list, *, limit: int,
                      per_attempt_s: float | None) -> dict:
    """PROJECT ONE PASS LIMIT OVER RECORDED PASSES. SIMULATED WITH
    DISCLOSED ASSUMPTIONS: it projects throughput and time, not
    observations."""
    evaluated, unevaluable, extra, ratios, breaches = 0, 0, [], [], 0
    for s in samples:
        offered, attempted = s.get("offered"), s.get("attempted")
        elapsed, budget = _num(s.get("elapsed_s")), _num(s.get("budget_s"))
        if offered is None or attempted is None or elapsed is None \
                or budget is None or budget <= 0:
            unevaluable += 1
            continue
        per = per_attempt_s
        if per is None:
            per = (elapsed / attempted) if attempted else None
        if per is None:
            unevaluable += 1
            continue
        proj = min(int(offered), int(limit))
        add = max(0, proj - int(attempted))
        proj_elapsed = elapsed + add * per
        evaluated += 1
        extra.append(add)
        ratios.append(proj_elapsed / budget)
        if proj_elapsed > budget:
            breaches += 1
    return {
        "limit": int(limit), "passes_evaluated": evaluated,
        "passes_unevaluable": unevaluable,
        "mean_additional_attempts_per_pass": (
            round(sum(extra) / evaluated, 6) if evaluated else None),
        "projected_deadline_breach_fraction": (
            round(breaches / evaluated, 6) if evaluated else None),
        "projected_p90_elapsed_over_budget": (
            None if not ratios else round(percentile(ratios, 0.9), 6)),
        "per_attempt_s_assumed": per_attempt_s,
        "evidence_category": SIMULATED,
        "assumptions": PASS_LIMIT_ASSUMPTIONS}


def assign_holdout(fixture: str, *, salt: str, percent: int) -> bool:
    """FIXTURE-LEVEL, DETERMINISTIC: every row of one fixture lands on the
    same side, so a fixture is never split between training and holdout."""
    h = int(hashlib.sha256(("%s|%s" % (salt, fixture)).encode())
            .hexdigest()[:8], 16)
    return (h % 100) < int(percent)


def fixture_metrics(rows: list, *, value_key: str = "pnl") -> dict:
    """ONE WEIGHT PER FIXTURE: repeated decisions on one fixture are one
    example. `decisions` is reported for reference only."""
    by: dict = {}
    for r in rows:
        by.setdefault(str(r["fixture"]), []).append(float(r[value_key]))
    means = [sum(v) / len(v) for v in by.values()]
    n = len(means)
    return {"fixtures": n, "decisions": len(rows),
            "fixture_mean_pnl_per_contract": (round(sum(means) / n, 6)
                                              if n else None),
            "negative_fixture_fraction": (
                round(sum(1 for m in means if m < 0) / n, 6) if n else None),
            "weighting": "ONE_WEIGHT_PER_FIXTURE"}


def split_rows(rows: list, *, training_boundary: float,
               evaluation_boundary: float, salt: str, percent: int) -> dict:
    """TRAINING vs PROSPECTIVE HOLDOUT, with no future leakage.

    Training: decided AND outcome known by the training boundary, fixture
    outside the holdout. Holdout: fixture in the holdout, decided after the
    training boundary, outcome known by the evaluation boundary, and the
    fixture never seen in training. Every exclusion is counted by reason."""
    train, hold = [], []
    excl = {"outcome_unknown": 0, "outcome_after_training_boundary": 0,
            "outcome_after_evaluation_boundary": 0,
            "holdout_fixture_decided_before_boundary": 0,
            "fixture_seen_in_training": 0, "non_holdout_after_boundary": 0}
    tb, eb = float(training_boundary), float(evaluation_boundary)
    for r in rows:
        oa = r.get("outcome_at")
        if oa is None or r.get("outcome") is None:
            excl["outcome_unknown"] += 1
            continue
        in_hold = assign_holdout(r["fixture"], salt=salt, percent=percent)
        if not in_hold:
            if r["decided_at"] <= tb:
                if oa <= tb:
                    train.append(r)
                else:
                    excl["outcome_after_training_boundary"] += 1
            else:
                excl["non_holdout_after_boundary"] += 1
            continue
        if r["decided_at"] <= tb:
            excl["holdout_fixture_decided_before_boundary"] += 1
            continue
        if oa > eb:
            excl["outcome_after_evaluation_boundary"] += 1
            continue
        hold.append(r)
    seen = {str(r["fixture"]) for r in train}
    kept = []
    for r in hold:
        if str(r["fixture"]) in seen:
            excl["fixture_seen_in_training"] += 1
        else:
            kept.append(r)
    return {"training": train, "holdout": kept, "excluded": excl,
            "training_fixtures": sorted(seen),
            "holdout_fixtures": sorted({str(r["fixture"]) for r in kept})}


EDGE_REFUSAL = "NO_ACTION_HAS_POSITIVE_NET_EDGE"


#: Float tolerance for the gross-edge boundary (exactly 5 pp qualifies).
GROSS_EDGE_TOLERANCE = 1e-9
BASIS_NET = "NET_EDGE_PER_CONTRACT"
BASIS_GROSS = "GROSS_PROBABILITY_EDGE"


def derek_selectable(row: dict, threshold: float, *,
                     basis: str = BASIS_NET) -> bool:
    """Would a threshold select this recorded valuation? Only a row whose
    ONLY refusal was the edge (or none), with its decision-time edge and
    cost recorded. Every other refusal still refuses.

    BASIS_NET: the shadow evaluator's net edge per contract, strictly above.
    BASIS_GROSS: Derek's policy measure -- decision-time probability minus
    executable price, at or above the threshold (the owner's 5 pp rule)."""
    cost = _num(row.get("cost"))
    other = [r for r in (row.get("refusals") or []) if r != EDGE_REFUSAL]
    if basis == BASIS_GROSS:
        p, price = _num(row.get("probability")), _num(row.get("price"))
        if p is None or price is None or cost is None:
            return False
        return not other and (p - price) >= float(threshold) - \
            GROSS_EDGE_TOLERANCE
    edge = _num(row.get("edge"))
    if edge is None or cost is None:
        return False
    return not other and edge > float(threshold)


def replay_derek_threshold(rows: list, *, threshold: float,
                           basis: str = BASIS_NET) -> dict:
    """KNOWN SETTLEMENT, HYPOTHETICAL EXECUTION: per contract, the settled
    payout minus the decision-time cost of every row the threshold would
    select. An unplaced order is not proven to have filled."""
    sel = [dict(r, pnl=float(r["outcome"]) - float(r["cost"]))
           for r in rows if derek_selectable(r, threshold, basis=basis)]
    m = fixture_metrics(sel)
    return dict(m, threshold=float(threshold), basis=basis,
                evidence_category=KNOWN_SETTLEMENT,
                could_have_filled="UNPROVEN")


# ═════════════════════════════════════════════════════════════════════
# 5 · POLICY VERSIONS, RELEASES, ROLLBACK
# ═════════════════════════════════════════════════════════════════════

async def current_policy(conn, cls: ChangeClass) -> dict:
    """The ACTIVE version's params, else the code default, labelled."""
    default = code_default_params(cls)
    R = _registry()
    if R is not None and hasattr(R, "active_policy"):
        try:
            got = await R.active_policy(conn, cls.agent, cls.policy_key,
                                        default=default)
            if isinstance(got, dict):
                params = got.get("params") if isinstance(
                    got.get("params"), dict) else {
                        k: v for k, v in got.items() if k in cls.bounds}
                return {"params": params or default,
                        "version": got.get("version"),
                        "source": got.get("source")}
        except Exception:                                       # noqa: BLE001
            pass
    if await _regclass(conn, "agent_policy_versions"):
        r = await conn.fetchrow(
            "SELECT version, params FROM agent_policy_versions "
            " WHERE agent_id=$1 AND policy_key=$2 AND state='ACTIVE'",
            cls.agent, cls.policy_key)
        if r is not None:
            return {"params": _obj(r["params"]) or {}, "version": r["version"],
                    "source": "AGENT_POLICY_VERSIONS"}
    return {"params": default, "version": None, "source": "CODE_DEFAULT"}


def code_default_params(cls: ChangeClass) -> dict:
    if cls.name == "COLLECTION_PASS_LIMIT":
        try:
            from .. import bettor_pair_observations as PO        # noqa: PLC0415
            return {"candidates_per_pass": int(PO.CANDIDATES_PER_PASS)}
        except Exception:                                       # noqa: BLE001
            return {"candidates_per_pass": 3}
    if cls.name == "DEREK_ENTRY_THRESHOLD":
        try:
            from .. import bettor_external_shadow as ES          # noqa: PLC0415
            return {"min_net_edge_per_contract":
                    float(ES.MIN_NET_EDGE_PER_CONTRACT)}
        except Exception:                                       # noqa: BLE001
            return {"min_net_edge_per_contract": 0.01}
    if cls.name == "DEREK_ENTRY_POLICY_THRESHOLD":
        try:
            from . import derek_policy as DP                     # noqa: PLC0415
            return {"min_gross_edge_pp":
                    float(DP.DEFAULT_PARAMS["min_gross_edge_pp"])}
        except Exception:                                       # noqa: BLE001
            return {"min_gross_edge_pp": 0.05}
    if cls.name == "REPORT_THRESHOLD":
        return {"min_fixtures_for_statistic": 30,
                "collection_alert_passes": 3,
                "evidence_gap_alert_fraction": 0.25}
    return {}


def acceptance_met(cls: ChangeClass, cand: dict, current: dict) -> dict:
    """THE PRE-AUTHORIZED CLASS'S OWN RULE. Nothing else releases."""
    if not cls.pre_authorized:
        return {"ok": False, "refusal": R_NOT_PREAUTHORIZED}
    ev = cand.get("evaluation") or {}
    if ev.get("verdict") != V_PASS or ev.get("segment") not in (
            "HOLDOUT", "CHECK"):
        return {"ok": False, "refusal": R_ACCEPTANCE,
                "why": "the holdout evaluation did not PASS"}
    params = cand.get("params") or {}
    pc = check_params(cls, params)
    if not pc["ok"]:
        return dict(pc, ok=False)
    step = cls.acceptance.get("max_step")
    if step is not None:
        for k, v in params.items():
            cur = _num((current.get("params") or {}).get(k))
            if cur is not None and abs(float(v) - cur) > float(step):
                return {"ok": False, "refusal": R_ACCEPTANCE,
                        "why": "step %s -> %s exceeds the canary step %s"
                               % (cur, v, step)}
    return {"ok": True}


async def _ensure_baseline(conn, cls: ChangeClass, current: dict,
                           now: float) -> str:
    """THE PRIOR VERSION a rollback reactivates. When nothing is ACTIVE the
    code default is written as a version first, so rollback always has a
    named target."""
    r = await conn.fetchrow(
        "SELECT version FROM agent_policy_versions WHERE agent_id=$1 "
        " AND policy_key=$2 AND state='ACTIVE'", cls.agent, cls.policy_key)
    if r is not None:
        return r["version"]
    v = "code-default-%s" % _sha(current.get("params") or {})[:10]
    await _ensure_identity(conn, cls.agent)
    # THE CODE DEFAULT IS WHAT THE DEPLOYED BUILD ALREADY BINDS; it is
    # recorded as approved by that deployment (152 requires an ACTIVE row
    # to name its approver), never by an agent.
    await conn.execute(
        "INSERT INTO agent_policy_versions (agent_id, policy_key, version, "
        " params, state, created_by, approved_by, approved_at, created_at) "
        "VALUES ($1,$2,$3,$4::jsonb,'ACTIVE','CODE_DEFAULT',"
        " 'CODE_DEFAULT:DEPLOYED_BUILD',$5,$5) "
        "ON CONFLICT (agent_id, policy_key, version) DO UPDATE "
        " SET state='ACTIVE'", cls.agent, cls.policy_key, v,
        _j(current.get("params") or {}), _ts(now))
    return v


async def release_preauthorized(conn, candidate_id: str, *,
                                now: float) -> dict:
    """UNATTENDED PROMOTION OF A PRE-AUTHORIZED CLASS: the acceptance rule,
    then a new agent_policy_versions row CANDIDATE -> ACTIVE under a canary.
    The prior version is RETIRED, never deleted, and is what rollback
    reactivates."""
    c = await candidate(conn, candidate_id)
    if c is None:
        return {"ok": False, "refusal": R_NO_SUCH_CANDIDATE}
    cls = class_of(c["change_class"])
    if cls is None:
        return {"ok": False, "refusal": R_UNKNOWN_CLASS}
    if not await _regclass(conn, "agent_policy_versions"):
        return {"ok": False, "refusal": R_POLICY_TABLE}
    current = await current_policy(conn, cls)
    acc = acceptance_met(cls, c, current)
    if not acc["ok"]:
        return acc
    approver = PREAUTH_PREFIX + cls.name
    new_v = "imp-%s" % _sha(candidate_id)[:12]
    rel_id = "rel:%s" % candidate_id
    async with conn.transaction():
        prior = await _ensure_baseline(conn, cls, current, now)
        await conn.execute(
            "UPDATE improvement_candidates SET approved_by=$2, "
            " approved_at=$3, approval_statement=$4, state='APPROVED' "
            " WHERE candidate_id=$1 AND approved_by IS NULL",
            candidate_id, approver, _ts(now),
            "the pre-authorized class's acceptance rule: %s" % _j(
                cls.acceptance))
        await conn.execute(
            "INSERT INTO agent_policy_versions (agent_id, policy_key, "
            " version, params, state, created_by, approved_by, approved_at, "
            " created_at) VALUES ($1,$2,$3,$4::jsonb,'CANDIDATE',$5,$6,$7,$7) "
            "ON CONFLICT (agent_id, policy_key, version) DO NOTHING",
            cls.agent, cls.policy_key, new_v, _j(c["params"] or {}),
            c["proposed_by"], approver, _ts(now))
        await conn.execute(
            "UPDATE agent_policy_versions SET state='RETIRED' "
            " WHERE agent_id=$1 AND policy_key=$2 AND state='ACTIVE'",
            cls.agent, cls.policy_key)
        await conn.execute(
            "UPDATE agent_policy_versions SET state='ACTIVE' "
            " WHERE agent_id=$1 AND policy_key=$2 AND version=$3",
            cls.agent, cls.policy_key, new_v)
        await conn.execute(
            "INSERT INTO improvement_releases (release_id, candidate_id, "
            " agent_id, policy_key, from_version, to_version, state, "
            " acceptance, canary, released_by, released_at, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11,$11) "
            "ON CONFLICT (release_id) DO NOTHING",
            rel_id, candidate_id, cls.agent, cls.policy_key, prior, new_v,
            "CANARY" if cls.canary.get("min_samples") else "ACTIVE",
            _j(cls.acceptance), _j(cls.canary), approver, _ts(now))
        await conn.execute(
            "UPDATE improvement_candidates SET state=$2 WHERE candidate_id=$1",
            candidate_id,
            "CANARY" if cls.canary.get("min_samples") else "RELEASED")
        await _event(conn, candidate_id=candidate_id, task_id=c["task_id"],
                     kind="RELEASED_UNATTENDED", actor=approver,
                     detail={"from_version": prior, "to_version": new_v,
                             "release_id": rel_id}, now=now)
    await task_event(conn, c["task_id"], kind="RELEASED", actor=AUDREY,
                     detail={"candidate_id": candidate_id,
                             "release_id": rel_id, "from_version": prior,
                             "to_version": new_v, "canary": cls.canary},
                     status="RELEASED", now=now)
    return {"ok": True, "release_id": rel_id, "from_version": prior,
            "to_version": new_v}


async def rollback(conn, release_id: str, *, reason: str, actor: str,
                   now: float) -> dict:
    """REACTIVATE THE PRIOR VERSION. The released version is marked
    REJECTED (kept, never deleted); the release, the candidate and the task
    say ROLLED_BACK and why."""
    r = await conn.fetchrow(
        "SELECT * FROM improvement_releases WHERE release_id=$1 "
        " AND state IN ('CANARY','ACTIVE')", release_id)
    if r is None:
        return {"ok": False, "refusal": R_NO_SUCH_RELEASE}
    rel = dict(r)
    if not rel.get("from_version"):
        return {"ok": False, "refusal": R_NO_PRIOR_VERSION}
    async with conn.transaction():
        await conn.execute(
            "UPDATE agent_policy_versions SET state='REJECTED' "
            " WHERE agent_id=$1 AND policy_key=$2 AND version=$3",
            rel["agent_id"], rel["policy_key"], rel["to_version"])
        await conn.execute(
            "UPDATE agent_policy_versions SET state='ACTIVE' "
            " WHERE agent_id=$1 AND policy_key=$2 AND version=$3",
            rel["agent_id"], rel["policy_key"], rel["from_version"])
        await conn.execute(
            "UPDATE improvement_releases SET state='ROLLED_BACK', "
            " rolled_back_at=$2, rollback_reason=$3, updated_at=$2 "
            " WHERE release_id=$1", release_id, _ts(now), reason)
        await conn.execute(
            "UPDATE improvement_candidates SET state='ROLLED_BACK' "
            " WHERE candidate_id=$1", rel["candidate_id"])
        c = await candidate(conn, rel["candidate_id"])
        await _event(conn, candidate_id=rel["candidate_id"],
                     task_id=c["task_id"], kind="ROLLED_BACK", actor=actor,
                     detail={"release_id": release_id, "reason": reason,
                             "reactivated": rel["from_version"]}, now=now)
    await task_event(conn, c["task_id"], kind="ROLLED_BACK", actor=actor,
                     detail={"release_id": release_id, "reason": reason,
                             "reactivated": rel["from_version"]},
                     status="ROLLED_BACK", now=now)
    return {"ok": True, "release_id": release_id,
            "reactivated": rel["from_version"],
            "rejected": rel["to_version"]}


async def check_canaries(conn, *, now: float) -> dict:
    """A CANARY IS CONFIRMED OR ROLLED BACK ON EVIDENCE AFTER THE RELEASE.
    For the pass limit: the collector's passes since the release, their
    deadline stops against the class's canary limit."""
    out = {"checked": 0, "confirmed": [], "rolled_back": [], "waiting": []}
    rows = await conn.fetch(
        "SELECT * FROM improvement_releases WHERE state='CANARY' "
        " ORDER BY released_at")
    for r in rows:
        rel = dict(r)
        out["checked"] += 1
        cand = await candidate(conn, rel["candidate_id"])
        cls = class_of(cand["change_class"]) if cand else None
        canary = _obj(rel["canary"]) or {}
        if cls is None or cls.name != "COLLECTION_PASS_LIMIT":
            out["waiting"].append({"release_id": rel["release_id"],
                                   "why": "no canary reader for the class"})
            continue
        samples = await conn.fetch(
            "SELECT stopped_for_deadline FROM audrey_collection_samples "
            " WHERE pass_at > $1 AND pass_at <= $2 ORDER BY pass_at",
            rel["released_at"], _ts(now))
        n = len(samples)
        stops = sum(1 for s in samples if s["stopped_for_deadline"])
        frac = (stops / n) if n else None
        metrics = {"samples": n, "deadline_stops": stops,
                   "deadline_stop_fraction": frac}
        early = canary.get("early_rollback_stops")
        if (early and stops >= int(early)) or (
                n >= int(canary.get("min_samples") or 0) and n
                and frac > float(canary.get("max_deadline_stop_fraction",
                                            1.0))):
            await record_trial(
                conn, candidate_id=rel["candidate_id"],
                task_id=cand["task_id"], segment="CANARY",
                variant=cand["params"], metrics=metrics, verdict=V_HARM,
                evidence_category=ACTUAL, now=now)
            got = await rollback(conn, rel["release_id"],
                                 reason="CANARY_HARM: %s" % _j(metrics),
                                 actor=EVALUATOR_REPLAY, now=now)
            out["rolled_back"].append(dict(got, metrics=metrics))
        elif n >= int(canary.get("min_samples") or 0):
            await record_trial(
                conn, candidate_id=rel["candidate_id"],
                task_id=cand["task_id"], segment="CANARY",
                variant=cand["params"], metrics=metrics, verdict=V_PASS,
                evidence_category=ACTUAL, now=now)
            await conn.execute(
                "UPDATE improvement_releases SET state='ACTIVE', "
                " confirmed_at=$2, updated_at=$2 WHERE release_id=$1",
                rel["release_id"], _ts(now))
            await conn.execute(
                "UPDATE improvement_candidates SET state='RELEASED' "
                " WHERE candidate_id=$1", rel["candidate_id"])
            await _event(conn, candidate_id=rel["candidate_id"],
                         task_id=cand["task_id"], kind="CANARY_CONFIRMED",
                         actor=EVALUATOR_REPLAY, detail=metrics, now=now)
            out["confirmed"].append({"release_id": rel["release_id"],
                                     "metrics": metrics})
        else:
            out["waiting"].append({"release_id": rel["release_id"],
                                   "metrics": metrics})
    return out


# ═════════════════════════════════════════════════════════════════════
# 6 · APPROVAL (the API's write route)
# ═════════════════════════════════════════════════════════════════════

async def approve(conn, candidate_id: str, *, approver: str,
                  credential_role: str, statement: str = "",
                  now: float) -> dict:
    """A NAMED PERSON APPROVES AN APPROVAL_READY CANDIDATE. Refused: an
    unknown or protected class, a protected key, anything but a PASS, the
    proposer or evaluator approving, and any agent or evaluator identity.
    Approval does NOT deploy: a policy candidate is written as a CANDIDATE
    policy version for a person to activate; code stays on its branch."""
    c = await candidate(conn, candidate_id)
    if c is None:
        return {"ok": False, "refusal": R_NO_SUCH_CANDIDATE}
    chk = check_class(c["change_class"])
    if not chk["ok"]:
        return dict(chk, candidate_id=candidate_id)
    hit = protected_touched(c.get("touched_keys") or [])
    if hit:
        return {"ok": False, "refusal": R_PROTECTED_KEY, "protected": hit}
    who = str(approver or "").strip()
    if not who:
        return {"ok": False, "refusal": R_NO_APPROVER}
    if who.upper() in AGENT_IDS or who.upper().startswith(
            ("EVALUATOR:", PREAUTH_PREFIX)) or ":" in who and \
            who.split(":", 1)[0].upper() in AGENT_IDS:
        return {"ok": False, "refusal": R_AGENT_APPROVER, "approver": who}
    if who in (c.get("proposed_by"), c.get("evaluated_by")) or \
            who.lower() in {str(c.get("proposed_by") or "").lower(),
                            str(c.get("evaluated_by") or "").lower()}:
        return {"ok": False, "refusal": R_SELF_APPROVAL, "approver": who}
    ev = c.get("evaluation") or {}
    if ev.get("verdict") != V_PASS:
        return {"ok": False, "refusal": R_NOT_PASSED,
                "verdict": ev.get("verdict"), "state": c.get("state")}
    if c.get("state") != "APPROVAL_READY":
        return {"ok": False, "refusal": R_NOT_APPROVAL_READY,
                "state": c.get("state")}
    async with conn.transaction():
        await conn.execute(
            "UPDATE improvement_candidates SET approved_by=$2, "
            " approved_at=$3, approval_statement=$4, state='APPROVED' "
            " WHERE candidate_id=$1 AND approved_by IS NULL", candidate_id,
            who, _ts(now), (statement or "")[:2000])
        await _event(conn, candidate_id=candidate_id, task_id=c["task_id"],
                     kind="APPROVED", actor=who,
                     detail={"credential_role": credential_role,
                             "statement": (statement or "")[:500]}, now=now)
        wrote_policy = None
        cls = chk["cls"]
        if cls.kind in (K_POLICY, K_REPORT) and cls.policy_key and \
                await _regclass(conn, "agent_policy_versions"):
            wrote_policy = "imp-%s" % _sha(candidate_id)[:12]
            await _ensure_identity(conn, cls.agent)
            await conn.execute(
                "INSERT INTO agent_policy_versions (agent_id, policy_key, "
                " version, params, state, created_by, approved_by, "
                " approved_at, created_at) VALUES ($1,$2,$3,$4::jsonb,"
                " 'CANDIDATE',$5,$6,$7,$7) ON CONFLICT DO NOTHING",
                cls.agent, cls.policy_key, wrote_policy,
                _j(c.get("params") or {}), c["proposed_by"], who, _ts(now))
    await task_event(conn, c["task_id"], kind="APPROVED", actor=who,
                     detail={"candidate_id": candidate_id,
                             "credential_role": credential_role,
                             "policy_version_written": wrote_policy,
                             "deployed": False},
                     status="APPROVED", now=now)
    return {"ok": True, "candidate_id": candidate_id, "approved_by": who,
            "credential_role": credential_role,
            "policy_version_written_as_candidate": wrote_policy,
            "deployed": False,
            "deployment": ("NOT_BY_AN_AGENT: a policy CANDIDATE row awaits a "
                           "person's activation; a code change stays on "
                           "its improve/<task_id> branch")}


# ═════════════════════════════════════════════════════════════════════
# 7 · TASK EVALUATORS
# ═════════════════════════════════════════════════════════════════════

async def _pass_samples(conn, start: float, end: float) -> list:
    return [dict(r) for r in await conn.fetch(
        "SELECT sample_id, extract(epoch FROM pass_at)::float8 AS pass_at, "
        " offered, attempted, configured_per_pass, limit_per_pass_left, "
        " skipped_recently_refused, stopped_for_deadline, "
        " elapsed_s::float8 AS elapsed_s, budget_s::float8 AS budget_s "
        " FROM audrey_collection_samples WHERE pass_at > $1 AND pass_at <= $2"
        " ORDER BY pass_at, sample_id", _ts(start), _ts(end))]


async def _attempt_p90(conn, start: float, end: float) -> float | None:
    if not await _regclass(conn, "bettor_pair_observation_attempts"):
        return None
    xs = [float(r["s"]) for r in await conn.fetch(
        "SELECT extract(epoch FROM finished_at - attempted_at)::float8 AS s "
        "  FROM bettor_pair_observation_attempts "
        " WHERE attempted_at > $1 AND attempted_at <= $2", _ts(start),
        _ts(end))]
    return None if not xs else round(percentile(xs, 0.9), 6)


def _boundaries(spec: dict, now: float, *, holdout_days: float) -> tuple:
    eb = _num(spec.get("evaluation_boundary")) or float(now)
    tb = _num(spec.get("training_boundary")) or (eb - holdout_days * 86400.0)
    start = _num(spec.get("training_start")) or (tb - 30 * 86400.0)
    return start, tb, eb


async def evaluate_pass_limit_task(conn, task: dict, *, now: float) -> dict:
    """COLLECTION_PASS_LIMIT BY REPLAY OVER RECORDED PASSES.

    Variants are the integers above the current limit up to the canary
    step and the class bound. ONE variant is selected on the TRAINING
    passes (the largest whose projected harm is within the class limits);
    only that variant is tried on the HOLDOUT passes, once, under the
    holdout's trial budget."""
    cls = CHANGE_CLASSES["COLLECTION_PASS_LIMIT"]
    spec = task.get("spec") or {}
    tid = task["task_id"]
    start, tb, eb = _boundaries(spec, now, holdout_days=float(
        spec.get("holdout_days") or 2))
    current = await current_policy(conn, cls)
    cur = int(current["params"].get("candidates_per_pass") or 3)
    lo, hi, _ = cls.bounds["candidates_per_pass"]
    step = int(cls.acceptance.get("max_step") or 1)
    variants = [int(v) for v in (spec.get("variants") or
                                 range(cur + 1, cur + step + 1))
                if lo <= int(v) <= hi and int(v) != cur]
    train = await _pass_samples(conn, start, tb)
    hold = await _pass_samples(conn, tb, eb)
    p90_train = await _attempt_p90(conn, start, tb)
    min_passes = int(cls.success_metrics.get("min_passes") or 1)
    if not variants:
        await task_event(conn, tid, kind="NO_VARIANT", actor=AUDREY,
                         detail={"current": cur, "bounds": [lo, hi]},
                         status="CLOSED_NO_CHANGE", now=now)
        return {"task_id": tid, "verdict": "NO_VARIANT_WITHIN_BOUNDS"}
    if len(train) < min_passes:
        await task_event(
            conn, tid, kind="WAITING_FOR_EVIDENCE", actor=AUDREY,
            detail={"training_passes": len(train), "needed": min_passes},
            status="WAITING", now=now)
        return {"task_id": tid, "verdict": V_INSUFFICIENT,
                "training_passes": len(train)}
    await task_event(conn, tid, kind="EVALUATING", actor=EVALUATOR_REPLAY,
                     detail={"variants": variants, "current": cur},
                     status="EVALUATING", now=now)
    hypothesis = (spec.get("hypothesis") or
                  "candidates are left unattempted for LIMIT_PER_PASS; a "
                  "higher pass limit attempts more within the pass budget")
    selected, trials = None, []
    base_metrics = replay_pass_limit(train, limit=cur, per_attempt_s=p90_train)
    for v in variants:
        prop = await propose(
            conn, task_id=tid, change_class=cls.name, proposed_by=cls.agent,
            hypothesis=hypothesis, evidence=spec.get("evidence") or {},
            affected_behavior=cls.description,
            params={"candidates_per_pass": v},
            training_boundary={"start": start, "end": tb},
            evaluation_boundary={"start": tb, "end": eb}, now=now)
        if not prop.get("ok"):
            trials.append(prop)
            continue
        m = replay_pass_limit(train, limit=v, per_attempt_s=p90_train)
        m["baseline"] = base_metrics
        j = judge(m, success=cls.success_metrics, harm=cls.harm_metrics,
                  min_key="min_passes", n_key="passes_evaluated")
        ok = j["verdict"] == V_PASS
        trials.append({"candidate_id": prop["candidate_id"], "limit": v,
                       "training": m, "judgement": j})
        if ok:
            selected = (v, prop["candidate_id"])
    # THE LARGEST PASSING VARIANT IS SELECTED; every other variant is
    # recorded and rejected as NOT_SELECTED, its trial kept.
    for t in trials:
        if "candidate_id" not in t:
            continue
        is_sel = selected is not None and t["candidate_id"] == selected[1]
        await record_trial(
            conn, candidate_id=t["candidate_id"], task_id=tid,
            segment="TRAINING", variant={"candidates_per_pass": t["limit"]},
            metrics=dict(t["training"], judgement=t["judgement"]),
            verdict=V_SELECTED if is_sel else V_NOT_SELECTED,
            evidence_category=SIMULATED, training_boundary=start,
            evaluation_boundary=tb, now=now)
        if not is_sel:
            await record_evaluation(
                conn, candidate_id=t["candidate_id"], evaluated_by=
                EVALUATOR_REPLAY, state="REJECTED", now=now,
                evaluation={"verdict": t["judgement"]["verdict"]
                            if t["judgement"]["verdict"] != V_PASS
                            else V_NOT_SELECTED, "segment": "TRAINING",
                            "judgement": t["judgement"],
                            "metrics": t["training"]})
    if selected is None:
        await task_event(conn, tid, kind="REJECTED", actor=EVALUATOR_REPLAY,
                         detail={"why": "no variant passed on training",
                                 "trials": len(trials)},
                         status="REJECTED", now=now)
        return {"task_id": tid, "verdict": "NO_VARIANT_PASSED_TRAINING",
                "trials": trials}
    v, cid = selected
    hid = "collection_passes:%s..%s" % (
        _ts(tb).date().isoformat(), _ts(eb).date().isoformat())
    await ensure_holdout(conn, holdout_id=hid, description=(
        "pair-collector passes after the training boundary"),
        rule={"kind": "TIME_AFTER_TRAINING_BOUNDARY", "start": tb,
              "end": eb})
    p90_hold = await _attempt_p90(conn, start, tb)   # no holdout leakage
    hm = replay_pass_limit(hold, limit=v, per_attempt_s=p90_hold)
    hm["baseline"] = replay_pass_limit(hold, limit=cur,
                                       per_attempt_s=p90_hold)
    j = judge(hm, success=cls.success_metrics, harm=cls.harm_metrics,
              min_key="min_passes", n_key="passes_evaluated")
    tr = await record_trial(
        conn, candidate_id=cid, task_id=tid, segment="HOLDOUT",
        holdout_id=hid, variant={"candidates_per_pass": v},
        metrics=dict(hm, judgement=j), verdict=j["verdict"],
        evidence_category=SIMULATED, training_boundary=tb,
        evaluation_boundary=eb, now=now)
    if not tr.get("ok"):
        await task_event(conn, tid, kind="HOLDOUT_REFUSED",
                         actor=EVALUATOR_REPLAY, detail=tr, status="WAITING",
                         now=now)
        return {"task_id": tid, "verdict": tr.get("refusal"), "trial": tr}
    passed = j["verdict"] == V_PASS
    c = await record_evaluation(
        conn, candidate_id=cid, evaluated_by=EVALUATOR_REPLAY, now=now,
        state="APPROVAL_READY" if passed else "REJECTED",
        evaluation={"verdict": j["verdict"], "segment": "HOLDOUT",
                    "holdout_id": hid, "judgement": j, "metrics": hm,
                    "evidence_category": SIMULATED})
    if not passed:
        await task_event(conn, tid, kind="REJECTED", actor=EVALUATOR_REPLAY,
                         detail={"candidate_id": cid, "judgement": j},
                         status="REJECTED", now=now)
        return {"task_id": tid, "verdict": j["verdict"], "candidate_id": cid}
    rel = await release_preauthorized(conn, cid, now=now)
    if not rel.get("ok"):
        await task_event(conn, tid, kind="APPROVAL_READY",
                         actor=EVALUATOR_REPLAY,
                         detail={"candidate_id": cid, "release": rel},
                         status="APPROVAL_READY", now=now)
    return {"task_id": tid, "verdict": V_PASS, "candidate_id": cid,
            "release": rel, "candidate_state": (await candidate(
                conn, cid))["state"], "evaluated": c is not None}


async def derek_rows(conn, *, start: float, end: float) -> list:
    """Recorded ENTRY_DECISION valuations with their decision-time edge
    and cost and the outcome and the instant it became known."""
    if not await _regclass(conn, "external_valuations"):
        return []
    return [{"id": r["id"], "fixture": r["fixture"],
             "decided_at": float(r["decided_at"]),
             "outcome_at": _num(r["outcome_at"]),
             "outcome": r["outcome"], "edge": _num(r["edge"]),
             "cost": _num(r["cost"]), "refusals": list(r["refusals"] or []),
             "probability": _num(r["probability"]),
             "price": _num(r["executable_price"]),
             "decision": r["decision"]}
            for r in await conn.fetch(
        "SELECT id, coalesce(event_key, us_market_slug, condition_id) "
        "         AS fixture, extract(epoch FROM decided_at)::float8 "
        "         AS decided_at, extract(epoch FROM outcome_at)::float8 "
        "         AS outcome_at, outcome, "
        "       estimated_edge_per_contract AS edge, "
        "       cost_per_contract AS cost, refusals, decision, "
        "       probability, executable_price "
        "  FROM external_valuations WHERE record_purpose='ENTRY_DECISION' "
        "   AND decided_at > $1 AND decided_at <= $2 "
        " ORDER BY decided_at, id", _ts(start), _ts(end))]


DEREK_HOLDOUT_SALT = "derek-entry-holdout-v1"
DEREK_HOLDOUT_PERCENT = 30


async def evaluate_derek_threshold_task(
        conn, task: dict, *, now: float,
        class_name: str = "DEREK_ENTRY_THRESHOLD") -> dict:
    """DEREK_ENTRY_THRESHOLD BY REPLAY OVER external_valuations.

    Fixture-level holdout (a fixed salt: the holdout is the same fixtures
    whatever the window, so its budget cannot be escaped by moving a
    boundary); training and holdout cut by outcome availability. The
    variant is selected on training, tried once on the holdout against
    the current threshold, judged by the class's rule. Not pre-authorized:
    a PASS stops at APPROVAL_READY."""
    cls = CHANGE_CLASSES[class_name]
    pname = next(iter(cls.bounds))
    basis = BASIS_GROSS if pname == "min_gross_edge_pp" else BASIS_NET
    spec = task.get("spec") or {}
    tid = task["task_id"]
    start, tb, eb = _boundaries(spec, now, holdout_days=float(
        spec.get("holdout_days") or 14))
    current = await current_policy(conn, cls)
    fallback = code_default_params(cls).get(pname)
    cur = float(current["params"].get(pname) if current["params"].get(
        pname) is not None else fallback)
    lo, hi, _ = cls.bounds[pname]
    step = 0.01 if basis == BASIS_GROSS else 0.005
    variants = sorted({round(float(v), 6) for v in (
        spec.get("variants") or (cur - step, cur + step, cur + 2 * step))
        if lo <= float(v) <= hi and abs(float(v) - cur) > 1e-12})
    rows = await derek_rows(conn, start=start, end=eb)
    sp = split_rows(rows, training_boundary=tb, evaluation_boundary=eb,
                    salt=DEREK_HOLDOUT_SALT, percent=DEREK_HOLDOUT_PERCENT)
    min_fx = int(cls.success_metrics.get("min_fixtures") or 1)
    base_train = replay_derek_threshold(sp["training"], threshold=cur, basis=basis)
    if not variants:
        await task_event(conn, tid, kind="NO_VARIANT", actor=AUDREY,
                         detail={"current": cur}, status="CLOSED_NO_CHANGE",
                         now=now)
        return {"task_id": tid, "verdict": "NO_VARIANT_WITHIN_BOUNDS"}
    await task_event(conn, tid, kind="EVALUATING", actor=EVALUATOR_REPLAY,
                     detail={"variants": variants, "current": cur,
                             "excluded": sp["excluded"]},
                     status="EVALUATING", now=now)
    scored = []
    for v in variants:
        prop = await propose(
            conn, task_id=tid, change_class=cls.name, proposed_by=cls.agent,
            hypothesis=spec.get("hypothesis") or (
                "Derek's entry threshold is miscalibrated against settled "
                "outcomes"), evidence=spec.get("evidence") or {},
            affected_behavior=cls.description,
            params={pname: v},
            training_boundary={"start": start, "end": tb,
                               "outcomes_known_by": tb},
            evaluation_boundary={"start": tb, "end": eb,
                                 "outcomes_known_by": eb,
                                 "holdout_salt": DEREK_HOLDOUT_SALT,
                                 "holdout_percent": DEREK_HOLDOUT_PERCENT},
            now=now)
        if not prop.get("ok"):
            scored.append({"limit": v, "refused": prop})
            continue
        m = replay_derek_threshold(sp["training"], threshold=v, basis=basis)
        m["baseline"] = base_train
        m["fixture_mean_pnl_delta_per_contract"] = (
            None if m["fixture_mean_pnl_per_contract"] is None
            or base_train["fixture_mean_pnl_per_contract"] is None
            else round(m["fixture_mean_pnl_per_contract"]
                       - base_train["fixture_mean_pnl_per_contract"], 6))
        scored.append({"candidate_id": prop["candidate_id"], "threshold": v,
                       "training": m})
    ok = [s for s in scored if "candidate_id" in s
          and s["training"]["fixtures"] >= min_fx
          and s["training"]["fixture_mean_pnl_per_contract"] is not None]
    best = max(ok, key=lambda s: (s["training"][
        "fixture_mean_pnl_per_contract"], -abs(s["threshold"] - cur)),
        default=None)
    for s in scored:
        if "candidate_id" not in s:
            continue
        is_sel = best is not None and s["candidate_id"] == best[
            "candidate_id"]
        await record_trial(
            conn, candidate_id=s["candidate_id"], task_id=tid,
            segment="TRAINING",
            variant={pname: s["threshold"]},
            metrics=dict(s["training"], excluded=sp["excluded"],
                         training_fixtures=len(sp["training_fixtures"])),
            verdict=V_SELECTED if is_sel else V_NOT_SELECTED,
            evidence_category=KNOWN_SETTLEMENT, training_boundary=start,
            evaluation_boundary=tb, now=now)
        if not is_sel:
            await record_evaluation(
                conn, candidate_id=s["candidate_id"],
                evaluated_by=EVALUATOR_REPLAY, state="REJECTED", now=now,
                evaluation={"verdict": (V_NOT_SELECTED if best else
                                        V_INSUFFICIENT),
                            "segment": "TRAINING",
                            "metrics": s["training"]})
    if best is None:
        await task_event(conn, tid, kind="REJECTED", actor=EVALUATOR_REPLAY,
                         detail={"why": "no variant had enough training "
                                        "fixtures", "min_fixtures": min_fx,
                                 "training_fixtures":
                                     len(sp["training_fixtures"])},
                         status="REJECTED", now=now)
        return {"task_id": tid, "verdict": V_INSUFFICIENT, "split": {
            k: v for k, v in sp.items() if k in ("excluded",)}}
    hid = "derek_threshold:%s:%dpct" % (DEREK_HOLDOUT_SALT,
                                        DEREK_HOLDOUT_PERCENT)
    await ensure_holdout(conn, holdout_id=hid, description=(
        "fixture-level holdout of Derek's recorded valuations"),
        rule={"kind": "FIXTURE_HASH", "salt": DEREK_HOLDOUT_SALT,
              "percent": DEREK_HOLDOUT_PERCENT})
    hm = replay_derek_threshold(sp["holdout"], threshold=best["threshold"], basis=basis)
    hb = replay_derek_threshold(sp["holdout"], threshold=cur, basis=basis)
    hm["baseline"] = hb
    hm["fixture_mean_pnl_delta_per_contract"] = (
        None if hm["fixture_mean_pnl_per_contract"] is None
        or hb["fixture_mean_pnl_per_contract"] is None
        else round(hm["fixture_mean_pnl_per_contract"]
                   - hb["fixture_mean_pnl_per_contract"], 6))
    hm["min_fixtures"] = min_fx
    hm["excluded"] = sp["excluded"]
    hm["training_fixture_count"] = len(sp["training_fixtures"])
    hm["holdout_fixture_count"] = len(sp["holdout_fixtures"])
    hm["fixtures_in_both"] = len(set(sp["training_fixtures"])
                                 & set(sp["holdout_fixtures"]))
    j = judge(hm, success=cls.success_metrics, harm=cls.harm_metrics,
              min_key="min_fixtures", n_key="fixtures")
    tr = await record_trial(
        conn, candidate_id=best["candidate_id"], task_id=tid,
        segment="HOLDOUT", holdout_id=hid,
        variant={pname: best["threshold"]},
        metrics=dict(hm, judgement=j), verdict=j["verdict"],
        evidence_category=KNOWN_SETTLEMENT, training_boundary=tb,
        evaluation_boundary=eb, now=now)
    if not tr.get("ok"):
        await task_event(conn, tid, kind="HOLDOUT_REFUSED",
                         actor=EVALUATOR_REPLAY, detail=tr, status="WAITING",
                         now=now)
        return {"task_id": tid, "verdict": tr.get("refusal"), "trial": tr}
    passed = j["verdict"] == V_PASS
    await record_evaluation(
        conn, candidate_id=best["candidate_id"],
        evaluated_by=EVALUATOR_REPLAY, now=now,
        state="APPROVAL_READY" if passed else "REJECTED",
        evaluation={"verdict": j["verdict"], "segment": "HOLDOUT",
                    "holdout_id": hid, "judgement": j, "metrics": hm,
                    "evidence_category": KNOWN_SETTLEMENT,
                    "could_have_filled": "UNPROVEN"})
    await task_event(
        conn, tid, kind="APPROVAL_READY" if passed else "REJECTED",
        actor=EVALUATOR_REPLAY, detail={"candidate_id": best["candidate_id"],
                                        "judgement": j},
        status="APPROVAL_READY" if passed else "REJECTED", now=now)
    return {"task_id": tid, "verdict": j["verdict"],
            "candidate_id": best["candidate_id"], "holdout": hm}


async def advance_code_task(conn, task: dict, *, now: float) -> dict:
    """A CODE (or unreplayable) TASK CANNOT RUN IN THE SERVING PROCESS.
    Without a sandbox artifact it WAITS on NEEDS_SANDBOX; with one it is
    reviewed from the sandbox's own recorded report (tests and harm) by an
    evaluator that is not the proposer, and stops at APPROVAL_READY or
    REJECTED. Never released unattended."""
    tid = task["task_id"]
    rows = [_row(r) for r in await conn.fetch(
        "SELECT * FROM improvement_candidates WHERE task_id=$1 "
        " AND artifact_ref IS NOT NULL ORDER BY created_at", tid)]
    pending = [c for c in rows if c.get("evaluation") is None]
    if not rows:
        if task.get("status") != "WAITING":
            await task_event(
                conn, tid, kind="WAITING", actor=AUDREY,
                detail={"waiting_on": NEEDS_SANDBOX,
                        "run": ("python backend/tools/improvement_sandbox.py "
                                "--task-id %s --dsn $DATABASE_URL "
                                "--base <commit>" % tid)},
                status="WAITING", now=now)
        return {"task_id": tid, "state": "WAITING", "waiting_on":
                NEEDS_SANDBOX}
    out = []
    for c in pending:
        tr = c.get("test_results") or {}
        rep = (c.get("evidence") or {}).get("sandbox_report") or {}
        harm = rep.get("harm") or {}
        breaches = [k for k, v in harm.items()
                    if isinstance(v, dict) and v.get("breached")]
        if tr.get("passed") is True and not breaches and c.get("diff"):
            verdict = V_PASS
        elif breaches:
            verdict = V_HARM
        else:
            verdict = V_NO_GAIN
        await record_trial(
            conn, candidate_id=c["candidate_id"], task_id=tid,
            segment="SANDBOX_TESTS", variant={"artifact_ref":
                                              c["artifact_ref"]},
            metrics={"tests": tr, "harm_breaches": breaches},
            verdict=verdict, evidence_category=(
                SIMULATED if rep.get("replay") else UNEVALUABLE),
            evaluated_by=EVALUATOR_SANDBOX_REVIEW, now=now)
        await record_evaluation(
            conn, candidate_id=c["candidate_id"],
            evaluated_by=EVALUATOR_SANDBOX_REVIEW, now=now,
            state="APPROVAL_READY" if verdict == V_PASS else "REJECTED",
            evaluation={"verdict": verdict, "segment": "SANDBOX_TESTS",
                        "tests_passed": tr.get("passed"),
                        "harm_breaches": breaches,
                        "artifact_ref": c["artifact_ref"]})
        out.append({"candidate_id": c["candidate_id"], "verdict": verdict})
    if out:
        best = V_PASS if any(o["verdict"] == V_PASS for o in out) else None
        await task_event(
            conn, tid, kind="SANDBOX_CANDIDATE_REVIEWED",
            actor=EVALUATOR_SANDBOX_REVIEW, detail={"candidates": out},
            status="APPROVAL_READY" if best else "REJECTED", now=now)
    return {"task_id": tid, "reviewed": out}


# ═════════════════════════════════════════════════════════════════════
# 8 · THE HOOK
# ═════════════════════════════════════════════════════════════════════

MAX_TASKS_PER_RUN = 10
ADVANCEABLE = ("OPEN", "IN_PROGRESS", "WAITING", "CANDIDATE_READY")
RETRY_WAITING_S = 6 * 3600


async def _waiting_too_recently(conn, task: dict, now: float) -> bool:
    if task.get("status") != "WAITING":
        return False
    evs = await task_events(conn, task["task_id"], limit=1)
    if not evs:
        return False
    at = _epoch(evs[0].get("at"))
    detail = evs[0].get("detail") or {}
    if detail.get("waiting_on") == NEEDS_SANDBOX:
        return False               # re-checked every run: cheap
    return at is not None and now - at < RETRY_WAITING_S


async def run_due(conn, *, now: float) -> dict:
    """ADVANCE THE IMPROVEMENT TASKS THAT CAN RUN IN-PROCESS. Never raises;
    bounded to MAX_TASKS_PER_RUN tasks per call."""
    out: dict[str, Any] = {"version": VERSION, "at": float(now),
                           "advanced": [], "errors": []}
    try:
        if not await has_schema(conn):
            return dict(out, ok=False, refusal=R_SCHEMA)
        if not await tasks_available(conn):
            out["tasks"] = {"status": "UNAVAILABLE",
                            "why": R_TASKS_UNAVAILABLE}
            tasks = []
        else:
            tasks = await read_tasks(conn, kind=TASK_KIND,
                                     statuses=ADVANCEABLE)
        n = 0
        for t in tasks:
            if n >= MAX_TASKS_PER_RUN:
                out["deferred"] = len(tasks) - n
                break
            if await _waiting_too_recently(conn, t, float(now)):
                continue
            n += 1
            spec = t.get("spec") or {}
            chk = check_class(spec.get("change_class"))
            try:
                if not chk["ok"]:
                    await task_event(conn, t["task_id"], kind="REFUSED",
                                     actor=AUDREY, detail=chk,
                                     status="CANCELLED", now=now)
                    out["advanced"].append(dict(chk, task_id=t["task_id"]))
                    continue
                cls: ChangeClass = chk["cls"]
                if cls.evaluator == "collection_pass_limit_replay":
                    got = await evaluate_pass_limit_task(conn, t, now=now)
                elif cls.evaluator in ("derek_threshold_replay",
                                       "derek_gross_edge_replay"):
                    got = await evaluate_derek_threshold_task(
                        conn, t, now=now, class_name=cls.name)
                else:
                    got = await advance_code_task(conn, t, now=now)
                out["advanced"].append(got)
            except Exception as exc:                            # noqa: BLE001
                out["errors"].append({"task_id": t["task_id"],
                                      "error": "%s: %s" % (
                                          type(exc).__name__,
                                          str(exc)[:200])})
        try:
            out["canaries"] = await check_canaries(conn, now=float(now))
        except Exception as exc:                                # noqa: BLE001
            out["canaries"] = {"error": type(exc).__name__}
        return dict(out, ok=not out["errors"])
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal="IMPROVEMENT_RUN_RAISED",
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]))


# ═════════════════════════════════════════════════════════════════════
# 9 · READERS (the workspace)
# ═════════════════════════════════════════════════════════════════════

async def candidates(conn, *, limit: int = 50) -> list:
    if not await has_schema(conn):
        return []
    return [_row(r) for r in await conn.fetch(
        "SELECT candidate_id, task_id, assigned_agent, change_class, "
        " change_kind, hypothesis, params, artifact_ref, state, proposed_by, "
        " evaluated_by, approved_by, release_scope, evaluation->>'verdict' "
        " AS verdict, created_at, updated_at FROM improvement_candidates "
        " ORDER BY updated_at DESC, candidate_id LIMIT $1", int(limit))]


async def trials(conn, *, candidate_id: str | None = None,
                 limit: int = 100) -> list:
    if not await has_schema(conn):
        return []
    if candidate_id:
        rows = await conn.fetch(
            "SELECT * FROM improvement_trials WHERE candidate_id=$1 "
            " ORDER BY created_at, CASE segment WHEN 'TRAINING' THEN 0 "
            "  WHEN 'HOLDOUT' THEN 1 WHEN 'SANDBOX_TESTS' THEN 2 ELSE 3 END,"
            " trial_id", candidate_id)
    else:
        rows = await conn.fetch(
            "SELECT * FROM improvement_trials ORDER BY created_at DESC, "
            " trial_id LIMIT $1", int(limit))
    return [_row(r) for r in rows]


async def releases(conn, *, limit: int = 50) -> list:
    if not await has_schema(conn):
        return []
    return [_row(r) for r in await conn.fetch(
        "SELECT * FROM improvement_releases ORDER BY released_at DESC "
        " LIMIT $1", int(limit))]


async def holdouts(conn) -> list:
    if not await has_schema(conn):
        return []
    return [_row(r) for r in await conn.fetch(
        "SELECT h.*, (SELECT count(*) FROM improvement_trials t "
        "  WHERE t.holdout_id=h.holdout_id) AS used "
        "  FROM improvement_holdouts h ORDER BY created_at DESC")]


async def candidate_detail(conn, candidate_id: str) -> dict | None:
    c = await candidate(conn, candidate_id)
    if c is None:
        return None
    c["trials"] = await trials(conn, candidate_id=candidate_id)
    c["events"] = [_row(r) for r in await conn.fetch(
        "SELECT * FROM improvement_events WHERE candidate_id=$1 "
        " ORDER BY event_id", candidate_id)]
    c["releases"] = [_row(r) for r in await conn.fetch(
        "SELECT * FROM improvement_releases WHERE candidate_id=$1",
        candidate_id)]
    return c
