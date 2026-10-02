"""Typed evidence toolbox. No URLs, SQL strings, shell or venue mutation tools."""
from __future__ import annotations
import asyncio
import json
import time
from . import capability_work as W

TOOLS={'account','position','decision','market','rules','recommendation','lessons','proposals','work'}


class Toolkit:
    def __init__(self,agent,now,*,max_calls=5):
        if agent not in W.AGENTS:raise ValueError('UNKNOWN_AGENT')
        self.agent,self.now,self.left=agent,now,min(5,max(1,max_calls))
        self.deadline=time.monotonic()+8

    async def read(self,conn,name,record_id=None):
        if name not in TOOLS:raise ValueError('TOOL_NOT_ALLOWED')
        if self.left<=0 or time.monotonic()>=self.deadline:raise ValueError('TOOL_BUDGET_EXHAUSTED')
        if record_id is not None and (not isinstance(record_id,str) or len(record_id)>150):raise ValueError('INVALID_RECORD_ID')
        self.left-=1
        try:
            async with asyncio.timeout(min(2,self.deadline-time.monotonic())):
                async with conn.transaction(readonly=True,isolation='repeatable_read'):
                    data=await self._read(conn,name,record_id)
            if len(json.dumps(data,default=str))>40000:return {'status':'UNAVAILABLE','why':'EVIDENCE_TOO_LARGE','tool':name}
            return {'status':'EMPTY' if data is None or data==[] else 'OK','tool':name,'read_at':self.now,'account_id':W.ACCOUNT,'data':data,'basis':'PERSISTED_RECORDS; source timestamps determine freshness, not read_at'}
        except (TimeoutError,ValueError) as exc:
            return {'status':'UNAVAILABLE','tool':name,'why':str(exc) if isinstance(exc,ValueError) else 'EVIDENCE_READ_TIMEOUT'}

    async def _read(self,c,name,rid):
        from .. import bettor_paper_ledger as L
        from . import paper_learning as P
        if name=='account':return await L.cash_state(c,W.ACCOUNT)
        if name=='work':return await W.tasks(c,self.agent,20)
        if name=='lessons':return await P.lessons(c,account_id=W.ACCOUNT,agent=self.agent,limit=10)
        if name=='proposals':return await P.proposals(c,account_id=W.ACCOUNT,agent=self.agent,limit=10)
        if not rid:raise ValueError('RECORD_ID_REQUIRED')
        if name=='recommendation':
            rec=await c.fetchrow('SELECT * FROM paper_recommendations WHERE account_id=$1 AND recommendation_id=$2',W.ACCOUNT,rid)
            if not rec:raise ValueError('RECOMMENDATION_NOT_IN_ACCOUNT')
            return dict(rec)
        if name=='position':
            exists=await c.fetchval('SELECT 1 FROM paper_orders WHERE account_id=$1 AND group_id=$2 LIMIT 1',W.ACCOUNT,rid)
            if not exists:raise ValueError('POSITION_NOT_IN_ACCOUNT')
            # Bounded detail instead of the unlimited historical chain renderer.
            orders=[dict(x) for x in await c.fetch('SELECT order_id,direction,role,qty,filled_qty,limit_price,state,created_at FROM paper_orders WHERE account_id=$1 AND group_id=$2 ORDER BY created_at DESC LIMIT 30',W.ACCOUNT,rid)]
            reviews=[dict(x) for x in await c.fetch('SELECT review_id,reviewed_at,recommendation,refusal,measure,selection,action FROM paper_xavier_reviews WHERE account_id=$1 AND group_id=$2 ORDER BY reviewed_at DESC LIMIT 5',W.ACCOUNT,rid)]
            settlements=[dict(x) for x in await c.fetch('SELECT settlement_id,settled_at,recorded_at,outcome,payout_per_contract,evidence,evidence_source FROM paper_settlements WHERE account_id=$1 AND group_id=$2 ORDER BY recorded_at DESC LIMIT 5',W.ACCOUNT,rid)]
            return {'group_id':rid,'orders':orders,'reviews':reviews,'settlements':settlements,'bounded_history':True}
        d=await c.fetchrow('SELECT * FROM paper_decisions WHERE account_id=$1 AND decision_id=$2',W.ACCOUNT,rid)
        if not d:raise ValueError('DECISION_NOT_IN_ACCOUNT')
        if name=='decision':return dict(d)
        if name=='rules':
            return {'decision_id':rid,'recorded_at':d['decided_at'],'policy_version':d['policy_version'],'qualification_gaps':d['qualification_gaps'],'qualification':d['policy_decision'],'basis':'RECORDED_DECISION_TERMS_NOT_A_NEW_VENUE_READ'}
        b=await c.fetchrow('SELECT obs_id,us_market_slug,observed_at,venue_ts,source,bids,offers,market_state,error,read_basis FROM paper_book_observations WHERE us_market_slug=$1 AND observed_at<=to_timestamp($2) ORDER BY observed_at DESC LIMIT 1',d['us_market_slug'],self.now)
        if not b:return None
        b=dict(b);age=self.now-b['observed_at'].timestamp();b.update(age_s=age,fresh_for_display=0<=age<=30,execution_authority=False)
        return b


async def context_facts(conn,facts,agent,task_id):
    """A peer's saved review is attributed as opinion, never promoted to fact."""
    async with asyncio.timeout(2):
        t=W.row(await conn.fetchrow("SELECT * FROM agent_tasks WHERE task_id=$1 AND kind=$2 AND assignee=$3 AND spec->>'account_id'=$4",task_id,W.KIND,agent.upper(),W.ACCOUNT))
        if not t:return {'status':'UNAVAILABLE','why':'NO_SCOPED_CAPABILITY_TASK'}
        facts.add('agent_tasks',task_id,'research_objective',t['title'], 'Research objective: '+t['title']+'. Read-only analysis; this task cannot authorize execution or policy changes.')
        for dep in (t['spec'].get('dependencies') or [])[:2]:
            d=W.row(await conn.fetchrow("SELECT * FROM agent_tasks WHERE task_id=$1 AND kind=$2 AND spec->>'account_id'=$3",dep,W.KIND,W.ACCOUNT))
            if d and d['status']=='CLOSED_NO_CHANGE' and d['outcome'].get('reviewed'):
                o=d['outcome']
                facts.add('agent_tasks',dep,'peer_review',o.get('message_id'),'Attributed peer opinion from '+d['assignee']+'; verify independently. Stored message '+str(o.get('message_id'))+': '+str(o.get('answer') or '')[:1800])
        snap=t['outcome'].get('investigation') or []
        for i,item in enumerate(snap[:4]):
            if item.get('status')=='OK':
                content=json.dumps(item,default=str)
                if len(content)<=9000:facts.add('agent_tasks',task_id,'investigation_'+str(i),item,'Stored investigation evidence (check source timestamps): '+content)
        return {'status':'OK','task_id':task_id,'dependencies':t['spec']['dependencies']}


async def active_work_facts(conn,facts,agent):
    """Bounded work memory for ordinary management chat, not just task buttons."""
    async with asyncio.timeout(1):
        rows=await conn.fetch("SELECT task_id,title,status,spec->>'due_at' AS due_at,spec->>'next_action' AS next_action FROM agent_tasks WHERE kind=$1 AND spec->>'account_id'=$2 AND assignee=$3 AND status IN ('OPEN','IN_PROGRESS','WAITING') ORDER BY (spec->>'priority')::integer DESC,created_at LIMIT 5",W.KIND,W.ACCOUNT,agent.upper())
    for r in rows:
        facts.add('agent_tasks',r['task_id'],'assigned_research_work',dict(r),
                  'Assigned research goal (not execution authority): '+r['title']+
                  '; recorded status '+r['status']+'; next action '+str(r['next_action']))
    return {'status':'OK','task_ids':[r['task_id'] for r in rows],'limit':5}
