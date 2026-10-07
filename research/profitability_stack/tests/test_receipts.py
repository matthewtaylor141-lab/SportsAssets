import json
from bettor_profit_stack.common import Receipt
from bettor_profit_stack.execution_truth import ExecutionCosts,taker_value,choose_execution
from bettor_profit_stack.receipts import execution_receipt

def test_receipt_hash_is_deterministic():
    r=Receipt("X","v1",{"b":2,"a":1})
    assert r.as_dict()["sha256"]==r.as_dict()["sha256"]

def test_execution_receipt_includes_selected_and_candidates():
    c=taker_value(.6,.5,ExecutionCosts(fees=.01))
    r=execution_receipt("c1",[c],c).as_dict()
    assert r["payload"]["candidate_id"]=="c1"
    assert r["payload"]["selected"]["action"]=="TAKE"
    assert len(r["sha256"])==64
