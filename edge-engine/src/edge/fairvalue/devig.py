"""De-vig: sharp-book odds -> fair probabilities.

Two methods; shadow grading decides which calibrates better per league:
  - multiplicative: p_i = q_i / sum(q)      (fast, biased at longshots)
  - power:          p_i = q_i^k, k solved so sum = 1  (better tail behavior)
where q_i = 1 / decimal_odds_i.

The reference account's calibration signature (positive edge at 5-10c AND
30-50c AND 75-90c simultaneously) is consistent with power de-vig on a
Pinnacle-class feed; validate in shadow.
"""
from scipy.optimize import brentq


def implied(decimal_odds: list[float]) -> list[float]:
    return [1.0 / o for o in decimal_odds]


def devig_multiplicative(decimal_odds: list[float]) -> list[float]:
    q = implied(decimal_odds)
    s = sum(q)
    return [x / s for x in q]


def devig_power(decimal_odds: list[float]) -> list[float]:
    q = implied(decimal_odds)
    if abs(sum(q) - 1.0) < 1e-9:
        return q

    def f(k):
        return sum(x ** k for x in q) - 1.0
    k = brentq(f, 0.25, 4.0)
    return [x ** k for x in q]


def fair_value(decimal_odds: list[float], method: str = "power") -> list[float]:
    fn = devig_power if method == "power" else devig_multiplicative
    return fn(decimal_odds)
