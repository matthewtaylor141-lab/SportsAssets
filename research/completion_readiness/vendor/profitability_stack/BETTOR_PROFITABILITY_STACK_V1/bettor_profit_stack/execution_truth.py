from __future__ import annotations
from dataclasses import dataclass, asdict
from math import sqrt
import numpy as np

@dataclass(frozen=True)
class ExecutionCosts:
    fees: float = 0.0
    slippage: float = 0.0
    adverse_selection: float = 0.0
    management_cost: float = 0.0
    cancel_failure_cost: float = 0.0
    inventory_risk_cost: float = 0.0
    rebate: float = 0.0

    @property
    def net_cost(self) -> float:
        return (
            self.fees + self.slippage + self.adverse_selection +
            self.management_cost + self.cancel_failure_cost +
            self.inventory_risk_cost - self.rebate
        )

@dataclass(frozen=True)
class ExecutionChoice:
    action: str
    expected_profit_per_posted_contract: float
    profit_if_filled: float
    fill_probability: float
    lower_bound_profit: float
    reason: str
    components: dict

    @property
    def admissible(self) -> bool:
        return self.lower_bound_profit > 0 and self.expected_profit_per_posted_contract > 0

def taker_value(probability: float, fill_price: float, costs: ExecutionCosts) -> ExecutionChoice:
    gross = float(probability) - float(fill_price)
    net = gross - costs.net_cost
    return ExecutionChoice(
        action="TAKE",
        expected_profit_per_posted_contract=net,
        profit_if_filled=net,
        fill_probability=1.0,
        lower_bound_profit=net,
        reason="POSITIVE_EXECUTABLE_EV" if net > 0 else "NON_POSITIVE_EXECUTABLE_EV",
        components={"gross_edge": gross, "costs": asdict(costs)},
    )

def maker_value(
    probability: float,
    conditional_fill_price: float,
    fill_probability: float,
    costs_if_filled: ExecutionCosts,
    fill_probability_se: float = 0.0,
    edge_se: float = 0.0,
    z: float = 1.645,
) -> ExecutionChoice:
    pf = min(1.0, max(0.0, float(fill_probability)))
    gross = float(probability) - float(conditional_fill_price)
    net_if_filled = gross - costs_if_filled.net_cost
    expected = pf * net_if_filled
    conservative_pf = max(0.0, pf - z * max(0.0, fill_probability_se))
    conservative_net = net_if_filled - z * max(0.0, edge_se)
    lower = conservative_pf * conservative_net
    return ExecutionChoice(
        action="MAKE",
        expected_profit_per_posted_contract=expected,
        profit_if_filled=net_if_filled,
        fill_probability=pf,
        lower_bound_profit=lower,
        reason="POSITIVE_CONSERVATIVE_MAKER_EV" if lower > 0 else "MAKER_EV_NOT_PROVEN",
        components={
            "gross_edge_if_filled": gross,
            "costs_if_filled": asdict(costs_if_filled),
            "fill_probability_se": fill_probability_se,
            "edge_se": edge_se,
        },
    )

def wait_value(
    current_probability: float,
    expected_future_price: float,
    expected_edge_decay: float,
    expected_wait_cost: float = 0.0,
) -> ExecutionChoice:
    p_after = float(current_probability) - max(0.0, float(expected_edge_decay))
    net = p_after - float(expected_future_price) - float(expected_wait_cost)
    return ExecutionChoice(
        action="WAIT",
        expected_profit_per_posted_contract=net,
        profit_if_filled=net,
        fill_probability=1.0,
        lower_bound_profit=net,
        reason="WAIT_PRESERVES_POSITIVE_EV" if net > 0 else "WAIT_DESTROYS_EDGE",
        components={
            "probability_after_decay": p_after,
            "expected_future_price": expected_future_price,
            "expected_edge_decay": expected_edge_decay,
            "expected_wait_cost": expected_wait_cost,
        },
    )

def choose_execution(*choices: ExecutionChoice, minimum_lower_bound: float = 0.0) -> ExecutionChoice:
    if not choices:
        raise ValueError("at least one execution choice is required")
    eligible = [c for c in choices if c.lower_bound_profit > minimum_lower_bound]
    if not eligible:
        return ExecutionChoice(
            action="REFUSE",
            expected_profit_per_posted_contract=0.0,
            profit_if_filled=0.0,
            fill_probability=0.0,
            lower_bound_profit=0.0,
            reason="NO_EXECUTION_PATH_HAS_POSITIVE_LOWER_BOUND",
            components={"candidates": [asdict(c) for c in choices]},
        )
    return max(eligible, key=lambda c: c.lower_bound_profit)

def adverse_selection_after_fill(fill_price: float, future_mark: float, side: str = "BUY") -> float:
    """Positive value means execution moved against us after fill."""
    if side.upper() == "BUY":
        return float(fill_price) - float(future_mark)
    if side.upper() == "SELL":
        return float(future_mark) - float(fill_price)
    raise ValueError("side must be BUY or SELL")

def estimate_fill_probability(binary_fills, alpha: float = 1.0, beta: float = 1.0) -> tuple[float, float]:
    """Beta-Binomial posterior mean and approximate standard error."""
    x = np.asarray(binary_fills, dtype=float)
    if x.size == 0:
        return alpha / (alpha + beta), 1.0
    a = alpha + float(x.sum())
    b = beta + float(x.size - x.sum())
    mean = a / (a + b)
    var = (a * b) / (((a + b) ** 2) * (a + b + 1))
    return mean, sqrt(var)

def edge_decay_slope(times_seconds, ev_values) -> float:
    t = np.asarray(times_seconds, dtype=float)
    e = np.asarray(ev_values, dtype=float)
    if len(t) < 2 or np.allclose(t, t[0]):
        return 0.0
    slope = np.polyfit(t, e, 1)[0]
    return float(slope)
