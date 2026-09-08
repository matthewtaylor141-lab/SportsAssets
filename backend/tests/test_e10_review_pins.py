"""E10 review pins (2026-09-08): the priority lane on the data-API
throttle, read adversarially.

The review's questions, each pinned on the code (never on the notes):
  Q1 THE RATE never exceeds the configured rate under every mix (all
     priority, all normal, alternating, a burst of 100 priority at once,
     a priority arriving while a normal is mid-wait), at a fake clock;
     env can only lower it; the 0.1 rps floor.
  Q2 STARVATION: a normal behind a continuous priority stream is served
     by the 13th slot, the counter resets on the normal grant; the
     inverse (a priority never waits behind two normals in a row); the
     worst-case queue a mirror read sees under the six-wide walk.
  Q3 WHO IS PRIORITY: one call site, by grep; no leak through a default,
     a kwargs passthrough or the cache.
  Q4 FAIRNESS among normals, cancellation in either lane, a waiter
     cancelled after its slot was granted (the slot is burned, the
     throttle consistent).
  Q5 the two bursts: 48 normal slots per RN1 walk, inside the cycle's
     cadence; _confirm_gone's outcome unchanged, a timeout never "gone".
  Q6 positions_sync: who reads api_positions (the API alone -- the UI's
     whale profile, the events view AND the edge engine's /api/signal),
     nothing in the workers; the env floor 60.
  Q7 the wall block: the counters' union, every enter has its exit.
  Q9 THE PUMP: killed mid-wait, raising once, idle -- what every waiter
     sees; the recovery on the next acquire; the strand (a defect,
     documented as a strict xfail at review time) and the burst-debt
     check.

The E10 fold (2026-09-08) closed the review's MEDIUM-1 (the pump's exit
fails its queued waiters by name, ThrottlePumpStopped, and logs its death
once), MEDIUM-2 (the mirror's own confirm on the priority lane, its wait
bounded by CONFIRM_GONE_WAIT_S), LOW-1 (a live foreign-loop waiter is
skipped, never popped) and LOW-2 (the readers of api_positions named):
the strict xfail is un-marked, the two Q9 pins of the strand and the
silence now pin the release and the log, the Q3 lane pin of the mirror's
confirm reads the priority lane, and the fold's own pins sit at the end.

The lane pins run the real Throttle at the builder's fake clock
(ratelimit._clock / _sleep); the tick pins ride the worker file's rails.
"""
import ast
import asyncio
import inspect
import logging
import pathlib
import re
import time
import warnings
from types import SimpleNamespace

import pytest

import sportsassets
from sportsassets import positions_sync, ratelimit
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import whale_exits as we
from tests.test_e10_priority_lane import _Clock, _Lanes, _names, _queue, _serve
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _Http, _Venue, _armed, _pool, _run, _tick,
)

PKG = pathlib.Path(sportsassets.__file__).resolve().parent


class _ReviewClock(_Clock):
    """The builder's clock plus a sleep that RAISES once (a pump that
    dies of an exception, Q9)."""

    def __init__(self):
        super().__init__()
        self.raise_once = False

    async def sleep(self, d):
        if self.raise_once:
            self.raise_once = False
            self.sleeps += 1
            raise RuntimeError("pump boom")
        await super().sleep(d)


@pytest.fixture
def clock(monkeypatch):
    c = _ReviewClock()
    monkeypatch.setattr(ratelimit, "_clock", c.now)
    monkeypatch.setattr(ratelimit, "_sleep", c.sleep)
    return c


async def _hop(n=3):
    for _ in range(n):
        await asyncio.sleep(0)


async def _step(clock, hops=4):
    """Release exactly one slot: the pump sleeps on the armed gate; a
    fresh gate is armed for its NEXT sleep before this one opens."""
    ev, clock.gate = clock.gate, asyncio.Event()
    ev.set()
    await _hop(hops)


def _rate_holds(served, interval, rate):
    """Every grant one interval apart from the first and never more
    than `rate` grants inside any one-second window."""
    times = sorted(t for _, t in served)
    t0 = times[0]
    for k, t in enumerate(times):
        assert t == pytest.approx(t0 + k * interval), (k, t)
    for i, t in enumerate(times):
        assert sum(1 for u in times if t < u <= t + 1.0) <= rate, i


# ------------------------------------------------------------ Q1 the rate

@pytest.mark.parametrize("mix", ["all_priority", "all_normal", "alternating", "burst_100_priority"])
def test_review_q1_the_rate_holds_under_every_mix(clock, mix):
    thr = ratelimit.Throttle(6.0)
    arrivals = {
        "all_priority": [(f"p{i}", True) for i in range(30)],
        "all_normal": [(f"n{i}", False) for i in range(30)],
        "alternating": [(f"c{i}", i % 2 == 0) for i in range(30)],
        "burst_100_priority": [("n", False)] + [(f"p{i}", True) for i in range(100)],
    }[mix]
    served = _run(_serve(thr, clock, arrivals))
    assert len(served) == len(arrivals)
    _rate_holds(served, thr.interval, 6.0)
    # two seconds past the first grant carry exactly rate x time slots
    t0 = served[0][1]
    assert sum(1 for _, t in served if t0 < t <= t0 + 2.0 + 1e-9) == 12
    if mix == "burst_100_priority":
        # a hundred priority arrivals at once: the one normal is the 13th grant, and
        # the burst is paid at the rate, never faster
        assert _names(served)[12] == "n" and served[-1][1] - t0 == pytest.approx(100 * thr.interval)


def test_review_q1_a_priority_arriving_while_a_normal_is_mid_wait_takes_the_next_slot_at_the_rate(clock):
    async def go():
        thr = ratelimit.Throttle(6.0)
        served: list = []
        clock.gate = asyncio.Event()
        tasks = _queue(thr, clock, [("n1", False), ("n2", False), ("n3", False)], served)
        await _hop(3)                       # n1 granted; the pump asleep on n2's slot
        assert _names(served) == ["n1"] and thr.waiting() == (0, 2)
        tasks += _queue(thr, clock, [("p", True)], served)
        await _hop(1)
        clock.gate.set()
        clock.gate = None
        await asyncio.gather(*tasks)
        return served, thr
    served, thr = _run(go())
    assert _names(served) == ["n1", "p", "n2", "n3"]
    _rate_holds(served, thr.interval, 6.0)
    assert served[1][1] - served[0][1] == pytest.approx(thr.interval), "the freed slot, not an extra one"


def test_review_q1_env_can_only_lower_and_the_floor_is_a_tenth_of_a_slot_per_second(monkeypatch):
    for setting, want in ((12.0, 6.0), (6.0001, 6.0), (float("inf"), 6.0), (5.9, 5.9), (0.5, 0.5)):
        monkeypatch.setattr(ratelimit, "settings", lambda s=setting: SimpleNamespace(data_api_max_rps=s))
        assert ratelimit.data_api_rate() == want, setting
    # the floor sits in the Throttle: a zero or negative rate is one slot per ten seconds, never a raise
    assert ratelimit.Throttle(0.0).interval == 10.0 and ratelimit.Throttle(-3.0).interval == 10.0
    assert ratelimit.Throttle(0.1).interval == 10.0 and ratelimit.Throttle(6.0).interval == pytest.approx(1 / 6)
    # one construction site in the package, and it reads the capped rate
    sites = [p.relative_to(PKG).as_posix() for p in sorted(PKG.rglob("*.py"))
             if re.search(r"^\s*[^#\n]*\bThrottle\(", p.read_text(), re.M)]
    assert sites == ["ratelimit.py"], sites
    assert "Throttle(data_api_rate())" in inspect.getsource(ratelimit.data_api_throttle)
    # ONE reservation clock for both lanes: the rate cannot double under two lanes
    src = inspect.getsource(ratelimit.Throttle)
    assert src.count("self._next = ") == 2 and "self._next = max(now, self._next) + self._interval" in src


# ------------------------------------------------------- Q2 starvation

def test_review_q2_a_normal_behind_a_continuous_priority_stream_is_served_by_the_13th_and_the_counter_resets(clock):
    async def go():
        thr = ratelimit.Throttle(6.0)
        served: list = []
        clock.gate = asyncio.Event()
        tasks = _queue(thr, clock, [("n", False)] + [(f"p{i}", True) for i in range(1, 15)], served)
        await _hop(3)                       # the first grant, at once; the pump on the gate
        assert _names(served) == ["p1"] and thr._burst == 1
        for k in range(2, 13):              # the stream: one priority per slot, the counter climbing
            await _step(clock)
            assert _names(served)[-1] == f"p{k}" and thr._burst == k, (k, _names(served), thr._burst)
        await _step(clock)                  # the 13th slot
        assert _names(served)[12] == "n", "the normal is served by the 13th slot"
        assert thr._burst == 0, "the run resets on the normal grant"
        await _step(clock)
        assert _names(served)[13] == "p13" and thr._burst == 0, \
            "the stream resumes; no normal waits now, so the run is not counted (it restarts at the next normal)"
        clock.gate.set()
        clock.gate = None
        await asyncio.gather(*tasks)
        return served, thr
    served, thr = _run(go())
    assert served[12][1] - served[0][1] == pytest.approx(12 * thr.interval), "2.0 s at the ceiling"


def test_review_q2_the_inverse_a_priority_never_waits_behind_two_normals_in_a_row(clock):
    thr = ratelimit.Throttle(6.0)
    served = _run(_serve(thr, clock, [(f"n{i}", False) for i in range(1, 6)]
                         + [(f"p{i}", True) for i in range(1, 31)]))
    names = _names(served)
    normals_at = [i for i, n in enumerate(names) if n.startswith("n")]
    # while a priority waited (the first 30 + 2 grants), consecutive normal grants are 13 apart
    while_waiting = [i for i in normals_at if i < names.index("p30")]
    assert while_waiting == [12, 25], while_waiting
    # the priority behind the bound's normal is served next: one normal ahead of it, never two
    assert names[13] == "p13" and names[26] == "p25"
    # the priority lane drained, the normals FIFO among themselves
    assert [n for n in names if n.startswith("n")] == [f"n{i}" for i in range(1, 6)]


def test_review_q2_the_worst_case_queue_a_mirror_read_sees_under_the_six_wide_walk(clock):
    from sportsassets.analytics import mirror_live_rules as rules
    assert rules.MIRROR_BOOK_CONCURRENCY == 6 and ml.FAST_TICK_MAX == 5
    # a read arriving sixth in a wave, twenty telemetry pages queued, the run counter at its worst
    # (the previous wave used the whole bound): one normal, five siblings, then this read
    thr = ratelimit.Throttle(6.0)
    thr._burst = ratelimit.PRIORITY_BURST
    served = _run(_serve(thr, clock, [(f"n{i}", False) for i in range(20)]
                         + [(f"p{i}", True) for i in range(1, 7)]))
    names = _names(served)
    assert names[:7] == ["n0", "p1", "p2", "p3", "p4", "p5", "p6"]
    worst = served[6][1] - served[0][1]
    assert worst == pytest.approx(6 * thr.interval) and worst <= 1.0 + 1e-9, "six slots: 1.0 s at 6 rps"
    # the same wave with the counter at rest: five siblings, 0.83 s
    thr2 = ratelimit.Throttle(6.0)
    served2 = _run(_serve(thr2, clock, [(f"n{i}", False) for i in range(20)]
                          + [(f"p{i}", True) for i in range(1, 7)]))
    assert _names(served2)[:6] == [f"p{i}" for i in range(1, 7)]
    assert served2[5][1] - served2[0][1] == pytest.approx(5 * thr2.interval)
    # the fast tick reads its markets ONE BY ONE (no siblings): at most one normal ahead
    src = inspect.getsource(ml._fast_tick)
    assert "one by one" in src and "gather(" not in src


# ------------------------------------------------- Q3 who is priority

def _call_args(text, at):
    """The text between the balanced parentheses opening at `at`."""
    depth = 0
    for i in range(at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[at + 1:i]
    raise AssertionError("unbalanced call")


def test_review_q3_the_one_priority_call_site_and_no_leak():
    calls = {}
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text()
        for m in re.finditer(r"^[^#\n]*\bmarket_positions\(", text, re.M):
            if "def market_positions" in m.group(0):
                continue
            calls.setdefault(path.relative_to(PKG).as_posix(), []).append(_call_args(text, m.end() - 1))
    assert list(calls) == ["workers/mirror_live.py"] and len(calls["workers/mirror_live.py"]) == 1, calls
    args = calls["workers/mirror_live.py"][0]
    assert "priority=True" in args and "**" not in args, "an explicit literal, no kwargs passthrough"
    # the call lives in _market_snap alone, and every consumer of the read passes through it
    assert "market_positions(" in inspect.getsource(ml._market_snap)
    for fn in (ml._read_market, ml._tick_book, ml._tick_candidate, ml._fast_book, ml._fast_candidate,
               ml._confirm_gone, ml._snapshot):
        assert "market_positions(" not in inspect.getsource(fn), fn.__name__
    # the normal callers never say priority: the walk, the callee, the cycle's confirm site,
    # polite_get (the mirror's OWN confirm site is a priority caller since the fold's MEDIUM-2:
    # pinned below, and by AST over the package in the fold's pins)
    for fn in (we._fetch_positions, we._confirm_gone, we._cycle, ratelimit.polite_get):
        body = re.sub(r"#.*", "", inspect.getsource(fn).split('"""', 2)[-1])
        assert "priority" not in body, fn.__name__
    # the flag is keyword-only with a False default: a positional caller cannot reach it
    sig = inspect.signature(we.market_positions)
    p = sig.parameters["priority"]
    assert p.default is False and p.kind is inspect.Parameter.KEYWORD_ONLY
    # the per-tick cache holds the read's ANSWER, never the lane: a cached partial cannot leak a flag
    snap = inspect.getsource(ml._market_snap)
    assert "t.mkts[" in snap and "priority" not in snap.split("t.mkts[")[0].split('"""')[-1]


def test_review_q3_mirror_lives_own_confirm_gone_takes_the_priority_lane(monkeypatch):
    """Pinned the normal lane at review time (MEDIUM-2 named it); the fold
    moved this site -- the mirror's own money read on its exit path, inside
    the walk -- onto the priority lane, whale_exits._cycle's site staying
    normal (the Q3 no-leak pin above and the builder's cycle pin)."""
    lanes = _Lanes()
    monkeypatch.setattr(ratelimit, "_throttle", lanes)
    p = _pool()
    http = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0}, {"conditionId": CID, "asset": N, "size": 0}])
    t = ml._Tick(pool=p, pmus=_Venue(), http=http, now=NOW, stats=ml._new_stats())
    assert _run(ml._confirm_gone(t, "rn1", M)) is True and lanes.calls == ["priority"]


# ------------------------------------------------------- Q4 fairness

def test_review_q4_fifo_among_three_normals_with_one_priority_and_cancellation_in_either_lane(clock):
    thr = ratelimit.Throttle(6.0)
    served = _run(_serve(thr, clock, [("n1", False), ("n2", False), ("p", True), ("n3", False)]))
    assert _names(served) == ["p", "n1", "n2", "n3"]

    async def go():
        thr = ratelimit.Throttle(6.0)
        served: list = []
        tasks = _queue(thr, clock, [("n1", False), ("p1", True), ("p2", True), ("n2", False), ("n3", False)], served)
        await _hop(2)                       # p1 granted; the pump asleep
        tasks[2].cancel()                   # a PRIORITY waiter cancelled in the queue
        tasks[3].cancel()                   # a NORMAL waiter cancelled in the queue
        out = await asyncio.gather(*tasks, return_exceptions=True)
        return served, thr, out
    served, thr, out = _run(go())
    assert _names(served) == ["p1", "n1", "n3"]
    assert isinstance(out[2], asyncio.CancelledError) and isinstance(out[3], asyncio.CancelledError)
    _rate_holds(served, thr.interval, 6.0)
    assert served[2][1] - served[0][1] == pytest.approx(2 * thr.interval), "no slot lost to the cancelled two"
    assert thr.waiting() == (0, 0) and thr._burst == 0 and not thr._priority and not thr._normal


def test_review_q4_a_waiter_cancelled_after_its_slot_was_granted_burns_that_slot_and_nothing_else(clock):
    async def go():
        thr = ratelimit.Throttle(6.0)
        served: list = []
        tasks = _queue(thr, clock, [("n1", False), ("n2", False), ("n3", False)], served)
        await _hop(2)
        n2 = tasks[1]
        for _ in range(10):                 # until the pump has set n2's result and n2 has not yet run
            fw = n2._fut_waiter
            if fw is not None and fw.done():
                break
            await asyncio.sleep(0)
        assert fw is not None and fw.done() and not n2.done()
        n2.cancel()
        out = await asyncio.gather(*tasks, return_exceptions=True)
        return served, thr, out
    served, thr, out = _run(go())
    assert _names(served) == ["n1", "n3"] and isinstance(out[1], asyncio.CancelledError)
    # the granted slot went unused (the conservative side: under the rate, never over it)
    assert served[1][1] - served[0][1] == pytest.approx(2 * thr.interval)
    assert thr.waiting() == (0, 0) and thr._burst == 0 and not thr._normal and not thr._priority


# --------------------------------------------------------- Q9 the pump

def test_review_q9_a_pump_killed_mid_wait_fails_its_queue_by_name_and_the_next_acquire_restarts_it(clock, caplog):
    """The review's harness of the strand (n1 granted, the pump cancelled,
    n3 stranded over 200 hops until `p` arrived), now reading the fold's
    MEDIUM-1: n3 is failed with ThrottlePumpStopped by the pump's own exit,
    and the next acquire from anyone restarts the pump and is served at
    the rate."""
    async def go():
        thr = ratelimit.Throttle(6.0)
        served: list = []
        tasks = _queue(thr, clock, [("n1", False), ("n2", False), ("n3", False)], served)
        await _hop(3)
        pump = thr._pump
        assert not pump.done() and thr.waiting()[1] >= 1
        pump.cancel()
        await _hop(3)
        assert pump.done() and pump.cancelled()
        # n2's slot was granted before the kill and is served; n3, still queued, is FAILED BY NAME
        # by the pump's exit -- no new arrival needed, nothing stranded over the same 200 hops
        for _ in range(200):
            await asyncio.sleep(0)
        released = (tasks[2].done() and isinstance(tasks[2].exception(), ratelimit.ThrottlePumpStopped)
                    and _names(served) == ["n1", "n2"] and thr.waiting() == (0, 0) and not thr._normal)
        # the next acquire, from anyone, restarts the pump and is served at the rate
        tasks += _queue(thr, clock, [("p", True), ("n4", False)], served)
        out = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)
        return served, thr, released, pump, out
    with caplog.at_level(logging.DEBUG):
        served, thr, released, pump, out = _run(go())
    assert released, "the waiter behind a dead pump is released by name within the pump's own exit"
    assert "normal-lane waiter" in str(out[2]) and out[3] is None and out[4] is None
    assert _names(served) == ["n1", "n2", "p", "n4"]
    _rate_holds(served, thr.interval, 6.0)
    assert thr._pump is not pump and thr._pump.done() and thr.waiting() == (0, 0)
    # the cancellation with a waiter queued is said, once, at the time; the restarted pump that then
    # ended because no one waits said nothing
    recs = [r for r in caplog.records if r.name == ratelimit.log.name]
    assert [r.levelno for r in recs] == [logging.WARNING] and "1 waiter(s) queued" in recs[0].getMessage()
    src = inspect.getsource(ratelimit.Throttle.acquire)
    assert "self._pump.done()" in src and "self._pump.get_loop() is not loop" in src


def test_review_q9_a_pump_that_raises_once_logs_its_death_once_fails_its_queue_and_the_next_acquire_recovers(clock, caplog):
    """The review's harness of the silent death (a `_sleep` that raises
    once: the pump dead of RuntimeError, no `sportsassets.ratelimit`
    record, n3 stranded over 100 hops), now reading the fold's MEDIUM-1:
    one log.exception record at the time, n3 failed with
    ThrottlePumpStopped, and the next acquire recovers at the rate."""
    async def go():
        thr = ratelimit.Throttle(6.0)
        served: list = []
        tasks = _queue(thr, clock, [("n1", False), ("n2", False), ("n3", False)], served)
        await _hop(2)                       # n1 granted; the pump in its sleep for n2's slot
        clock.raise_once = True             # the pump's NEXT sleep (for n3's slot) raises
        await _hop(4)
        pump = thr._pump
        dead = pump.done() and not pump.cancelled() and isinstance(pump.exception(), RuntimeError)
        for _ in range(100):
            await asyncio.sleep(0)
        released = (tasks[2].done() and isinstance(tasks[2].exception(), ratelimit.ThrottlePumpStopped)
                    and _names(served) == ["n1", "n2"] and thr.waiting() == (0, 0))
        tasks += _queue(thr, clock, [("p", True)], served)
        out = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)
        return served, thr, dead, released, pump, out
    with caplog.at_level(logging.DEBUG):
        served, thr, dead, released, pump, out = _run(go())
    assert dead and released, "an exception in the pump fails every queued waiter by name"
    recs = [r for r in caplog.records if r.name == ratelimit.log.name]
    assert len(recs) == 1 and recs[0].levelno == logging.ERROR, "the death is logged once, at the time"
    assert recs[0].exc_info and isinstance(recs[0].exc_info[1], RuntimeError) and "pump boom" in str(recs[0].exc_info[1])
    assert "ThrottlePumpStopped" in recs[0].getMessage() and "1 waiter(s) queued" in recs[0].getMessage()
    assert isinstance(out[2], ratelimit.ThrottlePumpStopped) and out[3] is None
    assert _names(served) == ["n1", "n2", "p"] and thr._pump is not pump
    _rate_holds(served, thr.interval, 6.0)
    pump.exception()                        # retrieved (acquire's restart did too): no GC report


def test_review_q9_a_waiter_behind_a_dead_pump_is_released_without_a_new_arrival(clock):
    """The review's strict xfail, un-marked by the fold's MEDIUM-1: released
    means failed closed by name (ThrottlePumpStopped) within the pump's own
    exit, no other caller arriving."""
    async def go():
        thr = ratelimit.Throttle(6.0)
        served: list = []
        tasks = _queue(thr, clock, [("n1", False), ("n2", False), ("n3", False)], served)
        await _hop(3)
        thr._pump.cancel()
        for _ in range(300):
            await asyncio.sleep(0)
        for t in tasks:                     # retrieved: the harness's own, no GC report
            if t.done() and not t.cancelled():
                t.exception()
        return all(t.done() for t in tasks)
    assert _run(go()), "released (served, or failed closed) without another caller arriving"


def test_review_q9_no_burst_debt_the_first_acquire_after_idle_is_served_at_once_and_the_next_waits_a_slot(clock):
    async def go():
        thr = ratelimit.Throttle(6.0)
        served: list = []
        await asyncio.gather(*_queue(thr, clock, [("a", False)], served))
        assert thr._pump.done(), "no one waits: the pump ends and the clock stands still"
        next_before = thr._next
        clock.t += 100.0                    # a hundred idle seconds: 600 slots' worth, none banked
        assert thr._next == next_before
        s0 = clock.sleeps
        await asyncio.gather(*_queue(thr, clock, [("b", True)], served))
        assert served[-1][1] == clock.t and clock.sleeps == s0, "served at once, no sleep"
        await asyncio.gather(*_queue(thr, clock, [("c", False), ("d", True)], served))
        return served, thr
    served, thr = _run(go())
    gaps = [b[1] - a[1] for a, b in zip(served[1:], served[2:])]
    assert gaps == [pytest.approx(thr.interval)] * 2, "one interval each: the idle time bought no burst"
    assert "self._next = max(now, self._next) + self._interval" in inspect.getsource(ratelimit.Throttle._serve)


def test_review_q9_a_callers_timeout_never_cancels_the_pump(clock):
    async def go():
        thr = ratelimit.Throttle(6.0)
        served: list = []
        clock.gate = asyncio.Event()
        tasks = _queue(thr, clock, [("n1", False), ("n2", False)], served)
        await _hop(3)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(thr.acquire(priority=True), timeout=0.05)
        alive = not thr._pump.done()
        assert thr.waiting() == (0, 1) and not thr._priority, "the timed-out waiter left its lane"
        clock.gate.set()
        clock.gate = None
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        return served, alive
    served, alive = _run(go())
    assert alive and _names(served) == ["n1", "n2"]


# ------------------------------------------------- Q5 the two bursts

class _FullPages:
    """RN1's book: full pages to POSITIONS_MAX, then the walk refuses."""

    def __init__(self):
        self.calls = 0

    async def get(self, path, params=None):
        self.calls += 1
        off = int(params["offset"])

        class _R:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return [{"asset": f"a{off + i}", "size": 1} for i in range(we.POSITIONS_PAGE)]
        return _R()


def test_review_q5_rn1s_walk_is_48_normal_slots_for_48_requests_inside_the_cycles_cadence(monkeypatch):
    lanes = _Lanes()
    monkeypatch.setattr(ratelimit, "_throttle", lanes)
    http = _FullPages()
    with pytest.raises(we.TruncatedPositions):
        _run(we._fetch_positions(http, "0xrn1"))
    assert http.calls == 48 == we.POSITIONS_MAX // we.POSITIONS_PAGE
    assert lanes.calls == ["normal"] * 48, "one normal slot per request, none priority"
    # 8 s of slots at the ceiling per held whale, under the cycle's own floor of 15 s between cycles;
    # main sleeps INTERVAL_S AFTER the cycle, so a slower cycle stretches the cadence and times nothing out
    assert 48 / ratelimit.DATA_API_MAX_RPS == 8.0 < 15.0
    assert 'capped_env("WHALE_EXIT_INTERVAL_S", 120.0, floor=15.0)' in inspect.getsource(we)
    main = inspect.getsource(we.main)
    assert main.index("await _cycle(http, pool)") < main.index("await asyncio.sleep(INTERVAL_S)")


def test_review_q5_confirm_gones_verdict_is_unchanged_by_the_queue_and_a_timeout_is_never_gone(monkeypatch):
    class _Slow:
        def __init__(self, s):
            self.s = s

        async def wait(self):
            await asyncio.sleep(self.s)

        async def acquire(self, priority=False):
            await asyncio.sleep(self.s)
    gone = [{"conditionId": CID, "asset": M, "size": 0}, {"conditionId": CID, "asset": N, "size": 0}]
    held = [{"conditionId": CID, "asset": M, "size": 300}, {"conditionId": CID, "asset": N, "size": 0}]
    p = _pool()
    for rows, want in ((gone, True), (held, False), ([], False)):
        for s in (0.0, 0.05):
            monkeypatch.setattr(ratelimit, "_throttle", _Slow(s))
            t = ml._Tick(pool=p, pmus=_Venue(), http=_Http(rows=rows), now=NOW, stats=ml._new_stats())
            assert _run(ml._confirm_gone(t, "rn1", M)) is want, (rows, s)
    # a caller's timeout inside the wait raises out (HOLD: the flatten is not taken), never True
    monkeypatch.setattr(ratelimit, "_throttle", _Slow(10.0))
    http = _Http(rows=gone)
    t = ml._Tick(pool=p, pmus=_Venue(), http=http, now=NOW, stats=ml._new_stats())
    with pytest.raises(asyncio.TimeoutError):
        _run(asyncio.wait_for(ml._confirm_gone(t, "rn1", M), timeout=0.05))
    assert http.calls == [], "no slot, no read"
    # a throttle that cannot be built, or a wait that raises: False, and no read (unknown is not gone)
    for bad in (lambda: (_ for _ in ()).throw(RuntimeError("no settings")),):
        monkeypatch.setattr(ratelimit, "data_api_throttle", bad)
        http = _Http(rows=gone)
        t = ml._Tick(pool=p, pmus=_Venue(), http=http, now=NOW, stats=ml._new_stats())
        assert _run(ml._confirm_gone(t, "rn1", M)) is False and http.calls == []
    # the decision reads the wrapper's bool: True/False only, through select_flatten
    src = inspect.getsource(ml._tick_book)
    assert src.count("confirm_gone = await _confirm_gone(t, w, his_token)") == 2
    assert "except Exception:" in inspect.getsource(ml._confirm_gone)


# ------------------------------------------------- Q6 positions_sync

def test_review_q6_api_positions_is_read_by_the_api_alone_and_the_floor_is_at_the_gate(monkeypatch):
    readers = {}
    for path in sorted(PKG.rglob("*.py")):
        if path.name == "positions_sync.py":
            continue                        # the writer
        n = len(re.findall(r"^[^#\n]*\bapi_positions\b", path.read_text(), re.M))
        if n:
            readers[path.relative_to(PKG).as_posix()] = n
    assert set(readers) == {"api/queries.py", "api/app.py"}, readers
    # named: the whale profile and the events view (the UI) and /api/signal (the edge engine's
    # alignment read, edge-engine/src/edge/shadow/runner.py) -- NOT "the UI only"
    from sportsassets.api import app as api_app, queries
    for fn in (queries.whale_profile, queries.events_view, api_app.api_signal):
        assert "api_positions" in inspect.getsource(fn), fn.__name__
    # nothing under workers/ or analytics/ reads it: the mirror's money path is its own reads
    for sub in ("workers", "analytics", "ingestion"):
        for path in (PKG / sub).rglob("*.py"):
            assert not re.search(r"^[^#\n]*\bapi_positions\b", path.read_text(), re.M), path.name
    # the interval is read through the floor alone
    for path in PKG.rglob("*.py"):
        text = path.read_text()
        if path.name in ("config.py", "positions_sync.py"):
            continue
        assert "positions_sync_interval_seconds" not in text, path.name
    src = inspect.getsource(positions_sync)
    assert src.count("settings().positions_sync_interval_seconds") == 1
    monkeypatch.setattr(positions_sync, "settings", lambda: SimpleNamespace(positions_sync_interval_seconds=30))
    assert positions_sync.sync_interval_s() == 60


# --------------------------------------------------- Q7 the wall clock

def test_review_q7_the_wall_clock_counts_the_union_and_every_enter_has_its_exit(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(ml.time, "monotonic", lambda: clock["t"])
    w = ml._WallClock()
    w.move("data", 1)                       # read A in flight
    clock["t"] = 101.0
    w.move("data", 1)                       # read B overlaps A
    clock["t"] = 103.0
    w.move("data", -1)                      # A done: still one in flight
    clock["t"] = 104.0
    w.move("data", -1)                      # B done
    clock["t"] = 110.0
    assert w.snapshot()["data"] == pytest.approx(4.0), "100 -> 104 once, never 3 + 3"
    # a planner step: a book in flight inside neither a venue call nor a data read
    w.move("books", 1)
    clock["t"] = 111.0
    w.move("venue", 1)                      # the book's quote read: plan pauses
    clock["t"] = 113.0
    w.move("venue", -1)
    clock["t"] = 114.0
    w.move("books", -1)
    s = w.snapshot()
    assert s["plan"] == pytest.approx(2.0) and s["venue"] == pytest.approx(2.0)
    # a decrement below zero is clamped, never negative time
    w.move("venue", -1)
    assert w._n == {"data": 0, "venue": 0, "books": 0}
    # every enter has its exit in the source: +1 and -1 paired per site
    for fn, key in ((ml._market_snap, "data"), (ml._bbo, "venue"), (ml._paced, "venue"),
                    (ml._walk_books, "books"), (ml._fast_book, "books"), (ml._tick_candidate, "books")):
        src = inspect.getsource(fn)
        assert src.count('_WALL.move("%s", 1)' % key) == 1 == src.count('_WALL.move("%s", -1)' % key), fn.__name__
        assert src.index('_WALL.move("%s", -1)' % key) > src.index("finally:"), fn.__name__


def test_review_q7_the_fast_split_is_the_wait_and_the_work_never_the_wait_twice():
    from tests.test_e9_fast_path import _walk
    p = _pool()
    p.add_book(ledger=0)
    _walk()
    p.clock = NOW + 1

    async def go():
        async with ml._TICK_LOCK:           # a full tick's hold of 0.12 s: the fast tick waits on it
            task = asyncio.create_task(ml.fast_tick_once(p, _Venue(), _Http(), cids=[CID], now_ts=NOW + 1))
            await asyncio.sleep(0.12)
        return await task
    fs = asyncio.run(go())
    acc, fast = ml._fast_wall, ml._fast_seconds["s"]
    assert fs["fast"]["placed"] == 1 and acc["wait"] >= 0.11
    assert acc["wait"] + acc["work"] == pytest.approx(fast, abs=1e-6), "the split sums to E9's seconds"
    assert acc["work"] < acc["wait"] and acc["work"] < fast - 0.1, "the work excludes the wait"
    wb = fs["short"]["wall"]
    assert wb["fast_wait"] >= 0.1 and wb["fast_work"] < wb["fast_wait"]


def test_review_q7_a_real_tick_leaves_the_counters_at_rest_and_the_walk_bounds_the_block():
    p = _pool()
    p.add_book(ledger=300)
    st = _tick(p, _Venue(held={SLUG: 300}))
    assert ml._WALL._n == {"data": 0, "venue": 0, "books": 0}
    wb, tm = st["short"]["wall"], st["short"]["timing"]
    assert max(wb["books_data_wall"], wb["books_venue_wall"], wb["books_plan_wall"]) <= tm["books"] <= st["tick_s"]
    src = inspect.getsource(ml._tick)
    assert src.index("wall0 = _WALL.snapshot()") < src.index("await _walk_books(") \
        < src.index("t.wall = _WALL.delta(wall0)") < src.index('t.timing["books"] += time.monotonic() - t0')



# ------------------------------------------------ Q8 the builder's kills, restated

def test_review_q8_the_four_kills_the_review_file_owed(monkeypatch, caplog):
    # M20: a NaN rate is the ceiling, never an interval of NaN (which would serve without a wait)
    monkeypatch.setattr(ratelimit, "settings", lambda: SimpleNamespace(data_api_max_rps=float("nan")))
    assert ratelimit.data_api_rate() == 6.0
    monkeypatch.setattr(ratelimit, "_throttle", None)
    assert ratelimit.data_api_throttle().interval == pytest.approx(1 / 6)
    # M23: the walk's retry is a request of its own and takes its own slot (source: two waits, two GETs)
    src = inspect.getsource(we._fetch_positions).split('"""')[2]
    assert src.count("await ratelimit.data_api_throttle().wait()") == 2 == src.count('http.get("/positions"')
    # M24: the default is 900 where the Settings field is declared
    from sportsassets.config import Settings
    assert Settings.model_fields["positions_sync_interval_seconds"].default == 900
    # M21: the token prints the WORK seconds, in E9's place
    stats = ml._new_stats()
    stats.update(mode=ml.MODE_ON, whales=["rn1"], books_live=1, orders_open=0)
    stats["short"]["timing"] = {"walk": 1.0, "orders": 0.1, "books": 2.0, "candidates": 0.5,
                                "read": 3, "quiet_skipped": 0, "placed": 0}
    stats["short"]["fast"] = {"fast": 0.4, "n": 2, "markets": 2, "placed": 0, "skipped": 0, "failed": 0, "calls": 0}
    stats["short"]["wall"] = {"books_data_wall": 0.1, "books_venue_wall": 0.2, "books_plan_wall": 0.3,
                              "fast_wait": 0.3, "fast_work": 0.1}
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS, None)
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")][0]
    assert " placed=0 fast=2/0.1s " in line and "fast=2/0.3s" not in line, line


# ------------------------------------ the fold (2026-09-08): MEDIUM-1 / MEDIUM-2 / LOW-1 / LOW-2

def test_fold_medium1_a_waiter_queued_when_the_pump_is_cancelled_gets_the_named_exception_within_one_step_and_the_next_acquire_restarts_the_pump(clock, caplog):
    async def go():
        thr = ratelimit.Throttle(6.0)
        served: list = []
        clock.gate = asyncio.Event()        # the pump held in its sleep for the next slot
        tasks = _queue(thr, clock, [("n1", False), ("n2", False)], served)
        await _hop(3)                       # n1 granted at once; the pump asleep on n2's slot
        tasks += _queue(thr, clock, [("p1", True)], served)
        await _hop(1)
        assert _names(served) == ["n1"] and thr.waiting() == (1, 1), "one waiter queued in EACH lane"
        pump = thr._pump
        pump.cancel()
        await asyncio.sleep(0)              # ONE loop step: the pump's exit ran
        assert pump.cancelled()
        assert thr.waiting() == (0, 0) and not thr._priority and not thr._normal, \
            "both lanes emptied within the step that ended the pump"
        assert tasks[1]._fut_waiter is None or tasks[1]._fut_waiter.done()
        await asyncio.sleep(0)              # the waiters' own step: each raises the named exception
        errs = [t.exception() for t in tasks[1:]]
        assert all(isinstance(e, ratelimit.ThrottlePumpStopped) for e in errs), errs
        assert "normal-lane waiter" in str(errs[0]) and "priority-lane waiter" in str(errs[1])
        # the next acquire, from anyone, restarts the pump and is served at the rate; the pump that
        # then ends because no one waits fails nothing
        clock.gate = None
        more = _queue(thr, clock, [("n3", False), ("p2", True)], served)
        await asyncio.wait_for(asyncio.gather(*more), timeout=5)
        assert thr._pump is not pump and thr._pump.done() and thr.waiting() == (0, 0)
        return served, thr
    with caplog.at_level(logging.DEBUG):
        served, thr = _run(go())
    assert _names(served) == ["n1", "p2", "n3"], "the restarted pump picks the lane by the rule"
    _rate_holds(served, thr.interval, 6.0)
    recs = [r for r in caplog.records if r.name == ratelimit.log.name]
    assert [r.levelno for r in recs] == [logging.WARNING] and "2 waiter(s) queued" in recs[0].getMessage()
    # by source: the loop under try/finally, the release in the finally, the exception logged once
    src = inspect.getsource(ratelimit.Throttle._serve)
    assert src.index("try:") < src.index("while self._queued(loop):") < src.index("except Exception:") \
        < src.index("log.exception(") < src.index("finally:") < src.index("self._release(loop)")
    assert issubclass(ratelimit.ThrottlePumpStopped, RuntimeError)


def test_fold_medium1_a_pump_that_ends_because_no_one_waits_fails_nothing_and_logs_nothing(clock, caplog):
    with caplog.at_level(logging.DEBUG):
        thr = ratelimit.Throttle(6.0)
        served = _run(_serve(thr, clock, [("n1", False), ("p1", True), ("n2", False)]))
    assert _names(served) == ["p1", "n1", "n2"] and thr._pump.done() and not thr._pump.cancelled()
    assert thr._pump.exception() is None and not thr._priority and not thr._normal
    assert not [r for r in caplog.records if r.name == ratelimit.log.name]


def test_fold_medium1_the_callers_fail_closed_paths_name_the_stopped_pump(monkeypatch):
    class _Stopped:
        async def wait(self):
            raise ratelimit.ThrottlePumpStopped("pump stopped")

        async def acquire(self, priority=False):
            raise ratelimit.ThrottlePumpStopped("pump stopped")
    monkeypatch.setattr(ratelimit, "_throttle", _Stopped())
    # market_positions: None, no GET (the mirror then counts snap_market_unreadable)
    http = _Http()
    assert _run(we.market_positions(http, "0xabc", CID, long_asset=M, priority=True)) is None
    assert http.calls == []
    # the mirror's own confirm: not gone, no read
    p = _pool()
    http = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0}, {"conditionId": CID, "asset": N, "size": 0}])
    t = ml._Tick(pool=p, pmus=_Venue(), http=http, now=NOW, stats=ml._new_stats())
    assert _run(ml._confirm_gone(t, "rn1", M)) is False and http.calls == []
    # the exit worker's walk and polite_get raise it, as any request error would
    with pytest.raises(ratelimit.ThrottlePumpStopped):
        _run(we._fetch_positions(_FullPages(), "0xrn1"))
    with pytest.raises(ratelimit.ThrottlePumpStopped):
        _run(ratelimit.polite_get(_Http(), "/x"))


def test_fold_low1_a_live_foreign_loop_waiter_is_skipped_never_popped_and_a_closed_loops_is_dropped(clock):
    other = asyncio.new_event_loop()
    try:
        async def go():
            thr = ratelimit.Throttle(6.0)
            served: list = []
            foreign = other.create_future()             # a LIVE waiter of another loop, at the HEAD
            thr._normal.append(foreign)
            tasks = _queue(thr, clock, [("n1", False), ("n2", False)], served)
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
            # this loop's waiters served past it, at the rate; the head still there, live, uncharged
            assert _names(served) == ["n1", "n2"]
            assert list(thr._normal) == [foreign] and not foreign.done() and thr.waiting() == (0, 1)
            assert thr._pump.done() and thr._pump.exception() is None, \
                "this loop's pump ended: none of ITS waiters is queued -- no spin on a foreign one"
            other.close()                               # its loop gone: dropped by the next pick
            more = _queue(thr, clock, [("n3", False)], served)
            await asyncio.wait_for(asyncio.gather(*more), timeout=5)
            assert _names(served) == ["n1", "n2", "n3"] and not thr._normal and not foreign.done()
            return served, thr
        served, thr = _run(go())
        _rate_holds(served, thr.interval, 6.0)
    finally:
        if not other.is_closed():
            other.close()
    src = inspect.getsource(ratelimit.Throttle._pick)
    assert "get_loop().is_closed()" in src and "is not loop" not in src, "dropped only when its loop is closed"
    assert "f.get_loop() is loop" in inspect.getsource(ratelimit.Throttle._head)


def test_fold_medium2_the_mirrors_confirm_waits_on_the_priority_lane_at_most_confirm_gone_wait_s_and_a_timeout_is_not_gone(monkeypatch):
    assert ml.CONFIRM_GONE_WAIT_S == 5.0 == ml._SNAP_READ_TIMEOUT_S
    assert 'capped_env("MIRROR_CONFIRM_GONE_WAIT_S", 5.0, floor=1.0)' in inspect.getsource(ml)
    from sportsassets.analytics import mirror_live_rules as rules
    for env, want in (("2.5", 2.5), ("9", 5.0), ("0.2", 1.0), ("nan", 5.0)):
        monkeypatch.setenv("MIRROR_CONFIRM_GONE_WAIT_S", env)
        assert rules.capped_env("MIRROR_CONFIRM_GONE_WAIT_S", 5.0, floor=1.0) == want, env
    gone = [{"conditionId": CID, "asset": M, "size": 0}, {"conditionId": CID, "asset": N, "size": 0}]
    p = _pool()
    # the slot within the bound: the verdict as before, on the PRIORITY lane, one read
    lanes = _Lanes()
    monkeypatch.setattr(ratelimit, "_throttle", lanes)
    http = _Http(rows=gone)
    t = ml._Tick(pool=p, pmus=_Venue(), http=http, now=NOW, stats=ml._new_stats())
    assert _run(ml._confirm_gone(t, "rn1", M)) is True and lanes.calls == ["priority"] and len(http.calls) == 1

    class _Never:
        """A slot that never comes."""

        async def wait(self):
            await asyncio.sleep(30)

        async def acquire(self, priority=False):
            await asyncio.sleep(30)
    monkeypatch.setattr(ratelimit, "_throttle", _Never())
    monkeypatch.setattr(ml, "CONFIRM_GONE_WAIT_S", 0.05)
    http = _Http(rows=gone)
    t = ml._Tick(pool=p, pmus=_Venue(), http=http, now=NOW, stats=ml._new_stats())
    t0 = time.monotonic()
    assert _run(ml._confirm_gone(t, "rn1", M)) is False, "a timeout in the wait is NOT gone (HOLD)"
    assert http.calls == [] and time.monotonic() - t0 < 2.0, "no slot, no read; the bound held"
    # by source: the wait_for around the priority acquire, inside the fail-closed try, before the callee
    src = inspect.getsource(ml._confirm_gone)
    i = src.index("if not address or t.http is None")
    j = src.index("asyncio.wait_for(ratelimit.data_api_throttle().acquire(priority=True)")
    assert i < src.index("try:", i) < j < src.index("timeout=CONFIRM_GONE_WAIT_S", j) \
        < src.index("whale_exits._confirm_gone(", j) < src.index("except Exception:", j)
    # whale_exits' own site is untouched: the normal lane, no bound (it is the exit worker's walk)
    cyc = inspect.getsource(we._cycle)
    assert cyc.count("await ratelimit.data_api_throttle().wait()") == 1 and "wait_for" not in cyc


def _priority_true_sites():
    """(file, innermost function) of every call passing the literal
    `priority=True`, by AST over the package."""
    sites = set()

    def walk(node, fn, rel):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if isinstance(child, ast.Call) and any(
                    k.arg == "priority" and isinstance(k.value, ast.Constant) and k.value.value is True
                    for k in child.keywords):
                sites.add((rel, fn))
            walk(child, name, rel)
    for path in sorted(PKG.rglob("*.py")):
        with warnings.catch_warnings():     # a file's own escape-sequence warning is not this pin's
            warnings.simplefilter("ignore")
            tree = ast.parse(path.read_text())
        walk(tree, None, path.relative_to(PKG).as_posix())
    return sites


def test_fold_medium2_priority_true_at_exactly_the_mirrors_two_sites_and_market_positions_own_branch():
    assert _priority_true_sites() == {("workers/mirror_live.py", "_market_snap"),
                                      ("workers/mirror_live.py", "_confirm_gone"),
                                      ("workers/whale_exits.py", "market_positions")}


def test_fold_low2_the_docstring_and_the_docs_name_every_reader_of_api_positions():
    from sportsassets.config import Settings
    cfg = inspect.getsource(Settings)
    end = cfg.index("positions_sync_interval_seconds: int = 900")
    para = cfg[cfg.rindex("# E10 (2026-09-08): 300 -> 900", 0, end):end]
    for k in ("whale_profile", "events_view", "/api/signal", "api_signal", "shadow/runner.py",
              "workers/", "analytics/", "ingestion/", "15 minutes old"):
        assert k in para, k
    doc = (PKG.parents[1] / "docs" / "mirror-coverage.md").read_text()
    sec = doc[doc.index("## 31. E10"):]
    for k in ("`api/queries.whale_profile`", "`api/queries.events_view`", "`api/app.api_signal`",
              "`/api/signal`", "shadow/runner.py", "CONFIRM_GONE_WAIT_S", "ThrottlePumpStopped",
              "six slots", "1.0 s at 6 rps"):
        assert k in sec, k
    assert "at most one slot behind a priority sibling" not in sec, "the notes' arithmetic, corrected"
