"""Five agent capabilities; existing COMMAND reads and CONTROL writes."""
from __future__ import annotations
import asyncio
import json
import time
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from .agents_core import require_read, require_write, _pool
from ..agents import capability_work as W, capability_scorecards as S

router=APIRouter()
BASE='/api/command/agents/capabilities'


async def use(fn,*,write=False):
    try:
        async with asyncio.timeout(8):
            pool=await _pool()
            async with pool.acquire() as conn:
                if not await W.schema(conn):raise HTTPException(503,'CAPABILITY_SCHEMA_UNAVAILABLE')
                if write:return await fn(conn)
                async with conn.transaction(readonly=True,isolation='repeatable_read'):
                    return await fn(conn)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    except HTTPException:raise
    except Exception as exc:raise HTTPException(503,'CAPABILITY_READ_OR_WRITE_UNAVAILABLE:'+type(exc).__name__) from exc


async def body(request,allowed):
    # Reject cross-site cookie-auth writes. Bearer/control auth still required.
    origin=request.headers.get('origin')
    if origin and origin not in (str(request.base_url).rstrip('/'),'https://command.bettortoken.com'):
        raise HTTPException(403,'CROSS_SITE_WRITE_REFUSED')
    raw=bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw)>12000:raise HTTPException(413,'REQUEST_TOO_LARGE')
    try:b=json.loads(raw)
    except (ValueError,UnicodeDecodeError):raise HTTPException(400,'JSON_OBJECT_REQUIRED')
    if not isinstance(b,dict) or set(b)-set(allowed)-{'actor'}:raise HTTPException(400,'UNKNOWN_REQUEST_FIELDS')
    actor=b.get('actor')
    if not isinstance(actor,str) or not 2<=len(actor.strip())<=100 or actor.upper() in (*W.AGENTS,'SYSTEM'):
        raise HTTPException(400,'NAMED_MANAGER_REQUIRED')
    return b


@router.get(BASE,dependencies=[Depends(require_read)])
async def capabilities(response:Response):
    response.headers['Cache-Control']='no-store'
    return await use(lambda c:S.snapshot(c,time.time()))


@router.get(BASE+'/events',dependencies=[Depends(require_read)])
async def events(response:Response,after:int=Query(0,ge=0)):
    response.headers['Cache-Control']='no-store'
    return await use(lambda c:S.delivery_events(c,after))


@router.get(BASE+'/tools/{name}',dependencies=[Depends(require_read)])
async def tool(name:str,response:Response,agent:str=Query('DEREK'),record_id:str|None=Query(None,max_length=150)):
    from ..agents.capability_tools import Toolkit
    response.headers['Cache-Control']='no-store'
    # Toolkit owns its read-only transaction; do not nest a different isolation.
    return await use(lambda c:Toolkit(agent.upper(),time.time()).read(c,name,record_id),write=True)


@router.post(BASE+'/goals',dependencies=[Depends(require_write)])
async def goal(request:Request,response:Response):
    b=await body(request,{'request_id','title','first','priority','due_at','context'})
    key=b.get('request_id')
    if not isinstance(key,str) or not 8<=len(key)<=100:raise HTTPException(400,'REQUEST_ID_REQUIRED')
    response.headers['Cache-Control']='no-store'
    return await use(lambda c:W.create_flow(c,source_key='management:'+key,title=b.get('title'),first=b.get('first','DEREK'),priority=b.get('priority',3),due=b.get('due_at'),actor=b['actor'],now=time.time(),context=b.get('context')),write=True)


@router.post(BASE+'/control',dependencies=[Depends(require_write)])
async def control(request:Request,response:Response):
    b=await body(request,{'enabled','hourly_limit'});response.headers['Cache-Control']='no-store'
    return await use(lambda c:W.configure(c,enabled=b.get('enabled'),hourly_limit=b.get('hourly_limit',24),actor=b['actor'],now=time.time()),write=True)


@router.post(BASE+'/work/{task_id}/cancel',dependencies=[Depends(require_write)])
async def cancel(task_id:str,request:Request,response:Response):
    b=await body(request,set());response.headers['Cache-Control']='no-store'
    return await use(lambda c:W.cancel(c,task_id,b['actor'],time.time()),write=True)


@router.post(BASE+'/experiments',dependencies=[Depends(require_write)])
async def experiment(request:Request,response:Response):
    from ..agents.capability_experiments import register
    b=await body(request,{'request_id','change_class','strategy','change','rationale','training','evaluation','min_outcomes','lesson_ids'})
    actor=b.pop('actor');response.headers['Cache-Control']='no-store'
    return await use(lambda c:register(c,b,actor=actor,now=time.time()),write=True)
