#!/usr/bin/env python3
"""THE PRE-ORDER EV RECEIPT. One machine-readable record per proposed order.

WHAT THE RECEIPT IS FOR. When the first live order eventually fills, the only
question worth asking is whether what we predicted matched what happened. That
comparison is impossible unless the prediction was written down, in full,
before the order existed. This is that record.

THE RULE THAT GOVERNS EVERY FIELD. A term that is NOT_IDENTIFIED stays
NOT_IDENTIFIED. It is never replaced by zero, and a receipt carrying an
unidentified EV-critical term cannot produce a TRADE decision -- the comparison
it would need has not been made. Zero is a measurement. Absence is not.

CONDITIONING IS CARRIED, NOT ASSUMED. The Day-1 layer's distinction is imported
rather than re-implemented: terms are CONDITIONAL_ON_FILL or UNCONDITIONAL,
the no-fill branch must be declared explicitly, and P_FILL may only come from
BETTOR's own admitted fills -- never from a whale's completion rate, which
measures whether somebody else's counterparty turned up on a different venue.

TRADING EV AND INCENTIVE EV ARE NEVER SUMMED INTO ONE HEADLINE. Ferrari's
lesson: one number hid a working mechanism and a leaking inventory book. They
are reported separately and added only into a clearly labelled total.

This module contacts nothing and can place no order.
"""
import json
from decimal import Decimal as D

import ev_semantics as EV

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_ESTABLISHED = "NOT_ESTABLISHED"

# EV-CRITICAL: if any of these is NOT_IDENTIFIED there is no valid comparison,
# and the decision is NO_TRADE. They are the terms whose absence changes the
# sign of the answer rather than its precision.
EV_CRITICAL_TERMS = (
    "FAIR_VALUE",
    "LIMIT_PRICE",
    "EXPECTED_FEES",
    "P_FILL_VALUE",
    "EV_IF_FILL",
    "EV_IF_NO_FILL",
)

# Reported, and their absence widens the uncertainty rather than voiding the
# comparison -- but each one absent is named in UNCERTAINTY_TERMS.
EV_SOFT_TERMS = (
    "EXPECTED_REBATES",
    "EXPECTED_ADVERSE_SELECTION",
    "EXPECTED_INVENTORY_COST",
    "EXPECTED_CAPITAL_OCCUPANCY_COST",
)

NO_TRADE = "NO_TRADE"
TRADE = "TRADE"
ACTIONS = ("MAKER_QUOTE", "TAKER_CROSS", "NO_TRADE")

UNKNOWNS_ARE_NOT_ZERO = (
    "an unpriced term is not a free term; replacing it with zero prices the "
    "option we could not price at exactly the value that makes it look best")
TRADING_AND_INCENTIVE_NEVER_ONE_NUMBER = (
    "a mechanism that works and an inventory book that leaks sum to a single "
    "figure that hides both")


def _d(x):
    if x in (None, NOT_IDENTIFIED, NOT_ESTABLISHED, ""):
        return None
    try:
        return D(str(x))
    except Exception:                                         # noqa: BLE001
        return None


def receipt(*, event_id, market_id, market_slug, side, order_type,
            limit_price, quantity, book_timestamp, decision_timestamp,
            fair_value=NOT_IDENTIFIED, fair_value_status=NOT_IDENTIFIED,
            p_fill_value=NOT_IDENTIFIED, p_fill_source=NOT_IDENTIFIED,
            p_fill_horizon=NOT_IDENTIFIED, p_fill_status=NOT_IDENTIFIED,
            expected_fees=NOT_IDENTIFIED, expected_rebates=NOT_IDENTIFIED,
            expected_adverse_selection=NOT_IDENTIFIED,
            expected_inventory_cost=NOT_IDENTIFIED,
            expected_capital_occupancy_cost=NOT_IDENTIFIED,
            no_fill_state=None, no_fill_horizon=None,
            ev_if_no_fill=NOT_IDENTIFIED, ev_taker=NOT_IDENTIFIED):
    """Build the receipt and decide. The decision follows from the fields.

    EV_IF_FILL is built from the terms that are CONDITIONAL_ON_FILL: the price
    edge against fair value, less fees, less expected adverse selection, less
    the inventory and capital costs of holding what the fill leaves us with.
    EV_IF_NO_FILL is whatever we are left holding if the quote never trades,
    and it is NOT assumed to be zero -- if we were trying to exit, we still own
    the position afterwards.
    """
    fv = _d(fair_value)
    px = _d(limit_price)
    qty = _d(quantity)
    fees = _d(expected_fees)
    rebate = _d(expected_rebates)
    adverse = _d(expected_adverse_selection)
    inv = _d(expected_inventory_cost)
    cap = _d(expected_capital_occupancy_cost)
    pfill = _d(p_fill_value)

    notional = (px * qty) if (px is not None and qty is not None) else None
    edge = None
    if fv is not None and px is not None:
        # BUY side: value is fair value above the price we pay.
        edge = (fv - px) if str(side).upper().startswith("BUY") else (px - fv)

    # EV_IF_FILL, CONDITIONAL_ON_FILL throughout. A missing hard term makes the
    # whole branch unidentified -- not a smaller number.
    ev_if_fill = NOT_IDENTIFIED
    missing_for_fill = [n for n, v in (("PRICE_EDGE", edge),
                                       ("QUANTITY", qty),
                                       ("EXPECTED_FEES", fees)) if v is None]
    soft_missing = [n for n, v in (("EXPECTED_REBATES", rebate),
                                   ("EXPECTED_ADVERSE_SELECTION", adverse),
                                   ("EXPECTED_INVENTORY_COST", inv),
                                   ("EXPECTED_CAPITAL_OCCUPANCY_COST", cap))
                    if v is None]
    trading_ev = NOT_IDENTIFIED
    incentive_ev = NOT_IDENTIFIED
    if not missing_for_fill and not soft_missing:
        trading_ev = (edge * qty) - fees - adverse - inv - cap
        incentive_ev = rebate
        ev_if_fill = trading_ev + incentive_ev

    nofill = _d(ev_if_no_fill)
    branch = None
    if no_fill_state is not None:
        try:
            branch = EV.no_fill_branch(no_fill_state, no_fill_horizon,
                                       continuation_value=(
                                           nofill if nofill is not None
                                           else NOT_IDENTIFIED))
        except Exception as exc:                              # noqa: BLE001
            branch = {"NO_FILL_BRANCH_ERROR": str(exc)}

    ev_maker = NOT_IDENTIFIED
    if (ev_if_fill != NOT_IDENTIFIED and pfill is not None
            and nofill is not None):
        ev_maker = pfill * ev_if_fill + (D("1") - pfill) * nofill

    fields = {
        "EVENT_ID": event_id, "MARKET_ID": market_id,
        "MARKET_SLUG": market_slug, "SIDE": side, "ORDER_TYPE": order_type,
        "LIMIT_PRICE": limit_price, "QUANTITY": quantity,
        "NOTIONAL": notional if notional is not None else NOT_IDENTIFIED,
        "BOOK_TIMESTAMP": book_timestamp,
        "DECISION_TIMESTAMP": decision_timestamp,

        "FAIR_VALUE": fair_value, "FAIR_VALUE_STATUS": fair_value_status,
        "PRICE_EDGE": edge if edge is not None else NOT_IDENTIFIED,

        "EV_MAKER": ev_maker, "EV_TAKER": ev_taker,
        "EV_NO_TRADE": D("0"),

        "P_FILL_SOURCE": p_fill_source, "P_FILL_VALUE": p_fill_value,
        "P_FILL_HORIZON": p_fill_horizon, "P_FILL_STATUS": p_fill_status,

        "EV_IF_FILL": ev_if_fill, "EV_IF_NO_FILL": ev_if_no_fill,
        "NO_FILL_BRANCH": branch or NOT_IDENTIFIED,

        "EXPECTED_FEES": expected_fees,
        "EXPECTED_REBATES": expected_rebates,
        "EXPECTED_ADVERSE_SELECTION": expected_adverse_selection,
        "EXPECTED_INVENTORY_COST": expected_inventory_cost,
        "EXPECTED_CAPITAL_OCCUPANCY_COST": expected_capital_occupancy_cost,

        "TRADING_EV_EX_INCENTIVES": trading_ev,
        "INCENTIVE_EV": incentive_ev,
        "TOTAL_EXPECTED_EV": ev_maker,
        "TRADING_AND_INCENTIVE_NEVER_ONE_NUMBER":
            TRADING_AND_INCENTIVE_NEVER_ONE_NUMBER,
    }

    unknown = [k for k in EV_CRITICAL_TERMS
               if fields.get(k, NOT_IDENTIFIED) in (NOT_IDENTIFIED,
                                                    NOT_ESTABLISHED, None)]
    soft_unknown = [k for k in EV_SOFT_TERMS
                    if fields.get(k, NOT_IDENTIFIED) in (NOT_IDENTIFIED,
                                                         NOT_ESTABLISHED, None)]
    # P_FILL's source is checked by the Day-1 guard, not by a string compare.
    p_fill_ok, p_fill_why = True, None
    try:
        EV.ev_maker_quote  # noqa: B018  (presence, not a call)
        import whale_bridge as WB
        WB.assert_p_fill_source(p_fill_source)
    except Exception as exc:                                  # noqa: BLE001
        p_fill_ok, p_fill_why = False, str(exc)

    blockers = list(unknown)
    if not p_fill_ok:
        blockers.append("P_FILL_SOURCE_REFUSED")
    decision = NO_TRADE if blockers else TRADE

    fields.update({
        "UNCERTAINTY_TERMS": soft_unknown,
        "NOT_IDENTIFIED_TERMS": unknown,
        "EV_CRITICAL_TERMS": list(EV_CRITICAL_TERMS),
        "UNKNOWNS_ARE_NOT_ZERO": UNKNOWNS_ARE_NOT_ZERO,
        "P_FILL_SOURCE_ACCEPTED": p_fill_ok,
        "P_FILL_SOURCE_REFUSAL": p_fill_why or NOT_IDENTIFIED,
        "DECISION": decision,
        "SELECTED_ACTION": (order_type if decision == TRADE else NO_TRADE),
        "REJECTED_ACTIONS": [a for a in ACTIONS
                             if a != (order_type if decision == TRADE
                                      else NO_TRADE)],
        "WHY_SELECTED": (
            "every EV-critical term is identified and the comparison is valid"
            if decision == TRADE else
            "an EV-critical term is NOT_IDENTIFIED, so no valid comparison "
            "exists; the unknown is not replaced by zero"),
        "WHY_REJECTED": {
            "NO_TRADE_BECAUSE": blockers or None,
            "TAKER_CROSS": ("not evaluated in this build: the first live test "
                            "is passive-maker only"),
        },
        "CONDITIONING": {
            "EV_IF_FILL": EV.CONDITIONAL_ON_FILL,
            "EV_IF_NO_FILL": EV.UNCONDITIONAL,
            "EV_MAKER": EV.UNCONDITIONAL,
        },
        "SNAPSHOT_EXECUTABILITY_IS_NOT_A_LIVE_FILL": True,
        "ACTUAL_BETTOR_FILL": NOT_IDENTIFIED,
        "REALIZED_ANYTHING": NOT_ESTABLISHED,
    })
    return fields


def render(r):
    keys = ("MARKET_SLUG", "SIDE", "ORDER_TYPE", "LIMIT_PRICE", "QUANTITY",
            "NOTIONAL", "FAIR_VALUE", "PRICE_EDGE", "P_FILL_SOURCE",
            "P_FILL_VALUE", "EV_IF_FILL", "EV_IF_NO_FILL",
            "TRADING_EV_EX_INCENTIVES", "INCENTIVE_EV", "TOTAL_EXPECTED_EV",
            "DECISION", "SELECTED_ACTION")
    return "\n".join("%-32s = %s" % (k, r.get(k, NOT_IDENTIFIED))
                     for k in keys)


def to_json(r):
    return json.dumps(r, indent=1, sort_keys=True, default=str)
