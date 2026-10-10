"""THE RC6.1 STORM SHAPE, AGAINST THE DOCUMENTED VENUE (RC6.2 regression).

Production RC6.1 (pm-acceptance 37954055578): one connection, ~273
resubscribes and ~305 dead-sid snapshots per gap, the market plane's event
loop held. The shape, reproduced here against a venue that does exactly
what docs.kalshi.com documents (websocket-connection, asyncapi, changelog
2025-09-25):

  * the FIRST subscribe on the channel is answered by `subscribed` {id,
    sid} and the markets' snapshots, in the sid's sequence;
  * a REPEATED subscribe on the channel "will no longer error ... If
    passing new market tickers, they will be added to your existing
    subscription": it MERGES into the same sid and is answered by `ok` {id,
    sid, seq, msg.market_tickers: the full list after the update} -- the
    `ok` takes the sid's next seq (one counter per sid, the plain reading
    of the schema) -- then the new markets' snapshots;
  * update_subscription add_markets / delete_markets / get_snapshot act on
    the same sid and are answered by `ok` (seq) the same way; get_snapshot
    then sends a snapshot of EVERY requested ticker, held or not ("without
    modifying the subscription");
  * unsubscribe {sids} -> `unsubscribed` {id, sid, seq}; an unknown sid ->
    error code 7.

300 markets (three chunks of 100). RC6.1 (rc6/int-62) sent the chunks as
three subscribes, bound markets to the sid only on `subscribed`, ignored
the `ok` without advancing the sid, so the next snapshot looked like a gap
and the storm began. The RC6.2 client sends ONE subscribe and two
add_markets, takes each `ok` into the sid's sequence, and ends with every
market CURRENT, no gap, three commands. The same holds when the venue
keeps control-frame seq in a SEPARATE counter (the client learns it from
the first `ok`, without a gap).

This file imports nothing but sportsassets.kalshi_ws, so it runs unchanged
against rc6/int-62's module (where it fails)."""
from __future__ import annotations

import asyncio
import json
from collections import deque

import pytest

from sportsassets import kalshi_ws as KWS

NOW = 1_791_700_000.0


def m_subscribed(cid, sid):
    return {"type": "subscribed", "id": cid,
            "msg": {"channel": "orderbook_delta", "sid": sid}}


class DocVenue:
    """The documented venue's side of ONE connection (orderbook_delta).
    `separate_control`: ok / unsubscribed / scoped errors carry a separate
    per-sid counter (the undocumented alternative). The socket ends after
    `budget` delivered frames, or when nothing is left to deliver."""

    def __init__(self, *, budget: int = 20_000,
                 separate_control: bool = False):
        self.budget = budget
        self.separate = separate_control
        self.sub = None             # {"sid", "seq", "cseq", "markets"}
        self.next_sid = 1
        self.out: deque = deque()
        self.sent: list = []
        self.delivered = 0
        self.probe = None
        self.levels = {}

    # ── frames ──
    def _seq(self, control: bool) -> int:
        if control and self.separate:
            self.sub["cseq"] += 1
            return self.sub["cseq"]
        self.sub["seq"] += 1
        return self.sub["seq"]

    def _snap(self, t):
        return {"type": "orderbook_snapshot", "sid": self.sub["sid"],
                "seq": self._seq(False),
                "msg": {"market_ticker": t,
                        "yes_dollars_fp": [["0.40", "10"]],
                        "no_dollars_fp": [["0.55", "10"]]}}

    def _ok(self, cid):
        return {"type": "ok", "id": cid, "sid": self.sub["sid"],
                "seq": self._seq(True),
                "msg": {"market_tickers": list(self.sub["markets"])}}

    def _error(self, cid, code):
        return {"type": "error", "id": cid,
                "msg": {"code": code, "msg": "error %d" % code}}

    def _add(self, tickers):
        new = [t for t in tickers if t not in self.sub["markets"]]
        self.sub["markets"] += new
        return new

    # ── the socket ──
    async def send(self, raw):
        m = json.loads(raw)
        self.sent.append(m)
        cid, p = m.get("id"), m.get("params") or {}
        if m["cmd"] == "subscribe":
            if self.sub is None:
                self.sub = {"sid": self.next_sid, "seq": 0, "cseq": 0,
                            "markets": []}
                self.next_sid += 1
                self.out.append(m_subscribed(cid, self.sub["sid"]))
                self.out.extend(self._snap(t) for t in
                                self._add(p["market_tickers"]))
            else:
                # changelog 2025-09-25: merged into the existing sid, `ok`;
                # tickers already held: no action (no snapshot)
                new = self._add(p["market_tickers"])
                self.out.append(self._ok(cid))
                self.out.extend(self._snap(t) for t in new)
            return
        if m["cmd"] == "unsubscribe":
            for sid in p.get("sids") or ():
                if self.sub is not None and sid == self.sub["sid"]:
                    self.out.append({"type": "unsubscribed", "id": cid,
                                     "sid": sid, "seq": self._seq(True)})
                    self.sub = None
                else:
                    self.out.append(self._error(cid, 7))
            return
        if m["cmd"] != "update_subscription":
            self.out.append(self._error(cid, 5))
            return
        sid = p.get("sid", (p.get("sids") or [None])[0])
        if self.sub is None or sid != self.sub["sid"]:
            self.out.append(self._error(cid, 7))
            return
        action, ts = p.get("action"), list(p.get("market_tickers") or ())
        if action == "add_markets":
            new = self._add(ts)
            self.out.append(self._ok(cid))
            self.out.extend(self._snap(t) for t in new)
        elif action == "delete_markets":
            self.sub["markets"] = [t for t in self.sub["markets"]
                                   if t not in ts]
            self.out.append(self._ok(cid))
        elif action == "get_snapshot":
            # asyncapi: "returns an orderbook_snapshot for the requested
            # market_tickers without modifying the subscription" -- for
            # every requested ticker, held or not (RC6.2 review: it used
            # to answer only the held ones, which the docs contradict)
            self.out.append(self._ok(cid))
            self.out.extend(self._snap(t) for t in ts)
        else:
            self.out.append(self._error(cid, 13))

    async def recv(self):
        if self.probe is not None:
            self.probe()
        if self.delivered >= self.budget or not self.out:
            raise ConnectionError("venue script ended")
        self.delivered += 1
        return json.dumps(self.out.popleft())

    async def close(self):
        pass

    def commands(self) -> list:
        return [(m["cmd"], (m.get("params") or {}).get("action"))
                for m in self.sent]


MARKETS = ["KXGAME-26OCT10-T%03d" % i for i in range(300)]


def run_session(venue, wanted=MARKETS):
    books = KWS.WsBooks(clock=lambda: NOW)
    seen = {"current_at_end": None, "max_current": 0}

    def probe():
        seen["max_current"] = max(seen["max_current"],
                                  books.counts()["current"])
        if not venue.out:
            seen["current_at_end"] = books.counts()["current"]
            seen["gaps_at_end"] = books.stats["gaps"]
    venue.probe = probe

    async def connect():
        return venue

    sub = KWS.Subscriber(connect, books, wanted=lambda: list(wanted),
                         clock=lambda: NOW)

    async def go():
        try:
            await sub.session()
        except Exception as exc:                                # noqa: BLE001
            seen["ended"] = exc
    asyncio.run(go())
    return sub, books, seen


@pytest.mark.parametrize("separate_control", [False, True],
                         ids=["shared_counter", "separate_counter"])
def test_300_markets_with_chunk_acks_answered_by_ok_never_storm(
        separate_control):
    venue = DocVenue(separate_control=separate_control)
    sub, books, seen = run_session(venue)
    sent = venue.commands()
    # ONE subscribe, then the other chunks as add_markets on its sid:
    # ceil(300 / 100) commands in all, nothing else (no unsubscribe, no
    # second subscribe, no get_snapshot -- there was no gap)
    assert sent == [("subscribe", None), ("update_subscription",
                                          "add_markets"),
                    ("update_subscription", "add_markets")], sent[:12]
    assert len(sent) == -(-len(MARKETS) // KWS.SUBSCRIBE_CHUNK)
    # the session ended only because the venue had nothing more to say
    assert isinstance(seen.get("ended"), ConnectionError), seen.get("ended")
    # no false gap: every frame (the two `ok`s too) in sequence
    assert books.stats["gaps"] == 0, books.stats
    assert books.stats["snapshots_out_of_sequence"] == 0
    assert books.stats["ignored_dead_sid"] == 0
    assert sub.resubscribes == 0 and sub.storms == 0
    # every market CURRENT once the venue's frames were all read
    assert seen["current_at_end"] == len(MARKETS), seen
    assert venue.delivered == 1 + 2 + len(MARKETS)
