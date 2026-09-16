"""pnl_by_open_band must reconcile to merge_pnl.replay's OWN totals.

The merge-only split was accepted at a $0.00 residual against
`realized_merge_pnl`. This split has a strictly harder target: summed
over bands it must equal `lot_s` and `lot_p` — every closed lot the
estimator books, through all three channels — because its whole purpose
is to stop a band's MERGE economics standing in for that band's TOTAL
economics.

The controlling test is `test_a_band_whose_merges_win_can_still_lose`:
it builds the exact shape the cross-account result is exposed to — cheap
legs that pair profitably, alongside cheap legs that never pair and
settle at zero — and asserts the band's MERGE_ROI is positive while its
TOTAL_ROI is negative. If that case could not be represented, the whole
measurement would be incapable of finding the bias it exists to find.

Run: python -m pytest research/beta48/test_three_channel_bands.py -q
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "backend"))

from whale_reconstruct import merge_pnl_by_open_band, pnl_by_open_band  # noqa: E402
from sportsassets.analytics import merge_pnl as M  # noqa: E402

CENT = 0.01


def fill(t, cid, idx, side, size, price, tx=None):
    return {"t": t, "condition_id": cid, "outcome_index": idx, "side": side,
            "size": float(size), "price": float(price), "tx": tx or f"tx{t}"}


def bands_only(split):
    return {k: v for k, v in split.items() if not k.startswith("_")}


# --------------------------------------------------------------------
# Reconciliation against the estimator itself
# --------------------------------------------------------------------

def test_the_three_channels_sum_to_the_estimators_closed_lot_totals():
    fills = [
        fill(1, "A", 0, "BUY", 100, 0.05),
        fill(2, "A", 1, "BUY", 100, 0.90),      # merge
        fill(3, "B", 0, "BUY", 200, 0.40),
        fill(4, "B", 0, "SELL", 200, 0.55),     # sell
        fill(5, "C", 0, "BUY", 300, 0.20),      # settles, never closed
        fill(6, "D", 1, "BUY", 50, 0.75),       # settles a loser
    ]
    pay = {"C": [1.0, 0.0], "D": [1.0, 0.0]}
    r = M.replay(fills, payouts=pay)
    split = pnl_by_open_band(fills, pay)
    assert abs(split["_TOTAL_STAKE"] - r["lot_s"]) < CENT, (
        split["_TOTAL_STAKE"], r["lot_s"])
    assert abs(split["_TOTAL_PNL"] - r["lot_p"]) < CENT, (
        split["_TOTAL_PNL"], r["lot_p"])


def test_the_merge_channel_still_equals_realized_merge_pnl():
    """The new split must not disturb the accepted merge attribution."""
    fills = [
        fill(1, "A", 0, "BUY", 100, 0.05),
        fill(2, "A", 1, "BUY", 100, 0.90),
        fill(3, "B", 0, "BUY", 200, 0.40),
        fill(4, "B", 1, "BUY", 120, 0.55),
        fill(5, "B", 0, "SELL", 80, 0.42),
    ]
    pay = {}
    r = M.replay(fills, payouts=pay)
    split = bands_only(pnl_by_open_band(fills, pay))
    got = round(sum(v["MERGE_PNL"] for v in split.values()), 2)
    assert abs(got - r["realized_merge_pnl"]) < CENT, (
        got, r["realized_merge_pnl"])


def test_it_agrees_band_for_band_with_the_accepted_merge_split():
    """Same bucketing rule, so the merge numbers must match exactly."""
    fills = [
        fill(1, "A", 0, "BUY", 40, 0.05),
        fill(2, "A", 0, "BUY", 60, 0.07),
        fill(3, "A", 1, "BUY", 100, 0.91),
        fill(4, "B", 0, "BUY", 500, 0.62),
        fill(5, "B", 1, "BUY", 500, 0.35),
    ]
    pay = {}
    old = bands_only(merge_pnl_by_open_band(fills))
    new = bands_only(pnl_by_open_band(fills, pay))
    for band, o in old.items():
        assert abs(new[band]["MERGE_PNL"] - o["MATCHED_PNL"]) < CENT, band
        assert abs(new[band]["MERGE_STAKE"] - o["MATCHED_STAKE"]) < CENT, band
        assert new[band]["merges"] == o["merges"], band


# --------------------------------------------------------------------
# The bias this exists to detect
# --------------------------------------------------------------------

def test_a_band_whose_merges_win_can_still_lose():
    """The survivorship case, stated as arithmetic.

    One cheap leg pairs at a good basis and books a merge profit. Four
    identical cheap legs never pair and settle worthless. The band's
    MERGE_ROI is strongly positive; its TOTAL_ROI is negative. Reading
    the merge split alone would call this band profitable.
    """
    fills = [fill(1, "P", 0, "BUY", 100, 0.05),
             fill(2, "P", 1, "BUY", 100, 0.88)]          # pairs, wins
    pay = {}
    for i, cid in enumerate(("L1", "L2", "L3", "L4"), start=3):
        fills.append(fill(i, cid, 0, "BUY", 100, 0.05))   # never pairs
        pay[cid] = [0.0, 1.0]                             # settles at zero
    split = bands_only(pnl_by_open_band(fills, pay))
    cheap = split["0.00-0.10"]
    assert cheap["MERGE_ROI"] > 0, cheap
    assert cheap["SETTLED_PNL"] < 0, cheap
    assert cheap["TOTAL_ROI"] < 0, cheap
    # and the merge-only view really would have said otherwise
    old = bands_only(merge_pnl_by_open_band(fills))["0.00-0.10"]
    assert old["MATCHED_ROI"] > 0


def test_an_unresolved_balance_is_reported_not_counted_as_a_loss():
    fills = [fill(1, "U", 0, "BUY", 100, 0.25)]
    split = bands_only(pnl_by_open_band(fills, {}))       # no payout known
    b = split["0.10-0.30"]
    assert b["UNGRADED_STAKE"] == 25.0
    assert b["UNGRADED_SHARES"] == 100.0
    assert b["SETTLED_PNL"] == 0.0
    assert b["TOTAL_STAKE"] == 0.0, "an ungraded balance is not a closed lot"


def test_a_settled_winner_books_to_the_band_it_OPENED_in():
    """Not the band its payout implies — the bucket is decision-time."""
    fills = [fill(1, "W", 0, "BUY", 100, 0.05)]
    split = bands_only(pnl_by_open_band(fills, {"W": [1.0, 0.0]}))
    assert "0.00-0.10" in split
    assert abs(split["0.00-0.10"]["SETTLED_PNL"] - 95.0) < CENT
    assert "0.90-1.01" not in split


def test_a_leg_reopened_after_going_flat_takes_its_NEW_opening_band():
    fills = [
        fill(1, "R", 0, "BUY", 100, 0.05),
        fill(2, "R", 0, "SELL", 100, 0.06),     # flat again
        fill(3, "R", 0, "BUY", 100, 0.80),      # reopened much higher
    ]
    split = bands_only(pnl_by_open_band(fills, {"R": [1.0, 0.0]}))
    assert abs(split["0.00-0.10"]["SELL_PNL"] - 1.0) < CENT
    assert split["0.70-0.90"]["settled_lots"] == 1
    assert abs(split["0.70-0.90"]["SETTLED_PNL"] - 20.0) < CENT


def test_sell_pnl_over_all_bands_equals_realized_sell_pnl():
    fills = [
        fill(1, "S", 0, "BUY", 100, 0.30),
        fill(2, "S", 0, "SELL", 60, 0.45),
        fill(3, "T", 1, "BUY", 200, 0.80),
        fill(4, "T", 1, "SELL", 200, 0.70),
    ]
    r = M.replay(fills, payouts={})
    split = bands_only(pnl_by_open_band(fills, {}))
    got = round(sum(v["SELL_PNL"] for v in split.values()), 2)
    assert abs(got - r["realized_sell_pnl"]) < CENT, (got, r["realized_sell_pnl"])


def test_merge_pnl_is_still_byte_identical_to_HEAD():
    """The estimator is the reference; nothing here may edit it."""
    out = subprocess.run(
        ["git", "diff", "--", "backend/sportsassets/analytics/merge_pnl.py"],
        cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "", out.stdout
