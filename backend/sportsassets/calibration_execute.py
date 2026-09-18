"""The calibration lifecycle at the venue: submit, cancel, reconcile.

BUILT AND TESTED OFFLINE, WIRED TO NOTHING. No route reaches this module
in the monitoring release, and `calibration_execute` appears in no
handler. It exists so that when a ticket is approved the path already
has a test suite behind it, instead of being written in a hurry with
real money on the wire.

THE VENUE IS INJECTED. Every call goes through a small `venue` object
with four methods -- `open_order_ids`, `submit`, `status`, `cancel` --
which `live_venue()` fills from the existing verified adapter
(`pmus.submit_fok`, `pmus.order_status`, `pmus.cancel_order`). The tests
pass a mock with the same four methods, so the sequencing, the ambiguous
responses and the reconciliation are exercised without a socket.

THE FOUR RULES THAT SHAPE EVERY PATH HERE.

1.  THE RECORD IS WRITTEN BEFORE THE SEND. An order we cannot name is an
    order we cannot reconcile. The lifecycle row and its reserve exist
    first (calibration_store.reserve), and the ids already open on the
    market are captured immediately before the send, so a lost response
    is matched only against orders that were NOT already there. That is
    the mirror's own `pre_ids` discipline, which exists because of book
    863: a placement whose response was lost, which the venue filled.

2.  A LOST RESPONSE IS NOT A REFUSAL. If the send raises or returns
    something unreadable, this module does NOT conclude that nothing was
    placed. It returns AMBIGUOUS, leaves the reserve held, and hands
    back the pre-image so a later read can decide. Nothing retries.

3.  A CANCEL ACKNOWLEDGEMENT IS NOT A TERMINAL STATE. The venue may have
    filled the order before it saw the cancel -- book 1333, and the
    18:05Z cancel storm where ten orders were re-cancelled nine times
    each while the status read never went terminal. `cancel()` sends the
    cancel and then READS THE STATUS; only the venue's own terminal
    state ends the lifecycle.

4.  A LIMIT EXIT IS NOT GUARANTEED TO EXECUTE. `plan_exit` returns an
    intention, and the inventory plan declared on the ticket is what
    happens if it does not fill. Nothing here assumes a fill.

NO BLIND RETRIES anywhere. Every failure returns a named outcome for an
operator to act on.
"""
from __future__ import annotations

import math

# ── outcomes, all named ──────────────────────────────────────────────
SUBMITTED = "SUBMITTED"
REFUSED_BY_VENUE = "REFUSED_BY_VENUE"
AMBIGUOUS = "AMBIGUOUS_RESPONSE"
# A ticket the adapter cannot map is refused HERE, before the send. It is
# not an ambiguous send: nothing reached the network, so `sent` is False
# and no reconciliation is owed. Without this the ValueError would have
# been caught by the send's own handler and reported as AMBIGUOUS with
# sent=True -- an unresolved lifecycle for an order that never existed.
NOT_SENT = "NOT_SENT_TICKET_UNMAPPABLE"
FILLED = "FILLED"
RESTING = "RESTING"
PARTIAL = "PARTIAL"
CANCEL_SENT = "CANCEL_SENT"
TERMINAL = "TERMINAL"
NOT_TERMINAL = "NOT_TERMINAL"
UNREADABLE = "STATUS_UNREADABLE"

TERMINAL_VENUE_STATES = ("FILLED", "CANCELLED", "EXPIRED", "REJECTED")

A_LOST_RESPONSE_IS_NOT_A_REFUSAL = (
    "a send whose response we could not read tells us nothing about "
    "whether the venue accepted it; the reserve stays held and an "
    "operator reconciles against the pre-image")

A_CANCEL_IS_NOT_TERMINAL = (
    "the venue may have filled the order before it saw the cancel; only "
    "the venue's own terminal state ends the lifecycle")

AN_EXIT_IS_NOT_GUARANTEED = (
    "a limit exit may never fill; the ticket's declared inventory plan "
    "is what happens then, and it is declared before the entry")


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# Defined in the read-only module and re-exported here, where every
# existing caller and test already looks for it.
from .calibration_read import VenueUnreadable                  # noqa: E402,F401


def submit(venue, ticket: dict) -> dict:
    """Place the entry. Returns a named outcome; never raises on a lost
    response, and never retries.

    The pre-image is taken FIRST and returned in every outcome, so an
    ambiguous send can be reconciled later against orders that were not
    already on the market.
    """
    from datetime import datetime, timezone

    from .calibration_adapter import order_params

    market = ticket["marketId"]

    # MAP BEFORE READING, LET ALONE SENDING. order_params is pure and
    # refuses an order type, operation or outcome side it cannot state
    # exactly -- including a ticket that does not name which side of a
    # shared-identifier market it is for.
    try:
        params = order_params(ticket)
    except (ValueError, KeyError, TypeError) as exc:
        return {"outcome": NOT_SENT, "sent": False,
                "reason": "UNMAPPABLE_TICKET: %s" % exc,
                "preOpenOrderIds": None, "venueOrderId": None}

    try:
        pre = sorted(set(str(x) for x in (venue.open_order_ids(market) or ())))
        pre_readable = True
    except Exception as exc:                                   # noqa: BLE001
        # WITHOUT THE PRE-IMAGE, AN AMBIGUOUS SEND IS UNRECONCILABLE.
        # So we do not send at all: refusing costs nothing, and sending
        # blind costs the ability to tell our order from someone else's.
        return {"outcome": REFUSED_BY_VENUE,
                "reason": "PRE_IMAGE_UNREADABLE: %s" % type(exc).__name__,
                "sent": False, "preOpenOrderIds": None,
                "preImageReadable": False}
    del pre_readable

    # THE SEND WINDOW. Attribution by read-back compares the candidate
    # order's creation time against this interval; an order created
    # outside it is not this send's, however well the rest lines up. It
    # is stamped here, around the call, rather than inferred later.
    sent_after = datetime.now(timezone.utc).isoformat()
    window = {"sentAfter": sent_after, "readBefore": None}

    try:
        resp = venue.submit(
            market_id=market, price=ticket["price"],
            quantity=ticket["quantity"], side=ticket.get("side", "BUY"),
            outcome_side=params["outcomeSide"],
            order_type=ticket["orderType"],
            client_order_id=ticket["clientOrderId"])
    except Exception as exc:                                   # noqa: BLE001
        window["readBefore"] = datetime.now(timezone.utc).isoformat()
        return {"outcome": AMBIGUOUS, "sent": True,
                "reason": "SEND_RAISED: %s" % type(exc).__name__,
                "preOpenOrderIds": pre, "venueOrderId": None,
                "sendWindow": window, "nativeIntent": params[
                    "nativeIntentExpected"],
                "note": A_LOST_RESPONSE_IS_NOT_A_REFUSAL}
    window["readBefore"] = datetime.now(timezone.utc).isoformat()

    if not isinstance(resp, dict):
        return {"outcome": AMBIGUOUS, "sent": True,
                "reason": "RESPONSE_NOT_A_MAPPING",
                "preOpenOrderIds": pre, "venueOrderId": None,
                "sendWindow": window,
                "nativeIntent": params["nativeIntentExpected"],
                "note": A_LOST_RESPONSE_IS_NOT_A_REFUSAL}

    if resp.get("ok") is False and resp.get("status") in (
            "post_only_rejected", "rejected"):
        # A NAMED REFUSAL IS DIFFERENT FROM SILENCE: the venue told us it
        # did not accept the order, so nothing rests and nothing filled.
        return {"outcome": REFUSED_BY_VENUE, "sent": True,
                "reason": str(resp.get("status")),
                "preOpenOrderIds": pre, "venueOrderId": None,
                "sendWindow": window,
                "nativeIntent": params["nativeIntentExpected"]}

    oid = resp.get("order_id")
    if not oid:
        return {"outcome": AMBIGUOUS, "sent": True,
                "reason": "NO_ORDER_ID_IN_RESPONSE",
                "preOpenOrderIds": pre, "venueOrderId": None,
                "sendWindow": window,
                "nativeIntent": params["nativeIntentExpected"],
                "note": A_LOST_RESPONSE_IS_NOT_A_REFUSAL}

    filled = _f(resp.get("filled_shares")) or 0.0
    px = _f(resp.get("fill_price"))
    return {"outcome": SUBMITTED, "sent": True,
            "venueOrderId": str(oid), "preOpenOrderIds": pre,
            "sendWindow": window,
            "nativeIntent": params["nativeIntentExpected"],
            "filled": filled, "fillPrice": px,
            "cashOut": None if px is None else round(filled * px, 2)}


def reconcile(venue, venue_order_id: str) -> dict:
    """Read the venue's own state for one order.

    An unreadable status is UNREADABLE, never 'nothing happened'. Only a
    state in TERMINAL_VENUE_STATES is terminal, and the filled quantity
    is read from the same response so the two can never disagree by
    being read at different moments.
    """
    try:
        st = venue.status(venue_order_id)
    except Exception as exc:                                   # noqa: BLE001
        return {"outcome": UNREADABLE,
                "reason": type(exc).__name__, "terminal": False}
    if not isinstance(st, dict):
        return {"outcome": UNREADABLE, "reason": "STATUS_NOT_A_MAPPING",
                "terminal": False}

    state = str(st.get("status") or st.get("state") or "").upper()
    filled = _f(st.get("filled_shares"))
    qty = _f(st.get("quantity"))
    px = _f(st.get("fill_price"))
    if not state:
        return {"outcome": UNREADABLE, "reason": "NO_STATE_IN_RESPONSE",
                "terminal": False}

    terminal = state in TERMINAL_VENUE_STATES
    if terminal:
        outcome = TERMINAL
    elif filled and qty and filled < qty:
        outcome = PARTIAL
    elif filled:
        outcome = PARTIAL
    else:
        outcome = RESTING
    # FEES TRAVEL WITH THE FILL THEY BELONG TO. Reading them in a second
    # call would let the quantity and the fee come from two different
    # moments, and the cumulative spend has to be one coherent figure.
    # Absent means absent -- `settle` withholds rather than assuming 0.
    fees = _f(st.get("fees"))
    if fees is None:
        fees = _f(st.get("commission"))
    out = {"outcome": outcome, "terminal": terminal,
           "venueState": state, "filled": filled, "quantity": qty,
           "fillPrice": px}
    if fees is not None:
        out["fees"] = fees
    elif filled == 0:
        # No fill, so no fee to report: an explicit zero, not an
        # assumption about an unread one.
        out["fees"] = 0.0
    return out


def cancel(venue, venue_order_id: str, market_id: str) -> dict:
    """Send a cancel, then READ THE STATUS. The ack alone decides nothing.

    Returns both the ack and the status read. `terminal` is true only if
    the venue's own state says so -- a successful cancel call with a
    status that still reads OPEN leaves the lifecycle open, and the
    reserve with it.
    """
    ack, ack_error = None, None
    try:
        ack = venue.cancel(venue_order_id, market_id)
    except Exception as exc:                                   # noqa: BLE001
        ack_error = type(exc).__name__

    st = reconcile(venue, venue_order_id)
    return {"outcome": CANCEL_SENT if ack_error is None else AMBIGUOUS,
            "ack": ack, "ackError": ack_error,
            "status": st,
            "terminal": bool(st.get("terminal")),
            "note": A_CANCEL_IS_NOT_TERMINAL}


def plan_exit(ticket: dict, position_qty: float, limit_price: float) -> dict:
    """The exit INTENTION, with its declared fallback.

    It is a plan, not a promise: `assumesFill` is False and the ticket's
    own inventory plan is carried alongside, so the answer to "what if
    the limit never fills?" is on the record before the entry is sent.
    """
    return {
        "side": "SELL",
        "quantity": position_qty,
        "limitPrice": limit_price,
        "orderType": ticket.get("exitOrderType", "LIMIT_GTC"),
        "assumesFill": False,
        "ifUnfilled": ticket.get("inventoryPlan"),
        "note": AN_EXIT_IS_NOT_GUARANTEED,
    }


# ── settlement ───────────────────────────────────────────────────────
EVIDENCE_COMPLETE = "FILL_EVIDENCE_COMPLETE"
EVIDENCE_MISSING = "FILL_EVIDENCE_MISSING"
EVIDENCE_MALFORMED = "FILL_EVIDENCE_MALFORMED"
EVIDENCE_INCONSISTENT = "FILL_EVIDENCE_INCONSISTENT"
FEES_UNKNOWN = "FEES_NOT_REPORTED"

A_TERMINAL_ENTRY_IS_NOT_A_RECONCILED_LIFECYCLE = (
    "a filled entry leaves INVENTORY. The lifecycle is reconciled when the "
    "position is gone and every fill is booked -- not when the entry order "
    "reaches a terminal state, which is the moment the obligation begins")

MISSING_IS_NOT_ZERO = (
    "missing, malformed or inconsistent fill evidence withholds the spend "
    "and the release; it never settles as $0.00 spent")


def fill_evidence(status: dict, quantity=None) -> dict:
    """Judge the venue's fill evidence BEFORE any arithmetic touches it.

    THE DEFECT THIS EXISTS TO PREVENT. The first cut read
    `filled = _f(status.get("filled")) or 0.0`, so an UNKNOWN fill became
    a fill of zero, `0.0 * 0.0` became a spend of $0.00, and a terminal
    order with no readable fills released its reserve as though nothing
    had happened. That is the same "unavailable rendered as zero" failure
    this whole codebase keeps being bitten by, written by me into the one
    module that books money.
    """
    filled = _f(status.get("filled"))
    px = _f(status.get("fillPrice"))
    fees = _f(status.get("fees"))
    qty = _f(quantity if quantity is not None else status.get("quantity"))
    state = str(status.get("venueState") or "").upper()

    if "filled" not in status or filled is None:
        return {"ok": False, "reason": EVIDENCE_MISSING,
                "detail": "filled quantity not reported"}
    if filled < 0:
        return {"ok": False, "reason": EVIDENCE_MALFORMED,
                "detail": "negative filled quantity"}
    if qty is not None and filled > qty:
        return {"ok": False, "reason": EVIDENCE_INCONSISTENT,
                "detail": "filled %s exceeds order quantity %s" % (filled, qty)}
    if state == "FILLED" and qty is not None and filled < qty:
        return {"ok": False, "reason": EVIDENCE_INCONSISTENT,
                "detail": "venue says FILLED but reports %s of %s"
                          % (filled, qty)}
    if filled > 0:
        if px is None:
            return {"ok": False, "reason": EVIDENCE_MISSING,
                    "detail": "fill price not reported for a non-zero fill"}
        if not (0.0 < px <= 1.0):
            return {"ok": False, "reason": EVIDENCE_MALFORMED,
                    "detail": "fill price %s outside (0,1]" % px}
        if fees is None:
            # THE FEE IS PART OF THE CASH THAT LEFT. Booking a fill
            # without it understates the cumulative spend against the
            # $100, so an unreported fee withholds the conclusion.
            return {"ok": False, "reason": FEES_UNKNOWN,
                    "detail": "fees not reported for a non-zero fill"}
        if fees < 0:
            return {"ok": False, "reason": EVIDENCE_MALFORMED,
                    "detail": "negative fees"}
    return {"ok": True, "reason": EVIDENCE_COMPLETE, "filled": filled,
            "fillPrice": px, "fees": fees or 0.0, "quantity": qty}


def settle(submit_result: dict, status: dict, quantity=None,
           booked_usd: float = 0.0) -> dict:
    """Cash that actually left the account, from the VENUE'S numbers.

    IDEMPOTENT BY CONSTRUCTION. `booked_usd` is the high-water mark
    already recorded for this lifecycle, and the result's `delta` is
    what is still unbooked. Reading the same terminal status ten times
    books the cash once, because the tenth read computes the same total
    and a delta of zero -- the `booked_filled` cursor discipline from
    mirror_orders, which exists because book 1333 booked one of two
    fills and the ledger and the venue disagreed by 93 shares.

    A cash total that goes DOWN between reads is not a refund; it is the
    venue contradicting itself, and it is refused rather than netted.
    """
    ev = fill_evidence(status, quantity)
    booked = _f(booked_usd) or 0.0
    if not ev["ok"]:
        return {"spend": None, "delta": None, "cashTotal": None,
                "evidence": ev["reason"], "detail": ev.get("detail"),
                "mayRelease": False,
                "whyNot": MISSING_IS_NOT_ZERO}

    cash = round(ev["filled"] * (ev["fillPrice"] or 0.0) + ev["fees"], 2)
    delta = round(cash - booked, 2)
    if delta < -1e-9:
        return {"spend": None, "delta": None, "cashTotal": cash,
                "evidence": EVIDENCE_INCONSISTENT,
                "detail": "cash total fell from %.2f to %.2f between reads"
                          % (booked, cash),
                "mayRelease": False, "whyNot": MISSING_IS_NOT_ZERO}

    entry_terminal = bool(status.get("terminal"))
    return {
        "cashTotal": cash,
        "delta": max(0.0, delta),          # book only what is unbooked
        "spend": cash,
        "filled": ev["filled"],
        "fees": ev["fees"],
        "evidence": EVIDENCE_COMPLETE,
        "entryTerminal": entry_terminal,
        # THE ENTRY BEING TERMINAL IS NOT THE LIFECYCLE BEING DONE.
        "mayRelease": False,
        "whyNot": (A_TERMINAL_ENTRY_IS_NOT_A_RECONCILED_LIFECYCLE
                   if entry_terminal else
                   "the venue's state is not terminal, so the order can "
                   "still fill"),
    }


def lifecycle_reconciled(entry_status: dict, settlement: dict,
                         inventory_quantity, fills_booked: bool) -> dict:
    """May the reserve be released? Only when the POSITION is resolved.

    Four separate facts, all required, each reported by name:
      * the entry order reached the venue's own terminal state;
      * its fill evidence is complete;
      * the inventory it created is GONE -- a known zero, not an
        unknown;
      * every fill is booked against the session's cumulative spend.

    A filled entry with 10 shares still held is the case that matters:
    the order is finished and the lifecycle is not, so the exit's
    obligations stay reserved.
    """
    blockers = []
    if not entry_status.get("terminal"):
        blockers.append("ENTRY_NOT_TERMINAL")
    if settlement.get("evidence") != EVIDENCE_COMPLETE:
        blockers.append(settlement.get("evidence") or EVIDENCE_MISSING)
    inv = _f(inventory_quantity)
    if inventory_quantity is None or inv is None:
        blockers.append("INVENTORY_UNKNOWN")
    elif abs(inv) > 1e-9:
        blockers.append("INVENTORY_OPEN")
    if fills_booked is not True:
        blockers.append("FILLS_NOT_BOOKED")
    return {"mayRelease": not blockers,
            "blockers": blockers,
            "inventoryQuantity": inv,
            "note": A_TERMINAL_ENTRY_IS_NOT_A_RECONCILED_LIFECYCLE}


def remaining_obligation(ticket: dict, inventory_quantity) -> dict:
    """What must stay reserved while inventory is still held.

    While a position is open the exit has not been paid for, so the exit
    fee reserve stays held. An unknown inventory holds the FULL all-in
    reserve, because an unknown position is not a small one.
    """
    inv = _f(inventory_quantity)
    xf = _f(ticket.get("exitFeeReserve"))
    if inventory_quantity is None or inv is None:
        return {"hold": "FULL_ALL_IN_RESERVE",
                "why": "inventory unknown; an unread position is not a "
                       "closed one"}
    if abs(inv) > 1e-9:
        return {"hold": "EXIT_FEE_RESERVE", "amount": xf,
                "why": "the exit has not been paid for"}
    return {"hold": "NOTHING", "amount": 0.0,
            "why": "the position is a known zero"}


def live_venue(**kw):
    """The real adapter, behind the same four methods.

    WHAT THIS USED TO BE, AND WHY IT WAS WORSE THAN MISSING. It raised
    NotImplementedError from open_order_ids -- and the other three
    methods guessed: they sent `TIME_IN_FORCE_GOOD_TILL_CANCELLED`, a
    token this venue does not take (the spelling it takes is
    ...GOOD_TILL_CANCEL), and passed `client_order_id` into
    `pmus.submit_fok`, which has no such parameter and would have
    dropped it silently. A stub that raises is honest; a stub that maps
    plausibly and wrongly is the dangerous kind.

    `calibration_adapter.LiveVenue` maps explicitly, refuses an order
    type it cannot state exactly, and says out loud that the venue has
    no client-identifier field. Nothing in the monitoring release
    imports it.
    """
    from .calibration_adapter import LiveVenue

    return LiveVenue(**kw)


# ── the durable handoff ──────────────────────────────────────────────
# THE CALLABLE PATH VERIFIES ITS OWN APPROVED STATE. Until now the
# sequence was described in a docstring -- "the lifecycle row and its
# reserve exist first" -- and `submit()` would happily send for a ticket
# no row had ever been written for. A comment is not a precondition. A
# handoff whose first step is trusted rather than checked is the same
# defect as the uncalled census, one layer down.

H_NO_ROW = "NO_APPROVED_DURABLE_ROW"
H_WRONG_STATE = "LIFECYCLE_NOT_IN_APPROVED_STATE"
H_NO_RESERVE = "NO_RESERVE_HELD"
H_TICKET_DRIFT = "TICKET_DOES_NOT_MATCH_THE_APPROVED_ROW"
H_STOPPED = "OPERATOR_STOP_ENGAGED"
H_ALREADY_SENT = "LIFECYCLE_ALREADY_HAS_A_VENUE_ORDER"

BOUND_FIELDS = ("marketId", "outcome", "side", "price", "quantity")


def approved_state_blockers(ticket: dict, row: dict | None,
                            session_stopped: bool = False) -> list:
    """Why this ticket may NOT be sent, read off the durable row.

    The row is the approval. Its price, quantity, market and outcome are
    what a human said yes to, so a ticket that differs from it in any of
    them is a different ticket wearing an approved name -- refused as
    drift rather than reconciled toward whichever copy looks newer.
    """
    out = []
    if session_stopped:
        out.append(H_STOPPED)
    if not row:
        out.append(H_NO_ROW)
        return sorted(set(out))
    if row.get("state") != "APPROVED":
        out.append("%s: %s" % (H_WRONG_STATE, row.get("state")))
    if not (_f(row.get("reserve")) or 0) > 0:
        out.append(H_NO_RESERVE)
    if row.get("venueOrderId"):
        out.append("%s: %s" % (H_ALREADY_SENT, row["venueOrderId"]))
    for k in BOUND_FIELDS:
        a, b = ticket.get(k), row.get(k)
        if a is None or b is None:
            out.append("%s: %s missing" % (H_TICKET_DRIFT, k))
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(float(a) - float(b)) > 1e-9:
                out.append("%s: %s %s != %s" % (H_TICKET_DRIFT, k, a, b))
        elif str(a) != str(b):
            out.append("%s: %s %r != %r" % (H_TICKET_DRIFT, k, a, b))
    return sorted(set(out))


def guarded_submit(venue, ticket: dict, row: dict | None,
                   session_stopped: bool = False, persist=None) -> dict:
    """The whole handoff, in one callable that checks before it acts.

        verified preflight
          -> approved ticket            (a human said yes)
          -> durable reserve + row      (checked HERE, not assumed)
          -> pre-image                  (read inside submit())
          -> ONE send                   (never retried)
          -> persisted response or ambiguity
          -> reconciliation             (reconcile/cancel/settle)

    `persist(record)` is called with the outcome BEFORE this returns, on
    every path including the ambiguous one -- an ambiguous send that was
    not written down is the one that cannot be reconciled afterwards. If
    persistence itself fails the failure is returned, not swallowed: we
    would rather an operator sees "sent but not recorded" than a clean
    result hiding it.
    """
    why = approved_state_blockers(ticket, row, session_stopped)
    if why:
        return {"outcome": REFUSED_BY_VENUE, "sent": False,
                "reason": "APPROVED_STATE_NOT_VERIFIED: " + ", ".join(why),
                "blockers": why, "preOpenOrderIds": None}

    result = submit(venue, ticket)
    result["clientOrderId"] = ticket.get("clientOrderId")
    if persist is not None:
        try:
            persist(result)
            result["persisted"] = True
        except Exception as exc:                               # noqa: BLE001
            result["persisted"] = False
            result["persistError"] = type(exc).__name__
            result["note"] = (
                "THE SEND HAPPENED AND THE RECORD DID NOT. Reconcile "
                "against preOpenOrderIds before anything else is sent.")
    return result
