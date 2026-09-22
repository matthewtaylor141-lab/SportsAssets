"""THE EPISODE REPORT: what the two-sided maker policy actually earns.

This replaces "+0.0086 per contract, positive in 1,551 of 1,551
observations" -- which was conditional arithmetic on the favourable
branch of an identity, repeated once per snapshot -- with whole
episodes that carry their inventory to an end.

It also computes, from the same tape:

  * q, the DOUBLE-FILL RATE, measured rather than assumed, against the
    break-even q* the earlier analysis derived;
  * the exact fee and rebate at every execution price and every
    realised partial-fill size, with banker's rounding per fill;
  * E[settlement - price], the estimand that was recorded as NOT YET
    MEASURED, on the ten markets whose outcome the capture observed;
  * a cluster-aware interval, at the EVENT level, with the number of
    independent clusters stated because it is the binding constraint.

Run:  python research/beta48/bettor_episode_report.py
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
import bettor_policy_ev as ev                                   # noqa: E402
import bettor_tape as tape                                      # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "acceptance", "episode_results.json")


# ── cluster-aware interval ────────────────────────────────────────────
def cluster_interval(values, clusters, conf=0.95):
    """A t-interval on CLUSTER means, not on observations.

    Episodes inside one event are not independent: the same book, the
    same counterparties, the same news. Pooling them and taking a
    naive standard error would divide by sqrt(943) when the honest
    denominator is sqrt(11). This returns both so the difference is
    visible rather than argued about.
    """
    by = collections.defaultdict(list)
    for v, c in zip(values, clusters):
        by[c].append(v)
    means = [st.fmean(v) for v in by.values()]
    k = len(means)
    naive_se = (st.pstdev(values) / math.sqrt(len(values))
                if len(values) > 1 else float("nan"))
    if k < 2:
        return {"clusters": k, "cluster_means": means,
                "point": st.fmean(values) if values else float("nan"),
                "ci": None, "naive_se": naive_se,
                "note": "fewer than two clusters -- no interval is defined"}
    m = st.fmean(means)
    se = st.stdev(means) / math.sqrt(k)
    # two-sided t critical values, k-1 df, 95%
    tcrit = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
             7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201,
             12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131}.get(k - 1, 1.96)
    return {"clusters": k,
            "cluster_means": [round(x, 6) for x in sorted(means)],
            "point": round(m, 6),
            "cluster_se": round(se, 6),
            "ci": [round(m - tcrit * se, 6), round(m + tcrit * se, 6)],
            "naive_se": round(naive_se, 6),
            "se_inflation_vs_naive": (round(se / naive_se, 2)
                                      if naive_se and naive_se == naive_se
                                      else None)}


# ── 1. the policy, as episodes ────────────────────────────────────────
def policy_episodes(size=100.0):
    grid = {}
    for rebates_on in (True, False):
        for qm in epi.QUEUE_MODELS:
            eps = epi.run_all(size=size, rebates_on=rebates_on,
                              queue_model=qm)
            tot = [e["total_if_residual_realises"] for e in eps]
            ev_cl = [e["event"] for e in eps]
            byst = collections.Counter(e["status"] for e in eps)
            n = len(eps)
            paired = byst[epi.FLAT_PAIRED]
            any_fill = sum(1 for e in eps if e["entry_fills"] > 0)
            key = "%s|%s" % ("PUBLISHED" if rebates_on else "EXCLUDED", qm)
            grid[key] = {
                "episodes": n,
                "markets": len({e["slug"] for e in eps}),
                "events": len({e["event"] for e in eps}),
                "statuses": dict(byst),
                "double_fill_rate_q": round(paired / n, 4) if n else None,
                "any_fill_rate": round(any_fill / n, 4) if n else None,
                "sum_total": round(sum(tot), 4),
                "per_episode": cluster_interval(tot, ev_cl),
                "per_contract": cluster_interval(
                    [x / size for x in tot], ev_cl),
                "positive_episodes": sum(1 for x in tot if x > 0),
                "unmatched_at_end": sum(
                    1 for e in eps
                    if e["residual_contracts"]["net_directional"] != 0),
                "rebates_received_total": round(
                    sum(e["rebates_received"] for e in eps), 4),
                "taker_fees_total": round(
                    sum(e["taker_fees_paid"] for e in eps), 4),
            }
    return grid


# ── 2. exact fee arithmetic, per price and per ACTUAL fill size ───────
def fee_table():
    """The claim "1-2 contracts round the rebate to zero" is not
    universally true. It is true at some prices and false at others,
    and what matters is the size of each FILL, not the size of the
    ORDER. This is the arithmetic, at the observed price range."""
    prices = [0.005, 0.01, 0.02, 0.05, 0.10, 0.13, 0.20, 0.30, 0.40,
              0.50, 0.60, 0.73, 0.90, 0.95]
    sizes = [1, 2, 3, 4, 5, 10, 25, 100, 1000]
    rows = []
    for p in prices:
        raw1 = abs(ev.THETA_MAKER) * 1 * p * (1 - p)
        row = {"price": p, "raw_rebate_1_contract": round(raw1, 8),
               "per_contract": {}}
        for n in sizes:
            r = ev.fee(p, n, maker=True)
            row["per_contract"][n] = round(r / n, 8)
        # the size at which one fill first earns a non-zero rebate
        first = next((n for n in range(1, 2001)
                      if ev.fee(p, n, maker=True) > 0), None)
        row["min_fill_for_nonzero_rebate"] = first
        rows.append(row)
    return rows


def realised_fill_sizes(size=100.0):
    """What the fills ACTUALLY were, so the fee question is answered on
    realised sizes rather than on the order size."""
    eps = epi.run_all(size=size, rebates_on=True,
                      queue_model="QUEUE_FRONT_IF_INSIDE")
    sizes = [abs(n) for e in eps for n in e["fill_sizes"]]
    if not sizes:
        return {"fills": 0}
    buckets = collections.Counter()
    for s in sizes:
        buckets["1" if s <= 1 else "2" if s <= 2 else "3-4" if s <= 4
                else "5-9" if s < 10 else "10-99" if s < 100
                else ">=100"] += 1
    return {"fills": len(sizes),
            "median_fill": st.median(sizes),
            "mean_fill": round(st.fmean(sizes), 3),
            "min": min(sizes), "max": max(sizes),
            "partial_fills_below_order_size": sum(
                1 for s in sizes if s < size - 1e-9),
            "size_buckets": dict(buckets)}


# ── 3. E[settlement - price]: the estimand recorded as NOT MEASURED ───
def settlement_returns():
    """For every market whose outcome the capture OBSERVED, the return
    to buying YES at the touch and holding, and to buying NO.

    This is estimand A -- E[SETTLEMENT - PRICE | STATE] -- which every
    prior document recorded as NOT YET MEASURED. It is measurable here
    because `settlementPx` moves to the realised outcome AFTER expiry.
    It is NOT estimand C: nothing here is conditioned on OUR fill.
    """
    by_slug, _ = tape.load_tape()
    out = {"markets": [], "note": (
        "DEVELOPMENT data. Ten labelled markets on ten events, eight of "
        "which settled at 0. Any estimate here is dominated by that "
        "class balance and by four NFL markets sharing one afternoon.")}
    for slug, rows in sorted(by_slug.items()):
        lab, det = tape.settlement_label(rows)
        if lab == tape.SETTLEMENT_NOT_OBSERVED:
            out["markets"].append({"slug": slug, "settlement": lab,
                                   "why": det.get("reason")})
            continue
        s = float(lab)
        opn = [r for r in rows
               if r["state"] == tape.OPEN and r["bid"] is not None
               and r["ask"] is not None]
        if not opn:
            out["markets"].append({"slug": slug, "settlement": s,
                                   "two_sided_open_rows": 0})
            continue
        mids = [0.5 * (r["bid"] + r["ask"]) for r in opn]
        asks = [r["ask"] for r in opn]
        bids = [r["bid"] for r in opn]
        out["markets"].append({
            "slug": slug, "event": tape.event_of(slug),
            "settlement": s,
            "two_sided_open_rows": len(opn),
            "first_mid": round(mids[0], 4),
            "last_mid": round(mids[-1], 4),
            "mean_mid": round(st.fmean(mids), 4),
            "buy_yes_at_ask_mean_return": round(
                s - st.fmean(asks), 4),
            "buy_no_at_1_minus_bid_mean_return": round(
                (1.0 - s) - st.fmean([1.0 - b for b in bids]), 4),
            "last_mid_called_it": (mids[-1] > 0.5) == (s > 0.5),
        })
    lab = [m for m in out["markets"] if isinstance(m.get("settlement"),
                                                   float)]
    ys = [m["buy_yes_at_ask_mean_return"] for m in lab
          if "buy_yes_at_ask_mean_return" in m]
    ns = [m["buy_no_at_1_minus_bid_mean_return"] for m in lab
          if "buy_no_at_1_minus_bid_mean_return" in m]
    evs = [m["event"] for m in lab if "event" in m]
    out["buy_yes_market_level"] = cluster_interval(ys, evs) if ys else None
    out["buy_no_market_level"] = cluster_interval(ns, evs) if ns else None
    out["settled_at_1"] = sum(1 for m in lab if m["settlement"] == 1.0)
    out["settled_at_0"] = sum(1 for m in lab if m["settlement"] == 0.0)
    return out


# ── 4. sensitivity ────────────────────────────────────────────────────
def sensitivity():
    grid = {}
    base_h, base_r = epi.QUOTE_HORIZON, epi.RECOVERY_WAIT
    for h in (5, 10, 20, 40):
        epi.QUOTE_HORIZON = h
        for size in (4.0, 100.0):
            eps = epi.run_all(size=size, rebates_on=True,
                              queue_model="QUEUE_FRONT_IF_INSIDE")
            tot = [e["total_if_residual_realises"] for e in eps]
            n = len(eps)
            paired = sum(1 for e in eps if e["status"] == epi.FLAT_PAIRED)
            grid["horizon=%d size=%g" % (h, size)] = {
                "episodes": n,
                "q_double_fill": round(paired / n, 4) if n else None,
                "sum_total": round(sum(tot), 4),
                "per_contract_point": round(
                    st.fmean(tot) / size, 6) if tot else None,
                "positive": sum(1 for x in tot if x > 0),
            }
    epi.QUOTE_HORIZON, epi.RECOVERY_WAIT = base_h, base_r
    return grid


def main():
    res = {}
    print("=" * 74)
    print("1. THE POLICY AS WHOLE EPISODES")
    print("=" * 74)
    res["policy"] = policy_episodes()
    for k, v in res["policy"].items():
        ci = v["per_contract"]["ci"]
        print("\n%s" % k)
        print("  episodes %4d over %d markets / %d events" % (
            v["episodes"], v["markets"], v["events"]))
        print("  statuses %s" % v["statuses"])
        print("  q (both legs filled)  %.4f      any fill %.4f" % (
            v["double_fill_rate_q"], v["any_fill_rate"]))
        print("  per contract, cluster-aware: %+0.6f   95%% CI [%+0.6f, "
              "%+0.6f]  on %d clusters" % (
                  v["per_contract"]["point"], ci[0], ci[1],
                  v["per_contract"]["clusters"]))
        print("  naive SE would have been %.6f; cluster SE is %.6f (x%s)" % (
            v["per_contract"]["naive_se"], v["per_contract"]["cluster_se"],
            v["per_contract"]["se_inflation_vs_naive"]))
        print("  total %+0.2f  positive %d/%d  unmatched at end %d" % (
            v["sum_total"], v["positive_episodes"], v["episodes"],
            v["unmatched_at_end"]))

    print()
    print("=" * 74)
    print("2. EXACT FEE ARITHMETIC -- per price, per FILL size")
    print("=" * 74)
    res["fee_table"] = fee_table()
    print("%-7s %-12s %s" % ("price", "raw@1", "rebate PER CONTRACT at a "
                             "single fill of n"))
    print("%-7s %-12s %s" % ("", "", "  ".join(
        "%9d" % n for n in (1, 2, 4, 10, 100, 1000))))
    for r in res["fee_table"]:
        print("%-7.3f %-12.8f %s   min n for a non-zero rebate: %s" % (
            r["price"], r["raw_rebate_1_contract"],
            "  ".join("%9.6f" % r["per_contract"][n]
                      for n in (1, 2, 4, 10, 100, 1000)),
            r["min_fill_for_nonzero_rebate"]))
    res["realised_fills"] = realised_fill_sizes()
    print("\nREALISED fill sizes in the episodes (order size 100):")
    print("  %s" % json.dumps(res["realised_fills"]))

    print()
    print("=" * 74)
    print("3. E[settlement - price] -- the estimand recorded NOT MEASURED")
    print("=" * 74)
    res["settlement"] = settlement_returns()
    for m in res["settlement"]["markets"]:
        if not isinstance(m.get("settlement"), float):
            print("  %-50s %s" % (m["slug"][:50], m["settlement"]))
            continue
        if "buy_yes_at_ask_mean_return" not in m:
            print("  %-50s settled %.2f  (no two-sided open rows)" % (
                m["slug"][:50], m["settlement"]))
            continue
        print("  %-50s settled %.2f  mean mid %.4f  buyYES %+0.4f  "
              "buyNO %+0.4f  last mid called it: %s" % (
                  m["slug"][:50], m["settlement"], m["mean_mid"],
                  m["buy_yes_at_ask_mean_return"],
                  m["buy_no_at_1_minus_bid_mean_return"],
                  m["last_mid_called_it"]))
    for side in ("buy_yes_market_level", "buy_no_market_level"):
        c = res["settlement"][side]
        if c and c.get("ci"):
            print("  %-26s %+0.4f  95%% CI [%+0.4f, %+0.4f] on %d clusters"
                  % (side, c["point"], c["ci"][0], c["ci"][1],
                     c["clusters"]))
    print("  settled at 1: %d    settled at 0: %d" % (
        res["settlement"]["settled_at_1"], res["settlement"]["settled_at_0"]))

    print()
    print("=" * 74)
    print("4. SENSITIVITY -- quoting horizon and size")
    print("=" * 74)
    res["sensitivity"] = sensitivity()
    print("%-22s %8s %8s %12s %12s %s" % (
        "setting", "episodes", "q", "sum", "per contract", "positive"))
    for k, v in res["sensitivity"].items():
        print("%-22s %8d %8.4f %+12.2f %+12.6f %d" % (
            k, v["episodes"], v["q_double_fill"], v["sum_total"],
            v["per_contract_point"], v["positive"]))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(res, f, indent=2, sort_keys=True)
    print("\nwritten: %s" % os.path.relpath(OUT))


if __name__ == "__main__":
    main()
