"""THE REFRESH BUDGET IS DELIVERED AND SHARED FAIRLY (RC6 lane D1, delivery;
market_plane.active_refresh + workers.universal_market_plane).

Production (research-sql run 37870039455, 2026-10-09 01:29Z): the plane's
SNAPSHOT events -- one per pass at most -- arrived p50 92 s / p90 248 s /
p99 739 s apart over 24 h, every one of the newest 40 more than 197 s
apart. RC6 made one book read per pass, so its 12-a-minute budget was about
0.25 a minute there; and it ordered candidates by event start before age,
so with more quiet members than the budget holds (12 x 285 s / 60 = 57)
the later starts were never read.

  §1  fair order: earliest lapse first inside a tier, HELD first; every
      quiet member is read when the budget cannot hold them all (on
      412c4962 the later starts are never read)
  §2  two drivers, one budget: the pass and the freshness task never pass
      one slot, never read one member twice, never overlap a client call
  §3  the REAL run loop with a slow pass: the freshness task keeps reading
      while coverage runs (on 412c4962 one read a pass)
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import pathlib
import threading
import time

import pytest

from sportsassets import institutional_stream as IS
from sportsassets.market_plane import active_refresh as AR
from sportsassets.market_plane import populate as POP
from sportsassets.workers import universal_market_plane as W

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
HERE = pathlib.Path(__file__).resolve().parent
BOUND = W.FRESH_SLA_S


def _rc6():
    spec = importlib.util.spec_from_file_location(
        "_rc6_refresh_helpers", HERE / "test_rc6_priority_active_refresh.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = _rc6()
T0 = H.T0


def run(coro):
    return asyncio.run(coro)


# ═════════════════════════════════════════════════════════════════════
# §1 fair order
# ═════════════════════════════════════════════════════════════════════

def test_inside_a_tier_the_earliest_lapse_goes_first_held_still_first():
    clock = H.Clock(T0)
    syms = ["held-late-lapse", "c-lapsed-long-ago", "c-lapsed-recently",
            "c-never-current", "c-refreshed-near-bound"]
    m, books = H.plane(clock, syms)
    H.update(books, "held-late-lapse", T0)
    H.update(books, "c-lapsed-long-ago", T0 - 3000)
    H.update(books, "c-lapsed-recently", T0)
    H.update(books, "c-refreshed-near-bound", T0 - 3000)
    clock.t = T0 + 301.0
    books.on_heartbeat()
    ref = AR.ActiveRefresh()
    ref.set_members([H.member("held-late-lapse", 0, T0 + 9000),
                     H.member("c-lapsed-long-ago", 10, T0 + 9000),
                     H.member("c-lapsed-recently", 10, T0 + 600),
                     H.member("c-never-current", 10, T0 + 60),
                     H.member("c-refreshed-near-bound", 10, T0 + 60)],
                    now=clock.t)
    ref.record("c-refreshed-near-bound", H.rest_book(
        "c-refreshed-near-bound"), at=clock.t - 290.0)
    due, _c = ref.plan(m, now=clock.t, bound=BOUND)
    # c-never-current has no book at all on this connection: it lapsed
    # first of all; the near-bound refresh lapses in 10 s, last
    assert due == ["held-late-lapse", "c-never-current", "c-lapsed-long-ago",
                   "c-lapsed-recently", "c-refreshed-near-bound"]


def _ten_minutes(n_members, minutes, pass_s=2.0):
    clock = H.Clock(T0)
    syms = ["quiet-%02d" % i for i in range(n_members)]
    m, books = H.plane(clock, syms)
    for s in syms:
        H.update(books, s, T0)
    clock.t = T0 + 400.0
    # ascending event starts: RC6's order read the soonest first
    pool = H._Pool([H.member(s, 10, T0 + 3600 + 60 * i)
                    for i, s in enumerate(syms)])
    ref = AR.ActiveRefresh()
    per, starts = {}, []
    fresh_s = {s: 0.0 for s in syms}

    def read(s):
        starts.append(clock.t)
        per.setdefault(s, []).append(clock.t)
        return H.rest_book(s)
    end = clock.t + 60.0 * minutes
    while clock.t < end:
        books.on_heartbeat()
        run(AR.step(pool, None, ref, m, bound=BOUND, clock=clock, read=read))
        for s in ref.current(m, now=clock.t, bound=BOUND):
            fresh_s[s] += pass_s
        clock.t += pass_s
    return syms, per, starts, fresh_s


def test_every_quiet_member_is_read_when_the_budget_cannot_hold_them_all():
    """80 quiet members, 20 minutes, the budget saturated (12 a minute):
    every member is read and keeps a share of the current time. On
    412c4962 the soonest 57 were re-read every 285 s and the 23 later
    starts were never read at all."""
    syms, per, starts, fresh_s = _ten_minutes(80, 20)
    for i, s in enumerate(starts):
        assert sum(1 for x in starts[i:] if x - s < 60.0) <= 12
    assert len(starts) >= 239                  # the whole budget, no more
    never = [s for s in syms if s not in per]
    assert never == [], never
    # the same reads spread over every member: none starved
    shares = sorted(v / (20 * 60.0) for v in fresh_s.values())
    assert shares[0] >= 0.30, shares[:5]
    # still never re-read before its refresh nears the bound
    for s, ts in per.items():
        assert all(b - a >= BOUND - AR.REFRESH_LEAD_S
                   for a, b in zip(ts, ts[1:])), (s, ts)


def test_with_enough_budget_every_quiet_member_is_kept_current():
    syms, per, starts, fresh_s = _ten_minutes(40, 12)
    # after the first sweep (40 reads at 12 a minute) all 40 stay current
    tail = [v for v in fresh_s.values()]
    assert min(tail) >= (12 - 4) * 60.0 - 10.0, sorted(tail)[:5]


# ═════════════════════════════════════════════════════════════════════
# §2 two drivers, one budget, one client
# ═════════════════════════════════════════════════════════════════════

def test_two_drivers_never_pass_one_slot_or_read_one_member_twice():
    clock = H.Clock(T0)
    syms = ["m-%02d" % i for i in range(30)]
    m, books = H.plane(clock, syms)
    for s in syms:
        H.update(books, s, T0)
    clock.t = T0 + 400.0
    books.on_heartbeat()
    pool = H._Pool([H.member(s, 10, T0 + 3600 + i)
                    for i, s in enumerate(syms)])
    ref = AR.ActiveRefresh()
    reads, active, overlap = [], [0], []
    gate = threading.Lock()

    def read(s):
        with gate:
            active[0] += 1
            if active[0] > 1:
                overlap.append(s)
        time.sleep(0.01)
        with gate:
            active[0] -= 1
        reads.append(s)
        return H.rest_book(s)

    async def driver(lock):
        for _ in range(40):
            await AR.step(pool, None, ref, m, bound=BOUND, clock=clock,
                          read=read, lock=lock)
            clock.t += 0.6
            await asyncio.sleep(0)

    async def both():
        lock = asyncio.Lock()
        await asyncio.gather(driver(lock), driver(lock))
    run(both())
    assert overlap == []                       # one client call at a time
    assert len(reads) == len(set(reads))       # no member read twice
    assert len(reads) <= 12 * (1 + int((clock.t - (T0 + 400.0)) // 60.0))
    assert ref.inflight == set()


def test_a_cancelled_read_leaves_nothing_in_flight():
    clock = H.Clock(T0)
    m, books = H.plane(clock, ["only"])
    H.update(books, "only", T0)
    clock.t = T0 + 400.0
    books.on_heartbeat()
    pool = H._Pool([H.member("only", 10, T0 + 3600)])
    ref = AR.ActiveRefresh()
    started = threading.Event()

    def read(s):
        started.set()
        time.sleep(0.3)
        return H.rest_book(s)

    async def go():
        t = asyncio.ensure_future(AR.step(pool, None, ref, m, bound=BOUND,
                                          clock=clock, read=read,
                                          lock=asyncio.Lock()))
        while not started.is_set():
            await asyncio.sleep(0.01)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    run(go())
    assert ref.inflight == set()
    due, _c = ref.plan(m, now=clock.t, bound=BOUND)
    assert due == ["only"]


# ═════════════════════════════════════════════════════════════════════
# §3 the REAL run loop with a slow pass
# ═════════════════════════════════════════════════════════════════════

QUIET = ["atc-rc6d1-quiet-%d-2026-10-10-q" % i for i in range(6)]


@pg
def test_the_freshness_task_keeps_reading_while_a_slow_pass_runs(
        monkeypatch):
    """The plane's REAL run loop on a rolled-back connection (a pool of one:
    acquire waits for release), six quiet candidates, the coverage pass
    taking 1.2 s (production's took minutes). The first snapshot counts all
    six current through the refresh: the freshness task read them while
    coverage ran. On 412c4962 the pass made one read, and the snapshot
    counted one."""
    import asyncpg
    from sportsassets import pmx_institutional as PMX
    from sportsassets.market_plane import sharded_stream as SS
    H._Client.reads = []
    snaps, beats = [], []

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        one = asyncio.Lock()

        class Pool:
            def acquire(self):
                class A:
                    async def __aenter__(self_):
                        await one.acquire()
                        return c

                    async def __aexit__(self_, *a):
                        one.release()
                        return False
                return A()

        async def get_pool():
            return Pool()

        async def hb(service, status="ok", detail=None, con=None):
            beats.append((service, status, detail))
            if len(beats) >= 1:
                raise asyncio.CancelledError()
        real_snapshot = W.snapshot

        async def snapshot(*a, **kw):
            s = await real_snapshot(*a, **kw)
            snaps.append(json.loads(json.dumps(s, default=str)))
            return s
        real_cov = POP.coverage_pass

        async def slow_coverage(*a, **kw):
            await asyncio.sleep(1.2)
            return await real_cov(*a, **kw)

        async def required(conn):
            return set(), set(QUIET), True
        monkeypatch.setattr(W, "get_pool", get_pool)
        monkeypatch.setattr(W, "heartbeat", hb)
        monkeypatch.setattr(W, "snapshot", snapshot)
        monkeypatch.setattr(W, "INTERVAL_S", 0.0)
        # (absent on 412c4962: the base fails on its behaviour below)
        monkeypatch.setattr(W, "FRESHNESS_TICK_S", 0.02, raising=False)
        monkeypatch.setattr(AR, "MIN_GAP_S", 0.05)
        monkeypatch.setattr(POP, "coverage_pass", slow_coverage)
        monkeypatch.setattr(W, "stream_arming",
                            lambda *a, **k: {"armed": True, "why": None})
        monkeypatch.setattr(W, "TokenKeeper", H._Keeper)
        monkeypatch.setattr(PMX, "Institutional", H._Client)
        monkeypatch.setattr(W, "Manager", lambda **kw: SS.Manager(
            transport_factory=lambda b, tok, **k: H._QuietTransport(
                b, tok, **k), **kw))
        monkeypatch.setattr(POP, "required_sets_read", required)
        monkeypatch.setenv("KALSHI_CATALOGUE", "off")
        monkeypatch.delenv("UMP_ACTIVE_REFRESH", raising=False)
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await c.execute("DELETE FROM us_premap")
            for s in QUIET:
                await c.execute(
                    "INSERT INTO market_plane_registry (contract_id, venue, "
                    " active, desired_subscription, updated_at, priority, "
                    " required_reason, event_start, last_seen_at, refdata) "
                    "VALUES ($1, 'POLYMARKET_US', true, true, now(), 10, "
                    " 'EVALUATED_CANDIDATE', now() + interval '20 hours', "
                    " now(), $2::jsonb) ON CONFLICT (contract_id) DO UPDATE "
                    " SET active = true, priority = 10, "
                    " refdata = excluded.refdata",
                    s, json.dumps({"symbol": s, "priceScale": "1000",
                                   "fractionalQtyScale": "100",
                                   "state": H.OPEN}))
            with pytest.raises(asyncio.CancelledError):
                await W.run()
        finally:
            await tr.rollback()
            await c.close()
    run(go())
    assert beats and beats[0][1] != "error", beats[0]
    cen = snaps[0]["freshness"]["priority_universe"]["census"]
    assert cen["members"] == 6
    assert cen["current_via_refresh"] == 6, cen
    assert cen["not_current"] == 0
    # the snapshot names the instant it verified (after the slow coverage
    # pass), and the coverage tiers keep their own, earlier one
    pu = snaps[0]["freshness"]["priority_universe"]
    assert snaps[0]["computed_at"] - pu["verified_at"] >= 1.2
    assert pu["verified_age_s"] >= 1.2
    assert sorted(s for _n, s in H._Client.reads) == sorted(QUIET)
    assert all(n == "book" for n, _s in H._Client.reads)
    # the task said what it did
    ft = beats[0][2]["freshness_task"]
    assert ft["ticks"] >= 1 and ft["errors"] == 0
