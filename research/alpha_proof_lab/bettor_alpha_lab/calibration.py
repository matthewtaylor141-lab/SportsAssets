from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss


def calibration_error(y_true, p, bins: int = 10) -> float:
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(p, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = len(y)
    if total == 0:
        return float("nan")
    err = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        n = int(mask.sum())
        if not n:
            continue
        err += (n / total) * abs(float(y[mask].mean()) - float(p[mask].mean()))
    return float(err)


def probability_metrics(y_true, p) -> dict:
    y = np.asarray(y_true, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-9, 1 - 1e-9)
    return {
        "n": int(len(y)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "ece_10": calibration_error(y, p, bins=10),
    }


class IsotonicCalibrator:
    def __init__(self):
        self.model = IsotonicRegression(out_of_bounds="clip")
        self.fitted = False

    def fit(self, p, y):
        self.model.fit(np.asarray(p, dtype=float), np.asarray(y, dtype=float))
        self.fitted = True
        return self

    def predict(self, p):
        if not self.fitted:
            raise RuntimeError("calibrator not fitted")
        return np.asarray(self.model.predict(np.asarray(p, dtype=float)), dtype=float)


class BetaCalibrator:
    """Beta calibration: logistic regression on log(p) and log(1-p)."""
    def __init__(self, c: float = 100.0):
        self.model = LogisticRegression(C=c, solver="lbfgs")
        self.fitted = False

    @staticmethod
    def _x(p):
        p = np.clip(np.asarray(p, dtype=float), 1e-9, 1 - 1e-9)
        return np.column_stack([np.log(p), np.log(1.0 - p)])

    def fit(self, p, y):
        self.model.fit(self._x(p), np.asarray(y, dtype=int))
        self.fitted = True
        return self

    def predict(self, p):
        if not self.fitted:
            raise RuntimeError("calibrator not fitted")
        return self.model.predict_proba(self._x(p))[:, 1]
