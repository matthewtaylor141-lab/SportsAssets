from bettor_profit_stack.execution_truth import ExecutionCosts,taker_value,maker_value,choose_execution
from bettor_profit_stack.structural_arb import complement_pair
from bettor_profit_stack.digital_twin import DigitalTwin,TwinConfig,MarketEvent,EventType,OrderIntent
from bettor_profit_stack.profitability_governor import AnytimeBoundedMean,capital_graduation
from bettor_profit_stack.pnl_attribution import AttributionInput,attribute

print("=== EXECUTION TRUTH ===")
take=taker_value(.58,.55,ExecutionCosts(fees=.005,slippage=.002))
make=maker_value(.58,.54,.65,ExecutionCosts(fees=.002,adverse_selection=.004),fill_probability_se=.02,edge_se=.002)
print("selected:",choose_execution(take,make).action)

print("\n=== STRUCTURAL ARB ===")
print(complement_pair(.45,.43,.005,.005))

print("\n=== DIGITAL TWIN ===")
events=[
 MarketEvent(1,1,EventType.BOOK,"m1",{"bid":.40,"bid_qty":0,"ask":.42,"ask_qty":10}),
 MarketEvent(2,2,EventType.SETTLEMENT,"m1",{"payout":1.0}),
]
def strat(view,event):
    if event.ts==1:return [OrderIntent("PLACE","m1","BUY",.45,1,"demo-order")]
    return []
twin=DigitalTwin(TwinConfig(submit_latency_s=0))
receipt=twin.replay(events,strat)
print("fills:",receipt.fills,"cash:",twin.cash)

print("\n=== PROFITABILITY GOVERNOR ===")
cs=AnytimeBoundedMean(-1,1)
for _ in range(5000):cs.update(.2)
print(capital_graduation(cs.state(),calibration_ok=True,execution_ok=True,settlement_ok=True,capacity_usd=10000))

print("\n=== P&L ATTRIBUTION ===")
a=attribute(AttributionInput(
    decision_id="demo",strategy="s",sport="NFL",family="MONEYLINE",regime="NORMAL",venue="PMUS",qty=10,
    market_prior_p=.50,model_p=.56,calibrated_p=.55,outcome=1,
    expected_fill_price=.50,actual_fill_price=.51,expected_execution_cost=.01,actual_execution_cost=.015,
    expected_management_value=0,realized_management_value=.005,
    expected_settlement_adjustment=0,realized_settlement_adjustment=0
))
print(a)
print("\nSynthetic demo only — not a profitability claim.")
