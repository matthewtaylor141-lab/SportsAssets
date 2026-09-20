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

# ── what each action does to exposure, on BOTH axes ──────────────────
#
# THIS IS THE SINGLE SOURCE. The risk engine consumes it rather than
# keeping its own copy: an earlier version had both, they disagreed
# about COMPLETE_PAIR, and a safety gate whose notion of "increases
# exposure" differs from the action table's is a gate that can be
# walked around by naming the action differently.
#
# GROSS is how much position exists. DIRECTIONAL is how much outcome
# risk it carries. A hedge raises the first and lowers the second, so
# a single axis must misclassify it whichever way it chooses.

INCREASE = "INCREASE"
DECREASE = "DECREASE"
UNCHANGED = "UNCHANGED"

EXPOSURE_EFFECT = {
    "MAKE_YES": (INCREASE, INCREASE),
    "MAKE_NO": (INCREASE, INCREASE),
    # Both legs resting seeks a pair, so a completed MAKE_BOTH is
    # directionally neutral -- but only if BOTH fill. One leg alone is
    # a directional position, which is the Ferrari residual.
    "MAKE_BOTH": (INCREASE, UNCHANGED),
    "TAKE_YES": (INCREASE, INCREASE),
    "TAKE_NO": (INCREASE, INCREASE),
    "POST_COMPLEMENT": (INCREASE, DECREASE),
    "TAKE_COMPLEMENT": (INCREASE, DECREASE),
    # COMPLETING A PAIR BUYS THE SECOND LEG. It neutralises outcome
    # risk and it occupies MORE capital, on a second position, with its
    # own fees. It belongs on the gated side of the table, and leaving
    # it off was a real omission.
    "COMPLETE_PAIR": (INCREASE, DECREASE),
    "HEDGE": (INCREASE, DECREASE),
    "MERGE": (DECREASE, UNCHANGED),
    "DIRECT_EXIT": (DECREASE, DECREASE),
    "HOLD": (UNCHANGED, UNCHANGED),
    "WAIT_REQUOTE": (UNCHANGED, UNCHANGED),
    "HOLD_TO_SETTLEMENT": (UNCHANGED, UNCHANGED),
    "NO_TRADE": (UNCHANGED, UNCHANGED),
}

TWO_AXES = (
    "GROSS exposure is how much position exists; DIRECTIONAL exposure "
    "is how much outcome risk it carries. A hedge raises the first and "
    "lowers the second, so a single-axis model must misclassify it "
    "whichever way it chooses")

# Derived, never hand-maintained beside the table above.
EXPOSURE_INCREASING = tuple(
    a for a in CANONICAL_ACTIONS if EXPOSURE_EFFECT[a][0] == INCREASE)

UNKNOWN_IS_NEVER_ZERO = (
    "a term whose value is not identified propagates as "
    "NOT_IDENTIFIED through the whole action EV. It is never defaulted "
    "to zero, because a missing adverse-selection term read as 0.0 and "
    "a missing P_FILL read as 1.0 together produce a confident "
    "positive EV built entirely out of the terms nobody measured -- "
    "which are exactly the terms that turn an apparent edge negative")


# ── §2: names management asked for, answered without aliasing ────────
#
# "The management action table should expose vocabulary gaps rather
# than hide them." A requested name resolves to a canonical action ONLY
# where the economics are the same action. Where they are not, the
# answer is NOT_REPRESENTED and the reason is recorded.

REQUESTED_VOCABULARY = {
    "EXIT_HEDGE": {
        "canonical": NOT_REPRESENTED,
        "closest": "HEDGE",
        "why": (
            "HEDGE is defined here as buying the complement to "
            "neutralise exposure. EXIT_HEDGE names an INTENT -- "
            "hedging in order to leave -- and no definition of it "
            "exists in this repository to check that intent against. "
            "Two actions with the same mechanics and different intents "
            "are not proven identical, and mapping them because the "
            "words overlap is the false mapping §2 forbids"),
        "whatWouldSettleIt": (
            "a definition of EXIT_HEDGE stating whether it differs "
            "from HEDGE in the book it touches, the quantity it takes, "
            "or only in why it was chosen"),
    },
    "SETTLE": {
        "canonical": NOT_REPRESENTED,
        "closest": "HOLD_TO_SETTLEMENT",
        "why": (
            "HOLD_TO_SETTLEMENT is a DECISION taken now to carry the "
            "position; settlement is an EVENT the venue performs at "
            "expiry. BETTOR chooses the first and cannot choose the "
            "second, so they are not the same action and one must not "
            "stand in for the other"),
        "namingCollisionInTheResearchVocabularies": (
            "action_ev and ev_core both list an action called SETTLE, "
            "and action_ev's own comment defines it as 'holding to "
            "settlement' -- so that label denotes the HOLD, not a "
            "separate settle action. The collision is recorded rather "
            "than resolved by adopting the name"),
        "whatWouldSettleIt": (
            "a venue mechanism BETTOR can invoke to realise settlement "
            "early. None is documented; this venue's settlement prose "
            "is CONFLICTING_VENUE_PROSE"),
    },
}

# A GAP INSIDE THE CANONICAL TABLE, recorded because it is real. HEDGE
# and TAKE_COMPLEMENT both buy the complement aggressively; only the
# INTENT differs -- neutralise versus complete a pair and keep it.
# position_state separates them by STATE (HEDGE_AVAILABLE vs
# PAIR_AVAILABLE) rather than by mechanics, so the two are
# distinguishable only where inventory state is known.
KNOWN_AMBIGUITY = {
    "actions": ("HEDGE", "TAKE_COMPLEMENT"),
    "sameMechanics": "cross to buy the complement leg",
    "differentIntent": ("HEDGE neutralises; TAKE_COMPLEMENT completes a "
                        "pair intended to be held or merged"),
    "separatedBy": ("inventory state, per position_state's "
                    "HEDGE_AVAILABLE vs PAIR_AVAILABLE -- not by the "
                    "order either would send"),
    "why": ("recorded rather than resolved: collapsing them would lose "
            "the distinction the exit engine needs, and inventing a "
            "mechanical difference they do not have would be worse"),
}


def requested_name(name: str) -> dict:
    """Resolve a name management used, or say it is not represented."""
    if name in CANONICAL_ACTIONS:
        return {"requested": name, "canonical": name, "exact": True}
    spec = REQUESTED_VOCABULARY.get(name)
    if spec is None:
        return {"requested": name, "canonical": NOT_REPRESENTED,
                "why": "no such action is declared"}
    return {"requested": name, "exact": False, **spec}


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
