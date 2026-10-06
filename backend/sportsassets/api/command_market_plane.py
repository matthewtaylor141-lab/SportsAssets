"""GET-only Command readback for the Universal Market Plane."""
from __future__ import annotations
import json,time
from fastapi import APIRouter,Depends,Query
from .agents_core import _pool, require_read
from ..market_plane import VERSION,AUTHORITY

router=APIRouter(); BASE="/api/command/market-plane"

def _row(r):
    d=dict(r)
    for k,v in list(d.items()):
        if hasattr(v,"timestamp"): d[k]=v.timestamp()
        elif k in ("ontology","detail","payload") and isinstance(v,str):
            try:d[k]=json.loads(v)
            except ValueError:pass
    return d

@router.get(BASE,dependencies=[Depends(require_read)])
async def market_plane(limit:int=Query(default=500,ge=1,le=5000)):
    pool=await _pool()
    async with pool.acquire() as c:
        ready=await c.fetchval("SELECT to_regclass('market_plane_registry') IS NOT NULL")
        if not ready:
            return {"status":"EMPTY","why":"MIGRATION_312_NOT_APPLIED","version":VERSION,"authority":AUTHORITY}
        rows=[_row(r) for r in await c.fetch("SELECT * FROM market_plane_registry ORDER BY active DESC,updated_at DESC LIMIT $1",limit)]
        cert=[_row(r) for r in await c.fetch("SELECT DISTINCT ON(contract_id) * FROM market_plane_certification ORDER BY contract_id,certified_at DESC")]
        counts=await c.fetchrow("SELECT count(*) FILTER(WHERE active) active,count(*) FILTER(WHERE active AND desired_subscription) desired,count(*) total FROM market_plane_registry")
        return {"status":"OK","version":VERSION,"authority":AUTHORITY,"computed_at":time.time(),
                "counts":dict(counts),"registry":rows,"certification":cert}
