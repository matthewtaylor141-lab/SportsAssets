"""THE SINGLE AUDITABLE BETTOR ACTION TABLE.

Owner directive, "RETURN TO THE ACTUAL BETTOR EV ENGINE" §1 and §23.

WHY THIS FILE HAD TO BE WRITTEN, AND WHY IT IS NOT A NEW ENGINE.

The repository already contains three action vocabularies, all tested,
all in use, and NONE of them the same set:

    research/beta48/shadow/ev_core.py       9 actions
        MAKE TAKE PAIR HOLD HEDGE PASSIVE_EXIT AGGRESSIVE_EXIT SETTLE
        NO_TRADE

    research/beta48/shadow/action_ev.py    15 actions
        NO_TRADE POST_BID POST_ASK IMPROVE_BID IMPROVE_ASK HOLD PAIR
        HEDGE PASSIVE_EXIT AGGRESSIVE_EXIT SETTLE MAKER_QUOTE
        TAKER_CROSS CANCEL NO_ACTION

    research/beta48/shadow/position_state.py  10 actions
        A_PASSIVE_COMPLEMENT_PAIR A_AGGRESSIVE_COMPLEMENT_PAIR A_WAIT
        A_PASSIVE_SELL_EXIT A_AGGRESSIVE_SELL_EXIT A_HEDGE
        A_DIRECTIONAL_HOLD A_SETTLEMENT_HOLD A_REALIZE_AND_RECYCLE
        A_HOLD_LOCKED_PAIR

Three vocabularies means three answers to "what did BETTOR consider?"
and no way to audit across them. §23 asks for ONE table. This module is
that table and nothing else: it defines the canonical set, states which
leg each action touches, and maps every canonical action onto the
research vocabularies that already implement it. It computes no EV. The
EV stays in the research modules, which are tested, and which this file
deliberately does not duplicate -- §0: "Do not create duplicate engines
because an existing module is imperfect."

THE MAPPING IS TOTAL AND ITS GAPS ARE NAMED. Every canonical action
resolves either to a research action or to NOT_REPRESENTED. A gap is
recorded as a gap. It is never silently dropped and never quietly
aliased onto the nearest similar-sounding name, because an action that
was never evaluated must not appear in the audit as one that was
evaluated and rejected.

PER-LEG, NEVER NET (§7). Each action declares the leg it touches. YES
and NO on one condition are complements, and an engine that nets them
reports a matched pair and a genuinely flat book identically -- losing
the locked P&L, the capital earning it, and the fact that one of the
two needs managing. `inventory_state.py` already refuses to net; this
table keeps the same discipline at the action layer.
"""

from __future__ import annotations

# ── legs (§7) ────────────────────────────────────────────────────────

LEG_YES = "YES"
LEG_NO = "NO"
LEG_BOTH = "BOTH"
LEG_NONE = "NONE"
LEG_HELD = "HELD"          # whichever leg inventory currently holds

DO_NOT_NET = (
    "an action names the leg it touches. YES and NO on one condition "
    "are complements, not opposites of a single scalar: 100 YES with "
    "100 NO is a MATCHED PAIR carrying locked P&L and occupying "
    "capital, while 0 and 0 is genuinely flat. Both net to zero, and "
    "reporting them identically loses the profit, the capital and the "
    "management obligation")

# ── how the action reaches the book ──────────────────────────────────

PASSIVE = "PASSIVE"        # rests; fill is uncertain
AGGRESSIVE = "AGGRESSIVE"  # crosses; fill is observed depth
NON_ORDER = "NON_ORDER"    # touches no book at all

# ── the canonical set (§1) ───────────────────────────────────────────
#
# `requires` lists the EV terms that must be IDENTIFIED before the
# action can carry a number. A term that is absent makes the EV
# NOT_IDENTIFIED -- never zero (§1).

NOT_REPRESENTED = "NOT_REPRESENTED_IN_RESEARCH_VOCABULARY"

CANONICAL_ACTIONS = {
    "MAKE_YES": {
        "leg": LEG_YES, "aggression": PASSIVE,
        "what": "rest a bid on the YES leg",
        "requires": ("P_FILL", "VALUE_IF_FILL"),
        "evCore": "MAKE", "actionEv": "POST_BID",
        "positionState": NOT_REPRESENTED,
    },
    "MAKE_NO": {
        "leg": LEG_NO, "aggression": PASSIVE,
        "what": "rest a bid on the NO leg",
        "requires": ("P_FILL", "VALUE_IF_FILL"),
        "evCore": "MAKE", "actionEv": "POST_ASK",
        "positionState": NOT_REPRESENTED,
    },
    "MAKE_BOTH": {
        "leg": LEG_BOTH, "aggression": PASSIVE,
        "what": "rest on both legs simultaneously, seeking a pair",
        "requires": ("P_FILL", "VALUE_IF_FILL", "JOINT_FILL_MODEL"),
        "evCore": "MAKE", "actionEv": "MAKER_QUOTE",
        "positionState": NOT_REPRESENTED,
        "note": ("the two fills are NOT independent and MAKE_BOTH is "
                 "not MAKE_YES + MAKE_NO: filling one leg alone leaves "
                 "an unpaired residual, which is the Ferrari failure "
                 "(§6). Its EV requires a JOINT fill model, which does "
                 "not exist, so this action cannot be priced by "
                 "summing its legs"),
    },
    "TAKE_YES": {
        "leg": LEG_YES, "aggression": AGGRESSIVE,
        "what": "cross the spread to buy YES against observed depth",
        "requires": ("VALUE_IF_FILL",),
        "evCore": "TAKE", "actionEv": "TAKER_CROSS",
        "positionState": NOT_REPRESENTED,
    },
    "TAKE_NO": {
        "leg": LEG_NO, "aggression": AGGRESSIVE,
        "what": "cross the spread to buy NO against observed depth",
        "requires": ("VALUE_IF_FILL",),
        "evCore": "TAKE", "actionEv": "TAKER_CROSS",
        "positionState": NOT_REPRESENTED,
    },
    "POST_COMPLEMENT": {
        "leg": LEG_BOTH, "aggression": PASSIVE,
        "what": "rest on the complement of a leg already held",
        "requires": ("P_FILL", "VALUE_IF_FILL"),
        "evCore": "PAIR", "actionEv": "PAIR",
        "positionState": "A_PASSIVE_COMPLEMENT_PAIR",
    },
    "TAKE_COMPLEMENT": {
        "leg": LEG_BOTH, "aggression": AGGRESSIVE,
        "what": "cross to buy the complement of a leg already held",
        "requires": ("VALUE_IF_FILL",),
        "evCore": "PAIR", "actionEv": "PAIR",
        "positionState": "A_AGGRESSIVE_COMPLEMENT_PAIR",
    },
    "COMPLETE_PAIR": {
        "leg": LEG_BOTH, "aggression": AGGRESSIVE,
        "what": "close the pair, locking (1 - basis)",
        "requires": ("VALUE_IF_FILL",),
        "evCore": "PAIR", "actionEv": "PAIR",
        "positionState": "A_HOLD_LOCKED_PAIR",
    },
    "MERGE": {
        "leg": LEG_BOTH, "aggression": NON_ORDER,
        "what": "redeem a matched pair for $1 without touching the book",
        "requires": ("MERGE_MECHANISM_CONFIRMED",),
        "evCore": NOT_REPRESENTED, "actionEv": NOT_REPRESENTED,
        "positionState": "A_REALIZE_AND_RECYCLE",
        "note": ("§14: SHADOW ONLY. research/beta48/merge_vs_hold_"
                 "regime.py studies the regime but no venue merge "
                 "mechanism is confirmed for this instrument, so the "
                 "action exists in the table and cannot be priced"),
    },
    "HOLD": {
        "leg": LEG_HELD, "aggression": NON_ORDER,
        "what": "keep the current position unchanged for now",
        "requires": (),
        "evCore": "HOLD", "actionEv": "HOLD",
        "positionState": "A_DIRECTIONAL_HOLD",
    },
    "WAIT_REQUOTE": {
        "leg": LEG_NONE, "aggression": NON_ORDER,
        "what": "cancel and wait for a better quote",
        "requires": (),
        "evCore": NOT_REPRESENTED, "actionEv": "CANCEL",
        "positionState": "A_WAIT",
    },
    "DIRECT_EXIT": {
        "leg": LEG_HELD, "aggression": AGGRESSIVE,
        "what": "sell the held leg back into its own book",
        "requires": ("VALUE_IF_FILL",),
        "evCore": "AGGRESSIVE_EXIT", "actionEv": "AGGRESSIVE_EXIT",
        "positionState": "A_AGGRESSIVE_SELL_EXIT",
    },
    "HEDGE": {
        "leg": LEG_BOTH, "aggression": AGGRESSIVE,
        "what": "buy the complement to neutralise, paying the hedge tax",
        "requires": ("VALUE_IF_FILL", "HEDGE_TAX"),
        "evCore": "HEDGE", "actionEv": "HEDGE",
        "positionState": "A_HEDGE",
        "note": ("§5/§12 (SwissTony): the best way to exit YES is NOT "
                 "always to sell YES. Buying NO neutralises the same "
                 "exposure and is sometimes cheaper. The difference is "
                 "the HEDGE TAX and it must be priced, not assumed"),
    },
    "HOLD_TO_SETTLEMENT": {
        "leg": LEG_HELD, "aggression": NON_ORDER,
        "what": "carry the position to settlement",
        "requires": ("SETTLEMENT_SEMANTICS_RESOLVED",),
        "evCore": "SETTLE", "actionEv": "SETTLE",
        "positionState": "A_SETTLEMENT_HOLD",
        "note": ("this venue's settlement prose is "
                 "CONFLICTING_VENUE_PROSE and unresolved, so the "
                 "terminal value of carrying to settlement is not "
                 "identified"),
    },
    "NO_TRADE": {
        "leg": LEG_NONE, "aggression": NON_ORDER,
        "what": "take no action",
        "requires": (),
        "evCore": "NO_TRADE", "actionEv": "NO_TRADE",
        "positionState": NOT_REPRESENTED,
    },
}

ACTIONS = tuple(CANONICAL_ACTIONS)

# Actions that can ever open or increase exposure. Listed so a reader
# can see at a glance which half of the table is gated by the standing
# "no new trades" instruction rather than by economics.
EXPOSURE_INCREASING = (
    "MAKE_YES", "MAKE_NO", "MAKE_BOTH", "TAKE_YES", "TAKE_NO",
    "POST_COMPLEMENT", "TAKE_COMPLEMENT", "HEDGE",
)

UNKNOWN_IS_NEVER_ZERO = (
    "a term whose value is not identified propagates as "
    "NOT_IDENTIFIED through the whole action EV. It is never defaulted "
    "to zero, because a missing adverse-selection term read as 0.0 and "
    "a missing P_FILL read as 1.0 together produce a confident "
    "positive EV built entirely out of the terms nobody measured -- "
    "which are exactly the terms that turn an apparent edge negative")


def leg_of(action: str) -> str:
    spec = CANONICAL_ACTIONS.get(action)
    return spec["leg"] if spec else LEG_NONE


def requires(action: str) -> tuple:
    spec = CANONICAL_ACTIONS.get(action)
    return tuple(spec.get("requires") or ()) if spec else ()


def research_action(action: str, vocabulary: str):
    """The research-module name for a canonical action, or NOT_REPRESENTED.

    `vocabulary` is one of "evCore", "actionEv", "positionState".
    Returns NOT_REPRESENTED when that vocabulary has no equivalent --
    which is a finding, not an error, and must not be papered over by
    aliasing onto the nearest similar name.
    """
    spec = CANONICAL_ACTIONS.get(action)
    if spec is None:
        return NOT_REPRESENTED
    return spec.get(vocabulary, NOT_REPRESENTED)


def coverage() -> dict:
    """Which canonical actions each research vocabulary can actually price."""
    out = {}
    for vocab in ("evCore", "actionEv", "positionState"):
        covered = [a for a in ACTIONS
                   if research_action(a, vocab) != NOT_REPRESENTED]
        out[vocab] = {
            "covered": covered,
            "notRepresented": [a for a in ACTIONS if a not in covered],
            "coveredCount": len(covered),
        }
    out["canonicalCount"] = len(ACTIONS)
    out["pricedByNoVocabulary"] = [
        a for a in ACTIONS
        if all(research_action(a, v) == NOT_REPRESENTED
               for v in ("evCore", "actionEv", "positionState"))]
    return out


def describe() -> dict:
    return {
        "canonicalActions": list(ACTIONS),
        "exposureIncreasing": list(EXPOSURE_INCREASING),
        "legs": {a: CANONICAL_ACTIONS[a]["leg"] for a in ACTIONS},
        "doNotNet": DO_NOT_NET,
        "unknownIsNeverZero": UNKNOWN_IS_NEVER_ZERO,
        "coverage": coverage(),
        "whyOneTable": (
            "three research vocabularies existed with three different "
            "action sets, so 'what did BETTOR consider' had three "
            "answers and no cross-audit was possible. This is the one "
            "table; the research modules keep their own names and this "
            "maps onto them"),
    }
