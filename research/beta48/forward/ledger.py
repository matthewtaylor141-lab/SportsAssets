#!/usr/bin/env python3
"""The BETTOR forward economic ledger. Thirteen terms, none of them blended.

WHY THIS FILE EXISTS RATHER THAN A SUM.

A single "P&L" number cannot answer the question the programme has to answer,
which is not "did it make money" but "WHERE did the money come from":

    A. actual market-making edge,
    B. incentives,
    C. both.

Those have completely different futures. Edge survives a programme ending;
incentives do not, and an incentive schedule is the venue's to change at any
time. So the terms are carried separately all the way to the end, and the two
totals are ALWAYS reported together:

    TRADING_NET_EX_INCENTIVES   the one that decides
    INCENTIVE_CONTRIBUTION      what the programmes added
    TOTAL_NET                   the sum, which is never reported alone

THE FOUR INCENTIVE CHANNELS ARE NOT ONE CHANNEL. Liquidity pays for resting,
fill pays for passive execution, volume pays taker-side notional, and the
Market Maker Program is bilateral and unidentified. Adding them up before each
is separately verified would credit a resting quote with a taker programme's
money.

NOTHING IS COUNTED BECAUSE A PROGRAMME EXISTS. Every term defaults to
NOT_IDENTIFIED, and a term becomes a number only when something was actually
observed. A zero and a NOT_IDENTIFIED are different claims: zero says "we
measured none", NOT_IDENTIFIED says "we cannot see it". This file never
converts the second into the first.
"""
from __future__ import annotations

import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fees_v2 import (  # noqa: E402
    INCENTIVE_CHANNEL_BASIS, INCENTIVE_CHANNELS,
)

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# The ledger's terms, in the order they are reported. Every one of them is a
# separate fact about the same position.
TRADING_TERMS = (
    "TRADING_EDGE",          # fair value minus our price, our own estimate
    "SPREAD_CAPTURE",        # what resting away from mid earned, mechanically
    "MAKER_REBATE",          # received, only on an actual fill
    "TAKER_FEE",             # paid, only on an actual fill (negative)
    "TAKER_FEE_REBATE",      # tier reduction of a fee actually paid
    "ADVERSE_SELECTION",     # post-fill markout against us (negative)
    "INVENTORY_PNL",         # what the held position did
    "EXIT_HEDGE_COST",       # what leaving cost (negative)
)

INCENTIVE_TERMS = INCENTIVE_CHANNELS   # the four, kept apart

ALL_TERMS = TRADING_TERMS + INCENTIVE_TERMS + ("TOTAL_NET_PNL",)


def new_ledger() -> dict:
    """Every term NOT_IDENTIFIED until something is actually observed."""
    return {t: NOT_IDENTIFIED for t in ALL_TERMS}


def _num(v):
    """A term's numeric value, or None if it is not a number."""
    if v is None or isinstance(v, str):
        return None
    return D(str(v))


def credit(led: dict, term: str, amount, verified: bool):
    """Record an amount against ONE term.

    `verified` is not decoration. An incentive that is merely scheduled is not
    income: it is refused here rather than accepted and caveated later, because
    a caveat does not survive being summed.
    """
    if term not in ALL_TERMS:
        raise ValueError("unknown ledger term: %r" % (term,))
    if term == "TOTAL_NET_PNL":
        raise ValueError("TOTAL_NET_PNL is derived, never credited directly")
    if term in INCENTIVE_TERMS and not verified:
        led[term] = NOT_IDENTIFIED
        return led
    if not verified:
        led[term] = NOT_IDENTIFIED
        return led
    prev = _num(led.get(term))
    led[term] = (prev or D("0")) + D(str(amount))
    return led


def credit_incentive(led: dict, channel: str, amount, verified: bool,
                     basis: str):
    """An incentive credit that must match the channel's ECONOMIC BASIS.

    A volume programme pays taker-side notional. Crediting it to a resting
    quote would pay a maker out of a taker programme, which is how a blended
    incentive number stops meaning anything. The basis is therefore checked,
    not trusted.
    """
    if channel not in INCENTIVE_TERMS:
        raise ValueError("unknown incentive channel: %r" % (channel,))
    want = INCENTIVE_CHANNEL_BASIS[channel]
    if want == NOT_IDENTIFIED or basis != want:
        led[channel] = NOT_IDENTIFIED
        return led
    return credit(led, channel, amount, verified)


def totals(led: dict) -> dict:
    """The three figures, always returned together.

    A term that is NOT_IDENTIFIED does not contribute and is NAMED, so a total
    can never quietly rest on a missing term treated as zero.
    """
    missing_trading = [t for t in TRADING_TERMS if _num(led.get(t)) is None]
    missing_inc = [t for t in INCENTIVE_TERMS if _num(led.get(t)) is None]

    trading = sum((_num(led[t]) for t in TRADING_TERMS
                   if _num(led.get(t)) is not None), D("0"))
    incentive = sum((_num(led[t]) for t in INCENTIVE_TERMS
                     if _num(led.get(t)) is not None), D("0"))

    out = {
        "TRADING_NET_EX_INCENTIVES": trading,
        "INCENTIVE_CONTRIBUTION": incentive,
        "TOTAL_NET": trading + incentive,
        "TRADING_TERMS_NOT_IDENTIFIED": missing_trading,
        "INCENTIVE_TERMS_NOT_IDENTIFIED": missing_inc,
        "TRADING_NET_IS_COMPLETE": not missing_trading,
    }
    out["EDGE_SOURCE"] = edge_source(out)
    return out


def edge_source(t: dict) -> str:
    """A / B / C -- where the money came from.

    Refuses to answer while the trading side is incomplete, because "it came
    from edge" cannot be claimed from a partial edge measurement.
    """
    if not t["TRADING_NET_IS_COMPLETE"]:
        return NOT_IDENTIFIED
    trading, inc = t["TRADING_NET_EX_INCENTIVES"], t["INCENTIVE_CONTRIBUTION"]
    if t["TOTAL_NET"] <= 0:
        return "NEITHER_TOTAL_NOT_POSITIVE"
    if trading > 0 and inc > 0:
        return "C_BOTH"
    if trading > 0:
        return "A_MARKET_MAKING_EDGE"
    return "B_INCENTIVES_ONLY"


def render(led: dict) -> str:
    t = totals(led)
    lines = ["FORWARD ECONOMIC LEDGER"]
    for term in TRADING_TERMS:
        lines.append("  %-26s %s" % (term, led[term]))
    lines.append("  %-26s %s" % ("TRADING_NET_EX_INCENTIVES",
                                 t["TRADING_NET_EX_INCENTIVES"]))
    lines.append("  -- incentive channels, never blended --")
    for term in INCENTIVE_TERMS:
        lines.append("  %-26s %-14s basis=%s"
                     % (term, led[term], INCENTIVE_CHANNEL_BASIS[term]))
    lines.append("  %-26s %s" % ("INCENTIVE_CONTRIBUTION",
                                 t["INCENTIVE_CONTRIBUTION"]))
    lines.append("  %-26s %s" % ("TOTAL_NET", t["TOTAL_NET"]))
    lines.append("  %-26s %s" % ("EDGE_SOURCE", t["EDGE_SOURCE"]))
    if t["TRADING_TERMS_NOT_IDENTIFIED"]:
        lines.append("  UNMEASURED TRADING TERMS: %s"
                     % ", ".join(t["TRADING_TERMS_NOT_IDENTIFIED"]))
    return "\n".join(lines)
