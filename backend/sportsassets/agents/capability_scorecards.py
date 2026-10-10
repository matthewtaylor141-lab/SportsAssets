"""Capability measurements with explicit windows, denominators and missingness.

These are operational research scores, never claims of profitability. All
sampling is disclosed; a successful conversation is not a model improvement.
"""
from __future__ import annotations
import statistics
from . import capability_work as W

LIMIT=500


def sample(rows):
    return {'rows':rows[:LIMIT],'limited':len(rows)>LIMIT,'limit':LIMIT}


def fraction(n,d):
    return {'numerator':n,'denominator':d,'rate':n/d if d else None}


def measure(decisions,reviews,findings,work,now):
    ds,rs,fs=sample(decisions),sample(reviews),sample(findings)
    rows=ds['rows'];review=rs['rows'];audits=fs['rows']
    refusals={}
    for r in rows:
        reason=r.get('refusal') or r.get('verdict') or 'NOT_RECORDED'
        refusals[reason]=refusals.get(reason,0)+1
    recorded=[W.obj(r.get('measure')) for r in review if W.obj(r.get('measure'))]
    stale=sum(x.get('stale') is True for x in recorded)
    completed=[t for t in work if t.get('outcome',{}).get('reviewed')]
    overdue=[t['task_id'] for t in work if t['status'] not in W.TERMINAL and t['spec'].get('due_at',now)>=0 and t['spec'].get('due_at',now)<now]
    # Compare to source handoff, not to the time the UI happened to read it.
    latencies=[float(r['handoff_latency_s']) for r in review if r.get('handoff_latency_s') is not None and float(r['handoff_latency_s'])>=0]
    return {
      'window':{'start':now-86400,'end':now,'basis':'LAST_24_HOURS'},
      'derek':{'decisions':len(rows),'entries':fraction(sum(r.get('verdict')=='ENTER' for r in rows),len(rows)),
               'refusals':refusals,'sample_limited':ds['limited'],'sample_limit':LIMIT,
               'coverage':'UNKNOWN_WITHOUT_AVAILABLE_MARKET_CENSUS'},
      'xavier':{'reviews':len(review),'stale_measures':fraction(stale,len(recorded)),
                'missing_measures':len(review)-len(recorded),'sample_limited':rs['limited'],
                'first_review_latency':{'median_s':statistics.median(latencies) if latencies else None,'n':len(latencies),'basis':'FIRST_REVIEW_MINUS_HANDOFF'}},
      'audrey':{'findings':len(audits),'critical_findings':sum(r.get('severity')=='CRITICAL' for r in audits),
                'sample_limited':fs['limited'],'genuine_reviews_in_visible_work':len(completed),
                'work_sample_size':len(work),'overdue_task_ids':overdue},
      'forecast_quality':{'status':'NOT_SCORED','why':'Operational counts do not establish forecast calibration; a frozen forecast/outcome cohort is required.'},
      'improvement_claim':'Only a completed forward evaluation can report its measured result. Research review completion is not a performance gain.'}


async def read(conn,now,*,account=None):
    acct=await W.scope(conn,account)
    ds=[dict(r) for r in await conn.fetch("SELECT decision_id,decided_at,strategy,policy_version,verdict,refusal FROM paper_decisions WHERE account_id=$1 AND decided_at BETWEEN to_timestamp($2-86400) AND to_timestamp($2) ORDER BY decided_at DESC,decision_id LIMIT 501",acct,now)]
    rs=[dict(r) for r in await conn.fetch("SELECT r.review_id,r.reviewed_at,r.measure,CASE WHEN r.review_id=(SELECT x.review_id FROM paper_xavier_reviews x WHERE x.account_id=r.account_id AND x.group_id=r.group_id ORDER BY x.reviewed_at,x.review_id LIMIT 1) THEN extract(epoch FROM(r.reviewed_at-h.created_at)) END AS handoff_latency_s FROM paper_xavier_reviews r LEFT JOIN paper_handoffs h ON h.account_id=r.account_id AND h.group_id=r.group_id WHERE r.account_id=$1 AND r.reviewed_at BETWEEN to_timestamp($2-86400) AND to_timestamp($2) ORDER BY r.reviewed_at DESC,r.review_id LIMIT 501",acct,now)]
    fs=[dict(r) for r in await conn.fetch("SELECT finding_id,severity,recorded_at FROM paper_audrey_findings WHERE account_id=$1 AND recorded_at BETWEEN to_timestamp($2-86400) AND to_timestamp($2) ORDER BY recorded_at DESC,finding_id LIMIT 501",acct,now)]
    work=await W.tasks(conn,limit=60,account=acct)
    cards=measure(ds,rs,fs,work,now)
    # Compare separate strategy/version cohorts; never mix exploration and investment.
    cohorts={}
    for r in ds[:LIMIT]:
        key=(r['strategy'],r['policy_version']);cohorts[key]=cohorts.get(key,0)+1
    cards['derek']['cohorts']=[{'strategy':k[0],'version':k[1],'decisions':n} for k,n in cohorts.items()]
    return cards


async def delivery_events(conn,after=0,limit=30,*,account=None):
    """Read-only cursor adapter for an authenticated Slack outbox consumer.

Caller must apply its installed-app/workspace/channel allowlist and persist its
own delivery idempotency key. This route never creates a second agent review.
"""
    if type(after) is not int or after<0:raise ValueError('INVALID_CURSOR')
    acct=await W.scope(conn,account)
    rows=await conn.fetch("SELECT e.event_id,e.at,e.actor,e.kind,e.task_id,t.title,e.detail FROM agent_task_events e JOIN agent_tasks t ON t.task_id=e.task_id WHERE t.kind=$1 AND t.spec->>'account_id'=$2 AND e.event_id>$3 AND e.kind='GENUINE_REVIEW' ORDER BY e.event_id LIMIT $4",W.KIND,acct,after,min(50,max(1,limit)))
    events=[]
    for r in rows:
        d=W.obj(r['detail'])
        events.append({'delivery_key':'capability-review:'+str(r['event_id']),
                       'event_id':r['event_id'],'at':r['at'],'agent':r['actor'],
                       'task_id':r['task_id'],'title':r['title'],'message_id':d.get('message_id'),
                       'answer':str(d.get('answer') or '')[:3500],'source_ids':d.get('source_ids',[]),
                       'label':'Recorded agent research review · simulated account',
                       'office':'https://command.bettortoken.com/'+r['actor'].lower()})
    return {'events':events,'next_cursor':events[-1]['event_id'] if events else after,
            'delivery_status':'NOT_SENT_BY_THIS_READ','authority':'READ_ONLY'}


async def snapshot(conn,now,*,account=None):
    from .capability_tools import TOOLS
    from .capability_experiments import catalog
    acct=await W.scope(conn,account)
    work=await W.tasks(conn,limit=60,account=acct)
    for t in work:
        t['overdue']=t['status'] not in W.TERMINAL and t['spec']['due_at']<now
        outcome=t['outcome'];outcome.pop('investigation',None)
        outcome['answer']=str(outcome.get('answer') or '')[:1400]
    experiments=[dict(r) for r in await conn.fetch("SELECT p.proposal_id,p.agent_id,p.strategy,p.change_class,p.rationale,p.status,p.training_start,p.training_end,p.evaluation_start,p.evaluation_end,p.protocol,p.verdict,p.evaluation,p.last_attempt FROM paper_improvement_proposals p WHERE p.account_id=$1 ORDER BY p.proposed_at DESC LIMIT 20",acct)]
    for e in experiments:
        for k in ('protocol','evaluation','last_attempt'):e[k]=W.obj(e.get(k))
    heartbeat=W.obj(await conn.fetchval('SELECT value FROM ingestion_state WHERE key=$1',W.heartbeat_key(acct)))
    return {'status':'OK','read_at':now,'account_id':acct,
            'data_label':'LIVE MARKET DATA · SIMULATED EXECUTION',
            'control':await W.control(conn,account=acct),'heartbeat':heartbeat,'work':work,
            'work_limit':60,'tool_names':sorted(TOOLS),'experiments':experiments,
            'experiment_catalog':catalog(),
            'scorecards':await read(conn,now),
            'authority':'Research tasks and prospective evaluations; policy activation remains separately controlled.'}
