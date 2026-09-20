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

THREE LABELS, NOT TWO (§5). Queue position is not observable -- we were
never in the queue -- so many outcomes are genuinely undecidable:

    DEFINITELY_NOT_FILLED           arithmetic: the volume that traded
                                    at or through our price while we
                                    rested was below the queue ahead of
                                    us. No model saves it.
    COUNTERFACTUAL_FILL_SUPPORTED   the tape traded THROUGH our price,
                                    which cannot happen while a
                                    resting order at that price
                                    survives.
    COUNTERFACTUAL_FILL_NOT_IDENTIFIED  the tape traded AT our price
                                    but not through it. Whether we
                                    would have been reached depends on
                                    queue position, which is not
                                    identified.

FORCING THE THIRD INTO YES OR NO IS THE WHOLE TRAP. A binary label
would turn "we cannot tell" into evidence, and a fill model trained on
it would learn our guess rather than the venue's behaviour. The third
label is the honest majority case and it stays.

QUEUE-AHEAD IS A LOWER BOUND. It is measured from DISPLAYED size at our
level at insert, so hidden liquidity can only make the true queue
longer. That makes DEFINITELY_NOT_FILLED conservative -- it fires less
often than the truth would justify, never more.

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

# ── §5: the three labels ─────────────────────────────────────────────

DEFINITELY_NOT_FILLED = "DEFINITELY_NOT_FILLED"
FILL_SUPPORTED = "COUNTERFACTUAL_FILL_SUPPORTED"
FILL_NOT_IDENTIFIED = "COUNTERFACTUAL_FILL_NOT_IDENTIFIED"

LABELS = (DEFINITELY_NOT_FILLED, FILL_SUPPORTED, FILL_NOT_IDENTIFIED)

WHY_THREE_LABELS = (
    "queue position is not observable, so many outcomes are genuinely "
    "undecidable. A binary label would turn 'we cannot tell' into "
    "evidence and a model trained on it would learn our guess rather "
    "than the venue's behaviour. The third label is the honest "
    "majority case")

QUEUE_AHEAD_IS_A_LOWER_BOUND = (
    "queue-ahead is measured from DISPLAYED size at our level at "
    "insert, so hidden liquidity can only lengthen the true queue. "
    "DEFINITELY_NOT_FILLED is therefore conservative: it fires less "
    "often than the truth would justify, never more")

# ── §4: the prospective record, closed at T0 ─────────────────────────

AT_T0_FIELDS = (
    "ORDER_DECISION_TIMESTAMP",
    "ORDER_ARRIVAL_TIMESTAMP",
    "SIDE",
    "PRICE",
    "QUANTITY",
    "QUEUE_AHEAD_ESTIMATE",
    "QUEUE_AHEAD_STATUS",
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
    "QUEUE_DEPLETION_EVIDENCE",
    "CANCEL_REQUOTE_STATE",
    "COUNTERFACTUAL_FILL_STATUS",
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
        # THE QUEUE WE WERE NEVER IN. Displayed size at our level is the
        # best available proxy and it is a LOWER bound, named as one.
        "QUEUE_AHEAD_ESTIMATE": (str(queue) if queue is not None
                                 else NOT_IDENTIFIED),
        "QUEUE_AHEAD_STATUS": ("LOWER_BOUND_FROM_DISPLAYED_SIZE"
                               if queue is not None else NOT_IDENTIFIED),
        "queueAheadIsALowerBound": QUEUE_AHEAD_IS_A_LOWER_BOUND,
        "BOOK_AT_ARRIVAL": {"bid": book.get("bid"), "ask": book.get("ask"),
                            "mid": book.get("mid")},
        "SPREAD_AT_T0": book.get("spread", NOT_IDENTIFIED),
        "DEPTH_AT_T0": book.get("availableDepth", NOT_IDENTIFIED),
        "MARKET_STATE_AT_T0": (market_state or {}).get("readable",
                                                       NOT_IDENTIFIED),
        # Not yet knowable, and named so the absence is visible.
        "COUNTERFACTUAL_FILL_STATUS": NOT_IDENTIFIED,
        "outcomeFieldsAppendedLater": list(OUTCOME_FIELDS),
        "separationRule": SEPARATION_RULE,
        ACTUAL: NOT_IDENTIFIED,
        "neverAnActualFill": NEVER_AN_ACTUAL_FILL,
    }
    record["BOOK_SHA_AT_ARRIVAL"] = hashlib.sha256(
        "|".join(str(record[k]) for k in
                 ("ORDER_ARRIVAL_TIMESTAMP", "SIDE", "PRICE", "QUANTITY",
                  "QUEUE_AHEAD_ESTIMATE")).encode()).hexdigest()[:16]
    return record


def label_outcome(quote: dict, *, volume_at_or_through_price=None,
                  traded_through=None, cancelled=None) -> dict:
    """The counterfactual outcome, appended to a sealed T0 record.

    THREE ANSWERS, and the third is not a failure to decide -- it is the
    correct answer when queue position governs and queue position is
    not observable.
    """
    out = {
        "object": COUNTERFACTUAL,
        "BOOK_SHA_AT_ARRIVAL": quote.get("BOOK_SHA_AT_ARRIVAL"),
        "TRADE_THROUGH_EVIDENCE": (traded_through
                                   if traded_through is not None
                                   else NOT_IDENTIFIED),
        "QUEUE_DEPLETION_EVIDENCE": (
            str(volume_at_or_through_price)
            if volume_at_or_through_price is not None else NOT_IDENTIFIED),
        "CANCEL_REQUOTE_STATE": (cancelled if cancelled is not None
                                 else NOT_IDENTIFIED),
        ACTUAL: NOT_IDENTIFIED,
        "neverAnActualFill": NEVER_AN_ACTUAL_FILL,
        "whyThreeLabels": WHY_THREE_LABELS,
        "labels": list(LABELS),
    }

    vol = _d(volume_at_or_through_price)
    queue = _d(quote.get("QUEUE_AHEAD_ESTIMATE"))

    # THE TAPE WENT THROUGH OUR PRICE. A resting order at that price
    # cannot survive a trade through it, so the fill is SUPPORTED --
    # which is still not an actual fill, because no order rested there.
    if traded_through is True:
        out.update({
            "COUNTERFACTUAL_FILL_STATUS": FILL_SUPPORTED,
            "why": ("the tape traded THROUGH this price, which cannot "
                    "happen while a resting order at it survives"),
        })
        return out

    # ARITHMETIC REFUTATION. Less volume than the queue ahead of us
    # means we were never reached, whatever the queue model.
    if vol is not None and queue is not None and vol < queue:
        out.update({
            "COUNTERFACTUAL_FILL_STATUS": DEFINITELY_NOT_FILLED,
            "why": ("only %s traded at or through this price while the "
                    "order rested, against a queue of at least %s ahead "
                    "of it. No queue model reaches it" % (vol, queue)),
            "isConservative": QUEUE_AHEAD_IS_A_LOWER_BOUND,
        })
        return out

    out.update({
        "COUNTERFACTUAL_FILL_STATUS": FILL_NOT_IDENTIFIED,
        "why": ("the tape traded AT this price but not through it, so "
                "whether the order would have been reached depends on "
                "queue position -- which is not identified, because "
                "BETTOR was never in the queue"),
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
        "whyThreeLabels": WHY_THREE_LABELS,
        "atT0Fields": list(AT_T0_FIELDS),
        "outcomeFields": list(OUTCOME_FIELDS),
        "separationRule": SEPARATION_RULE,
        "queueAheadIsALowerBound": QUEUE_AHEAD_IS_A_LOWER_BOUND,
        "neverAnActualFill": NEVER_AN_ACTUAL_FILL,
    }
