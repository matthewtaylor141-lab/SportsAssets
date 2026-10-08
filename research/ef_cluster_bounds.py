#!/usr/bin/env python3
"""ECONOMIC FUNNEL (owner directive section 6): THE UNCERTAINTY BOUNDS, FROM
THE CLUSTER VALUES PRODUCTION RETURNED -- NOTHING ELSE.

READ-ONLY, OFFLINE, STDLIB ONLY. It reads the two CSVs extracted verbatim from
research-sql run 37790252179 (research/ef_clusters.sql, sections 6.1 and
6.2) and prints, per mechanism and for the portfolio:

  n clusters, sum, mean, sd, se, the one-sided 98% Student-t lower and upper
  bounds on the mean net per independent unit (df = n - 1), a seeded cluster
  bootstrap 2nd percentile of the mean (20,000 resamples, seed 20261008),
  the skew, the worst cluster and the mean without it.

THE INDEPENDENT UNIT IS THE FIXTURE (paper_fills.fixture). Every position of
one strategy on one fixture is one cluster; the portfolio rows cluster across
strategies (a fixture traded by two strategies is ONE unit). Correlated fills
of one event are therefore never counted as independent samples (PM request
02:48Z).

WHY t AND A BOOTSTRAP. The clusters are few (37-141) and heavily left-skewed
(one Derek fixture is 57% of Derek's loss), so the normal bound the SQL prints
is reported beside the t bound and the bootstrap, never instead of them. When
the mean is negative no bound can make it positive; the bounds then say how
far from zero the evidence is, not whether a profit exists.

Usage:
  python3 research/ef_cluster_bounds.py \\
      research/data/ef_paper_clusters_run37790252179.csv \\
      research/data/ef_forward_refused_run37790252179.csv
"""
from __future__ import annotations

import collections
import csv
import math
import random
import sys

SEED = 20261008
REPS = 20000
ONE_SIDED = 0.98

#: EVIDENCE CLASS (owner directive / PM 02:48Z). Only Derek's two-model policy
#: carries BETTOR's own probability; the completed-game policy decides on the
#: de-vigged Pinnacle probability alone (NOT proprietary, whatever its
#: migration-223 sleeve label says); exploration is the training strategy.
EVIDENCE_CLASS = {
    "DEREK_ENTRY_POLICY_V2": "PROPRIETARY_BETTOR",
    "PINNACLE_COMPLETED_GAME_PAPER": "BENCHMARK_PINNACLE_ONLY",
    "PINNACLE_EXPLORATION_PAPER": "TRAINING",
}


def _betacf(a: float, b: float, x: float) -> float:
    tiny, eps = 1e-300, 3e-14
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)),
                   -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + aa / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: int) -> float:
    p = 0.5 * _betai(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - p if t > 0 else p


def t_quantile(q: float, df: int) -> float:
    lo, hi = 0.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        lo, hi = (mid, hi) if t_cdf(mid, df) < q else (lo, mid)
    return (lo + hi) / 2.0


def summarize(xs: list) -> dict:
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    se = sd / math.sqrt(n)
    tq = t_quantile(ONE_SIDED, n - 1)
    rng = random.Random(SEED)
    boots = sorted(sum(xs[rng.randrange(n)] for _ in range(n)) / n
                   for _ in range(REPS))
    skew = (sum((x - m) ** 3 for x in xs) / n) / sd ** 3 if sd else 0.0
    rest = sorted(xs)[1:]
    return {"n": n, "sum": round(sum(xs), 2), "mean": round(m, 2),
            "sd": round(sd, 2), "se": round(se, 2), "t98": round(tq, 4),
            "lb98_t": round(m - tq * se, 2), "ub98_t": round(m + tq * se, 2),
            "lb98_bootstrap": round(boots[int((1 - ONE_SIDED) * REPS)], 2),
            "skew": round(skew, 2), "worst": round(min(xs), 2),
            "mean_without_worst": round(sum(rest) / len(rest), 2),
            "units_positive": sum(1 for x in xs if x > 0)}


def main(paper_csv: str, forward_csv: str) -> None:
    rows = list(csv.DictReader(open(paper_csv)))
    out = {}
    for s in sorted({r["strategy"] for r in rows}):
        mine = [r for r in rows if r["strategy"] == s]
        out["%s | %s | realized" % (EVIDENCE_CLASS[s], s)] = summarize(
            [float(r["realized_usd"]) for r in mine])
        out["%s | %s | realized - expected at entry" % (
            EVIDENCE_CLASS[s], s)] = summarize(
            [float(r["realized_usd"]) - float(r["expected_at_entry_usd"] or 0)
             for r in mine])
    by_fx = collections.defaultdict(float)
    by_fx_nt = collections.defaultdict(float)
    for r in rows:
        by_fx[r["fixture"]] += float(r["realized_usd"])
        if EVIDENCE_CLASS[r["strategy"]] != "TRAINING":
            by_fx_nt[r["fixture"]] += float(r["realized_usd"])
    out["PORTFOLIO | all strategies, clustered across strategies"] = \
        summarize(list(by_fx.values()))
    out["PORTFOLIO | proprietary + benchmark (no training)"] = \
        summarize(list(by_fx_nt.values()))
    fw = [r for r in csv.DictReader(open(forward_csv))
          if r["strategy"] == "PINNACLE_COMPLETED_GAME_PAPER"]
    out["HYPOTHETICAL | forward refused Pinnacle-only | settled cf P&L"] = \
        summarize([float(r["cf_policy_size_pnl_usd"]) for r in fw])
    out["HYPOTHETICAL | forward refused Pinnacle-only | EV after measured costs"] = \
        summarize([float(r["ev_after_measured_usd"]) for r in fw])
    out["HYPOTHETICAL | forward refused Pinnacle-only | cf - EV after measured"] = \
        summarize([float(r["cf_policy_size_pnl_usd"])
                   - float(r["ev_after_measured_usd"]) for r in fw])
    out["HYPOTHETICAL | forward refused Pinnacle-only | cf - bind all-in EV"] = \
        summarize([float(r["cf_policy_size_pnl_usd"])
                   - float(r["ev_all_in_usd"]) for r in fw])
    for k, v in out.items():
        print(k)
        print("   " + ", ".join("%s=%s" % kv for kv in v.items()))
    # the sample a positive bound would need IF the measured-cost expectation
    # were the true mean, at the observed forward dispersion (normal, z98)
    mu = sum(float(r["ev_after_measured_usd"]) for r in fw) / len(fw)
    sd = out["HYPOTHETICAL | forward refused Pinnacle-only | settled cf P&L"]["sd"]
    z = 2.0537489
    print("required independent fixtures for a one-sided 98%% bound > 0 at "
          "mean %.2f and sd %.2f: %d" % (mu, sd, math.ceil((z * sd / mu) ** 2)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
