"""KALSHI WS ACCEPTANCE MODEL -- a model-checked proof of the owner's two
acceptance properties against the REAL WsBooks and Subscriber:

    "a late acknowledgement cannot revive dropped subscriptions, and no book
     can become CURRENT without valid sequence continuity"

THE REFERENCE ORACLE (`Oracle`) is written from the venue protocol alone. It
imports nothing from kalshi_ws and calls none of its logic:
  * every message of a subscription (sid) -- each market's
    `orderbook_snapshot` AND every `orderbook_delta` -- carries one sid-wide
    seq that rises by exactly 1; the venue sends the snapshot first and seq
    starts at 1 (docs: "Sends orderbook_snapshot first", seq minimum 1), so
    a subscription's sequence starts with its first snapshot: one with seq 1
    starts it whatever came before it on the number (a delta numbered from
    an older subscription -- red-team scenario 5 pins exactly this), any
    other must continue the message before it;
  * `subscribed` acknowledges a subscribe command (its id) with its sid; the
    venue may reuse a sid number once that subscription has ended (the client
    unsubscribed it, an error ended it, or -- the client unsubscribes every
    sid whose sequence broke -- it broke). An announcement of a number whose
    subscription is still live starts nothing new;
  * `error` may name a sid: that subscription is over for the client;
  * a disconnect ends every sid.
It RECORDS every event -- connect / disconnect, want / drop, every message the
fake venue actually delivered, every command the client sent -- and derives
from that record alone, for every market t:
  * LEGITIMATE CURRENT (`Oracle.legit`): connected; t wanted (not dropped
    since its last want); t's latest snapshot in that wanted epoch arrived on
    sid s on this connection, in s's current subscription; s was not
    unsubscribed or errored; EVERY message of s from that snapshot onward
    (the snapshot itself against the one before it, under the start rule
    above) has seq exactly previous + 1 -- sid-wide, across every market of
    s, dropped ones included; the book is that snapshot plus every later
    delta of t on s (Decimal, exact);
  * PROMISED CURRENT (`Oracle.promised`, liveness): t's latest snapshot is on
    a subscription that has never broken, errored or been unsubscribed --
    the code promises such a book is CURRENT;
  * the dropped markets, and per subscribe command which markets it named and
    when, so an ACK is judged against drops that came after its command.

PROPERTIES, checked after EVERY event (`check`; plus one internal invariant,
I1: no book is in state CURRENT on a sid whose sequence is untrusted):
  P1  a dropped market, until it is wanted again: has no book, is not in the
      resubscribe set, not in sid_markets / ticker_sid, not CURRENT, not in
      the Subscriber's subscribed set or any pending (unacknowledged)
      subscribe, not in the worker's written keys, never counted by the
      heartbeat (counts() / the worker's freshness), and never named by a
      subscribe command sent after the drop. The heartbeat's untracked count
      names no market dropped before the current session started.
  P1b NO REVIVAL BY A LATE ACK: a market is associated with a sid
      (sid_markets / ticker_sid) only if, within the market's CURRENT wanted
      epoch, either the sid's own ack answered a command naming it, or the
      sid delivered a message for it. So an ack for a command sent before
      the drop never brings the market back -- not even after it is wanted
      again (it is then subscribed by a new command of its own).
  P2  NO CURRENT WITHOUT CONTINUITY: current(t)['ok'] only when the oracle
      says CURRENT is legitimate, and then the book equals the oracle's.
  L   (liveness, only where the code promises it) a book the oracle
      promises CURRENT is CURRENT with that book; at the Subscriber level,
      at quiescence of a fault-free run, every wanted market is CURRENT and
      equals the venue's true book.
The Subscriber-level model venue also checks the ORACLE itself against
venue truth: every book the oracle calls legitimate equals the venue's true
book as of the last message delivered on its sid.

GENERATORS (deterministic):
  (a) WsBooks, EXHAUSTIVE: every event sequence up to depth D from eleven
      roots (D = 3 from `fresh` and `all_current_on_sid_1`, 2 from the
      other nine: 188,124 sequences, each checked after every event; the
      counts are pinned), over 2 sid numbers (incl. a reused number) and 3
      markets:
      snapshot / delta at seq last+1, last+2 (a lost message), last (a
      duplicate) and last-1 (a replay); the ack (bind + `subscribed`, naming
      every market subscribable on the connection, dropped ones included);
      a bare `subscribed` re-announcement; `error`; forget; want;
      disconnect / connect.
  (b) Subscriber: the real Subscriber.run against a model venue that honours
      subscribe / unsubscribe, acknowledges LATE (commands wait in its queue
      while everything else interleaves), reuses sid numbers, injects
      losses, duplicates and replays, ends sids by error, drops the
      connection; the worker's prune (prune_untracked) drops and re-wants
      markets at arbitrary points, between sessions too. 100 CI seeds: odd
      seeds inject losses, duplicates and replays (late delivery of a lost
      message among them), half of those as a storm (15-30% lost); even
      seeds are fault-free and must end with every wanted market CURRENT.
  (c) every _ORDERINGS script and the 63 drop-anywhere cases (and the other
      scripted Subscriber tests) re-run with the oracle checking every step,
      on top of their own assertions.

FOUND AND FIXED (kalshi_ws.WsBooks._first_snapshot_after): a delta that
arrived on a sid before any snapshot was not recorded, so the sid's first
snapshot was applied whatever its seq -- a replayed or duplicate snapshot
(a book missing the update that delta carried), or one past a lost message,
became CURRENT. It now has to start the subscription (seq 1) or follow that
delta; anything else gaps the sid (test_a_first_snapshot_must_agree_...).

CI runs the default sizes. Larger runs (offline): set KALSHI_PROOF_DEPTH
(every root's enumeration depth), KALSHI_PROOF_SEEDS / KALSHI_PROOF_SEED_BASE
(Subscriber seeds) and run this file. SMALL LIVE = SHADOW: read-only market
data only; nothing here touches an order path.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from collections import deque
from decimal import Decimal

import pytest

from sportsassets import kalshi_ws as KWS
from sportsassets.workers import kalshi_ws_market_data as W
from tests import test_rc6_kalshi_ws_merge_integration as MI
from tests import test_rc6_kalshi_ws_sequencing as SEQ

NOW = 1_791_600_000.0
SNAP, DELTA = "orderbook_snapshot", "orderbook_delta"


def _clock():
    return NOW


# ── wire messages (the venue's shapes, built here) ──────────────────────

def m_snapshot(sid, seq, t, yes, no):
    return {"type": SNAP, "sid": sid, "seq": seq,
            "msg": {"market_ticker": t,
                    "yes_dollars_fp": [[str(p), str(q)] for p, q in yes],
                    "no_dollars_fp": [[str(p), str(q)] for p, q in no]}}


def m_delta(sid, seq, t, price, d, side):
    return {"type": DELTA, "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "price_dollars": str(price),
                    "delta_fp": str(d), "side": side}}


def m_subscribed(cid, sid):
    return {"type": "subscribed", "id": cid,
            "msg": {"channel": "orderbook_delta", "sid": sid}}


def m_error(sid, *, cid=0, code=8):
    return {"type": "error", "id": cid, "sid": sid,
            "msg": {"code": code, "msg": "subscription error"}}


# ── the reference oracle (the venue protocol; no kalshi_ws logic) ──────

def _dec(x) -> Decimal:
    return Decimal(str(x))


def _levels(pairs) -> dict:
    out = {}
    for p, q in pairs or ():
        if _dec(q) > 0:
            out[_dec(p)] = _dec(q)
    return out


class _Sub:
    """One subscription as the client saw it: a sid number from its first
    announcement or message until the number is announced again after the
    subscription ended."""
    __slots__ = ("acks", "book", "errored", "first_snap", "last_break",
                 "msgs", "seen", "sid", "snap_at", "unsubscribed")

    def __init__(self, sid):
        self.sid = sid
        self.msgs = []          # (log index, seq, type, ticker), as delivered
        self.errored = False
        self.unsubscribed = False
        self.last_break = -1    # last position whose seq is not previous + 1
        self.first_snap = None  # position of its first snapshot
        self.acks = []          # command ids acknowledged as this subscription
        self.snap_at = {}       # ticker -> (position, log index), latest snapshot
        self.book = {}          # ticker -> that snapshot + every later delta
        self.seen = {}          # ticker -> log index of its latest message

    def ended(self) -> bool:
        return self.errored or self.unsubscribed or self.last_break >= 0

    def copy(self) -> _Sub:
        c = _Sub(self.sid)
        c.msgs = list(self.msgs)
        c.errored, c.unsubscribed = self.errored, self.unsubscribed
        c.last_break = self.last_break
        c.first_snap = self.first_snap
        c.acks = list(self.acks)
        c.snap_at = dict(self.snap_at)
        c.book = {t: {"yes": dict(bk["yes"]), "no": dict(bk["no"])}
                  for t, bk in self.book.items()}
        c.seen = dict(self.seen)
        return c


class Oracle:
    """Records what happened; derives what a correct client may and must
    hold. `live_from` is when a (re)wanted market's liveness starts: at the
    want itself ("want", WsBooks driven directly) or at the first subscribe
    command naming it ("subscribe", the Subscriber acts on the next
    message)."""

    def __init__(self, *, live_from="want"):
        self.live_from = live_from
        self.log = []
        self.connected = False
        self.wanted = set()
        self.epoch = {}             # ticker -> log index its wanted epoch began
        self.dropped = set()        # dropped, not wanted again
        self.dropped_this_session = set()   # a drop since the last disconnect
        self.subscribable = set()   # wanted at some time on this connection
        self._fresh_connection()

    def _fresh_connection(self):
        self.subs = {}              # sid number -> its current _Sub
        self.all_subs = []          # every _Sub of this connection
        self.cmds = {}              # command id -> (log index, tickers)
        self.first_sub_cmd = {}     # ticker -> first subscribe naming it (epoch)
        self.latest_snap = {}       # ticker -> (log index, _Sub)

    def copy(self) -> Oracle:
        """The same record and verdicts (the enumeration branches on it; a
        copy recorded further equals the record re-derived from scratch --
        test_the_oracle_copy_is_the_oracle)."""
        c = Oracle.__new__(Oracle)
        c.live_from = self.live_from
        c.log = list(self.log)
        c.connected = self.connected
        c.wanted = set(self.wanted)
        c.epoch = dict(self.epoch)
        c.dropped = set(self.dropped)
        c.dropped_this_session = set(self.dropped_this_session)
        c.subscribable = set(self.subscribable)
        same = {id(s): s.copy() for s in self.all_subs}
        c.all_subs = [same[id(s)] for s in self.all_subs]
        c.subs = {sid: same[id(s)] for sid, s in self.subs.items()}
        c.cmds = dict(self.cmds)
        c.first_sub_cmd = dict(self.first_sub_cmd)
        c.latest_snap = {t: (i, same[id(s)])
                         for t, (i, s) in self.latest_snap.items()}
        return c

    def verdicts(self, markets) -> tuple:
        """Everything the properties read, comparable across two oracles."""
        def sub_view(s):
            return (s.sid, tuple(s.msgs), s.errored, s.unsubscribed,
                    s.last_break, s.first_snap, tuple(s.acks),
                    sorted(s.snap_at.items()),
                    sorted((t, sorted(b["yes"].items()), sorted(
                        b["no"].items())) for t, b in s.book.items()))
        return (self.connected, sorted(self.wanted), sorted(self.dropped),
                sorted(self.dropped_this_session), sorted(self.subscribable),
                [sub_view(s) for s in self.all_subs],
                sorted((k, sub_view(s)) for k, s in self.subs.items()),
                [(t, str(self.legit(t)), str(self.promised(t)),
                  [self.justified(t, sid) for sid in WSIDS])
                 for t in markets])

    def _sub(self, sid) -> _Sub:
        s = self.subs.get(sid)
        if s is None:
            s = self.subs[sid] = _Sub(sid)
            self.all_subs.append(s)
        return s

    def record(self, entry) -> int:
        i = len(self.log)
        self.log.append(entry)
        kind = entry[0]
        if kind == "connect":
            self.connected = True
            self._fresh_connection()
            self.subscribable = set(self.wanted)
        elif kind == "disconnect":
            self.connected = False
            self._fresh_connection()
            self.subscribable = set()
            self.dropped_this_session = set()
        elif kind == "want":
            t = entry[1]
            if t not in self.wanted:
                self.wanted.add(t)
                self.epoch[t] = i
                self.dropped.discard(t)
                self.first_sub_cmd.pop(t, None)
            if self.connected:
                self.subscribable.add(t)
        elif kind == "drop":
            t = entry[1]
            if t in self.wanted:
                self.wanted.discard(t)
                self.dropped.add(t)
                self.dropped_this_session.add(t)
        elif kind == "sub":
            _, cid, tickers = entry
            self.cmds[cid] = (i, tuple(tickers))
            for t in tickers:
                if t in self.wanted and t not in self.first_sub_cmd:
                    self.first_sub_cmd[t] = i
        elif kind == "unsub":
            for sid in entry[2]:
                self._sub(sid).unsubscribed = True
        elif kind == "msg":
            self._message(i, entry[1])
        return i

    def _message(self, i, m):
        typ = m.get("type")
        if typ == "subscribed":
            sid = (m.get("msg") or {}).get("sid", m.get("sid"))
            if sid is None:
                return
            s = self.subs.get(sid)
            if s is None or s.ended():
                s = self.subs[sid] = _Sub(sid)
                self.all_subs.append(s)
            s.acks.append(m.get("id"))
            return
        if typ == "error":
            if m.get("sid") is not None:
                self._sub(m["sid"]).errored = True
            return
        if typ not in (SNAP, DELTA):
            return
        body = m.get("msg") or {}
        sid, seq, t = m.get("sid"), m.get("seq"), body.get("market_ticker")
        if sid is None or seq is None or t is None:
            return
        s = self._sub(sid)
        pos = len(s.msgs)
        broke = bool(pos) and seq != s.msgs[-1][1] + 1
        if typ == SNAP and s.first_snap is None:
            # THE SEQUENCE STARTS WITH THE FIRST SNAPSHOT: the venue sends
            # the snapshot first and seq starts at 1, so a first snapshot
            # with seq 1 begins the sequence whatever came before it on the
            # number (red-team scenario 5: a delta numbered from an older
            # subscription); any other first snapshot must continue the
            # message before it
            s.first_snap = pos
            broke = broke and seq != 1
        if broke and s.first_snap is not None:
            s.last_break = pos
        s.msgs.append((i, seq, typ, t))
        s.seen[t] = i
        if typ == SNAP:
            s.snap_at[t] = (pos, i)
            s.book[t] = {"yes": _levels(body.get("yes_dollars_fp")),
                         "no": _levels(body.get("no_dollars_fp"))}
            self.latest_snap[t] = (i, s)
        elif t in s.book and body.get("side") in ("yes", "no"):
            lv = s.book[t][body["side"]]
            p = _dec(body.get("price_dollars"))
            q = lv.get(p, Decimal(0)) + _dec(body.get("delta_fp"))
            if q > 0:
                lv[p] = q
            else:
                lv.pop(p, None)

    # ── derived verdicts ──
    def last_seq(self, sid):
        s = self.subs.get(sid)
        return s.msgs[-1][1] if s is not None and s.msgs else None

    def legit(self, t) -> dict:
        """{sid: book}: every subscription on which a CURRENT book of t is
        legitimate now (empty: no CURRENT book of t is)."""
        out = {}
        if not self.connected or t not in self.wanted:
            return out
        e = self.epoch[t]
        for sid, s in self.subs.items():
            if s.errored or s.unsubscribed:
                continue
            at = s.snap_at.get(t)
            if at is None or at[1] < e:
                continue
            # the snapshot in sequence with the message before it, and every
            # message after it in sequence too
            if s.last_break >= max(at[0], 1):
                continue
            out[sid] = s.book[t]
        return out

    def promised(self, t):
        """The book a correct client must hold CURRENT now, or None."""
        if not self.connected or t not in self.wanted:
            return None
        start = (self.epoch[t] if self.live_from == "want"
                 else self.first_sub_cmd.get(t))
        ls = self.latest_snap.get(t)
        if start is None or ls is None or ls[0] < start:
            return None
        s = ls[1]
        if self.subs.get(s.sid) is not s or s.ended():
            return None
        return s.book[t]

    def justified(self, t, sid) -> bool:
        """May the client associate t with `sid` (P1b)?"""
        if t not in self.wanted:
            return False
        e = self.epoch[t]
        for s in self.all_subs:
            if s.sid != sid:
                continue
            if s.seen.get(t, -1) >= e:
                return True
            for cid in s.acks:
                c = self.cmds.get(cid)
                if c is not None and c[0] >= e and t in c[1]:
                    return True
        return False


# ── the properties ───────────────────────────────────────────────────────

def _code_book(cur) -> dict:
    fp = cur["book"]["orderbook_fp"]
    return {"yes": {_dec(p): _dec(q) for p, q in fp["yes_dollars"]},
            "no": {_dec(p): _dec(q) for p, q in fp["no_dollars"]}}


def check(o: Oracle, b: KWS.WsBooks, *, sub=None, written=None,
          universe=()) -> list:
    """P1, P1b, P2 and L after one event; the list of violations."""
    bad = []
    assoc: dict = {}
    for sid, ms in b.sid_markets.items():
        for t in ms:
            assoc.setdefault(t, set()).add(sid)
    for t, sid in b.ticker_sid.items():
        assoc.setdefault(t, set()).add(sid)
    markets = sorted(set(universe) | set(b.books) | o.wanted | o.dropped)
    cur = {t: b.current(t) for t in markets}
    # P1: a dropped market is held nowhere
    for t in sorted(o.dropped):
        held = [name for name, hit in (
            ("book", t in b.books), ("resubscribe", t in b.resubscribe),
            ("sid_markets/ticker_sid", t in assoc),
            ("current", cur[t]["ok"]),
            ("subscribed", sub is not None and t in sub.subscribed),
            ("pending", sub is not None and any(
                t in ts for ts in sub.pending.values())),
            ("written", written is not None and t in written)) if hit]
        if held:
            bad.append(("P1_DROPPED_MARKET_HELD", t, held))
    # P1: the heartbeat counts tracked markets only
    h = W.health(b, sub, now=NOW, limits=None, pace=None)
    fr = h["freshness"]
    ok = {t for t in b.books if cur[t]["ok"]}
    if not set(b.books) <= o.wanted:
        bad.append(("P1_BOOK_OF_UNWANTED_MARKET",
                    sorted(set(b.books) - o.wanted)))
    if (fr["numerator"], fr["denominator"]) != (len(ok), len(b.books)):
        bad.append(("P1_HEARTBEAT_FRESHNESS", fr, sorted(ok)))
    n_untracked = h["untracked_dropped"]["still_subscribed_until_session_end"]
    if n_untracked > len(o.dropped_this_session):
        bad.append(("P1_HEARTBEAT_UNTRACKED_FROM_AN_ENDED_SESSION",
                    n_untracked, sorted(o.dropped_this_session)))
    # P1b: every association is justified within the market's wanted epoch
    for t in sorted(assoc):
        for sid in sorted(assoc[t], key=str):
            if not o.justified(t, sid):
                bad.append(("P1B_ASSOCIATION_NOT_JUSTIFIED", t, sid))
    # I1 (internal): no book is in state CURRENT on a sid whose sequence the
    # books no longer trust -- so current()'s own sid_seq guard is defence in
    # depth, never the deciding check (the mutation check's M09 / M23)
    for t, bk in b.books.items():
        if bk["state"] == KWS.CURRENT and (
                bk["sid"] is None or bk["sid"] in b.dead_sids
                or b.sid_seq.get(bk["sid"]) is None):
            bad.append(("I1_CURRENT_STATE_ON_AN_UNTRUSTED_SID", t, bk["sid"]))
    # P2 (safety) and L (liveness where promised)
    for t in markets:
        c = cur[t]
        mine = _code_book(c) if c["ok"] else None
        if mine is not None:
            legit = o.legit(t)
            if not any(mine == bk for bk in legit.values()):
                bad.append(("P2_CURRENT_WITHOUT_CONTINUITY", t,
                            b.books[t]["sid"], sorted(legit, key=str)))
        promised = o.promised(t)
        if promised is not None and mine != promised:
            bad.append(("L_NOT_CURRENT_WHERE_PROMISED", t, c["state"]))
    return bad


_CONTAINERS = (dict, list, set)


def _clone(x):
    """A deep copy of WsBooks state (dicts / lists / sets of immutables);
    generic, so it copies whatever state the class holds."""
    tx = type(x)
    if tx is dict:
        return {k: (_clone(v) if type(v) in _CONTAINERS else v)
                for k, v in x.items()}
    if tx is list:
        return [(_clone(v) if type(v) in _CONTAINERS else v) for v in x]
    if tx is set:
        return set(x)
    return x


def _clone_books(b):
    new = object.__new__(type(b))
    new.__dict__.update({k: _clone(v) for k, v in b.__dict__.items()})
    return new


# ── (a) WsBooks: exhaustive bounded enumeration ─────────────────────────

WA, WB, WC = "KX-A", "KX-B", "KX-C"
WMARKETS = (WA, WB, WC)
WSIDS = (1, 2)
VARIANTS = ("next", "skip", "dup", "replay")


def _w_snapshot(sid, seq, t):
    k = WMARKETS.index(t)
    return m_snapshot(sid, seq, t, [("0.4%d" % k, "%d" % (10 * seq + k + 1))],
                      [("0.5%d" % k, "%d" % (seq + k + 1))])


def _w_delta(sid, seq, t):
    k = WMARKETS.index(t)
    if seq % 3 == 0:           # takes the snapshot's YES level out
        return m_delta(sid, seq, t, "0.4%d" % k, "-%d" % (10 * seq + 50),
                       "yes")
    if seq % 3 == 1:           # a new YES level
        return m_delta(sid, seq, t, "0.3%d" % k, "%d.5" % seq, "yes")
    return m_delta(sid, seq, t, "0.5%d" % k, "%d" % (seq + 1), "no")


def _w_seq(o, sid, variant):
    last = o.last_seq(sid)
    if variant == "next":
        return 1 if last is None else last + 1
    if variant == "skip":
        return 2 if last is None else last + 2
    if variant == "dup":
        return last
    return None if last is None or last < 2 else last - 1


def w_events(o: Oracle) -> list:
    """The alphabet in state `o` (only the events defined there)."""
    ev = []
    if o.connected:
        for sid in WSIDS:
            for t in WMARKETS:
                if t not in o.subscribable:
                    continue
                for typ in (SNAP, DELTA):
                    for v in VARIANTS:
                        if _w_seq(o, sid, v) is not None:
                            ev.append(("msg", sid, t, typ, v))
            ev += [("ack", sid), ("announce", sid), ("error", sid)]
        ev.append(("disconnect",))
    else:
        ev.append(("connect",))
    for t in WMARKETS:
        ev.append(("forget", t) if t in o.wanted else ("want", t))
    return ev


def w_apply(b: KWS.WsBooks, o: Oracle, ev) -> list:
    """Apply one event to the real books; return the oracle's entries."""
    kind = ev[0]
    if kind == "msg":
        _, sid, t, typ, v = ev
        seq = _w_seq(o, sid, v)
        m = (_w_snapshot if typ == SNAP else _w_delta)(sid, seq, t)
        b.on_message(m, recv_at=NOW)
        return [("msg", m)]
    if kind == "ack":
        # the Subscriber's handling of `subscribed`: bind, then the message;
        # the command named every market subscribable on this connection
        cid = len(o.log)
        ts = sorted(o.subscribable)
        m = m_subscribed(cid, ev[1])
        b.bind(ev[1], ts)
        b.on_message(m, recv_at=NOW)
        return [("sub", cid, tuple(ts)), ("msg", m)]
    if kind == "announce":
        m = m_subscribed(None, ev[1])
        b.on_message(m, recv_at=NOW)
        return [("msg", m)]
    if kind == "error":
        m = m_error(ev[1])
        b.on_message(m, recv_at=NOW)
        return [("msg", m)]
    if kind == "forget":
        b.forget([ev[1]])
        return [("drop", ev[1])]
    if kind == "want":
        b.want([ev[1]])
        return [("want", ev[1])]
    if kind == "disconnect":
        b.on_disconnected()
        return [("disconnect",)]
    assert kind == "connect"
    b.on_connected()
    b.want(sorted(o.wanted))
    return [("connect",)]


def _derive(log) -> Oracle:
    o = Oracle()
    for e in log:
        o.record(e)
    return o


_ALL_ON_1 = [("ack", 1), ("msg", 1, WA, SNAP, "next"),
             ("msg", 1, WB, SNAP, "next"), ("msg", 1, WC, SNAP, "next")]
_DEAD_1 = _ALL_ON_1 + [("msg", 1, WA, DELTA, "skip"), ("ack", 2)]
#: where each enumeration starts (after: want A B C, connect), and the
#: depth CI enumerates from it (KALSHI_PROOF_DEPTH overrides every root)
ROOTS = {
    "fresh": ([], 3),
    # every book CURRENT on sid 1 (acknowledged)
    "all_current_on_sid_1": (_ALL_ON_1, 3),
    # sid 1 broke (a lost message) and the resubscribe was acknowledged as
    # sid 2: every book GAP, awaiting sid 2's snapshots
    "sid_1_dead_resubscribed_on_2": (_DEAD_1, 2),
    # B dropped while CURRENT on the live sid 1 (its messages still arrive)
    "b_dropped_on_live_sid_1": (_ALL_ON_1 + [("forget", WB)], 2),
    # A moved to sid 2 (a second subscription); B and C stay on sid 1
    "a_current_on_sid_2_b_c_on_sid_1": (_ALL_ON_1 + [
        ("ack", 2), ("msg", 2, WA, SNAP, "next")], 2),
    # sid 1 ended by an error while B and C awaited their snapshots
    "sid_1_errored_while_awaiting": ([("ack", 1), ("msg", 1, WA, SNAP,
                                                   "next"), ("error", 1)], 2),
    # a delta arrived on sid 1 before any snapshot of it
    "delta_before_the_first_snapshot": ([("ack", 1),
                                         ("msg", 1, WA, DELTA, "skip")], 2),
    # reconnected after every book was CURRENT (sid numbers start over)
    "reconnected_after_all_current": (_ALL_ON_1 + [
        ("msg", 1, WB, DELTA, "next"), ("disconnect",), ("connect",)], 2),
    # reconnected after sid 1 died (a dead sid never outlives a session)
    "reconnected_after_a_gap": (_DEAD_1 + [("disconnect",), ("connect",)],
                                2),
    # B dropped and wanted again while sid 1 still carries it
    "b_dropped_and_wanted_again": (_ALL_ON_1 + [("forget", WB),
                                                ("want", WB)], 2),
    # a delta, then a first snapshot that duplicates its seq: sid 1 died
    "first_snapshot_refused_after_a_delta": ([
        ("ack", 1), ("msg", 1, WA, DELTA, "skip"),
        ("msg", 1, WB, SNAP, "dup")], 2),
}
#: sequences enumerated per root at its CI depth -- pinned: the alphabet is
#: the oracle's, so the count does not depend on the code under test
CI_COUNTS = {
    "a_current_on_sid_2_b_c_on_sid_1": 2780,
    "all_current_on_sid_1": 109620,
    "b_dropped_and_wanted_again": 2228,
    "b_dropped_on_live_sid_1": 2228,
    "delta_before_the_first_snapshot": 2192,
    "first_snapshot_refused_after_a_delta": 2168,
    "fresh": 60180,
    "reconnected_after_a_gap": 1376,
    "reconnected_after_all_current": 1376,
    "sid_1_dead_resubscribed_on_2": 2204,
    "sid_1_errored_while_awaiting": 1772,
}                       # 188,124 sequences in all


def root_state(root):
    """The real books and the oracle after `root` (from: want A B C,
    connect)."""
    b = KWS.WsBooks(clock=_clock)
    o = Oracle()
    for t in WMARKETS:
        b.want([t])
        o.record(("want", t))
    b.on_connected()
    o.record(("connect",))
    for ev in ROOTS[root][0]:
        for e in w_apply(b, o, ev):
            o.record(e)
    bad = check(o, b, universe=WMARKETS)
    assert bad == [], ("root", root, bad)
    return b, o


def enumerate_root(root, depth, *, fail_fast=True, on_node=None):
    """DFS over every sequence up to `depth` from `root`; the number of
    sequences checked, and the violations (empty when the proof holds).
    Each sequence is checked after EVERY event (its prefixes are nodes)."""
    b, o = root_state(root)
    found = []
    n = [0]

    def rec(b, o, d, path):
        for ev in w_events(o):
            b2 = _clone_books(b)
            o2 = o.copy()
            for e in w_apply(b2, o, ev):
                o2.record(e)
            n[0] += 1
            if on_node is not None:
                on_node(o2)
            v = check(o2, b2, universe=WMARKETS)
            if v:
                found.append((root, path + [ev], v))
                if fail_fast:
                    raise _Found()
                continue
            if d + 1 < depth:
                rec(b2, o2, d + 1, path + [ev])
    try:
        rec(b, o, 0, [])
    except _Found:
        pass
    return n[0], found


class _Found(Exception):
    pass


def _env_int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def test_the_oracle_copy_is_the_oracle():
    """The enumeration branches on Oracle.copy(); at every node (depth 2
    from three roots, depth 1 from the rest) the copied-and-extended oracle
    derives exactly what the oracle re-derived from its whole record does."""
    nodes = [0]

    def same(o2):
        nodes[0] += 1
        assert o2.verdicts(WMARKETS) == _derive(o2.log).verdicts(WMARKETS)
    deep = ("fresh", "all_current_on_sid_1", "delta_before_the_first_snapshot")
    for root in sorted(ROOTS):
        _n, found = enumerate_root(root, 2 if root in deep else 1,
                                   on_node=same)
        assert found == []
    assert nodes[0] > 5000


@pytest.mark.parametrize("root", sorted(ROOTS))
def test_wsbooks_every_sequence_up_to_depth_d_holds_p1_p2_and_liveness(root):
    """(a) every event sequence up to the root's depth, every property after
    every event; the number of sequences is pinned."""
    depth = _env_int("KALSHI_PROOF_DEPTH", ROOTS[root][1])
    n, found = enumerate_root(root, depth)
    assert found == [], found[0]
    print("KALSHI_PROOF enumeration root=%s depth=%d sequences=%d"
          % (root, depth, n))
    if depth == ROOTS[root][1]:
        assert n == CI_COUNTS[root], (root, n)


# ── (b) the Subscriber against a model venue ────────────────────────────

SMARKETS = ("KS-A", "KS-B", "KS-C", "KS-D")


class _VenueConn:
    """The venue's side of ONE connection: commands wait in `in_q` until the
    venue gets to them (late acks), messages leave in order through
    `out_q` (one ordered socket), sid numbers are reused once released."""

    def __init__(self):
        self.in_q = deque()
        self.out_q = deque()        # (message, venue subscription id | None)
        self.subs = {}              # sid -> {"markets", "seq", "vinc"}
        self.free = []              # sid numbers released by an unsubscribe
        self.next_sid = 1
        self.sent = {}              # vinc -> [sequenced messages it sent]
        self.lost = {}              # vinc -> [messages lost on the way]
        self.last_seq = {}          # vinc -> last seq delivered


class _Socket:
    def __init__(self, sim):
        self.sim = sim

    async def send(self, s):
        self.sim._on_send(json.loads(s))

    async def recv(self):
        return await self.sim._recv()

    async def close(self):
        self.sim._on_close()


class SubscriberSim:
    """The production Subscriber.run (and the worker's prune) against a model
    venue, checked by the oracle after every event."""

    def __init__(self, seed, *, faults, chaos_steps=400, max_connections=4):
        self.seed, self.faults = seed, faults
        self.rng = r = random.Random(seed)
        self.chaos_steps = chaos_steps
        self.max_connections = max_connections
        # per-seed temperament: how late the venue acknowledges, how busy
        self.w = {"deliver": r.randint(25, 60), "process": r.randint(2, 14),
                  "activity": r.randint(8, 25), "drop": r.randint(1, 6),
                  "rewant": r.randint(1, 6), "flush": 3,
                  "venue_error": r.randint(0, 3), "disconnect": r.randint(0, 2)}
        # one faulty seed in two is a storm (a quarter of messages lost)
        storm = faults and seed % 4 == 3
        self.p_loss, self.p_dup, self.p_replay = (
            (0.0, 0.0, 0.0) if not faults else
            (r.uniform(0.15, 0.3), r.uniform(0.03, 0.08),
             r.uniform(0.1, 0.2)) if storm else
            (r.uniform(0.01, 0.06), r.uniform(0.01, 0.05),
             r.uniform(0.01, 0.05)))
        self.p_reuse = r.uniform(0.3, 0.95)
        self.truth = {t: {"yes": {Decimal("0.4%d" % k): Decimal(10 + k)},
                          "no": {Decimal("0.5%d" % k): Decimal(20 + k)}}
                      for k, t in enumerate(SMARKETS)}
        self.want = sorted(r.sample(SMARKETS, r.randint(2, len(SMARKETS))))
        self.books = KWS.WsBooks(clock=_clock)
        self.sub = KWS.Subscriber(self._connect, self.books,
                                  wanted=lambda: list(self.want),
                                  clock=_clock, backoff=(0,))
        self.written = {}
        self.oracle = Oracle(live_from="subscribe")
        for t in self.want:
            self.oracle.record(("want", t))
        self.violations = []
        self.steps = 0
        self.connections = 0
        self.vinc_n = 0
        self.truth_at = {}          # (vinc, seq) -> (ticker, its true book)
        self.vinc_of = {}           # oracle log index -> vinc
        self.done = False
        self.stop = None
        self.conn = None
        self.calm_rounds = 3
        self.calm_budget = None     # steps allowed to drain once calm
        self.counts = {"delivered": 0, "lost": 0, "duplicated": 0,
                       "replayed": 0, "acks": 0, "late_acks": 0,
                       "reused_sids": 0, "drops": 0, "rewants": 0,
                       "venue_errors": 0, "disconnects": 0, "checks": 0}

    # ── the run ──
    def run(self):
        async def main():
            self.stop = asyncio.Event()
            await asyncio.wait_for(self.sub.run(stop=self.stop), timeout=120)
        asyncio.run(main())
        return self

    def _violate(self, where, bad):
        self.violations.append((self.seed, self.steps, where, bad))
        if self.stop is not None:
            self.stop.set()

    def _check(self, where):
        self.counts["checks"] += 1
        bad = check(self.oracle, self.books, sub=self.sub,
                    written=self.written, universe=SMARKETS)
        bad += self._oracle_against_venue_truth()
        if bad:
            self._violate(where, bad)

    def _oracle_against_venue_truth(self):
        """The oracle itself: a book it calls legitimate equals the venue's
        true book as of the last message delivered on its subscription."""
        out = []
        for t in SMARKETS:
            for sid, bk in self.oracle.legit(t).items():
                s = self.oracle.subs[sid]
                pos, i = s.snap_at[t]
                vinc = self.vinc_of.get(i)
                if any(self.vinc_of.get(j) != vinc for j, *_ in s.msgs[pos:]):
                    out.append(("ORACLE_SUBSCRIPTION_MISMATCH", t, sid))
                    continue
                truth = None
                for q in range(s.msgs[-1][1], s.msgs[pos][1] - 1, -1):
                    rec = self.truth_at.get((vinc, q))
                    if rec is not None and rec[0] == t:
                        truth = rec[1]
                        break
                if truth != bk:
                    out.append(("ORACLE_DISAGREES_WITH_VENUE_TRUTH", t, sid))
        return out

    # ── the worker ──
    def _worker_refresh(self, kind):
        r = self.rng
        before = set(self.want)
        if kind == "drop":
            if len(self.want) <= 1:
                return
            gone = r.choice(self.want)
            new = [t for t in self.want if t != gone]
        else:
            cands = [t for t in SMARKETS if t not in self.want]
            if not cands:
                return
            new = sorted(self.want + [r.choice(cands)])
        self.want[:] = new
        W.prune_untracked(self.sub, self.written, self.want)
        for t in sorted(before - set(new)):
            self.oracle.record(("drop", t))
            self.counts["drops"] += 1
        for t in sorted(set(new) - before):
            self.oracle.record(("want", t))
            self.counts["rewants"] += 1
        self._check("after the worker's refresh (%s)" % kind)

    def _flush(self):
        for t, bk in self.books.books.items():
            self.written[t] = (bk["state"], bk["seq"], bk["updated_at"])

    # ── the socket ──
    async def _connect(self):
        if self.connections:
            # the worker keeps refreshing while the runtime reconnects
            for _ in range(self.rng.randint(0, 2)):
                self._worker_refresh(self.rng.choice(("drop", "rewant")))
        self.connections += 1
        self.conn = _VenueConn()
        self.oracle.record(("connect",))
        return _Socket(self)

    def _on_send(self, m):
        if m.get("cmd") == "subscribe":
            ts = list(m["params"]["market_tickers"])
            hit = set(ts) & self.oracle.dropped
            if hit:
                self._violate("send", [("P1_SUBSCRIBE_NAMES_DROPPED",
                                        sorted(hit), m)])
            self.oracle.record(("sub", m["id"], tuple(ts)))
        elif m.get("cmd") == "unsubscribe":
            live = [s for s in m["params"]["sids"]
                    if s in self.oracle.subs
                    and not self.oracle.subs[s].ended()]
            if live:
                self._violate("send", [("UNSUBSCRIBED_A_LIVE_SID", live)])
            self.oracle.record(("unsub", m["id"],
                                tuple(m["params"]["sids"])))
        self.conn.in_q.append(m)

    def _on_close(self):
        self.oracle.record(("disconnect",))
        self._check("after the disconnect")

    async def _recv(self):
        self._check("after the message")
        while True:
            if self.violations or self.done:
                raise ConnectionError("end")
            self.steps += 1
            calm = self.steps > self.chaos_steps
            if calm and self.calm_budget is None:
                # what is in flight, plus every market moving on every live
                # subscription carrying it each calm round, several times
                fan = sum(len(s["markets"]) for s in self.conn.subs.values())
                self.calm_budget = 4000 + 4 * (len(self.conn.out_q)
                                               + len(SMARKETS) * fan
                                               * self.calm_rounds)
            if calm and self.steps > self.chaos_steps + self.calm_budget:
                self._violate("calm", [("NO_QUIESCENCE", self.calm_budget)])
                continue
            act = self._choose(calm)
            if act == "deliver":
                m = self._deliver(calm)
                if m is not None:
                    return json.dumps(m)
            elif act == "process":
                self._process()
            elif act == "activity":
                self._activity()
            elif act == "calm_round":
                # every market moves once more, delivered without faults
                self.calm_rounds -= 1
                for t in SMARKETS:
                    self._activity(t)
            elif act in ("drop", "rewant"):
                self._worker_refresh(act)
            elif act == "flush":
                self._flush()
                self._check("after the flush")
            elif act == "venue_error":
                sid = self.rng.choice(sorted(self.conn.subs))
                self.conn.out_q.append((m_error(sid), None))
                self.counts["venue_errors"] += 1
            elif act == "disconnect":
                self.counts["disconnects"] += 1
                raise ConnectionError("venue dropped the connection")
            elif act == "quiet":
                self._quiescence()
                raise ConnectionError("end")

    def _choose(self, calm):
        c = self.conn
        if calm:
            if c.out_q:
                return "deliver"
            if c.in_q:
                return "process"
            return "calm_round" if self.calm_rounds else "quiet"
        w = dict(self.w)
        w["deliver"] *= bool(c.out_q)
        w["process"] *= bool(c.in_q)
        w["venue_error"] *= bool(c.subs)
        w["disconnect"] *= self.connections < self.max_connections
        acts = sorted(w)
        return self.rng.choices(acts, weights=[w[a] for a in acts])[0]

    def _process(self):
        """The venue gets to the oldest command."""
        c, r = self.conn, self.rng
        cmd = c.in_q.popleft()
        if cmd["cmd"] == "subscribe":
            if c.free and r.random() < self.p_reuse:
                sid = c.free.pop(r.randrange(len(c.free)))
                self.counts["reused_sids"] += 1
            else:
                sid = c.next_sid
                c.next_sid += 1
            self.vinc_n += 1
            sub = c.subs[sid] = {"markets": list(
                cmd["params"]["market_tickers"]), "seq": 0,
                "vinc": self.vinc_n}
            c.out_q.append((m_subscribed(cmd["id"], sid), None))
            self.counts["acks"] += 1
            for t in sub["markets"]:
                self._emit(sid, sub, t, None)
        else:
            for sid in cmd["params"]["sids"]:
                if sid in c.subs:
                    del c.subs[sid]
                    c.free.append(sid)
                    c.out_q.append(({"type": "unsubscribed", "id": cmd["id"],
                                     "sid": sid}, None))
                else:
                    c.out_q.append((m_error(sid, cid=cmd["id"], code=7),
                                    None))

    def _emit(self, sid, sub, t, change):
        sub["seq"] += 1
        bk = self.truth[t]
        if change is None:
            m = m_snapshot(sid, sub["seq"], t, sorted(bk["yes"].items()),
                           sorted(bk["no"].items()))
        else:
            side, price, d = change
            m = m_delta(sid, sub["seq"], t, price, d, side)
        self.truth_at[(sub["vinc"], sub["seq"])] = (
            t, {"yes": dict(bk["yes"]), "no": dict(bk["no"])})
        self.conn.out_q.append((m, sub["vinc"]))
        self.conn.sent.setdefault(sub["vinc"], []).append(m)

    def _activity(self, t=None):
        """The market moves: its true book changes and every live
        subscription carrying it sends the delta."""
        r = self.rng
        t = t or r.choice(SMARKETS)
        side = r.choice(("yes", "no"))
        lv = self.truth[t][side]
        if lv and r.random() < 0.5:
            price = r.choice(sorted(lv))
        else:
            price = Decimal("0.%02d" % r.randint(30, 69))
        have = lv.get(price, Decimal(0))
        if have > 0 and r.random() < 0.25:
            d = -have
        else:
            d = Decimal(r.randint(-4, 9)) + Decimal(r.choice(("0", "0.25",
                                                              "0.5")))
            if have + d < 0:
                d = -have
            if d == 0:
                d = Decimal("1.5")
        if have + d > 0:
            lv[price] = have + d
        else:
            lv.pop(price, None)
        for sid, sub in sorted(self.conn.subs.items()):
            if t in sub["markets"]:
                self._emit(sid, sub, t, (side, price, d))

    def _deliver(self, calm):
        c, r = self.conn, self.rng
        m, vinc = c.out_q.popleft()
        if vinc is not None and not calm and self.faults:
            x = r.random()
            if x < self.p_loss:
                self.counts["lost"] += 1
                c.lost.setdefault(vinc, []).append(m)
                return None
            if x < self.p_loss + self.p_dup:
                c.out_q.appendleft((m, vinc))       # it comes again next
                self.counts["duplicated"] += 1
            elif x < self.p_loss + self.p_dup + self.p_replay:
                # an older message of the same subscription arrives (again,
                # or late after it was lost): its seq is at most the last
                # one delivered, so a client can always tell
                pool = c.lost.get(vinc) if r.random() < 0.5 else None
                old = [o for o in (pool or c.sent.get(vinc, ()))
                       if o["seq"] <= c.last_seq.get(vinc, 0)]
                if old:
                    c.out_q.appendleft((m, vinc))
                    m = r.choice(old)
                    self.counts["replayed"] += 1
        i = self.oracle.record(("msg", m))
        self.counts["delivered"] += 1
        if vinc is not None:
            self.vinc_of[i] = vinc
            c.last_seq[vinc] = m["seq"]
        if m.get("type") == "subscribed":
            ts = self.oracle.cmds.get(m.get("id"), (None, ()))[1]
            if any(t in self.oracle.dropped or self.oracle.epoch.get(
                    t, -1) > self.oracle.cmds[m["id"]][0] for t in ts):
                self.counts["late_acks"] += 1
        return m

    def _quiescence(self):
        """Nothing in flight, nothing awaited: the code's promise is that
        every wanted market is CURRENT -- asserted for a fault-free run (a
        loss the code cannot see, such as a subscription's very first
        message, is outside it)."""
        self._check("at quiescence")
        if not self.faults:
            for t in self.want:
                cur = self.books.current(t)
                if not cur["ok"] or _code_book(cur) != self.truth[t]:
                    self._violate("quiescence", [(
                        "LIVENESS_AT_QUIESCENCE", t, cur["state"],
                        cur.get("why"))])
        self.done = True
        self.stop.set()


#: CI seeds (deterministic); half with losses / duplicates / replays
CI_SEEDS = 100


def _seeds():
    n = _env_int("KALSHI_PROOF_SEEDS", CI_SEEDS)
    base = _env_int("KALSHI_PROOF_SEED_BASE", 0)
    return list(range(base, base + n))


def run_seed(seed):
    sim = SubscriberSim(seed, faults=bool(seed % 2)).run()
    return sim


def test_the_subscriber_against_the_model_venue_holds_p1_p2_and_liveness():
    total = {}
    for seed in _seeds():
        sim = run_seed(seed)
        assert sim.violations == [], sim.violations[:3]
        assert sim.done, ("seed", seed, "never reached quiescence")
        for k, v in sim.counts.items():
            total[k] = total.get(k, 0) + v
    print("KALSHI_PROOF subscriber seeds=%d %s" % (len(_seeds()), total))
    # the generator exercised what the proof is about
    for k in ("lost", "duplicated", "replayed", "late_acks", "reused_sids",
              "drops", "rewants", "venue_errors", "disconnects"):
        assert total[k] > 0, (k, total)


# ── (c) the scripted Subscriber tests, re-run under the oracle ──────────

_SCRIPTED_SOCKETS: list = []


class _OracleScripted:
    """Feeds the oracle from what crosses a scripted socket (the existing
    VenueWS harness) and checks P1, P1b, P2 and L at every step."""

    def _o_init(self):
        self.oracle = Oracle(live_from="subscribe")
        self.o_started = False
        self.o_violations = []
        self.o_checks = 0
        _SCRIPTED_SOCKETS.append(self)

    def _o_start(self):
        if not self.o_started:
            self.o_started = True
            for t in self.sub_ref[0].wanted():
                self.oracle.record(("want", t))
            self.oracle.record(("connect",))

    def _o_check(self, where, *, fail=True):
        self.o_checks += 1
        bad = check(self.oracle, self.b, sub=self.sub_ref[0])
        if bad:
            self.o_violations.append((where, bad))
            if fail:
                raise AssertionError((where, bad))

    async def send(self, s):
        self._o_start()
        m = json.loads(s)
        if m["cmd"] == "subscribe":
            hit = set(m["params"]["market_tickers"]) & self.oracle.dropped
            if hit:
                self.o_violations.append(("P1_SUBSCRIBE_NAMES_DROPPED", m))
                raise AssertionError(("P1_SUBSCRIBE_NAMES_DROPPED", m))
            self.oracle.record(("sub", m["id"],
                                tuple(m["params"]["market_tickers"])))
        else:
            self.oracle.record(("unsub", m["id"],
                                tuple(m["params"]["sids"])))
        await super().send(s)

    async def close(self):
        self.oracle.record(("disconnect",))
        # (the session swallows what close raises: recorded, asserted after)
        self._o_check("after the disconnect", fail=False)
        await super().close()


class OracleVenueWS(_OracleScripted, SEQ.VenueWS):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._o_init()

    async def recv(self):
        self._o_start()
        self._o_check("before recv")
        raw = await super().recv()
        self.oracle.record(("msg", json.loads(raw)))
        return raw


class OracleVW(_OracleScripted, MI.VW):
    """MI.VW (callables in the script are the worker's wanted refresh)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._o_init()

    async def recv(self):
        self._o_start()
        self._o_check("before recv")
        while self.script and callable(self.script[0]):
            before = set(self.sub_ref[0].wanted())
            r = self.script.pop(0)()
            if r is not None:
                self.script.insert(0, r)
            after = set(self.sub_ref[0].wanted())
            for t in sorted(before - after):
                self.oracle.record(("drop", t))
            for t in sorted(after - before):
                self.oracle.record(("want", t))
            self._o_check("after the worker's refresh")
        raw = await super().recv()
        self.oracle.record(("msg", json.loads(raw)))
        return raw


_SCRIPTED = (
    [("SEQ", "test_one_gap_resubscribes_each_market_exactly_once", (o,))
     for o in sorted(SEQ._ORDERINGS)]
    + [("MI", "test_the_orderings_with_a_drop_anywhere", c)
       for c in MI._DROPS]
    + [("SEQ", n, ()) for n in (
        "test_a_late_dead_sid_snapshot_never_drops_the_live_resubscription",
        "test_a_gap_inside_a_100_market_snapshot_burst_sends_one_resubscribe",
        "test_only_dead_sids_are_ever_unsubscribed_and_each_once",
        "test_a_sid_number_the_venue_reuses_is_a_new_subscription")]
    + [("MI", n, ()) for n in (
        "test_an_ack_after_a_prune_never_brings_a_dropped_market_back",
        "test_ix_resubscribe_pending_then_forget_in_the_orderings_harness")])


def test_the_scripted_cases_are_all_here():
    """Every _ORDERINGS script and all 63 drop-anywhere cases."""
    assert len(MI._DROPS) == 63
    assert {c[2][0] for c in _SCRIPTED if c[1].startswith(
        "test_the_orderings")} == set(SEQ._ORDERINGS)
    assert len(_SCRIPTED) == 3 + 63 + 4 + 2


@pytest.mark.parametrize("mod,name,args", _SCRIPTED,
                         ids=["%s-%s-%s" % (m, n[5:40], "-".join(map(str, a)))
                              for m, n, a in _SCRIPTED])
def test_the_scripted_cases_hold_under_the_oracle(monkeypatch, mod, name,
                                                  args):
    """The test's own assertions, with the oracle checking every step."""
    _SCRIPTED_SOCKETS.clear()
    monkeypatch.setattr(SEQ, "VenueWS", OracleVenueWS)
    monkeypatch.setattr(MI, "VW", OracleVW)
    getattr({"SEQ": SEQ, "MI": MI}[mod], name)(*args)
    assert _SCRIPTED_SOCKETS, "the oracle harness was not used"
    for ws in _SCRIPTED_SOCKETS:
        assert ws.o_violations == [], ws.o_violations
        assert ws.o_checks >= 2


# ── regressions the model found, and directed cases ─────────────────────

@pytest.mark.parametrize("seq", [3, 4, 5, 9],
                         ids=["replay", "older", "duplicate", "skip"])
def test_a_first_snapshot_must_agree_with_the_deltas_before_it(seq):
    """FOUND BY THE MODEL (depth 2, every root): a delta (seq 5) arrives on
    a sid before any snapshot. It is not applied and does not start the
    sid's sequence (red-team scenario 5 pins that), but the snapshot after
    it used to be applied unchecked as the sid's first message and served
    CURRENT whatever its seq: a REPLAYED (3) or older (4) snapshot -- a book
    missing the update the delta carried -- a duplicate seq (5), or one past
    a lost message (9). Each now gaps the sid. Only the subscription's
    start (seq 1: the venue sends the snapshot first) or the message after
    the delta (seq 6) begins it."""
    b = KWS.WsBooks(clock=_clock)
    b.on_connected()
    b.want(["K-A", "K-B"])
    assert b.on_message(m_delta(1, 5, "K-A", "0.44", "5", "yes")) == \
        "IGNORED_NOT_CURRENT"
    assert b.on_message(m_snapshot(1, seq, "K-B", [("0.43", "100")], [])) \
        == "GAP"
    assert not b.current("K-B")["ok"]
    assert "K-B" in b.resubscribe
    for start in (6, 1):
        ok = KWS.WsBooks(clock=_clock)
        ok.on_connected()
        ok.on_message(m_delta(1, 5, "K-A", "0.44", "5", "yes"))
        assert ok.on_message(m_snapshot(1, start, "K-B", [("0.43", "100")],
                                        [])) == "SNAPSHOT"
        assert ok.current("K-B")["ok"]
        # and the sequence runs from that snapshot
        assert ok.on_message(m_delta(1, start + 1, "K-B", "0.43", "1",
                                     "yes")) == "DELTA"


def test_a_dropped_markets_delta_before_any_snapshot_counts_the_same():
    """The same through a dropped market's messages (_not_tracked): its
    delta before the sid's first snapshot, then a replayed snapshot -- of
    a tracked market, or of the dropped one."""
    for t in ("K-B", "K-C"):
        b = KWS.WsBooks(clock=_clock)
        b.on_connected()
        b.want(["K-A", "K-B", "K-C"])
        b.forget(["K-C"])
        assert b.on_message(m_delta(1, 5, "K-C", "0.44", "5", "yes")) == \
            "IGNORED_NOT_TRACKED"
        assert b.on_message(m_snapshot(1, 3, t, [("0.43", "100")], [])) == \
            "GAP"
        b.on_message(m_snapshot(1, 6, "K-B", [("0.43", "100")], []))
        assert not b.current("K-B")["ok"]


def test_a_late_ack_never_binds_a_market_dropped_after_its_command(
        monkeypatch):
    """P1b, directed: B is dropped after cmd 1 named it and wanted again
    before cmd 1 is acknowledged; the Subscriber has already subscribed B
    anew (cmd 2). The late ack of cmd 1 binds A alone -- B's association
    comes only from its own command or a message (the venue's sid 7 still
    carries B, and B's snapshot on it is a real snapshot)."""
    monkeypatch.setattr(MI, "VW", OracleVW)
    _SCRIPTED_SOCKETS.clear()
    want = ["K-A", "K-B"]
    ws, _sub, b, seen, ctx, go = MI.drive([], want)
    bound = {}
    ws.script = [MI.prune_to(ctx, ["K-A"]), MI.want_to(ctx, ["K-A", "K-B"]),
                 {"type": "ok", "id": 99},    # B noticed: cmd 2 sent
                 lambda: bound.update(cmds=[m["params"] for m in ws.sent]),
                 SEQ.subscribed(1, 7),        # the LATE ack of cmd 1
                 lambda: bound.update(at_ack=set(b.sid_markets.get(7, ()))),
                 SEQ.snap(7, 1, "K-A"), SEQ.snap(7, 2, "K-B"),
                 SEQ.subscribed(2, 8), SEQ.snap(8, 1, "K-B"),
                 SEQ.delta(8, 2, "K-B"), SEQ.delta(7, 3, "K-A")]
    asyncio.run(go())
    assert [c.get("market_tickers") for c in bound["cmds"]] == [
        ["K-A", "K-B"], ["K-B"]]
    assert bound["at_ack"] == {"K-A"}
    assert ws.o_violations == []
    assert seen["K-A"]["ok"] and seen["K-B"]["ok"] and seen["K-B"]["sid"] == 8
