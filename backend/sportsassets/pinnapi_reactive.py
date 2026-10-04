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

#: THE JOB'S CONNECTION (R30A, 2026-10-04). Production, 15:46-18:47Z: the
#: audit failed 14 times with TimeoutError inside asyncpg Pool._acquire --
#: the audit took its OWN connection twice per job (STARTED, then the
#: completion) under a 2 s budget, the evaluation a third, from an API pool
#: whose ten slots were six-held by single-writer loops (research-sql run
#: 37226381750). A failed first audit dropped a fresh change unevaluated; a
#: failed second one left the attempt STARTED for ever with its valuation ids
#: lost -- 19 such rows in 24 h, 13 of them HELD positions (research-sql run
#: 37226551461). Now ONE connection is taken per job, within SESSION_WAIT_S,
#: and both audits and the evaluation run on it. A job that gets no
#: connection in time is refused before anything starts, counted
#: SESSION_UNAVAILABLE and logged -- the same fail-closed rule as before
#: (no unaudited evaluation), now one acquire instead of three.
SESSION_WAIT_S = 2.0
#: the completion audit's own budget, and its one retry on a fresh connection
#: when the job's connection cannot take it (a cancelled statement can leave
#: that connection unusable)
AUDIT_S = 2.0


class Scheduler:
    """One worker, one deadline. HELD EVENTS FIRST: a held event's change is
    queued on `held_pending`, served before any discovery change, never
    evicted by discovery; its seed is pinned while held. Discovery keeps its
    FIFO queue, cap and eviction exactly as before."""

    def __init__(self, cache, evaluate, audit, *, clock=time.time,
                 queue_cap=128, seed_cap=512, seed_ttl=1800, deadline=12,
                 held=None, session=None, session_wait=SESSION_WAIT_S):
        from . import pinnapi_held as PH
        self.cache, self.evaluate, self.audit, self.clock = cache, evaluate, audit, clock
        # ONE CONNECTION PER JOB (R30A), when `session` is given: a callable
        # returning an async context manager that yields a connection. Both
        # audit records and the evaluation run on it -- see `run`.
        self.session, self.session_wait = session, float(session_wait)
        self.queue_cap, self.seed_cap = queue_cap, seed_cap
        self.seed_ttl, self.deadline = seed_ttl, deadline
        self.seeds, self.pending, self.seen = OrderedDict(), OrderedDict(), {}
        self.held_pending = OrderedDict()
        self.held = held if held is not None else PH.WATCH
        self.wake = asyncio.Event()
        self.counts = Counter()
        self.closed = False

    def _is_held(self, eid) -> bool:
        try:
            return bool(self.held.is_held(eid))
        except Exception:                                       # noqa: BLE001
            return False

    def _seed_live(self, eid, seed) -> bool:
        ttl = HELD_SEED_TTL_S if self._is_held(eid) else self.seed_ttl
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
            # the oldest NON-HELD seed goes; a held event's seed is pinned
            old = next((k for k in self.seeds if not self._is_held(k)), None)
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
        tick = dict(version=version, received_at=quote.received_ms/1000,
                    queued_at=self.clock(), held=held)
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
        self.held_pending[eid] = dict(version=version,
                                      received_at=q.received_ms/1000,
                                      queued_at=self.clock(), held=True,
                                      requested=True)
        self.counts['HELD_REQUESTED'] += 1
        self.wake.set()
        return 'QUEUED'

    def next_job(self):
        """(event id, tick) to evaluate next: held before discovery."""
        if self.held_pending:
            return self.held_pending.popitem(last=False)
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
            if not self.pending and not self.held_pending:
                self.wake.clear()
            if self.session is not None:
                await self._run_job_on_one_session(eid, tick)
                continue
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
            except asyncio.CancelledError:
                raise
            except Exception:
                self.counts['AUDIT_FAILED'] += 1
                log.exception('pinnapi reactive audit failed; no unaudited evaluation started')


async def _complete_audit(scheduler, attempt, conn):
    """The completion record: on the job's connection, else once on a fresh
    one. Raises when neither takes it (the caller counts AUDIT_FAILED)."""
    try:
        async with asyncio.timeout(AUDIT_S):
            await scheduler.audit(attempt, conn)
        return
    except asyncio.CancelledError:
        raise
    except Exception:                                           # noqa: BLE001
        scheduler.counts['COMPLETION_AUDIT_RETRIED'] += 1
    async with asyncio.timeout(scheduler.session_wait + AUDIT_S):
        async with scheduler.session() as fresh:
            await scheduler.audit(attempt, fresh)


async def _run_job_on_one_session(self, eid, tick):
    """One job, one connection (see SESSION_WAIT_S): STARTED audit,
    evaluation and completion audit all on it."""
    import contextlib
    t0 = self.clock()
    attempt = dict(attempt_id=uuid.uuid4().hex, event_id=eid, **tick,
                   evaluation_started_at=t0, state='STARTED',
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
        async with contextlib.AsyncExitStack() as stack:
            try:
                async with asyncio.timeout(self.session_wait):
                    conn = await stack.enter_async_context(self.session())
            except TimeoutError:
                # refused before anything started: no audit row, no
                # evaluation -- the change is re-queued by the next one
                self.counts['SESSION_UNAVAILABLE'] += 1
                if tick.get('held'):
                    self.counts['HELD_SESSION_UNAVAILABLE'] += 1
                log.warning('pinnapi reactive: no connection within %ss for '
                            'event %s (held=%s); not evaluated',
                            self.session_wait, eid, bool(tick.get('held')))
                return
            attempt['session_wait_s'] = round(self.clock() - t0, 3)
            # Audit failure blocks execution, it never becomes an unaudited trade.
            async with asyncio.timeout(AUDIT_S):
                await self.audit(attempt, conn)
            if attempt['state'] != 'STARTED':
                return
            job = copy.deepcopy(seed)
            job.update(valuation_ids=[], trigger=attempt)
            try:
                async with asyncio.timeout(self.deadline):
                    result = await self.evaluate(job, conn)
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
                await _complete_audit(self, attempt, conn)
        self.counts[attempt['state']] += 1
        if tick.get('held'):
            self.counts['HELD_' + attempt['state']] += 1
    except asyncio.CancelledError:
        raise
    except Exception:
        self.counts['AUDIT_FAILED'] += 1
        log.exception('pinnapi reactive audit failed; no unaudited evaluation started')


Scheduler._run_job_on_one_session = _run_job_on_one_session


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

    async def evaluate(seed, conn):
        # DECISION-TIME READS TAKE THE PRIORITY LANE of this process's venue
        # gate (venue_pace E11): a WS-triggered evaluation is one event, one
        # at a time, under a 12 s deadline, and its book reads must not queue
        # behind the periodic collector's bulk reads. The gap and the 429
        # circuit are unchanged; starvation is bounded by PACE_PRIORITY_BURST.
        # ON THE JOB'S CONNECTION (SESSION_WAIT_S), the one both audits use.
        from . import venue_pace
        with venue_pace.priority_claims():
            return await cycle(conn, stream_seed=seed)

    # WHICH PROCESS WROTE THE ATTEMPT: the deployed build and this feed
    # owner's runtime, so a readback proves the writer -- not merely the web
    # service -- runs the released code.
    writer = dict(build=os.environ.get('RENDER_GIT_COMMIT'), pid=os.getpid(),
                  runtime_id=runtime._STATE.get('runtime_id'))

    async def audit(attempt, conn):
        await conn.execute('''INSERT INTO pinnapi_reactive_attempts
            (attempt_id, event_id, state, detail) VALUES ($1,$2,$3,$4::jsonb)
            ON CONFLICT (attempt_id) DO UPDATE SET
            state=EXCLUDED.state, detail=EXCLUDED.detail, updated_at=now()''',
            attempt['attempt_id'], str(attempt['event_id']), attempt['state'],
            json.dumps(dict(attempt, writer=writer), default=str))

    ACTIVE = Scheduler(owner.cache, evaluate, audit,
                       session=lambda: pool.acquire())
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
        from . import pinnapi_held as PH
        # the held watch keeps watching (on the feed runtime's cache only)
        PH.reinstall_if_installed(scheduler.cache)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
