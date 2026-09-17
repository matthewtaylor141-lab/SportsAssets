"""Sections I, J, M. Inventory skew, the quote gate, and FILL_CONDITIONED_ACTION_EV.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING HERE PLACES OR SIZES A REAL ORDER.

THE FINAL EDGE IS NOT PRICE PREDICTION
--------------------------------------
It is FILL_CONDITIONED_ACTION_EV. A model that predicts the next mid perfectly
earns nothing if it never fills, fills only when wrong, or fills into a fee
that exceeds the move. So every candidate action is priced as a whole object,
in exact decimal, and any missing term makes the whole EV NOT_IDENTIFIED rather
than dropping to zero.

WHY A MISSING TERM IS NOT ZERO
------------------------------
Treating an unmeasured P_FILL as 1.0, or an unmeasured adverse selection as
0.0, produces a confident positive EV built on the terms nobody measured. Those
are exactly the terms that turn a apparent edge negative. So the arithmetic
REFUSES: UNKNOWN propagates, and the action is not recommended.

THE DOUBLE-COUNT
----------------
If VALUE_CONDITIONAL_ON_FILL was estimated on realised fills, it ALREADY
contains adverse selection. Subtracting EXPECTED_ADVERSE_SELECTION again
double-counts it and makes every quote look worse than it is. The convention is
declared per receipt and enforced.
"""

from decimal import Decimal, ROUND_HALF_EVEN

NOT_IDENTIFIED = "NOT_IDENTIFIED"

NOTHING_IS_PLACED = True

THE_FINAL_EDGE = "FILL_CONDITIONED_ACTION_EV"
NOT_THE_EDGE = "PRICE_PREDICTION"

UNKNOWN_IS_NOT_ZERO = (
    "an unmeasured P_FILL is not 1.0 and an unmeasured adverse selection is "
    "not 0.0. Those substitutions produce a confident positive EV built "
    "precisely on the terms nobody measured")


def _d(x):
    """Exact decimal from a string or integer. Never from a binary float."""
    if x is None or x == NOT_IDENTIFIED:
        return None
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        # A float that reached a money path is a defect, not a value to round.
        raise TypeError(
            "FLOAT_IN_A_MONEY_PATH: pass a decimal string, not %r. "
            "float(0.1) is not 0.1, and an economic threshold decided by "
            "binary floating point is decided by its rounding error" % (x,))
    return Decimal(str(x))


# --- Section M. The terms of one action. -----------------------------------

ACTION_TERMS = (
    "ACTION", "PRICE", "SIZE",
    "P_FILL",
    "EXPECTED_VALUE_IF_FILLED",
    "EXPECTED_ADVERSE_SELECTION",
    "FEE",
    "REBATE_OR_INCENTIVE",
    "INVENTORY_COST",
    "CAPITAL_REQUIRED",
    "EXPECTED_OCCUPANCY_TIME",
    "EXIT_OPTIONALITY",
    "UNCERTAINTY",
    "EXPECTED_NET_DOLLARS",
    "EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR",
)

CRITICAL_TERMS = ("P_FILL", "EXPECTED_VALUE_IF_FILLED", "FEE",
                  "EXPECTED_ADVERSE_SELECTION")

ADVERSE_SELECTION_CONVENTIONS = ("EMBEDDED_IN_VALUE_CONDITIONAL_ON_FILL",
                                 "SEPARATE_TERM")

DOUBLE_COUNT_WARNING = (
    "if VALUE_CONDITIONAL_ON_FILL was estimated on realised fills it ALREADY "
    "contains adverse selection. Subtracting it again double-counts and makes "
    "every quote look worse than it is. Declare which convention applies")

ACTIONS = ("MAKER_QUOTE", "TAKER_CROSS", "CANCEL", "NO_ACTION")


def action_ev(action, price=None, size=None, p_fill=None,
              expected_value_if_filled=None, expected_adverse_selection=None,
              fee=None, rebate_or_incentive=None, inventory_cost=None,
              capital_required=None, expected_occupancy_hours=None,
              exit_optionality=None, uncertainty=None,
              adverse_selection_convention="SEPARATE_TERM"):
    """Price one candidate action. Exact decimal. Fails closed on any unknown.

    Returns EXPECTED_NET_DOLLARS = NOT_IDENTIFIED unless every critical term is
    present. RECOMMENDED is never True on an unidentified EV.
    """
    if action not in ACTIONS:
        return {"ACTION": action, "STATUS": "UNKNOWN_ACTION",
                "DECLARED": ACTIONS, "RECOMMENDED": False}
    if adverse_selection_convention not in ADVERSE_SELECTION_CONVENTIONS:
        return {"ACTION": action, "STATUS": "UNKNOWN_CONVENTION",
                "DECLARED": ADVERSE_SELECTION_CONVENTIONS,
                "RECOMMENDED": False}

    terms = {
        "P_FILL": _d(p_fill),
        "EXPECTED_VALUE_IF_FILLED": _d(expected_value_if_filled),
        "EXPECTED_ADVERSE_SELECTION": _d(expected_adverse_selection),
        "FEE": _d(fee),
        "REBATE_OR_INCENTIVE": _d(rebate_or_incentive),
        "INVENTORY_COST": _d(inventory_cost),
        "CAPITAL_REQUIRED": _d(capital_required),
    }
    missing = [k for k in CRITICAL_TERMS
               if terms.get(k) is None
               and not (k == "EXPECTED_ADVERSE_SELECTION"
                        and adverse_selection_convention ==
                        "EMBEDDED_IN_VALUE_CONDITIONAL_ON_FILL")]

    out = {
        "ACTION": action,
        "PRICE": str(_d(price)) if price is not None else NOT_IDENTIFIED,
        "SIZE": str(_d(size)) if size is not None else NOT_IDENTIFIED,
        "ADVERSE_SELECTION_CONVENTION": adverse_selection_convention,
        "DOUBLE_COUNT_WARNING": DOUBLE_COUNT_WARNING,
        "EXPECTED_OCCUPANCY_TIME": (expected_occupancy_hours
                                    if expected_occupancy_hours is not None
                                    else NOT_IDENTIFIED),
        "EXIT_OPTIONALITY": (exit_optionality if exit_optionality is not None
                             else NOT_IDENTIFIED),
        "UNCERTAINTY": (uncertainty if uncertainty is not None
                        else NOT_IDENTIFIED),
        "MISSING_CRITICAL_TERMS": missing,
        "UNKNOWN_IS_NOT_ZERO": UNKNOWN_IS_NOT_ZERO,
    }
    for k, v in terms.items():
        out[k] = str(v) if v is not None else NOT_IDENTIFIED

    if missing:
        out.update({
            "EXPECTED_NET_DOLLARS": NOT_IDENTIFIED,
            "EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR": NOT_IDENTIFIED,
            "RECOMMENDED": False,
            "WHY_NOT": ("%d critical term(s) are not identified, so the EV "
                        "is not identified either" % len(missing)),
        })
        return out

    value = terms["EXPECTED_VALUE_IF_FILLED"]
    if adverse_selection_convention == "SEPARATE_TERM":
        value = value - terms["EXPECTED_ADVERSE_SELECTION"]
    # else: already embedded; subtracting again would double-count.

    gross = terms["P_FILL"] * value
    net = (gross
           - terms["FEE"]
           + (terms["REBATE_OR_INCENTIVE"] or Decimal("0"))
           - (terms["INVENTORY_COST"] or Decimal("0")))
    q = Decimal("0.000001")
    out["EXPECTED_NET_DOLLARS"] = str(net.quantize(q, ROUND_HALF_EVEN))

    cap = terms["CAPITAL_REQUIRED"]
    hours = _d(expected_occupancy_hours) if expected_occupancy_hours is not \
        None else None
    if cap and hours and cap > 0 and hours > 0:
        out["EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR"] = str(
            (net / (cap * hours)).quantize(q, ROUND_HALF_EVEN))
    else:
        out["EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR"] = NOT_IDENTIFIED
    out["RECOMMENDED"] = net > 0
    return out


# --- Section J. The quote gate. --------------------------------------------

GATE_RULE = "EXPECTED_NET_EV > UNCERTAINTY_BUFFER"

SIMPLE_CHALLENGER = "AVAILABLE_SPREAD_OVER_SHORT_HORIZON_VOLATILITY"
WHY_A_SIMPLE_CHALLENGER = (
    "a published-style ratio of available spread to short-horizon volatility "
    "is cheap, interpretable and may well beat the full EV gate. It is "
    "evaluated as a peer, not as a sanity check")

DO_NOT_TUNE_ON_TEST = (
    "gate thresholds are NOT tuned on the final test data. A threshold chosen "
    "to make the test look good has been fitted to the test")


def quote_gate(ev_row, uncertainty_buffer=None):
    """Quote only when EV exceeds the buffer. Unknown EV never quotes."""
    ev = (ev_row or {}).get("EXPECTED_NET_DOLLARS", NOT_IDENTIFIED)
    if ev == NOT_IDENTIFIED or uncertainty_buffer is None:
        return {"QUOTE": False, "GATE_RULE": GATE_RULE,
                "EXPECTED_NET_DOLLARS": ev,
                "UNCERTAINTY_BUFFER": (uncertainty_buffer
                                       if uncertainty_buffer is not None
                                       else NOT_IDENTIFIED),
                "WHY_NOT": ("EV or buffer not identified. An unidentified EV "
                            "does not clear any buffer"),
                "UNKNOWN_IS_NOT_ZERO": UNKNOWN_IS_NOT_ZERO}
    net, buf = _d(ev), _d(uncertainty_buffer)
    ok = net > buf
    return {"QUOTE": ok, "GATE_RULE": GATE_RULE,
            "EXPECTED_NET_DOLLARS": str(net),
            "UNCERTAINTY_BUFFER": str(buf),
            "MARGIN": str(net - buf),
            "DO_NOT_TUNE_ON_TEST": DO_NOT_TUNE_ON_TEST}


def simple_gate_challenger(available_spread=None, short_horizon_vol=None,
                           threshold=None):
    """The published-style challenger, scored as a peer of the EV gate."""
    if available_spread is None or short_horizon_vol is None or threshold is None:
        return {"CHALLENGER": SIMPLE_CHALLENGER, "QUOTE": False,
                "RATIO": NOT_IDENTIFIED,
                "WHY_NOT": "spread, volatility or threshold not identified"}
    v = _d(short_horizon_vol)
    if not v or v <= 0:
        return {"CHALLENGER": SIMPLE_CHALLENGER, "QUOTE": False,
                "RATIO": NOT_IDENTIFIED,
                "WHY_NOT": "zero or negative volatility gives no ratio"}
    ratio = _d(available_spread) / v
    return {"CHALLENGER": SIMPLE_CHALLENGER, "RATIO": str(ratio),
            "THRESHOLD": str(_d(threshold)),
            "QUOTE": ratio > _d(threshold),
            "WHY_A_SIMPLE_CHALLENGER": WHY_A_SIMPLE_CHALLENGER,
            "DO_NOT_TUNE_ON_TEST": DO_NOT_TUNE_ON_TEST}


# --- Section I. Inventory skew challengers. --------------------------------

SKEW_CHALLENGERS = ("NO_SKEW", "SYMMETRIC_SKEW", "ASYMMETRIC_HEAVY_SIDE_SKEW",
                    "EV_OPTIMAL_SKEW")

FLATTENING_IS_NOT_AUTOMATICALLY_RIGHT = (
    "inventory should not always be flattened. On a binary event contract a "
    "position held to settlement pays 0 or 1 with no further spread cost, so "
    "flattening can be strictly worse than holding")

SKEW_MUST_CONSIDER = ("SETTLEMENT_VALUE", "PAIR_OPTIONALITY",
                      "HEDGE_AVAILABILITY", "CAPITAL_OCCUPANCY",
                      "CORRELATION")

SKEW_STATUS = NOT_IDENTIFIED


def skew_comparison(results=None):
    """Compare skew policies. Refuses to crown one without all four measured."""
    results = results or {}
    missing = [s for s in SKEW_CHALLENGERS if s not in results]
    if missing:
        return {"SKEW_STATUS": SKEW_STATUS,
                "CHALLENGERS": SKEW_CHALLENGERS,
                "NOT_EVALUATED": missing,
                "BEST": NOT_IDENTIFIED,
                "WHY": ("a winner cannot be named while %d policy(ies) were "
                        "never evaluated" % len(missing)),
                "FLATTENING_IS_NOT_AUTOMATICALLY_RIGHT":
                    FLATTENING_IS_NOT_AUTOMATICALLY_RIGHT,
                "SKEW_MUST_CONSIDER": SKEW_MUST_CONSIDER}
    best = max(SKEW_CHALLENGERS, key=lambda s: results[s])
    return {"SKEW_STATUS": "MEASURED", "RESULTS": dict(results),
            "BEST": best,
            "FLATTENING_IS_NOT_AUTOMATICALLY_RIGHT":
                FLATTENING_IS_NOT_AUTOMATICALLY_RIGHT,
            "SKEW_MUST_CONSIDER": SKEW_MUST_CONSIDER}


def describe():
    return {
        "THE_FINAL_EDGE": THE_FINAL_EDGE,
        "NOT_THE_EDGE": NOT_THE_EDGE,
        "ACTION_TERMS": ACTION_TERMS,
        "CRITICAL_TERMS": CRITICAL_TERMS,
        "UNKNOWN_IS_NOT_ZERO": UNKNOWN_IS_NOT_ZERO,
        "ADVERSE_SELECTION_CONVENTIONS": ADVERSE_SELECTION_CONVENTIONS,
        "DOUBLE_COUNT_WARNING": DOUBLE_COUNT_WARNING,
        "GATE_RULE": GATE_RULE,
        "SIMPLE_CHALLENGER": SIMPLE_CHALLENGER,
        "DO_NOT_TUNE_ON_TEST": DO_NOT_TUNE_ON_TEST,
        "SKEW_CHALLENGERS": SKEW_CHALLENGERS,
        "FLATTENING_IS_NOT_AUTOMATICALLY_RIGHT":
            FLATTENING_IS_NOT_AUTOMATICALLY_RIGHT,
        "SKEW_MUST_CONSIDER": SKEW_MUST_CONSIDER,
        "NOTHING_IS_PLACED": NOTHING_IS_PLACED,
    }
