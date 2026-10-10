"""RC6.2 REVIEW, ROUND 4: NO ERROR REPLY IS EVIDENCE THAT THE COMMAND DID NOT
RUN (sportsassets.kalshi_ws module docstring, VENUE MEMBERSHIP; WsBooks.
_settle, _unknown_effect).

THE FINDING (round-3 protocol review, blocking). Round 3 read an error
answering a `delete_markets` as "the venue is as it was": the market stayed
held. Error 18 is "Server timed out while processing command" -- the delete
may well have RUN. Then, with the market wanted again, the client asked for
its snapshot by get_snapshot (answered for ANY ticker, "without modifying the
subscription"), took the venue's reply as the book of a market the venue no
longer streamed, and served it CURRENT: the market moved and no delta ever
followed. The proof's venue refused deletes only by error 27 (a command the
venue does not run), so it could not find it; here the venue answers by an
error AFTER executing the command (`ErrExec`, run=True), and the acceptance
model's venue does the same (tests/test_rc6_kalshi_ws_acceptance_model.py:
`err_exec_*`).

Each test below drives the real Subscriber against the documented venue
(tests/test_rc62_kalshi_ws_venue_membership.MemberVenue, one counter per sid,
the sid busy enough that no control frame is the separate-counter guard's
documented false gap) and checks, before every step, that every CURRENT book
equals the venue's TRUE book as of the highest seq delivered (STALE_CURRENT
otherwise). On fde50aff the executed-delete tests record STALE_CURRENT for the
re-wanted market and the executed-add test leaves a market the venue streams
GAP for the rest of the session; here they pass.

The rule (module docstring): an error says NOTHING about membership. A market
whose delete (or add) was answered by an error is UNSURE -- not held, so no
snapshot of it is applied and no get_snapshot names it -- until a reply
carrying the full ticker list settles it; wanted again it is added ALONE (an
add of a held market is "no action", answered by an `ok` listing it; of one
not held, it takes it) and the confirming `ok` asks for its snapshot.

SMALL LIVE = SHADOW: market data only."""
from __future__ import annotations

import pytest

from sportsassets import kalshi_ws as KWS
from tests import test_rc62_kalshi_ws_venue_membership as VM

A, B, C = VM.A, VM.B, "KXM-C"
NOW = VM.NOW


class ErrExec(VM.MemberVenue):
    """MemberVenue that answers the first `errors[action]` commands of an
    action by `code` (18, the server's timeout, unless a test says) INSTEAD of
    its `ok` -- after EXECUTING the command (`run=True`: the venue took the
    market out or in; a get_snapshot's snapshots still follow) or without
    (`run=False`: it refused it unprocessed, as error 27 is read to mean).
    The reply is scoped ((sid, seq), the sid's next number) unless
    `scoped=False`. `snap_first`: an add's snapshots are sent BEFORE its
    reply (the order is not documented; MemberVenue sends the `ok` first)."""

    def __init__(self, errors, *, code=18, run=True, snap_first=False, **kw):
        super().__init__(**kw)
        self.errors = dict(errors)
        self.code, self.run, self.snap_first = code, run, snap_first
        self._as_error = False
        self.errored = []                   # (action, tickers) answered so

    def process(self):
        if self.inq:
            m = self.inq[0]
            p = m.get("params") or {}
            action = p.get("action")
            if m["cmd"] == "update_subscription" and self.errors.get(action):
                self.errors[action] -= 1
                self.errored.append((action,
                                     list(p.get("market_tickers") or ())))
                if not self.run:
                    self.inq.popleft()
                    self.out.append(self._err(m["id"], self.code))
                    return
                if action == "add_markets" and self.snap_first:
                    # the market's own snapshot leaves before the error
                    self.inq.popleft()
                    new = [t for t in (p.get("market_tickers") or ())
                           if t not in self.markets]
                    self.markets += new
                    self.out.extend(self._snap(t) for t in new)
                    self.out.append(self._err(m["id"], self.code))
                    return
                self._as_error = True
                try:
                    super().process()
                finally:
                    self._as_error = False
                if action == "get_snapshot":
                    # the snapshots went out; the reply is an error
                    self.out.append(self._err(m["id"], self.code))
                return
        super().process()

    def _ok(self, cid, listed=True):
        if self._as_error:
            return self._err(cid, self.code)
        return super()._ok(cid, listed)


def drain(v_):
    """The venue processes every command sent so far, then every frame it
    produced is read (in order)."""
    while v_.inq:
        v_.process()
    v_.steps.extendleft(["deliver"] * len(v_.out))


def busy(n=6):
    """subscribe [A, C]: `subscribed`, A (seq 1), C (seq 2); then A moves n
    times: the sid has carried more frames than the separate-counter bound
    (the subscribe's reply counted) when any reply comes."""
    steps = ["process", "deliver", "deliver", "deliver"]
    for k in range(n):
        steps += [lambda v_, k=k: v_.activity(A, "0.%02d" % (50 + k),
                                              str(2 + k)), "deliver"]
    return steps


def drop(want, t):
    return lambda v_: (want.remove(t), v_.sub.forget([t]))


def rewant(want, t):
    return lambda v_: want.append(t)


SETTLE = [drain, "idle"] * 4


def moves():
    """C and A move at the venue (C streamed only if the venue holds it)."""
    return [lambda v_: v_.activity(C, "0.47", "9"),
            lambda v_: v_.activity(A, "0.49", "1"), drain, "idle", drain,
            "idle"]


def probe(into, t=C):
    """A step recording the membership state while the session is live (the
    books drop their subscriptions when the session ends)."""
    def step(v_):
        b = v_.sub.books
        into.update(unsure=set(b.anchors[1]["unsure"]), held=b.held(1, t),
                    may_hold=getattr(b, "may_hold", b.held)(1, t))
    return step


def acts_of(v):
    return [(a, ts) for _c, a, ts in v.commands()]


# ── the finding: an errored delete that RAN, the market wanted again ────────

@pytest.mark.parametrize("scoped", [True, False])
def test_a_delete_answered_by_error_18_after_it_ran_never_serves_a_stale_book(
        scoped):
    """The review's repro. subscribe [A, C]; six A deltas; C dropped -> the
    client sends delete_markets [C]; the venue RUNS it and answers error 18;
    C is wanted again. fde50aff took C as still held and sent get_snapshot
    [C]; the venue answered with a snapshot of C, which it no longer held:
    C went CURRENT and moved without a delta (STALE_CURRENT). Now the client
    adds C ALONE: the venue takes it (`ok` lists it, its own snapshot
    follows) and C ends CURRENT with the venue's book, still streamed."""
    v = ErrExec({"delete_markets": 1}, scoped=scoped)
    want = [A, C]
    state = {}
    steps = busy() + [drop(want, C), "idle", drain, rewant(want, C),
                      "idle"] + SETTLE + moves() + [probe(state)]
    sub, books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:3]
    assert v.errored == [("delete_markets", [C])]
    acts = acts_of(v)
    # the add precedes any get_snapshot naming C: C is never asked for as a
    # market the venue is taken to hold
    assert ("add_markets", [C]) in acts, acts
    first_add = acts.index(("add_markets", [C]))
    assert ("get_snapshot", [C]) not in acts[:first_add], acts
    assert v.markets == [A, C]
    fin = seen["final"]
    assert fin[A]["ok"] and fin[C]["ok"], {t: fin[t]["state_raw"] for t in fin}
    assert VM._code_book(fin[C]) == v.truth[C]
    assert books.stats["gaps"] == 0
    # settled by the `ok` listing C
    assert state == {"unsure": set(), "held": True, "may_hold": True}, state


def test_a_delete_answered_by_error_27_is_not_read_as_not_run_either():
    """Error 27 ("exceeded its command rate limit") is no more a proof that
    the command did not run than error 18 is: the client does not rely on it.
    Here the venue did NOT run the delete (refused it unprocessed) and still
    holds C: the add is "no action", its `ok` lists C, and the confirmation
    asks for the snapshot -- C CURRENT with the venue's book."""
    v = ErrExec({"delete_markets": 1}, code=27, run=False)
    want = [A, C]
    steps = busy() + [drop(want, C), "idle", drain, rewant(want, C),
                      "idle"] + SETTLE + moves()
    sub, books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:3]
    assert v.errored == [("delete_markets", [C])]
    assert acts_of(v) == [(None, [A, C]), ("delete_markets", [C]),
                          ("add_markets", [C]), ("get_snapshot", [C])]
    fin = seen["final"]
    assert fin[A]["ok"] and fin[C]["ok"]
    assert VM._code_book(fin[C]) == v.truth[C]


@pytest.mark.parametrize("run", [True, False])
def test_a_market_wanted_again_before_its_delete_error_arrives_is_never_stale(
        run):
    """C is dropped (delete sent) and wanted again BEFORE the venue answers
    (the add is sent behind the delete). The delete is answered by an error
    -- it ran (run=True) or not -- so the add's own snapshot may or may not
    follow: it is not waited for. Either way C ends CURRENT with the venue's
    book, and the venue's true state is never contradicted before then."""
    v = ErrExec({"delete_markets": 1}, run=run, code=18 if run else 27)
    want = [A, C]
    steps = busy() + [drop(want, C), "idle", rewant(want, C), "idle"] \
        + SETTLE + moves()
    sub, books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:3]
    fin = seen["final"]
    assert fin[A]["ok"] and fin[C]["ok"], {t: fin[t]["state_raw"] for t in fin}
    assert VM._code_book(fin[C]) == v.truth[C]
    assert v.markets == [A, C]


def test_an_unsure_market_is_not_asked_for_by_get_snapshot_while_it_is_wanted():
    """No get_snapshot names C between the errored delete and the `ok` that
    lists it, however the wanted set moves: here C is dropped again right
    after it was added (a second delete, which the venue answers by `ok`)."""
    v = ErrExec({"delete_markets": 1})
    want = [A, C]
    steps = busy() + [drop(want, C), "idle", drain, rewant(want, C),
                      "idle", drain, drop(want, C), "idle"] + SETTLE + [
        rewant(want, C), "idle"] + SETTLE + moves()
    sub, books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:3]
    fin = seen["final"]
    assert fin[A]["ok"] and fin[C]["ok"]
    assert VM._code_book(fin[C]) == v.truth[C]


# ── a market still dropped: the delete is sent again, bounded ─────────────

def test_an_errored_delete_of_a_market_still_dropped_is_sent_again_bounded():
    """C stays dropped. The delete ran (error 18): the retry
    RATE_LIMIT_RETRY_S later is answered by `ok` -- C is not held, nothing is
    sent again. A venue answering every delete by an error gets the first and
    MAX_RATE_LIMIT_RETRIES more, never a loop."""
    t = [NOW]
    v = ErrExec({"delete_markets": 1})
    want = [A, C]
    state = {}
    steps = busy() + [drop(want, C), "idle", drain, "idle", "idle",
                      lambda v_: t.__setitem__(0, t[0] + KWS.RATE_LIMIT_RETRY_S),
                      "idle"] + SETTLE + [lambda v_: v_.activity(C, "0.47", "1"),
                                          lambda v_: v_.activity(A, "0.49", "1"),
                                          drain, "idle", probe(state)]
    sub, books, seen = VM.run(v, want, steps, clock=lambda: t[0])
    assert seen["violations"] == [], seen["violations"][:3]
    dels = [ts for a, ts in acts_of(v) if a == "delete_markets"]
    assert dels == [[C], [C]], dels
    assert v.markets == [A]
    assert sub.next_due() is None
    assert state == {"unsure": set(), "held": False, "may_hold": False}, state
    # bounded: every delete errors
    t2 = [NOW]
    v2 = ErrExec({"delete_markets": 99})
    want2 = [A, C]
    steps2 = busy() + [drop(want2, C), "idle", drain]
    for _ in range(KWS.MAX_RATE_LIMIT_RETRIES + 2):
        steps2 += [lambda v_: t2.__setitem__(0, t2[0] + KWS.RATE_LIMIT_RETRY_S),
                   "idle", drain]
    sub2, books2, seen2 = VM.run(v2, want2, steps2, clock=lambda: t2[0])
    assert seen2["violations"] == []
    dels2 = [ts for a, ts in acts_of(v2) if a == "delete_markets"]
    assert dels2 == [[C]] * (1 + KWS.MAX_RATE_LIMIT_RETRIES)
    assert sub2.next_due() is None


# ── an errored ADD that ran ───────────────────────────────────────────────

@pytest.mark.parametrize("snap_first", [True, False])
@pytest.mark.parametrize("scoped", [True, False])
def test_an_add_answered_by_error_18_after_it_ran_ends_current(scoped,
                                                                snap_first):
    """The liveness half of the same rule (found by the same review, listed
    non-blocking): C is added, the venue TAKES it and answers error 18. On
    fde50aff C was refused, its re-add was "no action" (the `ok` lists it),
    and the client waited for the add's own snapshot -- which the venue had
    sent before -- so the book stayed GAP for the rest of the session while
    the venue streamed it. Now an errored add leaves C unsure: no snapshot
    of it is applied before a reply lists it, it is re-added alone, and the
    confirming `ok` asks for a snapshot. C ends CURRENT with the venue's
    book and keeps following its deltas."""
    v = ErrExec({"add_markets": 1}, scoped=scoped, snap_first=snap_first)
    want = [A]
    state = {}
    steps = ["process", "deliver", "deliver"] + [
        s for k in range(6) for s in (
            lambda v_, k=k: v_.activity(A, "0.%02d" % (50 + k), str(2 + k)),
            "deliver")] + [rewant(want, C), "idle"] + SETTLE + [
        lambda v_: v_.activity(C, "0.47", "9"),
        lambda v_: v_.activity(A, "0.49", "1"), drain, "idle", drain, "idle",
        probe(state)]
    sub, books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:3]
    assert v.errored == [("add_markets", [C])]
    assert v.markets == [A, C]
    fin = seen["final"]
    assert fin[A]["ok"] and fin[C]["ok"], {t: fin[t]["state_raw"] for t in fin}
    assert VM._code_book(fin[C]) == v.truth[C]
    # the errored add, its ONE re-add (alone), the confirmation's snapshot
    assert [a for a, _ts in acts_of(v)] == [None, "add_markets",
                                            "add_markets", "get_snapshot"]
    assert state["unsure"] == set() and state["held"], state


# ── WsBooks alone: membership after an error ──────────────────────────────

def _acked(tickers=(A, B)):
    """WsBooks as the Subscriber leaves it on the `subscribed` answering its
    subscribe (command 1) as sid 1, every market CURRENT (seqs 1..n)."""
    b = KWS.WsBooks(clock=lambda: NOW)
    b.want(list(tickers))
    b.on_connected()
    b.bind(1, list(tickers), ours=True)
    b.hold_subscribed(1, list(tickers))
    assert b.on_message(VM.m_subscribed(1, 1)) == "SUBSCRIBED"
    for k, t in enumerate(tickers):
        assert b.on_message(_snap(k + 1, t)) == "SNAPSHOT"
    return b


def _snap(seq, t, px="0.40"):
    return {"type": VM.SNAP, "sid": 1, "seq": seq,
            "msg": {"market_ticker": t, "yes_dollars_fp": [[px, "10"]],
                    "no_dollars_fp": []}}


def _err(cid, seq, code=18, sid=1):
    m = {"type": "error", "id": cid, "msg": {"code": code, "msg": "e"}}
    if seq is not None:
        m.update(sid=sid, seq=seq)
    return m


def _ok(cid, seq, tickers, sid=1):
    return {"type": "ok", "id": cid, "sid": sid, "seq": seq,
            "msg": {"market_tickers": list(tickers)}}


@pytest.mark.parametrize("code", [18, 27, 26, 1, 99])
def test_no_error_code_leaves_the_market_held_after_its_delete(code):
    """WsBooks alone, any code: B dropped, its delete answered by an error --
    B is not held and unsure, a snapshot of B that follows (get_snapshot
    answers for any ticker) is in the sequence and never applied, and only a
    reply LISTING B holds it again; the snapshot after that is applied."""
    b = _acked()
    b.forget([B])
    b.command_sent(1, 5, "delete_markets", [B])
    assert not b.held(1, B)                 # an unanswered delete
    assert b.on_message(_err(5, 3, code)) in ("ERROR", "GAP")
    b.fatal = None
    assert not b.held(1, B) and b.may_hold(1, B)
    assert B in b.anchors[1]["unsure"]
    assert b.stats["membership_unknown_after_error"] == 1
    # wanted again: the Subscriber adds it alone
    b.want([B])
    b.bind(1, [B])
    b.command_sent(1, 6, "add_markets", [B])
    top = b.sid_seq[1]
    # a snapshot of B in the sequence while the add is unanswered
    assert b.on_message(_snap(top + 1, B, "0.44")) == "IGNORED_NOT_HELD"
    assert not b.current(B)["ok"]
    assert b.on_message(_ok(6, top + 2, [A, B])) in ("OK", "GAP")
    b.fatal = None
    assert b.held(1, B) and not b.anchors[1]["unsure"]
    assert B in b.recover.get(1, set()) or b.current(B)["ok"] or \
        B in b.anchors[1]["awaiting"]


def test_an_older_ok_never_overrides_what_an_errored_delete_left_unknown():
    """A reply to an EARLIER command that arrives after the delete's error
    speaks for its own command id: it cannot make B held again. (Replies
    are answered in command order by C2; a replay or a late copy must not
    re-hold B.)"""
    b = _acked()
    b.forget([B])
    b.command_sent(1, 5, "delete_markets", [B])
    b.command_sent(1, 4, "add_markets", [A])       # (an older id, sent later)
    assert b.on_message(_err(5, 3)) in ("ERROR", "GAP")
    b.fatal = None
    assert not b.held(1, B)
    b.on_message(_ok(4, 4, [A, B]))
    b.fatal = None
    assert not b.held(1, B)                 # id 4 < 5: the error stands


def test_an_errored_add_of_a_held_market_leaves_it_held():
    """An add never removes a market: a re-add of B that the venue answered
    by an error leaves B as the venue's earlier `ok` said (held), and never
    unsure."""
    b = _acked()
    b.command_sent(1, 5, "add_markets", [B])
    assert b.on_message(_err(5, 3)) in ("ERROR", "GAP")
    b.fatal = None
    assert b.held(1, B)
    assert B not in b.anchors[1]["unsure"]


def test_an_errored_add_of_a_market_not_held_makes_it_unsure_not_held():
    b = _acked([A])
    b.want([B])
    b.bind(1, [B])
    b.command_sent(1, 5, "add_markets", [B])
    assert b.on_message(_err(5, 2)) in ("ERROR", "GAP")
    b.fatal = None
    assert not b.held(1, B) and b.unsure(1, B)
    assert b.refused.get(1, {}).get(B) == 18
