"""WHICH ACTIONS CAN EXIST AT ALL, BEFORE ANY OF THEM IS PRICED.

Owner directive, §2:

    "CURRENT PORTFOLIO STATE -> APPLICABLE ACTION SET -> ECONOMIC
    EVALUATION -> RISK GATE -> ACTION RANKING. NOT: ALL POSSIBLE
    ACTIONS -> PRICE EVERYTHING -> RISK GATE."

THE DEFECT THIS FIXES WAS MINE, AND IT WAS SEMANTIC, NOT ARITHMETIC.
With BETTOR holding zero positions, production reported:

    HOLD           status IDENTIFIED, EV 0
    DIRECT_EXIT    EXECUTION_COST_IDENTIFIED, risk permitted TRUE
    COMPLETE_PAIR  EXECUTION_COST_IDENTIFIED

Every one of those numbers was correctly computed and every one was
about an action that could not be taken. There was nothing to hold,
nothing to exit and no residual leg to complete against. A zero EV on
HOLD is not a cheap no-op -- it is a claim that holding nothing is an
available choice worth exactly nothing, which then competes in a
ranking against actions that are real.

    AN INAPPLICABLE ACTION PRICED AT ZERO IS WORSE THAN ONE LEFT
    UNPRICED. Zero is a strong number: it beats every negative one. An
    engine that ranks by EV would have preferred a nonexistent HOLD to
    a genuinely available take, and been right by its own arithmetic.

SO APPLICABILITY IS A SEPARATE GATE AND IT RUNS FIRST. An action that
cannot exist in the current inventory state never reaches economic
evaluation, never reaches the risk engine, and never appears as
risk-permitted.

────────────────────────────────────────────────────────────────────
THREE STATUSES THAT ARE NOT THE SAME STATUS (§9).

    APPLICABILITY_STATUS   can this action exist here at all
    ECONOMIC_STATUS        given it can, what is it worth
    RISK_STATUS            given it is worth something, is it allowed

DIRECT_EXIT while FLAT is NOT_APPLICABLE_CURRENT_STATE, and its
economic and risk statuses are NOT_EVALUATED_NOT_APPLICABLE -- not
NOT_IDENTIFIED, because nothing was attempted and failed. MAKE_YES
while FLAT is APPLICABLE with an economic status of
NOT_IDENTIFIED_P_FILL: that one WAS attempted and the input is missing.
The difference between "we did not ask" and "we asked and could not
answer" is the whole point of keeping the fields apart.
────────────────────────────────────────────────────────────────────

ONE TABLE, NOT ONE PER ENGINE (§2). The entry bridge, the exit engine
and the capital allocator all consult this module. Applicability rules
duplicated across three engines would drift, and the first symptom
would be an action offered by one and refused by another with no way
to tell which was right.

NOTHING HERE PLACES, SIZES OR FUNDS AN ORDER.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from . import bettor_ev_actions as acts

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ── §9: the three statuses ───────────────────────────────────────────

APPLICABLE = "APPLICABLE"
NOT_APPLICABLE = "NOT_APPLICABLE_CURRENT_STATE"
APPLICABILITY_NOT_IDENTIFIED = "APPLICABILITY_NOT_IDENTIFIED"

NOT_EVALUATED = "NOT_EVALUATED_NOT_APPLICABLE"

STATUS_MEANINGS = {
    APPLICABLE: "the action can exist in this inventory state",
    NOT_APPLICABLE: ("the action cannot exist here -- there is no "
                     "position, no residual or no matched quantity for "
                     "it to act on"),
    APPLICABILITY_NOT_IDENTIFIED: (
        "the inventory state itself is not identified, so whether the "
        "action applies cannot be decided either way"),
    NOT_EVALUATED: ("not asked, because the action does not apply. "
                    "Distinct from NOT_IDENTIFIED, which means asked "
                    "and unanswerable"),
}

WHY_ZERO_IS_WORSE_THAN_NOTHING = (
    "an inapplicable action priced at zero competes in a ranking and "
    "beats every negative one. A nonexistent HOLD at 0 would outrank a "
    "real take at -0.005, and the engine would be right by its own "
    "arithmetic. So inapplicable actions carry no number at all")

# ── §4: the canonical inventory states ───────────────────────────────

FLAT = "FLAT"
YES_RESIDUAL = "YES_RESIDUAL"
NO_RESIDUAL = "NO_RESIDUAL"
BOTH_LEGS_UNMATCHED = "BOTH_LEGS_UNMATCHED"
PARTIALLY_MATCHED_WITH_YES_RESIDUAL = "PARTIALLY_MATCHED_WITH_YES_RESIDUAL"
PARTIALLY_MATCHED_WITH_NO_RESIDUAL = "PARTIALLY_MATCHED_WITH_NO_RESIDUAL"
FULLY_MATCHED_PAIR = "FULLY_MATCHED_PAIR"
OPEN_MAKER_QUOTES_NO_POSITION = "OPEN_MAKER_QUOTES_NO_POSITION"
OPEN_MAKER_QUOTES_WITH_POSITION = "OPEN_MAKER_QUOTES_WITH_POSITION"
STATE_NOT_IDENTIFIED = "STATE_NOT_IDENTIFIED"

# The §3 field names every downstream engine reads. Declared so a
# rename cannot happen silently in one consumer.
STATE_FIELDS = (
    "YES_QTY", "YES_AVG_BASIS", "NO_QTY", "NO_AVG_BASIS",
    "MATCHED_QTY", "MATCHED_PAIR_BASIS",
    "RESIDUAL_YES_QTY", "RESIDUAL_YES_BASIS",
    "RESIDUAL_NO_QTY", "RESIDUAL_NO_BASIS",
    "PAIR_LOCKED_PNL",
    "CAPITAL_IN_MATCHED_INVENTORY", "CAPITAL_IN_RESIDUAL_INVENTORY",
    "OPEN_PASSIVE_YES_QTY", "OPEN_PASSIVE_NO_QTY",
    "COMPLEMENT_IDENTITY_STATUS", "PAIR_STATUS",
)

STATES = (FLAT, YES_RESIDUAL, NO_RESIDUAL, BOTH_LEGS_UNMATCHED,
          PARTIALLY_MATCHED_WITH_YES_RESIDUAL,
          PARTIALLY_MATCHED_WITH_NO_RESIDUAL, FULLY_MATCHED_PAIR,
          OPEN_MAKER_QUOTES_NO_POSITION, OPEN_MAKER_QUOTES_WITH_POSITION,
          STATE_NOT_IDENTIFIED)

UNKNOWN_IS_NOT_FLAT = (
    "an unidentified state is never forced into FLAT. Flat is a "
    "positive claim that we own nothing; not knowing what we own is a "
    "different fact, and treating it as flat would make every entry "
    "action look available on a book we cannot see")

# ── §10: what a decision is ABOUT ────────────────────────────────────
#
# NO_TRADE and HOLD are not synonyms. NO_TRADE decides whether to
# ESTABLISH exposure; HOLD decides whether to RETAIN it. Scoping them
# stops a generic zero-valued NO_TRADE from beating real inventory
# actions by construction, which it would otherwise do whenever the
# inventory question had no identified answer.

ENTRY = "ENTRY"
INVENTORY = "INVENTORY"

DECISION_SCOPE = {
    "MAKE_YES": ENTRY, "MAKE_NO": ENTRY, "MAKE_BOTH": ENTRY,
    "TAKE_YES": ENTRY, "TAKE_NO": ENTRY,
    "NO_TRADE": ENTRY,
    "POST_COMPLEMENT": INVENTORY, "TAKE_COMPLEMENT": INVENTORY,
    "COMPLETE_PAIR": INVENTORY, "MERGE": INVENTORY,
    "HOLD": INVENTORY, "DIRECT_EXIT": INVENTORY, "HEDGE": INVENTORY,
    "HOLD_TO_SETTLEMENT": INVENTORY,
    "WAIT_REQUOTE": INVENTORY,
}

SCOPE_RULE = (
    "NO_TRADE answers the ENTRY question -- do not establish exposure. "
    "HOLD answers the INVENTORY question -- retain what we already "
    "have. They are ranked within their own scope so a zero-valued "
    "NO_TRADE cannot beat a real inventory action by construction")


def _d(v):
    if v is None or v == "" or v == NOT_IDENTIFIED:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


# ── §3/§4: derive the state from per-leg inventory ───────────────────

def inventory_state(inventory: dict | None, *,
                    open_passive_yes_qty=None,
                    open_passive_no_qty=None) -> dict:
    """Which canonical state this book is in.

    `inventory` is a `bettor_inventory.inventory()` result. Quantities
    that are not identified produce STATE_NOT_IDENTIFIED rather than a
    guess.
    """
    if not inventory:
        return {"state": STATE_NOT_IDENTIFIED,
                "why": "no inventory was supplied",
                "unknownIsNotFlat": UNKNOWN_IS_NOT_FLAT}

    yq = _d(inventory.get("YES_QTY"))
    nq = _d(inventory.get("NO_QTY"))
    if yq is None or nq is None:
        return {"state": STATE_NOT_IDENTIFIED,
                "why": ("a leg quantity is not identified, so the state "
                        "cannot be decided"),
                "unknownIsNotFlat": UNKNOWN_IS_NOT_FLAT}

    pair_status = inventory.get("pairStatus")
    matched = _d(inventory.get("MATCHED_QTY"))
    res_yes = _d(inventory.get("RESIDUAL_YES_QTY"))
    res_no = _d(inventory.get("RESIDUAL_NO_QTY"))

    pq_yes = _d(open_passive_yes_qty) or Decimal("0")
    pq_no = _d(open_passive_no_qty) or Decimal("0")
    open_quotes = (pq_yes > 0) or (pq_no > 0)

    zero = Decimal("0")
    flat_legs = (yq == zero and nq == zero)

    # §3: THE CANONICAL STATE OBJECT, under the directive's own field
    # names. Every engine downstream reads these, so the names are the
    # contract -- not whatever bettor_inventory happens to call them
    # internally. A term the inventory could not compute arrives here
    # as NOT_IDENTIFIED rather than missing.
    out = {
        "YES_QTY": str(yq),
        "YES_AVG_BASIS": inventory.get("YES_AVG_BASIS", NOT_IDENTIFIED),
        "NO_QTY": str(nq),
        "NO_AVG_BASIS": inventory.get("NO_AVG_BASIS", NOT_IDENTIFIED),
        "MATCHED_QTY": inventory.get("MATCHED_QTY", NOT_IDENTIFIED),
        "MATCHED_PAIR_BASIS": inventory.get("MATCHED_PAIR_BASIS",
                                            NOT_IDENTIFIED),
        "RESIDUAL_YES_QTY": inventory.get("RESIDUAL_YES_QTY",
                                          NOT_IDENTIFIED),
        "RESIDUAL_YES_BASIS": inventory.get("RESIDUAL_YES_BASIS",
                                            NOT_IDENTIFIED),
        "RESIDUAL_NO_QTY": inventory.get("RESIDUAL_NO_QTY", NOT_IDENTIFIED),
        "RESIDUAL_NO_BASIS": inventory.get("RESIDUAL_NO_BASIS",
                                           NOT_IDENTIFIED),
        "PAIR_LOCKED_PNL": inventory.get("LOCKED_PNL", NOT_IDENTIFIED),
        "CAPITAL_IN_MATCHED_INVENTORY": inventory.get("MATCHED_CAPITAL",
                                                      NOT_IDENTIFIED),
        "CAPITAL_IN_RESIDUAL_INVENTORY": inventory.get("RESIDUAL_CAPITAL",
                                                       NOT_IDENTIFIED),
        "OPEN_PASSIVE_YES_QTY": str(pq_yes),
        "OPEN_PASSIVE_NO_QTY": str(pq_no),
        "COMPLEMENT_IDENTITY_STATUS": inventory.get("identityStatus",
                                                    NOT_IDENTIFIED),
        "PAIR_STATUS": pair_status or NOT_IDENTIFIED,
        "unknownIsNotFlat": UNKNOWN_IS_NOT_FLAT,
    }

    if flat_legs:
        out["state"] = (OPEN_MAKER_QUOTES_NO_POSITION if open_quotes
                        else FLAT)
        out["why"] = ("both legs are zero" +
                      (" but maker quotes are open" if open_quotes else ""))
        return out

    # Legs exist. Whether they PAIR is the identity layer's verdict, not
    # arithmetic: min(YES, NO) is only matched quantity if the two are
    # complements of one condition.
    if both := (yq > zero and nq > zero):
        if matched is None or matched == zero:
            # Both legs held but unmatched -- either the complement is
            # unconfirmed or they are not complements at all.
            out["state"] = BOTH_LEGS_UNMATCHED
            out["why"] = ("both legs are held but no matched quantity is "
                          "established: the complement is %s"
                          % (pair_status or NOT_IDENTIFIED))
            if open_quotes:
                out["state"] = OPEN_MAKER_QUOTES_WITH_POSITION
            return out

    if matched is not None and matched > zero:
        ry = res_yes or zero
        rn = res_no or zero
        if ry == zero and rn == zero:
            out["state"] = FULLY_MATCHED_PAIR
            out["why"] = "matched quantity with no residual on either leg"
        elif ry > zero:
            out["state"] = PARTIALLY_MATCHED_WITH_YES_RESIDUAL
            out["why"] = "matched quantity alongside a YES residual"
        else:
            out["state"] = PARTIALLY_MATCHED_WITH_NO_RESIDUAL
            out["why"] = "matched quantity alongside a NO residual"
        if open_quotes:
            out["openQuotesAlongsidePosition"] = True
        return out

    # One leg only, no matched quantity.
    if yq > zero and nq == zero:
        out["state"] = YES_RESIDUAL
        out["why"] = "a YES leg with no NO leg"
    elif nq > zero and yq == zero:
        out["state"] = NO_RESIDUAL
        out["why"] = "a NO leg with no YES leg"
    else:
        out["state"] = STATE_NOT_IDENTIFIED
        out["why"] = "quantities did not resolve to a declared state"
    if open_quotes and out["state"] != STATE_NOT_IDENTIFIED:
        out["openQuotesAlongsidePosition"] = True
    return out


# ── §5-§8, §12, §13: the applicability table ─────────────────────────
#
# Stated positively per state. An action absent from a state's set is
# NOT_APPLICABLE there -- the table never has to guess.

_ENTRY_ACTIONS = ("MAKE_YES", "MAKE_NO", "MAKE_BOTH", "TAKE_YES",
                  "TAKE_NO", "NO_TRADE")

# Inventory actions available whenever ONE leg is held unmatched.
_RESIDUAL_ACTIONS = ("HOLD", "POST_COMPLEMENT", "TAKE_COMPLEMENT",
                     "COMPLETE_PAIR", "DIRECT_EXIT", "HEDGE",
                     "HOLD_TO_SETTLEMENT")

APPLICABLE_BY_STATE = {
    # §5. Nothing held: only entry actions exist. HOLD has nothing to
    # hold, DIRECT_EXIT nothing to exit, COMPLETE_PAIR no residual leg
    # to complete against, MERGE no matched quantity.
    FLAT: _ENTRY_ACTIONS,

    # §11. An open quote is something to wait on; a position is not yet.
    OPEN_MAKER_QUOTES_NO_POSITION: _ENTRY_ACTIONS + ("WAIT_REQUOTE",),

    # §6. A residual leg makes the inventory actions real. MERGE stays
    # out: matched quantity is still zero.
    YES_RESIDUAL: _ENTRY_ACTIONS + _RESIDUAL_ACTIONS,
    NO_RESIDUAL: _ENTRY_ACTIONS + _RESIDUAL_ACTIONS,

    # Both legs held but not established as a pair. COMPLETE_PAIR is
    # out: acquiring more complement cannot create matched quantity
    # while the complement itself is unconfirmed, and MERGE is out for
    # the same reason.
    BOTH_LEGS_UNMATCHED: _ENTRY_ACTIONS + (
        "HOLD", "DIRECT_EXIT", "HOLD_TO_SETTLEMENT"),

    # §7. BOTH books exist at once, so both sets of actions do. MERGE
    # becomes applicable because matched quantity is positive.
    PARTIALLY_MATCHED_WITH_YES_RESIDUAL:
        _ENTRY_ACTIONS + _RESIDUAL_ACTIONS + ("MERGE",),
    PARTIALLY_MATCHED_WITH_NO_RESIDUAL:
        _ENTRY_ACTIONS + _RESIDUAL_ACTIONS + ("MERGE",),

    # §8. Nothing left to complete and no residual to hedge. Selling a
    # held leg remains mechanically real, so DIRECT_EXIT stays.
    FULLY_MATCHED_PAIR: _ENTRY_ACTIONS + (
        "MERGE", "HOLD", "HOLD_TO_SETTLEMENT", "DIRECT_EXIT"),

    OPEN_MAKER_QUOTES_WITH_POSITION:
        _ENTRY_ACTIONS + _RESIDUAL_ACTIONS + ("WAIT_REQUOTE",),
}

# Why each action needs what it needs, quoted where a caller will read it.
REQUIRES_STATE = {
    "HOLD": "a position to retain",
    "DIRECT_EXIT": "a held leg to sell",
    "POST_COMPLEMENT": "a held leg whose complement could be acquired",
    "TAKE_COMPLEMENT": "a held leg whose complement could be acquired",
    "COMPLETE_PAIR": ("a held residual leg, an established complement "
                      "identity, and quantity capable of becoming "
                      "matched"),
    "MERGE": "matched quantity greater than zero",
    "HEDGE": "exposure to neutralise",
    "HOLD_TO_SETTLEMENT": "a position to carry",
    "WAIT_REQUOTE": "an existing resting order or quote lifecycle",
}


def applicability(action: str, state: str) -> dict:
    """May this action exist in this state? Economics never consulted."""
    row = {
        "action": action,
        "inventoryState": state,
        "decisionScope": DECISION_SCOPE.get(action, NOT_IDENTIFIED),
        "scopeRule": SCOPE_RULE,
    }
    if action not in acts.CANONICAL_ACTIONS:
        row.update({"APPLICABILITY_STATUS": APPLICABILITY_NOT_IDENTIFIED,
                    "APPLICABILITY_REASON": "not a canonical action"})
        return row
    if state == STATE_NOT_IDENTIFIED:
        row.update({
            "APPLICABILITY_STATUS": APPLICABILITY_NOT_IDENTIFIED,
            "APPLICABILITY_REASON": (
                "the inventory state is not identified, so whether this "
                "action applies cannot be decided either way. It is not "
                "assumed applicable and it is not assumed flat"),
            "unknownIsNotFlat": UNKNOWN_IS_NOT_FLAT})
        return row

    allowed = APPLICABLE_BY_STATE.get(state)
    if allowed is None:
        row.update({"APPLICABILITY_STATUS": APPLICABILITY_NOT_IDENTIFIED,
                    "APPLICABILITY_REASON": "unknown inventory state"})
        return row

    if action in allowed:
        row.update({"APPLICABILITY_STATUS": APPLICABLE,
                    "APPLICABILITY_REASON": "available in state %s" % state})
        return row

    needs = REQUIRES_STATE.get(action)
    row.update({
        "APPLICABILITY_STATUS": NOT_APPLICABLE,
        "APPLICABILITY_REASON": (
            "%s requires %s, which state %s does not provide"
            % (action, needs, state) if needs else
            "%s is not available in state %s" % (action, state)),
        "ECONOMIC_STATUS": NOT_EVALUATED,
        "RISK_STATUS": NOT_EVALUATED,
        "whyNotZero": WHY_ZERO_IS_WORSE_THAN_NOTHING,
    })
    return row


def applicable_set(state: str) -> dict:
    """The whole table for one state, both halves named."""
    rows = [applicability(a, state) for a in acts.ACTIONS]
    return {
        "inventoryState": state,
        "APPLICABLE": [r["action"] for r in rows
                       if r["APPLICABILITY_STATUS"] == APPLICABLE],
        "NOT_APPLICABLE_CURRENT_STATE": [
            r["action"] for r in rows
            if r["APPLICABILITY_STATUS"] == NOT_APPLICABLE],
        "APPLICABILITY_NOT_IDENTIFIED": [
            r["action"] for r in rows
            if r["APPLICABILITY_STATUS"] == APPLICABILITY_NOT_IDENTIFIED],
        "rows": rows,
    }


def describe() -> dict:
    return {
        "purpose": "decide which actions can exist before any is priced",
        "states": list(STATES),
        "statusMeanings": dict(STATUS_MEANINGS),
        "unknownIsNotFlat": UNKNOWN_IS_NOT_FLAT,
        "whyZeroIsWorseThanNothing": WHY_ZERO_IS_WORSE_THAN_NOTHING,
        "scopeRule": SCOPE_RULE,
        "flatApplicable": applicable_set(FLAT)["APPLICABLE"],
        "flatNotApplicable":
            applicable_set(FLAT)["NOT_APPLICABLE_CURRENT_STATE"],
        "oneTableNotOnePerEngine": (
            "the entry bridge, the exit engine and the capital "
            "allocator all consult this module. Duplicated rules would "
            "drift, and the first symptom would be an action offered by "
            "one engine and refused by another"),
    }
