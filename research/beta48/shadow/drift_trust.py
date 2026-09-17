"""Drift, model trust, uncertainty, OOD, calibration, posterior predictive.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IS TRAINED HERE.

DRIFT REDUCES TRUST, IT DOES NOT TRIGGER A DEPLOY
-------------------------------------------------
The tempting response to drift is "retrain and ship". That replaces a model
whose failure is understood with one nobody has validated, at exactly the
moment the environment is least stable. Drift lowers trust, widens the
uncertainty buffer, shrinks eligible size, or moves a model to shadow. A
challenger retrain may START; it still passes every gate before it serves.

EPISTEMIC VS ALEATORIC
----------------------
Market randomness (aleatoric) does not shrink with more data. Ignorance
(epistemic) does. Separating them says whether more data would actually help
-- and whether an experiment is worth buying.
"""

import math

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOTHING_IS_TRAINED_HERE = True


# --- Section 20. Drift monitors. -------------------------------------------

DRIFT_MONITORS = (
    "FEATURE_DRIFT", "PRICE_REGIME_DRIFT", "SPREAD_DRIFT", "DEPTH_DRIFT",
    "FLOW_DRIFT", "PREDICTION_ERROR_DRIFT", "CALIBRATION_DRIFT",
    "FILL_RATE_DRIFT", "MARKOUT_DRIFT", "TOXICITY_DRIFT",
    "OPPORTUNITY_RATE_DRIFT", "CAPITAL_OCCUPANCY_DRIFT",
    "EXTERNAL_LEAD_LAG_DRIFT",
)

DRIFT_KINDS = ("SUDDEN", "GRADUAL", "NONE")

SUDDEN_AND_GRADUAL_ARE_DIFFERENT = (
    "a sudden shift is usually a venue or feed change; a gradual one is "
    "usually the market adapting or an edge decaying. Detecting only one "
    "misses half the failures, and they call for different responses")


def _mean_sd(xs):
    xs = [float(x) for x in xs if x is not None]
    if len(xs) < 2:
        return (xs[0] if xs else None), None
    m = sum(xs) / len(xs)
    return m, math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def drift(reference, recent, monitor="FEATURE_DRIFT", z_sudden=3.0,
          gradual_sd=1.5):
    """Compare a recent window against a reference. Reports BOTH kinds."""
    if monitor not in DRIFT_MONITORS:
        return {"STATUS": "UNKNOWN_MONITOR", "DECLARED": DRIFT_MONITORS}
    rm, rs = _mean_sd(reference)
    cm, cs = _mean_sd(recent)
    if rm is None or cm is None or not rs:
        # A reference window with zero spread cannot say what a surprising
        # value looks like, so no z-score exists. Reported, never assumed calm.
        return {"MONITOR": monitor, "DRIFT_KIND": NOT_IDENTIFIED,
                "DRIFT_DETECTED": NOT_IDENTIFIED,
                "STATUS": "INSUFFICIENT_DATA",
                "WHY_NOT_CALM": ("an unmeasurable drift check is not a "
                                 "passing one"),
                "REFERENCE_N": len(reference or ()),
                "RECENT_N": len(recent or ()),
                "WHY": "both windows need enough points to have a spread"}
    z = (cm - rm) / rs
    # Gradual: fit a slope across the recent window, scaled by reference sd.
    xs = [float(v) for v in recent if v is not None]
    n = len(xs)
    slope = 0.0
    if n >= 3:
        mx = (n - 1) / 2.0
        my = sum(xs) / n
        den = sum((i - mx) ** 2 for i in range(n))
        if den:
            slope = sum((i - mx) * (xs[i] - my) for i in range(n)) / den
    # TOTAL drift across the window, expressed in reference standard
    # deviations. An earlier version compared slope*n/sd against a threshold
    # that itself scaled with n, which double-counted the window length and
    # flagged ordinary noise as gradual drift.
    total_change_sd = (slope * n / rs) if rs else 0.0

    kind = "NONE"
    if abs(z) >= z_sudden:
        kind = "SUDDEN"
    elif abs(total_change_sd) >= gradual_sd:
        kind = "GRADUAL"
    return {
        "MONITOR": monitor,
        "REFERENCE_MEAN": round(rm, 10), "RECENT_MEAN": round(cm, 10),
        "Z_SCORE": round(z, 6),
        "TOTAL_CHANGE_IN_REFERENCE_SD": round(total_change_sd, 6),
        "GRADUAL_THRESHOLD_SD": gradual_sd,
        "DRIFT_KIND": kind,
        "DRIFT_DETECTED": kind != "NONE",
        "SUDDEN_AND_GRADUAL_ARE_DIFFERENT": SUDDEN_AND_GRADUAL_ARE_DIFFERENT,
    }


# --- Section 21. What drift is allowed to do. ------------------------------

DRIFT_RESPONSES = ("LOWER_MODEL_TRUST", "WIDEN_UNCERTAINTY_BUFFER",
                   "REDUCE_ELIGIBLE_SIZE", "MOVE_MODEL_TO_SHADOW",
                   "TRIGGER_CHALLENGER_RETRAIN", "ROLL_BACK", "NO_TRADE")

DRIFT_NEVER_AUTO_DEPLOYS = (
    "drift must NOT trigger retrain-and-ship. That swaps a model whose "
    "failure is understood for one nobody validated, exactly when the "
    "environment is least stable. A retrain may start; it still passes every "
    "gate before it serves")


def drift_response(drift_rows):
    fired = [d for d in drift_rows or () if d.get("DRIFT_DETECTED")]
    if not fired:
        return {"DRIFT_DETECTED": False, "RESPONSES": [],
                "AUTO_DEPLOY": False}
    resp = ["LOWER_MODEL_TRUST", "WIDEN_UNCERTAINTY_BUFFER"]
    if any(d.get("DRIFT_KIND") == "SUDDEN" for d in fired):
        resp += ["MOVE_MODEL_TO_SHADOW", "NO_TRADE"]
    else:
        resp += ["REDUCE_ELIGIBLE_SIZE", "TRIGGER_CHALLENGER_RETRAIN"]
    return {
        "DRIFT_DETECTED": True,
        "MONITORS_FIRED": [d["MONITOR"] for d in fired],
        "RESPONSES": resp,
        "AUTO_DEPLOY": False,
        "DRIFT_NEVER_AUTO_DEPLOYS": DRIFT_NEVER_AUTO_DEPLOYS,
        "AVAILABLE_RESPONSES": DRIFT_RESPONSES,
    }


# --- Section 22. Model trust. ----------------------------------------------

TRUST_INPUTS = ("RECENT_CALIBRATION", "RECENT_OOS_ERROR", "REGIME_SUPPORT",
                "TRAINING_SIMILARITY", "DRIFT", "SAMPLE_SIZE",
                "UNCERTAINTY", "TIME_SINCE_RETRAINING")

TRUST_WEIGHTS_NOT_MANUFACTURED = (
    "the inputs are declared; the numeric weights are not invented here. A "
    "trust score with made-up weights is a made-up number that then scales "
    "real capital")


def model_trust(inputs=None):
    inputs = inputs or {}
    present = [k for k in TRUST_INPUTS if inputs.get(k) is not None]
    missing = [k for k in TRUST_INPUTS if k not in present]
    return {
        "TRUST_INPUTS": TRUST_INPUTS,
        "SUPPLIED": present, "MISSING": missing,
        "MODEL_TRUST_SCORE": NOT_IDENTIFIED,
        "WHY": ("weights require prospective evidence about which inputs "
                "actually predict a model going wrong on THIS venue"),
        "TRUST_WEIGHTS_NOT_MANUFACTURED": TRUST_WEIGHTS_NOT_MANUFACTURED,
        "EFFECT_WHEN_LOW": "contributes less to ensemble EV",
    }


# --- Section 11/23. Uncertainty. -------------------------------------------

UNCERTAINTY_FIELDS = ("POINT_ESTIMATE", "UNCERTAINTY_INTERVAL",
                      "MODEL_DISAGREEMENT", "OUT_OF_DISTRIBUTION_SCORE",
                      "TRAINING_SUPPORT", "REGIME_SUPPORT")

ALEATORIC_VS_EPISTEMIC = (
    "aleatoric uncertainty is market randomness and does NOT shrink with more "
    "data. Epistemic uncertainty is BETTOR not knowing enough and does. "
    "Separating them says whether an experiment would help at all")

SIZING_NEVER_COMPENSATES = (
    "sizing never compensates for unidentified EV. A larger position on an "
    "unknown expectation is a larger unknown, not a better trade")


def uncertainty(point=None, interval=None, model_disagreement=None,
                ood_score=None, training_support=None, regime_support=None,
                aleatoric=None, epistemic=None):
    out = {
        "POINT_ESTIMATE": point if point is not None else NOT_IDENTIFIED,
        "UNCERTAINTY_INTERVAL": interval or NOT_IDENTIFIED,
        "MODEL_DISAGREEMENT": (model_disagreement
                               if model_disagreement is not None
                               else NOT_IDENTIFIED),
        "OUT_OF_DISTRIBUTION_SCORE": (ood_score if ood_score is not None
                                      else NOT_IDENTIFIED),
        "TRAINING_SUPPORT": training_support or NOT_IDENTIFIED,
        "REGIME_SUPPORT": regime_support or NOT_IDENTIFIED,
        "ALEATORIC_UNCERTAINTY": (aleatoric if aleatoric is not None
                                  else NOT_IDENTIFIED),
        "EPISTEMIC_UNCERTAINTY": (epistemic if epistemic is not None
                                  else NOT_IDENTIFIED),
        "ALEATORIC_VS_EPISTEMIC": ALEATORIC_VS_EPISTEMIC,
        "SIZING_NEVER_COMPENSATES": SIZING_NEVER_COMPENSATES,
    }
    if isinstance(epistemic, (int, float)) and isinstance(aleatoric,
                                                          (int, float)):
        tot = epistemic + aleatoric
        out["EPISTEMIC_SHARE"] = (round(epistemic / tot, 6) if tot > 0
                                  else NOT_IDENTIFIED)
        out["MORE_DATA_WOULD_HELP"] = (tot > 0 and epistemic / tot > 0.5)
    return out


# --- Section 24. The OOD gate. ---------------------------------------------

OOD_STATUSES = ("IN_DISTRIBUTION", "WEAK_SUPPORT", "OUT_OF_DISTRIBUTION")

OOD_EXAMPLES = ("NEW_LEAGUE", "NEW_MARKET_FAMILY", "UNSEEN_SPREAD_REGIME",
                "NEW_TICK_BEHAVIOUR", "EXTREME_VOLATILITY", "FEED_ANOMALY")


def ood(state_key=None, training_keys=(), support_n=0, weak_below=30):
    """Fails closed: an unseen key is OUT_OF_DISTRIBUTION, not merely new."""
    keys = set(training_keys or ())
    if state_key is None:
        return {"OOD_STATUS": "OUT_OF_DISTRIBUTION",
                "WHY": "no state key supplied",
                "FORCES": ("SHADOW_ONLY", "NO_TRADE")}
    if state_key not in keys:
        return {"OOD_STATUS": "OUT_OF_DISTRIBUTION", "STATE_KEY": state_key,
                "WHY": "this state was never seen in training",
                "EXAMPLES": OOD_EXAMPLES,
                "FORCES": ("SHADOW_ONLY", "NO_TRADE")}
    if support_n < weak_below:
        return {"OOD_STATUS": "WEAK_SUPPORT", "STATE_KEY": state_key,
                "SUPPORT_N": support_n, "WEAK_BELOW": weak_below,
                "FORCES": ("WIDEN_UNCERTAINTY_BUFFER",)}
    return {"OOD_STATUS": "IN_DISTRIBUTION", "STATE_KEY": state_key,
            "SUPPORT_N": support_n, "FORCES": ()}


# --- Sections 44, 45. Posterior predictive checks and calibration. ---------

POSTERIOR_PREDICTIVE_TARGETS = ("FILLS", "FILL_LATENCY", "MARKOUTS",
                                "PRICE_MOVES", "QUEUE_DEPLETION",
                                "CAPITAL_OCCUPANCY")

A_90_INTERVAL_SHOULD_CONTAIN_90_PERCENT = (
    "a 90% interval that contains reality 60% of the time is not cautious, it "
    "is wrong -- and every EV built on it is overconfident")


def interval_coverage(predicted_intervals, observed, level=0.90):
    """Do the model's intervals contain reality as often as they claim?"""
    pairs = [(iv, o) for iv, o in zip(predicted_intervals or (),
                                      observed or ())
             if iv and o is not None]
    if not pairs:
        return {"STATUS": "NO_DATA", "EMPIRICAL_COVERAGE": NOT_IDENTIFIED,
                "NOMINAL": level,
                "A_90_INTERVAL_SHOULD_CONTAIN_90_PERCENT":
                    A_90_INTERVAL_SHOULD_CONTAIN_90_PERCENT}
    inside = sum(1 for (lo, hi), o in pairs if lo <= float(o) <= hi)
    cov = inside / len(pairs)
    return {
        "STATUS": "MEASURED",
        "NOMINAL_LEVEL": level,
        "EMPIRICAL_COVERAGE": round(cov, 6),
        "N": len(pairs),
        "MISCALIBRATION": round(cov - level, 6),
        "OVERCONFIDENT": cov < level,
        "A_90_INTERVAL_SHOULD_CONTAIN_90_PERCENT":
            A_90_INTERVAL_SHOULD_CONTAIN_90_PERCENT,
        "EFFECT": ("lower trust" if cov < level else "within or above nominal"),
    }


def brier(predicted, outcomes):
    pairs = [(float(p), float(o)) for p, o in zip(predicted or (),
                                                  outcomes or ())
             if p is not None and o is not None]
    if not pairs:
        return {"BRIER": NOT_IDENTIFIED, "N": 0}
    return {"BRIER": round(sum((p - o) ** 2 for p, o in pairs) / len(pairs),
                           10),
            "N": len(pairs)}


def posterior_predictive(target, predicted_mean=None, observed_mean=None,
                         predicted_sd=None, n=0):
    """Does the posterior's prediction match what actually happened?"""
    if target not in POSTERIOR_PREDICTIVE_TARGETS:
        return {"STATUS": "UNKNOWN_TARGET",
                "DECLARED": POSTERIOR_PREDICTIVE_TARGETS}
    if predicted_mean is None or observed_mean is None or not predicted_sd:
        return {"TARGET": target, "STATUS": "NOT_MEASURED",
                "PRIOR_TO_POSTERIOR_SHIFT": NOT_IDENTIFIED,
                "WHY": "no BETTOR-native observations of this target yet"}
    z = (observed_mean - predicted_mean) / predicted_sd
    return {"TARGET": target, "STATUS": "MEASURED",
            "PREDICTED_MEAN": predicted_mean, "OBSERVED_MEAN": observed_mean,
            "STANDARDISED_MISS": round(z, 6), "N": n,
            "SYSTEMATIC_MISS": abs(z) > 2.0,
            "EFFECT": ("lower trust in this model" if abs(z) > 2.0
                       else "consistent with the posterior")}


# --- Section 46. Self-correcting priors. -----------------------------------

PRIOR_SHIFT_IS_PROPRIETARY_KNOWLEDGE = (
    "a transferred prior that is repeatedly wrong on this venue is itself a "
    "finding. Large, persistent prior-to-posterior shifts mark exactly where "
    "public and comparable-market assumptions fail HERE, and nobody else has "
    "that map")


def prior_shift_log(parameter, shifts=()):
    xs = [float(s) for s in shifts or () if s is not None]
    if not xs:
        return {"PARAMETER": parameter, "SHIFTS": 0,
                "PRIOR_TO_POSTERIOR_SHIFT": NOT_IDENTIFIED,
                "PRIOR_SHIFT_IS_PROPRIETARY_KNOWLEDGE":
                    PRIOR_SHIFT_IS_PROPRIETARY_KNOWLEDGE}
    mean = sum(xs) / len(xs)
    same_sign = all(x > 0 for x in xs) or all(x < 0 for x in xs)
    return {"PARAMETER": parameter, "SHIFTS": len(xs),
            "MEAN_SHIFT": round(mean, 10),
            "PERSISTENTLY_ONE_DIRECTION": same_sign and len(xs) >= 3,
            "TRANSFERRED_PRIOR_LIKELY_WRONG_HERE": same_sign and len(xs) >= 3,
            "PRIOR_SHIFT_IS_PROPRIETARY_KNOWLEDGE":
                PRIOR_SHIFT_IS_PROPRIETARY_KNOWLEDGE}


def describe():
    return {
        "DRIFT_MONITORS": DRIFT_MONITORS,
        "DRIFT_KINDS": DRIFT_KINDS,
        "SUDDEN_AND_GRADUAL_ARE_DIFFERENT": SUDDEN_AND_GRADUAL_ARE_DIFFERENT,
        "DRIFT_RESPONSES": DRIFT_RESPONSES,
        "DRIFT_NEVER_AUTO_DEPLOYS": DRIFT_NEVER_AUTO_DEPLOYS,
        "TRUST_INPUTS": TRUST_INPUTS,
        "TRUST_WEIGHTS_NOT_MANUFACTURED": TRUST_WEIGHTS_NOT_MANUFACTURED,
        "UNCERTAINTY_FIELDS": UNCERTAINTY_FIELDS,
        "ALEATORIC_VS_EPISTEMIC": ALEATORIC_VS_EPISTEMIC,
        "SIZING_NEVER_COMPENSATES": SIZING_NEVER_COMPENSATES,
        "OOD_STATUSES": OOD_STATUSES,
        "OOD_EXAMPLES": OOD_EXAMPLES,
        "POSTERIOR_PREDICTIVE_TARGETS": POSTERIOR_PREDICTIVE_TARGETS,
        "A_90_INTERVAL_SHOULD_CONTAIN_90_PERCENT":
            A_90_INTERVAL_SHOULD_CONTAIN_90_PERCENT,
        "PRIOR_SHIFT_IS_PROPRIETARY_KNOWLEDGE":
            PRIOR_SHIFT_IS_PROPRIETARY_KNOWLEDGE,
        "NOTHING_IS_TRAINED_HERE": NOTHING_IS_TRAINED_HERE,
    }
