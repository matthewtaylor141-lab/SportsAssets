import numpy as np

from bettor_revenue_reliability.current_contract import (
    validate_contract, AGENTS, LIFECYCLE_STATES, NO_ENTRY_STATES
)
from bettor_revenue_reliability.certification import AgentEvidence, certify_agent, certification_does_not_expand_authority
from bettor_revenue_reliability.tournament import CandidateMetrics, run_tournament
from bettor_revenue_reliability.regime import RegimeEvidence, decide_regime
from bettor_revenue_reliability.reliability import StrategyEvidence, optimize_reliable_portfolio
from bettor_revenue_reliability.counterfactuals import (
    xavier_management_alpha, archer_execution_alpha, allie_allocation_alpha,
    derek_entry_alpha, karen_challenge_value, audrey_reconciliation_score
)
from bettor_revenue_reliability.daily_contract import StrategyReadiness, build_daily_brief
from bettor_revenue_reliability.integration import run_shadow_cycle

def test_current_contract_is_consistent():
    validate_contract()
    assert "ACTIVE_CHAMPION" in LIFECYCLE_STATES
    assert NO_ENTRY_STATES == {"SHADOW_ONLY","QUARANTINED","RETIRED"}

def test_authority_mirrors_current_agent_contracts():
    assert AGENTS["DEREK"].current_authority == "ENTRY_REQUEST_THROUGH_GATED_PATH"
    assert AGENTS["KAREN"].current_authority == "CHALLENGE_ONLY_ZERO_AUTHORITY"
    assert AGENTS["ARCHER"].current_authority == "SHADOW_ONLY"
    assert AGENTS["CHIEF_ALLOCATOR"].display_name == "Allie"
    assert AGENTS["CHIEF_ALLOCATOR"].current_authority == "SHADOW_WEIGHTS_ONLY"
    assert AGENTS["AUDREY"].current_authority == "AUDIT_NO_ORDER_PATH"
    assert AGENTS["ADRIANA"].current_authority == "SHADOW_ONLY"

def test_no_agent_contract_grants_capital():
    assert all(not a.can_grant_capital for a in AGENTS.values())

def test_agent_certification_licenses_only_positive_proven_value():
    e=AgentEvidence("XAVIER",150,1000,1200,2.0,evidence_complete=True)
    c=certify_agent(e)
    assert c.license=="LICENSED"

def test_agent_certification_probation_when_lower_bound_not_positive():
    e=AgentEvidence("ARCHER",200,1000,900,-0.01,evidence_complete=True)
    c=certify_agent(e)
    assert c.license=="PROBATION"

def test_agent_certification_shadow_when_not_enough_events():
    e=AgentEvidence("DEREK",20,100,100,1.0,evidence_complete=True)
    c=certify_agent(e)
    assert c.license=="SHADOW_ONLY"

def test_agent_certification_can_suspend_bad_reconciliation():
    e=AgentEvidence("AUDREY",150,0,0,0.1,reconciliation_error_usd=10.0)
    c=certify_agent(e)
    assert c.license=="SUSPENDED"

def test_certification_never_expands_authority():
    e=AgentEvidence("ARCHER",150,100,120,0.2)
    c=certify_agent(e)
    r=certification_does_not_expand_authority(c,"SHADOW_ONLY")
    assert r["authority_changed"] is False
    assert r["capital_authority_granted"] is False

def test_tournament_keeps_cash_if_every_candidate_negative():
    cs=[
      CandidateMetrics("A",200,-.1,-.2,capital_hour_profit=-.1),
      CandidateMetrics("B",200,.1,-.01,capital_hour_profit=.2)
    ]
    r=run_tournament(cs)
    assert r.selected=="CASH"
    assert r.status=="NO_PROMOTION"

def test_tournament_requires_independent_events():
    cs=[CandidateMetrics("A",20,1.0,.8,capital_hour_profit=.8)]
    r=run_tournament(cs,minimum_events=100)
    assert r.selected=="CASH"

def test_tournament_selects_only_absolute_positive_candidate():
    cs=[
      CandidateMetrics("A",200,.5,.2,capital_hour_profit=.3,capacity_usd=1000),
      CandidateMetrics("B",200,.4,.1,capital_hour_profit=.2,capacity_usd=1000),
    ]
    r=run_tournament(cs)
    assert r.selected=="A"

def test_regime_abstains_when_freshness_bad():
    e=RegimeEvidence("NFL","MONEYLINE","PREGAME",200,.02,.03,False,True)
    assert decide_regime(e).authority=="ABSTAIN"

def test_regime_stays_shadow_if_underpowered():
    e=RegimeEvidence("NFL","MONEYLINE","PREGAME",20,.02,.03,True,True)
    assert decide_regime(e).authority=="SHADOW_ONLY"

def test_regime_cash_if_calibrated_but_ev_negative():
    e=RegimeEvidence("NFL","MONEYLINE","PREGAME",200,.02,-.01,True,True)
    assert decide_regime(e).authority=="CASH"

def test_regime_eligible_only_when_all_evidence_positive():
    e=RegimeEvidence("NFL","MONEYLINE","PREGAME",200,.02,.01,True,True)
    assert decide_regime(e).authority=="ELIGIBLE_FOR_EXISTING_GATED_PATH"

def test_reliability_optimizer_cash_when_nothing_eligible():
    ss=[
      StrategyEvidence("s1",200,-100,-10,50,1000,-1,10000,"ACTIVE_CHALLENGER"),
      StrategyEvidence("s2",200,100,10,50,1000,1,0,"ACTIVE_CHALLENGER"),
    ]
    p=optimize_reliable_portfolio(ss,bankroll=500000)
    assert p.allocations=={"CASH":500000.0}

def test_reliability_optimizer_respects_lifecycle():
    ss=[StrategyEvidence("s1",200,100,20,20,100,2,10000,"SHADOW_ONLY")]
    p=optimize_reliable_portfolio(ss,bankroll=500000)
    assert p.allocations["CASH"]==500000

def test_reliability_optimizer_respects_capacity_and_fraction():
    ss=[
      StrategyEvidence("s1",200,100,30,20,100,2,10000,"ACTIVE_CHALLENGER"),
      StrategyEvidence("s2",200,80,20,15,80,1.5,100000,"ACTIVE_CHALLENGER"),
    ]
    p=optimize_reliable_portfolio(ss,bankroll=100000,max_strategy_fraction=.25)
    assert p.allocations["s1"]<=10000
    assert p.allocations["s2"]<=25000
    assert p.cash>=65000

def test_reliability_optimizer_correlation_haircut_keeps_cash():
    ss=[
      StrategyEvidence("s1",200,100,30,20,100,2,50000,"ACTIVE_CHALLENGER"),
      StrategyEvidence("s2",200,100,30,20,100,2,50000,"ACTIVE_CHALLENGER"),
    ]
    corr=np.array([[1,.99],[.99,1]])
    p=optimize_reliable_portfolio(ss,bankroll=100000,correlation=corr)
    assert p.cash>=50000  # caps plus high-correlation haircut should not force deployment

def test_xavier_counterfactual_requires_beating_hold_and_exit():
    r=xavier_management_alpha(120,100,110)
    assert r["positive_against_both"]
    r2=xavier_management_alpha(105,100,110)
    assert not r2["positive_against_both"]

def test_archer_execution_alpha():
    r=archer_execution_alpha(10,8)
    assert r.incremental_value_usd==2
    assert r.status=="POSITIVE_VALUE_ADD"

def test_allie_allocation_alpha():
    r=allie_allocation_alpha(120,100)
    assert r.incremental_value_usd==20

def test_derek_entry_alpha():
    r=derek_entry_alpha(50,60)
    assert r.status=="NO_POSITIVE_VALUE_ADD"

def test_karen_challenge_value_accounts_for_false_blocks():
    r=karen_challenge_value(100,30)
    assert r.incremental_value_usd==130

def test_audrey_reconciliation():
    assert audrey_reconciliation_score(1e-8)["reconciled"]
    assert not audrey_reconciliation_score(.01)["reconciled"]

def test_daily_brief_cash_without_allocations():
    s=[StrategyReadiness("s1","SHADOW_ONLY",200,-1,0,-.1,"LIFECYCLE_SHADOW_ONLY")]
    b=build_daily_brief(500000,s,{"CASH":500000},0)
    assert b.overall_status=="CASH"

def test_daily_brief_reports_proven_capacity_not_turnover_target():
    s=[StrategyReadiness("s1","ACTIVE_CHALLENGER",200,10,20000,1,None)]
    b=build_daily_brief(500000,s,{"s1":20000,"CASH":480000},100)
    assert b.proven_positive_capacity_usd==20000
    assert b.planned_capital_usd==20000
    assert b.cash_usd==480000

def test_full_shadow_cycle_never_changes_authority():
    agents=[AgentEvidence("DEREK",150,100,120,.2)]
    candidates=[CandidateMetrics("s1",150,10,2,capital_hour_profit=1,capacity_usd=10000)]
    regimes=[RegimeEvidence("NFL","MONEYLINE","PREGAME",150,.02,.01,True,True)]
    strategies=[StrategyEvidence("s1",150,100,20,20,100,2,10000,"ACTIVE_CHALLENGER")]
    r=run_shadow_cycle(
        agent_evidence=agents,candidates=candidates,regime_evidence=regimes,
        strategies=strategies,bankroll_usd=500000
    )
    assert r["mode"]=="RESEARCH_PAPER_SHADOW_ONLY"
    assert r["authority_changed"] is False
    assert r["portfolio_plan"]["allocations"]["CASH"]>=490000
