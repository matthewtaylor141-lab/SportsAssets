from __future__ import annotations
import numpy as np
import pandas as pd
from bettor_alpha_lab.pipeline import evaluate_probabilities

rng = np.random.default_rng(7)
n = 1200
market = rng.uniform(0.2, 0.8, n)
# synthetic hidden residual alpha: intentionally small
signal = rng.normal(0, 1, n)
true_p = np.clip(market + 0.035 * signal, 0.02, 0.98)
y = rng.binomial(1, true_p)
raw = np.clip(market + 0.05 * signal + rng.normal(0, 0.02, n), 0.01, 0.99)
price = np.clip(market + rng.normal(0, 0.01, n), 0.02, 0.98)
fee = np.full(n, 0.005)

df = pd.DataFrame({
    "observed_at": pd.date_range("2025-01-01", periods=n, freq="h"),
    "p_raw": raw,
    "y": y,
    "price": price,
    "fee": fee,
})

report = evaluate_probabilities(df, cost_col="fee")
print({
    "status": report["status"],
    "folds": report.get("folds"),
    "rows": report.get("rows"),
    "probability_metrics": report.get("probability_metrics"),
    "mean_expected_net_profit_per_contract": report.get("mean_expected_net_profit_per_contract"),
    "conservative_95_net_profit_per_contract": report.get("conservative_95_net_profit_per_contract"),
    "gate": report.get("gate"),
})
