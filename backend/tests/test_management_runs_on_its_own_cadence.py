"""HELD-POSITION MANAGEMENT AND FILL RECOVERY RUN ON THEIR OWN CADENCE,
UNDER ONE EXECUTION AUTHORITY.

The owner's question: do Xavier's held-position reviews and fill recovery
wait behind the ~17-minute collection cycle? At b51378d they did.
`_funded_service` (reconciliation, `manage`'s recovery, the pair pass whose
first steps are reservation and claim recovery, and Xavier's review of every
held position) ran once per `cycle()`, and `run` slept CYCLE_S only AFTER the
whole cycle -- entry lane, outcome join, calibration measurement, pair
observation pass -- had finished. So the next review came CYCLE_S + elapsed_s
later: the ~17-18 minutes between production heartbeats.

What these tests pin, all without a network or a database and on controlled
time:

  A  the servicing task runs pass after pass, SERVICING_INTERVAL_S apart,
     while the REAL `cycle()` is stuck inside a slow observation pass -- and
     the cycle services nothing itself while the task is alive;
  B  the stated bound holds when a pass overruns;
  C  one execution authority: two servicing passes never overlap; a task pass
     and the cycle's own servicing cannot both dispatch for one position; the
     funded entry attempt takes the same lock and refuses by name when it
     cannot get it in time;
  D  a failure in either the servicing or the collection side stops neither,
     through `run()` itself; and a standby services nothing.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from sportsassets.workers import ext_pinnacle_loop as L


# ════════════════════════════════════════════════════════════════════
# FIXTURES: a controlled clock, a fake pool, a recording servicer
# ════════════════════════════════════════════════════════════════════

T0 = 1_790_000_000.0


class FakeClock:
    """Wall and monotonic time, moved only by the test."""

    def __init__(self, t: float = T0):
        self.t = float(t)

    def time(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.t


class FakeConn:
    """Records every statement; answers the writer lock as told."""

    def __init__(self, name: str, lock_answers=None):
        self.name = name
        self.executed: list = []
        self._lock_answers = list(lock_answers or [])

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "OK"

    async def fetchval(self, sql, *args):
        if "pg_try_advisory_lock" in sql:
            return self._lock_answers.pop(0) if self._lock_answers else True
        return None

    def rows_for(self, key):
        return [json.loads(a[1]) for s, a in self.executed
                if a and a[0] == key]


class _Acquired:
    def __init__(self, pool, timeout):
        self.pool = pool
        self.timeout = timeout

    async def __aenter__(self):
        self.pool.acquires.append(self.timeout)
        fail = self.pool.fail_next_acquires
        if fail:
            self.pool.fail_next_acquires -= 1
            raise ConnectionError("pool exhausted (test)")
        c = FakeConn("pooled-%d" % len(self.pool.acquires),
                     lock_answers=self.pool.lock_answers)
        self.pool.conns.append(c)
        return c

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, lock_answers=None):
        self.acquires: list = []
        self.conns: list = []
        self.fail_next_acquires = 0
        self.lock_answers = list(lock_answers or [])

    def acquire(self, timeout=None):
        return _Acquired(self, timeout)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    """Every test starts with no servicing history and a fresh lock."""
    monkeypatch.setattr(L, "_SERVICING", L._servicing_state())
    monkeypatch.setattr(L, "_EXEC_LOCK", {"lock": None, "loop": None})
    yield


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(L, "time", types.SimpleNamespace(
        time=c.time, monotonic=c.monotonic))
    return c


def _servicer(clock, calls, *, work_s=2.0, raises_on=(), dispatch=None,
              gate=None):
    """A stand-in for `_funded_service` with the real signature. It records
    every call, spends `work_s` of controlled time, and -- when `dispatch` is
    a list -- records one dispatch for position 'pos-1' per pass, the way a
    pair pass that chose an action would."""
    async def _funded_service(conn, *, now, review_interval_s=L.CYCLE_S,
                              run_learning=True):
        n = len(calls) + 1
        calls.append({"conn": getattr(conn, "name", conn), "now": now,
                      "review_interval_s": review_interval_s,
                      "run_learning": run_learning})
        if gate is not None:
            await gate()
        w = work_s(n) if callable(work_s) else work_s
        clock.t += w
        if n in raises_on:
            raise RuntimeError("servicing blew up on pass %d (test)" % n)
        if dispatch is not None:
            dispatch.append(("pos-1", getattr(conn, "name", conn), now))
        got = {"ok": True, "selection": [], "pass": n,
               "pair_cycle": {"at": now, "xavier": [{
                   "intent_id": "pos-1", "chosen_action": "HOLD",
                   "next_review_at": now + review_interval_s}]}}
        if run_learning:
            got["learning"] = {"ok": True, "at": now}
        return got
    return _funded_service


def _reviewer(calls):
    async def _xavier_daily_review(conn, *, now):
        calls.append(now)
        return {"ok": True, "ran": True, "review_date": "2026-09-30"}
    return _xavier_daily_review


def _controlled_sleep(clock, slept, gates=None):
    """Advance controlled time by the requested pause, then yield. `gates`
    maps a sleep number to an awaitable factory the sleep waits on first."""
    gates = gates or {}

    async def _sleep(d):
        slept.append(d)
        g = gates.get(len(slept))
        if g is not None:
            await g()
        clock.t += d
        await asyncio.sleep(0)
    return _sleep


# ════════════════════════════════════════════════════════════════════
# A · ITS OWN CADENCE WHILE THE REAL CYCLE IS IN A SLOW OBSERVATION PASS
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_management_and_recovery_run_on_their_own_cadence_while_the_observation_pass_is_slow(
        clock, monkeypatch):
    calls, reviews, slept = [], [], []
    observing, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(L, "_funded_service", _servicer(clock, calls))
    monkeypatch.setattr(L, "_xavier_daily_review", _reviewer(reviews))

    # THE REAL cycle(), BLOCKED on the entry side (no odds credential), which
    # is the path that still runs the pair observation pass -- made slow: it
    # does not return until the test releases it.
    async def _running(conn):
        return True, None

    async def _table_ready(conn):
        return True

    async def _slow_observation(conn, observable, *, now):
        observing.set()
        await release.wait()
        return {"ok": True, "observations_written": 0}

    monkeypatch.setattr(L, "_running", _running)
    monkeypatch.setattr(L, "_table_ready", _table_ready)
    monkeypatch.setattr(L.ext, "credential_present",
                        lambda: {"present": False,
                                 "refusal": "ODDS_CREDENTIAL_ABSENT"})
    monkeypatch.setattr(L, "_pair_observation_pass", _slow_observation)
    monkeypatch.setattr(L, "_LAST_OBSERVATION_PASS", [0.0])

    pool = FakePool()
    passes = 6
    # The first pause waits until the cycle is inside the observation pass,
    # so every later pass provably happens while that pass is in progress.
    servicing = asyncio.create_task(L._servicing_loop(
        pool, interval_s=L.SERVICING_INTERVAL_S,
        sleep=_controlled_sleep(clock, slept, {1: observing.wait}),
        clock=clock.monotonic, max_passes=passes))
    await asyncio.sleep(0)
    assert L._SERVICING["task_active"] is True

    cycle_conn = FakeConn("cycle")
    collecting = asyncio.create_task(L.cycle(cycle_conn))
    await asyncio.wait_for(servicing, timeout=5)

    # ALL SIX PASSES RAN WHILE THE OBSERVATION PASS WAS STILL IN PROGRESS.
    assert observing.is_set() and not collecting.done()
    assert len(calls) == passes
    # Every one on a servicing connection, none on the cycle's.
    assert all(c["conn"].startswith("pooled-") for c in calls), calls
    # START TO START, EXACTLY THE INTERVAL: 2 s of work + 58 s of pause.
    starts = [c["now"] for c in calls]
    assert [b - a for a, b in zip(starts, starts[1:])] == \
        [L.SERVICING_INTERVAL_S] * (passes - 1)
    assert slept == [L.SERVICING_INTERVAL_S - 2.0] * passes
    # Xavier's next review is stated at the interval the task keeps.
    assert all(c["review_interval_s"] == L.SERVICING_INTERVAL_S
               for c in calls)
    # THE SLOW HALF KEPT THE COLLECTION CADENCE: learning and the daily
    # review ran on the first pass only (LEARNING_INTERVAL_S > 5 intervals).
    assert [c["run_learning"] for c in calls] == [True] + [False] * 5
    assert len(reviews) == 1
    # Each pass wrote the servicing heartbeat, with the measured gaps.
    beats = [r for c in pool.conns for r in c.rows_for(L.SERVICING_KEY)]
    assert len(beats) == passes
    last = beats[-1]
    assert last["state"] == "SERVICED"
    assert last["source"] == L.SOURCE_SERVICING_TASK
    assert last["servicing_cadence"]["recent_start_gaps_s"]["max"] == \
        L.SERVICING_INTERVAL_S
    assert last["funded_servicing"]["xavier"][0]["intent_id"] == "pos-1"
    # The pool acquire is bounded, never an indefinite wait.
    assert set(pool.acquires) == {L.SERVICING_ACQUIRE_TIMEOUT_S}

    # NOW LET THE OBSERVATION PASS FINISH.
    release.set()
    out = await asyncio.wait_for(collecting, timeout=5)
    assert out["state"] == "BLOCKED"
    # THE CYCLE SERVICED NOTHING ITSELF: it reported the task's pass.
    assert len(calls) == passes
    assert out["funded_servicing"]["pass"] == 1, (
        "the cycle reports the task's latest pass at its servicing step")
    hb = cycle_conn.rows_for(L.HEARTBEAT_KEY)[-1]
    assert hb["servicing_cadence"]["last_pass_source"] == \
        L.SOURCE_SERVICING_TASK
    assert hb["funded_servicing"]["xavier"][0]["intent_id"] == "pos-1"
    assert "servicing_in_cycle" in hb["step_timing_s"]


def test_the_servicing_interval_is_far_inside_the_collection_cycle():
    """The point of the change, as numbers. The old period was CYCLE_S plus
    the cycle's own duration; the new one is SERVICING_INTERVAL_S."""
    assert L.SERVICING_INTERVAL_S <= 60.0
    assert L.SERVICING_INTERVAL_S * 10 <= L.CYCLE_S
    assert L.SERVICING_MIN_GAP_S < L.SERVICING_INTERVAL_S
    assert L.LEARNING_INTERVAL_S == L.CYCLE_S
    assert L.ENTRY_WAITS_FOR_EXECUTION_S == L.PINNACLE_MAX_AGE_S


# ════════════════════════════════════════════════════════════════════
# B · THE BOUND WHEN A PASS OVERRUNS
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_an_overrunning_pass_delays_the_next_by_at_most_the_min_gap(
        clock, monkeypatch):
    calls, slept = [], []
    durations = {1: 2.0, 2: 75.0, 3: 2.0, 4: 2.0}
    monkeypatch.setattr(L, "_funded_service",
                        _servicer(clock, calls, work_s=durations.get))
    monkeypatch.setattr(L, "_xavier_daily_review", _reviewer([]))
    await L._servicing_loop(FakePool(), interval_s=60.0,
                            sleep=_controlled_sleep(clock, slept),
                            clock=clock.monotonic, max_passes=4)
    starts = [c["now"] for c in calls]
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert slept == [58.0, L.SERVICING_MIN_GAP_S, 58.0, 58.0]
    assert gaps == [60.0, 75.0 + L.SERVICING_MIN_GAP_S, 60.0]
    # THE STATED BOUND: max(interval, previous duration + min gap).
    for i, g in enumerate(gaps, start=1):
        assert g <= max(60.0, durations[i] + L.SERVICING_MIN_GAP_S)


# ════════════════════════════════════════════════════════════════════
# C · ONE EXECUTION AUTHORITY
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_two_servicing_passes_never_overlap(clock, monkeypatch):
    calls, dispatch = [], []
    inside, release = asyncio.Event(), asyncio.Event()
    concurrent = {"now": 0, "max": 0}

    async def _gate():
        concurrent["now"] += 1
        concurrent["max"] = max(concurrent["max"], concurrent["now"])
        inside.set()
        await release.wait()
        concurrent["now"] -= 1

    monkeypatch.setattr(L, "_funded_service", _servicer(
        clock, calls, dispatch=dispatch, gate=_gate))
    monkeypatch.setattr(L, "_xavier_daily_review", _reviewer([]))
    first = asyncio.create_task(L._service_once(
        FakeConn("a"), now=clock.t, source=L.SOURCE_SERVICING_TASK))
    await inside.wait()
    # A SECOND PASS WHILE THE FIRST HOLDS THE LOCK: refused, not queued
    # (a pass that queued would wait here for ever, hence the timeout).
    second = await asyncio.wait_for(L._service_once(
        FakeConn("b"), now=clock.t, source=L.SOURCE_SERVICING_TASK),
        timeout=2)
    assert second["ran"] is False
    assert second["refusal"] == L.R_EXECUTION_AUTHORITY_BUSY
    release.set()
    got = await first
    assert got["ran"] is True
    assert concurrent["max"] == 1
    assert len(calls) == 1 and dispatch == [("pos-1", "a", T0)]
    assert L._SERVICING["skipped_busy"] == 1


@pytest.mark.asyncio
async def test_the_task_pass_and_the_cycle_servicing_cannot_both_dispatch_for_one_position(
        clock, monkeypatch):
    """The fallback path -- `cycle()` servicing on its own -- meets a task
    pass that is mid-dispatch for pos-1. The cycle's servicing is refused by
    the execution lock and sends nothing; pos-1 is dispatched once."""
    calls, dispatch = [], []
    inside, release = asyncio.Event(), asyncio.Event()

    async def _gate():
        if len(calls) == 1:          # only the task's pass blocks
            inside.set()
            await release.wait()

    monkeypatch.setattr(L, "_funded_service", _servicer(
        clock, calls, dispatch=dispatch, gate=_gate))
    monkeypatch.setattr(L, "_xavier_daily_review", _reviewer([]))

    async def _stopped(conn):
        return False, "STOPPED_BY_TEST"
    monkeypatch.setattr(L, "_running", _stopped)

    task_pass = asyncio.create_task(L._service_once(
        FakeConn("task"), now=clock.t, source=L.SOURCE_SERVICING_TASK))
    await inside.wait()
    # The cycle, with no servicing task registered, services itself -- and
    # finds the authority held.
    assert L._SERVICING["task_active"] is False
    cyc = FakeConn("cycle")
    out = await asyncio.wait_for(L.cycle(cyc), timeout=2)
    assert out["state"] == "STOPPED"
    assert out["funded_servicing"]["refusal"] == L.R_EXECUTION_AUTHORITY_BUSY
    release.set()
    await task_pass
    assert [d[0] for d in dispatch] == ["pos-1"]
    assert [c["conn"] for c in calls] == ["task"]

    # AND WITH THE TASK ALIVE the cycle does not even ask: it reports.
    L._SERVICING["task_active"] = True
    out = await L.cycle(cyc)
    assert [c["conn"] for c in calls] == ["task"]
    assert out["funded_servicing"]["pass"] == 1


@pytest.mark.asyncio
async def test_without_the_task_the_cycle_services_exactly_as_before(
        clock, monkeypatch):
    """`cycle()` on its own: `_funded_service` with its defaults (CYCLE_S,
    learning on) and the daily review, once per cycle, as at b51378d."""
    calls, reviews = [], []
    monkeypatch.setattr(L, "_funded_service", _servicer(clock, calls))
    monkeypatch.setattr(L, "_xavier_daily_review", _reviewer(reviews))

    async def _stopped(conn):
        return False, "STOPPED_BY_TEST"
    monkeypatch.setattr(L, "_running", _stopped)
    for _ in range(2):
        out = await L.cycle(FakeConn("cycle"))
        assert out["funded_servicing"]["ok"] is True
    assert [(c["conn"], c["review_interval_s"], c["run_learning"])
            for c in calls] == [("cycle", L.CYCLE_S, True)] * 2
    assert len(reviews) == 2


@pytest.mark.asyncio
async def test_the_funded_entry_attempt_takes_the_same_lock(monkeypatch):
    """The only other submit path in this module. Held lock -> a bounded
    wait, then a named refusal with nothing read or sent. Free lock -> it
    sends under the lock, so a servicing pass cannot run meanwhile."""
    from sportsassets import bettor_funded_activation as FA
    from sportsassets import bettor_funded_execution as FX

    async def _bound(conn, key):
        return {"account_id": "acct-test", "venue": "PMUS"}
    monkeypatch.setattr(FA, "_state", _bound)
    reads, sends, seen_busy = [], [], []

    async def _read():
        reads.append(1)
        return {"ok": True}

    async def _submit(conn, rec, **kw):
        # WHILE SENDING, A SERVICING PASS IS REFUSED.
        got = await asyncio.wait_for(L._service_once(
            FakeConn("svc"), now=T0, source=L.SOURCE_SERVICING_TASK),
            timeout=2)
        seen_busy.append(got.get("refusal"))
        sends.append(rec)
        return {"ok": False, "refusal": "FUNDED_SUBMISSION_DISABLED"}

    monkeypatch.setattr(L, "venue_account_exposure", _read)
    monkeypatch.setattr(FX, "submit_for_decision", _submit)
    monkeypatch.setattr(L, "ENTRY_WAITS_FOR_EXECUTION_S", 0.05)
    # (1002) DEREK'S ENTRY GATE says ENTER here: this test is about the
    # execution lock. The gate's own refusals (verdict, raise, missing
    # module -> nothing read or sent) are proven in
    # test_agent_entry_shutdown_preserves_exits_and_recovery.py.
    from tests import _emptybook_fixture as _EBF
    _EBF.derek_gate_enters(monkeypatch)

    lock = L._execution_lock()
    await lock.acquire()
    try:
        got = await L._funded_attempt(FakeConn("cycle"), {"edge": 0.1},
                                      now=T0)
    finally:
        lock.release()
    assert got["refusal"] == L.R_EXECUTION_AUTHORITY_BUSY
    assert reads == [] and sends == []

    got = await L._funded_attempt(FakeConn("cycle"), {"edge": 0.1}, now=T0)
    assert got["refusal"] == "FUNDED_SUBMISSION_DISABLED"
    assert len(sends) == 1 and reads == [1]
    assert seen_busy == [L.R_EXECUTION_AUTHORITY_BUSY]
    assert not lock.locked(), "the entry attempt released the lock"


def test_only_run_starts_the_task_and_only_after_the_writer_lock():
    """STRUCTURAL: the task is created in `run`, after the lock loop and the
    cooldown resume, and cancelled with it; nothing else creates it."""
    import inspect

    src = inspect.getsource(L.run)
    i_lock = src.index("pg_try_advisory_lock")
    i_resume = src.index("load_and_resume")
    i_task = src.index("_servicing_loop(pool")
    assert i_lock < i_resume < i_task < src.index("cycle(conn)")
    assert "servicing.cancel()" in src
    whole = inspect.getsource(L)
    assert whole.count("create_task(\n            _servicing_loop(") == 1
    assert whole.count("_servicing_loop(pool, interval_s=") == 1


# ════════════════════════════════════════════════════════════════════
# D · A FAILURE IN ONE STOPS NEITHER
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_failing_pass_or_connection_does_not_stop_the_servicing_task(
        clock, monkeypatch):
    calls, slept = [], []
    monkeypatch.setattr(L, "_funded_service",
                        _servicer(clock, calls, raises_on=(1,)))
    monkeypatch.setattr(L, "_xavier_daily_review", _reviewer([]))
    pool = FakePool()

    async def _sleep(d):
        slept.append(d)
        clock.t += d
        if len(slept) == 1:
            pool.fail_next_acquires = 1     # pass 2 gets no connection
        await asyncio.sleep(0)

    await L._servicing_loop(pool, interval_s=60.0, sleep=_sleep,
                            clock=clock.monotonic, max_passes=4)
    # Pass 1 raised inside servicing (reported, not raised); pass 2 had no
    # connection (counted); passes 3 and 4 ran.
    assert len(calls) == 3
    assert L._SERVICING["errors"] == 1
    assert "ConnectionError" in L._SERVICING["last_error"]
    beats = [r for c in pool.conns for r in c.rows_for(L.SERVICING_KEY)]
    assert beats[0]["funded_servicing"]["refusal"] == \
        "FUNDED_SERVICING_RAISED"
    assert beats[-1]["funded_servicing"]["ok"] is True
    assert L._SERVICING["task_active"] is False


@pytest.mark.asyncio
async def test_through_run_a_failing_cycle_and_failing_servicing_stop_neither(
        monkeypatch):
    """`run()` itself, on a fake pool, with small real intervals. The writer
    lock is refused twice first (standby), and the servicing task must not
    start until it is held. Then every cycle raises AND every servicing pass
    raises; both keep going."""
    from sportsassets import venue_cooldown_store as VCS

    cycles, services = [], []

    async def _cycle(conn):
        cycles.append(1)
        raise RuntimeError("collection blew up (test)")

    async def _service(conn, *, now, review_interval_s=L.CYCLE_S,
                       run_learning=True):
        services.append(now)
        raise RuntimeError("servicing blew up (test)")

    async def _resume(conn):
        return {"resumed": False, "why": "TEST"}

    monkeypatch.setattr(L, "cycle", _cycle)
    monkeypatch.setattr(L, "_funded_service", _service)
    monkeypatch.setattr(L, "_xavier_daily_review", _reviewer([]))
    monkeypatch.setattr(VCS, "load_and_resume", _resume)
    monkeypatch.setattr(VCS, "pending", lambda: None)
    monkeypatch.setattr(L, "IDLE_POLL_S", 0.01)
    monkeypatch.setattr(L, "SERVICING_INTERVAL_S", 0.01)
    monkeypatch.setattr(L, "SERVICING_MIN_GAP_S", 0.0)
    pool = FakePool(lock_answers=[False, False, True])

    async def _get_pool():
        return pool

    runner = asyncio.create_task(L.run(_get_pool))

    async def _both_progressed():
        while len(cycles) < 3 or len(services) < 3:
            await asyncio.sleep(0.005)
    await asyncio.wait_for(_both_progressed(), timeout=10)
    assert L._SERVICING["task_active"] is True
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner
    # The task died with the loop, and the cycle falls back to servicing.
    assert L._SERVICING["task_active"] is False
    # The standby answered twice and serviced nothing: the first standby
    # beat is on STANDBY_KEY, and no servicing row precedes the lock.
    lock_conn = pool.conns[0]
    assert lock_conn.rows_for(L.STANDBY_KEY), "the standby said so"
    assert len(services) >= 3 and len(cycles) >= 3
    assert all(r["funded_servicing"]["refusal"] == "FUNDED_SERVICING_RAISED"
               for c in pool.conns[1:] for r in c.rows_for(L.SERVICING_KEY))
