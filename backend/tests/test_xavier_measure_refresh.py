import asyncio
import copy
import math
import pytest
from sportsassets import xavier_measure_refresh as X

def base():
    return dict(pos={'us_market_slug':'m1','group_id':'g1','holding_side':'LONG'},
                contract={'id':7,'us_market_slug':'m1','payout_event':'TEAM_HOME','payout_is_complement':False,
                          'event_key':'pinnapi:123','market':'spread','line':-3.5},
                stored={'probability':.4,'observed_at':900,'received_at':950,'id':5},
                model={'ok':True,'model_id':'original-model'},levels_buy=[{'price':.5}],
                at=1000,max_age_s=30,score=lambda m,**kw:{'ok':True,'p':.6},
                blend=lambda a,b:(a+b)/2)

def run(*,response=None,feed=None,**changes):
    kw=base();kw.update(changes)
    async def reader(conn,**params):
        return response or {'ok':True,'p':.8,'provenance':{'change_ms':990000,'received_ms':990001}}
    return asyncio.run(X.refresh(None,**kw,held_feed=feed or reader))

def test_derek_two_model_gets_current_held_probability_and_preserves_blend():
    got=run()
    assert got['ok'] and got['p']==pytest.approx(.7)
    assert got['p_internal']==.6 and got['p_pinnacle']==.8
    assert got['source']=='CURRENT_BLEND_HELD_CACHE'
    assert got['valuation_id'] is None # existing caller MUST persist snapshot before gate
    assert got['entry_anchor_valuation_id']==7

def test_exact_entry_event_line_and_payout_reach_held_reader():
    seen={}
    async def reader(conn,**kw):
        seen.update(kw);return {'ok':True,'p':.8,'provenance':{'change_ms':990000}}
    run(feed=reader)
    assert seen['pos']['entry_event_key']=='pinnapi:123'
    assert seen['pos']['entry_line']==-3.5
    assert seen['payout_event']=='TEAM_HOME'
    assert seen['payout_is_complement'] is False
    assert seen['max_age_s']==30

def test_fresh_stored_value_avoids_reacquiring_same_market():
    async def no_read(*a,**k):raise AssertionError('should not read')
    got=run(stored={'id':5,'probability':.4,'observed_at':995},feed=no_read)
    assert got['source']=='CURRENT_BLEND' and got['p']==.5 and got['valuation_id']==5

@pytest.mark.parametrize('source_ms',[None,969999.6,1000000.4,float('nan'),float('inf')])
def test_bad_or_stale_source_clock_cannot_be_refreshed_by_receipt(source_ms):
    got=run(response={'ok':True,'p':.8,'provenance':{'change_ms':source_ms,'received_ms':1000000,'quote_age_s':1}})
    assert not got['ok']

def test_fresh_at_start_expired_after_callback_is_refused():
    t=[1000.0]
    async def slow(conn,**kw):
        t[0]=1031
        return {'ok':True,'p':.8,'provenance':{'change_ms':1000000,'quote_age_s':0}}
    assert not run(feed=slow,clock=lambda:t[0])['ok']

def test_provider_refusal_is_carried_not_relabelled_external():
    got=run(response={'ok':False,'reason':'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'})
    assert got['feed_refusal']=='FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'
    assert got['p'] is None and got['stale']

@pytest.mark.parametrize('mutation',[{'us_market_slug':'other'},{'payout_event':None},{'payout_is_complement':'false'}])
def test_unproven_entry_contract_is_not_repriced(mutation):
    c=base()['contract'];c.update(mutation)
    assert not run(contract=c)['ok']

def test_zero_probability_is_valid_not_absent():
    got=run(response={'ok':True,'p':0,'provenance':{'change_ms':999000}})
    assert got['ok'] and got['p']==.3

def test_non_mapping_feed_result_is_named_not_an_exception():
    async def reader(conn,**kw):return ['bad']
    assert run(feed=reader)['feed_refusal']=='INVALID_HELD_READ'

@pytest.mark.parametrize('p',[None,True,-.1,1.1,float('nan'),float('inf')])
def test_invalid_probability_is_unavailable(p):
    e=X.evidence({'p':p,'pinnacle_at':999},at=1000,limit_s=30,qty=5)
    assert e['evidence_state']=='PROBABILITY_UNAVAILABLE'
    assert e['current_hold_value_usd'] is None

@pytest.mark.parametrize('source,expect',[(970,True),(969.9996,False),(1000.0004,False),(1000,True),(None,False)])
def test_exact_boundary_not_rounded_for_freshness(source,expect):
    e=X.evidence({'p':.6,'pinnacle_at':source,'pinnacle_age_s':0},at=1000,limit_s=30,qty=10)
    assert (e['evidence_state']=='FRESH_CURRENT_PROBABILITY') is expect

def test_cached_age_does_not_override_real_source_time():
    e=X.evidence({'p':.7,'pinnacle_at':900,'pinnacle_age_s':1,'pinnacle_limit_s':999},at=1000,limit_s=30)
    assert e['probability_age_s']==100 and e['probability_limit_s']==30
    assert e['evidence_state']=='STALE_ENTRY_TIME_PROBABILITY'

def test_untimestamped_fresh_claim_remains_stale():
    e=X.evidence({'p':.6,'stale':False,'pinnacle_age_s':1},at=1000,limit_s=30)
    assert e['evidence_state']=='STALE_ENTRY_TIME_PROBABILITY'

def test_known_stale_measure_cannot_pass_on_recent_timestamp():
    assert X.evidence({'p':.6,'stale':True,'pinnacle_at':999},at=1000,limit_s=30)['evidence_state']!='FRESH_CURRENT_PROBABILITY'
