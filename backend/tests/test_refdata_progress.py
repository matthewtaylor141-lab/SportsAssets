"""(developer pass) reference-data recovery: classification, exact symbol,
cooldown before LIMIT, venue isolation, capacity vs backlog. Package tests,
run against the integrated repository code."""
import asyncio
import pytest
from sportsassets.market_plane import refdata_progress as R


def inst(s='m1'):
    return {'symbol':s,'priceScale':'1000','fractionalQtyScale':'100'}

@pytest.mark.parametrize('status',[401,403,404,429,500,502,503,None])
def test_failed_http_is_never_unlisted(status):
    response=R.instrument_response('m1',{'status':status,'body':{'instruments':[]}})
    assert R.classify_bootstrap('m1',response)['state']=='RETRY'

def test_transport_error_even_on_200_cannot_be_unlisted():
    r=R.instrument_response('m1',{'status':200,'body':{'instruments':[]},'transportError':'timeout'})
    assert R.classify_bootstrap('m1',r)['state']=='RETRY'

def test_explicit_successful_empty_list_is_named_unlisted():
    r=R.instrument_response('m1',{'status':200,'body':{'instruments':[]}})
    assert R.classify_bootstrap('m1',r)['state']=='UNLISTED'

@pytest.mark.parametrize('body',[None,{},'gateway response',{'instruments':None},{'instruments':{}}])
def test_unreadable_or_missing_list_never_asserts_absence(body):
    assert R.classify_bootstrap('m1',R.instrument_response('m1',{'status':200,'body':body}))['state']=='RETRY'

def test_uses_exact_symbol_not_first_record():
    r=R.instrument_response('m1',{'status':200,'body':{'instruments':[inst('other'),inst()]}})
    assert R.classify_bootstrap('m1',r)['record']['symbol']=='m1'

def test_wrong_symbol_is_not_an_empty_result():
    r=R.instrument_response('m1',{'status':200,'body':{'instruments':[inst('other')]}})
    assert not r['authoritative_empty']
    assert R.classify_bootstrap('m1',r)['state']=='RETRY'

def test_conflicting_same_symbol_records_refuse():
    r=R.instrument_response('m1',{'status':200,'body':{'instruments':[inst(),dict(inst(),priceScale='10')]}})
    assert R.classify_bootstrap('m1',r)['state']=='RETRY'

@pytest.mark.parametrize('scale',[0,-1,None,'NaN','Infinity',True,'junk'])
def test_invalid_scaling_never_authorizes_book(scale):
    r={'status':200,'record':dict(inst(),priceScale=scale)}
    assert R.classify_bootstrap('m1',r)['state']=='RETRY'

def test_cooldown_includes_future_clock_and_excludes_expired_attempts():
    assert R.cooling_ids({'future':102,'recent':99,'old':0},now=100,retry_s=30)==['future','recent']

def test_retry_filter_precedes_sql_limit_and_venue_isolated():
    from sportsassets.market_plane.registry import refdata_due
    class Conn:
        async def fetch(self,sql,*args):
            self.sql,self.args=sql,args
            assert "venue='POLYMARKET_US'" in sql
            assert sql.index('NOT (contract_id=ANY') < sql.index('LIMIT')
            excluded=set(args[2]);return [{'contract_id':str(i)} for i in range(100) if str(i) not in excluded][:args[1]]
    c=Conn();excluded=[str(i) for i in range(64)]
    got=asyncio.run(refdata_due(c,now=1000,unlisted_retry_s=300,limit=8,excluded=excluded))
    assert got==[str(i) for i in range(64,72)]

def test_capacity_does_not_call_73k_unread_records_stream_overflow():
    r=R.capacity_view(active=73072,pending=72936,subscribable=125,subscribed=117,max_per_stream=1000,max_streams=4)
    assert r['unused_configured_slots']==3883
    assert r['known_capacity_overflow']==0
    assert r['unresolved_coverage_backlog']==72936
    assert r['streams_if_all_active_are_pmx_listed']==74
    assert not r['full_capacity_requirement_proven']

@pytest.mark.parametrize('field',['active','pending','subscribable','subscribed'])
def test_capacity_refuses_negative_counts(field):
    kw=dict(active=10,pending=2,subscribable=8,subscribed=8,max_per_stream=10,max_streams=1);kw[field]=-1
    with pytest.raises(ValueError):R.capacity_view(**kw)


def test_existing_instrument_scale_parser_remains_authoritative():
    # The bootstrap calls the repository's existing scales_of parser; preserve
    # its normalized fields instead of requiring just one wire-schema alias.
    assert R.classify_bootstrap('m1',{'status':200,'record':{'symbol':'m1'},
                                     'priceScale':1000,'qtyScale':100})['state']=='VALID'

def test_unknown_full_universe_requirement_is_not_reported_as_one_stream():
    r=R.capacity_view(active=73072,pending=72936,subscribable=125,subscribed=117,max_per_stream=1000,max_streams=4)
    assert r['streams_for_known_subscribable']==1
    assert r['streams_required_for_full_coverage'] is None


def test_refdata_metrics_measure_throughput_and_never_count_retry_as_unlisted():
    t = {}
    a = R.refdata_metrics(t, {"attempted": 10, "stored": 7, "unlisted": 1,
                               "failed": 2}, lat_ms=[100, 200, 300, 400],
                           n429=1, budget=8, now=1000.0)
    b = R.refdata_metrics(t, {"attempted": 10, "stored": 10, "unlisted": 0,
                               "failed": 0}, lat_ms=[150] * 10, n429=0,
                           budget=12, now=1060.0)
    assert a["latency_ms"]["p50"] in (200, 300)
    tot = b["totals_since_boot"]
    assert tot["requests"] == 20 and tot["listed"] == 17
    assert tot["unlisted"] == 1 and tot["retryable"] == 2
    assert tot["http_429"] == 1 and tot["rate_429"] == 0.05
    assert tot["success_rate"] == 0.9
    assert tot["requests_per_min"] == 20.0
    assert tot["burn_down_per_hour"] == 1080.0
    assert b["budget"] == 12
