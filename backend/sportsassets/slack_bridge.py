"""Signed, bounded Slack transport. No trading or policy-activation authority.
Inbound mentions are answered by the existing read-only persona service.
Peer-review posts publish genuine recorded reviews, never fabricate dialogues.
An ambiguous send becomes DELIVERY_UNKNOWN; it is never blindly retried.

KAREN (red team, migration 207) is a fourth bot identity with her OWN Slack
app: SLACK_KAREN_BOT_TOKEN / SLACK_KAREN_SIGNING_SECRET / SLACK_KAREN_APP_ID.
She posts only under that token, and only when it (and her app id and
signing secret) differ from every other agent's -- a value shared with
Derek, Xavier or Audrey would make one bot speak as another, so her
deliveries are refused instead (NO IMPERSONATION). A delivery whose content
is Karen's (source_key 'karen:...') is never sent under another agent's
token. Her answers come from her challenge records only (no persona model).
She is optional: the bridge can be enabled with the three operating agents
configured and Karen not yet set up.

ARCHER (head of execution) and SCOUT (market intelligence), migration 217,
are dedicated apps on exactly the same terms: SLACK_ARCHER_BOT_TOKEN /
_SIGNING_SECRET / _APP_ID and SLACK_SCOUT_BOT_TOKEN / _SIGNING_SECRET /
_APP_ID. Each posts only under its own token, only when all three values
differ from EVERY other agent's (`dedicated_identity`), and content of
theirs (source_key 'archer:...' / 'scout:...') is never sent under another
agent's token. They answer mentions from their own records only (no persona
model), and they are optional too.

THE HISTORICAL ALIAS (migration 266). Archer was named EDDIE. His app's
values may still be configured under the historical names SLACK_EDDIE_*:
each ARCHER value falls back to its SLACK_EDDIE_* twin ONLY when the
SLACK_ARCHER_* one is unset -- still his own app, and still subject to the
same all-three-present, distinct-from-every-other-agent rule, so with
neither configured nothing is sent (fail closed). 'eddie' is not an agent
here: Slack events posted to /slack/eddie/events are Archer's (alias), a
delivery queued as 'eddie' before 266 is never claimed or sent, and content
keyed 'eddie:' travels under no token but Archer's.
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
AGENTS=('derek','xavier','audrey','karen','archer','scout','adriana')
#: The bridge may be enabled once these are configured; Karen, Archer, Scout
#: and Adriana are optional.
REQUIRED_AGENTS=('derek','xavier','audrey')
KAREN='karen'
KAREN_SOURCE='karen:'
ARCHER='archer'
SCOUT='scout'
ADRIANA='adriana'
#: The agents with a DEDICATED app whose content may never travel under
#: another agent's token: agent -> its source_key prefix.
DEDICATED={KAREN:KAREN_SOURCE,ARCHER:'archer:',SCOUT:'scout:',ADRIANA:'adriana:'}
#: (266) historical alias slug -> the agent it names now; its source-key
#: prefix stays that agent's content; its env prefix is read as a fallback
HISTORICAL_ALIASES={'eddie':ARCHER}
ALIAS_SOURCES={'eddie:':ARCHER}
LEGACY_ENV={ARCHER:'SLACK_EDDIE_'}

def canonical_agent(agent):
 """A slug or historical alias -> the canonical slug ('eddie' -> 'archer')."""
 a=str(agent or '').strip().lower()
 return HISTORICAL_ALIASES.get(a,a)
QUEUE_CAP=300

def _clean(v):
 # A pasted credential sometimes carries a trailing newline, spaces or
 # surrounding quotes; none of those is ever part of a Slack value.
 return (v or '').strip().strip('"\'').strip()

def _agent_env(agent,name):
 """SLACK_<AGENT>_<name>; for Archer, SLACK_EDDIE_<name> when his is unset."""
 v=_clean(os.getenv('SLACK_'+agent.upper()+'_'+name))
 if not v and agent in LEGACY_ENV:v=_clean(os.getenv(LEGACY_ENV[agent]+name))
 return v

def settings(agent):
 if agent not in AGENTS:raise ValueError('UNKNOWN_AGENT')
 return {'secret':_agent_env(agent,'SIGNING_SECRET'),'token':_agent_env(agent,'BOT_TOKEN'),
         'app':_agent_env(agent,'APP_ID'),'team':_clean(os.getenv('SLACK_TEAM_ID')),
         'channels':{x.strip() for x in os.getenv('SLACK_ALLOWED_CHANNEL_IDS','').split(',') if x.strip()},
         'managers':{x.strip() for x in os.getenv('SLACK_MANAGEMENT_USER_IDS','').split(',') if x.strip()},
         'workroom':_clean(os.getenv('SLACK_WORKROOM_CHANNEL_ID'))}

def dedicated_identity(agent):
 """Is a dedicated agent's Slack identity configured AND its own? Its bot
 token, app id and signing secret must each be present and differ from
 EVERY other agent's (an empty value never matches). Never returns a value,
 only booleans and the reason."""
 if agent not in DEDICATED:raise ValueError('NOT_A_DEDICATED_AGENT')
 k=settings(agent)
 configured=all(k[x] for x in ('token','secret','app','team','channels','managers'))
 clash=[]
 for a in AGENTS:
  if a==agent:continue
  o=settings(a)
  for f in ('token','secret','app'):
   if k[f] and o[f] and k[f]==o[f]:clash.append(a+'.'+f)
 why=None if configured and not clash else ('SHARES_'+'_'.join(c.upper().replace('.','_') for c in clash) if clash else agent.upper()+'_SLACK_APP_NOT_CONFIGURED')
 return {'configured':configured,'distinct':not clash,'ok':configured and not clash,'why':why}

def karen_identity():
 """Is Karen's Slack identity configured AND her own? (dedicated_identity)"""
 return dedicated_identity(KAREN)

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
  from .api.admin_token_guard import constant_time_text_equal
  return constant_time_text_equal(expected,signature or '')
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
  # Only a mention of THIS app's bot arrives as its app_mention event.
  # Mentions of teammates/other tools must not discard a manager's reply.
  # Resolve this bot from Slack's signed, app/team-checked envelope, not
  # from the text, the sender, or an unverified display name. Only the
  # agent that already answered in that thread takes it (admit checks).
  if e.get('channel_type') not in (None,'channel','group'):return None,'NOT_A_CHANNEL'
  mentions=set(re.findall(r'<@([A-Z0-9]+)(?:\|[^>]*)?>',str(e.get('text') or '')))
  if mentions:
   auth=payload.get('authorizations')
   if not isinstance(auth,list):return None,'MENTION_BOT_IDENTITY_UNPROVED'
   bot_users={a['user_id'] for a in auth if isinstance(a,dict) and
              a.get('team_id')==cfg['team'] and a.get('is_bot') is True and
              isinstance(a.get('user_id'),str) and a['user_id']}
   if len(bot_users)!=1:return None,'MENTION_BOT_IDENTITY_UNPROVED'
   if mentions & bot_users:return None,'MENTION_HANDLED_AS_APP_MENTION'
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

KAREN_POSTS_PER_PASS=3

async def publish_karen_challenges(conn):
 """THE #agent-workroom POSTING PATH FOR KAREN (SLACK_WORKROOM_CHANNEL_ID):
 her new challenges (HIGH / CRITICAL first) and the recorded outcomes of her
 challenges (peer response + independent evaluation), from the last hour,
 once each, at most KAREN_POSTS_PER_PASS per pass -- queued ONLY as agent
 'karen', so they go out under HER token only. Nothing is queued until her
 Slack app is configured and her own (karen_identity()['ok']): before the
 manual admin step this sends nothing."""
 if not karen_identity()['ok']:return
 cfg=settings(KAREN)
 if not cfg['workroom'] or cfg['workroom'] not in cfg['channels']:return
 if await conn.fetchval("SELECT to_regclass('karen_challenges')") is None:return
 if await conn.fetchval("SELECT count(*) FROM agent_slack_delivery WHERE state IN ('QUEUED','WORKING','READY','SENDING')")>=QUEUE_CAP-3:return
 from .agents import karen as K
 posts=[]
 for r in await conn.fetch("SELECT * FROM karen_challenges WHERE state='OPEN' AND challenged_at>now()-interval '1 hour' ORDER BY (severity IN ('HIGH','CRITICAL')) DESC, challenged_at DESC LIMIT $1",KAREN_POSTS_PER_PASS):
  c=K._with_links(K._row(r));posts.append(('challenge:'+c['challenge_id'],K.challenge_post(c),c['challenge_id']))
 for r in await conn.fetch("SELECT * FROM karen_challenges WHERE state IN ('UPHELD','REJECTED') AND resolved_at>now()-interval '1 hour' ORDER BY resolved_at DESC LIMIT $1",KAREN_POSTS_PER_PASS):
  c=K._with_links(K._row(r));posts.append(('outcome:'+c['challenge_id'],K.outcome_post(c),c['challenge_id']))
 for key,text,cid in posts[:KAREN_POSTS_PER_PASS]:
  source=KAREN_SOURCE+key
  if await conn.fetchval("SELECT 1 FROM agent_slack_delivery WHERE agent=$1 AND team_id=$2 AND source_key=$3",KAREN,cfg['team'],source):continue
  await conn.execute("INSERT INTO agent_slack_delivery(delivery_id,agent,team_id,channel_id,source_key,answer,message_id,state) VALUES($1,$2,$3,$4,$5,$6,$7,'READY') ON CONFLICT DO NOTHING",delivery_id(KAREN,cfg['team'],source),KAREN,cfg['team'],cfg['workroom'],source,text,cid)

POS_POSTS_PER_PASS=3

async def publish_pos_posts(conn):
 """THE #agent-workroom PATH FOR ARCHER, SCOUT AND ADRIANA: evidence-linked
 collaboration posts read from their records (archer.workroom_posts /
 scout.workroom_posts / adriana.workroom_posts -- review / estimate /
 outcome / tournament / census / opportunity ids), once
 each, at most POS_POSTS_PER_PASS per agent per pass, queued ONLY as that
 agent so they go out under its own token. Nothing is queued until the
 agent's dedicated identity is configured and its own."""
 for agent in (ARCHER,SCOUT,ADRIANA):
  if not dedicated_identity(agent)['ok']:continue
  cfg=settings(agent)
  if not cfg['workroom'] or cfg['workroom'] not in cfg['channels']:continue
  table={ARCHER:'eddie_execution_estimates',SCOUT:'scout_features',ADRIANA:'adriana_arb_scans'}[agent]
  if await conn.fetchval("SELECT to_regclass($1)",table) is None:continue
  if agent==ARCHER:
   from .agents import archer as M
  elif agent==SCOUT:
   from .agents import scout as M
  else:
   from .agents import adriana as M
  for key,text in (await M.workroom_posts(conn,limit=POS_POSTS_PER_PASS))[:POS_POSTS_PER_PASS]:
   source=DEDICATED[agent]+key
   if await conn.fetchval("SELECT 1 FROM agent_slack_delivery WHERE agent=$1 AND team_id=$2 AND source_key=$3",agent,cfg['team'],source):continue
   await conn.execute("INSERT INTO agent_slack_delivery(delivery_id,agent,team_id,channel_id,source_key,answer,state) VALUES($1,$2,$3,$4,$5,$6,'READY') ON CONFLICT DO NOTHING",delivery_id(agent,cfg['team'],source),agent,cfg['team'],cfg['workroom'],source,text)

IMPROVE_POSTS_PER_PASS=2
IMPROVE_POSTS_PER_HOUR=12
IMPROVE_WINDOW_H=6

async def publish_improvement_posts(conn):
 """THE #agent-workroom DIGEST OF THE IMPROVEMENT PIPELINE (migration 221):
 one evidence-linked line per STAGE TRANSITION (improve_events.is_transition)
 -- never for a repeated stage, never when nothing changed -- at most
 IMPROVE_POSTS_PER_PASS per pass and IMPROVE_POSTS_PER_HOUR per hour, from
 the last IMPROVE_WINDOW_H hours (an older transition is not backfilled).
 Each line goes out under the agent that recorded the transition, through
 the existing per-agent path: Karen / Archer / Scout content only under their
 own dedicated token (their source-key prefixes), a runner, human or
 engineering transition reported by Audrey naming who recorded it. Nothing
 is queued for an agent whose Slack identity is not configured."""
 if await conn.fetchval("SELECT to_regclass('improve_events')") is None:return
 if await conn.fetchval("SELECT count(*) FROM agent_slack_delivery WHERE state IN ('QUEUED','WORKING','READY','SENDING')")>=QUEUE_CAP-3:return
 sent=await conn.fetchval("SELECT count(*) FROM agent_slack_delivery WHERE source_key ~ '(^|:)improve:' AND created_at>now()-interval '1 hour'")
 budget=min(IMPROVE_POSTS_PER_PASS,IMPROVE_POSTS_PER_HOUR-int(sent or 0))
 if budget<=0:return
 from .agents import improvement_stages as IS
 rows=await conn.fetch("SELECT e.event_id,e.item_id FROM improve_events e WHERE e.is_transition AND e.at>now()-make_interval(hours=>$1) ORDER BY e.event_id LIMIT 50",IMPROVE_WINDOW_H)
 for r in rows:
  if budget<=0:return
  it=dict(await conn.fetchrow("SELECT * FROM improve_items WHERE item_id=$1",r['item_id']))
  evs=[dict(x) for x in await conn.fetch("SELECT * FROM improve_events WHERE item_id=$1 AND event_id<=$2 ORDER BY event_id",r['item_id'],r['event_id'])]
  for x in evs:
   for k in ('body','source_ref','evidence_refs'):x[k]=decode(x[k]) if x[k] is not None else None
  it['evidence_refs']=decode(it['evidence_refs'])
  ev=evs[-1];agent=IS.slack_agent(ev)
  if agent in DEDICATED:
   if not dedicated_identity(agent)['ok']:continue
   source=DEDICATED[agent]+'improve:'+str(ev['event_id'])
  else:
   source='improve:'+str(ev['event_id'])
  cfg=settings(agent)
  if not cfg['token'] or not cfg['team'] or not cfg['workroom'] or cfg['workroom'] not in cfg['channels']:continue
  if await conn.fetchval("SELECT 1 FROM agent_slack_delivery WHERE agent=$1 AND team_id=$2 AND source_key=$3",agent,cfg['team'],source):continue
  text=IS.slack_post(it,ev,IS.next_required(dict(it,stage=ev['stage']),evs))
  await conn.execute("INSERT INTO agent_slack_delivery(delivery_id,agent,team_id,channel_id,source_key,answer,message_id,state) VALUES($1,$2,$3,$4,$5,$6,$7,'READY') ON CONFLICT DO NOTHING",delivery_id(agent,cfg['team'],source),agent,cfg['team'],cfg['workroom'],source,text,it['item_id'])
  budget-=1

def impersonation(job):
 """A refusal code when sending `job` would put words in one bot's mouth
 under another bot's token; None when it may go."""
 agent=job.get('agent');source=str(job.get('source_key') or '')
 if source.startswith(KAREN_SOURCE) and agent!=KAREN:return 'IMPERSONATION_REFUSED_KAREN_CONTENT_ON_ANOTHER_TOKEN'
 if agent==KAREN and not karen_identity()['distinct']:return 'IMPERSONATION_REFUSED_KAREN_TOKEN_NOT_HER_OWN'
 if agent==KAREN and not karen_identity()['configured']:return 'KAREN_SLACK_APP_NOT_CONFIGURED_NOTHING_SENT'
 for owner in (ARCHER,SCOUT,ADRIANA):
  if source.startswith(DEDICATED[owner]) and agent!=owner:return 'IMPERSONATION_REFUSED_%s_CONTENT_ON_ANOTHER_TOKEN'%owner.upper()
 for prefix,owner in ALIAS_SOURCES.items():
  if source.startswith(prefix) and agent!=owner:return 'IMPERSONATION_REFUSED_%s_CONTENT_ON_ANOTHER_TOKEN'%owner.upper()
 if agent not in AGENTS:return 'NOT_A_CURRENT_AGENT_NOTHING_SENT'
 for owner in (ARCHER,SCOUT,ADRIANA):
  if agent==owner:
   ident=dedicated_identity(owner)
   if not ident['distinct']:return 'IMPERSONATION_REFUSED_%s_TOKEN_NOT_ITS_OWN'%owner.upper()
   if not ident['configured']:return '%s_SLACK_APP_NOT_CONFIGURED_NOTHING_SENT'%owner.upper()
 return None

async def claim(conn):
 async with conn.transaction():
  control=decode(await conn.fetchval('SELECT value FROM ingestion_state WHERE key=$1 FOR UPDATE',CONTROL))
  if control.get('enabled') is not True:return None
  await conn.execute("UPDATE agent_slack_delivery SET state='DELIVERY_UNKNOWN',error_code='SEND_LEASE_EXPIRED',updated_at=now() WHERE state='SENDING' AND lease_until<now()")
  if await conn.fetchval("SELECT 1 FROM agent_slack_delivery WHERE state IN ('WORKING','SENDING') AND lease_until>now() LIMIT 1"):return None
  await conn.execute("UPDATE agent_slack_delivery SET state='FAILED',error_code='ATTEMPT_LIMIT',updated_at=now() WHERE state='WORKING' AND lease_until<now() AND attempts>=3")
  await publish_reviews(conn)
  try:
   async with conn.transaction():await publish_karen_challenges(conn)
  except Exception:pass  # Karen's posts never block the bridge
  try:
   async with conn.transaction():await publish_pos_posts(conn)
  except Exception:pass  # Archer's / Scout's posts never block the bridge
  try:
   async with conn.transaction():await publish_improvement_posts(conn)
  except Exception:pass  # the improvement digest never blocks the bridge
  # only a CURRENT agent's delivery is claimed: one queued under the
  # historical alias 'eddie' before 266 stays as recorded, never sent
  row=await conn.fetchrow("SELECT * FROM agent_slack_delivery WHERE (state IN ('QUEUED','READY') OR (state='WORKING' AND lease_until<now())) AND attempts<3 AND agent=ANY($1::text[]) ORDER BY created_at,delivery_id LIMIT 1 FOR UPDATE SKIP LOCKED",list(AGENTS))
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
 refused=impersonation(job)
 if refused:
  async with pool.acquire() as c:await update(c,job,'FAILED',error=refused)
  return
 answer=job['answer'];message_id=job['message_id']
 if not answer and job['agent']==KAREN:
  # Karen answers from her challenge records only: no persona model, no
  # research assignment, nothing the question could instruct.
  from .agents import karen as K
  async with asyncio.timeout(10):
   async with pool.acquire() as c:answer=await K.slack_answer(c)
  message_id=None
 if not answer and job['agent'] in (ARCHER,SCOUT):
  # Archer and Scout answer from their own records only, likewise.
  if job['agent']==ARCHER:
   from .agents import archer as M
  else:
   from .agents import scout as M
  async with asyncio.timeout(10):
   async with pool.acquire() as c:answer=await M.slack_answer(c)
  message_id=None
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
         'karen_identity':karen_identity(),
         'dedicated_identities':{a:dedicated_identity(a) for a in DEDICATED},
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
 # Two agents resolving to ONE bot user would be one bot speaking as two.
 users={}
 for a,row in out.items():
  if row.get('bot_user_id'):users.setdefault(row['bot_user_id'],[]).append(a)
 for a,row in out.items():
  if row.get('bot_user_id'):row['shares_bot_user_with']=[b for b in users[row['bot_user_id']] if b!=a]
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
 if enabled and not all(all(settings(a)[k] for k in ('token','secret','app','team','channels','managers')) for a in REQUIRED_AGENTS):raise ValueError('THREE_AGENT_CONFIGURATION_REQUIRED')
 async with conn.transaction():
  await conn.fetchval('SELECT value FROM ingestion_state WHERE key=$1 FOR UPDATE',CONTROL)
  await conn.execute('UPDATE ingestion_state SET value=$2::jsonb WHERE key=$1',CONTROL,json.dumps({'enabled':enabled,'actor':actor,'changed_at':time.time()}))
  await conn.execute('INSERT INTO agent_slack_control_audit(enabled,actor) VALUES($1,$2)',enabled,actor)
 return await status(conn)
