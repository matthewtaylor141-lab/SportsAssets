import asyncio
import copy
import hashlib
import hmac
import json
import time
import uuid
from unittest.mock import AsyncMock
import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from sportsassets import slack_bridge as S
from sportsassets.api.slack_agents import router
from sportsassets.agents import persona_chat as P
from tests import paper_harness as H

@pytest.fixture
def cfg(monkeypatch):
    for k,v in {'SLACK_TEAM_ID':'T_TEST','SLACK_ALLOWED_CHANNEL_IDS':'C_TEST','SLACK_MANAGEMENT_USER_IDS':'U_TEST','SLACK_WORKROOM_CHANNEL_ID':'','SLACK_DEREK_BOT_TOKEN':'test-token','SLACK_DEREK_SIGNING_SECRET':'test-secret','SLACK_DEREK_APP_ID':'A_TEST'}.items():monkeypatch.setenv(k,v)
    return S.settings('derek')

def payload():return {'team_id':'T_TEST','api_app_id':'A_TEST','event_id':uuid.uuid4().hex,'event':{'type':'app_mention','channel':'C_TEST','user':'U_TEST','text':'Explain the recorded positions','ts':'123.456'}}
def headers(body,secret='test-secret'):
    stamp=str(int(time.time()))
    sig='v0='+hmac.new(secret.encode(),b'v0:'+stamp.encode()+b':'+body,hashlib.sha256).hexdigest()
    return {'x-slack-request-timestamp':stamp,'x-slack-signature':sig}

def test_signature_replay_and_tamper():
    b=b'example';h=headers(b)
    assert S.verify(b,h['x-slack-request-timestamp'],h['x-slack-signature'],'test-secret')
    assert not S.verify(b+b'x',h['x-slack-request-timestamp'],h['x-slack-signature'],'test-secret')
    assert not S.verify(b,h['x-slack-request-timestamp'],h['x-slack-signature'],'test-secret',time.time()+301)

@pytest.mark.parametrize('field,value',[('team_id','WRONG'),('api_app_id','WRONG'),('user','OTHER'),('channel','OTHER'),('type','message'),('bot_id','B_OTHER'),('subtype','bot_message'),('text','x'*4001)])
def test_scope_rejects_unauthorized_or_bot_events(cfg,field,value):
    p=payload();assert S.approved_event(p,cfg)
    (p if field in ('team_id','api_app_id') else p['event'])[field]=value
    assert S.approved_event(p,cfg) is None

@pytest.mark.asyncio
async def test_route_signature_challenge_and_size(cfg):
    app=FastAPI();app.include_router(router)
    b=json.dumps({'type':'url_verification','challenge':'challenge'}).encode()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        assert (await c.post('/api/integrations/slack/derek/events',content=b)).status_code==401
        r=await c.post('/api/integrations/slack/derek/events',content=b,headers=headers(b));assert r.json()=={'challenge':'challenge'}
        assert (await c.post('/api/integrations/slack/derek/events',content=b'x'*65537)).status_code==413

@pytest.fixture
async def database(cfg):
    if not H.DSN:pytest.skip('requires real Postgres')
    pool=await asyncpg.create_pool(H.DSN,min_size=1,max_size=4)
    async with pool.acquire() as c:
        await c.execute('DELETE FROM agent_slack_delivery')
        await c.execute("UPDATE ingestion_state SET value='{\"enabled\":true}' WHERE key=$1",S.CONTROL)
    yield pool
    async with pool.acquire() as c:
        await c.execute('DELETE FROM agent_slack_delivery')
        await c.execute("UPDATE ingestion_state SET value='{\"enabled\":false}' WHERE key=$1",S.CONTROL)
    await pool.close()

async def use(pool,fn):
    async with pool.acquire() as c:return await fn(c)
async def queued(pool,cfg):
    event=S.approved_event(payload(),cfg)
    assert await use(pool,lambda c:S.admit(c,'derek',cfg,event))=='QUEUED'
    return await use(pool,S.claim)

@pytest.mark.asyncio
async def test_concurrent_duplicate_admission_and_claim(database,cfg):
    event=S.approved_event(payload(),cfg)
    replies=await asyncio.gather(*(use(database,lambda c:S.admit(c,'derek',cfg,event)) for _ in range(2)))
    assert sorted(replies)==['DUPLICATE','QUEUED']
    jobs=await asyncio.gather(*(use(database,S.claim) for _ in range(2)))
    assert sum(j is not None for j in jobs)==1

@pytest.mark.asyncio
async def test_genuine_response_sent_once_and_thread_memory(database,cfg,monkeypatch):
    job=await queued(database,cfg)
    converse=AsyncMock(return_value={'status':'ANSWERED','provider':{'mode':'LLM'},'answer':'Recorded <@everyone> evidence','message_id':'m1','conversation_id':'conv1'})
    monkeypatch.setattr(P,'converse',converse);calls=[]
    async def transport(request):
        assert str(request.url)=='https://slack.com/api/chat.postMessage'
        calls.append(json.loads(request.content));return httpx.Response(200,json={'ok':True,'ts':'124.456'})
    cls=httpx.AsyncClient
    monkeypatch.setattr(S.httpx,'AsyncClient',lambda **kw:cls(transport=httpx.MockTransport(transport),**kw))
    await S.process(database,job)
    row=await use(database,lambda c:c.fetchrow('SELECT * FROM agent_slack_delivery'))
    assert row['state']=='SENT' and row['conversation_id']=='conv1'
    assert len(calls)==1 and calls[0]['mrkdwn'] is False and '&lt;@everyone&gt;' in calls[0]['text']
    assert converse.call_args.kwargs['role']=='command'
    assert await use(database,S.claim) is None

@pytest.mark.asyncio
async def test_ambiguous_send_is_not_retried(database,cfg,monkeypatch):
    job=await queued(database,cfg);job['answer']='Recorded answer';job['message_id']='m1'
    async def transport(request):raise httpx.ReadTimeout('ambiguous')
    cls=httpx.AsyncClient;monkeypatch.setattr(S.httpx,'AsyncClient',lambda **kw:cls(transport=httpx.MockTransport(transport),**kw))
    await S.process(database,job)
    assert await use(database,lambda c:c.fetchval('SELECT state FROM agent_slack_delivery'))=='DELIVERY_UNKNOWN'
    assert await use(database,S.claim) is None

@pytest.mark.asyncio
async def test_off_switch_blocks_admission_and_claim(database,cfg):
    await use(database,lambda c:c.execute("UPDATE ingestion_state SET value='{\"enabled\":false}' WHERE key=$1",S.CONTROL))
    assert await use(database,lambda c:S.admit(c,'derek',cfg,S.approved_event(payload(),cfg)))=='OFF'
    assert await use(database,S.claim) is None

@pytest.mark.asyncio
async def test_only_genuine_reviews_publish(cfg,monkeypatch):
    monkeypatch.setenv('SLACK_WORKROOM_CHANNEL_ID','C_TEST')
    c=AsyncMock();c.fetchval.return_value=0
    c.fetch.return_value=[{'event_id':1,'task_id':'task1','outcome':{'reviewed':True,'answer':'Measured research','message_id':'m1'}}]
    await S.publish_reviews(c)
    assert c.fetch.await_count==1
    assert "e.kind='GENUINE_REVIEW'" in c.fetch.call_args.args[0]
    assert c.execute.await_count==1
    assert 'no policy activation' in c.execute.call_args.args[-2]

@pytest.mark.asyncio
async def test_control_refuses_missing_configuration_and_audits_disable(database,cfg):
    with pytest.raises(ValueError,match='THREE_AGENT_CONFIGURATION_REQUIRED'):
        await use(database,lambda c:S.configure(c,True,'Test manager'))
    state=await use(database,lambda c:S.configure(c,False,'Test manager'))
    assert not state['enabled']
    assert await use(database,lambda c:c.fetchval("SELECT count(*) FROM agent_slack_control_audit WHERE actor='Test manager'"))>=1
    assert 'test-token' not in json.dumps(state) and 'test-secret' not in json.dumps(state)

@pytest.mark.asyncio
async def test_command_control_requires_authorization(cfg):
    app=FastAPI();app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        assert (await c.post('/api/command/agents/slack/control',json={'enabled':True,'actor':'Test manager'})).status_code in (401,403)

@pytest.mark.asyncio
async def test_expired_send_never_reenters_queue(database,cfg):
    job=await queued(database,cfg)
    await use(database,lambda c:c.execute("UPDATE agent_slack_delivery SET state='SENDING',lease_until=now()-interval '1 second'"))
    assert await use(database,S.claim) is None
    assert await use(database,lambda c:c.fetchval('SELECT state FROM agent_slack_delivery'))=='DELIVERY_UNKNOWN'

@pytest.mark.asyncio
async def test_review_publisher_query_runs_on_real_schema(database,cfg,monkeypatch):
    monkeypatch.setenv('SLACK_WORKROOM_CHANNEL_ID','C_TEST')
    await use(database,S.publish_reviews)
    rows=await use(database,lambda c:c.fetch('SELECT source_key,answer FROM agent_slack_delivery'))
    assert all(r['source_key'].startswith('review:') and 'Source message:' in r['answer'] for r in rows)


def followup_payload(thread='123.456', text='And what about the second position?'):
    return {'team_id':'T_TEST','api_app_id':'A_TEST','event_id':uuid.uuid4().hex,
            'event':{'type':'message','channel':'C_TEST','channel_type':'channel','user':'U_TEST',
                     'text':text,'ts':'125.000','thread_ts':thread}}

def test_a_thread_reply_without_a_mention_is_a_follow_up_candidate(cfg):
    e=S.approved_event(followup_payload(),cfg)
    assert e and e['followup'] is True and e['thread']=='123.456'
    assert S.approved_event(followup_payload(text='<@A_TEST> again'),cfg) is None   # app_mention handles it
    p=followup_payload();p['event'].pop('thread_ts')
    assert S.approved_event(p,cfg) is None                                          # not in a thread
    p=followup_payload();p['event']['user']='OTHER'
    assert S.approved_event(p,cfg) is None                                          # not a manager

@pytest.mark.asyncio
async def test_only_the_agent_already_in_the_thread_takes_the_follow_up(database,cfg):
    e=S.approved_event(followup_payload(thread='900.001'),cfg)
    assert await use(database,lambda c:S.admit(c,'derek',cfg,e))=='IGNORED_NOT_THIS_AGENTS_THREAD'
    await use(database,lambda c:c.execute("INSERT INTO agent_slack_delivery(delivery_id,agent,team_id,channel_id,thread_ts,source_key,question,state) VALUES('d-prev','derek','T_TEST','C_TEST','900.001','prev','first question','SENT')"))
    e=S.approved_event(followup_payload(thread='900.001'),cfg)
    assert await use(database,lambda c:S.admit(c,'derek',cfg,e))=='QUEUED'

@pytest.mark.asyncio
async def test_an_escalation_can_mention_only_a_listed_manager(database,cfg,monkeypatch):
    monkeypatch.setenv('SLACK_MANAGEMENT_USER_IDS','U_TEST,U0C64BKD2JE')
    await use(database,lambda c:c.execute("INSERT INTO agent_slack_delivery(delivery_id,agent,team_id,channel_id,source_key,answer,state) VALUES('d-esc','derek','T_TEST','C_TEST','escalation:x','{{@U0C64BKD2JE}} {{@U0EVIL99999}} <@everyone> decision needed','READY')"))
    job=await use(database,S.claim)
    calls=[]
    async def transport(request):
        calls.append(json.loads(request.content));return httpx.Response(200,json={'ok':True,'ts':'130.000'})
    cls=httpx.AsyncClient
    monkeypatch.setattr(S.httpx,'AsyncClient',lambda **kw:cls(transport=httpx.MockTransport(transport),**kw))
    await S.process(database,job)
    t=calls[0]['text']
    assert t.startswith('<@U0C64BKD2JE>  &lt;@everyone&gt; decision needed')
    assert 'U0EVIL' not in t

def test_a_pasted_credential_loses_whitespace_and_quotes_but_never_appears(monkeypatch):
    monkeypatch.setenv('SLACK_DEREK_BOT_TOKEN','  "xoxb-1-abc"\n')
    assert S.settings('derek')['token']=='xoxb-1-abc'
    shape=S.token_shape(' "xoxb-1-abc"\n')
    assert shape=={'kind':'xoxb','length':10,'had_whitespace_or_quotes':True}
    assert 'abc' not in json.dumps(shape)
    assert S.token_shape('xapp-1-x')['kind']=='xapp' and S.token_shape('')['kind']=='absent'
    assert S.token_shape('notatoken')['kind']=='other'

@pytest.mark.parametrize('field,value,reason',[('team_id','WRONG','WRONG_TEAM'),('api_app_id','WRONG','WRONG_APP'),('user','OTHER','USER_NOT_A_MANAGER'),('channel','OTHER','CHANNEL_NOT_ALLOWED'),('bot_id','B','BOT_OR_SUBTYPE'),('type','reaction_added','UNSUPPORTED_EVENT_TYPE')])
def test_an_ignored_event_names_its_reason(cfg,field,value,reason):
    p=payload();(p if field in ('team_id','api_app_id') else p['event'])[field]=value
    assert S.classify(p,cfg)==(None,reason)
    assert S.classify(payload(),cfg)[1] is None

@pytest.mark.asyncio
async def test_the_route_records_each_receipt_without_text(cfg):
    app=FastAPI();app.include_router(router);S.RECEIPTS.clear()
    p=payload();p['team_id']='WRONG';b=json.dumps(p).encode()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        r=await c.post('/api/integrations/slack/derek/events',content=b,headers=headers(b))
    assert r.json()['status']=='IGNORED'
    assert S.RECEIPTS[-1]['outcome']=='IGNORED_WRONG_TEAM' and S.RECEIPTS[-1]['type']=='app_mention'
    assert 'Explain' not in json.dumps(S.RECEIPTS)

@pytest.mark.asyncio
async def test_check_tokens_reports_identity_and_kind_never_the_token(cfg,monkeypatch):
    for a in ('xavier','audrey'):monkeypatch.delenv('SLACK_'+a.upper()+'_BOT_TOKEN',raising=False)
    seen=[]
    async def transport(request):
        seen.append(request.url.path);assert request.headers['authorization']=='Bearer test-token'
        return httpx.Response(200,json={'ok':False,'error':'invalid_auth'})
    cls=httpx.AsyncClient;monkeypatch.setattr(S.httpx,'AsyncClient',lambda **kw:cls(transport=httpx.MockTransport(transport),**kw))
    got=await S.check_tokens()
    assert seen==['/api/auth.test']
    assert got['derek']['ok'] is False and got['derek']['error']=='invalid_auth' and got['derek']['token']['kind']=='other'
    assert got['xavier']=={'token':{'kind':'absent','length':0,'had_whitespace_or_quotes':False},'ok':False,'error':'ABSENT'}
    assert 'test-token' not in json.dumps(got)

@pytest.mark.asyncio
async def test_only_answers_slack_refused_before_posting_are_requeued(database,cfg):
    job=await queued(database,cfg)
    await use(database,lambda c:c.execute("UPDATE agent_slack_delivery SET state='FAILED',error_code='invalid_auth',answer='Stored answer',attempts=1"))
    await use(database,lambda c:c.execute("INSERT INTO agent_slack_delivery(delivery_id,agent,team_id,channel_id,source_key,answer,state,error_code) VALUES('u1','derek','T_TEST','C_TEST','update:hourly:x','old summary','FAILED','invalid_auth'),('u2','derek','T_TEST','C_TEST','review:9','review','DELIVERY_UNKNOWN',NULL)"))
    got=await use(database,lambda c:S.requeue_refused(c,'Test manager'))
    assert got['requeued']==1 and got['items'][0]['source']==job['source_key']
    states=dict(await use(database,lambda c:c.fetch('SELECT delivery_id,state FROM agent_slack_delivery')))
    assert states[job['delivery_id']]=='READY' and states['u1']=='FAILED' and states['u2']=='DELIVERY_UNKNOWN'
    again=await use(database,S.claim)
    assert again['delivery_id']==job['delivery_id'] and again['answer']=='Stored answer'
    assert await use(database,lambda c:c.fetchval("SELECT count(*) FROM agent_slack_control_audit WHERE actor LIKE 'requeue_refused:%'"))>=1
