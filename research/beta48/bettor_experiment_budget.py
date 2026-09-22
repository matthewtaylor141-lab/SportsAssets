"""M1-M3 BUDGET, derived line by line rather than asserted.

THE ARITHMETIC THIS REPLACES. Two reports gave two totals, neither
derived:

    report A   M1 $4.00 + M2 $4.00 + M3 "$8.00 - $4.00 + 2 fees
               ~ $4.20"                                  = $12.20
    report B   the same three, M3 restated as $4.24      = $12.24
               and the difference explained as "$0.14 of fee"

Both are wrong, and the explanation was wrong too. $4.00 + $0.14 is
$4.14, not $4.24, so report B's own stated reason does not produce
report B's own number. And the $4 / $4 / $8 figures were rounded-up
COLLATERAL CEILINGS being reported as MAXIMUM LOSS, which are three
different quantities:

    GROSS ORDER EXPOSURE   the sum of order notionals sent
    PEAK COMMITTED CAPITAL the most the venue holds at any one instant
    MAXIMUM LOSS           the worst cash outcome over every path,
                           including failed cleanup and residual

Sequential experiments have a peak equal to the LARGEST, not the sum.

Run:  python research/beta48/bettor_experiment_budget.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bettor_policy_ev as ev                                   # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "acceptance", "experiment_budget.json")

# The reference book the experiments are specified against: a two-tick
# market at a one-cent grid around the mid, which is the most expensive
# place on the fee curve (p(1-p) is maximised at 0.50).
TICK = 0.01
BID = 0.49
ASK = 0.51
QTY = 4


def m1():
    """One resting BUY, 5 ticks below the bid, priced NOT to fill."""
    px = round(BID - 5 * TICK, 4)            # 0.44
    gross = px * QTY
    # if it fills it fills as a MAKER, so the fee is a credit
    rebate = ev.fee(px, QTY, maker=True)
    return {
        "name": "M1 collateral",
        "order": "BUY_LONG LIMIT %g @ %.2f GTC" % (QTY, px),
        "gross_order_exposure": round(gross, 4),
        "peak_committed_capital": round(gross, 4),
        "maker_rebate_if_filled": round(rebate, 4),
        "residual_if_cleanup_fails": "%g contracts long" % QTY,
        "worst_case_position_value": 0.0,
        "maximum_loss": round(gross - rebate, 4),
        "loss_derivation": ("the position goes to zero: %.4f paid, "
                            "less the %.4f maker rebate if it filled"
                            % (gross, rebate)),
    }


def m2():
    """One resting BUY at the touch, priced TO fill, then cancelled."""
    px = BID
    gross = px * QTY
    rebate = ev.fee(px, QTY, maker=True)
    return {
        "name": "M2 cancel race",
        "order": "BUY_LONG LIMIT %g @ %.2f GTC, cancel immediately" % (
            QTY, px),
        "gross_order_exposure": round(gross, 4),
        "peak_committed_capital": round(gross, 4),
        "maker_rebate_if_filled": round(rebate, 4),
        "residual_if_cleanup_fails": "%g contracts long" % QTY,
        "worst_case_position_value": 0.0,
        "maximum_loss": round(gross - rebate, 4),
        "loss_derivation": ("a fill inside the cancel window IS the "
                            "measurement; worst case it is worth zero: "
                            "%.4f paid less the %.4f rebate"
                            % (gross, rebate)),
    }


def m3():
    """Two taker orders that together form a matched pair."""
    yes_px, no_px = ASK, round(1.0 - BID, 4)     # 0.51 and 0.51
    gross = (yes_px + no_px) * QTY
    f_yes = ev.fee(yes_px, QTY, maker=False)
    f_no = ev.fee(no_px, QTY, maker=False)
    fees = f_yes + f_no
    payout = 1.0 * QTY                            # a matched pair pays 1
    both = payout - gross + fees                  # both legs fill
    # THE WORST PATH IS NOT "BOTH FILL". It is one leg filling and the
    # other failing -- then there is no pair, and the single leg can
    # settle at zero.
    one_leg = -(yes_px * QTY) + f_yes
    return {
        "name": "M3 netting and release",
        "order": ("BUY_LONG %g @ %.2f (taker) THEN BUY_SHORT %g @ %.2f "
                  "(taker)" % (QTY, yes_px, QTY, no_px)),
        "gross_order_exposure": round(gross, 4),
        "peak_committed_capital": round(gross, 4),
        "taker_fees": round(fees, 4),
        "outcome_both_legs_fill": round(both, 4),
        "outcome_one_leg_only": round(one_leg, 4),
        "residual_if_cleanup_fails": ("a matched pair, which pays "
                                      "exactly %.2f" % payout),
        "maximum_loss": round(-min(both, one_leg), 4),
        "loss_derivation": (
            "both fill: pay %.4f, receive %.2f at settlement, pay %.4f "
            "in taker fees -> %.4f. ONE LEG ONLY: pay %.4f for a single "
            "leg that can settle at zero, plus %.4f fee -> %.4f. The "
            "worst path is the second, not the first."
            % (gross, payout, -fees, both, yes_px * QTY, -f_yes,
               one_leg)),
    }


def main():
    parts = [m1(), m2(), m3()]
    gross_sum = sum(p["gross_order_exposure"] for p in parts)
    peak_sequential = max(p["peak_committed_capital"] for p in parts)
    peak_concurrent = sum(p["peak_committed_capital"] for p in parts)
    loss_sum = sum(p["maximum_loss"] for p in parts)

    print("=" * 72)
    print("M1-M3 BUDGET -- derived, at bid %.2f / ask %.2f, %d contracts"
          % (BID, ASK, QTY))
    print("=" * 72)
    for p in parts:
        print()
        print("%s" % p["name"])
        print("  order                     %s" % p["order"])
        print("  gross order exposure      $%0.4f" % p[
            "gross_order_exposure"])
        print("  peak committed capital    $%0.4f" % p[
            "peak_committed_capital"])
        print("  residual if cleanup fails %s" % p[
            "residual_if_cleanup_fails"])
        print("  MAXIMUM LOSS              $%0.4f" % p["maximum_loss"])
        print("  derivation                %s" % p["loss_derivation"])

    print()
    print("=" * 72)
    print("THE THREE QUANTITIES, KEPT APART")
    print("=" * 72)
    print("  gross order exposure, all three     $%0.4f" % gross_sum)
    print("  peak committed capital, SEQUENTIAL  $%0.4f   <- the funding"
          % peak_sequential)
    print("                                                  requirement")
    print("  peak committed capital, concurrent  $%0.4f   (not the plan)"
          % peak_concurrent)
    print("  MAXIMUM COMBINED LOSS               $%0.4f" % loss_sum)
    print()
    print("  Previously reported: $16.00 exposure / $12.20 then $12.24")
    print("  loss. Those used rounded-up collateral ceilings as losses")
    print("  and the $12.24 did not follow from its own stated reason.")

    res = {"reference_book": {"bid": BID, "ask": ASK, "tick": TICK,
                              "contracts": QTY},
           "experiments": parts,
           "gross_order_exposure_total": round(gross_sum, 4),
           "peak_committed_capital_sequential": round(peak_sequential, 4),
           "peak_committed_capital_if_concurrent": round(peak_concurrent, 4),
           "maximum_combined_loss": round(loss_sum, 4),
           "supersedes": {"reported_exposure": 16.00,
                          "reported_loss_a": 12.20,
                          "reported_loss_b": 12.24,
                          "why_wrong": (
                              "collateral ceilings reported as maximum "
                              "loss; and $4.00 + $0.14 is $4.14, not "
                              "$4.24, so the second total did not follow "
                              "from its own stated correction")}}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(res, f, indent=2, sort_keys=True)
    print("\nwritten: %s" % os.path.relpath(OUT))


if __name__ == "__main__":
    main()
