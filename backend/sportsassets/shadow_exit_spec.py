"""THE COMPLETE EXIT SEMANTICS, FROZEN BEFORE THE SUCCESSOR'S FIRST TRADE.

Owner directive 2026-09-20, "DO NOT RETROFIT MISSING EXIT SEMANTICS
INTO X1 V1":

    "Create a new prospective experiment version... The new declaration
    must completely specify entry AND exit before its first position
    exists."

WHAT THIS MODULE IS FOR, AND WHAT IT IS NOT FOR. Every rule here
applies to the SUCCESSOR ONLY. X1 V1's four positions keep
EXIT_MECHANISM_UNAVAILABLE_AT_ENTRY forever. Nothing here is applied
retroactively, and nothing here may be read as completing X1 V1's
frozen contract -- that contract is incomplete, that is a finding, and
the finding is preserved rather than repaired.

THE ORDER THESE WERE DECIDED IN MATTERS, so it is recorded. Each rule
below was derived from either (a) the frozen economic statement X1 V1
already carried, or (b) an operational measurement of the capture
pipeline. NONE was derived from X1's profit and loss, from X1's
markouts, from which book would have produced a better exit, or from
X1C. §5 requires that, and the requirement is the whole reason the
successor can be trusted where V1 cannot.
"""

from __future__ import annotations

import math

from . import shadow_markout_observability as ob

# ── §6: ONE authoritative anchor, chosen from the frozen economics ───
#
# X1 V1's frozen target said "mid at +60s versus mid at ARRIVAL" and
# its latency assumption charged value "against the book observed at
# ARRIVAL rather than at decision". Both point the same way. The
# markout implementation anchored on decision_timestamp instead, and
# that disagreement is V1's to keep -- the successor states the anchor
# once, in its own hashed declaration, so there is nothing to reconcile
# later.

EXIT_ANCHOR = "ARRIVAL"
EXIT_ANCHOR_COLUMN = "modeled_arrival_timestamp"
EXIT_HORIZON_S = 60

EXIT_ANCHOR_RULE = (
    "T0 is the modelled arrival timestamp of the entry -- the same "
    "instant the entry reconstruction was charged against. "
    "TARGET_EXIT_TIMESTAMP = T0 + %ds. The anchor is ARRIVAL and not "
    "DECISION, because the frozen economic statement this version "
    "inherits is 'mid at +%ds versus mid at arrival'; anchoring "
    "anywhere else would measure a different quantity than the one "
    "declared." % (EXIT_HORIZON_S, EXIT_HORIZON_S))


# ── §5: the maximum exit-observation delay, and where it comes from ──
#
# THIS IS THE FIGURE §5 GUARDS MOST CLOSELY, so its derivation is
# written out rather than asserted.
#
# The requirement: the exit must price against the FIRST legitimately
# observed book at or after TARGET_EXIT_TIMESTAMP. Books do not arrive
# on demand -- they arrive on the capture grid. The owner's own
# independent production measurement of that grid, recorded in
# shadow_markout_observability and taken from bettor_l2_evidence
# arrival spacing under DIRECT_INSTITUTIONAL_WORKER:
#
#     P50 60.95s    P95 63.98s    MAX 64.70s
#
# If the target instant falls immediately after a capture, the next
# book cannot arrive sooner than the grid delivers it. So a bound set
# BELOW the measured maximum grid period would refuse admissible books
# for a reason that has nothing to do with the market and everything to
# do with our own sampler. That is the operational/data-quality basis
# §5 asks for, and it is the only input:
#
#     MAX_EXIT_OBSERVATION_DELAY = ceil(measured MAX grid period)
#
# WHAT WAS NOT CONSULTED, explicitly: X1's P&L, X1's markouts, which
# book would have produced the best exit, X1C's performance. The number
# below is a property of the capture pipeline. It would be the same
# figure if X1 had been the most profitable experiment ever run.

MAX_EXIT_OBSERVATION_DELAY_S = math.ceil(ob.CAPTURE_MAX_S)      # 65
MAX_EXIT_OBSERVATION_DELAY_MS = MAX_EXIT_OBSERVATION_DELAY_S * 1000

MAX_EXIT_DELAY_BASIS = (
    "OPERATIONAL_CAPTURE_GRID_PERIOD: ceil(%.2fs), the measured MAXIMUM "
    "observed spacing between consecutive direct institutional L2 "
    "captures (P50 %.2fs, P95 %.2fs, MAX %.2fs; measured %s by %s under "
    "regime %s). A bound below this figure would refuse books for "
    "sampler-cadence reasons rather than market reasons. Derived from "
    "capture telemetry only -- NOT from X1 P&L, X1 markouts, X1C, or "
    "which book would have exited best."
    % (ob.CAPTURE_MAX_S, ob.CAPTURE_P50_S, ob.CAPTURE_P95_S,
       ob.CAPTURE_MAX_S, ob.CAPTURE_MEASURED_AT, ob.CAPTURE_MEASURED_BY,
       ob.CAPTURE_REGIME))

# THE SAMPLE MAXIMUM IS A SAMPLE MAXIMUM. The grid can be slower than
# anything yet observed, and when it is, the exit is NOT_IDENTIFIED
# rather than priced against a stale book. That is the intended
# behaviour and not a defect: a missed exit is a data-quality fact, a
# late book relabelled as on-time is a fabricated one. If the grid
# changes, the bound is RE-DERIVED IN A NEW VERSION from the new
# measurement -- never widened in place after seeing which exits it
# refused.
MAX_EXIT_DELAY_REVISION_RULE = (
    "a change to the capture grid requires a NEW experiment version "
    "carrying a re-derived bound; the bound is never widened in place, "
    "and never widened because of which exits it refused")


# ── §3: exit quantity, partials, residual and retry ──────────────────

EXIT_ACTION = (
    "SELL the same leg that was bought, on the same instrument, by "
    "marketable reconstruction against the BID side of the observed "
    "institutional book; never the complement, never a cash-out, never "
    "a mid or a last price")

EXIT_INTENDED_QTY_RULE = (
    "ALL REMAINING OPEN QUANTITY. At TARGET_EXIT_TIMESTAMP the position "
    "enters EXIT_PENDING and the intended exit quantity is the whole "
    "remaining open quantity -- never a fraction, never a "
    "discretionary size")

PARTIAL_EXIT_RULE = (
    "walk the observed BID book and fill NO MORE THAN ACTUAL AVAILABLE "
    "DEPTH. When AVAILABLE_EXIT_QTY < REMAINING_POSITION_QTY, record "
    "PARTIAL_EXIT for the quantity the book genuinely showed. Depth is "
    "never invented to force a full exit")

RESIDUAL_RULE = (
    "REMAINING_QTY = prior remaining qty - actual filled qty. The "
    "residual is an EXIT OBLIGATION, not discretionary holding: the "
    "position stays in EXIT_PENDING and continues to be offered. It is "
    "never re-classified as a held position, and its continued "
    "existence is never read as a decision to hold")

EXIT_RETRY_RULE = (
    "the residual is attempted against EVERY subsequent legitimately "
    "observed book, in arrival order, with no discretion about which "
    "book to use and no skipping. Each attempt is appended as its own "
    "EXIT_EXECUTION event carrying its own book evidence. The sequence "
    "ends when REMAINING_QTY reaches 0, at which point POSITION_CLOSED "
    "is appended. If the next book does not arrive within "
    "MAX_EXIT_OBSERVATION_DELAY of the previous attempt, the residual "
    "is NOT_IDENTIFIED_NO_ADMISSIBLE_BOOK and the position stays OPEN "
    "-- not interpolated, not closed")

# THE ONE CASE THE RETRY RULE CANNOT RESOLVE, declared rather than
# discovered later. Settlement semantics on this venue are
# CONFLICTING_VENUE_PROSE (earlier directive, unresolved), so a
# residual still outstanding when the market stops trading must NOT be
# silently converted into a settled P&L.
EXIT_INCOMPLETE_AT_CLOSE = "EXIT_INCOMPLETE_AT_MARKET_CLOSE"
EXIT_AT_CLOSE_RULE = (
    "a residual still outstanding when the market stops trading is "
    "recorded as %s with the residual quantity preserved. It is NOT "
    "converted into a settled P&L, because this venue's settlement "
    "semantics remain CONFLICTING_VENUE_PROSE and an unresolved "
    "settlement rule cannot be used to close an unresolved exit"
    % EXIT_INCOMPLETE_AT_CLOSE)

# §4's status for a target with no admissible book.
NO_ADMISSIBLE_BOOK = "NOT_IDENTIFIED_NO_ADMISSIBLE_BOOK"

EXIT_TIMING_RULE = (
    "the exit prices against the FIRST legitimately observed book whose "
    "received timestamp is AT OR AFTER TARGET_EXIT_TIMESTAMP, subject "
    "to EXIT_OBSERVATION_DELAY_MS <= %d. Three instants are recorded "
    "separately on every attempt -- EXIT_TARGET_TIMESTAMP, "
    "EXIT_BOOK_TIMESTAMP and EXIT_OBSERVATION_DELAY_MS -- so a late "
    "book is never relabelled as though it arrived at the target. When "
    "the bound is exceeded: EXIT_EXECUTION_STATUS = %s, no "
    "interpolation, and the position is NOT closed"
    % (MAX_EXIT_OBSERVATION_DELAY_MS, NO_ADMISSIBLE_BOOK))


# ── the evidence every exit attempt must carry ───────────────────────
#
# Named here so the ledger columns and the rule cannot drift apart.

EXIT_EVIDENCE_FIELDS = (
    "EXIT_TARGET_TIMESTAMP",
    "EXIT_BOOK_TIMESTAMP",
    "EXIT_OBSERVATION_DELAY_MS",
    "EXIT_BOOK_SHA",
    "EXIT_INTENDED_QTY",
    "EXIT_AVAILABLE_QTY",
    "EXIT_FILLED_QTY",
    "EXIT_REMAINING_QTY",
    "EXIT_VWAP",
    "EXIT_EXECUTION_STATUS",
)


def exit_semantics() -> dict:
    """The successor's complete exit contract, as one readable object.

    Every field here is hashed into the successor's declaration, so a
    later edit to any of them is a new version rather than a silent
    change to a running experiment.
    """
    return {
        "exitHorizonS": EXIT_HORIZON_S,
        "exitAnchor": EXIT_ANCHOR,
        "exitAnchorRule": EXIT_ANCHOR_RULE,
        "exitAction": EXIT_ACTION,
        "exitTimingRule": EXIT_TIMING_RULE,
        "exitIntendedQtyRule": EXIT_INTENDED_QTY_RULE,
        "partialExitRule": PARTIAL_EXIT_RULE,
        "residualRule": RESIDUAL_RULE,
        "exitRetryRule": EXIT_RETRY_RULE,
        "exitAtMarketCloseRule": EXIT_AT_CLOSE_RULE,
        "maxExitObservationDelayMs": MAX_EXIT_OBSERVATION_DELAY_MS,
        "maxExitDelayBasis": MAX_EXIT_DELAY_BASIS,
        "maxExitDelayRevisionRule": MAX_EXIT_DELAY_REVISION_RULE,
        "exitEvidenceFields": list(EXIT_EVIDENCE_FIELDS),
    }


def completeness() -> dict:
    """Can a position under this contract complete its lifecycle?

    THE QUESTION X1 V1 FAILS. V1 answers WHEN, WHAT and WHAT PRICE but
    not HOW MUCH, and declares no book tolerance, so a V1 position can
    open and can never close. This checks the successor against the
    same four questions plus the two gaps V1 left open.
    """
    spec = exit_semantics()
    required = {
        "WHEN TO EXIT": spec["exitAnchorRule"],
        "WHAT ACTION TO TAKE": spec["exitAction"],
        "WHAT PRICE TO USE": spec["exitAction"],
        "HOW MUCH QUANTITY TO EXIT": spec["exitIntendedQtyRule"],
        "EXIT_BOOK_TOLERANCE": spec["exitTimingRule"],
        "PARTIAL AND RESIDUAL DISPOSAL": spec["residualRule"],
    }
    missing = [k for k, v in required.items() if not v]
    return {
        "lifecycleCompletable": not missing,
        "answered": [k for k in required if k not in missing],
        "missing": missing,
        "maxExitObservationDelayMs": spec["maxExitObservationDelayMs"],
        "maxExitDelayBasis": spec["maxExitDelayBasis"],
        "derivedFromPerformance": False,
        "why": (
            "every question that blocks X1 V1 is answered here, and the "
            "one free numeric parameter -- the exit-observation delay "
            "bound -- is derived from measured capture cadence rather "
            "than from any outcome"),
    }
