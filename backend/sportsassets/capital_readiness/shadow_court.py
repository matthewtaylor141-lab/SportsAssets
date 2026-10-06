"""Decision Shadow Court.

Ranks realistic alternatives that were available at a decision instant using
risk-adjusted after-cost EV per capital-hour. CASH is always present. This is
SHADOW evaluation only; it cannot submit, cancel, size, or promote anything.
"""
from __future__ import annotations

import math
from . import common as C
from . import epistemic as E

VERSION = "DECISION_SHADOW_COURT_V1"
UNCERTAINTY_Z = 1.6448536269514722  # one-sided 95% lower bound


def _alt(a):
    name = str(a.get("name") or "UNKNOWN")
    ev = C.num(a.get("expected_net_usd"))
    capital = C.num(a.get("capital_usd"))
    hold = C.num(a.get("expected_hold_hours"))
    sigma = max(0.0, C.num(a.get("uncertainty_sigma_usd")) or 0.0)
    capacity = C.num(a.get("capacity_usd"))
    corr = max(0.0, C.num(a.get("correlation_penalty_usd")) or 0.0)
    epi = a.get("epistemic") or {}
    conf = C.num(epi.get("confidence_factor"))
    conf = 0.0 if conf is None else C.clamp(conf)

    if name == "CASH":
        return dict(a, expected_net_usd=0.0, lower_bound_usd=0.0,
                    ev_per_capital_hour=0.0, score=0.0,
                    confidence_factor=1.0, eligible=True)
    if ev is None or capital is None or hold is None or capital <= 0 or hold <= 0:
        return dict(a, lower_bound_usd=None, ev_per_capital_hour=None,
                    score=None, eligible=False,
                    refusal="INCOMPLETE_ECONOMIC_INPUT")
    if capacity is not None and capital > capacity + 1e-9:
        return dict(a, lower_bound_usd=None, ev_per_capital_hour=None,
                    score=None, eligible=False,
                    refusal="PROPOSED_CAPITAL_EXCEEDS_CAPACITY")

    conservative_ev = ev * conf - corr
    lower = conservative_ev - UNCERTAINTY_Z * sigma
    evch = lower / (capital * hold)
    eligible = lower > 0 and evch > 0
    return dict(a, expected_net_usd=C.rnd(ev),
                confidence_factor=C.rnd(conf),
                conservative_ev_usd=C.rnd(conservative_ev),
                lower_bound_usd=C.rnd(lower),
                ev_per_capital_hour=C.rnd(evch),
                score=C.rnd(evch), eligible=eligible,
                refusal=None if eligible else "LOWER_BOUND_NOT_POSITIVE")


def judge(*, decision_id: str, chosen: str, alternatives: list[dict]):
    names = {str(a.get("name")) for a in alternatives}
    if "CASH" not in names:
        alternatives = list(alternatives) + [{"name": "CASH"}]
    rows = [_alt(a) for a in alternatives]
    eligible = [r for r in rows if r.get("eligible")]
    best = max(eligible, key=lambda r: r.get("score") or -math.inf) if eligible else next(
        r for r in rows if r.get("name") == "CASH")
    chosen_row = next((r for r in rows if r.get("name") == chosen), None)
    return C.envelope(
        "OK", None, decision_id=decision_id, chosen=chosen,
        shadow_winner=best["name"], shadow_winner_score=best.get("score"),
        chosen_score=None if chosen_row is None else chosen_row.get("score"),
        disagreement=(chosen_row is None or best["name"] != chosen),
        alternatives=rows,
        rule="maximize positive one-sided-LCB after-cost EV per capital-hour; CASH otherwise",
        version=VERSION,
    )


def score_outcome(court: dict, realized_by_alternative: dict[str, float]):
    winner = court.get("shadow_winner")
    chosen = court.get("chosen")
    w = C.num(realized_by_alternative.get(winner))
    c = C.num(realized_by_alternative.get(chosen))
    vals = [C.num(v) for v in realized_by_alternative.values()]
    vals = [v for v in vals if v is not None]
    oracle = max(vals) if vals else None
    return {
        "decision_id": court.get("decision_id"),
        "chosen": chosen,
        "shadow_winner": winner,
        "chosen_realized_usd": C.rnd(c),
        "shadow_winner_realized_usd": C.rnd(w),
        "decision_alpha_vs_shadow_usd": None if c is None or w is None else C.rnd(c - w),
        "oracle_regret_usd": None if c is None or oracle is None else C.rnd(oracle - c),
        "oracle_is_diagnostic_only": True,
    }
