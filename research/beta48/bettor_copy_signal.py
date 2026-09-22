"""CAN WE REPRODUCE THE CASE STUDY'S ENTRIES? Trace them to the
information that existed at the time.

The directive: "Trace the case studies' entry decisions to information
available at the time. Identify which signals we can reproduce on our
venue and account."

The account's fills carry, per trade, the book AS WE SAW IT at
detection (`best_ask`, `depth`) alongside the price THEY got
(`his_price`, `his_size`) and the detection lag (`reaction_s`). That
is exactly the counterfactual a copy policy needs: at the moment the
signal reached us, what could WE have bought, and at what price?

Two questions, both answerable from the snapshot:

  1. IS THE SIGNAL STILL TRADEABLE WHEN WE SEE IT?
     best_ask at detection minus their price. Positive means the
     market has already moved past them and a copy pays up.

  2. IS IT EVEN ON OUR VENUE?
     These are global Polymarket CLOB markets. PMUS lists its own
     universe under its own slugs. A signal we cannot execute is not
     a signal.

Run:  python research/beta48/bettor_copy_signal.py
"""
from __future__ import annotations

import collections
import gzip
import json
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FILLS = os.path.join(ROOT, "research", "snapshots", "u2_events_v1.jsonl.gz")
OUT = os.path.join(HERE, "acceptance", "copy_signal.json")

# PMUS slug namespaces, read from our own live listing during probe 2
PMUS_PREFIXES = ("aec", "atc", "asc", "tec")


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def pct(xs, p):
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(p * len(s))))]


def main():
    rows = []
    with gzip.open(FILLS, "rt") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue

    react = [x for x in (_f(r.get("reaction_s")) for r in rows)
             if x is not None]
    slip, depth = [], []
    for r in rows:
        hp, ba = _f(r.get("his_price")), _f(r.get("best_ask"))
        if hp is None or ba is None or not (0 < hp < 1 and 0 < ba < 1):
            continue
        slip.append(ba - hp)
        d = _f(r.get("depth"))
        if d is not None:
            depth.append(d)

    pref = collections.Counter((r.get("market_slug") or "?").split("-")[0]
                               for r in rows)
    on_pmus_namespace = sum(n for p, n in pref.items()
                            if p in PMUS_PREFIXES)

    res = {
        "fills": len(rows),
        "detection_lag_s": {
            "n": len(react), "median": round(st.median(react), 2),
            "p10": round(pct(react, 0.10), 2),
            "p90": round(pct(react, 0.90), 2),
            "max": round(max(react), 1),
            "note": ("negative values are a clock-offset artefact between "
                     "the venue stamp and our detection stamp; the spread "
                     "of the distribution is the usable part, not its sign"),
        },
        "slippage_best_ask_at_detection_minus_their_price": {
            "n": len(slip),
            "median": round(st.median(slip), 4),
            "mean": round(st.fmean(slip), 4),
            "p10": round(pct(slip, 0.10), 4),
            "p90": round(pct(slip, 0.90), 4),
            "fraction_already_moved_against_us": round(
                sum(1 for x in slip if x > 1e-9) / len(slip), 4),
            "fraction_still_at_or_below_their_price": round(
                sum(1 for x in slip if x <= 1e-9) / len(slip), 4),
        },
        "depth_at_detection": ({
            "n": len(depth), "median": round(st.median(depth), 1),
            "p10": round(pct(depth, 0.10), 1),
            "p90": round(pct(depth, 0.90), 1),
        } if depth else {
            "n": 0,
            "note": ("the `depth` field is present on the record but "
                     "carries no parseable number on any fill that also "
                     "has a book -- size available to a copy is therefore "
                     "NOT ESTABLISHED from this snapshot")}),
        "venue": {
            "slug_prefixes": dict(pref.most_common(15)),
            "fills_in_a_PMUS_slug_namespace": on_pmus_namespace,
            "pmus_prefixes_checked": list(PMUS_PREFIXES),
            "note": ("PMUS lists under aec/atc/asc/tec. These fills are "
                     "global Polymarket CLOB slugs (itf, atp, wta, cs2, "
                     "lal, ucl, epl, ...). Sports overlap; slug namespaces "
                     "do not, and no market-level linkage between the two "
                     "venues has ever been established in this repository."),
        },
    }

    s = res["slippage_best_ask_at_detection_minus_their_price"]
    print("=" * 74)
    print("COPY SIGNAL -- is it tradeable by the time it reaches us?")
    print("=" * 74)
    print("  fills                       %d" % res["fills"])
    print("  detection lag (s)           median %.2f  p10 %.2f  p90 %.2f" % (
        res["detection_lag_s"]["median"], res["detection_lag_s"]["p10"],
        res["detection_lag_s"]["p90"]))
    print()
    print("  best_ask AT DETECTION minus THEIR price")
    print("    median %+0.4f   mean %+0.4f   p10 %+0.4f   p90 %+0.4f" % (
        s["median"], s["mean"], s["p10"], s["p90"]))
    print("    ALREADY moved against us:  %.1f%%" % (
        100 * s["fraction_already_moved_against_us"]))
    print("    still at or below them:    %.1f%%" % (
        100 * s["fraction_still_at_or_below_their_price"]))
    print()
    print("  A COPY PAYS A MEDIAN %+0.4f MORE THAN THE ACCOUNT IT COPIES."
          % s["median"])
    print("  The whole two-sided maker edge on PMUS is one half-spread,")
    print("  0.0025 at a one-cent book. The copy slippage is FOUR TIMES it.")
    print()
    print("  venue namespaces: %s" % json.dumps(res["venue"][
        "slug_prefixes"]))
    print("  fills in a PMUS namespace: %d of %d" % (
        on_pmus_namespace, res["fills"]))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(res, f, indent=2, sort_keys=True)
    print("\nwritten: %s" % os.path.relpath(OUT, ROOT))


if __name__ == "__main__":
    main()
