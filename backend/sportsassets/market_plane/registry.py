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
    # (integration) the event key is the CONTENT, not the write instant: the
    # same contract re-upserted unchanged appends nothing
    content={k:v for k,v in payload.items() if k not in ("updated_at","refdata_at")}
    await conn.execute("""INSERT INTO market_plane_events(event_key,contract_id,kind,payload,at,label,authority)
      VALUES($1,$2,'CONTRACT_UPSERT',$3::jsonb,clock_timestamp(),'RESEARCH',$4) ON CONFLICT(event_key) DO NOTHING""",
      _sha(content),cid,json.dumps(payload,default=str),AUTHORITY)

#: (integration) a contract is SUBSCRIBABLE on the institutional stream only
#: once its own refdata record is held (scales known: a book is never priced
#: with an assumed scale) and the venue lists it there
SUBSCRIBABLE_SQL = """
    SELECT contract_id, subscription_shard
      FROM market_plane_registry
     WHERE venue='POLYMARKET_US' AND active AND desired_subscription AND refdata IS NOT NULL
       AND coalesce(refdata->>'unlisted', 'false') <> 'true'
     ORDER BY priority, event_start NULLS LAST, contract_id"""


async def assign_missing_shards(conn, *, max_per_stream=1000,max_streams=20)->dict:
    rows=await conn.fetch(SUBSCRIBABLE_SQL)
    order=[r["contract_id"] for r in rows]
    existing={r["contract_id"]:r["subscription_shard"] for r in rows if r["subscription_shard"] is not None}
    plan=assign_stable(order,existing,max_per_stream=max_per_stream,
                       max_streams=max_streams,order=order)
    changed=[(sym,sid) for sym,sid in plan["assignments"].items() if existing.get(sym)!=sid]
    if changed:
        await conn.executemany("UPDATE market_plane_registry SET subscription_shard=$2 WHERE contract_id=$1",changed)
    # a contract no longer subscribable (retired, unlisted) or overflowed
    # gives up its slot -- by name in the plan, never silently
    await conn.execute(
        "UPDATE market_plane_registry SET subscription_shard=NULL "
        " WHERE venue='POLYMARKET_US' AND subscription_shard IS NOT NULL AND NOT (contract_id = ANY($1::text[]))",
        sorted(plan["assignments"]))
    plan=dict(plan)
    plan["overflow_count"]=len(plan.get("overflow") or ())
    plan["overflow"]=list(plan.get("overflow") or ())[:50]
    plan["subscribable"]=len(order)
    plan["shards_required_for_all"]=-(-len(order)//int(max_per_stream)) if order else 0
    plan["assignments"]=None
    return plan

async def desired_contracts(conn)->list[dict]:
    rows=await conn.fetch("SELECT contract_id,subscription_shard,refdata,extract(epoch FROM refdata_at)::float8 refdata_at,priority FROM market_plane_registry WHERE venue='POLYMARKET_US' AND active AND desired_subscription ORDER BY priority,subscription_shard,contract_id")
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


async def save_unlisted(conn, contract_id: str, *, at: float | None = None):
    """(integration) the institutional venue does not list this contract:
    recorded so the refdata catch-up does not re-ask every pass (retried
    after UNLISTED_RETRY_S by the worker); its book comes from the retail
    push / REST recovery sources instead."""
    await conn.execute(
        "UPDATE market_plane_registry SET refdata=$2::jsonb, "
        "       refdata_at=to_timestamp($3) WHERE contract_id=$1",
        contract_id, json.dumps({"unlisted": True}), float(at or time.time()))


async def record_event(conn, kind: str, key: str, payload: dict,
                       contract_id: str | None = None):
    await conn.execute(
        "INSERT INTO market_plane_events(event_key,contract_id,kind,payload,"
        "at,label,authority) VALUES($1,$2,$3,$4::jsonb,clock_timestamp(),"
        "'RESEARCH',$5) ON CONFLICT(event_key) DO NOTHING",
        key, contract_id, kind, json.dumps(payload, default=str), AUTHORITY)


async def assigned_contracts(conn) -> list:
    """(integration) the contracts holding a shard slot, with their refdata
    -- the manager's whole subscription, read once per assignment pass."""
    return [dict(r) for r in await conn.fetch(
        "SELECT contract_id, subscription_shard, refdata "
        "  FROM market_plane_registry "
        " WHERE venue='POLYMARKET_US' AND active AND subscription_shard IS NOT NULL "
        "   AND refdata IS NOT NULL "
        " ORDER BY subscription_shard, contract_id")]


async def refdata_due(conn, *, now: float, unlisted_retry_s: float,
                      limit: int = 64, excluded=()) -> list:
    """PMUS only; exclude retry cooldowns BEFORE LIMIT to prevent starvation."""
    rows = await conn.fetch(
        "SELECT contract_id FROM market_plane_registry "
        " WHERE venue='POLYMARKET_US' AND active AND desired_subscription "
        " AND NOT (contract_id=ANY($3::text[])) AND ("
        "       refdata IS NULL OR (refdata->>'unlisted'='true' AND "
        "       refdata_at<to_timestamp($1))) "
        " ORDER BY priority,event_start NULLS LAST,contract_id LIMIT $2",
        float(now)-float(unlisted_retry_s),int(limit),sorted(set(excluded)))
    return [r["contract_id"] for r in rows]
