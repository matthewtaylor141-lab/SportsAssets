"""A small, pure-Python ridge logistic regression with a Laplace posterior.

WHY HERE. numpy is not in the image (requirements.lock), and the meta-models
need a fitted probability WITH its uncertainty. The one-dimensional
recalibration is intel/calibration.fit_overlay (reused for M1 and M5); this
module is the multi-feature generalisation for M2 and the edge-confidence
meta-model: Newton-Raphson (IRLS) on the penalised log likelihood, features
standardised on the TRAINING rows only (their means and scales are frozen
into the registered parameters, so a forward row is never used to scale
itself), and the inverse Hessian at the optimum as the Laplace covariance of
the coefficients -- which gives the standard error of a forward row's linear
predictor, and so a confidence on any statement about its probability.

Deterministic: no randomness anywhere. Bounded: d <= ~20 features, n capped
by the caller.
"""
from __future__ import annotations

import math

from . import common as C


def _solve(a, b):
    """Gauss-Jordan with partial pivoting: x with a x = b. None if singular."""
    n = len(a)
    m = [list(map(float, a[i])) + [float(b[i])] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        p = m[col][col]
        m[col] = [v / p for v in m[col]]
        for r in range(n):
            if r != col and m[r][col] != 0.0:
                f = m[r][col]
                m[r] = [vr - f * vc for vr, vc in zip(m[r], m[col])]
    return [m[i][n] for i in range(n)]


def invert(a):
    n = len(a)
    cols = []
    for j in range(n):
        e = [1.0 if i == j else 0.0 for i in range(n)]
        x = _solve(a, e)
        if x is None:
            return None
        cols.append(x)
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def standardizer(rows, names):
    """{name: (mean, scale)} over the rows where the feature is present.
    A feature never present gets (0, 1) and is reported unused."""
    out = {}
    for k in names:
        xs = [r[k] for r in rows if r.get(k) is not None]
        if len(xs) < 2:
            out[k] = (0.0, 1.0, 0)
            continue
        m = sum(xs) / len(xs)
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
        out[k] = (m, sd if sd > 1e-9 else 1.0, len(xs))
    return out


def design(row, names, std):
    """[1, z_1 .. z_d]; a missing feature sits at its training mean (z=0)."""
    x = [1.0]
    for k in names:
        v = row.get(k)
        m, s = std[k][0], std[k][1]
        x.append(0.0 if v is None else (float(v) - m) / s)
    return x


def _objective(w, xs, ys, ridge):
    """Penalised negative log likelihood (intercept unpenalised)."""
    total = 0.0
    for x, y in zip(xs, ys):
        z = sum(wi * xi for wi, xi in zip(w, x))
        # log(1 + e^z) - y z, computed stably
        total += (z if z > 0 else 0.0) + math.log1p(math.exp(-abs(z))) \
            - y * z
    return total + 0.5 * ridge * sum(v * v for v in w[1:])


def nll(pairs):
    """Mean negative log likelihood of (p, o) pairs (for fit validation)."""
    tot = 0.0
    for p, o in pairs:
        q = min(max(float(p), 1e-12), 1 - 1e-12)
        tot -= math.log(q) if o else math.log(1 - q)
    return tot / max(1, len(pairs))


def fit(rows, ys, names, *, ridge=1.0, iters=50):
    """Fit; returns the frozen parameter dict or None (too few rows, one
    class only, or a singular system)."""
    n = len(rows)
    if n < 2 * (len(names) + 1) or len(set(ys)) < 2:
        return None
    std = standardizer(rows, names)
    xs = [design(r, names, std) for r in rows]
    d = len(names) + 1
    w = [0.0] * d
    hess = None
    for _ in range(iters):
        g = [0.0] * d
        h = [[0.0] * d for _ in range(d)]
        for x, y in zip(xs, ys):
            q = C.sigmoid(sum(wi * xi for wi, xi in zip(w, x)))
            r = q - y
            v = q * (1.0 - q)
            for i in range(d):
                g[i] += r * x[i]
                vi = v * x[i]
                hi = h[i]
                for j in range(i, d):
                    hi[j] += vi * x[j]
        for i in range(d):
            for j in range(i):
                h[i][j] = h[j][i]
        for i in range(1, d):              # no penalty on the intercept
            g[i] += ridge * w[i]
            h[i][i] += ridge
        step = _solve(h, g)
        if step is None:
            return None
        # DAMPED: halve the Newton step until the penalised likelihood
        # does not get worse (plain Newton can overshoot on extreme data)
        base = _objective(w, xs, ys, ridge)
        t = 1.0
        for _ in range(30):
            cand = [wi - t * si for wi, si in zip(w, step)]
            if _objective(cand, xs, ys, ridge) <= base + 1e-12:
                break
            t /= 2.0
        w = cand
        hess = h
        if max(abs(t * s) for s in step) < 1e-9:
            break
    cov = invert(hess) if hess is not None else None
    if cov is None:
        return None
    return {"names": list(names),
            "standardizer": {k: [C.rnd(std[k][0], 10), C.rnd(std[k][1], 10),
                                 std[k][2]] for k in names},
            "coef": [C.rnd(v, 10) for v in w],
            "cov": [[C.rnd(v, 12) for v in row] for row in cov],
            "ridge": ridge, "n": n, "positives": int(sum(ys))}


def linear(params, row):
    """(eta, se_eta, missing) for one row under frozen params."""
    names = params["names"]
    std = {k: (v[0], v[1]) for k, v in params["standardizer"].items()}
    x = design(row, names, std)
    w = params["coef"]
    eta = sum(wi * xi for wi, xi in zip(w, x))
    cov = params["cov"]
    var = sum(x[i] * sum(cov[i][j] * x[j] for j in range(len(x)))
              for i in range(len(x)))
    missing = [k for k in names if row.get(k) is None]
    return eta, math.sqrt(max(0.0, var)), missing


def predict(params, row, z=1.959964):
    eta, se, missing = linear(params, row)
    return {"p": C.sigmoid(eta), "p_lo": C.sigmoid(eta - z * se),
            "p_hi": C.sigmoid(eta + z * se), "eta": eta, "se_eta": se,
            "missing": missing}
