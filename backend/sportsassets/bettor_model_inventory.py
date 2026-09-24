"""WHAT SCORERS EXIST, AND THE THREE QUESTIONS KEPT APART.

The directive names three questions that a single "does the model work?"
collapses into mush. They have different answers here, and two of the
three are YES:

  1. DOES AN APPROPRIATE SCORER EXIST?
     For the entry gate's target -- settlement -- NO. Two targets are
     fitted and evaluated in this repository and neither is settlement:

        T_COMPLETE  will cohort account A be OBSERVED making a
                    complementary BUY within H?          (behaviour)
        T_CLEARS    ...and at a pair price strictly below 1.00?
                                                          (economics)

     T_CLEARS is an economic target and the closer of the two, but it
     predicts what A does next, not what the event settles at. No
     amount of retraining turns a next-action forecast into a
     settlement forecast: it is the label that is wrong, not the fit.

  2. DO ITS LIVE INPUTS REACH THE PRODUCTION DECISION FUNCTION?
     YES, and this is the part I expected to fail. All 14 shared
     features -- and Ferrari's 3 extra -- are functions of (a) the
     cohort fill row itself and (b) that market's earlier fills. The
     prospective lane already holds both at decision time: it selects
     on `trades` and the runner reads the same market's history to
     classify the entry. `feature_availability()` below computes the
     vector from a production-shaped row and reports which features
     resolved, so this is demonstrated rather than asserted.

  3. DOES ITS EVIDENCE QUALIFY IT FOR USE?
     Unanswerable for entry while (1) is NO -- the gate refuses on
     TARGET before evidence is consulted, which is the correct order.
     For its OWN target it is answerable and `learn/metrics.py` is how,
     with a base rate beside every score.

WHY THE LEDGER BELOW EXISTS. A model evaluated only on history it was
fitted near is a model evaluated by its author. The ledger records a
prediction, its model version, its training-data identity and its
feature availability BEFORE the outcome is known, so the join that
follows is a test rather than a description. That is the difference
between this and the policy comparator, which fits nothing.

NOTHING HERE FEEDS THE ENTRY GATE. `bettor_entry_gate.admit` still
requires a settlement-target model and still refuses; a test asserts
that wiring a prediction into the ledger does not change that.
"""

from __future__ import annotations

import hashlib
import json

# ── the targets, named explicitly ───────────────────────────────────

T_COMPLETE = "COHORT_COMPLEMENTARY_FILL_WITHIN_H"
T_CLEARS = "COHORT_COMPLEMENTARY_FILL_BELOW_PARITY_WITHIN_H"
T_SETTLEMENT = "EVENT_SETTLEMENT_PROBABILITY"

#: What each target predicts, in one line, and what it cannot be used for.
TARGETS = {
    T_COMPLETE: {
        "predicts": ("whether the cohort account makes a complementary "
                     "BUY on the same condition within the horizon"),
        "kind": "BEHAVIOURAL",
        "fitted": True,
        "module": "sportsassets.learn.dataset",
        "not_usable_for": ("entry valuation: it forecasts somebody else's "
                           "next action, not what the event settles at"),
    },
    T_CLEARS: {
        "predicts": ("the same complementary BUY, and that the pair price "
                     "(entry + completion) is strictly below 1.00"),
        "kind": "ECONOMIC_CONDITIONAL_ON_BEHAVIOUR",
        "fitted": True,
        "module": "sportsassets.learn.ferrari",
        "not_usable_for": ("entry valuation: still conditioned on the "
                           "cohort acting, and parity is not settlement"),
    },
    T_SETTLEMENT: {
        "predicts": "the probability the outcome settles at 1.0",
        "kind": "SETTLEMENT",
        "fitted": False,
        "module": None,
        "not_usable_for": None,
    },
}

#: The one the entry gate requires. Kept here so the mismatch is a lookup
#: rather than a memory.
ENTRY_REQUIRES = T_SETTLEMENT

FITTED_TARGETS = tuple(t for t, d in TARGETS.items() if d["fitted"])

#: The estimators that exist and can actually be fitted, from
#: `learn/kernel.py`. Listed because "no learning exists" was wrong and
#: the correction matters: there IS a fitting kernel, in the standard
#: library, with four estimators and a base-rate control.
ESTIMATORS = ("Ridge", "Isotonic", "Stumps", "Hazard", "BaseRate")


def qualifies_for_entry(target: str) -> dict:
    """The gate's question, answered on TARGET alone and before evidence.

    Order matters. Consulting a model's metrics first and its label
    second is how a well-calibrated forecast of the wrong quantity gets
    promoted: the numbers look excellent, because they are excellent, for
    a question nobody asked.
    """
    spec = TARGETS.get(target)
    if spec is None:
        return {"ok": False, "refusal": "UNKNOWN_TARGET", "target": target,
                "why": "target %r is not declared" % (target,)}
    if target != ENTRY_REQUIRES:
        return {"ok": False, "refusal": "R_MODEL_TARGET_MISMATCH",
                "target": target, "required": ENTRY_REQUIRES,
                "why": ("%s predicts %s. %s" % (target, spec["predicts"],
                                                spec["not_usable_for"]))}
    if not spec["fitted"]:
        return {"ok": False, "refusal": "R_NO_QUALIFIED_MODEL",
                "target": target, "required": ENTRY_REQUIRES,
                "why": ("the entry target %s has no fitted model in this "
                        "repository. The engine is connected and the "
                        "scorer is absent -- a different status from an "
                        "unimplemented entry path" % (target,))}
    return {"ok": True, "refusal": None, "target": target}


# ── (2) feature availability, computed rather than asserted ─────────

def feature_availability(entry_row, prior_fills=(), *, target=T_COMPLETE):
    """Build the decision-time vector for ONE production-shaped row.

    `entry_row` is a `trades` row as the prospective lane holds it:
    account, condition_id, outcome_index, side, price, size, ts,
    detected_at, source. `prior_fills` are that market's earlier fills.

    Returns which features resolved and which did not, so "the inputs
    reach the decision function" is a measurement with a denominator.
    """
    from .learn import dataset as D
    from .learn import ferrari as F

    names = F.FEATURES if target == T_CLEARS else D.FEATURES
    fills = list(prior_fills) + [dict(entry_row)]
    end = max(D.available_at(f) for f in fills) + 1.0
    ds = D.build(fills, horizon_s=3600.0, observation_end=end)
    rows = ds.get("rows") or []
    # The entry we asked about is the last one built from our row.
    mine = None
    for r in rows:
        if r.get("condition_id") == entry_row.get("condition_id"):
            mine = r
    feats = (mine or {}).get("features") or {}
    present = [n for n in names if feats.get(n) is not None]
    missing = [n for n in names if feats.get(n) is None]
    return {
        "target": target,
        "feature_count": len(names),
        "present": present,
        "missing": missing,
        "all_present": not missing and bool(present),
        "row_built": mine is not None,
        "features": {n: feats.get(n) for n in names},
        "source_of_inputs": ("the cohort fill row and that market's "
                             "earlier fills -- both already in hand at "
                             "decision time in the prospective lane"),
    }


# ── the prospective prediction ledger ───────────────────────────────

LEDGER_EXPERIMENT = "RN1X_MODEL_PROSPECTIVE_V1"

INSERT_PREDICTION = """
    INSERT INTO rn1x_model_predictions
        (experiment_id, target, model_key, model_version, dataset_sha,
         feature_sha, condition_id, source_trade_id, account,
         predicted_at, horizon_s, p_hat, features_present, features_missing,
         baseline_p, baseline_basis,
         outcome_known, outcome, outcome_at)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,to_timestamp($10),$11,$12,$13,$14,
            $15,$16,
            FALSE, NULL, NULL)
    ON CONFLICT (experiment_id, target, model_key, model_version,
                 source_trade_id) DO NOTHING
    RETURNING id
"""

JOIN_OUTCOME = """
    UPDATE rn1x_model_predictions
       SET outcome_known = TRUE, outcome = $2, outcome_at = to_timestamp($3)
     WHERE id = $1 AND outcome_known = FALSE
"""

UNRESOLVED = """
    SELECT id, target, model_key, model_version, condition_id,
           source_trade_id, account, extract(epoch FROM predicted_at) AS t0,
           horizon_s, p_hat
      FROM rn1x_model_predictions
     WHERE experiment_id = $1 AND outcome_known = FALSE
       AND extract(epoch FROM predicted_at) + horizon_s <= $2
     ORDER BY predicted_at
     LIMIT $3
"""


def feature_sha(features: dict) -> str:
    """Identity of the exact vector scored, so a later re-fit cannot
    quietly change what a recorded prediction was made from."""
    blob = json.dumps({k: features[k] for k in sorted(features)},
                      sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


async def record_prediction(conn, *, target, model_key, model_version,
                            dataset_sha, condition_id, source_trade_id,
                            account, predicted_at, horizon_s, p_hat,
                            availability, baseline_p=None,
                            baseline_basis=None) -> dict:
    """Write a prediction BEFORE its outcome exists.

    Refuses a p_hat outside [0, 1] and refuses to record against the
    settlement target, which has no fitted model: a row claiming one
    would be the exact overstatement the directive forbids.
    """
    if target not in FITTED_TARGETS:
        return {"ok": False, "refusal": "TARGET_HAS_NO_FITTED_MODEL",
                "target": target,
                "why": ("%r is declared but not fitted here, so there is "
                        "nothing to record a prediction from" % (target,))}
    try:
        p = float(p_hat)
    except (TypeError, ValueError):
        return {"ok": False, "refusal": "P_HAT_NOT_NUMERIC", "target": target}
    if not 0.0 <= p <= 1.0:
        return {"ok": False, "refusal": "P_HAT_OUT_OF_RANGE",
                "target": target, "p_hat": p}
    # `fetchval`, NOT `execute`, AND THE INSERT RETURNS ITS id.
    #
    # WHY THIS MATTERS. With DO NOTHING and `execute`, a conflicting insert
    # is indistinguishable from a successful one, so this returned ok for
    # rows it never wrote. A production cycle reported `recorded 293` while
    # the table held 159 -- the caller was counting validated ATTEMPTS and
    # calling them writes. A ledger whose own write count is wrong cannot
    # be the basis for a calibration claim.
    row_id = await conn.fetchval(
        INSERT_PREDICTION, LEDGER_EXPERIMENT, target, model_key,
        str(model_version), str(dataset_sha),
        feature_sha(availability.get("features") or {}),
        condition_id, int(source_trade_id), str(account),
        float(predicted_at), float(horizon_s), p,
        list(availability.get("present") or []),
        list(availability.get("missing") or []),
        # THE PRIOR, FIXED NOW. Scored against later, so it cannot be the
        # base rate of the labels it will be compared on. NULL when the
        # caller has none, and never filled in afterwards.
        (None if baseline_p is None else float(baseline_p)),
        (None if baseline_p is None
         else str(baseline_basis or "BASELINE_BASIS_NOT_DECLARED")))
    if row_id is None:
        # ALREADY RECORDED for this (target, model, version, trade). Not an
        # error and not a write.
        return {"ok": True, "written": False, "id": None,
                "refusal": None, "duplicate": True, "target": target,
                "p_hat": p, "experiment_id": LEDGER_EXPERIMENT,
                "why": ("a prediction for this trade already exists under "
                        "this model version; the ledger keeps the first")}
    return {"ok": True, "written": True, "id": int(row_id),
            "refusal": None, "duplicate": False, "target": target,
            "p_hat": p, "experiment_id": LEDGER_EXPERIMENT}


def describe() -> dict:
    return {
        "three_questions": {
            "scorer_exists_for_entry": False,
            "live_inputs_reach_the_decision_function": True,
            "evidence_qualifies_it_for_entry": ("unanswerable while the "
                                                "target is wrong; the gate "
                                                "refuses on TARGET first"),
        },
        "fitted_targets": list(FITTED_TARGETS),
        "entry_requires": ENTRY_REQUIRES,
        "estimators_available": list(ESTIMATORS),
        "ledger_experiment": LEDGER_EXPERIMENT,
        "ledger_records_before_outcome": True,
        "feeds_the_entry_gate": False,
    }
