"""THE EXIT TIMING CONTRACT, DERIVED FROM THE COLLECTOR AND FROZEN AS
LITERALS.

Owner review 2026-09-20 §4, §5, §6:

    "Determine the actual operational timing contract of the direct
    institutional collector... We need to know whether there is a
    genuine finite operational upper bound."

    §6 "Current V2 code computes MAX_EXIT_OBSERVATION_DELAY_S =
    ceil(shadow_markout_observability.CAPTURE_MAX_S). That is not
    acceptable for a frozen experiment... a telemetry update can change
    a supposedly frozen experiment declaration without anyone changing
    that experiment's economic policy."

BOTH CORRECTIONS ARE AGAINST MY OWN WORK AND BOTH ARE RIGHT.

THE FIRST DEFECT: V2 BOUNDED THE WRONG QUANTITY. Its 65s came from the
spacing of rows in `bettor_l2_evidence`. That table is
institutional_md's EVIDENCE_EVERY_S = 60.0 background trail -- written
once a minute per symbol so there is a record the process was reading
the venue at all, and explicitly described in that module as
"deliberately sparse". THE DIRECT EXECUTION PATH NEVER READS IT. It
reads the in-memory book in institutional_book.STORE, refreshed on a
2-second sweep. Bounding an exit on trail spacing bounds the wrong
thing by a factor of about twenty, and replacing 65 with 71 would have
preserved the error exactly while appearing to fix it.

THE SECOND DEFECT: V2 IMPORTED A LIVE VALUE INTO A FROZEN HASH. The
registry's own docstring says "EVERY VALUE IN THIS FILE IS A LITERAL,
and that is the point... a threshold read from an environment
variable, a start timestamp computed at import... would let a running
experiment's rules move without a new version". V2 then computed its
bound from a telemetry module whose whole purpose is to be updated
when the grid is re-measured. A later measurement would have silently
changed a frozen experiment's sha. Every figure below is a LITERAL for
that reason.
"""

from __future__ import annotations

# ── §4: the collector's configured contract, read from its source ────
#
# sportsassets/workers/institutional_md.py and
# sportsassets/pmx_institutional.py, at the revision this was frozen.

CONFIGURED_CAPTURE_PERIOD_S = 2.0          # institutional_md.SWEEP_S
BOOKS_PER_CYCLE = 8                        # MAX_INSTRUMENTS
READ_PACING_S = 0.15                       # per read, process-wide gate
REQUEST_TIMEOUT_CONNECT_S = 5.0            # pmx_institutional.TIMEOUT[0]
REQUEST_TIMEOUT_READ_S = 30.0              # pmx_institutional.TIMEOUT[1]
SEQUENTIAL_OR_PARALLEL = "SEQUENTIAL"      # one for-loop, paced per read
RETRY_POLICY = (
    "NONE WITHIN A SWEEP. A non-200 read increments `failed` and the "
    "loop moves to the next symbol; the retry is implicit -- the next "
    "sweep, one period later. There is no per-request retry and so no "
    "retry multiplier in the worst case")
FAILURE_BACKOFF_S = 30.0                   # BOOTSTRAP_BACKOFF_S
AUTH_BACKOFF_S = 60.0

# The worst case the configuration permits, written out rather than
# asserted: every one of the 8 reads pacing then hitting the read
# timeout, plus the sweep sleep.
THEORETICAL_WORST_CASE_CYCLE_S = (
    BOOKS_PER_CYCLE * (READ_PACING_S + REQUEST_TIMEOUT_READ_S)
    + CONFIGURED_CAPTURE_PERIOD_S)                          # 243.2
THEORETICAL_WORST_CASE_WITH_BACKOFF_S = (
    BOOKS_PER_CYCLE * (READ_PACING_S + REQUEST_TIMEOUT_READ_S)
    + FAILURE_BACKOFF_S)                                    # 271.2

# ── the observed cycle, as LITERALS ──────────────────────────────────
#
# institutional_md's own heartbeat, production, 2026-09-20T14:16:03Z.

OBSERVED_SWEEP_S = 1.132
OBSERVED_SYMBOLS = 8
OBSERVED_VENUE_MS_P50 = 65.1
OBSERVED_VENUE_MS_MAX = 69.4
OBSERVED_READS = 8
OBSERVED_FAILED = 0
OBSERVED_LOOP_PERIOD_S = OBSERVED_SWEEP_S + CONFIGURED_CAPTURE_PERIOD_S
OBSERVED_AT = "2026-09-20T14:16:03Z"

# ── the measurement the bound is actually about, as LITERALS ─────────
#
# `book_age_ms` on DIRECT decisions: how stale the in-memory book was
# when the execution path walked it. This is the SAME read an exit
# makes, so it is the same distribution an exit's admissibility must
# be bounded on. Production, 2026-09-20T14:16Z.

BOOK_AGE_N = 799
BOOK_AGE_P50_MS = -1652.3
BOOK_AGE_P95_MS = 4371.8
BOOK_AGE_MAX_MS = 21962.8
BOOK_AGE_OVER_5000_MS = 32
BOOK_AGE_MEASURED_AT = "2026-09-20T14:16Z"
BOOK_AGE_REGIME = "DIRECT_INSTITUTIONAL_WORKER"

# THE NEGATIVE P50 IS NOT A DATA ERROR AND IT IS NOT REPAIRED HERE.
# `book_age_ms` is (walk instant - book received). The walk instant is
# the MODELED arrival, derived from decision_timestamp, which sits in
# the tick's sealed clock domain; the book's received timestamp is
# wall-clock. Those domains are already recorded as non-monotonic
# (LATENCY_STATUS = NOT_IDENTIFIED_CLOCK_DOMAIN_CONFLICT on 4/4 X1 and
# 45/45 X1C rows). A negative median is that conflict showing through
# and it is named, not corrected -- correcting it would rewrite
# evidence. It does NOT weaken the bound below, because the bound is
# enforced by the store's own freshness verdict rather than by this
# number.

# The freshness verdict the store applies, and what it actually did.
FRESHNESS_CURRENT_N = 767
FRESHNESS_CURRENT_FILLED = 51
FRESHNESS_STALE_N = 32
FRESHNESS_STALE_FILLED = 0
FRESHNESS_STALE_NOT_IDENTIFIED = 29
FRESHNESS_ABSENT_N = 2
FRESHNESS_ABSENT_FILLED = 0

# ── §4's answer ──────────────────────────────────────────────────────
#
# YES, a finite operational bound exists -- and it is not a new number.
# It is the execution-admissibility contract this system already froze
# and already enforces on the ENTRY:
#
#     institutional_book.FRESHNESS_LIMIT_S = 5.0
#
# `executable()` returns nothing at all past it, so a decision walking
# a staler book is recorded NOT_IDENTIFIED rather than filled. In
# production that is not a claim, it is a measurement: 51 of 51 fills
# came from a CURRENT book, and all 32 STALE reads produced zero fills
# and 29 NOT_IDENTIFIED.
#
# Applying it to the exit is therefore not manufacturing a bound to
# permit trading. It is refusing to let the exit accept evidence the
# entry would already have refused. Anything looser would make the two
# halves of the same reconstruction answer to different standards.

OPERATIONAL_HARD_BOUND_ESTABLISHED = True
OPERATIONAL_HARD_BOUND_MS = 5000            # FRESHNESS_LIMIT_S = 5.0
OPERATIONAL_BOUND_BASIS = (
    "EXECUTION_ADMISSIBILITY_ALREADY_FROZEN: "
    "institutional_book.FRESHNESS_LIMIT_S = 5.0s, the limit past which "
    "BookStore.executable() returns nothing and the DIRECT path records "
    "NOT_IDENTIFIED instead of a fill. It is the contract the ENTRY "
    "already answers to, demonstrated in production over %d DIRECT "
    "decisions: %d of %d fills from a CURRENT book, %d STALE reads "
    "producing %d fills and %d NOT_IDENTIFIED. Derived from the "
    "collector's configuration and the store's frozen freshness rule; "
    "NOT from X1 P&L, X1 markouts, X1C, or which book would have "
    "exited best."
    % (BOOK_AGE_N, FRESHNESS_CURRENT_FILLED, FRESHNESS_CURRENT_FILLED,
       FRESHNESS_STALE_N, FRESHNESS_STALE_FILLED,
       FRESHNESS_STALE_NOT_IDENTIFIED))

# WHY THE 243s WORST CASE DOES NOT BECOME A 243s EXIT DELAY. A sweep
# that slow leaves the book older than the freshness limit, so the
# store refuses it and the exit is NOT_IDENTIFIED. The worst-case cycle
# bounds how long the collector can go quiet; it does not bound how
# stale a book an exit may use, because that is bounded separately and
# more tightly by a rule that already exists.
WORST_CASE_NOTE = (
    "a cycle at the %0.1fs theoretical worst case leaves every book "
    "past the %dms freshness limit, so the exit is NOT_IDENTIFIED "
    "rather than filled against a stale book. The slow-collector case "
    "is a refusal, not a loose bound"
    % (THEORETICAL_WORST_CASE_CYCLE_S, OPERATIONAL_HARD_BOUND_MS))


# ── §9: initial exit and residual retry, separately anchored ─────────
#
# "Do not silently give residual retries a different clock anchor."
# They DO have different anchors, necessarily -- so the difference is
# declared rather than left implicit. The WINDOW LENGTH is identical
# and has the same basis; only what it is measured from differs.

INITIAL_EXIT_ANCHOR = "TARGET_EXIT_TIMESTAMP = ARRIVAL + 60s"
INITIAL_EXIT_MAX_DELAY_MS = OPERATIONAL_HARD_BOUND_MS
INITIAL_EXIT_DELAY_BASIS = (
    "the first book RECEIVED AT OR AFTER TARGET_EXIT_TIMESTAMP, "
    "admissible only while its age at the walk instant is within "
    "FRESHNESS_LIMIT_S. Window: [TARGET, TARGET + %dms]. Basis: %s"
    % (INITIAL_EXIT_MAX_DELAY_MS, OPERATIONAL_BOUND_BASIS))

RESIDUAL_RETRY_ANCHOR = (
    "THE PREVIOUS ATTEMPT'S OWN BOOK TIMESTAMP -- not the original "
    "target. A residual attempt is a new observation, and measuring "
    "its admissibility from an instant minutes in the past would "
    "refuse every retry on a position that took more than one book to "
    "clear")
RESIDUAL_RETRY_MAX_INTERBOOK_DELAY_MS = OPERATIONAL_HARD_BOUND_MS
RESIDUAL_RETRY_DELAY_BASIS = (
    "the next book RECEIVED AT OR AFTER the previous attempt's book, "
    "admissible on the same %dms freshness contract, measured from "
    "that previous book rather than from TARGET. Same window length, "
    "same basis, different anchor -- stated explicitly because it is "
    "different. If no admissible book arrives within the window, the "
    "residual is NOT_IDENTIFIED_NO_ADMISSIBLE_BOOK and the position "
    "stays open: never interpolated, never closed"
    % RESIDUAL_RETRY_MAX_INTERBOOK_DELAY_MS)


def timing_contract() -> dict:
    """Everything §4 and §9 ask for, in one readable object."""
    return {
        "CONFIGURED_CAPTURE_PERIOD": "%.1fs" % CONFIGURED_CAPTURE_PERIOD_S,
        "BOOKS_PER_CYCLE": BOOKS_PER_CYCLE,
        "READ_PACING_S": READ_PACING_S,
        "REQUEST_TIMEOUT": "connect %.1fs / read %.1fs" % (
            REQUEST_TIMEOUT_CONNECT_S, REQUEST_TIMEOUT_READ_S),
        "RETRY_POLICY": RETRY_POLICY,
        "SEQUENTIAL_OR_PARALLEL": SEQUENTIAL_OR_PARALLEL,
        "THEORETICAL_WORST_CASE_CYCLE": "%.1fs (%.1fs on the failure "
                                        "backoff branch)" % (
            THEORETICAL_WORST_CASE_CYCLE_S,
            THEORETICAL_WORST_CASE_WITH_BACKOFF_S),
        "OBSERVED_LOOP_PERIOD": "%.3fs (sweep %.3fs + %.1fs sleep)" % (
            OBSERVED_LOOP_PERIOD_S, OBSERVED_SWEEP_S,
            CONFIGURED_CAPTURE_PERIOD_S),
        "OBSERVED_AT": OBSERVED_AT,
        "OBSERVED_CAPTURE_N": BOOK_AGE_N,
        "OBSERVED_CAPTURE_P50": "%.1fms book age" % BOOK_AGE_P50_MS,
        "OBSERVED_CAPTURE_P95": "%.1fms book age" % BOOK_AGE_P95_MS,
        "OBSERVED_CAPTURE_MAX": "%.1fms book age" % BOOK_AGE_MAX_MS,
        "OPERATIONAL_HARD_BOUND_ESTABLISHED": (
            "YES" if OPERATIONAL_HARD_BOUND_ESTABLISHED else "NO"),
        "OPERATIONAL_HARD_BOUND": "%dms" % OPERATIONAL_HARD_BOUND_MS,
        "OPERATIONAL_BOUND_BASIS": OPERATIONAL_BOUND_BASIS,
        "worstCaseNote": WORST_CASE_NOTE,
        "INITIAL_EXIT_MAX_DELAY": "%dms" % INITIAL_EXIT_MAX_DELAY_MS,
        "INITIAL_EXIT_DELAY_BASIS": INITIAL_EXIT_DELAY_BASIS,
        "RESIDUAL_RETRY_MAX_INTERBOOK_DELAY": "%dms"
            % RESIDUAL_RETRY_MAX_INTERBOOK_DELAY_MS,
        "RESIDUAL_RETRY_DELAY_BASIS": RESIDUAL_RETRY_DELAY_BASIS,
    }


def capture_provenance() -> dict:
    """§6: LITERAL provenance. Future telemetry must never mutate this.

    Every value is typed in this file. Nothing is imported from
    shadow_markout_observability, whose measurements are explicitly
    replaceable -- that import is exactly what let a telemetry update
    change V2's frozen sha.
    """
    return {
        "CAPTURE_MEASUREMENT_WINDOW": (
            "institutional_md heartbeat at %s (one cycle) and %d DIRECT "
            "decisions to %s" % (OBSERVED_AT, BOOK_AGE_N,
                                 BOOK_AGE_MEASURED_AT)),
        "CAPTURE_MEASUREMENT_ASOF": BOOK_AGE_MEASURED_AT,
        "CAPTURE_N": BOOK_AGE_N,
        "CAPTURE_P50_MS": BOOK_AGE_P50_MS,
        "CAPTURE_P95_MS": BOOK_AGE_P95_MS,
        "CAPTURE_MAX_MS": BOOK_AGE_MAX_MS,
        "CAPTURE_REGIME": BOOK_AGE_REGIME,
        "MAX_EXIT_OBSERVATION_DELAY_MS": OPERATIONAL_HARD_BOUND_MS,
        "measures": (
            "the age of the IN-MEMORY book at the instant the DIRECT "
            "path walked it -- the same read an exit makes"),
        "doesNotMeasure": (
            "bettor_l2_evidence row spacing, which is the 60s "
            "background trail and which V2 wrongly bounded on"),
        "negativeMedianNote": (
            "the negative P50 is the recorded clock-domain conflict "
            "between the sealed decision clock and wall-clock receipt, "
            "not a data error; it is named and not corrected, and the "
            "bound does not rest on it"),
    }
