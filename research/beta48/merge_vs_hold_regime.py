#!/usr/bin/env python3
"""BETA48 — MERGE_VS_HOLD_IS_REGIME_DEPENDENT, quantified.

READ ONLY, OFFLINE.

THE OBSERVATION TO EXPLAIN. Running merge_pnl.replay on RN1's raw fills
over nested windows, the counterfactual "what if he had HELD instead of
buying the complement" flips sign:

    sample 37d   holding better by  +$31,797
    last  30d    holding better by  +$30,267
    last  14d    MERGING better by   $82,769
    last   7d    MERGING better by   $80,382

NESTED WINDOWS CANNOT ANSWER WHY. 7d is inside 14d is inside 30d, so the
"reversal" could be one late week dominating a sum. This file therefore
re-cuts the sample into DISJOINT calendar slices and compares them
directly, which is the only way the question is answerable.

WHAT IS COMPARED, and what each thing is allowed to be used for:

  contemporaneous at the decision instant -- usable later as a feature
    sport, first-leg price band, complement timing, pair basis,
    fill size, market count, opens per day

  known only afterwards -- used HERE to describe a realised difference,
    and explicitly NOT proposed as a feature
    settlement outcome, hold-vs-merge dollars

The distinction is enforced by reporting them in separate blocks.
"""

from __future__ import annotations

import importlib.util
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = HERE / "evidence"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _load("merge_pnl",
          ROOT / "backend" / "sportsassets" / "analytics" / "merge_pnl.py")
W = _load("wr", HERE / "whale_reconstruct.py")


def slice_by_days(fills, lo_days, hi_days):
    """Fills in [end - hi_days, end - lo_days). DISJOINT by construction."""
    end = max(f["t"] for f in fills)
    lo_t = end - lo_days * 86400
    hi_t = end - hi_days * 86400
    return [f for f in fills if hi_t <= f["t"] < lo_t]


def describe(fills, pay):
    """Everything about a slice, decision-time facts first."""
    if not fills:
        return None
    r = M.replay(fills, payouts=pay)
    by_cond = defaultdict(list)
    for f in fills:
        by_cond[f["condition_id"]].append(f)

    opens = 0
    basis, basis_n = 0.0, 0
    dt_sum, dt_n = 0.0, 0
    band = Counter()
    sport = Counter()
    never = 0
    for cid, rows in by_cond.items():
        bal = [0.0, 0.0]
        for i, f in enumerate(rows):
            idx, other = f["outcome_index"], 1 - f["outcome_index"]
            if f["side"] == "SELL":
                bal[idx] = max(0.0, bal[idx] - f["size"])
                continue
            m = min(f["size"], bal[other])
            entry = f["size"] - m
            bal[other] -= m
            opening = entry > 0 and bal[other] <= 1e-9 and bal[idx] <= 1e-9
            bal[idx] += entry
            if not opening:
                continue
            opens += 1
            p0 = f["price"]
            band["%.2f" % (min(int(p0 * 10), 9) / 10.0)] += 1
            sport[(f["question"] or "NOT_IDENTIFIED")[:20]] += 1
            comp = next((g for g in rows[i + 1:]
                         if g["outcome_index"] == other
                         and g["side"] == "BUY"), None)
            if comp is None:
                never += 1
                continue
            basis += p0 + comp["price"]
            basis_n += 1
            dt_sum += comp["t"] - f["t"]
            dt_n += 1

    hold = r.get("cf_hold_on_graded") or 0.0
    act = r.get("cf_actual_on_graded") or 0.0
    return {
        "_DECISION_TIME_FACTS": {
            "fills": len(fills),
            "distinct_markets": len(by_cond),
            "first_side_acquisitions": opens,
            "never_completed": never,
            "never_completed_share": round(never / opens, 4) if opens else None,
            "mean_pair_basis": round(basis / basis_n, 5) if basis_n else None,
            "mean_complement_seconds": round(dt_sum / dt_n, 1) if dt_n else None,
            "first_leg_price_decile_mix": {k: round(v / opens, 4)
                                           for k, v in sorted(band.items())}
            if opens else {},
            "top_sports": dict(sorted(sport.items(),
                                      key=lambda kv: -kv[1])[:6]),
            "entry_notional": round(r.get("entry_notional") or 0, 2),
            "n_merges": r.get("n_merges"),
        },
        "_KNOWN_ONLY_AFTERWARDS": {
            "merge_pnl": round(r.get("realized_merge_pnl") or 0, 2),
            "edge_roi": r.get("edge_roi"),
            "edge_ci95": r.get("edge_ci95"),
            "edge_verdict": r.get("edge_verdict"),
            "cf_hold_on_graded": round(hold, 2),
            "cf_actual_on_graded": round(act, 2),
            "HOLD_MINUS_MERGE": round(hold - act, 2),
            "MERGING_WAS_BETTER": bool(act > hold),
            "cf_coverage": r.get("cf_coverage"),
            "settled_lots": r.get("settled_lots"),
        },
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    fills, pay, why = W.load_u2()
    end = max(f["t"] for f in fills)
    print("sample end %s  fills %d"
          % (datetime.fromtimestamp(end, timezone.utc).isoformat(), len(fills)),
          flush=True)

    # DISJOINT slices. 0-7 is the newest week, 7-14 the one before it,
    # 14-30 the fortnight before that, 30+ everything older.
    slices = {"D0_7": (0, 7), "D7_14": (7, 14),
              "D14_30": (14, 30), "D30_PLUS": (30, 400)}
    out = {}
    for name, (lo, hi) in slices.items():
        sub = slice_by_days(fills, lo, hi)
        d = describe(sub, pay)
        if d is None:
            continue
        out[name] = d
        a, b = d["_DECISION_TIME_FACTS"], d["_KNOWN_ONLY_AFTERWARDS"]
        print("\n=== %s  (%d fills, %d markets) ===" % (
            name, a["fills"], a["distinct_markets"]), flush=True)
        print("  opens %-6d never_completed %-6d (%s)  mean_basis %-8s "
              "mean_complement_s %s"
              % (a["first_side_acquisitions"], a["never_completed"],
                 a["never_completed_share"], a["mean_pair_basis"],
                 a["mean_complement_seconds"]), flush=True)
        print("  entry_notional $%-12s merges %-6s merge_pnl $%-11s"
              % (a["entry_notional"], a["n_merges"], b["merge_pnl"]),
              flush=True)
        print("  HOLD_MINUS_MERGE $%-11s  MERGING_BETTER=%s  cf_coverage %s"
              % (b["HOLD_MINUS_MERGE"], b["MERGING_WAS_BETTER"],
                 b["cf_coverage"]), flush=True)
        print("  top sports %s" % a["top_sports"], flush=True)

    (OUT / "merge_vs_hold_regime.json").write_text(
        json.dumps(out, indent=1, default=str))
    print("\nsealed -> research/beta48/evidence/merge_vs_hold_regime.json",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
