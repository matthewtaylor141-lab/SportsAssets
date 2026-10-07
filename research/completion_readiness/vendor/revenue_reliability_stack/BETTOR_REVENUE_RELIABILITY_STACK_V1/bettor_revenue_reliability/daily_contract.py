from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

@dataclass(frozen=True)
class StrategyReadiness:
    strategy: str
    lifecycle: str
    independent_events: int
    lower_bound_daily_profit: float
    positive_capacity_usd: float
    capital_hour_profit: float
    primary_blocker: str | None = None

@dataclass(frozen=True)
class RevenueReadinessBrief:
    as_of: str
    bankroll_usd: float
    proven_positive_capacity_usd: float
    planned_capital_usd: float
    cash_usd: float
    expected_daily_profit_usd: float
    strategies: list[dict]
    overall_status: str
    reason: str

def build_daily_brief(
    bankroll_usd: float,
    strategies: list[StrategyReadiness],
    allocations: dict[str,float],
    expected_daily_profit_usd: float,
    *,
    as_of: str | None=None,
) -> RevenueReadinessBrief:
    as_of=as_of or datetime.now(timezone.utc).isoformat()
    cap=sum(max(0.0,s.positive_capacity_usd) for s in strategies
            if s.lower_bound_daily_profit>0 and s.lifecycle not in {"SHADOW_ONLY","QUARANTINED","RETIRED"})
    planned=sum(v for k,v in allocations.items() if k!="CASH")
    cash=float(allocations.get("CASH",max(0.0,bankroll_usd-planned)))
    status="READY_WITH_PROVEN_CAPACITY" if planned>0 and expected_daily_profit_usd>0 else "CASH"
    reason="POSITIVE_CAPACITY_ALLOCATED" if status!="CASH" else "NO_PROVEN_POSITIVE_CAPACITY"
    return RevenueReadinessBrief(
        as_of=as_of,
        bankroll_usd=float(bankroll_usd),
        proven_positive_capacity_usd=float(cap),
        planned_capital_usd=float(planned),
        cash_usd=float(cash),
        expected_daily_profit_usd=float(expected_daily_profit_usd),
        strategies=[asdict(s) for s in strategies],
        overall_status=status,
        reason=reason,
    )
