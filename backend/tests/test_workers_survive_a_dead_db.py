"""The workers process survives a dead database without leaking.

THE SHAPE (investigation 2026-09-05, the workers dying during the
full-disk outage of 2026-09-04/05). Two leaks, one per module, each
paced by the outage itself:

  db.get_pool() was check-then-set with the whole connect between the
  check and the set, so every concurrent caller that found _pool None
  built its OWN asyncpg pool (min 1, max 10) and the last one to finish
  overwrote the global; the rest were never closed. workers/all.py
  boots by gathering _record_boot plus eighteen supervised loops at
  once -- exactly that race, every boot, and again on every restart
  while the database refused connections.

  Poller.run() spawned its two side loops with a bare create_task and
  dropped the handles, and read the roster outside any try. An
  unreadable database raised out of run(); supervise() restarted it
  through workers/poller.py, which builds a NEW Poller, while the two
  orphaned loops of the old one lived on forever (they catch Exception
  and loop) holding the old Poller and its never-closed httpx client.

And the review of the first fix (same day): the single-flight lock was
held across the whole 10-attempt walk and a waiter that acquired it
after the leader FAILED walked its own ~105 s, so under a dead database
the queue drained one caller per ~105 s -- in the API process, one
36-128 KB request handler per ~105 s, for hours.

And the containment re-review (same day again): two awaits the guards
did not cover. db.heartbeat bounded its STATEMENT through
pool.execute(timeout=) -- but asyncpg's Pool.execute is `async with
self.acquire(): con.execute(timeout=)` with no acquire timeout, so
against a SATURATED pool (size == max, idle == 0, what /healthz read at
2026-09-05 01:06Z) the beat blocked in acquire forever: no
TimeoutError, no 'not written' line, run() stopped pacing. And
_alert_degraded guarded the alert channel against a raise, not a hang.

Every pin here is against the REAL functions -- db.get_pool,
db.heartbeat, db.pool_stats, Poller.run with the real _history_loop,
_priority_loop, tracked_whales, _beat and _alert_degraded underneath --
with asyncpg.create_pool, the pool getter, the pool, the backfill and
the sleeps faked around them:

  * twenty concurrent get_pool() callers with no pool -> create_pool
    called ONCE, one object for all, never two connects in flight; and
    the same in a SECOND event loop, because a module-level Lock binds
    to the first loop that contends on it and raises in the next
  * a create_pool that keeps failing leaves _pool None, walks the
    existing backoff (1, 2, 4, 8, then 15 x 6) and raises RuntimeError;
    the lock is released, so the next caller connects
  * twenty waiters behind a FAILING leader all raise within the
    leader's walk: the sleeps total ONE walk's, ten connects, never two
    in flight, _pool None -- and the next arrival after the failure
    walks anew; a leader cancelled mid-walk stamps no verdict, so its
    waiter walks and lands
  * db.heartbeat bounds BOTH legs of its write with HEARTBEAT_TIMEOUT_S
    (10 s) -- pool.acquire(timeout=) and the connection's
    execute(timeout=) -- so a saturated pool ends the write with a
    TimeoutError from the acquire, no statement ever sent, and a
    stalled database ends it with one from the statement; neither
    holds the caller, and a write that went back to pool.execute would
    be held by the saturated pool exactly as the real one was
  * pool_stats() is synchronous, {"size","idle","max","min"} from the
    pool's own counters, None without a pool, None (never a raise) when
    the counters cannot be read
  * Poller.run() with the roster unreadable (the getter raising, and a
    pool whose fetch raises the outage's ConnectionDoesNotExistError):
    no raise across N paced retries, an 'error' heartbeat counting
    failures when the beat can be written, a log line when the beat
    itself cannot, and the 'Path B degraded' alert ONCE at the
    threshold -- in the third failed cycle, not the fourth, pinned on
    a timeline of beats, pace and alert -- from a channel that raises
    (an httpx error, not a RuntimeError: the guard is `except
    Exception`, reached from the roster branch AND the wallet branch),
    and from one that HANGS: bounded by ALERT_TIMEOUT_S, one 'not
    delivered' line naming TimeoutError, without ending run(); the
    real ceiling sits above telegram._send's own httpx timeout so the
    channel's own error text wins the line when it has one
  * each of the other three beat sites (per-wallet 'ok', per-wallet
    'error', empty-roster 'idle') with the REAL db.heartbeat raising or
    hanging underneath -- the statement, and the acquire of a saturated
    pool: logged as not written, run() paced on
  * when run() exits, both side loops are cancelled and done, the
    client's aclose() was awaited (a fake that counts, and the real
    httpx client), and a second run() in the same loop finds no task
    from the first; a side loop that dies on its own is logged at ERROR
    the moment it dies and again when run() leaves; a cancelled run()
    is waited for without a second cancel, so a run() that swallowed
    its cancel is a failed assertion in 2 s, never a hung suite
  * a source pin: every create_task handle in run() is kept and gets a
    done-callback, run() has a finally that cancels and closes, and the
    roster read sits inside a try with an except
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import re
import textwrap
import time

import asyncpg
import httpx
import pytest

from sportsassets import db as db_mod
from sportsassets.ingestion import poller as poller_mod
from sportsassets.ingestion.poller import Poller

_REAL_SLEEP = asyncio.sleep
LOGGER = "sportsassets.ingestion.poller"
_OUTAGE = "connection was closed in the middle of operation"


# ------------------------------------------------------------ db.get_pool

class _Pool:
    """Stands in for what asyncpg.create_pool returns."""


def _fresh_db(monkeypatch):
    """No pool, no lock and no walk yet, as a process finds them at
    boot; restored by monkeypatch so no other test inherits any."""
    monkeypatch.setattr(db_mod, "_pool", None)
    monkeypatch.setattr(db_mod, "_connect_lock", None)
    monkeypatch.setattr(db_mod, "_connect_lock_loop", None)
    monkeypatch.setattr(db_mod, "_walk_gen", 0)


class _Connects:
    """A create_pool that takes time (two yields), counts its calls and
    the most it ever had in flight at once, and either lands or fails."""

    def __init__(self, fail: Exception | None = None):
        self.calls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.fail = fail

    async def __call__(self, dsn, **kw):
        self.calls.append(dsn)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await _REAL_SLEEP(0)
            await _REAL_SLEEP(0)
            if self.fail is not None:
                raise self.fail
            return _Pool()
        finally:
            self.in_flight -= 1


def _counting_sleep(monkeypatch) -> list[float]:
    """db's backoff sleep as a counter that yields instead of waiting."""
    slept: list[float] = []

    async def _sleep(s):
        slept.append(s)
        await _REAL_SLEEP(0)

    monkeypatch.setattr(db_mod.asyncio, "sleep", _sleep)
    return slept


_ONE_WALK = [1, 2, 4, 8, 15, 15, 15, 15, 15, 15]


def test_twenty_concurrent_callers_share_one_create_pool(monkeypatch):
    _fresh_db(monkeypatch)
    connects = _Connects()
    monkeypatch.setattr(asyncpg, "create_pool", connects)

    async def _scenario():
        return await asyncio.gather(*(db_mod.get_pool() for _ in range(20)))

    # twice, in two asyncio.run loops: a Lock made at module scope binds
    # to the first loop that contends on it and raises "bound to a
    # different event loop" in the second
    for loop_no in range(2):
        monkeypatch.setattr(db_mod, "_pool", None)
        got = asyncio.run(_scenario())
        assert len(connects.calls) == loop_no + 1, connects.calls
        assert connects.max_in_flight == 1
        assert isinstance(got[0], _Pool)
        assert all(g is got[0] for g in got)
        assert db_mod._pool is got[0]
        assert connects.calls[-1] == db_mod._dsn()
    # and a caller that finds a pool never touches create_pool
    assert asyncio.run(db_mod.get_pool()) is db_mod._pool
    assert len(connects.calls) == 2


def test_a_failing_connect_leaves_no_pool_walks_the_backoff_and_raises(monkeypatch):
    _fresh_db(monkeypatch)
    connects = _Connects(fail=OSError("[Errno -2] Name or service not known"))
    monkeypatch.setattr(asyncpg, "create_pool", connects)
    slept: list[float] = []

    async def _sleep(s):
        slept.append(s)

    monkeypatch.setattr(db_mod.asyncio, "sleep", _sleep)
    with pytest.raises(RuntimeError, match="could not connect to Postgres"):
        asyncio.run(db_mod.get_pool())
    assert db_mod._pool is None
    assert len(connects.calls) == 10
    assert slept == _ONE_WALK
    # the failure released the lock: the next caller connects
    landing = _Connects()
    monkeypatch.setattr(asyncpg, "create_pool", landing)
    pool = asyncio.run(db_mod.get_pool())
    assert isinstance(pool, _Pool) and db_mod._pool is pool
    assert len(landing.calls) == 1


def test_twenty_waiters_behind_a_failing_leader_share_its_verdict(monkeypatch):
    """The review's blocking finding, pinned by the measure it named:
    with the backoff sleep a counter, twenty-one concurrent callers
    against a refusing database cost ONE walk's sleeps and ten
    connects, never two in flight; all twenty-one raise the leader's
    RuntimeError; _pool stays None. The first cut walked one ~105 s
    backoff PER WAITER, serially, and the API's request handlers
    queued behind the lock for hours. Then the next arrival, after the
    verdict, is the next leader and walks its own."""
    _fresh_db(monkeypatch)
    connects = _Connects(fail=OSError("connection refused"))
    monkeypatch.setattr(asyncpg, "create_pool", connects)
    slept = _counting_sleep(monkeypatch)
    raised_after: list[int] = []      # connects seen when each caller raised

    async def _one():
        try:
            return await db_mod.get_pool()
        except RuntimeError as exc:
            raised_after.append(len(connects.calls))
            return exc

    async def _scenario():
        got = await asyncio.gather(*(_one() for _ in range(21)))
        # the next arrival reads the new generation and walks anew --
        # same loop, same lock, as the API process would
        with pytest.raises(RuntimeError, match="could not connect to Postgres"):
            await db_mod.get_pool()
        return got

    got = asyncio.run(_scenario())
    assert len(got) == 21
    assert all(isinstance(g, RuntimeError) for g in got), got
    assert all("could not connect to Postgres" in str(g) for g in got)
    assert db_mod._pool is None
    assert connects.max_in_flight == 1
    # the twenty-one shared ONE walk ...
    assert len(raised_after) == 21
    assert all(n == 10 for n in raised_after), raised_after
    # ... and the arrival after it walked its own: two walks in total
    assert len(connects.calls) == 20
    assert slept == _ONE_WALK + _ONE_WALK
    assert db_mod._walk_gen == 2


def test_a_leader_cancelled_mid_walk_stamps_no_verdict_for_its_waiters(monkeypatch):
    """A cancelled leader learned nothing (the 2026-09-03 /healthz
    ceiling cancelled get_pool mid-connect on every check): its waiter
    must walk, not inherit a failure nobody observed -- so a database
    that is back by then is found, not reported gone."""
    _fresh_db(monkeypatch)
    connects = _Connects(fail=OSError("connection refused"))
    monkeypatch.setattr(asyncpg, "create_pool", connects)
    _counting_sleep(monkeypatch)

    async def _scenario():
        leader = asyncio.create_task(db_mod.get_pool())
        await _REAL_SLEEP(0)                  # leader is inside create_pool
        waiter = asyncio.create_task(db_mod.get_pool())
        await _REAL_SLEEP(0)                  # waiter is queued on the lock
        assert connects.in_flight == 1
        leader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leader
        connects.fail = None                  # the database is back
        return await waiter

    pool = asyncio.run(_scenario())
    assert isinstance(pool, _Pool) and db_mod._pool is pool
    assert connects.max_in_flight == 1
    assert len(connects.calls) == 2           # the cancelled one, then the waiter's
    assert db_mod._walk_gen == 0              # no verdict was ever stamped


# ---------------------------------------------------------- db.heartbeat

class _DbPool:
    """A pool as tracked_whales and db.heartbeat see it, in asyncpg's
    shape. fetch serves the roster (or raises). acquire(timeout=) is
    the async context manager that hands out a connection, and the
    connection's execute is the heartbeat upsert; each records its
    timeout and either lands, raises, or hangs: a SATURATED pool
    (acquire_hangs -- size == max, idle == 0, what /healthz read at
    2026-09-05 01:06Z) never hands out a connection, a STALLED database
    (execute_hangs) never answers the statement. A hang is forever when
    no timeout was given and timeout/1000 s when one was, then
    TimeoutError the way asyncpg raises it at each ceiling. execute on
    the pool itself is asyncpg's Pool.execute, shape for shape --
    acquire with NO timeout, then the connection's execute with one --
    so a heartbeat that goes back to it is held by the saturated pool
    exactly as the real one was. Everything yields once, so a loop that
    beats without pacing still lets the event loop turn."""

    def __init__(self, roster=None, fetch_raises=None, execute_raises=None,
                 execute_hangs=False, acquire_hangs=False):
        self.roster = roster or []
        self.fetch_raises = fetch_raises
        self.execute_raises = execute_raises
        self.execute_hangs = execute_hangs
        self.acquire_hangs = acquire_hangs
        self.fetches: list[str] = []
        self.acquires: list[float | None] = []   # the timeout each acquire got
        self.executes: list[tuple] = []          # (args, timeout)
        self.released = 0

    async def fetch(self, sql, *args, **kw):
        self.fetches.append(sql)
        await _REAL_SLEEP(0)
        if self.fetch_raises is not None:
            raise self.fetch_raises
        return [dict(r) for r in self.roster]

    @staticmethod
    async def _hang(timeout):
        if timeout is None:
            await asyncio.Event().wait()  # never
        await asyncio.wait_for(asyncio.Event().wait(), timeout / 1000)

    def acquire(self, *, timeout=None):
        return _Acquire(self, timeout)

    async def execute(self, sql, *args, timeout=None):
        # asyncpg's Pool.execute: no acquire timeout, only the statement's
        async with self.acquire() as con:
            return await con.execute(sql, *args, timeout=timeout)


class _Acquire:
    """asyncpg's PoolAcquireContext: the acquire happens in __aenter__
    (and is where a saturated pool holds the caller), the release in
    __aexit__."""

    def __init__(self, pool: _DbPool, timeout):
        self.pool = pool
        self.timeout = timeout

    async def __aenter__(self):
        self.pool.acquires.append(self.timeout)
        await _REAL_SLEEP(0)
        if self.pool.acquire_hangs:
            await self.pool._hang(self.timeout)
        return _Conn(self.pool)

    async def __aexit__(self, *exc):
        self.pool.released += 1


class _Conn:
    """The connection an acquire hands out: execute is the heartbeat
    upsert, and is where a stalled database holds the caller."""

    def __init__(self, pool: _DbPool):
        self.pool = pool

    async def execute(self, sql, *args, timeout=None):
        self.pool.executes.append((args, timeout))
        await _REAL_SLEEP(0)
        if self.pool.execute_hangs:
            await self.pool._hang(timeout)
        if self.pool.execute_raises is not None:
            raise self.pool.execute_raises


def _live_db(monkeypatch, pool: _DbPool) -> _DbPool:
    """get_pool answers `pool` -- for the poller's roster read AND for
    db.heartbeat, which the poller's beat goes through."""

    async def _get_pool():
        await _REAL_SLEEP(0)
        return pool

    monkeypatch.setattr(poller_mod, "get_pool", _get_pool)
    monkeypatch.setattr(db_mod, "get_pool", _get_pool)
    return pool


def test_heartbeat_bounds_the_statement_so_a_stalled_database_cannot_hold_it(monkeypatch):
    """The REAL db.heartbeat against a connection whose execute hangs
    unless it was given a timeout: it ends in a TimeoutError, well
    inside the 2 s guard, with HEARTBEAT_TIMEOUT_S passed through --
    on the statement, and on the acquire it went through to get there
    (the name's claim was only half true until the acquire was bounded
    too; see the saturated-pool pin next)."""
    pool = _live_db(monkeypatch, _DbPool(execute_hangs=True))
    assert db_mod.HEARTBEAT_TIMEOUT_S == 10.0
    t0 = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(db_mod.heartbeat("poller", "ok", {"x": 1}), 2.0))
    assert time.monotonic() - t0 < 1.0, "the guard fired, not the write's own ceiling"
    assert pool.acquires == [db_mod.HEARTBEAT_TIMEOUT_S]
    assert len(pool.executes) == 1
    args, timeout = pool.executes[0]
    assert timeout == db_mod.HEARTBEAT_TIMEOUT_S
    assert args[:2] == ("poller", "ok")
    assert '"x": 1' in args[2]
    assert pool.released == 1                 # the connection went back
    # and TimeoutError is an Exception: the poller's beat can contain it
    assert issubclass(asyncio.TimeoutError, Exception)


def test_heartbeat_bounds_the_acquire_so_a_saturated_pool_cannot_hold_it(monkeypatch):
    """The containment re-review's finding (2026-09-05): the first cut
    passed HEARTBEAT_TIMEOUT_S to pool.execute, which bounds only the
    STATEMENT -- asyncpg's Pool.execute is `async with self.acquire():
    con.execute(timeout=)` with no acquire timeout -- so against a
    saturated pool (size == max, idle == 0, what /healthz read at
    01:06Z that night) the beat blocked in acquire forever: no
    TimeoutError, no 'not written' line, run() stopped pacing. The
    REAL db.heartbeat against a pool whose acquire hangs unless given
    a timeout: it ends in a TimeoutError well inside the 2 s guard,
    with HEARTBEAT_TIMEOUT_S on the acquire and no statement ever
    sent. Then the same pool's own execute -- asyncpg's shape -- is
    shown to be exactly the hold: a write that went back to it is
    still in acquire, with no ceiling, when the guard fires."""
    pool = _live_db(monkeypatch, _DbPool(acquire_hangs=True))
    t0 = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(db_mod.heartbeat("poller", "ok", {"x": 1}), 2.0))
    assert time.monotonic() - t0 < 1.0, "the guard fired, not the acquire's own ceiling"
    assert pool.acquires == [db_mod.HEARTBEAT_TIMEOUT_S]
    assert pool.executes == []
    assert pool.released == 0
    # the shape the code relies on is asyncpg's own: acquire(*, timeout=)
    # returning an async context manager
    sig = inspect.signature(asyncpg.Pool.acquire)
    assert sig.parameters["timeout"].kind is inspect.Parameter.KEYWORD_ONLY
    assert hasattr(asyncpg.pool.PoolAcquireContext, "__aenter__")
    # and the fake's pool.execute is the hold the fix left behind: the
    # guard, not any ceiling, is what ends it
    t0 = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(
            pool.execute("x", timeout=db_mod.HEARTBEAT_TIMEOUT_S), 0.2))
    assert time.monotonic() - t0 >= 0.2
    assert pool.acquires == [db_mod.HEARTBEAT_TIMEOUT_S, None]
    assert pool.executes == []


# ---------------------------------------------------------- db.pool_stats

class _Counters:
    def __init__(self, size=3, idle=2, mx=10, mn=1):
        self._v = (size, idle, mx, mn)

    def get_size(self):
        return self._v[0]

    def get_idle_size(self):
        return self._v[1]

    def get_max_size(self):
        return self._v[2]

    def get_min_size(self):
        return self._v[3]


def test_pool_stats_reads_the_live_pools_counters_without_awaiting(monkeypatch):
    assert not inspect.iscoroutinefunction(db_mod.pool_stats)
    monkeypatch.setattr(db_mod, "_pool", _Counters(size=7, idle=0, mx=10, mn=1))
    assert db_mod.pool_stats() == {"size": 7, "idle": 0, "max": 10, "min": 1}
    # the fake's shape is the real pool's shape, and the reads are synchronous
    for m in ("get_size", "get_idle_size", "get_max_size", "get_min_size"):
        real = getattr(asyncpg.Pool, m)
        assert callable(real) and not inspect.iscoroutinefunction(real), m


def test_pool_stats_is_none_without_a_pool(monkeypatch):
    monkeypatch.setattr(db_mod, "_pool", None)
    assert db_mod.pool_stats() is None


def test_pool_stats_never_raises(monkeypatch):
    class _Broken(_Counters):
        def get_idle_size(self):
            raise RuntimeError("pool is closing")

    monkeypatch.setattr(db_mod, "_pool", _Broken())
    assert db_mod.pool_stats() is None
    monkeypatch.setattr(db_mod, "_pool", object())          # no counters at all
    assert db_mod.pool_stats() is None

    class _Odd(_Counters):
        def get_size(self):
            return "three"

    monkeypatch.setattr(db_mod, "_pool", _Odd())
    assert db_mod.pool_stats() is None


# ------------------------------------------------------------ Poller.run

class _Stop(Exception):
    """Raised from the paced sleep to end run(). An Exception on purpose
    (the retention tests end main() the same way): run() must let a
    non-database error out while containing the database one."""


class _Client:
    """The httpx client as run() sees it: something to aclose()."""

    def __init__(self):
        self.aclosed = 0

    async def aclose(self):
        self.aclosed += 1


def _poller(interval=3.5, priority=2.5) -> Poller:
    """A Poller without __init__ (settings + a real client), the way the
    neighbouring poll_wallet tests build one. 3.5 is the roster retry
    pace and nothing else's: the fast lane sleeps 5 on error and 10 on
    an empty roster, the history loop 60, so the sentinel below can
    count the MAIN loop's retries alone."""
    p = Poller.__new__(Poller)
    p._http = _Client()
    p._interval = interval
    p._priority_interval = priority
    p._fail_threshold = 3
    p._consecutive_failures = 0
    p.on_alert = None
    p.last_lag_s = None
    return p


def _dead_db(monkeypatch) -> dict:
    """get_pool raises the way it does after its backoff -- for the
    poller's roster read AND for db.heartbeat, which the poller's beat
    goes through. Yields once first, so a loop that neither paces nor
    awaits anything else still lets the event loop turn."""
    calls = {"n": 0}

    async def _no_pool():
        calls["n"] += 1
        await _REAL_SLEEP(0)
        raise RuntimeError("could not connect to Postgres")

    monkeypatch.setattr(poller_mod, "get_pool", _no_pool)
    monkeypatch.setattr(db_mod, "get_pool", _no_pool)
    return calls


def _record_beats(monkeypatch) -> list[tuple]:
    beats: list[tuple] = []

    async def _hb(service, status="ok", detail=None):
        beats.append((service, status, detail))
        await _REAL_SLEEP(0)

    monkeypatch.setattr(poller_mod, "heartbeat", _hb)
    return beats


def _record_alerts(p: Poller) -> list[str]:
    alerts: list[str] = []

    async def _alert(msg):
        alerts.append(msg)
        await _REAL_SLEEP(0)

    p.on_alert = _alert
    return alerts


def _fake_backfill(monkeypatch) -> dict:
    """The history loop's late import resolves on the history module."""
    from sportsassets.ingestion import history as history_mod

    calls = {"n": 0}

    async def _backfill():
        calls["n"] += 1
        return 0

    monkeypatch.setattr(history_mod, "backfill_pending", _backfill)
    return calls


def _bounded(coro):
    """run() under a wall-clock ceiling, so a finally that waits on
    something immortal, or a main loop that lost its pace and spins
    through fakes that yield but never sleep, is a failure in two
    seconds, not a hung suite (the retention tests' stance). The
    slowest real scenario here waits ~50 ms; a finally that honoured
    the full CANCEL_WAIT_S would be cut off by this and fail, which is
    the right verdict for a loop that ignored its cancel."""
    return asyncio.wait_for(coro, 2.0)


# The most sleeps of ANY duration a scenario may make before the paced
# sentinel fires: a loop that spins on some other pace than the one
# the sentinel recognises fails here in milliseconds, with a message
# that says which pace it was on. The busiest scenario below makes a
# few dozen. (A loop that sleeps NOT AT ALL never reaches this; the
# wall clock in _bounded is what ends that one.)
_SLEEP_CEILING = 1000


def _paced_stop(monkeypatch, interval: float, after: int) -> dict:
    """asyncio.sleep that yields instead of waiting, records durations,
    and raises _Stop from the `after`-th sleep of exactly `interval`
    seconds -- the main loop's roster retry pace."""
    state = {"n": 0, "slept": []}

    async def _sleep(s):
        state["slept"].append(s)
        if len(state["slept"]) > _SLEEP_CEILING:
            raise AssertionError(
                f"pace-less loop: {_SLEEP_CEILING} sleeps and only "
                f"{state['n']} of the {after} paced at {interval}s")
        if s == interval:
            state["n"] += 1
            if state["n"] >= after:
                raise _Stop()
        await _REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return state


def _not_written(caplog, status: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records
            if r.name == LOGGER and r.levelno == logging.WARNING
            and r.getMessage().startswith(f"poller heartbeat {status!r} not written")]


def test_an_unreadable_roster_is_a_paced_retry_not_a_raise(monkeypatch, caplog):
    _dead_db(monkeypatch)
    _fake_backfill(monkeypatch)
    beats = _record_beats(monkeypatch)
    p = _poller(interval=3.5)
    alerts = _record_alerts(p)
    state = _paced_stop(monkeypatch, 3.5, after=4)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(_Stop):
            asyncio.run(_bounded(p.run()))
    # four paced retries went by and run() was still alive for each
    assert state["n"] == 4
    assert state["slept"].count(3.5) == 4
    errs = [b for b in beats if b[0] == "poller" and b[1] == "error"]
    assert len(errs) == 4
    assert all(b[2]["stage"] == "roster" and "Postgres" in b[2]["error"] for b in errs)
    # each roster failure counted toward the degraded threshold ...
    assert [b[2]["failures"] for b in errs] == [1, 2, 3, 4]
    assert p._consecutive_failures == 4
    # ... and the alert fired ONCE, at exactly the threshold
    assert len(alerts) == 1 and "3×" in alerts[0] and "Path B degraded" in alerts[0]
    assert [r for r in caplog.records
            if r.name == LOGGER and "roster unreadable" in r.getMessage()]


def test_the_roster_branch_alert_fires_in_the_third_failed_cycle_not_the_fourth(
        monkeypatch):
    """'Once, exactly at the threshold' pinned by WHEN, not by how
    many (re-review 2026-09-05: a roster branch that checked the
    threshold before counting the failure fired one pace late, and
    every count-based pin still passed). On a timeline of the beats,
    the alert and the paced sleeps, the alert sits between the third
    'error' beat and the third pace, not after it."""
    _dead_db(monkeypatch)
    timeline: list[tuple] = []

    async def _hb(service, status="ok", detail=None):
        timeline.append(("beat", detail["failures"]))
        await _REAL_SLEEP(0)

    monkeypatch.setattr(poller_mod, "heartbeat", _hb)
    p = _poller(interval=3.5, priority=0)

    async def _alert(msg):
        timeline.append(("alert", msg))

    p.on_alert = _alert

    async def _sleep(s):
        timeline.append(("pace", s))
        if len([t for t in timeline if t[0] == "pace"]) >= 4:
            raise _Stop()
        await _REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    with pytest.raises(_Stop):
        asyncio.run(_bounded(p.run(history=False)))
    kinds = [f"beat{t[1]}" if t[0] == "beat" else t[0] for t in timeline]
    assert kinds == ["beat1", "pace", "beat2", "pace", "beat3", "alert", "pace",
                     "beat4", "pace"], kinds
    assert all(s == 3.5 for k, s in timeline if k == "pace")


def test_a_roster_read_that_raises_the_outages_exception_is_the_same_paced_retry(
        monkeypatch, caplog):
    """The real tracked_whales against a pool whose fetch raises what
    the 2026-09-04/05 database actually raised, once it accepted
    connections and then dropped them mid-statement."""
    pool = _live_db(monkeypatch, _DbPool(
        fetch_raises=asyncpg.exceptions.ConnectionDoesNotExistError(_OUTAGE)))
    beats = _record_beats(monkeypatch)
    p = _poller(interval=3.5, priority=0)
    alerts = _record_alerts(p)
    state = _paced_stop(monkeypatch, 3.5, after=4)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(_Stop):
            asyncio.run(_bounded(p.run(history=False)))
    assert state["n"] == 4
    assert len(pool.fetches) == 4 and all("FROM whales" in s for s in pool.fetches)
    errs = [b for b in beats if b[0] == "poller" and b[1] == "error"]
    assert len(errs) == 4
    assert all(b[2]["stage"] == "roster" and _OUTAGE in b[2]["error"] for b in errs)
    assert [b[2]["failures"] for b in errs] == [1, 2, 3, 4]
    assert len(alerts) == 1 and "3×" in alerts[0]
    assert len([r for r in caplog.records if r.name == LOGGER
                and "roster unreadable" in r.getMessage() and _OUTAGE in r.getMessage()]) == 4
    assert p._http.aclosed == 1


def test_an_alert_channel_that_raises_cannot_end_the_poller(monkeypatch, caplog):
    """Telegram down on the same bad night as the database: the alert
    is attempted once at the threshold, its raise is a log line, and
    the paced retries go on past it. The raise is an httpx error, not
    a RuntimeError, on purpose (re-review 2026-09-05: a guard narrowed
    to `except RuntimeError` survived this pin while it raised one) --
    what a carrier raises is the carrier's business, and the guard is
    `except Exception` because of it."""
    _dead_db(monkeypatch)
    _record_beats(monkeypatch)
    p = _poller(interval=3.5, priority=0)
    attempts: list[str] = []

    async def _broken_alert(msg):
        attempts.append(msg)
        raise httpx.HTTPError("telegram: 502")

    p.on_alert = _broken_alert
    state = _paced_stop(monkeypatch, 3.5, after=5)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(_Stop):
            asyncio.run(_bounded(p.run(history=False)))
    assert state["n"] == 5
    assert len(attempts) == 1 and "3×" in attempts[0]
    assert p._consecutive_failures == 5
    undelivered = [r for r in caplog.records if r.name == LOGGER
                   and "degraded-alert not delivered" in r.getMessage()
                   and "telegram: 502" in r.getMessage()]
    assert len(undelivered) == 1


def test_an_alert_channel_that_raises_cannot_end_the_poller_from_the_wallet_branch(
        monkeypatch, caplog):
    """The same guard reached from the OTHER threshold site (re-review
    2026-09-05: with only the roster branch pinned, the wallet branch
    could go back to the old unguarded `await self.on_alert(...)` and
    every test still passed). The roster reads fine, every poll fails
    the venue's way, the channel raises an httpx error at the third:
    one attempt, a 'not delivered' line carrying its text, five paced
    wallet cycles, run() alive for each."""
    _live_db(monkeypatch, _DbPool(roster=_ROSTER))
    _record_beats(monkeypatch)

    async def _poll(self, whale):
        await _REAL_SLEEP(0)
        raise ValueError("venue served a non-list /trades body: dict")

    monkeypatch.setattr(Poller, "poll_wallet", _poll)
    p = _poller(interval=3.5, priority=0)
    attempts: list[str] = []

    async def _broken_alert(msg):
        attempts.append(msg)
        raise httpx.ConnectError("telegram: connect timeout")

    p.on_alert = _broken_alert
    state = _paced_stop(monkeypatch, 1.75, after=5)     # 3.5 / 2 wallets
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(_Stop):
            asyncio.run(_bounded(p.run(history=False)))
    assert state["n"] == 5
    assert len(attempts) == 1 and "3×" in attempts[0]
    assert p._consecutive_failures == 5
    undelivered = [r for r in caplog.records if r.name == LOGGER
                   and r.levelno == logging.WARNING
                   and "degraded-alert not delivered" in r.getMessage()
                   and "telegram: connect timeout" in r.getMessage()]
    assert len(undelivered) == 1
    assert p._http.aclosed == 1


def test_an_alert_channel_that_hangs_cannot_hold_the_poller(monkeypatch, caplog):
    """The containment re-review's other finding (2026-09-05): the
    guard around on_alert caught a raise, not a hang, and a channel
    that neither answers nor fails held run() at the threshold with
    nothing logged -- and the alert fires on exactly the night the
    carrier is likeliest to be gone too. Pinned with an on_alert that
    never returns and ALERT_TIMEOUT_S shortened for the test (the real
    _alert_degraded is what runs): one 'degraded-alert not delivered'
    line naming TimeoutError, the paced retries go on past it; and the
    real ceiling sits above telegram._send's own httpx timeout so a
    channel with its own error text puts that text on the line."""
    _dead_db(monkeypatch)
    _record_beats(monkeypatch)
    real_ceiling = poller_mod.ALERT_TIMEOUT_S
    monkeypatch.setattr(poller_mod, "ALERT_TIMEOUT_S", 0.05)
    p = _poller(interval=3.5, priority=0)
    attempts: list[str] = []

    async def _hanging_alert(msg):
        attempts.append(msg)
        await asyncio.Event().wait()          # never

    p.on_alert = _hanging_alert
    state = _paced_stop(monkeypatch, 3.5, after=5)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(_Stop):
            asyncio.run(_bounded(p.run(history=False)))
    assert state["n"] == 5
    assert len(attempts) == 1 and "3×" in attempts[0]
    assert p._consecutive_failures == 5
    undelivered = [r for r in caplog.records if r.name == LOGGER
                   and "degraded-alert not delivered" in r.getMessage()]
    assert len(undelivered) == 1
    assert "TimeoutError" in undelivered[0].getMessage()
    assert p._http.aclosed == 1
    # the real ceiling, and why it is what it is: above the channel's own
    from sportsassets.notifications import telegram
    own = re.search(r"httpx\.AsyncClient\(timeout=(\d+(?:\.\d+)?)\)",
                    inspect.getsource(telegram._send))
    assert own, "telegram._send no longer carries an httpx timeout of its own"
    assert real_ceiling == 15.0
    assert real_ceiling > float(own.group(1))


def test_a_heartbeat_that_cannot_be_written_is_logged_not_raised(monkeypatch, caplog):
    """The REAL db.heartbeat, against the dead getter: it raises inside
    the poller's beat, the beat logs it, run() goes on."""
    dead = _dead_db(monkeypatch)
    p = _poller(interval=3.5, priority=0)
    state = _paced_stop(monkeypatch, 3.5, after=3)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(_Stop):
            asyncio.run(_bounded(p.run(history=False)))
    assert state["n"] == 3
    assert len(_not_written(caplog, "error")) == 3
    assert dead["n"] == 6                       # 3 roster reads + 3 beats
    assert p._http.aclosed == 1


_ROSTER = [{"id": 1, "address": "0xaaaa", "username": "rn1"},
           {"id": 2, "address": "0xbbbb", "username": "quiet"}]


def test_the_per_wallet_ok_beat_that_cannot_be_written_is_logged_and_the_wallet_stays_ok(
        monkeypatch, caplog):
    """The 'ok' site: two wallets poll fine, the REAL db.heartbeat
    raises the outage's exception on every write. A bare heartbeat()
    here would raise inside the per-wallet try, be counted as a wallet
    FAILURE and beat 'error' -- so this pins the status logged, the
    counter at zero, and the polls continuing at the stagger."""
    pool = _live_db(monkeypatch, _DbPool(
        roster=_ROSTER,
        execute_raises=asyncpg.exceptions.ConnectionDoesNotExistError(_OUTAGE)))
    polled: list[str] = []

    async def _poll(self, whale):
        polled.append(whale["address"])
        await _REAL_SLEEP(0)
        return 2

    monkeypatch.setattr(Poller, "poll_wallet", _poll)
    p = _poller(interval=3.5, priority=0)
    alerts = _record_alerts(p)
    state = _paced_stop(monkeypatch, 1.75, after=4)   # 3.5 / 2 wallets
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(_Stop):
            asyncio.run(_bounded(p.run(history=False)))
    assert state["n"] == 4
    assert polled == ["0xaaaa", "0xbbbb", "0xaaaa", "0xbbbb"]
    assert len(_not_written(caplog, "ok")) == 4
    assert _not_written(caplog, "error") == []
    assert [a[1] for a, _ in pool.executes] == ["ok"] * 4
    assert all('"new": 2' in a[2] and '"last_wallet"' in a[2] for a, _ in pool.executes)
    assert p._consecutive_failures == 0
    assert alerts == []
    assert p._http.aclosed == 1


def test_the_per_wallet_error_beat_that_cannot_be_written_is_logged_and_the_alert_still_fires(
        monkeypatch, caplog):
    """The 'error' site: every poll fails (the venue's non-list body),
    the REAL db.heartbeat raises on every write. A bare heartbeat()
    here raises out of the except branch and out of run() -- and the
    degraded alert sits AFTER it, which is what made the alert
    unreachable with the database down. Pinned: four failures counted,
    the alert once at three, run() paced on."""
    pool = _live_db(monkeypatch, _DbPool(
        roster=_ROSTER,
        execute_raises=asyncpg.exceptions.ConnectionDoesNotExistError(_OUTAGE)))

    async def _poll(self, whale):
        await _REAL_SLEEP(0)
        raise ValueError("venue served a non-list /trades body: dict")

    monkeypatch.setattr(Poller, "poll_wallet", _poll)
    p = _poller(interval=3.5, priority=0)
    alerts = _record_alerts(p)
    state = _paced_stop(monkeypatch, 1.75, after=4)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(_Stop):
            asyncio.run(_bounded(p.run(history=False)))
    assert state["n"] == 4
    assert len(_not_written(caplog, "error")) == 4
    assert _not_written(caplog, "ok") == []
    assert [a[1] for a, _ in pool.executes] == ["error"] * 4
    assert ['"failures": %d' % n in a[2] for n, (a, _) in enumerate(pool.executes, 1)] \
        == [True] * 4
    assert p._consecutive_failures == 4
    assert len(alerts) == 1 and "3×" in alerts[0] and "Path B degraded" in alerts[0]
    assert len([r for r in caplog.records if r.name == LOGGER
                and "poll failed for" in r.getMessage()]) == 4


def test_the_empty_roster_idle_beat_that_cannot_be_written_is_logged_not_raised(
        monkeypatch, caplog):
    """The 'idle' site: the roster reads fine and is empty, the REAL
    db.heartbeat raises on every write. A bare heartbeat() here raises
    straight out of run() on the first pass."""
    pool = _live_db(monkeypatch, _DbPool(
        roster=[],
        execute_raises=asyncpg.exceptions.ConnectionDoesNotExistError(_OUTAGE)))
    p = _poller(interval=3.5, priority=0)
    state = _paced_stop(monkeypatch, 3.5, after=3)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(_Stop):
            asyncio.run(_bounded(p.run(history=False)))
    assert state["n"] == 3
    assert len(pool.fetches) == 3
    assert len(_not_written(caplog, "idle")) == 3
    assert [a[1] for a, _ in pool.executes] == ["idle"] * 3
    assert all('"empty roster"' in a[2] for a, _ in pool.executes)
    assert p._consecutive_failures == 0
    assert p._http.aclosed == 1


def test_a_heartbeat_that_hangs_ends_at_its_ceiling_and_run_goes_on(monkeypatch, caplog):
    """The stalled-database shape through the whole stack: the REAL
    db.heartbeat under the REAL _beat, against an execute that hangs
    until its timeout. The write ends in a TimeoutError, the beat logs
    it as not written, the paced loop continues; without the ceiling
    the first beat would hold run() forever and _bounded's guard would
    be what ended this test."""
    pool = _live_db(monkeypatch, _DbPool(roster=[], execute_hangs=True))
    p = _poller(interval=3.5, priority=0)
    state = _paced_stop(monkeypatch, 3.5, after=2)
    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(_Stop):
            asyncio.run(_bounded(p.run(history=False)))
    assert time.monotonic() - t0 < 1.5
    assert state["n"] == 2
    assert [t for _, t in pool.executes] == [db_mod.HEARTBEAT_TIMEOUT_S] * 2
    idle = _not_written(caplog, "idle")
    assert len(idle) == 2
    # a TimeoutError's str() is empty, so the line names the type
    assert all("TimeoutError" in r.getMessage() for r in idle)
    assert p._http.aclosed == 1


def test_a_heartbeat_against_a_saturated_pool_ends_at_its_ceiling_and_run_goes_on(
        monkeypatch, caplog):
    """The saturated-pool shape through the whole stack: the REAL
    db.heartbeat under the REAL _beat, against a pool whose acquire
    hangs until its timeout. The acquire ends in a TimeoutError, the
    beat logs it as not written, the paced loop continues, and no
    statement was ever sent. With the ceiling on pool.execute alone
    the first beat sat in acquire forever and _bounded's guard would
    be what ended this test."""
    pool = _live_db(monkeypatch, _DbPool(roster=[], acquire_hangs=True))
    p = _poller(interval=3.5, priority=0)
    state = _paced_stop(monkeypatch, 3.5, after=2)
    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(_Stop):
            asyncio.run(_bounded(p.run(history=False)))
    assert time.monotonic() - t0 < 1.5
    assert state["n"] == 2
    assert pool.acquires == [db_mod.HEARTBEAT_TIMEOUT_S] * 2
    assert pool.executes == []
    assert pool.released == 0
    idle = _not_written(caplog, "idle")
    assert len(idle) == 2
    assert all("TimeoutError" in r.getMessage() for r in idle)
    assert p._http.aclosed == 1


def test_run_exit_cancels_both_loops_and_closes_the_client(monkeypatch):
    dead = _dead_db(monkeypatch)
    backfill = _fake_backfill(monkeypatch)
    _record_beats(monkeypatch)
    p = _poller(interval=3.5, priority=2.5)
    _paced_stop(monkeypatch, 3.5, after=3)

    async def _drive():
        with pytest.raises(_Stop):
            await _bounded(p.run())
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    leftover = asyncio.run(_drive())
    assert leftover == []
    # the REAL loops were spawned, ran, and are cancelled and done
    assert len(p._subtasks) == 2
    assert {t.get_coro().__qualname__ for t in p._subtasks} == {
        "Poller._history_loop", "Poller._priority_loop"}
    assert all(t.done() and t.cancelled() for t in p._subtasks)
    assert backfill["n"] >= 1                   # the history loop ran
    assert dead["n"] > 3                        # the fast lane read the roster too
    assert p._http.aclosed == 1


def test_run_closes_the_client_when_it_is_cancelled(monkeypatch):
    """The other way run() ends: the API's ingestion fallback is
    cancelled at shutdown, and a supervised loop can be too.

    The cancelled run() is waited for WITHOUT cancelling it again
    (re-review 2026-09-05). This used to await it under wait_for,
    whose timeout CANCELS its task a second time -- and a _beat that
    swallowed cancels (an `except BaseException` regression) swallowed
    that one too, about one run in three, so wait_for's own
    wait-for-the-cancel held the whole suite past 600 s instead of
    failing it. asyncio.wait only watches: a run() that swallowed its
    cancel is a failed assertion in 2 s. The finally then ends
    whatever is still alive through the patched sleep, so asyncio.run's
    shutdown is clean either way."""
    _dead_db(monkeypatch)
    _fake_backfill(monkeypatch)
    _record_beats(monkeypatch)
    p = _poller(interval=3.5, priority=2.5)
    stop = {"now": False}

    async def _sleep(s):
        if stop["now"]:
            raise _Stop()
        await _REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", _sleep)

    async def _drive():
        task = asyncio.create_task(p.run())
        for _ in range(20):
            await _REAL_SLEEP(0)
        task.cancel()
        try:
            done, _ = await asyncio.wait({task}, timeout=2.0)
            assert task in done, "run() did not end within 2 s of its cancel"
            assert task.cancelled(), "run() ended, but not by its cancel"
        finally:
            stop["now"] = True
            await asyncio.gather(
                *[t for t in asyncio.all_tasks() if t is not asyncio.current_task()],
                return_exceptions=True)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    assert asyncio.run(_drive()) == []
    assert len(p._subtasks) == 2
    assert all(t.done() and t.cancelled() for t in p._subtasks)
    assert p._http.aclosed == 1


def test_a_side_loop_that_ignores_cancel_is_abandoned_with_an_error_not_waited_on(
        monkeypatch, caplog):
    """The bound on the finally's wait: a child that swallows its
    CancelledError (nothing in the tree does today; this is the shape
    that would make run() hang forever and supervise() never restart
    Path B) is logged and left behind, the client is still closed, and
    run() returns. CANCEL_WAIT_S is shortened for the test; the real
    finally is what runs. The immortal is released in a finally so a
    failed assertion cannot leave it holding asyncio.run's shutdown."""
    _dead_db(monkeypatch)
    _record_beats(monkeypatch)
    monkeypatch.setattr(poller_mod, "CANCEL_WAIT_S", 0.05)
    p = _poller(interval=3.5, priority=2.5)
    state = _paced_stop(monkeypatch, 3.5, after=2)
    gate: dict = {}

    async def _immortal(self):
        # a fast lane that refuses to die: swallows every cancel until
        # the test lets it go
        while True:
            try:
                await gate["ev"].wait()
                return
            except BaseException:  # noqa: BLE001 -- the shape under test
                continue

    monkeypatch.setattr(Poller, "_priority_loop", _immortal)

    async def _drive():
        gate["ev"] = asyncio.Event()
        try:
            with caplog.at_level(logging.ERROR, logger=LOGGER):
                with pytest.raises(_Stop):
                    await _bounded(p.run(history=False))
            return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        finally:
            gate["ev"].set()            # now let it finish so the loop closes clean
            await asyncio.gather(
                *[t for t in asyncio.all_tasks() if t is not asyncio.current_task()],
                return_exceptions=True)

    alive = asyncio.run(_drive())
    assert state["n"] == 2
    assert len(alive) == 1 and alive[0] is p._subtasks[0]
    assert alive[0].get_name() == "poller.priority"
    assert [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.ERROR
            and "ignored cancel" in r.getMessage() and "abandoned" in r.getMessage()]
    assert p._http.aclosed == 1


def test_a_side_loop_that_dies_on_its_own_is_logged_while_run_is_alive_and_again_on_exit(
        monkeypatch, caplog):
    """Both loops catch Exception and go on forever, so a side loop
    that ENDS is a bug in its own containment -- and the first cut of
    the finally retrieved that exception and discarded it. Pinned with
    a fast lane that dies on its second turn: one ERROR line the
    moment it dies, while the main loop is still pacing (the sentinel
    count at the time of the record says so), and one more when run()
    leaves and finds it dead."""
    _dead_db(monkeypatch)
    _record_beats(monkeypatch)
    p = _poller(interval=3.5, priority=2.5)
    state = _paced_stop(monkeypatch, 3.5, after=3)

    async def _dies(self):
        await _REAL_SLEEP(0)
        raise KeyError("fast lane containment bug")

    monkeypatch.setattr(Poller, "_priority_loop", _dies)
    seen: list[tuple[str, int]] = []     # (message, paced retries so far)

    class _AtRecord(logging.Handler):
        def emit(self, record):
            if record.levelno == logging.ERROR and "died on its own" in record.getMessage():
                seen.append((record.getMessage(), state["n"]))

    handler = _AtRecord()
    logging.getLogger(LOGGER).addHandler(handler)
    try:
        with pytest.raises(_Stop):
            asyncio.run(_bounded(p.run(history=False)))
    finally:
        logging.getLogger(LOGGER).removeHandler(handler)
    assert state["n"] == 3
    assert len(seen) == 2, seen
    (at_death, n_death), (at_exit, n_exit) = seen
    assert "poller.priority" in at_death and "fast lane containment bug" in at_death
    assert n_death < 3, "logged while run() was still pacing, not only at exit"
    assert "poller.priority" in at_exit and "found dead when run() left" in at_exit
    assert n_exit == 3
    assert len(p._subtasks) == 1 and p._subtasks[0].done() and not p._subtasks[0].cancelled()
    assert isinstance(p._subtasks[0].exception(), KeyError)
    assert p._http.aclosed == 1


def test_no_task_from_a_previous_run_survives_a_second_run(monkeypatch):
    """supervise()'s shape, in one loop: run() ends, a new Poller runs.
    Before 2026-09-05 the first run's two loops were still alive under
    the second."""
    _dead_db(monkeypatch)
    _fake_backfill(monkeypatch)
    _record_beats(monkeypatch)
    state = _paced_stop(monkeypatch, 3.5, after=3)
    pollers: list[Poller] = []

    async def _drive():
        out = []
        for _ in range(2):
            state["n"] = 0
            p = _poller(interval=3.5, priority=2.5)
            pollers.append(p)
            with pytest.raises(_Stop):
                await _bounded(p.run())
            out.append([t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return out

    first, second = asyncio.run(_drive())
    assert first == [] and second == []
    assert all(t.done() for p in pollers for t in p._subtasks)
    assert [p._http.aclosed for p in pollers] == [1, 1]


def test_the_real_http_client_is_closed_when_run_exits(monkeypatch):
    """The real __init__ and the real httpx.AsyncClient: run()'s finally
    awaits its aclose(), so the client is closed, not collected."""
    _dead_db(monkeypatch)
    _record_beats(monkeypatch)
    p = Poller()
    assert isinstance(p._http, httpx.AsyncClient) and not p._http.is_closed
    p._interval = 3.5
    p._priority_interval = 0
    _paced_stop(monkeypatch, 3.5, after=2)
    with pytest.raises(_Stop):
        asyncio.run(_bounded(p.run(history=False)))
    assert p._http.is_closed
    assert p._subtasks == []


def _is_create_task(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_task")


def test_run_keeps_every_create_task_handle_and_has_a_finally():
    fn = ast.parse(textwrap.dedent(inspect.getsource(Poller.run))).body[0]
    assert isinstance(fn, ast.AsyncFunctionDef)
    spawned = [n for n in ast.walk(fn) if _is_create_task(n)]
    assert len(spawned) == 2, "the history loop and the fast lane"
    discarded = [n for n in ast.walk(fn)
                 if isinstance(n, ast.Expr) and _is_create_task(n.value)]
    assert discarded == [], "a create_task whose handle is dropped"
    # every spawned task gets the death callback, at spawn time
    hooked = [n for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "add_done_callback"
              and any(isinstance(a, ast.Attribute) and a.attr == "_side_loop_died"
                      for a in n.args)]
    assert len(hooked) == 2, "each side loop reports its own death"
    with_finally = [n for n in ast.walk(fn) if isinstance(n, ast.Try) and n.finalbody]
    assert with_finally, "run() has no finally"
    fin = "\n".join(ast.unparse(s) for t in with_finally for s in t.finalbody)
    assert ".cancel()" in fin and "aclose()" in fin
    assert "died on its own" in fin, "a self-died loop is silenced on the way out"
    # the roster read is awaited inside a try that has an except
    guarded = [
        t for t in ast.walk(fn) if isinstance(t, ast.Try) and t.handlers
        and any(isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Attribute)
                and n.value.func.attr == "tracked_whales"
                for s in t.body for n in ast.walk(s))]
    assert guarded, "tracked_whales() is awaited outside any try"
