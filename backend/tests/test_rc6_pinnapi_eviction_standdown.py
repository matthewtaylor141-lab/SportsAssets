"""CAPITAL-CRITICAL: AN EVICTION LOOP STANDS DOWN; IT NO LONGER ENDS THE FEED.

PRODUCTION (deployed RC5 API, 2026-10-08). The PinnAPI owner counted three
unrequested closes inside EVICTION_WINDOW_S (~18:52:40Z, 18:55:40Z,
19:00:51Z), set refused = FEED_EVICTION_LOOP_SUSPECTED at 19:00:51Z and,
being a REFUSAL, was never restarted by pinnapi_feed_runtime.supervise.
Five hours and more later no backend held the feed lease, every 15-minute
cycle carried 85-104 FEED_OWNERSHIP_NOT_HELD rows, and Xavier's held reads
and the collector's WS reference were absent. Only a process restart could
bring the feed back: a freshness defect.

Pinned here (the reference implementation is rc6/pipeline-reds 9c0c1fe6 and
c9db8ccf, ported onto the RC6 owner and runtime):

  1. an EVICTION_LOOP refusal is a STAND-DOWN of a stated length (30 min,
     1 h, 2 h, then 4 h for every later loop), timed on the MONOTONIC clock;
     the escalation counts stand-downs OPENED in this process; after it the
     SAME owner re-enters once -- refusal and eviction history cleared, any
     leaked lease session discarded -- and its authority stays revoked until
     the new epoch has contended, passed the fence and the arm row and
     resynchronized;
  2. a record belongs to the runtime that opened it: another runtime's is
     retired (RETIRED_RECORD_OF_ANOTHER_RUNTIME), never re-entered;
     shutdown_default closes an open one (CUT_SHORT_BY_OWNER_SHUTDOWN), kept
     visible as eviction_standdown_prior_runtime; the escalation survives a
     hold change (the defect a reviewer found in 9c0c1fe6);
  3. env PINNAPI_EVICTION_STANDDOWN=off restores the exact pre-RC6
     permanent refusal; the default is on;
  4. every other refusal (writer lock lost, unauthorized, plan_lacks_ws, no
     key) stays permanent, and a stopped owner is never re-entered;
     EVICTIONS_MAX, EVICTION_WINDOW_S, SILENCE_S are unchanged.
"""
from __future__ import annotations

import asyncio
import time
import types

import pytest
from websockets.exceptions import ConnectionClosedError

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as RT
from sportsassets import pinnapi_owner as O

from tests import paper_harness as H
from tests.test_pinnapi_feed_ownership import FakeWS, frames_for, until
from tests.test_pinnapi_feed_runtime import Pool, _owner, _set
from tests.test_rc6_feed_close_provenance import ClosingWS

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
#: the kill switch's name is the operator contract; spelled out here
STANDDOWN_ENV = "PINNAPI_EVICTION_STANDDOWN"


def _no_frame_ws():
    """Delivers the snapshots, then the connection ends with no close frame
    at all (1006) -- eviction-consistent, so it counts toward the limit."""
    return ClosingWS(frames_for(), ConnectionClosedError(None, None, None))


@pytest.fixture(autouse=True)
def _isolated_standdown_state(monkeypatch):
    """Every stand-down key starts empty and is restored afterwards: the
    runtime state is module-global and the escalation is process-wide. The
    kill switch is at its default (unset = on) unless a test sets it."""
    monkeypatch.delenv(STANDDOWN_ENV, raising=False)
    for k, v in (("standdown", None), ("standdown_prior", None),
                 ("eviction_standdowns", 0), ("eviction_reentries", 0),
                 ("runtime_id", None)):
        monkeypatch.setitem(RT._STATE, k, v)


class _Clock:
    """Stands in for the runtime's `time` module: a monotonic clock and a
    wall clock that move independently."""

    def __init__(self, mono, wall):
        self.mono, self.wall = float(mono), float(wall)

    def monotonic(self):
        return self.mono

    def time(self):
        return self.wall


async def _ended_task():
    async def done():
        return None
    t = asyncio.create_task(done())
    await t
    return t


def _refused_owner(why=O.R_EVICTION_LOOP):
    o = O.FeedOwner(F.FeedCache(), sport_ids=[6], lease_factory=None,
                    connect=None)
    o.refused = why
    o.state = "EVICTION_LOOP_SUSPECTED"
    o.evictions = [1.0, 2.0, 3.0]
    o.cache.lost(why)
    return o


def _parked_run(monkeypatch, o):
    ran = asyncio.Event()

    async def fake_run():
        ran.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(o, "run", fake_run)
    return ran


class _LeakedLease:
    def __init__(self):
        self.discarded = False

    def discard(self):
        self.discarded = True


# ── 0. THE DEFECT, by behaviour: refused forever ─────────────────────


@pg
async def test_an_eviction_loop_no_longer_leaves_the_owner_refused_forever(
        monkeypatch):
    """BASE (412c4962): supervise() never restarts a refusal, so a real
    owner that tripped EVICTION_LOOP_SUSPECTED is still refused -- and no
    socket is ever opened again -- however long the process lives."""
    conn = await H.connect()
    pool = Pool(conn)
    clock = _Clock(mono=10_000.0, wall=1_791_000_000.0)
    monkeypatch.setattr(RT, "time", clock)
    try:
        await _set(conn, RT.CONTROL_KEY, True)
        monkeypatch.setattr(O, "BACKOFF", (0.05,))
        socks, cache = [], F.FeedCache()
        o = _owner(cache, socks, pool, monkeypatch, ws=_no_frame_ws)
        first = asyncio.create_task(o.run())
        await asyncio.wait_for(asyncio.shield(first), 15)
        assert o.refused == O.R_EVICTION_LOOP
        assert len(socks) == O.EVICTIONS_MAX
        monkeypatch.setitem(RT._STATE, "owner", o)
        monkeypatch.setitem(RT._STATE, "task", first)
        monkeypatch.setitem(RT._STATE, "runtime_id", "rt-prod")
        # every heartbeat pass for five hours (the production gap) ...
        reentered = None
        for _ in range(5 * 120):
            clock.mono += 30.0
            clock.wall += 30.0
            reentered = RT.supervise() or reentered
            if reentered:
                break
        # ... and the owner came back: re-entered after its 30 min
        assert reentered == "EVICTION_STANDDOWN_ENDED", (
            "the owner stayed refused for 5 h: %s" % o.refused)
        second = RT._STATE["task"]
        await asyncio.wait_for(asyncio.shield(second), 15)
        assert len(socks) == 2 * O.EVICTIONS_MAX
    finally:
        t = RT._STATE.get("task")
        if t is not None and not t.done():
            t.cancel()
        await _set(conn, RT.CONTROL_KEY, None)
        await conn.close()


# ── 1. the stand-down, and the one re-entry after it ─────────────────


async def test_an_eviction_loop_stands_down_then_re_enters_once(monkeypatch):
    o = _refused_owner()
    ran = _parked_run(monkeypatch, o)
    leaked = _LeakedLease()
    o.lease = leaked
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "task", await _ended_task())
    monkeypatch.setitem(RT._STATE, "runtime_id", "rt-1")

    t0 = 1_000_000.0
    # the first pass that sees the refusal opens the stand-down, re-enters
    # nothing, and says so on the owner's own transition log
    assert RT.supervise(now=t0) is None
    sd = RT._STATE["standdown"]
    assert sd["seconds"] == 1800.0 and sd["until_mono"] == t0 + 1800.0
    assert sd["since_mono"] == t0 and sd["ordinal"] == 1
    assert sd["runtime_id"] == "rt-1" and sd["ended_mono"] is None
    assert sd["refused"] == O.R_EVICTION_LOOP
    # the wall clock is display only
    assert sd["until_at"] - sd["since_at"] == pytest.approx(1800.0)
    assert "EVICTION_STANDDOWN" in [e["what"] for e in o.events]
    d = RT.digest()
    assert d["eviction_standdown"]["until_mono"] == t0 + 1800.0
    assert d["eviction_standdowns"] == 1 and d["eviction_reentries"] == 0
    assert d["eviction_standdown_enabled"] is True
    # inside the stand-down: nothing, however often it is asked
    for dt in (30.0, 900.0, 1799.0):
        assert RT.supervise(now=t0 + dt) is None
    assert o.refused == O.R_EVICTION_LOOP and not ran.is_set()
    assert not leaked.discarded
    # the authority is revoked the whole time
    assert not o.cache.authority.granted

    assert RT.supervise(now=t0 + 1800.0) == "EVICTION_STANDDOWN_ENDED"
    await asyncio.wait_for(ran.wait(), 2)
    assert o.refused is None and o.evictions == [] and o.state == "STARTING"
    # the leaked lease session is discarded so the re-entry can contend
    assert leaked.discarded and o.lease is None
    assert RT._STATE["eviction_reentries"] == 1
    sd = RT._STATE["standdown"]
    assert sd["ended_mono"] == t0 + 1800.0
    assert sd["ended_by"] == RT.STANDDOWN_ENDED_BY_REENTRY
    assert "EVICTION_STANDDOWN_ENDED" in [e["what"] for e in o.events]
    # still revoked: nothing is served until the new epoch resynchronizes
    assert not o.cache.authority.granted
    assert o.cache.read(1, "s;0;m")["ok"] is False
    assert RT.supervise(now=t0 + 1830.0) is None      # running: nothing
    RT._STATE["task"].cancel()


async def test_each_later_loop_stands_down_longer_up_to_four_hours(
        monkeypatch):
    o = _refused_owner()
    _parked_run(monkeypatch, o)
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "runtime_id", "rt-1")
    now, seen = 5_000_000.0, []
    for _ in range(6):
        monkeypatch.setitem(RT._STATE, "task", await _ended_task())
        o.refused, o.state = O.R_EVICTION_LOOP, "EVICTION_LOOP_SUSPECTED"
        assert RT.supervise(now=now) is None
        seen.append(RT._STATE["standdown"]["seconds"])
        now = RT._STATE["standdown"]["until_mono"]
        assert RT.supervise(now=now - 0.001) is None
        assert RT.supervise(now=now) == "EVICTION_STANDDOWN_ENDED"
        RT._STATE["task"].cancel()
    assert seen == [1800.0, 3600.0, 7200.0, 14400.0, 14400.0, 14400.0]
    assert RT.EVICTION_STANDDOWN_S == (1800.0, 3600.0, 7200.0, 14400.0)
    assert RT._STATE["eviction_reentries"] == 6
    assert RT._STATE["eviction_standdowns"] == 6


async def test_the_standdown_is_timed_on_the_monotonic_clock(monkeypatch):
    """The reviewer's finding on 9c0c1fe6: the stand-down was timed on the
    wall clock, so a clock step (NTP) could end it at once or stretch it.
    The wall clock now only labels the record for display."""
    o = _refused_owner()
    ran = _parked_run(monkeypatch, o)
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "task", await _ended_task())
    monkeypatch.setitem(RT._STATE, "runtime_id", "rt-1")
    clock = _Clock(mono=50_000.0, wall=1_791_000_000.0)
    monkeypatch.setattr(RT, "time", clock)
    assert RT.supervise() is None
    sd = dict(RT._STATE["standdown"])
    assert sd["since_mono"] == 50_000.0 and sd["since_at"] == 1_791_000_000.0
    assert sd["until_at"] == 1_791_000_000.0 + 1800.0
    # the wall clock steps forward ten days: no early re-entry
    clock.wall += 10 * 86400.0
    clock.mono += 60.0
    assert RT.supervise() is None and not ran.is_set()
    # ... and back two days: the stand-down is not stretched either
    clock.wall -= 12 * 86400.0
    clock.mono = sd["until_mono"]
    assert RT.supervise() == "EVICTION_STANDDOWN_ENDED"
    await asyncio.wait_for(ran.wait(), 2)
    ended = RT._STATE["standdown"]
    assert ended["ended_mono"] == sd["until_mono"]
    assert ended["ended_at"] == clock.wall          # display only
    RT._STATE["task"].cancel()


@pytest.mark.parametrize("why", [O.R_WRITER_LOST, "unauthorized",
                                 "plan_lacks_ws",
                                 "KEY_NOT_PRESENT_IN_THIS_SERVICE"])
async def test_every_other_refusal_stays_permanent(monkeypatch, why):
    o = _refused_owner(why)
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "task", await _ended_task())
    for dt in (0.0, 1800.0, 10 ** 7):
        assert RT.supervise(now=1_000.0 + dt) is None
    assert o.refused == why and RT._STATE["standdown"] is None
    assert RT._STATE["eviction_standdowns"] == 0


async def test_a_stopped_owner_is_never_re_entered(monkeypatch):
    o = _refused_owner()
    o.stop()
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "task", await _ended_task())
    assert RT.supervise(now=1.0) is None
    assert RT.supervise(now=10 ** 7) is None
    assert RT._STATE["standdown"] is None


async def test_a_stop_during_the_standdown_is_never_re_entered(monkeypatch):
    o = _refused_owner()
    ran = _parked_run(monkeypatch, o)
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "task", await _ended_task())
    monkeypatch.setitem(RT._STATE, "runtime_id", "rt-1")
    assert RT.supervise(now=100.0) is None
    o.stop()
    assert RT.supervise(now=100.0 + 10 ** 6) is None
    assert not ran.is_set() and o.refused == O.R_EVICTION_LOOP


# ── 2. the kill switch: PINNAPI_EVICTION_STANDDOWN=off ───────────────


@pytest.mark.parametrize("off", ["off", "0", "false", "no", " OFF "])
async def test_the_kill_switch_restores_the_permanent_refusal(monkeypatch,
                                                              off):
    monkeypatch.setenv(STANDDOWN_ENV, off)
    assert RT.standdown_enabled() is False
    o = _refused_owner()
    ran = _parked_run(monkeypatch, o)
    leaked = _LeakedLease()
    o.lease = leaked
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "task", await _ended_task())
    monkeypatch.setitem(RT._STATE, "runtime_id", "rt-1")
    # exactly the pre-RC6 refusal: no record, no re-entry, ever
    for dt in (0.0, 1800.0, 14400.0, 10 ** 7):
        assert RT.supervise(now=1_000.0 + dt) is None
    assert not ran.is_set()
    assert o.refused == O.R_EVICTION_LOOP
    assert o.state == "EVICTION_LOOP_SUSPECTED"
    assert o.evictions == [1.0, 2.0, 3.0] and not leaked.discarded
    assert RT._STATE["standdown"] is None
    assert RT._STATE["eviction_standdowns"] == 0
    assert [e["what"] for e in o.events] == []
    d = RT.digest()
    assert d["eviction_standdown_enabled"] is False
    assert d["eviction_standdown"] is None


@pytest.mark.parametrize("on", [None, "", "on", "1", "true", "yes"])
async def test_the_standdown_is_on_by_default(monkeypatch, on):
    if on is not None:
        monkeypatch.setenv(STANDDOWN_ENV, on)
    assert RT.standdown_enabled() is True
    o = _refused_owner()
    ran = _parked_run(monkeypatch, o)
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "task", await _ended_task())
    monkeypatch.setitem(RT._STATE, "runtime_id", "rt-1")
    assert RT.supervise(now=1_000.0) is None
    assert RT._STATE["standdown"]["seconds"] == 1800.0
    assert RT.supervise(now=2_800.0) == "EVICTION_STANDDOWN_ENDED"
    await asyncio.wait_for(ran.wait(), 2)
    RT._STATE["task"].cancel()


@pg
async def test_the_kill_switch_keeps_a_real_owner_refused(monkeypatch):
    """The real owner, the real lease: with the switch off a tripped owner
    opens no further socket, and its lease is free for whoever is next."""
    monkeypatch.setenv(STANDDOWN_ENV, "off")
    conn = await H.connect()
    pool = Pool(conn)
    try:
        await _set(conn, RT.CONTROL_KEY, True)
        monkeypatch.setattr(O, "BACKOFF", (0.05,))
        socks, cache = [], F.FeedCache()
        o = _owner(cache, socks, pool, monkeypatch, ws=_no_frame_ws)
        first = asyncio.create_task(o.run())
        await asyncio.wait_for(asyncio.shield(first), 15)
        monkeypatch.setitem(RT._STATE, "owner", o)
        monkeypatch.setitem(RT._STATE, "task", first)
        monkeypatch.setitem(RT._STATE, "runtime_id", "rt-off")
        for dt in (0.0, 1800.0, 3600.0, 10 ** 6):
            assert RT.supervise(now=7_000.0 + dt) is None
        await asyncio.sleep(0.2)
        assert len(socks) == O.EVICTIONS_MAX
        assert o.state == "EVICTION_LOOP_SUSPECTED"
        assert RT._STATE["task"] is first
        assert not cache.authority.granted
        assert await conn.fetchval("SELECT pg_try_advisory_lock($1)",
                                   O.FEED_LOCK_KEY)
        await conn.execute("SELECT pg_advisory_unlock($1)", O.FEED_LOCK_KEY)
    finally:
        await _set(conn, RT.CONTROL_KEY, None)
        await conn.close()


# ── 3. end to end: at most EVICTIONS_MAX connections per stand-down ──


@pg
async def test_a_real_owner_re_enters_once_and_trips_again_at_the_threshold(
        monkeypatch):
    conn = await H.connect()
    pool = Pool(conn)
    try:
        await _set(conn, RT.CONTROL_KEY, True)
        monkeypatch.setattr(O, "BACKOFF", (0.05,))
        socks, cache = [], F.FeedCache()
        o = _owner(cache, socks, pool, monkeypatch, ws=_no_frame_ws)
        first = asyncio.create_task(o.run())
        await asyncio.wait_for(asyncio.shield(first), 15)
        assert len(socks) == O.EVICTIONS_MAX
        monkeypatch.setitem(RT._STATE, "owner", o)
        monkeypatch.setitem(RT._STATE, "task", first)
        monkeypatch.setitem(RT._STATE, "runtime_id", "rt-1")

        t0 = 2_000_000.0
        assert RT.supervise(now=t0) is None
        assert len(socks) == O.EVICTIONS_MAX            # no fight
        assert RT.supervise(now=t0 + 1800.0) == "EVICTION_STANDDOWN_ENDED"
        second = RT._STATE["task"]
        await asyncio.wait_for(asyncio.shield(second), 15)
        # the re-entry contended from scratch, took the lease, and stopped
        # at the same threshold: EVICTIONS_MAX more connections, no more
        assert len(socks) == 2 * O.EVICTIONS_MAX
        assert o.state == "EVICTION_LOOP_SUSPECTED"
        assert not cache.authority.granted
        assert RT.supervise(now=t0 + 1900.0) is None
        assert RT._STATE["standdown"]["seconds"] == 3600.0
        # and the lease is free again for whoever is next
        assert await conn.fetchval("SELECT pg_try_advisory_lock($1)",
                                   O.FEED_LOCK_KEY)
        await conn.execute("SELECT pg_advisory_unlock($1)", O.FEED_LOCK_KEY)
    finally:
        await _set(conn, RT.CONTROL_KEY, None)
        await conn.close()


@pg
async def test_the_re_entry_serves_only_once_its_new_epoch_resynchronizes(
        monkeypatch):
    """After the stand-down the provider behaves: the re-entered owner takes
    the lease, passes the fence and the arm row, opens a NEW epoch and
    serves a price only once that epoch's snapshots have all arrived."""
    conn = await H.connect()
    pool = Pool(conn)
    o = None
    try:
        await _set(conn, RT.CONTROL_KEY, True)
        monkeypatch.setattr(O, "BACKOFF", (0.05,))
        socks, cache = [], F.FeedCache()

        def ws():
            return (_no_frame_ws() if len(socks) < O.EVICTIONS_MAX
                    else FakeWS(frames_for()))
        o = _owner(cache, socks, pool, monkeypatch, ws=ws)
        first = asyncio.create_task(o.run())
        await asyncio.wait_for(asyncio.shield(first), 15)
        old_epoch = cache.authority.epoch
        monkeypatch.setitem(RT._STATE, "owner", o)
        monkeypatch.setitem(RT._STATE, "task", first)
        monkeypatch.setitem(RT._STATE, "runtime_id", "rt-1")
        assert RT.supervise(now=10.0) is None
        assert RT.supervise(now=10.0 + 1800.0) == "EVICTION_STANDDOWN_ENDED"
        assert not cache.authority.granted
        assert cache.read(1, "s;0;m")["ok"] is False
        assert await until(lambda: o.state == "OWNER_SYNCED", timeout=15)
        assert cache.authority.epoch == old_epoch + 1
        assert cache.authority.synced
        assert cache.read(1, "s;0;m", evaluated_ms=2_000)["ok"] is True
        assert len(socks) == O.EVICTIONS_MAX + 1
        assert [e["what"] for e in o.events].count("LEASE_ACQUIRED") == \
            O.EVICTIONS_MAX + 1
    finally:
        if o is not None:
            o.stop()
        t = RT._STATE.get("task")
        if t is not None:
            await asyncio.wait_for(t, 10)
        await _set(conn, RT.CONTROL_KEY, None)
        await conn.close()


# ── 4. a hold change: the stand-down belongs to the runtime that opened it ─


class _TerminalPool:
    """Takes shutdown_default's terminal heartbeat and nothing else."""

    def __init__(self):
        self.written = []

    def acquire(self):
        written = self.written

        class Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a):
                written.append(a)
                return "UPDATE 0"
        return Ctx()


def _install(monkeypatch, o, task, runtime_id, pool=None):
    """What start_default installs for a new hold's owner, minus the socket
    and the background loops."""
    for k, v in (("owner", o), ("task", task), ("runtime_id", runtime_id),
                 ("pool", pool or _TerminalPool()), ("beat", None),
                 ("held", None)):
        monkeypatch.setitem(RT._STATE, k, v)


async def test_a_hold_change_cuts_the_standdown_short_and_the_next_owner_stands_down_on_its_own(  # noqa: E501
        monkeypatch):
    o1 = _refused_owner()
    _parked_run(monkeypatch, o1)
    pool1 = _TerminalPool()
    _install(monkeypatch, o1, await _ended_task(), "rt-1", pool1)
    t0 = 3_000_000.0
    assert RT.supervise(now=t0) is None
    first = dict(RT._STATE["standdown"])
    assert first["seconds"] == 1800.0 and first["ended_mono"] is None

    # the writer hold ends: ext_pinnacle_loop's `finally` runs shutdown
    before = time.time()
    out = await RT.shutdown_default(wait_s=0.1)
    assert out["verdict"] == "CLOSED"
    # THE DEFECT (9c0c1fe6): the open record outlived its runtime
    assert RT._STATE.get("standdown") is None
    prior = RT._STATE["standdown_prior"]
    assert prior["runtime_id"] == "rt-1"
    assert prior["ended_by"] == RT.STANDDOWN_CUT_SHORT_BY_SHUTDOWN
    assert prior["ended_at"] >= before - 0.001      # display, ms-rounded
    assert prior["until_mono"] == first["until_mono"]
    assert RT._STATE["eviction_standdowns"] == 1
    assert RT._STATE["eviction_reentries"] == 0      # nothing re-entered
    # the terminal heartbeat carries the record it cut short
    import json
    terminal = json.loads(pool1.written[-1][2])
    assert terminal["eviction_standdown_at_shutdown"]["ended_by"] == (
        RT.STANDDOWN_CUT_SHORT_BY_SHUTDOWN)

    # the next hold's owner (start_default: a new runtime) loops again,
    # later than the cut-short record's end
    o2 = _refused_owner()
    ran = _parked_run(monkeypatch, o2)
    _install(monkeypatch, o2, await _ended_task(), "rt-2")
    t1 = first["until_mono"] + 200.0
    assert RT.supervise(now=t1) is None, "a NEW stand-down, not a re-entry"
    sd = RT._STATE["standdown"]
    # its own record; and the escalation carried across the hold change
    assert sd["runtime_id"] == "rt-2" and sd["ended_mono"] is None
    assert sd["since_mono"] == t1 and sd["seconds"] == 3600.0
    assert sd["until_mono"] == t1 + 3600.0 and sd["ordinal"] == 2
    for dt in (1.0, 1800.0, 3599.0):
        assert RT.supervise(now=t1 + dt) is None
    assert not ran.is_set() and o2.refused == O.R_EVICTION_LOOP
    d = RT.digest()
    assert d["eviction_standdown"]["runtime_id"] == "rt-2"
    assert d["eviction_standdown_prior_runtime"]["runtime_id"] == "rt-1"
    assert d["eviction_standdown_prior_runtime"]["ended_by"] == (
        RT.STANDDOWN_CUT_SHORT_BY_SHUTDOWN)
    assert d["eviction_standdowns"] == 2 and d["eviction_reentries"] == 0
    # and its own end is the only re-entry
    assert RT.supervise(now=t1 + 3600.0) == "EVICTION_STANDDOWN_ENDED"
    await asyncio.wait_for(ran.wait(), 2)
    assert RT._STATE["standdown"]["ended_by"] == RT.STANDDOWN_ENDED_BY_REENTRY
    assert RT._STATE["eviction_reentries"] == 1
    RT._STATE["task"].cancel()


async def test_a_record_of_another_runtime_is_never_this_owners_standdown(
        monkeypatch):
    """Whatever path leaves one behind: a stand-down opened for another
    runtime neither lets this owner re-enter early nor holds it to that
    runtime's clock, and the heartbeat never shows it as this one's."""
    o = _refused_owner()
    ran = _parked_run(monkeypatch, o)
    _install(monkeypatch, o, await _ended_task(), "rt-new")
    t0 = 4_000_000.0
    monkeypatch.setitem(RT._STATE, "standdown", {
        "refused": O.R_EVICTION_LOOP, "runtime_id": "rt-old",
        "since_mono": t0 - 1800.0, "seconds": 1800.0, "until_mono": t0,
        "since_at": 1.0, "until_at": 1801.0, "ordinal": 1,
        "ended_mono": None, "ended_at": None, "ended_by": None})
    monkeypatch.setitem(RT._STATE, "eviction_standdowns", 1)
    assert RT.digest()["eviction_standdown"] is None, "not this runtime's"
    assert RT.supervise(now=t0 + 1.0) is None, "not the old record's re-entry"
    assert not ran.is_set()
    sd = RT._STATE["standdown"]
    assert sd["runtime_id"] == "rt-new" and sd["since_mono"] == t0 + 1.0
    assert sd["seconds"] == 3600.0 and sd["ended_mono"] is None
    prior = RT._STATE["standdown_prior"]
    assert prior["runtime_id"] == "rt-old"
    assert prior["ended_by"] == RT.STANDDOWN_OF_ANOTHER_RUNTIME
    assert RT.digest()["eviction_standdown"]["runtime_id"] == "rt-new"


@pg
async def test_end_to_end_the_next_hold_stands_down_on_its_own(monkeypatch):
    """The real path: start_default, the heartbeat's own supervise, a real
    eviction loop, shutdown_default, start_default again in the same
    process, a second loop -- and no re-entry at the old record's end."""
    import asyncpg

    from tests.test_pinnapi_feed_runtime import WRITER_KEY
    conn = await H.connect()
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=4)
    try:
        await _set(conn, RT.CONTROL_KEY, True)
        monkeypatch.setenv("pinnapi_key", "k-test")
        monkeypatch.delenv("PINNAPI_FEED", raising=False)
        monkeypatch.setattr(O, "BACKOFF", (0.05,))
        monkeypatch.setattr(RT, "HEARTBEAT_S", 0.05)

        async def nothing(_pool):
            return {}
        monkeypatch.setattr(RT, "_census_once", nothing)
        monkeypatch.setattr(RT, "_discovery_once", nothing)
        socks = []

        async def connect(url, key):
            w = _no_frame_ws()
            socks.append(w)
            return w

        async def lf():
            return await O.Lease.open(H.DSN)

        async def hold():
            r = await RT.start_default(pool, writer_pid=None,
                                       writer_lock_key=WRITER_KEY,
                                       lease_factory=lf, connect=connect)
            assert r["state"] == "STARTED"
            return RT._STATE["runtime_id"]

        def ended_refused():
            o, t = RT._STATE.get("owner"), RT._STATE.get("task")
            return bool(o is not None and t is not None and t.done()
                        and o.refused == O.R_EVICTION_LOOP)

        rt1 = await hold()
        assert await until(lambda: RT._STATE.get("standdown") is not None,
                           timeout=15)
        assert len(socks) == O.EVICTIONS_MAX
        first = dict(RT._STATE["standdown"])
        assert first["seconds"] == 1800.0 and first["runtime_id"] == rt1

        # the writer hold ends and the next one begins, in the same process
        await RT.shutdown_default(wait_s=2.0)
        rt2 = await hold()
        assert rt2 != rt1
        assert await until(ended_refused, timeout=15)
        assert len(socks) == 2 * O.EVICTIONS_MAX
        await asyncio.sleep(0.3)                 # several heartbeat passes
        # past the OLD record's end: no re-entry, no further connection
        assert RT.supervise(now=first["until_mono"] + 1.0) is None
        await asyncio.sleep(0.3)
        assert len(socks) == 2 * O.EVICTIONS_MAX
        sd = dict(RT._STATE["standdown"])
        assert sd["runtime_id"] == rt2 and sd["ended_mono"] is None
        assert sd["seconds"] == 3600.0
        d = RT.digest()
        assert d["eviction_standdown"]["runtime_id"] == rt2
        assert d["eviction_standdown_prior_runtime"]["runtime_id"] == rt1
        assert d["eviction_standdown_prior_runtime"]["ended_by"] == (
            RT.STANDDOWN_CUT_SHORT_BY_SHUTDOWN)
        assert not RT._STATE["owner"].cache.authority.granted
    finally:
        await RT.shutdown_default(wait_s=2.0)
        await _set(conn, RT.CONTROL_KEY, None)
        await _set(conn, RT.HEARTBEAT_KEY, None)
        await pool.close()
        await conn.close()


# ── 5. nothing else moved ─────────────────────────────────────────────


def test_the_eviction_rule_and_the_liveness_bounds_are_unchanged():
    assert O.EVICTIONS_MAX == 3
    assert O.EVICTION_WINDOW_S == 600.0
    assert O.SILENCE_S == 75.0
    assert O.STOP_ON == ("unauthorized", "plan_lacks_ws")
    assert RT.STANDDOWN_ENV == STANDDOWN_ENV
    assert isinstance(RT.time, types.ModuleType)
