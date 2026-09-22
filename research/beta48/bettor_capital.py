"""CAPITAL-HOURS: the time integral of committed capital, not an average.

THE ARITHMETIC THIS REPLACES. The previous report said "+$0.0397 per
episode over $68.3 capital-hours = +5.8 bp per capital-hour" without
saying whether $68.3 was a total or a per-episode average. It was a
per-episode average, so the ratio happened to be consistent -- but the
report also quoted a $15.14 TOTAL beside it, and dividing that total by
a per-episode average gives 2,217 bp, which is meaningless. Two
different denominators sat in the same paragraph.

WHAT IS COMPUTED HERE

  capital_hours_total   the integral of committed capital over time,
                        summed across every episode
  peak_concurrent       the largest total commitment at any instant
  return_per_cap_hour   total profit / capital_hours_total

OVERLAPPING EPISODES. Episodes never overlap WITHIN a market -- the
runner advances past the previous one's end -- but they run
CONCURRENTLY ACROSS markets. That matters for the PEAK, which is a
funding requirement, and not for the INTEGRAL, which is additive over
episodes regardless of overlap. Both are reported, because a strategy
that needs $900 standing to earn its integral is a different business
from one that needs $99.

RESIDUAL INVENTORY is tracked separately: the hours between a first
fill and the episode going flat (or settling), during which capital is
committed to a position rather than to a resting quote.

THIS IS A REPLAY SCENARIO, NOT A YIELD. Every fill in it is simulated.
The figure describes what this development corpus would have produced
under this execution model; it is not an annualisable rate and it does
not scale.

Run:  python research/beta48/bettor_capital.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bettor_episodes as epi                                   # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "acceptance", "capital.json")
SIZE = 100.0


def _t(iso):
    try:
        return dt.datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return None


def capital_profile(eps, size=SIZE):
    """Per-episode commitment and duration, then the integral."""
    rows, events = [], []
    for e in eps:
        a, b = _t(e["t0"]), _t(e["t_end"])
        if a is None or b is None or b < a:
            continue
        dur = b - a
        # BOTH LEGS RESTING is the commitment the venue holds from the
        # instant the quotes go up: our bid at p_b plus our NO bid at
        # (1 - p_o). That is (1 - captured_spread) per contract-pair.
        cap = (e["quote_bid"] + (1.0 - e["quote_offer"])) * size
        rows.append({"slug": e["slug"], "t0": a, "t1": b, "dur_s": dur,
                     "cap": cap, "cap_hours": cap * dur / 3600.0,
                     "pnl": e["total_if_residual_realises"],
                     "filled": e["entry_fills"] > 0,
                     "status": e["status"]})
        events.append((a, +cap))
        events.append((b, -cap))

    events.sort()
    cur = peak = 0.0
    peak_at = None
    for t, d in events:
        cur += d
        if cur > peak:
            peak, peak_at = cur, t
    total_ch = sum(r["cap_hours"] for r in rows)
    total_pnl = sum(r["pnl"] for r in rows)

    # residual-inventory hours: first fill -> episode end
    inv_hours = []
    for e in eps:
        if e["entry_fills"] <= 0:
            continue
        b = _t(e["t_end"])
        if b is None:
            continue
        inv_hours.append((b - _t(e["t0"])) / 3600.0)

    return {
        "episodes": len(rows),
        "total_pnl": round(total_pnl, 4),
        "capital_hours_TOTAL": round(total_ch, 2),
        "capital_hours_MEAN_per_episode": round(
            total_ch / len(rows), 4) if rows else None,
        "peak_concurrent_capital": round(peak, 2),
        "peak_at": (dt.datetime.fromtimestamp(
            peak_at, dt.timezone.utc).isoformat() if peak_at else None),
        "mean_commitment_per_episode": round(
            st.fmean([r["cap"] for r in rows]), 2) if rows else None,
        "mean_duration_h": round(
            st.fmean([r["dur_s"] for r in rows]) / 3600.0, 4) if rows
        else None,
        "median_duration_h": round(
            st.median([r["dur_s"] for r in rows]) / 3600.0, 4) if rows
        else None,
        "filled_episodes": sum(1 for r in rows if r["filled"]),
        "inventory_hours_total": round(sum(inv_hours), 3),
        "inventory_hours_median": round(st.median(inv_hours), 4)
        if inv_hours else None,
        "return_per_capital_hour": (round(total_pnl / total_ch, 8)
                                    if total_ch else None),
        "return_per_capital_hour_bp": (round(1e4 * total_pnl / total_ch, 4)
                                       if total_ch else None),
        "_label": ("REPLAY SCENARIO on development data under a "
                   "simulated execution model -- NOT a yield, NOT "
                   "annualisable, NOT scalable"),
    }


def main():
    res = {}
    print("=" * 76)
    print("CAPITAL-HOURS -- the time integral, with both denominators named")
    print("=" * 76)
    for nm, kw in (("C0_base", dict()),
                   ("C2_wide_touch_flatten",
                    dict(min_spread_ticks=2, hard_flatten=True,
                         placement="AT_TOUCH"))):
        for f in (0.0, 0.25, 1.0):
            pol = epi.Policy(name=nm, queue_ahead_fraction=f,
                             execution_scenario="TRADE_ONLY", **kw)
            eps = epi.run_all(size=SIZE, policy=pol)
            c = capital_profile(eps)
            res["%s|qfrac=%.2f" % (nm, f)] = c
            print()
            print("%s   qfrac=%.2f" % (nm, f))
            print("  episodes %d   filled %d" % (c["episodes"],
                                                 c["filled_episodes"]))
            print("  TOTAL profit                 %+0.2f" % c["total_pnl"])
            print("  TOTAL capital-hours          %0.2f" % c[
                "capital_hours_TOTAL"])
            print("  mean capital-hours/episode   %0.4f" % c[
                "capital_hours_MEAN_per_episode"])
            print("  mean commitment/episode      $%0.2f over %0.3f h" % (
                c["mean_commitment_per_episode"], c["mean_duration_h"]))
            print("  PEAK concurrent capital      $%0.2f  at %s" % (
                c["peak_concurrent_capital"], c["peak_at"]))
            print("  inventory-hours (filled eps) %0.2f total, %0.3f median"
                  % (c["inventory_hours_total"],
                     c["inventory_hours_median"] or 0.0))
            print("  RETURN per capital-hour      %+0.8f  = %+0.3f bp" % (
                c["return_per_capital_hour"],
                c["return_per_capital_hour_bp"]))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(res, f, indent=2, sort_keys=True)
    print("\nwritten: %s" % os.path.relpath(OUT))


if __name__ == "__main__":
    main()
