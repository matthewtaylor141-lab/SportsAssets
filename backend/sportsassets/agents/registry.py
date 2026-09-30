"""THE THREE AGENTS: WHO THEY ARE, WHAT THEY MAY DO, WHAT THEY ARE DOING.

Derek (discovery / entry), Xavier (position management / exits) and Audrey
(audit / management communication / improvement) are NAMED ROLES over the
application's existing deterministic machinery. This module is their shared
registry (migration 152):

  * IDENTITIES -- mandate, versioned policy / model / code identity and an
    EXPLICIT allow and deny list of tools per agent;
  * heartbeat / runs -- a truthful state per agent (unknown is not zero; an
    empty pass is not management), never raising into its caller;
  * policy versions -- one ACTIVE per (agent, key), with a clearly labelled
    CODE_DEFAULT fallback;
  * tasks and their append-only history;
  * a decision INDEX that links to the authoritative records.

WHAT THIS MODULE CANNOT DO, BY CONSTRUCTION: send, cancel or recover an order
(it imports no order path), write accounting (the funded book is the one
book), or change a risk limit, credential, account authority, approval or
submission switch. Those are named on every agent's DENY list and no API here
writes them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

DEREK = "DEREK"
XAVIER = "XAVIER"
AUDREY = "AUDREY"
AGENTS = (DEREK, XAVIER, AUDREY)

# ── STATES (agent_status.state CHECK) ───────────────────────────────────
S_IDLE = "IDLE"
S_EVALUATING = "EVALUATING"
S_WAITING_FOR_EVIDENCE = "WAITING_FOR_EVIDENCE"
S_WAITING_FOR_PROVIDER = "WAITING_FOR_PROVIDER"
S_BLOCKED = "BLOCKED"
S_DECISION_RECORDED = "DECISION_RECORDED"
S_RECOVERING = "RECOVERING"
S_FAILED = "FAILED"
STATES = (S_IDLE, S_EVALUATING, S_WAITING_FOR_EVIDENCE, S_WAITING_FOR_PROVIDER,
          S_BLOCKED, S_DECISION_RECORDED, S_RECOVERING, S_FAILED)

TASK_STATUSES = ("OPEN", "IN_PROGRESS", "WAITING", "CANDIDATE_READY",
                 "EVALUATING", "REJECTED", "APPROVAL_READY", "APPROVED",
                 "RELEASED", "ROLLED_BACK", "CLOSED_NO_CHANGE", "CANCELLED")
POLICY_STATES = ("ACTIVE", "CANDIDATE", "REJECTED", "RETIRED")

SOURCE_ACTIVE_POLICY = "ACTIVE_POLICY"
SOURCE_CODE_DEFAULT = "CODE_DEFAULT"

R_UNKNOWN_AGENT = "THAT_IS_NOT_ONE_OF_THE_THREE_AGENTS"
R_UNKNOWN_STATE = "THAT_IS_NOT_AN_AGENT_STATE"
R_UNKNOWN_STATUS = "THAT_IS_NOT_A_TASK_STATUS"
R_NO_SUCH_TASK = "NO_SUCH_AGENT_TASK"
R_WRITE_FAILED = "AGENT_REGISTRY_WRITE_FAILED"
R_READ_FAILED = "AGENT_REGISTRY_READ_FAILED"
R_EVIDENCE_NOT_A_LIST = "EVIDENCE_REFS_MUST_BE_A_LIST_OF_REFERENCES"

# ═════════════════════════════════════════════════════════════════════
# TOOLS: every capability an agent could be granted, named once
# ═════════════════════════════════════════════════════════════════════
#
# A permission is a NAME from this catalogue. Nothing here grants a
# capability by itself -- the code paths are what they are -- but every
# agent-facing module checks `permits()` before acting, the UI shows these
# lists verbatim, and the tests pin them.
TOOLS: dict[str, str] = {
    # reads
    "read.catalogue": "the venue catalogue (us_premap, markets)",
    "read.prices": "provider odds and venue books through the existing readers",
    "read.valuations": "the entry lane's valuation rows (read only)",
    "read.funded_book": "bettor_funded_intents / fills / economics (read only)",
    "read.xavier_records": "bettor_xavier_decisions / execution events",
    "read.all": "every table and record, read only",
    # records an agent may write about its own work
    "write.entry_decisions": "agent_decisions rows of Derek's entry verdicts",
    "write.management_decisions": ("bettor_xavier_decisions via "
                                   "bettor_xavier.record_decision"),
    "write.agent_audits": "Audrey's audit/report rows",
    "write.agent_tasks": "agent_tasks and agent_task_events",
    "write.directives": "owner directives recorded by Audrey",
    "write.policy_candidates": ("agent_policy_versions rows in state "
                                "CANDIDATE only (never ACTIVE)"),
    # the ONLY two order-bearing capabilities, each through the existing path
    "request.funded_entry": (
        "an entry REQUEST through the one real entry path: "
        "workers/ext_pinnacle_loop._funded_attempt -> "
        "bettor_funded_execution.submit_for_decision, gated by "
        "derek_policy.gate_for_funded_entry and every existing rail"),
    "dispatch.xavier_claim": (
        "a management dispatch through the existing claim path: "
        "bettor_funded_pair_cycle.pass_once -> bettor_xavier.claim_dispatch "
        "-> record_execution_event, under the group lock and the one "
        "execution lock"),
    # NEVER granted to any agent
    "order.submit_direct": "calling a venue adapter's create outside the paths",
    "order.cancel_direct": "calling a venue adapter's cancel outside the paths",
    "deploy": "releasing code or changing the running process",
    "write.risk_limits": "approved limits, rails, loss stops",
    "write.credentials": "API keys, tokens, passwords",
    "write.account_authority": "account binding, owner/system authorization",
    "write.approvals": "approving a limit, model or policy version",
    "write.submission_switches": ("FUNDED_SUBMISSION_ENABLED, "
                                  "REAL_ORDER_SUBMISSION_ENABLED, "
                                  "FUNDED_EXIT_SUBMISSION_ENABLED"),
}

#: Denied to EVERY agent, whatever else it is granted.
NEVER_GRANTED = ("order.submit_direct", "order.cancel_direct", "deploy",
                 "write.risk_limits", "write.credentials",
                 "write.account_authority", "write.approvals",
                 "write.submission_switches")

IDENTITIES: dict[str, dict] = {
    DEREK: {
        "display_name": "Derek",
        "role": "DISCOVERY_AND_ENTRY",
        "mandate": (
            "Find admissible entries. Read the catalogue and prices, record "
            "each entry verdict with its evidence, and REQUEST an entry only "
            "through the existing funded entry path, where the owner's entry "
            "policy (derek_policy.gate_for_funded_entry) and every existing "
            "rail must agree. Owns nothing after a fill: confirmed filled "
            "quantity is handed to Xavier."),
        "policy_key": "entry",
        "tool_permissions": {
            "allowed": ["read.catalogue", "read.prices",
                        "read.valuations", "write.entry_decisions",
                        "write.agent_tasks", "request.funded_entry"],
            "denied": ["dispatch.xavier_claim", "write.management_decisions",
                       "write.agent_audits", "write.directives",
                       "write.policy_candidates", *NEVER_GRANTED],
            "order_path": "request.funded_entry",
        },
    },
    XAVIER: {
        "display_name": "Xavier",
        "role": "POSITION_MANAGEMENT_AND_EXITS",
        "mandate": (
            "Manage every position whose entry has CONFIRMED filled quantity, "
            "and every obligation it created, until reconciled. Read the "
            "book, persist each management decision before any action, and "
            "dispatch only through the existing claim path. Never opens a "
            "new entry."),
        "policy_key": "management",
        "tool_permissions": {
            "allowed": ["read.funded_book", "read.xavier_records",
                        "read.prices", "write.management_decisions",
                        "write.agent_tasks", "dispatch.xavier_claim"],
            "denied": ["request.funded_entry", "write.entry_decisions",
                       "write.agent_audits", "write.directives",
                       "write.policy_candidates", *NEVER_GRANTED],
            "order_path": "dispatch.xavier_claim",
        },
    },
    AUDREY: {
        "display_name": "Audrey",
        "role": "AUDIT_COMMUNICATION_AND_IMPROVEMENT",
        "mandate": (
            "Audit what Derek and Xavier did against the authoritative "
            "records, report to management, record directives and tasks, and "
            "propose policy/model CANDIDATES for owner approval. Reads "
            "everything; holds NO order tool, cannot deploy and cannot "
            "write risk limits or approvals."),
        "policy_key": "audit",
        "tool_permissions": {
            "allowed": ["read.all", "write.agent_audits", "write.agent_tasks",
                        "write.directives", "write.policy_candidates"],
            "denied": ["request.funded_entry", "dispatch.xavier_claim",
                       "write.entry_decisions", "write.management_decisions",
                       *NEVER_GRANTED],
            "order_path": None,
        },
    },
}


def _model_version(agent_id: str) -> str:
    """WHAT MODEL (IF ANY) EACH AGENT'S VERDICTS REST ON, named from code."""
    try:
        if agent_id == DEREK:
            from .. import bettor_pinnacle_devig as _d
            return "PINNACLE_DEVIG:%s" % getattr(_d, "VERSION", "UNNAMED")
        if agent_id == XAVIER:
            from .. import bettor_xavier as _x
            return "XAVIER:%s" % getattr(_x, "VERSION", "UNNAMED")
    except Exception as exc:                                    # noqa: BLE001
        return "UNREADABLE:%s" % type(exc).__name__
    return "NO_MODEL_DETERMINISTIC_AUDIT"


def permits(agent_id: str, tool: str) -> bool:
    """Is `tool` on `agent_id`'s allow list and not on its deny list? A tool
    denied to every agent is never permitted. Pure."""
    ident = IDENTITIES.get(str(agent_id or "").upper())
    if ident is None or tool in NEVER_GRANTED:
        return False
    perms = ident["tool_permissions"]
    if tool in perms["denied"]:
        return False
    if tool in perms["allowed"]:
        return True
    return tool.startswith("read.") and "read.all" in perms["allowed"]


def _code_version_text(code_version) -> str | None:
    if code_version is None:
        return None
    if isinstance(code_version, dict):
        parts = [str(code_version.get("module") or "")]
        if code_version.get("source_sha256_12"):
            parts.append("@%s" % code_version["source_sha256_12"])
        if code_version.get("build"):
            parts.append("#%s" % code_version["build"])
        return "".join(parts) or json.dumps(code_version, default=str)
    return str(code_version)


def _dt(epoch):
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), timezone.utc)


def _epoch(v):
    if isinstance(v, datetime):
        return v.timestamp()
    return v


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _row(r) -> dict:
    """A database row as JSON-ready data: jsonb decoded, times as epochs."""
    if r is None:
        return None
    out = {}
    for k, v in dict(r).items():
        v = _epoch(v)
        if k in ("tool_permissions", "waiting_on", "dependencies", "cadence",
                 "summary", "params", "spec", "evidence", "outcome", "detail",
                 "evidence_refs"):
            v = _j(v)
        elif hasattr(v, "as_tuple"):                    # Decimal
            v = float(v)
        out[k] = v
    return out


async def _ensure_identity_row(conn, agent_id: str) -> None:
    ident = IDENTITIES[agent_id]
    await conn.execute(
        "INSERT INTO agent_identities (agent_id, display_name, mandate, "
        " policy_version, model_version, tool_permissions) "
        "VALUES ($1,$2,$3,$4,$5,$6::jsonb) ON CONFLICT (agent_id) DO NOTHING",
        agent_id, ident["display_name"], ident["mandate"],
        SOURCE_CODE_DEFAULT, _model_version(agent_id),
        json.dumps(ident["tool_permissions"]))


async def ensure_identities(conn, *, code_version=None) -> dict:
    """UPSERT all three identities (mandate, permissions, versions) and make
    sure each has a status row. An agent that has never run says so
    (IDLE / NOT_YET_RUN) rather than inheriting a success. Never raises."""
    out: dict[str, Any] = {"ok": True, "agents": {}}
    cv = _code_version_text(code_version)
    for aid in AGENTS:
        ident = IDENTITIES[aid]
        try:
            pv = await _policy_version_label(conn, aid)
            await conn.execute(
                "INSERT INTO agent_identities (agent_id, display_name, "
                " mandate, policy_version, model_version, code_version, "
                " tool_permissions, updated_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb, now()) "
                "ON CONFLICT (agent_id) DO UPDATE SET "
                " display_name=EXCLUDED.display_name, mandate=EXCLUDED.mandate,"
                " policy_version=EXCLUDED.policy_version, "
                " model_version=EXCLUDED.model_version, "
                " code_version=coalesce(EXCLUDED.code_version, "
                "                       agent_identities.code_version), "
                " tool_permissions=EXCLUDED.tool_permissions, updated_at=now()",
                aid, ident["display_name"], ident["mandate"], pv,
                _model_version(aid), cv, json.dumps(ident["tool_permissions"]))
            await conn.execute(
                "INSERT INTO agent_status (agent_id, state, activity) "
                "VALUES ($1, 'IDLE', 'NOT_YET_RUN') "
                "ON CONFLICT (agent_id) DO NOTHING", aid)
            out["agents"][aid] = {"ok": True, "policy_version": pv,
                                  "code_version": cv}
        except Exception as exc:                                # noqa: BLE001
            out["ok"] = False
            out["agents"][aid] = {"ok": False, "refusal": R_WRITE_FAILED,
                                  "error": type(exc).__name__}
    return out


async def _policy_version_label(conn, agent_id: str) -> str:
    try:
        rows = await conn.fetch(
            "SELECT policy_key, version FROM agent_policy_versions "
            " WHERE agent_id=$1 AND state='ACTIVE' ORDER BY policy_key",
            agent_id)
    except Exception:                                           # noqa: BLE001
        return SOURCE_CODE_DEFAULT
    if not rows:
        return SOURCE_CODE_DEFAULT
    return ",".join("%s@%s" % (r["policy_key"], r["version"]) for r in rows)


# ═════════════════════════════════════════════════════════════════════
# HEARTBEAT AND RUNS
# ═════════════════════════════════════════════════════════════════════

async def heartbeat(conn, agent_id, *, state, activity=None, waiting_on=None,
                    dependencies=None, run: dict | None = None,
                    now: float | None = None, error: str | None = None,
                    cadence: dict | None = None) -> dict:
    """WRITE ONE AGENT'S CURRENT STATE. NEVER RAISES.

    `run` (optional) describes a run that just ended or started:
    {"started_at", "finished_at", "elapsed_s", "error"}; a finished run bumps
    `runs`, a run or `error` with an error bumps `errors` and sets
    `last_error`. The write runs in its own savepoint when the caller is in a
    transaction, so a failure here cannot abort the caller's work."""
    aid = str(agent_id or "").upper()
    if aid not in IDENTITIES:
        return {"ok": False, "refusal": R_UNKNOWN_AGENT, "agent_id": agent_id}
    if state not in STATES:
        return {"ok": False, "refusal": R_UNKNOWN_STATE, "state": state}
    at = float(now if now is not None else time.time())
    r = dict(run or {})
    err = error or r.get("error")
    finished = r.get("finished_at")
    try:
        async with conn.transaction():
            await _ensure_identity_row(conn, aid)
            await conn.execute(
                "INSERT INTO agent_status AS s (agent_id, state, activity, "
                " waiting_on, dependencies, last_heartbeat_at, "
                " last_run_started_at, last_run_finished_at, "
                " last_run_elapsed_s, runs, errors, last_error, cadence) "
                "VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,to_timestamp($6),"
                " CASE WHEN $7::float8 IS NULL THEN NULL "
                "      ELSE to_timestamp($7::float8) END,"
                " CASE WHEN $8::float8 IS NULL THEN NULL "
                "      ELSE to_timestamp($8::float8) END,"
                " $9, CASE WHEN $8::float8 IS NULL THEN 0 ELSE 1 END,"
                " CASE WHEN $10::text IS NULL THEN 0 ELSE 1 END, $10, "
                " $11::jsonb) "
                "ON CONFLICT (agent_id) DO UPDATE SET "
                " state=EXCLUDED.state, activity=EXCLUDED.activity, "
                " waiting_on=EXCLUDED.waiting_on, "
                " dependencies=EXCLUDED.dependencies, "
                " last_heartbeat_at=EXCLUDED.last_heartbeat_at, "
                " last_run_started_at=coalesce(EXCLUDED.last_run_started_at, "
                "                              s.last_run_started_at), "
                " last_run_finished_at=coalesce(EXCLUDED.last_run_finished_at,"
                "                               s.last_run_finished_at), "
                " last_run_elapsed_s=coalesce(EXCLUDED.last_run_elapsed_s, "
                "                             s.last_run_elapsed_s), "
                " runs=s.runs + EXCLUDED.runs, "
                " errors=s.errors + EXCLUDED.errors, "
                " last_error=coalesce(EXCLUDED.last_error, s.last_error), "
                " cadence=coalesce(EXCLUDED.cadence, s.cadence)",
                aid, state, None if activity is None else str(activity)[:500],
                json.dumps(waiting_on, default=str)
                if waiting_on is not None else None,
                json.dumps(dependencies, default=str)
                if dependencies is not None else None,
                at, r.get("started_at"), finished,
                r.get("elapsed_s"), None if err is None else str(err)[:500],
                json.dumps(cadence, default=str)
                if cadence is not None else None)
        return {"ok": True, "agent_id": aid, "state": state,
                "activity": activity, "at": at}
    except Exception as exc:                                    # noqa: BLE001
        log.warning("agents: heartbeat for %s failed (%s)", aid,
                    type(exc).__name__)
        return {"ok": False, "refusal": R_WRITE_FAILED, "agent_id": aid,
                "error": type(exc).__name__}


async def start_run(conn, agent_id, run_id, *, summary=None, outcome=None,
                    now: float | None = None) -> dict:
    """Append a run (append-only; it may be finished exactly once)."""
    aid = str(agent_id or "").upper()
    if aid not in IDENTITIES:
        return {"ok": False, "refusal": R_UNKNOWN_AGENT}
    at = float(now if now is not None else time.time())
    try:
        await _ensure_identity_row(conn, aid)
        res = await conn.execute(
            "INSERT INTO agent_runs (run_id, agent_id, started_at, outcome, "
            " summary) VALUES ($1,$2,to_timestamp($3),$4,$5::jsonb) "
            "ON CONFLICT (run_id) DO NOTHING",
            str(run_id), aid, at, outcome,
            json.dumps(summary or {}, default=str))
        return {"ok": True, "run_id": str(run_id),
                "created": res.endswith("1")}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_WRITE_FAILED,
                "error": type(exc).__name__}


async def finish_run(conn, agent_id, run_id, *, outcome, summary=None,
                     now: float | None = None) -> dict:
    """Finish a started run once. A second finish is refused by the table."""
    at = float(now if now is not None else time.time())
    try:
        res = await conn.execute(
            "UPDATE agent_runs SET finished_at=to_timestamp($3), outcome=$4, "
            " summary=summary || $5::jsonb "
            " WHERE run_id=$1 AND agent_id=$2 AND finished_at IS NULL",
            str(run_id), str(agent_id or "").upper(), at, outcome,
            json.dumps(summary or {}, default=str))
        return {"ok": res.endswith("1"), "run_id": str(run_id),
                "finished": res.endswith("1")}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_WRITE_FAILED,
                "error": type(exc).__name__}


# ═════════════════════════════════════════════════════════════════════
# POLICY VERSIONS
# ═════════════════════════════════════════════════════════════════════

async def active_policy(conn, agent_id, policy_key, *, default: dict) -> dict:
    """THE BINDING PARAMETERS for (agent, key): the ACTIVE version's, or the
    code default -- labelled source='CODE_DEFAULT', never presented as an
    approved policy. A failed read falls back to the default and says so."""
    base = {"agent_id": str(agent_id).upper(), "policy_key": policy_key,
            "source": SOURCE_CODE_DEFAULT, "version": SOURCE_CODE_DEFAULT,
            "params": dict(default or {}), "approved_by": None,
            "approved_at": None}
    try:
        r = await conn.fetchrow(
            "SELECT version, params, approved_by, approved_at "
            "  FROM agent_policy_versions WHERE agent_id=$1 "
            "   AND policy_key=$2 AND state='ACTIVE'",
            str(agent_id).upper(), policy_key)
    except Exception as exc:                                    # noqa: BLE001
        return dict(base, read_error=type(exc).__name__,
                    why="the policy table could not be read; the code "
                        "default is used and labelled so")
    if r is None:
        return base
    return dict(base, source=SOURCE_ACTIVE_POLICY, version=r["version"],
                params=_j(r["params"]) or {}, approved_by=r["approved_by"],
                approved_at=_epoch(r["approved_at"]))


# ═════════════════════════════════════════════════════════════════════
# TASKS
# ═════════════════════════════════════════════════════════════════════

async def create_task(conn, *, assignee, created_by, kind, title, spec,
                      directive_id=None, evidence=None, task_id=None,
                      now: float | None = None) -> dict:
    """OPEN A TASK, idempotently on `task_id`: a replay returns the existing
    task (created=False) and writes nothing."""
    aid = str(assignee or "").upper()
    if aid not in IDENTITIES:
        return {"ok": False, "refusal": R_UNKNOWN_AGENT, "assignee": assignee}
    tid = str(task_id) if task_id else "task-%s" % uuid.uuid4().hex
    at = float(now if now is not None else time.time())
    try:
        async with conn.transaction():
            await _ensure_identity_row(conn, aid)
            res = await conn.execute(
                "INSERT INTO agent_tasks (task_id, assignee, created_by, kind,"
                " title, spec, status, directive_id, evidence, created_at, "
                " updated_at) VALUES ($1,$2,$3,$4,$5,$6::jsonb,'OPEN',$7,"
                " $8::jsonb,to_timestamp($9),to_timestamp($9)) "
                "ON CONFLICT (task_id) DO NOTHING",
                tid, aid, str(created_by), str(kind), str(title),
                json.dumps(spec or {}, default=str), directive_id,
                json.dumps(evidence or [], default=str), at)
            created = res.endswith("1")
            if created:
                await conn.execute(
                    "INSERT INTO agent_task_events (task_id, at, kind, actor,"
                    " detail) VALUES ($1,to_timestamp($2),'CREATED',$3,"
                    " $4::jsonb)", tid, at, str(created_by),
                    json.dumps({"assignee": aid, "kind": kind,
                                "directive_id": directive_id}, default=str))
        got = await task(conn, tid)
        return {"ok": True, "created": created, "task_id": tid,
                "task": (got or {}).get("task")}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_WRITE_FAILED,
                "error": type(exc).__name__, "task_id": tid}


async def task_event(conn, task_id, *, kind, actor, detail, status=None,
                     now: float | None = None) -> dict:
    """APPEND an event to a task's history, optionally moving its status in
    the same transaction."""
    if status is not None and status not in TASK_STATUSES:
        return {"ok": False, "refusal": R_UNKNOWN_STATUS, "status": status}
    at = float(now if now is not None else time.time())
    try:
        async with conn.transaction():
            cur = await conn.fetchrow(
                "SELECT status FROM agent_tasks WHERE task_id=$1 FOR UPDATE",
                str(task_id))
            if cur is None:
                return {"ok": False, "refusal": R_NO_SUCH_TASK,
                        "task_id": task_id}
            d = dict(detail or {})
            if status is not None:
                d.setdefault("status_from", cur["status"])
                d.setdefault("status_to", status)
            eid = await conn.fetchval(
                "INSERT INTO agent_task_events (task_id, at, kind, actor, "
                " detail) VALUES ($1,to_timestamp($2),$3,$4,$5::jsonb) "
                "RETURNING event_id", str(task_id), at, str(kind), str(actor),
                json.dumps(d, default=str))
            await conn.execute(
                "UPDATE agent_tasks SET status=coalesce($2, status), "
                " updated_at=to_timestamp($3) WHERE task_id=$1",
                str(task_id), status, at)
        return {"ok": True, "task_id": str(task_id), "event_id": eid,
                "status": status or cur["status"]}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_WRITE_FAILED,
                "error": type(exc).__name__}


# ═════════════════════════════════════════════════════════════════════
# THE DECISION INDEX
# ═════════════════════════════════════════════════════════════════════

def decision_ref_for(*, agent_id, kind, subject, decided_at) -> str:
    raw = "%s|%s|%s|%.6f" % (str(agent_id).upper(), kind, subject,
                             float(decided_at))
    return "adr:%s" % hashlib.sha256(raw.encode()).hexdigest()[:24]


async def link_decision(conn, *, agent_id, kind, subject, decided_at, verdict,
                        summary, evidence_refs, decision_ref=None) -> dict:
    """INDEX an agent decision, idempotently. `evidence_refs` is a list of
    {"kind", "id", "href"} pointing at the authoritative records; this row is
    never a copy of their economics."""
    aid = str(agent_id or "").upper()
    if aid not in IDENTITIES:
        return {"ok": False, "refusal": R_UNKNOWN_AGENT}
    if not isinstance(evidence_refs, (list, tuple)):
        return {"ok": False, "refusal": R_EVIDENCE_NOT_A_LIST}
    ref = decision_ref or decision_ref_for(agent_id=aid, kind=kind,
                                           subject=subject,
                                           decided_at=decided_at)
    try:
        await _ensure_identity_row(conn, aid)
        res = await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " subject, decided_at, verdict, summary, evidence_refs) "
            "VALUES ($1,$2,$3,$4,to_timestamp($5),$6,$7::jsonb,$8::jsonb) "
            "ON CONFLICT (decision_ref) DO NOTHING",
            ref, aid, str(kind), None if subject is None else str(subject),
            float(decided_at), verdict, json.dumps(summary or {}, default=str),
            json.dumps(list(evidence_refs), default=str))
        return {"ok": True, "decision_ref": ref, "created": res.endswith("1")}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_WRITE_FAILED,
                "error": type(exc).__name__, "decision_ref": ref}


# ═════════════════════════════════════════════════════════════════════
# READERS (raise on a failed read: the API turns that into UNAVAILABLE)
# ═════════════════════════════════════════════════════════════════════

async def status_of(conn, agent_id) -> dict | None:
    """Identity + status for one agent, or None when it was never registered.
    A registered agent with no status row reads state None, not IDLE."""
    aid = str(agent_id or "").upper()
    r = await conn.fetchrow(
        "SELECT i.agent_id, i.display_name, i.mandate, i.policy_version, "
        "       i.model_version, i.code_version, i.tool_permissions, "
        "       i.updated_at AS identity_updated_at, s.state, s.activity, "
        "       s.waiting_on, s.dependencies, s.last_heartbeat_at, "
        "       s.last_run_started_at, s.last_run_finished_at, "
        "       s.last_run_elapsed_s, s.runs, s.errors, s.last_error, "
        "       s.cadence "
        "  FROM agent_identities i LEFT JOIN agent_status s USING (agent_id) "
        " WHERE i.agent_id=$1", aid)
    if r is None:
        return None
    out = _row(r)
    out["role"] = IDENTITIES.get(aid, {}).get("role")
    return out


async def tasks(conn, *, assignee=None, status=None, limit=50) -> list:
    rows = await conn.fetch(
        "SELECT * FROM agent_tasks WHERE ($1::text IS NULL OR assignee=$1) "
        "   AND ($2::text IS NULL OR status=$2) "
        " ORDER BY updated_at DESC, task_id LIMIT $3",
        None if assignee is None else str(assignee).upper(), status,
        max(1, min(int(limit or 50), 500)))
    return [_task_row(r) for r in rows]


def _task_row(r) -> dict:
    t = _row(r)
    t["evidence_links"] = [{"kind": "agent_tasks", "id": t["task_id"],
                            "href": "/api/command/agents/tasks/%s"
                            % t["task_id"]}]
    return t


async def task(conn, task_id) -> dict | None:
    r = await conn.fetchrow("SELECT * FROM agent_tasks WHERE task_id=$1",
                            str(task_id))
    if r is None:
        return None
    ev = await conn.fetch(
        "SELECT event_id, task_id, at, kind, actor, detail "
        "  FROM agent_task_events WHERE task_id=$1 ORDER BY event_id",
        str(task_id))
    return {"task": _task_row(r), "events": [_row(e) for e in ev]}


async def decisions(conn, *, agent_id=None, limit=50) -> list:
    rows = await conn.fetch(
        "SELECT * FROM agent_decisions "
        " WHERE ($1::text IS NULL OR agent_id=$1) "
        " ORDER BY decided_at DESC, decision_ref LIMIT $2",
        None if agent_id is None else str(agent_id).upper(),
        max(1, min(int(limit or 50), 500)))
    out = []
    for r in rows:
        d = _row(r)
        d["evidence"] = list(d.get("evidence_refs") or [])
        out.append(d)
    return out
