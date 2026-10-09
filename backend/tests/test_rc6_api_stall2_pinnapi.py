"""CAPITAL-CRITICAL: THE PINNAPI FEED OWNER NEVER HOLDS THE API EVENT LOOP
FOR A BURST, AND NEVER HOLDS THE GIL FOR A WHOLE BIG FRAME -- AND THE
FRAMES IT APPLIES, AND THE VALUES IT DECODES, ARE EXACTLY THE ONES IT
APPLIED AND DECODED BEFORE.

THE EVIDENCE. RC6.1 production, 2026-10-09 (render-ops logs runs
37949540216 and 37951709001; the API loop watchdog's ring, research-sql
rc6_api-responsive_loop_stalls.sql run 37950182966):

  14:44:27Z / 14:44:46Z  the feed started / synced (the subscribe snapshots);
  14:45:03Z, 14:45:43Z   lags 1.6 s and 1.9 s "held by task=none (a loop
                         callback) inner=ssl.py:859 read <- runners.py:118
                         run" -- the loop inside uvloop's TLS read, waiting to
                         take the GIL back from a thread decoding a whole
                         snapshot frame in ONE json.loads call (a C call
                         never hands the GIL over). Reproduced locally (LOCAL
                         BENCHMARK ONLY, uvloop + a TLS stream + a thread
                         decoding a 7 MB snapshot): the loop's longest gap
                         0.55-0.62 s, sampled at ssl.py:859 read /
                         runners.py:118 run; decoded element by element:
                         0.10-0.125 s;
  14:47:40Z              a 2.35 s stall, task FeedOwner.run, the loop thread
                         in json.loads from _own and the lag line naming
                         _put <- _replace_event <- _apply <- _apply_inline:
                         a burst of queued frames applied without the loop
                         ever getting a turn (ws.recv() does not suspend
                         while frames are queued).

Pinned here:
  * `decode_frame` is `json.loads` -- the same value for production-shaped
    frames and for a fuzzed corpus (whitespace, repeated names, escapes,
    nesting, binary frames), and for anything json.loads refuses, the same
    exception;
  * the owner's big-frame decode never holds the GIL for long: a thread
    waiting for it while the owner decodes a production-size snapshot
    waits a fraction of what one json.loads call of that frame takes;
  * a burst of production-size queued frames is applied one frame per loop
    turn -- the loop's longest gap is a small fraction of the burst and under
    the watchdog's 1.0 s lag threshold -- and leaves the cache exactly as
    applying the same frames inline does.
"""
from __future__ import annotations

import asyncio
import contextlib
import gc
import json
import random
import sys
import threading
import time

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_owner as O
from sportsassets import pinnapi_probe as PP

WATCHDOG_LAG_S = 1.0


def _market(rng, i):
    return {"key": "s;0;m" if i == 0 else "s;%d;s;%d" % (i % 3, i),
            "type": "moneyline" if i == 0 else "spread", "period": i % 3,
            "status": "open", "version": 3, "isAlternate": i % 7 == 0,
            "prices": [{"designation": "home", "points": 1.5,
                        "price": rng.choice([-150, -120, 105, 130])},
                       {"designation": "away", "points": -1.5,
                        "price": rng.choice([-140, 110, 125, -105])}]}


def _event(rng, eid, n=25):
    return {"id": eid, "type": "matchup", "isLive": False,
            "participants": [{"alignment": "home", "name": "Home %d" % eid},
                             {"alignment": "away", "name": "Away %d" % eid}],
            "startTime": "2026-10-09T18:00:00Z", "units": "Regular",
            "league": {"id": 1, "name": "L éè"}, "version": 3,
            "markets": [_market(rng, i) for i in range(n)]}


def _snapshot(rng, n_events, stream="prematch", sport=1, start=1000):
    return {"type": "snapshot", "stream": stream, "sport_id": sport,
            "ts": 1_791_000_000_000,
            "events": [_event(rng, start + i) for i in range(n_events)]}


# ═════════════════════════════════════════════════════════════════════
# decode_frame IS json.loads
# ═════════════════════════════════════════════════════════════════════

def _rand_value(rng, depth=0):
    k = rng.randrange(9 if depth < 3 else 6)
    if k == 0:
        return None
    if k == 1:
        return rng.choice([True, False])
    if k == 2:
        return rng.randint(-10**12, 10**12)
    if k == 3:
        return rng.choice([0.5, -1e-7, 1.5e300, 3.0, -0.0])
    if k in (4, 5):
        return "".join(rng.choice('ab"\\/\n\té \U0001f600 ,:[]{}')
                       for _ in range(rng.randrange(8)))
    if k in (6, 7):
        return [_rand_value(rng, depth + 1) for _ in range(rng.randrange(5))]
    return {"k%d" % rng.randrange(6): _rand_value(rng, depth + 1)
            for _ in range(rng.randrange(5))}


def _spaced(rng, text):
    """The same JSON with arbitrary JSON whitespace between tokens."""
    out, in_str, esc = [], False, False
    for ch in text:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        if ch in ",:[]{}" and rng.random() < 0.5:
            out.append(rng.choice([" ", "\n", "\t", "\r\n ", ""]))
        out.append(ch)
        if ch in ",:[]{}" and rng.random() < 0.5:
            out.append(rng.choice([" ", "\n", "\t", "  "]))
    return "".join(out)


def _same(raw):
    try:
        want = ("ok", json.loads(raw))
    except Exception as exc:                                    # noqa: BLE001
        want = ("raise", type(exc), str(exc))
    try:
        got = ("ok", O.decode_frame(raw))
    except Exception as exc:                                    # noqa: BLE001
        got = ("raise", type(exc), str(exc))
    assert got == want, raw[:200]
    if want[0] == "ok":
        assert json.dumps(got[1]) == json.dumps(want[1])     # key order too
    return want[0]


def test_decode_frame_is_json_loads_on_production_shaped_frames():
    rng = random.Random(1)
    snap = json.dumps(_snapshot(rng, 400))
    assert len(snap) > O.BIG_FRAME
    assert _same(snap) == "ok"
    pm = {"type": "prematch_markets", "sport_id": 1, "matchup_id": 7,
          "ts": 1, "data": [_market(rng, i) for i in range(60)]}
    assert _same(json.dumps(pm)) == "ok"
    assert _same(json.dumps(pm, indent=2)) == "ok"
    assert _same(json.dumps(pm, ensure_ascii=False).encode("utf-8")) == "ok"
    assert _same(json.dumps(pm).encode("utf-16")) == "ok"


def test_decode_frame_is_json_loads_on_a_fuzzed_corpus():
    rng = random.Random(20261009)
    oks = raises = 0
    for i in range(3000):
        top = {"k%d" % rng.randrange(8): _rand_value(rng)
               for _ in range(rng.randrange(7))}
        if rng.random() < 0.5:
            top["events"] = [_rand_value(rng, 1) for _ in range(
                rng.randrange(6))]
        text = _spaced(rng, json.dumps(top, ensure_ascii=rng.random() < .5))
        if rng.random() < 0.15:                 # a repeated member name
            text = text.rstrip()[:-1].rstrip()
            text += (", " if text.rstrip()[-1] != "{" else "") + \
                '"k1": [1, {"a": 2}]}'
        if rng.random() < 0.2:                  # damaged: json.loads refuses
            pos = rng.randrange(len(text))
            text = text[:pos] + rng.choice(["", ",", "]", "}", "x", '"']) + \
                text[pos + 1:]
        if rng.random() < 0.05:
            text = rng.choice(["  ", "﻿", "[1, 2]", "3", "{} x", "{",
                               '{"a": [1,]}', '{"a": [1 2]}', '{"a" 1}',
                               '{"a": 1,}', '{"a": NaN, "b": [Infinity]}'])
        if _same(text) == "ok":
            oks += 1
        else:
            raises += 1
    assert oks > 2000 and raises > 200, (oks, raises)


# ═════════════════════════════════════════════════════════════════════
# the GIL is never held for a whole big frame
# ═════════════════════════════════════════════════════════════════════

class _Lease:
    async def holds(self):
        return True


class _BurstWS:
    """A socket whose frames are ALL already queued: recv() returns without
    suspending until they run out (websockets' own behaviour), then the
    owner is stopped."""

    def __init__(self, frames, owner):
        self.frames, self.owner = list(frames), owner
        self.sent, self.closed = [], False
        self.drained_at = None

    async def send(self, s):
        self.sent.append(s)

    async def recv(self):
        if self.frames:
            return self.frames.pop(0)
        if self.drained_at is None:
            self.drained_at = time.monotonic()
        self.owner.stop_event.set()
        await asyncio.sleep(0.01)
        raise asyncio.TimeoutError          # nothing more arrived

    async def close(self):
        self.closed = True


def _owner(cache, frames, monkeypatch, clock=None):
    monkeypatch.setenv(PP.KEY_ENV, "k-test")
    holder = {}

    async def connect(url, key):
        holder["ws"] = _BurstWS(frames, holder["owner"])
        return holder["ws"]
    o = O.FeedOwner(cache, sport_ids=[1], lease_factory=None,
                    connect=connect, liveness_s=30.0,
                    clock=clock or time.time)
    holder["owner"] = o
    return o, holder


def test_the_owner_decodes_a_big_frame_without_holding_the_gil(monkeypatch):
    """A production-size snapshot frame (3,000 events of 25 markets, ~16 MB,
    inside WS_MAX_FRAME_BYTES): while the owner's worker thread decodes it,
    a thread that wants the GIL waits under a quarter of what ONE json.loads
    of the frame holds it for -- before, it waited the whole decode. (The
    collector is paused for the measurement: a full collection holds the
    GIL whoever triggers it, and is not what is measured here.)"""
    rng = random.Random(4)
    raw = json.dumps(_snapshot(rng, 3000))
    assert O.BIG_FRAME < len(raw) < PP.WS_MAX_FRAME_BYTES
    windows = []
    real_to_thread = asyncio.to_thread

    async def timed(fn, *a, **k):
        t0 = time.perf_counter()
        try:
            return await real_to_thread(fn, *a, **k)
        finally:
            windows.append((t0, time.perf_counter()))
    monkeypatch.setattr(O.asyncio, "to_thread", timed)
    old = sys.getswitchinterval()
    was_enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    gaps, stop = [], threading.Event()

    def waiter():
        last = time.perf_counter()
        while not stop.is_set():
            time.sleep(0.0005)
            now = time.perf_counter()
            gaps.append((last, now))
            last = now
    cache = F.FeedCache()
    cache.offload_snapshots = False
    o, holder = _owner(cache, [raw], monkeypatch)
    th = threading.Thread(target=waiter)
    try:
        one_call = None
        for _ in range(3):                     # warm, then the shortest
            t0 = time.perf_counter()
            json.loads(raw)
            took = time.perf_counter() - t0
            one_call = took if one_call is None else min(one_call, took)
        sys.setswitchinterval(0.001)           # the API's (api/app.py)
        th.start()
        asyncio.run(o._own(_Lease(), 0))
    finally:
        stop.set()
        if th.is_alive():
            th.join()
        sys.setswitchinterval(old)
        if was_enabled:
            gc.enable()
    assert len(cache.events) == 3000, "the frame was decoded and applied"
    assert len(windows) == 1, "the big frame was decoded off the loop"
    w0, w1 = windows[0]
    during = [b - a for a, b in gaps if b > w0 and a < w1]
    assert during, "the waiter ran while the frame was decoded"
    print("MEASURED longest GIL wait during the decode %.4f s; one "
          "json.loads of the frame %.4f s" % (max(during), one_call))
    assert max(during) < 0.25 * one_call, (max(during), one_call)
    assert max(during) < WATCHDOG_LAG_S


# ═════════════════════════════════════════════════════════════════════
# a burst of queued frames: one frame per loop turn
# ═════════════════════════════════════════════════════════════════════

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


def _burst(rng, n=1300):
    frames = [json.dumps(_snapshot(rng, 50, stream="live")),
              json.dumps(_snapshot(rng, 60, stream="prematch"))]
    for i in range(n):
        frames.append(json.dumps({
            "type": "prematch_markets", "sport_id": 1,
            "matchup_id": 1000 + i % 60 if i % 3 == 0 else 5000 + i,
            "ts": 1_791_000_000_000 + i,
            "data": [_market(rng, j) for j in range(25)]}))
    return frames


def _state(cache):
    return ({k: (q.prices, q.line, q.change_ms, q.observed_change_ms,
                 q.change_clock, q.confirmed_ms, q.confirmed_by, q.epoch,
                 q.first_observed_ms, q.received_ms)
             for k, q in cache.quotes.items()},
            dict(cache.counts), dict(cache.confirmations),
            {k: dict(v) for k, v in cache.events.items()})


def test_a_queued_burst_is_applied_one_frame_per_loop_turn(monkeypatch):
    rng = random.Random(9)
    frames = _burst(rng)
    cache = F.FeedCache()
    o, holder = _owner(cache, frames, monkeypatch,
                       clock=lambda: 1_791_000_000.0)
    stamps = []

    async def go():
        done = asyncio.Event()

        async def ticker():
            # a stamp per loop turn this task gets; one before the burst,
            # and one after it however long the loop was held
            while True:
                stamps.append(time.monotonic())
                if done.is_set():
                    return
                await asyncio.sleep(0.002)
        t = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        t0 = time.monotonic()
        await o._own(_Lease(), 0)
        took = holder["ws"].drained_at - t0
        done.set()
        await t
        return took
    with _collector_paused():
        took = asyncio.run(go())
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    print("MEASURED longest loop gap %.4f s; burst of %d frames %.4f s"
          % (max(gaps), len(frames), took))
    assert holder["ws"].frames == []
    assert max(gaps) < 0.25 * took, (max(gaps), took)
    assert max(gaps) < WATCHDOG_LAG_S

    # the same frames, applied inline in the same order, with the same
    # receipt clock: the same cache
    ref = F.FeedCache()
    ep = ref.new_connection([("live", 1), ("prematch", 1)])
    for raw in frames:
        ref.apply(json.loads(raw), epoch=ep,
                  received_ms=1_791_000_000.0 * 1000.0)
    ref.lost(O.R_SOCKET_CLOSED)
    got, want = _state(cache), _state(ref)
    assert got[0] == want[0] and got[3] == want[3]
    assert got[2] == want[2]
    assert {k: v for k, v in got[1].items() if k != "epochs"} == \
        {k: v for k, v in want[1].items() if k != "epochs"}
    assert len(got[0]) > 20_000
