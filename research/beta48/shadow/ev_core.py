#!/usr/bin/env python3
"""BETTOR_EV_CORE_V1 -- the output contract, and what it refuses to invent.

WHAT THIS EMITS. One receipt per priced contract, carrying every field the EV
core is specified to produce. A field whose input does not exist reads
NOT_IDENTIFIED. Nothing is substituted, defaulted or quietly zeroed.

WHY SO MUCH OF IT IS NOT_IDENTIFIED TODAY, AND WHY THAT IS THE POINT. The
validated champion fair value is the venue's own traded price -- no model in the
zoo beat it out of sample. Execution fair value, fill probability and
conditional-on-fill value all require BETTOR-native evidence that does not exist
because BETTOR has never rested an order. A receipt that filled those in would
be a receipt that lies, and a downstream reader could not tell.

    SETTLEMENT_FAIR_VALUE      available -- it is the calibrated venue price
    EXECUTION_FAIR_VALUE_*     NOT_IDENTIFIED -- no validated microstructure
    P_FILL                     NOT_IDENTIFIED -- no BETTOR fill has occurred
    VALUE_CONDITIONAL_ON_FILL  NOT_IDENTIFIED -- requires fills to condition on

THE ACTION LAYER IS DELIBERATELY INERT. Every EV_* action field is
NOT_IDENTIFIED and BEST_ACTION is NO_TRADE, because an action EV needs a fill
probability and a cost model, and both are absent. NO_TRADE here is not a
recommendation reached by comparing alternatives -- it is the only admissible
answer when the comparison cannot be made, and the receipt says which.

ONE REAL ORDER DOES NOT IDENTIFY P_FILL. It is one observation. The status
ladder NOT_IDENTIFIED -> PARTIALLY_IDENTIFIED -> IDENTIFIED exists so that a
single fill cannot be read as a measured rate.

This module contacts nothing and can place no order. There is no submit path.
"""
import json

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_ESTABLISHED = "NOT_ESTABLISHED"

EV_CORE_VERSION = "BETTOR_EV_CORE_V1"
MODEL_VERSION = "B0_VENUE_PRICE"
CALIBRATION_VERSION = "IDENTITY_NO_RECALIBRATION"
WHY_IDENTITY_CALIBRATION = (
    "every recalibration tested lost to the raw venue price on unseen "
    "chronological events; applying one would be a change with negative "
    "measured value")

FAIR_VALUE_STATUS = "NOT_YET_VALIDATED"
CHAMPION_IS_THE_MARKET = (
    "the champion fair value is the venue's own traded price; BETTOR has no "
    "validated independent forecast, so its fair value is the market's")

# ---------------------------------------------------------------------------
# THE GENERATION 1 FREEZE. Recorded so it cannot be quietly tuned away.
#
# B0 earned the status of STRONGEST EMPIRICAL BASELINE. It did NOT earn the
# status of BETTOR INDEPENDENT FAIR VALUE. Those are different claims and the
# difference is the whole programme: a baseline is what a challenger must beat;
# an independent fair value is a second opinion capable of disagreeing with the
# market on information. BETTOR has the first and not the second.
# ---------------------------------------------------------------------------

B0_VENUE_RAW = "CURRENT_STRONGEST_BASELINE"
B0_IS_NOT = "BETTOR_INDEPENDENT_FAIR_VALUE"
GENERATION_1_FROZEN = True
GENERATION_1_RESULT = {
    "LABELLED_OBSERVATIONS": 112535,
    "INDEPENDENT_EVENTS": 4103,
    "MARKETS": 9335,
    "AS_OF_VIOLATIONS_CAUGHT": 1684,
    "OUTCOME_CONTRADICTIONS_AFTER_PARSER_REPAIR": 0,
    "WALK_FORWARD_WINNER": "B0_VENUE_PRICE",
    "EVERY_RECALIBRATION_LOST_OUT_OF_SAMPLE": True,
    "DO_NOT_RETUNE_FOR_A_FLATTERING_RESULT": (
        "this split is frozen; re-cutting it until a challenger wins is how a "
        "false discovery is manufactured"),
}

# THE HOLDOUT IS SPENT. It was scored once, which is what a holdout is for.
# It may be quoted in reports. It may never again select anything.
GENERATION_1_FINAL_HOLDOUT = "BURNED_FOR_MODEL_SELECTION"
HOLDOUT_PERMITTED_USE = "REPORTING_ONLY"
HOLDOUT_FORBIDDEN_USES = (
    "FEATURE_SELECTION", "MODEL_CLASS_SELECTION", "CALIBRATION_SELECTION",
    "HYPERPARAMETER_SELECTION", "SPORT_SELECTION", "MARKET_FAMILY_SELECTION",
    "THRESHOLD_TUNING", "ENSEMBLE_WEIGHTS",
)
NEXT_GENERATION_REQUIRES = (
    "a NEW untouched chronological holdout: sealed from data whose outcomes "
    "were never inspected during model development, or accumulated "
    "prospectively if no such block exists")

# ---------------------------------------------------------------------------
# THE THREE PROBABILITY OBJECTS. Never the same thing, never the same name.
# ---------------------------------------------------------------------------

PROBABILITY_OBJECTS = {
    "P_MARKET_RAW": {
        "IS": "the venue's observed price for this contract (B0)",
        "MARKET_DERIVED": True,
        "INDEPENDENT_ALPHA": False,
        "STATUS": B0_VENUE_RAW,
    },
    "P_MARKET_SURFACE": {
        "IS": ("one coherent latent event distribution fitted to the "
               "contemporaneous family of linked market prices"),
        "MARKET_DERIVED": True,
        "INDEPENDENT_ALPHA": False,
        "STATUS": "BUILT_NOT_YET_VALIDATED_AGAINST_SETTLEMENTS",
        "WHAT_A_GAP_FROM_RAW_MEANS": ("internal inconsistency between linked "
                                      "contracts, NOT an informational edge"),
    },
    "P_BETTOR_INDEPENDENT": {
        "IS": ("a probability built WITHOUT the target market's price as an "
               "input -- the only object that can disagree with the market on "
               "information rather than arithmetic"),
        "MARKET_DERIVED": False,
        "INDEPENDENT_ALPHA": True,
        "STATUS": "DOES_NOT_EXIST",
        "BLOCKED_ON": ("no external odds feed and no sport fundamentals in "
                       "the retained corpus"),
    },
    "P_BETTOR_ENSEMBLE": {
        "IS": "a validated combination of market and independent experts",
        "MARKET_DERIVED": None,
        "INDEPENDENT_ALPHA": None,
        "STATUS": "DOES_NOT_EXIST",
        "BLOCKED_ON": ("there is only one expert; pooling a forecast with "
                       "transformations of itself adds no information"),
    },
}
NEVER_CONFLATE_THE_OBJECTS = (
    "calling a market-derived surface an independent fair value would turn an "
    "arithmetic consistency check into a claim of edge; the four objects carry "
    "distinct names for exactly that reason")

# ---------------------------------------------------------------------------
# WHAT "THE VENUE PRICE" ACTUALLY MEANS. "P_VENUE" was ambiguous and that is a
# defect: a traded price, a bid, an ask and a mid are four different numbers
# with four different uses, and mixing them silently is how a midpoint ends up
# standing in for an executable price.
# ---------------------------------------------------------------------------

B0_DEFINITION = "P_LAST_TRADE"
B0_DEFINITION_DETAIL = (
    "the price of the whale fill that generated the observation -- a TRADED "
    "price, not a quote; it is what the retained corpus carries per row")
B0_TIMESTAMP_POLICY = (
    "the observation's own trade timestamp `ts`, required strictly earlier "
    "than the settlement it is labelled with; violations are refused and "
    "counted, not clipped")
B0_QUOTE_AGE_POLICY = (
    "NOT_APPLICABLE to a traded price -- age zero by construction. The book "
    "fields on the same row (best_ask, depth) were captured at probe time, "
    "`reaction_s` after the trade, and that lag is carried on the row rather "
    "than assumed negligible")

MARKET_PRICE_FIELDS = {
    "P_LAST_TRADE": {"AVAILABLE": True, "USE": "settlement probability (B0)"},
    "P_BEST_ASK": {"AVAILABLE": True,
                   "USE": "EXECUTION price for a buy -- never for probability"},
    "P_BEST_BID": {"AVAILABLE": False,
                   "WHY": "the probe captured ask-side depth only"},
    "P_MID": {"AVAILABLE": False,
              "WHY": "needs both sides; no bid was captured"},
    "P_MICROPRICE": {"AVAILABLE": False,
                     "WHY": "needs both sides with sizes"},
}
B0_LAST_STATUS = "BUILT_AND_WALK_FORWARD_TESTED"
B0_MID_STATUS = "NOT_BUILT_NO_BID_SIDE_IN_CORPUS"
NEVER_SUBSTITUTE_MID_FOR_EXECUTION = (
    "a midpoint is not tradable; execution EV uses best bid or best ask, and "
    "substituting a mid overstates every fill")
BENCHMARK_FREEZE_RULE = (
    "the prediction benchmark is frozen on walk-forward evidence; B0_LAST won "
    "because it was tested, not because it was convenient. B0_MID has not been "
    "tested and therefore has not lost -- it is unbuilt, not rejected")

P_FILL_LADDER = ("NOT_IDENTIFIED", "PARTIALLY_IDENTIFIED", "IDENTIFIED")
ONE_ORDER_DOES_NOT_IDENTIFY_P_FILL = (
    "the first micro-live order yields ONE observation; a usable fill model "
    "needs an accumulating BETTOR-native sample with uncertainty, so the "
    "status stays NOT_IDENTIFIED until it does")

ACTIONS = ("MAKE", "TAKE", "PAIR", "HOLD", "HEDGE", "PASSIVE_EXIT",
           "AGGRESSIVE_EXIT", "SETTLE", "NO_TRADE")

EXECUTION_HORIZONS_S = (5, 30, 60, 300)

# The contract's full field list, so a consumer can assert completeness rather
# than discover a missing key at runtime.
OUTPUT_FIELDS = (
    "EVENT_ID", "MARKET_ID", "MARKET_FAMILY", "SIDE", "AS_OF_TIMESTAMP",
    "P_VENUE", "P_EXTERNAL_CONSENSUS", "P_FUNDAMENTAL", "P_EVENT_DISTRIBUTION",
    "P_WHALE_RESIDUAL", "P_ENSEMBLE_RAW", "P_SETTLEMENT_CALIBRATED",
    "SETTLEMENT_FAIR_VALUE", "SETTLEMENT_FV_LOWER", "SETTLEMENT_FV_UPPER",
    "EXECUTION_FAIR_VALUE_5S", "EXECUTION_FAIR_VALUE_30S",
    "EXECUTION_FAIR_VALUE_60S", "EXECUTION_FAIR_VALUE_300S",
    "P_FILL", "P_FILL_STATUS", "P_FILL_LOWER", "P_FILL_UPPER",
    "VALUE_CONDITIONAL_ON_FILL",
    "EV_MAKE", "EV_TAKE", "EV_HOLD", "EV_PAIR", "EV_HEDGE",
    "EV_PASSIVE_EXIT", "EV_AGGRESSIVE_EXIT", "EV_NO_TRADE",
    "BEST_ACTION", "BEST_ACTION_EV", "BEST_ACTION_EV_LOWER",
    "P_BEST_ACTION_EV_POSITIVE",
    "MODEL_VERSION", "CALIBRATION_VERSION", "DATA_CUTOFF", "UNCERTAINTY_STATUS",
    "P_MARKET_RAW", "P_MARKET_SURFACE", "P_BETTOR_INDEPENDENT",
    "P_BETTOR_ENSEMBLE",
)

# Why each absent component is absent. A reader should never have to guess
# whether NOT_IDENTIFIED means "zero", "small" or "we cannot see it".
ABSENT_BECAUSE = {
    "P_EXTERNAL_CONSENSUS": "no bookmaker or exchange odds in the corpus",
    "P_FUNDAMENTAL": "no sport data feed; DATA_STATUS NOT_AVAILABLE",
    "P_EVENT_DISTRIBUTION": "machinery built; sample too small to fit",
    "P_WHALE_RESIDUAL": "tested and rejected -- failed walk-forward validation",
    "EXECUTION_FAIR_VALUE": "no validated microstructure model",
    "P_FILL": "BETTOR has never rested an order",
    "VALUE_CONDITIONAL_ON_FILL": "requires BETTOR fills to condition on",
    "ACTION_EVS": "an action EV needs a fill probability and a cost model",
}


def price(event_id, market_id, market_family, side, as_of, p_venue,
          fv_lower=None, fv_upper=None, data_cutoff=None):
    """One EV receipt. Present components are carried; absent ones are named.

    `p_venue` is the venue's own price for this side, and it IS the champion
    settlement fair value -- not because that is satisfying, but because it is
    what survived out-of-sample comparison.
    """
    p = None
    try:
        p = float(p_venue)
        if not (0.0 < p < 1.0):
            p = None
    except (TypeError, ValueError):
        p = None

    fv = p if p is not None else NOT_IDENTIFIED
    rec = {
        "EVENT_ID": event_id or NOT_IDENTIFIED,
        "MARKET_ID": market_id or NOT_IDENTIFIED,
        "MARKET_FAMILY": market_family or NOT_IDENTIFIED,
        "SIDE": side or NOT_IDENTIFIED,
        "AS_OF_TIMESTAMP": as_of or NOT_IDENTIFIED,

        "P_VENUE": fv,
        # The three objects, kept apart by name at the receipt level.
        "P_MARKET_RAW": fv,
        "P_MARKET_SURFACE": NOT_IDENTIFIED,
        "P_BETTOR_INDEPENDENT": NOT_IDENTIFIED,
        "P_BETTOR_ENSEMBLE": NOT_IDENTIFIED,
        "P_EXTERNAL_CONSENSUS": NOT_IDENTIFIED,
        "P_FUNDAMENTAL": NOT_IDENTIFIED,
        "P_EVENT_DISTRIBUTION": NOT_IDENTIFIED,
        "P_WHALE_RESIDUAL": NOT_IDENTIFIED,
        # With one expert, the ensemble IS that expert. Reporting a pooled
        # number here would imply a combination that did not happen.
        "P_ENSEMBLE_RAW": fv,
        "P_SETTLEMENT_CALIBRATED": fv,

        "SETTLEMENT_FAIR_VALUE": fv,
        "SETTLEMENT_FV_LOWER": (fv_lower if fv_lower is not None
                                else NOT_IDENTIFIED),
        "SETTLEMENT_FV_UPPER": (fv_upper if fv_upper is not None
                                else NOT_IDENTIFIED),

        "P_FILL": NOT_IDENTIFIED,
        "P_FILL_STATUS": "NOT_IDENTIFIED",
        "P_FILL_LOWER": NOT_IDENTIFIED,
        "P_FILL_UPPER": NOT_IDENTIFIED,
        "ONE_ORDER_DOES_NOT_IDENTIFY_P_FILL": ONE_ORDER_DOES_NOT_IDENTIFY_P_FILL,
        "VALUE_CONDITIONAL_ON_FILL": NOT_IDENTIFIED,

        "BEST_ACTION": "NO_TRADE",
        "BEST_ACTION_EV": NOT_IDENTIFIED,
        "BEST_ACTION_EV_LOWER": NOT_IDENTIFIED,
        "P_BEST_ACTION_EV_POSITIVE": NOT_IDENTIFIED,
        "WHY_NO_TRADE": (
            "NO_TRADE is the only admissible answer when the action EVs cannot "
            "be computed; it is not the winner of a comparison, because no "
            "comparison was possible"),

        "EV_CORE_VERSION": EV_CORE_VERSION,
        "MODEL_VERSION": MODEL_VERSION,
        "CALIBRATION_VERSION": CALIBRATION_VERSION,
        "WHY_IDENTITY_CALIBRATION": WHY_IDENTITY_CALIBRATION,
        "DATA_CUTOFF": data_cutoff or NOT_IDENTIFIED,
        "UNCERTAINTY_STATUS": ("SETTLEMENT_FV_INTERVAL_ONLY"
                               if fv_lower is not None else NOT_IDENTIFIED),
        "FAIR_VALUE_STATUS": FAIR_VALUE_STATUS,
        "CHAMPION_IS_THE_MARKET": CHAMPION_IS_THE_MARKET,
        "B0_VENUE_RAW": B0_VENUE_RAW,
        "B0_IS_NOT": B0_IS_NOT,
        "B0_DEFINITION": B0_DEFINITION,
        "B0_TIMESTAMP_POLICY": B0_TIMESTAMP_POLICY,
        "B0_QUOTE_AGE_POLICY": B0_QUOTE_AGE_POLICY,
        "NEVER_CONFLATE_THE_OBJECTS": NEVER_CONFLATE_THE_OBJECTS,
        "ABSENT_BECAUSE": dict(ABSENT_BECAUSE),
    }
    for h in EXECUTION_HORIZONS_S:
        rec["EXECUTION_FAIR_VALUE_%dS" % h] = NOT_IDENTIFIED
    for a in ("MAKE", "TAKE", "HOLD", "PAIR", "HEDGE", "PASSIVE_EXIT",
              "AGGRESSIVE_EXIT", "NO_TRADE"):
        rec["EV_%s" % a] = NOT_IDENTIFIED
    return rec


def completeness(rec):
    """Which contract fields are present, and which are absent and why."""
    missing = [f for f in OUTPUT_FIELDS if f not in rec]
    absent = [f for f in OUTPUT_FIELDS
              if rec.get(f) in (NOT_IDENTIFIED, NOT_ESTABLISHED)]
    return {
        "FIELDS_REQUIRED": len(OUTPUT_FIELDS),
        "FIELDS_MISSING_FROM_RECEIPT": missing,
        "CONTRACT_COMPLETE": not missing,
        "FIELDS_NOT_IDENTIFIED": absent,
        "NOT_IDENTIFIED_COUNT": len(absent),
        "A_MISSING_FIELD_IS_A_BUG": (
            "every contract field must be PRESENT; being NOT_IDENTIFIED is a "
            "statement, being absent from the dict is a defect"),
    }


def describe():
    return {
        "EV_CORE_VERSION": EV_CORE_VERSION,
        "FAIR_VALUE_STATUS": FAIR_VALUE_STATUS,
        "MODEL_VERSION": MODEL_VERSION,
        "CALIBRATION_VERSION": CALIBRATION_VERSION,
        "ACTIONS": list(ACTIONS),
        "OUTPUT_FIELDS": list(OUTPUT_FIELDS),
        "ABSENT_BECAUSE": dict(ABSENT_BECAUSE),
        "P_FILL_LADDER": list(P_FILL_LADDER),
        "CHAMPION_IS_THE_MARKET": CHAMPION_IS_THE_MARKET,
        "B0_VENUE_RAW": B0_VENUE_RAW,
        "B0_IS_NOT": B0_IS_NOT,
        "GENERATION_1_FROZEN": GENERATION_1_FROZEN,
        "B0_DEFINITION": B0_DEFINITION,
        "B0_DEFINITION_DETAIL": B0_DEFINITION_DETAIL,
        "B0_TIMESTAMP_POLICY": B0_TIMESTAMP_POLICY,
        "B0_QUOTE_AGE_POLICY": B0_QUOTE_AGE_POLICY,
        "B0_LAST_STATUS": B0_LAST_STATUS,
        "B0_MID_STATUS": B0_MID_STATUS,
        "MARKET_PRICE_FIELDS": dict(MARKET_PRICE_FIELDS),
        "NEVER_SUBSTITUTE_MID_FOR_EXECUTION": NEVER_SUBSTITUTE_MID_FOR_EXECUTION,
        "BENCHMARK_FREEZE_RULE": BENCHMARK_FREEZE_RULE,
        "GENERATION_1_RESULT": dict(GENERATION_1_RESULT),
        "GENERATION_1_FINAL_HOLDOUT": GENERATION_1_FINAL_HOLDOUT,
        "HOLDOUT_PERMITTED_USE": HOLDOUT_PERMITTED_USE,
        "HOLDOUT_FORBIDDEN_USES": list(HOLDOUT_FORBIDDEN_USES),
        "NEXT_GENERATION_REQUIRES": NEXT_GENERATION_REQUIRES,
        "PROBABILITY_OBJECTS": dict(PROBABILITY_OBJECTS),
        "NEVER_CONFLATE_THE_OBJECTS": NEVER_CONFLATE_THE_OBJECTS,
        "NO_SUBMIT_PATH_EXISTS": True,
    }


def to_json(rep):
    return json.dumps(rep, indent=1, sort_keys=True, default=str)


# ===========================================================================
# GENERATION 2 HOLDOUT -- FROZEN, AND PROSPECTIVE (directive section 20)
# ===========================================================================
#
# Generation 1's final holdout was burned: it was consulted while choosing a
# model, so it can report and cannot judge. Generation 2 must not repeat that,
# and the only reliable way to stop it is to put the holdout somewhere no
# amount of development can reach -- IN THE FUTURE.
#
# So Gen2's holdout is not a slice of the retained corpus. It is every event
# that settles after the freeze stamp below. Nothing in it exists yet. It
# cannot be peeked at, cannot leak into a feature, and cannot be re-drawn if
# the first look disappoints.

GEN2_PROSPECTIVE_HOLDOUT_STATUS = "FROZEN_PROSPECTIVE"
GEN2_HOLDOUT_KIND = "PROSPECTIVE_NOT_A_SLICE_OF_RETAINED_DATA"
GEN2_FREEZE_STAMP = "2026-09-17T14:00:00Z"
GEN2_HOLDOUT_DEFINITION = (
    "every venue event whose settlement timestamp is strictly after "
    "GEN2_FREEZE_STAMP, on the market families predeclared below")

GEN2_DEVELOPMENT_DATA = (
    "everything settled at or before the freeze stamp, used walk-forward")
GEN2_EVALUATION_TRIGGER = (
    "the first report that quotes a Gen2 holdout number; until then no Gen2 "
    "holdout result may be computed, quoted or glanced at")

GEN2_FORBIDDEN_USES_BEFORE_THE_TRIGGER = (
    "selecting a model family",
    "selecting features",
    "selecting a calibration method",
    "selecting hyper-parameters",
    "selecting a sport or market family",
    "selecting thresholds",
    "selecting ensemble weights",
    "deciding whether to keep a challenger",
)

# Section 15: the challengers are named NOW, before the data that will judge
# them exists. Naming them afterwards is how a subgroup finding gets mined out
# of noise -- and V1 produced exactly the tempting subgroups, so the discipline
# is not hypothetical.
GEN2_PREREGISTERED_CHALLENGERS = (
    "FULL_TIME_MONEYLINE",
    "FULL_TIME_TOTAL",
    "DRAW",
    "EXACT_SCORE",
)
GEN2_PREREGISTRATION_NOTE = (
    "EXACT_SCORE is on the list because its leave-one-family-out residual kept "
    "its sign (+0.340) when every other family's reversed. That makes it a "
    "CHALLENGER. It is not validated alpha, and the whole point of naming it "
    "here is that the claim will be judged on events that did not suggest it."
)

GEN2_REGIME_FINDINGS_MAY_NOT_BE_OPTIMISED_ON_THE_SAMPLE_THAT_SUGGESTED_THEM = True


def gen2_holdout():
    return {
        "GEN2_PROSPECTIVE_HOLDOUT_STATUS": GEN2_PROSPECTIVE_HOLDOUT_STATUS,
        "GEN2_HOLDOUT_KIND": GEN2_HOLDOUT_KIND,
        "GEN2_FREEZE_STAMP": GEN2_FREEZE_STAMP,
        "GEN2_HOLDOUT_DEFINITION": GEN2_HOLDOUT_DEFINITION,
        "GEN2_DEVELOPMENT_DATA": GEN2_DEVELOPMENT_DATA,
        "GEN2_EVALUATION_TRIGGER": GEN2_EVALUATION_TRIGGER,
        "GEN2_FORBIDDEN_USES_BEFORE_THE_TRIGGER":
            list(GEN2_FORBIDDEN_USES_BEFORE_THE_TRIGGER),
        "GEN2_PREREGISTERED_CHALLENGERS": list(GEN2_PREREGISTERED_CHALLENGERS),
        "GEN2_PREREGISTRATION_NOTE": GEN2_PREREGISTRATION_NOTE,
        "GENERATION_1_FINAL_HOLDOUT": GENERATION_1_FINAL_HOLDOUT,
    }
