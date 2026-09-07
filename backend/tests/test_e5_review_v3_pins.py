"""E5 v3 review pins (2026-09-07, on 93e506b / L2): the re-check of E5
v2. Killers for the rails v2 states but no test exercised (F2's seat
move survived a mutant that deleted it: every F2 pin was satisfied by
F1's `frozen_fill_this_tick` refusal), the L2 interaction (the sleeve
breaker and the standing stop are increase-only: a frozen reduce
passes both), the brief's item-2 money path at a ledger that differs
from the plan, the register's option (b) after the sale, and the
findings of this review as STRICT xfails:

  V3-1 (MEDIUM) `_SQL_LOST_QTY` counts a lost SELL row's quantity, so
       a lost CLOSE -- the seven books' shape after P3 -- loosens the
       M1 bound by its own quantity and the book sells a surplus
       nobody explains.
  V3-2 (MEDIUM) after P3 the same book still writes one `frozen` row
       to _RECENT and re-counts `venue_ledger_disagree` EVERY tick
       (_freeze counts and emits whenever the second reason differs
       from the first, which on an already-frozen book is every tick).
  V3-3 (LOW) a transient failure of his per-market read (or of the
       co-hold read) cancels the standing frozen reduce and re-rests
       it next tick: the plan is unknown that tick, not unwanted.
  V3-4 (MEDIUM) `mirror-register` registers a book frozen under ANY
       reason; registered, it never thaws, and only placement_lost /
       venue_ledger_disagree books follow his exit -- a book registered
       under cancel_pending or lost_ambiguous is stuck for good.

Driven through workers.mirror_live.tick_once with the worker suite's
fakes and the E5 pools."""
import asyncio
import inspect
import pathlib
import re

import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e5_frozen_exits import _frozen_long, _placed, _plan_exit
from tests.test_e5_review_pins import _frozen_rest, _pool
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    BUY, CID, GTC_TIF, IOC_TIF, M, N, NOW, SELL, SHORT, SLUG, _Http, _NoClose, _Venue, _armed,
    _cancels, _census, _fill, _gone, _his, _kinds, _mkt, _places, _ratio_fills, _short_book,
    _shorts_on, _tick,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
YML = REPO / ".github" / "workflows" / "render-ops.yml"
SELL_SHORT = "ORDER_INTENT_SELL_SHORT"

XFAIL_V3_1 = "E5 v3 review V3-1 (MEDIUM): the M1 bound counts a lost SELL row's quantity"
XFAIL_V3_2 = "E5 v3 review V3-2 (MEDIUM): a frozen book re-emits and re-counts venue_ledger_disagree every tick"
XFAIL_V3_3 = "E5 v3 review V3-3 (LOW): a transient his-read failure cancels the standing frozen reduce"
XFAIL_V3_4 = "E5 v3 review V3-4 (MEDIUM): mirror-register accepts a book frozen under a reason the exit cannot follow"


class _LateFill(_Venue):
    """A rest that fills AFTER step O's status read and BEFORE the
    cancel's settle (the race F2 is about): `late` maps oid -> (filled,
    avg) and is applied on a status read only once the venue has seen
    a cancel of that order."""

    def __init__(self, *a, late=None, **kw):
        super().__init__(*a, **kw)
        self.late = dict(late or {})

    def order_status(self, oid):
        if oid in self.late and any(c[0] == "cancel" and c[1] == oid for c in self.calls):
            self.fills[oid] = self.late[oid]
        return super().order_status(oid)


class _LateNoClose(_LateFill, _NoClose):
    pass


# ------------------------------------------------ F1, each guard on its own

def test_e5v3_F1_a_frozen_reduce_filled_in_step_o_places_nothing_more_this_tick():
    """The `frozen_fill_this_tick` guard alone (the book was frozen
    before the tick, so the transition guard is not in play): the
    frozen reduce SELL 600 fills whole at step O's status read; the walk
    read 600 before it. Nothing is sized this tick -- a plan off the
    stale 600 with the bid inside the cent would send an IOC for 600 on
    a venue holding 0. Next tick venue 0 == ledger 0: thaw, close."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    o = _frozen_rest(p, b)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, ioc_fill=600, fills={"oid-1": (600.0, 0.31)})
    v.rest("oid-1", "SELL", 0.31, 600)
    st = _tick(p, v, http=_gone())
    assert o["state"] == "filled" and b["ledger_net"] == 0 and _census(st, "frozen_excess_sold") == 1
    assert not _places(v) and not _cancels(v) and _census(st, "frozen_fill_this_tick") == 1
    assert _plan_exit(b) == {"held": "frozen_fill_this_tick"} and b["state"] == "frozen"
    v2 = _Venue(held={SLUG: 0}, bid=0.30, ask=0.32)
    _tick(p, v2, now=NOW + 30, http=_gone())
    assert b["state"] == "closed" and not _places(v2)


def test_e5v3_F1_the_transition_tick_places_nothing_even_with_no_fill_booked():
    """The transition guard alone (nothing booked this tick, so the
    fill guard is not in play): a LIVE book, ledger 300, the walk reads
    600 with he gone. This tick it freezes `venue_ledger_disagree` and
    the plan says `held: transition_tick`, nothing placed; the next tick
    -- the disagreement standing -- sells the venue's 600 at his cent."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = p.add_book(ledger=300, avg_cost=0.31)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, ioc_fill=600)
    st = _tick(p, v, http=_gone())
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree"
    assert not _places(v) and _plan_exit(b) == {"held": "transition_tick"}
    assert _census(st, "frozen_reduce") == 0 and _census(st, "frozen_fill_this_tick") == 0
    assert _census(st, "venue_ledger_disagree") == 1 and b["ledger_net"] == 300
    v2 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, ioc_fill=600)
    st2 = _tick(p, v2, now=NOW + 30, http=_gone())
    assert [c[2:6] for c in _places(v2)] == [(0.30, 600, True, IOC_TIF)] and b["ledger_net"] == 0
    assert _census(st2, "frozen_reduce") == 1 and _plan_exit(b)["result"] == "take"


# ------------------------------------------------ F2, exercised on both legs

def test_e5v3_F2_a_fill_at_the_replaces_cancel_shrinks_the_long_re_rest_to_what_the_venue_holds():
    """Frozen long book, ledger 100, venue 600, a frozen reduce SELL 600
    at 0.31 standing; his level moved to 0.29 (replace). Step O read the
    rest unfilled; 200 filled at the cancel's settle. The re-plan reads
    the venue as the fill left it (400) and re-rests 400 at 0.29 --
    never the walk's 600 on a venue holding 400. (A mutant that drops
    the seat move in _book_fill re-rests 600.)"""
    fills = _his(300, sold=200) + [_fill(M, "SELL", 100, 0.29, NOW - 500)]
    p = _pool(fills=fills, snap=None)
    b = _frozen_long(p, ledger=100, lost=500)
    o = _frozen_rest(p, b)
    v = _LateFill(held={SLUG: 600}, bid=0.26, ask=0.32, late={"oid-1": (200.0, 0.31)})
    v.rest("oid-1", "SELL", 0.31, 600)
    st = _tick(p, v, http=_gone())
    assert _cancels(v) == [("cancel", "oid-1", SLUG)] and o["reason"] == "frozen_reduce: replace"
    assert b["ledger_net"] == 0 and _census(st, "frozen_fill_this_tick") == 0
    assert [c[2:6] for c in _places(v)] == [(0.29, 400, True, GTC_TIF)], _places(v)
    assert _plan_exit(b)["result"] == "rest_placed" and _census(st, "frozen_reduce") == 1
    assert "_frozen_venue" not in b


def test_e5v3_F2_a_fill_at_the_replaces_cancel_shrinks_the_short_re_cover_to_the_venues_short(monkeypatch):
    """Frozen short book, ledger -300, venue -600, a frozen cover BUY 600
    at 0.30 standing; his buy-back moved to 0.28 (his newest SELL of the
    other token at 0.72). 200 filled at the cancel's settle: the venue
    is short 400 and the re-cover is BUY 400 at 0.28 -- a re-cover of
    600 would buy 200 the account does not owe: a flip into a long."""
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 500, 0.72, NOW - 2500),
             _fill(N, "SELL", 400, 0.70, NOW - 2000), _fill(M, "SELL", 100, 0.31, NOW - 1000),
             _fill(N, "SELL", 100, 0.72, NOW - 500)]
    p = _pool(fills=fills, snap=None)
    b = _short_book(p, ledger=-300, state="frozen", frozen_reason="placement_lost", frozen_ts=NOW - 100)
    p.add_order(b, side=SELL, wire=0.32, qty=300, order_id=None, state="lost", placed_ts=NOW - 3000)
    o = p.add_order(b, side=BUY, wire=0.30, qty=600, kind="reduce", reason="frozen_reduce",
                    his_level=0.30, placed_ts=NOW - 30)
    v = _LateNoClose(held={SLUG: -600}, bid=0.30, ask=0.32, late={"oid-1": (200.0, 0.30)})
    v.rest("oid-1", "BUY", 0.30, 600, intent=SELL_SHORT)
    st = _tick(p, v, http=_gone())
    assert _cancels(v) == [("cancel", "oid-1", SLUG)] and o["reason"] == "frozen_reduce: replace"
    assert b["ledger_net"] == -100 and "close" not in _kinds(v)
    assert [c[1:] for c in _places(v)] == [(SLUG, 0.28, 400, True, GTC_TIF, SHORT, True, None)], _places(v)
    assert _plan_exit(b)["result"] == "rest_placed" and _census(st, "short_cover_rest") == 1
    assert b["last_plan"]["his_level"] == pytest.approx(0.28)


def test_e5v3_F2_the_seat_moves_by_the_leg_signed_fill_on_either_book():
    """The unit: a reduce fill moves a long book's seat down and a short
    book's seat up (toward zero); an add fill the other way."""
    long_b = {"id": 1, "intent": "ORDER_INTENT_BUY_LONG", "_frozen_venue": 600.0}
    short_b = {"id": 2, "intent": SHORT, "_frozen_venue": -600.0}
    src = inspect.getsource(ml._book_fill)
    assert 'book["_frozen_venue"] = fv + sign * inc' in src
    assert 'sign = 1.0 if action == "add" else -1.0' in src and "if _book_short(book):" in src
    # the clamps read the moved seat, never the walk's
    assert ml._sell_qty({**long_b, "_frozen_venue": 400.0, "ledger_net": 100}, 600) == 400
    assert ml._cover_qty({**short_b, "_frozen_venue": -400.0, "ledger_net": -100}, 600) == 400
    assert ml._plan_seat({**long_b, "_frozen_venue": 400.0}, None, {}) == (400.0, 400.0)


# ------------------------------------------------ L2: the increase-only rails

def _breaker(monkeypatch, total):
    async def _sum(pool, since=None):
        return float(total)
    monkeypatch.setattr(le, "_loss_breaker_sum", _sum)
    monkeypatch.setattr(le, "PMUS_LOSS_BREAKER_USD", 5000.0)


def test_e5v3_L2_the_sleeve_breaker_never_blocks_a_frozen_exit_and_step_o_keeps_its_rest(monkeypatch):
    """The sleeve's breaker tripped over the mirror's window (-6,000,
    no re-arm): `loss_breaker` on the census, and the frozen reduce
    still goes -- step O keeps the standing rest (the increase block
    cancels ADD rests alone), the bid inside the cent cancels it under
    `take` and sends the one IOC. With no rest standing the rest goes
    out under the same block."""
    _breaker(monkeypatch, -6000.0)
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    o = _frozen_rest(p, b)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, ioc_fill=600)
    v.rest("oid-1", "SELL", 0.31, 600)
    st = _tick(p, v, http=_gone())
    assert _census(st, "loss_breaker") >= 1 and _census(st, "mirror_loss_stop") == 0
    assert _cancels(v) == [("cancel", "oid-1", SLUG)] and o["reason"] == "frozen_reduce: take"
    assert [c[2:6] for c in _places(v)] == [(0.30, 600, True, IOC_TIF)] and b["ledger_net"] == 0
    assert _census(st, "frozen_reduce") == 1 and _plan_exit(b)["result"] == "take"
    p2 = _pool(fills=_his(300, sold=300), snap=None)
    b2 = _frozen_long(p2)
    v2 = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32)
    st2 = _tick(p2, v2, http=_gone())
    assert _census(st2, "loss_breaker") >= 1
    assert [c[2:6] for c in _places(v2)] == [(0.31, 600, True, GTC_TIF)] and _plan_exit(b2)["result"] == "rest_placed"


def test_e5v3_L2_a_standing_stop_key_holds_increases_only_and_the_frozen_reduce_passes(monkeypatch):
    """A standing `mirror_loss_stop` key (read first by _global_guards
    since L2, the sleeve then read over its full window under the
    threshold): `mirror_loss_stop` on the census, no `loss_breaker`, and
    the frozen book's reduce rests at his cent."""
    _breaker(monkeypatch, -100.0)
    p = _pool(fills=_his(300, sold=300), snap=None)
    p.state["mirror_loss_stop"] = {"at": "2026-09-07T16:00:00Z", "sum": -6000.0}
    b = _frozen_long(p)
    o = _frozen_rest(p, b)
    v = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32)
    v.rest("oid-1", "SELL", 0.31, 600)
    st = _tick(p, v, http=_gone())
    assert _census(st, "mirror_loss_stop") >= 1 and _census(st, "loss_breaker") == 0
    assert not _cancels(v) and o["state"] == "open" and _census(st, "open_order_pending") == 1
    assert _plan_exit(b)["result"] == "open_order_pending" and b["state"] == "frozen"
    # the source: the frozen path never reads the increase block
    src = inspect.getsource(ml._frozen_exit) + inspect.getsource(ml._frozen_refuse)
    assert "increase_block" not in src and "_increases_refusal" not in src
    ro = inspect.getsource(ml._reconcile_open)
    assert 'elif _order_action(o, book) == "add" and _increases_refusal(t, o["whale"]):' in ro


# ------------------------------------------------ the brief's item-2 money path

def test_e5v3_the_money_path_venue_300_ledger_100_his_300_to_0_sells_the_venues_300_at_his_cent():
    """Frozen placement_lost LONG book: ledger 100, the venue reports
    300 (a lost BUY of 200 filled unbooked), his net 300 -> 0. The
    reduce is 300 -- the VENUE's number, never the ledger's 100 nor the
    plan's target -- at his price within 1c (his SELL at 0.31: take
    cent 0.30) taken at once with the bid at 0.30; the fill books 100
    to the ledger and names 200 excess on the receipt; the next tick
    reads venue 0 == ledger 0, thaws and closes flat. With the bid at
    0.29 (outside the cent) the rest goes at 0.31 and nothing chases."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p, ledger=100, lost=200)
    v = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32, ioc_fill=300)
    st = _tick(p, v, http=_gone())
    assert [c[1:] for c in _places(v)] == [(SLUG, 0.30, 300, True, IOC_TIF, "ORDER_INTENT_BUY_LONG", False, None)]
    o = _placed(p)[0]
    assert o["qty"] == 300 and o["state"] == "filled" and o["reason"] == "frozen_reduce: take"
    assert b["ledger_net"] == 0 and o["receipt"]["frozen_excess"]["shares"] == pytest.approx(200.0)
    assert b["last_plan"]["his_level"] == pytest.approx(0.31) and b["last_plan"]["exit_take"] == 0.30
    assert abs(0.30 - 0.31) <= rules.MIRROR_EXIT_TOL + 1e-9
    fx = _plan_exit(b)
    assert (fx["venue_own"], fx["target"], fx["qty"], fx["result"]) == (300, 0, 300, "take")
    assert _census(st, "overfill") == 0 and p.state["mirror_live"] is True and st["books_live"] == 0
    v2 = _Venue(held={SLUG: 0}, bid=0.30, ask=0.32)
    _tick(p, v2, now=NOW + 30, http=_gone())
    assert b["state"] == "closed" and not _places(v2)
    # outside the cent: the rest at his cent, held by name
    p3 = _pool(fills=_his(300, sold=300), snap=None)
    b3 = _frozen_long(p3, ledger=100, lost=200)
    v3 = _Venue(held={SLUG: 300}, bid=0.29, ask=0.32, ioc_fill=300)
    st3 = _tick(p3, v3, http=_gone())
    assert [c[2:6] for c in _places(v3)] == [(0.31, 300, True, GTC_TIF)] and _census(st3, "exit_out_of_tol") == 1
    assert b3["ledger_net"] == 100 and _plan_exit(b3)["result"] == "rest_placed"


# ------------------------------------------------ P1: option (b) after the sale

def test_e5v3_option_b_after_the_sale_the_registered_book_stays_frozen_venue_flat_until_the_row_is_removed():
    """Book 77's shape registered (venue 1,128 / ledger 0 / register
    1,128), he is gone: the exit sells the 1,128; the next tick reads
    venue 0 against explained 1,128 -- `frozen_venue_flat`, frozen, not
    closed, nothing bought. The row removed by hand: venue 0 == ledger 0,
    the book thaws and closes flat."""
    p = _pool(fills=_his(300, sold=300), snap=None, registered={SLUG: 1128.0})
    b = _frozen_long(p, ledger=0, reason="venue_ledger_disagree")
    v = _Venue(held={SLUG: 1128}, bid=0.30, ask=0.32, ioc_fill=1128)
    _tick(p, v, http=_gone())
    assert [c[2:6] for c in _places(v)] == [(0.30, 1128, True, IOC_TIF)]
    v2 = _Venue(held={SLUG: 0}, bid=0.30, ask=0.32)
    st2 = _tick(p, v2, now=NOW + 30, http=_gone())
    assert not _places(v2) and b["state"] == "frozen" and _census(st2, "frozen_venue_flat") == 1
    assert _plan_exit(b)["held"] == "frozen_venue_flat" and b["last_plan"]["registered"] == 1128.0
    assert _census(st2, "closed_cancelled") + _census(st2, "closed_cashed_out") == 0
    p.registered = {}
    v3 = _Venue(held={SLUG: 0}, bid=0.30, ask=0.32)
    st3 = _tick(p, v3, now=NOW + 60, http=_gone())
    assert b["state"] == "closed" and not _places(v3)
    assert _census(st3, "closed_cancelled") + _census(st3, "closed_cashed_out") == 1


# ------------------------------------------------ the knob only lowers

def test_e5v3_e5_adds_one_environment_knob_and_it_can_only_turn_the_clause_off():
    rsrc = pathlib.Path(rules.__file__).read_text()
    assert rsrc.count('MIRROR_FROZEN_EXITS = env_switch("MIRROR_FROZEN_EXITS", True)') == 1
    assert len(re.findall(r'FROZEN_EXITS', rsrc)) == rsrc.count("MIRROR_FROZEN_EXITS")
    wsrc = pathlib.Path(ml.__file__).read_text()
    assert "MIRROR_FROZEN_EXITS" in wsrc
    for m in re.finditer(r"os\.environ[^\n]*", wsrc):
        assert "FROZEN" not in m.group(0) and "REGISTER" not in m.group(0), m.group(0)
    # off: the exit is read-only AND the standing frozen reduce is cancelled by step O
    assert ml._frozen_reduce_stands({"state": "frozen", "frozen_reason": "placement_lost",
                                     "intent": "ORDER_INTENT_BUY_LONG"},
                                    {"side": SELL, "reason": "frozen_reduce"}) is True


# ------------------------------------------------ the findings (strict xfails)

def test_e5v3_V3_1_a_lost_close_row_does_not_loosen_the_placement_lost_bound():
    """ledger 300, the only lost row a CLOSE (SELL 300, 'lost' -- the
    seven books after P3), the venue reporting 500: 200 nobody explains.
    The bound is the leg plus its lost ADD rows (300): refused
    `frozen_venue_unexplained`, nothing sold. v2 counts the SELL row
    and sells 500."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p, lost=0)
    p.add_order(b, side=SELL, wire=0.0, qty=300, kind="flatten_vanished", tif="CLOSE",
                order_id=None, state="lost", placed_ts=NOW - 3000)
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    assert asyncio.run(ml._lost_bound(t, b)) == 300
    v = _Venue(held={SLUG: 500}, bid=0.30, ask=0.32, ioc_fill=500)
    st = _tick(p, v, http=_gone())
    assert not _places(v) and _census(st, "frozen_venue_unexplained") == 1
    assert _plan_exit(b)["held"] == "frozen_venue_unexplained"


def test_e5v3_V3_2_after_the_lost_mark_the_book_re_emits_nothing_and_counts_no_new_transition():
    """The seven books' shape: the CLOSE row marked lost (tick 0), the
    remaining 100 sold toward his exit (tick 1). Ticks 2 and 3 read
    venue 0 against ledger 200 -- the same standing disagreement -- and
    must write NO `frozen` row to _RECENT and count NO
    `venue_ledger_disagree` (the W2 transition convention). v2 writes
    one row and counts one per tick, per book."""
    sell = {"side": "SELL", "ts": NOW - 1700, "order_qty": None, "order_price": None}
    trades = [{**sell, "order_id": "s-1", "qty": 120.0, "price": 0.29},
              {**sell, "order_id": "s-2", "qty": 80.0, "price": 0.29}]
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    o = p.add_order(b, side=SELL, wire=0.0, qty=300, kind="flatten_vanished", tif="CLOSE",
                    order_id=None, state="placing", placed_ts=NOW - 30 * 60)
    _tick(p, _Venue(held={SLUG: 100}, trades=trades), http=_gone())
    assert o["state"] == "lost"
    _tick(p, _Venue(held={SLUG: 100}, bid=0.30, ask=0.32, ioc_fill=100), now=NOW + 30, http=_gone())
    assert b["ledger_net"] == 200
    for i in (2, 3):
        ml._RECENT.clear()
        st = _tick(p, _Venue(held={SLUG: 0}, bid=0.30, ask=0.32), now=NOW + 30 * i, http=_gone())
        assert b["state"] == "frozen" and _plan_exit(b)["held"] == "frozen_venue_flat"
        assert not [x for x in ml._RECENT if x["what"] == "frozen"], list(ml._RECENT)
        assert _census(st, "venue_ledger_disagree") == 0


def test_e5v3_V3_3_a_transient_his_read_failure_leaves_the_frozen_reduce_standing():
    """A frozen reduce SELL 600 at his cent standing; this tick the data
    API answers 500 (`frozen_venue_unread`, why his_market_read): the
    plan is UNKNOWN, not unwanted -- the rest stands, as it stands on
    `frozen_fill_this_tick`. v2 cancels it and re-rests it next tick."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    o = _frozen_rest(p, b)
    v = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32)
    v.rest("oid-1", "SELL", 0.31, 600)
    st = _tick(p, v, http=_Http(status=500))
    assert _census(st, "frozen_venue_unread") == 1 and _plan_exit(b)["why"] == "his_market_read"
    assert not _cancels(v) and o["state"] == "open"


def test_e5v3_V3_4_the_register_preset_refuses_a_book_frozen_under_a_reason_the_exit_cannot_follow():
    """A registered book never thaws (option b) and only placement_lost
    / venue_ledger_disagree books follow his exit, so registering a book
    frozen under any other reason leaves it with no way out. The
    preset's INSERT must carry the eligibility predicate. And driven: a
    registered book frozen cancel_pending with venue == ledger +
    register is held `reason_not_eligible`, frozen, for good."""
    text = YML.read_text()
    reg = text[text.index("mirror-register) need_confirm"):text.index("mirror-frozen) SQL=")]
    assert "b.frozen_reason IN ('placement_lost', 'venue_ledger_disagree')" in reg
