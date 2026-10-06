"""Capital-scaling digital twin (pure, SHADOW).

Evaluates whether expected after-cost economics survive increasing capital.
It never recommends above measured capacity and never creates authority.
"""
from __future__ import annotations

import math
from . import common as C

VERSION = "CAPITAL_SCALE_TWIN_V1"
DEFAULT_LADDER = (1000, 5000, 25000, 50000, 100000, 250000, 500000)
LCB_Z = 1.6448536269514722


def evaluate(*, base_capital_usd, base_expected_ev_usd, capacity_usd,
             edge_decay_per_1000_usd=0.0, fixed_cost_usd=0.0,
             variable_cost_bps=0.0, uncertainty_sigma_usd=0.0,
             correlation_penalty_bps=0.0, ladder=None):
    ladder = tuple(ladder or DEFAULT_LADDER)
    bcap = C.num(base_capital_usd)
    bev = C.num(base_expected_ev_usd)
    cap = C.num(capacity_usd)
    if not bcap or bcap <= 0 or bev is None or cap is None or cap <= 0:
        return C.envelope("UNAVAILABLE", "INVALID_BASE_OR_CAPACITY", rows=[],
                          recommended_capital_usd=0, version=VERSION)
    base_rate = bev / bcap
    rows = []
    recommendation = 0
    for capital in ladder:
        c = float(capital)
        if c > cap + 1e-9:
            rows.append({"capital_usd": c, "status": "ABOVE_MEASURED_CAPACITY",
                         "expected_net_usd": None, "lower_bound_usd": None,
                         "positive_lower_bound": False})
            continue
        decay = max(0.0, float(edge_decay_per_1000_usd)) * (c / 1000.0)
        gross = c * max(0.0, base_rate - decay)
        variable = c * max(0.0, float(variable_cost_bps)) / 10000.0
        corr = c * max(0.0, float(correlation_penalty_bps)) / 10000.0
        net = gross - max(0.0, float(fixed_cost_usd)) - variable - corr
        sigma = max(0.0, float(uncertainty_sigma_usd)) * math.sqrt(max(c / bcap, 1e-12))
        lower = net - LCB_Z * sigma
        positive = lower > 0
        if positive:
            recommendation = int(c)
        rows.append({"capital_usd": c, "status": "MEASURED",
                     "expected_net_usd": C.rnd(net),
                     "lower_bound_usd": C.rnd(lower),
                     "positive_lower_bound": positive,
                     "effective_edge_bps": C.rnd(10000.0 * net / c)})
    return C.envelope("OK", None, rows=rows,
                      recommended_capital_usd=recommendation,
                      capacity_usd=C.rnd(cap),
                      rule="largest tested rung with positive one-sided 95% lower-bound net EV",
                      version=VERSION)
