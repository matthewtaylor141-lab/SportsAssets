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
    # (RC5) EACH SHARD BY ITS COUNT, NEVER ITS MEMBER LIST. The plan rides
    # every heartbeat (each 2 s pass) and every SNAPSHOT event (each 60 s):
    # with one subscribe-all shard of 32,937 symbols its member list made
    # the heartbeat detail 1,401,121 characters (production readback,
    # pm-acceptance 37738089957) and each snapshot 1.4 MB. No reader reads
    # the members (the registry's subscription_shard column holds them).
    plan["shards"]=[{"shard":x["shard"],"count":x["count"]}
                    for x in plan.get("shards") or ()]
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


#: (RC5) THE REFDATA FIELDS THE PLANE READS, extracted by the database.
#: ResidentBooks.set_instrument reads exactly priceScale, fractionalQtyScale
#: (pmx_institutional.scales_of), state and productId; the certifier reads
#: priceScale / price_scale and fractionalQtyScale / qty_scale. A refdata
#: record is a full ListInstruments object (~2.3 KB of JSON, the rules text
#: twice in its metadata): every assignment pass (30 s) read and parsed all
#: 32,937 of them, +52 MB for the rows and +165 MB for the parsed objects,
#: the latter alive until the next pass rebound them. An object keeps only
#: these keys (an absent key -> JSON null, which .get() reads as absent, as
#: before); a value that is not a JSON object passes through whole, so the
#: readers see exactly what they saw.
SLIM_REFDATA_SQL = (
    "CASE WHEN jsonb_typeof(refdata) = 'object' THEN jsonb_build_object("
    "  %s) ELSE refdata END")
INSTRUMENT_KEYS = ("priceScale", "fractionalQtyScale", "state", "productId")
CERT_SCALE_KEYS = ("priceScale", "price_scale", "fractionalQtyScale",
                   "qty_scale")


def slim_refdata_sql(keys) -> str:
    return SLIM_REFDATA_SQL % ", ".join(
        "'%s', refdata->'%s'" % (k, k) for k in keys)


async def assigned_instruments(conn) -> list:
    """assigned_contracts with each refdata reduced to INSTRUMENT_KEYS (the
    fields Manager.sync -> set_instrument reads): the same rows, the same
    order, the same values for every key the books read."""
    return await conn.fetch(
        "SELECT contract_id, subscription_shard, "
        + slim_refdata_sql(INSTRUMENT_KEYS) + " AS refdata "
        "  FROM market_plane_registry "
        " WHERE venue='POLYMARKET_US' AND active AND subscription_shard IS NOT NULL "
        "   AND refdata IS NOT NULL "
        " ORDER BY subscription_shard, contract_id")


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


# ── (completion readiness) batched reference data for the market plane ──
# refdata_universe.Planner decides; these persist. Every write is a registry
# row's refdata/refdata_at for PMUS contracts the registry already holds:
# instruments outside the registry are counted by the caller, never stored.

REFDATA_FULL_PULL_KIND = "REFDATA_FULL_PULL"


async def save_refdata_many(conn, records: dict, *, at: float) -> int:
    """{contract_id: instrument record} -> rows updated (registry members)."""
    if not records:
        return 0
    ids = sorted(records)
    rows = await conn.fetch(
        "UPDATE market_plane_registry r SET refdata = v.rec::jsonb, "
        "       refdata_at = to_timestamp($3) "
        "  FROM (SELECT unnest($1::text[]) AS id, unnest($2::text[]) AS rec) v "
        " WHERE r.contract_id = v.id AND r.venue = 'POLYMARKET_US' "
        "RETURNING r.contract_id",
        ids, [json.dumps(records[i], default=str) for i in ids], float(at))
    return len(rows)


async def save_unlisted_many(conn, contract_ids, *, at: float,
                             basis: str) -> int:
    """Proven absent (a 200 by-symbol read that omitted them, or a COMPLETE
    full pull that never listed them): recorded so they are re-asked only
    after the worker's UNLISTED_RETRY_S."""
    ids = sorted({str(x) for x in contract_ids or () if x})
    if not ids:
        return 0
    rows = await conn.fetch(
        "UPDATE market_plane_registry SET refdata = $2::jsonb, "
        "       refdata_at = to_timestamp($3) "
        " WHERE contract_id = ANY($1::text[]) AND venue = 'POLYMARKET_US' "
        "   AND (refdata IS NULL OR refdata->>'unlisted' = 'true') "
        "RETURNING contract_id",
        ids, json.dumps({"unlisted": True, "basis": basis}), float(at))
    return len(rows)


#: ── (RC6.3c PMX-1) THE DECIDING PROCESS'S ASKED SYMBOLS, FIRST ───────────
#: The API's stream task hands the symbols its consumers ask about to
#: ingestion_state[ASKED_HANDOFF_KEY] (institutional_api_stream
#: .handoff_asked: one bounded row, each symbol with its ask instant and
#: whether that process holds its refdata). The refdata slot reads it and
#: puts those symbols FIRST in the PRIORITY by-symbol read, before
#: EVALUATED_CANDIDATE, held and imminent contracts: the API takes the
#: record persisted here without a venue call. The plane reads only what the
#: registry holds (its refdata is a registry row's); an asked symbol outside
#: the registry is counted, never read.
#: = institutional_api_stream.ASKED_HANDOFF_KEY (a test pins it)
ASKED_HANDOFF_KEY = "pmx_asked_symbols"
#: the required_reason populate gives an evaluated candidate (populate
#: .contract_row / PROMOTE_SQL); second in the PRIORITY read
EVALUATED_CANDIDATE = "EVALUATED_CANDIDATE"
#: the PRIORITY read's order, by name (refdata_pending_split)
PRIORITY_ORDER = ("ASKED_BY_A_CONSUMER", EVALUATED_CANDIDATE,
                  "OPEN_PAPER_POSITION", "IMMINENT_BY_EVENT_START")


async def asked_handoff(conn, *, now: float, max_age_s: float,
                        idle_s: float | None = None) -> dict:
    """The asked symbols the deciding process handed off, most recently
    asked first: only from a hand-off written within `max_age_s` (a stopped
    API's row is not read as asks), only symbols asked within `idle_s`
    (default `max_age_s`). {"symbols": [...], "state": OK | ABSENT | STALE |
    UNREADABLE, "at", "age_s", "without_refdata": n}. Never raises."""
    idle = float(max_age_s if idle_s is None else idle_s)
    out = {"symbols": [], "state": "ABSENT", "at": None, "age_s": None,
           "without_refdata": 0, "process": None}
    try:
        if not await conn.fetchval(
                "SELECT to_regclass('ingestion_state') IS NOT NULL"):
            return out
        v = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1",
            ASKED_HANDOFF_KEY)
        if v is None:
            return out
        if isinstance(v, str):
            v = json.loads(v)
        if not isinstance(v, dict):
            return dict(out, state="UNREADABLE")
        at = float(v.get("at"))
        out.update(at=at, age_s=round(float(now) - at, 1),
                   process=v.get("process"))
        if not (0.0 <= float(now) - at <= float(max_age_s)):
            return dict(out, state="STALE")
        rows = [r for r in (v.get("symbols") or []) if isinstance(r, dict)
                and r.get("symbol")]
        rows = [r for r in rows
                if float(now) - float(r.get("asked_at") or 0.0) <= idle]
        rows.sort(key=lambda r: -float(r.get("asked_at") or 0.0))
        seen, syms = set(), []
        for r in rows:
            s = str(r["symbol"])
            if s not in seen:
                seen.add(s)
                syms.append(s)
        return dict(out, symbols=syms, state="OK",
                    without_refdata=sum(1 for r in rows
                                        if not r.get("refdata")))
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, state="UNREADABLE", error=type(exc).__name__)


async def refdata_pending_split(conn, *, now: float, unlisted_retry_s: float,
                                priority_max: int, imminent_s: float,
                                limit: int = 1000, excluded=(),
                                asked=()) -> dict:
    """The contracts without usable refdata, split into PRIORITY and OTHER,
    cooling ids excluded BEFORE the limit. PRIORITY, in this order
    (PRIORITY_ORDER): the `asked` symbols (the deciding process's hand-off,
    in its order: most recently asked first; registry members only), then
    EVALUATED_CANDIDATE, then the rest of held / candidate / imminent
    (priority <= `priority_max`, or starting within `imminent_s`) in
    priority order. `asked_pending` counts the asked symbols in the read;
    `asked_not_in_registry` the asked symbols the registry does not hold
    (nothing to persist a record into: counted, never read)."""
    ex = sorted(set(excluded))
    asked_order = []
    seen = set()
    for s in asked or ():
        s = str(s or "")
        if s and s not in seen:
            seen.add(s)
            asked_order.append(s)
    asked_order = asked_order[:int(limit)]
    asked_pri, not_in_registry = [], 0
    if asked_order:
        arows = await conn.fetch(
            "SELECT a.s AS contract_id, r.contract_id IS NOT NULL AS known, "
            "       (r.refdata IS NULL OR (r.refdata->>'unlisted'='true' "
            "        AND r.refdata_at < to_timestamp($2))) AS pending "
            "  FROM unnest($1::text[]) WITH ORDINALITY AS a(s, ord) "
            "  LEFT JOIN market_plane_registry r "
            "    ON r.contract_id = a.s AND r.venue = 'POLYMARKET_US' "
            " WHERE NOT (a.s = ANY($3::text[])) ORDER BY a.ord",
            asked_order, float(now) - float(unlisted_retry_s), ex)
        for r in arows:
            if not r["known"]:
                not_in_registry += 1
            elif r["pending"]:
                asked_pri.append(r["contract_id"])
    rows = await conn.fetch(
        "SELECT contract_id, (priority <= $4 OR (event_start IS NOT NULL AND "
        "        event_start BETWEEN to_timestamp($5) - interval '4 hours' "
        "                        AND to_timestamp($5 + $6))) AS pri "
        "  FROM market_plane_registry "
        " WHERE venue='POLYMARKET_US' AND active AND desired_subscription "
        "   AND NOT (contract_id = ANY($3::text[])) AND ("
        "       refdata IS NULL OR (refdata->>'unlisted'='true' AND "
        "       refdata_at < to_timestamp($1))) "
        " ORDER BY (required_reason IS NOT DISTINCT FROM $7) DESC, "
        "          priority, event_start NULLS LAST, contract_id LIMIT $2",
        float(now) - float(unlisted_retry_s), int(limit) * 2, ex,
        int(priority_max), float(now), float(imminent_s), EVALUATED_CANDIDATE)
    in_asked = set(asked_pri)
    pri = asked_pri + [r["contract_id"] for r in rows
                       if r["pri"] and r["contract_id"] not in in_asked]
    pri = pri[:int(limit)]
    oth = [r["contract_id"] for r in rows
           if not r["pri"] and r["contract_id"] not in in_asked][:int(limit)]
    return {"priority": pri, "other": oth, "asked_pending": len(asked_pri),
            "asked_not_in_registry": not_in_registry}


async def refdata_pending_ids(conn) -> set:
    """Contracts with no refdata at all: the set a full pull starting now
    can later prove unlisted (contracts added after it began cannot be)."""
    return {r["contract_id"] for r in await conn.fetch(
        "SELECT contract_id FROM market_plane_registry "
        " WHERE venue='POLYMARKET_US' AND active AND refdata IS NULL")}


async def last_complete_full_pull(conn) -> float | None:
    """The finish time of the newest COMPLETE full pull (durable: a restart
    does not repoll the universe inside the refresh window)."""
    v = await conn.fetchval(
        "SELECT max((payload->>'finished_at')::float8) "
        "  FROM market_plane_events WHERE kind = $1 "
        "   AND payload->>'status' = 'COMPLETE'", REFDATA_FULL_PULL_KIND)
    return float(v) if v is not None else None
