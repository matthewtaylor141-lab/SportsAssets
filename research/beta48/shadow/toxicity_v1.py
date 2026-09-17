"""Sections G and H. TOXICITY_V1, and benign-flow capacity.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IS TRAINED HERE.

THE DISTINCTION THAT DEFINES THIS MODULE
----------------------------------------
Before BETTOR has resting orders, what can be measured is:

    MARKET_STATE_TOXICITY  =  UNCONDITIONAL_ON_BETTOR_FILL
    "given this book state, how adversely does the executable price move next?"

What CANNOT be measured is:

    FILL_CONDITIONAL_TOXICITY  =  NOT_IDENTIFIED_UNTIL_BETTOR_FILL_DATA
    "given that WE were filled here, how adversely does it move next?"

WHAT SELECTION DOES AND DOES NOT ESTABLISH
------------------------------------------
Fills are selected. That is true, and it licenses exactly one conclusion:

    D(FUTURE_MARKOUT | ORDER_FILLED, STATE)
        MAY DIFFER FROM
    D(FUTURE_MARKOUT | QUOTE_AVAILABLE, STATE)

It does NOT establish which way. An earlier version of this module asserted
that the fill-conditional quantity is "strictly worse". That was a hypothesis
written as a theorem. Informed takers plausibly create adverse selection -- but
liquidity-driven, hedging, rebalancing and impatient-benign flow are also
selected into our fills, and those can cut the other way. Selection is a
statement that a conditioning event may shift a distribution; the SIGN of the
shift is an empirical quantity.

So:

    FILL_SELECTION_EFFECT = NOT_IDENTIFIED

with three admissible outcomes, none privileged in advance:

    MORE_ADVERSE / NO_MATERIAL_DIFFERENCE / LESS_ADVERSE

No invariant, lower bound, prior conclusion or test expectation encodes a
direction. Only evidence may.
"""

NOT_IDENTIFIED = "NOT_IDENTIFIED"

NOTHING_IS_TRAINED_HERE = True

MODEL_NAME = "TOXICITY_V1"
TOXICITY_V1 = "MARKET_STATE_TOXICITY"
NOT_YET = "FILL_CONDITIONAL_TOXICITY"

MARKET_STATE_TOXICITY = "UNCONDITIONAL_ON_BETTOR_FILL"
FILL_CONDITIONAL_TOXICITY = "NOT_IDENTIFIED_UNTIL_BETTOR_FILL_DATA"

# The correction: a direction was asserted where only a difference is licensed.
FILL_SELECTION_EFFECT = NOT_IDENTIFIED

FILL_SELECTION_OUTCOMES = ("MORE_ADVERSE", "NO_MATERIAL_DIFFERENCE",
                           "LESS_ADVERSE")
NO_OUTCOME_IS_PRIVILEGED_IN_ADVANCE = True

WHY_THE_TWO_MAY_DIFFER = (
    "fills are SELECTED, so D(FUTURE_MARKOUT | ORDER_FILLED, STATE) MAY "
    "differ from D(FUTURE_MARKOUT | QUOTE_AVAILABLE, STATE). That is all "
    "selection establishes. The DIRECTION of the difference is empirical")

THE_CORRECTED_CLAIM = (
    "selection licenses 'may differ', not 'is worse'. An earlier version of "
    "this module asserted the fill-conditional quantity is strictly worse, "
    "which was a hypothesis written as a theorem")

ADVERSE_SELECTION_IS_A_HYPOTHESIS = (
    "informed takers plausibly create adverse selection. But liquidity, "
    "hedging, rebalancing and impatient-benign flow are also selected into "
    "our fills, and those can cut the other way. Which dominates on THIS "
    "venue is measured, not assumed")

UPGRADE_REQUIRES = "BETTOR_NATIVE_FILL_EVIDENCE"
UPGRADE_TO = "VALUE_CONDITIONAL_ON_FILL"

TARGET = ("expected adverse future EXECUTABLE-price movement after a "
          "hypothetical or current resting quote")

WHY_EXECUTABLE_NOT_MID = (
    "a maker does not exit at the mid. Measuring adverse movement against the "
    "mid flatters every result by half a spread")

FEATURES = (
    "MICROPRICE_MINUS_MID",
    "L1_OFI",
    "MULTI_LEVEL_OFI",
    "BOOK_IMBALANCE",
    "RECENT_AGGRESSOR_FLOW",
    "DEPTH_SLOPE",
    "SPREAD",
    "VOLATILITY",
    "EXTERNAL_ODDS_DISAGREEMENT",
    "EXTERNAL_ODDS_RECENT_MOVE",
    "CROSS_MARKET_RESIDUAL",
    "TIME_TO_EVENT",
    "QUOTE_AGE",
    "RECENT_PRICE_ACCELERATION",
)

# Which features the current frozen capture can actually supply. A feature the
# capture cannot produce is NOT silently dropped -- it is listed as blocked, so
# a thin V1 is visibly thin rather than quietly narrow.
FEATURE_AVAILABILITY = {
    "MICROPRICE_MINUS_MID": "AWAITING_CAPTURE",
    "L1_OFI": "AWAITING_CAPTURE",
    "MULTI_LEVEL_OFI": "AWAITING_CAPTURE_DEPTH_DEPENDENT",
    "BOOK_IMBALANCE": "AWAITING_CAPTURE",
    "RECENT_AGGRESSOR_FLOW": "AWAITING_CAPTURE_REQUIRES_TRADE_SIDE",
    "DEPTH_SLOPE": "AWAITING_CAPTURE_DEPTH_DEPENDENT",
    "SPREAD": "AWAITING_CAPTURE",
    "VOLATILITY": "AWAITING_CAPTURE",
    "EXTERNAL_ODDS_DISAGREEMENT": "BLOCKED_NO_EXTERNAL_ODDS",
    "EXTERNAL_ODDS_RECENT_MOVE": "BLOCKED_NO_EXTERNAL_ODDS",
    "CROSS_MARKET_RESIDUAL": "BLOCKED_INSUFFICIENT_MARKET_FAMILY_DENSITY",
    "TIME_TO_EVENT": "BLOCKED_START_TIME_CLASS_A_IS_EMPTY",
    "QUOTE_AGE": "AWAITING_CAPTURE",
    "RECENT_PRICE_ACCELERATION": "AWAITING_CAPTURE",
}


def available_features(capture_supplies=()):
    """Which features V1 may use, and which are blocked and why."""
    have = set(capture_supplies or ())
    usable, blocked = [], {}
    for f in FEATURES:
        status = FEATURE_AVAILABILITY.get(f, NOT_IDENTIFIED)
        if f in have and not status.startswith("BLOCKED"):
            usable.append(f)
        else:
            blocked[f] = status
    return {"USABLE": usable, "BLOCKED": blocked,
            "USABLE_N": len(usable), "DECLARED_N": len(FEATURES),
            "A_THIN_V1_IS_VISIBLY_THIN": (
                "features the capture cannot supply are listed, not dropped, "
                "so the model's narrowness is part of its result")}


def label(has_native_fill_evidence=False):
    """What may this model legitimately be called?"""
    if has_native_fill_evidence:
        return {"LABEL": UPGRADE_TO, "FILL_CONDITIONAL": True}
    return {"LABEL": TOXICITY_V1, "FILL_CONDITIONAL": False,
            "CONDITIONING": MARKET_STATE_TOXICITY,
            "NOT_YET": NOT_YET,
            "FILL_CONDITIONAL_TOXICITY": FILL_CONDITIONAL_TOXICITY,
            "FILL_SELECTION_EFFECT": FILL_SELECTION_EFFECT,
            "FILL_SELECTION_OUTCOMES": FILL_SELECTION_OUTCOMES,
            "WHY_THE_TWO_MAY_DIFFER": WHY_THE_TWO_MAY_DIFFER,
            "THE_CORRECTED_CLAIM": THE_CORRECTED_CLAIM,
            "UPGRADE_REQUIRES": UPGRADE_REQUIRES,
            "DIRECTION_OF_THE_DIFFERENCE": NOT_IDENTIFIED}


# --- The eventual test, with its sign convention frozen NOW. ---------------

SELECTION_TEST_NAME = "FILL_SELECTION_MARKOUT_DELTA"

SELECTION_TEST_HORIZONS_S = (5, 30, 60, 300)

SIGN_CONVENTION = ("FILL_SELECTION_MARKOUT_DELTA_h = "
                   "MARKOUT_FILLED_h - MATCHED_COUNTERFACTUAL_MARKOUT_h")
SIGN_CONVENTION_MEANS = {
    "NEGATIVE": "MORE_ADVERSE -- our fills markout worse than matched quotes",
    "ZERO": "NO_MATERIAL_DIFFERENCE",
    "POSITIVE": "LESS_ADVERSE -- our fills markout better than matched quotes",
}
SIGN_CONVENTION_FROZEN_BEFORE_EVALUATION = True
WHY_FREEZE_THE_SIGN = (
    "a sign convention chosen after seeing the result is how 'worse' and "
    "'better' get swapped to match the story. It is fixed here, before any "
    "fill exists")

# Matching is the whole experiment. Comparing fills to arbitrary non-fill
# observations measures the difference between their states, not selection.
MATCH_CONTROLS = ("QUOTE_PRICE", "SIDE", "SPREAD", "DEPTH", "IMBALANCE",
                  "OFI", "VOLATILITY", "TIME_TO_EVENT", "MARKET_FAMILY",
                  "QUOTE_AGE", "EXTERNAL_INFORMATION_STATE")

UNMATCHED_COMPARISON_IS_NOT_THE_TEST = (
    "do not compare fills to arbitrary non-fill observations with materially "
    "different states. That comparison measures the difference between the "
    "states, and attributes it to selection")


def selection_delta(markout_filled=None, matched_counterfactual=None,
                    horizon_s=None, controls_matched=()):
    """FILL_SELECTION_MARKOUT_DELTA_h. Refuses on an unmatched comparison."""
    missing = [c for c in MATCH_CONTROLS if c not in set(controls_matched or ())]
    if markout_filled is None or matched_counterfactual is None:
        return {"TEST": SELECTION_TEST_NAME, "HORIZON_S": horizon_s,
                "DELTA": NOT_IDENTIFIED,
                "FILL_SELECTION_EFFECT": NOT_IDENTIFIED,
                "WHY": "no BETTOR fill evidence exists",
                "SIGN_CONVENTION": SIGN_CONVENTION}
    if missing:
        return {"TEST": SELECTION_TEST_NAME, "HORIZON_S": horizon_s,
                "DELTA": NOT_IDENTIFIED,
                "FILL_SELECTION_EFFECT": NOT_IDENTIFIED,
                "CONTROLS_NOT_MATCHED": missing,
                "WHY": "the comparison is not matched, so it is not the test",
                "UNMATCHED_COMPARISON_IS_NOT_THE_TEST":
                    UNMATCHED_COMPARISON_IS_NOT_THE_TEST,
                "SIGN_CONVENTION": SIGN_CONVENTION}
    delta = float(markout_filled) - float(matched_counterfactual)
    effect = ("NO_MATERIAL_DIFFERENCE" if delta == 0 else
              ("MORE_ADVERSE" if delta < 0 else "LESS_ADVERSE"))
    return {"TEST": SELECTION_TEST_NAME, "HORIZON_S": horizon_s,
            "DELTA": round(delta, 8),
            "FILL_SELECTION_EFFECT": effect,
            "SIGN_CONVENTION": SIGN_CONVENTION,
            "SIGN_CONVENTION_MEANS": dict(SIGN_CONVENTION_MEANS),
            "CONTROLS_MATCHED": sorted(set(controls_matched or ())),
            "ALL_OUTCOMES_WERE_ADMISSIBLE": FILL_SELECTION_OUTCOMES}


# --- Section H. Benign-flow capacity. --------------------------------------

BENIGN_FLOW_HYPOTHESIS = (
    "maximum SAFE quote size may be set by expected BENIGN flow rather than "
    "by risk capacity. Quoting more than the uninformed flow likely to arrive "
    "before material adverse information means the excess is filled "
    "disproportionately by the informed side")

BENIGN_FLOW_CAPACITY_STATUS = NOT_IDENTIFIED

DO_NOT_IMPORT = ("14_OVER_PROB", "85_OVER_PROB",
                 "ANY_SIMULATOR_SPECIFIC_CONSTANT")

WHY_NOT_IMPORT = (
    "those constants were fitted inside another project's simulator, against "
    "its flow mix and its fee schedule. Importing one would import a "
    "conclusion about a different market. BETTOR's relationship is learned "
    "prospectively or left NOT_IDENTIFIED")

THE_TEST = (
    "do quote sizes materially ABOVE the estimated benign capacity suffer "
    "worse markouts than quotes at or below it? That is a measurable "
    "prediction, and it is the test rather than the assumption")


def benign_flow_capacity(state=None, horizon_s=None, measured=None):
    """Estimated quantity likely to execute before material adverse info.

    Returns NOT_IDENTIFIED until BETTOR has measured it. There is deliberately
    no fallback formula: a fabricated capacity would silently become a size
    limit, and a size limit nothing measured supports is worse than none.
    """
    if measured is None:
        return {
            "BENIGN_FLOW_CAPACITY": NOT_IDENTIFIED,
            "STATUS": BENIGN_FLOW_CAPACITY_STATUS,
            "STATE": state if state is not None else NOT_IDENTIFIED,
            "HORIZON_S": horizon_s if horizon_s is not None else NOT_IDENTIFIED,
            "WHY": ("not measured on BETTOR's own flow. No default is "
                    "supplied, because a fabricated capacity would become a "
                    "size limit that nothing supports"),
            "DO_NOT_IMPORT": DO_NOT_IMPORT,
            "WHY_NOT_IMPORT": WHY_NOT_IMPORT,
        }
    return {"BENIGN_FLOW_CAPACITY": measured, "STATUS": "MEASURED",
            "STATE": state, "HORIZON_S": horizon_s, "THE_TEST": THE_TEST}


def capacity_test(rows, capacity_of=None, markout_key="MARKOUT",
                  size_key="SIZE"):
    """Compare markouts above vs at-or-below the estimated capacity.

    Refuses when capacity is unknown, rather than splitting on a guess.
    """
    capacity_of = capacity_of or (lambda r: r.get("BENIGN_FLOW_CAPACITY"))
    above, below, unknown = [], [], 0
    for r in rows or ():
        cap = capacity_of(r)
        m = r.get(markout_key)
        s = r.get(size_key)
        if cap in (None, NOT_IDENTIFIED) or m is None or s is None:
            unknown += 1
            continue
        (above if float(s) > float(cap) else below).append(float(m))
    if not above or not below:
        return {"STATUS": "NOT_TESTABLE",
                "ABOVE_CAPACITY_N": len(above),
                "AT_OR_BELOW_CAPACITY_N": len(below),
                "ROWS_WITHOUT_A_CAPACITY": unknown,
                "WHY": ("both arms need rows. A capacity that was never "
                        "estimated cannot split anything"),
                "THE_TEST": THE_TEST}
    ma = sum(above) / len(above)
    mb = sum(below) / len(below)
    return {
        "STATUS": "MEASURED",
        "MEAN_MARKOUT_ABOVE_CAPACITY": round(ma, 6),
        "MEAN_MARKOUT_AT_OR_BELOW": round(mb, 6),
        "DIFFERENCE": round(ma - mb, 6),
        "ABOVE_CAPACITY_N": len(above),
        "AT_OR_BELOW_CAPACITY_N": len(below),
        "ROWS_WITHOUT_A_CAPACITY": unknown,
        "HYPOTHESIS_DIRECTION": "oversized quotes should markout WORSE",
        "THE_DIRECTION_IS_A_HYPOTHESIS_NOT_A_GUARANTEE": True,
        "THIS_IS_NOT_A_FILL_CONDITIONAL_RESULT": True,
        "THE_TEST": THE_TEST,
    }


def describe():
    return {
        "MODEL_NAME": MODEL_NAME,
        "TOXICITY_V1": TOXICITY_V1,
        "MARKET_STATE_TOXICITY": MARKET_STATE_TOXICITY,
        "FILL_CONDITIONAL_TOXICITY": FILL_CONDITIONAL_TOXICITY,
        "FILL_SELECTION_EFFECT": FILL_SELECTION_EFFECT,
        "FILL_SELECTION_OUTCOMES": FILL_SELECTION_OUTCOMES,
        "NO_OUTCOME_IS_PRIVILEGED_IN_ADVANCE":
            NO_OUTCOME_IS_PRIVILEGED_IN_ADVANCE,
        "NOT_YET": NOT_YET,
        "WHY_THE_TWO_MAY_DIFFER": WHY_THE_TWO_MAY_DIFFER,
        "THE_CORRECTED_CLAIM": THE_CORRECTED_CLAIM,
        "ADVERSE_SELECTION_IS_A_HYPOTHESIS": ADVERSE_SELECTION_IS_A_HYPOTHESIS,
        "SELECTION_TEST_NAME": SELECTION_TEST_NAME,
        "SIGN_CONVENTION": SIGN_CONVENTION,
        "SIGN_CONVENTION_MEANS": dict(SIGN_CONVENTION_MEANS),
        "SIGN_CONVENTION_FROZEN_BEFORE_EVALUATION":
            SIGN_CONVENTION_FROZEN_BEFORE_EVALUATION,
        "MATCH_CONTROLS": MATCH_CONTROLS,
        "UNMATCHED_COMPARISON_IS_NOT_THE_TEST":
            UNMATCHED_COMPARISON_IS_NOT_THE_TEST,
        "UPGRADE_REQUIRES": UPGRADE_REQUIRES,
        "UPGRADE_TO": UPGRADE_TO,
        "TARGET": TARGET,
        "WHY_EXECUTABLE_NOT_MID": WHY_EXECUTABLE_NOT_MID,
        "FEATURES": FEATURES,
        "FEATURE_AVAILABILITY": dict(FEATURE_AVAILABILITY),
        "BENIGN_FLOW_HYPOTHESIS": BENIGN_FLOW_HYPOTHESIS,
        "BENIGN_FLOW_CAPACITY_STATUS": BENIGN_FLOW_CAPACITY_STATUS,
        "DO_NOT_IMPORT": DO_NOT_IMPORT,
        "WHY_NOT_IMPORT": WHY_NOT_IMPORT,
        "THE_TEST": THE_TEST,
        "NOTHING_IS_TRAINED_HERE": NOTHING_IS_TRAINED_HERE,
    }
