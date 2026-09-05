"""The workers process gets the API's memory guards, plus a memory watch
that names the moment RSS jumps.

THE INCIDENT (2026-09-05, all times UTC, read from Render's event log).
sportsassets-workers (python -m sportsassets.workers.all, one process,
2 GiB) was OOM-killed at 17:59:41, 18:05:24, 18:10:54, 18:16:25,
18:21:45, 18:28:15, 18:36:19, 18:47:24, 18:52:47, 18:58:32, 19:38:47,
20:02:28 and 20:21:49 -- every five to ten minutes until the analytics
replay was bounded (d18cc72, ebc24ce, live 19:01-19:06), every twenty
to twenty-four after. Something still ratchets RSS from boot to 2 GiB
in about twenty minutes; the log lines in the seconds before a kill are
only the steady-state mix, so the log cannot say which loop's cycle
moves the number; and the process had NO instrument for its own memory.
The API had the same ratchet in August and answered it with an arena
cap at import and a periodic malloc_trim (tests/test_arena_cap.py);
workers/all.py had neither.

Pinned against the REAL memory_watch, with the clock (all._now), the
sleeper (asyncio.sleep, through the module, the way
test_workers_boot_stagger records it), procmem.rss_mb,
procmem.malloc_trim and the heartbeat faked and recorded around it.
Every log line is pinned by its EXACT text: the reviewers run mutation
passes, and a substring survives a wording mutant.

  * a reading WORKERS_MEM_JUMP_MB (64) above the previous one is ONE
    warning, `workers rss jump +64 MB in 5s: 1000 -> 1064 MB`, the
    seconds from the clock, once per jump and never again for a level
    that merely stays high; a +63 step (and +63.9) is none, a release
    is none; a step that only float arithmetic puts a hair under 64 is
    one; the figures are rounded, not truncated; an unreadable reading
    between two readable ones neither warns nor resets the baseline
  * every WORKERS_TRIM_INTERVAL_S the trim runs on the watch's OWN
    thread (memory.trim, not the default executor every venue call
    queues on), and the readings before and after are taken IN THAT
    THREAD, either side of the trim and nothing else; EVERY reading --
    the cycle's sample, the before, the after -- is judged for a jump
    before it becomes the baseline, so a jump that lands during the
    trim is warned and never booked as a negative return; the INFO
    line carries after, since boot, peak, returned (never below zero)
    and the arena status: `workers rss 1250 MB (boot +250, peak 1300)
    trim returned 50 MB arena=arena_max=2 rc=1`
  * the heartbeat is 'workers_memory', its detail is exactly {rss_mb,
    boot_mb, peak_mb, trim_freed_mb, rss_delta_mb, jumps, arena}, its
    status is 'high' from WORKERS_MEM_HIGH_MB (1536) up, 'ok' a tenth
    below, judged on the reading AFTER the trim, and peak_mb is the
    peak, not the sample
  * the beat is a task the loop owns: one that raises is one 'not
    written' line naming the exception's type (a saturated pool's
    TimeoutError has an empty str), one that HANGS is skipped with a
    line the next time and cancelled when the loop leaves, and neither
    stops a sample or a trim (db.heartbeat goes through get_pool, whose
    walk against a dead database is ~105 s; awaited, that would hold
    the sampling for 105 s at every trim on exactly the night the
    watch is for)
  * a cancel that lands INSIDE the trim ends the watch under a ceiling
    -- a regression that swallowed cancellation fails here, it does
    not hang the suite -- and the trim thread is gone afterwards
  * rss None (no procfs): no jump, '?' for every unreadable figure,
    None in the beat, and the trim still runs
  * a malloc_trim that cannot run: 'trim returned 0 MB' and ONE warning
  * a cycle that raises -- any Exception, on the loop or in the trim
    thread -- is logged with its traceback and the next sample happens
  * the env knobs parse-or-default and floor: a sample interval below
    1 s is 1 s, a trim interval below 5 s is 5 s, a jump threshold
    below 1 MB is 1 MB (at 0 every flat sample is a "jump"), a value
    that does not parse is the default, each with a line
  * the clock is time.monotonic through all._now, and the rig's clock
    starts far from zero: a loop that took 0.0 for its first reading
    or its last trim instead of _now() trims and beats on its first
    cycle here
  * LOOPS ends with ("memory", memory_watch), the poller is still index
    0, and the arena cap runs at import after the loop imports -- early
    enough because a fresh interpreter importing the module has exactly
    one thread by Python's count and by the kernel's, which this file
    measures rather than asserts
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest

# Importing workers.all pulls every worker's third-party deps into the
# test process; the push dependency is optional in this environment
# (the same stub test_workers_boot_stagger and test_retention use).
sys.modules.setdefault("pywebpush", types.SimpleNamespace(
    webpush=None, WebPushException=Exception))

from sportsassets import procmem  # noqa: E402
from sportsassets.workers import all as all_mod  # noqa: E402

_REAL_SLEEP = asyncio.sleep
LOGGER = "sportsassets.workers.all"
ARENA = "arena_max=2 rc=1"
TRIM_THREAD = "memory.trim_0"          # the watch's own pool: one thread, named
SKIPPED = "workers_memory heartbeat skipped: the previous one is still in flight"
DETAIL_KEYS = {"rss_mb", "boot_mb", "peak_mb", "trim_freed_mb", "rss_delta_mb",
               "jumps", "arena"}
_KNOBS = ("WORKERS_MEM_SAMPLE_S", "WORKERS_MEM_JUMP_MB",
          "WORKERS_TRIM_INTERVAL_S", "WORKERS_MEM_HIGH_MB")
BACKEND = Path(__file__).resolve().parents[1]
# The rig's clock does not start at zero, so `last_trim = 0.0` or
# `prev_at = 0.0` in place of _now() shows up on the first cycle. A
# half, so that the whole and half seconds the sleeper adds stay exact
# in floating point and 'in 5s' is 5, not 4.999999999.
_T0 = 12345.5


class _Stop(BaseException):
    """Ends memory_watch from the sleeper: not an Exception, so the
    loop's own containment cannot swallow it."""


def _rig(monkeypatch, caplog, *, rss, sleeps, env=None, trim=True, beat=None,
         stop=True, arena=ARENA, trim_gate=None) -> dict:
    """Everything memory_watch touches, faked and recorded.

    rss is the sequence procmem.rss_mb serves, one value per read, the
    last one repeated once the list is spent; an Exception in the
    sequence is raised by that read. Each read records the thread it
    ran on. The clock (all._now) starts at _T0 and advances only when
    the loop sleeps, by what it slept, so 'in 5s' on a jump line is the
    clock's figure. The sleeper yields once through the real sleep --
    so a beat task started in the same cycle gets its first step -- and
    raises _Stop from the `sleeps`-th sleep when `stop` (a driven test
    can set st["stop_after"] = 0 later to end a loop it lost control
    of). The trim records the seconds since _T0, the thread it ran on
    and how many reads had happened; with trim_gate=(entered, release)
    it sets `entered` and then blocks on `release`, so a test can land
    a cancel while the trim is in flight. The beat records (service,
    status, detail) and can raise a given exception or hang."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    for key in _KNOBS:
        monkeypatch.delenv(key, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    st: dict = {"clock": _T0, "sleeps": [], "reads": 0, "read_threads": [],
                "trims": [], "reads_at_trim": [], "beats": [],
                "stop_after": sleeps if stop else None}
    seq = list(rss)

    def _rss():
        st["reads"] += 1
        st["read_threads"].append(threading.current_thread().name)
        value = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(value, Exception):
            raise value
        return value

    def _trim():
        st["trims"].append((st["clock"] - _T0, threading.current_thread().name))
        st["reads_at_trim"].append(st["reads"])
        if trim_gate is not None:
            entered, release = trim_gate
            entered.set()
            release.wait(5.0)
        return trim

    async def _beat(service, status="ok", detail=None):
        st["beats"].append((service, status, detail))
        if beat == "hang":
            await asyncio.Event().wait()          # never
        if isinstance(beat, BaseException):
            raise beat
        await _REAL_SLEEP(0)

    async def _sleep(seconds, *args, **kwargs):
        st["sleeps"].append(seconds)
        st["clock"] += seconds
        await _REAL_SLEEP(0)
        if st["stop_after"] is not None and len(st["sleeps"]) >= st["stop_after"]:
            raise _Stop()

    monkeypatch.setattr(all_mod, "_now", lambda: st["clock"])
    monkeypatch.setattr(asyncio, "sleep", _sleep)
    monkeypatch.setattr(procmem, "rss_mb", _rss)
    monkeypatch.setattr(procmem, "malloc_trim", _trim)
    monkeypatch.setattr(all_mod, "heartbeat", _beat)
    monkeypatch.setattr(all_mod, "_ARENA_STATUS", arena)
    return st


def _run() -> None:
    """memory_watch to its _Stop, under a wall-clock ceiling so a loop
    that lost its pace is a failure in seconds, not a hung suite."""
    with pytest.raises(_Stop):
        asyncio.run(asyncio.wait_for(all_mod.memory_watch(), 5.0))


def _lines(caplog, level: int, prefix: str | None = None) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == LOGGER and r.levelno == level
            and (prefix is None or r.getMessage().startswith(prefix))]


def _trim_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith("memory.trim")]


def _wait_for_the_trim_thread_to_go(within: float = 2.0) -> list[str]:
    """The pool is shut down on the loop's way out; its one thread
    leaves as soon as it is idle."""
    deadline = time.monotonic() + within
    while _trim_threads() and time.monotonic() < deadline:
        time.sleep(0.01)
    return _trim_threads()


# ------------------------------------------------------------------ jumps

@pytest.mark.parametrize("rss, expected", [
    ([1000.0, 1064.0], ["workers rss jump +64 MB in 5s: 1000 -> 1064 MB"]),
    ([1000.0, 1063.0], []),
    ([1000.0, 1063.9], []),
    # 1024.1 - 960.1 is 63.999999999999886 in floating point; the
    # readings are one-decimal figures, so the step is rounded to one
    # decimal before it is judged and printed
    ([960.1, 1024.1], ["workers rss jump +64 MB in 5s: 960 -> 1024 MB"]),
    # a release is not a jump: the line names the moment memory is
    # TAKEN, and a step of -64 is the trim (or a cycle ending) giving
    # it back
    ([1064.0, 1000.0], []),
], ids=["+64", "+63", "+63.9", "+64 through float noise", "a release is not a jump"])
def test_a_step_of_the_threshold_is_one_warning_and_a_step_below_it_is_none(
        monkeypatch, caplog, rss, expected):
    st = _rig(monkeypatch, caplog, rss=rss, sleeps=2)
    _run()
    assert st["sleeps"] == [5.0, 5.0]
    assert st["reads"] == 2
    assert _lines(caplog, logging.WARNING) == expected


def test_the_seconds_on_the_jump_line_are_the_clocks(monkeypatch, caplog):
    st = _rig(monkeypatch, caplog, rss=[1000.0, 1064.0], sleeps=2,
              env={"WORKERS_MEM_SAMPLE_S": "2"})
    _run()
    assert st["sleeps"] == [2.0, 2.0]
    assert _lines(caplog, logging.WARNING) == [
        "workers rss jump +64 MB in 2s: 1000 -> 1064 MB"]


def test_the_jump_threshold_is_tunable(monkeypatch, caplog):
    st = _rig(monkeypatch, caplog, rss=[1000.0, 1010.0], sleeps=2,
              env={"WORKERS_MEM_JUMP_MB": "10"})
    _run()
    assert st["reads"] == 2
    assert _lines(caplog, logging.WARNING) == [
        "workers rss jump +10 MB in 5s: 1000 -> 1010 MB"]


def test_a_level_that_stays_high_is_not_warned_about_again(monkeypatch, caplog):
    """The step is judged against the PREVIOUS reading, not boot: a
    jump is a moment, and the line exists to name it once."""
    st = _rig(monkeypatch, caplog, rss=[1000.0, 1064.0, 1064.0, 1064.0], sleeps=4)
    _run()
    assert st["reads"] == 4
    assert _lines(caplog, logging.WARNING) == [
        "workers rss jump +64 MB in 5s: 1000 -> 1064 MB"]


def test_an_unreadable_sample_between_two_readable_ones_neither_warns_nor_resets_the_baseline(
        monkeypatch, caplog):
    """procfs blinking for one read is not an error and not a fresh
    start: the baseline stays the last readable figure, and the seconds
    on the jump line count from when it was read."""
    st = _rig(monkeypatch, caplog, rss=[1000.0, None, 1064.0], sleeps=3)
    _run()
    assert st["reads"] == 3
    assert _lines(caplog, logging.ERROR) == []
    assert _lines(caplog, logging.WARNING) == [
        "workers rss jump +64 MB in 10s: 1000 -> 1064 MB"]


def test_the_jump_lines_figures_are_rounded_not_truncated(monkeypatch, caplog):
    """1000.6 -> 1064.9 is a step of 64.3: the readings print as 1001
    and 1065 (round), not 1000 and 1064 (%d's truncation toward zero),
    so the two figures and the step agree with each other."""
    st = _rig(monkeypatch, caplog, rss=[1000.6, 1064.9], sleeps=2)
    _run()
    assert st["reads"] == 2
    assert _lines(caplog, logging.WARNING) == [
        "workers rss jump +64 MB in 5s: 1001 -> 1065 MB"]


# ------------------------------------------------------------------- trim

def test_the_trim_runs_once_per_interval_on_its_own_thread_with_a_reading_either_side(
        monkeypatch, caplog):
    """Interval 10 s, samples every 5 s: trims at 10 and 20 and nowhere
    else. In a trim cycle the reading before and the reading after are
    both taken IN THE TRIM THREAD, the trim between them and nothing
    else, and both are judged: the before against the last sample (the
    +290 at 10 s), the after as the next jump's baseline (the +145 at
    20 s is from the trimmed 1255, not the 1300 before it). The INFO
    line carries after, since boot, peak, returned and the arena
    status; the beat carries the same with the raw delta across the
    trim."""
    st = _rig(monkeypatch, caplog,
              rss=[1000.0, 1010.0, 1300.0, 1250.0, 1255.0, 1400.0, 1380.0], sleeps=5,
              env={"WORKERS_TRIM_INTERVAL_S": "10"})
    _run()
    assert st["sleeps"] == [5.0] * 5
    assert st["reads"] == 7
    assert st["trims"] == [(10.0, TRIM_THREAD), (20.0, TRIM_THREAD)]
    # the trim ran after the 3rd and the 6th read; the 4th and the 7th
    # are the readings after it -- and all four sit in the trim thread,
    # the plain samples (1st, 2nd, 5th) on the loop
    assert st["reads_at_trim"] == [3, 6]
    assert st["read_threads"] == ["MainThread", "MainThread", TRIM_THREAD, TRIM_THREAD,
                                  "MainThread", TRIM_THREAD, TRIM_THREAD]
    assert _lines(caplog, logging.INFO) == [
        "workers rss 1250 MB (boot +250, peak 1300) trim returned 50 MB arena=arena_max=2 rc=1",
        "workers rss 1380 MB (boot +380, peak 1400) trim returned 20 MB arena=arena_max=2 rc=1",
    ]
    assert _lines(caplog, logging.WARNING) == [
        "workers rss jump +290 MB in 5s: 1010 -> 1300 MB",
        "workers rss jump +145 MB in 5s: 1255 -> 1400 MB",
    ]
    assert st["beats"] == [
        ("workers_memory", "ok", {"rss_mb": 1250.0, "boot_mb": 1000.0, "peak_mb": 1300.0,
                                  "trim_freed_mb": 50.0, "rss_delta_mb": -50.0, "jumps": 1,
                                  "arena": ARENA}),
        ("workers_memory", "ok", {"rss_mb": 1380.0, "boot_mb": 1000.0, "peak_mb": 1400.0,
                                  "trim_freed_mb": 20.0, "rss_delta_mb": -20.0, "jumps": 2,
                                  "arena": ARENA}),
    ]


def test_a_jump_that_lands_during_the_trim_is_warned_and_never_booked_as_a_negative_return(
        monkeypatch, caplog):
    """Another loop allocates 64 MB while the trim thread runs: the
    reading after the trim is 64 above the reading before it. That is
    a jump between two readings like any other (the seconds are the
    clock's, which the sleeper alone advances here), 'trim returned'
    is 0 and not -64, and the beat keeps the raw +64 as rss_delta_mb."""
    st = _rig(monkeypatch, caplog, rss=[1000.0, 1000.0, 1064.0], sleeps=2,
              env={"WORKERS_TRIM_INTERVAL_S": "5"})
    _run()
    assert st["reads"] == 3
    assert st["trims"] == [(5.0, TRIM_THREAD)]
    assert _lines(caplog, logging.WARNING) == [
        "workers rss jump +64 MB in 0s: 1000 -> 1064 MB"]
    assert _lines(caplog, logging.INFO) == [
        "workers rss 1064 MB (boot +64, peak 1064) trim returned 0 MB arena=arena_max=2 rc=1"]
    assert st["beats"] == [
        ("workers_memory", "ok", {"rss_mb": 1064.0, "boot_mb": 1000.0, "peak_mb": 1064.0,
                                  "trim_freed_mb": 0.0, "rss_delta_mb": 64.0, "jumps": 1,
                                  "arena": ARENA})]


def test_the_trim_lines_figures_are_rounded_not_truncated(monkeypatch, caplog):
    """before 1300.2, after 1250.6 from a boot of 1000: rss 1251 (not
    1250), boot +251 (not +250), returned 50 (49.6, not 49)."""
    st = _rig(monkeypatch, caplog, rss=[1000.0, 1300.2, 1250.6], sleeps=2,
              env={"WORKERS_TRIM_INTERVAL_S": "5"})
    _run()
    assert _lines(caplog, logging.INFO) == [
        "workers rss 1251 MB (boot +251, peak 1300) trim returned 50 MB arena=arena_max=2 rc=1"]
    assert _lines(caplog, logging.WARNING) == [
        "workers rss jump +300 MB in 5s: 1000 -> 1300 MB"]
    assert st["beats"][0][2]["trim_freed_mb"] == 49.6
    assert st["beats"][0][2]["rss_delta_mb"] == -49.6


def test_the_trim_cadence_counts_from_the_trim_that_ran_not_from_a_schedule(
        monkeypatch, caplog):
    """Interval 12 s over 5 s samples: a trim is due when 12 s have
    passed since the LAST TRIM, which ran at 15 (the first cycle past
    12), so the next is at 30 -- not at 25, which is what booking
    last_trim as 'the schedule's 12' instead of 'the clock's 15' would
    give."""
    st = _rig(monkeypatch, caplog, rss=[1000.0], sleeps=7,
              env={"WORKERS_TRIM_INTERVAL_S": "12"})
    _run()
    assert st["sleeps"] == [5.0] * 7               # cycles at 0, 5, ..., 30
    assert st["trims"] == [(15.0, TRIM_THREAD), (30.0, TRIM_THREAD)]


def test_the_peak_on_the_line_and_in_the_beat_is_the_peak_not_the_sample(
        monkeypatch, caplog):
    """1300 two reads before the trim, 1100 then 1090 around it: the
    line and the beat say peak 1300."""
    st = _rig(monkeypatch, caplog, rss=[1000.0, 1300.0, 1100.0, 1090.0], sleeps=3,
              env={"WORKERS_TRIM_INTERVAL_S": "10"})
    _run()
    assert st["reads"] == 4
    assert _lines(caplog, logging.INFO) == [
        "workers rss 1090 MB (boot +90, peak 1300) trim returned 10 MB arena=arena_max=2 rc=1"]
    assert st["beats"][0][2]["peak_mb"] == 1300.0
    assert st["beats"][0][2]["rss_mb"] == 1090.0


def test_the_trim_runs_on_the_watchs_own_thread_not_the_default_executor():
    """malloc_trim walks every arena and can take real time: on the
    event loop that is a stall on eighteen loops, and on the default
    executor -- the pool every venue call in this process queues on --
    it waits behind a full pool and the sampling waits with it. So: a
    one-thread pool of the watch's own, named memory.trim (the trims
    above record that name), shut down FIRST in the loop's finally,
    without waiting on a trim in flight."""
    src = inspect.getsource(all_mod.memory_watch)
    assert "asyncio.to_thread(" not in src
    assert "concurrent.futures.ThreadPoolExecutor(" in src
    assert "max_workers=1" in src and 'thread_name_prefix="memory.trim"' in src
    fn = ast.parse(textwrap.dedent(src)).body[0]
    outer = [t for t in ast.walk(fn) if isinstance(t, ast.Try) and t.finalbody]
    assert len(outer) == 1, "memory_watch has exactly one try/finally: the loop's"
    first = outer[0].finalbody[0]
    assert ast.unparse(first) == "trim_pool.shutdown(wait=False)", ast.unparse(first)


def test_a_trim_that_cannot_run_is_reported_as_returned_zero_and_warned_once(
        monkeypatch, caplog):
    """procmem.malloc_trim is False off glibc. The line says 0 -- not
    the difference of two readings that the trim had nothing to do
    with -- and the warning is once per loop, not once per trim. The
    raw movement between the readings still rides the beat."""
    st = _rig(monkeypatch, caplog, rss=[1000.0, 1000.0, 990.0], sleeps=3,
              env={"WORKERS_TRIM_INTERVAL_S": "5"}, trim=False)
    _run()
    assert st["trims"] == [(5.0, TRIM_THREAD), (10.0, TRIM_THREAD)]
    assert _lines(caplog, logging.INFO) == [
        "workers rss 990 MB (boot -10, peak 1000) trim returned 0 MB arena=arena_max=2 rc=1",
        "workers rss 990 MB (boot -10, peak 1000) trim returned 0 MB arena=arena_max=2 rc=1",
    ]
    assert _lines(caplog, logging.WARNING) == [
        "workers malloc_trim unavailable: nothing is returned to the OS"]
    assert [b[2]["trim_freed_mb"] for b in st["beats"]] == [0.0, 0.0]
    assert [b[2]["rss_delta_mb"] for b in st["beats"]] == [-10.0, 0.0]


# -------------------------------------------------------------- heartbeat

@pytest.mark.parametrize("rss, env, status", [
    ([1000.0, 1535.9], {}, "ok"),
    ([1000.0, 1536.0], {}, "high"),
    ([1000.0, 2000.0], {}, "high"),
    ([1000.0, 1000.0], {"WORKERS_MEM_HIGH_MB": "100"}, "high"),
    ([1000.0, 99.9], {"WORKERS_MEM_HIGH_MB": "100"}, "ok"),
    # judged on the reading AFTER the trim, the one the beat carries
    ([1000.0, 1536.0, 1535.9], {}, "ok"),
    ([1000.0, 1535.9, 1536.0], {}, "high"),
], ids=["a tenth below", "at the threshold", "well above", "tuned down", "tuned, below",
        "before high, after a tenth below", "before a tenth below, after high"])
def test_the_heartbeat_detail_keys_and_the_high_threshold(monkeypatch, caplog, rss, env, status):
    st = _rig(monkeypatch, caplog, rss=rss, sleeps=2,
              env={"WORKERS_TRIM_INTERVAL_S": "5", **env})
    _run()
    assert len(st["beats"]) == 1
    service, got_status, detail = st["beats"][0]
    assert service == "workers_memory"
    assert got_status == status
    assert set(detail) == DETAIL_KEYS
    assert detail["rss_mb"] == rss[-1] and detail["boot_mb"] == 1000.0
    assert detail["arena"] == ARENA


@pytest.mark.parametrize("exc, line", [
    (RuntimeError("could not connect to Postgres"),
     "workers_memory heartbeat 'ok' not written: RuntimeError: could not connect to Postgres"),
    # the saturated pool's shape: db.heartbeat's acquire(timeout=)
    # raises an asyncio.TimeoutError whose str() is empty, so the line
    # names the type or says nothing at all
    (asyncio.TimeoutError(),
     "workers_memory heartbeat 'ok' not written: TimeoutError: "),
], ids=["a dead database", "a saturated pool's TimeoutError"])
def test_a_heartbeat_that_raises_is_logged_and_stops_neither_the_sampling_nor_the_trim(
        monkeypatch, caplog, exc, line):
    st = _rig(monkeypatch, caplog, rss=[1000.0], sleeps=3,
              env={"WORKERS_TRIM_INTERVAL_S": "5"}, beat=exc)
    _run()
    assert st["sleeps"] == [5.0, 5.0, 5.0]
    assert st["reads"] == 5                    # one sample, two trims with a reading either side
    assert st["trims"] == [(5.0, TRIM_THREAD), (10.0, TRIM_THREAD)]
    assert len(st["beats"]) == 2
    assert _lines(caplog, logging.WARNING) == [line, line]
    assert _lines(caplog, logging.ERROR) == []


def test_a_heartbeat_that_hangs_is_skipped_next_time_and_cancelled_when_the_loop_leaves(
        monkeypatch, caplog):
    """The dead-database shape: get_pool walks ~105 s before it raises,
    db.heartbeat's own ceilings are 10 s a leg. The watch must keep
    sampling through it, so the beat is a task the loop owns -- at
    most one in flight, a skip line when the next is due, and the
    hung one cancelled on the way out so nothing outlives the loop.

    Gated and counted on the LOOP side: the trim's INFO line and the
    skip line are logged in one synchronous stretch after the trim
    thread returns, so every trim line after the first has its skip
    line. (Gating on the thread-side trim count raced the loop under
    CPU contention: a trim could be recorded and the cancel land
    before the loop had logged that cycle.)"""
    st = _rig(monkeypatch, caplog, rss=[1000.0], sleeps=0,
              env={"WORKERS_TRIM_INTERVAL_S": "5"}, beat="hang", stop=False)

    async def _drive():
        watch = asyncio.create_task(all_mod.memory_watch(), name="memory")
        deadline = time.monotonic() + 10.0
        while len(_lines(caplog, logging.WARNING, SKIPPED)) < 2 and time.monotonic() < deadline:
            await _REAL_SLEEP(0.001)
        beats = [t for t in asyncio.all_tasks() if t.get_name() == "memory.beat"]
        watch.cancel()
        done, _pending = await asyncio.wait({watch}, timeout=5.0)
        if watch not in done:
            st["stop_after"] = 0              # a loop that swallowed the cancel ends at its next sleep
        await _REAL_SLEEP(0)
        await _REAL_SLEEP(0)
        return watch, beats, [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    watch, beats, leftover = asyncio.run(_drive())
    assert watch.done() and watch.cancelled(), "the watch did not end under the ceiling"
    trims_logged = _lines(caplog, logging.INFO, "workers rss ")
    assert len(trims_logged) >= 3, "the sampling stalled behind the hung beat"
    # every trim after the first found the hung beat still pending
    assert _lines(caplog, logging.WARNING, SKIPPED) == [SKIPPED] * (len(trims_logged) - 1)
    assert len(st["trims"]) >= len(trims_logged)
    assert len(st["beats"]) == 1, "a second beat was started behind a hung one"
    assert len(beats) == 1 and beats[0].cancelled(), "the hung beat outlived the loop"
    assert leftover == []
    assert _wait_for_the_trim_thread_to_go() == []


def test_a_cancel_that_lands_inside_the_trim_ends_the_watch_under_a_ceiling(
        monkeypatch, caplog):
    """supervise cancels the watch the way it cancels every loop, and
    at any instant the watch is most likely awaiting its trim thread.
    The cancel must come out of that await as a cancel: not swallowed
    by the cycle's containment, not logged as a failed cycle, and not
    waiting on the thread. A regression that swallowed it FAILS here
    under the ceiling; it does not hang the suite."""
    entered, release = threading.Event(), threading.Event()
    st = _rig(monkeypatch, caplog, rss=[1000.0], sleeps=0,
              env={"WORKERS_TRIM_INTERVAL_S": "5"}, stop=False,
              trim_gate=(entered, release))

    async def _drive():
        watch = asyncio.create_task(all_mod.memory_watch(), name="memory")
        loop = asyncio.get_running_loop()
        # off the loop, wait for the trim thread to be INSIDE the trim ...
        assert await loop.run_in_executor(None, entered.wait, 5.0), "the trim never ran"
        # ... so the cancel lands on the await of its future
        watch.cancel()
        try:
            done, _pending = await asyncio.wait({watch}, timeout=5.0)
        finally:
            release.set()
        if watch not in done:
            st["stop_after"] = 0
        return watch

    watch = asyncio.run(_drive())
    assert watch.done() and watch.cancelled(), "the cancel was swallowed inside the trim"
    assert st["trims"] == [(5.0, TRIM_THREAD)]
    assert _lines(caplog, logging.INFO) == []          # the trim's line was never reached ...
    assert _lines(caplog, logging.ERROR) == []         # ... and the cancel was not a 'failed cycle'
    assert st["beats"] == []
    assert _wait_for_the_trim_thread_to_go() == []


def test_the_beat_awaits_the_heartbeat_inside_its_own_try():
    fn = ast.parse(textwrap.dedent(inspect.getsource(all_mod._beat))).body[0]
    guarded = [
        t for t in ast.walk(fn) if isinstance(t, ast.Try) and t.handlers
        and any(isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                and getattr(n.value.func, "id", None) == "heartbeat"
                for s in t.body for n in ast.walk(s))]
    assert guarded, "heartbeat() is awaited outside any try"


# -------------------------------------------------------------- no procfs

def test_no_procfs_means_no_jump_question_marks_none_in_the_beat_and_still_a_trim(
        monkeypatch, caplog):
    st = _rig(monkeypatch, caplog, rss=[None], sleeps=2,
              env={"WORKERS_TRIM_INTERVAL_S": "5"})
    _run()
    assert st["reads"] == 3
    assert st["trims"] == [(5.0, TRIM_THREAD)]
    assert _lines(caplog, logging.WARNING) == []
    assert _lines(caplog, logging.INFO) == [
        "workers rss ? MB (boot ?, peak ?) trim returned ? MB arena=arena_max=2 rc=1"]
    assert st["beats"] == [
        ("workers_memory", "ok", {"rss_mb": None, "boot_mb": None, "peak_mb": None,
                                  "trim_freed_mb": None, "rss_delta_mb": None, "jumps": 0,
                                  "arena": ARENA})]


def test_procfs_that_comes_back_after_a_blank_boot_is_read_from_then_on(monkeypatch, caplog):
    """boot is the first READABLE reading, whenever that is."""
    st = _rig(monkeypatch, caplog, rss=[None, None, 1200.0, 1264.0], sleeps=4,
              env={"WORKERS_TRIM_INTERVAL_S": "5"})
    _run()
    # reads: None (t=0); trim: None -> 1200 (t=5); trim: 1264 -> 1264 (t=10);
    # trim: 1264 -> 1264 (t=15)
    assert st["reads"] == 7
    assert _lines(caplog, logging.INFO) == [
        "workers rss 1200 MB (boot +0, peak 1200) trim returned ? MB arena=arena_max=2 rc=1",
        "workers rss 1264 MB (boot +64, peak 1264) trim returned 0 MB arena=arena_max=2 rc=1",
        "workers rss 1264 MB (boot +64, peak 1264) trim returned 0 MB arena=arena_max=2 rc=1",
    ]
    assert _lines(caplog, logging.WARNING) == [
        "workers rss jump +64 MB in 5s: 1200 -> 1264 MB"]


def test_the_arena_status_on_the_line_and_in_the_beat_is_the_one_the_cap_returned(
        monkeypatch, caplog):
    """Off glibc the cap says 'unavailable: <ExcName>', and the line
    and the beat must carry THAT -- the whole point of the field is
    that the log says whether the cap took, not that it was asked."""
    st = _rig(monkeypatch, caplog, rss=[1000.0], sleeps=2,
              env={"WORKERS_TRIM_INTERVAL_S": "5"}, arena="unavailable: RigSentinel")
    _run()
    assert _lines(caplog, logging.INFO) == [
        "workers rss 1000 MB (boot +0, peak 1000) trim returned 0 MB arena=unavailable: RigSentinel"]
    assert st["beats"][0][2]["arena"] == "unavailable: RigSentinel"


# ------------------------------------------------------------ containment

@pytest.mark.parametrize("exc_type", [RuntimeError, OSError],
                         ids=["RuntimeError", "OSError (not a RuntimeError)"])
def test_a_cycle_that_raises_is_logged_and_the_next_sample_happens(monkeypatch, caplog, exc_type):
    """A memory watch that dies on its own error is the instrument
    failing in the moment it is needed. supervise would restart it,
    five seconds later, with boot and peak forgotten. Any Exception,
    not one class of them."""
    st = _rig(monkeypatch, caplog,
              rss=[1000.0, exc_type("procfs went away"), 1000.0], sleeps=3)
    _run()
    assert st["sleeps"] == [5.0, 5.0, 5.0]
    assert st["reads"] == 3
    errors = [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == [
        "workers memory watch cycle failed; next sample in 5.0s"]
    assert errors[0].exc_info and type(errors[0].exc_info[1]) is exc_type


def test_a_reading_that_raises_in_the_trim_thread_is_the_same_failed_cycle(monkeypatch, caplog):
    """The pair is read in the trim thread; an exception there comes
    back through the await and is contained the same way. The trim
    clock was already booked, so the next trim is one interval on."""
    st = _rig(monkeypatch, caplog,
              rss=[1000.0, OSError("procfs went away"), 1000.0], sleeps=3,
              env={"WORKERS_TRIM_INTERVAL_S": "5"})
    _run()
    assert st["reads"] == 4                    # sample; the raising before; before and after
    assert st["trims"] == [(10.0, TRIM_THREAD)]
    assert _lines(caplog, logging.ERROR) == [
        "workers memory watch cycle failed; next sample in 5.0s"]
    assert _lines(caplog, logging.INFO) == [
        "workers rss 1000 MB (boot +0, peak 1000) trim returned 0 MB arena=arena_max=2 rc=1"]


def test_the_clock_is_monotonic_time_through_a_module_level_indirection():
    assert all_mod._now is time.monotonic


def test_the_first_cycles_neither_trim_nor_beat_from_a_clock_that_does_not_start_at_zero(
        monkeypatch, caplog):
    """The rig's clock starts at _T0, far from zero: a loop that took
    0.0 for its last trim instead of _now() would find the whole clock
    elapsed on its first cycle and trim, beat and log at once."""
    assert _T0 > 10_000
    st = _rig(monkeypatch, caplog, rss=[1000.0], sleeps=3)
    _run()
    assert st["sleeps"] == [5.0, 5.0, 5.0]
    assert st["trims"] == [] and st["beats"] == []
    assert _lines(caplog, logging.INFO) == []


# -------------------------------------------------------------- env knobs

@pytest.mark.parametrize("raw, floor, expected, line", [
    (None, 1.0, 5.0, None),
    ("", 1.0, 5.0, None),
    ("  ", 1.0, 5.0, None),
    ("7", 1.0, 7.0, None),
    ("0.2", 1.0, 1.0, "memory watch: WORKERS_MEM_SAMPLE_S=0.2 is below the floor; using 1.0"),
    ("0.2", None, 0.2, None),
    ("abc", 1.0, 5.0, "memory watch: WORKERS_MEM_SAMPLE_S='abc' does not parse; using 5.0"),
    ("nan", 1.0, 5.0, "memory watch: WORKERS_MEM_SAMPLE_S='nan' does not parse; using 5.0"),
    ("inf", 1.0, 5.0, "memory watch: WORKERS_MEM_SAMPLE_S='inf' does not parse; using 5.0"),
], ids=["unset", "empty", "blank", "7", "below floor", "no floor", "abc", "nan", "inf"])
def test_env_float_parses_or_defaults_and_floors(monkeypatch, caplog, raw, floor, expected, line):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    if raw is None:
        monkeypatch.delenv("WORKERS_MEM_SAMPLE_S", raising=False)
    else:
        monkeypatch.setenv("WORKERS_MEM_SAMPLE_S", raw)
    got = all_mod._env_float("WORKERS_MEM_SAMPLE_S", 5.0, floor=floor)
    assert got == expected and isinstance(got, float)
    assert _lines(caplog, logging.WARNING) == ([line] if line else [])


@pytest.mark.parametrize("jump", ["0", "-5"], ids=["zero", "negative"])
def test_the_sample_trim_and_jump_floors_hold_in_the_loop(monkeypatch, caplog, jump):
    """A jump threshold of 0 (or below) would make every flat sample a
    'jump' -- five one-second samples of a flat 1000 MB are five lines
    of '+0 MB'. The floor is 1 MB, and the flat run warns nowhere."""
    st = _rig(monkeypatch, caplog, rss=[1000.0], sleeps=5,
              env={"WORKERS_MEM_SAMPLE_S": "0.2", "WORKERS_MEM_JUMP_MB": jump,
                   "WORKERS_TRIM_INTERVAL_S": "2"})
    _run()
    assert st["sleeps"] == [1.0] * 5
    # the clock is 0, 1, 2, 3, 4 at the five cycles: no trim until 5 s
    # have passed, which is never inside five one-second samples
    assert st["trims"] == []
    assert _lines(caplog, logging.WARNING) == [
        "memory watch: WORKERS_MEM_SAMPLE_S=0.2 is below the floor; using 1.0",
        f"memory watch: WORKERS_MEM_JUMP_MB={float(jump)} is below the floor; using 1.0",
        "memory watch: WORKERS_TRIM_INTERVAL_S=2.0 is below the floor; using 5.0",
    ]


def test_the_jump_floor_still_names_a_one_megabyte_step(monkeypatch, caplog):
    st = _rig(monkeypatch, caplog, rss=[1000.0, 1000.0, 1000.5, 1001.5], sleeps=4,
              env={"WORKERS_MEM_JUMP_MB": "0"})
    _run()
    assert st["reads"] == 4
    assert _lines(caplog, logging.WARNING) == [
        "memory watch: WORKERS_MEM_JUMP_MB=0.0 is below the floor; using 1.0",
        "workers rss jump +1 MB in 5s: 1000 -> 1002 MB",
    ]


def test_the_defaults_are_the_briefs(monkeypatch, caplog):
    """5 s samples, a 64 MB jump, a 60 s trim, 1536 MB is high."""
    st = _rig(monkeypatch, caplog, rss=[1000.0], sleeps=13)
    _run()
    assert st["sleeps"] == [5.0] * 13
    assert st["trims"] == [(60.0, TRIM_THREAD)]    # the 13th cycle, at 60 s, not the 12th
    src = inspect.getsource(all_mod.memory_watch)
    assert '_env_float("WORKERS_MEM_SAMPLE_S", 5.0, floor=1.0)' in src
    assert '_env_float("WORKERS_MEM_JUMP_MB", 64.0, floor=1.0)' in src
    assert '_env_float("WORKERS_TRIM_INTERVAL_S", 60.0, floor=5.0)' in src
    assert '_env_float("WORKERS_MEM_HIGH_MB", 1536.0)' in src


# ------------------------------------------------------ LOOPS and the cap

def test_loops_ends_with_the_memory_watch_and_the_poller_is_still_first():
    """LAST, so the poller stays index 0 and every existing loop's boot
    delay is unchanged (test_workers_boot_stagger pins the delays)."""
    names = [n for n, _fn in all_mod.LOOPS]
    assert all_mod.LOOPS[-1] == ("memory", all_mod.memory_watch)
    assert names[0] == "poller"
    assert names.count("memory") == 1


def test_the_cap_runs_at_import_after_the_loop_imports_and_before_anything_runs():
    src = inspect.getsource(all_mod)
    cap_at = src.index("_ARENA_STATUS = procmem.cap_malloc_arenas()")
    assert cap_at > src.index("from . import (")
    assert cap_at < src.index("LOOPS")
    assert cap_at < src.index("async def ")


def test_the_cap_took_on_this_platform():
    status = all_mod._ARENA_STATUS
    assert isinstance(status, str) and status
    if status.startswith("unavailable"):
        return
    assert status == "arena_max=2 rc=1", f"mallopt did not take: {status}"


def test_a_fresh_interpreter_importing_the_module_has_one_thread():
    """Why after the imports is early enough: an arena is created when
    a thread first mallocs, and no loop module starts a thread at
    import. Measured, not asserted -- a fresh interpreter, the module
    imported, the thread census printed: Python's count (threads it
    started) and the kernel's from /proc/self/status (threads anyone
    started, a C extension's included)."""
    code = textwrap.dedent("""
        import sys, threading, types
        sys.modules.setdefault("pywebpush", types.SimpleNamespace(
            webpush=None, WebPushException=Exception))
        from sportsassets.workers import all as a
        kernel = "?"
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("Threads:"):
                        kernel = line.split()[1]
        except OSError:
            pass
        print("census", threading.active_count(),
              ",".join(t.name for t in threading.enumerate()), kernel, a._ARENA_STATUS)
    """)
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("census ")][-1]
    _, count, names, kernel, status = line.split(" ", 4)
    assert (count, names) == ("1", "MainThread"), line
    assert status.startswith("arena_max=2 rc=") or status.startswith("unavailable: "), line
    if kernel == "?":
        pytest.skip("no /proc here: the kernel's thread count cannot be read")
    assert kernel == "1", line
