"""Stable subscription sharding for the complete active universe."""
from __future__ import annotations

VERSION="SUBSCRIPTION_SHARDING_V2_STABLE"
DEFAULT_MAX_PER_STREAM=1000
DEFAULT_MAX_STREAMS=20


def assign_stable(symbols, existing=None, *, max_per_stream=DEFAULT_MAX_PER_STREAM,
                  max_streams=DEFAULT_MAX_STREAMS, order=None) -> dict:
    """Preserve valid existing assignments; place only new symbols.

    No reshuffle on membership additions. That prevents healthy streams from
    reconnecting merely because the universe changed.
    """
    syms=sorted({str(s).strip() for s in symbols or () if str(s).strip()})
    existing={str(k):int(v) for k,v in (existing or {}).items()
              if str(k) in syms and 0 <= int(v) < int(max_streams)}
    loads={i:0 for i in range(int(max_streams))}; assigned={}
    # keep existing only while its shard still has room
    for s in syms:
        if s in existing and loads[existing[s]] < max_per_stream:
            assigned[s]=existing[s]; loads[existing[s]]+=1
    overflow=[]
    # (integration) NEW symbols are placed in the caller's priority order
    # (held positions, candidates, core families first), so when capacity
    # runs out the overflow is the LOWEST-priority tail -- named, never the
    # alphabetical tail. Existing assignments are never moved.
    rank={str(x):i for i,x in enumerate(order or ())}
    placing=sorted(syms,key=lambda x:(rank.get(x,len(rank)),x)) if order else syms
    for s in placing:
        if s in assigned: continue
        candidates=[i for i in range(int(max_streams)) if loads[i] < max_per_stream]
        if not candidates:
            overflow.append(s); continue
        i=min(candidates,key=lambda x:(loads[x],x))
        assigned[s]=i; loads[i]+=1
    shards=[]
    for i in range(int(max_streams)):
        members=sorted(s for s,v in assigned.items() if v==i)
        if members: shards.append({"shard":i,"symbols":members,"count":len(members)})
    return {"assignments":assigned,"shards":shards,"symbols":len(assigned),
            "capacity":int(max_per_stream)*int(max_streams),"overflow":overflow,
            "complete":not overflow,"version":VERSION}


def shard(symbols, *, max_per_stream=DEFAULT_MAX_PER_STREAM,
          max_streams=DEFAULT_MAX_STREAMS) -> dict:
    return assign_stable(symbols,{},max_per_stream=max_per_stream,
                         max_streams=max_streams)
