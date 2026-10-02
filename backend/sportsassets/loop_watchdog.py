"""EVENT-LOOP STALL WATCHDOG: profiling evidence for the health-check 502s.

The platform restarts the API when /healthz misses its 5 s deadline
(Render events 2026-10-01 22:47, 2026-10-02 00:34 and 02:11, each 10-25
minutes after a deploy). /healthz is async and cheap, so a miss means the
event loop could not run it: either a coroutine did synchronous work on the
loop, or another thread held the GIL long enough to starve it. Which one,
and in which code, is the question this module answers with recorded stacks
instead of a guess.

How: a coroutine stamps `_last_tick` every TICK_S. A daemon thread wakes
every TICK_S and, when the stamp is older than STALL_S, captures EVERY
thread's stack (sys._current_frames) once per stall. The watchdog thread's
own wake-up overrun is recorded too: a large overrun means the watchdog
itself could not get the GIL (a GIL-holding thread, e.g. one huge
json.loads), a small one with a stale loop stamp means the loop thread was
busy in Python and the main-thread stack names the culprit.

Evidence only. It changes no behaviour, never raises into its host, holds
no lock the loop needs, and keeps a bounded ring (RING) in memory that a
coroutine persists to ingestion_state['api.loop_stalls'] when the loop is
free again. No request data, credentials or environment values are read.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
import traceback

log = logging.getLogger(__name__)

STATE_KEY = "api.loop_stalls"
TICK_S = 0.25
STALL_S = 2.0
RING = 20
FRAMES = 18

_last_tick = time.monotonic()
_ring: list[dict] = []
_ring_lock = threading.Lock()
_dirty = threading.Event()
_started = False
_loop_thread_id: int | None = None


def _stacks(loop_tid: int | None) -> dict:
    """Every thread's innermost FRAMES frames, the loop thread first."""
    names = {t.ident: t.name for t in threading.enumerate()}
    me = threading.get_ident()
    out = {}
    for tid, frame in sys._current_frames().items():
        if tid == me:
            continue
        lines = [f"{f.filename.rsplit('/sportsassets/', 1)[-1]}:{f.lineno} "
                 f"{f.name}" for f in traceback.extract_stack(frame)[-FRAMES:]]
        label = ("LOOP " if tid == loop_tid else "") + names.get(tid, str(tid))
        out[label] = lines
    return dict(sorted(out.items(), key=lambda kv: not kv[0].startswith("LOOP")))


def _record(entry: dict) -> None:
    with _ring_lock:
        _ring.append(entry)
        del _ring[:-RING]
    _dirty.set()


def snapshot() -> list[dict]:
    with _ring_lock:
        return list(_ring)


def _watch() -> None:
    captured_for = None          # the stall (by its last tick) already captured
    expected = time.monotonic() + TICK_S
    while True:
        time.sleep(TICK_S)
        now = time.monotonic()
        overrun = max(0.0, now - expected)
        expected = now + TICK_S
        try:
            lag = now - _last_tick
            if lag >= STALL_S and captured_for != _last_tick:
                captured_for = _last_tick
                _record({"at": time.time(), "loop_lag_s": round(lag, 3),
                         "watchdog_overrun_s": round(overrun, 3),
                         "stacks": _stacks(_loop_thread_id)})
            elif captured_for is not None and captured_for != _last_tick:
                # the stall ended: stamp its full length on the entry
                with _ring_lock:
                    if _ring and "ended_lag_s" not in _ring[-1]:
                        _ring[-1]["ended_lag_s"] = round(
                            now - captured_for, 3)
                captured_for = None
                _dirty.set()
        except Exception:                                       # noqa: BLE001
            pass


async def _tick() -> None:
    global _last_tick
    while True:
        _last_tick = time.monotonic()
        await asyncio.sleep(TICK_S)


async def _persist(get_pool) -> None:
    while True:
        await asyncio.sleep(5)
        if not _dirty.is_set():
            continue
        _dirty.clear()
        rows = snapshot()
        try:
            pool = await get_pool()
            async with pool.acquire() as c:
                await c.execute(
                    "INSERT INTO ingestion_state (key, value) VALUES "
                    "($1, $2::jsonb) ON CONFLICT (key) DO UPDATE SET "
                    "value = EXCLUDED.value", STATE_KEY,
                    json.dumps({"stalls": rows, "written_at": time.time(),
                                "stall_s": STALL_S}, default=str))
            last = rows[-1] if rows else {}
            log.warning("loop stall recorded: lag %.1fs, watchdog overrun "
                        "%.1fs (%d kept in %s)", last.get("loop_lag_s", 0),
                        last.get("watchdog_overrun_s", 0), len(rows),
                        STATE_KEY)
        except asyncio.CancelledError:
            raise
        except Exception:                                       # noqa: BLE001
            _dirty.set()


def start(get_pool) -> list[asyncio.Task]:
    """Arm once per process from the running loop; returns its tasks."""
    global _started, _loop_thread_id, _last_tick
    loop = asyncio.get_running_loop()
    _last_tick = time.monotonic()   # no stall is inferred from before arming
    tasks = [loop.create_task(_tick()), loop.create_task(_persist(get_pool))]
    if not _started:
        _started = True
        _loop_thread_id = threading.get_ident()
        threading.Thread(target=_watch, name="loop-watchdog",
                         daemon=True).start()
    return tasks
