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


