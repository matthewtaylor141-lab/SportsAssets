"""THE FROZEN RN1_SHADOW POLICY. Declared before row 1, never after.

Owner directive 2026-09-19: "FREEZE THE POLICY VERSION BEFORE ROW 1."

WHY A FREEZE IS NOT BUREAUCRACY HERE. A prospective ledger's whole claim
is that every row was produced by the same brain under the same rules,
so that two decisions a week apart are comparable and a later reader can
say what the system believed. If the rules can drift silently, the
ledger records a sequence of different systems wearing one name, and no
amount of later analysis can separate them -- the information was never
written down. So the rule set is DECLARED here, HASHED, and written once
into shadow_policy_versions; shadow_decisions.policy_version carries a
foreign key into that table, which means a decision produced under an
unfrozen policy cannot physically be written.

TWO HASHES, BECAUSE THERE ARE TWO WAYS TO DRIFT.

  POLICY_SHA      hashes the DECLARATION below -- the rule set as
                  stated. Changing a rule changes this hash, and a
                  changed hash under the same version name is refused
                  by the store. This is the load-bearing one.

  POLICY_CODE_SHA hashes the BYTES of the modules that implement the
                  rules. It catches the other drift: an edit to
                  shadow.py's reconstruction that leaves the
                  declaration untouched. It is RECORDED, not enforced,
                  and the reason is honest rather than lazy -- a typo
                  fix in a comment moves it, and a policy freeze that
                  breaks on a comment would be abandoned within a week
                  and then it would protect nothing. The store reports
                  whether it still matches; a mismatch is a question to
                  answer, not a halt.

  A hash we could not compute is the string NOT_IDENTIFIED. It is never
  a zero, never an empty string and never a plausible-looking digest.

THE SIZING POLICY IS DECLARED HERE AND NOT READ FROM THE LIVE MIRROR.
The live mirror's ratio and clip are operator knobs that have moved
several times by owner order, correctly -- that is what a live system's
knobs are for. A frozen shadow policy that READ those knobs would move
with them, which is precisely the drift this file exists to prevent. The
numbers below are a deliberate copy, pinned at their 2026-09-19 values,
and they change only by a new policy version.
"""

from __future__ import annotations

import hashlib
import json
import os

from . import shadow as sh
from . import shadow_lanes as lanes

NOT_IDENTIFIED = "NOT_IDENTIFIED"

RN1_SHADOW_POLICY_VERSION = "RN1_SHADOW_V1"

# ── the component rule versions the directive names by name ──────────

PAIRING_RULE_VERSION = "PAIR_V1"
CASHOUT_RULE_VERSION = "CASHOUT_V1"
LATENCY_POLICY_VERSION = "LATENCY_V1"
EXECUTION_RECONSTRUCTION_VERSION = "EXEC_RECON_V1"
SIZING_POLICY_VERSION = "SIZING_V1"

# ── the declaration ──────────────────────────────────────────────────
#
# Every value here is a STATEMENT OF RULE, not a tuning parameter. If
# one of them needs to change, the version above changes with it and the
# rows written under the old version keep meaning what they meant.

SIZING = {
    "basis": "PROPORTIONAL_TO_RN1_QUANTITY",
    "ratio": 0.10,
    "clipUsd": 2500.0,
    # NEVER more than the depth we actually observed. "Do not
    # interpolate a profitable fill" begins at the size.
    "depthCap": "NEVER_EXCEED_OBSERVED_DEPTH",
    "wholeShareFloor": True,
    # RN1's own price is not a size input and not a price input; it is
    # recorded beside ours and never substituted for it.
    "rn1PriceIsNotOurExecutablePrice": True,
    "pinnedFrom": "live mirror knobs as at 2026-09-19, copied not read",
}

LATENCY = {
    "components": ["dataLatencyMs", "decisionComputeMs", "executionLatencyMs"],
    # "Never use one blended latency number when its components are
    # known." A total exists only when every component does.
    "totalOnlyWhenAllComponentsKnown": True,
    "scenarioGridMs": list(sh.LATENCY_SCENARIOS_MS),
    "basisValues": ["OBSERVED", "SCENARIO", "NOT_IDENTIFIED"],
    "scenarioIsNotAMeasurement": True,
}

EXECUTION_RECONSTRUCTION = {
    "marketable": "WALK_OBSERVED_DEPTH_AT_ARRIVAL_BOOK",
    "marketableNeverExceedsObservedDepth": True,
    "passiveTracks": [sh.PASSIVE_COUNTERFACTUAL_MARKOUT,
                      sh.PASSIVE_QUEUE_MODEL_ESTIMATE],
    "realizableClasses": sorted(sh.REALIZABLE_CLASSES),
    # The four refusals, carried in the policy so they are part of what
    # was frozen rather than a habit of the code that reads it.
    "aPriceTouchedIsNotAFill": True,
    "simulatedAndObservedNeverSum": True,
    "decisionDoesNotExecuteAtItsOwnBook": True,
    "yesAndNoAreDifferentPositions": True,
}

PAIRING = {
    "basis": "COMPLEMENT_COMPLETION_TO_ONE",
    "pairBasis": 1.0,
    "measures": ["legShares", "legAvgCost", "complementPrice",
                 "combinedCost", "lockedResult", "unpairedRemainder"],
}

CASHOUT = {
    # "Do not assume CASH_OUT is good because it reduces risk."
    "comparedAgainst": ["CASH_OUT", "HOLD_TO_SETTLEMENT", "PAIR", "REDUCE"],
    "riskReductionIsNotAJustification": True,
    "uncertaintyRequired": True,
}

SCORING = {
    "horizons": ["30S", "60S", "300S", "SETTLEMENT"],
    # 5S stays dead. It was removed because the source cannot resolve
    # it, and the reason has not changed.
    "fiveSecondHorizon": "NOT_OBSERVABLE_FROM_THIS_SOURCE",
    "observabilityCheckedPerRow": True,
}

DECLARATION = {
    "lane": lanes.RN1_SHADOW,
    "policyVersion": RN1_SHADOW_POLICY_VERSION,
    "shadowMode": sh.SHADOW_MODE,
    "realOrderSubmissionEnabled": sh.REAL_ORDER_SUBMISSION_ENABLED,
    "capitalAtRisk": sh.CAPITAL_AT_RISK,
    "mirrorLive": False,
    "actionSet": list(sh.ACTIONS),
    # RN1_SHADOW is a MECHANISM benchmark. It holds no belief, and the
    # policy says so rather than leaving it to a NULL to imply.
    "pBettor": lanes.NOT_ESTABLISHED,
    "informationEv": lanes.NOT_ESTABLISHED,
    "fairValueManufacturedFromRn1": False,
    "sizing": SIZING,
    "sizingPolicyVersion": SIZING_POLICY_VERSION,
    "latency": LATENCY,
    "latencyPolicyVersion": LATENCY_POLICY_VERSION,
    "executionReconstruction": EXECUTION_RECONSTRUCTION,
    "executionReconstructionVersion": EXECUTION_RECONSTRUCTION_VERSION,
    "pairing": PAIRING,
    "pairingRuleVersion": PAIRING_RULE_VERSION,
    "cashout": CASHOUT,
    "cashoutRuleVersion": CASHOUT_RULE_VERSION,
    "scoring": SCORING,
    "noTradeRetained": True,
    "disclosure": sh.DISCLOSURE,
}


def canonical(declaration: dict | None = None) -> str:
    """The declaration as ONE canonical string. Sorted keys, no
    incidental whitespace -- so the hash depends on the rules and not on
    how the dict happened to be laid out in source."""
    return json.dumps(declaration if declaration is not None else DECLARATION,
                      sort_keys=True, separators=(",", ":"), default=str)


def policy_sha(declaration: dict | None = None) -> str:
    return hashlib.sha256(canonical(declaration).encode()).hexdigest()


# The modules whose bytes constitute the implementation. Order is fixed:
# a hash that depends on directory listing order is not a hash.
CODE_FILES = ("shadow.py", "shadow_lanes.py", "shadow_policy.py")


def policy_code_sha() -> str:
    """Hash the implementing modules' bytes, or say NOT_IDENTIFIED.

    Never a partial hash: if any one file cannot be read, the answer is
    that we do not know, because a digest over three files that silently
    became a digest over two would compare unequal for a reason nobody
    could reconstruct.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    digest = hashlib.sha256()
    for name in CODE_FILES:
        try:
            with open(os.path.join(here, name), "rb") as fh:
                digest.update(fh.read())
        except OSError:
            return NOT_IDENTIFIED
    return digest.hexdigest()


POLICY_SHA = policy_sha()
POLICY_CODE_SHA = policy_code_sha()

ACTION_SET = list(sh.ACTIONS)


def frozen_policy() -> dict:
    """Everything the store writes into one shadow_policy_versions row."""
    return {
        "policyVersion": RN1_SHADOW_POLICY_VERSION,
        "policySha": POLICY_SHA,
        "policyCodeSha": POLICY_CODE_SHA,
        "lane": lanes.RN1_SHADOW,
        "actionSet": ACTION_SET,
        "pairingRuleVersion": PAIRING_RULE_VERSION,
        "cashoutRuleVersion": CASHOUT_RULE_VERSION,
        "latencyPolicyVersion": LATENCY_POLICY_VERSION,
        "executionReconstructionVersion": EXECUTION_RECONSTRUCTION_VERSION,
        "sizingPolicyVersion": SIZING_POLICY_VERSION,
        "declaration": DECLARATION,
    }


def shadow_size(rn1_quantity, observed_depth_shares=None,
                price=None) -> dict:
    """The frozen sizing rule, applied.

    Returns the size and WHY it is that size. A size with no reason is
    unauditable, and the reason is the part a later reader needs when
    the shadow fill looks surprising.

    NEVER MORE THAN OBSERVED DEPTH. When depth is not established the
    answer is not "as much as we like" -- it is a refusal to state a
    size, recorded as NOT_IDENTIFIED with the reason attached.
    """
    reasons = []
    try:
        raw = float(rn1_quantity) * float(SIZING["ratio"])
    except (TypeError, ValueError):
        return {"shares": None, "status": NOT_IDENTIFIED,
                "why": "RN1 quantity is not a number",
                "reasons": ["RN1_QUANTITY_NOT_A_NUMBER"]}
    if raw <= 0:
        return {"shares": None, "status": NOT_IDENTIFIED,
                "why": "proportional size is not positive",
                "reasons": ["SIZE_NOT_POSITIVE"]}
    reasons.append("PROPORTIONAL_%.2f" % SIZING["ratio"])
    size = raw

    if price is not None:
        try:
            clip_shares = float(SIZING["clipUsd"]) / float(price)
        except (TypeError, ValueError, ZeroDivisionError):
            clip_shares = None
        if clip_shares is not None and size > clip_shares:
            size = clip_shares
            reasons.append("CLIPPED_TO_%gUSD" % SIZING["clipUsd"])

    if observed_depth_shares is None:
        return {"shares": None, "status": NOT_IDENTIFIED,
                "why": ("observed depth is not established, and the frozen "
                        "policy refuses a size it cannot cap"),
                "reasons": reasons + ["DEPTH_NOT_ESTABLISHED"]}
    try:
        depth = float(observed_depth_shares)
    except (TypeError, ValueError):
        return {"shares": None, "status": NOT_IDENTIFIED,
                "why": "observed depth is not a number",
                "reasons": reasons + ["DEPTH_NOT_A_NUMBER"]}
    if size > depth:
        size = depth
        reasons.append("CAPPED_AT_OBSERVED_DEPTH")

    if SIZING["wholeShareFloor"]:
        size = float(int(size))
        reasons.append("WHOLE_SHARE_FLOOR")
    if size <= 0:
        return {"shares": 0.0, "status": "NO_SIZE",
                "why": "the capped size floors to zero shares",
                "reasons": reasons}
    return {"shares": size, "status": "SIZED", "why": None,
            "reasons": reasons}
