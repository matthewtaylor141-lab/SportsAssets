"""RC6.2 REVIEW, ROUND 5: NO SILENCE IS EVIDENCE EITHER, AND A MARKET THE
VENUE MAY HOLD IS ADDED ALONE (sportsassets.kalshi_ws module docstring,
VENUE MEMBERSHIP; WsBooks._settle, command_sent; Subscriber._commands,
_readds, _reply_timeouts).

THE FINDING (round-4 protocol review, blocking; found by the acceptance
model's own liveness property at fresh seeds, "under D every seed must end
with every wanted market CURRENT": D-910795, D-921381, D-930151, D-931845,
and 910795 / 914069 with the model venue's executed-error behaviours off).
Every one left a market the venue held and streamed AWAITING_SNAPSHOT or
GAP (R_NOT_HELD_BY_VENUE) for the rest of the session -- fail closed, never
stale. Two mechanisms, both older than round 4:

  (A) Round 4 stopped taking an ERROR as proof that a delete did not run;
      it still took SILENCE as proof that a delete WOULD run.
        * `command_sent` marked an add queued behind an unanswered delete as
          "fresh" (it expects the market's own snapshot), assuming the
          delete takes effect first. Refused (or never run, its reply lost),
          the delete leaves the market held: the add is "no action", its
          `ok` lists the market and sends no snapshot, and the confirmation
          waited for one for ever.
        * a delete whose reply never came was never timed out (`reply_lost`
          covered adds only), and when the follow-up add was answered by an
          error while the older delete was still unanswered, `_settle` never
          reached `_refuse` (an older command was left): no re-add, no
          retry, no unsure marking.
  (B) the gap re-add of a market whose add was unanswered (`replace_add`
      marks it UNSURE: the venue may hold it) shared a command with another
      market, and error 26 for the other refused the whole command, hiding
      that the venue holds this one; its one refusal re-add was already
      spent and 26 is not retried later.

THE RULE (round 5): the full-list `ok` reflects every earlier command
(H2); a reply to a later command of the subscription while a DELETE is
unanswered means its reply was lost (C2: the venue answers a subscription's
commands in the order it got them), as does a delete unanswered
REPLY_TIMEOUT_S; a delete whose reply never came is read like an errored
one (WsBooks._settle, LOST: not held, UNSURE, a wanted market refused and
re-added, a dropped one deleted again, bounded); an add behind an unanswered
delete expects no snapshot of its own (the confirming `ok` asks for one);
and any UNSURE market is added alone, at most MAX_SOLO_ADDS_PER_BURST a
burst (the rest in chunks: the command rate). A get_snapshot whose snapshots
never came is asked again REPLY_TIMEOUT_S after the request, within the
per-market request bounds (the model's silent reply loss found it).

Each test drives the real Subscriber against the documented venue
(tests/test_rc62_kalshi_ws_venue_membership.MemberVenue; ErrExec answers by
error, unscoped so that losing the reply leaves no hole in the sid's
sequence) and checks, before every step, that every CURRENT book equals the
venue's TRUE book as of the highest seq delivered. The tests marked
"fails on 8e6911d1" end with the market stuck on that head.

SMALL LIVE = SHADOW: market data only."""
from __future__ import annotations

import pytest

from sportsassets import kalshi_ws as KWS
from tests import test_rc62_kalshi_ws_errored_commands as E
from tests import test_rc62_kalshi_ws_venue_membership as VM

A, B, C = VM.A, VM.B, E.C
D = "KXM-D"
NOW = VM.NOW


def adv(t, dt):
    """The runtime's clock moves on by dt."""
    return lambda v_: t.__setitem__(0, t[0] + dt)


def lose_next_reply(v_):
    """The venue's next frame never arrives."""
    v_.out.popleft()


def _final_ok(seen, v, *tickers):
    fin = seen["final"]
    for t in tickers:
        assert fin[t]["ok"], (t, {x: (fin[x]["state_raw"], fin[x].get(
            "why_raw")) for x in fin})
        assert VM._code_book(fin[t]) == v.truth[t], t


# ── (A) an add behind a delete the venue did not run ────────────────────────

@pytest.mark.parametrize("run,code", [(False, 27), (True, 18)])
def test_an_add_behind_a_delete_whose_reply_is_lost_gets_its_snapshot(run,
                                                                      code):
    """fails on 8e6911d1 (run=False). subscribe [A, C]; C dropped (delete
    sent) and wanted again (add sent, behind it) before the venue answers.
    The delete is refused unprocessed (27: the venue still holds C) -- or
    runs (18) -- and its reply is LOST (no seq: no gap shows it). The add is
    then "no action" (C held) or takes C: either way its `ok` lists C. On
    8e6911d1 the add was marked fresh, expecting a snapshot of its own that
    a "no action" never sends: C stayed AWAITING_SNAPSHOT for the session
    while the venue streamed it. Now the confirmation asks for it: one
    get_snapshot."""
    v = E.ErrExec({"delete_markets": 1}, code=code, run=run, scoped=False)
    want = [A, C]
    steps = E.busy() + [E.drop(want, C), "idle", E.rewant(want, C), "idle",
                        lambda v_: v_.process(), lose_next_reply] \
        + E.SETTLE + E.moves()
    _sub, _books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:3]
    assert v.markets == [A, C]
    _final_ok(seen, v, A, C)
    assert E.acts_of(v) == [(None, [A, C]), ("delete_markets", [C]),
                            ("add_markets", [C]), ("get_snapshot", [C])]


def test_an_add_behind_a_delete_expects_no_snapshot_of_its_own():
    """WsBooks alone: an add sent while a delete naming the market is
    unanswered does not wait for its own snapshot -- the delete may not run,
    and the add is then "no action". The confirming `ok` names the market
    for a get_snapshot."""
    b = E._acked()
    b.forget([B])
    b.command_sent(1, 5, "delete_markets", [B])
    b.want([B])
    b.bind(1, [B])
    b.command_sent(1, 6, "add_markets", [B])
    assert B not in b.anchors[1]["expect"]
    # the delete refused unprocessed, its reply lost; the add: "no action"
    assert b.on_message(E._ok(6, 3, [VM.A, B])) in ("OK", "GAP")
    b.fatal = None
    assert b.held(1, B)
    assert B in b.recover.get(1, set()), b.recover
    # an add with nothing in flight on a market not held still expects it
    b2 = E._acked([VM.A])
    b2.want([B])
    b2.bind(1, [B])
    b2.command_sent(1, 5, "add_markets", [B])
    assert b2.anchors[1]["expect"] == {B: 5}


# ── (A) a follow-up add answered by an error behind a delete never answered ─

@pytest.mark.parametrize("run,code", [(False, 27), (True, 18)])
def test_a_followup_add_refused_behind_a_lost_delete_reply_is_re_added(run,
                                                                       code):
    """fails on 8e6911d1 (both). The delete's reply is lost; the add behind
    it is answered by an error (27 refused unprocessed; or 18 after it
    took C). On 8e6911d1 the error left the add's market with the delete
    still pending: `_settle` never reached `_refuse` (an older command was
    left), so C was neither re-added nor retried nor marked unsure, and
    stayed AWAITING_SNAPSHOT. The error answers a LATER command: the
    delete's reply was lost (C2), it is settled like an errored one, C is
    refused, re-added ALONE, and ends CURRENT."""
    v = E.ErrExec({"delete_markets": 1, "add_markets": 1}, code=code,
                  run=run, scoped=False)
    want = [A, C]
    steps = E.busy() + [E.drop(want, C), "idle", E.rewant(want, C), "idle",
                        lambda v_: v_.process(), lose_next_reply,
                        lambda v_: v_.process(), "deliver"] \
        + E.SETTLE + E.moves()
    _sub, books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:3]
    assert v.markets == [A, C]
    _final_ok(seen, v, A, C)
    acts = E.acts_of(v)
    assert acts[:3] == [(None, [A, C]), ("delete_markets", [C]),
                        ("add_markets", [C])]
    assert acts[3] == ("add_markets", [C]), acts       # re-added, alone
    assert books.stats["membership_unknown_after_lost_reply"] >= 1


def test_a_reply_to_a_later_command_settles_an_unanswered_delete_as_lost():
    """WsBooks alone (C2): the delete 5 of B is unanswered when the reply to
    the add 6 of B arrives, an error. The delete's reply was lost: B is not
    held and UNSURE (its effect is unknown), and the add's error refuses it
    (named by the venue's code) -- it used to stay pending behind the
    delete."""
    b = E._acked()
    b.forget([B])
    b.command_sent(1, 5, "delete_markets", [B])
    b.want([B])
    b.bind(1, [B])
    b.command_sent(1, 6, "add_markets", [B])
    assert b.on_message(E._err(6, 3, 27)) in ("ERROR", "GAP")
    b.fatal = None
    assert not b.held(1, B) and b.unsure(1, B)
    assert b.anchors[1]["pend"].get(B) is None
    assert b.refused.get(1, {}).get(B) == 27
    assert b.stats["membership_unknown_after_lost_reply"] == 1
    assert 5 not in b.anchors[1]["cmds"]


def test_a_list_ok_for_a_later_command_covers_an_unanswered_delete():
    """H2: the `ok` of a later command lists the subscription after every
    command before it -- an unanswered delete needs no reply of its own, and
    the market is as the list says (here: B listed, so the delete did not
    run and B is held)."""
    b = E._acked()
    b.forget([B])
    b.command_sent(1, 5, "delete_markets", [B])
    b.want([B])
    b.bind(1, [B])
    b.command_sent(1, 6, "add_markets", [B])
    assert b.on_message(E._ok(6, 3, [VM.A, B])) in ("OK", "GAP")
    b.fatal = None
    assert b.held(1, B) and not b.unsure(1, B)
    assert b.anchors[1]["cmds"] == {}
    assert b.stats["membership_unknown_after_lost_reply"] == 0


# ── (A) a delete never answered is timed out like an add ───────────────────

class NoReplyDelete(VM.MemberVenue):
    """A venue that RUNS delete_markets and never answers it."""

    def process(self):
        if self.inq:
            m = self.inq[0]
            p = m.get("params") or {}
            if m["cmd"] == "update_subscription" and \
                    p.get("action") == "delete_markets":
                self.inq.popleft()
                self.markets = [t for t in self.markets
                                if t not in (p.get("market_tickers") or ())]
                return
        super().process()


def test_a_dropped_markets_unanswered_delete_is_sent_again_bounded():
    """fails on 8e6911d1. C is dropped; the venue runs the delete and never
    answers it. 8e6911d1 left the delete pending for ever (`reply_lost`
    covered adds only): nothing was sent again. Now REPLY_TIMEOUT_S after
    it, its reply is taken as lost and a dropped market the venue may hold
    is deleted again RATE_LIMIT_RETRY_S later, at most
    MAX_RATE_LIMIT_RETRIES times a session -- never a loop."""
    t = [NOW]
    v = NoReplyDelete()
    want = [A, C]
    steps = E.busy() + [E.drop(want, C), "idle", lambda v_: v_.process()]
    for _ in range(KWS.MAX_RATE_LIMIT_RETRIES + 3):
        steps += [adv(t, KWS.REPLY_TIMEOUT_S), "idle",
                  lambda v_: v_.process(),
                  adv(t, KWS.RATE_LIMIT_RETRY_S), "idle",
                  lambda v_: v_.process()]
    sub, books, seen = VM.run(v, want, steps, clock=lambda: t[0])
    assert seen["violations"] == [], seen["violations"][:3]
    dels = [ts for a, ts in E.acts_of(v) if a == "delete_markets"]
    assert dels == [[C]] * (1 + KWS.MAX_RATE_LIMIT_RETRIES), dels
    assert sub.next_due() is None
    assert books.stats["membership_unknown_after_lost_reply"] >= 1


def test_a_market_wanted_again_after_its_delete_timed_out_is_added_alone():
    """The delete ran and its reply never came; C is wanted again once the
    reply is taken as lost. C is unsure (the venue may or may not hold it):
    added alone, its `ok` lists it, the confirmation asks for the snapshot,
    and it ends CURRENT with the venue's book and follows its deltas."""
    t = [NOW]
    v = NoReplyDelete()
    want = [A, C]
    steps = E.busy() + [E.drop(want, C), "idle", lambda v_: v_.process(),
                        adv(t, KWS.REPLY_TIMEOUT_S), "idle",
                        E.rewant(want, C), "idle"] + E.SETTLE + E.moves()
    _sub, _books, seen = VM.run(v, want, steps, clock=lambda: t[0])
    assert seen["violations"] == [], seen["violations"][:3]
    assert v.markets == [A, C]
    _final_ok(seen, v, A, C)
    acts = E.acts_of(v)
    assert acts.count(("add_markets", [C])) == 1, acts
    assert acts.index(("get_snapshot", [C])) > acts.index(
        ("add_markets", [C])), acts


def test_a_dropped_market_the_later_list_ok_shows_still_held_is_deleted_again():
    """C2 on the subscriber's side: C is dropped, the venue refuses the
    delete unprocessed (27) and its reply is lost. The `ok` of a LATER
    command (B added) lists C -- the delete did not run, and its reply is
    taken as lost without waiting for the reply timeout: C, still dropped
    and held, is deleted again RATE_LIMIT_RETRY_S later (bounded), and the
    venue ends holding [A, B]."""
    t = [NOW]
    v = E.ErrExec({"delete_markets": 1}, code=27, run=False, scoped=False)
    want = [A, C]
    steps = E.busy() + [E.drop(want, C), "idle", lambda v_: v_.process(),
                        lose_next_reply, lambda v_: want.append(B), "idle",
                        E.drain, "idle", E.drain,
                        adv(t, KWS.RATE_LIMIT_RETRY_S), "idle"] \
        + E.SETTLE
    sub, _books, seen = VM.run(v, want, steps, clock=lambda: t[0])
    assert seen["violations"] == [], seen["violations"][:3]
    dels = [ts for a, ts in E.acts_of(v) if a == "delete_markets"]
    assert dels == [[C], [C]], dels
    assert v.markets == [A, B], v.markets
    assert sub.next_due() is None


# ── (B) a market the venue may hold is never added in a shared command ─────

def test_a_gap_re_add_of_an_unsure_market_never_shares_a_command():
    """fails on 8e6911d1 (reviewer's probe_classB; market limit 2). A is
    subscribed; C and D wanted: the venue refuses add [C, D] as a whole
    (26); each is re-added alone: C is taken but its `ok` is LOST, D is
    refused (26, the limit). C's own snapshot is then a gap, and the gap
    re-adds C -- on 8e6911d1 TOGETHER with D: error 26 refused the whole
    command, hiding that the venue already held C; its one refusal re-add
    was spent, 26 is not retried, and C stayed GAP (R_NOT_HELD_BY_VENUE)
    for the session while the venue streamed it. Now the gap re-add marks C
    UNSURE and an unsure market goes alone: "no action", the `ok` lists C,
    the confirmation asks for its snapshot. D (really refused) stays GAP by
    name."""
    v = VM.MemberVenue(market_limit=2)
    want = [A]
    steps = ["process", "deliver", "deliver"] + [s for k in range(6) for s in (
        (lambda v_, k=k: v_.activity(A, f"0.{50 + k:02d}", str(2 + k))),
        "deliver")]
    steps += [lambda v_: want.extend([C, D]), "idle",
              lambda v_: v_.process(), "deliver", "idle",
              lambda v_: v_.process(), "lose",
              "deliver", "idle"]
    steps += [E.drain, "idle"] * 6 + [
        lambda v_: v_.activity(C, "0.47", "9"),
        lambda v_: v_.activity(A, "0.49", "1"), E.drain, "idle", E.drain,
        "idle"]
    _sub, _books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:3]
    assert v.markets == [A, C]
    fin = seen["final"]
    _final_ok(seen, v, A, C)
    assert not fin[D]["ok"] and fin[D]["why_raw"] == KWS.R_NOT_HELD_BY_VENUE
    acts = E.acts_of(v)
    first_shared = next(i for i, a in enumerate(acts)
                        if a == ("add_markets", [C, D]))
    after = acts[first_shared + 1:]
    assert not [1 for a, ts in after if a == "add_markets" and len(ts) > 1], \
        acts


def test_unsure_markets_are_added_alone_at_most_a_burst_at_a_time():
    """A gap with more unanswered adds than MAX_SOLO_ADDS_PER_BURST: the
    lost `ok` of one add of N markets is a gap on the first snapshot; every
    market is re-added (unsure: alone) -- the first MAX_SOLO_ADDS_PER_BURST
    each in a command of its own, the rest in one chunk (hundreds of
    unanswered adds must not become hundreds of commands, the command rate
    bound ends the session). All end CURRENT with the venue's books."""
    n = KWS.MAX_SOLO_ADDS_PER_BURST + 5
    ms = [f"KXN-{k:03d}" for k in range(n)]
    v = VM.MemberVenue()
    want = [A]
    steps = ["process", "deliver", "deliver",
             lambda v_: want.extend(ms), "idle",
             "process", "lose"] + ["deliver"] * 3 + ["idle"] \
        + [E.drain, "idle"] * 8 + [E.drain, "idle"]
    _sub, _books, seen = VM.run(v, want, steps)
    assert seen["violations"] == [], seen["violations"][:3]
    _final_ok(seen, v, A, *ms)
    acts = E.acts_of(v)
    adds = [ts for a, ts in acts if a == "add_markets"]
    assert adds[0] == ms                         # the one first add
    singles = [ts for ts in adds[1:] if len(ts) == 1]
    chunks = [ts for ts in adds[1:] if len(ts) > 1]
    assert len(singles) == KWS.MAX_SOLO_ADDS_PER_BURST, adds[1:]
    assert chunks == [ms[KWS.MAX_SOLO_ADDS_PER_BURST:]], chunks


# ── a get_snapshot nothing came of ────────────────────────────────────────

class SnapshotsDropped(VM.MemberVenue):
    """A venue whose first `drops` get_snapshot commands produce nothing: no
    snapshot, no error (refused by an error that never arrived)."""

    def __init__(self, drops, **kw):
        super().__init__(**kw)
        self.drops = drops

    def process(self):
        if self.inq:
            m = self.inq[0]
            p = m.get("params") or {}
            if m["cmd"] == "update_subscription" and \
                    p.get("action") == "get_snapshot" and self.drops > 0:
                self.drops -= 1
                self.inq.popleft()
                return
        super().process()


def _gap_then(*after):
    """subscribe [A, C], both CURRENT; a delta of A lost, the next one read:
    a gap, the books GAP, a get_snapshot [A, C] sent -- then `after`."""
    return ["process", "deliver", "deliver", "deliver",
            lambda v_: v_.activity(A, "0.45", "3"), lose_next_reply,
            lambda v_: v_.activity(A, "0.46", "2"), "deliver"] + list(after)


def test_a_get_snapshot_unanswered_is_asked_again_after_the_reply_timeout():
    """fails on 8e6911d1. The venue refuses the gap's get_snapshot by an
    error that never arrives (unscoped, lost: no hole in the sequence shows
    it). On 8e6911d1 the books waited for ever (awaiting, nothing
    outstanding to time out); now REPLY_TIMEOUT_S after the request it is
    asked again, and both books end CURRENT with the venue's books."""
    t = [NOW]
    v = SnapshotsDropped(1, scoped=False)
    want = [A, C]
    steps = _gap_then(lambda v_: v_.process(),
                      adv(t, KWS.REPLY_TIMEOUT_S), "idle") \
        + [E.drain, "idle"] * 3 + [lambda v_: v_.activity(C, "0.47", "9"),
                                   lambda v_: v_.activity(A, "0.49", "1"),
                                   E.drain, "idle", E.drain, "idle"]
    sub, _books, seen = VM.run(v, want, steps, clock=lambda: t[0])
    assert seen["violations"] == [], seen["violations"][:3]
    _final_ok(seen, v, A, C)
    snaps = [ts for a, ts in E.acts_of(v) if a == "get_snapshot"]
    assert snaps == [[A, C], [A, C]], snaps
    assert sub.next_due() is None


def test_a_get_snapshot_never_answered_ends_the_session_by_name_not_a_loop():
    """Bounded: a venue that never answers get_snapshot is asked twice (the
    plane-hang bound, MAX_RESUBSCRIBES_WITHOUT_RECOVERY) -- the third
    request ends the session (ResubscribeStorm, fail closed; the reconnect
    subscribes afresh), never a loop."""
    t = [NOW]
    v = SnapshotsDropped(99, scoped=False)
    want = [A, C]
    steps = _gap_then(lambda v_: v_.process())
    for _ in range(4):
        steps += [adv(t, KWS.REPLY_TIMEOUT_S), "idle",
                  lambda v_: v_.process()]
    sub, _books, seen = VM.run(v, want, steps, clock=lambda: t[0])
    assert seen["violations"] == [], seen["violations"][:3]
    assert isinstance(seen["ended"], KWS.ResubscribeStorm), seen.get("ended")
    assert sub.last_end_reason == KWS.R_RESUBSCRIBE_STORM
    snaps = [ts for a, ts in E.acts_of(v) if a == "get_snapshot"]
    assert snaps == [[A, C]] * KWS.MAX_RESUBSCRIBES_WITHOUT_RECOVERY, snaps
