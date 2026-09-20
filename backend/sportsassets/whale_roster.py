"""WHALE INTELLIGENCE: the multi-whale research layer.

Owner hierarchy correction, 2026-09-20:

    BETTOR EV ENGINE            PRIMARY
    BETTOR EXPERIMENTAL LAB     PRIMARY RESEARCH
    WHALE INTELLIGENCE          MULTI-WHALE RESEARCH LAYER
    RN1                         ONE SPECIALIST WITHIN IT

"Do not collapse all whale behaviour into an RN1-style mechanism."

WHY THIS MODULE EXISTS AT ALL. The copy-trading side of this codebase
has known six whales by name for months, and the shadow/BETTOR side
knew exactly one. That asymmetry is how "RN1" quietly became the word
for the whole layer: the only whale the research lane could name was
the only whale it could think about. This module gives the layer its
own name and its own population, so a reader coming to the research
lane finds a roster rather than a single account.

WHAT THIS MODULE IS NOT. It is not a feed, not a selector and not a
scorer. It opens nothing and decides nothing. It records WHO is
studied and WHICH ECONOMICS each one was actually observed to run, so
that a later reader does not reach for RN1's mechanism by default when
reasoning about somebody whose economics are not RN1's.

EVERY MECHANISM HERE IS A FINDING, NOT A DEFINITION. The values are
OBSERVED or NOT_ESTABLISHED, never assumed, and a whale whose
economics have not been measured says NOT_ESTABLISHED rather than
inheriting a neighbour's. Assuming a whale pairs because the last one
did is exactly the collapse the directive forbids.
"""

from __future__ import annotations

NOT_ESTABLISHED = "NOT_ESTABLISHED"
OBSERVED = "OBSERVED"

# ── the six economics that must not be collapsed ─────────────────────
#
# Named by the owner on 2026-09-20. They are separate axes because two
# whales can agree on one and disagree on every other: a pairer and a
# directional holder can have identical entry behaviour and completely
# different settlement economics, and a copy sized from the wrong axis
# loses money while looking correct.

DIM_PAIR_COMPLETION = "PAIR_COMPLETION_ECONOMICS"
DIM_DIRECTIONAL_HOLD = "DIRECTIONAL_HOLD_ECONOMICS"
DIM_RESIDUAL_INVENTORY = "RESIDUAL_INVENTORY"
DIM_SETTLEMENT_LEAKAGE = "SETTLEMENT_LEAKAGE"
DIM_EXIT_BEHAVIOUR = "EXIT_BEHAVIOUR"
DIM_CAPITAL_RECYCLING = "CAPITAL_RECYCLING"

DIMENSIONS = (DIM_PAIR_COMPLETION, DIM_DIRECTIONAL_HOLD,
              DIM_RESIDUAL_INVENTORY, DIM_SETTLEMENT_LEAKAGE,
              DIM_EXIT_BEHAVIOUR, DIM_CAPITAL_RECYCLING)

DIMENSION_QUESTIONS = {
    DIM_PAIR_COMPLETION:
        "does the whale buy BOTH outcomes, and does the completed pair "
        "clear below 1.00 after fees?",
    DIM_DIRECTIONAL_HOLD:
        "does the whale take a side and hold it, and is the edge in the "
        "direction or in the holding?",
    DIM_RESIDUAL_INVENTORY:
        "what is left unmatched when the pairing does not complete, and "
        "who carries that risk?",
    DIM_SETTLEMENT_LEAKAGE:
        "what is lost between the last trade and settlement -- fees, "
        "dust, unexited residue?",
    DIM_EXIT_BEHAVIOUR:
        "does the whale sell, buy the other side, or hold to "
        "settlement?",
    DIM_CAPITAL_RECYCLING:
        "how fast does capital come back and get redeployed?",
}

# ── the studied population ───────────────────────────────────────────
#
# The roster the owner named on 2026-09-20, plus the broader tracked
# roster where appropriate. THE ORDER IS NOT A RANKING and RN1 is not
# first: it is one specialist among these, and a list that always
# started with RN1 would keep teaching the habit this module exists to
# correct.
#
# `mechanism` records what was OBSERVED in the case studies. Where a
# dimension was never measured for a whale it says NOT_ESTABLISHED --
# which is a real answer about our coverage, and is the thing that
# tells the next reader what to go and measure.

FERRARI = "FerrariChampions2026"
RN1 = "RN1"
SWISSTONY = "SwissTony"
HOMERUNHAZARD = "HomeRunHazard"
KCH123 = "kch123"
W2C33 = "w2c33"

ROSTER = (FERRARI, RN1, SWISSTONY, HOMERUNHAZARD, KCH123, W2C33)


def _blank() -> dict:
    return {d: NOT_ESTABLISHED for d in DIMENSIONS}


# WHAT IS FILLED IN HERE IS ONLY WHAT THE LEDGER ACTUALLY SHOWED. The
# two entries below come from case studies run against venue and chain
# rows in this repository; everything else is left NOT_ESTABLISHED on
# purpose rather than guessed from a neighbour.
MECHANISM = {name: _blank() for name in ROSTER}

MECHANISM[RN1].update({
    # The pair study: 58.6% of his cost bought BOTH outcomes, so his
    # NET erases the matched book and his GROSS double-counts it.
    DIM_PAIR_COMPLETION: OBSERVED,
    DIM_EXIT_BEHAVIOUR: OBSERVED,
    DIM_RESIDUAL_INVENTORY: OBSERVED,
})

MECHANISM[HOMERUNHAZARD].update({
    # The exit-methodology study answered for HRH specifically, and the
    # answer was NOT RN1's: directional rather than paired.
    DIM_DIRECTIONAL_HOLD: OBSERVED,
    DIM_EXIT_BEHAVIOUR: OBSERVED,
})


def mechanism_of(whale: str) -> dict:
    """What was OBSERVED about one whale's economics. Never inherited."""
    return dict(MECHANISM.get(whale) or _blank())


def unmeasured(whale: str) -> list:
    """Which of the six axes this whale has never been measured on.

    The useful direction of this module: it says what we do not know
    about whom, by name, instead of letting one whale's mechanism stand
    in for the layer.
    """
    row = mechanism_of(whale)
    return [d for d in DIMENSIONS if row.get(d) != OBSERVED]


def coverage() -> dict:
    """The whole roster's measured/unmeasured picture, for COMMAND.

    COMMAND's whale-facing default is WHALE INTELLIGENCE / ALL WHALES;
    RN1 stays available as an individual specialist and benchmark.
    """
    return {"layer": "WHALE_INTELLIGENCE",
            "default": "ALL_WHALES",
            "roster": list(ROSTER),
            "dimensions": list(DIMENSIONS),
            "measured": {w: [d for d in DIMENSIONS
                             if mechanism_of(w)[d] == OBSERVED]
                         for w in ROSTER},
            "unmeasured": {w: unmeasured(w) for w in ROSTER},
            "specialists": {RN1: "individual specialist / benchmark"}}
