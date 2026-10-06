"""P0 FIRST-LOSS PLUMBING: the three SOFTWARE codes ours to fix.

FEED_OWNERSHIP_NOT_HELD (38 provider events' first loss, 24 h census).
  Production 2026-10-06 (research-sql run 37411912022): from 01:29:56Z the
  deciding process had no PinnAPI authority for 2.5 h. Heartbeat state
  STARTING, epoch 0, transitions LEASE_ACQUIRED -> FEED_GUARD_CHECK_TIMED_OUT
  and nothing after; pg_locks: the feed lease still granted to the
  `pinnapi-feed-owner` session opened at 01:29:50, idle; 94-102
  FEED_OWNERSHIP_NOT_HELD rows/h (0 before); no reactive attempt after 01:59Z.
  Mechanism (reproduced below against a real Postgres): a guard timeout
  leaves asyncpg's CancelRequest unanswered; the teardown's close() awaits
  the cancelled future, raises a CancelledError that is not the task's own,
  `_abort()` is a no-op because `closing` is already set -- the session and
  its lock survive -- and the CancelledError ends the owner task for good.
  Plus the first cycle of every hold ran before the feed had synced
  (2026-10-05 09:16 / 18:16 / 18:21Z, 5-6 events each).

QUOTE_STALE_ON_ARRIVAL (45 NCAAF events' first loss). research-sql run
  37412834338: every event of an NCAAF fetch carries the same provider
  last_update (19.5-27 s old at receipt), the event at queue position 0/1
  takes the one paced venue read (10-15 s) and every later one is refused
  at lag + 11-25 s; 312 of 421 such rows had a provider lag inside 30 s.
  And the order was the SAME every fetch (equal last_update -> event id),
  so the same events took the only usable slot in all 29 fetches.

No freshness limit, clock, pacing, cap or gate is read or moved here.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from sportsassets import collector_coverage as cov
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as RT
from sportsassets import pinnapi_owner as O
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


async def until(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return False


async def _feed_lease_holders():
    import asyncpg
    c = await asyncpg.connect(DSN)
    try:
        return [r["pid"] for r in await c.fetch(
            "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted"
            "   AND ((classid::bigint << 32) | objid::bigint) = $1",
            O.FEED_LOCK_KEY)]
    finally:
        await c.close()


# ═════════════════════════════════════════════════════════════════════
# 1 · THE WEDGED OWNER, REPRODUCED AND REPAIRED
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_guard_timeout_with_an_unanswered_cancel_no_longer_kills_the_owner(
        monkeypatch):
    """The production sequence: the first guard check times out and
    asyncpg's CancelRequest is never answered. Before: the owner task ended
    (CancelledError), state stayed STARTING and the lease session kept the
    lock for ever. Now: the in-doubt session is discarded, the lock is
    freed, the owner contends again, re-acquires and syncs."""
    import asyncpg.connect_utils as CU
    from tests.test_pinnapi_feed_ownership import FakeWS, frames_for

    async def unanswered_cancel(**kw):
        await asyncio.Event().wait()
    monkeypatch.setattr(CU, "_cancel", unanswered_cancel)
    monkeypatch.setenv("pinnapi_key", "k-test")
    calls = {"holds": 0}

    class SlowFirstCheck(O.Lease):
        async def holds(self):
            calls["holds"] += 1
            if calls["holds"] == 1:
                # longer than liveness_s: the guard times out mid-query
                return bool(await self.conn.fetchval(
                    "SELECT pg_sleep(0.6) IS NOT NULL"))
            return await super().holds()

    async def lease_factory():
        return await SlowFirstCheck.open(DSN)

    sockets = []

    async def connect(url, key):
        ws = FakeWS(frames_for())
        sockets.append(ws)
        return ws

    o = O.FeedOwner(F.FeedCache(), sport_ids=[6], lease_factory=lease_factory,
                    connect=connect, liveness_s=0.3, standby_s=0.1)
    t = asyncio.create_task(o.run())
    try:
        assert await until(lambda: o.state == "OWNER_SYNCED", 15.0), (
            o.state, [e["what"] for e in o.events], t.done())
        whats = [e["what"] for e in o.events]
        assert whats[:3] == ["LEASE_ACQUIRED", O.R_GUARD_TIMEOUT,
                             "LEASE_DISCARDED"], whats
        assert whats.count("LEASE_ACQUIRED") >= 2
        assert not t.done()
        # exactly one session holds the feed lease: the live owner's
        assert len(await _feed_lease_holders()) == 1
    finally:
        o.stop()
        await asyncio.wait_for(t, 15)
    assert await until(lambda: True)
    assert await _feed_lease_holders() == []


class _Tr:
    def __init__(self):
        self.aborted = False

    def is_closing(self):
        return self.aborted

    def abort(self):
        self.aborted = True


class _WedgedConn:
    """A connection whose graceful close raises a CancelledError that is
    not the task's (asyncpg awaiting a cancelled CancelRequest future) and
    whose terminate() is a no-op (the protocol's `closing` already set)."""

    def __init__(self):
        self._transport = _Tr()
        self.terminated = 0

    async def execute(self, *a):
        raise TimeoutError("cancel still pending")

    async def close(self):
        fut = asyncio.get_running_loop().create_future()
        fut.cancel()
        await fut

    def terminate(self):
        self.terminated += 1


async def test_a_graceful_close_that_raises_a_stray_cancellation_is_discarded():
    lease = O.Lease(_WedgedConn())
    await lease.release()                   # a failed unlock: session in doubt
    assert lease.in_doubt
    await lease.close()                     # never raises; discards
    assert lease.discarded and lease.conn._transport.aborted


async def test_even_without_doubt_a_stray_cancellation_on_close_is_contained():
    lease = O.Lease(_WedgedConn())
    await lease.close()
    assert lease.discarded and lease.conn._transport.aborted


async def test_the_tasks_own_cancellation_still_propagates_after_discarding():
    gate = asyncio.Event()

    class Hanging(_WedgedConn):
        async def close(self):
            await gate.wait()
    lease = O.Lease(Hanging())
    t = asyncio.create_task(lease.close())
    await asyncio.sleep(0.01)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert lease.discarded and lease.conn._transport.aborted


async def test_a_stray_cancellation_inside_a_hold_is_an_owner_error_not_an_exit(
        monkeypatch):
    monkeypatch.setenv("pinnapi_key", "k-test")
    retired = []

    class L:
        in_doubt = False

        async def try_acquire(self):
            return True

        async def holds(self):
            return True

        async def release(self):
            retired.append("release")

        async def close(self):
            retired.append("close")

    async def lf():
        return L()

    async def connect(url, key):
        fut = asyncio.get_running_loop().create_future()
        fut.cancel()
        await fut                            # a driver future cancelled under us

    o = O.FeedOwner(F.FeedCache(), sport_ids=[6], lease_factory=lf,
                    connect=connect, liveness_s=0.2, standby_s=0.05)
    t = asyncio.create_task(o.run())
    try:
        assert await until(lambda: [e["what"] for e in o.events].count(
            O.R_STRAY_CANCELLATION) >= 2, 10.0)
        assert not t.done()
        assert o.cache.read(1, "s;0;m")["ok"] is False
    finally:
        o.stop()
        await asyncio.wait_for(t, 10)


# ═════════════════════════════════════════════════════════════════════
# 2 · AN OWNER TASK THAT ENDED BY ACCIDENT IS RESTARTED
# ═════════════════════════════════════════════════════════════════════

class _FakeLease:
    def __init__(self):
        self.discarded = False

    def discard(self):
        self.discarded = True


async def _ended_task():
    async def die():
        raise asyncio.CancelledError
    t = asyncio.create_task(die())
    try:
        await t
    except asyncio.CancelledError:
        pass
    return t


async def test_supervise_restarts_an_owner_task_that_ended_and_frees_its_lease(
        monkeypatch):
    o = O.FeedOwner(F.FeedCache(), sport_ids=[6], lease_factory=None,
                    connect=None)
    ran = asyncio.Event()

    async def fake_run():
        ran.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(o, "run", fake_run)
    leaked = _FakeLease()
    o.lease = leaked
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "task", await _ended_task())
    monkeypatch.setitem(RT._STATE, "restarts", 0)
    assert RT.owner_task_state()["state"] == "ENDED"
    assert RT.supervise() == "CANCELLED"
    assert leaked.discarded and o.lease is None
    await asyncio.wait_for(ran.wait(), 2)
    assert RT._STATE["restarts"] == 1
    assert RT.owner_task_state()["state"] == "RUNNING"
    assert "OWNER_TASK_RESTARTED" in [e["what"] for e in o.events]
    assert RT.supervise() is None            # running: nothing to do
    RT._STATE["task"].cancel()


@pytest.mark.parametrize("why", ["refused", "stopped"])
async def test_supervise_never_restarts_a_deliberate_stop(monkeypatch, why):
    o = O.FeedOwner(F.FeedCache(), sport_ids=[6], lease_factory=None,
                    connect=None)
    if why == "refused":
        o.refused = O.R_WRITER_LOST
    else:
        o.stop()
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "task", await _ended_task())
    assert RT.supervise() is None


async def test_shutdown_with_an_ended_owner_task_does_not_raise(monkeypatch):
    """The decider's teardown awaited the owner task through shield(): a
    task that had ended cancelled re-raised CancelledError into
    ext_pinnacle_loop's `finally`, past its `except Exception`."""
    o = O.FeedOwner(F.FeedCache(), sport_ids=[6], lease_factory=None,
                    connect=None)
    leaked = _FakeLease()
    o.lease = leaked

    class NoPool:
        def acquire(self):
            raise ConnectionError("no database in this test")
    for k, v in (("owner", o), ("task", await _ended_task()), ("beat", None),
                 ("held", None), ("pool", NoPool())):
        monkeypatch.setitem(RT._STATE, k, v)
    out = await RT.shutdown_default(wait_s=0.5)
    assert out["verdict"] == "CLOSED_WITH_ERROR"
    assert leaked.discarded


# ═════════════════════════════════════════════════════════════════════
# 3 · THE FIRST CYCLE OF A HOLD WAITS (BOUNDED) FOR THE FEED TO SYNC
# ═════════════════════════════════════════════════════════════════════

async def test_wait_synced_returns_by_name(monkeypatch):
    monkeypatch.setitem(RT._STATE, "owner", None)
    assert await RT.wait_synced(1.0) == "NOT_STARTED"
    c = F.FeedCache()
    o = O.FeedOwner(c, sport_ids=[6], lease_factory=None, connect=None)
    alive = asyncio.create_task(asyncio.Event().wait())
    monkeypatch.setitem(RT._STATE, "owner", o)
    monkeypatch.setitem(RT._STATE, "task", alive)
    try:
        o.state = "DISARMED"
        assert await RT.wait_synced(1.0) == "NOT_CONTENDING:DISARMED"
        o.state = "RESYNCHRONIZING"
        assert (await RT.wait_synced(0.1)).startswith("TIMEOUT_AFTER_")

        async def sync_soon():
            await asyncio.sleep(0.1)
            ep = c.new_connection([("live", 6)])
            c.apply({"type": "snapshot", "stream": "live", "sport_id": 6,
                     "ts": 1, "events": []}, epoch=ep)
        asyncio.create_task(sync_soon())
        assert await RT.wait_synced(5.0) == "SYNCED"
    finally:
        alive.cancel()
    monkeypatch.setitem(RT._STATE, "task", await _ended_task())
    o.cache = F.FeedCache()
    assert await RT.wait_synced(1.0) == "OWNER_TASK_ENDED"
    assert RT.FIRST_SYNC_WAIT_S == 60.0


def test_the_hold_waits_for_the_feed_before_its_first_cycle():
    import inspect
    src = inspect.getsource(loop._hold_once)
    assert src.index("_feed.wait_synced()") < src.index("_reactive.start(")
    assert src.index("start_default(pool") < src.index("_feed.wait_synced()")


# ═════════════════════════════════════════════════════════════════════
# 4 · STALE BY OUR QUEUE -> FIRST IN THE NEXT ONE
# ═════════════════════════════════════════════════════════════════════

def test_a_requeued_event_is_ordered_first_oldest_first():
    now = 1_000_000.0
    owed = {"b": now - 50.0, "c": now - 100.0}
    ids = ["a", "b", "c", "d"]
    order = sorted(ids, key=lambda i: cov.candidate_order_key(
        i, commence_epoch=now + 86400, now=now, deferred_since=owed,
        freshness_key=()))
    assert order == ["c", "b", "a", "d"]


def test_the_rule_moves_no_limit():
    assert loop.PINNACLE_MAX_AGE_S == 30.0
    assert loop.ADAPTIVE_ODDS_REFETCH_MAX_PER_SPORT == 0
    assert "no limit" in loop.REQUEUE_AFTER_OUR_DELAY_RULE


@pg
async def test_a_quote_our_queue_made_stale_is_requeued_and_judged_next(
        monkeypatch):
    """Through the real cycle: received 40 s ago with a 5 s provider lag
    (our delay) -> refused QUOTE_STALE_ON_ARRIVAL by name AND owed the next
    fetch's first slot; a fresh quote next cycle is judged (the 30 s rule
    admits it on its own terms) and the entry leaves the store."""
    from tests import test_quote_stale_on_arrival_is_not_self_inflicted as Q
    monkeypatch.setitem(loop._COVERAGE, "requeued_after_our_delay", {})
    conn, Fx, venue, out, calls, handed = await Q._run(
        monkeypatch, [(-40.0, -45.0)])
    try:
        assert out["refusals"].get(loop.R_QUOTE_STALE_ON_ARRIVAL) == 1
        assert out["latency"]["stale_on_arrival_due_to_our_processing"] == 1
        assert out["latency"]["requeued_after_our_delay"] == 1
        store = loop._COVERAGE["requeued_after_our_delay"]["baseball_mlb"]
        assert len(store) == 1
        await Fx.clean(conn)
    finally:
        await conn.close()
    conn, Fx, venue, out, calls, handed = await Q._run(
        monkeypatch, [(0.0, -2.0)])
    try:
        assert loop.R_QUOTE_STALE_ON_ARRIVAL not in out["refusals"]
        assert loop._COVERAGE["requeued_after_our_delay"]["baseball_mlb"] == {}
        assert out["latency"]["requeued_after_our_delay"] == 0
    finally:
        await Fx.clean(conn)
        await conn.close()


@pg
async def test_a_quote_the_provider_delivered_stale_is_not_requeued(
        monkeypatch):
    from tests import test_quote_stale_on_arrival_is_not_self_inflicted as Q
    monkeypatch.setitem(loop._COVERAGE, "requeued_after_our_delay", {})
    conn, Fx, venue, out, calls, handed = await Q._run(
        monkeypatch, [(0.0, -45.0)])
    try:
        assert out["refusals"].get(loop.R_QUOTE_STALE_ON_ARRIVAL) == 1
        assert out["latency"]["provider_stale_on_arrival"] == 1
        assert out["latency"]["requeued_after_our_delay"] == 0
        assert not loop._COVERAGE["requeued_after_our_delay"].get(
            "baseball_mlb")
    finally:
        await Fx.clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · DISCOVERY CHANGES NEWEST FIRST (PINNACLE_PROBABILITY_NOT_QUALIFIED_
#     BY_THE_LANE: FEED_QUOTE_OLDER_THAN_LIMIT after a 22-25 s queue wait)
# ═════════════════════════════════════════════════════════════════════

def _discovery_scheduler(now):
    from sportsassets import pinnapi_held as PH
    from sportsassets import pinnapi_reactive as RX

    class Cache:
        def read(self, eid, key, evaluated_ms=None, max_age_s=30.0):
            return {"ok": True}

    async def noop(*a, **k):
        return None
    s = RX.Scheduler(Cache(), noop, noop, clock=lambda: now[0], queue_cap=3,
                     held=PH.HeldWatch())
    for eid in range(1, 8):
        s.seeds[eid] = {"event": {}, "sport_key": "baseball_npb",
                        "registered_at": now[0]}
    return s


def _q(eid, at, price=-150):
    return F.Quote(key=F.FULL_GAME_MONEYLINE_KEY, event_id=eid, sport_id=6,
                   stream="prematch", period=0, market_type="moneyline",
                   side=None, line=None, prices={"home": price, "away": 135},
                   epoch=1, source_change_ms=at * 1000, frame_ts_ms=at * 1000,
                   received_ms=at * 1000 + 100)


def test_the_newest_discovery_change_is_evaluated_first():
    """The measured shape: changes queued while the single worker is busy.
    Oldest-first started every one a queue-wait old; newest-first starts
    the newest at once, and the cap still evicts the oldest."""
    from sportsassets import pinnapi_reactive as RX
    now = [1000.0]
    s = _discovery_scheduler(now)
    for eid in (1, 2, 3):
        now[0] += 1.0
        s.changed(_q(eid, now[0]))
    assert s.next_job()[0] == 3
    # a coalesced change is that fixture's newest: it moves to the front
    now[0] += 1.0
    s.changed(_q(1, now[0], price=-160))
    assert s.counts["COALESCED"] == 1
    eid, tick = s.next_job()
    assert eid == 1 and tick["queued_at"] == now[0]
    assert s.next_job()[0] == 2
    assert s.next_job() is None
    # the cap still evicts the OLDEST entry
    for eid in (4, 5, 6, 7):
        now[0] += 1.0
        s.changed(_q(eid, now[0]))
    assert s.counts["QUEUE_EVICTED"] == 1 and 4 not in s.pending
    assert [s.next_job()[0] for _ in range(3)] == [7, 6, 5]
    assert "unchanged 30 s" in RX.DISCOVERY_NEWEST_FIRST_RULE


def test_held_changes_keep_their_own_first_served_order():
    from sportsassets import pinnapi_held as PH
    now = [1000.0]
    w = PH.HeldWatch()
    w.set_targets({"a": (5, None), "b": (6, None)})
    s = _discovery_scheduler(now)
    s.held = w
    s.changed(_q(5, now[0]))
    s.changed(_q(6, now[0] + 1))
    s.changed(_q(1, now[0] + 2))
    assert [s.next_job()[0] for _ in range(3)] == [5, 6, 1]
