"""KALSHI WS ACCEPTANCE MODEL (RC6.2: THE DOCUMENTED PROTOCOL) -- a
model-checked proof of the owner's acceptance properties against the REAL
WsBooks, Subscriber and worker:

    "a late acknowledgement cannot revive dropped subscriptions, and no book
     can become CURRENT without valid sequence continuity"

THE VENUE, AS DOCUMENTED (docs.kalshi.com websocket-connection, asyncapi,
orderbook-updates, changelog 2025-09-25 / 2026-06-18), in three variants:
  D  (documented) one subscription per channel: the first `subscribe` is
     answered by `subscribed` {id, sid}; a repeated subscribe MERGES into the
     same sid, answered by `ok` {id, sid, seq, msg.market_tickers} (tickers
     already held: no action, no snapshot); update_subscription add_markets /
     delete_markets / get_snapshot act on that sid and are answered by `ok`
     (seq), get_snapshot's snapshots following in the sid's sequence;
     `unsubscribe` of an unknown sid -> error 7; errors 10 / 25 end the
     subscription. ONE counter per sid: snapshots, deltas, `ok`,
     `unsubscribed` and scoped errors all take the sid's next seq.
  S  as D, but control frames (`ok`, `unsubscribed`, scoped errors) carry a
     SEPARATE per-sid counter (from 1; a lost control frame still advanced
     it); data frames run by themselves.
  R  as D, and a number whose subscription an error ended is REUSED: by the
     next acknowledged subscription (WsBooks level), by the venue for
     frames that answer none of our commands (the Subscriber level; the
     runtime ends its session at the error and reads none of them), and by
     the next connection.

THE REFERENCE ORACLE (`Oracle`) is written from that protocol, told the
variant, and imports nothing from kalshi_ws:
  * every frame of a subscription carries the subscription's sequence (D/R:
    data and control frames alike; S: data frames, control frames on their
    own counter); a frame whose seq is not the highest so far + 1 BREAKS it
    (a loss before it, or a late / replayed / duplicate frame);
  * `subscribed` answering our subscribe starts a subscription (ANCHORED:
    its sequence starts at the ack, the first frame is seq 1); on one
    ordered connection everything the number carried before belonged to
    something else;
  * a number never acknowledged as ours: where the sequence starts is
    unknown. Deltas before the first snapshot are not in it; that snapshot
    must follow the highest of them (+ 1) -- or, ASSUMPTION S5 (red-team
    scenario 5 pins it), carry seq 1; with nothing before it, a first
    snapshot above seq 1 starts the sequence only by ASSUMPTION S0 (it is
    taken as the subscription's state then; a first snapshot at seq 1 is
    the subscription's first frame by the protocol). Any break ends such a
    subscription for good (no command can repair it). S0 and S5 are named,
    counted, and test_s0_and_s5_are_the_only_assumptions... shows what they
    alone decide: CURRENT books on numbers never acknowledged as ours, and
    nothing else;
  * errors 10 / 25 and `unsubscribed` END a subscription; a disconnect ends
    every one.
It RECORDS every event -- connect / disconnect, want / drop, every frame
delivered, every command sent -- and derives, for every market t:
  * LEGITIMATE CURRENT (`legit`, per sid): t wanted (not dropped since its
    last want), connected; t's latest snapshot in its wanted epoch is on the
    subscription now holding sid s, was not a late / replayed / duplicate
    frame (its seq above every seq before it), and no frame of that
    subscription's sequence after it broke the sequence; the subscription
    has not ended. The book is that snapshot plus every later delta of t on
    s (Decimal, exact). A snapshot right after a loss is legitimate: it is
    the whole book at its seq.
  * PROMISED CURRENT (`promised`, D only; per-event liveness): on an
    anchored subscription, t's latest snapshot was the sequence's next
    frame, nothing after it broke the sequence, and no control frame came
    after it (so both readings of the counter agree on what followed).

PROPERTIES, checked after EVERY event (`check`):
  P1  a dropped market, until it is wanted again, is held nowhere: no book,
      not in the resubscribe / recover sets, not associated with any sid,
      not CURRENT, not in the Subscriber's subscribed set or any unanswered
      command, not in the worker's written keys, never counted by the
      heartbeat; no subscribe / add_markets / get_snapshot sent after the
      drop names it (only its delete_markets) -- also when the drop lands
      while a command is being sent.
  P1b NO REVIVAL, PER SUBSCRIPTION: a market is associated with sid s only
      if the subscription NOW holding the number s -- not an earlier one
      under the same number -- justifies it within the market's current
      wanted epoch: its ack answered a command naming the market, an
      add_markets / get_snapshot naming it was sent on it, or it delivered
      a frame of the market.
  P2  NO CURRENT WITHOUT CONTINUITY, BY SID: current(t)['ok'] only when the
      oracle's legitimate book ON THE SID THE BOOK IS HELD ON exists and
      equals the code's book (it used to be compared with any sid's).
  L   (D only) a book the oracle promises CURRENT is CURRENT with that book.
  I1  no book in state CURRENT on a sid whose sequence is untrusted; I2 no
      CURRENT book marked for a fresh snapshot (resubscribe / recover /
      awaiting).
  R   THE READER PATH (reader_check): the worker's own flush, reassert,
      wanted-set retirement and immediate GAP write (write_gaps, run by the
      Subscriber's gap sink before the next frame is read) on an in-memory
      kalshi_books_current (BooksStore executes their statements;
      test_the_reader_path_on_postgres runs them on Postgres with the REST
      worker's readers). R0 (always) every row a reader takes as a current
      WebSocket book belongs to a CURRENT book that has stayed CURRENT
      since the row was written -- the pre-gap row is never served, not
      even between a gap and the next flush (the second review's window);
      R1 right after a flush, every served row has exactly its CURRENT
      book's levels and every CURRENT book is served; R2 a row the reassert
      re-stamps holds its book as it is now; R3 no row of a market the
      worker does not track is ever served.
The Subscriber-level model venue also checks the ORACLE itself, and the
code's every CURRENT book, against venue truth: each equals the venue's true
book as of the HIGHEST data seq delivered on its subscription.

GENERATORS (deterministic):
  (a) WsBooks, EXHAUSTIVE per variant: every event sequence up to depth D
      from fourteen roots (the counts are pinned), over 2 sid numbers and 3
      markets: snapshot / delta at the next seq, past a lost frame, a
      duplicate and a replay of a delivered data frame; the reply to an
      update command (`ok` / error 27; next, or past a lost frame -- under S
      a lost reply still advanced the counter); errors 10 / 25 and the
      venue's `unsubscribed`; our ack (bind + `subscribed`, a fresh number,
      or under R an ended one); an announcement answering no command of
      ours; forget; want; disconnect / connect.
  (b) Subscriber: the real Subscriber.run, the worker's wanted-set step
      (prune_untracked + retire_untracked), flush, reassert and gap sink
      against the model venue (late command processing, chunks of two,
      losses / duplicates / replays of data frames, lost control frames,
      terminal errors, the venue's unsubscribe, disconnects; the worker drops
      and re-wants markets at arbitrary points, also while a command is being
      sent). Seeds per variant; odd seeds inject faults, half of those as a
      storm. Under D every seed must end with every wanted market CURRENT,
      equal to venue truth and served by the store, and the commands sent
      bounded by O(markets x connections + gaps + refreshes); under S and R
      safety is asserted and liveness measured.
  (c) every _ORDERINGS script, the 63 drop-anywhere cases and the other
      scripted Subscriber tests re-run with the oracle (D) checking every
      step, on top of their own assertions.

FOUND AND FIXED by this model (each has a directed test): an acknowledged
subscription ended by error 10 / 25 kept its markets associated with its
number, so a later subscription under the same number "held" them (P1b per
subscription; WsBooks._end now drops them); a dead number never
acknowledged as ours, re-announced, kept the old markets in ticker_sid
(_revive now drops them); a snapshot making a book CURRENT on one
acknowledged sid left it marked for a snapshot on another (I2; every mark
is cleared). And on the reader path: the immediate GAP write closes the
window between a gap and the next flush (R0).

MUTATION CHECK (offline, scratch script; each mutant of the client must be
caught): RC6.1's `ok` that never advances the sequence (P2, D), no
control-frame guard (P2, S from "acked" at depth 3), a gap that leaves its
books CURRENT (I2 / P2), no gap sink (R0 in the Subscriber sims), an ended
subscription keeping its associations (P1b), a replayed frame accepted (P2),
a late ack reviving a dropped market (P1) -- all caught. Applying frames on
a number first seen after our ack is NOT a safety violation (the RC6 rules
for numbers not ours are safe); the runtime ignores them by policy
(test_rc62_kalshi_ws_protocol pins it).

CI runs the default sizes (467,851 enumerated sequences; 100 Subscriber
seeds per variant). Larger runs (offline): KALSHI_PROOF_DEPTH (every root's
depth), KALSHI_PROOF_SEEDS / KALSHI_PROOF_SEED_BASE. SMALL LIVE = SHADOW:
read-only market data only; nothing here touches an order path.
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
D, S, R = "D", "S", "R"
VARIANTS = (D, S, R)
TERMINAL = (10, 25)


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


def m_ok(cid, sid, seq, tickers=None):
    m = {"type": "ok", "id": cid, "sid": sid, "seq": seq}
    if tickers is not None:
        m["msg"] = {"market_tickers": list(tickers)}
    return m


def m_error(cid, code, sid=None, seq=None):
    m = {"type": "error", "id": cid,
         "msg": {"code": code, "msg": "error %d" % code}}
    if sid is not None:
        m["sid"], m["seq"] = sid, seq
    return m


def m_unsubscribed(cid, sid, seq):
    return {"type": "unsubscribed", "id": cid, "sid": sid, "seq": seq}


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
    """One subscription as the client saw it, under one sid number."""
    __slots__ = ("acks", "anchored", "book", "cmds", "created", "ctop",
                 "ctrl_at", "ended", "first_snap", "last_break", "msgs",
                 "s0", "s5", "seen", "sid", "snap_at", "top")

    def __init__(self, sid, created, *, anchored=False):
        self.sid = sid
        self.created = created      # log index it began at
        self.anchored = anchored
        self.msgs = []              # (log index, seq, type, ticker)
        self.ended = False
        self.top = 0 if anchored else None   # highest seq of the sequence
        self.ctop = 0               # S: highest control seq
        self.last_break = -1        # position of the last break
        self.ctrl_at = -1           # position of the latest control frame
        self.first_snap = None
        self.s0 = None              # position assumption S0 started it at
        self.s5 = None              # position assumption S5 started it at
        self.acks = []              # command ids its ack(s) answered
        self.cmds = []              # (log index, action, tickers) sent on it
        self.snap_at = {}           # t -> (pos, log index, fresh, in_seq)
        self.book = {}              # t -> latest snapshot + later deltas
        self.seen = {}              # t -> log index of its latest frame

    def copy(self) -> _Sub:
        c = _Sub(self.sid, self.created, anchored=self.anchored)
        for k in ("ended", "top", "ctop", "last_break", "ctrl_at",
                  "first_snap", "s0", "s5"):
            setattr(c, k, getattr(self, k))
        c.msgs, c.acks, c.cmds = list(self.msgs), list(self.acks), \
            list(self.cmds)
        c.snap_at, c.seen = dict(self.snap_at), dict(self.seen)
        c.book = {t: {"yes": dict(bk["yes"]), "no": dict(bk["no"])}
                  for t, bk in self.book.items()}
        return c

    def view(self):
        return (self.sid, self.created, self.anchored, tuple(self.msgs),
                self.ended, self.top, self.ctop, self.last_break,
                self.ctrl_at, self.first_snap, self.s0, self.s5,
                tuple(self.acks), tuple(self.cmds),
                sorted(self.snap_at.items()),
                sorted((t, sorted(b["yes"].items()), sorted(b["no"].items()))
                       for t, b in self.book.items()))


class Oracle:
    """Records what happened; derives what a correct client may and must
    hold. `variant`: D, S or R (the venue's counter); `live_from`: when a
    (re)wanted market's liveness starts -- at the want ("want", WsBooks
    driven directly) or at the first command naming it ("subscribe")."""

    def __init__(self, *, variant=D, live_from="want", s5=True, s0=True):
        self.variant = variant
        self.live_from = live_from
        self.s5, self.s0 = s5, s0
        #: how many times S5 / S0 decided a verdict the protocol would not
        self.s5_used = self.s0_used = 0
        self.log = []
        self.connected = False
        self.wanted = set()
        self.epoch = {}
        self.dropped = set()
        self.dropped_this_session = set()
        self.subscribable = set()
        self._fresh_connection()

    def _fresh_connection(self):
        self.subs = {}
        self.all_subs = []
        self.cmds = {}              # command id -> (log index, kind, ...)
        self.sub_ids = set()        # our subscribe command ids
        self.acked_ids = set()      # ids a `subscribed` or `ok` answered
        self.first_sub_cmd = {}
        self.latest_snap = {}

    def copy(self) -> Oracle:
        c = Oracle.__new__(Oracle)
        c.variant, c.live_from, c.s5, c.s0 = (self.variant, self.live_from,
                                              self.s5, self.s0)
        c.s5_used, c.s0_used = self.s5_used, self.s0_used
        c.log = list(self.log)
        c.connected = self.connected
        c.wanted, c.epoch = set(self.wanted), dict(self.epoch)
        c.dropped = set(self.dropped)
        c.dropped_this_session = set(self.dropped_this_session)
        c.subscribable = set(self.subscribable)
        same = {id(s): s.copy() for s in self.all_subs}
        c.all_subs = [same[id(s)] for s in self.all_subs]
        c.subs = {sid: same[id(s)] for sid, s in self.subs.items()}
        c.cmds = dict(self.cmds)
        c.sub_ids, c.acked_ids = set(self.sub_ids), set(self.acked_ids)
        c.first_sub_cmd = dict(self.first_sub_cmd)
        c.latest_snap = {t: (i, same[id(s)])
                         for t, (i, s) in self.latest_snap.items()}
        return c

    def verdicts(self, markets) -> tuple:
        return (self.connected, self.s5_used, self.s0_used,
                sorted(self.cmds.items(), key=str), sorted(self.sub_ids),
                sorted(self.acked_ids), sorted(self.wanted),
                sorted(self.dropped), sorted(self.dropped_this_session),
                sorted(self.subscribable), [s.view() for s in self.all_subs],
                sorted((k, s.view()) for k, s in self.subs.items()),
                [(t, str(self.legit(t)), str(self.promised(t)),
                  [self.justified(t, sid) for sid in WSIDS])
                 for t in markets])

    def _sub(self, sid, i) -> _Sub:
        s = self.subs.get(sid)
        if s is None:
            s = self.subs[sid] = _Sub(sid, i)
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
            self.cmds[cid] = (i, "subscribe", tuple(tickers))
            self.sub_ids.add(cid)
            self._first_cmd(i, tickers)
        elif kind == "upd":
            _, cid, sid, action, tickers = entry
            self.cmds[cid] = (i, action, tuple(tickers))
            s = self.subs.get(sid)
            if s is not None and not s.ended:
                s.cmds.append((i, action, tuple(tickers)))
            if action != "delete_markets":
                self._first_cmd(i, tickers)
        elif kind == "msg":
            self._message(i, entry[1])
        return i

    def _first_cmd(self, i, tickers):
        for t in tickers:
            if t in self.wanted and t not in self.first_sub_cmd:
                self.first_sub_cmd[t] = i

    def _message(self, i, m):
        typ = m.get("type")
        if typ == "subscribed":
            sid = (m.get("msg") or {}).get("sid", m.get("sid"))
            if sid is None:
                return
            cid = m.get("id")
            ours = cid is not None and cid in self.sub_ids
            if ours:
                self.acked_ids.add(cid)
            s = self.subs.get(sid)
            # a number whose subscription is over: ended (error 10 / 25,
            # `unsubscribed`), or -- never acknowledged as ours -- broken
            # (nothing can repair it); ours replaces one that carried only
            # frames ahead of any snapshot
            if s is None or s.ended or (
                    not s.anchored and s.last_break >= 0) or (
                    ours and not s.anchored and s.first_snap is None):
                s = self.subs[sid] = _Sub(sid, i, anchored=ours)
                self.all_subs.append(s)
            s.acks.append(cid)
            return
        if typ in ("ok", "error", "unsubscribed"):
            cid = m.get("id")
            if typ == "ok" and cid in self.cmds:
                self.acked_ids.add(cid)
            sid = m.get("sid")
            if sid is None:
                return
            s = self.subs.get(sid)
            if s is None or s.ended:
                return
            code = (m.get("msg") or {}).get("code")
            if m.get("seq") is not None:
                self._control(i, s, m["seq"])
            if typ == "unsubscribed" or code in TERMINAL:
                s.ended = True
            return
        if typ not in (SNAP, DELTA):
            return
        body = m.get("msg") or {}
        sid, seq, t = m.get("sid"), m.get("seq"), body.get("market_ticker")
        if sid is None or seq is None or t is None:
            return
        s = self._sub(sid, i)
        if s.ended:
            return                  # its subscription is over
        pos = len(s.msgs)
        top = s.top
        fresh = True
        if s.anchored:
            broke = seq != top + 1
            fresh = seq > top
        else:
            broke = top is not None and seq != top + 1
            if s.first_snap is None and typ != SNAP:
                broke = False       # ahead of the first snapshot: not in it
            elif s.first_snap is None and typ == SNAP:
                if top is None and seq > 1:
                    # nothing before it: where its sequence starts is
                    # unknown -- ASSUMPTION S0 takes it as the state then
                    if self.s0:
                        s.s0 = pos
                        self.s0_used += 1
                    else:
                        broke = fresh = False
                elif broke and seq == 1 and self.s5:
                    broke = False   # ASSUMPTION S5 (red-team scenario 5)
                    s.s5, top = pos, None
                    self.s5_used += 1
                elif broke:
                    fresh = False
        if typ == SNAP and s.first_snap is None:
            s.first_snap = pos
        if broke:
            s.last_break = pos
        s.top = seq if top is None else max(top, seq)
        s.msgs.append((i, seq, typ, t))
        s.seen[t] = i
        if typ == SNAP:
            s.snap_at[t] = (pos, i, fresh, not broke)
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

    def _control(self, i, s, seq):
        """A control frame of subscription s: in its sequence under D / R,
        on its own counter under S (where its breaks touch no data)."""
        pos = len(s.msgs)
        s.msgs.append((i, seq, "ctrl", None))
        s.ctrl_at = pos
        if self.variant == S:
            s.ctop = max(s.ctop, seq)
            return
        top = s.top if s.top is not None else 0
        if seq != top + 1:
            s.last_break = pos
        s.top = max(top, seq)

    # ── derived verdicts ──
    def data_seqs(self, sid):
        s = self.subs.get(sid)
        return [] if s is None else [q for _i, q, ty, _t in s.msgs
                                     if ty in (SNAP, DELTA)]

    def legit(self, t) -> dict:
        """{sid: book}: every sid on which a CURRENT book of t is legitimate
        now (empty: no CURRENT book of t is)."""
        out = {}
        if not self.connected or t not in self.wanted:
            return out
        e = self.epoch[t]
        for sid, s in self.subs.items():
            if s.ended:
                continue
            at = s.snap_at.get(t)
            if at is None or at[1] < e:
                continue
            pos, _i, fresh, _in_seq = at
            if not fresh or s.last_break > pos:
                continue
            if not s.anchored and s.last_break >= 0:
                continue        # no command can repair such a subscription
            out[sid] = s.book[t]
        return out

    def relies_on(self, t, sid) -> set:
        """The assumptions (S0, S5) a CURRENT book of t on sid rests on."""
        s = self.subs.get(sid)
        out = set()
        if s is None or t not in s.snap_at:
            return out
        pos = s.snap_at[t][0]
        if s.s0 is not None and pos >= s.s0:
            out.add("S0")
        if s.s5 is not None and pos >= s.s5:
            out.add("S5")
        return out

    def promised(self, t):
        """(D) the book a correct client must hold CURRENT now, or None."""
        if self.variant != D or not self.connected or t not in self.wanted:
            return None
        start = (self.epoch[t] if self.live_from == "want"
                 else self.first_sub_cmd.get(t))
        ls = self.latest_snap.get(t)
        if start is None or ls is None or ls[0] < start:
            return None
        s = ls[1]
        if self.subs.get(s.sid) is not s or s.ended or not s.anchored:
            return None
        pos, _i, fresh, in_seq = s.snap_at[t]
        if not in_seq or s.last_break > pos or s.ctrl_at > pos:
            return None
        return s.book[t]

    def justified(self, t, sid) -> bool:
        """May the client associate t with `sid` (P1b)? Only by the
        subscription NOW holding the number."""
        if t not in self.wanted:
            return False
        s = self.subs.get(sid)
        if s is None:
            return False
        e = self.epoch[t]
        if s.seen.get(t, -1) >= e:
            return True
        for cid in s.acks:
            c = self.cmds.get(cid)
            if c is not None and c[0] >= e and t in c[2]:
                return True
        return any(i >= e and i >= s.created and t in ts
                   and action != "delete_markets"
                   for i, action, ts in s.cmds)


# ── the properties ───────────────────────────────────────────────────────

def _code_book(cur) -> dict:
    fp = cur["book"]["orderbook_fp"]
    return {"yes": {_dec(p): _dec(q) for p, q in fp["yes_dollars"]},
            "no": {_dec(p): _dec(q) for p, q in fp["no_dollars"]}}


def check(o: Oracle, b: KWS.WsBooks, *, sub=None, written=None,
          universe=()) -> list:
    """P1, P1b, P2, L, I1, I2 after one event; the list of violations."""
    bad = []
    assoc: dict = {}
    for sid, ms in b.sid_markets.items():
        for t in ms:
            assoc.setdefault(t, set()).add(sid)
    for t, sid in b.ticker_sid.items():
        assoc.setdefault(t, set()).add(sid)
    markets = sorted(set(universe) | set(b.books) | o.wanted | o.dropped)
    cur = {t: b.current(t) for t in markets}
    want_snap = set().union(*b.recover.values()) if b.recover else set()
    awaiting = set().union(*(a["awaiting"] for a in b.anchors.values())) \
        if b.anchors else set()
    for t in sorted(o.dropped):
        held = [name for name, hit in (
            ("book", t in b.books), ("resubscribe", t in b.resubscribe),
            ("recover", t in want_snap or t in awaiting),
            ("sid_markets/ticker_sid", t in assoc),
            ("current", cur[t]["ok"]),
            ("subscribed", sub is not None and t in sub.subscribed),
            ("pending", sub is not None and any(
                t in ts for ts in sub.pending.values())),
            ("queued", sub is not None and (
                t in sub.queued_add or t in sub.readd or t in sub.rewant)),
            ("written", written is not None and t in written)) if hit]
        if held:
            bad.append(("P1_DROPPED_MARKET_HELD", t, held))
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
    for t in sorted(assoc):
        for sid in sorted(assoc[t], key=str):
            if not o.justified(t, sid):
                bad.append(("P1B_ASSOCIATION_NOT_JUSTIFIED", t, sid))
    for t, bk in b.books.items():
        if bk["state"] == KWS.CURRENT and (
                bk["sid"] is None or bk["sid"] in b.dead_sids
                or b.sid_seq.get(bk["sid"]) is None):
            bad.append(("I1_CURRENT_STATE_ON_AN_UNTRUSTED_SID", t, bk["sid"]))
        if bk["state"] == KWS.CURRENT and (
                t in b.resubscribe or t in want_snap or t in awaiting):
            bad.append(("I2_CURRENT_BOOK_MARKED_FOR_A_SNAPSHOT", t))
    if sub is not None:
        acked = [k for k in sub.pending if k in o.acked_ids]
        if acked:
            bad.append(("PENDING_HOLDS_AN_ANSWERED_COMMAND", acked))
    for t in markets:
        c = cur[t]
        mine = _code_book(c) if c["ok"] else None
        if mine is not None:
            held_on = b.books[t]["sid"]
            legit = o.legit(t)
            if held_on not in legit or legit[held_on] != mine:
                bad.append(("P2_CURRENT_WITHOUT_CONTINUITY", t, held_on,
                            sorted(legit, key=str)))
        promised = o.promised(t)
        if promised is not None and mine != promised:
            bad.append(("L_NOT_CURRENT_WHERE_PROMISED", t, c["state"]))
    return bad


_CONTAINERS = (dict, list, set)


def _clone(x):
    """A deep copy of WsBooks state (dicts / lists / sets, nested)."""
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


# ── (a) WsBooks: exhaustive bounded enumeration, per variant ────────────

WA, WB, WC = "KX-A", "KX-B", "KX-C"
WMARKETS = (WA, WB, WC)
WSIDS = (1, 2)
DATA_VARIANTS = ("next", "skip", "dup", "replay")


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


def _top(o, sid, *, control=False):
    s = o.subs.get(sid)
    if s is None or s.ended:
        return None if not control else 0
    if control and o.variant == S:
        return s.ctop
    if o.variant == S:
        q = o.data_seqs(sid)
        return max(q) if q else (0 if s.anchored else None)
    return s.top


def _w_seq(o, sid, variant):
    """The seq a data frame of `variant` carries on sid's subscription now
    (None: not defined there). dup / replay repeat a DELIVERED DATA frame
    (a venue replays what it sent; a control frame's slot is never data)."""
    top = _top(o, sid)
    q = o.data_seqs(sid)
    if variant == "next":
        return 1 if top is None else top + 1
    if variant == "skip":
        return 2 if top is None else top + 2
    if variant == "dup":
        return q[-1] if q else None
    prior = sorted(set(x for x in q if x < q[-1])) if q else []
    return prior[-1] if prior else None


def _live_anchor(o, sid):
    s = o.subs.get(sid)
    return s is not None and s.anchored and not s.ended


def _may_ack(o, sid):
    """D / S: a fresh number only; R: also one whose subscription ended."""
    s = o.subs.get(sid)
    if s is None:
        return not any(x.sid == sid for x in o.all_subs)
    return o.variant == R and s.ended


def w_events(o: Oracle) -> list:
    """The alphabet in state `o` (only the events defined there)."""
    ev = []
    if o.connected:
        for sid in WSIDS:
            for t in WMARKETS:
                if t not in o.subscribable:
                    continue
                for typ in (SNAP, DELTA):
                    for v in DATA_VARIANTS:
                        if _w_seq(o, sid, v) is not None:
                            ev.append(("msg", sid, t, typ, v))
            if _live_anchor(o, sid):
                ev += [("reply", sid, "ok", "next"),
                       ("reply", sid, "ok", "skip"),
                       ("reply", sid, "err27", "next"),
                       ("end", sid, "err10"), ("end", sid, "unsubscribed")]
            if _may_ack(o, sid):
                ev.append(("ack", sid))
            ev.append(("announce", sid))
        ev.append(("disconnect",))
    else:
        ev.append(("connect",))
    for t in WMARKETS:
        ev.append(("forget", t) if t in o.wanted else ("want", t))
    return ev


def _new_cid(o):
    return len(o.log) + 1000


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
        # the Subscriber's handling of `subscribed` answering its subscribe:
        # bind (ours), then the message; it named every market subscribable
        cid = _new_cid(o)
        ts = sorted(o.subscribable)
        m = m_subscribed(cid, ev[1])
        b.bind(ev[1], ts)
        b.on_message(m, recv_at=NOW)
        return [("sub", cid, tuple(ts)), ("msg", m)]
    if kind == "announce":
        m = m_subscribed(None, ev[1])
        b.bind(ev[1], [], ours=False)
        b.on_message(m, recv_at=NOW)
        return [("msg", m)]
    if kind == "reply":
        # an update command sent on the sid (the Subscriber's record), and
        # its reply: `ok` / error 27 -- under S a lost reply still advanced
        # the counter, so "skip" stands for two commands, one reply lost
        _, sid, what, v = ev
        out = []
        cids = [_new_cid(o) + k for k in range(2 if v == "skip" else 1)]
        for cid in cids:
            b.command_sent(sid, cid)
            out.append(("upd", cid, sid, "get_snapshot", ()))
        top = _top(o, sid, control=True)
        seq = top + (2 if v == "skip" else 1)
        m = (m_ok(cids[-1], sid, seq) if what == "ok"
             else m_error(cids[-1], 27, sid=sid, seq=seq))
        b.on_message(m, recv_at=NOW)
        return out + [("msg", m)]
    if kind == "end":
        _, sid, what = ev
        seq = _top(o, sid, control=True) + 1
        m = (m_error(0, 10, sid=sid, seq=seq) if what == "err10"
             else m_unsubscribed(0, sid, seq))
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


def _derive(log, variant) -> Oracle:
    o = Oracle(variant=variant)
    for e in log:
        o.record(e)
    return o


_ALL_ON_1 = [("ack", 1), ("msg", 1, WA, SNAP, "next"),
             ("msg", 1, WB, SNAP, "next"), ("msg", 1, WC, SNAP, "next")]
_GAP_1 = _ALL_ON_1 + [("msg", 1, WA, DELTA, "skip")]
#: where each enumeration starts (after: want A B C, connect), with the
#: depth CI enumerates from it per variant (KALSHI_PROOF_DEPTH overrides)
ROOTS = {
    "fresh": [],
    "acked": [("ack", 1)],
    "all_current_on_sid_1": _ALL_ON_1,
    # a lost frame: every book GAP, the sid re-based (recovery requested)
    "gapped_on_sid_1": _GAP_1,
    # the get_snapshot's reply and A's fresh snapshot: B, C still awaited
    "recovering_on_sid_1": _GAP_1 + [("reply", 1, "ok", "next"),
                                     ("msg", 1, WA, SNAP, "next")],
    "b_dropped_on_live_sid_1": _ALL_ON_1 + [("forget", WB)],
    "b_dropped_and_wanted_again": _ALL_ON_1 + [("forget", WB),
                                               ("want", WB)],
    "a_reply_after_data": _ALL_ON_1 + [("msg", 1, WA, DELTA, "next"),
                                       ("reply", 1, "ok", "next")],
    "sid_1_ended_by_error": [("ack", 1), ("msg", 1, WA, SNAP, "next"),
                             ("end", 1, "err10")],
    "frames_on_a_number_not_ours": [("ack", 1), ("msg", 1, WA, SNAP, "next"),
                                    ("msg", 2, WB, SNAP, "next")],
    "delta_before_the_first_snapshot": [("msg", 1, WA, DELTA, "skip")],
    "announced_not_ours_then_a_delta": [("announce", 1),
                                        ("msg", 1, WA, DELTA, "skip")],
    "first_snapshot_refused_after_a_delta": [
        ("msg", 1, WA, DELTA, "skip"), ("msg", 1, WB, SNAP, "dup")],
    "reconnected_after_all_current": _ALL_ON_1 + [
        ("msg", 1, WB, DELTA, "next"), ("disconnect",), ("connect",)],
}
#: CI depth per (root, variant): 3 where each variant's hard cases live --
#: under D the two densest roots; under S the roots where a separate
#: counter's reply can coincide with the shared slot (a mutant without the
#: control-frame guard is caught from "acked" at depth 3: snapshot, a reply
#: past a lost one, a delta past a lost frame); under R the reused number --
#: 2 everywhere else
DEEP = {("fresh", D), ("all_current_on_sid_1", D), ("acked", S),
        ("a_reply_after_data", S), ("sid_1_ended_by_error", R)}


def ci_depth(root, variant):
    return 3 if (root, variant) in DEEP else 2


#: sequences enumerated per (variant, root) at its CI depth -- pinned: the
#: alphabet is the oracle's, so the count does not depend on the code
CI_COUNTS = {
    (D, 'a_reply_after_data'): 2361,
    (D, 'acked'): 1425,
    (D, 'all_current_on_sid_1'): 116414,
    (D, 'announced_not_ours_then_a_delta'): 1508,
    (D, 'b_dropped_and_wanted_again'): 2361,
    (D, 'b_dropped_on_live_sid_1'): 2361,
    (D, 'delta_before_the_first_snapshot'): 1508,
    (D, 'first_snapshot_refused_after_a_delta'): 1502,
    (D, 'frames_on_a_number_not_ours'): 2347,
    (D, 'fresh'): 44784,
    (D, 'gapped_on_sid_1'): 2361,
    (D, 'reconnected_after_all_current'): 1154,
    (D, 'recovering_on_sid_1'): 2361,
    (D, 'sid_1_ended_by_error'): 1430,
    (R, 'a_reply_after_data'): 2363,
    (R, 'acked'): 1427,
    (R, 'all_current_on_sid_1'): 2363,
    (R, 'announced_not_ours_then_a_delta'): 1508,
    (R, 'b_dropped_and_wanted_again'): 2363,
    (R, 'b_dropped_on_live_sid_1'): 2363,
    (R, 'delta_before_the_first_snapshot'): 1508,
    (R, 'first_snapshot_refused_after_a_delta'): 1502,
    (R, 'frames_on_a_number_not_ours'): 2349,
    (R, 'fresh'): 1154,
    (R, 'gapped_on_sid_1'): 2363,
    (R, 'reconnected_after_all_current'): 1154,
    (R, 'recovering_on_sid_1'): 2363,
    (R, 'sid_1_ended_by_error'): 60406,
    (S, 'a_reply_after_data'): 116630,
    (S, 'acked'): 59750,
    (S, 'all_current_on_sid_1'): 2361,
    (S, 'announced_not_ours_then_a_delta'): 1508,
    (S, 'b_dropped_and_wanted_again'): 2361,
    (S, 'b_dropped_on_live_sid_1'): 2361,
    (S, 'delta_before_the_first_snapshot'): 1508,
    (S, 'first_snapshot_refused_after_a_delta'): 1502,
    (S, 'frames_on_a_number_not_ours'): 2347,
    (S, 'fresh'): 1154,
    (S, 'gapped_on_sid_1'): 2361,
    (S, 'reconnected_after_all_current'): 1154,
    (S, 'recovering_on_sid_1'): 2361,
    (S, 'sid_1_ended_by_error'): 1430,
}                       # 467,851 sequences in all


def root_state(root, variant, *, s5=True, s0=True):
    b = KWS.WsBooks(clock=_clock)
    o = Oracle(variant=variant, s5=s5, s0=s0)
    for t in WMARKETS:
        b.want([t])
        o.record(("want", t))
    b.on_connected()
    o.record(("connect",))
    for ev in ROOTS[root]:
        for e in w_apply(b, o, ev):
            o.record(e)
    bad = check(o, b, universe=WMARKETS)
    assert bad == [], ("root", root, variant, bad)
    return b, o


class _Found(Exception):
    pass


def enumerate_root(root, variant, depth, *, fail_fast=True, on_node=None,
                   s5=True, s0=True):
    """DFS over every sequence up to `depth` from `root`; the number of
    sequences checked, and the violations (empty when the proof holds).
    Each sequence is checked after EVERY event (its prefixes are nodes)."""
    b, o = root_state(root, variant, s5=s5, s0=s0)
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


def _env_int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def test_the_oracle_copy_is_the_oracle():
    """The enumeration branches on Oracle.copy(); at every node (depth 2
    from three roots, depth 1 from the rest, every variant) the copied-and-
    extended oracle derives exactly what the oracle re-derived from its
    whole record does."""
    nodes = [0]
    for variant in VARIANTS:
        def same(o2, variant=variant):
            nodes[0] += 1
            assert o2.verdicts(WMARKETS) == \
                _derive(o2.log, variant).verdicts(WMARKETS)
        deep = ("fresh", "gapped_on_sid_1", "delta_before_the_first_snapshot")
        for root in sorted(ROOTS):
            _n, found = enumerate_root(root, variant,
                                       2 if root in deep else 1,
                                       on_node=same)
            assert found == []
    assert nodes[0] > 5000


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("root", sorted(ROOTS))
def test_wsbooks_every_sequence_up_to_depth_d_holds_the_properties(
        root, variant):
    """(a) every event sequence up to the root's depth, every property after
    every event, under each venue variant; the number of sequences is
    pinned."""
    depth = _env_int("KALSHI_PROOF_DEPTH", ci_depth(root, variant))
    n, found = enumerate_root(root, variant, depth)
    assert found == [], found[0]
    print("KALSHI_PROOF enumeration variant=%s root=%s depth=%d sequences=%d"
          % (variant, root, depth, n))
    if depth == ci_depth(root, variant):
        assert n == CI_COUNTS[(variant, root)], (variant, root, n)


def test_s0_and_s5_are_the_only_assumptions_beyond_the_protocol():
    """P2 FROM THE PROTOCOL ALONE. The same enumeration (depth 2, every
    root, every variant) under the oracle WITHOUT assumptions S0 and S5.
    Every violation it finds is a CURRENT book that only S0 or S5 admits,
    on a number NEVER acknowledged as ours -- the behaviour red-team
    scenario 5 pins. None is on a subscription the venue acknowledged as
    ours (every subscription the runtime opens): there P2 holds with no
    assumption. The counts are pinned, so the residual is measured."""
    totals = {"sequences": 0, "S0": 0, "S5": 0}
    for variant in VARIANTS:
        for root in sorted(ROOTS):
            _n, found = enumerate_root(root, variant, 2, fail_fast=False,
                                       s5=False, s0=False)
            totals["sequences"] += len(found)
            for _r, path, v in found:
                b, o = root_state(root, variant)        # S0, S5 on
                for ev in path:
                    for e in w_apply(b, o, ev):
                        o.record(e)
                used = set()
                for x in v:
                    assert x[0] == "P2_CURRENT_WITHOUT_CONTINUITY", (
                        variant, root, path, x)
                    t, sid = x[1], x[2]
                    assert not o.subs[sid].anchored, (variant, root, path, x)
                    used |= o.relies_on(t, sid)
                assert len(used) == 1, (variant, root, path, v, used)
                totals[used.pop()] += 1
    print("KALSHI_PROOF assumption-only sequences (depth 2): %s" % totals)
    assert totals == ASSUMPTION_ONLY, totals


#: sequences (depth 2, all roots and variants) ending in a CURRENT book the
#: protocol alone does not vouch for, by the one assumption admitting it
ASSUMPTION_ONLY = {"sequences": 1323, "S0": 1134, "S5": 189}


# ── the reader path: kalshi_books_current as the worker writes it ─────────

_LEVELS = ("yes_bids", "no_bids", "yes_asks", "no_asks")


class BooksStore:
    """kalshi_books_current, executing the WebSocket worker's OWN statements
    (write_book, reassert, retire_untracked, write_gaps) with their SQL
    semantics; any other statement fails the proof. Readers take a row as a
    current WebSocket book when it is readable, on the WebSocket basis and
    observed within BOOK_SLA_S (`served`). `epoch_of` (optional): the
    book's CURRENT-epoch when its row is written (R0)."""

    def __init__(self, epoch_of=None):
        self.rows = {}
        self.restamped = []
        self.epoch_of = epoch_of

    async def execute(self, sql, *a):
        if sql.startswith("INSERT INTO kalshi_books_current"):
            self.rows[a[0]] = dict(zip(_LEVELS, a[3:7]), basis=a[7],
                                   readable=True, error=None,
                                   observed_at=float(a[8]),
                                   epoch=(self.epoch_of(a[0])
                                          if self.epoch_of else None))
        elif sql == W.GAP_ROWS_SQL:
            ts, err, basis = a
            for t in ts:
                r = self.rows.get(t)
                if r is not None and r["basis"] == basis and r["readable"]:
                    r.update(readable=False, error=err)
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
    b = KMD.book_from_orderbook(books.current(t)["book"], observed_at=now)
    return {k: W._lv(b.get(k)) for k in _LEVELS}


def reader_check(store, books, want, *, now, synced, restamped=(),
                 epochs=None) -> list:
    """R (the reader path), the list of violations (module docstring)."""
    bad = []
    served = store.served(now)
    for t, r in sorted(served.items()):
        if t not in want:
            bad.append(("R3_SERVED_UNTRACKED", t))
            continue
        if epochs is not None:
            cur = books.current(t)
            if not cur["ok"]:
                bad.append(("R0_SERVED_A_BOOK_NOT_CURRENT", t, cur["state"]))
            elif r.get("epoch") != epochs.get(t, 0):
                bad.append(("R0_SERVED_A_PRE_GAP_ROW", t))
        if synced or t in restamped:
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
    venue gets to them (late replies), frames leave in order through
    `out_q` as (frame, vinc | None, kind)."""

    def __init__(self, variant):
        self.variant = variant
        self.in_q = deque()
        self.out_q = deque()
        self.sub = None             # {"sid", "markets", "seq", "cseq", "vinc"}
        self.next_sid = 1
        self.ended_numbers = []
        self.sent = {}              # vinc -> [data frames it sent]
        self.lost = {}              # vinc -> [data frames lost]
        self.last_seq = {}          # vinc -> last data seq delivered
        self.max_seq = {}           # vinc -> highest data seq delivered


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
    """The production Subscriber.run (and the worker's prune, flush,
    reassert and gap sink) against a model venue of `variant`, checked by
    the oracle after every event."""

    def __init__(self, seed, *, variant=D, faults, chaos_steps=400,
                 max_disconnects=3):
        self.seed, self.variant, self.faults = seed, variant, faults
        self.rng = r = random.Random("%s-%d" % (variant, seed))
        self.chaos_steps = chaos_steps
        self.max_disconnects = max_disconnects
        self.w = {"deliver": r.randint(25, 60), "process": r.randint(2, 14),
                  "activity": r.randint(8, 25), "drop": r.randint(1, 6),
                  "rewant": r.randint(1, 6), "flush": 3, "reassert": 2,
                  "venue_end": r.randint(0, 2), "disconnect": r.randint(0, 2)}
        storm = faults and seed % 4 == 3
        self.p_loss, self.p_dup, self.p_replay = (
            (0.0, 0.0, 0.0) if not faults else
            (r.uniform(0.15, 0.3), r.uniform(0.03, 0.08),
             r.uniform(0.1, 0.2)) if storm else
            (r.uniform(0.01, 0.06), r.uniform(0.01, 0.05),
             r.uniform(0.01, 0.05)))
        self.p_ctrl_loss = r.uniform(0.05, 0.2) if faults else 0.0
        self.p_reuse = r.uniform(0.3, 0.95)
        self.p_send_refresh = r.uniform(0.05, 0.3)
        self.truth = {t: {"yes": {Decimal("0.4%d" % k): Decimal(10 + k)},
                          "no": {Decimal("0.5%d" % k): Decimal(20 + k)}}
                      for k, t in enumerate(SMARKETS)}
        self.want = sorted(r.sample(SMARKETS, r.randint(2, len(SMARKETS))))
        self.books = KWS.WsBooks(clock=_clock)
        self.written = {}
        self.epochs = {}            # market -> times its book left CURRENT
        self.prev_state = {}
        self.store = BooksStore(epoch_of=lambda t: self.epochs.get(t, 0))
        self.sub = KWS.Subscriber(self._connect, self.books,
                                  wanted=lambda: list(self.want),
                                  clock=_clock, backoff=(0,),
                                  gap_sink=self._gap_sink)
        self.oracle = Oracle(variant=variant, live_from="subscribe")
        for t in self.want:
            self.oracle.record(("want", t))
        self.violations = []
        self.steps = 0
        self.connections = 0
        self.vinc_n = 0
        self.truth_at = {}
        self.vinc_of = {}
        self.max_seq_all = {}       # vinc -> highest data seq delivered
        self.done = False
        self.stop = None
        self.conn = None
        self.calm_rounds = 3
        self.calm_budget = None
        self.n_commands = 0
        self.counts = {"delivered": 0, "lost": 0, "duplicated": 0,
                       "replayed": 0, "ctrl_lost": 0, "acks": 0,
                       "late_acks": 0, "reused_sids": 0, "drops": 0,
                       "rewants": 0, "venue_ends": 0, "venue_unsubscribes":
                       0, "disconnects": 0, "checks": 0,
                       "refresh_during_send": 0, "flushes": 0,
                       "rows_written": 0, "reasserts": 0, "restamped": 0,
                       "reader_checks": 0, "gap_writes": 0,
                       "client_session_ends": 0}

    # ── the run ──
    def run(self):
        async def main():
            self.stop = asyncio.Event()
            await asyncio.wait_for(self.sub.run(stop=self.stop), timeout=120)
        real = KWS.chunks
        KWS.chunks = lambda xs, n=2: real(xs, n)
        try:
            asyncio.run(main())
        finally:
            KWS.chunks = real
        self.counts["client_session_ends"] = sum(
            self.sub.session_ends.values())
        return self

    def _violate(self, where, bad):
        self.violations.append((self.variant, self.seed, self.steps, where,
                                bad))
        if self.stop is not None:
            self.stop.set()

    def _track_epochs(self):
        """A book that was CURRENT at the last check and is not now left
        CURRENT in between: its rows written before are pre-gap rows."""
        for t in set(self.prev_state) | set(self.books.books):
            was = self.prev_state.get(t)
            now = self.books.current(t)["ok"] if t in self.books.books \
                else False
            if was and not now:
                self.epochs[t] = self.epochs.get(t, 0) + 1
        self.prev_state = {t: self.books.current(t)["ok"]
                           for t in self.books.books}

    def _check(self, where):
        self.counts["checks"] += 1
        self._track_epochs()
        bad = check(self.oracle, self.books, sub=self.sub,
                    written=self.written, universe=SMARKETS)
        bad += self._against_venue_truth()
        bad += reader_check(self.store, self.books, self.want, now=NOW,
                            synced=False, epochs=self.epochs)
        if bad:
            self._violate(where, bad)

    async def _gap_sink(self, tickers):
        self.counts["gap_writes"] += 1
        await W.write_gaps(self.store, self.books, self.written, tickers)

    def _truth(self, t, sid):
        s = self.oracle.subs.get(sid)
        if s is None or t not in s.snap_at:
            return "NO_SNAPSHOT_OF_IT_ON_ITS_SID"
        pos, i = s.snap_at[t][:2]
        vinc = self.vinc_of.get(i)
        if any(self.vinc_of.get(j) != vinc for j, _q, ty, _t in s.msgs[pos:]
               if ty != "ctrl"):
            return "ORACLE_SUBSCRIPTION_MISMATCH"
        snap_seq = s.msgs[pos][1]
        for q in range(self.conn_max_seq(vinc), snap_seq - 1, -1):
            rec = self.truth_at.get((vinc, q))
            if rec is not None and rec[0] == t:
                return rec[1]
        return "NO_TRUTH"

    def conn_max_seq(self, vinc):
        return self.max_seq_all.get(vinc, 0)

    def _against_venue_truth(self):
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
        self.counts["flushes"] += 1
        self.counts["rows_written"] += _drive(W.flush(
            self.store, self.books, self.written, now=NOW))
        self.counts["reader_checks"] += 1
        bad = reader_check(self.store, self.books, self.want, now=NOW,
                           synced=True, epochs=self.epochs)
        if bad:
            self._violate("after the flush", bad)

    def _reassert(self):
        self.counts["reasserts"] += 1
        self.store.restamped = []
        _drive(W.reassert(self.store, self.books, now=NOW,
                          written=self.written))
        self.counts["restamped"] += len(self.store.restamped)
        self.counts["reader_checks"] += 1
        bad = reader_check(self.store, self.books, self.want, now=NOW,
                           synced=False, restamped=self.store.restamped,
                           epochs=self.epochs)
        if bad:
            self._violate("after the reassert", bad)

    # ── the socket ──
    async def _connect(self):
        if self.connections:
            for _ in range(self.rng.randint(0, 2)):
                self._worker_refresh(self.rng.choice(("drop", "rewant")))
        self.connections += 1
        self.conn = _VenueConn(self.variant)
        self.oracle.record(("connect",))
        return _Socket(self)

    def _on_send(self, m):
        self.n_commands += 1
        p = m.get("params") or {}
        ts = list(p.get("market_tickers") or ())
        action = p.get("action")
        if m.get("cmd") == "subscribe" or action in ("add_markets",
                                                     "get_snapshot"):
            hit = set(ts) & self.oracle.dropped
            if hit:
                self._violate("send", [("P1_COMMAND_NAMES_DROPPED",
                                        sorted(hit), m)])
        if m.get("cmd") == "subscribe":
            self.oracle.record(("sub", m["id"], tuple(ts)))
        elif m.get("cmd") == "update_subscription":
            self.oracle.record(("upd", m["id"], p.get("sid"), action,
                                tuple(ts)))
        else:
            self._violate("send", [("A_COMMAND_OUTSIDE_THE_SET", m)])
        self.conn.in_q.append(m)

    def _on_close(self):
        self.oracle.record(("disconnect",))
        self._check("after the disconnect")

    async def _recv(self):
        self._check("after the frame")
        while True:
            if self.violations or self.done:
                raise ConnectionError("end")
            self.steps += 1
            calm = self.steps > self.chaos_steps
            if calm and self.calm_budget is None:
                self.calm_budget = 4000 + 4 * (len(self.conn.out_q)
                                               + len(SMARKETS) * 4
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
            elif act == "venue_end":
                self._venue_end()
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
        w["venue_end"] *= c.sub is not None
        w["disconnect"] *= self.counts["disconnects"] < self.max_disconnects
        acts = sorted(w)
        return self.rng.choices(acts, weights=[w[a] for a in acts])[0]

    # ── the venue ──
    def _seq(self, control):
        sub = self.conn.sub
        if control and self.variant == S:
            sub["cseq"] += 1
            return sub["cseq"]
        sub["seq"] += 1
        return sub["seq"]

    def _emit_ok(self, cid):
        sub = self.conn.sub
        self.conn.out_q.append((m_ok(cid, sub["sid"], self._seq(True),
                                     sub["markets"]), None, "ctrl"))

    def _new_sub(self):
        c, r = self.conn, self.rng
        if self.variant == R and c.ended_numbers and r.random() < \
                self.p_reuse:
            sid = c.ended_numbers.pop(r.randrange(len(c.ended_numbers)))
            self.counts["reused_sids"] += 1
        else:
            sid = c.next_sid
            c.next_sid += 1
        self.vinc_n += 1
        c.sub = {"sid": sid, "markets": [], "seq": 0, "cseq": 0,
                 "vinc": self.vinc_n}
        return c.sub

    def _process(self):
        c = self.conn
        cmd = c.in_q.popleft()
        cid, p = cmd["id"], cmd.get("params") or {}
        ts = list(p.get("market_tickers") or ())
        if cmd["cmd"] == "subscribe":
            if c.sub is None:
                sub = self._new_sub()
                c.out_q.append((m_subscribed(cid, sub["sid"]), None, "meta"))
                self.counts["acks"] += 1
                new = [t for t in ts if t not in sub["markets"]]
                sub["markets"] += new
            else:
                sub = c.sub
                new = [t for t in ts if t not in sub["markets"]]
                sub["markets"] += new
                self._emit_ok(cid)
            for t in new:
                self._emit(sub, t, None)
            return
        sub = c.sub
        if sub is None or p.get("sid") != sub["sid"]:
            c.out_q.append((m_error(cid, 7), None, "meta"))
            return
        action = p.get("action")
        if action == "add_markets":
            new = [t for t in ts if t not in sub["markets"]]
            sub["markets"] += new
            self._emit_ok(cid)
            for t in new:
                self._emit(sub, t, None)
        elif action == "delete_markets":
            sub["markets"] = [t for t in sub["markets"] if t not in ts]
            self._emit_ok(cid)
        elif action == "get_snapshot":
            self._emit_ok(cid)
            for t in ts:
                if t in sub["markets"]:
                    self._emit(sub, t, None)

    def _venue_end(self):
        """The venue ends our subscription: error 10 / 25, or its own
        `unsubscribed`. Under R it then reuses the number at once for frames
        answering none of our commands (the runtime ends its session at the
        error, so it reads none of them)."""
        c, r = self.conn, self.rng
        sub = c.sub
        seq = self._seq(True)
        if r.random() < 0.5:
            c.out_q.append((m_error(0, r.choice(TERMINAL), sid=sub["sid"],
                                    seq=seq), None, "meta"))
            self.counts["venue_ends"] += 1
        else:
            c.out_q.append((m_unsubscribed(0, sub["sid"], seq), None,
                            "meta"))
            self.counts["venue_unsubscribes"] += 1
        c.sub = None
        c.ended_numbers.append(sub["sid"])
        if self.variant == R:
            other = self._new_sub()
            c.out_q.append((m_subscribed(None, other["sid"]), None, "meta"))
            for t in SMARKETS:
                other["markets"].append(t)
                self._emit(other, t, None)
            c.sub = None
            c.ended_numbers.append(other["sid"])

    def _emit(self, sub, t, change):
        sub["seq"] += 1
        bk = self.truth[t]
        if change is None:
            m = m_snapshot(sub["sid"], sub["seq"], t,
                           sorted(bk["yes"].items()),
                           sorted(bk["no"].items()))
        else:
            side, price, d = change
            m = m_delta(sub["sid"], sub["seq"], t, price, d, side)
        self.truth_at[(sub["vinc"], sub["seq"])] = (
            t, {"yes": dict(bk["yes"]), "no": dict(bk["no"])})
        self.conn.out_q.append((m, sub["vinc"], "data"))
        self.conn.sent.setdefault(sub["vinc"], []).append(m)

    def _activity(self, t=None):
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
        sub = self.conn.sub
        if sub is not None and t in sub["markets"]:
            self._emit(sub, t, (side, price, d))

    def _deliver(self, calm):
        c, r = self.conn, self.rng
        m, vinc, kind = c.out_q.popleft()
        if kind == "ctrl" and not calm and self.faults and \
                r.random() < self.p_ctrl_loss:
            self.counts["ctrl_lost"] += 1
            return None
        if kind == "data" and not calm and self.faults:
            x = r.random()
            if x < self.p_loss:
                self.counts["lost"] += 1
                c.lost.setdefault(vinc, []).append(m)
                return None
            if x < self.p_loss + self.p_dup:
                c.out_q.appendleft((m, vinc, kind))
                self.counts["duplicated"] += 1
            elif x < self.p_loss + self.p_dup + self.p_replay:
                pool = c.lost.get(vinc) if r.random() < 0.5 else None
                old = [o for o in (pool or c.sent.get(vinc, ()))
                       if o["seq"] <= c.last_seq.get(vinc, 0)]
                if old:
                    c.out_q.appendleft((m, vinc, kind))
                    m = r.choice(old)
                    self.counts["replayed"] += 1
        i = self.oracle.record(("msg", m))
        self.counts["delivered"] += 1
        if vinc is not None:
            self.vinc_of[i] = vinc
            c.last_seq[vinc] = m["seq"]
            self.max_seq_all[vinc] = max(m["seq"],
                                         self.max_seq_all.get(vinc, 0))
        if m.get("type") == "subscribed" and m.get("id") is not None:
            ts = self.oracle.cmds.get(m.get("id"), (None, None, ()))[2]
            if any(t in self.oracle.dropped or self.oracle.epoch.get(
                    t, -1) > self.oracle.cmds[m["id"]][0] for t in ts):
                self.counts["late_acks"] += 1
        return m

    def _quiescence(self):
        """Nothing in flight, nothing awaited: (D) every wanted market is
        CURRENT with the venue's true book, and after the worker's flush
        the store serves exactly that; the commands sent are bounded."""
        self._check("at quiescence")
        self._flush()
        self.live = all(self.books.current(t)["ok"] and _code_book(
            self.books.current(t)) == self.truth[t] for t in self.want)
        if self.variant == D:
            for t in self.want:
                cur = self.books.current(t)
                if not cur["ok"] or _code_book(cur) != self.truth[t]:
                    self._violate("quiescence", [(
                        "LIVENESS_AT_QUIESCENCE", t, cur["state"],
                        cur.get("why"))])
            bound = self.command_bound()
            if self.n_commands > bound:
                self._violate("quiescence", [("COMMANDS_UNBOUNDED",
                                              self.n_commands, bound)])
        self.done = True
        self.stop.set()

    def command_bound(self) -> int:
        """O(markets x connections + gaps + refreshes): per connection one
        subscribe, ceil(M / 2) adds (chunks of two) and one re-add per
        market; per gap ceil(M / 2) get_snapshots (one recovery, the same
        chunks); per refresh one delete or add or get_snapshot."""
        m = len(SMARKETS)
        per_conn = 1 + -(-m // 2) + m
        return (self.connections * per_conn
                + self.books.stats["gaps"] * -(-m // 2)
                + self.counts["drops"] + self.counts["rewants"])


def _new_sim(seed, variant):
    return SubscriberSim(seed, variant=variant, faults=bool(seed % 2))


#: CI seeds per variant (deterministic); odd seeds inject faults
CI_SEEDS = {D: 100, S: 100, R: 100}


def _seeds(variant):
    n = _env_int("KALSHI_PROOF_SEEDS", CI_SEEDS[variant])
    base = _env_int("KALSHI_PROOF_SEED_BASE", 0)
    return list(range(base, base + n))


def run_seed(seed, variant=D):
    return _new_sim(seed, variant).run()


@pytest.mark.parametrize("variant", VARIANTS)
def test_the_subscriber_against_the_model_venue(variant):
    """(b) safety under every variant; under D liveness at quiescence and
    the command bound too. The generator exercised what the proof is
    about."""
    total, live = {}, 0
    seeds = _seeds(variant)
    for seed in seeds:
        sim = run_seed(seed, variant)
        assert sim.violations == [], sim.violations[:3]
        assert sim.done, (variant, seed, "never reached quiescence")
        live += bool(sim.live)
        for k, v in sim.counts.items():
            total[k] = total.get(k, 0) + v
        total["commands"] = total.get("commands", 0) + sim.n_commands
    print("KALSHI_PROOF subscriber variant=%s seeds=%d live=%d %s"
          % (variant, len(seeds), live, total))
    if variant == D:
        assert live == len(seeds)
    for k in ("lost", "duplicated", "replayed", "ctrl_lost", "drops",
              "rewants", "disconnects", "refresh_during_send", "rows_written",
              "restamped", "reader_checks", "gap_writes"):
        assert total[k] > 0, (variant, k, total)
    assert total["venue_ends"] + total["venue_unsubscribes"] > 0
    if variant == R:
        assert total["reused_sids"] > 0


# ── (c) the scripted Subscriber tests, re-run under the oracle ──────────

_SCRIPTED_SOCKETS: list = []


class _OracleScripted:
    """Feeds the oracle (D) from what crosses a scripted socket and checks
    P1, P1b, P2 and L at every step."""

    def _o_init(self):
        self.oracle = Oracle(variant=D, live_from="subscribe")
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
        p = m.get("params") or {}
        ts = tuple(p.get("market_tickers") or ())
        if m["cmd"] == "subscribe" or p.get("action") in ("add_markets",
                                                         "get_snapshot"):
            hit = set(ts) & self.oracle.dropped
            if hit:
                self.o_violations.append(("P1_COMMAND_NAMES_DROPPED", m))
                raise AssertionError(("P1_COMMAND_NAMES_DROPPED", m))
        if m["cmd"] == "subscribe":
            self.oracle.record(("sub", m["id"], ts))
        elif m["cmd"] == "update_subscription":
            self.oracle.record(("upd", m["id"], p.get("sid"),
                                p.get("action"), ts))
        else:
            self.o_violations.append(("A_COMMAND_OUTSIDE_THE_SET", m))
            raise AssertionError(("A_COMMAND_OUTSIDE_THE_SET", m))
        await super().send(s)

    async def close(self):
        self.oracle.record(("disconnect",))
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
    [("SEQ", "test_one_gap_asks_each_market_snapshot_exactly_once", (o,))
     for o in sorted(SEQ._ORDERINGS)]
    + [("MI", "test_the_orderings_with_a_drop_anywhere", c)
       for c in MI._DROPS]
    + [("SEQ", n, ()) for n in (
        "test_a_late_snapshot_never_drops_the_live_subscription",
        "test_a_gap_inside_a_100_market_snapshot_burst_sends_one_get_snapshot",
        "test_no_sid_is_ever_unsubscribed_and_an_error_elsewhere_sends_nothing",
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


# ── the oracle's own findings, directed (second review of the RC6 proof) ──

def test_p2_is_compared_on_the_sid_the_book_is_held_on():
    """The RC6 check accepted a CURRENT book equal to a legitimate book on
    ANY sid. A book held on sid 2 whose levels equal sid 1's legitimate book
    -- sid 2 having broken its sequence after its snapshot -- is now a P2
    violation (a mutant of the books: the real code never holds it)."""
    o = Oracle()
    b = KWS.WsBooks(clock=_clock)
    for t in (WA,):
        o.record(("want", t))
        b.want([t])
    o.record(("connect",))
    b.on_connected()
    snap = m_snapshot(1, 1, WA, [("0.40", "10")], [])
    for sid in (1, 2):
        cid = 10 + sid
        o.record(("sub", cid, (WA,)))
        m = m_subscribed(cid, sid)
        o.record(("msg", m))
        b.bind(sid, [WA])
        b.on_message(m)
    o.record(("msg", snap))
    b.on_message(snap)
    s2 = dict(snap, sid=2)
    o.record(("msg", s2))
    o.record(("msg", m_delta(2, 3, WA, "0.41", "1", "yes")))   # sid 2 broke
    assert sorted(o.legit(WA)) == [1]
    mutant = _clone_books(b)
    mutant.books[WA].update(sid=2)                 # held on sid 2
    mutant.sid_seq[2] = 3
    bad = check(o, mutant, universe=(WA,))
    assert any(x[0] == "P2_CURRENT_WITHOUT_CONTINUITY" and x[2] == 2
               for x in bad), bad
    assert check(o, b, universe=(WA,)) == []       # the real books: held on 1


def test_p1b_is_judged_by_the_subscription_now_holding_the_number():
    """The RC6 check let ANY subscription that ever held a number justify
    an association with it. Under variant R an error-ended number is reused
    by a subscription that never named the market: the association is no
    longer justified (the real books drop it at the end: _end), and a
    mutant that kept it is a P1b violation."""
    o = Oracle(variant=R)
    b = KWS.WsBooks(clock=_clock)
    for t in (WA, WB):
        o.record(("want", t))
        b.want([t])
    o.record(("connect",))
    b.on_connected()
    for e in w_apply(b, o, ("ack", 1)):
        o.record(e)
    for e in w_apply(b, o, ("msg", 1, WA, SNAP, "next")):
        o.record(e)
    for e in w_apply(b, o, ("end", 1, "err10")):
        o.record(e)
    # our next subscription reuses number 1 and names B only
    o.subscribable = {WB}
    for e in w_apply(b, o, ("ack", 1)):
        o.record(e)
    assert check(o, b, universe=(WA, WB)) == []
    assert WA not in b.sid_markets.get(1, ()) and b.ticker_sid.get(WA) != 1
    assert not o.justified(WA, 1) and o.justified(WB, 1)
    mutant = _clone_books(b)
    mutant.sid_markets[1].add(WA)
    bad = check(o, mutant, universe=(WA, WB))
    assert ("P1B_ASSOCIATION_NOT_JUSTIFIED", WA, 1) in bad, bad


def test_assumption_s0_is_named_and_decides_only_numbers_not_ours():
    """S0: a first snapshot above seq 1, with nothing before it, on a
    number never acknowledged as ours, is taken as the subscription's state
    then. The code serves it (the RC6 rule); the protocol alone cannot
    vouch for it -- with S0 off the oracle flags exactly that book, and on
    a number acknowledged as ours the same frame is a gap."""
    for s0, expect in ((True, []), (False, ["P2_CURRENT_WITHOUT_CONTINUITY"])):
        o = Oracle(s0=s0)
        b = KWS.WsBooks(clock=_clock)
        o.record(("want", WA))
        b.want([WA])
        o.record(("connect",))
        b.on_connected()
        m = m_snapshot(1, 7, WA, [("0.40", "10")], [])
        assert b.on_message(m) == "SNAPSHOT"
        o.record(("msg", m))
        assert [x[0] for x in check(o, b, universe=(WA,))] == expect
        assert o.s0_used == (1 if s0 else 0)
    b = KWS.WsBooks(clock=_clock)
    b.want([WA])
    b.on_connected()
    b.bind(1, [WA])
    assert b.on_message(m_snapshot(1, 7, WA, [("0.40", "10")], [])) == "GAP"


def test_a_separate_counters_coincidence_never_hides_a_lost_delta():
    """Variant S, the path a client without the control-frame guard fails
    (the mutation check's M2): our ack, A's snapshot (seq 1), then the reply
    to our 2nd update command arrives with control seq 2 -- the 1st reply
    lost -- exactly the shared slot after A's snapshot; then A's delta past
    a lost data frame. Taking the reply into the sequence made that delta
    look like the next frame: A CURRENT without the lost update. With the
    guard (a separate counter could have reached 2 by the 2nd command's
    reply, and A is CURRENT) the reply is a gap; the oracle agrees at every
    step."""
    b, o = root_state("acked", S)
    for ev in (("msg", 1, WA, SNAP, "next"), ("reply", 1, "ok", "skip"),
               ("msg", 1, WA, DELTA, "skip")):
        for e in w_apply(b, o, ev):
            o.record(e)
        assert check(o, b, universe=WMARKETS) == [], ev
    assert not b.current(WA)["ok"]
    assert b.books[WA]["why"] == KWS.R_CONTROL_SEQUENCE_AMBIGUOUS
    # variant D, the same shape: with one frame on the sid and one command
    # the reply's slot is still one a separate counter could hold -- the
    # guard's documented cost, one gap (safe: the oracle agrees) ...
    b, o = root_state("acked", D)
    for ev in (("msg", 1, WA, SNAP, "next"), ("reply", 1, "ok", "next")):
        for e in w_apply(b, o, ev):
            o.record(e)
        assert check(o, b, universe=WMARKETS) == [], ev
    assert b.books[WA]["why"] == KWS.R_CONTROL_SEQUENCE_AMBIGUOUS
    # ... and once the sid has carried more frames than commands, the reply
    # is the shared slot: taken, and the next delta applies, no gap
    b, o = root_state("all_current_on_sid_1", D)
    for ev in (("reply", 1, "ok", "next"), ("msg", 1, WA, DELTA, "next")):
        for e in w_apply(b, o, ev):
            o.record(e)
        assert check(o, b, universe=WMARKETS) == [], ev
    assert b.current(WA)["ok"] and b.stats["gaps"] == 0
    assert b.stats["deltas"] == 1


# ── regressions the RC6 model found (the legacy rules, unchanged) ───────

@pytest.mark.parametrize("seq", [3, 4, 5, 9],
                         ids=["replay", "older", "duplicate", "skip"])
def test_a_first_snapshot_must_agree_with_the_deltas_before_it(seq):
    """FOUND BY THE RC6 MODEL: a delta (seq 5) arrives on a sid never
    acknowledged as ours before any snapshot. It is not applied and does
    not start the sequence (red-team scenario 5 pins that), but the snapshot
    after it used to be applied unchecked: a REPLAYED (3) or older (4)
    snapshot, a duplicate seq (5), or one past a lost message (9). Each now
    gaps the sid; only the message after the delta (seq 6) or, by
    assumption S5, seq 1 begins it."""
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
        assert ok.on_message(m_delta(1, start + 1, "K-B", "0.43", "1",
                                     "yes")) == "DELTA"


def test_a_dropped_markets_delta_before_any_snapshot_counts_the_same():
    """The same through a dropped market's messages (_not_tracked)."""
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


_F3_CASES = {
    "delta_4_delta_2_snap_3": ([m_delta(1, 4, "K-B", "0.47", "7", "yes"),
                                m_delta(1, 2, "K-A", "0.44", "5", "yes")],
                               m_snapshot(1, 3, "K-B", [("0.43", "100")], [])),
    "delta_2_delta_1_snap_2": ([m_delta(1, 2, "K-A", "0.44", "5", "yes"),
                                m_delta(1, 1, "K-A", "0.44", "5", "yes")],
                               m_snapshot(1, 2, "K-B", [("0.43", "100")], [])),
}


@pytest.mark.parametrize("dropped", [False, True],
                         ids=["tracked", "through_a_dropped_market"])
@pytest.mark.parametrize("case", sorted(_F3_CASES))
def test_an_unacknowledged_first_snapshot_must_follow_the_highest_seq_before_it(
        case, dropped):
    """F3 (review of ca102147): on a sid never acknowledged as ours the
    first snapshot is checked against the HIGHEST seq before it, for a
    tracked market's deltas and a dropped market's alike."""
    pre, snap = _F3_CASES[case]
    b = KWS.WsBooks(clock=_clock)
    b.want(["K-A", "K-B", "K-C"])
    b.on_connected()
    if dropped:
        b.forget(["K-C"])
        pre = [dict(m, msg=dict(m["msg"], market_ticker="K-C")) for m in pre]
    for m in pre:
        assert b.on_message(m) in ("IGNORED_NOT_CURRENT",
                                   "IGNORED_NOT_TRACKED")
    assert b.on_message(snap) == "GAP"
    assert b.books["K-B"]["why"] == KWS.R_SNAPSHOT_OUT_OF_SEQUENCE
    assert not b.current("K-B")["ok"]
    ok = KWS.WsBooks(clock=_clock)
    ok.want(["K-A", "K-B"])
    ok.on_connected()
    for m in _F3_CASES[case][0]:
        ok.on_message(m)
    top = max(m["seq"] for m in _F3_CASES[case][0])
    assert ok.on_message(m_snapshot(1, top + 1, "K-B", [("0.43", "1")], [])) \
        == "SNAPSHOT"
    assert ok.on_message(m_delta(1, top + 2, "K-B", "0.43", "1", "yes")) == \
        "DELTA"


# ── our ack starts the sequence (RC6 F1, under the documented repair) ───

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
    # its seq-2 delta delivered first, the seq-1 snapshot late
    "late_first_snapshot": [
        (m_delta(1, 2, _A, "0.44", "5", "yes"), "GAP"),
        (m_snapshot(1, 1, _A, [("0.43", "100")], []), "GAP")],
    # three deltas, then the seq-1 snapshot
    "three_deltas_then_seq_1": [
        (m_delta(1, 2, _A, "0.40", "5", "yes"), "GAP"),
        (m_delta(1, 3, _A, "0.41", "7", "yes"), "IGNORED_NOT_CURRENT"),
        (m_delta(1, 4, _A, "0.40", "-100", "yes"), "IGNORED_NOT_CURRENT"),
        (m_snapshot(1, 1, _A, [("0.40", "100")], []), "GAP")],
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
    """F1 (review of ca102147). After our ack the venue sends the
    subscription's frames in order from seq 1. A seq-1 snapshot after the
    subscription's own later frames, a lost first snapshot, a duplicate seq:
    the first frame that is not the next gaps the sid. (RC6.2) The sid stays
    live and is asked for fresh snapshots; only a snapshot in sequence after
    the gap's baseline -- never the late one -- restores CURRENT, on the
    same sid."""
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
    assert {_A, _B} <= b.recover[1] and 1 not in b.dead_sids
    nxt = b.sid_seq[1] + 1
    m = m_snapshot(1, nxt, _A, [("0.47", "9")], [])
    assert b.on_message(m) == "SNAPSHOT"
    o.record(("msg", m))
    assert check(o, b, universe=(_A, _B)) == []
    assert b.current(_A)["book"]["orderbook_fp"]["yes_dollars"] == \
        [["0.47", "9"]] and b.books[_A]["sid"] == 1


class _Sock:
    """A scripted socket: frames (dicts) in order; ConnectionError at the
    end; every sent command recorded."""

    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    async def send(self, s):
        self.sent.append(json.loads(s))

    async def recv(self):
        if not self.script:
            raise ConnectionError("end")
        return json.dumps(self.script.pop(0))

    async def close(self):
        pass


def test_the_subscriber_never_serves_a_late_first_snapshot():
    """F1 end to end (the shape of CI seed 55 of ca102147), RC6.2: our
    subscribe acknowledged as sid 2, its seq-2 delta, then its seq-1
    snapshot late. The delta gaps the sid and ONE get_snapshot asks it for
    a fresh one; the late snapshot is a second gap while that is
    outstanding: never served, the session ends. The next connection's own
    snapshot is the first book served."""
    socks = [_Sock([m_subscribed(1, 2),
                    m_delta(2, 2, "KS-A", "0.44", "5", "yes"),
                    m_snapshot(2, 1, "KS-A", [("0.43", "100")], [])]),
             _Sock([m_subscribed(3, 2),
                    m_snapshot(2, 1, "KS-A", [("0.43", "100"),
                                              ("0.44", "5")], [])])]
    pool = list(socks)
    books = KWS.WsBooks(clock=_clock)
    served, ends = [], []
    real = books.on_message

    def watch(m, **k):
        out = real(m, **k)
        cur = books.current("KS-A")
        served.append(cur["book"]["orderbook_fp"]["yes_dollars"]
                      if cur["ok"] else None)
        return out
    books.on_message = watch

    async def connect():
        return pool.pop(0)
    sub = KWS.Subscriber(connect, books, wanted=lambda: ["KS-A"],
                         clock=_clock)
    for _ in socks:
        try:
            asyncio.run(sub.session())
        except Exception as exc:                                # noqa: BLE001
            ends.append(type(exc).__name__)
    assert ends == ["GapDuringRecovery", "ConnectionError"]
    assert [(m["cmd"], m["params"].get("action")) for m in socks[0].sent] \
        == [("subscribe", None), ("update_subscription", "get_snapshot")]
    assert [m["cmd"] for m in socks[1].sent] == ["subscribe"]
    assert [x for x in served if x] == [[["0.43", "100"], ["0.44", "5"]]]


def test_a_late_ack_never_binds_a_market_dropped_after_its_command(
        monkeypatch):
    """P1b, directed (RC6.2): B is dropped after the subscribe (cmd 1)
    named it and wanted again before cmd 1 is acknowledged. The late ack
    binds A alone; the venue still holds B (cmd 1 named it, no delete went
    out), so B is asked for by get_snapshot -- bound to the sid when THAT is
    sent -- and its fresh snapshot makes it CURRENT."""
    monkeypatch.setattr(MI, "VW", OracleVW)
    _SCRIPTED_SOCKETS.clear()
    want = ["K-A", "K-B"]
    ws, _sub, b, seen, ctx, go = MI.drive([], want)
    bound = {"binds": []}
    real_bind = b.bind

    def bind(sid, tickers, *, ours=True):
        bound["binds"].append((sid, sorted(tickers), ours))
        return real_bind(sid, tickers, ours=ours)
    b.bind = bind
    ws.script = [MI.prune_to(ctx, ["K-A"]), MI.want_to(ctx, ["K-A", "K-B"]),
                 SEQ.subscribed(1, 7),        # the LATE ack of cmd 1
                 SEQ.snap(7, 1, "K-A"), SEQ.snap(7, 2, "K-B"),
                 lambda: bound.update(cmds=[(m["cmd"], m["params"].get(
                     "action"), m["params"].get("market_tickers"))
                     for m in ws.sent]),
                 SEQ.ok(2, 7, 3), SEQ.snap(7, 4, "K-B"),
                 SEQ.delta(7, 5, "K-A"), SEQ.delta(7, 6, "K-B")]
    asyncio.run(go())
    # the ack bound A alone; B only when its own get_snapshot was sent
    assert bound["binds"][:2] == [(7, ["K-A"], True), (7, ["K-B"], True)]
    assert bound["cmds"][0] == ("subscribe", None, ["K-A", "K-B"])
    assert bound["cmds"][1] == ("update_subscription", "get_snapshot",
                                ["K-B"])
    assert ws.o_violations == []
    assert seen["K-A"]["ok"] and seen["K-B"]["ok"] and seen["K-B"]["sid"] == 7


def test_an_ack_that_answers_none_of_our_commands_starts_no_sequence():
    """Which `subscribed` anchors a sequence, at the Subscriber: only one
    answering the subscribe THIS session sent. An announcement with no id,
    an unknown id or an earlier session's id does not -- there a delta ahead
    of a seq-1 snapshot falls under assumption S5 (red-team scenario 5 pins
    it) and the snapshot is served; after the ack of our own subscribe the
    same frames are a gap, the repair is asked for, and the late snapshot is
    a second gap while it is outstanding (the session ends)."""
    def run(ack_id, *, sessions=1):
        scripts = [[] for _ in range(sessions - 1)] + [[
            {"type": "subscribed", "id": ack_id,
             "msg": {"channel": "orderbook_delta", "sid": 1}},
            m_delta(1, 2, "K-A", "0.44", "1", "yes"),
            m_snapshot(1, 1, "K-A", [("0.52", "3")], [])]]
        out, ends = [], []
        books = KWS.WsBooks(clock=_clock)
        real = books.on_message

        def watch(m, **k):
            out.append(real(m, **k))
            return out[-1]
        books.on_message = watch

        async def connect():
            return _Sock(scripts.pop(0))
        sub = KWS.Subscriber(connect, books, wanted=lambda: ["K-A"],
                             clock=_clock)
        for _ in range(sessions):
            try:
                asyncio.run(sub.session())
            except Exception as exc:                            # noqa: BLE001
                ends.append(type(exc).__name__)
        return out, ends[-1]
    s5 = (["SUBSCRIBED", "IGNORED_NOT_CURRENT", "SNAPSHOT"],
          "ConnectionError")
    assert run(None) == s5                      # no id
    assert run(99) == s5                        # an id we never sent
    assert run(1, sessions=2) == s5             # session 1's command id
    ours = (["SUBSCRIBED", "GAP", "GAP"], "GapDuringRecovery")
    assert run(1) == ours
    assert run(2, sessions=2) == ours


def test_the_pending_map_evicts_its_oldest_command(monkeypatch):
    """MAX_PENDING_SUBSCRIBES bounds the commands awaiting a reply; past it
    the OLDEST is forgotten, never one just sent."""
    monkeypatch.setattr(KWS, "MAX_PENDING_SUBSCRIBES", 2)
    books = KWS.WsBooks(clock=_clock)
    ts = ["K-%03d" % i for i in range(300)]
    sub = KWS.Subscriber(None, books, wanted=lambda: ts)
    books.on_connected()
    books.bind(1, [])
    sub.sid, sub.sub_id = 1, 0
    sock = _Sock([])
    asyncio.run(sub._commands(sock))
    assert [m["params"]["action"] for m in sock.sent] == ["add_markets"] * 3
    assert sorted(sub.pending) == [2, 3]


# ── the worker's writer and its readers ──

def test_the_flush_key_tells_every_version_of_a_book_apart():
    """(review of ca102147) The flush key was (state, seq, updated_at): a
    disconnect and the next connection's first snapshot at the same seq
    inside one clock tick left it unchanged, so the row kept the old book.
    The key is the book's change stamp."""
    store, written = BooksStore(), {}
    b = _acked([_A])
    b.on_message(m_snapshot(1, 1, _A, [("0.40", "10")], []))
    _drive(W.flush(store, b, written, now=NOW))
    before = dict(b.books[_A])
    b.on_disconnected()
    b.on_connected()
    b.bind(1, [_A])                        # the next connection, same number
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
    is not: the sid gapped and its repair's snapshot arrived since the last
    flush, so the book is CURRENT again -- but its row still holds the
    PRE-GAP book (here without the gap sink, as a worker whose immediate
    write failed). Only a row holding the book as it is now is re-stamped."""
    store, written = BooksStore(), {}
    b = _acked([_A])
    b.on_message(m_snapshot(1, 1, _A, [("0.40", "10")], []))
    _drive(W.flush(store, b, written, now=NOW))
    assert b.on_message(m_delta(1, 3, _A, "0.40", "1", "yes")) == "GAP"
    assert b.on_message(m_ok(9, 1, 4)) == "OK"
    assert b.on_message(m_snapshot(1, 5, _A, [("0.45", "7")], [])) == \
        "SNAPSHOT"
    assert _drive(W.reassert(store, b, now=NOW + 5, written=written)) == 0
    assert store.rows[_A]["observed_at"] == NOW
    _drive(W.flush(store, b, written, now=NOW + 6))
    assert reader_check(store, b, [_A], now=NOW + 6, synced=True) == []
    assert _drive(W.reassert(store, b, now=NOW + 7, written=written)) == 1
    assert store.rows[_A]["observed_at"] == NOW + 7


def test_the_gap_write_closes_the_reader_window():
    """(the second review's reader window) Between a gap and the next flush
    the row of the pre-gap book used to stay readable. write_gaps -- what
    the Subscriber's gap sink runs before it reads the next frame -- makes
    it unreadable at once, records what the row now holds, and leaves the
    flush nothing to redo."""
    store, written = BooksStore(), {}
    b = _acked([_A, _B])
    b.on_message(m_snapshot(1, 1, _A, [("0.40", "10")], []))
    b.on_message(m_snapshot(1, 2, _B, [("0.41", "10")], []))
    _drive(W.flush(store, b, written, now=NOW))
    assert set(store.served(NOW)) == {_A, _B}
    assert b.on_message(m_delta(1, 4, _A, "0.40", "1", "yes")) == "GAP"
    left = sorted(b.left_current)
    assert left == [_A, _B]
    assert _drive(W.write_gaps(store, b, written, left)) == 2
    assert store.served(NOW) == {}
    assert store.rows[_A]["error"] == "GAP:%s" % KWS.R_SEQ_GAP
    assert _drive(W.flush(store, b, written, now=NOW)) == 0


def test_a_market_the_worker_drops_has_its_row_retired_in_the_same_pass():
    """(review of ca102147) The wanted-set refresh retires every readable
    WebSocket row outside the set; a REST row is not touched."""
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
    every heartbeat reports the key CLASS; (RC6.2) a gap makes the row
    unreadable before anything later is applied (the gap sink), the repair
    is get_snapshot on the same sid, and a pass whose reassert is due and
    whose flush is not leaves the row holding the pre-gap book unstamped;
    the wanted-set refresh that drops a market retires its row."""
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
            self.seq = 0
            self.sent = []

        def _next(self):
            self.seq += 1
            return self.seq

        async def send(self, s):
            m = json.loads(s)
            self.sent.append(m)
            p = m["params"]
            if m["cmd"] == "subscribe":
                self.q.put_nowait(json.dumps(m_subscribed(m["id"], 1)))
            else:
                self.q.put_nowait(json.dumps(m_ok(m["id"], 1, self._next())))
            if m["cmd"] == "subscribe" or p.get("action") in (
                    "add_markets", "get_snapshot"):
                for t in p["market_tickers"]:
                    self.q.put_nowait(json.dumps(m_snapshot(
                        1, self._next(), t, levels[t], [])))

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
            seen["first_row"] = dict(conn.rows[a])
            monkeypatch.setattr(W, "FLUSH_EVERY_S", 1e9)
            levels[a] = [("0.45", "7")]
            sock.seq += 1                     # a frame lost on the way
            sock.q.put_nowait(json.dumps(m_delta(1, sock._next(), a, "0.40",
                                                 "1", "yes")))
        elif n == 3:
            seen["after_reassert"] = dict(conn.rows[a])
            seen["book_now"] = KWS.WsBooks.book_of(
                _BOOKS["b"], a)["orderbook_fp"]["yes_dollars"]
            want.clear()
        elif n == 4:
            seen["after_drop"] = dict(conn.rows[a])
            raise Stop()
        await real_sleep(0.05)
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
        "Lock": asyncio.Lock,
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
    # the subscribe, then the repair on the same sid (the drop's
    # delete_markets goes out with the session's next frame, after pass 4)
    assert [(m["cmd"], m["params"].get("action")) for m in sock.sent] == [
        ("subscribe", None), ("update_subscription", "get_snapshot")]
    # pass 3: A CURRENT again (the get_snapshot's book), its row unreadable
    # since the gap (the gap sink), its levels and age untouched
    assert seen["book_now"] == [["0.45", "7"]]
    assert seen["after_reassert"]["readable"] is False
    assert seen["after_reassert"]["error"] == "GAP:%s" % KWS.R_SEQ_GAP
    assert seen["after_reassert"]["yes_bids"] == \
        seen["first_row"]["yes_bids"]
    assert seen["after_reassert"]["observed_at"] == \
        seen["first_row"]["observed_at"]
    assert seen["after_drop"]["readable"] is False
    assert seen["after_drop"]["error"] in (W.UNTRACKED,
                                           "GAP:%s" % KWS.R_SEQ_GAP)


DSN = os.environ.get("RN1X_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
def test_the_reader_path_on_postgres():
    """The worker's statements on the real kalshi_books_current, read by
    the REST worker's own readers: ws_current (a market there is not polled
    by REST) and db_freshness (counted current, by basis); (RC6.2) the
    immediate GAP write takes a gapped row out of both at once. The
    in-memory BooksStore the model checks against must agree with this."""
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
            await conn.execute(
                "INSERT INTO kalshi_books_current (ticker, event_ticker, "
                " series_ticker, book_basis, readable, observed_at) VALUES "
                " ($1, 'KXPROOF-EV0', 'KXPROOF', $2, true, to_timestamp($3))",
                old, KWS.BOOK_BASIS, now)
            await W.flush(conn, b, written, now=now)
            _drive(W.flush(store, b, dict(), now=now))
            ts = [a, bb, c, old]
            assert await RESTW.ws_current(conn, ts) == {a, bb, c, old}
            sub = KWS.Subscriber(None, b, wanted=lambda: [a, c])
            W.prune_untracked(sub, written, [a, c])
            await W.retire_untracked(conn, [a, c])
            assert b.on_message(m_delta(2, 3, c, "0.42", "1", "yes")) == "GAP"
            # the immediate GAP write: out of both readers at once
            assert await W.write_gaps(conn, b, written,
                                      sorted(b.left_current)) == 1
            assert await RESTW.ws_current(conn, ts) == {a}
            fr = await RESTW.db_freshness(conn, ts, now=now)
            assert fr["current_by_source"] == {"WS": 1, "REST": 0}
            assert await W.flush(conn, b, written, now=now + 1) == 0
            rows = {r["ticker"]: r for r in await conn.fetch(
                "SELECT ticker, readable, error, yes_bids FROM "
                " kalshi_books_current WHERE ticker = ANY($1::text[])", ts)}
            assert rows[bb]["error"] == rows[old]["error"] == W.UNTRACKED
            assert rows[c]["error"] == "GAP:%s" % KWS.R_SEQ_GAP
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
    """(review of ca102147) `send` can suspend and the worker's prune runs
    then. Every record of a command is made before the send, and each later
    chunk leaves out what was dropped meanwhile. (RC6.2) The subscribe (the
    first chunk) named the dropped market, so once the sid is known it is
    taken out by delete_markets; the add_markets of the rest never names the
    other dropped market; wanted again, it is added by a command of its
    own."""
    many = ["KXT-EV-%03d" % i for i in range(150)]
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
            if len(self.sent) == 1:          # the subscribe is on its way
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
        assert len(sock.sent) == 1 and gone[0] in \
            sock.sent[0]["params"]["market_tickers"]
        for t in gone:
            assert t not in sub.subscribed
            assert not any(t in ts for ts in sub.pending.values())
        sock.q.put_nowait(json.dumps(m_subscribed(1, 1)))
        for _ in range(20):
            await asyncio.sleep(0)
        cmds = [(m["cmd"], m["params"].get("action"),
                 m["params"]["market_tickers"]) for m in sock.sent]
        assert cmds[1] == ("update_subscription", "delete_markets",
                           [gone[0]])
        assert cmds[2][:2] == ("update_subscription", "add_markets")
        assert gone[1] not in cmds[2][2] and len(cmds[2][2]) == 49
        assert books.ticker_sid.get(gone[0]) is None   # the ack: no bind
        want.append(gone[0])
        sock.q.put_nowait(json.dumps(m_ok(2, 1, 1)))
        for _ in range(20):
            await asyncio.sleep(0)
        assert sock.sent[-1]["params"] == {"sid": 1, "action": "add_markets",
                                           "market_tickers": [gone[0]]}
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())


def test_a_session_that_starts_with_nothing_wanted_subscribes_when_it_fills():
    """(review of ca102147) A session with nothing to read re-reads the
    wanted set every IDLE_RECHECK_S."""
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
