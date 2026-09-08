"""E16 (2026-09-08; the PNL program, lane 2): the freeze reads twice; the
frozen exit follows his witnessed sale; the D2 thaw ON (owner decision
D2 = YES, 13:3xZ), a switch that may only turn it off.

Book 266 (hard2/book_266_1152.log): our 1,940 @0.50 filled 22:39:50Z; the
book froze `venue_ledger_disagree` on ONE walk that had not caught up with
that fill; while his net went 19,400 -> 30,900 the frozen book never
increased ($571 of stake), and while he cut 30% at 0.11-0.25 the frozen
exit refused `frozen_venue_unread` and we held 1,955 to 0 (-100% vs his
-51%). Three rules, every one failing closed toward NOT trading:

  * TWO READS: a live book's first disagreeing venue read is a SUSPECT --
    no freeze, the increase held by name (`venue_suspect_hold`), the exit
    planned as a live book plans it; the freeze fires on the NEXT fresh
    walk (t.walk_at: the tick's own step R, never the fast tick's cached
    _last_walk) disagreeing in the same direction; an agreeing fresh read
    clears the suspect; a cached read carries it. `wrong_sign_trip` keeps
    its one-read trip.
  * THE FROZEN EXIT ON HIS WITNESSED SALE: with the walk unread
    (`frozen_venue_unread`) a reducing fill of his clocked after the last
    frozen plan's `fills_at` (mi.reducing_since, E12b's witness) sizes a
    reduce on the FILLS' net x ratio with the LEDGER in both seats, capped
    at the ledger, placed as E5 places it (his price within the cent),
    marked `frozen_reduce_on_fill`. No witness: refused as before.
  * THE THAW (owner decision D2 = YES): a HELD venue_ledger_disagree book
    the venue agrees with on two consecutive FRESH reads thaws to live --
    ON BY DEFAULT (`ml.MIRROR_FROZEN_THAW`, read once at import from
    PMUS_MIRROR_AUTO_THAW: absent is on, any word but on/1/true/yes is
    off -- a switch may only turn a rail off) and behind
    rules.MIRROR_FROZEN_EXITS; off, the book stays frozen by name
    (`thaw_held`) with its E5 exits. One read, a cached read, a lost
    order's freeze: never. The thawed book is a live book again and
    re-freezes on the next two disagreeing fresh reads. A FLAT book thaws
    on one read as E5 did.

Driven through workers.mirror_live.tick_once with the E5 suite's pool
(tests.test_e5_frozen_exits: the register, co-hold and receipt statements)
and the worker suite's venue and rails.
"""
import re
import asyncio
import inspect
import pathlib

import pytest

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e5_frozen_exits import _E5Pool, _frozen_long, _placed, _plan_exit
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    BUY, GTC_TIF, IOC_TIF, M, N, NOW, SELL, SHORT, SLUG, _Http, _NoClose, _Venue, _armed, _cancels,
    _census, _fill, _gone, _his, _kinds, _mkt, _places, _ratio_fills, _short_book, _shorts_on, _tick,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
NEW_KEYS = ("venue_ledger_suspect", "venue_suspect_hold", "frozen_reduce_on_fill", "thaw_held")


class _E16Pool(_E5Pool):
    """The E5 pool plus the D2 thaw's own statement (`ml-book-thaw-agrees`:
    frozen_ticks 0, the one reason named), read before the worker suite's
    substring match on `ml-book-thaw`."""

    def _run(self, kind, sql, a):
        s = " ".join(sql.split())
        if "ml-book-thaw-agrees" in s:
            self.sent.append((kind, s, a))
            b = self.books[a[0]]
            if b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree":
                b.update(state="live", frozen_reason=None, frozen_ts=None, frozen_ticks=0)
            return "UPDATE 1"
        return super()._run(kind, sql, a)


def _pool(**kw):
    kw.setdefault("fills", _his())
    kw.setdefault("snap", {M: 300.0, N: 0.0})
    kw.setdefault("snap_at", NOW - 40)
    kw.setdefault("ratio_fills", _ratio_fills())
    return _E16Pool(**kw)


def _suspect(b):
    return (b.get("last_plan") or {}).get("venue_ledger_suspect")


def _witness(b):
    return (b.get("last_plan") or {}).get("frozen_witness")


def _unread():
    """His per-market read failing this tick (the data API answering 500):
    E5's `frozen_venue_unread`, why `his_market_read`."""
    return _Http(status=500)


def _recent(what):
    return [x for x in ml._RECENT if x["what"] == what]


def _thaw_off(monkeypatch):
    """The D2 switch OFF, the way the environment turns it off, for the
    frozen exit's pins: those rails (the fills' net x ratio, the ledger
    cap, the marker, never a BUY, the clock) are the FROZEN book's, so
    the book must stay frozen across the agreeing walks they drive;
    under the default the second agreeing fresh walk thaws it and the
    live path takes his sale instead -- pinned once,
    test_e16_the_default_thaw_takes_over_from_the_frozen_exit_on_the_second_agreeing_walk."""
    monkeypatch.setenv("PMUS_MIRROR_AUTO_THAW", "off")
    monkeypatch.setattr(ml, "MIRROR_FROZEN_THAW", ml._auto_thaw_switch())
    assert ml.MIRROR_FROZEN_THAW is False


# ------------------------------------------------------------ the two reads

def test_e16_one_disagreeing_read_is_a_suspect_no_freeze_no_increase_the_exit_still_plans():
    """(a) ledger 0, the walk reads 50, he holds 300: the increase the
    plan wants is held `venue_suspect_hold`, nothing placed, the book
    LIVE with the suspect on its plan (no `venue_ledger_disagree` counted,
    no frozen row in _RECENT). (b) ledger 300, the walk reads 600, he
    sold 100: the reduce still plans as a live book's -- ONE IOC of 100 at
    his cent -- with the ledger in the seat; the reading is not sold on."""
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue(held={SLUG: 50})
    st = _tick(p, v)
    assert b["state"] == "live" and b["frozen_reason"] is None and not _places(v)
    assert _census(st, "venue_ledger_suspect") == 1 and _census(st, "venue_suspect_hold") == 1
    assert _census(st, "venue_ledger_disagree") == 0 and not _recent("frozen")
    assert b["last_plan"]["kind"] == "increase" and b["last_plan"]["reason"] == "venue_suspect_hold"
    assert _suspect(b) == {"venue": 50, "explained": 0.0, "delta": 50.0, "at": NOW, "walk_at": NOW, "cached": 0}
    assert st["books_live"] == 1 and st["books_frozen"] == 0
    # (b) the exit plans on the suspect tick, on the LEDGER
    p2 = _pool(fills=_his(300, sold=100), snap={M: 200.0, N: 0.0})
    b2 = p2.add_book(ledger=300, avg_cost=0.31)
    v2 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, ioc_fill=100)
    st2 = _tick(p2, v2, http=_mkt(200))
    assert b2["state"] == "live" and _census(st2, "venue_ledger_suspect") == 1
    assert [c[2:6] for c in _places(v2)] == [(0.30, 100, True, IOC_TIF)] and b2["ledger_net"] == 200
    assert _suspect(b2)["delta"] == 300.0 and _census(st2, "venue_ledger_disagree") == 0


def test_e16_two_disagreeing_fresh_reads_in_a_row_freeze_venue_ledger_disagree_with_the_e5_detail():
    """The second fresh walk disagreeing the same way: frozen
    `venue_ledger_disagree`, the E5 detail on the plan and the recent row
    byte for byte, the transition-tick hold (F1) on the exit, the first
    read carried on the frozen plan for the record."""
    p = _pool()
    b = p.add_book(ledger=0)
    _tick(p, _Venue(held={SLUG: 50}))
    v = _Venue(held={SLUG: 50})
    st = _tick(p, v, now=NOW + 15)
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree" and not _places(v)
    assert _census(st, "venue_ledger_disagree") == 1 and _census(st, "venue_ledger_suspect") == 0
    lp = b["last_plan"]
    assert (lp["venue"], lp["ledger"], lp["manual"], lp["registered"], lp["kind"]) == (50, 0, 0.0, 0.0, "frozen")
    assert _plan_exit(b) == {"held": "transition_tick"} and _suspect(b)["at"] == NOW
    fr = _recent("frozen")[-1]
    assert {k: fr[k] for k in ("book", "what", "reason", "venue", "ledger", "manual", "registered")} == {
        "book": b["id"], "what": "frozen", "reason": "venue_ledger_disagree", "venue": 50, "ledger": 0,
        "manual": 0.0, "registered": 0.0}
    assert st["books_frozen"] == 0 and lp["fills_at"] == NOW - 3000, "the frozen plan carries the fills' clock"


def test_e16_a_disagreeing_read_then_an_agreeing_fresh_read_clears_the_suspect():
    p = _pool()
    b = p.add_book(ledger=300, avg_cost=0.31)
    st = _tick(p, _Venue(held={SLUG: 600}))
    assert b["state"] == "live" and _suspect(b)["delta"] == 300.0 and _census(st, "venue_suspect_hold") == 0
    st2 = _tick(p, _Venue(held={SLUG: 300}), now=NOW + 15)
    assert b["state"] == "live" and "venue_ledger_suspect" not in b["last_plan"]
    assert _census(st2, "on_target") == 1 and _census(st2, "venue_ledger_disagree") == 0
    # a third disagreeing read after the clear is a FIRST read again (the
    # cleared book is quiet under E6's rotation: read as after a deploy)
    ml._quiet_memo.pop(b["id"], None)
    st3 = _tick(p, _Venue(held={SLUG: 600}), now=NOW + 30)
    assert b["state"] == "live" and _census(st3, "venue_ledger_suspect") == 1 and _suspect(b)["at"] == NOW + 30


def test_e16_a_cached_second_read_never_freezes_and_a_fresh_one_after_it_does():
    """The full tick's walk reads 300 against a ledger of 0 (a suspect);
    the fast tick plans on the SAME cached walk: the suspect carries with
    its cached count, no freeze (two reads of one walk are one read); the
    next full tick's fresh walk disagreeing freezes."""
    p = _pool()
    b = p.add_book(ledger=0)
    _tick(p, _Venue(held={SLUG: 300}))
    assert b["state"] == "live" and _suspect(b)["cached"] == 0 and _suspect(b)["walk_at"] == NOW
    _walk({SLUG: 300.0})
    fs = _fast(p, _Venue())
    assert _skips(fs) == {} and b["state"] == "live" and b["frozen_reason"] is None
    assert _suspect(b) == {"venue": 300, "explained": 0.0, "delta": 300.0, "at": NOW, "walk_at": NOW, "cached": 1}
    assert b["last_plan"]["reason"] == "venue_suspect_hold"
    st = _tick(p, _Venue(held={SLUG: 300}), now=NOW + 15)
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree" and _census(st, "venue_ledger_disagree") == 1
    # the rule, at the unit: no prior, no fresh walk, an opposite sign, a
    # CACHED prior (the review's R1, folded 2026-09-08) -- never a second read
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    assert t.walk_at is None and ml._second_disagreeing_read({"delta": 50.0, "walk_at": NOW - 15}, 50.0, t) is False
    t.walk_at = NOW
    assert ml._second_disagreeing_read(None, 50.0, t) is False
    assert ml._second_disagreeing_read({"delta": 50.0, "walk_at": NOW - 15}, -50.0, t) is False
    assert ml._second_disagreeing_read({"delta": "junk", "walk_at": NOW - 15}, 50.0, t) is False
    assert ml._second_disagreeing_read({"delta": 50.0}, 50.0, t) is False
    assert ml._second_disagreeing_read({"delta": 50.0, "walk_at": None, "cached": 1}, 50.0, t) is False
    assert ml._second_disagreeing_read({"delta": 50.0, "walk_at": NOW - 15}, 50.0, t) is True
    assert ml._second_disagreeing_read({"delta": -50.0, "walk_at": NOW - 15}, -70.0, t) is True


def test_e16_an_opposite_direction_second_read_is_a_first_read_again():
    """ledger 0: the walk reads 50, then -50 (a zero ledger has no sign to
    trip on): the second read is a NEW suspect, never the freeze."""
    p = _pool()
    b = p.add_book(ledger=0)
    _tick(p, _Venue(held={SLUG: 50}))
    st = _tick(p, _Venue(held={SLUG: -50}), now=NOW + 15)
    assert b["state"] == "live" and _census(st, "venue_ledger_disagree") == 0 and _census(st, "wrong_sign_trip") == 0
    assert _suspect(b)["delta"] == -50.0 and _suspect(b)["at"] == NOW + 15 and _census(st, "venue_ledger_suspect") == 1


def test_e16_a_suspect_book_is_never_quiet_and_its_add_rest_is_cancelled_by_name():
    """On target with a suspect standing, the book is HOT (read next
    tick, never queued behind the quiet budget); an add rest standing on
    the suspect tick is cancelled under `venue_suspect_hold`."""
    p = _pool()
    b = p.add_book(ledger=300, avg_cost=0.31)
    _tick(p, _Venue(held={SLUG: 600}))
    assert b["last_plan"]["reason"] == "on target" and ml._quiet_memo[b["id"]]["quiet"] is False
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    o = p2.add_order(b2)
    v2 = _Venue(held={SLUG: 50})
    v2.rest("oid-1")
    _tick(p2, v2)
    assert _cancels(v2) == [("cancel", "oid-1", SLUG)] and o["state"] == "cancelled" and o["reason"] == "venue_suspect_hold"
    assert b2["state"] == "live"


def test_e16_wrong_sign_trip_on_one_read_is_unchanged_byte_for_byte():
    """ledger 10, the walk reads -10 (the genuine inversion: the venue's
    magnitude is the leg's, E20): the trip, the freeze and its receipt
    on ONE read, exactly as before; no suspect is written."""
    p = _pool()
    b = p.add_book(ledger=10)
    p.add_order(b)
    v = _Venue(held={SLUG: -10})
    v.rest("oid-1")
    st = _tick(p, v)
    assert p.state["mirror_live"] is False and p.state["mirror_live_trip"]["why"] == "wrong_sign_trip"
    assert b["state"] == "frozen" and b["frozen_reason"] == "wrong_sign_trip"
    assert _cancels(v) and not _places(v) and _census(st, "wrong_sign_trip") == 1 and _census(st, "venue_ledger_suspect") == 0
    trip = p.state["mirror_live_trip"]
    assert {k: trip[k] for k in ("book", "venue", "ledger", "manual", "registered")} == {
        "book": b["id"], "venue": -10, "ledger": 10, "manual": 0.0, "registered": 0.0}
    assert _suspect(b) is None and b["last_plan"]["kind"] == "frozen" and b["last_plan"]["venue"] == -10
    src = inspect.getsource(ml._tick_book)
    assert 'await _freeze(t, book, "wrong_sign_trip", detail)' in src
    assert src.index('await _freeze(t, book, "wrong_sign_trip", detail)') < src.index("elif book.get(\"state\") != \"frozen\" and not _second_disagreeing_read(")


def test_e16_a_suspect_book_flat_on_his_vanish_is_not_closed_on_the_suspect_tick():
    """ledger 300, the walk reads 600, he is gone (E5 v3 F1's world):
    the live vanish flatten sells OUR 300 -- the ledger, never the
    reading -- and the book stays LIVE at 0 with `close:
    venue_ledger_suspect` (closed, the next walk could neither freeze it
    nor clear it, and the 300 the walk reported would be nobody's). The
    next fresh walk decides: (a) agreeing at 0 -> the vanish close as any
    tick's; (b) 300 still there -> frozen venue_ledger_disagree, and E5
    sells the venue's 300 the tick after."""
    def _world():
        p = _pool(fills=_his(300, sold=300), snap=None)
        b = p.add_book(ledger=300, avg_cost=0.31)
        v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, ioc_fill=600)
        st = _tick(p, v, http=_gone())
        assert [c[2:6] for c in _places(v)] == [(0.30, 300, True, IOC_TIF)] and b["ledger_net"] == 0
        assert b["state"] == "live" and b["last_plan"]["close"] == "venue_ledger_suspect"
        assert _census(st, "venue_ledger_suspect") == 1 and _census(st, "venue_ledger_disagree") == 0
        assert _census(st, "closed_cashed_out") == 0 and st["books_live"] == 1
        return p, b
    p, b = _world()
    p2, b2 = _world()
    st2 = _tick(p, _Venue(held={SLUG: 0}, bid=0.30, ask=0.32), now=NOW + 15, http=_gone())
    assert b["state"] == "closed" and _census(st2, "closed_cashed_out") == 1 and "venue_ledger_suspect" not in b["last_plan"]
    v2 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=300)
    st3 = _tick(p2, v2, now=NOW + 15, http=_gone())
    assert b2["state"] == "frozen" and b2["frozen_reason"] == "venue_ledger_disagree" and not _places(v2)
    assert _plan_exit(b2) == {"held": "transition_tick"} and _census(st3, "venue_ledger_disagree") == 1
    v3 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=300)
    st4 = _tick(p2, v3, now=NOW + 30, http=_gone())
    assert [c[2:6] for c in _places(v3)] == [(0.30, 300, True, IOC_TIF)] and _census(st4, "frozen_reduce") == 1
    assert _plan_exit(b2)["result"] == "take" and b2["state"] == "frozen"


# ------------------------------------------------- the D2 thaw (YES, on)

def test_e16_frozen_plus_two_agreeing_fresh_reads_thaws_to_live_by_default():
    """DEFAULT ON (D2 = YES): nothing set. venue_ledger_disagree, ledger
    300, the venue agrees at 300, he holds 300: the first agreeing read
    is `thaw_held: one_read` (venue_agrees reads 1), the second thaws --
    live, frozen_reason NULL, frozen_ticks 0, `thawed_venue_agrees` on
    the plan, the thaw's own statement -- and the book plans live on
    that tick (on target with him)."""
    assert ml.MIRROR_FROZEN_THAW is True and rules.MIRROR_FROZEN_EXITS is True
    p = _pool()
    b = _frozen_long(p, reason="venue_ledger_disagree", frozen_ticks=7)
    st = _tick(p, _Venue(held={SLUG: 300}))
    assert b["state"] == "frozen" and b["last_plan"]["thaw_held"] == "one_read" and _census(st, "thaw_held") == 1
    assert b["last_plan"]["venue_agrees"]["reads"] == 1 and _plan_exit(b)["held"] == "frozen_no_his_exit"
    st2 = _tick(p, _Venue(held={SLUG: 300}), now=NOW + 15)
    assert b["state"] == "live" and b["frozen_reason"] is None and b["frozen_ticks"] == 0
    assert b["last_plan"]["thawed_venue_agrees"] is True and b["last_plan"]["kind"] != "frozen"
    assert _census(st2, "thaw_held") == 0 and _census(st2, "on_target") == 1
    assert [x for x in _recent("thawed") if x.get("why") == "venue_agrees"]
    assert any("ml-book-thaw-agrees" in s for _k, s, _a in p.sent)
    s = " ".join(ml._SQL_BOOK_THAW_AGREES.split())
    # E20 (review, HIGH-1): the statement names the two reasons that thaw
    # under the two-reads rule -- `wrong_sign_hold` beside this one -- and
    # still no other
    assert "frozen_ticks = 0" in s and "AND frozen_reason IN ('venue_ledger_disagree', 'wrong_sign_hold')" in s
    assert ml._TWO_READS_THAW_REASONS == frozenset({"venue_ledger_disagree", "wrong_sign_hold"})


def test_e16_a_disagreeing_read_between_two_agreeing_ones_starts_the_count_over():
    p = _pool()
    b = _frozen_long(p, reason="venue_ledger_disagree")
    _tick(p, _Venue(held={SLUG: 300}))
    assert b["last_plan"]["venue_agrees"]["reads"] == 1
    # the venue UNDER the ledger (100 against 300): E5 places nothing
    # (`frozen_no_his_exit`), so the count's restart is read clean
    v = _Venue(held={SLUG: 100})
    _tick(p, v, now=NOW + 15)
    assert b["state"] == "frozen" and "venue_agrees" not in b["last_plan"] and not _places(v)
    assert _plan_exit(b)["held"] == "frozen_no_his_exit"
    _tick(p, _Venue(held={SLUG: 300}), now=NOW + 30)
    assert b["state"] == "frozen" and b["last_plan"]["venue_agrees"]["reads"] == 1 and b["last_plan"]["thaw_held"] == "one_read"


def test_e16_a_held_venue_ledger_disagree_book_that_agrees_stays_frozen_with_the_switch_off(monkeypatch):
    """ENV OFF HOLDS: PMUS_MIRROR_AUTO_THAW=off read into the module
    constant -- two, three agreeing fresh reads and the book stays frozen
    `thaw_held: thaw_off` (never `thawed_venue_agrees`, never the thaw's
    statement), its E5 exit following him (he sold: the venue-sized
    reduce goes out). The E5 knob off with the switch on: `thaw_off` too
    (the thaw sits behind both)."""
    monkeypatch.setenv("PMUS_MIRROR_AUTO_THAW", "off")
    monkeypatch.setattr(ml, "MIRROR_FROZEN_THAW", ml._auto_thaw_switch())
    assert ml.MIRROR_FROZEN_THAW is False
    p = _pool()
    b = _frozen_long(p, reason="venue_ledger_disagree")
    for i in range(3):
        st = _tick(p, _Venue(held={SLUG: 300}), now=NOW + 15 * i)
        assert b["state"] == "frozen" and b["last_plan"]["thaw_held"] == "thaw_off" and _census(st, "thaw_held") == 1
        assert b["last_plan"]["venue_agrees"]["reads"] == i + 1 and "thawed_venue_agrees" not in b["last_plan"]
    assert not _recent("thawed")
    # the exit still follows him while frozen: he sold 100 -> SELL 100 at his cent
    p2 = _pool(fills=_his(300, sold=100), snap={M: 200.0, N: 0.0})
    b2 = _frozen_long(p2, reason="venue_ledger_disagree")
    v2 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=100)
    st2 = _tick(p2, v2, http=_mkt(200))
    assert b2["state"] == "frozen" and [c[2:6] for c in _places(v2)] == [(0.30, 100, True, IOC_TIF)]
    assert _census(st2, "frozen_reduce") == 1 and b2["last_plan"]["thaw_held"] == "thaw_off"
    assert not any("ml-book-thaw-agrees" in s for _k, s, _a in p.sent + p2.sent)
    monkeypatch.setattr(ml, "MIRROR_FROZEN_THAW", True)
    monkeypatch.setattr(rules, "MIRROR_FROZEN_EXITS", False)
    p3 = _pool()
    b3 = _frozen_long(p3, reason="venue_ledger_disagree")
    for i in range(2):
        _tick(p3, _Venue(held={SLUG: 300}), now=NOW + 15 * i)
    assert b3["state"] == "frozen" and b3["last_plan"]["thaw_held"] == "thaw_off"


def test_e16_the_auto_thaw_switch_is_on_by_default_and_may_only_turn_it_off(monkeypatch):
    """D2 = YES moved the default in CODE: the module constant is read
    once at import from PMUS_MIRROR_AUTO_THAW through the strict reader
    (never rules.env_switch, whose typo keeps the default: here the
    default is the raised rail, so an unreadable word falls to OFF).
    Absent: on. on/1/true/yes (any case, padded): on. Anything else --
    off/0/false/no, a blank, a typo, "yes please" -- off. Nothing in the
    environment can make it MORE than the code's default; the old names
    MIRROR_FROZEN_THAW / PMUS_MIRROR_FROZEN_THAW are not read at all."""
    src = pathlib.Path(ml.__file__).read_text()
    assert "\nMIRROR_FROZEN_THAW = _auto_thaw_switch()\n" in src and "\nMIRROR_FROZEN_THAW = False\n" not in src
    assert 'env_switch("PMUS_MIRROR_AUTO_THAW"' not in src and 'env_switch("MIRROR_AUTO_THAW"' not in src
    assert "AUTO_THAW" not in pathlib.Path(rules.__file__).read_text()
    monkeypatch.delenv("PMUS_MIRROR_AUTO_THAW", raising=False)
    assert ml._auto_thaw_switch() is True
    for raw in ("on", "1", "true", "yes", "ON", " Yes ", "TRUE"):
        monkeypatch.setenv("PMUS_MIRROR_AUTO_THAW", raw)
        assert ml._auto_thaw_switch() is True, raw
    for raw in ("off", "0", "false", "no", "", " ", "maybe", "yes please", "on/off", "enabled", "None"):
        monkeypatch.setenv("PMUS_MIRROR_AUTO_THAW", raw)
        assert ml._auto_thaw_switch() is False, raw
    # another name never reaches it: the switch reads its own name alone
    monkeypatch.delenv("PMUS_MIRROR_AUTO_THAW", raising=False)
    monkeypatch.setenv("MIRROR_FROZEN_THAW", "off")
    monkeypatch.setenv("PMUS_MIRROR_FROZEN_THAW", "off")
    monkeypatch.setenv("MIRROR_AUTO_THAW", "off")
    assert ml._auto_thaw_switch() is True and ml._auto_thaw_switch("MIRROR_AUTO_THAW") is False
    # the tick reads the constant through the module: a typo in the
    # environment at import is OFF, and off holds the thaw by name
    monkeypatch.setenv("PMUS_MIRROR_AUTO_THAW", "onn")
    monkeypatch.setattr(ml, "MIRROR_FROZEN_THAW", ml._auto_thaw_switch())
    p = _pool()
    b = _frozen_long(p, reason="venue_ledger_disagree")
    for i in range(2):
        _tick(p, _Venue(held={SLUG: 300}), now=NOW + 15 * i)
    assert b["state"] == "frozen" and b["last_plan"]["thaw_held"] == "thaw_off" and b["last_plan"]["venue_agrees"]["reads"] == 2
    assert not _recent("thawed") and not any("ml-book-thaw-agrees" in s for _k, s, _a in p.sent)


def test_e16_placement_lost_is_never_thawed_by_the_two_reads_rule():
    """With the thaw ON (the default): a placement_lost book whose venue
    disagrees (600 on the venue against 300, past what its rows explain:
    E5's `frozen_venue_unexplained`, nothing placed) stays frozen through
    many ticks -- no `venue_agrees` record, no `thawed_venue_agrees`, the
    E5 exit its only road; the D2 statement names venue_ledger_disagree
    alone; order_lost and lost_ambiguous are outside the rule the same
    way, and the thaw's own writer is a no-op on any of them (no
    statement, the row untouched). (A placement_lost book the venue
    AGREES with -- the lost order never filled -- keeps E5's own one-read
    thaw: the code and its pins win over the brief's word,
    hard2/PNL_L2_notes.md.)"""
    assert ml.MIRROR_FROZEN_THAW is True
    p = _pool()
    b = _frozen_long(p, lost=0)
    for i in range(3):
        v = _Venue(held={SLUG: 600})
        st = _tick(p, v, now=NOW + 15 * i)
        assert b["state"] == "frozen" and b["frozen_reason"] == "placement_lost" and not _places(v)
        assert "venue_agrees" not in b["last_plan"] and "thawed_venue_agrees" not in b["last_plan"]
        assert _plan_exit(b)["held"] == "frozen_venue_unexplained" and _census(st, "thaw_held") == 0
    assert not _recent("thawed") and not any("ml-book-thaw-agrees" in s for _k, s, _a in p.sent)
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    t.walk_at = NOW
    agreed = {"venue_agrees": {"reads": 5, "at": NOW - 15, "walk_at": NOW - 15}}
    for reason in ("placement_lost", "order_lost", "lost_ambiguous"):
        assert ml._thaw_verdict(t, {"frozen_reason": reason}, 300, agreed, {}) is None, reason
        row = {"id": b["id"], "state": "frozen", "frozen_reason": reason}
        plan = {}
        asyncio.run(ml._thaw_agrees(t, row, plan))
        assert row["state"] == "frozen" and plan == {} and not any("ml-book-thaw-agrees" in s for _k, s, _a in p.sent)
    assert ml._thaw_verdict(t, {"frozen_reason": "venue_ledger_disagree"}, 300, agreed, {}) == "venue_agrees"
    t.walk_at = None
    assert ml._thaw_verdict(t, {"frozen_reason": "venue_ledger_disagree"}, 300, {}, {}) == "cached_read"
    assert ml._thaw_verdict(t, {"frozen_reason": "venue_ledger_disagree"}, 300, agreed, {}) == "cached_read"


def test_e16_a_flat_frozen_book_thaws_on_one_agreeing_read_as_e5_did():
    """venue_ledger_disagree, ledger 0, the venue at 0, he is gone: the
    E5 close path -- thawed on the one read and closed as any flat book
    (book 77's shape after its sale); nothing of the D2 rule touches it."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p, ledger=0, reason="venue_ledger_disagree")
    st = _tick(p, _Venue(held={SLUG: 0}, bid=0.30, ask=0.32), http=_gone())
    assert b["state"] == "closed" and "thaw_held" not in (b["last_plan"] or {}) and _census(st, "thaw_held") == 0
    assert _recent("thawed") and not [x for x in _recent("thawed") if x.get("why")]


def test_e16_one_agreeing_fresh_read_never_thaws():
    """ONE READ NEVER THAWS (the default on): the first agreeing fresh
    read is `thaw_held: one_read`, the book frozen with its reason and
    tick count as they were, no `thawed_venue_agrees`, no statement, no
    `thawed` row; the same walk read again at the same clock (a re-run
    of the tick, no new walk stamp) is still one read of the venue --
    the count is of FRESH walks, and the plan's record moves by one on
    each. At the unit: a prior record of one read and this fresh walk is
    the second (`venue_agrees`); no prior and a fresh walk is `one_read`;
    a prior of one and a cached walk is `cached_read`."""
    p = _pool()
    b = _frozen_long(p, reason="venue_ledger_disagree", frozen_ticks=4)
    st = _tick(p, _Venue(held={SLUG: 300}))
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree" and b["frozen_ticks"] == 4
    assert b["last_plan"]["thaw_held"] == "one_read" and b["last_plan"]["venue_agrees"]["reads"] == 1
    assert "thawed_venue_agrees" not in b["last_plan"] and _census(st, "thaw_held") == 1
    assert not _recent("thawed") and not any("ml-book-thaw-agrees" in s for _k, s, _a in p.sent)
    assert b["last_plan"]["venue_agrees"]["walk_at"] == NOW and _plan_exit(b)["held"] == "frozen_no_his_exit"
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW + 15, stats=ml._new_stats())
    t.walk_at = NOW + 15
    assert ml._thaw_verdict(t, {"frozen_reason": "venue_ledger_disagree"}, 300, {}, {}) == "one_read"
    one = {"venue_agrees": {"reads": 1, "at": NOW, "walk_at": NOW}}
    assert ml._thaw_verdict(t, {"frozen_reason": "venue_ledger_disagree"}, 300, one, {}) == "venue_agrees"
    t.walk_at = None
    assert ml._thaw_verdict(t, {"frozen_reason": "venue_ledger_disagree"}, 300, one, {}) == "cached_read"
    # the record a cached read carries is the prior's, unchanged
    assert ml._agree_record(one, t) == {"reads": 1, "at": NOW, "walk_at": NOW}
    assert ml._agree_record({}, t) == {"reads": 0, "at": NOW + 15}


def test_e16_a_cached_read_never_thaws(monkeypatch):
    """A CACHED READ NEVER THAWS. The only cached read is the fast tick's
    (t.walk_at None: it plans on E9's _last_walk); the full tick stamps
    its own walk at step R, so every full-tick read is fresh. (a) The
    fast tick never plans a FROZEN book at all (`not_live`): after the
    full tick's one agreeing read, two fast ticks on the same cached
    walk leave the book frozen `one_read` with reads 1, no statement,
    no `thawed` row; the next full tick's fresh walk is the second read
    and thaws it. (b) At the unit, the verdict a cached read gets on a
    frozen venue_ledger_disagree book is `cached_read` whatever the
    prior count (0, 1, 5), and the record it carries is the prior's
    unchanged; a mutant that let the fast tick reach a frozen book
    would still be held by name. (c) The stamp is the full tick's own
    line, pinned in the source."""
    p = _pool()
    b = _frozen_long(p, reason="venue_ledger_disagree")
    _tick(p, _Venue(held={SLUG: 300}))
    assert b["state"] == "frozen" and b["last_plan"]["thaw_held"] == "one_read" and b["last_plan"]["venue_agrees"]["reads"] == 1
    _walk({SLUG: 300.0})
    for i in range(2):
        fs = _fast(p, _Venue(), now=NOW + 1 + i)
        assert list(_skips(fs).values()) == ["not_live"] and _census(fs, "thaw_held") == 0
        assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree"
        assert b["last_plan"]["thaw_held"] == "one_read" and b["last_plan"]["venue_agrees"]["reads"] == 1
        assert "thawed_venue_agrees" not in b["last_plan"]
    assert not _recent("thawed") and not any("ml-book-thaw-agrees" in s for _k, s, _a in p.sent)
    # (b) the verdict at the unit, on a tick that did not walk
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW + 1, stats=ml._new_stats())
    assert t.walk_at is None
    for n in (0, 1, 5):
        prior = {"venue_agrees": {"reads": n, "at": NOW, "walk_at": NOW}}
        plan = {}
        assert ml._thaw_verdict(t, {"frozen_reason": "venue_ledger_disagree"}, 300, prior, plan) == "cached_read", n
        assert plan["venue_agrees"] == prior["venue_agrees"]
    assert ml._thaw_verdict(t, {"frozen_reason": "venue_ledger_disagree"}, 300, {}, {}) == "cached_read"
    # (c) the full tick's stamp, and the fast tick's None
    assert "t.walk_at = float(t.now)" in inspect.getsource(ml._tick)
    assert "walk_at" not in inspect.getsource(ml.fast_tick_once).replace("_last_walk", "")
    # the next FULL tick's fresh walk is the second read: thawed
    st = _tick(p, _Venue(held={SLUG: 300}), now=NOW + 15)
    assert b["state"] == "live" and b["frozen_reason"] is None and b["frozen_ticks"] == 0
    assert b["last_plan"]["thawed_venue_agrees"] is True and b["last_plan"]["venue_agrees"]["reads"] == 2
    assert _census(st, "thaw_held") == 0 and [x for x in _recent("thawed") if x.get("why") == "venue_agrees"]


def test_e16_a_thawed_book_re_freezes_on_two_disagreeing_fresh_reads():
    """D2's own bound: the thawed book is a LIVE book again under the
    two-reads rule. Thawed on two agreeing reads at 300; the venue then
    reports 600 (a fill the venue reports later): the first fresh read a
    SUSPECT (live, no freeze, nothing placed -- he holds 300 and the
    book is on target, nothing to add), the second fresh read the freeze
    `venue_ledger_disagree` with the E5 detail, frozen_ticks 1 (the
    thaw's zero, then this freeze), the transition-tick hold on the
    exit; and two agreeing reads after THAT thaw it again (the freeze
    named the one reason the rule thaws)."""
    p = _pool()
    b = _frozen_long(p, reason="venue_ledger_disagree", frozen_ticks=7)
    _tick(p, _Venue(held={SLUG: 300}))
    _tick(p, _Venue(held={SLUG: 300}), now=NOW + 15)
    assert b["state"] == "live" and b["last_plan"]["thawed_venue_agrees"] is True and b["frozen_ticks"] == 0
    # the thawed book planned on target: quiet under E6's rotation until
    # its turn -- read as after a deploy, so the next walk is its own
    ml._quiet_memo.pop(b["id"], None)
    v = _Venue(held={SLUG: 600})
    st = _tick(p, v, now=NOW + 30)
    assert b["state"] == "live" and b["frozen_reason"] is None and not _places(v)
    assert _census(st, "venue_ledger_suspect") == 1 and _census(st, "venue_ledger_disagree") == 0
    assert _suspect(b) == {"venue": 600, "explained": 300.0, "delta": 300.0, "at": NOW + 30, "walk_at": NOW + 30, "cached": 0}
    assert "thaw_held" not in b["last_plan"] and "venue_agrees" not in b["last_plan"]
    v2 = _Venue(held={SLUG: 600})
    st2 = _tick(p, v2, now=NOW + 45)
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree" and not _places(v2)
    assert b["frozen_ticks"] == 1 and _census(st2, "venue_ledger_disagree") == 1
    lp = b["last_plan"]
    assert (lp["venue"], lp["ledger"], lp["manual"], lp["registered"], lp["kind"]) == (600, 300, 0.0, 0.0, "frozen")
    assert _plan_exit(b) == {"held": "transition_tick"} and _suspect(b)["at"] == NOW + 30
    assert [x for x in _recent("frozen") if x["reason"] == "venue_ledger_disagree" and x["venue"] == 600]
    # and the cycle closes: two agreeing fresh reads thaw it again
    _tick(p, _Venue(held={SLUG: 300}), now=NOW + 60)
    assert b["state"] == "frozen" and b["last_plan"]["thaw_held"] == "one_read"
    _tick(p, _Venue(held={SLUG: 300}), now=NOW + 75)
    assert b["state"] == "live" and b["frozen_ticks"] == 0 and b["last_plan"]["thawed_venue_agrees"] is True
    assert len([x for x in _recent("thawed") if x.get("why") == "venue_agrees"]) == 2


def test_e16_the_default_thaw_takes_over_from_the_frozen_exit_on_the_second_agreeing_walk():
    """The frozen exit on his witnessed sale and the thaw, together under
    the default: venue_ledger_disagree, ledger 300, his per-market read
    down, the positions walk READ and agreeing at 300. Tick 1: one
    agreeing read (`thaw_held: one_read`), the frozen exit refused
    `frozen_venue_unread` / `unclocked` as before. Tick 2: his SELL of 90
    ingested after the clock AND the second agreeing fresh walk -- the
    book thaws first and the LIVE path takes his sale: the same SELL 90
    at his cent (the IOC at 0.30), a plain `take`, never the frozen
    marker, the ledger 210, the book live with `thawed_venue_agrees`;
    nothing sold twice."""
    assert ml.MIRROR_FROZEN_THAW is True
    p = _pool(snap=None)
    b = _frozen_long(p, reason="venue_ledger_disagree")
    v = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=90)
    st = _tick(p, v, http=_unread())
    assert not _places(v) and b["state"] == "frozen" and b["last_plan"]["thaw_held"] == "one_read"
    assert _census(st, "frozen_venue_unread") == 1 and _census(st, "thaw_held") == 1
    assert _plan_exit(b) == {"held": "frozen_venue_unread", "why": "his_market_read"}
    p.fills = _his(300, sold=90)
    v2 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=90)
    st2 = _tick(p, v2, now=NOW + 15, http=_unread())
    assert b["state"] == "live" and b["frozen_reason"] is None and b["last_plan"]["thawed_venue_agrees"] is True
    assert [c[2:6] for c in _places(v2)] == [(0.30, 90, True, IOC_TIF)] and b["ledger_net"] == 210
    o = _placed(p)[0]
    assert (o["kind"], o["side"], o["reason"], o["qty"], o["state"]) == ("take", SELL, "take", 90, "filled")
    assert _census(st2, "frozen_reduce_on_fill") == 0 and _census(st2, "frozen_venue_unread") == 0
    assert _census(st2, "thaw_held") == 0 and "frozen_exit" not in b["last_plan"] and "frozen_witness" not in b["last_plan"]
    assert not _recent("frozen_reduce_on_fill") and [x for x in _recent("thawed") if x.get("why") == "venue_agrees"]


# ------------------------------------------ the frozen exit on his witnessed sale
#
# Pinned with the D2 switch OFF (_thaw_off): the rails below are the
# frozen book's and the walks they drive agree, so under the default the
# second walk would thaw the book (the pin above).

def test_e16_frozen_plus_his_witnessed_sale_with_the_walk_unread_reduces_on_the_fills_net_at_his_price(monkeypatch):
    """venue_ledger_disagree, ledger 300, his per-market read down (E5's
    `frozen_venue_unread`, why his_market_read). Tick 1: no clock on the
    prior plan -- `frozen_witness: unclocked`, refused as before, the
    plan writes `fills_at`. Tick 2: his SELL of 90 (30%) ingested after
    that clock -> the fills' net 210 x ratio 1.0 = target 210, SELL 90
    off the LEDGER at his cent (the take at 0.30, the bid there), the
    row marked `frozen_reduce_on_fill`, the ledger 210, still frozen.
    Tick 3: the same fills, no new witness -> refused as before."""
    _thaw_off(monkeypatch)
    p = _pool(snap=None)
    b = _frozen_long(p, reason="venue_ledger_disagree")
    v = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=90)
    st = _tick(p, v, http=_unread())
    assert not _places(v) and _census(st, "frozen_venue_unread") == 1
    assert _plan_exit(b) == {"held": "frozen_venue_unread", "why": "his_market_read"}
    assert _witness(b) == {"why": "his_market_read", "held": "unclocked"} and b["last_plan"]["fills_at"] == NOW - 3000
    p.fills = _his(300, sold=90)
    v2 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=90)
    st2 = _tick(p, v2, now=NOW + 15, http=_unread())
    assert [c[1:] for c in _places(v2)] == [(SLUG, 0.30, 90, True, IOC_TIF, "ORDER_INTENT_BUY_LONG", False, None)]
    o = _placed(p)[0]
    assert (o["kind"], o["side"], o["tif"], o["reason"], o["qty"], o["state"]) == (
        "take", SELL, "IOC", "frozen_reduce_on_fill: take", 90, "filled")
    assert b["ledger_net"] == 210 and b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree"
    assert _census(st2, "frozen_reduce_on_fill") == 1 and _census(st2, "frozen_venue_unread") == 0
    assert _census(st2, "frozen_reduce") == 0 and _census(st2, "overfill") == 0 and p.state["mirror_live"] is True
    fx = _plan_exit(b)
    assert (fx["why"], fx["witnessed"], fx["fills_net"], fx["target"], fx["ledger"], fx["side"], fx["qty"],
            fx["reduce_on_fill"], fx["result"]) == ("his_market_read", 90.0, 210.0, 210, 300, SELL, 90, True, "take")
    assert fx["since"] == NOW - 3000 and _witness(b)["placed"] is True and b["last_plan"]["fills_at"] == NOW - 1000
    assert b["last_plan"]["his_level"] == pytest.approx(0.31) and b["last_plan"]["exit_take"] == 0.30
    assert _recent("frozen_reduce_on_fill")[-1]["qty"] == 90 and "frozen_excess" not in (o["receipt"] or {})
    v3 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=90)
    st3 = _tick(p, v3, now=NOW + 30, http=_unread())
    assert not _places(v3) and _census(st3, "frozen_venue_unread") == 1 and _census(st3, "frozen_reduce_on_fill") == 0
    assert _plan_exit(b) == {"held": "frozen_venue_unread", "why": "his_market_read"} and "frozen_witness" not in b["last_plan"]


def test_e16_the_on_fill_reduce_is_ratio_x_his_sale_never_more_than_his_proportion(monkeypatch):
    """Ratio 0.1: ledger 30 against his 300; his SELL of 90 -> target
    0.1 x 210 = 21 -> SELL 9 (30% of the ledger, his proportion). And
    our share already UNDER his proportion after his sale (ledger 100,
    his 300 -> 210: target 210 >= 100): no sale (`under_proportion`),
    refused as before -- 266's own shape after his 11,000 adds."""
    _thaw_off(monkeypatch)
    p = _pool(snap=None)
    b = _frozen_long(p, ledger=30, reason="venue_ledger_disagree", ratio=0.1)
    _tick(p, _Venue(held={SLUG: 30}, bid=0.30, ask=0.32), http=_unread())
    p.fills = _his(300, sold=90)
    v2 = _Venue(held={SLUG: 30}, bid=0.30, ask=0.32, ioc_fill=9)
    st2 = _tick(p, v2, now=NOW + 15, http=_unread())
    assert [c[2:6] for c in _places(v2)] == [(0.30, 9, True, IOC_TIF)] and b["ledger_net"] == 21
    assert _plan_exit(b)["target"] == 21 and _census(st2, "frozen_reduce_on_fill") == 1
    p3 = _pool(snap=None)
    b3 = _frozen_long(p3, ledger=100, reason="venue_ledger_disagree")
    _tick(p3, _Venue(held={SLUG: 100}, bid=0.30, ask=0.32), http=_unread())
    p3.fills = _his(300, sold=90)
    v3 = _Venue(held={SLUG: 100}, bid=0.30, ask=0.32, ioc_fill=90)
    st3 = _tick(p3, v3, now=NOW + 15, http=_unread())
    assert not _places(v3) and _census(st3, "frozen_venue_unread") == 1 and _census(st3, "frozen_reduce_on_fill") == 0
    assert _witness(b3)["held"] == "under_proportion" and _witness(b3)["target"] == 210 and _witness(b3)["witnessed"] == 90.0
    assert _plan_exit(b3) == {"held": "frozen_venue_unread", "why": "his_market_read"} and b3["ledger_net"] == 100


def test_e16_a_stale_walk_with_no_reducing_fill_refuses_frozen_venue_unread_as_before(monkeypatch):
    """Two ticks with him holding (no sale): `frozen_venue_unread` both,
    the plan's verdict byte for byte, nothing placed; his ADD after the
    clock is no witness either (a BUY of the long token is not a sale)."""
    _thaw_off(monkeypatch)
    p = _pool(snap=None)
    b = _frozen_long(p, reason="venue_ledger_disagree")
    for i in range(2):
        v = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=300)
        st = _tick(p, v, now=NOW + 15 * i, http=_unread())
        assert not _places(v) and _census(st, "frozen_venue_unread") == 1 and _census(st, "frozen_reduce_on_fill") == 0
        assert _plan_exit(b) == {"held": "frozen_venue_unread", "why": "his_market_read"}
    assert "frozen_witness" not in b["last_plan"]
    p.fills = _his(300) + [_fill(M, "BUY", 200, 0.33, NOW - 5)]
    v = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=300)
    st = _tick(p, v, now=NOW + 30, http=_unread())
    assert not _places(v) and _census(st, "frozen_venue_unread") == 1 and "frozen_witness" not in b["last_plan"]
    # the other two unread reasons refuse the same way with no witness
    p2 = _pool(fills=_his(300, sold=300), snap=None, coheld=None)
    b2 = _frozen_long(p2)
    st2 = _tick(p2, _Venue(held={SLUG: 600}, bid=0.30, ask=0.32), http=_gone())
    assert _plan_exit(b2) == {"held": "frozen_venue_unread", "why": "coheld_unreadable"} and _census(st2, "frozen_reduce_on_fill") == 0


def test_e16_a_reducing_fill_older_than_the_plan_clock_is_no_witness(monkeypatch):
    """His SELL stamped BEFORE the last frozen plan's clock -- read again
    (tick 1 counted it), or a row stamped hours ago and inserted now with
    no ingest clock -- witnesses nothing: refused as before."""
    _thaw_off(monkeypatch)
    p = _pool(fills=_his(300, sold=90), snap=None)
    b = _frozen_long(p, reason="venue_ledger_disagree")
    _tick(p, _Venue(held={SLUG: 300}, bid=0.30, ask=0.32), http=_unread())
    assert b["last_plan"]["fills_at"] == NOW - 1000, "the clock is the SELL's, the newest fill counted"
    v2 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=90)
    st2 = _tick(p, v2, now=NOW + 15, http=_unread())
    assert not _places(v2) and _census(st2, "frozen_venue_unread") == 1 and _census(st2, "frozen_reduce_on_fill") == 0
    p2 = _pool(snap=None)
    b2 = _frozen_long(p2, reason="venue_ledger_disagree")
    _tick(p2, _Venue(held={SLUG: 300}, bid=0.30, ask=0.32), http=_unread())
    p2.fills = _his(300) + [_fill(M, "SELL", 90, 0.31, NOW - 5000)]
    v3 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=90)
    st3 = _tick(p2, v3, now=NOW + 15, http=_unread())
    assert not _places(v3) and _census(st3, "frozen_reduce_on_fill") == 0 and b2["ledger_net"] == 300
    # an ingest clock AFTER the plan's clock on an old stamp IS a witness (E12b's rule: his sale happened)
    p2.fills = _his(300) + [_fill(M, "SELL", 90, 0.31, NOW - 5000, detected_at=NOW + 10)]
    v4 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=90)
    st4 = _tick(p2, v4, now=NOW + 30, http=_unread())
    assert [c[2:6] for c in _places(v4)] == [(0.30, 90, True, IOC_TIF)] and _census(st4, "frozen_reduce_on_fill") == 1


def test_e16_the_frozen_exit_on_fill_never_exceeds_the_ledger_and_never_buys(monkeypatch):
    """ledger 100, he sold his whole 300 (gone): the reduce is 100 -- the
    ledger, never his 300; a plan that came back a BUY is
    `frozen_reduce_only`, never sent; a placement_lost book with a venue
    surplus (600 against 300, the lost BUY filled unbooked) takes the
    same road: his sale reduces the LEDGER's share, the surplus waits
    for E5's read (never sold on a witness)."""
    _thaw_off(monkeypatch)
    p = _pool(snap=None)
    b = _frozen_long(p, ledger=100, reason="venue_ledger_disagree")
    _tick(p, _Venue(held={SLUG: 100}, bid=0.30, ask=0.32), http=_unread())
    p.fills = _his(300, sold=300)
    v2 = _Venue(held={SLUG: 100}, bid=0.30, ask=0.32, ioc_fill=300)
    st2 = _tick(p, v2, now=NOW + 15, http=_unread())
    assert [c[2:6] for c in _places(v2)] == [(0.30, 100, True, IOC_TIF)] and b["ledger_net"] == 0
    assert _plan_exit(b)["target"] == 0 and _plan_exit(b)["qty"] == 100 and _census(st2, "frozen_reduce_on_fill") == 1
    assert _census(st2, "frozen_excess_sold") == 0 and _census(st2, "overfill") == 0
    # the placement_lost surplus: 90 off the ledger's 300, the 300 unbooked untouched, still frozen
    p3 = _pool(snap=None)
    b3 = _frozen_long(p3)
    _tick(p3, _Venue(held={SLUG: 600}, bid=0.30, ask=0.32), http=_unread())
    assert _plan_exit(b3) == {"held": "frozen_venue_unread", "why": "his_market_read"} and b3["last_plan"]["fills_at"] == NOW - 3000
    p3.fills = _his(300, sold=90)
    v4 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, ioc_fill=90)
    st4 = _tick(p3, v4, now=NOW + 15, http=_unread())
    assert [c[2:6] for c in _places(v4)] == [(0.30, 90, True, IOC_TIF)] and b3["ledger_net"] == 210
    assert _census(st4, "frozen_reduce_on_fill") == 1 and b3["state"] == "frozen" and b3["frozen_reason"] == "placement_lost"
    assert _placed(p3)[0]["reason"] == "frozen_reduce_on_fill: take" and _census(st4, "frozen_excess_sold") == 0
    # the mutant: a BUY plan out of the seat -> refused, nothing sent, the witness kept for the next tick
    p2 = _pool(snap=None)
    b2 = _frozen_long(p2, reason="venue_ledger_disagree")
    _tick(p2, _Venue(held={SLUG: 300}, bid=0.30, ask=0.32), http=_unread())
    p2.fills = _his(300, sold=90)
    orig = mi.plan

    def _buy(target, ledger, venue, book, his, mark):
        pl = orig(target, ledger, venue, book, his, mark)
        return mi.Plan(BUY, 100, 0.30, "increase toward target") if pl.side == SELL else pl
    monkeypatch.setattr(ml.mi, "plan", _buy)
    v3 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=90)
    st3 = _tick(p2, v3, now=NOW + 15, http=_unread())
    assert not _places(v3) and _census(st3, "frozen_reduce_only") == 1 and _census(st3, "frozen_reduce_on_fill") == 0
    assert _plan_exit(b2)["held"] == "frozen_reduce_only" and b2["last_plan"]["fills_at"] == NOW - 3000
    assert _witness(b2).get("placed") is not True and _witness(b2)["witnessed"] == 90.0 and b2["ledger_net"] == 300
    src = inspect.getsource(ml._frozen_reduce_on_fill)
    assert "int(p.qty) > abs(ledger)" in src and 'float(ledger), float(ledger)' in src
    assert "_frozen_venue_own" not in src and "r.venue" not in src.replace("r.venue is None", "")


def test_e16_a_witnessed_exit_that_did_not_go_out_keeps_its_clock_and_fires_next_tick(monkeypatch):
    """The bid away this tick: the reduce RESTS at his cent (placed:
    the clock moves); a refusal that sent nothing keeps `since`, so the
    next tick witnesses the same sale again (unit: _frozen_clock)."""
    _thaw_off(monkeypatch)
    p = _pool(snap=None)
    b = _frozen_long(p, reason="venue_ledger_disagree")
    _tick(p, _Venue(held={SLUG: 300}, bid=0.28, ask=0.32), http=_unread())
    p.fills = _his(300, sold=90)
    v2 = _Venue(held={SLUG: 300}, bid=0.28, ask=0.32)
    st2 = _tick(p, v2, now=NOW + 15, http=_unread())
    assert [c[1:] for c in _places(v2)] == [(SLUG, 0.31, 90, True, GTC_TIF, "ORDER_INTENT_BUY_LONG", True, None)]
    o = _placed(p)[0]
    assert (o["kind"], o["reason"], o["state"], o["qty"]) == ("reduce", "frozen_reduce_on_fill", "open", 90)
    assert _census(st2, "frozen_reduce_on_fill") == 1 and ml._frozen_reduce_stands(b, o) is True
    assert b["last_plan"]["fills_at"] == NOW - 1000 and _witness(b)["placed"] is True
    # the rest stands through the next unread tick (V3-3: unknown, not unwanted)
    v3 = _Venue(held={SLUG: 300}, bid=0.28, ask=0.32)
    v3.orders = v2.orders
    st3 = _tick(p, v3, now=NOW + 30, http=_unread())
    assert not _cancels(v3) and not _places(v3) and o["state"] == "open" and _census(st3, "frozen_venue_unread") == 1
    fills = _his(300, sold=90)
    # (the prior plan is the second argument since the review's R2 fold, 2026-09-08)
    assert ml._frozen_clock({"frozen_witness": {"witnessed": 90.0, "since": 5.0, "placed": False}}, {}, fills, NOW) == 5.0
    assert ml._frozen_clock({"frozen_witness": {"witnessed": 90.0, "since": 5.0, "placed": True}}, {}, fills, NOW) == NOW - 1000
    assert ml._frozen_clock({"frozen_witness": {"witnessed": 0.0, "since": 5.0}}, {}, fills, NOW) == NOW - 1000
    assert ml._frozen_clock({}, {}, [], NOW) == NOW


def test_e16_a_frozen_short_book_reduces_on_his_witnessed_buy_back_with_the_walk_unread(monkeypatch):
    """The mirror image: ledger -300 (his 400 No against 100 Yes), his
    SELL of 120 of the other token (a buy-back, 0.70 = 0.30 in long
    space) after the clock -> target -180, the cover of 120 through S4 --
    a BUY of the long token with the closing intent rested at floor(his)
    0.30 while the ask is outside the ceiling -- sized on the ledger."""
    _thaw_off(monkeypatch)
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500)]
    p = _pool(fills=fills, snap=None)
    b = _short_book(p, ledger=-300, state="frozen", frozen_reason="venue_ledger_disagree", frozen_ts=NOW - 100)
    _tick(p, _NoClose(held={SLUG: -300}, bid=0.30, ask=0.32), http=_unread())
    assert _plan_exit(b) == {"held": "frozen_venue_unread", "why": "his_market_read"} and b["last_plan"]["fills_at"] == NOW - 2500
    p.fills = fills + [_fill(N, "SELL", 120, 0.70, NOW - 1000)]
    v2 = _NoClose(held={SLUG: -300}, bid=0.30, ask=0.32)
    st2 = _tick(p, v2, now=NOW + 15, http=_unread())
    assert "close" not in _kinds(v2)
    assert [c[1:] for c in _places(v2)] == [(SLUG, 0.30, 120, True, GTC_TIF, SHORT, True, None)]
    o = _placed(p)[0]
    assert (o["kind"], o["side"], o["intent"], o["reason"], o["qty"]) == ("reduce", BUY, "ORDER_INTENT_SELL_SHORT", "frozen_reduce_on_fill", 120)
    assert _census(st2, "frozen_reduce_on_fill") == 1 and _census(st2, "short_cover_rest") == 1
    assert _plan_exit(b)["target"] == -180 and _plan_exit(b)["witnessed"] == 120.0 and b["ledger_net"] == -300


# ------------------------------------------------------------ the census, the docs

def test_e16_every_new_name_is_a_census_key_before_the_pinned_tail_and_the_docs_name_them():
    keys = ml.CENSUS_KEYS
    for k in NEW_KEYS:
        assert k in keys and keys.index(k) < keys.index("venue_market_ended"), k
        assert ml._new_stats()["census"][k] == 0
    assert keys[-12] == "registered_no_increase" and keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    assert keys[-11:-8] == ("open_flow_only", "open_catchup", "flow_guard_unreadable")
    # the reason prefix every reader of the marker matches; the row's own marker survives an adoption
    assert "frozen_reduce_on_fill".startswith("frozen_reduce")
    assert ml._adopt_reason({"reason": "frozen_reduce_on_fill"}, "adopted by fingerprint") == "frozen_reduce_on_fill: adopted by fingerprint"
    assert ml._adopt_reason({"reason": "frozen_reduce_on_fill: take"}, "order_lost") == "frozen_reduce_on_fill: order_lost"
    assert ml._adopt_reason({"reason": "frozen_reduce: take"}, "order_lost") == "frozen_reduce: order_lost"
    assert ml._adopt_reason({"reason": "reduce"}, "order_lost") == "order_lost"
    doc = (REPO / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E16 \(2026-09-08\)", doc, re.M), "the E16 section header (numbered at landing: 38 on the tip)"
    for s in NEW_KEYS + ("MIRROR_FROZEN_THAW", "PMUS_MIRROR_AUTO_THAW", "D2 = YES", "fills_at", "walk_at",
                         "266", "D2", "thawed_venue_agrees", "venue_agrees", "under_proportion", "unclocked"):
        assert s in doc, s
    src = inspect.getsource(ml)
    assert "THE FREEZE READS TWICE (E16" in src and "THE FROZEN EXIT ON HIS WITNESSED SALE (E16" in src
