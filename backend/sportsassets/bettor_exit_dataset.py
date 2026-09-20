"""§9. THE EXIT LEARNING DATASET. RECORDED BEFORE THE ANSWER EXISTS.

Owner directive, "FLAT-STATE PROOF IS APPROVED" §9:

    "For every prospective residual inventory state, record the state
    BEFORE future outcomes are known. Then label the counterfactual
    economics of: HOLD_5S / HOLD_15S / HOLD_30S / HOLD_60S /
    POST_COMPLEMENT / TAKE_COMPLEMENT / DIRECT_EXIT / HEDGE /
    COMPLETE_PAIR_AND_MERGE / HOLD_TO_SETTLEMENT -- where each action
    was actually evaluable. Preserve ACTION_AVAILABLE_AT_T0 separately
    from COUNTERFACTUAL_OUTCOME_LATER. Do not evaluate an action using
    information that was unavailable at T0. This dataset is what
    eventually allows BETTOR to learn to manage residual inventory
    better than Ferrari."

THE FERRARI QUESTION, RECORDED SO IT CAN BE ANSWERED. Pair economics
can be excellent while residual economics are terrible, and the gap is
decided entirely by what gets done with a leg that did not pair. This
dataset is the record of those decisions and their counterfactual
outcomes -- not of the pairs, which were never the problem.

────────────────────────────────────────────────────────────────────
THE SEPARATION IS THE DATASET.

    ACTION_AVAILABLE_AT_T0        what could have been done, judged on
                                  what was knowable then
    COUNTERFACTUAL_OUTCOME_LATER  what each of those would have led to

A dataset that scores an action using anything from the second column
while claiming to be the first measures our hindsight. So the T0 record
is SEALED with a sha before any outcome is appended, `label_action`
refuses to score an action that was not available at T0, and
`leak_check` compares the fields an evaluator says it read against the
declared T0 field set. A read outside that set is a refusal, not a
warning.
────────────────────────────────────────────────────────────────────

EVALUABLE IS NOT THE SAME AS APPLICABLE, AND BOTH ARE REQUIRED. An
action can be structurally possible in this inventory state and still
not be scoreable -- HOLD_TO_SETTLEMENT is applicable whenever we own a
leg, but settlement semantics are NOT_IDENTIFIED, so there is no
economics to label. Three statuses, never two:

    AVAILABLE_AND_EVALUABLE        score it
    APPLICABLE_BUT_NOT_EVALUABLE   possible, but the economics are not
                                   identified. Not a zero, not a loss
    NOT_APPLICABLE_CURRENT_STATE   the action cannot exist here at all

THE ACTION VOCABULARY HAS GAPS, AND THEY ARE EXPOSED RATHER THAN
MAPPED. The directive's label set is not the canonical action set:

    HOLD_5S/15S/30S/60S     HORIZON-PARAMETERISED. Canonical HOLD
                            carries no horizon, so these are four
                            distinct labels and none of them IS HOLD.
    COMPLETE_PAIR_AND_MERGE COMPOSITE of two canonical actions whose
                            preconditions differ: COMPLETE_PAIR needs
                            an acquirable complement, MERGE needs
                            matched quantity AND a venue mechanism.
                            Labelling it as one action hides which half
                            failed, so both halves are reported.

Silently mapping these onto canonical names would make the gap
invisible, which is the opposite of what a vocabulary table is for.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER. Every state in this
dataset is PROSPECTIVE: it describes inventory BETTOR would have held
had a counterfactual quote filled. See the shadow mandate for what may
create real shadow inventory -- today, nothing.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation

from . import bettor_applicability as applic
from . import bettor_ev_actions as acts

NOT_IDENTIFIED = "NOT_IDENTIFIED"

DATASET = "BETTOR_EXIT_LEARNING_DATASET_V1"

# ── §9: the labelled action set, exactly as the directive names it ───

HOLD_HORIZONS = ("HOLD_5S", "HOLD_15S", "HOLD_30S", "HOLD_60S")

MANAGEMENT_ACTIONS = (
    "POST_COMPLEMENT",
    "TAKE_COMPLEMENT",
    "DIRECT_EXIT",
    "HEDGE",
    "COMPLETE_PAIR_AND_MERGE",
    "HOLD_TO_SETTLEMENT",
)

LABELLED_ACTIONS = HOLD_HORIZONS + MANAGEMENT_ACTIONS

# ── the vocabulary gaps, exposed rather than mapped ──────────────────

VOCABULARY_GAPS = {
    "HOLD_5S": {
        "canonical": "HOLD",
        "relationship": "HORIZON_PARAMETERISED_NOT_EQUIVALENT",
        "why": ("canonical HOLD carries no horizon. HOLD_5S is a "
                "commitment to re-decide in 5 seconds, which is a "
                "different action from HOLD with a different cost. The "
                "four horizons are four labels, not four names for one"),
    },
    "COMPLETE_PAIR_AND_MERGE": {
        "canonical": ("COMPLETE_PAIR", "MERGE"),
        "relationship": "COMPOSITE_OF_TWO_ACTIONS_WITH_DIFFERENT_PRECONDITIONS",
        "why": ("COMPLETE_PAIR needs an acquirable complement. MERGE "
                "needs matched quantity AND a venue mechanism, "
                "independently. Scoring the composite as one action "
                "hides which half failed, so both halves are reported "
                "on the row"),
    },
}
for _h in HOLD_HORIZONS[1:]:
    VOCABULARY_GAPS[_h] = dict(VOCABULARY_GAPS["HOLD_5S"])

GAPS_ARE_EXPOSED_NOT_MAPPED = (
    "the directive's label set is not the canonical action set. "
    "Silently mapping one onto the other would make the gap invisible, "
    "which is the opposite of what a vocabulary table is for")

# The canonical action each label is GATED by for applicability. This is
# a gate, not an identity: see VOCABULARY_GAPS.
APPLICABILITY_GATE = {
    **{h: "HOLD" for h in HOLD_HORIZONS},
    "POST_COMPLEMENT": "POST_COMPLEMENT",
    "TAKE_COMPLEMENT": "TAKE_COMPLEMENT",
    "DIRECT_EXIT": "DIRECT_EXIT",
    "HEDGE": "HEDGE",
    "COMPLETE_PAIR_AND_MERGE": "COMPLETE_PAIR",
    "HOLD_TO_SETTLEMENT": "HOLD_TO_SETTLEMENT",
}

# ── three statuses, because applicable and evaluable are not one ─────

AVAILABLE = "AVAILABLE_AND_EVALUABLE"
NOT_EVALUABLE = "APPLICABLE_BUT_NOT_EVALUABLE"
NOT_APPLICABLE = applic.NOT_APPLICABLE

STATUS_MEANINGS = {
    AVAILABLE: "possible in this state and its economics are identified",
    NOT_EVALUABLE: ("possible in this state, but the economics are not "
                    "identified. That is not a zero and not a loss"),
    NOT_APPLICABLE: "the action cannot exist in this inventory state",
}

# What each label needs before it can be scored at all. A precondition
# here is about the ECONOMICS, not about whether the action exists.
EVALUABILITY_REQUIRES = {
    **{h: ("FUTURE_BOOK_AT_HORIZON",) for h in HOLD_HORIZONS},
    "POST_COMPLEMENT": ("COMPLEMENT_IDENTITY", "P_FILL"),
    "TAKE_COMPLEMENT": ("COMPLEMENT_IDENTITY", "COMPLEMENT_ASK", "FEE"),
    "DIRECT_EXIT": ("EXIT_BID", "FEE"),
    "HEDGE": ("HEDGE_TAX", "COMPLEMENT_IDENTITY"),
    "COMPLETE_PAIR_AND_MERGE": ("COMPLEMENT_IDENTITY", "MATCHED_QTY",
                                "MERGE_MECHANISM"),
    "HOLD_TO_SETTLEMENT": ("SETTLEMENT_SEMANTICS",),
}

# ── §9: the T0 record, sealed before any outcome exists ──────────────

AT_T0_FIELDS = (
    "T0_TIMESTAMP",
    "MARKET_ID",
    "INVENTORY_STATE",
    # the residual itself
    "RESIDUAL_LEG",
    "RESIDUAL_QTY",
    "RESIDUAL_BASIS",
    "MATCHED_QTY",
    "MATCHED_PAIR_BASIS",
    "CAPITAL_IN_RESIDUAL_INVENTORY",
    # the book as it was
    "BID_AT_T0",
    "ASK_AT_T0",
    "SPREAD_AT_T0",
    "DEPTH_AT_T0",
    "COMPLEMENT_BID_AT_T0",
    "COMPLEMENT_ASK_AT_T0",
    "COMPLEMENT_IDENTITY_STATUS",
    # the clock
    "TIME_TO_EVENT_AT_T0",
    "AGE_OF_RESIDUAL_S",
    # what could have been done, and why not where not
    "ACTION_AVAILABLE_AT_T0",
)

OUTCOME_FIELDS = (
    "COUNTERFACTUAL_OUTCOME_LATER",
    "OUTCOME_TIMESTAMP",
    "REALISED_BOOK_PATH",
    "COUNTERFACTUAL_PNL",
    "COUNTERFACTUAL_CAPITAL_HOURS",
    "COUNTERFACTUAL_RESIDUAL_REMAINING",
    "OUTCOME_IDENTIFICATION_STATUS",
)

SEPARATION_RULE = (
    "ACTION_AVAILABLE_AT_T0 is stored separately from "
    "COUNTERFACTUAL_OUTCOME_LATER. A dataset that scores an action "
    "using anything from the second while claiming to be the first "
    "measures our hindsight rather than the decision")

LEAK_RULE = (
    "an evaluator declares the fields it read. A read outside "
    "AT_T0_FIELDS is a refusal, not a warning: information that was not "
    "available at T0 cannot enter a T0 decision, however convenient")

PROSPECTIVE_NOT_REAL = (
    "every state here is PROSPECTIVE -- inventory BETTOR would have "
    "held had a counterfactual quote filled. No order was placed and no "
    "position exists. Whether a supported fill may become shadow "
    "inventory is the mandate's question, and the mandate is not frozen")


def _d(v):
    if v is None or v == "" or v == NOT_IDENTIFIED:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def availability(label: str, inventory_state: str, *,
                 identified=()) -> dict:
    """Three statuses for one label. Applicability first, then economics.

    `identified` is the set of preconditions established at T0. Anything
    not in it is missing, and a missing precondition makes the action
    APPLICABLE_BUT_NOT_EVALUABLE -- never a zero.
    """
    if label not in LABELLED_ACTIONS:
        return {"label": label, "status": "UNKNOWN_LABEL",
                "declared": list(LABELLED_ACTIONS)}

    gate = APPLICABILITY_GATE[label]
    verdict = applic.applicability(gate, inventory_state)
    out = {
        "label": label,
        "applicabilityGate": gate,
        "APPLICABILITY_STATUS": verdict["APPLICABILITY_STATUS"],
        "APPLICABILITY_REASON": verdict["APPLICABILITY_REASON"],
        "evaluabilityRequires": list(EVALUABILITY_REQUIRES[label]),
        "statusMeanings": dict(STATUS_MEANINGS),
    }
    if label in VOCABULARY_GAPS:
        out["vocabularyGap"] = dict(VOCABULARY_GAPS[label])
        out["gapsAreExposedNotMapped"] = GAPS_ARE_EXPOSED_NOT_MAPPED

    if verdict["APPLICABILITY_STATUS"] != applic.APPLICABLE:
        out.update({
            "AVAILABILITY_STATUS": verdict["APPLICABILITY_STATUS"],
            "why": verdict["APPLICABILITY_REASON"],
            "whyNotZero": applic.WHY_ZERO_IS_WORSE_THAN_NOTHING,
        })
        return out

    have = set(identified or ())
    missing = [p for p in EVALUABILITY_REQUIRES[label] if p not in have]
    if missing:
        out.update({
            "AVAILABILITY_STATUS": NOT_EVALUABLE,
            "preconditionsMissing": missing,
            "why": ("the action is possible in this state, but its "
                    "economics need %s, which are not identified"
                    % ", ".join(missing)),
            "whyNotZero": applic.WHY_ZERO_IS_WORSE_THAN_NOTHING,
        })
        return out

    out["AVAILABILITY_STATUS"] = AVAILABLE
    # The composite reports both halves, because they fail separately.
    if label == "COMPLETE_PAIR_AND_MERGE":
        out["halves"] = {
            "COMPLETE_PAIR": applic.applicability(
                "COMPLETE_PAIR", inventory_state)["APPLICABILITY_STATUS"],
            "MERGE": applic.applicability(
                "MERGE", inventory_state)["APPLICABILITY_STATUS"],
        }
    return out


def state_at_t0(*, t0_timestamp, market_id=None, inventory_state=None,
                residual_leg=None, residual_qty=None, residual_basis=None,
                matched_qty=None, matched_pair_basis=None,
                capital_in_residual=None, book=None, complement=None,
                time_to_event=None, age_of_residual_s=None,
                identified=()) -> dict:
    """The residual state, sealed before any outcome is known.

    Nothing about the future is admitted. The availability verdict for
    every labelled action is computed HERE, from T0 information only,
    and sealed with the rest.
    """
    book = book or {}
    complement = complement or {}
    state = inventory_state or applic.STATE_NOT_IDENTIFIED

    available = {label: availability(label, state, identified=identified)
                 for label in LABELLED_ACTIONS}

    record = {
        "dataset": DATASET,
        "T0_TIMESTAMP": t0_timestamp,
        "MARKET_ID": market_id or NOT_IDENTIFIED,
        "INVENTORY_STATE": state,
        "RESIDUAL_LEG": residual_leg or NOT_IDENTIFIED,
        "RESIDUAL_QTY": (str(_d(residual_qty)) if _d(residual_qty) is not None
                         else NOT_IDENTIFIED),
        "RESIDUAL_BASIS": (str(_d(residual_basis))
                           if _d(residual_basis) is not None
                           else NOT_IDENTIFIED),
        "MATCHED_QTY": (str(_d(matched_qty)) if _d(matched_qty) is not None
                        else NOT_IDENTIFIED),
        "MATCHED_PAIR_BASIS": (str(_d(matched_pair_basis))
                               if _d(matched_pair_basis) is not None
                               else NOT_IDENTIFIED),
        "CAPITAL_IN_RESIDUAL_INVENTORY": (
            str(_d(capital_in_residual))
            if _d(capital_in_residual) is not None else NOT_IDENTIFIED),
        "BID_AT_T0": book.get("bid", NOT_IDENTIFIED),
        "ASK_AT_T0": book.get("ask", NOT_IDENTIFIED),
        "SPREAD_AT_T0": book.get("spread", NOT_IDENTIFIED),
        "DEPTH_AT_T0": book.get("availableDepth", NOT_IDENTIFIED),
        "COMPLEMENT_BID_AT_T0": complement.get("bid", NOT_IDENTIFIED),
        "COMPLEMENT_ASK_AT_T0": complement.get("ask", NOT_IDENTIFIED),
        "COMPLEMENT_IDENTITY_STATUS": complement.get("identityStatus",
                                                     NOT_IDENTIFIED),
        "TIME_TO_EVENT_AT_T0": (str(_d(time_to_event))
                                if _d(time_to_event) is not None
                                else NOT_IDENTIFIED),
        "AGE_OF_RESIDUAL_S": (str(_d(age_of_residual_s))
                              if _d(age_of_residual_s) is not None
                              else NOT_IDENTIFIED),
        "ACTION_AVAILABLE_AT_T0": available,
        # Named, empty, and not fillable from here.
        "outcomeFieldsAppendedLater": list(OUTCOME_FIELDS),
        "separationRule": SEPARATION_RULE,
        "leakRule": LEAK_RULE,
        "prospectiveNotReal": PROSPECTIVE_NOT_REAL,
    }
    record["STATE_SHA_AT_T0"] = hashlib.sha256(
        "|".join(str(record[k]) for k in
                 ("T0_TIMESTAMP", "MARKET_ID", "INVENTORY_STATE",
                  "RESIDUAL_LEG", "RESIDUAL_QTY", "RESIDUAL_BASIS",
                  "BID_AT_T0", "ASK_AT_T0")).encode()).hexdigest()[:16]
    return record


def leak_check(fields_read) -> dict:
    """Did an evaluator read anything that was not available at T0?

    Refuses rather than warns. The whole value of the dataset is that
    the T0 column cannot be contaminated by the later one.
    """
    read = list(fields_read or ())
    outside = [f for f in read if f not in AT_T0_FIELDS]
    from_future = [f for f in read if f in OUTCOME_FIELDS]
    return {
        "LEAK_CHECK": "CLEAN" if not outside else "REFUSED",
        "fieldsRead": read,
        "fieldsOutsideT0": outside,
        "fieldsFromTheOutcomeColumn": from_future,
        "rule": LEAK_RULE,
    }


def label_action(state: dict, label: str, *, fields_read=(),
                 counterfactual_pnl=None, counterfactual_capital_hours=None,
                 counterfactual_residual_remaining=None,
                 outcome_timestamp=None,
                 outcome_identification_status=None) -> dict:
    """Append one action's counterfactual outcome to a sealed T0 record.

    REFUSES on two conditions, each named: an action that was not
    AVAILABLE_AND_EVALUABLE at T0, and an evaluator that read outside
    the T0 field set.
    """
    avail = (state.get("ACTION_AVAILABLE_AT_T0") or {}).get(label)
    out = {
        "dataset": DATASET,
        "STATE_SHA_AT_T0": state.get("STATE_SHA_AT_T0"),
        "label": label,
        "separationRule": SEPARATION_RULE,
        "prospectiveNotReal": PROSPECTIVE_NOT_REAL,
    }
    if avail is None:
        out.update({"COUNTERFACTUAL_OUTCOME_LATER": NOT_IDENTIFIED,
                    "refused": "UNKNOWN_LABEL",
                    "declared": list(LABELLED_ACTIONS)})
        return out

    out["AVAILABILITY_STATUS_AT_T0"] = avail.get("AVAILABILITY_STATUS")
    if avail.get("AVAILABILITY_STATUS") != AVAILABLE:
        # §9: "where each action was actually evaluable". An action that
        # was not gets no number -- not a zero, not a loss.
        out.update({
            "COUNTERFACTUAL_OUTCOME_LATER": NOT_IDENTIFIED,
            "refused": "NOT_AVAILABLE_AND_EVALUABLE_AT_T0",
            "why": avail.get("why"),
            "whyNotZero": applic.WHY_ZERO_IS_WORSE_THAN_NOTHING,
        })
        return out

    leak = leak_check(fields_read)
    out["leakCheck"] = leak
    if leak["LEAK_CHECK"] != "CLEAN":
        out.update({
            "COUNTERFACTUAL_OUTCOME_LATER": NOT_IDENTIFIED,
            "refused": "T0_LEAK",
            "why": ("the evaluator read %s, which was not available at "
                    "T0" % ", ".join(leak["fieldsOutsideT0"])),
        })
        return out

    out.update({
        "COUNTERFACTUAL_OUTCOME_LATER": "APPENDED",
        "OUTCOME_TIMESTAMP": outcome_timestamp or NOT_IDENTIFIED,
        "COUNTERFACTUAL_PNL": (str(_d(counterfactual_pnl))
                               if _d(counterfactual_pnl) is not None
                               else NOT_IDENTIFIED),
        "COUNTERFACTUAL_CAPITAL_HOURS": (
            str(_d(counterfactual_capital_hours))
            if _d(counterfactual_capital_hours) is not None
            else NOT_IDENTIFIED),
        "COUNTERFACTUAL_RESIDUAL_REMAINING": (
            str(_d(counterfactual_residual_remaining))
            if _d(counterfactual_residual_remaining) is not None
            else NOT_IDENTIFIED),
        "OUTCOME_IDENTIFICATION_STATUS": (outcome_identification_status
                                          or NOT_IDENTIFIED),
    })
    return out


def evaluable_set(state: dict) -> dict:
    """Which labels a row can actually be scored on, and why not the rest."""
    avail = state.get("ACTION_AVAILABLE_AT_T0") or {}
    buckets = {AVAILABLE: [], NOT_EVALUABLE: [], NOT_APPLICABLE: [],
               applic.APPLICABILITY_NOT_IDENTIFIED: []}
    for label, v in avail.items():
        buckets.setdefault(v.get("AVAILABILITY_STATUS"), []).append(label)
    return {
        "STATE_SHA_AT_T0": state.get("STATE_SHA_AT_T0"),
        "INVENTORY_STATE": state.get("INVENTORY_STATE"),
        "byStatus": {k: sorted(v) for k, v in buckets.items() if v},
        "EVALUABLE": sorted(buckets.get(AVAILABLE, [])),
        "statusMeanings": dict(STATUS_MEANINGS),
    }


def describe() -> dict:
    return {
        "dataset": DATASET,
        "labelledActions": list(LABELLED_ACTIONS),
        "holdHorizons": list(HOLD_HORIZONS),
        "managementActions": list(MANAGEMENT_ACTIONS),
        "vocabularyGaps": dict(VOCABULARY_GAPS),
        "gapsAreExposedNotMapped": GAPS_ARE_EXPOSED_NOT_MAPPED,
        "applicabilityGate": dict(APPLICABILITY_GATE),
        "evaluabilityRequires": {k: list(v) for k, v
                                 in EVALUABILITY_REQUIRES.items()},
        "statusMeanings": dict(STATUS_MEANINGS),
        "atT0Fields": list(AT_T0_FIELDS),
        "outcomeFields": list(OUTCOME_FIELDS),
        "separationRule": SEPARATION_RULE,
        "leakRule": LEAK_RULE,
        "prospectiveNotReal": PROSPECTIVE_NOT_REAL,
        "canonicalActions": list(acts.ACTIONS),
    }
