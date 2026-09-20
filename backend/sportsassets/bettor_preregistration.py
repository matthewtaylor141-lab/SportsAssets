"""PRE-REGISTERED ANALYSIS PLAN for BETTOR_UNSELECTED_STATE_V1.

Owner directive 2026-09-20, §9:

    "The historical RN1-flow result showed a large price-band
     interaction. Do NOT trade it. Do NOT tune bands from those
     historical results. Pre-register only the hypothesis ...
     Do not call this validation of the historical RN1 result unless
     the population and measurement are genuinely comparable."

WHY THIS FILE EXISTS AT ALL

The capture began at 2026-09-20. Nothing has matured. That is the only
moment at which an analysis plan can be written honestly, because it is
the only moment at which nobody -- including whoever writes it -- knows
which cut will look good.

Every element below is fixed NOW: the bands, the tests, the minimum
sample, the multiplicity correction, the comparison features. `PLAN_SHA`
is computed over the whole plan. A plan edited after the data matured
shows up as a different sha against the same PLAN_VERSION, which is the
condition being guarded against, made detectable rather than trusted.

THE FAILURE THIS PREVENTS

Track P is the repository's own example, preserved as a locked negative:
+10.5% train, +0.5% validation, -11.1% holdout. A cut chosen after
seeing outcomes will find something in any dataset large enough. The
only defence is to name the cuts before the outcomes exist.

WHAT MAY BE ESTIMATED FROM THIS DATASET, AND WHAT MAY NOT

The capture identifies object A -- E[SETTLEMENT - QUOTE | STATE]. It
does NOT identify object C, fill-conditional adverse selection, because
it never observes whether a hypothetical resting order would have
filled. Every hypothesis below is therefore a hypothesis about the STATE
component, and the plan says so in each one's own scope field rather
than once in a preamble.
"""

from __future__ import annotations

import hashlib
import json

from . import bettor_state_capture as sc

PLAN_VERSION = "BETTOR_UNSELECTED_PREREG_V1"
PLAN_REGISTERED_AT = "2026-09-20"
PLAN_REGISTERED_BEFORE_ANY_ROW_MATURED = True

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_YET_EVALUABLE = "NOT_YET_EVALUABLE_INSUFFICIENT_MATURED_DATA"


# ── §9. THE PRICE-BAND HYPOTHESIS ────────────────────────────────────

H0 = ("FUTURE-VALUE ECONOMICS DO NOT DIFFER MATERIALLY BY PRICE LEVEL: "
      "E[SETTLEMENT - MID | PRICE BAND] is equal across bands")
H1 = ("FUTURE-VALUE ECONOMICS VARY WITH PRICE LEVEL: "
      "E[SETTLEMENT - MID | PRICE BAND] differs across bands")

# THE BANDS, FIXED BEFORE ANY DATA MATURED.
#
# They are symmetric about 0.50 and set on round numbers. They are NOT
# taken from the RN1 result, NOT taken from the measured PMUS spread
# distribution, and NOT chosen to make any boundary fall anywhere
# convenient. A band scheme fitted to a prior result is the same
# mistake as a threshold fitted to a holdout.
PRICE_BANDS = (
    ("DEEP_LOW", "0.00", "0.05"),
    ("LOW", "0.05", "0.15"),
    ("MID_LOW", "0.15", "0.35"),
    ("CENTRE", "0.35", "0.65"),
    ("MID_HIGH", "0.65", "0.85"),
    ("HIGH", "0.85", "0.95"),
    ("DEEP_HIGH", "0.95", "1.00"),
)

BANDS_ARE_NOT_TUNED = (
    "symmetric, on round numbers, fixed before any row matured. Not "
    "derived from the RN1 result, not derived from the observed PMUS "
    "price distribution, and not adjustable without a PLAN_VERSION "
    "bump that leaves this version's result standing")

# ── the pre-declared tests ───────────────────────────────────────────
#
# Naming them, and their number, before the data is what makes the
# multiplicity correction meaningful. A correction applied to however
# many tests happened to get run is not a correction.

TESTS = (
    {
        "id": "T1_PRICE_BAND_FUTURE_VALUE",
        "question": "§7: how does future value change by price band?",
        "estimand": "E[SETTLEMENT - MID | PRICE BAND]",
        "object": sc.OBJECT_A,
        "primary": True,
        "statistic": ("difference in mean settlement-minus-mid between "
                      "each band and CENTRE"),
        "clustering": "by event_id",
        "scope": ("STATE component only. Says nothing about what a "
                  "resting order would have earned, because no fill is "
                  "observed"),
    },
    {
        "id": "T2_PRICE_BAND_SHORT_HORIZON_DRIFT",
        "question": "§7: which price levels later move adversely?",
        "estimand": "E[MID_300S - MID | PRICE BAND]",
        "object": sc.OBJECT_A,
        "primary": True,
        "statistic": "mean signed drift per band, clustered by event",
        "clustering": "by event_id",
        "scope": ("drift of the MARKET's mid, not of a position. There "
                  "is no position"),
    },
    {
        "id": "T3_SPREAD_REGIME_DRIFT",
        "question": "§7: which spread regimes are toxic?",
        "estimand": "E[MID_300S - MID | SPREAD REGIME]",
        "object": sc.OBJECT_A,
        "primary": False,
        "statistic": "mean absolute and signed drift per regime",
        "clustering": "by event_id",
        "scope": "STATE component only",
    },
    {
        "id": "T4_DEPTH_IMBALANCE_DRIFT",
        "question": "§7: which depth/imbalance states are toxic?",
        "estimand": "E[MID_300S - MID | DISPLAYED IMBALANCE QUINTILE]",
        "object": sc.OBJECT_A,
        "primary": False,
        "statistic": "mean signed drift per quintile",
        "clustering": "by event_id",
        "scope": ("DISPLAYED depth, which is not queue ahead and not "
                  "what a resting order would meet"),
    },
    {
        "id": "T5_TIME_TO_EVENT_INTERACTION",
        "question": "§7: how does toxicity change with time to event?",
        "estimand": "E[MID_300S - MID | TIME-TO-EVENT BUCKET]",
        "object": sc.OBJECT_A,
        "primary": False,
        "statistic": "mean signed drift per bucket, pregame and live",
        "clustering": "by event_id",
        "scope": "STATE component only",
    },
)

PRIMARY_TESTS = tuple(t["id"] for t in TESTS if t["primary"])
N_TESTS = len(TESTS)

# Holm-Bonferroni over the declared family. Chosen now, over a family
# whose size is fixed now.
MULTIPLICITY = {
    "method": "HOLM_BONFERRONI",
    "family": [t["id"] for t in TESTS],
    "familySize": N_TESTS,
    "alpha": "0.05",
    "why": ("the family is fixed before the data, so the correction "
            "means something. A correction applied to however many "
            "tests happened to get run is not a correction"),
}

# ── the gate before any test may be run ──────────────────────────────
#
# Minimums in INDEPENDENT EVENTS, not rows. Rows within an event are
# not independent: one game's markets move together, so 10,000 rows
# from 40 events carry roughly 40 events' worth of information.

MIN_INDEPENDENT_EVENTS = 400
MIN_MATURED_SETTLEMENTS = 1_000
MIN_EVENTS_PER_BAND = 30

WHY_EVENTS_NOT_ROWS = (
    "rows within one event are not independent -- a game's markets move "
    "together. The capture writes ~11,520 rows a day; the binding "
    "quantity is how many distinct EVENTS have settled")

EARLY_LOOKS_ARE_NOT_TESTS = (
    "descriptive counts -- rows, markets, events, maturation -- may be "
    "read at any time and are reported in the status block. The "
    "declared TESTS may not be run before the gate opens, and a look "
    "that is not a test may not become one retrospectively")


# ── §8. THE RN1-SELECTED vs UNSELECTED COMPARISON ────────────────────
#
# "Do NOT compare P&L across venues as though they are the same
# population. Instead compare structural features where definitions are
# compatible."

COMPARISON_QUESTION = (
    "WHAT MAKES THE STATES RN1 CHOOSES TO TRADE DIFFERENT FROM ORDINARY "
    "STATES?")

COMPARISON_FEATURES = (
    "PRICE_BAND", "SPREAD", "DISPLAYED_DEPTH", "BOOK_IMBALANCE",
    "RECENT_MOVE", "REALISED_VOLATILITY", "TIME_TO_EVENT",
    "MARKET_TYPE", "FUTURE_PRICE_MOVEMENT", "SETTLEMENT_DIRECTION",
)

COMPARISON_FORBIDDEN = {
    "PNL": ("P&L across venues is not a comparison of populations. The "
            "retraction of 2026-09-20 is exactly this error"),
    "NET_EV": "same defect",
    "ADVERSE_SELECTION": (
        "the RN1 figure is fill-conditional (object C) and the "
        "unselected figure is not (object A). Comparing them compares "
        "two different estimands"),
}

# A feature may be compared only if BOTH sides compute it the same way
# from compatible fields. Anything else is NOT_COMPARABLE and is
# reported as such rather than quietly aligned.
COMPARABILITY_REQUIRED = (
    "SAME_DEFINITION", "SAME_FIELD_SEMANTICS", "SAME_UNITS")

COMPARISON_SCOPE = (
    "this is a comparison of STATE DISTRIBUTIONS between a selected and "
    "an unselected sample. It describes which states RN1 chooses. It "
    "does not transfer either sample's economics onto the other, and a "
    "difference found here is a fact about selection, not an edge")


# ── the conclusion that may NOT be drawn ─────────────────────────────

NOT_A_VALIDATION_OF_RN1 = (
    "a price-band effect found in the unselected PMUS capture is NOT "
    "validation of the historical RN1 price-band result. The RN1 result "
    "is fill-conditional, on a different venue, over a different "
    "window, on flow selected by RN1's own trades. The capture is "
    "unconditional, on PMUS, prospective, unselected. Agreement between "
    "them would be two different estimands pointing the same way, which "
    "is interesting and is not confirmation")

DO_NOT_TRADE_THIS = (
    "§9: 'Do NOT trade it.' A pre-registered hypothesis is a hypothesis. "
    "No result from this plan authorises an order, a size, a mandate or "
    "a capital allocation")


def plan_sha() -> str:
    blob = json.dumps(describe_plan(), sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def describe_plan() -> dict:
    """The whole pre-registered plan, as one object. Hashed."""
    return {
        "planVersion": PLAN_VERSION,
        "registeredAt": PLAN_REGISTERED_AT,
        "registeredBeforeAnyRowMatured":
            PLAN_REGISTERED_BEFORE_ANY_ROW_MATURED,
        "universeVersion": sc.UNIVERSE_VERSION,
        "ruleSha": sc.RULE_SHA,
        "H0": H0,
        "H1": H1,
        "priceBands": [list(b) for b in PRICE_BANDS],
        "bandsAreNotTuned": BANDS_ARE_NOT_TUNED,
        "tests": [dict(t) for t in TESTS],
        "primaryTests": list(PRIMARY_TESTS),
        "multiplicity": MULTIPLICITY,
        "minIndependentEvents": MIN_INDEPENDENT_EVENTS,
        "minMaturedSettlements": MIN_MATURED_SETTLEMENTS,
        "minEventsPerBand": MIN_EVENTS_PER_BAND,
        "whyEventsNotRows": WHY_EVENTS_NOT_ROWS,
        "earlyLooksAreNotTests": EARLY_LOOKS_ARE_NOT_TESTS,
        "comparisonQuestion": COMPARISON_QUESTION,
        "comparisonFeatures": list(COMPARISON_FEATURES),
        "comparisonForbidden": dict(COMPARISON_FORBIDDEN),
        "comparabilityRequired": list(COMPARABILITY_REQUIRED),
        "comparisonScope": COMPARISON_SCOPE,
        "notAValidationOfRn1": NOT_A_VALIDATION_OF_RN1,
        "doNotTradeThis": DO_NOT_TRADE_THIS,
        "identifiesObject": sc.OBJECT_A,
        "doesNotIdentify": [sc.OBJECT_B, sc.OBJECT_C, sc.OBJECT_D],
    }


PLAN_SHA = plan_sha()


def band_of(price) -> str:
    """Which declared band a price falls in. Closed at the top."""
    from decimal import Decimal, InvalidOperation
    try:
        p = Decimal(str(price))
    except (InvalidOperation, TypeError, ValueError):
        return NOT_IDENTIFIED
    for name, lo, hi in PRICE_BANDS:
        if Decimal(lo) <= p < Decimal(hi):
            return name
    if p == Decimal("1.00"):
        return PRICE_BANDS[-1][0]
    return NOT_IDENTIFIED


def gate(*, independent_events=0, matured_settlements=0,
         events_per_band=None) -> dict:
    """May the declared tests be run yet? FAIL CLOSED.

    Returns a named state, never a bare boolean, because "not yet" and
    "never" are different answers and the difference matters to whoever
    reads the status block.
    """
    events_per_band = events_per_band or {}
    blockers = []
    if independent_events < MIN_INDEPENDENT_EVENTS:
        blockers.append("INDEPENDENT_EVENTS_%d_OF_%d"
                        % (independent_events, MIN_INDEPENDENT_EVENTS))
    if matured_settlements < MIN_MATURED_SETTLEMENTS:
        blockers.append("MATURED_SETTLEMENTS_%d_OF_%d"
                        % (matured_settlements, MIN_MATURED_SETTLEMENTS))
    thin = [name for name, _lo, _hi in PRICE_BANDS
            if events_per_band.get(name, 0) < MIN_EVENTS_PER_BAND]
    if thin:
        blockers.append("BANDS_BELOW_MINIMUM:%s" % ",".join(thin))
    return {
        "planVersion": PLAN_VERSION,
        "planSha": PLAN_SHA,
        "TESTS_MAY_RUN": not blockers,
        "STATUS": ("GATE_OPEN" if not blockers else NOT_YET_EVALUABLE),
        "BLOCKERS": blockers,
        "earlyLooksAreNotTests": EARLY_LOOKS_ARE_NOT_TESTS,
        "doNotTradeThis": DO_NOT_TRADE_THIS,
    }
