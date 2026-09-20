"""TWO QUANTITIES THAT ARE NOT THE SAME QUANTITY.

Owner review 2026-09-20 §1:

    "institutional_book.FRESHNESS_LIMIT_S = 5.0s answers 'HOW OLD MAY
    THE BOOK ITSELF BE WHEN WE USE IT?' It does NOT answer 'HOW LONG
    AFTER THE TARGET EXIT TIME MAY WE WAIT FOR A BOOK?'"

THE DEFECT IN V3, WHICH WAS MINE. V3 set
INITIAL_EXIT_MAX_DELAY = 5,000ms and justified it with
FRESHNESS_LIMIT_S. Those are orthogonal:

    TARGET_EXIT           10:00:00
    BOOK_SOURCE           10:00:19
    BOOK_RECEIVED         10:00:20
    BOOK_AGE               1s   -> FRESH, admissible
    EXIT_OBSERVATION_DELAY 20s  -> outside V3's window

A book can be perfectly fresh and arrive twenty seconds late. V3's
window would have refused it -- not because the evidence was stale,
but because I had applied a staleness rule to a waiting time. The two
checks are independent and V4 keeps them independent.

WHY NO DELAY BOUND IS DECLARED AT ALL. §5: the collector gives no
guaranteed finite target-to-next-fresh-book interval. SWEEP_S = 2.0s
with 8 sequential reads and a 30s read timeout means the normal cycle
is ~3.13s and the theoretical slow cycle is hundreds of seconds. There
is no operational basis for any particular ceiling, so none is
invented:

    MAX_EXIT_OBSERVATION_DELAY        = NOT_ESTABLISHED
    RESIDUAL_RETRY_MAX_INTERBOOK_DELAY = NOT_ESTABLISHED

THAT IS NOT A LICENCE TO NEVER EXIT. The exit obligation waits until
the first admissible fresh book actually appears, and the waiting is
recorded rather than hidden. §6: a data outage is an economic fact.
Capital stays deployed, the position is not closed, no realized P&L is
generated, and the time spent waiting lands in ACTUAL_HOLD_TIME and
CAPITAL_HOURS. A strategy whose intended 60-second exit took 180
seconds because the data path was unavailable must SHOW that, not
delete the trade from its performance.
"""

from __future__ import annotations

# ── the two checks, kept apart by name ───────────────────────────────

BOOK_FRESHNESS_LIMIT_MS = 5000          # institutional_book.FRESHNESS_LIMIT_S
NOT_ESTABLISHED = "NOT_ESTABLISHED"

MAX_EXIT_OBSERVATION_DELAY = NOT_ESTABLISHED
RESIDUAL_RETRY_MAX_INTERBOOK_DELAY = NOT_ESTABLISHED

BOOK_ADMISSIBILITY_RULE = (
    "a book is admissible only if it independently satisfies the "
    "existing freshness contract: BOOK_AGE <= %dms, where BOOK_AGE is "
    "computed from the BOOK'S OWN source and receipt timestamps. This "
    "is a property of the book and of nothing else -- it does not "
    "depend on when the target was, and it never stands in for a "
    "waiting-time bound" % BOOK_FRESHNESS_LIMIT_MS)

TARGET_DELAY_RULE = (
    "NO MAXIMUM TARGET-TO-BOOK DELAY IS ESTABLISHED. The collector "
    "offers no guaranteed finite interval between a target instant and "
    "the next fresh book: SWEEP_S = 2.0s over 8 sequential reads with "
    "a 30s read timeout gives a ~3.13s normal cycle and a theoretical "
    "slow cycle in the hundreds of seconds. No operational basis "
    "exists for any particular ceiling, so none is declared. The exit "
    "obligation WAITS for the first admissible fresh book and the "
    "delay is recorded, never bounded by invention and never hidden")

# ── §3: the initial exit ─────────────────────────────────────────────

EXIT_ANCHOR = "ARRIVAL"
EXIT_HORIZON_S = 60

TARGET_EXIT_RULE = (
    "TARGET_EXIT_TIMESTAMP = ENTRY_ARRIVAL_TIMESTAMP + %ds. At that "
    "instant the position becomes EXIT_PENDING" % EXIT_HORIZON_S)

INITIAL_EXIT_RULE = (
    "at TARGET_EXIT_TIMESTAMP the position becomes EXIT_PENDING. The "
    "exit then uses THE FIRST LEGITIMATELY OBSERVED FRESH BOOK AT OR "
    "AFTER TARGET_EXIT_TIMESTAMP -- 'fresh' meaning it independently "
    "satisfies BOOK_AGE <= %dms from its own timestamps. There is no "
    "requirement that the book arrive within any particular time of "
    "the target: the two checks are separate, and only the freshness "
    "one is established. SELL the same leg by marketable "
    "reconstruction against the BID side of that book"
    % BOOK_FRESHNESS_LIMIT_MS)

# ── §6: a data outage is an economic fact, not a deleted trade ───────

EXIT_PENDING_DATA = "EXIT_PENDING_DATA"

DATA_OUTAGE_RULE = (
    "if no admissible fresh book exists at the target, the position is "
    "%s. Capital REMAINS DEPLOYED. The position is not closed, no "
    "realized P&L is generated, and nothing is interpolated. When the "
    "next admissible fresh book eventually arrives the marketable exit "
    "is attempted then. The waiting time is part of ACTUAL_HOLD_TIME, "
    "CAPITAL_HOURS and EXIT_OBSERVATION_DELAY -- a strategy whose "
    "intended 60s exit took 180s because the data path was unavailable "
    "shows that fact rather than deleting the trade from performance"
    % EXIT_PENDING_DATA)

# ── §7: partials and the residual ────────────────────────────────────

PARTIAL_EXIT_RULE = (
    "at the first admissible fresh book, INTENDED_EXIT_QTY = ALL "
    "REMAINING OPEN QUANTITY. Walk the BID book and fill ONLY ACTUAL "
    "OBSERVED DEPTH. If FILLED_QTY < REMAINING_QTY record PARTIAL_EXIT "
    "and REMAINING_QTY = prior remaining - filled. Depth is never "
    "invented to force a full exit")

RESIDUAL_RULE = (
    "the residual remains EXIT_PENDING -- an exit obligation, not "
    "discretionary holding. It is never re-classified as a held "
    "position and its continued existence is never read as a decision "
    "to hold")

# ── §8: the residual retry, with no interbook ceiling ────────────────

RETRY_RULE = (
    "attempt the residual against EVERY SUBSEQUENT LEGITIMATELY "
    "OBSERVED FRESH BOOK in arrival order, each judged by the same "
    "independent freshness check. NO MAXIMUM INTERBOOK DELAY IS "
    "ESTABLISHED, for the same reason none is established for the "
    "initial exit: the %dms rule governs BOOK FRESHNESS, not time "
    "between books. The residual remains an exit obligation until "
    "REMAINING_QTY = 0 -- at which point POSITION_CLOSED is appended "
    "-- or until another predeclared terminal condition applies"
    % BOOK_FRESHNESS_LIMIT_MS)

# ── §9: market close, settlement still unresolved ────────────────────

EXIT_INCOMPLETE_AT_CLOSE = "EXIT_INCOMPLETE_AT_MARKET_CLOSE"

MARKET_CLOSE_RULE = (
    "a residual outstanding at market close is recorded as %s with the "
    "residual quantity preserved. No marketable exit is manufactured, "
    "and it is NOT automatically converted into settled P&L while this "
    "venue's settlement semantics remain CONFLICTING_VENUE_PROSE. That "
    "unresolved prose is kept separate from instrument identity"
    % EXIT_INCOMPLETE_AT_CLOSE)

# ── §4: the evidence every attempt preserves ─────────────────────────
#
# EXIT_OBSERVATION_DELAY_MS is recorded WHATEVER IT IS. 2 seconds, 20
# seconds or 70 seconds: the number is the finding. "Do not relabel it
# as a +60s execution."

EXIT_EVIDENCE_FIELDS = (
    "TARGET_EXIT_TIMESTAMP",
    "EXIT_BOOK_SOURCE_TIMESTAMP",
    "EXIT_BOOK_RECEIVED_TIMESTAMP",
    "BOOK_AGE_MS",
    "EXIT_OBSERVATION_DELAY_MS",
    "EXIT_BOOK_SHA",
    "EXIT_INTENDED_QTY",
    "EXIT_AVAILABLE_QTY",
    "EXIT_FILLED_QTY",
    "EXIT_REMAINING_QTY",
    "EXIT_VWAP",
    "EXIT_EXECUTION_STATUS",
    "ACTUAL_HOLD_TIME_MS",
)

RESIDUAL_EVIDENCE_FIELDS = (
    "PREVIOUS_ATTEMPT_TIMESTAMP",
    "NEXT_BOOK_RECEIVED_TIMESTAMP",
    "INTERBOOK_DELAY_MS",
    "BOOK_AGE_MS",
    "EXIT_ATTEMPT_NO",
)

DELAY_DISCLOSURE_RULE = (
    "EXIT_OBSERVATION_DELAY_MS = EXIT_BOOK_RECEIVED_TIMESTAMP - "
    "TARGET_EXIT_TIMESTAMP, recorded whatever it is. A late book is "
    "never relabelled as a +%ds execution. Management sees TARGET HOLD "
    "= %ds, ACTUAL EXIT ATTEMPT = target + delay, EXIT OBSERVATION "
    "DELAY = the delay -- which is honest execution evidence"
    % (EXIT_HORIZON_S, EXIT_HORIZON_S))


def exit_semantics() -> dict:
    """The V4 exit contract, for the hashed declaration."""
    return {
        "exitAnchor": EXIT_ANCHOR,
        "exitAnchorRule": TARGET_EXIT_RULE,
        "exitAction": (
            "SELL the same leg that was bought, by marketable "
            "reconstruction against the BID side of the admissible "
            "book; never the complement, never a cash-out, never a mid "
            "or a last price, never invented depth"),
        "exitTimingRule": INITIAL_EXIT_RULE,
        "exitIntendedQtyRule": PARTIAL_EXIT_RULE,
        "partialExitRule": PARTIAL_EXIT_RULE,
        "residualRule": RESIDUAL_RULE,
        "exitRetryRule": RETRY_RULE,
        # THE FIELD THAT USED TO CARRY A NUMBER NOW CARRIES THE TRUTH.
        "maxExitObservationDelayMs": MAX_EXIT_OBSERVATION_DELAY,
        "maxExitDelayBasis": TARGET_DELAY_RULE,
        "exitHorizonS": EXIT_HORIZON_S,
        "exitAtMarketCloseRule": MARKET_CLOSE_RULE,
        "maxExitDelayRevisionRule": (
            "a target-to-book delay ceiling may be declared only once "
            "the collector offers a guaranteed finite interval, and "
            "only in a NEW experiment version. It is never inferred "
            "from the freshness limit, never widened in place, and "
            "never chosen because of which exits it refused"),
        "exitEvidenceFields": list(EXIT_EVIDENCE_FIELDS)
                              + list(RESIDUAL_EVIDENCE_FIELDS),
    }


def separation() -> dict:
    """The two quantities, side by side, so they cannot be merged again."""
    return {
        "BOOK_FRESHNESS": {
            "question": "how old may the book itself be when we use it",
            "computedFrom": "the book's own source and receipt timestamps",
            "bound": "%dms" % BOOK_FRESHNESS_LIMIT_MS,
            "established": True,
            "basis": ("institutional_book.FRESHNESS_LIMIT_S, already "
                      "frozen and already enforced on the entry"),
        },
        "TARGET_TO_OBSERVATION_DELAY": {
            "question": ("how long after the target exit time may we "
                         "wait for a book"),
            "computedFrom": ("EXIT_BOOK_RECEIVED_TIMESTAMP - "
                             "TARGET_EXIT_TIMESTAMP"),
            "bound": NOT_ESTABLISHED,
            "established": False,
            "basis": TARGET_DELAY_RULE,
        },
        "whyTheyAreNotTheSame": (
            "a book received 20s after the target whose own age is 1s "
            "is FRESH and admissible; its observation delay is 20s. "
            "Bounding the second with the first would refuse sound "
            "evidence for a reason that has nothing to do with its "
            "staleness"),
    }
