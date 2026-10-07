from __future__ import annotations
import math


EPS = 1e-12


def clamp01(x: float, eps: float = EPS) -> float:
    return min(1.0 - eps, max(eps, float(x)))


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def logit(p: float) -> float:
    p = clamp01(p)
    return math.log(p / (1.0 - p))


def implied_probability(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        raise ValueError("decimal odds must be > 1")
    return 1.0 / float(decimal_odds)


def devig_binary_from_decimal(home_odds: float, away_odds: float) -> tuple[float, float]:
    """Normalize two implied probabilities so they sum to one."""
    a = implied_probability(home_odds)
    b = implied_probability(away_odds)
    s = a + b
    return a / s, b / s


def devig_binary_from_prices(yes_price: float, no_price: float) -> tuple[float, float]:
    """Normalize complementary contract prices when they contain overround."""
    if yes_price <= 0 or no_price <= 0:
        raise ValueError("prices must be positive")
    s = yes_price + no_price
    return yes_price / s, no_price / s


def residual_probability(market_p: float, residual_logit: float, alpha: float = 1.0) -> float:
    """Market-prior residual model: logit(p) = logit(market) + alpha * residual."""
    return sigmoid(logit(market_p) + float(alpha) * float(residual_logit))
