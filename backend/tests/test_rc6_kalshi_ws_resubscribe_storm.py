"""THE KALSHI WS RESUBSCRIBE STORM THAT HUNG THE MARKET PLANE (2026-10-09).

Production sportsassets-market-plane ran RC6.1 (3d5af039, tree b3f1b0cd) from
14:42Z. The last universal_market_plane pass completed at 15:22:06Z (its
SNAPSHOT event and its "RSS by step" line); then CPU sat at one core and RSS
rose ~10 MB/min until the rollback, while the plane's freshness task kept
sampling a little late every minute (the event loop was alive but held). The
signed acceptance (pm-acceptance 37954055578, venues.json
KALSHI_HEALTH.mechanism.ws) read the Kalshi WS runtime -- which shares the
plane's one event loop -- in ONE connection with no disconnect:

    gaps 4,716   resubscribes 1,286,756   ignored_dead_sid 1,440,903
    errors 0     by_state {"GAP": 397}    current_books 0

i.e. ~273 resubscribes and ~305 dead-sid snapshots per gap: every snapshot
of a market not bound to the dead sid it arrived on resubscribed that market
at once, and each resubscribe's snapshot again arrived on a sid the books had
declared dead. 732cc0c6 (the previous release, 11 h clean) applied every
snapshot and never resubscribed from one.

THE VENUE MODEL (`MergingVenue`) reproduces those counters: one orderbook
subscription per connection; a subscribe while it exists ADDS its markets to
it and answers `ok` (the sid and a seq of the sid's own sequence -- the
documented OK Response carries both), then the added markets' snapshots on
that sid. Whatever the venue's exact reason, the client must not answer it
with an unbounded number of subscribes.

RC6.2 (the documented protocol). That venue IS the documented one (changelog
2025-09-25: a repeated subscribe is merged into the existing sid). The client
now sends ONE subscribe and update_subscription add_markets for the other
chunks, takes every `ok` into the sid's sequence, and repairs a gap with
get_snapshot on the same sid: against this venue there is no storm at all.
Three tests here pinned the RC6.1 storm itself (that it HAPPENS and is cut by
the bound); they now pin its absence with the same bounds asserted tighter,
and the bound that ends a session whose repairs run away is exercised through
the RC6.2 command-rate bound (MAX_RESUBSCRIBES_WITHOUT_RECOVERY is kept
beneath it: tests/test_rc62_kalshi_ws_protocol.py).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from sportsassets import kalshi_ws as KWS

NOW = 1_791_500_000.0


class MergingVenue:
    """A socket whose far end follows the model above; every message
    delivered is counted, and the socket ends after `budget` of them."""

    def __init__(self, budget: int):
        self.budget = budget
        self.delivered = 0
        self.sub = None             # {"sid", "seq", "markets"}
        self.next_sid = 1
        self.out: list = []
        self.sent: list = []
        self.probe = None

    def _snap(self, t):
        self.sub["seq"] += 1
        return {"type": "orderbook_snapshot", "sid": self.sub["sid"],
                "seq": self.sub["seq"],
                "msg": {"market_ticker": t,
                        "yes_dollars_fp": [["0.40", "10"]],
                        "no_dollars_fp": [["0.55", "10"]]}}

    async def send(self, s):
        m = json.loads(s)
        self.sent.append(m)
        if m["cmd"] == "unsubscribe":
            if self.sub is not None and self.sub["sid"] in \
                    m["params"]["sids"]:
                self.sub["seq"] += 1
                self.out.append({"type": "unsubscribed", "id": m["id"],
                                 "sid": self.sub["sid"],
                                 "seq": self.sub["seq"]})
                self.sub = None
            return
        mk = list(m["params"]["market_tickers"])
        if self.sub is None:
            self.sub = {"sid": self.next_sid, "seq": 0, "markets": list(mk)}
            self.next_sid += 1
            self.out.append({"type": "subscribed", "id": m["id"],
                             "msg": {"channel": "orderbook_delta",
                                     "sid": self.sub["sid"]}})
        else:
            self.sub["markets"] += [t for t in mk
                                    if t not in self.sub["markets"]]
            self.sub["seq"] += 1
            self.out.append({"type": "ok", "id": m["id"],
                             "sid": self.sub["sid"], "seq": self.sub["seq"],
                             "msg": {"market_tickers":
                                     list(self.sub["markets"])}})
        self.out.extend(self._snap(t) for t in mk)

    async def recv(self):
        if self.probe is not None:
            self.probe()
        if self.delivered >= self.budget or not self.out:
            raise ConnectionError("venue script ended")
        self.delivered += 1
        return json.dumps(self.out.pop(0))

    async def close(self):
        pass

    def subscribes(self) -> int:
        return sum(1 for m in self.sent if m["cmd"] == "subscribe")


WANTED = ["KXGAME-26OCT10-T%03d" % i for i in range(397)]


def _session(budget: int, wanted=WANTED):
    venue = MergingVenue(budget)
    books = KWS.WsBooks(clock=lambda: NOW)

    async def connect():
        return venue
    sub = KWS.Subscriber(connect, books, wanted=lambda: list(wanted),
                         clock=lambda: NOW)
    seen = {"max_dead_sids": 0, "max_sid_market_entries": 0,
            "max_current": 0}

    def probe():
        seen["max_current"] = max(seen["max_current"],
                                  books.counts()["current"])
        seen["max_dead_sids"] = max(seen["max_dead_sids"],
                                    len(books.dead_sids))
        seen["max_sid_market_entries"] = max(
            seen["max_sid_market_entries"],
            sum(len(x) for x in books.sid_markets.values()))
    venue.probe = probe

    async def go():
        try:
            await sub.session()
        except Exception as exc:                                # noqa: BLE001
            seen["ended"] = exc
            seen["counts"] = dict(books.counts())
    asyncio.run(go())
    return venue, sub, books, seen


def test_the_production_storm_shape_is_bounded_and_never_storms():
    """(RC6.2; was test_the_production_storm_is_bounded_and_ends_the_session,
    which asserted the storm happened and the bound ended it.) Against the
    venue that stormed RC6.1, every bound that test held is held tighter:
    the session reads every frame the venue has (no repair loop to cut),
    ONE subscribe and ceil(397 / 100) - 1 add_markets in all, each market
    named exactly once, no dead sid, every market CURRENT; the session's end
    still leaves every book GAP for the reconnect."""
    budget = 60_000
    venue, sub, books, seen = _session(budget)
    # nothing to cut: the session ended only because the venue had no more
    # frames, with no bound reached
    assert isinstance(seen["ended"], ConnectionError), seen
    assert sub.storms == 0 and sub.session_ends == {}
    n_chunks = -(-len(WANTED) // KWS.SUBSCRIBE_CHUNK)
    assert venue.delivered == 1 + (n_chunks - 1) + len(WANTED) \
        < budget // 10, venue.delivered
    # ONE subscribe (the old bound: the chunks plus the resubscribe bound)
    assert venue.subscribes() == 1 <= n_chunks + \
        KWS.MAX_RESUBSCRIBES_WITHOUT_RECOVERY * len(WANTED), venue.subscribes()
    assert [m["cmd"] for m in venue.sent] == ["subscribe"] + [
        "update_subscription"] * (n_chunks - 1)
    per_market = {}
    for m in venue.sent:
        for t in m["params"]["market_tickers"]:
            per_market[t] = per_market.get(t, 0) + 1
    assert set(per_market) == set(WANTED)
    assert max(per_market.values()) == 1 <= \
        1 + KWS.MAX_RESUBSCRIBES_WITHOUT_RECOVERY, max(per_market.values())
    assert sub.resubscribes == 0 and seen["counts"]["gaps"] == 0
    assert seen["max_current"] == len(WANTED)
    # the session's end leaves every book GAP for the reconnect
    assert seen["counts"]["current"] == 0
    assert all(b["state"] == KWS.GAP for b in books.books.values())
    # bounded state: no dead sid at all, one sid's markets
    # (production: 4,716 dead sids, 1.44M dead-sid snapshots in one session)
    assert seen["max_dead_sids"] == 0 <= \
        KWS.MAX_RESUBSCRIBES_WITHOUT_RECOVERY + 2, seen["max_dead_sids"]
    assert seen["max_sid_market_entries"] == len(WANTED) <= 4 * len(WANTED)


def test_the_bound_does_not_depend_on_how_long_the_venue_keeps_talking():
    # before the fix the subscribes grew with every message the venue sent
    # (25,777 in 60,000 messages); now they are the same for any budget
    small = _session(5_000)[0].subscribes()
    large = _session(200_000)[0].subscribes()
    assert small == large


def test_a_market_whose_repairs_converge_is_never_counted_against_it():
    # one subscribe, one sid: a lost message gaps the sid, each market is
    # asked for a snapshot once (RC6.2: get_snapshot on the same sid; it was
    # a resubscribe acknowledged as a new sid) and its fresh snapshot makes
    # it CURRENT again (its count resets) -- three separate gaps never end
    # the session
    wanted = ["K-A", "K-B"]

    def snap(sid, seq, t):
        return {"type": "orderbook_snapshot", "sid": sid, "seq": seq,
                "msg": {"market_ticker": t, "yes_dollars_fp": [["0.4", "1"]],
                        "no_dollars_fp": [["0.5", "1"]]}}

    def delta(sid, seq, t):
        return {"type": "orderbook_delta", "sid": sid, "seq": seq,
                "msg": {"market_ticker": t, "price_dollars": "0.41",
                        "delta_fp": "1", "side": "yes"}}

    def ack(cmd, sid):
        return {"type": "subscribed", "id": cmd,
                "msg": {"channel": "orderbook_delta", "sid": sid}}

    def ok(cmd, sid, seq):
        return {"type": "ok", "id": cmd, "sid": sid, "seq": seq}
    # cmd ids: 1 subscribe; per gap g: get_snapshot g + 1 (on sid 1)
    script = [ack(1, 1), snap(1, 1, "K-A"), snap(1, 2, "K-B")]
    for g in range(1, 4):
        base = 5 * g - 2                               # 3, 8, 13 lost
        script += [delta(1, base + 1, "K-A"),          # gap
                   ok(g + 1, 1, base + 2), snap(1, base + 3, "K-A"),
                   snap(1, base + 4, "K-B")]
    seen = {}

    class WS:
        sent = []

        async def send(self, s):
            self.sent.append(json.loads(s))

        async def recv(self):
            if not script:
                seen["current"] = books.counts()["current"]
                raise ConnectionError("end")
            return json.dumps(script.pop(0))

        async def close(self):
            pass
    ws = WS()
    books = KWS.WsBooks(clock=lambda: NOW)

    async def connect():
        return ws
    sub = KWS.Subscriber(connect, books, wanted=lambda: wanted,
                         clock=lambda: NOW)

    async def go():
        with pytest.raises(ConnectionError):
            await sub.session()
    asyncio.run(go())
    assert sub.storms == 0 and sub.resubscribes == 3
    assert books.stats["gaps"] == 3
    assert seen["current"] == 2
    assert [m["params"].get("action") for m in WS.sent] == [
        None, "get_snapshot", "get_snapshot", "get_snapshot"]


def test_run_reconnects_after_a_storm_and_names_it(monkeypatch):
    # through Subscriber.run as production runs it. (RC6.2) Against the
    # venue that stormed RC6.1 no session storms: each ends only when that
    # venue has nothing more to send, and every connection subscribes each
    # wanted market exactly once (one subscribe, the rest add_markets on its
    # sid). Then a session whose commands run away -- the command-rate bound
    # set below one session's needs -- is ended by the bound, named (the
    # heartbeat's last_error), counted as a storm, and the next connection
    # starts over with ONE subscribe.
    def run(n):
        venues = []
        holder = {}

        async def connect():
            v = MergingVenue(60_000)
            venues.append(v)
            if len(venues) >= n:
                holder["stop"].set()
            return v
        books = KWS.WsBooks(clock=lambda: NOW)
        sub = KWS.Subscriber(connect, books, wanted=lambda: list(WANTED),
                             clock=lambda: NOW, backoff=(0,))

        async def go():
            holder["stop"] = asyncio.Event()
            await asyncio.wait_for(sub.run(stop=holder["stop"]), timeout=60)
        asyncio.run(go())
        return sub, venues
    sub, venues = run(3)
    assert sub.connections == 3 and sub.storms == 0
    assert sub.last_error == "ConnectionError"
    for v in venues:
        assert v.subscribes() == 1 <= 4 + \
            KWS.MAX_RESUBSCRIBES_WITHOUT_RECOVERY * len(WANTED)
        first = v.sent[:4]
        assert sorted(t for m in first for t in
                      m["params"]["market_tickers"]) == sorted(WANTED)
        assert len(v.sent) == 4
    monkeypatch.setattr(KWS, "MAX_COMMANDS_PER_MINUTE", 3)
    sub, venues = run(3)
    assert sub.connections == 3 and sub.storms == 3
    assert sub.last_error == "CommandRateBound"
    assert sub.session_ends == {KWS.R_COMMAND_RATE: 3}
    for v in venues:
        assert [m["cmd"] for m in v.sent] == ["subscribe",
                                              "update_subscription",
                                              "update_subscription"]
