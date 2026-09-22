"""WHAT WOULD HAVE TO BE TRUE for a BETTOR maker policy to earn money.

This does not stop at `P_FILL = NOT_IDENTIFIED`. It inverts the
question: given the fee schedule we have VERIFIED and the spreads we
have MEASURED, what fill behaviour and post-fill return would make the
policy profitable -- and how far is that from the only adverse-selection
number we hold?

THE DECOMPOSITION THAT MAKES THIS ANSWERABLE. From the engine itself
(`bettor_policy_ev.incremental_ev`, the QUOTE_BID / QUOTE_OFFER
branch):

    value_if_filled = as_fill * contracts + rebate
    EV              = p_fill * value_if_filled

`p_fill` multiplies. It CANNOT change the sign. So:

  * whether a maker policy makes money per fill depends on
    `as_fill` (= E[SETTLEMENT - QUOTE | FILLED]) and the rebate ONLY;
  * `p_fill` decides the RATE of earning, the capital turnover and the
    capacity -- not the sign.

That splits one unanswerable question into one answerable and one
bounded. The break-even is exact arithmetic on a verified fee
schedule. The remaining unknown is named, and priced.

NOTHING HERE IS A SEPARATE MODEL. Every price, fee and rebate comes
from `bettor_policy_ev` -- the same module the engine's policies call.
The quoting rule is the engine's own `QUOTE_BID` rule, invoked through
`incremental_ev`, not reimplemented.

Run:  python research/beta48/bettor_maker_breakeven.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bettor_policy_ev as ev                                  # noqa: E402
from bettor_economic_test import load_holdout                  # noqa: E402

# ── THE ONE ADVERSE-SELECTION MEASUREMENT WE HOLD ──────────────────────
#
# Resting an offer that RN1 lifts earns -0.0090/share, 95% CI
# [-0.0143, -0.0038], n = 9,337 independent conditions, settlement
# strictly after trade.
#
# SCOPE, WHICH MUST TRAVEL WITH THE NUMBER EVERY TIME IT IS USED:
#   * Polymarket GLOBAL CLOB, not PMUS. Different book, different fee
#     schedule, different participants.
#   * ASK SIDE ONLY. RN1 only ever buys, so only offers are observed
#     being lifted.
#   * SELECTED BECAUSE RN1 TRADED IT. This is the return to a maker
#     whose counterparty is a specifically informed trader lifting at
#     the touch. It is a STRESS CASE, not an expectation.
TOXIC_AS_FILL = -0.0090
TOXIC_CI = (-0.0143, -0.0038)
TOXIC_N_CONDITIONS = 9337
TOXIC_SCOPE = ("global CLOB, ask side only, counterparty-selected; "
               "SELECTION_BIAS = SEVERE")


# Fill size matters, and not linearly -- see `rebate_per_contract`.
# This is the size the headline numbers are quoted at. It is an
# ASSUMPTION about our own order size, not a measurement of anything.
ASSUMED_FILL_CONTRACTS = 100.0


def rebate_per_contract(px: float,
                        contracts: float = ASSUMED_FILL_CONTRACTS) -> float:
    """The VERIFIED PMUS maker rebate at this price, PER CONTRACT.

    Straight from the engine's fee function so there is one fee
    implementation, not two. `fee()` returns signed cash: positive is
    a credit to us.

    THE REBATE IS NOT LINEAR IN SIZE, AND THIS IS A REAL ECONOMIC
    FACT, not a rounding nuisance. The venue computes
    `theta * contracts * p * (1-p)` and then rounds the RESULT to the
    cent, half-to-even, PER FILL. At the median mid of this corpus
    (0.13) the raw maker rebate on ONE contract is 0.001414 -- which
    rounds to ZERO. So:

        1 contract     rebate  0.000000/contract   (rounds away)
        2 contracts    rebate  0.000000/contract   (rounds away)
        4 contracts    rebate  0.002500/contract   (rounds UP)
      100 contracts    rebate  0.001400/contract
     1000 contracts    rebate  0.001410/contract

    A maker filling in ones and twos at this price earns NO REBATE AT
    ALL. Any break-even that quotes a per-contract rebate without
    naming the fill size is quoting a number the venue will not pay.
    """
    return ev.fee(px, contracts, maker=True) / contracts


def breakeven_as_fill(px: float) -> float:
    """The value of E[SETTLEMENT - QUOTE | FILLED] at which a filled
    quote exactly breaks even.

    value_if_filled = as_fill + rebate = 0  =>  as_fill = -rebate.

    Note what is NOT here: `p_fill`. It multiplies the whole
    expression, so it cannot move the break-even point.
    """
    return -rebate_per_contract(px)


def toxic_fraction_breakeven(half_spread: float, px: float,
                             toxic: float = TOXIC_AS_FILL) -> float | None:
    """What fraction of fills may be TOXIC before the policy breaks even.

    A two-state mixture, and both states are assumptions that are
    labelled as such below:

      uninformed fill  -> we keep the half-spread          (+half_spread)
      toxic fill       -> we earn the stress-case return   (`toxic`)

        EV = phi*toxic + (1-phi)*half_spread + rebate = 0

    Returns None when the policy cannot break even at any mixture, or
    when it breaks even at every mixture.
    """
    r = rebate_per_contract(px)
    denom = half_spread - toxic
    if denom <= 0:
        return None
    phi = (half_spread + r) / denom
    if phi < 0 or phi > 1:
        return None
    return phi


def main() -> int:
    print("=" * 74)
    print("WHAT WOULD HAVE TO BE TRUE -- BETTOR maker policy break-even")
    print("=" * 74)
    print(ev.describe()["fees"]["status"]
          if isinstance(ev.describe().get("fees"), dict) else "")

    rows, files = load_holdout()
    if not rows:
        print("\nNO OBSERVATIONS. Set BETTOR_CAPTURE_ROOT to the capture "
              "directory.")
        return 2

    # Two-sided, open, and inside the frozen rule's price band.
    obs = []
    for r in rows:
        sp = r["ask"] - r["bid"]
        mid = 0.5 * (r["ask"] + r["bid"])
        if sp <= 0 or not (0 < r["bid"] < r["ask"] < 1):
            continue
        obs.append({"slug": r["slug"], "bid": r["bid"], "ask": r["ask"],
                    "spread": sp, "mid": mid, "half": sp / 2.0,
                    "segment": r.get("segment")})
    markets = sorted({o["slug"] for o in obs})
    print("\nDATASET")
    print("  segments read          %d" % len(files))
    print("  two-sided observations %d" % len(obs))
    print("  DISTINCT MARKETS       %d   <- the unit that matters" %
          len(markets))
    print("  NOTE: these capture segments were opened by the Class C")
    print("        analysis, so they are DEVELOPMENT data now. Nothing")
    print("        here is a holdout result and none is claimed.")

    spreads = sorted(o["spread"] for o in obs)
    mids = sorted(o["mid"] for o in obs)
    med_spread = statistics.median(spreads)
    med_mid = statistics.median(mids)
    print("\nMEASURED BOOK (PMUS, this corpus)")
    print("  median spread          %.4f" % med_spread)
    print("  median half-spread     %.4f" % (med_spread / 2.0))
    print("  median mid             %.4f" % med_mid)

    # ── 1. THE BREAK-EVEN, EXACTLY ────────────────────────────────────
    print("\n" + "-" * 74)
    print("1. BREAK-EVEN ADVERSE SELECTION  (exact, on a VERIFIED fee "
          "schedule)")
    print("-" * 74)
    print("  value_if_filled = as_fill + rebate.  p_fill MULTIPLIES it,")
    print("  so p_fill cannot change the sign -- only the rate.\n")
    print("  THE REBATE IS ROUNDED TO THE CENT PER FILL, so it depends")
    print("  on FILL SIZE. Quoted here at %d contracts." %
          ASSUMED_FILL_CONTRACTS)
    print()
    print("  %-10s %-16s %-18s" % ("mid", "rebate/contract",
                                   "break-even as_fill"))
    for p in (0.10, 0.13, 0.20, 0.30, 0.40, 0.50):
        print("  %-10.2f %-16.6f %-18.6f" %
              (p, rebate_per_contract(p), breakeven_as_fill(p)))
    print("\n  AND THE SAME THING AT THE MEDIAN MID BY FILL SIZE:")
    print("  %-14s %-18s %-18s" % ("contracts", "rebate/contract",
                                   "break-even as_fill"))
    for c in (1, 2, 4, 10, 100, 1000):
        rc = rebate_per_contract(med_mid, float(c))
        print("  %-14d %-18.6f %-18.6f" % (c, rc, -rc))
    print("\n  A MAKER FILLING IN ONES AND TWOS EARNS NO REBATE AT ALL")
    print("  at this price. The rebate is not a per-share constant.")
    be_med = breakeven_as_fill(med_mid)
    print("\n  At this corpus's median mid (%.4f):" % med_mid)
    print("    the policy breaks even if E[SETTLEMENT - QUOTE | FILLED]")
    print("    is no worse than  %+.6f  per share." % be_med)

    # ── 2. AGAINST THE ONLY MEASUREMENT WE HOLD ───────────────────────
    print("\n" + "-" * 74)
    print("2. AGAINST THE ONLY ADVERSE-SELECTION NUMBER WE HOLD")
    print("-" * 74)
    print("  TOXIC_FLOW_STRESS_CASE  %+.4f  95%% CI [%+.4f, %+.4f], "
          "n=%d" % (TOXIC_AS_FILL, TOXIC_CI[0], TOXIC_CI[1],
                    TOXIC_N_CONDITIONS))
    print("  scope: %s" % TOXIC_SCOPE)
    print("\n  break-even needs      %+.6f" % be_med)
    print("  stress case delivers  %+.6f" % TOXIC_AS_FILL)
    gap = TOXIC_AS_FILL - be_med
    print("  shortfall             %+.6f  per share" % gap)
    cover = abs(rebate_per_contract(med_mid) / TOXIC_AS_FILL)
    print("  the rebate covers     %.1f%% of the stress-case deficit"
          "  (at %d contracts)" % (100 * cover, ASSUMED_FILL_CONTRACTS))
    print("\n  READ THIS CORRECTLY: it does NOT say the policy loses.")
    print("  It says that IF every fill were RN1-grade informed flow,")
    print("  on that venue, the policy loses by roughly 3x. That is the")
    print("  worst case we can evidence, not the expected case.")

    # ── 3. THE MIXTURE: HOW MUCH TOXICITY IS SURVIVABLE ───────────────
    print("\n" + "-" * 74)
    print("3. HOW MUCH OF THE FLOW MAY BE TOXIC")
    print("-" * 74)
    print("  ASSUMPTION, NOT MEASUREMENT: an uninformed fill leaves us")
    print("  the half-spread. That is true only if the mid is our fair")
    print("  value. BETTOR has no identified fair value")
    print("  (FV_BETTOR_INDEPENDENT = NOT_IDENTIFIED), so this is the")
    print("  assumption the whole scenario rests on, and it is the one")
    print("  a pilot would test first.\n")
    phi = toxic_fraction_breakeven(med_spread / 2.0, med_mid)
    print("  %-14s %-14s %-12s" % ("half-spread", "toxic share", "verdict"))
    for hs in sorted({round(med_spread / 2.0, 4), 0.0025, 0.0050, 0.0075}):
        f = toxic_fraction_breakeven(hs, med_mid)
        v = "never profitable" if f is None else "break-even at %.1f%%" % (
            100 * f)
        print("  %-14.4f %-14s %-12s" %
              (hs, "-" if f is None else "%.3f" % f, v))
    if phi is not None:
        print("\n  AT THE MEASURED MEDIAN HALF-SPREAD (%.4f):" %
              (med_spread / 2.0))
        print("    the policy breaks even when %.1f%% of fills are"
              % (100 * phi))
        print("    RN1-grade toxic. Below that it earns; above it loses.")
        print("\n  THE MISSING QUANTITY IS THEREFORE NAMED AND BOUNDED:")
        print("    TOXIC_FILL_FRACTION on PMUS = NOT_IDENTIFIED")
        print("    break-even value            = %.3f" % phi)

    # ── 4. SENSITIVITY: WHAT MODESTLY WORSE EXECUTION DOES ────────────
    print("\n" + "-" * 74)
    print("4. EXECUTION SENSITIVITY")
    print("-" * 74)
    print("  If profitability disappears under modestly worse execution,")
    print("  that is a material result. Here is where it disappears.\n")
    hs = med_spread / 2.0
    r = rebate_per_contract(med_mid)
    print("  %-28s %-12s %-10s" % ("scenario", "EV/fill", "sign"))
    scen = [
        ("optimistic: 0% toxic", 0.0),
        ("10% toxic", 0.10),
        ("25% toxic", 0.25),
        ("break-even mixture", phi if phi is not None else 0.0),
        ("50% toxic", 0.50),
        ("conservative: 100% toxic", 1.0),
    ]
    for name, f in scen:
        val = f * TOXIC_AS_FILL + (1 - f) * hs + r
        print("  %-28s %+.6f    %s" % (name, val,
                                       "EARNS" if val > 0 else "LOSES"))
    print("\n  AND IF WE LOSE HALF THE ASSUMED HALF-SPREAD EDGE")
    print("  (quoting at the touch on a grid that leaves no room, which")
    print("   493 of 788 observations in this corpus do):")
    for name, f in scen:
        val = f * TOXIC_AS_FILL + (1 - f) * (hs * 0.5) + r
        print("  %-28s %+.6f    %s" % (name, val,
                                       "EARNS" if val > 0 else "LOSES"))

    # ── 5. WHAT p_fill ACTUALLY DECIDES ───────────────────────────────
    print("\n" + "-" * 74)
    print("5. WHAT p_fill DECIDES -- CAPITAL, NOT SIGN")
    print("-" * 74)
    print("  p_fill multiplies value_if_filled, so it scales the rate of")
    print("  earning and the capital turnover. It does not decide")
    print("  whether the policy earns at all.\n")
    print("  %-10s %-16s %-18s" % ("p_fill", "EV per quote", "quotes per $1"))
    base = 0.25 * TOXIC_AS_FILL + 0.75 * hs + r     # a 25%-toxic scenario
    for pf in (0.01, 0.05, 0.10, 0.25, 0.50):
        print("  %-10.2f %-16.6f %-18.0f" % (pf, pf * base,
                                             (1.0 / (pf * base))
                                             if pf * base > 0 else 0))
    print("\n  The right-hand column is how many quotes must be RESTED to")
    print("  earn one dollar at that fill rate -- the capacity question,")
    print("  and the reason p_fill still has to be measured even though")
    print("  it cannot flip the sign.")

    # ── 6. THE SMALLEST EXPERIMENT THAT RESOLVES IT ───────────────────
    print("\n" + "-" * 74)
    print("6. THE SMALLEST EXPERIMENT THAT RESOLVES THE MISSING QUANTITY")
    print("-" * 74)
    print("  MEASURE: realised (settlement - our fill price) on BETTOR's")
    print("  OWN admitted fills. That is `as_fill` by definition and")
    print("  nothing else identifies it -- not a perfect prospective")
    print("  capture of an unselected PMUS universe, which identifies")
    print("  E[SETTLEMENT - QUOTE | STATE] and leaves fill selection")
    print("  exactly where it was.")
    print("\n  It requires an order path and capital. Neither is")
    print("  authorised, and this file does not request them.")
    out = {
        "median_spread": round(med_spread, 6),
        "median_half_spread": round(med_spread / 2.0, 6),
        "median_mid": round(med_mid, 6),
        "rebate_per_contract_at_median_mid": round(r, 6),
        "breakeven_as_fill_at_median_mid": round(be_med, 6),
        "toxic_stress_case": TOXIC_AS_FILL,
        "toxic_stress_scope": TOXIC_SCOPE,
        "breakeven_toxic_fraction": None if phi is None else round(phi, 4),
        "observations": len(obs),
        "distinct_markets": len(markets),
        "data_status": "DEVELOPMENT -- these segments were opened by the "
                       "Class C analysis; no untouched PMUS holdout remains",
        "p_fill_affects": "rate and capacity, NOT the sign",
        "missing_quantity": "TOXIC_FILL_FRACTION on PMUS, and the "
                            "mid-is-fair-value assumption underneath it",
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "acceptance", "maker_breakeven_result.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print("\nwrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
