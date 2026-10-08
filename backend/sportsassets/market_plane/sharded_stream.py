"""Incremental sharded PMX gRPC manager using BETTOR's existing transport.

New symbols are subscribed without reconnecting healthy shards. A shard is
rebuilt only when explicitly requested (e.g. compaction / credential reset).
No order path exists here.

SUBSCRIBE-ALL (completion readiness, venue guidance 2026-10-07): the venue's
20 concurrent gRPC streams are a FIRM-WIDE budget shared with orders, drop
copy, positions, balances and RFQ, and an empty symbol list subscribes one
market-data stream to every instrument. In that mode this manager holds
EXACTLY ONE stream (`symbols=[]`), whatever the assignments say; the books
hold only the assigned (registry) symbols and count the rest; a symbol the
registry no longer assigns is dropped locally (nothing is sent). The
explicit-shard mode is kept as a fallback / debug mode only.
"""
from __future__ import annotations
import threading,time

VERSION="SHARDED_PMX_STREAM_V3_SUBSCRIBE_ALL"
MODE_SUBSCRIBE_ALL="SUBSCRIBE_ALL_EMPTY_SYMBOL_LIST"
MODE_EXPLICIT="EXPLICIT_SYMBOL_SHARDS"
#: symbols seen on a subscribe-all stream outside the books, remembered for
#: discovery (new listings / non-registry instruments); bounded
SEEN_MAX=250000

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
                self.seen={}
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
            def on_filtered(self, symbol, update=None):
                super().on_filtered(symbol, update)
                # discovery evidence: the instrument universe the stream
                # actually carries, and the scales each wildcard update
                # states (evidence beside refdata; never used to price)
                try:
                    sym=str(symbol or "")
                    if sym and sym not in self.seen and len(self.seen)<SEEN_MAX:
                        ps=getattr(update,"price_scale",None) if update is not None else None
                        qs=getattr(update,"quantity_scale",None) if update is not None else None
                        self.seen[sym]=(self._clock(),int(ps) if ps else None,int(qs) if qs else None)
                except Exception:
                    pass
            def unwant(self, symbols):
                with self._lock:
                    for s in symbols or ():
                        symbol=str(s)
                        self._markets.pop(symbol,None)
                        # a retired symbol releases its refdata record too:
                        # in subscribe-all mode the assignment rotates
                        # through the catalogue, and keeping every record
                        # ever set defeats the books' bound (the next sync
                        # re-sets it if the symbol is assigned again)
                        self._instruments.pop(symbol,None)
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
                 transport_factory=None, clock=None, invalidate_token=None,
                 subscribe_all=False):
        from sportsassets import institutional_stream as IS
        self.IS=IS; self.token_fn=token_fn; self.max_per_stream=int(max_per_stream)
        self.invalidate_token=invalidate_token
        self.subscribe_all=bool(subscribe_all)
        # ONE stream in subscribe-all mode, whatever the caller configured
        self.max_streams=1 if self.subscribe_all else int(max_streams)
        self.subscription_mode=MODE_SUBSCRIBE_ALL if self.subscribe_all else MODE_EXPLICIT
        self.transport_factory=transport_factory
        self.clock=clock or time.time; self._lock=threading.RLock(); self.shards={}
        self.symbol_to_shard={}

    def _new_shard(self, shard_id:int):
        if len(self.shards)>=self.max_streams: raise RuntimeError("STREAM_LIMIT_REACHED")
        books=SizedBooks.create(self.IS.ResidentBooks,self.max_per_stream,self.clock)
        kw={"subscribe_all":True} if self.subscribe_all else {}
        t=(self.transport_factory(books,self.token_fn,**kw) if self.transport_factory
           else self.IS.GrpcBidiTransport(books,self.token_fn,
                                          invalidate_token=self.invalidate_token,**kw))
        t.start(); rec={"books":books,"transport":t,"symbols":set()}
        self.shards[int(shard_id)]=rec; return rec

    def stream_count(self)->int:
        """Market-data streams this manager holds open (1 in subscribe-all)."""
        with self._lock:
            return len(self.shards)

    def sync(self, assignments:dict[str,int], instruments:dict[str,dict]|None=None)->dict:
        instruments=instruments or {}; added=0; instrument_updates=0; failures=[]
        removed=0
        with self._lock:
            if self.subscribe_all:
                # every assignment lives on the one stream; a symbol no
                # longer assigned is dropped from the books (local only)
                assignments={k:0 for k in assignments}
                rec=self.shards.get(0)
                if rec is not None:
                    gone=[s for s in rec["symbols"] if s not in assignments]
                    if gone:
                        rec["books"].unwant(gone)
                        for s in gone:
                            rec["symbols"].discard(s); self.symbol_to_shard.pop(s,None)
                        removed=len(gone)
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
                "removed":removed,"shards":len(self.shards),"symbols":len(self.symbol_to_shard),
                "failures":failures[:50],"failure_count":len(failures),
                "subscription_mode":self.subscription_mode,"version":VERSION}

    def discovery(self, *, limit=0)->dict:
        """Subscribe-all evidence: instruments the stream carried that the
        books do not hold (counted; the first `limit` named)."""
        # (RC5) counted and sampled without copying the seen map (up to
        # SEEN_MAX entries) and sorting every key for a 10-name sample on
        # each snapshot: one list of its keys (taken under the GIL, as the
        # stream thread inserts), the `limit` smallest of them
        import heapq
        with self._lock:
            rec=self.shards.get(0) if self.subscribe_all else None
            seen=getattr(rec["books"],"seen",None) or {} if rec else {}
            filtered=getattr(rec["transport"],"filtered_updates",0) if rec else 0
            n=len(seen)
            sample=heapq.nsmallest(int(limit),list(seen)) if limit else []
        return {"mode":self.subscription_mode,"instruments_seen_outside_books":n,
                "filtered_updates":filtered,"seen_cap":SEEN_MAX,
                "sample":sample}

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
                            "consecutive_failures":getattr(t,"consecutive_failures",None),
                            "subscription_mode":getattr(t,"subscription_mode",self.subscription_mode),
                            "outbound":dict(getattr(t,"outbound",{}) or {}),
                            "filtered_updates":getattr(t,"filtered_updates",None)})
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
