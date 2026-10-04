"""BETTOR Marginal Capital Value reference model.

RESEARCH / SHADOW ONLY. No order, capital, limit, policy or authority behavior.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable

@dataclass(frozen=True)
class Tranche:
    opportunity_id: str
    tranche_id: str
    capital_usd: float
    expected_net_usd: float
    expected_hours_to_release: float
    fill_probability: float = 1.0
    confidence: float = 1.0
    risk_multiplier: float = 1.0
    correlation_multiplier: float = 1.0

    @property
    def capital_hours(self) -> float:
        return self.capital_usd * self.expected_hours_to_release

    @property
    def raw_ppch(self) -> float:
        return self.expected_net_usd / self.capital_hours

    @property
    def marginal_value(self) -> float:
        return (
            self.expected_net_usd
            * self.fill_probability
            * self.confidence
            * self.risk_multiplier
            * self.correlation_multiplier
            / self.capital_hours
        )

def allocate(tranches: Iterable[Tranche], available_capital_usd: float,
             reserve_usd: float = 0.0, hurdle_ppch: float = 0.0):
    usable = max(0.0, float(available_capital_usd) - float(reserve_usd))
    ranked = sorted(tranches, key=lambda t: (-t.marginal_value,
                                             t.opportunity_id, t.tranche_id))
    chosen, rejected = [], []
    remaining = usable
    for t in ranked:
        if t.capital_usd <= 0 or t.expected_hours_to_release <= 0:
            rejected.append((t, "INVALID_CAPITAL_OR_DURATION")); continue
        if t.expected_net_usd <= 0:
            rejected.append((t, "NON_POSITIVE_EXPECTED_NET")); continue
        if not (0 <= t.fill_probability <= 1 and 0 <= t.confidence <= 1):
            rejected.append((t, "INVALID_PROBABILITY_OR_CONFIDENCE")); continue
        if t.marginal_value <= hurdle_ppch:
            rejected.append((t, "BELOW_MARGINAL_HURDLE")); continue
        if t.capital_usd > remaining + 1e-9:
            rejected.append((t, "INSUFFICIENT_REMAINING_CAPITAL")); continue
        chosen.append(t); remaining -= t.capital_usd
    return {
        "usable_capital_usd": usable,
        "allocated_capital_usd": sum(t.capital_usd for t in chosen),
        "remaining_capital_usd": remaining,
        "expected_net_usd": sum(t.expected_net_usd * t.fill_probability
                                for t in chosen),
        "expected_capital_hours": sum(t.capital_hours for t in chosen),
        "chosen": chosen,
        "rejected": rejected,
        "authority": "RESEARCH_SHADOW_ONLY",
    }
