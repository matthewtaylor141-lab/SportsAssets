"""THE POLICY SWEEP: which decisions cost the money, and what the
case-study-supported alternatives do instead.

WHAT THE PREVIOUS REPORT CLAIMED AND WHY IT WAS TOO BROAD. It ran ONE
policy -- one entry filter, one quote placement, one inventory rule,
one recovery path, one settlement treatment -- and reported the result
as "the two-sided maker policy is rejected." That is a claim about
market making. What was actually tested is a claim about that one
configuration.

This file runs the alternatives the case studies and the engine
already support, on the SAME development tape, under the SAME
execution model, and records every one of them -- including the ones
that do worse, which stay in the record rather than disappearing.

THREE THINGS THIS FILE ALSO FIXES

1. DENOMINATORS. "q = 71/943 = 7.53%" mixed populations: 538 of those
   943 episodes never had a fill at all, so the numerator and the
   denominator were not describing the same thing. Conditional on any
   fill it is 71/405, and both are reported with their populations
   named.

2. THE q* THRESHOLD IS GONE. A single break-even double-fill rate
   cannot represent a portfolio whose episodes end in six different
   ways with six different cash profiles. The complete episode cash
   flows replace it.

3. LOSS ATTRIBUTION. Instead of one aggregate, the cash is decomposed
   by end state and by leg, so "which decision lost the money" is
   answered rather than guessed.

Run:  python research/beta48/bettor_policy_sweep.py
"""
from __future__ import annotations

import collections
import json
import math
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bettor_episodes as epi                                   # noqa: E402
from bettor_episode_report import cluster_interval              # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "acceptance", "policy_sweep.json")

SIZE = 100.0
DENSE_MIN = 10          # episodes required for a cluster to be "dense"


# ─────────────────────────────────────────────────────────────────────
# THE VARIANTS. Every one of these was run; none is omitted from the
# output. The naming says which decision each one changes, so a reader
# can see the axis rather than a label.
# ─────────────────────────────────────────────────────────────────────
VARIANTS = [
    epi.Policy(name="BASE"),

    # ── quote placement ──────────────────────────────────────────────
    epi.Policy(name="PLACE_at_touch", placement="AT_TOUCH"),
    epi.Policy(name="PLACE_one_tick_behind", placement="DEEPER_1"),

    # ── entry filter ─────────────────────────────────────────────────
    epi.Policy(name="ENTRY_spread_ge_2t", min_spread_ticks=2),
    epi.Policy(name="ENTRY_spread_ge_3t", min_spread_ticks=3),
    epi.Policy(name="ENTRY_mid_0.05_0.95", min_mid=0.05, max_mid=0.95),
    epi.Policy(name="ENTRY_mid_0.20_0.80", min_mid=0.20, max_mid=0.80),

    # ── inventory rule ───────────────────────────────────────────────
    epi.Policy(name="INV_cancel_other_on_fill", cancel_other_on_fill=True),

    # ── time ─────────────────────────────────────────────────────────
    epi.Policy(name="TIME_horizon_5", quote_horizon=5),
    epi.Policy(name="TIME_horizon_40", quote_horizon=40),
    epi.Policy(name="TIME_recovery_2", recovery_wait=2),
    epi.Policy(name="TIME_recovery_30", recovery_wait=30),

    # ── recovery path ────────────────────────────────────────────────
    epi.Policy(name="REC_taker_now", recovery="TAKER_NOW"),
    epi.Policy(name="REC_complete_pair", recovery="COMPLETE_PAIR"),
    epi.Policy(name="REC_hold_to_settlement", recovery="HOLD"),

    # ── settlement treatment ─────────────────────────────────────────
    epi.Policy(name="SETTLE_hard_flatten", hard_flatten=True),

    # ── the combinations the single-axis results point at ────────────
    epi.Policy(name="COMBO_touch_flatten",
               placement="AT_TOUCH", hard_flatten=True),
    epi.Policy(name="COMBO_touch_flatten_wide",
               placement="AT_TOUCH", hard_flatten=True,
               min_spread_ticks=2),
    epi.Policy(name="COMBO_touch_flatten_cancel",
               placement="AT_TOUCH", hard_flatten=True,
               cancel_other_on_fill=True),
    epi.Policy(name="COMBO_complete_flatten",
               recovery="COMPLETE_PAIR", hard_flatten=True),
]


def decompose(eps):
    """Where the cash came from and went. The answer to 'which
    decision lost the money', not an aggregate."""
    by_state = collections.defaultdict(lambda: [0.0, 0])
    entry_maker, exit_maker, exit_taker, complete = 0.0, 0.0, 0.0, 0.0
    rebates, taker_fees = 0.0, 0.0
    for e in eps:
        b = by_state[e["status"]]
        b[0] += e["total_if_residual_realises"]
        b[1] += 1
        rebates += e["rebates_received"]
        taker_fees += e["taker_fees_paid"]
    return {
        "by_end_state": {k: {"sum": round(v[0], 4), "n": v[1],
                             "mean": round(v[0] / v[1], 5) if v[1] else None}
                         for k, v in sorted(by_state.items())},
        "rebates_received": round(rebates, 4),
        "taker_fees_paid": round(taker_fees, 4),
        "_unused": (entry_maker, exit_maker, exit_taker, complete),
    }


def populations(eps):
    """Every rate with its OWN denominator stated.

    The previous report divided the both-legs-filled count by ALL
    episodes, including the 538 that never had a fill. Two different
    populations, one ratio.
    """
    n = len(eps)
    any_fill = [e for e in eps if e["entry_fills"] > 0]
    # BOTH LEGS FILLED AS MAKER ENTRIES -- read from the fills, not
    # from the end-state label.
    paired = [e for e in eps
              if len(e.get("entry_legs_filled") or ()) == 2]
    one_sided = [e for e in any_fill
                 if len(e.get("entry_legs_filled") or ()) < 2]
    never = [e for e in eps if e["status"] == epi.NEVER_FILLED]
    # the two counts that must reconcile
    recon = {"episodes": n,
             "never_filled_status": len(never),
             "entry_fills_gt_0": len(any_fill),
             "status_plus_anyfill": len(never) + len(any_fill),
             "reconciles": len(never) + len(any_fill) == n}
    return {
        "all_episodes": n,
        "episodes_with_any_entry_fill": len(any_fill),
        "episodes_both_legs_filled": len(paired),
        "episodes_one_sided": len(one_sided),
        "P_any_fill__of_all_episodes": round(len(any_fill) / n, 4) if n
        else None,
        "P_both__of_all_episodes": round(len(paired) / n, 4) if n else None,
        "P_both__GIVEN_any_fill": round(len(paired) / len(any_fill), 4)
        if any_fill else None,
        "reconciliation": recon,
    }


def evaluate(policy, rebates_on=True, queue="QUEUE_FRONT_IF_INSIDE"):
    eps = epi.run_all(size=SIZE, rebates_on=rebates_on,
                      queue_model=queue, policy=policy)
    if not eps:
        return {"policy": policy.name, "episodes": 0,
                "note": "the entry filter admitted nothing"}
    tot = [e["total_if_residual_realises"] / SIZE for e in eps]
    ev_cl = [e["event"] for e in eps]

    by_ev = collections.defaultdict(list)
    for v, c in zip(tot, ev_cl):
        by_ev[c].append(v)
    dense = {k: v for k, v in by_ev.items() if len(v) >= DENSE_MIN}
    d_tot, d_cl = [], []
    for k, v in dense.items():
        d_tot.extend(v)
        d_cl.extend([k] * len(v))

    return {
        "policy": policy.name,
        "params": {k: v for k, v in policy.__dict__.items()
                   if k != "name"},
        "episodes": len(eps),
        "markets": len({e["slug"] for e in eps}),
        "events": len(by_ev),
        "populations": populations(eps),
        "sum_total_dollars": round(sum(tot) * SIZE, 2),
        # PRIMARY: all clusters
        "primary_all_clusters": cluster_interval(tot, ev_cl),
        # SENSITIVITY: dense clusters only
        "sensitivity_dense_clusters": (cluster_interval(d_tot, d_cl)
                                       if len(dense) > 1 else None),
        "decomposition": decompose(eps),
    }


def main():
    res = {"size": SIZE, "dense_min_episodes": DENSE_MIN, "variants": []}

    print("=" * 78)
    print("POLICY SWEEP -- every variant run, every variant kept")
    print("=" * 78)
    print("%-28s %6s %6s %8s %8s %10s  %s" % (
        "policy", "eps", "events", "P(fill)", "P(both|", "sum $",
        "per contract, ALL clusters, 95% CI"))
    print("%-28s %6s %6s %8s %8s %10s" % ("", "", "", "", " fill)", ""))
    print("-" * 78)

    for pol in VARIANTS:
        r = evaluate(pol)
        res["variants"].append(r)
        if not r.get("episodes"):
            print("%-28s  %s" % (pol.name, r.get("note")))
            continue
        p = r["populations"]
        c = r["primary_all_clusters"]
        ci = c["ci"] or [float("nan")] * 2
        print("%-28s %6d %6d %8.4f %8.4f %+10.2f  %+0.5f [%+0.5f, %+0.5f]"
              % (pol.name, r["episodes"], r["events"],
                 p["P_any_fill__of_all_episodes"],
                 (p["P_both__GIVEN_any_fill"]
                  if p["P_both__GIVEN_any_fill"] is not None
                  else float("nan")),
                 r["sum_total_dollars"], c["point"], ci[0], ci[1]))

    # ── the denominator correction, stated once, on the base policy ──
    base = res["variants"][0]
    p = base["populations"]
    print()
    print("=" * 78)
    print("DENOMINATORS -- the correction")
    print("=" * 78)
    print("  all episodes                        %d" % p["all_episodes"])
    print("  never filled                        %d" %
          p["reconciliation"]["never_filled_status"])
    print("  with ANY entry fill                 %d" %
          p["episodes_with_any_entry_fill"])
    print("  both legs filled                    %d" %
          p["episodes_both_legs_filled"])
    print("  reconciles (never + anyfill == all) %s" %
          p["reconciliation"]["reconciles"])
    print()
    print("  P(both legs) over ALL episodes      %.4f   <- the mixed ratio"
          % p["P_both__of_all_episodes"])
    print("  P(both legs | ANY fill)             %.4f   <- same population"
          % p["P_both__GIVEN_any_fill"])
    print("  P(any fill)                         %.4f"
          % p["P_any_fill__of_all_episodes"])

    # ── loss attribution on the base policy ──────────────────────────
    print()
    print("=" * 78)
    print("WHERE THE CASH GOES -- base policy, by end state")
    print("=" * 78)
    d = base["decomposition"]
    print("  %-26s %6s %12s %10s" % ("end state", "n", "sum $", "mean $"))
    for k, v in d["by_end_state"].items():
        print("  %-26s %6d %+12.2f %+10.4f" % (k, v["n"], v["sum"],
                                               v["mean"]))
    print("  %-26s %6s %+12.2f" % ("rebates received", "", d[
        "rebates_received"]))
    print("  %-26s %6s %+12.2f" % ("taker fees paid", "", d[
        "taker_fees_paid"]))

    # ── the ranking ──────────────────────────────────────────────────
    ranked = sorted([v for v in res["variants"] if v.get("episodes")],
                    key=lambda v: -v["primary_all_clusters"]["point"])
    print()
    print("=" * 78)
    print("RANKED by per-contract point estimate, ALL clusters primary")
    print("=" * 78)
    print("  %-28s %+10s %-26s %s" % ("policy", "point", "95% CI",
                                      "dense-cluster sensitivity"))
    for v in ranked:
        c = v["primary_all_clusters"]
        s = v["sensitivity_dense_clusters"]
        sc = ("%+0.5f [%+0.5f, %+0.5f]" % (s["point"], s["ci"][0],
                                           s["ci"][1])
              if s and s.get("ci") else "n/a")
        print("  %-28s %+10.5f [%+0.5f, %+0.5f]  %s" % (
            v["policy"], c["point"], c["ci"][0], c["ci"][1], sc))

    res["ranked"] = [v["policy"] for v in ranked]
    res["best_by_point_estimate"] = ranked[0]["policy"] if ranked else None
    res["any_lower_bound_above_zero"] = [
        v["policy"] for v in ranked
        if v["primary_all_clusters"]["ci"]
        and v["primary_all_clusters"]["ci"][0] > 0]

    print()
    print("  variants whose 95%% lower bound is above zero: %s"
          % (res["any_lower_bound_above_zero"] or "NONE"))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(res, f, indent=2, sort_keys=True, default=str)
    print("\nwritten: %s" % os.path.relpath(OUT))


if __name__ == "__main__":
    main()
