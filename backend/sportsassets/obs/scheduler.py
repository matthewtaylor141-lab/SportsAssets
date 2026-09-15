"""The central due-time scheduler -- run 83.2, owner decisions 6 and 7.

WHAT THIS REPLACES, AND WHY IT IS NOT A TUNING CHANGE. V1's collector.py::run
created one asyncio task per event and each task held a slot of
`asyncio.Semaphore(max_inflight())` for its ENTIRE 60-second horizon. The
semaphore was therefore a cap on `observations whose future timestamps are
pending`, not on network operations, and the collector's ceiling was

    max_inflight / horizon  =  8 / 60 s  =  0.133 events/s

against a measured RN1-only arrival rate of 0.187/s (busiest complete day) to
0.255/s (the activation window). IT FAILED BY 1.4x TO 1.9x EVEN AT THE CORRECT
POPULATION -- correcting the subject filter alone would have moved capture from
~0% to roughly half, not to working. That is why the architecture changed.

Here a pending slot is an entry in a heap. It costs one tuple and no
concurrency. The heap is drained by ONE task that sleeps until the earliest due
instant, samples local state (no I/O), and writes the result. At the design rate
of 0.30 events/s that is 18 events in flight and 180 pending tuples.

MONOTONIC DEADLINES DO NOT SURVIVE A PROCESS REPLACEMENT (owner decision 7).
Python's monotonic origin moves on every restart, so a deadline computed in a
dead process is meaningless in a new one. This module therefore never
reconstructs one. A slot whose process is gone is resolved as
PROCESS_RESTARTED_BEFORE_CAPTURE against its ORIGINAL plan row, and the new
process does not pretend to resume the same experiment.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import time
from dataclasses import dataclass, field

from . import clock
from .config import OFFSETS, window_for
from .slot import PREREGISTRATION_VERSION, slot_id

log = logging.getLogger(__name__)


class SampleStatus:
    CAPTURED_LOCAL = "CAPTURED_LOCAL"
    CAPTURED_HTTP = "CAPTURED_HTTP"
    MISSED_WINDOW = "MISSED_WINDOW"
    MISSED_ADMISSION_LATE = "MISSED_ADMISSION_LATE"
    CACHE_MISS = "CACHE_MISS"
    CACHE_INVALID = "CACHE_INVALID"
    VENUE_ERROR = "VENUE_ERROR"
    PROCESS_RESTARTED = "PROCESS_RESTARTED_BEFORE_CAPTURE"


@dataclass(frozen=True)
class PlannedSlot:
    """One immutable intention. Written to rn1_obs_plan before anything runs."""

    observation_slot_id: str
    obs_event_id: str
    source_event_id: str
    observation_channel: str
    target_offset_ms: int
    preregistration_version: str
    process_boot_id: str
    receipt_wall: object
    receipt_monotonic: float
    target_monotonic: float
    window_ms: int
    source_token_id: str | None
    source_market_id: str | None
    planned_at_monotonic: float
    planned_at_wall: object

    @property
    def expires_at_monotonic(self) -> float:
        return self.target_monotonic + self.window_ms / 1000.0


def plan_for_event(*, obs_event_id: str, source_event_id: str,
                   receipt: clock.Instant, channel: str,
                   source_token_id: str | None = None,
                   source_market_id: str | None = None,
                   offsets=None,
                   preregistration_version: str = PREREGISTRATION_VERSION
                   ) -> list[PlannedSlot]:
    """Every slot this event will ever have, computed once, up front.

    The whole plan exists before the first sample, which is what lets a missing
    result be distinguished from a slot that never existed. V1 could not tell
    those apart because it had no plan -- only rows that appeared when something
    happened.

    The anchor is the caller's receipt Instant and nothing here re-reads a clock
    to derive a deadline: target = anchor + offset, exactly.
    """
    # Read at CALL time, not bound as a default argument. A default is evaluated
    # once at import, which would make the pre-registered ladder unmockable and
    # -- worse -- would freeze it against any later amendment to config.OFFSETS
    # while the version string went on claiming the amendment had taken effect.
    if offsets is None:
        offsets = OFFSETS
    planned_at = clock.now()
    out: list[PlannedSlot] = []
    for label, target_s in offsets:
        del label
        offset_ms = int(round(target_s * 1000))
        out.append(PlannedSlot(
            observation_slot_id=slot_id(
                source_event_id=source_event_id, observation_channel=channel,
                target_offset_ms=offset_ms,
                preregistration_version=preregistration_version),
            obs_event_id=obs_event_id,
            source_event_id=source_event_id,
            observation_channel=channel,
            target_offset_ms=offset_ms,
            preregistration_version=preregistration_version,
            process_boot_id=receipt.process_boot_id,
            receipt_wall=receipt.wall,
            receipt_monotonic=receipt.monotonic,
            target_monotonic=receipt.monotonic + target_s,
            window_ms=int(round(window_for(target_s) * 1000)),
            source_token_id=source_token_id,
            source_market_id=source_market_id,
            planned_at_monotonic=planned_at.monotonic,
            planned_at_wall=planned_at.wall,
        ))
    return out


@dataclass
class DueTimeScheduler:
    """A heap of pending slots, drained by one task.

    NO SLOT HOLDS NETWORK CONCURRENCY WHILE IT WAITS. That is the entire
    correction. The semaphore this module does not own is acquired by the cache
    refresher and, if enabled, by the secondary HTTP channel -- only while a
    request is actually executing.
    """

    _heap: list = field(default_factory=list)
    _seq: itertools.count = field(default_factory=itertools.count)
    _wake: asyncio.Event = field(default_factory=asyncio.Event)
    pending: int = 0

    def push(self, slot: PlannedSlot) -> None:
        """Admit a planned slot. O(log n), no I/O, never blocks."""
        heapq.heappush(self._heap, (slot.target_monotonic, next(self._seq), slot))
        self.pending += 1
        # Wake the drain loop: the slot just pushed may be due before whatever
        # it is currently sleeping until.
        self._wake.set()

    def push_all(self, slots) -> int:
        n = 0
        for slot in slots:
            self.push(slot)
            n += 1
        return n

    def peek_due_monotonic(self) -> float | None:
        return self._heap[0][0] if self._heap else None

    def pop_ready(self, now_monotonic: float) -> list[PlannedSlot]:
        """Every slot whose target instant has arrived, earliest first."""
        ready: list[PlannedSlot] = []
        while self._heap and self._heap[0][0] <= now_monotonic:
            _, _, slot = heapq.heappop(self._heap)
            self.pending -= 1
            ready.append(slot)
        return ready

    def drain_all(self) -> list[PlannedSlot]:
        """Every remaining slot, for shutdown accounting."""
        out = [entry[2] for entry in self._heap]
        self._heap.clear()
        self.pending = 0
        return out

    async def wait_until_next(self, max_sleep: float = 1.0) -> None:
        """Sleep until the earliest slot is due, or until something is pushed.

        Bounded by max_sleep so an empty scheduler still wakes to notice
        shutdown, and woken early by push() so a newly admitted 0 ms slot is not
        left waiting behind an older 60 s one. THAT BOUND IS WHY THE 0 MS OFFSET
        IS REACHABLE AT ALL: a fixed poll interval would quantise every short
        offset to the interval.
        """
        self._wake.clear()
        due = self.peek_due_monotonic()
        if due is None:
            delay = max_sleep
        else:
            delay = min(max(0.0, due - time.monotonic()), max_sleep)
        if delay <= 0:
            return
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


def resolve_local(slot: PlannedSlot, sample, *, admission_monotonic: float | None
                  = None) -> dict:
    """Turn a planned slot plus a local cache sample into a result row.

    PURE. Takes no clock reading of its own and writes nothing, so the whole
    decision table is testable without a database, an event loop or a venue.

    THE ORDER OF THESE BRANCHES IS THE DECISION TABLE, and it is deliberate:

      1. admission arrived after the window shut  -> MISSED_ADMISSION_LATE
      2. the sampler arrived after the window shut -> MISSED_WINDOW
      3. we never held state for this token        -> CACHE_MISS
      4. state present but not admissible         -> CACHE_INVALID
      5. otherwise                                -> CAPTURED_LOCAL

    (1) is separated from (2) on purpose. Both are misses, but one says the
    instrument was too slow to start and the other says it was too slow to
    arrive, and lumping them together would hide which. V1 had neither: an
    admission that landed 72 s late produced a MISSED_WINDOW indistinguishable
    from a scheduler that overslept.
    """
    from .cache import Continuity, StateValidity      # noqa: PLC0415 (cycle)

    at = sample.sampled_at
    lateness_ms = (at.monotonic - slot.target_monotonic) * 1000.0
    row = {
        "observation_slot_id": slot.observation_slot_id,
        "obs_event_id": slot.obs_event_id,
        "process_boot_id": at.process_boot_id,
        "observation_channel": slot.observation_channel,
        "target_offset_ms": slot.target_offset_ms,
        "target_monotonic": slot.target_monotonic,
        "actual_sample_monotonic": at.monotonic,
        "actual_sample_wall": at.wall,
        "sample_lateness_ms": round(lateness_ms, 3),
        "source_token_id": slot.source_token_id,
        "source_market_id": slot.source_market_id,
        "continuity_status": Continuity.UNVERIFIED,
        "state_validity": sample.validity,
        "cache_age_ms": (round(sample.cache_age_ms, 3)
                         if sample.cache_age_ms is not None else None),
    }
    if sample.state is not None:
        st = sample.state
        row.update({
            "state_received_wall": st.received.wall,
            "state_received_monotonic": st.received.monotonic,
            "best_bid": st.bids[0][0] if st.bids else None,
            "best_ask": st.asks[0][0] if st.asks else None,
            "depth_levels": len(st.asks),
            "venue_snapshot_ts": None,          # the venue's string, kept raw below
            "venue_sequence": None,             # the venue supplies none
            "venue_book_hash": st.venue_hash,
            "book_provenance": "clob_books_batch",
        })

    if admission_monotonic is not None and admission_monotonic > slot.expires_at_monotonic:
        row["status"] = SampleStatus.MISSED_ADMISSION_LATE
        row["miss_reason"] = (
            f"admitted at +{(admission_monotonic - slot.receipt_monotonic) * 1000:.1f}ms, "
            f"window shut at +{slot.target_offset_ms + slot.window_ms}ms")
        return row
    if at.monotonic > slot.expires_at_monotonic:
        row["status"] = SampleStatus.MISSED_WINDOW
        row["miss_reason"] = (
            f"sampled {lateness_ms:.1f}ms late; window {slot.window_ms}ms")
        return row
    if sample.state is None:
        row["status"] = SampleStatus.CACHE_MISS
        row["miss_reason"] = "no local state had ever arrived for this token"
        return row
    if sample.validity != StateValidity.VALID:
        row["status"] = SampleStatus.CACHE_INVALID
        row["miss_reason"] = f"local state not admissible: {sample.validity}"
        return row
    if sample.cache_age_ms is None:
        # A CAPTURE WITHOUT ITS AGE IS NOT A CAPTURE. State with no measurable
        # age is indistinguishable from a fresh reading, which is the most
        # misleading row this table could hold -- so it is refused as a capture
        # rather than written as one. migration 063's
        # rn1_obs_samples_capture_has_age_ck says the same thing in the schema;
        # this branch means the row never reaches it and the reason is recorded
        # instead of an insert error.
        row["status"] = SampleStatus.CACHE_INVALID
        row["miss_reason"] = ("local state carried no measurable age; a reading "
                              "without its staleness is not a reading")
        return row

    row["status"] = SampleStatus.CAPTURED_LOCAL
    return row


def resolve_restarted(slot: PlannedSlot, *, boot_id: str) -> dict:
    """A slot whose process is gone. Owner decision 7.

    Recorded against the ORIGINAL plan row, carrying the ORIGINAL
    target_monotonic so the row still says which experiment was lost -- but with
    THIS process's boot id, because that mismatch is the evidence. No sample
    values, no derived lateness: there is no common clock in which to compute
    one, and inventing a number here is precisely what clock.elapsed_between
    raises to prevent.
    """
    return {
        "observation_slot_id": slot.observation_slot_id,
        "obs_event_id": slot.obs_event_id,
        "process_boot_id": boot_id,
        "observation_channel": slot.observation_channel,
        "target_offset_ms": slot.target_offset_ms,
        "target_monotonic": slot.target_monotonic,
        "actual_sample_monotonic": None,
        "actual_sample_wall": None,
        "sample_lateness_ms": None,
        "cache_age_ms": None,
        "status": SampleStatus.PROCESS_RESTARTED,
        "miss_reason": (
            "the process holding this monotonic deadline was replaced; a later "
            "reading would be a different experiment, not this one"),
        "source_token_id": slot.source_token_id,
        "source_market_id": slot.source_market_id,
    }
