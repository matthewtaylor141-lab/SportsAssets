"""RC6 integration of three Kalshi WS lanes (red-team sequence integrity,
api-responsive's forgotten markets, the kalshi lane's dead sids and bind):
the defects an independent review of the merge (b4506ed9) reproduced.

  * B1 -- a market dropped while its subscribe awaited the venue's ack was
    brought back by the ack's bind(): its book recreated, counted on the
    heartbeat, and resubscribed after a gap (neither parent alone could:
    one had bind and no forget, the other forget and no bind);
  * B2 -- a dropped market's snapshot re-based its sid's sequence without
    the check every other snapshot gets: a snapshot past a lost message left
    a tracked book CURRENT missing that update, and a replay re-applied a
    delta to a CURRENT book.
Tests adapted from the reviewer's probes; they fail on b4506ed9."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from sportsassets import kalshi_ws as KWS
from sportsassets.workers import kalshi_ws_market_data as W
from tests import test_rc6_kalshi_ws_sequencing as SEQ
from tests.test_rc6_kalshi_ws_sequencing import (NOW, delta, snap,
                                                 subscribed)


def books():
    b = KWS.WsBooks(clock=lambda: NOW)
    b.on_connected()
    return b


class VW(SEQ.VenueWS):
    """The invariant harness, plus callables in the script (the worker's
    wanted refresh between two messages)."""

    async def recv(self):
        while self.script and callable(self.script[0]):
            r = self.script.pop(0)()
            if r is not None:
                self.script.insert(0, r)
        return await super().recv()


def drive(script, want, *, at_end=None):
    b = KWS.WsBooks(clock=lambda: NOW)
    ref = []
    seen = {}
    written: dict = {}

    def end():
        seen.update({t: dict(b.current(t), sid=b.books[t]["sid"])
                     for t in b.books})
        seen["_books"] = set(b.books)
        seen["_counts"] = b.counts()
        if at_end is not None:
            at_end(b, seen)
    ws = VW(script, b, ref, on_empty=end)

    async def connect():
        return ws
    sub = KWS.Subscriber(connect, b, wanted=lambda: list(want),
                         clock=lambda: NOW)
    ref.append(sub)
    ctx = {"sub": sub, "written": written, "want": want, "b": b}

    async def go():
        with pytest.raises(ConnectionError):
            await sub.session()
    return ws, sub, b, seen, ctx, go


def prune_to(ctx, new_want):
    def f():
        ctx["want"][:] = list(new_want)
        W.prune_untracked(ctx["sub"], ctx["written"], ctx["want"])
    return f


def want_to(ctx, new_want):
    def f():
        ctx["want"][:] = list(new_want)
    return f


def test_inv_a_forgotten_markets_snapshot_past_a_lost_message_gaps_the_sid():
    """B was acked on sid 7 with A; B is dropped before its snapshot
    arrives; seq 2 (a delta of A) is lost; B's in-flight snapshot is seq 3.
    The sid skipped a message: A must not stay CURRENT missing seq 2."""
    b = books()
    b.want(["A", "B"])
    b.bind(7, ["A", "B"])
    assert b.on_message(snap(7, 1, "A")) == "SNAPSHOT"
    b.forget(["B"])
    # seq 2 lost
    out_snap = b.on_message(snap(7, 3, "B"))
    out_delta = b.on_message(delta(7, 4, "A", "0.44", "5"))
    assert not b.current("A")["ok"], (out_snap, out_delta)


def test_inv_a_forgotten_markets_replayed_snapshot_never_rewinds_the_sid():
    """Red-team replay through a dropped market: a replay of B's snapshot
    (seq 2) after A's delta seq 3 must not re-base sid 7 to 2, or the
    replay of A's delta seq 3 is applied twice to a CURRENT book."""
    b = books()
    b.want(["A", "B"])
    assert b.on_message(snap(7, 1, "A", yes=(("0.43", "100"),))) == \
        "SNAPSHOT"
    sb = snap(7, 2, "B")
    assert b.on_message(sb) == "SNAPSHOT"
    d3 = delta(7, 3, "A", "0.44", "50")
    assert b.on_message(d3) == "DELTA"
    b.forget(["B"])
    r1 = b.on_message(sb)                     # replayed snapshot of B
    r2 = b.on_message(d3)                     # replayed delta of A
    cur = b.current("A")
    lvl = b.books["A"]["yes"].get(Decimal("0.44"))
    assert not (cur["ok"] and lvl != Decimal("50")), (r1, r2, lvl)


def test_ix_bind_after_forget_never_brings_the_book_back():
    """B is dropped while its subscribe awaits the venue's ack (the pending
    command still names it); the ack's bind must not recreate B's book."""
    b = books()
    b.want(["A", "B"])
    b.forget(["B"])
    b.bind(7, ["A", "B"])                     # the ack of the command
    assert "B" not in b.books


def test_an_ack_after_a_prune_never_brings_a_dropped_market_back():
    """Production Subscriber: wanted {A,B}; one subscribe (cmd 1). The
    worker's wanted refresh drops B before the venue acks cmd 1. Then the
    ack (sid 7), A's snapshot, B's in-flight snapshot, a lost message and a
    delta that gaps sid 7."""
    want = ["A", "B"]
    script = []
    ws, sub, b, seen, ctx, go = drive(script, want)
    script[:] = [prune_to(ctx, ["A"]),
                 subscribed(1, 7), snap(7, 1, "A"), snap(7, 2, "B"),
                 delta(7, 4, "A")]                       # seq 3 lost
    ws.script = list(script)
    asyncio.run(go())
    resub = [m["params"]["market_tickers"] for m in SEQ._subs(ws)][1:]
    assert ws.violations == []
    assert seen["_books"] == {"A"}, seen["_books"]
    assert all("B" not in r for r in resub), resub


def test_ix_resubscribe_pending_then_forget_in_the_orderings_harness():
    """_ORDERINGS 'in_flight_after_the_resubscribe_ack' with K-C dropped
    after the gap, before the repair's reply. (RC6.2: the repair is the
    documented get_snapshot on the same sid -- its `ok` takes the sid's
    next seq and the fresh snapshots follow on sid 3 -- where it used to be
    a resubscribe acknowledged as sid 4; the script's venue replies follow
    the documented protocol, the assertions are the test's own plus: the
    dropped market is taken out by delete_markets and no later command
    names it, its late snapshots keep the sid's sequence.)"""
    want = ["K-A", "K-B", "K-C"]
    script = []
    ws, sub, b, seen, ctx, go = drive(script, want)
    ws.script = (list(SEQ._HEAD) + [prune_to(ctx, ["K-A", "K-B"])] + [
        SEQ.ok(2, 3, 4), snap(3, 5, "K-A"), snap(3, 6, "K-B"),
        snap(3, 7, "K-C"), snap(3, 8, "K-C"), delta(3, 9, "K-A")])
    asyncio.run(go())
    assert ws.violations == []
    assert seen["K-A"]["ok"] and seen["K-B"]["ok"]
    assert seen["_books"] == {"K-A", "K-B"}, seen["_books"]
    after = [m for m in ws.sent[2:]]
    assert [(m["cmd"], m["params"].get("action"), m["params"].get(
        "market_tickers")) for m in after] == [
        ("update_subscription", "delete_markets", ["K-C"])], ws.sent
    assert seen["_counts"]["gaps"] == 1
    assert seen["_counts"]["ignored_not_tracked"] == 2


#: every _ORDERINGS script with one market dropped at each of its first
#: seven positions (only positions the script has: no skipped cases)
_DROPS = [(o, d, p) for o in sorted(SEQ._ORDERINGS)
          for d in ("K-A", "K-B", "K-C")
          for p in range(1, min(7, len(SEQ._ORDERINGS[o])) + 1)]


@pytest.mark.parametrize("ordering,drop,pos", _DROPS)
def test_the_orderings_with_a_drop_anywhere(ordering, drop, pos):
    """The reviewer's sweep (21 of its cases failed on b4506ed9): no
    harness violation, the kept markets each CURRENT on the live
    resubscription, the dropped one has no book and is never resubscribed
    after its drop."""
    base = list(SEQ._ORDERINGS[ordering])
    want = ["K-A", "K-B", "K-C"]
    keep = [t for t in want if t != drop]
    ws, sub, b, seen, ctx, go = drive([], want)
    marker = {"n_sent": None}

    def dropper():
        marker["n_sent"] = len(ws.sent)
        prune_to(ctx, keep)()
    ws.script = base[:pos] + [dropper] + base[pos:]
    asyncio.run(go())
    after = ws.sent[marker["n_sent"]:]
    resub_after = [m["params"]["market_tickers"] for m in after
                   if m["cmd"] == "subscribe"]
    # (RC6.2) the documented repair and additions are update_subscription
    # add_markets / get_snapshot on the sid: after its drop the market is
    # named by none of them either (only by its delete_markets)
    named_after = [m["params"]["market_tickers"] for m in after
                   if m["cmd"] == "update_subscription"
                   and m["params"]["action"] != "delete_markets"]
    assert ws.violations == [], ws.violations
    assert drop not in seen["_books"], (seen["_books"], ws.sent)
    assert all(drop not in r for r in resub_after), resub_after
    assert all(drop not in r for r in named_after), named_after
    for t in keep:
        assert seen[t]["ok"], (t, seen[t], ws.sent)
