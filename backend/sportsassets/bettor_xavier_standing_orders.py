"""XAVIER'S STANDING PROTECTIVE ORDERS -- STRICT FALLBACK, ONE GROUP AUTHORITY.

WHAT A STANDING PROTECTIVE ORDER IS. For a group whose PRIMARY leg holds
CONFIRMED inventory, Xavier may rest ONE hedge order on ONE selected hedge
instrument at a PROTECTIVE PRICE: a limit at which

    primary cost + entry fees + hedge cost + hedge fees + other costs + buffer

is BELOW the minimum combined payout over the settlement states the floor
classification claims. It is a MANAGEMENT policy (this module, versioned as
`STANDING_PROTECTIVE_ORDER_POLICY_V1`), never Derek's entry policy.

IT IS NOT A SECOND EXECUTION PATH, A SECOND BOOK OR A SECOND SELECTOR.
    * It runs ONLY inside the group authority: `ext_pinnacle_loop._service_once`
      (the in-process execution lock) -> `bettor_funded_pair_cycle.pass_once`
      (Xavier's group advisory lock, taken per group) -> here. A venue event
      does not act on its own: it triggers that same pass
      (`ext_pinnacle_loop.on_private_order_message` / `on_market_message`).
    * A placement is a Xavier decision row (`bettor_xavier.record_decision`)
      whose chosen plan is claimed (`claim_dispatch`) and sent through the
      existing leg reservation and `bettor_funded_execution.submit_for_decision`
      (`bettor_funded_pair_cycle.acquire_second_leg`), as an
      `AcquisitionPlan`. Fills land in the ONE book (`bettor_funded_intents` /
      `_fills` / `_economics`) through `bettor_funded_book.ingest_fills`.
    * Desirability versus HOLD is decided by the active Xavier policy's own
      function (`agents.xavier_policy.apply`) on the approved payout-state
      distribution (`bettor_funded_model.predict_distribution`) over the ONE
      payout table builder (`bettor_indirect_structures.payoff_table` /
      `bettor_funded_indirect_pair.position_worst_case`).
    * Migration 157's tables RECORD the plan, the selection, the lifecycle and
      the capacity held; the accounting stays the book's.

WHAT THE VENUE CAN AND CANNOT DO (the installed SDK, polymarket-us 1.0.2):
limit orders with GTC / GTD (`goodTillTime`, venue-enforced) / IOC / FOK, post-
only, order states NEW, PENDING_NEW, PENDING_REPLACE, PENDING_CANCEL,
PENDING_RISK, PARTIALLY_FILLED, FILLED, CANCELED, REPLACED, REJECTED, EXPIRED
with `cumQuantity` / `leavesQuantity`; create, retrieve, open orders, cancel,
modify, cancel-all by slug; a private websocket with order snapshot / update
streams. NOT PRESENT: one-cancels-other or any linked / contingent order across
markets, a cross-market quantity cap, cancel-on-disconnect. So
`EXCHANGE_LINKED_EXCLUSIVITY = UNAVAILABLE_OR_UNVERIFIED`, and the only mode
this module implements is STRICT_FALLBACK. A test that mocks an exchange
guarantee is not evidence the venue provides one, and `second_live_hedge_
permitted` refuses a second live hedge order whatever a caller claims.

WHY ONLY ONE LIVE ORDER PER GROUP. A database lock (or a unique index) can
stop US from SENDING a second order. It cannot stop two orders that were
ALREADY SENT from both matching at the venue -- the venue does not know they
are alternatives. Two resting hedges of 100 on different instruments against
100 primary can both fill: 200 hedge against 100 held (reproduced in
`tests/test_xavier_standing_orders_*`). So at most ONE live or potentially
live hedge order may exist per group: accepted, pending-new, partially filled
with leaves, pending-cancel, pending-replace, and every ambiguous state (a
lost acknowledgement, a claim with no recorded outcome). Every other candidate
is an internal, MONITORED candidate and is never submitted.

SWITCHING INSTRUMENTS, AND WHAT IT COSTS. cancel -> confirm the TERMINAL state
from the venue (a cancel acknowledgement is not terminal until the final fills
are reconciled: `bettor_funded_book.record_venue_terminal` applies an ending
only when the venue's cumulative quantity equals the ledger's) -> recompute
capacity -> only then place the replacement. The trade-off, disclosed on every
plan and in the workspace (`LATENCY_DISCLOSURE`): the replacement joins the
back of the queue at its price, and between the cancel and the confirmed
terminal read the group has NO resting protection (one cancel round trip plus
a reconciliation read -- the next order event, or at worst one servicing
interval). A second simultaneous order would close that gap and is exactly
the double-fill hazard above. Splitting the quantity across instruments is
refused by name (`R_SPLIT_QUANTITIES_NEED_A_SEPARATE_POLICY_DECISION`).

WHAT REMAINS LIVE AT THE VENUE IF THIS PROCESS DIES. Every standing order is
sent GOOD_TILL_DATE: it stays live until its `goodTillTime` (or a fill or a
cancel), and the venue expires it then. A GTC order would stay live
indefinitely, which is why this policy never sends one. On restart everything
is rebuilt from the database (plans, reservations, lifecycle events, intents,
fills) and the servicing pass's venue reads (`bettor_funded_book.recover`);
nothing is resent -- an ambiguous send is resolved by ASKING the venue.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import time
from dataclasses import replace
from decimal import ROUND_CEILING, Decimal
from typing import Any

from . import bettor_funded_book as FB
from . import bettor_funded_execution as FX
from . import bettor_funded_hedge_supply as HS

VERSION = "XAVIER_STANDING_PROTECTIVE_ORDERS_V1"
AGENT_ID = "XAVIER"
POLICY_KEY = "XAVIER_STANDING_ORDER_POLICY"
POLICY_VERSION = "STANDING_PROTECTIVE_ORDER_POLICY_V1"
ACTION = "STANDING_PROTECTIVE_ORDER"
MODE_STRICT_FALLBACK = "STRICT_FALLBACK"
MODES_IMPLEMENTED = (MODE_STRICT_FALLBACK,)

#: ── THE VENUE CAPABILITY THE STRICT FALLBACK RESTS ON ────────────────
EXCHANGE_LINKED_EXCLUSIVITY = "UNAVAILABLE_OR_UNVERIFIED"
VENUE_CAPABILITY = {
    "source": "the installed SDK, polymarket-us 1.0.2 (read, not the docs)",
    "order_params": ["marketSlug", "intent", "type LIMIT|MARKET", "price",
                     "quantity (integer)", "tif GTC|GTD|IOC|FOK",
                     "goodTillTime (GTD, venue-enforced expiry)",
                     "participateDontInitiate (post-only)",
                     "synchronousExecution", "maxBlockTime"],
    "order_states": ["NEW", "PENDING_NEW", "PENDING_REPLACE",
                     "PENDING_CANCEL", "PENDING_RISK", "PARTIALLY_FILLED",
                     "FILLED", "CANCELED", "REPLACED", "REJECTED", "EXPIRED"],
    "endpoints": ["create", "retrieve /v1/order/{id}", "open orders",
                  "cancel /v1/order/{id}/cancel", "modify",
                  "cancel_all by slugs"],
    "private_websocket": ["order snapshot", "order update",
                          "position snapshot/update",
                          "balance snapshot/update", "heartbeats"],
    "not_present": ["one-cancels-other / linked or contingent orders across "
                    "markets", "a cross-market quantity cap",
                    "cancel-on-disconnect"],
    "exchange_linked_exclusivity": EXCHANGE_LINKED_EXCLUSIVITY,
    "a_mocked_guarantee_is_not_evidence": True,
}
WHY_ONE_LIVE_ORDER = (
    "a database lock can stop us SENDING a second order; it cannot stop two "
    "orders that were already sent from both matching at the venue. With no "
    "venue-enforced linkage, at most one live or potentially-live hedge order "
    "may exist per group")
LATENCY_DISCLOSURE = (
    "STRICT_FALLBACK TRADE-OFF: switching instruments requires cancel -> "
    "confirmed terminal state -> reconciled fills -> recomputed capacity "
    "before the replacement is placed. The replacement loses its queue "
    "position, and between the cancel and the confirmed terminal read the "
    "group has no resting protection (a cancel round trip plus a "
    "reconciliation read: the next order event, at worst one servicing "
    "interval). A second simultaneous order would avoid that gap and could "
    "double-fill (hedge > primary), which is why it is never sent")
PROCESS_DEATH_DISCLOSURE = (
    "if this process dies, a GOOD_TILL_DATE standing order stays live at the "
    "venue until its goodTillTime (or a fill or a cancel), then the venue "
    "expires it; a GOOD_TILL_CANCEL order would stay live indefinitely, so "
    "this policy never sends one. On restart the state is rebuilt from the "
    "database and the venue's own order reads; nothing is resent")

#: ── FLOOR CLASSES (exactly one per evaluated hedge) ──────────────────
F_ALL = "POSITIVE_FLOOR_ALL_ESTABLISHED_STATES"
F_ORDINARY = "POSITIVE_FLOOR_CONDITIONAL_ON_ORDINARY_SETTLEMENT"
F_CONTROLLED = "CONTROLLED_LOSS_PAIR"
F_NONE = "FLOOR_NOT_ESTABLISHABLE"
FLOOR_CLASSES = (F_ALL, F_ORDINARY, F_CONTROLLED, F_NONE)
_FLOOR_RANK = {F_ALL: 3, F_ORDINARY: 2, F_CONTROLLED: 1, F_NONE: 0}
ORDINARY_STATES = ("REGULAR", "TIE", "PUSH")
EXTRAORDINARY_STATES = ("VOID",)
UNKNOWN_TREATMENT_RULE = (
    "an extraordinary state whose treatment the venue's prose does not "
    "establish is NEVER a refund and NEVER 50 cents: the leg is valued at 0 "
    "in it, and a floor that needs it is at best conditional on ordinary "
    "settlement")

#: ── LIFECYCLE STATES (derived from the book and the events) ──────────
L_NONE = "NO_STANDING_ORDER"
L_CLAIMED = "PLACEMENT_CLAIMED_OUTCOME_UNRECORDED"
L_SENT = "SENT_AWAITING_ACKNOWLEDGEMENT"
L_AMBIGUOUS = "AMBIGUOUS_POTENTIALLY_LIVE"
L_RESTING = "RESTING"
L_PARTIAL = "PARTIALLY_FILLED_RESTING"
L_CANCEL_REQUESTED = "CANCEL_REQUESTED_NOT_YET_TERMINAL"
L_TERMINAL = "TERMINAL"            # prefix: "TERMINAL:<book state>"
LIVE_BOOK_STATES = ("INTENT_RECORDED", "SEND_ATTEMPTED", "ACKNOWLEDGED",
                    "PARTIALLY_FILLED", "UNRESOLVED")

#: ── EVENT KINDS (append-only, `bettor_standing_order_events`) ────────
EV = dict.fromkeys((
    "PLAN_RECORDED", "CAPACITY_RESERVED", "PLACEMENT_CLAIMED",
    "PLACEMENT_NOT_SENT", "SENT_ACKNOWLEDGED_RESTING",
    "SENT_FILLED_ON_ARRIVAL", "SENT_OUTCOME_UNKNOWN", "REFUSED_BY_THE_VENUE",
    "FILL_OBSERVED", "INSTRUMENT_SELECTED", "COVERAGE_RECORDED",
    "CANCEL_REQUESTED", "CANCEL_NOT_CONFIRMED", "CANCEL_NOT_ADDRESSABLE",
    "CANCEL_WITHHELD_SWITCH_DISABLED", "TERMINAL_CONFIRMED",
    "CAPACITY_RELEASED", "FLOOR_INVALIDATED", "DESIRABILITY_LOST",
    "CAPACITY_EXCEEDED", "PRIMARY_QUANTITY_CHANGED", "AUTHORIZATION_EXPIRED",
    "MARKET_SUSPENDED_OR_CLOSED", "PRIMARY_CLOSED", "EXPIRY_PASSED",
    "SWITCH_REQUESTED", "INSTRUMENT_TRANSITION_EVALUATED",
    "INSTRUMENT_TRANSITION_REFUSED", "REMAINDER_REPLACEMENT_REFUSED",
    "PLACEMENT_REFUSED", "SECOND_LIVE_ORDER_REFUSED",
    "AMBIGUOUS_NO_COMPETING_ORDER", "EXIT_WAITS_FOR_HEDGE_TERMINAL",
    "EXIT_REFUSED_HEDGE_WOULD_EXCEED_PRIMARY", "VENUE_ORDER_EVENT",
    "VENUE_MARKET_EVENT", "PRICE_TOUCH_IS_NOT_A_FILL", "UNMATCHED_ORDER_EVENT",
    "RESIZE_REQUESTED"))
SOURCES = ("SCHEDULED_SERVICING", "VENUE_ORDER_EVENT", "VENUE_MARKET_EVENT",
           "RECONCILIATION_READ", "DISPATCHER", "OPERATOR")

#: ── REFUSALS, EACH NAMING ONE THING ──────────────────────────────────
R_POLICY_DISABLED = "THE_STANDING_ORDER_POLICY_IS_NOT_ENABLED"
R_SCHEMA = "THE_STANDING_ORDER_TABLES_ARE_NOT_IN_THIS_DATABASE"
R_NO_CONFIRMED_PRIMARY = "THE_PRIMARY_HOLDS_NO_CONFIRMED_INVENTORY_TO_PROTECT"
R_NO_CAPACITY = "NO_HEDGE_CAPACITY_REMAINS_AGAINST_THE_CONFIRMED_PRIMARY"
R_LIVE_ORDER_EXISTS = (
    "A_LIVE_OR_POTENTIALLY_LIVE_HEDGE_ORDER_EXISTS_FOR_THIS_GROUP")
R_NO_VERIFIED_EXCLUSIVITY = (
    "NO_VERIFIED_EXCHANGE_LINKED_EXCLUSIVITY_SO_A_SECOND_LIVE_HEDGE_ORDER_"
    "IS_NEVER_PLACED")
R_SPLIT_QUANTITIES_NEED_A_SEPARATE_POLICY_DECISION = (
    "SPLITTING_HEDGE_QUANTITY_ACROSS_INSTRUMENTS_NEEDS_A_SEPARATE_POLICY_"
    "DECISION")
R_MODE_NOT_IMPLEMENTED = "ONLY_STRICT_FALLBACK_IS_IMPLEMENTED"
R_NO_PROTECTIVE_PRICE = (
    "NO_PRICE_ON_THE_TICK_GRID_MEETS_THE_POLICY_S_MINIMUM_FLOOR_CLASS")
R_NOT_DESIRABLE = "THE_POLICY_PREFERS_HOLD_TO_THIS_STANDING_ORDER"
R_DESIRABILITY_NOT_ESTABLISHED = (
    "THE_STANDING_ORDER_COULD_NOT_BE_VALUED_AGAINST_HOLD")
R_GROUP_GATED = "THE_GROUP_HAS_AN_ORDER_IN_FLIGHT_OR_UNRESOLVED"
R_DECISION_NOT_HOLD = "THE_GROUP_REVIEW_DID_NOT_SELECT_HOLD"
R_EXIT_WAITS_FOR_HEDGE_TERMINAL = (
    "THE_EXIT_WAITS_UNTIL_THE_STANDING_HEDGE_ORDER_IS_CONFIRMED_TERMINAL")
R_EXIT_WOULD_LEAVE_HEDGE_ABOVE_PRIMARY = (
    "THE_PRIMARY_EXIT_WOULD_LEAVE_MORE_HEDGE_THAN_PRIMARY")
R_REMAINDER_NEEDS_A_SECOND_HEDGE_LEG = (
    "THE_REMAINDER_WOULD_NEED_A_SECOND_OPEN_HEDGE_LEG_WHICH_ONE_OPEN_LEG_PER_"
    "ROLE_REFUSES")
R_INSTRUMENT_ALREADY_SELECTED = (
    "ANOTHER_INSTRUMENT_IS_SELECTED_A_CHANGE_NEEDS_A_SEPARATE_TRANSITION")
R_TICK_NOT_ESTABLISHED = "THE_INSTRUMENT_S_TICK_GRID_IS_NOT_ESTABLISHED"
R_MARKET_NOT_TRADING = "THE_MARKET_IS_SUSPENDED_CLOSED_OR_SETTLED"
R_AUTHORIZATION_LAPSED = "THE_SYSTEM_AUTHORIZATION_HAS_EXPIRED_OR_BEEN_REVOKED"
R_PLAN_REFUSED = "THE_STANDING_PLAN_COULD_NOT_BE_BUILT"
R_CHANGED_BEFORE_SEND = "THE_GROUP_CHANGED_BETWEEN_THE_PLAN_AND_THE_SEND"

FORBIDDEN_KEYS = (
    "max_downside_usd", "max_incremental_capital_usd", "capital_usd",
    "per_order_usd", "event_exposure_usd", "max_exposure_usd",
    "daily_loss_stop_usd", "limits", "risk_limits", "credentials", "api_key",
    "account_id", "authorization", "approval", "approved", "approved_by",
    "submission_enabled", "FUNDED_SUBMISSION_ENABLED",
    "REAL_ORDER_SUBMISSION_ENABLED", "FUNDED_EXIT_SUBMISSION_ENABLED")

#: THE MANAGEMENT POLICY'S PARAMETERS. The code default is DISABLED: a
#: standing order policy is activated only by a person's write
#: (`activate_version`), and the funded submission switches stay exactly as
#: they are whatever this says.
PARAMETERS: dict[str, dict] = {
    "enabled": {"default": False,
                "doc": "OFF in code; activated only by a person"},
    "mode": {"default": MODE_STRICT_FALLBACK,
             "doc": "only STRICT_FALLBACK is implemented"},
    "minimum_floor_class": {
        "default": F_ORDINARY,
        "doc": ("the weakest floor class a standing order may be placed at. "
                "With a cancellation that refunds the purchase basis and "
                "fees that are not refunded, the VOID state nets minus the "
                "fees, so POSITIVE_FLOOR_ALL_ESTABLISHED_STATES is reachable "
                "only where the fixture cannot void; the conditional class is "
                "the practical minimum and its condition is recorded")},
    "buffer_usd_per_pair": {"default": 0.01,
                            "doc": "safety margin per matched pair"},
    "other_costs_usd_per_pair": {"default": 0.0,
                                 "doc": "any further per-pair cost"},
    "gtd_seconds": {"default": 900.0,
                    "doc": ("the venue-enforced expiry (goodTillTime) of each "
                            "standing order, from placement")},
    "switch_min_improvement_usd": {
        "default": 0.05,
        "doc": ("an unfilled standing order is moved to another instrument "
                "only when that instrument's value over HOLD is better by at "
                "least this much (the queue position and the unprotected "
                "window are the cost of moving)")},
    "cancel_retry_after_s": {"default": 30.0,
                             "doc": "a cancel request is re-sent at most this "
                                    "often while the order is not terminal"},
    "resize_on_primary_growth": {
        "default": True,
        "doc": ("an UNFILLED order whose capacity grew (a partial entry "
                "filled more) is cancelled and re-placed at the new capacity "
                "after its terminal state is confirmed")},
    "split_quantities_across_instruments": {
        "default": False,
        "doc": ("REFUSED if true: splitting needs a separate policy "
                "decision")},
}


def default_params() -> dict:
    return {k: v["default"] for k, v in PARAMETERS.items()}


def validate(params: dict | None) -> dict:
    """One version's parameters, checked. Pure. A forbidden key (a risk
    limit, credential or approval), an unknown key, a mode other than
    STRICT_FALLBACK or a request to split quantities refuses the version."""
    out = default_params()
    for k, v in dict(params or {}).items():
        if k in FORBIDDEN_KEYS:
            return {"ok": False, "refusal": "FORBIDDEN_KEY", "key": k}
        if k not in PARAMETERS:
            return {"ok": False, "refusal": "UNKNOWN_KEY", "key": k}
        out[k] = v
    if out["mode"] not in MODES_IMPLEMENTED:
        return {"ok": False, "refusal": R_MODE_NOT_IMPLEMENTED,
                "mode": out["mode"]}
    if out["split_quantities_across_instruments"]:
        return {"ok": False,
                "refusal": R_SPLIT_QUANTITIES_NEED_A_SEPARATE_POLICY_DECISION}
    if out["minimum_floor_class"] not in (F_ALL, F_ORDINARY):
        return {"ok": False, "refusal": "A_STANDING_ORDER_NEEDS_A_POSITIVE_"
                "FLOOR_CLASS", "value": out["minimum_floor_class"]}
    for k in ("buffer_usd_per_pair", "other_costs_usd_per_pair",
              "gtd_seconds", "switch_min_improvement_usd",
              "cancel_retry_after_s"):
        try:
            f = float(out[k])
        except (TypeError, ValueError):
            return {"ok": False, "refusal": "BAD_VALUE", "key": k}
        if not math.isfinite(f) or f < 0:
            return {"ok": False, "refusal": "BAD_VALUE", "key": k}
        out[k] = f
    if out["gtd_seconds"] < 60:
        return {"ok": False, "refusal": "BAD_VALUE", "key": "gtd_seconds"}
    out["enabled"] = bool(out["enabled"])
    out["resize_on_primary_growth"] = bool(out["resize_on_primary_growth"])
    return {"ok": True, "refusal": None, "params": out}


def code_default(why: str | None = None) -> dict:
    return {"agent_id": AGENT_ID, "policy_key": POLICY_KEY,
            "version": POLICY_VERSION, "params": default_params(),
            "source": "CODE_DEFAULT", "why": why, "approved_by": None,
            "latency_disclosure": LATENCY_DISCLOSURE,
            "process_death_disclosure": PROCESS_DEATH_DISCLOSURE}


async def load_policy(conn) -> dict:
    """THE ACTIVE STANDING ORDER POLICY, or the (disabled) code default.
    Never raises."""
    try:
        if await conn.fetchval(
                "SELECT to_regclass('agent_policy_versions')") is None:
            return code_default("THE_AGENT_POLICY_VERSIONS_TABLE_IS_ABSENT")
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT version, params, approved_by, approved_at "
                "  FROM agent_policy_versions WHERE agent_id=$1 "
                "   AND policy_key=$2 AND state='ACTIVE' "
                " ORDER BY created_at DESC LIMIT 1", AGENT_ID, POLICY_KEY)
    except Exception as exc:                                    # noqa: BLE001
        return code_default("POLICY_READ_FAILED:%s" % type(exc).__name__)
    if row is None:
        return code_default("NO_ACTIVE_STANDING_ORDER_POLICY_VERSION")
    params = row["params"]
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except ValueError:
            return code_default("THE_STORED_VERSION_DID_NOT_PARSE")
    v = validate(params)
    if not v.get("ok"):
        return dict(code_default("THE_STORED_VERSION_DID_NOT_VALIDATE"),
                    rejected_because=v)
    return dict(code_default(), version=str(row["version"]),
                params=v["params"], source="ACTIVE_POLICY", why=None,
                approved_by=row["approved_by"])


async def activate_version(conn, *, version: str, params: dict,
                           approved_by: str, created_by: str) -> dict:
    """A PERSON'S WRITE: make one standing-order policy version ACTIVE. An
    agent may not approve; the parameters must validate. Never raises."""
    who = str(approved_by or "").strip()
    if not who:
        return {"ok": False, "refusal": "AN_ACTIVE_VERSION_NAMES_A_PERSON"}
    if who.upper() in ("DEREK", "XAVIER", "AUDREY") or \
            who.upper().startswith("AGENT"):
        return {"ok": False, "refusal": "NO_AGENT_MAY_APPROVE_A_POLICY",
                "approved_by": who}
    v = validate(params)
    if not v.get("ok"):
        return dict(v, ok=False)
    try:
        async with conn.transaction():
            if await conn.fetchval(
                    "SELECT to_regclass('agent_identities')") is not None:
                await conn.execute(
                    "INSERT INTO agent_identities (agent_id, display_name, "
                    " mandate, tool_permissions) VALUES ($1,'Xavier',$2,"
                    " '{}'::jsonb) ON CONFLICT (agent_id) DO NOTHING",
                    AGENT_ID, "position management within the approved "
                    "policy")
            await conn.execute(
                "UPDATE agent_policy_versions SET state='RETIRED' "
                " WHERE agent_id=$1 AND policy_key=$2 AND state='ACTIVE' "
                "   AND version<>$3", AGENT_ID, POLICY_KEY, str(version))
            await conn.execute(
                "INSERT INTO agent_policy_versions (agent_id, policy_key, "
                " version, params, state, created_by, approved_by, "
                " approved_at, created_at) VALUES ($1,$2,$3,$4::jsonb,"
                " 'ACTIVE',$5,$6,now(),now()) ON CONFLICT (agent_id, "
                " policy_key, version) DO UPDATE SET state='ACTIVE', "
                " params=$4::jsonb, approved_by=$6, approved_at=now()",
                AGENT_ID, POLICY_KEY, str(version), json.dumps(v["params"]),
                str(created_by), who)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": "POLICY_WRITE_FAILED",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    return {"ok": True, "version": str(version), "params": v["params"],
            "approved_by": who}


# ═════════════════════════════════════════════════════════════════════
# THE CAPABILITY GATE: ONE LIVE HEDGE ORDER, WHATEVER IS CLAIMED
# ═════════════════════════════════════════════════════════════════════

def second_live_hedge_permitted(*, live_or_potentially_live: int,
                                claimed_exclusivity: Any = None,
                                mode: str = MODE_STRICT_FALLBACK) -> dict:
    """MAY A HEDGE ORDER BE SENT WHILE `live_or_potentially_live` OTHERS
    EXIST FOR THE GROUP? Pure.

    Under STRICT_FALLBACK -- the only mode implemented -- the answer is no
    whenever one exists, and a caller's CLAIM that the exchange links the two
    orders (a mock, a flag, a document nobody verified) changes nothing: the
    venue capability is UNAVAILABLE_OR_UNVERIFIED."""
    n = int(live_or_potentially_live or 0)
    out = {"mode": mode, "exchange_linked_exclusivity":
           EXCHANGE_LINKED_EXCLUSIVITY, "live_or_potentially_live": n,
           "claimed_exclusivity": claimed_exclusivity,
           "claim_is_evidence": False}
    if mode != MODE_STRICT_FALLBACK:
        return dict(out, permitted=False, refusal=R_MODE_NOT_IMPLEMENTED)
    if n <= 0:
        return dict(out, permitted=True, refusal=None)
    return dict(out, permitted=False,
                refusal=(R_NO_VERIFIED_EXCLUSIVITY if claimed_exclusivity
                         else R_LIVE_ORDER_EXISTS),
                why=WHY_ONE_LIVE_ORDER)


# ═════════════════════════════════════════════════════════════════════
# FEES, TICKS AND THE FLOOR CLASSIFICATION
# ═════════════════════════════════════════════════════════════════════

def _date_of(at: float) -> str:
    return _dt.datetime.fromtimestamp(float(at), _dt.timezone.utc).date(
    ).isoformat()


def _iso(at: float) -> str:
    return _dt.datetime.fromtimestamp(float(at), _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def fee_schedule_identity(*, at: float, sport=None) -> dict:
    """WHICH FEE SCHEDULE A CLASSIFICATION WAS COMPUTED UNDER. The digest
    changes when the dated schedule, its taker coefficient for the sport or
    its formula changes -- and a changed digest INVALIDATES the floor
    classification of every plan computed under the old one."""
    from . import bettor_fee_schedule as FS
    from . import calibration_fees as CF
    when = _dt.datetime.fromtimestamp(float(at), _dt.timezone.utc)
    try:
        sched = FS.for_date(when.date().isoformat())
        theta = CF.taker_coefficient(sport, at=when.isoformat())
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "digest": None,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    ident = {"schedule_id": sched.schedule_id,
             "effective_from": sched.effective_from,
             "theta_taker": str(theta), "formula": CF.SCHEDULE["FORMULA"],
             "maker_rebate_counted": False, "sport": sport}
    blob = json.dumps(ident, sort_keys=True)
    return dict(ident, ok=True,
                digest=hashlib.sha256(blob.encode()).hexdigest()[:24])


def hedge_fee_usd(*, qty: int, cost_price: float, at: float,
                  sport=None) -> dict:
    """THE TAKER FEE a fill of `qty` at `cost_price` would be charged, from
    the dated schedule (theta x C x p x (1 - p), symmetric in the price's
    spelling), rounded UP to the cent. No maker rebate is counted even though
    a resting order may be the maker: a rebate not yet earned reduces
    nothing."""
    from . import bettor_fee_schedule as FS
    from . import calibration_fees as CF
    when = _dt.datetime.fromtimestamp(float(at), _dt.timezone.utc)
    try:
        sched = FS.for_date(when.date().isoformat())
        theta = CF.taker_coefficient(sport, at=when.isoformat())
        exact = sched.exact(theta, int(qty), Decimal(str(cost_price)))
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "fee_usd": None,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    fee = exact.quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    return {"ok": True, "fee_usd": float(fee), "exact": str(exact),
            "basis": "DATED_TAKER_SCHEDULE_ROUNDED_UP_NO_REBATE"}


def settlement_identity(*, held_leg, hedge_leg, sport_permits_tie,
                        fixture_can_void, fixture_can_postpone) -> dict:
    """WHAT BOTH LEGS SETTLE ON, AS READ: each leg's settlement rules and
    prose hash, and the partition flags. A different digest later means the
    venue's rules (or our reading of them) changed, which invalidates the
    floor classification."""
    from . import bettor_xavier as XV
    ident = {"held": XV.settlement_identity_of_leg(held_leg),
             "hedge": XV.settlement_identity_of_leg(hedge_leg),
             "sport_permits_tie": sport_permits_tie,
             "fixture_can_void": bool(fixture_can_void),
             "fixture_can_postpone": bool(fixture_can_postpone)}
    blob = json.dumps(ident, sort_keys=True, default=str)
    return {"digest": hashlib.sha256(blob.encode()).hexdigest()[:24],
            "identity": ident}


def classify_floor(*, held_leg, hedge_leg, primary_qty, primary_cost_usd,
                   entry_fees_usd, hedge_qty, hedge_cost_price,
                   hedge_fee_usd_, other_costs_usd=0.0, buffer_usd=0.0,
                   sport_permits_tie, fixture_can_void=True,
                   fixture_can_postpone=True,
                   whole_position: bool = False) -> dict:
    """ONE HEDGE, EXACTLY ONE FLOOR CLASS. Pure.

    THE CLASS IS A PROPERTY OF THE PAIRS the hedge makes: `hedge_qty` matched
    pairs, each carrying its share of the confirmed primary's actual cost and
    entry fees. Uncovered primary contracts are not part of any pair -- they
    are reported as uncovered, never folded into (or out of) the pair's floor.
    `whole_position=True` instead builds the table of the ACTUAL combined
    inventory (every held primary contract and every hedge contract), for the
    record and the whole-position worst case.

    The payout table is `bettor_indirect_structures.payoff_table` at the REAL
    quantities (the held leg at the CONFIRMED primary quantity, the hedge at
    `hedge_qty`, priced at `hedge_cost_price`). The cost side is the actual
    primary cost and entry fees from the book, the hedge's cost and fee at
    this price, other costs and the buffer. Then:

      POSITIVE_FLOOR_ALL_ESTABLISHED_STATES  every ordinary settlement state
          and every extraordinary one (a cancellation) is established and
          pays more than the total cost;
      POSITIVE_FLOOR_CONDITIONAL_ON_ORDINARY_SETTLEMENT  every ordinary
          state pays more, but an extraordinary state is unknown (never a
          refund, never 50 cents) or does not;
      CONTROLLED_LOSS_PAIR  the ordinary states are established and at least
          one pays no more than the cost: a bounded, known loss;
      FLOOR_NOT_ESTABLISHABLE  an ordinary state is undetermined, a leg lacks
          a fact, the fee could not be priced or a quantity is not a whole
          contract.
    """
    from . import bettor_indirect_structures as IS
    out: dict[str, Any] = {"unknown_treatment_rule": UNKNOWN_TREATMENT_RULE}
    try:
        pq = float(primary_qty)
        hq = float(hedge_qty)
        c = float(hedge_cost_price)
    except (TypeError, ValueError):
        return dict(out, floor_class=F_NONE, reason="QUANTITY_OR_PRICE_UNREAD")
    if held_leg is None or hedge_leg is None:
        return dict(out, floor_class=F_NONE, reason="A_LEG_IS_MISSING")
    if pq < 1 or not pq.is_integer() or hq < 1 or not hq.is_integer():
        return dict(out, floor_class=F_NONE,
                    reason="QUANTITIES_ARE_WHOLE_CONTRACTS_AT_THIS_VENUE",
                    primary_qty=pq, hedge_qty=hq)
    if hq > pq + 1e-9:
        return dict(out, floor_class=F_NONE,
                    reason="THE_HEDGE_EXCEEDS_THE_CONFIRMED_PRIMARY",
                    primary_qty=pq, hedge_qty=hq)
    if not (0.0 < c < 1.0) or abs(round(c * 100) - c * 100) > 1e-9:
        return dict(out, floor_class=F_NONE,
                    reason="THE_HEDGE_PRICE_IS_NOT_ON_THE_CENT_GRID", price=c)
    if hedge_fee_usd_ is None or primary_cost_usd is None \
            or entry_fees_usd is None:
        return dict(out, floor_class=F_NONE,
                    reason="A_COST_OR_FEE_IS_NOT_ESTABLISHED",
                    hedge_fee_usd=hedge_fee_usd_,
                    primary_cost_usd=primary_cost_usd,
                    entry_fees_usd=entry_fees_usd)
    gaps = list(held_leg.missing_facts()) + list(hedge_leg.missing_facts())
    gaps += IS.integer_total_push_gaps((held_leg, hedge_leg))
    if gaps:
        return dict(out, floor_class=F_NONE,
                    reason="A_LEG_LACKS_A_GRADING_FACT", missing=gaps[:5])
    # THE HELD LEG'S REFUND BASIS: the actual average cost, floored to the
    # cent, so a refunded cancellation is never valued above what was paid.
    held_cents = int(math.floor(float(primary_cost_usd) / pq * 100 + 1e-9))
    # THE PAIRS: the held leg at the matched quantity, carrying its pro-rata
    # share of the actual primary cost and entry fees.
    scope_q = pq if whole_position else hq
    share = scope_q / pq
    primary_cost_usd = round(float(primary_cost_usd) * share, 6)
    entry_fees_usd = round(float(entry_fees_usd) * share, 6)
    out.update(scope=("WHOLE_POSITION" if whole_position
                      else "MATCHED_PAIRS"),
               confirmed_primary_qty=pq,
               uncovered_primary_qty=round(pq - hq, 6))
    pq = scope_q
    legs = (replace(held_leg, quantity=int(pq),
                    cost_cents_per_unit=held_cents),
            replace(hedge_leg, quantity=int(hq),
                    cost_cents_per_unit=int(round(c * 100))))
    try:
        table = IS.payoff_table(legs, sport_permits_tie=bool(
            sport_permits_tie), fixture_can_void=fixture_can_void,
            fixture_can_postpone=fixture_can_postpone)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, floor_class=F_NONE, reason="THE_TABLE_RAISED",
                    error=type(exc).__name__)
    hedge_cost = round(hq * c, 6)
    total_cost = round(float(primary_cost_usd) + float(entry_fees_usd)
                       + hedge_cost + float(hedge_fee_usd_)
                       + float(other_costs_usd) + float(buffer_usd), 6)
    rows, ordinary, extra, unresolved = [], [], [], []
    for r in table:
        st = r.get("state")
        per = list(r.get("per_leg_cents") or [])
        if st == IS.STATE_POSTPONED:
            unresolved.append(r["region"])
            continue
        determined = bool(r.get("determined"))
        # UNKNOWN TREATMENT IS NEVER A REFUND AND NEVER 50 CENTS: a leg whose
        # payout is undetermined contributes 0 to the worst case.
        worst_payout = sum(float(p or 0) * q for p, q in zip(
            per, (pq, hq))) / 100.0
        row = {"region": r["region"], "state": st, "determined": determined,
               "per_leg_cents": per,
               "payout_usd": (round(float(r["joint_cents"]) / 100.0, 6)
                              if determined else None),
               "worst_payout_usd": round(worst_payout, 6)}
        row["net_usd"] = (None if row["payout_usd"] is None
                          else round(row["payout_usd"] - total_cost, 6))
        row["worst_net_usd"] = round(worst_payout - total_cost, 6)
        rows.append(row)
        (ordinary if st in ORDINARY_STATES else extra).append(row)
    out.update(regions=rows, unresolved_states=unresolved,
               primary_qty=pq, hedge_qty=hq, hedge_cost_price=c,
               held_refund_cents=held_cents,
               costs={"primary_cost_usd": float(primary_cost_usd),
                      "entry_fees_usd": float(entry_fees_usd),
                      "primary_cost_usd_confirmed": round(
                          float(primary_cost_usd) / share, 6),
                      "entry_fees_usd_confirmed": round(
                          float(entry_fees_usd) / share, 6),
                      "hedge_cost_usd": hedge_cost,
                      "hedge_fee_usd": float(hedge_fee_usd_),
                      "other_costs_usd": float(other_costs_usd),
                      "buffer_usd": float(buffer_usd),
                      "total_cost_usd": total_cost})
    if not ordinary or any(not r["determined"] for r in ordinary):
        return dict(out, floor_class=F_NONE,
                    reason="AN_ORDINARY_SETTLEMENT_STATE_IS_UNDETERMINED",
                    undetermined=[r["region"] for r in ordinary
                                  if not r["determined"]])
    min_ord = min(r["payout_usd"] for r in ordinary)
    extra_det = [r for r in extra if r["determined"]]
    extra_unknown = [r["region"] for r in extra if not r["determined"]]
    worst_all = min(r["worst_net_usd"] for r in rows)
    out.update(min_ordinary_payout_usd=round(min_ord, 6),
               ordinary_margin_usd=round(min_ord - total_cost, 6),
               extraordinary_unknown=extra_unknown,
               extraordinary_margins={r["region"]: r["net_usd"]
                                      for r in extra_det},
               worst_case_net_usd=round(worst_all, 6))
    if min_ord - total_cost <= 1e-9:
        return dict(out, floor_class=F_CONTROLLED,
                    claimed_states=list(ORDINARY_STATES),
                    reason=("the cheapest ordinary state pays $%.4f against "
                            "a total cost of $%.4f: a bounded loss"
                            % (min_ord, total_cost)))
    if not extra_unknown and all(r["net_usd"] > 1e-9 for r in extra_det):
        return dict(out, floor_class=F_ALL,
                    claimed_states=sorted({r["state"] for r in rows}),
                    min_claimed_payout_usd=round(min(
                        r["payout_usd"] for r in rows), 6))
    return dict(out, floor_class=F_ORDINARY,
                claimed_states=list(ORDINARY_STATES),
                min_claimed_payout_usd=round(min_ord, 6),
                condition=("positive only if the fixture settles ordinarily: "
                           "%s" % ("; ".join(
                               ["%s treatment unknown" % x
                                for x in extra_unknown]
                               + ["%s nets $%+.4f" % (r["region"],
                                                       r["net_usd"])
                                  for r in extra_det
                                  if r["net_usd"] <= 1e-9]))))


def tick_grid(row: dict | None) -> dict:
    """THE WIRE GRID a standing limit must sit on: a multiple of BOTH the
    market's own `orderPriceMinTickSize` and the adapter's whole cent. From
    the candidate's ranked row (`executable_grid`); absent -> not
    established, and nothing is placed."""
    g = dict((row or {}).get("executable_grid") or {})
    try:
        step = Decimal(str(g.get("step") or g.get("tick")))
    except Exception:                                           # noqa: BLE001
        return {"ok": False, "refusal": R_TICK_NOT_ESTABLISHED}
    if not g.get("ok", True) or step <= 0:
        return {"ok": False, "refusal": R_TICK_NOT_ESTABLISHED}
    cent = Decimal("0.01")
    if (step % cent) != 0:
        # the lowest common multiple of the tick and the cent
        m = step
        while (m % cent) != 0:
            m += step
        step = m
    return {"ok": True, "step": step, "tick": g.get("tick"),
            "tick_source": g.get("tick_source")}


def wire_for(cost: Decimal, side: str) -> Decimal:
    return (Decimal("1") - cost) if side == "ORDER_INTENT_BUY_SHORT" else cost


def protective_price(*, held_leg, hedge_leg, side, primary_qty,
                     primary_cost_usd, entry_fees_usd, hedge_qty, grid,
                     policy_params, at, sport=None,
                     sport_permits_tie, fixture_can_void=True,
                     fixture_can_postpone=True) -> dict:
    """THE HIGHEST COST ON THE GRID AT WHICH THE PAIR MEETS THE POLICY'S
    MINIMUM FLOOR CLASS (the most fillable protective limit). Pure but for
    the dated fee schedule. Every price is on the wire grid as the venue
    receives it, and the fee is charged at that price."""
    if not grid.get("ok"):
        return {"ok": False, "refusal": grid.get("refusal")}
    want = _FLOOR_RANK[policy_params.get("minimum_floor_class", F_ORDINARY)]
    step = grid["step"]
    tried = 0
    cost = Decimal("1") - step
    last = None
    while cost > 0:
        wire = wire_for(cost, side)
        if (wire % step) == 0 and Decimal("0") < wire < Decimal("1"):
            tried += 1
            fee = hedge_fee_usd(qty=int(hedge_qty), cost_price=float(cost),
                                at=at, sport=sport)
            fl = classify_floor(
                held_leg=held_leg, hedge_leg=hedge_leg,
                primary_qty=primary_qty, primary_cost_usd=primary_cost_usd,
                entry_fees_usd=entry_fees_usd, hedge_qty=hedge_qty,
                hedge_cost_price=float(cost), hedge_fee_usd_=fee.get(
                    "fee_usd"),
                other_costs_usd=float(policy_params.get(
                    "other_costs_usd_per_pair") or 0) * float(hedge_qty),
                buffer_usd=float(policy_params.get("buffer_usd_per_pair")
                                 or 0) * float(hedge_qty),
                sport_permits_tie=sport_permits_tie,
                fixture_can_void=fixture_can_void,
                fixture_can_postpone=fixture_can_postpone)
            last = fl
            if fl["floor_class"] == F_NONE and fl.get("reason") != \
                    "THE_HEDGE_PRICE_IS_NOT_ON_THE_CENT_GRID":
                # NOT A MATTER OF PRICE: an undetermined ordinary state, a
                # missing grading fact or an unpriced cost is the same at
                # every price, so the search stops and says which.
                return {"ok": False, "refusal": R_NO_PROTECTIVE_PRICE,
                        "floor": fl, "tried": tried}
            if _FLOOR_RANK[fl["floor_class"]] >= want:
                return {"ok": True, "cost_price": float(cost),
                        "wire_price": float(wire), "fee": fee, "floor": fl,
                        "tried": tried, "step": str(step)}
        cost -= step
    return {"ok": False, "refusal": R_NO_PROTECTIVE_PRICE, "tried": tried,
            "last_floor": last}


# ═════════════════════════════════════════════════════════════════════
# THE GROUP'S STANDING STATE, FROM THE BOOK AND THE RECORDS
# ═════════════════════════════════════════════════════════════════════

async def has_schema(conn) -> bool:
    try:
        return await conn.fetchval(
            "SELECT to_regclass('bettor_standing_order_plans')") is not None
    except Exception:                                           # noqa: BLE001
        return False


def _obj(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return dict(v or {})


def is_standing_hedge(row: dict) -> bool:
    """A HEDGE entry intent that a standing order plan created."""
    r = dict(row or {})
    if str(r.get("leg_role") or "") != "HEDGE":
        return False
    return bool(_obj(r.get("decision_ref")).get("standing_order"))


def is_unfilled_standing_hedge(row: dict) -> bool:
    """A standing hedge order holding NO inventory yet -- an obligation, not
    protection. The pass reviews its group as the primary alone (with the
    order's fill-capable quantity counted here) until it fills."""
    return is_standing_hedge(row) and float(
        dict(row or {}).get("residual_qty") or 0) <= 1e-9


async def group_state(conn, *, group_id: str, primary_intent_id: str | None
                      = None, at: float | None = None) -> dict:
    """WHAT THE GROUP HOLDS, WHAT MAY STILL FILL, AND WHAT IS RESERVED --
    from the book (intents and fills) and migration 157's records. Never
    writes. The invariant it checks:

        hedge held + hedge fill-capable  <=  confirmed primary

    where fill-capable counts EVERY live or potentially-live order quantity
    (accepted, pending-new, partially filled leaves, pending cancel,
    pending replace, a lost acknowledgement, a claim with no recorded
    outcome)."""
    now = float(at if at is not None else time.time())
    legs = [dict(r) for r in await conn.fetch(
        "SELECT i.intent_id, i.leg_role, i.state, i.us_market_slug, "
        "       i.order_intent, i.venue_order_id, i.limit_price::float8 AS "
        "       limit_price, i.quantity::float8 AS quantity, "
        "       coalesce(i.residual_qty,0)::float8 AS residual_qty, "
        "       i.closed_at, i.closed_reason, i.decision_ref, i.created_at, "
        "       i.event_key, i.account_id, i.venue, "
        "       coalesce((SELECT sum(f.qty) FROM bettor_funded_fills f "
        "                  WHERE f.intent_id=i.intent_id "
        "                    AND f.direction='ENTRY'),0)::float8 AS filled, "
        "       coalesce((SELECT sum(f.cash_usd) FROM bettor_funded_fills f "
        "                  WHERE f.intent_id=i.intent_id "
        "                    AND f.direction='ENTRY'),0)::float8 AS cash, "
        "       coalesce((SELECT sum(f.fee_usd) FROM bettor_funded_fills f "
        "                  WHERE f.intent_id=i.intent_id "
        "                    AND f.direction='ENTRY'),0)::float8 AS fees "
        "  FROM bettor_funded_intents i "
        " WHERE i.portfolio_group_id=$1 AND i.kind='ENTRY' "
        " ORDER BY i.created_at", str(group_id))]
    prim = [l_ for l_ in legs if str(l_.get("leg_role") or "PRIMARY")
            == "PRIMARY"]
    if primary_intent_id:
        prim = [l_ for l_ in prim if l_["intent_id"] == primary_intent_id] \
            or prim
    primary = prim[0] if prim else None
    hedges = [l_ for l_ in legs if l_.get("leg_role") == "HEDGE"]
    has = await has_schema(conn)
    plans, reservations, selection, cancel_events = [], [], None, {}
    if has:
        plans = [dict(r) for r in await conn.fetch(
            "SELECT * FROM bettor_standing_order_plans WHERE group_id=$1 "
            " ORDER BY created_at", str(group_id))]
        reservations = [dict(r) for r in await conn.fetch(
            "SELECT * FROM bettor_standing_capacity_reservations "
            " WHERE group_id=$1 ORDER BY opened_at", str(group_id))]
        s = await conn.fetchrow(
            "SELECT * FROM bettor_hedge_group_selection WHERE group_id=$1 "
            " ORDER BY selection_seq DESC LIMIT 1", str(group_id))
        selection = None if s is None else dict(s)
        for r in await conn.fetch(
                "SELECT hedge_intent_id, max(occurred_at) AS at "
                "  FROM bettor_standing_order_events WHERE group_id=$1 "
                "   AND event_kind='CANCEL_REQUESTED' "
                " GROUP BY hedge_intent_id", str(group_id)):
            cancel_events[r["hedge_intent_id"]] = r["at"]
    by_xid = {}
    for h in hedges:
        xid = _obj(h.get("decision_ref")).get("xavier_decision_id")
        if xid:
            by_xid[str(xid)] = h
    orders, fill_capable, live = [], 0.0, 0
    for h in hedges:
        st = str(h["state"])
        potentially_live = st in LIVE_BOOK_STATES
        fc = max(0.0, float(h["quantity"]) - float(h["filled"])) \
            if potentially_live else 0.0
        fill_capable += fc
        live += int(potentially_live)
        standing = is_standing_hedge(h)
        if not potentially_live:
            life = "%s:%s" % (L_TERMINAL, st)
        elif st == "UNRESOLVED":
            life = L_AMBIGUOUS
        elif st in ("INTENT_RECORDED", "SEND_ATTEMPTED"):
            life = L_SENT
        elif h["intent_id"] in cancel_events:
            life = L_CANCEL_REQUESTED
        elif float(h["filled"]) > 0:
            life = L_PARTIAL
        else:
            life = L_RESTING
        plan = next((p for p in plans if p.get("hedge_intent_id")
                     == h["intent_id"] or p.get("xavier_decision_id") ==
                     _obj(h.get("decision_ref")).get("xavier_decision_id")),
                    None)
        so = _obj(h.get("decision_ref")).get("standing_order") or {}
        orders.append({
            "intent_id": h["intent_id"], "standing": standing,
            "plan_id": None if plan is None else plan["plan_id"],
            "candidate_id": "%s#%s" % (h["us_market_slug"],
                                       h["order_intent"]),
            "us_market_slug": h["us_market_slug"],
            "order_intent": h["order_intent"],
            "venue_order_id": h["venue_order_id"],
            "limit_price": h["limit_price"], "quantity": h["quantity"],
            "filled_qty": round(float(h["filled"]), 6),
            "residual_qty": round(float(h["residual_qty"]), 6),
            "fill_capable_qty": round(fc, 6), "book_state": st,
            "potentially_live": potentially_live, "lifecycle_state": life,
            "good_till": so.get("good_till"),
            "cost_usd": round(float(h["cash"]), 6),
            "fees_usd": round(float(h["fees"]), 6)})
    # A CLAIM WITH NO ORDER ROW IS POTENTIALLY LIVE TOO: the process may have
    # died between the claim and the intent write.
    claimed_unbound = []
    if has:
        for p in plans:
            if p.get("hedge_intent_id") or str(p["xavier_decision_id"]) in \
                    by_xid:
                continue
            res = next((r for r in reservations if r["plan_id"]
                        == p["plan_id"]), None)
            if res is None or res["state"] != "RESERVED":
                continue
            claimed_unbound.append({"plan_id": p["plan_id"],
                                    "xavier_decision_id":
                                        p["xavier_decision_id"],
                                    "quantity": float(p["quantity"])})
            fill_capable += float(p["quantity"])
            live += 1
    primary_qty = 0.0 if primary is None else float(primary["residual_qty"])
    hedge_held = sum(float(h["residual_qty"]) for h in hedges
                     if h.get("closed_at") is None)
    covered = min(hedge_held, primary_qty)
    current = next((o for o in orders if o["potentially_live"]), None)
    lifecycle = (current["lifecycle_state"] if current is not None else
                 (L_CLAIMED if claimed_unbound else
                  (orders[-1]["lifecycle_state"] if orders else L_NONE)))
    reserved = [r for r in reservations if r["state"] == "RESERVED"]
    return {
        "group_id": group_id,
        "primary": None if primary is None else {
            "intent_id": primary["intent_id"],
            "us_market_slug": primary["us_market_slug"],
            "state": primary["state"],
            "quantity_ordered": primary["quantity"],
            "confirmed_qty": round(primary_qty, 6),
            "filled_qty": round(float(primary["filled"]), 6),
            "cost_usd": round(float(primary["cash"]), 6),
            "entry_fees_usd": round(float(primary["fees"]), 6),
            "closed_at": primary.get("closed_at"),
            "closed_reason": primary.get("closed_reason"),
            "event_key": primary.get("event_key"),
            "account_id": primary.get("account_id"),
            "venue": primary.get("venue")},
        "confirmed_primary_qty": round(primary_qty, 6),
        "hedge_held_qty": round(hedge_held, 6),
        "covered_qty": round(covered, 6),
        "uncovered_qty": round(max(0.0, primary_qty - covered), 6),
        "fill_capable_qty": round(fill_capable, 6),
        "live_or_potentially_live_orders": live,
        "claimed_unbound": claimed_unbound,
        "orders": orders,
        # RESTING ORDERS ARE OBLIGATIONS, NOT PROTECTION: listed apart from
        # the filled protection above.
        "resting_orders": [o for o in orders if o["potentially_live"]],
        "filled_protection": [
            {k: o[k] for k in ("intent_id", "candidate_id", "residual_qty",
                               "filled_qty", "cost_usd", "fees_usd")}
            for o in orders if o["residual_qty"] > 0],
        "capacity_remaining_qty": round(max(
            0.0, primary_qty - hedge_held - fill_capable), 6),
        "invariant": {
            "rule": "hedge held + hedge fill-capable <= confirmed primary",
            "hedge_held_plus_fill_capable": round(hedge_held + fill_capable,
                                                  6),
            "confirmed_primary": round(primary_qty, 6),
            "holds": hedge_held + fill_capable <= primary_qty + 1e-9},
        "lifecycle_state": lifecycle,
        "current_order": current,
        "plans": plans, "reservations": reservations,
        "reserved": reserved,
        "selection": selection,
        "mode": MODE_STRICT_FALLBACK,
        "exchange_linked_exclusivity": EXCHANGE_LINKED_EXCLUSIVITY,
        "read_at": now}


# ═════════════════════════════════════════════════════════════════════
# THE APPEND-ONLY LIFECYCLE
# ═════════════════════════════════════════════════════════════════════

async def record_event(conn, *, kind: str, source: str, group_id: str,
                       account_id: str, venue: str, key: str | None = None,
                       primary_intent_id=None, plan_id=None,
                       hedge_intent_id=None, venue_order_id=None,
                       xavier_decision_id=None, lifecycle_state=None,
                       primary_qty=None, filled_qty=None,
                       fill_capable_qty=None, evidence: dict | None = None,
                       at: float | None = None) -> dict:
    """APPEND ONE LIFECYCLE FACT, idempotently on `key` (default: the fact's
    own content). Never raises."""
    if kind not in EV:
        return {"ok": False, "refusal": "UNKNOWN_EVENT_KIND", "kind": kind}
    if source not in SOURCES:
        return {"ok": False, "refusal": "UNKNOWN_EVENT_SOURCE",
                "source": source}
    when = float(at if at is not None else time.time())
    if key is None:
        key = "spe:" + hashlib.sha256(json.dumps(
            [kind, group_id, plan_id, hedge_intent_id, venue_order_id,
             filled_qty, fill_capable_qty, lifecycle_state,
             round(when, 3)], default=str).encode()).hexdigest()[:32]
    try:
        async with conn.transaction():
            eid = await conn.fetchval(
                "INSERT INTO bettor_standing_order_events (idempotency_key, "
                " account_id, venue, group_id, primary_intent_id, plan_id, "
                " hedge_intent_id, venue_order_id, xavier_decision_id, "
                " event_kind, source, lifecycle_state, primary_qty, "
                " filled_qty, fill_capable_qty, evidence, occurred_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,"
                " $16::jsonb, to_timestamp($17)) ON CONFLICT "
                " (idempotency_key) DO NOTHING RETURNING event_id",
                str(key), str(account_id), str(venue), str(group_id),
                primary_intent_id, plan_id, hedge_intent_id, venue_order_id,
                xavier_decision_id, kind, source, lifecycle_state,
                None if primary_qty is None else float(primary_qty),
                None if filled_qty is None else float(filled_qty),
                None if fill_capable_qty is None else float(fill_capable_qty),
                json.dumps(evidence or {}, default=str), when)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": "EVENT_WRITE_FAILED",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    return {"ok": True, "appended": eid is not None, "event_id": eid,
            "key": key, "kind": kind}


async def events(conn, *, group_id: str | None = None,
                 hedge_intent_id: str | None = None,
                 limit: int = 200) -> list:
    if not await has_schema(conn):
        return []
    where, args = [], []
    if group_id:
        args.append(group_id)
        where.append("group_id=$%d" % len(args))
    if hedge_intent_id:
        args.append(hedge_intent_id)
        where.append("hedge_intent_id=$%d" % len(args))
    args.append(int(limit))
    rows = await conn.fetch(
        "SELECT * FROM bettor_standing_order_events "
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + "ORDER BY event_id LIMIT $%d" % len(args), *args)
    out = []
    for r in rows:
        d = dict(r)
        d["evidence"] = _obj(d.get("evidence"))
        out.append(d)
    return out


# ═════════════════════════════════════════════════════════════════════
# CANCEL: A REQUEST, NEVER A TERMINAL STATE
# ═════════════════════════════════════════════════════════════════════

def _adapter(mod=None):
    if mod is not None:
        return mod
    import importlib
    return importlib.import_module(FX.ADAPTER_MODULE)


async def request_cancel(conn, *, order: dict, gs: dict, reason: str,
                         source: str, at: float, adapter=None,
                         policy_params: dict | None = None,
                         evidence: dict | None = None) -> dict:
    """ASK THE VENUE TO STOP A STANDING ORDER, AND CHANGE NOTHING ELSE.

    The intent keeps its state, the capacity stays reserved and the order's
    leaves stay fill-capable until the venue's own record shows it TERMINAL
    and the book holds exactly what it says filled (`bettor_funded_book.
    record_venue_terminal`, run by the servicing pass's reconciliation). A
    cancel that raced a fill and was booked as a cancellation is how filled
    inventory vanishes -- `bettor_funded_management.cancel_outstanding`
    marks CANCELLED on the acknowledgement and is therefore NOT used here.

    Behind the cancel switch the management module owns
    (`FUNDED_EXIT_SUBMISSION_ENABLED`); an ambiguous order with no venue id
    cannot be addressed and stays potentially live."""
    from . import bettor_funded_management as FM
    p = dict(policy_params or default_params())
    base = dict(group_id=gs["group_id"],
                account_id=(gs.get("primary") or {}).get("account_id") or "",
                venue=(gs.get("primary") or {}).get("venue") or "",
                primary_intent_id=(gs.get("primary") or {}).get("intent_id"),
                plan_id=order.get("plan_id"),
                hedge_intent_id=order.get("intent_id"),
                venue_order_id=order.get("venue_order_id"),
                primary_qty=gs.get("confirmed_primary_qty"),
                filled_qty=order.get("filled_qty"),
                fill_capable_qty=order.get("fill_capable_qty"), at=at)
    ev = dict(evidence or {}, reason=reason)
    if not order.get("venue_order_id"):
        return dict(await record_event(
            conn, kind="CANCEL_NOT_ADDRESSABLE", source=source,
            lifecycle_state=L_AMBIGUOUS,
            key="spe:cna:%s:%s" % (order.get("intent_id"), reason),
            evidence=dict(ev, why=("no venue order id: the order is "
                                   "potentially live and only a venue read "
                                   "can establish it")), **base),
            sent=False)
    last = (await conn.fetchval(
        "SELECT extract(epoch FROM max(occurred_at)) FROM "
        " bettor_standing_order_events WHERE hedge_intent_id=$1 "
        "   AND event_kind='CANCEL_REQUESTED'", order.get("intent_id")))
    if last is not None and at - float(last) < float(
            p.get("cancel_retry_after_s") or 30.0):
        return {"ok": True, "sent": False, "already_requested_at": float(last),
                "why": "a cancel was requested moments ago; the order is "
                       "not yet confirmed terminal"}
    if not FM.FUNDED_EXIT_SUBMISSION_ENABLED:
        return dict(await record_event(
            conn, kind="CANCEL_WITHHELD_SWITCH_DISABLED", source=source,
            lifecycle_state=order.get("lifecycle_state"),
            evidence=dict(ev, switch="FUNDED_EXIT_SUBMISSION_ENABLED"),
            **base), sent=False)
    try:
        ans = _adapter(adapter).cancel_order(
            str(order["venue_order_id"]), str(order["us_market_slug"]))
    except Exception as exc:                                    # noqa: BLE001
        ans = {"ok": False, "error": "%s: %s" % (type(exc).__name__,
                                                 str(exc)[:160])}
    kind = "CANCEL_REQUESTED" if (ans or {}).get("ok") else \
        "CANCEL_NOT_CONFIRMED"
    got = await record_event(
        conn, kind=kind, source=source,
        lifecycle_state=L_CANCEL_REQUESTED,
        evidence=dict(ev, venue_answer=ans,
                      not_terminal=("a cancel request or acknowledgement "
                                    "is not the end of the order: its leaves "
                                    "stay fill-capable and its capacity "
                                    "reserved until the venue's record is "
                                    "terminal and reconciled")),
        **base)
    return dict(got, sent=True, venue_answer=ans)


# ═════════════════════════════════════════════════════════════════════
# MAINTENANCE, UNDER THE GROUP LOCK, BEFORE THE GROUP IS DECIDED
# ═════════════════════════════════════════════════════════════════════

async def _authorization(conn, at: float) -> dict:
    from . import bettor_funded_activation as FA
    try:
        rec = FA._obj(await FA._state(conn, FA.AUTHORIZATION_KEY)) or {}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "why": type(exc).__name__}
    exp = rec.get("expires_at")
    try:
        exp = None if exp is None else float(exp)
    except (TypeError, ValueError):
        exp = None
    valid = bool(rec) and not rec.get("revoked") and exp is not None \
        and exp > at
    return {"ok": valid, "expires_at": exp, "revoked": bool(
        rec.get("revoked"))}


async def _market_state(conn, slug: str) -> str | None:
    """The newest market state a venue market event reported for `slug`."""
    v = await conn.fetchval(
        "SELECT evidence->>'market_state' FROM bettor_standing_order_events "
        " WHERE event_kind='VENUE_MARKET_EVENT' "
        "   AND evidence->>'market_slug'=$1 "
        "   AND evidence->>'market_state' IS NOT NULL "
        " ORDER BY event_id DESC LIMIT 1", str(slug))
    return None if v is None else str(v)


NOT_TRADING_MARKET_STATES = ("SUSPENDED", "HALTED", "CLOSED", "SETTLED",
                             "RESOLVED", "EXPIRED", "MARKET_STATE_SUSPENDED",
                             "MARKET_STATE_HALTED", "MARKET_STATE_CLOSED",
                             "MARKET_STATE_SETTLED", "MARKET_STATE_RESOLVED",
                             "MARKET_STATE_EXPIRED")


async def maintain(conn, *, account_id: str, venue: str, group_id: str,
                   primary_intent_id: str, at: float, source: str,
                   adapter=None, policy: dict | None = None) -> dict:
    """RECONCILE THE GROUP'S STANDING ORDER FROM WHAT THE BOOK NOW HOLDS.

    The venue reads already ran (`manage` -> `bettor_funded_book.recover`
    ingests every live order's executions and applies a TERMINAL state only
    when the venue's cumulative quantity equals the ledger's). This step:
    binds a plan to the intent its send created; records fills, the first
    fill's SELECTION of the instrument and a confirmed terminal state; and
    releases capacity ONLY on that confirmed terminal state. It asks the
    venue to cancel -- and changes nothing else -- when the invariant is
    broken (the confirmed primary shrank), the primary is closed or settled,
    the authorization lapsed, the market stopped trading or the expiry
    passed without a terminal read. Never raises."""
    out: dict[str, Any] = {"ok": True, "group_id": group_id, "actions": []}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA)
    pol = policy or code_default()
    params = dict(pol.get("params") or default_params())
    try:
        gs = await group_state(conn, group_id=group_id,
                               primary_intent_id=primary_intent_id, at=at)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal="GROUP_STATE_UNREADABLE",
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
    base = dict(account_id=account_id, venue=venue, group_id=group_id,
                primary_intent_id=primary_intent_id)
    # ── 1 · BIND each plan to the intent its send created (by decision id)
    for p in gs["plans"]:
        if p.get("hedge_intent_id"):
            continue
        iid = await conn.fetchval(
            "SELECT intent_id FROM bettor_funded_intents WHERE "
            " decision_ref->>'xavier_decision_id'=$1 AND kind='ENTRY' "
            " ORDER BY created_at LIMIT 1", str(p["xavier_decision_id"]))
        if iid:
            await _bind(conn, plan_id=p["plan_id"], intent_id=iid)
            out["actions"].append({"bound": p["plan_id"], "intent_id": iid})
    # ── 2 · A CLAIM THAT NEVER BECAME AN ORDER: released only on evidence
    from . import bettor_xavier as XV
    for cu in gs["claimed_unbound"]:
        st = await XV.execution_state(
            conn, xavier_decision_id=cu["xavier_decision_id"])
        # NOT_CLAIMED: every send claims first, and this step runs under the
        # same group lock as the placement, so an unclaimed plan sent
        # nothing. NOT_SENT: the claim was answered by evidence. A claim
        # with no recorded outcome stays potentially live.
        if st.get("status") in (XV.X_NOT_SENT, XV.X_NOT_CLAIMED):
            rel = await _release(conn, plan_id=cu["plan_id"],
                                 reason="NEVER_SENT", evidence={
                                     "xavier_execution_status":
                                         st.get("status")})
            await record_event(conn, kind="CAPACITY_RELEASED",
                               source=source, plan_id=cu["plan_id"],
                               key="spe:rel:%s" % cu["plan_id"],
                               evidence={"reason": "NEVER_SENT",
                                         "released": rel}, at=at, **base)
    gs = await group_state(conn, group_id=group_id,
                           primary_intent_id=primary_intent_id, at=at)
    out["state_before"] = {k: gs.get(k) for k in (
        "confirmed_primary_qty", "hedge_held_qty", "fill_capable_qty",
        "lifecycle_state", "live_or_potentially_live_orders", "invariant")}
    # ── 3 · FILLS, SELECTION AND TERMINAL CONFIRMATION, PER ORDER ────────
    for o in gs["orders"]:
        if not o["standing"]:
            continue
        ob = dict(base, plan_id=o["plan_id"], hedge_intent_id=o["intent_id"],
                  venue_order_id=o["venue_order_id"],
                  primary_qty=gs["confirmed_primary_qty"],
                  filled_qty=o["filled_qty"],
                  fill_capable_qty=o["fill_capable_qty"])
        if o["filled_qty"] > 0:
            await record_event(
                conn, kind="FILL_OBSERVED", source=source,
                key="spe:fill:%s:%s" % (o["intent_id"], o["filled_qty"]),
                lifecycle_state=o["lifecycle_state"], at=at,
                evidence={"from": "bettor_funded_fills (the one ledger)",
                          "touching_the_price_is_not_a_fill": True,
                          "covered_qty": gs["covered_qty"],
                          "uncovered_qty": gs["uncovered_qty"],
                          "remaining_fillable_on_the_live_order":
                              o["fill_capable_qty"]}, **ob)
            sel = await select_on_first_fill(conn, gs=gs, order=o, at=at,
                                             source=source)
            if sel.get("selected"):
                out["actions"].append({"selected": o["candidate_id"]})
        if o["book_state"] == "UNRESOLVED":
            await record_event(
                conn, kind="AMBIGUOUS_NO_COMPETING_ORDER", source=source,
                key="spe:amb:%s" % o["intent_id"],
                lifecycle_state=L_AMBIGUOUS, at=at,
                evidence={"why": ("the send's answer was lost: the order is "
                                  "potentially live for its full remaining "
                                  "quantity, no competing order is created "
                                  "and nothing is resent -- only a venue "
                                  "read resolves it")}, **ob)
        if not o["potentially_live"]:
            res = next((r for r in gs["reserved"]
                        if r["plan_id"] == o["plan_id"]), None)
            if res is not None:
                rel = await _release(
                    conn, plan_id=o["plan_id"],
                    reason="TERMINAL_CONFIRMED_BY_THE_VENUE",
                    evidence={"book_state": o["book_state"],
                              "filled_qty": o["filled_qty"]})
                await record_event(
                    conn, kind="TERMINAL_CONFIRMED", source=source,
                    key="spe:term:%s" % o["intent_id"],
                    lifecycle_state=o["lifecycle_state"], at=at,
                    evidence={"book_state": o["book_state"],
                              "final_filled_qty": o["filled_qty"],
                              "confirmed_by": (
                                  "bettor_funded_book.record_venue_terminal:"
                                  " the venue's own record, terminal, with "
                                  "its cumulative quantity equal to the "
                                  "ledger's"),
                              "capacity_released": rel}, **ob)
                out["actions"].append({"terminal": o["intent_id"],
                                       "released": rel})
    gs = await group_state(conn, group_id=group_id,
                           primary_intent_id=primary_intent_id, at=at)
    out["state"] = gs
    cur = gs.get("current_order")
    if cur is None or not cur.get("standing"):
        return out
    # ── 4 · A LIVE STANDING ORDER THAT MUST STOP ─────────────────────────
    reasons = []
    prim = gs.get("primary") or {}
    if not gs["invariant"]["holds"]:
        reasons.append(("CAPACITY_EXCEEDED", {
            "invariant": gs["invariant"],
            "why": ("the confirmed primary is below hedge held + fill-capable "
                    "(the primary was reduced or corrected): the live order "
                    "is cancelled first, and the unresolved exposure is "
                    "reported until it is terminal")}))
    if prim.get("closed_at") is not None or \
            gs["confirmed_primary_qty"] <= 1e-9:
        reasons.append(("PRIMARY_CLOSED", {
            "closed_reason": prim.get("closed_reason")}))
    auth = await _authorization(conn, at)
    if not auth["ok"]:
        reasons.append(("AUTHORIZATION_EXPIRED", auth))
    for slug in (cur["us_market_slug"], prim.get("us_market_slug")):
        ms = await _market_state(conn, slug) if slug else None
        if ms and ms.upper() in NOT_TRADING_MARKET_STATES:
            reasons.append(("MARKET_SUSPENDED_OR_CLOSED", {
                "market_slug": slug, "market_state": ms}))
    gt = cur.get("good_till")
    if gt:
        try:
            gte = _dt.datetime.strptime(gt, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=_dt.timezone.utc).timestamp()
        except ValueError:
            gte = None
        if gte is not None and at > gte + 5.0:
            reasons.append(("EXPIRY_PASSED", {
                "good_till": gt, "why": ("the venue should have expired it; "
                                         "until a read says so it stays "
                                         "fill-capable")}))
    for kind, ev in reasons:
        await record_event(conn, kind=kind, source=source,
                           key="spe:%s:%s:%s" % (kind.lower(),
                                                 cur["intent_id"],
                                                 int(at // 60)),
                           plan_id=cur["plan_id"],
                           hedge_intent_id=cur["intent_id"],
                           venue_order_id=cur["venue_order_id"],
                           lifecycle_state=cur["lifecycle_state"],
                           primary_qty=gs["confirmed_primary_qty"],
                           filled_qty=cur["filled_qty"],
                           fill_capable_qty=cur["fill_capable_qty"],
                           evidence=ev, at=at, **base)
    if reasons:
        out["cancel"] = await request_cancel(
            conn, order=cur, gs=gs, reason=reasons[0][0], source=source,
            at=at, adapter=adapter, policy_params=params)
        out["actions"].append({"cancel_requested_because":
                               [r[0] for r in reasons]})
    return out


async def sweep_orphaned(conn, *, account_id: str, venue: str,
                         held_primary_ids: set, at: float, source: str,
                         adapter=None, policy: dict | None = None) -> dict:
    """A STANDING ORDER WHOSE PRIMARY IS NO LONGER AN OPEN POSITION (exited,
    settled, voided) is still Xavier's until it is confirmed terminal: each
    such group is maintained under its own group lock -- which cancels the
    order (PRIMARY_CLOSED) and releases its capacity only on the venue's
    terminal read. Never raises."""
    from . import bettor_xavier as XV
    out: dict[str, Any] = {"examined": 0, "maintained": [],
                           "skipped_in_flight": []}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA)
    try:
        rows = await conn.fetch(
            "SELECT DISTINCT p.group_id, p.primary_intent_id "
            "  FROM bettor_standing_order_plans p "
            "  JOIN bettor_standing_capacity_reservations r "
            "    ON r.plan_id=p.plan_id AND r.state='RESERVED' "
            " WHERE p.account_id=$1 AND upper(p.venue)=upper($2)",
            str(account_id), str(venue))
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, error=type(exc).__name__)
    for r in rows:
        if str(r["primary_intent_id"]) in held_primary_ids:
            continue
        out["examined"] += 1
        lock = await XV.try_group_lock(conn, str(r["group_id"]))
        if not lock.get("ok"):
            out["skipped_in_flight"].append(r["group_id"])
            continue
        try:
            got = await maintain(conn, account_id=account_id, venue=venue,
                                 group_id=str(r["group_id"]),
                                 primary_intent_id=str(
                                     r["primary_intent_id"]), at=at,
                                 source=source, adapter=adapter,
                                 policy=policy)
        finally:
            await XV.release_group_lock(conn, lock)
        out["maintained"].append({"group_id": r["group_id"],
                                  "actions": got.get("actions"),
                                  "cancel": got.get("cancel")})
    return dict(out, ok=True)


async def _bind(conn, *, plan_id: str, intent_id: str) -> None:
    await conn.execute(
        "UPDATE bettor_standing_order_plans SET hedge_intent_id=$2 "
        " WHERE plan_id=$1 AND hedge_intent_id IS NULL", plan_id, intent_id)
    await conn.execute(
        "UPDATE bettor_standing_capacity_reservations SET hedge_intent_id=$2 "
        " WHERE plan_id=$1 AND hedge_intent_id IS NULL AND state='RESERVED'",
        plan_id, intent_id)


async def _release(conn, *, plan_id: str, reason: str,
                   evidence: dict | None = None) -> dict:
    """RELEASE THE PLAN'S CAPACITY -- the database refuses it unless the
    order is confirmed terminal (or was never sent)."""
    try:
        async with conn.transaction():
            n = await conn.execute(
                "UPDATE bettor_standing_capacity_reservations SET "
                " state='RELEASED', released_at=now(), release_reason=$2, "
                " release_evidence=$3::jsonb "
                " WHERE plan_id=$1 AND state='RESERVED'", plan_id, reason,
                json.dumps(evidence or {}, default=str))
    except Exception as exc:                                    # noqa: BLE001
        return {"released": False, "refusal": "RELEASE_REFUSED",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:240])}
    return {"released": str(n).endswith(" 1"), "reason": reason}


async def select_on_first_fill(conn, *, gs: dict, order: dict, at: float,
                               source: str) -> dict:
    """THE FIRST FILL SELECTS THE INSTRUMENT, once. Later partial fills of
    the same instrument add to it; a different instrument later is a
    TRANSITION, evaluated and recorded separately."""
    if gs.get("selection") is not None:
        return {"selected": False, "already": gs["selection"]["candidate_id"]}
    first = await conn.fetchrow(
        "SELECT fill_id, at FROM bettor_funded_fills WHERE intent_id=$1 "
        " AND direction='ENTRY' ORDER BY at, fill_id LIMIT 1",
        order["intent_id"])
    if first is None:
        return {"selected": False}
    prim = gs.get("primary") or {}
    try:
        await conn.execute(
            "INSERT INTO bettor_hedge_group_selection (group_id, "
            " selection_seq, account_id, venue, primary_intent_id, "
            " candidate_id, venue_slug, order_intent, hedge_intent_id, "
            " plan_id, selected_by, first_fill_id, evidence, selected_at) "
            "VALUES ($1,1,$2,$3,$4,$5,$6,$7,$8,$9,'FIRST_FILL',$10,$11::jsonb,"
            " $12) ON CONFLICT DO NOTHING", gs["group_id"],
            str(prim.get("account_id")), str(prim.get("venue")),
            str(prim.get("intent_id")), order["candidate_id"],
            order["us_market_slug"], order["order_intent"],
            order["intent_id"], order.get("plan_id"),
            str(first["fill_id"]), json.dumps({
                "covered_qty": gs["covered_qty"],
                "uncovered_qty": gs["uncovered_qty"],
                "remaining_fillable_on_the_live_order":
                    order["fill_capable_qty"]}),
            first["at"])
    except Exception as exc:                                    # noqa: BLE001
        return {"selected": False, "error": type(exc).__name__}
    await record_event(
        conn, kind="INSTRUMENT_SELECTED", source=source,
        key="spe:sel:%s:1" % gs["group_id"], account_id=str(prim.get(
            "account_id")), venue=str(prim.get("venue")),
        group_id=gs["group_id"], primary_intent_id=prim.get("intent_id"),
        plan_id=order.get("plan_id"), hedge_intent_id=order["intent_id"],
        venue_order_id=order.get("venue_order_id"),
        lifecycle_state=order.get("lifecycle_state"),
        primary_qty=gs["confirmed_primary_qty"],
        filled_qty=order["filled_qty"],
        fill_capable_qty=order["fill_capable_qty"], at=at,
        evidence={"selected_instrument": order["candidate_id"],
                  "first_fill_id": str(first["fill_id"]),
                  "rule": ("once a hedge instrument has any fill it is the "
                           "group's selected instrument; changing it is a "
                           "separately evaluated TRANSITION")})
    return {"selected": True}


# ═════════════════════════════════════════════════════════════════════
# THE GOVERNED STEP, AFTER THE GROUP'S DECISION IS RECORDED
# ═════════════════════════════════════════════════════════════════════

def _held_leg(facts: dict):
    return (facts or {}).get("held_leg")


def _candidate_leg(admitted_all, cid):
    for a in admitted_all or []:
        if str(a.get("condition_id")) == str(cid):
            return a.get("leg"), a
    return None, None


def _row_for(ranking, cid):
    for r in list((ranking or {}).get("ranked") or []) + list(
            (ranking or {}).get("not_rankable") or []):
        if str(r.get("condition_id")) == str(cid):
            return r
    return None


async def valuation_vs_hold(conn, *, facts: dict, admitted: dict,
                            held_leg, hedge_leg, primary_qty, hedge_qty,
                            cost_price, fee_usd, floor: dict, policy: dict,
                            at: float, candidate_id: str) -> dict:
    """IS THIS STANDING ORDER, IF IT FILLS, PREFERRED TO HOLD UNDER THE
    ACTIVE XAVIER POLICY? The approved payout-state distribution
    (`bettor_funded_model.predict_distribution`) prices the pair's table at
    this price and quantity; HOLD is valued under the SAME measure and table
    (`bettor_funded_pair_cycle._hold_value_under`); the choice between the
    two is `agents.xavier_policy.apply` -- the capital-preservation leg of
    the active policy (disabled at a zero sacrifice, where the expected-value
    winner stands). Nothing here ranks anything else."""
    from . import bettor_funded_indirect_pair as FIP
    from . import bettor_funded_model as FMD
    from . import bettor_funded_pair_cycle as PC
    from .agents import xavier_policy as XP
    out: dict[str, Any] = {"candidate_id": candidate_id, "ok": False}
    try:
        hedge_at = replace(hedge_leg, cost_cents_per_unit=int(round(
            float(cost_price) * 100)))
        # THE SAME HELD BASIS THE FLOOR WAS CLASSIFIED ON (the actual cost,
        # floored to the cent), so the table, the structure and the floor
        # describe one position.
        held_cents = (floor or {}).get("held_refund_cents") or getattr(
            held_leg, "cost_cents_per_unit", None)
        held_at = replace(held_leg, quantity=int(primary_qty),
                          cost_cents_per_unit=int(held_cents))
        # THE STRUCTURE AT THIS PRICE, from the one classifier -- a
        # cancellation refunds each leg's own basis, so the structure priced
        # at the quote would not match a table priced at the limit.
        found = PC.discover(
            held_leg=held_at, candidate_legs=[hedge_at],
            sport_permits_tie=facts.get("sport_permits_tie"),
            fixture_can_void=bool(facts.get("fixture_can_void", True)),
            fixture_can_postpone=bool(facts.get("fixture_can_postpone",
                                                True)))
        if not found.get("admitted"):
            return dict(out, refusal=R_DESIRABILITY_NOT_ESTABLISHED,
                        structure_refusal=found.get("refusal"),
                        rejected=found.get("rejected"))
        admitted = found["admitted"][0]
        pv = FIP.position_worst_case(
            held_leg=held_at, hedge_leg=hedge_at, hedge_qty=int(hedge_qty),
            sport_permits_tie=bool(facts.get("sport_permits_tie")),
            fee_usd=float(fee_usd), fee_basis="STANDING_ORDER_TAKER_FEE",
            fixture_can_void=bool(facts.get("fixture_can_void", True)),
            fixture_can_postpone=bool(facts.get("fixture_can_postpone",
                                                True)))
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, refusal=R_DESIRABILITY_NOT_ESTABLISHED,
                    error=type(exc).__name__)
    if not pv.get("ok"):
        return dict(out, refusal=R_DESIRABILITY_NOT_ESTABLISHED,
                    position_value_refusal=pv.get("refusal"))
    mi = dict(facts.get("model_inputs") or {})
    mi.update((facts.get("model_inputs_by_candidate") or {}).get(
        candidate_id, {}))
    try:
        pred = await FMD.predict_distribution(
            conn, structure=admitted["structure"],
            primary_cost_cents=int(held_cents),
            hedge_cost_cents=int(round(float(cost_price) * 100)),
            overtime_included=mi.get("overtime_included"),
            primary_probability=mi.get("primary_probability"),
            primary_source=mi.get("primary_source"),
            primary_partial_probability=mi.get("primary_partial_probability"),
            primary_calibration=mi.get("primary_calibration"),
            position_value=pv, at=at)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, refusal=R_DESIRABILITY_NOT_ESTABLISHED,
                    error=type(exc).__name__)
    if not pred.get("ok"):
        return dict(out, refusal=R_DESIRABILITY_NOT_ESTABLISHED,
                    prediction_refusal=pred.get("refusal"))
    rp = dict(pred["region_probabilities"])
    mpv = pred.get("merged_position_value") or pv
    ev_pair = 0.0
    for r in mpv.get("regions") or ():
        p = rp.get(r.get("region"))
        if p is None:
            return dict(out, refusal=R_DESIRABILITY_NOT_ESTABLISHED,
                        missing_region=r.get("region"))
        ev_pair += float(p) * float(r.get("payout_usd") or 0.0)
    ev_pair = round(ev_pair - float(mpv.get("cost_usd") or 0.0)
                    - float(fee_usd), 6)
    ev_hold = PC._hold_value_under(mpv, rp)
    if ev_hold is None:
        return dict(out, refusal=R_DESIRABILITY_NOT_ESTABLISHED,
                    why="HOLD could not be valued on the same table")
    # THE WORST CASES, from the WHOLE-POSITION table at this price (every
    # held contract, covered or not; unknown treatments valued at 0).
    whole = classify_floor(
        held_leg=held_leg, hedge_leg=hedge_leg, primary_qty=primary_qty,
        primary_cost_usd=(floor.get("costs") or {}).get(
            "primary_cost_usd_confirmed"),
        entry_fees_usd=(floor.get("costs") or {}).get(
            "entry_fees_usd_confirmed"),
        hedge_qty=hedge_qty, hedge_cost_price=cost_price,
        hedge_fee_usd_=fee_usd, sport_permits_tie=facts.get(
            "sport_permits_tie"),
        fixture_can_void=bool(facts.get("fixture_can_void", True)),
        fixture_can_postpone=bool(facts.get("fixture_can_postpone", True)),
        whole_position=True)
    worst_pair = whole.get("worst_case_net_usd")
    wcosts = whole.get("costs") or {}
    held_only = [float((r.get("per_leg_cents") or [0])[0] or 0) / 100.0
                 * float(primary_qty) for r in (whole.get("regions") or [])]
    worst_hold = (None if not held_only or not wcosts else round(
        min(held_only) - float(wcosts.get("primary_cost_usd") or 0)
        - float(wcosts.get("entry_fees_usd") or 0), 6))
    hold_c = {"action": "HOLD", "value_usd": ev_hold,
              "worst_case_net_usd": worst_hold, "candidate_id": None,
              "qty": float(primary_qty)}
    so_c = {"action": ACTION, "value_usd": ev_pair,
            "worst_case_net_usd": worst_pair, "candidate_id": candidate_id,
            "qty": float(hedge_qty), "plan_digest": None}
    best = max((hold_c, so_c), key=lambda c: (
        c["value_usd"], -1e18 if c["worst_case_net_usd"] is None
        else c["worst_case_net_usd"]))
    verdict = {"selected": best["action"], "selected_candidate": best,
               "candidates": [hold_c, so_c]}
    choice = XP.apply(verdict, policy)
    return dict(out, ok=True, refusal=None,
                desirable=choice.get("selected") == ACTION,
                ev_pair_if_filled_usd=ev_pair, ev_hold_usd=ev_hold,
                increment_vs_hold_usd=round(ev_pair - ev_hold, 6),
                worst_case_pair_usd=worst_pair, worst_case_hold_usd=worst_hold,
                policy_choice={k: choice.get(k) for k in (
                    "policy_key", "version", "source", "selection_rule",
                    "selected", "identical_to_approved_ev_policy",
                    "capital_preservation")},
                region_probabilities_came_from=(
                    "APPROVED_DISTRIBUTION:%s@%s" % (pred.get("model_key"),
                                                     pred.get(
                                                         "model_version"))),
                probabilities=rp)


async def evaluate_candidate(conn, *, facts, admitted_all, ranking, cid,
                             gs, qty, spo_params, xavier_policy, at) -> dict:
    """ONE MONITORED CANDIDATE: its protective price on its own grid, its
    floor class there, and its value against HOLD. Never sends."""
    hedge_leg, adm = _candidate_leg(admitted_all, cid)
    held_leg = _held_leg(facts)
    slug, side = HS.split_identity(cid)
    out: dict[str, Any] = {"candidate_id": cid, "venue_slug": slug,
                           "side": side, "submitted": False,
                           "is": "MONITORED_CANDIDATE"}
    if hedge_leg is None or held_leg is None or adm is None:
        return dict(out, ok=False, refusal="THE_CANDIDATE_OR_HELD_LEG_WAS_"
                    "NOT_BUILT_THIS_REVIEW")
    if side not in ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"):
        return dict(out, ok=False, refusal="THE_CANDIDATE_NAMES_NO_SIDE")
    prim = gs.get("primary") or {}
    row = _row_for(ranking, cid)
    grid = tick_grid(row)
    pp = protective_price(
        held_leg=held_leg, hedge_leg=hedge_leg, side=side,
        primary_qty=gs["confirmed_primary_qty"],
        primary_cost_usd=prim.get("cost_usd"),
        entry_fees_usd=prim.get("entry_fees_usd"), hedge_qty=qty, grid=grid,
        policy_params=spo_params, at=at,
        sport_permits_tie=facts.get("sport_permits_tie"),
        fixture_can_void=bool(facts.get("fixture_can_void", True)),
        fixture_can_postpone=bool(facts.get("fixture_can_postpone", True)))
    out["protective_price"] = {k: pp.get(k) for k in (
        "ok", "refusal", "cost_price", "wire_price", "tried", "step")}
    if not pp.get("ok"):
        out["floor"] = pp.get("floor") or pp.get("last_floor")
        out["floor_class"] = (out["floor"] or {}).get("floor_class", F_NONE)
        return dict(out, ok=False, refusal=pp.get("refusal"))
    out["floor"] = pp["floor"]
    out["floor_class"] = pp["floor"]["floor_class"]
    out["fee"] = pp["fee"]
    val = await valuation_vs_hold(
        conn, facts=facts, admitted=adm, held_leg=held_leg,
        hedge_leg=hedge_leg, primary_qty=gs["confirmed_primary_qty"],
        hedge_qty=qty, cost_price=pp["cost_price"],
        fee_usd=pp["fee"]["fee_usd"], floor=pp["floor"],
        policy=xavier_policy, at=at, candidate_id=cid)
    out["valuation"] = val
    out["settlement_identity"] = settlement_identity(
        held_leg=held_leg, hedge_leg=hedge_leg,
        sport_permits_tie=facts.get("sport_permits_tie"),
        fixture_can_void=bool(facts.get("fixture_can_void", True)),
        fixture_can_postpone=bool(facts.get("fixture_can_postpone", True)))
    out["grid"] = {"step": str(grid.get("step")), "tick": grid.get("tick"),
                   "tick_source": grid.get("tick_source")}
    return dict(out, ok=bool(val.get("ok")), desirable=bool(
        val.get("desirable")), increment_vs_hold_usd=val.get(
        "increment_vs_hold_usd"), refusal=val.get("refusal"))


def _pick(evaluated: list, selected_cid: str | None) -> dict | None:
    """ONE instrument. A selected instrument (it has filled) is the only one
    that may be used; otherwise the desirable candidate with the strongest
    floor class, then the largest value over HOLD."""
    pool = [e for e in evaluated if e.get("ok") and e.get("desirable")]
    if selected_cid is not None:
        pool = [e for e in pool if e["candidate_id"] == selected_cid]
    if not pool:
        return None
    pool.sort(key=lambda e: (-_FLOOR_RANK.get(e.get("floor_class"), 0),
                             -(e.get("increment_vs_hold_usd") or 0.0),
                             e["candidate_id"]))
    return pool[0]


async def govern(conn, *, xs, pos: dict, grp, facts: dict, dec: dict,
                 xr: dict, step: dict, admitted_all, ranking, account_id: str,
                 venue: str, adapter=None, venue_positions=None,
                 xavier_policy=None, gate=None, at: float,
                 source: str = "SCHEDULED_SERVICING",
                 spo_policy: dict | None = None) -> dict:
    """THE STANDING ORDER'S PART OF ONE GROUP REVIEW, UNDER THE GROUP LOCK.

    Called by `bettor_funded_pair_cycle.pass_once` after the group's decision
    and Xavier's record persisted, for every selected action:
      * an EXIT or REDUCE of a leg: a fill-capable standing hedge order is
        cancelled FIRST and the exit waits for its confirmed terminal state
        (the order can still fill); a primary exit that would leave more
        hedge than primary is refused and the residual valued and reported;
      * otherwise the live order is re-classified (fee schedule, settlement
        rules), re-valued against HOLD and checked for a better instrument
        or a grown capacity -- any of which CANCELS it (never a competing
        order); and with no live or potentially-live order, a HOLD decision
        may place ONE standing order on ONE instrument.
    Returns what it did; `block_dispatch` tells the pass not to send the
    group's own action. Never raises."""
    out: dict[str, Any] = {"version": VERSION, "block_dispatch": False,
                           "placed": False, "cancel": None}
    try:
        return await _govern(conn, out=out, xs=xs, pos=pos, grp=grp,
                             facts=facts, dec=dec, xr=xr, step=step,
                             admitted_all=admitted_all, ranking=ranking,
                             account_id=account_id, venue=venue,
                             adapter=adapter, venue_positions=venue_positions,
                             xavier_policy=xavier_policy, gate=gate, at=at,
                             source=source, spo_policy=spo_policy)
    except Exception as exc:                                    # noqa: BLE001
        # A RAISE HERE SENDS NOTHING NEW; an exit is held back while a
        # standing hedge order may be fill-capable (the safe direction).
        out.update(ok=False, refusal="STANDING_ORDER_STEP_RAISED",
                   error="%s: %s" % (type(exc).__name__, str(exc)[:240]))
        try:
            gs = await group_state(conn, group_id=str(
                pos.get("portfolio_group_id")), primary_intent_id=str(
                pos.get("intent_id")), at=at)
            out["block_dispatch"] = gs["fill_capable_qty"] > 1e-9
        except Exception:                                       # noqa: BLE001
            out["block_dispatch"] = True
        return out


async def _govern(conn, *, out, xs, pos, grp, facts, dec, xr, step,
                  admitted_all, ranking, account_id, venue, adapter,
                  venue_positions, xavier_policy, gate, at, source,
                  spo_policy):
    from . import bettor_funded_pair_cycle as PC
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA)
    gid = pos.get("portfolio_group_id")
    if not gid or str(pos.get("leg_role") or "PRIMARY") != "PRIMARY":
        return dict(out, ok=True, refusal="NOT_A_PRIMARY_GROUP_REVIEW")
    pol = spo_policy or await load_policy(conn)
    params = dict(pol.get("params") or default_params())
    out["policy"] = {k: pol.get(k) for k in ("policy_key", "version",
                                             "source", "why")}
    out["policy"]["enabled"] = bool(params.get("enabled"))
    out["mode"] = MODE_STRICT_FALLBACK
    out["exchange_linked_exclusivity"] = EXCHANGE_LINKED_EXCLUSIVITY
    gs = await group_state(conn, group_id=str(gid),
                           primary_intent_id=str(pos.get("intent_id")), at=at)
    base = dict(account_id=account_id, venue=venue, group_id=str(gid),
                primary_intent_id=str(pos.get("intent_id")))
    out["state"] = _brief(gs)
    action = dec.get("action")
    cur = gs.get("current_order")
    # ── 1 · AN EXIT OR REDUCE WAITS FOR THE HEDGE ORDER TO BE TERMINAL ──
    if action in PC.LEDGER_EXIT_ACTIONS:
        return await _exit_gate(conn, out=out, gs=gs, dec=dec, facts=facts,
                                pos=pos, base=base, at=at, source=source,
                                adapter=adapter, params=params)
    # ── 2 · COVERAGE OF THE ACTUAL INVENTORY, WHEN THE HEDGE HOLDS ANY ──
    if gs["hedge_held_qty"] > 0 and gs["plans"]:
        await _record_coverage(conn, gs=gs, facts=facts,
                               admitted_all=admitted_all, base=base, at=at,
                               source=source)
    held_leg = _held_leg(facts)
    qty_cap = int(math.floor(gs["confirmed_primary_qty"]
                             - gs["hedge_held_qty"] + 1e-9))
    # ── 3 · A LIVE STANDING ORDER: STILL VALID? STILL WANTED? ──────────
    if cur is not None:
        if not cur.get("standing"):
            return dict(out, ok=True, refusal=R_LIVE_ORDER_EXISTS,
                        why="a non-standing hedge order is live; nothing "
                            "competes with it")
        if cur["lifecycle_state"] in (L_AMBIGUOUS, L_SENT, L_CLAIMED):
            await record_event(
                conn, kind="AMBIGUOUS_NO_COMPETING_ORDER", source=source,
                key="spe:amb:%s" % cur["intent_id"], plan_id=cur["plan_id"],
                hedge_intent_id=cur["intent_id"],
                lifecycle_state=cur["lifecycle_state"], at=at, **base)
            return dict(out, ok=True, refusal=R_LIVE_ORDER_EXISTS)
        plan = next((p for p in gs["plans"]
                     if p["plan_id"] == cur.get("plan_id")), None)
        seen: dict = {}
        reasons = await _revalidate(conn, gs=gs, cur=cur, plan=plan,
                                    facts=facts, admitted_all=admitted_all,
                                    ranking=ranking, params=params,
                                    xavier_policy=xavier_policy, at=at,
                                    qty_cap=qty_cap, seen=seen)
        out["revalidation"] = reasons
        # THE RE-EVALUATION AGAINST HOLD, on every pass (a venue event's
        # included), whatever it concluded.
        out["valuation"] = seen.get("valuation")
        for kind, ev in reasons:
            await record_event(
                conn, kind=kind, source=source,
                key="spe:%s:%s:%s" % (kind.lower(), cur["intent_id"],
                                      int(at // 60)),
                plan_id=cur["plan_id"], hedge_intent_id=cur["intent_id"],
                venue_order_id=cur["venue_order_id"],
                lifecycle_state=cur["lifecycle_state"],
                primary_qty=gs["confirmed_primary_qty"],
                filled_qty=cur["filled_qty"],
                fill_capable_qty=cur["fill_capable_qty"],
                evidence=dict(ev, latency_disclosure=LATENCY_DISCLOSURE),
                at=at, **base)
        if reasons:
            out["cancel"] = await request_cancel(
                conn, order=cur, gs=gs, reason=reasons[0][0], source=source,
                at=at, adapter=adapter, policy_params=params)
        return dict(out, ok=True, refusal=R_LIVE_ORDER_EXISTS,
                    live_order=cur["intent_id"])
    if gs["live_or_potentially_live_orders"] > 0:
        # a claimed plan whose order row is not written yet, or a
        # non-standing hedge still in flight: potentially live
        return dict(out, ok=True, refusal=R_LIVE_ORDER_EXISTS)
    if not params.get("enabled"):
        # DISABLED: nothing new is placed. A live order placed while the
        # policy was enabled is still maintained and re-validated above (and
        # cancelled if it must be); disabling does not cancel it by itself.
        return dict(out, ok=True, refusal=R_POLICY_DISABLED)
    # ── 4 · NO LIVE ORDER: MAY ONE BE PLACED? ───────────────────────────
    if action not in (PC.ACTION_HOLD,):
        return dict(out, ok=True, refusal=R_DECISION_NOT_HOLD,
                    decision=action)
    if gate is not None or xr.get("gate_blocked"):
        return dict(out, ok=True, refusal=R_GROUP_GATED)
    if gs["confirmed_primary_qty"] < 1:
        return dict(out, ok=True, refusal=R_NO_CONFIRMED_PRIMARY)
    sel = gs.get("selection")
    if gs["hedge_held_qty"] > 0:
        # THE SELECTED INSTRUMENT'S LEG IS OPEN: the remainder would be a
        # second open HEDGE intent, which migration 131 refuses.
        await record_event(
            conn, kind="REMAINDER_REPLACEMENT_REFUSED", source=source,
            key="spe:rem:%s:%s" % (gid, gs["hedge_held_qty"]),
            primary_qty=gs["confirmed_primary_qty"],
            filled_qty=gs["hedge_held_qty"], fill_capable_qty=0.0,
            evidence={"refusal": R_REMAINDER_NEEDS_A_SECOND_HEDGE_LEG,
                      "uncovered_qty": gs["uncovered_qty"],
                      "selected_instrument": (sel or {}).get(
                          "candidate_id")}, at=at, **base)
        return dict(out, ok=True, refusal=R_REMAINDER_NEEDS_A_SECOND_HEDGE_LEG)
    if qty_cap < 1:
        return dict(out, ok=True, refusal=R_NO_CAPACITY)
    if held_leg is None:
        return dict(out, ok=True, refusal="THE_HELD_LEG_WAS_NOT_BUILT")
    evaluated = []
    for a in admitted_all or []:
        cid = str(a.get("condition_id"))
        evaluated.append(await evaluate_candidate(
            conn, facts=facts, admitted_all=admitted_all, ranking=ranking,
            cid=cid, gs=gs, qty=qty_cap, spo_params=params,
            xavier_policy=xavier_policy, at=at))
    out["monitored_candidates"] = [_cand_brief(e) for e in evaluated]
    selected_cid = None if sel is None else str(sel["candidate_id"])
    pick = _pick(evaluated, selected_cid)
    if pick is None and selected_cid is not None:
        alt = _pick(evaluated, None)
        if alt is not None:
            await record_event(
                conn, kind="INSTRUMENT_TRANSITION_REFUSED", source=source,
                key="spe:trans:%s:%s:%s" % (gid, alt["candidate_id"],
                                            int(at // 60)),
                evidence={"selected": selected_cid,
                          "candidate": alt["candidate_id"],
                          "refusal": R_INSTRUMENT_ALREADY_SELECTED,
                          "why": ("the group's instrument was selected by "
                                  "its first fill; a change is a separately "
                                  "evaluated transition and is not taken "
                                  "automatically")}, at=at, **base)
        return dict(out, ok=True, refusal=R_INSTRUMENT_ALREADY_SELECTED)
    if pick is None:
        best = sorted(evaluated, key=lambda e: -_FLOOR_RANK.get(
            e.get("floor_class"), 0))
        why = (best[0].get("refusal") if best else "NO_CANDIDATE")
        await record_event(
            conn, kind="PLACEMENT_REFUSED", source=source,
            key="spe:nop:%s:%s" % (gid, int(at // 60)),
            primary_qty=gs["confirmed_primary_qty"],
            evidence={"refusal": why, "candidates": [
                _cand_brief(e) for e in evaluated]}, at=at, **base)
        return dict(out, ok=True, refusal=why or R_NOT_DESIRABLE)
    # A SWITCH AFTER A TERMINAL, UNFILLED ORDER ON ANOTHER INSTRUMENT
    prev = next((p for p in reversed(gs["plans"])), None)
    if prev is not None and prev["candidate_id"] != pick["candidate_id"]:
        await record_event(
            conn, kind="INSTRUMENT_TRANSITION_EVALUATED", source=source,
            key="spe:switch:%s:%s" % (prev["plan_id"], pick["candidate_id"]),
            evidence={"from": prev["candidate_id"],
                      "to": pick["candidate_id"],
                      "previous_order_terminal_and_reconciled": True,
                      "latency_disclosure": LATENCY_DISCLOSURE}, at=at,
            **base)
    return await place(conn, out=out, xs=xs, pos=pos, facts=facts, dec=dec,
                       xr=xr, gs=gs, pick=pick, admitted_all=admitted_all,
                       evaluated=evaluated, params=params, pol=pol,
                       account_id=account_id, venue=venue, adapter=adapter,
                       venue_positions=venue_positions, at=at, source=source,
                       base=base)


def _brief(gs: dict) -> dict:
    return {k: gs.get(k) for k in (
        "confirmed_primary_qty", "hedge_held_qty", "covered_qty",
        "uncovered_qty", "fill_capable_qty",
        "live_or_potentially_live_orders", "lifecycle_state", "invariant",
        "capacity_remaining_qty")}


def _cand_brief(e: dict) -> dict:
    return {"candidate_id": e.get("candidate_id"), "is": e.get("is"),
            "submitted": False, "ok": e.get("ok"),
            "refusal": e.get("refusal"), "floor_class": e.get("floor_class"),
            "protective_price": e.get("protective_price"),
            "desirable": e.get("desirable"),
            "increment_vs_hold_usd": e.get("increment_vs_hold_usd")}


async def _revalidate(conn, *, gs, cur, plan, facts, admitted_all, ranking,
                      params, xavier_policy, at, qty_cap,
                      seen: dict | None = None) -> list:
    """WHY THE LIVE STANDING ORDER MUST BE CANCELLED, if it must. `seen`
    receives the re-valuation against HOLD, when one was made."""
    reasons = []
    seen = {} if seen is None else seen
    if plan is None:
        return reasons
    held_leg = _held_leg(facts)
    hedge_leg, adm = _candidate_leg(admitted_all, plan["candidate_id"])
    if hedge_leg is None:
        # NO LONGER ADMITTED (the classifier could not establish the
        # structure -- e.g. a settlement clause stopped being read). The leg
        # the supplier BUILT is still examined, and the loss of admission is
        # itself an invalidation.
        hedge_leg = next((lg for lg in (facts or {}).get("candidate_legs")
                          or () if str(getattr(lg, "condition_id", ""))
                          == str(plan["candidate_id"])), None)
        reasons.append(("FLOOR_INVALIDATED", {
            "because": "THE_INSTRUMENT_IS_NO_LONGER_AN_ESTABLISHED_"
                       "STRUCTURE_WITH_THE_HELD_LEG",
            "leg_still_built": hedge_leg is not None}))
    fee_now = fee_schedule_identity(at=at)
    if fee_now.get("digest") != plan["fee_schedule_identity"]:
        reasons.append(("FLOOR_INVALIDATED", {
            "because": "THE_FEE_SCHEDULE_CHANGED",
            "classified_under": plan["fee_schedule_identity"],
            "now": fee_now}))
    if held_leg is not None and hedge_leg is not None:
        si = settlement_identity(
            held_leg=held_leg, hedge_leg=hedge_leg,
            sport_permits_tie=facts.get("sport_permits_tie"),
            fixture_can_void=bool(facts.get("fixture_can_void", True)),
            fixture_can_postpone=bool(facts.get("fixture_can_postpone",
                                                True)))
        if si["digest"] != plan["settlement_identity"]:
            reasons.append(("FLOOR_INVALIDATED", {
                "because": "THE_SETTLEMENT_RULES_AS_READ_CHANGED",
                "classified_under": plan["settlement_identity"],
                "now": si["digest"]}))
        remaining = float(cur["fill_capable_qty"])
        fee = hedge_fee_usd(qty=int(max(1, round(float(plan["quantity"])))),
                            cost_price=float(plan["cost_price"]), at=at)
        prim = gs.get("primary") or {}
        fl = classify_floor(
            held_leg=held_leg, hedge_leg=hedge_leg,
            primary_qty=gs["confirmed_primary_qty"],
            primary_cost_usd=prim.get("cost_usd"),
            entry_fees_usd=prim.get("entry_fees_usd"),
            hedge_qty=float(plan["quantity"]),
            hedge_cost_price=float(plan["cost_price"]),
            hedge_fee_usd_=fee.get("fee_usd"),
            other_costs_usd=float(params.get("other_costs_usd_per_pair")
                                  or 0) * float(plan["quantity"]),
            buffer_usd=float(params.get("buffer_usd_per_pair") or 0)
            * float(plan["quantity"]),
            sport_permits_tie=facts.get("sport_permits_tie"),
            fixture_can_void=bool(facts.get("fixture_can_void", True)),
            fixture_can_postpone=bool(facts.get("fixture_can_postpone",
                                                True)))
        want = _FLOOR_RANK[params.get("minimum_floor_class", F_ORDINARY)]
        if _FLOOR_RANK[fl["floor_class"]] < want or \
                fl["floor_class"] != plan["floor_class"]:
            reasons.append(("FLOOR_INVALIDATED", {
                "because": "THE_FLOOR_CLASS_CHANGED",
                "was": plan["floor_class"], "now": fl["floor_class"],
                "reason": fl.get("reason"), "condition": fl.get(
                    "condition")}))
        if not reasons and adm is not None and cur["filled_qty"] <= 0:
            val = await valuation_vs_hold(
                conn, facts=facts, admitted=adm, held_leg=held_leg,
                hedge_leg=hedge_leg, primary_qty=gs["confirmed_primary_qty"],
                hedge_qty=float(plan["quantity"]),
                cost_price=float(plan["cost_price"]),
                fee_usd=float(fee.get("fee_usd") or 0), floor=fl,
                policy=xavier_policy, at=at,
                candidate_id=plan["candidate_id"])
            seen["valuation"] = {k: val.get(k) for k in (
                "ok", "refusal", "desirable", "ev_pair_if_filled_usd",
                "ev_hold_usd", "increment_vs_hold_usd", "worst_case_pair_usd",
                "worst_case_hold_usd", "policy_choice",
                "region_probabilities_came_from")}
            if val.get("ok") and not val.get("desirable"):
                reasons.append(("DESIRABILITY_LOST", {
                    "valuation": {k: val.get(k) for k in (
                        "ev_pair_if_filled_usd", "ev_hold_usd",
                        "increment_vs_hold_usd", "policy_choice")}}))
            elif val.get("ok"):
                # A BETTER INSTRUMENT, BY ENOUGH TO PAY FOR THE QUEUE AND THE
                # UNPROTECTED WINDOW?
                better = []
                for a in admitted_all or []:
                    cid = str(a.get("condition_id"))
                    if cid == plan["candidate_id"]:
                        continue
                    e = await evaluate_candidate(
                        conn, facts=facts, admitted_all=admitted_all,
                        ranking=ranking, cid=cid, gs=gs,
                        qty=int(plan["quantity"]), spo_params=params,
                        xavier_policy=xavier_policy, at=at)
                    if e.get("ok") and e.get("desirable") and (
                            e.get("increment_vs_hold_usd") or 0) - (
                            val.get("increment_vs_hold_usd") or 0) >= float(
                            params.get("switch_min_improvement_usd") or 0):
                        better.append(_cand_brief(e))
                if better:
                    reasons.append(("SWITCH_REQUESTED", {
                        "from": plan["candidate_id"], "to": better[0],
                        "live_increment_vs_hold_usd":
                            val.get("increment_vs_hold_usd"),
                        "sequence": ("cancel -> confirmed terminal -> "
                                     "reconciled fills -> recomputed "
                                     "capacity -> replacement")}))
        if not reasons and cur["filled_qty"] <= 0 and params.get(
                "resize_on_primary_growth") and qty_cap > float(
                plan["quantity"]) + 1e-9:
            reasons.append(("RESIZE_REQUESTED", {
                "planned_qty": float(plan["quantity"]),
                "capacity_now": qty_cap}))
        del remaining
    return reasons


async def _exit_gate(conn, *, out, gs, dec, facts, pos, base, at, source,
                     adapter, params):
    """AN EXIT OR REDUCE OF THE GROUP, WITH A STANDING HEDGE IN PLAY."""
    sel = dec.get("selected") or {}
    # ONLY A STANDING ORDER is governed here. A non-standing hedge order in
    # flight (an ordinary acquisition's lost answer) is gated by the group
    # gate the pass already applies, and says so under its own name.
    standing_live = [o for o in gs["orders"]
                     if o["standing"] and o["potentially_live"]]
    fc = sum(o["fill_capable_qty"] for o in standing_live) + sum(
        c["quantity"] for c in gs["claimed_unbound"])
    if not gs["plans"]:
        return dict(out, ok=True, block_dispatch=False,
                    refusal="NO_STANDING_ORDER_IN_THIS_GROUP")
    if fc > 1e-9 or standing_live or gs["claimed_unbound"]:
        cur = standing_live[0] if standing_live else None
        await record_event(
            conn, kind="EXIT_WAITS_FOR_HEDGE_TERMINAL", source=source,
            key="spe:exitwait:%s:%s" % (base["group_id"], int(at // 60)),
            hedge_intent_id=(cur or {}).get("intent_id"),
            plan_id=(cur or {}).get("plan_id"),
            primary_qty=gs["confirmed_primary_qty"],
            filled_qty=gs["hedge_held_qty"], fill_capable_qty=fc,
            lifecycle_state=gs["lifecycle_state"],
            evidence={"decision": dec.get("action"),
                      "unresolved_exposure": {
                          "hedge_fill_capable_qty": fc,
                          "why": ("the live hedge order can still fill; the "
                                  "exit is decided again on what is held "
                                  "once its terminal state is confirmed and "
                                  "its final fills reconciled")},
                      "sequence": "cancel -> terminal -> reconcile -> exit"},
            at=at, **base)
        if cur is not None and cur.get("venue_order_id"):
            out["cancel"] = await request_cancel(
                conn, order=cur, gs=gs, reason="EXIT_REQUIRES_TERMINAL_HEDGE",
                source=source, at=at, adapter=adapter, policy_params=params)
        return dict(out, ok=True, block_dispatch=True,
                    refusal=R_EXIT_WAITS_FOR_HEDGE_TERMINAL)
    # NO FILL-CAPABLE HEDGE: a PRIMARY exit may not leave hedge > primary.
    target = str(sel.get("intent_id") or pos.get("intent_id"))
    role = str(sel.get("leg_role") or "PRIMARY")
    prim_id = (gs.get("primary") or {}).get("intent_id")
    qty = sel.get("qty", sel.get("quantity"))
    try:
        qty = float(qty)
    except (TypeError, ValueError):
        qty = None
    if gs["hedge_held_qty"] > 0 and role == "PRIMARY" and \
            (target == prim_id or target == "None") and qty is not None:
        after = gs["confirmed_primary_qty"] - qty
        if after + 1e-9 < gs["hedge_held_qty"]:
            excess = round(gs["hedge_held_qty"] - max(after, 0.0), 6)
            hedge = next((o for o in gs["orders"] if o["residual_qty"] > 0),
                         None) or {}
            per = (hedge.get("cost_usd") or 0.0) / max(
                hedge.get("filled_qty") or 1.0, 1.0)
            residual = {
                "hedge_excess_qty_after_the_exit": excess,
                "primary_after_qty": round(after, 6),
                "hedge_held_qty": gs["hedge_held_qty"],
                "excess_cost_basis_usd": round(excess * per, 6),
                "excess_worst_case_usd": round(-excess * per, 6),
                "decision_alternative": {k: sel.get(k) for k in (
                    "action", "qty", "value_usd", "unpaired_residual_qty",
                    "unpaired_value_at_risk_usd")},
                "why": ("selling the primary below the hedge held would leave "
                        "an unprotected hedge excess; the group may exit the "
                        "hedge leg (or both) instead, and is decided again")}
            await record_event(
                conn, kind="EXIT_REFUSED_HEDGE_WOULD_EXCEED_PRIMARY",
                source=source,
                key="spe:exitrefuse:%s:%s:%s" % (base["group_id"], qty,
                                                 int(at // 60)),
                primary_qty=gs["confirmed_primary_qty"],
                filled_qty=gs["hedge_held_qty"], fill_capable_qty=0.0,
                evidence=residual, at=at, **base)
            return dict(out, ok=True, block_dispatch=True,
                        refusal=R_EXIT_WOULD_LEAVE_HEDGE_ABOVE_PRIMARY,
                        residual=residual)
    return dict(out, ok=True, block_dispatch=False)


async def _record_coverage(conn, *, gs, facts, admitted_all, base, at,
                           source) -> None:
    """THE PAYOUT TABLE OF THE ACTUAL COMBINED INVENTORY (primary held, hedge
    filled at its actual cost), recorded once per filled quantity."""
    hedge = next((o for o in gs["orders"] if o["residual_qty"] > 0), None)
    if hedge is None:
        return
    key = "spe:cov:%s:%s:%s" % (hedge["intent_id"], hedge["residual_qty"],
                                gs["confirmed_primary_qty"])
    held_leg = _held_leg(facts)
    hedge_leg, _adm = _candidate_leg(admitted_all, hedge["candidate_id"])
    table = None
    if held_leg is not None and hedge_leg is not None:
        prim = gs.get("primary") or {}
        avg = (hedge["cost_usd"] / hedge["filled_qty"]
               if hedge["filled_qty"] else None)
        cost = None if avg is None else round(
            math.floor(avg * 100 + 1e-9) / 100.0, 2)
        if cost and 0 < cost < 1:
            table = classify_floor(
                held_leg=held_leg, hedge_leg=hedge_leg,
                primary_qty=gs["confirmed_primary_qty"],
                primary_cost_usd=prim.get("cost_usd"),
                entry_fees_usd=prim.get("entry_fees_usd"),
                hedge_qty=hedge["residual_qty"], hedge_cost_price=cost,
                hedge_fee_usd_=hedge["fees_usd"],
                sport_permits_tie=facts.get("sport_permits_tie"),
                fixture_can_void=bool(facts.get("fixture_can_void", True)),
                fixture_can_postpone=bool(facts.get("fixture_can_postpone",
                                                    True)),
                whole_position=True)
    await record_event(
        conn, kind="COVERAGE_RECORDED", source=source, key=key,
        plan_id=hedge.get("plan_id"), hedge_intent_id=hedge["intent_id"],
        venue_order_id=hedge.get("venue_order_id"),
        lifecycle_state=gs["lifecycle_state"],
        primary_qty=gs["confirmed_primary_qty"],
        filled_qty=hedge["filled_qty"],
        fill_capable_qty=hedge["fill_capable_qty"], at=at,
        evidence={"selected_instrument": hedge["candidate_id"],
                  "covered_qty": gs["covered_qty"],
                  "uncovered_qty": gs["uncovered_qty"],
                  "remaining_fillable_on_the_live_order":
                      hedge["fill_capable_qty"],
                  "payout_table_actual_inventory": table,
                  "resting_is_not_protection": True}, **base)


# ═════════════════════════════════════════════════════════════════════
# PLACEMENT, THROUGH THE ONE BOUND-PLAN DISPATCH
# ═════════════════════════════════════════════════════════════════════

def standing_admission_record(*, plan, pick: dict, pos: dict, facts: dict,
                              admitted_all, decision_id, xavier_decision_id,
                              good_till_iso: str, spo_policy: dict) -> dict:
    """THE SUBMISSION RECORD FOR ONE STANDING ORDER, in the shape
    `bettor_funded_execution.plan_from_decision` reads -- and it must
    reproduce the plan through that reader. Pure."""
    from . import bettor_xavier as XV
    passed, failed = [], []

    def _check(name, ok, why):
        (passed if ok else failed).append(name if ok else {
            "check": name, "why": why})
    fl = pick.get("floor") or {}
    _check("FLOOR_CLASS_MEETS_THE_POLICY",
           _FLOOR_RANK.get(fl.get("floor_class"), 0) >= _FLOOR_RANK[
               spo_policy.get("minimum_floor_class", F_ORDINARY)],
           "floor class %r" % fl.get("floor_class"))
    _check("PREFERRED_TO_HOLD_BY_THE_POLICY", bool(pick.get("desirable")),
           "the policy prefers HOLD")
    _check("FEE_PRICED", (pick.get("fee") or {}).get("fee_usd") is not None,
           "the fee is not priced")
    _check("EVENT_KEY_STATED", bool(pos.get("event_key")),
           "the held position names no event")
    _check("DOES_NOT_NET_THE_HELD_INSTRUMENT",
           str(pos.get("us_market_slug")) != str(plan.venue_slug),
           "the plan addresses the held instrument")
    if failed:
        return {"ok": False, "refusal": R_PLAN_REFUSED, "failed": failed,
                "passed": passed}
    sup = XV.hedge_record_supplied(facts, admitted_all, plan.candidate_id) \
        or {}
    record = {
        "admissible": True, "refusals": [],
        "admitted_because": list(passed),
        "admitted_by": "bettor_xavier_standing_orders.standing_admission_"
                       "record",
        "us_market_slug": plan.venue_slug,
        "event_key": str(pos.get("event_key")),
        "order_intent": plan.side,
        "payout_event": sup.get("payout_event"),
        "settlement_identity": sup.get("settlement_identity"),
        "execution_plan": {"execution": {
            "size": plan.quantity, "vwap": plan.limit_price,
            "limit_price": plan.limit_price,
            "sized_from": "the standing AcquisitionPlan (%s)" % plan.digest}},
        "collateral_usd": plan.collateral_usd,
        "account_id": plan.account_id, "venue": plan.venue,
        "group_id": plan.group_id, "candidate_id": plan.candidate_id,
        "plan_digest": plan.digest, "decision_id": decision_id,
        "xavier_decision_id": xavier_decision_id,
        "fee_usd": float((pick.get("fee") or {}).get("fee_usd") or 0.0),
        "inputs_expire_at": plan.inputs_expire_at,
        "expected_net_usd": (pick.get("valuation") or {}).get(
            "ev_pair_if_filled_usd"),
        "standing_order": {
            "good_till": good_till_iso, "plan_id": plan.digest,
            "policy_version": POLICY_VERSION,
            "floor_class": fl.get("floor_class"),
            "mode": MODE_STRICT_FALLBACK,
            "exchange_linked_exclusivity": EXCHANGE_LINKED_EXCLUSIVITY}}
    got = FX.plan_from_decision(record)
    if not got.get("ok"):
        return {"ok": False, "refusal": R_PLAN_REFUSED,
                "reader_refusal": got.get("refusal"), "why": got.get("why")}
    drift = []
    for f, g, a in (("slug", "us_market_slug", "venue_slug"),
                    ("side", "intent", "side")):
        if str(got.get(g)) != str(getattr(plan, a)):
            drift.append(f)
    for f, g, a in (("qty", "quantity", "quantity"),
                    ("wire", "limit_price", "limit_price"),
                    ("collateral", "collateral_usd", "collateral_usd")):
        if abs(float(got.get(g) or 0) - float(getattr(plan, a) or 0)) > 1e-9:
            drift.append(f)
    if drift or got.get("tif") != FX.TIF_GOOD_TILL_DATE:
        return {"ok": False, "refusal": R_PLAN_REFUSED, "drift": drift,
                "tif": got.get("tif")}
    return {"ok": True, "record": record, "reproduces": got}


async def place(conn, *, out, xs, pos, facts, dec, xr, gs, pick,
                admitted_all, evaluated, params, pol, account_id, venue,
                adapter, venue_positions, at, source, base) -> dict:
    """ONE STANDING ORDER: Xavier's decision row -> the plan -> the capacity
    reservation (one per group, database-enforced) -> the dispatch claim ->
    the leg reservation and `submit_for_decision` (GOOD_TILL_DATE) -> the
    outcome, bound back to the plan. Nothing here retries a send."""
    from . import bettor_funded_pair_cycle as PC
    from . import bettor_xavier as XV
    live = second_live_hedge_permitted(
        live_or_potentially_live=gs["live_or_potentially_live_orders"])
    if not live["permitted"]:
        await record_event(conn, kind="SECOND_LIVE_ORDER_REFUSED",
                           source=source, evidence=live, at=at, **base)
        return dict(out, ok=True, refusal=live["refusal"])
    qty = int(math.floor(gs["confirmed_primary_qty"] - gs["hedge_held_qty"]
                         + 1e-9))
    gt = at + float(params.get("gtd_seconds") or 900.0)
    auth = await _authorization(conn, at)
    if not auth["ok"]:
        return dict(out, ok=True, refusal=R_AUTHORIZATION_LAPSED)
    if auth.get("expires_at"):
        gt = min(gt, float(auth["expires_at"]))
    for slug in (pick["venue_slug"], pos.get("us_market_slug")):
        ms = await _market_state(conn, slug) if slug else None
        if ms and ms.upper() in NOT_TRADING_MARKET_STATES:
            return dict(out, ok=True, refusal=R_MARKET_NOT_TRADING,
                        market_slug=slug, market_state=ms)
    gt = float(int(gt))
    gt_iso = _iso(gt)
    wire = float(pick["protective_price"]["wire_price"])
    try:
        plan = PC.AcquisitionPlan(
            account_id=account_id, venue=venue, group_id=pos[
                "portfolio_group_id"], candidate_id=pick["candidate_id"],
            action=ACTION, quantity=qty, limit_price=wire,
            collateral_usd=FX.collateral_for(wire, qty, pick["side"]),
            inputs_expire_at=gt, assessed_at=at,
            source="bettor_xavier_standing_orders.place",
            quantity_from=("the CONFIRMED primary quantity less the hedge "
                           "held (never the intended primary quantity)"),
            price_from="the protective price on the instrument's grid")
    except PC.PlanRefused as exc:
        return dict(out, ok=True, refusal=R_PLAN_REFUSED,
                    plan_refusal=exc.as_dict())
    fl = pick["floor"]
    fee_ident = fee_schedule_identity(at=at)
    resp = xs.responsibility_of(pos.get("intent_id")) or {}
    alternatives = [dict(_cand_brief(e), eligibility=(
        "SELECTED" if e["candidate_id"] == pick["candidate_id"] else
        "MONITORED_NEVER_SUBMITTED")) for e in evaluated]
    alternatives.append({"action": "HOLD", "value_usd": (
        pick.get("valuation") or {}).get("ev_hold_usd"),
        "eligibility": "THE_ALTERNATIVE_THE_POLICY_COMPARED"})
    rec = await XV.record_decision(
        conn, account_id=account_id, venue=venue,
        # A MILLISECOND AFTER THE GROUP REVIEW it was decided inside, so the
        # position's history orders the two records deterministically.
        intent_id=str(pos["intent_id"]), decided_at=at + 0.001, salt=ACTION,
        responsibility_state=resp.get("state") or XV.HELD,
        execution_eligibility=XV.E_DISPATCHED,
        alternatives=alternatives,
        reasoning={"standing_order_policy": {k: pol.get(k) for k in (
            "policy_key", "version", "source", "params")},
            "group_review_xavier_decision_id": xr.get("xavier_decision_id"),
            "group_review_action": dec.get("action"),
            "mode": MODE_STRICT_FALLBACK,
            "exchange_linked_exclusivity": EXCHANGE_LINKED_EXCLUSIVITY,
            "why_one_live_order": WHY_ONE_LIVE_ORDER,
            "latency_disclosure": LATENCY_DISCLOSURE,
            "process_death_disclosure": PROCESS_DEATH_DISCLOSURE,
            "valuation_vs_hold": pick.get("valuation"),
            "resting_order_is_an_obligation_not_protection": True},
        expected_economics={"ev_pair_if_filled_usd": (pick.get(
            "valuation") or {}).get("ev_pair_if_filled_usd"),
            "increment_vs_hold_usd": pick.get("increment_vs_hold_usd"),
            "floor_class": fl["floor_class"],
            "ordinary_margin_usd": fl.get("ordinary_margin_usd"),
            "worst_case_net_usd": fl.get("worst_case_net_usd")},
        residual_exposure={"confirmed_primary_qty": gs[
            "confirmed_primary_qty"], "hedge_held_qty": gs["hedge_held_qty"],
            "capacity_qty": qty, "uncovered_until_filled_qty": gs[
                "confirmed_primary_qty"] - gs["hedge_held_qty"]},
        evidence={"floor": fl, "fee_schedule_identity": fee_ident,
                  "settlement_identity": pick.get("settlement_identity"),
                  "grid": pick.get("grid"),
                  "venue_capability": VENUE_CAPABILITY},
        obligations=list(resp.get("obligations") or []),
        chosen_action=ACTION, chosen_plan_digest=plan.digest,
        decision_id=facts.get("decision_id"),
        portfolio_group_id=pos.get("portfolio_group_id"),
        us_market_slug=pos.get("us_market_slug"))
    out["xavier_standing_decision"] = rec
    if not rec.get("ok") or rec.get("already"):
        return dict(out, ok=True, refusal=rec.get("refusal") or
                    "THE_STANDING_DECISION_WAS_ALREADY_RECORDED")
    xid = rec["xavier_decision_id"]
    adm = standing_admission_record(
        plan=plan, pick=pick, pos=pos, facts=facts, admitted_all=admitted_all,
        decision_id=facts.get("decision_id"), xavier_decision_id=xid,
        good_till_iso=gt_iso, spo_policy=params)
    out["admission"] = {k: adm.get(k) for k in ("ok", "refusal", "failed",
                                                "drift", "reader_refusal")}
    if not adm.get("ok"):
        return dict(out, ok=True, refusal=adm.get("refusal"))
    capacity = {"confirmed_primary_qty": gs["confirmed_primary_qty"],
                "hedge_held_qty": gs["hedge_held_qty"],
                "fill_capable_before_qty": gs["fill_capable_qty"],
                "planned_qty": qty,
                "rule": "capacity = CONFIRMED primary - hedge held, never "
                        "the intended primary quantity"}
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO bettor_standing_order_plans (plan_id, "
                " account_id, venue, group_id, primary_intent_id, "
                " xavier_decision_id, group_review_xavier_decision_id, "
                " decision_id, policy_key, policy_version, policy, mode, "
                " exchange_linked_exclusivity, candidate_id, venue_slug, "
                " order_intent, wire_limit_price, cost_price, quantity, tif, "
                " good_till_time, capacity, floor_class, floor, "
                " fee_schedule_identity, settlement_identity, desirability) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,"
                " $14,$15,$16,$17,$18,$19,$20,to_timestamp($21),$22::jsonb,"
                " $23,$24::jsonb,$25,$26,$27::jsonb)",
                plan.digest, account_id, venue, str(pos[
                    "portfolio_group_id"]), str(pos["intent_id"]), xid,
                xr.get("xavier_decision_id"), facts.get("decision_id"),
                POLICY_KEY, str(pol.get("version")), json.dumps(params),
                MODE_STRICT_FALLBACK, EXCHANGE_LINKED_EXCLUSIVITY,
                plan.candidate_id, plan.venue_slug, plan.side, wire,
                float(pick["protective_price"]["cost_price"]), qty,
                FX.TIF_GOOD_TILL_DATE, gt, json.dumps(capacity),
                fl["floor_class"], json.dumps(fl, default=str),
                str(fee_ident.get("digest")),
                str((pick.get("settlement_identity") or {}).get("digest")),
                json.dumps(pick.get("valuation") or {}, default=str))
            await conn.execute(
                "INSERT INTO bettor_standing_capacity_reservations ("
                " reservation_id, account_id, venue, group_id, plan_id, "
                " primary_intent_id, reserved_qty, reserved_collateral_usd, "
                " state, opened_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,"
                " 'RESERVED', to_timestamp($9))",
                "scr:%s" % plan.digest, account_id, venue,
                str(pos["portfolio_group_id"]), plan.digest,
                str(pos["intent_id"]), float(qty), float(plan.collateral_usd),
                at)
    except Exception as exc:                                    # noqa: BLE001
        # THE DATABASE'S ONE-RESERVED-PER-GROUP INDEX (or a replayed plan):
        # nothing is sent.
        await record_event(
            conn, kind="SECOND_LIVE_ORDER_REFUSED", source=source,
            xavier_decision_id=xid,
            evidence={"refusal": R_LIVE_ORDER_EXISTS,
                      "database": "%s: %s" % (type(exc).__name__,
                                              str(exc)[:200])}, at=at,
            **base)
        await XV.claim_dispatch(conn, xavier_decision_id=xid,
                                plan_digest=plan.digest,
                                decision_id=facts.get("decision_id"),
                                at=time.time(), evidence={"action": ACTION})
        await xs.record_execution(conn, xavier_decision_id=xid, result={
            "sent": False, "refusal": R_LIVE_ORDER_EXISTS})
        return dict(out, ok=True, refusal=R_LIVE_ORDER_EXISTS)
    pb = dict(base, plan_id=plan.digest, xavier_decision_id=xid,
              primary_qty=gs["confirmed_primary_qty"])
    await record_event(conn, kind="PLAN_RECORDED", source="DISPATCHER",
                       key="spe:plan:%s" % plan.digest, at=at,
                       evidence={"plan": plan.as_dict(), "good_till": gt_iso,
                                 "floor_class": fl["floor_class"],
                                 "capacity": capacity,
                                 "monitored_candidates": [
                                     _cand_brief(e) for e in evaluated
                                     if e["candidate_id"]
                                     != pick["candidate_id"]],
                                 "latency_disclosure": LATENCY_DISCLOSURE},
                       **pb)
    await record_event(conn, kind="CAPACITY_RESERVED", source="DISPATCHER",
                       key="spe:res:%s" % plan.digest, at=at,
                       fill_capable_qty=float(qty),
                       evidence={"reserved_qty": qty, "collateral_usd":
                                 plan.collateral_usd,
                                 "released_only_on": "a confirmed terminal "
                                 "state (or never sent)"}, **pb)
    claim = await XV.claim_dispatch(
        conn, xavier_decision_id=xid, plan_digest=plan.digest,
        decision_id=facts.get("decision_id"), at=time.time(),
        evidence={"action": ACTION, "candidate_id": plan.candidate_id})
    out["dispatch_claim"] = claim
    if not claim.get("claimed"):
        await _release(conn, plan_id=plan.digest, reason="NEVER_SENT",
                       evidence={"claim": claim})
        await record_event(conn, kind="PLACEMENT_NOT_SENT",
                           source="DISPATCHER", at=at,
                           evidence={"claim": claim}, **pb)
        return dict(out, ok=True, refusal=XV.R_DISPATCH_NOT_CLAIMED)
    await record_event(conn, kind="PLACEMENT_CLAIMED", source="DISPATCHER",
                       key="spe:claim:%s" % plan.digest, at=at,
                       fill_capable_qty=float(qty), **pb)
    # THE QUANTITIES, AGAIN, IMMEDIATELY BEFORE THE SEND
    now_gs = await group_state(conn, group_id=str(pos["portfolio_group_id"]),
                               primary_intent_id=str(pos["intent_id"]),
                               at=at)
    if abs(now_gs["confirmed_primary_qty"] - gs["confirmed_primary_qty"]) \
            > 1e-9 or abs(now_gs["hedge_held_qty"] - gs["hedge_held_qty"]) \
            > 1e-9:
        await xs.record_execution(conn, xavier_decision_id=xid, result={
            "sent": False, "refusal": R_CHANGED_BEFORE_SEND})
        await _release(conn, plan_id=plan.digest, reason="NEVER_SENT",
                       evidence={"refusal": R_CHANGED_BEFORE_SEND})
        await record_event(conn, kind="PLACEMENT_NOT_SENT",
                           source="DISPATCHER", at=at,
                           evidence={"refusal": R_CHANGED_BEFORE_SEND},
                           **pb)
        return dict(out, ok=True, refusal=R_CHANGED_BEFORE_SEND)
    got = await PC.acquire_second_leg(
        conn, operation_id="spo:%s" % plan.digest,
        group_id=str(pos["portfolio_group_id"]),
        plan=plan, expect_candidate_id=plan.candidate_id,
        expect_digest=plan.digest,
        decision_record=adm["record"], account_id=account_id, venue=venue,
        adapter=adapter, venue_positions=venue_positions, now=at)
    out["acquisition"] = {k: got.get(k) for k in (
        "ok", "refusal", "submitted", "intent_id", "exposure",
        "order_binding")}
    await xs.record_execution(conn, xavier_decision_id=xid,
                              result=XV.acquisition_result(got))
    iid = got.get("intent_id")
    if iid:
        await _bind(conn, plan_id=plan.digest, intent_id=iid)
    after = await group_state(conn, group_id=str(pos["portfolio_group_id"]),
                              primary_intent_id=str(pos["intent_id"]),
                              at=at)
    order = next((o for o in after["orders"] if o["intent_id"] == iid),
                 None) or {}
    ob = dict(pb, hedge_intent_id=iid,
              venue_order_id=order.get("venue_order_id"),
              filled_qty=order.get("filled_qty"),
              fill_capable_qty=order.get("fill_capable_qty"))
    if not got.get("submitted"):
        await _release(conn, plan_id=plan.digest, reason="NEVER_SENT",
                       evidence={"refusal": got.get("refusal")})
        await record_event(conn, kind="PLACEMENT_NOT_SENT",
                           source="DISPATCHER", at=at,
                           key="spe:notsent:%s" % plan.digest,
                           evidence={"refusal": got.get("refusal"),
                                     "why": got.get("why")}, **ob)
        return dict(out, ok=True, refusal=got.get("refusal"))
    if got.get("refusal") == FX.R_LOST_ACKNOWLEDGEMENT:
        await record_event(conn, kind="SENT_OUTCOME_UNKNOWN",
                           source="DISPATCHER", at=at,
                           key="spe:unknown:%s" % plan.digest,
                           lifecycle_state=L_AMBIGUOUS,
                           fill_capable_qty=float(qty),
                           evidence={"why": ("the answer was lost: the order "
                                             "is POTENTIALLY LIVE for its "
                                             "whole quantity until a venue "
                                             "read establishes otherwise; "
                                             "nothing is resent")}, **{
                               k: v for k, v in ob.items()
                               if k != "fill_capable_qty"})
        return dict(out, ok=True, placed=True, refusal=got.get("refusal"),
                    intent_id=iid, lifecycle_state=L_AMBIGUOUS)
    kind = ("REFUSED_BY_THE_VENUE" if order.get("book_state") == "REJECTED"
            else "SENT_FILLED_ON_ARRIVAL" if order.get("filled_qty")
            else "SENT_ACKNOWLEDGED_RESTING")
    await record_event(conn, kind=kind, source="DISPATCHER", at=at,
                       key="spe:sent:%s" % plan.digest,
                       lifecycle_state=order.get("lifecycle_state"),
                       evidence={"venue_status": (got.get("submission") or {})
                                 .get("refusal"), "good_till": gt_iso,
                                 "tif": FX.TIF_GOOD_TILL_DATE}, **ob)
    if kind == "REFUSED_BY_THE_VENUE":
        await _release(conn, plan_id=plan.digest,
                       reason="TERMINAL_CONFIRMED_BY_THE_VENUE",
                       evidence={"book_state": "REJECTED"})
    return dict(out, ok=True, placed=True, refusal=None, intent_id=iid,
                plan_id=plan.digest, lifecycle_state=order.get(
                    "lifecycle_state"), xavier_decision_id=xid)


# ═════════════════════════════════════════════════════════════════════
# EVENTS FROM THE VENUE: A TRIGGER INTO THE SAME GROUP AUTHORITY
# ═════════════════════════════════════════════════════════════════════

def _order_updates(message: dict) -> list:
    """(order, execution-or-None) pairs from a private websocket message:
    `orderSubscriptionUpdate.execution` / `orderUpdate`, or the orders of
    `orderSubscriptionSnapshot` / `ordersSnapshot` (the SDK's shapes)."""
    m = dict(message or {})
    out = []
    for key in ("orderSubscriptionUpdate", "orderUpdate"):
        u = m.get(key)
        if isinstance(u, dict):
            ex = u.get("execution") if isinstance(u.get("execution"),
                                                  dict) else None
            order = (ex or {}).get("order") or u.get("order") or {}
            out.append((dict(order), ex))
    for key in ("orderSubscriptionSnapshot", "ordersSnapshot"):
        s = m.get(key)
        if isinstance(s, dict):
            for o in s.get("orders") or []:
                if isinstance(o, dict):
                    out.append((dict(o), None))
    return out


async def ingest_private_order_message(conn, message: dict, *,
                                       now: float | None = None) -> dict:
    """AN AUTHENTICATED ORDER UPDATE OR SNAPSHOT, RECORDED AND -- WHEN IT
    CARRIES AN EXECUTION -- INGESTED INTO THE ONE BOOK.

    The execution goes through `bettor_funded_book.executions_of` (the one
    reader) and `ingest_fills` (idempotent on the venue's own fill id), so a
    duplicate report -- the same execution on the websocket twice, or on the
    websocket and again on the reconciliation read -- is one fill. An order
    state in the message is EVIDENCE for the record only: the ending is
    applied by the reconciliation read (`record_venue_terminal`), which also
    checks the quantities balance. An order no intent owns is recorded as
    unmatched and never adopted. Never raises."""
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"relevant": False, "matched": [], "unmatched": [],
                           "ingested": []}
    if not await has_schema(conn):
        return dict(out, refusal=R_SCHEMA)
    for order, ex in _order_updates(message):
        vid = str(order.get("id") or "")
        if not vid:
            continue
        row = await conn.fetchrow(
            "SELECT intent_id, kind, account_id, venue, portfolio_group_id, "
            "       leg_role, decision_ref FROM bettor_funded_intents "
            " WHERE venue_order_id=$1", vid)
        state = str(order.get("state") or "")
        cum = order.get("cumQuantity")
        leaves = order.get("leavesQuantity")
        ev = {"venue_order_id": vid, "order_state": state,
              "cum_quantity": cum, "leaves_quantity": leaves,
              "execution_id": (ex or {}).get("id"),
              "execution_type": (ex or {}).get("type"),
              "last_shares": (ex or {}).get("lastShares"),
              "last_px": (ex or {}).get("lastPx"),
              "a_state_here_is_evidence_the_reconciliation_read_applies_it":
                  True}
        key = "spe:voe:%s:%s:%s:%s" % (vid, (ex or {}).get("id") or "-",
                                       state, cum)
        if row is None:
            out["unmatched"].append(vid)
            continue
        gid = row["portfolio_group_id"] or ("intent:%s" % row["intent_id"])
        out["relevant"] = True
        await record_event(
            conn, kind="VENUE_ORDER_EVENT", source="VENUE_ORDER_EVENT",
            key=key, account_id=row["account_id"], venue=row["venue"],
            group_id=gid, hedge_intent_id=(row["intent_id"] if str(
                row["leg_role"] or "") == "HEDGE" else None),
            venue_order_id=vid, evidence=ev, at=at)
        out["matched"].append({"venue_order_id": vid,
                               "intent_id": row["intent_id"],
                               "group_id": gid})
        if ex is not None:
            read = FB.executions_of([ex])
            if read["executions"]:
                ing = await FB.ingest_fills(
                    conn, row["intent_id"], read["executions"], at=at,
                    direction=("EXIT" if str(row["kind"]) == "EXIT"
                               else "ENTRY"))
                out["ingested"].append({
                    "intent_id": row["intent_id"],
                    "written": len(ing.get("written") or []),
                    "already_held": len(ing.get("already_held") or [])})
    return out


def _levels(side):
    out = []
    for lv in side or []:
        try:
            px = float(((lv or {}).get("px") or {}).get("value"))
        except (TypeError, ValueError):
            continue
        out.append(px)
    return out


async def note_market_message(conn, message: dict, *,
                              now: float | None = None) -> dict:
    """A MARKET UPDATE (book, trade, market state) ON A WATCHED SLUG.

    TOUCHING THE PRICE IS NOT A FILL: a book level or a trade print at our
    limit is recorded as `PRICE_TOUCH_IS_NOT_A_FILL` and NOTHING is
    ingested -- only the venue's own executions on our order count. The
    message is a trigger to re-evaluate the standing order under the group
    authority, and a market state (suspended, closed, settled) is recorded
    for `maintain` to act on. Never raises."""
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"relevant": False}
    if not await has_schema(conn):
        return dict(out, refusal=R_SCHEMA)
    m = dict(message or {})
    md = m.get("marketData") or m.get("marketDataLite") or {}
    tr = m.get("trade") or {}
    slug = md.get("marketSlug") or tr.get("marketSlug")
    if not slug:
        return out
    live = [dict(r) for r in await conn.fetch(
        "SELECT i.intent_id, i.account_id, i.venue, i.portfolio_group_id, "
        "       i.order_intent, i.limit_price::float8 AS limit_price "
        "  FROM bettor_funded_intents i "
        " WHERE i.us_market_slug=$1 AND i.kind='ENTRY' "
        "   AND i.leg_role='HEDGE' AND i.decision_ref ? 'standing_order' "
        "   AND i.decision_ref->'standing_order' <> 'null'::jsonb "
        "   AND bettor_funded_order_is_outstanding(i.state)", str(slug))]
    watched_groups = [dict(r) for r in await conn.fetch(
        "SELECT DISTINCT p.group_id, p.account_id, p.venue "
        "  FROM bettor_standing_order_plans p "
        "  JOIN bettor_funded_intents i ON i.intent_id=p.primary_intent_id "
        " WHERE (p.venue_slug=$1 OR i.us_market_slug=$1) "
        "   AND i.closed_at IS NULL", str(slug))]
    if not live and not watched_groups:
        return out
    out["relevant"] = True
    prices = _levels(md.get("bids")) + _levels(md.get("offers"))
    for k in ("bestBid", "bestAsk", "lastTradePx"):
        try:
            prices.append(float((md.get(k) or {}).get("value")))
        except (TypeError, ValueError):
            pass
    try:
        prices.append(float((tr.get("price") or {}).get("value")))
    except (TypeError, ValueError):
        pass
    state = md.get("state")
    groups = {(g["group_id"], g["account_id"], g["venue"])
              for g in watched_groups}
    for r in live:
        groups.add((r["portfolio_group_id"], r["account_id"], r["venue"]))
    for gid, acct, ven in groups:
        await record_event(
            conn, kind="VENUE_MARKET_EVENT", source="VENUE_MARKET_EVENT",
            account_id=acct, venue=ven, group_id=gid, at=at,
            evidence={"market_slug": slug, "market_state": state,
                      "prices_seen": prices[:20],
                      "is_a_fill": False})
    for r in live:
        if any(abs(p - float(r["limit_price"])) < 1e-9 for p in prices):
            await record_event(
                conn, kind="PRICE_TOUCH_IS_NOT_A_FILL",
                source="VENUE_MARKET_EVENT", account_id=r["account_id"],
                venue=r["venue"], group_id=r["portfolio_group_id"],
                hedge_intent_id=r["intent_id"], at=at,
                evidence={"market_slug": slug,
                          "our_limit": r["limit_price"],
                          "rule": ("a book level or trade print at our limit "
                                   "is not an execution of our order; only "
                                   "the venue's own executions are "
                                   "ingested")})
    out["market_state"] = state
    return out


def describe() -> dict:
    return {"version": VERSION, "policy": code_default(),
            "mode": MODE_STRICT_FALLBACK,
            "venue_capability": VENUE_CAPABILITY,
            "why_one_live_order": WHY_ONE_LIVE_ORDER,
            "latency_disclosure": LATENCY_DISCLOSURE,
            "process_death_disclosure": PROCESS_DEATH_DISCLOSURE,
            "floor_classes": list(FLOOR_CLASSES),
            "is_a_second_execution_path": False,
            "is_a_second_book": False, "is_a_second_selector": False}
