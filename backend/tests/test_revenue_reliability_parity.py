"""The production port (sportsassets.revenue_reliability.core) must decide
exactly as the vendored package does, on the same randomized inputs.

THE ORACLE ALWAYS RUNS (red-team closeout, DEFECT 4). The package's
reliability and tournament modules import numpy. numpy is pinned TEST-ONLY in
backend/requirements-test.lock -- the image installs requirements.lock alone
and no production module imports it (tests/test_numpy_is_test_only.py). A
missing numpy is therefore a FAILURE here, never a skip: until this change the
oracle half was `importorskip`ped in CI on every run, the file was taken off
the capital-critical list to keep the verdict green, and the parity proof never
ran. The oracle's bytes are pinned so it cannot be edited into agreement with a
changed port, and every randomized loop asserts that it reached each decision
branch it claims to cover (a parity loop that never reaches CASH, or never
allocates, proves nothing about that branch)."""
from __future__ import annotations

import hashlib
import random
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import pytest

from sportsassets.revenue_reliability import core as C

PKG = Path(__file__).resolve().parents[2] / "research" / "revenue_reliability_stack"

#: sha256 of every oracle module exactly as imported (zip 04d1da83...,
#: research/revenue_reliability_stack/IMPORT_RECEIPT.txt; unchanged since 219d050a)
ORACLE_SHA256 = {
    "__init__.py": "285772e66a988d52b3dfbb152417db82d0c06c34d6ce36ab99beba07c550504e",
    "certification.py": "d222652f3ddb24dd82b088cc3068af0dc95ac1dfdeed328252ba3630c41db51c",
    "counterfactuals.py": "8e801eaa00b7cf4974b4c576a70de0bc77a902a4b2d1405d13bd7c3e8aac36d8",
    "current_contract.py": "c8ffd0986ef44e83b12a6a5a82bd0f4b4eb7117ee07db0d84418484fdc5f6e44",
    "daily_contract.py": "b9a8733dd0693fe60c9ab8776f6b8350e9ca4dd941d8578b58d33b8ff09283b3",
    "integration.py": "0fe30c610a196f1d99d48a504df5f3557198cc4717517ab4cbe94875c5973d75",
    "regime.py": "8edd11ea4695498682c284d232454e9c9d5b725b169d5a2018a46d1b97481db4",
    "reliability.py": "6bfee8c84927080402e810413a28aca226f9b69db84de5664724c794c9e833fe",
    "tournament.py": "33de74f75abdfb18462f62768b00dfb15e1c8cdf8d71f8dfc4b23985e266135f",
}


def _pkg():
    try:
        import numpy  # noqa: F401  -- the oracle's reliability.py / tournament.py import it
    except ModuleNotFoundError as exc:
        pytest.fail("the revenue-reliability parity oracle needs numpy, which backend/requirements-test.lock "
                    "pins for the test environment (%s). A parity proof that cannot run is a failure, never "
                    "a skip." % exc, pytrace=False)
    if str(PKG) not in sys.path:
        sys.path.insert(0, str(PKG))
    import bettor_revenue_reliability as P  # noqa: F401
    from bettor_revenue_reliability import (certification, counterfactuals, current_contract, daily_contract,
                                            regime, reliability, tournament)
    for m in (certification, counterfactuals, current_contract, daily_contract, regime, reliability, tournament):
        assert Path(m.__file__).resolve().parent == (PKG / "bettor_revenue_reliability").resolve(), m.__file__
    return certification, counterfactuals, current_contract, daily_contract, regime, reliability, tournament


def test_the_oracle_is_the_imported_package_byte_for_byte():
    src = PKG / "bettor_revenue_reliability"
    assert sorted(p.name for p in src.glob("*.py")) == sorted(ORACLE_SHA256)
    for name, want in ORACLE_SHA256.items():
        assert hashlib.sha256((src / name).read_bytes()).hexdigest() == want, name


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
    cc = _pkg()[2]
    assert {k: asdict(v) for k, v in cc.AGENTS.items()} == {k: asdict(v) for k, v in C.AGENTS.items()}
    assert tuple(cc.LIFECYCLE_STATES) == C.LIFECYCLE_STATES


def _r(rng, lo, hi):
    return rng.uniform(lo, hi)


def test_certification_tournament_regime_parity():
    cert, _, _, _, reg, _, tour = _pkg()
    rng = random.Random(7)
    seen = Counter()
    for _ in range(400):
        kw = dict(agent_id="X", independent_events=rng.randint(0, 250), expected_value_usd=_r(rng, -100, 100),
                  realized_value_usd=_r(rng, -100, 100), lower_bound_value_per_event=_r(rng, -1, 1),
                  calibration_error=rng.choice([None, _r(rng, 0, 0.1)]),
                  reconciliation_error_usd=rng.choice([None, 0.0, _r(rng, -1, 1)]),
                  false_block_rate=rng.choice([None, _r(rng, 0, 0.5)]), evidence_complete=rng.random() > 0.2)
        a, b = C.certify_agent(C.AgentEvidence(**kw)), cert.certify_agent(cert.AgentEvidence(**kw))
        assert (a.license, a.reason) == (b.license, b.reason)
        seen["license:" + a.license] += 1
        cands = [dict(name="s%d" % i, independent_events=rng.randint(0, 200), mean_net_per_event=_r(rng, -1, 1),
                      lower_bound_net_per_event=_r(rng, -1, 1), capital_hour_profit=rng.choice([None, _r(rng, -1, 1)]),
                      drawdown_usd=_r(rng, 0, 100), evidence_complete=rng.random() > 0.1) for i in range(4)]
        ta = C.run_tournament([C.CandidateMetrics(**c) for c in cands])
        tb = tour.run_tournament([tour.CandidateMetrics(**c) for c in cands])
        assert asdict(ta) == asdict(tb)
        seen["tournament:" + ta.status] += 1
        if ta.selected != "CASH":
            # the absolute-positive champion rule, checked on every promotion
            best = next(c for c in cands if c["name"] == ta.selected)
            assert best["lower_bound_net_per_event"] > 0 and best["independent_events"] >= 100
            assert best["evidence_complete"]
        rk = dict(sport="nfl", family="ML", regime="PRE", independent_events=rng.randint(0, 200),
                  calibration_error=rng.choice([None, _r(rng, 0, 0.1)]),
                  lower_bound_net_ev=rng.choice([None, _r(rng, -1, 1)]), freshness_ok=rng.random() > 0.2,
                  settlement_ok=rng.random() > 0.2, evidence_complete=rng.random() > 0.1)
        ra = C.decide_regime(C.RegimeEvidence(**rk))
        assert asdict(ra) == asdict(reg.decide_regime(reg.RegimeEvidence(**rk)))
        seen["regime:" + ra.reason] += 1
    # every branch the parity claims to cover was actually reached
    for lic in C.LICENSES:
        assert seen["license:" + lic] > 0, lic
    assert seen["tournament:NO_PROMOTION"] > 0 and seen["tournament:CHALLENGER_SELECTED"] > 0, seen
    for why in ("UNMEASURED_OR_INCOMPLETE_EVIDENCE", "FRESHNESS_NOT_PROVEN", "SETTLEMENT_IDENTITY_NOT_PROVEN",
                "INSUFFICIENT_INDEPENDENT_EVENTS", "CALIBRATION_NOT_PROVEN", "POSITIVE_EXECUTABLE_EV_NOT_PROVEN",
                "POSITIVE_REGIME_EVIDENCE"):
        assert seen["regime:" + why] > 0, why


def test_reliability_and_daily_brief_parity():
    _, _, _, daily, _, rel, _ = _pkg()
    import numpy as np
    rng = random.Random(11)
    seen = Counter()
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
        seen["plan:" + ("CASH_ONLY" if list(a.allocations) == ["CASH"] else "ALLOCATED")] += 1
        for s in ss:
            seen["reason:" + a.reasons[s["name"]]] += 1
            got = a.allocations.get(s["name"])
            if got is not None:
                seen["capacity_bound"] += abs(got - s["capacity_usd"]) < 1e-9
                seen["fraction_bound"] += abs(got - 25000.0) < 1e-9
        elig = [s for s in ss if a.reasons[s["name"]] == "ELIGIBLE"]
        if len(elig) >= 2:
            seen["correlation"] += 1
            corr = [[1.0 if i == j else rng.uniform(-1, 1) for j in range(len(elig))] for i in range(len(elig))]
            a2 = C.optimize_reliable_portfolio([C.StrategyEvidence(**s) for s in ss], bankroll=1e5, correlation=corr)
            b2 = rel.optimize_reliable_portfolio([rel.StrategyEvidence(**s) for s in ss], bankroll=1e5,
                                                 correlation=np.array(corr))
            assert a2.allocations.keys() == b2.allocations.keys()
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
        seen["brief:" + da["overall_status"]] += 1
    # every branch the parity claims to cover was actually reached
    assert seen["plan:CASH_ONLY"] > 0 and seen["plan:ALLOCATED"] > 0, seen
    for why in ("ELIGIBLE", "EVIDENCE_INCOMPLETE", "LIFECYCLE_SHADOW_ONLY", "LIFECYCLE_QUARANTINED",
                "LIFECYCLE_RETIRED", "INSUFFICIENT_INDEPENDENT_EVENTS", "LOWER_BOUND_DAILY_PROFIT_NOT_POSITIVE",
                "CAPITAL_HOUR_PROFIT_NOT_POSITIVE"):
        assert seen["reason:" + why] > 0, why
    assert seen["correlation"] > 0 and seen["capacity_bound"] > 0 and seen["fraction_bound"] > 0, seen
    assert seen["brief:CASH"] > 0 and seen["brief:READY_WITH_PROVEN_CAPACITY"] > 0, seen


def test_counterfactual_parity():
    _, cf, *_ = _pkg()
    rng = random.Random(3)
    seen = Counter()
    for _ in range(200):
        x = [_r(rng, -100, 100) for _ in range(3)]
        a, b = C.xavier_management_alpha(*x), cf.xavier_management_alpha(*x)
        assert asdict(a["vs_hold"]) == asdict(b["vs_hold"]) and asdict(a["vs_immediate_exit"]) == asdict(b["vs_immediate_exit"])
        assert a["conservative_incremental_value_usd"] == b["conservative_incremental_value_usd"]
        assert a["positive_against_both"] == b["positive_against_both"]
        seen[a["positive_against_both"]] += 1
        for fa, fb in ((C.archer_execution_alpha, cf.archer_execution_alpha),
                       (C.allie_allocation_alpha, cf.allie_allocation_alpha),
                       (C.derek_entry_alpha, cf.derek_entry_alpha)):
            assert asdict(fa(x[0], x[1])) == asdict(fb(x[0], x[1]))
        # KAREN: the red team's formula SUPERSEDES the package's (which
        # added the false-block cost); production = saved - |false cost|
        k = C.karen_challenge_value(x[0], x[1])
        assert k.incremental_value_usd == pytest.approx(x[0] - abs(x[1]))
        assert C.audrey_reconciliation_score(x[2]) == cf.audrey_reconciliation_score(x[2])
    assert seen[True] > 0 and seen[False] > 0, seen


def test_boundary_cases_parity():
    """The thresholds themselves, where a port's `<` against the package's
    `<=` would hide inside random floats: zero capacity, a lower bound of
    exactly zero, exactly the minimum event count, a capital-hour profit of
    exactly zero, a calibration error of exactly the maximum."""
    cert, _, _, _, reg, rel, tour = _pkg()
    base = dict(name="s", independent_events=100, mean_daily_profit=10.0, lower_bound_daily_profit=5.0,
                daily_std=3.0, max_drawdown=50.0, capital_hour_profit=0.5, capacity_usd=10000.0,
                current_lifecycle="ACTIVE_CHAMPION")
    for over in ({}, {"capacity_usd": 0.0}, {"lower_bound_daily_profit": 0.0}, {"capital_hour_profit": 0.0},
                 {"independent_events": 99}, {"current_lifecycle": "REDUCED_SIZE"},
                 {"current_lifecycle": "ACTIVE_CHALLENGER"}, {"evidence_complete": False}):
        s = dict(base, **over)
        a = C.optimize_reliable_portfolio([C.StrategyEvidence(**s)], bankroll=1e5)
        b = rel.optimize_reliable_portfolio([rel.StrategyEvidence(**s)], bankroll=1e5)
        assert (a.allocations, a.reasons) == (b.allocations, b.reasons), over
        assert a.expected_daily_profit == pytest.approx(b.expected_daily_profit, rel=1e-12, abs=1e-12)
    assert C.optimize_reliable_portfolio([C.StrategyEvidence(**dict(base, capacity_usd=0.0))],
                                         bankroll=1e5).reasons == {"s": "NO_POSITIVE_CAPACITY"}
    for lb, events in ((0.0, 100), (1e-12, 100), (0.5, 99), (0.5, 100)):
        cm = dict(name="c", independent_events=events, mean_net_per_event=1.0, lower_bound_net_per_event=lb)
        assert asdict(C.run_tournament([C.CandidateMetrics(**cm)])) == \
            asdict(tour.run_tournament([tour.CandidateMetrics(**cm)])), (lb, events)
    for ce, lb, events in ((0.05, 0.1, 100), (0.0500001, 0.1, 100), (0.01, 0.0, 100), (0.01, 0.1, 99),
                           (None, 0.1, 100), (0.01, None, 100)):
        rk = dict(sport="nfl", family="ML", regime="PRE", independent_events=events, calibration_error=ce,
                  lower_bound_net_ev=lb, freshness_ok=True, settlement_ok=True)
        assert asdict(C.decide_regime(C.RegimeEvidence(**rk))) == asdict(reg.decide_regime(reg.RegimeEvidence(**rk)))
    for over in ({}, {"lower_bound_value_per_event": 0.0}, {"independent_events": 99},
                 {"calibration_error": 0.05}, {"reconciliation_error_usd": 1e-6}, {"reconciliation_error_usd": 2e-6},
                 {"false_block_rate": 0.2}, {"evidence_complete": False}):
        kw = dict(dict(agent_id="X", independent_events=100, expected_value_usd=1.0, realized_value_usd=1.0,
                       lower_bound_value_per_event=0.1), **over)
        a, b = C.certify_agent(C.AgentEvidence(**kw)), cert.certify_agent(cert.AgentEvidence(**kw))
        assert (a.license, a.reason) == (b.license, b.reason), over


def test_core_never_promotes_least_negative_and_keeps_cash():
    t = C.run_tournament([C.CandidateMetrics("a", 500, -0.01, -0.001), C.CandidateMetrics("b", 500, -2, -1)])
    assert t.selected == "CASH" and t.status == "NO_PROMOTION"
    p = C.optimize_reliable_portfolio([C.StrategyEvidence("a", 500, 10, -1, 5, 10, 1, 1e5, "ACTIVE_CHAMPION")],
                                      bankroll=500000.0)
    assert p.allocations == {"CASH": 500000.0}
    # and the package agrees on both
    _, _, _, _, _, rel, tour = _pkg()
    tb = tour.run_tournament([tour.CandidateMetrics("a", 500, -0.01, -0.001), tour.CandidateMetrics("b", 500, -2, -1)])
    assert (tb.selected, tb.status) == ("CASH", "NO_PROMOTION")
    pb = rel.optimize_reliable_portfolio([rel.StrategyEvidence("a", 500, 10, -1, 5, 10, 1, 1e5, "ACTIVE_CHAMPION")],
                                         bankroll=500000.0)
    assert pb.allocations == {"CASH": 500000.0}


def test_karen_value_is_saved_loss_minus_false_block_cost():
    """RED TEAM CLOSEOUT V1 item 17: $100 saved, $30 false-block cost =
    $70 value added (the vendored package's baseline form gave $130)."""
    from sportsassets.red_team.karen_value import karen_incremental_value
    assert C.karen_challenge_value(100, 30).incremental_value_usd == 70.0
    assert float(karen_incremental_value(100, 30)) == 70.0
    assert C.karen_challenge_value(100, -30).incremental_value_usd == 70.0
    assert C.karen_challenge_value(0, 0).incremental_value_usd == 0.0
