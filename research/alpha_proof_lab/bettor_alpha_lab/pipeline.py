from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss
from .calibration import BetaCalibrator, probability_metrics
from .execution import CostBreakdown, binary_taker_ev
from .validation import chronological_walkforward, walkforward_summary, acceptance_gate


@dataclass
class ProofConfig:
    train_min: int = 500
    test_size: int = 100
    min_segment_calibration_n: int = 200
    max_ece: float = 0.05
    max_pbo: float = 0.50
    min_dsr: float = 0.95


def evaluate_probabilities(
    df: pd.DataFrame,
    p_raw_col: str = "p_raw",
    y_col: str = "y",
    price_col: str = "price",
    cost_col: str | None = None,
    config: ProofConfig = ProofConfig(),
) -> dict:
    """Chronological probability + executable economics evaluation.

    This function does not fit a forecasting model; it verifies a supplied
    probability stream without shuffling future outcomes into the past.
    """
    df = df.sort_values("observed_at").reset_index(drop=True).copy()
    folds = chronological_walkforward(len(df), config.train_min, config.test_size)
    rows = []

    for k, fold in enumerate(folds):
        train = df.iloc[fold.train_idx]
        test = df.iloc[fold.test_idx]
        cal = BetaCalibrator().fit(train[p_raw_col].values, train[y_col].values)
        p = cal.predict(test[p_raw_col].values)

        for j, (_, r) in enumerate(test.iterrows()):
            costs = float(r[cost_col]) if cost_col else 0.0
            ev = binary_taker_ev(float(p[j]), float(r[price_col]), CostBreakdown(fees=costs))
            rows.append({
                "fold": k,
                "observed_at": r["observed_at"],
                "y": int(r[y_col]),
                "p": float(p[j]),
                "price": float(r[price_col]),
                "net_ev": float(ev.net_ev_per_contract),
                "realized_profit": float(int(r[y_col]) - float(r[price_col]) - costs),
            })

    out = pd.DataFrame(rows)
    if out.empty:
        return {"status": "INSUFFICIENT_WALKFORWARD_DATA", "folds": 0, "rows": 0}

    pm = probability_metrics(out["y"], out["p"])
    mean_net_ev = float(out["net_ev"].mean())
    se = float(out["net_ev"].std(ddof=1) / np.sqrt(len(out))) if len(out) > 1 else float("inf")
    conservative = mean_net_ev - 1.96 * se if np.isfinite(se) else -float("inf")

    gate = acceptance_gate(
        mean_net_profit=mean_net_ev,
        conservative_net_profit=conservative,
        calibration_error=pm["ece_10"],
        pbo=None,
        dsr_probability=None,
        max_ece=config.max_ece,
        max_pbo=config.max_pbo,
        min_dsr=config.min_dsr,
    )

    return {
        "status": "OK",
        "folds": len(folds),
        "rows": int(len(out)),
        "probability_metrics": pm,
        "mean_expected_net_profit_per_contract": mean_net_ev,
        "conservative_95_net_profit_per_contract": conservative,
        "realized": walkforward_summary(out, p_col="p", y_col="y", price_col="price"),
        "gate": gate,
        "records": out.to_dict(orient="records"),
    }
