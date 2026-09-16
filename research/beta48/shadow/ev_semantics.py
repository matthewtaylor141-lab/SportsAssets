#!/usr/bin/env python3
"""EV TERM SEMANTICS: unit, sign, conditioning, horizon, source, uncertainty.

Contacts nothing. No orders, no capital, no credentials, mirror_live = false.

WHY THIS MODULE EXISTS, AND A CORRECTION IT ENCODES.

An earlier audit of mine asserted that `position_state.ev_pair_now` double
counts, because it sums inventory and execution terms unconditionally. THAT WAS
WRONG, and reading the code rather than the shape settled it:

    ev_pair_now  prices A_AGGRESSIVE_COMPLEMENT_PAIR
                 -- "the currently executable complement", PROBES says
                 COMPLEMENT_EXECUTABLE_NOW

An immediately executed action has no fill uncertainty, so UNCONDITIONAL terms
are exactly right there and there is no double count to fix.

The real gap is the other one. `A_PASSIVE_COMPLEMENT_PAIR` and
`A_PASSIVE_SELL_EXIT` are feasible actions with NO fill-conditional EV function
anywhere. Priced through `ev_simple` -- a flat named sum -- a passive action
either silently omits the no-fill branch, or invites exactly the double count I
wrongly attributed to ev_pair_now the moment somebody multiplies the sum by
P_FILL. So this module ADDS the missing structure rather than modifying a
function that was already correct.

THE RULE, ONCE:

    A term CONDITIONAL_ON_FILL lives INSIDE the fill branch and is never
    multiplied by P_FILL a second time.

    A term that is ALREADY UNCONDITIONAL -- i.e. already contains its own
    P_FILL factor -- lives OUTSIDE and is never multiplied by P_FILL at all.

    The same economic quantity may appear in the sum ONCE, in ONE of those two
    forms. Not both.

Conditioning is therefore a DECLARED PROPERTY of every term, checkable by
machine, not a convention held in someone's head.
"""
from __future__ import annotations

from decimal import Decimal as D

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_ESTABLISHED = "NOT_ESTABLISHED"
THIS_MODULE_CONTACTS_NOTHING = True
ORDERS = 0
CAPITAL = 0
CREDENTIALS = "NONE"
mirror_live = False

# ---------------------------------------------------------------------------
# CONDITIONING
# ---------------------------------------------------------------------------
CONDITIONAL_ON_FILL = "CONDITIONAL_ON_FILL"
UNCONDITIONAL = "UNCONDITIONAL"
CERTAIN = "CERTAIN"          # the action executes now; there is no fill branch
CONDITIONING_VALUES = (CONDITIONAL_ON_FILL, UNCONDITIONAL, CERTAIN)

# Units. A probability added to a dollar figure is a defect, not a rounding
# issue, so the unit travels with the term.
USD = "USD"
USD_PER_CONTRACT = "USD_PER_CONTRACT"
PROBABILITY = "PROBABILITY"
SECONDS = "SECONDS"
USD_SECONDS = "USD_SECONDS"
CONTRACTS = "CONTRACTS"

CREDIT = "+"      # increases EV
DEBIT = "-"       # decreases EV
SIGNED = "+/-"    # may take either sign; the estimate carries it


class ConditioningError(RuntimeError):
    """A term was used in a branch its conditioning forbids."""


class DoubleCountError(RuntimeError):
    """The same economic quantity entered one sum twice."""


class NoFillBranchError(RuntimeError):
    """A passive EV was priced without declaring what happens if it misses."""


class ExecutabilityError(RuntimeError):
    """CERTAIN was claimed without proof the required size can execute."""


class LiveCertaintyError(ExecutabilityError):
    """A snapshot was offered as evidence of an actual venue execution."""


# ---------------------------------------------------------------------------
# CORRECTION 7 -- CERTAIN REQUIRES PROVEN DISPLAYED DEPTH, NOT A TOUCH
# ---------------------------------------------------------------------------
#
# CERTAIN means "this action executes now, so there is no fill branch". That
# claim is only true if the size we need can actually execute now. A best bid
# or best ask proves a price exists; it proves nothing about SIZE. A one-lot
# resting ask does not make a 500-lot aggressive pair CERTAIN -- the remainder
# walks the book at unknown prices, or does not execute at all, which is a
# fill branch wearing a different name.
#
# CORRECTION 10 -- AND SNAPSHOT EXECUTABILITY IS NOT A LIVE FILL
#
# The gate above is sufficient to establish exactly one thing:
#
#   THE CAPTURED SNAPSHOT CONTAINS ENOUGH DISPLAYED DEPTH TO PRICE THE FULL
#   ACTION AT THAT INSTANT.
#
# It does NOT establish that a real order sent afterwards fills that size. The
# book can change at four points between the two:
#
#   OBSERVATION -> DECISION -> ORDER_TRANSMISSION -> VENUE_ARRIVAL
#
# and displayed depth is not reserved for us in any of those gaps. So the two
# quantities are kept as SEPARATE VARIABLES, and there is no code path from
# one to the other:
#
#   SNAPSHOT_FULL_SIZE_EXECUTABLE   YES / NO / NOT_IDENTIFIED   (this gate)
#   LIVE_FULL_SIZE_FILL_CERTAINTY   NOT_ESTABLISHED             (until an
#                                   actual venue execution result exists, or
#                                   documented venue execution semantics give
#                                   a stronger guarantee -- which is itself
#                                   NOT_ESTABLISHED for PMUS today)
#
# THIS DOES NOT REINTRODUCE P_FILL INTO THE SNAPSHOT ARITHMETIC. For a
# same-snapshot counterfactual, displayed ladder depth proving full-size
# execution under the stated pricing rule is enough to price the action with
# CERTAIN terms and no hypothetical fill probability. What changes is the
# LABEL on the result: it is a SNAPSHOT_EXECUTION_COUNTERFACTUAL, a pre-trade
# estimate, and never GUARANTEED_LIVE_EXECUTION.
CERTAIN_REQUIRES = "CAPTURED_BOOK_DEPTH_COVERING_THE_REQUIRED_SIZE"
CERTAIN_IS_NOT_ESTABLISHED_BY = ("PRESENCE_OF_A_BEST_BID",
                                 "PRESENCE_OF_A_BEST_ASK",
                                 "A_QUOTED_PRICE_WITH_NO_SIZE",
                                 "A_LAST_TRADE_PRICE",
                                 "AN_UNTIMED_OR_UNSOURCED_SNAPSHOT")
DEPTH_SOURCE_REQUIRED = "CAPTURED_BOOK_SNAPSHOT"
STALE_SNAPSHOT_IS_NOT_DEPTH = True

AGGRESSIVE_SNAPSHOT_EXECUTABILITY_GATE = "PROVEN_DISPLAYED_DEPTH_FOR_FULL_SIZE"
LIVE_AGGRESSIVE_FILL_CERTAINTY = "NOT_ESTABLISHED_UNTIL_ACTUAL_EXECUTION"
SNAPSHOT_CALCULATION_LABEL = "SNAPSHOT_EXECUTION_COUNTERFACTUAL"
SNAPSHOT_CALCULATION_IS_NOT = "GUARANTEED_LIVE_EXECUTION"
SNAPSHOT_MODEL_IS = "A_PRE_TRADE_ESTIMATE_ONLY"
WHAT_CAN_CHANGE_BETWEEN_SNAPSHOT_AND_ARRIVAL = (
    "OBSERVATION", "DECISION", "ORDER_TRANSMISSION", "VENUE_ARRIVAL")
DISPLAYED_DEPTH_IS_NOT_RESERVED_FOR_US = True
VENUE_EXECUTION_SEMANTICS_GUARANTEE = NOT_ESTABLISHED

# The only things that may set LIVE_FULL_SIZE_FILL_CERTAINTY. A snapshot is
# not among them, and `live_fill_certainty` refuses one by name.
OBSERVED_VENUE_EXECUTION = "OBSERVED_VENUE_EXECUTION"
VENUE_RESULTS = ("FULL_FILL", "PARTIAL_FILL", "NO_FILL", "REJECT",
                 "OTHER_VENUE_RESULT")
REALIZED_EV_SOURCE = "ACTUAL_EXECUTION_ONLY"


def snapshot_executability_gate(required_size, executable_depth=None,
                                depth_source=None, snapshot_age_s=None,
                                max_snapshot_age_s=5):
    """Does the CAPTURED SNAPSHOT display enough depth to price full size?

    Returns SNAPSHOT_FULL_SIZE_EXECUTABLE = YES / NO / NOT_IDENTIFIED, the
    conditioning that is justified FOR THE SNAPSHOT COUNTERFACTUAL, and --
    always, on every branch, including YES -- LIVE_FULL_SIZE_FILL_CERTAINTY =
    NOT_ESTABLISHED. The live field is a constant here by design: nothing this
    function can see is evidence about a later execution.

    A PARTIAL depth result is deliberately NOT downgraded to
    CONDITIONAL_ON_FILL here: an aggressive order that can only partially
    execute has an execution-price problem as well as a size problem, and
    pricing it needs the walk-the-book cost this module does not model.
    """
    out = {"REQUIRED_SIZE": required_size,
           "EXECUTABLE_DEPTH": (executable_depth if executable_depth is not None
                                else NOT_IDENTIFIED),
           "DEPTH_SOURCE": depth_source or NOT_IDENTIFIED,
           "GATE": AGGRESSIVE_SNAPSHOT_EXECUTABILITY_GATE,
           "CERTAIN_REQUIRES": CERTAIN_REQUIRES,
           "CALCULATION_LABEL": SNAPSHOT_CALCULATION_LABEL,
           "IS_NOT": SNAPSHOT_CALCULATION_IS_NOT,
           # Carried on EVERY branch, so no caller can read a YES without it.
           "LIVE_FULL_SIZE_FILL_CERTAINTY": LIVE_AGGRESSIVE_FILL_CERTAINTY,
           "VENUE_EXECUTION_SEMANTICS_GUARANTEE":
               VENUE_EXECUTION_SEMANTICS_GUARANTEE}

    def _no(why, **extra):
        return dict(out, SNAPSHOT_FULL_SIZE_EXECUTABLE="NO",
                    SNAPSHOT_CONDITIONING_ALLOWED=NOT_ESTABLISHED,
                    WHY=why, **extra)

    def _unknown(why):
        return dict(out, SNAPSHOT_FULL_SIZE_EXECUTABLE=NOT_IDENTIFIED,
                    SNAPSHOT_CONDITIONING_ALLOWED=NOT_ESTABLISHED, WHY=why)

    if depth_source != DEPTH_SOURCE_REQUIRED:
        return _unknown("depth must come from a %s; %r does not establish "
                        "displayed size" % (DEPTH_SOURCE_REQUIRED,
                                            depth_source))
    if snapshot_age_s is None:
        return _unknown("the snapshot carries no age; an untimed snapshot is "
                        "not evidence about what was displayed at any instant")
    if snapshot_age_s > max_snapshot_age_s:
        return _unknown("snapshot is %ss old against a %ss limit; a stale "
                        "book is not proof of displayed depth"
                        % (snapshot_age_s, max_snapshot_age_s))
    if executable_depth is None or isinstance(executable_depth, str):
        return _unknown("displayed depth is not identified")
    if D(str(executable_depth)) < D(str(required_size)):
        return _no("displayed depth covers less than the required size, so "
                   "part of the order does not execute even in the snapshot; "
                   "that is a fill branch, not a certainty",
                   DEPTH_SHORTFALL=str(D(str(required_size))
                                       - D(str(executable_depth))))
    return dict(out, SNAPSHOT_FULL_SIZE_EXECUTABLE="YES",
                SNAPSHOT_CONDITIONING_ALLOWED=CERTAIN,
                WHY="the captured snapshot displays enough depth to price the "
                    "full required size at that instant; this prices a "
                    "counterfactual, not a promise about a later order")


# The old name, kept so nothing silently calls a function that no longer
# exists -- but it points at the snapshot gate, which is all it ever was.
certain_execution_gate = snapshot_executability_gate


def assert_snapshot_certain_allowed(gate_result):
    """Refuse to price CERTAIN terms on an ungated snapshot action."""
    if gate_result.get("SNAPSHOT_CONDITIONING_ALLOWED") != CERTAIN:
        raise ExecutabilityError(
            "CERTAIN conditioning refused for the snapshot counterfactual: "
            "%s. %s = %s"
            % (gate_result.get("WHY"),
               "AGGRESSIVE_SNAPSHOT_EXECUTABILITY_GATE",
               AGGRESSIVE_SNAPSHOT_EXECUTABILITY_GATE))
    return gate_result


assert_certain_allowed = assert_snapshot_certain_allowed


def live_fill_certainty(snapshot_gate=None, venue_result=None,
                        result_source=None):
    """THE GUARD. A snapshot can never produce live fill certainty.

    There is deliberately no argument combination that turns
    SNAPSHOT_FULL_SIZE_EXECUTABLE = YES into LIVE_FULL_SIZE_FILL_CERTAINTY =
    YES. The only input that moves the live field is an OBSERVED venue
    execution, and passing a snapshot as one raises.
    """
    if venue_result is None:
        return {"LIVE_FULL_SIZE_FILL_CERTAINTY":
                    LIVE_AGGRESSIVE_FILL_CERTAINTY,
                "SNAPSHOT_FULL_SIZE_EXECUTABLE":
                    (snapshot_gate or {}).get("SNAPSHOT_FULL_SIZE_EXECUTABLE",
                                              NOT_IDENTIFIED),
                "WHY": "no order has been sent, so there is no execution to "
                       "observe; displayed depth is not reserved for us and "
                       "the book may change between %s"
                       % " -> ".join(WHAT_CAN_CHANGE_BETWEEN_SNAPSHOT_AND_ARRIVAL),
                "REALIZED_EV_SOURCE": REALIZED_EV_SOURCE}
    if result_source != OBSERVED_VENUE_EXECUTION:
        raise LiveCertaintyError(
            "a venue result must come from %s; %r may not stand in for one. A "
            "stale snapshot is not an observed fill."
            % (OBSERVED_VENUE_EXECUTION, result_source))
    if venue_result not in VENUE_RESULTS:
        raise LiveCertaintyError(
            "unknown venue result %r; expected one of %s"
            % (venue_result, ", ".join(VENUE_RESULTS)))
    return {"LIVE_EXECUTION_RESULT": venue_result,
            "RESULT_SOURCE": OBSERVED_VENUE_EXECUTION,
            "LIVE_FULL_SIZE_FILL_CERTAINTY":
                "OBSERVED_FULL_FILL" if venue_result == "FULL_FILL"
                else "OBSERVED_%s" % venue_result,
            "REALIZED_EV_SOURCE": REALIZED_EV_SOURCE,
            "SNAPSHOT_MODEL_ROLE": SNAPSHOT_MODEL_IS,
            "WHY": "the observed execution is authoritative; the snapshot "
                   "estimate does not override it and is not re-scored "
                   "against it here"}


# ---------------------------------------------------------------------------
# THE REGISTRY
# ---------------------------------------------------------------------------
#
# ECONOMIC_QUANTITY is the key that detects double counting: two terms with the
# same quantity are the SAME MONEY expressed two ways, and a sum may contain at
# most one of them.
def _t(name, quantity, unit, sign, conditioning, horizon, source,
       uncertainty=NOT_IDENTIFIED, note=""):
    return {
        "TERM": name,
        "ECONOMIC_QUANTITY": quantity,
        "UNIT": unit,
        "SIGN": sign,
        "CONDITIONING": conditioning,
        "TIME_HORIZON": horizon,
        "SOURCE": source,
        "UNCERTAINTY": uncertainty,
        "NOTE": note,
    }


TERMS = {t["TERM"]: t for t in (
    # -- inside the fill branch -------------------------------------------
    _t("SPREAD_CAPTURE_CONDITIONAL_ON_FILL", "SPREAD_CAPTURE",
       USD_PER_CONTRACT, CREDIT, CONDITIONAL_ON_FILL, "AT_FILL",
       "BOOK_AT_QUOTE_TIME",
       note="what resting rather than crossing earns, if it fills"),
    _t("MAKER_REBATE_CONDITIONAL_ON_FILL", "MAKER_REBATE",
       USD, CREDIT, CONDITIONAL_ON_FILL, "AT_FILL", "FEES_V2_REGIME",
       note="EXACTLY zero below MINIMUM_CLIP_FOR_NONZERO_REBATE; the rebate "
            "rounds to the cent per fill"),
    _t("VERIFIED_INCENTIVES_CONDITIONAL_ON_FILL", "VERIFIED_INCENTIVES",
       USD, CREDIT, CONDITIONAL_ON_FILL, "AT_FILL", "VERIFIED_PROGRAMME_ONLY",
       note="never an estimate of an unannounced programme"),
    _t("ADVERSE_SELECTION_CONDITIONAL_ON_FILL", "ADVERSE_SELECTION",
       USD_PER_CONTRACT, DEBIT, CONDITIONAL_ON_FILL, "MARKOUT_HORIZON",
       "BETTOR_NATIVE_FILLS_REQUIRED", NOT_IDENTIFIED,
       note="E[markout | FILLED, state], signed against OUR side. NOT "
            "E[markout | state]: we fill when the other side wanted to trade"),
    _t("INVENTORY_COST_CONDITIONAL_ON_FILL", "INVENTORY_COST",
       USD, DEBIT, CONDITIONAL_ON_FILL, "HOLD_HORIZON",
       "WHALE_PRIOR_PLUS_CURRENT_STATE", NOT_IDENTIFIED,
       note="carried only if we filled; see INVENTORY_RISK_IS_NOT_ONLY_PQ"),
    _t("CLOSE_COST_CONDITIONAL_ON_FILL", "CLOSE_COST",
       USD, DEBIT, CONDITIONAL_ON_FILL, "TO_EXIT", "EXIT_ENGINE",
       NOT_IDENTIFIED),
    _t("SETTLEMENT_VALUE_CONDITIONAL_ON_FILL", "TERMINAL_VALUE",
       USD, SIGNED, CONDITIONAL_ON_FILL, "TO_SETTLEMENT", "FV_SETTLEMENT",
       NOT_IDENTIFIED),

    # -- outside both branches ---------------------------------------------
    _t("CAPITAL_RESERVATION_COST_UNCONDITIONAL", "CAPITAL_RESERVATION",
       USD, DEBIT, UNCONDITIONAL, "REST_DURATION", "COLLATERAL_MODEL",
       NOT_IDENTIFIED,
       note="charged whether or not the quote fills, for capital or a quote "
            "slot committed while resting. MEASURE it; if the venue reserves "
            "nothing against a resting post-only order it is near zero"),
    _t("QUEUE_PRIORITY_VALUE_UNCONDITIONAL", "QUEUE_PRIORITY",
       USD, CREDIT, UNCONDITIONAL, "REST_DURATION", "QUEUE_MODEL",
       NOT_IDENTIFIED,
       note="the option value of a queue position already held; a cancel "
            "forfeits it and a requote re-buys it at the back"),
    _t("EXPECTED_ADVERSE_SELECTION_UNCONDITIONAL", "ADVERSE_SELECTION",
       USD, DEBIT, UNCONDITIONAL, "MARKOUT_HORIZON",
       "P_FILL_TIMES_CONDITIONAL", NOT_IDENTIFIED,
       note="ALREADY contains its P_FILL factor. Multiplying it by P_FILL "
            "again is the classic double count this registry exists to stop"),
    _t("EXPECTED_INVENTORY_COST_UNCONDITIONAL", "INVENTORY_COST",
       USD, DEBIT, UNCONDITIONAL, "HOLD_HORIZON", "P_FILL_TIMES_CONDITIONAL",
       NOT_IDENTIFIED, note="as above"),

    # -- actions that execute now: no fill branch exists --------------------
    _t("LOCKED_PAIR_VALUE_CERTAIN", "LOCKED_PAIR_VALUE",
       USD, SIGNED, CERTAIN, "AT_EXECUTION", "EXECUTABLE_BOOK"),
    _t("EXECUTION_COSTS_CERTAIN", "EXECUTION_COSTS",
       USD, DEBIT, CERTAIN, "AT_EXECUTION", "FEES_V2_REGIME"),
    _t("TAKER_FEE_CERTAIN", "TAKER_FEE",
       USD, DEBIT, CERTAIN, "AT_EXECUTION", "FEES_V2_REGIME",
       note="theta * C * p * (1-p), regime-versioned, never pooled across "
            "the 2026-09-17T03:59Z cutover"),
)}

CONDITIONAL_TERMS = tuple(sorted(
    n for n, t in TERMS.items() if t["CONDITIONING"] == CONDITIONAL_ON_FILL))
UNCONDITIONAL_TERMS = tuple(sorted(
    n for n, t in TERMS.items() if t["CONDITIONING"] == UNCONDITIONAL))
CERTAIN_TERMS = tuple(sorted(
    n for n, t in TERMS.items() if t["CONDITIONING"] == CERTAIN))


def describe(term):
    if term not in TERMS:
        raise KeyError("unregistered EV term: %r. Register it with its unit, "
                       "sign, conditioning, horizon and source before using "
                       "it in a sum." % (term,))
    return dict(TERMS[term])


def conditioning_of(term):
    return describe(term)["CONDITIONING"]


# ---------------------------------------------------------------------------
# THE DOUBLE-COUNT CHECK
# ---------------------------------------------------------------------------
def check_no_double_count(conditional_names=(), unconditional_names=()):
    """Refuse a sum that contains one economic quantity twice.

    Two failure shapes, both real:

      1. The SAME quantity appears in both bags -- e.g. adverse selection
         charged inside the fill branch AND again outside. That is the same
         money twice.
      2. A term is placed in a bag its declared conditioning forbids -- e.g.
         EXPECTED_ADVERSE_SELECTION_UNCONDITIONAL (which already carries its
         own P_FILL) put inside a branch that will multiply it by P_FILL.

    Returns a report; raises DoubleCountError / ConditioningError on a break,
    because a silent EV that is wrong by a factor of P_FILL is worse than a
    crash.
    """
    for n in conditional_names:
        c = conditioning_of(n)
        if c != CONDITIONAL_ON_FILL:
            raise ConditioningError(
                "%s is %s and may not sit inside the fill branch: the branch "
                "multiplies by P_FILL, and this term %s" %
                (n, c, "already contains it" if c == UNCONDITIONAL
                 else "belongs to an action that executes now"))
    for n in unconditional_names:
        c = conditioning_of(n)
        if c == CONDITIONAL_ON_FILL:
            raise ConditioningError(
                "%s is CONDITIONAL_ON_FILL and may not sit outside the fill "
                "branch: outside, it would be charged even when the quote "
                "does not fill" % (n,))

    qc = {TERMS[n]["ECONOMIC_QUANTITY"] for n in conditional_names}
    qu = {TERMS[n]["ECONOMIC_QUANTITY"] for n in unconditional_names}
    both = sorted(qc & qu)
    if both:
        raise DoubleCountError(
            "the same economic quantity appears inside AND outside the fill "
            "branch: %s. Choose one form." % ", ".join(both))

    dupes = sorted(q for q in qc | qu
                   if [TERMS[n]["ECONOMIC_QUANTITY"]
                       for n in tuple(conditional_names) +
                       tuple(unconditional_names)].count(q) > 1)
    if dupes:
        raise DoubleCountError(
            "one economic quantity named by two terms in the same bag: %s"
            % ", ".join(sorted(set(dupes))))

    return {"DOUBLE_COUNT_CHECK": "PASS",
            "CONDITIONAL_QUANTITIES": sorted(qc),
            "UNCONDITIONAL_QUANTITIES": sorted(qu)}


# ---------------------------------------------------------------------------
# THE MISSING FUNCTION: A PASSIVE QUOTE'S EV
# ---------------------------------------------------------------------------
PARTIAL_FILL_IS_ITS_OWN_OUTCOME = True
WHY_PARTIAL_MATTERS = (
    "a room-clipped or partially filled rest leaves inventory AND a live "
    "remainder; collapsing it into FULL or NONE misprices both")

# ---------------------------------------------------------------------------
# CORRECTION 6 -- THE NO-FILL BRANCH IS EXPLICIT, DECLARED, AND NEVER A ZERO
#                 BY DEFAULT
# ---------------------------------------------------------------------------
#
# A previous version defaulted VALUE_IF_NO_FILL to 0. For a NEW quote on a
# flat book that is correct -- no fill, no position, nothing carried. But the
# same function prices `A_PASSIVE_SELL_EXIT` and `A_PASSIVE_COMPLEMENT_PAIR`,
# and in BOTH of those we are ALREADY EXPOSED when the quote misses. There,
# zero is not "nothing happened": it is silently valuing a position we still
# hold at nothing, which understates the cost of a missed exit exactly when it
# matters most.
#
# So the no-fill state is now DECLARED, the horizon it is valued over is
# declared with it, and an unpriced continuation is NOT_IDENTIFIED rather than
# zero. The default is a sentinel, not a number: a caller who says nothing
# gets a refusal naming the three states.
NO_FILL_BRANCH_NOT_DECLARED = "NO_FILL_BRANCH_NOT_DECLARED"
NO_EXPOSURE_CARRIED = "NO_EXPOSURE_CARRIED"
QUEUE_POSITION_RETAINED = "QUEUE_POSITION_RETAINED"
EXPOSURE_CONTINUES = "EXPOSURE_CONTINUES"
NO_FILL_STATES = (NO_EXPOSURE_CARRIED, QUEUE_POSITION_RETAINED,
                  EXPOSURE_CONTINUES)
EV_IF_NO_FILL_MAY_DEFAULT_TO_ZERO_ONLY_WHEN = NO_EXPOSURE_CARRIED
WHY_ZERO_IS_WRONG_WHEN_EXPOSED = (
    "a missed passive exit leaves the position on the book: inventory cost, "
    "markout, close cost and settlement risk all continue. Valuing that at "
    "zero prices the miss as free")


def no_fill_branch(state, horizon=None, continuation_value=None):
    """Declare what the quote leaves behind when it does not fill.

    Returns EV_IF_NO_FILL with its state and horizon. The only state that may
    carry a zero without a number being supplied is NO_EXPOSURE_CARRIED --
    a new quote on a flat book. Both other states demand a continuation value
    and return NOT_IDENTIFIED when one is not supplied.
    """
    if state not in NO_FILL_STATES:
        raise NoFillBranchError(
            "NO_FILL_STATE must be one of %s, not %r"
            % (", ".join(NO_FILL_STATES), state))
    if state == NO_EXPOSURE_CARRIED:
        if continuation_value is None:
            continuation_value = D("0")
        return {"NO_FILL_STATE": state,
                "EV_IF_NO_FILL": continuation_value,
                "NO_FILL_TIME_HORIZON": horizon or "AT_QUOTE_EXPIRY",
                "ZERO_IS_JUSTIFIED_BECAUSE":
                    "no fill means no position, no inventory and no markout"}
    if horizon is None:
        raise NoFillBranchError(
            "NO_FILL_STATE=%s carries value forward, so it needs an explicit "
            "NO_FILL_TIME_HORIZON: over what period is the continuation "
            "valued?" % state)
    if continuation_value is None:
        return {"NO_FILL_STATE": state,
                "EV_IF_NO_FILL": NOT_IDENTIFIED,
                "NO_FILL_TIME_HORIZON": horizon,
                "WHY_NOT_ZERO": WHY_ZERO_IS_WRONG_WHEN_EXPOSED}
    return {"NO_FILL_STATE": state,
            "EV_IF_NO_FILL": continuation_value,
            "NO_FILL_TIME_HORIZON": horizon}


def ev_maker_quote(p_full_fill, conditional_terms=None,
                   unconditional_terms=None, p_partial_fill=None,
                   partial_terms=None,
                   value_if_no_fill=NO_FILL_BRANCH_NOT_DECLARED,
                   no_fill_state=None, no_fill_horizon=None,
                   p_fill_source=None):
    """EV of RESTING a quote. The branch structure is the point.

        EV = P_FULL      * sum(conditional terms at full size)
           + P_PARTIAL   * sum(conditional terms at partial size)
           + P_NO_FILL   * EV_IF_NO_FILL
           + sum(unconditional terms)

    THE NO-FILL BRANCH IS MANDATORY. Pass either `no_fill_state` (and, for a
    state that carries exposure forward, `no_fill_horizon` and a continuation
    value in `value_if_no_fill`) or a `value_if_no_fill` that is already a
    declared branch payload from `no_fill_branch`. There is no zero default:
    for a passive EXIT the position survives the miss, and valuing it at zero
    prices the miss as free.

    `p_fill_source`, when given, is checked: a whale completion hazard may
    never seed a BETTOR fill probability (whale_bridge correction 5).

    Every term is checked against its declared conditioning first. A
    NOT_IDENTIFIED anywhere propagates and names itself, exactly as ev_sum
    does -- an unpriced branch is not a zero branch.
    """
    conditional_terms = dict(conditional_terms or {})
    unconditional_terms = dict(unconditional_terms or {})
    partial_terms = dict(partial_terms or {})

    if p_fill_source is not None:
        from whale_bridge import assert_p_fill_source
        assert_p_fill_source(p_fill_source)

    # Resolve the no-fill branch BEFORE any arithmetic, so a missing
    # declaration is a refusal rather than a quietly-zero branch.
    if isinstance(value_if_no_fill, dict):
        branch = dict(value_if_no_fill)
    elif no_fill_state is not None:
        branch = no_fill_branch(
            no_fill_state, no_fill_horizon,
            None if value_if_no_fill == NO_FILL_BRANCH_NOT_DECLARED
            else value_if_no_fill)
    elif value_if_no_fill == NO_FILL_BRANCH_NOT_DECLARED:
        raise NoFillBranchError(
            "the no-fill branch is not declared. Pass no_fill_state=%s for a "
            "new quote on a flat book, or %s / %s with a "
            "no_fill_horizon when the quote leaves something behind. %s"
            % (NO_EXPOSURE_CARRIED, QUEUE_POSITION_RETAINED,
               EXPOSURE_CONTINUES, WHY_ZERO_IS_WRONG_WHEN_EXPOSED))
    else:
        raise NoFillBranchError(
            "value_if_no_fill was given as a bare number (%r) with no "
            "NO_FILL_STATE. The state and its horizon must be declared: a "
            "number alone does not say whether we are still exposed."
            % (value_if_no_fill,))

    # FULL and PARTIAL are MUTUALLY EXCLUSIVE branches, so the same quantity
    # appearing in both is correct -- it is the same economics at a different
    # size, and at most one branch is realised. Each is therefore checked
    # against the unconditional bag SEPARATELY. Merging them would flag the
    # legitimate repeat as a double count, which is the error this comment
    # exists to stop someone "simplifying" back in.
    check_no_double_count(tuple(conditional_terms),
                          tuple(unconditional_terms))
    if partial_terms:
        check_no_double_count(tuple(partial_terms),
                              tuple(unconditional_terms))

    missing = []
    if isinstance(p_full_fill, str) or p_full_fill is None:
        missing.append("P_FULL_FILL")
    if p_partial_fill is not None and isinstance(p_partial_fill, str):
        missing.append("P_PARTIAL_FILL")
    ev_no_fill = branch.get("EV_IF_NO_FILL")
    if ev_no_fill is None or (isinstance(ev_no_fill, str)
                              and ev_no_fill == NOT_IDENTIFIED):
        # An exposed no-fill state with no continuation value is NOT a zero
        # branch. It propagates, exactly as any other unpriced term does.
        missing.append("EV_IF_NO_FILL[%s]" % branch.get("NO_FILL_STATE"))

    def _bag(d, label):
        tot = D("0")
        for n, v in sorted(d.items()):
            if v is None or (isinstance(v, str) and v == NOT_IDENTIFIED):
                missing.append(n)
                continue
            if isinstance(v, float):
                raise TypeError("refusing a float EV term %r: pass a Decimal "
                                "or an exact decimal string" % (n,))
            tot += v if isinstance(v, D) else D(str(v))
        return tot

    inside_full = _bag(conditional_terms, "FULL")
    inside_part = _bag(partial_terms, "PARTIAL") if partial_terms else D("0")
    outside = _bag(unconditional_terms, "UNCONDITIONAL")

    if missing:
        return NOT_IDENTIFIED, tuple(sorted(set(missing)))

    pf = D(str(p_full_fill))
    pp = D(str(p_partial_fill)) if p_partial_fill is not None else D("0")
    if not (D("0") <= pf <= D("1")) or not (D("0") <= pp <= D("1")):
        raise ValueError("fill probabilities out of [0,1]: %s, %s" % (pf, pp))
    if pf + pp > D("1"):
        raise ValueError("P_FULL_FILL + P_PARTIAL_FILL exceeds 1: %s + %s"
                         % (pf, pp))
    p_no = D("1") - pf - pp
    nofill = (ev_no_fill if isinstance(ev_no_fill, D)
              else D(str(ev_no_fill)))

    return (pf * inside_full + pp * inside_part + p_no * nofill + outside), ()


# ---------------------------------------------------------------------------
# THE THREE SEMANTIC GUARDS
# ---------------------------------------------------------------------------
#
# 1. p(1-p) IS ONE COMPONENT, NOT THE MEASURE.
P_TIMES_ONE_MINUS_P_IS = "TERMINAL_BERNOULLI_PAYOUT_VARIANCE"
P_TIMES_ONE_MINUS_P_IS_NOT = "THE_COMPLETE_MEASURE_OF_INVENTORY_RISK"
INVENTORY_RISK_ALSO_DEPENDS_ON = (
    "POSITION_SIZE", "TIME_TO_SETTLEMENT", "CURRENT_PRICE",
    "SHORT_HORIZON_PRICE_DYNAMICS", "INFORMATION_REGIME", "LIQUIDITY",
    "CORRELATION", "EXIT_ALTERNATIVES", "CAPITAL_OCCUPANCY",
)

# 2. SETTLEMENT IS NOT A FREE EXIT.
SETTLEMENT_IS = ("SETTLEMENT PROVIDES THE CONTRACT'S TERMINAL PAYOFF SUBJECT "
                 "TO RESOLUTION, TIMING, COLLATERAL AND VENUE RISK")
SETTLEMENT_IS_NOT = "A_GUARANTEED_ZERO_SPREAD_EXIT"
SETTLEMENT_RISKS = ("RESOLUTION_RISK", "TIMING_RISK", "COLLATERAL_RISK",
                    "VENUE_RISK")

# 3. QUEUE IMBALANCE IS A DIAGNOSTIC UNTIL BETTOR HAS FILLS.
QUEUE_IMBALANCE_STATUS = "MICROSTRUCTURE_DIAGNOSTIC"
QUEUE_IMBALANCE_IS_NOT_YET = "BETTOR_ADVERSE_SELECTION_CONDITIONAL_ON_FILL"
WHY = ("a book-state predictor of future mid is not a model of what happens "
       "to OUR fills, because we have no admitted fills to condition on")

# 4. NO_TRADE IS NOT FORCED TO ZERO.
EV_NO_TRADE_IS_FORCED_TO_ZERO = False
WAITING_MAY_HAVE_OPTION_VALUE = True
EV_WAIT_COMPONENTS = ("FUTURE_INFORMATION", "FUTURE_QUOTE_IMPROVEMENT",
                      "TOXICITY_DECAY", "CAPITAL_PRESERVATION",
                      "BETTER_OPPORTUNITIES", "FUTURE_COMPLETION_STATES")
EV_WAIT_NUMERIC_VALUE = NOT_IDENTIFIED     # earned from data, never invented


def semantic_guards():
    """The guards as a reportable block, so a receipt can carry them."""
    return {
        "P_TIMES_ONE_MINUS_P_IS": P_TIMES_ONE_MINUS_P_IS,
        "P_TIMES_ONE_MINUS_P_IS_NOT": P_TIMES_ONE_MINUS_P_IS_NOT,
        "INVENTORY_RISK_ALSO_DEPENDS_ON": list(INVENTORY_RISK_ALSO_DEPENDS_ON),
        "SETTLEMENT_IS": SETTLEMENT_IS,
        "SETTLEMENT_IS_NOT": SETTLEMENT_IS_NOT,
        "SETTLEMENT_RISKS": list(SETTLEMENT_RISKS),
        "QUEUE_IMBALANCE_STATUS": QUEUE_IMBALANCE_STATUS,
        "QUEUE_IMBALANCE_IS_NOT_YET": QUEUE_IMBALANCE_IS_NOT_YET,
        "EV_NO_TRADE_IS_FORCED_TO_ZERO": EV_NO_TRADE_IS_FORCED_TO_ZERO,
        "EV_WAIT_NUMERIC_VALUE": EV_WAIT_NUMERIC_VALUE,
        # 5. THE NO-FILL BRANCH IS DECLARED, AND ZERO IS NOT ITS DEFAULT.
        "PASSIVE_NO_FILL_BRANCH": "EXPLICIT",
        "NO_FILL_STATES": list(NO_FILL_STATES),
        "EV_IF_NO_FILL_MAY_DEFAULT_TO_ZERO_ONLY_WHEN":
            EV_IF_NO_FILL_MAY_DEFAULT_TO_ZERO_ONLY_WHEN,
        "WHY_ZERO_IS_WRONG_WHEN_EXPOSED": WHY_ZERO_IS_WRONG_WHEN_EXPOSED,
        # 6. CERTAIN IS GATED ON PROVEN DISPLAYED DEPTH -- AND THAT GATE IS
        #    ABOUT THE SNAPSHOT, NOT ABOUT A LATER LIVE ORDER.
        "AGGRESSIVE_SNAPSHOT_EXECUTABILITY_GATE":
            AGGRESSIVE_SNAPSHOT_EXECUTABILITY_GATE,
        "LIVE_AGGRESSIVE_FILL_CERTAINTY": LIVE_AGGRESSIVE_FILL_CERTAINTY,
        "CERTAIN_REQUIRES": CERTAIN_REQUIRES,
        "CERTAIN_IS_NOT_ESTABLISHED_BY": list(CERTAIN_IS_NOT_ESTABLISHED_BY),
        "SNAPSHOT_CALCULATION_LABEL": SNAPSHOT_CALCULATION_LABEL,
        "SNAPSHOT_CALCULATION_IS_NOT": SNAPSHOT_CALCULATION_IS_NOT,
        "REALIZED_EV_SOURCE": REALIZED_EV_SOURCE,
    }


def render_registry():
    L = ["%-46s %-22s %-6s %-22s %s"
         % ("TERM", "QUANTITY", "SIGN", "CONDITIONING", "HORIZON")]
    for n in sorted(TERMS):
        t = TERMS[n]
        L.append("%-46s %-22s %-6s %-22s %s"
                 % (n, t["ECONOMIC_QUANTITY"], t["SIGN"], t["CONDITIONING"],
                    t["TIME_HORIZON"]))
    return "\n".join(L)


if __name__ == "__main__":                                    # pragma: no cover
    print(render_registry())
    for k, v in sorted(semantic_guards().items()):
        print("%-38s = %s" % (k, v))
