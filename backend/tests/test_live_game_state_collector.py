import asyncio
import copy
import unittest
from sportsassets.live_game_state.core import bind_game,ScoreError,parse_odds
from sportsassets.live_game_state.providers import ScoreClient,Reply,ProviderError
from sportsassets.live_game_state.collector import Collector
from _bettor_live_game_state_test_helpers import fixture,espn,odds,parsed,raw,MemoryStore,NOW

class Transport:
    def __init__(self,clock=lambda:NOW):self.clock=clock;self.primary_error=None;self.fallback_error=None;self.calls=[]
    async def get(self,url,*,params=None):
        self.calls.append(url);now=self.clock()
        if 'odds-api' in url:
            if self.fallback_error:raise self.fallback_error
            b=odds()
        else:
            if self.primary_error:raise self.primary_error
            b=espn()
        return Reply(b,200,now,now,'TEST_HASH',{})
    async def close(self):pass

class CollectorTests(unittest.IsolatedAsyncioTestCase):
    def setup(self,**kwargs):
        self.now=[NOW];self.t=Transport(lambda:self.now[0]);self.s=MemoryStore()
        self.c=ScoreClient(self.t,clock=lambda:self.now[0],odds_key='TEST_ONLY',odds_enabled=True)
        self.worker=Collector(self.c,self.s,clock=lambda:self.now[0],**kwargs)
    async def asyncTearDown(self):
        if hasattr(self,'c'):await self.c.close()
    async def test_primary_recorded_and_bound(self):
        self.setup();r=await self.worker.refresh(fixture())
        self.assertTrue(r['ok']);self.assertEqual(r['source'],'ESPN');self.assertIn(fixture().key,self.s.latest)
    async def test_no_fallback_call_on_primary_success(self):
        self.setup();await self.worker.refresh(fixture());self.assertEqual(len(self.t.calls),1)
    async def test_fallback_is_real_separate_source(self):
        self.setup();self.t.primary_error=ProviderError('HTTP_429',retry_after=60)
        r=await self.worker.refresh(fixture());self.assertTrue(r['ok']);self.assertTrue(r['fallback'])
        self.assertEqual(r['source'],'THE_ODDS_API');self.assertEqual(self.s.latest[fixture().key]['last_play'],'')
    async def test_both_fail_keeps_last_good_timestamp(self):
        self.setup();await self.worker.refresh(fixture());before=copy.deepcopy(self.s.latest)
        self.now[0]+=20;self.t.primary_error=ProviderError('HTTP_500');self.t.fallback_error=ProviderError('HTTP_503')
        r=await self.worker.refresh(fixture());self.assertFalse(r['ok']);self.assertEqual(before,self.s.latest)
    async def test_many_markets_same_event_one_request_and_observation(self):
        self.setup();r=await self.worker.cycle([fixture(),fixture(),fixture()])
        self.assertEqual(r['attempted'],1);self.assertEqual(len(self.t.calls),1);self.assertEqual(len(self.s.latest),1)
    async def test_no_open_positions_no_provider_calls(self):
        self.setup();r=await self.worker.cycle([]);self.assertEqual(r['held_fixture_count'],0);self.assertEqual(self.t.calls,[])
    async def test_due_schedule_not_polled_each_tick(self):
        self.setup();await self.worker.cycle([fixture()]);self.now[0]+=2
        r=await self.worker.cycle([fixture()]);self.assertEqual(r['attempted'],0);self.assertEqual(len(self.t.calls),1)
    async def test_fairness_rotates_deferred_fixtures(self):
        self.setup(per_cycle=1);fx=[fixture(event_id=str(n)) for n in range(3)]
        for _ in range(3):await self.worker.cycle(fx);self.now[0]+=2
        self.assertEqual(len(self.s.latest),3)
    async def test_state_memory_prunes_closed_fixtures(self):
        self.setup();await self.worker.cycle([fixture()]);await self.worker.cycle([]);self.assertEqual(self.worker.work,{})
    async def test_conflicting_canonical_fixtures_excluded(self):
        self.setup();r=await self.worker.cycle([fixture(),fixture(home='Other Team')]);self.assertEqual(r['identity_collisions'],1);self.assertEqual(r['attempted'],0)
    async def test_provider_pin_prevents_silent_rebinding(self):
        self.setup();await self.worker.refresh(fixture())
        self.s.bindings[(fixture().key,'ESPN',fixture().fingerprint)]['provider_event_id']='other'
        self.c.odds_enabled=False
        r=await self.worker.refresh(fixture());self.assertFalse(r['ok'])
    async def test_ambiguous_pair_not_recorded(self):
        self.setup()
        async def duplicate(*a,**kw):return [parsed(),dict(parsed(),provider_event_id='other')]
        self.c.espn_board=duplicate;self.c.odds_enabled=False
        r=await self.worker.refresh(fixture());self.assertFalse(r['ok']);self.assertEqual(self.s.latest,{})
    async def test_cycle_error_does_not_kill_other_fixture(self):
        self.setup()
        orig=self.worker.refresh
        async def one(f,**kw):
            if f.event_id=='broken':raise RuntimeError('secret must not appear')
            return await orig(f,**kw)
        self.worker.refresh=one
        r=await self.worker.cycle([fixture(event_id='broken'),fixture()]);self.assertEqual(r['successful'],1)
        self.assertNotIn('secret',str(r))
    async def test_health_counts_are_not_global_freshness_claim(self):
        self.setup();r=await self.worker.cycle([fixture()]);self.assertIn('NOT_A_GLOBAL',r['rates_scope'])
    async def test_overflow_exposed(self):
        self.setup(max_state=1);r=await self.worker.cycle([fixture(event_id='a'),fixture(event_id='b')]);self.assertEqual(r['state_overflow'],1)
