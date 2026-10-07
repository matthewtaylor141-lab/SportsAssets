"""Probability models evaluated against the market prior (research only).

Every model is fitted on TRAINING events only and predicts held-out events.

  HierarchicalCalibrator  calibration of BETTOR's raw probability at the most
      specific level the evidence supports: sport x family x regime ->
      sport x family -> sport -> global -> the market prior itself. A level
      is fitted only with >= min_events DISTINCT EVENTS and both outcomes;
      repeated evaluations of one game never make a subgroup look supported.
      method 'beta' uses the package BetaCalibrator; 'isotonic' uses the
      package IsotonicCalibrator only where >= isotonic_min_events support
      it (else beta).

  shrink weight           how far the calibrated probability may move away
      from the market: w in [0, 1] on the logit scale (package
      shrink_to_market), learned by event-weighted likelihood on training.

  MarketResidualOffset    logit p = logit p_market + a + alpha * r, where
      r = logit p_raw - logit p_market (BETTOR's disagreement with the
      market). (a, alpha) are fitted by event-weighted maximum likelihood
      with the market logit as a fixed offset, so the model can only ADD to
      the market; the estimates are then shrunk by their event-clustered
      standard errors (|theta| - z se, floored at 0), so a residual that is
      not distinguishable from noise collapses to the market (alpha -> 0).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from bettor_ev_probability_engine.calibration import BetaCalibrator, IsotonicCalibrator  # noqa: E402
from bettor_ev_probability_engine.residual_model import shrink_to_market                # noqa: E402

LEVELS = (("sport", "family", "regime"), ("sport", "family"), ("sport",), ())
EPS = 1e-6


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float)))


def event_weights(events) -> np.ndarray:
    """1 / (rows of that event): every event carries total weight 1."""
    ev = np.asarray(events).astype(str)
    _, inv, cnt = np.unique(ev, return_inverse=True, return_counts=True)
    return 1.0 / cnt[inv]


class HierarchicalCalibrator:
    def __init__(self, method="beta", min_events=40, isotonic_min_events=150):
        self.method, self.min_events, self.iso_min = method, min_events, isotonic_min_events
        self.cals, self.support = {}, {}

    def fit(self, rows):
        for keys in LEVELS:
            groups = {}
            for r in rows:
                groups.setdefault(tuple(r[k] for k in keys), []).append(r)
            for g, rs in groups.items():
                evs = {r["event_id"] for r in rs}
                ys = {r["outcome"] for r in rs}
                self.support[(keys, g)] = len(evs)
                if len(evs) < self.min_events or len(ys) < 2:
                    continue
                use_iso = self.method == "isotonic" and len(evs) >= self.iso_min
                c = IsotonicCalibrator() if use_iso else BetaCalibrator()
                c.fit([r["p_raw"] for r in rs], [int(r["outcome"]) for r in rs])
                self.cals[(keys, g)] = (c, "ISOTONIC" if use_iso else "BETA", len(evs))
        return self

    def level_for(self, r):
        for keys in LEVELS:
            k = (keys, tuple(r[x] for x in keys))
            if k in self.cals:
                return k
        return None

    def predict(self, rows):
        p, lev = [], []
        for r in rows:
            k = self.level_for(r)
            if k is None:      # nothing supported: the market prior, not a guess
                p.append(r["p_venue"]); lev.append(("MARKET_PRIOR_FALLBACK", 0, None))
            else:
                c, kind, n = self.cals[k]
                p.append(float(np.clip(c.predict([r["p_raw"]])[0], EPS, 1 - EPS)))
                lev.append(("/".join(k[0]) or "GLOBAL", n, kind))
        return np.array(p), lev


def fit_shrink_weight(p_cand, p_mkt, y, w_ev, grid=np.linspace(0, 1, 21)) -> float:
    best, bw = None, 0.0
    for w in grid:
        q = np.clip(shrink_to_market(p_cand, p_mkt, w), EPS, 1 - EPS)
        ll = -np.sum(w_ev * (y * np.log(q) + (1 - y) * np.log(1 - q)))
        if best is None or ll < best - 1e-12:
            best, bw = ll, float(w)
    return bw


class MarketResidualOffset:
    def __init__(self, z=1.645, ridge=1e-3):
        self.z, self.ridge = z, ridge
        self.a = self.alpha = 0.0
        self.a_hat = self.alpha_hat = 0.0
        self.se = (float("nan"), float("nan"))
        self.fitted = False

    @staticmethod
    def _x(p_raw, p_mkt):
        r = logit(p_raw) - logit(p_mkt)
        return np.column_stack([np.ones_like(r), r])

    def fit(self, p_raw, p_mkt, y, events):
        X, off = self._x(p_raw, p_mkt), logit(p_mkt)
        y = np.asarray(y, dtype=float)
        w = event_weights(events)
        th = np.zeros(2)
        for _ in range(50):
            mu = sigmoid(off + X @ th)
            g = X.T @ (w * (y - mu)) - self.ridge * th
            H = (X * (w * mu * (1 - mu))[:, None]).T @ X + self.ridge * np.eye(2)
            step = np.linalg.solve(H, g)
            th += step
            if np.max(np.abs(step)) < 1e-10:
                break
        mu = sigmoid(off + X @ th)
        H = (X * (w * mu * (1 - mu))[:, None]).T @ X + self.ridge * np.eye(2)
        # event-clustered sandwich covariance
        ev = np.asarray(events).astype(str)
        S = np.zeros((2, 2))
        for e in np.unique(ev):
            m = ev == e
            s = (X[m] * (w[m] * (y[m] - mu[m]))[:, None]).sum(0)
            S += np.outer(s, s)
        Hi = np.linalg.inv(H)
        V = Hi @ S @ Hi
        se = np.sqrt(np.maximum(np.diag(V), 0))
        self.a_hat, self.alpha_hat = float(th[0]), float(th[1])
        self.se = (float(se[0]), float(se[1]))
        shrink = lambda t, s: math.copysign(max(0.0, abs(t) - self.z * s), t)
        self.a, self.alpha = shrink(th[0], se[0]), shrink(th[1], se[1])
        return self

    def predict(self, p_raw, p_mkt):
        X = self._x(p_raw, p_mkt)
        return np.clip(sigmoid(logit(p_mkt) + X @ np.array([self.a, self.alpha])), EPS, 1 - EPS)

    def summary(self):
        return {"intercept_hat": self.a_hat, "alpha_hat": self.alpha_hat,
                "intercept_se_clustered": self.se[0], "alpha_se_clustered": self.se[1],
                "intercept_used": self.a, "alpha_used": self.alpha,
                "collapsed_to_market": self.alpha == 0.0 and self.a == 0.0}
