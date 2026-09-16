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


def ev_maker_quote(p_full_fill, conditional_terms=None,
                   unconditional_terms=None, p_partial_fill=None,
                   partial_terms=None, value_if_no_fill=D("0")):
    """EV of RESTING a quote. The branch structure is the point.

        EV = P_FULL      * sum(conditional terms at full size)
           + P_PARTIAL   * sum(conditional terms at partial size)
           + P_NO_FILL   * VALUE_IF_NO_FILL
           + sum(unconditional terms)

    VALUE_IF_NO_FILL defaults to zero for the QUOTE ITSELF -- no fill means no
    position, no inventory, no markout. It is a parameter rather than a
    constant because a quote that expires can still leave something behind
    (a retained queue position, say), and that is an observation, not a zero.

    Every term is checked against its declared conditioning first. A
    NOT_IDENTIFIED anywhere propagates and names itself, exactly as ev_sum
    does -- an unpriced branch is not a zero branch.
    """
    conditional_terms = dict(conditional_terms or {})
    unconditional_terms = dict(unconditional_terms or {})
    partial_terms = dict(partial_terms or {})

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
    nofill = (D(str(value_if_no_fill))
              if not isinstance(value_if_no_fill, D) else value_if_no_fill)

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
