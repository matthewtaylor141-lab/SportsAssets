"""E14b (2026-09-08; FILL program lane 1): a long book's exit IOC that
is withheld at the send or fills only part of its quantity rests the
unfilled quantity at his cent on the SAME tick.

The rows (hard2/post_exits_1707.txt, the exits-paired preset at 17:07Z):
book 334 cfb-smu-flst spread, his exit 0.549, our 358 peak, our exit
filled 0.50 at a lag of 279 s (row 335); book 467 atp-serna-jianu, his
0.250, our 570, filled 0.43 (row 333); book 419 itf-olivar-hashimo, his
0.180, filled 0.09 at a lag of 3 s (row 336); book 285 lal-elc-rso
total, his 0.840, our exit avg 0.718, filled 0.05 (row 334). Book 661
(hard2/closerows_1750.txt row 529, order 3632): an exit rest of 609
@0.620 partially filled 89.24, then cancelled `replace`.

THE RULE. The prices are E4's, byte for byte: ONE IOC at the take cent
(the lowest cent at or above his price less rules.MIRROR_EXIT_TOL)
whenever the bid is there, the rest at ceil(his), nothing chasing
outside the cent. What changes: when the IOC is withheld at the send
(`bid_moved`, `ioc_quote_unread`: E18's re-read) or fills only part of
its quantity, the unfilled quantity RESTS at his cent on the same tick
(ml._exit_take, the mirror image of ml._entry_take) instead of leaving
the book with no exit order until the next full tick. The remainder is
min(qty - floor(take_filled), _sell_qty(book, ...)): never more than
the plan's unfilled quantity, never more than the ledger reads now.
Census `exit_take_rested`; the plan carries `exit_take_rested`
{take, rest, qty, filled, rested}; the IOC's row keeps decision 'take',
the rest's writes 'exit_rest' (an existing word at a new site). No rail
added; MIRROR_EXIT_TOL untouched; an exit's ops stay exempt from the ops
budget and the replace budget.

Driven against the worker file's fakes (its autouse rails are imported)
and E18's moving venue.
"""
import inspect
import pathlib
import re
import types

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e18_rest_life import _MovingVenue, _bbos, _inserts
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    BUY, CID, M, N, NOW, SELL, SLUG, _NoClose, _Venue, _armed, _cancels, _census, _fill, _flip_world, _mkt,
    _places, _pool, _run, _shorts_on, _tick,
)

GTC_TIF = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
IOC_TIF = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
ROOT = pathlib.Path(__file__).resolve().parents[2]
NEW_NAMES = ("exit_take_rested",)


def _exit_world(his_px, qty, entry=0.60):
    """He bought 2 x `qty` at `entry` and sold `qty` at `his_px` (his net
    `qty`); we hold 2 x `qty` at ratio 1.0, so the plan is a SELL of
    `qty` priced off his newest reducing fill: floor his - 0.01, the
    take at the lowest cent at or above it, the rest at ceil(his)."""
    p = _pool(fills=[_fill(M, "BUY", 2 * qty, entry, NOW - 3000), _fill(M, "SELL", qty, his_px, NOW - 1000)],
              snap={M: float(qty), N: 0.0})
    b = p.add_book(ledger=2 * qty, avg_cost=entry)
    return p, b


def _opens(p):
    return [o for o in p.orders.values() if o["state"] == "open"]


# ------------------------------------------------------ (1) book 334's shape

def test_e14b_book_334_the_withheld_exit_ioc_rests_the_whole_quantity_at_his_cent_the_same_tick(monkeypatch):
    """His 0.549: floor 0.539, take 0.54, rest 0.55. The tick's bid at
    0.54 fires the IOC; the re-read finds the bid at 0.53 -> `bid_moved`
    -> the 358 rest at 0.55 THIS tick, decision 'exit_rest', census
    `exit_take_rested`, no second IOC, and the exit's slot never
    `ops_capped` (the ops budget is set to 0: an exit is exempt, M-1)."""
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 0)
    p, b = _exit_world(0.549, 358)
    v = _MovingVenue([(0.54, 0.56), (0.53, 0.56)], held={SLUG: 716}, ioc_fill=358.0)
    st = _tick(p, v, http=_mkt(358.0))
    lp = b["last_plan"]
    assert (lp["exit_px"], lp["exit_px_src"], lp["exit_floor"], lp["exit_take"], lp["exit_rest"]) == (
        0.549, "his_fill", 0.539, 0.54, 0.55)
    assert _bbos(v).count(SLUG) == 2, "the tick's read and the IOC's re-read; the rest is not re-read"
    assert [c[2:6] for c in _places(v)] == [(0.55, 358, True, GTC_TIF)], "the rest at his cent, no IOC sent"
    assert _census(st, "bid_moved") == 1 and _census(st, "exit_take") == 0 and _census(st, "take_placed") == 0
    assert _census(st, "exit_take_rested") == 1 and _census(st, "rest_placed") == 1
    assert _census(st, "ops_capped") == 0 and _census(st, "exit_out_of_tol") == 0
    assert lp["bid_moved"] == {"bid_at_plan": 0.54, "bid_at_send": 0.53, "wire": 0.54}
    assert lp["exit_take_rested"] == {"take": 0.54, "rest": 0.55, "qty": 358, "filled": 0.0, "rested": 358}
    assert [a[20] for a in _inserts(p)] == ["exit_rest"], "the withheld IOC wrote no row; the rest's says exit_rest"
    opens = _opens(p)
    assert len(opens) == 1 and (opens[0]["kind"], opens[0]["side"], opens[0]["wire"], opens[0]["qty"],
                                opens[0]["tif"]) == ("reduce", SELL, 0.55, 358, "GTC")
    assert b["open_order_id"] == opens[0]["id"] and b["ledger_net"] == 716 and b["state"] == "live"
    assert st["ops"] == 1
    # the next tick with the bid still under the take cent: the rest stands, held by name, nothing more
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(358.0))
    assert len(_places(v)) == 1 and not _cancels(v)
    assert _census(st2, "exit_out_of_tol") == 1 and _census(st2, "exit_take_rested") == 0 and _census(st2, "bid_moved") == 0


def test_e14b_an_unreadable_re_read_rests_the_whole_quantity_too():
    """`ioc_quote_unread` (the re-read came back without a bid): no IOC
    on a figure that was never read, the whole quantity at his cent."""
    p, b = _exit_world(0.549, 358)
    v = _MovingVenue([(0.54, 0.56), (None, 0.56)], held={SLUG: 716}, ioc_fill=358.0)
    st = _tick(p, v, http=_mkt(358.0))
    assert [c[2:6] for c in _places(v)] == [(0.55, 358, True, GTC_TIF)]
    assert _census(st, "ioc_quote_unread") == 1 and _census(st, "exit_take_rested") == 1 and _census(st, "exit_take") == 0
    assert b["last_plan"]["exit_take_rested"]["rested"] == 358 and b["ledger_net"] == 716


# ------------------------------------------------------ (2) book 467's shape

def test_e14b_book_467_the_partial_exit_ioc_rests_its_remainder_and_the_next_tick_takes_off_that_rest():
    """His 0.250: floor 0.24, take 0.24, rest 0.25. The IOC for 570 fills
    245 -> the remainder 325 = _sell_qty(570 - 245) rests at 0.25 the
    same tick (the ledger reads 895 after the booking). The next tick
    with the bid back at the take cent: the keep branch cancels the rest
    and takes for the 325, as today."""
    p, b = _exit_world(0.25, 570, entry=0.50)
    v = _Venue(bid=0.24, ask=0.26, held={SLUG: 1140}, ioc_fill=245.0)
    st = _tick(p, v, http=_mkt(570.0))
    lp = b["last_plan"]
    assert (lp["exit_floor"], lp["exit_take"], lp["exit_rest"]) == (0.24, 0.24, 0.25)
    assert [c[2:6] for c in _places(v)] == [(0.24, 570, True, IOC_TIF), (0.25, 325, True, GTC_TIF)]
    assert b["ledger_net"] == 895 and _census(st, "exit_take") == 1 and _census(st, "take_placed") == 1
    assert _census(st, "exit_take_rested") == 1 and _census(st, "rest_placed") == 1 and _census(st, "ops_capped") == 0
    assert (lp["take_qty"], lp["take_filled"]) == (570, 245.0)
    assert lp["exit_take_rested"] == {"take": 0.24, "rest": 0.25, "qty": 570, "filled": 245.0, "rested": 325}
    assert [a[20] for a in _inserts(p)] == ["take", "exit_rest"]
    take = next(o for o in p.orders.values() if o["kind"] == "take")
    assert take["booked_filled"] == 245.0 and take["state"] == "cancelled" and take["tif"] == "IOC"
    opens = _opens(p)
    assert len(opens) == 1 and (opens[0]["kind"], opens[0]["wire"], opens[0]["qty"]) == ("reduce", 0.25, 325)
    assert st["ops"] == 2
    # the next tick, the bid at the take cent: the rest cancelled, ONE IOC for the 325, filled
    v.portfolio.held[SLUG] = 895
    v.ioc_fill = 325.0
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(570.0))
    assert [c[1] for c in _cancels(v)] == ["oid-2"], "the rest's venue id (the IOC took oid-1)"
    assert [c[2:6] for c in _places(v)][2:] == [(0.24, 325, True, IOC_TIF)]
    assert b["ledger_net"] == 570 and _census(st2, "exit_take") == 1 and _census(st2, "exit_take_rested") == 0
    assert not _opens(p) and b["open_order_id"] is None and "exit_take_rested" not in b["last_plan"]


# ------------------------------------------------------ (3) book 419's shape

def test_e14b_book_419_the_bid_under_the_floor_sends_no_ioc_and_rests_at_his_cent_byte_for_byte_today():
    """His 0.180: floor 0.17, take 0.17, rest 0.18. The bid at 0.16 is
    under the floor: no IOC, no re-read, `exit_out_of_tol` {bid 0.16,
    floor 0.17}, the rest at 0.18 -- E4's path, untouched."""
    p, b = _exit_world(0.18, 152, entry=0.20)
    v = _Venue(bid=0.16, ask=0.19, held={SLUG: 304})
    st = _tick(p, v, http=_mkt(152.0))
    lp = b["last_plan"]
    assert (lp["exit_floor"], lp["exit_take"], lp["exit_rest"]) == (0.17, 0.17, 0.18)
    assert [c[2:6] for c in _places(v)] == [(0.18, 152, True, GTC_TIF)] and _bbos(v).count(SLUG) == 1
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "exit_take") == 0
    assert _census(st, "exit_take_rested") == 0 and _census(st, "bid_moved") == 0
    assert lp["exit_out_of_tol"] == {"bid": 0.16, "ask": 0.19, "floor": 0.17, "at": NOW}
    assert "exit_take_rested" not in lp and "bid_moved" not in lp and "take_qty" not in lp
    assert [a[20] for a in _inserts(p)] == ["exit_rest"] and b["ledger_net"] == 304


# ------------------------------------------------------ (4) book 285's shape

def test_e14b_book_285_never_an_ioc_under_his_price_less_the_tolerance_the_mandate_pin():
    """His 0.840: floor 0.83, take 0.83, rest 0.84. Bids at 0.70, 0.71
    and 0.82 send NO IOC (the rest at 0.84, held by name); the bid at
    0.83 sends the one IOC at 0.83 and nowhere under. The tolerance is
    the rail it was: 0.01, capped_env, floor 0.0."""
    for bid in (0.70, 0.71, 0.82):
        p, b = _exit_world(0.84, 432, entry=0.80)
        v = _Venue(bid=bid, ask=0.86, held={SLUG: 864}, ioc_fill=432.0)
        st = _tick(p, v, http=_mkt(432.0))
        assert [c[2:6] for c in _places(v)] == [(0.84, 432, True, GTC_TIF)], bid
        assert _census(st, "exit_take") == 0 and _census(st, "exit_take_rested") == 0, bid
        assert _census(st, "exit_out_of_tol") == 1 and _bbos(v).count(SLUG) == 1, bid
        assert (b["last_plan"]["exit_floor"], b["last_plan"]["exit_take"]) == (0.83, 0.83)
    p, b = _exit_world(0.84, 432, entry=0.80)
    v = _Venue(bid=0.83, ask=0.86, held={SLUG: 864}, ioc_fill=432.0)
    st = _tick(p, v, http=_mkt(432.0))
    assert [c[2:6] for c in _places(v)] == [(0.83, 432, True, IOC_TIF)] and b["ledger_net"] == 432
    assert _census(st, "exit_take") == 1 and _census(st, "exit_take_rested") == 0
    assert rules.MIRROR_EXIT_TOL == 0.01
    # _exit_take prices off rules.exit_terms alone: the take cent and the
    # rest cent, never the quote (no chasing wire is computed in it)
    src = inspect.getsource(ml._exit_take)
    assert 'ex["take"]' in src and 'ex["rest"]' in src
    assert "r.bid" not in src and "r.ask" not in src and "sell_price" not in src and "sell_wire" not in src


# ------------------------------------------------------ (5) book 661's shape

def _book_661(cancel_ok=True, ioc_fill=200.0):
    """An exit rest of 609 @0.62 partially filled 89.24 (the row's shape),
    his newest sale now at 0.60 (the cent moved: a replace). The venue
    holds what the fill left: 1218 - 89.24."""
    p = _pool(fills=[_fill(M, "BUY", 1218, 0.65, NOW - 3000), _fill(M, "SELL", 300, 0.62, NOW - 1000),
                     _fill(M, "SELL", 309, 0.60, NOW - 500)],
              snap={M: 609.0, N: 0.0})
    b = p.add_book(ledger=1218, avg_cost=0.65)
    o = p.add_order(b, side=SELL, wire=0.62, qty=609, kind="reduce", placed_ts=NOW - 100)
    v = _Venue(bid=0.59, ask=0.61, held={SLUG: 1218 - 89.24}, ioc_fill=ioc_fill, cancel_ok=cancel_ok)
    v.rest("oid-1", "SELL", 0.62, 609, filled=89.24, avg=0.62)
    return p, b, o, v


def test_e14b_book_661_the_replace_cancel_books_the_partial_fill_and_the_remainder_alone_is_taken_then_rested():
    """The cancel books the 89.24 (ledger 1129); the re-plan sizes off
    that ledger: 1129 - 609 = 520, never the row's 609; the bid at the
    new take cent 0.59 sends the IOC for 520, which fills 200; the 320
    left rest at 0.60 the same tick."""
    p, b, o, v = _book_661()
    st = _tick(p, v, http=_mkt(609.0))
    assert [c[1] for c in _cancels(v)] == ["oid-1"] and p.orders[o["id"]]["state"] == "cancelled"
    assert p.orders[o["id"]]["booked_filled"] == 89.24 and b["last_plan"]["replaced"] == "replace_cent"
    assert [c[2:6] for c in _places(v)] == [(0.59, 520, True, IOC_TIF), (0.60, 320, True, GTC_TIF)]
    assert b["ledger_net"] == 1129 - 200 and _census(st, "exit_take") == 1 and _census(st, "exit_take_rested") == 1
    assert b["last_plan"]["exit_take_rested"] == {"take": 0.59, "rest": 0.60, "qty": 520, "filled": 200.0, "rested": 320}
    assert [a[20] for a in _inserts(p)] == ["take", "exit_rest"]
    opens = _opens(p)
    assert len(opens) == 1 and (opens[0]["wire"], opens[0]["qty"]) == (0.60, 320)


def test_e14b_book_661_no_rest_is_placed_while_the_cancels_row_is_non_terminal():
    """The venue refuses the cancel: the row is not terminal, the
    re-plan never reaches the IOC, and NOTHING rests (E14b places a
    rest only after an IOC of its own, under _entry_take's guards)."""
    p, b, o, v = _book_661(cancel_ok=False)
    st = _tick(p, v, http=_mkt(609.0))
    assert {c[1] for c in _cancels(v)} == {"oid-1"} and not _places(v)
    assert _census(st, "exit_take") == 0 and _census(st, "exit_take_rested") == 0
    assert "exit_take_rested" not in (b["last_plan"] or {})
    assert p.orders[o["id"]]["state"] not in ("filled", "cancelled", "expired")


# ------------------------------------ (6) frozen / tripped / abandoned / non-terminal

class _Fake:
    """ml._place replaced by a scripted result list: each entry
    (result, filled) -- a 'take' books `filled` onto the plan the way
    _place_reserved does (plan['take_qty'] / plan['take_filled'])."""

    def __init__(self, results):
        self.results, self.calls = list(results), []

    async def __call__(self, t, book, r, kind, side, wire, qty, his_px, p, plan, tif="GTC", take_first=False):
        self.calls.append((kind, side, wire, int(qty), tif))
        res, filled = self.results.pop(0)
        if res == "take":
            plan["take_qty"], plan["take_filled"] = int(qty), float(filled)
        return res


def _unit(monkeypatch, results, *, state="live", cancel_all=None, abandoned=False, nonterminal=(),
          ledger=300, held=300.0, qty=200):
    fake = _Fake(results)
    monkeypatch.setattr(ml, "_place", fake)
    stats = ml._new_stats()
    monkeypatch.setattr(ml, "_current_stats", stats)
    ex = rules.exit_terms(SELL, 0.31)
    book = {"id": 7, "state": state, "ledger_net": ledger, "_held": held, "intent": "ORDER_INTENT_BUY_LONG"}
    t = types.SimpleNamespace(cancel_all=cancel_all, abandoned=abandoned, nonterminal=set(nonterminal))
    r = types.SimpleNamespace(whale="rn1")
    plan = {}
    res = _run(ml._exit_take(t, book, r, mi.Plan(SELL, qty, 0.31, "reduce"), ex, qty, 0.31, plan, "reduce"))
    return res, fake.calls, plan, stats["census"]


def test_e14b_a_withheld_or_partial_ioc_on_a_frozen_tripped_abandoned_or_non_terminal_book_rests_nothing(monkeypatch):
    assert rules.exit_terms(SELL, 0.31)["take"] == 0.30 and rules.exit_terms(SELL, 0.31)["rest"] == 0.31
    for kw in ({"state": "frozen"}, {"cancel_all": "overfill"}, {"abandoned": True}, {"nonterminal": (7,)}):
        for res0, filled in (("bid_moved", 0.0), ("ioc_quote_unread", 0.0), ("take", 50.0)):
            res, calls, plan, census = _unit(monkeypatch, [(res0, filled)], **kw)
            assert res == res0 and calls == [("take", SELL, 0.30, 200, "IOC")], (kw, res0)
            assert "exit_take_rested" not in plan and census["exit_take_rested"] == 0, (kw, res0)


def test_e14b_the_rest_goes_only_after_a_withheld_ioc_or_a_partial_take_and_is_bounded_twice(monkeypatch):
    # every IOC_SKIPPED name: nothing executed, the whole quantity rests
    for name in ml.IOC_SKIPPED:
        res, calls, plan, census = _unit(monkeypatch, [(name, 0.0), ("rest_placed", 0.0)])
        assert res == "rest_placed" and calls == [("take", SELL, 0.30, 200, "IOC"), ("reduce", SELL, 0.31, 200, "GTC")], name
        assert plan["exit_take_rested"] == {"take": 0.30, "rest": 0.31, "qty": 200, "filled": 0.0, "rested": 200}
        assert census["exit_take_rested"] == 1
    # a partial take: the plan's own remainder
    res, calls, plan, census = _unit(monkeypatch, [("take", 50.0), ("rest_placed", 0.0)], ledger=250, held=250.0)
    assert res == "rest_placed" and calls[1] == ("reduce", SELL, 0.31, 150, "GTC")
    assert plan["exit_take_rested"] == {"take": 0.30, "rest": 0.31, "qty": 200, "filled": 50.0, "rested": 150}
    # ... never more than the ledger reads NOW (the IOC's fill booked, or a
    # ledger that reads lower still): min(150, _sell_qty(150))
    res, calls, plan, census = _unit(monkeypatch, [("take", 50.0), ("rest_placed", 0.0)], ledger=100, held=300.0)
    assert calls[1][3] == 100 and plan["exit_take_rested"]["rested"] == 100
    res, calls, plan, census = _unit(monkeypatch, [("take", 50.0), ("rest_placed", 0.0)], ledger=250, held=120.5)
    assert calls[1][3] == 121 and plan["exit_take_rested"]["rested"] == 121
    # a fractional fill floors the remainder: 200 - 50.4 -> 149
    res, calls, plan, census = _unit(monkeypatch, [("take", 50.4), ("rest_placed", 0.0)], ledger=250, held=250.0)
    assert calls[1][3] == 149 and plan["exit_take_rested"]["filled"] == 50.4
    # a whole fill: no rest, 'take' returned, nothing on the plan
    res, calls, plan, census = _unit(monkeypatch, [("take", 200.0)], ledger=100, held=100.0)
    assert res == "take" and len(calls) == 1 and "exit_take_rested" not in plan and census["exit_take_rested"] == 0
    assert census["under_one_share"] == 0
    # a remainder of 150 the ledger cannot cover (ledger 0): HELD BY NAME
    # (`under_one_share`, the plan's fail-closed list), `rested` 0 on the
    # plan, no rest sized past the ledger, 'take' returned (review MEDIUM-1)
    res, calls, plan, census = _unit(monkeypatch, [("take", 50.0)], ledger=0, held=0.0)
    assert res == "take" and len(calls) == 1 and census["exit_take_rested"] == 0
    assert census["under_one_share"] == 1
    assert plan["exit_take_rested"] == {"take": 0.30, "rest": 0.31, "qty": 200, "filled": 50.0, "rested": 0}
    # the rest refused by name: the plan says rested 0, the census not counted, the IOC's result returned
    res, calls, plan, census = _unit(monkeypatch, [("bid_moved", 0.0), ("open_order_pending", 0.0)])
    assert res == "bid_moved" and len(calls) == 2 and plan["exit_take_rested"]["rested"] == 0 and census["exit_take_rested"] == 0
    res, calls, plan, census = _unit(monkeypatch, [("take", 50.0), ("under_one_share", 0.0)], ledger=250, held=250.0)
    assert res == "take" and plan["exit_take_rested"]["rested"] == 0 and census["exit_take_rested"] == 0
    # any other IOC result is returned as today, nothing rests
    for name in ("ops_capped", "open_order_pending", "place_refused:rejected", "tick_abandoned", "open_orders_unreadable"):
        res, calls, plan, census = _unit(monkeypatch, [(name, 0.0)])
        assert res == name and len(calls) == 1 and "exit_take_rested" not in plan, name


def test_e14b_a_frozen_books_partial_exit_ioc_leaves_nothing_resting_through_the_worker():
    """E5's frozen long book (ledger 300, venue 600, he reduced to 100):
    the frozen reduce's IOC for 500 fills 100; frozen -> no same-tick
    rest (the E5 / E16 pins stand)."""
    from tests.test_e5_frozen_exits import _frozen_long
    from tests.test_e5_frozen_exits import _pool as _e5_pool
    from tests.test_mirror_live_worker import _his
    p = _e5_pool(fills=_his(300, sold=200), snap=None)
    b = _frozen_long(p)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, ioc_fill=100)
    st = _tick(p, v, http=_mkt(100))
    assert [c[2:6] for c in _places(v)] == [(0.30, 500, True, IOC_TIF)]
    assert _census(st, "frozen_reduce") == 1 and _census(st, "exit_take") == 1 and _census(st, "exit_take_rested") == 0
    assert b["state"] == "frozen" and "exit_take_rested" not in b["last_plan"] and not _opens(p)


# ------------------------------------------------ (7) the short cover, untouched

def test_e14b_the_short_cover_emits_none_of_the_new_names_and_the_flatten_send_is_untouched(monkeypatch):
    _shorts_on(monkeypatch)
    # S4: the cover's IOC fills 100 of 300 -- nothing rests after it this tick, as before
    p, b, v = _flip_world(ioc_fill=100.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.32, 300, True, IOC_TIF)] and b["ledger_net"] == -200
    assert _census(st, "short_cover_take") == 1 and _census(st, "exit_take_rested") == 0 and not _opens(p)
    assert "exit_take_rested" not in b["last_plan"]
    # the call sites: the two long-exit sites of _act, and nowhere else
    src = inspect.getsource(ml)
    assert src.count("_exit_take(") == 3, "the def and the two long-exit sites"
    act = inspect.getsource(ml._act)
    assert act.count("_exit_take(") == 2
    for fn in (ml._flatten_send, ml._flatten_vanished, ml._frozen_exit, ml._frozen_reduce_on_fill,
               ml._exit_held, ml._cover_qty, ml._s4_refusal, ml._entry_take, ml._sell_qty):
        s = inspect.getsource(fn)
        assert "_exit_take(" not in s and "exit_take_rested" not in s, fn.__name__
    assert "sell_limit_price" in inspect.getsource(ml._flatten_send), "the co-held IOC priced as before"
    assert "exit_take_rested" not in inspect.getsource(rules)
    # the exit terms are the rule's: E4's cents on both sides
    assert rules.exit_terms(SELL, 0.549) == {"px": 0.549, "floor": 0.539, "take": 0.54, "rest": 0.55}
    assert rules.exit_terms(BUY, 0.31) == {"px": 0.31, "ceiling": 0.32, "cover": 0.32, "take": 0.32, "rest": 0.31}
    assert ml.IOC_SKIPPED == ("ask_moved", "bid_moved", "ioc_reread_capped", "ioc_quote_unread")


# ------------------------------------------------------ (8) the E18 triple

def test_e14b_the_census_name_sits_before_drift_smaller_open_and_the_tail_pins_hold():
    keys = ml.CENSUS_KEYS
    assert keys.count("exit_take_rested") == 1 and len(set(keys)) == len(keys)
    # E14 (FILL lane 2) landed after this lane and placed `take_in_band` nearer the key (-14 -> -15)
    assert keys[-15] == "exit_take_rested" and keys[-14] == "take_in_band"
    assert keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-1] == "cand_terminal_skipped" and keys[-8:-4] == ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")
    assert ml._new_stats()["census"]["exit_take_rested"] == 0
    # no knob of the lane's own: no env read added to the worker or the rules
    assert "_env_float(" not in inspect.getsource(ml._exit_take) and "capped_env" not in inspect.getsource(ml._exit_take)
    assert 'capped_env("MIRROR_EXIT_TAKE' not in inspect.getsource(rules)
    assert '_mirror_stop("exit_take_rested"' in inspect.getsource(ml._exit_take)


def test_e14b_every_name_is_emitted_here(monkeypatch):
    test_e14b_book_334_the_withheld_exit_ioc_rests_the_whole_quantity_at_his_cent_the_same_tick(monkeypatch)
    test_e14b_book_467_the_partial_exit_ioc_rests_its_remainder_and_the_next_tick_takes_off_that_rest()


def test_e14b_the_decision_words_are_the_existing_ones_and_059s_list_stands():
    assert rules.order_decision("reduce", True, False) == "take"
    assert rules.order_decision("reduce", False, False) == "exit_rest"
    sql = (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "('rest', 'take', 'cover', 'exit_rest'; 'take_in_band' is" in sql
    assert "exit_take" not in sql, "no new decision word"


def test_e14b_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E14b \(2026-09-08, FILL lane 1\)", doc, re.M), "the E14b section header"
    for k in NEW_NAMES + ("_exit_take", "exit_rest", "MIRROR_EXIT_TOL", "bid_moved", "ioc_quote_unread",
                          "take_filled", "_sell_qty", "test_e14b_exit_take_rests.py", "334", "467", "419", "285", "661"):
        assert k in doc, k
