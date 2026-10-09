"""KALSHI WS ACCEPTANCE MODEL -- a model-checked proof of the owner's two
acceptance properties against the REAL WsBooks, Subscriber and worker:

    "a late acknowledgement cannot revive dropped subscriptions, and no book
     can become CURRENT without valid sequence continuity"

THE REFERENCE ORACLE (`Oracle`) is written from the venue protocol. It
imports nothing from kalshi_ws and calls none of its logic:
  * every message of a subscription (sid) -- each market's
    `orderbook_snapshot` AND every `orderbook_delta` -- carries one sid-wide
    seq that rises by exactly 1;
  * `subscribed` answering a command this connection sent (its id) starts
    a subscription (ANCHORED): on one ordered connection its messages follow
    the ack, the snapshot first, from seq 1 (docs: "Sends orderbook_snapshot
    first", seq minimum 1). Every message of it must be the highest seq so
    far + 1 -- a late, replayed or duplicate message, or a lost one (its
    first snapshot too), breaks it. Whatever the number carried before the
    ack, with no snapshot, was not this subscription's;
  * on a number never acknowledged as ours (no ack, or an announcement
    answering none of our commands) where the sequence starts is unknown:
    deltas ahead of the first snapshot are not in it, and that snapshot must
    follow the HIGHEST of them (+ 1) -- or, ASSUMPTION S5, carry seq 1 (a
    seq-1 snapshot starts a subscription whatever preceded it there).
    S5 is not derived from the protocol; it is what red-team scenario 5's
    two tests pin. It is named, counted, and test_s5_is_the_only_assumption_
    beyond_the_protocol shows what it alone decides: CURRENT books on sids
    never acknowledged as ours, and nothing else;
  * the venue may reuse a sid number once that subscription has ended (the
    client unsubscribed it, an error ended it, or -- the client unsubscribes
    every sid whose sequence broke -- it broke). An announcement of a number
    whose subscription is live (and has a snapshot, or is anchored) starts
    nothing new;
  * `error` may name a sid: that subscription is over for the client;
  * a disconnect ends every sid.
It RECORDS every event -- connect / disconnect, want / drop, every message the
fake venue actually delivered, every command the client sent -- and derives
from that record alone, for every market t:
  * LEGITIMATE CURRENT (`Oracle.legit`): connected; t wanted (not dropped
    since its last want); t's latest snapshot in that wanted epoch arrived on
    sid s, in s's current subscription; that subscription never broke its
    sequence (from its ack, or from its first snapshot), was not
    unsubscribed and did not error -- sid-wide, across every market of s,
    dropped ones included; the book is that snapshot plus every later delta
    of t on s (Decimal, exact);
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
      subscribe command sent after the drop -- also when the drop lands
      while a subscribe is being sent. The heartbeat's untracked count names
      no market dropped before the current session started.
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
      at quiescence, every wanted market is CURRENT and equals the venue's
      true book -- with or without injected faults.
  R   THE READER PATH (reader_check): the worker's own flush, reassert and
      wanted-set retirement write an in-memory kalshi_books_current
      (BooksStore executes their statements; test_the_reader_path_on_
      postgres runs them on Postgres with the REST worker's readers). R1
      right after a flush, every row a reader takes as a current WebSocket
      book (readable, the WS basis, inside BOOK_SLA_S) is a CURRENT book with
      exactly its levels, and every CURRENT book is served; R2 a row the
      reassert re-stamps holds its book as it is now; R3 no row of a market
      the worker does not track is ever served.
The Subscriber-level model venue also checks the ORACLE itself, and the
code's every CURRENT book, against venue truth: each equals the venue's true
book as of the HIGHEST seq delivered on its subscription (a late or
replayed older message never lowers it).

GENERATORS (deterministic):
  (a) WsBooks, EXHAUSTIVE: every event sequence up to depth D from thirteen
      roots (D = 3 from `fresh` and `all_current_on_sid_1`, 2 from the
      other eleven: 197,248 sequences, each checked after every event; the
      counts are pinned), over 2 sid numbers (incl. a reused number) and 3
      markets: snapshot / delta at seq last+1, last+2 (a lost message), last
      (a duplicate), last-1 (a replay) and, after a replay, the venue's true
      next; the ack (bind + `subscribed`, naming every market subscribable
      on the connection, dropped ones included); a bare `subscribed`
      re-announcement (answering no command); `error`; forget; want;
      disconnect / connect.
  (b) Subscriber: the real Subscriber.run and the worker's wanted-set step
      (prune_untracked + retire_untracked), flush and reassert against a
      model venue that honours subscribe / unsubscribe, acknowledges LATE
      (commands wait in its queue while everything else interleaves), reuses
      sid numbers, injects losses, duplicates and replays, ends sids by
      error, drops the connection; the worker drops and re-wants markets at
      arbitrary points -- between sessions, and while a subscribe is being
      sent (chunks of two). 100 CI seeds: odd seeds inject losses,
      duplicates and replays (late delivery of a lost message among them),
      half of those as a storm (15-30% lost); every seed must end with every
      wanted market CURRENT, equal to venue truth, and served by the store.
  (c) every _ORDERINGS script and the 63 drop-anywhere cases (and the other
      scripted Subscriber tests) re-run with the oracle checking every step,
      on top of their own assertions.

FOUND AND FIXED. ca102147 (kalshi_ws.WsBooks._first_snapshot_after): a
delta ahead of a sid's first snapshot was not recorded, so that snapshot was
applied whatever its seq. The second review (of ca102147) found the first
oracle shared two of the code's rules, and so could not see: F1 a seq-1
snapshot after later messages of the same acknowledged subscription (lost,
then delivered late) was served CURRENT without them -- our ack now starts
the sequence (`bind(..., ours=True)`); F3 the first snapshot was checked
against the LAST delta before it, not the highest -- now the highest. And
on the reader path: a dropped market's row stayed readable (retired now);
the reassert re-stamped a row still holding a pre-gap book (only rows
holding their book as it is now); the flush key could not tell a fresh
snapshot from the pre-gap book at the same seq and clock tick (the book's
change stamp now); the heartbeat's key class was overwritten by a flush
key; the Subscriber put a market dropped during a send back into
`subscribed` and named it in the next chunk; a session that began with
nothing wanted never subscribed. Each has a directed test below.

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

from sportsassets import kalshi_market_data as KMD
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
    subscription ended (or, for an ack of ours, while the number had only
    carried messages ahead of any snapshot: those were not this
    subscription's). ANCHORED: it began at the venue's ack of a command of
    ours, so its sequence is known to start at 1."""
    __slots__ = ("acks", "anchored", "book", "errored", "first_snap",
                 "last_break", "msgs", "s5", "seen", "sid", "snap_at", "top",
                 "unsubscribed")

    def __init__(self, sid, *, anchored=False):
        self.sid = sid
        self.anchored = anchored
        self.msgs = []          # (log index, seq, type, ticker), as delivered
        self.errored = False
        self.unsubscribed = False
        self.last_break = -1    # last position out of sequence
        self.first_snap = None  # position of its first snapshot
        self.top = None         # the highest seq so far in its sequence
        self.s5 = None          # position assumption S5 started it at
        self.acks = []          # command ids acknowledged as this subscription
        self.snap_at = {}       # ticker -> (position, log index), latest snapshot
        self.book = {}          # ticker -> that snapshot + every later delta
        self.seen = {}          # ticker -> log index of its latest message

    def ended(self) -> bool:
        return self.errored or self.unsubscribed or self.last_break >= 0

    def copy(self) -> _Sub:
        c = _Sub(self.sid, anchored=self.anchored)
        c.msgs = list(self.msgs)
        c.errored, c.unsubscribed = self.errored, self.unsubscribed
        c.last_break = self.last_break
        c.first_snap = self.first_snap
        c.top, c.s5 = self.top, self.s5
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

    def __init__(self, *, live_from="want", s5=True):
        self.live_from = live_from
        #: assumption S5 (see _message); s5=False is the protocol alone
        self.s5 = s5
        #: how many times S5 decided a verdict the protocol alone would not
        self.s5_used = 0
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
        self.cmd_ids = set()        # every command id sent on this connection
        self.acked_ids = set()      # ids a `subscribed` answered
        self.first_sub_cmd = {}     # ticker -> first subscribe naming it (epoch)
        self.latest_snap = {}       # ticker -> (log index, _Sub)

    def copy(self) -> Oracle:
        """The same record and verdicts (the enumeration branches on it; a
        copy recorded further equals the record re-derived from scratch --
        test_the_oracle_copy_is_the_oracle)."""
        c = Oracle.__new__(Oracle)
        c.live_from = self.live_from
        c.s5, c.s5_used = self.s5, self.s5_used
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
        c.cmd_ids = set(self.cmd_ids)
        c.acked_ids = set(self.acked_ids)
        c.first_sub_cmd = dict(self.first_sub_cmd)
        c.latest_snap = {t: (i, same[id(s)])
                         for t, (i, s) in self.latest_snap.items()}
        return c

    def verdicts(self, markets) -> tuple:
        """Everything the properties read, comparable across two oracles."""
        def sub_view(s):
            return (s.sid, s.anchored, tuple(s.msgs), s.errored,
                    s.unsubscribed, s.last_break, s.first_snap, s.top, s.s5,
                    tuple(s.acks), sorted(s.snap_at.items()),
                    sorted((t, sorted(b["yes"].items()), sorted(
                        b["no"].items())) for t, b in s.book.items()))
        return (self.connected, self.s5_used, sorted(self.cmd_ids),
                sorted(self.acked_ids),
                sorted(self.wanted), sorted(self.dropped),
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
            self.cmd_ids.add(cid)
            for t in tickers:
                if t in self.wanted and t not in self.first_sub_cmd:
                    self.first_sub_cmd[t] = i
        elif kind == "unsub":
            self.cmd_ids.add(entry[1])
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
            # OURS: it answers a command this connection sent. The venue
            # then sends that subscription's messages, in order, from seq 1;
            # whatever the number carried before the ack (with no snapshot)
            # was not this subscription's
            ours = m.get("id") is not None and m.get("id") in self.cmd_ids
            if ours:
                self.acked_ids.add(m.get("id"))
            s = self.subs.get(sid)
            if s is None or s.ended() or (
                    ours and not s.anchored and s.first_snap is None):
                s = self.subs[sid] = _Sub(sid, anchored=ours)
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
        top = s.top
        if s.anchored:
            # every message of the subscription is in its sequence: the
            # first is seq 1, each later one the highest so far + 1 (a
            # replayed or late message is never "next" again)
            broke = seq != (top or 0) + 1
        else:
            # NOT ANCHORED (no ack of ours): where the sequence starts is
            # unknown. Deltas ahead of the first snapshot are not in it;
            # that snapshot must follow the highest of them (+ 1)...
            broke = top is not None and seq != top + 1
            if s.first_snap is None and typ != SNAP:
                broke = False
            elif s.first_snap is None and broke and seq == 1 and self.s5:
                # ...or, ASSUMPTION S5 (red-team scenario 5 pins it), carry
                # seq 1: it starts a subscription whatever preceded it on
                # the number. Not derived from the protocol (on one ordered
                # connection a subscription's own snapshot comes first); it
                # is counted, and test_s5_is_the_only_assumption... shows what
                # it alone decides
                broke = False
                s.s5, top = pos, None
                self.s5_used += 1
        if typ == SNAP and s.first_snap is None:
            s.first_snap = pos
        if broke:
            s.last_break = pos
        s.top = seq if top is None else max(top, seq)
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

    def top_seq(self, sid):
        s = self.subs.get(sid)
        return s.top if s is not None else None

    def legit(self, t) -> dict:
        """{sid: book}: every subscription on which a CURRENT book of t is
        legitimate now (empty: no CURRENT book of t is)."""
        out = {}
        if not self.connected or t not in self.wanted:
            return out
        e = self.epoch[t]
        for sid, s in self.subs.items():
            # the subscription never out of sequence (from its ack of ours:
            # from seq 1; otherwise from its first snapshot, itself after
            # the highest seq before it), never errored or unsubscribed
            if s.ended():
                continue
            at = s.snap_at.get(t)
            if at is None or at[1] < e:
                continue
            out[sid] = s.book[t]
        return out

    def relies_on_s5(self, t, sid) -> bool:
        """A CURRENT book of t on sid is legitimate only by assumption S5."""
        s = self.subs.get(sid)
        return s is not None and s.s5 is not None and \
            s.snap_at.get(t, (-1,))[0] >= s.s5

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
    # I2 (internal): a CURRENT book is never marked for a resubscribe (that
    # would open a second subscription for a market already served)
    for t in sorted(b.resubscribe):
        if t in b.books and b.books[t]["state"] == KWS.CURRENT:
            bad.append(("I2_CURRENT_BOOK_MARKED_FOR_RESUBSCRIBE", t))
    # the Subscriber's pending map holds unacknowledged commands only
    if sub is not None:
        acked = [k for k in sub.pending if k in o.acked_ids]
        if acked:
            bad.append(("PENDING_HOLDS_AN_ACKNOWLEDGED_COMMAND", acked))
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
VARIANTS = ("next", "skip", "dup", "replay", "resume")


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
    if variant == "resume":
        # after a replayed / duplicated message: the venue's true next (only
        # defined when it differs from "next")
        top = o.top_seq(sid)
        return None if top is None or top == last else top + 1
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
        # a `subscribed` answering none of our commands, handled as the
        # Subscriber handles it: bind (no markets, not ours), the message
        m = m_subscribed(None, ev[1])
        b.bind(ev[1], [], ours=False)
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
    # a delta arrived on sid 1 (never acknowledged) before any snapshot
    "delta_before_the_first_snapshot": ([("msg", 1, WA, DELTA, "skip")], 2),
    # the same after an announcement answering no command of ours (the
    # shape red-team scenario 5 pins)
    "announced_not_ours_then_a_delta": ([("announce", 1),
                                         ("msg", 1, WA, DELTA, "skip")], 2),
    # our ack of sid 1, then a delta ahead of its seq-1 snapshot: sid 1 died
    # (the late first snapshot would miss that delta)
    "acked_sid_broken_by_an_early_delta": ([("ack", 1),
                                            ("msg", 1, WA, DELTA, "skip")],
                                           2),
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
        ("msg", 1, WA, DELTA, "skip"), ("msg", 1, WB, SNAP, "dup")], 2),
}
#: sequences enumerated per root at its CI depth -- pinned: the alphabet is
#: the oracle's, so the count does not depend on the code under test
CI_COUNTS = {
    "a_current_on_sid_2_b_c_on_sid_1": 2816,
    "acked_sid_broken_by_an_early_delta": 2204,
    "all_current_on_sid_1": 113940,
    "announced_not_ours_then_a_delta": 2198,
    "b_dropped_and_wanted_again": 2264,
    "b_dropped_on_live_sid_1": 2264,
    "delta_before_the_first_snapshot": 2198,
    "first_snapshot_refused_after_a_delta": 2204,
    "fresh": 60396,
    "reconnected_after_a_gap": 1376,
    "reconnected_after_all_current": 1376,
    "sid_1_dead_resubscribed_on_2": 2240,
    "sid_1_errored_while_awaiting": 1772,
}                       # 197,248 sequences in all
#: with assumption S5 off (the protocol alone), depth 2 from every root:
#: the sequences ending in a CURRENT book only S5 admits -- each on a sid
#: never acknowledged as ours (test_s5_is_the_only_assumption...)
S5_ONLY_SEQUENCES = 372


def root_state(root, *, s5=True):
    """The real books and the oracle after `root` (from: want A B C,
    connect)."""
    b = KWS.WsBooks(clock=_clock)
    o = Oracle(s5=s5)
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


def enumerate_root(root, depth, *, fail_fast=True, on_node=None, s5=True):
    """DFS over every sequence up to `depth` from `root`; the number of
    sequences checked, and the violations (empty when the proof holds).
    Each sequence is checked after EVERY event (its prefixes are nodes)."""
    b, o = root_state(root, s5=s5)
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


def test_s5_is_the_only_assumption_beyond_the_protocol():
    """P2 FROM THE PROTOCOL ALONE. The same enumeration (depth 2, every
    root) under the oracle WITHOUT assumption S5. Every violation it finds
    is a CURRENT book that only S5 admits, on a sid NEVER acknowledged as
    ours (no ack, or an announcement answering none of our commands) -- the
    behaviour red-team scenario 5 pins (test_reconnect_no_pre_resync_book_
    is_served_or_persisted, test_reconnect_end_to_end_two_sessions). None is
    on a subscription the venue acknowledged as ours, which is every
    subscription the runtime opens: there P2 holds with no assumption. The
    count is pinned, so the residual is measured, not just described."""
    total = 0
    for root in sorted(ROOTS):
        _n, found = enumerate_root(root, 2, fail_fast=False, s5=False)
        for _r, path, v in found:
            b, o = root_state(root)          # the same path, S5 on
            for ev in path:
                for e in w_apply(b, o, ev):
                    o.record(e)
            for x in v:
                assert x[0] == "P2_CURRENT_WITHOUT_CONTINUITY", (root, path, x)
                t, sid = x[1], x[2]
                assert not o.subs[sid].anchored, (root, path, x)
                assert o.relies_on_s5(t, sid), (root, path, x)
        total += len(found)
    print("KALSHI_PROOF S5-only sequences (depth 2): %d" % total)
    assert total == S5_ONLY_SEQUENCES


# ── the reader path: kalshi_books_current as the worker writes it ─────────

_LEVELS = ("yes_bids", "no_bids", "yes_asks", "no_asks")


class BooksStore:
    """kalshi_books_current, executing the WebSocket worker's OWN statements
    (write_book, reassert, retire_untracked) with their SQL semantics; any
    other statement fails the proof (so a changed writer cannot slip past
    it). Readers: REST ws_current and db_freshness take a row as a current
    WebSocket book when it is readable, on the WebSocket basis and observed
    within BOOK_SLA_S (`served`)."""

    def __init__(self):
        self.rows = {}
        self.restamped = []

    async def execute(self, sql, *a):
        if sql.startswith("INSERT INTO kalshi_books_current"):
            self.rows[a[0]] = dict(zip(_LEVELS, a[3:7]), basis=a[7],
                                   readable=True, error=None,
                                   observed_at=float(a[8]))
        elif sql.startswith("UPDATE kalshi_books_current SET readable = "
                            "false") and "NOT (ticker = ANY" in sql:
            basis, err, keep = a
            for t, r in self.rows.items():
                if r["basis"] == basis and r["readable"] and t not in keep:
                    r.update(readable=False, error=err)
        elif sql.startswith("UPDATE kalshi_books_current SET readable = "
                            "false"):
            t, err, basis = a
            r = self.rows.get(t)
            if r is not None and r["basis"] == basis:
                r.update(readable=False, error=err)
        elif sql.startswith("UPDATE kalshi_books_current SET observed_at"):
            ts, at, basis = a
            self.restamped = [t for t in ts if t in self.rows
                              and self.rows[t]["basis"] == basis
                              and self.rows[t]["readable"]]
            for t in self.restamped:
                self.rows[t]["observed_at"] = float(at)
        else:
            raise AssertionError(("a statement the proof does not model",
                                  sql))

    def served(self, now) -> dict:
        return {t: r for t, r in self.rows.items()
                if r["basis"] == KWS.BOOK_BASIS and r["readable"]
                and now - r["observed_at"] <= KMD.BOOK_SLA_S}


def _drive(coro):
    """Run a worker coroutine over the in-memory store (it never
    suspends)."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value
    raise AssertionError("the store suspended")


def _row_levels(books, t, now) -> dict:
    """The levels write_book writes for t's CURRENT book."""
    b = KMD.book_from_orderbook(books.current(t)["book"], observed_at=now)
    return {k: W._lv(b.get(k)) for k in _LEVELS}


def reader_check(store, books, want, *, now, synced, restamped=()) -> list:
    """R (the reader path), the list of violations:
      R1 (synced: right after a flush) every row a reader takes as a current
         WebSocket book is a CURRENT book with exactly its levels, and every
         CURRENT book is served;
      R2 a row the reassert just re-stamped holds its CURRENT book as it is
         now (never a pre-gap book);
      R3 (always) no row of a market the worker does not track is served."""
    bad = []
    served = store.served(now)
    for t, r in sorted(served.items()):
        if t not in want:
            bad.append(("R3_SERVED_UNTRACKED", t))
        elif synced or t in restamped:
            cur = books.current(t)
            if not cur["ok"]:
                bad.append(("R_SERVED_NOT_CURRENT", t, cur["state"]))
            elif {k: r[k] for k in _LEVELS} != _row_levels(books, t, now):
                bad.append(("R_SERVED_ANOTHER_BOOK", t))
    if synced:
        for t in sorted(books.books):
            if books.current(t)["ok"] and t not in served:
                bad.append(("R1_CURRENT_NOT_SERVED", t))
    return bad


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
        self.max_seq = {}           # vinc -> highest seq delivered


class _Socket:
    def __init__(self, sim):
        self.sim = sim

    async def send(self, s):
        self.sim._on_send(json.loads(s))
        # a send can suspend (write backpressure): the worker's pass runs
        # then, between the client's records of the command and its return
        self.sim._during_send()

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
                  "rewant": r.randint(1, 6), "flush": 3, "reassert": 2,
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
        self.p_send_refresh = r.uniform(0.05, 0.3)
        self.truth = {t: {"yes": {Decimal("0.4%d" % k): Decimal(10 + k)},
                          "no": {Decimal("0.5%d" % k): Decimal(20 + k)}}
                      for k, t in enumerate(SMARKETS)}
        self.want = sorted(r.sample(SMARKETS, r.randint(2, len(SMARKETS))))
        self.books = KWS.WsBooks(clock=_clock)
        self.sub = KWS.Subscriber(self._connect, self.books,
                                  wanted=lambda: list(self.want),
                                  clock=_clock, backoff=(0,))
        self.written = {}
        #: kalshi_books_current, written by the worker's own code
        self.store = BooksStore()
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
                       "venue_errors": 0, "disconnects": 0, "checks": 0,
                       "refresh_during_send": 0, "flushes": 0,
                       "rows_written": 0, "reasserts": 0, "restamped": 0,
                       "reader_checks": 0}

    # ── the run ──
    def run(self):
        async def main():
            self.stop = asyncio.Event()
            await asyncio.wait_for(self.sub.run(stop=self.stop), timeout=120)
        # subscribes go out in chunks of two (production: 100), so a drop
        # while one chunk is being sent meets the chunks after it
        real = KWS.chunks
        KWS.chunks = lambda xs, n=2: real(xs, n)
        try:
            asyncio.run(main())
        finally:
            KWS.chunks = real
        return self

    def _violate(self, where, bad):
        self.violations.append((self.seed, self.steps, where, bad))
        if self.stop is not None:
            self.stop.set()

    def _check(self, where):
        self.counts["checks"] += 1
        bad = check(self.oracle, self.books, sub=self.sub,
                    written=self.written, universe=SMARKETS)
        bad += self._against_venue_truth()
        bad += reader_check(self.store, self.books, self.want, now=NOW,
                            synced=False)
        if bad:
            self._violate(where, bad)

    def _truth(self, t, sid):
        """The venue's true book of t on the subscription that sid's latest
        snapshot of t came from, as of the HIGHEST seq delivered on that
        subscription (a late or replayed older message never lowers it);
        a problem string when it cannot be told."""
        s = self.oracle.subs.get(sid)
        if s is None or t not in s.snap_at:
            return "NO_SNAPSHOT_OF_IT_ON_ITS_SID"
        pos, i = s.snap_at[t]
        vinc = self.vinc_of.get(i)
        if any(self.vinc_of.get(j) != vinc for j, *_ in s.msgs[pos:]):
            return "ORACLE_SUBSCRIPTION_MISMATCH"
        for q in range(self.conn.max_seq[vinc], s.msgs[pos][1] - 1, -1):
            rec = self.truth_at.get((vinc, q))
            if rec is not None and rec[0] == t:
                return rec[1]
        return "NO_TRUTH"

    def _against_venue_truth(self):
        """Every book the oracle calls legitimate, and every book the code
        holds CURRENT, equals the venue's true book as of the highest seq
        delivered on its subscription."""
        out = []
        for t in SMARKETS:
            for sid, bk in self.oracle.legit(t).items():
                truth = self._truth(t, sid)
                if truth != bk:
                    out.append(("ORACLE_DISAGREES_WITH_VENUE_TRUTH", t, sid,
                                truth if isinstance(truth, str) else ""))
            cur = self.books.current(t)
            if cur["ok"]:
                sid = self.books.books[t]["sid"]
                truth = self._truth(t, sid)
                if truth != _code_book(cur):
                    out.append(("CURRENT_BOOK_IS_NOT_VENUE_TRUTH", t, sid,
                                truth if isinstance(truth, str) else ""))
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
        # the worker's wanted-set step (workers/kalshi_ws_market_data.run)
        W.prune_untracked(self.sub, self.written, self.want)
        _drive(W.retire_untracked(self.store, self.want))
        for t in sorted(before - set(new)):
            self.oracle.record(("drop", t))
            self.counts["drops"] += 1
        for t in sorted(set(new) - before):
            self.oracle.record(("want", t))
            self.counts["rewants"] += 1
        self._check("after the worker's refresh (%s)" % kind)

    def _during_send(self):
        if self.rng.random() < self.p_send_refresh and not self.done:
            self.counts["refresh_during_send"] += 1
            self._worker_refresh(self.rng.choice(("drop", "rewant")))

    def _flush(self):
        """The worker's flush, then the reader view: every row a reader
        takes as a current WS book is a CURRENT book, with its levels, and
        every CURRENT book has one."""
        self.counts["flushes"] += 1
        self.counts["rows_written"] += _drive(W.flush(
            self.store, self.books, self.written, now=NOW))
        self.counts["reader_checks"] += 1
        bad = reader_check(self.store, self.books, self.want, now=NOW,
                           synced=True)
        if bad:
            self._violate("after the flush", bad)

    def _reassert(self):
        """The worker's reassert, due when the flush is not: it re-stamps
        only rows holding their CURRENT book as it is now."""
        self.counts["reasserts"] += 1
        self.store.restamped = []
        _drive(W.reassert(self.store, self.books, now=NOW,
                          written=self.written))
        self.counts["restamped"] += len(self.store.restamped)
        self.counts["reader_checks"] += 1
        bad = reader_check(self.store, self.books, self.want, now=NOW,
                           synced=False, restamped=self.store.restamped)
        if bad:
            self._violate("after the reassert", bad)

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
            elif act == "reassert":
                self._reassert()
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
            c.max_seq[vinc] = max(m["seq"], c.max_seq.get(vinc, 0))
        if m.get("type") == "subscribed":
            ts = self.oracle.cmds.get(m.get("id"), (None, ()))[1]
            if any(t in self.oracle.dropped or self.oracle.epoch.get(
                    t, -1) > self.oracle.cmds[m["id"]][0] for t in ts):
                self.counts["late_acks"] += 1
        return m

    def _quiescence(self):
        """Nothing in flight, nothing awaited: every wanted market is
        CURRENT with the venue's true book, and after the worker's flush
        the store serves exactly that -- faults or not. Every subscription
        here is acknowledged as ours, so a lost, duplicated or replayed
        message (its very first one too) is seen at the next message of the
        subscription, and each calm round moves every market on every live
        subscription."""
        self._check("at quiescence")
        self._flush()
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
              "drops", "rewants", "venue_errors", "disconnects",
              "refresh_during_send", "rows_written", "restamped",
              "reader_checks"):
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
    a lost message (9). Each now gaps the sid. On this sid -- never
    acknowledged as ours -- only the message after the delta (seq 6) or,
    by assumption S5, seq 1 begins it (on a sid acknowledged as ours the
    delta itself gaps the sid: test_our_ack_starts_the_sequence...)."""
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


# ── the second review (ca102147): F1, F3, the reader path, the Subscriber ──

def _acked(tickers, sid=1):
    """WsBooks after want, connect and the ack of our command 1 as `sid`
    (the Subscriber's handling: bind, then the message)."""
    b = KWS.WsBooks(clock=_clock)
    b.want(tickers)
    b.on_connected()
    b.bind(sid, tickers)
    assert b.on_message(m_subscribed(1, sid)) == "SUBSCRIBED"
    return b


_A, _B = "K-A", "K-B"
_F1_CASES = {
    # its seq-2 delta delivered first, the seq-1 snapshot late (the
    # reviewers' repro, and CI seeds 7 19 47 55 63 79 83 87 95 of ca102147)
    "late_first_snapshot": [
        (m_delta(1, 2, _A, "0.44", "5", "yes"), "GAP"),
        (m_snapshot(1, 1, _A, [("0.43", "100")], []), "IGNORED_DEAD_SID")],
    # three deltas, then the seq-1 snapshot (mutation review's probe)
    "three_deltas_then_seq_1": [
        (m_delta(1, 2, _A, "0.40", "5", "yes"), "GAP"),
        (m_delta(1, 3, _A, "0.41", "7", "yes"), "IGNORED_NOT_CURRENT"),
        (m_delta(1, 4, _A, "0.40", "-100", "yes"), "IGNORED_NOT_CURRENT"),
        (m_snapshot(1, 1, _A, [("0.40", "100")], []), "IGNORED_DEAD_SID")],
    # seq 1 (A's snapshot) lost: B's snapshot arrives as the first message
    "lost_first_snapshot": [
        (m_snapshot(1, 2, _B, [("0.43", "100")], []), "GAP")],
    # a delta at seq 1, then a snapshot duplicating seq 1
    "duplicate_seq_1": [
        (m_delta(1, 1, _A, "0.40", "5", "yes"), "IGNORED_NOT_CURRENT"),
        (m_snapshot(1, 1, _B, [("0.43", "100")], []), "GAP")],
}


@pytest.mark.parametrize("case", sorted(_F1_CASES))
def test_our_ack_starts_the_sequence_so_no_late_first_snapshot_is_current(
        case):
    """F1 (review of ca102147; the code and the first oracle shared the
    rule). After our ack of sid 1 the venue sends that subscription's
    messages in order from seq 1, the snapshot first. A seq-1 snapshot
    after the subscription's own later messages (lost, then delivered
    late; or a duplicate seq) used to be served CURRENT without what those
    messages carried, and a lost first snapshot went unnoticed. Now the
    first message that is not the subscription's next gaps the sid, every
    market bound to it is resubscribed, and only the resubscription's own
    snapshot restores CURRENT."""
    b = _acked([_A, _B])
    o = Oracle()
    for t in (_A, _B):
        o.record(("want", t))
    o.record(("connect",))
    o.record(("sub", 1, (_A, _B)))
    o.record(("msg", m_subscribed(1, 1)))
    for m, want in _F1_CASES[case]:
        assert b.on_message(m) == want, (case, m)
        o.record(("msg", m))
        assert check(o, b, universe=(_A, _B)) == []
    assert not b.current(_A)["ok"] and not b.current(_B)["ok"]
    assert {_A, _B} <= b.resubscribe and 1 in b.dead_sids
    b.bind(2, [_A, _B])
    b.on_message(m_subscribed(2, 2))
    assert b.on_message(m_snapshot(2, 1, _A, [("0.47", "9")], [])) == \
        "SNAPSHOT"
    assert b.current(_A)["book"]["orderbook_fp"]["yes_dollars"] == \
        [["0.47", "9"]]


def test_the_subscriber_resubscribes_a_subscription_whose_first_snapshot_came_late():
    """F1 end to end (the shape of CI seed 55 of ca102147): the ack of our
    subscribe as sid 2, its seq-2 delta, then its seq-1 snapshot late. The
    late snapshot is never served; sid 2 is unsubscribed and the market
    resubscribed; the new subscription's snapshot is the first book
    served."""
    script = [m_subscribed(1, 2), m_delta(2, 2, "KS-A", "0.44", "5", "yes"),
              m_snapshot(2, 1, "KS-A", [("0.43", "100")], []),
              m_subscribed(3, 4),
              m_snapshot(4, 1, "KS-A", [("0.43", "100"), ("0.44", "5")], [])]

    class Sock:
        sent: list = []

        async def send(self, s):
            self.sent.append(json.loads(s))

        async def recv(self):
            if not script:
                raise ConnectionError("end")
            return json.dumps(script.pop(0))

        async def close(self):
            pass
    sock = Sock()
    books = KWS.WsBooks(clock=_clock)
    served = []
    real = books.on_message

    def watch(m, **k):
        out = real(m, **k)
        cur = books.current("KS-A")
        served.append(cur["book"]["orderbook_fp"]["yes_dollars"]
                      if cur["ok"] else None)
        return out
    books.on_message = watch

    async def connect():
        return sock
    sub = KWS.Subscriber(connect, books, wanted=lambda: ["KS-A"],
                         clock=_clock)
    with pytest.raises(ConnectionError):
        asyncio.run(sub.session())
    assert [(m["cmd"], m["params"]) for m in sock.sent] == [
        ("subscribe", {"channels": ["orderbook_delta"],
                       "market_tickers": ["KS-A"]}),
        ("unsubscribe", {"sids": [2]}),
        ("subscribe", {"channels": ["orderbook_delta"],
                       "market_tickers": ["KS-A"]})]
    assert [x for x in served if x] == [[["0.43", "100"], ["0.44", "5"]]]


_F3_CASES = {
    # sid 1: seq 1 snap A, seq 2 delta A, seq 3 snap B, seq 4 delta B;
    # delivered 4, then the lost 2, then the lost 3 (the reviewer's repro)
    "delta_4_delta_2_snap_3": ([m_delta(1, 4, _B, "0.47", "7", "yes"),
                                m_delta(1, 2, _A, "0.44", "5", "yes")],
                               m_snapshot(1, 3, _B, [("0.43", "100")], [])),
    "delta_2_delta_1_snap_2": ([m_delta(1, 2, _A, "0.44", "5", "yes"),
                                m_delta(1, 1, _A, "0.44", "5", "yes")],
                               m_snapshot(1, 2, _B, [("0.43", "100")], [])),
}


@pytest.mark.parametrize("dropped", [False, True],
                         ids=["tracked", "through_a_dropped_market"])
@pytest.mark.parametrize("case", sorted(_F3_CASES))
def test_an_unacknowledged_first_snapshot_must_follow_the_highest_seq_before_it(
        case, dropped):
    """F3 (review of ca102147). On a sid never acknowledged as ours, the
    first snapshot was checked against the LAST delta before it: a replayed
    older delta in between lowered the mark, and a snapshot older than (or
    equal to) a delta already received passed as its successor, CURRENT
    without that delta. It is now checked against the HIGHEST seq -- for a
    tracked market's deltas and a dropped market's (_not_tracked) alike."""
    pre, snap = _F3_CASES[case]
    b = KWS.WsBooks(clock=_clock)
    b.want([_A, _B, "K-C"])
    b.on_connected()
    if dropped:
        b.forget(["K-C"])
        pre = [dict(m, msg=dict(m["msg"], market_ticker="K-C")) for m in pre]
    for m in pre:
        assert b.on_message(m) in ("IGNORED_NOT_CURRENT",
                                   "IGNORED_NOT_TRACKED")
    assert b.on_message(snap) == "GAP"
    assert b.books[_B]["why"] == KWS.R_SNAPSHOT_OUT_OF_SEQUENCE
    assert not b.current(_B)["ok"]
    # the snapshot after the highest seq does begin the sequence
    ok = KWS.WsBooks(clock=_clock)
    ok.want([_A, _B])
    ok.on_connected()
    for m in _F3_CASES[case][0]:
        ok.on_message(m)
    top = max(m["seq"] for m in _F3_CASES[case][0])
    assert ok.on_message(m_snapshot(1, top + 1, _B, [("0.43", "1")], [])) \
        == "SNAPSHOT"
    assert ok.on_message(m_delta(1, top + 2, _B, "0.43", "1", "yes")) == \
        "DELTA"


# ── the worker's writer and its readers ──

def test_the_flush_key_tells_every_version_of_a_book_apart():
    """(review of ca102147) The flush key was (state, seq, updated_at). A
    gap, the re-announced sid number and a fresh snapshot at the same seq
    inside one clock tick left the key unchanged: the row kept the pre-gap
    book, readable, and the reassert kept it fresh. The key is now the
    book's change stamp."""
    store, written = BooksStore(), {}
    b = _acked([_A])
    b.on_message(m_snapshot(1, 1, _A, [("0.40", "10")], []))
    _drive(W.flush(store, b, written, now=NOW))
    before = dict(b.books[_A])
    assert b.on_message(m_delta(1, 3, _A, "0.40", "1", "yes")) == "GAP"
    b.bind(1, [_A])                        # sid 1 reused for the resubscribe
    b.on_message(m_subscribed(2, 1))
    assert b.on_message(m_snapshot(1, 1, _A, [("0.45", "7")], [])) == \
        "SNAPSHOT"
    after = b.books[_A]
    assert (after["state"], after["seq"], after["updated_at"]) == \
        (before["state"], before["seq"], before["updated_at"])
    assert W.flush_key(after) != W.flush_key(before)
    assert _drive(W.flush(store, b, written, now=NOW)) == 1
    assert reader_check(store, b, [_A], now=NOW, synced=True) == []


def test_reassert_never_restamps_a_row_holding_a_pre_gap_book():
    """(review of ca102147) A pass where the reassert is due and the flush
    is not: the market's sid gapped and its resubscription's snapshot
    arrived since the last flush, so the book is CURRENT again -- but its
    row still holds the PRE-GAP book, which the reassert used to re-stamp
    fresh. Only a row holding the book as it is now is re-stamped."""
    store, written = BooksStore(), {}
    b = _acked([_A])
    b.on_message(m_snapshot(1, 1, _A, [("0.40", "10")], []))
    _drive(W.flush(store, b, written, now=NOW))
    assert b.on_message(m_delta(1, 3, _A, "0.40", "1", "yes")) == "GAP"
    b.bind(2, [_A])
    b.on_message(m_subscribed(2, 2))
    assert b.on_message(m_snapshot(2, 1, _A, [("0.45", "7")], [])) == \
        "SNAPSHOT"
    assert _drive(W.reassert(store, b, now=NOW + 5, written=written)) == 0
    assert store.rows[_A]["observed_at"] == NOW
    _drive(W.flush(store, b, written, now=NOW + 6))
    assert reader_check(store, b, [_A], now=NOW + 6, synced=True) == []
    assert _drive(W.reassert(store, b, now=NOW + 7, written=written)) == 1
    assert store.rows[_A]["observed_at"] == NOW + 7


def test_a_market_the_worker_drops_has_its_row_retired_in_the_same_pass():
    """(review of ca102147) A dropped market's row stayed readable at its
    last observed_at: REST ws_current and db_freshness took it as a current
    WebSocket book (REST then skipped polling it) for up to BOOK_SLA_S, and
    a row an earlier process left (its market no longer wanted at restart)
    stayed readable for ever. The wanted-set refresh now retires every
    readable WebSocket row outside the set; a REST row is not touched."""
    store, written = BooksStore(), {}
    b = _acked([_A, _B])
    b.on_message(m_snapshot(1, 1, _A, [("0.40", "10")], []))
    b.on_message(m_snapshot(1, 2, _B, [("0.41", "10")], []))
    _drive(W.flush(store, b, written, now=NOW))
    store.rows["K-OLD"] = dict(store.rows[_A])           # an earlier process
    store.rows["K-REST"] = dict(store.rows[_A], basis="KALSHI_REST_BOOK")
    sub = KWS.Subscriber(None, b, wanted=lambda: [_A])
    assert W.prune_untracked(sub, written, [_A]) == 1
    _drive(W.retire_untracked(store, [_A]))
    assert set(store.served(NOW)) == {_A}
    for t in (_B, "K-OLD"):
        assert store.rows[t]["readable"] is False
        assert store.rows[t]["error"] == W.UNTRACKED
    assert store.rows["K-REST"]["readable"] is True
    assert reader_check(store, b, [_A], now=NOW, synced=True) == []


def test_the_run_loop_wires_the_writer_as_the_proof_models_it(monkeypatch):
    """The real run() loop (fake venue, pool and clock), pass by pass:
    every heartbeat reports the key CLASS (run() named its flush loop
    variable `key`, overwriting it: after the first flush with a book the
    beat's `key` was a book's flush tuple -- review of ca102147); a pass
    whose reassert is due and whose flush is not leaves a row holding a
    pre-gap book unstamped (run() passes the flush's keys); the wanted-set
    refresh that drops a market retires its row (UNTRACKED)."""
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import ed25519
    pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
        ser.Encoding.PEM, ser.PrivateFormat.PKCS8,
        ser.NoEncryption()).decode()
    env = {KWS.KEY_ID_ENV: "kid", KWS.PRIVATE_KEY_PEM_ENV: pem}
    a = "KXT-EV1-A"
    levels = {a: [("0.40", "10")]}
    beats, seen = [], {}

    class Sock:
        def __init__(self):
            self.q = asyncio.Queue()
            self.sid = 0

        async def send(self, s):
            m = json.loads(s)
            if m["cmd"] == "subscribe":
                self.sid += 1
                self.q.put_nowait(json.dumps(m_subscribed(m["id"],
                                                          self.sid)))
                for i, t in enumerate(m["params"]["market_tickers"]):
                    self.q.put_nowait(json.dumps(m_snapshot(
                        self.sid, i + 1, t, levels[t], [])))

        async def recv(self):
            return await self.q.get()

        async def close(self):
            pass
    sock = Sock()

    class Conn(BooksStore):
        async def fetchval(self, sql, *a):
            return False
    conn = Conn()

    class Acquire:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    class Pool:
        def acquire(self):
            return Acquire()

    async def get_pool():
        return Pool()
    want = [a]

    async def wanted(c, *, now):
        return list(want)

    async def limits(key_id, pk, *, now):
        return {"status": "OK", "as_of": now}

    async def heartbeat(service, status, detail, con=None):
        beats.append(detail)

    class Stop(Exception):
        pass
    real_sleep = asyncio.sleep
    passes = [0]

    async def sleep(s):
        passes[0] += 1
        n = passes[0]
        if n == 2:
            # A's row holds its first book; from now on no flush is due.
            # sid 1 gaps (a lost message) and the resubscription brings a
            # NEW book before the next pass's reassert
            seen["first_row"] = dict(conn.rows[a])
            monkeypatch.setattr(W, "FLUSH_EVERY_S", 1e9)
            levels[a] = [("0.45", "7")]
            sock.q.put_nowait(json.dumps(m_delta(1, 3, a, "0.40", "1",
                                                 "yes")))
        elif n == 3:
            seen["after_reassert"] = dict(conn.rows[a])
            seen["book_now"] = KWS.WsBooks.book_of(
                _BOOKS["b"], a)["orderbook_fp"]["yes_dollars"]
            want.clear()                    # the next refresh drops A
        elif n == 4:
            seen["after_drop"] = dict(conn.rows[a])
            raise Stop()
        await real_sleep(0.05)              # the runtime task runs meanwhile
    _BOOKS = {}

    class CapBooks(KWS.WsBooks):
        def __init__(self, *a_, **k):
            super().__init__(*a_, **k)
            _BOOKS["b"] = self

    async def connect():
        return sock
    monkeypatch.setattr(KWS, "WsBooks", CapBooks)
    monkeypatch.setattr(KWS, "websockets_connect", lambda k, p: connect)
    monkeypatch.setattr(W, "wanted_tickers", wanted)
    monkeypatch.setattr(W, "read_limits", limits)
    monkeypatch.setattr(W, "heartbeat", heartbeat)
    monkeypatch.setattr(W, "asyncio", type("A", (), {
        "create_task": staticmethod(asyncio.create_task),
        "CancelledError": asyncio.CancelledError,
        "sleep": staticmethod(sleep)}))
    for name in ("WANTED_EVERY_S", "FLUSH_EVERY_S", "REASSERT_EVERY_S",
                 "HEARTBEAT_EVERY_S"):
        monkeypatch.setattr(W, name, 0.0)
    with pytest.raises(Stop):
        asyncio.run(W.run(get_pool, env=env))
    assert len(beats) == 4
    assert all(beat["key"] == W.key_class(env) for beat in beats), \
        [beat["key"] for beat in beats]
    assert beats[1]["ws"]["current"] == 1
    # pass 3: A was CURRENT again with a new book its row did not hold
    assert seen["book_now"] == [["0.45", "7"]]
    assert seen["after_reassert"]["yes_bids"] == \
        seen["first_row"]["yes_bids"]
    assert seen["after_reassert"]["observed_at"] == \
        seen["first_row"]["observed_at"]
    # pass 4: the refresh that dropped A retired its row
    assert seen["after_drop"]["readable"] is False
    assert seen["after_drop"]["error"] == W.UNTRACKED


def test_an_ack_that_answers_none_of_our_commands_starts_no_sequence():
    """Which `subscribed` anchors a sequence, at the Subscriber: only one
    whose id names a command THIS session sent. An announcement with no
    id, an unknown id or an earlier session's id does not -- there a delta
    ahead of a seq-1 snapshot falls under assumption S5 (red-team scenario
    5 pins it) and the snapshot is served; after the ack of our own command
    the same messages gap the sid."""
    def run(ack_id, *, sessions=1):
        scripts = [[] for _ in range(sessions - 1)] + [[
            {"type": "subscribed", "id": ack_id,
             "msg": {"channel": "orderbook_delta", "sid": 1}},
            m_delta(1, 2, "K-A", "0.44", "1", "yes"),
            m_snapshot(1, 1, "K-A", [("0.52", "3")], [])]]
        out = []

        class Sock:
            def __init__(self, script):
                self.script = script

            async def send(self, s):
                pass

            async def recv(self):
                if not self.script:
                    raise ConnectionError("end")
                return json.dumps(self.script.pop(0))

            async def close(self):
                pass
        books = KWS.WsBooks(clock=_clock)
        real = books.on_message

        def watch(m, **k):
            out.append(real(m, **k))
            return out[-1]
        books.on_message = watch

        async def connect():
            return Sock(scripts.pop(0))
        sub = KWS.Subscriber(connect, books, wanted=lambda: ["K-A"],
                             clock=_clock)
        for _ in range(sessions):
            with pytest.raises(ConnectionError):
                asyncio.run(sub.session())
        return out
    s5 = ["SUBSCRIBED", "IGNORED_NOT_CURRENT", "SNAPSHOT"]
    assert run(None) == s5                      # no id
    assert run(99) == s5                        # an id we never sent
    assert run(1, sessions=2) == s5             # session 1's command id
    assert run(1) == ["SUBSCRIBED", "GAP", "IGNORED_DEAD_SID"]   # ours
    assert run(2, sessions=2) == ["SUBSCRIBED", "GAP", "IGNORED_DEAD_SID"]


def test_the_pending_map_evicts_its_oldest_command(monkeypatch):
    """MAX_PENDING_SUBSCRIBES bounds the commands awaiting an ack; past it
    the OLDEST is forgotten (its ack, if it ever comes, binds nothing),
    never one just sent."""
    monkeypatch.setattr(KWS, "MAX_PENDING_SUBSCRIBES", 2)

    class Sock:
        async def send(self, s):
            pass
    sub = KWS.Subscriber(None, KWS.WsBooks(clock=_clock), wanted=list)
    asyncio.run(sub._subscribe(Sock(), ["K-%03d" % i for i in range(300)]))
    assert sorted(sub.pending) == [2, 3]


DSN = os.environ.get("RN1X_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
def test_the_reader_path_on_postgres():
    """The worker's statements on the real kalshi_books_current, read by
    the REST worker's own readers: ws_current (a market there is not polled
    by REST) and db_freshness (counted current, by basis). The in-memory
    BooksStore the model checks against must agree with this."""
    import asyncpg
    import time
    from sportsassets.workers import kalshi_market_data as RESTW
    a, bb, c = "KXPROOF-EV1-A", "KXPROOF-EV1-B", "KXPROOF-EV2-C"
    old = "KXPROOF-EV0-OLD"

    async def go():
        conn = await asyncpg.connect(DSN)
        tr = conn.transaction()
        await tr.start()
        try:
            now = time.time()
            store, written = BooksStore(), {}
            b = _acked([a, bb])
            b.want([c])
            b.bind(2, [c])
            b.on_message(m_subscribed(2, 2))
            for m in (m_snapshot(1, 1, a, [("0.40", "10")], []),
                      m_snapshot(1, 2, bb, [("0.41", "10")], []),
                      m_snapshot(2, 1, c, [("0.42", "10")], [])):
                b.on_message(m)
            # a WS row an earlier process left readable
            await conn.execute(
                "INSERT INTO kalshi_books_current (ticker, event_ticker, "
                " series_ticker, book_basis, readable, observed_at) VALUES "
                " ($1, 'KXPROOF-EV0', 'KXPROOF', $2, true, to_timestamp($3))",
                old, KWS.BOOK_BASIS, now)
            await W.flush(conn, b, written, now=now)
            _drive(W.flush(store, b, dict(), now=now))
            ts = [a, bb, c, old]
            assert await RESTW.ws_current(conn, ts) == {a, bb, c, old}
            # the worker drops B; sid 2 gaps; the flush follows
            sub = KWS.Subscriber(None, b, wanted=lambda: [a, c])
            W.prune_untracked(sub, written, [a, c])
            await W.retire_untracked(conn, [a, c])
            assert b.on_message(m_delta(2, 3, c, "0.42", "1", "yes")) == "GAP"
            await W.flush(conn, b, written, now=now + 1)
            assert await RESTW.ws_current(conn, ts) == {a}
            fr = await RESTW.db_freshness(conn, ts, now=now + 1)
            assert fr["current_by_source"] == {"WS": 1, "REST": 0}
            rows = {r["ticker"]: r for r in await conn.fetch(
                "SELECT ticker, readable, error, yes_bids FROM "
                " kalshi_books_current WHERE ticker = ANY($1::text[])", ts)}
            assert rows[bb]["error"] == rows[old]["error"] == W.UNTRACKED
            assert rows[c]["error"] == "GAP:%s" % KWS.R_SEQ_GAP
            # the in-memory store wrote the same levels for A
            assert json.loads(rows[a]["yes_bids"]) == json.loads(
                store.rows[a]["yes_bids"])
            assert await W.reassert(conn, b, now=now + 2,
                                    written=written) == 1
        finally:
            await tr.rollback()
            await conn.close()
    asyncio.run(go())


# ── the Subscriber: a drop during a send; nothing wanted at the start ──

def test_a_market_dropped_while_its_subscribe_is_being_sent_is_held_nowhere():
    """(review of ca102147) `send` can suspend (write backpressure) and the
    worker's prune runs then. The Subscriber recorded `subscribed` AFTER
    the send, so a market dropped meanwhile was put back (and, wanted
    again, never subscribed again in the session), and the next chunk --
    listed before the drop -- still named a market dropped then. Every
    record is now made before the send, and each chunk leaves out what was
    dropped in the meantime."""
    many = ["KXT-EV-%03d" % i for i in range(150)]           # two chunks
    gone = (many[10], many[120])
    want = list(many)
    written: dict = {}
    holder = {}

    class Sock:
        def __init__(self):
            self.sent = []
            self.q = asyncio.Queue()

        async def send(self, s):
            self.sent.append(json.loads(s))
            if len(self.sent) == 1:          # chunk 1 is on its way
                want[:] = [t for t in many if t not in gone]
                W.prune_untracked(holder["sub"], written, want)
            await asyncio.sleep(0)

        async def recv(self):
            return await self.q.get()

        async def close(self):
            pass
    sock = Sock()

    async def connect():
        return sock

    async def go():
        books = KWS.WsBooks(clock=_clock)
        sub = holder["sub"] = KWS.Subscriber(
            connect, books, wanted=lambda: list(want), clock=_clock)
        task = asyncio.create_task(sub.session())
        for _ in range(20):
            await asyncio.sleep(0)
        cmds = [m["params"]["market_tickers"] for m in sock.sent]
        assert len(cmds) == 2 and gone[0] in cmds[0]
        assert gone[1] not in cmds[1]
        for t in gone:
            assert t not in sub.subscribed
            assert not any(t in ts for ts in sub.pending.values())
        # wanted again: subscribed again, by a command of its own
        want.append(gone[0])
        sock.q.put_nowait(json.dumps(m_subscribed(1, 1)))
        for _ in range(20):
            await asyncio.sleep(0)
        assert sock.sent[-1]["params"]["market_tickers"] == [gone[0]]
        assert books.ticker_sid.get(gone[0]) is None   # the old ack: no bind
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())


def test_a_session_that_starts_with_nothing_wanted_subscribes_when_it_fills():
    """(review of ca102147) The runtime starts before the worker's first
    wanted read lands (a slow or failing database at boot): the session
    subscribed nothing, so the venue sent nothing, and it waited in recv()
    for ever while the wanted set filled. A session with nothing to read
    now re-reads the wanted set every IDLE_RECHECK_S."""
    want: list = []

    class Sock:
        def __init__(self):
            self.sent = []
            self.q = asyncio.Queue()

        async def send(self, s):
            m = json.loads(s)
            self.sent.append(m)
            self.q.put_nowait(json.dumps(m_subscribed(m["id"], 1)))
            for i, t in enumerate(m["params"]["market_tickers"]):
                self.q.put_nowait(json.dumps(m_snapshot(
                    1, i + 1, t, [("0.40", "10")], [])))

        async def recv(self):
            return await self.q.get()

        async def close(self):
            pass
    sock = Sock()

    async def connect():
        return sock

    async def go():
        books = KWS.WsBooks(clock=_clock)
        sub = KWS.Subscriber(connect, books, wanted=lambda: list(want),
                             clock=_clock, idle_recheck_s=0.02)
        task = asyncio.create_task(sub.session())
        await asyncio.sleep(0.1)
        assert sock.sent == []
        want.append("K-A")
        await asyncio.sleep(0.2)
        assert [m["params"]["market_tickers"] for m in sock.sent] == \
            [["K-A"]]
        assert books.current("K-A")["ok"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
