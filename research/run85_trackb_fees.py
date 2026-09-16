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
THE SYMMETRY — WHAT IT BUYS, AND WHAT IT DOES NOT
--------------------------------------------------------------------------
RETRACTED: an earlier version of this module claimed that "both legs of a
maker/maker pair earn the same rebate". That is FALSE whenever a != b, and it
invited a 2 * rebate(long) shortcut which is not used anywhere here.

The passive maker/maker construction on a binary book with bestBid b and
bestAsk a is:

    LONG_MAKER_PRICE  = b
    SHORT_MAKER_PRICE = 1 - a          <-- NOT 1 - b
    PAIR_COST         = b + (1 - a) = 1 - spread

The B-1 identity `shortQuote = 1 - bestBid` describes the displayed
opposite-side TAKER quote. It must never be substituted for the passive
short-side maker price, which is 1 - a.

What the symmetry of p*(1-p) genuinely buys is narrower and worth stating
exactly: rebate(a) == rebate(1 - a) identically, so the short leg may be
evaluated at either spelling of its price without changing the number. It does
NOT make the two LEGS equal, because the legs sit at b and 1 - a, which differ
by the spread. Both legs are therefore always computed independently below,
and each is rounded on its own.

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
    """A passive maker/maker pair on one binary book.

        LONG_MAKER_PRICE  = bid
        SHORT_MAKER_PRICE = 1 - ask          (NOT 1 - bid)
        PAIR_COST         = bid + (1 - ask) = 1 - spread

    Under Finding B-1 this is the only construction that can earn the spread;
    crossing both sides costs 1 + spread and loses it.

    The two legs sit at DIFFERENT prices, so their rebates are computed
    independently and each is rounded on its own. No 2 * rebate(long)
    shortcut is used, and the two realized rebates may differ both in exact
    value and, through per-fill cent rounding, in realized dollars.

    Nothing here is profit: it is the budget, before any fill is assumed.
    """
    b, a = D(str(bid)), D(str(ask))
    spread = a - b
    long_price = b
    short_price = D(1) - a
    pair_cost = long_price + short_price
    displayed = spread * D(str(contracts))
    ex_long, rd_long = maker_rebate(contracts, long_price)
    ex_short, rd_short = maker_rebate(contracts, short_price)
    return {
        "contracts": D(str(contracts)),
        "bid": b, "ask": a, "spread": spread,
        "long_maker_price": long_price,
        "short_maker_price": short_price,
        "pair_cost": pair_cost,
        "displayed_spread_capture": displayed,
        "displayed_pair_edge": displayed,
        "long_rebate_exact": ex_long, "long_rebate_rounded": rd_long,
        "short_rebate_exact": ex_short, "short_rebate_rounded": rd_short,
        "legs_earn_equal_rebate": ex_long == ex_short,
        "exact_unrounded_maker_rebate": ex_long + ex_short,
        "actual_rounded_maker_rebate": rd_long + rd_short,
        "total_pre_adverse_selection_budget_exact": displayed + ex_long + ex_short,
        "total_pre_adverse_selection_budget_rounded": displayed + rd_long + rd_short,
        "budget_exact": displayed + ex_long + ex_short,
        "budget_rounded": displayed + rd_long + rd_short,
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
