from completion_logic.probability_authority import ForecastEvidence, choose_probability
from completion_logic.ev_authority import ExecutableEconomics, evaluate_all_in_ev
from completion_logic.readiness_gate import ReadinessEvidence, evaluate_readiness

def test_market_prior_wins_when_bettor_loses_oos():
    d=choose_probability(ForecastEvidence(
        market_prior=.55,bettor_raw=.62,bettor_calibrated=.58,
        independent_events=200,calibration_error=.02,
        oos_logloss_delta_vs_market=.05,oos_logloss_delta_ci_low=.02,
        evidence_complete=True
    ))
    assert d.authority=="MARKET_PRIOR_ONLY"
    assert d.p_used==.55

def test_residual_allowed_only_with_positive_lower_bound():
    d=choose_probability(ForecastEvidence(
        market_prior=.55,bettor_raw=.62,bettor_calibrated=.58,
        independent_events=200,calibration_error=.02,
        oos_logloss_delta_vs_market=-.03,oos_logloss_delta_ci_low=-.01,
        residual_alpha=.5,residual_signal=.3,residual_improvement_lb=.01,
        evidence_complete=True
    ))
    assert d.authority=="BETTOR_RESIDUAL_ALLOWED"
    assert d.p_used > .55

def test_ev_cash_when_lower_bound_negative():
    d=evaluate_all_in_ev(ExecutableEconomics(
        p_used=.55, executable_price=.53, fee_per_contract=.01,
        adverse_selection_per_contract=.01, probability_uncertainty_per_contract=.01
    ))
    assert d.verdict=="CASH"

def test_ev_eligible_only_after_all_costs_and_haircuts():
    d=evaluate_all_in_ev(ExecutableEconomics(
        p_used=.60, executable_price=.50, fee_per_contract=.01,
        spread_slippage_per_contract=.005, adverse_selection_per_contract=.005,
        probability_uncertainty_per_contract=.01, execution_uncertainty_per_contract=.005
    ))
    assert d.verdict=="ELIGIBLE_FOR_EXISTING_GATED_PATH"

def test_readiness_gate_blocks_capital_when_profitability_unproven():
    d=evaluate_readiness(ReadinessEvidence(
        no_oom_minutes=90, worker_rss_highwater_fraction=.5,
        priority_freshness_rate=.99, management_freshness_rate=.99,
        software_red_count=0, xavier_packet_complete_rate=.99, canary_pass=True,
        probability_edge_lb=None, forward_independent_events=200,
        digital_twin_fill_agreement=.98, settlement_proven=True,
        capacity_proven=True, mirror_venue_confirmed=True,
        small_live_shadow=True, historical_paper_immutable=True
    ))
    assert d.status=="PAPER_SHADOW_ONLY"
    assert "POSITIVE_PROBABILITY_EDGE_NOT_PROVEN" in d.blockers

def test_readiness_gate_can_be_capital_candidate_when_all_green():
    d=evaluate_readiness(ReadinessEvidence(
        no_oom_minutes=90, worker_rss_highwater_fraction=.5,
        priority_freshness_rate=.99, management_freshness_rate=.99,
        software_red_count=0, xavier_packet_complete_rate=.99, canary_pass=True,
        probability_edge_lb=.01, forward_independent_events=200,
        digital_twin_fill_agreement=.98, settlement_proven=True,
        capacity_proven=True, mirror_venue_confirmed=True,
        small_live_shadow=True, historical_paper_immutable=True
    ))
    assert d.status=="CAPITAL_CANDIDATE"
    assert d.blockers==()
