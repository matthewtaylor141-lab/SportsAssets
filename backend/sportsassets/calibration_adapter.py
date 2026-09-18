"""THE REAL VENUE ADAPTER for the calibration lifecycle.

`calibration_execute.live_venue()` used to raise NotImplementedError from
`open_order_ids` and guessed at the rest. Two things were wrong with the
guess, and both would have reached the venue:

  * it sent `TIME_IN_FORCE_GOOD_TILL_CANCELLED`. The token this venue
    actually takes is `TIME_IN_FORCE_GOOD_TILL_CANCEL` -- the misspelling
    appeared exactly once in this repository, in that function, and
    nowhere the venue has ever accepted.
  * it passed `client_order_id` into `pmus.submit_fok`, which has no such
    parameter, so the identifier would have been dropped on the floor.

Both are fixed here by mapping EXPLICITLY and refusing anything unmapped,
rather than passing a plausible string through.

THE VENUE HAS NO CLIENT ORDER IDENTIFIER.

`CreateOrderParams` carries marketSlug, intent, type, price, quantity,
tif, synchronousExecution, and optionally participateDontInitiate and
goodTillTime. There is no field for a caller-supplied id. So the
calibration ticket's `clientOrderId` is NOT sent, and this module says so
loudly instead of accepting it and discarding it.

THE ALTERNATIVE RECONCILIATION, since there is no echo to match on:

  1. the durable row is written first, keyed by clientOrderId, so the
     INTENT has a name before anything is sent (calibration_store);
  2. the ids already open on that market are read immediately before the
     send (`open_order_ids`), so a lost response is matched only against
     orders that were NOT already there -- the mirror's `pre_ids`
     discipline, which exists because of book 863;
  3. `adopt_from_pre_image()` is the read-back: the ids open AFTER the
     send, minus the pre-image, on that market. Exactly one new id is an
     attribution; zero or several is NOT, and is returned as such rather
     than resolved by picking one.

UNREADABLE IS NOT EMPTY. A failed open-orders read raises
`VenueUnreadable`. An account with no resting orders returns `[]`. The
caller treats those completely differently and must never be handed the
same value for both.
"""
from __future__ import annotations

from .calibration_execute import VenueUnreadable

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

SIDES = {"BUY": False, "SELL": True}      # -> pmus.submit_fok(sell=...)

CLIENT_ORDER_ID_IS_NOT_SENT = (
    "this venue's CreateOrderParams has no client-identifier field, so the "
    "ticket's clientOrderId is never transmitted. It names the DURABLE ROW, "
    "and attribution at the venue is done by pre-image difference "
    "(adopt_from_pre_image), not by an echo")

UNREADABLE_IS_NOT_EMPTY = (
    "a failed open-orders read raises; an account with no resting orders "
    "returns []. Collapsing the two would let an outage read as a clean "
    "market and an ambiguous send as a first order")

R_UNMAPPED_ORDER_TYPE = "UNMAPPED_ORDER_TYPE"
R_UNMAPPED_SIDE = "UNMAPPED_SIDE"

# Attribution outcomes for the read-back.
ADOPTED = "ADOPTED_ONE_NEW_ORDER"
NONE_NEW = "NO_NEW_ORDER_ON_THE_MARKET"
AMBIGUOUS_NEW = "SEVERAL_NEW_ORDERS_CANNOT_ATTRIBUTE"


def order_params(ticket: dict) -> dict:
    """Ticket -> the adapter's own arguments. Refuses what it cannot map.

    Returned as a dict rather than passed straight through so a test can
    assert the mapping without a venue, and so an operator can read what
    would be sent before it is.
    """
    kind = ticket.get("orderType")
    if kind not in ORDER_TYPES:
        raise ValueError("%s: %r (known: %s)"
                         % (R_UNMAPPED_ORDER_TYPE, kind,
                            sorted(ORDER_TYPES)))
    side = ticket.get("side", "BUY")
    if side not in SIDES:
        raise ValueError("%s: %r" % (R_UNMAPPED_SIDE, side))
    tif, post_only = ORDER_TYPES[kind]
    assert tif != NOT_A_VENUE_TIF                      # see module docstring
    return {
        "us_market_slug": ticket["marketId"],
        "limit_price": float(ticket["price"]),
        "quantity": int(ticket["quantity"]),
        "sell": SIDES[side],
        "tif": tif,
        "post_only": post_only,
        # The venue decides the side of a shared-identifier market from
        # the INTENT, not the slug (the wrong-side incident). Calibration
        # is fully funded long only, so the intent is stated rather than
        # defaulted.
        "intent": "ORDER_INTENT_BUY_LONG",
        "clientOrderIdSent": False,
        "clientOrderIdNote": CLIENT_ORDER_ID_IS_NOT_SENT,
    }


def _order_id(o: dict):
    for k in ("order_id", "orderId", "id"):
        if o.get(k):
            return str(o[k])
    return None


def read_open_order_ids(market_id: str, reader=None, max_pages: int = 20):
    """Every resting order id on ONE market, from the venue.

    PAGINATION IS VERIFIED, NOT ASSUMED. `orders.list` is called with the
    market filter; if the response carries a continuation cursor the walk
    follows it to exhaustion, and if it does not, that fact is recorded
    rather than treated as "there was only one page". Hitting the page
    bound is an unresolved walk and RAISES -- a partial list of resting
    orders is exactly as dangerous as an unreadable one, because the
    pre-image it feeds is supposed to be complete.
    """
    from . import pmus

    reader = reader or pmus.open_orders
    ids, seen_cursor, pages = [], False, 0
    cursor = None
    while pages < max_pages:
        pages += 1
        try:
            resp = reader([market_id]) if cursor is None else reader(
                [market_id], cursor=cursor)
        except TypeError:
            # The verified reader takes no cursor. That is not a bug --
            # it is the endpoint not offering one -- and it ends the walk
            # after the single page it does serve.
            resp = reader([market_id])
        except Exception as exc:                               # noqa: BLE001
            raise VenueUnreadable("OPEN_ORDERS_UNREADABLE: %s"
                                  % type(exc).__name__) from exc

        if isinstance(resp, dict):
            rows = resp.get("orders") or []
            cursor = resp.get("nextCursor") or resp.get("cursor") or None
            seen_cursor = seen_cursor or bool(cursor)
        else:
            rows = list(resp or ())
            cursor = None
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
        if not cursor:
            break
    else:
        raise VenueUnreadable("OPEN_ORDERS_PAGINATION_UNRESOLVED")

    return {"ids": sorted(set(ids)), "pages": pages,
            "paginated": seen_cursor,
            "paginationOffered": seen_cursor,
            "unreadableIsNotEmpty": UNREADABLE_IS_NOT_EMPTY}


def adopt_from_pre_image(pre_ids, post_ids) -> dict:
    """Attribute an ambiguous send by difference, or refuse to.

    EXACTLY ONE new resting id is an attribution. None means the order
    is not resting -- which is NOT the same as never placed, because it
    may have filled and left the book, so the caller still has to read
    the account's fills. Several means we cannot tell ours from someone
    else's, and picking one would be a guess with money behind it.
    """
    pre, post = set(map(str, pre_ids or ())), set(map(str, post_ids or ()))
    new = sorted(post - pre)
    if len(new) == 1:
        return {"outcome": ADOPTED, "venueOrderId": new[0], "new": new}
    if not new:
        return {"outcome": NONE_NEW, "venueOrderId": None, "new": [],
                "note": "not resting is not the same as never placed; it "
                        "may have filled and left the book"}
    return {"outcome": AMBIGUOUS_NEW, "venueOrderId": None, "new": new,
            "note": "two or more orders appeared; choosing one would be a "
                    "guess"}


class LiveVenue:
    """The four methods `calibration_execute` calls, on the real adapter.

    Constructed by `calibration_execute.live_venue()`. Nothing in the
    monitoring release imports it.
    """

    def __init__(self, submit_fn=None, status_fn=None, cancel_fn=None,
                 open_orders_fn=None):
        from . import pmus
        self._submit = submit_fn or pmus.submit_fok
        self._status = status_fn or pmus.order_status
        self._cancel = cancel_fn or pmus.cancel_order
        self._open = open_orders_fn or pmus.open_orders

    def open_order_ids(self, market_id):
        return read_open_order_ids(market_id, reader=self._open)["ids"]

    def submit(self, market_id, price, quantity, side, order_type,
               client_order_id=None):
        p = order_params({"marketId": market_id, "price": price,
                          "quantity": quantity, "side": side,
                          "orderType": order_type})
        # client_order_id is accepted by the signature and DELIBERATELY
        # not forwarded -- the venue has no field for it. See the module
        # docstring for the reconciliation that replaces it.
        return self._submit(p["us_market_slug"], p["limit_price"],
                            p["quantity"], sell=p["sell"], tif=p["tif"],
                            intent=p["intent"], post_only=p["post_only"])

    def status(self, order_id):
        return self._status(order_id)

    def cancel(self, order_id, market_id):
        return self._cancel(order_id, market_id)
