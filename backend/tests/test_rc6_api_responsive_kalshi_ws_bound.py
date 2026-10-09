"""RC6 api-responsive: the Kalshi WebSocket runtime holds books only for the
markets it tracks.

PRODUCTION 2026-10-09 03:11Z (research-sql rc6_api-responsive_kalshi_ws_
growth.sql, read only): the runtime (workers/kalshi_ws_market_data, in the
dedicated market-plane service) held 235 books -- its heartbeat freshness
235 / 235 "current" -- while the wanted set (ESTABLISHED fixtures starting
within [now - 4 h, now + 36 h]) was 185 markets: 50 CURRENT books were
markets it no longer tracked, and 50 kalshi_books_current rows of them were
still re-asserted every 10 s. Nothing removed a book, a written-key entry or
a subscription: all three grew with every market the process ever tracked.

THE CONTRACT PINNED HERE:
  * every wanted-set refresh drops the books, written keys and tracked
    subscriptions of the markets that left it -- the map is bounded by the
    wanted set, however long the process lives; no new command reaches the
    socket: the venue-side subscription ends with the session, and the next
    session subscribes the wanted set alone;
  * a message still in flight for a dropped market never brings its book
    back, and keeps its sid's sequence discipline: an unbroken seq stays
    unbroken for the tracked markets on that sid, a jump is still a GAP;
  * the heartbeat's freshness counts tracked markets only.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from sportsassets import kalshi_ws as KWS
from sportsassets.workers import kalshi_ws_market_data as W

NOW = 1_791_400_000.0


def snap(sid, seq, t, yes=(("0.43", "100"),), no=(("0.55", "80"),)):
    return {"type": "orderbook_snapshot", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "yes_dollars_fp": [list(x)
                                                           for x in yes],
                    "no_dollars_fp": [list(x) for x in no]}}


def delta(sid, seq, t, price="0.44", d="5", side="yes"):
    return {"type": "orderbook_delta", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "price_dollars": price,
                    "delta_fp": d, "side": side}}


class _Sub:
    """The Subscriber's book-keeping without a socket."""

    def __new__(cls, books):
        return KWS.Subscriber(lambda: None, books, wanted=lambda: [])


def test_the_book_map_is_bounded_by_the_wanted_set_over_many_refreshes():
    """Thirty wanted-set refreshes of a rolling 40-market window (10 markets
    leave and 10 arrive each time): the books and the written keys never
    exceed the window, and none of the dropped markets comes back."""
    books = KWS.WsBooks(clock=lambda: NOW)
    books.on_connected()
    sub = _Sub(books)
    written: dict = {}
    seq = {"n": 0}
    dropped_ever = set()
    for k in range(30):
        want = ["KX-%04d" % i for i in range(10 * k, 10 * k + 40)]
        dropped = W.prune_untracked(sub, written, want)
        if k:
            assert dropped == 10
        dropped_ever |= {t for t in written} - set(want)
        books.want(want)
        sid = 100 + k
        for t in want:
            if books.books[t]["state"] != KWS.CURRENT:
                seq["n"] += 1
                books.on_message(snap(sid, seq["n"], t))
        for t, b in books.books.items():
            written[t] = (b["state"], b["seq"], b["updated_at"])
        assert len(books.books) == 40 and len(written) == 40
        assert set(books.books) == set(want)
    assert books.stats["forgotten"] == 29 * 10
    assert not (set(books.books) & {"KX-%04d" % i for i in range(250)})


def test_a_dropped_markets_late_messages_keep_the_sid_sequence():
    b = KWS.WsBooks(clock=lambda: NOW)
    b.on_connected()
    b.want(["A", "B"])
    assert b.on_message(snap(7, 1, "A")) == "SNAPSHOT"
    assert b.on_message(snap(7, 2, "B")) == "SNAPSHOT"
    assert b.forget(["A"]) == {7: ["A"]}
    assert "A" not in b.books
    # in flight before the venue processed the delete: no book comes back,
    # and the sid's sequence moves on with it
    assert b.on_message(delta(7, 3, "A")) == "IGNORED_NOT_TRACKED"
    assert "A" not in b.books
    assert b.on_message(delta(7, 4, "B")) == "DELTA"
    assert b.current("B")["ok"] and b.stats["gaps"] == 0
    # a sequence jump is still a gap, whoever's message it is
    assert b.on_message(delta(7, 6, "A")) == "GAP"
    assert not b.current("B")["ok"] and b.stats["gaps"] == 1
    assert "A" not in b.books
    # a disconnect ends the session; the next one subscribes what is wanted
    b.on_disconnected()
    assert not b.forgotten
    b.want(["A"])
    assert "A" in b.books and b.books["A"]["state"] == KWS.PENDING


class FakeWS:
    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    async def send(self, s):
        self.sent.append(json.loads(s))

    async def recv(self):
        if not self.script:
            raise ConnectionError("closed")
        item = self.script.pop(0)
        if callable(item):
            item = item()
        return json.dumps(item)

    async def close(self):
        pass


def test_a_dropped_market_is_ignored_and_the_next_session_drops_it():
    """No new command reaches the socket (subscribe / unsubscribe only, the
    plane's boot test pins it): a dropped market's messages are ignored for
    the rest of the session, its sid's sequence kept, and the next session
    subscribes the wanted set alone."""
    want = ["A", "B", "C"]
    written: dict = {}
    books = KWS.WsBooks(clock=lambda: NOW)
    holder = {}

    def drop_b_and_c():
        # the worker's wanted refresh, between two messages
        want[:] = ["A"]
        assert W.prune_untracked(holder["sub"], written, want) == 2
        return {"type": "subscribed", "msg": {"sid": 9}}

    first = FakeWS([snap(9, 1, "A"), snap(9, 2, "B"), snap(9, 3, "C"),
                    drop_b_and_c, delta(9, 4, "C"), delta(9, 5, "A")])
    second = FakeWS([snap(11, 1, "A")])
    sockets = [first, second]

    async def connect():
        return sockets.pop(0)
    sub = KWS.Subscriber(connect, books, wanted=lambda: list(want),
                         clock=lambda: NOW)
    holder["sub"] = sub

    async def go():
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await sub.session()
    asyncio.run(go())
    assert {m["cmd"] for m in first.sent + second.sent} == {"subscribe"}
    assert first.sent[0]["params"]["market_tickers"] == ["A", "B", "C"]
    # in the first session: B and C dropped, C's late delta ignored with the
    # sequence kept, A's next delta applied (no gap)
    assert books.stats["ignored_not_tracked"] == 1
    assert books.stats["gaps"] == 0 and books.stats["deltas"] == 1
    # the next session asks for the wanted set alone
    assert second.sent[0]["params"]["market_tickers"] == ["A"]
    assert set(books.books) == {"A"} and sub.subscribed == {"A"}
    assert not books.forgotten


def test_the_heartbeat_freshness_counts_tracked_markets_only():
    books = KWS.WsBooks(clock=lambda: NOW)
    books.on_connected()
    sub = _Sub(books)
    books.want(["A", "B", "C", "D"])
    for i, t in enumerate("ABCD"):
        books.on_message(snap(1, i + 1, t))
    written = {t: 1 for t in "ABCD"}
    W.prune_untracked(sub, written, ["A", "B", "C"])
    h = W.health(books, sub, now=NOW, limits=None,
                 pace=KWS.pacing(None))
    assert h["ws"]["markets"] == 3 and h["ws"]["current"] == 3
    assert h["freshness"]["denominator"] == 3
    assert h["freshness"]["numerator"] == 3
    assert h["untracked_dropped"]["books"] == 1
    assert written == {"A": 1, "B": 1, "C": 1}
