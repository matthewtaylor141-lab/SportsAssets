"""§16. INDEPENDENT FAIR VALUE: WIRED, AND STILL NOT VALIDATED.

Owner directive, "CONTINUE THE BUILD" §16:

    "Wire the existing independent fair-value research... according to
    their actual validation status. Do not promote an unvalidated
    model... If independent FV is not validated: directional actions
    remain blocked / shadow-only. Do not let venue midpoint become
    BETTOR belief."

THE BOTTLENECK IS NOT PLUMBING. Every engine in this stack terminates
at FV_BETTOR_INDEPENDENT = NOT_IDENTIFIED -- pair EV, hedge-tax-vs-
hold, the exit ranking, the capital allocator. It would be easy to
read that as unfinished wiring. It is not. The models exist, they run,
and they have been tested against the market under a protocol built to
catch exactly the error that would flatter them.

WHAT THE MEASUREMENT SAYS (P_BETTOR_INDEPENDENT_V3_RESULT.md §7b),
under a four-window protocol with no evaluation fixture inside the
training or calibration windows and disjointness enforced by EVENT,
not by row:

    Calibrator selected            IDENTITY, by k-fold CV inside W1
    Delta log loss (blend-market)  -0.00926  -- the blend is WORSE
    95% event-clustered CI         [-0.00222, +0.02036]
    INCREMENTAL_SIGNAL_STATUS      NOT_DETECTED_AT_THIS_SAMPLE_SIZE

So the venue price remains the strongest settlement forecast, and
adding the challenger made the held-out forecast worse by about 0.013
log loss. That is why directional actions are blocked. It is an
empirical result, not a missing integration.

    AND IT IS NOT A NEGATIVE CLAIM EITHER. The report is explicit:
    "no negative claim about fundamental alpha is licensed."
    NOT_DETECTED_AT_THIS_SAMPLE_SIZE means the test could not resolve
    the question, not that the answer is no. Both readings are wrong:
    the model is not ready, and it is not refuted.

TWO METHOD FINDINGS PRESERVED HERE BECAUSE THEY BIND FUTURE VERSIONS.

1. METHOD SELECTION IS PART OF FITTING. Isotonic scored 0.4925
   in-sample against identity's 0.5259 and cross-validated at 0.5702 --
   worse than doing nothing. An in-sample comparison hands the prize to
   the most flexible candidate, so selection is by k-fold CV inside the
   calibration window.

2. THE LEAK CONTROL STAYS EVEN THOUGH IT BOUND NOTHING. Running the
   forbidden variant -- calibrator fitted ON the test events -- moved
   the answer by 0.00000, because an identity map cannot carry outcome
   information wherever it is fitted. That is a fact about THIS run. A
   run that selects isotonic or beta would leak and nothing in the
   numbers would show it.

WHAT THIS MODULE THEREFORE DOES. It exposes the models, their versions
and their measured status, and it answers one question --
may this model set a BETTOR belief? -- with NO. The venue midpoint is
available as FV_VENUE_IMPLIED and is never renamed into a belief.
"""

from __future__ import annotations

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_VALIDATED = "NOT_VALIDATED"

FV_VENUE_IMPLIED = "FV_VENUE_IMPLIED"
FV_BETTOR_INDEPENDENT = "FV_BETTOR_INDEPENDENT"

# ── the measured status of each version ──────────────────────────────

NOT_DETECTED = "NOT_DETECTED_AT_THIS_SAMPLE_SIZE"

MODELS = {
    "p_bettor_independent": {
        "version": "V1",
        "module": "research/beta48/shadow/p_bettor_independent.py",
        "validation": NOT_VALIDATED,
        "supersededBy": "V2",
    },
    "p_bettor_independent_v2": {
        "version": "V2",
        "module": "research/beta48/shadow/p_bettor_independent_v2.py",
        "validation": NOT_VALIDATED,
        "why": ("B4 was disqualified: it fitted a shared component and "
                "then predicted through an independent-Poisson grid "
                "that threw it away"),
        "supersededBy": "V3",
    },
    "p_bettor_independent_v3": {
        "version": "V3",
        "module": "research/beta48/shadow/p_bettor_independent_v3.py",
        "validation": NOT_VALIDATED,
        "incrementalSignalStatus": NOT_DETECTED,
        "deltaLogLossBlendMinusMarket": -0.00926,
        "ci95EventClustered": [-0.00222, 0.02036],
        "calibratorSelected": "IDENTITY",
        "leakCheck": "CLEAN",
        "why": ("under a four-window protocol with event-level "
                "disjointness, adding the challenger made the held-out "
                "forecast WORSE. The market remains the strongest "
                "settlement forecast"),
        "repaired": ("V2's bivariate-Poisson defect: the shared "
                     "component now drives both the likelihood and the "
                     "emitted mass function"),
    },
}

CHAMPION = "B0_VENUE_PRICE"

CHAMPION_IS_THE_MARKET = (
    "no model in the zoo beat the venue price out of sample, so the "
    "venue price is the champion. A challenger that loses to the "
    "champion does not get to set a belief because it is ours")

# ── what the status does and does not mean ───────────────────────────

NOT_DETECTED_IS_NOT_REFUTED = (
    "NOT_DETECTED_AT_THIS_SAMPLE_SIZE means the test could not resolve "
    "the question. It is not evidence that no edge exists -- the "
    "result explicitly licenses no negative claim about fundamental "
    "alpha -- and it is not evidence that one does. Reading it either "
    "way would turn an inconclusive measurement into a conclusion")

WHY_DIRECTIONAL_IS_BLOCKED = (
    "a directional action is a bet that the market is wrong. That "
    "requires a fair value formed independently of the market, and the "
    "only candidates measured so far lose to it out of sample. Until "
    "one wins, a directional position would be backed by nothing but "
    "the venue's own price wearing our name")

MIDPOINT_IS_NOT_A_BELIEF = (
    "FV_VENUE_IMPLIED is available on every row and must never be "
    "promoted into FV_BETTOR_INDEPENDENT by being the only number "
    "present. A benchmark cannot be evidence against itself, so a "
    "position taken because the midpoint said so is a position taken "
    "for no stated reason")

# ── method findings that bind future versions ────────────────────────

METHOD_FINDINGS = {
    "SELECTION_IS_PART_OF_FITTING": (
        "isotonic scored 0.4925 in-sample against identity's 0.5259 and "
        "cross-validated at 0.5702 -- worse than doing nothing. An "
        "in-sample comparison hands the prize to the most flexible "
        "candidate, so the calibrator is selected by k-fold CV inside "
        "the calibration window"),
    "KEEP_THE_LEAK_CONTROL": (
        "the forbidden variant -- calibrator fitted ON the test events "
        "-- moved the answer by 0.00000, because an identity map "
        "cannot carry outcome information wherever it is fitted. That "
        "is a fact about that run, not a reason to drop the control: a "
        "run selecting isotonic or beta would leak and nothing in the "
        "numbers would show it"),
    "CALIBRATION_CREATES_NO_INFORMATION": (
        "a monotone transform cannot change a forecast's ranking of "
        "outcomes and therefore cannot manufacture orthogonal "
        "information. Recalibration may improve probability quality; it "
        "never adds signal"),
}


def status(model=None) -> dict:
    """The validation status of one model, or of the whole set."""
    if model is not None:
        row = MODELS.get(model)
        if row is None:
            return {"model": model, "validation": NOT_IDENTIFIED,
                    "why": "unknown model"}
        return {"model": model, **row,
                "notDetectedIsNotRefuted": NOT_DETECTED_IS_NOT_REFUTED}
    return {
        "models": {k: v["validation"] for k, v in MODELS.items()},
        "anyValidated": False,
        "champion": CHAMPION,
        "championIsTheMarket": CHAMPION_IS_THE_MARKET,
        "notDetectedIsNotRefuted": NOT_DETECTED_IS_NOT_REFUTED,
        "methodFindings": dict(METHOD_FINDINGS),
    }


def fair_value() -> dict:
    """BETTOR's independent fair value. There is not one."""
    return {
        FV_BETTOR_INDEPENDENT: NOT_IDENTIFIED,
        "validation": NOT_VALIDATED,
        "bestCandidate": "p_bettor_independent_v3",
        "incrementalSignalStatus": NOT_DETECTED,
        "why": MODELS["p_bettor_independent_v3"]["why"],
        "notDetectedIsNotRefuted": NOT_DETECTED_IS_NOT_REFUTED,
        "midpointIsNotABelief": MIDPOINT_IS_NOT_A_BELIEF,
        "evidence": "research/beta48/P_BETTOR_INDEPENDENT_V3_RESULT.md",
    }


def directional_permitted() -> dict:
    """May BETTOR take a position because it thinks the market is wrong?"""
    return {
        "permitted": False,
        "blocker": "FV_BETTOR_INDEPENDENT_NOT_VALIDATED",
        "why": WHY_DIRECTIONAL_IS_BLOCKED,
        "shadowOnly": True,
        "whatWouldLiftIt": (
            "a challenger that beats the venue price out of sample "
            "under the four-window protocol, with the calibrator "
            "selected by cross-validation inside the calibration "
            "window and the leak control still in place"),
        "isNotPerformanceBased": (
            "this blocks a model that has not been shown to work. It is "
            "not a reaction to a loss and no P&L is read here"),
    }


def describe() -> dict:
    return {
        "purpose": "expose the independent fair-value research and its status",
        "models": list(MODELS),
        "anyValidated": False,
        FV_BETTOR_INDEPENDENT: NOT_IDENTIFIED,
        "champion": CHAMPION,
        "directionalPermitted": False,
        "midpointIsNotABelief": MIDPOINT_IS_NOT_A_BELIEF,
        "notDetectedIsNotRefuted": NOT_DETECTED_IS_NOT_REFUTED,
    }
