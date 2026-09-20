"""WIRING THE BUILT EV MACHINERY INTO THE PRODUCTION DECISION LANE.

Owner directive, "RETURN TO THE ACTUAL BETTOR EV ENGINE" §0:

    "Before writing new modules, inventory the current repository...
    Do not create duplicate engines because an existing module is
    imperfect... Then immediately implement the missing integration. Do
    not stop after producing an audit document."

WHAT WAS ACTUALLY MISSING. Not the engine. `research/beta48/shadow/`
contains `action_ev.py`, `action_ev_mc.py`, `ev_core.py`,
`inventory_state.py`, `position_state.py` and `maker_fill.py` -- all
tested, all importable, all correct. What did not exist was any import
of them from `backend/`. The production lane
(`shadow_bettor.decide`) wrote 4,503 rows of NO_TRADE with
`total_action_ev` NULL while a working action-EV calculator sat in the
same repository, unreferenced. That gap is what this module closes.

IT IS LOADED BY PATH, DELIBERATELY. `calibration_domain.py` already
does this for `run_census` and `venue_domain`, and states the reason:
"they are the tested implementations. Importing them by path is
deliberate: the alternative is a copy, and a copy drifts." The same
reasoning applies here with more force, because these modules carry
frozen refusal semantics that a copy would silently soften.

WHAT THIS CHANGES ABOUT THE ANSWER, AND WHAT IT DOES NOT.

It does NOT open the gate. Every exposure-increasing action remains
unavailable; `mirror_live` is false and no order path exists here or
anywhere below. What changes is the QUALITY of the refusal. Before,
NO_TRADE was asserted -- the lane had no EV and said so. Now NO_TRADE
is DERIVED: every canonical action is priced or explicitly refused by
name, and the row records which.

THE TWO RESULTS THAT NEED NO PRIORS AT ALL. This is the whole reason
the integration is worth doing before any model is validated:

1. AGGRESSIVE ACTIONS ARE IDENTIFIED, AND NEGATIVE. The validated
   champion fair value is the venue's own price (`ev_core`:
   MODEL_VERSION = B0_VENUE_PRICE -- no model in the zoo beat it out of
   sample). Crossing the spread against that champion pays half the
   spread and receives nothing for it. So TAKE_YES, TAKE_NO,
   DIRECT_EXIT and TAKE_COMPLEMENT carry a REAL NEGATIVE NUMBER, not a
   NOT_IDENTIFIED. That is a decision-grade finding available today.

2. PASSIVE ACTIONS YIELD A REFUTABLE LOWER BOUND. P_FILL has never
   been observed for BETTOR, and adverse selection is unmeasured, so
   the EV is genuinely NOT_IDENTIFIED. But adverse selection cannot be
   NEGATIVE -- being filled is never systematically GOOD news. So
   setting it to zero yields the most generous case that arithmetic
   permits, and the fill rate that breaks even there is a LOWER BOUND
   on the fill rate the action truly needs:

       BREAK_EVEN_P_FILL_LOWER_BOUND = FEE / GROSS_VALUE_IF_FILL

   If that bound exceeds 1.0, NO fill rate saves the action and it is
   REFUTED outright -- with no prior, no assumption and no invented
   term. If it is 0.02, the action is worth researching. Either way the
   unknown becomes a question with a number attached instead of a dead
   end.

   THE BOUND IS NOT AN ESTIMATE OF P_FILL. It is the threshold P_FILL
   must clear, computed under the most favourable admissible
   assumption. The true requirement is higher by however much adverse
   selection actually is. Treating the bound as a forecast would be the
   exact error the refusal semantics exist to prevent.

UNKNOWN NEVER BECOMES ZERO (§1). The single exception above is not an
exception to that rule: zero is not substituted for the unknown, it is
used as a DIRECTIONAL BOUND on it, the direction is argued from the
sign of the quantity, and every field carrying the result says
LOWER_BOUND in its name.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import bettor_ev_actions as acts

NOT_IDENTIFIED = "NOT_IDENTIFIED"
MACHINERY_UNAVAILABLE = "EV_MACHINERY_UNAVAILABLE"
# TWO DIFFERENT REFUTATIONS, KEPT APART BY NAME. A passive action dies
# because no fill rate could save it; an aggressive action has no fill
# rate at all -- it dies because the edge is already negative before a
# single cost is counted. One word for both would hide which.
REFUTED = "REFUTED_NO_ADMISSIBLE_FILL_RATE"
REFUTED_BEFORE_COSTS = "REFUTED_NEGATIVE_BEFORE_COSTS"
REFUTATIONS = (REFUTED, REFUTED_BEFORE_COSTS)

# The research modules this bridge needs. Named explicitly so a missing
# one is a named failure rather than an AttributeError halfway through
# pricing.
REQUIRED_MODULES = ("action_ev", "inventory_state", "ev_core")

_CACHE: dict = {}


class MachineryUnavailable(RuntimeError):
    """The research EV modules could not be loaded from the repository."""


def _repo_root(root=None) -> Path:
    if root is not None:
        return Path(root)
    # backend/sportsassets/bettor_ev_bridge.py -> repo root
    return Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise MachineryUnavailable("cannot load %s from %s" % (name, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


def machinery(root=None) -> dict:
    """The tested EV modules, imported by path rather than copied."""
    key = str(_repo_root(root))
    if key in _CACHE:
        return _CACHE[key]
    shadow = _repo_root(root) / "research" / "beta48" / "shadow"
    if not shadow.is_dir():
        raise MachineryUnavailable("no EV machinery at %s" % shadow)
    if str(shadow) not in sys.path:
        sys.path.insert(0, str(shadow))
    try:
        got = {n: _load(n, shadow / ("%s.py" % n)) for n in REQUIRED_MODULES}
    except MachineryUnavailable:
        raise
    except Exception as exc:                                   # noqa: BLE001
        raise MachineryUnavailable(
            "%s: %s" % (type(exc).__name__, exc)) from exc
    _CACHE[key] = got
    return got


def available(root=None) -> bool:
    try:
        machinery(root)
        return True
    except MachineryUnavailable:
        return False


# ── reading the book, refusing to guess ──────────────────────────────

def _d(v):
    if v is None or v == "" or v == NOT_IDENTIFIED:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def fair_value(market_state: dict | None) -> dict:
    """The champion fair value, which is the venue's own price.

    `ev_core` states the finding this rests on: no model in the zoo
    beat the venue price out of sample, so the champion IS the market.
    That makes the fair value available today and makes every spread
    crossing a measurable loss against it.
    """
    ms = market_state or {}
    bid, ask, mid = _d(ms.get("bid")), _d(ms.get("ask")), _d(ms.get("mid"))
    if mid is None and bid is not None and ask is not None:
        mid = (bid + ask) / Decimal("2")
    if mid is None:
        return {"fairValue": NOT_IDENTIFIED, "basis": "NO_READABLE_BOOK",
                "why": ("neither a mid nor a two-sided book was "
                        "available, so no fair value exists to price "
                        "any action against")}
    return {
        "fairValue": str(mid),
        "basis": "B0_VENUE_PRICE",
        "modelVersion": "B0_VENUE_PRICE",
        "why": ("the validated champion is the venue's own price: no "
                "model in the zoo beat it out of sample. This is a "
                "measured benchmark, not a BETTOR belief"),
    }


def _gross_value_if_fill(action: str, fair: Decimal, bid, ask):
    """Book-derived gross edge, before any unmeasured term.

    AGGRESSIVE: we pay the far side, so the edge is fair - ask (buying)
    or bid - fair (selling). Both are negative by construction whenever
    the champion is the mid, and that negative is a real number.

    PASSIVE: we rest at the near side and, IF filled, capture fair -
    bid. That figure is GROSS -- it excludes adverse selection and
    fill selection, both unmeasured, which is exactly why the passive
    EV stays NOT_IDENTIFIED and only a bound is emitted.
    """
    spec = acts.CANONICAL_ACTIONS.get(action) or {}
    aggression = spec.get("aggression")
    if aggression == acts.NON_ORDER:
        return None, "action touches no book"
    if aggression == acts.AGGRESSIVE:
        if action in ("DIRECT_EXIT",):
            if bid is None:
                return None, "no bid to sell into"
            return bid - fair, "sell the held leg at the bid"
        if ask is None:
            return None, "no ask to buy from"
        return fair - ask, "cross and pay the ask"
    if bid is None:
        return None, "no bid to rest at"
    return fair - bid, "rest at the bid and capture the near side if filled"


def _scaled(per_contract, size):
    """Order-level dollars, or NOT_IDENTIFIED when size is not known.

    An unknown size does not become 1. It makes the ORDER-LEVEL figure
    unidentified while leaving the per-contract economics -- which is
    the part the book actually determines -- fully reported.
    """
    if per_contract in (None, NOT_IDENTIFIED):
        return NOT_IDENTIFIED
    qty = _d(size)
    if qty is None:
        return NOT_IDENTIFIED
    return str((Decimal(per_contract) * qty).quantize(Decimal("0.000001")))


def _break_even_lower_bound(gross, fee):
    """FEE / GROSS, the most generous admissible break-even fill rate.

    Adverse selection cannot be negative, so zeroing it can only make
    the action look BETTER. The fill rate that breaks even here is
    therefore a LOWER BOUND on the fill rate genuinely required. A
    bound above 1.0 refutes the action with no prior at all.
    """
    if gross is None or gross <= 0:
        return {
            "BREAK_EVEN_P_FILL_LOWER_BOUND": REFUTED,
            "refuted": True,
            "why": ("the gross edge is not positive even before any "
                    "cost or unmeasured term, so no fill rate makes "
                    "the action pay"),
        }
    if fee is None:
        return {"BREAK_EVEN_P_FILL_LOWER_BOUND": NOT_IDENTIFIED,
                "refuted": False,
                "why": "no fee schedule is identified for this venue leg"}
    bound = (fee / gross).quantize(Decimal("0.000001"))
    return {
        "BREAK_EVEN_P_FILL_LOWER_BOUND": str(bound),
        "refuted": bound > 1,
        "isNotAnEstimateOfPFill": (
            "this is the threshold P_FILL must CLEAR under the most "
            "favourable admissible assumption (adverse selection = 0), "
            "not a forecast of P_FILL. The true requirement is higher "
            "by however much adverse selection actually is"),
        "why": ("adverse selection cannot be negative, so setting it "
                "to zero bounds the requirement from below"),
    }


# ── the evaluation ───────────────────────────────────────────────────

def evaluate_action(action: str, market_state: dict | None, *,
                    size=None, fee=None, root=None) -> dict:
    """Price one canonical action, or say exactly why it cannot be priced."""
    spec = acts.CANONICAL_ACTIONS.get(action)
    if spec is None:
        return {"action": action, "status": "UNKNOWN_ACTION",
                "declared": list(acts.ACTIONS)}

    m = machinery(root)
    AE = m["action_ev"]

    fv = fair_value(market_state)
    row = {
        "action": action,
        "leg": spec["leg"],
        "aggression": spec["aggression"],
        "what": spec["what"],
        "fairValueBasis": fv["basis"],
        "researchVocabulary": {
            v: acts.research_action(action, v)
            for v in ("evCore", "actionEv", "positionState")},
    }
    if spec.get("note"):
        row["note"] = spec["note"]

    if fv["fairValue"] == NOT_IDENTIFIED:
        row.update({"expectedNetDollars": NOT_IDENTIFIED,
                    "status": NOT_IDENTIFIED, "whyNot": fv["why"]})
        return row

    fair = Decimal(fv["fairValue"])
    ms = market_state or {}
    bid, ask = _d(ms.get("bid")), _d(ms.get("ask"))
    gross, how = _gross_value_if_fill(action, fair, bid, ask)
    row["grossEdgeBasis"] = how
    row["grossValueIfFill"] = str(gross) if gross is not None \
        else NOT_IDENTIFIED

    # Actions with requirements the repository cannot satisfy are
    # refused BY NAME. MERGE has no confirmed venue mechanism;
    # HOLD_TO_SETTLEMENT has unresolved settlement prose. Naming the
    # missing precondition is the finding.
    unmet = [r for r in spec.get("requires") or ()
             if r in ("MERGE_MECHANISM_CONFIRMED",
                      "SETTLEMENT_SEMANTICS_RESOLVED", "HEDGE_TAX",
                      "JOINT_FILL_MODEL")]
    if unmet:
        row.update({"expectedNetDollars": NOT_IDENTIFIED,
                    "status": NOT_IDENTIFIED,
                    "unmetPreconditions": unmet,
                    "whyNot": ("required precondition(s) %s are not "
                               "established in this repository"
                               % ", ".join(unmet))})
        return row

    if spec["aggression"] == acts.NON_ORDER:
        row.update({"expectedNetDollarsPerContract": "0",
                    "expectedNetDollars": "0",
                    "status": "IDENTIFIED",
                    "whyIdentified": ("the action touches no book, so "
                                      "its immediate cash effect is "
                                      "exactly zero. That is not a "
                                      "claim about its opportunity "
                                      "cost, which is the comparison "
                                      "the table performs")})
        return row

    # NO FEE SCHEDULE IS DECLARED ANYWHERE IN THIS REPOSITORY, so the
    # fee is NOT_IDENTIFIED and is NOT defaulted to zero. A zero fee
    # would make every break-even fill rate 0.000000 -- "any fill rate
    # whatsoever makes this pay" -- which is the precise failure §1
    # names: the unmeasured term read as zero produces a confident
    # result built out of what nobody measured.
    fee_d = _d(fee)
    row["feeStatus"] = ("IDENTIFIED" if fee_d is not None
                        else NOT_IDENTIFIED)
    if fee_d is None:
        row["whyFeeUnidentified"] = (
            "no fee schedule is declared for this venue leg. It is "
            "left unidentified rather than zeroed, and the bounds "
            "below use only the fact that a fee cannot be negative")

    if spec["aggression"] == acts.AGGRESSIVE:
        # THE FILL IS OBSERVED, NOT PROBABILISTIC. We cross into depth
        # that the book displays, so P_FILL is 1 against that depth --
        # this is not an assumption, it is what crossing means. Depth
        # beyond what is displayed is NOT available and is never
        # invented.
        if fee_d is None:
            # THE BOUND NEEDS NO FEE SCHEDULE. A fee cannot be
            # negative, so NET <= GROSS always. When the gross edge is
            # already negative -- which it is whenever the champion is
            # the mid, because crossing pays half the spread and
            # receives nothing for it -- the action is refuted before
            # costs and no fee schedule could rescue it.
            row.update({
                "expectedNetDollarsPerContract": NOT_IDENTIFIED,
                "expectedNetDollars": NOT_IDENTIFIED,
                "evUpperBoundPerContract": (str(gross) if gross is not None
                                            else NOT_IDENTIFIED),
                "evUpperBoundBasis": (
                    "a fee cannot be negative, so NET <= GROSS. This "
                    "is a bound on the EV, not an estimate of it"),
                "recommended": False,
                "pFillBasis": ("OBSERVED_DEPTH: crossing fills against "
                               "displayed depth, so no fill "
                               "probability is assumed"),
            })
            if gross is not None and gross <= 0:
                row.update({
                    "status": REFUTED_BEFORE_COSTS,
                    "whyNot": ("the gross edge is negative before any "
                               "cost: crossing pays the far side "
                               "against a champion fair value that is "
                               "the venue's own price. No fee "
                               "schedule can make this positive, so "
                               "the action is refuted without one"),
                })
            else:
                row.update({"status": NOT_IDENTIFIED,
                            "whyNot": ("the gross edge is positive but "
                                       "no fee schedule is declared, "
                                       "so the net is not identified")})
            return row

        priced = AE.action_ev(
            acts.research_action(action, "actionEv"),
            price=str(ask if action != "DIRECT_EXIT" else bid),
            size=size, p_fill="1.0",
            expected_value_if_filled=str(gross) if gross is not None else None,
            expected_adverse_selection="0",
            fee=str(fee_d),
            adverse_selection_convention="EMBEDDED_IN_VALUE_CONDITIONAL_ON_FILL",
        )
        per_contract = priced.get("EXPECTED_NET_DOLLARS")
        row.update({
            # PER CONTRACT, AND SAID SO. `action_ev` prices one
            # contract and does not multiply by size; a field called
            # "expectedNetDollars" carrying a per-contract figure while
            # a size sits beside it in the same row is a units error
            # waiting to be read as an order-level number.
            "expectedNetDollarsPerContract": per_contract,
            "expectedNetDollars": _scaled(per_contract, size),
            "sizeBasis": ("EXPECTED_NET_DOLLARS = per-contract figure "
                          "x SIZE. Where SIZE is not identified the "
                          "order-level figure is not identified "
                          "either, and only the per-contract number is "
                          "reported"),
            "status": ("IDENTIFIED"
                       if per_contract != NOT_IDENTIFIED
                       else NOT_IDENTIFIED),
            "recommended": False,
            "pFillBasis": ("OBSERVED_DEPTH: crossing fills against "
                           "displayed depth, so no fill probability is "
                           "assumed. Depth beyond the display is not "
                           "available and is never invented"),
            "whyNotRecommended": (
                "no exposure-increasing action is available: "
                "mirror_live is false and no order path exists"),
        })
        return row

    # PASSIVE. P_FILL has never been observed for BETTOR and adverse
    # selection is unmeasured, so the EV is genuinely not identified.
    # The bound is what can honestly be said instead.
    bound = _break_even_lower_bound(gross, fee_d)
    row.update({
        "expectedNetDollarsPerContract": NOT_IDENTIFIED,
        "expectedNetDollars": NOT_IDENTIFIED,
        "status": NOT_IDENTIFIED,
        "missingCriticalTerms": ["P_FILL", "EXPECTED_ADVERSE_SELECTION"],
        "whyNot": ("BETTOR has never rested an order, so P_FILL is not "
                   "identified, and adverse selection cannot be "
                   "conditioned on fills that do not exist"),
        "unknownIsNotZero": acts.UNKNOWN_IS_NEVER_ZERO,
        "recommended": False,
    })
    row.update(bound)
    if bound.get("refuted"):
        row["status"] = REFUTED
    return row


def evaluate(market_state: dict | None, *, size=None, fee=None,
             root=None) -> dict:
    """The whole canonical action table for one observation.

    Returns the table plus the derived best action. NO_TRADE is still
    the answer, but it is now the CONCLUSION of a comparison rather
    than a statement that no comparison was possible.
    """
    try:
        machinery(root)
    except MachineryUnavailable as exc:
        return {
            "actionEvStatus": MACHINERY_UNAVAILABLE,
            "bestAction": "NO_TRADE",
            "table": [],
            "why": ("the tested EV modules could not be loaded (%s), "
                    "so no action was priced. NO_TRADE here is a "
                    "refusal to act without the engine, not a "
                    "comparison" % exc),
        }

    table = [evaluate_action(a, market_state, size=size, fee=fee, root=root)
             for a in acts.ACTIONS]

    # Identified on the PER-CONTRACT figure, which is what the book
    # determines. An unknown order size must not make a known economic
    # result disappear from the audit.
    identified = [r for r in table
                  if r.get("status") == "IDENTIFIED"
                  and r.get("expectedNetDollarsPerContract")
                  not in (None, NOT_IDENTIFIED)]
    refuted = [r["action"] for r in table
               if r.get("status") in REFUTATIONS]
    unidentified = [r["action"] for r in table
                    if r.get("status") in (NOT_IDENTIFIED, None)]

    # THE GATE STAYS SHUT, AND FOR A STATED REASON. Even an action with
    # a positive identified EV would not be taken: no order path
    # exists and mirror_live is false. The comparison is recorded so
    # that when the gate does open, the row written today is still
    # readable as what BETTOR believed at the time.
    best = "NO_TRADE"
    positives = [r for r in identified
                 if r["action"] in acts.EXPOSURE_INCREASING
                 and Decimal(r["expectedNetDollarsPerContract"]) > 0]

    return {
        "actionEvStatus": ("PARTIALLY_IDENTIFIED" if identified
                           else NOT_IDENTIFIED),
        "bestAction": best,
        "table": table,
        "identifiedCount": len(identified),
        "refutedActions": refuted,
        "unidentifiedActions": unidentified,
        "positiveExposureIncreasingActions": [r["action"] for r in positives],
        "whyNoTrade": (
            "every exposure-increasing action is either refuted, not "
            "identified, or gated: mirror_live is false and no order "
            "path exists in this lane. NO_TRADE is the conclusion of "
            "the comparison above, not a substitute for making it"),
        "engineProvenance": {
            "loadedFrom": "research/beta48/shadow",
            "modules": list(REQUIRED_MODULES),
            "notCopied": ("imported by path so the tested "
                          "implementation is the one that runs; a copy "
                          "would drift from the module the tests "
                          "cover"),
        },
    }


def describe() -> dict:
    return {
        "purpose": ("wire the built EV machinery into the production "
                    "decision lane"),
        "machineryAvailable": available(),
        "requiredModules": list(REQUIRED_MODULES),
        "actionTable": acts.describe(),
        "whatItDoesNotDo": (
            "it does not open any gate, submit, size, or fund "
            "anything. There is no order path in this module or below "
            "it, and mirror_live is false"),
    }
