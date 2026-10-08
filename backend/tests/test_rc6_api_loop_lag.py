"""RC6 · THE API'S EVENT-LOOP STALLS ARE NAMED IN THE LOG (lag monitor).

PRODUCTION EVIDENCE (RC5, release 69a8a07e, 2026-10-08, read-only):

  * runtime_window.json (pm-acceptance 37836393458): sportsassets-api FAILED
    on two `server_failed` / `unhealthy` events, not OOM, 16:34:42Z and
    18:39:26Z on srv-d9gcv6urnols73ce6er0-9z9mb; workers and plane CLEAN.
  * render-ops logs (filter 'healthz'): Render's checker (10.213.24.255) got
    no answer 16:32:24-16:33:04Z (40 s), and 11 s gaps at 18:37:16-18:37:27Z
    and 18:37:34-18:37:44Z, before each restart.
  * render-ops logs (filter 'loop stall'): 192 lines = 132 distinct stalls
    >= 2 s between 16:32Z and 20:29Z, every one reading only
    `lag 2.x s, watchdog overrun 0.0s` -- the capture threshold, not the
    stall's length, and no code named. The stacks went to
    ingestion_state['api.loop_stalls'], which no read-only research file
    reads. Correlating timestamps was all that was left (19 of 24 intel
    cycles ended 0-12 s after a stall record, 13 of 16 ext_pinnacle cycles
    within 6 s of one).

PINNED HERE (each fails on 1c874c1f, where the record carries no holder,
the line names nothing and there is no lag monitor):
  1 · a stall's record names the asyncio TASK holding the loop, the
      coroutine it runs, and the innermost frames of our code;
  2 · the log line prints that holder and the stall's FULL length, once per
      stall, even when the database write fails;
  3 · the lag monitor attributes a lag under the 2 s capture threshold to
      its holder and logs the window's worst at most once per
      LAG_LOG_EVERY_S, counting the rest; a plain loop callback reads as
      "no task"; a ticking loop logs nothing;
  4 · nothing logged or persisted carries a local or an argument (code
      locations and task names only);
  5 · the persisted value carries the lag counters;
  6 · THE COLLECTOR IS TIMED: 39 of the 132 RC5 stalls carried a watchdog
      overrun >= 0.1 s (up to 1.1 s), i.e. the GIL was held by something
      other than Python on the loop -- a big C-level parse on another thread
      or a full collection, which a stack cannot tell apart. Every
      collection is timed (gc.callbacks), each stall record and lag line
      says how much of its window the collector held, and the counters are
      persisted.
"""
from __future__ import annotations

import asyncio
import collections
import gc
import json
import logging
import re
import time

import pytest

from sportsassets import loop_watchdog as W

SECRET = "sk-test-not-a-real-secret-value-7f3a"


def _block_the_loop(seconds, token=SECRET):
    """Pure-Python busy work on the loop thread. `token` is an argument a
    careless recorder would print."""
    local_secret = token + "-local"                              # noqa: F841
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        sum(range(500))


async def _blocking_coro(seconds):
    _block_the_loop(seconds)


@pytest.fixture
def wd(monkeypatch):
    """A clean watchdog whose "own code" is this test file, so the
    blocking frames above count as ours."""
    # raising=False: on a build without these knobs the tests below fail on
    # what the watchdog does, not on the fixture
    monkeypatch.setattr(W, "OUR_CODE", "/tests/", raising=False)
    monkeypatch.setattr(W, "STALL_S", 0.6)
    monkeypatch.setattr(W, "LAG_LOG_S", 0.4, raising=False)
    monkeypatch.setattr(W, "LAG_LOG_EVERY_S", 30.0, raising=False)
    W._ring.clear()
    monkeypatch.setattr(W, "_lag", {"over_log_s": 0, "over_stall_s": 0,
                                     "max_s": 0.0}, raising=False)
    monkeypatch.setattr(W, "_window", {"n": 0, "worst_s": 0.0, "worst": None,
                                       "since": None, "last_line": None},
                        raising=False)
    monkeypatch.setattr(W, "_lag_holder", {}, raising=False)
    yield W


async def _stop(tasks):
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# ── 1 · the record names the holder ──────────────────────────────────────

async def test_a_stall_record_names_the_task_the_coroutine_and_our_frames(wd):
    tasks = W.start(lambda: None)
    try:
        await asyncio.sleep(0.3)
        await asyncio.get_running_loop().create_task(
            _blocking_coro(1.4), name="rc6-blocker")
        await asyncio.sleep(0.6)
        rows = W.snapshot()
        assert len(rows) == 1, rows
        r = rows[0]
        assert r["task"] == "rc6-blocker"
        assert r["coro"].endswith(":_blocking_coro"), r["coro"]
        assert r["culprit"] and "_block_the_loop" in r["culprit"][0], r
        assert any("_block_the_loop" in x for x in r["inner"]), r["inner"]
        assert r["ended_lag_s"] >= 1.3
        assert r["gc_s"] >= 0.0          # the collector's share is recorded
    finally:
        await _stop(tasks)


# ── 2 · the log line: holder, full length, once ──────────────────────────

class _FailingPool:
    """The pool a stall usually coincides with: it cannot answer."""

    def acquire(self):
        raise TimeoutError("pool starved")


async def test_the_log_line_names_the_holder_and_the_full_length_once(
        wd, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=W.__name__)
    tasks = W.start(lambda: None)
    try:
        await asyncio.sleep(0.3)
        await asyncio.get_running_loop().create_task(
            _blocking_coro(1.5), name="rc6-blocker")
        await asyncio.sleep(0.6)
        assert W.snapshot() and "ended_lag_s" in W.snapshot()[0]
    finally:
        await _stop(tasks)

    real_sleep = asyncio.sleep
    sleeps = []

    async def fast_sleep(s):
        sleeps.append(s)
        if len(sleeps) > 3:
            raise asyncio.CancelledError
        W._dirty.set()                    # a second write: must not re-log
        await real_sleep(0)

    async def get_pool():
        return _FailingPool()

    monkeypatch.setattr(W.asyncio, "sleep", fast_sleep)
    with pytest.raises(asyncio.CancelledError):
        await W._persist(get_pool)
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("loop stall recorded")]
    assert len(lines) == 1, lines
    line = lines[0]
    assert "task=rc6-blocker" in line and "_blocking_coro" in line
    assert "_block_the_loop" in line
    held = float(line.split("(held ")[1].split("s)")[0])
    assert held >= 1.4, line


# ── 3 · the lag monitor ───────────────────────────────────────────────────

def _lag_lines(caplog):
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("event loop lag")]


async def test_a_lag_under_the_capture_threshold_is_named_and_rate_limited(
        wd, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=W.__name__)
    monkeypatch.setattr(W, "STALL_S", 30.0)      # nothing reaches the ring
    tasks = W.start(lambda: None)
    try:
        await asyncio.sleep(0.3)
        await asyncio.get_running_loop().create_task(
            _blocking_coro(0.9), name="rc6-lagger")
        await asyncio.sleep(0.4)
        lines = _lag_lines(caplog)
        assert len(lines) == 1, lines
        assert "task=rc6-lagger" in lines[0] and "_block_the_loop" in lines[0]
        assert W.snapshot() == []                # under the stall threshold
        # a second lag inside the window is counted, not printed
        await asyncio.get_running_loop().create_task(
            _blocking_coro(0.9), name="rc6-lagger-2")
        await asyncio.sleep(0.4)
        assert len(_lag_lines(caplog)) == 1
        assert W.lag_summary()["over_log_s"] >= 2
        # once the window has passed, the next line reports what it held
        W._window["last_line"] -= 31.0
        await asyncio.get_running_loop().create_task(
            _blocking_coro(1.2), name="rc6-lagger-3")
        await asyncio.sleep(0.4)
        lines = _lag_lines(caplog)
        assert len(lines) == 2, lines
        assert re.search(r"worst of [2-9] lag\(s\)", lines[1]), lines[1]
        assert "rc6-lagger-3" in lines[1]
    finally:
        await _stop(tasks)


async def test_a_lag_in_a_plain_loop_callback_reads_as_no_task(wd, caplog):
    caplog.set_level(logging.WARNING, logger=W.__name__)
    tasks = W.start(lambda: None)
    try:
        await asyncio.sleep(0.3)
        asyncio.get_running_loop().call_soon(_block_the_loop, 0.9)
        await asyncio.sleep(0.5)
        lines = _lag_lines(caplog)
        assert len(lines) == 1, lines
        assert "task=none (a loop callback" in lines[0]
        assert "_block_the_loop" in lines[0]
    finally:
        await _stop(tasks)


async def test_a_ticking_loop_logs_no_lag(wd, caplog):
    caplog.set_level(logging.WARNING, logger=W.__name__)
    tasks = W.start(lambda: None)
    try:
        for _ in range(8):
            await asyncio.sleep(0.15)
        assert _lag_lines(caplog) == []
        assert W.lag_summary()["over_log_s"] == 0
    finally:
        await _stop(tasks)


# ── 4 · code locations and task names only ───────────────────────────────

async def test_nothing_recorded_or_logged_carries_a_local_or_an_argument(
        wd, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=W.__name__)
    tasks = W.start(lambda: None)
    try:
        await asyncio.sleep(0.3)
        await asyncio.get_running_loop().create_task(
            _blocking_coro(1.2), name="rc6-blocker")
        await asyncio.sleep(0.6)
    finally:
        await _stop(tasks)
    blob = json.dumps(W.snapshot(), default=str)
    assert SECRET not in blob
    W._window["last_line"] = None
    W._note_lag(1.0, -1.0)
    real_sleep = asyncio.sleep
    n = []

    async def fast_sleep(s):
        n.append(s)
        if len(n) > 2:
            raise asyncio.CancelledError
        await real_sleep(0)

    async def get_pool():
        return _FailingPool()

    monkeypatch.setattr(W.asyncio, "sleep", fast_sleep)
    W._dirty.set()
    with pytest.raises(asyncio.CancelledError):
        await W._persist(get_pool)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "loop stall recorded" in text
    assert SECRET not in text


# ── 5 · the persisted value carries the lag counters ─────────────────────

async def test_the_persisted_value_carries_the_lag_counters(wd, monkeypatch):
    written = {}

    class _Conn:
        async def execute(self, sql, key, value):
            written[key] = json.loads(value)

    class _Acq:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Acq()

    async def get_pool():
        return _Pool()

    W._note_lag(0.5, -2.0)
    W._note_lag(0.7, -3.0)
    W._record({"at": time.time() - 60, "loop_lag_s": 2.1,
               "watchdog_overrun_s": 0.0, "stacks": {}})
    real_sleep = asyncio.sleep
    n = []

    async def fast_sleep(s):
        n.append(s)
        if len(n) > 1:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(W.asyncio, "sleep", fast_sleep)
    with pytest.raises(asyncio.CancelledError):
        await W._persist(get_pool)
    v = written[W.STATE_KEY]
    assert v["lag"]["over_log_s"] == 2 and v["lag"]["max_s"] == 0.7
    assert v["lag"]["log_s"] == W.LAG_LOG_S and v["stall_s"] == W.STALL_S
    assert {"collections", "total_s", "max_s", "long",
            "by_generation"} <= set(v["gc"])


# ── 6 · the collector is timed ────────────────────────────────────────────

@pytest.fixture
def gcw(wd, monkeypatch):
    monkeypatch.setattr(W, "_gc", {"collections": 0, "total_s": 0.0,
                                   "max_s": 0.0, "long": 0,
                                   "by_generation": {}}, raising=False)
    monkeypatch.setattr(W, "_gc_pauses", collections.deque(maxlen=256),
                        raising=False)
    monkeypatch.setattr(W, "_gc_started", [None], raising=False)
    yield W


async def test_the_watchdog_times_every_collection(gcw):
    tasks = W.start(lambda: None)
    try:
        assert W._gc_cb in gc.callbacks
        gc.collect()
        s = W.gc_summary()
        assert s["collections"] >= 1 and s["by_generation"]["2"]["n"] >= 1
        assert s["total_s"] >= 0.0
    finally:
        await _stop(tasks)


def test_a_long_pause_is_counted_and_charged_to_its_window(gcw):
    t0 = time.monotonic()
    W._gc_cb("start", {"generation": 2})
    time.sleep(0.3)
    W._gc_cb("stop", {"generation": 2})
    s = W.gc_summary()
    assert s["long"] == 1 and s["max_s"] >= 0.29
    assert s["by_generation"]["2"]["n"] == 1
    assert W.gc_held(t0) >= 0.29              # ended inside the window
    assert W.gc_held(time.monotonic()) == 0.0  # and not in a later one


def test_a_lag_line_says_how_much_the_collector_held(gcw, monkeypatch,
                                                     caplog):
    caplog.set_level(logging.WARNING, logger=W.__name__)
    stamp = time.monotonic()
    W._gc_cb("start", {"generation": 2})
    time.sleep(0.06)
    W._gc_cb("stop", {"generation": 2})
    monkeypatch.setattr(W, "_lag_holder", {
        "tick": stamp, "task": "rc6-t", "coro": "x.py:run",
        "culprit": ["a.py:1 f"], "inner": []})
    W._note_lag(1.2, stamp)
    lines = _lag_lines(caplog)
    assert len(lines) == 1 and "task=rc6-t" in lines[0]
    assert re.search(r"gc=0\.0[5-9]s", lines[0]), lines[0]
    # an unsampled lag stays unattributed, with the collector's share
    W._window["last_line"] = None
    stamp2 = time.monotonic()
    W._gc_cb("start", {"generation": 1})
    time.sleep(0.06)
    W._gc_cb("stop", {"generation": 1})
    W._note_lag(1.1, stamp2)
    line = _lag_lines(caplog)[-1]
    assert "an unattributed holder" in line and "task=" not in line
    assert re.search(r"gc=0\.0[5-9]s", line), line
