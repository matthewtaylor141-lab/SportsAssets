"""rc6.2 agent-truth: the capability claim is a CONSTANT number of round trips.

A DEFENSIVE bound. The claim issued one dependency query per candidate (up
to 300) and one UPDATE + event INSERT per rejection inside the tick's 5 s
CLAIM budget, so a large not-yet-ready queue could need hundreds of round
trips. These tests build that synthetic shape on a real Postgres database:
180 queued tasks, none ready, each round trip delayed 30 ms. On the base
implementation the claim needs 185 round trips (~5.6 s) and times out; it
must now finish in a bounded handful, with the same decisions.

This is NOT the cause of production's agents.capability_runtime errors
('CLAIM: TimeoutError' recorded with 0 open tasks, last error now
'HEARTBEAT: TimeoutError') nor of the mass REJECTED (REVIEW_INCOMPLETE in
finish() spending MAX_ATTEMPTS, then DEPENDENCY_FAILED); see claim().

Requires RN1X_TEST_DSN. Scratch account keys only.
"""
import asyncio
import json
import time
import uuid
import asyncpg
import pytest
from sportsassets.agents import capability_work as W
from tests import paper_harness as H

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(not H.DSN,reason='requires RN1X_TEST_DSN')]

DELAY_S=0.03
CLAIM_BUDGET_S=5  # capability_runtime.tick's CLAIM timeout


class SlowConn:
    """Each statement costs DELAY_S before it reaches the server."""

    def __init__(self,conn):
        self.conn=conn;self.calls=0

    def transaction(self):
        return self.conn.transaction()

    async def _rt(self,name,*a):
        self.calls+=1
        await asyncio.sleep(DELAY_S)
        return await getattr(self.conn,name)(*a)

    async def fetch(self,*a):return await self._rt('fetch',*a)
    async def fetchrow(self,*a):return await self._rt('fetchrow',*a)
    async def fetchval(self,*a):return await self._rt('fetchval',*a)
    async def execute(self,*a):return await self._rt('execute',*a)


def isolate(monkeypatch):
    account='paper_test_capclaim_'+uuid.uuid4().hex[:12]
    monkeypatch.setattr(W,'ACCOUNT',account)
    monkeypatch.setattr(W,'CONTROL','agent.capabilities.v1:'+account)


async def _queue(conn,now,flows,priority=3):
    firsts=[]
    for i in range(flows):
        got=await W.create_flow(conn,source_key='claim-shape-%d-%d'%(priority,i),title='Investigate recorded evidence',first='DEREK',priority=priority,due=now+86400,actor='Gate reviewer',now=now)
        firsts.append(got['task_ids'])
    return firsts


async def _set_spec(conn,task_id,status,**fields):
    spec=W.obj(await conn.fetchval('SELECT spec FROM agent_tasks WHERE task_id=$1',task_id))
    spec.update(fields)
    await conn.execute('UPDATE agent_tasks SET status=$2,spec=$3::jsonb WHERE task_id=$1',task_id,status,json.dumps(spec))


async def test_large_unready_queue_claims_within_the_tick_budget(monkeypatch):
    isolate(monkeypatch)
    conn=await asyncpg.connect(H.DSN)
    now=time.time()
    try:
        await W.configure(conn,enabled=True,hourly_limit=120,actor='Gate reviewer',now=now)
        flows=await _queue(conn,now,60)
        # Every DEREK task waits on a pending provider request (finish's
        # retry_after lease); XAVIER and AUDREY wait on their DEREK.
        for ids in flows:
            await _set_spec(conn,ids[0],'WAITING',lease_until=now+65)
        # Large recorded investigations on every row (the outcome column).
        await conn.execute("UPDATE agent_tasks SET outcome=jsonb_build_object('investigation',jsonb_build_array(repeat('x',20000))) WHERE spec->>'account_id'=$1",W.ACCOUNT)
        slow=SlowConn(conn)
        async with asyncio.timeout(CLAIM_BUDGET_S):
            got=await W.claim(slow,now+1)
        assert got is None
        assert slow.calls<=8, slow.calls
        # One becomes ready: claimed in the same bounded pass, without outcome.
        await _set_spec(conn,flows[7][0],'WAITING',lease_until=0)
        slow=SlowConn(conn)
        async with asyncio.timeout(CLAIM_BUDGET_S):
            got=await W.claim(slow,now+2)
        assert got is not None and got['task_id']==flows[7][0] and got['assignee']=='DEREK'
        assert 'outcome' not in got and got['spec']['attempts']==1 and got['spec']['claim_token']
        assert slow.calls<=12, slow.calls
        row=await conn.fetchrow('SELECT status,spec FROM agent_tasks WHERE task_id=$1',got['task_id'])
        assert row['status']=='IN_PROGRESS' and W.obj(row['spec'])['claim_token']==got['spec']['claim_token']
    finally:
        await W.configure(conn,enabled=False,hourly_limit=24,actor='Gate reviewer',now=time.time())
        await conn.close()


async def test_bulk_rejections_are_recorded_and_cascade_in_one_pass(monkeypatch):
    isolate(monkeypatch)
    conn=await asyncpg.connect(H.DSN)
    now=time.time()
    try:
        await W.configure(conn,enabled=True,hourly_limit=120,actor='Gate reviewer',now=now)
        flows=await _queue(conn,now,40)
        for ids in flows:
            # Retry budget spent and lease expired: each DEREK is rejected,
            # then its XAVIER and AUDREY fail on the rejected dependency.
            await _set_spec(conn,ids[0],'WAITING',attempts=W.MAX_ATTEMPTS,lease_until=0)
        slow=SlowConn(conn)
        async with asyncio.timeout(CLAIM_BUDGET_S):
            got=await W.claim(slow,now+1)
        assert got is None and slow.calls<=8, slow.calls
        # A dependent ordered before its DEREK sees it only on the next pass
        # (as the per-row reads did); a second pass finishes the cascade.
        async with asyncio.timeout(CLAIM_BUDGET_S):
            assert await W.claim(SlowConn(conn),now+2) is None
        rows=await conn.fetch("SELECT task_id,status FROM agent_tasks WHERE kind=$2 AND spec->>'account_id'=$1",W.ACCOUNT,W.KIND)
        assert len(rows)==120 and {r['status'] for r in rows}=={'REJECTED'}
        ev=await conn.fetch("SELECT e.task_id,e.kind,e.actor,e.detail FROM agent_task_events e JOIN agent_tasks t USING(task_id) WHERE t.spec->>'account_id'=$1 AND e.kind IN ('RETRY_BUDGET_EXHAUSTED','DEPENDENCY_FAILED')",W.ACCOUNT)
        kinds={}
        for e in ev:kinds.setdefault(e['task_id'],[]).append(e['kind'])
        assert all(len(v)==1 for v in kinds.values()) and len(kinds)==120
        assert sum(v==['RETRY_BUDGET_EXHAUSTED'] for v in kinds.values())==40
        dep=[W.obj(e['detail']) for e in ev if e['kind']=='DEPENDENCY_FAILED']
        assert len(dep)==80 and all('REJECTED' in d['dependencies'].values() for d in dep)
        assert {e['actor'] for e in ev}=={'SYSTEM'}
    finally:
        await W.configure(conn,enabled=False,hourly_limit=24,actor='Gate reviewer',now=time.time())
        await conn.close()
