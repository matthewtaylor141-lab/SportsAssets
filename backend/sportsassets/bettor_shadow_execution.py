"""COUNTERFACTUAL PASSIVE EXECUTION. THE ORDER BETTOR DID NOT PLACE.

Owner directive, §4:

    "We need a SHADOW execution model or BETTOR will never generate the
    prospective inventory needed to test the Ferrari/RN1 architecture.
    Do NOT call a touch a fill. Do NOT call a price move a fill. Do NOT
    call whale completion BETTOR P_FILL. Build a separate prospective
    object: COUNTERFACTUAL_PASSIVE_EXECUTION distinct from
    ACTUAL_BETTOR_FILL."

THE PROBLEM THIS SOLVES, AND THE ONE IT MUST NOT CREATE. BETTOR has
never rested an order, so P_FILL is NOT_IDENTIFIED and the whole
inventory architecture -- pair completion, residual management, the
exit engine, the Ferrari improvement -- has nothing to act on. Waiting
for a real order path would mean the architecture is never tested. But
a shadow model that quietly calls something a fill would poison every
downstream measurement with a number nobody earned.

So there are TWO NAMES and they never touch:

    ACTUAL_BETTOR_FILL              requires a real BETTOR order.
                                    Does not exist. Cannot be written
                                    by anything in this module.

    COUNTERFACTUAL_PASSIVE_EXECUTION  what WOULD have happened to a
                                    quote we did not send, judged
                                    against tape we did not influence.

`maker_fill.py` already enforces the same separation -- its strongest
positive output is COUNTERFACTUAL_FILL_F0/F1/F2 and it has no code path
that writes FILLED -- and this module carries that discipline into the
prospective dataset rather than re-deriving it.

────────────────────────────────────────────────────────────────────
PROSPECTIVE MEANS THE RECORD IS CLOSED BEFORE THE ANSWER EXISTS (§9).

The hypothetical order is written at T0 with the book as it was, and
NOTHING about the future is in that record. The outcome is appended
later, keyed to it. That ordering is the whole value: a dataset where
the order could be adjusted after seeing the tape would measure our
hindsight, not the venue.

    ACTION_AVAILABLE_AT_T0 is stored separately from
    COUNTERFACTUAL_OUTCOME_LATER, and an evaluation that reads the
    second while claiming to be the first is the error the split
    exists to make visible.
────────────────────────────────────────────────────────────────────

════════════════════════════════════════════════════════════════════
RETRACTION (owner directive, "STOP BEFORE BUILDING §5 P_FILL LABELS").

An earlier revision of this module carried the claim:

    "queue-ahead is measured from DISPLAYED size at our level at
    insert, so hidden liquidity can only lengthen the true queue.
    DEFINITELY_NOT_FILLED is therefore conservative: it fires less
    often than the truth would justify, never more."

THAT CLAIM IS RETRACTED. It is wrong, and it was load-bearing: it was
the entire justification for manufacturing a negative fill label out of
a single T0 snapshot. It ignores queue depletion. The counterexample is
routine, not exotic:

    T0      displayed queue ahead of us = 500
    T0..T1  400 contracts ahead of us CANCEL
    T1      effective queue ahead = 100
    T1..T2  200 contracts trade at our price

The old classifier compares 200 traded against 500 displayed, sees
200 < 500, and writes DEFINITELY_NOT_FILLED. But the effective queue had
already fallen to 100 and 200 traded through it, so the order may well
have been reached. The label would have been a fabricated negative.

The correct statement, which replaces it:

    DISPLAYED_QUEUE_AHEAD_AT_T0 IS NOT A PERMANENT LOWER BOUND ON QUEUE
    AHEAD THROUGHOUT THE RESTING INTERVAL.

    Hidden liquidity can LENGTHEN the true queue.
    Cancellations ahead can SHORTEN it.
    Additions ahead can lengthen it again.
    All of them matter, and the NET direction is NOT_IDENTIFIED from a
    snapshot. We are not entitled to a sign.

The retracted sentence must not reappear in code comments, tests,
management claims, P_FILL labels or training documentation. The
constant that carried it is retired by name below so a search finds
the retraction rather than the claim.
════════════════════════════════════════════════════════════════════

THREE LABELS, AND THE NEGATIVE IS NOT FREE (§5, §8). Queue position is
not observable -- we were never in the queue -- so most outcomes are
genuinely undecidable, and the negative now has to be EARNED the same
way the positive does:

    COUNTERFACTUAL_FILL_SUPPORTED     the tape traded THROUGH our price,
                                      under stated assumptions (§6).
    COUNTERFACTUAL_NO_FILL_SUPPORTED  the observed queue evolution --
                                      trades AND cancellations AND
                                      additions ahead -- never depleted
                                      the queue to our position, under
                                      identified priority semantics.
    COUNTERFACTUAL_FILL_NOT_IDENTIFIED  everything else, including the
                                      case the retracted claim used to
                                      convert into a negative.

FORCING THE THIRD INTO YES OR NO IS THE WHOLE TRAP. A binary label
would turn "we cannot tell" into evidence, and a fill model trained on
it would learn our guess rather than the venue's behaviour.

BUT NOT_IDENTIFIED IS NOT ONE THING (§8). A row where the tape traded at
our price all interval carries real information bounded by that
interval; a row where we have no tape at all carries none. Both are
unresolved and neither is a negative, so both get the third label --
and EVIDENCE_CLASS keeps them apart so the P_FILL stage can weight
interval-censored rows instead of discarding them.

    POSITIVE_SUPPORTED / NEGATIVE_SUPPORTED / INTERVAL_CENSORED /
    NOT_IDENTIFIED

IDENTIFICATION IS ITSELF A SELECTION (§9). The rows that resolve are not
a random sample of the rows we wrote: trade-throughs happen in fast,
informed markets, and full queue evolution is observable mostly in thin
ones. A P_FILL model fitted on resolved rows alone estimates P(fill |
resolvable), not P(fill). That is recorded on every row rather than
left for the modelling stage to rediscover.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ── the two names, kept apart forever ────────────────────────────────

COUNTERFACTUAL = "COUNTERFACTUAL_PASSIVE_EXECUTION"
ACTUAL = "ACTUAL_BETTOR_FILL"

ACTUAL_BETTOR_FILL = NOT_IDENTIFIED
ORDER_PATH_EXISTS = False
SHADOW_ONLY = True

NEVER_AN_ACTUAL_FILL = (
    "no input to this module produces ACTUAL_BETTOR_FILL. BETTOR placed "
    "no order, so there is nothing for an actual fill to describe. The "
    "separation is enforced by the absence of the name from every "
    "output rather than by a comment asking readers to be careful")

# ── §2: the retraction, kept in the source so a search finds it ──────

RETRACTED_CLAIMS = {
    "QUEUE_AHEAD_IS_A_LOWER_BOUND": {
        "claim": ("hidden liquidity can only lengthen the true queue, "
                  "therefore DEFINITELY_NOT_FILLED fires less often "
                  "than truth would justify, never more"),
        "status": "RETRACTED",
        "why": ("it ignores queue depletion. Cancellations ahead of us "
                "shorten the effective queue during the resting "
                "interval, so a T0 displayed size is not a bound on "
                "queue ahead later in that interval. The claim was "
                "load-bearing: it was the sole justification for "
                "manufacturing a negative fill label from a snapshot"),
        "counterexample": ("displayed queue ahead 500 at T0; 400 ahead "
                           "cancel; 200 then trade at our price. "
                           "200 < 500 reads as refuted, but the "
                           "effective queue was 100 and the order may "
                           "have been reached"),
        "mustNotAppearIn": ("code comments", "tests", "management claims",
                            "P_FILL labels", "training documentation"),
    },
}

QUEUE_AHEAD_AT_T0_IS_NOT_A_BOUND = (
    "DISPLAYED_QUEUE_AHEAD_AT_T0 is NOT a permanent lower bound on queue "
    "ahead throughout the resting interval. Hidden liquidity can lengthen "
    "the true queue; cancellations ahead can shorten it; additions ahead "
    "can lengthen it again. Both directions matter and the net direction "
    "is NOT_IDENTIFIED from a snapshot")

# Retired label name. Kept so that any reader or grep that goes looking
# for it lands on the reason it is gone rather than on a live code path.
RETIRED_LABELS = {
    "DEFINITELY_NOT_FILLED": (
        "retired. It asserted certainty that a T0 snapshot cannot carry. "
        "The earned negative is COUNTERFACTUAL_NO_FILL_SUPPORTED, which "
        "requires observed queue evolution rather than arithmetic on an "
        "initial displayed size"),
}

# ── §5/§8: the three labels ──────────────────────────────────────────

FILL_SUPPORTED = "COUNTERFACTUAL_FILL_SUPPORTED"
NO_FILL_SUPPORTED = "COUNTERFACTUAL_NO_FILL_SUPPORTED"
FILL_NOT_IDENTIFIED = "COUNTERFACTUAL_FILL_NOT_IDENTIFIED"

LABELS = (FILL_SUPPORTED, NO_FILL_SUPPORTED, FILL_NOT_IDENTIFIED)

# §8: unresolved is not one thing. The label says what we may assert;
# the evidence class says what the row is worth to the P_FILL stage.
POSITIVE_SUPPORTED = "POSITIVE_SUPPORTED"
NEGATIVE_SUPPORTED = "NEGATIVE_SUPPORTED"
INTERVAL_CENSORED = "INTERVAL_CENSORED"
EVIDENCE_NOT_IDENTIFIED = "NOT_IDENTIFIED"

EVIDENCE_CLASSES = (POSITIVE_SUPPORTED, NEGATIVE_SUPPORTED,
                    INTERVAL_CENSORED, EVIDENCE_NOT_IDENTIFIED)

INTERVAL_CENSORED_IS_NOT_A_NEGATIVE = (
    "a row where trading occurred at our price across the resting "
    "interval without resolving our position carries real information "
    "bounded by that interval. It is not a negative and it is not "
    "worthless. Do not discard these rows and do not train on them as "
    "no-fills")

WHY_THREE_LABELS = (
    "queue position is not observable, so many outcomes are genuinely "
    "undecidable. A binary label would turn 'we cannot tell' into "
    "evidence and a model trained on it would learn our guess rather "
    "than the venue's behaviour. The third label is the honest "
    "majority case")

# ── §3: queue is a snapshot AND a process, and they are named apart ──

QUEUE_FIELDS = (
    # what we could see at T0. A snapshot, nothing more.
    "QUEUE_AHEAD_AT_T0",
    # what happened to that queue while the order would have rested
    "QUEUE_AHEAD_DYNAMIC_STATUS",
    "QUEUE_DEPLETION_FROM_TRADES",
    "QUEUE_DEPLETION_FROM_CANCELLATIONS",
    "QUEUE_ADDITION_AHEAD",
    # the two things that decide whether any of it is usable
    "HIDDEN_LIQUIDITY_STATUS",
    "QUEUE_POSITION_STATUS",
)

QUEUE_DYNAMIC_OBSERVED = "OBSERVED_THROUGH_RESTING_INTERVAL"
QUEUE_DYNAMIC_NOT_OBSERVED = "NOT_OBSERVED"

HIDDEN_LIQUIDITY_IDENTIFIED = "IDENTIFIED"
HIDDEN_LIQUIDITY_NOT_IDENTIFIED = "NOT_IDENTIFIED"

PRIORITY_IDENTIFIED = "PRICE_TIME_PRIORITY_IDENTIFIED"
PRIORITY_NOT_IDENTIFIED = "NOT_IDENTIFIED"

QUEUE_POSITION_NEVER_HELD = "NEVER_HELD_BETTOR_WAS_NOT_IN_THE_QUEUE"

WHY_QUEUE_IS_TWO_OBJECTS = (
    "QUEUE_AHEAD_AT_T0 answers 'what was displayed ahead of us when the "
    "order would have arrived'. It does not answer 'what was ahead of "
    "us later', which is what decides a fill. Collapsing the two is the "
    "defect that produced the retracted claim, so they are separate "
    "fields and the dynamic one defaults to NOT_OBSERVED")

# ── §4: the reason code that replaces the manufactured negative ──────

REASON_INITIAL_QUEUE_ONLY = (
    "INITIAL_QUEUE_NOT_DEPLETED_BY_TRADE_VOLUME_BUT_QUEUE_CANCELLATION_"
    "AND_PRIORITY_EVOLUTION_NOT_IDENTIFIED")

# ── §5: what a supported negative actually costs ─────────────────────

NEGATIVE_REQUIRES = (
    "NO_TRADE_THROUGH_AT_OR_BEYOND_OUR_PRICE",
    "QUEUE_AHEAD_DYNAMIC_STATUS == OBSERVED_THROUGH_RESTING_INTERVAL",
    "QUEUE_DEPLETION_FROM_CANCELLATIONS IDENTIFIED",
    "QUEUE_ADDITION_AHEAD IDENTIFIED",
    "HIDDEN_LIQUIDITY_STATUS IDENTIFIED",
    "QUEUE_POSITION_STATUS PRICE_TIME_PRIORITY_IDENTIFIED",
    "EFFECTIVE_QUEUE_AHEAD_NEVER_REACHED_ZERO",
)

WHY_HIDDEN_LIQUIDITY_IS_REQUIRED_FOR_A_NEGATIVE = (
    "it is tempting to argue that unidentified hidden liquidity ahead "
    "only lengthens the queue and so can only strengthen a negative. "
    "That is the shape of the retracted claim -- assuming a sign for an "
    "unobserved quantity -- so it is refused here too. Hidden liquidity "
    "must be IDENTIFIED, not assumed to point our way")

# ── §6: the positive's assumptions, stated rather than implied ───────

TRADE_THROUGH_ASSUMPTIONS = (
    "PRICE_TIME_PRIORITY: the venue fills resting orders at a price in "
    "arrival order, so a print beyond our price implies our level was "
    "exhausted first",
    "CONTINUOUS_REST: the hypothetical order rested unmodified and "
    "uncancelled from arrival until the trade-through print",
    "ADMITTED: the venue would have accepted the order at that price "
    "and size. ORDER_PATH_EXISTS is False, so this is assumed",
    "NO_MARKET_IMPACT: the tape is judged as it occurred, but our order "
    "was not in it. A real order adds displayed size at our level and "
    "may change what other participants do. The counterfactual assumes "
    "it would not, and that assumption is NOT VERIFIED",
    "SIDE_AND_DIRECTION: the print is on the side that would have "
    "consumed our order, beyond our price, not merely near it",
)

TRADE_THROUGH_IS_STILL_COUNTERFACTUAL = (
    "a supported positive is evidence about the venue, not a fill. It "
    "never crosses into ACTUAL_BETTOR_FILL, which requires a real "
    "BETTOR order and no such order exists")

# ── §7: at-price volume settles nothing ──────────────────────────────

AT_PRICE_VOLUME_IS_NOT_A_FILL = (
    "volume printed AT our price is not positive fill evidence. It "
    "establishes that trading occurred at the level, not that it "
    "reached our position in a queue we never joined. Whether we were "
    "reached depends on how much of that volume was ahead of us, which "
    "is exactly the unobserved quantity. At-price volume upgrades a row "
    "from NOT_IDENTIFIED to INTERVAL_CENSORED and no further")

# ── §9: identification is a selection mechanism ──────────────────────

IDENTIFICATION_SELECTION = (
    "rows that resolve are not a random sample of rows written. "
    "Trade-throughs concentrate in fast, informed markets; full queue "
    "evolution is observable mostly in thin ones. A P_FILL model fitted "
    "on resolved rows estimates P(FILL | RESOLVABLE), not P(FILL). The "
    "selection must be modelled or the estimate carries it silently")

IDENTIFICATION_SELECTION_STATUS = "PRESENT_NOT_YET_CORRECTED"

# ── §4 (prior directive): the prospective record, closed at T0 ───────

AT_T0_FIELDS = (
    "ORDER_DECISION_TIMESTAMP",
    "ORDER_ARRIVAL_TIMESTAMP",
    "SIDE",
    "PRICE",
    "QUANTITY",
    "QUEUE_AHEAD_AT_T0",
    "QUEUE_AHEAD_AT_T0_STATUS",
    "QUEUE_POSITION_STATUS",
    "HIDDEN_LIQUIDITY_STATUS",
    "BOOK_AT_ARRIVAL",
    "BOOK_SHA_AT_ARRIVAL",
    "MARKET_STATE_AT_T0",
    "SPREAD_AT_T0",
    "DEPTH_AT_T0",
)

# Appended LATER, keyed to the record above. Never written at T0.
OUTCOME_FIELDS = (
    "FUTURE_TRADE_TAPE",
    "TRADE_THROUGH_EVIDENCE",
    "QUEUE_AHEAD_DYNAMIC_STATUS",
    "QUEUE_DEPLETION_FROM_TRADES",
    "QUEUE_DEPLETION_FROM_CANCELLATIONS",
    "QUEUE_ADDITION_AHEAD",
    "CANCEL_REQUOTE_STATE",
    "COUNTERFACTUAL_FILL_STATUS",
    "EVIDENCE_CLASS",
    "COUNTERFACTUAL_FILLED_QTY",
    "TIME_TO_COUNTERFACTUAL_FILL",
    "ADVERSE_SELECTION_AFTER_FILL",
    "MARKOUTS",
)

SEPARATION_RULE = (
    "ACTION_AVAILABLE_AT_T0 is stored separately from "
    "COUNTERFACTUAL_OUTCOME_LATER. An evaluation that reads the second "
    "while claiming to be the first measures our hindsight rather than "
    "the venue, and the split is what makes that visible")


def _d(v):
    if v is None or v == "" or v == NOT_IDENTIFIED:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def hypothetical_quote(*, decision_ts, arrival_ts, side, price, quantity,
                       book=None, displayed_size_at_level=None,
                       hidden_liquidity_status=None,
                       priority_semantics=None,
                       market_state=None) -> dict:
    """The order BETTOR did not send, recorded before its outcome exists.

    Every field here is knowable at T0. Nothing about the future is
    admitted, and the record is sealed with a sha so a later append
    cannot silently revise it.
    """
    book = book or {}
    qty = _d(quantity)
    px = _d(price)
    queue = _d(displayed_size_at_level)

    record = {
        "object": COUNTERFACTUAL,
        "ORDER_DECISION_TIMESTAMP": decision_ts,
        "ORDER_ARRIVAL_TIMESTAMP": arrival_ts,
        "SIDE": side,
        "PRICE": str(px) if px is not None else NOT_IDENTIFIED,
        "QUANTITY": str(qty) if qty is not None else NOT_IDENTIFIED,
        # §3: A SNAPSHOT, AND ONLY A SNAPSHOT. This is what was displayed
        # ahead of us at arrival. It is NOT a bound on what was ahead of
        # us later, and the field name and its status both say so.
        "QUEUE_AHEAD_AT_T0": (str(queue) if queue is not None
                              else NOT_IDENTIFIED),
        "QUEUE_AHEAD_AT_T0_STATUS": ("DISPLAYED_SNAPSHOT_AT_ARRIVAL"
                                     if queue is not None
                                     else NOT_IDENTIFIED),
        "queueAheadAtT0IsNotABound": QUEUE_AHEAD_AT_T0_IS_NOT_A_BOUND,
        "whyQueueIsTwoObjects": WHY_QUEUE_IS_TWO_OBJECTS,
        # Neither of these is knowable from a displayed book alone, so
        # both default to NOT_IDENTIFIED rather than to a convenient
        # assumption.
        "HIDDEN_LIQUIDITY_STATUS": (hidden_liquidity_status
                                    or HIDDEN_LIQUIDITY_NOT_IDENTIFIED),
        "QUEUE_POSITION_STATUS": (priority_semantics
                                  or PRIORITY_NOT_IDENTIFIED),
        "queuePositionEverHeld": QUEUE_POSITION_NEVER_HELD,
        "BOOK_AT_ARRIVAL": {"bid": book.get("bid"), "ask": book.get("ask"),
                            "mid": book.get("mid")},
        "SPREAD_AT_T0": book.get("spread", NOT_IDENTIFIED),
        "DEPTH_AT_T0": book.get("availableDepth", NOT_IDENTIFIED),
        "MARKET_STATE_AT_T0": (market_state or {}).get("readable",
                                                       NOT_IDENTIFIED),
        # Not yet knowable, and named so the absence is visible.
        "COUNTERFACTUAL_FILL_STATUS": NOT_IDENTIFIED,
        "EVIDENCE_CLASS": NOT_IDENTIFIED,
        "outcomeFieldsAppendedLater": list(OUTCOME_FIELDS),
        "separationRule": SEPARATION_RULE,
        ACTUAL: NOT_IDENTIFIED,
        "neverAnActualFill": NEVER_AN_ACTUAL_FILL,
    }
    record["BOOK_SHA_AT_ARRIVAL"] = hashlib.sha256(
        "|".join(str(record[k]) for k in
                 ("ORDER_ARRIVAL_TIMESTAMP", "SIDE", "PRICE", "QUANTITY",
                  "QUEUE_AHEAD_AT_T0")).encode()).hexdigest()[:16]
    return record


def label_outcome(quote: dict, *, volume_at_or_through_price=None,
                  traded_through=None, cancelled=None,
                  queue_dynamic_status=None,
                  depletion_from_trades=None,
                  depletion_from_cancellations=None,
                  addition_ahead=None,
                  effective_queue_ahead_min=None) -> dict:
    """The counterfactual outcome, appended to a sealed T0 record.

    THREE ANSWERS and FOUR EVIDENCE CLASSES. The third label is not a
    failure to decide -- it is the correct answer when queue position
    governs and queue position is not observed. The negative is EARNED
    from observed queue evolution, never manufactured from the T0
    snapshot.
    """
    queue_t0 = _d(quote.get("QUEUE_AHEAD_AT_T0"))
    hidden = quote.get("HIDDEN_LIQUIDITY_STATUS", NOT_IDENTIFIED)
    priority = quote.get("QUEUE_POSITION_STATUS", NOT_IDENTIFIED)
    dyn = queue_dynamic_status or QUEUE_DYNAMIC_NOT_OBSERVED

    out = {
        "object": COUNTERFACTUAL,
        "BOOK_SHA_AT_ARRIVAL": quote.get("BOOK_SHA_AT_ARRIVAL"),
        "TRADE_THROUGH_EVIDENCE": (traded_through
                                   if traded_through is not None
                                   else NOT_IDENTIFIED),
        # §3: the snapshot and the process, reported side by side and
        # never summed into one "queue" number.
        "QUEUE_AHEAD_AT_T0": quote.get("QUEUE_AHEAD_AT_T0", NOT_IDENTIFIED),
        "QUEUE_AHEAD_DYNAMIC_STATUS": dyn,
        "QUEUE_DEPLETION_FROM_TRADES": (
            str(_d(depletion_from_trades))
            if _d(depletion_from_trades) is not None else NOT_IDENTIFIED),
        "QUEUE_DEPLETION_FROM_CANCELLATIONS": (
            str(_d(depletion_from_cancellations))
            if _d(depletion_from_cancellations) is not None
            else NOT_IDENTIFIED),
        "QUEUE_ADDITION_AHEAD": (
            str(_d(addition_ahead))
            if _d(addition_ahead) is not None else NOT_IDENTIFIED),
        "HIDDEN_LIQUIDITY_STATUS": hidden,
        "QUEUE_POSITION_STATUS": priority,
        "VOLUME_AT_OR_THROUGH_PRICE": (
            str(volume_at_or_through_price)
            if volume_at_or_through_price is not None else NOT_IDENTIFIED),
        "CANCEL_REQUOTE_STATE": (cancelled if cancelled is not None
                                 else NOT_IDENTIFIED),
        ACTUAL: NOT_IDENTIFIED,
        "neverAnActualFill": NEVER_AN_ACTUAL_FILL,
        "whyThreeLabels": WHY_THREE_LABELS,
        "labels": list(LABELS),
        "evidenceClasses": list(EVIDENCE_CLASSES),
        "queueAheadAtT0IsNotABound": QUEUE_AHEAD_AT_T0_IS_NOT_A_BOUND,
        # §9: stamped on every row, resolved or not.
        "IDENTIFICATION_SELECTION_STATUS": IDENTIFICATION_SELECTION_STATUS,
        "identificationSelection": IDENTIFICATION_SELECTION,
    }

    vol = _d(volume_at_or_through_price)

    # ── §6: THE POSITIVE. The tape went THROUGH our price, which a
    # resting order at that price cannot survive under price-time
    # priority. Supported, assumptions named, still not an actual fill.
    if traded_through is True:
        out.update({
            "COUNTERFACTUAL_FILL_STATUS": FILL_SUPPORTED,
            "EVIDENCE_CLASS": POSITIVE_SUPPORTED,
            "why": ("the tape traded THROUGH this price, which cannot "
                    "happen while a resting order at it survives"),
            "assumptions": list(TRADE_THROUGH_ASSUMPTIONS),
            "stillCounterfactual": TRADE_THROUGH_IS_STILL_COUNTERFACTUAL,
        })
        return out

    # ── §5: THE EARNED NEGATIVE. Every one of these must hold. The
    # arithmetic comes LAST and it runs on observed queue evolution,
    # not on the T0 snapshot.
    missing = []
    if traded_through is None:
        missing.append("TRADE_THROUGH_EVIDENCE")
    if dyn != QUEUE_DYNAMIC_OBSERVED:
        missing.append("QUEUE_AHEAD_DYNAMIC_STATUS")
    if _d(depletion_from_cancellations) is None:
        missing.append("QUEUE_DEPLETION_FROM_CANCELLATIONS")
    if _d(addition_ahead) is None:
        missing.append("QUEUE_ADDITION_AHEAD")
    if hidden != HIDDEN_LIQUIDITY_IDENTIFIED:
        missing.append("HIDDEN_LIQUIDITY_STATUS")
    if priority != PRIORITY_IDENTIFIED:
        missing.append("QUEUE_POSITION_STATUS")
    eff = _d(effective_queue_ahead_min)
    if eff is None:
        missing.append("EFFECTIVE_QUEUE_AHEAD_MIN")

    if not missing and traded_through is False and eff > 0:
        out.update({
            "COUNTERFACTUAL_FILL_STATUS": NO_FILL_SUPPORTED,
            "EVIDENCE_CLASS": NEGATIVE_SUPPORTED,
            "EFFECTIVE_QUEUE_AHEAD_MIN": str(eff),
            "why": ("queue evolution was observed through the resting "
                    "interval under identified priority semantics: "
                    "trades, cancellations and additions ahead are all "
                    "accounted for and the effective queue ahead never "
                    "fell below %s. The order was not reached" % eff),
            "requires": list(NEGATIVE_REQUIRES),
            "hiddenLiquidityIsRequired":
                WHY_HIDDEN_LIQUIDITY_IS_REQUIRED_FOR_A_NEGATIVE,
        })
        return out

    # ── §4: THE CASE THE RETRACTED CLAIM USED TO CONVERT. Less volume
    # traded than the T0 displayed queue. That is NOT a negative. It is
    # exactly the counterexample: the queue ahead may have been cancelled
    # away beneath the volume we measured.
    if (vol is not None and queue_t0 is not None and vol < queue_t0
            and missing):
        out.update({
            "COUNTERFACTUAL_FILL_STATUS": FILL_NOT_IDENTIFIED,
            "EVIDENCE_CLASS": INTERVAL_CENSORED,
            "reason": REASON_INITIAL_QUEUE_ONLY,
            "why": ("%s traded at or through this price against a T0 "
                    "displayed queue of %s. That comparison does not "
                    "refute a fill: cancellations ahead of us during the "
                    "resting interval could have depleted the queue "
                    "beneath that volume. The T0 snapshot is not a bound "
                    "on queue ahead later" % (vol, queue_t0)),
            "missingForNegative": missing,
            "retracted": RETRACTED_CLAIMS["QUEUE_AHEAD_IS_A_LOWER_BOUND"],
            "notANegative": INTERVAL_CENSORED_IS_NOT_A_NEGATIVE,
        })
        return out

    # ── everything else. §7: at-price volume is information about the
    # interval, never a positive -- so it separates INTERVAL_CENSORED
    # from NOT_IDENTIFIED without ever reaching a fill label.
    censored = vol is not None and vol > 0
    out.update({
        "COUNTERFACTUAL_FILL_STATUS": FILL_NOT_IDENTIFIED,
        "EVIDENCE_CLASS": (INTERVAL_CENSORED if censored
                           else EVIDENCE_NOT_IDENTIFIED),
        "why": (("the tape traded AT this price but not through it, so "
                 "whether the order would have been reached depends on "
                 "queue position -- which is not identified, because "
                 "BETTOR was never in the queue") if censored else
                ("no evidence bearing on this quote's fate was "
                 "observed during the resting interval")),
        "missingForNegative": missing,
        "atPriceVolumeIsNotAFill": AT_PRICE_VOLUME_IS_NOT_A_FILL,
        "notANegative": INTERVAL_CENSORED_IS_NOT_A_NEGATIVE,
        "notForcedToBinary": WHY_THREE_LABELS,
    })
    return out


def describe() -> dict:
    return {
        "object": COUNTERFACTUAL,
        "distinctFrom": ACTUAL,
        ACTUAL: NOT_IDENTIFIED,
        "orderPathExists": ORDER_PATH_EXISTS,
        "labels": list(LABELS),
        "evidenceClasses": list(EVIDENCE_CLASSES),
        "retiredLabels": dict(RETIRED_LABELS),
        "retractedClaims": dict(RETRACTED_CLAIMS),
        "whyThreeLabels": WHY_THREE_LABELS,
        "queueFields": list(QUEUE_FIELDS),
        "queueAheadAtT0IsNotABound": QUEUE_AHEAD_AT_T0_IS_NOT_A_BOUND,
        "whyQueueIsTwoObjects": WHY_QUEUE_IS_TWO_OBJECTS,
        "negativeRequires": list(NEGATIVE_REQUIRES),
        "hiddenLiquidityIsRequired":
            WHY_HIDDEN_LIQUIDITY_IS_REQUIRED_FOR_A_NEGATIVE,
        "tradeThroughAssumptions": list(TRADE_THROUGH_ASSUMPTIONS),
        "atPriceVolumeIsNotAFill": AT_PRICE_VOLUME_IS_NOT_A_FILL,
        "intervalCensoredIsNotANegative": INTERVAL_CENSORED_IS_NOT_A_NEGATIVE,
        "identificationSelection": IDENTIFICATION_SELECTION,
        "atT0Fields": list(AT_T0_FIELDS),
        "outcomeFields": list(OUTCOME_FIELDS),
        "separationRule": SEPARATION_RULE,
        "neverAnActualFill": NEVER_AN_ACTUAL_FILL,
    }
