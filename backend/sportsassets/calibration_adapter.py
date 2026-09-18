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

# The read-only half lives in its own module so the evidence command can
# use it without importing a submit path. Re-exported here because these
# names are part of this module's published surface.
from .calibration_read import (            # noqa: F401
    UNREADABLE_IS_NOT_EMPTY,
    VenueUnreadable,
    _order_id,
    _reader_takes_a_cursor,
    _rows_of,
    raw_open_orders,
    read_open_order_ids,
)

# THE ORDER MAPPING lives in calibration_read, beside the reads, so the
# evidence command can build a preview request without importing this
# module. Re-exported here: this module's public surface is unchanged.
from .calibration_read import (            # noqa: E402,F401
    ACCEPTED_INTENT_ARGUMENTS,
    INTENTS,
    NATIVE_INTENTS,
    NOT_A_VENUE_TIF,
    OPERATIONS,
    ORDER_TYPES,
    OUTCOMES,
    ADOPTED,
    AMBIGUOUS_NEW,
    CLIENT_ORDER_ID_IS_NOT_SENT,
    CORROBORATING_FIELDS,
    ISOLATION_CONDITIONS,
    ISOLATION_SOLE_CLAIM,
    NEVER_CANCEL_TO_RESOLVE,
    NONE_NEW,
    R_UNMAPPED_OPERATION,
    R_UNMAPPED_ORDER_TYPE,
    R_UNMAPPED_OUTCOME,
    R_UNMAPPED_SIDE,
    UNCORROBORATED,
    VENUE_TIFS,
    _fail,
    order_params,
    preview_params,
)

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

    # THE VENUE'S OWN CORRELATION IDENTIFIER: this venue has none.
    #
    # `calibration_execute` asks every venue for one before it sends, and
    # records what it gets beside the pre-image. Answering None here is a
    # STATEMENT ABOUT THE RETAIL VENUE -- `CreateOrderParams` carries no
    # client-supplied field, which is why the pre-image reconciliation
    # exists at all -- and not a failure to mint one. An institutional
    # adapter, whose REST order schema does carry `clordId`, answers with
    # the identifier it is about to send.
    #
    # It is a method rather than a flag so that the identifier is minted
    # ONCE, by whoever will actually put it on the wire, instead of being
    # guessed here and hoped to match.
    VENUE_HAS_NO_CLIENT_IDENTIFIER = (
        "polymarket-us retail: CreateOrderParams has no client-supplied "
        "order identifier, so an ambiguous response is reconciled against "
        "the pre-image of resting order ids and nothing else")

    def mint_client_id(self):
        return None

    # WHERE THIS ADAPTER ACTUALLY SENDS, read from the runtime it will use.
    #
    # The ticket's `venue` / `environment` / `account` are LABELS on an
    # approval. They say what a human meant; they do not say what the
    # process bound. A preprod-labelled ticket handed to an adapter
    # configured for production would pass every ledger check in this
    # codebase and still reach the wrong exchange.
    #
    # So the destination is asked of the adapter, not read off the ticket:
    # the module the submit function actually came from, and the account
    # the SAME credential the reads use is bound to. An adapter that cannot
    # answer is one whose destination is unestablished, and
    # `calibration_execute` refuses rather than assuming.
    RUNTIME_ENVIRONMENT = "PRODUCTION"
    RUNTIME_VENUE = "polymarket-us"

    def identity(self):
        from .calibration_evidence import account_identity
        who = account_identity()
        return {
            "venue": self.RUNTIME_VENUE,
            "environment": self.RUNTIME_ENVIRONMENT,
            "account": who.get("ACCOUNT"),
            "accountBlocker": who.get("BLOCKER"),
            # The module the write actually goes through, so a swapped
            # submit function is visible rather than implied.
            "submitModule": getattr(self._submit, "__module__", None),
            "source": "adapter runtime: credential-bound account identity "
                      "plus the submit function's own module",
        }

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
