"""Fit a resting-fill model on our own order history, validate it forward.

THE NUMBER THIS REPLACES. "A genuinely resting order filled ~26% of
the time" is not a property of resting. It is one ratio over a
denominator that mixes an order cancelled after five seconds with one
that rested ten minutes, at every distance from the touch, across
1,006 markets. Transferring it to a different policy would be an error
of kind, not of precision.

WHAT 'GENUINELY RESTING' MEANS HERE. Derived from the book stored at
placement, not taken from our own flag:

    BUY_LONG    wire < ask_at_place      (does not cross the offer)
    SELL_LONG   wire > bid_at_place      (does not cross the bid)

and additionally NOT taker_at_placement. The two disagree on 1,891 of
10,815 orders (17.5%), so the flag alone would have been wrong about
one order in six:

    our flag says   book says    orders   with fill
    not taker       crossed       1,418          38     <- flagged
                                                           resting,
                                                           actually
                                                           crossing,
                                                           and almost
                                                           never filled
    not taker       resting       7,223       2,294     <- the universe
    taker           crossed       1,701       1,701
    taker           resting         473         473

CENSORING IS THE CENTRAL PROBLEM. Of the 7,223 resting orders, 4,916
ended in CANCELLATION and 1,933 in a fill. A cancellation is not a
failure to fill; it is the observation ending. Median time to fill
(86.3 s) and median time to cancel (84.9 s) are almost identical,
which is what it looks like when the cancel policy, not the market,
decides the denominator.

So fill probability is modelled AGAINST EXPOSURE, and the exposure
bands are reported rather than collapsed:

    under 30 s     23.3%        2-10 min      45.9%
    30-120 s       37.6%        over 10 min   10.3%    <- NOT a decay

The over-10-minute collapse is selection, not decay: an order still
resting after ten minutes is disproportionately in a market where
nothing trades at all. Reading it as "fills decay after ten minutes"
would invert the cause.

HOLD-OUT IS CHRONOLOGICAL. Fit on 2026-09-06..08, predict 09-09..10.
Never a random split: adjacent orders in one market on one day are not
independent draws, and a random split leaks the market's own activity
level across the boundary.

Run:  python research/beta48/bettor_fill_calibration.py
"""
from __future__ import annotations

import json
import math
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "acceptance", "fill_calibration.json")

# (day, exposure band, cents inside touch, orders, filled)
# Read out of mirror_orders; -3 and +2 are clamped end bins.
GRID = [
    ("2026-09-06", "a_lt30s", -3, 49, 0), ("2026-09-06", "a_lt30s", -1, 4, 0),
    ("2026-09-06", "a_lt30s", 0, 28, 1),
    ("2026-09-06", "b_30_120s", -3, 10, 2), ("2026-09-06", "b_30_120s", -2, 4, 3),
    ("2026-09-06", "b_30_120s", -1, 6, 3), ("2026-09-06", "b_30_120s", 0, 56, 26),
    ("2026-09-06", "c_2_10min", -3, 13, 5), ("2026-09-06", "c_2_10min", -2, 9, 4),
    ("2026-09-06", "c_2_10min", -1, 8, 4), ("2026-09-06", "c_2_10min", 0, 44, 26),
    ("2026-09-06", "d_gt10min", -3, 27, 0), ("2026-09-06", "d_gt10min", -2, 5, 0),
    ("2026-09-06", "d_gt10min", -1, 4, 2), ("2026-09-06", "d_gt10min", 0, 9, 3),
    ("2026-09-07", "a_lt30s", -3, 10, 1), ("2026-09-07", "a_lt30s", -2, 2, 0),
    ("2026-09-07", "a_lt30s", -1, 7, 1), ("2026-09-07", "a_lt30s", 0, 29, 7),
    ("2026-09-07", "a_lt30s", 1, 1, 0),
    ("2026-09-07", "b_30_120s", -3, 31, 13), ("2026-09-07", "b_30_120s", -2, 21, 12),
    ("2026-09-07", "b_30_120s", -1, 49, 28), ("2026-09-07", "b_30_120s", 0, 191, 101),
    ("2026-09-07", "b_30_120s", 2, 1, 1),
    ("2026-09-07", "c_2_10min", -3, 74, 29), ("2026-09-07", "c_2_10min", -2, 34, 15),
    ("2026-09-07", "c_2_10min", -1, 50, 22), ("2026-09-07", "c_2_10min", 0, 112, 70),
    ("2026-09-07", "c_2_10min", 1, 1, 1), ("2026-09-07", "c_2_10min", 2, 5, 2),
    ("2026-09-07", "d_gt10min", -3, 62, 7), ("2026-09-07", "d_gt10min", -2, 13, 2),
    ("2026-09-07", "d_gt10min", -1, 54, 4), ("2026-09-07", "d_gt10min", 0, 41, 10),
    ("2026-09-08", "a_lt30s", -3, 85, 16), ("2026-09-08", "a_lt30s", -2, 37, 10),
    ("2026-09-08", "a_lt30s", -1, 95, 18), ("2026-09-08", "a_lt30s", 0, 257, 44),
    ("2026-09-08", "a_lt30s", 1, 6, 2), ("2026-09-08", "a_lt30s", 2, 9, 2),
    ("2026-09-08", "b_30_120s", -3, 177, 43), ("2026-09-08", "b_30_120s", -2, 65, 29),
    ("2026-09-08", "b_30_120s", -1, 145, 82), ("2026-09-08", "b_30_120s", 0, 480, 200),
    ("2026-09-08", "b_30_120s", 1, 8, 0), ("2026-09-08", "b_30_120s", 2, 13, 7),
    ("2026-09-08", "c_2_10min", -3, 156, 57), ("2026-09-08", "c_2_10min", -2, 54, 28),
    ("2026-09-08", "c_2_10min", -1, 109, 61), ("2026-09-08", "c_2_10min", 0, 254, 154),
    ("2026-09-08", "c_2_10min", 1, 4, 2), ("2026-09-08", "c_2_10min", 2, 4, 1),
    ("2026-09-08", "d_gt10min", -3, 115, 8), ("2026-09-08", "d_gt10min", -2, 43, 4),
    ("2026-09-08", "d_gt10min", -1, 60, 7), ("2026-09-08", "d_gt10min", 0, 119, 13),
    ("2026-09-08", "d_gt10min", 1, 2, 2), ("2026-09-08", "d_gt10min", 2, 1, 0),
    ("2026-09-09", "a_lt30s", -3, 80, 15), ("2026-09-09", "a_lt30s", -2, 37, 11),
    ("2026-09-09", "a_lt30s", -1, 75, 23), ("2026-09-09", "a_lt30s", 0, 250, 49),
    ("2026-09-09", "a_lt30s", 1, 4, 2), ("2026-09-09", "a_lt30s", 2, 5, 0),
    ("2026-09-09", "b_30_120s", -3, 143, 50), ("2026-09-09", "b_30_120s", -2, 64, 23),
    ("2026-09-09", "b_30_120s", -1, 165, 83), ("2026-09-09", "b_30_120s", 0, 441, 135),
    ("2026-09-09", "b_30_120s", 1, 2, 1), ("2026-09-09", "b_30_120s", 2, 14, 5),
    ("2026-09-09", "c_2_10min", -3, 166, 56), ("2026-09-09", "c_2_10min", -2, 61, 21),
    ("2026-09-09", "c_2_10min", -1, 103, 47), ("2026-09-09", "c_2_10min", 0, 189, 87),
    ("2026-09-09", "c_2_10min", 1, 1, 1), ("2026-09-09", "c_2_10min", 2, 6, 3),
    ("2026-09-09", "d_gt10min", -3, 162, 7), ("2026-09-09", "d_gt10min", -2, 18, 2),
    ("2026-09-09", "d_gt10min", -1, 58, 5), ("2026-09-09", "d_gt10min", 0, 73, 14),
    ("2026-09-09", "d_gt10min", 2, 2, 1),
    ("2026-09-10", "a_lt30s", -3, 90, 24), ("2026-09-10", "a_lt30s", -2, 25, 11),
    ("2026-09-10", "a_lt30s", -1, 56, 14), ("2026-09-10", "a_lt30s", 0, 266, 45),
    ("2026-09-10", "a_lt30s", 1, 13, 3), ("2026-09-10", "a_lt30s", 2, 28, 5),
    ("2026-09-10", "b_30_120s", -3, 201, 64), ("2026-09-10", "b_30_120s", -2, 71, 26),
    ("2026-09-10", "b_30_120s", -1, 85, 37), ("2026-09-10", "b_30_120s", 0, 278, 54),
    ("2026-09-10", "b_30_120s", 1, 22, 6), ("2026-09-10", "b_30_120s", 2, 34, 9),
    ("2026-09-10", "c_2_10min", -3, 128, 59), ("2026-09-10", "c_2_10min", -2, 57, 27),
    ("2026-09-10", "c_2_10min", -1, 61, 22), ("2026-09-10", "c_2_10min", 0, 97, 28),
    ("2026-09-10", "c_2_10min", 1, 8, 1), ("2026-09-10", "c_2_10min", 2, 13, 3),
    ("2026-09-10", "d_gt10min", -3, 129, 4), ("2026-09-10", "d_gt10min", -2, 26, 5),
    ("2026-09-10", "d_gt10min", -1, 15, 4), ("2026-09-10", "d_gt10min", 0, 32, 7),
    ("2026-09-10", "d_gt10min", 1, 5, 0), ("2026-09-10", "d_gt10min", 2, 2, 0),
]

TRAIN_DAYS = {"2026-09-06", "2026-09-07", "2026-09-08"}
TEST_DAYS = {"2026-09-09", "2026-09-10"}
BANDS = ["a_lt30s", "b_30_120s", "c_2_10min", "d_gt10min"]


def fit(rows):
    """P(fill | band, cents inside touch), pooled over days."""
    num, den = {}, {}
    for _d, band, near, n, k in rows:
        num[(band, near)] = num.get((band, near), 0) + k
        den[(band, near)] = den.get((band, near), 0) + n
    # back off to the band when a cell is thin
    bnum, bden = {}, {}
    for _d, band, _near, n, k in rows:
        bnum[band] = bnum.get(band, 0) + k
        bden[band] = bden.get(band, 0) + n
    return num, den, bnum, bden


def predict(model, band, near, min_n=25):
    num, den, bnum, bden = model
    n = den.get((band, near), 0)
    if n >= min_n:
        return num[(band, near)] / n, "cell"
    if bden.get(band):
        return bnum[band] / bden[band], "band"
    return None, "none"


def main():
    train = [r for r in GRID if r[0] in TRAIN_DAYS]
    test = [r for r in GRID if r[0] in TEST_DAYS]
    model = fit(train)

    print("=" * 74)
    print("RESTING-FILL MODEL, FIT 2026-09-06..08, VALIDATED 09-09..10")
    print("=" * 74)
    print("fit rows %d (%d orders) | test rows %d (%d orders)"
          % (len(train), sum(r[3] for r in train),
             len(test), sum(r[4 - 1] for r in test)))

    print()
    print("FITTED CELL RATES (training only)")
    print("%-12s %s" % ("band", "".join("%9d" % c for c in range(-3, 3))))
    for band in BANDS:
        cells = []
        for c in range(-3, 3):
            p, src = predict(model, band, c)
            cells.append("     -   " if p is None
                         else ("%8.1f%%" % (100 * p)
                               if src == "cell" else "%7.1f%%~" % (100 * p)))
        print("%-12s %s" % (band, "".join(cells)))
    print("  ~ = backed off to the band rate (cell had under 25 training orders)")

    print()
    print("HELD-OUT VALIDATION, 2026-09-09..10")
    print("%-12s %7s %8s %10s %9s %8s" % (
        "band", "orders", "observed", "predicted", "obs rate", "pred"))
    tot_n = tot_k = tot_pred = 0.0
    rows_out = []
    for band in BANDS:
        n = sum(r[3] for r in test if r[1] == band)
        k = sum(r[4] for r in test if r[1] == band)
        pred = 0.0
        for r in test:
            if r[1] != band:
                continue
            p, _ = predict(model, band, r[2])
            pred += (p or 0.0) * r[3]
        tot_n += n; tot_k += k; tot_pred += pred
        print("%-12s %7d %8d %10.1f %8.1f%% %7.1f%%" % (
            band, n, k, pred, 100.0 * k / max(n, 1),
            100.0 * pred / max(n, 1)))
        rows_out.append({"band": band, "orders": n, "observed": k,
                         "predicted": round(pred, 1)})

    # a binomial standard error on the held-out total, so "close" is
    # a statement with a scale rather than an impression
    phat = tot_pred / tot_n
    se = math.sqrt(tot_n * phat * (1 - phat))
    z = (tot_k - tot_pred) / se if se else float("nan")
    print("-" * 58)
    print("%-12s %7d %8d %10.1f %8.1f%% %7.1f%%" % (
        "TOTAL", tot_n, tot_k, tot_pred,
        100.0 * tot_k / tot_n, 100.0 * tot_pred / tot_n))
    print()
    print("held-out error  %+.1f fills on %d orders (%+.2f pp)"
          % (tot_k - tot_pred, tot_n,
             100.0 * (tot_k - tot_pred) / tot_n))
    print("binomial s.e.   %.1f fills -> z = %+.2f" % (se, z))
    if abs(z) < 2:
        print("VERDICT: the forward error is inside two standard errors.")
        print("The model transfers across the day boundary at the")
        print("AGGREGATE level.")
    else:
        print("VERDICT: the forward error EXCEEDS two standard errors.")
        print("The model does NOT transfer across the day boundary; the")
        print("fill rate is drifting and a fitted constant will mislead.")
    print()
    print("WHAT THIS DOES NOT ESTABLISH. It is a REDUCED-FORM model of")
    print("our own mirror-lane orders: exposure and distance in, fill")
    print("probability out. It does NOT validate the replay's MECHANISM")
    print("-- queue position and volume crossing our price -- because")
    print("that needs contemporaneous depth and prints for 2026-09-06..10")
    print("and the BETTOR capture starts 09-13. That gap is reported, not")
    print("filled in.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump({"train_days": sorted(TRAIN_DAYS),
                   "test_days": sorted(TEST_DAYS),
                   "held_out": rows_out,
                   "held_out_orders": tot_n,
                   "held_out_observed": tot_k,
                   "held_out_predicted": round(tot_pred, 1),
                   "binomial_se": round(se, 2),
                   "z": round(z, 3)}, fh, indent=2, sort_keys=True)
    print("\nwritten: %s" % os.path.relpath(OUT))


if __name__ == "__main__":
    main()
