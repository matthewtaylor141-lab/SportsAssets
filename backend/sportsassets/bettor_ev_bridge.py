"""WIRING THE BUILT EV MACHINERY INTO THE PRODUCTION DECISION LANE.

Owner directive, "RETURN TO THE ACTUAL BETTOR EV ENGINE" §0:

    "Before writing new modules, inventory the current repository...
    Do not create duplicate engines because an existing module is
    imperfect... Then immediately implement the missing integration."

WHAT WAS ACTUALLY MISSING. Not the engine. `research/beta48/shadow/`
holds `action_ev.py`, `action_ev_mc.py`, `ev_core.py`,
`inventory_state.py`, `position_state.py`, `maker_fill.py` and
`prior_registry.py` -- all tested, all importable. What did not exist
was any import of them from `backend/`. The production lane wrote
4,503 rows of NO_TRADE with `total_action_ev` NULL while a working
action-EV calculator sat unreferenced in the same repository.

They are loaded BY PATH, never copied. `calibration_domain.py` set
that precedent and gave the reason: "a copy drifts."

────────────────────────────────────────────────────────────────────
TWO CORRECTIONS AGAINST THIS FILE'S OWN FIRST VERSION (869b662).
Both were mine and both were wrong in the same way: they turned an
absence of evidence into a directional claim.

CORRECTION 1 -- FILL SELECTION HAS NO ESTABLISHED SIGN (§0).

The first version argued that "adverse selection cannot be negative --
being filled is never systematically GOOD news" and used a zeroed
adverse-selection term to derive a LOWER BOUND on the required fill
rate. `prior_registry.py` had already refused exactly that:

    FILL_SELECTION_PRIOR_SOURCE = STRUCTURAL_NONDIRECTIONAL_PRIOR
    PRIOR_CENTER                = ZERO
    DIRECTION_ASSUMED           = NO
    POSTERIOR_MAY_MOVE_EITHER_WAY

and it named the failure mode in advance: "Any consumer that reads
only the mean sees 0.0 and silently treats the term as absent -- which
is the exact substitution the evidence-class rule exists to prevent."
That consumer was this file.

Informed takers plausibly push the effect adverse; liquidity, hedging
and impatient-benign flow plausibly push it favourable. Nothing
establishes which dominates here. So the term is now CARRIED as the
frozen weak prior and its EV is published across the prior's own
P10/P50/P90 band -- option A of §0. There is no lower bound, because a
bound requires a sign and no sign exists.

CORRECTION 2 -- A VENUE-RELATIVE COST IS NOT A SETTLEMENT EV (§1).

The first version priced crossing against `B0_VENUE_PRICE` and called
the negative result REFUTED. But B0 is the venue's own price: it is
FV_VENUE_IMPLIED, not FV_BETTOR_INDEPENDENT. Measuring the venue
against itself establishes what it costs to cross the spread RIGHT
NOW relative to the venue's own benchmark. It cannot establish that
the settlement EV is negative, because that would require an
independent fair value, and none is validated.

So the aggressive result is now named for what it is --
SNAPSHOT_EXECUTION_COST_VS_VENUE_PRICE -- and the settlement EV stays
NOT_IDENTIFIED. The two fair values are kept apart by name throughout
so the venue's midpoint can never quietly become BETTOR's belief.

WHAT SURVIVES BOTH CORRECTIONS. Crossing the spread costs money
against the venue-implied benchmark, and that is a real, measured
execution fact worth recording on every row. It is simply not alpha,
and this file no longer implies that it is.
────────────────────────────────────────────────────────────────────

THE GATE DOES NOT MOVE. Nothing here produces a BUY or a SELL.
mirror_live is false and no order path exists in this module or below
it. A number in the table is evidence, never an instruction.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import bettor_ev_actions as acts

NOT_IDENTIFIED = "NOT_IDENTIFIED"
MACHINERY_UNAVAILABLE = "EV_MACHINERY_UNAVAILABLE"

# ── §14: the no-fill branch, which is not always nothing ─────────────
#
# A passive order is NOT P_FILL x profit_if_filled. It has three
# outcomes -- FULL_FILL, PARTIAL_FILL, NO_FILL -- and the last one
# depends on WHAT THE POSITION WOULD BE IF NOTHING HAPPENED:
#
#     PASSIVE ENTRY FROM FLAT   no fill -> no exposure. EV_IF_NO_FILL
#                               is genuinely 0, and that zero is
#                               MEASURED, not assumed.
#
#     PASSIVE EXIT              no fill -> WE STILL OWN THE POSITION.
#                               EV_IF_NO_FILL is the value of
#                               continuing to hold, which needs a fair
#                               value we do not have.
#
# Collapsing the second into zero is the error the distinction exists
# to prevent: it prices a failed exit as though the risk had gone away.

FILL_OUTCOMES = ("FULL_FILL", "PARTIAL_FILL", "NO_FILL")

ENTRY_FROM_FLAT = "ENTRY_FROM_FLAT"
PASSIVE_EXIT = "PASSIVE_EXIT"

NO_FILL_RULE = (
    "EV_IF_NO_FILL is priced explicitly and is never silently zero. It "
    "is zero only for a passive ENTRY FROM FLAT, where not filling "
    "leaves no exposure -- there the zero is a measured consequence of "
    "the state. For a passive EXIT, not filling means we still own the "
    "position, so EV_IF_NO_FILL is the value of continuing to hold and "
    "is NOT_IDENTIFIED while no independent fair value exists")


def no_fill_branch(context=ENTRY_FROM_FLAT) -> dict:
    """What happens to the book if the resting order never fills."""
    if context == ENTRY_FROM_FLAT:
        return {
            "context": ENTRY_FROM_FLAT,
            "EV_IF_NO_FILL": "0",
            "evIfNoFillStatus": "IDENTIFIED",
            "why": ("a passive entry that does not fill leaves no "
                    "position, so the outcome is exactly nothing. This "
                    "zero is measured from the state, not assumed"),
            "residualExposure": "NONE",
        }
    return {
        "context": PASSIVE_EXIT,
        "EV_IF_NO_FILL": NOT_IDENTIFIED,
        "evIfNoFillStatus": NOT_IDENTIFIED,
        "why": ("a passive exit that does not fill leaves the position "
                "OPEN. The no-fill branch is therefore the value of "
                "continuing to hold, which requires a fair value that "
                "does not exist. Pricing it as zero would book a failed "
                "exit as though the risk had gone away"),
        "residualExposure": "UNCHANGED",
    }


# ── the two fair values, kept apart by name (§1) ─────────────────────

FV_VENUE_IMPLIED = "FV_VENUE_IMPLIED"
FV_BETTOR_INDEPENDENT = "FV_BETTOR_INDEPENDENT"

FAIR_VALUE_SEPARATION = (
    "FV_VENUE_IMPLIED is the venue's own price. FV_BETTOR_INDEPENDENT "
    "is a belief BETTOR formed for itself. Only the first exists "
    "today. Pricing an action against the first measures EXECUTION "
    "COST relative to the venue; it cannot measure MISPRICING, because "
    "a benchmark cannot be evidence against itself. The venue midpoint "
    "must never become BETTOR's belief by being the only number "
    "available")

# What an aggressive action's number DOES establish, and what it does not.
SNAPSHOT_EXECUTION_COST = "SNAPSHOT_EXECUTION_COST_VS_VENUE_PRICE"
SETTLEMENT_EV_NOT_IDENTIFIED = "SETTLEMENT_EV_NOT_IDENTIFIED"

# ── §2: where the research modules live in a deployed container ──────
#
# THE PARENT-DIRECTORY GUESS WAS NOT SAFE. The first version walked up
# two parents from this file, which resolves in a source checkout and
# has no reason to resolve inside an image whose layout is chosen by a
# Dockerfile. An explicit contract replaces the guess, and absence
# fails closed rather than silently pricing nothing forever.

RESEARCH_ROOT_ENV = "BETTOR_RESEARCH_ROOT"
DEFAULT_CONTAINER_ROOT = "/app/research/beta48/shadow"

REQUIRED_MODULES = ("prior_registry", "action_ev", "action_ev_mc",
                    "ev_core", "inventory_state", "position_state",
                    "maker_fill")

_CACHE: dict = {}


class MachineryUnavailable(RuntimeError):
    """The research EV modules could not be loaded."""


def research_root(root=None) -> Path | None:
    """The FIRST existing candidate, explicit contract before guesswork.

    Order: an explicit argument, then $BETTOR_RESEARCH_ROOT, then the
    container path this repository's image is built to, then the
    source-checkout layout. The last is a convenience for tests and
    local work, never the thing production depends on.
    """
    candidates = []
    if root is not None:
        candidates.append(Path(root))
    env = os.environ.get(RESEARCH_ROOT_ENV)
    if env:
        candidates.append(Path(env))
    candidates.append(Path(DEFAULT_CONTAINER_ROOT))
    candidates.append(
        Path(__file__).resolve().parents[2] / "research" / "beta48" / "shadow")
    for c in candidates:
        if c.is_dir():
            return c
    return None


def machinery(root=None) -> dict:
    """The tested EV modules, imported by path rather than copied."""
    shadow = research_root(root)
    if shadow is None:
        raise MachineryUnavailable(
            "no EV machinery found; set %s to the directory holding "
            "action_ev.py (expected %s in the deployed image)"
            % (RESEARCH_ROOT_ENV, DEFAULT_CONTAINER_ROOT))
    key = str(shadow)
    if key in _CACHE:
        return _CACHE[key]
    if key not in sys.path:
        sys.path.insert(0, key)
    got = {}
    for name in REQUIRED_MODULES:
        path = shadow / ("%s.py" % name)
        if not path.is_file():
            raise MachineryUnavailable("%s is missing from %s" % (name, key))
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise MachineryUnavailable("cannot load %s" % name)
            mod = importlib.util.module_from_spec(spec)
            sys.modules.setdefault(name, mod)
            spec.loader.exec_module(mod)
        except MachineryUnavailable:
            raise
        except Exception as exc:                               # noqa: BLE001
            raise MachineryUnavailable(
                "%s failed to import: %s: %s"
                % (name, type(exc).__name__, exc)) from exc
        got[name] = mod
    _CACHE[key] = got
    return got


def available(root=None) -> bool:
    try:
        machinery(root)
        return True
    except MachineryUnavailable:
        return False


def preflight(root=None) -> dict:
    """Prove the modules import HERE. Run inside the built image (§2)."""
    try:
        mods = machinery(root)
    except MachineryUnavailable as exc:
        return {"ok": False, "researchRoot": str(research_root(root)),
                "why": str(exc),
                "modules": {m: "NOT_IMPORTED" for m in REQUIRED_MODULES}}
    return {"ok": True, "researchRoot": str(research_root(root)),
            "modules": {m: "IMPORTED" for m in mods}}


# ── reading the book, refusing to guess ──────────────────────────────

def _d(v):
    if v is None or v == "" or v == NOT_IDENTIFIED:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def fair_value(market_state: dict | None) -> dict:
    """The VENUE-IMPLIED fair value. Explicitly not BETTOR's own."""
    ms = market_state or {}
    bid, ask, mid = _d(ms.get("bid")), _d(ms.get("ask")), _d(ms.get("mid"))
    if mid is None and bid is not None and ask is not None:
        mid = (bid + ask) / Decimal("2")
    if mid is None:
        return {"fairValue": NOT_IDENTIFIED, "basis": "NO_READABLE_BOOK",
                "kind": NOT_IDENTIFIED,
                "why": "no mid and no two-sided book"}
    return {
        "fairValue": str(mid),
        "kind": FV_VENUE_IMPLIED,
        "basis": "B0_VENUE_PRICE",
        FV_BETTOR_INDEPENDENT: NOT_IDENTIFIED,
        "separation": FAIR_VALUE_SEPARATION,
        "why": ("no model in the zoo beat the venue price out of "
                "sample, so it is the benchmark. A benchmark is not a "
                "belief and cannot be evidence against itself"),
    }


def _gross_vs_venue(action: str, fair: Decimal, bid, ask):
    """Book-derived edge RELATIVE TO THE VENUE-IMPLIED BENCHMARK.

    Negative for every cross, by construction: crossing pays the far
    side and the benchmark sits between them. That is an execution
    cost, not a settlement EV.
    """
    spec = acts.CANONICAL_ACTIONS.get(action) or {}
    aggression = spec.get("aggression")
    if aggression == acts.NON_ORDER:
        return None, "action touches no book"
    if aggression == acts.AGGRESSIVE:
        if action == "DIRECT_EXIT":
            if bid is None:
                return None, "no bid to sell into"
            return bid - fair, "sell the held leg at the bid"
        if ask is None:
            return None, "no ask to buy from"
        return fair - ask, "cross and pay the ask"
    if bid is None:
        return None, "no bid to rest at"
    return fair - bid, "rest at the bid; capture the near side if filled"


def fill_selection_band(root=None) -> dict:
    """The frozen weak prior, carried with its width (§0 option A).

    Its centre is zero because no direction is established -- NOT
    because the effect was measured at zero. The band is published so
    the width travels with the mean, which is the whole content of the
    prior.
    """
    try:
        PR = machinery(root)["prior_registry"]
    except MachineryUnavailable:
        return {"status": NOT_IDENTIFIED}
    p = PR.prior("FILL_SELECTION_EFFECT")
    return {
        "status": p.get("EVIDENCE_CLASS", NOT_IDENTIFIED),
        "source": p.get("FILL_SELECTION_PRIOR_SOURCE"),
        "priorCenter": p.get("PRIOR_CENTER"),
        "directionAssumed": p.get("DIRECTION_ASSUMED"),
        "priorStrength": p.get("PRIOR_STRENGTH"),
        "bettorNativeObservations": p.get("BETTOR_NATIVE_OBSERVATIONS"),
        "P10": p.get("P10"), "P50": p.get("MEDIAN"), "P90": p.get("P90"),
        "priorSha": p.get("PRIOR_SHA"),
        "posteriorMayMoveEitherWay": (
            "once BETTOR-native fill data exist the posterior may move "
            "ADVERSE or FAVOURABLE. Neither is a surprise, and a prior "
            "that could only be revised one way was a directional "
            "assumption wearing a symmetric distribution"),
        "notEvidenceTheEffectIsZero": (
            "a centre of zero records that no direction has been "
            "established -- a statement about our evidence, not about "
            "the venue. A measured zero and an unmeasured zero share a "
            "number and share nothing else"),
    }


def _break_even_band(gross, fee, band):
    """Required P_FILL across the fill-selection prior's own band.

    NOT a lower bound. A bound needs a sign and FILL_SELECTION_EFFECT
    has none: the band runs from ADVERSE at P10 to FAVOURABLE at P90,
    and the required fill rate moves with it in both directions.
    """
    if fee is None:
        return {"BREAK_EVEN_P_FILL_BAND": NOT_IDENTIFIED,
                "why": ("no fee schedule is declared, so no break-even "
                        "fill rate is identified at any point of the "
                        "fill-selection band")}
    if gross is None:
        return {"BREAK_EVEN_P_FILL_BAND": NOT_IDENTIFIED,
                "why": "no near-side price to rest at"}
    if band.get("status") == NOT_IDENTIFIED:
        return {"BREAK_EVEN_P_FILL_BAND": NOT_IDENTIFIED,
                "why": "the fill-selection prior could not be loaded"}

    out, signs = {}, []
    for key in ("P10", "P50", "P90"):
        q = band.get(key)
        if q is None:
            out[key] = NOT_IDENTIFIED
            continue
        # FILL_SELECTION_EFFECT enters VALUE_IF_FILL ADDITIVELY;
        # POSITIVE is FAVOURABLE to BETTOR.
        value = gross + Decimal(str(q))
        if value <= 0:
            out[key] = "NO_ADMISSIBLE_FILL_RATE"
            signs.append("negative")
            continue
        need = (fee / value).quantize(Decimal("0.000001"))
        out[key] = str(need)
        signs.append("positive" if need <= 1 else "negative")

    return {
        "BREAK_EVEN_P_FILL_BAND": out,
        "fillSelectionMaterialToThisAction": len(set(signs)) > 1,
        "isNotAnEstimateOfPFill": (
            "each entry is the fill rate P_FILL would have to CLEAR at "
            "that point of the fill-selection prior. It is not a "
            "forecast of P_FILL, and the band is not a confidence "
            "interval on one -- it is the sensitivity of the "
            "requirement to a term whose direction is unknown"),
        "noSignIsAssumed": (
            "the requirement is reported at an ADVERSE P10 and a "
            "FAVOURABLE P90 alike, because nothing establishes which "
            "way the effect runs on this venue"),
    }


# ── the evaluation ───────────────────────────────────────────────────

def evaluate_action(action: str, market_state: dict | None, *,
                    size=None, fee=None, root=None, band=None,
                    no_fill_context=None) -> dict:
    """Price one canonical action, or say exactly why it cannot be."""
    spec = acts.CANONICAL_ACTIONS.get(action)
    if spec is None:
        return {"action": action, "status": "UNKNOWN_ACTION",
                "declared": list(acts.ACTIONS)}

    m = machinery(root)
    AE = m["action_ev"]
    band = band if band is not None else fill_selection_band(root)

    fv = fair_value(market_state)
    row = {
        "action": action,
        "leg": spec["leg"],
        "aggression": spec["aggression"],
        "what": spec["what"],
        "fairValueKind": fv["kind"],
        "fairValueBasis": fv["basis"],
        # §1: kept on EVERY row, so no reader can mistake a
        # venue-relative number for independent alpha.
        FV_BETTOR_INDEPENDENT: NOT_IDENTIFIED,
        "settlementEv": SETTLEMENT_EV_NOT_IDENTIFIED,
        "researchVocabulary": {
            v: acts.research_action(action, v)
            for v in ("evCore", "actionEv", "positionState")},
    }
    if spec.get("note"):
        row["note"] = spec["note"]

    if fv["fairValue"] == NOT_IDENTIFIED:
        row.update({"expectedNetDollarsPerContract": NOT_IDENTIFIED,
                    "expectedNetDollars": NOT_IDENTIFIED,
                    "status": NOT_IDENTIFIED, "whyNot": fv["why"]})
        return row

    fair = Decimal(fv["fairValue"])
    ms = market_state or {}
    bid, ask = _d(ms.get("bid")), _d(ms.get("ask"))
    gross, how = _gross_vs_venue(action, fair, bid, ask)
    row["grossEdgeBasis"] = how
    row["grossValueVsVenuePrice"] = str(gross) if gross is not None \
        else NOT_IDENTIFIED

    unmet = [r for r in spec.get("requires") or ()
             if r in ("MERGE_MECHANISM_CONFIRMED",
                      "SETTLEMENT_SEMANTICS_RESOLVED", "HEDGE_TAX",
                      "JOINT_FILL_MODEL")]
    if unmet:
        row.update({"expectedNetDollarsPerContract": NOT_IDENTIFIED,
                    "expectedNetDollars": NOT_IDENTIFIED,
                    "status": NOT_IDENTIFIED,
                    "unmetPreconditions": unmet,
                    "whyNot": ("required precondition(s) %s are not "
                               "established" % ", ".join(unmet))})
        return row

    if spec["aggression"] == acts.NON_ORDER:
        row.update({"expectedNetDollarsPerContract": "0",
                    "expectedNetDollars": "0",
                    "status": "IDENTIFIED",
                    "whyIdentified": ("the action touches no book, so "
                                      "its immediate cash effect is "
                                      "exactly zero. That is not a "
                                      "claim about opportunity cost")})
        return row

    fee_d = _d(fee)
    row["feeStatus"] = "IDENTIFIED" if fee_d is not None else NOT_IDENTIFIED
    if fee_d is None:
        row["whyFeeUnidentified"] = (
            "no fee schedule is declared for this venue leg. It is "
            "left unidentified rather than zeroed")

    if spec["aggression"] == acts.AGGRESSIVE:
        # §1. WHAT THIS NUMBER IS: the cost of crossing right now,
        # measured against the venue's own benchmark. WHAT IT IS NOT: a
        # settlement EV. No independent fair value exists, so nothing
        # here says the market is mispriced in either direction, and
        # the action is NOT refuted -- it is priced on one axis and
        # unidentified on the other.
        row.update({
            SNAPSHOT_EXECUTION_COST: (str(gross) if gross is not None
                                      else NOT_IDENTIFIED),
            "executionCostBenchmark": FV_VENUE_IMPLIED,
            "status": "EXECUTION_COST_IDENTIFIED",
            "recommended": False,
            "pFillBasis": ("OBSERVED_DEPTH: crossing fills against "
                           "displayed depth, so no fill probability is "
                           "assumed. Depth beyond the display is never "
                           "invented"),
            "whatThisEstablishes": (
                "crossing the current spread costs this much relative "
                "to the current venue-implied benchmark"),
            "whatThisDoesNotEstablish": (
                "that the settlement EV is negative, that the market "
                "is mispriced, or that BETTOR has directional alpha. "
                "All three would require FV_BETTOR_INDEPENDENT, which "
                "is NOT_IDENTIFIED"),
            "incentivesStatus": NOT_IDENTIFIED,
            "whyIncentivesMatter": (
                "taker incentives and rebates are unverified on this "
                "venue, so even the execution cost is gross of "
                "anything that might offset it"),
        })
        if fee_d is None:
            row.update({"expectedNetDollarsPerContract": NOT_IDENTIFIED,
                        "expectedNetDollars": NOT_IDENTIFIED,
                        "whyNot": ("no fee schedule, so the net "
                                   "execution cost is not identified; "
                                   "the pre-fee figure is reported "
                                   "above")})
            return row
        priced = AE.action_ev(
            acts.research_action(action, "actionEv"),
            price=str(ask if action != "DIRECT_EXIT" else bid),
            size=size, p_fill="1.0",
            expected_value_if_filled=str(gross),
            expected_adverse_selection="0",
            fee=str(fee_d),
            adverse_selection_convention="EMBEDDED_IN_VALUE_CONDITIONAL_ON_FILL",
        )
        per_contract = priced.get("EXPECTED_NET_DOLLARS")
        row.update({
            "expectedNetDollarsPerContract": per_contract,
            "expectedNetDollars": _scaled(per_contract, size),
            "sizeBasis": ("per-contract x SIZE. Where SIZE is not "
                          "identified the order-level figure is not "
                          "identified either"),
            # A CROSS CANNOT BE SELECTED AGAINST: we take displayed
            # depth at a price we choose. EXCLUDED, never 0.0 --
            # excluded says the EV does not carry the term, zero would
            # say it was carried and found nil.
            "fillSelectionConvention": "EXCLUDED",
            "whyFillSelectionExcluded": (
                "a marketable cross consumes displayed depth rather "
                "than resting to be selected against, so the term does "
                "not apply. It is EXCLUDED, not zero"),
        })
        return row

    # PASSIVE. P_FILL has never been observed for BETTOR, so the EV is
    # not identified. The fill-selection term is CARRIED with its width
    # rather than zeroed, and the requirement is published across its
    # band.
    row.update({
        "expectedNetDollarsPerContract": NOT_IDENTIFIED,
        "expectedNetDollars": NOT_IDENTIFIED,
        "status": NOT_IDENTIFIED,
        "missingCriticalTerms": ["P_FILL"],
        "whyNot": ("BETTOR has never rested an order, so P_FILL is not "
                   "identified and cannot be inferred from displayed "
                   "depth, a touch, a price move or whale completion"),
        "fillSelectionConvention": "SEPARATE_TERM",
        "fillSelectionPrior": band,
        "unknownIsNotZero": acts.UNKNOWN_IS_NEVER_ZERO,
        "recommended": False,
        # §14: three outcomes, not two. The no-fill branch is priced
        # rather than assumed away.
        "fillOutcomes": list(FILL_OUTCOMES),
        "noFillRule": NO_FILL_RULE,
    })
    # Every passive action in the CANONICAL table is an entry or a
    # complement acquisition from a book we do not yet hold, so the
    # no-fill branch leaves no NEW exposure. A passive EXIT is priced
    # by the exit engine, which passes PASSIVE_EXIT instead -- the
    # context is a parameter precisely so this file cannot decide it
    # by assumption.
    row.update(no_fill_branch(no_fill_context or ENTRY_FROM_FLAT))
    row.update(_break_even_band(gross, fee_d, band))
    return row


def _scaled(per_contract, size):
    """Order-level dollars, or NOT_IDENTIFIED when size is unknown."""
    if per_contract in (None, NOT_IDENTIFIED):
        return NOT_IDENTIFIED
    qty = _d(size)
    if qty is None:
        return NOT_IDENTIFIED
    return str((Decimal(per_contract) * qty).quantize(Decimal("0.000001")))


def evaluate(market_state: dict | None, *, size=None, fee=None,
             root=None, no_fill_context=None, risk_observed=None,
             risk_state=None) -> dict:
    """The whole canonical action table for one observation."""
    try:
        machinery(root)
    except MachineryUnavailable as exc:
        return {
            "actionEvStatus": MACHINERY_UNAVAILABLE,
            "bestAction": "NO_TRADE",
            "table": [],
            "researchRoot": str(research_root(root)),
            "why": ("the tested EV modules could not be loaded (%s), "
                    "so no action was priced. NO_TRADE here is a "
                    "refusal to act without the engine, not a "
                    "comparison" % exc),
        }

    from . import bettor_risk_engine as risk

    band = fill_selection_band(root)
    table = [evaluate_action(a, market_state, size=size, fee=fee,
                             root=root, band=band,
                             no_fill_context=no_fill_context)
             for a in acts.ACTIONS]

    # §18. THE RISK GATE IS CONSULTED HERE, not left as a module nobody
    # calls -- which is the exact failure this whole integration
    # started by fixing. Risk and economics are separate verdicts on
    # every row: an action can be economically unidentified AND risk
    # blocked, and collapsing them would lose which one to fix.
    for row in table:
        verdict = risk.evaluate(row["action"], observed=risk_observed,
                                state=risk_state)
        row["risk"] = {
            "direction": verdict["direction"],
            "permitted": verdict["permitted"],
            "grossExposure": verdict["grossExposure"],
            "directionalExposure": verdict["directionalExposure"],
            "railsNotPassed": verdict["railsNotPassed"],
            "gatesNotPassed": verdict["gatesNotPassed"],
            "why": verdict["why"],
        }

    cost_identified = [r["action"] for r in table
                       if r.get("status") == "EXECUTION_COST_IDENTIFIED"]
    identified = [r for r in table
                  if r.get("status") == "IDENTIFIED"
                  and r.get("expectedNetDollarsPerContract")
                  not in (None, NOT_IDENTIFIED)]
    unidentified = [r["action"] for r in table
                    if r.get("status") in (NOT_IDENTIFIED, None)]

    return {
        # NO SETTLEMENT EV IS IDENTIFIED FOR ANY ACTION. Execution cost
        # is, for the aggressive ones, and the two are different facts.
        "actionEvStatus": NOT_IDENTIFIED,
        "settlementEvStatus": SETTLEMENT_EV_NOT_IDENTIFIED,
        "executionCostIdentifiedFor": cost_identified,
        "bestAction": "NO_TRADE",
        "table": table,
        "identifiedCount": len(identified),
        "unidentifiedActions": unidentified,
        "fairValueSeparation": FAIR_VALUE_SEPARATION,
        FV_BETTOR_INDEPENDENT: NOT_IDENTIFIED,
        "fillSelectionPrior": band,
        "riskPermittedActions": [r["action"] for r in table
                                 if r["risk"]["permitted"]],
        "riskBlockedActions": [r["action"] for r in table
                               if not r["risk"]["permitted"]],
        "whyNoTrade": (
            "no action has an identified settlement EV, because that "
            "would require an independent fair value and none is "
            "validated. Separately, every exposure-increasing action "
            "is gated: mirror_live is false and no order path exists. "
            "NO_TRADE is the conclusion of the comparison above, not a "
            "substitute for making it"),
        "engineProvenance": {
            "loadedFrom": str(research_root(root)),
            "modules": list(REQUIRED_MODULES),
            "notCopied": ("imported by path so the tested "
                          "implementation is the one that runs"),
        },
    }


def describe() -> dict:
    return {
        "purpose": "wire the built EV machinery into the decision lane",
        "machineryAvailable": available(),
        "researchRoot": str(research_root()),
        "researchRootEnv": RESEARCH_ROOT_ENV,
        "requiredModules": list(REQUIRED_MODULES),
        "fairValueSeparation": FAIR_VALUE_SEPARATION,
        "actionTable": acts.describe(),
        "whatItDoesNotDo": (
            "it does not open any gate, submit, size or fund anything. "
            "There is no order path in this module or below it"),
    }
