"""§A. THE DISPLAYED BOOK, CAPTURED LOSSLESSLY AND NAMED HONESTLY.

Owner directive, "GO on the narrow next step" §A:

    "Preserve the distinction between DISPLAYED_DEPTH_AT_T0 and
    QUEUE_AHEAD_AT_T0. Do not automatically equate total displayed
    quantity at the price with queue ahead... Do not infer
    cancellations or queue evolution from later snapshots. Persist the
    raw ladder or sufficient lossless source representation so we can
    audit derived values later... Do not manufacture zero for absent
    quantity."

WHY THIS MODULE EXISTS. `pmus.bbo_read` calls `client.markets.book`,
receives a payload carrying `bids[].px/qty`, `offers[].px/qty` and
`stats.sharesTraded`, and returns four keys: bid, ask, state, error.
Everything else is discarded. The BETTOR worker then writes
`available_depth=None`, and `INSUFFICIENT_DEPTH` has fired on 984 of
1,972 decisions as a result. The venue was never the constraint.

`bbo_read` IS NOT TOUCHED. The mirror lane depends on its exact
behaviour and the mirror is frozen. This module reads the same payload
separately and keeps everything.

────────────────────────────────────────────────────────────────────
DISPLAYED_DEPTH_AT_T0 IS NOT QUEUE_AHEAD_AT_T0.

    DISPLAYED_DEPTH_AT_T0    what the venue showed resting at a price.
                             An observation.
    QUEUE_AHEAD_AT_T0        how much would sit in front of a BETTOR
                             order at that price. A COUNTERFACTUAL
                             about an order that does not exist.

They coincide only under assumptions, and the assumptions are listed on
every row rather than implied by the arithmetic:

    JOINS_THE_BACK_OF_THE_QUEUE   a new order at an existing price
                                  enters behind everything already
                                  resting there. This is price-time
                                  priority, which the venue documents
                                  and which its Rulebook permits it to
                                  override per contract after notice.
    DISPLAYED_IS_ALL_THERE_IS     hidden or reserve size at the level
                                  is not displayed and would sit ahead
                                  of us undetected. HIDDEN_LIQUIDITY_
                                  STATUS is NOT_IDENTIFIED, so this is
                                  assumed, not observed.
    NO_RACE                       nothing arrives between the snapshot
                                  and the hypothetical insertion. The
                                  order is hypothetical, so there is no
                                  insertion instant to measure against.
    SNAPSHOT_IS_THE_INSERTION_INSTANT  the book did not move between
                                  the read and T0.

Under all four, displayed size at our price is a conservative
queue-ahead SNAPSHOT. Under none of them is it a bound on queue-ahead
later in the resting interval -- that claim was retracted, and
QUEUE_AHEAD_DYNAMIC_STATUS stays NOT_OBSERVED here because this module
looks at one snapshot and never at two.

A PRICE WE WOULD IMPROVE ON IS DIFFERENT. Quoting inside the touch
rests at a price nobody is displaying, so queue-ahead is genuinely
zero -- the one place a zero is a measurement rather than a default.
That case is marked PRICE_IMPROVEMENT_NOTHING_DISPLAYED.
────────────────────────────────────────────────────────────────────

ABSENT IS NOT ZERO. A level the venue did not publish, a `qty` that
did not parse, a `sharesTraded` the payload omitted -- each is
NOT_IDENTIFIED with a named reason. A zero quantity says the venue
showed nothing resting there, which is a different fact and a much
stronger one.

THE RAW LADDER IS KEPT. `RAW_SOURCE` carries the venue's own bids and
offers arrays as received, so every derived number on the row can be
recomputed and audited later. A derived value whose source is gone
cannot be checked when the derivation changes.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER. It is a read.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

NOT_IDENTIFIED = "NOT_IDENTIFIED"

SNAPSHOT_VERSION = "BETTOR_BOOK_SNAPSHOT_V1"

LADDER_LEVELS = 5

SIDE_BID = "BID"
SIDE_ASK = "ASK"

# ── the two names, kept apart ────────────────────────────────────────

DISPLAYED_DEPTH_IS_NOT_QUEUE_AHEAD = (
    "DISPLAYED_DEPTH_AT_T0 is what the venue showed resting at a price: "
    "an observation. QUEUE_AHEAD_AT_T0 is how much would sit in front "
    "of an order that does not exist: a counterfactual. They coincide "
    "only under stated assumptions, and never beyond the snapshot "
    "instant")

QUEUE_AHEAD_ASSUMPTIONS = (
    ("JOINS_THE_BACK_OF_THE_QUEUE",
     "a new order at an existing price enters behind everything already "
     "resting there. Price-time priority is documented by the venue and "
     "its Rulebook permits a per-contract override after notice, so "
     "CHECK_PRODUCT_SPECIFIC_MATCHING_NOTICE remains REQUIRED"),
    ("DISPLAYED_IS_ALL_THERE_IS",
     "hidden or reserve size at the level would sit ahead of us "
     "undetected. HIDDEN_LIQUIDITY_STATUS is NOT_IDENTIFIED, so this is "
     "assumed rather than observed"),
    ("NO_RACE",
     "nothing arrives between the snapshot and the hypothetical "
     "insertion. The order is hypothetical, so there is no insertion "
     "instant to measure this against"),
    ("SNAPSHOT_IS_THE_INSERTION_INSTANT",
     "the book did not move between the read and T0"),
)

QUEUE_AHEAD_DERIVED_FROM_DISPLAYED = "DISPLAYED_SIZE_AT_OUR_PRICE_AT_SNAPSHOT"
QUEUE_AHEAD_PRICE_IMPROVEMENT = "PRICE_IMPROVEMENT_NOTHING_DISPLAYED"
QUEUE_AHEAD_NOT_DERIVABLE = "NOT_DERIVABLE_NO_DISPLAYED_SIZE_AT_THIS_PRICE"

# One snapshot cannot see a process. Stated on every row so nothing
# downstream reads a single observation as queue evolution.
QUEUE_AHEAD_DYNAMIC_STATUS = "NOT_OBSERVED"
NO_EVOLUTION_FROM_ONE_SNAPSHOT = (
    "this module reads ONE snapshot. Cancellations, additions and "
    "depletion are properties of a sequence and are not inferred here "
    "from a later snapshot either -- a shrink between two reads cannot "
    "be separated into trades and cancellations, and QUEUE_DEPLETION_"
    "FROM_CANCELLATIONS stays NOT_IDENTIFIED")

ABSENT_IS_NOT_ZERO = (
    "a level the venue did not publish, a quantity that did not parse "
    "and an omitted sharesTraded are each NOT_IDENTIFIED with a reason. "
    "A zero says the venue showed nothing resting there, which is a "
    "different and much stronger fact")

# ── machine-readable parse outcomes ──────────────────────────────────

PARSE_OK = "PARSED"
PARSE_NO_MARKET_DATA = "NO_MARKET_DATA_IN_PAYLOAD"
PARSE_MARKET_DATA_NOT_OBJECT = "MARKET_DATA_NOT_AN_OBJECT"
PARSE_NO_LEVELS = "NO_BOOK_LEVELS_PUBLISHED"

R_LEVEL_ABSENT = "LEVEL_NOT_PUBLISHED_BY_VENUE"
R_QTY_UNPARSEABLE = "QUANTITY_DID_NOT_PARSE_AS_A_DECIMAL"
R_PX_UNPARSEABLE = "PRICE_DID_NOT_PARSE_AS_A_DECIMAL"
R_SHARES_TRADED_ABSENT = "STATS_SHARES_TRADED_NOT_IN_PAYLOAD"
R_NO_SIZE_AT_PRICE = "NO_DISPLAYED_SIZE_AT_THIS_PRICE"

MISSING_FIELD_REASONS = (
    R_LEVEL_ABSENT, R_QTY_UNPARSEABLE, R_PX_UNPARSEABLE,
    R_SHARES_TRADED_ABSENT, R_NO_SIZE_AT_PRICE,
)


def _d(v):
    """Exact decimal or None. Floats are accepted via str, never coerced
    silently from a dict."""
    if v is None or v == "" or v == NOT_IDENTIFIED:
        return None
    if isinstance(v, dict):
        v = v.get("value")
        if v is None:
            return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _level(entry):
    """One ladder level as (price, qty, reasons), each named if absent.

    The venue publishes a level as {"px": {"value": "0.52"}, "qty": "25"}.
    Both shapes -- nested value and bare scalar -- are read, and a field
    that does not parse is NOT_IDENTIFIED with its own reason rather
    than dropped or zeroed.
    """
    if not isinstance(entry, dict):
        return None, None, [R_LEVEL_ABSENT]
    reasons = []
    px = _d(entry.get("px"))
    if px is None:
        px = _d(entry.get("price"))
    qty = _d(entry.get("qty"))
    if qty is None:
        qty = _d(entry.get("size"))
    if px is None:
        reasons.append(R_PX_UNPARSEABLE)
    if qty is None:
        reasons.append(R_QTY_UNPARSEABLE)
    return px, qty, reasons


def _ladder(levels, limit=LADDER_LEVELS):
    """The top-N levels, with per-level reasons preserved."""
    out, reasons = [], []
    for i, e in enumerate(levels or []):
        if i >= limit:
            break
        px, qty, why = _level(e)
        out.append({
            "level": i,
            "price": str(px) if px is not None else NOT_IDENTIFIED,
            "qty": str(qty) if qty is not None else NOT_IDENTIFIED,
        })
        for w in why:
            if w not in reasons:
                reasons.append(w)
    return out, reasons


def displayed_size_at_price(ladder, price):
    """Displayed size at exactly this price, or NOT_IDENTIFIED.

    An exact decimal comparison. A price the venue is not displaying
    returns NOT_IDENTIFIED with a reason -- never 0, which would say
    the venue showed an empty level there.
    """
    p = _d(price)
    if p is None:
        return None, R_PX_UNPARSEABLE
    for lvl in ladder or []:
        lp = _d(lvl.get("price"))
        if lp is not None and lp == p:
            q = _d(lvl.get("qty"))
            if q is None:
                return None, R_QTY_UNPARSEABLE
            return q, None
    return None, R_NO_SIZE_AT_PRICE


def snapshot(market_data, *, symbol=None, captured_at=None,
             receipt_at=None, feed=None, request_at=None) -> dict:
    """One lossless book observation. Every §A field, absences named.

    `market_data` is the venue's `marketData` object exactly as
    received. It is kept verbatim on RAW_SOURCE so every derived value
    here can be recomputed and audited later.
    """
    base = {
        "snapshotVersion": SNAPSHOT_VERSION,
        "SYMBOL": symbol or NOT_IDENTIFIED,
        "CAPTURED_AT": captured_at or NOT_IDENTIFIED,
        "RECEIPT_AT": receipt_at or captured_at or NOT_IDENTIFIED,
        "REQUEST_AT": request_at or NOT_IDENTIFIED,
        "RAW_SOURCE_PROVENANCE": {
            "feed": feed or NOT_IDENTIFIED,
            "field": "marketData",
            "reader": "bettor_book_snapshot.snapshot",
            "note": ("the venue's own marketData object, kept verbatim "
                     "so every derived value on this row can be "
                     "recomputed"),
        },
        "displayedDepthIsNotQueueAhead": DISPLAYED_DEPTH_IS_NOT_QUEUE_AHEAD,
        "absentIsNotZero": ABSENT_IS_NOT_ZERO,
        "QUEUE_AHEAD_DYNAMIC_STATUS": QUEUE_AHEAD_DYNAMIC_STATUS,
        "noEvolutionFromOneSnapshot": NO_EVOLUTION_FROM_ONE_SNAPSHOT,
        "missingFieldReasonsDeclared": list(MISSING_FIELD_REASONS),
    }
    if market_data is None:
        return {**base, "PARSE_STATUS": PARSE_NO_MARKET_DATA,
                "RAW_SOURCE": None, "MISSING_FIELD_REASONS": []}
    if not isinstance(market_data, dict):
        return {**base, "PARSE_STATUS": PARSE_MARKET_DATA_NOT_OBJECT,
                "RAW_SOURCE": None, "MISSING_FIELD_REASONS": [],
                "rawType": type(market_data).__name__}

    bids = market_data.get("bids") or []
    offers = market_data.get("offers")
    if offers is None:
        offers = market_data.get("asks") or []

    bid_ladder, bid_why = _ladder(bids)
    ask_ladder, ask_why = _ladder(offers)
    reasons = list(dict.fromkeys(bid_why + ask_why))

    best_bid = _d(bid_ladder[0]["price"]) if bid_ladder else None
    best_ask = _d(ask_ladder[0]["price"]) if ask_ladder else None
    spread = (best_ask - best_bid
              if best_bid is not None and best_ask is not None else None)

    stats = market_data.get("stats")
    stats = stats if isinstance(stats, dict) else {}
    shares = _d(stats.get("sharesTraded"))
    if shares is None:
        reasons.append(R_SHARES_TRADED_ABSENT)

    out = {
        **base,
        "PARSE_STATUS": (PARSE_OK if (bid_ladder or ask_ladder)
                         else PARSE_NO_LEVELS),
        "BEST_BID": str(best_bid) if best_bid is not None
                    else NOT_IDENTIFIED,
        "BEST_ASK": str(best_ask) if best_ask is not None
                    else NOT_IDENTIFIED,
        "SPREAD": str(spread) if spread is not None else NOT_IDENTIFIED,
        "BID_LADDER": bid_ladder,
        "ASK_LADDER": ask_ladder,
        "DEPTH_LEVELS_BID": len(bids) if isinstance(bids, list) else 0,
        "DEPTH_LEVELS_ASK": len(offers) if isinstance(offers, list) else 0,
        # DISPLAYED depth: the sum of what the venue showed on each side
        # within the captured ladder. An OBSERVATION, and explicitly not
        # a queue.
        "DISPLAYED_DEPTH_AT_T0": {
            "bid": _sum_qty(bid_ladder),
            "ask": _sum_qty(ask_ladder),
            "levelsCaptured": LADDER_LEVELS,
            "isNotQueueAhead": DISPLAYED_DEPTH_IS_NOT_QUEUE_AHEAD,
        },
        "STATS_SHARES_TRADED": (str(shares) if shares is not None
                                else NOT_IDENTIFIED),
        "sharesTradedIsCumulative": (
            "a running total for the market, not a per-print tape and "
            "not queue evolution. Its DIFFERENCE between two reads is "
            "market-wide volume, never volume at our price"),
        "VENUE_STATE": market_data.get("state", NOT_IDENTIFIED),
        "TRANSACT_TIME": market_data.get("transactTime", NOT_IDENTIFIED),
        "LAST_TRADE_PRICE": (str(_d((stats.get("lastTradePx") or {})))
                             if _d(stats.get("lastTradePx")) is not None
                             else NOT_IDENTIFIED),
        "LAST_TRADE_SET_TIME": stats.get("lastTradeSetTime", NOT_IDENTIFIED),
        "MISSING_FIELD_REASONS": reasons,
        # THE LOSSLESS SOURCE. Everything above is recomputable from it.
        "RAW_SOURCE": market_data,
    }
    return out


def _sum_qty(ladder):
    """Sum of displayed quantity across captured levels, or NOT_IDENTIFIED.

    Refuses to sum when ANY captured level's quantity did not parse: a
    partial sum presented as a total is a smaller number that looks
    like a measurement.
    """
    if not ladder:
        return NOT_IDENTIFIED
    total = Decimal("0")
    for lvl in ladder:
        q = _d(lvl.get("qty"))
        if q is None:
            return NOT_IDENTIFIED
        total += q
    return str(total)


def queue_ahead_at_t0(snap: dict, *, side, price) -> dict:
    """The counterfactual queue in front of an order we would not send.

    Returns the snapshot value AND the assumptions it rests on. Never a
    zero by default: a price the venue is not displaying yields
    NOT_DERIVABLE, and only a deliberate price improvement yields a
    measured zero.
    """
    out = {
        "SIDE": side,
        "HYPOTHETICAL_PRICE": str(_d(price)) if _d(price) is not None
                              else NOT_IDENTIFIED,
        "QUEUE_AHEAD_DYNAMIC_STATUS": QUEUE_AHEAD_DYNAMIC_STATUS,
        "displayedDepthIsNotQueueAhead": DISPLAYED_DEPTH_IS_NOT_QUEUE_AHEAD,
        "noEvolutionFromOneSnapshot": NO_EVOLUTION_FROM_ONE_SNAPSHOT,
        "assumptions": [{"name": n, "why": w}
                        for n, w in QUEUE_AHEAD_ASSUMPTIONS],
        "assumptionsAreAssumed": (
            "every one of these is assumed, not observed. The value "
            "below is a conservative snapshot only if all four hold, "
            "and is not a bound on queue-ahead at any later instant"),
    }
    if side not in (SIDE_BID, SIDE_ASK):
        out.update({"QUEUE_AHEAD_AT_T0": NOT_IDENTIFIED,
                    "DERIVATION": NOT_IDENTIFIED,
                    "why": "side must be BID or ASK"})
        return out

    ladder = snap.get("BID_LADDER" if side == SIDE_BID else "ASK_LADDER")
    qty, why = displayed_size_at_price(ladder, price)
    if qty is not None:
        out.update({
            "QUEUE_AHEAD_AT_T0": str(qty),
            "DISPLAYED_SIZE_AT_OUR_PRICE": str(qty),
            "DERIVATION": QUEUE_AHEAD_DERIVED_FROM_DISPLAYED,
        })
        return out

    # THE ONE PLACE A ZERO IS A MEASUREMENT. Quoting inside the touch
    # rests at a price nobody is displaying, so nothing is ahead of us
    # there -- observed, not defaulted.
    p, best = _d(price), _d(snap.get("BEST_BID" if side == SIDE_BID
                                     else "BEST_ASK"))
    improves = (best is not None and p is not None
                and (p > best if side == SIDE_BID else p < best))
    if improves:
        out.update({
            "QUEUE_AHEAD_AT_T0": "0",
            "DERIVATION": QUEUE_AHEAD_PRICE_IMPROVEMENT,
            "whyZeroIsMeasuredHere": (
                "this price is inside the touch and nobody is quoting "
                "it, so the venue showing nothing there IS the "
                "measurement. It also gives up the only leverage a "
                "snapshot has over the fill question"),
        })
        return out

    out.update({
        "QUEUE_AHEAD_AT_T0": NOT_IDENTIFIED,
        "DERIVATION": QUEUE_AHEAD_NOT_DERIVABLE,
        "MISSING_FIELD_REASON": why or R_NO_SIZE_AT_PRICE,
        "whyNotZero": ABSENT_IS_NOT_ZERO,
    })
    return out


def describe() -> dict:
    return {
        "snapshotVersion": SNAPSHOT_VERSION,
        "ladderLevels": LADDER_LEVELS,
        "displayedDepthIsNotQueueAhead": DISPLAYED_DEPTH_IS_NOT_QUEUE_AHEAD,
        "queueAheadAssumptions": [{"name": n, "why": w}
                                  for n, w in QUEUE_AHEAD_ASSUMPTIONS],
        "queueAheadDynamicStatus": QUEUE_AHEAD_DYNAMIC_STATUS,
        "noEvolutionFromOneSnapshot": NO_EVOLUTION_FROM_ONE_SNAPSHOT,
        "absentIsNotZero": ABSENT_IS_NOT_ZERO,
        "missingFieldReasons": list(MISSING_FIELD_REASONS),
        "parseStatuses": [PARSE_OK, PARSE_NO_MARKET_DATA,
                          PARSE_MARKET_DATA_NOT_OBJECT, PARSE_NO_LEVELS],
        "bboReadIsNotTouched": (
            "pmus.bbo_read keeps its exact behaviour. The mirror lane "
            "depends on it and the mirror is frozen; this module reads "
            "the same payload separately and keeps everything"),
    }
