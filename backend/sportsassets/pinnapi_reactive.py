"""Bounded WS -> paper evaluation scheduler beside the existing feed owner.

No new socket or funded execution. A collector-confirmed discovery seed is
required; other books keep their original clocks. Each attempted evaluation
is durably started BEFORE evaluating and completed with valuation IDs.
"""
from __future__ import annotations
import asyncio
from collections import Counter, OrderedDict
import copy
import json
import logging
import os
import time
import uuid

from . import pinnapi_feed as F
from . import pinnapi_primary as P

log = logging.getLogger(__name__)
ACTIVE = None


class Scheduler:
    def __init__(self, cache, evaluate, audit, *, clock=time.time,
                 queue_cap=128, seed_cap=512, seed_ttl=1800, deadline=12):
        self.cache, self.evaluate, self.audit, self.clock = cache, evaluate, audit, clock
        self.queue_cap, self.seed_cap = queue_cap, seed_cap
        self.seed_ttl, self.deadline = seed_ttl, deadline
        self.seeds, self.pending, self.seen = OrderedDict(), OrderedDict(), {}
        self.wake = asyncio.Event()
        self.counts = Counter()
        self.closed = False

    def register(self, event, *, sport_key, family, received_at):
        # Registration is called only AFTER competition confirmation.
        hit, why = P.match_event(self.cache, event, family)
        if why:
            self.counts[why] += 1
            return
        if len(json.dumps(event, default=str)) > 16384:
            self.counts['SEED_TOO_LARGE'] += 1
            return
        eid = hit[0]
        self.seeds[eid] = dict(event=copy.deepcopy(event), sport_key=sport_key,
                               family=family, received_at=received_at,
                               registered_at=self.clock())
        self.seeds.move_to_end(eid)
        while len(self.seeds) > self.seed_cap:
            old, _ = self.seeds.popitem(last=False)
            self.pending.pop(old, None)
            self.seen.pop(old, None)
            self.counts['SEED_EVICTED'] += 1

    def changed(self, quote):
        if self.closed or quote.key != F.FULL_GAME_MONEYLINE_KEY:
            return
        eid = quote.event_id
        seed = self.seeds.get(eid)
        if seed is None:
            self.counts['NO_CONFIRMED_DISCOVERY'] += 1
            return
        if not 0 <= self.clock() - seed['registered_at'] <= self.seed_ttl:
            self.counts['DISCOVERY_EXPIRED'] += 1
            return
        if not self.cache.read(eid, quote.key, evaluated_ms=self.clock()*1000).get('ok'):
            self.counts['UNUSABLE_CHANGE'] += 1
            return
        version = (quote.epoch, quote.source_change_ms, tuple(sorted(quote.prices.items())))
        if self.seen.get(eid) == version:
            self.counts['UNCHANGED'] += 1
            return
        self.seen[eid] = version
        if eid in self.pending:
            self.counts['COALESCED'] += 1
        elif len(self.pending) >= self.queue_cap:
            old, _ = self.pending.popitem(last=False)
            self.seen.pop(old, None)
            self.counts['QUEUE_EVICTED'] += 1
        self.pending[eid] = dict(version=version, received_at=quote.received_ms/1000,
                                 queued_at=self.clock())
        self.wake.set()

    async def run(self):
        while not self.closed:
            await self.wake.wait()
            if not self.pending:
                self.wake.clear()
                continue
            eid, tick = self.pending.popitem(last=False)
            if not self.pending:
                self.wake.clear()
            attempt = dict(attempt_id=uuid.uuid4().hex, event_id=eid, **tick,
                           evaluation_started_at=self.clock(), state='STARTED',
                           counters=dict(self.counts))
            seed = self.seeds.get(eid)
            got = self.cache.read(eid, F.FULL_GAME_MONEYLINE_KEY,
                                  evaluated_ms=self.clock()*1000)
            q = got.get('quote')
            version = None if q is None else (q.epoch, q.source_change_ms,
                                              tuple(sorted(q.prices.items())))
            if (not got.get('ok') or version != tick['version'] or seed is None or
                    not 0 <= self.clock()-seed['registered_at'] <= self.seed_ttl):
                attempt.update(state='REFUSED', reason=got.get('reason') or 'SUPERSEDED_OR_EXPIRED')
            try:
                # Audit failure blocks execution, it never becomes an unaudited trade.
                async with asyncio.timeout(2):
                    await self.audit(attempt)
                if attempt['state'] != 'STARTED':
                    continue
                job = copy.deepcopy(seed)
                job.update(valuation_ids=[], trigger=attempt)
                try:
                    async with asyncio.timeout(self.deadline):
                        result = await self.evaluate(job)
                    attempt.update(state='COMPLETED', result=result)
                except TimeoutError:
                    attempt.update(state='TIMEOUT')
                except asyncio.CancelledError:
                    attempt.update(state='CANCELLED')
                    raise
                except Exception as exc:
                    attempt.update(state='ERROR', error_type=type(exc).__name__)
                finally:
                    attempt.update(finished_at=self.clock(), valuation_ids=job['valuation_ids'])
                    async with asyncio.timeout(2):
                        await self.audit(attempt)
                self.counts[attempt['state']] += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                self.counts['AUDIT_FAILED'] += 1
                log.exception('pinnapi reactive audit failed; no unaudited evaluation started')


def register(event, **kwargs):
    if ACTIVE is not None:
        ACTIVE.register(event, **kwargs)


def start(pool, *, cycle):
    global ACTIVE
    # Independent rollout switch. Requires SQL in the delivery package first.
    if os.environ.get('PINNAPI_REACTIVE_PAPER', '').strip().lower() != 'on':
        return None
    if os.environ.get('PAPER_SESSION', '').strip().lower() not in ('on', '1', 'true', 'yes'):
        return None
    from . import pinnapi_feed_runtime as runtime
    owner = runtime._STATE.get('owner')
    if owner is None or ACTIVE is not None:
        return None

    async def evaluate(seed):
        async with pool.acquire() as conn:
            return await cycle(conn, stream_seed=seed)

    async def audit(attempt):
        async with pool.acquire() as conn:
            await conn.execute('''INSERT INTO pinnapi_reactive_attempts
                (attempt_id, event_id, state, detail) VALUES ($1,$2,$3,$4::jsonb)
                ON CONFLICT (attempt_id) DO UPDATE SET
                state=EXCLUDED.state, detail=EXCLUDED.detail, updated_at=now()''',
                attempt['attempt_id'], str(attempt['event_id']), attempt['state'],
                json.dumps(attempt, default=str))

    ACTIVE = Scheduler(owner.cache, evaluate, audit)
    owner.cache.on_change = ACTIVE.changed
    return asyncio.create_task(ACTIVE.run(), name='pinnapi-reactive-paper')


async def stop(task):
    global ACTIVE
    scheduler, ACTIVE = ACTIVE, None
    if scheduler is not None:
        scheduler.closed = True
        scheduler.cache.on_change = None
        scheduler.pending.clear()
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
