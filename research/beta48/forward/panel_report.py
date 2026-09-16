#!/usr/bin/env python3
"""Apply the Target Size rule to a captured panel and report. Contacts nothing.

Reads a segment's `panel.jsonl(.gz)` and answers, per side and per quote
distance, whether a hypothetical BETTOR quote would be INSIDE the scoring
range — using `depth_panel.py`, whose rule was fixed before any of this data
existed.

WHAT IT DELIBERATELY DOES NOT DO. It does not estimate a reward, a share, a
fill probability or a markout. Those need either a denominator no public
endpoint publishes (every participant's qualifying score through time) or a
time series this file is not given. They are emitted as NOT_IDENTIFIED and are
meant to stay that way until something actually measures them.

Usage:
    python3 panel_report.py PANEL.jsonl[.gz] [PLAN.json]
"""
from __future__ import annotations

import collections
import gzip
import json
import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import depth_panel as P

NOT_IDENTIFIED = "NOT_IDENTIFIED"


def _open(path):
    p = Path(path)
    return (gzip.open(p, "rt") if p.suffix == ".gz" else p.open())


def load(path):
    with _open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def score_rows(rows):
    """One scored record per captured snapshot."""
    out = []
    for r in rows:
        leg = r.get("leg") or {}
        if leg.get("http_status") != 200 or not leg.get("body"):
            out.append({"slug": r.get("slug"), "round": r.get("round"),
                        "UNREADABLE": leg.get("error") or leg.get(
                            "http_status")})
            continue
        tick = r.get("tick")
        target = r.get("TARGET_SIZE")
        row = P.panel_row(leg["body"],
                          D(str(tick)) if tick else D("0.01"),
                          D(str(target)) if target is not None else None,
                          slug=r.get("slug"),
                          captured_at=leg.get("local_request_wall_utc"))
        row["round"] = r.get("round")
        row["PROGRAM_TYPE"] = r.get("PROGRAM_TYPE")
        row["PROGRAM_PERIOD"] = r.get("PROGRAM_PERIOD")
        row["REWARD_POOL"] = r.get("REWARD_POOL")
        row["DISCOUNT_FACTOR"] = r.get("DISCOUNT_FACTOR")
        row["INSTRUMENT_STATE"] = r.get("INSTRUMENT_STATE")
        row["tick"] = tick
        out.append(row)
    return out


def _rate(rows, key):
    """Eligibility across readable side-observations, with the THIRD answer
    shown rather than folded away.

    `YES_ON_VISIBLE_BOOK` means the whole visible book never reached Target
    Size, so a quote at that level qualifies ON THIS SNAPSHOT but hidden or
    later size could still close the range ahead of us. Counting it as a plain
    YES would overstate eligibility; dropping it would shrink the denominator
    silently. It is reported as its own number.
    """
    vals = [r[key] for r in rows if key in r]
    if not vals:
        return NOT_IDENTIFIED
    c = collections.Counter(vals)
    decided = c["YES"] + c["NO"]
    parts = []
    if decided:
        parts.append("YES %d/%d = %.1f%%"
                     % (c["YES"], decided, 100.0 * c["YES"] / decided))
    else:
        parts.append("no decided observations")
    if c["YES_ON_VISIBLE_BOOK"]:
        parts.append("+%d target-not-reached-in-visible-book"
                     % c["YES_ON_VISIBLE_BOOK"])
    if c[NOT_IDENTIFIED]:
        parts.append("+%d NOT_IDENTIFIED" % c[NOT_IDENTIFIED])
    return "  ".join(parts)


def summarize(scored):
    readable = [r for r in scored if "UNREADABLE" not in r]
    out = {
        "PANEL_SNAPSHOTS": len(scored),
        "PANEL_SNAPSHOTS_READABLE": len(readable),
        "PANEL_MARKETS": len({r.get("slug") for r in readable}),
    }
    for side in ("BID", "ASK"):
        for k, label in ((0, "QUOTE_AT_BEST_SCORE_ELIGIBILITY"),
                         (1, "QUOTE_1_TICK_BACK_SCORE_ELIGIBILITY"),
                         (2, "QUOTE_2_TICKS_BACK_SCORE_ELIGIBILITY")):
            out["%s_%s" % (side, label)] = _rate(
                readable, "%s_QUOTE_%d_TICKS_BACK_SCORE_ELIGIBILITY"
                % (side, k))
        reached = [r["%s_TARGET_ALREADY_REACHED_AT_BEST" % side]
                   for r in readable
                   if r.get("%s_TARGET_ALREADY_REACHED_AT_BEST" % side)
                   not in (None, NOT_IDENTIFIED)]
        out["%s_TARGET_ALREADY_REACHED_AT_BEST_FREQUENCY" % side] = (
            "%d/%d = %.1f%%" % (sum(1 for x in reached if x), len(reached),
                                100.0 * sum(1 for x in reached if x)
                                / len(reached))
            if reached else NOT_IDENTIFIED)
        ahead = [r["%s_SIZE_AHEAD_0_TICKS" % side] for r in readable
                 if isinstance(r.get("%s_SIZE_AHEAD_0_TICKS" % side), D)]
        if ahead:
            ahead = sorted(ahead)
            out["%s_SIZE_AHEAD_AT_BEST_MEDIAN" % side] = str(
                ahead[len(ahead) // 2])
            out["%s_SIZE_AHEAD_AT_BEST_MIN" % side] = str(ahead[0])
            out["%s_SIZE_AHEAD_AT_BEST_MAX" % side] = str(ahead[-1])
        else:
            out["%s_SIZE_AHEAD_AT_BEST_MEDIAN" % side] = NOT_IDENTIFIED

    # Structurally unavailable from a book snapshot. Named, not omitted.
    for f in ("ESTIMATED_QUEUE_AHEAD_WITHIN_LEVEL", "TOUCH_RATE",
              "F0", "F1", "F2", "F3_TOUCH_UPPER_BOUND",
              "MARKOUT_5S", "MARKOUT_30S", "MARKOUT_60S", "MARKOUT_5M",
              "ADVERSE_SELECTION_VS_MAKER_BUDGET",
              "PAIR_CONVERSION_OPPORTUNITY", "ONE_SIDED_INVENTORY_DURATION",
              "REWARD_SHARE", "ACTUAL_REWARD"):
        out[f] = NOT_IDENTIFIED
    return out


def distributions(rows):
    d = {}
    for f in ("DISCOUNT_FACTOR", "REWARD_POOL", "PROGRAM_PERIOD",
              "PROGRAM_TYPE", "tick", "INSTRUMENT_STATE"):
        d[f] = collections.Counter(r.get(f) for r in rows).most_common()
    return d


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print(__doc__)
        return 2
    rows = list(load(argv[0]))
    scored = score_rows(rows)
    print("=== INCENTIVE_DEPTH_PANEL (never pooled with BREADTH_CENSUS) ===")
    if len(argv) > 1 and Path(argv[1]).exists():
        plan = json.loads(Path(argv[1]).read_text())
        for k in ("DATASET", "SAMPLING_FROZEN_BEFORE_ECONOMICS",
                  "SELECTION_WITHIN_STRATUM", "strata", "markets_picked",
                  "rounds", "per_stratum"):
            print("%-42s %s" % (k, plan.get(k)))
    for k, v in summarize(scored).items():
        print("%-42s %s" % (k, v))
    print("\n--- decision-time strata actually captured ---")
    for k, v in distributions(rows).items():
        print("%-20s %s" % (k, v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
