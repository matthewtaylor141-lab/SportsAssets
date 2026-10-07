from __future__ import annotations
from dataclasses import dataclass
from math import sqrt


@dataclass(frozen=True)
class Allocation:
    stake_fraction: float
    stake_usd: float
    conservative_probability: float
    reason: str


def conservative_probability(p: float, standard_error: float, z: float = 1.0) -> float:
    """One-sided uncertainty haircut. Default z=1 is intentionally configurable."""
    return min(1.0, max(0.0, float(p) - float(z) * max(0.0, float(standard_error))))


def binary_kelly_fraction(p: float, price: float) -> float:
    """Kelly fraction for a $1 binary contract bought at price q."""
    p = float(p)
    q = float(price)
    if not 0 < q < 1:
        raise ValueError("price must be between 0 and 1")
    edge = p - q
    if edge <= 0:
        return 0.0
    return edge / (1.0 - q)


def allocate_binary(
    bankroll: float,
    p: float,
    price: float,
    standard_error: float = 0.0,
    fractional_kelly: float = 0.25,
    correlation_haircut: float = 1.0,
    capacity_haircut: float = 1.0,
    max_fraction: float = 0.02,
    z: float = 1.0,
    all_in_cost_per_contract: float = 0.0,
) -> Allocation:
    pc = conservative_probability(p, standard_error, z=z)
    effective_price = float(price) + float(all_in_cost_per_contract)
    if effective_price >= 1.0 or pc <= effective_price:
        return Allocation(0.0, 0.0, pc, "CASH_WAIT_NON_POSITIVE_CONSERVATIVE_EDGE")

    raw = binary_kelly_fraction(pc, effective_price)
    f = raw * max(0.0, float(fractional_kelly))
    f *= min(1.0, max(0.0, float(correlation_haircut)))
    f *= min(1.0, max(0.0, float(capacity_haircut)))
    f = min(max(0.0, f), max(0.0, float(max_fraction)))
    return Allocation(f, float(bankroll) * f, pc, "ALLOCATE_POSITIVE_CONSERVATIVE_EDGE" if f > 0 else "CASH")
