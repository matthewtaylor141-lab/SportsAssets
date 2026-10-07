"""Incremental sharded PMX gRPC manager using BETTOR's existing transport.

New symbols are subscribed without reconnecting healthy shards. A shard is
rebuilt only when explicitly requested (e.g. compaction / credential reset).
No order path exists here.
"""
from __future__ import annotations
import threading,time

VERSION="SHARDED_PMX_STREAM_V2_INCREMENTAL"

class SizedBooks:
    @staticmethod
    def create(base_cls, max_symbols:int, clock):
        class _Books(base_cls):
            def __init__(self):
                super().__init__(clock=clock); self.max_symbols=max_symbols
                # (integration) a fresh ResidentBooks is not RUNNING: every
                # current() would refuse NOT_RUNNING. A shard's books are
                # armed IDLE exactly like start_default arms the singleton.
                try:
                    from sportsassets import institutional_stream as IS
                    self.set_state(IS.S_IDLE, "market-plane shard armed")
                except Exception:
                    pass
                import collections
                self.latency=collections.deque(maxlen=4000)
            def on_update(self, u, *, received_at=None):
                recv=float(received_at if received_at is not None else self._clock())
                super().on_update(u, received_at=recv)
                # the instant the canonical book for this symbol was
                # replaced (normalised and readable in this process)
                try:
                    norm=float(self._clock()); vt=(u or {}).get("transact_time")
                    vts=vt.timestamp() if hasattr(vt,"timestamp") else (
                        float(vt) if isinstance(vt,(int,float)) else None)
                    self.latency.append((vts,recv,norm))
                    sym=str((u or {}).get("symbol") or "")
                    with self._lock:
                        m=self._markets.get(sym)
                        if m is not None: m["normalized_at"]=norm
                except Exception:
                    pass
            def want(self, symbols):
                at=self._clock(); fresh=[]
                with self._lock:
                    for s in symbols or ():
                        s=str(s or "").strip()
                        if not s: continue
                        if s not in self._markets:
                            if len(self._markets)>=self.max_symbols: continue
                            from sportsassets import institutional_stream as IS
                            self._markets[s]=IS._new_market(at); fresh.append(s)
                        self._markets[s]["wanted_at"]=at
                return fresh
        return _Books()

class Manager:
    def __init__(self, *, token_fn, max_per_stream=1000, max_streams=20,
                 transport_factory=None, clock=None, invalidate_token=None):
        from sportsassets import institutional_stream as IS
        self.IS=IS; self.token_fn=token_fn; self.max_per_stream=int(max_per_stream)
        self.invalidate_token=invalidate_token
        self.max_streams=int(max_streams); self.transport_factory=transport_factory
        self.clock=clock or time.time; self._lock=threading.RLock(); self.shards={}
        self.symbol_to_shard={}

    def _new_shard(self, shard_id:int):
        if len(self.shards)>=self.max_streams: raise RuntimeError("STREAM_LIMIT_REACHED")
        books=SizedBooks.create(self.IS.ResidentBooks,self.max_per_stream,self.clock)
        t=(self.transport_factory(books,self.token_fn) if self.transport_factory
           else self.IS.GrpcBidiTransport(books,self.token_fn,
                                          invalidate_token=self.invalidate_token))
        t.start(); rec={"books":books,"transport":t,"symbols":set()}
        self.shards[int(shard_id)]=rec; return rec

    def sync(self, assignments:dict[str,int], instruments:dict[str,dict]|None=None)->dict:
        instruments=instruments or {}; added=0; instrument_updates=0; failures=[]
        with self._lock:
            for symbol,shard_id in sorted(assignments.items()):
                shard_id=int(shard_id)
                if shard_id<0 or shard_id>=self.max_streams:
                    failures.append({"symbol":symbol,"why":"SHARD_OUT_OF_RANGE"}); continue
                rec=self.shards.get(shard_id) or self._new_shard(shard_id)
                if symbol not in rec["symbols"]:
                    if len(rec["symbols"])>=self.max_per_stream:
                        failures.append({"symbol":symbol,"why":"SHARD_FULL"}); continue
                    fresh=rec["books"].want([symbol])
                    if fresh: rec["transport"].subscribe(fresh)
                    rec["symbols"].add(symbol); self.symbol_to_shard[symbol]=shard_id; added+=1
                inst=instruments.get(symbol)
                if inst is not None:
                    rec["books"].set_instrument(symbol,inst); instrument_updates+=1
        return {"ok":not failures,"added":added,"instrument_updates":instrument_updates,
                "shards":len(self.shards),"symbols":len(self.symbol_to_shard),
                "failures":failures,"version":VERSION}

    def set_instrument(self,symbol,record)->bool:
        with self._lock:
            sid=self.symbol_to_shard.get(symbol)
            if sid is None:return False
            self.shards[sid]["books"].set_instrument(symbol,record); return True

    def current(self,symbol,*,now=None,max_snapshot_age_s=None):
        with self._lock:
            sid=self.symbol_to_shard.get(symbol)
            if sid is None:return {"ok":False,"symbol":symbol,"refusal":"SYMBOL_NOT_SUBSCRIBED_TO_ANY_SHARD"}
            b=self.shards[sid]["books"]
        return b.current(symbol,now=now,max_snapshot_age_s=max_snapshot_age_s)

    def shard_digest(self)->list:
        out=[]
        with self._lock:
            for sid,rec in sorted(self.shards.items()):
                b=rec["books"]; t=rec["transport"]
                out.append({"shard":sid,"symbols":len(rec["symbols"]),
                            "state":getattr(b,"state",None),
                            "connected":getattr(t,"_connected",None),
                            "attempts":getattr(t,"attempts",None),
                            "consecutive_failures":getattr(t,"consecutive_failures",None)})
        return out

    def latency_samples(self, limit_per_shard=2000)->list:
        out=[]
        with self._lock:
            for rec in self.shards.values():
                lat=getattr(rec["books"],"latency",None)
                if lat: out.extend(list(lat)[-int(limit_per_shard):])
        return out

    def subscribed(self)->list:
        with self._lock:
            return sorted(self.symbol_to_shard)

    def stop(self):
        with self._lock:
            for s in self.shards.values():
                try:s["transport"].stop()
                except Exception:pass
            self.shards.clear(); self.symbol_to_shard.clear()
