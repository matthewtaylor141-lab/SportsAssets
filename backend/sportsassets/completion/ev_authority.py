from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class ExecutableEconomics:
    p_used: float
    executable_price: float
    fee_per_contract: float = 0.0
    spread_slippage_per_contract: float = 0.0
    adverse_selection_per_contract: float = 0.0
    management_cost_per_contract: float = 0.0
    correlation_haircut_per_contract: float = 0.0
    incentive_per_contract: float = 0.0
    probability_uncertainty_per_contract: float = 0.0
    execution_uncertainty_per_contract: float = 0.0

@dataclass(frozen=True)
class EVDecision:
    net_ev_per_contract: float
    lower_bound_ev_per_contract: float
    verdict: str
    reason: str

def evaluate_all_in_ev(x: ExecutableEconomics) -> EVDecision:
    gross=float(x.p_used)-float(x.executable_price)
    net=(gross
         - float(x.fee_per_contract)
         - float(x.spread_slippage_per_contract)
         - float(x.adverse_selection_per_contract)
         - float(x.management_cost_per_contract)
         - float(x.correlation_haircut_per_contract)
         + float(x.incentive_per_contract))
    lb=(net
        - float(x.probability_uncertainty_per_contract)
        - float(x.execution_uncertainty_per_contract))
    if lb <= 0:
        return EVDecision(net,lb,"CASH","LOWER_BOUND_NET_EXECUTABLE_EV_NOT_POSITIVE")
    return EVDecision(net,lb,"ELIGIBLE_FOR_EXISTING_GATED_PATH","POSITIVE_ALL_IN_EXECUTABLE_EV_LOWER_BOUND")
