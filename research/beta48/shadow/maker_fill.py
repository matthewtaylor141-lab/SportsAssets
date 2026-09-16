#!/usr/bin/env python3
"""THE COUNTERFACTUAL MAKER-FILL MODEL, over the tick capture. Contacts nothing.

BETTOR PLACED NO ORDER. Every quote named here is one BETTOR DID NOT SUBMIT, so
there is no value of any input for which this file writes FILLED. The strongest
positive output is COUNTERFACTUAL_FILL_F0/F1/F2, and the distinction is enforced
by the absence of the other name rather than by a comment.

WHAT THE TICK CAPTURE CAN AND CANNOT SETTLE -- the whole point of this module.

A tick row carries BID/ASK, the top-5 ladders, the displayed size at each level,
and `SHARES_TRADED_DELTA`: the change in the market's CUMULATIVE traded volume
between two polls. It does NOT carry a per-print tape. So for a quote resting at
price p:

    volume traded AT p          NOT_IDENTIFIED  (no per-price attribution)
    which side consumed it      NOT_IDENTIFIED  (no aggressor flag anywhere)
    our queue position          NOT_IDENTIFIED  (we were never in the queue)

`fill_model_v2` needs volume-at-price to support F0/F1/F2, and every one of its
models shares the precondition `at > 0`. From ticks alone that precondition is
not identified, therefore NO POSITIVE FILL SUPPORT COMES OUT OF TICK DATA. A
positive F0/F1/F2 here is produced by exactly one route -- `with_tape()`, which
delegates to `fill_model_v2.evaluate` over a real execution tape and reads its
verdict rather than re-deriving one.

IDENTIFICATION IS ASYMMETRIC, AND THAT ASYMMETRY IS THE USEFUL RESULT.

What ticks CAN settle is the NEGATIVE, by an upper bound nobody can argue with.
Credit EVERY share the market traded after entry to our price AND to our side --
the most generous reading physically available, and one that is certainly too
generous:

    MAXIMAL_ATTRIBUTION = sum of SHARES_TRADED_DELTA over the window

If even that does not clear the queue displayed ahead of us, then the true
at-price volume does not either, and the model is REFUTED rather than unknown:

    F0 needs   at >= QUEUE_AHEAD + QUOTE_SIZE   -> refuted if MAX < that
    F1 needs   at >  QUEUE_AHEAD                -> refuted if MAX <= QUEUE_AHEAD
    F2 needs   at >  0  (plus depletion)        -> refuted only if MAX == 0
    F3 needs   at >  0  or through-volume > 0   -> refuted only if MAX == 0

MAXIMAL_ATTRIBUTION == 0 -- an interval in which the market traded nothing at
all -- refutes every model including the touch bound, and is expected to be the
common case in a book polled every few seconds.

THE CONSEQUENCE FOR QUOTE PLACEMENT, stated here because it is structural and
not a tuning knob: joining the back of a DEEP displayed queue is refutable from
aggregate volume alone, while improving the price to the front of the book
(QUEUE_AHEAD = 0) makes the fill question ENTIRELY tape-dependent. The cheaper
the queue, the less our own data can say about it.

QUEUE_AHEAD IS AN ESTIMATE AND IS LABELLED ONE. It is the DISPLAYED size at our
level at the moment of the hypothetical insert. Hidden size, iceberg refresh and
the orders that join between two polls are all unobserved, and each of them
makes the true queue LONGER, never shorter. So the estimate is a LOWER bound on
the work required -- which means the refutations above are conservative in the
right direction: a refutation computed against a too-short queue would still be
a refutation against the true one.

NOTHING HERE IS A FILL, AND A TOUCH IS NEVER PROMOTED TO ONE. `F3` is carried as
`TOUCH_ONLY`, it is an upper bound on everything, and no function in this file
maps it to a FILL_STATUS other than UNKNOWN.
"""
from __future__ import annotations

import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tape"))

from position_state import NOT_IDENTIFIED, COUNTERFACTUAL  # noqa: E402,F401

SHADOW_ONLY = True
ORDER_PATH_EXISTS = False
ACTUAL_BETTOR_FILL = NOT_IDENTIFIED
ACTUAL_BETTOR_QUEUE_POSITION = NOT_IDENTIFIED

SIDE_BID = "BID"
SIDE_ASK = "ASK"
SIDES = (SIDE_BID, SIDE_ASK)

# The four statuses section 8 names, and no fifth one.
NOT_FILLED = "NOT_FILLED"
COUNTERFACTUAL_FILL_F0 = "COUNTERFACTUAL_FILL_F0"
COUNTERFACTUAL_FILL_F1 = "COUNTERFACTUAL_FILL_F1"
COUNTERFACTUAL_FILL_F2 = "COUNTERFACTUAL_FILL_F2"
UNKNOWN = "UNKNOWN"

FILL_STATUSES = (NOT_FILLED, COUNTERFACTUAL_FILL_F0, COUNTERFACTUAL_FILL_F1,
                 COUNTERFACTUAL_FILL_F2, UNKNOWN)

# A touch is not one of them. Named here so the absence is deliberate.
TOUCH_IS_NOT_A_FILL_STATUS = True
FILL_STATUS_FOR_A_TOUCH = UNKNOWN

MODELS = ("F0", "F1", "F2")
STATUS_FOR_MODEL = {"F0": COUNTERFACTUAL_FILL_F0, "F1": COUNTERFACTUAL_FILL_F1,
                    "F2": COUNTERFACTUAL_FILL_F2}

# Where a positive verdict may come from. One entry, on purpose -- and that one
# entry has a precondition of its own: `fill_model_v2.classify_execution`
# reaches CLOB_EXECUTION only through a venue execution-type flag or a block
# index, and UNKNOWN_EXECUTION_TYPE is never counted as CLOB. So a tape with no
# block publication and no type column leaves at = 0 and supports NOTHING, no
# matter how much volume it shows. Captured here because it is the second gate
# between this programme and its first counterfactual fill, and it is easy to
# mistake for a bug in the join.
POSITIVE_FILL_SUPPORT_REQUIRES = "EXECUTION_TAPE_JOIN"
TAPE_JOIN_ALSO_REQUIRES = "BLOCK_INDEX_OR_VENUE_EXECUTION_TYPE_FLAG"
UNKNOWN_EXECUTION_TYPE_IS_NOT_A_CLOB_EXECUTION = True
TICKS_ALONE_SUPPORT_POSITIVE_FILL = False
TICKS_ALONE_SUPPORT_REFUTATION = True

QUEUE_AHEAD_BASIS = "DISPLAYED_SIZE_AT_OUR_LEVEL_AT_INSERT"
QUEUE_AHEAD_IS_A_LOWER_BOUND = True
HIDDEN_LIQUIDITY = NOT_IDENTIFIED
AGGRESSOR_SIDE = NOT_IDENTIFIED
PER_PRICE_ATTRIBUTION_FROM_TICKS = NOT_IDENTIFIED


class TouchPromotion(RuntimeError):
    """Raised when a caller tries to read a touch as a fill."""


def _d(v):
    """Exact decimal, or NOT_IDENTIFIED. Floats refused, never coerced."""
    if v is None or (isinstance(v, str) and v == NOT_IDENTIFIED):
        return NOT_IDENTIFIED
    if isinstance(v, float):
        raise TypeError(
            "refusing a float book quantity: pass a Decimal or an exact "
            "decimal string. float(0.1) is not 0.1.")
    if isinstance(v, D):
        return v
    return D(str(v))


def _mid(tick):
    b, a = _d(tick.get("BID")), _d(tick.get("ASK"))
    if b == NOT_IDENTIFIED or a == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    return (b + a) / D("2")


def _level_qty(tick, side, price):
    """Displayed size at `price` on `side`, from the captured ladder.

    A price we cannot find in the ladder returns NOT_IDENTIFIED rather than
    zero: the capture keeps five levels, and "deeper than we looked" is not
    "empty".
    """
    ladder = tick.get("BID_LADDER" if side == SIDE_BID else "ASK_LADDER") or []
    for px, qty in ladder:
        if _d(px) == price:
            return _d(qty)
    return NOT_IDENTIFIED


# ---------------------------------------------------------------------------
# THE HYPOTHETICAL QUOTE -- section 8's fields, named as section 8 names them
# ---------------------------------------------------------------------------

def hypothetical_quote(tick, side, quote_size, price=None, improve_ticks=None,
                       tick_size="0.01"):
    """A quote BETTOR DID NOT PLACE, priced off a captured tick.

    `price=None` rests AT the touch on `side`, joining the back of whatever is
    displayed there. `improve_ticks` prices INSIDE it instead, which sets
    QUEUE_AHEAD_ESTIMATE to zero -- and, per the module docstring, removes the
    only leverage the tick capture has over the fill question.
    """
    if side not in SIDES:
        raise ValueError("side is BID or ASK; got %r" % (side,))
    if price is not None and improve_ticks:
        raise ValueError("give a price or an improvement, not both")

    touch = _d(tick.get("BID" if side == SIDE_BID else "ASK"))
    if price is not None:
        px = _d(price)
        ahead = _level_qty(tick, side, px)
    elif improve_ticks:
        if touch == NOT_IDENTIFIED:
            px, ahead = NOT_IDENTIFIED, NOT_IDENTIFIED
        else:
            step = _d(tick_size) * D(str(int(improve_ticks)))
            px = touch + step if side == SIDE_BID else touch - step
            # Nothing is displayed at a price nobody is quoting. This is the
            # one place QUEUE_AHEAD is genuinely zero rather than estimated.
            ahead = D("0")
    else:
        px = touch
        ahead = _d(tick.get("BID_QTY" if side == SIDE_BID else "ASK_QTY"))

    return {
        "HYPOTHETICAL": True,
        "PROVENANCE": "COUNTERFACTUAL_MAKER_FILL",
        "SLUG": tick.get("slug"),
        "SIDE": side,
        "QUOTE_TIME": tick.get("RECEIPT_UTC"),
        "QUOTE_ELAPSED_S": tick.get("ELAPSED_S"),
        "QUOTE_PRICE": px,
        "QUOTE_SIZE": _d(quote_size),
        "QUEUE_AHEAD_ESTIMATE": ahead,
        "QUEUE_AHEAD_BASIS": (("PRICE_IMPROVEMENT_NOTHING_DISPLAYED"
                               if improve_ticks else QUEUE_AHEAD_BASIS)),
        "QUEUE_AHEAD_IS_A_LOWER_BOUND": not bool(improve_ticks),
        "PRICE_IMPROVED": bool(improve_ticks),
        "BOOK_STATE_AT_ENTRY": {
            "BID": _d(tick.get("BID")), "ASK": _d(tick.get("ASK")),
            "BID_QTY": _d(tick.get("BID_QTY")), "ASK_QTY": _d(tick.get("ASK_QTY")),
            "SPREAD": _d(tick.get("SPREAD")), "MID": _mid(tick),
            "DEPTH_LEVELS_BID": tick.get("DEPTH_LEVELS_BID"),
            "DEPTH_LEVELS_ASK": tick.get("DEPTH_LEVELS_ASK"),
            "STATE": tick.get("STATE"),
            "SHARES_TRADED": tick.get("SHARES_TRADED"),
            "SEQ": tick.get("seq"),
        },
        "ACTUAL_BETTOR_QUEUE_POSITION": NOT_IDENTIFIED,
        "ORDER_WAS_SUBMITTED": False,
    }


# ---------------------------------------------------------------------------
# WHAT HAPPENED AFTER THE HYPOTHETICAL INSERT
# ---------------------------------------------------------------------------

def walk_after_entry(quote, ticks, horizon_s=None):
    """TRADE_FLOW_AFTER_ENTRY and PRICE_MOVEMENT_AFTER_ENTRY, measured.

    `ticks` are the rows for the SAME slug, in sequence, strictly after the row
    the quote was priced from. Rows for another slug are dropped rather than
    tolerated: a book joined on the wrong key looks like evidence.

    ANY unreadable SHARES_TRADED_DELTA in the window makes the traded total
    NOT_IDENTIFIED for the whole window. It does not skip the row and it does
    not treat the gap as zero -- a window with a hole in it cannot support an
    upper bound, and the upper bound is the only thing this walk is for.
    """
    t0 = quote.get("QUOTE_ELAPSED_S")
    rows = [r for r in ticks
            if r.get("slug") == quote.get("SLUG")
            and r.get("ELAPSED_S") is not None and t0 is not None
            and r["ELAPSED_S"] > t0
            and (horizon_s is None or r["ELAPSED_S"] - t0 <= horizon_s)]
    rows.sort(key=lambda r: r["ELAPSED_S"])

    traded = D("0")
    traded_ok = True
    errors = 0
    px = quote.get("QUOTE_PRICE")
    side = quote["SIDE"]
    mae = mfe = NOT_IDENTIFIED
    last_mid = NOT_IDENTIFIED
    touched = crossed = False
    level_persisted_s = D("0")
    depth_removed_ahead = D("0")
    depth_ok = True
    prev_ahead = quote.get("QUEUE_AHEAD_ESTIMATE")

    for r in rows:
        if r.get("kind") == "TICK_ERROR":
            errors += 1
            traded_ok = depth_ok = False
            continue
        dlt = r.get("SHARES_TRADED_DELTA")
        if dlt is None or dlt == NOT_IDENTIFIED:
            traded_ok = False
        else:
            traded += _d(dlt)

        m = _mid(r)
        if m != NOT_IDENTIFIED and px != NOT_IDENTIFIED:
            last_mid = m
            # Signed for the side we would be holding after a fill: a filled
            # BID leaves us LONG, so a falling mid is adverse.
            move = (m - px) if side == SIDE_BID else (px - m)
            mfe = move if mfe == NOT_IDENTIFIED else max(mfe, move)
            mae = move if mae == NOT_IDENTIFIED else min(mae, move)

        # Did the market come to our price at all? TOUCH ONLY. This never
        # becomes a fill status, and the name says so.
        b, a = _d(r.get("BID")), _d(r.get("ASK"))
        if px != NOT_IDENTIFIED:
            if side == SIDE_BID and a != NOT_IDENTIFIED and a <= px:
                touched = True
                if a < px:
                    crossed = True
            if side == SIDE_ASK and b != NOT_IDENTIFIED and b >= px:
                touched = True
                if b > px:
                    crossed = True

        here = _d(r.get("BID" if side == SIDE_BID else "ASK"))
        if here != NOT_IDENTIFIED and here == px:
            level_persisted_s = _d(str(r.get("ELAPSED_S", 0))) - _d(str(t0))
            now_ahead = _level_qty(r, side, px)
            if (now_ahead != NOT_IDENTIFIED
                    and prev_ahead not in (None, NOT_IDENTIFIED)):
                shrink = _d(prev_ahead) - now_ahead
                if shrink > 0:
                    depth_removed_ahead += shrink
                prev_ahead = now_ahead
            else:
                depth_ok = False

    return {
        "SLUG": quote.get("SLUG"),
        "TICKS_IN_WINDOW": len(rows),
        "TICK_ERRORS_IN_WINDOW": errors,
        "WINDOW_S": (rows[-1]["ELAPSED_S"] - t0) if rows else D("0"),
        # The generous reading: every share the MARKET traded, credited to our
        # price and our side. Certainly too large, which is what makes it an
        # upper bound worth having.
        "TRADE_FLOW_AFTER_ENTRY": traded if traded_ok else NOT_IDENTIFIED,
        "MAXIMAL_ATTRIBUTION_AT_OUR_PRICE": (traded if traded_ok
                                             else NOT_IDENTIFIED),
        "VOLUME_AT_OUR_PRICE": NOT_IDENTIFIED,
        "VOLUME_ATTRIBUTION_BASIS": "WHOLE_MARKET_CREDITED_TO_OUR_LEVEL",
        "AGGRESSOR_SIDE": NOT_IDENTIFIED,
        "PRICE_MOVEMENT_AFTER_ENTRY": (
            (last_mid - _d(px)) if last_mid != NOT_IDENTIFIED
            and px != NOT_IDENTIFIED else NOT_IDENTIFIED),
        "MAX_ADVERSE_EXCURSION": mae,
        "MAX_FAVORABLE_EXCURSION": mfe,
        "TOUCHED_OUR_PRICE": touched,
        "CROSSED_OUR_PRICE": crossed,
        "TOUCH_IS_NOT_A_FILL": True,
        "LEVEL_PERSISTED_S": level_persisted_s,
        "VISIBLE_DEPTH_REMOVED_AHEAD": (depth_removed_ahead if depth_ok
                                        else NOT_IDENTIFIED),
        "LAST_MID": last_mid,
    }


# ---------------------------------------------------------------------------
# THE VERDICT -- refutation from ticks, support only from a tape
# ---------------------------------------------------------------------------

def _refuted(model, maximal, ahead, size):
    """Is `model` refuted by the most generous attribution available?

    Returns True (refuted), False (not refuted -- NOT the same as supported),
    or NOT_IDENTIFIED when the window itself is unreadable.
    """
    if maximal == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    if maximal == 0:
        # Nothing traded. Every model's shared precondition `at > 0` fails, and
        # so does the touch bound.
        return True
    if ahead == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    if model == "F0":
        if size == NOT_IDENTIFIED:
            return NOT_IDENTIFIED
        return maximal < (ahead + size)
    if model == "F1":
        return maximal <= ahead
    if model == "F2":
        # F2 lets a CANCELLATION clear the queue ahead, so a queue-based
        # refutation does not reach it. Only "nothing traded at all" does, and
        # that case returned above.
        return False
    raise ValueError("unknown model: %r" % (model,))


def fill_status(quote, walk, model="F1"):
    """FILL_STATUS from tick evidence alone: NOT_FILLED or UNKNOWN. Never a fill.

    This function has NO branch that returns COUNTERFACTUAL_FILL_*. That is not
    an oversight to be patched later -- it is the identification result, and
    `test_maker_fill.py` proves the absence by walking the AST.
    """
    if model not in MODELS:
        raise ValueError("unknown model: %r" % (model,))
    maximal = walk.get("MAXIMAL_ATTRIBUTION_AT_OUR_PRICE", NOT_IDENTIFIED)
    ahead = quote.get("QUEUE_AHEAD_ESTIMATE", NOT_IDENTIFIED)
    size = quote.get("QUOTE_SIZE", NOT_IDENTIFIED)
    ref = _refuted(model, maximal, ahead, size)

    if ref is True:
        status, why = NOT_FILLED, (
            "MAXIMAL_ATTRIBUTION_DOES_NOT_CLEAR_QUEUE_AHEAD"
            if maximal != 0 else "NO_VOLUME_TRADED_IN_WINDOW")
    else:
        status, why = UNKNOWN, (
            "WINDOW_UNREADABLE" if ref == NOT_IDENTIFIED
            else "NO_PER_PRICE_ATTRIBUTION_WITHOUT_TAPE")

    return {
        "FILL_STATUS": status,
        "FILL_MODEL": model,
        "FILL_STATUS_BASIS": "TICK_CAPTURE_ONLY",
        "WHY": why,
        "MODEL_REFUTED": ref,
        "MAXIMAL_ATTRIBUTION_AT_OUR_PRICE": maximal,
        "QUEUE_AHEAD_ESTIMATE": ahead,
        "VOLUME_AT_OUR_PRICE": NOT_IDENTIFIED,
        "TOUCHED_OUR_PRICE": walk.get("TOUCHED_OUR_PRICE"),
        "POSITIVE_SUPPORT_POSSIBLE_FROM_THIS_EVIDENCE": False,
        "POSITIVE_FILL_SUPPORT_REQUIRES": POSITIVE_FILL_SUPPORT_REQUIRES,
        "ACTUAL_BETTOR_FILL": NOT_IDENTIFIED,
    }


def with_tape(quote, tape, t1=None, block_index=None, model="F1",
              depth_removed_ahead=None):
    """The one route to a POSITIVE counterfactual fill: join a real tape.

    Delegates to `tape.fill_model_v2.evaluate`, which owns the F0<=F1<=F2<=F3
    hierarchy, the block-trade exclusion and the `at > 0` precondition. This
    function READS that verdict; it never re-derives one, because two
    implementations of the same model would eventually disagree and the nicer
    number would win.
    """
    import fill_model_v2 as FM

    if model not in MODELS:
        raise ValueError("unknown model: %r" % (model,))
    for k in ("QUOTE_PRICE", "QUOTE_SIZE", "QUEUE_AHEAD_ESTIMATE"):
        if quote.get(k) == NOT_IDENTIFIED:
            return dict(fill_status(quote, {"MAXIMAL_ATTRIBUTION_AT_OUR_PRICE":
                                            NOT_IDENTIFIED}, model),
                        FILL_STATUS_BASIS="TAPE_JOIN_REFUSED_INCOMPLETE_QUOTE")

    q = FM.Quote(quote["QUOTE_TIME"], quote["SLUG"], quote["SIDE"],
                 quote["QUOTE_PRICE"], quote["QUOTE_SIZE"],
                 quote["QUEUE_AHEAD_ESTIMATE"])
    ev = FM.evaluate(q, tape, t1=t1, block_index=block_index,
                     depth_removed_ahead=(
                         None if depth_removed_ahead in (None, NOT_IDENTIFIED)
                         else depth_removed_ahead))
    supported = ev.get("COUNTERFACTUAL_FILL_SUPPORTED_%s" % model)
    if supported == "YES":
        status, why = STATUS_FOR_MODEL[model], "TAPE_SUPPORTS_%s" % model
    elif supported == "NO":
        status, why = NOT_FILLED, "TAPE_DOES_NOT_SUPPORT_%s" % model
    else:
        status, why = UNKNOWN, "TAPE_VERDICT_NOT_IDENTIFIED"

    return {
        "FILL_STATUS": status,
        "FILL_MODEL": model,
        "FILL_STATUS_BASIS": "EXECUTION_TAPE_JOIN",
        "WHY": why,
        "TRADED_VOLUME_AT_PRICE": ev.get("TRADED_VOLUME_AT_PRICE"),
        "BLOCK_VOLUME_AT_PRICE": ev.get("BLOCK_VOLUME_AT_PRICE"),
        "UNKNOWN_TYPE_VOLUME_AT_PRICE": ev.get("UNKNOWN_TYPE_VOLUME_AT_PRICE"),
        "TIME_TO_QUEUE_DEPLETION": ev.get("TIME_TO_QUEUE_DEPLETION"),
        "F3_TOUCH_ONLY": ev.get("F3_TOUCH_ONLY"),
        "TOUCH_IS_NOT_A_FILL": True,
        "ACTUAL_BETTOR_FILL": NOT_IDENTIFIED,
        "FILL_EVIDENCE": ev,
    }


def is_a_fill(status_row):
    """True only for a COUNTERFACTUAL_FILL_*. A touch raises rather than passes.

    Downstream code asks this instead of testing strings itself, so the one
    promotion this programme must never make has exactly one place to live.
    """
    s = status_row.get("FILL_STATUS")
    if s not in FILL_STATUSES:
        raise ValueError("not a fill status: %r" % (s,))
    if s == UNKNOWN and status_row.get("TOUCHED_OUR_PRICE") and \
            status_row.get("FILL_STATUS_BASIS") == "TICK_CAPTURE_ONLY":
        # Reading this row as a fill is precisely the promotion that is
        # forbidden, so the answer is a plain False -- and the caller that
        # wanted a fill here gets nothing to round up.
        return False
    return s in (COUNTERFACTUAL_FILL_F0, COUNTERFACTUAL_FILL_F1,
                 COUNTERFACTUAL_FILL_F2)


def promote_touch_to_fill(*_a, **_k):
    """The named thing this module refuses to do. Calling it raises.

    It exists so the refusal is greppable and testable rather than implied by
    the absence of a function nobody thought to look for.
    """
    raise TouchPromotion(
        "TOUCH != FILL. A price reaching our hypothetical level is not "
        "evidence that an order which was never submitted was executed.")


# ---------------------------------------------------------------------------
# THE SUMMARY THAT MAY BE REPORTED
# ---------------------------------------------------------------------------

REPORTABLE_FILL_FIELDS = (
    "QUOTES_EVALUATED", "REFUTED_NOT_FILLED", "UNKNOWN_NO_ATTRIBUTION",
    "UNKNOWN_WINDOW_UNREADABLE", "TOUCHED_BUT_UNKNOWN",
    "COUNTERFACTUAL_FILLS_FROM_TAPE",
)

FORBIDDEN_FILL_FIELDS = ("FILL_RATE", "MAKER_FILL_PROBABILITY",
                         "EXPECTED_FILLS_PER_HOUR", "PROFITABILITY",
                         "WIN_RATE", "EXPECTED_MONTHLY_RETURN")


def summarise_fills(rows):
    """Counts, and a FILL RATE only when one is identified -- which it is not.

    A denominator of quotes whose outcome is mostly UNKNOWN does not produce a
    rate. Reporting `fills / quotes` over such a set silently treats every
    UNKNOWN as a miss, which is the same error as treating one as a fill, just
    in the flattering direction for a cautious-sounding number.
    """
    n = len(rows)
    refuted = sum(1 for r in rows if r.get("FILL_STATUS") == NOT_FILLED)
    unknown_attr = sum(1 for r in rows if r.get("WHY")
                       == "NO_PER_PRICE_ATTRIBUTION_WITHOUT_TAPE")
    unreadable = sum(1 for r in rows if r.get("WHY") == "WINDOW_UNREADABLE")
    touched = sum(1 for r in rows if r.get("FILL_STATUS") == UNKNOWN
                  and r.get("TOUCHED_OUR_PRICE"))
    fills = sum(1 for r in rows if is_a_fill(r))
    return {
        "QUOTES_EVALUATED": n,
        "REFUTED_NOT_FILLED": refuted,
        "UNKNOWN_NO_ATTRIBUTION": unknown_attr,
        "UNKNOWN_WINDOW_UNREADABLE": unreadable,
        "TOUCHED_BUT_UNKNOWN": touched,
        "COUNTERFACTUAL_FILLS_FROM_TAPE": fills,
        "FILL_RATE": (NOT_IDENTIFIED if (n - refuted - fills) > 0
                      else (D(fills) / D(n) if n else NOT_IDENTIFIED)),
        "FILL_RATE_DENOMINATOR_COMPLETE": (n - refuted - fills) == 0,
        "UNKNOWN_COUNTED_AS_MISS": False,
        "PROFITABILITY": NOT_IDENTIFIED,
        "WIN_RATE": NOT_IDENTIFIED,
    }
