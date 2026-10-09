"""RC6 api-responsive: the PinnAPI subscribe snapshot is built off the API
event loop and swapped in whole.

PRODUCTION. The owner (pinnapi_owner._own) calls `cache.apply(msg)` inline
after each recv, so a snapshot frame -- a whole (stream, sport): 1,291 soccer
prematch events on 2026-10-01, a cache of 31,849 markets on 2026-10-04,
sized for 45,136 -- was applied in ONE synchronous call on the API loop at
every connect and resync. The API loop watchdog recorded that frame holding
the loop 2.1 s and 2.2 s (`_replace_event <- _apply <- apply <-
pinnapi_owner._own`, research-sql run 37231263685). On this class of
machine a 1,300 x 25-market snapshot applies inline in ~0.5 s.

THE CONTRACT PINNED HERE:
  * a snapshot of >= SNAPSHOT_OFFLOOP_MIN_EVENTS events applied from a
    running loop returns at once; the same `_apply` runs on a private copy in
    a worker thread and the result is swapped in whole: a reader sees the
    cache before the snapshot or after it, never part of it;
  * frames that arrive meanwhile are queued and applied after the swap in
    arrival order -- the final state, every counter and every change
    notification equal the inline replay's;
  * the snapshot counts as seen for resynchronisation only at the swap;
  * a new connection discards the build and its queue; a build whose epoch
    was revoked meanwhile is never swapped in;
  * the frame's receipt time is the one passed with it, never the swap's.
"""
from __future__ import annotations

import asyncio
import random
import time

from sportsassets import pinnapi_feed as F

LOOP_GAP_BOUND_S = 0.25        # see test_rc6_api_responsive_offloop


def _mk(rng, eid, n=25, sport=1):
    return {"id": eid, "type": "matchup",
            "participants": [{"alignment": "home", "name": "Home %d" % eid},
                             {"alignment": "away", "name": "Away %d" % eid}],
            "startTime": "2026-10-09T18:00:00Z", "isLive": False,
            "units": "Regular", "league": {"id": 1, "name": "L"},
            "version": 3,
            "markets": [{"key": "s;0;m" if i == 0 else "s;%d;s;%d"
                         % (i % 3, i), "type": "moneyline" if i == 0
                         else "spread", "period": i % 3, "status": "open",
                         "version": 3,
                         "prices": [{"designation": "home", "points": 1.5,
                                     "price": rng.choice([-150, -120, 105])},
                                    {"designation": "away", "points": -1.5,
                                     "price": rng.choice([-140, 110, 125])}]}
                        for i in range(n)]}


def _snapshot(seed=1, n=1300, sport=1, stream="prematch", ts=1_000_000,
              base=1000):
    rng = random.Random(seed)
    return {"type": "snapshot", "stream": stream, "sport_id": sport,
            "ts": ts, "events": [_mk(rng, base + i, sport=sport)
                                 for i in range(n)]}


def _deltas(seed, ids, ts0, n=60):
    """prematch_markets + live frames touching events of the snapshot."""
    rng = random.Random(seed)
    out = []
    for k in range(n):
        eid = rng.choice(ids)
        rec = _mk(rng, eid, n=4)
        ts = ts0 + 10 * (k + 1)
        if k % 2:
            out.append({"type": "prematch_markets", "sport_id": 1, "ts": ts,
                        "matchup_id": eid, "data": rec["markets"]})
        else:
            out.append({"type": "live", "sport_id": 1, "op": "upd",
                        "ts": ts, "rec": dict(rec, isLive=True)})
    return out


def _state(c):
    return ([(k, q.event_id, q.key, q.prices, q.change_ms, q.confirmed_ms,
              q.confirmed_by, q.received_ms, q.first_observed_ms, q.epoch)
             for k, q in c.quotes.items()],
            [(k, dict(v)) for k, v in c.events.items()],
            {k: v for k, v in c.counts.items()
             if not k.startswith(("snapshot_build", "frames_queued"))},
            dict(c.confirmations), dict(c.frames_by_sport_type),
            c.last_frame_received_ms, c.authority.state())


def _inline(frames, subs):
    c = F.FeedCache()
    c.offload_snapshots = False
    seen = []
    c.on_change = lambda q: seen.append((q.event_id, q.key, q.change_ms))
    ep = c.new_connection(subs)
    for i, m in enumerate(frames):
        c.apply(m, epoch=ep, received_ms=2_000_000 + i)
    return c, seen


async def _drained(c):
    for _ in range(4000):
        if getattr(c, "_pending", None) is None:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the build never finished")


def test_the_off_loop_replay_equals_the_inline_replay():
    """Two big snapshots (prematch, live) with deltas arriving while each is
    built, plus deltas after: state, counters and the change notifications
    are the inline replay's, frame for frame."""
    subs = [("prematch", 1), ("live", 1)]
    pre = _snapshot(seed=1)
    live = _snapshot(seed=2, stream="live", n=400, ts=1_000_500,
                     base=1000)
    ids = [e["id"] for e in pre["events"]]
    frames = ([pre] + _deltas(3, ids, 1_000_100) + [live]
              + _deltas(4, ids, 1_000_600) + _deltas(5, ids, 1_001_000))
    want, want_seen = _inline(frames, subs)

    async def go():
        c = F.FeedCache()
        assert c.offload_snapshots is True
        seen = []
        c.on_change = lambda q: seen.append((q.event_id, q.key,
                                             q.change_ms))
        ep = c.new_connection(subs)
        answers = []
        for i, m in enumerate(frames):
            answers.append(c.apply(m, epoch=ep, received_ms=2_000_000 + i))
            if i % 7 == 0:
                await asyncio.sleep(0)
        await _drained(c)
        return c, seen, answers
    got, got_seen, answers = asyncio.run(go())
    assert answers[0] == F.APPLY_BUILDING
    assert F.APPLY_QUEUED in answers
    assert got.counts["snapshot_builds_swapped_in"] == 2
    assert _state(got) == _state(want)
    assert got_seen == want_seen
    assert got.authority.synced is True


def scenario_apply_at_once() -> dict:
    snap = _snapshot(seed=9)

    async def go():
        c = F.FeedCache()
        ep = c.new_connection([("prematch", 1)])
        gaps = [0.0]
        done = asyncio.Event()

        async def tick():
            last = time.perf_counter()
            while not done.is_set():
                await asyncio.sleep(0.002)
                now = time.perf_counter()
                gaps[0] = max(gaps[0], now - last)
                last = now
        t = asyncio.create_task(tick())
        await asyncio.sleep(0.01)
        t0 = time.perf_counter()
        c.apply(snap, epoch=ep, received_ms=1.0)
        took = time.perf_counter() - t0
        await _drained(c)
        done.set()
        await t
        return c, took, gaps[0]
    c, took, gap = asyncio.run(go())
    return {"quotes": len(c.quotes), "took": took, "gap": gap,
            "builds": c.snapshot_build_ms.summary()["n"]}


def _fresh(name: str) -> dict:
    """Run this module's scenario `name` in a fresh interpreter: a loop gap
    read late in a long test session measures the session (its heap, the
    threads earlier tests left) -- 0.68 s here in a 12,000-test run, for a
    build a fresh process swaps in without holding the loop."""
    import json
    import os
    import subprocess
    import sys
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = ("import json, sys; sys.path[:0] = [%r, %r]; "
            "import test_rc6_api_responsive_snapshot as T; "
            "print('RESULT ' + json.dumps(T.%s()))"
            % (backend, os.path.join(backend, "tests"), name))
    proc = subprocess.run([sys.executable, "-c", code], cwd=backend,
                          capture_output=True, text=True, timeout=600,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    got = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert got, proc.stderr[-3000:]
    return json.loads(got[-1][len("RESULT "):])


def test_apply_returns_at_once_and_the_loop_is_never_held():
    got = _fresh("scenario_apply_at_once")
    assert got["quotes"] == 1300 * 25
    assert got["took"] < 0.05, "apply held the loop %.3f s" % got["took"]
    assert got["gap"] < LOOP_GAP_BOUND_S, \
        "the loop was held %.3f s" % got["gap"]
    assert got["builds"] == 1


def test_readers_never_see_a_half_applied_snapshot():
    """While the build runs, the cache is the one before the snapshot (and
    unsynchronised); after the swap it is all of it, at once."""
    snap = _snapshot(seed=4, n=600)
    first = snap["events"][0]["id"]

    async def go():
        c = F.FeedCache()
        ep = c.new_connection([("prematch", 1)])
        c.apply(snap, epoch=ep, received_ms=5.0)
        sizes = set()
        reads = []
        while c._pending is not None:
            sizes.add(len(c.quotes))
            reads.append(c.read(first, "s;0;m", evaluated_ms=6.0)["reason"])
            await asyncio.sleep(0)
        return c, sizes, reads
    c, sizes, reads = asyncio.run(go())
    assert sizes <= {0, 600 * 25}, sizes
    assert reads and set(reads) == {F.R_NOT_SYNCED}
    assert len(c.quotes) == 600 * 25 and c.authority.synced
    q = c.quotes[(first, "s;0;m")]
    # the receipt time is the frame's, never the swap's
    assert q.received_ms == 5.0 and q.first_observed_ms == 5.0


def test_a_new_connection_discards_the_build_and_its_queue():
    snap = _snapshot(seed=6, n=500)
    ids = [e["id"] for e in snap["events"]]

    async def go():
        c = F.FeedCache()
        ep = c.new_connection([("prematch", 1)])
        c.apply(snap, epoch=ep, received_ms=1.0)
        for m in _deltas(7, ids, 1_000_100, n=10):
            assert c.apply(m, epoch=ep, received_ms=2.0) == F.APPLY_QUEUED
        ep2 = c.new_connection([("prematch", 1)])
        assert c._pending is None and not c._backlog
        await asyncio.sleep(0.5)          # the old build ends; never lands
        small = _snapshot(seed=8, n=3)
        c.apply(small, epoch=ep2, received_ms=3.0)
        return c
    c = asyncio.run(go())
    assert len(c.quotes) == 3 * 25
    assert c.counts["snapshot_builds_discarded_new_connection"] == 1
    assert c.counts["frames_dropped_with_a_discarded_build"] == 10
    assert c.authority.synced


def test_a_build_whose_epoch_was_revoked_is_never_swapped_in():
    snap = _snapshot(seed=10, n=500)

    async def go():
        c = F.FeedCache()
        ep = c.new_connection([("prematch", 1)])
        c.apply(snap, epoch=ep, received_ms=1.0)
        c.lost(F.R_NO_AUTHORITY)
        c.apply({"type": "ping"}, epoch=ep, received_ms=2.0)
        await _drained(c)
        return c
    c = asyncio.run(go())
    assert len(c.quotes) == 0
    assert c.counts["snapshot_builds_discarded_authority"] == 1
    assert c.counts["frames_from_revoked_epoch"] == 1
    assert not c.authority.synced


def test_small_snapshots_and_calls_without_a_loop_apply_inline():
    c = F.FeedCache()
    ep = c.new_connection([("prematch", 1)])
    assert c.apply(_snapshot(n=1300), epoch=ep, received_ms=1.0) == \
        "snapshot"
    assert len(c.quotes) == 1300 * 25

    async def go():
        c2 = F.FeedCache()
        ep2 = c2.new_connection([("prematch", 1)])
        got = c2.apply(_snapshot(n=F.SNAPSHOT_OFFLOOP_MIN_EVENTS - 1),
                       epoch=ep2, received_ms=1.0)
        return c2, got
    c2, got = asyncio.run(go())
    assert got == "snapshot" and c2._pending is None
    assert c2.authority.synced


def test_the_census_publishes_the_build():
    c = F.FeedCache()
    sb = c.census()["snapshot_build"]
    assert sb == {"off_loop": True,
                  "min_events": F.SNAPSHOT_OFFLOOP_MIN_EVENTS,
                  "building": False, "queued_frames": 0,
                  "build_ms": {"n": 0}}
