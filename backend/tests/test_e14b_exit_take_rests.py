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

RE-PINNED WHOLE at E31 (FILL lane 31, 2026-09-10; owner order ~03:3xZ
"become a maker not taker ... mirror him to a tee"). This lane's OUTCOME
-- the unfilled quantity resting at his cent on the same tick -- is now
the UNCONDITIONAL behaviour of every exit: there is no exit IOC to
withhold or to fill in part, so the exit rests at max(sell_wire(his),
bid + 0.01) on every tick, whole, and never crosses the bid. What that
retires here: the IOC leg of every shape (`exit_take`, `take_placed`),
the names that said WHY it was withheld (`bid_moved`,
`ioc_quote_unread`, replaced at the same site by `rest_quote_unread`),
this lane's own `exit_take_rested` (a declared zero from E31: the rest
no longer needs a withheld take to justify it), the `exit_out_of_tol`
hold on a FRESH exit (it survives only on the keep branch, where an
existing rest waits for the bid) and the worker's `_exit_take` wrapper
itself. What stands: the exit CENT (rules.exit_terms(SELL, his)["rest"]
is ceil(his), which is what the maker clamp returns whenever his cent is
over the bid), the ledger bounds on the quantity (_sell_qty), the ops
and replace exemptions, and every frozen / tripped / abandoned refusal.

THE RULE AS IT WAS. The prices are E4's, byte for byte: ONE IOC at the take cent
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

def test_e14b_book_334_the_exit_rests_the_whole_quantity_at_his_cent_the_same_tick(monkeypatch):
    """His 0.549: floor 0.539, take 0.54, rest 0.55. E14b pinned the IOC
    at 0.54 withheld by the re-read's bid of 0.53 (`bid_moved`) and the
    358 resting at 0.55 the same tick.

    RE-PINNED at E31: the 358 rest at 0.55 IS the order -- max(0.55, 0.54
    + 0.01) = 0.55, his own cent, one tick over the bid -- and it goes out
    whether the bid moves or not. The two bbo reads stand: 0.55 is the
    touch bound, so the rest re-reads at the send (E31 D) where the IOC's
    re-read stood, and the fresher bid of 0.53 only lowers the bound
    (max(0.55, 0.54) is still 0.55), so nothing is re-quoted. The exit's
    slot is still never `ops_capped` with the ops budget at 0 (M-1)."""
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 0)
    p, b = _exit_world(0.549, 358)
    v = _MovingVenue([(0.54, 0.56), (0.53, 0.56)], held={SLUG: 716}, ioc_fill=358.0)
    st = _tick(p, v, http=_mkt(358.0))
    lp = b["last_plan"]
    assert (lp["exit_px"], lp["exit_px_src"], lp["exit_floor"], lp["exit_take"], lp["exit_rest"]) == (
        0.549, "his_fill", 0.539, 0.54, 0.55)
    assert _bbos(v).count(SLUG) == 2, "the tick's read and the touch-bound rest's re-read"
    assert [c[2:6] for c in _places(v)] == [(0.55, 358, True, GTC_TIF)], "the rest at his cent, nothing crossing"
    assert _census(st, "bid_moved") == 0 and _census(st, "exit_take") == 0 and _census(st, "take_placed") == 0
    assert _census(st, "exit_take_rested") == 0 and _census(st, "rest_placed") == 1
    assert _census(st, "ops_capped") == 0 and _census(st, "exit_out_of_tol") == 0
    assert "bid_moved" not in lp and "exit_take_rested" not in lp
    assert lp["rest_quote_at_send"] == {"bid": 0.53, "ask": 0.56, "bid_at_plan": 0.54, "ask_at_plan": 0.56}
    assert lp["maker"]["clause"] == "his_cent" and lp["maker"]["wire"] == 0.55
    assert [a[20] for a in _inserts(p)] == ["exit_rest"], "one row, the rest's: exit_rest"
    opens = _opens(p)
    assert len(opens) == 1 and (opens[0]["kind"], opens[0]["side"], opens[0]["wire"], opens[0]["qty"],
                                opens[0]["tif"]) == ("reduce", SELL, 0.55, 358, "GTC")
    assert b["open_order_id"] == opens[0]["id"] and b["ledger_net"] == 716 and b["state"] == "live"
    assert st["ops"] == 1
    # the next tick with the bid still under the take cent: the rest stands, held by
    # name on the KEEP branch (where `exit_out_of_tol` still lives), nothing more sent
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(358.0))
    assert len(_places(v)) == 1 and not _cancels(v)
    assert _census(st2, "exit_out_of_tol") == 1 and _census(st2, "exit_take_rested") == 0 and _census(st2, "bid_moved") == 0


def test_e14b_an_unreadable_re_read_rests_the_whole_quantity_too():
    """The re-read came back without a bid: nothing is priced off a figure
    that was never read (`rest_quote_unread` at E31, `ioc_quote_unread`
    before it), the whole quantity rests at his cent on the tick's own
    reading -- a cent the tick's read said does not cross."""
    p, b = _exit_world(0.549, 358)
    v = _MovingVenue([(0.54, 0.56), (None, 0.56)], held={SLUG: 716}, ioc_fill=358.0)
    st = _tick(p, v, http=_mkt(358.0))
    assert [c[2:6] for c in _places(v)] == [(0.55, 358, True, GTC_TIF)]
    assert _census(st, "rest_quote_unread") == 1 and _census(st, "exit_take_rested") == 0 and _census(st, "exit_take") == 0
    assert _census(st, "ioc_quote_unread") == 0, "the retired name stays a declared zero"
    assert b["last_plan"]["rest_quote_unread"]["bid"] is None and b["ledger_net"] == 716


# ------------------------------------------------------ (2) book 467's shape

def test_e14b_book_467_the_exit_rests_whole_and_a_taker_lifting_part_of_it_leaves_the_rest_standing():
    """His 0.250: floor 0.24, take 0.24, rest 0.25. E14b pinned the IOC
    for 570 at 0.24 filling 245 and the remainder 325 resting at 0.25 the
    same tick.

    RE-PINNED at E31 (FILL lane 31, 2026-09-10): the exit is ONE post-only
    rest for the whole 570 at 0.25 -- max(sell_wire(0.25), 0.24 + 0.01) --
    a cent ABOVE where E14b crossed, and a taker who lifts 245 of it at
    create (`lift`, the venue's `aggressor` reading False) books the same
    245 and leaves the SAME 325 standing, on the same order, with no
    second send and no second row. The ledger reads 895 either way."""
    p, b = _exit_world(0.25, 570, entry=0.50)
    v = _Venue(bid=0.24, ask=0.26, held={SLUG: 1140}, ioc_fill=245.0, lift=245.0)
    st = _tick(p, v, http=_mkt(570.0))
    lp = b["last_plan"]
    assert (lp["exit_floor"], lp["exit_take"], lp["exit_rest"]) == (0.24, 0.24, 0.25)
    assert [c[2:6] for c in _places(v)] == [(0.25, 570, True, GTC_TIF)]
    assert b["ledger_net"] == 895 and _census(st, "exit_take") == 0 and _census(st, "take_placed") == 0
    assert _census(st, "exit_take_rested") == 0 and _census(st, "rest_placed") == 1 and _census(st, "ops_capped") == 0
    assert _census(st, "maker_fill_at_create") == 1 and _census(st, "post_only_block") == 0
    assert "take_qty" not in lp and "exit_take_rested" not in lp
    assert [a[20] for a in _inserts(p)] == ["exit_rest"]
    opens = _opens(p)
    assert len(opens) == 1 and (opens[0]["kind"], opens[0]["wire"], opens[0]["qty"]) == ("reduce", 0.25, 570)
    assert opens[0]["tif"] == "GTC" and st["ops"] == 1
    # the next tick, the ledger at 895 against a target of 570: the standing order's
    # LEAVES are exactly the 325 still to sell, at the cent the plan wants -- so it is
    # KEPT and its queue position with it. E14b cancelled a 325 rest here and crossed
    # for it; nothing is cancelled and nothing crosses now
    v.portfolio.held[SLUG] = 895
    v.lift = 0.0
    st2 = _tick(p, v, now=NOW + 30, http=_mkt(570.0))
    assert not _cancels(v) and len(_places(v)) == 1
    assert b["ledger_net"] == 895 and _census(st2, "exit_take") == 0 and _census(st2, "exit_take_rested") == 0
    assert _census(st2, "open_order_pending") == 1 and _opens(p)[0]["qty"] == 570


# ------------------------------------------------------ (3) book 419's shape

def test_e14b_book_419_the_bid_under_the_floor_sends_no_ioc_and_rests_at_his_cent_byte_for_byte_today():
    """His 0.180: floor 0.17, take 0.17, rest 0.18. The bid at 0.16 is
    under the floor: no IOC, no re-read, the rest at 0.18 -- E4's path.

    RE-PINNED at E31 (FILL lane 31, 2026-09-10): the rest and its cent are
    byte for byte (max(sell_wire(0.18), 0.16 + 0.01) = 0.18, his own cent
    two ticks over the bid), and `exit_take` is gone with `_exit_take`:
    there is no take to send. E4's HELD RECORD `exit_out_of_tol` STANDS --
    the lane keeps `_exit_held` on the road that PLACES the exit as well as
    on the keep branch, because the exits presets read it and docs 75 lists
    the name as reachable -- so the tick that rests two ticks over a bid
    under his floor still records the quote and the floor it read. The rest
    is not at the touch bound, so there is still no re-read: one bbo read."""
    p, b = _exit_world(0.18, 152, entry=0.20)
    v = _Venue(bid=0.16, ask=0.19, held={SLUG: 304})
    st = _tick(p, v, http=_mkt(152.0))
    lp = b["last_plan"]
    assert (lp["exit_floor"], lp["exit_take"], lp["exit_rest"]) == (0.17, 0.17, 0.18)
    assert [c[2:6] for c in _places(v)] == [(0.18, 152, True, GTC_TIF)] and _bbos(v).count(SLUG) == 1
    # E31: `exit_take` 1 -> 0 (no take is sent); `exit_out_of_tol` 0 -> 1 -- the take cent
    # 0.17 is over the bid 0.16, so the touch is outside his tolerance cent and the record
    # is written on the placement road with the quote and the floor it was read against
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "exit_take") == 0
    assert _census(st, "exit_take_rested") == 0 and _census(st, "bid_moved") == 0
    assert lp["exit_out_of_tol"]["bid"] == 0.16 and lp["exit_out_of_tol"]["ask"] == 0.19
    assert lp["exit_out_of_tol"]["floor"] == 0.17 and lp["maker"]["clause"] == "his_cent"
    assert "exit_take_rested" not in lp and "bid_moved" not in lp and "take_qty" not in lp
    assert [a[20] for a in _inserts(p)] == ["exit_rest"] and b["ledger_net"] == 304


# ------------------------------------------------------ (4) book 285's shape

def test_e14b_book_285_never_an_ioc_under_his_price_less_the_tolerance_the_mandate_pin():
    """His 0.840: floor 0.83, take 0.83, rest 0.84. E14b pinned that bids
    at 0.70, 0.71 and 0.82 sent NO IOC and that the bid at 0.83 sent the
    one IOC at 0.83 and nowhere under -- the mandate's floor.

    RE-PINNED at E31 (FILL lane 31, 2026-09-10) and STRENGTHENED: the
    exit never leaves his own cent AT ALL. At every one of those bids,
    0.83 included, the maker wire is max(sell_wire(0.84), bid + 0.01) =
    0.84 -- HIS cent -- and the exit rests there. Where E14b sold a cent
    under him at 0.83 on the tolerance, this lane sells at 0.84 or not at
    all. The tolerance constant is unmoved (0.01, capped_env, floor 0.0)
    and MIRROR_EXIT_TOL keeps exit_terms as its one reader (docs 75).
    E4's held record `exit_out_of_tol` still rides the placement road, so
    the three bids under the take cent record it (0 -> 1) and the bid AT
    the take cent does not (0)."""
    for bid in (0.70, 0.71, 0.82):
        p, b = _exit_world(0.84, 432, entry=0.80)
        v = _Venue(bid=bid, ask=0.86, held={SLUG: 864}, ioc_fill=432.0)
        st = _tick(p, v, http=_mkt(432.0))
        assert [c[2:6] for c in _places(v)] == [(0.84, 432, True, GTC_TIF)], bid
        assert _census(st, "exit_take") == 0 and _census(st, "exit_take_rested") == 0, bid
        # E31: `exit_out_of_tol` 0 -> 1 at each of these three bids. E4's held record is kept
        # on the road that PLACES the exit (the exits presets read it, docs 75), and the take
        # cent 0.83 is over every one of 0.70 / 0.71 / 0.82: the touch is outside his
        # tolerance cent, so the tick records the quote and the floor it was read against.
        # No take is sent at any of them -- that is the pin this test exists for
        assert _census(st, "exit_out_of_tol") == 1 and _bbos(v).count(SLUG) == 1, bid
        assert b["last_plan"]["exit_out_of_tol"]["bid"] == bid, bid
        assert b["last_plan"]["exit_out_of_tol"]["floor"] == 0.83, bid
        assert (b["last_plan"]["exit_floor"], b["last_plan"]["exit_take"]) == (0.83, 0.83)
        assert b["last_plan"]["maker"]["clause"] == "his_cent", bid
    # the bid AT the take cent 0.83: E14b crossed here. E31 rests at 0.84 still
    p, b = _exit_world(0.84, 432, entry=0.80)
    v = _Venue(bid=0.83, ask=0.86, held={SLUG: 864}, ioc_fill=432.0)
    st = _tick(p, v, http=_mkt(432.0))
    assert [c[2:6] for c in _places(v)] == [(0.84, 432, True, GTC_TIF)] and b["ledger_net"] == 864
    assert _census(st, "exit_take") == 0 and _census(st, "exit_take_rested") == 0
    # the bid IS the take cent here, so the touch is INSIDE his tolerance and nothing is
    # recorded -- the same guard, the other way: `not at_or_through(SELL, 0.83, 0.86, 0.83)`
    # is False. E14b crossed on exactly this reading; E31 rests a cent over it instead
    assert _census(st, "exit_out_of_tol") == 0 and "exit_out_of_tol" not in b["last_plan"]
    assert b["last_plan"]["maker"] == {"wire": 0.84, "bound": 0.84, "his_cent": 0.84, "clause": "his_cent",
                                       "side": SELL, "bid": 0.83, "ask": 0.86, "at": NOW, "hint": None}
    assert rules.MIRROR_EXIT_TOL == 0.01
    # the exit's cent is rules.maker_wire's, off exit_terms' `px` (his own level) and
    # the BID -- the touch it must not cross. exit_terms is byte for byte and keeps
    # MIRROR_EXIT_TOL as its one reader; `take` / `floor` are the plan's record alone
    assert not hasattr(ml, "_exit_take"), "E31: the exit take's wrapper is gone"
    from tests.test_e31_maker_only import _code
    src = inspect.getsource(ml._wire_for)
    assert "rules.maker_wire(side, his_px, r.bid, r.ask" in src
    code = _code(ml._wire_for)
    assert 'ex["take"]' not in code and 'ex["rest"]' not in code, "no exit cent of exit_terms is on the wire"


# ------------------------------------------------------ (5) book 661's shape

def _book_661(cancel_ok=True, ioc_fill=200.0, lift=0.0):
    """An exit rest of 609 @0.62 partially filled 89.24 (the row's shape),
    his newest sale now at 0.60 (the cent moved: a replace). The venue
    holds what the fill left: 1218 - 89.24."""
    p = _pool(fills=[_fill(M, "BUY", 1218, 0.65, NOW - 3000), _fill(M, "SELL", 300, 0.62, NOW - 1000),
                     _fill(M, "SELL", 309, 0.60, NOW - 500)],
              snap={M: 609.0, N: 0.0})
    b = p.add_book(ledger=1218, avg_cost=0.65)
    o = p.add_order(b, side=SELL, wire=0.62, qty=609, kind="reduce", placed_ts=NOW - 100)
    v = _Venue(bid=0.59, ask=0.61, held={SLUG: 1218 - 89.24}, ioc_fill=ioc_fill, cancel_ok=cancel_ok, lift=lift)
    v.rest("oid-1", "SELL", 0.62, 609, filled=89.24, avg=0.62)
    return p, b, o, v


def test_e14b_book_661_the_replace_cancel_books_the_partial_fill_and_the_remainder_alone_is_rested():
    """The cancel books the 89.24 (ledger 1129); the re-plan sizes off
    that ledger: 1129 - 609 = 520, never the row's 609.

    RE-PINNED at E31 (FILL lane 31, 2026-09-10): E14b sent an IOC for 520
    at the take cent 0.59 (through the bid), 200 filled, and the 320 left
    rested at 0.60. Now ONE post-only rest for the whole 520 goes out at
    0.60 -- max(sell_wire(his 0.60), 0.59 + 0.01) -- and a taker lifting
    200 of it at create books the same 200 and leaves the same 320
    standing. Same shares, same ledger, a cent better and one row."""
    p, b, o, v = _book_661(lift=200.0)
    st = _tick(p, v, http=_mkt(609.0))
    assert [c[1] for c in _cancels(v)] == ["oid-1"] and p.orders[o["id"]]["state"] == "cancelled"
    assert p.orders[o["id"]]["booked_filled"] == 89.24 and b["last_plan"]["replaced"] == "replace_cent"
    assert [c[2:6] for c in _places(v)] == [(0.60, 520, True, GTC_TIF)]
    assert b["ledger_net"] == 1129 - 200 and _census(st, "exit_take") == 0 and _census(st, "exit_take_rested") == 0
    assert _census(st, "maker_fill_at_create") == 1 and "exit_take_rested" not in b["last_plan"]
    assert [a[20] for a in _inserts(p)] == ["exit_rest"]
    opens = _opens(p)
    assert len(opens) == 1 and (opens[0]["wire"], opens[0]["qty"]) == (0.60, 520)


def test_e14b_book_661_no_rest_is_placed_while_the_cancels_row_is_non_terminal():
    """The venue refuses the cancel: the row is not terminal, the
    re-plan never reaches the placement, and NOTHING is sent. RE-PINNED
    at E31 only in its words -- there is no IOC to reach -- and the
    outcome is byte for byte E14b's: no place, no take, no rest."""
    p, b, o, v = _book_661(cancel_ok=False)
    st = _tick(p, v, http=_mkt(609.0))
    assert {c[1] for c in _cancels(v)} == {"oid-1"} and not _places(v)
    assert _census(st, "exit_take") == 0 and _census(st, "exit_take_rested") == 0
    assert "exit_take_rested" not in (b["last_plan"] or {})
    assert p.orders[o["id"]]["state"] not in ("filled", "cancelled", "expired")


# ------------------------------------ (6) frozen / tripped / abandoned / non-terminal

# RE-PINNED WHOLE at E31 (FILL lane 31, 2026-09-10). E14b drove ml._exit_take at the unit
# through a fake ml._place, because the rule under test was CONDITIONAL: the rest went out
# only after a withheld IOC or a partial take, and only inside a set of guards. There is no
# _exit_take and no IOC, so the condition is gone: the exit rests on every tick it plans one.
# What remains true, and is pinned here through the WORKER instead of a deleted wrapper, is
# the other half of E14b's finding -- the quantity is bounded by the ledger as it reads NOW
# (ml._sell_qty, untouched), and a frozen / tripped / abandoned / non-terminal book still
# sends nothing.


def _reduce_world(ledger, held, his_px=0.31, entry=0.40):
    """A long book of `ledger` with the venue at `held`, his newest move a
    SELL at `his_px` that leaves him holding nothing on this market: the
    plan is a full reduce, priced at ceil(his) = 0.31 on a 0.29 / 0.33
    book (max(0.31, 0.29 + 0.01) is his own cent)."""
    p = _pool(fills=[_fill(M, "BUY", 1000, entry, NOW - 3000), _fill(M, "SELL", 1000, his_px, NOW - 1000)],
              snap={M: 0.0, N: 0.0})
    b = p.add_book(ledger=ledger, avg_cost=entry)
    v = _Venue(bid=0.29, ask=0.33, held={SLUG: held})
    return p, b, v


def test_e14b_the_exit_rest_is_bounded_by_the_ledger_as_it_reads_now():
    """E14b bounded the same-tick remainder by min(the plan's unfilled,
    _sell_qty(the ledger now)). E31 rests the whole exit, and the SAME
    bound holds it: never more shares than the book reads it holds."""
    # the ledger under the plan: the rest is the ledger's figure, not the plan's
    p, b, v = _reduce_world(ledger=120, held=120.0)
    st = _tick(p, v, http=_mkt(0.0))
    assert [c[2:6] for c in _places(v)] == [(0.31, 120, True, GTC_TIF)]
    assert [a[20] for a in _inserts(p)] == ["exit_rest"] and _census(st, "exit_take_rested") == 0
    # a fractional venue figure ceils to the whole share it can sell
    p2, b2, v2 = _reduce_world(ledger=121, held=120.5)
    _tick(p2, v2, http=_mkt(0.0))
    assert [c[2:6] for c in _places(v2)] == [(0.31, 121, True, GTC_TIF)]
    # a flat ledger: nothing to sell, nothing sent, named
    p3, b3, v3 = _reduce_world(ledger=0, held=0.0)
    st3 = _tick(p3, v3, http=_mkt(0.0))
    assert not _places(v3) and _census(st3, "exit_take_rested") == 0
    # the bound itself, at the unit: _sell_qty is untouched by this lane and by E31
    assert ml._sell_qty({"ledger_net": 300, "_held": 300.0}, 200) == 200
    assert ml._sell_qty({"ledger_net": 100, "_held": 300.0}, 150) == 100
    assert ml._sell_qty({"ledger_net": 250, "_held": 120.5}, 150) == 121
    assert ml._sell_qty({"ledger_net": 0, "_held": 0.0}, 150) == 0


def test_e14b_a_tripped_or_abandoned_tick_sends_no_exit_rest(monkeypatch):
    """Two of the four guards E14b pinned at the unit, through the worker
    (the frozen book is the next test's): an abandoned tick and a book
    whose row is non-terminal send NOTHING -- and now there is no IOC to
    send either, so the pin is the whole placement, not just the rest."""
    # an abandoned tick: the venue's quote read raises, every book abandons
    p2, b2, v2 = _reduce_world(ledger=300, held=300.0)
    v2.raise_bbo = True
    st2 = _tick(p2, v2, http=_mkt(0.0))
    assert not _places(v2) and _census(st2, "exit_take_rested") == 0
    # a non-terminal row: the standing order cannot be read terminal, so nothing new goes
    p3, b3, v3 = _reduce_world(ledger=300, held=300.0)
    p3.add_order(b3, side=SELL, wire=0.31, qty=300, kind="reduce", placed_ts=NOW - 100)
    v3.rest("oid-1", "SELL", 0.31, 300)
    v3.cancel_ok = False
    st3 = _tick(p3, v3, http=_mkt(0.0))
    assert not _places(v3) and _census(st3, "exit_take_rested") == 0


def test_e14b_a_frozen_books_exit_rests_at_his_cent_and_nothing_of_this_lane_fires():
    """E5's frozen long book (ledger 300, venue 600, he reduced to 100):
    E14b pinned the frozen reduce's IOC for 500 at 0.30 filling 100, with
    no same-tick rest because the book is frozen.

    RE-PINNED at E31 (FILL lane 31, 2026-09-10): the frozen reduce is a
    post-only rest at his own cent 0.31 -- max(sell_wire(0.31), 0.30 +
    0.01) -- one tick over the bid, where E14b crossed AT the bid. The
    E5 / E16 pins stand: the book stays frozen, the exit still goes, and
    none of this lane's names fire."""
    from tests.test_e5_frozen_exits import _frozen_long
    from tests.test_e5_frozen_exits import _pool as _e5_pool
    from tests.test_mirror_live_worker import _his
    p = _e5_pool(fills=_his(300, sold=200), snap=None)
    b = _frozen_long(p)
    v = _Venue(held={SLUG: 600}, bid=0.30, ask=0.32, ioc_fill=100)
    st = _tick(p, v, http=_mkt(100))
    assert [c[2:6] for c in _places(v)] == [(0.31, 500, True, GTC_TIF)]
    assert _census(st, "frozen_reduce") == 1 and _census(st, "exit_take") == 0 and _census(st, "exit_take_rested") == 0
    assert b["state"] == "frozen" and "exit_take_rested" not in b["last_plan"]


# ------------------------------------------------ (7) the short cover, untouched

def test_e14b_the_short_cover_emits_none_of_the_new_names_and_the_flatten_send_is_untouched(monkeypatch):
    _shorts_on(monkeypatch)
    # S4: the cover is a post-only rest that a taker lifts in part at create (E31:
    # `lift` where `ioc_fill` filled the cover's IOC); nothing of this lane fires
    p, b, v = _flip_world(ioc_fill=100.0, lift=100.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.31, 300, True, GTC_TIF)] and b["ledger_net"] == -200
    assert _census(st, "short_cover_take") == 0 and _census(st, "short_cover_rest") == 1
    assert _census(st, "exit_take_rested") == 0 and "exit_take_rested" not in b["last_plan"]
    # the call sites: RE-PINNED at E31 -- `_exit_take` is DELETED, so there are none, and
    # `_flatten_send` (the flatten's slippage leg) is deleted with it. The exits go out of
    # _act through _place like every other order
    src = inspect.getsource(ml)
    assert not hasattr(ml, "_exit_take") and not hasattr(ml, "_flatten_send")
    assert "_exit_take(" not in src and "exit_take_rested" not in _code_of(ml)
    for fn in (ml._flatten_vanished, ml._frozen_exit, ml._frozen_reduce_on_fill,
               ml._exit_held, ml._cover_qty, ml._s4_refusal, ml._sell_qty):
        s = inspect.getsource(fn)
        assert "_exit_take(" not in s and "exit_take_rested" not in s, fn.__name__
    assert "exit_take_rested" not in inspect.getsource(rules)
    # the exit terms are the rule's: E4's cents on both sides
    # (FILL lane 3 re-pinned: the band keys, equal to the take / the cover at the default band)
    assert rules.exit_terms(SELL, 0.549) == {"px": 0.549, "floor": 0.539, "take": 0.54, "rest": 0.55,
                                             "band_floor": 0.539, "take_band": 0.54}
    assert rules.exit_terms(BUY, 0.31) == {"px": 0.31, "ceiling": 0.32, "cover": 0.32, "take": 0.32, "rest": 0.31,
                                           "band_ceiling": 0.32, "cover_band": 0.32}
    assert ml.REST_REREAD_SKIPPED == ("rest_reread_capped", "rest_quote_unread")
    assert not hasattr(ml, "IOC_SKIPPED")


def _code_of(obj):
    from tests.test_e31_maker_only import _code
    return _code(obj)


# ------------------------------------------------------ (8) the E18 triple

def test_e14b_the_census_name_sits_before_drift_smaller_open_and_the_tail_pins_hold():
    keys = ml.CENSUS_KEYS
    assert keys.count("exit_take_rested") == 1 and len(set(keys)) == len(keys)
    # E14 (FILL lane 2) landed after this lane and placed `take_in_band` nearer the key (-14 -> -15);
    # FILL lane 3 (three names), T2 (two), FILL lane 5 (three), E22 (FILL lane 22, four) and FILL lane 11 (one) placed theirs after these (-15 -> -28, -14 -> -27) -- FILL lane 16 (one name) and E21 (FILL lane 10, six) landed first, so every index past this lane's six moved by seven more
    # E23 (FILL lane 23) placed its six names nearer the key (-28 / -27 -> -34 / -33, -21:-18 -> -27:-24, -14 -> -20)
    # FILL lane 24 (E24, the desk's hand) placed its four names nearer the key (-41 / -40 -> -45 / -44, -34:-31 -> -38:-35, -27 / -26 / -25 / -20 -> -31 / -30 / -29 / -24)
    assert keys[-69] == "exit_take_rested" and keys[-68] == "take_in_band"
    assert keys[-62:-59] == ("he_holds", "he_holds_unread", "reopen_refused")
    assert keys[-55] == "cand_market_closed_db"
    assert keys[-54] == "turn_woke_fast" and keys[-53] == "fast_order_open" and keys[-48] == "fast_status_unread"
    assert keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-1] == "cand_terminal_skipped" and keys[-8:-4] == ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")
    # RE-PINNED at E31: this lane's name is RETIRED -- it stays DECLARED (every from-end
    # index above is unmoved) and reads a documentary zero, because the rest it counted no
    # longer waits on a withheld take. No emit site is left
    assert ml._new_stats()["census"]["exit_take_rested"] == 0
    assert '_mirror_stop("exit_take_rested"' not in inspect.getsource(ml)
    # no knob of the lane's own: no env read added to the worker or the rules
    assert "_env_float(" not in inspect.getsource(ml._wire_for) and "capped_env" not in inspect.getsource(ml._wire_for)
    # (FILL lane 3, 2026-09-08, re-pinned: the exit band rail MIRROR_EXIT_TAKE_BAND is lane 3's, one
    # downward-only capped_env; this lane still adds none of its own)
    assert inspect.getsource(rules).count('capped_env("MIRROR_EXIT_TAKE') == 1
    assert 'capped_env("MIRROR_EXIT_TAKE_BAND", 0.01, floor=0.0)' in inspect.getsource(rules)


def test_e14b_the_name_is_retired_and_can_no_longer_be_emitted(monkeypatch):
    """RE-PINNED at E31: E14b's driver ran the two shapes to emit
    `exit_take_rested` for the worker file's coverage read. The name is
    retired, so the driver runs the same two shapes and proves the census
    stays at ZERO; the worker file's coverage read carries the name in
    its `unreachable` set with this lane named."""
    test_e14b_book_334_the_exit_rests_the_whole_quantity_at_his_cent_the_same_tick(monkeypatch)
    test_e14b_book_467_the_exit_rests_whole_and_a_taker_lifting_part_of_it_leaves_the_rest_standing()
    assert ml._MIRROR_CENSUS.get("exit_take_rested|rn1", 0) == 0
    assert ml._MIRROR_CENSUS.get("exit_take|rn1", 0) == 0


def test_e14b_the_decision_words_are_the_existing_ones_and_059s_list_stands():
    assert rules.order_decision("reduce", True, False) == "take"
    assert rules.order_decision("reduce", False, False) == "exit_rest"
    sql = (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "('rest', 'take', 'cover', 'exit_rest'; 'take_in_band' is" in sql
    assert "exit_take" not in sql, "no new decision word"


def test_e14b_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E14b \(2026-09-08, FILL lane 1\)", doc, re.M), "the E14b section header"
    for k in NEW_NAMES + ("exit_rest", "MIRROR_EXIT_TOL", "bid_moved", "ioc_quote_unread",
                          "take_filled", "_sell_qty", "test_e14b_exit_take_rests.py", "334", "467", "419", "285", "661"):
        assert k in doc, k
