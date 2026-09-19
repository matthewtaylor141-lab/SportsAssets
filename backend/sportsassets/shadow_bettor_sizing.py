"""THE FROZEN $1,000 SIZING STANDARD, declared before the first entry.

Owner directive 2026-09-19: "FREEZE STANDARD INTENDED TRADE SIZE.
Before the first eligible BETTOR shadow trade:
STANDARD_BETTOR_SHADOW_NOTIONAL_USD = 1000."

FROZEN BEFORE ROW ONE IS THE WHOLE POINT. A sizing rule chosen after
seeing results is a rule chosen to flatter them. There are zero eligible
BETTOR entries today -- every post-freeze decision is NO_TRADE -- so this
declaration is being made at the only moment when it cannot be
retrofitted.

INTENDED IS NOT EXECUTED, AND THE DISTINCTION IS LOAD-BEARING.

    INTENDED_NOTIONAL          1000
    EXECUTED_ENTRY_NOTIONAL     650   <- what the book actually supported
    UNFILLED_NOTIONAL           350

"Only $650 enters economic P&L accounting. Never invent liquidity to
reach $1,000." A system that reported $1,000 played whenever it wanted
$1,000 would be reporting its own intentions as results, and every
return computed on that denominator would be wrong in the flattering
direction.

WHY THIS IS A SEPARATE VERSION FROM BETTOR_EV_SHADOW_V1. The EV policy
decides WHETHER to act; this decides HOW MUCH once something is already
eligible. They change for different reasons and on different clocks, and
the directive asks for the $1,000 cohort to survive later sizing changes
as a benchmark -- which it cannot do if it is welded to a policy version
that will move when the EV model does. Migration 074's header sets out
the full reasoning, including why editing the frozen EV declaration
would be refused by the database.

THIS DOES NOT MAKE BETTOR TRADE. "The $1,000 assumption determines
sizing. It does NOT determine whether BETTOR trades. BETTOR still must
earn the trade through its EV gates." Nothing in this module is
consulted until an eligible entry exists, and today none can: the frozen
action set is exactly [NO_TRADE].
"""

from __future__ import annotations

import hashlib
import json

from . import shadow_lanes as lanes

NOT_APPLICABLE = "NOT_APPLICABLE"

SIZING_POLICY_VERSION = "BETTOR_SHADOW_SIZING_V1"

# The management-facing name of the standardized strategy view.
# "Because management specifically wants approximately $1,000 per trade,
# maintain a standardized strategy view: BETTOR_$1000_STANDARD."
COHORT = "BETTOR_$1000_STANDARD"

STANDARD_BETTOR_SHADOW_NOTIONAL_USD = 1000

# The executed legs that change a position and therefore count toward
# turnover. The directive names them: "entries + exits + cash-outs +
# pair/complement executions + other executed position-changing
# actions." Migration 074 carries the same list as a CHECK.
ENTRY = "ENTRY"
EXIT = "EXIT"
CASHOUT = "CASHOUT"
PAIR = "PAIR"
COMPLEMENT = "COMPLEMENT"
SETTLEMENT = "SETTLEMENT"
OTHER_POSITION_CHANGING = "OTHER_POSITION_CHANGING"

LEG_KINDS = (ENTRY, EXIT, CASHOUT, PAIR, COMPLEMENT, SETTLEMENT,
             OTHER_POSITION_CHANGING)

# ENTRY_NOTIONAL_PLAYED counts entries. GROSS_TRADING_TURNOVER counts
# every one of them. Two names because they answer two questions, and
# collapsing them is the first thing the directive forbids.
TURNOVER_KINDS = LEG_KINDS
ENTRY_KINDS = (ENTRY,)


DECLARATION = {
    "sizingPolicyVersion": SIZING_POLICY_VERSION,
    "cohort": COHORT,
    "lane": lanes.BETTOR_EV_SHADOW,
    "standardNotionalUsd": STANDARD_BETTOR_SHADOW_NOTIONAL_USD,
    "currency": "USD",
    "basis": "INTENDED_NOTIONAL",
    # The three facts every eligible entry must record separately.
    "records": ["INTENDED_NOTIONAL", "EXECUTED_ENTRY_NOTIONAL",
                "UNFILLED_NOTIONAL"],
    "executedMayNotExceedIntended": True,
    "liquidityMayNotBeInvented": True,
    "onlyExecutedNotionalEntersPnl": True,
    # Sizing does not confer eligibility.
    "confersEligibility": False,
    "eligibilityRemainsWith": "BETTOR_EV_SHADOW_V1",
    # Scaling rules that do not exist are named, not invented.
    "scalingRule": NOT_APPLICABLE,
    "leverage": NOT_APPLICABLE,
    "proceedsRecycling": NOT_APPLICABLE,
    "shadowMode": True,
    "realOrderSubmissionEnabled": False,
    "capitalAtRisk": 0,
}


def canonical(declaration: dict | None = None) -> str:
    return json.dumps(declaration if declaration is not None else DECLARATION,
                      sort_keys=True, separators=(",", ":"), default=str)


def policy_sha(declaration: dict | None = None) -> str:
    return hashlib.sha256(canonical(declaration).encode()).hexdigest()


POLICY_SHA = policy_sha()


def frozen_policy() -> dict:
    """The row written into bettor_sizing_policies, once."""
    return {
        "sizingPolicyVersion": SIZING_POLICY_VERSION,
        "cohort": COHORT,
        "lane": lanes.BETTOR_EV_SHADOW,
        "standardNotionalUsd": STANDARD_BETTOR_SHADOW_NOTIONAL_USD,
        "policySha": POLICY_SHA,
        "declaration": DECLARATION,
    }


def size(executable_notional_usd=None) -> dict:
    """Intended, executed and unfilled for one eligible entry.

    The only sizing arithmetic in the system, in one place, so no caller
    can round the executable book up to the intended figure.

    `executable_notional_usd` is what the LATENCY-ADJUSTED executable
    book supports. None means nobody has established it -- and that is
    NOT zero and not $1,000. An entry may not be sized against a book
    that was never read, so this refuses rather than guessing.
    """
    intended = float(STANDARD_BETTOR_SHADOW_NOTIONAL_USD)
    if executable_notional_usd is None:
        return {
            "sizingPolicyVersion": SIZING_POLICY_VERSION,
            "intendedNotionalUsd": intended,
            "executedEntryNotionalUsd": None,
            "unfilledNotionalUsd": None,
            "status": "EXECUTABLE_LIQUIDITY_NOT_IDENTIFIED",
            "why": ("the latency-adjusted executable book was not read, "
                    "so executed notional is not identified; it is "
                    "neither zero nor the intended $%d" % intended),
        }
    executable = float(executable_notional_usd)
    if executable < 0:
        raise ValueError("refused: negative executable notional")
    # NEVER INVENT LIQUIDITY TO REACH $1,000. The min is the whole rule.
    executed = min(intended, executable)
    return {
        "sizingPolicyVersion": SIZING_POLICY_VERSION,
        "intendedNotionalUsd": intended,
        "executedEntryNotionalUsd": executed,
        "unfilledNotionalUsd": round(intended - executed, 6),
        "status": ("FULLY_SUPPORTED" if executed >= intended
                   else "LIQUIDITY_LIMITED"),
        "why": ("the executable book supported $%.2f of the intended "
                "$%.2f" % (executed, intended)),
    }
