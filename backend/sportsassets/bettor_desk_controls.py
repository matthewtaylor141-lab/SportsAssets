"""THE OPERATING CONTROLS BEHIND THE DESK. Real writes, named limits.

WHAT THIS IS FOR. A control panel whose buttons do nothing is a mockup,
and a mockup of a kill switch is worse than no kill switch, because an
operator may believe they have one. Every action here performs the write
its label claims, on the control surface that already governs the lane,
and reads the result back before reporting it.

WHAT IT DELIBERATELY CANNOT DO. It cannot fund anything. There is no
venue submission path in it, it never touches `live_orders`, and the one
action that would GRANT authority -- resuming the funded lane -- is
refused by name rather than implemented. The direction is asymmetric on
purpose: an action that REMOVES authority (pause, halt, cancel) is always
available, and an action that GRANTS it is not available from here at all.

    pause                   stops the research lane admitting anything
    resume                  re-arms the RESEARCH lane only
    cancel-working-orders   cancels the shadow book's MODELLED orders
    halt                    the emergency stop: research lane off, funded
                            executor paused, operator stop taken, all
                            three read back
    limits                  records a PROPOSED limit set. It does not
                            change an enforced rail, and says so
    account                 records a PROPOSED funded account binding.
                            It activates nothing

AND THE PAUSED ACCOUNT STAYS PAUSED. `account` refuses the
ACCOUNTING_UNCERTAIN account by name: that account's accounting is
unresolved, it is paused for that reason, and nothing here may switch to
it or unpause it.
"""

from __future__ import annotations

import asyncio
import json
import time

from . import bettor_desk as dk
from . import bettor_external_shadow as ext

VERSION = "BETTOR_DESK_CONTROLS_V1"

#: The funded executor's own pause key -- the funded lane's existing
#: control, not a new one, and IMPORTED rather than retyped. A halt that
#: wrote a key nobody reads would report a stop that never happened; the
#: constant comes from the module the executor itself reads it from.
def _live_pause_key() -> str:
    from . import live_executor as _LE

    return _LE.PAUSE_KEY


LIVE_PAUSE_KEY = _live_pause_key()

#: Where a PROPOSED limit set and a PROPOSED account binding are recorded.
#: Both are proposals: no risk rail and no account selector reads them.
LIMITS_KEY = "bettor_funded_limits_proposal"
ACCOUNT_KEY = "bettor_funded_account_proposal"

#: The account that must not be selected or unpaused from anywhere. Its
#: accounting is unresolved; that is why it is paused.
PAUSED_ACCOUNT_MARKER = "ACCOUNTING_UNCERTAIN"

#: How long the durable operator stop may take before the halt reports it
#: as NOT TAKEN. An emergency control that waits is not a control.
OPERATOR_STOP_TIMEOUT_S = 10.0

ACTIONS = ("pause", "resume", "cancel-working-orders", "halt", "limits",
           "account", "state")

#: Refusals, by name, so a blocked action never looks like a failure.
R_FUNDED_RESUME = "FUNDED_RESUME_IS_NOT_AVAILABLE_FROM_THE_DESK"
R_PAUSED_ACCOUNT = "THE_ACCOUNTING_UNCERTAIN_ACCOUNT_STAYS_PAUSED"
R_UNKNOWN_ACTION = "UNKNOWN_CONTROL_ACTION"
R_LIMITS_INCOMPLETE = "LIMIT_SET_INCOMPLETE"

#: The limit names an activation proposal must carry. A partial set is
#: refused: "approved limits" with a missing daily loss stop is not an
#: approved limit set.
REQUIRED_LIMITS = ("capital_usd", "per_order_usd", "max_exposure_usd",
                   "daily_loss_stop_usd")


def _truthy(raw) -> bool:
    """A jsonb control value as a bool, explicitly. `bool("false")` is
    True, and that silent affirmative is what this avoids."""
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    try:
        return bool(json.loads(raw))
    except (TypeError, ValueError):
        return False


async def _write_state(conn, key: str, value) -> None:
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        key, json.dumps(value))


async def _read_state(conn, key: str):
    return await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key = $1", key)


async def _readback_bool(conn, key: str) -> dict:
    """The written value as the table now reports it, or why not.

    A WRITE IS NOT A STATE. A request that timed out, 502'd or was retried
    leaves the caller unable to say whether the row moved, so what is
    reported is the readback and not the request.
    """
    try:
        return {"confirmed": _truthy(await _read_state(conn, key)),
                "readback": "OK"}
    except Exception as exc:                                   # noqa: BLE001
        return {"confirmed": None, "readback": "FAILED",
                "error": type(exc).__name__,
                "why": ("the row was written and could not be read back, "
                        "so this control's state is UNCONFIRMED")}


# ── the actions ─────────────────────────────────────────────────────

async def pause(conn, *, by: str) -> dict:
    """Stop the research lane. Removes authority, so always available."""
    await _write_state(conn, ext.CONTROL_KEY, False)
    rb = await _readback_bool(conn, ext.CONTROL_KEY)
    return {"action": "pause", "control_key": ext.CONTROL_KEY,
            "requested": {"armed": False}, "armed_confirmed": rb["confirmed"],
            "ok": rb["confirmed"] is False, "readback": rb,
            "by": by, "at": time.time(),
            "effect": ("the scheduled research lane stops admitting at its "
                       "next cycle. Held inventory is NOT liquidated by a "
                       "pause -- pausing entry and closing positions are "
                       "different acts"),
            "funded_submission": "DISABLED"}


async def resume(conn, *, by: str) -> dict:
    """Re-arm the RESEARCH lane. It has no funded submission path."""
    await _write_state(conn, ext.CONTROL_KEY, True)
    rb = await _readback_bool(conn, ext.CONTROL_KEY)
    return {"action": "resume", "control_key": ext.CONTROL_KEY,
            "requested": {"armed": True}, "armed_confirmed": rb["confirmed"],
            "ok": rb["confirmed"] is True, "readback": rb,
            "by": by, "at": time.time(),
            "scope": "RESEARCH_SHADOW_ONLY",
            "env_flag_also_required": "EXT_PINNACLE_SHADOW",
            "effect": ("the research lane may cycle again. It submits no "
                       "order: its writer CHECKs order_submitted FALSE"),
            "does_not_resume": "any funded lane",
            "funded_submission": "DISABLED"}


CANCEL_SQL = """
    UPDATE rn1x_orders
       SET state = $1, updated_at = now()
     WHERE state = ANY($2::text[])
       AND is_modelled
       AND position_id IN (SELECT position_id FROM rn1x_positions
                            WHERE experiment_id = ANY($3::text[]))
 RETURNING order_id, position_id, side, qty::float8 AS qty,
           limit_price::float8 AS limit_price
"""


async def cancel_working_orders(conn, *, by: str, experiments) -> dict:
    """Cancel the shadow book's WORKING MODELLED orders.

    WHAT IS BEING CANCELLED, EXACTLY. These are modelled orders in our own
    ledger. NOTHING WAS SUBMITTED TO A VENUE, so there is no venue
    cancellation to make and none is claimed: the honest description is
    that the shadow book's working orders are closed out, and a funded
    order does not exist to cancel.

    `is_modelled` is in the predicate as well as the CHECK constraint, so
    this statement cannot reach a row that was not modelled even if such a
    row were ever inserted.
    """
    rows = await conn.fetch(CANCEL_SQL, dk.CANCELLED,
                            list(dk.OPEN_STATES), list(experiments))
    return {"action": "cancel-working-orders", "by": by, "at": time.time(),
            "cancelled": len(rows),
            "orders": [dict(r) for r in rows],
            "states_considered_open": list(dk.OPEN_STATES),
            "experiments": list(experiments),
            "ok": True,
            "what_this_is": ("MODELLED orders in our own ledger moved to "
                             "CANCELLED. No venue order existed, so no "
                             "venue cancellation was sent or claimed"),
            "funded_orders_cancelled": 0,
            "why_zero": ("no funded order exists: funded submission is "
                         "disabled and `live_orders` is untouched")}


async def halt(conn, *, by: str, reason: str, experiments) -> dict:
    """THE EMERGENCY STOP. Three independent controls, each read back.

    It is deliberately not one switch. The research lane, the funded
    executor and the operator stop fail for different reasons and are
    stored in different places; a halt that flipped one of them and
    reported success would be the most dangerous possible lie on this
    page.

    IT DOES NOT LIQUIDATE. Stopping admission and closing inventory are
    different acts, and an emergency stop that dumped a book into a thin
    market would be a decision nobody asked for. Working modelled orders
    ARE cancelled, because a working order is an admission that has not
    finished.
    """
    out = {"action": "halt", "by": by, "reason": reason,
           "at": time.time(), "components": {}}
    await _write_state(conn, ext.CONTROL_KEY, False)
    out["components"]["research_lane"] = dict(
        await _readback_bool(conn, ext.CONTROL_KEY),
        key=ext.CONTROL_KEY, intended=False)
    await _write_state(conn, LIVE_PAUSE_KEY, True)
    out["components"]["funded_executor_paused"] = dict(
        await _readback_bool(conn, LIVE_PAUSE_KEY),
        key=LIVE_PAUSE_KEY, intended=True)
    out["components"]["working_orders"] = await cancel_working_orders(
        conn, by=by, experiments=experiments)
    try:
        from . import calibration_store as CSTORE

        # AN EMERGENCY STOP IS BOUNDED. The operator stop lives in its own
        # store with its own pool, and an unreachable database made this
        # call sit for a hundred seconds -- measured. A halt that hangs is a
        # halt an operator cannot use, so the wait is capped and a timeout
        # is reported as NOT TAKEN rather than waited out.
        async def _take():
            await CSTORE.ensure_session()
            return await CSTORE.stop(by, reason)

        out["components"]["operator_stop"] = {
            "taken": True,
            "budget": await asyncio.wait_for(_take(),
                                             OPERATOR_STOP_TIMEOUT_S)}
    except Exception as exc:                                   # noqa: BLE001
        # A FAILED COMPONENT IS NAMED, and it makes the halt PARTIAL rather
        # than successful. An operator must never read "halted" when one of
        # the three did not take.
        out["components"]["operator_stop"] = {
            "taken": False, "error": type(exc).__name__,
            "timeout_s": OPERATOR_STOP_TIMEOUT_S,
            "why": ("the durable operator stop could not be taken. The "
                    "other components above stand on their own readbacks")}
    lane = out["components"]["research_lane"].get("confirmed")
    live = out["components"]["funded_executor_paused"].get("confirmed")
    out["ok"] = (lane is False and live is True
                 and out["components"]["operator_stop"].get("taken") is True)
    out["halted"] = out["ok"]
    out["partial"] = not out["ok"]
    out["does_not"] = ["liquidate inventory", "settle a position",
                       "cancel a venue order (none exists)"]
    return out


async def set_limits(conn, *, by: str, proposed: dict) -> dict:
    """Record a PROPOSED limit set. It changes no enforced rail.

    WHY IT IS A PROPOSAL AND NOT A SETTING. The rails this lane enforces
    are frozen in `bettor_entry_execution` and are read from there at every
    decision; a number typed into a page does not become a risk limit, and
    a page that implied otherwise would be worse than one with no fields
    at all. What this stores is the OWNER'S INTENT, for the activation
    checklist to compare against the enforced values.
    """
    missing = [k for k in REQUIRED_LIMITS
               if proposed.get(k) in (None, "")]
    if missing:
        return {"action": "limits", "ok": False,
                "refusal": R_LIMITS_INCOMPLETE, "missing": missing,
                "required": list(REQUIRED_LIMITS),
                "why": ("a partial limit set is not an approved limit set. "
                        "Nothing was stored")}
    bad = {}
    clean = {}
    for k in REQUIRED_LIMITS:
        try:
            v = float(proposed[k])
        except (TypeError, ValueError):
            bad[k] = "not a number"
            continue
        if v <= 0:
            bad[k] = "must be greater than zero"
        clean[k] = v
    if bad:
        return {"action": "limits", "ok": False,
                "refusal": R_LIMITS_INCOMPLETE, "invalid": bad,
                "why": "nothing was stored"}
    if clean["per_order_usd"] > clean["capital_usd"]:
        return {"action": "limits", "ok": False,
                "refusal": R_LIMITS_INCOMPLETE,
                "invalid": {"per_order_usd": ("cannot exceed the capital "
                                              "limit")},
                "why": "nothing was stored"}
    record = {"proposed": clean, "by": by, "at": time.time(),
              "approved": False, "enforced": False,
              "why_not_enforced": ("the enforced rails are frozen in "
                                   "bettor_entry_execution and are read "
                                   "from there at every decision. This is "
                                   "the owner's stated intent, held for "
                                   "the activation checklist")}
    await _write_state(conn, LIMITS_KEY, record)
    stored = await _read_state(conn, LIMITS_KEY)
    return {"action": "limits", "ok": stored is not None,
            "stored": (json.loads(stored) if isinstance(stored, str)
                       else stored),
            "key": LIMITS_KEY,
            "changes_an_enforced_limit": False,
            "activation_still_blocked": True}


async def set_account(conn, *, by: str, account: dict) -> dict:
    """Record a PROPOSED funded account binding. It activates nothing.

    THE PAUSED ACCOUNT IS REFUSED BY NAME. An account whose identifier or
    label carries ACCOUNTING_UNCERTAIN is not selectable here: its
    accounting is unresolved, which is why it is paused, and a control
    panel is exactly where that must not be quietly undone.
    """
    name = str(account.get("name") or "").strip()
    venue = str(account.get("venue") or "").strip()
    ident = str(account.get("account_id") or "").strip()
    if not name or not venue:
        return {"action": "account", "ok": False,
                "refusal": "ACCOUNT_NOT_NAMED",
                "why": ("an activation needs a named account and its venue. "
                        "Nothing was stored")}
    blob = " ".join((name, venue, ident)).upper()
    if PAUSED_ACCOUNT_MARKER in blob:
        return {"action": "account", "ok": False,
                "refusal": R_PAUSED_ACCOUNT,
                "why": ("that account's accounting is unresolved and it is "
                        "paused for that reason. This panel cannot select "
                        "it, switch to it or unpause it"),
                "stored": None}
    record = {"name": name, "venue": venue, "account_id": ident or None,
              "by": by, "at": time.time(), "bound": False,
              "approved": False,
              "why_not_bound": ("naming an account is not activating it. "
                                "Funded submission stays disabled until the "
                                "readiness evidence exists and the owner "
                                "authorises it")}
    await _write_state(conn, ACCOUNT_KEY, record)
    stored = await _read_state(conn, ACCOUNT_KEY)
    return {"action": "account", "ok": stored is not None,
            "stored": (json.loads(stored) if isinstance(stored, str)
                       else stored),
            "key": ACCOUNT_KEY,
            "activates_nothing": True,
            "activation_still_blocked": True}


async def state(conn) -> dict:
    """WHAT EVERY CONTROL ACTUALLY READS RIGHT NOW.

    The panel's displayed state comes from here, so what an operator sees
    is the row and not a cached assumption. A field that could not be read
    is reported as unreadable, never as a comfortable default.
    """
    out = {"version": VERSION, "at": time.time(),
           "funded_submission": "DISABLED"}
    for label, key in (("research_lane_armed", ext.CONTROL_KEY),
                       ("funded_executor_paused", LIVE_PAUSE_KEY)):
        try:
            raw = await _read_state(conn, key)
            out[label] = {"value": _truthy(raw), "key": key,
                          "row_present": raw is not None}
        except Exception as exc:                               # noqa: BLE001
            out[label] = {"value": None, "key": key,
                          "unreadable": type(exc).__name__}
    for label, key in (("limits_proposal", LIMITS_KEY),
                       ("account_proposal", ACCOUNT_KEY)):
        try:
            raw = await _read_state(conn, key)
            out[label] = (json.loads(raw) if isinstance(raw, str)
                          else raw)
        except Exception as exc:                               # noqa: BLE001
            out[label] = {"unreadable": type(exc).__name__}
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM rn1x_orders WHERE state = ANY($1::text[]) "
            "AND is_modelled", list(dk.OPEN_STATES))
        out["working_modelled_orders"] = int(n or 0)
    except Exception as exc:                                   # noqa: BLE001
        out["working_modelled_orders"] = None
        out["working_modelled_orders_unreadable"] = type(exc).__name__
    try:
        out["funded_orders"] = int(await conn.fetchval(
            "SELECT count(*) FROM live_orders") or 0)
    except Exception as exc:                                   # noqa: BLE001
        out["funded_orders"] = None
        out["funded_orders_unreadable"] = type(exc).__name__
    return out


def describe() -> dict:
    """The panel's own contract: what each action does, and what it cannot."""
    return {
        "version": VERSION, "actions": list(ACTIONS),
        "removes_authority": ["pause", "halt", "cancel-working-orders"],
        "grants_authority": [],
        "refused_by_name": {
            R_FUNDED_RESUME: ("resuming a funded lane is not available from "
                              "this panel at all"),
            R_PAUSED_ACCOUNT: ("the ACCOUNTING_UNCERTAIN account stays "
                               "paused and cannot be selected here"),
        },
        "proposals_not_settings": ["limits", "account"],
        "enforced_rails_live_in": "bettor_entry_execution",
        "submits_orders": False,
        "touches_live_orders": False,
        "activation_requires": [
            "a named account, recorded and approved by the owner",
            "an approved limit set, compared against the enforced rails",
            "the readiness evidence the activation checklist lists",
        ],
    }
