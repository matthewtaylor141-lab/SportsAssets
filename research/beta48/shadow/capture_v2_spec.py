"""Sections 12, 13, 14. Relative-value identifiability, and CAPTURE V2 (not dispatched).

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
THE CURRENT FROZEN CAPTURE IS NOT ALTERED BY ANYTHING IN THIS MODULE.

SECTION 12: THE CURRENT CAPTURE MAY BE STRUCTURALLY LIMITED FOR RELATIVE VALUE
-----------------------------------------------------------------------------
The frozen capture carries MONEYLINE + SPREAD per event. The relative-value
design fits a coherent latent event surface EXCLUDING the target contract, then
asks whether the target's residual against that surface predicts the target's
own later price.

With two market families, excluding the target leaves ONE family to define the
surface. A surface identified by a single constraint is not a surface; it is a
restatement of the remaining contract. The residual then measures the gap
between moneyline and spread, which is a real quantity but a much weaker object
than the intended latent-event-state residual.

So the assessment is a genuine question to be answered AFTER harvest, and its
negative answer has a specific name:

    RELATIVE_VALUE_SURFACE_V1_STATUS = INSUFFICIENT_MARKET_FAMILY_DENSITY

NOT "FAILED". A design that could not have identified the object did not fail
to find it; it was never able to look.

SECTION 14: WHAT THE CURRENT CAPTURE IS PRIMARILY FOR
-----------------------------------------------------
Execution-relevant market state, not relative value. Relative value is secondary
and conditional on the density assessment above.
"""

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# --- Section 12. Identifiability. ------------------------------------------

CURRENT_CAPTURE_FAMILIES = ("MONEYLINE", "SPREAD")
FAMILIES_LEFT_AFTER_EXCLUDING_THE_TARGET = 1

RELATIVE_VALUE_IDENTIFIABILITY_STATUS = "AWAITING_HARVEST"

INSUFFICIENT_LABEL = "INSUFFICIENT_MARKET_FAMILY_DENSITY"
NOT_THE_SAME_AS_FAILED = (
    "a design that could not have identified the object did not fail to find "
    "it. Recording INSUFFICIENT_MARKET_FAMILY_DENSITY rather than FAILED keeps "
    "the two apart, so a later reader does not treat an unidentifiable "
    "experiment as evidence of absence")

WHY_TWO_FAMILIES_MAY_NOT_BE_ENOUGH = (
    "excluding the target leaves one family to define the surface, and one "
    "constraint does not identify a latent event state. The residual becomes "
    "the moneyline-minus-spread gap -- a real quantity, but not the intended "
    "object")

MINIMUM_FAMILIES_FOR_A_SURFACE = 3
WHY_THREE = (
    "with three families, excluding one still leaves two independent "
    "constraints on the same latent event state, which is the minimum that "
    "makes the excluded contract's residual a genuine cross-market object "
    "rather than a two-contract spread"),


def identifiability(families_present):
    """Can a surface excluding the target be identified at all?"""
    n = len(set(families_present or ()))
    ok = (n - 1) >= (MINIMUM_FAMILIES_FOR_A_SURFACE - 1)
    return {
        "FAMILIES_PRESENT": n,
        "FAMILIES_AFTER_EXCLUDING_TARGET": max(n - 1, 0),
        "MINIMUM_FAMILIES_FOR_A_SURFACE": MINIMUM_FAMILIES_FOR_A_SURFACE,
        "IDENTIFIABLE": ok,
        "STATUS": "IDENTIFIABLE" if ok else INSUFFICIENT_LABEL,
        "NOT_THE_SAME_AS_FAILED": NOT_THE_SAME_AS_FAILED,
    }


# --- Section 14. The current capture's primary mission. --------------------

CURRENT_CAPTURE_PRIMARY_MISSION = (
    "OBSERVABLE_STATE_CHANGE_RATE",
    "TOUCH_SPREAD_DEPTH_DYNAMICS",
    "TRADE_FLOW",
    "MICROPRICE_IMBALANCE_PREDICTION",
    "SHORT_HORIZON_PRICE_MOVEMENT",
    "QUOTE_PERSISTENCE",
    "TRANSITION_FREQUENCY",
    "EXECUTION_RELEVANT_MARKET_STATE",
)

RELATIVE_VALUE_IS_SECONDARY = (
    "secondary, and conditional on the density assessment. If the families are "
    "insufficient the relative-value lane waits for V2 rather than producing a "
    "weak result from a design that cannot carry it")


# --- Section 13. SUBSTANTIVE_CAPTURE_V2_SPEC. PREPARED, NOT DISPATCHED. ----

V2_STATUS = "PREPARED_NOT_DISPATCHED"

V2_MAY_NOT_RUN_UNTIL = (
    "the current frozen capture COMPLETES and its scientific result is "
    "HARVESTED. Running V2 first would spend the venue-read budget on a richer "
    "design before knowing whether the simpler one worked")

V2_MARKET_FAMILIES = ("MONEYLINE", "SPREAD", "BOUND_TOTALS")

V2_INTENT = (
    "COHERENT_SURFACE_DYNAMICS",
    "RELATIVE_VALUE_RESIDUAL_CREATION",
    "RELATIVE_VALUE_CONVERGENCE",
)

V2_RATIONALE = (
    "totals are now canonically bindable, so a third family can join the same "
    "canonical event architecture. Three families mean excluding the target "
    "still leaves two independent constraints, which is what makes the "
    "residual a cross-market object")

V2_DENSITY_FIGURES = {
    "EVENTS_WITH_MONEYLINE_SPREAD_TOTALS_TRIPLETS": 41,
    "BOUND_TOTALS_MARKETS": 647,
    "SOURCE": "SUPPLIED_IN_DIRECTIVE",
    "RE_DERIVED_HERE": False,
    "WHY_NOT_RE_DERIVED": (
        "the retained totals-binding artifact is a FORWARD board of 1,757 "
        "slugs whose event keys do not parse into the settled-corpus shape, so "
        "these two counts could not be independently reproduced from the "
        "artifacts on hand. They are recorded as supplied rather than asserted "
        "or contradicted"),
}

DO_NOT_CONFUSE_MARKETS_WITH_EVENTS = (
    "647 bound totals markets are NOT 647 independent events. They sit inside "
    "41 triplet events, so the within-event derivative density rises while the "
    "independent event count does not. Every V2 result must be reported at the "
    "event level for exactly this reason")

V2_UNCHANGED_FROM_V1 = (
    "rate, requests, interval, support floors, isolation rules, overlap rules, "
    "the clean-start gate and the selection methodology are NOT changed by "
    "this spec. V2 differs from V1 in the market families captured and in "
    "nothing else")

V2_PRECONDITIONS = (
    "CURRENT_FROZEN_CAPTURE_COMPLETED",
    "CURRENT_CAPTURE_HARVESTED",
    "HARVEST_STEPS_1_THROUGH_5_DONE",
    "EXPLICIT_AUTHORIZATION",
)


# --- Section 24. The V2 design template. PREPARED, NOT DISPATCHED. --------

V2_TEMPLATE_STATUS = "PREPARED_VALUES_DEFERRED_TO_V1_HARVEST"

V2_DESIGN_DIMENSIONS = ("TARGET_INDEPENDENT_EVENTS", "TARGET_MARKETS_PER_EVENT",
                        "TARGET_CAPTURE_DURATION_S", "TARGET_POLL_INTERVAL_S",
                        "TARGET_MARKET_FAMILIES")

V2_DIMENSIONS_ARE_CHOSEN_SEPARATELY = (
    "event count and markets-per-event are DIFFERENT design targets and are "
    "never traded against each other. More markets on the same three events "
    "raises within-event derivative density and leaves the independent event "
    "count exactly where it was")

V2_VALUES_DEFERRED_UNTIL = ("TRANSITION_RATE_MEASURED", "RATE_LIMITS_MEASURED",
                            "OBSERVATION_GAPS_MEASURED",
                            "SIGNAL_VARIANCE_MEASURED")

WHY_VALUES_ARE_DEFERRED = (
    "choosing a poll interval before knowing the transition rate, or an event "
    "count before knowing the signal variance, is guessing with a number "
    "attached. V1 measures all four")


def v2_template(measured=None):
    """The V2 design, with every value NOT_IDENTIFIED until V1 measures it."""
    measured = set(measured or ())
    missing = [m for m in V2_VALUES_DEFERRED_UNTIL if m not in measured]
    vals = {d: NOT_IDENTIFIED for d in V2_DESIGN_DIMENSIONS}
    vals["TARGET_MARKET_FAMILIES"] = V2_MARKET_FAMILIES   # the one fixed choice
    return {
        "V2_TEMPLATE_STATUS": V2_TEMPLATE_STATUS,
        "DESIGN_DIMENSIONS": V2_DESIGN_DIMENSIONS,
        "VALUES": vals,
        "MEASUREMENTS_REQUIRED": V2_VALUES_DEFERRED_UNTIL,
        "MEASUREMENTS_MISSING": missing,
        "MAY_FREEZE_VALUES": not missing,
        "WHY_VALUES_ARE_DEFERRED": WHY_VALUES_ARE_DEFERRED,
        "V2_DIMENSIONS_ARE_CHOSEN_SEPARATELY":
            V2_DIMENSIONS_ARE_CHOSEN_SEPARATELY,
        "DISPATCHED": False,
    }


def v2_gate(current_capture_completed=False, harvested=False,
            harvest_steps_done=(), authorized=False):
    """May V2 be dispatched? Fail closed on every precondition."""
    done = set(harvest_steps_done or ())
    steps_ok = all(s in done for s in ("1_INTEGRITY", "2_TRANSITIONS",
                                       "3_BASELINES", "4_COMPLEX",
                                       "5_CROSS_MARKET"))
    met = {
        "CURRENT_FROZEN_CAPTURE_COMPLETED": bool(current_capture_completed),
        "CURRENT_CAPTURE_HARVESTED": bool(harvested),
        "HARVEST_STEPS_1_THROUGH_5_DONE": steps_ok,
        "EXPLICIT_AUTHORIZATION": bool(authorized),
    }
    return {
        "V2_STATUS": V2_STATUS,
        "PRECONDITIONS": met,
        "MAY_DISPATCH": all(met.values()),
        "V2_MAY_NOT_RUN_UNTIL": V2_MAY_NOT_RUN_UNTIL,
        "BLOCKED_BY": [k for k, v in met.items() if not v],
    }


def describe():
    return {
        "CURRENT_CAPTURE_FAMILIES": CURRENT_CAPTURE_FAMILIES,
        "RELATIVE_VALUE_IDENTIFIABILITY_STATUS":
            RELATIVE_VALUE_IDENTIFIABILITY_STATUS,
        "INSUFFICIENT_LABEL": INSUFFICIENT_LABEL,
        "NOT_THE_SAME_AS_FAILED": NOT_THE_SAME_AS_FAILED,
        "WHY_TWO_FAMILIES_MAY_NOT_BE_ENOUGH": WHY_TWO_FAMILIES_MAY_NOT_BE_ENOUGH,
        "CURRENT_CAPTURE_PRIMARY_MISSION": CURRENT_CAPTURE_PRIMARY_MISSION,
        "RELATIVE_VALUE_IS_SECONDARY": RELATIVE_VALUE_IS_SECONDARY,
        "V2_STATUS": V2_STATUS,
        "V2_MARKET_FAMILIES": V2_MARKET_FAMILIES,
        "V2_INTENT": V2_INTENT,
        "V2_RATIONALE": V2_RATIONALE,
        "V2_DENSITY_FIGURES": dict(V2_DENSITY_FIGURES),
        "DO_NOT_CONFUSE_MARKETS_WITH_EVENTS": DO_NOT_CONFUSE_MARKETS_WITH_EVENTS,
        "V2_UNCHANGED_FROM_V1": V2_UNCHANGED_FROM_V1,
        "V2_PRECONDITIONS": V2_PRECONDITIONS,
        "V2_TEMPLATE_STATUS": V2_TEMPLATE_STATUS,
        "V2_DESIGN_DIMENSIONS": V2_DESIGN_DIMENSIONS,
        "V2_DIMENSIONS_ARE_CHOSEN_SEPARATELY":
            V2_DIMENSIONS_ARE_CHOSEN_SEPARATELY,
        "V2_VALUES_DEFERRED_UNTIL": V2_VALUES_DEFERRED_UNTIL,
        "THE_FROZEN_CAPTURE_IS_NOT_ALTERED": True,
    }
