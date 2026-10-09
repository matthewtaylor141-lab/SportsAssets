"""CAPITAL-CRITICAL: AN AMBIGUOUS CLOSE IS EVICTION-CONSISTENT, AND THE ONE
CLOSE RECORD CARRIES THE CLOSE'S OWN FACTS.

RC6 (rc6/pinnapi-xavier, 412c4962) classifies every unrequested close from
the websockets library's own record of the closing handshake (`close_of`):
SERVER, CLIENT, NO_CLOSE_FRAME or UNKNOWN; a CLIENT close is ours and never
counts toward EVICTIONS_MAX. Two gaps, pinned here:

  1. when the library recorded BOTH close frames but not their order
     (rcvd_then_sent None) the base called it CLIENT -- so a provider close
     frame that may have been an eviction never counted and the owner could
     fight another holder of the key indefinitely. It is UNKNOWN now
     (EVICTION_CONSISTENT), carrying the provider's own code and reason;
  2. the close record also carries the receive gap and the socket's age
     (rc6/pipeline-reds abf4ea43: production 2026-10-08, three closes counted
     as evictions with nothing recorded but a count), in the SAME record
     close_of builds -- the transition's `close`, `last_unrequested_close`,
     the heartbeat -- never a second, parallel one.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_owner as O

from tests import paper_harness as H
from tests.test_pinnapi_feed_ownership import frames_for
from tests.test_rc6_feed_close_provenance import ClosingWS

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


# ── 1. an ambiguous close is eviction-consistent ──────────────────────
class _Recorded:
    """A websockets-shaped close record: both frames, order unknown."""

    def __init__(self, rcvd, sent, rcvd_then_sent=None):
        self.rcvd, self.sent, self.rcvd_then_sent = rcvd, sent, rcvd_then_sent


def test_both_close_frames_with_no_order_is_unknown_never_client():
    amb = O.close_of(_Recorded(Close(4000, "replaced"), Close(1000, "")))
    # BASE: CLIENT -- a provider close frame present, yet never counted
    assert amb["initiator"] == O.CLOSE_UNKNOWN
    assert amb["initiator"] in O.EVICTION_CONSISTENT
    # the provider's own frame is what an eviction would say: kept
    assert amb["code"] == 4000 and amb["reason"] == "replaced"
    # the real library exception, its order lost after the fact
    exc = ConnectionClosedError(Close(4000, "x"), Close(1000, ""), True)
    exc.rcvd_then_sent = None
    assert O.close_of(exc)["initiator"] == O.CLOSE_UNKNOWN
    # every ORDERED record classifies exactly as before
    assert O.close_of(_Recorded(Close(4000, "r"), Close(4000, "r"), True))[
        "initiator"] == O.CLOSE_SERVER
    assert O.close_of(_Recorded(Close(1000, ""), Close(1009, "cap"), False))[
        "initiator"] == O.CLOSE_CLIENT
    assert O.close_of(_Recorded(None, Close(1011, "keepalive")))[
        "initiator"] == O.CLOSE_CLIENT
    assert O.close_of(_Recorded(Close(1008, "x"), None))[
        "initiator"] == O.CLOSE_SERVER
    assert O.close_of(_Recorded(None, None))["initiator"] == O.CLOSE_NO_FRAME


@pg
async def test_an_ambiguous_close_after_delivery_counts_toward_the_limit(
        monkeypatch):
    socks: list = []

    def amb():
        exc = ConnectionClosedError(Close(4000, "replaced"), Close(1000, ""),
                                    True)
        exc.rcvd_then_sent = None
        return ClosingWS(frames_for(), exc)
    o = _owner(monkeypatch, socks, amb)
    monkeypatch.setattr(O, "BACKOFF", (0.05,))
    # BASE: CLIENT, so the owner reconnected forever and this never ended
    await asyncio.wait_for(o.run(), 15)
    assert o.refused == O.R_EVICTION_LOOP
    assert o.state == "EVICTION_LOOP_SUSPECTED"
    assert len(socks) == O.EVICTIONS_MAX
    assert o.last_close["initiator"] == O.CLOSE_UNKNOWN
    assert o.closes_by_initiator == {O.CLOSE_UNKNOWN: O.EVICTIONS_MAX}


# ── 2. one close record, with its facts ───────────────────────────────


def test_the_close_record_carries_the_receive_gap_and_the_socket_age():
    got = O.close_of(ConnectionClosedError(None, None, None), now=110.0,
                     last_rx=109.5, opened=10.0)
    assert got == {"error": "ConnectionClosedError",
                   "initiator": O.CLOSE_NO_FRAME,
                   "code": O.ABNORMAL_CLOSURE, "reason": None,
                   "rx_gap_s": 0.5, "socket_age_s": 100.0}
    srv = O.close_of(ConnectionClosedOK(Close(4000, "replaced"),
                                        Close(4000, "replaced"), True),
                     now=200.0, last_rx=170.0, opened=20.0)
    assert srv["initiator"] == O.CLOSE_SERVER and srv["code"] == 4000
    assert srv["rx_gap_s"] == 30.0 and srv["socket_age_s"] == 180.0
    # a non-websockets error and an unknown clock: the facts it has
    other = O.close_of(ConnectionError("reset"), now=5.0, last_rx=6.0,
                       opened=1.0)
    assert other["initiator"] == O.CLOSE_UNKNOWN
    assert other["rx_gap_s"] == 0.0 and other["socket_age_s"] == 4.0
    bare = O.close_of(ConnectionError("reset"))
    assert bare["rx_gap_s"] is None and bare["socket_age_s"] is None


@pg
async def test_every_unrequested_close_carries_its_facts_in_the_one_record(
        monkeypatch):
    socks: list = []
    o = _owner(monkeypatch, socks, lambda: ClosingWS(
        frames_for(), ConnectionClosedError(None, None, None)))
    monkeypatch.setattr(O, "BACKOFF", (0.05,))
    await asyncio.wait_for(o.run(), 15)
    assert o.refused == O.R_EVICTION_LOOP
    closes = [e for e in o.events if e["what"] == "UNREQUESTED_CLOSE"]
    assert [e["recent"] for e in closes] == [1, 2, 3]
    for e in closes:
        # ONE record per close, under `close`: no parallel record beside it
        assert set(e) == {"at", "what", "recent", "close"}
        c = e["close"]
        assert c["initiator"] == O.CLOSE_NO_FRAME and c["code"] == 1006
        assert c["rx_gap_s"] >= 0.0 and c["socket_age_s"] >= c["rx_gap_s"]
        assert c["delivered"] is True and "epoch" in c
    assert o.last_close["socket_age_s"] >= 0.0
    st = o.status()
    assert st["last_unrequested_close"]["rx_gap_s"] >= 0.0
    json.dumps(st)


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
