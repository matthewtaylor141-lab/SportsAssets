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


#: A held event's discovery seed is pinned while the position is held (no
#: eviction, no 30-minute expiry), up to this age: the evaluation itself
#: still re-proves identity, venue, settlement, depth and fees.
HELD_SEED_TTL_S = 6 * 3600

#: R30A · HOT NFL FRESHNESS PRIORITY. An NFL game near kickoff (from
#: HOT_BEFORE_KICKOFF_S before its scheduled start through HOT_AFTER_KICKOFF_S
#: after it) is where a fresh Pinnacle line matters most and where the
#: periodic collector, every ~15 minutes, is too slow for the 30 s rule (the
#: 2026-10-04 production receipt: two NFL games QUOTE_STALE_ON_ARRIVAL in all
#: 25 cycles). Such an event's changes get their OWN queue, served after held
#: events (open inventory keeps first call) and before every other discovery
#: change, and its seed is pinned against discovery eviction while it is hot.
#: Same socket, same owner, same single worker and deadline: nothing new is
#: opened and no rule is loosened -- each queued change is still read through
#: the cache's own 30 s freshness check and re-proved end to end by the
#: evaluation it triggers. The kickoff is the discovery seed's own
#: commence_time (a schedule; used only to ORDER work, never to classify a
#: quote's context).
HOT_SPORT_KEYS = frozenset(("americanfootball_nfl",))
HOT_BEFORE_KICKOFF_S = 6 * 3600
HOT_AFTER_KICKOFF_S = 4 * 3600


class Scheduler:
    """One worker, one deadline. HELD EVENTS FIRST: a held event's change is
    queued on `held_pending`, served before any discovery change, never
    evicted by discovery; its seed is pinned while held. HOT NFL EVENTS
    SECOND (R30A, `hot_pending`): an NFL game near kickoff. Discovery keeps
    its FIFO queue, cap and eviction exactly as before."""

    def __init__(self, cache, evaluate, audit, *, clock=time.time,
                 queue_cap=128, seed_cap=512, seed_ttl=1800, deadline=12,
                 held=None):
        from . import pinnapi_held as PH
        self.cache, self.evaluate, self.audit, self.clock = cache, evaluate, audit, clock
        self.queue_cap, self.seed_cap = queue_cap, seed_cap
        self.seed_ttl, self.deadline = seed_ttl, deadline
        self.seeds, self.pending, self.seen = OrderedDict(), OrderedDict(), {}
        self.held_pending = OrderedDict()
        self.hot_pending = OrderedDict()
        self.held = held if held is not None else PH.WATCH
        self.wake = asyncio.Event()
        self.counts = Counter()
        self.closed = False

    def _is_held(self, eid) -> bool:
        try:
            return bool(self.held.is_held(eid))
        except Exception:                                       # noqa: BLE001
            return False

    def _is_hot(self, eid, seed=None) -> bool:
        """An NFL game inside its hot window, from the discovery seed's own
        sport key and scheduled start. Never raises."""
        try:
            seed = seed if seed is not None else self.seeds.get(eid)
            if not seed or seed.get('sport_key') not in HOT_SPORT_KEYS:
                return False
            start = P.epoch((seed.get('event') or {}).get('commence_time'))
            if start is None:
                return False
            dt = start - float(self.clock())
            return -HOT_AFTER_KICKOFF_S <= dt <= HOT_BEFORE_KICKOFF_S
        except Exception:                                       # noqa: BLE001
            return False

    def _seed_live(self, eid, seed) -> bool:
        ttl = (HELD_SEED_TTL_S if (self._is_held(eid) or self._is_hot(eid, seed))
               else self.seed_ttl)
        return 0 <= self.clock() - seed['registered_at'] <= ttl

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
        self._evict_seeds()

    def _evict_seeds(self):
        while len(self.seeds) > self.seed_cap:
            # the oldest NON-HELD, NON-HOT seed goes; held and hot seeds
            # are pinned
            old = next((k for k in self.seeds if not self._is_held(k)
                        and not self._is_hot(k)), None)
            if old is None:
                break
            self.seeds.pop(old)
            self.pending.pop(old, None)
            self.seen.pop(old, None)
            self.counts['SEED_EVICTED'] += 1

    def changed(self, quote):
        if self.closed or quote.key != F.FULL_GAME_MONEYLINE_KEY:
            return
        eid = quote.event_id
        held = self._is_held(eid)
        seed = self.seeds.get(eid)
        if seed is None:
            self.counts['HELD_NO_DISCOVERY_SEED' if held
                        else 'NO_CONFIRMED_DISCOVERY'] += 1
            return
        if not self._seed_live(eid, seed):
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
        hot = (not held) and self._is_hot(eid, seed)
        tick = dict(version=version, received_at=quote.received_ms/1000,
                    queued_at=self.clock(), held=held, hot=hot)
        if held:
            # HELD FIRST: its own queue, never evicted by discovery, and a
            # change already queued for discovery is moved here
            self.pending.pop(eid, None)
            if eid in self.held_pending:
                self.counts['HELD_COALESCED'] += 1
            self.held_pending[eid] = tick
            self.counts['HELD_QUEUED'] += 1
            self.wake.set()
            return
        if hot:
            # HOT NFL SECOND: its own queue, never evicted by discovery
            self.pending.pop(eid, None)
            if eid in self.hot_pending:
                self.counts['HOT_COALESCED'] += 1
            self.hot_pending[eid] = tick
            self.counts['HOT_QUEUED'] += 1
            self.wake.set()
            return
        if eid in self.pending:
            self.counts['COALESCED'] += 1
        elif len(self.pending) >= self.queue_cap:
            old, _ = self.pending.popitem(last=False)
            self.seen.pop(old, None)
            self.counts['QUEUE_EVICTED'] += 1
        self.pending[eid] = tick
        self.wake.set()

    def request_held(self, eid) -> str:
        """A FRESH-EVIDENCE REQUEST for a HELD event (agents.work_queue,
        owner R30): queue the event's CURRENT quote on the held queue exactly
        as a change would have. Bounded by construction -- held events only,
        a live seed, a quote the cache reads as usable now (fresh within its
        30 s limit: a stale quote is never evaluated as if fresh), never a
        version already evaluated or already queued; the same single worker
        and deadline serve it. Returns what happened, by name; never
        raises."""
        if self.closed:
            return 'SCHEDULER_CLOSED'
        if not self._is_held(eid):
            return 'NOT_A_HELD_EVENT'
        seed = self.seeds.get(eid)
        if seed is None:
            self.counts['HELD_REQUEST_NO_DISCOVERY_SEED'] += 1
            return 'HELD_NO_DISCOVERY_SEED'
        if not self._seed_live(eid, seed):
            return 'DISCOVERY_EXPIRED'
        if eid in self.held_pending:
            return 'ALREADY_QUEUED'
        got = self.cache.read(eid, F.FULL_GAME_MONEYLINE_KEY,
                              evaluated_ms=self.clock()*1000)
        q = got.get('quote')
        if not got.get('ok') or q is None:
            self.counts['HELD_REQUEST_UNUSABLE'] += 1
            return str(got.get('reason') or 'UNUSABLE_QUOTE')
        version = (q.epoch, q.source_change_ms, tuple(sorted(q.prices.items())))
        if self.seen.get(eid) == version:
            self.counts['HELD_REQUEST_ALREADY_EVALUATED'] += 1
            return 'ALREADY_EVALUATED_THIS_VERSION'
        self.seen[eid] = version
        self.pending.pop(eid, None)
        self.hot_pending.pop(eid, None)
        self.held_pending[eid] = dict(version=version,
                                      received_at=q.received_ms/1000,
                                      queued_at=self.clock(), held=True,
                                      requested=True)
        self.counts['HELD_REQUESTED'] += 1
        self.wake.set()
        return 'QUEUED'

    def next_job(self):
        """(event id, tick) to evaluate next: held, then hot NFL, then
        discovery."""
        if self.held_pending:
            return self.held_pending.popitem(last=False)
        if self.hot_pending:
            return self.hot_pending.popitem(last=False)
        if self.pending:
            return self.pending.popitem(last=False)
        return None

    async def run(self):
        while not self.closed:
            await self.wake.wait()
            job = self.next_job()
            if job is None:
                self.wake.clear()
                continue
            eid, tick = job
            if not self.pending and not self.held_pending and \
                    not self.hot_pending:
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
                    not self._seed_live(eid, seed)):
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
                if tick.get('held'):
                    self.counts['HELD_' + attempt['state']] += 1
                elif tick.get('hot'):
                    self.counts['HOT_' + attempt['state']] += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                self.counts['AUDIT_FAILED'] += 1
                log.exception('pinnapi reactive audit failed; no unaudited evaluation started')


def request_held_reevaluation(slug) -> dict:
    """THE HELD RE-EVALUATION OF ONE HELD SLUG, on request (agents.
    work_queue's PROBABILITY request): the slug's provider event from the
    held watch, then `Scheduler.request_held`. In-process, no I/O, never
    raises; every refusal is named (not a held target, no scheduler in this
    process, no seed, a stale quote, already evaluated)."""
    from . import pinnapi_held as PH
    try:
        eid = PH.WATCH.slug_event.get(slug)
        if eid is None:
            return {"queued": False, "event_id": None,
                    "reason": PH.WATCH.unmatched.get(slug)
                    or "NOT_A_HELD_TARGET_IN_THIS_PROCESS"}
        if ACTIVE is None:
            return {"queued": False, "event_id": eid,
                    "reason": "REACTIVE_SCHEDULER_NOT_RUNNING"}
        why = ACTIVE.request_held(eid)
        return {"queued": why == 'QUEUED', "event_id": eid, "reason": why}
    except Exception as exc:                                    # noqa: BLE001
        return {"queued": False, "reason": "REQUEST_FAILED",
                "error": type(exc).__name__}


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
        # DECISION-TIME READS TAKE THE PRIORITY LANE of this process's venue
        # gate (venue_pace E11): a WS-triggered evaluation is one event, one
        # at a time, under a 12 s deadline, and its book reads must not queue
        # behind the periodic collector's bulk reads. The gap and the 429
        # circuit are unchanged; starvation is bounded by PACE_PRIORITY_BURST.
        from . import venue_pace
        async with pool.acquire() as conn:
            with venue_pace.priority_claims():
                return await cycle(conn, stream_seed=seed)

    # WHICH PROCESS WROTE THE ATTEMPT: the deployed build and this feed
    # owner's runtime, so a readback proves the writer -- not merely the web
    # service -- runs the released code.
    writer = dict(build=os.environ.get('RENDER_GIT_COMMIT'), pid=os.getpid(),
                  runtime_id=runtime._STATE.get('runtime_id'))

    async def audit(attempt):
        async with pool.acquire() as conn:
            await conn.execute('''INSERT INTO pinnapi_reactive_attempts
                (attempt_id, event_id, state, detail) VALUES ($1,$2,$3,$4::jsonb)
                ON CONFLICT (attempt_id) DO UPDATE SET
                state=EXCLUDED.state, detail=EXCLUDED.detail, updated_at=now()''',
                attempt['attempt_id'], str(attempt['event_id']), attempt['state'],
                json.dumps(dict(attempt, writer=writer), default=str))

    ACTIVE = Scheduler(owner.cache, evaluate, audit)
    owner.cache.on_change = ACTIVE.changed
    # the held watch stays first on the change notification (priority
    # targets and review triggers), the scheduler after it
    from . import pinnapi_held as PH
    PH.reinstall_if_installed(owner.cache)
    return asyncio.create_task(ACTIVE.run(), name='pinnapi-reactive-paper')


async def stop(task):
    global ACTIVE
    scheduler, ACTIVE = ACTIVE, None
    if scheduler is not None:
        scheduler.closed = True
        scheduler.cache.on_change = None
        scheduler.pending.clear()
        scheduler.held_pending.clear()
        scheduler.hot_pending.clear()
        from . import pinnapi_held as PH
        # the held watch keeps watching (on the feed runtime's cache only)
        PH.reinstall_if_installed(scheduler.cache)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
