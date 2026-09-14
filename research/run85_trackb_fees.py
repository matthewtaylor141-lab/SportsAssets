#!/usr/bin/env python3
"""RUN 85 TRACK B — the VERIFIED PMUS fee/rebate rules. Contacts nothing.

Primary source, supplied externally because this environment's organization
egress policy refuses every Polymarket-owned host:

    Polymarket US Documentation — "Fee Schedule"
    https://docs.polymarket.us/fees
    effective exchange-wide 12 AM ET, Wednesday 1 July 2026

    Fee = THETA * C * p * (1 - p)

    THETA_TAKER = +0.06     a charge
    THETA_MAKER = -0.0125   a REBATE, applied at the point of trade

    rounded to the nearest $0.01, banker's rounding (half to even),
    per applicable fill, computed per fill INDEPENDENTLY.
    Cancelled and expired orders incur nothing: fees and rebates occur
    only on execution.

The rules are implemented here rather than quoted, and the module reproduces
every worked example the documentation gives (see the tests). The sealed venue
field `feeCoefficient = 0.06`, uniform across 51,566 market rows, is consistent
with the TAKER coefficient and is NOT the maker coefficient — the earlier
refusal to read maker economics out of that single number was correct.

--------------------------------------------------------------------------
THE SYMMETRY THAT MATTERS
--------------------------------------------------------------------------
p * (1 - p) is symmetric about p = 1/2, so a leg priced p and a leg priced
(1 - p) earn exactly the same rebate. Under Finding B-1 the short leg of a
maker/maker pair IS priced at one minus the long leg, so **both legs of a pair
earn the same rebate to within the spread**. The pair rebate is therefore very
close to twice the single-leg rebate, and no separate short-side fee model is
needed.

--------------------------------------------------------------------------
WHY THE ROUNDING IS NOT A DETAIL
--------------------------------------------------------------------------
The rebate is computed per fill and rounded to a whole cent. At p = 0.50 a
contract earns $0.003125, so a single fill needs about 2 contracts to clear
half a cent and round up at all. At p = 0.01 it earns $0.000124, and a fill
needs about 41 contracts. Below that threshold the fill rebate rounds to ZERO.

Because each fill rounds independently, the SAME volume broken into small
fills can earn materially less — at low prices, nothing at all. Multiplying a
continuous rebate rate by total volume overstates income for small clips, so
this module always reports the exact and the rounded figure side by side.
"""
from __future__ import annotations

from decimal import Decimal as D, ROUND_HALF_EVEN

# Verified 2026-07-01, Polymarket US Fee Schedule.
THETA_TAKER = D("0.06")
THETA_MAKER = D("-0.0125")
CENT = D("0.01")

SOURCE = "https://docs.polymarket.us/fees"
EFFECTIVE_DATE = "2026-07-01"
CURRENCY = "USD"


def bankers_cents(x: D) -> D:
    """Nearest $0.01, half to even — the documented rounding."""
    return D(x).quantize(CENT, rounding=ROUND_HALF_EVEN)


def exact_fee(theta: D, contracts, price) -> D:
    """THETA * C * p * (1-p), unrounded. Negative theta yields a rebate."""
    p = D(str(price))
    return D(str(theta)) * D(str(contracts)) * p * (D(1) - p)


def taker_fee(contracts, price):
    """(exact, rounded) charge in USD, reported positive as a cost."""
    e = exact_fee(THETA_TAKER, contracts, price)
    return e, bankers_cents(e)


def maker_rebate(contracts, price):
    """(exact, rounded) rebate in USD, reported positive as income.

    THETA_MAKER is negative in the venue's sign convention; the magnitude is
    what the maker receives.
    """
    e = -exact_fee(THETA_MAKER, contracts, price)
    return e, bankers_cents(e)


def rebate_per_contract(price) -> D:
    """The continuous per-contract rebate. NOT what a small fill receives."""
    return -exact_fee(THETA_MAKER, 1, price)


def min_contracts_for_a_cent(price) -> int:
    """Smallest single fill whose rebate does not round away to zero.

    Half a cent is the boundary; at exactly half a cent banker's rounding goes
    to the even cent, which is $0.00, so the threshold is strict.
    """
    per = rebate_per_contract(price)
    if per <= 0:
        return 0
    c = 1
    while bankers_cents(per * c) < CENT:
        c += 1
        if c > 10 ** 7:
            return -1
    return c


def maker_pair(contracts, bid, ask):
    """A maker/maker pair on one token: rest a buy at `bid`, a sell at `ask`.

    Under Finding B-1 this is the only construction that can earn the spread;
    crossing both sides costs 1 + spread and loses it.

    Returns the displayed capture, both leg rebates exact and rounded, and the
    total budget available to absorb adverse selection and residual cost.
    Nothing here is profit: it is the budget, before any fill is assumed.
    """
    b, a = D(str(bid)), D(str(ask))
    spread = a - b
    displayed = spread * D(str(contracts))
    ex_a, rd_a = maker_rebate(contracts, b)        # the resting buy
    ex_b, rd_b = maker_rebate(contracts, a)        # the resting sell
    return {
        "contracts": D(str(contracts)),
        "bid": b, "ask": a, "spread": spread,
        "displayed_pair_edge": displayed,
        "exact_unrounded_maker_rebate": ex_a + ex_b,
        "actual_rounded_maker_rebate": rd_a + rd_b,
        "rebate_leg_a_exact": ex_a, "rebate_leg_a_rounded": rd_a,
        "rebate_leg_b_exact": ex_b, "rebate_leg_b_rounded": rd_b,
        "budget_exact": displayed + ex_a + ex_b,
        "budget_rounded": displayed + rd_a + rd_b,
        "adverse_selection": "NOT_IDENTIFIED",
        "incomplete_pair_cost": "NOT_IDENTIFIED",
        "maker_fill_probability": "NOT_IDENTIFIED",
        "maker_pair_completion_probability": "NOT_IDENTIFIED",
        "expected_economic_edge": "NOT_IDENTIFIED",
    }


def fragmentation(contracts, price, fills):
    """What the same volume earns when broken into `fills` equal pieces.

    Each fill rounds independently, so this can be strictly less than the
    single-fill rebate — at low prices, zero.
    """
    per = D(str(contracts)) / D(str(fills))
    ex_one, rd_one = maker_rebate(contracts, price)
    ex_each, rd_each = maker_rebate(per, price)
    return {
        "contracts": D(str(contracts)), "fills": fills, "price": D(str(price)),
        "exact_unrounded_maker_rebate": ex_one,
        "rounded_if_one_fill": rd_one,
        "rounded_if_fragmented": rd_each * fills,
        "lost_to_rounding": rd_one - rd_each * fills,
    }
