"""THE AGENTS' RUNTIME SEAM IN THE SCHEDULED WORKER, GUARDED.

`workers/ext_pinnacle_loop.py` calls the functions below at a handful of
clearly marked points. Every one of them NEVER RAISES into the worker:

  * a stream hook that is not installed (ImportError / AttributeError) is
    recorded as that agent's WAITING_FOR_PROVIDER with the exception type;
  * a hook that raises (or overruns its bound) is recorded as FAILED with the
    exception type;
  * either way the cycle, the servicing pass, recovery and settlement carry
    on exactly as if the hook did not exist.

WHAT THE HOOKS CAN NEVER DO: hold the execution lock (the slow half runs
after the servicing pass releases it), send an order (only the Derek entry
gate is consulted on an order path, and it can only REFUSE), or change the
deterministic work of the pass (their results are reported, not read).

States are TRUTHFUL: an empty servicing pass is IDLE with
`SCHEDULER_RAN_NO_OWNED_INVENTORY`, never position management; an
unreadable count is unknown, never zero.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import time
from typing import Any

from . import registry as R

log = logging.getLogger(__name__)

PACKAGE = "sportsassets.agents"

#: Per-pass hooks (Derek's after_cycle, Xavier's ladder) and the slow half
#: (Audrey's daily audit, improvement). A bound, so a wedged hook cannot hold
#: the servicing task: an overrun is FAILED(TimeoutError), never a wait.
HOOK_TIMEOUT_S = 60.0
SLOW_HOOK_TIMEOUT_S = 300.0
#: The Derek entry gate sits on the one real entry path, under the execution
#: lock: it gets a short bound, and an overrun REFUSES the entry.
GATE_TIMEOUT_S = 10.0

A_HOOK_NOT_INSTALLED = "HOOK_NOT_INSTALLED"
A_HOOK_RAISED = "HOOK_RAISED"
A_NO_OWNED_INVENTORY = "SCHEDULER_RAN_NO_OWNED_INVENTORY"
A_FUNDED_LANE_NOT_CONFIGURED = "FUNDED_LANE_NOT_CONFIGURED_NOTHING_TO_MANAGE"
A_EXECUTION_AUTHORITY_BUSY = "EXECUTION_LOCK_HELD_THIS_PASS_SENT_NOTHING"

#: The Derek gate's refusals, returned by `_funded_attempt` by name.
R_DEREK_GATE_REFUSED = "DEREK_ENTRY_POLICY_DID_NOT_SAY_ENTER_SO_NOTHING_IS_SENT"
R_DEREK_GATE_UNAVAILABLE = (
    "DEREK_ENTRY_POLICY_UNAVAILABLE_SO_NOTHING_IS_SENT")
VERDICT_ENTER = "ENTER"


def _now(now) -> float:
    return float(now if now is not None else time.time())


def _err(exc) -> str:
    return "%s: %s" % (type(exc).__name__, str(exc)[:200])


def _resolve(module: str, func: str):
    """(callable, None, False) or (None, exception, missing) -- a missing
    module or attribute is reported (missing=True), not raised. A
    ModuleNotFoundError for a DEPENDENCY of an installed stream module is a
    failure of that module (missing=False), not absence."""
    full = "%s.%s" % (PACKAGE, module)
    try:
        mod = importlib.import_module(full)
    except ModuleNotFoundError as exc:
        if exc.name in (full, PACKAGE):
            return None, exc, True
        return None, exc, False
    except Exception as exc:                                    # noqa: BLE001
        return None, exc, False
    fn = getattr(mod, func, None)
    if fn is None or not callable(fn):
        return None, AttributeError("%s has no callable %s" % (full, func)), \
            True
    return fn, None, False


async def call_hook(conn, agent_id: str, module: str, func: str, *,
                    kwargs: dict | None = None, now: float | None = None,
                    timeout_s: float | None = None,
                    record_failure: bool = True) -> dict:
    """CALL `sportsassets.agents.<module>.<func>(conn, **kwargs, now=now)`.

    Returns {"ok", "hook", "result", "installed", "error", "error_type",
    "state_recorded", "elapsed_s"}. NEVER RAISES (CancelledError excepted, so
    a task shutdown still shuts down). On a missing hook the agent is set
    WAITING_FOR_PROVIDER, on a failure FAILED, both with the exception type."""
    at = _now(now)
    hook = "%s.%s.%s" % (PACKAGE, module, func)
    out: dict[str, Any] = {"ok": False, "hook": hook, "agent_id": agent_id,
                           "result": None, "installed": True, "error": None,
                           "error_type": None, "state_recorded": None}
    t0 = time.monotonic()
    fn, exc, missing = _resolve(module, func)
    if fn is None:
        out.update(installed=not missing, error=_err(exc),
                   error_type=type(exc).__name__)
    else:
        try:
            # THE BOUND, read at call time (None -> HOOK_TIMEOUT_S).
            bound = HOOK_TIMEOUT_S if timeout_s is None else timeout_s
            coro = fn(conn, **dict(kwargs or {}), now=at)
            res = (await asyncio.wait_for(coro, bound)
                   if bound else await coro)
            out.update(ok=True, result=res if isinstance(res, dict)
                       else {"returned": res})
        except asyncio.CancelledError:
            raise
        except Exception as exc2:                               # noqa: BLE001
            out.update(error=_err(exc2), error_type=type(exc2).__name__)
    out["elapsed_s"] = round(time.monotonic() - t0, 3)
    if not out["ok"] and record_failure:
        state = R.S_WAITING_FOR_PROVIDER if not out["installed"] \
            else R.S_FAILED
        activity = "%s:%s" % (A_HOOK_NOT_INSTALLED if not out["installed"]
                              else A_HOOK_RAISED, hook)
        hb = await R.heartbeat(
            conn, agent_id, state=state, activity=activity, now=at,
            error=out["error"], waiting_on={"hook": hook,
                                            "error_type": out["error_type"]},
            run={"started_at": at, "finished_at": at + out["elapsed_s"],
                 "elapsed_s": out["elapsed_s"]})
        out["state_recorded"] = state if hb.get("ok") else None
        log.warning("agents: %s hook %s did not run cleanly (%s)", agent_id,
                    hook, out["error_type"])
    return out


def _digest(hook: dict | None) -> dict | None:
    if not isinstance(hook, dict):
        return None
    res = hook.get("result")
    return {"ok": hook.get("ok"), "hook": hook.get("hook"),
            "installed": hook.get("installed"),
            "error_type": hook.get("error_type"), "error": hook.get("error"),
            "state_recorded": hook.get("state_recorded"),
            "elapsed_s": hook.get("elapsed_s"),
            "result_keys": sorted(res)[:20] if isinstance(res, dict) else None}


# ═════════════════════════════════════════════════════════════════════
# RUN(): IDENTITIES, ONCE, AFTER THE WRITER LOCK
# ═════════════════════════════════════════════════════════════════════

async def ensure_identities(conn, *, code_version=None) -> dict:
    try:
        return await R.ensure_identities(conn, code_version=code_version)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "error": _err(exc)}


# ═════════════════════════════════════════════════════════════════════
# DEREK: THE COLLECTION CYCLE
# ═════════════════════════════════════════════════════════════════════

async def derek_cycle_started(conn, *, now: float | None = None) -> dict:
    return await R.heartbeat(conn, R.DEREK, state=R.S_EVALUATING,
                             activity="COLLECTION_CYCLE_STARTED", now=now,
                             run={"started_at": _now(now)})


def derek_end_state(cycle: dict | None, hook: dict | None) -> tuple:
    """(state, activity, waiting_on) that is TRUE of this cycle. Pure.

    A hook that failed keeps FAILED; a hook that states its own end state
    (one of the registry's states) is believed; otherwise the cycle's own
    result decides: stopped/blocked -> BLOCKED naming why; valuation rows
    written -> DECISION_RECORDED; nothing evaluated -> WAITING_FOR_EVIDENCE
    naming the cycle's label."""
    c = dict(cycle or {})
    h = dict(hook or {})
    if h and not h.get("ok"):
        return ((R.S_WAITING_FOR_PROVIDER if not h.get("installed")
                 else R.S_FAILED),
                "%s:%s" % (A_HOOK_NOT_INSTALLED if not h.get("installed")
                           else A_HOOK_RAISED, h.get("hook")),
                {"error_type": h.get("error_type")})
    res = h.get("result") if isinstance(h.get("result"), dict) else {}
    if res.get("state") in R.STATES:
        return res["state"], res.get("activity") or "REPORTED_BY_DEREK", \
            res.get("waiting_on")
    if not c.get("ran"):
        why = c.get("why") or c.get("state") or "CYCLE_DID_NOT_RUN"
        return R.S_BLOCKED, "ENTRY_BLOCKED:%s" % why, {"why": why}
    written = int(c.get("written") or 0)
    if written > 0:
        return (R.S_DECISION_RECORDED,
                "VALUATION_ROWS_WRITTEN:%d" % written, None)
    label = c.get("cycle_label") or "NOTHING_WRITTEN"
    return (R.S_WAITING_FOR_EVIDENCE, "NO_DECISION_RECORDED:%s" % label,
            {"evaluated": c.get("evaluated"), "refusals": dict(
                list((c.get("refusals") or {}).items())[:10])})


async def derek_cycle_finished(conn, *, cycle: dict,
                               now: float | None = None) -> dict:
    """`derek.after_cycle(conn, cycle=<cycle result>, now=now)`, guarded,
    then Derek's truthful end state. Returns a digest for the heartbeat."""
    at = _now(now)
    hook = await call_hook(conn, R.DEREK, "derek", "after_cycle",
                           kwargs={"cycle": cycle}, now=at)
    state, activity, waiting = derek_end_state(cycle, hook)
    if hook.get("ok") or hook.get("state_recorded") is None:
        await R.heartbeat(conn, R.DEREK, state=state, activity=activity,
                          waiting_on=waiting, now=at,
                          run={"finished_at": at,
                               "elapsed_s": cycle.get("elapsed_s")})
    return {"state": state, "activity": activity, "hook": _digest(hook)}


async def gate_for_funded_entry(conn, rec: dict, *,
                                now: float | None = None) -> dict:
    """THE OWNER'S ENTRY POLICY, BINDING ON THE ONE REAL ENTRY PATH.

    `derek_policy.gate_for_funded_entry(conn, rec, now=now)` must answer
    verdict 'ENTER' for the entry to proceed. Anything else -- another
    verdict, a raise, an overrun, the module missing -- REFUSES, by name,
    and nothing is sent. Returns {"enter": bool, "refusal", "gate"}."""
    at = _now(now)
    fn, exc, missing = _resolve("derek_policy", "gate_for_funded_entry")
    if fn is None:
        await R.heartbeat(
            conn, R.DEREK, state=R.S_WAITING_FOR_PROVIDER, now=at,
            activity="%s:%s.derek_policy.gate_for_funded_entry"
            % (A_HOOK_NOT_INSTALLED, PACKAGE), error=_err(exc))
        return {"enter": False, "refusal": R_DEREK_GATE_UNAVAILABLE,
                "gate": {"installed": not missing,
                         "error_type": type(exc).__name__}}
    try:
        got = await asyncio.wait_for(fn(conn, rec, now=at), GATE_TIMEOUT_S)
    except asyncio.CancelledError:
        raise
    except Exception as exc2:                                   # noqa: BLE001
        await R.heartbeat(conn, R.DEREK, state=R.S_FAILED, now=at,
                          activity="%s:derek_policy.gate_for_funded_entry"
                          % A_HOOK_RAISED, error=_err(exc2))
        return {"enter": False, "refusal": R_DEREK_GATE_UNAVAILABLE,
                "gate": {"installed": True,
                         "error_type": type(exc2).__name__,
                         "error": _err(exc2)}}
    got = got if isinstance(got, dict) else {"returned": got}
    if got.get("verdict") == VERDICT_ENTER:
        return {"enter": True, "refusal": None, "gate": got}
    return {"enter": False,
            "refusal": R_DEREK_GATE_REFUSED,
            "gate": got}


# ═════════════════════════════════════════════════════════════════════
# XAVIER: EVERY SERVICING PASS
# ═════════════════════════════════════════════════════════════════════

async def _bound_account(conn) -> tuple:
    from .. import bettor_funded_activation as _FA
    bound = _FA._obj(await _FA._state(conn, _FA.ACCOUNT_KEY)) or {}
    return (str(bound.get("account_id") or "").strip() or None,
            str(bound.get("venue") or "").strip() or None)


async def _unresolved_counts(conn, account_id, venue) -> dict:
    """Lost acknowledgements and unanswered dispatch claims -- the reasons
    Xavier is RECOVERING. Raises on a failed read (unknown is not zero)."""
    from .. import bettor_xavier as _XV
    lost = int(await conn.fetchval(
        "SELECT count(*) FROM bettor_funded_intents WHERE state='UNRESOLVED' "
        "   AND account_id=$1 AND venue=$2", account_id, venue))
    claims = await _XV.unresolved_claims(conn, account_id=account_id,
                                         venue=venue)
    return {"lost_acknowledgements": lost,
            "unresolved_dispatch_claims": len(claims or [])}


def _decisions_recorded(svc) -> int:
    pc = (svc or {}).get("pair_cycle") if isinstance(svc, dict) else None
    briefs = (pc or {}).get("xavier") or []
    return sum(1 for b in briefs if isinstance(b, dict) and b.get("recorded"))


def xavier_state(*, res: dict | None, svc, owned, unresolved: dict | None,
                 decisions: int, read_error: str | None = None) -> tuple:
    """(state, activity, waiting_on) TRUE of this servicing pass. Pure.

    Order of precedence: a pass that raised -> FAILED; recovery owed ->
    RECOVERING; a decision row written -> DECISION_RECORDED; nothing owned ->
    IDLE / SCHEDULER_RAN_NO_OWNED_INVENTORY; owned but nothing recorded ->
    WAITING_FOR_EVIDENCE; a count that could not be read is named, never
    taken as zero."""
    r = dict(res or {})
    if not r.get("ran"):
        return (R.S_BLOCKED, A_EXECUTION_AUTHORITY_BUSY
                if r.get("refusal") else "SERVICING_PASS_DID_NOT_RUN",
                {"refusal": r.get("refusal")})
    if svc is None:
        return R.S_IDLE, A_FUNDED_LANE_NOT_CONFIGURED, None
    if isinstance(svc, dict) and svc.get("ok") is False and str(
            svc.get("refusal") or "").endswith("_RAISED"):
        return (R.S_FAILED, "SERVICING_RAISED:%s" % svc.get("refusal"),
                {"error": svc.get("error")})
    u = dict(unresolved or {})
    if any(int(v or 0) > 0 for v in u.values()):
        return R.S_RECOVERING, "RECOVERY_OWED", u
    if decisions > 0:
        return (R.S_DECISION_RECORDED,
                "MANAGEMENT_DECISIONS_RECORDED:%d" % decisions,
                {"owned_open_positions": owned})
    if owned is None:
        return (R.S_WAITING_FOR_EVIDENCE, "OWNED_INVENTORY_UNREADABLE",
                {"read_error": read_error})
    if int(owned) == 0:
        return R.S_IDLE, A_NO_OWNED_INVENTORY, None
    return (R.S_WAITING_FOR_EVIDENCE, "OWNED_INVENTORY_NO_DECISION_RECORDED",
            {"owned_open_positions": owned})


async def xavier_pass_finished(conn, *, res: dict,
                               now: float | None = None) -> dict:
    """AFTER EVERY SERVICING PASS (outside the execution lock): repair any
    inventory without a manager, the optional ladder hook, and Xavier's
    truthful heartbeat. Never raises."""
    at = _now(now)
    out: dict[str, Any] = {"at": at}
    if not (res or {}).get("ran"):
        # ANOTHER PASS (OR AN ENTRY ATTEMPT) HOLDS THE AUTHORITY AND WILL
        # REPORT ITS OWN STATE; a skipped pass does not overwrite it.
        out["state"] = None
        out["skipped"] = A_EXECUTION_AUTHORITY_BUSY
        return out
    try:
        from . import handoff as _AH
        out["handoff_reconciliation"] = await _AH.reconcile_unowned(
            conn, now=at)
    except Exception as exc:                                    # noqa: BLE001
        out["handoff_reconciliation"] = {"ok": False, "error": _err(exc)}
    svc = res.get("funded_service")
    pc = svc.get("pair_cycle") if isinstance(svc, dict) else None
    if isinstance(pc, dict):
        review = {"pair_cycle": pc, "xavier": pc.get("xavier") or [],
                  "responsibility": pc.get("xavier_responsibility"),
                  "considered": pc.get("considered") or [],
                  "source": res.get("source"), "at": res.get("at")}
        out["ladder"] = _digest(await call_hook(
            conn, R.XAVIER, "xavier_ladder", "after_review",
            kwargs={"review": review}, now=at, record_failure=False))
    owned, unresolved, read_error = None, None, None
    try:
        acct, venue = await _bound_account(conn)
        if acct and venue:
            from . import handoff as _AH
            owned = await _AH.owned_open_positions(conn, account_id=acct,
                                                   venue=venue)
            unresolved = await _unresolved_counts(conn, acct, venue)
    except Exception as exc:                                    # noqa: BLE001
        read_error = type(exc).__name__
    decisions = _decisions_recorded(svc)
    state, activity, waiting = xavier_state(
        res=res, svc=svc, owned=owned, unresolved=unresolved,
        decisions=decisions, read_error=read_error)
    await R.heartbeat(
        conn, R.XAVIER, state=state, activity=activity, waiting_on=waiting,
        # THE OPTIONAL LADDER HOOK'S OUTCOME, named: its failure does not make
        # the deterministic pass a failure, and is not hidden either.
        dependencies={"xavier_ladder": out.get("ladder"),
                      "handoff_reconciliation": {
                          k: (out.get("handoff_reconciliation") or {}).get(k)
                          for k in ("ok", "examined", "created", "refreshed",
                                    "error")}},
        now=at, error=((svc or {}).get("error")
                       if state == R.S_FAILED else None),
        run={"started_at": res.get("at"), "finished_at": at,
             "elapsed_s": res.get("elapsed_s")},
        cadence={"review_interval_s": res.get("review_interval_s"),
                 "source": res.get("source")})
    out.update(state=state, activity=activity, owned_open_positions=owned,
               unresolved=unresolved, decisions_recorded=decisions)
    return out


# ═════════════════════════════════════════════════════════════════════
# AUDREY AND IMPROVEMENT: THE SLOW HALF, OUTSIDE THE EXECUTION LOCK
# ═════════════════════════════════════════════════════════════════════

async def slow_half(conn, *, now: float | None = None) -> dict:
    """`audrey_audit.run_due` and `improvement.run_due`, guarded and bounded,
    with Audrey's heartbeat. Runs AFTER the servicing pass released the
    execution lock, so it can never delay a dispatch or recovery in that
    pass. Never raises."""
    at = _now(now)
    await R.heartbeat(conn, R.AUDREY, state=R.S_EVALUATING,
                      activity="SLOW_HALF_STARTED", now=at,
                      run={"started_at": at})
    audit = await call_hook(conn, R.AUDREY, "audrey_audit", "run_due",
                            now=at, timeout_s=SLOW_HOOK_TIMEOUT_S,
                            record_failure=False)
    improve = await call_hook(conn, R.AUDREY, "improvement", "run_due",
                              now=at, timeout_s=SLOW_HOOK_TIMEOUT_S,
                              record_failure=False)
    # Management directives follow their linked tasks' outcomes on the
    # schedule, not only when someone reads them (directives.monitor).
    directives = await call_hook(conn, R.AUDREY, "directives", "monitor",
                                 now=at, timeout_s=SLOW_HOOK_TIMEOUT_S,
                                 record_failure=False)
    hooks = (audit, improve, directives)
    raised = [h for h in hooks if not h.get("ok") and h.get("installed")]
    missing = [h for h in hooks if not h.get("ok") and not h.get("installed")]
    deps = {h["hook"]: {"ok": h.get("ok"), "installed": h.get("installed"),
                        "error_type": h.get("error_type")} for h in hooks}
    elapsed = round(sum(float(h.get("elapsed_s") or 0) for h in hooks), 3)
    error = None
    if raised or missing:
        # ONE RECORD FOR THE SLOW HALF: a hook that RAISED is FAILED (worse
        # than absent); only absent hooks is WAITING_FOR_PROVIDER. Every
        # failing hook is named with its exception type.
        worst = (raised or missing)[0]
        state = R.S_FAILED if raised else R.S_WAITING_FOR_PROVIDER
        activity = "%s:%s" % (A_HOOK_RAISED if raised
                              else A_HOOK_NOT_INSTALLED, worst.get("hook"))
        error = "; ".join(h.get("error") or "" for h in raised + missing)
    else:
        res = audit.get("result") or {}
        state = res.get("state") if res.get("state") in R.STATES else (
            R.S_DECISION_RECORDED if res.get("recorded") or res.get(
                "ran") else R.S_IDLE)
        activity = res.get("activity") or (
            "AUDIT_RAN" if state == R.S_DECISION_RECORDED
            else "AUDIT_NOT_DUE")
    await R.heartbeat(conn, R.AUDREY, state=state, activity=activity,
                      now=at, error=error, dependencies=deps,
                      run={"finished_at": at + elapsed, "elapsed_s": elapsed})
    return {"at": at, "state": state, "audrey_audit": _digest(audit),
            "improvement": _digest(improve),
            "directives": _digest(directives)}
