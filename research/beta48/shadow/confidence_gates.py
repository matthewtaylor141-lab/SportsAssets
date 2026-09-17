"""Sections 19, 20, 22, 23, 25. Disagreement, data contracts, and the gates.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IS TRAINED HERE.

WHY GATES INSTEAD OF JUDGEMENT
------------------------------
Confidence that moves with the last result is not confidence, it is mood. The
three gates below are written before the evidence exists, so a later "this
feels promising" cannot promote anything. Each returns the list of conditions
NOT met, which is the only honest summary of where the programme stands.
"""

NOT_IDENTIFIED = "NOT_IDENTIFIED"

NOTHING_IS_TRAINED_HERE = True
DO_NOT_UPDATE_CONFIDENCE_EMOTIONALLY = (
    "confidence that moves with the last result is mood, not evidence. These "
    "conditions are fixed before the evidence exists")


# --- Section 19. The disagreement engine. ----------------------------------

DISAGREEMENT_ROLE = "FILTER_AND_ORTHOGONALITY_TEST"
NOT_THE_PRIMARY_THREAD = (
    "pure handicapping is NOT the primary thread. BETTOR already knows the "
    "market beats its fundamental models on settlement. The useful question "
    "is narrower: when BETTOR disagrees STRONGLY, does it carry information "
    "the market has not already priced?")

PROBABILITY_SOURCES = ("P_MARKET", "P_EXTERNAL_CONSENSUS",
                       "P_BETTOR_FUNDAMENTAL")

DISAGREEMENT_MEASURES = ("FUNDAMENTAL_MINUS_MARKET", "EXTERNAL_MINUS_MARKET",
                         "FUNDAMENTAL_MINUS_EXTERNAL")

# Pre-registered BEFORE any evaluation sample is scored.
DISAGREEMENT_BUCKETS = ((0.00, 0.02), (0.02, 0.05), (0.05, 0.10),
                        (0.10, 1.00))
BUCKETS_PRE_REGISTERED = True
DO_NOT_TUNE_BUCKETS_ON_THE_EVALUATION_SAMPLE = (
    "bucket edges chosen after seeing which cut looks significant are a "
    "result, not a design. They are fixed here")


def disagreement(p_market=None, p_external=None, p_fundamental=None):
    """The three differences, each NOT_IDENTIFIED when a source is absent."""
    def _sub(a, b):
        if a is None or b is None:
            return NOT_IDENTIFIED
        return round(float(a) - float(b), 10)
    out = {
        "P_MARKET": p_market if p_market is not None else NOT_IDENTIFIED,
        "P_EXTERNAL_CONSENSUS": (p_external if p_external is not None
                                 else NOT_IDENTIFIED),
        "P_BETTOR_FUNDAMENTAL": (p_fundamental if p_fundamental is not None
                                 else NOT_IDENTIFIED),
        "FUNDAMENTAL_MINUS_MARKET": _sub(p_fundamental, p_market),
        "EXTERNAL_MINUS_MARKET": _sub(p_external, p_market),
        "FUNDAMENTAL_MINUS_EXTERNAL": _sub(p_fundamental, p_external),
        "DISAGREEMENT_ROLE": DISAGREEMENT_ROLE,
        "NOT_THE_PRIMARY_THREAD": NOT_THE_PRIMARY_THREAD,
    }
    d = out["FUNDAMENTAL_MINUS_MARKET"]
    out["BUCKET"] = bucket_of(d)
    return out


def bucket_of(delta):
    if delta == NOT_IDENTIFIED or delta is None:
        return NOT_IDENTIFIED
    a = abs(float(delta))
    for lo, hi in DISAGREEMENT_BUCKETS:
        if lo <= a < hi:
            return "%.2f-%.2f" % (lo, hi)
    return "%.2f-%.2f" % DISAGREEMENT_BUCKETS[-1]


THE_QUESTION = (
    "within each pre-registered disagreement bucket, does adding "
    "P_BETTOR_FUNDAMENTAL to P_MARKET improve the score out of sample on "
    "unseen EVENTS? Orthogonality is an incremental question, never a "
    "standalone one")


# --- Section 20. The player / xG data contract. ----------------------------

PLAYER_XG_FIELDS = ("XG", "NPXG", "PLAYER_STRENGTH", "CONFIRMED_LINEUP",
                    "EXPECTED_LINEUP", "INJURY", "SUSPENSION", "GOALKEEPER",
                    "REST", "TRAVEL")

MANDATORY_FIELD = "INFORMATION_AVAILABLE_AT"

NO_TIMESTAMP_NO_HIGH_INTEGRITY = (
    "a fact without INFORMATION_AVAILABLE_AT cannot be shown to have been "
    "knowable before the decision. It is not eligible for the "
    "highest-integrity as-of lane -- not because it is wrong, but because its "
    "timing is unproven")

INTEGRITY_LANES = ("HIGH_INTEGRITY", "RESEARCH_ONLY", "EXCLUDED")


def contract_row(field, value=None, information_available_at=None,
                 decision_timestamp=None):
    """Admit a fundamental fact into a lane, by its timestamp alone."""
    if field not in PLAYER_XG_FIELDS:
        return {"FIELD": field, "LANE": "EXCLUDED",
                "WHY": "not a declared field", "DECLARED": PLAYER_XG_FIELDS}
    if not information_available_at:
        return {"FIELD": field, "VALUE": value,
                "INFORMATION_AVAILABLE_AT": NOT_IDENTIFIED,
                "LANE": "RESEARCH_ONLY",
                "ELIGIBLE_FOR_HIGH_INTEGRITY": False,
                "NO_TIMESTAMP_NO_HIGH_INTEGRITY":
                    NO_TIMESTAMP_NO_HIGH_INTEGRITY}
    if decision_timestamp and str(information_available_at) > \
            str(decision_timestamp):
        return {"FIELD": field, "VALUE": value,
                "INFORMATION_AVAILABLE_AT": information_available_at,
                "LANE": "EXCLUDED",
                "ELIGIBLE_FOR_HIGH_INTEGRITY": False,
                "WHY": "the fact became known AFTER the decision it would "
                       "have informed"}
    return {"FIELD": field, "VALUE": value,
            "INFORMATION_AVAILABLE_AT": information_available_at,
            "LANE": "HIGH_INTEGRITY", "ELIGIBLE_FOR_HIGH_INTEGRITY": True}


# --- Section 22. The three promotion gates. --------------------------------

SHORT_HORIZON_STRUCTURE_GATE_CONDITIONS = (
    "BEATS_NULL_AND_SIMPLE_BASELINE",
    "CHRONOLOGICAL_OUT_OF_SAMPLE",
    "EVENT_SAFE_SPLITS",
    "STABLE_SIGN_ACROSS_FOLDS",
    "ECONOMICALLY_MEANINGFUL_RELATIVE_TO_SPREAD",
    "REGIME_ROBUST",
    "SUFFICIENT_INDEPENDENT_EVENTS",
)

EXECUTION_EDGE_GATE_CONDITIONS = (
    "REAL_BETTOR_ORDERS_EXIST",
    "P_FILL_IDENTIFIED",
    "FILL_CONDITIONED_MARKOUTS_MEASURED",
    "QUEUE_MODEL_CALIBRATED",
    "POSITIVE_EXPECTED_NET_AFTER_FEES_AND_INCENTIVES",
    "POSITIVE_OUT_OF_SAMPLE_ACTION_EV",
)

SCALABILITY_GATE_CONDITIONS = (
    "SUFFICIENT_OPPORTUNITY_ARRIVAL",
    "CAPITAL_THROUGHPUT_DEMONSTRATED",
    "EVENT_DIVERSIFICATION",
    "NO_CONCENTRATION_IN_ONE_LEAGUE_OR_REGIME",
    "EV_SURVIVES_SIZE_INCREASES",
)

GATES = {
    "SHORT_HORIZON_STRUCTURE_GATE": SHORT_HORIZON_STRUCTURE_GATE_CONDITIONS,
    "EXECUTION_EDGE_GATE": EXECUTION_EDGE_GATE_CONDITIONS,
    "SCALABILITY_GATE": SCALABILITY_GATE_CONDITIONS,
}

GATE_ORDER = ("SHORT_HORIZON_STRUCTURE_GATE", "EXECUTION_EDGE_GATE",
              "SCALABILITY_GATE")

GATES_ARE_ORDERED = (
    "execution edge is not asked before short-horizon structure exists, and "
    "scalability is not asked before execution edge does. Passing a later "
    "gate on an unpassed earlier one is not a result")


def gate(name, met=None):
    """Which conditions are met, and which are not. Fails closed."""
    conds = GATES.get(name)
    if not conds:
        return {"GATE": name, "STATUS": "UNKNOWN_GATE",
                "DECLARED": tuple(GATES)}
    met = set(met or ())
    unmet = [c for c in conds if c not in met]
    return {
        "GATE": name,
        "CONDITIONS": conds,
        "MET": [c for c in conds if c in met],
        "NOT_MET": unmet,
        "PASSED": not unmet,
        "DO_NOT_UPDATE_CONFIDENCE_EMOTIONALLY":
            DO_NOT_UPDATE_CONFIDENCE_EMOTIONALLY,
    }


def programme_status(met_by_gate=None):
    """Where the programme actually stands, gate by gate, in order."""
    met_by_gate = met_by_gate or {}
    rows, first_open = [], None
    prior_passed = True
    for name in GATE_ORDER:
        g = gate(name, met_by_gate.get(name))
        g["BLOCKED_BY_EARLIER_GATE"] = not prior_passed
        if not g["PASSED"] and first_open is None:
            first_open = name
        prior_passed = prior_passed and g["PASSED"]
        rows.append(g)
    return {
        "GATE_ORDER": GATE_ORDER,
        "GATES": rows,
        "FIRST_UNPASSED_GATE": first_open or "ALL_PASSED",
        "GATES_ARE_ORDERED": GATES_ARE_ORDERED,
        "ANY_GATE_PASSED": any(g["PASSED"] for g in rows),
    }


# --- Section 23. The first capture is not an admission. --------------------

FIRST_CAPTURE_IS_FOR = ("MEASUREMENT_INTEGRITY", "SIGNAL_EXISTENCE",
                        "EFFECT_MAGNITUDE", "VARIANCE_ESTIMATION",
                        "NEXT_EXPERIMENT_SIZING")

FIRST_CAPTURE_IS_NOT_FOR = ("PRODUCTION_ADMISSION", "SCALABILITY_CLAIM",
                            "CAPITAL_ALLOCATION")

NO_SCALABILITY_FROM_THE_FIRST_CAPTURE = (
    "a three-event pilot can establish that a measurement works and roughly "
    "how big an effect might be. It cannot establish that the effect survives "
    "at size, across leagues, or over regimes -- those are the scalability "
    "gate's questions and it has not been asked")


def may_claim(claim, independent_events=0):
    """Fails closed on any claim the first capture cannot support."""
    c = str(claim).upper()
    if c in FIRST_CAPTURE_IS_NOT_FOR:
        return {"CLAIM": c, "PERMITTED": False,
                "WHY": NO_SCALABILITY_FROM_THE_FIRST_CAPTURE,
                "FIRST_CAPTURE_IS_FOR": FIRST_CAPTURE_IS_FOR}
    if c in FIRST_CAPTURE_IS_FOR:
        return {"CLAIM": c, "PERMITTED": True,
                "INDEPENDENT_EVENTS": independent_events}
    return {"CLAIM": c, "PERMITTED": False,
            "WHY": "not a declared purpose of the first capture",
            "FIRST_CAPTURE_IS_FOR": FIRST_CAPTURE_IS_FOR}


# --- Section 25. The moat, recorded explicitly. ----------------------------

MOAT_IS_NOT = "ONE_GREAT_PREDICTION_MODEL"
WHY_NOT_ONE_MODEL = (
    "a model can be reproduced by anyone with the same paper and the same "
    "data. The market already beats BETTOR's settlement models, and a better "
    "fit to the same single probability source cannot produce information "
    "that source does not contain")

MOAT_CANDIDATES = (
    "PROPRIETARY_MARKET_STATE_DATA",
    "EXTERNAL_INFORMATION_ALIGNMENT",
    "PRICE_MOVE_MODEL",
    "TOXICITY_MODEL",
    "QUEUE_FILL_MODEL",
    "INVENTORY_MODEL",
    "CAPITAL_ALLOCATION",
)

THE_DECISION_OBJECT = "FILL_CONDITIONED_ACTION_EV"

MOAT_STATUS = "CANDIDATE_ARCHITECTURE_NOT_DEMONSTRATED"


def moat():
    return {
        "MOAT_IS_NOT": MOAT_IS_NOT,
        "WHY_NOT_ONE_MODEL": WHY_NOT_ONE_MODEL,
        "MOAT_CANDIDATES": MOAT_CANDIDATES,
        "THE_DECISION_OBJECT": THE_DECISION_OBJECT,
        "MOAT_STATUS": MOAT_STATUS,
        "WHY_CANDIDATE": ("none of the seven components has been "
                          "demonstrated on prospective data yet. Calling it a "
                          "moat today would describe an intention"),
    }


def describe():
    return {
        "DISAGREEMENT_ROLE": DISAGREEMENT_ROLE,
        "NOT_THE_PRIMARY_THREAD": NOT_THE_PRIMARY_THREAD,
        "PROBABILITY_SOURCES": PROBABILITY_SOURCES,
        "DISAGREEMENT_MEASURES": DISAGREEMENT_MEASURES,
        "DISAGREEMENT_BUCKETS": DISAGREEMENT_BUCKETS,
        "BUCKETS_PRE_REGISTERED": BUCKETS_PRE_REGISTERED,
        "DO_NOT_TUNE_BUCKETS_ON_THE_EVALUATION_SAMPLE":
            DO_NOT_TUNE_BUCKETS_ON_THE_EVALUATION_SAMPLE,
        "THE_QUESTION": THE_QUESTION,
        "PLAYER_XG_FIELDS": PLAYER_XG_FIELDS,
        "MANDATORY_FIELD": MANDATORY_FIELD,
        "NO_TIMESTAMP_NO_HIGH_INTEGRITY": NO_TIMESTAMP_NO_HIGH_INTEGRITY,
        "GATES": {k: v for k, v in GATES.items()},
        "GATE_ORDER": GATE_ORDER,
        "GATES_ARE_ORDERED": GATES_ARE_ORDERED,
        "FIRST_CAPTURE_IS_FOR": FIRST_CAPTURE_IS_FOR,
        "FIRST_CAPTURE_IS_NOT_FOR": FIRST_CAPTURE_IS_NOT_FOR,
        "NO_SCALABILITY_FROM_THE_FIRST_CAPTURE":
            NO_SCALABILITY_FROM_THE_FIRST_CAPTURE,
        "MOAT": moat(),
        "DO_NOT_UPDATE_CONFIDENCE_EMOTIONALLY":
            DO_NOT_UPDATE_CONFIDENCE_EMOTIONALLY,
        "NOTHING_IS_TRAINED_HERE": NOTHING_IS_TRAINED_HERE,
    }
