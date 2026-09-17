"""Sections G and H. TOXICITY_V1, and benign-flow capacity.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IS TRAINED HERE.

THE DISTINCTION THAT DEFINES THIS MODULE
----------------------------------------
Before BETTOR has resting orders, what can be measured is:

    MARKET_STATE_TOXICITY
    "given this book state, how adversely does the executable price move next?"

What CANNOT be measured is:

    FILL_CONDITIONAL_TOXICITY
    "given that WE were filled here, how adversely does it move next?"

These differ because fills are SELECTED. We are filled precisely when somebody
wanted the other side, and that wanting is correlated with being right. The
conditional quantity is the one that costs money, and it is strictly worse than
the unconditional one. Reporting market-state toxicity as if it were
fill-conditional toxicity understates adverse selection by exactly the amount
that matters.

So V1 is labelled MARKET_STATE_TOXICITY and stays that way until BETTOR-native
fill evidence exists.
"""

NOT_IDENTIFIED = "NOT_IDENTIFIED"

NOTHING_IS_TRAINED_HERE = True

MODEL_NAME = "TOXICITY_V1"
TOXICITY_V1 = "MARKET_STATE_TOXICITY"
NOT_YET = "FILL_CONDITIONAL_TOXICITY"

WHY_THE_TWO_DIFFER = (
    "fills are SELECTED. We are filled exactly when somebody wanted the other "
    "side, and that wanting correlates with being right. So the "
    "fill-conditional quantity is strictly worse than the unconditional one, "
    "and it is the one that costs money")

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
            "NOT_YET": NOT_YET, "WHY_THE_TWO_DIFFER": WHY_THE_TWO_DIFFER,
            "UPGRADE_REQUIRES": UPGRADE_REQUIRES,
            "THIS_UNDERSTATES_ADVERSE_SELECTION": True}


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
        "THIS_IS_NOT_A_FILL_CONDITIONAL_RESULT": True,
        "THE_TEST": THE_TEST,
    }


def describe():
    return {
        "MODEL_NAME": MODEL_NAME,
        "TOXICITY_V1": TOXICITY_V1,
        "NOT_YET": NOT_YET,
        "WHY_THE_TWO_DIFFER": WHY_THE_TWO_DIFFER,
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
