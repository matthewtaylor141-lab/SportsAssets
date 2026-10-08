"""Synthetic fixtures, explicitly not live market/score evidence."""
import copy
from sportsassets.live_game_state.core import Fixture, parse_espn, bind_game

NOW = 1791412800.0
START = NOW - 3600

def fixture(**kw):
    base=dict(venue="POLYMARKET_US",event_id="test:mlb:ny-bos",league="MLB",home="Boston Red Sox",
              away="New York Yankees",start_at=START,evidence_id="SYNTHETIC_TEST_FIXTURE")
    base.update(kw)
    return Fixture(**base)

def espn(league="MLB",state="STATUS_IN_PROGRESS",eid="401999001"):
    from sportsassets.live_game_state.core import LEAGUES
    from datetime import datetime, timezone
    return {"leagues":[{"slug":LEAGUES[league][1]}],"events":[{
        "id":eid,"date":datetime.fromtimestamp(START,timezone.utc).isoformat(),
        "competitions":[{"id":eid,"date":datetime.fromtimestamp(START,timezone.utc).isoformat(),
            "competitors":[
                {"homeAway":"home","team":{"id":"2","displayName":"Boston Red Sox","abbreviation":"BOS",
                 "logo":"https://a.espncdn.com/i/teamlogos/mlb/500/bos.png"},"score":"4"},
                {"homeAway":"away","team":{"id":"10","displayName":"New York Yankees","abbreviation":"NYY",
                 "logoDark":"https://a.espncdn.com/i/teamlogos/mlb/500-dark/scoreboard/nyy.png"},"score":"3"}],
            "status":{"period":7,"clock":321,"displayClock":"5:21","type":{"name":state,"state":"in","shortDetail":"Top 7th"}},
            "situation":{"outs":1,"balls":2,"strikes":1,"onFirst":True,"onSecond":False,"onThird":False,
                         "lastPlay":{"text":"Synthetic test single."}}}]}]}

def odds():
    from datetime import datetime, timezone
    return [{"id":"test-odds-1","sport_key":"baseball_mlb","home_team":"Boston Red Sox",
             "away_team":"New York Yankees","commence_time":datetime.fromtimestamp(START,timezone.utc).isoformat(),
             "completed":False,"scores":[{"name":"Boston Red Sox","score":"4"},
                                          {"name":"New York Yankees","score":"3"}],
             "last_update":datetime.fromtimestamp(NOW-5,timezone.utc).isoformat()}]

def parsed(**kwargs):
    return parse_espn(espn(),league="MLB",received_at=NOW,observed_at=NOW,
                      payload_hash="TEST_PAYLOAD_HASH",**kwargs)[0]

def raw(**kw):
    g=parsed();g.update(kw)
    return bind_game(fixture(),g)

class Context:
    async def __aenter__(self): return self
    async def __aexit__(self,*exc): return False

class MemoryStore:
    def __init__(self): self.latest={};self.bindings={};self.issues={};self.reports=[]
    async def binding(self,f,p): return self.bindings.get((f.key,p,f.fingerprint))
    async def write(self,f,r,*,now,issue=None):
        from sportsassets.live_game_state.storage import validate_transition
        from sportsassets.live_game_state.core import display,digest,ScoreError
        d=display(r,venue=f.venue,event_id=f.event_id,now=now)
        if d["status"]!="CURRENT": raise ScoreError(d["why"])
        validate_transition(self.latest.get(f.key,{}),r)
        self.latest[f.key]=copy.deepcopy(r);self.issues[f.key]=issue
        self.bindings[(f.key,r["source"],f.fingerprint)]=copy.deepcopy(r["identity"])
        return digest(r)
    async def issue(self,f,why,*,now): self.issues[f.key]=why
    async def heartbeat(self,payload,*,now): self.reports.append(copy.deepcopy(payload))
