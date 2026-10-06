"""Universal Market Plane supervisor: durable registry -> incremental PMX shards.

No orders. Refdata is bootstrapped incrementally and persisted; adding a market
does not reconnect existing healthy shards.
"""
from __future__ import annotations
import asyncio,logging,os,time
from ..db import get_pool, heartbeat
from ..market_plane import registry as R
from ..market_plane.sharded_stream import Manager

log=logging.getLogger(__name__); SERVICE="universal_market_plane"
INTERVAL_S=2.0; ASSIGN_EVERY_S=30.0; REFDATA_PER_PASS=24; REFDATA_RETRY_S=300.0

async def main():
    from .. import pmx_institutional as PMX
    from .institutional_md import bootstrap_instrument
    client=PMX.Institutional(env=os.environ); mgr=Manager(token_fn=client.token)
    last_assign=0.0; attempted={}
    while True:
        try:
            pool=await get_pool(); now=time.time()
            async with pool.acquire() as c:
                if now-last_assign>=ASSIGN_EVERY_S:
                    plan=await R.assign_missing_shards(c); last_assign=now
                else: plan={"complete":True}
                rows=await R.desired_contracts(c)
            assignments={r["contract_id"]:r["subscription_shard"] for r in rows if r["subscription_shard"] is not None}
            instruments={r["contract_id"]:r["refdata"] for r in rows if r.get("refdata")}
            sync=mgr.sync(assignments,instruments)
            # bounded refdata catch-up; missing scales stay fail-closed until read
            due=[r for r in rows if not r.get("refdata") and now-attempted.get(r["contract_id"],0)>=REFDATA_RETRY_S][:REFDATA_PER_PASS]
            boot={"attempted":0,"stored":0,"unlisted":0,"failed":0}
            for r in due:
                s=r["contract_id"]; attempted[s]=now; boot["attempted"]+=1
                try: got=await asyncio.to_thread(bootstrap_instrument,client,s)
                except Exception:
                    boot["failed"]+=1; continue
                rec=(got or {}).get("record")
                if rec is None:
                    boot["unlisted"]+=1; continue
                async with pool.acquire() as c: await R.save_refdata(c,s,rec,at=now)
                mgr.set_instrument(s,rec); boot["stored"]+=1
            status="ok" if plan.get("complete",True) and sync.get("ok") else "degraded"
            await heartbeat(SERVICE,status,{"assign":plan,"sync":sync,"refdata":boot})
            await asyncio.sleep(INTERVAL_S)
        except asyncio.CancelledError:
            mgr.stop(); raise
        except Exception as exc:
            log.exception("universal market plane failed")
            try: await heartbeat(SERVICE,"error",{"error":type(exc).__name__})
            except Exception: pass
            await asyncio.sleep(10)

if __name__=="__main__": asyncio.run(main())
