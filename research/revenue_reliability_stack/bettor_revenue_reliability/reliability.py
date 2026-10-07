from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence
import numpy as np

@dataclass(frozen=True)
class StrategyEvidence:
    name: str
    independent_events: int
    mean_daily_profit: float
    lower_bound_daily_profit: float
    daily_std: float
    max_drawdown: float
    capital_hour_profit: float
    capacity_usd: float
    current_lifecycle: str
    evidence_complete: bool = True

@dataclass(frozen=True)
class PortfolioPlan:
    allocations: dict[str,float]
    expected_daily_profit: float
    reliability_score: float
    cash: float
    reasons: dict[str,str]

def _eligible(s: StrategyEvidence, min_events: int) -> tuple[bool,str]:
    if not s.evidence_complete:
        return False,"EVIDENCE_INCOMPLETE"
    if s.current_lifecycle in {"SHADOW_ONLY","QUARANTINED","RETIRED"}:
        return False,f"LIFECYCLE_{s.current_lifecycle}"
    if s.independent_events < min_events:
        return False,"INSUFFICIENT_INDEPENDENT_EVENTS"
    if s.lower_bound_daily_profit <= 0:
        return False,"LOWER_BOUND_DAILY_PROFIT_NOT_POSITIVE"
    if s.capital_hour_profit <= 0:
        return False,"CAPITAL_HOUR_PROFIT_NOT_POSITIVE"
    if s.capacity_usd <= 0:
        return False,"NO_POSITIVE_CAPACITY"
    return True,"ELIGIBLE"

def optimize_reliable_portfolio(
    strategies: Sequence[StrategyEvidence],
    *,
    bankroll: float,
    correlation: np.ndarray | None = None,
    minimum_events: int = 100,
    max_strategy_fraction: float = 0.25,
    reliability_penalty: float = 1.0,
) -> PortfolioPlan:
    names=[s.name for s in strategies]
    reasons={}
    eligible=[]
    for s in strategies:
        ok,reason=_eligible(s,minimum_events)
        reasons[s.name]=reason
        if ok: eligible.append(s)

    if not eligible:
        return PortfolioPlan({"CASH":float(bankroll)},0.0,0.0,float(bankroll),reasons)

    # Score only absolute positive evidence. Reward lower bound and capital-hour
    # productivity; penalize variability and drawdown. This is not a live
    # optimizer; it produces shadow weights for the existing allocator contract.
    raw=[]
    for s in eligible:
        denom=max(1e-9, s.daily_std + reliability_penalty*abs(s.max_drawdown)/100.0)
        score=max(0.0,s.lower_bound_daily_profit)*max(0.0,s.capital_hour_profit)/denom
        raw.append(score)
    raw=np.asarray(raw,dtype=float)
    if raw.sum()<=0:
        return PortfolioPlan({"CASH":float(bankroll)},0.0,0.0,float(bankroll),reasons)

    weights=raw/raw.sum()

    # Correlation haircut: higher average absolute correlation to the eligible
    # set reduces allocation. If correlation is not measured, do not fabricate
    # diversification benefit; leave weights unchanged and let CASH absorb caps.
    if correlation is not None:
        corr=np.asarray(correlation,dtype=float)
        if corr.shape!=(len(eligible),len(eligible)):
            raise ValueError("correlation shape must match eligible strategies")
        penalties=[]
        for i in range(len(eligible)):
            others=[abs(corr[i,j]) for j in range(len(eligible)) if i!=j]
            avg=sum(others)/len(others) if others else 0.0
            penalties.append(max(0.0,1.0-avg))
        weights*=np.asarray(penalties)
        if weights.sum()>0:
            weights/=weights.sum()

    allocations={}
    remaining=float(bankroll)
    for i,s in sorted(enumerate(eligible),key=lambda z:weights[z[0]],reverse=True):
        target=bankroll*float(weights[i])
        cap=min(bankroll*max_strategy_fraction,s.capacity_usd,remaining)
        amt=max(0.0,min(target,cap))
        if amt>0:
            allocations[s.name]=amt
            remaining-=amt

    allocations["CASH"]=max(0.0,remaining)
    expected=sum(
        allocations.get(s.name,0.0)/max(1.0,s.capacity_usd)*s.mean_daily_profit
        for s in eligible
    )
    portfolio_std=sum(
        allocations.get(s.name,0.0)/max(1.0,s.capacity_usd)*s.daily_std
        for s in eligible
    )
    reliability=expected/max(1e-9,portfolio_std) if expected>0 else 0.0
    return PortfolioPlan(allocations,expected,reliability,allocations["CASH"],reasons)
