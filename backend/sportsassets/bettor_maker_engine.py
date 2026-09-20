"""§6. THE MAKER ENGINE. MAKER-FIRST IS NOT MAKER-ALWAYS.

Owner directive, "FLAT-STATE PROOF IS APPROVED" §6:

    "For every FLAT market evaluate MAKE_YES / MAKE_NO / MAKE_BOTH /
    TAKE_YES / TAKE_NO / NO_TRADE. Maker-first does NOT mean
    maker-always. Compute the components separately: GROSS_SPREAD_
    CAPTURE, P_FILL, PARTIAL_FILL_DISTRIBUTION, VALUE_IF_FILL,
    VALUE_IF_NO_FILL, FILL_SELECTION_EFFECT, ADVERSE_SELECTION, REBATE,
    INCENTIVE, FEE, TOXICITY, RESIDUAL_INVENTORY_COST, CAPITAL_HOURS.
    Do not combine unidentified terms into zero. MAKE_BOTH requires a
    JOINT fill model. Do not calculate EV(MAKE_BOTH) = EV(MAKE_YES) +
    EV(MAKE_NO). That recreates the Ferrari residual mistake."

THE COMPONENTS ARE THE PRODUCT. A single EV number per action hides
which term is carrying it and which term is missing, and an engine that
reports one number cannot be argued with. Here every action returns a
COMPONENT TABLE first and a total only if the table earns one.

────────────────────────────────────────────────────────────────────
UNIDENTIFIED DOES NOT COMBINE INTO ZERO.

    NOT_IDENTIFIED + 0.004 is NOT 0.004.

Treating a missing term as zero is not conservatism, it is a claim --
the claim that the term is zero -- and it is the claim most likely to
be wrong in the favourable direction, because the terms we have not
measured are mostly costs. So a total is produced only when every
REQUIRED component is identified. Otherwise the action is
NOT_IDENTIFIED and the table names which terms are missing.

An OPTIONAL component (REBATE, INCENTIVE) that is NOT_IDENTIFIED does
not block a total, because its absence is a known fact about this venue
rather than an unmeasured quantity -- and it is recorded as ABSENT, not
as zero, so a venue that later pays a rebate does not look like a
change in the model.
────────────────────────────────────────────────────────────────────

MAKE_BOTH IS NOT TWO MAKE ONES. Resting on both legs does not have the
expected value of resting on each, because the outcomes are not
independent and the bad one is not symmetric:

    both fill    -> a pair, the intended outcome
    neither      -> nothing, cost is time
    ONE fills    -> a RESIDUAL: directional inventory we did not want,
                    which must be hedged, exited or carried

Adding EV(MAKE_YES) to EV(MAKE_NO) prices the first two cases and
silently assigns the third the average of the other two. That is
exactly the Ferrari mistake -- pair economics excellent, residual
economics terrible -- and it is refused in code rather than in a
comment: `evaluate_entry` has no path that sums two single-leg totals,
and `additive_composition_refused()` states why for any reader who
looks for one.

MAKER-FIRST IS A PRIOR, NOT A RULE. The engine prices passive and
aggressive actions on the same footing and ranks them by what the
tables say. Nothing here gives a maker action a bonus, and a taker
action wins whenever its table is better -- which is the only way to
learn that maker-first was right.

TODAY EVERY ENTRY ACTION IS NOT_IDENTIFIED, and that is the correct
output rather than a failure: P_FILL is NOT_IDENTIFIED until BETTOR-
native fills exist, and no maker action can be priced without it. The
engine exists now so that the day P_FILL arrives, the decision is
already wired and does not have to be invented under pressure.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from . import bettor_applicability as applic
from . import bettor_ev_actions as acts
from . import bettor_ev_bridge as evb
from . import bettor_p_fill as pf

NOT_IDENTIFIED = "NOT_IDENTIFIED"
ABSENT = "ABSENT_AT_THIS_VENUE"

ENGINE = "BETTOR_MAKER_ENGINE_V1"

# ── §6: the action set evaluated from FLAT ───────────────────────────

ENTRY_ACTIONS = ("MAKE_YES", "MAKE_NO", "MAKE_BOTH",
                 "TAKE_YES", "TAKE_NO", "NO_TRADE")

MAKER_FIRST_IS_NOT_MAKER_ALWAYS = (
    "passive and aggressive actions are priced on the same footing and "
    "ranked by what their component tables say. Nothing gives a maker "
    "action a bonus, and a taker action wins whenever its table is "
    "better -- which is the only way to learn whether maker-first was "
    "right")

# ── §6: the thirteen components, each computed separately ────────────

COMPONENTS = (
    "GROSS_SPREAD_CAPTURE",
    "P_FILL",
    "PARTIAL_FILL_DISTRIBUTION",
    "VALUE_IF_FILL",
    "VALUE_IF_NO_FILL",
    "FILL_SELECTION_EFFECT",
    "ADVERSE_SELECTION",
    "REBATE",
    "INCENTIVE",
    "FEE",
    "TOXICITY",
    "RESIDUAL_INVENTORY_COST",
    "CAPITAL_HOURS",
)

# ── KINDS, BECAUSE A PROBABILITY IS NOT A DOLLAR ─────────────────────
#
# The components do not live in one unit, so they cannot be added. The
# earlier version of this engine summed every identified row and
# produced 0.917 dollars per contract for a table whose largest entry
# was a 0.900 PROBABILITY. Declaring the kind makes that a refusal
# rather than a plausible-looking number.

PROBABILITY = "PROBABILITY"
DISTRIBUTION = "DISTRIBUTION_OVER_FILLED_QUANTITY"
DOLLARS_IF_FILL = "DOLLARS_PER_CONTRACT_CONDITIONAL_ON_FILL"
DOLLARS_IF_NO_FILL = "DOLLARS_PER_CONTRACT_CONDITIONAL_ON_NO_FILL"
DOLLARS_UNCONDITIONAL = "DOLLARS_PER_CONTRACT_UNCONDITIONAL"

COMPONENT_KIND = {
    "GROSS_SPREAD_CAPTURE": DOLLARS_IF_FILL,
    "P_FILL": PROBABILITY,
    "PARTIAL_FILL_DISTRIBUTION": DISTRIBUTION,
    "VALUE_IF_FILL": DOLLARS_IF_FILL,
    "VALUE_IF_NO_FILL": DOLLARS_IF_NO_FILL,
    "FILL_SELECTION_EFFECT": DOLLARS_IF_FILL,
    "ADVERSE_SELECTION": DOLLARS_IF_FILL,
    "REBATE": DOLLARS_IF_FILL,
    "INCENTIVE": DOLLARS_IF_FILL,
    "FEE": DOLLARS_IF_FILL,
    "TOXICITY": DOLLARS_IF_FILL,
    "RESIDUAL_INVENTORY_COST": DOLLARS_IF_FILL,
    "CAPITAL_HOURS": DOLLARS_UNCONDITIONAL,
}

A_PROBABILITY_IS_NOT_A_DOLLAR = (
    "the components do not share a unit, so they cannot be added. An "
    "earlier version summed every identified row and reported 0.917 "
    "dollars per contract for a table whose largest entry was a 0.900 "
    "PROBABILITY. Each component declares its kind and the combination "
    "refuses to add across kinds")

# THE COMBINATION IS A DECLARED MODELLING CHOICE, NOT A MEASUREMENT.
# It is written here rather than left implicit in the arithmetic so it
# can be argued with.
COMBINATION_RULE = {
    "PASSIVE": ("P_FILL * sum(DOLLARS_IF_FILL) + (1 - P_FILL) * "
                "VALUE_IF_NO_FILL + sum(DOLLARS_UNCONDITIONAL)"),
    "AGGRESSIVE": ("sum(DOLLARS_IF_FILL) + sum(DOLLARS_UNCONDITIONAL). "
                   "A crossing order fills by construction against "
                   "observed depth, so there is no no-fill branch to "
                   "weight and P_FILL does not enter"),
    "NON_ORDER": "exactly 0; no book is touched",
    "PARTIAL_FILL_DISTRIBUTION": (
        "scales expected filled QUANTITY, not per-contract value, so it "
        "is never summed into a per-contract total. It is required for "
        "a passive action because expected size is not known without "
        "it, and it enters at the sizing step"),
    "isAModellingChoice": (
        "this decomposition is declared, not measured. A different "
        "decomposition is arguable; an undeclared one is not"),
}

# Which components a total cannot be produced without, by aggression.
# A taker does not need P_FILL -- it crosses -- and does not create a
# residual on a single leg, so its required set is genuinely smaller.
REQUIRED_FOR_PASSIVE = (
    "GROSS_SPREAD_CAPTURE", "P_FILL", "PARTIAL_FILL_DISTRIBUTION",
    "VALUE_IF_FILL", "VALUE_IF_NO_FILL", "FILL_SELECTION_EFFECT",
    "ADVERSE_SELECTION", "FEE", "TOXICITY", "RESIDUAL_INVENTORY_COST",
    "CAPITAL_HOURS",
)
REQUIRED_FOR_AGGRESSIVE = (
    "GROSS_SPREAD_CAPTURE", "VALUE_IF_FILL", "ADVERSE_SELECTION", "FEE",
    "TOXICITY", "CAPITAL_HOURS",
)
# Absence is a fact about the venue, not an unmeasured quantity.
OPTIONAL_COMPONENTS = ("REBATE", "INCENTIVE")

UNIDENTIFIED_DOES_NOT_COMBINE_INTO_ZERO = (
    "NOT_IDENTIFIED + 0.004 is not 0.004. Treating a missing term as "
    "zero is the claim that the term is zero, and it is the claim most "
    "likely to be wrong in the favourable direction, because the terms "
    "we have not measured are mostly costs. A total is produced only "
    "when every required component is identified")

OPTIONAL_ABSENCE_IS_NOT_ZERO = (
    "REBATE and INCENTIVE are recorded ABSENT_AT_THIS_VENUE rather than "
    "0, so a venue that later pays a rebate shows up as a change in the "
    "venue rather than as a change in the model")

# ── the refusal that keeps Ferrari from happening again ──────────────

ADDITIVE_COMPOSITION_REFUSED = (
    "EV(MAKE_BOTH) is NOT EV(MAKE_YES) + EV(MAKE_NO). Resting on both "
    "legs has three outcomes, not two: both fill (a pair), neither "
    "fills (cost is time), or ONE fills -- a RESIDUAL, directional "
    "inventory we did not want, which must be hedged, exited or "
    "carried. Adding the single-leg totals prices the first two cases "
    "and silently gives the third the average of the other two. That is "
    "the Ferrari mistake: pair economics excellent, residual economics "
    "terrible")

JOINT_FILL_MODEL_REQUIRED = (
    "MAKE_BOTH needs P(both fill), P(neither), P(YES only) and P(NO "
    "only) as a JOINT distribution. Two marginal fill probabilities do "
    "not give those four numbers without an independence assumption "
    "that the order book does not support -- the same flow that fills "
    "one leg moves the other")


def _d(v):
    if v is None or v == "" or v in (NOT_IDENTIFIED, ABSENT):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _component(name, value, *, why=None, source=None, optional=False):
    kind = COMPONENT_KIND[name]
    d = _d(value)
    if d is not None:
        return {"component": name, "value": str(d), "kind": kind,
                "status": "IDENTIFIED", "source": source or NOT_IDENTIFIED}
    return {
        "component": name,
        "value": ABSENT if (optional and value == ABSENT) else NOT_IDENTIFIED,
        "kind": kind,
        "status": ABSENT if (optional and value == ABSENT)
                  else NOT_IDENTIFIED,
        "why": why or "not established",
        "source": source or NOT_IDENTIFIED,
        "notZero": UNIDENTIFIED_DOES_NOT_COMBINE_INTO_ZERO,
    }


def combine(table: dict) -> dict:
    """Per-contract expected value from a component table, by KIND.

    Refuses to add across kinds. An absent OPTIONAL dollar term
    contributes nothing -- which is the one place absence and zero
    coincide, because the venue genuinely pays nothing.
    """
    if table.get("componentsMissing"):
        return {"status": NOT_IDENTIFIED,
                "expectedNetDollarsPerContract": NOT_IDENTIFIED,
                "componentsMissing": list(table["componentsMissing"]),
                "whyNotZero": UNIDENTIFIED_DOES_NOT_COMBINE_INTO_ZERO}

    by = {c["component"]: c for c in table["components"]}
    agg = table["aggression"]

    def dollars(kind):
        return [c for c in table["components"]
                if c["kind"] == kind and c["status"] == "IDENTIFIED"]

    if_fill = dollars(DOLLARS_IF_FILL)
    uncond = dollars(DOLLARS_UNCONDITIONAL)
    fill_sum = sum(Decimal(c["value"]) for c in if_fill)
    uncond_sum = sum(Decimal(c["value"]) for c in uncond)

    if agg == acts.AGGRESSIVE:
        total = fill_sum + uncond_sum
        used = [c["component"] for c in if_fill + uncond]
    else:
        p = _d(by["P_FILL"]["value"])
        no_fill = _d(by["VALUE_IF_NO_FILL"]["value"])
        total = p * fill_sum + (Decimal(1) - p) * no_fill + uncond_sum
        used = ["P_FILL", "VALUE_IF_NO_FILL"] + [
            c["component"] for c in if_fill + uncond]

    return {
        "status": "IDENTIFIED",
        "expectedNetDollarsPerContract": str(total),
        "combinationRule": COMBINATION_RULE[agg],
        "componentsUsed": used,
        "componentsNotSummed": [
            c["component"] for c in table["components"]
            if c["kind"] in (PROBABILITY, DISTRIBUTION)],
        "aProbabilityIsNotADollar": A_PROBABILITY_IS_NOT_A_DOLLAR,
        "partialFillDistributionEntersAtSizing":
            COMBINATION_RULE["PARTIAL_FILL_DISTRIBUTION"],
        "isAModellingChoice": COMBINATION_RULE["isAModellingChoice"],
    }


def components(action: str, market_state: dict | None = None, *,
               root=None, fee=None, rebate=ABSENT, incentive=ABSENT,
               gross_spread_capture=None, partial_fill_distribution=None,
               value_if_fill=None, value_if_no_fill=None,
               adverse_selection=None, toxicity=None,
               residual_inventory_cost=None, capital_hours=None,
               joint_fill_model=None) -> dict:
    """The component table for one entry action. Thirteen rows, always.

    Every declared component appears whether or not it is identified,
    because a component that vanishes when it is missing is a component
    nobody notices is missing.
    """
    spec = acts.CANONICAL_ACTIONS.get(action)
    if spec is None:
        return {"action": action, "status": "UNKNOWN_ACTION",
                "declared": list(acts.ACTIONS)}

    band = evb.fill_selection_band(root)
    p = pf.p_fill(root=root)

    table = [
        _component("GROSS_SPREAD_CAPTURE", gross_spread_capture,
                   source="VENUE_BOOK",
                   why=("no readable two-sided book, so the spread "
                        "available to a resting order is not measured")),
        # THE BINDING ONE. Everything passive waits on it.
        _component("P_FILL", None if p["P_FILL"] == NOT_IDENTIFIED
                   else p["P_FILL"], source="BETTOR_NATIVE_FILLS",
                   why=("P_FILL is NOT_IDENTIFIED until BETTOR-native "
                        "admitted executions exist. Whale completion, a "
                        "touch, a price move and displayed depth are "
                        "each refused by name")),
        _component("PARTIAL_FILL_DISTRIBUTION", partial_fill_distribution,
                   source="BETTOR_NATIVE_FILLS",
                   why=("a fill is not all-or-nothing. The distribution "
                        "over filled quantity needs the same native "
                        "evidence P_FILL needs")),
        _component("VALUE_IF_FILL", value_if_fill,
                   source="FV_BETTOR_INDEPENDENT",
                   why=("requires an independent fair value. The venue "
                        "price is not one, and FV_BETTOR_INDEPENDENT is "
                        "NOT_IDENTIFIED")),
        _component("VALUE_IF_NO_FILL", value_if_no_fill,
                   source="NO_FILL_BRANCH",
                   why=("the branch where the order rests and nothing "
                        "happens still costs time and option value, and "
                        "neither is measured")),
        # Frozen, weak, centred at zero, direction NOT assumed.
        _component("FILL_SELECTION_EFFECT", band.get("P50"),
                   source="FROZEN_PRIOR_REGISTRY"),
        _component("ADVERSE_SELECTION", adverse_selection,
                   source="MARKOUTS_AFTER_SUPPORTED_FILL",
                   why=("markouts after a fill need fills. The frozen "
                        "prior is a prior, not this measurement")),
        _component("REBATE", rebate, optional=True, source="VENUE_SCHEDULE",
                   why=OPTIONAL_ABSENCE_IS_NOT_ZERO),
        _component("INCENTIVE", incentive, optional=True,
                   source="VENUE_PROGRAMME",
                   why=OPTIONAL_ABSENCE_IS_NOT_ZERO),
        _component("FEE", fee, source="VENUE_SCHEDULE",
                   why=("no fee schedule is declared for this venue "
                        "leg. It is left unidentified rather than "
                        "zeroed -- a zero fee makes break-even 0.000000 "
                        "and every edge look real")),
        _component("TOXICITY", toxicity, source="FLOW_CLASSIFICATION",
                   why="flow toxicity at this venue is not classified"),
        _component("RESIDUAL_INVENTORY_COST", residual_inventory_cost,
                   source="EXIT_ENGINE",
                   why=("what an unpaired leg costs to carry is the "
                        "exit engine's output, and the exit engine is "
                        "not built")),
        _component("CAPITAL_HOURS", capital_hours,
                   source="SHADOW_MANDATE",
                   why=("capital-hours pricing needs a holding-period "
                        "model, which the exit rule supplies. The "
                        "mandate's EXIT_RULE is unresolved")),
    ]
    by = {c["component"]: c for c in table}

    required = (REQUIRED_FOR_PASSIVE if spec["aggression"] == acts.PASSIVE
                else REQUIRED_FOR_AGGRESSIVE
                if spec["aggression"] == acts.AGGRESSIVE else ())
    missing = [n for n in required if by[n]["status"] != "IDENTIFIED"]

    out = {
        "engine": ENGINE,
        "action": action,
        "aggression": spec["aggression"],
        "components": table,
        "componentsRequired": list(required),
        "componentsMissing": missing,
        "componentsOptional": list(OPTIONAL_COMPONENTS),
        "unidentifiedDoesNotCombineIntoZero":
            UNIDENTIFIED_DOES_NOT_COMBINE_INTO_ZERO,
        "optionalAbsenceIsNotZero": OPTIONAL_ABSENCE_IS_NOT_ZERO,
        "fillSelectionEffectBand": {"P10": band.get("P10"),
                                    "P50": band.get("P50"),
                                    "P90": band.get("P90"),
                                    "directionAssumed":
                                        band.get("directionAssumed")},
    }

    # MAKE_BOTH carries one precondition beyond its components, and it
    # is not satisfiable by having the single-leg components.
    if action == "MAKE_BOTH":
        out["JOINT_FILL_MODEL"] = (joint_fill_model or NOT_IDENTIFIED)
        out["jointFillModelRequired"] = JOINT_FILL_MODEL_REQUIRED
        out["additiveCompositionRefused"] = ADDITIVE_COMPOSITION_REFUSED
        if out["JOINT_FILL_MODEL"] == NOT_IDENTIFIED:
            out["componentsMissing"] = missing + ["JOINT_FILL_MODEL"]
    return out


def additive_composition_refused() -> dict:
    """There is no code path that sums two single-leg totals. Here is why."""
    return {
        "refused": "EV(MAKE_BOTH) = EV(MAKE_YES) + EV(MAKE_NO)",
        "why": ADDITIVE_COMPOSITION_REFUSED,
        "outcomes": {
            "BOTH_FILL": "a pair, the intended outcome",
            "NEITHER_FILLS": "nothing; the cost is time",
            "ONE_FILLS": ("a RESIDUAL -- directional inventory we did "
                          "not want, which must be hedged, exited or "
                          "carried. The additive form never prices it"),
        },
        "requires": JOINT_FILL_MODEL_REQUIRED,
        "JOINT_FILL_MODEL": NOT_IDENTIFIED,
    }


def evaluate_entry(market_state: dict | None = None, *, root=None,
                   inventory_state=None, state_reference=None,
                   **overrides) -> dict:
    """§6: the six entry actions from a FLAT market, with their tables.

    APPLICABILITY FIRST. Entry actions exist only from FLAT, so a
    non-flat or unidentified state produces NOT_APPLICABLE rows with no
    numbers on them rather than an entry decision nobody can take.
    """
    state = inventory_state or applic.STATE_NOT_IDENTIFIED
    rows = []
    for action in ENTRY_ACTIONS:
        verdict = applic.applicability(action, state)
        row = {
            "engine": ENGINE,
            "action": action,
            "APPLICABILITY_STATUS": verdict["APPLICABILITY_STATUS"],
            "APPLICABILITY_REASON": verdict["APPLICABILITY_REASON"],
            "INVENTORY_STATE": state,
            "stateReference": state_reference or NOT_IDENTIFIED,
        }
        if verdict["APPLICABILITY_STATUS"] != applic.APPLICABLE:
            # Not priced and not risk-evaluated: nothing was attempted,
            # so nothing failed.
            row.update({
                "ECONOMIC_STATUS": applic.NOT_EVALUATED,
                "expectedNetDollarsPerContract": NOT_IDENTIFIED,
                "whyNot": verdict["APPLICABILITY_REASON"],
                "whyNotZero": applic.WHY_ZERO_IS_WORSE_THAN_NOTHING,
            })
            rows.append(row)
            continue

        table = components(action, market_state, root=root, **overrides)
        row.update(table)
        if action == "NO_TRADE":
            # §10 of the applicability directive: NO_TRADE is a real
            # outcome with an exactly zero cash effect, and it is never
            # recorded as HOLD.
            row.update({
                "ECONOMIC_STATUS": "EVALUATED",
                "expectedNetDollarsPerContract": "0",
                "status": "IDENTIFIED",
                "whyIdentified": ("no book is touched, so the immediate "
                                  "cash effect is exactly zero. That is "
                                  "not a claim about opportunity cost"),
                "isNotHold": ("NO_TRADE is declining to act from FLAT. "
                              "HOLD is a decision about inventory that "
                              "exists. They are never the same row"),
            })
        else:
            row["ECONOMIC_STATUS"] = "EVALUATED"
            row.update(combine(table))
            if row["status"] == NOT_IDENTIFIED:
                row["whyNot"] = ("required component(s) not identified: %s"
                                 % ", ".join(table["componentsMissing"]))
        rows.append(row)

    priced = [r for r in rows if r.get("status") == "IDENTIFIED"]
    return {
        "engine": ENGINE,
        "INVENTORY_STATE": state,
        "actions": rows,
        "PRICED": len(priced),
        "makerFirstIsNotMakerAlways": MAKER_FIRST_IS_NOT_MAKER_ALWAYS,
        "additiveCompositionRefused": additive_composition_refused(),
        "createsNoInventory": (
            "an evaluation is not an order and not a position. Whether "
            "a supported fill may become shadow inventory is the "
            "mandate's question, and the mandate is not frozen"),
    }


def describe(root=None) -> dict:
    return {
        "engine": ENGINE,
        "entryActions": list(ENTRY_ACTIONS),
        "components": list(COMPONENTS),
        "requiredForPassive": list(REQUIRED_FOR_PASSIVE),
        "requiredForAggressive": list(REQUIRED_FOR_AGGRESSIVE),
        "optionalComponents": list(OPTIONAL_COMPONENTS),
        "componentKind": dict(COMPONENT_KIND),
        "combinationRule": dict(COMBINATION_RULE),
        "aProbabilityIsNotADollar": A_PROBABILITY_IS_NOT_A_DOLLAR,
        "unidentifiedDoesNotCombineIntoZero":
            UNIDENTIFIED_DOES_NOT_COMBINE_INTO_ZERO,
        "optionalAbsenceIsNotZero": OPTIONAL_ABSENCE_IS_NOT_ZERO,
        "makerFirstIsNotMakerAlways": MAKER_FIRST_IS_NOT_MAKER_ALWAYS,
        "additiveCompositionRefused": additive_composition_refused(),
        "bindingComponentToday": "P_FILL",
        "whyNothingPricesToday": (
            "P_FILL is NOT_IDENTIFIED until BETTOR-native fills exist, "
            "so no passive action can be priced. VALUE_IF_FILL needs an "
            "independent fair value, which is also NOT_IDENTIFIED, so "
            "no aggressive action can either. Every entry action except "
            "NO_TRADE is NOT_IDENTIFIED, and that is the correct output"),
    }
