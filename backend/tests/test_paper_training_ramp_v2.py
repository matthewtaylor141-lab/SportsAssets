"""Larger PAPER training entries and broader inclusion, same account bounds."""
import hashlib
from unittest.mock import AsyncMock
import pytest
from sportsassets.agents import paper_explore as E, paper_benchmark as B


def test_versioned_ramp_preserves_investment_requirements_and_total_bounds():
    assert E.VERSION=='PINNACLE_EXPLORATION_PAPER_V2'
    assert E.MAX_ENTRY_COST_USD==1000
    assert E.MAX_AGGREGATE_EXPOSURE_USD==5000 and E.LOSS_STOP_USD==1000
    assert B.CG_PARAMETERS_V2=={'min_gross_edge_pp':.5}
    assert B.EXPLORE_POLICY['strategy']=='PINNACLE_EXPLORATION_PAPER'
    assert 'not investment performance' in B.EXPLORE_DISCLOSURE
    from sportsassets import bettor_paper_ops as OPS
    meta=OPS._policy_meta()[E.STRATEGY]
    assert meta['version']==E.VERSION and '$1,000 per position' in meta['summary']


@pytest.mark.parametrize('price',[.05,.27,.50,.71,.95])
def test_larger_order_is_within_budget_after_fees_and_current_depth(price):
    levels=[{'price':price,'wire':price,'qty':100000}]
    old=E.size_entry(levels,consumed={},fee_fn=None,at=1800000000,budget_usd=100)
    new=E.size_entry(levels,consumed={},fee_fn=None,at=1800000000,budget_usd=1000)
    assert new['qty']>old['qty'] and 990<new['reservation_usd']<=1000
    shallow=E.size_entry([{'price':price,'wire':price,'qty':7}],consumed={},fee_fn=None,at=1800000000,budget_usd=1000)
    assert shallow['qty']==7


@pytest.mark.parametrize('n',[1,6,7,12,20,60,500])
def test_inclusion_only_expands_and_keeps_original_fixture_draw(n):
    old_p=round(min(1,max(.35,6/max(1,n))),6)
    new_p=E.inclusion_probability(n);assert old_p<=new_p<=1
    old_set=set();new_set=set()
    for i in range(200):
        fixture='fixture:'+str(i)
        h=hashlib.sha256(('PINNACLE_EXPLORATION_PAPER_V1:'+fixture).encode()).hexdigest()
        old_u=int(h[:15],16)/float(16**15)
        assert E.draw(fixture)==old_u
        if old_u<old_p:old_set.add(fixture)
        if E.draw(fixture)<new_p:new_set.add(fixture)
    assert old_set<=new_set
    if n>=12:assert len(new_set)>len(old_set)


def test_version_change_does_not_duplicate_existing_valuation_decision_keys():
    v1={**B.EXPLORE_POLICY,'version':'PINNACLE_EXPLORATION_PAPER_V1'}
    assert B.decision_id_for('session',123,v1)==B.decision_id_for('session',123,B.EXPLORE_POLICY)


@pytest.mark.asyncio
@pytest.mark.parametrize('reserve,exposure,loss,held,reason',[
    (1001,0,False,False,E.R_TOO_DEAR),
    (1000,4100,False,False,E.R_AGGREGATE),
    (1000,0,True,False,E.R_LOSS_STOP),
    (1000,0,False,True,E.R_FIXTURE_TAKEN),
    (1000,4000,False,False,None)])
async def test_locked_checks_still_enforce_all_bounds(monkeypatch,reserve,exposure,loss,held,reason):
    monkeypatch.setattr(E,'limits_state',AsyncMock(return_value={'loss_stop_reached':loss,'exposure_usd':exposure}))
    monkeypatch.setattr(E,'fixture_taken',AsyncMock(return_value=held))
    got=await E.locked_check_for('f','m')(None,{'account_id':'paper_test'},reserve)
    assert (got or {}).get('refusal')==reason
