"""RC6.2 REVIEW: VENUE MEMBERSHIP -- a book is CURRENT only for a market the
venue HOLDS on the sid by its own replies (sportsassets.kalshi_ws module
docstring, VENUE MEMBERSHIP).

Two blocking review findings, each reproduced here against a venue that does
what docs.kalshi.com documents, with ONE counter per sid and no frame lost
unless a step says so:

  * get_snapshot "returns an orderbook_snapshot for the requested
    market_tickers without modifying the subscription" (asyncapi; changelog
    2026-04-20: "without adding them to the subscription or affecting the
    existing delta stream"). The RC6.2 candidate named, in its get_snapshot,
    a market whose add the venue had refused (error 26), and the venue's
    snapshot of it made the book CURRENT -- with no delta ever following:
    a stale book served as current (scenarios A and B of the protocol
    review).
  * a market dropped (delete_markets sent) and wanted again (add_markets
    sent) went CURRENT from its snapshot sent BEFORE the delete, while the
    venue streamed nothing for it between processing the delete and the
    re-add (the safety review).

Each test checks, before every step, that every CURRENT book equals the
venue's TRUE book as of the highest seq delivered (the venue changes a
market it does not hold without telling anyone). Run against 609c52da's
kalshi_ws.py they fail with STALE_CURRENT; here they pass.

The non-blocking findings taken are pinned too: an error 10 / 25 without a
sid ends the session; a re-add the venue refused again for the command rate
(error 27) is retried after RATE_LIMIT_RETRY_S, bounded; a session that
lasted BACKOFF_RESET_AFTER_S resets the reconnect backoff.

This file imports nothing but sportsassets.kalshi_ws (it runs unchanged
against an earlier module). SMALL LIVE = SHADOW: market data only."""
from __future__ import annotations

import asyncio
import json
from collections import deque
from decimal import Decimal

import pytest

from sportsassets import kalshi_ws as KWS

NOW = 1_791_900_000.0
SNAP, DELTA = "orderbook_snapshot", "orderbook_delta"
A, B = "KXM-A", "KXM-B"


def m_subscribed(cid, sid):
    return {"type": "subscribed", "id": cid,
            "msg": {"channel": "orderbook_delta", "sid": sid}}


def _lv(levels):
    return [[str(p), str(q)] for p, q in sorted(levels.items())]


class MemberVenue:
    """The documented venue's side of ONE connection, driven step by step.

    Commands wait in `inq` until a "process" step; frames leave in order
    through `out` on "deliver" ("lose" drops the next one; "idle" raises
    the TimeoutError the runtime's wait_for raises when nothing arrives
    for IDLE_RECHECK_S, so it runs its commands). One counter per sid:
    snapshots, deltas, `ok` and scoped errors take the sid's next seq.
      * subscribe -> `subscribed` {id, sid 1}, then each market's snapshot;
      * add_markets -> the whole command refused by error 26 past
        `market_limit` markets, or by error 27 for the first `refuse27`
        adds (scoped: (sid, seq), when `scoped`); otherwise `ok` {id, sid,
        seq, msg.market_tickers: the full list after it}, then each new
        market's snapshot;
      * delete_markets -> `ok` with the full list;
      * get_snapshot -> a snapshot of EVERY requested market, held or not
        ("without modifying the subscription"); an `ok` only if `gs_ok`.
    `truth` is the venue's book of EVERY market; `activity` changes it and
    streams a delta only when the subscription holds the market. Each frame
    with a seq records the whole truth at its emission; `delivered_truth`
    is that of the highest seq delivered."""

    def __init__(self, *, market_limit=None, scoped=True, gs_ok=False,
                 refuse27=0, refuse27_at=()):
        self.market_limit, self.scoped, self.gs_ok = (market_limit, scoped,
                                                      gs_ok)
        self.refuse27 = refuse27
        #: the add_markets (1-based, in processing order) refused by 27
        self.refuse27_at = set(refuse27_at)
        self.adds_seen = 0
        self.markets = None             # the subscription's tickers
        self.seq = 0
        self.inq, self.out = deque(), deque()
        self.sent, self.delivered = [], []
        self.truth = {}
        self.truth_at = {}
        self.delivered_truth = None
        self.steps = deque()
        self.check = None
        #: (optional) an async probe run before every step, and steps may
        #: be coroutine functions (the worker's writers, on Postgres:
        #: tests/test_rc62_kalshi_ws_reader_proof_pg.py)
        self.aprobe = None

    def book(self, t):
        return self.truth.setdefault(t, {"yes": {Decimal("0.40"):
                                                 Decimal(10)}, "no": {}})

    def _seq(self):
        self.seq += 1
        self.truth_at[self.seq] = {
            t: {"yes": dict(b["yes"]), "no": dict(b["no"])}
            for t, b in self.truth.items()}
        return self.seq

    def _snap(self, t):
        bk = self.book(t)
        return {"type": SNAP, "sid": 1, "seq": self._seq(),
                "msg": {"market_ticker": t, "yes_dollars_fp": _lv(bk["yes"]),
                        "no_dollars_fp": _lv(bk["no"])}}

    def _ok(self, cid, listed=True):
        m = {"type": "ok", "id": cid, "sid": 1, "seq": self._seq()}
        if listed:
            m["msg"] = {"market_tickers": list(self.markets)}
        return m

    def _err(self, cid, code):
        m = {"type": "error", "id": cid,
             "msg": {"code": code, "msg": "error %d" % code}}
        if self.scoped:
            m.update(sid=1, seq=self._seq())
        return m

    # ── the venue ──
    def process(self):
        if not self.inq:
            return                  # nothing sent to process
        m = self.inq.popleft()
        cid, p = m["id"], m.get("params") or {}
        ts = list(p.get("market_tickers") or ())
        if m["cmd"] == "subscribe":
            assert self.markets is None, "one subscribe per connection"
            self.markets = list(ts)
            self.out.append(m_subscribed(cid, 1))
            self.out.extend(self._snap(t) for t in ts)
            return
        assert m["cmd"] == "update_subscription" and p.get("sid") == 1, m
        action = p["action"]
        if action == "add_markets":
            self.adds_seen += 1
            new = [t for t in ts if t not in self.markets]
            if self.market_limit is not None and \
                    len(self.markets) + len(new) > self.market_limit:
                self.out.append(self._err(cid, 26))
                return
            if self.refuse27 or self.adds_seen in self.refuse27_at:
                self.refuse27 = max(0, self.refuse27 - 1)
                self.out.append(self._err(cid, 27))
                return
            self.markets += new
            self.out.append(self._ok(cid))
            self.out.extend(self._snap(t) for t in new)
        elif action == "delete_markets":
            self.markets = [t for t in self.markets if t not in ts]
            self.out.append(self._ok(cid))
        elif action == "get_snapshot":
            if self.gs_ok:
                self.out.append(self._ok(cid, listed=False))
            self.out.extend(self._snap(t) for t in ts)
        else:
            raise AssertionError(action)

    def activity(self, t, price, qty):
        """The market moves at the venue: a level set to `qty` (0 removes
        it); a delta only if the subscription holds the market."""
        lv = self.book(t)["yes"]
        p, q = Decimal(price), Decimal(qty)
        d = q - lv.get(p, Decimal(0))
        if q > 0:
            lv[p] = q
        else:
            lv.pop(p, None)
        if self.markets is not None and t in self.markets:
            self.out.append({"type": DELTA, "sid": 1, "seq": self._seq(),
                             "msg": {"market_ticker": t, "price_dollars":
                                     price, "delta_fp": str(d),
                                     "side": "yes"}})

    # ── the socket ──
    async def send(self, raw):
        m = json.loads(raw)
        self.sent.append(m)
        self.inq.append(m)

    async def recv(self):
        while True:
            if self.check is not None:
                self.check()
            if self.aprobe is not None:
                await self.aprobe()
            if not self.steps:
                raise ConnectionError("script over")
            st = self.steps.popleft()
            if st == "deliver":
                if not self.out:
                    raise ConnectionError("nothing left to deliver")
                m = self.out.popleft()
                self.delivered.append(m)
                if m.get("seq") is not None:
                    self.delivered_truth = self.truth_at[m["seq"]]
                return json.dumps(m)
            if st == "lose":
                self.out.popleft()
            elif st == "idle":
                raise asyncio.TimeoutError()
            elif st == "process":
                self.process()
            elif asyncio.iscoroutinefunction(st):
                await st(self)
            else:
                st(self)

    async def close(self):
        pass

    def commands(self):
        return [(m["cmd"], (m.get("params") or {}).get("action"),
                 (m.get("params") or {}).get("market_tickers"))
                for m in self.sent]


def _code_book(cur):
    fp = cur["book"]["orderbook_fp"]
    return {"yes": {Decimal(p): Decimal(q) for p, q in fp["yes_dollars"]},
            "no": {Decimal(p): Decimal(q) for p, q in fp["no_dollars"]}}


def run(venue, wanted, steps, *, clock=lambda: NOW):
    """One session of the real Subscriber against `venue` playing `steps`;
    before every step, every CURRENT book must equal the venue's truth as
    of the highest seq delivered (else STALE_CURRENT is recorded)."""
    books = KWS.WsBooks(clock=clock)
    seen = {"violations": [], "current_ever": set()}

    def check():
        truth = venue.delivered_truth
        # what the books held before this step (the last: before the
        # script's end disconnects the session, which GAPs every book)
        seen["final"] = {t: dict(books.current(t), state_raw=b["state"],
                                 why_raw=b["why"])
                         for t, b in books.books.items()}
        for t in sorted(books.books):
            cur = books.current(t)
            if not cur["ok"]:
                continue
            seen["current_ever"].add(t)
            want = (truth or {}).get(t)
            if want is None or _code_book(cur) != want:
                seen["violations"].append(("STALE_CURRENT", t, len(
                    venue.delivered), _code_book(cur), want))
    venue.check = check
    venue.steps.extend(steps)
    sub = KWS.Subscriber(None, books, wanted=lambda: list(wanted),
                         clock=clock, idle_recheck_s=5.0)
    venue.sub = sub                 # the steps act as the worker through it

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


def _snapshot_requests(venue):
    return [ts for cmd, action, ts in venue.commands()
            if action == "get_snapshot"]


# ── blocking 1: get_snapshot answers for a market the venue does not hold ──

def test_scenario_a_a_refused_market_is_never_current_from_a_get_snapshot():
    """Scenario A of the protocol review: market limit 1, error 26 scoped.
    subscribe [A] -> A CURRENT; B wanted -> add B -> error 26 at seq 2 (the
    documented false 'ambiguous' gap: A's book goes GAP and is asked for
    again); the re-add of B is refused too -> B GAP, not held. The
    get_snapshot names A ONLY (the venue would answer for B too, and no
    delta of B would ever follow); B then moves at the venue."""
    v = MemberVenue(market_limit=1)
    want = [A]
    steps = ["process", "deliver", "deliver",
             lambda v_: want.append(B), "idle",       # add B (cid 2)
             "process", "deliver",                    # error 26, seq 2
             "process", "process", "deliver", "deliver",
             lambda v_: v_.activity(B, "0.41", "7"),
             lambda v_: v_.activity(A, "0.45", "3"), "deliver",
             lambda v_: v_.activity(B, "0.42", "3"),
             lambda v_: v_.activity(A, "0.46", "2"), "deliver"]
    sub, books, seen = run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:2]
    assert isinstance(seen["ended"], ConnectionError), seen.get("ended")
    # the venue never held B; it was never served
    assert B not in v.markets and B not in seen["current_ever"]
    fin = seen["final"]
    assert fin[B]["state_raw"] == KWS.GAP and not fin[B]["ok"]
    assert fin[B]["why_raw"] == KWS.R_NOT_HELD_BY_VENUE
    assert fin[A]["ok"]
    # no get_snapshot ever named B; one subscribe, the add and its ONE
    # re-add, one get_snapshot of A
    assert all(B not in ts for ts in _snapshot_requests(v))
    assert [(c, a) for c, a, _t in v.commands()] == [
        ("subscribe", None), ("update_subscription", "add_markets"),
        ("update_subscription", "add_markets"),
        ("update_subscription", "get_snapshot")]
    assert _snapshot_requests(v) == [[A]]
    assert books.stats["snapshots_not_held"] == 0


def test_scenario_b_unscoped_refusal_and_a_lost_delta():
    """Scenario B: error 26 WITHOUT (sid, seq), and the gap comes from a
    lost delta while B's re-add is still unanswered -- the RC6.2 candidate
    named B in that gap's get_snapshot, its re-add was then refused, and the
    venue's snapshot of B made it CURRENT. Here B is never named, never
    CURRENT; A recovers on the same sid."""
    v = MemberVenue(market_limit=1, scoped=False)
    want = [A]
    steps = ["process", "deliver", "deliver",
             lambda v_: want.append(B), "idle",       # add B (cid 2)
             "process", "deliver",                    # error 26 (no sid)
             lambda v_: v_.activity(A, "0.45", "3"), "lose",
             lambda v_: v_.activity(A, "0.46", "2"), "deliver",   # the gap
             # whatever the client sent since: processed, then delivered
             "process", "process", "process",
             lambda v_: v_.steps.extendleft(["deliver"] * len(v_.out)),
             lambda v_: v_.activity(B, "0.41", "7"),
             lambda v_: v_.activity(A, "0.47", "1"), "deliver"]
    sub, books, seen = run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:2]
    assert B not in seen["current_ever"]
    fin = seen["final"]
    assert fin[B]["why_raw"] == KWS.R_NOT_HELD_BY_VENUE and not fin[B]["ok"]
    assert all(B not in ts for ts in _snapshot_requests(v))
    assert _snapshot_requests(v) == [[A]]
    assert fin[A]["ok"] and books.stats["gaps"] == 1


def test_a_snapshot_of_a_market_the_venue_does_not_hold_is_never_applied():
    """Defence in depth, WsBooks alone: whatever names it, a snapshot of a
    market the venue's reply did not list -- in the sid's sequence -- keeps
    the sequence and is never applied; the venue's later `ok` listing it
    makes it eligible, and only a snapshot after that is applied."""
    b = KWS.WsBooks(clock=lambda: NOW)
    b.want([A, B])
    b.on_connected()
    b.bind(1, [A])
    b.on_message(m_subscribed(1, 1))
    snap = (lambda seq, t, px="0.40": {
        "type": SNAP, "sid": 1, "seq": seq,
        "msg": {"market_ticker": t, "yes_dollars_fp": [[px, "10"]],
                "no_dollars_fp": []}})
    assert b.on_message(snap(1, A)) == "SNAPSHOT"
    b.bind(1, [B])
    b.command_sent(1, 2, "add_markets", [B])
    # its ok lists the subscription WITHOUT B (the venue did not take it)
    assert b.on_message({"type": "ok", "id": 2, "sid": 1, "seq": 2,
                         "msg": {"market_tickers": [A]}}) in ("OK", "GAP")
    b.fatal = None
    assert b.books[B]["why"] == KWS.R_NOT_HELD_BY_VENUE
    top = b.sid_seq[1]
    assert b.on_message(snap(top + 1, B)) == "IGNORED_NOT_HELD"
    assert not b.current(B)["ok"] and b.sid_seq[1] == top + 1
    assert b.stats["snapshots_not_held"] == 1
    # a re-add the venue takes: its ok lists B, then B's own snapshot
    b.command_sent(1, 3, "add_markets", [B])
    assert b.on_message(snap(top + 2, B)) == "IGNORED_NOT_HELD"   # unanswered
    assert b.on_message({"type": "ok", "id": 3, "sid": 1, "seq": top + 3,
                         "msg": {"market_tickers": [A, B]}}) == "OK"
    # a snapshot of B came while its add was unanswered (maybe the add's
    # own): a fresh one is asked for
    assert B in b.recover[1]
    assert b.on_message(snap(top + 4, B, "0.44")) == "SNAPSHOT"
    assert b.current(B)["ok"]


# ── blocking 2: dropped, wanted again, a pre-delete snapshot in flight ──

def _rewant_steps(want):
    return ["process", "deliver", "deliver",          # subscribed, A's snap
            # B's subscribe snapshot is still unread: the worker drops B
            # (delete_markets sent) and wants it again (add_markets sent)
            lambda v_: (want.remove(B), v_.sub.forget([B])), "idle",
            lambda v_: want.append(B), "idle",
            "deliver",                                # B's PRE-delete snap
            "process",                                # the delete: ok [A]
            lambda v_: v_.activity(B, "0.45", "99"),  # not streamed
            lambda v_: v_.activity(A, "0.41", "4"),
            "deliver", "deliver",                     # ok(delete), A delta
            "process",                                # the add: ok, B snap
            "deliver", "deliver",
            "process", "deliver",                     # any get_snapshot
            lambda v_: v_.activity(B, "0.46", "5"),
            lambda v_: v_.activity(A, "0.42", "6"), "deliver", "deliver"]



def test_a_dropped_and_rewanted_market_is_never_current_from_a_pre_delete_snapshot():
    """The safety review's sequence, no frame lost: B dropped -> delete
    sent; wanted again -> add sent; B's snapshot from before the delete
    arrives in sequence. It used to make B CURRENT; the venue then processed
    the delete and B moved without a delta: B was served stale until the
    re-add's snapshot. Now no snapshot of B is applied while an add / delete
    naming it is unanswered; B is CURRENT again only from a snapshot after
    the venue's `ok` listing it, equal to the venue's book."""
    v = MemberVenue()
    want = [A, B]
    sub, books, seen = run(v, want, _rewant_steps(want))
    assert seen["violations"] == [], seen["violations"][:2]
    acts = [(a, ts) for _c, a, ts in v.commands()]
    assert acts[:3] == [(None, [A, B]), ("delete_markets", [B]),
                        ("add_markets", [B])]
    # B ends CURRENT with the venue's book (the re-add's own snapshot or a
    # get_snapshot after its ok), never from the pre-delete one
    fin = seen["final"]
    assert fin[B]["ok"] and fin[A]["ok"]
    assert _code_book(fin[B]) == v.truth[B]
    assert books.stats["snapshots_not_held"] >= 1
    assert books.stats["gaps"] == 0
    assert len(v.sent) <= 4


# ── non-blocking findings taken ──────────────────────────────────────────

@pytest.mark.parametrize("code", [10, 25])
def test_a_terminal_error_without_a_sid_ends_the_session(code):
    """errorResponsePayload: sid "Present when the error is scoped to a
    subscription". A 10 / 25 without one used to leave every book CURRENT
    and the session running; the connection holds ONE subscription, so it
    ends (fail closed), every book GAP."""
    v = MemberVenue()
    steps = ["process", "deliver", "deliver",
             lambda v_: v_.out.append({"type": "error", "id": 1,
                                       "msg": {"code": code, "msg": "x"}}),
             "deliver", "deliver"]
    sub, books, seen = run(v, [A], steps)
    assert isinstance(seen["ended"], KWS.SubscriptionEnded), seen["ended"]
    assert sub.last_end_reason == KWS.R_SUBSCRIPTION_ENDED
    assert not books.current(A)["ok"]


def test_a_rate_refused_readd_is_retried_later_and_bounded():
    """Error 27 (the per-subscription command rate; the values are not
    published): the add and its one immediate re-add refused leave the
    market GAP; it is added again RATE_LIMIT_RETRY_S later (not inside the
    same rate window), at most MAX_RATE_LIMIT_RETRIES times a session."""
    t = [NOW]
    v = MemberVenue(refuse27=2)
    want = [A]

    def later(v_):
        t[0] += KWS.RATE_LIMIT_RETRY_S
    steps = ["process", "deliver", "deliver",
             lambda v_: want.append(B), "idle",       # add B
             "process", "deliver",                    # 27 (a false gap:
             # the ambiguous slot, A asked for again) -> the re-add
             "process", "process", "deliver",         # re-add: 27 again
             "deliver",                               # A's fresh snapshot
             "idle", "idle",                          # nothing inside 30 s
             later, "idle",                           # the retry
             "process", "deliver", "deliver",
             # (a quiet sid: its `ok` is again a slot a separate counter
             # could hold -- the guard's documented false gap, repaired by
             # one get_snapshot)
             "process",
             lambda v_: v_.steps.extendleft(["deliver"] * len(v_.out))]
    sub, books, seen = run(v, want, steps, clock=lambda: t[0])
    assert seen["violations"] == []
    adds = [ts for _c, a, ts in v.commands() if a == "add_markets"]
    assert adds == [[B], [B], [B]]
    assert seen["final"][B]["ok"] and seen["final"][A]["ok"]
    # bounded: a venue that refuses every add for the rate
    t2 = [NOW]
    v2 = MemberVenue(refuse27=99)
    want2 = [A]
    steps2 = ["process", "deliver", "deliver",
              lambda v_: want2.append(B), "idle", "process", "deliver",
              "process", "deliver"]
    for _ in range(KWS.MAX_RATE_LIMIT_RETRIES + 2):
        steps2 += [lambda v_: t2.__setitem__(0, t2[0] +
                                             KWS.RATE_LIMIT_RETRY_S),
                   "idle", lambda v_: v_.inq and v_.process(),
                   lambda v_: v_.out and v_.steps.appendleft("deliver")]
    sub2, books2, seen2 = run(v2, want2, steps2, clock=lambda: t2[0])
    adds2 = [ts for _c, a, ts in v2.commands() if a == "add_markets"]
    assert len(adds2) == 2 + KWS.MAX_RATE_LIMIT_RETRIES
    assert seen2["final"][B]["why_raw"] == KWS.R_NOT_HELD_BY_VENUE
    assert not seen2["final"][B]["ok"]


def test_a_gap_while_an_add_is_unanswered_and_its_refused_readd_still_converge():
    """B's add is executed by the venue but its `ok` is lost: the next frame
    (B's own snapshot) is a gap, B is re-added (the lost frame may be the
    reply) and that re-add is refused for the command rate, and so is the
    one re-add a refusal earns. The gap re-add takes the unanswered add
    over -- B is refused, not left waiting for ever on the lost reply --
    and the retry RATE_LIMIT_RETRY_S later is "no action" at the venue (it
    already holds B), so nothing waits for the retry's own snapshot: the
    retry's `ok` asks for one. B ends CURRENT with the venue's book; never
    CURRENT before."""
    t = [NOW]
    v = MemberVenue(refuse27_at={2, 3})
    want = [A]
    steps = ["process", "deliver", "deliver",
             lambda v_: want.append(B), "idle",       # add B
             "process", "lose",                       # its ok lost
             "deliver",                               # B's snap: the gap
             "process", "process", "deliver",         # gap re-add: 27
             "deliver",                               # A's fresh snapshot
             "process", "deliver",                    # refusal re-add: 27
             "idle", "idle",                          # nothing inside 30 s
             lambda v_: t.__setitem__(0, t[0] + KWS.RATE_LIMIT_RETRY_S),
             "idle",                                  # the retry
             "process", "deliver",                    # ok: no action
             "process", "deliver",                    # B's get_snapshot
             lambda v_: v_.activity(B, "0.47", "2"), "deliver"]
    sub, books, seen = run(v, want, steps, clock=lambda: t[0])
    assert seen["violations"] == [], seen["violations"][:2]
    adds = [ts for _c, a, ts in v.commands() if a == "add_markets"]
    assert adds == [[B], [B], [B], [B]]
    assert _snapshot_requests(v)[-1] == [B]
    assert seen["final"][B]["ok"] and seen["final"][A]["ok"]
    assert _code_book(seen["final"][B]) == v.truth[B]


def test_an_add_unanswered_past_the_reply_timeout_is_added_again_then_bounded():
    """The venue takes B but the `ok` is lost, and the venue then holds
    nothing that could send a frame revealing the loss (A dropped). No gap
    ever comes: REPLY_TIMEOUT_S after the add the reply is taken as lost and
    B is added again (no action at the venue; its `ok` lists B, a
    get_snapshot follows). With the re-adds spent (every reply lost) the
    session ends by name instead of waiting for ever."""
    t = [NOW]
    v = MemberVenue()
    want = [B]
    later = (lambda v_: t.__setitem__(0, t[0] + KWS.REPLY_TIMEOUT_S))
    steps = ["process", "deliver", "deliver",        # subscribed, B's snap
             lambda v_: (want.remove(B), v_.sub.forget([B]),
                         want.append(A)), "idle",
             "process", "process",                    # delete B: ok; add A
             "deliver", "lose",                       # ok(delete); ok(A) lost
             "lose",                                  # A's own snap lost too
             later, "idle",                           # the reply timeout
             "process", "deliver",                    # re-add: ok, no action
             "process", "deliver",                    # A's get_snapshot
             lambda v_: v_.activity(A, "0.44", "6"), "deliver"]
    sub, books, seen = run(v, want, steps, clock=lambda: t[0])
    assert seen["violations"] == [], seen["violations"][:2]
    acts = [(a, ts) for _c, a, ts in v.commands()]
    assert acts == [(None, [B]), ("delete_markets", [B]),
                    ("add_markets", [A]), ("add_markets", [A]),
                    ("get_snapshot", [A])], acts
    assert seen["final"][A]["ok"]
    assert _code_book(seen["final"][A]) == v.truth[A]
    # every reply lost: the re-adds spent, the session ends by name
    t2 = [NOW]
    v2 = MemberVenue()
    want2 = [A]
    later2 = (lambda v_: t2.__setitem__(0, t2[0] + KWS.REPLY_TIMEOUT_S))
    steps2 = ["process", "deliver", "deliver",
              lambda v_: want2.append(B), "idle",     # add B
              "process", "lose", "lose"]              # its ok, its snap
    for _ in range(KWS.MAX_READDS_PER_MARKET + 1):
        steps2 += [later2, "idle", "process", "lose"]
    sub2, books2, seen2 = run(v2, want2, steps2, clock=lambda: t2[0])
    assert isinstance(seen2["ended"], KWS.ReplyTimeout), seen2["ended"]
    assert sub2.last_end_reason == KWS.R_REPLY_TIMEOUT
    adds2 = [ts for _c, a, ts in v2.commands() if a == "add_markets"]
    assert adds2 == [[B]] * (1 + KWS.MAX_READDS_PER_MARKET)
    assert not seen2["final"][B]["ok"]


def test_a_long_session_resets_the_reconnect_backoff(monkeypatch):
    """A session only ever ends by raising, so the backoff index never went
    back to its first step: after six ends in the life of the process every
    reconnect waited the last step. A session that lasted
    BACKOFF_RESET_AFTER_S starts the next wait from the first step."""
    clock = [NOW]
    waits = []
    lengths = [0.0, 0.0, 0.0, KWS.BACKOFF_RESET_AFTER_S, 0.0]
    stop = {}

    class Books(KWS.WsBooks):
        pass
    sub = KWS.Subscriber(None, Books(clock=lambda: clock[0]), wanted=list,
                         clock=lambda: clock[0], backoff=(1, 2, 5, 10))

    async def session(**k):
        clock[0] += lengths.pop(0)
        if not lengths:
            stop["e"].set()
        raise ConnectionError("dropped")
    sub.session = session

    async def sleep(s):
        waits.append(s)
    monkeypatch.setattr(KWS, "asyncio", type("A", (), {
        "sleep": staticmethod(sleep),
        "CancelledError": asyncio.CancelledError}))

    async def go():
        stop["e"] = asyncio.Event()
        await sub.run(stop=stop["e"])
    asyncio.run(go())
    assert waits == [1, 2, 5, 1, 2]
