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


class VenueUnreadable(Exception):
    """A read failed. Not an empty book, not a cancelled order."""


def submit(venue, ticket: dict) -> dict:
    """Place the entry. Returns a named outcome; never raises on a lost
    response, and never retries.

    The pre-image is taken FIRST and returned in every outcome, so an
    ambiguous send can be reconciled later against orders that were not
    already on the market.
    """
    market = ticket["marketId"]
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

    try:
        resp = venue.submit(
            market_id=market, price=ticket["price"],
            quantity=ticket["quantity"], side=ticket.get("side", "BUY"),
            order_type=ticket["orderType"],
            client_order_id=ticket["clientOrderId"])
    except Exception as exc:                                   # noqa: BLE001
        return {"outcome": AMBIGUOUS, "sent": True,
                "reason": "SEND_RAISED: %s" % type(exc).__name__,
                "preOpenOrderIds": pre, "venueOrderId": None,
                "note": A_LOST_RESPONSE_IS_NOT_A_REFUSAL}

    if not isinstance(resp, dict):
        return {"outcome": AMBIGUOUS, "sent": True,
                "reason": "RESPONSE_NOT_A_MAPPING",
                "preOpenOrderIds": pre, "venueOrderId": None,
                "note": A_LOST_RESPONSE_IS_NOT_A_REFUSAL}

    if resp.get("ok") is False and resp.get("status") in (
            "post_only_rejected", "rejected"):
        # A NAMED REFUSAL IS DIFFERENT FROM SILENCE: the venue told us it
        # did not accept the order, so nothing rests and nothing filled.
        return {"outcome": REFUSED_BY_VENUE, "sent": True,
                "reason": str(resp.get("status")),
                "preOpenOrderIds": pre, "venueOrderId": None}

    oid = resp.get("order_id")
    if not oid:
        return {"outcome": AMBIGUOUS, "sent": True,
                "reason": "NO_ORDER_ID_IN_RESPONSE",
                "preOpenOrderIds": pre, "venueOrderId": None,
                "note": A_LOST_RESPONSE_IS_NOT_A_REFUSAL}

    filled = _f(resp.get("filled_shares")) or 0.0
    px = _f(resp.get("fill_price"))
    return {"outcome": SUBMITTED, "sent": True,
            "venueOrderId": str(oid), "preOpenOrderIds": pre,
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
    return {"outcome": outcome, "terminal": terminal,
            "venueState": state, "filled": filled, "quantity": qty,
            "fillPrice": px,
            "cashOut": None if (px is None or filled is None)
            else round(filled * px, 2)}


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


def settle(submit_result: dict, status: dict) -> dict:
    """What actually left the account, from the VENUE'S numbers.

    The spend booked against the $100 is the venue's filled quantity at
    the venue's price, never the ticket's intended cost. A ticket that
    intended $4.00 and filled 6 of 10 shares spent what the venue says,
    and a reserve is released only once that is known.
    """
    filled = _f(status.get("filled")) or 0.0
    px = _f(status.get("fillPrice"))
    if px is None and filled:
        return {"spend": None, "reason": "FILL_PRICE_UNREADABLE",
                "mayRelease": False}
    spend = round(filled * (px or 0.0), 2)
    return {"spend": spend,
            "filled": filled,
            "mayRelease": bool(status.get("terminal")),
            "whyNot": None if status.get("terminal") else
            "the venue's state is not terminal, so the order can still fill"}


def live_venue():                                             # pragma: no cover
    """The real adapter, behind the same four methods. Never imported by
    a route in the monitoring release."""
    from . import pmus

    class _Live:
        def open_order_ids(self, market_id):
            raise NotImplementedError(
                "wire to the existing open-orders read before first use")

        def submit(self, market_id, price, quantity, side, order_type,
                   client_order_id):
            return pmus.submit_fok(
                market_id, price, int(quantity),
                sell=(side == "SELL"),
                tif="TIME_IN_FORCE_GOOD_TILL_CANCELLED",
                post_only=("POST_ONLY" in (order_type or "")))

        def status(self, order_id):
            return pmus.order_status(order_id)

        def cancel(self, order_id, market_id):
            return pmus.cancel_order(order_id, market_id)

    return _Live()
