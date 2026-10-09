"""KALSHI WEBSOCKET SEQUENCING AND RECONNECT RECOVERY (RC6 lane K).

The venue's contract (research/kalshi_canonical_venue/REP_PRODUCTION_
CONTRACT_2026-10-07/docs/websockets_orderbook-updates.md): every message of
a subscription -- the `orderbook_snapshot` of each market AND every
`orderbook_delta` -- carries the subscription's `sid` and a per-sid `seq`
"that should be checked if you want to guarantee you received all the
messages. Used for snapshot/delta consistency". A duplicate subscribe is not
refused (error code 6 "already subscribed" is retired): it opens a SECOND
sid for the same market.

Production RC5 (pm-acceptance 37836393458, venues.json KALSHI_HEALTH.
mechanism.ws): connections 10, resubscribes 18 -- every reconnect re-sent a
subscribe for every market on top of the session's own subscribe (the
disconnect left every book in the resubscribe set), so each reconnect
doubled the subscriptions and counted a resubscribe that no gap caused.

Regressions (each fails on 412c4962):
  * a SNAPSHOT whose seq skips on a live sid means messages of the sid were
    lost: every book of the sid goes GAP (the snapshot used to be applied and
    the sid's sequence silently re-based, so a lost delta of another market
    stayed CURRENT);
  * a sid that has gapped is dead: a later snapshot on it (a market of the
    same subscribe whose snapshot was still in flight) never makes a book
    CURRENT on a subscription that is being torn down -- it is resubscribed;
  * a gap (or error) on a retired sid never gaps a book that is CURRENT on
    its new sid;
  * the per-sid sequence advances on every message of the sid, so a delta
    for a market whose book now lives on another sid cannot fake a gap for
    the markets that remain on it;
  * a reconnect subscribes each wanted market ONCE on the new connection
    and every book returns to CURRENT only on its fresh snapshot, through
    Subscriber.run as production runs it.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from sportsassets import kalshi_ws as KWS

NOW = 1_791_500_000.0


def snap(sid, seq, t, yes=(("0.43", "100"),), no=(("0.55", "80"),)):
    return {"type": "orderbook_snapshot", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "market_id": "u-" + t,
                    "yes_dollars_fp": [list(x) for x in yes],
                    "no_dollars_fp": [list(x) for x in no]}}


def delta(sid, seq, t, price="0.44", d="5", side="yes"):
    return {"type": "orderbook_delta", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "market_id": "u-" + t,
                    "price_dollars": price, "delta_fp": d, "side": side}}


def subscribed(cmd_id, sid):
    return {"type": "subscribed", "id": cmd_id,
            "msg": {"channel": "orderbook_delta", "sid": sid}}


def books():
    b = KWS.WsBooks(clock=lambda: NOW)
    b.on_connected()
    return b


# ── per-sid sequence: snapshots are in it too ───────────────────────────

def test_a_snapshot_whose_seq_skips_gaps_every_book_of_the_sid():
    b = books()
    assert b.on_message(snap(7, 1, "K-A")) == "SNAPSHOT"
    # seq 2 (say, a delta of K-A) was lost; K-B's snapshot arrives as seq 3
    assert b.on_message(snap(7, 3, "K-B")) == "GAP"
    for t in ("K-A", "K-B"):
        cur = b.current(t)
        assert not cur["ok"] and cur["state"] == KWS.GAP
        assert cur["why"] == KWS.R_SEQ_GAP
    assert set(b.resubscribe) == {"K-A", "K-B"}
    # nothing later on the broken sid resurrects either book
    assert b.on_message(delta(7, 4, "K-A")) != "DELTA"
    assert not b.current("K-A")["ok"]


def test_snapshots_in_sequence_on_one_sid_stay_current():
    b = books()
    assert b.on_message(snap(7, 1, "K-A")) == "SNAPSHOT"
    assert b.on_message(snap(7, 2, "K-B")) == "SNAPSHOT"
    assert b.on_message(delta(7, 3, "K-A")) == "DELTA"
    assert b.on_message(delta(7, 4, "K-B")) == "DELTA"
    assert b.current("K-A")["ok"] and b.current("K-B")["ok"]
    assert b.stats["gaps"] == 0


def test_a_snapshot_on_a_sid_that_already_gapped_never_makes_a_book_current():
    b = books()
    b.on_message(snap(7, 1, "K-A"))
    assert b.on_message(delta(7, 3, "K-A")) == "GAP"
    # K-C was in the same subscribe; its snapshot was still in flight
    out = b.on_message(snap(7, 4, "K-C"))
    assert out != "SNAPSHOT"
    cur = b.current("K-C")
    assert not cur["ok"] and cur["book"] is None
    # the subscription is being torn down: K-C is resubscribed with K-A
    assert {"K-A", "K-C"} <= set(b.resubscribe)


def test_a_gap_on_a_retired_sid_never_gaps_a_book_current_on_its_new_sid():
    b = books()
    b.on_message(snap(3, 1, "K-A"))
    assert b.on_message(delta(3, 3, "K-A")) == "GAP"
    b.resubscribe.clear()                       # the subscriber resubscribed
    assert b.on_message(snap(4, 1, "K-A")) == "SNAPSHOT"
    assert b.current("K-A")["ok"]
    # a late error / delta on the retired sid 3
    b.on_message({"type": "error", "sid": 3, "id": 9,
                  "msg": {"code": 7, "msg": "Unknown subscription ID"}})
    b.on_message(delta(3, 9, "K-A"))
    assert b.current("K-A")["ok"], b.current("K-A")
    assert "K-A" not in b.resubscribe


def test_the_sid_sequence_advances_for_a_market_whose_book_moved_sid():
    b = books()
    b.on_message(snap(1, 1, "K-A"))
    b.on_message(snap(1, 2, "K-B"))
    # K-A also arrives on a second subscription (sid 2): its book moves
    b.on_message(snap(2, 1, "K-A"))
    # sid 1 keeps sending K-A's deltas, then K-B's: no message of sid 1 is
    # missing, so K-B is in sequence
    b.on_message(delta(1, 3, "K-A"))
    assert b.on_message(delta(1, 4, "K-B")) == "DELTA"
    assert b.current("K-B")["ok"] and b.current("K-A")["ok"]
    assert b.stats["gaps"] == 0


# ── the subscriber: gap -> resubscribe; reconnect -> one subscribe ──────

class FakeWS:
    """Scripted socket; `on_empty` runs when the script is exhausted (the
    books are inspected there, while the session is still connected)."""

    def __init__(self, script, on_empty=None):
        self.script = list(script)
        self.sent = []
        self.closed = False
        self.on_empty = on_empty

    async def send(self, s):
        self.sent.append(json.loads(s))

    async def recv(self):
        if not self.script:
            if self.on_empty is not None:
                self.on_empty()
                self.on_empty = None
            raise ConnectionError("closed")
        return json.dumps(self.script.pop(0))

    async def close(self):
        self.closed = True


def _subs(ws):
    return [m for m in ws.sent if m["cmd"] == "subscribe"]


def test_a_reconnect_subscribes_each_wanted_market_once():
    seen = {}
    ws1 = FakeWS([subscribed(1, 5), snap(5, 1, "K-A"), snap(5, 2, "K-B")])
    ws2 = FakeWS([subscribed(1, 1), snap(1, 1, "K-A"), snap(1, 2, "K-B"),
                  delta(1, 3, "K-A")],
                 on_empty=lambda: seen.update(
                     a=b.current("K-A"), bb=b.current("K-B")))
    sockets = [ws1, ws2]

    async def connect():
        return sockets.pop(0)
    b = KWS.WsBooks(clock=lambda: NOW)
    sub = KWS.Subscriber(connect, b, wanted=lambda: ["K-A", "K-B"],
                         clock=lambda: NOW)

    async def go():
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await sub.session()
    asyncio.run(go())
    # each connection: ONE subscribe of the wanted markets, nothing else
    for ws in (ws1, ws2):
        assert [m["cmd"] for m in ws.sent] == ["subscribe"], ws.sent
        assert _subs(ws)[0]["params"]["market_tickers"] == ["K-A", "K-B"]
    # no gap happened: no resubscribe is counted
    assert sub.resubscribes == 0 and b.stats["gaps"] == 0
    # CURRENT again on the new connection, from its fresh snapshots
    assert seen["a"]["ok"] and seen["bb"]["ok"]
    assert b.stats["disconnects"] == 2


def test_reconnect_recovery_through_run():
    """Subscriber.run as production runs it: the first connection drops,
    the runner reconnects after its backoff, each book is CURRENT again
    only after the new connection's snapshot."""
    seen = {}
    stop = None
    ws1 = FakeWS([subscribed(1, 2), snap(2, 1, "K-A"),
                  delta(2, 2, "K-A")])

    def at_end():
        seen["cur"] = b.current("K-A")
        seen["state_before_snapshot"] = mid.get("state")
        stop.set()
    mid = {}
    ws2 = FakeWS([subscribed(1, 1)], on_empty=None)
    ws2.script.append(snap(1, 1, "K-A", yes=(("0.47", "10"),)))
    ws2.on_empty = at_end
    sockets = [ws1, ws2]
    b = KWS.WsBooks(clock=lambda: NOW)

    async def connect():
        if len(sockets) == 1:
            # the new connection is open but no snapshot yet: not CURRENT
            mid["state"] = b.current("K-A")["state"]
        return sockets.pop(0)
    sub = KWS.Subscriber(connect, b, wanted=lambda: ["K-A"],
                         clock=lambda: NOW, backoff=(0,))

    async def go():
        nonlocal stop
        stop = asyncio.Event()
        await asyncio.wait_for(sub.run(stop=stop), timeout=5)
    asyncio.run(go())
    assert sub.connections == 2
    assert seen["state_before_snapshot"] == KWS.GAP
    assert seen["cur"]["ok"]
    assert seen["cur"]["book"]["orderbook_fp"]["yes_dollars"] == [
        ["0.47", "10"]]
    assert [m["cmd"] for m in ws2.sent] == ["subscribe"]
    assert sub.resubscribes == 0


def test_a_gap_still_unsubscribes_the_broken_sid_and_resubscribes_once():
    ws = FakeWS([subscribed(1, 3), snap(3, 1, "K-A"), snap(3, 2, "K-B"),
                 delta(3, 4, "K-A"),                 # seq 3 lost
                 subscribed(3, 4), snap(4, 1, "K-A"), snap(4, 2, "K-B")])
    seen = {}
    b = KWS.WsBooks(clock=lambda: NOW)
    ws.on_empty = lambda: seen.update(a=b.current("K-A"),
                                      bb=b.current("K-B"))

    async def connect():
        return ws
    sub = KWS.Subscriber(connect, b, wanted=lambda: ["K-A", "K-B"],
                         clock=lambda: NOW)

    async def go():
        with pytest.raises(ConnectionError):
            await sub.session()
    asyncio.run(go())
    cmds = [m["cmd"] for m in ws.sent]
    assert cmds == ["subscribe", "unsubscribe", "subscribe"], ws.sent
    assert ws.sent[1]["params"] == {"sids": [3]}
    assert ws.sent[2]["params"]["market_tickers"] == ["K-A", "K-B"]
    assert sub.resubscribes == 1 and b.stats["gaps"] == 1
    assert seen["a"]["ok"] and seen["bb"]["ok"]


def test_a_pending_market_of_a_gapped_subscription_is_resubscribed_too():
    """K-C was subscribed in the same command as K-A; the sid gapped before
    K-C's snapshot arrived. Unsubscribing the sid drops K-C's subscription
    too, so K-C must be resubscribed (it used to wait for a snapshot that
    could never come, until the next reconnect)."""
    ws = FakeWS([subscribed(1, 3), snap(3, 1, "K-A"),
                 delta(3, 3, "K-A")])                # seq 2 lost
    b = KWS.WsBooks(clock=lambda: NOW)

    async def connect():
        return ws
    sub = KWS.Subscriber(connect, b, wanted=lambda: ["K-A", "K-C"],
                         clock=lambda: NOW)

    async def go():
        with pytest.raises(ConnectionError):
            await sub.session()
    asyncio.run(go())
    assert [m["cmd"] for m in ws.sent] == ["subscribe", "unsubscribe",
                                          "subscribe"], ws.sent
    assert ws.sent[2]["params"]["market_tickers"] == ["K-A", "K-C"]
