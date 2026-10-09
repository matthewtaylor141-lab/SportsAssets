"""THE RC6.2 KALSHI WS CLIENT, ACTION BY ACTION, AGAINST THE DOCUMENTED
VENUE (tests/test_rc62_kalshi_ws_storm_regression.DocVenue).

  * one subscribe per connection; further markets by add_markets on its
    sid, bound when SENT; dropped markets by delete_markets; a market
    dropped and wanted again before its delete went out asks for a
    snapshot instead (the venue still holds it: an add would be "no action")
  * a gap: the sid's books GAP, ONE get_snapshot {sid, its markets}, CURRENT
    again only from a snapshot in sequence after the gap's baseline; a
    second gap while it is outstanding ends the session (bounded reconnect)
  * every frame carrying (sid, seq) -- ok, unsubscribed, scoped error too --
    is in the sid's sequence; under a SEPARATE control counter a control
    frame never makes a data frame look continuous
  * errors: only 10 / 25 end the subscription; `unsubscribed` from the venue
    GAPs the sid by name; frames on a sid that is not ours are counted and
    never answered by a command
  * the `ok`'s full ticker list: a market the venue lacks is GAP and added
    again once
  * storm bounds end the session by name: commands per minute, get_snapshot
    per market, the plane-hang bound
  * the session yields to the event loop (a 50,000-frame burst never holds
    it 50 ms), logs bounded diagnostics, and makes a gapped book's row
    unreadable before it reads the next frame (the reader window)."""
from __future__ import annotations

import asyncio
import json
import logging
import time

import pytest

from sportsassets import kalshi_ws as KWS
from sportsassets.workers import kalshi_ws_market_data as W
from tests import test_rc62_kalshi_ws_storm_regression as SR

NOW = SR.NOW


def snap(sid, seq, t, yes=(("0.40", "10"),)):
    return {"type": "orderbook_snapshot", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t,
                    "yes_dollars_fp": [list(x) for x in yes],
                    "no_dollars_fp": [["0.55", "10"]]}}


def delta(sid, seq, t, price="0.41", d="3", side="yes"):
    return {"type": "orderbook_delta", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "price_dollars": price,
                    "delta_fp": d, "side": side}}


def ok(cid, sid, seq, tickers=None):
    m = {"type": "ok", "id": cid, "sid": sid, "seq": seq}
    if tickers is not None:
        m["msg"] = {"market_tickers": list(tickers)}
    return m


def error(cid, code, sid=None, seq=None):
    m = {"type": "error", "id": cid, "msg": {"code": code, "msg": "e"}}
    if sid is not None:
        m.update(sid=sid, seq=seq)
    return m


class LossyVenue(SR.DocVenue):
    """DocVenue that loses chosen frames on the way (by delivery order of
    the frames it would have sent: `lose` holds 1-based positions) and can
    hold its replies to get_snapshot (`answer_get_snapshot`)."""

    def __init__(self, *, lose=(), answer_get_snapshot=True, **k):
        super().__init__(**k)
        self.lose = set(lose)
        self.n_out = 0
        self.lost = []
        self.answer_get_snapshot = answer_get_snapshot

    async def send(self, raw):
        m = json.loads(raw)
        if m.get("cmd") == "update_subscription" and \
                m["params"].get("action") == "get_snapshot" and \
                not self.answer_get_snapshot:
            self.sent.append(m)
            self.out.append(self._ok(m["id"]))
            return
        await super().send(raw)

    async def recv(self):
        while self.out:
            self.n_out += 1
            if self.n_out in self.lose:
                self.lost.append(self.out.popleft())
                continue
            break
        return await super().recv()

    def market_frame(self, t):
        """Append a delta of t on the live sid (the market moved)."""
        self.out.append({"type": "orderbook_delta", "sid": self.sub["sid"],
                         "seq": self._seq(False),
                         "msg": {"market_ticker": t, "price_dollars": "0.41",
                                 "delta_fp": "2", "side": "yes"}})


def drive(venue, wanted, *, until=None, sink=None, clock=lambda: NOW):
    """One session against `venue`; `until(books, sub)` is checked before
    every frame (the venue's probe): it returns True to look no further.
    seen["left"]: per market, the state and reason its book had each time
    it left CURRENT (recorded by a gap sink, before the next frame)."""
    books = KWS.WsBooks(clock=clock)
    seen = {"left": {}}

    async def record(ts):
        for t in ts:
            b = books.books.get(t)
            if b is not None:
                seen["left"].setdefault(t, []).append((b["state"],
                                                       b["why"]))
        if sink is not None:
            await sink(ts)
    sub = KWS.Subscriber(None, books, wanted=lambda: list(wanted),
                         clock=clock, gap_sink=record)

    def probe():
        if until is not None and "at" not in seen and until(books, sub):
            seen["at"] = venue.delivered
            seen["counts"] = books.counts()
            seen["books"] = {t: books.current(t) for t in books.books}
    venue.probe = probe

    async def connect():
        return venue
    sub.connect = connect

    async def go():
        try:
            await sub.session()
        except Exception as exc:                                # noqa: BLE001
            seen["ended"] = exc
    asyncio.run(go())
    return sub, books, seen


def _drained(v):
    return lambda b, s: not v.out


# ── one subscribe, add_markets bound when sent ──────────────────────────

def test_one_subscribe_then_add_markets_bound_when_sent(monkeypatch):
    monkeypatch.setattr(KWS, "SUBSCRIBE_CHUNK", 2)
    ts = ["K-%d" % i for i in range(5)]
    v = SR.DocVenue()
    bound = {}

    def until(books, sub):
        if sub.sid is not None and "at_ack" not in bound:
            # before any ok arrived: the add chunks are already bound
            bound["at_ack"] = sorted(books.sid_markets.get(sub.sid, ()))
        return not v.out
    sub, books, seen = drive(v, ts, until=until)
    assert v.commands() == [("subscribe", None)] + [
        ("update_subscription", "add_markets")] * 2
    assert [m["params"]["market_tickers"] for m in v.sent] == [
        ts[:2], ts[2:4], ts[4:]]
    assert all(m["params"].get("sid") == 1 for m in v.sent[1:])
    assert bound["at_ack"] == ts
    assert seen["counts"]["current"] == 5 and books.stats["gaps"] == 0
    assert books.stats["control_consumed"] == 2


# ── a gap: one get_snapshot on the same sid ─────────────────────────────

def test_a_gap_asks_the_same_sid_for_snapshots_once_and_recovers():
    ts = ["K-A", "K-B", "K-C"]
    v = LossyVenue(lose={3})          # B's snapshot (seq 2) is lost
    out = []

    def until(books, sub):
        if len(v.out) == 0 and not out:
            out.append(1)
            # the markets move after the recovery: deltas in sequence
            for t in ts:
                v.market_frame(t)
            return False
        return bool(out) and not v.out
    sub, books, seen = drive(v, ts, until=until)
    assert v.commands() == [("subscribe", None),
                            ("update_subscription", "get_snapshot")]
    gs = v.sent[1]["params"]
    assert gs["sid"] == 1 and gs["action"] == "get_snapshot"
    # every market of the sid that was not CURRENT on it (B's snapshot may
    # be the lost frame; C's was still to come) -- each named once
    assert gs["market_tickers"] == ["K-A", "K-B", "K-C"]
    assert books.stats["gaps"] == 1 and sub.resubscribes == 1
    assert seen["counts"]["current"] == 3, seen
    # recovered on the same sid; no unsubscribe, no second subscribe
    assert {b["sid"] for b in books.books.values()} <= {1, None}
    assert all(seen["books"][t]["ok"] for t in ts)


def test_a_gapped_book_is_current_again_only_from_a_snapshot_after_the_baseline():
    b = KWS.WsBooks(clock=lambda: NOW)
    b.want(["K-A", "K-B"])
    b.on_connected()
    b.bind(1, ["K-A", "K-B"])
    assert b.on_message(snap(1, 1, "K-A")) == "SNAPSHOT"
    assert b.on_message(snap(1, 2, "K-B")) == "SNAPSHOT"
    assert b.on_message(delta(1, 5, "K-A")) == "GAP"      # 3, 4 lost
    assert b.recover[1] == {"K-A", "K-B"}
    for t in ("K-A", "K-B"):
        assert not b.current(t)["ok"] and b.books[t]["why"] == KWS.R_SEQ_GAP
    # a late frame of the lost range is no snapshot after the baseline
    assert b.on_message(snap(1, 4, "K-A")) == "GAP"
    assert not b.current("K-A")["ok"]
    b.fatal = None
    # the next in sequence after the highest seen (5) restores A only
    assert b.on_message(snap(1, 6, "K-A", yes=(("0.47", "9"),))) == \
        "SNAPSHOT"
    assert b.current("K-A")["ok"] and not b.current("K-B")["ok"]
    assert b.current("K-A")["book"]["orderbook_fp"]["yes_dollars"] == \
        [["0.47", "9"]]
    assert b.on_message(delta(1, 7, "K-B")) == "IGNORED_NOT_CURRENT"
    assert b.on_message(snap(1, 8, "K-B")) == "SNAPSHOT"
    assert b.current("K-B")["ok"]


def test_a_second_gap_while_the_get_snapshot_is_outstanding_ends_the_session():
    ts = ["K-A", "K-B", "K-C"]
    # B's snapshot lost (C's then gaps the sid), then the get_snapshot's
    # `ok` lost too (A's recovery snapshot then gaps it again)
    v = LossyVenue(lose={3, 5})
    sub, books, seen = drive(v, ts)
    assert isinstance(seen["ended"], KWS.GapDuringRecovery), seen
    assert seen["ended"].code == KWS.R_GAP_DURING_RECOVERY
    assert sub.last_end_reason == KWS.R_GAP_DURING_RECOVERY
    assert sub.session_ends == {KWS.R_GAP_DURING_RECOVERY: 1}
    # one get_snapshot, never a second: the session ended instead
    assert [c for c in v.commands() if c[1] == "get_snapshot"] == [
        ("update_subscription", "get_snapshot")]
    assert books.counts()["current"] == 0          # every book GAP


def test_run_reconnects_after_a_gap_during_recovery_with_its_backoff():
    ts = ["K-A", "K-B", "K-C"]
    venues = [LossyVenue(lose={3, 5}), SR.DocVenue()]
    stop = asyncio.Event
    holder = {}

    async def connect():
        v = venues.pop(0)
        if not venues:
            holder["last"] = v
            holder["stop"].set()
        return v
    books = KWS.WsBooks(clock=lambda: NOW)
    sub = KWS.Subscriber(connect, books, wanted=lambda: list(ts),
                         clock=lambda: NOW, backoff=(0,))

    async def go():
        holder["stop"] = stop()
        await asyncio.wait_for(sub.run(stop=holder["stop"]), timeout=10)
    asyncio.run(go())
    assert sub.connections == 2
    assert sub.session_ends == {KWS.R_GAP_DURING_RECOVERY: 1}
    # the next connection subscribes each wanted market once, afresh
    assert holder["last"].commands() == [("subscribe", None)]
    assert holder["last"].sent[0]["params"]["market_tickers"] == ts


# ── the control-frame sequence rule ─────────────────────────────────────

def test_a_control_frame_never_makes_a_data_frame_look_continuous():
    """Separate control counter: B's delta (data seq 2) is lost; an `ok`
    carrying control seq 2 arrives. Taking it into the sequence would make
    data seq 3 look like the next frame -- A CURRENT without the lost
    delta. A control seq a separate counter could have reached, with a
    CURRENT book on the sid, is a gap instead."""
    b = KWS.WsBooks(clock=lambda: NOW)
    b.want(["K-A"])
    b.on_connected()
    b.bind(1, ["K-A"])
    b.command_sent(1, 8)                  # two update commands on sid 1:
    b.command_sent(1, 9)                  # the reply to the 2nd may carry 2
    assert b.on_message(snap(1, 1, "K-A")) == "SNAPSHOT"
    assert b.on_message(ok(9, 1, 2)) == "GAP"
    assert b.books["K-A"]["why"] == KWS.R_CONTROL_SEQUENCE_AMBIGUOUS
    assert b.on_message(delta(1, 3, "K-A")) == "IGNORED_NOT_CURRENT"
    assert not b.current("K-A")["ok"]


def test_a_control_frame_past_any_separate_counter_is_a_shared_slot():
    """The documented reading: the `ok` takes the sid's next seq. Past
    what a separate counter could reach (commands on the sid + slack), it
    advances the sequence with CURRENT books, and the next delta applies."""
    b = KWS.WsBooks(clock=lambda: NOW)
    b.want(["K-A"])
    b.on_connected()
    b.bind(1, ["K-A"])
    for s in range(1, 5):
        assert b.on_message(snap(1, s, "K-A") if s == 1
                            else delta(1, s, "K-A")) in ("SNAPSHOT", "DELTA")
    assert b.on_message(ok(9, 1, 5)) == "OK"
    assert b.on_message(delta(1, 6, "K-A")) == "DELTA"
    assert b.current("K-A")["ok"] and b.stats["gaps"] == 0


def test_a_separate_counter_is_learned_from_the_first_control_frame():
    b = KWS.WsBooks(clock=lambda: NOW)
    b.want(["K-A"])
    b.on_connected()
    b.bind(1, ["K-A"])
    b.on_message(snap(1, 1, "K-A"))
    b.on_message(delta(1, 2, "K-A"))
    assert b.on_message(ok(9, 1, 1)) == "OK"          # at or below last
    assert b.anchors[1]["mode"] == KWS.SEPARATE
    assert b.on_message(delta(1, 3, "K-A")) == "DELTA"
    assert b.on_message(ok(10, 1, 2)) == "OK"         # never sequenced now
    assert b.on_message(delta(1, 4, "K-A")) == "DELTA"
    assert b.on_message(delta(1, 6, "K-A")) == "GAP"  # data run broken
    assert b.stats["gaps"] == 1


def test_control_frames_before_any_data_are_taken_then_a_separate_run_is_seen():
    """Two `ok`s before any snapshot (nothing CURRENT, nothing to hide):
    taken into the sequence; then the data run starts at 1 exactly over
    them -- a separate counter, learned without a gap."""
    b = KWS.WsBooks(clock=lambda: NOW)
    b.want(["K-A"])
    b.on_connected()
    b.bind(1, ["K-A"])
    assert b.on_message(ok(2, 1, 1)) == "OK"
    assert b.on_message(ok(3, 1, 2)) == "OK"
    assert b.on_message(snap(1, 1, "K-A")) == "SNAPSHOT"
    assert b.anchors[1]["mode"] == KWS.SEPARATE and b.stats["gaps"] == 0
    assert b.on_message(delta(1, 2, "K-A")) == "DELTA"


# ── errors, the venue's unsubscribe, sids that are not ours ─────────────

@pytest.mark.parametrize("code", [10, 25])
def test_errors_10_and_25_end_the_subscription_and_the_session(code):
    script = [SR.m_subscribed(1, 1), snap(1, 1, "K-A"), snap(1, 2, "K-B"),
              error(0, code, sid=1, seq=3), snap(1, 4, "K-A")]
    sock = _Scripted(script)
    sub, books, seen = drive(sock, ["K-A", "K-B"])
    assert isinstance(seen["ended"], KWS.SubscriptionEnded)
    assert sub.last_end_reason == KWS.R_SUBSCRIPTION_ENDED
    # both books left CURRENT on the error itself, named by it
    assert seen["left"] == {"K-A": [(KWS.GAP, KWS.R_SUB_ERROR)],
                            "K-B": [(KWS.GAP, KWS.R_SUB_ERROR)]}
    assert [m["cmd"] for m in sock.sent] == ["subscribe"]
    assert sock.n == 4                    # nothing read after the error
    assert books.stats["errors_terminal"] == 1


@pytest.mark.parametrize("code", [7, 18, 26, 27])
def test_other_scoped_errors_take_their_seq_and_are_counted_not_fatal(code):
    script = [SR.m_subscribed(1, 1)] + [
        snap(1, i, "K-A") if i == 1 else delta(1, i, "K-A")
        for i in range(1, 6)] + [error(0, code, sid=1, seq=6),
                                 delta(1, 7, "K-A")]
    sock = _Scripted(script)
    sub, books, seen = drive(sock, ["K-A"], until=lambda b, s: not
                             sock.script)
    assert isinstance(seen["ended"], ConnectionError)
    assert seen["books"]["K-A"]["ok"] and books.stats["gaps"] == 0
    assert books.stats["errors_nonterminal"] == 1
    assert books.stats["errors_terminal"] == 0
    assert [m["cmd"] for m in sock.sent] == ["subscribe"]


def test_the_venues_unsubscribe_gaps_the_sid_by_name_and_ends_the_session():
    script = [SR.m_subscribed(1, 1), snap(1, 1, "K-A"),
              {"type": "unsubscribed", "id": 0, "sid": 1, "seq": 2},
              snap(1, 3, "K-A")]
    sock = _Scripted(script)
    sub, books, seen = drive(sock, ["K-A"])
    assert isinstance(seen["ended"], KWS.SubscriptionEnded)
    assert seen["left"] == {"K-A": [(KWS.GAP, KWS.R_UNSUBSCRIBED_BY_VENUE)]}
    assert sock.n == 3 and [m["cmd"] for m in sock.sent] == ["subscribe"]
    assert books.stats["unsubscribed_by_venue"] == 1


def test_frames_on_a_sid_that_is_not_ours_are_counted_and_never_answered():
    script = [SR.m_subscribed(1, 1), snap(1, 1, "K-A"),
              SR.m_subscribed(77, 5),               # not our command
              snap(5, 1, "K-A", yes=(("0.99", "1"),)), delta(5, 3, "K-A"),
              error(0, 10, sid=5, seq=4),
              {"type": "unsubscribed", "id": 0, "sid": 5, "seq": 5},
              delta(1, 2, "K-A")]
    sock = _Scripted(script)
    sub, books, seen = drive(sock, ["K-A"], until=lambda b, s: not
                             sock.script)
    assert [m["cmd"] for m in sock.sent] == ["subscribe"]
    assert books.stats["ignored_unknown_sid"] == 4
    cur = seen["books"]["K-A"]
    assert cur["ok"] and cur["book"]["orderbook_fp"]["yes_dollars"] == [
        ["0.40", "10"], ["0.41", "3"]]
    assert books.stats["gaps"] == 0 and isinstance(seen["ended"],
                                                   ConnectionError)


def test_our_subscribe_answered_by_an_error_ends_the_session():
    sock = _Scripted([error(1, 8)])
    sub, books, seen = drive(sock, ["K-A"])
    assert isinstance(seen["ended"], KWS.SubscribeRefused)


# ── the ok's full list ───────────────────────────────────────────────────

class _Forgetful(SR.DocVenue):
    """Never holds `skip` (its add is acknowledged without it)."""

    def __init__(self, skip, **k):
        super().__init__(**k)
        self.skip = set(skip)

    def _add(self, tickers):
        return super()._add([t for t in tickers if t not in self.skip])


def test_a_market_the_venues_list_lacks_is_gap_and_added_again_once(
        monkeypatch):
    monkeypatch.setattr(KWS, "SUBSCRIBE_CHUNK", 2)
    ts = ["K-A", "K-B", "K-C", "K-D"]
    v = _Forgetful({"K-D"})
    sub, books, seen = drive(v, ts, until=_drained(v))
    assert v.commands() == [("subscribe", None),
                            ("update_subscription", "add_markets"),
                            ("update_subscription", "add_markets")]
    assert v.sent[1]["params"]["market_tickers"] == ["K-C", "K-D"]
    assert v.sent[2]["params"]["market_tickers"] == ["K-D"]    # once
    assert seen["books"]["K-D"]["why"] == KWS.R_NOT_HELD_BY_VENUE
    assert not seen["books"]["K-D"]["ok"]
    assert all(seen["books"][t]["ok"] for t in ts[:3])
    assert books.stats["gaps"] == 0


# ── dropped markets: delete_markets; wanted again ───────────────────────

def test_a_dropped_market_is_deleted_on_the_sid_and_its_frames_keep_the_seq():
    ts = ["K-A", "K-B"]
    want = list(ts)
    v = LossyVenue()
    step = {"n": 0}

    def until(books, sub):
        step["n"] += 1
        if step["n"] == 4:                       # A, B CURRENT: drop B
            want[:] = ["K-A"]
            sub.forget(["K-B"])
            v.market_frame("K-B")                # in flight before delete
            v.market_frame("K-A")
        return step["n"] > 4 and not v.out
    sub, books, seen = drive(v, want, until=until)
    assert v.commands() == [("subscribe", None),
                            ("update_subscription", "delete_markets")]
    assert v.sent[1]["params"] == {"sid": 1, "market_tickers": ["K-B"],
                                   "action": "delete_markets"}
    assert set(books.books) == {"K-A"} and seen["books"]["K-A"]["ok"]
    assert books.stats["gaps"] == 0 and books.stats["deltas"] == 1


def test_a_market_wanted_again_before_its_delete_went_out_asks_a_snapshot():
    want = ["K-A", "K-B"]
    v = LossyVenue()
    step = {"n": 0}

    def until(books, sub):
        step["n"] += 1
        if step["n"] == 4:
            want[:] = ["K-A"]
            sub.forget(["K-B"])                  # dropped ...
            want[:] = ["K-A", "K-B"]             # ... and wanted again
            v.market_frame("K-A")                # before the next frame
        return step["n"] > 4 and not v.out
    sub, books, seen = drive(v, want, until=until)
    # the venue still holds B: no delete, no add (an add of a held ticker is
    # "no action": no snapshot would come) -- a get_snapshot
    assert v.commands() == [("subscribe", None),
                            ("update_subscription", "get_snapshot")]
    assert seen["books"]["K-B"]["ok"] and seen["books"]["K-A"]["ok"]


# ── the storm bounds ─────────────────────────────────────────────────────

def test_the_command_rate_bound_ends_the_session_by_name(monkeypatch):
    monkeypatch.setattr(KWS, "MAX_COMMANDS_PER_MINUTE", 3)
    monkeypatch.setattr(KWS, "SUBSCRIBE_CHUNK", 1)
    ts = ["K-%d" % i for i in range(6)]
    v = SR.DocVenue()
    sub, books, seen = drive(v, ts)
    assert isinstance(seen["ended"], KWS.CommandRateBound)
    assert len(v.sent) == 3 and sub.storms == 1
    assert sub.last_end_reason == KWS.R_COMMAND_RATE
    h = W.health(books, sub, now=NOW, limits=None, pace=None)
    assert h["session_end"]["last"] == KWS.R_COMMAND_RATE
    assert h["session_end"]["by_reason"] == {KWS.R_COMMAND_RATE: 1}


def test_the_rate_window_is_sixty_seconds(monkeypatch):
    monkeypatch.setattr(KWS, "MAX_COMMANDS_PER_MINUTE", 2)

    class Sock:
        async def send(self, s):
            pass

    def run(later):
        t = [NOW]
        sub = KWS.Subscriber(None, KWS.WsBooks(clock=lambda: t[0]),
                             wanted=list, clock=lambda: t[0])

        async def go():
            for _ in range(2):
                await sub._send(Sock(), sub.cmd.subscribe(["K"]))
            t[0] += later
            await sub._send(Sock(), sub.cmd.subscribe(["K"]))
        asyncio.run(go())
    with pytest.raises(KWS.CommandRateBound):
        run(59.9)
    run(60.0)                              # the first two left the window


def test_the_per_market_snapshot_bound_and_the_plane_hang_bound():
    sub = KWS.Subscriber(None, KWS.WsBooks(clock=lambda: NOW), wanted=list)
    for _ in range(KWS.MAX_RESUBSCRIBES_WITHOUT_RECOVERY):
        sub._count_requests(["K-A"])
    with pytest.raises(KWS.ResubscribeStorm):
        sub._count_requests(["K-A"])
    assert sub.storms == 1
    sub2 = KWS.Subscriber(None, KWS.WsBooks(clock=lambda: NOW), wanted=list)
    for _ in range(KWS.MAX_SNAPSHOT_REQUESTS_PER_MARKET):
        sub2._count_requests(["K-A"])
        sub2.unrecovered.pop("K-A")             # each recovered
    with pytest.raises(KWS.SnapshotRequestBound):
        sub2._count_requests(["K-A"])


def test_a_venue_that_never_delivers_a_requested_snapshot_is_bounded():
    """Every get_snapshot answered by `ok` and nothing else, and the
    venue keeps losing frames: never more than one get_snapshot per gap,
    and the second gap ends the session."""
    ts = ["K-A", "K-B"]
    v = LossyVenue(lose={3}, answer_get_snapshot=False)
    state = {"moved": 0}

    def until(books, sub):
        if not v.out and state["moved"] < 3:
            state["moved"] += 1
            v._seq(False)                          # one more frame lost
            v.market_frame("K-A")
        return False
    sub, books, seen = drive(v, ts, until=until)
    assert isinstance(seen["ended"], KWS.GapDuringRecovery)
    assert sum(1 for c in v.commands() if c[1] == "get_snapshot") == 1
    assert len(v.sent) <= 2


# ── the event loop is never held ─────────────────────────────────────────

class _Burst:
    """A socket with every frame already buffered: recv never suspends (a
    real socket under load), so only the session's own yields free the
    loop."""

    def __init__(self, frames):
        self.frames = frames
        self.i = 0
        self.sent = []

    async def send(self, s):
        self.sent.append(json.loads(s))

    async def recv(self):
        if self.i >= len(self.frames):
            raise ConnectionError("burst over")
        self.i += 1
        return self.frames[self.i - 1]

    async def close(self):
        pass


def burst_frames(n_markets=2000, n_frames=50_000):
    ts = ["KXB-%04d" % i for i in range(n_markets)]
    out = [json.dumps(SR.m_subscribed(1, 1))]
    seq = 0
    for t in ts:
        seq += 1
        out.append(json.dumps(snap(1, seq, t)))
    i = 0
    while len(out) < n_frames:
        seq += 1
        out.append(json.dumps(delta(1, seq, ts[i % n_markets],
                                    price="0.4%d" % (i % 10),
                                    d="1" if i % 3 else "-1")))
        i += 1
    return ts, out


def test_a_50000_frame_burst_never_holds_the_loop_50_ms():
    """A 50,000-frame burst (2,000 snapshots, 48,000 deltas) on a socket
    that never suspends: a probe task on the same loop records, between
    two of its turns, the event-loop thread's CPU time (what the session
    spent before yielding -- wall time on a shared CI host also counts the
    host descheduling the process) and the wall time."""
    ts, frames = burst_frames()
    sock = _Burst(frames)
    books = KWS.WsBooks()
    holds_cpu, holds_wall = [], []
    done = {"v": False}

    async def connect():
        return sock
    # the worker's own shape: the SAME list object every call
    sub = KWS.Subscriber(connect, books, wanted=lambda: ts)

    async def ticker():
        cpu, wall = time.thread_time(), time.perf_counter()
        while not done["v"]:
            await asyncio.sleep(0)
            c, w = time.thread_time(), time.perf_counter()
            holds_cpu.append(c - cpu)
            holds_wall.append(w - wall)
            cpu, wall = c, w

    async def go():
        tk = asyncio.create_task(ticker())
        t0 = time.perf_counter()
        try:
            await sub.session()
        except ConnectionError:
            pass
        done["v"] = True
        await tk
        return time.perf_counter() - t0
    elapsed = asyncio.run(go())
    worst = max(holds_cpu)
    print("KWS_LOOP_HOLD frames=%d elapsed_s=%.3f yields=%d "
          "max_hold_cpu_ms=%.2f max_hold_wall_ms=%.2f"
          % (len(frames), elapsed, len(holds_cpu), worst * 1000,
             max(holds_wall) * 1000))
    assert sock.i == len(frames)
    assert books.stats["gaps"] == 0 and books.stats["snapshots"] == 2000
    assert books.stats["deltas"] == len(frames) - 1 - 2000
    # the session yielded at least once per YIELD_EVERY_FRAMES frames
    assert len(holds_cpu) >= len(frames) // KWS.YIELD_EVERY_FRAMES
    assert worst < 0.050, worst


# ── bounded diagnostics ──────────────────────────────────────────────────

def test_diagnostics_are_bounded_and_carry_no_tickers(monkeypatch, caplog):
    monkeypatch.setitem(KWS._DIAG, "connections", 0)
    monkeypatch.setattr(KWS, "DIAG_MAX_CONTROL", 3)
    monkeypatch.setattr(KWS, "DIAG_MAX_COMMANDS", 2)
    monkeypatch.setattr(KWS, "SUBSCRIBE_CHUNK", 1)
    caplog.set_level(logging.INFO, logger=KWS.__name__)
    ts = ["KXSECRET-%d" % i for i in range(4)]
    for _ in range(4):
        drive(SR.DocVenue(), ts)
    lines = [r.getMessage() for r in caplog.records]
    ctrl = [x for x in lines if x.startswith("KWS_CTRL ")]
    nxt = [x for x in lines if x.startswith("KWS_NEXT ")]
    cmd = [x for x in lines if x.startswith("KWS_CMD ")]
    # three connections logged, the fourth nothing; per connection at most
    # DIAG_MAX_CONTROL control frames and DIAG_MAX_COMMANDS commands
    assert sorted({x.split()[1] for x in ctrl + cmd}) == ["c=1", "c=2",
                                                          "c=3"]
    for c in ("c=1", "c=2", "c=3"):
        assert sum(1 for x in ctrl if x.split()[1] == c) == 3
        assert sum(1 for x in cmd if x.split()[1] == c) == 2
    assert nxt and all("type=orderbook_" in x for x in nxt)
    assert ctrl[0].startswith("KWS_CTRL c=1 n=1 type=subscribed id=1 sid=1 ")
    assert any(" type=ok " in x and " seq=" in x and "prev_data_seq=1" in x
               for x in ctrl)
    assert cmd[0] == ("KWS_CMD c=1 id=%s cmd=subscribe action=None sids=- "
                      "n_tickers=1" % cmd[0].split()[2][3:])
    assert not any("KXSECRET" in x or "0.40" in x for x in lines)


# ── the reader window: the gapped row unreadable before the next frame ──

class _Rows:
    """kalshi_books_current as the worker writes it (the statements the
    proof models); records what was readable when each frame arrived."""

    def __init__(self):
        self.rows = {}

    async def execute(self, sql, *a):
        if sql.startswith("INSERT INTO kalshi_books_current"):
            self.rows[a[0]] = {"readable": True, "yes": a[3],
                               "error": None}
        elif sql == W.GAP_ROWS_SQL:
            ts, err, basis = a
            for t in ts:
                r = self.rows.get(t)
                if r is not None and r["readable"]:
                    r.update(readable=False, error=err)
        elif sql.startswith("UPDATE kalshi_books_current SET readable = "
                            "false"):
            t, err, basis = a
            if t in self.rows:
                self.rows[t].update(readable=False, error=err)
        else:
            raise AssertionError(sql)


def test_a_gapped_books_row_is_unreadable_before_the_next_frame_is_read():
    """The second review's window: between a gap and the next flush (up to
    FLUSH_EVERY_S) the row of the pre-gap book stayed readable. Now the
    session awaits the GAP write before it reads the next frame, and the
    flush then has nothing to redo."""
    rows, written = _Rows(), {}
    books_ref = {}
    lock = asyncio.Lock()

    class Pool:
        def acquire(self):
            class A:
                async def __aenter__(self):
                    return rows

                async def __aexit__(self, *x):
                    return False
            return A()

    async def get_pool():
        return Pool()
    readable_at_frame = []
    script = [SR.m_subscribed(1, 1), snap(1, 1, "K-A"), snap(1, 2, "K-B"),
              "FLUSH", delta(1, 4, "K-A"), delta(1, 5, "K-B"),
              ok(2, 1, 6), snap(1, 7, "K-A")]

    class Sock(_Scripted):
        async def recv(self):
            while self.script and self.script[0] == "FLUSH":
                self.script.pop(0)
                await W.flush(rows, books_ref["b"], written, now=NOW,
                              lock=lock)
            readable_at_frame.append({t: r["readable"]
                                      for t, r in rows.rows.items()})
            return await super().recv()
    sock = Sock(script)
    books = books_ref["b"] = KWS.WsBooks(clock=lambda: NOW)
    sub = KWS.Subscriber(None, books, wanted=lambda: ["K-A", "K-B"],
                         clock=lambda: NOW,
                         gap_sink=W.gap_sink(get_pool, books, written, lock))

    async def connect():
        return sock
    sub.connect = connect

    async def go():
        with pytest.raises(ConnectionError):
            await sub.session()
    asyncio.run(go())
    # frame 4 (the gap: seq 3 lost) -> before frame 5 is read, both rows
    # are unreadable; they stay so until a flush writes the new book
    assert readable_at_frame[3] == {"K-A": True, "K-B": True}
    assert readable_at_frame[4] == {"K-A": False, "K-B": False}
    assert rows.rows["K-B"]["error"] == "GAP:%s" % KWS.R_SEQ_GAP
    # the flush after the gap has nothing to write for B: its row already
    # holds what the book is
    assert set(written) == {"K-A", "K-B"}
    # the disconnect at the end: every row unreadable, nothing left CURRENT
    assert all(not r["readable"] for r in rows.rows.values())


class _Scripted:
    """A socket replaying a script (frames as dicts); `n` frames read."""

    def __init__(self, script):
        self.script = list(script)
        self.sent = []
        self.n = 0
        self.delivered = 0
        self.out = self.script
        self.probe = None

    async def send(self, s):
        self.sent.append(json.loads(s))

    async def recv(self):
        if self.probe is not None:
            self.probe()
        if not self.script:
            raise ConnectionError("script over")
        self.n += 1
        self.delivered += 1
        return json.dumps(self.script.pop(0))

    async def close(self):
        pass


# ── an error answering our own update command ────────────────────────────

class _Refusing(LossyVenue):
    """Answers the first `refuse` commands of `action` with a scoped error
    27 (the sid's next seq) instead of executing them."""

    def __init__(self, action, refuse, **k):
        super().__init__(**k)
        self.action, self.refuse = action, refuse

    async def send(self, raw):
        m = json.loads(raw)
        if m.get("cmd") == "update_subscription" and \
                m["params"].get("action") == self.action and self.refuse:
            self.refuse -= 1
            self.sent.append(m)
            self.out.append({"type": "error", "id": m["id"],
                             "sid": self.sub["sid"], "seq": self._seq(True),
                             "msg": {"code": 27, "msg": "Too many requests"}})
            return
        await super().send(raw)


def test_a_refused_get_snapshot_is_asked_again_and_a_second_refusal_ends():
    """A get_snapshot answered by error 27 (counted, not fatal) would leave
    its books waiting for snapshots that never come: they are asked again.
    Refused twice, the plane-hang bound ends the session (no third ask)."""
    ts = ["K-A", "K-B", "K-C"]
    v = _Refusing("get_snapshot", 1, lose={3})
    sub, books, seen = drive(v, ts, until=_drained(v))
    assert [c[1] for c in v.commands()] == [None, "get_snapshot",
                                            "get_snapshot"]
    assert seen["counts"]["current"] == 3 and books.stats["errors"] == 1
    v2 = _Refusing("get_snapshot", 2, lose={3})
    sub2, books2, seen2 = drive(v2, ts)
    assert isinstance(seen2["ended"], KWS.ResubscribeStorm)
    assert [c[1] for c in v2.commands()] == [None, "get_snapshot",
                                             "get_snapshot"]


def test_a_refused_add_markets_is_added_again_once(monkeypatch):
    monkeypatch.setattr(KWS, "SUBSCRIBE_CHUNK", 2)
    ts = ["K-A", "K-B", "K-C"]
    v = _Refusing("add_markets", 1)
    sub, books, seen = drive(v, ts, until=_drained(v))
    assert [c[1] for c in v.commands()] == [None, "add_markets",
                                            "add_markets"]
    assert seen["counts"]["current"] == 3
    v2 = _Refusing("add_markets", 2)
    sub2, books2, seen2 = drive(v2, ts, until=_drained(v2))
    assert [c[1] for c in v2.commands()] == [None, "add_markets",
                                             "add_markets"]
    assert seen2["books"]["K-C"]["why"] == KWS.R_NOT_HELD_BY_VENUE
    assert seen2["counts"]["current"] == 2
