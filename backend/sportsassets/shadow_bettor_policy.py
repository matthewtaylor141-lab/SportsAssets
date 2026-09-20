"""THE FROZEN BETTOR_EV_SHADOW POLICY. BETTOR's own, not RN1's.

Owner approval 2026-09-19: "Create and freeze the canonical
BETTOR_EV_SHADOW_V1 policy ... This must be BETTOR's own policy
declaration -- do not reuse, alias, copy, or satisfy it with
RN1_SHADOW_V1."

WHY THIS FILE EXISTS AT ALL, AND WHY IT IS LATE. Migration 070 made
shadow_decisions.policy_version a foreign key into
shadow_policy_versions -- the directive's "freeze the policy before
row 1", enforced by the database rather than by intention. RN1_SHADOW_V1
was frozen at 17:53:05Z. BETTOR_EV_SHADOW_V1 never was, so every BETTOR
decision was refused by that constraint, 99 times, until the watchdog
named it. The constraint did exactly its job; what was missing was this
declaration.

WHY A SEPARATE MODULE FROM shadow_policy.py. RN1's POLICY_CODE_SHA
hashes the bytes of shadow_policy.py. Adding BETTOR's declaration there
would move RN1's recorded code hash for a reason that has nothing to do
with RN1's rules, and the owner's instruction is not to alter the RN1
system as part of this repair. Two lanes, two declarations, two files.

WHAT IS HONESTLY NOT ESTABLISHED STAYS NOT ESTABLISHED. This lane has no
fair-value model, no fill model, no size, no position and therefore no
pairing, no cash-out and no execution to reconstruct. The declaration
says so in those words rather than inventing a rule to look complete.
"Anything genuinely not established must remain NOT_ESTABLISHED,
NOT_IDENTIFIED, or NOT_APPLICABLE; do not manufacture values simply to
populate the policy."

THE ACTION SET IS [NO_TRADE], AND THAT IS THE POINT. It is not a
placeholder for a richer set arriving later -- it is the complete set of
actions this policy can emit today, because no independently validated
Action EV exists. When one does, the version changes and the rows
written under this one keep meaning what they meant.
"""

from __future__ import annotations

import hashlib
import json
import os

from . import bettor_ev_actions as evacts
from . import shadow as sh
from . import shadow_bettor as bettor
from . import shadow_bettor_codesha as codesha
from . import shadow_lanes as lanes

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_ESTABLISHED = lanes.NOT_ESTABLISHED
NOT_APPLICABLE = "NOT_APPLICABLE"

BETTOR_POLICY_VERSION = bettor.POLICY_VERSION          # BETTOR_EV_SHADOW_V1

# ── the component rule versions ──────────────────────────────────────
#
# shadow_policy_versions requires each of these NOT NULL, so each is a
# STRING and each string is true. A lane that never sizes, never holds
# and never executes has no sizing, pairing, cash-out or execution
# reconstruction rule; saying NOT_APPLICABLE is the accurate answer, and
# inventing "SIZING_V1" here would put a rule in the frozen record that
# no code implements.

SIZING_POLICY_VERSION = NOT_APPLICABLE
PAIRING_RULE_VERSION = NOT_APPLICABLE
CASHOUT_RULE_VERSION = NOT_APPLICABLE
EXECUTION_RECONSTRUCTION_VERSION = NOT_APPLICABLE
# Latency fields are CARRIED on the opportunity row, but no latency
# POLICY has been established for this lane -- nothing yet decides
# anything on the basis of them.
LATENCY_POLICY_VERSION = NOT_ESTABLISHED

# The only action this policy can PRODUCE today. Unchanged by V3: the
# lane still cannot emit a BUY or a SELL.
ACTION_SET = [sh.NO_TRADE]

# What V3 EVALUATES before producing it, which is the part that
# changed. The two lists are deliberately separate: an engine that
# considered one action and an engine that considered fifteen both
# produce NO_TRADE, and only this distinction tells them apart in the
# ledger.
CONSIDERED_ACTION_SET = list(evacts.ACTIONS)

# ── the declaration ──────────────────────────────────────────────────

UNIVERSE = {
    "universeVersion": bettor.UNIVERSE_VERSION,
    "source": bettor.UNIVERSE_SOURCE_PREMAP,
    "selectionReason": bettor.SELECTION_ROUND_ROBIN,
    # The dataset is NOT conditioned on what BETTOR already believes:
    # there is no profitability filter on admission, which is what makes
    # the refusals worth analysing later.
    "conditionedOnExpectedProfitability": False,
    "cadenceSeconds": 300,
}

BELIEF = {
    # The lane's whole honest position today.
    "pBettor": NOT_ESTABLISHED,
    "pBettorStatus": NOT_ESTABLISHED,
    "informationEv": NOT_ESTABLISHED,
    "pFill": NOT_IDENTIFIED,
    "pFillStatus": NOT_IDENTIFIED,
    "actionEv": NOT_ESTABLISHED,
    "fairValueModel": NOT_ESTABLISHED,
    "modelVersion": bettor.MODEL_VERSION,
    # A decision may not be produced from a belief that does not exist.
    "decisionRequiresEstablishedActionEv": True,
}

INDEPENDENCE = {
    # "BETTOR_EV_SHADOW may not use: RN1 action, RN1 identity, RN1
    # mirror decision, RN1 future action, RN1-derived target as
    # independent features." Frozen here so it is part of the record and
    # not merely a habit of the code.
    "rn1FeaturesUsed": False,
    "forbiddenFeatureClasses": ["RN1_ACTION", "RN1_ACCOUNT_IDENTITY",
                                "RN1_MIRROR_DECISION", "RN1_FUTURE_ACTION",
                                "RN1_DERIVED_TARGET"],
    "allowedProvenances": sorted(lanes.INDEPENDENT_PROVENANCES),
    "lineageDeclaredPerFeature": True,
    "enforcedBy": ["assert_lineage",
                   "bettor_opportunity_independent CHECK",
                   "writer refuses RN1 tables by name"],
}

DECISION_SEMANTICS = {
    "actionSet": ACTION_SET,
    "currentEligibleAction": sh.NO_TRADE,
    # "Do not consider NO_TRADE a failure."
    "noTradeIsADecision": True,
    "noTradeRetained": True,
    "everyDecisionCarriesItsBlockers": True,
    "blockerVocabulary": list(bettor.BLOCKERS),
    "firstBlockerIsTheTrueOne": True,
    "unreadableBookIsANamedBlockerNotASkip": True,
    "depthAbsentFromBboIsReportedNotInvented": True,
    "everyDecisionDescendsFromAnObservedOpportunity": True,
    "evidenceSourceTravelsWithEveryDecision": True,
}

SAFETY = {
    "shadowMode": sh.SHADOW_MODE,
    "realOrderSubmissionEnabled": sh.REAL_ORDER_SUBMISSION_ENABLED,
    "capitalAtRisk": sh.CAPITAL_AT_RISK,
    "mirrorLive": False,
    "orderPathPresent": False,
    "disclosure": sh.DISCLOSURE,
}

DECLARATION = {
    "lane": lanes.BETTOR_EV_SHADOW,
    "policyVersion": BETTOR_POLICY_VERSION,
    "role": "PRIMARY — INDEPENDENT INTELLIGENCE",
    "universe": UNIVERSE,
    "belief": BELIEF,
    "independence": INDEPENDENCE,
    "decisionSemantics": DECISION_SEMANTICS,
    "safety": SAFETY,
    # The four that do not exist for this lane, named rather than absent.
    "sizing": NOT_APPLICABLE,
    "sizingPolicyVersion": SIZING_POLICY_VERSION,
    "pairing": NOT_APPLICABLE,
    "pairingRuleVersion": PAIRING_RULE_VERSION,
    "cashout": NOT_APPLICABLE,
    "cashoutRuleVersion": CASHOUT_RULE_VERSION,
    "executionReconstruction": NOT_APPLICABLE,
    "executionReconstructionVersion": EXECUTION_RECONSTRUCTION_VERSION,
    # WHICH CODE BOUNDARY THIS VERSION IS GATED ON. Part of the
    # declaration, so the rule that decides whether decisions may be
    # written is itself frozen rather than merely current practice.
    "codeBoundary": codesha.BOUNDARY_VERSION,
    "codeShaEnforced": True,
    # WHAT V5 WEIGHS BEFORE REFUSING. Frozen in the declaration so the
    # breadth of the comparison is part of the policy rather than a
    # property of whatever the code happened to loop over that day.
    "consideredActionSet": CONSIDERED_ACTION_SET,
    "actionEvEngine": "research/beta48/shadow/action_ev.py",
    "actionEvEngineIsImportedNotCopied": True,
    "supersedes": "BETTOR_EV_SHADOW_V4",
    "latencyPolicy": NOT_ESTABLISHED,
    "latencyPolicyVersion": LATENCY_POLICY_VERSION,
    # Scoring belongs to outcomes, and this lane has produced no action
    # whose outcome could be scored.
    "scoring": NOT_APPLICABLE,
}


def canonical(declaration: dict | None = None) -> str:
    """The declaration as ONE canonical string, so the hash depends on
    the rules and not on how the dict was laid out in source."""
    return json.dumps(declaration if declaration is not None else DECLARATION,
                      sort_keys=True, separators=(",", ":"), default=str)


def policy_sha(declaration: dict | None = None) -> str:
    return hashlib.sha256(canonical(declaration).encode()).hexdigest()


# BETTOR's OWN implementing modules, in fixed order. shadow_policy.py is
# deliberately absent: RN1's rule file implements nothing on this lane.
# ── V1's boundary, kept EXACTLY as it was ────────────────────────────
#
# "Do not change V1's historical hash methodology." This function still
# hashes whole file bytes, so 34fbb4ab992cf2a3... remains reproducible
# from the code frozen at 19:33 on 2026-09-19. Nothing calls it for
# freezing any more; it exists so V1's recorded number stays checkable.
#
# ITS TWO DEFECTS, for the record, both fixed in V2's boundary and
# NEITHER applied retroactively: bytes include comments (15 of the 16
# lines that drifted it were comments), and two of these four files are
# shared with RN1, so RN1's own edits would move BETTOR's hash.

CODE_FILES_V1 = ("shadow.py", "shadow_lanes.py", "shadow_bettor.py",
                 "shadow_bettor_policy.py")

# The value recorded in production for V1, immutable.
V1_FROZEN_POLICY_SHA = (
    "6db08437ceed0dc82fb491d23e9da0a534ef255422f045a2d3345aa0b6b48101")
V1_FROZEN_POLICY_CODE_SHA = (
    "34fbb4ab992cf2a395c35a2b3b8bda89998a02096fb57d719ea836b68a234ad1")


def policy_code_sha_v1() -> str:
    """V1's boundary: whole file bytes, or NOT_IDENTIFIED.

    Never a partial hash: a digest over four files that silently became
    a digest over three would compare unequal for a reason nobody could
    reconstruct later.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    digest = hashlib.sha256()
    for name in CODE_FILES_V1:
        try:
            with open(os.path.join(here, name), "rb") as fh:
                digest.update(fh.read())
        except OSError:
            return NOT_IDENTIFIED
    return digest.hexdigest()


# ── V2's boundary: the parsed decision path ──────────────────────────
#
# Owner directive 2026-09-19 20:2xZ. Defined in shadow_bettor_codesha
# and pinned there by mutation tests: a comment or whitespace edit
# leaves it unchanged, while a change to the action, a blocker,
# pBettorStatus, pFillStatus or the lineage moves it.
#
# shadow_bettor_policy.py is deliberately NOT in that boundary. This
# file IS the declaration, and the declaration is already covered by
# POLICY_SHA; hashing it twice would make a prose edit here block
# decision writing for no reason.

POLICY_SHA = policy_sha()
POLICY_CODE_SHA = codesha.semantic_code_sha()
CODE_BOUNDARY = codesha.BOUNDARY_VERSION


def frozen_policy() -> dict:
    """Everything the store writes into BETTOR's shadow_policy_versions
    row. Shaped like RN1's because the store takes one shape -- but every
    value is this lane's own."""
    return {
        "policyVersion": BETTOR_POLICY_VERSION,
        "policySha": POLICY_SHA,
        "policyCodeSha": POLICY_CODE_SHA,
        "lane": lanes.BETTOR_EV_SHADOW,
        "actionSet": ACTION_SET,
        "pairingRuleVersion": PAIRING_RULE_VERSION,
        "cashoutRuleVersion": CASHOUT_RULE_VERSION,
        "latencyPolicyVersion": LATENCY_POLICY_VERSION,
        "executionReconstructionVersion": EXECUTION_RECONSTRUCTION_VERSION,
        "sizingPolicyVersion": SIZING_POLICY_VERSION,
        "declaration": DECLARATION,
    }
