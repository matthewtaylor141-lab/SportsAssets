"""Portfolio stress/tail twin from caller-supplied scenario P&Ls.

Uses frozen scenario outcomes supplied by the existing scenario/correlation
layer. Pure and authority-free.
"""
from __future__ import annotations

from . import common as C

VERSION = "PORTFOLIO_TAIL_TWIN_V1"


def evaluate(scenarios: list[dict], *, confidence=0.95):
    vals=[]
    for s in scenarios:
        pnl=C.num(s.get("portfolio_pnl_usd"))
        if pnl is not None:
            vals.append(pnl)
    if not vals:
        return C.envelope("UNAVAILABLE", "NO_MEASURED_SCENARIO_PNL", version=VERSION)
    vals.sort()
    n=len(vals)
    q=max(0, min(n-1, int((1.0-confidence)*n)))
    var=vals[q]
    tail=vals[:q+1]
    cvar=C.mean(tail)
    return C.envelope("OK", None, sample_n=n, confidence=confidence,
                      expected_pnl_usd=C.rnd(C.mean(vals)),
                      var_loss_usd=C.rnd(max(0.0, -var)),
                      cvar_loss_usd=C.rnd(max(0.0, -(cvar or 0.0))),
                      worst_scenario_pnl_usd=C.rnd(vals[0]),
                      best_scenario_pnl_usd=C.rnd(vals[-1]), version=VERSION)
