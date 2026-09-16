"""Polite Data-API access: one shared throttle per process + 429 backoff.

Every Data-API caller (live poller, history backfill, position snapshots,
reconciler, the exit worker's walks, the mirror's per-market read) goes
through the one `Throttle` -- `polite_get`, or its `wait()` / `acquire()`
directly -- so their combined request rate stays under the configured
ceiling and a 429 backs everyone off together instead of spiraling into
a rate-limit ban.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

import httpx

from .config import settings

log = logging.getLogger(__name__)

# THE CEILING, AND WHO MAY MOVE IT (E10, 2026-09-08). 6.0 rps is the
# process's whole data-API budget -- the live poller's roster pass and
# fast lane, positions_sync, the backfill, the reconciler, the exit
# worker's walks and the mirror's per-market reads all draw on it -- and
# it is the venue's number, not an operator dial: settings()
# .data_api_max_rps may only LOWER it (capped_env-style; a shell that
# raised it raised every lane's rate at once, and polite_get's 429
# backoff was what then owned the true ceiling).
DATA_API_MAX_RPS = 6.0
# THE PRIORITY LANE'S STARVATION BOUND: at most this many consecutive
# priority slots are served while a normal caller waits, then one normal
# slot. Twelve is TWO WAVES of the mirror's six-wide book walk
# (rules.MIRROR_BOOK_CONCURRENCY = 6): the walk's per-market reads
# arrive six at a time, a bound under one wave would hand a telemetry
# page a slot in the middle of the wave and a money-path read would wait
# behind it, and past two waves a normal caller has waited 12 x 0.167 s
# = 2 s at the ceiling -- the poller's fast-lane cycle, and no more.
PRIORITY_BURST = 12

# indirections so a test can run the lanes at a fake clock (the real
# throttle sleeps 1/rate per slot; hard2/E10_brief.md's 2 s harness)
_clock = time.monotonic
_sleep = asyncio.sleep


class ThrottlePumpStopped(RuntimeError):
    """The pump ended -- cancelled, or dead of an exception -- with this
    waiter still queued (E10 fold, the review's MEDIUM-1). Raised out of
    `acquire` / `wait` so the caller's own fail-closed path names it
    instead of hanging: `whale_exits.market_positions` returns None and
    the mirror counts `snap_market_unreadable`, the mirror's own
    `_confirm_gone` reads it as NOT gone, `polite_get` and the exit
    worker's walk raise as any request error would. Never raised on a
    pump that ended because no one waits."""


class Throttle:
    """Serializes request starts to at most `rate` per second
    (process-wide), in TWO LANES (E10, 2026-09-08).

    It was one lock and one reserved slot: every caller took the lock,
    reserved the next slot and slept until it, so slots went out in
    ARRIVAL order and a money-path read -- the mirror's per-market
    position read, whale_exits.market_positions -- queued behind
    whatever telemetry pages had arrived just before it (nine concurrent
    poller `/trades` calls, a positions_sync burst): ~1.4 s of queue per
    read at 6 rps with the budget oversubscribed (hard2/E10_map.md §1e).

    Now a slot is handed out only when it FREES, to the lane the rule
    picks: a PRIORITY caller (`acquire(priority=True)`) takes the next
    free slot ahead of every waiting normal caller; normal callers
    (`wait()`, `acquire()`) keep FIFO among themselves, and so do the
    priority callers among themselves. THE RATE DOES NOT CHANGE: one
    slot per 1/rate seconds whatever the mix -- the reservation clock
    `_next` advances by the interval per slot served, exactly as before.
    STARVATION BOUND: at most PRIORITY_BURST consecutive priority slots
    are served while a normal caller waits, then one normal slot (the
    run resets whenever a normal is served or none is waiting). A waiter
    cancelled in the queue (a caller's timeout: mirror_live's
    _SNAP_READ_TIMEOUT_S, CONFIRM_GONE_WAIT_S) leaves the lane and is
    never charged a slot.

    Mechanics: one pump task per event loop (`_serve`), alive while
    anyone waits, that sleeps until the slot frees and picks the lane
    AFTER the sleep -- so a priority caller who arrives during the wait
    takes the slot -- and resolves the winner's future. The uncontended
    case is unchanged: an idle throttle hands the slot out at once.
    The priority callers in the process are the mirror's per-market
    read (whale_exits.market_positions, `priority=True` from
    mirror_live._market_snap) and the mirror's own vanish confirmation
    (mirror_live._confirm_gone; E10 fold); everything else is a normal
    caller.

    THE PUMP CANNOT DIE QUIETLY (E10 fold, the review's MEDIUM-1): a
    pump that ends by cancellation or by an exception with waiters still
    queued fails every one of them, in both lanes, with
    ThrottlePumpStopped -- within the loop step that ends the pump, no
    new arrival needed -- and an exception is logged once, at the time;
    the next `acquire` from anyone restarts the pump (a done pump, or
    one on another loop, is replaced) and is served at the rate. A pump
    that ends because no one waits fails nothing. Each loop's pump
    serves its own loop's waiters only (LOW-1: a future is resolved
    safely from its own loop alone), so a live waiter of another loop
    stays in its lane for that loop's pump and is never dropped; one
    whose loop is closed is dropped, never charged.
    """

    def __init__(self, rate: float, priority_burst: int = PRIORITY_BURST) -> None:
        self._interval = 1.0 / max(rate, 0.1)
        self._next = 0.0                    # the instant the next slot may start
        self._normal: deque = deque()       # FIFO among themselves
        self._priority: deque = deque()     # FIFO among themselves, ahead of every normal
        self._burst_max = max(1, int(priority_burst))
        self._burst = 0                     # consecutive priority slots served while a normal waited
        self._pump: asyncio.Task | None = None

    @property
    def interval(self) -> float:
        return self._interval

    async def wait(self) -> None:
        """A NORMAL slot: polite_get's callers and every direct caller
        that does not say otherwise (the pre-E10 entry point, unchanged
        in name and meaning)."""
        await self.acquire()

    async def acquire(self, priority: bool = False) -> None:
        """One slot on the lane named: returns once it is this caller's
        turn to start a request. Cancelled while waiting: leaves the
        lane, charges nothing, re-raises. Raises ThrottlePumpStopped if
        the pump ended while this caller was queued (E10 fold)."""
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        lane = self._priority if priority else self._normal
        lane.append(fut)
        if self._pump is None or self._pump.done() or self._pump.get_loop() is not loop:
            if self._pump is not None and self._pump.done() and not self._pump.cancelled():
                self._pump.exception()      # its death was logged once, in _serve, at the time
            self._pump = loop.create_task(self._serve(), name="data-api-throttle")
        try:
            await fut
        except asyncio.CancelledError:
            try:
                lane.remove(fut)
            except ValueError:
                pass                        # already served (or dropped): nothing to leave
            raise

    async def _serve(self) -> None:
        """THE PUMP: sleeps until the next slot frees, then hands it to
        the lane the rule picks -- picked AFTER the sleep, never before
        it. One task per loop, serving its own loop's waiters, ending
        when none is queued. Its exit is never silent (E10 fold,
        MEDIUM-1): a waiter of this loop still queued when the pump ends
        is failed with ThrottlePumpStopped, and a pump that died of an
        exception logs it once, here."""
        loop = asyncio.get_running_loop()
        died = False
        try:
            while self._queued(loop):
                now = _clock()
                if self._next - now > 1e-6:
                    await _sleep(self._next - now)
                    continue                # re-read the lanes: an arrival during the wait counts
                fut = self._pick(loop)
                if fut is None:
                    continue                # only cancelled waiters were queued: nothing to serve
                self._next = max(now, self._next) + self._interval
                fut.set_result(None)
        except Exception:                   # noqa: BLE001 -- a pump's own bug, never a shutdown
            died = True
            log.exception("data-api throttle pump died with %d waiter(s) queued: each fails "
                          "closed (ThrottlePumpStopped); the next acquire restarts the pump",
                          self._queued(loop, count=True))
            raise
        finally:
            failed = self._release(loop)
            if failed and not died:
                log.warning("data-api throttle pump cancelled with %d waiter(s) queued: each "
                            "fails closed (ThrottlePumpStopped)", failed)

    def _queued(self, loop, count: bool = False):
        """Whether (or, `count`, how many) LIVE waiters of `loop` are
        queued in either lane. Snapshots of the deques: another loop's
        thread may append meanwhile."""
        n = 0
        for lane in (self._priority, self._normal):
            for f in tuple(lane):
                if not f.done() and f.get_loop() is loop:
                    if not count:
                        return True
                    n += 1
        return n if count else False

    @staticmethod
    def _head(lane, loop) -> asyncio.Future | None:
        """The first LIVE waiter of `loop` in `lane`: a done one (a
        caller's timeout, a waiter already failed) is skipped, and so
        is a live waiter of another loop -- left where it is for its
        own loop's pump (LOW-1), never popped by this one."""
        for f in tuple(lane):
            if not f.done() and f.get_loop() is loop:
                return f
        return None

    def _pick(self, loop) -> asyncio.Future | None:
        """The lane rule. A done waiter (a caller's timeout) or one whose
        loop is CLOSED is dropped, never charged; a live waiter of
        another loop is skipped without being popped."""
        for lane in (self._priority, self._normal):
            for f in tuple(lane):
                if f.done() or f.get_loop().is_closed():
                    _drop(lane, f)
        p, n = self._head(self._priority, loop), self._head(self._normal, loop)
        if p is not None and n is not None:
            if self._burst >= self._burst_max:
                self._burst = 0
                self._normal.remove(n)
                return n
            self._burst += 1
            self._priority.remove(p)
            return p
        self._burst = 0                     # no one is starving: the run starts over
        if p is not None:
            self._priority.remove(p)
            return p
        if n is not None:
            self._normal.remove(n)
            return n
        return None

    def _release(self, loop) -> int:
        """The pump's exit (E10 fold, MEDIUM-1): every LIVE waiter of
        `loop` still queued in either lane is failed with
        ThrottlePumpStopped and leaves its lane; a done waiter leaves
        too; a live waiter of another loop stays for its own pump.
        Returns how many were failed -- zero on a normal exit, which
        happens only when none of this loop's is queued."""
        failed = 0
        for name, lane in (("priority", self._priority), ("normal", self._normal)):
            for f in tuple(lane):
                if not f.done() and f.get_loop() is loop:
                    f.set_exception(ThrottlePumpStopped(
                        f"data-api throttle pump stopped with this {name}-lane waiter queued"))
                    failed += 1
                if f.done():
                    _drop(lane, f)
        return failed

    def waiting(self) -> tuple[int, int]:
        """(priority, normal) callers queued -- measurement only."""
        return (sum(1 for f in self._priority if not f.done()),
                sum(1 for f in self._normal if not f.done()))


def _drop(lane: deque, fut: asyncio.Future) -> None:
    """Remove `fut` from `lane` if it is still there (another loop's
    pump may have dropped the same done waiter first)."""
    try:
        lane.remove(fut)
    except ValueError:
        pass


_throttle: Throttle | None = None


def data_api_rate() -> float:
    """The rate the process-wide throttle runs at: settings()
    .data_api_max_rps, and NEVER above DATA_API_MAX_RPS (E10: the
    environment may only lower it). Unreadable settings raise, as they
    did: a throttle that cannot be built is no request at all."""
    rate = float(settings().data_api_max_rps)
    if rate != rate:                        # NaN: not a rate; the ceiling
        return DATA_API_MAX_RPS
    return min(rate, DATA_API_MAX_RPS)


def data_api_throttle() -> Throttle:
    global _throttle
    if _throttle is None:
        _throttle = Throttle(data_api_rate())
    return _throttle


async def polite_get(
    http: httpx.AsyncClient, url: str, params: dict | None = None, retries: int = 3
) -> httpx.Response:
    """Throttled GET with Retry-After-aware 429 backoff. Returns the final
    response (raise_for_status is the caller's decision). A NORMAL
    caller of the throttle (E10): the priority lane is the mirror's
    per-market read and its own vanish confirmation alone. A pump that
    ended with this caller queued raises ThrottlePumpStopped out of the
    wait, as any request error would (E10 fold)."""
    throttle = data_api_throttle()
    resp: httpx.Response | None = None
    for attempt in range(retries + 1):
        await throttle.wait()
        resp = await http.get(url, params=params)
        if resp.status_code != 429:
            return resp
        retry_after = resp.headers.get("Retry-After")
        try:
            wait = min(float(retry_after), 120.0) if retry_after else 10.0 * (attempt + 1)
        except ValueError:
            wait = 10.0 * (attempt + 1)
        log.warning("data-api 429 — backing off %.0fs (attempt %s)", wait, attempt + 1)
        await asyncio.sleep(wait)
    return resp  # last 429 — caller's raise_for_status surfaces it
