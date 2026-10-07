from __future__ import annotations
from dataclasses import dataclass
from itertools import combinations
from math import erf, exp, log, sqrt
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss


@dataclass(frozen=True)
class Fold:
    train_idx: np.ndarray
    test_idx: np.ndarray


def chronological_walkforward(n: int, train_min: int, test_size: int, step: int | None = None) -> list[Fold]:
    if n <= 0 or train_min <= 0 or test_size <= 0:
        raise ValueError("n, train_min, and test_size must be positive")
    step = test_size if step is None else int(step)
    out = []
    train_end = train_min
    while train_end + test_size <= n:
        out.append(Fold(
            train_idx=np.arange(0, train_end, dtype=int),
            test_idx=np.arange(train_end, train_end + test_size, dtype=int),
        ))
        train_end += step
    return out


def betting_profit_binary(y, fill_price, fee=0.0):
    y = np.asarray(y, dtype=float)
    q = np.asarray(fill_price, dtype=float)
    fee = np.asarray(fee, dtype=float)
    return y - q - fee


def clv(entry_price, closing_price, side: str = "YES"):
    e = np.asarray(entry_price, dtype=float)
    c = np.asarray(closing_price, dtype=float)
    if side.upper() == "YES":
        return c - e
    if side.upper() == "NO":
        return e - c
    raise ValueError("side must be YES or NO")


def sharpe_ratio(returns, periods_per_year: float = 1.0):
    r = np.asarray(returns, dtype=float)
    if len(r) < 2:
        return 0.0
    sd = float(r.std(ddof=1))
    if sd <= 0:
        return 0.0
    return float(r.mean() / sd * sqrt(periods_per_year))


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_returns: int,
    n_trials: int,
    skew: float = 0.0,
    excess_kurtosis: float = 0.0,
) -> float:
    """Approximate DSR probability using a multiple-testing expected max Sharpe.

    This is a transparent approximation for research screening, not a substitute
    for a formal statistical review.
    """
    if n_returns < 3:
        return 0.0
    n_trials = max(1, int(n_trials))
    # expected max of n standard normals (Blom approximation)
    if n_trials == 1:
        sr_star = 0.0
    else:
        p = (n_trials - 0.375) / (n_trials + 0.25)
        # Acklam-ish inverse via scipy-free bisection
        lo, hi = -8.0, 8.0
        for _ in range(80):
            mid = (lo + hi) / 2
            if normal_cdf(mid) < p:
                lo = mid
            else:
                hi = mid
        sr_star = (lo + hi) / 2 / sqrt(max(1, n_returns - 1))

    sr = float(observed_sharpe)
    denom_sq = 1.0 - skew * sr + ((excess_kurtosis + 2.0) / 4.0) * sr * sr
    denom = sqrt(max(1e-12, denom_sq / max(1, n_returns - 1)))
    z = (sr - sr_star) / denom
    return float(normal_cdf(z))


def probability_of_backtest_overfitting(performance_matrix: np.ndarray) -> float:
    """CSCV-style PBO approximation.

    performance_matrix shape: [time_blocks, strategies].
    For every half-split of time blocks, choose the best strategy in-sample and
    measure whether its out-of-sample rank falls below the median.
    """
    m = np.asarray(performance_matrix, dtype=float)
    if m.ndim != 2:
        raise ValueError("performance_matrix must be 2D [blocks, strategies]")
    s, n_strat = m.shape
    if s < 4 or n_strat < 2 or s % 2:
        raise ValueError("need an even number >=4 of blocks and >=2 strategies")

    half = s // 2
    failures = 0
    total = 0
    all_idx = set(range(s))
    # avoid mirrored duplicate splits by requiring 0 in train
    for train_tuple in combinations(range(s), half):
        if 0 not in train_tuple:
            continue
        train = np.array(train_tuple, dtype=int)
        test = np.array(sorted(all_idx - set(train_tuple)), dtype=int)
        ins = m[train].mean(axis=0)
        oos = m[test].mean(axis=0)
        best = int(np.argmax(ins))
        rank = int(np.argsort(np.argsort(oos))[best])  # 0 worst
        failures += rank < (n_strat - 1) / 2
        total += 1
    return float(failures / total) if total else float("nan")


def walkforward_summary(df: pd.DataFrame, p_col="p", y_col="y", price_col="price") -> dict:
    y = df[y_col].to_numpy(dtype=int)
    p = np.clip(df[p_col].to_numpy(dtype=float), 1e-9, 1 - 1e-9)
    price = df[price_col].to_numpy(dtype=float)
    profit = betting_profit_binary(y, price)
    return {
        "n": int(len(df)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "total_profit_per_contract": float(profit.sum()),
        "mean_profit_per_contract": float(profit.mean()) if len(profit) else 0.0,
        "win_rate": float(y.mean()) if len(y) else 0.0,
        "sharpe_unannualized": sharpe_ratio(profit),
    }


def acceptance_gate(
    *,
    mean_net_profit: float,
    conservative_net_profit: float,
    calibration_error: float,
    pbo: float | None,
    dsr_probability: float | None,
    min_dsr: float = 0.95,
    max_pbo: float = 0.50,
    max_ece: float = 0.05,
) -> dict:
    reasons = []
    if mean_net_profit <= 0:
        reasons.append("MEAN_NET_PROFIT_NOT_POSITIVE")
    if conservative_net_profit <= 0:
        reasons.append("CONSERVATIVE_NET_PROFIT_NOT_POSITIVE")
    if calibration_error > max_ece:
        reasons.append("CALIBRATION_ERROR_TOO_HIGH")
    if pbo is not None and pbo > max_pbo:
        reasons.append("BACKTEST_OVERFITTING_RISK_TOO_HIGH")
    if dsr_probability is not None and dsr_probability < min_dsr:
        reasons.append("DEFLATED_SHARPE_NOT_CONVINCING")
    return {
        "eligible": not reasons,
        "status": "ACTIVE_CHALLENGER" if not reasons else "SHADOW_ONLY",
        "reasons": reasons,
    }
