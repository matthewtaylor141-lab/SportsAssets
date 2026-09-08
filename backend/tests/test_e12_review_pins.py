"""E12 review pins (2026-09-08): the mirror follows his FLOW from first
sight (decision 13 (A), Rule LE / D25's pro-rata ratchet). Driven on the
real planner through the worker file's fakes, exactly as the builder's
tests are. Each pin names the review question it answers.

THE FOLD (2026-09-08, the same day). The review's two strict xfails --
HIGH-1 (a backfilled OLD fill read as flow and bought at his newest
cent) and HIGH-2 (the ratchet run on a reading wobble, converting block
into flow; one zero reading writing block 0) -- are un-marked and pass
as written; the three FINDING pins that documented the defects now pin
the fix by name; the fold's own pins follow at the end (the late-row
bound, the reading named on the plan, the sale his fill witnesses, the
full exit, the reference at open, the fast tick, the docs).
"""
import inspect
import pathlib

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.ingestion import shadow_v2
from sportsassets.workers import mirror_live as ml
from tests.test_e12_flow_only import _flow_book, _one_book, _sent
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    CID, M, N, NOW, SLUG, _armed, _census, _fill, _gone, _mkt, _places, _pool, _rails_2026_09_06,
    _tick, _Venue,
)

GTC = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
IOC = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
ROOT = pathlib.Path(__file__).resolve().parents[2]


def _world(monkeypatch, fills, net, bid, ask):
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=list(fills), snap={M: float(net), N: 0.0})
    return p, _Venue(bid=bid, ask=ask), _mkt(float(net))


# --------------------------------------------------------- Q1: the open

def test_q1_the_briefs_numbers_20000_at_29_plus_1000_at_71_first_tick_100_second_tick_50(monkeypatch):
    """He holds 20,000 at 0.29 and adds 1,000 at 0.71 as we look, ratio
    10%, mark 0.72: the first tick places 100 at 0.71 (never 2,100); his
    next 500 at 0.72 moves the flow to 1,500 and the second tick rests
    50 more at 0.72 -- the block of 20,000 is never bought."""
    fills = [_fill(M, "BUY", 20_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 10)]
    p, v, http = _world(monkeypatch, fills, 21_000, 0.71, 0.73)
    st = _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (20_000.0, 21_000.0, 100)
    assert _places(v)[0][1:5] == (SLUG, 0.71, 100, False) and _census(st, "open_flow_only") == 1
    # the second tick: the 100 filled and held, his next 500 at 0.72 ingested now
    _rails_2026_09_06(monkeypatch)
    p2 = _pool(fills=fills + [_fill(M, "BUY", 500, 0.72, NOW - 5, detected_at=NOW - 5)],
               snap={M: 21_500.0, N: 0.0})
    b2 = _flow_book(p2, ledger=100, flow_base=20_000.0, flow_last_net=21_000.0)
    v2 = _Venue(bid=0.72, ask=0.74, held={SLUG: 100})
    _tick(p2, v2, http=_mkt(21_500.0))
    assert (b2["flow_base"], b2["flow_last_net"], b2["target"]) == (20_000.0, 21_500.0, 150)
    assert _places(v2)[0][1:5] == (SLUG, 0.72, 50, False) and "flow_ratchet" not in b2["last_plan"]


def test_q1_addendum_a_a_market_mapped_three_minutes_after_his_first_fills_is_all_block(monkeypatch):
    """Every fill of his is 3 minutes old at first sight (the map lagged
    his first fill by 3 min): the whole net is the block. With the mark
    6c off his cost the book opens at 0 and waits; within 2c it catches
    up in full at his cent -- the same answer a book-open-time cut
    would give, since nothing of his is inside 60 s.

    Re-pinned under PNL lane 1 part (b) (owner decision D1 = YES,
    2026-09-08): this pin's first world had the mark 3c over his 0.61
    cost, which E12's flat 2c refused; the worker hands the book's axis
    and the worse side's band on a 0.61 cost is min(5c, max(2c, 10% x
    0.61)) = 5c, so 3c over is now `within_pct` (test_e12_catchup_side
    pins it). 6c over is past the 5c cap on any cost and keeps the
    flow-only half of this pin; the 1c world is unchanged."""
    fills = [_fill(M, "BUY", 5_000, 0.60, NOW - 180), _fill(M, "BUY", 5_000, 0.62, NOW - 170)]
    p, v, http = _world(monkeypatch, fills, 10_000, 0.66, 0.68)      # mark 0.67, vwap 0.61: 6c over
    st = _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["flow_base"], b["target"]) == (10_000.0, 0) and not _places(v)
    assert b["last_plan"]["catchup"]["why"] == "flow_only" and b["last_plan"]["flow_wait"] is True
    assert _census(st, "open_flow_only") == 1
    p2, v2, http2 = _world(monkeypatch, fills, 10_000, 0.61, 0.63)   # mark 0.62: 1c over
    st2 = _tick(p2, v2, http=http2)
    b2 = _one_book(p2)
    assert (b2["flow_base"], b2["target"]) == (0.0, 1_000) and _places(v2)[0][1:5] == (SLUG, 0.61, 1_000, False)
    assert _census(st2, "open_catchup") == 1 and b2["last_plan"]["catchup"]["why"] == "within_tol"


def test_q1_addendum_b_a_poll_fill_whose_ingest_lags_its_stamp_by_two_minutes_is_flow(monkeypatch):
    """The fill is 130 s old by its stamp and 10 s old by its ingest: the
    ingest clock reads it as the flow that woke the market, and it is
    mirrored at his cent for ratio x its size."""
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000),
             _fill(M, "BUY", 1_000, 0.71, NOW - 130, detected_at=NOW - 10)]
    p, v, http = _world(monkeypatch, fills, 11_000, 0.71, 0.73)
    _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["flow_base"], b["target"]) == (10_000.0, 100) and _places(v)[0][1:5] == (SLUG, 0.71, 100, False)


def test_q1_addendum_b2_a_backfilled_two_hour_old_fill_ingested_now_is_the_block_and_its_cost_enters_the_vwap(monkeypatch):
    """WAS THE FINDING (HIGH-1): history.py writes `detected_at` = the
    backfill time and the S1 emitter's rows are stamped at
    reconciliation, so a fill he made two hours ago that landed in
    `trades` now sat inside FIRST_SIGHT_S by its ingest clock, was read
    as FLOW and bought at his NEWEST cent (block 10,000, target 500: the
    4,000 bought). THE FOLD: a fill is flow only when its own stamp is
    also inside mi.LATE_FILL_S of the window's start (mi.is_flow) -- the
    4,000 is the block (14,000), its 0.33 enters his cost over it, and
    the book opens on the 1,000 he added as we looked, at his cent."""
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000),
             _fill(M, "BUY", 4_000, 0.33, NOW - 7200, detected_at=NOW - 10),   # backfilled now
             _fill(M, "BUY", 1_000, 0.71, NOW - 10, detected_at=NOW - 10)]
    p, v, http = _world(monkeypatch, fills, 15_000, 0.71, 0.73)
    st = _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (14_000.0, 15_000.0, 100)
    assert _places(v)[0][1:5] == (SLUG, 0.71, 100, False) and _census(st, "open_flow_only") == 1
    cu = b["last_plan"]["catchup"]
    assert cu["vwap"] == round((10_000 * 0.29 + 4_000 * 0.33) / 14_000, 6) and cu["why"] == "flow_only"
    since = NOW - ml.FIRST_SIGHT_S
    assert mi.is_flow(fills[2], since) and not mi.is_flow(fills[1], since) and not mi.is_flow(fills[0], since)
    assert mi.vwap_of(fills, M, N, before=since) == cu["vwap"]


def test_q1_addendum_b2_fix_a_fill_stamped_hours_before_the_window_stays_the_block(monkeypatch):
    """The review's strict xfail, un-marked by the fold: passes as written."""
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000),
             _fill(M, "BUY", 4_000, 0.33, NOW - 7200, detected_at=NOW - 10),
             _fill(M, "BUY", 1_000, 0.71, NOW - 10, detected_at=NOW - 10)]
    p, v, http = _world(monkeypatch, fills, 15_000, 0.71, 0.73)
    _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["flow_base"], b["target"]) == (14_000.0, 100)
    assert _places(v)[0][1:5] == (SLUG, 0.71, 100, False)


def test_q1_addendum_c_he_adds_every_30s_for_ten_minutes_before_we_open_two_adds_are_flow(monkeypatch):
    """Twenty adds of 500 at 30 s spacing (the last 30 s ago): the two
    inside FIRST_SIGHT_S (60 s) are the flow, the other eighteen the
    block. Open target 100 at his last cent; his earlier 9,000 never."""
    fills = [_fill(M, "BUY", 500, 0.50 + k * 0.001, NOW - 30 * (20 - k)) for k in range(20)]
    net = 10_000.0
    # the mark 0.56 against his 0.5085 over the block: 5c, outside the
    # allowance (at 0.52 the same market catches up whole -- 1.15c)
    p, v, http = _world(monkeypatch, fills, net, 0.55, 0.57)
    _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (9_000.0, net, 100)
    assert _places(v)[0][3] == 100 and b["last_plan"]["catchup"]["why"] == "flow_only"
    # the arithmetic behind it, pure
    since = NOW - ml.FIRST_SIGHT_S
    assert mi.pre_existing_block(net, fills, M, N, since) == 9_000.0
    assert mi.vwap_of(fills, M, N, before=since) == round(sum(0.5 + k * 0.001 for k in range(18)) / 18, 6)


# ------------------------------------------------------- Q2: the ratchet

def test_q2_the_briefs_steps_in_d25s_words_20000_block_plus_1000_flow():
    """Block 20,000, flow 1,000, we hold 100. He sells 5,000 (to 16,000):
    block 15,238.1, flow 761.9, target 76 -- our 24 sold, his 23.8%. He
    sells to 100: block 95.2, flow 4.8, target 0 -- all sold. He sells
    all: 0. He re-buys 3,000 after the first sale (to 19,000): the block
    stays 15,238.1 (D25: unchanged on increases), flow 3,761.9, target
    376 -- the re-buy is flow he put on as we watched."""
    r = mi.pre_existing_ratchet
    b1 = r(20_000.0, 21_000.0, 16_000.0)
    assert b1 == round(20_000 * 16 / 21, 6) == 15_238.095238
    assert rules.mirror_target(0.10, mi.flow_net(16_000.0, b1), 0.72, 2500.0, cap_usd=2500.0)["target"] == 76
    b2 = r(20_000.0, 21_000.0, 100.0)
    assert b2 == round(20_000 * 100 / 21_000, 6) and mi.flow_net(100.0, b2) == round(100 - b2, 6)
    assert rules.mirror_target(0.10, mi.flow_net(100.0, b2), 0.72, 2500.0, cap_usd=2500.0)["target"] == 0
    assert r(20_000.0, 21_000.0, 0.0) == 0.0 and mi.flow_net(0.0, 0.0) == 0.0
    b3 = r(b1, 16_000.0, 19_000.0)
    assert b3 == b1 and mi.flow_net(19_000.0, b3) == round(19_000 - b1, 6)
    assert rules.mirror_target(0.10, mi.flow_net(19_000.0, b3), 0.72, 2500.0, cap_usd=2500.0)["target"] == 376


def test_q2_his_sale_to_100_of_21000_is_our_full_exit_at_his_price(monkeypatch):
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 20_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000),
             _fill(M, "SELL", 20_900, 0.70, NOW - 100)]
    p = _pool(fills=fills, snap={M: 100.0, N: 0.0})
    b = _flow_book(p, ledger=100, flow_base=20_000.0, flow_last_net=21_000.0)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=100.0)
    st = _tick(p, v, http=_mkt(100.0))
    assert b["target"] == 0 and round(b["flow_base"], 3) == round(20_000 * 100 / 21_000, 3)
    assert _places(v)[0][2:6] == (0.69, 100, True, IOC) and _census(st, "exit_take") == 1
    assert b["last_plan"]["exit_px"] == 0.70 and b["ledger_net"] == 0


def test_q2_the_fast_tick_on_an_existing_flow_book_sizes_on_the_flow_not_his_whole_net(monkeypatch):
    """The wake on a book with a block (mutant: _fast_book reading the
    056 row shape): his add of 2,000 at 0.75 is answered with 200, never
    the 1,200 his whole net would size."""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000),
             _fill(M, "BUY", 2_000, 0.75, NOW - 5, detected_at=NOW - 5)]
    p = _pool(fills=fills, snap={M: 13_000.0, N: 0.0})
    b = _flow_book(p)
    v = _Venue(bid=0.75, ask=0.77, held={SLUG: 100})
    _walk({SLUG: 100})                  # the last full tick's walk read our 100
    fs = _fast(p, v, http=_mkt(13_000.0))
    assert _skips(fs) == {} and (b["flow_base"], b["target"]) == (10_000.0, 300)
    assert _places(v)[0][1:5] == (SLUG, 0.75, 200, False) and _census(fs, "fast_tick_placed") == 1


def test_q2_a_reading_wobble_with_no_fill_of_his_is_named_on_the_plan_and_moves_nothing(monkeypatch):
    """WAS THE FINDING (HIGH-2): D25's ratchet ran on the tick's
    two-source net (_net_for), so the venue reading 10,900 for one tick
    (0.9% under the fills, inside MIRROR_DRIFT_MAX) ratcheted the block
    to 9,909 and the next read at 11,000 BOUGHT 9 shares of the block
    at his cent with no fill of his, compounding per wobble. THE FOLD:
    the ratchet moves only on a fall his FILLS witness; the reading is
    compared with the fills' arithmetic and named `flow_reading_disagree`
    on the plan -- no ratchet, no write, the book planned on the fills'
    net (target 100, held), nothing placed on either tick."""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 100)]
    p = _pool(fills=fills, snap={M: 11_000.0, N: 0.0})
    b = _flow_book(p)
    v1 = _Venue(bid=0.71, ask=0.73, held={SLUG: 100})
    st = _tick(p, v1, http=_mkt(10_900.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (10_000.0, 11_000.0, 100)
    lp = b["last_plan"]
    assert lp["flow_reading"] == {"why": "flow_reading_disagree", "reading": 10_900.0, "fills": 11_000.0}
    assert "flow_ratchet" not in lp and lp["flow_net"] == 1_000.0 and lp["net"] == 10_900.0
    assert not _places(v1) and not _places_of(p) and not _sent(p, "ml-book-flow")
    assert _census(st, "on_target") == 1
    v2 = _Venue(bid=0.71, ask=0.73, held={SLUG: 100})
    _tick(p, v2, now=NOW + 30, http=_mkt(11_000.0))
    assert (b["flow_base"], b["target"]) == (10_000.0, 100) and not _places(v2)
    assert "flow_reading" not in b["last_plan"] and not _sent(p, "ml-book-flow")


def _places_of(p):
    return [s for k, s, a in p.sent if "INSERT INTO mirror_orders" in s]


def test_q2_one_zero_reading_of_his_net_writes_nothing_sells_nothing_and_the_next_tick_buys_nothing(monkeypatch):
    """WAS THE FINDING (HIGH-2, the worst case): the per-market read
    answered 0/0 for one tick (D1: he merged a pair on-chain; drift 1.0,
    so _net_for sized on the SMALLER reading, 0); the ratchet read a
    crossing and wrote block 0 to the row, the zero read's own flatten
    sold our 100, and the next read at 11,000 bought 1,100 at his newest
    cent -- his whole block. THE FOLD: a zero reading with no reducing
    fill of his writes NOTHING -- the block stands, the plan names
    `flow_reading_zero`, the book is planned on the fills' net (target
    100, held: no flatten, so no re-buy), and the next tick at 11,000
    buys nothing."""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 100)]
    p = _pool(fills=fills, snap={M: 11_000.0, N: 0.0})
    b = _flow_book(p)
    v1 = _Venue(bid=0.71, ask=0.73, held={SLUG: 100})
    st = _tick(p, v1, http=_mkt(0.0, 0.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (10_000.0, 11_000.0, 100)
    lp = b["last_plan"]
    assert lp["flow_reading"] == {"why": "flow_reading_zero", "reading": 0.0, "fills": 11_000.0}
    assert "flow_ratchet" not in lp and lp["net"] == 0.0 and lp["flow_net"] == 1_000.0
    assert not _places(v1) and not _sent(p, "ml-book-flow") and b["ledger_net"] == 100
    assert _census(st, "sign_flip") == 0 and _census(st, "exit_take") == 0 and b["state"] == "live"
    v2 = _Venue(bid=0.71, ask=0.73, held={SLUG: 100})
    _tick(p, v2, now=NOW + 30, http=_mkt(11_000.0))
    assert (b["flow_base"], b["target"]) == (10_000.0, 100) and not _places(v2)
    assert "flow_reading" not in b["last_plan"] and not _sent(p, "ml-book-flow")


def test_q2_fix_a_reading_wobble_with_no_fill_of_his_leaves_the_block_where_it_is(monkeypatch):
    """The review's strict xfail, un-marked by the fold: passes as written."""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 100)]
    p = _pool(fills=fills, snap={M: 11_000.0, N: 0.0})
    b = _flow_book(p)
    _tick(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 100}), http=_mkt(10_900.0))
    v2 = _Venue(bid=0.71, ask=0.73, held={SLUG: 100})
    _tick(p, v2, now=NOW + 30, http=_mkt(11_000.0))
    assert b["flow_base"] == 10_000.0 and b["target"] == 100 and not _places(v2)


def test_q2_the_sign_flip_reopen_reads_his_crossing_fills_as_the_block_after_the_close():
    """The flip is the close and the other side opens as a new episode
    at least two ticks later (the flatten, the venue read at 0, the
    candidate): his crossing fills are then past FIRST_SIGHT_S and the
    reopen is flow-only at 0 unless the mark is within 2c of his cost
    on the new side (the short axis reads his other-token BUY at 1 - p)."""
    since = NOW - ml.FIRST_SIGHT_S
    fills = [_fill(M, "SELL", 11_000, 0.70, NOW - 100), _fill(N, "BUY", 500, 0.30, NOW - 90)]
    assert mi.pre_existing_block(-500.0, fills, M, N, since) == -500.0
    vwap = mi.vwap_of(fills, M, N, short=True, before=since)
    assert vwap == 0.70
    assert rules.open_catchup(-500.0, 0.71, 0.10, -500.0, vwap)["why"] == "within_tol"
    assert rules.open_catchup(-500.0, 0.74, 0.10, -500.0, vwap)["why"] == "flow_only"


# ---------------------------------------------------- Q3 / Q5: the row, env

def test_q3_a_restart_between_the_open_and_his_first_fill_reads_the_block_from_the_row(monkeypatch):
    """A fresh process (no memory of the open) ticks a row carrying the
    block: flat at target 0, flow_wait, nothing bought."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 11_000, 0.29, NOW - 9000)], snap={M: 11_000.0, N: 0.0})
    b = _flow_book(p, ledger=0, flow_base=11_000.0, flow_last_net=11_000.0, avg_cost=None)
    v = _Venue(bid=0.71, ask=0.73, held={SLUG: 0})
    _tick(p, v, http=_mkt(11_000.0))
    assert b["target"] == 0 and not _places(v) and b["last_plan"]["flow_wait"] is True
    assert not _sent(p, "ml-book-flow"), "nothing moved: nothing written"


def test_q5_the_tolerance_is_read_at_call_time_and_a_raised_env_never_widens_it(monkeypatch):
    monkeypatch.setenv("MIRROR_CATCHUP_TOL_CENTS", "9")
    assert rules.capped_env("MIRROR_CATCHUP_TOL_CENTS", 2.0, floor=0.0) == 2.0
    assert rules.open_catchup(11_000.0, 0.34, 0.10, 10_000.0, 0.29)["why"] == "flow_only"
    monkeypatch.setenv("MIRROR_CATCHUP_TOL_CENTS", "0.5")
    assert rules.capped_env("MIRROR_CATCHUP_TOL_CENTS", 2.0, floor=0.0) == 0.5


# ------------------------------------------------------------------- the fold
#
# (2026-09-08; the review's HIGH-1 / HIGH-2 closed, the mediums said.)

def test_fold_h1_the_late_row_bound_is_the_poll_lanes_own_and_both_clocks_decide(monkeypatch):
    """A poll row whose ingest trails its stamp by two minutes is flow; a
    chain row stamped twenty minutes ago and ingested now is the block.
    The bound is ingestion/shadow_v2.LATE_POLL_ROW_S (900 s, the poll's
    own late-row allowance), reused, read at the window's start."""
    assert mi.LATE_FILL_S == shadow_v2.LATE_POLL_ROW_S == 900.0
    assert "from ..ingestion.shadow_v2 import LATE_POLL_ROW_S" in inspect.getsource(mi)
    since = NOW - ml.FIRST_SIGHT_S
    poll = _fill(M, "BUY", 1_000, 0.71, NOW - 125, detected_at=NOW - 5, source="poll")
    chain = _fill(M, "BUY", 4_000, 0.33, NOW - 1200, detected_at=NOW - 5, source="chain")
    assert mi.is_flow(poll, since) and not mi.is_flow(chain, since)
    # the bound exactly: a stamp 900 s before the window's start is flow, one second older the block
    assert mi.is_flow(_fill(M, "BUY", 1, 0.5, since - 900.0, detected_at=NOW - 5), since)
    assert not mi.is_flow(_fill(M, "BUY", 1, 0.5, since - 901.0, detected_at=NOW - 5), since)
    # the ingest clock outside the window is the block whatever the stamp;
    # an unstamped fill is the block; `since` None is old; junk is old
    assert not mi.is_flow(_fill(M, "BUY", 1, 0.5, NOW - 10, detected_at=NOW - 61), since)
    assert not mi.is_flow({"asset": M, "side": "BUY", "size": 1, "price": 0.5, "detected_at": NOW - 5}, since)
    assert not mi.is_flow(poll, None) and not mi.is_flow("x", since) and not mi.is_flow(None, since)
    # an unreadable allowance admits nothing past the window's start
    assert not mi.is_flow(poll, since, late_s=None) and mi.is_flow(_fill(M, "BUY", 1, 0.5, NOW - 5), since, late_s="x")
    # the planner: the poll row opens the book on 100 at his cent, the block 10,000
    block = _fill(M, "BUY", 10_000, 0.29, NOW - 9000)
    p, v, http = _world(monkeypatch, [block, poll], 11_000, 0.71, 0.73)
    _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["flow_base"], b["target"]) == (10_000.0, 100) and _places(v)[0][1:5] == (SLUG, 0.71, 100, False)
    # the chain row ingested now is the block; his add as we looked is the flow; the row's price is his cost
    add = _fill(M, "BUY", 1_000, 0.71, NOW - 10, detected_at=NOW - 10)
    p2, v2, http2 = _world(monkeypatch, [block, chain, add], 15_000, 0.71, 0.73)
    _tick(p2, v2, http=http2)
    b2 = _one_book(p2)
    assert (b2["flow_base"], b2["target"]) == (14_000.0, 100) and _places(v2)[0][1:5] == (SLUG, 0.71, 100, False)
    assert b2["last_plan"]["catchup"]["vwap"] == round((10_000 * 0.29 + 4_000 * 0.33) / 14_000, 6)
    # the chain row alone, with nothing of his inside the window: all block, the book waits
    p3, v3, http3 = _world(monkeypatch, [block, chain], 14_000, 0.71, 0.73)
    _tick(p3, v3, http=http3)
    b3 = _one_book(p3)
    assert (b3["flow_base"], b3["target"]) == (14_000.0, 0) and not _places(v3) and b3["last_plan"]["flow_wait"] is True


def test_fold_h2_a_reading_past_the_fills_buys_nothing_and_the_dust_is_one_share(monkeypatch):
    """The venue reads 11,400 (3.6% OVER the fills, inside
    MIRROR_DRIFT_MAX: the old net sized 1,140 and BOUGHT 40 his fills
    never made): planned on the fills' net, target 100, the reading
    named, nothing placed, nothing written. Within one share (D1's "to
    the share", mi.VENUE_LEDGER_TOL_SHARES) the reading agrees and
    nothing is named."""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 100)]
    p = _pool(fills=fills, snap={M: 11_000.0, N: 0.0})
    b = _flow_book(p)
    v = _Venue(bid=0.71, ask=0.73, held={SLUG: 100})
    _tick(p, v, http=_mkt(11_400.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (10_000.0, 11_000.0, 100)
    assert b["last_plan"]["flow_reading"] == {"why": "flow_reading_disagree", "reading": 11_400.0, "fills": 11_000.0}
    assert not _places(v) and not _sent(p, "ml-book-flow")
    p2 = _pool(fills=fills, snap={M: 11_000.0, N: 0.0})
    b2 = _flow_book(p2)
    _tick(p2, _Venue(bid=0.71, ask=0.73, held={SLUG: 100}), http=_mkt(11_000.5))
    assert "flow_reading" not in b2["last_plan"] and b2["target"] == 100 and b2["last_plan"]["net"] == 11_000.5
    # the pure verdict
    assert mi.flow_reading(11_000.5, 11_000.0) is None and mi.flow_reading(11_001.5, 11_000.0) == "flow_reading_disagree"
    assert mi.flow_reading(0.0, 11_000.0) == "flow_reading_zero" and mi.flow_reading(0.0, 0.0) is None
    assert mi.flow_reading(0.0, -500.0) == "flow_reading_zero" and mi.flow_reading(-400.0, -500.0) == "flow_reading_disagree"
    assert mi.flow_reading(None, 11_000.0) == "flow_reading_disagree" == mi.flow_reading(1.0, "x")
    assert mi.flow_reading(11_002.0, 11_000.0, dust=2.0) is None and mi.flow_reading(11_002.0, 11_000.0, dust=None) == "flow_reading_disagree"
    assert mi.FLOW_READING_ZERO == "flow_reading_zero" and mi.FLOW_READING_DISAGREE == "flow_reading_disagree"
    assert mi.VENUE_LEDGER_TOL_SHARES == 1.0


def test_fold_h2_the_fast_tick_plans_the_same_function_a_wobble_on_the_wake_buys_nothing(monkeypatch):
    """The wake on a book with a block, the venue reading 10,900 for the
    fast tick: the same _tick_book, the same verdict -- nothing placed,
    nothing written, the reading named."""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000)]
    p = _pool(fills=fills, snap={M: 11_000.0, N: 0.0})
    b = _flow_book(p)
    v = _Venue(bid=0.71, ask=0.73, held={SLUG: 100})
    _walk({SLUG: 100})
    fs = _fast(p, v, http=_mkt(10_900.0))
    assert _skips(fs) == {} and (b["flow_base"], b["flow_last_net"], b["target"]) == (10_000.0, 11_000.0, 100)
    assert b["last_plan"]["flow_reading"]["why"] == "flow_reading_disagree" and not _places(v)
    assert not _sent(p, "ml-book-flow") and _census(fs, "fast_tick_placed") == 0
    assert "async def _fast_book" in inspect.getsource(ml) and "await _tick_book(t, fresh)" in inspect.getsource(ml._fast_book)


def test_fold_h2_his_25_percent_sale_witnessed_by_his_fill_ratchets_25_percent_at_his_price_whatever_the_reading(monkeypatch):
    """His SELL of 2,750 at 0.70 is in the fills: block 7,500 (10,000 x
    8,250 / 11,000), flow 750, target 75, our 25 sold IOC at the 0.69 bid
    within MIRROR_EXIT_TOL of his 0.70 -- with the venue agreeing
    (8,250), lagging at the old 11,000 (the smaller reading is the
    fills'), and reading ZERO: a reducing fill of his IS in, so the
    ratchet moves by the fills' arithmetic, the zero reading is named,
    and the reduce is the same."""
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000),
             _fill(M, "SELL", 2_750, 0.70, NOW - 100)]
    assert abs(0.69 - 0.70) <= float(rules.MIRROR_EXIT_TOL) + 1e-9
    for reading, named in ((8_250.0, None), (11_000.0, None), (0.0, "flow_reading_zero")):
        _rails_2026_09_06(monkeypatch)
        p = _pool(fills=fills, snap={M: 8_250.0, N: 0.0})
        b = _flow_book(p)
        v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
        st = _tick(p, v, http=_mkt(reading))
        assert (b["flow_base"], b["flow_last_net"], b["target"]) == (7_500.0, 8_250.0, 75), reading
        assert _places(v)[0][2:6] == (0.69, 25, True, IOC) and _census(st, "exit_take") == 1, reading
        lp = b["last_plan"]
        assert lp["flow_ratchet"] == {"from": 10_000.0, "to": 7_500.0} and lp["exit_px"] == 0.70, reading
        assert lp.get("flow_reading", {}).get("why") == named and lp["kind"] == "reduce", reading
        assert _sent(p, "ml-book-flow")[0][1][:3] == (b["id"], 7_500.0, 8_250.0) and b["ledger_net"] == 75, reading


def test_fold_h2_his_full_exit_witnessed_by_his_fill_is_our_full_exit(monkeypatch):
    """His SELL of 11,000 in the fills and the venue reading him gone:
    block 0 (a crossing his fills witness), the flatten at once at his
    price, ledger 0, nothing named."""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000),
             _fill(M, "SELL", 11_000, 0.70, NOW - 100)]
    p = _pool(fills=fills, snap={M: 0.0, N: 0.0})
    b = _flow_book(p)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=100.0)
    st = _tick(p, v, http=_gone())
    assert (b["flow_base"], b["flow_last_net"], b["target"], b["ledger_net"]) == (0.0, 0.0, 0, 0)
    lp = b["last_plan"]
    assert lp["kind"] == "flatten_vanished" and lp["flow_ratchet"] == {"from": 10_000.0, "to": 0.0}
    assert "flow_reading" not in lp and lp["exit_px_src"] == "his_fill" and lp.get("flow_wait") is None
    assert _places(v)[0][2:6] == (0.69, 100, True, IOC) and _census(st, "exit_take") == 1


def test_fold_h2_the_reference_at_open_is_his_fills_net_not_the_reading(monkeypatch):
    """At open the venue reads 10,900 against fills of 11,000 (0.9%,
    inside MIRROR_DRIFT_MAX: admission's whole-net figure is 1,090 as
    before); the row stores the block 10,000 and the reference 11,000 --
    the fills' -- and the book opens on the 1,000 he added, 100 at his
    cent, the reading named on the first plan. The next tick at 11,000:
    nothing moves, nothing is written, nothing more is bought. (Under a
    reference of 10,900 the fills' 11,000 would have read as an increase,
    and a later reading back at 10,900 as a fall.)"""
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 10)]
    p, v, _http = _world(monkeypatch, fills, 11_000, 0.71, 0.73)
    st = _tick(p, v, http=_mkt(10_900.0))
    b = _one_book(p)
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (10_000.0, 11_000.0, 100)
    assert _places(v)[0][1:5] == (SLUG, 0.71, 100, False) and _census(st, "open_flow_only") == 1
    assert _sent(p, "INSERT INTO mirror_books")[0][1][12:14] == (10_000.0, 11_000.0)
    lp = b["last_plan"]
    assert lp["flow_reading"]["why"] == "flow_reading_disagree" and lp["net"] == 10_900.0 and lp["flow_net"] == 1_000.0
    v2 = _Venue(bid=0.71, ask=0.73, held={SLUG: 0})
    v2.orders = v.orders
    _tick(p, v2, now=NOW + 30, http=_mkt(11_000.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (10_000.0, 11_000.0, 100)
    assert not _places(v2) and not _sent(p, "ml-book-flow") and "flow_reading" not in b["last_plan"]


def test_fold_the_ratchet_reads_his_fills_net_and_the_cut_is_one_predicate():
    src = inspect.getsource(ml._tick_book)
    assert "fills_net = mi.his_net(r.his_long, r.his_other)" in src
    assert 'mi.pre_existing_ratchet(book.get("flow_base"), book.get("flow_last_net"), fills_net)' in src
    assert 'mi.pre_existing_ratchet(book.get("flow_base"), book.get("flow_last_net"), net)' not in src
    assert "flow = mi.flow_net(fills_net, fb)" in src and "verdict = mi.flow_reading(net, fills_net)" in src
    assert 'plan["flow_reading"] = {"why": verdict, "reading": net, "fills": fills_net}' in src
    assert 'await t.pool.execute(_SQL_BOOK_FLOW, book["id"], float(fb), float(fills_net))' in src
    csrc = inspect.getsource(ml._tick_candidate)
    assert "fills_net = mi.his_net(r.his_long, r.his_other)" in csrc
    assert "_open_flow(t, fills, la, oa, short, fills_net, r.mark" in csrc and '"flow_last_net": fills_net' in csrc
    osrc = inspect.getsource(ml._open_flow)
    assert "mi.pre_existing_block(net, fills, la, oa, since)" in osrc
    assert "mi.vwap_of(fills, la, oa, short=short, before=since)" in osrc
    msrc = inspect.getsource(mi)
    assert msrc.count("is_flow(f, since, late_s)") == 1 and msrc.count("is_flow(f, before, late_s)") == 1
    assert "LATE_FILL_S = float(LATE_POLL_ROW_S)" in msrc and "MIRROR_LATE" not in msrc
    for name in ("LATE_FILL_S", "is_flow", "flow_reading", "FLOW_READING_ZERO", "FLOW_READING_DISAGREE"):
        assert name in mi.__all__, name
    # no census name was added: the verdict lives on the plan, the served prefix stands
    assert "flow_reading" not in ml.CENSUS_KEYS and "flow_reading_zero" not in ml.CENSUS_KEYS
    assert ml.CENSUS_KEYS[-11:-8] == ("open_flow_only", "open_catchup", "flow_guard_unreadable")


def test_fold_the_mediums_and_the_fold_are_said_in_the_docs_and_the_module():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    sec = doc[doc.index("## 33. E12"):]
    for s in ("The fold (2026-09-08", "HIGH-1", "HIGH-2", "MEDIUM-1", "MEDIUM-2", "MEDIUM-3", "LOW-1",
              "LATE_POLL_ROW_S", "LATE_FILL_S", "is_flow", "flow_reading_disagree", "flow_reading_zero",
              "9 of 29", "follow-up", "vwap_unread", "VENUE_LEDGER_TOL_SHARES"):
        assert s in sec, s
    assert "sign-flip reopen is flow-only" in sec.lower()
    msrc = inspect.getsource(mi)
    for s in ("HIGH-1", "HIGH-2", "MEDIUM-1", "MEDIUM-2", "MEDIUM-3", "9 of 29", "FOLLOW-UP", "LATE_POLL_ROW_S"):
        assert s in msrc, s
    wsrc = inspect.getsource(ml)
    for s in ("HIGH-1", "HIGH-2", "MEDIUM-1", "LATE_FILL_S", "flow_reading_zero", "flow_reading_disagree"):
        assert s in wsrc, s


def test_fold_no_xfail_is_left_in_this_file():
    src = pathlib.Path(__file__).read_text()
    assert src.count("mark." + "xfail") == 0 and src.count("import " + "pytest") == 0


# ------------------------------------------------------ the fold re-review
#
# (2026-09-08; HIGH-1 / HIGH-2 as folded, attacked at their edges, and the
# E11 seating. No xfail here by design: every pin states the tree's
# behaviour, and the two named as findings say so in their docstrings.)

def test_fold_review_h1_the_s1_and_backfill_shaped_rows_are_the_block_and_the_poll_lag_is_flow(monkeypatch):
    """The venue's own time is `ts` on every ingest path (the S1 emitter's
    TradeEvent ts_epoch, history.py's $15; `detected_at` is the insert on
    both), and is_flow reads THAT: an S1 row stamped 2 h ago and a backfill
    row stamped 67 min ago, both ingested 5 s ago, are the block and their
    prices enter his cost; a poll row 2 min late is flow; a row with no
    ingest clock but a stamp inside the window is flow; a row with an
    ingest clock and no stamp is the block."""
    since = NOW - ml.FIRST_SIGHT_S
    s1 = _fill(M, "BUY", 4_000, 0.33, NOW - 7200, detected_at=NOW - 5, source="s1")
    bf = _fill(M, "BUY", 3_000, 0.35, NOW - 4000, detected_at=NOW - 5, enriched_at=NOW - 5, source="backfill")
    poll = _fill(M, "BUY", 1_000, 0.71, NOW - 125, detected_at=NOW - 5, source="poll")
    assert not mi.is_flow(s1, since) and not mi.is_flow(bf, since) and mi.is_flow(poll, since)
    assert mi.is_flow(_fill(M, "BUY", 1, 0.5, NOW - 20), since), "no ingest clock: the stamp decides"
    assert not mi.is_flow({"asset": M, "side": "BUY", "size": 1, "price": 0.5, "detected_at": NOW - 5}, since)
    block = _fill(M, "BUY", 10_000, 0.29, NOW - 9000)
    p, v, http = _world(monkeypatch, [block, s1, bf, poll], 18_000, 0.71, 0.73)
    _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (17_000.0, 18_000.0, 100)
    assert _places(v)[0][1:5] == (SLUG, 0.71, 100, False)
    assert b["last_plan"]["catchup"]["vwap"] == round((10_000 * 0.29 + 4_000 * 0.33 + 3_000 * 0.35) / 17_000, 6)


def test_fold_review_h2_a_sale_the_venue_shows_before_his_fill_lands_reduces_on_the_ingest_tick(monkeypatch):
    """The latency cost, measured: the venue reads 8,250 (his 25% sale)
    while the fills still say 11,000 -- named, target 100, nothing sold,
    nothing written. Two minutes later his SELL lands on the poll path
    (stamped then, ingested now): the ratchet moves on THAT tick and the
    reduce goes at his price. The wait is the ingest lag -- chain p50
    1.66 s / p90 9.17 s, poll ~281 s -- never longer."""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000)]
    p = _pool(fills=fills, snap={M: 8_250.0, N: 0.0})
    b = _flow_book(p)
    v1 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    st1 = _tick(p, v1, http=_mkt(8_250.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (10_000.0, 11_000.0, 100)
    assert b["last_plan"]["flow_reading"] == {"why": "flow_reading_disagree", "reading": 8_250.0, "fills": 11_000.0}
    assert not _places(v1) and not _sent(p, "ml-book-flow") and _census(st1, "exit_take") == 0
    p.fills.append(_fill(M, "SELL", 2_750, 0.70, NOW - 100, detected_at=NOW + 115, source="poll"))
    v2 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    st2 = _tick(p, v2, now=NOW + 120, http=_mkt(8_250.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (7_500.0, 8_250.0, 75)
    assert _places(v2)[0][2:6] == (0.69, 25, True, IOC) and _census(st2, "exit_take") == 1
    assert b["last_plan"]["flow_ratchet"] == {"from": 10_000.0, "to": 7_500.0} and "flow_reading" not in b["last_plan"]


def test_fold_review_h2_two_reducing_fills_in_one_tick_telescope_and_a_reduce_with_a_readd_moves_nothing(monkeypatch):
    """Two sales in one tick ratchet once by their sum -- the pro-rata
    product telescopes, so one read or two give the same block. A sale
    and a re-buy of the same size in one tick leave the fills' net where
    it was: no ratchet, no write (two ticks would have ratcheted on the
    sale and read the re-buy as flow -- D25's path dependence, stated)."""
    one = mi.pre_existing_ratchet(10_000.0, 11_000.0, 8_250.0)
    two = mi.pre_existing_ratchet(mi.pre_existing_ratchet(10_000.0, 11_000.0, 9_000.0), 9_000.0, 8_250.0)
    assert abs(one - two) < 1e-6 and one == 7_500.0
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000),
             _fill(M, "SELL", 1_000, 0.70, NOW - 100), _fill(M, "BUY", 1_000, 0.72, NOW - 50)]
    p = _pool(fills=fills, snap={M: 11_000.0, N: 0.0})
    b = _flow_book(p)
    v = _Venue(bid=0.71, ask=0.73, held={SLUG: 100})
    _tick(p, v, http=_mkt(11_000.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (10_000.0, 11_000.0, 100)
    assert not _sent(p, "ml-book-flow") and not _places(v) and "flow_ratchet" not in b["last_plan"]
    # two sales in one tick, on the planner: block 7,500, one write
    p2 = _pool(fills=fills[:2] + [_fill(M, "SELL", 2_000, 0.70, NOW - 100), _fill(M, "SELL", 750, 0.70, NOW - 90)],
               snap={M: 8_250.0, N: 0.0})
    b2 = _flow_book(p2)
    _tick(p2, _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0), http=_mkt(8_250.0))
    assert (b2["flow_base"], b2["target"]) == (7_500.0, 75) and len(_sent(p2, "ml-book-flow")) == 1


def test_fold_review_h2_the_witnessed_fall_is_applied_once_the_next_tick_writes_nothing(monkeypatch):
    """After the sale tick the row carries 7,500 / 8,250; the next tick
    with the same fills ratchets nothing, writes nothing, sells nothing."""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000),
             _fill(M, "SELL", 2_750, 0.70, NOW - 100)]
    p = _pool(fills=fills, snap={M: 8_250.0, N: 0.0})
    b = _flow_book(p)
    _tick(p, _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0), http=_mkt(8_250.0))
    assert (b["flow_base"], b["flow_last_net"], b["ledger_net"]) == (7_500.0, 8_250.0, 75)
    writes = len(_sent(p, "ml-book-flow"))
    v2 = _Venue(bid=0.69, ask=0.71, held={SLUG: 75})
    _tick(p, v2, now=NOW + 30, http=_mkt(8_250.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (7_500.0, 8_250.0, 75)
    assert len(_sent(p, "ml-book-flow")) == writes and not _places(v2) and "flow_ratchet" not in b["last_plan"]


def test_fold_review_h2_the_vanish_confirmation_still_reads_the_venue_but_never_moves_the_block(monkeypatch):
    """The data API confirms him gone (the old rule's reader) while his
    fills say he holds 11,000: whatever the flatten does to OUR flow,
    the block is never written down and never bought -- the next tick
    at 11,000 sizes 100 again, never 1,100."""
    from tests.test_mirror_live_worker import _gone
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000)]
    p = _pool(fills=fills, snap={M: 0.0, N: 0.0})
    b = _flow_book(p)
    v1 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=100.0)
    _tick(p, v1, http=_gone())
    assert b["flow_base"] == 10_000.0 and not _sent(p, "ml-book-flow")
    assert b["last_plan"].get("flow_reading", {}).get("why") == "flow_reading_zero"
    v2 = _Venue(bid=0.71, ask=0.73, held={SLUG: int(b["ledger_net"] or 0)})
    v2.orders = v1.orders
    _tick(p, v2, now=NOW + 30, http=_mkt(11_000.0))
    assert b["flow_base"] == 10_000.0 and b["target"] == 100
    assert all(c[3] <= 100 for c in _places(v2)), "never more than our flow"


def test_fold_review_h2_finding_a_fall_of_the_fills_net_with_no_reducing_fill_of_his_still_ratchets(monkeypatch):
    """WAS THE FINDING (the fold re-review's MEDIUM-1). The fold witnessed
    the fills' NET, not a reducing FILL: the chain-first collapse
    (ms.his_fills: a per-match row whose legs do not sum to the chain
    row still collapses) lowered the fills' net with no sale of his --
    two poll legs of 6,000 + 5,640 replaced by their chain row of 11,000
    -- and the ratchet moved: block 100,000 x 111,000 / 111,640 =
    99,426.7, target 1,157 against the corrected flow's 1,100 (57 shares
    of the block held as flow; nothing placed only because the 7-share
    reduce sat inside MIN_MOVE_FRAC). E12b: the ratchet moves only on a
    reducing fill of his clocked after the reference's clock
    (mi.reducing_since / mi.witnessed_ratchet) -- the collapse ratchets
    NOTHING, writes nothing, names `flow_fills_shrank` = {from 111,640,
    to 111,000, unexplained 640, witnessed 0}; the target is the
    corrected flow's 1,100 and the 64-share reduce toward it is HELD
    (`flow_hold`, reason `flow_fills_shrank`): he sold nothing, so
    nothing is sold. The name is the review's; the pin is the fix."""
    _rails_2026_09_06(monkeypatch)
    old = _fill(M, "BUY", 100_000, 0.29, NOW - 9000, source="chain")
    # stamped inside HOT_S so E11's quiet rotation reads the book on both ticks
    legs = [_fill(M, "BUY", 6_000, 0.71, NOW - 100, source="poll"), _fill(M, "BUY", 5_640, 0.71, NOW - 99, source="poll")]
    p = _pool(fills=[old] + legs, snap={M: 111_640.0, N: 0.0})
    b = _flow_book(p, ledger=1_164, flow_base=100_000.0, flow_last_net=111_640.0, flow_last_at=NOW - 99)
    _tick(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164}), http=_mkt(111_640.0))
    assert (b["flow_base"], b["target"]) == (100_000.0, 1_164) and not _sent(p, "ml-book-flow")
    p.fills[:] = [old, _fill(M, "BUY", 11_000, 0.71, NOW - 100, source="chain")]
    p.snap[M] = 111_100.0
    v2 = _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164})
    _tick(p, v2, now=NOW + 30, http=_mkt(111_100.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (100_000.0, 111_640.0, 1_100)
    lp = b["last_plan"]
    assert "flow_ratchet" not in lp and not _sent(p, "ml-book-flow") and not _places(v2)
    assert lp["flow_fills_shrank"] == {"from": 111_640.0, "to": 111_000.0, "unexplained": 640.0, "witnessed": 0.0}
    assert (lp["flow_hold"]["kind"], lp["flow_hold"]["qty"]) == ("reduce", 64) and b["last_reason"] == "flow_fills_shrank"
    assert b["ledger_net"] == 1_164 and lp["flow_net"] == 11_000.0
    assert not any(f.get("side") == "SELL" or f.get("asset") == N for f in p.fills), "no reducing fill of his"


def test_fold_review_e11_the_guard_and_the_plan_sit_inside_the_priority_context_and_the_rotation_is_nine():
    """b915373: tick_once / fast_tick_once run the tick body under
    venue_pace.priority_claims() inside _TICK_LOCK; E12's _flow_guard
    is called inside that body before any book row is read, in both
    ticks. QUIET_EVERY_TICKS is 9 via capped_env (env may only lower);
    the flow-wait pin reads the rotation off the constant."""
    src = inspect.getsource(ml.tick_once)
    assert src.index("async with _TICK_LOCK") < src.index("with venue_pace.priority_claims():") < src.index("await _tick(t, woken)")
    fsrc = inspect.getsource(ml.fast_tick_once)
    assert fsrc.index("_TICK_LOCK") < fsrc.index("with venue_pace.priority_claims():") < fsrc.index("await _fast_tick(t, taken)")
    tsrc = inspect.getsource(ml._tick)
    assert tsrc.index("_SQL_INTENT_GUARD") < tsrc.index("_flow_guard(t, stats)") < tsrc.index("_sql_books_open(t)")
    ftsrc = inspect.getsource(ml._fast_tick)
    assert ftsrc.index("_SQL_INTENT_GUARD") < ftsrc.index("_flow_guard(t, stats)") < ftsrc.index("_sql_books_open(t)")
    assert ml.QUIET_EVERY_TICKS == 9
    assert 'capped_env("MIRROR_QUIET_EVERY_TICKS", 9, floor=1)' in inspect.getsource(ml)
    assert "q = int(ml.QUIET_EVERY_TICKS)" in (ROOT / "backend" / "tests" / "test_e12_flow_only.py").read_text()
    keys = ml.CENSUS_KEYS
    assert keys[-11:-8] == ("open_flow_only", "open_catchup", "flow_guard_unreadable")
    assert keys[-8:-4] == ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")
