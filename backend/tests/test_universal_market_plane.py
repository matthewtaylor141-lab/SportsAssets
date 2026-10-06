import asyncio, importlib.util, pathlib, sys, types
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sportsassets.market_plane import ontology,entities,freshness,arbitration,certification,sharding,catalogue,coverage,opportunity,radar
from sportsassets.market_plane.models import SOURCE_PMX,SOURCE_RETAIL,SOURCE_REST

def test_ontology_generic_period_and_player_prop():
    r=ontology.parse_market_type(venue="POLYMARKET",contract_id="x",sports_market_type="football_player_passing_yards_first_half_total",line=125.5,subject_id="p1")
    assert r["ok"] and r["meaning"]["sport"]=="football"
    assert r["meaning"]["period"]=="FIRST_HALF"
    assert r["meaning"]["metric"]=="PASSING_YARDS"
    assert r["meaning"]["subject_type"]=="PLAYER"

def test_ontology_unknown_is_named_not_guessed():
    r=ontology.parse_market_type(venue="V",contract_id="x",sports_market_type="weird_magic_market")
    assert not r["ok"] and "METRIC_NOT_NORMALIZED" in r["gaps"]

def test_entities_collision_safe():
    reg=entities.Registry(); reg.add(entities.Entity("a","TEAM","Atlanta Hawks","nba",{"ATL"})); reg.add(entities.Entity("b","TEAM","Atlanta Dream","wnba",{"ATL"}))
    assert reg.resolve("ATL",kind="TEAM",competition="nba")["entity_id"]=="a"
    assert reg.resolve("ATL",kind="TEAM")["status"]=="UNRESOLVED"

def test_freshness_decomposes_internal_latency():
    d=freshness.decompose(venue_at=1.0,received_at=1.05,normalized_at=1.07,distributed_at=1.09,now=1.1)
    assert round(d["transport_ms"])==50 and round(d["internal_total_ms"])==40

def test_freshness_slo_red_when_internal_slow():
    rep=freshness.summarize([{"transport_ms":10,"processing_ms":300,"distribution_ms":10,"internal_total_ms":310}])
    assert not rep["green"] and "processing_p95" in rep["failures"]

def test_arbitration_prefers_certified_pmx():
    reads={SOURCE_PMX:{"ok":True,"venue_at":99,"certified":True},SOURCE_RETAIL:{"ok":True,"venue_at":100}}
    assert arbitration.choose(reads,now=100,max_age_s=5)["source"]==SOURCE_PMX

def test_arbitration_falls_back_when_pmx_uncertified():
    reads={SOURCE_PMX:{"ok":True,"venue_at":100,"certified":False},SOURCE_RETAIL:{"ok":True,"venue_at":99}}
    got=arbitration.choose(reads,now=100,max_age_s=5)
    assert got["source"]==SOURCE_RETAIL and got["fallback"]

def test_certification_survives_restart_when_fingerprint_same():
    ident={"venue":"P","contract_id":"x","institutional_symbol":"x","price_transform":"IDENTITY","price_scale":100,"qty_scale":1,"ontology_version":"v1","book_schema_version":"b1"}
    c=certification.verdict(comparable=30,agreeing=30,identity=ident)
    assert c["status"]=="SUPPORTED" and certification.can_reuse(c,ident)
    ident2=dict(ident,price_scale=1000)
    assert not certification.can_reuse(c,ident2)

def test_sharding_complete_and_no_loss():
    syms=[f"s{i}" for i in range(2501)]
    p=sharding.shard(syms,max_per_stream=1000,max_streams=3)
    assert p["complete"] and sum(x["count"] for x in p["shards"])==2501
    assert max(x["count"] for x in p["shards"])<=1000

def test_sharding_names_overflow():
    p=sharding.shard([str(i) for i in range(11)],max_per_stream=5,max_streams=2)
    assert not p["complete"] and len(p["overflow"])==1

def test_catalogue_recursively_eliminates_truncation():
    async def fetch(s,a,b):
        if b-a>25:return {"items":[],"truncated":True}
        return {"items":[(s,a,b)],"truncated":False,"cursor_complete":True}
    r=asyncio.run(catalogue.enumerate_complete(fetch,sports=["football"],start=0,end=100,min_window_s=1))
    assert r["complete"] and len(r["items"])==4

def test_catalogue_never_silently_accepts_unresolved_truncation():
    async def fetch(s,a,b):return {"items":[],"truncated":True}
    r=asyncio.run(catalogue.enumerate_complete(fetch,sports=["x"],start=0,end=1,min_window_s=2,max_depth=1))
    assert not r["complete"] and r["failures"]

def test_coverage_terminal_states_are_explicit():
    rows=[{"contract_id":"a","mapped":False},{"contract_id":"b","mapped":True,"settlement_proven":False},{"contract_id":"c","mapped":True,"settlement_proven":True,"fair_value_source":None},{"contract_id":"d","mapped":True,"settlement_proven":True,"fair_value_source":"pin","fresh_book":True}]
    m=coverage.matrix(rows)
    assert m["total"]==4 and m["silent_omissions"]==0 and m["by_state"]["PRICEABLE"]==1

def test_opportunity_is_content_addressed_and_fail_closed():
    a=opportunity.build(contract={"id":1},entities={},book=None,probability=None,settlement=None,execution=None,portfolio=None)
    assert not a["ready_for_agent_evaluation"] and a["content_sha256"]

def test_radar_red_on_missing_or_unfresh():
    cov=coverage.matrix([{"contract_id":"a","mapped":False}])
    r=radar.audit(venue_active=2,registry_active=1,subscribed=1,fresh=0,coverage=cov,catalogue_complete=False,shard_complete=True)
    assert not r["green"] and "ACTIVE_CONTRACTS_MISSING_FROM_REGISTRY" in r["failures"]

def test_stable_sharding_does_not_move_existing_symbols_when_new_one_added():
    first=sharding.assign_stable(["a","b","c"],max_per_stream=2,max_streams=2)
    second=sharding.assign_stable(["a","b","c","d"],first["assignments"],max_per_stream=2,max_streams=2)
    for s in ("a","b","c"):
        assert second["assignments"][s]==first["assignments"][s]

def test_incremental_stream_manager_adds_without_restarting(monkeypatch):
    import threading, types, sys
    class FakeBooks:
        def __init__(self,clock=None):
            self._clock=clock or (lambda:0); self._lock=threading.Lock(); self._markets={}; self.instruments={}
        def set_instrument(self,s,r): self.instruments[s]=r
        def current(self,s,**kw): return {"ok":s in self._markets,"symbol":s}
    def new_market(at): return {"wanted_at":at}
    fake=types.ModuleType('sportsassets.institutional_stream')
    fake.ResidentBooks=FakeBooks; fake._new_market=new_market; fake.GrpcBidiTransport=object
    monkeypatch.setitem(sys.modules,'sportsassets.institutional_stream',fake)
    import sportsassets
    monkeypatch.setattr(sportsassets,'institutional_stream',fake,raising=False)
    from sportsassets.market_plane.sharded_stream import Manager
    made=[]
    class T:
        def __init__(self,b): self.b=b; self.starts=0; self.subs=[]
        def start(self): self.starts+=1
        def subscribe(self,x): self.subs.extend(x)
        def stop(self): pass
    def factory(b,token):
        t=T(b); made.append(t); return t
    m=Manager(token_fn=lambda:'x',max_per_stream=10,max_streams=2,transport_factory=factory,clock=lambda:1)
    assert m.sync({'a':0})['ok'] and len(made)==1 and made[0].starts==1
    assert m.sync({'a':0,'b':0})['ok'] and len(made)==1 and made[0].starts==1
    assert 'b' in made[0].subs
