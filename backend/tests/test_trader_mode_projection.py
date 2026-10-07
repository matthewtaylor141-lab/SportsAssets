import copy
import pytest
from sportsassets import trader_mode as T

def position():
    pid='paperpos:a:g:m:LONG'
    return {'position_id':pid,'event_id':'event1','qty':10,'holding_side':'LONG','cost_basis_usd':5,
            'quote':{'current':True,'bid':.6,'ask':.61,'at':999,'depth_at_bid':20,'market_state':'OPEN'},
            'review':{'review_id':'r1','reviewed_at':999,'recommendation':'HOLD',
                      'measure':{'p':.7,'pinnacle_at':999,'evidence_state':'FRESH_CURRENT_PROBABILITY'},
                      'selection':{'management_packet':{'gate':{'complete':True,'missing':[]},
                                                       'protection':{'order_id':'o1'}}}},
            'orders':[{'position_id':pid,'order_id':'o1','state':'RESTING','role':'STANDING_PROTECTION',
                       'direction':'SELL','qty':10,'remaining_qty':10,'limit_price':.7,'expires_at':1100}]}

def snapshot(p=None,**kw):return T.build_snapshot([p or position()],now=1000,**kw)

def test_complete_packet_current_mark_and_one_exact_position():
    s=snapshot();p=s['positions'][0]
    assert p['packet']['complete'] and p['unrealized_usd']==1
    assert s['counts']['packet_rate']==1 and s['counts']['mark_rate']==1
    assert s['execution_authority'] is False and s['mode']=='PAPER'

def test_stale_probability_blocks_packet_but_not_current_mark():
    p=position();p['review']['measure']['pinnacle_at']=900
    s=snapshot(p)
    assert not s['positions'][0]['packet']['complete']
    assert s['positions'][0]['quote']['current']
    assert s['positions'][0]['unrealized_usd']==1
    assert s['counts']['active']==1 and s['counts']['incomplete_packets']==1

def test_expired_book_blocks_current_pnl_not_previous_quote_inspection():
    p=position();p['quote']['at']=699
    q=snapshot(p)['positions'][0]
    assert q['quote']['bid']==.6 and not q['quote']['current'] and q['unrealized_usd'] is None

def test_no_timestamp_reset_when_rendering_again():
    p=position();s=T.build_snapshot([p],now=1031)
    assert s['positions'][0]['packet']['probability_at']==999
    assert not s['positions'][0]['packet']['complete']

@pytest.mark.parametrize('depth',[None,0,-1])
def test_exit_depth_disappearing_invalidates_old_complete_packet(depth):
    p=position();p['quote']['depth_at_bid']=depth
    assert 'NO_EXECUTABLE_EXIT_DEPTH' in snapshot(p)['positions'][0]['packet']['missing']

@pytest.mark.parametrize('state',['CANCEL_PENDING','PENDING_SIMULATION','CANCELED','EXPIRED','FILLED'])
def test_old_protection_review_not_current_after_order_state_changes(state):
    p=position();p['orders'][0]['state']=state
    assert not snapshot(p)['positions'][0]['packet']['complete']

def test_protection_expiry_and_wrong_quantity_refuse():
    p=position();p['orders'][0]['expires_at']=1000
    assert not snapshot(p)['positions'][0]['packet']['complete']
    p=position();p['orders'][0]['remaining_qty']=9
    assert not snapshot(p)['positions'][0]['packet']['complete']

def test_multiple_potentially_live_protections_never_complete():
    p=position();p['orders'].append(dict(p['orders'][0],order_id='o2'))
    assert not snapshot(p)['positions'][0]['packet']['complete']

def test_group_siblings_do_not_share_orders():
    p=position();p['orders'][0]['position_id']='paperpos:a:g:other:SHORT'
    with pytest.raises(ValueError):snapshot(p)

def test_duplicate_canonical_positions_refuse():
    with pytest.raises(ValueError):T.build_snapshot([position(),position()],now=1000)

def test_last_review_is_position_keyed_not_group_or_nested_count():
    rs=[{'position_id':'x','reviewed_at':998,'review_id':'1'},
        {'position_id':'other','reviewed_at':999,'review_id':'2'},
        {'position_id':'x','reviewed_at':1001,'review_id':'future'}]
    assert T.review_for_position(rs,'x',now=1000)['review_id']=='1'

def test_settlement_pending_requires_venue_terminal_evidence_not_scheduled_time():
    p=position();p['event_start']=1
    assert snapshot(p)['counts']['active']==1
    p['quote']['market_state']='EXPIRED'
    s=snapshot(p)
    assert s['counts']['active']==0 and s['counts']['all_open']==1
    assert s['counts']['settlement_pending']==1 and s['counts']['packet_rate'] is None

def test_truncation_never_hidden_or_called_all_position_success():
    s=snapshot(total_count=1001)
    assert s['truncated'] and s['returned_position_count']==1 and s['total_position_count']==1001
    assert 'RETURNED' in s['counts']['rates_scope']

@pytest.mark.parametrize('direction,bid,ask,limit,met',[('SELL',.6,.61,.6,True),('SELL',.6,.8,.7,False),('BUY',.6,.61,.6,False),('BUY',.6,.61,.61,True)])
def test_price_touch_uses_correct_book_side_and_never_means_fill(direction,bid,ask,limit,met):
    r=T.price_gap(direction,limit,{'bid':bid,'ask':ask,'current':True,'at':999},now=1000)
    assert r['condition_met']==met and r['is_fill'] is False

def test_stale_touch_is_unknown():
    r=T.price_gap('SELL',.5,{'bid':.6,'current':True,'at':1},now=1000)
    assert r['condition_met'] is None

def game():
    return dict(event_id='event1',identity_verified=True,source='LICENSED_TEST',source_at=999,
                home='Home',away='Away',home_score=0,away_score=1,clock_seconds=60,
                clock_running=True,clock_direction='DOWN',bases=[True,False,True],outs=2,balls=3,strikes=2)

def test_game_state_carries_real_zero_and_full_baseball_fields():
    g=T.normalize_game(game(),expected_event_id='event1',now=1000)
    assert g['home_score']==0 and g['bases']==[True,False,True] and g['outs']==2
    assert g['clock_running']

def test_no_game_source_is_unavailable_not_zero_scores():
    g=T.normalize_game(None,expected_event_id='event1',now=1000)
    assert g['status']=='UNAVAILABLE' and g.get('home_score') is None

def test_wrong_score_identity_is_unavailable():
    assert T.normalize_game(game(),expected_event_id='other',now=1000)['status']=='UNAVAILABLE'

@pytest.mark.parametrize('source_at',[984,1001])
def test_stale_or_future_game_clock_stops(source_at):
    g=T.normalize_game(dict(game(),source_at=source_at),expected_event_id='event1',now=1000)
    assert not g['clock_running'] and g['status']=='STALE'

def test_missing_baseball_data_remains_unknown():
    g=game();g.pop('outs');g['bases']=[1,0,1]
    r=T.normalize_game(g,expected_event_id='event1',now=1000)
    assert r['outs'] is None and r['bases'] is None
