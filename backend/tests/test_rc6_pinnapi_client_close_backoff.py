"""CAPITAL-CRITICAL: A CLOSE OF OUR OWN THAT KEEPS COMING WAITS LONGER EACH
TIME.

RC6 (rc6/pinnapi-xavier, 412c4962) made a close THIS client initiated (the
websockets keepalive 1011, the frame cap 1009) FEED_SOCKET_CLOSED_BY_THIS_
CLIENT: never an eviction, a reconnect with the ordinary backoff. But the
ordinary backoff resets on every delivered epoch (`attempt = 0`), so a
CLIENT close that repeats AFTER delivery -- a persistent 1009 on a frame
the provider keeps sending, a loop stall that keeps failing the keepalive
-- reconnected about every 1 s forever, each time a new epoch that reloads
every subscribed snapshot, with nothing on the heartbeat saying so.

Pinned here: CLIENT closes are counted in a rolling CLIENT_CLOSE_WINDOW_S
(600 s) of CONNECTED time -- the seconds the owner's sockets were actually
open, summed over epochs, so a slow connect, lease or standby time and any
backoff wait (scheduled, overlong or cut short) are never on that clock;
from the 3rd close inside it the owner waits 5, 10, 20 ... up to 300 s
before reconnecting. Delivery does not reset it; only a quiet window does. A
storm holds 300 s; a close every ~240 s of streaming (2026-10-08's keepalive
cadence) waits a steady 5 s and never starves the feed (integration review
of the first version, which climbed to 300 s, and of the second, which
subtracted the scheduled waits from the monotonic clock).
The wait is named on the heartbeat (`client_close_backoff`), in the
transitions (CLIENT_CLOSE_BACKOFF) and by the authority's reason
(FEED_CLIENT_CLOSE_BACKOFF). It is never a refusal and never counts toward
EVICTIONS_MAX; a stop ends it at once.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
import time

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as RT
from sportsassets import pinnapi_owner as O
from sportsassets import refusal_taxonomy_table as TT

from tests import paper_harness as H
from tests.test_pinnapi_feed_ownership import frames_for, until
from tests.test_rc6_feed_close_provenance import (_GUID, ClosingWS,
                                                  _client_frame, _text_frame)

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

FRAME_CAP = Close(1009, "frame exceeds limit of 33554432 bytes")


# ── the CLIENT-close backoff ──────────────────────────────────────


def test_the_client_close_wait_doubles_from_five_seconds_to_five_minutes():
    assert O.CLIENT_CLOSE_WINDOW_S == 600.0
    assert O.CLIENT_CLOSES_FREE == 2
    assert [O.client_close_wait_s(n) for n in range(10)] == [
        0.0, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 300.0, 300.0, 300.0]


def _bare_owner():
    return O.FeedOwner(F.FeedCache(), sport_ids=[6], lease_factory=None,
                       connect=None)


def test_the_third_client_close_inside_the_window_starts_the_wait():
    o = _bare_owner()
    waits = [o._client_close(float(t)) for t in range(10)]
    assert waits == [0.0, 0.0, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 300.0,
                     300.0]
    assert o.evictions == [] and o.refused is None


def test_the_wait_does_not_collapse_while_the_closes_keep_coming():
    """Each socket closing one second after it opened: the 600 s connected
    window keeps filling, so the backoff keeps its level for as long as the
    closes keep coming (the waits are not on the connected clock)."""
    o = _bare_owner()
    c, waits = 0.0, []
    for _ in range(14):
        waits.append(o._client_close(c))
        c += 1.0
    assert waits[:9] == [0.0, 0.0, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 300.0]
    assert waits[9:] == [300.0] * 5


def test_a_close_every_four_minutes_of_streaming_waits_five_seconds_not_five_minutes():
    """The integration review's case: a CLIENT close after every 240 s of
    connected time (2026-10-08: three keepalive 1011s in 491 s). The first
    version kept escalating while each close came within 600 s of the last
    and climbed to 300 s, so the feed was down 300 of every 540 s. Counted
    on the connected clock the window never holds more than 3 such closes:
    the wait stays at the first step and the feed streams ~98% of the time."""
    o = _bare_owner()
    c, waits = 0.0, []
    for _ in range(30):
        waits.append(o._client_close(c))
        c += 240.0
    assert waits[:2] == [0.0, 0.0]
    assert set(waits[2:]) == {5.0}
    assert len(o.client_closes) <= 3
    assert o.evictions == [] and o.refused is None


def test_the_storm_still_holds_the_cap_while_a_slow_cadence_does_not_climb():
    """Both at once on separate owners: 1 s after each reconnect holds 300 s;
    600 s after each reconnect never waits at all."""
    storm, slow = _bare_owner(), _bare_owner()
    for i in range(20):
        storm._client_close(float(i))
    assert storm.client_close_wait == 300.0
    waits = [slow._client_close(600.0 * i) for i in range(20)]
    assert set(waits) == {0.0}


def test_a_quiet_window_resets_the_backoff_and_delivery_does_not():
    o = _bare_owner()
    for c in (0.0, 1.0, 2.0, 3.0):
        o._client_close(c)
    assert o.client_close_level == 2
    # still inside one connected window of the first close: escalating
    c = O.CLIENT_CLOSE_WINDOW_S - 0.5
    assert o._client_close(c) == 20.0
    assert o.client_close_level == 3
    # a full connected window after the last close: quiet, so no wait, and
    # the window holds this close alone
    c = c + O.CLIENT_CLOSE_WINDOW_S
    assert o._client_close(c) == 0.0
    assert o.client_close_level == 0
    assert o.client_closes == [c]
    # (a delivered epoch is not a quiet window: the persistent-close test
    # below closes after delivery every time and the wait still grows)


@pg
async def test_a_persistent_client_close_after_delivery_waits_longer_each_time(
        monkeypatch):
    """BASE: every reconnect after a delivered epoch waited BACKOFF[0] (1 s)
    -- [1.0, 1.0, 1.0, ...] forever, each a new epoch reloading every
    snapshot."""
    socks: list = []
    o = _owner(monkeypatch, socks, lambda: ClosingWS(
        frames_for(), ConnectionClosedError(None, FRAME_CAP, None)))
    waits, seen = [], []

    async def recorded_wait(s):
        waits.append(s)
        seen.append((o.state, o.cache.authority.reason,
                     dict(o.status().get("client_close_backoff") or {})))
        await asyncio.sleep(0.005)
    monkeypatch.setattr(o, "_wait", recorded_wait)
    t = asyncio.create_task(o.run())
    try:
        assert await until(lambda: len(waits) >= 10, timeout=20), waits
        assert waits[:10] == [1.0, 1.0, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0,
                              300.0, 300.0]
        # never a refusal, never an eviction
        assert o.refused is None and o.evictions == []
        assert o.closes_by_initiator.get(O.CLOSE_CLIENT, 0) >= 10
        assert set(o.closes_by_initiator) == {O.CLOSE_CLIENT}
        # the wait is named: the owner's state and the authority's reason
        # while it waits, the transition, and the heartbeat
        state, reason, bo = seen[2]
        assert state == O.CLIENT_CLOSE_BACKOFF == "CLIENT_CLOSE_BACKOFF"
        assert reason == O.R_CLIENT_CLOSE_BACKOFF
        assert bo["wait_s"] == 5.0 and bo["closes_in_window"] == 3
        assert bo["waiting"] is True and bo["level"] == 1
        assert bo["window_s"] == O.CLIENT_CLOSE_WINDOW_S
        assert bo["until_at"] - bo["since_at"] == pytest.approx(5.0)
        assert seen[0][0] != O.CLIENT_CLOSE_BACKOFF   # the first two: free
        named = [e for e in o.events if e["what"] == O.CLIENT_CLOSE_BACKOFF]
        assert [e["wait_s"] for e in named][:3] == [5.0, 10.0, 20.0]
        assert named[0]["closes_in_window"] == 3
        client = [e for e in o.events if e["what"] == O.R_CLIENT_CLOSED]
        assert client[-1]["close"]["code"] == 1009
        assert client[-1]["close"]["delivered"] is True
        assert client[-1]["close"]["socket_age_s"] >= 0.0
        monkeypatch.setitem(RT._STATE, "owner", o)
        hb = RT.digest()["client_close_backoff"]
        assert hb["level"] >= 7 and hb["wait_s"] == 300.0
        assert hb["current_level"] >= hb["level"]
        json.dumps(RT.digest(), default=str)
    finally:
        o.stop()
        await asyncio.wait_for(t, 10)


@pg
async def test_a_stop_ends_the_client_close_wait_at_once(monkeypatch):
    socks: list = []
    o = _owner(monkeypatch, socks, lambda: ClosingWS(
        frames_for(), ConnectionClosedError(None, FRAME_CAP, None)))
    monkeypatch.setattr(O, "BACKOFF", (0.05,))
    t = asyncio.create_task(o.run())
    try:
        # BASE: no wait, so the owner kept reconnecting every 0.05 s
        assert await until(lambda: o.state == O.CLIENT_CLOSE_BACKOFF,
                           timeout=15), (o.state, len(socks))
        await asyncio.sleep(1.0)
        assert len(socks) == O.CLIENT_CLOSES_FREE + 1, "it waits 5 s"
        t0 = time.monotonic()
        o.stop()
        await asyncio.wait_for(t, 5)
        assert time.monotonic() - t0 < 2.0
        assert o.state == "STOPPED" and o.refused is None
        assert not o.cache.authority.granted
    finally:
        if not t.done():
            o.stop()
            await asyncio.wait_for(t, 10)


# ── the connected clock, through the real owner loop ─────────────────


class _MonoClock:
    """pinnapi_owner's monotonic clock, advanced only by the test (every
    other attribute is the real `time` module)."""

    def __init__(self):
        self.v = 10_000.0

    def monotonic(self):
        return self.v

    def __getattr__(self, name):
        return getattr(time, name)


class _TimedClosingWS(ClosingWS):
    """Delivers its frames, then stays open `life_s` on the owner's clock
    and closes with `exc` (our own frame-cap close)."""

    def __init__(self, frames, exc, clock, life_s):
        super().__init__(frames, exc)
        self.clock, self.life_s = clock, life_s

    async def recv(self):
        if self.frames:
            return json.dumps(self.frames.pop(0))
        self.clock.v += self.life_s
        raise self.exc


def _distorted_owner(monkeypatch, clock, life_s, waits):
    """A real owner whose every non-streaming period is distorted on its
    own clock: the handshake takes 1,000 s, taking the lease 500 s, and the
    backoff waits alternately run three times their scheduled length and
    are cut short to nothing. None of that is connected time."""
    monkeypatch.setattr(O, "time", clock)
    monkeypatch.setenv("pinnapi_key", "k-test")

    async def lease_factory():
        clock.v += 500.0
        return await O.Lease.open(H.DSN)

    async def connect(url, key):
        clock.v += 1_000.0
        return _TimedClosingWS(frames_for(),
                               ConnectionClosedError(None, FRAME_CAP, None),
                               clock, life_s)
    o = O.FeedOwner(F.FeedCache(), sport_ids=[6],
                    lease_factory=lease_factory, connect=connect,
                    liveness_s=0.2, standby_s=0.2)

    async def distorted_wait(s):
        waits.append(s)
        clock.v += 3 * s if len(waits) % 2 else 0.0
        await asyncio.sleep(0.005)
    monkeypatch.setattr(o, "_wait", distorted_wait)
    return o


@pg
async def test_slow_connects_lease_time_and_wrong_length_waits_do_not_thin_a_storm(
        monkeypatch):
    """Sockets that close 1 s after they open, with 1,500 s of handshake and
    lease time around each and waits that never run their scheduled
    length. 0a40533b subtracted only the SCHEDULED waits from the monotonic
    clock, so those 1,500 s put every close in a window of its own and the
    storm never backed off: [1.0, 1.0, 1.0, ...]."""
    clock, waits = _MonoClock(), []
    o = _distorted_owner(monkeypatch, clock, 1.0, waits)
    t = asyncio.create_task(o.run())
    try:
        assert await until(lambda: len(waits) >= 9, timeout=20), waits
        assert waits[:9] == [1.0, 1.0, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0,
                             300.0]
        # the connected clock holds the sockets' open time and nothing else
        n = o.closes_by_initiator[O.CLOSE_CLIENT]
        assert o.connected_s == pytest.approx(1.0 * n, abs=1e-6)
        # each close sits 1 s of connected time after the one before
        assert o.client_closes == pytest.approx([float(i) for i in range(1, n + 1)])
        assert o.refused is None and o.evictions == []
        bo = o.status()["client_close_backoff"]
        assert bo["window_clock"] == "CONNECTED_SOCKET_SECONDS"
    finally:
        o.stop()
        await asyncio.wait_for(t, 10)


@pg
async def test_a_four_minute_cadence_stays_at_five_seconds_whatever_the_gaps_between_sockets(
        monkeypatch):
    """Sockets open 240 s each, with the same distorted gaps between them:
    the connected window holds at most 3 closes, so the wait is 5 s from the
    3rd on, exactly as without the gaps. 0a40533b put each close ~1,740 s
    apart and never waited at all: [1.0, 1.0, 1.0, ...]."""
    clock, waits = _MonoClock(), []
    o = _distorted_owner(monkeypatch, clock, 240.0, waits)
    t = asyncio.create_task(o.run())
    try:
        assert await until(lambda: len(waits) >= 8, timeout=20), waits
        assert waits[:8] == [1.0, 1.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0]
        n = o.closes_by_initiator[O.CLOSE_CLIENT]
        assert o.connected_s == pytest.approx(240.0 * n, abs=1e-6)
        assert len(o.client_closes) <= 3
        assert o.refused is None and o.evictions == []
    finally:
        o.stop()
        await asyncio.wait_for(t, 10)


# ── the REAL websockets client: a frame over the cap, every time ──────


async def _oversize_provider(reader, writer):
    """Completes the upgrade, sends the subscribe snapshots and a change,
    then one frame larger than the client's max_size -- every connection,
    the way a provider frame that outgrew our cap would. Echoes a close
    frame and ends, as a provider does."""
    try:
        req = await reader.readuntil(b"\r\n\r\n")
        key = next(line.split(b":", 1)[1].strip()
                   for line in req.split(b"\r\n")
                   if line.lower().startswith(b"sec-websocket-key:"))
        accept = base64.b64encode(hashlib.sha1(key + _GUID).digest())
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\n"
                     b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        for f in frames_for():
            writer.write(_text_frame(f))
        await writer.drain()
        await asyncio.sleep(0.05)
        writer.write(_text_frame({"type": "pad", "x": "y" * 20000}))
        await writer.drain()
        while True:
            op, data = await _client_frame(reader)
            if op == 0x8:
                writer.write(struct.pack("!BB", 0x88, len(data)) + data)
                await writer.drain()
                return
    except Exception:                                           # noqa: BLE001
        pass
    finally:
        writer.close()


@pg
async def test_the_real_frame_cap_close_backs_off_instead_of_a_reconnect_storm(
        monkeypatch):
    from websockets.asyncio.client import connect as ws_connect
    server = await asyncio.start_server(_oversize_provider, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("pinnapi_key", "k-test")
    opened: list = []

    async def lease_factory():
        return await O.Lease.open(H.DSN)

    async def connect(url, key):
        ws = await ws_connect("ws://127.0.0.1:%d" % port, max_size=4096,
                              proxy=None)
        opened.append(ws)
        return ws
    o = O.FeedOwner(F.FeedCache(), sport_ids=[6],
                    lease_factory=lease_factory, connect=connect,
                    liveness_s=0.5, standby_s=0.2)
    waits: list = []

    async def recorded_wait(s):
        waits.append(s)
        await asyncio.sleep(0.005)
    monkeypatch.setattr(o, "_wait", recorded_wait)
    t = asyncio.create_task(o.run())
    try:
        assert await until(lambda: len(waits) >= 5, timeout=20), (
            o.state, o.events[-4:])
        last = o.last_close
        assert last["initiator"] == O.CLOSE_CLIENT, last
        assert last["code"] == 1009, last
        assert last["delivered"] is True
        # BASE: [1.0, 1.0, 1.0, 1.0, 1.0]
        assert waits[:5] == [1.0, 1.0, 5.0, 10.0, 20.0]
        assert o.refused is None and o.evictions == []
    finally:
        o.stop()
        await asyncio.wait_for(t, 10)
        server.close()
        await server.wait_closed()


def test_the_new_code_is_classified_like_the_close_it_follows():
    cls, fam, stage = TT.TABLE[O.R_CLIENT_CLOSE_BACKOFF]
    assert O.R_CLIENT_CLOSE_BACKOFF == "FEED_CLIENT_CLOSE_BACKOFF"
    assert (cls, fam, stage) == TT.TABLE[O.R_CLIENT_CLOSED]


# ── helpers ───────────────────────────────────────────────────────────


def _owner(monkeypatch, sockets, make_ws):
    monkeypatch.setenv("pinnapi_key", "k-test")

    async def lease_factory():
        return await O.Lease.open(H.DSN)

    async def connect(url, key):
        w = make_ws()
        sockets.append(w)
        return w
    return O.FeedOwner(F.FeedCache(), sport_ids=[6],
                       lease_factory=lease_factory, connect=connect,
                       liveness_s=0.2, standby_s=0.2)
