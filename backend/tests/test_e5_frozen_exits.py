"""E5 (2026-09-07): frozen books follow his exits; the operator register;
a lost close marked lost once. Owner question 16:5xZ ("when he wins we
win, when he loses we lose?"): eight books were frozen (placement_lost
7, one venue_ledger_disagree -- book 77, venue 1,128 / ledger 0), and a
frozen book cancelled its orders, wrote a plan of kind 'frozen' and
held to settlement while he sold.

Driven end to end through workers.mirror_live.tick_once with the
worker suite's fakes (tests.test_mirror_live_worker: the in-memory
pool, the venue, the autouse rails). The pool here answers three
statements the worker suite's fake leaves unmodelled (the register
read, the co-hold read, the receipt merge) so the frozen exit can be
driven; against the unmodelled fake the exit refuses `frozen_venue_
unread` by name -- which is why every older frozen pin still holds.

P2, the money path of one placement_lost LONG book (ledger 300, venue
600: the lost BUY's response was lost and its 300 filled unbooked): he
reduces to 100 -> the plan is SELL 500 off the VENUE (never 200 off the
ledger), the E4 exit at his price (the IOC at his cent the tick the bid
is there), the fill books 300 to the ledger and names the other 200
`frozen_excess_sold` on the row's receipt -- never the overfill trip
-- and the next tick reads venue 100 against his 100: `frozen_no_his_
exit`. One SHORT book (ledger -300, venue -600): the cover is a priced
BUY of the long token with the closing intent through _act's S4 gate,
rested at floor(his) outside the ceiling, taken at the ceiling cent the
tick the ask arrives, clamped on the venue's short, never on the leg.
"""
import asyncio
import inspect
import json
import pathlib
import re

import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.scripts import migrate
from sportsassets.workers import mirror_live as ml
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    BUY, CID, GTC_TIF, IOC_TIF, M, N, NOW, SELL, SHORT, SLUG, _Http, _NoClose, _Pool, _Venue,
    _armed, _cancels, _census, _fill, _gone, _his, _kinds, _mkt, _places, _ratio_fills,
    _s4_unproven, _short_book, _shorts_on, _tick,
)

SELL_SHORT = "ORDER_INTENT_SELL_SHORT"
REPO = pathlib.Path(__file__).resolve().parents[2]
YML = REPO / ".github" / "workflows" / "render-ops.yml"
MIG_DIR = pathlib.Path(migrate.MIGRATIONS_DIR)
SQL_056 = MIG_DIR / "056_mirror_registered_positions.sql"
REGISTER_COLUMNS = {"id", "whale", "us_market_slug", "condition_id", "asset", "shares", "side",
                    "registered_at", "registered_by", "note", "source"}


def _flat(s: str) -> str:
    return " ".join(s.split())


class _E5Pool(_Pool):
    """The worker suite's pool plus the three statements E5 reads or
    writes: the register's signed sum per slug (`registered`, or a
    raise), the co-hold read (`coheld`: True / False / None for an
    unreadable answer) and the receipt merge."""

    def __init__(self, *a, coheld=False, registered=None, register_raises=False, **kw):
        super().__init__(*a, **kw)
        self.coheld = coheld
        self.registered = dict(registered or {})
        self.register_raises = register_raises
        self.receipts = []
        self.coheld_reads = 0

    def _run(self, kind, sql, a):
        s = _flat(sql)
        for needle, exc in self.raise_on:      # the worker suite's raise_on, for these tags too
            if needle in s:
                raise exc
        if "ml-registered-shares" in s:
            self.sent.append((kind, s, a))
            if self.register_raises:
                raise RuntimeError('relation "mirror_registered_positions" does not exist')
            return self.registered.get(a[0], 0.0)
        if "ml-slug-coheld" in s:
            self.coheld_reads += 1
            return self.coheld
        if "ml-lost-qty" in s:
            # the worker's predicate: the book's 'lost' rows and its
            # 'placing' rows whose response was lost (no order id) --
            # each with its side (E5 review v3, V3-1: the worker keeps
            # only the rows that ADD to the leg)
            return [{"side": o["side"], "qty": float(o["qty"])} for o in self.orders.values()
                    if o["book_id"] == a[0] and (o["state"] == "lost" or
                                                 (o["state"] == "placing" and o["order_id"] is None))]
        if "ml-order-receipt" in s:
            o = self.orders[a[0]]
            o["receipt"] = {**(o.get("receipt") or {}), **json.loads(a[1])}
            self.receipts.append((a[0], json.loads(a[1])))
            return "UPDATE 1"
        return super()._run(kind, sql, a)


def _pool(**kw):
    kw.setdefault("fills", _his())
    kw.setdefault("snap", {M: 300.0, N: 0.0})
    kw.setdefault("snap_at", NOW - 40)
    kw.setdefault("ratio_fills", _ratio_fills())
    return _E5Pool(**kw)


def _frozen_long(p, ledger=300, reason="placement_lost", lost=300, **over):
    """A frozen long book of `ledger` shares at 0.31. A placement_lost
    book carries the row whose response was lost -- a BUY of `lost`
    shares in state 'lost' -- the surplus the venue reports stands on
    (E5 review M1: a placement_lost book sells no more than its ledger
    plus what its lost rows asked for); `lost=0` leaves it out."""
    b = p.add_book(ledger=ledger, avg_cost=(0.31 if ledger else None), state="frozen",
                   frozen_reason=reason, frozen_ts=NOW - 100, **over)
    if lost and reason == "placement_lost":
        p.add_order(b, side=BUY, wire=0.30, qty=lost, order_id=None, state="lost", placed_ts=NOW - 3000)
    return b


def _placed(p):
    """The orders the tick placed, id ascending: never the fixture's lost row."""
    return [o for o in sorted(p.orders.values(), key=lambda o: o["id"]) if o["state"] != "lost"]


def _recent(what):
    return [x for x in ml._RECENT if x["what"] == what]


def _plan_exit(b):
    return (b.get("last_plan") or {}).get("frozen_exit") or {}


# ------------------------------------------------ P2: the long book's exits

def test_e5_a_frozen_long_book_sells_toward_his_exit_sized_on_the_venue_and_names_the_excess():
    """ledger 300, venue 600 (the lost BUY filled 300 unbooked), he
    reduced 300 -> 100: SELL 500 off the venue -- a ledger-sized reduce
    would be 200 -- at his cent, filled: 300 book to the ledger, 200 are
    `frozen_excess_sold` on the receipt, no overfill, no trip. Next tick
    venue 100 against his 100: `frozen_no_his_exit`, nothing placed.

    RE-PINNED AT E31 (FILL lane 31, 2026-09-10). The sizing, the excess and
    the trip are the subject and are untouched; HOW the 500 leave is not:
    the bid being at his take cent used to send ONE IOC at 0.30, and the
    frozen exit is now a POST-ONLY REST like every other order this worker
    sends -- SELL 500 at the maker wire max(sell_wire(0.31), bid 0.30 + a
    tick) = 0.31, GTC, post_only True. The fill is a TAKER lifting it
    inside the create's window (`lift`, the fixture's post-only stand-in
    for `ioc_fill`), so the row is kind 'reduce' / tif GTC / reason
    'frozen_reduce: reduce' where it read 'take' / 'IOC' / 'frozen_reduce:
    take', and the excess is priced at the cent we actually sold at: 200
    shares at 0.31 = $62.00, where the IOC's cent gave $60.00. The census
    follows: `exit_take` and `filled_take` are 0 (the first retired, the
    second reserved for a rest the venue fills as a TAKER -- here the
    venue's own `aggressor` is False, so it is `filled_rest` beside
    `maker_fill_at_create`), and the plan's result is 'filled_at_create'
    where it read 'take'."""
    p = _pool(fills=_his(300, sold=200), snap=None)
    b = _frozen_long(p)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, lift=500)
    st = _tick(p, v, http=_mkt(100))
    assert [c[1:] for c in _places(v)] == [(SLUG, 0.31, 500, True, GTC_TIF, "ORDER_INTENT_BUY_LONG",
                                            True, None)]
    o = _placed(p)[0]
    assert (o["kind"], o["side"], o["tif"], o["reason"], o["qty"], o["state"]) == (
        "reduce", SELL, "GTC", "frozen_reduce: reduce", 500, "filled")
    assert b["ledger_net"] == 0 and b["state"] == "frozen" and b["frozen_reason"] == "placement_lost"
    assert o["receipt"]["frozen_excess"]["shares"] == pytest.approx(200.0)
    assert o["receipt"]["frozen_excess"]["usd"] == pytest.approx(62.0)
    assert o["receipt"]["frozen_excess"]["px"] == pytest.approx(0.31)
    assert o["maker"] is True and o["taker_at_placement"] is False
    assert p.state["mirror_live"] is True and "mirror_live_trip" not in p.state
    assert _census(st, "overfill") == 0 and _census(st, "frozen_excess_sold") == 1
    assert _census(st, "frozen_reduce") == 1 and _census(st, "exit_take") == 0
    assert _census(st, "filled_take") == 0 and _census(st, "filled_rest") == 1
    assert _census(st, "rest_placed") == 1 and _census(st, "maker_fill_at_create") == 1
    assert st["books_live"] == 0 and st["books_frozen"] == 1
    fx = _plan_exit(b)
    assert (fx["venue_own"], fx["target"], fx["side"], fx["qty"], fx["result"]) == (
        600, 100, SELL, 500, "filled_at_create")
    assert b["last_plan"]["kind"] == "frozen" and b["last_plan"]["exit_px_src"] == "his_fill"
    assert b["last_plan"]["exit_take"] == 0.30 and b["last_plan"]["his_level"] == pytest.approx(0.31)
    assert _recent("frozen_reduce")[-1]["venue_own"] == 600 and _recent("frozen_excess_sold")[-1]["shares"] == 200
    # the next tick: the venue holds 100, he holds 100 -- read only
    v2 = _Venue(held={SLUG: 100}, bid=0.30, ask=0.32, lift=100)
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(100))
    assert not _places(v2) and _census(st2, "frozen_no_his_exit") == 1 and _census(st2, "frozen_reduce") == 0
    assert _plan_exit(b)["held"] == "frozen_no_his_exit" and b["state"] == "frozen"


def test_e5_book_77_shape_a_zero_ledger_book_sells_the_venue_position_when_he_is_gone_then_closes_flat():
    """venue 128 / ledger 0 (book 77's shape, scaled), he is gone: SELL
    128 at his cent, booked as excess (the ledger held nothing), no
    trip; the next tick reads venue 0 == ledger 0, thaws, and the flat
    book closes as any flat book does.

    RE-PINNED AT E31: the 128 leave as a post-only rest at the maker wire
    0.31 lifted by a taker, never an IOC at 0.30 (see the P2 test above);
    the excess is the same 128 shares at the cent they sold at."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p, ledger=0, reason="venue_ledger_disagree")
    v = _Venue(held={SLUG: 128}, bid=0.30, ask=0.32, lift=128)
    st = _tick(p, v, http=_gone())
    assert [c[2:6] for c in _places(v)] == [(0.31, 128, True, GTC_TIF)] and _places(v)[0][7] is True
    o = _placed(p)[0]
    assert o["state"] == "filled" and o["receipt"]["frozen_excess"]["shares"] == pytest.approx(128.0)
    assert b["ledger_net"] == 0 and p.state["mirror_live"] is True
    assert _census(st, "frozen_excess_sold") == 1 and _census(st, "overfill") == 0
    v2 = _Venue(held={SLUG: 0}, bid=0.30, ask=0.32)
    st2 = _tick(p, v2, now=NOW + 30, http=_gone())
    assert not _places(v2) and b["state"] == "closed"
    assert _census(st2, "closed_cancelled") + _census(st2, "closed_cashed_out") == 1


def test_e5_the_reduce_is_sized_on_the_venue_not_the_ledger_when_the_venue_holds_less():
    """ledger 300, venue 200 (the lost close sold 100 the ledger never
    booked), he is gone: SELL 200 -- the venue's number -- never 300.
    The fill books 200 to the ledger (no excess); the next tick reads
    venue 0 and holds `frozen_venue_flat` with the stale ledger for a
    human, never a sale of shares the venue does not hold.

    RE-PINNED AT E31: the 200 leave as a post-only rest at 0.31 lifted by a
    taker, never an IOC at 0.30 (see the P2 test above); the sizing, the
    booking and the next tick's hold are the subject and are unchanged."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    v = _Venue(held={SLUG: 200}, bid=0.30, ask=0.32, lift=200)
    st = _tick(p, v, http=_gone())
    assert [c[2:6] for c in _places(v)] == [(0.31, 200, True, GTC_TIF)] and _places(v)[0][7] is True
    assert b["ledger_net"] == 100 and _census(st, "frozen_excess_sold") == 0 and _census(st, "overfill") == 0
    assert "frozen_excess" not in (_placed(p)[0]["receipt"] or {})
    v2 = _Venue(held={SLUG: 0}, bid=0.30, ask=0.32)
    st2 = _tick(p, v2, now=NOW + 30, http=_gone())
    assert not _places(v2) and _census(st2, "frozen_venue_flat") == 1
    assert b["state"] == "frozen" and b["ledger_net"] == 100 and _plan_exit(b)["held"] == "frozen_venue_flat"


def test_e5_a_frozen_book_never_places_a_buy_that_increases_and_a_non_reduce_plan_is_refused_by_name(monkeypatch):
    """He holds 300 while the venue holds 200 for us: nothing is bought
    (`frozen_no_his_exit`). And the belt and braces: a plan that came
    back a BUY on a long book is `frozen_reduce_only`, never sent."""
    p = _pool()
    b = _frozen_long(p)
    v = _Venue(held={SLUG: 200}, bid=0.30, ask=0.32)
    st = _tick(p, v)
    assert not _places(v) and _census(st, "frozen_no_his_exit") == 1 and _census(st, "rest_placed") == 0
    assert b["state"] == "frozen"
    # the mutant: a BUY plan out of the seat -> refused, nothing sent
    p2 = _pool(fills=_his(300, sold=300), snap=None)
    b2 = _frozen_long(p2)
    orig = mi.plan

    def _buy(target, ledger, venue, book, his, mark):
        pl = orig(target, ledger, venue, book, his, mark)
        return mi.Plan(BUY, 100, 0.30, "increase toward target") if pl.side == SELL else pl
    monkeypatch.setattr(ml.mi, "plan", _buy)
    v2 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32)
    st2 = _tick(p2, v2, http=_gone())
    assert not _places(v2) and _census(st2, "frozen_reduce_only") == 1
    assert _plan_exit(b2)["held"] == "frozen_reduce_only" and b2["state"] == "frozen"


def test_e5_the_frozen_exit_is_read_only_when_the_venue_or_his_market_read_is_missing():
    """r.venue None / no positions walk: `frozen_venue_unread` (the
    tick that could not walk the account abandons before any book, as
    before; the unit refuses by name). His per-market read failing this
    tick refuses the same name with `why: his_market_read`; the co-hold
    read unreadable too (`coheld_unreadable`)."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    st = _tick(p, _Venue(held={SLUG: 600}, raise_walk=True), http=_gone())
    assert st["abandoned"] and _census(st, "positions_unreadable") == 1 and not _placed(p)
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    r = ml._Reading("rn1", CID, SLUG, M, N, [], 0.0, 0.0, {}, None, False, False, False, None, None,
                    0.30, 0.32, 0.31, 600.0, 0.0, None, True, snap_market_fresh=True)
    plan: dict = {}
    assert t.positions is None
    out = asyncio.run(ml._frozen_exit(t, b, r, 0, [], 0.0, plan))
    assert out == "frozen_venue_unread" and plan["frozen_exit"] == {"held": "frozen_venue_unread", "why": "venue_positions"}
    # his side not read this tick (the data API down): read only, by name
    p2 = _pool(fills=_his(300, sold=300), snap=None)
    _frozen_long(p2)
    v2 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32)
    st2 = _tick(p2, v2, http=_Http(status=500))
    assert not _places(v2) and _census(st2, "frozen_venue_unread") == 1
    assert _plan_exit(p2.books[next(iter(p2.books))])["why"] == "his_market_read"
    # the co-hold read unreadable: the venue's number cannot be attributed
    p3 = _pool(fills=_his(300, sold=300), snap=None, coheld=None)
    b3 = _frozen_long(p3)
    v3 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32)
    st3 = _tick(p3, v3, http=_gone())
    assert not _places(v3) and _census(st3, "frozen_venue_unread") == 1
    assert _plan_exit(b3) == {"held": "frozen_venue_unread", "why": "coheld_unreadable"}


def test_e5_a_co_held_slug_refuses_the_frozen_exit_by_name():
    """A live non-mirror row on the slug (the copy lane's per-fill
    position): the venue's number is not the book's alone -- refused
    `frozen_coheld`, nothing sold under this book. The read is the
    worker's own statement: live, non-mirror, not the desk's manual."""
    p = _pool(fills=_his(300, sold=300), snap=None, coheld=True)
    b = _frozen_long(p)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32)
    st = _tick(p, v, http=_gone())
    assert not _places(v) and _census(st, "frozen_coheld") == 1 and p.coheld_reads == 1
    assert _plan_exit(b) == {"held": "frozen_coheld"}
    s = _flat(ml._SQL_SLUG_COHELD)
    assert "COALESCE(lane, '') <> 'mirror'" in s
    # E5 review M1: a manual row in a live state the manual read does not
    # count ('submitting') is co-held too
    assert "(COALESCE(whale_username, '') <> 'manual' OR status NOT IN ('filled', 'exiting'))" in s
    assert "status IN ('filled', 'submitting', 'exiting')" in s and "ml-slug-coheld" in s


def test_e5_the_knob_only_lowers_the_rail_off_is_read_only_and_the_default_is_on(monkeypatch):
    assert rules.MIRROR_FROZEN_EXITS is True
    src = pathlib.Path(rules.__file__).read_text()
    assert 'MIRROR_FROZEN_EXITS = env_switch("MIRROR_FROZEN_EXITS", True)' in src
    assert rules.env_switch("MIRROR_FROZEN_EXITS", True) is True
    monkeypatch.setenv("MIRROR_FROZEN_EXITS", "off")
    assert rules.env_switch("MIRROR_FROZEN_EXITS", True) is False
    monkeypatch.setenv("MIRROR_FROZEN_EXITS", "banana")
    assert rules.env_switch("MIRROR_FROZEN_EXITS", True) is True
    monkeypatch.setattr(rules, "MIRROR_FROZEN_EXITS", False)
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32)
    st = _tick(p, v, http=_gone())
    assert not _places(v) and _census(st, "frozen_exits_off") == 1 and p.coheld_reads == 0
    assert _plan_exit(b) == {"held": "frozen_exits_off"} and b["last_plan"]["kind"] == "frozen"


@pytest.mark.parametrize("reason", ["cancel_pending", "overfill", "lost_ambiguous", "row_not_live",
                                    "order_state_unknown", "write_failed"])
def test_e5_only_placement_lost_and_venue_ledger_disagree_follow_his_exit(reason):
    """Every other freeze does what it did: reads, cancels, places
    nothing; the plan says the reason was not eligible, no census name."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p, reason=reason)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32)
    st = _tick(p, v, http=_gone())
    assert not _places(v) and b["state"] == "frozen"
    assert _plan_exit(b) == {"held": "reason_not_eligible", "reason": reason}
    for k in ("frozen_reduce", "frozen_no_his_exit", "frozen_venue_flat", "frozen_reduce_only",
              "frozen_coheld", "frozen_venue_unread", "frozen_exits_off"):
        assert _census(st, k) == 0, k
    assert ml.FROZEN_EXIT_REASONS == frozenset({"placement_lost", "venue_ledger_disagree"})


def test_e5_the_loss_stop_never_blocks_a_frozen_exit_and_the_reduce_path_reads_no_increase_refusal():
    """The loss stop (and every increase refusal) gates increases
    ONLY: `_tick_book` consults `inc_refusal` on the add action alone
    and `_frozen_exit` never reads it. Driven: the stop tripped, the
    frozen book still sells toward his exit."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    p.state["mirror_loss_stop"] = {"at": "2026-09-07T16:00:00Z", "sum": -600.0}
    b = _frozen_long(p)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, lift=600)
    st = _tick(p, v, http=_gone())
    assert _census(st, "mirror_loss_stop") >= 1
    # E31: the sale is the post-only rest at the maker wire 0.31 lifted by a
    # taker, never the IOC at 0.30 -- the gate under test is unchanged
    assert [c[2:6] for c in _places(v)] == [(0.31, 600, True, GTC_TIF)] and b["ledger_net"] == 0
    src = inspect.getsource(ml._frozen_exit)
    assert "_increases_refusal" not in src and "_short_open_refusal" not in src
    tb = inspect.getsource(ml._tick_book)
    assert 'if action == "add":' in tb and 'if inc_refusal:' in tb
    assert tb.index("inc_refusal = _increases_refusal(t, w)") > tb.index("await _frozen_exit(")
    ir = inspect.getsource(ml._increases_refusal)
    assert "INCREASE" in ir and "reduced" in ir


def test_e5_the_frozen_reduce_rest_stands_across_ticks_and_is_filled_where_it_stands():
    """Outside the cent the reduce RESTS at his cent (post-only, GTC, no
    good-till) and STANDS on the frozen book: step O keeps it
    (`_frozen_reduce_stands`), the freeze's cancel skips it, the next
    tick re-plans against it and keeps it (`open_order_pending`, no
    cancel, no second rest).

    RE-PINNED AT E31 (FILL lane 31, 2026-09-10). The third tick used to
    cancel that rest under `take` and cross with ONE IOC at the take cent
    the moment the bid arrived. That is the trade this lane exists to stop:
    the bid arriving at our cent is the TAKER COMING TO US, and the rest is
    now filled WHERE IT STANDS -- no cancel, no second order, the same 600
    shares, and the spread ours instead of theirs. So the third tick scripts
    the venue filling the standing rest (`fills`, the status read's own
    shape) and pins what the book does with that fill: `filled_rest` and
    `frozen_fill_this_tick`, the 300 excess booked at the rest's own cent
    0.31 ($93.00), the ledger to 0 and no trip.

    The rest's own cent moves with the lane: his cent 0.31 is now the maker
    wire max(sell_wire(0.31), bid 0.28 + a tick) = 0.31 -- the same cent it
    always rested at, reached by the maker rule."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    v = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32)
    st = _tick(p, v, http=_gone())
    assert [c[1:] for c in _places(v)] == [(SLUG, 0.31, 600, True, GTC_TIF, "ORDER_INTENT_BUY_LONG", True, None)]
    o = _placed(p)[0]
    assert (o["kind"], o["reason"], o["state"], o["good_till"], o["qty"]) == ("reduce", "frozen_reduce", "open", None, 600)
    assert _census(st, "frozen_reduce") == 1 and _census(st, "rest_placed") == 1 and _census(st, "exit_out_of_tol") == 1
    assert ml._frozen_reduce_stands(b, o) is True
    assert ml._frozen_reduce_stands({**b, "state": "live"}, o) is False
    assert ml._frozen_reduce_stands({**b, "frozen_reason": "overfill"}, o) is False
    assert ml._frozen_reduce_stands(b, {**o, "reason": "reduce"}) is False
    assert ml._frozen_reduce_stands(b, {**o, "side": BUY}) is False
    v2 = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32)
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_gone())
    assert not _cancels(v2) and not _places(v2) and o["state"] == "open"
    assert _census(st2, "open_order_pending") == 1 and _census(st2, "frozen_reduce") == 0
    assert b["state"] == "frozen" and _plan_exit(b)["result"] == "open_order_pending"
    # the tick the taker comes to the rest: it fills WHERE IT STANDS
    v3 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, fills={"oid-1": (600.0, 0.31)})
    v3.orders = v.orders
    st3 = _tick(p, v3, now=NOW + 60, http=_gone())
    assert not _cancels(v3) and not _places(v3), "no cancel and no second order: the rest was hit"
    assert o["state"] == "filled" and o["reason"] == "frozen_reduce: frozen_reduce" and o["maker"] is True
    assert o["booked_filled"] == 600.0 and o["tif"] == "GTC" and o["wire"] == 0.31
    assert b["ledger_net"] == 0 and _census(st3, "frozen_excess_sold") == 1 and p.state["mirror_live"] is True
    assert _census(st3, "filled_rest") == 1 and _census(st3, "frozen_fill_this_tick") == 1
    assert _census(st3, "filled_take") == 0 and _plan_exit(b) == {"held": "frozen_fill_this_tick"}
    assert o["receipt"]["frozen_excess"]["shares"] == pytest.approx(300.0)
    assert o["receipt"]["frozen_excess"]["px"] == pytest.approx(0.31)
    assert o["receipt"]["frozen_excess"]["usd"] == pytest.approx(93.0)
    assert [x for x in p.orders.values() if x["tif"] == "IOC"] == [], "no IOC on any tick"


def test_e5_any_other_rest_on_a_frozen_book_is_still_cancelled_under_the_freeze_and_the_lost_row_blocks_a_placement():
    """A rest that is not the frozen exit's own -- a live reduce sized
    on the ledger, an add -- is cancelled under the freeze's name as
    before. And the placement_lost row still 'placing' inside the window
    keeps the book's order slot: nothing is placed (`open_order_pending`,
    the one-open-per-book index), the book waits as before."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    o = p.add_order(b, side=SELL, wire=0.31, qty=300, kind="reduce")
    v = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32)
    v.rest("oid-1", "SELL", 0.31, 300)
    st = _tick(p, v, http=_gone())
    assert _cancels(v) == [("cancel", "oid-1", SLUG)] and o["state"] == "cancelled" and o["reason"] == "placement_lost"
    # the cancel cleared the slot: the venue-sized reduce rests after it
    assert [c[2:6] for c in _places(v)] == [(0.31, 600, True, GTC_TIF)] and _census(st, "frozen_reduce") == 1
    p2 = _pool(fills=_his(300, sold=300), snap=None)
    b2 = _frozen_long(p2)
    o2 = p2.add_order(b2, side=BUY, wire=0.30, qty=300, order_id=None, state="placing", placed_ts=NOW - 90)
    v2 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32)
    st2 = _tick(p2, v2, http=_gone())
    assert not _places(v2) and o2["state"] == "placing" and b2["state"] == "frozen"
    assert _census(st2, "open_order_pending") >= 1 and _plan_exit(b2)["result"] == "open_order_pending"


def test_e5_a_frozen_reduce_adopted_after_a_lost_response_keeps_its_marker():
    """The reduce's own response lost, the rest standing on the venue:
    step O adopts it by fingerprint and the marker rides the reason, so
    its fill past the ledger still books as `frozen_excess_sold`."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    o = p.add_order(b, side=SELL, wire=0.31, qty=600, kind="reduce", reason="frozen_reduce",
                    order_id=None, state="placing", placed_ts=NOW - 90, his_level=0.31)
    v = _Venue(held={SLUG: 600}, bid=0.28, ask=0.32)
    v.rest("lost-7", "SELL", 0.31, 600, created=NOW - 85)
    st = _tick(p, v, http=_gone())
    assert o["order_id"] == "lost-7" and o["state"] == "open" and o["reason"] == "frozen_reduce: adopted by fingerprint"
    assert not _cancels(v) and not _places(v) and _census(st, "open_order_pending") == 1
    assert ml._adopt_reason({"reason": "reduce"}, "adopted by fingerprint") == "adopted by fingerprint"
    assert ml._frozen_excess({"reason": "frozen_reduce: adopted by fingerprint", "qty": 600}, b, "reduce", 600.0)
    assert not ml._frozen_excess({"reason": "frozen_reduce", "qty": 600}, b, "reduce", 601.5), "more than asked: the overfill"
    assert not ml._frozen_excess({"reason": "frozen_reduce", "qty": 600}, b, "add", 600.0)
    assert not ml._frozen_excess({"reason": "reduce", "qty": 600}, b, "reduce", 600.0)


# ------------------------------------------------ P2: the short book's cover

def _short_fills():
    """His net negative, then gone: his buy-back of the other token at
    0.70 (0.30 in long space) is the cover's price."""
    return [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
            _fill(N, "SELL", 400, 0.70, NOW - 2000), _fill(M, "SELL", 100, 0.31, NOW - 1000)]


def _frozen_short(p, venue=-600, lost=300, **venue_kw):
    """A frozen short book of 300 with the lost BUY_SHORT add row of
    `lost` shares the venue's larger short stands on (M1)."""
    b = _short_book(p, ledger=-300, state="frozen", frozen_reason="placement_lost", frozen_ts=NOW - 100)
    if lost:
        p.add_order(b, side=SELL, wire=0.32, qty=lost, order_id=None, state="lost", placed_ts=NOW - 3000)
    venue_kw.setdefault("held", {SLUG: venue})
    return b, _NoClose(**venue_kw)


def test_e5_a_frozen_short_books_cover_goes_through_s4_rests_at_floor_his_and_takes_at_the_ceiling_clamped_on_the_venue(monkeypatch):
    """ledger -300, venue -600 (a lost BUY_SHORT add filled 300
    unbooked), he is gone: the cover is a BUY of the long token with
    the closing intent (SELL_SHORT on the wire) for 600 -- the venue's
    short, never the leg's 300 -- rested post-only at floor(his) = 0.30
    while the ask (0.32) is outside the ceiling (0.31).

    RE-PINNED AT E31 (FILL lane 31, 2026-09-10). The next tick used to
    cancel that rest and take ONE IOC at the ceiling 0.31 the moment the
    ask arrived. The cover is a maker's rest for as long as the leg is
    held: the ask coming to 0.31 is a seller reaching our 0.30 bid, and the
    rest is filled WHERE IT STANDS -- no cancel, no IOC, the same 600, and
    the cover costs 0.30 a share instead of 0.31 (the excess $90.00 where
    it was $93.00). So the second tick scripts the venue filling the
    standing rest and pins the book's answer: 300 book the leg to zero
    (`short_flatten_close`), 300 are excess, no trip. close_position is
    never called; _short_open_refusal never runs (a cover is not an
    open)."""
    _shorts_on(monkeypatch)

    async def _never(t):
        raise AssertionError("_short_open_refusal on a cover")
    monkeypatch.setattr(ml, "_short_open_refusal", _never)
    p = _pool(fills=_short_fills(), snap=None)
    b, v = _frozen_short(p, bid=0.30, ask=0.32)
    st = _tick(p, v, http=_gone())
    assert "close" not in _kinds(v)
    assert [c[1:] for c in _places(v)] == [(SLUG, 0.30, 600, True, GTC_TIF, SHORT, True, None)]
    o = _placed(p)[0]
    assert (o["kind"], o["side"], o["intent"], o["reason"], o["wire"], o["qty"], o["state"]) == (
        "reduce", BUY, SELL_SHORT, "frozen_reduce", 0.30, 600, "open")
    assert _census(st, "short_cover_rest") == 1 and _census(st, "frozen_reduce") == 1
    assert _census(st, "short_cover_out_of_tol") == 1 and b["ledger_net"] == -300
    lp = b["last_plan"]
    assert lp["exit_px"] == pytest.approx(0.30) and lp["exit_ceiling"] == pytest.approx(0.31)
    assert lp["exit_cover"] == 0.31 and lp["exit_rest"] == 0.30 and lp["kind"] == "frozen"
    assert _plan_exit(b)["venue_own"] == -600 and _plan_exit(b)["target"] == 0 and _plan_exit(b)["side"] == BUY
    v2 = _NoClose(bid=0.30, ask=0.31, held={SLUG: -600}, fills={"oid-1": (600.0, 0.30)})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_gone())
    assert not _cancels(v2) and not _places(v2), "no cancel and no IOC: the seller came to the rest"
    assert (o["intent"], o["reason"], o["state"], o["wire"]) == (
        SELL_SHORT, "frozen_reduce: frozen_reduce", "filled", 0.30)
    assert o["maker"] is True and o["booked_filled"] == 600.0
    assert b["ledger_net"] == 0 and o["receipt"]["frozen_excess"]["shares"] == pytest.approx(300.0)
    assert o["receipt"]["frozen_excess"]["px"] == pytest.approx(0.30)
    assert o["receipt"]["frozen_excess"]["usd"] == pytest.approx(90.0)
    assert _census(st2, "short_cover_take") == 0 and _census(st2, "short_flatten_close") == 1
    assert _census(st2, "filled_rest") == 1 and _census(st2, "frozen_fill_this_tick") == 1
    assert _census(st2, "frozen_excess_sold") == 1 and _census(st2, "overfill") == 0
    assert [x for x in p.orders.values() if x["tif"] == "IOC"] == [], "no IOC on any tick"
    assert p.state["mirror_live"] is True and "close" not in _kinds(v2)


def test_e5_a_frozen_short_books_cover_is_refused_unproven_exactly_as_a_live_one(monkeypatch):
    """The S4 gate in front of every cover: with the read-back proof
    not passed the frozen book's partial reduce is `short_reduce_unproven`
    and nothing is sent; the sign flip / a flatten would be `s4_unproven`."""
    _shorts_on(monkeypatch)
    p = _pool(fills=_short_fills(), snap=None)
    _s4_unproven(p)
    b, v = _frozen_short(p, bid=0.30, ask=0.31)
    st = _tick(p, v, http=_gone())
    assert not _places(v) and "close" not in _kinds(v)
    assert _census(st, "short_reduce_unproven") == 1 and _census(st, "frozen_reduce") == 0
    assert b["last_plan"]["short_reduce"] == "unproven" and _plan_exit(b)["result"] == "short_reduce_unproven"
    assert b["ledger_net"] == -300 and b["state"] == "frozen"


def test_e5_a_frozen_short_book_never_adds_to_the_short_when_he_holds_more_than_the_venue(monkeypatch):
    """His net -1000 against our venue short of -600: no BUY_SHORT
    (`frozen_no_his_exit`); a frozen book never grows its leg."""
    _shorts_on(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 1100, 0.72, NOW - 2500)],
              snap=None)
    b, v = _frozen_short(p, bid=0.30, ask=0.32)
    st = _tick(p, v, http=_mkt(100.0, 1100.0))
    assert not _places(v) and _census(st, "frozen_no_his_exit") == 1 and _census(st, "short_add") == 0
    assert _plan_exit(b)["venue_own"] == -600 and _plan_exit(b)["target"] == -1000
    assert b["ledger_net"] == -300 and b["state"] == "frozen"


# ------------------------------------------------ P1: the operator register

def test_e5_a_registered_book_never_thaws_it_exits_the_registered_shares_toward_his_net_and_never_buys():
    """Book 77's shape registered (venue 1,128 / ledger 0 / register
    1,128), he reduced 300 -> 200 (E5 review F3, the owner's option b):
    the freeze comparison agrees, the book STAYS frozen (`registered_
    frozen` on the plan) and never plans from its own ledger -- no BUY
    -- and the frozen exit sells 928 off the venue toward his 200 (the
    register is not subtracted from the seat): the ledger books nothing,
    928 are excess, no trip. The register explains the venue to the
    freeze comparison and nothing else."""
    p = _pool(fills=_his(300, sold=100), snap=None, registered={SLUG: 1128.0})
    b = _frozen_long(p, ledger=0, reason="venue_ledger_disagree")
    v = _Venue(held={SLUG: 1128}, bid=0.30, ask=0.32, lift=928)
    st = _tick(p, v, http=_mkt(200))
    assert b["state"] == "frozen" and b["last_plan"]["registered_frozen"] is True
    assert _census(st, "venue_ledger_disagree") == 0 and _census(st, "registered_books") == 1
    # E31: the 928 leave as a post-only rest at the maker wire 0.31 lifted by a
    # taker, never an IOC at 0.30; the register's rule is the subject and holds
    assert [c[2:6] for c in _places(v)] == [(0.31, 928, True, GTC_TIF)] and _places(v)[0][7] is True
    assert not [c for c in _places(v) if c[4] is False], "never a BUY on a registered book"
    assert _plan_exit(b)["venue_own"] == 1128 and _plan_exit(b)["target"] == 200
    o = next(iter(p.orders.values()))
    assert o["receipt"]["frozen_excess"]["shares"] == pytest.approx(928.0) and b["ledger_net"] == 0
    assert o["receipt"]["frozen_excess"]["px"] == pytest.approx(0.31)   # E31: the rest's cent, was 0.30
    assert p.state["mirror_live"] is True and b["last_plan"]["kind"] == "frozen"
    assert b["last_plan"]["registered"] == 1128.0 and b["last_plan"]["manual"] == 0.0


def test_e5_a_register_row_against_the_books_leg_is_not_read_and_the_book_stays_frozen():
    """A negative register on a long book (venue 0 / ledger 300: the lost
    close's sale registered as -300) would make the ledger appear
    explained and the live plan would sell 300 shares the venue does not
    hold: not read (`registered_sign_refused`), the book stays frozen,
    nothing placed. The same on a short book with a positive row."""
    p = _pool(fills=_his(300, sold=300), snap=None, registered={SLUG: -300.0})
    b = _frozen_long(p, reason="venue_ledger_disagree")
    v = _Venue(held={SLUG: 0}, bid=0.30, ask=0.32)
    st = _tick(p, v, http=_gone())
    assert not _places(v) and b["state"] == "frozen" and _census(st, "registered_sign_refused") == 1
    assert b["last_plan"]["registered"] == 0.0 and b["last_plan"]["registered_sign_refused"] == -300.0
    assert _census(st, "registered_books") == 1 and _census(st, "frozen_venue_flat") == 1


def test_e5_a_register_that_explains_only_part_keeps_the_freeze_and_the_detail_names_it():
    """Register 1,000 on a venue of 1,128 with ledger 0: still frozen
    (128 unexplained); the frozen exit sells the WHOLE 1,128 toward his
    exit -- the register is not subtracted from the seat (E5 review F3,
    option b: registered shares are the book's to exit)."""
    p = _pool(fills=_his(300, sold=300), snap=None, registered={SLUG: 1000.0})
    b = _frozen_long(p, ledger=0, reason="venue_ledger_disagree")
    v = _Venue(held={SLUG: 1128}, bid=0.30, ask=0.32, lift=1128)
    st = _tick(p, v, http=_gone())
    assert b["state"] == "frozen" and b["last_plan"]["registered"] == 1000.0 and b["last_plan"]["venue"] == 1128
    # E31: a post-only rest at the maker wire 0.31 lifted by a taker, was an IOC at 0.30
    assert [c[2:6] for c in _places(v)] == [(0.31, 1128, True, GTC_TIF)] and _plan_exit(b)["venue_own"] == 1128
    assert _census(st, "registered_books") == 1 and b["last_plan"]["kind"] == "frozen"


def test_e5_a_live_book_with_a_register_row_reads_it_as_manual_in_its_seat_and_never_increases():
    """A register row on a LIVE book's slug (written by hand: the preset
    registers frozen books alone, and a registered book never thaws).
    ledger 300, venue 400, register 100: the freeze comparison agrees,
    the live plan reads venue 300 against ledger 300 -> on target with
    his 300, no order. With him at 600 the plan wants a BUY and it is
    refused by name (`registered_no_increase`, E5 review F3): a slug
    that already holds registered shares beyond the ledger never grows."""
    p = _pool(registered={SLUG: 100.0})
    b = p.add_book(ledger=300, avg_cost=0.31)
    v = _Venue(held={SLUG: 400}, bid=0.30, ask=0.32)
    st = _tick(p, v)
    assert b["state"] == "live" and not _places(v) and _census(st, "on_target") == 1
    assert _census(st, "venue_ledger_disagree") == 0 and b["last_plan"]["registered"] == 100.0
    p2 = _pool(fills=_his(600), snap={M: 600.0, N: 0.0}, registered={SLUG: 100.0})
    b2 = p2.add_book(ledger=300, avg_cost=0.31)
    v2 = _Venue(held={SLUG: 400}, bid=0.30, ask=0.32)
    st2 = _tick(p2, v2, http=_mkt(600))
    assert not _places(v2) and _census(st2, "registered_no_increase") == 1 and b2["state"] == "live"
    assert b2["last_plan"]["kind"] == "increase" and b2["last_plan"]["reason"] == "registered_no_increase"
    assert _census(st2, "rest_placed") == 0 and _census(st2, "take_placed") == 0


def test_e5_a_placement_lost_book_sells_no_more_than_its_ledger_plus_its_lost_rows_ask_for():
    """E5 review M1: ledger 300 and one lost BUY row of 300 explain a
    venue of at most 600. Venue 1,000, he gone: `frozen_venue_
    unexplained` (bound 600), nothing sold; venue 600: sold. A
    venue_ledger_disagree book has no such bound (the disagreement is
    what it holds); lost rows unreadable: refused by name."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p, lost=0)
    p.add_order(b, side=BUY, wire=0.30, qty=300, order_id=None, state="lost", placed_ts=NOW - 3000)
    v = _Venue(held={SLUG: 1000}, bid=0.30, ask=0.32, lift=1000)
    st = _tick(p, v, http=_gone())
    assert not _places(v) and _census(st, "frozen_venue_unexplained") == 1
    assert _plan_exit(b) == {"venue_own": 1000, "target": 0, "bound": 600, "held": "frozen_venue_unexplained"}
    # E31: what the bound LETS through is a post-only rest at the maker wire
    # 0.31 lifted by a taker, never an IOC at 0.30; the bound is the subject
    v2 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, lift=600)
    st2 = _tick(p, v2, now=NOW + 30, http=_gone())
    assert [c[2:6] for c in _places(v2)] == [(0.31, 600, True, GTC_TIF)] and _census(st2, "frozen_venue_unexplained") == 0
    p3 = _pool(fills=_his(300, sold=300), snap=None)
    b3 = _frozen_long(p3, reason="venue_ledger_disagree")
    v3 = _Venue(held={SLUG: 1000}, bid=0.30, ask=0.32, lift=1000)
    _tick(p3, v3, http=_gone())
    assert [c[2:6] for c in _places(v3)] == [(0.31, 1000, True, GTC_TIF)] and "bound" not in _plan_exit(b3)
    p4 = _pool(fills=_his(300, sold=300), snap=None)
    p4.raise_on.append(("ml-lost-qty", RuntimeError("down")))     # unreadable rows: no bound is known
    b4 = _frozen_long(p4)
    v4 = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32)
    st4 = _tick(p4, v4, http=_gone())
    assert not _places(v4) and _census(st4, "frozen_venue_unexplained") == 1
    assert _plan_exit(b4)["why"] == "lost_rows_unreadable"
    s4 = _flat(ml._SQL_LOST_QTY)
    assert "WHERE book_id = $1 AND (state = 'lost' OR (state = 'placing' AND order_id IS NULL))" in s4


def test_e5_an_unreadable_register_explains_nothing_is_counted_and_logged_once(caplog):
    """Before 056 lands the read raises: `registered_unreadable` every
    read, the warning once per process, r.registered 0.0 -- the freeze
    as before."""
    ml._registered_logged = False
    p = _pool(register_raises=True)
    b = _frozen_long(p, ledger=0, reason="venue_ledger_disagree")
    v = _Venue(held={SLUG: 1128}, bid=0.30, ask=0.32)
    st = _tick(p, v)
    assert b["state"] == "frozen" and _census(st, "registered_unreadable") >= 1
    assert b["last_plan"]["registered"] == 0.0 and _census(st, "registered_books") == 0
    lines = [r.getMessage() for r in caplog.records if "operator register could not be read" in r.getMessage()]
    assert len(lines) == 1 and "056" in lines[0]
    _tick(p, _Venue(held={SLUG: 1128}), now=NOW + 30)
    assert len([r for r in caplog.records if "operator register could not be read" in r.getMessage()]) == 1


def test_e5_the_worker_never_writes_the_register_and_never_adopts_a_fill_into_it():
    src = pathlib.Path(ml.__file__).read_text()
    assert "mirror_registered_positions" in src
    for stmt in re.findall(r"(?is)(INSERT INTO|UPDATE|DELETE FROM)\s+mirror_registered_positions", src):
        raise AssertionError(f"the worker writes the register: {stmt}")
    s = _flat(ml._SQL_REGISTERED_SHARES)
    assert s.startswith("SELECT COALESCE(sum(shares), 0)::float8 FROM mirror_registered_positions")
    assert "WHERE us_market_slug = $1" in s and "ml-registered-shares" in s
    # the same shape as the manual read, read beside it
    m = _flat(ml._SQL_MANUAL_SHARES)
    assert m.startswith("SELECT COALESCE(sum(filled_shares), 0)::float8 FROM live_orders")
    rm = inspect.getsource(ml._read_market)
    assert rm.index("_SQL_MANUAL_SHARES") < rm.index("_registered_shares(t, whale, slug)")
    # the register is the venue-vs-ledger comparison's third term, exactly as manual
    tb = inspect.getsource(ml._tick_book)
    assert "explained = ledger + r.manual + registered" in tb
    assert "float(venue_int - r.manual - registered)" in tb


# ------------------------------------------------ P3: the lost close marked lost once

def test_e5_an_unattributed_lost_close_is_marked_lost_once_past_the_window_with_the_venues_reading():
    """The seven books' shape: a CLOSE row 'placing' for 30 minutes, the
    venue holding 100 of the ledger's 300 (200 sold), the log naming two
    sellers -> `close: unattributed`. Past the window the row is 'lost'
    ONCE with the venue's own reading on the receipt; the next tick
    reads no trade log and re-emits nothing; the book stays frozen by
    name for a human. Inside the window the row is left 'placing' as
    before (the log may still name the seller)."""
    sell = {"side": "SELL", "ts": NOW - 1700, "order_qty": None, "order_price": None}
    trades = [{**sell, "order_id": "s-1", "qty": 120.0, "price": 0.29},
              {**sell, "order_id": "s-2", "qty": 80.0, "price": 0.29}]
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = _frozen_long(p)
    o = p.add_order(b, side=SELL, wire=0.0, qty=300, kind="flatten_vanished", tif="CLOSE",
                    order_id=None, state="placing", placed_ts=NOW - 30 * 60)
    v = _Venue(held={SLUG: 100}, trades=trades)
    st = _tick(p, v, http=_gone())
    # the book was frozen placement_lost already: no transition, no re-count
    assert o["state"] == "lost" and _census(st, "order_lost") == 1 and _census(st, "placement_lost") == 0
    assert o["receipt"] == {"close": "unattributed", "sold": 200, "sellers": 2, "held": 100, "qty": 300,
                            "venue": 100, "at": NOW}
    # E31: the CLOSE row let the slot go, as it always did -- and the slot is
    # taken again on the same tick by the frozen exit's own rest (below), where
    # the old road's IOC filled nothing and left it empty. What is pinned is
    # that the LOST row is not the holder
    assert b["open_order_id"] != o["id"] and b["state"] == "frozen" and b["frozen_reason"] == "placement_lost"
    assert [x for x in ml._RECENT if x["what"] == "frozen" and x.get("close") == "unattributed"]
    # E31 (FILL lane 31, 2026-09-10): with the CLOSE row marked lost the slot
    # is free ON THIS TICK, and the frozen exit rests the 100 the venue still
    # holds at the maker wire max(sell_wire(0.31), bid 0.30 + a tick) = 0.31,
    # post-only and GTC -- where the old road sent ONE IOC at the take cent
    # 0.30 the NEXT tick. The rest goes out beside the lost row, so the
    # marking and the exit are one tick, not two
    assert [c[2:6] for c in _places(v)] == [(0.31, 100, True, GTC_TIF)] and _places(v)[0][7] is True
    assert _census(st, "rest_placed") == 1 and b["ledger_net"] == 300
    ml._RECENT.clear()
    # the next tick reads no trade log and re-emits nothing, and the rest STANDS
    v2 = _Venue(held={SLUG: 100}, trades=trades, bid=0.30, ask=0.32)
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_gone())
    assert "trades" not in _kinds(v2) and _census(st2, "placement_lost") == 0 and _census(st2, "order_lost") == 0
    assert not [x for x in ml._RECENT if x["what"] == "frozen" and x.get("close") == "unattributed"]
    assert not _places(v2) and not _cancels(v2) and _census(st2, "open_order_pending") == 1
    assert b["ledger_net"] == 300 and _plan_exit(b)["result"] == "open_order_pending"
    # and the taker who comes to it sells the 100, he being gone
    v3 = _Venue(held={SLUG: 100}, trades=trades, bid=0.30, ask=0.32, fills={"oid-1": (100.0, 0.31)})
    v3.orders = v.orders
    st3 = _tick(p, v3, now=NOW + 60, http=_gone())
    assert not _places(v3) and _census(st3, "filled_rest") == 1 and b["ledger_net"] == 200
    # inside the window: left 'placing', read again next tick, as before
    p3 = _pool(fills=_his(300, sold=300), snap=None)
    b3 = _frozen_long(p3)
    o3 = p3.add_order(b3, side=SELL, wire=0.0, qty=300, kind="flatten_vanished", tif="CLOSE",
                      order_id=None, state="placing", placed_ts=NOW - 90)
    st3 = _tick(p3, _Venue(held={SLUG: 100}, trades=trades), http=_gone())
    assert o3["state"] == "placing" and o3["receipt"] is None and _census(st3, "order_lost") == 0
    assert le._LOST_FILL_WINDOW_S == 20 * 60.0


# ------------------------------------------------ migration 056 and the presets

def test_e5_056_exists_sorts_after_055_and_is_one_create_only_table_with_the_briefs_columns():
    assert SQL_056.exists()
    files = [x.name for x in sorted(MIG_DIR.glob("*.sql"))]
    i = files.index("055_us_premap_team.sql")
    assert files[i + 1] == "056_mirror_registered_positions.sql" and sum(f.startswith("056_") for f in files) == 1
    sql = SQL_056.read_text()
    assert sql.splitlines()[0].startswith("-- 056: MIRROR REGISTERED POSITIONS (E5 / P1, 2026-09-07")
    body = "\n".join(ln.split("--", 1)[0] for ln in sql.splitlines())
    stmts = [_flat(s) for s in body.split(";") if s.strip()]
    assert len(stmts) == 2 and stmts[0].startswith("CREATE TABLE IF NOT EXISTS mirror_registered_positions (")
    assert stmts[1] == ("CREATE INDEX IF NOT EXISTS mirror_registered_positions_slug_idx "
                        "ON mirror_registered_positions (us_market_slug)")
    up = " ".join(stmts).upper()
    assert "ALTER TABLE" not in up and "DROP " not in up and "UPDATE " not in up
    inner = stmts[0].split("(", 1)[1].rsplit(")", 1)[0]
    cols = {}
    for part in re.split(r",\s*(?=[a-z_]+\s+[A-Z])", inner):
        name, rest = part.strip().split(" ", 1)
        cols[name] = rest
    assert set(cols) == REGISTER_COLUMNS, set(cols) ^ REGISTER_COLUMNS
    assert cols["shares"].startswith("DOUBLE PRECISION NOT NULL")
    assert "CHECK (side IN ('LONG', 'SHORT'))" in cols["side"]
    assert cols["registered_at"].startswith("TIMESTAMPTZ NOT NULL DEFAULT now()")
    for c in ("whale", "us_market_slug", "condition_id", "asset", "registered_by", "note", "source"):
        assert cols[c].startswith("TEXT NOT NULL"), c
    for name in ("mirror_books", "mirror_orders", "live_orders"):
        assert name not in " ".join(stmts), name
    pglast = pytest.importorskip("pglast")
    tree = pglast.parse_sql(sql)
    assert [type(s.stmt).__name__ for s in tree] == ["CreateStmt", "IndexStmt"]
    assert {e.colname for e in tree[0].stmt.tableElts if type(e).__name__ == "ColumnDef"} == REGISTER_COLUMNS


def test_e5_the_register_preset_needs_confirm_reads_the_books_own_row_and_refuses_a_typed_slug():
    """The ONLY writer of the register: confirm=DO, the arg is
    `<book_id>=<shares> <note>` (a bash regex: an integer book id, a
    signed number, a note), the slug / asset / condition come from
    mirror_books by id, the one statement prints venue / ledger / manual
    / registered / unexplained / figure and inserts only when the figure
    equals venue - ledger - manual exactly, with the leg's sign, on a
    frozen book read inside 10 minutes and no prior row; the verdict
    column says registered or REFUSED. The read-only `mirror-frozen`
    preset carries no confirm. The `sql: arg must be one of` line names
    both, regenerated from the case labels."""
    text = YML.read_text()
    reg = text[text.index("mirror-register) need_confirm"):text.index("mirror-frozen) SQL=")]
    assert 'if [[ ! "$ARG" =~ ^([0-9]+)=(-?[0-9]+(\\.[0-9]+)?)\\ (.+)$ ]]' in reg
    assert "FROM mirror_books b WHERE b.id = $RB" in reg and "us_market_slug" in reg
    assert "$ARG" not in reg.split("SQL=", 1)[1], "nothing typed reaches the statement but the parsed figure and note"
    assert "INSERT INTO mirror_registered_positions (whale, us_market_slug, condition_id, asset, shares, side, registered_by, note, source)" in reg
    assert "SELECT b.whale, b.us_market_slug, b.condition_id, b.asset, $RS," in reg
    assert "$RS = b.venue_net - b.ledger_net - b.manual" in reg and "b.registered = 0" in reg
    assert "b.state = 'frozen'" in reg and "b.updated_at > now() - interval '10 minutes'" in reg
    assert "($RS > 0 AND b.intent <> 'ORDER_INTENT_BUY_SHORT') OR ($RS < 0 AND b.intent = 'ORDER_INTENT_BUY_SHORT')" in reg
    assert "COALESCE(whale_username, '') = 'manual' AND status IN ('filled', 'exiting')" in reg
    assert "'REFUSED: the book must be frozen placement_lost or venue_ledger_disagree" in reg and "the figure must equal venue - ledger - manual exactly" in reg   # V3-4
    assert "RN=\"${BASH_REMATCH[4]//\\'/\\'\\'}\"" in reg, "the note's quotes are doubled before it reaches SQL"
    fz = text[text.index("mirror-frozen) SQL="):text.index("*) echo \"sql: arg must be one of")]
    assert "need_confirm" not in fz and "WHERE b.state = 'frozen'" in fz
    assert "o.reason LIKE 'frozen_reduce%'" in fz and "b.last_plan->'frozen_exit'" in fz
    for col in ("ledger_net AS ledger", "venue_net AS venue", "AS manual", "AS registered", "AS his_net", "b.target"):
        assert col in fz, col
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    assert "mirror-register" in names and "mirror-frozen" in names and len(names) == len(set(names))
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    assert names == labels, "the line is the case labels, in order"
    assert "mirror-register (DO) | mirror-frozen" in text


def test_e5_every_new_name_is_a_census_key_before_the_pinned_last_key_and_the_docs_name_them():
    keys = ml.CENSUS_KEYS
    new = ("frozen_reduce", "frozen_exits_off", "frozen_venue_unread", "frozen_coheld",
           "frozen_venue_flat", "frozen_no_his_exit", "frozen_reduce_only", "frozen_excess_sold",
           "registered_books", "registered_sign_refused", "registered_unreadable",
           "frozen_fill_this_tick", "frozen_venue_unexplained", "registered_no_increase")
    for k in new:
        assert k in keys and keys.index(k) < keys.index("cand_terminal_skipped"), k
    assert keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    doc = (REPO / "docs" / "mirror-coverage.md").read_text()
    assert "## 25. E5" in doc
    for k in new + ("MIRROR_FROZEN_EXITS", "mirror-register", "mirror-frozen", "056"):
        assert k in doc, k
