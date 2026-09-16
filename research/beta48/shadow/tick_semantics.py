#!/usr/bin/env python3
"""SHARES_TRADED: what we can see it DO, and what we know it MEANS. Kept apart.

WHY THIS FILE EXISTS, AND WHY IT EXISTS BEFORE THE REFUTATION USES THE FIELD.

`maker_fill` was written to refute a counterfactual fill from an upper bound on
traded volume: credit every share the market traded to our price, and if even
that fails to clear the displayed queue, the fill is refuted. The arithmetic is
sound. The INPUT was not established.

The project state already said so, and I used the field anyway:

    SHARES_TRADED_RESET_SEMANTICS = NOT_IDENTIFIED

An upper bound is only an upper bound if the quantity it bounds is the quantity
we think it is. Every one of these would break the refutation, and none of them
is currently excluded:

    the field is INTERVAL volume, not cumulative     -> deltas are meaningless
    it resets on a session/day boundary              -> a reset reads as a
                                                        NEGATIVE delta, or
                                                        worse, a tiny one
    it is SIDE-SPECIFIC                              -> the bound is not a
                                                        bound on the whole book
    it EXCLUDES blocks, or INCLUDES them             -> either way the number
                                                        is not CLOB volume
    it updates lazily                                -> a zero delta means "we
                                                        did not see an update",
                                                        not "nothing traded"

That last one is the dangerous case, because it is the one that produces a
CONFIDENT WRONG ANSWER. Under the previous code an unchanged SHARES_TRADED gave
MAXIMAL_ATTRIBUTION = 0, which refuted every model including the touch bound. A
quiet field and a quiet market are indistinguishable from one snapshot pair, and
the quiet field is exactly what a low-liquidity venue feed does.

SO THE TWO QUESTIONS ARE SEPARATED HERE AND NEVER MERGED:

    RUNTIME BEHAVIOUR   what the field DID during our own bounded capture.
                        Measurable by us, from our own rows, today.

    VENUE SEMANTICS     what the field MEANS -- what it counts, over what
                        window, for which side, including or excluding blocks,
                        and where it resets. NOT measurable from our rows at
                        all. It needs venue documentation or a venue-published
                        figure to reconcile against.

Observing monotonicity for 75 minutes on 24 markets establishes the FIRST and
says nothing about the SECOND. A field can be perfectly monotone all afternoon
and still reset at midnight, still count one side, still include blocks. So
`diagnose()` reports runtime facts, `VENUE_SEMANTICS` reports what is known of
the meaning (currently: nothing), and only `semantics_established()` -- which
requires BOTH -- unlocks a hard refutation.

UNTIL THEN:

    SHARES_TRADED_DELTA_STATUS = CONSERVATIVE_DIAGNOSTIC_ONLY
    FILL_REFUTATION_STATUS     = NOT_IDENTIFIED
    PROVEN_NOT_FILLED          does not exist as an output of this programme

and the volume bound produces, at most, the separately named and deliberately
unequivalent:

    AGGREGATE_VOLUME_UPPER_BOUND_INSUFFICIENT
"""
from __future__ import annotations

from decimal import Decimal as D

NOT_IDENTIFIED = "NOT_IDENTIFIED"

CONSERVATIVE_DIAGNOSTIC_ONLY = "CONSERVATIVE_DIAGNOSTIC_ONLY"
AGGREGATE_VOLUME_UPPER_BOUND_INSUFFICIENT = (
    "AGGREGATE_VOLUME_UPPER_BOUND_INSUFFICIENT")

# The status of the field TODAY. Changing this constant is not enough to unlock
# anything: `semantics_established()` reads VENUE_SEMANTICS, not this label.
SHARES_TRADED_DELTA_STATUS = CONSERVATIVE_DIAGNOSTIC_ONLY

# --- WHAT THE FIELD MEANS. Every entry needs VENUE evidence, not our rows. ---
#
# These stay NOT_IDENTIFIED until a captured venue page, a published figure that
# reconciles, or a venue reply resolves them. A value inferred from the shape of
# our own deltas would be circular: we would be using the field to validate the
# field.
VENUE_SEMANTICS = {
    "WHAT_SHARES_TRADED_COUNTS": NOT_IDENTIFIED,
    "CUMULATIVE_OR_INTERVAL": NOT_IDENTIFIED,
    "MARKET_WIDE_OR_SIDE_SPECIFIC": NOT_IDENTIFIED,
    "INCLUDES_BLOCKS": NOT_IDENTIFIED,
    "RESET_BOUNDARY": NOT_IDENTIFIED,
}

VENUE_SEMANTICS_FIELDS = tuple(VENUE_SEMANTICS)

# What each field would have to say for the volume bound to be a real bound.
SEMANTICS_REQUIRED_FOR_A_HARD_REFUTATION = {
    "CUMULATIVE_OR_INTERVAL": "CUMULATIVE",
    "MARKET_WIDE_OR_SIDE_SPECIFIC": "MARKET_WIDE",
    "RESET_BOUNDARY": "KNOWN_AND_AVOIDABLE_IN_WINDOW",
}

# The name this programme refuses to produce. Present so the refusal is
# greppable and testable rather than implied by an absence nobody checks.
PROVEN_NOT_FILLED = "REFUSED_NOT_AN_OUTPUT_OF_THIS_PROGRAMME"

EVIDENCE_SOURCES_THAT_WOULD_SETTLE_SEMANTICS = (
    "VENUE_DOCUMENTATION_PAGE_CAPTURED",
    "VENUE_PUBLISHED_VOLUME_FIGURE_THAT_RECONCILES",
    "EXECUTION_TAPE_WHOSE_SUM_MATCHES_THE_FIELD",
)


class SemanticsNotEstablished(RuntimeError):
    """Raised when a hard refutation is asked for on an unproven field."""


def semantics_established(semantics=None, runtime=None):
    """May SHARES_TRADED deltas carry a HARD refutation? Both halves required.

    Runtime evidence ALONE is never enough, and that is the whole design: a
    monotone afternoon does not tell us what the number counts.
    """
    s = dict(VENUE_SEMANTICS)
    s.update(semantics or {})
    for k, need in SEMANTICS_REQUIRED_FOR_A_HARD_REFUTATION.items():
        if s.get(k) in (None, NOT_IDENTIFIED) or s[k] != need:
            return False
    if runtime is None:
        return False
    # Even with the meaning settled, a window in which the field misbehaved is
    # not a window the bound holds over.
    return bool(runtime.get("MONOTONIC_WITHIN_MARKET") is True
                and runtime.get("RESET_EVENTS_OBSERVED") == 0
                and runtime.get("NEGATIVE_DELTAS_OBSERVED") == 0)


def _d(v):
    if v is None or v == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    if isinstance(v, float):
        raise TypeError("refusing a float share count")
    return v if isinstance(v, D) else D(str(v))


def diagnose(rows, large_discontinuity_ratio="10"):
    """RUNTIME behaviour of SHARES_TRADED across one capture. Never its meaning.

    `rows` are tick rows for ANY set of slugs; each slug is walked separately,
    because a field that is monotone per market and scrambled across markets is
    a different fact from one that is monotone everywhere.
    """
    by_slug = {}
    for r in rows:
        if r.get("kind") == "TICK_ERROR":
            by_slug.setdefault(r.get("slug"), []).append(r)
            continue
        by_slug.setdefault(r.get("slug"), []).append(r)

    negatives = resets = missing = same_despite = large = 0
    updates = observations = 0
    monotone = True
    seen_any = False
    per_slug = {}
    thresh = D(str(large_discontinuity_ratio))

    for slug, rs in by_slug.items():
        rs = sorted([r for r in rs if r.get("ELAPSED_S") is not None],
                    key=lambda r: r["ELAPSED_S"])
        prev = None
        s_neg = s_reset = s_missing = s_same = s_large = 0
        s_updates = s_obs = 0
        deltas = []
        for r in rs:
            if r.get("kind") == "TICK_ERROR":
                # A gap in the sequence. The next delta spans an unobserved
                # interval, so the chain is broken rather than continued.
                s_missing += 1
                prev = None
                continue
            st = _d(r.get("SHARES_TRADED"))
            if st == NOT_IDENTIFIED:
                s_missing += 1
                prev = None
                continue
            seen_any = True
            if prev is not None:
                s_obs += 1
                delta = st - prev["st"]
                deltas.append(delta)
                if delta < 0:
                    s_neg += 1
                    monotone = False
                    # A cumulative counter that goes DOWN is either a reset or
                    # a revision. Both are named; neither is silently dropped.
                    if st < (prev["st"] / D("2")):
                        s_reset += 1
                elif delta > 0:
                    s_updates += 1
                else:
                    # Zero delta while the BOOK changed. The case that makes a
                    # quiet field look like a quiet market.
                    if (r.get("BID_CHANGED") is True
                            or r.get("ASK_CHANGED") is True
                            or r.get("DEPTH_CHANGED") is True):
                        s_same += 1
            prev = {"st": st}
        # A jump far larger than this market's own typical positive step.
        pos = sorted(x for x in deltas if x > 0)
        if len(pos) >= 4:
            med = pos[len(pos) // 2]
            if med > 0:
                s_large = sum(1 for x in pos if x > med * thresh)
        per_slug[slug] = {
            "OBSERVED_TRANSITIONS": s_obs, "NEGATIVE_DELTAS": s_neg,
            "RESET_EVENTS": s_reset, "MISSING_TRANSITIONS": s_missing,
            "POSITIVE_UPDATES": s_updates,
            "SAME_VALUE_DESPITE_BOOK_CHANGES": s_same,
            "LARGE_DISCONTINUITIES": s_large,
        }
        negatives += s_neg
        resets += s_reset
        missing += s_missing
        same_despite += s_same
        large += s_large
        updates += s_updates
        observations += s_obs

    return {
        # --- what the field DID here. Ours to measure. ---
        "MONOTONIC_WITHIN_MARKET": (monotone if seen_any else NOT_IDENTIFIED),
        "NEGATIVE_DELTAS_OBSERVED": negatives,
        "RESET_EVENTS_OBSERVED": resets,
        "MISSING_TRANSITIONS": missing,
        "FIELD_UPDATE_FREQUENCY": ((D(updates) / D(observations))
                                   if observations else NOT_IDENTIFIED),
        "SAME_VALUE_DESPITE_BOOK_CHANGES": same_despite,
        "LARGE_DISCONTINUITIES": large,
        "OBSERVED_TRANSITIONS": observations,
        "MARKETS": len(by_slug),
        "PER_SLUG": per_slug,

        # --- what the field MEANS. Not ours to measure, and not inferred. ---
        "VENUE_SEMANTICS": dict(VENUE_SEMANTICS),
        "VENUE_SEMANTICS_ESTABLISHED": all(
            v != NOT_IDENTIFIED for v in VENUE_SEMANTICS.values()),

        # --- and the two kept explicitly apart ---
        "RUNTIME_BEHAVIOUR_IS_NOT_VENUE_SEMANTICS": True,
        "WHY": ("a field can be monotone for an afternoon and still reset at "
                "midnight, still count one side, still include blocks -- "
                "runtime observation bounds none of those"),
        "SHARES_TRADED_DELTA_STATUS": SHARES_TRADED_DELTA_STATUS,
        "HARD_REFUTATION_UNLOCKED": False,
    }


def refutation_status(bound_would_refute, semantics=None, runtime=None):
    """The only place a volume bound becomes -- or fails to become -- a verdict.

    `bound_would_refute` is the ARITHMETIC result: would the upper bound, taken
    at face value, refute the model? Turning that into a refutation requires the
    field to mean what the arithmetic assumes, which is a separate question with
    a separate answer.
    """
    if not semantics_established(semantics, runtime):
        return {
            "FILL_REFUTATION_STATUS": NOT_IDENTIFIED,
            "AGGREGATE_VOLUME_BOUND_ARITHMETIC": bound_would_refute,
            "WHY": AGGREGATE_VOLUME_UPPER_BOUND_INSUFFICIENT,
            "SHARES_TRADED_DELTA_STATUS": SHARES_TRADED_DELTA_STATUS,
            "EQUIVALENT_TO_EXECUTION_EVIDENCE": False,
            "SEMANTICS_MISSING": [k for k in
                                  SEMANTICS_REQUIRED_FOR_A_HARD_REFUTATION
                                  if (semantics or VENUE_SEMANTICS).get(k)
                                  in (None, NOT_IDENTIFIED)],
        }
    return {
        "FILL_REFUTATION_STATUS": ("REFUTED" if bound_would_refute
                                   else "NOT_REFUTED"),
        "AGGREGATE_VOLUME_BOUND_ARITHMETIC": bound_would_refute,
        "WHY": "VENUE_SEMANTICS_AND_RUNTIME_BEHAVIOUR_BOTH_ESTABLISHED",
        "EQUIVALENT_TO_EXECUTION_EVIDENCE": False,
        "SEMANTICS_MISSING": [],
    }
