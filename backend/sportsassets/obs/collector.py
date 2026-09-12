"""The passive collector -- run 83F/83G.

SHAPE. A synchronous, zero-I/O, never-raises `observe()` is called at the moment
a source event arrives; it stamps the receipt instant and hands the event to an
in-memory queue. A background loop drains the queue and runs each event's forward
snapshot schedule. The split matters: the receipt instant has to be taken AT
ARRIVAL. Stamping it later -- when a poller next looks at a table, say -- measures
the poller, which is precisely the error that makes copy_probes.reaction_s
unusable as a latency figure.

That pattern is not invented here. chain.py:755-763 already calls
`shadow_observe` and `emitter_observe` exactly this way, for exactly this reason.

NO ORDER PATH. This module imports clock, config, book, record, schedule and the
connection pool. It reads the book by plain HTTP GET. There is no venue SDK in
its import graph, and tests/test_obs_safety.py fails the build if one appears.

BACKPRESSURE IS DROPPING, AND DROPS ARE COUNTED. The queue is bounded. If it
fills, the event is dropped and a counter increments -- the collector must never
slow down or block the ingestion path it is attached to. An instrument that
degrades the thing it measures is worse than no instrument. The dropped count is
reported, so a gap in the data is visible rather than silent.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field

from . import clock, record
from .book import read_book
from .config import (COLLECTOR_VERSION, clob_base, max_inflight,
                     reads_per_second, shadow_enabled)
from .schedule import Plan, SnapshotStatus, vwap

log = logging.getLogger(__name__)

QUEUE_MAX = 512

_QUEUE: asyncio.Queue | None = None
_LOOP: asyncio.AbstractEventLoop | None = None

STATS: dict[str, int] = {
    "observed": 0, "dropped_queue_full": 0, "dropped_disabled": 0,
    "dropped_no_loop": 0, "events_written": 0, "events_duplicate": 0,
    "snapshots_captured": 0, "snapshots_missed": 0, "snapshots_error": 0,
    "observe_exceptions": 0,
}


@dataclass
class Observation:
    """What `observe()` captures at arrival, before any I/O has happened."""

    receipt: clock.Instant
    source_event_id: str
    source_lane: str
    source_venue: str
    source_token_id: str | None
    source_ts: clock.SourceTimestamp
    ingest_worker: str
    fields: dict = field(default_factory=dict)


# --------------------------------------------------------------- the hook
def observe(*, source_event_id: str, source_lane: str, source_venue: str,
            source_token_id: str | None, source_ts: clock.SourceTimestamp,
            ingest_worker: str, **fields) -> None:
    """Stamp arrival and enqueue. SYNCHRONOUS, NON-BLOCKING, NEVER RAISES.

    Called from the ingestion path. Every failure mode -- disabled, no running
    loop, full queue, anything unforeseen -- results in a counter and a return,
    never an exception into the caller. Instrumentation that can break ingestion
    is not instrumentation, it is a new way to lose fills.
    """
    try:
        if not shadow_enabled():
            STATS["dropped_disabled"] += 1
            return
        q = _QUEUE
        if q is None:
            STATS["dropped_no_loop"] += 1
            return
        obs = Observation(
            receipt=clock.now(),          # <- the whole point: stamped HERE
            source_event_id=source_event_id,
            source_lane=source_lane,
            source_venue=source_venue,
            source_token_id=source_token_id,
            source_ts=source_ts,
            ingest_worker=ingest_worker,
            fields=fields,
        )
        try:
            q.put_nowait(obs)
            STATS["observed"] += 1
        except asyncio.QueueFull:
            STATS["dropped_queue_full"] += 1
    except Exception:                     # noqa: BLE001 -- never reach the caller
        STATS["observe_exceptions"] += 1


# ------------------------------------------------------------------ pacing
class Pacer:
    """A token bucket over this collector's own book reads.

    The venue has 429'd a board walk above roughly 3 req/s before. A collector
    that triggers rate limiting would corrupt the very curve it is measuring, and
    would do it invisibly -- a throttled read arrives late and looks like a slow
    market rather than a slow reader.
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


# ------------------------------------------------------------- per-event run
async def _run_plan(pool, http, obs: Observation, obs_event_id: str,
                    pacer: Pacer, base_url: str) -> None:
    """Walk one event's pre-registered offsets, writing every slot exactly once."""
    plan = Plan.for_event(obs.receipt.monotonic)
    q_a = None
    q_b = None
    size = obs.fields.get("source_size")
    if size:
        try:
            q_b = float(size)
            q_a = 0.10 * q_b
        except (TypeError, ValueError):
            q_a = q_b = None

    try:
        while True:
            slot = plan.next_pending(time.monotonic())
            if slot is None:
                break
            wait = slot.wait_s(time.monotonic())
            if wait > 0:
                await asyncio.sleep(wait)
            # Re-check: the sleep may have overshot the window, and a reading
            # taken late is a MISS, not a late reading. next_pending() will
            # expire it on the following pass.
            if slot.is_expired(time.monotonic()):
                continue

            if obs.source_token_id is None:
                slot.status = SnapshotStatus.NOT_ATTEMPTED
                slot.miss_reason = "no source token id to read a book for"
                await record.insert_snapshot(pool, obs_event_id=obs_event_id,
                                             slot=slot)
                continue

            await pacer.wait()
            if slot.is_expired(time.monotonic()):
                slot.status = SnapshotStatus.SKIPPED_PACING
                slot.miss_reason = "pacer delayed the read past its window"
                STATS["snapshots_missed"] += 1
                await record.insert_snapshot(pool, obs_event_id=obs_event_id,
                                             slot=slot)
                continue

            snap = await read_book(http, base_url, obs.source_token_id)
            if snap.ok:
                slot.status = SnapshotStatus.CAPTURED
                STATS["snapshots_captured"] += 1
            else:
                slot.status = SnapshotStatus.VENUE_ERROR
                slot.miss_reason = snap.error
                STATS["snapshots_error"] += 1

            va = vwap(snap.asks, q_a) if (snap.ok and q_a) else None
            vb = vwap(snap.asks, q_b) if (snap.ok and q_b) else None
            await record.insert_snapshot(
                pool, obs_event_id=obs_event_id, slot=slot, snap=snap,
                vwap_qa=va, qa_exhausted=(q_a is not None and va is None),
                vwap_qb=vb, qb_exhausted=(q_b is not None and vb is None),
            )
    except asyncio.CancelledError:
        plan.expire_all(time.monotonic(), "collector cancelled")
        raise
    finally:
        # Any slot still unresolved is recorded as missed, never dropped.
        plan.expire_all(time.monotonic(), "event closed with slots unresolved")
        for slot in plan.slots:
            if slot.status == SnapshotStatus.MISSED_WINDOW and slot.miss_reason:
                with contextlib.suppress(Exception):
                    await record.insert_snapshot(pool, obs_event_id=obs_event_id,
                                                 slot=slot)
                    STATS["snapshots_missed"] += 1


async def _handle(pool, http, obs: Observation, pacer: Pacer,
                  base_url: str) -> None:
    obs_event_id = record.new_event_id()
    written = await record.insert_event(
        pool,
        obs_event_id=obs_event_id,
        source_event_id=obs.source_event_id,
        source_lane=obs.source_lane,
        source_venue=obs.source_venue,
        receipt=obs.receipt,
        source_ts=obs.source_ts,
        ingest_worker=obs.ingest_worker,
        # source_token_id is an Observation attribute rather than one of
        # `fields` -- the book reader needs it directly -- so it has to be
        # passed explicitly or it reaches the venue read and never the row.
        # It did exactly that until the sample event in
        # research/RUN83_SAMPLE_EVENT.txt showed the column arriving null.
        source_token_id=obs.source_token_id,
        **obs.fields,
    )
    if written is None:
        # Already observed -- a replay after a restart. One observation, not two.
        STATS["events_duplicate"] += 1
        return
    STATS["events_written"] += 1
    await record.append_transition(pool, obs_event_id=obs_event_id, seq=1,
                                   stage="RECEIPT", state="OBSERVED",
                                   detail={"lane": obs.source_lane})
    await _run_plan(pool, http, obs, obs_event_id, pacer, base_url)
    await record.append_transition(pool, obs_event_id=obs_event_id, seq=2,
                                   stage="SNAPSHOTS", state="COMPLETE")


async def run(pool, http) -> None:
    """Drain the queue and run each event's schedule. The collector's main loop.

    Inflight events are capped: each one holds a slot for its full 60 s horizon,
    so an unbounded fan-out would be an unbounded number of live tasks and an
    unbounded read rate.
    """
    global _QUEUE, _LOOP
    _QUEUE = asyncio.Queue(maxsize=QUEUE_MAX)
    _LOOP = asyncio.get_running_loop()
    pacer = Pacer(reads_per_second())
    sem = asyncio.Semaphore(max_inflight())
    base_url = clob_base()
    live: set[asyncio.Task] = set()

    log.info("rn1 observability collector up: version=%s offsets=pre-registered "
             "queue_max=%d inflight=%d rps=%.2f base=%s",
             COLLECTOR_VERSION, QUEUE_MAX, max_inflight(), reads_per_second(),
             base_url)

    async def _one(obs: Observation) -> None:
        async with sem:
            try:
                await _handle(pool, http, obs, pacer, base_url)
            except asyncio.CancelledError:
                raise
            except Exception:                        # noqa: BLE001
                log.exception("rn1 obs: event %s failed", obs.source_event_id)

    try:
        while True:
            obs = await _QUEUE.get()
            task = asyncio.create_task(_one(obs))
            live.add(task)
            task.add_done_callback(live.discard)
    finally:
        for task in list(live):
            task.cancel()
        if live:
            await asyncio.gather(*live, return_exceptions=True)
        _QUEUE = None
        _LOOP = None
