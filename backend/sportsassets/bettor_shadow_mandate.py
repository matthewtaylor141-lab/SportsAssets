"""§12. THE PROSPECTIVE SHADOW MANDATE. PROPOSED, NOT ACTIVE.

Owner directive, "STOP BEFORE BUILDING §5 P_FILL LABELS" §12:

    "Do not create prospective BETTOR inventory merely because a
    COUNTERFACTUAL_FILL_SUPPORTED label exists. Before shadow positions
    are allowed to affect the BETTOR portfolio, define the exact
    prospective shadow mandate: WHICH ACTIONS MAY CREATE SHADOW
    INVENTORY / STANDARD NOTIONAL / MAX MARKET EXPOSURE / MAX EVENT
    EXPOSURE / MAX RESIDUAL INVENTORY / MAX CAPITAL DEPLOYED / MAX
    CAPITAL HOURS / PAIRING RULE / EXIT RULE / NO-TRADE RULE. These
    must be frozen before the first BETTOR EV shadow position. Do not
    choose them after seeing portfolio results."

WHY A LABEL IS NOT A PERMISSION. `bettor_shadow_execution` can now
produce COUNTERFACTUAL_FILL_SUPPORTED, and the obvious next move --
turn every supported label into a shadow position -- is the one this
module exists to block. A fill label answers "would this quote have
been reached". It says nothing about whether we wanted the quote, how
large it should have been, how much of the book we may hold at once, or
when we would have got out. Those are policy, and policy chosen after
the portfolio exists is chosen by the portfolio.

THE LIMITS ARE THE EXPERIMENT. Ferrari's lesson is that pair economics
and residual economics diverge: the pairs looked excellent while the
residual inventory was terrible. A shadow book with no exposure cap
would never surface that, because it would never accumulate a residual
it could not carry. MAX_RESIDUAL_INVENTORY and MAX_CAPITAL_HOURS are
not safety furniture around the test -- they are the test.

    A mandate chosen after seeing which limits would have been
    profitable is a backtest of the limits, not of the strategy.

STATUS: PROPOSED. Nothing here is frozen, nothing is active, and no
inventory may be created while that remains true. `freeze()` refuses
without an explicit owner approval token, and `may_create_inventory()`
returns False with a named blocker. The numbers below are a PROPOSAL
for review -- the owner sets them, and the frozen values are whatever
the owner approves, sha'd at the moment of approval so a later edit is
visible as a different sha rather than as nothing at all.

NOTHING HERE PLACES, SIZES OR FUNDS A REAL ORDER. Every quantity is a
shadow quantity. BETTOR_EV_REAL_ORDER_ACTIVITY = NONE and
BETTOR_EV_REAL_CAPITAL_AT_RISK = 0 are unchanged by this module and
cannot be changed by it.
"""

from __future__ import annotations

import hashlib
import json

from . import bettor_ev_actions as acts

NOT_IDENTIFIED = "NOT_IDENTIFIED"

MANDATE_VERSION = "BETTOR_EV_SHADOW_MANDATE_V1_PROPOSED"

# ── the gate. Both False, and only the owner moves them ──────────────

# §6 of "STOP here": the owner declined to let OWNER_APPROVAL become
# the only thing between this system and inventory creation while
# P_FILL has zero rows, EXIT has zero rows, the maker engine prices
# nothing and the allocator ranks nothing. EVIDENCE is now a gate of
# its own, ahead of approval and not satisfiable by it.
MANDATE_STATUS = "PROPOSED_AWAITING_EVIDENCE_AND_OWNER_APPROVAL"
MANDATE_FROZEN = False
MANDATE_ACTIVE = False

BETTOR_EV_SHADOW_POSITIONS = 0
BETTOR_EV_REAL_ORDER_ACTIVITY = "NONE"
BETTOR_EV_REAL_CAPITAL_AT_RISK = 0

WHY_A_LABEL_IS_NOT_A_PERMISSION = (
    "COUNTERFACTUAL_FILL_SUPPORTED answers whether a quote would have "
    "been reached. It says nothing about whether we wanted the quote, "
    "how large it should have been, how much we may hold at once, or "
    "when we would have got out. Those are policy, and policy chosen "
    "after the portfolio exists is chosen by the portfolio")

WHY_THE_LIMITS_ARE_THE_EXPERIMENT = (
    "Ferrari's lesson is that pair economics and residual economics "
    "diverge. A shadow book with no exposure cap would never surface "
    "that, because it would never accumulate a residual it could not "
    "carry. MAX_RESIDUAL_INVENTORY and MAX_CAPITAL_HOURS are the test, "
    "not furniture around it")

CHOSEN_BEFORE_RESULTS = (
    "a mandate chosen after seeing which limits would have been "
    "profitable is a backtest of the limits, not of the strategy. The "
    "sha is taken at approval so a later edit is visible as a different "
    "sha rather than as nothing at all")

# ── §12: the ten parameters, every one required ──────────────────────

REQUIRED_PARAMETERS = (
    "ACTIONS_THAT_MAY_CREATE_SHADOW_INVENTORY",
    "STANDARD_NOTIONAL",
    "MAX_MARKET_EXPOSURE",
    "MAX_EVENT_EXPOSURE",
    "MAX_RESIDUAL_INVENTORY",
    "MAX_CAPITAL_DEPLOYED",
    "MAX_CAPITAL_HOURS",
    "PAIRING_RULE",
    "EXIT_RULE",
    "NO_TRADE_RULE",
)

# ── THE PROPOSAL. Numbers for review, not settings in force ──────────

PROPOSED = {
    # Entry only, and only the two passive actions. A shadow book built
    # from aggressive actions would measure the taker cost we already
    # know rather than the maker question we cannot answer.
    "ACTIONS_THAT_MAY_CREATE_SHADOW_INVENTORY": ("MAKE_YES", "MAKE_NO",
                                                 "MAKE_BOTH"),
    # One size for every quote. A sized-by-conviction shadow book would
    # confound fill evidence with sizing skill, and sizing is not what
    # this experiment is testing.
    "STANDARD_NOTIONAL": {"contracts": 10, "currency": "USD",
                          "why": ("one size everywhere, so fill evidence "
                                  "is not confounded with sizing")},
    "MAX_MARKET_EXPOSURE": {"contractsPerMarket": 40},
    "MAX_EVENT_EXPOSURE": {"contractsPerEvent": 100},
    # THE FERRARI PARAMETER. An unpaired first leg is the residual, and
    # the cap is deliberately low: the point is to hit it and find out
    # what carrying it costs, not to avoid it.
    "MAX_RESIDUAL_INVENTORY": {"unpairedLegs": 5,
                               "why": ("deliberately low. The point is "
                                       "to hit this and measure what "
                                       "carrying a residual costs")},
    "MAX_CAPITAL_DEPLOYED": {"usd": 500, "note": "SHADOW_DOLLARS_NOT_REAL"},
    "MAX_CAPITAL_HOURS": {"usdHours": 5000,
                          "why": ("time in a position is a cost even "
                                  "when the position is flat. Capping "
                                  "it forces the exit engine to be "
                                  "measured rather than assumed")},
    "PAIRING_RULE": {
        "rule": "SAME_EVENT_COMPLEMENTARY_OUTCOME_VENUE_NATIVE_ID_MATCH",
        "why": ("deterministic venue-native identity only. No fuzzy "
                "title matching, no approximate team-name matching, no "
                "price matching"),
    },
    "EXIT_RULE": {
        "rule": "NOT_IDENTIFIED_UNTIL_THE_EXIT_ENGINE_IS_BUILT",
        "why": ("the exit rule is the output of the exit learning "
                "dataset and the ML exit interface, both downstream in "
                "the §11 sequence. Proposing a number here would freeze "
                "a guess as policy"),
    },
    "NO_TRADE_RULE": {
        "rule": ("NO_TRADE when the action set is empty, when the "
                 "economics are NOT_IDENTIFIED, or when a risk gate "
                 "refuses. NO_TRADE is a distinct outcome and is never "
                 "recorded as HOLD"),
    },
}

# EXIT_RULE is deliberately unresolved. The mandate cannot freeze until
# it is, and that ordering is the point: inventory that cannot be exited
# by a stated rule should not be created.
UNRESOLVED_PARAMETERS = ("EXIT_RULE",)

# ── THE EVIDENCE GATE, WHICH APPROVAL CANNOT SATISFY ─────────────────
#
# Owner directive, "STOP here" §6:
#
#     "I do not want OWNER_APPROVAL to become the only thing standing
#     between the current system and inventory creation while P_FILL =
#     zero rows, EXIT = zero rows, maker = nothing priced, allocator =
#     nothing ranked."
#
# So the freeze now takes TWO independent gates and the owner holds only
# one of them. A mandate approved while the engine has measured nothing
# would be a mandate about a system nobody has observed.

EVIDENCE_REQUIRED = (
    "P_FILL_DATASET_ROWS_ABOVE_ZERO",
    "P_FILL_IDENTIFIED_POSITIVES_ABOVE_ZERO",
    "P_FILL_IDENTIFIED_NEGATIVES_ABOVE_ZERO",
    "EXIT_LEARNING_DATASET_ROWS_ABOVE_ZERO",
    "MAKER_ENGINE_PRICES_AT_LEAST_ONE_ACTION",
    "CAPITAL_ALLOCATOR_RANKS_AT_LEAST_ONE_CANDIDATE",
)

WHY_EVIDENCE_IS_A_SEPARATE_GATE = (
    "owner approval is a judgement about a system. With zero P_FILL "
    "rows, zero exit rows, nothing priced and nothing ranked there is "
    "no system to judge -- approval would be consent to a description "
    "rather than to a measurement. The evidence gate cannot be "
    "satisfied by an approval token and the approval gate cannot be "
    "satisfied by evidence; both must clear on their own terms")


def evidence_status(*, p_fill_rows=0, p_fill_positives=0,
                    p_fill_negatives=0, exit_rows=0,
                    maker_actions_priced=0,
                    allocator_candidates_ranked=0) -> dict:
    """Has the engine measured enough to be worth writing a mandate for?

    Defaults are the current production values, so calling it with no
    arguments reports the state of the world rather than a blank form.
    """
    missing = []
    if p_fill_rows <= 0:
        missing.append("P_FILL_DATASET_ROWS_ABOVE_ZERO")
    if p_fill_positives <= 0:
        missing.append("P_FILL_IDENTIFIED_POSITIVES_ABOVE_ZERO")
    if p_fill_negatives <= 0:
        missing.append("P_FILL_IDENTIFIED_NEGATIVES_ABOVE_ZERO")
    if exit_rows <= 0:
        missing.append("EXIT_LEARNING_DATASET_ROWS_ABOVE_ZERO")
    if maker_actions_priced <= 0:
        missing.append("MAKER_ENGINE_PRICES_AT_LEAST_ONE_ACTION")
    if allocator_candidates_ranked <= 0:
        missing.append("CAPITAL_ALLOCATOR_RANKS_AT_LEAST_ONE_CANDIDATE")
    return {
        "EVIDENCE_SUFFICIENT": not missing,
        "required": list(EVIDENCE_REQUIRED),
        "missing": missing,
        "observed": {
            "P_FILL_DATASET_ROWS": p_fill_rows,
            "P_FILL_IDENTIFIED_POSITIVES": p_fill_positives,
            "P_FILL_IDENTIFIED_NEGATIVES": p_fill_negatives,
            "EXIT_LEARNING_DATASET_ROWS": exit_rows,
            "MAKER_ACTIONS_PRICED": maker_actions_priced,
            "ALLOCATOR_CANDIDATES_RANKED": allocator_candidates_ranked,
        },
        "whyASeparateGate": WHY_EVIDENCE_IS_A_SEPARATE_GATE,
    }

WHY_EXIT_RULE_BLOCKS_THE_FREEZE = (
    "the exit rule is the output of the exit learning dataset and the "
    "ML exit interface, both downstream in the §11 sequence. Freezing a "
    "guess would make the exit engine's job to justify the guess. "
    "Inventory that cannot be exited by a stated rule should not be "
    "created, so the mandate stays unfrozen until this resolves")


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


PROPOSAL_SHA = _sha(PROPOSED)


def proposal() -> dict:
    """The mandate as proposed, with every gap named."""
    missing = [p for p in REQUIRED_PARAMETERS if p not in PROPOSED]
    return {
        "version": MANDATE_VERSION,
        "MANDATE_STATUS": MANDATE_STATUS,
        "MANDATE_FROZEN": MANDATE_FROZEN,
        "MANDATE_ACTIVE": MANDATE_ACTIVE,
        "PROPOSAL_SHA": PROPOSAL_SHA,
        "requiredParameters": list(REQUIRED_PARAMETERS),
        "evidenceRequired": list(EVIDENCE_REQUIRED),
        "evidenceStatus": evidence_status(),
        "proposed": dict(PROPOSED),
        "parametersAbsent": missing,
        "parametersPresentButUnresolved": list(UNRESOLVED_PARAMETERS),
        "whyExitRuleBlocksTheFreeze": WHY_EXIT_RULE_BLOCKS_THE_FREEZE,
        "whyALabelIsNotAPermission": WHY_A_LABEL_IS_NOT_A_PERMISSION,
        "whyTheLimitsAreTheExperiment": WHY_THE_LIMITS_ARE_THE_EXPERIMENT,
        "chosenBeforeResults": CHOSEN_BEFORE_RESULTS,
        "BETTOR_EV_SHADOW_POSITIONS": BETTOR_EV_SHADOW_POSITIONS,
        "BETTOR_EV_REAL_ORDER_ACTIVITY": BETTOR_EV_REAL_ORDER_ACTIVITY,
        "BETTOR_EV_REAL_CAPITAL_AT_RISK": BETTOR_EV_REAL_CAPITAL_AT_RISK,
    }


def creating_actions_are_real_actions() -> dict:
    """The proposed creating actions must exist in the canonical set.

    A mandate that permits an action nobody implements permits nothing,
    and would look like a permission in a review.
    """
    canonical = set(acts.ACTIONS)
    named = list(PROPOSED["ACTIONS_THAT_MAY_CREATE_SHADOW_INVENTORY"])
    unknown = [a for a in named if a not in canonical]
    return {
        "named": named,
        "unknownToTheActionSet": unknown,
        "allExist": not unknown,
        "exposureEffect": {a: acts.EXPOSURE_EFFECT.get(a, NOT_IDENTIFIED)
                           for a in named if a in canonical},
    }


def freeze(*, owner_approval_token=None, parameters=None,
           evidence=None) -> dict:
    """Freeze the mandate. REFUSES without BOTH evidence and approval.

    Fails closed on four separate conditions, each named, so a refusal
    says which one rather than reading as a generic error. The evidence
    gate is not satisfiable by an approval token: see §6.
    """
    blockers = []
    ev = evidence if evidence is not None else evidence_status()
    if not ev.get("EVIDENCE_SUFFICIENT"):
        blockers.append("EVIDENCE_INSUFFICIENT")
    if not owner_approval_token:
        blockers.append("OWNER_APPROVAL_ABSENT")
    params = dict(parameters or PROPOSED)
    missing = [p for p in REQUIRED_PARAMETERS if p not in params]
    if missing:
        blockers.append("REQUIRED_PARAMETERS_ABSENT")
    unresolved = [p for p in UNRESOLVED_PARAMETERS
                  if str(params.get(p, {}).get("rule", "")).startswith(
                      "NOT_IDENTIFIED")]
    if unresolved:
        blockers.append("PARAMETERS_UNRESOLVED")
    if blockers:
        return {
            "frozen": False,
            "MANDATE_STATUS": MANDATE_STATUS,
            "blockers": blockers,
            "parametersAbsent": missing,
            "parametersUnresolved": unresolved,
            "evidence": ev,
            "whyExitRuleBlocksTheFreeze": WHY_EXIT_RULE_BLOCKS_THE_FREEZE,
            "whyEvidenceIsASeparateGate": WHY_EVIDENCE_IS_A_SEPARATE_GATE,
            "chosenBeforeResults": CHOSEN_BEFORE_RESULTS,
        }
    return {
        "frozen": True,
        "MANDATE_STATUS": "FROZEN",
        "MANDATE_SHA": _sha(params),
        "evidence": ev,
        "frozenAt": "SET_BY_CALLER",
        "parameters": params,
        "chosenBeforeResults": CHOSEN_BEFORE_RESULTS,
    }


def may_create_inventory(*, label=None, mandate=None) -> dict:
    """May a labelled quote become a shadow position? Today: no.

    TWO GATES AND THE MANDATE IS THE FIRST. A supported fill label does
    not reach the second gate while the mandate is unfrozen, which is
    the §12 requirement expressed as a code path rather than a note.
    """
    m = mandate or freeze()
    blockers = []
    if not m.get("frozen"):
        blockers.append("MANDATE_NOT_FROZEN")
        blockers.extend(m.get("blockers", []))
    if not MANDATE_ACTIVE:
        blockers.append("MANDATE_NOT_ACTIVE")
    if label is not None and label != "COUNTERFACTUAL_FILL_SUPPORTED":
        blockers.append("LABEL_DOES_NOT_SUPPORT_A_FILL")
    return {
        "mayCreate": False if blockers else True,
        "blockers": blockers,
        "label": label if label is not None else NOT_IDENTIFIED,
        "whyALabelIsNotAPermission": WHY_A_LABEL_IS_NOT_A_PERMISSION,
        "BETTOR_EV_SHADOW_POSITIONS": BETTOR_EV_SHADOW_POSITIONS,
        "BETTOR_EV_REAL_ORDER_ACTIVITY": BETTOR_EV_REAL_ORDER_ACTIVITY,
        "BETTOR_EV_REAL_CAPITAL_AT_RISK": BETTOR_EV_REAL_CAPITAL_AT_RISK,
    }


def describe() -> dict:
    return {
        **proposal(),
        "creatingActions": creating_actions_are_real_actions(),
        "whyEvidenceIsASeparateGate": WHY_EVIDENCE_IS_A_SEPARATE_GATE,
        "freezeRefusal": freeze(),
        "mayCreateInventory": may_create_inventory(
            label="COUNTERFACTUAL_FILL_SUPPORTED"),
    }
