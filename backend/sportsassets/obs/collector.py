"""RN1 forward-observability collector -- run 83.2.

WHAT CHANGED FROM V1, AND WHY EACH CHANGE WAS FORCED BY EVIDENCE.
RUN83_ACTIVATION_FAILED_V1 captured 337 of 125,270 scheduled observations
(0.27%), with ZERO captures at each of 0, 100, 250, 500 ms, 1 s and 2 s. Run 83.1
found two independent causes, and either one alone would still have left a broken
instrument:

  1. NO POPULATION FILTER. observe() checked one thing -- is the shadow on -- and
     the hook never passed the whale identity it already held. 94.671% of the
     cohort belonged to the other nineteen wallets on the ingestion roster, and
     94.6% were fills the ledger had already held for up to 36.9 days,
     re-presented by the poll lane's page walk. Fixed by stamp()/admit(): the
     subject is screened on an immutable id and admission waits for the
     canonical trades dedupe.

  2. THE SEMAPHORE WAS A SCHEDULER. Each event held a slot of
     Semaphore(max_inflight()) for its whole 60-second horizon, capping the
     collector at 8/60 = 0.133 events/s against an RN1-only arrival rate of
     0.187-0.255/s. Fixed by obs/scheduler.py: a pending slot is a heap entry
     and holds no concurrency.

  3. THE SCHEDULED OPERATION MADE A NETWORK REQUEST. A request started after
     receipt cannot observe receipt-time state -- measured p50 duration was
     170 ms, so the 0 ms and 100 ms offsets were inside the round trip. Fixed by
     obs/cache.py: the scheduled operation reads local state and does no I/O.
     The HTTP path remains, as a secondary diagnostic channel only.

INERT UNTIL SWITCHED ON, and now inert twice over: RN1_OBSERVABILITY_SHADOW must
be on AND RN1_OBSERVABILITY_SUBJECT_WHALE_ID must name a subject. With either
unset, main() logs one line and PARKS FOREVER.

IT PARKS RATHER THAN RETURNS, and that is not stylistic -- it was a production
defect (2026-09-12, found in the run 83 deployment gate). Every other entry in
workers/all.py's LOOPS is a `while True` that only ever ends by raising, so the
supervisor reads a CLEAN RETURN as an anomaly: it logs a WARNING and restarts
after RESTART_DELAY_SECONDS. An inert main() that returned span twelve times a
minute, burying the worker log under about 17,000 warnings a day.

MEASUREMENT ONLY. This worker never places, cancels or touches an order. It has
no order path at all: tests/test_obs_safety.py walks its import graph and fails
the build if one appears.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field


from . import cache as _cache
from . import clock, record, scheduler
from .book import ObservationChannel
from .config import (COLLECTOR_VERSION, cache_hot_tokens, clob_base,
                     max_inflight, reads_per_second, shadow_enabled)
from .subject import AdmissionReason, admit as _subject_admit
from .subject import configured_subject_whale_id

log = logging.getLogger(__name__)

QUEUE_MAX = 512

_QUEUE: asyncio.Queue | None = None
_LOOP: asyncio.AbstractEventLoop | None = None

STATS: dict[str, int] = {
    "stamped": 0, "admitted": 0, "dropped_disabled": 0, "dropped_no_loop": 0,
    "dropped_not_configured": 0, "dropped_not_subject": 0,
    "dropped_not_first_receipt": 0, "dropped_no_whale_id": 0,
    "dropped_queue_full": 0, "events_written": 0, "events_duplicate": 0,
    "slots_planned": 0, "samples_captured": 0, "samples_missed": 0,
    "samples_cache_miss": 0, "samples_cache_invalid": 0,
    "samples_restarted": 0, "stamp_exceptions": 0, "admit_exceptions": 0,
}

_DROP_STAT = {
    AdmissionReason.NOT_CONFIGURED: "dropped_not_configured",
    AdmissionReason.NOT_SUBJECT: "dropped_not_subject",
    AdmissionReason.NOT_FIRST_RECEIPT: "dropped_not_first_receipt",
    AdmissionReason.NO_WHALE_ID: "dropped_no_whale_id",
}


@dataclass
class PendingObservation:
    """Stamped at arrival, not yet admitted.

    receipt IS THE ANCHOR and is never replaced. admission is stamped later,
    after the canonical insert, and is carried separately so the insert's cost
    is visible and can never be folded into the anchor.
    """

    receipt: clock.Instant
    source_event_id: str
    source_lane: str
    source_venue: str
    source_token_id: str | None
    source_ts: clock.SourceTimestamp
    ingest_worker: str
    subject_whale_id: int | None
    subject_username: str | None
    fields: dict = field(default_factory=dict)
    admission: clock.Instant | None = None
    admission_delay_ms: float | None = None


# --------------------------------------------------------------- the two hooks
def stamp(*, source_event_id: str, source_lane: str, source_venue: str,
          source_token_id: str | None, source_ts: clock.SourceTimestamp,
          ingest_worker: str, subject_whale_id: int | None = None,
          subject_username: str | None = None, **fields
          ) -> PendingObservation | None:
    """STEP 1-2: stamp the anchor, screen the subject. Never raises, no I/O.

    Returns None for everything that cannot become an observation, so the
    ingestion path carries no state for the 94.7% of events that are not the
    subject's.

    THE STAMP IS TAKEN BEFORE THE SUBJECT CHECK. The anchor must not be a
    function of how long a branch took. It costs two syscalls.
    """
    try:
        if not shadow_enabled():
            STATS["dropped_disabled"] += 1
            return None
        if _QUEUE is None:
            STATS["dropped_no_loop"] += 1
            return None
        receipt = clock.now()              # <- FIRST_BETTOR_RECEIPT_*, the anchor
        STATS["stamped"] += 1

        # Screen on the subject alone here. was_insert does not exist yet -- it
        # is produced by the INSERT that has not run -- so this is deliberately
        # only the first half of the admission test. The full test, including
        # first-receipt, runs in admit().
        subject = configured_subject_whale_id()
        if subject is None:
            STATS["dropped_not_configured"] += 1
            return None
        if subject_whale_id is None:
            STATS["dropped_no_whale_id"] += 1
            return None
        if int(subject_whale_id) != subject:
            STATS["dropped_not_subject"] += 1
            return None

        return PendingObservation(
            receipt=receipt,
            source_event_id=source_event_id,
            source_lane=source_lane,
            source_venue=source_venue,
            source_token_id=source_token_id,
            source_ts=source_ts,
            ingest_worker=ingest_worker,
            subject_whale_id=int(subject_whale_id),
            subject_username=subject_username,
            fields=fields,
        )
    except Exception:                      # noqa: BLE001 -- never reach the caller
        STATS["stamp_exceptions"] += 1
        return None


def admit(pending: PendingObservation | None, *, was_insert: bool) -> bool:
    """STEP 4: admit iff subject AND canonical first receipt. Never raises.

    was_insert is the answer from the production INSERT's `RETURNING (xmax = 0)`
    and is used verbatim. Nothing here recomputes it -- owner decision 4 rejected
    a second first-receipt authority, and re-deriving the same fact under a
    different name would be that authority with extra steps.
    """
    if pending is None:
        return False
    try:
        ok, reason = _subject_admit(whale_id=pending.subject_whale_id,
                                    was_insert=was_insert)
        if not ok:
            STATS[_DROP_STAT.get(reason, "dropped_not_subject")] += 1
            return False
        admission = clock.now()
        pending.admission = admission
        pending.admission_delay_ms = round(
            (admission.monotonic - pending.receipt.monotonic) * 1000.0, 3)
        q = _QUEUE
        if q is None:
            STATS["dropped_no_loop"] += 1
            return False
        try:
            q.put_nowait(pending)
            STATS["admitted"] += 1
            return True
        except asyncio.QueueFull:
            STATS["dropped_queue_full"] += 1
            return False
    except Exception:                      # noqa: BLE001
        STATS["admit_exceptions"] += 1
        return False


# ------------------------------------------------------------------ pacing
class Pacer:
    """A token bucket over this collector's own HTTP requests.

    Governs the cache refresher and the secondary diagnostic channel. It does
    NOT govern the primary channel, which makes no requests -- and that is the
    point of the V2 architecture rather than an omission here.

    The old docstring cited "the venue has 429'd a board walk above roughly
    3 req/s". That figure was traced by `git log -S` to run 83's own commits and
    nowhere else; no 429 against clob.polymarket.com is recorded anywhere in this
    repository. The limit is UNESTABLISHED and is not quoted as if measured.
    """

    def __init__(self, rps: float) -> None:
        self._min_gap = 1.0 / max(rps, 0.1)
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self._min_gap
        if delay > 0:
            await asyncio.sleep(delay)


# ------------------------------------------------------------------ the loop
async def _admit_loop(pool, sched: scheduler.DueTimeScheduler,
                      mcache: _cache.MarketStateCache) -> None:
    """Drain admitted events: write the event row, write the plan, promote.

    THE WHOLE PLAN IS WRITTEN BEFORE THE FIRST SAMPLE. That is what makes a
    missing result distinguishable from a slot that never existed -- the gap V1
    could not express, where 125,270 rows held 337 measurements and no query
    could tell an attempted miss from an unvisited offset.
    """
    assert _QUEUE is not None
    while True:
        pending: PendingObservation = await _QUEUE.get()
        try:
            obs_event_id = record.new_event_id()
            written = await record.insert_event_v2(pool, obs_event_id=obs_event_id,
                                                   pending=pending)
            if not written:
                STATS["events_duplicate"] += 1
                continue
            STATS["events_written"] += 1

            mcache.promote(pending.source_token_id, cache_hot_tokens())
            slots = scheduler.plan_for_event(
                obs_event_id=obs_event_id,
                source_event_id=pending.source_event_id,
                receipt=pending.receipt,
                channel=ObservationChannel.LOCAL_CACHE_BATCH_POLL_PATH,
                source_token_id=pending.source_token_id,
                source_market_id=pending.fields.get("source_market_id"),
            )
            await record.insert_plan(pool, slots)
            STATS["slots_planned"] += len(slots)
            sched.push_all(slots)
        except asyncio.CancelledError:
            raise
        except Exception:                  # noqa: BLE001
            log.exception("rn1 obs: admitting %s failed", pending.source_event_id)


async def _sample_loop(pool, sched: scheduler.DueTimeScheduler,
                       mcache: _cache.MarketStateCache) -> None:
    """THE SCHEDULED OPERATION. Local, no I/O, one task for the whole heap."""
    while True:
        await sched.wait_until_next()
        ready = sched.pop_ready(time.monotonic())
        if not ready:
            continue
        rows = []
        for slot in ready:
            sample = mcache.sample(slot.source_token_id)
            rows.append(scheduler.resolve_local(slot, sample))
        for row in rows:
            status = row["status"]
            if status == scheduler.SampleStatus.CAPTURED_LOCAL:
                STATS["samples_captured"] += 1
            elif status == scheduler.SampleStatus.CACHE_MISS:
                STATS["samples_cache_miss"] += 1
            elif status == scheduler.SampleStatus.CACHE_INVALID:
                STATS["samples_cache_invalid"] += 1
            else:
                STATS["samples_missed"] += 1
        try:
            await record.insert_samples(pool, rows)
        except Exception:                  # noqa: BLE001
            log.exception("rn1 obs: writing %d samples failed", len(rows))


async def run(pool, http) -> None:
    """Start the three V2 tasks and supervise none of them: they never return.

    The refresher owns all of this channel's I/O; the sampler owns the schedule
    and touches no socket; the admit loop owns the writes that must happen before
    a slot can be sampled. They communicate through the cache and the heap, and
    nothing in the sampler's path can block on a request.
    """
    global _QUEUE, _LOOP
    _QUEUE = asyncio.Queue(maxsize=QUEUE_MAX)
    _LOOP = asyncio.get_running_loop()
    pacer = Pacer(reads_per_second())
    sched = scheduler.DueTimeScheduler()
    mcache = _cache.MarketStateCache()

    log.info("rn1 observability collector up: version=%s subject=%s "
             "channel=%s hot=%d refresh_rps=%.2f inflight=%d base=%s",
             COLLECTOR_VERSION, configured_subject_whale_id(),
             ObservationChannel.LOCAL_CACHE_BATCH_POLL_PATH, cache_hot_tokens(),
             reads_per_second(), max_inflight(), clob_base())

    async def _feed(kind: str, **kw):
        try:
            await record.insert_feed_event(
                pool, session_id=mcache.session_id,
                channel=ObservationChannel.LOCAL_CACHE_BATCH_POLL_PATH,
                kind=kind, tokens_tracked=kw.pop("tokens_tracked", len(mcache.hot)),
                **kw)
        except Exception:                  # noqa: BLE001
            log.exception("rn1 obs: feed event %s failed to record", kind)

    tasks = [
        asyncio.create_task(_admit_loop(pool, sched, mcache), name="rn1obs-admit"),
        asyncio.create_task(_sample_loop(pool, sched, mcache), name="rn1obs-sample"),
        asyncio.create_task(
            _cache.refresher(http, mcache, pacer, record_feed=_feed),
            name="rn1obs-refresh"),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        # SHUTDOWN IS EVIDENCE, NOT CLEANUP (owner decision 7). Every slot still
        # pending belongs to a monotonic deadline this process is about to take
        # with it, so each one is resolved against its own plan row as
        # PROCESS_RESTARTED_BEFORE_CAPTURE. A later reading would be a different
        # experiment and is never written as this one.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        orphans = sched.drain_all()
        if orphans:
            STATS["samples_restarted"] += len(orphans)
            log.warning("rn1 obs: %d planned slots lost to process replacement",
                        len(orphans))
            try:
                await record.insert_samples(pool, [
                    scheduler.resolve_restarted(s, boot_id=clock.PROCESS_BOOT_ID)
                    for s in orphans])
            except Exception:              # noqa: BLE001
                log.exception("rn1 obs: could not record restart losses")
