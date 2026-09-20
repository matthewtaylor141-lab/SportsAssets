"""§10. THE EXIT ML INTERFACE. BUILT NOW, TRAINED LATER, NEVER GUESSED.

Owner directive, "FLAT-STATE PROOF IS APPROVED" §10:

    "Build the interface now. Do not train/promote a policy until
    evidence supports it. Inputs: LEG / BASIS / QUANTITY / MATCHED_QTY
    / RESIDUAL_QTY / RESIDUAL_AGE / BID / ASK / SPREAD / DEPTH /
    IMBALANCE / RECENT_MOVE / VOLATILITY / COMPLEMENT_PRICE /
    PAIR_BASIS / PAIR_MARGIN / COMPLETION_HAZARD / TIME_TO_COMPLETION /
    FV_BETTOR / FV_UNCERTAINTY / P_FILL / ADVERSE_SELECTION / TOXICITY
    / TIME_TO_EVENT / MARKET_STATE / CAPITAL_HOURS / EVENT_EXPOSURE /
    CORRELATED_EXPOSURE. Outputs must be a DISTRIBUTION for each
    applicable action: EXPECTED_NET_DOLLARS / LOWER_CONFIDENCE_VALUE /
    UPPER_CONFIDENCE_VALUE / DOWNSIDE_TAIL / EXPECTED_CAPITAL_RELEASE /
    EXPECTED_CAPITAL_HOURS. Do not output only BUY / SELL."

WHY THE INTERFACE COMES BEFORE THE MODEL. The shape of the output
decides what the model is allowed to be. An interface that returns a
verdict admits only models that produce verdicts, and by the time the
data exists the shape is already load-bearing everywhere downstream.
Fixing it now costs nothing; fixing it later costs the callers.

────────────────────────────────────────────────────────────────────
A POINT ESTIMATE IS A DECISION SOMEBODY ELSE ALREADY MADE.

    EXPECTED_NET_DOLLARS        the mean
    LOWER_CONFIDENCE_VALUE      the conservative read
    UPPER_CONFIDENCE_VALUE      the optimistic read
    DOWNSIDE_TAIL               what the bad case costs
    EXPECTED_CAPITAL_RELEASE    what the action frees
    EXPECTED_CAPITAL_HOURS      what it ties up

Two actions with the same mean and different tails are different
actions, and the exit problem is precisely where that matters: HOLD and
DIRECT_EXIT routinely have similar means and completely different
downside. A BUY/SELL output would collapse them, and the collapse is
invisible at the call site. So `predict` returns a distribution per
action or NOT_IDENTIFIED, and there is no code path that returns a
single recommended action.
────────────────────────────────────────────────────────────────────

NOT_TRAINED IS THE HONEST STATE AND IT IS ENFORCED. No exit policy
exists: the exit learning dataset has zero rows, so there is nothing to
fit. `predict` returns POLICY_NOT_TRAINED for every action with the
inputs that would have been used, so a caller can see what the model
WOULD have consumed without receiving a number nobody earned.
`promote` refuses on named blockers and cannot be argued into a
promotion by a caller.

MISSING INPUTS ARE NOT IMPUTED. An absent feature stays
NOT_IDENTIFIED. Imputation -- a median, a zero, a last-known value --
is a prediction about the input dressed as data, and it makes a model
look confident about exactly the states it knows least about.

P_FILL IS AN INPUT AND IT IS NOT_IDENTIFIED. Three of the listed
inputs (P_FILL, FV_BETTOR, FV_UNCERTAINTY) are structurally
unavailable today. The interface names them as inputs anyway, because
a feature list that quietly drops what is missing is how a model ends
up trained on whatever happened to be easy.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER, AND NOTHING HERE IS A
RECOMMENDATION.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from . import bettor_applicability as applic
from . import bettor_exit_dataset as xd

NOT_IDENTIFIED = "NOT_IDENTIFIED"

INTERFACE = "BETTOR_EXIT_ML_INTERFACE_V1"

# ── the gate. No policy exists and none may be promoted ──────────────

POLICY_STATUS = "POLICY_NOT_TRAINED"
POLICY_VERSION = NOT_IDENTIFIED
POLICY_ACTIVE = False
TRAINING_ROWS = 0

WHY_INTERFACE_BEFORE_MODEL = (
    "the shape of the output decides what the model is allowed to be. "
    "An interface that returns a verdict admits only models that "
    "produce verdicts, and by the time the data exists the shape is "
    "load-bearing everywhere downstream. Fixing it now costs nothing")

# ── §10: the twenty-eight inputs, named even where unavailable ───────

INPUTS = (
    "LEG",
    "BASIS",
    "QUANTITY",
    "MATCHED_QTY",
    "RESIDUAL_QTY",
    "RESIDUAL_AGE",
    "BID",
    "ASK",
    "SPREAD",
    "DEPTH",
    "IMBALANCE",
    "RECENT_MOVE",
    "VOLATILITY",
    "COMPLEMENT_PRICE",
    "PAIR_BASIS",
    "PAIR_MARGIN",
    "COMPLETION_HAZARD",
    "TIME_TO_COMPLETION",
    "FV_BETTOR",
    "FV_UNCERTAINTY",
    "P_FILL",
    "ADVERSE_SELECTION",
    "TOXICITY",
    "TIME_TO_EVENT",
    "MARKET_STATE",
    "CAPITAL_HOURS",
    "EVENT_EXPOSURE",
    "CORRELATED_EXPOSURE",
)

# Inputs that cannot be supplied today, with the reason. Named here so
# a feature list that quietly drops them is a visible difference.
STRUCTURALLY_UNAVAILABLE = {
    "P_FILL": ("NOT_IDENTIFIED until BETTOR-native admitted executions "
               "exist. No substitute is accepted"),
    "FV_BETTOR": ("FV_BETTOR_INDEPENDENT is NOT_IDENTIFIED. The venue "
                  "price is not an independent fair value"),
    "FV_UNCERTAINTY": ("the width of a fair value that does not exist "
                       "is not a number"),
    "COMPLETION_HAZARD": ("available only as a whale-derived "
                          "STRUCTURAL_COMPLETION_PRIOR with provenance. "
                          "It is NOT BETTOR P_FILL and must enter as a "
                          "prior, labelled"),
}

NO_IMPUTATION = (
    "an absent feature stays NOT_IDENTIFIED. Imputation -- a median, a "
    "zero, a last-known value -- is a prediction about the input "
    "dressed as data, and it makes a model look confident about exactly "
    "the states it knows least about")

FEATURE_LIST_KEEPS_ITS_GAPS = (
    "inputs that are structurally unavailable are still named as "
    "inputs. A feature list that quietly drops what is missing is how a "
    "model ends up trained on whatever happened to be easy")

# ── §10: the outputs, a distribution per action ──────────────────────

OUTPUTS = (
    "EXPECTED_NET_DOLLARS",
    "LOWER_CONFIDENCE_VALUE",
    "UPPER_CONFIDENCE_VALUE",
    "DOWNSIDE_TAIL",
    "EXPECTED_CAPITAL_RELEASE",
    "EXPECTED_CAPITAL_HOURS",
)

NOT_A_VERDICT = (
    "there is no code path that returns a single recommended action. "
    "Two actions with the same mean and different tails are different "
    "actions, and the exit problem is where that matters most: HOLD and "
    "DIRECT_EXIT routinely have similar means and completely different "
    "downside. A BUY/SELL output collapses them invisibly")

RANKING_IS_THE_CALLERS_JOB = (
    "this interface returns distributions. Choosing among them is a "
    "policy decision that belongs to the risk gate and the allocator, "
    "which have the exposure limits this module does not")

# ── promotion gate ───────────────────────────────────────────────────

PROMOTION_REQUIRES = (
    "EXIT_LEARNING_DATASET_ROWS_ABOVE_ZERO",
    "IDENTIFIED_OUTCOMES_FOR_EACH_LABELLED_ACTION",
    "OUT_OF_SAMPLE_EVALUATION",
    "LEAK_CHECK_CLEAN",
    "SELECTION_MEASURED",
    "OWNER_APPROVAL",
)

ONE_DATASET_DOES_NOT_MAKE_A_POLICY = (
    "rows are not evidence that a policy generalises. The promotion "
    "gate asks for out-of-sample performance and a clean leak check as "
    "well as rows, because a policy fitted on a leaked dataset scores "
    "perfectly and decides nothing")


def _d(v):
    if v is None or v == "" or v == NOT_IDENTIFIED:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def features(state: dict | None = None, **supplied) -> dict:
    """The full input vector. Every declared input, present or named absent.

    Values come from an exit-dataset T0 state where the mapping is
    unambiguous, and from explicit keyword arguments otherwise. Nothing
    is imputed.
    """
    s = state or {}
    from_state = {
        "LEG": s.get("RESIDUAL_LEG"),
        "BASIS": s.get("RESIDUAL_BASIS"),
        "QUANTITY": s.get("RESIDUAL_QTY"),
        "MATCHED_QTY": s.get("MATCHED_QTY"),
        "RESIDUAL_QTY": s.get("RESIDUAL_QTY"),
        "RESIDUAL_AGE": s.get("AGE_OF_RESIDUAL_S"),
        "BID": s.get("BID_AT_T0"),
        "ASK": s.get("ASK_AT_T0"),
        "SPREAD": s.get("SPREAD_AT_T0"),
        "DEPTH": s.get("DEPTH_AT_T0"),
        "COMPLEMENT_PRICE": s.get("COMPLEMENT_ASK_AT_T0"),
        "PAIR_BASIS": s.get("MATCHED_PAIR_BASIS"),
        "TIME_TO_EVENT": s.get("TIME_TO_EVENT_AT_T0"),
        "CAPITAL_HOURS": s.get("CAPITAL_IN_RESIDUAL_INVENTORY"),
    }

    vec, missing = {}, []
    for name in INPUTS:
        v = supplied.get(name, from_state.get(name))
        if v is None or v == NOT_IDENTIFIED:
            vec[name] = NOT_IDENTIFIED
            missing.append(name)
        else:
            vec[name] = v

    return {
        "interface": INTERFACE,
        "inputs": vec,
        "inputsDeclared": list(INPUTS),
        "inputsMissing": missing,
        "structurallyUnavailable": {
            k: v for k, v in STRUCTURALLY_UNAVAILABLE.items()
            if k in missing},
        "noImputation": NO_IMPUTATION,
        "featureListKeepsItsGaps": FEATURE_LIST_KEEPS_ITS_GAPS,
    }


def _blank_distribution(action, why):
    """A distribution-shaped absence. Every output named, none invented."""
    return {
        "action": action,
        "POLICY_STATUS": POLICY_STATUS,
        **{o: NOT_IDENTIFIED for o in OUTPUTS},
        "why": why,
        "isNotAVerdict": NOT_A_VERDICT,
        "whyNotZero": applic.WHY_ZERO_IS_WORSE_THAN_NOTHING,
    }


def predict(state: dict | None = None, **supplied) -> dict:
    """A DISTRIBUTION per applicable action, or a named absence.

    Today every action returns POLICY_NOT_TRAINED with the inputs the
    model WOULD have consumed, so a caller can see the shape without
    receiving a number nobody earned.
    """
    s = state or {}
    f = features(s, **supplied)
    avail = s.get("ACTION_AVAILABLE_AT_T0") or {}

    rows = {}
    for label in xd.LABELLED_ACTIONS:
        v = avail.get(label)
        if v is None:
            rows[label] = _blank_distribution(
                label, "no availability verdict: the T0 state was not "
                       "supplied, so applicability is not identified")
            rows[label]["AVAILABILITY_STATUS_AT_T0"] = NOT_IDENTIFIED
            continue
        status = v.get("AVAILABILITY_STATUS")
        if status != xd.AVAILABLE:
            # §10 says a distribution "for each applicable action". An
            # action that is not applicable gets no distribution at all
            # -- not a zero-valued one, which would rank.
            rows[label] = {
                "action": label,
                "AVAILABILITY_STATUS_AT_T0": status,
                "PREDICTION_STATUS": applic.NOT_EVALUATED,
                "why": v.get("why") or v.get("APPLICABILITY_REASON"),
                "whyNotZero": applic.WHY_ZERO_IS_WORSE_THAN_NOTHING,
            }
            continue
        d = _blank_distribution(
            label, "no exit policy is trained: the exit learning "
                   "dataset has %d rows, so there is nothing to fit"
                   % TRAINING_ROWS)
        d["AVAILABILITY_STATUS_AT_T0"] = status
        d["PREDICTION_STATUS"] = POLICY_STATUS
        rows[label] = d

    return {
        "interface": INTERFACE,
        "POLICY_STATUS": POLICY_STATUS,
        "POLICY_VERSION": POLICY_VERSION,
        "POLICY_ACTIVE": POLICY_ACTIVE,
        "TRAINING_ROWS": TRAINING_ROWS,
        "predictions": rows,
        "outputsDeclared": list(OUTPUTS),
        "inputsUsed": f,
        "isNotAVerdict": NOT_A_VERDICT,
        "rankingIsTheCallersJob": RANKING_IS_THE_CALLERS_JOB,
        "whyInterfaceBeforeModel": WHY_INTERFACE_BEFORE_MODEL,
    }


def promote(*, rows=0, out_of_sample=None, leak_check=None,
            selection_measured=None, owner_approval_token=None,
            actions_with_outcomes=()) -> dict:
    """Promote a trained exit policy. REFUSES, with each blocker named."""
    blockers = []
    if not rows:
        blockers.append("EXIT_LEARNING_DATASET_ROWS_ABOVE_ZERO")
    uncovered = [a for a in xd.LABELLED_ACTIONS
                 if a not in set(actions_with_outcomes or ())]
    if uncovered:
        blockers.append("IDENTIFIED_OUTCOMES_FOR_EACH_LABELLED_ACTION")
    if not out_of_sample:
        blockers.append("OUT_OF_SAMPLE_EVALUATION")
    if leak_check != "CLEAN":
        blockers.append("LEAK_CHECK_CLEAN")
    if not selection_measured:
        blockers.append("SELECTION_MEASURED")
    if not owner_approval_token:
        blockers.append("OWNER_APPROVAL")
    return {
        "promoted": False if blockers else True,
        "POLICY_STATUS": POLICY_STATUS if blockers else "POLICY_TRAINED",
        "POLICY_ACTIVE": False,
        "blockers": blockers,
        "requires": list(PROMOTION_REQUIRES),
        "actionsWithoutOutcomes": uncovered,
        "oneDatasetDoesNotMakeAPolicy": ONE_DATASET_DOES_NOT_MAKE_A_POLICY,
        "promotionIsNotActivation": (
            "a promoted policy is still not active. Activation is a "
            "separate owner decision and this module cannot take it"),
    }


def describe() -> dict:
    return {
        "interface": INTERFACE,
        "POLICY_STATUS": POLICY_STATUS,
        "POLICY_VERSION": POLICY_VERSION,
        "POLICY_ACTIVE": POLICY_ACTIVE,
        "TRAINING_ROWS": TRAINING_ROWS,
        "inputs": list(INPUTS),
        "structurallyUnavailable": dict(STRUCTURALLY_UNAVAILABLE),
        "outputs": list(OUTPUTS),
        "actions": list(xd.LABELLED_ACTIONS),
        "promotionRequires": list(PROMOTION_REQUIRES),
        "isNotAVerdict": NOT_A_VERDICT,
        "noImputation": NO_IMPUTATION,
        "featureListKeepsItsGaps": FEATURE_LIST_KEEPS_ITS_GAPS,
        "rankingIsTheCallersJob": RANKING_IS_THE_CALLERS_JOB,
        "whyInterfaceBeforeModel": WHY_INTERFACE_BEFORE_MODEL,
        "oneDatasetDoesNotMakeAPolicy": ONE_DATASET_DOES_NOT_MAKE_A_POLICY,
        "promotionRefusal": promote(),
    }
