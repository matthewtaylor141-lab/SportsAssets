from bettor_profit_stack.structural_arb import (
    Contract, complement_pair, exhaustive_basket, solve_superhedge,
    settlement_compatible, implication_violation
)

def test_complement_pair_locks_profit():
    r=complement_pair(.45,.43,.005,.005)
    assert r.executable and r.guaranteed_profit>0

def test_complement_pair_rejects_after_costs():
    r=complement_pair(.50,.49,.01,.01)
    assert not r.executable

def test_exhaustive_basket():
    r=exhaustive_basket([.20,.25,.30],fees=.01)
    assert r.executable
    assert abs(r.guaranteed_payout-1)<1e-12

def test_superhedge_finds_cheapest_cover():
    # Two states. YES pays first, NO pays second.
    cs=[
      Contract("yes","PMUS",.45,(1,0),1,.005,0,"event1"),
      Contract("no","KALSHI",.43,(0,1),1,.005,0,"event1"),
    ]
    r=solve_superhedge(cs)
    assert r.executable
    assert set(r.quantities)=={"yes","no"}

def test_superhedge_respects_depth_cap():
    cs=[
      Contract("yes","PMUS",.30,(1,0),.5,0,0,"x"),
      Contract("no","KALSHI",.30,(0,1),.5,0,0,"x"),
    ]
    r=solve_superhedge(cs)
    assert not r.executable
    assert r.reason=="NO_FEASIBLE_SUPERHEDGE"

def test_settlement_compatibility_explicit():
    a=Contract("a","A",.4,(1,0),settlement_key="same")
    b=Contract("b","B",.4,(0,1),settlement_key="same")
    c=Contract("c","B",.4,(0,1),settlement_key="different")
    assert settlement_compatible(a,b)
    assert not settlement_compatible(a,c)

def test_implication_violation_after_costs():
    r=implication_violation(.60,.55,.01)
    assert r["candidate"]
    r2=implication_violation(.56,.55,.02)
    assert not r2["candidate"]
