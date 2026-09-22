"""PROFIT ATTRIBUTION for the researched account, not capital share.

THE CLAIM BEING CORRECTED. Earlier documents said "43.9% to 91.9% of
the capital behind every headline is held to settlement, not traded as
pairs," and then concluded that hold-to-settlement "explains most of
the returns." That does not follow. Capital deployed is not profit
earned. A channel can absorb 90% of the capital and produce none of
the profit, or 10% and produce all of it.

This computes the attribution properly, from the fills and the
settlement outcomes:

    for each condition
        net position and average cost PER OUTCOME
        matched = min over outcomes of the long positions
        PAIR CHANNEL      matched x (1 - sum of average costs)
        SETTLEMENT        residual x (payout - average cost)

`matched x 1` is exact: in a binary condition one outcome pays 1 and
the other 0, so a matched pair pays exactly 1 whatever happens. The
residual is where the outcome matters, and that is the settlement
channel by definition.

ATTRIBUTION CONVENTION, stated because it is a choice. Shares are
fungible, so "which share was the matched one" has no physical answer.
This uses AVERAGE cost per outcome, which splits a channel's profit in
proportion to size. An alternative (FIFO matching) would move profit
between the channels without changing the total. The TOTAL is
convention-free; the SPLIT is not, and is labelled as such.

WHAT THIS IS NOT. These are Polymarket global-CLOB fills by another
account. Gross of fees, on a venue with a different schedule, in
markets we do not select. It is evidence about what that account did.
It is NOT our executable edge and nothing here is treated as one.

Run:  python research/beta48/bettor_rn1_attribution.py
"""
from __future__ import annotations

import collections
import gzip
import json
import math
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FILLS = os.path.join(ROOT, "research", "snapshots", "u2_events_v1.jsonl.gz")
SETTLE = os.path.join(ROOT, "research", "snapshots", "settlement_v1.jsonl")


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def load_settlements():
    out = {}
    with open(SETTLE) as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not (r.get("closed") and r.get("resolved")):
                continue
            pay = r.get("payouts")
            if not isinstance(pay, list) or not pay:
                continue
            vals = [_f(p) for p in pay]
            if any(v is None for v in vals):
                continue
            out[r["condition_id"]] = {
                "payouts": vals, "slug": r.get("market_slug"),
                "sport": r.get("sport"),
                "resolved_at": r.get("resolved_at")}
    return out


def load_fills():
    by_cond = collections.defaultdict(list)
    with gzip.open(FILLS, "rt") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            cid = r.get("condition_id_effective") or r.get("condition_id")
            sz, px = _f(r.get("size")), _f(r.get("price"))
            idx = r.get("outcome_index")
            if cid is None or sz is None or px is None or idx is None:
                continue
            by_cond[cid].append({"idx": int(idx), "side": r.get("side"),
                                 "size": sz, "price": px,
                                 "ts": r.get("ts"),
                                 "slug": r.get("market_slug"),
                                 "sport": r.get("sport")})
    return by_cond


def attribute(fills, payouts):
    """Split one condition's realised P&L into the two channels."""
    pos = collections.defaultdict(float)
    cost = collections.defaultdict(float)
    for f in fills:
        s = f["size"] if f["side"] == "BUY" else -f["size"]
        pos[f["idx"]] += s
        cost[f["idx"]] += s * f["price"]
    longs = {i: p for i, p in pos.items() if p > 1e-9}
    avg = {i: (cost[i] / pos[i] if abs(pos[i]) > 1e-12 else 0.0)
           for i in pos}

    matched = 0.0
    if len(longs) >= 2 and len(payouts) >= 2:
        # a matched set needs one unit of EVERY outcome
        if set(longs) >= set(range(len(payouts))):
            matched = min(longs[i] for i in range(len(payouts)))

    pair_cost_per_set = sum(avg.get(i, 0.0) for i in range(len(payouts)))
    pair_pnl = matched * (1.0 - pair_cost_per_set)

    settle_pnl = 0.0
    residual_notional = 0.0
    for i, p in pos.items():
        resid = p - matched if p > 0 else p
        if abs(resid) < 1e-9:
            continue
        payout = payouts[i] if i < len(payouts) else 0.0
        settle_pnl += resid * (payout - avg.get(i, 0.0))
        residual_notional += abs(resid * avg.get(i, 0.0))

    return {
        "pair_pnl": pair_pnl,
        "settlement_pnl": settle_pnl,
        "total_pnl": pair_pnl + settle_pnl,
        "matched_sets": matched,
        "matched_notional": matched * pair_cost_per_set,
        "residual_notional": residual_notional,
        "fills": len(fills),
        "outcomes_touched": len(pos),
    }


def main():
    print("loading settlements ...")
    sett = load_settlements()
    print("  resolved conditions with payouts: %d" % len(sett))
    print("loading fills ...")
    by_cond = load_fills()
    print("  conditions with fills: %d   fills: %d" % (
        len(by_cond), sum(len(v) for v in by_cond.values())))

    rows, skipped = [], collections.Counter()
    for cid, fl in by_cond.items():
        s = sett.get(cid)
        if s is None:
            skipped["no_resolved_settlement"] += 1
            continue
        a = attribute(fl, s["payouts"])
        a.update(condition_id=cid, slug=s["slug"], sport=s["sport"],
                 resolved_at=s["resolved_at"])
        rows.append(a)

    tot_pair = sum(r["pair_pnl"] for r in rows)
    tot_set = sum(r["settlement_pnl"] for r in rows)
    tot = tot_pair + tot_set
    cap_pair = sum(r["matched_notional"] for r in rows)
    cap_res = sum(r["residual_notional"] for r in rows)

    print()
    print("=" * 74)
    print("PROFIT ATTRIBUTION -- the two channels, on resolved conditions")
    print("=" * 74)
    print("  conditions attributed      %d" % len(rows))
    print("  conditions skipped         %s" % dict(skipped))
    print()
    print("  %-26s %14s %14s" % ("", "CAPITAL", "PROFIT"))
    print("  %-26s %14.2f %14.2f" % ("pair channel (matched)",
                                     cap_pair, tot_pair))
    print("  %-26s %14.2f %14.2f" % ("settlement (residual)",
                                     cap_res, tot_set))
    print("  %-26s %14.2f %14.2f" % ("TOTAL", cap_pair + cap_res, tot))
    if cap_pair + cap_res > 0:
        print()
        print("  CAPITAL share  pair %.1f%%   settlement %.1f%%" % (
            100 * cap_pair / (cap_pair + cap_res),
            100 * cap_res / (cap_pair + cap_res)))
    if abs(tot) > 1e-9:
        print("  PROFIT  share  pair %.1f%%   settlement %.1f%%" % (
            100 * tot_pair / tot, 100 * tot_set / tot))
    print()
    print("  THE TWO SHARES ARE DIFFERENT NUMBERS. That is the whole point:")
    print("  a capital share was previously quoted as if it were a profit")
    print("  share, and it is not.")

    # dispersion, clustered by SPORT (the coarsest honest grouping here)
    by_sport = collections.defaultdict(lambda: [0.0, 0.0, 0])
    for r in rows:
        b = by_sport[r["sport"] or "?"]
        b[0] += r["pair_pnl"]
        b[1] += r["settlement_pnl"]
        b[2] += 1
    print()
    print("  %-16s %10s %12s %12s" % ("sport", "conditions", "pair", "settle"))
    for s, (p, se, n) in sorted(by_sport.items(), key=lambda kv: -kv[1][2]):
        print("  %-16s %10d %12.2f %12.2f" % (s[:16], n, p, se))

    pair_rows = [r for r in rows if r["matched_sets"] > 1e-9]
    print()
    print("  conditions with ANY matched pair: %d of %d (%.1f%%)" % (
        len(pair_rows), len(rows), 100 * len(pair_rows) / max(1, len(rows))))
    print("  conditions that are PURELY directional: %d" % (
        len(rows) - len(pair_rows)))

    # per-condition dispersion -- the honest unit
    tots = [r["total_pnl"] for r in rows]
    if len(tots) > 2:
        m = st.fmean(tots)
        se = st.stdev(tots) / math.sqrt(len(tots))
        print()
        print("  per-condition total P&L: mean %+0.4f  SE %0.4f  "
              "95%% CI [%+0.4f, %+0.4f]" % (m, se, m - 1.96 * se,
                                            m + 1.96 * se))
        print("  (conditions are the unit; they are NOT independent across "
              "one event or one day, so this interval is a FLOOR on the "
              "uncertainty, not an estimate of it)")

    out = os.path.join(HERE, "acceptance", "rn1_attribution.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump({
            "conditions_attributed": len(rows),
            "skipped": dict(skipped),
            "capital": {"pair": round(cap_pair, 2),
                        "settlement": round(cap_res, 2)},
            "profit": {"pair": round(tot_pair, 2),
                       "settlement": round(tot_set, 2),
                       "total": round(tot, 2)},
            "capital_share_pct": {
                "pair": round(100 * cap_pair / (cap_pair + cap_res), 2)
                if cap_pair + cap_res else None,
                "settlement": round(100 * cap_res / (cap_pair + cap_res), 2)
                if cap_pair + cap_res else None},
            "profit_share_pct": {
                "pair": round(100 * tot_pair / tot, 2) if tot else None,
                "settlement": round(100 * tot_set / tot, 2) if tot else None},
            "conditions_with_any_matched_pair": len(pair_rows),
            "conditions_purely_directional": len(rows) - len(pair_rows),
            "by_sport": {s: {"conditions": n, "pair": round(p, 2),
                             "settlement": round(se, 2)}
                         for s, (p, se, n) in by_sport.items()},
            "attribution_convention": (
                "average cost per outcome; the TOTAL is convention-free, "
                "the SPLIT between channels is not"),
            "fees": "GROSS -- global Polymarket CLOB, not the PMUS schedule",
        }, f, indent=2, sort_keys=True)
    print("\nwritten: %s" % os.path.relpath(out, ROOT))


if __name__ == "__main__":
    main()
