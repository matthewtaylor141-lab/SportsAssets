from bettor_profit_stack.pnl_attribution import AttributionInput,attribute,aggregate,require_reconciled

def sample(**kw):
    base=dict(
      decision_id="d1",strategy="s1",sport="NFL",family="MONEYLINE",regime="NORMAL",venue="PMUS",qty=10,
      market_prior_p=.50,model_p=.56,calibrated_p=.55,outcome=1,
      expected_fill_price=.50,actual_fill_price=.51,
      expected_execution_cost=.01,actual_execution_cost=.015,
      expected_management_value=0,realized_management_value=.005,
      expected_settlement_adjustment=0,realized_settlement_adjustment=0
    )
    base.update(kw);return AttributionInput(**base)

def test_attribution_reconciles():
    a=attribute(sample())
    assert abs(a.reconciliation_error)<1e-9
    assert require_reconciled(a)

def test_execution_residual_penalizes_worse_fill_and_cost():
    a=attribute(sample())
    assert a.execution_residual<0

def test_management_residual_positive_when_management_adds_value():
    a=attribute(sample())
    assert a.management_residual>0

def test_outcome_variance_is_realized_minus_calibrated():
    a=attribute(sample(outcome=0))
    assert a.outcome_variance<0

def test_forecast_residual_tracks_calibrated_vs_market_prior():
    a=attribute(sample())
    assert abs(a.forecast_residual-(.55-.50)*10)<1e-12

def test_aggregate_by_strategy():
    a1=attribute(sample(decision_id="a"))
    a2=attribute(sample(decision_id="b"))
    rows=aggregate([a1,a2],"strategy")
    assert rows[0]["n"]==2
    assert abs(rows[0]["reconciliation_error"])<1e-9
