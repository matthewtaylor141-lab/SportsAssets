"""E5 review pins (2026-09-07): killers for the mutants the builder's
tests let live, rails the review probed, and the review's findings as
STRICT xfails at review time; the fixes landed (E5 v2) and the four
markers came off with them.

Findings (hard2/E5_review.md): a fill booked in step O after step R's
walk makes r.venue stale by that fill for the whole book walk; before
E5 that was a harmless one-tick `venue_ledger_disagree` freeze, and E5
fires the frozen exit on it (F1: a live book's reduce is sold twice and
bought back next tick; F2: a standing frozen reduce is re-planned at
the stale venue size). A registered book thaws into the increase path
(F3: book 77's shape buys his net again from a ledger of 0, and with
him gone closes flat with the 1,128 unmanaged). A standing frozen
reduce is left resting when the plan no longer wants it (F4).

Driven through workers.mirror_live.tick_once with the worker suite's
fakes; the pool here answers the register, co-hold and receipt
statements the way tests.test_e5_frozen_exits does, and the manual
read with a number (the worker suite's fake answers it with rows the
worker cannot read, so `manual` is always 0 there)."""
import asyncio

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e5_frozen_exits import _E5Pool, _frozen_long, _frozen_short, _placed, _plan_exit, _short_fills
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    BUY, CID, GTC_TIF, IOC_TIF, M, N, NOW, SELL, SHORT, SLUG, _NoClose, _Venue, _armed,
    _cancels, _census, _fill, _gone, _his, _kinds, _mkt, _places, _ratio_fills, _short_book,
    _shorts_on, _tick,
)

XFAIL_F1 = "E5 review F1 (CRITICAL): the frozen exit fires on the transition tick of a stale-venue disagreement"
XFAIL_F2 = "E5 review F2 (HIGH): a standing frozen reduce is re-planned at the stale venue size"
XFAIL_F3 = "E5 review F3 (HIGH): a registered book thaws into the increase path"
XFAIL_F4 = "E5 review F4 (MEDIUM): a standing frozen reduce is left resting when the plan no longer wants it"


class _ReviewPool(_E5Pool):
    """_E5Pool plus the desk's manual shares answered as a number."""

    def __init__(self, *a, manual=0.0, **kw):
        super().__init__(*a, **kw)
        self.manual_n = manual

    def _run(self, kind, sql, a):
        if "ml-manual-shares" in " ".join(sql.split()):
            return self.manual_n
        return super()._run(kind, sql, a)


def _pool(**kw):
    kw.setdefault("fills", _his())
    kw.setdefault("snap", {M: 300.0, N: 0.0})
    kw.setdefault("snap_at", NOW - 40)
    kw.setdefault("ratio_fills", _ratio_fills())
    return _ReviewPool(**kw)


def _frozen_rest(p, b, qty=600, wire=0.31):
    """A frozen reduce rest of the book's own placing, standing."""
    return p.add_order(b, side=SELL, wire=wire, qty=qty, kind="reduce", reason="frozen_reduce",
                       his_level=wire, placed_ts=NOW - 30)


# ------------------------------------------------------------ mutant killers

def test_e5r_the_desks_manual_shares_are_subtracted_from_the_frozen_seat():
    """M19: ledger 300, venue 700, the desk's manual sleeve 100, he is
    gone: venue_own is 600 and the reduce sells 600, never the desk's
    100 under this book."""
    p = _pool(fills=_his(300, sold=300), snap=None, manual=100.0)
    b = _frozen_long(p)
    v = _Venue(held={SLUG: 700}, bid=0.30, ask=0.32, lift=600)
    st = _tick(p, v, http=_gone())
    # E31 (FILL lane 31, 2026-09-10): the reduce is a post-only rest at the maker
    # wire max(sell_wire(his 0.31), bid 0.30 + MAKER_TICK) = 0.31, GTC, lifted at
    # create -- where it was ONE IOC at the take cent 0.30. The SIZE is the subject
    assert [c[2:6] for c in _places(v)] == [(0.31, 600, True, GTC_TIF)]
    assert _plan_exit(b)["venue_own"] == 600 and b["last_plan"]["manual"] == 100.0
    assert b["last_plan"]["registered"] == 0.0 and _census(st, "frozen_reduce") == 1
    # E5 review F3, the owner's option (b): the register is NOT subtracted
    # from the frozen seat -- registered shares are the book's to exit
    assert ml._frozen_venue_own(ml._Reading("rn1", CID, SLUG, M, N, [], 0.0, 0.0, {}, None, False, False,
                                            False, None, None, 0.30, 0.32, 0.31, 700.0, 100.0, None, True,
                                            registered=50.0)) == 600


def test_e5r_a_registered_short_book_records_no_position_sign_proof(monkeypatch):
    """M27: venue == ledger + registered on a short book is NOT the
    venue saying our BUY_SHORT alone holds the short side -- the proof
    needs the register at 0 exactly as it needs manual at 0."""
    _shorts_on(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500)],
              snap=None, registered={SLUG: -100.0})
    b = _short_book(p, ledger=-300)
    v = _NoClose(held={SLUG: -400}, bid=0.30, ask=0.32)
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert b["state"] == "live" and _census(st, "registered_books") == 1
    assert b["last_plan"].get("short_proof") is None
    # the same book with no register row records it
    p2 = _pool(fills=[_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500)],
               snap=None)
    b2 = _short_book(p2, ledger=-300)
    _tick(p2, _NoClose(held={SLUG: -300}, bid=0.30, ask=0.32), http=_mkt(100.0, 300.0))
    assert b2["last_plan"].get("short_proof") == "ok"


def test_e5r_with_the_knob_off_step_o_cancels_a_standing_frozen_reduce(monkeypatch):
    """M29: `_frozen_reduce_stands` reads the knob: off, the rest the
    frozen exit placed is cancelled under the freeze's name by step O,
    as any rest on a frozen book was before E5."""
    monkeypatch.setattr(rules, "MIRROR_FROZEN_EXITS", False)
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    o = _frozen_rest(p, b)
    v = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32)
    v.rest("oid-1", "SELL", 0.31, 600)
    st = _tick(p, v, http=_gone())
    assert _cancels(v) == [("cancel", "oid-1", SLUG)] and o["state"] == "cancelled"
    assert o["reason"] == "frozen_reduce: placement_lost" and not _places(v)
    assert _census(st, "frozen_exits_off") == 1 and ml._frozen_reduce_stands(b, o) is False


def test_e5r_the_frozen_excess_accumulates_across_two_partial_fills_of_one_rest():
    """M30: a rest of 600 on ledger 300 fills 400 then 200 across two
    ticks: the receipt's excess is 100 then 300 (cumulative, with the
    proceeds), the ledger books its 300 once, no trip; the book then
    reads venue 0 == ledger 0, thaws and closes flat."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    o = _frozen_rest(p, b)
    v = _Venue(held={SLUG: 200}, bid=0.28, ask=0.32, fills={"oid-1": (400.0, 0.31)})
    v.rest("oid-1", "SELL", 0.31, 600)
    st = _tick(p, v, http=_gone())
    assert b["ledger_net"] == 0 and o["receipt"]["frozen_excess"]["shares"] == pytest.approx(100.0)
    assert o["state"] == "open" and not _cancels(v) and not _places(v)
    assert _census(st, "frozen_excess_sold") == 1 and _census(st, "overfill") == 0
    v2 = _Venue(held={SLUG: 0}, bid=0.28, ask=0.32, fills={"oid-1": (600.0, 0.31)})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_gone())
    assert o["state"] == "filled" and o["receipt"]["frozen_excess"]["shares"] == pytest.approx(300.0)
    assert o["receipt"]["frozen_excess"]["usd"] == pytest.approx(93.0)
    assert p.state["mirror_live"] is True and _census(st2, "overfill") == 0
    assert b["state"] == "closed" and b["ledger_net"] == 0


def test_e5r_a_target_against_the_leg_is_refused_by_name_before_any_plan():
    """M33: the unit, handed a target the sign flip would have
    flattened: `frozen_reduce_only`, no plan, nothing sent."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    t.positions = {SLUG: 600.0}
    r = ml._Reading("rn1", CID, SLUG, M, N, [], 0.0, 0.0, {}, None, False, False, False, None, None,
                    0.30, 0.32, 0.31, 600.0, 0.0, None, True, snap_market_fresh=True)
    plan: dict = {}
    out = asyncio.run(ml._frozen_exit(t, b, r, -100, [], 0.0, plan))
    assert out == "frozen_reduce_only" and plan["frozen_exit"]["held"] == "frozen_reduce_only"
    assert "side" not in plan and not _placed(p) and _plan_exit({"last_plan": plan})["target"] == -100
    b2 = _short_book(p, ledger=-300, state="frozen", frozen_reason="placement_lost", frozen_ts=NOW - 100)
    t.positions = {SLUG: -600.0}
    r2 = ml._Reading("rn1", CID, SLUG, M, N, [], 0.0, 0.0, {}, None, False, False, False, None, None,
                     0.30, 0.32, 0.31, -600.0, 0.0, None, True, snap_market_fresh=True)
    plan2: dict = {}
    assert asyncio.run(ml._frozen_exit(t, b2, r2, 100, [], 0.0, plan2)) == "frozen_reduce_only"
    assert not _placed(p)


def test_e5r_the_replace_at_his_new_cent_sizes_on_the_venue_seat():
    """M34 (_plan_seat): his level moved 0.31 -> 0.29 with nothing
    filled: the rest at 0.31 is cancelled `replace` and re-rested at
    0.29 for the venue-sized 600 -- a re-plan on the ledger would read
    `frozen: venue and ledger disagree` and rest nothing."""
    fills = _his(300, sold=200) + [_fill(M, "SELL", 100, 0.29, NOW - 500)]
    p = _pool(fills=fills, snap=None)
    b = _frozen_long(p)
    o = _frozen_rest(p, b)
    v = _Venue(held={SLUG: 600}, bid=0.26, ask=0.32)
    v.rest("oid-1", "SELL", 0.31, 600)
    st = _tick(p, v, http=_gone())
    assert _cancels(v) == [("cancel", "oid-1", SLUG)] and o["reason"] == "frozen_reduce: replace"
    assert [c[2:6] for c in _places(v)] == [(0.29, 600, True, GTC_TIF)]
    assert b["ledger_net"] == 300 and _census(st, "frozen_reduce") == 1
    assert _plan_exit(b)["result"] == "rest_placed"


# ------------------------------------------------------------ rails probed

def test_e5r_a_frozen_reduce_the_venue_filled_past_its_own_quantity_is_the_overfill_still():
    """The venue reports 700 filled on a 600 rest: outside the row's
    quantity, so outside the venue's reading the row was sized on --
    the overfill it always was: freeze, trip, no excess note."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    o = _frozen_rest(p, b)
    v = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32, fills={"oid-1": (700.0, 0.31)})
    v.rest("oid-1", "SELL", 0.31, 600)
    st = _tick(p, v, http=_gone())
    assert b["frozen_reason"] == "placement_lost" and p.state["mirror_live"] is False
    assert p.state["mirror_live_trip"]["why"] == "overfill" and _census(st, "overfill") == 1
    assert _census(st, "frozen_excess_sold") == 0 and not _places(v)
    assert "frozen_excess" not in (o.get("receipt") or {})


def test_e5r_a_frozen_short_cover_refuses_with_the_proof_key_absent(monkeypatch):
    """Production's state (mirror-state read no `mirror_s4_proof` key):
    the frozen cover is `short_reduce_unproven` with `absent` on the
    plan; nothing sent, close_position never called."""
    _shorts_on(monkeypatch)
    p = _pool(fills=_short_fills(), snap=None)
    p.state.pop("mirror_s4_proof", None)
    b, v = _frozen_short(p, bid=0.30, ask=0.31)
    st = _tick(p, v, http=_gone())
    assert not _places(v) and "close" not in _kinds(v)
    assert _census(st, "short_reduce_unproven") == 1 and b["last_plan"]["s4"] == {"unproven": "absent"}
    assert b["ledger_net"] == -300 and b["state"] == "frozen"


def test_e5r_exits_only_mode_still_lets_the_frozen_book_reduce():
    """mirror_live false in the DB (exits-only): a reduce is an exit."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    p.state["mirror_live"] = False
    b = _frozen_long(p)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, lift=600)
    st = _tick(p, v, http=_gone())
    # E31: the maker rest at 0.31, lifted at create, where it was the IOC at 0.30
    assert st["mode"] == "exits" and [c[2:6] for c in _places(v)] == [(0.31, 600, True, GTC_TIF)]
    assert b["ledger_net"] == 0 and _census(st, "frozen_reduce") == 1


def test_e5r_his_sign_flip_flattens_the_frozen_long_book_and_never_opens_the_other_side(monkeypatch):
    """His net crossed to the short side: the target is flattened to 0
    (sign_flip), the frozen exit sells the venue's 600 and nothing
    opens the short -- never a flip, never a new episode."""
    _shorts_on(monkeypatch)
    fills = _his(300, sold=300) + [_fill(N, "BUY", 500, 0.72, NOW - 500)]
    p = _pool(fills=fills, snap=None)
    b = _frozen_long(p)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, lift=600)
    st = _tick(p, v, http=_mkt(0.0, 500.0))
    # E31: the flip's exit is the maker rest, GTC, never an IOC
    assert [(c[3], c[4], c[5], c[6]) for c in _places(v)] == [(600, True, GTC_TIF, "ORDER_INTENT_BUY_LONG")]
    assert b["last_plan"].get("sign_flip") is True and _plan_exit(b)["target"] == 0
    assert _census(st, "short_add") == 0 and _census(st, "short_open") == 0
    assert b["ledger_net"] == 0 and _plan_exit(b)["qty"] == 600 and st["books_live"] == 0


# ------------------------------------------------------------ the findings

def test_e5r_F1_a_live_books_reduce_fill_between_the_walk_and_step_o_is_not_sold_twice():
    """Live long book, ledger 300, he sold 100 (net 200), a reduce rest
    SELL 100 standing. Step R walked the venue at 300; the rest filled
    before step O read it; step O books 100 -> ledger 200; the book
    tick reads venue 300 (stale by the fill) against explained 200.
    On a5ff689 this is a one-tick `venue_ledger_disagree` freeze that
    thaws next tick `on_target`. Under E5 nothing may be placed. E16
    (the freeze reads twice): the one stale read is a SUSPECT, not a
    freeze -- still nothing placed, and the next fresh walk agreeing
    clears it `on_target`."""
    p = _pool(fills=_his(300, sold=100), snap={M: 200.0, N: 0.0})
    b = p.add_book(ledger=300, avg_cost=0.31)
    o = p.add_order(b, side=SELL, wire=0.31, qty=100, kind="reduce", his_level=0.31, placed_ts=NOW - 30)
    v = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=100, fills={"oid-1": (100.0, 0.31)})
    v.rest("oid-1", "SELL", 0.31, 100)
    st = _tick(p, v, http=_mkt(200))
    assert o["state"] == "filled" and b["frozen_reason"] is None and b["state"] == "live"
    assert b["last_plan"]["venue_ledger_suspect"]["delta"] == 100.0 and _census(st, "venue_ledger_suspect") == 1
    assert not _places(v) and b["ledger_net"] == 200 and _census(st, "frozen_reduce") == 0
    v2 = _Venue(held={SLUG: 200}, bid=0.30, ask=0.32)
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(200))
    assert b["state"] == "live" and not _places(v2) and _census(st2, "on_target") == 1
    assert "venue_ledger_suspect" not in b["last_plan"] and _census(st2, "venue_ledger_disagree") == 0


def test_e5r_F2_a_standing_frozen_reduce_partly_filled_after_the_walk_is_not_re_sized_past_the_venue():
    """Frozen book, ledger 300, a frozen reduce rest SELL 600 standing,
    he is gone. The walk read 600; the rest filled 200 before step O
    read it (the venue holds 400). No order for more than 400 may go
    out this tick."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    _frozen_rest(p, b)
    v = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32, fills={"oid-1": (200.0, 0.31)})
    v.rest("oid-1", "SELL", 0.31, 600)
    _tick(p, v, http=_gone())
    assert b["ledger_net"] == 100
    assert all(c[3] <= 400 for c in _places(v)), _places(v)


def test_e5r_F3_a_registered_book_neither_re_buys_nor_abandons_the_registered_shares():
    """Book 77's shape registered (venue 1,128 / ledger 0 / register
    1,128). He holds 300: the book must not BUY 300 more on top of the
    1,128 the account already holds. He is gone: the book must not
    close flat with the 1,128 still on the venue."""
    p = _pool(registered={SLUG: 1128.0})
    b = _frozen_long(p, ledger=0, reason="venue_ledger_disagree")
    v = _Venue(held={SLUG: 1128}, bid=0.30, ask=0.32)
    _tick(p, v)
    assert not [c for c in _places(v) if c[4] is False], _places(v)
    assert b["last_plan"].get("kind") != "increase"
    p2 = _pool(fills=_his(300, sold=300), snap=None, registered={SLUG: 1128.0})
    b2 = _frozen_long(p2, ledger=0, reason="venue_ledger_disagree")
    v2 = _Venue(held={SLUG: 1128}, bid=0.30, ask=0.32, ioc_fill=1128)
    _tick(p2, v2, http=_gone())
    _tick(p2, _Venue(held={SLUG: 1128}, bid=0.30, ask=0.32, ioc_fill=1128), now=NOW + 30, http=_gone())
    assert b2["state"] != "closed" and _places(v2)


def test_e5r_F4_a_standing_frozen_reduce_is_cancelled_when_the_plan_no_longer_wants_it():
    """A frozen reduce rest SELL 300 standing (he was at 300 against
    the venue's 600); this tick he is back at 600 (`frozen_no_his_exit`)
    -- or a live non-mirror row appears on the slug (`frozen_coheld`).
    A live book replaces a rest its plan no longer wants; the frozen
    exit returns before _act and leaves the rest standing at his old
    exit cent."""
    p = _pool(fills=_his(600), snap=None)
    b = _frozen_long(p)
    o = p.add_order(b, side=SELL, wire=0.31, qty=300, kind="reduce", reason="frozen_reduce",
                    his_level=0.31, placed_ts=NOW - 30)
    v = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32)
    v.rest("oid-1", "SELL", 0.31, 300)
    st = _tick(p, v, http=_mkt(600))
    assert _census(st, "frozen_no_his_exit") == 1 and _plan_exit(b)["target"] == 600
    assert _cancels(v) == [("cancel", "oid-1", SLUG)] and o["state"] == "cancelled"
