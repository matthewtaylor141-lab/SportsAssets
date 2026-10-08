"""Bounded, read-only research reports. No trading or connector authority."""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime
import json
import math

LIMIT = 500
# These paths continue before ext.persist/_paper_valuation in the collector.
# Generic REFUSED is NOT enough: calibration-only rows can still reach Derek.
PRE_VALUATION_CODES = {'NO_PINNACLE_ON_EVENT','QUOTE_STALE_ON_ARRIVAL',
                       'QUOTE_STALE_AS_DELIVERED_BY_THE_PROVIDER',
                       'PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED',
                       'PINNAPI_PRIMARY_FIXTURE_LISTS_NO_FULL_GAME_MONEYLINE',
                       'NO_VENUE_NATIVE_CONTRACT_IN_PREMAP','WS_REFERENCE_NOT_USABLE',
                       'WS_TRIGGER_SUPERSEDED',
                       # NO_PINNACLE_ON_EVENT by cause (P0 incident): the WS
                       # refusal reason or the discovery payload's absence
                       # now leads such an event; it stops at the same point.
                       'THEODDSAPI_PAYLOAD_HAS_NO_PINNACLE_BOOK',
                       'THEODDSAPI_PINNACLE_HAS_NO_H2H_MARKET',
                       'PINNAPI_PRIMARY_CLOCK_INVALID',
                       'PINNAPI_PRIMARY_RUNTIME_UNIDENTIFIED',
                       'PINNAPI_PRIMARY_SPORT_UNSUPPORTED',
                       'PINNAPI_PRIMARY_FIXTURE_UNPROVED',
                       'PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS',
                       'PINNAPI_PRIMARY_NO_EXACT_FIXTURE',
                       'PINNAPI_PRIMARY_FEED_HOLDS_NO_FIXTURE_FOR_EITHER_TEAM',
                       'PINNAPI_PRIMARY_NOT_FULL_GAME_H2H',
                       'PINNAPI_PRIMARY_INCOMPLETE_OUTCOMES',
                       'PINNAPI_PRIMARY_PHASE_UNPROVED',
                       'FEED_OWNERSHIP_NOT_HELD', 'FEED_EPOCH_NOT_RESYNCHRONIZED',
                       'FEED_MARKET_NOT_IN_CURRENT_STATE',
                       'FEED_QUOTE_FROM_A_PREVIOUS_CONNECTION',
                       'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE',
                       'FEED_QUOTE_CHANGE_TIME_IN_THE_FUTURE',
                       'FEED_QUOTE_OLDER_THAN_LIMIT', 'FEED_MARKET_CLOSED'}

# Global ingestion evidence: deliberately not presented as account decisions.
OPPORTUNITIES_SQL = """SELECT id,cycle_id,cycle_at,sport_key,family,provider_event_id,
 us_market_slug,stage,outcome,first_refusal,provider_lag_s,our_processing_s,quote_age_s
 FROM ext_candidate_outcomes WHERE cycle_at BETWEEN to_timestamp($1-86400)
 AND to_timestamp($1) ORDER BY cycle_at DESC,id DESC LIMIT 501"""
# Read both purposes, report separately; never selects a candidate for execution.
FORECAST_SQL = """SELECT id,experiment_id,version,provider,devig_method,record_purpose,
 event_key,condition_id,contract_selection,sport_family,market,period,line,
 probability,decided_at,outcome_known,outcome,outcome_at
 FROM external_valuations WHERE decided_at BETWEEN to_timestamp($1-2592000)
 AND to_timestamp($1) AND record_purpose IN ('ENTRY_DECISION','CALIBRATION_ONLY')
 ORDER BY decided_at DESC,id DESC LIMIT 501"""
CONTRIBUTION_SQL = """SELECT provider,version,record_purpose,count(*) AS valuations,
 count(DISTINCT event_key) AS distinct_events,max(decided_at) AS latest_valuation_at
 FROM external_valuations WHERE decided_at BETWEEN to_timestamp($1-86400)
 AND to_timestamp($1) AND record_purpose IN ('ENTRY_DECISION','CALIBRATION_ONLY')
 GROUP BY provider,version,record_purpose ORDER BY count(*) DESC LIMIT 30"""

BOOK_HEALTH_SQL = """SELECT source,count(*) AS observations,
 count(*) FILTER (WHERE error IS NULL) AS reads_without_error,
 count(DISTINCT us_market_slug) AS distinct_contracts,
 max(observed_at) AS latest_observation_at,
 count(*) FILTER (WHERE error IS NULL AND observed_at>=to_timestamp($1-30)) AS recent_reads
 FROM paper_book_observations WHERE observed_at BETWEEN to_timestamp($1-86400)
 AND to_timestamp($1) GROUP BY source ORDER BY count(*) DESC LIMIT 20"""


def number(x):
    if isinstance(x, bool): return None
    try:
        n=float(x)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError): return None


def epoch(x):
    return x.timestamp() if isinstance(x, datetime) else number(x)


def obj(x):
    if isinstance(x,dict): return x
    try:
        y=json.loads(x)
        return y if isinstance(y,dict) else {}
    except (TypeError,ValueError): return {}


def sample(rows):
    return {'rows': min(len(rows),LIMIT), 'limit':LIMIT, 'truncated':len(rows)>LIMIT,
            'population_totals_known':len(rows)<=LIMIT}


def missed(rows, now):
    rows=list(rows); meta=sample(rows); visible=rows[:LIMIT]
    latest={}; unidentified=0; reasons=Counter(); examples=defaultdict(list)
    for r in visible:
        if not r.get('provider_event_id'):
            unidentified+=1
            continue
        # Do not collapse sports or market families into one opportunity.
        key=(r['sport_key'],r.get('family'),r['provider_event_id'])
        if key not in latest or (epoch(r['cycle_at']),r['id']) > (epoch(latest[key]['cycle_at']),latest[key]['id']): latest[key]=r
    for r in latest.values():
        reason=r.get('first_refusal') or r['outcome']
        reasons[reason]+=1
        if len(examples[reason])<3:
            examples[reason].append({k:r.get(k) for k in ('id','provider_event_id','us_market_slug','stage','cycle_id','provider_lag_s','our_processing_s','quote_age_s')})
    blocked=sum(r.get('first_refusal') in PRE_VALUATION_CODES for r in latest.values())
    unclassified=sum(r['outcome']=='REFUSED' and r.get('first_refusal') not in PRE_VALUATION_CODES for r in latest.values())
    return {'status':'OK' if visible else 'NO_EVIDENCE','window_s':86400,'read_at':now,
            'scope':'GLOBAL_INGESTION_NOT_ACCOUNT_EXECUTION','sample':meta,
            'recorded_attempts':len(visible),'distinct_candidate_keys':len(latest),
            'latest_refused_before_valuation':blocked,'unidentified_rows':unidentified,
            'refusals_with_unproven_decision_reach':unclassified,
            'latest_deferred_before_evaluation':sum(r['outcome']=='DEFERRED' for r in latest.values()),
            'latest_admitted':sum(r['outcome']=='ADMITTED' for r in latest.values()),
            'blockers':[{'reason':k,'distinct_keys':n,'examples':examples[k]} for k,n in reasons.most_common(15)],
            'omitted_reason_groups':max(0,len(reasons)-15),
            'limitation':'Latest state within sampled window. ADMITTED does not prove a Derek decision. Keys are provider event/family, not all underlying markets. Only known early-return codes prove pre-valuation loss; other refusals may still have paper decisions. Unseen provider markets and lost profit are unknown.'}


def forecasts(rows, now):
    """Descriptive forward-record scoring; no tuned thresholds or performance claims.

    One earliest available forecast per event/contract/version/cohort in this
    bounded sample. Versions are forecast producer versions, not entry policies.
    """
    rows=list(rows); meta=sample(rows); groups={}; excluded=Counter()
    for r in rows[:LIMIT]:
        p=number(r.get('probability')); at=epoch(r.get('decided_at'))
        if p is None or not 0<=p<=1 or at is None or at>now or not r.get('event_key'):
            excluded['INVALID_FORECAST_OR_IDENTITY']+=1; continue
        cohort=tuple(r.get(k) for k in ('experiment_id','version','provider','devig_method','record_purpose','sport_family','market','period','line'))
        identity=(r['event_key'],r.get('condition_id'),r.get('contract_selection'))
        key=(cohort,identity)
        if key not in groups or (at,r['id']) < (epoch(groups[key]['decided_at']),groups[key]['id']): groups[key]=r
    cohorts=defaultdict(list)
    for (cohort,_),r in groups.items(): cohorts[cohort].append(r)
    result=[]
    for cohort,rs in cohorts.items():
        scored=[]; pending=invalid=0
        for r in rs:
            at=epoch(r.get('outcome_at')); forecast_at=epoch(r['decided_at'])
            if not r.get('outcome_known') or at is None or at>now: pending+=1; continue
            if r.get('outcome') not in (0,1) or at<=forecast_at:
                invalid+=1; continue
            scored.append((float(r['probability']),int(r['outcome']),r['id']))
        n=len(scored); bins=[]
        for i in range(10):
            b=[(p,y) for p,y,_ in scored if min(9,int(p*10))==i]
            if b: bins.append({'lower':i/10,'upper':(i+1)/10,'n':len(b),'mean_forecast':sum(p for p,y in b)/len(b),'observed_rate':sum(y for p,y in b)/len(b)})
        result.append(dict(zip(('experiment','forecast_version','provider','method','record_purpose','sport','market','period','line'),cohort),
            n_forecasts=len(rs),n_resolved=n,n_pending=pending,n_invalid_outcomes=invalid,
            brier=sum((p-y)**2 for p,y,_ in scored)/n if n else None,
            log_loss=-sum(y*math.log(max(1e-15,p))+(1-y)*math.log(max(1e-15,1-p)) for p,y,_ in scored)/n if n else None,
            calibration_bins=bins,source_ids=[i for _,_,i in scored[:10]],
            status='DESCRIPTIVE_FORWARD_RECORDS' if n else 'INSUFFICIENT_OUTCOMES'))
    return {'status':'OK' if result else 'NO_EVIDENCE','sample':meta,'window_s':2592000,
            'cohorts':result[:8],'cohort_count':len(result),'omitted_cohorts':max(0,len(result)-8),'excluded':dict(excluded),'improvement_proven':False,
            'scope':'GLOBAL_SOURCE_FORECASTS_NOT_ACCOUNT_PNL',
            'limitation':'First forecast within sample, not necessarily first ever. Versions and record purposes stay separate. Brier/log loss measure probability quality, not trading P&L. No paired superiority test or causal improvement claim; unresolved outcomes are not losses.'}


def connectors(feed, contributions, now, cycle=None, books=()):
    cycle=obj(cycle); credits=obj(cycle.get('credits'))
    cycle_at=number(cycle.get('at')); cycle_age=now-cycle_at if cycle_at is not None else None
    f=obj(feed); stamp=number(f.get('beat_at')); age=now-stamp if stamp is not None else None
    recent=age is not None and 0<=age<=90
    cache=obj(f.get('cache')); census=obj(f.get('coverage_census'))
    def counters(x):
        return {str(k)[:100]:v for k,v in list(obj(x).items())[:40] if type(v) is int and v>=0}
    return {'status':'RECENT_TELEMETRY' if recent else 'STALE_OR_MISSING_TELEMETRY',
            'execution_authority':False,'heartbeat_age_s':age,'scope':'GLOBAL_INGESTION',
            'connectors':[
                {'name':'PinnAPI WebSocket','record_id':'ingestion_state:pinnapi_feed_last',
                 'reported_state':str(f.get('state','UNKNOWN'))[:80],
                 'events':number(cache.get('events')),'markets':number(cache.get('markets')),
                 'unknown_change_age_markets':number(cache.get('markets_age_unknown')),
                 'coverage_states':counters(census.get('states')),
                 'coverage_reconciled':census.get('reconciled') is True,
                 'coverage_current':recent,'requests':'WebSocket message flow is not REST request count'},
                {'name':'The Odds API','record_id':'ingestion_state:ext_pinnacle_last_cycle',
                 'cycle_age_s':cycle_age,'credits_used':number(credits.get('used')),'credits_remaining':number(credits.get('remaining')),
                 'status':'SEE_RECORDED_PROVIDER_CONTRIBUTIONS','independent_book_attribution':'NOT_INSTRUMENTED'},
                {'name':'Polymarket US paper book reader','window_s':86400,'recorded_sources':[dict(b) for b in books[:20]],
                 'status':'RECORDED_OBSERVATIONS_NOT_EXECUTION_AUTHORITY'},
                {'name':'Kalshi','status':'NO_VERIFIED_BOOK_HEALTH_ADAPTER_IN_THIS_REPORT'},
                {'name':'Anthropic / ElevenLabs','status':'MODEL_VOICE_USAGE_AND_COST_NOT_INSTRUMENTED'}],
            'recorded_valuation_contributions':[dict(r) for r in contributions[:30]],
            'contribution_window_s':86400,'contribution_basis':'Persisted primary-reference provider; independent secondary books are not attributed by this query.',
            'limitation':'A connection or valuation does not prove a fill, profit, or subscription ROI. Invoice costs, quota usage and per-connector execution attribution remain unknown.'}


async def read(conn,name,now):
    if name=='missed_opportunities': return missed([dict(r) for r in await conn.fetch(OPPORTUNITIES_SQL,now)],now)
    if name=='forecast_evaluation': return forecasts([dict(r) for r in await conn.fetch(FORECAST_SQL,now)],now)
    if name=='connector_health':
        feed=await conn.fetchval('SELECT value FROM ingestion_state WHERE key=$1','pinnapi_feed_last')
        cycle=await conn.fetchval('SELECT value FROM ingestion_state WHERE key=$1','ext_pinnacle_last_cycle')
        books=[dict(r) for r in await conn.fetch(BOOK_HEALTH_SQL,now)]
        return connectors(feed,[dict(r) for r in await conn.fetch(CONTRIBUTION_SQL,now)],now,cycle,books)
    raise ValueError('UNKNOWN_REPORT')
