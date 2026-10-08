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

THE OFFENDER IS NAMED IN THE LOG, NOT ONLY IN THE DATABASE (RC6, 2026-10-08).
The RC5 API was restarted twice for health ('server_failed unhealthy',
render-ops events: 16:34:42Z and 18:39:26Z on srv-d9gcv6urnols73ce6er0-9z9mb,
not OOM) and this watchdog saw it coming -- 132 distinct stalls >= 2 s
between 16:32Z and 20:29Z (render-ops logs, filter 'loop stall') -- but its
log line said only `lag 2.1s, watchdog overrun 0.0s`. The stacks went to
ingestion_state, and no read-only research file reads that key, so the one
instrument built to name the blocking code named nothing anyone could read.
Correlating log timestamps was the only way left: 19 of the 24 intel
shadow cycles that day completed 0-12 s after a stall record, and 13 of the
16 ext_pinnacle cycles within 6 s of one -- suggestive, not a stack. So now:

  * the watchdog thread names, AT THE MOMENT OF THE STALL, the asyncio task
    the loop is running (its name and the coroutine it was created from,
    e.g. `intel/runner.py:run`), or none when the loop is inside a plain
    callback (a protocol, a call_soon); the innermost frames of OUR code on
    the loop thread (CULPRIT_FRAMES of them); and the innermost frames of
    any code (INNER_FRAMES), for a hold inside a library;
  * the persisted record keeps them beside the stacks, and the log line
    prints them with the stall's FULL length (`ended_lag_s`), not just the
    2 s threshold it was caught at -- every RC5 line read 2.0-2.3 s because
    that is when the capture fires, whatever the stall really lasted. One
    line per stall (about half of the RC5 stalls printed two identical
    lines: the second write, when the end was stamped, re-logged the last
    row);
  * A LAG MONITOR (the tick coroutine measures its own scheduling lag):
    every lag >= LAG_LOG_S is attributed to the holder the watchdog thread
    sampled for that same stale tick, and at most one line per
    LAG_LOG_EVERY_S reports the worst lag of the window with its holder and
    how many lags there were -- so lags under the 2 s capture threshold,
    which the ring never records, are named too. Counters (lags over
    LAG_LOG_S, over STALL_S, the worst) ride the persisted value.

THE COLLECTOR IS COUNTED TOO (RC6). 39 of the 132 RC5 stalls carried a
watchdog overrun >= 0.1 s (up to 1.1 s): the watchdog thread itself could not
run, so the GIL was held by something that is not Python bytecode on the
loop -- a big C-level parse on another thread (9 of the 10 such stalls
between 18:40Z and 20:34Z fell inside a desk sweep, which parses 15 venue
pages of 100 events with full boards), or a full garbage collection, which
stops every thread and lands in whatever code happened to allocate. A stack
cannot tell those apart, and it would name the innocent allocator. So a
gc.callbacks hook times every collection (by generation, total, longest,
and every pause >= GC_SAMPLE_S with when it ended), each stall record and
each lag line carries how much of its window the collector held, and the
counters ride the persisted value. Measurement only: no threshold, no
freeze, no collection is triggered.

What is logged is code locations and task names only: no arguments, no
locals, no request data, no environment.
"""
from __future__ import annotations

import asyncio
import collections
import gc
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
#: the innermost frames of OUR code named in a log line
CULPRIT_FRAMES = 4
#: the innermost frames of ANY code (a hold inside json, asyncpg, ssl ...)
INNER_FRAMES = 2
#: a scheduling lag at or above this is attributed and logged (rate-limited)
LAG_LOG_S = 1.0
#: at most one lag line per this many seconds; the rest are counted into it
LAG_LOG_EVERY_S = 30.0
#: a stall whose end was never stamped is logged anyway after this long
UNENDED_LOG_AFTER_S = 30.0
#: the marker that makes a frame "ours" (and the prefix every line drops)
OUR_CODE = "/sportsassets/"
#: a collector pause at or above this is kept (with when it ended), so a
#: stall or a lag can say how much of it the collector held
GC_SAMPLE_S = 0.02
#: a collector pause at or above this is counted as long
GC_LONG_S = 0.25

_last_tick = time.monotonic()
_ring: list[dict] = []
_ring_lock = threading.Lock()
_dirty = threading.Event()
_started = False
_loop_thread_id: int | None = None
_loop_ref = None
#: {"tick": the stale stamp it was sampled for, **holder} -- written by the
#: watchdog thread, read by the tick coroutine once that lag has ended
_lag_holder: dict = {}
_lag: dict = {"over_log_s": 0, "over_stall_s": 0, "max_s": 0.0}
_window: dict = {"n": 0, "worst_s": 0.0, "worst": None, "worst_gc": 0.0,
                 "since": None, "last_line": None}
#: the collector, timed by `_gc_cb` (installed once, in `start`)
_gc: dict = {"collections": 0, "total_s": 0.0, "max_s": 0.0, "long": 0,
             "by_generation": {}}
#: (monotonic end, seconds, generation) of every pause >= GC_SAMPLE_S
_gc_pauses: collections.deque = collections.deque(maxlen=256)
_gc_started: list = [None]


def _short(filename: str) -> str:
    return filename.rsplit(OUR_CODE, 1)[-1]


def _stacks(loop_tid: int | None) -> dict:
    """Every thread's innermost FRAMES frames, the loop thread first."""
    names = {t.ident: t.name for t in threading.enumerate()}
    me = threading.get_ident()
    out = {}
    for tid, frame in sys._current_frames().items():
        if tid == me:
            continue
        lines = [f"{_short(f.filename)}:{f.lineno} "
                 f"{f.name}" for f in traceback.extract_stack(frame)[-FRAMES:]]
        label = ("LOOP " if tid == loop_tid else "") + names.get(tid, str(tid))
        out[label] = lines
    return dict(sorted(out.items(), key=lambda kv: not kv[0].startswith("LOOP")))


def _frames_of(frame) -> tuple:
    """(our innermost CULPRIT_FRAMES, anyone's innermost INNER_FRAMES), each
    innermost first, as `file:line function`. Never a local or argument."""
    if frame is None:
        return [], []
    st = traceback.extract_stack(frame)
    ours = [f"{_short(f.filename)}:{f.lineno} {f.name}" for f in st
            if OUR_CODE in f.filename]
    inner = [f"{_short(f.filename).rsplit('/', 1)[-1]}:{f.lineno} {f.name}"
             for f in st[-INNER_FRAMES:]]
    return list(reversed(ours[-CULPRIT_FRAMES:])), list(reversed(inner))


def _task_label(task) -> dict:
    """{'task': its name, 'coro': where its coroutine is defined} or Nones.
    The coroutine's file and qualname name the LOOP that owns the work
    (`intel/runner.py:run`), which a bare 'Task-123' does not."""
    if task is None:
        return {"task": None, "coro": None}
    name = coro = None
    try:
        name = task.get_name()
    except Exception:                                           # noqa: BLE001
        pass
    try:
        c = task.get_coro()
        code = getattr(c, "cr_code", None) or getattr(c, "gi_code", None)
        qual = getattr(c, "__qualname__", None) or getattr(code, "co_name",
                                                            None)
        coro = (f"{_short(code.co_filename)}:{qual}" if code is not None
                else (str(qual) if qual else None))
    except Exception:                                           # noqa: BLE001
        pass
    return {"task": name, "coro": coro}


def holder(loop, loop_tid) -> dict:
    """WHO IS HOLDING THE LOOP RIGHT NOW, read from another thread: the
    current asyncio task of `loop` (None inside a plain callback), the
    innermost frames of our code on the loop thread, and its innermost
    frames of any code. Never raises."""
    out = {"task": None, "coro": None, "culprit": [], "inner": []}
    try:
        if loop is not None:
            out.update(_task_label(asyncio.current_task(loop)))
    except Exception:                                           # noqa: BLE001
        pass
    try:
        out["culprit"], out["inner"] = _frames_of(
            sys._current_frames().get(loop_tid))
    except Exception:                                           # noqa: BLE001
        pass
    return out


def describe(h: dict | None) -> str:
    """One short phrase for a holder: `task=... coro=... at=a <- b`, and the
    collector's share when it held part of the window."""
    h = h or {}
    at = " <- ".join(h.get("culprit") or []) or "-"
    inner = " <- ".join(h.get("inner") or []) or "-"
    if h.get("task") is None and h.get("coro") is None:
        who = "task=none (a loop callback, not a task)"
    else:
        who = "task=%s coro=%s" % (h.get("task"), h.get("coro"))
    gc_s = h.get("gc_s")
    gcp = (" gc=%.2fs (the collector held this long of it)" % gc_s
           if gc_s else "")
    return "%s at=%s inner=%s%s" % (who, at, inner, gcp)


def _record(entry: dict) -> None:
    with _ring_lock:
        _ring.append(entry)
        del _ring[:-RING]
    _dirty.set()


def snapshot() -> list[dict]:
    with _ring_lock:
        return list(_ring)


def lag_summary() -> dict:
    """Lags seen by this process since the watchdog started."""
    return dict(_lag, log_s=LAG_LOG_S, stall_s=STALL_S)


def _gc_cb(phase, info) -> None:
    """gc.callbacks hook: time every collection. Runs with the GIL held in
    whichever thread triggered the collection; does the least it can and
    never raises."""
    try:
        if phase == "start":
            _gc_started[0] = time.perf_counter()
            return
        t0 = _gc_started[0]
        if phase != "stop" or t0 is None:
            return
        _gc_started[0] = None
        dt = time.perf_counter() - t0
        gen = str((info or {}).get("generation"))
        _gc["collections"] += 1
        _gc["total_s"] += dt
        if dt > _gc["max_s"]:
            _gc["max_s"] = dt
        if dt >= GC_LONG_S:
            _gc["long"] += 1
        g = _gc["by_generation"].setdefault(gen, {"n": 0, "total_s": 0.0,
                                                  "max_s": 0.0})
        g["n"] += 1
        g["total_s"] += dt
        if dt > g["max_s"]:
            g["max_s"] = dt
        if dt >= GC_SAMPLE_S:
            _gc_pauses.append((time.monotonic(), dt, gen))
    except Exception:                                           # noqa: BLE001
        pass


def gc_held(since: float, until: float | None = None) -> float:
    """Seconds the collector held the process in pauses >= GC_SAMPLE_S that
    ENDED after `since` (monotonic) and, when given, by `until`."""
    try:
        return round(sum(dt for end, dt, _ in list(_gc_pauses)
                         if end >= since and (until is None or end <= until)),
                     3)
    except Exception:                                           # noqa: BLE001
        return 0.0


def gc_summary() -> dict:
    out = {k: (round(v, 3) if isinstance(v, float) else v)
           for k, v in _gc.items() if k != "by_generation"}
    out["by_generation"] = {
        k: {"n": v["n"], "total_s": round(v["total_s"], 3),
            "max_s": round(v["max_s"], 3)}
        for k, v in dict(_gc["by_generation"]).items()}
    out.update(long_s=GC_LONG_S, sample_s=GC_SAMPLE_S)
    return out


def _watch() -> None:
    global _lag_holder
    captured_for = None          # the stall (by its last tick) already captured
    sampled_for = None           # the lag (by its last tick) already attributed
    expected = time.monotonic() + TICK_S
    while True:
        time.sleep(TICK_S)
        now = time.monotonic()
        overrun = max(0.0, now - expected)
        expected = now + TICK_S
        try:
            stamp = _last_tick
            lag = now - stamp
            if lag >= LAG_LOG_S and sampled_for != stamp:
                sampled_for = stamp
                _lag_holder = dict(holder(_loop_ref, _loop_thread_id),
                                   tick=stamp)
            if lag >= STALL_S and captured_for != stamp:
                captured_for = stamp
                h = holder(_loop_ref, _loop_thread_id)
                _record({"at": time.time(), "loop_lag_s": round(lag, 3),
                         "watchdog_overrun_s": round(overrun, 3),
                         "task": h["task"], "coro": h["coro"],
                         "culprit": h["culprit"], "inner": h["inner"],
                         "gc_s": gc_held(stamp),
                         "stacks": _stacks(_loop_thread_id)})
            elif captured_for is not None and captured_for != stamp:
                # the stall ended: stamp its full length on the entry
                with _ring_lock:
                    if _ring and "ended_lag_s" not in _ring[-1]:
                        _ring[-1]["ended_lag_s"] = round(
                            now - captured_for, 3)
                        # the collector's share of the WHOLE stall
                        _ring[-1]["gc_s"] = gc_held(captured_for, now)
                captured_for = None
                _dirty.set()
        except Exception:                                       # noqa: BLE001
            pass


def _note_lag(lag: float, stamp: float, *, now: float | None = None) -> None:
    """Count one scheduling lag >= LAG_LOG_S, attribute it to the holder the
    watchdog thread sampled for the same stale stamp, and log the window's
    worst at most once per LAG_LOG_EVERY_S. Never raises."""
    try:
        now = time.monotonic() if now is None else now
        _lag["over_log_s"] += 1
        if lag >= STALL_S:
            _lag["over_stall_s"] += 1
        _lag["max_s"] = round(max(_lag["max_s"], lag), 3)
        h = _lag_holder if _lag_holder.get("tick") == stamp else None
        g = gc_held(stamp, now)
        w = _window
        if w["since"] is None:
            w["since"] = now
        w["n"] += 1
        if lag >= w["worst_s"]:
            w["worst_s"], w["worst"], w["worst_gc"] = lag, h, g
        last = w["last_line"]
        if last is not None and now - last < LAG_LOG_EVERY_S:
            return
        wg = w.get("worst_gc") or 0.0
        who = (describe(dict(w["worst"], gc_s=wg)) if w["worst"] is not None
               else "an unattributed holder (the watchdog thread did not "
                    "sample this lag)%s" % (
                        " gc=%.2fs (the collector held this long of it)" % wg
                        if wg else ""))
        log.warning("event loop lag %.1fs (worst of %d lag(s) >= %.1fs in "
                    "the last %.0fs) held by %s", w["worst_s"], w["n"],
                    LAG_LOG_S, max(0.0, now - w["since"]), who)
        w.update(n=0, worst_s=0.0, worst=None, worst_gc=0.0, since=None,
                 last_line=now)
    except Exception:                                           # noqa: BLE001
        pass


async def _tick() -> None:
    """The heartbeat the watchdog thread reads AND the lag monitor: each
    wake-up measures how late it is, and a late one is noted."""
    global _last_tick
    while True:
        stamp = time.monotonic()
        _last_tick = stamp
        await asyncio.sleep(TICK_S)
        lag = time.monotonic() - stamp - TICK_S
        if lag >= LAG_LOG_S:
            _note_lag(lag, stamp)


def stall_line(e: dict) -> str:
    """What a log line says about one recorded stall."""
    ended = e.get("ended_lag_s")
    held = ("held %.1fs" % ended) if ended is not None else "end not seen"
    return ("lag %.1fs (%s), watchdog overrun %.1fs, %s"
            % (e.get("loop_lag_s") or 0, held,
               e.get("watchdog_overrun_s") or 0, describe(e)))


def _to_log(rows: list, logged: set, *, now: float) -> list:
    """The stalls to log now: each once, when its end has been stamped (so
    the line carries its full length), or after UNENDED_LOG_AFTER_S without
    one."""
    return [e for e in rows if e.get("at") not in logged and (
        e.get("ended_lag_s") is not None
        or now - float(e.get("at") or now) >= UNENDED_LOG_AFTER_S)]


async def _persist(get_pool) -> None:
    logged: set = set()          # `at` of every stall already logged
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
                                "stall_s": STALL_S, "lag": lag_summary(),
                                "gc": gc_summary()},
                               default=str))
        except asyncio.CancelledError:
            raise
        except Exception:                                       # noqa: BLE001
            _dirty.set()
        # THE LOG LINE DOES NOT WAIT FOR THE DATABASE: a stall is often the
        # moment the pool is starved too, and a line that only prints after
        # a successful write is the line that goes missing then.
        try:
            for e in _to_log(rows, logged, now=time.time()):
                logged.add(e.get("at"))
                log.warning("loop stall recorded: %s (%d kept in %s)",
                            stall_line(e), len(rows), STATE_KEY)
            logged.intersection_update({e.get("at") for e in rows})
        except Exception:                                       # noqa: BLE001
            pass


def start(get_pool) -> list[asyncio.Task]:
    """Arm once per process from the running loop; returns its tasks."""
    global _started, _loop_thread_id, _last_tick, _loop_ref
    loop = asyncio.get_running_loop()
    _last_tick = time.monotonic()   # no stall is inferred from before arming
    _loop_ref = loop
    _loop_thread_id = threading.get_ident()
    tasks = [loop.create_task(_tick(), name="loop-watchdog-tick"),
             loop.create_task(_persist(get_pool),
                              name="loop-watchdog-persist")]
    if _gc_cb not in gc.callbacks:
        gc.callbacks.append(_gc_cb)
    if not _started:
        _started = True
        threading.Thread(target=_watch, name="loop-watchdog",
                         daemon=True).start()
    return tasks
