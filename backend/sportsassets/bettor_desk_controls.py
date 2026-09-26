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
#: wrote a key nobody reads would report a stop that never happened, so the
#: value comes from the module the executor itself reads it from.
#:
#: RESOLVED LAZILY, AND THAT MATTERS. Doing it at import time pulled
#: `live_executor` -- a large module with import-time setup of its own --
#: into the process the moment anything imported this one. In the full test
#: session that moved when that setup happened, and three unrelated
#: mirror/P&L tests began failing on state they had been seeing in a
#: different order. A control module must not change when another lane
#: initialises; the lookup is deferred and cached here instead.
_LIVE_PAUSE_KEY: str | None = None


def live_pause_key() -> str:
    global _LIVE_PAUSE_KEY

    if _LIVE_PAUSE_KEY is None:
        from . import live_executor as _LE

        _LIVE_PAUSE_KEY = _LE.PAUSE_KEY
    return _LIVE_PAUSE_KEY


def __getattr__(name):
    """`CTL.LIVE_PAUSE_KEY` still reads as a constant -- resolved on first
    access rather than at import (PEP 562)."""
    if name == "LIVE_PAUSE_KEY":
        return live_pause_key()
    raise AttributeError(name)

#: WHERE THE LIMIT SET AND THE ACCOUNT BINDING LIVE, and they are no longer
#: inert. `bettor_funded_activation` reads both; the approved limit set is
#: consumed by `bettor_entry_execution.effective_limits`, where it can only
#: TIGHTEN a rail; and the account id is resolved against the canonical
#: registry before anything accepts it.
from . import bettor_funded_activation as FA           # noqa: E402

LIMITS_KEY = FA.LIMITS_KEY
ACCOUNT_KEY = FA.ACCOUNT_KEY

#: The account that must not be selected or unpaused from anywhere. Its
#: accounting is unresolved; that is why it is paused.
PAUSED_ACCOUNT_MARKER = "ACCOUNTING_UNCERTAIN"

#: How long the durable operator stop may take before the halt reports it
#: as NOT TAKEN. An emergency control that waits is not a control.
OPERATOR_STOP_TIMEOUT_S = 10.0

ACTIONS = ("pause", "resume", "cancel-working-orders", "halt", "limits",
           "account", "activate", "state")

#: EVERY RESPONSE SAYS THESE THREE THINGS SEPARATELY, because "the button
#: was pressed", "the state changed" and "it did not work" are three facts
#: and a single ok flag collapses them:
#:
#:    requested   what was asked for
#:    applied     what the SERVER read back afterwards
#:    failed      the action did not take, with its reason
RESULT_FIELDS = ("requested", "applied", "failed")

#: Refusals, by name, so a blocked action never looks like a failure.
R_FUNDED_RESUME = "FUNDED_RESUME_IS_NOT_AVAILABLE_FROM_THE_DESK"
R_PAUSED_ACCOUNT = "THE_ACCOUNTING_UNCERTAIN_ACCOUNT_STAYS_PAUSED"
R_UNKNOWN_ACTION = "UNKNOWN_CONTROL_ACTION"
R_NOT_READY = "FUNDED_ACTIVATION_PREREQUISITES_NOT_MET"
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


def _obj(raw):
    """A jsonb value as an object, or None. Never a guess."""
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


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
            "applied": {"armed": rb["confirmed"]},
            "failed": (None if rb["confirmed"] is False
                       else {"why": ("the row does not read false "
                                     "afterwards, so the pause is NOT "
                                     "applied"),
                             "readback": rb}),
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
            "applied": {"armed": rb["confirmed"]},
            "failed": (None if rb["confirmed"] is True
                       else {"why": ("the row does not read true afterwards, "
                                     "so the lane is NOT armed"),
                             "readback": rb}),
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
            "requested": {"cancel_open_modelled_orders_in": list(experiments)},
            "applied": {"cancelled": len(rows)},
            "failed": None,
            "cancelled": len(rows),
            "orders": [dict(r) for r in rows],
            "states_considered_open": list(dk.OPEN_STATES),
            "experiments": list(experiments),
            "ok": True,
            "what_this_is": ("MODELLED orders in this ledger moved to "
                             "CANCELLED. This lane submitted nothing to a "
                             "venue, so no venue cancellation was sent or "
                             "claimed"),
            "funded_orders_cancelled": 0,
            # CORRECTED AFTER READING PRODUCTION. The first version said "no
            # funded order exists". `live_orders` in production holds 166,585
            # rows from the earlier live beta -- historical, not open, and
            # nothing to do with this lane, but the claim as written was
            # false. What is true is narrower and is what matters: this
            # statement cannot reach that table at all.
            "why_zero": ("this control touches `rn1x_orders` only, filtered "
                         "to modelled rows in the named experiments. It does "
                         "not read or write `live_orders`, which holds the "
                         "funded lane's own history, and it cannot cancel "
                         "anything at a venue")}


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
    _live = live_pause_key()
    await _write_state(conn, _live, True)
    out["components"]["funded_executor_paused"] = dict(
        await _readback_bool(conn, _live),
        key=_live, intended=True)
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
    out["requested"] = {"research_lane_armed": False,
                        "funded_executor_paused": True,
                        "operator_stop": True,
                        "cancel_working_modelled_orders": True}
    out["applied"] = {
        "research_lane_armed": lane,
        "funded_executor_paused": live,
        "operator_stop": out["components"]["operator_stop"].get("taken"),
        "working_orders_cancelled":
            out["components"]["working_orders"].get("cancelled")}
    out["failed"] = (None if out["ok"] else {
        "why": ("at least one component of the halt did not take. A halt is "
                "not partial-then-reported-as-done"),
        "components": {k: v for k, v in out["applied"].items()
                       if v is None or v is False}})
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
                "requested": dict(proposed), "applied": None,
                "failed": {"refusal": R_LIMITS_INCOMPLETE,
                           "missing": missing},
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
                "requested": dict(proposed), "applied": None,
                "failed": {"refusal": R_LIMITS_INCOMPLETE, "invalid": bad},
                "refusal": R_LIMITS_INCOMPLETE, "invalid": bad,
                "why": "nothing was stored"}
    if clean["per_order_usd"] > clean["capital_usd"]:
        return {"action": "limits", "ok": False,
                "requested": dict(proposed), "applied": None,
                "failed": {"refusal": R_LIMITS_INCOMPLETE,
                           "invalid": {"per_order_usd": ("cannot exceed the "
                                                         "capital limit")}},
                "refusal": R_LIMITS_INCOMPLETE,
                "invalid": {"per_order_usd": ("cannot exceed the capital "
                                              "limit")},
                "why": "nothing was stored"}
    record = {"proposed": clean, "by": by, "at": time.time(),
              # APPROVAL IS THE OWNER'S ACT, through the admin route. An
              # operator records the intent; approving it is what makes it
              # an enforced tightening.
              "approved": False, "enforced": False,
              "approval_route": "POST /api/admin/funded-limits/approve",
              "effective_when_approved": None,
              "why_not_enforced": ("the enforced rails are frozen in "
                                   "bettor_entry_execution and are read "
                                   "from there at every decision. This is "
                                   "the owner's stated intent, held for "
                                   "the activation checklist")}
    await _write_state(conn, LIMITS_KEY, record)
    stored = await _read_state(conn, LIMITS_KEY)
    return {"action": "limits", "ok": stored is not None,
            "requested": clean,
            "applied": ({"recorded_as_a_proposal": True}
                        if stored is not None else None),
            "failed": (None if stored is not None
                       else {"why": "the proposal did not read back"}),
            "stored": (json.loads(stored) if isinstance(stored, str)
                       else stored),
            "key": LIMITS_KEY,
            "changes_an_enforced_limit": False,
            "activation_still_blocked": True}


async def set_account(conn, *, by: str, account: dict) -> dict:
    """BIND A CANONICAL ACCOUNT ID, resolved against the registry or refused.

    WHAT CHANGED. The first version stored whatever name it was given and
    refused the paused account by looking for a STRING in it -- a rename
    would have walked straight past the guard. The id is now resolved
    against `bettor_desk_accounts`, and every refusal comes off that row:
    unknown id, not ACTIVE, `paused` true, or an accounting status that is
    not resolved. The display name is recorded for the audit and decides
    nothing.

    IT STILL ACTIVATES NOTHING. Binding is the owner's stated intent;
    `activate` runs the authorisation, and funded submission is off in code
    either way.
    """
    ident = str(account.get("account_id") or "").strip()
    name = str(account.get("name") or "").strip()
    venue = str(account.get("venue") or "").strip()
    req = {"account_id": ident or None, "name": name or None,
           "venue": venue or None}
    if not ident:
        return {"action": "account", "ok": False, "requested": req,
                "applied": None,
                "failed": {"refusal": FA.R_NO_ACCOUNT,
                           "why": ("a canonical account_id is required: a "
                                   "display name is not an identity")},
                "refusal": FA.R_NO_ACCOUNT}
    if not venue or FA.venue_class(venue) is None:
        return {"action": "account", "ok": False, "requested": req,
                "applied": None,
                "failed": {"refusal": FA.R_VENUE_UNKNOWN,
                           "known": sorted(FA.VENUE_CLASS)},
                "refusal": FA.R_VENUE_UNKNOWN}
    sel = await FA.account_selection(conn, ident)
    if not sel.get("ok"):
        return {"action": "account", "ok": False, "requested": req,
                "applied": None, "selection": sel,
                "failed": {"refusal": sel["refusal"], "why": sel.get("why")},
                "refusal": sel["refusal"],
                "resolved_from": "bettor_desk_accounts, by account_id"}
    record = {"account_id": ident, "name": name or None, "venue": venue,
              "venue_class": FA.venue_class(venue),
              "by": by, "at": time.time(), "bound": True, "approved": False,
              "registry": {k: sel["account"].get(k) for k in
                           ("desk_id", "status", "paused",
                            "accounting_status")},
              "authorises_nothing": ("binding records the intent. "
                                     "Authorisation is a separate action "
                                     "and funded submission stays off")}
    await _write_state(conn, ACCOUNT_KEY, record)
    stored = await _read_state(conn, ACCOUNT_KEY)
    return {"action": "account", "ok": stored is not None, "requested": req,
            "applied": ({"bound_account_id": ident, "venue_class":
                         FA.venue_class(venue)}
                        if stored is not None else None),
            "failed": (None if stored is not None
                       else {"why": "the binding did not read back"}),
            "stored": (json.loads(stored) if isinstance(stored, str)
                       else stored),
            "selection": sel,
            "resolved_from": "bettor_desk_accounts, by account_id",
            "activates_nothing": True, "activation_still_blocked": True}


async def readiness(conn) -> dict:
    """WHAT STILL BLOCKS FUNDED ACTIVATION -- derived from evidence.

    This used to assemble its own list, three of whose checks were hard
    coded FALSE. It now delegates to `bettor_funded_activation`, which reads
    the rows: the cycle's own book-read bases, the settlement comparisons,
    the resolved market scopes, and whether an entry was admitted WITHOUT
    the calibration waiver. A check with no evidence is UNKNOWN and blocks.
    """
    bound = _obj(await _read_state(conn, ACCOUNT_KEY)) or {}
    return await FA.readiness(conn, account_id=bound.get("account_id"))


async def activate(conn, *, by: str) -> dict:
    """RUN THE AUTHORISATION. It can succeed -- for a TEST venue.

    The refusal is still computed on the server and still names what is
    unmet; what changed is that the path is COMPLETE. With a clean
    canonical account, an owner-approved limit set inside the frozen rails
    and every readiness check met, a TEST-class venue is AUTHORIZED and its
    effective limits are recorded. Capital is not enabled by any of it: real
    submission is off in code and the venue-boundary gate is separate.
    """
    bound = _obj(await _read_state(conn, ACCOUNT_KEY)) or {}
    got = await FA.authorize(conn, account_id=bound.get("account_id") or "",
                             venue=bound.get("venue") or "", by=by)
    got["action"] = "activate"
    got.setdefault("requested", {"funded_trading": True})
    got["bound_account"] = bound or None
    got["enforced_server_side"] = (
        "the account is resolved against the canonical registry, the limits "
        "against the frozen rails, and the readiness checks against the "
        "lane's own rows. A disabled button establishes nothing")
    return got


async def state(conn) -> dict:
    """WHAT EVERY CONTROL ACTUALLY READS RIGHT NOW.

    The panel's displayed state comes from here, so what an operator sees
    is the row and not a cached assumption. A field that could not be read
    is reported as unreadable, never as a comfortable default.
    """
    out = {"version": VERSION, "at": time.time(),
           "funded_submission": "DISABLED"}
    for label, key in (("research_lane_armed", ext.CONTROL_KEY),
                       ("funded_executor_paused", live_pause_key())):
        try:
            raw = await _read_state(conn, key)
            out[label] = {"value": _truthy(raw), "key": key,
                          "row_present": raw is not None}
        except Exception as exc:                               # noqa: BLE001
            out[label] = {"value": None, "key": key,
                          "unreadable": type(exc).__name__}
    for label, key in (("limits_proposal", LIMITS_KEY),
                       ("account_proposal", ACCOUNT_KEY),
                       ("authorization", FA.AUTHORIZATION_KEY)):
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
        # ROWS IN `live_orders`, WHICH IS HISTORY AND NOT AN OPEN POSITION.
        # Production holds 166,585 of them from the earlier live beta;
        # reporting that as "funded orders" invited exactly the wrong
        # reading, so the field says what it counts.
        out["live_orders_rows_all_time"] = int(await conn.fetchval(
            "SELECT count(*) FROM live_orders") or 0)
        out["funded_orders_this_lane_submitted"] = 0
        out["funded_orders_note"] = (
            "`live_orders` is the funded lane's own table and its rows are "
            "HISTORICAL. This research lane has submitted none of them: its "
            "writer CHECKs order_submitted FALSE")
    except Exception as exc:                                   # noqa: BLE001
        out["live_orders_rows_all_time"] = None
        out["live_orders_unreadable"] = type(exc).__name__
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
            FA.R_ACCOUNT_PAUSED: ("the account's own registry row says "
                                  "paused. THIS is the guard: it is read "
                                  "off `bettor_desk_accounts`, so renaming "
                                  "the account, relabelling it on a screen "
                                  "or submitting it under another display "
                                  "name changes nothing"),
            FA.R_ACCOUNTING_UNCERTAIN: ("the account's accounting status is "
                                        "not resolved on its row"),
            FA.R_ACCOUNT_NOT_ACTIVE: "the account's status is not ACTIVE",
            FA.R_ACCOUNT_UNKNOWN: ("no such account_id in the canonical "
                                   "registry"),
            FA.R_NO_ACCOUNT: ("a canonical account_id is required: a display "
                              "name is not an identity"),
        },
        "the_paused_account_is_protected_by": (
            "its row in bettor_desk_accounts, not by its display name"),
        "activation_verdicts": {
            "AUTHORIZED_FOR_A_TEST_VENUE": (
                "200. Every check met, a TEST-class venue, a clean canonical "
                "account and an owner-approved limit set. It authorises the "
                "operating path against a venue sandbox and no capital"),
            FA.R_OWNER_AUTH: ("409. Every check met and the venue is FUNDED: "
                              "the owner's written authorisation is a "
                              "separate act this service can only read"),
            R_NOT_READY: "409, with the unmet and UNKNOWN checks named",
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
