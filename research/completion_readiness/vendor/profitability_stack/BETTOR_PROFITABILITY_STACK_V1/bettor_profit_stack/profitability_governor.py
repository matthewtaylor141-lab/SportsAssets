from __future__ import annotations
from dataclasses import dataclass
from math import log, sqrt
from typing import Iterable
import numpy as np

@dataclass(frozen=True)
class EvidenceState:
    n: int
    mean: float
    lower_bound: float
    upper_bound: float
    alpha_spent: float
    status: str
    reason: str

class AnytimeBoundedMean:
    """Time-uniform confidence sequence via a union bound over Hoeffding bounds.

    Assumptions:
    - event-level observations are independent/conditionally independent enough
      for the chosen unit of analysis;
    - each observation lies in [lower, upper];
    - observations are supplied at the independent event/settlement level, not
      repeated decisions from the same event.

    We spend alpha_t = alpha / (t(t+1)), whose sum is <= alpha. Applying a
    Hoeffding interval at each t and union bounding yields simultaneous coverage
    over all t under the stated assumptions.
    """
    def __init__(self, lower: float, upper: float, alpha: float = 0.05):
        if not upper > lower:
            raise ValueError("upper must exceed lower")
        if not 0 < alpha < 1:
            raise ValueError("alpha must be in (0,1)")
        self.lower=lower; self.upper=upper; self.alpha=alpha
        self.values=[]

    def update(self, value: float) -> EvidenceState:
        x=float(value)
        if x<self.lower-1e-12 or x>self.upper+1e-12:
            raise ValueError("observation outside declared bounded range")
        self.values.append(x)
        return self.state()

    def state(self) -> EvidenceState:
        n=len(self.values)
        if n==0:
            return EvidenceState(0,float("nan"),self.lower,self.upper,0.0,"UNMEASURED","NO_INDEPENDENT_EVENTS")
        mean=float(np.mean(self.values))
        alpha_t=self.alpha/(n*(n+1))
        radius=(self.upper-self.lower)*sqrt(log(2.0/alpha_t)/(2.0*n))
        lb=max(self.lower,mean-radius); ub=min(self.upper,mean+radius)
        if lb>0:
            status="POSITIVE_LOWER_BOUND"; reason="TIME_UNIFORM_LOWER_BOUND_POSITIVE"
        elif ub<0:
            status="NEGATIVE_UPPER_BOUND"; reason="TIME_UNIFORM_UPPER_BOUND_NEGATIVE"
        else:
            status="UNRESOLVED"; reason="CONFIDENCE_SEQUENCE_CROSSES_ZERO"
        return EvidenceState(n,mean,lb,ub,alpha_t,status,reason)

@dataclass(frozen=True)
class GraduationDecision:
    capital_status: str
    reason: str
    evidence: dict

def capital_graduation(
    net_profit_state: EvidenceState,
    *,
    calibration_ok: bool,
    execution_ok: bool,
    settlement_ok: bool,
    minimum_events: int = 100,
    capacity_usd: float = 0.0,
    max_status: str = "ACTIVE_CHAMPION",
) -> GraduationDecision:
    ev={
        "n":net_profit_state.n,
        "mean":net_profit_state.mean,
        "lower_bound":net_profit_state.lower_bound,
        "upper_bound":net_profit_state.upper_bound,
        "calibration_ok":calibration_ok,
        "execution_ok":execution_ok,
        "settlement_ok":settlement_ok,
        "capacity_usd":capacity_usd,
    }
    if net_profit_state.n<minimum_events:
        return GraduationDecision("SHADOW_ONLY","INSUFFICIENT_INDEPENDENT_FORWARD_EVENTS",ev)
    if not settlement_ok:
        return GraduationDecision("SHADOW_ONLY","SETTLEMENT_IDENTITY_NOT_PROVEN",ev)
    if not calibration_ok:
        return GraduationDecision("SHADOW_ONLY","CALIBRATION_NOT_PROVEN",ev)
    if not execution_ok:
        return GraduationDecision("SHADOW_ONLY","EXECUTION_ECONOMICS_NOT_PROVEN",ev)
    if net_profit_state.lower_bound<=0:
        return GraduationDecision("CASH","POSITIVE_FORWARD_LOWER_BOUND_NOT_PROVEN",ev)
    if capacity_usd<=0:
        return GraduationDecision("CASH","NO_EXECUTABLE_POSITIVE_CAPACITY",ev)
    status="ACTIVE_CHALLENGER" if max_status=="ACTIVE_CHAMPION" else max_status
    return GraduationDecision(status,"POSITIVE_FORWARD_EVIDENCE_WITH_CAPACITY",ev)

def strategy_capital_weights(
    strategy_states: dict[str,EvidenceState],
    capacities: dict[str,float],
    bankroll: float,
    max_strategy_fraction: float = 0.25,
) -> dict[str,float]:
    """Allocate only to strategies with positive time-uniform lower bounds."""
    eligible={k:max(0.0,s.lower_bound)*max(0.0,capacities.get(k,0.0))
              for k,s in strategy_states.items() if s.lower_bound>0 and capacities.get(k,0)>0}
    if not eligible:
        return {"CASH":float(bankroll)}
    score_sum=sum(eligible.values())
    out={}
    remaining=float(bankroll)
    for k,score in sorted(eligible.items(),key=lambda x:x[1],reverse=True):
        raw=bankroll*score/score_sum
        amount=min(raw,bankroll*max_strategy_fraction,capacities[k],remaining)
        if amount>0:
            out[k]=amount; remaining-=amount
    out["CASH"]=max(0.0,remaining)
    return out
