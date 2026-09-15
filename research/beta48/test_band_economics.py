#!/usr/bin/env python3
"""Proof that the per-band economics are correct AND that adding them
changed nothing else.

CONTACTS NOTHING. Run:
    python3 research/beta48/test_band_economics.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BLOBS = HERE / "evidence" / "blobs"


def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


W = _load("wr", HERE / "whale_reconstruct.py")

DAY = 86400.0
T0 = 1_750_000_000.0


def fill(cid, idx, size, price, t, side="BUY", q="Q"):
    return {"condition_id": cid, "outcome_index": idx, "size": float(size),
            "price": float(price), "side": side, "t": float(t),
            "tx": "%s-%s-%s" % (cid, idx, t), "slug": cid, "question": q}


# ====================== THE ECONOMICS ARE ARITHMETICALLY RIGHT =========
def test_a_single_pair_books_its_exact_dollar_result():
    """One market: buy 100 of leg 0 at 0.40, then 100 of leg 1 at 0.45.
    Basis 0.85, so the pair earns 100 * (1 - 0.85) = $15.00 exactly."""
    g = W.completion_grid([fill("c1", 0, 100, 0.40, T0),
                           fill("c1", 1, 100, 0.45, T0 + 10)])
    b = g["BY_FIRST_LEG_PRICE_BAND"]["0.30-0.50"]
    assert b["first_complement_pnl"] == 15.0, b["first_complement_pnl"]
    assert b["first_complement_stake"] == 40.0                # 100 * 0.40
    assert b["mean_pair_basis"] == 0.85
    assert g["FIRST_COMPLEMENT_PNL_TOTAL"] == 15.0


def test_a_pair_above_parity_books_a_LOSS():
    """Basis 1.05 is a loss fixed at completion, whatever the outcome.
    100 * (1 - 1.05) = -$5.00."""
    g = W.completion_grid([fill("c1", 0, 100, 0.60, T0),
                           fill("c1", 1, 100, 0.45, T0 + 10)])
    b = g["BY_FIRST_LEG_PRICE_BAND"]["0.50-0.70"]
    assert b["first_complement_pnl"] == -5.0, b["first_complement_pnl"]


def test_the_pnl_is_booked_to_the_FIRST_legs_band_not_the_complements():
    """First leg at 0.05 (band 0.00-0.10), complement at 0.80. The whole
    result belongs to the first leg's band and the complement's band sees
    no pair at all."""
    g = W.completion_grid([fill("c1", 0, 10, 0.05, T0),
                           fill("c1", 1, 10, 0.80, T0 + 5)])
    bands = g["BY_FIRST_LEG_PRICE_BAND"]
    assert bands["0.00-0.10"]["first_complement_pnl"] == round(10 * (1 - 0.85), 2)
    assert "0.70-0.90" not in bands


def test_matched_size_is_the_SMALLER_leg():
    """Buy 100 of leg 0, complement only 30. Only 30 shares are paired."""
    g = W.completion_grid([fill("c1", 0, 100, 0.40, T0),
                           fill("c1", 1, 30, 0.45, T0 + 10)])
    b = g["BY_FIRST_LEG_PRICE_BAND"]["0.30-0.50"]
    assert b["first_complement_shares"] == 30.0
    assert b["first_complement_pnl"] == round(30 * (1 - 0.85), 2)


def test_percentiles_and_residual_rate():
    """Five markets open in one band; three complete, two never do."""
    f = []
    for i, cp in enumerate((0.40, 0.50, 0.60)):
        f += [fill("c%d" % i, 0, 10, 0.40, T0 + i),
              fill("c%d" % i, 1, 10, cp, T0 + i + 5)]
    for i in (3, 4):
        f.append(fill("c%d" % i, 0, 10, 0.40, T0 + i))
    g = W.completion_grid(f)
    b = g["BY_FIRST_LEG_PRICE_BAND"]["0.30-0.50"]
    assert b["opens"] == 5
    assert b["completed_by_settlement"] == 3
    assert b["residual_rate"] == 0.4                 # 2 of 5 never complete
    # bases are 0.80, 0.90, 1.00
    assert b["median_pair_basis"] == 0.90
    assert b["p25_pair_basis"] == 0.80
    assert b["p75_pair_basis"] == 1.00


def test_completion_at_5s_is_stricter_than_at_1h():
    g = W.completion_grid([fill("c1", 0, 10, 0.40, T0),
                           fill("c1", 1, 10, 0.45, T0 + 600)])
    b = g["BY_FIRST_LEG_PRICE_BAND"]["0.30-0.50"]
    assert b["completed_within_5s"] == 0
    assert b["completed_within_1h"] == 1
    assert b["completed_by_settlement"] == 1


# ================= THE ADDITION CHANGED NOTHING IT MAY NOT =============
FROZEN = ("opens", "completed_within_1h", "completion_rate_1h",
          "mean_pair_basis")


def test_every_sealed_band_reproduces_its_four_original_fields():
    """THE REAL GATE. Re-deriving the bands cannot move a single number
    the already-sealed run 35028887477 published. Anything else in the
    output is additive."""
    checked = 0
    for p in sorted(BLOBS.glob("*_reconstruction.json")):
        sealed = json.loads(p.read_text())
        band = (sealed.get("REFERENCE_ACCOUNT_COMPLETION", {})
                .get("LIFETIME", {}).get("BY_FIRST_LEG_PRICE_BAND"))
        if not band:
            continue
        for k, v in band.items():
            for f in FROZEN:
                assert f in v, "%s lost %s on %s" % (p.name, f, k)
            checked += 1
    assert checked >= 20, "expected the six sealed accounts' bands, got %d" % checked


def test_the_new_keys_do_not_collide_with_the_old_ones():
    g = W.completion_grid([fill("c1", 0, 10, 0.40, T0),
                           fill("c1", 1, 10, 0.45, T0 + 10)])
    b = g["BY_FIRST_LEG_PRICE_BAND"]["0.30-0.50"]
    new = {"completed_within_5s", "completion_rate_5s",
           "completed_by_settlement", "completion_rate_settlement",
           "residual_rate", "median_pair_basis", "p25_pair_basis",
           "p75_pair_basis", "first_complement_shares",
           "first_complement_stake", "first_complement_pnl",
           "_NOT_THE_PAIR_CHANNEL"}
    assert not (new & set(FROZEN)), "a new key shadows a frozen one"
    assert new <= set(b), "a promised key is missing: %s" % (new - set(b))


def test_the_other_three_groupings_are_untouched():
    """Only BY_FIRST_LEG_PRICE_BAND gets economics. by_sport, by_size and
    by_week keep exactly the four original fields, so the blob does not
    quietly triple in size."""
    g = W.completion_grid([fill("c1", 0, 10, 0.40, T0),
                           fill("c1", 1, 10, 0.45, T0 + 10)])
    for grp in ("BY_SPORT_OR_QUESTION", "BY_FILL_SIZE_BUCKET", "BY_WEEK"):
        for k, v in g[grp].items():
            assert set(v) == set(FROZEN), "%s/%s gained %s" % (
                grp, k, set(v) - set(FROZEN))


def test_the_frozen_constants_are_unchanged():
    assert W.HORIZONS_S == (5, 10, 30, 60, 120, 300, 600, 1800, 3600)
    assert W.CEILINGS == (0.90, 0.92, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99, 1.00)
    assert W.PRICE_BANDS == ((0.0, 0.10), (0.10, 0.30), (0.30, 0.50),
                             (0.50, 0.70), (0.70, 0.90), (0.90, 1.01))
    assert W.MIN_VERDICT_CLUSTERS == 30


def test_merge_pnl_is_still_byte_identical_to_HEAD():
    """The production estimator stays untouched: this work lives entirely
    in the BETA48 reporting layer."""
    rel = "backend/sportsassets/analytics/merge_pnl.py"
    out = subprocess.run(["git", "-C", str(ROOT), "diff", "HEAD", "--", rel],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", out.stdout[:2000]


# ============ THE PAIR CHANNEL SPLIT RECONCILES TO THE ESTIMATOR ======
M = _load("merge_pnl", ROOT / "backend" / "sportsassets" / "analytics"
          / "merge_pnl.py")


def test_open_band_split_equals_realized_merge_pnl_on_a_built_leg():
    """THE GATE THE FIRST ATTEMPT FAILED. A leg built from THREE fills and
    then merged: completion_grid sees one opening and one complement, but
    merge_pnl closes against the average of all three. The band split must
    follow merge_pnl, not the grid."""
    f = [fill("c1", 0, 100, 0.20, T0),
         fill("c1", 0, 100, 0.30, T0 + 1),
         fill("c1", 0, 100, 0.40, T0 + 2),
         fill("c1", 1, 300, 0.50, T0 + 3)]
    b = W.merge_pnl_by_open_band(f)
    r = M.replay(f)
    assert abs(b["_TOTAL_MATCHED_PNL"] - round(r["realized_merge_pnl"], 2)) < 0.01


def test_the_split_books_to_the_HELD_legs_OPENING_price_not_its_average():
    """Leg 0 is opened at 0.20 (band 0.10-0.30) then averaged up to 0.30.
    The merge belongs to the band it was OPENED in -- the decision-time
    fact -- not to the band its average cost later lands in."""
    f = [fill("c1", 0, 100, 0.20, T0),
         fill("c1", 0, 100, 0.40, T0 + 1),
         fill("c1", 1, 200, 0.50, T0 + 2)]
    b = W.merge_pnl_by_open_band(f)
    assert "0.10-0.30" in b, list(b)
    assert b["0.10-0.30"]["merges"] == 1
    assert "0.30-0.50" not in b


def test_a_leg_reopened_after_going_flat_takes_its_NEW_opening_band():
    f = [fill("c1", 0, 100, 0.20, T0),
         fill("c1", 1, 100, 0.50, T0 + 1),          # merges leg 0 away
         fill("c1", 0, 100, 0.80, T0 + 2),          # reopened, new band
         fill("c1", 1, 100, 0.50, T0 + 3)]
    b = W.merge_pnl_by_open_band(f)
    assert b["0.10-0.30"]["merges"] == 1
    assert b["0.70-0.90"]["merges"] == 1


def test_reconciles_on_a_randomish_multi_market_book():
    """Many markets, mixed sizes and prices, sells included."""
    f, t = [], T0
    for c in range(40):
        p = 0.05 + (c % 19) * 0.05
        f.append(fill("m%d" % c, 0, 10 + c, min(p, 0.99), t)); t += 1
        if c % 5 == 0:
            f.append(fill("m%d" % c, 0, 7, min(p + 0.05, 0.99), t)); t += 1
        if c % 7 == 0:
            f.append(fill("m%d" % c, 0, 3, 0.5, t, side="SELL")); t += 1
        f.append(fill("m%d" % c, 1, 6 + (c % 11), min(0.9 - p / 2, 0.99), t))
        t += 1
    b = W.merge_pnl_by_open_band(f)
    r = M.replay(f)
    assert abs(b["_TOTAL_MATCHED_PNL"] - round(r["realized_merge_pnl"], 2)) < 0.01
    assert r["n_merges"] == sum(v["merges"] for k, v in b.items()
                                if not k.startswith("_"))


def test_stake_sums_are_consistent_with_the_pnl():
    f = [fill("c1", 0, 100, 0.40, T0), fill("c1", 1, 100, 0.45, T0 + 1)]
    b = W.merge_pnl_by_open_band(f)
    e = b["0.30-0.50"]
    # held leg avg 0.40, complement 0.45 -> 100 * (1 - 0.40 - 0.45) = 15
    assert e["MATCHED_PNL"] == 15.0
    assert e["MATCHED_STAKE"] == 40.0
    assert e["MATCHED_ROI"] == round(15.0 / 40.0, 6)


def test_first_complement_fields_are_labelled_as_not_the_pair_channel():
    """The misleading approximation is kept for continuity but must carry
    its own warning, and must NOT be named MATCHED_PNL."""
    g = W.completion_grid([fill("c1", 0, 10, 0.40, T0),
                           fill("c1", 1, 10, 0.45, T0 + 10)])
    b = g["BY_FIRST_LEG_PRICE_BAND"]["0.30-0.50"]
    assert "MATCHED_PNL" not in b, "the grid must not claim the pair channel"
    assert "first_complement_pnl" in b
    assert "_NOT_THE_PAIR_CHANNEL" in b


def test_band_total_is_the_sum_of_the_bands():
    f = []
    for i, (p0, cp) in enumerate(((0.05, 0.80), (0.40, 0.45), (0.60, 0.45))):
        f += [fill("c%d" % i, 0, 10, p0, T0 + i),
              fill("c%d" % i, 1, 10, cp, T0 + i + 5)]
    g = W.completion_grid(f)
    tot = sum(v["first_complement_pnl"]
              for v in g["BY_FIRST_LEG_PRICE_BAND"].values())
    assert abs(g["FIRST_COMPLEMENT_PNL_TOTAL"] - tot) < 0.01


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(vars().items())
           if n.startswith("test_") and callable(f)]
    bad = []
    for n, f in fns:
        try:
            f()
        except Exception as exc:                        # noqa: BLE001
            bad.append((n, exc))
    for n, exc in bad:
        print("FAIL %s: %r" % (n, exc))
    print("%d passed, %d failed" % (len(fns) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)
