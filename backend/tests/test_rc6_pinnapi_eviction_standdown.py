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
     permanent.
"""
from __future__ import annotations

import asyncio

import pytest

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as RT
from sportsassets import pinnapi_owner as O

from tests import paper_harness as H
from tests.test_pinnapi_feed_ownership import frames_for
from tests.test_pinnapi_feed_runtime import Pool, _owner, _set
from tests.test_rc6_pinnapi_close_facts import ClosedWithoutFrameWS

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


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
