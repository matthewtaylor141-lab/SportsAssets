"""Persistent research plans on agent_tasks; no trading or provider I/O here.

Claims are fenced and budgeted under the durable control row. Every review
keeps a stable model request ID across restarts. Terminal work is never reclaimed.
"""
from __future__ import annotations
import hashlib
import json
import math
import uuid

#: The research queue's account before any PAPER epoch exists. The queue
#: itself FOLLOWS THE DURABLE PAPER SELECTOR (scope(): simulated_account_
#: context.selected_account), so after an activation or a rollback its task
#: specs, flow IDs, source-context checks, claims, control row and heartbeat
#: are the selected account's -- never an archived account's.
LEGACY_ACCOUNT='paper_acct_main'
#: None: follow the selector. A string PINS the queue to that account (an
#: isolated test or tool account); CONTROL, when set, pins its control row.
ACCOUNT=None
CONTROL=None
CONTROL_PREFIX='agent.capabilities.v1:'
HEARTBEAT_PREFIX='agent.capabilities.heartbeat:'
#: the part of a key that names the queue's account (loop_health copies the
#: heartbeat key template and resolves it the same way)
SELECTED_ACCOUNT='{selected_account}'
KIND='AGENT_CAPABILITY_REVIEW_V1'
AGENTS=('DEREK','XAVIER','AUDREY')
TERMINAL=('CLOSED_NO_CHANGE','REJECTED','CANCELLED')
DEFAULT={'enabled':False,'hourly_limit':24,'spent':0,'hour':0,'revision':0}
MAX_OPEN=300
LEASE_S=120
MAX_ATTEMPTS=3


def obj(v):
    return json.loads(v) if isinstance(v,str) else dict(v or {})


def row(v):
    if not v:return None
    r=dict(v)
    for k in ('spec','outcome'):
        if k in r:r[k]=obj(r[k])
    return r


def stable(*parts):
    return hashlib.sha256(json.dumps(parts,sort_keys=True,default=str).encode()).hexdigest()[:32]


def validate_plan(title,agent,priority,due,now):
    if not isinstance(title,str) or not 1<=len(title.strip())<=500:raise ValueError('INVALID_GOAL')
    if agent not in AGENTS:raise ValueError('UNKNOWN_AGENT')
    if type(priority) is not int or priority not in range(1,6):raise ValueError('PRIORITY_1_TO_5_REQUIRED')
    if isinstance(due,bool) or not isinstance(due,(int,float)) or not math.isfinite(due) or not now<due<=now+30*86400:raise ValueError('DUE_WITHIN_30_DAYS_REQUIRED')


async def scope(conn,account=None):
    """The account the research queue works for: `account` when the caller
    supplies one, else the pinned ACCOUNT, else the durable PAPER selector
    (an unreadable selector raises; it never falls back to an account)."""
    if account is not None:return account
    if ACCOUNT is not None:return ACCOUNT
    from ..simulated_account_context import selected_account
    return await selected_account(conn)


def control_key(account):
    return CONTROL or CONTROL_PREFIX+account


def heartbeat_key(account):
    return (HEARTBEAT_PREFIX+SELECTED_ACCOUNT).replace(SELECTED_ACCOUNT,account)


async def _inherited_control(conn,account):
    """A newly selected epoch account has no control row of its own: the
    nearest registered ancestor's row (account_lineage) carries the
    manager's decision (enabled, hourly limit, this hour's spend) across the
    switch, so the worker is neither silently switched off nor given a
    fresh budget. A pinned CONTROL never inherits."""
    if CONTROL is not None:return None
    from ..simulated_account_context import account_lineage
    for parent in (await account_lineage(conn,account))[1:]:
        v=await conn.fetchval("SELECT value FROM ingestion_state WHERE key=$1",CONTROL_PREFIX+parent)
        if v is not None:return {**obj(v),'inherited_from_account_id':parent}
    return None


async def schema(conn):
    return bool(await conn.fetchval("SELECT to_regclass('agent_tasks') IS NOT NULL AND to_regclass('paper_handoffs') IS NOT NULL AND to_regclass('paper_recommendations') IS NOT NULL"))


async def control(conn,lock=False,*,account=None):
    """The control row of the queue's account (scope()), with `account_id`.
    Absent for a newly selected epoch account: its nearest ancestor's row is
    read (and, under the lock, copied as its first row)."""
    acct=await scope(conn,account)
    key=control_key(acct)
    if lock:
        first=await conn.fetchval("SELECT value FROM ingestion_state WHERE key=$1",key)
        if first is None:
            seed={**DEFAULT,**((await _inherited_control(conn,acct)) or {})}
            await conn.execute("INSERT INTO ingestion_state(key,value) VALUES($1,$2::jsonb) ON CONFLICT DO NOTHING",key,json.dumps(seed,allow_nan=False))
    v=await conn.fetchval("SELECT value FROM ingestion_state WHERE key=$1"+(' FOR UPDATE' if lock else ''),key)
    if v is None and not lock:
        v=await _inherited_control(conn,acct)
    return {**DEFAULT,**obj(v),'account_id':acct}


async def save_control(conn,c):
    acct=c.get('account_id') or await scope(conn)
    body={k:v for k,v in c.items() if k!='account_id'}
    await conn.execute("UPDATE ingestion_state SET value=$2::jsonb WHERE key=$1",control_key(acct),json.dumps(body,allow_nan=False))


async def configure(conn,*,enabled,hourly_limit,actor,now,account=None):
    from . import registry as R
    if type(enabled) is not bool or type(hourly_limit) is not int or not 1<=hourly_limit<=120:raise ValueError('INVALID_CONTROL')
    if not actor or actor.upper() in AGENTS or len(actor)>100:raise ValueError('NAMED_MANAGER_REQUIRED')
    async with conn.transaction():
        c=await control(conn,True,account=account)
        acct=c['account_id']
        c.update(enabled=enabled,hourly_limit=hourly_limit,revision=int(c['revision'])+1,updated_at=now,actor=actor)
        await save_control(conn,c)
        got=await R.create_task(conn,assignee='AUDREY',created_by=actor,kind='CAPABILITY_CONTROL',title='Research worker controls',spec={'account_id':acct},task_id='capability-control:'+acct,now=now)
        if not got.get('ok'):raise RuntimeError('CONTROL_AUDIT_UNAVAILABLE')
        got=await R.task_event(conn,got['task_id'],kind='CONTROL_CHANGED',actor=actor,detail=c,now=now)
        if not got.get('ok'):raise RuntimeError('CONTROL_AUDIT_UNAVAILABLE')
    return c


async def create_flow(conn,*,source_key,title,first='DEREK',priority=3,due,actor,now,context=None,account=None):
    """One immutable, three-agent research plan; duplicate source -> same IDs.
    The plan belongs to the queue's account (scope()): its IDs, its spec and
    the account its source context must be recorded in."""
    from . import registry as R
    validate_plan(title,first,priority,now+1,now)
    if not isinstance(source_key,str) or not 1<=len(source_key)<=180:raise ValueError('INVALID_SOURCE_KEY')
    context=dict(context or {})
    if set(context)-{'position_id','decision_id','recommendation_id'} or any(not isinstance(v,str) or len(v)>150 for v in context.values()):raise ValueError('INVALID_SOURCE_CONTEXT')
    order=[first]+[a for a in AGENTS if a!=first and a!='AUDREY']
    if first!='AUDREY':order.append('AUDREY')
    acct=await scope(conn,account)
    ids=['capwork:'+stable(acct,source_key,a) for a in order]
    async with conn.transaction():
        await control(conn,True,account=acct)
        existing=await conn.fetchrow('SELECT * FROM agent_tasks WHERE task_id=$1',ids[0])
        if existing:
            old=row(existing)
            if old['title']!=title or old['spec'].get('context')!=context or old['spec'].get('flow_ids')!=ids or old['spec'].get('priority')!=priority:raise ValueError('SOURCE_IDEMPOTENCY_MISMATCH')
            return {'created':False,'task_ids':ids}
        validate_plan(title,first,priority,due,now)
        scoped={'position_id':('paper_orders','group_id'),'decision_id':('paper_decisions','decision_id'),'recommendation_id':('paper_recommendations','recommendation_id')}
        for key,value in context.items():
            table,column=scoped[key]  # fixed identifiers; never supplied by a caller
            if not await conn.fetchval(f'SELECT 1 FROM {table} WHERE account_id=$1 AND {column}=$2 LIMIT 1',acct,value):raise ValueError('SOURCE_CONTEXT_NOT_IN_ACCOUNT')
        n=await conn.fetchval("SELECT count(*) FROM agent_tasks WHERE kind=$1 AND spec->>'account_id'=$2 AND status NOT IN ('CLOSED_NO_CHANGE','REJECTED','CANCELLED')",KIND,acct)
        if n+3>MAX_OPEN:raise ValueError('WORK_QUEUE_AT_CAPACITY')
        for i,agent in enumerate(order):
            spec={'account_id':acct,'source_key':source_key,'context':context,'priority':priority,'due_at':due,'dependencies':ids[:i], 'next_action':'Gather scoped evidence, review it, and identify uncertainty and a measurable next step.', 'attempts':0,'lease_until':0,'claim_token':None,'authority':'RESEARCH_ONLY','flow_ids':ids}
            got=await R.create_task(conn,assignee=agent,created_by=actor,kind=KIND,title=title,spec=spec,task_id=ids[i],evidence=[{'source_key':source_key,**context}],now=now)
            if not got.get('ok'):raise RuntimeError('TASK_WRITE_FAILED')
    return {'created':True,'task_ids':ids}


async def tasks(conn,agent=None,limit=60,*,include_investigation=False,account=None):
    if agent is not None and agent not in AGENTS:raise ValueError('UNKNOWN_AGENT')
    acct=await scope(conn,account)
    projection='*' if include_investigation else "task_id,assignee,created_by,kind,title,spec,status,created_at,updated_at,(outcome - 'investigation') AS outcome"
    return [row(r) for r in await conn.fetch('SELECT '+projection+" FROM agent_tasks WHERE kind=$1 AND spec->>'account_id'=$2 AND ($3::text IS NULL OR assignee=$3) ORDER BY created_at DESC,task_id LIMIT $4",KIND,acct,agent,min(100,max(1,int(limit))))]


def ready(task,dependencies,now,account=None):
    s=task['spec']
    return (task['status'] not in TERMINAL and s.get('account_id')==(account if account is not None else ACCOUNT)
            and s.get('attempts',0)<MAX_ATTEMPTS and s.get('lease_until',0)<=now
            and all(dependencies.get(x)=='CLOSED_NO_CHANGE' for x in s.get('dependencies',[])))


# The claim reads every column except ``outcome``: that column holds each
# task's recorded investigation (up to five tool snapshots) and is never
# needed to claim -- execute/save_investigation/finish re-read the row.
CLAIM_COLUMNS='task_id,assignee,created_by,kind,title,spec,status,directive_id,evidence,created_at,updated_at'


async def claim(conn,now,*,account=None):
    """Claim at most one ready task in a CONSTANT number of round trips.

    rc6.2 agent-truth, DEFENSIVE BOUND. The scan used to issue one
    dependency query per candidate (up to 300) and one UPDATE + one event
    INSERT per rejected task inside the tick's 5 s CLAIM budget, so a large
    not-yet-ready queue could need hundreds of round trips. Now: one
    candidate read (without ``outcome``), one dependency read for the whole
    candidate set, one bulk rejection and one bulk event insert, then the
    single claim. Decisions are the same, in the same order: a rejection
    decided earlier in the pass is visible to later candidates, exactly as
    the per-row reads saw it.

    This does NOT explain or fix the production loop errors. Readback
    (research-sql run 37978689499): the claim worked at its hourly budget
    (24 CLAIMED per hour through 2026-10-08, with 16 DEPENDENCY_FAILED
    committed per hour, so rejections were not rolled back); 0 tasks were
    open when 'CLAIM: TimeoutError' was recorded, so the base ran the same
    few statements this does; the last error is now 'HEARTBEAT:
    TimeoutError', a phase that never touches the claim; and the
    per-candidate dependency query shows max 68 ms. The mass REJECTED
    comes from finish(): REVIEW_INCOMPLETE on every claimed review spends
    MAX_ATTEMPTS and dependents then cascade DEPENDENCY_FAILED. The open
    causes are review-phase failures in execute/finish and unattributed
    waits (pool acquire, the ingestion_state row lock, event-loop stalls).
    """
    async with conn.transaction():
        c=await control(conn,True,account=account)
        acct=c['account_id']
        if c.get('enabled') is not True:return None
        hour=int(now//3600)
        if c.get('hour')!=hour:c.update(hour=hour,spent=0)
        if c['spent']>=c['hourly_limit']:return None
        active=await conn.fetchval("SELECT count(*) FROM agent_tasks WHERE kind=$1 AND spec->>'account_id'=$2 AND status='IN_PROGRESS' AND (spec->>'lease_until')::double precision>$3",KIND,acct,now)
        if active>=2:return None
        rs=await conn.fetch("SELECT "+CLAIM_COLUMNS+" FROM agent_tasks WHERE kind=$1 AND spec->>'account_id'=$2 AND status IN ('OPEN','IN_PROGRESS','WAITING') ORDER BY (spec->>'priority')::integer DESC,(spec->>'due_at')::double precision,task_id LIMIT 300 FOR UPDATE SKIP LOCKED",KIND,acct)
        candidates=[row(r) for r in rs]
        wanted=sorted({str(d) for t in candidates for d in (t['spec'].get('dependencies') or [])})
        statuses={}
        if wanted:
            statuses={r['task_id']:r['status'] for r in await conn.fetch('SELECT task_id,status FROM agent_tasks WHERE task_id=ANY($1::text[])',wanted)}
        rejected=[];chosen=None
        for t in candidates:
            s=t['spec']
            deps={x:statuses[x] for x in (s.get('dependencies') or []) if x in statuses}
            if s.get('attempts',0)>=MAX_ATTEMPTS and s.get('lease_until',0)<=now:
                rejected.append((t['task_id'],'RETRY_BUDGET_EXHAUSTED',{}))
                statuses[t['task_id']]='REJECTED'
                continue
            if any(v in ('REJECTED','CANCELLED') for v in deps.values()):
                rejected.append((t['task_id'],'DEPENDENCY_FAILED',{'dependencies':deps}))
                statuses[t['task_id']]='REJECTED'
                continue
            if not ready(t,deps,now,acct):continue
            chosen=t;break
        if rejected:
            ids=[x[0] for x in rejected]
            await conn.execute("UPDATE agent_tasks SET status='REJECTED',updated_at=to_timestamp($2) WHERE task_id=ANY($1::text[])",ids,now)
            await conn.execute("INSERT INTO agent_task_events(task_id,at,kind,actor,detail) SELECT x.task_id,to_timestamp($2),x.kind,'SYSTEM',x.detail::jsonb FROM unnest($1::text[],$3::text[],$4::text[]) WITH ORDINALITY AS x(task_id,kind,detail,n) ORDER BY x.n",
                               ids,now,[x[1] for x in rejected],[json.dumps(x[2],default=str,allow_nan=False) for x in rejected])
        if chosen is None:return None
        t=chosen;s=t['spec']
        s.update(attempts=s['attempts']+1,claim_token=uuid.uuid4().hex,lease_until=now+LEASE_S)
        await conn.execute("UPDATE agent_tasks SET status='IN_PROGRESS',spec=$2::jsonb,updated_at=to_timestamp($3) WHERE task_id=$1",t['task_id'],json.dumps(s),now)
        c['spent']+=1;await save_control(conn,c)
        await event(conn,t['task_id'],'CLAIMED','SYSTEM',{'attempt':s['attempts'],'lease_until':s['lease_until']},now)
        t['status']='IN_PROGRESS';return t


async def event(conn,tid,kind,actor,detail,now):
    await conn.execute("INSERT INTO agent_task_events(task_id,at,kind,actor,detail) VALUES($1,to_timestamp($2),$3,$4,$5::jsonb)",tid,now,kind,actor,json.dumps(detail,default=str,allow_nan=False))


def genuine(reply):
    return (reply.get('status')=='ANSWERED' and (reply.get('provider') or {}).get('mode')=='LLM'
            and bool(reply.get('message_id')) and bool(reply.get('answer'))
            and not reply.get('demonstration')
            and any(f.get('source','').startswith('paper_') or
                    (f.get('source')=='agent_tasks' and f.get('field','').startswith('investigation_'))
                    for f in (reply.get('facts') or []) if isinstance(f,dict)))


async def finish(conn,task,reply,now,*,error=None):
    """CAS completion: a revoked/expired worker cannot record a review."""
    async with conn.transaction():
        current=row(await conn.fetchrow('SELECT * FROM agent_tasks WHERE task_id=$1 FOR UPDATE',task['task_id']))
        if not current or current['status'] in TERMINAL:return False
        s=current['spec']
        if s.get('claim_token')!=task['spec']['claim_token'] or s.get('lease_until',0)<=now:return False
        good=genuine(reply) and error is None
        status='CLOSED_NO_CHANGE' if good else 'REJECTED' if s['attempts']>=MAX_ATTEMPTS else 'WAITING'
        retry_after=0
        if reply.get('status')=='PENDING' and error is None:
            # persona_chat retains pending requests for 300s after a dead
            # process. A 120s task lease must not burn all three attempts
            # before that existing request can be recovered.
            s['pending_polls']=s.get('pending_polls',0)+1
            status='WAITING' if s['pending_polls']<=6 else 'REJECTED'
            if status=='WAITING':
                s['attempts']=max(0,s['attempts']-1)
                retry_after=now+65
            error='PROVIDER_REQUEST_IN_PROGRESS' if status=='WAITING' else 'PROVIDER_PENDING_BUDGET_EXHAUSTED'
        outcome={'reviewed':good,'message_id':reply.get('message_id'),'answer':str(reply.get('answer') or '')[:6000] if good else None,'provider_mode':(reply.get('provider') or {}).get('mode'),'source_ids':[str(f.get('record_id')) for f in (reply.get('facts') or [])[:30]],'error':error or (None if good else 'NO_GENUINE_GROUNDED_REVIEW'),'completed_at':now if good else None}
        outcome['investigation']=current['outcome'].get('investigation',[])
        s.update(claim_token=None,lease_until=retry_after)
        await conn.execute("UPDATE agent_tasks SET status=$2,spec=$3::jsonb,outcome=$4::jsonb,updated_at=to_timestamp($5) WHERE task_id=$1",task['task_id'],status,json.dumps(s),json.dumps(outcome),now)
        await event(conn,task['task_id'],'GENUINE_REVIEW' if good else 'REVIEW_INCOMPLETE',task['assignee'] if good else 'SYSTEM',outcome,now)
        rid=s.get('context',{}).get('recommendation_id')
        if good and rid and task['assignee'] in ('DEREK','XAVIER'):
            await conn.execute("INSERT INTO paper_recommendation_events(recommendation_id,at,actor,kind,body,detail) SELECT recommendation_id,to_timestamp($2),$3,'RESPONSE',$4,$5::jsonb FROM paper_recommendations WHERE recommendation_id=$1 AND account_id=$6 AND owner_agent=$3",rid,now,task['assignee'],outcome['answer'][:3500],json.dumps({'task_id':task['task_id'],'message_id':outcome['message_id']}),s.get('account_id'))
    return True


async def cancel(conn,tid,actor,now,*,account=None):
    acct=await scope(conn,account)
    async with conn.transaction():
        t=row(await conn.fetchrow("SELECT * FROM agent_tasks WHERE task_id=$1 AND kind=$2 AND spec->>'account_id'=$3 FOR UPDATE",tid,KIND,acct))
        if not t:raise ValueError('NO_SUCH_WORK')
        if t['status'] in TERMINAL:return {'changed':False,'status':t['status']}
        t['spec'].update(claim_token=None,lease_until=0)
        await conn.execute("UPDATE agent_tasks SET status='CANCELLED',spec=$2::jsonb,updated_at=to_timestamp($3) WHERE task_id=$1",tid,json.dumps(t['spec']),now)
        await event(conn,tid,'CANCELLED',actor,{'server_model_call_may_finish':True},now)
        return {'changed':True,'status':'CANCELLED'}


async def save_investigation(conn,task,evidence,now):
    """A stale claimant cannot replace the new owner's evidence."""
    async with conn.transaction():
        t=row(await conn.fetchrow('SELECT * FROM agent_tasks WHERE task_id=$1 FOR UPDATE',task['task_id']))
        if (not t or t['status']!='IN_PROGRESS' or
                t['spec'].get('claim_token')!=task['spec']['claim_token'] or
                t['spec'].get('lease_until',0)<=now):return False
        # Request IDs survive restarts; retain the exact evidence associated
        # with that request instead of attaching newer reads to an old answer.
        if 'investigation' in t['outcome']:return True
        outcome={**t['outcome'],'investigation':evidence}
        await conn.execute('UPDATE agent_tasks SET outcome=$2::jsonb WHERE task_id=$1',task['task_id'],json.dumps(outcome,default=str,allow_nan=False))
        return True
