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

REFUTATION IS ALSO NOT FREE, AND AN EARLIER REVISION OF THIS FILE SAID
IT WAS. The retracted version of the paragraph below read:

    "But REFUTATION does: if the total volume that traded while our
    hypothetical order rested is smaller than the queue that was ahead
    of it, that order cannot have filled, whatever the model."

RETRACTED. It treats displayed queue-ahead at insert as a standing
lower bound on queue-ahead for the whole resting interval, and it is
not one. Cancellations ahead of us deplete the queue during the
interval: 500 displayed at T0, 400 ahead cancel, 200 then trade at our
price -- 200 < 500 reads as refuted while the effective queue was 100
and the order may have been reached. Hidden liquidity lengthens the
true queue, cancellations shorten it, additions lengthen it again, and
the NET direction is not identified from a snapshot.

What ticks can still do is narrower and it is stated as such in
`bettor_shadow_execution.py`: a negative requires OBSERVED queue
evolution -- trades AND cancellations AND additions ahead -- under
identified priority semantics and identified hidden liquidity. Absent
any of those, the row is COUNTERFACTUAL_FILL_NOT_IDENTIFIED with
EVIDENCE_CLASS = INTERVAL_CENSORED, which is preserved and weighted,
never trained on as a no-fill.

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
    "tick data alone cannot SUPPORT a hypothetical fill: support needs "
    "volume-at-price, which no tick row carries. It can bear on the "
    "NEGATIVE, but not by arithmetic on the insert snapshot -- a "
    "supported negative requires observed queue evolution through the "
    "resting interval (trades, cancellations and additions ahead) under "
    "identified priority semantics and identified hidden liquidity")

RETRACTED_REFUTATION_CLAIM = {
    "claim": ("queue-ahead is measured from DISPLAYED size at our "
              "level, so it is a LOWER bound on the true queue and the "
              "refutation is conservative -- it fires less often than "
              "truth would justify, never more"),
    "status": "RETRACTED",
    "why": ("displayed queue-ahead at insert is not a standing lower "
            "bound on queue-ahead throughout the resting interval. "
            "Cancellations ahead deplete it. Hidden liquidity lengthens "
            "the true queue, cancellations shorten it, additions "
            "lengthen it again, and the net direction is not identified "
            "from a snapshot"),
    "counterexample": ("500 displayed ahead at T0; 400 ahead cancel; "
                       "200 then trade at our price. 200 < 500 reads as "
                       "refuted, but the effective queue was 100"),
    "replacedBy": ("bettor_shadow_execution.NEGATIVE_REQUIRES -- the "
                   "negative is earned from observed queue evolution, "
                   "never manufactured from an initial snapshot"),
    "mustNotAppearIn": ("code comments", "tests", "management claims",
                        "P_FILL labels", "training documentation"),
}

POSITIVE_SUPPORT_REQUIRES = "EXECUTION_TAPE_JOIN"

# Rows that do not resolve are not discarded and are not negatives.
INTERVAL_CENSORED_ROWS_ARE_KEPT = (
    "a quote whose fate was not identified still carries information "
    "bounded by its resting interval. It is retained with EVIDENCE_"
    "CLASS = INTERVAL_CENSORED and never trained on as a no-fill")

# The set of rows that DO resolve is selected, and the selection is not
# random with respect to fill probability.
IDENTIFICATION_SELECTION = (
    "trade-through resolution concentrates in fast, informed markets "
    "and full queue-evolution observability in thin ones, so a model "
    "fitted on resolved rows alone estimates P(FILL | RESOLVABLE) "
    "rather than P(FILL). P_FILL must model that selection explicitly")

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
    """What a negative costs. NOT arithmetic on the insert snapshot."""
    st = machinery_status(root)
    if st.get("status") != "LOADED":
        return {"available": False, **st}
    from . import bettor_shadow_execution as sx
    return {
        # Available as a PATH, not as a result: ticks alone do not carry
        # the cancellation dynamics the negative requires, so today the
        # condition is essentially never satisfied and that is correct.
        "available": True,
        "direction": "NEGATIVE_MUST_BE_EARNED",
        "rule": ("a negative requires observed queue evolution through "
                 "the resting interval -- depletion from trades AND "
                 "from cancellations AND additions ahead -- under "
                 "identified priority semantics and identified hidden "
                 "liquidity, with the effective queue ahead never "
                 "reaching zero"),
        "requires": list(sx.NEGATIVE_REQUIRES),
        "insufficient": ("comparing traded volume against displayed "
                         "queue-ahead at insert. That yields "
                         "EVIDENCE_CLASS = INTERVAL_CENSORED with reason "
                         + sx.REASON_INITIAL_QUEUE_ONLY),
        "retracted": dict(RETRACTED_REFUTATION_CLAIM),
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
            "QUEUE_AHEAD_AT_T0", "HIDDEN_LIQUIDITY_STATUS",
            "QUEUE_POSITION_STATUS", "BOOK_SHA_AT_INSERT"),
        # The dynamic fields are what the negative actually needs, and
        # they are the ones tick data does not currently carry. Naming
        # them here is how the gap stays visible instead of being
        # papered over by arithmetic on the snapshot.
        "perObservation": (
            "OBSERVATION_TIMESTAMP", "BEST_BID", "BEST_ASK",
            "SHARES_TRADED_DELTA", "DISPLAYED_SIZE_AT_OUR_LEVEL",
            "QUEUE_DEPLETION_FROM_TRADES",
            "QUEUE_DEPLETION_FROM_CANCELLATIONS",
            "QUEUE_ADDITION_AHEAD", "QUEUE_AHEAD_DYNAMIC_STATUS",
            "TOUCHED", "TRADED_THROUGH"),
        "notRecordedAsFills": (
            "TOUCHED and TRADED_THROUGH are observations about the "
            "market, stored under their own names. Neither is ever "
            "counted toward a fill rate"),
        "queueAheadAtT0IsNotABound": (
            "displayed size at our level at insert is a snapshot, not a "
            "standing bound on queue-ahead for the resting interval. "
            "Cancellations ahead deplete it, hidden liquidity lengthens "
            "it, additions lengthen it again, and the net direction is "
            "not identified from the snapshot"),
        "intervalCensoredRowsAreKept": INTERVAL_CENSORED_ROWS_ARE_KEPT,
        "identificationSelection": IDENTIFICATION_SELECTION,
    }


def describe() -> dict:
    return {
        "P_FILL": NOT_IDENTIFIED,
        "requiredSource": REQUIRED_SOURCE,
        "forbiddenSubstitutes": list(FORBIDDEN_AS_P_FILL),
        "tickAsymmetry": TICK_ASYMMETRY,
        "retractedRefutationClaim": dict(RETRACTED_REFUTATION_CLAIM),
        "intervalCensoredRowsAreKept": INTERVAL_CENSORED_ROWS_ARE_KEPT,
        "identificationSelection": IDENTIFICATION_SELECTION,
        "positiveSupportRequires": POSITIVE_SUPPORT_REQUIRES,
        "neverWritesFilled": NEVER_WRITES_FILLED,
        "machinery": machinery_status(),
        "accumulationContract": accumulation_contract(),
    }
