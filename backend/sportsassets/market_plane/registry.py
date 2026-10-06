"""Durable shared operational registry helpers (Postgres, no authority)."""
from __future__ import annotations
import json,time,hashlib
from .sharding import assign_stable

VERSION="MARKET_REGISTRY_V2"
AUTHORITY="MARKET_DATA_ONLY_NO_ORDER_AUTHORITY"

def _sha(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()

async def upsert_contract(conn, row:dict):
    payload=dict(row); cid=str(payload["contract_id"])
    await conn.execute("""
      INSERT INTO market_plane_registry(contract_id,venue,sport,competition,event_id,
       market_type,ontology,active,desired_subscription,subscription_shard,refdata,refdata_at,
       updated_at,label,authority)
      VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11::jsonb,
             CASE WHEN $12::float8 IS NULL THEN NULL ELSE to_timestamp($12) END,
             to_timestamp($13),'RESEARCH',$14)
      ON CONFLICT(contract_id) DO UPDATE SET
       venue=excluded.venue,sport=excluded.sport,competition=excluded.competition,
       event_id=excluded.event_id,market_type=excluded.market_type,ontology=excluded.ontology,
       active=excluded.active,desired_subscription=excluded.desired_subscription,
       refdata=coalesce(excluded.refdata,market_plane_registry.refdata),
       refdata_at=coalesce(excluded.refdata_at,market_plane_registry.refdata_at),
       updated_at=excluded.updated_at
    """,cid,payload.get("venue"),payload.get("sport"),payload.get("competition"),
    payload.get("event_id"),payload.get("market_type"),json.dumps(payload.get("ontology") or {}),
    bool(payload.get("active",True)),bool(payload.get("desired_subscription",True)),
    payload.get("subscription_shard"),json.dumps(payload.get("refdata")) if payload.get("refdata") is not None else None,
    payload.get("refdata_at"),float(payload.get("updated_at") or time.time()),AUTHORITY)
    await conn.execute("""INSERT INTO market_plane_events(event_key,contract_id,kind,payload,at,label,authority)
      VALUES($1,$2,'CONTRACT_UPSERT',$3::jsonb,clock_timestamp(),'RESEARCH',$4) ON CONFLICT(event_key) DO NOTHING""",
      _sha(payload),cid,json.dumps(payload),AUTHORITY)

async def assign_missing_shards(conn, *, max_per_stream=1000,max_streams=20)->dict:
    rows=await conn.fetch("SELECT contract_id,subscription_shard FROM market_plane_registry WHERE active AND desired_subscription ORDER BY contract_id")
    symbols=[r["contract_id"] for r in rows]
    existing={r["contract_id"]:r["subscription_shard"] for r in rows if r["subscription_shard"] is not None}
    plan=assign_stable(symbols,existing,max_per_stream=max_per_stream,max_streams=max_streams)
    for sym,sid in plan["assignments"].items():
        if existing.get(sym)!=sid:
            await conn.execute("UPDATE market_plane_registry SET subscription_shard=$2 WHERE contract_id=$1",sym,sid)
    return plan

async def desired_contracts(conn)->list[dict]:
    rows=await conn.fetch("SELECT contract_id,subscription_shard,refdata,extract(epoch FROM refdata_at)::float8 refdata_at FROM market_plane_registry WHERE active AND desired_subscription ORDER BY subscription_shard,contract_id")
    out=[]
    for r in rows:
        d=dict(r)
        if isinstance(d.get("refdata"),str):
            try:d["refdata"]=json.loads(d["refdata"])
            except ValueError:d["refdata"]=None
        out.append(d)
    return out

async def save_refdata(conn,contract_id:str,record:dict,*,at:float|None=None):
    await conn.execute("UPDATE market_plane_registry SET refdata=$2::jsonb,refdata_at=to_timestamp($3) WHERE contract_id=$1",
                       contract_id,json.dumps(record),float(at or time.time()))

async def save_certification(conn, contract_id:str, cert:dict):
    await conn.execute("""INSERT INTO market_plane_certification(contract_id,fingerprint,status,comparable,agreeing,agreement,detail,certified_at,label,authority)
    VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,clock_timestamp(),'RESEARCH',$8)
    ON CONFLICT(contract_id,fingerprint) DO UPDATE SET status=excluded.status,comparable=excluded.comparable,
      agreeing=excluded.agreeing,agreement=excluded.agreement,detail=excluded.detail,certified_at=excluded.certified_at""",
      contract_id,cert["fingerprint"],cert["status"],cert["comparable"],cert["agreeing"],cert.get("agreement"),json.dumps(cert),AUTHORITY)
