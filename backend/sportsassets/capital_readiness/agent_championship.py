"""Economic championship for BETTOR agents.

Agents are judged on incremental economic value in the part of the process they
control, not on conversational output, raw activity, or raw portfolio P&L.
"""
from __future__ import annotations

from collections import defaultdict
from . import common as C

VERSION = "AGENT_ECONOMIC_CHAMPIONSHIP_V1"
MIN_SAMPLES = 20

ROLE_METRIC = {
    "DEREK": "discovery_alpha_usd",
    "XAVIER": "management_alpha_usd",
    "ARCHER": "execution_alpha_usd",
    "ALLIE": "allocation_alpha_usd",
    "ADRIANA": "arbitrage_alpha_usd",
    "AUDREY": "prevented_loss_usd",
    "SCOUT": "feature_lift_usd",
    "KAREN": "prevented_loss_usd",
}


def evaluate(rows: list[dict]):
    by = defaultdict(list)
    for r in rows:
        agent = str(r.get("agent") or "UNKNOWN").upper()
        metric = ROLE_METRIC.get(agent, str(r.get("metric") or "economic_alpha_usd"))
        v = C.num(r.get(metric))
        if v is None:
            v = C.num(r.get("economic_alpha_usd"))
        if v is not None:
            by[agent].append(v)
    out = {}
    for agent, vals in sorted(by.items()):
        lo, hi = C.ci95_mean(vals)
        m = C.mean(vals)
        n = len(vals)
        positive = n >= MIN_SAMPLES and lo is not None and lo > 0
        out[agent] = {
            "role_metric": ROLE_METRIC.get(agent, "economic_alpha_usd"),
            "sample_n": n,
            "mean_incremental_value_usd": C.rnd(m),
            "ci95_low_usd": C.rnd(lo),
            "ci95_high_usd": C.rnd(hi),
            "economically_positive": positive,
            "status": "ELIGIBLE" if positive else "INSUFFICIENT_OR_NONPOSITIVE",
        }
    return C.envelope("OK" if out else "EMPTY", None if out else "NO_AGENT_ECONOMIC_OBSERVATIONS",
                      agents=out, minimum_samples=MIN_SAMPLES, version=VERSION)
