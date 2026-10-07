from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class CapacityPoint:
    qty:int
    lower_bound_ev_per_contract:Decimal
    fill_probability:Decimal
    capital_hours:Decimal
    expected_net:Decimal

def capacity_frontier(points:list[CapacityPoint], *,
                      min_fill_probability:Decimal=Decimal("0.80"))->dict:
    pts=sorted(points,key=lambda p:p.qty)
    eligible=[]
    blockers=[]
    prev=0
    for p in pts:
        if p.qty<=prev:
            blockers.append("NON_MONOTONIC_QUANTITY_GRID")
        prev=p.qty
        if p.lower_bound_ev_per_contract>0 and p.fill_probability>=min_fill_probability and p.expected_net>0:
            eligible.append(p)
    max_qty=max((p.qty for p in eligible),default=0)
    best=max(eligible,key=lambda p:p.expected_net,default=None)
    return {"green":bool(eligible) and not blockers,
            "max_positive_qty":max_qty,
            "best_qty":None if best is None else best.qty,
            "best_expected_net":None if best is None else best.expected_net,
            "blockers":tuple(blockers or (() if eligible else ("NO_POSITIVE_CAPACITY",)))}

def deployment_cap(*, requested_turnover:Decimal, proven_positive_capacity:Decimal)->Decimal:
    """Revenue target never forces deployment beyond proven positive capacity."""
    return min(Decimal(requested_turnover),Decimal(proven_positive_capacity))
