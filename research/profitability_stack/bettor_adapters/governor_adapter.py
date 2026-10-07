"""PROFITABILITY GOVERNOR on BETTOR's forward PAPER record.

The statistical unit is the canonical EVENT. Every settled paper position is
reduced to its event; a strategy's observation for an event is its
quantity-weighted realized P&L per contract across all its positions on that
event (repeated evaluations of one game are one observation). The package's
AnytimeBoundedMean confidence sequence runs over those event observations in
settlement order, and capital_graduation decides with:
  * calibration_ok -- >= MIN_EVENTS events, the event-clustered 90% interval of
    (outcome - p) contains 0, and the event-level ECE <= 0.05;
  * execution_ok  -- the EXECUTION TRUTH receipt shows a non-REFUSE path for
    this strategy whose realized check has a positive lower bound;
  * settlement_ok -- every contract traded is SETTLEMENT_PROVEN_COMPATIBLE in
    the Settlement Rule Registry (supplied as evidence; none is today);
  * capacity_usd  -- executable capacity at a positive bound; UNMEASURED = 0.
No strategy is promoted for being least negative; nothing graduated -> CASH.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict

import numpy as np

from common import cluster_mean_ci, window
from bettor_profit_stack.profitability_governor import (AnytimeBoundedMean, capital_graduation,
                                                        strategy_capital_weights)

MIN_EVENTS = 100
# per-contract realized P&L of a binary contract lies in [-(1 + fees), 1];
# fees are a few cents per contract, so [-1.10, 1.00] bounds every observation
LOWER, UPPER = -1.10, 1.00
MAX_ECE = 0.05


def event_observations(positions: list[dict]) -> list[tuple[str, float, float, float]]:
    """[(event, settle_ts, pnl_per_contract, qty)] in settlement order."""
    by = defaultdict(lambda: [0.0, 0.0, 0.0])
    for p in positions:
        b = by[p["event"]]
        b[0] += p["realized_pnl"]; b[1] += p["qty"]
        b[2] = max(b[2], float(p.get("settled_at") or p.get("entered_at") or 0.0))
    obs = [(e, v[2], v[0] / v[1], v[1]) for e, v in by.items() if v[1] > 0]
    return sorted(obs, key=lambda x: x[1])


def calibration(positions: list[dict]) -> dict:
    rows = [p for p in positions if p.get("p") is not None and p.get("payout") is not None]
    if not rows:
        return {"ok": False, "reason": "NO_SETTLED_OUTCOMES_WITH_PROBABILITY", "events": 0}
    ci = cluster_mean_ci([p["payout"] - float(p["p"]) for p in rows], [p["event"] for p in rows], z=1.645)
    # event-level ECE over 10 probability bins
    ev = defaultdict(lambda: [0.0, 0.0, 0])
    for p in rows:
        e = ev[p["event"]]; e[0] += float(p["p"]); e[1] += p["payout"]; e[2] += 1
    pts = [(v[0] / v[2], v[1] / v[2]) for v in ev.values()]
    bins = defaultdict(list)
    for pp, yy in pts:
        bins[min(9, int(pp * 10))].append((pp, yy))
    ece = sum(len(b) / len(pts) * abs(np.mean([x for x, _ in b]) - np.mean([y for _, y in b]))
              for b in bins.values())
    reasons = []
    if ci["events"] < MIN_EVENTS:
        reasons.append("FEWER_THAN_%d_SETTLED_EVENTS" % MIN_EVENTS)
    if ci.get("lower") is None or not (ci["lower"] <= 0 <= ci["upper"]):
        reasons.append("OUTCOME_MINUS_P_INTERVAL_EXCLUDES_ZERO")
    if ece > MAX_ECE:
        reasons.append("EVENT_LEVEL_ECE_ABOVE_%.2f" % MAX_ECE)
    return {"ok": not reasons, "reasons": reasons, "events": ci["events"], "rows": ci["n_rows"],
            "mean_outcome_minus_p": ci["mean"], "interval_90": [ci.get("lower"), ci.get("upper")],
            "event_ece": float(ece)}


def run(positions: list[dict], *, decision_rows_by_strategy: dict, execution_ok_by_strategy: dict,
        settlement_evidence: dict) -> dict:
    settled = [p for p in positions if p["status"] == "SETTLED" and p["event"]]
    open_ = [p for p in positions if p["status"] == "OPEN"]
    settlement_ok = bool(settlement_evidence.get("all_traded_contracts_proven_compatible"))
    by_s = defaultdict(list)
    for p in settled:
        by_s[p["strategy"]].append(p)
    out, states, capacities = {}, {}, {}
    for s, ps in sorted(by_s.items()):
        cs = AnytimeBoundedMean(LOWER, UPPER, alpha=0.05)
        obs = event_observations(ps)
        for _, _, v, _ in obs:
            cs.update(min(UPPER, max(LOWER, v)))
        st = cs.state()
        cal = calibration(ps)
        exe = execution_ok_by_strategy.get(s, {"ok": False, "reason": "NO_EXECUTION_TRUTH_EVIDENCE"})
        dec = capital_graduation(st, calibration_ok=cal["ok"], execution_ok=bool(exe["ok"]),
                                 settlement_ok=settlement_ok, minimum_events=MIN_EVENTS,
                                 capacity_usd=0.0)
        cells = defaultdict(list)
        for p in ps:
            cells["|".join((p["family"], p["regime"]))].append(p)
        out[s] = {
            "decision_rows_total": decision_rows_by_strategy.get(s),
            "settled_positions": len(ps),
            "unique_independent_events": len(obs),
            "realized_pnl_usd": sum(p["realized_pnl"] for p in ps),
            "cost_usd": sum(p["cost"] for p in ps),
            "evidence_state": asdict(st),
            "calibration": cal,
            "execution": exe,
            "settlement_ok": settlement_ok,
            "capacity_usd": {"value": 0.0, "status": "UNMEASURED_TREATED_AS_ZERO"},
            "capital_status": dec.capital_status,
            "capital_reason": dec.reason,
            "all_blockers": [r for r, bad in (
                ("INSUFFICIENT_INDEPENDENT_FORWARD_EVENTS", st.n < MIN_EVENTS),
                ("SETTLEMENT_IDENTITY_NOT_PROVEN", not settlement_ok),
                ("CALIBRATION_NOT_PROVEN", not cal["ok"]),
                ("EXECUTION_ECONOMICS_NOT_PROVEN", not exe["ok"]),
                ("POSITIVE_FORWARD_LOWER_BOUND_NOT_PROVEN", not st.lower_bound > 0),
                ("NO_EXECUTABLE_POSITIVE_CAPACITY", True)) if bad],
            "by_family_regime": {k: {"positions": len(v), "events": len({p["event"] for p in v}),
                                     "realized_pnl_usd": sum(p["realized_pnl"] for p in v)}
                                 for k, v in sorted(cells.items())},
        }
        states[s] = st
        capacities[s] = 0.0
    weights = strategy_capital_weights(states, capacities, bankroll=1.0)
    graduated = [s for s, v in out.items() if v["capital_status"] == "ACTIVE_CHALLENGER"]
    return {
        "module": "PROFITABILITY_GOVERNOR",
        "unit_of_analysis": "CANONICAL_EVENT (positions on one game are one observation)",
        "decision_rows": {"settled_positions": len(settled), "open_positions_excluded": len(open_),
                          "paper_decisions_by_strategy": decision_rows_by_strategy},
        "unique_independent_events": len({p["event"] for p in settled}),
        "event_identity_sources": dict(Counter(p["event_src"] for p in settled)),
        "window": window(p.get("settled_at") or p.get("entered_at") for p in settled),
        "observation_bounds_per_contract": [LOWER, UPPER],
        "settlement_evidence": settlement_evidence,
        "strategies": out,
        "allocation_fraction": weights,
        "capital_status": "CASH" if not graduated else "ACTIVE_CHALLENGER_SHADOW_ONLY",
        "capital_reason": ("NO_STRATEGY_GRADUATED: CASH is the required result while positive "
                           "executable expectancy is not proven") if not graduated else
                          "GRADUATED_STRATEGIES_REMAIN_SHADOW_NO_PRODUCTION_ACTIVATION",
    }
