"""The production port (sportsassets.revenue_reliability.core) must decide
exactly as the vendored package does. The package needs numpy; when numpy is
absent (the production image) the oracle half is skipped, the contract half
still runs."""
from __future__ import annotations

import random
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from sportsassets.revenue_reliability import core as C

PKG = Path(__file__).resolve().parents[2] / "research" / "revenue_reliability_stack"


def _pkg():
    pytest.importorskip("numpy")
    if str(PKG) not in sys.path:
        sys.path.insert(0, str(PKG))
    import bettor_revenue_reliability as P  # noqa: F401
    from bettor_revenue_reliability import (certification, counterfactuals, current_contract, daily_contract,
                                            regime, reliability, tournament)
    return certification, counterfactuals, current_contract, daily_contract, regime, reliability, tournament


def test_agent_contracts_match_the_package_and_grant_nothing():
    assert {k: (a.display_name, a.current_authority) for k, a in C.AGENTS.items()} == {
        "DEREK": ("Derek", "ENTRY_REQUEST_THROUGH_GATED_PATH"),
        "KAREN": ("Karen", "CHALLENGE_ONLY_ZERO_AUTHORITY"),
        "CHIEF_ALLOCATOR": ("Allie", "SHADOW_WEIGHTS_ONLY"),
        "ARCHER": ("Archer", "SHADOW_ONLY"),
        "XAVIER": ("Xavier", "MANAGEMENT_DISPATCH_THROUGH_CLAIM_PATH"),
        "AUDREY": ("Audrey", "AUDIT_NO_ORDER_PATH"),
        "SCOUT": ("Scout", "RESEARCH_SHADOW_ONLY"),
        "ADRIANA": ("Adriana", "SHADOW_ONLY")}
    assert not any(a.can_grant_capital for a in C.AGENTS.values())
    assert C.NO_ENTRY_STATES == {"SHADOW_ONLY", "QUARANTINED", "RETIRED"}
    try:
        cc = _pkg()[2]
    except pytest.skip.Exception:
        return
    assert {k: asdict(v) for k, v in cc.AGENTS.items()} == {k: asdict(v) for k, v in C.AGENTS.items()}
    assert tuple(cc.LIFECYCLE_STATES) == C.LIFECYCLE_STATES


def _r(rng, lo, hi):
    return rng.uniform(lo, hi)


def test_certification_tournament_regime_parity():
    cert, _, _, _, reg, _, tour = _pkg()
    rng = random.Random(7)
    for _ in range(400):
        kw = dict(agent_id="X", independent_events=rng.randint(0, 250), expected_value_usd=_r(rng, -100, 100),
                  realized_value_usd=_r(rng, -100, 100), lower_bound_value_per_event=_r(rng, -1, 1),
                  calibration_error=rng.choice([None, _r(rng, 0, 0.1)]),
                  reconciliation_error_usd=rng.choice([None, 0.0, _r(rng, -1, 1)]),
                  false_block_rate=rng.choice([None, _r(rng, 0, 0.5)]), evidence_complete=rng.random() > 0.2)
        a, b = C.certify_agent(C.AgentEvidence(**kw)), cert.certify_agent(cert.AgentEvidence(**kw))
        assert (a.license, a.reason) == (b.license, b.reason)
        cands = [dict(name="s%d" % i, independent_events=rng.randint(0, 200), mean_net_per_event=_r(rng, -1, 1),
                      lower_bound_net_per_event=_r(rng, -1, 1), capital_hour_profit=rng.choice([None, _r(rng, -1, 1)]),
                      drawdown_usd=_r(rng, 0, 100), evidence_complete=rng.random() > 0.1) for i in range(4)]
        ta = C.run_tournament([C.CandidateMetrics(**c) for c in cands])
        tb = tour.run_tournament([tour.CandidateMetrics(**c) for c in cands])
        assert asdict(ta) == asdict(tb)
        rk = dict(sport="nfl", family="ML", regime="PRE", independent_events=rng.randint(0, 200),
                  calibration_error=rng.choice([None, _r(rng, 0, 0.1)]),
                  lower_bound_net_ev=rng.choice([None, _r(rng, -1, 1)]), freshness_ok=rng.random() > 0.2,
                  settlement_ok=rng.random() > 0.2, evidence_complete=rng.random() > 0.1)
        assert asdict(C.decide_regime(C.RegimeEvidence(**rk))) == asdict(reg.decide_regime(reg.RegimeEvidence(**rk)))


def test_reliability_and_daily_brief_parity():
    _, _, _, daily, _, rel, _ = _pkg()
    import numpy as np
    rng = random.Random(11)
    for _ in range(300):
        n = rng.randint(1, 5)
        ss = [dict(name="s%d" % i, independent_events=rng.randint(50, 250), mean_daily_profit=_r(rng, -50, 200),
                   lower_bound_daily_profit=_r(rng, -50, 150), daily_std=_r(rng, 1, 80), max_drawdown=_r(rng, 0, 500),
                   capital_hour_profit=_r(rng, -1, 2), capacity_usd=_r(rng, 0, 60000),
                   current_lifecycle=rng.choice(C.LIFECYCLE_STATES), evidence_complete=rng.random() > 0.1)
              for i in range(n)]
        a = C.optimize_reliable_portfolio([C.StrategyEvidence(**s) for s in ss], bankroll=100000.0)
        b = rel.optimize_reliable_portfolio([rel.StrategyEvidence(**s) for s in ss], bankroll=100000.0)
        assert a.allocations.keys() == b.allocations.keys() and a.reasons == b.reasons
        for k in a.allocations:
            assert a.allocations[k] == pytest.approx(b.allocations[k], rel=1e-9, abs=1e-9)
        assert a.expected_daily_profit == pytest.approx(b.expected_daily_profit, rel=1e-9, abs=1e-9)
        elig = [s for s in ss if a.reasons[s["name"]] == "ELIGIBLE"]
        if len(elig) >= 2:
            corr = [[1.0 if i == j else rng.uniform(-1, 1) for j in range(len(elig))] for i in range(len(elig))]
            a2 = C.optimize_reliable_portfolio([C.StrategyEvidence(**s) for s in ss], bankroll=1e5, correlation=corr)
            b2 = rel.optimize_reliable_portfolio([rel.StrategyEvidence(**s) for s in ss], bankroll=1e5,
                                                 correlation=np.array(corr))
            for k in a2.allocations:
                assert a2.allocations[k] == pytest.approx(b2.allocations[k], rel=1e-9, abs=1e-9)
        readiness = [dict(strategy=s["name"], lifecycle=s["current_lifecycle"],
                          independent_events=s["independent_events"],
                          lower_bound_daily_profit=s["lower_bound_daily_profit"],
                          positive_capacity_usd=s["capacity_usd"], capital_hour_profit=s["capital_hour_profit"])
                     for s in ss]
        da = C.build_daily_brief(1e5, [C.StrategyReadiness(**r) for r in readiness], a.allocations,
                                 a.expected_daily_profit, as_of="t")
        db = asdict(daily.build_daily_brief(1e5, [daily.StrategyReadiness(**r) for r in readiness], b.allocations,
                                            b.expected_daily_profit, as_of="t"))
        assert da.keys() == db.keys()
        for k in da:
            if isinstance(da[k], float):
                assert da[k] == pytest.approx(db[k], rel=1e-9, abs=1e-9)
            else:
                assert da[k] == db[k]


def test_counterfactual_parity():
    _, cf, *_ = _pkg()
    rng = random.Random(3)
    for _ in range(200):
        x = [_r(rng, -100, 100) for _ in range(3)]
        a, b = C.xavier_management_alpha(*x), cf.xavier_management_alpha(*x)
        assert asdict(a["vs_hold"]) == asdict(b["vs_hold"]) and asdict(a["vs_immediate_exit"]) == asdict(b["vs_immediate_exit"])
        assert a["conservative_incremental_value_usd"] == b["conservative_incremental_value_usd"]
        for fa, fb in ((C.archer_execution_alpha, cf.archer_execution_alpha),
                       (C.allie_allocation_alpha, cf.allie_allocation_alpha),
                       (C.derek_entry_alpha, cf.derek_entry_alpha)):
            assert asdict(fa(x[0], x[1])) == asdict(fb(x[0], x[1]))
        # KAREN: the red team's formula SUPERSEDES the package's (which
        # added the false-block cost); production = saved - |false cost|
        k = C.karen_challenge_value(x[0], x[1])
        assert k.incremental_value_usd == pytest.approx(x[0] - abs(x[1]))
        assert C.audrey_reconciliation_score(x[2]) == cf.audrey_reconciliation_score(x[2])


def test_core_never_promotes_least_negative_and_keeps_cash():
    t = C.run_tournament([C.CandidateMetrics("a", 500, -0.01, -0.001), C.CandidateMetrics("b", 500, -2, -1)])
    assert t.selected == "CASH" and t.status == "NO_PROMOTION"
    p = C.optimize_reliable_portfolio([C.StrategyEvidence("a", 500, 10, -1, 5, 10, 1, 1e5, "ACTIVE_CHAMPION")],
                                      bankroll=500000.0)
    assert p.allocations == {"CASH": 500000.0}



def test_karen_value_is_saved_loss_minus_false_block_cost():
    """RED TEAM CLOSEOUT V1 item 17: $100 saved, $30 false-block cost =
    $70 value added (the vendored package's baseline form gave $130)."""
    from sportsassets.red_team.karen_value import karen_incremental_value
    assert C.karen_challenge_value(100, 30).incremental_value_usd == 70.0
    assert float(karen_incremental_value(100, 30)) == 70.0
    assert C.karen_challenge_value(100, -30).incremental_value_usd == 70.0
    assert C.karen_challenge_value(0, 0).incremental_value_usd == 0.0
