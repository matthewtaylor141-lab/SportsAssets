"""Real feed frames -> bounded worker; external venue/book and audit are seams.
Full collector/Postgres release gate remains a separate integration requirement.
"""
import asyncio
import copy
import importlib
import unittest
from unittest.mock import patch
from types import SimpleNamespace
try:    # pytest collects tests/ as a package; unittest discovery does not
    from tests.test_pinnapi_primary_source import F, P, PKG, AT, seed, ROOT
except ImportError:
    from test_pinnapi_primary_source import F, P, PKG, AT, seed, ROOT

R = importlib.import_module(PKG + '.pinnapi_reactive')


def tick(cache, price=-115, ts=AT-1):
    market = {'key': F.FULL_GAME_MONEYLINE_KEY, 'type': 'moneyline',
              'period': 0, 'status': 'open', 'prices': [
                  {'designation': 'home', 'price': price},
                  {'designation': 'away', 'price': 110}]}
    return cache.apply({'type': 'prematch_markets', 'matchup_id': 123,
                        'data': [market], 'sport_id': 6, 'ts': ts*1000},
                       epoch=cache.authority.epoch, received_ms=ts*1000+5)


class Reactive(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cache, self.event = seed()
        self.records, self.calls = [], []
        self.done = asyncio.Event()
        async def audit(record):
            self.records.append(copy.deepcopy(record))
            if record['state'] in ('COMPLETED', 'TIMEOUT', 'REFUSED', 'ERROR'):
                self.done.set()
        async def evaluate(job):
            self.calls.append(job)
            return {'valuation_ids': []}
        self.audit, self.evaluate = audit, evaluate
        self.s = R.Scheduler(self.cache, evaluate, audit, clock=lambda: AT)
        self.s.register(self.event, sport_key='baseball_mlb', family='baseball', received_at=AT-4)
        self.cache.on_change = self.s.changed
        self.task = None

    async def asyncTearDown(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def run_one(self):
        self.task = asyncio.create_task(self.s.run())
        await asyncio.wait_for(self.done.wait(), 1)

    async def test_push_wakes_without_collection_timer_and_reads_current_book(self):
        book = {'ask': .49, 'depth': 1000}
        async def evaluate(job):
            # Real source selection at evaluation time; no receipt restamping.
            quote = P.select(self.cache, job['event'], None, family='baseball',
                             sharp_books={'pinnacle','betfair_ex_eu'}, at=AT, runtime_id='r1')
            await asyncio.sleep(0)  # stand-in for fresh venue request
            self.calls.append(dict(book=copy.deepcopy(book), quote=quote))
            return {'book': copy.deepcopy(book)}
        self.s.evaluate = evaluate
        tick(self.cache)
        book.update(ask=.61, depth=15)  # changed AFTER push, BEFORE evaluation
        await self.run_one()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['book'], {'ask': .61, 'depth': 15})
        self.assertEqual(self.calls[0]['quote']['reference_input']['provider'], P.PROVIDER)
        self.assertEqual(self.records[0]['state'], 'STARTED')
        self.assertEqual(self.records[-1]['state'], 'COMPLETED')
        self.assertEqual(self.calls[0]['quote']['observed_at'], AT-1)

    async def test_bursts_coalesce_and_unchanged_frames_do_not_reevaluate(self):
        tick(self.cache, -115)
        tick(self.cache, -110, AT-.5)
        tick(self.cache, -110, AT-.1)
        self.assertEqual(len(self.s.pending), 1)
        self.assertEqual(self.s.counts['COALESCED'], 1)
        self.assertEqual(self.s.counts['UNCHANGED'], 1)
        await self.run_one()
        self.assertEqual(self.calls[0]['trigger']['version'][2][1], ('home', -110))
        self.assertEqual(self.calls[0]['received_at'], AT-4)

    async def test_revoked_epoch_never_evaluates(self):
        tick(self.cache)
        self.cache.lost('LEASE_LOST')
        await self.run_one()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.records[-1]['state'], 'REFUSED')

    async def test_closed_market_never_evaluates(self):
        tick(self.cache)
        self.cache.quotes.clear()
        await self.run_one()
        self.assertEqual(self.calls, [])

    async def test_future_or_unknown_or_undiscovered_does_not_queue(self):
        tick(self.cache, ts=AT+2)
        self.assertFalse(self.s.pending)
        self.s.seeds.clear()
        tick(self.cache, -110)
        self.assertFalse(self.s.pending)

    async def test_expired_discovery_does_not_queue(self):
        self.s.seeds[123]['registered_at'] = AT-1801
        tick(self.cache)
        self.assertFalse(self.s.pending)
        self.assertEqual(self.s.counts['DISCOVERY_EXPIRED'], 1)

    async def test_timeout_is_recorded_with_persisted_valuation(self):
        async def slow(job):
            job['valuation_ids'].append(321)
            await asyncio.Event().wait()
        self.s.evaluate, self.s.deadline = slow, .01
        tick(self.cache)
        await self.run_one()
        self.assertEqual(self.records[-1]['state'], 'TIMEOUT')
        self.assertEqual(self.records[-1]['valuation_ids'], [321])

    async def test_audit_failure_prevents_evaluation(self):
        attempted = asyncio.Event()
        async def fail(record):
            attempted.set()
            raise RuntimeError('DB unavailable')
        self.s.audit = fail
        tick(self.cache)
        with self.assertLogs(R.log, level='ERROR'):
            self.task = asyncio.create_task(self.s.run())
            await asyncio.wait_for(attempted.wait(), 1)
            await asyncio.sleep(0)
        self.assertFalse(self.calls)
        self.assertEqual(self.s.counts['AUDIT_FAILED'], 1)

    async def test_new_change_while_busy_is_evaluated_next(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def evaluate(job):
            self.calls.append(job)
            if len(self.calls) == 1:
                entered.set()
                await release.wait()
            else:
                self.done.set()
            return {}
        self.s.evaluate = evaluate
        tick(self.cache)
        self.task = asyncio.create_task(self.s.run())
        await asyncio.wait_for(entered.wait(), 1)
        tick(self.cache, -105, AT-.5)
        release.set()
        # First completion also sets done; yield until second callback.
        for _ in range(20):
            if len(self.calls) == 2:
                break
            await asyncio.sleep(0)
        self.assertEqual(len(self.calls), 2)

    async def test_notification_failure_does_not_break_feed(self):
        def fail(q):
            raise RuntimeError('consumer failure')
        self.cache.on_change = fail
        tick(self.cache)
        self.assertTrue(self.cache.read(123, F.FULL_GAME_MONEYLINE_KEY,
                                       evaluated_ms=AT*1000)['ok'])
        self.assertEqual(self.cache.counts['change_notification_errors'], 1)

    def test_real_collector_has_paper_only_exit_and_book_read(self):
        # Supplemental structural check, not a substitute for full DB gate.
        text = (ROOT/'workers/ext_pinnacle_loop.py').read_text()
        cycle = text[text.index('async def cycle('):text.index('async def _paper_valuation')]
        self.assertIn('vq = await venue_quote(', cycle)
        self.assertIn('if stream_seed is not None:\n                # Never reach inventory', cycle)
        self.assertIn('if stream_seed is None and served_by_this_fetch', cycle)
        self.assertLess(cycle.index('return {"ran": True, "state": "WS_PAPER_EVALUATED"'),
                        cycle.index('joined = await join_outcomes'))
        self.assertIn('reactive_task = _reactive.start(pool, cycle=cycle)', text)


class Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_start_connects_feed_to_evaluator_and_stop_detaches(self):
        cache, event = seed()
        finished = asyncio.Event()
        rows, jobs = [], []
        class Connection:
            async def execute(self, sql, *args):
                rows.append(args)
                if args[2] == 'COMPLETED':
                    finished.set()
        class Acquire:
            async def __aenter__(self):
                return Connection()
            async def __aexit__(self, *args):
                pass
        pool = SimpleNamespace(acquire=Acquire)
        async def cycle(conn, *, stream_seed):
            jobs.append(stream_seed)
            return {'ran': True}
        task = None
        try:
            with patch.dict(R.os.environ, {'PINNAPI_REACTIVE_PAPER':'on', 'PAPER_SESSION':'on'}):
                task = R.start(pool, cycle=cycle)
                self.assertIsNotNone(task)
                R.ACTIVE.clock = lambda: AT
                R.register(event, sport_key='baseball_mlb', family='baseball', received_at=AT-4)
                tick(cache)
                await asyncio.wait_for(finished.wait(), 1)
                self.assertEqual(len(jobs), 1)
                self.assertEqual([r[2] for r in rows], ['STARTED','COMPLETED'])
        finally:
            await R.stop(task)
        self.assertIsNone(cache.on_change)
        self.assertIsNone(R.ACTIVE)

    async def test_queue_bound_discards_oldest_and_keeps_newest(self):
        cache, event = seed()
        async def nothing(*args):
            pass
        scheduler = R.Scheduler(cache, nothing, nothing, clock=lambda:AT, queue_cap=1)
        q = cache.quotes[(123, F.FULL_GAME_MONEYLINE_KEY)]
        scheduler.seeds[123] = {'registered_at':AT}
        scheduler.seeds[124] = {'registered_at':AT}
        scheduler.changed(q)
        from dataclasses import replace
        second = replace(q, event_id=124)
        cache.quotes[(124, F.FULL_GAME_MONEYLINE_KEY)] = second
        scheduler.changed(second)
        self.assertEqual(list(scheduler.pending), [124])
        self.assertEqual(scheduler.counts['QUEUE_EVICTED'], 1)
