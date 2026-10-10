"""RC6.2 REVIEW, ROUND 3: THE CONTROL-FRAME SEQUENCE RULE AND A REFUSED
DELETE (sportsassets.kalshi_ws module docstring, THE SEQUENCE RULE FOR
CONTROL FRAMES and VENUE MEMBERSHIP).

Each finding of the third review, reproduced against the real WsBooks and
the real Subscriber.session() playing a venue step by step
(tests/test_rc62_kalshi_ws_venue_membership.MemberVenue: one counter per
sid unless a test says otherwise); before every step every CURRENT book must
equal the venue's TRUE book as of the highest data seq delivered.

  * BLOCKING, the separate-counter bound one short. Assumption C1 lets a
    separate control counter number the subscription's own `subscribed`
    and at most CONTROL_SEQ_SLACK unasked frames; the bound used to be
    j + CONTROL_SEQ_SLACK. A counter that numbered `subscribed` (1) and an
    unasked scoped error (2) put that error on the shared slot after A's
    snapshot (data seq 1): it was consumed, the next delta was lost and
    the one after it applied -- A CURRENT without the lost level. The bound
    is now 1 + j + CONTROL_SEQ_SLACK (an unknown id: every update command).
  * a REPLAYED control frame (documented shared counter) switched the sid
    to SEPARATE without a gap, after which a loss right before the next
    control frame went unseen; it is now a replay (a gap while a book is
    CURRENT), never a switch; and a control frame above the bound on a
    SEPARATE sid proves it shared after all.
  * a delete the venue REFUSED (error 27 is documented for any command on
    the subscription) left the market held: wanted again, its add was "no
    action" and the book waited for ever. It is asked for by get_snapshot
    now; a market still dropped is deleted again, bounded.
  * a separate counter that numbered `subscribed`: the documented false
    gap at the first `ok`, then the add's own snapshot at the next DATA seq
    was a second gap while the get_snapshot was outstanding -- the session
    ended. The data run continuing over the control frame's slot proves the
    separate counter: no second gap.
  * two liveness holes the extended model seeds found (both fail on
    355d7d2a with a book left waiting while the venue streams it): a market
    the venue may hold already (its add's reply lost) refused with another
    market by one error 26 is re-added alone; a market dropped and wanted
    again while its add was in flight is asked for by get_snapshot when the
    reply confirms it.
  * the guard's documented costs, pinned so they stay bounded: a two-market
    quiet sid's first `ok` is a false gap repaired by one get_snapshot; a
    quiet sid whose refusals carry no (sid, seq) can reach a second
    ambiguous reply while that get_snapshot is outstanding -- the session
    ends by name, nothing is ever served stale.

SMALL LIVE = SHADOW: market data only."""
from __future__ import annotations

import json
from decimal import Decimal

from sportsassets import kalshi_ws as KWS
from tests import test_rc62_kalshi_ws_venue_membership as VM

NOW = VM.NOW
A, B, C = VM.A, VM.B, "KXM-C"
SNAP, DELTA = VM.SNAP, VM.DELTA


def snap(seq, t=A, px="0.40", sid=1):
    return {"type": SNAP, "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "yes_dollars_fp": [[px, "10"]],
                    "no_dollars_fp": []}}


def delta(seq, t=A, px="0.41", d="5", sid=1):
    return {"type": DELTA, "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "price_dollars": px, "delta_fp": d,
                    "side": "yes"}}


def err(seq, code=18, cid=None, sid=1):
    m = {"type": "error", "sid": sid, "seq": seq,
         "msg": {"code": code, "msg": "e"}}
    if cid is not None:
        m["id"] = cid
    return m


def ok(cid, seq, tickers=None, sid=1):
    m = {"type": "ok", "id": cid, "sid": sid, "seq": seq}
    if tickers is not None:
        m["msg"] = {"market_tickers": list(tickers)}
    return m


def acked(tickers=(A,)):
    """WsBooks as the Subscriber leaves it on the `subscribed` answering
    its subscribe (command 1) as sid 1."""
    b = KWS.WsBooks(clock=lambda: NOW)
    b.want(list(tickers))
    b.on_connected()
    b.bind(1, list(tickers), ours=True)
    b.hold_subscribed(1, list(tickers))
    assert b.on_message(VM.m_subscribed(1, 1)) == "SUBSCRIBED"
    return b


def book(b, t=A):
    cur = b.current(t)
    return VM._code_book(cur) if cur["ok"] else None


class SepVenue(VM.MemberVenue):
    """MemberVenue whose control frames (`ok`, scoped errors) carry a
    SEPARATE per-sid counter that also numbered `subscribed` (the case C1
    allows: the subscription's own reply may take a number), and which may
    send ONE scoped non-terminal error unasked (C1: at most
    CONTROL_SEQ_SLACK)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.cseq = 0

    def _cseq(self):
        self.cseq += 1
        # (harness) a control frame's seq is not a data seq: the truth is
        # looked up by data seq only (see recv)
        self.truth_at.setdefault(self.cseq, None)
        return self.cseq

    def process(self):
        if self.inq and self.inq[0]["cmd"] == "subscribe":
            self.cseq += 1                  # `subscribed` took number 1
        super().process()

    def _ok(self, cid, listed=True):
        m = {"type": "ok", "id": cid, "sid": 1, "seq": self._cseq()}
        if listed:
            m["msg"] = {"market_tickers": list(self.markets)}
        return m

    def _err(self, cid, code):
        m = {"type": "error", "id": cid,
             "msg": {"code": code, "msg": "error %d" % code}}
        if self.scoped:
            m.update(sid=1, seq=self._cseq())
        return m

    def unasked(self, code=18):
        self.out.append({"type": "error", "sid": 1, "seq": self._cseq(),
                         "msg": {"code": code, "msg": "unasked"}})

    async def recv(self):
        before = self.delivered_truth
        raw = await super().recv()
        if json.loads(raw).get("type") not in (SNAP, DELTA):
            self.delivered_truth = before
        return raw


# ── blocking: the bound counts the subscribe's own reply ────────────────

def test_an_unasked_frame_after_a_numbered_subscribed_never_hides_a_lost_delta():
    """The review's repro, WsBooks as the Subscriber drives it: A's snapshot
    (data seq 1), then an unasked scoped error carrying control seq 2 (the
    separate counter numbered `subscribed` 1) -- exactly last + 1. No update
    command sent: a separate counter may be at 1 + 0 + CONTROL_SEQ_SLACK = 2
    here, and A is CURRENT: a gap (it used to be consumed, and the delta at
    3 then applied without the lost one at 2)."""
    assert KWS.CONTROL_SEQ_SLACK == 1
    b = acked()
    assert b.on_message(snap(1)) == "SNAPSHOT"
    assert b.on_message(err(2)) == "GAP"
    assert b.books[A]["why"] == KWS.R_CONTROL_SEQUENCE_AMBIGUOUS
    assert b.on_message(delta(3, px="0.42", d="7")) == "IGNORED_NOT_CURRENT"
    assert not b.current(A)["ok"] and b.recover[1] == {A}
    # only a snapshot in sequence after the baseline restores it
    assert b.on_message(snap(4, px="0.45")) == "SNAPSHOT"
    assert book(b) == {"yes": {Decimal("0.45"): Decimal(10)}, "no": {}}
    # one past the bound the same frame can only be the shared slot
    b2 = acked()
    assert b2.on_message(snap(1)) == "SNAPSHOT"
    assert b2.on_message(delta(2)) == "DELTA"
    assert b2.on_message(err(3)) == "ERROR"           # 3 > 2: consumed
    assert b2.on_message(delta(4, px="0.42", d="7")) == "DELTA"
    assert b2.current(A)["ok"] and b2.stats["gaps"] == 0


def test_the_subscriber_never_serves_the_lost_delta_behind_an_unasked_frame():
    """The same against the real Subscriber and a venue whose separate
    control counter numbered `subscribed`: the unasked frame gaps the sid,
    the lost delta is never hidden, ONE get_snapshot repairs it and A ends
    CURRENT with the venue's book."""
    v = SepVenue()
    steps = ["process", "deliver", "deliver",          # subscribed, A (1)
             lambda v_: v_.unasked(), "deliver",        # control seq 2
             lambda v_: v_.activity(A, "0.41", "5"), "lose",    # data 2
             lambda v_: v_.activity(A, "0.42", "7"), "deliver",  # data 3
             "process", "deliver",                      # the get_snapshot
             lambda v_: v_.activity(A, "0.43", "1"), "deliver"]
    sub, books, seen = VM.run(v, [A], steps)
    assert seen["violations"] == [], seen["violations"][:2]
    assert books.stats["gaps"] == 1 and books.stats["control_consumed"] == 0
    assert [a for _c, a, _t in v.commands()] == [None, "get_snapshot"]
    assert seen["final"][A]["ok"]
    assert VM._code_book(seen["final"][A]) == v.truth[A]


def test_an_unknown_ids_bound_counts_every_update_command_sent():
    """A control frame answering no command we know (here: none at all) is
    bounded by EVERY update command sent on the sid -- not by none: with one
    add sent, a separate counter may be at 1 + 1 + 1 = 3, so a frame at 3
    on the shared slot with A CURRENT is a gap; at 4 it is consumed."""
    for top, expect in ((2, "GAP"), (3, "ERROR")):
        b = acked([A, B])
        assert b.on_message(snap(1)) == "SNAPSHOT"
        for s in range(2, top + 1):
            assert b.on_message(delta(s, px="0.4%d" % s)) == "DELTA"
        b.bind(1, [C])
        b.want([C])
        b.command_sent(1, 2, "add_markets", [C])
        assert b.on_message(err(top + 1)) == expect, top
        assert b.current(A)["ok"] == (expect == "ERROR")


# ── a replayed control frame is never proof of a separate counter ───────

def test_a_replayed_ok_is_a_gap_never_a_switch_to_separate():
    """Documented shared counter: our add's `ok` at seq 4 is consumed; the
    venue then replays it (seq 4 <= last). It used to switch the sid to
    SEPARATE (control frames never sequenced again); now a second reply to
    one command is a replay -- a gap while a book is CURRENT -- and the sid
    stays SHARED, so the next control frame still reveals a loss before
    it."""
    b = acked([A])
    for s, m in ((1, snap(1)), (2, delta(2)), (3, delta(3, px="0.42"))):
        assert b.on_message(m) in ("SNAPSHOT", "DELTA"), s
    b.want([B])
    b.bind(1, [B])
    b.command_sent(1, 2, "add_markets", [B])
    assert b.on_message(ok(2, 4, [A, B])) == "OK"
    assert b.on_message(snap(5, B)) == "SNAPSHOT"
    assert b.on_message(ok(2, 4, [A, B])) == "GAP"       # the replay
    assert b.anchors[1]["mode"] == KWS.SHARED
    assert b.stats["control_replayed"] == 1
    assert not b.current(A)["ok"] and not b.current(B)["ok"]
    assert b.books[A]["why"] == KWS.R_SEQ_GAP
    # the replay never lowered the baseline: the repair's snapshots follow 5
    assert b.on_message(snap(6, A)) == "SNAPSHOT"
    assert b.on_message(snap(7, B)) == "SNAPSHOT"
    # a later loss (seq 8) right before a control frame is seen AT it
    b.command_sent(1, 9, "delete_markets", [B])
    assert b.on_message(ok(9, 9, [A])) == "GAP"
    assert not b.current(A)["ok"]


def test_a_replay_above_the_bound_is_a_gap_and_with_nothing_current_counted():
    b = acked([A])
    for s in range(1, 6):
        assert b.on_message(snap(s) if s == 1 else delta(
            s, px="0.4%d" % s)) in ("SNAPSHOT", "DELTA")
    # seq 4 <= last 5, above the bound 1 + 0 + 1 = 2: a replay
    assert b.on_message(ok(77, 4)) == "GAP"
    assert b.anchors[1]["mode"] == KWS.SHARED and not b.current(A)["ok"]
    # nothing CURRENT now: the next replay is counted, nothing else
    assert b.on_message(ok(78, 3)) == "OK"
    assert b.stats["control_replayed"] == 2 and b.stats["gaps"] == 1
    assert b.anchors[1]["mode"] == KWS.SHARED


def test_the_replayed_ok_against_the_subscriber_hides_no_later_loss():
    """The review's repro end to end: the replay of our add's `ok`, then B
    dropped (delete sent), a delta of A lost, the delete's `ok` -- A was
    served CURRENT without the lost delta on a quiet sid. Now nothing is
    ever served that differs from the venue's book."""
    v = VM.MemberVenue()
    want = [A]
    steps = ["process", "deliver", "deliver",              # subscribed, A
             lambda v_: v_.activity(A, "0.41", "3"), "deliver",   # delta 2
             lambda v_: v_.activity(A, "0.42", "3"), "deliver",   # delta 3
             lambda v_: want.append(B), "idle",            # add B
             "process", "deliver", "deliver",               # ok 4, B snap 5
             lambda v_: v_.out.appendleft(v_.delivered[-2]), "deliver",
             "process", "deliver", "deliver",               # the repair
             lambda v_: (want.remove(B), v_.sub.forget([B])), "idle",
             lambda v_: v_.activity(A, "0.43", "9"), "lose",
             "process", "deliver",                          # ok(delete)
             "idle", "process", "deliver", "idle"]
    sub, books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:2]
    assert books.stats["control_replayed"] == 1
    assert books.stats["control_seq_separate"] == 0
    assert seen["final"][A]["ok"]
    assert VM._code_book(seen["final"][A]) == v.truth[A]


def test_a_control_frame_above_the_bound_on_a_separate_sid_makes_it_shared():
    """In SEPARATE mode control frames are not sequenced; one whose seq no
    separate counter reaches (C1 / C2) proves the shared counter after all:
    a gap (whatever was lost meanwhile cannot be known), and the sid is
    sequenced as SHARED again from its seq."""
    b = acked([A])
    for cid in (9, 10, 11):
        b.command_sent(1, cid)
    assert b.on_message(snap(1)) == "SNAPSHOT"
    assert b.on_message(delta(2)) == "DELTA"
    assert b.on_message(ok(9, 1)) == "OK"                 # SEPARATE
    assert b.anchors[1]["mode"] == KWS.SEPARATE
    assert b.on_message(ok(10, 2)) == "OK"                # 2 <= bound 4
    assert b.on_message(delta(3)) == "DELTA" and b.current(A)["ok"]
    assert b.on_message(ok(11, 50)) == "GAP"              # 50 > bound 5
    assert b.anchors[1]["mode"] == KWS.SHARED and b.sid_seq[1] == 50
    assert b.stats["control_seq_shared_again"] == 1
    assert not b.current(A)["ok"]
    assert b.on_message(snap(51, px="0.47")) == "SNAPSHOT"
    assert b.current(A)["ok"]


def test_on_a_separate_sid_a_control_frame_past_the_data_runs_next_slot_gaps():
    """In SEPARATE mode a control frame within the bound but past the last
    DATA seq + 1, with a book CURRENT, is what a shared counter shows after
    a lost frame (a separate counter only on a sid that carried fewer data
    frames than control frames): a gap -- a loss is never left unseen until
    the next data frame on a quiet sid."""
    b = acked([A])
    for cid in (9, 10):
        b.command_sent(1, cid)
    assert b.on_message(snap(1)) == "SNAPSHOT"
    assert b.on_message(delta(2)) == "DELTA"
    assert b.on_message(ok(9, 1)) == "OK"                 # SEPARATE
    # data 3 lost; the next control frame at 4 (bound 1 + 2 + 1 = 4)
    assert b.on_message(ok(10, 4)) == "GAP"
    assert b.books[A]["why"] == KWS.R_CONTROL_SEQUENCE_AMBIGUOUS
    assert b.anchors[1]["mode"] == KWS.SEPARATE


def test_a_frame_answering_no_command_we_know_at_or_below_the_last_seq_is_a_replay():
    """Only the FIRST reply to a command of ours, with no gap since it was
    sent, proves a separate counter: a frame answering no command we know
    (or after a gap since its command went out) at or below the last seq is
    a replay of the shared sequence -- a gap while a book is CURRENT, never
    a switch."""
    b = acked([A])
    assert b.on_message(snap(1)) == "SNAPSHOT"
    assert b.on_message(delta(2)) == "DELTA"
    assert b.on_message(ok(9, 1)) == "GAP"                # unknown id
    assert b.anchors[1]["mode"] == KWS.SHARED
    assert b.books[A]["why"] == KWS.R_SEQ_GAP
    # after a gap since our command went out: its first reply may be a
    # late copy of a lost one
    b2 = acked([A])
    b2.command_sent(1, 9)
    assert b2.on_message(snap(1)) == "SNAPSHOT"
    assert b2.on_message(delta(3)) == "GAP"               # 2 lost
    assert b2.on_message(snap(4, px="0.47")) == "SNAPSHOT"
    assert b2.on_message(ok(9, 2)) == "GAP"               # a late copy?
    assert b2.anchors[1]["mode"] == KWS.SHARED
    assert b2.stats["control_replayed"] == 1


# ── a refused delete ─────────────────────────────────────────────────────

class RefuseDeletes(VM.MemberVenue):
    """Refuses the first `n` delete_markets with a scoped error 27 (the
    per-subscription command rate; the venue keeps the market)."""

    def __init__(self, n=1, **kw):
        super().__init__(**kw)
        self.n = n
        self.refused_deletes = 0

    def process(self):
        if self.inq and (self.inq[0].get("params") or {}).get(
                "action") == "delete_markets" and self.n:
            m = self.inq.popleft()
            self.n -= 1
            self.refused_deletes += 1
            self.out.append(self._err(m["id"], 27))
            return
        super().process()


def _quiet_free(steps):
    """A moves twice before the scenario: the sid has carried more frames
    than the separate-counter bound when the replies come (not the guard's
    documented false gap, which is pinned on its own below)."""
    return ["process", "deliver", "deliver", "deliver",
            lambda v_: v_.activity(A, "0.43", "2"), "deliver",
            lambda v_: v_.activity(A, "0.44", "3"), "deliver"] + steps


def test_a_market_wanted_again_after_its_delete_was_refused_gets_its_snapshot():
    """The review's repro: B dropped, its delete refused (27, scoped), B
    wanted again. The venue holds B (an error leaves it as it was), so an
    add would be "no action" with no snapshot -- B waited AWAITING_SNAPSHOT
    for the rest of the session. Now its snapshot is asked for, and B ends
    CURRENT with the venue's book; never CURRENT before."""
    v = RefuseDeletes()
    want = [A, B]
    steps = _quiet_free([
        lambda v_: (want.remove(B), v_.sub.forget([B])), "idle",  # delete
        "process", "deliver",                          # error 27 (scoped)
        lambda v_: want.append(B), "idle",              # wanted again
        "process", "deliver",                           # B's get_snapshot
        lambda v_: v_.activity(B, "0.44", "6"), "deliver",
        lambda v_: v_.activity(A, "0.45", "2"), "deliver", "idle"])
    sub, books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:2]
    acts = [(a, ts) for _c, a, ts in v.commands()]
    assert acts == [(None, [A, B]), ("delete_markets", [B]),
                    ("get_snapshot", [B])], acts
    fin = seen["final"]
    assert fin[A]["ok"] and fin[B]["ok"]
    assert VM._code_book(fin[B]) == v.truth[B]
    assert books.stats["gaps"] == 0
    # B is the venue's again: dropped once more, it is deleted
    assert B in sub.on_venue


def test_a_refused_delete_of_a_market_still_dropped_is_sent_again_bounded():
    """Still dropped, the refused market is deleted again RATE_LIMIT_RETRY_S
    later (not inside the same rate window); a venue refusing every delete
    gets 1 + MAX_RATE_LIMIT_RETRIES of them a session, never a loop."""
    t = [NOW]
    later = (lambda v_: t.__setitem__(0, t[0] + KWS.RATE_LIMIT_RETRY_S))
    v = RefuseDeletes(n=1)
    want = [A, B]
    steps = _quiet_free([
        lambda v_: (want.remove(B), v_.sub.forget([B])), "idle",
        "process", "deliver",                          # refused
        "idle", "idle",                                 # nothing inside 30 s
        later, "idle",                                  # the retry
        "process", "deliver",                           # ok: B gone
        lambda v_: v_.activity(B, "0.47", "1"),          # not streamed
        lambda v_: v_.activity(A, "0.48", "1"), "deliver", "idle"])
    sub, books, seen = VM.run(v, want, steps, clock=lambda: t[0])
    assert seen["violations"] == [], seen["violations"][:2]
    assert [(a, ts) for _c, a, ts in v.commands()] == [
        (None, [A, B]), ("delete_markets", [B]), ("delete_markets", [B])]
    assert v.markets == [A] and seen["final"][A]["ok"]
    assert B not in books.books
    # bounded: every delete refused
    t2 = [NOW]
    v2 = RefuseDeletes(n=99)
    want2 = [A, B]
    steps2 = _quiet_free([
        lambda v_: (want2.remove(B), v_.sub.forget([B])), "idle",
        "process", "deliver"])
    for _ in range(KWS.MAX_RATE_LIMIT_RETRIES + 2):
        steps2 += [lambda v_: t2.__setitem__(0, t2[0] +
                                             KWS.RATE_LIMIT_RETRY_S),
                   "idle", lambda v_: v_.inq and v_.process(),
                   lambda v_: v_.out and v_.steps.appendleft("deliver")]
    sub2, books2, seen2 = VM.run(v2, want2, steps2, clock=lambda: t2[0])
    assert seen2["violations"] == []
    dels = [ts for _c, a, ts in v2.commands() if a == "delete_markets"]
    assert dels == [[B]] * (1 + KWS.MAX_RATE_LIMIT_RETRIES)
    assert sub2.next_due() is None


# ── a separate counter that numbered `subscribed` ───────────────────────

def test_a_numbered_subscribed_costs_one_gap_and_never_ends_the_session():
    """The review's S2 repro: A's snapshot (data 1), our add of B answered by
    an `ok` carrying control seq 2 -- the shared slot, A CURRENT, within the
    bound: the documented false gap, one get_snapshot. B's own snapshot then
    comes at DATA seq 2: it used to be "out of sequence" (a second gap while
    the get_snapshot was outstanding: the session ended). The data run
    continues exactly over the control frame's slot -- a separate counter,
    learned: B CURRENT, then the repair's snapshots in the data run."""
    v = SepVenue()
    want = [A]
    steps = ["process", "deliver", "deliver",          # subscribed, A (1)
             lambda v_: want.append(B), "idle",          # add B
             "process", "deliver",                       # ok: control seq 2
             "process",
             lambda v_: v_.steps.extendleft(["deliver"] * len(v_.out)),
             lambda v_: v_.activity(A, "0.46", "4"), "deliver",
             lambda v_: v_.activity(B, "0.47", "5"), "deliver", "idle"]
    sub, books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:2]
    assert isinstance(seen["ended"], ConnectionError), seen["ended"]
    assert books.stats["gaps"] == 1
    assert books.stats["control_seq_separate"] == 1
    assert [a for _c, a, _t in v.commands()] == [None, "add_markets",
                                                 "get_snapshot"]
    fin = seen["final"]
    assert fin[A]["ok"] and fin[B]["ok"]
    assert VM._code_book(fin[A]) == v.truth[A]
    assert VM._code_book(fin[B]) == v.truth[B]


# ── the guard's documented costs, bounded ───────────────────────────────

def test_a_two_market_quiet_sids_first_ok_is_one_false_gap_and_one_repair():
    """Documented shared counter, nothing lost: subscribe [A, B] (seq 1, 2),
    add C -> its `ok` at 3. A separate counter could be at 1 + 1 + 1 = 3 by
    then, and A, B are CURRENT: the documented false gap. ONE get_snapshot
    repairs it on the same sid; every book ends CURRENT with the venue's
    book, never stale in between."""
    v = VM.MemberVenue()
    want = [A, B]
    steps = ["process", "deliver", "deliver", "deliver",
             lambda v_: want.append(C), "idle",            # add C
             "process", "deliver",                          # ok at 3
             "process",
             lambda v_: v_.steps.extendleft(["deliver"] * len(v_.out)),
             lambda v_: v_.activity(A, "0.46", "4"), "deliver", "idle"]
    sub, books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:2]
    assert books.stats["gaps"] == 1
    fin = seen["final"]
    assert all(fin[t]["ok"] and VM._code_book(fin[t]) == v.truth[t]
               for t in (A, B, C))
    assert [a for _c, a, _t in v.commands()] == [None, "add_markets",
                                                 "get_snapshot"]


def test_unscoped_refusals_on_a_quiet_sid_end_the_session_by_name():
    """The review's other cost: refusals WITHOUT (sid, seq) count as update
    commands for the bound but take no seq, so on a one-market sid the
    venue's next `ok`s sit inside the bound with a book CURRENT -- two
    ambiguous replies, the second while the first's get_snapshot is
    outstanding: the session ends (GapDuringRecovery, by name; the
    reconnect subscribes every wanted market in its one subscribe).
    Bounded, fail closed, nothing served stale."""
    v = VM.MemberVenue(scoped=False, refuse27_at={1, 2})
    want = [A]
    steps = ["process", "deliver", "deliver",
             lambda v_: want.append(B), "idle",
             "process", "deliver", "process", "deliver",
             lambda v_: want.append(C), "idle",
             lambda v_: want.append("KXM-D"), "idle",
             "process", "deliver", "deliver",
             "process", "deliver", "deliver", "deliver"]
    sub, books, seen = VM.run(v, want, steps)
    assert seen["violations"] == []
    assert isinstance(seen["ended"], KWS.GapDuringRecovery), seen["ended"]
    assert sub.last_end_reason == KWS.R_GAP_DURING_RECOVERY
    assert books.counts()["current"] == 0
    assert len(v.sent) <= 8


# ── liveness holes the extended model seeds found (round 3) ─────────────

def test_a_refused_market_the_venue_may_hold_already_is_re_added_alone():
    """Model seed D-50869, directed (market limit 3): D's add is executed
    but its `ok` is lost; D's own snapshot is then a gap, and D is re-added
    together with C, newly wanted. Error 26 refuses that WHOLE command (C
    would pass the limit) -- twice -- and D was left GAP, not held, while the
    venue held and streamed it. D's add may have been taken already (its
    reply was lost), so after a refusal it is re-added ALONE: an add of a
    held market is "no action", answered by `ok` listing it; D ends CURRENT
    with the venue's book, C (truly refused) GAP by name."""
    v = VM.MemberVenue(market_limit=3)
    want = [A, B]
    steps = ["process", "deliver", "deliver", "deliver",
             lambda v_: v_.activity(A, "0.43", "2"), "deliver",
             lambda v_: v_.activity(A, "0.44", "3"), "deliver",
             lambda v_: want.append("KXM-D"), "idle",     # add D
             "process", "lose",                          # its ok lost
             lambda v_: want.append(C),                  # C wanted
             "deliver",                                  # D's snap: the gap
             "process", "deliver",                       # add [C, D]: 26
             "process", "process", "process",
             lambda v_: v_.steps.extendleft(["deliver"] * len(v_.out)),
             "idle", "process", "process", "process",
             lambda v_: v_.steps.extendleft(["deliver"] * len(v_.out)),
             lambda v_: v_.activity("KXM-D", "0.47", "2"), "deliver",
             "idle"]
    sub, books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:2]
    acts = [(a, ts) for _c, a, ts in v.commands()]
    assert ("add_markets", ["KXM-D"]) in acts[3:], acts
    assert v.markets == [A, B, "KXM-D"]
    fin = seen["final"]
    assert fin["KXM-D"]["ok"]
    assert VM._code_book(fin["KXM-D"]) == v.truth["KXM-D"]
    assert fin[C]["why_raw"] == KWS.R_NOT_HELD_BY_VENUE and not fin[C]["ok"]
    # bounded: D alone once (the refusal's one re-add), C its own
    assert sum(1 for a, ts in acts if a == "add_markets"
               and "KXM-D" in ts) <= 3


def test_a_market_rewanted_while_its_add_was_in_flight_never_waits_unasked():
    """Model seed D-50213, directed: B dropped (delete), wanted again (add),
    dropped and wanted again before the add's reply -- B is not bound to the
    sid any more. The add's `ok` confirms B: it used to wait on the add's
    own snapshot, so with that snapshot lost no gap could name B and it
    stayed AWAITING_SNAPSHOT for the rest of the session. Now the
    confirmation asks for B by get_snapshot (binding it when sent): with
    nothing lost B ends CURRENT with the venue's book; with the own
    snapshot lost the gap during that request ends the session by name
    (fail closed, the reconnect subscribes B afresh) -- never a book left
    waiting with nothing outstanding."""
    def scenario(lose):
        v = VM.MemberVenue()
        want = [A, B]
        steps = ["process", "deliver", "deliver", "deliver",
                 lambda v_: v_.activity(A, "0.43", "2"), "deliver",
                 lambda v_: v_.activity(A, "0.44", "3"), "deliver",
                 lambda v_: (want.remove(B), v_.sub.forget([B])), "idle",
                 "process", "deliver",                   # delete: ok [A]
                 lambda v_: want.append(B), "idle",       # add B
                 lambda v_: (want.remove(B), v_.sub.forget([B])),
                 lambda v_: want.append(B), "idle",       # again
                 "process", "deliver",                    # ok(add) lists B
                 "lose" if lose else "deliver",           # B's own snapshot
                 "process",
                 lambda v_: v_.steps.extendleft(["deliver"] * len(v_.out)),
                 lambda v_: v_.activity(B, "0.46", "4"), "deliver", "idle"]
        return v, VM.run(v, want, steps)
    v, (sub, books, seen) = scenario(lose=False)
    assert seen["violations"] == [], seen["violations"][:2]
    acts = [(a, ts) for _c, a, ts in v.commands()]
    assert acts == [(None, [A, B]), ("delete_markets", [B]),
                    ("add_markets", [B]), ("get_snapshot", [B])], acts
    assert seen["final"][B]["ok"]
    assert VM._code_book(seen["final"][B]) == v.truth[B]
    v, (sub, books, seen) = scenario(lose=True)
    assert seen["violations"] == [], seen["violations"][:2]
    assert [(a, ts) for _c, a, ts in v.commands()][-1] == ("get_snapshot",
                                                            [B])
    assert isinstance(seen["ended"], KWS.GapDuringRecovery), seen["ended"]


def test_on_a_number_not_ours_a_control_frame_at_or_below_the_last_seq_gaps():
    """A sid never acknowledged as ours (frames before any ack; the RC6
    rules): no command of ours can be matched on it, so a control frame at
    or below the last seq while a book is CURRENT is a replay -- the sid
    dies (a gap), it no longer switches to separate; with nothing CURRENT
    it is counted, and the sid's control frames are never sequenced from
    then on, except that one past the data run's next slot with a book
    CURRENT gaps."""
    b = KWS.WsBooks(clock=lambda: NOW)
    b.want([A])
    b.on_connected()
    assert b.on_message(snap(1)) == "SNAPSHOT"
    assert b.on_message(delta(2)) == "DELTA"
    assert b.on_message(ok(9, 1)) == "GAP"
    assert not b.current(A)["ok"] and 1 in b.dead_sids
    assert b.stats["control_replayed"] == 1
    # nothing CURRENT: counted, then separate
    b2 = KWS.WsBooks(clock=lambda: NOW)
    b2.want([A, B])
    b2.on_connected()
    b2.forget([B])
    assert b2.on_message(delta(1, B)) == "IGNORED_NOT_TRACKED"
    assert b2.on_message(snap(2, B)) == "IGNORED_NOT_TRACKED"
    assert b2.on_message(ok(9, 1)) == "IGNORED"       # 1 <= last 2
    assert 1 in b2.legacy_separate
    assert b2.on_message(snap(3)) == "SNAPSHOT"
    assert b2.on_message(ok(10, 2)) == "IGNORED"      # never sequenced
    assert b2.current(A)["ok"]
    assert b2.on_message(ok(11, 5)) == "GAP"          # past 3 + 1
    assert not b2.current(A)["ok"]
