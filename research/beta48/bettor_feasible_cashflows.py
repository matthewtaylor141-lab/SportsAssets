"""FEASIBLE ENTRY AND EXIT PRICES, THROUGH THE ENGINE.

THREE CORRECTIONS THIS FILE EXISTS TO MAKE. All three were errors in
`bettor_maker_breakeven.py`'s interpretation, not in the engine.

1. THE p_fill DECOMPOSITION WAS OVER-GENERALISED.

   I wrote that `p_fill` multiplies `value_if_filled` and therefore
   "cannot change the sign", and then treated that as a general fact
   about maker policies. It is not. It holds only inside the engine's
   SIMPLIFIED model, where `as_fill` is a fixed conditional value and
   there are no costs outside fills. In reality quote price, size,
   waiting time and the policy itself move `p_fill` and the conditional
   return TOGETHER: quoting deeper raises the conditional return and
   lowers the fill rate; resting longer raises the fill rate and
   selects worse counterparties. And there ARE costs outside fills --
   collateral on the resting order, and the option value given up by
   cancelling.

   So the decomposition is a property of one model, useful for seeing
   where the sign comes from, and NOT a complete maker-policy
   evaluation. It is not repeated as one here.

2. 33.9% WAS A SCENARIO, NOT A THRESHOLD.

   A two-state mixture of "uninformed keeps the half-spread" and
   "toxic earns the RN1 stress case" is a calculation, not an
   empirical acceptance criterion: neither component value is measured
   on PMUS and no classification method exists that would sort a real
   fill into one bucket or the other.

   Worse, I proposed measuring settlement returns "with an interval
   excluding 33.9%". That conflates two different quantities -- a cash
   return per share and a mixture proportion. What a pilot measures is
   NET CASH per contract, including costs and unresolved inventory.
   That is what this file computes.

3. THE TICK DOES NOT IMPOSE A HAIRCUT -- AND THE REAL EFFECT IS THE
   OPPOSITE OF WHAT I CLAIMED.

   I applied a "half the half-spread" penalty because a half-cent grid
   leaves no room to quote inside. Run through the engine, that is
   backwards:

     book            tick    engine quote   mid      mid - quote
     1 tick (0.5c)   0.005   0.6000         0.6025   +0.00250   <- FULL
     2 ticks (0.5c)  0.005   0.6050         0.6050    0.00000   <- ZERO
     1 tick (1c)     0.010   0.6000         0.6050   +0.00500   <- FULL
     2 ticks (1c)    0.010   0.6100         0.6100    0.00000   <- ZERO
     4 ticks (1c)    0.010   0.6100         0.6200   +0.01000

   A one-tick-wide book captures the FULL half-spread, because the
   engine quotes AT THE TOUCH and buying at the bid is a half-spread
   below the mid. No haircut exists.

   The case that destroys the edge is a TWO-TICK book, where the
   engine's own "improve by one tick where there is room" rule lands
   the quote EXACTLY ON THE MID and captures nothing. That is a
   property of OUR QUOTING RULE, not of the venue's tick, and it is
   the finding the invented haircut was hiding.

WHAT THIS FILE DOES. For every observed book it asks the engine for
the feasible entry price, then prices every feasible exit as CASH:
crossing out, completing the pair, and holding to settlement. No
spread is deducted twice; every number is a price the venue would
accept or a fee the engine computes.

Run:  python research/beta48/bettor_feasible_cashflows.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bettor_policy_ev as ev                                  # noqa: E402
from bettor_economic_test import load_holdout                  # noqa: E402

CONTRACTS = 100.0


def infer_tick(bid: float, ask: float) -> float:
    """The venue's tick for this market, INFERRED from the price grid.

    `orderPriceMinTickSize` is NOT in the captured BBO bodies -- their
    keys are askDepth, bestAsk, bestBid, bidDepth, currentPx,
    lastPriceSample, lastTradePx, longQuote, marketSlug, openInterest,
    settlementPx, sharesTraded, shortQuote, state. So the tick is
    inferred here and LABELLED as inferred.

    A price that is not a whole number of cents can only sit on a
    finer grid. Half a cent is the finest grid observed in this
    corpus.
    """
    for p in (bid, ask):
        c = round(p * 100.0, 6)
        if abs(c - round(c)) > 1e-6:
            return 0.005
    return 0.01


def row_cashflows(bid: float, ask: float, tick: float) -> dict:
    """Feasible entry and every feasible exit, as CASH, via the engine."""
    book = ev.Book(slug="x", bid=bid, ask=ask, tick=tick)
    mid = 0.5 * (bid + ask)

    q = ev.incremental_ev("QUOTE_BID", book, ev.Inventory(),
                          contracts=CONTRACTS, as_fill=0.0, p_fill=1.0,
                          rebate_eligible=True)
    px = q.terms["quote_price"]
    rebate = q.terms.get("rebate_if_filled", 0.0)

    # Entry cash: we PAY px per contract, and receive the maker rebate.
    entry_cash = -px * CONTRACTS + rebate

    # Now hold that inventory and price every feasible exit.
    inv = ev.Inventory(contracts=CONTRACTS, avg_cost=px)

    # 1. CROSS OUT -- sell into the bid, taker fee.
    close = ev.incremental_ev("CLOSE", book, inv, contracts=CONTRACTS)
    # 2. COMPLETE THE PAIR -- buy the complement, pair settles at 1.
    comp = ev.incremental_ev("COMPLETE_PAIR", book, inv, contracts=CONTRACTS)
    # 3. HOLD TO SETTLEMENT -- needs a fair value; the engine refuses
    #    without one, and that refusal is the honest answer.
    hold = ev.incremental_ev("HOLD", book, inv, contracts=CONTRACTS)

    return {
        "bid": bid, "ask": ask, "mid": mid, "tick": tick,
        "spread_ticks": round((ask - bid) / tick, 2),
        "quote_px": px,
        "at_touch": bool(q.terms.get("at_touch")),
        # THE GROSS EDGE THE ENTRY PRICE ITSELF BUYS, if the mid is
        # fair. This is a PRICE FACT, not an assumption about flow.
        "capture_vs_mid_per_contract": round(mid - px, 6),
        "rebate_total": round(rebate, 6),
        "entry_cash": round(entry_cash, 6),
        "close_cash": None if close.ev is None else round(close.ev, 6),
        "close_net": (None if close.ev is None
                      else round(entry_cash + close.ev, 6)),
        "complete_pair_ev": None if comp.ev is None else round(comp.ev, 6),
        "hold_identified": hold.identified,
        "hold_missing": list(hold.missing),
    }


def main() -> int:
    print("=" * 74)
    print("FEASIBLE CASH FLOWS -- entry and exit prices from the engine")
    print("=" * 74)

    rows, files = load_holdout()
    obs = []
    for r in rows:
        bid, ask = r["bid"], r["ask"]
        if not (0 < bid < ask < 1):
            continue
        obs.append((r["slug"], bid, ask))
    if not obs:
        print("NO OBSERVATIONS")
        return 2

    out = []
    for slug, bid, ask in obs:
        t = infer_tick(bid, ask)
        d = row_cashflows(bid, ask, t)
        d["slug"] = slug
        out.append(d)

    markets = sorted({d["slug"] for d in out})
    print("\nDATASET  %d observations, %d DISTINCT MARKETS, %d segments"
          % (len(out), len(markets), len(files)))
    print("  DEVELOPMENT data. Tick is INFERRED from the price grid --")
    print("  orderPriceMinTickSize is not in the captured bodies.")

    # ── where the entry price actually lands ──────────────────────────
    print("\n" + "-" * 74)
    print("1. WHERE THE ENGINE'S QUOTE ACTUALLY LANDS")
    print("-" * 74)
    by_w: dict = {}
    for d in out:
        w = int(round(d["spread_ticks"]))
        b = by_w.setdefault(w, {"n": 0, "cap": [], "touch": 0})
        b["n"] += 1
        b["cap"].append(d["capture_vs_mid_per_contract"])
        b["touch"] += 1 if d["at_touch"] else 0
    print("  %-14s %-8s %-10s %-14s %-10s" %
          ("spread(ticks)", "n", "at touch", "median capture", "share"))
    for w in sorted(by_w):
        b = by_w[w]
        print("  %-14d %-8d %-10d %-14.5f %-10.1f%%" %
              (w, b["n"], b["touch"], statistics.median(b["cap"]),
               100.0 * b["n"] / len(out)))
    zero = [d for d in out if d["capture_vs_mid_per_contract"] <= 0]
    print("\n  OBSERVATIONS WHERE THE ENGINE'S QUOTE CAPTURES NOTHING")
    print("  (or worse) vs the mid: %d of %d  (%.1f%%)"
          % (len(zero), len(out), 100.0 * len(zero) / len(out)))
    print("  THIS IS OUR QUOTING RULE, NOT THE VENUE'S TICK. The rule")
    print("  improves by one tick 'where there is room', and on a")
    print("  two-tick book that lands exactly on the mid.")

    # ── the exits, as cash ────────────────────────────────────────────
    print("\n" + "-" * 74)
    print("2. FEASIBLE EXITS, AS CASH (per %d-contract fill)" % CONTRACTS)
    print("-" * 74)
    closes = [d["close_net"] for d in out if d["close_net"] is not None]
    comps = [d["complete_pair_ev"] for d in out
             if d["complete_pair_ev"] is not None]
    print("  ENTER THEN IMMEDIATELY CROSS OUT (round trip, both fees):")
    print("    median net   %+.4f     mean net   %+.4f"
          % (statistics.median(closes), statistics.mean(closes)))
    print("    positive in  %d of %d  (%.1f%%)"
          % (sum(1 for c in closes if c > 0), len(closes),
             100.0 * sum(1 for c in closes if c > 0) / len(closes)))
    print("    -- this is the cost of a round trip with NO price move.")
    print("       It is what the policy must beat, not a strategy.")
    print("\n  COMPLETE THE PAIR from a maker first leg:")
    print("    median EV    %+.4f     mean EV    %+.4f"
          % (statistics.median(comps), statistics.mean(comps)))
    print("    positive in  %d of %d  (%.1f%%)"
          % (sum(1 for c in comps if c > 0), len(comps),
             100.0 * sum(1 for c in comps if c > 0) / len(comps)))
    hold_ok = sum(1 for d in out if d["hold_identified"])
    print("\n  HOLD TO SETTLEMENT:")
    print("    identified in %d of %d -- the engine REFUSES without a"
          % (hold_ok, len(out)))
    print("    fair value, and BETTOR has none. Missing: %s"
          % (out[0]["hold_missing"] or "-"))

    # ── what the entry price buys, before any flow assumption ─────────
    print("\n" + "-" * 74)
    print("3. THE GROSS EDGE THE ENTRY PRICE BUYS")
    print("-" * 74)
    caps = sorted(d["capture_vs_mid_per_contract"] for d in out)
    print("  capture vs mid, per contract, across %d observations:" % len(caps))
    for lbl, v in (("min", caps[0]),
                   ("p25", caps[len(caps) // 4]),
                   ("median", statistics.median(caps)),
                   ("p75", caps[3 * len(caps) // 4]),
                   ("max", caps[-1])):
        print("    %-8s %+.5f" % (lbl, v))
    reb = statistics.median(d["rebate_total"] for d in out) / CONTRACTS
    print("\n  median maker rebate, per contract, at %d-contract fills:"
          % CONTRACTS)
    print("    %+.6f" % reb)
    print("\n  GROSS per contract, if the mid is fair value:")
    print("    %+.6f" % (statistics.median(caps) + reb))
    print("\n  THIS IS NOT AN EDGE ESTIMATE. It is what the entry price")
    print("  and the fee schedule give us BEFORE anything is known")
    print("  about who fills us or where the price goes. The unknown")
    print("  is subtracted from this, and it is unmeasured.")

    # ── 4. THE TWO-SIDED MAKER PAIR -- the sequential inventory case
    print("\n" + "-" * 74)
    print("4. THE TWO-SIDED MAKER PAIR  (Class A), AS NET CASH")
    print("-" * 74)
    print("  Rest on BOTH sides: a bid at the YES bid and an offer at")
    print("  the YES ask. VERIFIED from the capture, not assumed --")
    print("  the bodies carry longQuote = bestAsk and")
    print("  shortQuote = 1 - bestBid, so the NO side is the exact")
    print("  mirror of the YES book and buying NO at 1-ask IS selling")
    print("  YES at ask.")
    print()
    print("  That makes the same arithmetic readable two ways, and they")
    print("  differ ONLY in capital, not in gross:")
    print("    a) buy YES at bid, sell YES at ask -> FLAT, +spread now")
    print("    b) buy YES at bid, buy NO at 1-ask -> a PAIR costing")
    print("       1-spread that settles at 1, +spread AT SETTLEMENT")
    print("  (a) releases capital immediately; (b) ties up 1-spread per")
    print("  pair until the event resolves. I described this as a")
    print("  'complementary pair' earlier; (a) is the more accurate")
    print("  reading and the cheaper one.")
    print()
    print("  REJECTING THE TAKER PAIR DOES NOT REJECT THIS. The taker")
    print("  pair costs 1 + spread; this earns 1 - spread. Same")
    print("  identity, opposite sign, and only the taker side was")
    print("  falsified.\n")
    both, single, bes = [], [], []
    for d in out:
        bid, ask, t = d["bid"], d["ask"], d["tick"]
        C = CONTRACTS
        r_yes = ev.fee(bid, C, maker=True)
        r_no = ev.fee(1.0 - ask, C, maker=True)
        gross = (1.0 - (bid + (1.0 - ask))) * C
        both_v = (gross + r_yes + r_no) / C
        book = ev.Book(slug="x", bid=bid, ask=ask, tick=t)
        inv = ev.Inventory(contracts=C, avg_cost=bid)
        cp = ev.incremental_ev("COMPLETE_PAIR", book, inv, contracts=C)
        one_v = ((cp.ev or 0.0) + r_yes) / C
        both.append(both_v)
        single.append(one_v)
        denom = both_v - one_v
        if denom > 0:
            bes.append(one_v / (one_v - both_v) * -1.0 if False
                       else (-one_v) / denom)
    print("  %-34s %-12s %-12s" % ("outcome", "median", "mean"))
    print("  %-34s %+.6f    %+.6f" % ("BOTH legs fill (maker/maker)",
                                      statistics.median(both),
                                      statistics.mean(both)))
    print("  %-34s %+.6f    %+.6f" % ("ONE leg fills, complete taker",
                                      statistics.median(single),
                                      statistics.mean(single)))
    print("  positive when both fill: %d of %d  (%.1f%%)"
          % (sum(1 for v in both if v > 0), len(both),
             100.0 * sum(1 for v in both if v > 0) / len(both)))
    print("  positive when one fills: %d of %d  (%.1f%%)"
          % (sum(1 for v in single if v > 0), len(single),
             100.0 * sum(1 for v in single if v > 0) / len(single)))
    if bes:
        print("\n  BREAK-EVEN DOUBLE-FILL RATE q*, where")
        print("    q*(both) + (1-q*)(one leg) = 0")
        for lbl, v in (("min", min(bes)),
                       ("median", statistics.median(bes)),
                       ("max", max(bes))):
            print("    %-8s %.4f" % (lbl, v))
        print("\n  q* is a probability ABOUT OUR OWN ORDERS, in net cash.")
        print("  It is not a mixture proportion and needs no")
        print("  classification of counterparties. That makes it the")
        print("  best-posed unknown in the whole programme.")
    print("\n  WHY q IS NOT A FREE PARAMETER: the two fills are")
    print("  ADVERSELY CORRELATED. One leg fills precisely when the")
    print("  price moves toward it, which is when the other leg is")
    print("  least likely to fill. Assuming independence would be the")
    print("  single most flattering error available here, and it is")
    print("  not made.")

    res = {
        "two_sided_pair_both_fill_median": round(statistics.median(both), 6),
        "two_sided_pair_one_leg_median": round(statistics.median(single), 6),
        "breakeven_double_fill_rate_median": (
            round(statistics.median(bes), 4) if bes else None),
        "observations": len(out),
        "distinct_markets": len(markets),
        "median_capture_vs_mid_per_contract": round(
            statistics.median(caps), 6),
        "median_rebate_per_contract": round(reb, 6),
        "gross_per_contract_if_mid_is_fair": round(
            statistics.median(caps) + reb, 6),
        "observations_capturing_nothing": len(zero),
        "share_capturing_nothing": round(len(zero) / len(out), 4),
        "round_trip_median_net_per_fill": round(statistics.median(closes), 4),
        "complete_pair_median_ev_per_fill": round(statistics.median(comps), 4),
        "hold_to_settlement": "NOT IDENTIFIED -- engine refuses without a "
                              "fair value",
        "tick_source": "INFERRED from the price grid; "
                       "orderPriceMinTickSize absent from captured bodies",
        "data_status": "DEVELOPMENT",
        "corrections": [
            "p_fill decomposition holds only in the engine's simplified "
            "model; quote price, size and waiting time move p_fill and "
            "the conditional return together",
            "33.9% was a scenario, not an empirical threshold; a pilot "
            "measures NET CASH, not a mixture proportion",
            "the tick imposes no half-spread haircut; a one-tick book "
            "captures the FULL half-spread and it is OUR two-tick "
            "quoting rule that captures zero",
        ],
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "acceptance", "feasible_cashflows_result.json")
    with open(path, "w") as f:
        json.dump(res, f, indent=2)
    print("\nwrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
