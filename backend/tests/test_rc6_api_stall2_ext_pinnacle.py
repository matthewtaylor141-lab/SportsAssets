"""CAPITAL-CRITICAL: THE EXT_PINNACLE CYCLE'S PINNAPI READS MATCH BY THE FEED
CACHE'S CURRENT-GENERATION INDEX -- THE SCAN'S OWN ANSWER -- AND THE CYCLE
GIVES THE API LOOP A TURN BETWEEN EVENTS: A RUN OF REFUSED EVENTS NO LONGER
HOLDS THE LOOP, AND EVERY ONE OF THEM IS REFUSED AS BEFORE.

THE EVIDENCE. RC6.1 production, 2026-10-09 (render-ops logs runs
37949540216 and 37950489691), the API loop watchdog:
  15:00:21Z  lag 1.1 s held by task ext_pinnacle_loop.py:run at
             pinnapi_feed.py:414 participants <- fixture_view <-
             pinnapi_primary.py:203 _candidates <- _match_tier;
  15:15:21Z  lag 1.9 s held by task ext_pinnacle_loop.py:run at
             pinnapi_names.py:455 absence <- pinnapi_primary.match_event <-
             select <- ext_pinnacle_loop.py:3590 primary_pinnacle_h2h.
An event whose PinnAPI read is refused `continue`s without an await, and
that read scanned the whole feed cache (a fixture view per name tier; on a
miss, the absence pass over every record): ~95 ms per refused event on
2,600 cached events on a quiet local core, against ~30 ms to build the
index once and 0.05 ms per read by it (LOCAL BENCHMARK ONLY; production's
CPU is several times slower). `evaluated` counts only events that pass, so
MAX_PER_CYCLE never bounds a run of refused ones, and the loop got no turn
until the run ended.

Pinned here: the index read through `current_index` equals a fresh scan at
every instant while frames of every kind land between reads, and is built
once per cache generation; `select` and the reactive register read it; and
on a real cycle (the provider and the venue stubbed at their transport
boundaries, a production-size synced feed cache in the process) every
refused event is refused by exactly the scan's reason, while the loop's
longest gap stays under the watchdog's 1.0 s lag threshold and under a
quarter of what the scan path's reads alone cost.
"""
from __future__ import annotations

import asyncio
import contextlib
import gc
import os
import time

import pytest

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as RT
from sportsassets import pinnapi_owner as O
from sportsassets import pinnapi_primary as P
from sportsassets.workers import ext_pinnacle_loop as loop

try:
    from tests.test_ext_pinnacle_loop import (SOCCER_KEY, _event,
                                              _stub_sport_catalogue,
                                              _stub_venue_board)
except ImportError:                                             # pragma: no cover
    from test_ext_pinnacle_loop import (SOCCER_KEY, _event,
                                        _stub_sport_catalogue,
                                        _stub_venue_board)

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")
WATCHDOG_LAG_S = 1.0


@contextlib.contextmanager
def _collector_paused():
    """A full collection holds the GIL whichever thread triggers it -- in a
    long test session's heap, for longer than the work measured here (the
    API loop watchdog reports the collector's share apart) -- so it is
    paused while a loop gap is measured."""
    was = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        if was:
            gc.enable()


def _synced_cache(n_events=2600):
    """A feed cache the size production held (2,600 events), every
    (stream, sport) snapshot seen: an authority the cycle reads."""
    c = F.FeedCache()
    c.offload_snapshots = False
    ep = c.new_connection([("live", 1), ("prematch", 1)])
    recs = [{"id": 10_000 + i, "type": "matchup",
             "startTime": "2026-10-09T18:00:00Z", "isLive": False,
             "units": "Regular", "league": {"id": 1, "name": "L"},
             "participants": [{"name": "Home %d" % i, "alignment": "home"},
                              {"name": "Away %d" % i, "alignment": "away"}],
             "markets": [{"key": "s;0;m", "type": "moneyline", "period": 0,
                          "status": "open",
                          "prices": [{"designation": "home", "price": -120},
                                     {"designation": "away", "price": 110}]}]}
            for i in range(n_events)]
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 1,
             "ts": 1_000, "events": recs}, epoch=ep, received_ms=1_000)
    c.apply({"type": "snapshot", "stream": "live", "sport_id": 1,
             "ts": 1_000, "events": []}, epoch=ep, received_ms=1_000)
    assert c.authority.synced
    return c


def _refused_events(n):
    """Provider events naming teams the feed holds no record of near their
    start -- each is read (a full scan and the absence pass) and refused;
    the payload carries no Pinnacle book, so no fallback either."""
    return [{"id": "evt-stall-%d" % i, "home_team": "Nobody %d" % i,
             "away_team": "Noone %d" % i,
             "commence_time": "2026-10-09T18:00:00Z",
             "bookmakers": [{"key": "smarkets", "last_update": None,
                             "markets": []}]}
            for i in range(n)]


def _seed_events(rng, n):
    out = []
    for i in range(n):
        j = rng.randrange(40)
        kind = rng.random()
        if kind < 0.45:
            home, away = "Home %d" % j, "Away %d" % j          # a fixture
        elif kind < 0.6:
            home, away = "Away %d" % j, "Home %d" % j          # swapped
        elif kind < 0.75:
            home, away = "Home %d" % j, "Nobody %d" % j        # one named
        else:
            home, away = "Nobody %d" % i, "Noone %d" % i       # absent
        out.append({"id": "e%d" % i, "home_team": home, "away_team": away,
                    "commence_time": rng.choice(["2026-10-09T18:00:00Z",
                                                 "2026-10-09T19:00:00Z",
                                                 "2026-10-10T18:00:00Z"]),
                    "sport_key": "soccer_epl"})
    return out


def _frame(rng, ep_ts):
    eid = 10_000 + rng.randrange(48)
    k = rng.random()
    if k < 0.25:
        return {"type": "live", "sport_id": 1, "op": "del", "ts": ep_ts,
                "rec": {"id": eid}}
    if k < 0.5:
        return {"type": "prematch_matchups", "sport_id": 1, "ts": ep_ts,
                "data": [{"id": eid, "startTime": rng.choice(
                    ["2026-10-09T18:00:00Z", "2026-10-09T21:30:00Z"]),
                    "participants": [
                        {"name": "Home %d" % (eid - 10_000),
                         "alignment": "home"},
                        {"name": rng.choice(["Away %d" % (eid - 10_000),
                                             "Home %d" % (eid - 10_000)]),
                         "alignment": "away"}]}]}
    if k < 0.75:
        return {"type": "snapshot", "stream": "prematch", "sport_id": 1,
                "ts": ep_ts, "events": [
                    {"id": eid, "type": "matchup",
                     "startTime": "2026-10-09T18:00:00Z",
                     "participants": [
                         {"name": "Home %d" % (eid - 10_000),
                          "alignment": "home"},
                         {"name": "Away %d" % (eid - 10_000),
                          "alignment": "away"}],
                     "markets": []}]}
    return {"type": "live", "sport_id": 1, "op": "upd", "ts": ep_ts,
            "rec": {"id": 90_000 + rng.randrange(5), "parentId": eid,
                    "isLive": True, "units": "Regular",
                    "participants": [
                        {"name": "Home %d" % (eid - 10_000),
                         "alignment": "home"},
                        {"name": "Away %d" % (eid - 10_000),
                         "alignment": "away"}],
                    "markets": []}}


def test_the_generation_index_answers_what_the_scan_answers_as_frames_land():
    """The memoised index is the scan's answer at every instant: frames of
    every kind (new events, deletions, re-timed or re-named matchups, live
    children) land between reads, and every read through
    `current_index` equals a fresh scan of the cache at that moment. Every
    applied frame moves the generation; a read without one in between
    reuses the index."""
    import random
    rng = random.Random(77)
    c = _synced_cache(40)
    ep = c.authority.epoch
    seeds = _seed_events(rng, 400)
    builds = []
    real = P.fixture_index

    def counting(cache):
        builds.append(cache.generation)
        return real(cache)
    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(P, "fixture_index", counting)
    try:
        for step in range(400):
            if step % 3 == 0:
                g = c.generation
                c.apply(_frame(rng, 2_000 + step), epoch=ep,
                        received_ms=2_000 + step)
                assert c.generation == g + 1
            e = seeds[step]
            for fam in ("soccer",):
                ex_scan, ex_idx = {}, {}
                want = P.match_event(c, e, fam, explain=ex_scan)
                got = P.match_event(c, e, fam, explain=ex_idx,
                                    index=P.current_index(c))
                assert got == want, (step, e)
                assert ex_idx == ex_scan, (step, e)
    finally:
        mp.undo()
    # one build per generation that was read, never one per read
    assert len(builds) == len(set(builds)) and len(builds) <= 135


def test_select_and_the_register_read_the_generation_index(monkeypatch):
    """`select` and a register without a batch index match through
    `current_index`; a reused index is never older than the cache."""
    import random
    rng = random.Random(5)
    c = _synced_cache(300)
    seeds = _seed_events(rng, 50)
    calls = []
    real = P.match_event

    def spy(cache, event, family, *, index=None, explain=None):
        calls.append(index)
        return real(cache, event, family, index=index, explain=explain)
    monkeypatch.setattr(P, "match_event", spy)
    for e in seeds:
        P.select(c, e, None, family="soccer", sharp_books=(), at=1.0,
                 runtime_id="r")
    assert calls and all(isinstance(i, dict) for i in calls)
    assert all(i is calls[0] for i in calls), "one index for one generation"
    c.apply({"type": "ping"}, epoch=c.authority.epoch)
    P.select(c, seeds[0], None, family="soccer", sharp_books=(), at=1.0,
             runtime_id="r")
    assert calls[-1] is not calls[0], "a new generation, a new index"
    from sportsassets import pinnapi_reactive as RX
    sch = RX.Scheduler(c, None, None)
    n = len(calls)
    for e in seeds[:10]:
        sch.register(e, sport_key="soccer_epl", family="soccer",
                     received_at=1.0)
    assert len(calls) == n + 10
    assert all(i is calls[n - 1] for i in calls[n:]), \
        "the register reads the same generation's index"
    batch = {"__a_batch_index__": True}
    sch.register(seeds[0], sport_key="soccer_epl", family="soccer",
                 received_at=1.0, index=batch)
    assert calls[-1] is batch, "a batch's own index is still the one used"


@pg
async def test_a_run_of_refused_events_never_holds_the_loop(monkeypatch):
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS ingestion_state "
            "(key TEXT PRIMARY KEY, value TEXT)")
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
            "ON CONFLICT (key) DO UPDATE SET value = 'true'",
            loop.CONTROL_KEY)
        # the venue lists the competition's fixture (as the existing cycle
        # tests seed it), so the competition is confirmed and its events are
        # judged
        await conn.execute(
            "INSERT INTO markets (condition_id, title, event_title, slug, "
            "sport, closed, resolved) VALUES "
            "('c-lfc-mci','Will Liverpool beat Manchester City?',"
            "'Liverpool vs. Manchester City','lfc-mci','Soccer',false,false)"
            " ON CONFLICT (condition_id) DO UPDATE SET "
            "sport = EXCLUDED.sport, closed = FALSE, resolved = FALSE")
        monkeypatch.setenv("EDGE_ODDS_API_KEY", "x" * 32)
        _stub_venue_board(monkeypatch, "unl")
        _stub_sport_catalogue(monkeypatch, SOCCER_KEY)
        # the competition's one confirmable fixture, then the refused run
        events = [_event()] + _refused_events(60)

        async def fake_fetch(sport_key, *, api_key, timeout=20.0):
            got = events if sport_key == SOCCER_KEY else []
            return {"ok": True, "events": [dict(e) for e in got],
                    "received_at": time.time(), "credits_used": "1",
                    "credits_remaining": "9"}
        monkeypatch.setattr(loop, "fetch_odds", fake_fetch)

        cache = _synced_cache()
        owner = O.FeedOwner(cache, sport_ids=[1], lease_factory=None,
                            connect=None)
        monkeypatch.setitem(RT._STATE, "owner", owner)
        monkeypatch.setitem(RT._STATE, "runtime_id", "stall2-test")

        calls, refusals = [], {}
        real = loop.primary_pinnacle_h2h

        def spy(event, **kw):
            t0 = time.monotonic()
            why = kw.get("explain")
            out = real(event, **kw)
            calls.append((t0, time.monotonic()))
            refusals[event["id"]] = (why or {}).get("reason")
            return out
        monkeypatch.setattr(loop, "primary_pinnacle_h2h", spy)

        stamps = []
        done = asyncio.Event()

        async def ticker():
            # a stamp per loop turn this task gets, the last one after the
            # cycle however long the loop was held
            while True:
                stamps.append(time.monotonic())
                if done.is_set():
                    return
                await asyncio.sleep(0.005)
        t = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        try:
            with _collector_paused():
                out = await loop.cycle(conn)
        finally:
            done.set()
            await t
        gaps = list(zip(stamps, stamps[1:]))
    finally:
        await conn.close()
    assert out["ran"] is True, out
    # every refused event was still read, and refused by the reason the
    # scan gives for it on this cache
    refused = events[1:]
    assert len(calls) >= len(refused), (len(calls), out.get("refusals"))
    want = {e["id"]: P.match_event(cache, e, "soccer")[1] for e in refused}
    assert {k: refusals.get(k) for k in want} == want
    assert set(want.values()) <= {P.R_NOT_IN_FEED, P.R_NO_EXACT}
    # what the scan path costs for the same reads on this cache, now
    t0 = time.monotonic()
    for e in refused:
        P.match_event(cache, e, "soccer")
    t_scan = time.monotonic() - t0
    w0, w1 = calls[0][0], calls[-1][1]
    during = [b - a for a, b in gaps if b > w0 and a < w1]
    assert during
    print("MEASURED longest loop gap %.4f s while %d events were judged in "
          "%.4f s; the scan of their reads alone %.4f s"
          % (max(during), len(calls), w1 - w0, t_scan))
    assert max(during) < WATCHDOG_LAG_S, (max(during), t_scan)
    assert max(during) < 0.25 * t_scan, (max(during), t_scan)
