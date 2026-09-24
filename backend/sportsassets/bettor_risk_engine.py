"""THE RISK ENGINE. WHAT MUST BE TRUE BEFORE EXPOSURE MAY INCREASE.

Owner directive, "CONTINUE THE BUILD" §18:

    "Unknown risk state must fail closed for exposure-increasing
    actions. Risk-reducing actions remain separately considered."

THE SECOND SENTENCE IS NOT A SOFTENING OF THE FIRST. A rail that
blocked everything when the risk state was unknown would trap the book:
the one moment you most need to be able to reduce a position is the
moment you cannot measure it. So the two directions are judged on
separate paths, and an unknown risk state blocks only the direction
that makes the problem larger.

NO LIMIT IS INVENTED HERE, AND THAT HAS TEETH. Not one of these rails
has a number the owner has declared for the BETTOR EV lane. Writing a
plausible-looking MAX_CAPITAL_DEPLOYED would create a limit nobody
chose and make the engine look governed when it is not. Every rail is
therefore NOT_PREDECLARED, and because an unknown limit fails closed,
the standing result is:

    NO EXPOSURE-INCREASING ACTION CAN PASS THE RISK ENGINE TODAY.

That agrees with the rest of the system rather than duplicating it --
mirror_live is false and no order path exists -- but it agrees for a
DIFFERENT REASON, which is the point of having the layer. When the
order path eventually exists, this gate does not open with it. It opens
only when someone declares the numbers.

────────────────────────────────────────────────────────────────────
GROSS AND DIRECTIONAL EXPOSURE MOVE IN OPPOSITE DIRECTIONS (§7).

A hedge is the case that breaks a single-axis risk model. Buying NO
while holding YES:

    GROSS exposure       INCREASES -- there are now two positions,
                         two fills, two sets of fees, and capital is
                         occupied on both legs
    DIRECTIONAL exposure DECREASES -- the outcome risk is neutralised

A model with one "exposure" number has to pick, and either choice is
wrong somewhere: call it risk-reducing and a capital limit never binds
on a book that keeps hedging itself larger; call it risk-increasing and
the engine forbids the very action that removes outcome risk. So both
deltas are recorded per action and the rails bind on whichever axis
they actually govern -- capital rails on gross, outcome rails on
directional.
────────────────────────────────────────────────────────────────────

A RAIL THAT CANNOT BE EVALUATED IS NOT A RAIL THAT PASSED. Three
distinct verdicts, kept apart: PASS (measured, inside the limit), BLOCK
(measured, outside it) and NOT_EVALUABLE (the limit or the measurement
is missing). Collapsing the third into PASS is how risk systems fail
quietly, so NOT_EVALUABLE blocks exposure increases exactly as BLOCK
does, while saying something different about why.

────────────────────────────────────────────────────────────────────
AN UNRECOGNISED ACTION IS NOT A HARMLESS ONE (found 2026-09-24).

`EXPOSURE_EFFECT` is keyed by the EV action vocabulary -- MAKE_YES,
TAKE_YES, DIRECT_EXIT and so on. `exposure_effect()` answers
NOT_IDENTIFIED for anything else, and the verdict below used to read
only "is this INCREASE?". So an action spelled any other way -- the
entry gate's own `BUY`, a typo, a new verb added upstream -- fell past
the increasing branch into the reducing/neutral one and came back
**permitted: True, direction EXPOSURE_NEUTRAL**, with every rail
NOT_EVALUABLE and nothing blocking it.

That is the exact failure this module's own first principle forbids,
one level up: not an unevaluable rail treated as a pass, but an
unevaluable ACTION treated as harmless. Wiring the external-valuation
entry lane into this engine is what surfaced it, because that lane's
action name is `BUY`.

An action whose exposure effect is not identified is now REFUSED, and
by name. Widening the vocabulary is a deliberate edit to
`bettor_ev_actions`, not something a caller does by choosing a string.
────────────────────────────────────────────────────────────────────

LIMITS MAY BE SUPPLIED BY A LANE, AND THE DEFAULT STAYS EMPTY. The
module-level rails carry no numbers and never will: a limit written
here would govern every lane at once without anyone choosing it for
any of them. `evaluate(..., limits=...)` lets a lane pass its OWN
predeclared set, so the numbers live with the mandate that declared
them and a lane that declares nothing is still refused. An unknown
rail name in a supplied set is refused rather than ignored, because a
misspelled limit is an ungoverned rail wearing a governed name.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from . import bettor_ev_actions as acts

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_PREDECLARED = "NOT_PREDECLARED"

# ── verdicts ─────────────────────────────────────────────────────────

PASS = "PASS"
BLOCK = "BLOCK"
NOT_EVALUABLE = "NOT_EVALUABLE"

VERDICTS = (PASS, BLOCK, NOT_EVALUABLE)

NOT_EVALUABLE_IS_NOT_PASS = (
    "a rail that could not be evaluated did not pass. The distinction "
    "is kept because the fixes differ: BLOCK means declare less "
    "exposure, NOT_EVALUABLE means declare a limit or start measuring. "
    "Collapsing them into PASS is how a risk system reports green "
    "while governing nothing")

# ── the two axes (§7) ────────────────────────────────────────────────

GROSS = "GROSS"
DIRECTIONAL = "DIRECTIONAL"

# THE EXPOSURE EFFECTS COME FROM THE ACTION TABLE, not from a second
# copy here. An earlier version kept its own and the two disagreed
# about COMPLETE_PAIR -- a safety gate whose notion of "increases
# exposure" differs from the canonical table's is a gate that can be
# walked around by naming the action differently.
INCREASE = acts.INCREASE
DECREASE = acts.DECREASE
UNCHANGED = acts.UNCHANGED

EXPOSURE_EFFECT = acts.EXPOSURE_EFFECT
TWO_AXES = acts.TWO_AXES

# ── the rails (§18), each with the axis it governs ───────────────────
#
# `limit` is NOT_PREDECLARED for every one of them. That is the honest
# state and it is load bearing: an invented number would make this
# engine look governed while governing nothing.

RAILS = {
    "MAX_EVENT_EXPOSURE": {
        "axis": GROSS, "limit": NOT_PREDECLARED,
        "measures": "capital deployed across all markets of one event",
        "why": ("one event resolving badly can take every market in it "
                "at once, so per-market limits do not bound it")},
    "MAX_MARKET_EXPOSURE": {
        "axis": GROSS, "limit": NOT_PREDECLARED,
        "measures": "capital deployed in one market"},
    "MAX_RESIDUAL_INVENTORY": {
        "axis": DIRECTIONAL, "limit": NOT_PREDECLARED,
        "measures": "unpaired quantity carrying outcome risk",
        "why": ("the Ferrari failure was residual inventory, not pair "
                "generation. This is the rail that would have bound "
                "it")},
    "MAX_CORRELATED_EXPOSURE": {
        "axis": DIRECTIONAL, "limit": NOT_PREDECLARED,
        "measures": "exposure across outcomes that move together",
        "why": ("positions in different markets are not independent "
                "when the same result drives them")},
    "MAX_CAPITAL_DEPLOYED": {
        "axis": GROSS, "limit": NOT_PREDECLARED,
        "measures": "total capital occupied across the book"},
    "MAX_CAPITAL_HOURS": {
        "axis": GROSS, "limit": NOT_PREDECLARED,
        "measures": "capital x time, which prices stale inventory",
        "why": ("capital held a long time at a small edge can be worse "
                "than a loss taken quickly, and nothing else on this "
                "list notices")},
    "MAX_DRAWDOWN": {
        "axis": DIRECTIONAL, "limit": NOT_PREDECLARED,
        "measures": "realised and unrealised loss against a predeclared "
                    "policy"},
}

# ── the state gates: conditions, not quantities ──────────────────────
#
# These do not compare a number against a limit. They ask whether the
# system is in a state where any exposure increase is admissible.

STATE_GATES = {
    "STALE_DATA": (
        "the book must satisfy the freshness contract. A stale book "
        "prices an action against a market that may no longer exist"),
    "UNRESOLVED_SETTLEMENT_SEMANTICS": (
        "this venue's settlement prose is CONFLICTING_VENUE_PROSE, so "
        "the terminal value of a held position is not established. "
        "Increasing exposure into an instrument whose payout rule is "
        "unresolved is not a risk that has been measured"),
    "OUT_OF_DISTRIBUTION": (
        "an observation outside the range the model was built on is "
        "not evidence the model handles it"),
    "MODEL_TRUST_DRIFT": (
        "a model whose calibration has drifted is not a model whose "
        "outputs may size a position"),
}


def _d(v):
    if v is None or v == "" or v in (NOT_IDENTIFIED, NOT_PREDECLARED):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def exposure_effect(action: str) -> dict:
    gross, directional = EXPOSURE_EFFECT.get(
        action, (NOT_IDENTIFIED, NOT_IDENTIFIED))
    return {
        "action": action,
        "grossExposure": gross,
        "directionalExposure": directional,
        "increasesExposure": gross == INCREASE,
        "reducesDirectionalRisk": directional == DECREASE,
        "twoAxes": TWO_AXES,
    }


R_ACTION_UNKNOWN = "ACTION_EXPOSURE_EFFECT_NOT_IDENTIFIED"
R_LIMIT_NAME_UNKNOWN = "PREDECLARED_LIMIT_NAMES_AN_UNKNOWN_RAIL"


def evaluate_rail(name: str, observed=None, limit_override=None) -> dict:
    """One rail. A missing limit or a missing measurement is not a pass.

    `limit_override` is a number a LANE predeclared for this rail. It is
    used only when supplied; there is no fallback number anywhere.
    """
    spec = RAILS.get(name)
    if spec is None:
        return {"rail": name, "verdict": NOT_EVALUABLE,
                "why": "unknown rail"}
    limit = _d(spec["limit"] if limit_override is None else limit_override)
    value = _d(observed)
    row = {
        "rail": name,
        "axis": spec["axis"],
        "limit": (spec["limit"] if limit_override is None
                  else str(limit_override)),
        # WHOSE NUMBER THIS IS. A reader must be able to tell a lane's
        # predeclared limit from a module default, because only one of
        # them was chosen by anybody.
        "limitSource": ("MODULE_DEFAULT" if limit_override is None
                        else "LANE_PREDECLARED"),
        "observed": (str(value) if value is not None else NOT_IDENTIFIED),
        "measures": spec["measures"],
    }
    if spec.get("why"):
        row["whyThisRailExists"] = spec["why"]
    if limit is None:
        row.update({"verdict": NOT_EVALUABLE,
                    "why": ("no limit is predeclared for this rail, so "
                            "nothing can be compared against it. A "
                            "limit invented here would govern nothing "
                            "while appearing to")})
        return row
    if value is None:
        row.update({"verdict": NOT_EVALUABLE,
                    "why": "the quantity is not measured"})
        return row
    row.update({"verdict": PASS if value <= limit else BLOCK,
                "why": ("%s the predeclared limit"
                        % ("within" if value <= limit else "beyond"))})
    return row


def evaluate(action: str, *, observed=None, state=None, limits=None) -> dict:
    """May this action proceed on risk grounds?

    `observed` maps rail name -> measured value.
    `state` maps state-gate name -> True when the condition is CLEAR.
    `limits` maps rail name -> the limit A LANE predeclared. Omitted, the
    module's own rails apply and none of them carries a number.
    """
    effect = exposure_effect(action)
    observed = observed or {}
    state = state or {}
    limits = dict(limits or {})

    # A LIMIT FOR A RAIL THAT DOES NOT EXIST IS REFUSED, NOT DROPPED.
    # Silently ignoring `MAX_MARKET_EXPOSURES` would leave
    # MAX_MARKET_EXPOSURE ungoverned while the caller's declaration
    # showed a number for it.
    unknown_limits = sorted(n for n in limits if n not in RAILS)

    rails = [evaluate_rail(name, observed.get(name), limits.get(name))
             for name in RAILS]
    gates = []
    for name, why in STATE_GATES.items():
        clear = state.get(name)
        gates.append({
            "gate": name,
            "verdict": (PASS if clear is True
                        else BLOCK if clear is False else NOT_EVALUABLE),
            "why": why,
            "clear": clear,
        })

    failing = [r["rail"] for r in rails if r["verdict"] != PASS]
    failing_gates = [g["gate"] for g in gates if g["verdict"] != PASS]

    out = {
        "action": action,
        **effect,
        "rails": rails,
        "stateGates": gates,
        "railsNotPassed": failing,
        "gatesNotPassed": failing_gates,
        "notEvaluableIsNotPass": NOT_EVALUABLE_IS_NOT_PASS,
        "verdicts": list(VERDICTS),
        "limitsSupplied": sorted(limits),
        "limitsNamingUnknownRails": unknown_limits,
    }

    if unknown_limits:
        out.update({
            "direction": NOT_IDENTIFIED,
            "permitted": False,
            "refusal": R_LIMIT_NAME_UNKNOWN,
            "why": ("the supplied limits name rail(s) this engine does "
                    "not declare: %s. A misspelled limit leaves the real "
                    "rail ungoverned while the declaration appears to "
                    "cover it, so the whole set is refused"
                    % ", ".join(unknown_limits)),
        })
        return out

    # AN UNRECOGNISED ACTION FAILS CLOSED, BEFORE EITHER DIRECTION.
    # See the module header: this branch used to be absent, and an
    # action outside the EV vocabulary came back EXPOSURE_NEUTRAL and
    # permitted while no rail had been evaluated at all.
    if effect["grossExposure"] == NOT_IDENTIFIED:
        out.update({
            "direction": NOT_IDENTIFIED,
            "permitted": False,
            "refusal": R_ACTION_UNKNOWN,
            "why": ("%r is not in the declared action vocabulary, so "
                    "neither its gross nor its directional exposure "
                    "effect is known. An action whose effect on the "
                    "book is unidentified cannot be shown not to "
                    "increase exposure, and is refused on the same "
                    "principle that refuses an unevaluable rail. Add it "
                    "to bettor_ev_actions deliberately; do not reach "
                    "this branch by choosing a spelling" % (action,)),
            "declaredActions": sorted(EXPOSURE_EFFECT),
        })
        return out

    # THE ASYMMETRY, WHICH IS THE WHOLE DESIGN. An unmeasurable risk
    # state blocks making the problem bigger. It does not block making
    # it smaller, because the moment the book is hardest to measure is
    # exactly the moment it may most need reducing.
    if effect["grossExposure"] == INCREASE:
        permitted = not failing and not failing_gates
        out.update({
            "direction": "EXPOSURE_INCREASING",
            "permitted": permitted,
            "why": ("every rail and state gate passed" if permitted else
                    "an exposure-increasing action requires EVERY rail "
                    "and state gate to pass. %d rail(s) and %d gate(s) "
                    "did not, and a rail that could not be evaluated "
                    "did not pass" % (len(failing), len(failing_gates))),
        })
        return out

    out.update({
        "direction": ("EXPOSURE_REDUCING"
                      if effect["grossExposure"] == DECREASE
                      else "EXPOSURE_NEUTRAL"),
        "permitted": True,
        "why": ("this action does not increase gross exposure, so an "
                "unknown risk state does not block it. Blocking "
                "reduction on unmeasurable risk would trap the book at "
                "exactly the moment it most needs reducing"),
        "stillSubjectTo": ("economic evaluation: not being blocked on "
                           "risk grounds is not a reason to do it"),
    })
    return out


def report() -> dict:
    """What the risk layer currently governs, stated plainly."""
    return {
        "railsDeclared": list(RAILS),
        "railsWithPredeclaredLimits": [
            n for n, s in RAILS.items() if s["limit"] != NOT_PREDECLARED],
        "stateGates": list(STATE_GATES),
        "exposureIncreasingActions": list(acts.EXPOSURE_INCREASING),
        "anyExposureIncreaseCurrentlyPermitted": False,
        "why": ("no rail has a predeclared limit, and an unevaluable "
                "rail fails closed for exposure-increasing actions. "
                "This is a separate refusal from mirror_live being "
                "false, and it does not lift when the order path "
                "exists -- it lifts when the limits are declared"),
        "twoAxes": TWO_AXES,
        "notEvaluableIsNotPass": NOT_EVALUABLE_IS_NOT_PASS,
    }
