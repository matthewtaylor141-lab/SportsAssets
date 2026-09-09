"""E23 (FILL lane 23) adversarial review pins: book 986's REAL shape (the
standing row 411.95 under the whole-share ledger column 413), the signed
sum over a cancelled reduce's late fills, and the four surviving mutants'
killers (the memo re-read that carries no more; the unbooked part's price
over a booked row; the log's window; the in-code candidate predicate).
Fixture world: tests/test_e23_cancel_fill_adopt.py's."""
from __future__ import annotations

import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import mirror as mi
from sportsassets.workers import mirror_live as ml
from tests.test_e23_cancel_fill_adopt import (  # noqa: F401 -- the autouse rails ride along
    FILLED_AT, FIRST_PLACED, HIS, LEDGER, NEW_NAMES, REST_QTY, ROWS_986, SURPLUS, VENUE, WIRE,
    _LateVenue, _book_986, _dis, _live_rest, _log_fill, _pool, _recent, _row, _venue,
)
from tests.test_e5_frozen_exits import _E5Pool, _plan_exit
from tests.test_mirror_live_worker import (  # noqa: F401
    BUY, M, N, NOW, SELL, SLUG, _armed, _census, _his, _mkt, _places, _ratio_fills, _tick,
)


# ------------------------------------------------ HIGH-1: 986's real shape


def test_review_986s_real_shape_the_standing_row_411_95_under_the_column_413_adopts_the_logs_803_05():
    """book_986_1426.txt line 998: ledger 413; line 1019: the standing row
    412015 filled 411.95 (= 6.13 + 405.82, lines 1003-1004) -- the whole-
    share column carries +1.05 of rounding over the standing row (every
    booking rounds: _book_fill's int(round(...))). frozen_1423.txt line 998:
    venue 1215. The fills the venue's own ledger holds against the ten
    cancelled ids are 1215 - 411.95 = 803.05 -- never the column's 802.
    The adoption must judge the log against the ledger the booking would
    LEAVE, inside the freeze's own tolerance (mi.VENUE_LEDGER_TOL_SHARES),
    not against the column's surplus to 1e-6: then 803.05 books, the
    standing row reads 1215.00, the column 1216 (its rounding class), the
    venue 1215 agrees within the tolerance and E16's thaw takes the book."""
    p = _pool()
    b, rows = _book_986(p)
    standing = p.rows[b["standing_row_id"]]
    standing["filled_shares"] = 411.95
    standing["orig_shares"] = 411.95
    v = _venue(trades=[_log_fill("oid-6292", 803.05)])
    st = _tick(p, v, http=_mkt(HIS))
    o = _row(rows, "oid-6292")
    assert _census(st, "disagree_fill_adopted") == 1, "the cure must fire on the book it was built for"
    assert _census(st, "disagree_fill_ambiguous") == 0 and _census(st, "disagree_fill_unexplained") == 0
    assert o["booked_filled"] == pytest.approx(803.05) and o["reason"] == "adopted from the trade log after the cancel"
    assert standing["filled_shares"] == pytest.approx(1215.0), "the standing row is the venue's own figure"
    assert b["ledger_net"] == 1216, "the column's rounding (task 24's class), inside the freeze's tolerance of the venue"
    assert abs(VENUE - b["ledger_net"]) <= mi.VENUE_LEDGER_TOL_SHARES
    assert _dis(b)["verdict"] == "adopted" and _dis(b)["shares"] == pytest.approx(803.05) and _dis(b)["delta"] == SURPLUS
    assert _plan_exit(b)["held"] == "frozen_fill_this_tick" and b["state"] == "frozen"
    # the existing thaw: one_read on the next fresh agreeing tick, thawed on the one after
    st2 = _tick(p, _venue(trades=[]), now=NOW + 30, http=_mkt(HIS))
    assert b["state"] == "frozen" and b["last_plan"]["thaw_held"] == "one_read" and _census(st2, "thaw_held") == 1
    st3 = _tick(p, _venue(trades=[]), now=NOW + 60, http=_mkt(HIS))
    assert b["state"] == "live" and b["frozen_reason"] is None and _census(st3, "thaw_held") == 0
    assert b["last_plan"]["kind"] == "increase" and b["last_plan"]["qty"] == 2876 - 1216


def test_review_a_cancelled_reduces_late_fills_count_against_the_surplus_by_their_leg():
    """A long book frozen venue_ledger_disagree at ledger 1000 / venue
    1200 with a cancelled BUY rest (2467 @0.56, booked 0) AND a cancelled
    SELL reduce rest (300 @0.60, booked 0, replace-cancelled) whose fills
    both outran their cancels: the log names BUY 300 under the rest's id
    and SELL 100 under the reduce's -- the net +200 IS the surplus. Summed
    unsigned (400) the adoption reads ambiguous and the book stays frozen
    for good; signed by the leg both rows book and the ledger reads the
    venue."""
    p = _pool()
    b = p.add_book(ledger=1000, avg_cost=WIRE, ratio=0.1, target=2876, state="frozen",
                   frozen_reason="venue_ledger_disagree", frozen_ts=NOW - 1200)
    ob = p.add_order(b, side=BUY, wire=WIRE, qty=REST_QTY, order_id="oid-b", state="cancelled",
                     placed_ts=FIRST_PLACED, kind="increase", booked=0.0, venue_state="canceled",
                     reason="ttl", done_at=FIRST_PLACED + 600.0)
    os_ = p.add_order(b, side=SELL, wire=0.60, qty=300, order_id="oid-s", state="cancelled",
                      placed_ts=FIRST_PLACED + 60.0, kind="reduce", booked=0.0, venue_state="canceled",
                      reason="replace", done_at=FIRST_PLACED + 660.0)
    v = _venue(held=1200, trades=[_log_fill("oid-b", 300.0, px=WIRE),
                                  _log_fill("oid-s", 100.0, px=0.60, ts=FILLED_AT + 5, side="SELL",
                                            order_qty=300.0, order_price=0.60)])
    st = _tick(p, v, http=_mkt(HIS))
    assert _census(st, "disagree_fill_adopted") == 1 and _census(st, "disagree_fill_ambiguous") == 0
    assert ob["booked_filled"] == 300.0 and os_["booked_filled"] == 100.0
    assert b["ledger_net"] == 1200 and b["realized_pnl"] == pytest.approx(100.0 * (0.60 - WIRE))
    assert len(_recent("disagree_fill_adopted", b["id"])) == 2


def test_review_the_adoption_is_judged_inside_the_freezes_own_tolerance_and_not_a_share_past_it():
    """The bound is mi.VENUE_LEDGER_TOL_SHARES (1.0), the freeze's own: the
    log naming 803 (the column would read 1216 against the venue's 1215,
    a share inside) adopts; 804 (two shares over) is ambiguous; 800 (two
    under) unexplained; 801 (one under) adopts. Nothing wider."""
    assert mi.VENUE_LEDGER_TOL_SHARES == 1.0
    for named, verdict, ledger_after in ((803.0, "adopted", 1216), (804.0, "ambiguous", LEDGER),
                                         (800.0, "unexplained", LEDGER), (801.0, "adopted", 1214)):
        ml._disagree_fill_read_at.clear()
        p = _pool()
        b, rows = _book_986(p)
        v = _venue(trades=[_log_fill("oid-6292", named)])
        st = _tick(p, v, http=_mkt(HIS))
        assert _dis(b)["verdict"] == verdict and b["ledger_net"] == ledger_after, (named, _dis(b))
        assert _census(st, "disagree_fill_" + verdict) == 1, named
        assert _row(rows, "oid-6292")["booked_filled"] == (named if verdict == "adopted" else 0.0)


# ---------------------------------------- the surviving mutants' killers


def test_review_the_memo_re_read_that_carries_no_more_counts_nothing_and_warns_nothing(caplog):
    """M07: the memoed row's re-read reads the cancel's own figure (0): the
    entry is dropped, nothing booked, nothing counted, no 'the booking
    answered' line -- a read that carries no more is not a booking."""
    p = _pool()
    b, o = _live_rest(p)
    v = _LateVenue(held={SLUG: 0}, bid=0.60, ask=0.61,
                   script={"oid-986": [(0.0, None), (0.0, None), RuntimeError("status down"), (0.0, None)]})
    v.rest("oid-986", price=WIRE, qty=REST_QTY)
    _tick(p, v, http=_mkt(HIS))
    assert o["id"] in ml._cancel_reread_pending
    v2 = _LateVenue(held={SLUG: 0}, bid=0.60, ask=0.61, script={"oid-986": [(0.0, None)]})
    v2.orders = v.orders
    with caplog.at_level("WARNING"):
        st2 = _tick(p, v2, now=NOW + 30, http=_mkt(HIS))
    assert o["id"] not in ml._cancel_reread_pending and p.orders[o["id"]]["booked_filled"] == 0.0
    assert all(_census(st2, k) == 0 for k in NEW_NAMES) and b["ledger_net"] == 0
    assert not [r for r in caplog.records if "the booking answered" in r.message]
    assert not _recent("cancel_fill_late", b["id"])


def test_review_the_unbooked_part_is_priced_from_its_own_fills_over_a_booked_row():
    """M16: 5908 booked 405.82 @0.56 by the cancel; the log names its id
    with 405.82 @0.56 (booked) and 802 @0.55 (the late fills): the delta
    books at 0.55 -- the id's notional less the booked part -- never at
    the id's whole average (0.5534)."""
    p = _pool()
    b, rows = _book_986(p)
    v = _venue(trades=[_log_fill("oid-5908", 405.82, px=WIRE, ts=FIRST_PLACED + 200, order_qty=2874.0),
                       _log_fill("oid-5908", 802.0, px=0.55, ts=FILLED_AT, order_qty=2874.0)])
    st = _tick(p, v, http=_mkt(HIS))
    o = _row(rows, "oid-5908")
    assert _census(st, "disagree_fill_adopted") == 1 and o["booked_filled"] == pytest.approx(1207.82)
    assert o["avg_px"] == pytest.approx(0.55, abs=1e-6), "the unbooked part's price, not the id's average"
    assert b["avg_cost"] == pytest.approx((413 * WIRE + 802 * 0.55) / 1215, abs=1e-6)
    rec = _recent("disagree_fill_adopted", b["id"])
    assert len(rec) == 1 and rec[0]["px"] == pytest.approx(0.55, abs=1e-6) and rec[0]["shares"] == 802.0


def test_review_a_fill_dated_outside_the_window_is_never_matched():
    """M20: a fill under our id dated before the earliest row's placement
    less the skew, or after the tick's clock, is outside the window the
    walk was read in: unexplained, nothing booked."""
    for ts in (FIRST_PLACED - le._ORPHAN_SKEW_S - 1.0, NOW + 5.0):
        ml._disagree_fill_read_at.clear()
        p = _pool()
        b, rows = _book_986(p)
        v = _venue(trades=[_log_fill("oid-6292", 802.0, ts=ts)])
        st = _tick(p, v, http=_mkt(HIS))
        assert _census(st, "disagree_fill_unexplained") == 1 and _census(st, "disagree_fill_adopted") == 0, ts
        assert _row(rows, "oid-6292")["booked_filled"] == 0.0 and b["ledger_net"] == LEDGER


class _DriftedPool(_E5Pool):
    """A pool whose cancelled-rows statement has drifted: it hands back
    EVERY cancelled row of the book, booked whole or not."""

    def _run(self, kind, sql, a):
        s = " ".join(str(sql).split())
        if "ml-cancelled-rows" in s:
            rows = [dict(o) for o in sorted(self.orders.values(), key=lambda o: (o["placed_ts"], o["id"]))
                    if o["book_id"] == a[0] and o["state"] == "cancelled"]
            return rows
        return super()._run(kind, sql, a)


def test_review_the_in_code_predicate_keeps_a_drifted_statement_from_widening_the_candidates():
    """M26: with every row but 6292 booked whole and a statement that hands
    them all back, the worker's own re-read of the predicate leaves 6292
    the one candidate: the log's 50 for 6263 (a whole row) is ignored,
    6292's 802 adopted; without the predicate 6263's 50 under its 2,467
    would read ambiguous and nothing would book."""
    one = tuple((oid, qty, (0.0 if oid == "oid-6292" else float(qty)), why) for oid, qty, _bk, why in ROWS_986)
    p = _DriftedPool(fills=_his(HIS, WIRE), snap={M: HIS, N: 0.0}, snap_at=NOW - 40, ratio_fills=_ratio_fills())
    b, rows = _book_986(p, rows=one)
    v = _venue(trades=[_log_fill("oid-6292", 802.0), _log_fill("oid-6263", 50.0, ts=FILLED_AT + 2)])
    st = _tick(p, v, http=_mkt(HIS))
    assert _census(st, "disagree_fill_adopted") == 1 and _census(st, "disagree_fill_ambiguous") == 0
    assert _dis(b)["rows"] == [_row(rows, "oid-6292")["id"]] and b["ledger_net"] == VENUE
    assert _row(rows, "oid-6263")["booked_filled"] == float(REST_QTY)


def test_review_a_short_books_surplus_is_its_leg_in_ledger_space():
    """A SHORT book frozen venue_ledger_disagree (ledger -413, venue -1215:
    its adds are SELLs of the long token and take the ledger DOWN) with one
    cancelled add rest whose late fills the log names under its id: the
    surplus is the leg's, adopted; the ledger reads the venue."""
    import asyncio
    from tests.test_mirror_live_worker import CID
    p = _pool()
    b = p.add_book(ledger=-413, avg_cost=0.44, ratio=0.1, target=-2876, state="frozen",
                   frozen_reason="venue_ledger_disagree", frozen_ts=NOW - 1200, intent="ORDER_INTENT_BUY_SHORT")
    p.rows[b["standing_row_id"]]["filled_shares"] = 413.0
    p.rows[b["standing_row_id"]]["orig_shares"] = 413.0
    o = p.add_order(b, side=SELL, wire=0.44, qty=REST_QTY, order_id="oid-s1", state="cancelled",
                    placed_ts=FIRST_PLACED, kind="increase", booked=0.0, venue_state="canceled",
                    reason="ttl", done_at=FIRST_PLACED + 600.0)
    v = _venue(held=-1215, trades=[_log_fill("oid-s1", 802.0, px=0.44, side="SELL", order_price=0.44)])
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW, stats=ml._new_stats())
    t.walk_at = NOW
    r = ml._Reading("rn1", CID, SLUG, M, N, [], 0.0, HIS, {}, None, False, False, False, None, None,
                    0.43, 0.44, 0.435, -1215.0, 0.0, None, True)
    plan = {}
    out = asyncio.run(ml._disagree_fill_adopt(t, b, r, -413, 0.0, {}, plan))
    assert out == "adopted", plan.get("disagree_fill")
    assert b["ledger_net"] == -1215 and o["booked_filled"] == 802.0 and plan["disagree_fill"]["delta"] == -802
