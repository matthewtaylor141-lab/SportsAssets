"""KALSHI WEBSOCKET SEQUENCING AND RECONNECT RECOVERY (RC6 lane K).

The venue's contract (research/kalshi_canonical_venue/REP_PRODUCTION_
CONTRACT_2026-10-07/docs/websockets_orderbook-updates.md): every message of
a subscription -- the `orderbook_snapshot` of each market AND every
`orderbook_delta` -- carries the subscription's `sid` and a per-sid `seq`
"that should be checked if you want to guarantee you received all the
messages. Used for snapshot/delta consistency".

RC6.2 (the documented protocol; docs.kalshi.com changelog 2025-09-25:
"Repeated subscriptions on the same WebSocket call will no longer error. If
passing the same market tickers as before, no action will be taken. If
passing new market tickers, they will be added to your existing
subscription."): a repeated subscribe does NOT open a second sid -- it merges
into the existing one and is answered by `ok` (with the sid's next seq). So
a gap is repaired on the SAME sid by update_subscription get_snapshot
(orderbook-updates: "get_snapshot returns an orderbook_snapshot for the
requested market_tickers without modifying the subscription"), never by
unsubscribe + subscribe. The tests below that pinned the RC6 repair
(unsubscribe the dead sid, resubscribe on a new one) now pin that action,
with the same safety properties (one repair per market per gap, no book
CURRENT on a subscription the venue does not feed, CURRENT again only from
a fresh snapshot in sequence).

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


def ok(cmd_id, sid, seq):
    """The venue's `ok` to an update_subscription on `sid`: it carries the
    sid's next seq (asyncapi OK Response: sid, seq, msg)."""
    return {"type": "ok", "id": cmd_id, "sid": sid, "seq": seq}


def get_snapshot(sid, tickers):
    return {"sid": sid, "market_tickers": list(tickers),
            "action": "get_snapshot"}


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


def test_a_gap_asks_the_same_sid_for_snapshots_once():
    """(RC6.2; was test_a_gap_still_unsubscribes_the_broken_sid_and_
    resubscribes_once) the documented repair: ONE get_snapshot on the same
    sid (never unsubscribe + subscribe), and both books CURRENT again only
    from the fresh snapshots that follow its `ok` in the sid's sequence."""
    ws = FakeWS([subscribed(1, 3), snap(3, 1, "K-A"), snap(3, 2, "K-B"),
                 delta(3, 4, "K-A"),                 # seq 3 lost
                 ok(2, 3, 5), snap(3, 6, "K-A"), snap(3, 7, "K-B")])
    seen = {}
    b = KWS.WsBooks(clock=lambda: NOW)
    ws.on_empty = lambda: seen.update(
        a=b.current("K-A"), bb=b.current("K-B"),
        sids={b.books["K-A"]["sid"], b.books["K-B"]["sid"]})

    async def connect():
        return ws
    sub = KWS.Subscriber(connect, b, wanted=lambda: ["K-A", "K-B"],
                         clock=lambda: NOW)

    async def go():
        with pytest.raises(ConnectionError):
            await sub.session()
    asyncio.run(go())
    cmds = [m["cmd"] for m in ws.sent]
    assert cmds == ["subscribe", "update_subscription"], ws.sent
    assert ws.sent[1]["params"] == get_snapshot(3, ["K-A", "K-B"])
    assert sub.resubscribes == 1 and b.stats["gaps"] == 1
    assert seen["a"]["ok"] and seen["bb"]["ok"] and seen["sids"] == {3}


def test_a_pending_market_of_a_gapped_subscription_is_asked_for_too():
    """K-C was subscribed in the same command as K-A; the sid gapped before
    K-C's snapshot arrived (the lost message may have been it). K-C must be
    asked for with K-A (it used to wait for a snapshot that could never
    come, until the next reconnect). (RC6.2; was test_a_pending_market_of_a_
    gapped_subscription_is_resubscribed_too: the request is the documented
    get_snapshot on the same sid, not unsubscribe + subscribe.)"""
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
    assert [m["cmd"] for m in ws.sent] == ["subscribe",
                                          "update_subscription"], ws.sent
    assert ws.sent[1]["params"] == get_snapshot(3, ["K-A", "K-C"])


def test_the_readback_names_what_caused_the_resubscribes():
    """venues.json KALSHI_HEALTH.mechanism.ws carries the books' own gap /
    error / disconnect / dead-sid counts beside resubscribes and
    connections, so production can show that resubscribes follow gaps and
    no longer follow reconnects (RC5: connections 10, resubscribes 18, and
    no gap count to tell them apart)."""
    b = KWS.WsBooks(clock=lambda: NOW)
    b.on_connected()
    b.on_message(snap(1, 1, "K-A"))
    b.on_message(delta(1, 3, "K-A"))
    ws_beat = {"status": "ok", "at": NOW, "detail": {
        "ws": b.counts(), "resubscribes": 1, "connections": 2,
        "subscribed_markets": 1}}
    plane = {"status": "ok", "at": NOW, "detail": {"guard": {}}}
    m = KWS.mechanism({"freshness": {"current_by_source": {"WS": 1},
                                     "denominator": 1}}, ws_beat, plane,
                      now=NOW)["ws"]
    assert (m["gaps"], m["errors"], m["disconnects"],
            m["ignored_dead_sid"]) == (1, 0, 0, 0)
    assert (m["resubscribes"], m["connections"]) == (1, 2)


# ── review of 5f8b2de5: the ack-bound gap and the dead-sid snapshot ─────
#
# The `subscribed` ack binds every market of a command to its sid, so a gap
# on that sid already resubscribes a market whose snapshot is still in
# flight. Two outcomes the subscriber must never produce, as the reviewer
# reproduced them with the production Subscriber:
#   (1) that market's in-flight snapshot, arriving later on the dead sid,
#       resubscribes it a SECOND time (a second live sid per market; a gap
#       inside a 100-market snapshot burst sent up to 99 extra subscribes);
#   (2) after the resubscribe's ack bound the market to its NEW sid, the
#       late dead-sid snapshot made the subscriber unsubscribe that LIVE
#       sid, leaving its siblings CURRENT on a sid the venue no longer feeds
#       (and re-stamped fresh by the worker's reassert every cycle).

class VenueWS(FakeWS):
    """FakeWS that checks the subscriber's invariants on every step, from
    what actually crossed the wire (so the check reads the same on any
    version of the subscriber):
      * an unsubscribe never names a LIVE sid -- one whose sequence the
        books still trust, or that the books have not declared dead;
      * no book is current() ok on a sid this socket was told to
        unsubscribe (until the venue announces that sid number anew)."""

    def __init__(self, script, b, sub_ref, on_empty=None):
        super().__init__(script, on_empty)
        self.b = b
        self.sub_ref = sub_ref
        self.violations = []
        self.dropped = set()

    async def send(self, s):
        m = json.loads(s)
        if m["cmd"] == "unsubscribe":
            dead = getattr(self.b, "dead_sids", None)
            live = {x for x in m["params"]["sids"]
                    if self.b.sid_seq.get(x) is not None
                    or (dead is not None and x not in dead)}
            if live:
                self.violations.append(("UNSUBSCRIBED_LIVE_SID", sorted(live)))
            self.dropped.update(m["params"]["sids"])
        self.sent.append(m)

    def _check_current(self):
        for t, bk in self.b.books.items():
            if self.b.current(t)["ok"] and bk["sid"] in self.dropped:
                self.violations.append(("CURRENT_ON_UNSUBSCRIBED_SID", t,
                                        bk["sid"]))

    async def recv(self):
        self._check_current()
        raw = await super().recv()
        m = json.loads(raw)
        if m.get("type") == "subscribed":
            self.dropped.discard((m.get("msg") or {}).get("sid"))
        return raw


def _drive(script, wanted, *, at_end=None):
    b = KWS.WsBooks(clock=lambda: NOW)
    ref = []
    seen = {}

    def end():
        seen.update({t: dict(b.current(t), sid=b.books[t]["sid"])
                     for t in wanted})
        if at_end is not None:
            at_end(b, seen)
    ws = VenueWS(script, b, ref, on_empty=end)

    async def connect():
        return ws
    sub = KWS.Subscriber(connect, b, wanted=lambda: list(wanted),
                         clock=lambda: NOW)
    ref.append(sub)

    async def go():
        with pytest.raises(ConnectionError):
            await sub.session()
    asyncio.run(go())
    return ws, sub, b, seen


def _subscribe_counts(ws):
    n = {}
    for m in _subs(ws):
        for t in m["params"]["market_tickers"]:
            n[t] = n.get(t, 0) + 1
    return n


# A, B, C subscribed together (cmd 1 -> sid 3); A's delta skips seq 2. RC6
# repaired by unsubscribe (cmd 2) + a resubscribe (cmd 3) the venue acked as
# sid 4. RC6.2, the documented protocol: the repair is cmd 2, get_snapshot
# {sid 3, [A, B, C]}; the venue answers `ok` (the sid's next seq) and the
# fresh snapshots follow on sid 3, in its sequence. B's and C's first
# snapshots (from the subscribe) were already in flight; arriving in
# sequence after the gap's baseline, they are real snapshots. The ordering
# names are kept: "the resubscribe ack" is now get_snapshot's `ok`.
_HEAD = [subscribed(1, 3), snap(3, 1, "K-A"), delta(3, 3, "K-A")]
_ORDERINGS = {
    "in_flight_before_the_resubscribe_ack": _HEAD + [
        snap(3, 4, "K-B"), snap(3, 5, "K-C"),
        ok(2, 3, 6), snap(3, 7, "K-A"), snap(3, 8, "K-B"),
        snap(3, 9, "K-C")],
    "in_flight_after_the_resubscribe_ack": _HEAD + [
        ok(2, 3, 4), snap(3, 5, "K-A"), snap(3, 6, "K-B"),
        snap(3, 7, "K-C"),                       # late: the subscribe's
        snap(3, 8, "K-C")],
    "one_before_one_after": _HEAD + [
        snap(3, 4, "K-B"),
        ok(2, 3, 5), snap(3, 6, "K-A"), snap(3, 7, "K-B"),
        snap(3, 8, "K-C"),                       # late: the subscribe's
        delta(3, 9, "K-A"), snap(3, 10, "K-C"), delta(3, 11, "K-B")],
}


def _snapshot_requests(ws):
    """market -> how many get_snapshot commands named it"""
    n = {}
    for m in ws.sent:
        if m["cmd"] == "update_subscription" and \
                m["params"]["action"] == "get_snapshot":
            for t in m["params"]["market_tickers"]:
                n[t] = n.get(t, 0) + 1
    return n


@pytest.mark.parametrize("ordering", sorted(_ORDERINGS))
def test_one_gap_asks_each_market_snapshot_exactly_once(ordering):
    """(RC6.2; was test_one_gap_resubscribes_each_market_exactly_once) one
    gap: ONE get_snapshot naming each market of the sid once (it used to be
    one resubscribe per market after unsubscribing the sid); nothing is
    ever unsubscribed; every book CURRENT again on the one live sid."""
    ws, sub, b, seen = _drive(_ORDERINGS[ordering], ["K-A", "K-B", "K-C"])
    assert ws.violations == [], ws.violations
    assert [m["cmd"] for m in ws.sent] == [
        "subscribe", "update_subscription"], ws.sent
    assert ws.sent[1]["params"] == get_snapshot(3, ["K-A", "K-B", "K-C"])
    # the initial subscribe once, and ONE snapshot request per market
    assert _subscribe_counts(ws) == {"K-A": 1, "K-B": 1, "K-C": 1}
    assert _snapshot_requests(ws) == {"K-A": 1, "K-B": 1, "K-C": 1}
    assert sub.resubscribes == 1 and b.stats["gaps"] == 1
    # every book CURRENT again, on the one live subscription
    for t in ("K-A", "K-B", "K-C"):
        assert seen[t]["ok"] and seen[t]["sid"] == 3, (t, seen[t])
    assert ws.dropped == set()


def test_a_late_snapshot_never_drops_the_live_subscription():
    """Reviewer reproduction (2), RC6.2 (was test_a_late_dead_sid_snapshot_
    never_drops_the_live_resubscription; the repair is now get_snapshot on
    the same sid, so there is no second sid for the late snapshot to come
    from): after get_snapshot's `ok` and A's and B's fresh snapshots, C's
    LATE first snapshot (the subscribe's, in flight) arrives. No command
    names the live sid for removal (nothing is ever unsubscribed); A and B
    stay CURRENT -- the venue keeps feeding the sid, so their deltas apply.
    C's late snapshot is in sequence after the gap's baseline: a real book
    of the subscription, CURRENT. The same late snapshot OUT of sequence (a
    replay of a seq at or below the baseline) is never applied: C stays
    without a book, and that second gap while the get_snapshot is
    outstanding ends the session (no further command)."""
    tail = [delta(3, 8, "K-A", price="0.45", d="7"),
            delta(3, 9, "K-B", price="0.46", d="9")]
    script = _HEAD + [ok(2, 3, 4), snap(3, 5, "K-A"),
                      snap(3, 6, "K-B"), snap(3, 7, "K-C")] + tail
    ws, sub, b, seen = _drive(script, ["K-A", "K-B", "K-C"])
    assert ws.violations == [], ws.violations
    assert not [m for m in ws.sent if m["cmd"] == "unsubscribe"], ws.sent
    assert _subscribe_counts(ws) == {"K-A": 1, "K-B": 1, "K-C": 1}
    assert _snapshot_requests(ws) == {"K-A": 1, "K-B": 1, "K-C": 1}
    assert seen["K-A"]["ok"] and seen["K-B"]["ok"]
    assert seen["K-A"]["sid"] == seen["K-B"]["sid"] == 3
    assert b.stats["deltas"] == 2
    assert ["0.45", "7"] in seen["K-A"]["book"]["orderbook_fp"]["yes_dollars"]
    assert ["0.46", "9"] in seen["K-B"]["book"]["orderbook_fp"]["yes_dollars"]
    assert seen["K-C"]["ok"] and seen["K-C"]["sid"] == 3
    # the late snapshot replayed below the baseline instead: never applied
    late = _HEAD + [ok(2, 3, 4), snap(3, 5, "K-A"), snap(3, 6, "K-B"),
                    snap(3, 2, "K-C")]
    ended = {}
    b2 = KWS.WsBooks(clock=lambda: NOW)

    def at_end(bk, sn):
        pass
    ws2 = VenueWS(late, b2, [])

    async def connect():
        return ws2
    sub2 = KWS.Subscriber(connect, b2, wanted=lambda: ["K-A", "K-B", "K-C"],
                          clock=lambda: NOW)
    ws2.sub_ref.append(sub2)

    async def go():
        try:
            await sub2.session()
        except Exception as exc:                                # noqa: BLE001
            ended["exc"] = exc
    asyncio.run(go())
    assert isinstance(ended["exc"], KWS.GapDuringRecovery)
    assert ws2.violations == []
    assert [m["cmd"] for m in ws2.sent] == ["subscribe",
                                           "update_subscription"]
    assert b2.stats["snapshots"] == 3            # A (twice) and B, never C


def test_a_gap_inside_a_100_market_snapshot_burst_sends_one_get_snapshot():
    """One subscribe of 100 markets (one chunk) acked as sid 3; the second
    message is lost, so the first delta gaps the sid while 99 snapshots are
    still in flight on it. Exactly ONE get_snapshot of the 100 markets
    follows -- not 99 single-market requests, never an unsubscribe. (RC6.2;
    was ..._sends_one_resubscribe: the documented repair is on the same
    sid, where the 99 in-flight snapshots arrive in sequence after the gap's
    baseline and are real books -- no dead-sid snapshot exists any more.)"""
    ts = ["K-%03d" % i for i in range(100)]
    script = [subscribed(1, 3), snap(3, 1, ts[0]), delta(3, 3, ts[0])]
    script += [snap(3, 4 + i, t) for i, t in enumerate(ts[1:])]
    script += [ok(2, 3, 103)]
    script += [snap(3, 104 + i, t) for i, t in enumerate(ts)]
    ws, sub, b, seen = _drive(script, ts)
    assert ws.violations == [], ws.violations
    assert [m["cmd"] for m in ws.sent] == [
        "subscribe", "update_subscription"]
    assert ws.sent[1]["params"] == get_snapshot(3, ts)
    assert sub.resubscribes == 1
    assert b.stats["ignored_dead_sid"] == 0 and b.stats["gaps"] == 1
    assert all(seen[t]["ok"] and seen[t]["sid"] == 3 for t in ts)


def test_a_dead_sid_snapshot_resubscribes_an_unbound_market_once():
    """WsBooks without acks (the dead-sid branch's own purpose): a market
    the gap could not know about is resubscribed by its in-flight snapshot,
    once; a repeated snapshot of it on the same dead sid adds nothing."""
    b = books()
    b.on_message(snap(7, 1, "K-A"))
    assert b.on_message(delta(7, 3, "K-A")) == "GAP"
    assert b.on_message(snap(7, 4, "K-C")) == "IGNORED_DEAD_SID"
    assert set(b.resubscribe) == {"K-A", "K-C"}
    b.resubscribe.clear()                       # the subscriber acted
    assert b.on_message(snap(7, 5, "K-C")) == "IGNORED_DEAD_SID"
    assert "K-C" not in b.resubscribe


def test_a_market_awaited_on_a_live_sid_is_not_resubscribed_by_a_dead_one():
    """K-C is acknowledged on the live sid 9 (its snapshot still to come);
    a stray snapshot of it on the dead sid 7 must not open a third
    subscription for it, and a gap of sid 7 must not gap it either."""
    b = books()
    b.on_message(snap(7, 1, "K-A"))
    b.bind(9, ["K-C"])
    assert b.on_message(delta(7, 3, "K-A")) == "GAP"
    assert b.on_message(snap(7, 4, "K-C")) == "IGNORED_DEAD_SID"
    assert b.resubscribe == {"K-A"}
    assert b.ticker_sid["K-C"] == 9
    assert b.on_message(snap(9, 1, "K-C")) == "SNAPSHOT"
    assert b.current("K-C")["ok"]


def test_no_sid_is_ever_unsubscribed_and_an_error_elsewhere_sends_nothing():
    """(RC6.2; was test_only_dead_sids_are_ever_unsubscribed_and_each_once,
    which pinned an unsubscribe of the errored sid 8.) The docs: only
    errors 10 and 25 end a subscription; code 7 is "Unknown subscription
    ID - The command references a subscription ID that is not active in the
    session". Sid 8 is not ours (first seen after our ack): its errors are
    counted and answered by NO command -- an unsubscribe of a number the
    venue may have reused for a live subscription could end it (the RC6
    acceptance oracle's blocking finding). Our sid 3 is untouched."""
    script = [subscribed(1, 3), snap(3, 1, "K-A"),
              {"type": "error", "id": 0, "sid": 8,
               "msg": {"code": 7, "msg": "Unknown subscription ID"}},
              {"type": "error", "id": 2, "sid": 8,
               "msg": {"code": 7, "msg": "Unknown subscription ID"}},
              delta(3, 2, "K-A")]
    ws, sub, b, seen = _drive(script, ["K-A"])
    assert ws.violations == [], ws.violations
    assert [m["cmd"] for m in ws.sent] == ["subscribe"]
    assert sub.resubscribes == 0
    assert seen["K-A"]["ok"] and seen["K-A"]["sid"] == 3
    assert b.stats["ignored_unknown_sid"] == 2 and b.stats["gaps"] == 0


def test_a_sid_number_the_venue_reuses_is_a_new_subscription():
    """The venue's docs promise no unique sid. RC6.2: only errors 10 / 25
    end a subscription, and the session then ends (the run loop reconnects
    after its backoff): the next connection's subscribe, acknowledged under
    the SAME number, is a NEW subscription -- its own snapshot makes the
    book CURRENT, nothing of the old one is reused -- and if that reborn sid
    gaps it is repaired on itself by get_snapshot (it used to be
    unsubscribed again and resubscribed, which the docs make a merge)."""
    s1 = [subscribed(1, 3), snap(3, 1, "K-A"), delta(3, 2, "K-A"),
          {"type": "error", "id": 0, "sid": 3, "seq": 3,
           "msg": {"code": 10, "msg": "Channel error"}},
          delta(3, 4, "K-A")]                         # never read
    s2 = [subscribed(2, 3),                           # the number reused
          snap(3, 1, "K-A", yes=(("0.48", "12"),)), delta(3, 2, "K-A")]

    def run(scripts):
        b = KWS.WsBooks(clock=lambda: NOW)
        socks = [VenueWS(sc, b, []) for sc in scripts]
        seen, ends = {}, []
        socks[-1].on_empty = lambda: seen.update(
            a=dict(b.current("K-A"), sid=b.books["K-A"]["sid"]))
        pool = list(socks)

        async def connect():
            return pool.pop(0)
        sub = KWS.Subscriber(connect, b, wanted=lambda: ["K-A"],
                             clock=lambda: NOW)
        for s in socks:
            s.sub_ref.append(sub)

        async def go():
            for _ in socks:
                try:
                    await sub.session()
                except Exception as exc:                        # noqa: BLE001
                    ends.append(type(exc).__name__)
        asyncio.run(go())
        return socks, sub, b, seen, ends
    socks, sub, b, seen, ends = run([s1, s2])
    assert ends == ["SubscriptionEnded", "ConnectionError"]
    for s in socks:
        assert s.violations == [], s.violations
        assert [m["cmd"] for m in s.sent] == ["subscribe"], s.sent
    assert socks[0].script == [delta(3, 4, "K-A")]  # nothing read after it
    assert seen["a"]["ok"] and seen["a"]["sid"] == 3
    assert seen["a"]["book"]["orderbook_fp"]["yes_dollars"] == [
        ["0.44", "5"], ["0.48", "12"]]
    assert b.stats["deltas"] == 2 and sub.resubscribes == 0
    # the reborn sid gaps: ONE get_snapshot on it, never an unsubscribe
    socks2, sub2, b2, seen2, ends2 = run([s1, s2 + [delta(3, 4, "K-A")]])
    assert [m["cmd"] for m in socks2[1].sent] == ["subscribe",
                                                 "update_subscription"]
    assert socks2[1].sent[1]["params"] == get_snapshot(3, ["K-A"])
    assert sub2.resubscribes == 1 and not seen2["a"]["ok"]
    assert all(s.violations == [] for s in socks2)


def test_the_books_alone_treat_a_reannounced_dead_sid_as_new():
    """WsBooks without the subscriber: the `subscribed` announcement of a
    dead sid number revives it; an announcement never resets a live sid."""
    b = books()
    b.on_message(snap(3, 1, "K-A"))
    assert b.on_message(delta(3, 3, "K-A")) == "GAP"
    assert b.on_message(snap(3, 4, "K-A")) == "IGNORED_DEAD_SID"
    b.on_message({"type": "subscribed", "id": 3,
                  "msg": {"channel": "orderbook_delta", "sid": 3}})
    assert b.on_message(snap(3, 1, "K-A", yes=(("0.41", "5"),))) == \
        "SNAPSHOT"
    assert b.current("K-A")["ok"]
    b.on_message({"type": "subscribed", "id": 4,
                  "msg": {"channel": "orderbook_delta", "sid": 3}})
    assert b.on_message(delta(3, 2, "K-A")) == "DELTA"
    assert b.current("K-A")["ok"]
