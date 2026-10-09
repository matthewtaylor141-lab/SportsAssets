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
# INSIDE THE HOST'S 429 COOLDOWN the pump re-reads its lanes at least this
# often, so a priority arrival is served once its bounded wait is over and
# never held for the whole cooldown (P0-429). A poll of the pump's own state,
# not a request: nothing is sent.
COOLDOWN_POLL_S = 0.25

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

    THE HOST'S 429 COOLDOWN (P0-429, 2026-10-09). Production workers
    03:44:18-03:44:24Z logged `data-api 429 — backing off 0s` seventeen
    times in six seconds, attempts 1 to 4: polite_get slept the venue's own
    Retry-After (0, or under half a second), per CALLER, while every other
    caller kept taking slots at the full rate. Now a 429 read by polite_get
    arms ONE cooldown on this throttle -- the escalation venue_pace applies
    to the gateway (venue_pace.escalate: COOLDOWN_FLOOR_S, doubled per
    consecutive 429 on a request sent after the cooldown was armed, capped
    at COOLDOWN_CAP_S, jittered, a longer Retry-After honoured), reset only
    by a 2xx sent after it was armed. While it stands the pump hands out
    NO normal slot; a priority waiter is served once it has waited at most
    venue_pace.PRIORITY_COOLDOWN_MAX_WAIT_S of the cooldown (bounded, never
    starved), and the starvation bound above does not hand a normal a slot
    inside the cooldown. The rate itself is unchanged: one slot per
    interval, the same reservation clock.
    """

    def __init__(self, rate: float, priority_burst: int = PRIORITY_BURST) -> None:
        self._interval = 1.0 / max(rate, 0.1)
        self._next = 0.0                    # the instant the next slot may start
        self._normal: deque = deque()       # FIFO among themselves
        self._priority: deque = deque()     # FIFO among themselves, ahead of every normal
        self._burst_max = max(1, int(priority_burst))
        self._burst = 0                     # consecutive priority slots served while a normal waited
        self._pump: asyncio.Task | None = None
        self._cd: dict = {}
        self.reset_cooldown()

    # ── the host's 429 cooldown (P0-429) ───────────────────────────────

    def reset_cooldown(self) -> None:
        """No cooldown, no counts (construction, and tests)."""
        self._cd = {"consecutive": 0, "level_s": 0.0, "until": 0.0,
                    "armed": None, "cooldown_s": 0.0, "cooldown_is": None,
                    "retry_after_s": None, "rate_limited_total": 0,
                    "escalations": 0, "in_flight_429s": 0, "resets": 0,
                    "priority_served_in_cooldown": 0, "last_429": None,
                    "last_ok_epoch": None}

    def note_429(self, *, retry_after_s=None, dispatched_at: float | None = None,
                 path: str | None = None) -> dict:
        """A 429 from the host: escalate (or, for a request already in
        flight when the cooldown was armed, only count). Returns what was
        applied. `path` is the request path alone -- never its parameters
        (a /trades query names a wallet)."""
        from . import venue_pace as _vp
        now = _clock()
        cd = self._cd
        ra = _vp.clean_retry_after(retry_after_s)
        cd["consecutive"] += 1
        cd["rate_limited_total"] += 1
        cd["last_429"] = {"at_epoch": round(time.time(), 3), "path": path,
                          "retry_after_s": ra}
        if (dispatched_at is not None and cd["armed"] is not None
                and dispatched_at < cd["armed"] and now < cd["until"]):
            cd["in_flight_429s"] += 1
            if ra is not None and now + ra > cd["until"]:
                cd["until"], cd["cooldown_is"], cd["retry_after_s"] = now + ra, "RETRY_AFTER", ra
            out = {"escalated": False, "why": "IN_FLIGHT_BEFORE_THE_COOLDOWN"}
        else:
            e = _vp.escalate(cd["level_s"], ra, _vp._jitter())
            cd["level_s"] = e["level_s"]
            cd["until"] = max(cd["until"], now + e["cooldown_s"])
            cd["armed"] = now
            cd["cooldown_s"] = round(e["cooldown_s"], 3)
            cd["cooldown_is"] = e["cooldown_is"]
            cd["retry_after_s"] = ra
            cd["escalations"] += 1
            out = {"escalated": True, "level_s": e["level_s"],
                   "cooldown_s": round(e["cooldown_s"], 3),
                   "cooldown_is": e["cooldown_is"]}
        out.update(consecutive=cd["consecutive"],
                   seconds_left=round(max(0.0, cd["until"] - now), 3))
        return out

    def note_ok(self, *, dispatched_at: float | None = None) -> bool:
        """A 2xx from the host: resets the escalation and lifts the
        cooldown, only for a request sent after the cooldown was armed."""
        cd = self._cd
        cd["last_ok_epoch"] = round(time.time(), 3)
        if (cd["armed"] is not None and dispatched_at is not None
                and dispatched_at < cd["armed"]):
            return False
        if cd["consecutive"] == 0 and cd["level_s"] == 0.0 and _clock() >= cd["until"]:
            return False
        cd.update(consecutive=0, level_s=0.0, until=0.0, armed=None)
        cd["resets"] += 1
        return True

    def cooldown_left(self) -> float:
        return max(0.0, self._cd["until"] - _clock())

    def cooldown_state(self) -> dict:
        """THE READBACK (the poller's heartbeat): the host, the cooldown,
        the consecutive 429 count, the waiters it holds and the last 429."""
        cd = self._cd
        left = self.cooldown_left()
        try:
            from urllib.parse import urlparse
            host = urlparse(settings().data_api_base).netloc or None
        except Exception:                   # noqa: BLE001 -- telemetry only
            host = None
        p, n = self.waiting()
        return {"version": "DATA_API_429_ESCALATING_COOLDOWN_V1", "host": host,
                "scope": "THIS_PROCESS", "cooldown_active": left > 0,
                "cooldown_seconds_left": round(left, 3),
                "cooldown_s": cd["cooldown_s"], "level_s": cd["level_s"],
                "cooldown_is": cd["cooldown_is"],
                "retry_after_s": cd["retry_after_s"],
                "consecutive_429": cd["consecutive"],
                "rate_limited_total": cd["rate_limited_total"],
                "escalations": cd["escalations"],
                "in_flight_429s": cd["in_flight_429s"], "resets": cd["resets"],
                "priority_served_in_cooldown": cd["priority_served_in_cooldown"],
                "normal_waiters_held": n if left > 0 else 0,
                "priority_waiters": p,
                "last_429": dict(cd["last_429"]) if cd["last_429"] else None,
                "last_ok_epoch": cd["last_ok_epoch"]}

    def _cooldown_hold(self, loop, now: float) -> float:
        """How long the pump must hold before its next grant because of the
        cooldown: 0.0 outside it; inside it, the time until the priority
        head has waited its bound (0.0 once it has), else a short poll so a
        priority arrival is never held past its bound."""
        from . import venue_pace as _vp
        left = self._cd["until"] - now
        if left <= 1e-6:
            return 0.0
        if self._head(self._priority, loop) is not None:
            armed = self._cd["armed"] if self._cd["armed"] is not None else now
            return max(0.0, min(left, armed + _vp.PRIORITY_COOLDOWN_MAX_WAIT_S - now))
        return min(left, COOLDOWN_POLL_S)

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
                hold = self._cooldown_hold(loop, now)
                if hold > 1e-6:
                    await _sleep(hold)      # the host's 429 cooldown (P0-429): no normal slot
                    continue
                fut = self._pick(loop, priority_only=self._cd["until"] - now > 1e-6)
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

    def _pick(self, loop, priority_only: bool = False) -> asyncio.Future | None:
        """The lane rule. A done waiter (a caller's timeout) or one whose
        loop is CLOSED is dropped, never charged; a live waiter of
        another loop is skipped without being popped. `priority_only`
        (inside the host's 429 cooldown): the priority head or nothing --
        no normal slot, and the starvation run is left where it stood."""
        for lane in (self._priority, self._normal):
            for f in tuple(lane):
                if f.done() or f.get_loop().is_closed():
                    _drop(lane, f)
        p, n = self._head(self._priority, loop), self._head(self._normal, loop)
        if priority_only:
            if p is not None:
                self._priority.remove(p)
                self._cd["priority_served_in_cooldown"] += 1
            return p
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


def clock() -> float:
    """The throttle's clock (monotonic, or a test's fake one): a caller that
    takes its slot and GETs directly stamps the instant it sent with this, so
    the host's cooldown can tell a response that left before it was armed."""
    return _clock()


def observe_data_api_response(resp, *, sent_at: float | None = None,
                              path: str | None = None,
                              throttle=None) -> dict | None:
    """ONE data-api response read by the host's 429 cooldown (P0-429): a 429
    arms or escalates it (Throttle.note_429, with the host's own Retry-After),
    a 2xx sent after it was armed resets it (Throttle.note_ok). polite_get
    reads every response through here, and so does every caller that takes
    its slot and GETs directly -- the exit worker's positions walk and its
    per-market read (workers/whale_exits) -- so a 429 any of them meets holds
    every caller of the host, not only the one that met it. `path` is the
    request path alone, never its query (a /trades or /positions query names
    a wallet). Returns what a 429 applied (None otherwise). Never raises; a
    stand-in throttle without the cooldown (a test's) is left alone."""
    try:
        thr = throttle if throttle is not None else data_api_throttle()
        status = getattr(resp, "status_code", None)
        if status == 429:
            note = getattr(thr, "note_429", None)
            if note is None:
                return None
            try:
                from .venue_http_error import _retry_after_seconds
                hdrs = getattr(resp, "headers", None) or {}
                ra = _retry_after_seconds(hdrs.get("Retry-After"))
            except Exception:               # noqa: BLE001 -- an unreadable header is no header
                ra = None
            return note(retry_after_s=ra, dispatched_at=sent_at, path=path)
        if (isinstance(status, int) and not isinstance(status, bool)
                and 200 <= status < 300):
            ok = getattr(thr, "note_ok", None)
            if ok is not None:
                ok(dispatched_at=sent_at)
    except Exception:                       # noqa: BLE001 -- telemetry and a hold, never a raise
        return None
    return None


async def polite_get(
    http: httpx.AsyncClient, url: str, params: dict | None = None, retries: int = 3
) -> httpx.Response:
    """Throttled GET with Retry-After-aware 429 backoff. Returns the final
    response (raise_for_status is the caller's decision). A NORMAL
    caller of the throttle (E10): the priority lane is the mirror's
    per-market read and its own vanish confirmation alone. A pump that
    ended with this caller queued raises ThrottlePumpStopped out of the
    wait, as any request error would (E10 fold).

    A 429 NEVER RETRIES AT ZERO SECONDS (P0-429). It was `min(Retry-After,
    120)` slept by this caller alone -- `backing off 0s` seventeen times in
    six seconds in production, while every other caller kept its slots. Now
    the 429 arms the throttle's own escalating cooldown (Throttle.note_429:
    at least COOLDOWN_FLOOR_S, a longer Retry-After honoured) and the next
    `throttle.wait()` -- this caller's retry and every other normal
    caller's next request alike -- is held until it ends. A 2xx sent after
    the cooldown was armed resets it."""
    throttle = data_api_throttle()
    resp: httpx.Response | None = None
    for attempt in range(retries + 1):
        await throttle.wait()
        sent_at = _clock()
        resp = await http.get(url, params=params)
        applied = observe_data_api_response(resp, sent_at=sent_at,
                                            path=str(url).split("?", 1)[0],
                                            throttle=throttle) or {}
        if getattr(resp, "status_code", None) != 429:
            return resp
        log.warning("data-api 429 — the host's cooldown is %.1fs (%s, %d consecutive; "
                    "attempt %s): no data-api request until it ends",
                    float(applied.get("seconds_left") or 0.0),
                    applied.get("cooldown_is") or applied.get("why"),
                    int(applied.get("consecutive") or 0), attempt + 1)
    return resp  # last 429 — caller's raise_for_status surfaces it
