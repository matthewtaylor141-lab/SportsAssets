"""SMALL, PURE STATISTICS FOR THE LAB (no numpy: the production image carries
none). Deterministic: every resample uses a seeded generator.

  quantile           linear-interpolated sample quantile
  cluster_bootstrap  percentile interval of a statistic, resampling CLUSTERS
                     (independent events, e.g. fixtures), never rows
  kaplan_meier       survival of a duration with right censoring
  km_quantile        the time by which a fraction q of units had crossed
  bonferroni_level   the per-interval level for k simultaneous intervals
  fisher_exact       two-sided exact test of a 2x2 table (hypergeometric)
"""
from __future__ import annotations

import math
import random

DEFAULT_B = 1000
DEFAULT_SEED = 20261004


def quantile(xs, q: float):
    v = sorted(float(x) for x in xs if x is not None and not math.isnan(float(x)))
    if not v:
        return None
    i = (len(v) - 1) * float(q)
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return round(v[lo] + (v[hi] - v[lo]) * (i - lo), 9)


def quartiles(xs) -> dict:
    v = [x for x in xs if x is not None]
    return {"n": len(v), "p25": quantile(v, 0.25), "p50": quantile(v, 0.5),
            "p75": quantile(v, 0.75)}


def median(xs):
    return quantile(xs, 0.5)


def bonferroni_level(k: int, family: float = 0.95) -> float:
    k = max(1, int(k))
    return 1.0 - (1.0 - float(family)) / k


def cluster_bootstrap(clusters: dict, stat, *, b: int = DEFAULT_B,
                      level: float = 0.95, seed: int = DEFAULT_SEED) -> dict:
    """Percentile interval of `stat(list_of_units)` resampling whole clusters
    with replacement. `clusters` maps a cluster key to its list of units.
    Returns {lo, hi, level, b, clusters} or {status: UNAVAILABLE, why}."""
    keys = sorted(clusters, key=str)
    if len(keys) < 2:
        return {"status": "UNAVAILABLE", "why": "FEWER_THAN_2_CLUSTERS",
                "clusters": len(keys)}
    rng = random.Random(seed)
    vals = []
    for _ in range(int(b)):
        units = []
        for _k in range(len(keys)):
            units.extend(clusters[keys[rng.randrange(len(keys))]])
        s = stat(units)
        if s is not None:
            vals.append(float(s))
    if len(vals) < max(10, int(b) // 2):
        return {"status": "UNAVAILABLE", "why": "STATISTIC_UNDEFINED_IN_MOST_"
                "RESAMPLES", "defined": len(vals), "b": int(b)}
    a = (1.0 - float(level)) / 2.0
    return {"status": "MEASURED", "lo": quantile(vals, a),
            "hi": quantile(vals, 1.0 - a), "level": round(float(level), 6),
            "b": int(b), "clusters": len(keys),
            "method": "percentile bootstrap resampling independent clusters"}


def kaplan_meier(units) -> list:
    """`units` = [(time, crossed: bool)]. Returns the step curve
    [(t, survival)] at each crossing time (right-censored units leave the
    risk set at their time without a step)."""
    pts = sorted((float(t), bool(e)) for t, e in units if t is not None)
    n = len(pts)
    s, out, i = 1.0, [], 0
    at_risk = n
    while i < n:
        t = pts[i][0]
        d = c = 0
        while i < n and pts[i][0] == t:
            if pts[i][1]:
                d += 1
            else:
                c += 1
            i += 1
        if d and at_risk > 0:
            s *= (1.0 - d / at_risk)
            out.append((t, round(s, 9)))
        at_risk -= d + c
    return out


def km_quantile(units, q: float):
    """The earliest time by which a fraction `q` of the units had crossed
    (survival <= 1 - q), or None when censoring leaves it unreached."""
    for t, s in kaplan_meier(units):
        if s <= 1.0 - float(q) + 1e-12:
            return t
    return None


def fisher_exact(a: int, b: int, c: int, d: int) -> dict:
    """TWO-SIDED FISHER EXACT TEST of the 2x2 table [[a, b], [c, d]] (rows =
    arms, columns = event / no event): the probability, under independence
    with every margin fixed, of a table at most as likely as the observed
    one (the conventional two-sided definition; a relative tolerance keeps
    ties of equal probability on both sides). Exact, no approximation."""
    a, b, c, d = (int(x) for x in (a, b, c, d))
    if min(a, b, c, d) < 0:
        return {"status": "UNAVAILABLE", "why": "NEGATIVE_CELL"}
    r1, r2, c1 = a + b, c + d, a + c
    n = r1 + r2
    if r1 == 0 or r2 == 0 or c1 == 0 or c1 == n:
        return {"status": "UNAVAILABLE", "why": "A_MARGIN_IS_ZERO",
                "table": [[a, b], [c, d]]}
    denom = math.comb(n, c1)

    def pr(x):
        return math.comb(r1, x) * math.comb(r2, c1 - x) / denom
    lo, hi = max(0, c1 - r2), min(r1, c1)
    p_obs = pr(a)
    p = sum(pr(x) for x in range(lo, hi + 1)
            if pr(x) <= p_obs * (1.0 + 1e-7))
    return {"status": "MEASURED", "p_two_sided": round(min(1.0, p), 6),
            "table": [[a, b], [c, d]],
            "method": "Fisher exact, two-sided (sum of tables no more likely "
                      "than the observed one)"}


def km_quartiles(units) -> dict:
    u = [x for x in units if x[0] is not None]
    return {"n": len(u), "crossed": sum(1 for _, e in u if e),
            "censored": sum(1 for _, e in u if not e),
            "p25": km_quantile(u, 0.25), "p50": km_quantile(u, 0.5),
            "p75": km_quantile(u, 0.75),
            "method": ("Kaplan-Meier with right censoring; a crossing time is "
                       "the first recorded observation at or below the target "
                       "(an upper bound on the true crossing); a unit that "
                       "never crossed is censored at its last observation")}
