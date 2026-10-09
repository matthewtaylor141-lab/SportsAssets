"""THE API PROCESS'S CPU LANE: one worker thread for pure-Python compute.

WHY ONE THREAD. Pure-Python work moved off the event loop with
`asyncio.to_thread` still competes with the loop for the GIL, and the cost
grows with the number of such threads, not with the work: CPython hands the
GIL over every switch interval (5 ms) to whichever waiter the OS wakes, so a
loop thread that needs it back waits behind every other runnable thread.
Measured on this class of machine (the loop ticks every 2 ms while N threads
run a pure-Python loop): the longest tick gap is 6 ms with none, 19 ms with
one, 73 ms with two, 197 ms with four and 309 ms with eight. The RC5 API ran
up to eight such jobs at once -- the intel shadow cycle (calibration,
attribution, regime, risk), the research labeller, Derek's model check, the
coverage census, the capital blocker census, the pair-observation labeller
-- each moved off the loop on its own, all landing in the default executor
together (production stall ring 2026-10-09 00:52-01:29Z, research-sql
rc6_api-responsive_loop_stalls.sql: 20 stalls of 2.3-3.5 s, every one in
one of those jobs or a desk sweep).

So every pure compute job of the API process runs here, one at a time, in
arrival order. The jobs are background reads of hundreds of milliseconds to
a few seconds; queueing them behind each other costs their callers latency,
not correctness, and keeps the loop's worst wait to one other thread's
switch interval. I/O-bound blocking calls (venue SDK reads, file reads) stay
on `asyncio.to_thread` -- they release the GIL while they wait, and a
queue here would serialise network latency.

(RC6.1 api-stall2) The RC6.1 production watchdog (2026-10-09 14:43-15:15Z,
render-ops logs run 37949540216) named three more pure jobs still on the
event loop; they run here now: the profitability cycle's warehouse lineage
(8.7 s on the loop), the rn1x model's dataset preparation (1.2 s) and the
rn1x learn evaluation (1.8 s).

NO JOB HOLDS THE LANE FOR LONG. Every job queued here waits for the one
running, and some callers wait under a deadline: the paper decision's
context rebuild runs its model check here under an 8 s hook timeout, and a
Command read holds a pooled connection while its tally runs here. So a
computation that can run for many seconds is given the lane in slices, or
kept off it. The learn evaluation runs through `run_steps`, at most SLICE_S
(plus one step) per job. The warehouse lineage is built in slices of the
same length by its own runner (profitability/warehouse.build_some; that
layer may not import this module). The rn1x model fit -- 30-50 s in one
call that cannot be cut, and already off the loop -- stays on its own
`asyncio.to_thread`, where it costs the loop GIL turns but costs no lane
job its place in the queue.

The lane copies the caller's context (as `asyncio.to_thread` does), so
context variables -- the venue-pace lane, the request id -- reach the job.
Its counters (jobs, the longest wait and run, what is running) are read by
`status()` for the loop-health readback. A fork gets a fresh lane.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import functools
import os
import threading
import time

#: worker threads in the lane. One is the point (module docstring).
WORKERS = 1
THREAD_PREFIX = "api-cpu"
#: (RC6.1 api-stall2) the longest one job of a stepped computation
#: (`run_steps`) holds the lane before it lets the next queued job run. A
#: scheduling bound: it changes when the steps run, never what they compute.
SLICE_S = 0.25

_lock = threading.Lock()
_pool: concurrent.futures.ThreadPoolExecutor | None = None
_stats: dict = {"jobs": 0, "failed": 0, "queued": 0, "max_wait_s": 0.0,
                "max_run_s": 0.0, "max_run_job": None, "running": None}


def _executor() -> concurrent.futures.ThreadPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=WORKERS, thread_name_prefix=THREAD_PREFIX)
        return _pool


def _after_fork() -> None:
    """A forked child has no worker threads: drop the parent's pool."""
    global _pool, _lock
    _pool = None
    _lock = threading.Lock()
    _stats.update(queued=0, running=None)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _name(fn) -> str:
    f = getattr(fn, "func", fn)
    return "%s.%s" % (getattr(f, "__module__", "?"),
                      getattr(f, "__qualname__", getattr(f, "__name__", "?")))


def _timed(call, name: str, queued_at: float, flag: list):
    started = time.monotonic()
    wait = started - queued_at
    with _lock:
        flag[0] = True
        _stats["queued"] -= 1
        _stats["running"] = name
        if wait > _stats["max_wait_s"]:
            _stats["max_wait_s"] = round(wait, 3)
    ok = False
    try:
        out = call()
        ok = True
        return out
    finally:
        ran = time.monotonic() - started
        with _lock:
            _stats["jobs"] += 1
            _stats["running"] = None
            if not ok:
                _stats["failed"] += 1
            if ran > _stats["max_run_s"]:
                _stats["max_run_s"] = round(ran, 3)
                _stats["max_run_job"] = name


async def run(fn, /, *args, **kwargs):
    """`await asyncio.to_thread(fn, *args, **kwargs)`, on the CPU lane."""
    return await _submit(_name(fn), fn, *args, **kwargs)


def _slice(steps, budget_s: float) -> tuple:
    """Advance the generator `steps` until it returns or `budget_s` has
    passed since this slice began (checked after each step): (True, its
    return value) or (False, None)."""
    end = time.monotonic() + budget_s
    while True:
        try:
            next(steps)
        except StopIteration as stop:
            return True, stop.value
        if time.monotonic() >= end:
            return False, None


async def run_steps(steps, *, slice_s: float = SLICE_S):
    """Run the generator `steps` to its end on the CPU lane and return its
    return value -- the value the same generator returns when it is drained
    in one call -- in jobs of at most `slice_s` plus one step each.

    (RC6.1 api-stall2) Each job queues behind the jobs already waiting, so
    no computation holds the lane for longer than one slice, however long
    it runs in all (module docstring). Each slice is awaited before the
    next is queued, so one thread at a time advances the steps; between
    two slices the loop runs other tasks, so the steps must touch nothing
    those tasks change -- pure work over the caller's own data. An
    exception from a step propagates as it would from `run`. If the caller
    is cancelled, the steps stop at the end of the running slice."""
    mod = (getattr(getattr(steps, "gi_frame", None), "f_globals", None)
           or {}).get("__name__", "?")
    name = "%s.%s[slices]" % (mod, getattr(steps, "__qualname__", "?"))
    while True:
        done, value = await _submit(name, _slice, steps, slice_s)
        if done:
            return value


async def _submit(name: str, fn, /, *args, **kwargs):
    asyncio.get_running_loop()
    call = functools.partial(contextvars.copy_context().run, fn, *args,
                             **kwargs)
    started = [False]
    with _lock:
        _stats["queued"] += 1
    try:
        cf = _executor().submit(_timed, call, name, time.monotonic(),
                                started)
    except BaseException:
        with _lock:
            _stats["queued"] -= 1
        raise

    def _never_ran(f) -> None:
        # cancelled (the awaiting task, or shutdown) before a worker took it
        with _lock:
            if not started[0]:
                started[0] = True
                _stats["queued"] -= 1
    cf.add_done_callback(_never_ran)
    return await asyncio.wrap_future(cf)


def status() -> dict:
    with _lock:
        return dict(_stats, workers=WORKERS)


def shutdown() -> None:
    """Stop accepting jobs (the API's lifespan end). Running jobs finish."""
    global _pool
    with _lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)
