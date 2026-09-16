#!/usr/bin/env python3
"""COUNTERFACTUAL FILL MODEL V2. Pure computation. Contacts nothing.

Joins captured book snapshots to the venue's PUBLIC execution tape and asks, of
a quote BETTOR never submitted, what evidence would support its having filled.

THE ONE SENTENCE THAT GOVERNS THIS FILE. The order was not there. It never sat
in the queue, never displaced anyone, and nobody traded against it. So there is
no value of any input for which this file writes BETTOR_FILLED; the strongest
output it can produce is COUNTERFACTUAL_FILL_SUPPORTED, and the distinction is
enforced by the absence of the other name rather than by a comment.

WHAT THE TAPE IS, from our own captured page (docs.polymarket.us/faqs/
execution-tape.md, sha256 5ff446a6...):

    Transaction Time | Symbol | Last Price | Last Quantity

and explicitly NO side, NO aggressor flag, NO buyer or seller. A tape without
an aggressor cannot say which side of the book a print consumed. That single
absence is why F1 has to be conservative rather than exact, and it is the
reason DEPTH_CONSUMPTION_SIDE stays NOT_IDENTIFIED no matter how much tape we
accumulate.

THE FOUR MODELS, frozen here BEFORE the tape is read, so the hierarchy cannot
be tuned to whichever produced the nicer number:

  F0 PESSIMISTIC   only prints that are unambiguously ours to count: the trade
                   price equals our quote price, and the traded volume exceeds
                   the queue ahead PLUS our whole quote. Cancellations ahead of
                   us are credited at zero, because a cancellation is not an
                   execution and the tape cannot see one.
  F1 CONSERVATIVE  executed volume AT our price counted against the queue
                   ahead, cancellations still credited at zero.
  F2 MODERATE      F1 plus observed depth disappearance ahead of us, when two
                   book snapshots bracket the interval and the shrink is not
                   already explained by tape volume. This is the only model
                   that credits a cancellation, and it does so from a
                   difference of two observations rather than an assumption.
  F3 TOUCH         the price traded at or through our level at all. An upper
                   bound on anything, and NEVER a fill.

F0 <= F1 <= F2 <= F3 is an invariant, asserted in the tests.

THE MATCHING RULE THIS RESTS ON is the venue's documented price-time priority:
better price first, and at the same price earlier entry first. The Rulebook
also permits a different algorithm for a particular contract after advance
notice, so `MATCHING_ALGORITHM_EXCEPTION_POSSIBLE` is YES and
`CHECK_PRODUCT_SPECIFIC_MATCHING_NOTICE` is REQUIRED before anything here is
relied on in production.
"""
from __future__ import annotations

from decimal import Decimal as D

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# --- what the public tape is, and is not -----------------------------------
PUBLIC_TIME_SALES_AVAILABLE = "YES"
AUTH_REQUIRED = "NO"
EXECUTION_TIMESTAMP = "YES"
EXECUTION_PRICE = "YES"
EXECUTION_QUANTITY = "YES"
SYMBOL = "YES"
TRADE_SIDE = "NO"
AGGRESSOR = "NO"
PARTICIPANT_IDENTITY = "NO"

# These do not move because the tape arrived. They move when BETTOR submits a
# real passive order, and at no other time.
ACTUAL_BETTOR_FILL = NOT_IDENTIFIED
ACTUAL_BETTOR_FILL_PROBABILITY = NOT_IDENTIFIED
TRADE_AGGRESSOR = NOT_IDENTIFIED
ACTUAL_BETTOR_QUEUE_POSITION = NOT_IDENTIFIED

# --- the matching rule ------------------------------------------------------
CURRENT_MATCHING_PRIORITY = "PRICE_TIME"
BETTER_PRICE_PRIORITY = "YES"
SAME_PRICE_TIME_PRIORITY = "YES"
MATCHING_ALGORITHM_EXCEPTION_POSSIBLE = "YES"
CONTRACT_SPECIFIC_ALGORITHM_OVERRIDE_POSSIBLE = "YES"
CHECK_PRODUCT_SPECIFIC_MATCHING_NOTICE = "REQUIRED"
PRODUCT_NOTICE_CHECK_REQUIRED_BEFORE_LIVE = "YES"

MODELS = ("F0_PESSIMISTIC", "F1_CONSERVATIVE", "F2_MODERATE",
          "F3_TOUCH_UPPER_BOUND")


# --- BLOCK TRADES: the tape is not a CLOB tape --------------------------
#
# Block trades execute APART FROM the public order book and do not touch
# orders resting in it -- and they are reported into Time & Sales anyway. A
# block print therefore depletes nothing: a hypothetical maker sitting in the
# queue is exactly as far from the front after a block as before it.
#
# So TIME_SALES_VOLUME != ORDER_BOOK_DEPLETION, and every model below counts
# only executions classified CLOB. This is the difference between a maker who
# would have filled and one who watched a $500,000 block print past them while
# their quote sat untouched.
#
# WHAT WE HAVE VERIFIED OURSELVES, from the 340-page capture: block trades
# exist (FIX ExecInst 'j', "SINGLE EXECUTION REQUESTED FOR BLOCK TRADE") and
# the Daily Market Report carries a separate `Block Volume` column beside
# `Trade Volume`. That is enough to establish the contamination risk.
#
# WHAT WE HAVE NOT VERIFIED: that blocks appear in Time & Sales, and any
# row-level flag distinguishing them. No Block Trade Data page appears in
# llms.txt or in any captured page. The conservative consequence is the same
# either way -- an unflagged tape's volume is an UPPER BOUND on CLOB volume,
# never equal to it.
BLOCK_TRADE_EXCLUSION_REQUIRED = "YES"
TIME_SALES_QUEUE_DEPLETION_VALID_ONLY_FOR_CLOB_EXECUTIONS = "YES"
BLOCK_TRADES_EXIST = "VERIFIED_FROM_CAPTURE"
BLOCKS_APPEAR_IN_TIME_SALES = "RELAYED_NOT_CAPTURED"
BLOCK_TRADE_DATA_PAGE_CAPTURED = "NO"
BLOCK_TRADE_DATA_PUBLIC = NOT_IDENTIFIED
BLOCK_TRADE_TIMESTAMP_AVAILABLE = NOT_IDENTIFIED
BLOCK_TRADE_SYMBOL_AVAILABLE = NOT_IDENTIFIED
BLOCK_TRADE_PRICE_AVAILABLE = NOT_IDENTIFIED
BLOCK_TRADE_QUANTITY_AVAILABLE = NOT_IDENTIFIED
ROW_LEVEL_MATCH_TO_TIME_SALES_POSSIBLE = NOT_IDENTIFIED

CLOB_EXECUTION = "CLOB_EXECUTION"
BLOCK_EXECUTION = "BLOCK_EXECUTION"
UNKNOWN_EXECUTION_TYPE = "UNKNOWN_EXECUTION_TYPE"

# Whether the Daily Market Report's `Trade Volume` INCLUDES or EXCLUDES
# `Block Volume` decides the reconciliation arithmetic, and the captured page
# does not say. Guessing would make a reconciliation that always passes or one
# that always fails, and both look like evidence.
DMR_TRADE_VOLUME_INCLUDES_BLOCK_VOLUME = NOT_IDENTIFIED


def _d(x):
    return x if isinstance(x, D) else D(str(x))


def classify_execution(row, block_index=None):
    """CLOB / BLOCK / UNKNOWN for one tape print.

    `block_index` is a set of (symbol, time, price, qty) keys built from the
    venue's own block publication, when such a publication turns out to exist
    and to be row-matchable. A print found there is a BLOCK.

    A row carrying an explicit venue flag is believed. Everything else is
    UNKNOWN -- NOT CLOB. The tape as documented has four columns and no trade
    type, so "unflagged" means "we do not know", and the one thing that must
    never happen is UNKNOWN being counted as CLOB by default: that is how the
    whole contamination correction would be silently undone.
    """
    flag = row.get("execution_type") or row.get("trade_type")
    if flag in (CLOB_EXECUTION, BLOCK_EXECUTION, UNKNOWN_EXECUTION_TYPE):
        return flag
    if isinstance(flag, str) and flag.strip().lower() == "block":
        return BLOCK_EXECUTION
    if block_index is not None:
        key = (row.get("symbol"), row.get("time"),
               str(row.get("price")), str(row.get("qty")))
        if key in block_index:
            return BLOCK_EXECUTION
        # The index EXISTS and this row is not in it. That is positive
        # evidence of a CLOB execution, and it is the only route to CLOB.
        return CLOB_EXECUTION
    return UNKNOWN_EXECUTION_TYPE


class Quote:
    """A maker quote BETTOR did not place. Named hypothetical throughout."""

    def __init__(self, insert_time, symbol, side, price, quote_size,
                 queue_ahead_at_insert):
        if str(side).upper() not in ("BID", "ASK"):
            raise ValueError("side is BID or ASK; got %r" % (side,))
        self.HYPOTHETICAL_INSERT_TIME = insert_time
        self.SYMBOL = symbol
        self.SIDE = str(side).upper()
        self.PRICE = _d(price)
        self.QUOTE_SIZE = _d(quote_size)
        self.QUEUE_AHEAD_AT_INSERT = _d(queue_ahead_at_insert)


def prints_after(tape, symbol, t0, t1=None):
    """Tape rows for one symbol in (t0, t1], in time order.

    A row is {"time":, "symbol":, "price":, "qty":}. Rows for other symbols are
    dropped rather than tolerated: a tape joined on the wrong key is worse than
    no tape, because it looks like evidence.
    """
    out = [r for r in tape
           if r.get("symbol") == symbol and r.get("time") > t0
           and (t1 is None or r.get("time") <= t1)]
    return sorted(out, key=lambda r: r["time"])


def _through(side, print_px, quote_px):
    """Did a print at `print_px` trade THROUGH our resting level?

    A resting BID is passed over when trading happens BELOW it; a resting ASK
    when trading happens ABOVE it. Trading through us is strong evidence the
    book moved past our price -- and it is still not evidence that WE filled,
    because the volume that went through may have been at prices we were not
    quoting.
    """
    return print_px < quote_px if side == "BID" else print_px > quote_px


def observe(quote, tape, t1=None, depth_removed_ahead=None,
            depth_added_ahead=None, block_index=None):
    """The observable quantities, before any model interprets them.

    CLOB volume is separated from block and unknown volume here, at the point
    of measurement, so no downstream model has to remember to do it.
    """
    rows = prints_after(tape, quote.SYMBOL, quote.HYPOTHETICAL_INSERT_TIME, t1)
    at = through = D("0")
    at_block = at_unknown = D("0")
    n_clob = n_block = n_unknown = 0
    first_at_time = None
    for r in rows:
        px, qty = _d(r["price"]), _d(r["qty"])
        kind = classify_execution(r, block_index)
        n_clob += kind == CLOB_EXECUTION
        n_block += kind == BLOCK_EXECUTION
        n_unknown += kind == UNKNOWN_EXECUTION_TYPE
        if px == quote.PRICE:
            if kind == CLOB_EXECUTION:
                at += qty
                if first_at_time is None:
                    first_at_time = r["time"]
            elif kind == BLOCK_EXECUTION:
                at_block += qty
            else:
                at_unknown += qty
        elif _through(quote.SIDE, px, quote.PRICE):
            # F3 is a TOUCH bound, not a depletion claim, so it may count any
            # print that moved the price past us.
            through += qty
    return {
        "SYMBOL": quote.SYMBOL,
        "SIDE": quote.SIDE,
        "PRICE": quote.PRICE,
        "QUOTE_SIZE": quote.QUOTE_SIZE,
        "QUEUE_AHEAD_AT_INSERT": quote.QUEUE_AHEAD_AT_INSERT,
        # CLOB ONLY. A block print depletes no queue, so it is not in here.
        "TRADED_VOLUME_AT_PRICE": at,
        "CLOB_VOLUME_AT_PRICE": at,
        "BLOCK_VOLUME_AT_PRICE": at_block,
        "UNKNOWN_TYPE_VOLUME_AT_PRICE": at_unknown,
        "EXECUTIONS_CLOB": n_clob,
        "EXECUTIONS_BLOCK": n_block,
        "EXECUTIONS_UNKNOWN_TYPE": n_unknown,
        "TRADED_VOLUME_THROUGH_PRICE": through,
        # A shrink at our level that the tape does not explain. Only F2 uses
        # it, and only when both snapshots exist -- absent snapshots give None,
        # never zero, because "we did not look" and "nothing happened" are
        # different facts.
        "VISIBLE_DEPTH_REMOVED_AHEAD": (None if depth_removed_ahead is None
                                        else _d(depth_removed_ahead)),
        "VISIBLE_DEPTH_ADDED_AHEAD": (None if depth_added_ahead is None
                                      else _d(depth_added_ahead)),
        "FIRST_PRINT_AT_PRICE_TIME": first_at_time,
        "PRINTS_CONSIDERED": len(rows),
        # Structural, not a gap in this dataset: the tape has no side.
        "DEPTH_CONSUMPTION_SIDE": NOT_IDENTIFIED,
    }


def evaluate(quote, tape, t1=None, depth_removed_ahead=None,
             depth_added_ahead=None, block_index=None):
    """The four models over one hypothetical quote.

    Returns COUNTERFACTUAL_FILL_SUPPORTED_* and never a fill.
    """
    o = observe(quote, tape, t1, depth_removed_ahead, depth_added_ahead,
                block_index)
    at = o["TRADED_VOLUME_AT_PRICE"]
    ahead = quote.QUEUE_AHEAD_AT_INSERT
    # Size joining the queue AHEAD of us cannot exist under price-time
    # priority -- a later order at the same price is behind us. `added_ahead`
    # is therefore only meaningful at BETTER prices, and is treated as
    # increasing the work required, never decreasing it.
    added = o["VISIBLE_DEPTH_ADDED_AHEAD"] or D("0")
    removed = o["VISIBLE_DEPTH_REMOVED_AHEAD"]

    # THE PRECONDITION EVERY MODEL SHARES, stated once and applied to all
    # three rather than left to emerge from each inequality. A counterfactual
    # fill requires that somebody actually executed AGAINST THE BOOK at our
    # price after our hypothetical order was entered. Cancellation can advance
    # a queue position; it cannot create a fill. Nor can a block, which never
    # touched the book at all.
    clob_after_entry = at > 0

    # F0: the whole queue ahead AND our entire quote must have been traded
    # through at our price. Cancellations credited at zero.
    f0 = clob_after_entry and at >= (ahead + added + quote.QUOTE_SIZE)
    # F1: executed volume at our price exceeds the queue ahead. The first
    # contract of ours would have traded. Cancellations still credited at zero.
    f1 = clob_after_entry and at > (ahead + added)
    # F2: F1, or the queue ahead was cleared by execution plus an observed,
    # tape-unexplained disappearance -- AND somebody then traded at our price.
    #
    # THE `at > 0` TERM IS NOT DEFENSIVE PADDING. Without it this model credits
    # a fill to a level where nothing traded at all: a queue that empties by
    # cancellation moves us to the FRONT of the queue, which is not the same
    # event as being filled, and a maker at the front of an untouched queue has
    # exactly zero contracts. The first version of this file had that bug and
    # the monotonicity sweep caught it -- F2 fired while F3, its own touch
    # upper bound, was NO.
    #
    # `removed is None` means we did not observe the interval, so F2 collapses
    # to F1: absence of observation never becomes evidence.
    f2 = f1 or (removed is not None and at > 0
                and (at + removed) > (ahead + added))
    # F3: the price traded at or through our level at all. An upper bound.
    f3 = (at > 0) or (o["TRADED_VOLUME_THROUGH_PRICE"] > 0)

    # Monotonicity is a property of the definitions above, and is re-checked
    # here so a future edit that breaks it fails loudly rather than quietly
    # producing an F0 above its own upper bound.
    if not (f3 >= f2 >= f1 >= f0):
        raise AssertionError("model hierarchy violated: %r" % [f0, f1, f2, f3])

    out = dict(o)
    out.update({
        "COUNTERFACTUAL_FILL_SUPPORTED_F0": "YES" if f0 else "NO",
        "COUNTERFACTUAL_FILL_SUPPORTED_F1": "YES" if f1 else "NO",
        "COUNTERFACTUAL_FILL_SUPPORTED_F2": "YES" if f2 else "NO",
        "F3_TOUCH_ONLY": "YES" if f3 else "NO",
        "TIME_TO_QUEUE_DEPLETION": (o["FIRST_PRINT_AT_PRICE_TIME"]
                                    if f1 else NOT_IDENTIFIED),
        # Carried on every row so the reading of the row cannot drift from it.
        "ACTUAL_BETTOR_FILL": NOT_IDENTIFIED,
        "ACTUAL_BETTOR_FILL_PROBABILITY": NOT_IDENTIFIED,
        "TRADE_AGGRESSOR": NOT_IDENTIFIED,
        "ACTUAL_BETTOR_QUEUE_POSITION": NOT_IDENTIFIED,
    })
    return out


def symbol_day_gate(tape_qty, dmr_trade_volume=None, dmr_block_volume=None,
                    row_level_blocks_excluded=False, tolerance="0"):
    """Is one (Symbol, Business Date) usable for queue inference at all?

    THE GATE, not a caveat. A symbol-day whose Daily Market Report shows block
    volume, and whose block rows we cannot identify individually, has a tape
    whose volume is an UPPER BOUND on CLOB volume. Using it as queue depletion
    would credit counterfactual fills to makers who were never touched, and the
    error is worst exactly where it matters most -- the large, illiquid,
    block-traded markets where a maker's queue position is most valuable.

    So the verdict is USABLE / CONTAMINATED / UNRECONCILED, and the two failing
    verdicts block the symbol-day rather than annotating it.

    The reconciliation itself is deliberately NOT computed when
    DMR_TRADE_VOLUME_INCLUDES_BLOCK_VOLUME is unknown, which it is: the
    captured page lists `Trade Volume` ("Total traded volume for the day") and
    `Block Volume` ("Volume from block trades") as separate columns without
    saying whether the first contains the second. Picking one reading would
    produce a check that always passes or one that always fails, and both look
    like evidence.
    """
    out = {
        "TAPE_TOTAL_QTY": _d(tape_qty),
        "DMR_TRADE_VOLUME": (None if dmr_trade_volume is None
                             else _d(dmr_trade_volume)),
        "DMR_BLOCK_VOLUME": (None if dmr_block_volume is None
                             else _d(dmr_block_volume)),
        "DMR_TRADE_VOLUME_INCLUDES_BLOCK_VOLUME":
            DMR_TRADE_VOLUME_INCLUDES_BLOCK_VOLUME,
        "ROW_LEVEL_BLOCKS_EXCLUDED": bool(row_level_blocks_excluded),
    }
    if dmr_trade_volume is None:
        out["RECONCILIATION"] = NOT_IDENTIFIED
        out["QUEUE_DEPLETION_CONTAMINATED_BY_BLOCKS"] = NOT_IDENTIFIED
        out["SYMBOL_DAY_USABLE_FOR_QUEUE_INFERENCE"] = "NO"
        out["VERDICT"] = "UNRECONCILED_NO_DMR_ROW"
        return out

    # Only the block-free case has an unambiguous expected total, so only it
    # can be reconciled while the inclusive/exclusive question is open.
    blk = out["DMR_BLOCK_VOLUME"]
    if blk is not None and blk > 0:
        if not row_level_blocks_excluded:
            out["RECONCILIATION"] = NOT_IDENTIFIED
            out["QUEUE_DEPLETION_CONTAMINATED_BY_BLOCKS"] = "YES"
            out["SYMBOL_DAY_USABLE_FOR_QUEUE_INFERENCE"] = "NO"
            out["VERDICT"] = "CONTAMINATED_BY_BLOCKS"
            return out
        out["QUEUE_DEPLETION_CONTAMINATED_BY_BLOCKS"] = "NO"
    else:
        out["QUEUE_DEPLETION_CONTAMINATED_BY_BLOCKS"] = "NO"

    delta = abs(out["TAPE_TOTAL_QTY"] - out["DMR_TRADE_VOLUME"])
    ok = delta <= _d(tolerance)
    out["RECONCILIATION_DELTA"] = delta
    out["RECONCILIATION"] = "PASS" if ok else "FAIL"
    out["SYMBOL_DAY_USABLE_FOR_QUEUE_INFERENCE"] = "YES" if ok else "NO"
    out["VERDICT"] = "USABLE" if ok else "UNRECONCILED_VOLUME_MISMATCH"
    return out


def reconciliation_report(gates):
    """The six rates, over a set of symbol-day gate results."""
    n = len(gates)
    if not n:
        return {k: NOT_IDENTIFIED for k in (
            "TAPE_DMR_JOIN_RATE", "TAPE_VOLUME_RECONCILIATION_RATE",
            "SYMBOLS_WITH_BLOCK_VOLUME", "BLOCK_VOLUME_SHARE",
            "ROW_LEVEL_BLOCK_EXCLUSION_RATE", "QUEUE_USABLE_SYMBOL_DAYS")}
    joined = [g for g in gates if g.get("DMR_TRADE_VOLUME") is not None]
    blocky = [g for g in gates
              if g.get("DMR_BLOCK_VOLUME") not in (None,)
              and g["DMR_BLOCK_VOLUME"] > 0]
    blk_total = sum((g["DMR_BLOCK_VOLUME"] for g in blocky), D("0"))
    trade_total = sum((g["DMR_TRADE_VOLUME"] for g in joined), D("0"))
    return {
        "SYMBOL_DAYS": n,
        "TAPE_DMR_JOIN_RATE": "%d/%d" % (len(joined), n),
        "TAPE_VOLUME_RECONCILIATION_RATE":
            "%d/%d" % (sum(1 for g in gates
                           if g.get("RECONCILIATION") == "PASS"), n),
        "SYMBOLS_WITH_BLOCK_VOLUME": len(blocky),
        "BLOCK_VOLUME_SHARE": (str(blk_total / trade_total)
                               if trade_total > 0 else NOT_IDENTIFIED),
        "ROW_LEVEL_BLOCK_EXCLUSION_RATE":
            "%d/%d" % (sum(1 for g in blocky
                           if g.get("ROW_LEVEL_BLOCKS_EXCLUDED")), len(blocky))
            if blocky else "0/0",
        "QUEUE_USABLE_SYMBOL_DAYS":
            sum(1 for g in gates
                if g.get("SYMBOL_DAY_USABLE_FOR_QUEUE_INFERENCE") == "YES"),
    }


# No counterfactual fill result may be promoted to a finding until a
# symbol-day's gate says USABLE. Recorded as a flag so the promotion step has
# something to check rather than a paragraph to remember.
COUNTERFACTUAL_RESULTS_PROMOTABLE = False
PROMOTION_REQUIRES = ("SYMBOL_DAY_USABLE_FOR_QUEUE_INFERENCE == YES",
                      "BLOCK_CONTAMINATION_RESOLVED",
                      "TAPE_SYMBOL_JOINS_TO_MARKET_SLUG != NOT_IDENTIFIED")

MARKOUT_HORIZONS_S = (1, 5, 30, 60, 300)


def markout(reference_price, side, tape, symbol, t_fill, horizons_s=None,
            seconds_between=None):
    """Signed mark-to-tape after a counterfactual maker fill.

    POSITIVE means the print moved in the maker's favour. A maker who bought
    gains when later prints are higher; a maker who sold gains when they are
    lower -- so the ASK side is negated, and getting that sign backwards would
    turn adverse selection into edge.

    A horizon with no print is NOT_IDENTIFIED, never zero. Zero says the price
    did not move; NOT_IDENTIFIED says nobody traded, and at 1.7% of 30-second
    intervals carrying any trade at all, that difference is most of the data.

    `seconds_between(a, b)` lets the caller supply the clock, because the tape
    page documents a "Transaction Time" WITHOUT stating its precision. A
    1-second markout on a 1-second-resolution timestamp is not measurable, and
    this file will not pretend otherwise -- see MARKOUT_1S_FEASIBLE below.
    """
    horizons = tuple(horizons_s or MARKOUT_HORIZONS_S)
    if seconds_between is None:
        def seconds_between(a, b):
            return float(b - a)
    later = prints_after(tape, symbol, t_fill)
    ref = _d(reference_price)
    sign = D("1") if str(side).upper() == "BID" else D("-1")
    out = {}
    for h in horizons:
        px = None
        for r in later:
            if seconds_between(t_fill, r["time"]) <= h:
                px = _d(r["price"])
            else:
                break
        out["MARKOUT_%dS" % h] = (NOT_IDENTIFIED if px is None
                                  else sign * (px - ref))
    return out


# The tape's timestamp precision is not stated on the captured page, so whether
# a one-second markout is even measurable is an open question about the DATA,
# not about this code. It is answered by looking at a real file, not by
# choosing a horizon list.
MARKOUT_1S_FEASIBLE = NOT_IDENTIFIED
TAPE_TIMESTAMP_PRECISION = NOT_IDENTIFIED


def split_markouts(any_trade_rows, counterfactual_fill_rows):
    """The two populations, kept apart because only one measures our exposure.

    MARKOUT_AFTER_ANY_TRADE is a property of the market. MARKOUT_AFTER
    _COUNTERFACTUAL_MAKER_FILL is a property of the subset where a passive
    quote at our price would have been hit -- which is selected precisely on
    somebody having wanted to trade against it. Pooling them averages the
    adverse selection away, which is the one thing the study exists to find.
    """
    return {
        "MARKOUT_AFTER_ANY_TRADE": list(any_trade_rows),
        "MARKOUT_AFTER_COUNTERFACTUAL_MAKER_FILL":
            list(counterfactual_fill_rows),
        "THESE_ARE_NEVER_POOLED": True,
    }


# --- the maker economic reconstruction -------------------------------------
ECONOMIC_TERMS = (
    "GROSS_FAIR_VALUE_EDGE", "SPREAD_CAPTURE", "MAKER_REBATE",
    "LIQUIDITY_INCENTIVE", "FILL_INCENTIVE", "OTHER_VERIFIED_INCENTIVE",
    "ADVERSE_SELECTION", "RESIDUAL_INVENTORY_COST", "EXIT_COST",
)


def reconstruct(**terms):
    """Sum the maker terms, and refuse to sum an unmeasured one.

    TRADING_NET_EX_INCENTIVES is reported before and separately from
    TOTAL_NET_INCL_INCENTIVES, and a strategy that is negative on the first and
    positive on the second is labelled INCENTIVE_DEPENDENT. That may be a real
    business. It is not a structural trading edge, and the two must not share a
    word.
    """
    unknown = [k for k in ECONOMIC_TERMS
               if terms.get(k, NOT_IDENTIFIED) == NOT_IDENTIFIED]
    row = {k: terms.get(k, NOT_IDENTIFIED) for k in ECONOMIC_TERMS}
    row["UNMEASURED_TERMS"] = unknown
    if unknown:
        row["TRADING_NET_EX_INCENTIVES"] = NOT_IDENTIFIED
        row["TOTAL_NET_INCL_INCENTIVES"] = NOT_IDENTIFIED
        row["TOTAL_COUNTERFACTUAL_NET"] = NOT_IDENTIFIED
        row["EDGE_CLASSIFICATION"] = NOT_IDENTIFIED
        return row

    trading = (_d(terms["GROSS_FAIR_VALUE_EDGE"])
               + _d(terms["SPREAD_CAPTURE"])
               + _d(terms["MAKER_REBATE"])
               - _d(terms["ADVERSE_SELECTION"])
               - _d(terms["RESIDUAL_INVENTORY_COST"])
               - _d(terms["EXIT_COST"]))
    incentives = (_d(terms["LIQUIDITY_INCENTIVE"])
                  + _d(terms["FILL_INCENTIVE"])
                  + _d(terms["OTHER_VERIFIED_INCENTIVE"]))
    total = trading + incentives
    row["TRADING_NET_EX_INCENTIVES"] = trading
    row["INCENTIVE_CONTRIBUTION"] = incentives
    row["TOTAL_NET_INCL_INCENTIVES"] = total
    row["TOTAL_COUNTERFACTUAL_NET"] = total
    if trading < 0 and total > 0:
        row["EDGE_CLASSIFICATION"] = "INCENTIVE_DEPENDENT"
    elif trading > 0:
        row["EDGE_CLASSIFICATION"] = "STRUCTURAL_TRADING_EDGE"
    else:
        row["EDGE_CLASSIFICATION"] = "NO_EDGE"
    # Whatever the classification, the numbers above it are counterfactual.
    row["BASIS"] = "COUNTERFACTUAL_MAKER_FILL_NOT_ACTUAL_FILL"
    return row
