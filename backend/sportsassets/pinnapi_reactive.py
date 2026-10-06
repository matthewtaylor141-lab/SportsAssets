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
#: THE HOT TIER'S SHARE IS BOUNDED (R30A review). Strict priority let an NFL
#: Sunday -- a dozen-plus games hot all day, each re-queued on every change
#: -- hold every MLB and soccer discovery change behind it on the single
#: worker, which changes other sports' behaviour. While a discovery change
#: is waiting, at most HOT_MAX_CONSECUTIVE hot jobs run before one discovery
#: job is served: a waiting discovery change is evaluated within
#: HOT_MAX_CONSECUTIVE + 1 non-held jobs, and discovery keeps at least a
#: third of the non-held throughput whenever both queues hold work. With no
#: discovery change waiting, the hot tier runs freely (it starves nobody).
#: The hot queue itself is bounded by the number of NFL events inside their
#: window (one entry per event, later changes coalesce). Held events still
#: go first, exactly as before R30A.
HOT_MAX_CONSECUTIVE = 2
#: ── DISCOVERY CHANGES: NEWEST FIRST (P0 first-loss, 2026-10-06) ──────────
#: MEASURED (research-sql run 37413249749): of the PinnAPI valuations the
#: lane refused FEED_QUOTE_OLDER_THAN_LIMIT in 24 h (the code behind
#: PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE on the census's NPB and
#: NBA events), the change reached the queue 0.1-0.3 s after it happened,
#: then WAITED in this FIFO queue 22-25 s (p50; p90 25-29 s) for the single
#: worker before its evaluation started, and the evaluation (its paced venue
#: read) took ~10 s: 32-36 s old at the decision. NPB Hanshin-Hiroshima:
#: queued 15:39:13.28Z, started 15:39:35.28Z, refused at 33.3 s. NBA
#: Bucks-Timberwolves spread: queued 22:57:48.29Z, started 22:58:17.04Z,
#: refused at 32.3 s. The quote was fresh when it arrived; our queue made
#: it stale -- every job served oldest-first starts ~one queue-wait old.
#: Served NEWEST first, a change starts within a job of arriving, and an
#: older one the worker could not reach in time is refused at its start by
#: the cache's own 30 s read (unchanged; nothing is forecast or skipped
#: early) -- the same work, spent on changes that can still be decided
#: fresh. Discovery only: held events keep their own first-served queue and
#: the hot NFL tier its FIFO and bounded share. The cap still evicts the
#: OLDEST entry. No limit, clock or worker count changes.
DISCOVERY_NEWEST_FIRST_RULE = (
    "discovery changes are evaluated newest first: an older change the "
    "single worker could not reach in time is refused at its start by the "
    "cache's unchanged 30 s read, instead of making every later change wait "
    "behind it; held events and the hot NFL tier keep their own order")
#: THE JOB'S CONNECTION (R30A, 2026-10-04). Production, 15:47-18:47Z
#: (render-ops logs run 37225745383): the audit failed 12 times -- 10 with
#: the TimeoutError inside asyncpg Pool._acquire, 2 (16:00:53, 17:46:10)
#: inside the INSERT after the connection was acquired. The audit took its
#: OWN connection twice per job (STARTED, then the completion) under ONE 2 s
#: budget covering both the wait and the statement, the evaluation a third,
#: from an API pool whose ten slots were six-held by single-writer loops
#: (research-sql run 37226381750). The INSERT's server time never exceeded
#: 12 ms (pg_stat_statements: 4,139 calls, mean 0.17 ms; research-sql run
#: 37231484481), so the two in-statement timeouts were the client's budget
#: expiring around it: a wait in acquire that left the statement almost no
#: time, and the API event loop held 2-4 s at a time in the same minutes
#: (the loop watchdog; fixed at the sources, tests/test_r30a_loop_stalls.py).
#: A failed first audit dropped a fresh change unevaluated; a failed second
#: one left the attempt STARTED for ever with its valuation ids lost -- 19
#: such rows in 24 h, 13 of them HELD positions (research-sql run
#: 37226551461). Now ONE connection is taken per job, within SESSION_WAIT_S,
#: and both audits and the evaluation run on it, each statement with its
#: own budget (the wait no longer eats the statement's). A job that gets no
#: connection in time is refused before anything starts, counted
#: SESSION_UNAVAILABLE and logged -- the same fail-closed rule as before
#: (no unaudited evaluation), now one acquire instead of three.
SESSION_WAIT_S = 2.0
#: the completion audit's own budget, and its one retry on a fresh connection
#: when the job's connection cannot take it (a cancelled statement can leave
#: that connection unusable)
AUDIT_S = 2.0
#: THE FENCE'S BUDGET (R30A review). The writer-lock proof on the job's
#: connection is one pg_locks read; unanswered within FENCE_S it is a
#: refusal (FENCED_OUT, counted FENCE_UNANSWERED, logged) -- before, a
#: stalled read blocked the one reactive worker for ever, uncounted.
FENCE_S = 2.0
#: THE WORST CASE ONE JOB CAN TAKE, every bound spent in full: the session
#: wait, the fence, the STARTED audit, the evaluation deadline, the
#: completion audit, then its retry on a fresh session (wait + audit).
#: = 2 + 2 + 2 + 12 + 2 + (2 + 2) = 24 s with the defaults; a test pins it.
def worst_case_job_s(deadline: float = 12.0, session_wait: float = SESSION_WAIT_S
                     ) -> float:
    return (session_wait + FENCE_S + AUDIT_S + float(deadline) + AUDIT_S
            + session_wait + AUDIT_S)
#: HOW MANY FIXTURES MAY HOLD A DISCOVERY SEED (R30A P0 incident). 512 was
#: sized for the metered cycle's few competitions. PinnAPI-native discovery
#: now seeds EVERY matched fixture of six subscribed sports each census pass
#: (pinnapi_feed_runtime._discovery_once), and the measured six-sport load is
#: ~1,750 cached fixtures (pinnapi_feed "CAPACITY FOR THE R30A SCOPE"). Past
#: the cap, each pass's later registrations evict its earlier ones in the
#: same iteration order every time, so the same fixtures would be unseeded
#: for good -- a silent loss (counted only as SEED_EVICTED). The cap now
#: covers the cache's own event bound, the same order of magnitude; a seed
#: is one copied discovery event (~1-2 KB), ~8 MB at the cap. A memory bound,
#: not a threshold: no evaluation, freshness or economic rule reads it.
SEED_CAP = 4000


def version_of(q) -> tuple:
    """A quote's evaluation version: (epoch, change instant, prices). The
    change instant is the one the 30 s rule measures from (the provider
    stamp, or our labelled observation of an unstamped change -- R30A RC4),
    so a change the cache dates either way is a new version."""
    return (q.epoch, getattr(q, "change_ms", q.source_change_ms),
            tuple(sorted(q.prices.items())))


def fixture_of(cache, quote):
    """The fixture a changed quote prices (R30A RC3): a live-phase child's
    change is its prematch parent's, which is what seeds are keyed on."""
    fid = getattr(quote, "fixture_id", None)
    if fid is not None:
        return fid
    canon = getattr(cache, "canonical_id", None)
    return canon(quote.event_id) if callable(canon) else quote.event_id


def _quote_record(cache, eid):
    """(the record that prices fixture `eid` now, None) or (None, reason)."""
    fq = getattr(cache, "fixture_quote_id", None)
    if not callable(fq):
        return eid, None
    return fq(eid)


class Scheduler:
    """One worker, one deadline. HELD EVENTS FIRST: a held event's change is
    queued on `held_pending`, served before any discovery change, never
    evicted by discovery; its seed is pinned while held. HOT NFL EVENTS
    SECOND (R30A, `hot_pending`): an NFL game near kickoff, ahead of
    discovery for at most HOT_MAX_CONSECUTIVE jobs in a row while discovery
    waits. Discovery keeps its FIFO queue, cap and eviction exactly as
    before."""

    def __init__(self, cache, evaluate, audit, *, clock=time.time,
                 queue_cap=128, seed_cap=SEED_CAP, seed_ttl=1800,
                 deadline=12,
                 held=None, session=None, session_wait=SESSION_WAIT_S):
        from . import pinnapi_held as PH
        self.cache, self.evaluate, self.audit, self.clock = cache, evaluate, audit, clock
        # ONE CONNECTION PER JOB (R30A), when `session` is given: a callable
        # returning an async context manager that yields a connection. Both
        # audit records and the evaluation run on it -- see `run`.
        self.session, self.session_wait = session, float(session_wait)
        # CHILD FENCING (R30A): set by ext_pinnacle_loop.run to an async
        # callable(conn) -> bool that re-proves, on the job's connection, that
        # the decider's writer backend still holds its lock. None: no fence.
        self.fence = None
        self.queue_cap, self.seed_cap = queue_cap, seed_cap
        self.seed_ttl, self.deadline = seed_ttl, deadline
        self.seeds, self.pending, self.seen = OrderedDict(), OrderedDict(), {}
        self.held_pending = OrderedDict()
        self.hot_pending = OrderedDict()
        self.hot_streak = 0
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

    def register(self, event, *, sport_key, family, received_at,
                 native=False, index=None):
        """Seed one fixture. Called after competition confirmation (the
        metered cycle) or after a MATCHED PinnAPI-native discovery receipt
        (`native`, pinnapi_discovery). A native registration never replaces
        a live seed the metered cycle registered for the same fixture WITH
        BOOKS: that seed's event carries independent books that corroborate
        the price. A book-less seed (the unmetered `/events` refresh) never
        replaces a live native one, and a native one replaces it.
        `index` (`pinnapi_primary.fixture_index` of this cache, built once by
        a pass that registers many seeds) spares a full fixture-view rebuild
        per seed. Returns what happened, by name."""
        hit, why = P.match_event(self.cache, event, family, index=index)
        if why:
            self.counts[why] += 1
            return why
        if len(json.dumps(event, default=str)) > 16384:
            self.counts['SEED_TOO_LARGE'] += 1
            return 'SEED_TOO_LARGE'
        eid = hit[0]
        old = self.seeds.get(eid)
        old_ev = (old or {}).get('event') or {}
        live = old is not None and self._seed_live(eid, old)
        # THE METERED SEED WINS ONLY WHEN IT CARRIES BOOKS (incident release,
        # verifier finding 1). The collector's unmetered `/events` refresh
        # registers book-less seeds as metered (native=False); on the premise
        # above they displaced native seeds -- which carry the discovery's
        # venue identity -- and lost to none, so the seeder that ran last
        # decided the fixture's identity and ledger key. A book-less seed
        # corroborates nothing: it neither displaces a live native seed nor
        # survives one, whichever registers first.
        if (native and live and not old_ev.get('pinnapi_native')
                and old_ev.get('bookmakers')):
            self.counts['NATIVE_KEPT_METERED_SEED'] += 1
            return 'NATIVE_KEPT_METERED_SEED'
        if (not native and live and old_ev.get('pinnapi_native')
                and not (event or {}).get('bookmakers')):
            self.counts['BOOKLESS_SEED_KEPT_NATIVE_SEED'] += 1
            return 'BOOKLESS_SEED_KEPT_NATIVE_SEED'
        if native:
            self.counts['NATIVE_SEEDED'] += 1
        self.seeds[eid] = dict(event=copy.deepcopy(event), sport_key=sport_key,
                               family=family, received_at=received_at,
                               registered_at=self.clock())
        self.seeds.move_to_end(eid)
        self._evict_seeds()
        return 'SEEDED' if eid in self.seeds else 'SEED_EVICTED'

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
        eid = fixture_of(self.cache, quote)
        held = self._is_held(eid)
        seed = self.seeds.get(eid)
        if seed is None:
            self.counts['HELD_NO_DISCOVERY_SEED' if held
                        else 'NO_CONFIRMED_DISCOVERY'] += 1
            return
        if not self._seed_live(eid, seed):
            self.counts['DISCOVERY_EXPIRED'] += 1
            return
        got = self.cache.read(quote.event_id, quote.key,
                              evaluated_ms=self.clock()*1000)
        if not got.get('ok'):
            self.counts['UNUSABLE_CHANGE'] += 1
            # WHICH refusal, not only that there was one (incident RC7)
            self.counts['UNUSABLE_CHANGE:%s' % got.get('reason')] += 1
            return
        version = version_of(quote)
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
        # NEWEST CHANGE FIRST (DISCOVERY_NEWEST_FIRST_RULE): a coalesced
        # change is this fixture's newest, so it moves to the newest end
        self.pending.move_to_end(eid)
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
        qid, why = _quote_record(self.cache, eid)
        got = (self.cache.read(qid, F.FULL_GAME_MONEYLINE_KEY,
                               evaluated_ms=self.clock()*1000)
               if why is None else {'ok': False, 'reason': why})
        q = got.get('quote')
        if not got.get('ok') or q is None:
            self.counts['HELD_REQUEST_UNUSABLE'] += 1
            return str(got.get('reason') or 'UNUSABLE_QUOTE')
        version = version_of(q)
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
        """(event id, tick) to evaluate next: held first; then hot NFL ahead
        of discovery, but never more than HOT_MAX_CONSECUTIVE hot jobs in a
        row while a discovery change waits; then discovery."""
        if self.held_pending:
            return self.held_pending.popitem(last=False)
        if self.hot_pending:
            if not self.pending:
                self.hot_streak = 0
                return self.hot_pending.popitem(last=False)
            if self.hot_streak < HOT_MAX_CONSECUTIVE:
                self.hot_streak += 1
                return self.hot_pending.popitem(last=False)
            self.counts['HOT_YIELDED_TO_DISCOVERY'] += 1
        if self.pending:
            self.hot_streak = 0
            # DISCOVERY_NEWEST_FIRST_RULE: the newest change, not the oldest
            return self.pending.popitem(last=True)
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
            if self.session is not None:
                await self._run_job_on_one_session(eid, tick)
                continue
            # `deadline_s` travels with the job so work the evaluation may
            # add (the line-market lane) is never started when it cannot
            # finish inside this same deadline (ext_pinnacle_loop.
            # line_market_pass): the money-line evaluation is never turned
            # into a TIMEOUT by it.
            attempt = dict(attempt_id=uuid.uuid4().hex, event_id=eid, **tick,
                           evaluation_started_at=self.clock(), state='STARTED',
                           deadline_s=self.deadline,
                           counters=dict(self.counts))
            seed = self.seeds.get(eid)
            qid, why = _quote_record(self.cache, eid)
            got = (self.cache.read(qid, F.FULL_GAME_MONEYLINE_KEY,
                                   evaluated_ms=self.clock()*1000)
                   if why is None else {'ok': False, 'reason': why})
            q = got.get('quote')
            version = None if q is None else version_of(q)
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
    # (integration) the same reads the single-connection path makes since
    # inc-pinnapi: the fixture's CURRENT pricing record (its live-phase
    # child while in play, R30A RC3), the change instant the 30 s rule
    # measures from (`version_of`, RC4), and `deadline_s` travelling with
    # the job so the line-market lane never starts work it cannot finish
    # inside this deadline. Production runs THIS path (`start` passes
    # `session`), so without them the stream's fixes would not run there.
    attempt = dict(attempt_id=uuid.uuid4().hex, event_id=eid, **tick,
                   evaluation_started_at=t0, state='STARTED',
                   deadline_s=self.deadline,
                   counters=dict(self.counts))
    seed = self.seeds.get(eid)
    qid, why = _quote_record(self.cache, eid)
    got = (self.cache.read(qid, F.FULL_GAME_MONEYLINE_KEY,
                           evaluated_ms=self.clock()*1000)
           if why is None else {'ok': False, 'reason': why})
    q = got.get('quote')
    version = None if q is None else version_of(q)
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
            if self.fence is not None:
                try:
                    async with asyncio.timeout(FENCE_S):
                        fenced_in = await self.fence(conn)
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    self.counts['FENCE_UNANSWERED'] += 1
                    fenced_in = False
                except Exception:                               # noqa: BLE001
                    fenced_in = False
                if not fenced_in:
                    # nothing evaluated and nothing audited: a process that
                    # is not the writer must not write a decision
                    self.counts['FENCED_OUT'] += 1
                    log.error('pinnapi reactive: event %s refused: the '
                              'writer lock is not held by this process\'s '
                              'writer backend', eid)
                    return
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
        return ACTIVE.register(event, **kwargs)
    return None


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
