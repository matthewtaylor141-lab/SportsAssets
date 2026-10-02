"""Mandatory release proofs against a freshly migrated real Postgres database.

Requires RN1X_TEST_DSN. Scratch account keys only. No venue/model/Slack calls.
The model response fixture tests commitment, not the provider's correctness.
"""
import asyncio
import json
import time
import uuid
import asyncpg
import pytest
from sportsassets.agents import capability_work as W, capability_scorecards as S
from tests import paper_harness as H

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(not H.DSN,reason='requires RN1X_TEST_DSN and migrations through 189')]


def isolate(monkeypatch):
    account='paper_test_cap_'+uuid.uuid4().hex[:12]
    monkeypatch.setattr(W,'ACCOUNT',account)
    monkeypatch.setattr(W,'CONTROL','agent.capabilities.v1:'+account)


def reply():return {'status':'ANSWERED','provider':{'mode':'LLM'},'message_id':'fixture-message','answer':'Recorded research review [F1].','facts':[{'source':'paper_ledger','record_id':'fixture:1'}]}


async def with_conn(pool,fn):
    async with pool.acquire() as c:return await fn(c)


async def test_concurrent_admission_claim_completion_and_cancel(monkeypatch):
    isolate(monkeypatch);pool=await asyncpg.create_pool(H.DSN,min_size=1,max_size=3)
    now=time.time()
    try:
        await with_conn(pool,lambda c:W.configure(c,enabled=True,hourly_limit=24,actor='Gate reviewer',now=now))
        async def create(c):return await W.create_flow(c,source_key='same-event',title='Investigate recorded evidence',first='DEREK',priority=3,due=now+86400,actor='Gate reviewer',now=now)
        a,b=await asyncio.gather(with_conn(pool,create),with_conn(pool,create))
        assert sorted([a['created'],b['created']])==[False,True] and a['task_ids']==b['task_ids']
        claims=await asyncio.gather(with_conn(pool,lambda c:W.claim(c,now)),with_conn(pool,lambda c:W.claim(c,now)))
        assert sum(x is not None for x in claims)==1
        first=next(x for x in claims if x);assert first['assignee']=='DEREK'
        assert await with_conn(pool,lambda c:W.finish(c,first,reply(),now+1))
        assert not await with_conn(pool,lambda c:W.finish(c,first,reply(),now+2))
        second=await with_conn(pool,lambda c:W.claim(c,now+3));assert second['assignee']=='XAVIER'
        await with_conn(pool,lambda c:W.cancel(c,second['task_id'],'Gate reviewer',now+4))
        assert not await with_conn(pool,lambda c:W.finish(c,second,reply(),now+5))
        assert await with_conn(pool,lambda c:W.claim(c,now+6)) is None
        rows=await with_conn(pool,lambda c:W.tasks(c));assert len(rows)==3
        assert {r['assignee']:r['status'] for r in rows}=={'DEREK':'CLOSED_NO_CHANGE','XAVIER':'CANCELLED','AUDREY':'REJECTED'}
        events=await with_conn(pool,lambda c:S.delivery_events(c))
        assert len(events['events'])==1
        assert not (await with_conn(pool,lambda c:S.delivery_events(c,events['next_cursor'])))['events']
    finally:
        await with_conn(pool,lambda c:W.configure(c,enabled=False,hourly_limit=24,actor='Gate reviewer',now=time.time()))
        await pool.close()


async def test_restart_fencing_retains_evidence_and_durable_hourly_budget(monkeypatch):
    isolate(monkeypatch);pool=await asyncpg.create_pool(H.DSN,min_size=1,max_size=2)
    now=int(time.time()//3600)*3600+100
    try:
        await with_conn(pool,lambda c:W.configure(c,enabled=True,hourly_limit=2,actor='Gate reviewer',now=now))
        await with_conn(pool,lambda c:W.create_flow(c,source_key='recovery',title='Review recovery evidence',first='DEREK',due=now+86400,actor='Gate reviewer',now=now))
        old=await with_conn(pool,lambda c:W.claim(c,now))
        original=[{'status':'OK','tool':'account','read_at':now}]
        await with_conn(pool,lambda c:W.save_investigation(c,old,original,now+1))
        recovered=await with_conn(pool,lambda c:W.claim(c,now+121))
        assert old['task_id']==recovered['task_id'] and old['spec']['claim_token']!=recovered['spec']['claim_token']
        assert not await with_conn(pool,lambda c:W.finish(c,old,reply(),now+122))
        await with_conn(pool,lambda c:W.save_investigation(c,recovered,[{'read_at':now+121}],now+122))
        assert await with_conn(pool,lambda c:W.finish(c,recovered,reply(),now+123))
        rows=await with_conn(pool,lambda c:W.tasks(c,include_investigation=True))
        assert next(r for r in rows if r['task_id']==old['task_id'])['outcome']['investigation']==original
        assert await with_conn(pool,lambda c:W.claim(c,now+124)) is None
        # A different connection/process reads the same exhausted hourly budget.
        assert (await with_conn(pool,lambda c:W.control(c)))['spent']==2
    finally:
        for t in await with_conn(pool,lambda c:W.tasks(c)):
            await with_conn(pool,lambda c:W.cancel(c,t['task_id'],'Gate reviewer',time.time()))
        await with_conn(pool,lambda c:W.configure(c,enabled=False,hourly_limit=2,actor='Gate reviewer',now=time.time()))
        await pool.close()
