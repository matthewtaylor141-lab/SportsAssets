"""Model registry, training manifest, promotion gate, rollback, kill switches.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IS TRAINED HERE. AUTO_PRODUCTION_PROMOTION = DISABLED.

WHAT THIS MODULE REFUSES
------------------------
- a model in production without full lineage
- a training run whose metric, subgroup or threshold was chosen after seeing
  the test result
- a promotion on accuracy alone
- a tie counted as a win
- a challenger promoted because its backtest looked good
- online weight mutation in the live champion

ONLINE BAYES, OFFLINE PROMOTION
-------------------------------
Beliefs update continuously. Deployed policy versions do not. New observations
feed the dataset; a separate challenger retrains; it passes gates; only then
may an immutable new version be promoted, by a human.
"""

import hashlib
import json

NOT_IDENTIFIED = "NOT_IDENTIFIED"

NOTHING_IS_TRAINED_HERE = True
AUTO_PRODUCTION_PROMOTION = "DISABLED"
NO_ONLINE_WEIGHT_MUTATION = True

WHY_NO_ONLINE_MUTATION = (
    "updating a deployed champion's weights after every observation is "
    "uncontrolled online learning: the thing serving decisions is never the "
    "thing anybody validated, and a bad hour rewrites the policy with no "
    "gate and no rollback point")

ONLINE_BAYES_OFFLINE_PROMOTION = (
    "beliefs update continuously; deployed policy versions do not. That "
    "separation is the whole safety property")


# --- Section 9. Specialist families. ---------------------------------------

MODEL_FAMILIES = (
    "SETTLEMENT_FAIR_VALUE", "SHORT_HORIZON_PRICE_MOVE", "EXTERNAL_LEAD_LAG",
    "CROSS_MARKET_RELATIVE_VALUE", "MARKET_STATE_TOXICITY",
    "FILL_PROBABILITY", "FILL_CONDITIONAL_VALUE", "QUEUE_TIME_TO_FILL",
    "INVENTORY_TRANSITION_VALUE", "CAPITAL_OCCUPANCY", "ACTION_EV",
)

NOT_ONE_MONOLITH = (
    "one model that predicts everything is one model that can be wrong about "
    "everything at once, with no way to tell which part failed. Separate "
    "families have separate features, targets, validation and confidence")

CADENCES = {"FAST": ("SHORT_HORIZON_PRICE_MOVE",),
            "MEDIUM": ("MARKET_STATE_TOXICITY", "FILL_PROBABILITY",
                       "QUEUE_TIME_TO_FILL"),
            "SLOW": ("SETTLEMENT_FAIR_VALUE",
                     "CROSS_MARKET_RELATIVE_VALUE")}
DO_NOT_RETRAIN_EVERYTHING_DAILY = (
    "a settlement model refitted daily chases noise; a calibration layer "
    "refitted monthly is stale. Cadence follows the quantity, not a schedule")


# --- Section 11. The registry. ---------------------------------------------

REGISTRY_FIELDS = (
    "MODEL_ID", "MODEL_FAMILY", "VERSION", "TRAINING_START", "TRAINING_END",
    "TRAINING_EVENT_IDS", "FEATURE_MANIFEST_SHA", "LABEL_MANIFEST_SHA",
    "HYPERPARAMETER_MANIFEST_SHA", "TRAINING_CODE_SHA", "RANDOM_SEED",
    "VALIDATION_PROTOCOL_SHA", "VALIDATION_RESULTS", "PROMOTION_STATUS",
    "DEPLOYED_AT", "RETIRED_AT", "ROLLBACK_PARENT",
)

PROMOTION_STATUSES = ("ACTIVE", "SHADOW", "DEPRECATED", "RETIRED",
                      "ROLLED_BACK", "CANDIDATE")

NO_PRODUCTION_WITHOUT_LINEAGE = (
    "a model whose training population, features, labels, code and seed "
    "cannot be named is a model nobody can reproduce, debug or roll back to")

NEVER_DELETE_LINEAGE = (
    "a retired or rolled-back model keeps its artifacts. Deleting the "
    "lineage of a failure deletes the evidence of why it failed")


def register(model_id, model_family, version, **kw):
    """One registry row. Production requires every lineage field."""
    if model_family not in MODEL_FAMILIES:
        return {"STATUS": "UNKNOWN_FAMILY", "GOT": model_family,
                "DECLARED": MODEL_FAMILIES}
    row = {f: NOT_IDENTIFIED for f in REGISTRY_FIELDS}
    row.update({"MODEL_ID": model_id, "MODEL_FAMILY": model_family,
                "VERSION": version, "PROMOTION_STATUS": "CANDIDATE"})
    for k, v in kw.items():
        if k in REGISTRY_FIELDS and v is not None:
            row[k] = v
    missing = [f for f in REGISTRY_FIELDS
               if f not in ("DEPLOYED_AT", "RETIRED_AT", "ROLLBACK_PARENT")
               and row.get(f) == NOT_IDENTIFIED]
    row["MISSING_LINEAGE"] = missing
    row["LINEAGE_COMPLETE"] = not missing
    row["MAY_ENTER_PRODUCTION"] = False
    row["WHY_NOT"] = ("AUTO_PRODUCTION_PROMOTION is DISABLED and no "
                      "deployment authorization exists")
    row["NO_PRODUCTION_WITHOUT_LINEAGE"] = NO_PRODUCTION_WITHOUT_LINEAGE
    return row


# --- Section 12. The immutable training manifest. --------------------------

MANIFEST_FIELDS = ("FEATURES", "TARGET", "EVENT_POPULATION", "TIME_RANGE",
                   "HYPERPARAMETERS", "SPLITS", "METRICS",
                   "PROMOTION_CRITERIA")

TRAINING_CODE_MAY_NOT_DECIDE_AFTER_THE_FACT = (
    "which metric matters, which subgroup counts and which threshold "
    "qualifies are decided BEFORE the run and hashed. Choosing them after "
    "seeing the test result is how a null becomes a discovery")


def training_manifest(features, target, event_population, time_range,
                      hyperparameters, splits, metrics, promotion_criteria):
    m = {
        "FEATURES": sorted(features or ()),
        "TARGET": target,
        "EVENT_POPULATION": sorted(str(e) for e in (event_population or ())),
        "TIME_RANGE": tuple(time_range or ()),
        "HYPERPARAMETERS": dict(hyperparameters or {}),
        "SPLITS": dict(splits or {}),
        "METRICS": tuple(metrics or ()),
        "PROMOTION_CRITERIA": tuple(promotion_criteria or ()),
    }
    missing = [f for f in MANIFEST_FIELDS if not m.get(f)]
    m["MISSING"] = missing
    m["FROZEN"] = not missing
    m["TRAINING_MANIFEST_SHA"] = hashlib.sha256(json.dumps(
        m, sort_keys=True, default=str).encode()).hexdigest()
    m["TRAINING_CODE_MAY_NOT_DECIDE_AFTER_THE_FACT"] = \
        TRAINING_CODE_MAY_NOT_DECIDE_AFTER_THE_FACT
    return m


def manifest_intact(manifest):
    got = hashlib.sha256(json.dumps(
        {k: v for k, v in manifest.items()
         if k not in ("TRAINING_MANIFEST_SHA",
                      "TRAINING_CODE_MAY_NOT_DECIDE_AFTER_THE_FACT")},
        sort_keys=True, default=str).encode()).hexdigest()
    rec = manifest.get("TRAINING_MANIFEST_SHA")
    return {"INTACT": got == rec, "RECORDED": rec, "RECOMPUTED": got,
            "REASON": None if got == rec else "MANIFEST_EDITED_AFTER_FREEZE"}


# --- Section 14. Prospective holdouts. -------------------------------------

HOLDOUT_WINDOWS = ("DEVELOPMENT", "CALIBRATION", "CHALLENGER_SELECTION",
                   "PROSPECTIVE_VALIDATION")

A_BURNED_HOLDOUT_IS_BURNED = (
    "once a model has been tuned against a window, that window can no longer "
    "estimate out-of-sample performance for it. A NEW future holdout is "
    "created; the burned one becomes reporting-only")


def holdout_state(burned=()):
    burned = set(burned or ())
    return {
        "WINDOWS": HOLDOUT_WINDOWS,
        "BURNED": sorted(burned),
        "AVAILABLE": [w for w in HOLDOUT_WINDOWS if w not in burned],
        "PROSPECTIVE_VALIDATION_AVAILABLE":
            "PROSPECTIVE_VALIDATION" not in burned,
        "A_BURNED_HOLDOUT_IS_BURNED": A_BURNED_HOLDOUT_IS_BURNED,
        "NEXT_ACTION": ("create a new future holdout"
                        if "PROSPECTIVE_VALIDATION" in burned
                        else "do not tune against PROSPECTIVE_VALIDATION"),
    }


# --- Sections 15, 16. The promotion gate. ----------------------------------

PROMOTION_CONDITIONS = (
    "DATA_QUALITY", "POINT_IN_TIME_INTEGRITY", "EVENT_SAFE_VALIDATION",
    "CHRONOLOGICAL_OOS", "SIMPLE_BASELINE_COMPARISON", "ECONOMIC_IMPROVEMENT",
    "UNCERTAINTY_REQUIREMENT", "REGIME_STABILITY", "NO_CRITICAL_DEGRADATION",
)

EXECUTION_EXTRA_CONDITION = "REAL_FILL_CALIBRATION"

TIE_IS_NOT_A_WIN = (
    "a challenger that matches the champion has not earned the cost of "
    "switching. A tie means the incumbent stays")

ACCURACY_CANNOT_PROMOTE = (
    "a price model does not promote on accuracy. It promotes on economic "
    "move relative to spread. An execution model promotes on "
    "EXPECTED_NET_DOLLARS and/or EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR. A "
    "classifier can be right about direction on moves smaller than the "
    "spread and earn nothing")


def promotion_gate(model_family, conditions_met=(), economic_improvement=None,
                   accuracy_improvement=None, is_execution_model=False,
                   authorized=False):
    """Every condition, plus economics, plus a human. Fails closed."""
    required = list(PROMOTION_CONDITIONS)
    if is_execution_model:
        required.append(EXECUTION_EXTRA_CONDITION)
    met = set(conditions_met or ())
    unmet = [c for c in required if c not in met]

    econ_ok = isinstance(economic_improvement, (int, float)) \
        and economic_improvement > 0
    notes = []
    if not econ_ok and isinstance(accuracy_improvement, (int, float)) \
            and accuracy_improvement > 0:
        notes.append("accuracy improved but economics did not; "
                     "ACCURACY_CANNOT_PROMOTE")
    if isinstance(economic_improvement, (int, float)) \
            and economic_improvement == 0:
        notes.append("economic improvement is exactly zero; TIE_IS_NOT_A_WIN")

    gates_pass = not unmet and econ_ok
    return {
        "MODEL_FAMILY": model_family,
        "REQUIRED_CONDITIONS": tuple(required),
        "CONDITIONS_NOT_MET": unmet,
        "ECONOMIC_IMPROVEMENT": (economic_improvement
                                 if economic_improvement is not None
                                 else NOT_IDENTIFIED),
        "ECONOMIC_IMPROVEMENT_POSITIVE": econ_ok,
        "AUTOMATED_GATES_PASS": gates_pass,
        "EXPLICIT_AUTHORIZATION": bool(authorized),
        "MAY_PROMOTE": bool(gates_pass and authorized),
        "AUTO_PRODUCTION_PROMOTION": AUTO_PRODUCTION_PROMOTION,
        "NOTES": notes,
        "TIE_IS_NOT_A_WIN": TIE_IS_NOT_A_WIN,
        "ACCURACY_CANNOT_PROMOTE": ACCURACY_CANNOT_PROMOTE,
        "WHY_NOT": (None if gates_pass and authorized else
                    ("%d gate(s) unmet" % len(unmet) if unmet else
                     ("economics did not improve" if not econ_ok else
                      "automated gates pass but no human authorized it"))),
    }


# --- Section 18. Retraining triggers. --------------------------------------

RETRAIN_TRIGGERS = ("NEW_INDEPENDENT_EVENTS", "NEW_EVENT_HOURS",
                    "LABEL_MATURITY", "DRIFT_DETECTED",
                    "LIVE_CALIBRATION_DEGRADES",
                    "OPPORTUNITY_REGIME_CHANGED", "EXTERNAL_SOURCE_CHANGED")

RETRAIN_TRIGGER_THRESHOLDS = "NOT_IDENTIFIED_PENDING_PROSPECTIVE_DATA"

WHY_NO_THRESHOLDS_YET = (
    "a numeric retraining threshold chosen before any prospective data exists "
    "is a guess that would then govern the system. The trigger NAMES are "
    "frozen; their thresholds wait for evidence")


def retrain_check(observed=None):
    observed = observed or {}
    return {
        "TRIGGERS": RETRAIN_TRIGGERS,
        "RETRAIN_TRIGGER_THRESHOLDS": RETRAIN_TRIGGER_THRESHOLDS,
        "OBSERVED": dict(observed),
        "ANY_TRIGGER_FIRED": NOT_IDENTIFIED,
        "WHY": WHY_NO_THRESHOLDS_YET,
    }


# --- Sections 33, 34. Lifecycle and rollback. ------------------------------

ROLLBACK_SLOTS = ("CURRENT_CHAMPION", "PREVIOUS_CHAMPION", "LAST_KNOWN_GOOD")


def rollback_state(current=None, previous=None, last_known_good=None,
                   degradation_detected=False, authorized=False):
    have = {"CURRENT_CHAMPION": current or NOT_IDENTIFIED,
            "PREVIOUS_CHAMPION": previous or NOT_IDENTIFIED,
            "LAST_KNOWN_GOOD": last_known_good or NOT_IDENTIFIED}
    can = last_known_good is not None
    return {
        "SLOTS": have,
        "ROLLBACK_TARGET_AVAILABLE": can,
        "DEGRADATION_DETECTED": bool(degradation_detected),
        "RECOMMENDS_ROLLBACK": bool(degradation_detected and can),
        "MAY_EXECUTE_ROLLBACK": bool(degradation_detected and can
                                     and authorized),
        "NEVER_DELETE_LINEAGE": NEVER_DELETE_LINEAGE,
        "WHY_NOT": (None if (degradation_detected and can and authorized)
                    else "machinery only; no live deployment authorization"),
    }


# --- Section 35. Kill switches. --------------------------------------------

KILL_CONDITIONS = ("FEED_STALE", "MODEL_DRIFT", "MODEL_OUTPUT_INVALID",
                   "P_FILL_CALIBRATION_FAILURE", "MARKOUT_DEGRADATION",
                   "LOSS_LIMIT", "EXPOSURE_LIMIT",
                   "VENUE_RECONCILIATION_FAILURE", "LATENCY_SPIKE",
                   "SCHEMA_DRIFT", "OOD_MARKET_STATE")

FAIL_CLOSED = (
    "a kill condition whose state cannot be READ counts as TRIPPED. An "
    "unreadable safety check is not a passing one")


def kill_switch(states=None):
    """Any tripped OR unreadable condition halts. Fails closed."""
    states = states or {}
    tripped, unreadable = [], []
    for c in KILL_CONDITIONS:
        v = states.get(c, NOT_IDENTIFIED)
        if v == NOT_IDENTIFIED or v is None:
            unreadable.append(c)
        elif bool(v):
            tripped.append(c)
    halt = bool(tripped or unreadable)
    return {
        "CONDITIONS": KILL_CONDITIONS,
        "TRIPPED": tripped,
        "UNREADABLE": unreadable,
        "HALT": halt,
        "FAIL_CLOSED": FAIL_CLOSED,
        "TRADING_PERMITTED": False,
        "WHY_TRADING_NEVER_PERMITTED_HERE": (
            "mirror_live=false and no order path exists, independently of "
            "these switches"),
    }


# --- Section 43. The safe learning hierarchy. ------------------------------

LEVELS = ("L0_OFFLINE_RESEARCH", "L1_SHADOW", "L2_PAPER_REPLAY",
          "L3_MICRO_LIVE_RESEARCH", "L4_LIMITED_PRODUCTION",
          "L5_SCALED_PRODUCTION")

CURRENT_LEVEL = "L0_OFFLINE_RESEARCH"

NO_LEVEL_SKIPPING = (
    "the system may never jump a level because a backtest is attractive. An "
    "attractive backtest is the most common reason a system skips a level, "
    "which is exactly why it is not a reason")


def level_gate(current, target, gates_passed=(), authorized=False):
    if current not in LEVELS or target not in LEVELS:
        return {"STATUS": "UNKNOWN_LEVEL", "DECLARED": LEVELS}
    ci, ti = LEVELS.index(current), LEVELS.index(target)
    if ti - ci > 1:
        return {"MAY_PROMOTE": False, "REASON": "LEVEL_SKIP_REFUSED",
                "FROM": current, "TO": target,
                "NO_LEVEL_SKIPPING": NO_LEVEL_SKIPPING}
    return {"MAY_PROMOTE": bool(gates_passed and authorized and ti == ci + 1),
            "FROM": current, "TO": target,
            "GATES_PASSED": tuple(gates_passed or ()),
            "EXPLICIT_AUTHORIZATION": bool(authorized),
            "CURRENT_LEVEL": CURRENT_LEVEL,
            "NO_LEVEL_SKIPPING": NO_LEVEL_SKIPPING}


# --- Section 48. Never train on failed capture data. -----------------------

FAILED_CAPTURE_USE = "CAPTURE_DEBUGGING_ONLY"
NEVER_TRAIN_ON_FAILED_CAPTURE = (
    "if the capture fails its frozen quality gate, its rows may debug the "
    "capture and nothing else. They may not enter training, selection or any "
    "edge claim -- a model fitted to a broken measurement learns the breakage")


def capture_data_use(quality_gate_verdict):
    ok = quality_gate_verdict == "PASS"
    return {"QUALITY_GATE": quality_gate_verdict,
            "MAY_TRAIN": ok, "MAY_SELECT_MODELS": ok,
            "MAY_SUPPORT_EDGE_CLAIMS": ok,
            "PERMITTED_USE": ("FULL_RESEARCH" if ok else FAILED_CAPTURE_USE),
            "NEVER_TRAIN_ON_FAILED_CAPTURE": NEVER_TRAIN_ON_FAILED_CAPTURE}


# --- Sections 26, 27, 28. Bandit, propensities, OPE. -----------------------

CONTEXTUAL_BANDIT_STATUS = "BLOCKED_NO_LIVE_AUTHORIZATION"
BANDIT_REQUIRED_BOUNDS = ("EXPLORATION_BUDGET", "MAX_ORDER_SIZE",
                          "MAX_EVENT_EXPOSURE", "MAX_DAILY_RESEARCH_LOSS",
                          "ELIGIBLE_MARKETS", "KILL_SWITCH")

OFF_POLICY_EVALUATION_STATUS = \
    "BLOCKED_UNTIL_VALID_PROPENSITIES_AND_OUTCOME_DATA"
OPE_METHODS = ("DIRECT_METHOD", "IPS", "SNIPS", "DOUBLY_ROBUST")

WITHOUT_PROPENSITIES_NO_OPE = (
    "IPS, SNIPS and doubly-robust estimators all divide by the probability "
    "the behaviour policy would have taken the action. A deterministic policy "
    "has propensity 1 for what it did and 0 for everything else, so the "
    "estimator is undefined off-support. Observational trade history does NOT "
    "identify counterfactual policy value")


def ope(method, propensities_logged=False, outcomes=False):
    if method not in OPE_METHODS:
        return {"STATUS": "UNKNOWN_METHOD", "DECLARED": OPE_METHODS}
    ready = bool(propensities_logged and outcomes)
    return {"METHOD": method,
            "OFF_POLICY_EVALUATION_STATUS": (
                "READY" if ready else OFF_POLICY_EVALUATION_STATUS),
            "PROPENSITIES_LOGGED": bool(propensities_logged),
            "OUTCOMES_AVAILABLE": bool(outcomes),
            "MAY_ESTIMATE": ready,
            "WITHOUT_PROPENSITIES_NO_OPE": WITHOUT_PROPENSITIES_NO_OPE}


def describe():
    return {
        "MODEL_FAMILIES": MODEL_FAMILIES,
        "NOT_ONE_MONOLITH": NOT_ONE_MONOLITH,
        "CADENCES": {k: v for k, v in CADENCES.items()},
        "DO_NOT_RETRAIN_EVERYTHING_DAILY": DO_NOT_RETRAIN_EVERYTHING_DAILY,
        "REGISTRY_FIELDS": REGISTRY_FIELDS,
        "PROMOTION_STATUSES": PROMOTION_STATUSES,
        "NO_PRODUCTION_WITHOUT_LINEAGE": NO_PRODUCTION_WITHOUT_LINEAGE,
        "NEVER_DELETE_LINEAGE": NEVER_DELETE_LINEAGE,
        "MANIFEST_FIELDS": MANIFEST_FIELDS,
        "TRAINING_CODE_MAY_NOT_DECIDE_AFTER_THE_FACT":
            TRAINING_CODE_MAY_NOT_DECIDE_AFTER_THE_FACT,
        "HOLDOUT_WINDOWS": HOLDOUT_WINDOWS,
        "A_BURNED_HOLDOUT_IS_BURNED": A_BURNED_HOLDOUT_IS_BURNED,
        "PROMOTION_CONDITIONS": PROMOTION_CONDITIONS,
        "TIE_IS_NOT_A_WIN": TIE_IS_NOT_A_WIN,
        "ACCURACY_CANNOT_PROMOTE": ACCURACY_CANNOT_PROMOTE,
        "RETRAIN_TRIGGERS": RETRAIN_TRIGGERS,
        "RETRAIN_TRIGGER_THRESHOLDS": RETRAIN_TRIGGER_THRESHOLDS,
        "ROLLBACK_SLOTS": ROLLBACK_SLOTS,
        "KILL_CONDITIONS": KILL_CONDITIONS,
        "FAIL_CLOSED": FAIL_CLOSED,
        "LEVELS": LEVELS,
        "CURRENT_LEVEL": CURRENT_LEVEL,
        "NO_LEVEL_SKIPPING": NO_LEVEL_SKIPPING,
        "NEVER_TRAIN_ON_FAILED_CAPTURE": NEVER_TRAIN_ON_FAILED_CAPTURE,
        "CONTEXTUAL_BANDIT_STATUS": CONTEXTUAL_BANDIT_STATUS,
        "BANDIT_REQUIRED_BOUNDS": BANDIT_REQUIRED_BOUNDS,
        "OFF_POLICY_EVALUATION_STATUS": OFF_POLICY_EVALUATION_STATUS,
        "WITHOUT_PROPENSITIES_NO_OPE": WITHOUT_PROPENSITIES_NO_OPE,
        "AUTO_PRODUCTION_PROMOTION": AUTO_PRODUCTION_PROMOTION,
        "NO_ONLINE_WEIGHT_MUTATION": NO_ONLINE_WEIGHT_MUTATION,
        "WHY_NO_ONLINE_MUTATION": WHY_NO_ONLINE_MUTATION,
        "ONLINE_BAYES_OFFLINE_PROMOTION": ONLINE_BAYES_OFFLINE_PROMOTION,
        "NOTHING_IS_TRAINED_HERE": NOTHING_IS_TRAINED_HERE,
    }
