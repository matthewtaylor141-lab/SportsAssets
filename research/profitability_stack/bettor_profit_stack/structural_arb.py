from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Iterable, Sequence
import numpy as np
from scipy.optimize import linprog

@dataclass(frozen=True)
class Contract:
    contract_id: str
    venue: str
    price: float
    payoff_by_state: tuple[float, ...]
    max_qty: float = 1.0
    fee_per_contract: float = 0.0
    slippage_per_contract: float = 0.0
    settlement_key: str = ""

    @property
    def all_in_price(self) -> float:
        return float(self.price) + float(self.fee_per_contract) + float(self.slippage_per_contract)

@dataclass(frozen=True)
class ArbPortfolio:
    quantities: dict[str, float]
    total_cost: float
    guaranteed_payout: float
    guaranteed_profit: float
    roi_on_cost: float
    executable: bool
    settlement_states: int
    reason: str

def complement_pair(
    yes_price: float,
    no_price: float,
    yes_fee: float = 0.0,
    no_fee: float = 0.0,
    slippage: float = 0.0,
) -> ArbPortfolio:
    cost = yes_price + no_price + yes_fee + no_fee + slippage
    profit = 1.0 - cost
    return ArbPortfolio(
        quantities={"YES": 1.0, "NO": 1.0},
        total_cost=cost,
        guaranteed_payout=1.0,
        guaranteed_profit=profit,
        roi_on_cost=profit / cost if cost > 0 else 0.0,
        executable=profit > 0,
        settlement_states=2,
        reason="LOCKED_POSITIVE_PROFIT" if profit > 0 else "NO_LOCKED_PROFIT_AFTER_COSTS",
    )

def exhaustive_basket(prices: Sequence[float], fees: float = 0.0, slippage: float = 0.0) -> ArbPortfolio:
    cost = float(sum(prices)) + float(fees) + float(slippage)
    profit = 1.0 - cost
    return ArbPortfolio(
        quantities={f"OUTCOME_{i}": 1.0 for i in range(len(prices))},
        total_cost=cost,
        guaranteed_payout=1.0,
        guaranteed_profit=profit,
        roi_on_cost=profit / cost if cost > 0 else 0.0,
        executable=profit > 0,
        settlement_states=len(prices),
        reason="LOCKED_POSITIVE_PROFIT" if profit > 0 else "NO_LOCKED_PROFIT_AFTER_COSTS",
    )

def solve_superhedge(
    contracts: Sequence[Contract],
    target_payout: float = 1.0,
    minimum_profit: float = 0.0,
) -> ArbPortfolio:
    """Find cheapest non-negative portfolio paying >= target in every state.

    This is a pure long-only superhedge solver. It does not assume logical
    equivalence beyond the supplied payoff vectors. Settlement compatibility
    must therefore be encoded explicitly in payoff_by_state.
    """
    if not contracts:
        raise ValueError("contracts required")
    n_states = len(contracts[0].payoff_by_state)
    if n_states == 0 or any(len(c.payoff_by_state) != n_states for c in contracts):
        raise ValueError("all contracts must use the same non-empty settlement state space")
    cvec = np.array([c.all_in_price for c in contracts], dtype=float)
    payoff = np.array([c.payoff_by_state for c in contracts], dtype=float).T
    # linprog uses A_ub x <= b_ub; require payoff x >= target.
    A_ub = -payoff
    b_ub = -np.full(n_states, float(target_payout))
    bounds = [(0.0, max(0.0, float(c.max_qty))) for c in contracts]
    res = linprog(cvec, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if not res.success:
        return ArbPortfolio({}, float("inf"), 0.0, -float("inf"), 0.0, False, n_states, "NO_FEASIBLE_SUPERHEDGE")
    qty = np.asarray(res.x)
    state_payouts = payoff @ qty
    guarantee = float(state_payouts.min())
    cost = float(cvec @ qty)
    profit = guarantee - cost
    qmap = {contracts[i].contract_id: float(qty[i]) for i in range(len(contracts)) if qty[i] > 1e-9}
    return ArbPortfolio(
        quantities=qmap,
        total_cost=cost,
        guaranteed_payout=guarantee,
        guaranteed_profit=profit,
        roi_on_cost=profit / cost if cost > 0 else 0.0,
        executable=profit > float(minimum_profit),
        settlement_states=n_states,
        reason="LOCKED_POSITIVE_PROFIT" if profit > float(minimum_profit) else "NO_LOCKED_PROFIT_AFTER_COSTS",
    )

def settlement_compatible(a: Contract, b: Contract) -> bool:
    return bool(a.settlement_key and a.settlement_key == b.settlement_key)

def implication_violation(
    stronger_yes_price: float,
    weaker_yes_price: float,
    costs: float = 0.0,
) -> dict:
    """If A implies B, fair YES(A) cannot exceed YES(B) absent frictions."""
    raw = float(stronger_yes_price) - float(weaker_yes_price)
    after = raw - float(costs)
    return {
        "raw_violation": raw,
        "after_costs": after,
        "candidate": after > 0,
        "reason": "LOGICAL_PRICE_BOUND_VIOLATED" if after > 0 else "NO_EXECUTABLE_LOGIC_VIOLATION",
    }
