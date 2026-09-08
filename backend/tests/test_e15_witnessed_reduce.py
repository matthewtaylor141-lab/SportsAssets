"""E15 (2026-09-08, task 69; PNL program lane 3+4): a reduce on EVERY
book needs his witness -- a reducing fill of his on the book's leg
clocked after the plan's reference (`reduce_ref`); a target that falls
for any reason of ours (the ratio step at the $10 line, a reading
smaller than his fills, a cap, the collapse) HOLDS at the ledger,
named `reduce_unwitnessed` = {from, to, cause}. The two-sided burst
(278) sizes on the net of his fills (`flip_burst_net`); the flip
reopen a fill of his witnessed cuts first sight at the reference's
clock (`flip_witness` / `catchup.flip_reopen`); the drift his chain
fills explain sizes the increase on the fills' net
(`drift_fills_explain`). Driven on the real planner through the worker
file's fakes, as the E12 / E12b pins are.
"""
import re
import inspect
import pathlib

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_e12_flow_only import _flow_book
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    BUY, CID, M, N, NOW, SELL, SHORT, SLUG, _armed, _cancels, _census, _fill, _gone, _Http, _mkt,
    _places, _pool, _rails_2026_09_06, _short_book, _shorts_on, _tick, _Venue,
)

IOC = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
ROOT = pathlib.Path(__file__).resolve().parents[2]
REF = {"target": 100, "at": NOW - 30.0}          # the last plan: target 100, clocked 30 s ago


WALK = _Http(rows=[])           # no per-market answer: the whole-book walk is the reading


def _long_world(monkeypatch, *more, net, ledger=100, last_plan=None, ratio=0.10, snap_at=NOW - 40):
    """A long book of `ledger` on his 1,000 built at 0.31 an hour ago,
    the walk reading `net`; `more` are the tick's newer fills of his.
    The per-market read agrees with the walk unless a test hands WALK."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 1_000, 0.31, NOW - 3000)] + list(more), snap={M: float(net), N: 0.0},
              snap_at=snap_at)
    b = p.add_book(ledger=ledger, ratio=ratio, avg_cost=0.31, last_plan=dict(REF) if last_plan is None else last_plan)
    return p, b


# ------------------------------------------------------------ the arithmetic

def test_e15_the_witness_reads_the_books_own_leg_on_either_axis_and_nothing_with_no_clock():
    since = NOW - 300
    sell_m = _fill(M, "SELL", 100, 0.70, NOW - 100)
    buy_n = _fill(N, "BUY", 40, 0.30, NOW - 90)
    buy_m = _fill(M, "BUY", 60, 0.70, NOW - 80)
    old = _fill(M, "SELL", 500, 0.70, NOW - 400)
    assert mi.reducing_on([sell_m, buy_n, buy_m, old], M, N, False, since) == 140.0
    assert mi.reducing_on([sell_m, buy_n, buy_m, old], M, N, True, since) == 60.0
    assert mi.reducing_on([sell_m], M, N, False, None) == 0.0 and mi.reducing_on([], M, N, False, since) == 0.0


def test_e15_the_burst_is_two_sided_or_nothing_and_reads_the_net_on_the_leg():
    since = NOW - 300
    yes = _fill(M, "BUY", 4_415, 0.09, NOW - 10)
    no = _fill(N, "BUY", 1_414, 0.90, NOW - 8)
    assert mi.burst_since([yes, no], M, N, True, since) == {"adds": 1_414.0, "reductions": 4_415.0, "net": -3_001.0}
    assert mi.burst_since([yes, no], M, N, False, since) == {"adds": 4_415.0, "reductions": 1_414.0, "net": 3_001.0}
    assert mi.burst_since([no], M, N, True, since) is None and mi.burst_since([yes], M, N, True, since) is None
    assert mi.burst_since([yes, no], M, N, True, None) is None


def test_e15_the_drift_his_chain_fills_explain_and_the_poll_rows_never_do():
    at = NOW - 40
    chain = _fill(M, "BUY", 200, 0.31, NOW - 20, detected_at=NOW - 15, source="chain")
    poll = _fill(M, "BUY", 200, 0.31, NOW - 20, detected_at=NOW - 15, source="poll")
    assert mi.drift_explained([chain], M, N, False, 1_200.0, 1_000.0, at) == {"fills_after": 200.0, "delta": 200.0}
    assert mi.drift_explained([poll], M, N, False, 1_200.0, 1_000.0, at) is None
    assert mi.drift_explained([chain], M, N, False, 1_300.0, 1_000.0, at) is None, "unexplained by 100"
    assert mi.drift_explained([chain], M, N, False, 800.0, 1_000.0, at) is None, "under the snapshot: a sale's, never this rule's"
    assert mi.drift_explained([chain], M, N, False, None, 1_000.0, at) is None
    assert mi.drift_explained([chain], M, N, False, 1_200.0, 1_000.0, None) is None
    s1 = _fill(N, "BUY", 200, 0.69, NOW - 20, detected_at=NOW - 15, source="s1")
    assert mi.drift_explained([s1], M, N, True, -1_200.0, -1_000.0, at) == {"fills_after": 200.0, "delta": 200.0}


# ------------------------------------------------------------- the planner

def test_e15_451s_shape_the_ratio_step_at_the_ten_dollar_line_never_sells(monkeypatch):
    """Exact copy 16 @0.39; he adds 100 (his $45 at the mark > $20): the
    ratio steps to 0.1 for life, the target reads 11 under our 16 -- and
    NOTHING is sold: no fill of his reduced the leg. Held, named."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 16, 0.39, NOW - 3000),
                     _fill(M, "BUY", 100, 0.39, NOW - 20, detected_at=NOW - 15)],
              snap={M: 116.0, N: 0.0})
    b = p.add_book(ledger=16, ratio=1.0, avg_cost=0.39, last_plan={"target": 16, "at": NOW - 30.0})
    v = _Venue(bid=0.38, ask=0.40, held={SLUG: 16})
    st = _tick(p, v, http=_mkt(116.0))
    assert b["ratio"] == 0.10 and b["target"] == 11 and b["ledger_net"] == 16
    assert not _places(v) and not _cancels(v) and _census(st, "ratio_stepped") == 1
    lp = b["last_plan"]
    assert lp["reduce_unwitnessed"] == {"from": 16, "to": 11, "cause": "ratio_stepped"}
    assert b["last_reason"] == "reduce_unwitnessed" and lp["reduce_ref"] == {"target": 16, "at": NOW - 30.0}
    # the reference stands: the next tick holds the same way
    _tick(p, _Venue(bid=0.38, ask=0.40, held={SLUG: 16}), now=NOW + 30, http=_mkt(116.0))
    assert b["ledger_net"] == 16 and not _places(v) and b["last_plan"]["reduce_ref"]["target"] == 16


def test_e15_a_witnessed_25_percent_sale_reduces_25_percent_at_his_price_within_the_tolerance(monkeypatch):
    sale = _fill(M, "SELL", 250, 0.70, NOW - 10, detected_at=NOW - 5)
    p, b = _long_world(monkeypatch, sale, net=750.0)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    st = _tick(p, v, http=_mkt(750.0))
    assert b["target"] == 75 and _places(v)[0][2:6] == (0.69, 25, True, IOC) and b["ledger_net"] == 75
    assert _census(st, "exit_take") == 1 and "reduce_unwitnessed" not in b["last_plan"]
    assert b["last_plan"]["reduce_ref"] == {"target": 75, "at": NOW - 5}, "the reference moves to his sale's clock"
    assert abs(0.69 - 0.70) <= float(rules.MIRROR_EXIT_TOL) + 1e-9


def test_e15_a_witnessed_sale_is_sized_on_his_fills_never_on_a_reading_that_lags_them(monkeypatch):
    """He sells 100 of 1,000 (10 %) as we watch; the walk (drift 22 %)
    reads 700. The landed rule sized the reduce from the smaller reading
    (target 70: 30 sold, 20 on a sale he never made). Now the witnessed
    reduce is HIS fills' 10 %: 10 at his price, the reading's excess
    waits for the fills."""
    sale = _fill(M, "SELL", 100, 0.70, NOW - 10, detected_at=NOW - 5)
    p, b = _long_world(monkeypatch, sale, net=700.0)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=10.0)
    _tick(p, v, http=WALK)
    lp = b["last_plan"]
    assert b["target"] == 90 and _places(v)[0][2:6] == (0.69, 10, True, IOC) and b["ledger_net"] == 90
    assert "reduce_unwitnessed" not in lp and lp["reduce_ref"] == {"target": 90, "at": NOW - 5}
    assert lp["drift"] > float(rules.MIRROR_DRIFT_MAX) and lp["net"] == 700.0, "the reading stays on the row"


def test_e15_his_witnessed_full_exit_is_our_full_exit_and_the_confirmed_vanish_keeps_its_reader(monkeypatch):
    p, b = _long_world(monkeypatch, _fill(M, "SELL", 1_000, 0.70, NOW - 10, detected_at=NOW - 5), net=0.0)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=100.0)
    _tick(p, v, http=_gone())
    assert b["last_plan"]["kind"] == "flatten_vanished" and b["ledger_net"] == 0
    assert _places(v)[0][2:6] == (0.69, 100, True, IOC) and "reduce_unwitnessed" not in b["last_plan"]
    # a vanish with NO fill of his (the data API confirms him gone): its own reader, untouched
    p2, b2 = _long_world(monkeypatch, net=0.0)
    p2.fills[:] = []
    v2 = _Venue(bid=0.29, ask=0.31, held={SLUG: 100})
    _tick(p2, v2, http=_gone())
    assert b2["last_plan"]["kind"] == "flatten_vanished" and "reduce_unwitnessed" not in b2["last_plan"]


def test_e15_a_snapshot_smaller_than_his_fills_holds_and_names_the_reading(monkeypatch):
    """The walk reads 800 against his fills' 1,000 (drift 20 %): the
    landed rule sized the reduce from the smaller reading and sold 20 on
    a sale he never made. Now: held, `reading`, nothing sent."""
    p, b = _long_world(monkeypatch, net=800.0)
    v = _Venue(bid=0.30, ask=0.32, held={SLUG: 100})
    st = _tick(p, v, http=WALK)
    assert b["target"] == 80 and b["ledger_net"] == 100 and not _places(v) and not _cancels(v)
    assert b["last_plan"]["reduce_unwitnessed"] == {"from": 100, "to": 80, "cause": "reading"}
    # the drift refusal is on the plan (the census names `drift` on a
    # refused INCREASE alone, as the landed rule's pin reads it)
    assert b["last_reason"] == "reduce_unwitnessed" and b["last_plan"]["drift"] > float(rules.MIRROR_DRIFT_MAX)
    assert _census(st, "reduce_unwitnessed") == 0, "no census name: the verdict lives on the plan row"


def test_e15_278s_two_sided_burst_sizes_on_the_net_of_the_burst_never_a_sale_of_yes_at_his_no_cent(monkeypatch):
    """A short of 400 on his -4,980 (No bought at 0.11). In one tick he
    buys 4,415 Yes @0.09 AND 1,414 No @0.90: the net is -1,979, a cover
    of ours -- never an increase of the short at 1 - 0.90 sized on the
    No leg alone (the 24 @0.12 and 292 @0.07 we sold on 278)."""
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    p = _pool(fills=[_fill(N, "BUY", 4_980, 0.11, NOW - 3000),
                     _fill(M, "BUY", 4_415, 0.09, NOW - 10, detected_at=NOW - 9),
                     _fill(N, "BUY", 1_414, 0.90, NOW - 9, detected_at=NOW - 8)],
              snap={M: 4_415.0, N: 6_394.0})
    b = _short_book(p, ledger=-400, avg=0.11, ratio=0.10, last_plan={"target": -498, "at": NOW - 30.0})
    v = _Venue(bid=0.09, ask=0.11, held={SLUG: -400})
    _tick(p, v, http=_mkt(4_415.0, 6_394.0))
    lp = b["last_plan"]
    assert b["target"] == -197 and lp["kind"] == "reduce" and "reduce_unwitnessed" not in lp
    assert lp["flip_burst_net"] == {"adds": 1_414.0, "reductions": 4_415.0, "net": -3_001.0}
    assert [c[3] for c in _places(v)] == [203] and lp["side"] == BUY, "the cover of 203, never an increase of the short"


def test_e15_278s_reading_that_lags_his_yes_leg_holds_the_cover_until_his_fill_lands(monkeypatch):
    """Only his No leg is ingested (fills -6,394); the venue already
    shows -1,979: drift, the smaller reading, a cover of 203 -- on a
    reading. Held: no fill of his reduced the leg yet. And no increase
    of the short at 1 - 0.90 either (one leg is no burst)."""
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    p = _pool(fills=[_fill(N, "BUY", 4_980, 0.11, NOW - 3000),
                     _fill(N, "BUY", 1_414, 0.90, NOW - 9, detected_at=NOW - 8)],
              snap={M: 4_415.0, N: 6_394.0})
    b = _short_book(p, ledger=-400, avg=0.11, ratio=0.10, last_plan={"target": -498, "at": NOW - 30.0})
    v = _Venue(bid=0.09, ask=0.11, held={SLUG: -400})
    st = _tick(p, v, http=_mkt(4_415.0, 6_394.0))
    lp = b["last_plan"]
    assert lp["reduce_unwitnessed"]["cause"] == "reading" and not _places(v) and b["ledger_net"] == -400
    assert "flip_burst_net" not in lp and lp["drift"] > float(rules.MIRROR_DRIFT_MAX) and _census(st, "drift") == 0


def test_e15_the_short_mirror_image_his_buy_of_the_long_token_witnesses_the_cover(monkeypatch):
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    p = _pool(fills=[_fill(N, "BUY", 1_000, 0.69, NOW - 3000),
                     _fill(M, "BUY", 250, 0.31, NOW - 10, detected_at=NOW - 5)],
              snap={M: 250.0, N: 1_000.0})
    b = _short_book(p, ledger=-100, avg=0.31, ratio=0.10, last_plan={"target": -100, "at": NOW - 30.0})
    v = _Venue(bid=0.30, ask=0.32, held={SLUG: -100})
    _tick(p, v, http=_mkt(250.0, 1_000.0))
    lp = b["last_plan"]
    assert b["target"] == -75 and lp["kind"] == "reduce" and "reduce_unwitnessed" not in lp
    assert lp["reduce_ref"] == {"target": -75, "at": NOW - 5}


def test_e15_the_flip_reopen_with_a_witnessed_crossing_sizes_ratio_times_his_net_at_once(monkeypatch):
    """198 / 122 / 451's shape: he sells his 1,000 and buys 500 No as we
    watch. Tick 1: the flip flattens at his price and names the witness;
    tick 2: the venue reads 0, the episode closes, the witness carried;
    tick 3: the reopen cuts first sight at the reference's clock -- the
    crossing fills are flow, the block 0, the short opens at -50."""
    _shorts_on(monkeypatch)
    p, b = _long_world(monkeypatch, _fill(M, "SELL", 1_000, 0.30, NOW - 10, detected_at=NOW - 8),
                       _fill(N, "BUY", 500, 0.70, NOW - 9, detected_at=NOW - 7), net=-500.0)
    p.snap[N] = 500.0
    p.snap[M] = 0.0
    v1 = _Venue(bid=0.29, ask=0.31, held={SLUG: 100}, ioc_fill=100.0)
    st1 = _tick(p, v1, http=_mkt(0.0, 500.0))
    assert b["last_plan"]["sign_flip"] is True and b["ledger_net"] == 0 and _census(st1, "sign_flip") == 1
    assert b["last_plan"]["flip_witness"] == {"since": NOW - 30.0, "reduced": 1_500.0, "at": NOW}
    v2 = _Venue(bid=0.29, ask=0.31, held={SLUG: 0})
    v2.orders = v1.orders
    _tick(p, v2, now=NOW + 30, http=_mkt(0.0, 500.0))
    assert b["state"] == "closed" and b["last_plan"]["flip_witness"]["since"] == NOW - 30.0
    v3 = _Venue(bid=0.29, ask=0.31, held={SLUG: 0})
    v3.orders = v1.orders
    st3 = _tick(p, v3, now=NOW + 60, http=_mkt(0.0, 500.0))
    b2 = [x for x in p.books.values() if x["id"] != b["id"]][0]
    assert b2["intent"] == SHORT and b2["target"] == -50 and b2["episode"] == 2
    assert b2["last_plan"]["catchup"]["flip_reopen"] == {"since": NOW - 30.0} and b2["flow_base"] == 0.0
    assert b2["last_plan"]["catchup"]["why"] == "no_block" and _census(st3, "open_flow_only") == 0
    assert len(_places(v3)) == 1 and _places(v3)[0][6] == SHORT and _places(v3)[0][3] == 50


def test_e15_the_flip_reopen_on_a_reading_alone_is_flow_only_as_today(monkeypatch):
    """The same crossing but every fill of his is older than the
    reference (no witness): no `flip_witness`; the reopen's window is
    FIRST_SIGHT_S, the whole net is the block, his cost 0.36 on the
    short axis (1,000 sold at 0.30, 500 No at 0.52 = 0.48) six cents
    from the 0.30 mark -- past lane 1's D1 band (max(2c, 10 % of 0.36)
    = 3.6c, landed 2026-09-08; the lane's 0.60 / 3c world sat inside
    it) -- target 0. His newest reducing fill is the
    0.30 SELL, so the flip's flatten takes at the 0.29 bid (E4) and the
    venue reads 0 the next tick."""
    _shorts_on(monkeypatch)
    p, b = _long_world(monkeypatch, _fill(N, "BUY", 500, 0.52, NOW - 1900),
                       _fill(M, "SELL", 1_000, 0.30, NOW - 1800), net=-500.0)
    p.snap[N], p.snap[M] = 500.0, 0.0
    v1 = _Venue(bid=0.29, ask=0.31, held={SLUG: 100}, ioc_fill=100.0)
    _tick(p, v1, http=_mkt(0.0, 500.0))
    assert b["last_plan"]["sign_flip"] is True and "flip_witness" not in b["last_plan"]
    assert b["last_plan"]["kind"] == "flatten_paired" and b["ledger_net"] == 0 and _places(v1)[0][3] == 100
    v2 = _Venue(bid=0.29, ask=0.31, held={SLUG: 0})
    v2.orders = v1.orders
    _tick(p, v2, now=NOW + 30, http=_mkt(0.0, 500.0))
    assert b["state"] == "closed"
    v3 = _Venue(bid=0.29, ask=0.31, held={SLUG: 0})
    v3.orders = v1.orders
    st3 = _tick(p, v3, now=NOW + 60, http=_mkt(0.0, 500.0))
    b2 = [x for x in p.books.values() if x["id"] != b["id"]][0]
    assert b2["target"] == 0 and b2["flow_base"] == -500.0 and "flip_reopen" not in b2["last_plan"]["catchup"]
    assert b2["last_plan"]["catchup"]["why"] == "flow_only" and not _places(v3) and _census(st3, "open_flow_only") == 1


def test_e15_drift_explained_by_his_chain_fills_sizes_the_increase_on_the_fills_net(monkeypatch):
    """The walk (40 s old, older than POLL_S) reads 1,000; his chain
    fill of 200 landed after it: drift 16.7 % refused the increase.
    Explained: the increase of 20 goes out at his cent, named."""
    add = _fill(M, "BUY", 200, 0.31, NOW - 20, detected_at=NOW - 15, source="chain")
    p, b = _long_world(monkeypatch, add, net=1_000.0)
    v = _Venue(bid=0.31, ask=0.33, held={SLUG: 100})
    st = _tick(p, v, http=WALK)
    lp = b["last_plan"]
    ex = lp["drift_fills_explain"]
    assert b["target"] == 120 and (ex["fills_after"], ex["delta"]) == (200.0, 200.0)
    # the walk's OWN clock: the wall clock at the read less the age
    # ms.snapshot_sizes measured on the same wall clock -- never t.now
    # less the age (the fake's walk is stamped NOW - 40 on the wall)
    assert abs(ex["snapshot_at"] - (NOW - 40)) < 0.5, "the walk's own clock"
    assert len(_places(v)) == 1 and _places(v)[0][3] == 20 and _places(v)[0][4] is False
    assert _census(st, "drift") == 0 and lp["drift"] is not None


def test_e15_drift_explained_by_poll_rows_only_or_unexplained_refuses_as_today(monkeypatch):
    """A ledger of 80 under the smaller reading's 100 wants an increase
    of 20 -- refused by name `drift` (the census counts a refused
    increase) exactly as today, nothing placed, nothing explained."""
    poll = _fill(M, "BUY", 200, 0.31, NOW - 20, detected_at=NOW - 15, source="poll")
    p, b = _long_world(monkeypatch, poll, net=1_000.0, ledger=80)
    v = _Venue(bid=0.31, ask=0.33, held={SLUG: 80})
    st = _tick(p, v, http=WALK)
    assert not _places(v) and _census(st, "drift") == 1 and "drift_fills_explain" not in b["last_plan"]
    assert b["target"] == 100 and b["ledger_net"] == 80
    chain = _fill(M, "BUY", 200, 0.31, NOW - 20, detected_at=NOW - 15, source="chain")
    p2, b2 = _long_world(monkeypatch, chain, net=900.0, ledger=80)          # 300 apart, 200 explained
    v2 = _Venue(bid=0.31, ask=0.33, held={SLUG: 80})
    st2 = _tick(p2, v2, http=WALK)
    assert not _places(v2) and _census(st2, "drift") == 1 and "drift_fills_explain" not in b2["last_plan"]
    assert b2["target"] == 90
    # a walk younger than one tick is not this rule's
    p3, b3 = _long_world(monkeypatch, chain, net=1_000.0, ledger=80, snap_at=NOW - 10)
    v3 = _Venue(bid=0.31, ask=0.33, held={SLUG: 80})
    st3 = _tick(p3, v3, http=WALK)
    assert not _places(v3) and _census(st3, "drift") == 1 and "drift_fills_explain" not in b3["last_plan"]


def test_e15_the_fast_tick_plans_the_same_function(monkeypatch):
    p, b = _long_world(monkeypatch, net=800.0)
    _walk({SLUG: 100})
    v = _Venue(bid=0.30, ask=0.32, held={SLUG: 100})
    fs = _fast(p, v, http=WALK)
    assert _skips(fs) == {} and not _places(v) and b["last_reason"] == "reduce_unwitnessed"
    assert "await _tick_book(t, fresh)" in inspect.getsource(ml._fast_book)


def test_e15_his_witnessed_exit_resting_keeps_its_e4_life_under_a_later_unwitnessed_fall(monkeypatch):
    """The reduce of 25 rests at his 0.70 (witnessed, the reference at
    75); the walk then reads 600 (target 60): held -- and the rest of 25
    STANDS, the plan handed over capped at the reference's 75 (E12b's
    construction); with the bid inside the cent it is taken."""
    sale = _fill(M, "SELL", 250, 0.70, NOW - 200, detected_at=NOW - 195)
    p, b = _long_world(monkeypatch, sale, net=600.0, last_plan={"target": 75, "at": NOW - 190.0})
    o = p.add_order(b, side=SELL, wire=0.70, qty=25, kind="reduce", order_id="oid-exit")
    v = _Venue(bid=0.68, ask=0.71, held={SLUG: 100})
    v.rest("oid-exit", side="SELL", price=0.70, qty=25)
    st = _tick(p, v, http=WALK)
    lp = b["last_plan"]
    assert lp["reduce_unwitnessed"] == {"from": 75, "to": 60, "cause": "reading", "cap": 25}
    assert (lp["side"], lp["qty"], lp["open_order"]) == (SELL, 25, o["id"])
    assert not _cancels(v) and not _places(v) and p.orders[o["id"]]["state"] == "open"
    assert _census(st, "exit_out_of_tol") == 1 and b["ledger_net"] == 100
    v2 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    v2.rest("oid-exit", side="SELL", price=0.70, qty=25)
    st2 = _tick(p, v2, now=NOW + 30, http=WALK)
    assert _census(st2, "exit_take") == 1 and b["ledger_net"] == 75


def test_e15_a_resting_add_is_cancelled_under_the_hold_and_a_flattens_reference_caps_nothing(monkeypatch):
    p, b = _long_world(monkeypatch, net=800.0, last_plan={"target": 120, "at": NOW - 30.0})
    o = p.add_order(b, side=BUY, wire=0.31, qty=20, kind="increase", order_id="oid-add")
    v = _Venue(bid=0.30, ask=0.32, held={SLUG: 100})
    v.rest("oid-add", side="BUY", price=0.31, qty=20)
    _tick(p, v, http=WALK)
    assert [c[:2] for c in _cancels(v)] == [("cancel", "oid-add")] and p.orders[o["id"]]["state"] == "cancelled"
    assert b["last_plan"]["reduce_unwitnessed"] == {"from": 120, "to": 80, "cause": "reading"} and not _places(v)
    # a reference of 0 (a flatten's) never re-sends a flatten as the standing exit
    p2, b2 = _long_world(monkeypatch, net=800.0, last_plan={"target": 0, "at": NOW - 30.0})
    v2 = _Venue(bid=0.30, ask=0.32, held={SLUG: 100})
    _tick(p2, v2, http=WALK)
    assert not _places(v2) and b2["last_plan"]["reduce_unwitnessed"] == {"from": 0, "to": 80, "cause": "reading"}


def test_e15_a_reducing_fill_clocked_before_the_reference_is_no_witness_and_one_after_is(monkeypatch):
    before = _fill(M, "SELL", 250, 0.70, NOW - 100, detected_at=NOW - 60)
    p, b = _long_world(monkeypatch, before, net=750.0)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    _tick(p, v, http=_mkt(750.0))
    assert not _places(v) and b["last_plan"]["reduce_unwitnessed"]["cause"] == "fills"
    after = _fill(M, "SELL", 250, 0.70, NOW - 100, detected_at=NOW - 20)
    p2, b2 = _long_world(monkeypatch, after, net=750.0)
    v2 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    _tick(p2, v2, http=_mkt(750.0))
    assert _places(v2)[0][2:6] == (0.69, 25, True, IOC) and b2["ledger_net"] == 75


def test_e15_a_plan_with_no_readable_clock_holds_no_reference_and_seeds_the_ledger(monkeypatch):
    p, b = _long_world(monkeypatch, net=800.0, last_plan={"kind": "no_plan"})
    v = _Venue(bid=0.30, ask=0.32, held={SLUG: 100})
    _tick(p, v, http=WALK)
    lp = b["last_plan"]
    assert not _places(v) and lp["reduce_unwitnessed"] == {"from": None, "to": 80, "cause": "no_reference"}
    assert lp["reduce_ref"] == {"target": 100, "at": NOW - 3000}, "seeded at the ledger and the fills' clock"
    # his sale after the seed witnesses the next tick
    p.fills.append(_fill(M, "SELL", 250, 0.70, NOW + 10, detected_at=NOW + 12))
    p.snap[M] = 750.0
    v2 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    _tick(p, v2, now=NOW + 30, http=_mkt(750.0))
    assert _places(v2)[0][2:6] == (0.69, 25, True, IOC) and b["ledger_net"] == 75


def test_e15_a_never_planned_book_plans_as_today_and_its_first_plan_writes_the_reference(monkeypatch):
    p, b = _long_world(monkeypatch, net=1_000.0, last_plan=None)
    b["last_plan"] = None
    v = _Venue(bid=0.30, ask=0.32, held={SLUG: 100})
    _tick(p, v, http=_mkt(1_000.0))
    assert b["last_plan"]["reduce_ref"] == {"target": 100, "at": NOW - 3000} and "reduce_unwitnessed" not in b["last_plan"]


def test_e15_a_flow_book_reads_its_own_reference_and_the_e12b_witness_agrees(monkeypatch):
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000),
                     _fill(M, "SELL", 2_750, 0.70, NOW - 100, detected_at=NOW - 95)], snap={M: 8_250.0, N: 0.0})
    b = _flow_book(p)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    _tick(p, v, http=_mkt(8_250.0))
    assert b["target"] == 75 and b["ledger_net"] == 75 and b["last_plan"]["reduce_ref"] == {"target": 75, "at": NOW - 95}
    assert "reduce_unwitnessed" not in b["last_plan"]


def test_e15_the_docs_no_new_knob_and_no_census_name():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E15 \(2026-09-08\)", doc, re.M) and "reduce_unwitnessed" in doc and "flip_burst_net" in doc
    assert "drift_fills_explain" in doc and "flip_witness" in doc
    src = inspect.getsource(ml)
    assert "reduce_unwitnessed" not in ml.CENSUS_KEYS and "capped_env(\"MIRROR_E15" not in src
    assert mi.REDUCE_UNWITNESSED == "reduce_unwitnessed" and mi.FLIP_BURST_NET == "flip_burst_net"
    assert "since: float | None = None" in inspect.getsource(ml._open_flow)
    assert "ml-book-flip" in ml._SQL_BOOK_FLIP and "state = 'closed'" in ml._SQL_BOOK_FLIP
