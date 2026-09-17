"""Sections 15 and 16. Per-leg inventory, and capital-hour accounting.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IS PLACED. Exact decimal throughout.

WHY THE LEGS ARE NOT NETTED
---------------------------
YES and NO on the same condition are complements, so a naive engine nets them
to a single scalar and reports "flat". They are not the same thing:

  - 100 YES and 100 NO is a MATCHED pair. It carries locked P&L equal to
    (1 - basis), occupies capital, and has no directional exposure.
  - 0 YES and 0 NO is genuinely flat. It occupies nothing.

Both net to zero. Reporting them identically loses the locked profit, the
capital tied up earning it, and the fact that one position needs managing and
the other does not. So every leg is tracked separately and the pair is a
DERIVED view, never the stored one.

CAPITAL-HOURS DO NOT REPLACE EV
-------------------------------
EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR is how a high-turnover book is ranked.
It is NOT a substitute for EXPECTED_NET_DOLLARS: a trade earning a cent in a
second has a spectacular hourly rate and still earns a cent. Both are reported,
always, side by side.
"""

from decimal import Decimal, ROUND_HALF_EVEN

NOT_IDENTIFIED = "NOT_IDENTIFIED"

NOTHING_IS_PLACED = True
ORDER_PATH_EXISTS = False

LEG_FIELDS = ("YES_QTY", "YES_AVG_COST", "NO_QTY", "NO_AVG_COST",
              "MATCHED_QTY", "RESIDUAL_YES", "RESIDUAL_NO", "PAIR_BASIS",
              "LOCKED_PNL", "UNLOCKED_EXPOSURE", "CAPITAL_OCCUPIED",
              "TIME_IN_INVENTORY")

DO_NOT_NET_THE_LEGS = (
    "100 YES with 100 NO and a genuinely flat book both net to zero. The "
    "first carries locked P&L, occupies capital and needs managing; the "
    "second does not. Netting reports them identically and loses all three "
    "facts")

Q = Decimal("0.00000001")


def _d(x):
    """Exact decimal. A binary float in a money path is a defect."""
    if x is None or x == NOT_IDENTIFIED:
        return None
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        raise TypeError(
            "FLOAT_IN_A_MONEY_PATH: pass a decimal string, not %r" % (x,))
    return Decimal(str(x))


def inventory(yes_qty="0", yes_avg_cost=None, no_qty="0", no_avg_cost=None,
              time_in_inventory_s=None):
    """The per-leg state. Both legs stored; the pair view is derived."""
    yq, nq = _d(yes_qty) or Decimal("0"), _d(no_qty) or Decimal("0")
    yc, nc = _d(yes_avg_cost), _d(no_avg_cost)

    matched = min(yq, nq)
    res_yes = yq - matched
    res_no = nq - matched

    out = {
        "YES_QTY": str(yq), "YES_AVG_COST": str(yc) if yc is not None
        else NOT_IDENTIFIED,
        "NO_QTY": str(nq), "NO_AVG_COST": str(nc) if nc is not None
        else NOT_IDENTIFIED,
        "MATCHED_QTY": str(matched),
        "RESIDUAL_YES": str(res_yes),
        "RESIDUAL_NO": str(res_no),
        "DO_NOT_NET_THE_LEGS": DO_NOT_NET_THE_LEGS,
        "TIME_IN_INVENTORY": (time_in_inventory_s
                              if time_in_inventory_s is not None
                              else NOT_IDENTIFIED),
    }

    # PAIR_BASIS is what the matched pair cost. It settles at exactly 1.
    if matched > 0 and yc is not None and nc is not None:
        basis = yc + nc
        out["PAIR_BASIS"] = str(basis.quantize(Q, ROUND_HALF_EVEN))
        out["LOCKED_PNL"] = str(
            ((Decimal("1") - basis) * matched).quantize(Q, ROUND_HALF_EVEN))
        out["CAPITAL_OCCUPIED_BY_PAIR"] = str(
            (basis * matched).quantize(Q, ROUND_HALF_EVEN))
    else:
        out["PAIR_BASIS"] = NOT_IDENTIFIED
        out["LOCKED_PNL"] = (str(Decimal("0")) if matched == 0
                             else NOT_IDENTIFIED)
        out["CAPITAL_OCCUPIED_BY_PAIR"] = (str(Decimal("0")) if matched == 0
                                           else NOT_IDENTIFIED)
        if matched > 0:
            out["WHY_LOCKED_PNL_UNKNOWN"] = (
                "a matched pair exists but one leg's average cost is not "
                "known, so the basis cannot be computed. It is not zero")

    # Unlocked exposure is the residual leg only -- the part that can still move.
    res_qty = res_yes if res_yes > 0 else res_no
    res_cost = yc if res_yes > 0 else (nc if res_no > 0 else None)
    if res_qty == 0:
        out["UNLOCKED_EXPOSURE"] = str(Decimal("0"))
        out["CAPITAL_OCCUPIED_BY_RESIDUAL"] = str(Decimal("0"))
    elif res_cost is None:
        out["UNLOCKED_EXPOSURE"] = NOT_IDENTIFIED
        out["CAPITAL_OCCUPIED_BY_RESIDUAL"] = NOT_IDENTIFIED
    else:
        out["UNLOCKED_EXPOSURE"] = str(res_qty)
        out["CAPITAL_OCCUPIED_BY_RESIDUAL"] = str(
            (res_cost * res_qty).quantize(Q, ROUND_HALF_EVEN))

    cap_p = out["CAPITAL_OCCUPIED_BY_PAIR"]
    cap_r = out["CAPITAL_OCCUPIED_BY_RESIDUAL"]
    if NOT_IDENTIFIED in (cap_p, cap_r):
        out["CAPITAL_OCCUPIED"] = NOT_IDENTIFIED
    else:
        out["CAPITAL_OCCUPIED"] = str(
            (Decimal(cap_p) + Decimal(cap_r)).quantize(Q, ROUND_HALF_EVEN))
    out["IS_GENUINELY_FLAT"] = (yq == 0 and nq == 0)
    out["IS_MATCHED_NOT_FLAT"] = (matched > 0 and res_yes == 0 and res_no == 0)
    return out


# --- Section 15. Capital-hour accounting. ----------------------------------

CAPITAL_HOUR_FIELDS = ("CAPITAL_REQUIRED", "EXPECTED_CAPITAL_OCCUPANCY_SECONDS",
                       "EXPECTED_NET_DOLLARS",
                       "EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR")

BOTH_ARE_REPORTED = (
    "EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR ranks a high-turnover book, but it "
    "does NOT replace EXPECTED_NET_DOLLARS. A trade earning one cent in one "
    "second has a spectacular hourly rate and still earns one cent")

RATE_WITHOUT_LEVEL_IS_MISLEADING = (
    "reporting only the rate makes tiny, fast, operationally expensive trades "
    "look like the best in the book")


def capital_hours(expected_net_dollars=None, capital_required=None,
                  expected_occupancy_seconds=None):
    """Both figures, always together. Neither is derived from a guess."""
    net = _d(expected_net_dollars)
    cap = _d(capital_required)
    secs = _d(expected_occupancy_seconds)
    out = {
        "EXPECTED_NET_DOLLARS": str(net) if net is not None else NOT_IDENTIFIED,
        "CAPITAL_REQUIRED": str(cap) if cap is not None else NOT_IDENTIFIED,
        "EXPECTED_CAPITAL_OCCUPANCY_SECONDS": (str(secs) if secs is not None
                                               else NOT_IDENTIFIED),
        "BOTH_ARE_REPORTED": BOTH_ARE_REPORTED,
        "RATE_WITHOUT_LEVEL_IS_MISLEADING": RATE_WITHOUT_LEVEL_IS_MISLEADING,
    }
    if net is None or cap is None or secs is None or cap <= 0 or secs <= 0:
        out["EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR"] = NOT_IDENTIFIED
        out["WHY_NOT"] = ("net dollars, capital and occupancy must all be "
                          "known and positive; a rate built on a guessed "
                          "denominator is not a measurement")
        return out
    hours = secs / Decimal("3600")
    out["EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR"] = str(
        (net / (cap * hours)).quantize(Q, ROUND_HALF_EVEN))
    return out


def describe():
    return {
        "LEG_FIELDS": LEG_FIELDS,
        "DO_NOT_NET_THE_LEGS": DO_NOT_NET_THE_LEGS,
        "CAPITAL_HOUR_FIELDS": CAPITAL_HOUR_FIELDS,
        "BOTH_ARE_REPORTED": BOTH_ARE_REPORTED,
        "RATE_WITHOUT_LEVEL_IS_MISLEADING": RATE_WITHOUT_LEVEL_IS_MISLEADING,
        "NOTHING_IS_PLACED": NOTHING_IS_PLACED,
        "ORDER_PATH_EXISTS": ORDER_PATH_EXISTS,
    }
