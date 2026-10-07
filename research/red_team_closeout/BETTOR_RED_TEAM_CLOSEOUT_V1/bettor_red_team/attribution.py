from __future__ import annotations
from decimal import Decimal

COMPONENTS=("selection","execution","allocation","management","settlement","outcome_variance")

def reconcile_attribution(*, realized_pnl, components:dict, tolerance=Decimal("0.01"))->dict:
    missing=[k for k in COMPONENTS if k not in components]
    vals={k:Decimal(str(components.get(k,0))) for k in COMPONENTS}
    total=sum(vals.values(),Decimal("0"))
    realized=Decimal(str(realized_pnl))
    residual=realized-total
    blockers=[]
    if missing: blockers.append("ATTRIBUTION_COMPONENTS_MISSING:"+",".join(missing))
    if abs(residual)>Decimal(tolerance): blockers.append("ATTRIBUTION_DOES_NOT_RECONCILE")
    return {"green":not blockers,"realized":realized,"components":vals,
            "component_sum":total,"residual":residual,"blockers":tuple(blockers)}
