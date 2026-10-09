"""FEED_OWNERSHIP_NOT_HELD for the life of the process: an eviction loop stands down.

PRODUCTION (research-sql 37846538322, p0_first_loss_plumbing.sql, read
2026-10-08T21:24Z, release 69a8a07e). The API's PinnAPI owner read
EVICTION_LOOP_SUSPECTED / FEED_EVICTION_LOOP_SUSPECTED from 19:00:51Z: three
unrequested closes inside 600 s (~18:52Z, 18:55:40Z, 19:00:51Z), each after
minutes of healthy streaming, recorded as nothing but a count. No session
held the feed lease at 21:24Z and only ext_pinnacle_loop starts an owner,
so no holder of ours can have caused them. Being a REFUSAL, the owner was
never restarted: every 15-minute cycle since 19:09Z carried 85-104
FEED_OWNERSHIP_NOT_HELD rows (SOFTWARE), pinnapi_reactive_attempts
stopped, and only a process restart could bring the feed back.

  1. (the close's own facts ride on every UNREQUESTED_CLOSE:
     tests/test_rc6_pinnapi_close_facts.py)
  2. an EVICTION_LOOP refusal is a stand-down of a stated length (30 min,
     1 h, 2 h, then 4 h), after which the SAME owner re-enters once; the
     owner still stops at EVICTIONS_MAX inside a run, so a real second
     holder sees at most EVICTIONS_MAX connections per stand-down;
  3. authority stays revoked through the stand-down and the re-entry until
     the new epoch resynchronizes;
  4. every other refusal (writer lock lost, provider refusal, no key) stays
     permanent;
  5. A STAND-DOWN BELONGS TO THE RUNTIME THAT OPENED IT (review of
     9c0c1fe6). ext_pinnacle_loop runs shutdown_default and then
     start_default in the same process every time a writer hold ends
     (WRITER_LOCK_NOT_HELD, FENCE_UNANSWERED). shutdown_default reset the
     owner and the task but left an open stand-down in _STATE, so the NEXT
     owner's first eviction loop re-entered at once (or after the old
     record's remainder) instead of standing down: 2 x EVICTIONS_MAX
     connections back to back, and the new runtime's heartbeat showing the
     old owner's stand-down. Now shutdown closes the open record (labelled
     as cut short, kept visible as the prior runtime's), the next owner's
     loop opens a stand-down of its own, and the doubling carries across
     the hold change.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as RT
from sportsassets import pinnapi_owner as O

from tests import paper_harness as H
from tests.test_pinnapi_feed_ownership import frames_for, until
from tests.test_pinnapi_feed_runtime import Pool, _owner, _set
from tests.test_rc6_pinnapi_close_facts import ClosedWithoutFrameWS

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


@pytest.fixture(autouse=True)
def _isolated_standdown_state(monkeypatch):
    """Every stand-down key starts empty and is restored afterwards: the
    runtime state is module-global and the doubling is process-wide."""
    for k, v in (("standdown", None), ("standdown_prior", None),
                 ("eviction_standdowns", 0), ("eviction_reentries", 0),
                 ("runtime_id", None)):
        monkeypatch.setitem(RT._STATE, k, v)


# ── 2. the stand-down, and the one re-entry after it ─────────────────


async def _ended_task():
    async def done():
        return None
    t = asyncio.create_task(done())
    await t
    return t


def _refused_owner(monkeypatch, why=O.R_EVICTION_LOOP):
    o = O.FeedOwner(F.FeedCache(), sport_ids=[6], lease_factory=None,
                    connect=None)
    o.refused = why
    o.state = "EVICTION_LOOP_SUSPECTED"
    o.evictions = [1.0, 2.0, 3.0]
    o.cache.lost(why)
    return o


async def test_an_eviction_loop_stands_down_then_re_enters_once(monkeypatch):
    o = _refused_owner(monkeypatch)
    ran = asyncio.Event()

    async def fake_run():
        ran.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(o, "run", fake_run)
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "task", await _ended_task())
    monkeypatch.setitem(RT._STATE, "standdown", None)
    monkeypatch.setitem(RT._STATE, "eviction_reentries", 0)

    t0 = 1_000_000.0
    # the first pass that sees the refusal starts the stand-down, re-enters
    # nothing, and says so on the owner's own transition log
    assert RT.supervise(now=t0) is None
    sd = RT._STATE["standdown"]
    assert sd["seconds"] == 1800.0 and sd["until"] == t0 + 1800.0
    assert sd["reentry"] == 1 and sd["ended_at"] is None
    assert "EVICTION_STANDDOWN" in [e["what"] for e in o.events]
    assert RT.digest()["eviction_standdown"]["until"] == t0 + 1800.0
    # inside the stand-down: nothing, however often it is asked
    for dt in (30.0, 900.0, 1799.0):
        assert RT.supervise(now=t0 + dt) is None
    assert o.refused == O.R_EVICTION_LOOP and not ran.is_set()
    # the authority is revoked the whole time
    assert not o.cache.authority.granted

    assert RT.supervise(now=t0 + 1800.0) == "EVICTION_STANDDOWN_ENDED"
    await asyncio.wait_for(ran.wait(), 2)
    assert o.refused is None and o.evictions == [] and o.state == "STARTING"
    assert RT._STATE["eviction_reentries"] == 1
    assert RT._STATE["standdown"]["ended_at"] == t0 + 1800.0
    assert "EVICTION_STANDDOWN_ENDED" in [e["what"] for e in o.events]
    # still revoked: nothing is served until the new epoch resynchronizes
    assert not o.cache.authority.granted
    assert RT.supervise(now=t0 + 1830.0) is None      # running: nothing
    RT._STATE["task"].cancel()


async def test_each_later_loop_stands_down_longer_up_to_four_hours(
        monkeypatch):
    o = _refused_owner(monkeypatch)

    async def fake_run():
        await asyncio.Event().wait()
    monkeypatch.setattr(o, "run", fake_run)
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "standdown", None)
    monkeypatch.setitem(RT._STATE, "eviction_reentries", 0)
    now, seen = 5_000_000.0, []
    for _ in range(6):
        monkeypatch.setitem(RT._STATE, "task", await _ended_task())
        o.refused, o.state = O.R_EVICTION_LOOP, "EVICTION_LOOP_SUSPECTED"
        assert RT.supervise(now=now) is None
        seen.append(RT._STATE["standdown"]["seconds"])
        now = RT._STATE["standdown"]["until"]
        assert RT.supervise(now=now) == "EVICTION_STANDDOWN_ENDED"
        RT._STATE["task"].cancel()
    assert seen == [1800.0, 3600.0, 7200.0, 14400.0, 14400.0, 14400.0]
    assert RT._STATE["eviction_reentries"] == 6


@pytest.mark.parametrize("why", [O.R_WRITER_LOST, "unauthorized",
                                 "plan_lacks_ws",
                                 "KEY_NOT_PRESENT_IN_THIS_SERVICE"])
async def test_every_other_refusal_stays_permanent(monkeypatch, why):
    o = _refused_owner(monkeypatch, why)
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "task", await _ended_task())
    monkeypatch.setitem(RT._STATE, "standdown", None)
    for dt in (0.0, 1800.0, 10 ** 7):
        assert RT.supervise(now=1_000.0 + dt) is None
    assert o.refused == why and RT._STATE["standdown"] is None


async def test_a_stopped_owner_is_never_re_entered(monkeypatch):
    o = _refused_owner(monkeypatch)
    o.stop()
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "task", await _ended_task())
    monkeypatch.setitem(RT._STATE, "standdown", None)
    assert RT.supervise(now=1.0) is None
    assert RT.supervise(now=10 ** 7) is None
    assert RT._STATE["standdown"] is None


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
        o = _owner(cache, socks, pool, monkeypatch,
                   ws=lambda: ClosedWithoutFrameWS(frames_for()))
        first = asyncio.create_task(o.run())
        await asyncio.wait_for(asyncio.shield(first), 15)
        assert len(socks) == O.EVICTIONS_MAX
        monkeypatch.setitem(RT._STATE, "owner", o)
        monkeypatch.setitem(RT._STATE, "task", first)
        monkeypatch.setitem(RT._STATE, "standdown", None)
        monkeypatch.setitem(RT._STATE, "eviction_reentries", 0)

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


# ── 5. a hold change: the stand-down belongs to the runtime that opened it ─


class _TerminalPool:
    """Takes shutdown_default's terminal heartbeat and nothing else."""

    def acquire(self):
        class Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a):
                return "UPDATE 0"
        return Ctx()


def _install(monkeypatch, o, task, runtime_id):
    """What start_default installs for a new hold's owner, minus the socket
    and the background loops."""
    for k, v in (("owner", o), ("task", task), ("runtime_id", runtime_id),
                 ("pool", _TerminalPool()), ("beat", None), ("held", None)):
        monkeypatch.setitem(RT._STATE, k, v)


def _parked_run(monkeypatch, o):
    ran = asyncio.Event()

    async def fake_run():
        ran.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(o, "run", fake_run)
    return ran


async def test_a_hold_change_cuts_the_standdown_short_and_the_next_owner_stands_down_on_its_own(  # noqa: E501
        monkeypatch):
    o1 = _refused_owner(monkeypatch)
    _parked_run(monkeypatch, o1)
    _install(monkeypatch, o1, await _ended_task(), "rt-1")
    t0 = 3_000_000.0
    assert RT.supervise(now=t0) is None
    first = dict(RT._STATE["standdown"])
    assert first["seconds"] == 1800.0 and first["ended_at"] is None

    # the writer hold ends: ext_pinnacle_loop's `finally` runs shutdown
    before = time.time()
    out = await RT.shutdown_default(wait_s=0.1)
    assert out["verdict"] == "CLOSED"
    # THE DEFECT: the open record outlived its runtime
    assert RT._STATE.get("standdown") is None
    prior = RT._STATE["standdown_prior"]
    assert prior["runtime_id"] == "rt-1"
    assert prior["ended_by"] == RT.STANDDOWN_CUT_SHORT_BY_SHUTDOWN
    assert prior["ended_at"] >= before and prior["until"] == first["until"]
    assert RT._STATE["eviction_standdowns"] == 1
    assert RT._STATE["eviction_reentries"] == 0      # nothing re-entered

    # the next hold's owner (start_default: a new runtime) loops again,
    # later than the cut-short record's end
    o2 = _refused_owner(monkeypatch)
    ran = _parked_run(monkeypatch, o2)
    _install(monkeypatch, o2, await _ended_task(), "rt-2")
    t1 = first["until"] + 200.0
    assert RT.supervise(now=t1) is None, "a NEW stand-down, not a re-entry"
    sd = RT._STATE["standdown"]
    # its own record; and the doubling carried across the hold change, since
    # the cut-short stand-down ended in a return to the provider all the same
    assert sd["runtime_id"] == "rt-2" and sd["ended_at"] is None
    assert sd["since"] == t1 and sd["seconds"] == 3600.0
    assert sd["until"] == t1 + 3600.0 and sd["reentry"] == 2
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
    o = _refused_owner(monkeypatch)
    ran = _parked_run(monkeypatch, o)
    _install(monkeypatch, o, await _ended_task(), "rt-new")
    t0 = 4_000_000.0
    monkeypatch.setitem(RT._STATE, "standdown", {
        "refused": O.R_EVICTION_LOOP, "runtime_id": "rt-old",
        "since": t0 - 1800.0, "seconds": 1800.0, "until": t0,
        "reentry": 1, "ended_at": None, "ended_by": None})
    monkeypatch.setitem(RT._STATE, "eviction_standdowns", 1)
    assert RT.supervise(now=t0 + 1.0) is None, "not the old record's re-entry"
    assert not ran.is_set()
    sd = RT._STATE["standdown"]
    assert sd["runtime_id"] == "rt-new" and sd["since"] == t0 + 1.0
    assert sd["seconds"] == 3600.0 and sd["ended_at"] is None
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
            w = ClosedWithoutFrameWS(frames_for())
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
        assert first["seconds"] == 1800.0

        # the writer hold ends and the next one begins, in the same process
        await RT.shutdown_default(wait_s=2.0)
        rt2 = await hold()
        assert rt2 != rt1
        assert await until(ended_refused, timeout=15)
        assert len(socks) == 2 * O.EVICTIONS_MAX
        await asyncio.sleep(0.3)                 # several heartbeat passes
        # past the OLD record's end: no re-entry, no further connection
        assert RT.supervise(now=first["until"] + 1.0) is None
        await asyncio.sleep(0.3)
        assert len(socks) == 2 * O.EVICTIONS_MAX
        sd = dict(RT._STATE["standdown"])
        assert sd["runtime_id"] == rt2 and sd["ended_at"] is None
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
