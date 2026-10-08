"""Every PinnAPI UNREQUESTED_CLOSE carries the close's own facts.

PRODUCTION (research-sql 37846538322, p0_first_loss_plumbing.sql, read
2026-10-08T21:24Z, release 69a8a07e). The API's PinnAPI owner read
EVICTION_LOOP_SUSPECTED from 19:00:51Z after three unrequested closes inside
600 s (~18:52Z, 18:55:40Z, 19:00:51Z), each after minutes of healthy
streaming -- recorded as nothing but a count ("recent": 2, 3). No session
held the feed lease at 21:24Z and only ext_pinnacle_loop starts an owner,
so "another holder of the account key" was an inference the record could
not check. Each close now carries the provider's close frame (code, reason)
or that none came, the receive gap and the socket's age. Evidence only: the
rule (stop at EVICTIONS_MAX inside EVICTION_WINDOW_S) is unchanged.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as RT
from sportsassets import pinnapi_owner as O

from tests import paper_harness as H
from tests.test_pinnapi_feed_ownership import FakeWS, frames_for
from tests.test_pinnapi_feed_runtime import Pool, _owner, _set

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


# ── 1. the close's own facts ─────────────────────────────────────────


def test_a_provider_close_frame_is_recorded_by_code_and_reason():
    exc = ConnectionClosedError(Close(4001, "replaced by a newer connection"),
                                None)
    got = O.close_facts(exc, now=110.0, last_rx=109.5, opened=10.0)
    assert got == {"close_error": "ConnectionClosedError",
                   "close_code": 4001,
                   "close_reason": "replaced by a newer connection",
                   "close_frame": "RECEIVED",
                   "rx_gap_s": 0.5, "socket_age_s": 100.0}


def test_a_close_without_a_frame_says_none_came():
    got = O.close_facts(ConnectionClosedError(None, None), now=200.0,
                        last_rx=170.0, opened=20.0)
    assert got["close_frame"] == O.CLOSE_FRAME_NONE == "NONE_RECEIVED"
    assert got["close_code"] is None and got["close_reason"] is None
    assert got["rx_gap_s"] == 30.0 and got["socket_age_s"] == 180.0


def test_a_non_websocket_error_is_named_and_the_reason_is_bounded():
    got = O.close_facts(ConnectionError("x"), now=1.0, last_rx=1.0,
                        opened=0.0)
    assert got["close_error"] == "ConnectionError"
    assert got["close_frame"] is None and got["close_code"] is None
    long = ConnectionClosedError(Close(1011, "r" * 500), None)
    assert len(O.close_facts(long, now=1, last_rx=1, opened=0)
               ["close_reason"]) == O.CLOSE_REASON_MAX


class ClosedWithoutFrameWS(FakeWS):
    """Delivers its frames, then the connection ends with no close frame
    -- the shape of a liveness close, not of a named eviction."""

    async def recv(self):
        if self.frames:
            return json.dumps(self.frames.pop(0))
        raise ConnectionClosedError(None, None)


@pg
async def test_every_unrequested_close_carries_its_facts(monkeypatch):
    conn = await H.connect()
    pool = Pool(conn)
    try:
        await _set(conn, RT.CONTROL_KEY, True)
        monkeypatch.setattr(O, "BACKOFF", (0.05,))
        socks, cache = [], F.FeedCache()
        o = _owner(cache, socks, pool, monkeypatch,
                   ws=lambda: ClosedWithoutFrameWS(frames_for()))
        await asyncio.wait_for(o.run(), 15)
        # THE RULE IS UNCHANGED INSIDE A RUN: stop at the threshold
        assert o.state == "EVICTION_LOOP_SUSPECTED"
        assert o.refused == O.R_EVICTION_LOOP
        assert len(socks) == O.EVICTIONS_MAX
        closes = [e for e in o.events if e["what"] == "UNREQUESTED_CLOSE"]
        assert [e["recent"] for e in closes] == [1, 2, 3]
        for e in closes:
            assert e["close_error"] == "ConnectionClosedError"
            assert e["close_frame"] == "NONE_RECEIVED"
            assert e["close_code"] is None
            assert e["socket_age_s"] >= 0 and e["rx_gap_s"] >= 0
        # the heartbeat payload stays bounded JSON
        json.dumps(o.status())
    finally:
        await _set(conn, RT.CONTROL_KEY, None)
        await conn.close()
