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
 event=S.approved_event(body,cfg)
 if event is None:return {'ok':True,'status':'IGNORED'}
 from ..db import get_pool
 try:
  async with asyncio.timeout(2):
   pool=await get_pool()
   async with pool.acquire() as c:result=await S.admit(c,agent,cfg,event)
 except TimeoutError:raise HTTPException(503,'QUEUE_UNAVAILABLE')
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
 b=await body(request,{'enabled'})
 response.headers['Cache-Control']='no-store'
 return await use(lambda c:S.configure(c,b.get('enabled'),b['actor']),write=True)
