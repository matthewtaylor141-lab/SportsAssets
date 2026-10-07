from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class ArbResult:
    kind: str
    total_cost: float
    guaranteed_payout: float
    guaranteed_profit: float
    roi_on_cost: float
    executable: bool
    reason: str


def cross_venue_complement_arb(
    yes_price: float,
    no_price: float,
    yes_fee: float = 0.0,
    no_fee: float = 0.0,
    slippage: float = 0.0,
) -> ArbResult:
    """Buy YES at one venue and NO at another. Pays $1 regardless of outcome."""
    cost = float(yes_price) + float(no_price) + float(yes_fee) + float(no_fee) + float(slippage)
    profit = 1.0 - cost
    return ArbResult(
        kind="CROSS_VENUE_COMPLEMENT",
        total_cost=cost,
        guaranteed_payout=1.0,
        guaranteed_profit=profit,
        roi_on_cost=profit / cost if cost > 0 else 0.0,
        executable=profit > 0,
        reason="POSITIVE_AFTER_COSTS" if profit > 0 else "NO_GUARANTEED_EDGE_AFTER_COSTS",
    )


def mutually_exclusive_basket_arb(
    outcome_prices,
    total_fees: float = 0.0,
    slippage: float = 0.0,
) -> ArbResult:
    """Buy one contract for every mutually exclusive and exhaustive outcome."""
    cost = sum(float(x) for x in outcome_prices) + float(total_fees) + float(slippage)
    profit = 1.0 - cost
    return ArbResult(
        kind="EXHAUSTIVE_BASKET",
        total_cost=cost,
        guaranteed_payout=1.0,
        guaranteed_profit=profit,
        roi_on_cost=profit / cost if cost > 0 else 0.0,
        executable=profit > 0,
        reason="POSITIVE_AFTER_COSTS" if profit > 0 else "NO_GUARANTEED_EDGE_AFTER_COSTS",
    )
