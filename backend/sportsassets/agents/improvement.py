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

from . import xavier_replay as _XR

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
#: the Derek threshold replay's own version, bound into every evaluation
DEREK_REPLAY_VERSION = "DEREK_THRESHOLD_REPLAY_V2"
#: the threshold's unit, stated wherever a value is recorded
THRESHOLD_UNITS = ("PROBABILITY_DIFFERENCE_ON_A_0_TO_1_DOLLAR_CONTRACT "
                   "(0.05 == 5 percentage points; never 5)")
#: what a retrospective replay can and cannot establish
QUAL_RETROSPECTIVE = "RETROSPECTIVE_REPLAY_ON_KNOWN_SETTLEMENT"
ECON_PENDING = "PENDING_PROSPECTIVE_QUALIFICATION_WITH_EXECUTION_EVIDENCE"
ECON_QUALIFIED = "QUALIFIED"
R_NO_ARTIFACT = "THE_CANDIDATE_HAS_NO_COMMITTED_ARTIFACT"
R_ARTIFACT_TESTS = "THE_ARTIFACT_TESTS_DID_NOT_PASS"
R_NO_BINDING = "THE_EVALUATION_RECORDS_NO_EVIDENCE_BINDING"
R_BASIS_CHANGED = "THE_APPROVAL_BASIS_CHANGED_SINCE_EVALUATION"
R_ECON_PENDING = "ECONOMIC_QUALIFICATION_IS_PENDING"
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
        # SUCCESS IS AN IMPROVED NET RESULT PER ELIGIBLE FIXTURE (paired
        # against the current threshold on the same rows) WITHOUT A DEEPER
        # DRAWDOWN -- not fewer trades, not a higher mean on fewer trades.
        # An identical selection gains 0 and fails; no trades is
        # INSUFFICIENT_EVIDENCE, never a success.
        success_metrics={"delta_net_per_eligible_fixture": {">": 0.0},
                         "delta_max_drawdown": {"<=": 0.0},
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
        description=("Derek's binding entry policy threshold "
                     "(DEREK_ENTRY_POLICY_V2): the minimum GROSS probability "
                     "edge in probability points (blended probability = "
                     "(internal + Pinnacle) / 2, minus the executable price), "
                     "agents.derek_policy MIN_GROSS_EDGE_PROBABILITY (DEFAULT_PARAMS); "
                     "units: " + THRESHOLD_UNITS),
        policy_key="DEREK_ENTRY_POLICY",
        bounds={"min_gross_edge_pp": (0.0, 0.20, False)},
        pre_authorized=False, evaluator="derek_gross_edge_replay",
        # SUCCESS IS AN IMPROVED NET RESULT PER ELIGIBLE FIXTURE (paired
        # against the current threshold on the same rows) WITHOUT A DEEPER
        # DRAWDOWN -- not fewer trades, not a higher mean on fewer trades.
        # An identical selection gains 0 and fails; no trades is
        # INSUFFICIENT_EVIDENCE, never a success.
        success_metrics={"delta_net_per_eligible_fixture": {">": 0.0},
                         "delta_max_drawdown": {"<=": 0.0},
                         "min_fixtures": 30},
        harm_metrics={"fixture_mean_pnl_per_contract": {">=": 0.0},
                      "negative_fixture_fraction": {"<=": 0.6}},
        acceptance={"holdout_verdict": V_PASS},
        rollback=("reactivate the prior agent_policy_versions row for "
                  "(DEREK, DEREK_ENTRY_POLICY)"),
        code_default=("backend/sportsassets/agents/derek_policy.py",
                      "MIN_GROSS_EDGE_PROBABILITY")),
    ChangeClass(
        name="XAVIER_CAPITAL_PRESERVATION_TRADEOFF", kind=K_POLICY,
        agent=XAVIER,
        description=("how much whole-position EXPECTED VALUE Xavier may give "
                     "up for a strictly better WORST CASE, choosing only "
                     "among the alternatives decide() already admitted under "
                     "the approved limits (agents.xavier_policy "
                     "MAX_EV_SACRIFICE_FOR_DOWNSIDE_USD, the "
                     "CAPITAL_PRESERVATION_V1 leg; 0 == the approved "
                     "EXPECTED_NET_VALUE policy). It changes which admitted "
                     "action the real selector picks; it touches no limit, "
                     "credential, authority, switch, binding or settlement "
                     "check; units: " + _XR.UNITS),
        policy_key="XAVIER_MANAGEMENT_POLICY",
        bounds={"max_ev_sacrifice_for_downside_usd": (0.0, 1.0, False)},
        pre_authorized=False, evaluator="xavier_capital_preservation_replay",
        # SUCCESS IS A BETTER SETTLED NET RESULT PER ELIGIBLE FIXTURE (paired
        # against the current parameter on the same recorded decisions)
        # WITHOUT A DEEPER DRAWDOWN. HARM: a worse worst decision, or a worse
        # ex-ante worst case on average -- the class exists to protect the
        # downside. A variant that changes no action gains 0 and fails; no
        # eligible decision is INSUFFICIENT_EVIDENCE, never a success.
        success_metrics={"delta_net_per_eligible_fixture": {">": 0.0},
                         "delta_max_drawdown": {"<=": 0.0},
                         "min_fixtures": 10},
        harm_metrics={"delta_worst_decision_net_usd": {">=": 0.0},
                      "delta_mean_ex_ante_worst_case_usd": {">=": 0.0}},
        acceptance={"holdout_verdict": V_PASS},
        rollback=("reactivate the prior agent_policy_versions row for "
                  "(XAVIER, XAVIER_MANAGEMENT_POLICY)"),
        code_default=("backend/sportsassets/agents/xavier_policy.py",
                      "MAX_EV_SACRIFICE_FOR_DOWNSIDE_USD")),
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
    ONLY refusal was the edge (or none), with its decision-time inputs
    recorded. Every other refusal still refuses.

    BASIS_NET: the shadow evaluator's net edge per contract, strictly above.
    BASIS_GROSS: Derek's ACTIVE policy measure (derek_policy.ACTIVE_POLICY,
    DEREK_ENTRY_POLICY_V2) -- the blended probability, (the internal
    probability Derek recorded + the recorded Pinnacle probability) / 2,
    minus the executable price, at or above the threshold (the owner's 5 pp
    rule, 0.05 as a probability difference). A row with no recorded
    internal probability is not eligible: none is ever substituted."""
    return derek_eligibility(row, basis=basis) is None and \
        _meets(row, threshold, basis)


def derek_eligibility(row: dict, *, basis: str = BASIS_NET) -> str | None:
    """None when the row is an ELIGIBLE opportunity (any threshold could
    select it); else the reason it is not."""
    if [r for r in (row.get("refusals") or []) if r != EDGE_REFUSAL]:
        return "OTHER_REFUSAL"
    if _num(row.get("price")) is None:
        return "NO_EXECUTABLE_PRICE"
    if _num(row.get("cost")) is None:
        return "NO_FEE"
    if basis == BASIS_GROSS and _num(row.get("probability")) is None:
        return "NO_PROBABILITY"
    if basis == BASIS_GROSS and \
            _num(row.get("internal_probability")) is None:
        return "NO_INTERNAL_PROBABILITY"
    if basis == BASIS_NET and _num(row.get("edge")) is None:
        return "NO_NET_EDGE"
    return None


def derek_policy_probability(row: dict) -> float | None:
    """THE ACTIVE POLICY'S PROBABILITY for a recorded row, through
    `derek_policy.blend` (V2: the mean of the recorded internal and Pinnacle
    probabilities; None when either is missing)."""
    from . import derek_policy as DP                          # noqa: PLC0415
    return DP.blend(row.get("internal_probability"), row.get("probability"))


def _meets(row: dict, threshold: float, basis: str) -> bool:
    if basis == BASIS_GROSS:
        p = derek_policy_probability(row)
        return p is not None and (p - _num(row["price"])) >= \
            float(threshold) - GROSS_EDGE_TOLERANCE
    return _num(row["edge"]) > float(threshold)


#: HOW A SELECTED ROW IS VALUED. `cost_per_contract` in external_valuations
#: is the FEE per contract (bettor_external_shadow: cost_per_contract =
#: fee_per); the acquisition cost is the executable price PLUS that fee.
EXECUTION_ASSUMPTION = (
    "one contract per selected decision, acquired at the recorded "
    "decision-time executable price plus the recorded fee per contract; the "
    "order was not necessarily placed and a fill is NOT proven")


def replay_derek_threshold(rows: list, *, threshold: float,
                           basis: str = BASIS_NET) -> dict:
    """KNOWN SETTLEMENT, HYPOTHETICAL EXECUTION (`DEREK_REPLAY_VERSION`).

    Reports, separately: rows considered; ELIGIBLE opportunities (rows and
    fixtures) and why the rest were not; TRADES the threshold selects (and a
    distinct NO_TRADES flag); net result after the price and the fee;
    capital deployed; losing trades and total loss; the maximum drawdown of
    the chronological cumulative net; and the fixture-weighted means. It
    measures what the selected entries would have settled to at their
    recorded prices -- not realised performance."""
    missing: dict[str, int] = {}
    eligible = []
    for r in rows:
        why = derek_eligibility(r, basis=basis)
        if why is None:
            if r.get("outcome") is None:
                why = "NO_OUTCOME"
        if why is not None:
            missing[why] = missing.get(why, 0) + 1
            continue
        eligible.append(r)
    trades = []
    for r in eligible:
        if not _meets(r, threshold, basis):
            continue
        acq = float(_num(r["price"])) + float(_num(r["cost"]))
        trades.append(dict(r, capital=acq, pnl=float(r["outcome"]) - acq))
    m = fixture_metrics(trades)
    cum = peak = dd = 0.0
    for t in sorted(trades, key=lambda t: (t["decided_at"], str(t["id"]))):
        cum += t["pnl"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    per_fx: dict[str, float] = {}
    for r in eligible:
        per_fx.setdefault(str(r["fixture"]), 0.0)
    for t in trades:
        per_fx[str(t["fixture"])] += t["pnl"]
    net = sum(t["pnl"] for t in trades)
    cap = sum(t["capital"] for t in trades)
    losers = [t for t in trades if t["pnl"] < 0]
    from . import derek_policy as DP                          # noqa: PLC0415
    return dict(
        m, threshold=float(threshold), basis=basis, units=THRESHOLD_UNITS,
        policy=(DP.ACTIVE_POLICY if basis == BASIS_GROSS else None),
        probability_basis=(
            "%s: (recorded internal + recorded Pinnacle) / 2" % DP.ACTIVE_POLICY
            if basis == BASIS_GROSS else None),
        replay_version=DEREK_REPLAY_VERSION, rows_considered=len(rows),
        eligible_opportunities=len(eligible),
        eligible_fixtures=len(per_fx), not_eligible=missing,
        trades=len(trades), no_trades=not trades,
        net_total=round(net, 6), capital_deployed=round(cap, 6),
        return_on_capital=(round(net / cap, 6) if cap > 0 else None),
        losing_trades=len(losers),
        loss_total=round(sum(t["pnl"] for t in losers), 6),
        max_drawdown=round(dd, 6),
        evidence_category=KNOWN_SETTLEMENT, could_have_filled="UNPROVEN",
        execution_assumption=EXECUTION_ASSUMPTION,
        measures="RETROSPECTIVE_HYPOTHETICAL_EXECUTION_ON_KNOWN_SETTLEMENT",
        _per_fixture_net=per_fx,
        _selected_ids=sorted(str(t["id"]) for t in trades))


def paired_comparison(var: dict, base: dict) -> dict:
    """THE VARIANT AGAINST THE CURRENT THRESHOLD ON THE SAME ROWS, per
    eligible fixture (an unselected fixture nets 0): the mean change in net
    result with a normal-approximation 95% interval, and the changes in
    drawdown, capital, trades and losses. Classifies the outcome."""
    a, b = var.get("_per_fixture_net") or {}, base.get("_per_fixture_net") or {}
    fx = sorted(set(a) | set(b))
    d = [a.get(f, 0.0) - b.get(f, 0.0) for f in fx]
    n = len(d)
    mean = sum(d) / n if n else None
    se = None
    if n > 1:
        var_ = sum((x - mean) ** 2 for x in d) / (n - 1)
        se = math.sqrt(var_ / n)
    identical = var.get("_selected_ids") == base.get("_selected_ids")
    if not var.get("trades"):
        outcome = "NO_TRADES"
    elif identical:
        outcome = "IDENTICAL_SELECTION"
    elif mean is not None and mean > 0:
        outcome = "IMPROVED_NET_RESULT"
    elif mean is not None and mean < 0:
        outcome = "WORSE_NET_RESULT"
    else:
        outcome = "NO_CHANGE_IN_NET_RESULT"
    return {
        "eligible_fixtures_compared": n,
        "delta_net_per_eligible_fixture": (None if mean is None
                                           else round(mean, 6)),
        "delta_net_std_error": None if se is None else round(se, 6),
        "delta_net_ci95": (None if se is None else
                           [round(mean - 1.96 * se, 6),
                            round(mean + 1.96 * se, 6)]),
        "delta_net_total": round(sum(d), 6),
        "delta_max_drawdown": round(float(var.get("max_drawdown") or 0)
                                    - float(base.get("max_drawdown") or 0), 6),
        "delta_capital_deployed": round(
            float(var.get("capital_deployed") or 0)
            - float(base.get("capital_deployed") or 0), 6),
        "delta_trades": int(var.get("trades") or 0)
        - int(base.get("trades") or 0),
        "delta_losing_trades": int(var.get("losing_trades") or 0)
        - int(base.get("losing_trades") or 0),
        "identical_selection": identical,
        "objective_outcome": outcome,
        "uncertainty": ("normal approximation over eligible fixtures; "
                        "fixtures are treated as independent")}


def public(m: dict) -> dict:
    """A replay's metrics without its working sets (kept out of records)."""
    return {k: v for k, v in (m or {}).items() if not str(k).startswith("_")}


def rows_digest(rows: list) -> str:
    """THE INPUT RECORDS an evaluation read, by identity and by every field
    it used -- a changed outcome, price, fee or refusal changes it."""
    keys = ("id", "fixture", "decided_at", "outcome", "outcome_at",
            "probability", "price", "cost", "edge")
    flat = sorted([[str(r.get(k)) for k in keys]
                   + [sorted(r.get("refusals") or [])] for r in rows],
                  key=lambda x: (x[0], x[2]))
    return hashlib.sha256(json.dumps(flat, sort_keys=True).encode()
                          ).hexdigest()


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
    if cls.name == "XAVIER_CAPITAL_PRESERVATION_TRADEOFF":
        try:
            from . import xavier_policy as XP                    # noqa: PLC0415
            return {"max_ev_sacrifice_for_downside_usd":
                    float(XP.MAX_EV_SACRIFICE_FOR_DOWNSIDE_USD)}
        except Exception:                                       # noqa: BLE001
            return {"max_ev_sacrifice_for_downside_usd": 0.0}
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

XAVIER_EVALUATOR = "xavier_capital_preservation_replay"
#: the Xavier tradeoff replay's own version, bound into every evaluation
XAVIER_REPLAY_VERSION = _XR.VERSION
REPLAY_EVALUATORS = ("derek_threshold_replay", "derek_gross_edge_replay",
                     XAVIER_EVALUATOR)


async def _xavier_split(conn, tbd: dict, ebd: dict) -> dict:
    """The Xavier replay's rows over the evaluation's own windows, split as
    the evaluation split them (fixture-level holdout, outcome cutoffs), in
    the evidence scope it resolved."""
    got = await _XR.read_rows(
        conn, start=float(tbd["start"]), end=float(ebd["end"]),
        scope=ebd.get("evidence_scope") or _XR.SCOPE_AUTO)
    sp = split_rows(got["rows"], training_boundary=float(tbd["end"]),
                    evaluation_boundary=float(ebd["end"]),
                    salt=ebd.get("holdout_salt") or _XR.HOLDOUT_SALT,
                    percent=int(ebd.get("holdout_percent")
                                or _XR.HOLDOUT_PERCENT))
    return dict(sp, scope=got["scope"],
                outside_scope=got.get("outside_scope"),
                records_read=got.get("records_read"))


async def verify_approval_basis(conn, c: dict) -> dict:
    """WHAT AN APPROVAL IS OF, RE-ESTABLISHED AT THE MOMENT OF APPROVAL.

    For a replay-evaluated class: the committed artifact (branch@sha, diff,
    passing tests) must exist and implement exactly the evaluated
    parameters; the evaluation must carry its binding; the evaluator
    version must still be the one that ran; and the input records the
    evaluation read -- re-read now over the same windows -- must hash to the
    recorded digest (a changed outcome, price, fee or refusal changes it).
    Any difference refuses: the old approval basis is void."""
    cls = class_of(c.get("change_class"))
    if cls is None or cls.evaluator not in REPLAY_EVALUATORS:
        return {"ok": True, "basis": None}
    if not c.get("artifact_ref") or not c.get("diff"):
        return {"ok": False, "refusal": R_NO_ARTIFACT}
    tr = _obj(c.get("test_results")) or {}
    if tr.get("passed") is not True:
        return {"ok": False, "refusal": R_ARTIFACT_TESTS,
                "test_results": {k: tr.get(k) for k in ("passed", "counts")}}
    ev = _obj(c.get("evaluation")) or {}
    b = ev.get("binding") or {}
    if not b:
        return {"ok": False, "refusal": R_NO_BINDING}
    rep = (_obj(c.get("evidence")) or {}).get("sandbox_report") or {}
    changed = []
    if rep.get("params") != c.get("params") or \
            b.get("params") != c.get("params"):
        changed.append("PARAMETERS")
    if rep.get("diff_sha256") and hashlib.sha256(
            str(c["diff"]).encode()).hexdigest() != rep["diff_sha256"]:
        changed.append("DIFF")
    xav = cls.evaluator == XAVIER_EVALUATOR
    if b.get("evaluator_version") != (XAVIER_REPLAY_VERSION if xav
                                      else DEREK_REPLAY_VERSION):
        changed.append("EVALUATOR_VERSION")
    tbd, ebd = b.get("training_boundary") or {}, b.get(
        "evaluation_boundary") or {}
    if xav:
        # THE SAME RECORDED DECISIONS AND SETTLEMENTS, RE-READ NOW: a changed
        # settlement, correction, record or exclusion changes the digest
        sp = await _xavier_split(conn, tbd, ebd)
        now_digest = _XR.rows_digest(sp["training"] + sp["holdout"])
    else:
        rows = await derek_rows(conn, start=float(tbd["start"]),
                                end=float(ebd["end"]))
        sp = split_rows(rows, training_boundary=float(tbd["end"]),
                        evaluation_boundary=float(ebd["end"]),
                        salt=ebd.get("holdout_salt") or DEREK_HOLDOUT_SALT,
                        percent=int(ebd.get("holdout_percent")
                                    or DEREK_HOLDOUT_PERCENT))
        now_digest = rows_digest(sp["training"] + sp["holdout"])
    if now_digest != (b.get("input_records") or {}).get("digest"):
        changed.append("INPUT_RECORDS")
    if changed:
        return {"ok": False, "refusal": R_BASIS_CHANGED, "changed": changed}
    return {"ok": True, "basis": {
        "artifact_ref": c["artifact_ref"], "base_commit": c.get("base_commit"),
        "diff_sha256": hashlib.sha256(str(c["diff"]).encode()).hexdigest(),
        "params": c.get("params"),
        "evaluator_version": b.get("evaluator_version"),
        "training_boundary": tbd, "evaluation_boundary": ebd,
        "input_digest": now_digest,
        "tests": {k: tr.get(k) for k in ("passed", "counts", "tests")}}}


def economic_promotion_gate(c: dict) -> dict:
    """LIVE PROMOTION NEEDS ECONOMIC QUALIFICATION, which an approved
    artifact does not supply: a retrospective replay at recorded prices, with
    fills unproven, is not it. Refuses unless the evaluation records a
    QUALIFIED economic qualification (no evaluator writes one today)."""
    ev = _obj(c.get("evaluation")) or {}
    q = (ev.get("qualification") or {}).get("economic_qualification")
    if q == ECON_QUALIFIED:
        return {"permitted": True, "economic_qualification": q}
    return {"permitted": False, "refusal": R_ECON_PENDING,
            "economic_qualification": q or "NOT_ASSESSED",
            "evidence": (ev.get("qualification") or {}).get("evidence")}


async def policy_version_params(conn, cls: ChangeClass, params: dict) -> dict:
    """What a policy CANDIDATE row carries. For Xavier's tradeoff the WHOLE
    parameter set -- the active policy's, with the evaluated sacrifice and
    the rule it implies -- so the row validates when a person activates it
    (a lone sacrifice under the EXPECTED_NET_VALUE rule would not). Every
    other class: its own parameters."""
    if cls.name != "XAVIER_CAPITAL_PRESERVATION_TRADEOFF":
        return dict(params or {})
    from . import xavier_policy as XP                            # noqa: PLC0415
    active = await XP.load(conn)
    return XP.policy_params_for_sacrifice(
        float((params or {})["max_ev_sacrifice_for_downside_usd"]),
        base=active.get("params"))


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
    basis = await verify_approval_basis(conn, c)
    if not basis["ok"]:
        return dict(basis, candidate_id=candidate_id)
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
                _j(await policy_version_params(conn, cls,
                                               c.get("params") or {})),
                c["proposed_by"], who, _ts(now))
    await task_event(conn, c["task_id"], kind="APPROVED", actor=who,
                     detail={"candidate_id": candidate_id,
                             "credential_role": credential_role,
                             "policy_version_written": wrote_policy,
                             "deployed": False},
                     status="APPROVED", now=now)
    return {"ok": True, "candidate_id": candidate_id, "approved_by": who,
            "credential_role": credential_role,
            "policy_version_written_as_candidate": wrote_policy,
            "approval_scope": ("THE_ARTIFACT_AND_ITS_BOUND_EVIDENCE_WERE_"
                               "REVIEWED"),
            "approval_basis": basis.get("basis"),
            "live_promotion": economic_promotion_gate(c),
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
    and cost and the outcome and the instant it became known -- and the
    INTERNAL probability Derek recorded for the valuation (the approved
    model's, at the decision; NULL when no approved model scored it), which
    the active policy (V2) averages with the Pinnacle probability."""
    if not await _regclass(conn, "external_valuations"):
        return []
    internal = ("(SELECT d.model_p FROM derek_entry_decisions d "
                "  WHERE d.valuation_id = v.id AND d.model_p IS NOT NULL "
                "  ORDER BY d.decided_at, d.decision_id LIMIT 1)"
                if await _regclass(conn, "derek_entry_decisions")
                else "NULL::float8")
    return [{"id": r["id"], "fixture": r["fixture"],
             "decided_at": float(r["decided_at"]),
             "outcome_at": _num(r["outcome_at"]),
             "outcome": r["outcome"], "edge": _num(r["edge"]),
             "cost": _num(r["cost"]), "refusals": list(r["refusals"] or []),
             "probability": _num(r["probability"]),
             "internal_probability": _num(r["internal_probability"]),
             "price": _num(r["executable_price"]),
             "decision": r["decision"]}
            for r in await conn.fetch(
        "SELECT v.id, coalesce(v.event_key, v.us_market_slug, "
        "         v.condition_id) AS fixture, "
        "       extract(epoch FROM v.decided_at)::float8 AS decided_at, "
        "       extract(epoch FROM v.outcome_at)::float8 AS outcome_at, "
        "       v.outcome, v.estimated_edge_per_contract AS edge, "
        "       v.cost_per_contract AS cost, v.refusals, v.decision, "
        "       v.probability, v.executable_price, "
        "       %s AS internal_probability "
        "  FROM external_valuations v WHERE v.record_purpose='ENTRY_DECISION' "
        "   AND v.decided_at > $1 AND v.decided_at <= $2 "
        " ORDER BY v.decided_at, v.id" % internal, _ts(start), _ts(end))]


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
    base_train = replay_derek_threshold(sp["training"], threshold=cur,
                                        basis=basis)
    if not variants:
        await task_event(conn, tid, kind="NO_VARIANT", actor=AUDREY,
                         detail={"current": cur}, status="CLOSED_NO_CHANGE",
                         now=now)
        return {"task_id": tid, "verdict": "NO_VARIANT_WITHIN_BOUNDS"}
    await task_event(conn, tid, kind="EVALUATING", actor=EVALUATOR_REPLAY,
                     detail={"variants": variants, "current": cur,
                             "units": THRESHOLD_UNITS,
                             "excluded": sp["excluded"]},
                     status="EVALUATING", now=now)
    tbd = {"start": start, "end": tb, "outcomes_known_by": tb}
    ebd = {"start": tb, "end": eb, "outcomes_known_by": eb,
           "holdout_salt": DEREK_HOLDOUT_SALT,
           "holdout_percent": DEREK_HOLDOUT_PERCENT}
    scored = []
    for v in variants:
        prop = await propose(
            conn, task_id=tid, change_class=cls.name, proposed_by=cls.agent,
            hypothesis=spec.get("hypothesis") or (
                "Derek's entry threshold is miscalibrated against settled "
                "outcomes"), evidence=spec.get("evidence") or {},
            affected_behavior=cls.description,
            params={pname: v}, training_boundary=tbd,
            evaluation_boundary=ebd, now=now)
        if not prop.get("ok"):
            scored.append({"threshold": v, "refused": prop})
            continue
        m = replay_derek_threshold(sp["training"], threshold=v, basis=basis)
        cmp_ = paired_comparison(m, base_train)
        scored.append({"candidate_id": prop["candidate_id"], "threshold": v,
                       "training": dict(public(m), **cmp_,
                                        baseline=public(base_train))})
    # SELECTION USES TRAINING EVIDENCE ONLY. The holdout is replayed once,
    # afterwards, for the selected variant and the current threshold.
    ok = [s for s in scored if "candidate_id" in s
          and s["training"]["fixtures"] >= min_fx
          and s["training"]["delta_net_per_eligible_fixture"] is not None]
    best = max(ok, key=lambda s: (
        s["training"]["delta_net_per_eligible_fixture"],
        -abs(s["threshold"] - cur)), default=None)
    selection = {
        "segment": "TRAINING",
        "rule": ("among variants with at least %d selected training fixtures,"
                 " the largest mean paired change in net result per eligible "
                 "fixture against the current threshold (%.4f); ties go to the"
                 " variant nearest the current threshold" % (min_fx, cur)),
        "holdout_used_for_selection": False,
        "current": cur, "units": THRESHOLD_UNITS,
        "attempted": [{"threshold": s["threshold"],
                       "candidate_id": s.get("candidate_id"),
                       "refused": (s.get("refused") or {}).get("refusal"),
                       "training": ({k: s["training"].get(k) for k in (
                           "fixtures", "trades", "net_total",
                           "delta_net_per_eligible_fixture",
                           "delta_max_drawdown", "objective_outcome")}
                           if "training" in s else None)}
                      for s in scored],
        "selected": None if best is None else {
            "threshold": best["threshold"],
            "candidate_id": best["candidate_id"]}}
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
                            "segment": "TRAINING", "selection": selection,
                            "metrics": s["training"]})
    if best is None:
        await task_event(conn, tid, kind="REJECTED", actor=EVALUATOR_REPLAY,
                         detail={"why": "no variant had enough selected "
                                        "training fixtures",
                                 "min_fixtures": min_fx,
                                 "selection": selection,
                                 "training_fixtures":
                                     len(sp["training_fixtures"])},
                         status="REJECTED", now=now)
        return {"task_id": tid, "verdict": V_INSUFFICIENT,
                "selection": selection,
                "split": {k: v for k, v in sp.items() if k in ("excluded",)}}
    hid = "derek_threshold:%s:%dpct" % (DEREK_HOLDOUT_SALT,
                                        DEREK_HOLDOUT_PERCENT)
    await ensure_holdout(conn, holdout_id=hid, description=(
        "fixture-level holdout of Derek's recorded valuations"),
        rule={"kind": "FIXTURE_HASH", "salt": DEREK_HOLDOUT_SALT,
              "percent": DEREK_HOLDOUT_PERCENT})
    hv = replay_derek_threshold(sp["holdout"], threshold=best["threshold"],
                                basis=basis)
    hb = replay_derek_threshold(sp["holdout"], threshold=cur, basis=basis)
    hm = dict(public(hv), **paired_comparison(hv, hb))
    hm["baseline"] = public(hb)
    hm["min_fixtures"] = min_fx
    hm["excluded"] = sp["excluded"]
    hm["training_fixture_count"] = len(sp["training_fixtures"])
    hm["holdout_fixture_count"] = len(sp["holdout_fixtures"])
    hm["fixtures_in_both"] = len(set(sp["training_fixtures"])
                                 & set(sp["holdout_fixtures"]))
    j = judge(hm, success=cls.success_metrics, harm=cls.harm_metrics,
              min_key="min_fixtures", n_key="fixtures")
    used = sp["training"] + sp["holdout"]
    binding = {
        "evaluator_version": DEREK_REPLAY_VERSION,
        "change_class": cls.name, "params": {pname: best["threshold"]},
        "current": {pname: cur, "source": current.get("source"),
                    "version": current.get("version")},
        "units": THRESHOLD_UNITS, "basis": basis,
        "training_boundary": tbd, "evaluation_boundary": ebd,
        "holdout_id": hid,
        "input_records": {"table": "external_valuations "
                                   "(record_purpose=ENTRY_DECISION)",
                          "digest": rows_digest(used),
                          "training_rows": len(sp["training"]),
                          "holdout_rows": len(sp["holdout"]),
                          "outcomes_as_of": eb}}
    qualification = {
        "evidence": QUAL_RETROSPECTIVE,
        "execution": EXECUTION_ASSUMPTION, "fills": "UNPROVEN",
        "establishes": ("that the selected threshold would have selected "
                        "entries with a better settled net result on the "
                        "holdout, at recorded prices and fees"),
        "does_not_establish": ("that orders at this threshold fill, or "
                               "realised performance; nor any prospective "
                               "result"),
        "economic_qualification": ECON_PENDING}
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
                    "selection": selection, "binding": binding,
                    "qualification": qualification,
                    "approval_ready_means": (
                        "ARTIFACT_READY_FOR_REVIEW once a committed artifact "
                        "is attached; NOT economic qualification for live "
                        "promotion"),
                    "evidence_category": KNOWN_SETTLEMENT,
                    "could_have_filled": "UNPROVEN"})
    await task_event(
        conn, tid, kind="APPROVAL_READY" if passed else "REJECTED",
        actor=EVALUATOR_REPLAY, detail={"candidate_id": best["candidate_id"],
                                        "judgement": j,
                                        "objective_outcome":
                                            hm["objective_outcome"]},
        status="APPROVAL_READY" if passed else "REJECTED", now=now)
    return {"task_id": tid, "verdict": j["verdict"],
            "candidate_id": best["candidate_id"], "holdout": hm,
            "selection": selection, "binding": binding}


async def _xavier_current(conn, cls: ChangeClass) -> dict:
    """The parameter Xavier runs under now: the ACTIVE validated version
    (`xavier_policy.load`, the scheduled path's own reader), else the code
    default -- labelled either way."""
    from . import xavier_policy as XP                            # noqa: PLC0415
    pname = next(iter(cls.bounds))
    pol = await XP.load(conn)
    v = _num((pol.get("params") or {}).get(pname))
    return {"value": float(v if v is not None
                           else code_default_params(cls).get(pname) or 0.0),
            "source": pol.get("source"), "version": pol.get("version"),
            "params": dict(pol.get("params") or {})}


XAVIER_STEPS = (0.25, 0.50, 1.00)


async def evaluate_xavier_tradeoff_task(conn, task: dict, *,
                                        now: float) -> dict:
    """XAVIER_CAPITAL_PRESERVATION_TRADEOFF BY REPLAY OVER HIS RECORDED
    DECISIONS (`agents.xavier_replay`).

    Each recorded decision's frozen inputs are re-run through Xavier's real
    decision function under the current sacrifice and each variant; the
    chosen action is valued on the position's known settlement through its
    persisted payout table. Fixture-level holdout (fixed salt), cut by
    outcome availability. EVERY variant is tried on TRAINING only and
    recorded; the one selected (the largest paired gain among those whose
    training judgement passes the class's rule) is replayed ONCE on the
    holdout against the current parameter and judged. Not pre-authorized: a
    PASS stops at APPROVAL_READY."""
    cls = CHANGE_CLASSES["XAVIER_CAPITAL_PRESERVATION_TRADEOFF"]
    pname = next(iter(cls.bounds))
    spec = task.get("spec") or {}
    tid = task["task_id"]
    start, tb, eb = _boundaries(spec, now, holdout_days=float(
        spec.get("holdout_days") or 14))
    current = await _xavier_current(conn, cls)
    cur = float(current["value"])
    lo, hi, _ = cls.bounds[pname]
    variants = sorted({round(float(v), 6) for v in (
        spec.get("variants") or [cur + d for d in XAVIER_STEPS]
        + [cur - d for d in XAVIER_STEPS])
        if lo <= float(v) <= hi and abs(float(v) - cur) > 1e-12})
    if spec.get("direction") == MORE_PROTECTION:
        # a loss / drawdown directive only ever buys MORE protection
        variants = [v for v in variants if v > cur]
    if not variants:
        await task_event(conn, tid, kind="NO_VARIANT", actor=AUDREY,
                         detail={"current": cur, "bounds": [lo, hi]},
                         status="CLOSED_NO_CHANGE", now=now)
        return {"task_id": tid, "verdict": "NO_VARIANT_WITHIN_BOUNDS"}
    got = await _XR.read_rows(conn, start=start, end=eb, scope=spec.get(
        "evidence_scope") or _XR.SCOPE_AUTO)
    scope = got["scope"]
    sp = split_rows(got["rows"], training_boundary=tb, evaluation_boundary=eb,
                    salt=_XR.HOLDOUT_SALT, percent=_XR.HOLDOUT_PERCENT)
    min_fx = int(cls.success_metrics.get("min_fixtures") or 1)
    base_train = _XR.replay(sp["training"], sacrifice=cur)
    await task_event(conn, tid, kind="EVALUATING", actor=EVALUATOR_REPLAY,
                     detail={"variants": variants, "current": cur,
                             "units": _XR.UNITS, "evidence_scope": scope,
                             "records_read": got.get("records_read"),
                             "excluded": sp["excluded"]},
                     status="EVALUATING", now=now)
    tbd = {"start": start, "end": tb, "outcomes_known_by": tb}
    ebd = {"start": tb, "end": eb, "outcomes_known_by": eb,
           "holdout_salt": _XR.HOLDOUT_SALT,
           "holdout_percent": _XR.HOLDOUT_PERCENT,
           "evidence_scope": scope}
    scored = []
    for v in variants:
        prop = await propose(
            conn, task_id=tid, change_class=cls.name, proposed_by=cls.agent,
            hypothesis=spec.get("hypothesis") or (
                "Xavier gives up too little (or too much) expected value for "
                "a better worst case, judged on settled outcomes"),
            evidence=spec.get("evidence") or {},
            affected_behavior=cls.description, params={pname: v},
            training_boundary=tbd, evaluation_boundary=ebd, now=now)
        if not prop.get("ok"):
            scored.append({"sacrifice": v, "refused": prop})
            continue
        m = _XR.replay(sp["training"], sacrifice=v)
        tr = dict(_XR.public(m), **_XR.paired(m, base_train),
                  baseline=_XR.public(base_train), min_fixtures=min_fx)
        j = judge(tr, success=cls.success_metrics, harm=cls.harm_metrics,
                  min_key="min_fixtures", n_key="fixtures")
        scored.append({"candidate_id": prop["candidate_id"], "sacrifice": v,
                       "training": tr, "judgement": j})
    # SELECTION USES TRAINING EVIDENCE ONLY: among the variants whose
    # training judgement PASSES the class's rule, the largest paired gain per
    # eligible fixture (ties to the variant nearest the current value). The
    # holdout is replayed once, afterwards, for that variant alone.
    ok = [s for s in scored if "candidate_id" in s
          and s["judgement"]["verdict"] == V_PASS]
    best = max(ok, key=lambda s: (
        s["training"]["delta_net_per_eligible_fixture"],
        -abs(s["sacrifice"] - cur)), default=None)
    selection = {
        "segment": "TRAINING",
        "rule": ("among variants whose TRAINING judgement passes the class's "
                 "rule (at least %d eligible fixtures, a better paired net "
                 "result, no deeper drawdown, no harm), the largest mean "
                 "paired change in settled net per eligible fixture against "
                 "the current %s (%.4f); ties go to the variant nearest the "
                 "current value" % (min_fx, pname, cur)),
        "holdout_used_for_selection": False,
        "current": cur, "units": _XR.UNITS, "parameter": pname,
        "attempted": [{"sacrifice": s["sacrifice"],
                       "candidate_id": s.get("candidate_id"),
                       "refused": (s.get("refused") or {}).get("refusal"),
                       "verdict": (s.get("judgement") or {}).get("verdict"),
                       "training": ({k: s["training"].get(k) for k in (
                           "fixtures", "eligible_decisions", "net_total",
                           "delta_net_per_eligible_fixture",
                           "delta_max_drawdown", "actions_changed",
                           "objective_outcome")}
                           if "training" in s else None)}
                      for s in scored],
        "selected": None if best is None else {
            "sacrifice": best["sacrifice"],
            "candidate_id": best["candidate_id"]}}
    for s in scored:
        if "candidate_id" not in s:
            continue
        is_sel = best is not None and s["candidate_id"] == best[
            "candidate_id"]
        await record_trial(
            conn, candidate_id=s["candidate_id"], task_id=tid,
            segment="TRAINING", variant={pname: s["sacrifice"]},
            metrics=dict(s["training"], judgement=s["judgement"],
                         excluded=sp["excluded"],
                         training_fixtures=len(sp["training_fixtures"])),
            verdict=V_SELECTED if is_sel else V_NOT_SELECTED,
            evidence_category=KNOWN_SETTLEMENT, training_boundary=start,
            evaluation_boundary=tb, now=now)
        if not is_sel:
            jv = s["judgement"]["verdict"]
            await record_evaluation(
                conn, candidate_id=s["candidate_id"],
                evaluated_by=EVALUATOR_REPLAY, state="REJECTED", now=now,
                evaluation={"verdict": (jv if jv != V_PASS
                                        else V_NOT_SELECTED),
                            "segment": "TRAINING", "selection": selection,
                            "judgement": s["judgement"],
                            "metrics": s["training"],
                            "evidence_scope": scope})
    if best is None:
        insufficient = all((s.get("judgement") or {}).get("verdict")
                           == V_INSUFFICIENT for s in scored
                           if "candidate_id" in s)
        verdict = V_INSUFFICIENT if insufficient else \
            "NO_VARIANT_PASSED_TRAINING"
        await task_event(conn, tid, kind="REJECTED", actor=EVALUATOR_REPLAY,
                         detail={"why": ("too few eligible training "
                                         "fixtures" if insufficient else
                                         "no variant passed the class's "
                                         "rule on training"),
                                 "verdict": verdict, "min_fixtures": min_fx,
                                 "selection": selection,
                                 "evidence_scope": scope,
                                 "training_fixtures":
                                     len(sp["training_fixtures"])},
                         status="REJECTED", now=now)
        return {"task_id": tid, "verdict": verdict, "selection": selection,
                "evidence_scope": scope,
                "split": {"excluded": sp["excluded"]},
                "baseline_training": _XR.public(base_train)}
    hid = "xavier_tradeoff:%s:%dpct" % (_XR.HOLDOUT_SALT,
                                        _XR.HOLDOUT_PERCENT)
    await ensure_holdout(conn, holdout_id=hid, description=(
        "fixture-level holdout of Xavier's recorded decisions"),
        rule={"kind": "FIXTURE_HASH", "salt": _XR.HOLDOUT_SALT,
              "percent": _XR.HOLDOUT_PERCENT})
    hv = _XR.replay(sp["holdout"], sacrifice=best["sacrifice"])
    hb = _XR.replay(sp["holdout"], sacrifice=cur)
    hm = dict(_XR.public(hv), **_XR.paired(hv, hb))
    hm["baseline"] = _XR.public(hb)
    hm["min_fixtures"] = min_fx
    hm["excluded"] = sp["excluded"]
    hm["training_fixture_count"] = len(sp["training_fixtures"])
    hm["holdout_fixture_count"] = len(sp["holdout_fixtures"])
    hm["fixtures_in_both"] = len(set(sp["training_fixtures"])
                                 & set(sp["holdout_fixtures"]))
    hm["evidence_scope"] = scope
    j = judge(hm, success=cls.success_metrics, harm=cls.harm_metrics,
              min_key="min_fixtures", n_key="fixtures")
    used = sp["training"] + sp["holdout"]
    rehearsal = scope == _XR.SCOPE_REHEARSAL
    binding = {
        "evaluator_version": XAVIER_REPLAY_VERSION,
        "change_class": cls.name, "params": {pname: best["sacrifice"]},
        "current": {pname: cur, "source": current.get("source"),
                    "version": current.get("version")},
        "units": _XR.UNITS,
        "decision_function": ("agents.xavier_policy.run -> "
                              "bettor_funded_decision.decide (+ the "
                              "CAPITAL_PRESERVATION_V1 leg when > 0), on "
                              "each record's persisted frozen inputs"),
        "dispatch_gate": "bettor_funded_pair_cycle.common_valuation_gate",
        "training_boundary": tbd, "evaluation_boundary": ebd,
        "holdout_id": hid,
        "input_records": {
            "table": ("bettor_xavier_decisions (reasoning.decision_inputs, "
                      "alternatives' payout tables) + settlements "
                      "(bettor_funded_intents ENTRY venue settlements with "
                      "booked corrections; bettor_pair_observations labels)"),
            "digest": _XR.rows_digest(used),
            "training_rows": len(sp["training"]),
            "holdout_rows": len(sp["holdout"]),
            "records_read": got.get("records_read"),
            "outside_evidence_scope": got.get("outside_scope"),
            "evidence_scope": scope, "outcomes_as_of": eb}}
    qualification = {
        "evidence": QUAL_RETROSPECTIVE,
        "execution": _XR.EXECUTION_ASSUMPTION, "fills": "UNPROVEN",
        "evidence_scope": scope, "rehearsal": rehearsal,
        "rehearsal_label": _XR.REHEARSAL_LABEL if rehearsal else None,
        "establishes": ("that, re-running Xavier's decision function on the "
                        "recorded inputs, the selected sacrifice would have "
                        "chosen actions with a better settled net result on "
                        "the holdout without a deeper drawdown, at the "
                        "alternatives' frozen prices and fees"
                        + (" -- IN A REHEARSAL (demonstration books)"
                           if rehearsal else "")),
        "does_not_establish": ("that the alternatives not executed would "
                               "have filled, realised performance, later "
                               "management of the position, or any "
                               "prospective result"),
        "economic_qualification": ECON_PENDING}
    tr = await record_trial(
        conn, candidate_id=best["candidate_id"], task_id=tid,
        segment="HOLDOUT", holdout_id=hid,
        variant={pname: best["sacrifice"]},
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
                    "selection": selection, "binding": binding,
                    "qualification": qualification,
                    "approval_ready_means": (
                        "ARTIFACT_READY_FOR_REVIEW once a committed artifact "
                        "is attached; NOT economic qualification for live "
                        "promotion"),
                    "evidence_category": KNOWN_SETTLEMENT,
                    "evidence_scope": scope,
                    "could_have_filled": "UNPROVEN"})
    await task_event(
        conn, tid, kind="APPROVAL_READY" if passed else "REJECTED",
        actor=EVALUATOR_REPLAY, detail={"candidate_id": best["candidate_id"],
                                        "judgement": j,
                                        "evidence_scope": scope,
                                        "objective_outcome":
                                            hm["objective_outcome"]},
        status="APPROVAL_READY" if passed else "REJECTED", now=now)
    return {"task_id": tid, "verdict": j["verdict"],
            "candidate_id": best["candidate_id"], "holdout": hm,
            "selection": selection, "binding": binding,
            "evidence_scope": scope}


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


# ═════════════════════════════════════════════════════════════════════
# 8b · A MANAGEMENT DIRECTIVE BECOMES AGENT WORK
# ═════════════════════════════════════════════════════════════════════
#
# A directive (agents.directives) assigns DIRECTIVE_IMPROVEMENT tasks to Derek
# and/or Xavier. Here the assigned agent TAKES ONE UP: for a POLICY_CANDIDATE
# directive it opens its own IMPROVEMENT task on the change class it owns
# (deterministic id, linked to the directive and the directive's task), which
# the evaluator below then replays like any other -- the proposer is the
# agent, the evaluator the deterministic replay, the approver a person. The
# directive task follows the improvement task's outcome. A directive cannot
# name the class, the bounds or the criteria: those are the registry's.

DIRECTIVE_TASK_KIND = "DIRECTIVE_IMPROVEMENT"
#: what each agent's directive work is, when a replay for it is registered
DIRECTIVE_WORK = {DEREK: "DEREK_ENTRY_POLICY_THRESHOLD",
                  XAVIER: "XAVIER_CAPITAL_PRESERVATION_TRADEOFF"}
#: objectives under which a threshold may only TIGHTEN (fewer, better
#: entries); never loosened in the name of reducing losses or drawdown
TIGHTEN_ONLY = ("DRAWDOWN_REDUCTION", "LOSS_REDUCTION")
#: THE OBJECTIVE KINDS AN AGENT'S CLASS CAN ADDRESS, and the one direction
#: its parameter may move under each (an agent absent here: any kind, its
#: own rule above). Xavier's tradeoff addresses losses and drawdown ONLY by
#: MORE downside protection -- a larger sacrifice of expected value for a
#: better worst case -- never less. Any other objective (profit targets,
#: pairing, exits, execution quality...) is not something this class can
#: move: the task WAITS and names why.
MORE_PROTECTION = "MORE_DOWNSIDE_PROTECTION"
DIRECTIVE_OBJECTIVES = {XAVIER: {"DRAWDOWN_REDUCTION": MORE_PROTECTION,
                                 "LOSS_REDUCTION": MORE_PROTECTION}}
R_NO_DIRECTIVE_EVALUATOR = "NO_REGISTERED_EVALUATOR_FOR_THIS_AGENTS_DIRECTIVE_WORK"
R_OBJECTIVE_NOT_ADDRESSED = (
    "NO_REGISTERED_EVALUATOR_ADDRESSES_THIS_OBJECTIVE_FOR_THIS_AGENT")
R_PRIORITY_ONLY = "PRIORITY_ONLY_DIRECTIVE_PRODUCES_NO_CANDIDATE"
#: directive-task status that mirrors each improvement-task outcome
_MIRROR = {"APPROVAL_READY": "APPROVAL_READY", "APPROVED": "APPROVED",
           "RELEASED": "RELEASED", "REJECTED": "REJECTED",
           "ROLLED_BACK": "ROLLED_BACK", "CLOSED_NO_CHANGE": "CLOSED_NO_CHANGE",
           "CANDIDATE_READY": "CANDIDATE_READY"}


def directive_work_task_id(directive_task_id: str) -> str:
    return "imp-%s" % directive_task_id


async def _has_event(conn, task_id: str, kind: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM agent_task_events WHERE task_id=$1 AND kind=$2 "
        "LIMIT 1", task_id, kind))


async def take_up_directive_tasks(conn, *, now: float) -> dict:
    """THE ASSIGNED AGENT TAKES UP EACH OPEN DIRECTIVE TASK. Idempotent:
    the improvement task id is derived from the directive task's."""
    out: dict[str, Any] = {"taken_up": [], "waiting": [], "skipped": []}
    for t in await read_tasks(conn, kind=DIRECTIVE_TASK_KIND,
                              statuses=("OPEN",)):
        tid, agent = t["task_id"], str(t.get("assignee") or "")
        spec = t.get("spec") or {}
        if spec.get("change_class") != "POLICY_CANDIDATE":
            if not await _has_event(conn, tid, "NO_CANDIDATE_WORK"):
                await task_event(conn, tid, kind="NO_CANDIDATE_WORK",
                                 actor=agent or AUDREY,
                                 detail={"why": R_PRIORITY_ONLY}, now=now)
            out["skipped"].append(tid)
            continue
        cls_name = DIRECTIVE_WORK.get(agent)
        if cls_name is None:
            await task_event(conn, tid, kind="WAITING_FOR_AN_EVALUATOR",
                             actor=agent or AUDREY,
                             detail={"why": R_NO_DIRECTIVE_EVALUATOR,
                                     "agent": agent,
                                     "registered": dict(DIRECTIVE_WORK)},
                             status="WAITING", now=now)
            out["waiting"].append(tid)
            continue
        kind = str(spec.get("objective_kind") or "")
        addressed = DIRECTIVE_OBJECTIVES.get(agent)
        if addressed is not None and kind not in addressed:
            await task_event(conn, tid, kind="WAITING_FOR_AN_EVALUATOR",
                             actor=agent or AUDREY,
                             detail={"why": R_OBJECTIVE_NOT_ADDRESSED,
                                     "agent": agent, "objective_kind": kind,
                                     "change_class": cls_name,
                                     "addresses": sorted(addressed)},
                             status="WAITING", now=now)
            out["waiting"].append(tid)
            continue
        cls = CHANGE_CLASSES[cls_name]
        pname = next(iter(cls.bounds))
        if cls.evaluator == XAVIER_EVALUATOR:
            current = await _xavier_current(conn, cls)
            cur = float(current["value"])
        else:
            current = await current_policy(conn, cls)
            cur = current["params"].get(pname)
            cur = float(cur if cur is not None
                        else code_default_params(cls).get(pname))
        lo, hi, _ = cls.bounds[pname]
        variants = None
        if cls.evaluator == XAVIER_EVALUATOR:
            # MORE DOWNSIDE PROTECTION ONLY: a larger sacrifice, in bounds
            variants = [round(cur + d, 6) for d in XAVIER_STEPS
                        if lo <= cur + d <= hi + 1e-12]
        elif kind in TIGHTEN_ONLY:
            variants = [round(cur + d, 6) for d in (0.01, 0.02)
                        if lo <= cur + d <= hi]
        wid = directive_work_task_id(tid)
        if cls.evaluator == XAVIER_EVALUATOR:
            hyp = ("Management directive %s (%s): giving up at most a "
                   "larger %s (USD of expected value per decision) than the "
                   "current %.4f for a strictly better worst case, among the "
                   "actions the approved limits already admit, settles "
                   "better with no deeper drawdown"
                   % (spec.get("directive_id"), kind, pname, cur))
            tests = ["tests/test_xavier_tradeoff_directive_becomes_an_"
                     "evaluated_artifact.py::test_the_versioned_default_is_"
                     "a_whole_valid_policy_the_selector_reads"]
        else:
            hyp = ("Management directive %s (%s): a %s %s than the current "
                   "%.4f selects fewer losing entries without giving up "
                   "settled profit" % (spec.get("directive_id"),
                                       kind or "UNCLASSIFIED", pname,
                                       "higher" if variants else "different",
                                       cur))
            tests = ["tests/test_improvement_is_evaluated_released_"
                     "and_rolled_back.py::test_the_derek_policy_class_"
                     "targets_the_binding_policy_key"]
        wspec = {"change_class": cls_name, "variants": variants,
                 "directive_id": spec.get("directive_id"),
                 "directive_task_id": tid,
                 "objective": spec.get("objective"),
                 "objective_kind": kind or None,
                 "direction": (addressed or {}).get(kind),
                 "hypothesis": hyp,
                 "evidence": {"directive_id": spec.get("directive_id"),
                              "directive_task_id": tid,
                              "current": {pname: cur,
                                          "source": current.get("source")}},
                 "standing_rules": spec.get("standing_rules"),
                 "tests": tests,
                 "test_cwd": "backend"}
        if cls.evaluator == XAVIER_EVALUATOR:
            wspec["evidence_scope"] = _XR.SCOPE_AUTO
        made = await create_task(
            conn, assignee=agent, created_by=agent, kind=TASK_KIND,
            title="%s for directive %s" % (cls_name, spec.get("directive_id")),
            spec=wspec, directive_id=spec.get("directive_id"),
            evidence=[{"kind": "agent_tasks", "id": tid}], task_id=wid,
            now=now)
        if not made.get("ok"):
            out["skipped"].append({"task_id": tid, "refusal": made})
            continue
        await task_event(conn, tid, kind="WORK_TAKEN_UP", actor=agent,
                         detail={"improvement_task_id": wid,
                                 "change_class": cls_name,
                                 "variants": variants, "current": cur},
                         status="IN_PROGRESS", now=now)
        out["taken_up"].append({"task_id": tid, "improvement_task_id": wid,
                                "change_class": cls_name,
                                "variants": variants})
    return out


async def reflect_directive_work(conn, *, now: float) -> dict:
    """EACH DIRECTIVE TASK FOLLOWS ITS IMPROVEMENT TASK'S OUTCOME, naming
    the candidate and its evaluation."""
    out: dict[str, Any] = {"reflected": []}
    for t in await read_tasks(conn, kind=DIRECTIVE_TASK_KIND,
                              statuses=("IN_PROGRESS", "CANDIDATE_READY",
                                        "APPROVAL_READY", "APPROVED")):
        tid = t["task_id"]
        w = await read_task(conn, directive_work_task_id(tid))
        if w is None:
            continue
        to = _MIRROR.get(str(w.get("status")))
        if to is None or to == t.get("status"):
            continue
        cands = [dict(c) for c in await conn.fetch(
            "SELECT candidate_id, state, params, artifact_ref "
            "  FROM improvement_candidates WHERE task_id=$1 "
            " ORDER BY created_at, candidate_id", w["task_id"])]
        await task_event(conn, tid, kind="IMPROVEMENT_OUTCOME",
                         actor=str(t.get("assignee") or AUDREY),
                         detail={"improvement_task_id": w["task_id"],
                                 "improvement_status": w.get("status"),
                                 "candidates": [
                                     {"candidate_id": c["candidate_id"],
                                      "state": c["state"],
                                      "params": _obj(c["params"]),
                                      "artifact_ref": c["artifact_ref"]}
                                     for c in cands]},
                         status=to, now=now)
        out["reflected"].append({"task_id": tid, "to": to})
    return out


async def attach_artifact(conn, candidate_id: str, *, diff: str,
                          artifact_ref: str, base_commit: str,
                          test_results: dict, report: dict,
                          now: float) -> dict:
    """THE SANDBOX'S COMMITTED ARTIFACT, ON THE CANDIDATE THAT WAS
    EVALUATED. Written once (migration 155 refuses a second artifact or a
    changed diff); a REJECTED or WITHDRAWN candidate gets none."""
    c = await candidate(conn, candidate_id)
    if c is None:
        return {"ok": False, "refusal": "NO_SUCH_CANDIDATE"}
    if c.get("state") in ("REJECTED", "WITHDRAWN"):
        return {"ok": False, "refusal": "THE_CANDIDATE_WAS_%s" % c["state"]}
    if c.get("artifact_ref"):
        return {"ok": False, "refusal": "THE_ARTIFACT_IS_ALREADY_RECORDED",
                "artifact_ref": c["artifact_ref"]}
    ev = dict(_obj(c.get("evidence")) or {})
    ev["sandbox_report"] = report
    await conn.execute(
        "UPDATE improvement_candidates SET diff=$2, artifact_ref=$3, "
        " base_commit=$4, test_results=$5::jsonb, evidence=$6::jsonb "
        " WHERE candidate_id=$1 AND artifact_ref IS NULL",
        candidate_id, diff, artifact_ref, base_commit, _j(test_results),
        _j(ev))
    await task_event(conn, c["task_id"], kind="ARTIFACT_BUILT",
                     actor=report.get("proposed_by") or c.get("proposed_by"),
                     detail={"candidate_id": candidate_id,
                             "artifact_ref": artifact_ref,
                             "tests_passed": (test_results or {}).get(
                                 "passed")}, now=now)
    return {"ok": True, "candidate_id": candidate_id,
            "artifact_ref": artifact_ref}


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
            # directive tasks first, so a task taken up now is evaluated now
            try:
                out["directive_work"] = await take_up_directive_tasks(
                    conn, now=float(now))
            except Exception as exc:                            # noqa: BLE001
                out["directive_work"] = {"error": type(exc).__name__}
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
                elif cls.evaluator == XAVIER_EVALUATOR:
                    got = await evaluate_xavier_tradeoff_task(conn, t,
                                                              now=now)
                else:
                    got = await advance_code_task(conn, t, now=now)
                out["advanced"].append(got)
            except Exception as exc:                            # noqa: BLE001
                out["errors"].append({"task_id": t["task_id"],
                                      "error": "%s: %s" % (
                                          type(exc).__name__,
                                          str(exc)[:200])})
        try:
            out["directive_outcomes"] = await reflect_directive_work(
                conn, now=float(now))
        except Exception as exc:                                # noqa: BLE001
            out["directive_outcomes"] = {"error": type(exc).__name__}
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
