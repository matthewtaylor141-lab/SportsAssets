import asyncio
from datetime import datetime,timezone
import unittest
import httpx
from sportsassets.live_game_state.providers import (
    Reply,ProviderError,ScoreClient,HttpTransport,http_observed_at,retry_after_seconds)
from _bettor_live_game_state_test_helpers import espn,odds,NOW

class FakeTransport:
    def __init__(self,clock=lambda:NOW): self.clock=clock;self.calls=[];self.fail=None;self.active=0;self.peak=0
    async def get(self,url,*,params=None):
        self.calls.append((url,params));self.active+=1;self.peak=max(self.active,self.peak)
        try:
            await asyncio.sleep(.005)
            if self.fail: raise self.fail
            body=odds() if 'odds-api' in url else espn()
            now=self.clock()
            return Reply(body,200,now,now,'TEST_HASH',{})
        finally:self.active-=1
    async def close(self): pass

class HeaderTests(unittest.TestCase):
    def test_age_header_not_ignored(self): self.assertEqual(http_observed_at({'Age':'120'},NOW),NOW-120)
    def test_date_and_age_not_double_counted(self):
        d=datetime.fromtimestamp(NOW-120,timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        self.assertEqual(http_observed_at({'Age':'120','Date':d},NOW),NOW-120)
    def test_missing_date_not_fabricated_as_source(self): self.assertEqual(http_observed_at({},NOW),NOW)
    def test_negative_age_refused(self):
        with self.assertRaises(ProviderError): http_observed_at({'Age':'-1'},NOW)
    def test_malformed_date_refused(self):
        with self.assertRaises(ProviderError): http_observed_at({'Date':'oops'},NOW)
    def test_retry_after_seconds(self): self.assertEqual(retry_after_seconds({'Retry-After':'180'},NOW),180)
    def test_retry_after_http_date(self):
        d=datetime.fromtimestamp(NOW+70,timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        self.assertEqual(retry_after_seconds({'retry-after':d},NOW),70)
    def test_bad_retry_after_conservative(self): self.assertEqual(retry_after_seconds({'retry-after':'oops'},NOW),60)

class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_flight(self):
        t=FakeTransport();c=ScoreClient(t,clock=lambda:NOW)
        await asyncio.gather(*(c.espn_board('MLB','20261007') for _ in range(10)))
        self.assertEqual(len(t.calls),1);self.assertEqual(c.counters['coalesced'],9)
        await c.close()
    async def test_cache_retains_real_observation_time(self):
        now=[NOW];t=FakeTransport(lambda:now[0]);c=ScoreClient(t,clock=lambda:now[0])
        first=await c.espn_board('MLB','20261007');now[0]+=5
        second=await c.espn_board('MLB','20261007')
        self.assertEqual(first[0]['received_at'],second[0]['received_at']);self.assertEqual(len(t.calls),1)
        await c.close()
    async def test_cache_expires(self):
        now=[NOW];t=FakeTransport(lambda:now[0]);c=ScoreClient(t,clock=lambda:now[0])
        await c.espn_board('MLB','20261007');now[0]+=9;await c.espn_board('MLB','20261007')
        self.assertEqual(len(t.calls),2);await c.close()
    async def test_cache_size_bounded(self):
        t=FakeTransport();c=ScoreClient(t,clock=lambda:NOW,cache_size=2)
        for date in ('20261005','20261006','20261007'): await c.espn_board('MLB',date)
        self.assertEqual(len(c._cache),2);await c.close()
    async def test_rate_budget_precedes_http(self):
        t=FakeTransport();c=ScoreClient(t,clock=lambda:NOW,espn_per_minute=1)
        await c.espn_board('MLB','20261007')
        with self.assertRaisesRegex(ProviderError,'MINUTE_BUDGET'): await c.espn_board('MLB','20261008')
        self.assertEqual(len(t.calls),1);await c.close()
    async def test_429_backoff_shared_per_provider(self):
        now=[NOW];t=FakeTransport();t.fail=ProviderError('HTTP_429',retry_after=180)
        c=ScoreClient(t,clock=lambda:now[0])
        with self.assertRaisesRegex(ProviderError,'429'): await c.espn_board('MLB','20261007')
        now[0]+=90
        with self.assertRaisesRegex(ProviderError,'BACKOFF'): await c.espn_board('MLB','20261008')
        self.assertEqual(len(t.calls),1);await c.close()
    async def test_odds_disabled_by_default(self):
        t=FakeTransport();c=ScoreClient(t,clock=lambda:NOW,odds_key='TEST_ONLY')
        with self.assertRaisesRegex(ProviderError,'NOT_ENABLED'): await c.odds_scores('MLB')
        self.assertEqual(len(t.calls),0);await c.close()
    async def test_odds_explicit_key_and_enable(self):
        t=FakeTransport();c=ScoreClient(t,clock=lambda:NOW,odds_key='TEST_ONLY',odds_enabled=True)
        rows=await c.odds_scores('MLB');self.assertEqual(rows[0]['source_at'],NOW-5)
        self.assertEqual(t.calls[0][1]['daysFrom'],3);await c.close()
    async def test_odds_hour_budget(self):
        now=[NOW];t=FakeTransport(lambda:now[0]);c=ScoreClient(t,clock=lambda:now[0],odds_key='TEST_ONLY',odds_enabled=True,odds_max_per_hour=1)
        await c.odds_scores('MLB');now[0]+=31
        with self.assertRaisesRegex(ProviderError,'HOURLY_BUDGET'): await c.odds_scores('MLB')
        self.assertEqual(len(t.calls),1);await c.close()
    async def test_quota_exhaustion_stops_new_requests(self):
        class T(FakeTransport):
            async def get(self,*a,**kw):
                r=await super().get(*a,**kw)
                return Reply(r.body,r.status,r.received_at,r.observed_at,r.payload_hash,{'x-requests-remaining':'0'})
        now=[NOW];t=T(lambda:now[0]);c=ScoreClient(t,clock=lambda:now[0],odds_key='TEST_ONLY',odds_enabled=True)
        await c.odds_scores('MLB');now[0]+=31
        with self.assertRaisesRegex(ProviderError,'QUOTA_EXHAUSTED'): await c.odds_scores('MLB')
        self.assertEqual(len(t.calls),1);await c.close()
    async def test_summary_rejects_untrusted_event_id(self):
        t=FakeTransport();c=ScoreClient(t)
        with self.assertRaisesRegex(ProviderError,'ID_INVALID'):await c.espn_summary('MLB','../../secrets')
        self.assertEqual(len(t.calls),0);await c.close()
    async def test_one_cancelled_waiter_does_not_cancel_shared_request(self):
        t=FakeTransport();c=ScoreClient(t,clock=lambda:NOW)
        a=asyncio.create_task(c.espn_board('MLB','20261007'));b=asyncio.create_task(c.espn_board('MLB','20261007'))
        await asyncio.sleep(.001);a.cancel()
        with self.assertRaises(asyncio.CancelledError):await a
        rows=await b;self.assertEqual(len(rows),1);self.assertEqual(len(t.calls),1);await c.close()

class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def make(self,handler,**kw):
        c=HttpTransport(**kw);await c._client.aclose()
        c._client=httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=False)
        return c
    async def test_transport_rejects_non_allowlisted_host(self):
        c=await self.make(lambda r:httpx.Response(200,json={}))
        try:
            with self.assertRaisesRegex(ProviderError,'URL_NOT_ALLOWED'):await c.get('https://evil.example/scoreboard')
        finally:await c.close()
    async def test_transport_rejects_other_api_paths(self):
        c=await self.make(lambda r:httpx.Response(200,json={}))
        try:
            with self.assertRaisesRegex(ProviderError,'PATH_NOT_ALLOWED'):await c.get('https://api.the-odds-api.com/v4/sports/x/orders')
        finally:await c.close()
    async def test_redirect_not_followed(self):
        c=await self.make(lambda r:httpx.Response(302,headers={'location':'https://evil.example/'}))
        try:
            with self.assertRaisesRegex(ProviderError,'HTTP_302'):await c.get('https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard')
        finally:await c.close()
    async def test_body_cap(self):
        c=await self.make(lambda r:httpx.Response(200,headers={'content-type':'application/json'},content=b' '*2000),max_body_bytes=1024)
        try:
            with self.assertRaisesRegex(ProviderError,'BODY_LIMIT'):await c.get('https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard')
        finally:await c.close()
    async def test_no_error_url_or_secret_leak(self):
        def bad(r):raise httpx.ConnectError('url?apiKey=DO_NOT_LEAK',request=r)
        c=await self.make(bad)
        try:
            with self.assertRaises(ProviderError) as got:await c.get('https://api.the-odds-api.com/v4/sports/baseball_mlb/scores',params={'apiKey':'DO_NOT_LEAK'})
            self.assertNotIn('DO_NOT_LEAK',str(got.exception));self.assertNotIn('apiKey',str(got.exception))
        finally:await c.close()
    async def test_html_is_not_parsed_as_scores(self):
        c=await self.make(lambda r:httpx.Response(200,text='<html>no</html>'))
        try:
            with self.assertRaisesRegex(ProviderError,'NOT_JSON'):await c.get('https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard')
        finally:await c.close()
    async def test_nonfinite_json_rejected(self):
        c=await self.make(lambda r:httpx.Response(200,headers={'content-type':'application/json'},content=b'{"a":NaN}'))
        try:
            with self.assertRaisesRegex(ProviderError,'JSON_INVALID'):await c.get('https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard')
        finally:await c.close()
    async def test_http_429_preserves_retry_after(self):
        c=await self.make(lambda r:httpx.Response(429,headers={'retry-after':'600'}))
        try:
            with self.assertRaises(ProviderError) as got:await c.get('https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard')
            self.assertEqual(got.exception.retry_after,600)
        finally:await c.close()
