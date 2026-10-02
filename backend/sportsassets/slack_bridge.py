"""Signed, bounded Slack transport. No trading or policy-activation authority.
Inbound mentions are answered by the existing read-only persona service.
Peer-review posts publish genuine recorded reviews, never fabricate dialogues.
An ambiguous send becomes DELIVERY_UNKNOWN; it is never blindly retried.
"""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import re
import json
import os
import time
import uuid
import httpx

CONTROL='agent.slack.bridge'
AGENTS=('derek','xavier','audrey')
QUEUE_CAP=300

def _clean(v):
 # A pasted credential sometimes carries a trailing newline, spaces or
 # surrounding quotes; none of those is ever part of a Slack value.
 return (v or '').strip().strip('"\'').strip()

def settings(agent):
 if agent not in AGENTS:raise ValueError('UNKNOWN_AGENT')
 prefix='SLACK_'+agent.upper()+'_'
 return {'secret':_clean(os.getenv(prefix+'SIGNING_SECRET')),'token':_clean(os.getenv(prefix+'BOT_TOKEN')),
         'app':_clean(os.getenv(prefix+'APP_ID')),'team':_clean(os.getenv('SLACK_TEAM_ID')),
         'channels':{x.strip() for x in os.getenv('SLACK_ALLOWED_CHANNEL_IDS','').split(',') if x.strip()},
         'managers':{x.strip() for x in os.getenv('SLACK_MANAGEMENT_USER_IDS','').split(',') if x.strip()},
         'workroom':_clean(os.getenv('SLACK_WORKROOM_CHANNEL_ID'))}

def token_shape(raw):
 """What KIND of value is stored, never the value: its Slack prefix class,
 its length and whether it carried whitespace or quotes."""
 v=_clean(raw)
 kind=next((k for k in ('xoxb','xoxp','xapp','xoxe') if v.startswith(k+'-') or v.startswith(k+'.')),'other' if v else 'absent')
 return {'kind':kind,'length':len(v),'had_whitespace_or_quotes':(raw or '')!=v}

# Inbound receipt trace: the outcome of each signed request, in memory only
# (no text, no credential). Survives nothing; it answers "did Slack reach us
# and why was it accepted or ignored" for the last requests.
RECEIPTS=[]
def note_receipt(agent,event_id,kind,outcome):
 RECEIPTS.append({'at':round(time.time(),3),'agent':agent,'event_id':str(event_id or '')[:40],'type':str(kind or '')[:30],'outcome':outcome})
 del RECEIPTS[:-50]

def verify(body,timestamp,signature,secret,now=None):
 try:
  stamp=int(timestamp);now=time.time() if now is None else now
  if not secret or abs(now-stamp)>300:return False
  expected='v0='+hmac.new(secret.encode(),b'v0:'+str(stamp).encode()+b':'+body,hashlib.sha256).hexdigest()
  return hmac.compare_digest(expected,signature or '')
 except (ValueError,TypeError):return False

def classify(payload,cfg):
 """(event, None) for an authorized manager message, else (None, reason)."""
 if not cfg['team'] or payload.get('team_id')!=cfg['team']:return None,'WRONG_TEAM'
 if not cfg['app'] or payload.get('api_app_id')!=cfg['app']:return None,'WRONG_APP'
 e=payload.get('event') or {}
 if not isinstance(e,dict):return None,'NO_EVENT'
 if e.get('bot_id') or e.get('subtype'):return None,'BOT_OR_SUBTYPE'
 followup=False
 if e.get('type')=='message':
  # A manager's reply INSIDE a thread, without an @mention (a mention
  # arrives as its own app_mention event, so it is skipped here). Only the
  # agent that already answered in that thread takes it (admit checks).
  if e.get('channel_type') not in (None,'channel','group'):return None,'NOT_A_CHANNEL'
  if '<@' in str(e.get('text') or ''):return None,'MENTION_HANDLED_AS_APP_MENTION'
  if not e.get('thread_ts') or e.get('thread_ts')==e.get('ts'):return None,'NOT_A_THREAD_REPLY'
  followup=True
 elif e.get('type')!='app_mention':return None,'UNSUPPORTED_EVENT_TYPE'
 if e.get('channel') not in cfg['channels']:return None,'CHANNEL_NOT_ALLOWED'
 if e.get('user') not in cfg['managers']:return None,'USER_NOT_A_MANAGER'
 if not isinstance(e.get('text'),str) or not 1<=len(e['text'])<=4000:return None,'TEXT_SIZE'
 if not isinstance(payload.get('event_id'),str) or len(payload['event_id'])>150:return None,'EVENT_ID'
 ts=e.get('thread_ts') or e.get('ts')
 if not isinstance(ts,str) or len(ts)>40:return None,'TS'
 return {'source':payload['event_id'],'channel':e['channel'],'thread':ts,'text':e['text'],'user':e['user'],'followup':followup},None

def approved_event(payload,cfg):
 return classify(payload,cfg)[0]

def decode(v):return json.loads(v) if isinstance(v,str) else v or {}

def delivery_id(agent,team,source):
 return 'slack:'+hashlib.sha256(json.dumps([agent,team,source]).encode()).hexdigest()

async def admit(conn,agent,cfg,event):
 async with conn.transaction():
  control=decode(await conn.fetchval('SELECT value FROM ingestion_state WHERE key=$1 FOR UPDATE',CONTROL))
  if control.get('enabled') is not True:return 'OFF'
  did=delivery_id(agent,cfg['team'],event['source'])
  if await conn.fetchval('SELECT 1 FROM agent_slack_delivery WHERE delivery_id=$1',did):return 'DUPLICATE'
  if event.get('followup') and not await conn.fetchval(
     "SELECT 1 FROM agent_slack_delivery WHERE agent=$1 AND team_id=$2 AND channel_id=$3 AND thread_ts=$4 AND state='SENT' LIMIT 1",
     agent,cfg['team'],event['channel'],event['thread']):return 'IGNORED_NOT_THIS_AGENTS_THREAD'
  n=await conn.fetchval("SELECT count(*) FROM agent_slack_delivery WHERE state IN ('QUEUED','WORKING','READY','SENDING')")
  if n>=QUEUE_CAP:return 'QUEUE_FULL'
  await conn.execute('INSERT INTO agent_slack_delivery(delivery_id,agent,team_id,channel_id,thread_ts,source_key,question,requested_by) VALUES($1,$2,$3,$4,$5,$6,$7,$8)',did,agent,cfg['team'],event['channel'],event['thread'],event['source'],event['text'],event.get('user'))
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
   text='Recorded '+agent.title()+' research review · '+r['task_id']+'\n'+answer+'\nSource message: '+str(outcome['message_id'])+' · record: https://command.bettortoken.com/'+agent+'\nStage: review of a hypothesis · research opinion; no policy activation or profitability claim.'
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

ASSIGN=re.compile(r'^\s*(?:assign|research)\s*[:\-]\s*(.{8,480})$',re.I|re.S)

async def assignment(pool,job):
 """`@Agent assign: <question>` from a verified manager creates one durable
 research flow (capability_work.create_flow: this agent investigates, the
 next reviews, Audrey audits) and answers with its task ids. Research only:
 no order, policy or control is reachable from here."""
 text=re.sub(r'<@[A-Z0-9]+>','',str(job.get('question') or '')).strip()
 m=ASSIGN.match(text)
 if not m:return None
 from .agents import capability_work as W
 now=time.time()
 async with pool.acquire() as c:
  got=await W.create_flow(c,source_key='slack:'+job['delivery_id'],title=m.group(1).strip()[:480],first=job['agent'].upper(),priority=3,due=now+86400,actor='slack:'+str(job.get('requested_by') or 'manager'),now=now)
 ids=got['task_ids']
 return ('Research assigned · '+' → '.join(ids)+'\nOrder: '+job['agent'].title()+' investigates, the next agent reviews, Audrey audits. '
         'Stage: hypothesis under investigation; nothing is approved or activated.\nRecord: https://command.bettortoken.com/'+job['agent'])

async def process(pool,job):
 cfg=settings(job['agent'])
 if not cfg['token'] or job['team_id']!=cfg['team'] or job['channel_id'] not in cfg['channels']:
  async with pool.acquire() as c:await update(c,job,'FAILED',error='CONFIGURATION_NOT_AUTHORIZED')
  return
 answer=job['answer'];message_id=job['message_id']
 if not answer:
  assigned=await assignment(pool,job)
  if assigned:answer,message_id=assigned,None
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
 # Management escalations name a listed manager with {{@U...}}; that is the
 # ONLY mention a delivery can carry, and only for the verified managers.
 text=re.sub(r'\{\{@(U[A-Z0-9]{6,15})\}\}',lambda m:('<@'+m.group(1)+'>') if m.group(1) in cfg['managers'] else '',text)
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
    pool=await get_pool()
    try:
     # management updates on their own connection, outside the claim lock:
     # an update read can never delay an incoming mention
     async with asyncio.timeout(30):
      async with pool.acquire() as c:
       from . import slack_updates as U
       await U.queue(c)
    except asyncio.CancelledError:raise
    except Exception:pass
    async with asyncio.timeout(5):
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
         'errors':{r['error_code']:r['n'] for r in await conn.fetch("SELECT error_code,count(*) AS n FROM agent_slack_delivery WHERE error_code IS NOT NULL AND updated_at>now()-interval '6 hours' GROUP BY 1")},
         'receipts':RECEIPTS[-20:],
         'authority':'READ_ONLY_PERSONA_AND_RECORDED_RESEARCH','ambiguous_delivery':'MANUAL_RECONCILIATION_REQUIRED'}

async def check_tokens():
 """Slack's auth.test per agent: ok/error, the bot user and team it
 resolves to, and the stored value's KIND. The token itself is never
 returned. A read: it posts nothing."""
 out={}
 async with httpx.AsyncClient(timeout=10,follow_redirects=False) as client:
  for a in AGENTS:
   cfg=settings(a);shape=token_shape(os.getenv('SLACK_'+a.upper()+'_BOT_TOKEN'))
   row={'token':shape}
   if not cfg['token']:out[a]=dict(row,ok=False,error='ABSENT');continue
   try:
    r=await client.post('https://slack.com/api/auth.test',headers={'Authorization':'Bearer '+cfg['token']})
    j=r.json() if r.status_code==200 else {}
    row.update(ok=j.get('ok') is True,error=None if j.get('ok') else str(j.get('error') or 'HTTP_'+str(r.status_code))[:60],
               bot_user_id=j.get('user_id'),team_id=j.get('team_id'),team_matches=bool(j.get('team_id')) and j.get('team_id')==cfg['team'],
               bot_id=j.get('bot_id'))
   except Exception as exc:row.update(ok=False,error=type(exc).__name__)
   out[a]=row
 return out

# Slack answered ok:false for these BEFORE posting anything: re-sending the
# stored answer cannot duplicate a message. Only answers to a manager's
# question and recorded reviews, from the last 6 hours, and only on an
# explicit control request.
REFUSED_BEFORE_POST=('invalid_auth','not_authed','token_revoked','token_expired','account_inactive','not_in_channel','channel_not_found','missing_scope')

async def requeue_refused(conn,actor):
 if not isinstance(actor,str) or not 2<=len(actor.strip())<=100:raise ValueError('NAMED_MANAGER_REQUIRED')
 rows=await conn.fetch("UPDATE agent_slack_delivery SET state='READY',attempts=0,error_code=NULL,claim_token=NULL,lease_until=NULL,updated_at=now() WHERE state='FAILED' AND error_code=ANY($1::text[]) AND answer IS NOT NULL AND (question IS NOT NULL OR source_key LIKE 'review:%') AND created_at>now()-interval '6 hours' RETURNING delivery_id,agent,source_key",list(REFUSED_BEFORE_POST))
 await conn.execute('INSERT INTO agent_slack_control_audit(enabled,actor) VALUES((SELECT (value->>\'enabled\')::boolean FROM ingestion_state WHERE key=$1),$2)',CONTROL,('requeue_refused:'+actor.strip())[:100])
 return {'requeued':len(rows),'items':[{'agent':r['agent'],'source':r['source_key']} for r in rows]}

async def configure(conn,enabled,actor):
 if not isinstance(enabled,bool):raise ValueError('BOOLEAN_ENABLED_REQUIRED')
 if not isinstance(actor,str) or not 2<=len(actor.strip())<=100:raise ValueError('NAMED_MANAGER_REQUIRED')
 if enabled and not all(all(settings(a)[k] for k in ('token','secret','app','team','channels','managers')) for a in AGENTS):raise ValueError('THREE_AGENT_CONFIGURATION_REQUIRED')
 async with conn.transaction():
  await conn.fetchval('SELECT value FROM ingestion_state WHERE key=$1 FOR UPDATE',CONTROL)
  await conn.execute('UPDATE ingestion_state SET value=$2::jsonb WHERE key=$1',CONTROL,json.dumps({'enabled':enabled,'actor':actor,'changed_at':time.time()}))
  await conn.execute('INSERT INTO agent_slack_control_audit(enabled,actor) VALUES($1,$2)',enabled,actor)
 return await status(conn)
