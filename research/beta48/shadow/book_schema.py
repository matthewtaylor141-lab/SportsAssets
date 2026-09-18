#!/usr/bin/env python3
"""THE VENUE'S NATIVE BOOK SHAPE, read in one place.

WHY THIS MODULE EXISTS.

The production response to GET /v1/markets/{slug}/book is

    {"marketData": {"bids": [...], "offers": [...], "state": ..., "stats": ...}}

Two readers in this codebase disagreed about that. `eligibility.book_bbo` read
it correctly. `substantive_select.two_sided` and `throughput_v1._two_sided`
read `body["bids"]` and `body["asks"]` -- a shape the venue has never sent.
Measured against the 15 retained BLOCK_4 book bodies, all 15 are genuinely
two-sided and both of those readers accepted zero. Every candidate was refused
BOOK_NOT_TWO_SIDED, no event ever reached MARKETS_PER_EVENT, and the run
reported "insufficient qualifying events" as though that described the venue.

So the shape is defined ONCE, here, and both readers route through it.

WHAT THIS DOES NOT DO.

It does not weaken anything. The requirement is unchanged and unchanged in
strength: a book needs a bid AND an offer. Missing data, a malformed container,
a non-list side and a one-sided book are all still refused, and each is
distinguishable by `reason()`. No price, spread, quantity, recency, identity,
ranking or risk threshold is introduced here -- this module answers one
question, "did the venue send both sides", and every downstream check runs
afterwards exactly as before.

THE PRODUCTION CONTRACT IS NOT NEGOTIABLE BY A TEST.

The old mock shape (top-level bids/asks) is NOT accepted. Accepting both would
let a test fixture that does not resemble the venue keep passing, which is the
precise reason this defect survived so long: the rehearsal fed
{"bids": [1], "asks": [1]} and the venue-shaped truth never met the code. A
test that wants a normalized representation must build it through
`native_book()`, which is named, explicit, and produces the production shape.
"""
from __future__ import annotations

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# The venue's own field names. `offers`, not `asks`.
CONTAINER_KEY = "marketData"
BID_KEY = "bids"
ASK_KEY = "offers"

NATIVE_BOOK_PATH = "%s.%s / %s.%s" % (CONTAINER_KEY, BID_KEY,
                                      CONTAINER_KEY, ASK_KEY)

# Why a body was refused. Each is a different fact about the response.
NO_BODY = "NO_BOOK_BODY"
NOT_A_MAPPING = "BOOK_BODY_NOT_A_MAPPING"
NO_CONTAINER = "BOOK_BODY_HAS_NO_MARKETDATA"
CONTAINER_NOT_A_MAPPING = "MARKETDATA_NOT_A_MAPPING"
SIDE_NOT_A_LIST = "BOOK_SIDE_NOT_A_LIST"
NO_BIDS = "BOOK_HAS_NO_BIDS"
NO_ASKS = "BOOK_HAS_NO_OFFERS"
ONE_SIDED = "BOOK_ONE_SIDED"
TWO_SIDED = "BOOK_TWO_SIDED"

REFUSAL_REASONS = (NO_BODY, NOT_A_MAPPING, NO_CONTAINER,
                   CONTAINER_NOT_A_MAPPING, SIDE_NOT_A_LIST,
                   NO_BIDS, NO_ASKS, ONE_SIDED)

LEGACY_TOP_LEVEL_SHAPE_IS_NOT_ACCEPTED = True


def levels(book_body):
    """(bids, offers, reason). Lists only when the venue really sent lists."""
    if book_body is None:
        return None, None, NO_BODY
    if not isinstance(book_body, dict):
        return None, None, NOT_A_MAPPING
    if CONTAINER_KEY not in book_body:
        return None, None, NO_CONTAINER
    md = book_body.get(CONTAINER_KEY)
    if not isinstance(md, dict):
        return None, None, CONTAINER_NOT_A_MAPPING
    bids, asks = md.get(BID_KEY), md.get(ASK_KEY)
    if (bids is not None and not isinstance(bids, list)) or \
            (asks is not None and not isinstance(asks, list)):
        return None, None, SIDE_NOT_A_LIST
    return (bids or []), (asks or []), None


def reason(book_body):
    """TWO_SIDED, or the specific reason it is not. Never a bare False."""
    bids, asks, why = levels(book_body)
    if why:
        return why
    if not bids and not asks:
        return NO_BIDS if not bids else NO_ASKS
    if not bids:
        return NO_BIDS
    if not asks:
        return NO_ASKS
    return TWO_SIDED


def two_sided(book_body):
    """A book with a bid AND an offer, in the venue's own shape."""
    return reason(book_body) == TWO_SIDED


def native_book(bids, offers, state="MARKET_STATE_OPEN", **extra):
    """Build a book in the PRODUCTION shape. For fixtures and tests.

    Named so that a normalized test representation is visible as one. It emits
    the production contract rather than standing in for it, so a test built
    with this cannot pass against a reader that expects some other shape.
    """
    md = {BID_KEY: list(bids or []), ASK_KEY: list(offers or []),
          "state": state}
    md.update(extra)
    return {CONTAINER_KEY: md}
