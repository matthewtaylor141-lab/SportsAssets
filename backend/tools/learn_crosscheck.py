"""CROSS-CHECK THE KERNEL AGAINST ESTABLISHED IMPLEMENTATIONS.

WHY THIS EXISTS. `learn/kernel.py` is hand-written because the deployed
image has no numerical library. That is a deployment constraint, not a
licence to trust my own arithmetic: a hand-rolled IRLS with a sign
error passes every self-consistency test ever written for it. So the
kernel is checked against scikit-learn, scipy and numpy -- which needs
NO production dependency change, because this script runs only in a
development environment where those are installed separately.

    pip install --target /tmp/mlcheck numpy scikit-learn scipy
    PYTHONPATH=/tmp/mlcheck python backend/tools/learn_crosscheck.py

It is deliberately NOT a pytest file. The repository's suite runs in an
environment without these packages, and a test that skips when its
dependency is missing is a test that silently never runs.

WHAT IS COMPARED, and the objectives matched exactly:

    Ridge      vs sklearn LogisticRegression(C = 1/l2, lbfgs).
               sklearn minimises 0.5||w||^2 + C * SUM log-loss; the
               kernel minimises SUM log-loss + 0.5*l2*||w||^2, so
               C = 1/l2 and the two objectives are identical. Features
               are pre-standardised for sklearn using the kernel's own
               centre and scale, because the kernel standardises
               internally and a penalty means something different on a
               raw scale.
    Isotonic   vs sklearn IsotonicRegression(increasing=True)
    Hazard     its inner logistic vs sklearn on the same person-period
               design, AND its survival curve vs a hand-computed
               Kaplan-Meier on a no-covariate case
    metrics    vs sklearn log_loss, brier_score_loss, roc_auc_score

THE FAILURE CASES ARE THE POINT. Agreement on well-conditioned data is
cheap. The cases below are chosen to break things: perfect separation,
collinear columns, features differing by ten orders of magnitude,
extreme weights, single-class labels and ties everywhere.
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sportsassets.learn import kernel as K          # noqa: E402
from sportsassets.learn import metrics as M         # noqa: E402

try:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import log_loss as sk_log_loss
    from sklearn.metrics import brier_score_loss, roc_auc_score
except ImportError as exc:                                     # pragma: no cover
    raise SystemExit(
        "this script needs numpy/scikit-learn, which are DEV-ONLY here:\n"
        "  pip install --target /tmp/mlcheck numpy scikit-learn scipy\n"
        "  PYTHONPATH=/tmp/mlcheck python %s\n(%s)" % (sys.argv[0], exc))

RESULTS = []


def check(name, ok, detail):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print("%-4s %-52s %s" % ("PASS" if ok else "FAIL", name, detail))


def _standardise(X):
    """The kernel's own standardisation, applied outside it."""
    n, d = len(X), len(X[0])
    c = [sum(r[j] for r in X) / n for j in range(d)]
    s = []
    for j in range(d):
        var = sum((r[j] - c[j]) ** 2 for r in X) / n
        s.append(math.sqrt(var) if var > 1e-18 else 1.0)
    return [[(r[j] - c[j]) / s[j] for j in range(d)] for r in X], c, s


# ── 1. logistic regression, on data that behaves ─────────────────────

def case_logistic(name, X, y, l2, tol_coef=1e-4, tol_pred=1e-5):
    feats = ["f%d" % j for j in range(len(X[0]))]
    rows = [dict(zip(feats, r)) for r in X]
    m = K.Ridge(feats, l2=l2, max_iter=300, tol=1e-12).fit(rows, y)

    Xs, _, _ = _standardise(X)
    sk = LogisticRegression(C=1.0 / l2, solver="lbfgs", max_iter=20000,
                            tol=1e-12, fit_intercept=True)
    sk.fit(np.array(Xs), np.array(y))

    dc = max(abs(m.coef[j] - sk.coef_[0][j]) for j in range(len(feats)))
    di = abs(m.intercept - sk.intercept_[0])
    pk = m.predict_many(rows)
    ps = sk.predict_proba(np.array(Xs))[:, 1]
    dp = max(abs(pk[i] - ps[i]) for i in range(len(y)))
    ok = dc < tol_coef and di < tol_coef and dp < tol_pred
    check(name, ok, "max|dcoef|=%.3g max|dintercept|=%.3g max|dp|=%.3g"
          % (dc, di, dp))
    return dc, di, dp


def logistic_suite():
    # A. ordinary, well-conditioned, two informative features.
    X, y = [], []
    for i in range(400):
        a = -2.0 + 4.0 * (i % 20) / 19.0
        b = -1.0 + 2.0 * ((i // 20) % 20) / 19.0
        X.append([a, b])
        y.append(1 if (1.3 * a - 0.8 * b + 0.2) > 0 and (i % 7) else 0)
    case_logistic("logistic / well-conditioned", X, y, l2=1.0)

    # B. PERFECT SEPARATION. The unpenalised MLE is at infinity; both
    #    implementations must land on the same finite penalised answer.
    Xs = [[float(v)] for v in (-4, -3, -2, -1, 1, 2, 3, 4)]
    ys = [0, 0, 0, 0, 1, 1, 1, 1]
    case_logistic("logistic / perfect separation", Xs, ys, l2=1.0)

    # C. COLLINEAR COLUMNS. f1 = 2*f0 exactly. The Hessian is singular
    #    without the ridge; with it the answer is unique and shared.
    Xc = [[float(i), 2.0 * i] for i in range(200)]
    yc = [1 if i > 90 else 0 for i in range(200)]
    case_logistic("logistic / exactly collinear columns", Xc, yc, l2=1.0)

    # D. TEN ORDERS OF MAGNITUDE APART. Standardisation is what makes
    #    the penalty comparable; without it the small feature is
    #    effectively unpenalised and the fits diverge.
    Xw = [[1e-6 * i, 1e6 * (i % 13)] for i in range(300)]
    yw = [1 if (i % 13) > 6 else 0 for i in range(300)]
    case_logistic("logistic / scales 1e-6 vs 1e6", Xw, yw, l2=1.0)

    # E. A HEAVY PENALTY, which should shrink everything towards the
    #    base rate in both.
    case_logistic("logistic / heavy penalty l2=100", X, y, l2=100.0)

    # F. A LIGHT PENALTY on separable data -- the hardest numerical
    #    case in the set, and where an unstable IRLS diverges.
    case_logistic("logistic / separable, light penalty l2=0.01",
                  Xs, ys, l2=0.01, tol_coef=1e-3, tol_pred=1e-4)

    # G. CONSTANT FEATURE. Zero variance -> scale 1.0 in the kernel;
    #    sklearn sees an all-zero standardised column. Both must return
    #    the base rate.
    Xk = [[1.0, float(i % 2)] for i in range(100)]
    yk = [1 if (i % 2) else 0 for i in range(100)]
    case_logistic("logistic / one constant feature", Xk, yk, l2=1.0)


# ── 2. isotonic ──────────────────────────────────────────────────────

def isotonic_suite():
    cases = {
        "isotonic / classic inversion":
            ([1.0, 2.0, 3.0, 4.0], [0.0, 1.0, 0.0, 1.0]),
        "isotonic / ties at one score":
            ([1.0, 1.0, 1.0, 2.0, 3.0], [0.0, 1.0, 1.0, 0.0, 1.0]),
        "isotonic / already monotone":
            ([0.1, 0.2, 0.3, 0.4], [0.0, 0.0, 1.0, 1.0]),
        "isotonic / fully reversed":
            ([1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 1.0, 1.0, 0.0, 0.0]),
        "isotonic / long noisy run":
            ([i / 60.0 for i in range(61)],
             [float((i * 7 % 11) < 5) for i in range(61)]),
    }
    # OURS IS PAV PLUS A SHARED AFFINE SHRINK, so the comparison has to
    # undo the shrink before it means anything. Undoing it is exact --
    # (y*(n+1) - 0.5) / n -- and if the PAV pass itself had drifted, the
    # unshrunk values would not land on sklearn's.
    def unshrink(v, n):
        return (v * (n + 1.0) - 0.5) / n

    for name, (x, yv) in cases.items():
        c = K.Isotonic().fit(x, yv)
        sk = IsotonicRegression(increasing=True, out_of_bounds="clip")
        sk.fit(np.array(x), np.array(yv))
        mine = [unshrink(c.predict(v), len(x)) for v in x]
        theirs = list(sk.predict(np.array(x)))
        d = max(abs(mine[i] - theirs[i]) for i in range(len(x)))
        check(name, d < 1e-9, "max|d|=%.3g (unshrunk)" % d)

    # Clipping outside the fitted range must agree too.
    c = K.Isotonic().fit([0.2, 0.5, 0.8], [0.0, 0.5, 1.0])
    sk = IsotonicRegression(increasing=True, out_of_bounds="clip")
    sk.fit(np.array([0.2, 0.5, 0.8]), np.array([0.0, 0.5, 1.0]))
    d = max(abs(unshrink(c.predict(v), 3) - float(sk.predict(np.array([v]))[0]))
            for v in (-3.0, 0.0, 0.35, 0.65, 1.0, 9.0))
    check("isotonic / outside the fitted range", d < 1e-9,
          "max|d|=%.3g (unshrunk)" % d)

    # AND THE SHRINK ITSELF: sklearn does NOT do it, and that is the
    # point -- its curve asserts p = 0 on a block of zeros. Ours must
    # differ from sklearn exactly there and nowhere else.
    x = [float(i) for i in range(40)]
    yv = [0.0] * 20 + [1.0] * 20
    c = K.Isotonic().fit(x, yv)
    sk = IsotonicRegression(increasing=True, out_of_bounds="clip")
    sk.fit(np.array(x), np.array(yv))
    theirs = list(sk.predict(np.array(x)))
    check("isotonic / sklearn does assert certainty here",
          min(theirs) == 0.0 and max(theirs) == 1.0,
          "sklearn min=%.3g max=%.3g" % (min(theirs), max(theirs)))
    check("isotonic / ours does not",
          min(c.y) > 0.0 and max(c.y) < 1.0,
          "ours min=%.6g max=%.6g" % (min(c.y), max(c.y)))
    d = max(abs(unshrink(c.predict(v), len(x)) - theirs[i])
            for i, v in enumerate(x))
    check("isotonic / the difference is exactly the shared affine map",
          d < 1e-12, "max|d|=%.3g" % d)


# ── 3. hazard ────────────────────────────────────────────────────────

def hazard_suite():
    edges = [60.0, 300.0, 900.0, 3600.0]
    rows, times, obs = [], [], []
    for i in range(600):
        x = float(i % 5)
        rows.append({"x": x})
        # deterministic, covariate-dependent timing
        times.append(60.0 * (1 + (i * 3 + int(x)) % 40))
        obs.append((i % 4) != 0)
    h = K.Hazard(["x"], buckets=edges, l2=1.0).fit(rows, times, obs)

    # The inner logistic, on the SAME expansion, against sklearn.
    er, ey, _ = h._expand(rows, times, obs)
    feats = h.inner.features
    X = [[float(r[f]) for f in feats] for r in er]
    Xs, _, _ = _standardise(X)
    sk = LogisticRegression(C=1.0, solver="lbfgs", max_iter=20000,
                            tol=1e-12)
    sk.fit(np.array(Xs), np.array(ey))
    dc = max(abs(h.inner.coef[j] - sk.coef_[0][j]) for j in range(len(feats)))
    check("hazard / inner logistic vs sklearn", dc < 1e-4,
          "max|dcoef|=%.3g over %d person-periods" % (dc, len(er)))

    # NO-COVARIATE SURVIVAL vs KAPLAN-MEIER, computed by hand. This is
    # the check that the person-period expansion is the right shape:
    # if censored subjects were counted as negatives the two curves
    # would separate immediately.
    t2, o2 = [], []
    plan = [(60.0, True, 120), (300.0, True, 90), (300.0, False, 40),
            (900.0, True, 60), (3600.0, True, 30), (9999.0, False, 160)]
    for t, ob, n in plan:
        t2 += [t] * n
        o2 += [ob] * n
    r2 = [{"x": 0.0}] * len(t2)
    h2 = K.Hazard(["x"], buckets=edges, l2=1e-8).fit(r2, t2, o2)

    at_risk, surv, km = len(t2), 1.0, []
    for k, e in enumerate(edges):
        events = sum(n for (t, ob, n) in plan if ob and t <= e
                     and (k == 0 or t > edges[k - 1]))
        cens = sum(n for (t, ob, n) in plan if not ob and t <= e
                   and (k == 0 or t > edges[k - 1]))
        if at_risk > 0:
            surv *= (1.0 - events / at_risk)
        km.append(1.0 - surv)
        at_risk -= (events + cens)
    mine = [h2.p_by({"x": 0.0}, k) for k in range(len(edges))]
    d = max(abs(mine[k] - km[k]) for k in range(len(edges)))
    check("hazard / survival vs hand-computed Kaplan-Meier", d < 0.02,
          "max|d|=%.3g; mine=%s km=%s"
          % (d, [round(v, 4) for v in mine], [round(v, 4) for v in km]))


# ── 4. metrics ───────────────────────────────────────────────────────

def metrics_suite():
    p = [0.02, 0.14, 0.31, 0.5, 0.62, 0.77, 0.88, 0.95, 0.3, 0.3]
    y = [0, 0, 1, 0, 1, 1, 1, 1, 1, 0]
    d = abs(M.log_loss(p, y) - sk_log_loss(np.array(y), np.array(p)))
    check("metrics / log loss vs sklearn", d < 1e-12, "|d|=%.3g" % d)
    d = abs(M.brier(p, y) - brier_score_loss(np.array(y), np.array(p)))
    check("metrics / brier vs sklearn", d < 1e-12, "|d|=%.3g" % d)
    d = abs(M.auc(p, y)["auc"] - roc_auc_score(np.array(y), np.array(p)))
    check("metrics / AUC with ties vs sklearn", d < 1e-12, "|d|=%.3g" % d)

    # A CONSTANT SCORE must be AUC 0.5 in both -- the case where
    # counting ties as concordant would report 1.0.
    pc = [0.4] * 10
    d = abs(M.auc(pc, y)["auc"] - roc_auc_score(np.array(y), np.array(pc)))
    check("metrics / AUC on a constant score", d < 1e-12, "|d|=%.3g" % d)

    # SINGLE CLASS. Neither may return a usable number, and this check
    # was WRONG ABOUT SKLEARN the first time: it asserted a raise, and
    # sklearn 1.9 warns and returns nan instead. Both behaviours refuse
    # -- the point is that no caller can read a score out of it -- but
    # nan is the weaker refusal, because nan propagates silently through
    # a comparison while the kernel's UNDEFINED carries the reason with
    # it. The check now asserts the property that matters.
    r = M.auc([0.1, 0.9], [1, 1])
    sk_usable = True
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            v = roc_auc_score(np.array([1, 1]), np.array([0.1, 0.9]))
        sk_usable = not (isinstance(v, float) and math.isnan(v))
    except ValueError:
        sk_usable = False
    check("metrics / single-class AUC yields no usable number",
          r["auc"] is None and r["status"] == "UNDEFINED"
          and not sk_usable,
          "kernel=%s (+reason); sklearn returns nan or raises"
          % r["status"])

    # Weighted log loss.
    w = [3.0, 1.0, 1.0, 2.0, 1.0, 1.0, 1.0, 1.0, 4.0, 1.0]
    d = abs(M.log_loss(p, y, w)
            - sk_log_loss(np.array(y), np.array(p), sample_weight=np.array(w)))
    check("metrics / weighted log loss vs sklearn", d < 1e-12, "|d|=%.3g" % d)


# ── 5. the failure cases the kernel is supposed to refuse ────────────

def refusal_suite():
    def refuses(fn, exc=Exception):
        try:
            fn()
            return False
        except exc:
            return True

    check("refusal / a None feature value",
          refuses(lambda: K.Ridge(["x"], l2=1.0).fit(
              [{"x": 1.0}, {"x": None}], [1, 0]), ValueError),
          "ValueError raised rather than a silent zero")
    check("refusal / NaN feature value",
          refuses(lambda: K.Ridge(["x"], l2=1.0).fit(
              [{"x": 1.0}, {"x": float('nan')}], [1, 0]), ValueError),
          "ValueError raised")
    check("refusal / a label outside [0,1]",
          refuses(lambda: K.Ridge(["x"], l2=1.0).fit(
              [{"x": 1.0}], [7.0]), ValueError), "ValueError raised")
    check("refusal / zero rows",
          refuses(lambda: K.Ridge(["x"], l2=1.0).fit([], []), ValueError),
          "ValueError raised")
    check("refusal / a non-positive ridge",
          refuses(lambda: K.Ridge(["x"], l2=0.0), ValueError),
          "ValueError raised; an unpenalised fit is not offered")
    check("refusal / a model from a different kernel version",
          refuses(lambda: K.load({"kind": "RIDGE_LOGISTIC",
                                  "kernel": "OTHER_V9"}), ValueError),
          "ValueError raised")

    # EXTREME WEIGHTS must not produce a non-finite fit.
    rows = [{"x": float(i)} for i in range(50)]
    y = [1 if i > 25 else 0 for i in range(50)]
    w = [1e9 if i == 0 else 1e-9 for i in range(50)]
    m = K.Ridge(["x"], l2=1.0).fit(rows, y, w)
    ok = all(math.isfinite(c) for c in m.coef) and math.isfinite(m.intercept)
    check("stability / weights spanning 1e18", ok,
          "coef=%s intercept=%.6g" % ([round(c, 6) for c in m.coef],
                                      m.intercept))

    # ALL ONE CLASS: a degenerate but legal fit. It must be finite and
    # must predict near that class, not NaN.
    m = K.Ridge(["x"], l2=1.0).fit(rows, [1.0] * 50)
    p = m.predict({"x": 10.0})
    check("stability / all-one-class labels",
          math.isfinite(p) and p > 0.9, "p=%.6f" % p)

    # A 40-FEATURE DESIGN -- the Cholesky path at a realistic width.
    rows = [{("f%d" % j): float((i * (j + 3)) % 17) for j in range(40)}
            for i in range(500)]
    y = [1 if (i % 3) else 0 for i in range(500)]
    m = K.Ridge(["f%d" % j for j in range(40)], l2=1.0).fit(rows, y)
    check("stability / 40 features, 500 rows",
          all(math.isfinite(c) for c in m.coef) and m.converged,
          "converged in %d IRLS iterations" % m.n_iter)


def main() -> int:
    print("kernel:", K.VERSION, "| numpy", np.__version__)
    print()
    logistic_suite()
    isotonic_suite()
    hazard_suite()
    metrics_suite()
    refusal_suite()
    n_ok = sum(1 for r in RESULTS if r["ok"])
    print()
    print("%d/%d checks passed" % (n_ok, len(RESULTS)))
    out = {"kernel_version": K.VERSION, "numpy": np.__version__,
           "checks": RESULTS, "passed": n_ok, "total": len(RESULTS)}
    dest = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        "research/beta48/learning/CROSSCHECK.json")
    with open(dest, "w") as fh:
        json.dump(out, fh, indent=1)
    print("written:", dest)
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
