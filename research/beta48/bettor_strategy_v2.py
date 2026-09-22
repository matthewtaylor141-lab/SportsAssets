"""The two missing capabilities, implemented and evaluated honestly.

WHAT THE DEMONSTRATION REPORTED AS ABSENT, and what is now here:

  DECISION-TIME SIZING   `size` was a run-wide constant. It now has a
      rule that uses only what is knowable when the quote is placed --
      the spread the book is offering, in ticks above the minimum this
      policy will quote. Depth is not used because the corpus has none;
      realised fills are not used because they are outcomes.

  CAPITAL RELEASE        a matched pair was held to settlement because
      no merge/netting call has been demonstrated at this venue. The
      FEASIBLE ALTERNATIVE is implemented: sell both legs back through
      the real ladder and take the spread as the price of getting the
      capital back now.

WHAT IS EVALUATED, AND THE HONEST FRAME. Sizing is an ALLOCATION rule,
not a profitability lever: edge and capital both scale with the clip,
so a uniform change of size leaves per-capital-hour untouched. What it
can change is which books receive the capital. Capital release is a
genuine trade: it pays the spread to convert a certain settlement claim
into cash now. Both are reported against the same baseline, on the same
corpus, under the same execution assumptions, and a worse result is
reported as a worse result.

Run:  python research/beta48/bettor_strategy_v2.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bettor_episodes as epi                                # noqa: E402
import bettor_policy_final as pf                             # noqa: E402
import bettor_prints as prints_mod                           # noqa: E402

OUT = os.path.join(HERE, "acceptance", "strategy_v2.json")

BASE = dict(name="C3", min_spread_ticks=2, placement="AT_TOUCH",
            hard_flatten=True, cancel_other_on_fill=True,
            max_unmatched_mult=0.5, recovery="COMPLETE_PAIR")

VARIANTS = [
    ("V0  baseline (fixed clip, hold)", dict(BASE)),
    ("V1  + decision-time sizing", dict(BASE, size_rule="EDGE_SCALED")),
    ("V2  + capital release", dict(BASE, release_matched=True)),
    ("V3  + both", dict(BASE, size_rule="EDGE_SCALED",
                        release_matched=True)),
]
QFRACS = (0.00, 0.25, 0.50, 1.00)


def _t(s):
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


def summarise(eps):
    s = pf.summarise(eps)
    # Capital-hours on FILLED collateral only, which is what a release
    # actually frees -- resting collateral is released by cancelling,
    # not by unwinding.
    held = 0.0
    for e in eps:
        a, b = _t(e.get("t0")), _t(e.get("t_end"))
        if a is None or b is None or b <= a:
            continue
        held += e.get("collateral_filled_only", 0.0) * (b - a) / 3600.0
    sizes = [e.get("size") for e in eps if e.get("size")]
    rules = {}
    for e in eps:
        r = (e.get("size_rule") or "?").split(" ")[0]
        rules[r] = rules.get(r, 0) + 1
    released = sum(1 for e in eps
                   if any("RELEASED" in n for n in (e.get("notes") or ())))
    unliq = sum(1 for e in eps
                if any("UNLIQUIDATED" in n for n in (e.get("notes") or ())))
    s.update({"held_capital_hours": round(held, 2),
              "size_min": round(min(sizes), 1) if sizes else None,
              "size_max": round(max(sizes), 1) if sizes else None,
              "size_mean": round(sum(sizes) / len(sizes), 1) if sizes
              else None,
              "size_rules": rules,
              "pairs_released": released,
              "episodes_with_unliquidated": unliq})
    return s


def main():
    if not prints_mod.tape_dir():
        print("NO TAPE -- refusing to report a tape-backed result without "
              "the tape.")
        return 1
    print("=" * 84)
    print("STRATEGY v2 -- decision-time sizing and capital release")
    print("simulated replay on the SAME corpus, policy and execution "
          "assumptions")
    print("=" * 84)
    print("%-32s %5s %6s %9s %10s %11s %8s" % (
        "variant", "qfrac", "eps", "net$", "cap_hrs", "per_cap_hr",
        "held_hrs"))
    print("-" * 88)
    rows = []
    for name, kw in VARIANTS:
        for q in QFRACS:
            pol = epi.Policy(queue_ahead_fraction=q, **kw)
            eps = epi.run_all(size=100.0, rebates_on=True, policy=pol,
                              use_tape=True)
            s = summarise(eps)
            assert s["snapshot_intervals"] == 0, "snapshot fallback leaked"
            rows.append({"variant": name, "qfrac": q, **s})
            print("%-32s %5.2f %6d %9.2f %10.1f %11s %8.1f" % (
                name, q, s["episodes"], s["net_usd"], s["capital_hours"],
                ("%.6f" % s["per_capital_hour"])
                if s["per_capital_hour"] is not None else "-",
                s["held_capital_hours"]))

    print()
    print("=" * 84)
    print("WHAT EACH CAPABILITY ACTUALLY DID")
    print("=" * 84)
    base = {r["qfrac"]: r for r in rows if r["variant"].startswith("V0")}
    for tag, label in (("V1", "decision-time sizing"),
                       ("V2", "capital release"),
                       ("V3", "both")):
        rs = [r for r in rows if r["variant"].startswith(tag)]
        print("\n%s  %s" % (tag, label))
        for r in rs:
            b = base[r["qfrac"]]
            d_net = r["net_usd"] - b["net_usd"]
            d_held = r["held_capital_hours"] - b["held_capital_hours"]
            print("   qfrac %.2f  net %+8.2f (%+7.2f)  held-hrs %8.1f "
                  "(%+8.1f)  sizes %s..%s  released %d  unliq %d"
                  % (r["qfrac"], r["net_usd"], d_net,
                     r["held_capital_hours"], d_held,
                     r["size_min"], r["size_max"], r["pairs_released"],
                     r["episodes_with_unliquidated"]))

    with open(OUT, "w") as fh:
        json.dump({"strategy": "BETTOR_STRATEGY_V2",
                   "execution": "tape-backed replay; SIMULATED, not "
                                "account performance",
                   "corpus": "the same 12-market corpus, fully consumed",
                   "baseline": "V0 = C3 as previously evaluated",
                   "rows": rows}, fh, indent=2, default=str)
    print("\nwritten: %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
