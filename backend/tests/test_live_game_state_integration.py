import asyncio
import copy
from datetime import datetime,timezone
import os
from unittest.mock import patch
import unittest
from sportsassets.live_game_state.integration import apply_rows,enrich_snapshot
from _bettor_live_game_state_test_helpers import raw,fixture,NOW,Context


def snapshot():
    return {'snapshot_id':'ORIGINAL','schema':'bettor.trader.v1','mode':'PAPER','source':'NATIVE_LEDGER',
            'execution_authority':False,'authority':'READ_ONLY_SMALL_LIVE_SHADOW','snapshot_at':NOW,
            'positions':[{'venue':'POLYMARKET_US','event_id':fixture().event_id,'position_id':'p1',
              'qty':5,'state':'ACTIVE','holding_side':'LONG','quote':{'bid':.51,'current':False},
              'orders':[{'order_id':'porder','direction':'SELL','remaining_qty':5}],
              'packet':{'complete':False,'probability':.67,'missing':['NO_FRESH_PROBABILITY']},
              'unrealized_usd':None,'game':{'status':'UNAVAILABLE'}}],
            'counts':{'all_open':1,'complete_packets':0},'total_position_count':1}

class Conn:
    def __init__(self,fail=False):self.fail=fail;self.calls=[]
    def transaction(self,**kw):return Context()
    async def fetchval(self,q,*args):self.calls.append(q);return '8s'
    async def execute(self,q,*args):self.calls.append((q,args))
    async def fetch(self,q,*args):
        self.calls.append((q,args))
        if self.fail:raise RuntimeError('private DSN must not leak')
        return [dict(venue='POLYMARKET_US',event_id=fixture().event_id,payload=raw(),issue=None)]
    async def fetchrow(self,q,*args):self.calls.append((q,args));return {'heartbeat_at':datetime.fromtimestamp(NOW,timezone.utc),'payload':{'status':'OK'}}

class IntegrationTests(unittest.TestCase):
    def test_trading_fields_byte_equivalent(self):
        s=snapshot();r=apply_rows(s,[dict(venue='POLYMARKET_US',event_id=fixture().event_id,payload=raw(),issue=None)],now=NOW)
        for a,b in zip(s['positions'],r['positions']):
            self.assertEqual({k:v for k,v in a.items() if k!='game'},{k:v for k,v in b.items() if k!='game'})
        self.assertEqual(s['counts'],r['counts']);self.assertFalse(r['execution_authority'])
    def test_original_input_not_mutated(self):
        s=snapshot();before=copy.deepcopy(s);apply_rows(s,[],now=NOW);self.assertEqual(s,before)
    def test_final_score_never_settles_position(self):
        r=apply_rows(snapshot(),[dict(venue='POLYMARKET_US',event_id=fixture().event_id,payload=raw(game_status='FINAL'))],now=NOW)
        self.assertEqual(r['positions'][0]['state'],'ACTIVE');self.assertEqual(r['positions'][0]['game']['game_status'],'FINAL')
    def test_same_text_id_wrong_venue_not_attached(self):
        r=apply_rows(snapshot(),[dict(venue='KALSHI',event_id=fixture().event_id,payload=raw())],now=NOW)
        self.assertEqual(r['positions'][0]['game']['status'],'UNAVAILABLE')
    def test_score_does_not_fill_missing_probability(self):
        r=apply_rows(snapshot(),[dict(venue='POLYMARKET_US',event_id=fixture().event_id,payload=raw())],now=NOW)
        self.assertFalse(r['positions'][0]['packet']['complete'])
    def test_failed_read_not_vacuous_green(self):
        r=apply_rows(snapshot(),[],now=NOW,error='SCORE_EVIDENCE_READ_FAILED:TimeoutError')
        self.assertEqual(r['score_feed']['status'],'UNAVAILABLE');self.assertEqual(r['score_feed']['counts']['current'],0)
    def test_content_digest_changes(self):
        r=apply_rows(snapshot(),[],now=NOW);self.assertNotEqual(r['snapshot_id'],'ORIGINAL')
    def test_no_event_identity_is_explicit(self):
        s=snapshot();s['positions'][0]['event_id']=None
        r=apply_rows(s,[],now=NOW);self.assertEqual(r['positions'][0]['game']['why'],'CANONICAL_VENUE_EVENT_MISSING')
    def test_current_empty_denominator_not_100percent(self):
        s=snapshot();s['positions']=[];r=apply_rows(s,[],now=NOW)
        self.assertEqual(r['score_feed']['counts']['current'],0);self.assertNotIn('rate',r['score_feed'])

class AsyncIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_is_exact_noop(self):
        c=Conn();s=snapshot()
        with patch.dict(os.environ,{'BETTOR_DISPLAY_SCORES':'off'}):r=await enrich_snapshot(c,s,now=NOW)
        self.assertIs(r,s);self.assertEqual(c.calls,[])
    async def test_enabled_reads_only_db(self):
        c=Conn()
        with patch.dict(os.environ,{'BETTOR_DISPLAY_SCORES':'on'}):r=await enrich_snapshot(c,snapshot(),now=NOW)
        self.assertEqual(r['positions'][0]['game']['status'],'CURRENT')
        self.assertIn("SHOW statement_timeout",c.calls)
        self.assertTrue(any(isinstance(x,tuple) and 'set_config' in x[0] and x[1]==('8s',) for x in c.calls))
    async def test_database_error_localized(self):
        c=Conn(fail=True)
        with patch.dict(os.environ,{'BETTOR_DISPLAY_SCORES':'on'}):r=await enrich_snapshot(c,snapshot(),now=NOW)
        self.assertEqual(r['score_feed']['status'],'UNAVAILABLE');self.assertNotIn('private',str(r));self.assertEqual(len(r['positions']),1)
    async def test_cancellation_propagates(self):
        c=Conn()
        async def cancelled(*a,**kw):raise asyncio.CancelledError()
        c.fetch=cancelled
        with patch.dict(os.environ,{'BETTOR_DISPLAY_SCORES':'on'}):
            with self.assertRaises(asyncio.CancelledError):await enrich_snapshot(c,snapshot(),now=NOW)
