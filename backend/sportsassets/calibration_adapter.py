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

Three further defects were found by an independent read of the first
version of this file and are fixed here. Each was a place where this
module ANSWERED a question it had not actually established.

  A. ATTRIBUTION FROM A SINGLE NEW ORDER ID. One new resting id was
     treated as proof that the id was ours. It is not. It is a
     CANDIDATE. Corroboration and a justified writer-isolation condition
     are now both required, and without them the send stays ambiguous.
  B. UNREADABLE COLLAPSING INTO EMPTY. `list(resp or ())` turned None
     into []; `resp.get("orders") or []` turned an error envelope into
     []. An outage then read as a clean market and an ambiguous send as
     a first order. Every non-order-bearing shape now RAISES.
  C. PAGINATION INFERRED FROM A CAUGHT TypeError. A TypeError raised
     INSIDE the reader -- a real bug, a bad row, a changed SDK -- was
     indistinguishable from "this endpoint takes no cursor", and was
     silently read as the latter, ending the walk early on a partial
     page. The reader's signature is inspected instead, and a TypeError
     from within the reader now surfaces as unreadable.

THE NATIVE INTENT, VERIFIED AGAINST THE ADAPTER IT IS SENT TO

The first version emitted `ORDER_INTENT_BUY_LONG` for BOTH sides. That is
wrong for two independent reasons, and reading pmus rather than guessing
is what shows why:

  * `pmus.submit_fok` accepts ONLY `ORDER_INTENT_BUY_LONG` or
    `ORDER_INTENT_BUY_SHORT` as its `intent` argument. Anything else --
    including the SELL_* tokens -- returns `{"status": "bad_intent"}`
    without sending. So the argument is not the native wire intent; it
    NAMES THE OUTCOME, and pmus derives the wire intent from it together
    with `sell`.
  * The argument therefore has to carry the OUTCOME, which the first
    version hardcoded to LONG. On this venue every two-sided market
    shares one identifier between its sides, so the slug does not name a
    side and `intent` is the only thing that distinguishes them -- the
    wrong-side incident's root cause. A calibration ticket on a SHORT
    outcome sent with BUY_LONG would buy the other side of the market.

So the mapping is by (operation, outcome), all four cases stated, and an
absent or unknown outcome is REFUSED rather than defaulted to LONG:

    (BUY,  LONG)  -> intent=BUY_LONG,  sell=False -> native BUY_LONG
    (BUY,  SHORT) -> intent=BUY_SHORT, sell=False -> native BUY_SHORT
    (SELL, LONG)  -> intent=BUY_LONG,  sell=True  -> native SELL_LONG
    (SELL, SHORT) -> intent=BUY_SHORT, sell=True  -> native SELL_SHORT

`order_params` records the native intent it EXPECTS, and the tests drive
`pmus.submit_fok` itself and assert the intent that lands in
`CreateOrderParams` is that one. Naming the intent on the exit also keeps
`pmus._exit_intent` off its fallback branch, which otherwise infers the
side from a venue position read.

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
  3. `adopt_from_pre_image()` is the read-back, and it is a CORROBORATED
     match, not a difference of one.

UNREADABLE IS NOT EMPTY. A failed open-orders read raises
`VenueUnreadable`. An account with no resting orders returns `[]`. The
caller treats those completely differently and must never be handed the
same value for both.

A KNOWN FAIL-OPEN UPSTREAM, NOT FIXED HERE. `pmus.open_orders` does
`client.orders.list(params) or {}` and then `resp.get("orders") or []`,
so a None or an error envelope has ALREADY become an empty list before
this module could see it, and a row that is not a mapping is silently
dropped. That is the production module the mirror also uses and it is not
changed by the calibration work. This adapter therefore reads the RAW
response itself (`raw_open_orders`) so the shapes it is required to
refuse actually reach it.
"""
from __future__ import annotations

import inspect

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

UNREADABLE_IS_NOT_EMPTY = (
    "a failed open-orders read raises; an account with no resting orders "
    "returns []. Collapsing the two would let an outage read as a clean "
    "market and an ambiguous send as a first order")

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


# ── corroboration ────────────────────────────────────────────────────

def _amount(v):
    """A venue money field as a float, or None when it is unreadable."""
    if isinstance(v, dict):
        for k in ("value", "amount", "decimal"):
            if v.get(k) is not None:
                try:
                    return float(v[k])
                except (TypeError, ValueError):
                    return None
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def corroborate(row: dict, expected: dict, window: dict) -> dict:
    """Does this venue order match the approved ticket, field by field?

    A field the venue did not report is NOT a match. Missing evidence
    leaves the candidate uncorroborated; it never passes by default.
    """
    if not isinstance(row, dict):
        return _fail("ORDER_ROW_NOT_A_MAPPING")

    slug = row.get("marketSlug") or row.get("us_market_slug")
    if not slug:
        return _fail("ORDER_NAMES_NO_MARKET")
    if str(slug) != str(expected.get("market")):
        return _fail("MARKET_MISMATCH", saw=str(slug))

    native = row.get("intent")
    if not native:
        return _fail("ORDER_NAMES_NO_INTENT")
    if str(native) != str(expected.get("nativeIntent")):
        # The intent carries BOTH the operation and the outcome, so this
        # single comparison is where a wrong-side order is caught.
        return _fail("INTENT_MISMATCH", saw=str(native))

    px = _amount(row.get("price"))
    if px is None:
        return _fail("ORDER_PRICE_UNREADABLE")
    want_px = expected.get("price")
    if want_px is None or abs(px - float(want_px)) > 1e-9:
        return _fail("PRICE_MISMATCH", saw=px)

    try:
        qty = float(row.get("quantity"))
    except (TypeError, ValueError):
        return _fail("ORDER_QUANTITY_UNREADABLE")
    want_qty = expected.get("quantity")
    if want_qty is None or abs(qty - float(want_qty)) > 1e-9:
        return _fail("QUANTITY_MISMATCH", saw=qty)

    created = row.get("createTime") or row.get("insertTime") or \
        row.get("created_at")
    if not created:
        return _fail("ORDER_NAMES_NO_CREATION_TIME")
    start, end = window.get("sentAfter"), window.get("readBefore")
    if not start or not end:
        return _fail("SEND_WINDOW_NOT_ESTABLISHED")
    created, start, end = str(created), str(start), str(end)
    if not (start <= created <= end):
        # An order created outside the send window is not this send's,
        # however well the other fields line up.
        return _fail("CREATED_OUTSIDE_THE_SEND_WINDOW", saw=created)

    return {"MATCHES": True, "REASON": None,
            "FIELDS": list(CORROBORATING_FIELDS) + ["time"]}


def isolation_holds(condition) -> dict:
    """Is the writer-isolation claim named, justified and true?

    A single new order is only ours if nothing else could have written
    one. That has to be ASSERTED by the caller as a named condition with
    its evidence -- not inferred here from the fact that we do not know
    of another writer.
    """
    if not isinstance(condition, dict):
        return _fail("WRITER_ISOLATION_NOT_ASSERTED")
    name = condition.get("CONDITION")
    if name not in ISOLATION_CONDITIONS:
        return _fail("WRITER_ISOLATION_CONDITION_UNKNOWN", saw=name)
    if condition.get("HOLDS") is not True:
        return _fail("WRITER_ISOLATION_DOES_NOT_HOLD", saw=condition.get("HOLDS"))
    evidence = condition.get("EVIDENCE")
    if not isinstance(evidence, dict) or not evidence:
        return _fail("WRITER_ISOLATION_UNJUSTIFIED")
    return {"MATCHES": True, "REASON": None, "CONDITION": name}


def adopt_from_pre_image(pre_ids, post_ids, expected=None, rows=None,
                         window=None, writer_isolation=None) -> dict:
    """Attribute an ambiguous send by corroborated read-back, or refuse to.

    A SINGLE NEW ORDER ID IS ONLY A CANDIDATE. To become an attribution
    it must also match the approved ticket on market, outcome, intent,
    price, quantity and creation time, and the caller must assert a named
    writer-isolation condition that holds and is justified. Anything less
    stays ambiguous.

    None new means the order is not RESTING -- which is NOT the same as
    never placed, because it may have filled and left the book, so the
    caller still has to read the account's fills. Several means we cannot
    tell ours from someone else's.

    Nothing here cancels anything. See NEVER_CANCEL_TO_RESOLVE.
    """
    pre, post = set(map(str, pre_ids or ())), set(map(str, post_ids or ()))
    new = sorted(post - pre)
    base = {"new": new, "neverCancelToResolve": NEVER_CANCEL_TO_RESOLVE}

    if not new:
        return dict(base, outcome=NONE_NEW, venueOrderId=None,
                    note="not resting is not the same as never placed; it "
                         "may have filled and left the book")
    if len(new) > 1:
        return dict(base, outcome=AMBIGUOUS_NEW, venueOrderId=None,
                    note="two or more orders appeared; choosing one would be "
                         "a guess")

    candidate = new[0]
    if not isinstance(expected, dict) or not expected:
        return dict(base, outcome=UNCORROBORATED, venueOrderId=None,
                    candidate=candidate,
                    blocker="NO_EXPECTED_TICKET_TO_CORROBORATE_AGAINST")

    by_id = {}
    for r in (rows or ()):
        if isinstance(r, dict):
            rid = _order_id(r)
            if rid is not None:
                by_id[rid] = r
    row = by_id.get(candidate)
    if row is None:
        return dict(base, outcome=UNCORROBORATED, venueOrderId=None,
                    candidate=candidate,
                    blocker="NO_ORDER_ROW_FOR_THE_CANDIDATE_ID")

    matched = corroborate(row, expected, window or {})
    if not matched["MATCHES"]:
        return dict(base, outcome=UNCORROBORATED, venueOrderId=None,
                    candidate=candidate, blocker=matched["REASON"],
                    detail=matched)

    iso = isolation_holds(writer_isolation)
    if not iso["MATCHES"]:
        return dict(base, outcome=UNCORROBORATED, venueOrderId=None,
                    candidate=candidate, blocker=iso["REASON"], detail=iso)

    return dict(base, outcome=ADOPTED, venueOrderId=candidate,
                corroboratedOn=matched["FIELDS"],
                writerIsolation=iso["CONDITION"])


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
        self._open = open_orders_fn or raw_open_orders

    def open_order_ids(self, market_id):
        return read_open_order_ids(market_id, reader=self._open)["ids"]

    def open_orders_read(self, market_id):
        """ids AND rows -- attribution needs the fields, not the ids."""
        return read_open_order_ids(market_id, reader=self._open)

    def submit(self, market_id, price, quantity, side, order_type,
               outcome_side=None, client_order_id=None):
        p = order_params({"marketId": market_id, "price": price,
                          "quantity": quantity, "side": side,
                          "outcomeSide": outcome_side,
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
