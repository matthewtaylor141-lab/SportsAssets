"""THE READ-ONLY HALF OF THE VENUE ADAPTER.

Split out of `calibration_adapter` so that `calibration_evidence` can use
the strict open-orders reader WITHOUT importing a module that also holds
`LiveVenue.submit`. "Submission is not reachable from the evidence
command" has to be true of what the command imports, not only of what it
calls; a module in memory whose submit path is one attribute away is a
weaker statement than the docstring was making.

Nothing here creates, cancels or amends an order. It reads.

UNREADABLE IS NOT EMPTY, and the shapes that must refuse to look empty
are listed in `_rows_of`: None, error envelopes, a mapping with no orders
field, a null or non-list orders field, and anything that is neither a
mapping nor a sequence.

PAGINATION IS DECIDED BY THE READER'S SIGNATURE, not by catching a
TypeError -- a TypeError raised inside the reader is a failed read, and
reading it as "this endpoint offers no cursor" ends the walk on a partial
page.
"""
from __future__ import annotations

import inspect


class VenueUnreadable(Exception):
    """A read failed. Not an empty book, not a cancelled order.

    Defined here rather than in `calibration_execute` so the read-only
    module does not depend on the module that sends orders.
    """


UNREADABLE_IS_NOT_EMPTY = (
    "a failed open-orders read raises; an account with no resting orders "
    "returns []. Collapsing the two would let an outage read as a clean "
    "market and an ambiguous send as a first order")


def _order_id(o: dict):
    for k in ("order_id", "orderId", "id"):
        if o.get(k):
            return str(o[k])
    return None


def raw_open_orders(slugs):
    """The venue's UNNORMALIZED orders.list response.

    `pmus.open_orders` flattens None and error envelopes into [] before a
    caller can see them, so the pre-image reads the raw response instead.
    Read-only; raises to the caller.
    """
    from . import pmus
    client = pmus._get_client()
    params = {"slugs": list(slugs)} if slugs else None
    return client.orders.list(params)


def _reader_takes_a_cursor(reader) -> bool:
    """Whether the reader ACCEPTS a cursor, by signature.

    Not by catching TypeError: a TypeError raised inside the reader --
    from a changed SDK, a bad row, a genuine bug -- is indistinguishable
    from 'this endpoint takes no cursor', and reading it as the latter
    ends the walk on a partial page.
    """
    try:
        sig = inspect.signature(reader)
    except (TypeError, ValueError):
        return False
    for name, p in sig.parameters.items():
        if name == "cursor":
            return True
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            return True
    return False


def _rows_of(resp):
    """The order rows of one response, or raise.

    EVERY shape that is not a response carrying orders raises. None, an
    error envelope, a mapping with no `orders` key, and anything that is
    neither a mapping nor a sequence are all UNREADABLE -- never empty.
    """
    if resp is None:
        raise VenueUnreadable("OPEN_ORDERS_RESPONSE_IS_NONE")
    if isinstance(resp, dict):
        for key in ("error", "errors", "detail", "fault"):
            if resp.get(key):
                raise VenueUnreadable("OPEN_ORDERS_ERROR_ENVELOPE: %s" % key)
        if "orders" not in resp:
            # A mapping without the field cannot be read as 'no orders':
            # we do not know whether the account is clean or the shape
            # changed under us.
            raise VenueUnreadable("OPEN_ORDERS_RESPONSE_HAS_NO_ORDERS_FIELD")
        rows = resp.get("orders")
        if rows is None:
            raise VenueUnreadable("OPEN_ORDERS_FIELD_IS_NULL")
        if not isinstance(rows, (list, tuple)):
            raise VenueUnreadable("OPEN_ORDERS_FIELD_IS_NOT_A_LIST")
        cursor = resp.get("nextCursor") or resp.get("cursor") or None
        return list(rows), cursor
    if isinstance(resp, (list, tuple)):
        return list(resp), None
    raise VenueUnreadable("OPEN_ORDERS_RESPONSE_NOT_A_MAPPING_OR_LIST")


def read_open_order_ids(market_id: str, reader=None, max_pages: int = 20):
    """Every resting order id on ONE market, from the venue.

    PAGINATION IS VERIFIED, NOT ASSUMED. If the reader accepts a cursor
    the walk follows it to exhaustion. If it does not and the response
    nonetheless carries one, there are pages we cannot reach and the walk
    RAISES -- a partial list of resting orders is exactly as dangerous as
    an unreadable one, because the pre-image it feeds is supposed to be
    complete. Hitting the page bound raises for the same reason.

    Returns the rows as well as the ids: attribution needs the fields,
    not just the identifiers.
    """
    reader = reader or raw_open_orders
    takes_cursor = _reader_takes_a_cursor(reader)

    ids, rows_out, pages = [], [], 0
    cursor, saw_cursor = None, False
    while pages < max_pages:
        pages += 1
        try:
            resp = (reader([market_id], cursor=cursor)
                    if (takes_cursor and cursor is not None)
                    else reader([market_id]))
        except VenueUnreadable:
            raise
        except Exception as exc:                               # noqa: BLE001
            # Including TypeError. A TypeError from inside the reader is
            # a failed read, never evidence about pagination.
            raise VenueUnreadable("OPEN_ORDERS_UNREADABLE: %s"
                                  % type(exc).__name__) from exc

        rows, cursor = _rows_of(resp)
        saw_cursor = saw_cursor or bool(cursor)
        for o in rows:
            if not isinstance(o, dict):
                raise VenueUnreadable("OPEN_ORDER_ROW_NOT_A_MAPPING")
            oid = _order_id(o)
            if oid is None:
                # AN ORDER WE CANNOT NAME BREAKS THE PRE-IMAGE. It would
                # be invisible in the after-image too, so a new order
                # could hide behind it.
                raise VenueUnreadable("OPEN_ORDER_WITHOUT_AN_ID")
            ids.append(oid)
            rows_out.append(o)
        if not cursor:
            break
        if not takes_cursor:
            # The venue is offering pages this reader cannot ask for.
            raise VenueUnreadable("OPEN_ORDERS_CURSOR_THE_READER_CANNOT_FOLLOW")
    else:
        raise VenueUnreadable("OPEN_ORDERS_PAGINATION_UNRESOLVED")

    return {"ids": sorted(set(ids)), "rows": rows_out, "pages": pages,
            "paginated": saw_cursor,
            "readerAcceptsCursor": takes_cursor,
            "unreadableIsNotEmpty": UNREADABLE_IS_NOT_EMPTY}




# ── THE ORDER MAPPING, pure and read-only ────────────────────────────
#
# It lives here, beside the reads, for one reason: the evidence command
# must be able to build a PREVIEW request -- a documented read-only
# operation -- without importing a module that can also send an order.
# Duplicating the intent table in two places would be the same defect
# this codebase keeps hitting: a rule declared twice drifts, and the
# copy that drifts is the one nobody is testing.
#
# `calibration_adapter` imports these and re-exports them, so its public
# surface is unchanged. Nothing here opens a socket.

# ── the mapping, explicit and closed ─────────────────────────────────
# Each ticket order type maps to (venue tif, post_only). An order type
# not in this table is REFUSED: a calibration ticket whose semantics we
# cannot state exactly is not one to send.
ORDER_TYPES = {
    "LIMIT_GTC": ("TIME_IN_FORCE_GOOD_TILL_CANCEL", False),
    "LIMIT_GTC_POST_ONLY": ("TIME_IN_FORCE_GOOD_TILL_CANCEL", True),
    "LIMIT_IOC": ("TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", False),
    "LIMIT_FOK": ("TIME_IN_FORCE_FILL_OR_KILL", False),
}

# The venue's own spelling. The 'CANCELLED' variant is not a token this
# venue takes and is listed here so the mistake cannot come back quietly.
VENUE_TIFS = frozenset(t for t, _ in ORDER_TYPES.values())
NOT_A_VENUE_TIF = "TIME_IN_FORCE_GOOD_TILL_CANCELLED"

OPERATIONS = ("BUY", "SELL")
OUTCOMES = ("LONG", "SHORT")

# (operation, outcome) -> (intent ARGUMENT to pmus.submit_fok, sell flag,
#                          the NATIVE wire intent the venue must receive)
INTENTS = {
    ("BUY", "LONG"): ("ORDER_INTENT_BUY_LONG", False, "ORDER_INTENT_BUY_LONG"),
    ("BUY", "SHORT"): ("ORDER_INTENT_BUY_SHORT", False, "ORDER_INTENT_BUY_SHORT"),
    ("SELL", "LONG"): ("ORDER_INTENT_BUY_LONG", True, "ORDER_INTENT_SELL_LONG"),
    ("SELL", "SHORT"): ("ORDER_INTENT_BUY_SHORT", True, "ORDER_INTENT_SELL_SHORT"),
}

# What pmus.submit_fok will accept as its `intent` argument. Anything
# else comes back 'bad_intent' having sent nothing.
ACCEPTED_INTENT_ARGUMENTS = frozenset(("ORDER_INTENT_BUY_LONG",
                                       "ORDER_INTENT_BUY_SHORT"))
NATIVE_INTENTS = frozenset(n for _, _, n in INTENTS.values())

CLIENT_ORDER_ID_IS_NOT_SENT = (
    "this venue's CreateOrderParams has no client-identifier field, so the "
    "ticket's clientOrderId is never transmitted. It names the DURABLE ROW, "
    "and attribution at the venue is done by corroborated read-back "
    "(adopt_from_pre_image), not by an echo")

NEVER_CANCEL_TO_RESOLVE = (
    "an unattributed order is NOT cancelled to find out whose it is. A "
    "cancel aimed at a possibly unrelated order is an action on someone "
    "else's money, and the 18:05Z cancel storm showed a 200 OK cancel does "
    "not even mean the order stopped. Ambiguity is escalated, not cleared "
    "by an intervention.")

R_UNMAPPED_ORDER_TYPE = "UNMAPPED_ORDER_TYPE"
R_UNMAPPED_SIDE = "UNMAPPED_SIDE"
R_UNMAPPED_OUTCOME = "UNMAPPED_OUTCOME"
R_UNMAPPED_OPERATION = "UNMAPPED_OPERATION_OUTCOME_PAIR"

# Attribution outcomes for the read-back.
ADOPTED = "ADOPTED_ONE_CORROBORATED_ORDER"
NONE_NEW = "NO_NEW_ORDER_ON_THE_MARKET"
AMBIGUOUS_NEW = "SEVERAL_NEW_ORDERS_CANNOT_ATTRIBUTE"
UNCORROBORATED = "CANDIDATE_NOT_CORROBORATED"

# The named writer-isolation conditions. Attribution requires one of
# these to be asserted AND to hold; "nobody else would have" is not one
# of them.
ISOLATION_SOLE_CLAIM = "SOLE_DURABLE_CLAIM_ON_THIS_MARKET"
ISOLATION_CONDITIONS = frozenset((ISOLATION_SOLE_CLAIM,))

# Evidence that must match between the approved ticket and the candidate
# order before the candidate is ours. `account` is not a per-order field:
# the venue scopes orders.list to the authenticated account, so it is
# asserted as part of the isolation condition rather than compared here.
CORROBORATING_FIELDS = ("market", "outcomeSide", "intent", "price",
                        "quantity")


def _fail(reason, **extra):
    d = {"MATCHES": False, "REASON": reason}
    d.update(extra)
    return d


def order_params(ticket: dict) -> dict:
    """Ticket -> the adapter's own arguments. Refuses what it cannot map.

    Returned as a dict rather than passed straight through so a test can
    assert the mapping without a venue, and so an operator can read what
    would be sent before it is.
    """
    kind = ticket.get("orderType")
    if kind not in ORDER_TYPES:
        raise ValueError("%s: %r (known: %s)"
                         % (R_UNMAPPED_ORDER_TYPE, kind, sorted(ORDER_TYPES)))
    side = ticket.get("side", "BUY")
    if side not in OPERATIONS:
        raise ValueError("%s: %r (known: %s)"
                         % (R_UNMAPPED_SIDE, side, list(OPERATIONS)))

    # NO DEFAULT. The outcome decides which side of a shared-identifier
    # market the order lands on, so a ticket that does not name it is
    # refused rather than assumed to be LONG.
    # `outcome` on a ticket is the human/venue DESCRIPTION of the side
    # ("SIN to win"). `outcomeSide` is the machine selector the venue
    # actually reads through the intent, and it is what is mapped here.
    outcome = ticket.get("outcomeSide")
    if outcome not in OUTCOMES:
        raise ValueError("%s: %r (known: %s) -- the outcome side names which "
                         "side of a shared-identifier market the order lands "
                         "on and cannot be defaulted"
                         % (R_UNMAPPED_OUTCOME, outcome, list(OUTCOMES)))
    if (side, outcome) not in INTENTS:
        raise ValueError("%s: %r" % (R_UNMAPPED_OPERATION, (side, outcome)))

    tif, post_only = ORDER_TYPES[kind]
    assert tif != NOT_A_VENUE_TIF                      # see module docstring
    intent_arg, sell, native = INTENTS[(side, outcome)]
    # If this ever fails, pmus would answer 'bad_intent' and send nothing;
    # better to refuse here, where the reason is legible.
    assert intent_arg in ACCEPTED_INTENT_ARGUMENTS
    assert native in NATIVE_INTENTS

    return {
        "us_market_slug": ticket["marketId"],
        "limit_price": float(ticket["price"]),
        "quantity": int(ticket["quantity"]),
        "sell": sell,
        "tif": tif,
        "post_only": post_only,
        # The ARGUMENT pmus takes (it names the outcome), and the NATIVE
        # wire intent the venue must end up receiving. They differ on an
        # exit, and the test asserts the native one off CreateOrderParams.
        "intent": intent_arg,
        "nativeIntentExpected": native,
        "operation": side,
        "outcomeSide": outcome,
        "clientOrderIdSent": False,
        "clientOrderIdNote": CLIENT_ORDER_ID_IS_NOT_SENT,
    }




def preview_params(ticket: dict) -> dict:
    """The venue's own PREVIEW request for exactly this sized ticket.

    The same mapping the order would use -- so a preview that disagrees
    with the schedule is disagreeing about the order we would actually
    send, not about a differently-shaped one. It creates nothing: the
    caller hands this to `orders.preview`, which is a documented
    read-only operation.
    """
    p = order_params(ticket)
    return {"request": {
        "marketSlug": p["us_market_slug"],
        "intent": p["nativeIntentExpected"],
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": "%.4f" % p["limit_price"], "currency": "USD"},
        "quantity": p["quantity"],
        "tif": p["tif"],
        "synchronousExecution": True,
    }, "previewOnly": True}
