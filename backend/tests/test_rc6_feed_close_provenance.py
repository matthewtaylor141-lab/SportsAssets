"""CAPITAL-CRITICAL: A CLOSE THIS CLIENT MADE IS NEVER AN EVICTION.

Production 2026-10-08 (research-sql run 37839436591): after the 18:39:27Z API
restart the PinnAPI owner counted three unrequested closes inside 600 s,
the third at 19:00:51Z, refused itself FEED_EVICTION_LOOP_SUSPECTED and
never contended again: no backend held the feed lease, loop health read
pinnapi_feed.heartbeat UNHEALTHY (WRITER_LOCK_HELD_BY_NO_BACKEND), and every
held read answered FEED_OWNERSHIP_NOT_HELD (0 of 3 held packets current).
Nothing recorded who closed those sockets.

An eviction is the PROVIDER closing our older socket for a newer one on the
same key. A close the websockets library itself makes -- its keepalive ping
unanswered within ping_timeout (an event loop that stalls past it cannot
read the pong), a frame over max_size -- is ours, and stopping the feed for
the life of the process over it is the defect. Pinned here:

  * `close_of` reads the side, the code and the reason from the library's
    own record of the closing handshake (real websockets exceptions);
  * through the REAL websockets client against a provider that never
    answers a ping (the client-side keepalive close, 1011), the owner
    reconnects with its backoff instead of stopping, and names the close;
  * a close the SERVER made, or one with no close frame at all, still
    counts toward EVICTIONS_MAX exactly as before: the owner still never
    fights another holder of the key.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_owner as O
from sportsassets import refusal_taxonomy_table as RT

from tests import paper_harness as H
from tests.test_pinnapi_feed_ownership import FakeWS, frames_for, until

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

KEEPALIVE = Close(1011, "keepalive ping timeout")


# ── who closed it: the library's own record, read exactly ─────────────
def test_the_librarys_own_keepalive_close_is_this_client():
    got = O.close_of(ConnectionClosedError(None, KEEPALIVE, None))
    assert got["initiator"] == O.CLOSE_CLIENT
    assert got["code"] == 1011 and got["reason"] == "keepalive ping timeout"
    assert got["error"] == "ConnectionClosedError"
    # a frame over our max_size: also ours
    big = O.close_of(ConnectionClosedError(
        None, Close(1009, "frame exceeds limit of 33554432 bytes"), None))
    assert big["initiator"] == O.CLOSE_CLIENT and big["code"] == 1009
    # we closed first and the provider echoed: still ours
    echo = O.close_of(ConnectionClosedOK(Close(1000, ""), Close(1000, ""),
                                         False))
    assert echo["initiator"] == O.CLOSE_CLIENT


def test_a_close_the_provider_made_or_none_at_all_stays_eviction_consistent():
    srv = O.close_of(ConnectionClosedOK(Close(4000, "replaced"),
                                        Close(4000, "replaced"), True))
    assert srv["initiator"] == O.CLOSE_SERVER and srv["code"] == 4000
    assert srv["reason"] == "replaced"
    only = O.close_of(ConnectionClosedError(Close(1008, "x"), None, None))
    assert only["initiator"] == O.CLOSE_SERVER
    gone = O.close_of(ConnectionClosedError(None, None, None))
    assert gone["initiator"] == O.CLOSE_NO_FRAME
    assert gone["code"] == O.ABNORMAL_CLOSURE == 1006
    other = O.close_of(ConnectionError("reset"))
    assert other["initiator"] == O.CLOSE_UNKNOWN and other["code"] is None
    assert set(O.EVICTION_CONSISTENT) == {O.CLOSE_SERVER, O.CLOSE_NO_FRAME,
                                          O.CLOSE_UNKNOWN}
    assert O.CLOSE_CLIENT not in O.EVICTION_CONSISTENT


def test_the_peer_reason_is_bounded_and_printable():
    got = O.close_of(ConnectionClosedOK(
        Close(4001, "r\x00\x07" + "y" * 200), None, None))
    assert len(got["reason"]) <= O.CLOSE_REASON_MAX
    assert got["reason"].isprintable()


def test_the_new_code_is_classified_software():
    cls, _family, stage = RT.TABLE[O.R_CLIENT_CLOSED]
    assert O.R_CLIENT_CLOSED == "FEED_SOCKET_CLOSED_BY_THIS_CLIENT"
    assert cls == RT.TABLE["FEED_SOCKET_CLOSED"][0]
    assert stage == "INGESTION"


# ── the owner: real lease, the close as the library raises it ─────────
class ClosingWS(FakeWS):
    """Delivers its frames, then recv raises `exc` (a real websockets
    ConnectionClosed with its handshake record)."""

    def __init__(self, frames, exc):
        super().__init__(frames)
        self.exc = exc

    async def recv(self):
        if self.frames:
            return json.dumps(self.frames.pop(0))
        raise self.exc


def _owner(monkeypatch, sockets, make_ws):
    monkeypatch.setenv("pinnapi_key", "k-test")
    monkeypatch.setattr(O, "BACKOFF", (0.05,))

    async def lease_factory():
        return await O.Lease.open(H.DSN)

    async def connect(url, key):
        w = make_ws()
        sockets.append(w)
        return w
    return O.FeedOwner(F.FeedCache(), sport_ids=[6],
                       lease_factory=lease_factory, connect=connect,
                       liveness_s=0.2, standby_s=0.2)


@pg
async def test_our_own_keepalive_close_reconnects_instead_of_stopping_for_good(
        monkeypatch):
    socks: list = []
    o = _owner(monkeypatch, socks, lambda: ClosingWS(
        frames_for(), ConnectionClosedError(None, KEEPALIVE, None)))
    t = asyncio.create_task(o.run())
    try:
        # BASE: the third close stopped the owner for good
        # (EVICTION_LOOP_SUSPECTED) and no fourth socket was ever opened
        assert await until(lambda: len(socks) >= O.EVICTIONS_MAX + 2,
                           timeout=15), (o.state, o.refused, len(socks))
        assert o.refused is None and o.state != "EVICTION_LOOP_SUSPECTED"
        assert o.evictions == [], "our own close is never an eviction"
        assert o.closes_by_initiator.get(O.CLOSE_CLIENT, 0) >= \
            O.EVICTIONS_MAX + 1
        last = o.last_close
        assert last["initiator"] == O.CLOSE_CLIENT and last["code"] == 1011
        assert last["reason"] == "keepalive ping timeout"
        assert last["delivered"] is True
        named = [e for e in o.events if e["what"] == O.R_CLIENT_CLOSED]
        assert named and named[-1]["close"]["code"] == 1011
        st = o.status()
        assert st["unrequested_closes_by_initiator"][O.CLOSE_CLIENT] >= 3
        assert st["last_unrequested_close"]["initiator"] == O.CLOSE_CLIENT
        # the closed epoch's authority is revoked: nothing stale is served
        assert not o.cache.authority.synced or o.state == "OWNER_SYNCED"
    finally:
        o.stop()
        await asyncio.wait_for(t, 10)


@pg
async def test_a_provider_close_still_stops_at_the_threshold_and_is_named(
        monkeypatch):
    socks: list = []
    o = _owner(monkeypatch, socks, lambda: ClosingWS(
        frames_for(), ConnectionClosedOK(Close(4000, "replaced"),
                                         Close(4000, "replaced"), True)))
    await asyncio.wait_for(o.run(), 15)
    assert o.state == "EVICTION_LOOP_SUSPECTED"
    assert o.refused == O.R_EVICTION_LOOP
    assert len(socks) == O.EVICTIONS_MAX, "never fights another holder"
    closes = [e for e in o.events if e["what"] == "UNREQUESTED_CLOSE"]
    assert len(closes) == O.EVICTIONS_MAX
    assert closes[-1]["close"]["initiator"] == O.CLOSE_SERVER
    assert closes[-1]["close"]["code"] == 4000
    assert o.cache.read(1, "s;0;m")["ok"] is False


@pg
async def test_a_close_with_no_close_frame_still_counts_toward_the_threshold(
        monkeypatch):
    socks: list = []
    o = _owner(monkeypatch, socks, lambda: ClosingWS(
        frames_for(), ConnectionClosedError(None, None, None)))
    await asyncio.wait_for(o.run(), 15)
    assert o.state == "EVICTION_LOOP_SUSPECTED"
    assert len(socks) == O.EVICTIONS_MAX
    assert o.last_close["initiator"] == O.CLOSE_NO_FRAME
    assert o.last_close["code"] == 1006


# ── the REAL websockets client: a ping nobody answers ─────────────────
_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _text_frame(obj) -> bytes:
    data = json.dumps(obj).encode()
    n = len(data)
    if n < 126:
        head = struct.pack("!BB", 0x81, n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x81, 126, n)
    else:
        head = struct.pack("!BBQ", 0x81, 127, n)
    return head + data


async def _client_frame(reader) -> tuple:
    """(opcode, payload) of one masked client frame."""
    b0, b1 = await reader.readexactly(2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack("!H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if b1 & 0x80 else b"\0\0\0\0"
    data = bytes(c ^ mask[i % 4] for i, c in enumerate(
        await reader.readexactly(n)))
    return b0 & 0x0F, data


async def _silent_provider(reader, writer):
    """A minimal WebSocket server that completes the upgrade, sends the
    subscribe snapshots and a change, then reads the client's frames and
    NEVER ANSWERS A PING. It answers a close frame the way a provider does:
    echoes it and ends the TCP connection. The client's own keepalive must
    therefore fail the connection (1011) -- the client-side close."""
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
        while True:
            op, data = await _client_frame(reader)
            if op == 0x8:                           # close: echo and end
                writer.write(struct.pack("!BB", 0x88, len(data)) + data)
                await writer.drain()
                return
            # 0x9 ping: deliberately unanswered; 0x1 subscribe/pong: read
    except Exception:                                           # noqa: BLE001
        pass
    finally:
        writer.close()


@pg
async def test_the_real_keepalive_close_is_named_ours_and_the_owner_reconnects(
        monkeypatch):
    from websockets.asyncio.client import connect as ws_connect
    server = await asyncio.start_server(_silent_provider, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("pinnapi_key", "k-test")
    monkeypatch.setattr(O, "BACKOFF", (0.05,))
    opened: list = []

    async def lease_factory():
        return await O.Lease.open(H.DSN)

    async def connect(url, key):
        # the production connector's library, its keepalive shortened so
        # the unanswered ping fails the connection inside the test
        ws = await ws_connect("ws://127.0.0.1:%d" % port, ping_interval=0.2,
                              ping_timeout=0.3, proxy=None)
        opened.append(ws)
        return ws
    o = O.FeedOwner(F.FeedCache(), sport_ids=[6],
                    lease_factory=lease_factory, connect=connect,
                    liveness_s=0.5, standby_s=0.2)
    t = asyncio.create_task(o.run())
    try:
        assert await until(lambda: len(opened) >= O.EVICTIONS_MAX + 1,
                           timeout=20), (o.state, o.refused, o.events[-4:])
        # what the library raised, as the owner recorded it
        last = o.last_close
        assert last["initiator"] == O.CLOSE_CLIENT, last
        assert last["code"] == 1011, last
        assert last["reason"] == "keepalive ping timeout"
        assert o.refused is None and o.evictions == []
        assert await until(lambda: o.state == "OWNER_SYNCED"
                           or len(opened) >= O.EVICTIONS_MAX + 2, timeout=10)
    finally:
        o.stop()
        await asyncio.wait_for(t, 10)
        server.close()
        await server.wait_closed()
