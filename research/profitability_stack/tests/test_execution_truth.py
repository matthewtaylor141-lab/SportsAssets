import math
from bettor_profit_stack.execution_truth import (
    ExecutionCosts, taker_value, maker_value, wait_value, choose_execution,
    adverse_selection_after_fill, estimate_fill_probability, edge_decay_slope
)

def test_taker_rejects_edge_consumed_by_costs():
    r=taker_value(.55,.54,ExecutionCosts(fees=.006,slippage=.005))
    assert r.expected_profit_per_posted_contract<0
    assert not r.admissible

def test_taker_accepts_positive_net():
    r=taker_value(.58,.54,ExecutionCosts(fees=.005,slippage=.002))
    assert r.admissible

def test_maker_conditions_on_fill_probability():
    r=maker_value(.58,.54,.25,ExecutionCosts(fees=.002,adverse_selection=.005))
    assert abs(r.expected_profit_per_posted_contract-r.fill_probability*r.profit_if_filled)<1e-12

def test_maker_lower_bound_can_fail_even_when_mean_positive():
    r=maker_value(.56,.53,.20,ExecutionCosts(fees=.001),fill_probability_se=.12,edge_se=.02)
    assert r.expected_profit_per_posted_contract>0
    assert r.lower_bound_profit<=0
    assert not r.admissible

def test_choose_execution_picks_highest_positive_lower_bound():
    a=taker_value(.58,.55,ExecutionCosts(fees=.005))
    b=maker_value(.58,.54,.8,ExecutionCosts(fees=.002),fill_probability_se=.01,edge_se=.001)
    c=choose_execution(a,b)
    assert c.action=="MAKE"

def test_choose_execution_refuses_when_all_nonpositive():
    a=taker_value(.51,.52,ExecutionCosts())
    b=wait_value(.51,.52,.01)
    c=choose_execution(a,b)
    assert c.action=="REFUSE"

def test_adverse_selection_direction():
    assert adverse_selection_after_fill(.50,.48,"BUY")>0
    assert adverse_selection_after_fill(.50,.52,"SELL")>0

def test_fill_probability_posterior_bounded():
    mean,se=estimate_fill_probability([1,0,1,1,0])
    assert 0<mean<1 and se>0

def test_edge_decay_negative_slope():
    s=edge_decay_slope([0,1,2,3],[.03,.02,.01,0])
    assert s<0
