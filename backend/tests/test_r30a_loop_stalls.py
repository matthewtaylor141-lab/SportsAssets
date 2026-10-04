"""R30A RUNTIME: THE API EVENT-LOOP STALLS BEHIND THE 2-3 S TIMEOUTS.

PRODUCTION EVIDENCE (2026-10-04, read-only):

  * render-ops `logs` 15:40-20:10Z, filter 'loop stall' (run 37231102537):
    the API's loop watchdog recorded the event loop held >= 2 s about sixty
    times, clustered in the same minutes as the three timeout classes --
    16:00:08-16:02:06, 17:19:37-17:22:53, 17:46:23-17:47:02,
    18:20:43-18:27:03, 18:42:48 -- against reactive-audit failures at
    16:00:53 / 16:01:20 / 16:02:24 / 17:40:58 / 17:46:10 / 17:46:12 /
    17:48:56 / 18:21:28 / 18:21:31 / 18:21:58 / 18:23:43, research-tick
    timeouts at 16:00:14-16:01:48 (every ~18 s), 17:19:37-17:22:40,
    17:37-17:46, 18:20:13-18:22:56, 18:42:45, and feed-heartbeat failures
    at 16:01:03, 17:20:01, 17:47:01, 18:20:26, 18:22:40, 18:23:13, 18:26:57.
    A held loop expires every 2-3 s budget in the process whatever the pool
    holds; it is also the only explanation for the two audit failures whose
    traceback is INSIDE the INSERT (16:00:53, 17:46:10: asyncpg prepare,
    after the connection was acquired).
  * research-sql run 37231263685 (ingestion_state api.loop_stalls, the
    watchdog's stacks): the loop thread was in
    `pinnapi_feed._replace_event <- _apply <- apply <- pinnapi_owner._own`
    (2.1 s, ended 4.0 s; 2.2 s, ended 2.5 s), in
    `pinnapi_feed.participants <- pinnapi_census.feed_event_view <-
    pinnapi_feed_runtime.held_event_id <- pinnapi_held.refresh` (2.0 s), and
    in `pinnapi_feed_runtime._census_once` (2.1 s).

THE DEFECTS, PINNED HERE:
  1 · FeedCache found an event's quotes by scanning EVERY quote (up to
      MAX_MARKETS 120,000) -- once per event of a snapshot, per prematch
      frame, per live frame. Now a per-event index (EventIndexedQuotes); the
      cache's behaviour is unchanged (frame sequences are replayed against
      the scanning implementation and compared).
  2 · the held refresh rebuilt the whole feed event view once PER HELD SLUG;
      now once per pass.
  3 · the census matched the venue catalogue against the feed view on the
      event loop; the view is still taken on the loop (the cache is the
      loop's), the matching -- pure, over copies -- runs in a worker thread.
"""
from __future__ import annotations

import asyncio
import random
import time

import pytest

from sportsassets import pinnapi_feed as F


# ── the implementation before R30A, verbatim (191b299) ────────────────

class _ScanningCache(F.FeedCache):
    """The four per-event operations exactly as they were: full scans."""

    def _drop_event(self, eid):
        self.events.pop(eid, None)
        for k in [k for k in self.quotes if k[0] == eid]:
            del self.quotes[k]
        self.counts["events_deleted"] += 1

    def _replace_event(self, ev, *, stream, sport, epoch, frame_ts, rx,
                       as_change, keep_meta=False):
        eid = ev["id"]
        parsed = self.extract(ev)
        if parsed is None:
            self.counts["unparsed"] += 1
            return
        if not keep_meta:
            self._touch_meta(ev, stream=stream, sport=sport)
        else:
            self.events.setdefault(eid, {"id": eid, "stream": stream,
                                         "sport_id": sport})
            self.events.move_to_end(eid)
        old = {k: q for k, q in self.quotes.items() if k[0] == eid}
        for k in old:
            del self.quotes[k]
        closed = F.closed_periods(ev)
        for key, f in parsed:
            if not f["open"] or (f["period"] or 0) in closed:
                continue
            prev = old.get((eid, key))
            changed = prev is None or prev.prices != f["prices"]
            if as_change and changed and prev is not None:
                change = frame_ts
            elif as_change and prev is None:
                change = None
            else:
                change = prev.source_change_ms if (
                    prev and not changed) else None
            self._put(eid, key, f, stream, sport, epoch, change, frame_ts,
                      rx)
        self._bound()

    def _merge_event(self, rec, *, stream, sport, epoch, frame_ts, rx):
        eid = rec["id"]
        self._touch_meta(rec, stream=stream, sport=sport)
        closed = F.closed_periods(rec)
        for k in [k for k, q in self.quotes.items()
                  if k[0] == eid and (q.period or 0) in closed]:
            del self.quotes[k]
        parsed = self.extract(rec)
        if parsed is None:
            self.counts["unparsed"] += 1
            return
        for key, f in parsed:
            k = (eid, key)
            if not f["open"] or (f["period"] or 0) in closed:
                self.quotes.pop(k, None)
                self.counts["markets_closed"] += 1
                continue
            prev = self.quotes.get(k)
            changed = prev is None or prev.prices != f["prices"]
            change = frame_ts if changed else prev.source_change_ms
            if changed:
                self.counts["price_changes"] += 1
                self.last_change_received_ms = rx
            self._put(eid, key, f, stream, sport, epoch, change, frame_ts, rx)
        self._bound()

    def _bound(self):
        while len(self.events) > self.max_events:
            eid, _ = self.events.popitem(last=False)
            for k in [k for k in self.quotes if k[0] == eid]:
                del self.quotes[k]
            self.counts["events_evicted"] += 1
        if len(self.quotes) > self.max_markets:
            for eid in list(self.events):
                if len(self.quotes) <= self.max_markets:
                    break
                self._drop_event(eid)
                self.counts["events_evicted"] += 1


# ── frames ─────────────────────────────────────────────────────────────

def _market(rng, key, period):
    return {"key": key, "type": rng.choice(["moneyline", "spread", "total"]),
            "period": period,
            "status": rng.choice(["open", "open", "open", "closed"]),
            "prices": [{"designation": "home",
                        "price": rng.choice([-150, -120, 105, 130])},
                       {"designation": "away",
                        "price": rng.choice([-140, 110, 125])}]}


def _record(rng, eid, n_markets=4):
    periods = [0, 1, 2]
    rec = {"id": eid,
           "participants": [{"alignment": "home", "name": "H%d" % eid},
                            {"alignment": "away", "name": "A%d" % eid}],
           "markets": [_market(rng, "s;%d;m%d" % (rng.choice(periods), i),
                               rng.choice(periods))
                       for i in range(rng.randint(0, n_markets))]}
    if rng.random() < 0.2:
        rec["periods"] = [{"period": rng.choice(periods),
                           "status": rng.choice(["open", "closed",
                                                 "settled"])}]
    return rec


def _frames(seed, n=400, n_events=40):
    rng = random.Random(seed)
    out = []
    ts = 1_000_000
    for _ in range(n):
        ts += rng.randint(1, 900)
        kind = rng.random()
        if kind < 0.15:
            out.append({"type": "snapshot", "stream": "live", "sport_id": 6,
                        "ts": ts, "events": [
                            _record(rng, rng.randrange(n_events))
                            for _ in range(rng.randint(1, 12))]})
        elif kind < 0.55:
            op = "del" if rng.random() < 0.1 else "upd"
            out.append({"type": "live", "sport_id": 6, "op": op, "ts": ts,
                        "rec": _record(rng, rng.randrange(n_events))})
        elif kind < 0.85:
            rec = _record(rng, rng.randrange(n_events))
            out.append({"type": "prematch_markets", "sport_id": 1, "ts": ts,
                        "matchup_id": rec["id"], "data": rec["markets"]})
        else:
            out.append({"type": "prematch_matchups", "sport_id": 1,
                        "ts": ts, "data": [_record(rng,
                                                   rng.randrange(n_events))]})
    return out


def _state(c):
    return (list(c.quotes.items()), [(k, dict(v)) for k, v in
                                     c.events.items()], dict(c.counts))


def _replay(cache_cls, frames, **kw):
    c = cache_cls(**kw)
    seen = []
    c.on_change = lambda q: seen.append((q.event_id, q.key,
                                         q.source_change_ms))
    ep = c.new_connection([("live", 6), ("prematch", 1)])
    states = []
    for i, msg in enumerate(frames):
        c.apply(msg, epoch=ep, received_ms=2_000_000 + i)
        states.append(_state(c))
    return c, states, seen


@pytest.mark.parametrize("seed", range(12))
def test_the_indexed_cache_replays_every_frame_exactly_as_the_scanning_one(
        seed):
    """Same frames, same bounds (small, so eviction runs): the same quotes
    in the same order, the same events, counts and change notifications
    after EVERY frame."""
    frames = _frames(seed)
    kw = dict(max_events=25, max_markets=60)
    new, s_new, seen_new = _replay(F.FeedCache, frames, **kw)
    old, s_old, seen_old = _replay(_ScanningCache, frames, **kw)
    assert len(s_new) == len(s_old) == len(frames)
    for i, (a, b) in enumerate(zip(s_new, s_old)):
        assert a == b, "state diverged after frame %d: %r" % (i, frames[i])
    assert seen_new == seen_old
    assert new.counts["events_evicted"] or new.counts["events_deleted"]


@pytest.mark.parametrize("seed", range(6))
def test_the_index_is_exact_after_every_mutation(seed):
    c = F.FeedCache(max_events=20, max_markets=50)
    ep = c.new_connection([("live", 6), ("prematch", 1)])
    for i, msg in enumerate(_frames(seed)):
        c.apply(msg, epoch=ep, received_ms=i)
        expect: dict = {}
        for k in c.quotes:
            expect.setdefault(k[0], set()).add(k)
        assert c.quotes.by_event == expect
    c.new_connection([("live", 6)])
    assert c.quotes.by_event == {} and len(c.quotes) == 0


def test_direct_writers_keep_the_index_exact():
    """Tests (and any caller) set, pop and clear `cache.quotes` directly:
    the index follows every dict mutation method."""
    q = F.EventIndexedQuotes()
    q[(1, "a")] = "x"
    q[(1, "b")] = "y"
    q.setdefault((2, "a"), "z")
    q.update({(3, "a"): "w"})
    assert q.keys_of(1) and sorted(q.keys_of(1)) == [(1, "a"), (1, "b")]
    del q[(1, "a")]
    assert q.pop((1, "b")) == "y" and q.pop((9, "x"), None) is None
    with pytest.raises(KeyError):
        q.pop((9, "x"))
    assert 1 not in q.by_event and set(q.by_event) == {2, 3}
    k, _ = q.popitem()
    assert k == (3, "a") and set(q.by_event) == {2}
    q.clear()
    assert q.by_event == {} and dict(q) == {}


def test_a_snapshot_onto_a_full_cache_no_longer_scans_every_quote():
    """THE PRODUCTION SHAPE: a snapshot of 400 events onto a cache already
    holding ~100,000 quotes. The scanning implementation walks every quote
    once per event; the indexed one touches each event's own markets. The
    indexed path must be at least 20 x faster on the same input (measured
    here: well over 100 x)."""
    rng = random.Random(7)

    def full(cls):
        c = cls(max_events=10_000, max_markets=200_000)
        ep = c.new_connection([("live", 6)])
        for base in range(0, 5000, 500):
            c.apply({"type": "snapshot", "stream": "live", "sport_id": 6,
                     "ts": 1, "events": [
                         {"id": e, "markets": [
                             {"key": "k%d" % j, "type": "moneyline",
                              "period": 0, "status": "open",
                              "prices": [{"designation": "home",
                                          "price": -110}]}
                             for j in range(20)]}
                         for e in range(base, base + 500)]},
                    epoch=ep, received_ms=1)
        return c, ep

    snap = {"type": "snapshot", "stream": "live", "sport_id": 6, "ts": 2,
            "events": [_record(rng, 10_000 + e, n_markets=8)
                       for e in range(400)]}
    c_new, ep_new = full(F.FeedCache)
    assert len(c_new.quotes) == 100_000
    t0 = time.perf_counter()
    c_new.apply(snap, epoch=ep_new, received_ms=2)
    t_new = time.perf_counter() - t0
    c_old, ep_old = full(_ScanningCache)
    t0 = time.perf_counter()
    c_old.apply(snap, epoch=ep_old, received_ms=2)
    t_old = time.perf_counter() - t0
    assert list(c_new.quotes.items()) == list(c_old.quotes.items())
    assert t_new * 20 < t_old, (t_new, t_old)


# ── 2 · the held refresh builds the feed view once per pass ─────────────

def test_the_held_refresh_builds_the_feed_view_once_per_pass(monkeypatch):
    from sportsassets import pinnapi_census as C
    from sportsassets import pinnapi_feed_runtime as FR
    from sportsassets import pinnapi_held as PH

    calls = {"view": 0, "resolved": []}

    def _view(cache):
        calls["view"] += 1
        return {6: []}

    async def _held_event_id(conn, slug, *, view=None):
        assert view == {6: []}, "every slug is matched against ONE view"
        calls["resolved"].append(slug)
        return None, "TEST"

    async def _slugs(conn):
        return ["a", "b", "c", "d"]

    class _Cache:
        class authority:
            synced = True

    class _Owner:
        cache = _Cache()

    monkeypatch.setattr(C, "feed_event_view", _view)
    monkeypatch.setattr(FR, "held_event_id", _held_event_id)
    monkeypatch.setattr(PH, "held_slugs", _slugs)
    monkeypatch.setitem(FR._STATE, "owner", _Owner())
    w = PH.HeldWatch()
    out = asyncio.run(PH.refresh(object(), watch=w))
    assert out["ok"] is True
    assert calls["view"] == 1 and calls["resolved"] == ["a", "b", "c", "d"]


def test_held_event_id_uses_a_given_view_and_builds_none(monkeypatch):
    from sportsassets import pinnapi_census as C
    from sportsassets import pinnapi_feed_runtime as FR

    def _boom(cache):
        raise AssertionError("a given view must not be rebuilt")

    class _Conn:
        async def fetchrow(self, sql, *a):
            return {"sports_type": "mlb", "event_slug": None,
                    "us_market_slug": a[0]}

        async def fetch(self, sql, *a):
            return []

    class _Cache:
        class authority:
            synced = True

    class _Owner:
        cache = _Cache()
        sport_ids = [3]

    seen = {}

    def _match(row, rows, view, **kw):
        seen["view"] = view
        return C.S_SUPPORTED, 77, 3

    monkeypatch.setattr(C, "feed_event_view", _boom)
    monkeypatch.setattr(C, "contract_match", _match)
    monkeypatch.setitem(FR._STATE, "owner", _Owner())
    monkeypatch.setattr(FR, "HELD_FULL_GAME_TYPES", {"mlb"})
    got = asyncio.run(FR.held_event_id(_Conn(), "slug-1", view={3: ["v"]}))
    assert got == (77, None) and seen["view"] == {3: ["v"]}


# ── 3 · the census matching runs off the event loop ─────────────────────

def test_the_census_matching_runs_in_a_worker_thread(monkeypatch):
    import threading

    from sportsassets import pinnapi_census as C
    from sportsassets import pinnapi_feed_runtime as FR

    where = {}

    def _census(rows, view, **kw):
        where["thread"] = threading.current_thread().name
        where["view"] = view
        return {"ok": True}

    class _Conn:
        async def fetch(self, sql, *a):
            return []

    class _Acq:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *e):
            return False

    class _Pool:
        def acquire(self, *a, **kw):
            return _Acq()

    class _Cache:
        events: dict = {}

        class authority:
            synced = True

    class _Owner:
        cache = _Cache()
        sport_ids = [3]

    monkeypatch.setattr(C, "census", _census)
    monkeypatch.setattr(C, "feed_event_view", lambda cache: {3: ["v"]})
    monkeypatch.setitem(FR._STATE, "owner", _Owner())
    loop_thread = {}

    async def main():
        loop_thread["name"] = threading.current_thread().name
        return await FR._census_once(_Pool())
    out = asyncio.run(main())
    assert out["ok"] is True and where["view"] == {3: ["v"]}
    assert where["thread"] != loop_thread["name"], (
        "the census matching must not run on the event loop's thread")


# ── the OOM evidence the next release will carry ───────────────────────

def test_the_cycle_records_rss_at_each_step_boundary():
    """The API was OOM-killed seven times on 2026-10-04 (render-ops events
    run 37231548727) with RSS stepping up in the minutes of the entry lane
    and the outcome join; which step allocates is the missing evidence.
    Each step's RSS delta now rides the cycle heartbeat; an unreadable
    boundary is None, never a number."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as L
    got = L._step_rss({"start": 300.0, "serviced": 301.0, "entry": 900.0,
                       "persisted": 905.5, "joined": 1400.0,
                       "calibrated": None, "observed": 1450.0})
    assert got["at_start"] == 300.0 and got["at_end"] == 1450.0
    assert got["delta"] == {"servicing_in_cycle": 1.0, "entry_lane": 599.0,
                            "candidate_outcomes": 5.5,
                            "outcome_join": 494.5,
                            "calibration_measurement": None,
                            "pair_observation": None}
    src = inspect.getsource(L.cycle)
    assert '"step_rss_mb": step_rss_mb' in src
    for b in ("serviced", "entry", "persisted", "joined", "calibrated",
              "observed"):
        assert '_rss_at["%s"] = _procmem.rss_mb()' % b in src
