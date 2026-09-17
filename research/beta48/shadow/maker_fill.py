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

AND THE NEGATIVE IS NOT FREE EITHER. THE UPPER BOUND IS ARITHMETIC, NOT A
VERDICT.

The tempting move is to refute a fill from an upper bound: credit EVERY share
the market traded after entry to our price AND to our side -- the most generous
reading physically available -- and if even that does not clear the queue
displayed ahead of us, call the model refuted.

    MAXIMAL_ATTRIBUTION = sum of SHARES_TRADED_DELTA over the window

    F0 needs   at >= QUEUE_AHEAD + QUOTE_SIZE   -> bound fails if MAX < that
    F1 needs   at >  QUEUE_AHEAD                -> bound fails if MAX <= AHEAD
    F2 needs   at >  0  (plus depletion)        -> bound fails only if MAX == 0
    F3 needs   at >  0  or through-volume > 0   -> bound fails only if MAX == 0

The arithmetic is sound. THE INPUT IS NOT ESTABLISHED. `SHARES_TRADED` has
    SHARES_TRADED_RESET_SEMANTICS = NOT_IDENTIFIED
and until its venue semantics are known -- cumulative or interval, market-wide
or side-specific, blocks in or out, where it resets, how often it updates -- a
delta of zero cannot be distinguished from a field that did not update. That
case is the dangerous one, because it produces a CONFIDENT WRONG ANSWER: a
quiet feed reads exactly like a quiet market and would refute every model,
including the touch bound.

So the bound is computed, carried, and NOT promoted:

    SHARES_TRADED_DELTA_STATUS = CONSERVATIVE_DIAGNOSTIC_ONLY
    FILL_REFUTATION_STATUS     = NOT_IDENTIFIED
    WHY                        = AGGREGATE_VOLUME_UPPER_BOUND_INSUFFICIENT

which is NOT equivalent to observed execution evidence, and `PROVEN_NOT_FILLED`
is not an output of this programme at all. `tick_semantics` owns that gate and
keeps RUNTIME BEHAVIOUR (what the field did in our capture -- ours to measure)
strictly apart from VENUE SEMANTICS (what it means -- not ours to infer).

THREE DIFFERENT EVENTS, NEVER COLLAPSED (and this is structural, not a naming
convention):

    TOUCH               the market reached our price
    TRADE_EVIDENCE      a QUALIFYING execution occurred at our price after our
                        hypothetical entry -- CLOB, typed, not a block
    COUNTERFACTUAL_FILL trade evidence PLUS the queue model establishes that our
                        order would have executed

    TOUCH != TRADE_EVIDENCE != COUNTERFACTUAL_FILL

Each is recorded separately on every row. From ticks alone the first is
observable and the other two are NOT_IDENTIFIED.

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

import tick_semantics as TS                                # noqa: E402
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

# ---------------------------------------------------------------------------
# THE THREE EVENTS. Separate names, separate fields, separate evidence.
# ---------------------------------------------------------------------------
TOUCH = "TOUCH"
TRADE_EVIDENCE = "TRADE_EVIDENCE"
COUNTERFACTUAL_FILL = "COUNTERFACTUAL_FILL"

EVENT_LADDER = (TOUCH, TRADE_EVIDENCE, COUNTERFACTUAL_FILL)

# Each rung requires strictly more than the one below it, and no rung is ever
# read as the next one up. Written as data so a test can assert the ladder
# rather than trusting three prose sentences to stay true.
EVENT_REQUIRES = {
    TOUCH: "THE_MARKET_REACHED_OUR_PRICE",
    TRADE_EVIDENCE: "A_QUALIFYING_CLOB_EXECUTION_AT_OUR_PRICE_AFTER_ENTRY",
    COUNTERFACTUAL_FILL: "TRADE_EVIDENCE_PLUS_THE_QUEUE_MODEL",
}
TOUCH_IS_NOT_TRADE_EVIDENCE = True
TRADE_EVIDENCE_IS_NOT_A_COUNTERFACTUAL_FILL = True
EVENTS_ARE_DISTINCT = True

# A block print at our price is a TRADE and is NOT trade evidence: it never
# touched the book, so it depleted no queue.
BLOCK_IS_NOT_TRADE_EVIDENCE = True
UNKNOWN_EXECUTION_TYPE_IS_NOT_TRADE_EVIDENCE = True

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

def _bound_fails(model, maximal, ahead, size):
    """Does the most generous attribution FAIL to reach what `model` needs?

    ARITHMETIC ONLY. True here is not a refutation -- it is the statement that
    the upper bound, TAKEN AT FACE VALUE, would not clear the queue. Whether the
    bound may be taken at face value is `tick_semantics`' question, and the
    answer today is no.

    Returns True, False, or NOT_IDENTIFIED when the window is unreadable.
    """
    if maximal == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    if maximal == 0:
        # The field did not move. That is NOT "nothing traded" until the field's
        # update semantics are known -- see `tick_semantics`. Arithmetically the
        # bound fails for every model; whether that means anything is gated.
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


def fill_status(quote, walk, model="F1", semantics=None, runtime=None):
    """FILL_STATUS from tick evidence alone. Today: UNKNOWN, with a reason.

    TWO GATES, AND THEY FAIL IN OPPOSITE DIRECTIONS.

    The POSITIVE gate: this function has NO branch that returns
    COUNTERFACTUAL_FILL_*. Ticks carry no per-price attribution, so a fill
    cannot be supported here at all.

    The NEGATIVE gate, which is the correction that produced this version: the
    volume bound is ARITHMETIC over a field whose venue semantics are
    NOT_IDENTIFIED. A zero delta may be a quiet market or a lazy field, and
    those are different facts. So a failing bound yields
    FILL_REFUTATION_STATUS = NOT_IDENTIFIED and the separately named
    AGGREGATE_VOLUME_UPPER_BOUND_INSUFFICIENT -- which is NOT equivalent to
    observed execution evidence -- rather than NOT_FILLED.

    `semantics` / `runtime` are how that gate opens: both the venue's meaning
    and our own capture's behaviour, checked by `tick_semantics`. Passing a
    runtime diagnostic alone does not open it.
    """
    if model not in MODELS:
        raise ValueError("unknown model: %r" % (model,))
    maximal = walk.get("MAXIMAL_ATTRIBUTION_AT_OUR_PRICE", NOT_IDENTIFIED)
    ahead = quote.get("QUEUE_AHEAD_ESTIMATE", NOT_IDENTIFIED)
    size = quote.get("QUOTE_SIZE", NOT_IDENTIFIED)
    bound = _bound_fails(model, maximal, ahead, size)
    ref = TS.refutation_status(bound is True, semantics, runtime)

    if ref["FILL_REFUTATION_STATUS"] == "REFUTED":
        status, why = NOT_FILLED, (
            "MAXIMAL_ATTRIBUTION_DOES_NOT_CLEAR_QUEUE_AHEAD"
            if maximal != 0 else "NO_VOLUME_TRADED_IN_WINDOW")
    elif bound is NOT_IDENTIFIED:
        status, why = UNKNOWN, "WINDOW_UNREADABLE"
    elif bound is True:
        # The bound would have refuted, and is not allowed to.
        status, why = UNKNOWN, TS.AGGREGATE_VOLUME_UPPER_BOUND_INSUFFICIENT
    else:
        status, why = UNKNOWN, "NO_PER_PRICE_ATTRIBUTION_WITHOUT_TAPE"

    out = {
        "FILL_STATUS": status,
        "FILL_MODEL": model,
        "FILL_STATUS_BASIS": "TICK_CAPTURE_ONLY",
        "WHY": why,
        "AGGREGATE_VOLUME_BOUND_ARITHMETIC": bound,
        "MAXIMAL_ATTRIBUTION_AT_OUR_PRICE": maximal,
        "QUEUE_AHEAD_ESTIMATE": ahead,
        "VOLUME_AT_OUR_PRICE": NOT_IDENTIFIED,

        # The three events, side by side, never collapsed into one another.
        "TOUCH": walk.get("TOUCHED_OUR_PRICE"),
        "TOUCHED_OUR_PRICE": walk.get("TOUCHED_OUR_PRICE"),
        "TRADE_EVIDENCE": NOT_IDENTIFIED,
        "COUNTERFACTUAL_FILL": NOT_IDENTIFIED,
        "TRADE_EVIDENCE_BASIS": "REQUIRES_A_TYPED_EXECUTION_AT_OUR_PRICE",

        "POSITIVE_SUPPORT_POSSIBLE_FROM_THIS_EVIDENCE": False,
        "POSITIVE_FILL_SUPPORT_REQUIRES": POSITIVE_FILL_SUPPORT_REQUIRES,
        "ACTUAL_BETTOR_FILL": NOT_IDENTIFIED,
    }
    # The refutation block carries its own WHY about the FIELD; the row's WHY
    # is about the QUOTE. Merging them under one key would let the field's
    # excuse overwrite the quote's reason, which is how a row ends up saying
    # something true about the wrong subject.
    ref = dict(ref)
    ref["REFUTATION_WHY"] = ref.pop("WHY")
    out.update(ref)
    return out


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

    clob_at_price = ev.get("TRADED_VOLUME_AT_PRICE")
    return {
        "FILL_STATUS": status,
        "FILL_MODEL": model,
        "FILL_STATUS_BASIS": "EXECUTION_TAPE_JOIN",
        "WHY": why,

        # The three events again, and here the middle one is finally
        # observable. TRADE_EVIDENCE counts CLOB volume at our price ONLY: a
        # block never touched the book and an untyped print is not known to
        # have, so neither depletes a queue and neither is evidence.
        "TOUCH": ev.get("F3_TOUCH_ONLY") == "YES",
        "TRADE_EVIDENCE": (clob_at_price > 0
                           if clob_at_price is not None else NOT_IDENTIFIED),
        "TRADE_EVIDENCE_BASIS": "CLOB_VOLUME_AT_OUR_PRICE_AFTER_ENTRY",
        "COUNTERFACTUAL_FILL": status in (COUNTERFACTUAL_FILL_F0,
                                          COUNTERFACTUAL_FILL_F1,
                                          COUNTERFACTUAL_FILL_F2),
        "TRADED_VOLUME_AT_PRICE": clob_at_price,
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
    "HYPOTHETICAL_QUOTES", "TOUCHES", "TRADE_EVIDENCE",
    "ADMITTED_COUNTERFACTUAL_FILLS", "REFUTED_NOT_FILLED",
    "UNKNOWN_NO_ATTRIBUTION", "UNKNOWN_VOLUME_BOUND_INSUFFICIENT",
    "UNKNOWN_WINDOW_UNREADABLE",
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
    bound_short = sum(1 for r in rows if r.get("WHY")
                      == TS.AGGREGATE_VOLUME_UPPER_BOUND_INSUFFICIENT)
    unreadable = sum(1 for r in rows if r.get("WHY") == "WINDOW_UNREADABLE")
    touches = sum(1 for r in rows if r.get("TOUCH") is True)
    evidence = sum(1 for r in rows if r.get("TRADE_EVIDENCE") is True)
    fills = sum(1 for r in rows if is_a_fill(r))
    return {
        # The three events reported as three counts, in ladder order, so the
        # gap between them is visible instead of inferred.
        "HYPOTHETICAL_QUOTES": n,
        "TOUCHES": touches,
        "TRADE_EVIDENCE": evidence,
        "ADMITTED_COUNTERFACTUAL_FILLS": fills,

        "REFUTED_NOT_FILLED": refuted,
        "UNKNOWN_NO_ATTRIBUTION": unknown_attr,
        "UNKNOWN_VOLUME_BOUND_INSUFFICIENT": bound_short,
        "UNKNOWN_WINDOW_UNREADABLE": unreadable,
        "TOUCHED_BUT_UNKNOWN": sum(1 for r in rows
                                   if r.get("FILL_STATUS") == UNKNOWN
                                   and r.get("TOUCH") is True),
        "FILL_RATE": (NOT_IDENTIFIED if (n - refuted - fills) > 0
                      else (D(fills) / D(n) if n else NOT_IDENTIFIED)),
        "FILL_RATE_DENOMINATOR_COMPLETE": (n - refuted - fills) == 0,
        "UNKNOWN_COUNTED_AS_MISS": False,
        "TOUCH_COUNTED_AS_FILL": False,
        "TRADE_EVIDENCE_COUNTED_AS_FILL": False,
        "PROFITABILITY": NOT_IDENTIFIED,
        "WIN_RATE": NOT_IDENTIFIED,
    }


# ===========================================================================
# Section 34. The fill QUANTITY interface. INTERFACES ONLY -- NOTHING IS
# ESTIMATED HERE.
#
# The EV engine's P_FILL is a single scalar: the probability that a posted
# order is filled. That is not the quantity a maker actually cares about. A
# 5,000-share quote that gets 200 shares away is not "filled", and it is not
# "not filled" either -- and the economics of the two readings differ by more
# than an order of magnitude.
#
# Every field below is NOT_IDENTIFIED. BETTOR has posted no orders, so there
# is no fill distribution to estimate, and a plausible shape invented here
# would propagate straight into EV_TOTAL_USD. What this section fixes is that
# the QUESTION was not even representable: the interface existed only for the
# binary.
# ===========================================================================

FILL_QUANTITY_FIELDS = (
    "P_ANY_FILL_BY_HORIZON",
    "P_FULL_FILL_BY_HORIZON",
    "EXPECTED_FILL_FRACTION_BY_HORIZON",
    "EXPECTED_FILLED_QTY_BY_HORIZON",
    "FILL_QTY_DISTRIBUTION",
    "TIME_TO_FIRST_FILL",
    "TIME_TO_FULL_FILL",
)

FILL_QUANTITY_STATUS = "INTERFACE_ONLY_NOT_ESTIMATED"

A_SCALAR_P_FILL_IS_NOT_A_FILL_MODEL = (
    "P_FILL answers 'was the order filled'. A resting maker quote is filled "
    "in PARTS, over TIME, and the part that fills is selected -- the informed "
    "flow takes the shares it wants and leaves the rest. EXPECTED_FILLED_QTY "
    "and P_ANY_FILL can differ by an order of magnitude on the same quote, "
    "and the EV built on the scalar cannot tell which it meant")

WHY_EVERY_FIELD_IS_NOT_IDENTIFIED = (
    "BETTOR has posted no orders. There is no BETTOR-native fill-size "
    "distribution, no time-to-first-fill and no partial-fill curve, and a "
    "shape guessed from venue mechanics would enter EV_TOTAL_USD as though "
    "it had been measured. The interface is declared so the question can be "
    "asked of the prospective capture; the answers stay NOT_IDENTIFIED")

WHAT_WOULD_IDENTIFY_THESE = (
    "BETTOR_NATIVE_RESTING_ORDER_LOG",
    "PER_ORDER_PARTIAL_FILL_SEQUENCE",
    "QUEUE_POSITION_AT_INSERT_AND_AT_EACH_FILL",
    "EXECUTION_TAPE_JOIN_WITH_BLOCK_INDEX",
)

FILL_QUANTITY_MAY_ENTER_ACTION_EV = False


def fill_quantity_interface(quote=None, horizons_s=(5, 30, 60, 300)):
    """The fill-quantity question, representable and unanswered.

    Returns every field NOT_IDENTIFIED, per horizon where a horizon applies.
    No caller may substitute P_FILL for any of these: a scalar fill
    probability is a different quantity, and the substitution is what this
    interface exists to make visible.
    """
    by_horizon = {"%dS" % h: NOT_IDENTIFIED for h in horizons_s}
    out = {
        "QUOTE": (quote or {}).get("QUOTE_ID", NOT_IDENTIFIED),
        "FILL_QUANTITY_STATUS": FILL_QUANTITY_STATUS,
        "P_ANY_FILL_BY_HORIZON": dict(by_horizon),
        "P_FULL_FILL_BY_HORIZON": dict(by_horizon),
        "EXPECTED_FILL_FRACTION_BY_HORIZON": dict(by_horizon),
        "EXPECTED_FILLED_QTY_BY_HORIZON": dict(by_horizon),
        "FILL_QTY_DISTRIBUTION": NOT_IDENTIFIED,
        "TIME_TO_FIRST_FILL": NOT_IDENTIFIED,
        "TIME_TO_FULL_FILL": NOT_IDENTIFIED,
        "FILL_QUANTITY_FIELDS": FILL_QUANTITY_FIELDS,
        "HORIZONS_S": tuple(horizons_s),
        "A_SCALAR_P_FILL_IS_NOT_A_FILL_MODEL":
            A_SCALAR_P_FILL_IS_NOT_A_FILL_MODEL,
        "WHY_EVERY_FIELD_IS_NOT_IDENTIFIED": WHY_EVERY_FIELD_IS_NOT_IDENTIFIED,
        "WHAT_WOULD_IDENTIFY_THESE": WHAT_WOULD_IDENTIFY_THESE,
        "MAY_ENTER_ACTION_EV": FILL_QUANTITY_MAY_ENTER_ACTION_EV,
        "NOTHING_IS_TRAINED_HERE": True,
        "NO_ORDER_IS_PLACED": True,
    }
    return out
