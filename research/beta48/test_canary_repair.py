#!/usr/bin/env python3
"""Proof that the BETA48 canary repair works AND that it changed nothing
it was not allowed to change.

CONTACTS NOTHING. Run:
    python3 research/beta48/test_canary_repair.py
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


W = _load("wr", HERE / "whale_reconstruct.py")
M = _load("merge_pnl", HERE.parent.parent / "backend" / "sportsassets"
          / "analytics" / "merge_pnl.py")

DAY = 86400.0
NOW = datetime(2026, 9, 15, 22, 0, 0, tzinfo=timezone.utc).timestamp()


def fills_ending(last_t, n=40, step=DAY):
    """n fills, one per `step`, ending at last_t."""
    out = []
    for i in range(n):
        t = last_t - (n - 1 - i) * step
        out.append({"condition_id": "c%d" % i, "outcome_index": i % 2,
                    "size": 10.0, "price": 0.5, "side": "BUY", "t": t,
                    "tx": "t%d" % i, "slug": "s", "question": "q"})
    return out


# ============================== DEFECT 2: window anchoring ==============
def test_window_anchors_to_as_of_not_the_accounts_last_fill():
    """THE CANARY. kch123's pull ended 2026-06-29; anchored to its own
    last fill, 'LAST_7D' meant late June while RN1's meant September."""
    stale_last = NOW - 78 * DAY          # like kch123
    fills = fills_ending(stale_last)
    assert W.window(fills, 7, as_of=NOW) == [], (
        "an account dormant for 78 days must have an EMPTY last-7-days "
        "window, not its own final week")
    self_anchored = W.window(fills, 7)   # the old behaviour
    assert len(self_anchored) > 0, "sanity: the old anchor was non-empty"


def test_window_is_the_same_calendar_span_for_every_account():
    a = fills_ending(NOW - 1 * DAY)
    b = fills_ending(NOW - 78 * DAY)
    cut = NOW - 30 * DAY
    for name, f in (("recent", a), ("dormant", b)):
        w = W.window(f, 30, as_of=NOW)
        assert all(x["t"] >= cut for x in w), name
        assert all(x["t"] < cut for x in f if x not in w), name


def test_lifetime_is_untouched_by_the_anchor():
    """LIFETIME takes days=None and must ignore as_of entirely."""
    f = fills_ending(NOW - 78 * DAY)
    assert W.window(f, None, as_of=NOW) == f
    assert W.window(f, None) == f


# ============================== DEFECT 1: verdict at small n ============
def _replay_with_clusters(k):
    """A replay result carrying exactly k clusters."""
    f = []
    for i in range(k):
        f.append({"condition_id": "c%d" % i, "outcome_index": 0, "size": 10.0,
                  "price": 0.4, "side": "BUY", "t": NOW - 100 + i,
                  "tx": "a%d" % i, "slug": "", "question": ""})
        f.append({"condition_id": "c%d" % i, "outcome_index": 1, "size": 10.0,
                  "price": 0.4, "side": "BUY", "t": NOW - 50 + i,
                  "tx": "b%d" % i, "slug": "", "question": ""})
    return M.replay(f)


def test_a_verdict_below_the_cluster_floor_is_suppressed():
    r = _replay_with_clusters(5)
    assert (r.get("edge_clusters") or 0) < W.MIN_VERDICT_CLUSTERS
    raw = r.get("edge_verdict")
    g = W.gate_verdict(dict(r))
    assert g["edge_verdict"].startswith(W.INSUFFICIENT), g["edge_verdict"]
    assert g["edge_verdict_raw"] == raw, "the estimator's own words are kept"
    assert "must not be quoted" in g["edge_verdict"]


def test_a_verdict_above_the_floor_is_passed_through_unchanged():
    r = _replay_with_clusters(60)
    assert (r.get("edge_clusters") or 0) >= W.MIN_VERDICT_CLUSTERS
    g = W.gate_verdict(dict(r))
    assert g["edge_verdict"] == r["edge_verdict"]
    assert "edge_verdict_raw" not in g


def test_the_floor_is_the_repos_own_number():
    p = _load("proof", HERE.parent.parent / "backend" / "sportsassets"
              / "analytics" / "proof.py")
    assert W.MIN_VERDICT_CLUSTERS == p.MIN_PROOF_CLUSTERS == 30, (
        "the gate must use proof.py's existing threshold, not a new one")


# ====================== THE REPAIR CHANGED NOTHING ELSE =================
def test_gating_does_not_touch_the_estimator_or_the_economics():
    """Directive: do not alter estimator definitions, pair accounting,
    filters, horizons or thresholds. Every economic field must survive
    gating byte-identical."""
    r = _replay_with_clusters(5)
    before = {k: v for k, v in r.items() if k not in ("rows", "clus")}
    g = W.gate_verdict(dict(r))
    for k, v in before.items():
        if k == "edge_verdict":
            continue
        assert g[k] == v, ("gating altered %s" % k)
    for k in ("edge_roi", "edge_ci95", "edge_se", "realized_merge_pnl",
              "entry_notional", "n_merges", "n_entries", "edge_clusters",
              "edge_lots", "edge_deployed"):
        assert g.get(k) == r.get(k), k


def test_the_frozen_constants_are_unchanged():
    """Horizons, ceilings, price bands and size buckets are the
    directive's, and the repair may not touch them."""
    assert W.HORIZONS_S == (5, 10, 30, 60, 120, 300, 600, 1800, 3600)
    assert W.CEILINGS == (0.90, 0.92, 0.94, 0.95, 0.96, 0.97, 0.98,
                          0.99, 1.00)
    assert W.PRICE_BANDS == ((0.0, 0.10), (0.10, 0.30), (0.30, 0.50),
                             (0.50, 0.70), (0.70, 0.90), (0.90, 1.01))
    assert W.SIZE_BUCKETS == ((0, 100), (100, 1000), (1000, 10000),
                              (10000, 100000), (100000, float("inf")))


def test_merge_pnl_itself_was_not_edited():
    """The production estimator stays BYTE-IDENTICAL to what is committed:
    the repair lives entirely in the BETA48 reporting layer.

    Checked against git rather than by grepping for words. An earlier
    version of this test grepped for "INSUFFICIENT" and failed -- not
    because the file had been edited, but because merge_pnl ALREADY uses
    that word for its own floor ("fewer than two closed lots",
    merge_pnl.py:79). That pre-existing floor is exactly the gap: two
    lots is not thirty clusters, which is why kch123's five-lot window
    still came back "PROFITABLE at 95%".
    """
    import subprocess
    root = HERE.parent.parent
    rel = "backend/sportsassets/analytics/merge_pnl.py"
    out = subprocess.run(["git", "-C", str(root), "diff", "HEAD", "--", rel],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", (
        "merge_pnl.py differs from HEAD:\n" + out.stdout[:2000])


def test_the_preexisting_floor_is_weaker_than_the_new_one():
    """Names the gap the canary found, so it cannot be re-closed by
    accident and then forgotten."""
    r = _replay_with_clusters(5)
    assert (r.get("edge_lots") or 0) >= 2, (
        "merge_pnl's own floor only fires below TWO closed lots, so a "
        "five-cluster window sails past it")
    assert not (r.get("edge_verdict") or "").startswith("INSUFFICIENT"), (
        "the pre-existing floor did not catch this window -- which is "
        "why the BETA48 gate exists")


def test_completion_grid_is_unaffected_by_the_verdict_gate():
    """The grid never reads edge_verdict, so gating cannot move a
    completion rate."""
    src = (HERE / "whale_reconstruct.py").read_text()
    body = src.split("def completion_grid(")[1].split("\ndef ")[0]
    assert "edge_verdict" not in body
    assert "gate_verdict" not in body


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
