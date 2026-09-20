"""§15. P_FILL: THE INTERFACE WIRED, THE QUANTITY STILL NOT IDENTIFIED.

Owner directive, "CONTINUE THE BUILD" §15:

    "P_FILL remains NOT_IDENTIFIED until BETTOR-native admitted
    execution evidence establishes it. Do not use whale completion,
    touch, price move or displayed depth as actual P_FILL.
    maker_fill.py already contains the mechanism/interface. Wire it.
    Begin accumulating the BETTOR-native data required to estimate it."

WHY THE FOUR FORBIDDEN SUBSTITUTES ARE ALL TEMPTING, AND ALL WRONG.
Each is a number that exists, correlates with filling, and is not a
fill rate:

    WHALE COMPLETION  how fast someone else completed their pairs, on
                      positions selected because THEY chose to enter
                      them, with their size and their order policy --
                      and WHALE_ORDER_POLICY is NOT_IDENTIFIED. The
                      frozen priors mark every row
                      MAY_SEED_BETTOR_P_FILL = false.
    A TOUCH           the price reached our level. Nobody traded with
                      us; we were not in the queue. maker_fill returns
                      UNKNOWN for a touch precisely so it cannot be
                      promoted.
    A PRICE MOVE      the market went through our price. That says
                      volume existed somewhere, not that it reached a
                      hypothetical order at our position in a queue we
                      never joined.
    DISPLAYED DEPTH   what the book showed. Depth is what COULD have
                      traded, not what did, and it excludes hidden
                      liquidity entirely.

WHAT `maker_fill.py` ACTUALLY ESTABLISHES, which is the useful part.
It is asymmetric by construction, and the asymmetry is the finding:

    TICKS_ALONE_SUPPORT_POSITIVE_FILL   False
    TICKS_ALONE_SUPPORT_REFUTATION      True
    POSITIVE_FILL_SUPPORT_REQUIRES      EXECUTION_TAPE_JOIN

A tick row carries bid/ask, the top-5 ladders, displayed size and the
change in cumulative volume. It carries no per-print tape, so
volume-at-price, which side consumed it, and our queue position are all
NOT_IDENTIFIED -- and every fill model needs volume-at-price above
zero. So no positive fill support comes out of tick data at all.

But REFUTATION does: if the total volume that traded while our
hypothetical order rested is smaller than the queue that was ahead of
it, that order cannot have filled, whatever the model. That is an
arithmetic upper bound, not a verdict, and it is available today.

THE STRONGEST POSITIVE OUTPUT IS STILL COUNTERFACTUAL. The module
never writes FILLED for any input, because BETTOR placed no order.
Its positive statuses are COUNTERFACTUAL_FILL_F0/F1/F2 and the
distinction is enforced by the absence of the other name rather than
by a comment.

ACCUMULATION STARTS NOW, WHICH IS THE POINT OF WIRING IT EARLY. The
evidence P_FILL needs is BETTOR-native and PROSPECTIVE: it cannot be
recovered later from a tape that never recorded our orders, because
there were none. Recording the hypothetical quote, its queue position
at insert and what the book did afterwards is what makes the estimate
possible on the day the order path exists.
"""

from __future__ import annotations

from . import bettor_ev_bridge as evb

NOT_IDENTIFIED = "NOT_IDENTIFIED"

P_FILL_STATUS = NOT_IDENTIFIED
P_FILL_LADDER = ("NOT_IDENTIFIED", "PARTIALLY_IDENTIFIED", "IDENTIFIED")

REQUIRED_SOURCE = "BETTOR_NATIVE_ADMITTED_FILLS_REQUIRED"

# ── §15: the four substitutes, each refused by name ──────────────────

FORBIDDEN_AS_P_FILL = {
    "WHALE_COMPLETION": (
        "how fast another account completed THEIR pairs, on positions "
        "selected because they chose to enter them, with their size "
        "and an order policy that is itself NOT_IDENTIFIED. The frozen "
        "priors mark every hazard row MAY_SEED_BETTOR_P_FILL = false"),
    "TOUCH": (
        "the price reached our level and nobody traded with us -- we "
        "were never in the queue. maker_fill returns UNKNOWN for a "
        "touch so it cannot be promoted into a fill"),
    "PRICE_MOVE": (
        "the market traded through our price. That says volume existed "
        "somewhere, not that it reached a hypothetical order at a "
        "queue position we never held"),
    "DISPLAYED_DEPTH": (
        "what the book showed is what COULD have traded, not what did, "
        "and it excludes hidden liquidity entirely"),
}

ONE_FILL_DOES_NOT_IDENTIFY_IT = (
    "a single observed fill is one observation. The ladder "
    "NOT_IDENTIFIED -> PARTIALLY_IDENTIFIED -> IDENTIFIED exists so "
    "that one fill cannot be read as a measured rate")

# ── what ticks can and cannot settle ─────────────────────────────────

TICK_ASYMMETRY = (
    "tick data alone can REFUTE a hypothetical fill and cannot SUPPORT "
    "one. Refutation is arithmetic: if the volume that traded while the "
    "order rested is below the queue ahead of it, it cannot have "
    "filled. Support needs volume-at-price, which no tick row carries")

POSITIVE_SUPPORT_REQUIRES = "EXECUTION_TAPE_JOIN"

NEVER_WRITES_FILLED = (
    "BETTOR placed no order, so there is no input for which this path "
    "writes FILLED. The strongest positive output is "
    "COUNTERFACTUAL_FILL_F0/F1/F2")


def machinery_status(root=None) -> dict:
    """Is maker_fill loaded, and what does it declare about itself?"""
    try:
        MF = evb.machinery(root)["maker_fill"]
    except evb.MachineryUnavailable as exc:
        return {"status": evb.MACHINERY_UNAVAILABLE, "why": str(exc)}
    return {
        "status": "LOADED",
        "module": "research/beta48/shadow/maker_fill.py",
        "SHADOW_ONLY": MF.SHADOW_ONLY,
        "ORDER_PATH_EXISTS": MF.ORDER_PATH_EXISTS,
        "ACTUAL_BETTOR_FILL": MF.ACTUAL_BETTOR_FILL,
        "ACTUAL_BETTOR_QUEUE_POSITION": MF.ACTUAL_BETTOR_QUEUE_POSITION,
        "TICKS_ALONE_SUPPORT_POSITIVE_FILL":
            MF.TICKS_ALONE_SUPPORT_POSITIVE_FILL,
        "TICKS_ALONE_SUPPORT_REFUTATION": MF.TICKS_ALONE_SUPPORT_REFUTATION,
        "POSITIVE_FILL_SUPPORT_REQUIRES": MF.POSITIVE_FILL_SUPPORT_REQUIRES,
        "FILL_STATUSES": list(MF.FILL_STATUSES),
        "TOUCH_IS_NOT_A_FILL_STATUS": MF.TOUCH_IS_NOT_A_FILL_STATUS,
        "HIDDEN_LIQUIDITY": MF.HIDDEN_LIQUIDITY,
        "AGGRESSOR_SIDE": MF.AGGRESSOR_SIDE,
    }


def p_fill(*, bettor_native_fills=0, root=None) -> dict:
    """BETTOR's fill probability. NOT_IDENTIFIED until its own fills exist."""
    out = {
        "P_FILL": NOT_IDENTIFIED,
        "status": NOT_IDENTIFIED,
        "ladder": list(P_FILL_LADDER),
        "requiredSource": REQUIRED_SOURCE,
        "bettorNativeFills": bettor_native_fills,
        "forbiddenSubstitutes": dict(FORBIDDEN_AS_P_FILL),
        "oneFillDoesNotIdentifyIt": ONE_FILL_DOES_NOT_IDENTIFY_IT,
        "tickAsymmetry": TICK_ASYMMETRY,
        "neverWritesFilled": NEVER_WRITES_FILLED,
    }
    if bettor_native_fills <= 0:
        out["why"] = (
            "BETTOR has never rested an order, so there are no admitted "
            "executions to condition on. No substitute is accepted: "
            "whale completion, a touch, a price move and displayed "
            "depth are each a different quantity that correlates with "
            "filling without being a fill rate")
        return out
    # A LADDER, NOT A SWITCH. Even with fills in hand the status moves
    # to PARTIALLY_IDENTIFIED, never straight to IDENTIFIED, so a small
    # sample cannot present itself as a measured rate.
    out.update({
        "status": "PARTIALLY_IDENTIFIED",
        "why": ("%d BETTOR-native fill(s) exist. That is evidence and "
                "not yet a rate -- the estimate stays PARTIALLY_"
                "IDENTIFIED until the sample supports one"
                % bettor_native_fills),
    })
    return out


def refutation_available(root=None) -> dict:
    """What ticks CAN settle today: the negative, by arithmetic."""
    st = machinery_status(root)
    if st.get("status") != "LOADED":
        return {"available": False, **st}
    return {
        "available": True,
        "direction": "REFUTATION_ONLY",
        "rule": ("if the volume traded while the order rested is below "
                 "the queue ahead of it at insert, that order cannot "
                 "have filled -- whatever the model"),
        "isAnUpperBound": ("queue-ahead is measured from DISPLAYED size "
                           "at our level, so it is a LOWER bound on the "
                           "true queue and the refutation is "
                           "conservative"),
        "cannotSupportAFill": st["POSITIVE_FILL_SUPPORT_REQUIRES"],
        "tickAsymmetry": TICK_ASYMMETRY,
    }


def accumulation_contract() -> dict:
    """What must be recorded NOW for P_FILL to be estimable later.

    This cannot be reconstructed after the fact: there is no tape of
    BETTOR orders to go back to, because none were placed. The evidence
    is prospective or it does not exist.
    """
    return {
        "whyProspective": (
            "P_FILL needs BETTOR-native fills, and a fill that was "
            "never attempted leaves no trace to recover. Recording the "
            "hypothetical quote and what the book did afterwards is "
            "what makes the estimate possible on the day an order path "
            "exists"),
        "perQuote": (
            "QUOTE_PRICE", "QUOTE_SIZE", "SIDE", "INSERT_TIMESTAMP",
            "QUEUE_AHEAD_AT_INSERT", "BOOK_SHA_AT_INSERT"),
        "perObservation": (
            "OBSERVATION_TIMESTAMP", "BEST_BID", "BEST_ASK",
            "SHARES_TRADED_DELTA", "DISPLAYED_SIZE_AT_OUR_LEVEL",
            "TOUCHED", "TRADED_THROUGH"),
        "notRecordedAsFills": (
            "TOUCHED and TRADED_THROUGH are observations about the "
            "market, stored under their own names. Neither is ever "
            "counted toward a fill rate"),
        "queueAheadIsALowerBound": (
            "it is measured from displayed size at our level at insert, "
            "so hidden liquidity makes the true queue longer and the "
            "resulting refutation conservative"),
    }


def describe() -> dict:
    return {
        "P_FILL": NOT_IDENTIFIED,
        "requiredSource": REQUIRED_SOURCE,
        "forbiddenSubstitutes": list(FORBIDDEN_AS_P_FILL),
        "tickAsymmetry": TICK_ASYMMETRY,
        "positiveSupportRequires": POSITIVE_SUPPORT_REQUIRES,
        "neverWritesFilled": NEVER_WRITES_FILLED,
        "machinery": machinery_status(),
        "accumulationContract": accumulation_contract(),
    }
