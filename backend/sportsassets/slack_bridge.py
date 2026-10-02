"""Signed, bounded Slack transport. No trading or policy-activation authority.
Inbound mentions are answered by the existing read-only persona service.
Peer-review posts publish genuine recorded reviews, never fabricate dialogues.
An ambiguous send becomes DELIVERY_UNKNOWN; it is never blindly retried.
"""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
import httpx

CONTROL='agent.slack.bridge'
AGENTS=('derek','xavier','audrey')
QUEUE_CAP=300

def settings(agent):
 if agent not in AGENTS:raise ValueError('UNKNOWN_AGENT')
 prefix='SLACK_'+agent.upper()+'_'
 return {'secret':os.getenv(prefix+'SIGNING_SECRET',''),'token':os.getenv(prefix+'BOT_TOKEN',''),
         'app':os.getenv(prefix+'APP_ID',''),'team':os.getenv('SLACK_TEAM_ID',''),
         'channels':{x.strip() for x in os.getenv('SLACK_ALLOWED_CHANNEL_IDS','').split(',') if x.strip()},
         'managers':{x.strip() for x in os.getenv('SLACK_MANAGEMENT_USER_IDS','').split(',') if x.strip()},
         'workroom':os.getenv('SLACK_WORKROOM_CHANNEL_ID','')}

def verify(body,timestamp,signature,secret,now=None):
 try:
  stamp=int(timestamp);now=time.time() if now is None else now
  if not secret or abs(now-stamp)>300:return False
  expected='v0='+hmac.new(secret.encode(),b'v0:'+str(stamp).encode()+b':'+body,hashlib.sha256).hexdigest()
  return hmac.compare_digest(expected,signature or '')
 except (ValueError,TypeError):return False

def approved_event(payload,cfg):
 if payload.get('team_id')!=cfg['team'] or not cfg['team'] or payload.get('api_app_id')!=cfg['app'] or not cfg['app']:return None
 e=payload.get('event') or {}
 if not isinstance(e,dict) or e.get('type')!='app_mention' or e.get('bot_id') or e.get('subtype'):return None
 if e.get('channel') not in cfg['channels'] or e.get('user') not in cfg['managers']:return None
 if not isinstance(e.get('text'),str) or not 1<=len(e['text'])<=4000:return None
 if not isinstance(payload.get('event_id'),str) or len(payload['event_id'])>150:return None
 ts=e.get('thread_ts') or e.get('ts')
 if not isinstance(ts,str) or len(ts)>40:return None
 return {'source':payload['event_id'],'channel':e['channel'],'thread':ts,'text':e['text']}

def decode(v):return json.loads(v) if isinstance(v,str) else v or {}

def delivery_id(agent,team,source):
 return 'slack:'+hashlib.sha256(json.dumps([agent,team,source]).encode()).hexdigest()

async def admit(conn,agent,cfg,event):
 async with conn.transaction():
  control=decode(await conn.fetchval('SELECT value FROM ingestion_state WHERE key=$1 FOR UPDATE',CONTROL))
  if control.get('enabled') is not True:return 'OFF'
  did=delivery_id(agent,cfg['team'],event['source'])
  if await conn.fetchval('SELECT 1 FROM agent_slack_delivery WHERE delivery_id=$1',did):return 'DUPLICATE'
  n=await conn.fetchval("SELECT count(*) FROM agent_slack_delivery WHERE state IN ('QUEUED','WORKING','READY','SENDING')")
  if n>=QUEUE_CAP:return 'QUEUE_FULL'
  await conn.execute('INSERT INTO agent_slack_delivery(delivery_id,agent,team_id,channel_id,thread_ts,source_key,question) VALUES($1,$2,$3,$4,$5,$6,$7)',did,agent,cfg['team'],event['channel'],event['thread'],event['source'],event['text'])
 return 'QUEUED'

async def publish_reviews(conn):
 """Current genuine reviews only, with explicit provenance; no backfill flood."""
 for agent in AGENTS:
  if await conn.fetchval("SELECT count(*) FROM agent_slack_delivery WHERE state IN ('QUEUED','WORKING','READY','SENDING')")>=QUEUE_CAP-3:return
  cfg=settings(agent)
  if not cfg['token'] or not cfg['team'] or cfg['workroom'] not in cfg['channels']:continue
  rows=await conn.fetch("SELECT e.event_id,e.task_id,t.outcome FROM agent_task_events e JOIN agent_tasks t USING(task_id) WHERE e.kind='GENUINE_REVIEW' AND e.actor=$1 AND e.at>now()-interval '1 hour' AND t.kind='AGENT_CAPABILITY_REVIEW_V1' AND t.spec->>'account_id'='paper_acct_main' ORDER BY e.event_id DESC LIMIT 3",agent.upper())
  for r in rows:
   outcome=decode(r['outcome'])
   if outcome.get('reviewed') is not True or not outcome.get('message_id'):continue
   source='review:'+str(r['event_id']);answer=str(outcome.get('answer') or '')[:3000]
   text='Recorded '+agent.title()+' research review · '+r['task_id']+'\n'+answer+'\nSource message: '+str(outcome['message_id'])+'\nResearch opinion; no policy activation or profitability claim.'
   await conn.execute("INSERT INTO agent_slack_delivery(delivery_id,agent,team_id,channel_id,source_key,answer,message_id,state) VALUES($1,$2,$3,$4,$5,$6,$7,'READY') ON CONFLICT DO NOTHING",delivery_id(agent,cfg['team'],source),agent,cfg['team'],cfg['workroom'],source,text,outcome['message_id'])

async def claim(conn):
 async with conn.transaction():
  control=decode(await conn.fetchval('SELECT value FROM ingestion_state WHERE key=$1 FOR UPDATE',CONTROL))
  if control.get('enabled') is not True:return None
  await conn.execute("UPDATE agent_slack_delivery SET state='DELIVERY_UNKNOWN',error_code='SEND_LEASE_EXPIRED',updated_at=now() WHERE state='SENDING' AND lease_until<now()")
  if await conn.fetchval("SELECT 1 FROM agent_slack_delivery WHERE state IN ('WORKING','SENDING') AND lease_until>now() LIMIT 1"):return None
  await conn.execute("UPDATE agent_slack_delivery SET state='FAILED',error_code='ATTEMPT_LIMIT',updated_at=now() WHERE state='WORKING' AND lease_until<now() AND attempts>=3")
  await publish_reviews(conn)
  row=await conn.fetchrow("SELECT * FROM agent_slack_delivery WHERE (state IN ('QUEUED','READY') OR (state='WORKING' AND lease_until<now())) AND attempts<3 ORDER BY created_at,delivery_id LIMIT 1 FOR UPDATE SKIP LOCKED")
  if not row:return None
  token=uuid.uuid4().hex
  await conn.execute("UPDATE agent_slack_delivery SET state='WORKING',claim_token=$2,attempts=attempts+1,lease_until=now()+interval '120 seconds',updated_at=now() WHERE delivery_id=$1",row['delivery_id'],token)
  return dict(row,claim_token=token)

async def update(conn,job,state,*,answer=None,message_id=None,slack_ts=None,error=None):
 return await conn.execute("UPDATE agent_slack_delivery SET state=$3,answer=coalesce($4,answer),message_id=coalesce($5,message_id),slack_ts=$6,error_code=$7,updated_at=now() WHERE delivery_id=$1 AND claim_token=$2 AND lease_until>now()",job['delivery_id'],job['claim_token'],state,answer,message_id,slack_ts,error)

async def process(pool,job):
 cfg=settings(job['agent'])
 if not cfg['token'] or job['team_id']!=cfg['team'] or job['channel_id'] not in cfg['channels']:
  async with pool.acquire() as c:await update(c,job,'FAILED',error='CONFIGURATION_NOT_AUTHORIZED')
  return
 answer=job['answer'];message_id=job['message_id']
 if not answer:
  from .agents import persona_chat as P
  async with asyncio.timeout(60):
   async with pool.acquire() as c:
    cid=await c.fetchval("SELECT conversation_id FROM agent_slack_delivery WHERE agent=$1 AND team_id=$2 AND channel_id=$3 AND thread_ts=$4 AND conversation_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",job['agent'],job['team_id'],job['channel_id'],job['thread_ts'])
   reply=await P.converse(pool,agent=job['agent'],role='command',message=job['question'],conversation_id=cid,request_id=job['delivery_id'],allow_records_only=False,now=time.time())
  if reply.get('status')=='PENDING':
   async with pool.acquire() as c:await update(c,job,'FAILED',error='PERSONA_PENDING_REQUIRES_REVIEW')
   return
  if reply.get('provider',{}).get('mode')!='LLM' or not reply.get('answer'):
   async with pool.acquire() as c:await update(c,job,'FAILED',error='NO_GENUINE_PERSONA_RESPONSE')
   return
  answer=reply['answer'];message_id=reply.get('message_id')
  async with pool.acquire() as c:
   await c.execute("UPDATE agent_slack_delivery SET conversation_id=$3 WHERE delivery_id=$1 AND claim_token=$2 AND lease_until>now()",job['delivery_id'],job['claim_token'],reply.get('conversation_id'))
 # Persist the exact reply before attempting the one outbound delivery.
 async with pool.acquire() as c:
  control=decode(await c.fetchval('SELECT value FROM ingestion_state WHERE key=$1',CONTROL))
  if control.get('enabled') is not True:
   await update(c,job,'READY',answer=answer,message_id=message_id);return
  changed=await update(c,job,'SENDING',answer=answer,message_id=message_id)
 if changed!='UPDATE 1':return
 text=answer[:3500]+ ('\nRecorded response: '+str(message_id) if message_id and not job['answer'] else '')
 # Plain text prevents an answer from issuing Slack mentions or link unfurls.
 text=text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
 payload={'channel':job['channel_id'],'text':text,'mrkdwn':False,'unfurl_links':False,'unfurl_media':False}
 if job['thread_ts']:payload['thread_ts']=job['thread_ts']
 state='DELIVERY_UNKNOWN';error=None;ts=None
 try:
  async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
   r=await client.post('https://slack.com/api/chat.postMessage',headers={'Authorization':'Bearer '+cfg['token']},json=payload)
   if r.status_code==200:
    result=r.json()
    if result.get('ok') is True and result.get('ts'):state='SENT';ts=result['ts']
    elif result.get('ok') is False:state='FAILED';error=str(result.get('error','SLACK_REFUSED'))[:100]
   else:error='HTTP_'+str(r.status_code)
 except Exception as exc:error=type(exc).__name__
 async with pool.acquire() as c:await update(c,job,state,slack_ts=ts,error=error)

async def run(get_pool):
 while True:
  try:
   # Default off without a configured workspace; no provider call on boot.
   if os.getenv('SLACK_TEAM_ID'):
    async with asyncio.timeout(5):
     pool=await get_pool()
     async with pool.acquire() as c:job=await claim(c)
    if job:
     try:
      async with asyncio.timeout(90):await process(pool,job)
     except Exception as exc:
      async with asyncio.timeout(3):
       async with pool.acquire() as c:
        # Never turn an ambiguous in-flight send back into a retry.
        await c.execute("UPDATE agent_slack_delivery SET state=CASE WHEN state='SENDING' THEN 'DELIVERY_UNKNOWN' ELSE 'FAILED' END,error_code=$3,updated_at=now() WHERE delivery_id=$1 AND claim_token=$2 AND state NOT IN ('SENT','DELIVERY_UNKNOWN')",job['delivery_id'],job['claim_token'],type(exc).__name__)
  except asyncio.CancelledError:raise
  except Exception:pass  # Durable queue remains; no credentials/body are logged.
  await asyncio.sleep(3)

async def status(conn):
 control=decode(await conn.fetchval('SELECT value FROM ingestion_state WHERE key=$1',CONTROL))
 counts=await conn.fetch('SELECT state,count(*) AS n FROM agent_slack_delivery GROUP BY state')
 return {'enabled':control.get('enabled') is True,'actor':control.get('actor'),
         'delivery_counts':{r['state']:r['n'] for r in counts},
         'agents':{a:{'configured':all(settings(a)[k] for k in ('token','secret','app','team','channels','managers'))} for a in AGENTS},
         'authority':'READ_ONLY_PERSONA_AND_RECORDED_RESEARCH','ambiguous_delivery':'MANUAL_RECONCILIATION_REQUIRED'}

async def configure(conn,enabled,actor):
 if not isinstance(enabled,bool):raise ValueError('BOOLEAN_ENABLED_REQUIRED')
 if not isinstance(actor,str) or not 2<=len(actor.strip())<=100:raise ValueError('NAMED_MANAGER_REQUIRED')
 if enabled and not all(all(settings(a)[k] for k in ('token','secret','app','team','channels','managers')) for a in AGENTS):raise ValueError('THREE_AGENT_CONFIGURATION_REQUIRED')
 async with conn.transaction():
  await conn.fetchval('SELECT value FROM ingestion_state WHERE key=$1 FOR UPDATE',CONTROL)
  await conn.execute('UPDATE ingestion_state SET value=$2::jsonb WHERE key=$1',CONTROL,json.dumps({'enabled':enabled,'actor':actor,'changed_at':time.time()}))
  await conn.execute('INSERT INTO agent_slack_control_audit(enabled,actor) VALUES($1,$2)',enabled,actor)
 return await status(conn)
