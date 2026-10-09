"""WHAT FRACTION OF PRIORITY MEMBERS STAYS FRESH, BY REFRESH POLICY: A
SIMULATION ON PRODUCTION'S OWN DISTRIBUTIONS (RC6 lane D1, delivery).

Inputs (research-sql run 37870039455, 2026-10-09 01:29Z, RC5 production,
read only; nothing here is tuned to a result):

  STREAM GAPS   the priority members' stream update gaps over 6 h
                (PRIORITY_PMX_BOOKS, 182 symbols, 12,352 gaps), by bucket:
                count and total seconds (GAP_BUCKETS)
  QUIET-FOREVER the members the stream never re-sent in the horizon
                (census STARTED_GT_4H + AWAITING_FIRST_SNAPSHOT, about 9 % of
                the priority denominator over the last 6 h): DEAD_FRACTION
  MEMBERS       86 (01:29Z), 134 (6 h mean), 186 (RC5 20:07Z snapshot),
                306 (24 h maximum)
  PASS          the plane's pass (SNAPSHOT inter-arrival): recent regime
                200-320 s; 24 h p50 92 s / p90 248 s

Policies (each reads at most the venue's 12 GetOrderBook a minute, >= 1 s
apart; a current read counts for the 300 s bound from its receipt):

  NONE              no refresh (RC5)
  RC6_INLINE        RC6 as merged: one read per plane pass, candidates by
                    event start (the pass duration from production)
  DECOUPLED_START   one read a second when due (the freshness task), RC6's
                    event-start order
  DECOUPLED_FAIR    the same, earliest lapse first (RC6 D1)
  FAIR_PLUS_SNAPSHOT  DECOUPLED_FAIR + one snapshot-only gRPC call a minute
                    re-proving every member due within 90 s of its bound
                    (`snapshot_success` of them returned; 1.0 = the venue
                    returns every symbol asked)

Run: python -m tools.freshness_refresh_sim [--seed N] [--hours H]
"""
from __future__ import annotations

import argparse
import bisect
import json
import random

BOUND = 300.0
PER_MIN = 12
MIN_GAP = 1.0
REST_LEAD = 15.0
SNAP_EVERY = 60.0
SNAP_LEAD = 90.0
#: (lower s, upper s, gaps, seconds) -- research-sql run 37870039455 C2
GAP_BUCKETS = ((0.0, 10.0, 238, 1543), (10.0, 30.0, 1534, 27566),
               (30.0, 60.0, 1169, 56399), (60.0, 120.0, 2665, 233630),
               (120.0, 300.0, 5105, 1086972), (300.0, 600.0, 1241, 505925),
               (600.0, 1800.0, 366, 317594), (1800.0, 3600.0, 24, 56629),
               (3600.0, 7200.0, 10, 50510))
DEAD_FRACTION = 0.09
SCENARIOS = {"now_01_29Z": 86, "mean_6h": 134, "rc5_20_07Z": 186,
             "max_24h": 306}
PASS_RECENT = (200.0, 320.0)


def draw_gap(rng) -> float:
    """One stream gap from the production histogram: a bucket by its gap
    count, then a point inside it whose mean is the bucket's own mean."""
    tot = sum(b[2] for b in GAP_BUCKETS)
    x = rng.random() * tot
    for lo, hi, n, secs in GAP_BUCKETS:
        if x < n:
            mean = secs / n
            # a symmetric triangle around the bucket mean, clipped to it
            half = min(mean - lo, hi - mean)
            return mean + (rng.random() - rng.random()) * half
        x -= n
    return GAP_BUCKETS[-1][3] / GAP_BUCKETS[-1][2]


def stream_updates(rng, horizon: float, *, dead: bool, warmup: float):
    """Sorted update instants in [-warmup, horizon] (none for a dead one
    after its last update before the horizon)."""
    if dead:
        return [-warmup - rng.random() * 3600.0]
    t = -warmup - rng.random() * 600.0
    out = []
    while t < horizon:
        out.append(t)
        t += draw_gap(rng)
    return out


def _union(intervals, lo, hi) -> float:
    tot, cur_a, cur_b = 0.0, None, None
    for a, b in sorted(intervals):
        a, b = max(a, lo), min(b, hi)
        if b <= a:
            continue
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                tot += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        tot += cur_b - cur_a
    return tot


def simulate(n: int, policy: str, *, hours: float = 6.0, seed: int = 7,
             dead_fraction: float = DEAD_FRACTION,
             snapshot_success: float = 1.0, pass_s=PASS_RECENT) -> dict:
    rng = random.Random(seed)
    H, warm = hours * 3600.0, 1800.0
    dead = [rng.random() < dead_fraction for _ in range(n)]
    ups = [stream_updates(rng, H, dead=d, warmup=warm) for d in dead]
    starts = [rng.random() * 48 * 3600.0 for _ in range(n)]   # event starts
    reads = [[] for _ in range(n)]
    starts_log = []

    def last_stream(i, t):
        k = bisect.bisect_right(ups[i], t)
        return ups[i][k - 1] if k else None

    def last_read(i):
        return reads[i][-1] if reads[i] else None

    def due(t, lead):
        out = []
        for i in range(n):
            ls = last_stream(i, t)
            if ls is not None and t - ls <= BOUND:
                continue                       # stream current
            lr = last_read(i)
            if lr is not None and t - lr <= BOUND - lead:
                continue                       # refresh current
            lapse = max([x for x in (ls, lr) if x is not None],
                        default=None)
            lapse = float("-inf") if lapse is None else lapse + BOUND
            out.append((lapse, starts[i], i))
        return out

    def slot_ok(t):
        while starts_log and t - starts_log[0] >= 60.0:
            starts_log.pop(0)
        return len(starts_log) < PER_MIN and (
            not starts_log or t - starts_log[-1] >= MIN_GAP)

    t = 0.0
    next_pass = 0.0
    next_snap = 0.0
    snap_calls = snap_syms = rest_reads = 0
    while t < H and policy != "NONE":
        if policy == "FAIR_PLUS_SNAPSHOT" and t >= next_snap:
            d = due(t, SNAP_LEAD)
            if d:
                snap_calls += 1
                snap_syms += len(d)
                for _l, _s, i in d:
                    if rng.random() < snapshot_success:
                        reads[i].append(t)
            next_snap = t + SNAP_EVERY
        make = (policy != "RC6_INLINE") or t >= next_pass
        if policy == "RC6_INLINE" and t >= next_pass:
            next_pass = t + rng.uniform(*pass_s)
        if make and slot_ok(t):
            d = due(t, REST_LEAD)
            if d:
                if policy in ("RC6_INLINE", "DECOUPLED_START"):
                    d.sort(key=lambda x: (x[1], x[2]))
                else:
                    d.sort()
                i = d[0][2]
                reads[i].append(t)
                starts_log.append(t)
                rest_reads += 1
        t += 1.0
    fresh = []
    for i in range(n):
        iv = [(u, u + BOUND) for u in ups[i]] + [(r, r + BOUND)
                                                for r in reads[i]]
        fresh.append(_union(iv, 0.0, H) / H)
    fresh.sort()
    return {"members": n, "policy": policy,
            "fresh_rate": round(sum(fresh) / n, 4),
            "worst_member": round(fresh[0], 4),
            "p10_member": round(fresh[int(0.1 * (n - 1))], 4),
            "members_below_0_5": sum(1 for f in fresh if f < 0.5),
            "rest_reads_per_min": round(rest_reads / (H / 60.0), 2),
            "snapshot_calls_per_min": round(snap_calls / (H / 60.0), 2),
            "snapshot_symbols_per_call": (round(snap_syms / snap_calls, 1)
                                          if snap_calls else 0)}


POLICIES = ("NONE", "RC6_INLINE", "DECOUPLED_START", "DECOUPLED_FAIR",
            "FAIR_PLUS_SNAPSHOT")


def rest_budget_for(n: int, target: float = 0.95, *, seed: int = 7,
                    hours: float = 3.0) -> int | None:
    """The smallest REST budget (reads / min) at which DECOUPLED_FAIR keeps
    `target` of the member-time fresh, or None above 60 / min."""
    global PER_MIN
    keep = PER_MIN
    try:
        for b in range(12, 61, 4):
            PER_MIN = b
            if simulate(n, "DECOUPLED_FAIR", seed=seed, hours=hours)[
                    "fresh_rate"] >= target:
                return b
        return None
    finally:
        PER_MIN = keep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--budget", action="store_true",
                    help="also find the REST budget each scenario needs")
    a = ap.parse_args(argv)
    out = {}
    for name, n in SCENARIOS.items():
        out[name] = [simulate(n, p, seed=a.seed, hours=a.hours)
                     for p in POLICIES]
        if a.budget:
            out[name].append({"rest_budget_for_0_95_per_min":
                              rest_budget_for(n, seed=a.seed)})
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
