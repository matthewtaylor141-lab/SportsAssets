#!/usr/bin/env python3
"""EV_MAKER vs EV_TAKER vs EV_NO_TRADE across both fee regimes. Contacts nothing.

WHAT FAIR VALUE IS TAKEN TO BE, stated before any number.

Every figure below sets FAIR_VALUE = MID. That is not a claim that the mid is
correct; it is the deliberate ZERO-INFORMATIONAL-EDGE baseline, and it is the
only honest baseline available because BETTOR has no validated directional
signal -- ENGINE_B_GATE = FAIL is on the record. So the table answers exactly
one question:

    "With NO edge, which channel is least bad, and how much must a signal be
     worth before trading beats not trading?"

Under that baseline the earlier measured identity does the work:

    taker buying at the ask:   gross_edge = mid - ask = -spread/2
    maker resting at the bid:  gross_edge = mid - bid = +spread/2

A full spread separates the two channels before a single fee is applied. The
fee then widens the gap, and the rebate widens it again. Everything the maker
gains here is CONDITIONAL ON A FILL, and a fill is precisely what an unfilled
resting order does not get -- no fill probability is applied anywhere in this
file, because none has been measured for BETTOR. BLOCK_4 stands: a 22,297-share
displayed queue saw 180 shares trade in 16 minutes with ZERO touches.

The maker column is therefore an UPPER BOUND on the maker channel, and the
break-even adverse selection is the control variable that says how much of that
bound survives contact with whoever trades against us.

INCENTIVES ARE REPORTED APART. The reward column is zero throughout: no
liquidity reward has been observed for any BETTOR quote. TRADING_PNL_EX_
INCENTIVES is the column that decides, per the architecture rule that an
incentive may never be used to rationalize a negative trade.
"""
from __future__ import annotations

import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fees_v2 as F

# A declared GRID, not an observation. Prices span the cheap tail through the
# even-money centre; spreads span one cent through a wide book. When the
# forward capture has a measured spread distribution these are replaced by it,
# and the replacement is labelled as measured.
PRICES = [D("0.05"), D("0.10"), D("0.25"), D("0.50"), D("0.75"), D("0.90")]
SPREADS = [D("0.01"), D("0.02"), D("0.05"), D("0.10")]
CLIP = 1000          # contracts per fill; the rounding is per fill

GRID_PROVENANCE = "DECLARED_GRID_NOT_MEASURED"


def row(mid: D, spread: D, regime: str, contracts: int = CLIP) -> dict:
    """One (price, spread, regime) cell, every channel, per contract."""
    half = spread / D(2)
    ask = mid + half
    bid = mid - half

    out = {
        "mid": mid, "spread": spread, "regime": regime,
        "bid": bid, "ask": ask, "contracts": contracts,
        "EV_NO_TRADE": F.ev_no_trade()["net"],
    }

    for label, tier in (("BASE", "BASE"), ("10PCT", "T10_250k_1M"),
                        ("25PCT", "T25_1M_10M"), ("50PCT", "T50_10M_PLUS")):
        ev = F.ev_taker(mid, ask, regime, tier=tier, contracts=contracts)
        out["EV_TAKER_" + label] = ev["net"]
        out["FEE_" + label] = -ev["fee"]

    mk = F.ev_maker(mid, bid, adverse_selection_per_contract=D("0"),
                    expected_reward_per_contract=D("0"), contracts=contracts)
    out["EV_MAKER"] = mk["trading_net_ex_incentives"]
    out["MAKER_REBATE"] = mk["rebate"]
    out["MAKER_CAPTURED_SPREAD"] = mk["gross_edge"]
    out["ESTIMATED_REWARD"] = mk["estimated_reward"]          # zero: unmeasured
    out["ACTUAL_REWARD"] = "NOT_IDENTIFIED"

    be = F.breakeven_adverse_selection(mid, bid, contracts=contracts)
    out["BREAKEVEN_ADVERSE_SELECTION_TOTAL"] = be
    out["BREAKEVEN_ADVERSE_SELECTION_PER_CONTRACT"] = be / D(contracts)
    # As a share of the price paid -- the markout the shadow engine must beat.
    out["BREAKEVEN_AS_PCT_OF_NOTIONAL"] = (
        (be / D(contracts) / bid * D(100)) if bid > 0 else D("0"))

    # What a taker's directional signal must be worth, per contract, merely to
    # reach zero. Not what makes it a good trade -- what stops it losing.
    out["TAKER_EDGE_NEEDED_BASE"] = (
        -out["EV_TAKER_BASE"] / D(contracts)) if contracts else D("0")
    return out


def table(regime: str) -> list:
    return [row(p, s, regime) for p in PRICES for s in SPREADS
            if p - s / D(2) > 0 and p + s / D(2) < 1]


def _fmt(x) -> str:
    return x if isinstance(x, str) else ("%.4f" % float(x))


def render(regime: str) -> str:
    rows = table(regime)
    theta = F.theta_taker(regime)
    verified = (F.THETA_TAKER_SEP2026_VERIFIED if regime == "SEP2026"
                else True)
    head = ("\n=== REGIME %s   THETA_TAKER=%s  THETA_MAKER=%s  "
            "VERIFIED=%s  clip=%d ===\n"
            % (regime, theta, F.THETA_MAKER_ALL,
               "YES" if verified else "NO -- RELAYED, NOT CAPTURED", CLIP))
    cols = ["mid", "spread", "EV_MAKER", "EV_TAKER_BASE", "EV_TAKER_10PCT",
            "EV_TAKER_25PCT", "EV_TAKER_50PCT", "EV_NO_TRADE",
            "BREAKEVEN_ADVERSE_SELECTION_PER_CONTRACT",
            "BREAKEVEN_AS_PCT_OF_NOTIONAL"]
    short = {"BREAKEVEN_ADVERSE_SELECTION_PER_CONTRACT": "BE_ADV_$/CT",
             "BREAKEVEN_AS_PCT_OF_NOTIONAL": "BE_ADV_%NOT"}
    lines = [head, "  ".join("%12s" % short.get(c, c) for c in cols)]
    for r in rows:
        lines.append("  ".join("%12s" % _fmt(r[c]) for c in cols))
    return "\n".join(lines)


def headline(regime: str) -> dict:
    """The two facts the architecture actually needs out of this table."""
    rows = table(regime)
    maker_wins = sum(1 for r in rows if r["EV_MAKER"] > r["EV_TAKER_BASE"])
    maker_beats_flat = sum(1 for r in rows if r["EV_MAKER"] > r["EV_NO_TRADE"])
    taker_beats_flat = sum(1 for r in rows
                           if r["EV_TAKER_BASE"] > r["EV_NO_TRADE"])
    taker_beats_flat_top = sum(1 for r in rows
                               if r["EV_TAKER_50PCT"] > r["EV_NO_TRADE"])
    return {
        "regime": regime,
        "cells": len(rows),
        "MAKER_BEATS_TAKER_CELLS": maker_wins,
        "MAKER_BEATS_NO_TRADE_CELLS": maker_beats_flat,
        "TAKER_BEATS_NO_TRADE_CELLS_BASE": taker_beats_flat,
        "TAKER_BEATS_NO_TRADE_CELLS_TOP_TIER": taker_beats_flat_top,
        "GRID_PROVENANCE": GRID_PROVENANCE,
        "FAIR_VALUE_ASSUMPTION": "MID (zero informational edge)",
        "FILL_PROBABILITY_APPLIED": "NONE -- conditional on a fill",
    }


def main() -> int:
    print(__doc__)
    for regime in ("JUL2026", "SEP2026"):
        print(render(regime))
    print("\n=== HEADLINE ===")
    for regime in ("JUL2026", "SEP2026"):
        h = headline(regime)
        for k, v in h.items():
            print("%-36s %s" % (k, v))
        print()

    print("=== COST OF THE CUTOVER, per 1000-contract take at the ask ===")
    for p in PRICES:
        for s in (D("0.02"),):
            a = p + s / D(2)
            old = F.taker_fee_at(CLIP, a, "JUL2026")
            new = F.taker_fee_at(CLIP, a, "SEP2026")
            print("  mid=%s ask=%s  fee %s -> %s   delta %s"
                  % (p, a, old, new, new - old))

    print("\n=== LABELS ===")
    print("TAKER_BASE_THETA_JUL2026        %s" % F.THETA_TAKER_JUL2026)
    print("TAKER_BASE_THETA_SEP2026        %s" % F.THETA_TAKER_SEP2026)
    print("TAKER_REBATE_TIER               BASE (assumed)")
    print("EFFECTIVE_TAKER_THETA_SEP2026   %s"
          % F.effective_theta_taker("SEP2026", "BASE"))
    print("TIER_VERIFIED                   %s"
          % ("YES" if F.TIER_VERIFIED else "NO"))
    print("THETA_TAKER_SEP2026_VERIFIED    %s"
          % ("YES" if F.THETA_TAKER_SEP2026_VERIFIED else "NO"))
    print("MAKER_THETA                     %s (unchanged across the cutover)"
          % F.THETA_MAKER_ALL)
    print("NEGOTIATED_MARKET_MAKER_ECONOMICS %s"
          % F.NEGOTIATED_MARKET_MAKER_ECONOMICS)
    print("ESTIMATED_REWARD                0 (none observed)")
    print("ACTUAL_REWARD                   NOT_IDENTIFIED")
    print("MAKER_FILL_PROBABILITY          NOT_IDENTIFIED")
    print("MAKER_ADVERSE_SELECTION         NOT_IDENTIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
