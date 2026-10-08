from bettor_profit_stack.profitability_governor import (
    AnytimeBoundedMean,capital_graduation,strategy_capital_weights
)

def test_confidence_sequence_starts_unmeasured():
    cs=AnytimeBoundedMean(-1,1)
    assert cs.state().status=="UNMEASURED"

def test_positive_evidence_eventually_has_positive_lower_bound():
    cs=AnytimeBoundedMean(-1,1,alpha=.05)
    s=None
    for _ in range(5000):
        s=cs.update(.20)
    assert s.lower_bound>0
    assert s.status=="POSITIVE_LOWER_BOUND"

def test_negative_evidence_eventually_has_negative_upper_bound():
    cs=AnytimeBoundedMean(-1,1,alpha=.05)
    s=None
    for _ in range(5000):
        s=cs.update(-.20)
    assert s.upper_bound<0

def test_out_of_range_rejected():
    cs=AnytimeBoundedMean(-1,1)
    try:
        cs.update(2)
        assert False
    except ValueError:
        pass

def test_governor_requires_minimum_independent_events():
    cs=AnytimeBoundedMean(-1,1)
    for _ in range(20):cs.update(.5)
    d=capital_graduation(cs.state(),calibration_ok=True,execution_ok=True,settlement_ok=True,minimum_events=100,capacity_usd=1000)
    assert d.capital_status=="SHADOW_ONLY"

def test_governor_cash_when_positive_lower_bound_not_proven():
    cs=AnytimeBoundedMean(-1,1)
    for _ in range(200):cs.update(.01)
    d=capital_graduation(cs.state(),calibration_ok=True,execution_ok=True,settlement_ok=True,minimum_events=100,capacity_usd=1000)
    assert d.capital_status=="CASH"

def test_governor_requires_execution():
    cs=AnytimeBoundedMean(-1,1)
    for _ in range(5000):cs.update(.2)
    d=capital_graduation(cs.state(),calibration_ok=True,execution_ok=False,settlement_ok=True,minimum_events=100,capacity_usd=1000)
    assert d.capital_status=="SHADOW_ONLY"

def test_capital_weights_leave_cash_when_none_positive():
    a=AnytimeBoundedMean(-1,1);b=AnytimeBoundedMean(-1,1)
    for _ in range(100):a.update(-.1);b.update(0)
    w=strategy_capital_weights({"a":a.state(),"b":b.state()},{"a":1000,"b":1000},10000)
    assert w=={"CASH":10000.0}

def test_capital_weights_respect_capacity_and_fraction_cap():
    a=AnytimeBoundedMean(-1,1);b=AnytimeBoundedMean(-1,1)
    for _ in range(5000):a.update(.3);b.update(.2)
    w=strategy_capital_weights({"a":a.state(),"b":b.state()},{"a":1000,"b":5000},10000,max_strategy_fraction=.25)
    assert w["a"]<=1000
    assert w["b"]<=2500
    assert w["CASH"]>=6500
