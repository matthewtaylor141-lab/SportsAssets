from __future__ import annotations
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class CostBreakdown:
    fees: float = 0.0
    slippage: float = 0.0
    adverse_selection: float = 0.0
    management_cost: float = 0.0
    correlation_charge: float = 0.0
    rebate: float = 0.0

    @property
    def total_cost(self) -> float:
        return (
            self.fees
            + self.slippage
            + self.adverse_selection
            + self.management_cost
            + self.correlation_charge
            - self.rebate
        )


@dataclass(frozen=True)
class EVResult:
    p: float
    fill_price: float
    gross_edge: float
    net_ev_per_contract: float
    fill_probability: float
    expected_profit_per_posted_contract: float
    costs: CostBreakdown
    admissible: bool

    def as_dict(self) -> dict:
        d = asdict(self)
        d["costs"] = asdict(self.costs)
        return d


def binary_taker_ev(p: float, fill_price: float, costs: CostBreakdown) -> EVResult:
    p = float(p)
    q = float(fill_price)
    gross = p - q
    net = gross - costs.total_cost
    return EVResult(
        p=p,
        fill_price=q,
        gross_edge=gross,
        net_ev_per_contract=net,
        fill_probability=1.0,
        expected_profit_per_posted_contract=net,
        costs=costs,
        admissible=net > 0,
    )


def binary_maker_ev(
    p: float,
    conditional_fill_price: float,
    fill_probability: float,
    costs_if_filled: CostBreakdown,
) -> EVResult:
    pf = min(1.0, max(0.0, float(fill_probability)))
    q = float(conditional_fill_price)
    gross = float(p) - q
    net_if_filled = gross - costs_if_filled.total_cost
    expected = pf * net_if_filled
    return EVResult(
        p=float(p),
        fill_price=q,
        gross_edge=gross,
        net_ev_per_contract=net_if_filled,
        fill_probability=pf,
        expected_profit_per_posted_contract=expected,
        costs=costs_if_filled,
        admissible=expected > 0 and net_if_filled > 0,
    )
