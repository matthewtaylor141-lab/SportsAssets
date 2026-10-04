"""LAB-F DRIFT SENTINEL: THE STATISTICS (pure; standard library only).

Every function here is deterministic, interpretable and has a unit test
against a known distribution or a hand-computed exact value
(tests/test_lab_drift_stats.py). No numpy / scipy: the production image has
neither (the learning kernel is pure Python for the same reason).

  quantile            type-7 (linear interpolation between order
                      statistics; numpy / R default)
  psi_numeric         Population Stability Index with a FIXED BINNING RULE:
                      the bins are the REFERENCE window's own quantiles --
                      quintiles when min(n_ref, n_cmp) < 100, deciles from
                      100 -- edges de-duplicated, bin i = (e_{i-1}, e_i],
                      first bin (-inf, e_1], last (e_last, +inf). Empty bins
                      are floored at PSI_EPS = 1e-4 of mass (no
                      renormalisation). PSI = sum (c - r) ln(c / r).
  psi_categorical     the same index over the union of categories
  psi_null_expectation  (B - 1)(1/n_ref + 1/n_cmp): the approximate mean of
                      PSI between two samples of ONE distribution (PSI is
                      ~ a symmetrised KL divergence whose multinomial
                      sampling bias is that term). Reported beside every PSI
                      so a small-sample PSI is never read as drift by itself
  ks_2samp            two-sample Kolmogorov-Smirnov. D is computed EXACTLY in
                      integers (D = max |i m - j n| / (n m)); the p-value is
                      EXACT (Hodges' lattice-path count, integer arithmetic)
                      when n m <= KS_EXACT_MAX_CELLS, else the asymptotic
                      Kolmogorov distribution with Stephens' small-sample
                      correction lambda = (sqrt(ne) + 0.12 + 0.11 /
                      sqrt(ne)) D, ne = n m / (n + m). Ties make both
                      conservative (the null distribution is the continuous
                      one)
  chi_square_homogeneity  2 x k test of homogeneity of a categorical mix,
                      categories pooled into OTHER until every expected
                      count is >= CHI_MIN_EXPECTED (5; Cochran's rule)
  tvd                 total-variation distance 0.5 sum |p - q| (effect size
                      for a categorical mix, beside the PSI)
  chi2_sf             chi-square survival function (regularised upper
                      incomplete gamma, series / continued fraction)
  bh_adjust           Benjamini-Hochberg step-up q-values (FDR control
                      across every metric x segment test of one run)
  bootstrap_quantile_shift  percentile-bootstrap 95% interval for
                      Q_cmp(q) - Q_ref(q), seeded and reproducible; each
                      window resampled independently; at most BOOT_CAP
                      values per window (a systematic subsample of the
                      sorted sample -- conservative: fewer values give a
                      wider interval)
"""
from __future__ import annotations

import bisect
import hashlib
import math
import random

VERSION = "LAB_DRIFT_STATS_V1"

PSI_EPS = 1e-4
PSI_DECILE_MIN_N = 100
KS_EXACT_MAX_CELLS = 250_000
CHI_MIN_EXPECTED = 5.0
BOOT_B = 400
BOOT_CAP = 2000
BOOT_LEVEL = 0.95
OTHER = "__OTHER__"


# ═════════════════════════════════════════════════════════════════════
# ORDER STATISTICS
# ═════════════════════════════════════════════════════════════════════

def _finite(xs) -> list:
    out = []
    for x in xs or ():
        if x is None or isinstance(x, bool):
            continue
        try:
            f = float(x)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def quantile(sorted_xs: list, q: float) -> float | None:
    """Type-7 quantile of an ASCENDING list. None for an empty list."""
    n = len(sorted_xs)
    if n == 0:
        return None
    if not 0.0 <= q <= 1.0:
        raise ValueError("q outside [0, 1]")
    h = (n - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return sorted_xs[lo] + (h - lo) * (sorted_xs[hi] - sorted_xs[lo])


def median(xs) -> float | None:
    s = sorted(_finite(xs))
    return quantile(s, 0.5)


# ═════════════════════════════════════════════════════════════════════
# POPULATION STABILITY INDEX
# ═════════════════════════════════════════════════════════════════════

def psi_bins(n_ref: int, n_cmp: int) -> int:
    """THE FIXED BINNING RULE: quintiles below PSI_DECILE_MIN_N in the
    smaller window, deciles from it."""
    return 10 if min(n_ref, n_cmp) >= PSI_DECILE_MIN_N else 5


def psi_edges(ref_sorted: list, k: int) -> list:
    """The reference window's own j/k quantiles, de-duplicated. A constant
    reference has the single edge at its value (two bins: <= v and > v)."""
    if not ref_sorted:
        return []
    edges = sorted({quantile(ref_sorted, j / k) for j in range(1, k)})
    return edges or [ref_sorted[0]]


def _index_value(p: float, q: float) -> float:
    p, q = max(p, PSI_EPS), max(q, PSI_EPS)
    return (q - p) * math.log(q / p)


def psi_from_counts(ref_counts: list, cmp_counts: list) -> float:
    nr, nc = float(sum(ref_counts)), float(sum(cmp_counts))
    if nr <= 0 or nc <= 0:
        raise ValueError("empty window")
    return sum(_index_value(r / nr, c / nc)
               for r, c in zip(ref_counts, cmp_counts))


def psi_numeric(ref, cmp) -> dict:
    r, c = sorted(_finite(ref)), sorted(_finite(cmp))
    if not r or not c:
        raise ValueError("empty window")
    k = psi_bins(len(r), len(c))
    edges = psi_edges(r, k)
    nb = len(edges) + 1
    rc, cc = [0] * nb, [0] * nb
    for x in r:
        rc[bisect.bisect_left(edges, x)] += 1
    for x in c:
        cc[bisect.bisect_left(edges, x)] += 1
    psi = psi_from_counts(rc, cc)
    return {"psi": psi, "bins": nb, "rule_bins": k, "edges": edges,
            "ref_counts": rc, "cmp_counts": cc,
            "psi_null_expectation": psi_null_expectation(nb, len(r), len(c))}


def psi_categorical(ref_counts: dict, cmp_counts: dict) -> dict:
    cats = sorted(set(ref_counts) | set(cmp_counts), key=str)
    rc = [float(ref_counts.get(k, 0.0)) for k in cats]
    cc = [float(cmp_counts.get(k, 0.0)) for k in cats]
    psi = psi_from_counts(rc, cc)
    return {"psi": psi, "bins": len(cats), "categories": cats,
            "psi_null_expectation": psi_null_expectation(
                len(cats), sum(rc), sum(cc))}


def psi_null_expectation(bins: int, n_ref: float, n_cmp: float) -> float:
    if n_ref <= 0 or n_cmp <= 0:
        return float("inf")
    return max(0, bins - 1) * (1.0 / n_ref + 1.0 / n_cmp)


# ═════════════════════════════════════════════════════════════════════
# TWO-SAMPLE KOLMOGOROV-SMIRNOV
# ═════════════════════════════════════════════════════════════════════

def ks_statistic_num(a_sorted: list, b_sorted: list) -> int:
    """max over the pooled values of |i m - j n| (integers), where i, j are
    the counts of a, b at or below the value. D = result / (n m)."""
    n, m = len(a_sorted), len(b_sorted)
    i = j = 0
    best = 0
    while i < n or j < m:
        if j >= m or (i < n and a_sorted[i] <= b_sorted[j]):
            v = a_sorted[i]
        else:
            v = b_sorted[j]
        while i < n and a_sorted[i] <= v:
            i += 1
        while j < m and b_sorted[j] <= v:
            j += 1
        best = max(best, abs(i * m - j * n))
    return best


def ks_exact_sf(d_num: int, n: int, m: int) -> float:
    """P(D >= d) under H0 (continuous), d = d_num / (n m): one minus the
    share of the C(n+m, n) monotone lattice paths that stay STRICTLY
    inside |i m - j n| < d_num. Integer arithmetic throughout."""
    if d_num <= 0:
        return 1.0
    row = [0] * (m + 1)
    for j in range(m + 1):
        if abs(0 * m - j * n) < d_num:
            row[j] = 1 if j == 0 else row[j - 1]
        else:
            row[j] = 0
    for i in range(1, n + 1):
        new = [0] * (m + 1)
        for j in range(m + 1):
            if abs(i * m - j * n) >= d_num:
                new[j] = 0
            else:
                new[j] = row[j] + (new[j - 1] if j else 0)
        row = new
    total = math.comb(n + m, n)
    p = (total - row[m]) / total
    return min(1.0, max(0.0, p))


def kolmogorov_q(lam: float) -> float:
    """Q_KS(lambda) = 2 sum_{k>=1} (-1)^(k-1) exp(-2 k^2 lambda^2)."""
    # Below 0.2 the alternating series converges too slowly to sum and
    # 1 - Q(0.2) < 1e-12 (the dual series of the CDF), so 1.0 is exact to
    # that precision.
    if lam < 0.2:
        return 1.0
    a2 = -2.0 * lam * lam
    fac, total = 2.0, 0.0
    for k in range(1, 201):
        term = fac * math.exp(a2 * k * k)
        total += term
        if abs(term) <= 1e-16 * abs(total) or abs(term) <= 1e-300:
            break
        fac = -fac
    return min(1.0, max(0.0, total))


def ks_2samp(a, b) -> dict:
    x, y = sorted(_finite(a)), sorted(_finite(b))
    n, m = len(x), len(y)
    if n == 0 or m == 0:
        raise ValueError("empty window")
    d_num = ks_statistic_num(x, y)
    d = d_num / float(n * m)
    if n * m <= KS_EXACT_MAX_CELLS:
        p = ks_exact_sf(d_num, n, m)
        method = "EXACT_LATTICE_PATHS"
    else:
        ne = n * m / float(n + m)
        s = math.sqrt(ne)
        p = kolmogorov_q((s + 0.12 + 0.11 / s) * d)
        method = "ASYMPTOTIC_KOLMOGOROV_STEPHENS"
    return {"d": d, "p": p, "n_ref": n, "n_cmp": m, "method": method}


# ═════════════════════════════════════════════════════════════════════
# CHI-SQUARE
# ═════════════════════════════════════════════════════════════════════

def _gser(a: float, x: float) -> float:
    """Regularised lower incomplete gamma P(a, x) by its series."""
    ap, total = a, 1.0 / a
    delta = total
    for _ in range(10_000):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * 1e-15:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a: float, x: float) -> float:
    """Regularised upper incomplete gamma Q(a, x) by Lentz's continued
    fraction."""
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 10_000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-15:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_sf(x: float, df: float) -> float:
    if df <= 0:
        raise ValueError("df must be positive")
    if x <= 0:
        return 1.0
    a, z = df / 2.0, x / 2.0
    if z < a + 1.0:
        return min(1.0, max(0.0, 1.0 - _gser(a, z)))
    return min(1.0, max(0.0, _gcf(a, z)))


def pool_categories(ref_counts: dict, cmp_counts: dict,
                    min_expected: float = CHI_MIN_EXPECTED) -> list:
    """The categories after pooling: every category whose expected count in
    either window is below `min_expected` goes to OTHER; if OTHER itself is
    still below it, OTHER absorbs the smallest remaining category, until
    every cell qualifies or one category is left. Deterministic (ties by
    name). Returns [(label, ref, cmp)]."""
    cats = sorted(set(ref_counts) | set(cmp_counts), key=str)
    nr = float(sum(ref_counts.values()))
    nc = float(sum(cmp_counts.values()))
    n = nr + nc
    if nr <= 0 or nc <= 0:
        raise ValueError("empty window")
    need = min_expected * n / min(nr, nc)
    cells = [(k, float(ref_counts.get(k, 0.0)), float(cmp_counts.get(k, 0.0)))
             for k in cats]
    keep = [c for c in cells if c[1] + c[2] >= need]
    other = [c for c in cells if c[1] + c[2] < need]
    o_r, o_c = sum(c[1] for c in other), sum(c[2] for c in other)
    keep.sort(key=lambda c: (c[1] + c[2], str(c[0])))
    while other and o_r + o_c < need and keep:
        k = keep.pop(0)
        o_r, o_c = o_r + k[1], o_c + k[2]
    out = sorted(keep, key=lambda c: str(c[0]))
    if other or (o_r + o_c) > 0:
        out.append((OTHER, o_r, o_c))
    return out


def chi_square_homogeneity(ref_counts: dict, cmp_counts: dict,
                           min_expected: float = CHI_MIN_EXPECTED) -> dict:
    cells = pool_categories(ref_counts, cmp_counts, min_expected)
    nr = sum(c[1] for c in cells)
    nc = sum(c[2] for c in cells)
    n = nr + nc
    k = len(cells)
    if k < 2:
        return {"chi2": 0.0, "df": 0, "p": 1.0, "cells": cells,
                "note": "ONE_CATEGORY_AFTER_POOLING_NO_MIX_TO_COMPARE"}
    stat = 0.0
    for _lab, r, c in cells:
        col = r + c
        er, ec = nr * col / n, nc * col / n
        stat += (r - er) ** 2 / er + (c - ec) ** 2 / ec
    df = k - 1
    return {"chi2": stat, "df": df, "p": chi2_sf(stat, df), "cells": cells,
            "note": None}


def tvd(ref_counts: dict, cmp_counts: dict) -> float:
    nr = float(sum(ref_counts.values()))
    nc = float(sum(cmp_counts.values()))
    if nr <= 0 or nc <= 0:
        raise ValueError("empty window")
    cats = set(ref_counts) | set(cmp_counts)
    return 0.5 * sum(abs(ref_counts.get(k, 0.0) / nr
                         - cmp_counts.get(k, 0.0) / nc) for k in cats)


# ═════════════════════════════════════════════════════════════════════
# MULTIPLE TESTING
# ═════════════════════════════════════════════════════════════════════

def bh_adjust(pvalues: list) -> list:
    """Benjamini-Hochberg step-up adjusted p-values (q-values), in the input
    order: q_(i) = min_{j >= i} m p_(j) / j, capped at 1."""
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: (pvalues[i], i))
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, pvalues[i] * m / rank)
        # q_i >= p_i mathematically (m / rank >= 1 over p_(j) >= p_(i));
        # max() only removes a last-ulp float rounding below p_i
        q[i] = min(1.0, max(running, pvalues[i]))
    return q


# ═════════════════════════════════════════════════════════════════════
# BOOTSTRAP
# ═════════════════════════════════════════════════════════════════════

def seed_for(*parts) -> int:
    return int(hashlib.sha256(":".join(str(p) for p in parts).encode()
                              ).hexdigest()[:12], 16)


def systematic_subsample(sorted_xs: list, cap: int) -> list:
    n = len(sorted_xs)
    if n <= cap:
        return list(sorted_xs)
    return [sorted_xs[min(n - 1, int((i + 0.5) * n / cap))]
            for i in range(cap)]


def bootstrap_quantile_shift(ref, cmp, q: float, *, seed: int,
                             b: int = BOOT_B, cap: int = BOOT_CAP,
                             level: float = BOOT_LEVEL) -> dict:
    r = systematic_subsample(sorted(_finite(ref)), cap)
    c = systematic_subsample(sorted(_finite(cmp)), cap)
    if not r or not c:
        raise ValueError("empty window")
    shift = quantile(c, q) - quantile(r, q)
    rng = random.Random(seed)
    nr, nc = len(r), len(c)
    stats = []
    for _ in range(b):
        rr = sorted(r[rng.randrange(nr)] for _ in range(nr))
        cc = sorted(c[rng.randrange(nc)] for _ in range(nc))
        stats.append(quantile(cc, q) - quantile(rr, q))
    stats.sort()
    a = (1.0 - level) / 2.0
    return {"q": q, "ref": quantile(r, q), "cmp": quantile(c, q),
            "shift": shift, "lo": quantile(stats, a),
            "hi": quantile(stats, 1.0 - a), "b": b, "level": level,
            "subsampled": len(r) < len(_finite(ref))
            or len(c) < len(_finite(cmp))}
