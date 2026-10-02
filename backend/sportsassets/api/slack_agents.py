"""Slack signs the raw request; COMMAND cookies cannot authorize this endpoint."""
import asyncio
import json
from fastapi import APIRouter,Request,HTTPException
from .. import slack_bridge as S
router=APIRouter()

@router.post('/api/integrations/slack/{agent}/events')
async def events(agent:str,request:Request):
 try:cfg=S.settings(agent)
 except ValueError:raise HTTPException(404,'UNKNOWN_AGENT')
 if not cfg['secret']:raise HTTPException(503,'SLACK_NOT_CONFIGURED')
 raw=bytearray()
 async with asyncio.timeout(2):
  async for chunk in request.stream():
   raw.extend(chunk)
   if len(raw)>65536:raise HTTPException(413,'REQUEST_TOO_LARGE')
 if not S.verify(bytes(raw),request.headers.get('x-slack-request-timestamp'),request.headers.get('x-slack-signature'),cfg['secret']):raise HTTPException(401,'INVALID_SLACK_SIGNATURE')
 try:body=json.loads(raw)
 except ValueError:raise HTTPException(400,'INVALID_JSON')
 if not isinstance(body,dict):raise HTTPException(400,'INVALID_PAYLOAD')
 if body.get('type')=='url_verification':
  challenge=body.get('challenge')
  if not isinstance(challenge,str) or len(challenge)>500:raise HTTPException(400,'INVALID_CHALLENGE')
  return {'challenge':challenge}
 event,why=S.classify(body,cfg)
 ev=body.get('event') if isinstance(body.get('event'),dict) else {}
 if event is None:
  S.note_receipt(agent,body.get('event_id'),ev.get('type'),'IGNORED_'+why)
  return {'ok':True,'status':'IGNORED'}
 from ..db import get_pool
 try:
  async with asyncio.timeout(2):
   pool=await get_pool()
   async with pool.acquire() as c:result=await S.admit(c,agent,cfg,event)
 except TimeoutError:
  S.note_receipt(agent,body.get('event_id'),ev.get('type'),'QUEUE_UNAVAILABLE')
  raise HTTPException(503,'QUEUE_UNAVAILABLE')
 S.note_receipt(agent,body.get('event_id'),ev.get('type'),result)
 if result in ('OFF','QUEUE_FULL'):raise HTTPException(503,result)
 return {'ok':True,'status':result}

from fastapi import Depends,Response
from .agents_core import require_read,require_write
from .agent_capabilities import body,use

@router.get('/api/command/agents/slack',dependencies=[Depends(require_read)])
async def slack_status(response:Response):
 response.headers['Cache-Control']='no-store'
 return await use(S.status)

@router.post('/api/command/agents/slack/control',dependencies=[Depends(require_write)])
async def slack_control(request:Request,response:Response):
 b=await body(request,{'enabled','action'})
 response.headers['Cache-Control']='no-store'
 if b.get('action')=='check_tokens':
  # auth.test only: what each stored bot token authenticates as. No value.
  return {'tokens':await S.check_tokens()}
 if b.get('action')=='requeue_refused':
  return await use(lambda c:S.requeue_refused(c,b['actor']),write=True)
 if b.get('action') is not None:raise HTTPException(400,'UNKNOWN_ACTION')
 return await use(lambda c:S.configure(c,b.get('enabled'),b['actor']),write=True)
