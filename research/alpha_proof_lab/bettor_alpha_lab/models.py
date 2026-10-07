from __future__ import annotations
from dataclasses import dataclass, field
import math
import numpy as np
from sklearn.linear_model import LogisticRegression
from .market import logit, sigmoid


@dataclass
class DynamicElo:
    """Simple online Elo model suitable as a transparent team-strength feature."""
    k: float = 20.0
    home_advantage: float = 45.0
    base: float = 1500.0
    ratings: dict[str, float] = field(default_factory=dict)

    def rating(self, team: str) -> float:
        return self.ratings.get(team, self.base)

    def win_probability(self, home: str, away: str) -> float:
        rh = self.rating(home) + self.home_advantage
        ra = self.rating(away)
        return 1.0 / (1.0 + 10 ** ((ra - rh) / 400.0))

    def update(self, home: str, away: str, home_score: float, away_score: float) -> float:
        p = self.win_probability(home, away)
        outcome = 1.0 if home_score > away_score else 0.0 if home_score < away_score else 0.5
        delta = self.k * (outcome - p)
        self.ratings[home] = self.rating(home) + delta
        self.ratings[away] = self.rating(away) - delta
        return p


class MarketResidualLogistic:
    """Logistic residual model with the market logit used as a fixed prior offset.

    We fit a regularized logistic model on features while preserving the market
    price as the baseline. sklearn does not support offsets directly, so this
    reference implementation includes market logit as a feature and constrains
    residual complexity through strong L2 regularization.
    """
    def __init__(self, c: float = 0.1, max_iter: int = 500):
        self.model = LogisticRegression(C=c, penalty="l2", solver="lbfgs", max_iter=max_iter)
        self.fitted = False

    @staticmethod
    def _design(market_p: np.ndarray, features: np.ndarray) -> np.ndarray:
        market_logit = np.array([logit(float(p)) for p in market_p], dtype=float).reshape(-1, 1)
        x = np.asarray(features, dtype=float)
        if x.ndim == 1:
            x = x.reshape(-1, 1)
        return np.hstack([market_logit, x])

    def fit(self, market_p, features, y):
        X = self._design(np.asarray(market_p), np.asarray(features))
        self.model.fit(X, np.asarray(y, dtype=int))
        self.fitted = True
        return self

    def predict_proba(self, market_p, features) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("model is not fitted")
        X = self._design(np.asarray(market_p), np.asarray(features))
        return self.model.predict_proba(X)[:, 1]
