from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from math import sqrt

@dataclass(frozen=True)
class ResidualObservation:
    mechanism: str
    expected: Decimal
    realized: Decimal

def mechanism_breaker(rows: list[ResidualObservation], *, min_n:int=30,
                      z:Decimal=Decimal("1.645"),
                      absolute_loss_limit:Decimal|None=None) -> dict:
    """Sleeve-specific expected-vs-realized circuit breaker.

    The residual is realized-expected. A materially negative lower/upper
    confidence regime disables only the failing sleeve, not unrelated sleeves.
    """
    groups={}
    for r in rows:
        groups.setdefault(r.mechanism,[]).append(Decimal(r.realized)-Decimal(r.expected))
    out={}
    for mech,rs in groups.items():
        n=len(rs); mean=sum(rs,Decimal("0"))/n
        if n<2:
            sd=None; upper=None
        else:
            var=sum((x-mean)*(x-mean) for x in rs)/Decimal(n-1)
            sd=var.sqrt()
            upper=mean + Decimal(z)*sd/Decimal(n).sqrt()
        loss=sum(rs,Decimal("0"))
        if n<min_n:
            status="SHADOW_ONLY"; reason="INSUFFICIENT_INDEPENDENT_OBSERVATIONS"
        elif upper is None or upper < 0:
            status="DISABLED"; reason="NEGATIVE_RESIDUAL_CONFIDENCE_BOUND"
        elif absolute_loss_limit is not None and loss < -abs(Decimal(absolute_loss_limit)):
            status="DISABLED"; reason="ABSOLUTE_RESIDUAL_LOSS_LIMIT"
        else:
            status="ELIGIBLE"; reason=None
        out[mech]={"status":status,"reason":reason,"n":n,"mean_residual":mean,
                   "upper_90":upper,"cumulative_residual":loss}
    return out

def all_profit_breakers_green(report:dict)->bool:
    return all(v["status"]=="ELIGIBLE" for v in report.values()) if report else False
