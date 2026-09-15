"""Dixon-Coles goals model: (lambda_home, mu_away, rho) -> exact-score matrix
-> moneyline / totals / spread probabilities.

Used when the feed carries goal-expectancy or when deriving alt lines from a
main line. rho applies the standard low-score dependence correction.
"""

from __future__ import annotations

import math

MAX_GOALS = 12


def _pois(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Dixon-Coles low-score adjustment."""
    if x == 0 and y == 0:
        return 1 - lam * mu * rho
    if x == 0 and y == 1:
        return 1 + lam * rho
    if x == 1 and y == 0:
        return 1 + mu * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def score_matrix(lam: float, mu: float, rho: float = 0.0) -> list[list[float]]:
    """P(home=x, away=y) for x,y in [0, MAX_GOALS]; renormalized."""
    m = [
        [_pois(x, lam) * _pois(y, mu) * _tau(x, y, lam, mu, rho) for y in range(MAX_GOALS + 1)]
        for x in range(MAX_GOALS + 1)
    ]
    total = sum(sum(row) for row in m)
    return [[v / total for v in row] for row in m]


def match_probs(lam: float, mu: float, rho: float = 0.0) -> dict[str, float]:
    """Home / draw / away win probabilities."""
    m = score_matrix(lam, mu, rho)
    home = sum(m[x][y] for x in range(MAX_GOALS + 1) for y in range(MAX_GOALS + 1) if x > y)
    draw = sum(m[x][x] for x in range(MAX_GOALS + 1))
    return {"home": home, "draw": draw, "away": 1.0 - home - draw}


def total_over_prob(line: float, lam: float, mu: float, rho: float = 0.0) -> float:
    """P(total goals > line) for a half-point line (e.g. 2.5)."""
    m = score_matrix(lam, mu, rho)
    return sum(
        m[x][y] for x in range(MAX_GOALS + 1) for y in range(MAX_GOALS + 1) if x + y > line
    )


def handicap_cover_prob(spread: float, lam: float, mu: float, rho: float = 0.0) -> float:
    """P(home covers a half-point spread), e.g. spread=-1.5 → P(home - away > 1.5)."""
    m = score_matrix(lam, mu, rho)
    return sum(
        m[x][y] for x in range(MAX_GOALS + 1) for y in range(MAX_GOALS + 1) if x - y > -spread
    )


def solve_lambda_mu(p_home: float, p_draw: float, total_line: float,
                    p_over: float) -> tuple[float, float]:
    """Invert (match odds + total) into (lam, mu) by grid + refine — lets the
    engine derive exact-score / alt-line fair values from de-vigged 1X2+total."""
    best = (1.3, 1.1)
    best_err = float("inf")
    for lam10 in range(2, 45):
        for mu10 in range(2, 45):
            lam, mu = lam10 / 10, mu10 / 10
            probs = match_probs(lam, mu, rho=-0.05)
            over = total_over_prob(total_line, lam, mu, rho=-0.05)
            err = (probs["home"] - p_home) ** 2 + (probs["draw"] - p_draw) ** 2 \
                + (over - p_over) ** 2
            if err < best_err:
                best_err, best = err, (lam, mu)
    return best
