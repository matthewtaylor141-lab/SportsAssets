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


# ── THE SIDE THAT ACTUALLY PAYS ON OUR OUTCOME ───────────────────────
#
# THE DEFECT THIS REPLACES (acceptance run 28). `ext_pinnacle_loop.
# venue_quote` took an `outcome_index` argument and NEVER USED IT: it
# returned BEST_ASK for the market however the caller asked. On the
# `aec-` family that is wrong for half of all candidates, because BOTH
# sides of one of those markets carry the SAME identifier -- equal to
# the slug -- and the side is carried only by the ORDER INTENT. Eleven
# of run 28's forty-one candidates resolved to a real venue contract
# whose intent was ORDER_INTENT_BUY_SHORT, and the loop refused every
# one of them rather than price a short leg off the long book.
#
# The refusal was right. The reader was one-sided.
#
# THE CONVERSION IS NOT NEW AND IS NOT GUESSED. `pmus.slug_bid` settled
# the quote shape against five live markets, exact to the cent on all
# five (2026-08-31, run 33395797987):
#
#     long.price  == bestAsk          (5/5)
#     short.price == 1 - bestBid      (5/5)
#
# and `live_executor.cost_per_share` is the one-line pure arithmetic
# already used for money: (1 - px) for a short intent, px for a long.
# This function is the LADDER form of exactly that, and it delegates the
# per-share step to that same definition so the two can never drift.
#
#     LONG  entry -> consume the OFFER ladder, cost = px
#     SHORT entry -> consume the BID  ladder, cost = 1 - px
#
# A short's quantity comes from the BID levels, not the offers. On a
# book showing bid 0.60 / ask 0.63 the short acquisition cost is 0.40
# taken against the BID's quantity -- never 0.37 (which is 1 - ask, a
# price nobody is offering) and never the offer quantity.

ACQ_VERSION = "BETTOR_ACQUISITION_LADDER_V1"

#: Kept apart on purpose. `api_price` is the YES-denominated number the
#: venue publishes and the one an order carries; `acquisition_price` is
#: what a contract costs us. For a long they coincide; for a short they
#: are different numbers in different spaces, and conflating them is the
#: mis-denomination `live_executor.wire_limit` exists to prevent.
PRICE_SPACES = (
    "api_price is the venue's YES-denominated price for the level. "
    "acquisition_price is our economic cost per contract. They are "
    "equal for a LONG and complementary for a SHORT")

R_SIDE_EMPTY = "REQUIRED_SIDE_HAS_NO_PUBLISHED_LEVEL"
R_NOT_ENOUGH_DEPTH = "DISPLAYED_DEPTH_BELOW_REQUESTED_SIZE"
R_INTENT_UNKNOWN = "ORDER_INTENT_NOT_RECOGNISED"


def is_short_intent(intent) -> bool:
    """True for the venue's SHORT intents.

    EXTRACTED, NOT IMPORTED. The identical predicate and the per-share
    conversion below live in `live_executor`, which also carries the
    funded order path. `ext_pinnacle_loop` is guarded by a test asserting
    that no order-submission module is even NAMEABLE from it, and that
    guard is worth more than the import: a shadow loop should not be able
    to reach a submit function transitively for the sake of one line of
    arithmetic.

    So the arithmetic is duplicated deliberately and
    `test_acquisition_ladder_is_side_aware` pins it EQUAL to the
    executor's own definition, so the two cannot drift silently.
    """
    return "SHORT" in (str(intent) or "").upper()


def cost_per_share(px: float, intent) -> float:
    """Our cost for ONE contract at venue price `px`, unrounded.

    A short is a book-level SELL: `price` denominates the contract and a
    short ties up (1 - price). Confirmed by the venue's own receipts
    (ORDER_SIDE_SELL on 6 of 6) and by the quote shape measured across
    five live markets, exact to the cent: short.price == 1 - bestBid.
    """
    v = float(px or 0)
    return (1.0 - v) if is_short_intent(intent) else v


def acquisition_ladder(market_data, *, intent, limit=None) -> dict:
    """The executable ladder for ONE resolved intent, in cost space.

    `intent` is the RESOLVED exposure from `premap.resolve` -- the thing
    that actually names the side on this venue. It is required: there is
    no default, because defaulting is what produced the one-sided read.

    `limit` bounds how many levels are read. It defaults to ALL of them.
    LADDER_LEVELS (5) is the snapshot's display truncation and must not
    be inherited silently by a calculation that needs real depth.

    Returns levels ordered best-first in ACQUISITION terms (cheapest
    first), each carrying both prices so neither space is lost.
    """
    out = {"version": ACQ_VERSION, "intent": intent,
           "price_spaces": PRICE_SPACES, "levels": [], "reasons": []}
    if not intent or not isinstance(intent, str):
        return {**out, "ok": False, "refusal": R_INTENT_UNKNOWN,
                "parse_status": NOT_IDENTIFIED}
    short = is_short_intent(intent)
    out["side_consumed"] = SIDE_BID if short else SIDE_ASK
    out["pays_on"] = ("THE_COMPLEMENT_OF_THE_PRICED_OUTCOME" if short
                      else "THE_PRICED_OUTCOME")

    if market_data is None:
        return {**out, "ok": False, "parse_status": PARSE_NO_MARKET_DATA}
    if not isinstance(market_data, dict):
        return {**out, "ok": False,
                "parse_status": PARSE_MARKET_DATA_NOT_OBJECT}

    raw = market_data.get("bids") if short else market_data.get("offers")
    if raw is None:
        # THE SIDE IS ABSENT FROM THE PAYLOAD -- different from a side
        # that is present and empty, which is a real, readable book with
        # nobody on it.
        return {**out, "ok": False, "parse_status": PARSE_NO_LEVELS,
                "refusal": R_SIDE_EMPTY,
                "why": "the payload publishes no %s array at all"
                       % ("bids" if short else "offers")}
    if not isinstance(raw, list):
        return {**out, "ok": False,
                "parse_status": PARSE_MARKET_DATA_NOT_OBJECT,
                "why": "the side is present but is not a list"}

    n = len(raw) if limit is None else min(len(raw), int(limit))
    out["levels_published"] = len(raw)
    out["levels_read"] = n
    out["truncated"] = n < len(raw)
    levels, reasons = [], []
    for i in range(n):
        px, qty, why = _level(raw[i])
        for w in why:
            if w not in reasons:
                reasons.append(w)
        if px is None or qty is None:
            continue
        if qty <= 0:
            if R_NO_SIZE_AT_PRICE not in reasons:
                reasons.append(R_NO_SIZE_AT_PRICE)
            continue
        acq = cost_per_share(float(px), intent)
        levels.append({"level": i,
                       "api_price": float(px),
                       "acquisition_price": round(float(acq), 6),
                       "qty": float(qty)})
    # Cheapest acquisition first. For a long the offers already ascend;
    # for a short the best bid gives the LOWEST cost, so sorting on the
    # acquisition price is the one rule that is correct for both.
    levels.sort(key=lambda r: r["acquisition_price"])
    out["levels"] = levels
    out["reasons"] = reasons
    if not levels:
        # A VALID EMPTY BOOK AND A MALFORMED ONE ARE DIFFERENT FACTS.
        malformed = bool(reasons) and len(raw) > 0
        return {**out, "ok": False,
                "parse_status": (PARSE_OK if not malformed
                                 else PARSE_NO_LEVELS),
                "refusal": R_SIDE_EMPTY,
                "book_was": ("MALFORMED_LEVELS" if malformed
                             else "VALID_BUT_EMPTY")}
    out.update(ok=True, parse_status=PARSE_OK,
               best_acquisition_price=levels[0]["acquisition_price"],
               best_api_price=levels[0]["api_price"],
               displayed_depth=round(sum(r["qty"] for r in levels), 6))
    return out


def fill_across_levels(ladder: dict, size: float) -> dict:
    """Walk `size` contracts down an acquisition ladder.

    Returns the size actually available, the notional cost and the
    volume-weighted acquisition price. A ladder that cannot cover `size`
    is NOT silently partially filled into a confident number: the
    shortfall is named and `covers_requested` is false.
    """
    want = float(size or 0)
    out = {"requested": want, "filled": 0.0, "cost": 0.0,
           "levels_used": 0, "covers_requested": False}
    if not ladder.get("ok") or want <= 0:
        return {**out, "refusal": ladder.get("refusal") or R_SIDE_EMPTY}
    filled = cost = 0.0
    used = 0
    for lv in ladder["levels"]:
        if filled >= want:
            break
        take = min(lv["qty"], want - filled)
        filled += take
        cost += take * lv["acquisition_price"]
        used += 1
    out.update(filled=round(filled, 6), cost=round(cost, 6),
               levels_used=used,
               covers_requested=filled + 1e-9 >= want)
    if filled > 0:
        out["vwap_acquisition_price"] = round(cost / filled, 6)
    if not out["covers_requested"]:
        out["refusal"] = R_NOT_ENOUGH_DEPTH
        out["why"] = ("the displayed ladder shows %.6f of the %.6f "
                      "requested" % (filled, want))
    return out


def fill_to_notional(ladder: dict, budget_usd, max_price=None) -> dict:
    """Walk an acquisition ladder to a DOLLAR budget, not a quantity.

    WHY BOTH WALKS EXIST. `fill_across_levels` answers "we want N
    contracts -- can the book supply them?". The frozen sizing policy asks
    the other question: "we intend to spend $1,000 -- how many contracts
    is that?" Deriving a quantity from the budget at one assumed price and
    then walking it conflates the two, and it produced a real absurdity:
    filling the whole intended quantity cheaply reported UNFILLED
    NOTIONAL, because fewer dollars had been spent for exactly the
    contracts asked for.

    `max_price` is a limit in ACQUISITION (cost) space -- levels priced
    above it are not taken at all, however much budget is left. The
    entry lane's limit is its break-even price, so a level beyond it is
    not worth buying rather than merely expensive.

    `total_inside_limit` is what the whole ladder would support inside the
    limit, which is the number the sizing policy needs to tell a budget
    that was fully spent from a book that ran out.
    """
    want = float(budget_usd or 0)
    cap = None if max_price is None else float(max_price)
    out = {"budget_usd": want, "filled": 0.0, "cost": 0.0,
           "levels_used": 0, "covers_budget": False,
           "total_inside_limit": 0.0, "max_price": cap}
    if not ladder.get("ok") or want <= 0:
        return {**out, "refusal": ladder.get("refusal") or R_SIDE_EMPTY}

    filled = cost = total = 0.0
    used = 0
    # THE LEVELS ACTUALLY CONSUMED, PRICE BY PRICE. A walk that reports
    # only a VWAP cannot be checked against the book it walked, and the
    # ledger needs the per-level prices: an order filled across .62/.64/.66
    # did not fill three times at the average.
    taken = []
    for lv in ladder["levels"]:
        px = float(lv["acquisition_price"])
        if cap is not None and px > cap:
            break
        qty = float(lv["qty"])
        total += qty * px
        if cost >= want:
            continue
        # PARTIAL LEVELS ARE ALLOWED, WHOLE CONTRACTS ARE NOT ASSUMED.
        # Taking a fraction of a level is ordinary; rounding the quantity
        # up to spend the last cent would invent liquidity.
        afford = (want - cost) / px
        take = min(qty, afford)
        if take <= 0:
            continue
        filled += take
        cost += take * px
        used += 1
        taken.append({"price": round(px, 6), "qty": round(take, 6),
                      "cost": round(take * px, 6),
                      "level_qty_displayed": round(qty, 6)})
    out["levels_taken"] = taken

    out.update(filled=round(filled, 6), cost=round(cost, 6),
               levels_used=used, total_inside_limit=round(total, 6),
               covers_budget=cost + 1e-9 >= want)
    if filled > 0:
        out["vwap_acquisition_price"] = round(cost / filled, 6)
    else:
        out["refusal"] = R_NOT_ENOUGH_DEPTH
        out["why"] = ("no level is at or inside the limit %s"
                      % ("none" if cap is None else "%.6f" % cap))
        return out
    if not out["covers_budget"]:
        out["why"] = ("the ladder supports $%.2f of the $%.2f intended "
                      "inside the limit" % (cost, want))
    return out


# ── EXITING IS NOT ACQUIRING, AND THE TWO PRICES ARE NOT THE SAME ────
#
# THE DEFECT THIS EXISTS FOR, found by independent inspection of
# deployed commit 5b19bc5 and reproduced exactly.
#
# `challenger_inputs_for` needed an exit price for a held position. It
# asked `acquisition_ladder` for the OPPOSITE intent and passed that
# ladder's `acquisition_price` straight through as `bid`. On a book of
# YES bid .60 / ask .63 that supplied:
#
#     held LONG   bid .4000   complement_ask .6300
#     held SHORT  bid .6300   complement_ask .4000
#
# Both numbers are wrong in both cases. .40 is what it COSTS to acquire
# the short leg; it is not what we RECEIVE for selling the long leg,
# which is the raw bid .60. And .63 is the cost of acquiring MORE long
# exposure -- adding to the position -- not the cost of the complement.
#
# THE CORRECT PAIR, and the relation that generates both:
#
#     exit_proceeds        = 1 - acquisition_price(OPPOSITE intent)
#     complement_cost      =     acquisition_price(OPPOSITE intent)
#
#     held LONG   exit .6000 (the raw bid)      complement .4000
#     held SHORT  exit .3700 (= 1 - the ask)    complement .6300
#
# BOTH COME OFF THE SAME LADDER, and that is not a coincidence: on a
# venue holding ONE signed netPosition, selling our side and buying the
# other side are the SAME ORDER consuming the SAME side of the book.
# Their economics therefore agree exactly -- receiving q is the same as
# paying (1 - q) to neutralise -- and any builder that hands the ranking
# two different prices for them has invented a spread that does not
# exist and will pick a winner on it.
#
# QUANTITY COMES FROM THE SIDE THAT PAYS. A held long exits into the
# BIDS, so the size is bid size. `acquisition_ladder(SHORT)` already
# reads the bids and preserves their quantity, which is exactly why the
# opposite-intent ladder is the right source for both numbers.

EXIT_VERSION = "BETTOR_EXIT_LADDER_V1"

R_NO_EXIT_SIDE = "NO_EXIT_SIDE_PUBLISHED"

EXIT_RELATION = (
    "exit_proceeds = 1 - acquisition_price(opposite intent); "
    "complement_cost = acquisition_price(opposite intent). Both read off "
    "the OPPOSITE side's ladder, because on a one-signed-net venue "
    "selling our side and buying the other side are the same order")


def opposite_intent(intent) -> str:
    """The intent that CLOSES the exposure `intent` opened."""
    return ("ORDER_INTENT_BUY_LONG" if is_short_intent(intent)
            else "ORDER_INTENT_BUY_SHORT")


def exit_ladder(market_data, *, held_intent, limit=None) -> dict:
    """What CLOSING a position held under `held_intent` actually pays.

    Returns levels carrying BOTH prices for the same executable size:

        exit_price       what we receive per contract for closing
        complement_price what it costs per contract to neutralise
                         (they sum to 1.00 by construction)

    Levels are ordered BEST EXIT FIRST -- the highest proceeds -- which
    is the reverse of the acquisition ladder's cheapest-first order, and
    is what a sizing walk against a hold hurdle needs.
    """
    opp = opposite_intent(held_intent)
    acq = acquisition_ladder(market_data, intent=opp, limit=limit)
    out = {
        "version": EXIT_VERSION,
        "held_intent": str(held_intent),
        "closing_intent": opp,
        "relation": EXIT_RELATION,
        "side_consumed": acq.get("side_consumed"),
        "levels_published": acq.get("levels_published"),
        "levels_read": acq.get("levels_read"),
        "truncated": acq.get("truncated"),
        "parse_status": acq.get("parse_status"),
        "reasons": acq.get("reasons"),
        "acquisition_ladder_used": opp,
    }
    if not acq.get("ok"):
        return {**out, "ok": False,
                "refusal": acq.get("refusal") or R_NO_EXIT_SIDE,
                "book_was": acq.get("book_was"),
                "why": acq.get("why") or (
                    "the side a close would consume publishes no "
                    "executable level")}
    levels = []
    for lv in acq["levels"]:
        comp = float(lv["acquisition_price"])
        levels.append({
            "level": lv["level"],
            "api_price": lv["api_price"],
            # THE TWO NUMBERS, KEPT APART BY NAME so a caller cannot
            # reach for the wrong one the way the builder did.
            "exit_price": round(1.0 - comp, 6),
            "complement_price": round(comp, 6),
            "qty": lv["qty"],
        })
    # Best exit first. `acquisition_ladder` sorts cheapest-acquisition
    # first, and cheapest complement IS best exit, so this is already the
    # right order -- sorted explicitly rather than relied upon.
    levels.sort(key=lambda r: -r["exit_price"])
    out.update(
        ok=True, levels=levels,
        best_exit_price=levels[0]["exit_price"],
        best_complement_price=levels[0]["complement_price"],
        best_api_price=levels[0]["api_price"],
        size_at_best=levels[0]["qty"],
        displayed_depth=round(sum(r["qty"] for r in levels), 6),
        sums_to_one=True,
        note=("exit_price + complement_price = 1.00 at every level: "
              "receiving q and paying (1 - q) to neutralise are the same "
              "trade on this venue"))
    return out


def as_sale_ladder(exit_lad: dict) -> dict:
    """An exit ladder shaped for `marginal_sale_size`.

    That walker compares `acquisition_price` against a per-contract hold
    value, and what it must compare is the EXIT PROCEEDS. Handing it an
    acquisition ladder compares the wrong number; this adapts without
    teaching the walker a second field name.
    """
    if not (exit_lad or {}).get("ok"):
        return {"levels": [], "refusal": (exit_lad or {}).get("refusal")}
    return {"levels": [{"level": lv["level"],
                        "acquisition_price": lv["exit_price"],
                        "exit_price": lv["exit_price"],
                        "qty": lv["qty"]} for lv in exit_lad["levels"]],
            "price_space": "EXIT_PROCEEDS_PER_CONTRACT",
            "why": EXIT_RELATION}
