"""§C. IS THE FROZEN LABEL CONTRACT SATISFIABLE FROM PMUS PUBLIC FEEDS?

Owner directive, "GO on the narrow next step" §C:

    "Once depth and one real tape sample are in hand, build a simple
    evidence matrix: REQUIRED FACT -> SOURCE -> OBSERVED? -> EXACT /
    DERIVED / ASSUMED -> SUFFICIENT FOR POSITIVE? -> SUFFICIENT FOR
    NEGATIVE? -> LIMITATION. Specifically test every requirement of
    COUNTERFACTUAL_FILL_SUPPORTED and COUNTERFACTUAL_NO_FILL_SUPPORTED.
    If a required fact can never be observed from these feeds, do NOT
    loosen the contract. Report LABEL_CONTRACT_NOT_IDENTIFIABLE_FROM_
    CURRENT_FEEDS and name the exact missing fact."

THE MATRIX IS GRADED AGAINST TWO MEASURED FEEDS, not against a
specification:

    BOOK SNAPSHOT   pmus book feed, measured 2026-09-20. Carries
                    bids[]/offers[] with px and qty, depth level
                    counts, stats.sharesTraded, state, transactTime.
    PUBLIC TAPE     www.polymarketexchange.com time-and-sales, fetched
                    2026-09-20 (TAPE_FETCH_STATUS = PROVEN). 326 daily
                    files, 20251029..20260919. Columns exactly
                    Transaction Time | Symbol | Last Price | Last
                    Quantity. 767,797+ rows on one day, 102,875+
                    symbols, nanosecond timestamps, NO side, NO
                    aggressor, NO participant.

────────────────────────────────────────────────────────────────────
THE GRADE IS NOT A SCORE. "OBSERVED" means the feed publishes the
field. "EXACT" means we read the venue's own value; "DERIVED" means we
computed it from values the venue published; "ASSUMED" means we
supplied it and the venue did not. An ASSUMED fact can still carry a
label -- the trade-through positive rests on price-time priority, which
is assumed -- but it must be visible as assumed, because a row of
assumptions produces a label that measures our beliefs.
────────────────────────────────────────────────────────────────────

WHAT THE MEASUREMENT CHANGED. Before the fetch, two facts were
unknown and are now settled, and one was assumed and turned out false:

    SETTLED   the tape exists, downloads, parses, and its header
              matches its documentation exactly.
    SETTLED   block trades are NOT published row-by-row
              (block-trade-data.html is HTTP 404), so tape volume
              stays an UPPER BOUND on CLOB volume -- a block executes
              apart from the book and depletes no queue.
    FALSIFIED the assumption that tape prices live on the book's cent
              grid. 350,153 of 767,797 prints on 2026-09-19 carry
              THREE decimal places. A three-decimal print cannot be
              equated to a two-decimal quote without a matching rule
              nobody has stated, and equating them would attribute
              volume to a price level that did not trade.

NOTHING HERE LOOSENS THE CONTRACT. Where a required fact is not
obtainable, the matrix says so and the verdict degrades. It never
edits the requirement.
"""

from __future__ import annotations

from . import bettor_shadow_execution as sx

NOT_IDENTIFIED = "NOT_IDENTIFIED"

MATRIX_VERSION = "BETTOR_EVIDENCE_MATRIX_V1"

# ── the two feeds, as MEASURED ───────────────────────────────────────

SOURCE_BOOK = "PMUS_BOOK_SNAPSHOT"
SOURCE_TAPE = "PMUS_PUBLIC_TIME_AND_SALES"
SOURCE_NONE = "NO_FEED_PUBLISHES_THIS"

FEEDS_MEASURED = {
    SOURCE_BOOK: {
        "measuredAt": "2026-09-20",
        "carries": ("bids[].px", "bids[].qty", "offers[].px",
                    "offers[].qty", "depth level counts",
                    "stats.sharesTraded", "state", "transactTime"),
        "doesNotCarry": ("order identity", "queue position",
                         "hidden or reserve size",
                         "per-order cancellations"),
        "cadence": "one snapshot per market per 300s bucket",
    },
    SOURCE_TAPE: {
        "measuredAt": "2026-09-20",
        "fetchStatus": "PROVEN",
        "files": 326,
        "dateRange": "20251029..20260919",
        "columns": ("Transaction Time", "Symbol", "Last Price",
                    "Last Quantity"),
        "carries": ("per-print time to nanosecond", "market symbol",
                    "print price", "print quantity"),
        "doesNotCarry": ("side", "aggressor", "participant identity",
                         "order id", "queue position",
                         "block-trade flag"),
        "publicationLagUpperBoundHours": 42.62,
        "blockTradesPublishedRowByRow": False,
    },
}

# ── the measured facts that decide the verdicts ──────────────────────

PRICE_GRID_MISMATCH = {
    "finding": "TAPE_PRICES_ARE_NOT_ALL_ON_THE_BOOK_CENT_GRID",
    "measured": {"twoDecimalPrints": 417644, "threeDecimalPrints": 350153,
                 "file": "20260919-time-and-sales.csv"},
    "why": ("matching 'volume AT our price' requires the tape's price "
            "and the book's price to be the same object. 46% of prints "
            "on the measured day carry three decimals where the book "
            "quotes cents. Equating them needs a matching rule the "
            "venue has not stated, and assuming one would attribute "
            "volume to a level that did not trade"),
    "isNotFixableByRounding": (
        "rounding 0.525 to 0.52 or 0.53 decides which resting order it "
        "consumed. That is the whole question, answered by our choice"),
}

BLOCK_TRADES_NOT_SEPARABLE = {
    "finding": "BLOCK_PRINTS_CANNOT_BE_EXCLUDED_ROW_BY_ROW",
    "measured": {"blockTradePageHttpStatus": 404},
    "why": ("a block trade executes apart from the public order book "
            "and depletes no queue. The tape carries no block flag and "
            "the block publication page is 404, so every symbol-day's "
            "tape volume is an UPPER BOUND on the volume that actually "
            "consumed book liquidity"),
}

NO_AGGRESSOR = {
    "finding": "TAPE_CARRIES_NO_SIDE_AND_NO_AGGRESSOR",
    "measured": {"columns": 4, "sideColumn": False,
                 "aggressorColumn": False},
    "why": ("a print at our price consumed either a resting bid or a "
            "resting offer. Without the aggressor we cannot say which "
            "side's queue it depleted, so volume at a price cannot be "
            "attributed to OUR side of it"),
}

CANCELLATIONS_NOT_OBSERVABLE = {
    "finding": "CANCELLATIONS_CANNOT_BE_SEPARATED_FROM_TRADES",
    "measured": {"bookSnapshotCadenceSeconds": 300,
                 "perOrderEventsPublished": False},
    "why": ("the book publishes SNAPSHOTS, not order events. A level "
            "that shrank between two snapshots shrank by some mix of "
            "trades and cancellations, and the tape's volume is "
            "market-wide and sideless, so the mix cannot be recovered. "
            "QUEUE_DEPLETION_FROM_CANCELLATIONS is unobservable from "
            "these feeds at any cadence"),
}

# ── grades ───────────────────────────────────────────────────────────

EXACT = "EXACT"
DERIVED = "DERIVED"
ASSUMED = "ASSUMED"
UNOBSERVABLE = "UNOBSERVABLE_FROM_THESE_FEEDS"

YES, NO, PARTIAL = "YES", "NO", "PARTIALLY"


def _row(fact, source, observed, grade, positive, negative, limitation):
    return {
        "REQUIRED_FACT": fact,
        "SOURCE": source,
        "OBSERVED": observed,
        "GRADE": grade,
        "SUFFICIENT_FOR_POSITIVE": positive,
        "SUFFICIENT_FOR_NEGATIVE": negative,
        "LIMITATION": limitation,
    }


# ── COUNTERFACTUAL_FILL_SUPPORTED, requirement by requirement ────────

POSITIVE_MATRIX = (
    _row("TRADE_PRINTED_THROUGH_OUR_PRICE", SOURCE_TAPE, True, DERIVED,
         PARTIAL, NO,
         "derived by comparing print price to our hypothetical price. "
         "A print BEYOND our price is unambiguous on the grid where "
         "both are cents; 46% of prints carry three decimals and no "
         "venue-stated matching rule maps them onto the book's grid"),
    # CORRECTED ON REVIEW. My first grading called this UNOBSERVABLE
    # because the tape carries no aggressor. That was too strict, and
    # in the direction that makes the answer look worse than it is.
    # Under PRICE priority a print STRICTLY BEYOND our price could not
    # have occurred while our order rested, whichever side initiated
    # it: a seller who accepted 0.47 while our 0.48 bid was available
    # took a worse price than the book offered. The aggressor is
    # needed to attribute volume AT our price; it is not needed for a
    # print BEYOND it.
    _row("PRINT_WAS_ON_THE_SIDE_THAT_WOULD_CONSUME_US", SOURCE_TAPE,
         True, DERIVED, PARTIAL, NO,
         "derived from the PRICE relationship, not from an aggressor "
         "flag: a print strictly beyond our price is inconsistent with "
         "our order resting, under price priority. This reasoning does "
         "NOT extend to prints AT our price, where the aggressor is "
         "required and absent"),
    _row("PRINT_WAS_A_CLOB_EXECUTION_NOT_A_BLOCK", SOURCE_NONE, False,
         UNOBSERVABLE, NO, NO,
         "no block flag on the tape and block-trade-data.html is 404. "
         "A block depletes no queue, so a block print beyond our price "
         "is not evidence our level was cleared"),
    _row("PRICE_TIME_PRIORITY_HELD_FOR_THIS_CONTRACT", SOURCE_NONE,
         False, ASSUMED, PARTIAL, PARTIAL,
         "the venue documents price-time priority and its Rulebook "
         "permits a per-contract override after notice. "
         "CHECK_PRODUCT_SPECIFIC_MATCHING_NOTICE is REQUIRED and has "
         "not been performed"),
    _row("ORDER_RESTED_CONTINUOUSLY_THROUGH_THE_INTERVAL", SOURCE_NONE,
         False, ASSUMED, PARTIAL, PARTIAL,
         "the order is hypothetical. Nothing observable can confirm it "
         "would not have been cancelled, repriced or rejected"),
    _row("OUR_PRESENCE_WOULD_NOT_HAVE_CHANGED_THE_TAPE", SOURCE_NONE,
         False, ASSUMED, PARTIAL, PARTIAL,
         "a real order adds displayed size at our level. The tape is "
         "judged as it occurred, without us in it. NOT VERIFIED"),
    # NOT A REQUIREMENT OF THIS LABEL, and listed so a reader does not
    # look for it. sx.FILL_SUPPORTED is produced from trade-through
    # alone: if the tape printed beyond our price, our order was
    # reached whatever sat in front of it. Queue-ahead is required by
    # the VOLUME-based positives (F0/F1), which this contract does not
    # admit, and by the negative.
    _row("QUEUE_AHEAD_AT_T0", SOURCE_BOOK, True, DERIVED,
         "NOT_REQUIRED_FOR_THIS_LABEL", PARTIAL,
         "displayed size at our price at the snapshot, under four "
         "stated assumptions. Hidden or reserve size would sit ahead "
         "of us undetected and is not published"),
)

# ── COUNTERFACTUAL_NO_FILL_SUPPORTED, requirement by requirement ─────
#
# sx.NEGATIVE_REQUIRES names seven conditions. Each is graded here
# against the same two feeds.

NEGATIVE_MATRIX = (
    _row("NO_TRADE_THROUGH_AT_OR_BEYOND_OUR_PRICE", SOURCE_TAPE, True,
         DERIVED, NO, PARTIAL,
         "absence of a through-print is derivable, but only as far as "
         "the price-grid mismatch allows, and a truncated file read "
         "makes any 'no print occurred' claim a claim about the part "
         "we read"),
    _row("QUEUE_AHEAD_DYNAMIC_STATUS_OBSERVED_THROUGH_INTERVAL",
         SOURCE_NONE, False, UNOBSERVABLE, NO, NO,
         "the book publishes snapshots on a 300s bucket, not order "
         "events. Evolution BETWEEN snapshots is not observed at any "
         "cadence this feed offers"),
    _row("QUEUE_DEPLETION_FROM_CANCELLATIONS_IDENTIFIED", SOURCE_NONE,
         False, UNOBSERVABLE, NO, NO,
         "THE BINDING ONE. A level that shrank between two snapshots "
         "shrank by some mix of trades and cancellations. The tape's "
         "volume is market-wide and sideless, so the mix cannot be "
         "recovered by subtraction either"),
    _row("QUEUE_ADDITION_AHEAD_IDENTIFIED", SOURCE_NONE, False,
         UNOBSERVABLE, NO, NO,
         "an order that joined ahead of us and left between snapshots "
         "is invisible; so is one that joined behind and was cancelled"),
    _row("HIDDEN_LIQUIDITY_STATUS_IDENTIFIED", SOURCE_NONE, False,
         UNOBSERVABLE, NO, NO,
         "the venue publishes displayed size only. Whether reserve or "
         "iceberg size exists at a level is not stated either way"),
    _row("QUEUE_POSITION_STATUS_PRICE_TIME_PRIORITY_IDENTIFIED",
         SOURCE_NONE, False, ASSUMED, PARTIAL, PARTIAL,
         "documented generally, overridable per contract after notice, "
         "and the notice check has not been performed"),
    _row("EFFECTIVE_QUEUE_AHEAD_NEVER_REACHED_ZERO", SOURCE_NONE,
         False, UNOBSERVABLE, NO, NO,
         "a function of the four unobservable rows above. It cannot be "
         "computed from quantities that are not published"),
)

# ── the verdict, derived from the matrix rather than asserted ────────

CONTRACT_NOT_IDENTIFIABLE = "LABEL_CONTRACT_NOT_IDENTIFIABLE_FROM_CURRENT_FEEDS"


def _verdict(matrix, key):
    """A label class is identifiable only if no required fact is
    UNOBSERVABLE. An ASSUMED fact degrades it to PARTIALLY."""
    unobservable = [r["REQUIRED_FACT"] for r in matrix
                    if r["GRADE"] == UNOBSERVABLE]
    assumed = [r["REQUIRED_FACT"] for r in matrix
               if r["GRADE"] == ASSUMED]
    if unobservable:
        return NO, unobservable, assumed
    return (PARTIAL if assumed else YES), unobservable, assumed


def positive_identifiability() -> dict:
    v, unobs, assumed = _verdict(POSITIVE_MATRIX, "positive")
    return {
        "label": sx.FILL_SUPPORTED,
        "POSITIVE_LABEL_IDENTIFIABILITY": v,
        "unobservableRequiredFacts": unobs,
        "assumedRequiredFacts": assumed,
        "status": CONTRACT_NOT_IDENTIFIABLE if v == NO else "IDENTIFIABLE",
        "why": (
            "a print beyond our price IS derivable, and under price "
            "priority its side is derivable too. Exactly one required "
            "fact is unobservable: whether the print was a CLOB "
            "execution or a block. A block executes away from the book "
            "and depletes no queue, the tape carries no block flag, "
            "and block-trade-data.html is 404 -- so a through-print "
            "cannot be shown to have cleared our level"),
        "theSingleMissingFact": "PRINT_WAS_A_CLOB_EXECUTION_NOT_A_BLOCK",
    }


def negative_identifiability() -> dict:
    v, unobs, assumed = _verdict(NEGATIVE_MATRIX, "negative")
    return {
        "label": sx.NO_FILL_SUPPORTED,
        "NEGATIVE_LABEL_IDENTIFIABILITY": v,
        "unobservableRequiredFacts": unobs,
        "assumedRequiredFacts": assumed,
        "status": CONTRACT_NOT_IDENTIFIABLE if v == NO else "IDENTIFIABLE",
        "why": (
            "the negative needs queue EVOLUTION and the book publishes "
            "SNAPSHOTS. Cancellations cannot be separated from trades "
            "by subtraction because the tape's volume is market-wide "
            "and sideless, so FIVE of the seven required facts are "
            "unobservable rather than merely unmeasured"),
    }


def unobservable_required_facts() -> dict:
    """Every fact no PMUS public feed publishes, named once."""
    facts = {}
    for m in (POSITIVE_MATRIX, NEGATIVE_MATRIX):
        for r in m:
            if r["GRADE"] == UNOBSERVABLE:
                facts[r["REQUIRED_FACT"]] = r["LIMITATION"]
    return facts


def what_would_change_it() -> dict:
    """What each unobservable fact would take. Not a plan -- a bound.

    None of these is a change to the contract. Every one is a change to
    the EVIDENCE, which is the only honest way to move a verdict.
    """
    return {
        "PRINT_WAS_ON_THE_SIDE_THAT_WOULD_CONSUME_US": (
            "an aggressor or side column on the tape, or a venue "
            "statement that one can be derived. Neither exists"),
        "PRINT_WAS_A_CLOB_EXECUTION_NOT_A_BLOCK": (
            "row-level block publication. block-trade-data.html is 404"),
        "QUEUE_DEPLETION_FROM_CANCELLATIONS_IDENTIFIED": (
            "an order-event feed, or book snapshots fast enough that "
            "between-snapshot evolution is bounded to a single event. "
            "The second is not a feed change we can make -- it is a "
            "request rate against a venue we pace deliberately, and "
            "even then two snapshots bracketing one event still cannot "
            "say whether the event was a trade or a cancel without the "
            "tape's side"),
        "HIDDEN_LIQUIDITY_STATUS_IDENTIFIED": (
            "a venue statement that reserve orders do or do not exist "
            "on this book. Not published either way"),
        "THE_ONE_THAT_WOULD_SETTLE_EVERYTHING": (
            "BETTOR resting a real order and reading its own admitted "
            "executions. That is ACTUAL_BETTOR_FILL, it requires an "
            "order path and capital, and it is out of scope"),
    }


def matrix() -> dict:
    pos, neg = positive_identifiability(), negative_identifiability()
    return {
        "matrixVersion": MATRIX_VERSION,
        "feedsMeasured": dict(FEEDS_MEASURED),
        "POSITIVE_MATRIX": [dict(r) for r in POSITIVE_MATRIX],
        "NEGATIVE_MATRIX": [dict(r) for r in NEGATIVE_MATRIX],
        "POSITIVE_LABEL_IDENTIFIABILITY": pos,
        "NEGATIVE_LABEL_IDENTIFIABILITY": neg,
        "UNOBSERVABLE_REQUIRED_FACTS": unobservable_required_facts(),
        "measuredFindings": {
            "PRICE_GRID_MISMATCH": dict(PRICE_GRID_MISMATCH),
            "BLOCK_TRADES_NOT_SEPARABLE": dict(BLOCK_TRADES_NOT_SEPARABLE),
            "NO_AGGRESSOR": dict(NO_AGGRESSOR),
            "CANCELLATIONS_NOT_OBSERVABLE": dict(
                CANCELLATIONS_NOT_OBSERVABLE),
        },
        "whatWouldChangeIt": what_would_change_it(),
        "contractUnchanged": (
            "no requirement was edited to reach these verdicts. Where a "
            "fact is not obtainable the verdict degrades and the fact "
            "is named; the contract is not loosened to produce labels"),
        "theThirdLabelStillWorks": (
            "COUNTERFACTUAL_FILL_NOT_IDENTIFIED, with EVIDENCE_CLASS "
            "INTERVAL_CENSORED, remains fully producible from these "
            "feeds and is not a failure -- it is the honest answer for "
            "a quote whose fate these feeds cannot decide"),
    }


def describe() -> dict:
    return matrix()
