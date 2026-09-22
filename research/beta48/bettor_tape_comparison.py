"""The declared candidates, re-run on the venue's own prints.

WHAT CHANGED AND WHAT DID NOT. The replay's traded volume came from the
difference in `sharesTraded` between two BBO snapshots, priced entirely
at `lastTradePx`. Measured against eight complete days of the venue's
published Time & Sales:

    the TOTAL was right      ratio 1.00 on eight of twelve markets,
                             1.02-1.08 on the four sparsely-sampled ones
    the PRICE was not        one interval carries up to 120 distinct
                             prices, and the old test decided the whole
                             interval on whichever side of our quote one
                             arbitrary print happened to fall

So this is not a correction to how much traded. It is a correction to
WHETHER WHAT TRADED EVER REACHED OUR QUOTE.

THE SECOND CORRECTION IS RARITY. Counting intervals containing at least
one real print, the tradeable fraction runs from 34.7% down to 0.2%.
The snapshot proxy offered a non-zero `dvol` far more often than the
tape says a trade actually occurred, so every fill rate computed from
it was an upper bound on an upper bound.

NO CANDIDATE IS ADDED OR RETUNED HERE. The two candidates already
declared are re-run unchanged, at the same four queue fractions, with
the tape on and off, so the difference is attributable to the fill
model and to nothing else.

Run:  python research/beta48/bettor_tape_comparison.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dataclasses                                              # noqa: E402

import bettor_episodes as epi                                   # noqa: E402
import bettor_prints as prints_mod                              # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "acceptance", "tape_comparison.json")

# The candidates AS ALREADY DECLARED. C2 is DECISION_PACKAGE.md section
# 9 verbatim: books two or more ticks wide, at the touch, hard-flatten
# before expiry.
CANDIDATES = [
    ("C0", epi.Policy(name="C0")),
    ("C2", epi.Policy(name="C2", min_spread_ticks=2,
                      placement="AT_TOUCH", hard_flatten=True)),
]
QFRACS = (0.00, 0.25, 0.50, 1.00)


def summarise(eps):
    n = len(eps)
    # A FILL MEANS A MAKER ENTRY LEG FILLED. The end-state label is not
    # a safe proxy -- COMPLETE_PAIR reaches FLAT_PAIRED by BUYING the
    # complement as a taker, which would read as a double-fill.
    anyfill = sum(1 for e in eps if e.get("entry_legs_filled"))
    both = sum(1 for e in eps if len(e.get("entry_legs_filled") or ()) == 2)
    net = sum(e.get("total_if_residual_realises", 0.0) for e in eps)
    contracts = sum(sum(abs(x) for x in e.get("fill_sizes") or ())
                    for e in eps)
    tape_iv = sum(e.get("tape_intervals", 0) for e in eps)
    snap_iv = sum(e.get("snapshot_intervals", 0) for e in eps)
    return {"episodes": n, "any_fill": anyfill, "both_legs": both,
            "net_usd": round(net, 4), "contracts": round(contracts, 2),
            "per_contract": (round(net / contracts, 6)
                             if contracts else None),
            "tape_intervals": tape_iv, "snapshot_intervals": snap_iv}


def main():
    have = bool(prints_mod.tape_dir())
    print("=" * 78)
    print("DECLARED CANDIDATES, SNAPSHOT-DERIVED FILLS vs THE VENUE'S TAPE")
    print("=" * 78)
    if not have:
        print("NO TAPE PRESENT -- refusing to report a tape column that "
              "would actually be the snapshot proxy under another name.")
        return 1

    rows = []
    for name, pol in CANDIDATES:
        for q in QFRACS:
            p = dataclasses.replace(pol, queue_ahead_fraction=q)
            res = {}
            for label, use_tape in (("snapshot", False), ("tape", True)):
                eps = epi.run_all(size=100.0, rebates_on=True,
                                  policy=p, use_tape=use_tape)
                res[label] = summarise(eps)
            rows.append({"candidate": name, "qfrac": q, **res})

    hdr = ("%-4s %5s | %6s %7s %8s %11s %11s | %6s %7s %8s %11s %11s"
           % ("cand", "qfrac", "eps", "anyfil", "both", "net$",
              "per_ctr", "eps", "anyfil", "both", "net$", "per_ctr"))
    print()
    print("%39s | %s" % ("SNAPSHOT-DERIVED (old)", "VENUE TAPE (corrected)"))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        s, t = r["snapshot"], r["tape"]
        print("%-4s %5.2f | %6d %7d %8d %11.2f %11s | %6d %7d %8d %11.2f %11s"
              % (r["candidate"], r["qfrac"],
                 s["episodes"], s["any_fill"], s["both_legs"], s["net_usd"],
                 ("%.6f" % s["per_contract"]) if s["per_contract"] is not None
                 else "-",
                 t["episodes"], t["any_fill"], t["both_legs"], t["net_usd"],
                 ("%.6f" % t["per_contract"]) if t["per_contract"] is not None
                 else "-"))

    print()
    print("EPISODE COUNTS DIFFER BETWEEN THE TWO COLUMNS, and I had")
    print("written that they could not. They can: episodes are")
    print("non-overlapping, so the next one starts a cooldown after the")
    print("previous one ENDS, and when an episode ends depends on what")
    print("filled. A fill model that fills more ends episodes later and")
    print("therefore fits fewer of them into the same tape (C0 qfrac")
    print("0.00: 734 snapshot vs 817 tape). The two columns are")
    print("comparable as POLICY OUTCOMES over one corpus; they are not")
    print("a paired per-episode comparison.")
    print()
    print("NEITHER COLUMN REPRODUCES THE DECISION_PACKAGE_V2 TABLE, and")
    print("is not meant to. That table predates the causal-join, queue-")
    print("persistence and depletion repairs, and counts 'any fill' from")
    print("the end-state label rather than from entry legs. The snapshot")
    print("column here is the POST-REPAIR snapshot model, which is why")
    print("C2 reads about -$127 rather than the +$1.23 published there.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump({"candidates": [c[0] for c in CANDIDATES],
                   "qfracs": list(QFRACS), "rows": rows}, fh,
                  indent=2, sort_keys=True)
    print("\nwritten: %s" % os.path.relpath(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
