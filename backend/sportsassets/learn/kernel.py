"""THE LEARNING KERNEL. Fitted parameters, in the standard library.

WHY THIS EXISTS AT ALL, stated plainly because the audit found it and
the mandate names it: before this file there was no code in this
repository that fitted a parameter to data. There were dataset
interfaces, a model registry, a promotion gate and a learning ledger --
all careful, all correct, and all of them explicitly saying NOTHING IS
TRAINED HERE. An agent reading records is not a learning system and
neither is a hand-written rule with a version number.

WHY THE STANDARD LIBRARY. `numpy` is not a dependency of
`backend/pyproject.toml` and is not installed in the deployed image.
Adding one is a production change that needs its own authorization, and
the mandate asks for interpretable baselines FIRST. All four estimators
below are a few hundred lines, exactly reproducible, and readable by
somebody who has to defend a number to a regulator:

    Ridge        L2 logistic regression, fitted by IRLS
    Isotonic     the PAV calibrator -- monotone, non-parametric
    Stumps       gradient-boosted depth-1 trees
    Hazard       discrete-time hazard, which is Ridge over a
                 person-period expansion, and is how censoring is
                 handled honestly

A numerical library can be added later. Nothing here would have to be
thrown away if it is: these are the baselines a heavier model must beat
before it is allowed anywhere near a decision.

────────────────────────────────────────────────────────────────────
DETERMINISM IS A REQUIREMENT, NOT A PREFERENCE.

Every fit in this file is a pure function of (rows, hyper-parameters).
There is no randomness anywhere -- no shuffling, no random init, no
sampling. Two runs over the same rows produce bit-identical
coefficients, which is what makes a model version mean something and a
promotion reproducible. Where an algorithm would normally want
randomness (boosting subsample), the deterministic variant is used
instead and the cost is noted.

FEATURE ORDER IS PART OF THE MODEL. A model is stored with the exact
ordered list of feature names it was fitted on, and scoring refuses a
row that cannot supply them. A silently reordered vector is a model
that returns confident nonsense.
"""
from __future__ import annotations

import math

VERSION = "BETTOR_LEARN_KERNEL_V1"

# Beyond this the logistic saturates in float and the gradient is zero;
# clipping keeps the IRLS weights positive so the solve stays defined.
_Z_CLIP = 30.0

# The smallest IRLS weight we will admit. p(1-p) underflows to exactly
# 0.0 for |z| around 37, and a zero weight silently drops a row from
# the normal equations -- the fit then reports convergence on a subset
# it never told anybody about.
_MIN_W = 1e-10


# ── small linear algebra, written out ────────────────────────────────

def _cholesky(a: list) -> list:
    """Lower-triangular L with L Lᵀ = a. Raises if a is not PD.

    Used instead of a general solver because the matrices here are
    Gram matrices plus a positive ridge, so they ARE positive definite
    by construction -- and if one is not, that is a bug worth raising
    rather than a condition worth limping through.
    """
    n = len(a)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] - sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                if s <= 0.0:
                    raise ValueError(
                        "matrix is not positive definite at pivot %d "
                        "(got %.6g); the ridge is too small for this "
                        "design" % (i, s))
                L[i][i] = math.sqrt(s)
            else:
                L[i][j] = s / L[j][j]
    return L


def _chol_solve(L: list, b: list) -> list:
    """Solve L Lᵀ x = b by forward then back substitution."""
    n = len(L)
    y = [0.0] * n
    for i in range(n):
        y[i] = (b[i] - sum(L[i][k] * y[k] for k in range(i))) / L[i][i]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (y[i] - sum(L[k][i] * x[k] for k in range(i + 1, n))) / L[i][i]
    return x


def _sigmoid(z: float) -> float:
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-min(z, _Z_CLIP)))
    e = math.exp(max(z, -_Z_CLIP))
    return e / (1.0 + e)


# ── L2 logistic regression ───────────────────────────────────────────

class Ridge:
    """L2-penalised logistic regression, fitted by IRLS.

    WHY IRLS AND NOT GRADIENT DESCENT. Newton's method on the logistic
    likelihood converges in under a dozen iterations for the problem
    sizes here and needs no learning rate -- and a learning rate is a
    hyper-parameter whose value would have to be defended. The cost is
    one d×d solve per iteration, which for d in the tens is nothing.

    WHY THE PENALTY IS NOT OPTIONAL. Separable data drives unpenalised
    logistic coefficients to infinity, and separation is common here:
    a market type that never completed, an account that always bought
    the favourite. The ridge keeps the fit finite and the Hessian
    positive definite in the same stroke. THE INTERCEPT IS NOT
    PENALISED -- shrinking it would bias the base rate, which is the
    one quantity we most want reported honestly.

    STANDARDISATION IS PART OF THE MODEL. Features are centred and
    scaled by the TRAINING mean and deviation, both stored; a feature
    with zero variance in training gets scale 1.0 and contributes only
    through its (penalised) coefficient. Applying a penalty to raw
    features would make the penalty mean something different for a
    price in [0,1] than for a size in the thousands.
    """

    def __init__(self, features, *, l2=1.0, max_iter=50, tol=1e-9):
        if not features:
            raise ValueError("a model needs at least one feature")
        if len(set(features)) != len(features):
            raise ValueError("duplicate feature names: %r" % (features,))
        if l2 <= 0.0:
            raise ValueError("l2 must be positive; see the docstring")
        self.features = list(features)
        self.l2 = float(l2)
        self.max_iter = int(max_iter)
        self.tol = float(tol)
        self.coef = None          # one per feature, on the SCALED scale
        self.intercept = 0.0
        self.center = None
        self.scale = None
        self.n_iter = 0
        self.converged = False
        self.n_rows = 0
        self.pos_rate = None

    # -- the design matrix -------------------------------------------
    def _raw(self, rows) -> list:
        out = []
        for r in rows:
            v = []
            for f in self.features:
                if f not in r:
                    raise KeyError(
                        "row is missing feature %r; a model scores the "
                        "features it was fitted on or it does not score" % f)
                x = r[f]
                if x is None or (isinstance(x, float) and math.isnan(x)):
                    raise ValueError(
                        "feature %r is missing in this row. Impute it "
                        "explicitly upstream and carry the indicator -- "
                        "silently treating absence as zero is how a "
                        "missing measurement becomes a confident one" % f)
                v.append(float(x))
            out.append(v)
        return out

    def _standardise(self, X) -> None:
        n, d = len(X), len(self.features)
        self.center = [0.0] * d
        self.scale = [1.0] * d
        for j in range(d):
            col = [X[i][j] for i in range(n)]
            m = sum(col) / n
            var = sum((c - m) ** 2 for c in col) / n
            self.center[j] = m
            # A constant feature keeps scale 1.0 rather than exploding.
            self.scale[j] = math.sqrt(var) if var > 1e-18 else 1.0

    def _apply(self, v) -> list:
        return [(v[j] - self.center[j]) / self.scale[j]
                for j in range(len(v))]

    # -- fitting -----------------------------------------------------
    def fit(self, rows, labels, weights=None):
        if len(rows) != len(labels):
            raise ValueError("rows and labels differ in length")
        if not rows:
            raise ValueError("cannot fit on zero rows")
        y = [float(v) for v in labels]
        for v in y:
            if v < 0.0 or v > 1.0:
                raise ValueError("labels must be in [0, 1]; got %r" % v)
        w = [1.0] * len(rows) if weights is None else [float(x)
                                                       for x in weights]
        if any(x < 0.0 for x in w):
            raise ValueError("weights must be non-negative")

        Xr = self._raw(rows)
        self._standardise(Xr)
        X = [self._apply(v) for v in Xr]
        n, d = len(X), len(self.features)
        self.n_rows = n
        tw = sum(w) or 1.0
        self.pos_rate = sum(w[i] * y[i] for i in range(n)) / tw

        # The intercept rides in column 0 and is NOT penalised.
        beta = [0.0] * (d + 1)
        # Start at the base rate rather than at zero: it is the right
        # answer when no feature carries information, and it is where
        # a separable problem's intercept wants to go anyway.
        p0 = min(max(self.pos_rate, 1e-6), 1.0 - 1e-6)
        beta[0] = math.log(p0 / (1.0 - p0))

        for it in range(self.max_iter):
            H = [[0.0] * (d + 1) for _ in range(d + 1)]
            g = [0.0] * (d + 1)
            for i in range(n):
                if w[i] == 0.0:
                    continue
                z = beta[0] + sum(beta[j + 1] * X[i][j] for j in range(d))
                p = _sigmoid(z)
                wi = w[i] * max(p * (1.0 - p), _MIN_W)
                ri = w[i] * (y[i] - p)
                xi = [1.0] + X[i]
                for a in range(d + 1):
                    g[a] += ri * xi[a]
                    xa = wi * xi[a]
                    for b in range(a + 1):
                        H[a][b] += xa * xi[b]
            # symmetrise and add the ridge (never to the intercept)
            for a in range(d + 1):
                for b in range(a):
                    H[b][a] = H[a][b]
            for a in range(1, d + 1):
                H[a][a] += self.l2
                g[a] -= self.l2 * beta[a]

            step = _chol_solve(_cholesky(H), g)
            beta = [beta[a] + step[a] for a in range(d + 1)]
            self.n_iter = it + 1
            if max(abs(s) for s in step) < self.tol:
                self.converged = True
                break

        self.intercept = beta[0]
        self.coef = beta[1:]
        return self

    # -- scoring -----------------------------------------------------
    def decision(self, row) -> float:
        if self.coef is None:
            raise RuntimeError("model is not fitted")
        v = self._apply(self._raw([row])[0])
        return self.intercept + sum(self.coef[j] * v[j]
                                    for j in range(len(v)))

    def predict(self, row) -> float:
        return _sigmoid(self.decision(row))

    def predict_many(self, rows) -> list:
        return [self.predict(r) for r in rows]

    def weights_on_raw_scale(self) -> dict:
        """Coefficients per ONE NATURAL UNIT of each feature.

        The fitted coefficients are on the standardised scale, where
        "one unit" means one training standard deviation -- useful for
        comparing features, useless for explaining a decision to
        somebody who is looking at a price. This returns the log-odds
        change per unit of the feature as it is actually measured,
        which is the number that belongs in an explanation.
        """
        if self.coef is None:
            raise RuntimeError("model is not fitted")
        out = {f: self.coef[j] / self.scale[j]
               for j, f in enumerate(self.features)}
        out["__intercept__"] = self.intercept - sum(
            self.coef[j] * self.center[j] / self.scale[j]
            for j in range(len(self.features)))
        return out

    def to_dict(self) -> dict:
        return {"kind": "RIDGE_LOGISTIC", "kernel": VERSION,
                "features": list(self.features), "l2": self.l2,
                "coef": list(self.coef or []), "intercept": self.intercept,
                "center": list(self.center or []),
                "scale": list(self.scale or []),
                "n_rows": self.n_rows, "n_iter": self.n_iter,
                "converged": self.converged, "pos_rate": self.pos_rate}

    @classmethod
    def from_dict(cls, d: dict) -> "Ridge":
        if d.get("kind") != "RIDGE_LOGISTIC":
            raise ValueError("not a ridge logistic model: %r" % d.get("kind"))
        m = cls(d["features"], l2=d.get("l2", 1.0))
        m.coef = list(d["coef"])
        m.intercept = float(d["intercept"])
        m.center = list(d["center"])
        m.scale = list(d["scale"])
        m.n_rows = d.get("n_rows", 0)
        m.n_iter = d.get("n_iter", 0)
        m.converged = bool(d.get("converged"))
        m.pos_rate = d.get("pos_rate")
        return m


# ── isotonic calibration ─────────────────────────────────────────────

class Isotonic:
    """Pool-adjacent-violators calibration. Monotone, non-parametric.

    WHY CALIBRATION IS SEPARATE FROM THE MODEL. A model that RANKS well
    can be badly wrong about magnitudes, and every downstream consumer
    here multiplies a probability by dollars. A ranking that is 0.30
    when it should be 0.12 does not misorder anything and loses money
    on every sizing decision made from it.

    WHY ISOTONIC AND NOT PLATT. Platt scaling assumes the miscalibration
    is a logistic function of the score, which is an assumption nobody
    has checked here. PAV assumes only monotonicity -- that a higher
    score should not mean a lower probability -- which is the one thing
    we are willing to assert.

    IT MUST BE FITTED ON HELD-OUT SCORES. Calibrating on the rows the
    model was fitted to measures the model's memory, not its calibration,
    and produces a curve that looks perfect and is worthless. The caller
    is responsible for that split; `fit` records how many rows it saw so
    a suspiciously perfect curve is at least visible.

    PAV ALONE CLAIMS CERTAINTY IT HAS NOT EARNED, AND IT COSTS REAL
    MONEY. A pooled block of twenty zeros has mean exactly 0.0, and the
    raw curve then says an event is IMPOSSIBLE on the strength of twenty
    observations. It is not impossible. Ferrari run 20260923T1308Z
    measured the damage: 12 of 837 held-out rows were handed p = 0 and
    SIX of them completed. Those six rows alone contributed 0.198 of a
    0.857 log loss -- without them the calibrated model scored 0.669
    against a 0.689 base rate, so the calibrator was helping and one
    unearned certainty buried it.

    THE CORRECTION IS AFFINE AND SHARED, WHICH IS THE WHOLE POINT.
    Every block mean is shrunk by

        m -> (n * m + 0.5) / (n + 1)

    with the SAME n -- the total number of calibration rows -- for every
    block. A per-block Laplace correction, (w*m + 0.5) / (w + 2*0.5), is
    the more obvious choice and it is WRONG HERE: it shrinks small
    blocks harder than large ones, so a two-row block at 0.0 can be
    lifted above a thousand-row block at 0.05 and the curve stops being
    monotone. Monotonicity is the one property PAV exists to provide,
    and a correction that breaks it is not a correction.

    A shared affine map cannot reorder anything, and its floor --
    0.5/(n+1) -- states the resolution the sample actually has: with n
    observations you cannot distinguish a probability from zero below
    about 1/n.

    APPLIED AT FIT TIME, NOT AT PREDICT TIME. A stored artifact keeps
    the exact values it was fitted with, so `from_dict` on a model
    frozen before this change reproduces that model unchanged. Nothing
    already frozen moves; only a NEW fit gets the correction.
    """

    def __init__(self):
        self.x = []          # breakpoints, ascending
        self.y = []          # fitted values, non-decreasing
        self.n_rows = 0
        self.shrinkage = None

    def fit(self, scores, labels, weights=None):
        if len(scores) != len(labels):
            raise ValueError("scores and labels differ in length")
        if not scores:
            raise ValueError("cannot calibrate on zero rows")
        w = [1.0] * len(scores) if weights is None else [float(v)
                                                         for v in weights]
        # Ties must be pooled BEFORE the PAV pass. Two rows with the
        # same score and different labels are not an ordering violation
        # to be resolved; they are one point whose value is their mean.
        order = sorted(range(len(scores)),
                       key=lambda i: (float(scores[i]), i))
        blocks = []          # [sum_wy, sum_w, x]
        for i in order:
            xi = float(scores[i])
            if blocks and blocks[-1][2] == xi:
                blocks[-1][0] += w[i] * float(labels[i])
                blocks[-1][1] += w[i]
            else:
                blocks.append([w[i] * float(labels[i]), w[i], xi])

        # PAV: merge any block whose mean is below its predecessor's.
        stack = []
        for b in blocks:
            stack.append(b)
            while len(stack) > 1:
                a, c = stack[-2], stack[-1]
                ma = a[0] / a[1] if a[1] else 0.0
                mc = c[0] / c[1] if c[1] else 0.0
                if ma <= mc + 1e-15:
                    break
                stack.pop()
                stack.pop()
                stack.append([a[0] + c[0], a[1] + c[1], c[2]])

        n = float(len(scores))

        def _shrink(m: float) -> float:
            """See the class docstring. Shared n, so strictly monotone."""
            return (n * m + 0.5) / (n + 1.0)

        self.x, self.y = [], []
        lo = 0
        for blk in stack:
            mean = blk[0] / blk[1] if blk[1] else 0.0
            value = _shrink(mean)
            # Every original breakpoint inside the merged block takes
            # the block's value, so `predict` can interpolate.
            while lo < len(blocks) and blocks[lo][2] <= blk[2]:
                self.x.append(blocks[lo][2])
                self.y.append(value)
                lo += 1
        self.n_rows = len(scores)
        self.shrinkage = {
            "rule": "(n * block_mean + 0.5) / (n + 1), one shared n",
            "n": len(scores),
            "floor": _shrink(0.0),
            "ceiling": _shrink(1.0),
            "why": "PAV's raw 0.0 and 1.0 are certainty claims a finite "
                   "sample cannot support. A shared affine map removes "
                   "them without reordering the curve.",
        }
        return self

    def predict(self, score: float) -> float:
        """Piecewise-linear between breakpoints, flat outside them.

        FLAT OUTSIDE IS DELIBERATE. Extrapolating a calibration curve
        past the scores it was fitted on invents a probability for a
        region where none was observed. The end values are the honest
        answer and they are also conservative.
        """
        if not self.x:
            raise RuntimeError("calibrator is not fitted")
        s = float(score)
        if s <= self.x[0]:
            return self.y[0]
        if s >= self.x[-1]:
            return self.y[-1]
        lo, hi = 0, len(self.x) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.x[mid] <= s:
                lo = mid
            else:
                hi = mid
        x0, x1 = self.x[lo], self.x[hi]
        y0, y1 = self.y[lo], self.y[hi]
        if x1 == x0:
            return y1
        return y0 + (y1 - y0) * (s - x0) / (x1 - x0)

    def to_dict(self) -> dict:
        return {"kind": "ISOTONIC", "kernel": VERSION,
                "x": list(self.x), "y": list(self.y), "n_rows": self.n_rows,
                "shrinkage": self.shrinkage}

    @classmethod
    def from_dict(cls, d: dict) -> "Isotonic":
        if d.get("kind") != "ISOTONIC":
            raise ValueError("not an isotonic calibrator")
        c = cls()
        c.x = list(d["x"])
        c.y = list(d["y"])
        c.n_rows = d.get("n_rows", 0)
        # ABSENT MEANS FITTED BEFORE THE CORRECTION EXISTED, not
        # "corrected with no record". An artifact frozen earlier keeps
        # its own values and reports the fact rather than pretending.
        c.shrinkage = d.get("shrinkage")
        return c


# ── gradient-boosted stumps ──────────────────────────────────────────

class Stumps:
    """Depth-1 gradient boosting on the logistic loss.

    WHY STUMPS AND NOT TREES. A stump is a single question about a
    single feature -- "was the spread wider than 3 cents?" -- and a sum
    of stumps is an additive model that can be read one line at a time.
    Depth-2 interactions are the first thing to try if stumps are not
    enough, and the mandate's order is explicit: complexity only when it
    IMPROVES MEASURED PERFORMANCE.

    WHY IT IS DETERMINISTIC. No subsampling, no random feature subsets.
    Candidate thresholds are the midpoints between adjacent DISTINCT
    sorted values of each feature, capped at `max_bins` by taking
    evenly-spaced quantiles of that sorted list. Ties in gain are broken
    by (feature index, threshold), never arbitrarily.

    THE BASE SCORE IS THE BASE RATE. Boosting starts from the log-odds
    of the weighted positive rate, so a model with zero rounds is the
    honest "I know nothing but the prior" answer rather than 0.5.
    """

    def __init__(self, features, *, rounds=60, learning_rate=0.1,
                 min_leaf=20, max_bins=32, l2=1.0):
        if not features:
            raise ValueError("a model needs at least one feature")
        self.features = list(features)
        self.rounds = int(rounds)
        self.lr = float(learning_rate)
        self.min_leaf = int(min_leaf)
        self.max_bins = int(max_bins)
        self.l2 = float(l2)
        self.base = 0.0
        self.trees = []       # (feature_index, threshold, left, right)
        self.n_rows = 0

    def _thresholds(self, col) -> list:
        vals = sorted(set(col))
        if len(vals) < 2:
            return []
        cuts = [(vals[i] + vals[i + 1]) / 2.0 for i in range(len(vals) - 1)]
        if len(cuts) <= self.max_bins:
            return cuts
        step = len(cuts) / float(self.max_bins)
        return [cuts[min(len(cuts) - 1, int(k * step))]
                for k in range(self.max_bins)]

    def fit(self, rows, labels, weights=None):
        if not rows:
            raise ValueError("cannot fit on zero rows")
        n, d = len(rows), len(self.features)
        X = [[float(r[f]) for f in self.features] for r in rows]
        y = [float(v) for v in labels]
        w = [1.0] * n if weights is None else [float(v) for v in weights]
        tw = sum(w) or 1.0
        p0 = min(max(sum(w[i] * y[i] for i in range(n)) / tw, 1e-6),
                 1.0 - 1e-6)
        self.base = math.log(p0 / (1.0 - p0))
        self.n_rows = n
        F = [self.base] * n

        cols = [[X[i][j] for i in range(n)] for j in range(d)]
        cand = [self._thresholds(c) for c in cols]

        self.trees = []
        for _ in range(self.rounds):
            grad, hess = [0.0] * n, [0.0] * n
            for i in range(n):
                p = _sigmoid(F[i])
                grad[i] = w[i] * (y[i] - p)          # -dL/dF
                hess[i] = w[i] * max(p * (1.0 - p), _MIN_W)

            best = None                               # (gain, j, t, l, r)
            for j in range(d):
                for t in cand[j]:
                    gl = hl = nl = 0.0
                    gr = hr = nr = 0.0
                    for i in range(n):
                        if cols[j][i] <= t:
                            gl += grad[i]; hl += hess[i]; nl += 1
                        else:
                            gr += grad[i]; hr += hess[i]; nr += 1
                    if nl < self.min_leaf or nr < self.min_leaf:
                        continue
                    gain = (gl * gl / (hl + self.l2)
                            + gr * gr / (hr + self.l2))
                    if best is None or gain > best[0] + 1e-15:
                        best = (gain, j, t,
                                gl / (hl + self.l2), gr / (hr + self.l2))
            if best is None:
                # No split leaves `min_leaf` on both sides. Stopping is
                # the right answer; padding with zero-value trees would
                # only make the round count a lie.
                break
            _, j, t, lv, rv = best
            self.trees.append((j, t, self.lr * lv, self.lr * rv))
            for i in range(n):
                F[i] += self.lr * (lv if cols[j][i] <= t else rv)
        return self

    def decision(self, row) -> float:
        s = self.base
        for j, t, lv, rv in self.trees:
            s += lv if float(row[self.features[j]]) <= t else rv
        return s

    def predict(self, row) -> float:
        return _sigmoid(self.decision(row))

    def predict_many(self, rows) -> list:
        return [self.predict(r) for r in rows]

    def explain(self) -> list:
        """Every stump in plain words, most influential first."""
        agg = {}
        for j, t, lv, rv in self.trees:
            k = (self.features[j], round(t, 6))
            a = agg.setdefault(k, {"feature": self.features[j],
                                   "threshold": t, "below": 0.0,
                                   "above": 0.0, "rounds": 0})
            a["below"] += lv
            a["above"] += rv
            a["rounds"] += 1
        out = sorted(agg.values(),
                     key=lambda a: (-abs(a["above"] - a["below"]),
                                    a["feature"], a["threshold"]))
        for a in out:
            a["reads_as"] = (
                "%s <= %.6g shifts the log-odds by %+.4f; above it by "
                "%+.4f" % (a["feature"], a["threshold"], a["below"],
                           a["above"]))
        return out

    def to_dict(self) -> dict:
        return {"kind": "STUMPS", "kernel": VERSION,
                "features": list(self.features), "base": self.base,
                "trees": [list(t) for t in self.trees],
                "rounds_requested": self.rounds,
                "rounds_fitted": len(self.trees),
                "learning_rate": self.lr, "min_leaf": self.min_leaf,
                "max_bins": self.max_bins, "l2": self.l2,
                "n_rows": self.n_rows}

    @classmethod
    def from_dict(cls, d: dict) -> "Stumps":
        if d.get("kind") != "STUMPS":
            raise ValueError("not a stumps model")
        m = cls(d["features"], rounds=d.get("rounds_requested", 0),
                learning_rate=d.get("learning_rate", 0.1),
                min_leaf=d.get("min_leaf", 20),
                max_bins=d.get("max_bins", 32), l2=d.get("l2", 1.0))
        m.base = float(d["base"])
        m.trees = [(int(t[0]), float(t[1]), float(t[2]), float(t[3]))
                   for t in d["trees"]]
        m.n_rows = d.get("n_rows", 0)
        return m


# ── discrete-time hazard ─────────────────────────────────────────────

class Hazard:
    """Time to the next action, with censoring handled rather than dropped.

    THE PROBLEM THIS SOLVES, and why a plain classifier cannot. "Will
    this account add to the position within an hour?" has three possible
    answers in the data, not two: it did, it did not, and WE STOPPED
    LOOKING. The third is censoring, and the two usual ways of handling
    it are both wrong:

        drop censored rows   biases towards fast actions, because the
                             rows that were still waiting when the
                             window closed are exactly the slow ones
        call them negatives  asserts the action never happened, which
                             the data does not say

    THE PERSON-PERIOD EXPANSION IS THE HONEST VERSION. Each subject
    contributes one row per elapsed time bucket it was STILL AT RISK in,
    labelled 1 in the bucket where the action happened and 0 in each
    bucket it survived. A subject censored in bucket k contributes
    buckets 0..k-1 as zeros and then simply stops -- it makes no claim
    about bucket k, because none is available. The hazard in each bucket
    is then an ordinary logistic regression, which is the Ridge above
    with the bucket index as a feature.

    SURVIVAL IS THE PRODUCT OF (1 - HAZARD) ACROSS BUCKETS, so
    `p_by(row, k)` is the probability the action has happened by the end
    of bucket k, and that is the number a horizon question wants.
    """

    def __init__(self, features, *, buckets, l2=1.0):
        if not buckets or len(buckets) < 1:
            raise ValueError("a hazard model needs at least one bucket edge")
        edges = [float(b) for b in buckets]
        if any(edges[i] >= edges[i + 1] for i in range(len(edges) - 1)):
            raise ValueError("bucket edges must be strictly increasing")
        if edges[0] <= 0.0:
            raise ValueError("the first bucket edge must be positive")
        self.edges = edges
        self.features = list(features)
        # THE BUCKET IS A FEATURE, one indicator per bucket after the
        # first. The first is absorbed by the intercept, so the model
        # can express a baseline hazard that changes with elapsed time
        # -- which is the whole reason not to use a single classifier.
        self._bfeat = ["__bucket_%d__" % k for k in range(1, len(edges))]
        self.inner = Ridge(self.features + self._bfeat, l2=l2)
        self.n_subjects = 0
        self.n_periods = 0
        self.n_censored = 0

    def bucket_of(self, t: float) -> int:
        """Which bucket an elapsed time falls in; len(edges) if beyond."""
        for k, e in enumerate(self.edges):
            if t <= e:
                return k
        return len(self.edges)

    def _expand(self, rows, times, observed):
        out_rows, out_y = [], []
        n_cens = 0
        for r, t, ob in zip(rows, times, observed):
            k = self.bucket_of(float(t))
            if not ob:
                n_cens += 1
                # CENSORED: it contributes a survival for every bucket it
                # got all the way THROUGH, and says nothing about a
                # bucket it was only partway into.
                #
                # THE BOUNDARY CASE IS NOT COSMETIC, and the cross-check
                # against Kaplan-Meier is what found it. A subject last
                # seen at exactly a bucket's end edge survived that
                # whole bucket. The first version of this line indexed
                # off `bucket_of`, which puts t == edge INSIDE that
                # bucket, so those subjects were dropped from it -- 340
                # at risk where Kaplan-Meier counted 380, and a hazard
                # of 0.2647 where the truth was 0.2368.
                #
                # THE BIAS RAN THE WRONG WAY. A denominator that is too
                # small OVER-states the hazard, i.e. over-predicts that
                # the action happens, which is the direction that makes
                # a trading system act when it should wait.
                #
                # So: the largest bucket whose END EDGE is at or before
                # the censoring time. A subject censored before the
                # first edge contributes nothing at all, which is
                # correct -- it never completed a single bucket.
                last = sum(1 for e in self.edges if e <= float(t)) - 1
            else:
                if k >= len(self.edges):
                    # The event happened after the last edge. Within the
                    # modelled horizon it is a survivor, and the model
                    # is not asked about anything beyond its edges.
                    last = len(self.edges) - 1
                    ob = False
                else:
                    last = k
            for b in range(0, last + 1):
                row = dict(r)
                for idx, name in enumerate(self._bfeat, start=1):
                    row[name] = 1.0 if idx == b else 0.0
                out_rows.append(row)
                out_y.append(1.0 if (ob and b == last) else 0.0)
        return out_rows, out_y, n_cens

    def fit(self, rows, times, observed, weights=None):
        if not (len(rows) == len(times) == len(observed)):
            raise ValueError("rows, times and observed differ in length")
        if not rows:
            raise ValueError("cannot fit on zero subjects")
        er, ey, nc = self._expand(rows, times, observed)
        if not er:
            raise ValueError(
                "the expansion produced no person-periods: every subject "
                "was censored before the first bucket edge, so this data "
                "cannot speak to any horizon this model defines")
        ew = None
        if weights is not None:
            ew, i = [], 0
            for r, t, ob in zip(rows, times, observed):
                # THE SAME RULE AS `_expand`, and it has to be: a
                # weight vector of a different length than the design
                # is a silent misalignment, not an error.
                if not ob:
                    last = sum(1 for e in self.edges if e <= float(t)) - 1
                else:
                    last = min(self.bucket_of(float(t)),
                               len(self.edges) - 1)
                ew.extend([float(weights[i])] * max(0, last + 1))
                i += 1
        self.inner.fit(er, ey, ew)
        self.n_subjects = len(rows)
        self.n_periods = len(er)
        self.n_censored = nc
        return self

    def hazard(self, row, bucket: int) -> float:
        r = dict(row)
        for idx, name in enumerate(self._bfeat, start=1):
            r[name] = 1.0 if idx == bucket else 0.0
        return self.inner.predict(r)

    def p_by(self, row, bucket: int) -> float:
        """P(the action has happened by the END of `bucket`)."""
        if bucket < 0:
            return 0.0
        b = min(bucket, len(self.edges) - 1)
        surv = 1.0
        for k in range(b + 1):
            surv *= (1.0 - self.hazard(row, k))
        return 1.0 - surv

    def p_by_time(self, row, t: float) -> float:
        k = self.bucket_of(float(t))
        if k >= len(self.edges):
            return self.p_by(row, len(self.edges) - 1)
        return self.p_by(row, k)

    def survival_curve(self, row) -> list:
        return [{"bucket": k, "edge_s": self.edges[k],
                 "hazard": self.hazard(row, k), "p_by": self.p_by(row, k)}
                for k in range(len(self.edges))]

    def to_dict(self) -> dict:
        return {"kind": "HAZARD", "kernel": VERSION,
                "edges": list(self.edges),
                "features": list(self.features),
                "inner": self.inner.to_dict(),
                "n_subjects": self.n_subjects, "n_periods": self.n_periods,
                "n_censored": self.n_censored}

    @classmethod
    def from_dict(cls, d: dict) -> "Hazard":
        if d.get("kind") != "HAZARD":
            raise ValueError("not a hazard model")
        m = cls(d["features"], buckets=d["edges"])
        m.inner = Ridge.from_dict(d["inner"])
        m.n_subjects = d.get("n_subjects", 0)
        m.n_periods = d.get("n_periods", 0)
        m.n_censored = d.get("n_censored", 0)
        return m


# ── the baselines every model must beat ──────────────────────────────

class BaseRate:
    """Predicts the training base rate for everything.

    THIS IS NOT A JOKE MODEL. It is the number a real model has to
    beat, and on imbalanced problems -- which most of these are -- it
    beats a surprising amount of work. Reporting a model's log loss
    without this beside it is how a useless model gets promoted.
    """

    def __init__(self):
        self.rate = None
        self.n_rows = 0

    def fit(self, rows, labels, weights=None):
        w = [1.0] * len(labels) if weights is None else [float(v)
                                                         for v in weights]
        tw = sum(w) or 1.0
        self.rate = sum(w[i] * float(labels[i])
                        for i in range(len(labels))) / tw
        self.n_rows = len(labels)
        return self

    def predict(self, row) -> float:
        if self.rate is None:
            raise RuntimeError("not fitted")
        return self.rate

    def predict_many(self, rows) -> list:
        return [self.predict(r) for r in rows]

    def to_dict(self) -> dict:
        return {"kind": "BASE_RATE", "kernel": VERSION,
                "rate": self.rate, "n_rows": self.n_rows}

    @classmethod
    def from_dict(cls, d: dict) -> "BaseRate":
        m = cls()
        m.rate = d["rate"]
        m.n_rows = d.get("n_rows", 0)
        return m


LOADERS = {"RIDGE_LOGISTIC": Ridge, "ISOTONIC": Isotonic,
           "STUMPS": Stumps, "HAZARD": Hazard, "BASE_RATE": BaseRate}


def load(d: dict):
    """Rebuild any model this kernel can produce, from its dict."""
    kind = (d or {}).get("kind")
    if kind not in LOADERS:
        raise ValueError("unknown model kind %r" % (kind,))
    if d.get("kernel") != VERSION:
        raise ValueError(
            "model was written by kernel %r and this is %r. A model is "
            "not loaded across kernel versions: the arithmetic may have "
            "changed and a silently different prediction is worse than "
            "a refusal." % (d.get("kernel"), VERSION))
    return LOADERS[kind].from_dict(d)
