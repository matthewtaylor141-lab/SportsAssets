"""E12b (2026-09-08): the ratchet's witness is a reducing FILL of his,
clocked after the reference; the other token's SELL enters his cost.

The E12 fold re-review's MEDIUM-1: the fold witnessed his fills' NET,
and the net falls with no sale of his when the chain-first collapse
(ms.his_fills) replaces the poll lane's legs by a smaller chain row --
a 100,000 block ratcheted to 99,426.7 and 57 shares of it were held as
flow. Now the block moves only by the reduction his fills clocked after
the reference's clock (`mirror_books.flow_last_at`, migration 058)
carry on the book's axis (mi.reducing_since / mi.witnessed_ratchet); a
fall none of them explains is named `flow_fills_shrank` and the book
HOLDS (`flow_hold`): it never sells on a fall he did not make. LOW-1:
the docs say the S1 reconciler's wait. The first review's MEDIUM-3:
mi.vwap_of reads every fill that ADDS on the axis.

Driven on the real planner through the worker file's fakes, as the E12
pins are; the migration against a scratch Postgres where one answers
(skips visibly otherwise -- text is not proof).
"""
import inspect
import logging
import math
import pathlib
import re

import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.api import app as api_app
from sportsassets.scripts import migrate
from sportsassets.workers import mirror_live as ml
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_e12_flow_only import MIG_DIR, _drop, _flow_book, _one_book, _scratch, _sent
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    BUY, CID, M, N, NOW, SELL, SLUG, _armed, _cancels, _census, _fill, _gone, _mkt, _places, _pool,
    _rails_2026_09_06, _run, _tick, _Venue,
)

IOC = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
ROOT = pathlib.Path(__file__).resolve().parents[2]
SQL_058 = MIG_DIR / "058_mirror_books_flow_clock.sql"
# the re-review's harness: the block, the two poll legs, their chain row
OLD = _fill(M, "BUY", 100_000, 0.29, NOW - 9000, source="chain")
LEGS = [_fill(M, "BUY", 6_000, 0.71, NOW - 100, source="poll"),
        _fill(M, "BUY", 5_640, 0.71, NOW - 99, source="poll")]
CHAIN = _fill(M, "BUY", 11_000, 0.71, NOW - 100, source="chain")
FB_LANDED = round(100_000 * 111_000 / 111_640, 6)          # the landed ratchet on the collapse


def _collapse_world(monkeypatch, ledger=1_164, **book):
    """The re-review's world: block 100,000 at 0.29, the legs' 11,640 at
    0.71 as his flow (the fills' net 111,640), we hold `ledger`; the row
    clocked at the newer leg's ingest (what its reference write stamped)."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[OLD] + LEGS, snap={M: 111_640.0, N: 0.0})
    book.setdefault("flow_last_at", NOW - 99)
    b = _flow_book(p, ledger=ledger, flow_base=100_000.0, flow_last_net=111_640.0, **book)
    return p, b


def _collapse(p, *extra):
    """The chain row lands: the legs collapse into 11,000 (plus `extra`)."""
    p.fills[:] = [OLD, CHAIN, *extra]
    p.snap[M] = 111_000.0 + sum(mi._signed_size(f, M, N) or 0.0 for f in extra)


def _sale_world(monkeypatch, *sales, net):
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000), *sales]
    p = _pool(fills=fills, snap={M: float(net), N: 0.0})
    return p, _flow_book(p)


# ------------------------------------------------------------ the arithmetic

def test_e12b_the_witness_is_a_reducing_fill_on_the_blocks_axis_clocked_after_the_reference():
    since = NOW - 300
    sell_m = _fill(M, "SELL", 1_000, 0.70, NOW - 100)          # a SELL of the long token reduces a long
    buy_n = _fill(N, "BUY", 500, 0.30, NOW - 90)                 # a BUY of the other token reduces a long
    buy_m = _fill(M, "BUY", 2_000, 0.72, NOW - 80)               # an add
    sell_n = _fill(N, "SELL", 700, 0.30, NOW - 70)               # an ADD on a long book: the axis rises
    fills = [sell_m, buy_n, buy_m, sell_n]
    assert mi.reducing_since(fills, M, N, 10_000.0, since) == 1_500.0
    # the short block's mirror image: a BUY of the long token or a SELL of the other reduces it
    assert mi.reducing_since(fills, M, N, -10_000.0, since) == 2_700.0
    # the clock: strictly after the reference, the ingest clock over the stamp
    assert mi.reducing_since([_fill(M, "SELL", 1_000, 0.70, NOW - 100)], M, N, 10_000.0, NOW - 100) == 0.0
    assert mi.reducing_since([_fill(M, "SELL", 1_000, 0.70, NOW - 100, detected_at=NOW - 400)], M, N, 10_000.0, since) == 0.0
    assert mi.reducing_since([_fill(M, "SELL", 1_000, 0.70, NOW - 3400, detected_at=NOW - 5)], M, N, 10_000.0, since) == 1_000.0
    assert mi.reducing_since([{"asset": M, "side": "SELL", "size": 5, "price": 0.5, "detected_at": NOW - 5}], M, N, 10_000.0, since) == 5.0
    # no block, no clock, no axis, junk: nothing witnessed (fail closed toward holding)
    assert mi.reducing_since(fills, M, N, 0.0, since) == 0.0 and mi.reducing_since(fills, M, N, None, since) == 0.0
    assert mi.reducing_since(fills, M, N, 10_000.0, None) == 0.0 and mi.reducing_since(fills, None, None, 10_000.0, since) == 0.0
    assert mi.reducing_since(["x", None, {"asset": M, "side": "SELL", "size": "big", "ts": NOW}], M, N, 10_000.0, since) == 0.0
    assert mi.reducing_since(fills, M, N, 10_000.0, "x") == 0.0 and mi.reducing_since(None, M, N, 10_000.0, since) == 0.0


def test_e12b_the_ratchet_moves_by_the_witnessed_share_only_and_names_the_rest():
    r = mi.witnessed_ratchet
    # no fall: D25 unchanged on increases; nothing to witness
    assert r(10_000.0, 11_000.0, 13_000.0, 0.0) == (10_000.0, None)
    assert r(10_000.0, 11_000.0, 11_000.0, 2_750.0) == (10_000.0, None)
    # a fall his fills explain: D25 as landed
    assert r(10_000.0, 11_000.0, 8_250.0, 2_750.0) == (7_500.0, None)
    # a sale and a re-add in one tick explain more than the fall; the fall is what is applied
    assert r(10_000.0, 11_000.0, 10_000.0, 2_000.0) == (round(10_000 * 10 / 11, 6), None)
    # the collapse alone: the block stands, the whole fall unexplained, nothing witnessed
    assert r(100_000.0, 111_640.0, 111_000.0, 0.0) == (100_000.0, {"from": 111_640.0, "to": 111_000.0,
                                                                     "unexplained": 640.0, "witnessed": 0.0})
    # a sale and a collapse in one tick: the witnessed share only, the remainder named
    fb, sh = r(100_000.0, 111_640.0, 108_250.0, 2_750.0)
    assert fb == round(100_000 * (111_640 - 2_750) / 111_640, 6)
    assert sh == {"from": 111_640.0, "to": 108_250.0, "unexplained": 640.0, "witnessed": 2_750.0}
    # a witnessed crossing to <= 0 is 0; an unwitnessed one holds; a part-witnessed one is the witnessed part
    assert r(10_000.0, 11_000.0, 0.0, 11_000.0) == (0.0, None) and r(10_000.0, 11_000.0, -500.0, 11_500.0) == (0.0, None)
    assert r(10_000.0, 11_000.0, 0.0, 0.0) == (10_000.0, {"from": 11_000.0, "to": 0.0, "unexplained": 11_000.0, "witnessed": 0.0})
    fb2, sh2 = r(100_000.0, 111_640.0, 0.0, 111_000.0)
    assert fb2 == round(100_000 * 640 / 111_640, 6) and (sh2["unexplained"], sh2["witnessed"]) == (640.0, 111_000.0)
    # the short axis: the block negative, falls toward zero, the mirror image
    assert r(-10_000.0, -11_000.0, -8_250.0, 2_750.0) == (-7_500.0, None)
    assert r(-10_000.0, -11_000.0, -8_250.0, 0.0) == (-10_000.0, {"from": -11_000.0, "to": -8_250.0,
                                                                    "unexplained": 2_750.0, "witnessed": 0.0})
    # no reference: the block stands in; a block of 0 is 0; an unreadable witness is nothing seen; unreadables None
    assert r(10_000.0, None, 8_000.0, 2_000.0) == (8_000.0, None) and r(0.0, 5.0, 4.0, 1.0) == (0.0, None)
    assert r(10_000.0, 11_000.0, 8_250.0, "x")[1]["witnessed"] == 0.0 and r(10_000.0, 11_000.0, 8_250.0, -3.0)[0] == 10_000.0
    for bad in ((None, 1.0, 1.0, 0.0), (1.0, 1.0, None, 0.0), ("1", 1.0, 1.0, 0.0), (True, 1.0, 1.0, 0.0), (math.nan, 1.0, 1.0, 0.0)):
        assert r(*bad) == (None, None), bad
    assert mi.FLOW_FILLS_SHRANK == "flow_fills_shrank"
    for name in ("fills_clock", "reducing_since", "witnessed_ratchet", "FLOW_FILLS_SHRANK"):
        assert name in mi.__all__, name


def test_e12b_the_references_clock_is_the_newest_ingest_clock_the_reference_counted():
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 400, detected_at=NOW - 10),
             _fill(M, "BUY", 5, 0.5, NOW - 8)]
    assert mi.fills_clock(fills) == NOW - 8 and mi.fills_clock(fills[:2]) == NOW - 10
    assert mi.fills_clock([], NOW) == NOW and mi.fills_clock([{"asset": M}], NOW) == NOW and mi.fills_clock([]) is None
    assert mi.fills_clock(["x", None, fills[0]], NOW) == NOW - 9000 and mi.fills_clock([], "x") is None


def test_e12b_his_cost_is_read_over_every_fill_that_adds_on_the_axis():
    since = NOW - ml.FIRST_SIGHT_S
    # a long built by SELLING the other token alone: 1 - his price, and the catch-up reads it
    sells = [_fill(N, "SELL", 10_000, 0.71, NOW - 9000), _fill(N, "SELL", 2_000, 0.65, NOW - 8000)]
    assert mi.vwap_of(sells, M, N, before=since) == round((10_000 * 0.29 + 2_000 * 0.35) / 12_000, 6) == 0.30
    assert rules.open_catchup(12_000.0, 0.31, 0.10, 12_000.0, mi.vwap_of(sells, M, N))["why"] == "within_tol"
    assert rules.open_catchup(12_000.0, 0.33, 0.10, 12_000.0, mi.vwap_of(sells, M, N))["why"] == "flow_only"
    # mixed: a BUY of the long token at p beside a SELL of the other at 1 - p
    mixed = [_fill(M, "BUY", 5_000, 0.29, NOW - 9000), _fill(N, "SELL", 5_000, 0.60, NOW - 8000)]
    assert mi.vwap_of(mixed, M, N, before=since) == 0.345
    # reducing fills on the axis are excluded as before (a SELL of the long token, a BUY of the other)
    assert mi.vwap_of(mixed + [_fill(M, "SELL", 500, 0.9, NOW - 7000), _fill(N, "BUY", 500, 0.1, NOW - 7000)], M, N, before=since) == 0.345
    # a short's mirror image: a BUY of the other token at 1 - p and a SELL of the long token at p
    short = [_fill(N, "BUY", 5_000, 0.72, NOW - 9000), _fill(M, "SELL", 5_000, 0.35, NOW - 8000)]
    assert mi.vwap_of(short, M, N, short=True, before=since) == 0.315
    assert mi.vwap_of([_fill(M, "SELL", 5_000, 0.35, NOW - 8000)], M, N, short=True) == 0.35
    assert mi.vwap_of(short + [_fill(M, "BUY", 100, 0.5, NOW - 7000), _fill(N, "SELL", 100, 0.5, NOW - 7000)],
                      M, N, short=True, before=since) == 0.315
    assert rules.open_catchup(-5_500.0, 0.33, 0.10, -5_000.0, 0.315)["why"] == "within_tol"
    assert rules.open_catchup(-5_500.0, 0.33, 0.10, -5_000.0, 0.28)["why"] == "flow_only", "his BUYs of the other token alone: 5c"
    # a fill of both kinds in one block, and the BLOCK's fills only (a flow fill is not his cost over the block)
    both = [_fill(M, "BUY", 3_000, 0.30, NOW - 9000), _fill(N, "SELL", 1_000, 0.68, NOW - 8000)]
    assert mi.vwap_of(both, M, N, before=since) == round((3_000 * 0.30 + 1_000 * 0.32) / 4_000, 6) == 0.305
    flow = _fill(N, "SELL", 4_000, 0.20, NOW - 5)
    assert mi.vwap_of(both + [flow], M, N, before=since) == 0.305
    assert mi.vwap_of(both + [flow], M, N) == round((3_000 * 0.30 + 1_000 * 0.32 + 4_000 * 0.80) / 8_000, 6)
    # the E12 shapes stand: BUYs of the long token at p; a short's other-token BUYs at 1 - p; the wrong side None
    assert mi.vwap_of([_fill(N, "BUY", 12_000, 0.72, NOW - 9000)], M, N, before=since) is None
    assert mi.vwap_of([_fill(N, "BUY", 12_000, 0.72, NOW - 9000)], M, N, short=True, before=since) == 0.28
    assert mi.vwap_of([{"asset": N, "side": "SELL", "size": 10, "price": 1.0}], M, N) is None
    assert mi.vwap_of([{"asset": N, "side": "SELL", "size": "x", "price": 0.5}], M, N) is None
    assert mi.vwap_of([_fill(N, "SELL", 10, 0.5, NOW - 9000)], M, None) is None, "no other token: not on the axis"


def test_e12b_a_long_he_built_partly_by_selling_the_other_token_catches_up_on_his_cost_over_both_legs(monkeypatch):
    """5,000 Yes bought at 0.29 and 5,000 No (a pair) sold at 0.60 -- his
    cost over the block 0.345 on the axis; E12 read 0.29, his BUYs alone
    -- and the mark 0.36: 7c off the BUYs (flow-only under E12) but 1.5c
    off his cost over both legs, so the block is admitted and the book
    opens on his whole net at his cent. At 0.38 (3.5c) it is flow-only.
    (A long built by other-token SELLs ALONE never reaches the planner:
    net_positions floors each token at 0, so his net is long only with
    a BUY of the long token in the fills -- the pure pin above.)"""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 5_000, 0.29, NOW - 9000), _fill(N, "BUY", 5_000, 0.72, NOW - 8800),
             _fill(N, "SELL", 5_000, 0.60, NOW - 8000), _fill(M, "BUY", 500, 0.36, NOW - 10)]
    p = _pool(fills=fills, snap={M: 5_500.0, N: 0.0})
    v = _Venue(bid=0.35, ask=0.37)
    st = _tick(p, v, http=_mkt(5_500.0))
    b = _one_book(p)
    cu = b["last_plan"]["catchup"]
    assert (cu["vwap"], cu["mark"], cu["why"], cu["allowed"]) == (0.345, 0.36, "within_tol", True)
    assert (b["flow_base"], b["target"]) == (0.0, 550) and _census(st, "open_catchup") == 1
    assert len(_places(v)) == 1 and _places(v)[0][3] == 550 and _places(v)[0][2] <= 0.36
    _rails_2026_09_06(monkeypatch)
    p2 = _pool(fills=fills, snap={M: 5_500.0, N: 0.0})
    v2 = _Venue(bid=0.37, ask=0.39)
    st2 = _tick(p2, v2, http=_mkt(5_500.0))
    b2 = _one_book(p2)
    assert (b2["flow_base"], b2["target"]) == (5_000.0, 50) and b2["last_plan"]["catchup"]["why"] == "flow_only"
    assert b2["last_plan"]["catchup"]["vwap"] == 0.345 and _places(v2)[0][3] == 50 and _census(st2, "open_flow_only") == 1


# ------------------------------------------------------------- the witness

def test_e12b_the_collapse_alone_ratchets_nothing_names_the_shrink_and_the_book_holds(monkeypatch):
    """The re-review's harness, inverted: the legs collapse into their
    chain row (111,640 -> 111,000, no fill of his reducing) -- the block
    stands at 100,000, nothing is written, `flow_fills_shrank` names the
    640 unexplained, the target is the corrected flow's 1,100 and the
    64-share reduce toward it is HELD: he sold nothing, so nothing is
    sold. Sticky: the next tick reads the same fall against the same
    reference and holds again. No census name: the plan row says it."""
    p, b = _collapse_world(monkeypatch)
    _tick(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164}), http=_mkt(111_640.0))
    assert (b["flow_base"], b["target"]) == (100_000.0, 1_164) and not _sent(p, "ml-book-flow")
    _collapse(p)
    v2 = _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164})
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(111_000.0))
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"]) == (100_000.0, 111_640.0, NOW - 99, 1_100)
    lp = b["last_plan"]
    assert lp["flow_fills_shrank"] == {"from": 111_640.0, "to": 111_000.0, "unexplained": 640.0, "witnessed": 0.0}
    assert (lp["flow_hold"]["kind"], lp["flow_hold"]["qty"]) == ("reduce", 64) and lp["reason"] == "flow_fills_shrank"
    assert "flow_ratchet" not in lp and (lp["flow_base"], lp["flow_net"]) == (100_000.0, 11_000.0) and "flow_reading" not in lp
    assert not _sent(p, "ml-book-flow") and not _places(v2) and not _cancels(v2) and b["ledger_net"] == 1_164
    assert b["last_reason"] == "flow_fills_shrank" and _census(st2, "exit_take") == 0 and b["state"] == "live"
    assert "flow_fills_shrank" not in st2["census"] and "flow_fills_shrank" not in ml.CENSUS_KEYS
    v3 = _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164})
    _tick(p, v3, now=NOW + 60, http=_mkt(111_000.0))
    assert b["last_reason"] == "flow_fills_shrank" and not _places(v3) and not _sent(p, "ml-book-flow")
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"]) == (100_000.0, 111_640.0, NOW - 99)


def test_e12b_the_money_the_landed_rule_buys_57_of_the_block_at_his_cent_and_the_witness_rule_buys_nothing(monkeypatch):
    """The re-review's harness held 1,164 (10 % of the legs' 11,640), so
    the landed ratchet's 1,157 sat 7 under it, inside MIN_MOVE_FRAC:
    nothing placed. Hold the corrected 1,100 instead (the last 64 never
    filled) and the landed rule BUYS 57 of the block at his 0.71 --
    $40.47 of his history at his newest cent, one-shot per collapse,
    scaling with the legs' mismatch. A NULL clock IS the landed rule
    (a book opened between 057 and 058): it buys; the clocked book is
    on target and buys nothing."""
    for clock, placed in ((None, [(SLUG, 0.71, 57, False)]), (NOW - 99, [])):
        p, b = _collapse_world(monkeypatch, ledger=1_100, flow_last_at=clock)
        _collapse(p)
        v = _Venue(bid=0.71, ask=0.73, held={SLUG: 1_100})
        st = _tick(p, v, http=_mkt(111_000.0))
        assert [c[1:5] for c in _places(v)] == placed, clock
        if clock is None:
            assert (b["flow_base"], b["target"], b["flow_last_at"]) == (FB_LANDED, 1_157, NOW - 100)
            assert b["last_plan"]["flow_ratchet"] == {"from": 100_000.0, "to": FB_LANDED} and _census(st, "rest_placed") == 1
        else:
            assert (b["flow_base"], b["target"]) == (100_000.0, 1_100) and _census(st, "on_target") == 1
            assert b["last_plan"]["flow_fills_shrank"]["witnessed"] == 0.0 and "flow_hold" not in b["last_plan"]


def test_e12b_a_witnessed_25_percent_sale_ratchets_25_percent_and_reduces_at_his_price_within_the_tolerance(monkeypatch):
    p, b = _sale_world(monkeypatch, _fill(M, "SELL", 2_750, 0.70, NOW - 100, detected_at=NOW - 95), net=8_250.0)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    st = _tick(p, v, http=_mkt(8_250.0))
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"]) == (7_500.0, 8_250.0, NOW - 95, 75)
    assert _places(v)[0][2:6] == (0.69, 25, True, IOC) and _census(st, "exit_take") == 1 and b["ledger_net"] == 75
    lp = b["last_plan"]
    assert lp["flow_ratchet"] == {"from": 10_000.0, "to": 7_500.0} and lp["exit_px"] == 0.70 and lp["kind"] == "reduce"
    assert "flow_fills_shrank" not in lp and "flow_hold" not in lp
    w = _sent(p, "ml-book-flow")
    assert len(w) == 1 and w[0][1] == (b["id"], 7_500.0, 8_250.0, NOW - 95) and "flow_last_at = $4" in w[0][0]
    assert abs(0.69 - 0.70) <= float(rules.MIRROR_EXIT_TOL) + 1e-9
    # applied once: the next tick reads no fall, writes nothing, sells nothing; the clock stands
    v2 = _Venue(bid=0.69, ask=0.71, held={SLUG: 75})
    _tick(p, v2, now=NOW + 30, http=_mkt(8_250.0))
    assert (b["flow_base"], b["flow_last_at"], b["target"]) == (7_500.0, NOW - 95, 75)
    assert len(_sent(p, "ml-book-flow")) == 1 and not _places(v2) and "flow_ratchet" not in b["last_plan"]


def test_e12b_a_witnessed_full_exit_is_our_full_exit(monkeypatch):
    p, b = _sale_world(monkeypatch, _fill(M, "SELL", 11_000, 0.70, NOW - 100, detected_at=NOW - 95), net=0.0)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=100.0)
    st = _tick(p, v, http=_gone())
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"], b["ledger_net"]) == (0.0, 0.0, NOW - 95, 0, 0)
    lp = b["last_plan"]
    assert lp["kind"] == "flatten_vanished" and lp["flow_ratchet"] == {"from": 10_000.0, "to": 0.0}
    assert "flow_hold" not in lp and "flow_fills_shrank" not in lp and lp["exit_px_src"] == "his_fill"
    assert _places(v)[0][2:6] == (0.69, 100, True, IOC) and _census(st, "exit_take") == 1


def test_e12b_a_sale_and_a_collapse_in_one_tick_ratchet_by_the_witnessed_share_and_name_the_remainder(monkeypatch):
    """The legs collapse (640 unexplained) and he sells 2,750 in the same
    tick: net_t' = 111,640 - 2,750, the block 100,000 x 108,890 / 111,640
    = 97,536.7, the reference to the fills' 108,250 with its clock, the
    640 named. The reduce is to the target -- his 2.46 % (28.7 of ours)
    AND the 64 the collapse revealed we held past his flow: 93 sold."""
    p, b = _collapse_world(monkeypatch)
    _collapse(p, _fill(M, "SELL", 2_750, 0.70, NOW - 50, detected_at=NOW - 40))
    assert p.snap[M] == 108_250.0
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 1_164}, ioc_fill=93.0)
    st = _tick(p, v, http=_mkt(108_250.0))
    fb = round(100_000 * (111_640 - 2_750) / 111_640, 6)
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"]) == (fb, 108_250.0, NOW - 40)
    lp = b["last_plan"]
    assert lp["flow_fills_shrank"] == {"from": 111_640.0, "to": 108_250.0, "unexplained": 640.0, "witnessed": 2_750.0}
    assert lp["flow_ratchet"] == {"from": 100_000.0, "to": fb} and "flow_hold" not in lp
    assert lp["flow_net"] == round(108_250 - fb, 6) and b["target"] == 1_071
    assert _places(v)[0][2:6] == (0.69, 93, True, IOC) and b["ledger_net"] == 1_071 and _census(st, "exit_take") == 1
    assert _sent(p, "ml-book-flow")[0][1] == (b["id"], fb, 108_250.0, NOW - 40)


def test_e12b_the_hold_ends_at_his_next_sale_with_the_same_arithmetic(monkeypatch):
    """After the collapse's hold (the reference and its clock standing
    at 111,640 / NOW - 99), his 2,750 sale two ticks later is the
    witness: the same figures as the one-tick case above, one hold later."""
    p, b = _collapse_world(monkeypatch)
    _collapse(p)
    _tick(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164}), now=NOW + 30, http=_mkt(111_000.0))
    assert b["last_reason"] == "flow_fills_shrank" and b["flow_last_net"] == 111_640.0
    _collapse(p, _fill(M, "SELL", 2_750, 0.70, NOW + 50, detected_at=NOW + 55))
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 1_164}, ioc_fill=93.0)
    _tick(p, v, now=NOW + 60, http=_mkt(108_250.0))
    fb = round(100_000 * (111_640 - 2_750) / 111_640, 6)
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"], b["ledger_net"]) == (fb, 108_250.0, NOW + 55, 1_071, 1_071)
    assert b["last_plan"]["flow_fills_shrank"]["unexplained"] == 640.0 and _places(v)[0][2:6] == (0.69, 93, True, IOC)


def test_e12b_a_reducing_fill_clocked_before_the_reference_is_no_witness_and_one_after_is(monkeypatch):
    """The reference 11,000 carries its clock at NOW - 3000; his SELL
    ingested at NOW - 3500 (an old row the reference never counted,
    re-read) is no witness: the fall to 8,250 is unexplained, the block
    stands, the fills' net under the block reads a flow of 0 and the
    paired flatten it plans is HELD. The same SELL ingested at NOW -
    2900 is the witness: 7,500 / 75, the 25 sold at his price."""
    sale = _fill(M, "SELL", 2_750, 0.70, NOW - 3600, detected_at=NOW - 3500)
    p, b = _sale_world(monkeypatch, sale, net=8_250.0)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=100.0)
    st = _tick(p, v, http=_mkt(8_250.0))
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"]) == (10_000.0, 11_000.0, NOW - 3000, 0)
    lp = b["last_plan"]
    assert lp["flow_fills_shrank"] == {"from": 11_000.0, "to": 8_250.0, "unexplained": 2_750.0, "witnessed": 0.0}
    assert (lp["flow_hold"]["kind"], lp["flow_hold"]["qty"], lp["flow_net"]) == ("flatten_paired", 100, 0.0)
    assert not _places(v) and not _sent(p, "ml-book-flow") and b["ledger_net"] == 100 and _census(st, "exit_take") == 0
    p2, b2 = _sale_world(monkeypatch, {**sale, "detected_at": NOW - 2900}, net=8_250.0)
    v2 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    _tick(p2, v2, http=_mkt(8_250.0))
    assert (b2["flow_base"], b2["flow_last_at"], b2["target"], b2["ledger_net"]) == (7_500.0, NOW - 2900, 75, 75)
    assert _places(v2)[0][2:6] == (0.69, 25, True, IOC) and "flow_hold" not in b2["last_plan"]


def test_e12b_the_other_tokens_buy_is_a_witness_and_its_sell_is_an_add(monkeypatch):
    """His 2,750 No bought as we hold the Yes book is 2,750 less on the
    axis: the witness, the block to 7,500. A SELL of No is an ADD on a
    long book (the axis rises): nothing to witness, nothing reduced."""
    p, b = _sale_world(monkeypatch, _fill(N, "BUY", 2_750, 0.30, NOW - 100, detected_at=NOW - 95), net=8_250.0)
    p.snap[N] = 2_750.0
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    _tick(p, v, http=_mkt(11_000.0, 2_750.0))
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"]) == (7_500.0, 8_250.0, NOW - 95, 75)
    assert b["last_plan"]["flow_ratchet"] == {"from": 10_000.0, "to": 7_500.0} and len(_places(v)) == 1 and _places(v)[0][3] == 25
    assert mi.reducing_since([_fill(N, "SELL", 500, 0.70, NOW - 5)], M, N, 10_000.0, NOW - 3000) == 0.0
    assert mi.reducing_since([_fill(N, "SELL", 500, 0.70, NOW - 5)], M, N, -10_000.0, NOW - 3000) == 500.0, "on a short it reduces"


def test_e12b_the_poll_lanes_late_reducing_fill_is_a_witness(monkeypatch):
    """His SELL stamped before the reference's clock (NOW - 3400 against
    NOW - 3000) but ingested now (the poll lane, ~281 s behind): the
    ingest clock decides -- a witness, the reduce at his price."""
    p, b = _sale_world(monkeypatch, _fill(M, "SELL", 2_750, 0.70, NOW - 3400, detected_at=NOW - 5, source="poll"), net=8_250.0)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    _tick(p, v, http=_mkt(8_250.0))
    assert (b["flow_base"], b["flow_last_at"], b["target"], b["ledger_net"]) == (7_500.0, NOW - 5, 75, 75)
    assert _places(v)[0][2:6] == (0.69, 25, True, IOC) and "flow_fills_shrank" not in b["last_plan"]


def test_e12b_an_unwitnessed_fall_under_the_block_holds_and_sells_nothing(monkeypatch):
    """The fills re-read 1,500 smaller with no sale of his (9,000 + 500
    where 10,000 + 1,000 stood): the fills' net 9,500 under the block
    10,000 reads a flow of 0 and a target of 0 -- the paired flatten of
    our 100 is planned and HELD, nothing sold, the block and the
    reference untouched, the book live and not on the flat clock."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 9_000, 0.29, NOW - 9000), _fill(M, "BUY", 500, 0.71, NOW - 3000)],
              snap={M: 9_500.0, N: 0.0})
    b = _flow_book(p)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=100.0)
    st = _tick(p, v, http=_mkt(9_500.0))
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"]) == (10_000.0, 11_000.0, NOW - 3000, 0)
    lp = b["last_plan"]
    assert lp["flow_fills_shrank"] == {"from": 11_000.0, "to": 9_500.0, "unexplained": 1_500.0, "witnessed": 0.0}
    assert (lp["flow_hold"]["kind"], lp["flow_hold"]["qty"], lp["flow_net"]) == ("flatten_paired", 100, 0.0)
    assert not _places(v) and not _sent(p, "ml-book-flow") and b["ledger_net"] == 100 and b["state"] == "live"
    assert _census(st, "vanish_unconfirmed") == 0 and _census(st, "exit_take") == 0 and "flat_since" not in lp


def test_e12b_a_crossing_of_the_fills_net_with_no_reducing_fill_holds_and_the_confirmed_vanish_keeps_its_reader(monkeypatch):
    """The fills read him gone (no row at all) with no reducing fill:
    the fall to 0 is unexplained -- the block stands, nothing written.
    With the data API reading him holding, the vanish is unconfirmed,
    the paired flatten it falls to is HELD: nothing sold. With the data
    API confirming him gone, the vanish flatten is the old rule's own
    reader (the re-review's LOW-1) and runs -- the block is still never
    written down."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[], snap={M: 0.0, N: 0.0})
    b = _flow_book(p)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=100.0)
    st = _tick(p, v, http=_mkt(11_000.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (10_000.0, 11_000.0, 0)
    lp = b["last_plan"]
    assert lp["flow_fills_shrank"] == {"from": 11_000.0, "to": 0.0, "unexplained": 11_000.0, "witnessed": 0.0}
    assert lp["flow_hold"]["kind"] == "flatten_paired" and _census(st, "vanish_unconfirmed") == 1
    assert not _places(v) and not _sent(p, "ml-book-flow") and b["ledger_net"] == 100
    _rails_2026_09_06(monkeypatch)
    p2 = _pool(fills=[], snap={M: 0.0, N: 0.0})
    b2 = _flow_book(p2)
    v2 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=100.0)
    _tick(p2, v2, http=_gone())
    lp2 = b2["last_plan"]
    assert lp2["kind"] == "flatten_vanished" and "flow_hold" not in lp2 and lp2["flow_fills_shrank"]["witnessed"] == 0.0
    assert b2["flow_base"] == 10_000.0 and not _sent(p2, "ml-book-flow"), "the block is never written down"


# ------------------------------------------------------ the clock, the column

def test_e12b_the_clock_is_written_at_the_open_and_at_every_reference_write(monkeypatch):
    """The open stores the newest ingest clock the reference counted
    (his add's, NOW - 10) beside the block; his next add moves the
    reference and the clock (NOW + 25); his sale ratchets and moves
    both (NOW + 55). A fill the reference counted never witnesses it
    again: the add at NOW + 25 is not a witness at NOW + 60."""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 10, detected_at=NOW - 10)]
    p = _pool(fills=fills, snap={M: 11_000.0, N: 0.0})
    v = _Venue(bid=0.71, ask=0.73)
    _tick(p, v, http=_mkt(11_000.0))
    b = _one_book(p)
    ins = _sent(p, "INSERT INTO mirror_books")
    assert len(ins) == 1 and "flow_last_net, flow_last_at)" in ins[0][0] and ins[0][1][12:15] == (10_000.0, 11_000.0, NOW - 10)
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"]) == (10_000.0, 11_000.0, NOW - 10, 100)
    assert _places(v)[0][1:5] == (SLUG, 0.71, 100, False)
    p.fills.append(_fill(M, "BUY", 2_000, 0.75, NOW + 20, detected_at=NOW + 25))
    p.snap[M] = 13_000.0
    v2 = _Venue(bid=0.75, ask=0.77, held={SLUG: 100})
    v2.orders = v.orders
    _tick(p, v2, now=NOW + 30, http=_mkt(13_000.0))
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"]) == (10_000.0, 13_000.0, NOW + 25, 300)
    assert _sent(p, "ml-book-flow")[-1][1] == (b["id"], 10_000.0, 13_000.0, NOW + 25) and "flow_ratchet" not in b["last_plan"]
    p.fills.append(_fill(M, "SELL", 3_250, 0.74, NOW + 50, detected_at=NOW + 55))
    p.snap[M] = 9_750.0
    v3 = _Venue(bid=0.73, ask=0.75, held={SLUG: 300}, ioc_fill=75.0)
    v3.orders = v2.orders
    _tick(p, v3, now=NOW + 60, http=_mkt(9_750.0))
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"]) == (7_500.0, 9_750.0, NOW + 55)
    assert b["last_plan"]["flow_ratchet"] == {"from": 10_000.0, "to": 7_500.0} and "flow_fills_shrank" not in b["last_plan"]
    assert _sent(p, "ml-book-flow")[-1][1] == (b["id"], 7_500.0, 9_750.0, NOW + 55)


def test_e12b_the_clock_column_absent_is_the_landed_rule_said_on_the_heartbeat_and_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(ml, "_flow_clock_absent_logged", False)
    p, b = _collapse_world(monkeypatch)
    p.no_flow_clock_column = True
    _collapse(p)
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164}), http=_mkt(111_000.0))
    assert st["flow_clock_absent"] == "UndefinedColumnError" and "flow_guard_unreadable" not in st and st["status"] == "ok"
    assert "flow_column_absent" not in st and _census(st, "flow_guard_unreadable") == 0
    # the landed rule: the collapse ratchets (the re-review's finding, kept on purpose so no exit stalls)
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (FB_LANDED, 111_000.0, 1_157)
    assert b["last_plan"]["flow_ratchet"] == {"from": 100_000.0, "to": FB_LANDED} and "flow_fills_shrank" not in b["last_plan"]
    # the 057 statements: the write carries no clock, no read names the column, the row's own clock is untouched
    w = _sent(p, "ml-book-flow")
    assert len(w) == 1 and w[0][1] == (b["id"], FB_LANDED, 111_000.0) and "flow_last_at" not in w[0][0]
    assert all("flow_last_at" not in s for k, s, a in p.sent if "ml-flow-clock-guard" not in s)
    assert b["flow_last_at"] == NOW - 99
    assert len([r for r in caplog.records if "migration 058" in r.getMessage()]) == 1
    # a second tick and the fast tick say nothing more
    st2 = _tick(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164}), now=NOW + 30, http=_mkt(111_000.0))
    assert len([r for r in caplog.records if "migration 058" in r.getMessage()]) == 1
    assert st2["flow_clock_absent"] == "UndefinedColumnError" and _census(st2, "book_error") == 0
    _walk({SLUG: 1_164})
    fs = _fast(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164}), http=_mkt(111_000.0), now=NOW + 40)
    assert fs["flow_clock_absent"] == "UndefinedColumnError"
    # the open under 057 alone: the 14-parameter INSERT, no clock on the row, the block stored as E12 stores it
    _rails_2026_09_06(monkeypatch)
    p2 = _pool(fills=[_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 10)],
               snap={M: 11_000.0, N: 0.0})
    p2.no_flow_clock_column = True
    _tick(p2, _Venue(bid=0.71, ask=0.73), http=_mkt(11_000.0))
    b2 = _one_book(p2)
    ins = _sent(p2, "INSERT INTO mirror_books")
    assert len(ins[0][1]) == 14 and "flow_last_at" not in ins[0][0]
    assert (b2["flow_base"], b2["flow_last_net"], b2["flow_last_at"], b2["target"]) == (10_000.0, 11_000.0, None, 100)


def test_e12b_the_clock_probe_failing_for_any_other_reason_refuses_the_tick_by_name(monkeypatch):
    p, b = _collapse_world(monkeypatch)
    p.raise_on.append(("ml-flow-clock-guard", RuntimeError("db")))
    st = _tick(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164}), http=_mkt(111_640.0))
    assert _census(st, "flow_guard_unreadable") == 1 and st["status"] == "degraded"
    assert st["flow_guard_unreadable"] == "RuntimeError" and "flow_clock_absent" not in st
    assert "ml-books-open" not in " ".join(s for k, s, a in p.sent), "refused before any book read"
    _walk({SLUG: 1_164})
    ml._MIRROR_CENSUS.clear()
    fs = _fast(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164}), http=_mkt(111_640.0))
    assert _skips(fs) == {CID: "flow_guard_unreadable"} and _census(fs, "flow_guard_unreadable") == 1
    # the probes' order; with 057 absent the clock is never probed (nothing has a block to clock)
    src = inspect.getsource(ml._flow_guard)
    assert src.index("_SQL_FLOW_GUARD") < src.index("_SQL_FLOW_CLOCK_GUARD")
    p3 = _pool()
    p3.no_flow_column = True
    st3 = _tick(p3, _Venue(bid=0.30, ask=0.32))
    assert st3["flow_column_absent"] == "UndefinedColumnError" and "flow_clock_absent" not in st3
    assert not any("ml-flow-clock-guard" in s for k, s, a in p3.sent)
    assert ml._SQL_FLOW_CLOCK_GUARD == "SELECT flow_last_at FROM mirror_books LIMIT 0 /* ml-flow-clock-guard */"
    assert ml._SQL_FLOW_GUARD == "SELECT flow_base, flow_last_net FROM mirror_books LIMIT 0 /* ml-flow-guard */"


def test_e12b_a_null_clock_on_a_block_is_the_landed_rule_until_its_first_write_stamps_the_clock(monkeypatch):
    """A book opened between 057 and 058 (its clock NULL): the collapse
    ratchets as landed and the write stamps the clock (the chain row's
    ingest, NOW - 100). Clocked, a second collapse holds. A NULL block
    stays untouched: nothing of E12 on the plan, nothing written."""
    p, b = _collapse_world(monkeypatch, flow_last_at=None)
    _collapse(p)
    _tick(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164}), http=_mkt(111_000.0))
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"]) == (FB_LANDED, 111_000.0, NOW - 100, 1_157)
    assert _sent(p, "ml-book-flow")[0][1] == (b["id"], FB_LANDED, 111_000.0, NOW - 100)
    assert b["last_plan"]["flow_ratchet"]["from"] == 100_000.0 and "flow_fills_shrank" not in b["last_plan"]
    p.fills[:] = [OLD, _fill(M, "BUY", 10_500, 0.71, NOW - 100, source="chain")]
    p.snap[M] = 110_500.0
    v2 = _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164})
    _tick(p, v2, now=NOW + 30, http=_mkt(110_500.0))
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"]) == (FB_LANDED, 111_000.0, NOW - 100)
    assert b["last_plan"]["flow_fills_shrank"] == {"from": 111_000.0, "to": 110_500.0, "unexplained": 500.0, "witnessed": 0.0}
    assert len(_sent(p, "ml-book-flow")) == 1 and not _places(v2) and b["last_reason"] == "flow_fills_shrank"
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    _tick(p2, _Venue(bid=0.30, ask=0.32))
    # his 300 at the 2026-09-06 rails' ratio 0.10 (set above): the old rule's 30, nothing of E12
    assert b2["flow_last_at"] is None and b2["target"] == 30 and not _sent(p2, "ml-book-flow")
    assert not ({"flow_base", "flow_net", "flow_fills_shrank", "flow_hold", "flow_ratchet"} & set(b2["last_plan"]))


def test_e12b_the_fast_tick_plans_the_same_function(monkeypatch):
    p, b = _collapse_world(monkeypatch)
    _collapse(p)
    _walk({SLUG: 1_164})
    v = _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164})
    fs = _fast(p, v, http=_mkt(111_000.0))
    assert _skips(fs) == {} and (b["flow_base"], b["target"]) == (100_000.0, 1_100) and not _places(v)
    assert _census(fs, "fast_tick_placed") == 0 and b["last_reason"] == "flow_fills_shrank" and not _sent(p, "ml-book-flow")
    assert b["last_plan"]["flow_fills_shrank"]["witnessed"] == 0.0
    assert "await _tick_book(t, fresh)" in inspect.getsource(ml._fast_book)
    fsrc = inspect.getsource(ml._fast_tick)
    assert fsrc.index("_flow_guard(t, stats)") < fsrc.index("_sql_books_open(t)")
    # the wake with his witnessed sale: the same reduce the full tick makes
    p2, b2 = _sale_world(monkeypatch, _fill(M, "SELL", 2_750, 0.70, NOW - 10, detected_at=NOW - 5), net=8_250.0)
    _walk({SLUG: 100})
    v2 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    fs2 = _fast(p2, v2, http=_mkt(8_250.0))
    assert (b2["flow_base"], b2["flow_last_at"], b2["target"], b2["ledger_net"]) == (7_500.0, NOW - 5, 75, 75)
    assert _census(fs2, "fast_tick_placed") == 1 and _places(v2)[0][2:6] == (0.69, 25, True, IOC)


def test_e12b_under_the_hold_a_resting_add_is_cancelled_and_a_rest_of_his_exit_stands(monkeypatch):
    """A rest of 36 at his cent from a third leg (his flow 12,000 by the
    legs, we hold 1,164): the collapse to 11,000 makes the target 1,100
    -- the rest is an add for flow he never put on: cancelled under the
    hold's name, nothing bought, nothing sold. A rest of HIS exit (his
    witnessed 25 % sale, out of tolerance) stands through a later
    unwitnessed fall: it is his sale, not the collapse's."""
    _rails_2026_09_06(monkeypatch)
    third = _fill(M, "BUY", 360, 0.71, NOW - 98, source="poll")
    p = _pool(fills=[OLD] + LEGS + [third], snap={M: 112_000.0, N: 0.0})
    b = _flow_book(p, ledger=1_164, flow_base=100_000.0, flow_last_net=112_000.0, flow_last_at=NOW - 98)
    o = p.add_order(b, side=BUY, wire=0.71, qty=36, kind="increase", order_id="oid-add")
    _collapse(p)
    v = _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164})
    v.rest("oid-add", side="BUY", price=0.71, qty=36)
    _tick(p, v, http=_mkt(111_000.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (100_000.0, 112_000.0, 1_100)
    assert [c[:2] for c in _cancels(v)] == [("cancel", "oid-add")] and not _places(v)
    assert p.orders[o["id"]]["state"] == "cancelled" and b["open_order_id"] is None and b["ledger_net"] == 1_164
    assert b["last_reason"] == "flow_fills_shrank" and b["last_plan"]["flow_hold"]["kind"] == "reduce"
    assert b["last_plan"]["flow_fills_shrank"] == {"from": 112_000.0, "to": 111_000.0, "unexplained": 1_000.0, "witnessed": 0.0}
    # his exit's rest stands: the row already ratcheted on his sale (7,500 / 8,250, clocked at it), the
    # reduce of 25 rests at his 0.70 with the bid out of tolerance; now the block's row re-reads 500 smaller
    _rails_2026_09_06(monkeypatch)
    p2 = _pool(fills=[_fill(M, "BUY", 9_500, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000),
                      _fill(M, "SELL", 2_750, 0.70, NOW - 200, detected_at=NOW - 195)], snap={M: 7_750.0, N: 0.0})
    b2 = _flow_book(p2, ledger=100, flow_base=7_500.0, flow_last_net=8_250.0, flow_last_at=NOW - 195)
    o2 = p2.add_order(b2, side=SELL, wire=0.70, qty=25, kind="reduce", order_id="oid-exit")
    v2 = _Venue(bid=0.68, ask=0.71, held={SLUG: 100})
    v2.rest("oid-exit", side="SELL", price=0.70, qty=25)
    st2 = _tick(p2, v2, http=_mkt(7_750.0))
    assert (b2["flow_base"], b2["flow_last_net"], b2["flow_last_at"], b2["target"]) == (7_500.0, 8_250.0, NOW - 195, 25)
    lp2 = b2["last_plan"]
    assert lp2["flow_fills_shrank"] == {"from": 8_250.0, "to": 7_750.0, "unexplained": 500.0, "witnessed": 0.0}
    assert (lp2["flow_hold"]["kind"], lp2["flow_hold"]["qty"], lp2["flow_hold"]["cap"]) == ("reduce", 75, 25)
    assert lp2["open_order"] == o2["id"] and (lp2["side"], lp2["qty"]) == (SELL, 25), "the plan handed to _act: the rest's 25"
    assert not _cancels(v2) and not _places(v2) and p2.orders[o2["id"]]["state"] == "open" and b2["open_order_id"] == o2["id"]
    # the E12b fold (CRITICAL-1): the rest keeps its E4 life -- held outside the cent by E4's own name, not by the hold
    assert b2["last_reason"] == "open_order_pending" and _census(st2, "exit_out_of_tol") == 1
    assert b2["ledger_net"] == 100 and not _sent(p2, "ml-book-flow")


def test_e12b_the_open_refuses_a_clock_without_a_block_and_sends_the_057_insert_without_one():
    p = _pool()
    kw = dict(whale="rn1", cid=CID, slug=SLUG, long_asset=M, other_asset=N, ratio=0.10,
              anchor_usd=50.0, his_level=0.71, target=100, map_source="ledger", game_key="g")
    r = _run(le._open_mirror_book(p, **kw, flow_last_at=NOW))
    assert r["refusal"] == "open_failed:ValueError" and not p.books
    r = _run(le._open_mirror_book(p, **kw, flow_base=10_000.0, flow_last_net=11_000.0, flow_last_at=math.nan))
    assert r["refusal"] == "open_failed:ValueError" and not p.books
    r = _run(le._open_mirror_book(p, **kw, flow_base=10_000.0, flow_last_net=11_000.0))
    assert r["ok"] and p.books[r["book_id"]]["flow_last_at"] is None and p.books[r["book_id"]]["flow_base"] == 10_000.0
    ins = _sent(p, "INSERT INTO mirror_books")
    assert len(ins) == 1 and len(ins[0][1]) == 14 and "flow_last_at" not in ins[0][0]
    p2 = _pool()
    r2 = _run(le._open_mirror_book(p2, **kw, flow_base=10_000.0, flow_last_net=11_000.0, flow_last_at=NOW - 10))
    assert r2["ok"] and p2.books[r2["book_id"]]["flow_last_at"] == NOW - 10
    ins2 = _sent(p2, "INSERT INTO mirror_books")
    assert len(ins2[0][1]) == 15 and "flow_last_net, flow_last_at)" in ins2[0][0] and "$13, $14, $15" in ins2[0][0]
    assert "$15" in le._MIRROR_BOOK_INSERT_CLOCK_SQL and "flow_last_at" not in le._MIRROR_BOOK_INSERT_FLOW_SQL
    assert "flow" not in le._MIRROR_BOOK_INSERT_SQL


def test_e12b_058_exists_sorts_after_057_and_is_one_nullable_add_column_if_not_exists_with_no_default():
    assert SQL_058.exists()
    files = [x.name for x in sorted(MIG_DIR.glob("*.sql"))]
    i = files.index("057_mirror_books_flow.sql")
    assert files[i + 1] == "058_mirror_books_flow_clock.sql" and sum(f.startswith("058_") for f in files) == 1
    assert files[-1] == "059_mirror_orders_send_record.sql"    # E18 (PNL lane 6): the send record sorts after the clock
    sql = SQL_058.read_text()
    assert sql.splitlines()[0].startswith("-- 058: MIRROR BOOKS FLOW CLOCK (E12b, 2026-09-08")
    body = "\n".join(ln.split("--", 1)[0] for ln in sql.splitlines())
    stmts = [" ".join(s.split()) for s in body.split(";") if s.strip()]
    assert stmts == ["ALTER TABLE mirror_books ADD COLUMN IF NOT EXISTS flow_last_at DOUBLE PRECISION NULL"]
    up = " ".join(stmts).upper()
    assert "DEFAULT" not in up and "NOT NULL" not in up and "CREATE " not in up and "DROP " not in up
    assert "mirror_orders" not in " ".join(stmts) and "live_orders" not in " ".join(stmts) and not re.search(r"\bkind\b", sql)
    start = (ROOT / "backend" / "start.sh").read_text()
    assert "python -m sportsassets.scripts.migrate" in start and start.index("scripts.migrate") < start.index("exec uvicorn")
    assert 'sorted(MIGRATIONS_DIR.glob("*.sql"))' in pathlib.Path(migrate.__file__).read_text()
    # the worker's shapes name the clock past the 057 shape only; the guards; the writes
    assert "flow_last_at" in ml._SQL_BOOK_COLS and "flow_last_at" not in ml._SQL_BOOK_COLS_057 and "flow" not in ml._SQL_BOOK_COLS_056
    for name in ("_SQL_BOOKS_OPEN", "_SQL_BOOK_READ"):
        assert "flow_last_at" in getattr(ml, name) and "flow_base" in getattr(ml, name + "_057")
        assert "flow_last_at" not in getattr(ml, name + "_057") and "flow" not in getattr(ml, name + "_056")
    assert "flow_last_at = $4" in ml._SQL_BOOK_FLOW_CLOCK and "flow_last_at" not in ml._SQL_BOOK_FLOW
    assert "ml-book-flow" in ml._SQL_BOOK_FLOW_CLOCK and "ml-book-flow" in ml._SQL_BOOK_FLOW
    pglast = pytest.importorskip("pglast")
    from pglast.enums import AlterTableType, ConstrType
    parsed = pglast.parse_sql(sql)
    assert len(parsed) == 1 and type(parsed[0].stmt).__name__ == "AlterTableStmt"
    n = parsed[0].stmt
    assert n.relation.relname == "mirror_books" and len(n.cmds) == 1
    cmd = n.cmds[0]
    assert cmd.subtype == AlterTableType.AT_AddColumn and cmd.missing_ok is True
    col = cmd.def_
    types = {c.contype for c in (col.constraints or [])}
    assert ConstrType.CONSTR_NULL in types and ConstrType.CONSTR_NOTNULL not in types and ConstrType.CONSTR_DEFAULT not in types
    assert (col.colname, [x.sval for x in col.typeName.names][-1]) == ("flow_last_at", "float8")


def test_e12b_058_applies_on_a_real_postgres_twice_and_the_clock_statements_run_against_it():
    """047's table, 057, then the clock probe answering the absence
    rules.column_missing reads and the 057 shapes running; 058 twice; the
    column nullable double precision with no default; the clock INSERT,
    the 058 read, the clock UPDATE, the 057 UPDATE leaving the clock, the
    058 / 057 / 056 reads; a 057-opened row reading NULL there."""
    async def _go():
        admin, conn, name = await _scratch()
        try:
            await conn.execute("CREATE TABLE live_orders (id bigserial PRIMARY KEY, us_market_slug text, lane text)")
            await conn.execute((MIG_DIR / "047_mirror_live.sql").read_text())
            await conn.execute((MIG_DIR / "057_mirror_books_flow.sql").read_text())
            try:
                await conn.fetch(ml._SQL_FLOW_CLOCK_GUARD)
            except Exception as exc:  # noqa: BLE001 — the absence, as Postgres answers it
                assert rules.column_missing(exc, "flow_last_at") and type(exc).__name__ == "UndefinedColumnError"
            else:
                raise AssertionError("the clock probe read a column 057 never declared")
            assert await conn.fetch(ml._SQL_FLOW_GUARD) == []
            row57 = await conn.fetchrow(le._MIRROR_BOOK_INSERT_FLOW_SQL, "rn1", CID, SLUG, "g", M, N,
                                        rules.ORDER_INTENT, "ledger", 0.10, 50.0, 0.71, 100, 10_000.0, 11_000.0)
            assert (await conn.fetchrow(ml._SQL_BOOK_READ_057, row57["id"]))["flow_base"] == 10_000.0
            await conn.execute(SQL_058.read_text())
            await conn.execute(SQL_058.read_text())          # idempotent
            cols = await conn.fetch(
                "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'mirror_books' AND column_name = 'flow_last_at'")
            assert [tuple(c) for c in cols] == [("flow_last_at", "double precision", "YES", None)]
            assert await conn.fetch(ml._SQL_FLOW_CLOCK_GUARD) == []
            row = await conn.fetchrow(le._MIRROR_BOOK_INSERT_CLOCK_SQL, "rn1", CID, SLUG + "-2", "g", M, N,
                                      rules.ORDER_INTENT, "ledger", 0.10, 50.0, 0.71, 100, 10_000.0, 11_000.0,
                                      1_700_000_000.5)
            got = await conn.fetchrow(ml._SQL_BOOK_READ, row["id"])
            assert (got["flow_base"], got["flow_last_net"], got["flow_last_at"]) == (10_000.0, 11_000.0, 1_700_000_000.5)
            got57 = await conn.fetchrow(ml._SQL_BOOK_READ, row57["id"])
            assert got57["flow_last_at"] is None and got57["flow_base"] == 10_000.0, "opened under 057: NULL = the landed rule"
            await conn.execute(ml._SQL_BOOK_FLOW_CLOCK, row57["id"], 7_500.0, 8_250.0, 1_700_000_100.0)
            got57 = await conn.fetchrow(ml._SQL_BOOK_READ, row57["id"])
            assert (got57["flow_base"], got57["flow_last_net"], got57["flow_last_at"]) == (7_500.0, 8_250.0, 1_700_000_100.0)
            await conn.execute(ml._SQL_BOOK_FLOW, row["id"], 9_000.0, 10_000.0)      # the 057 write leaves the clock
            got = await conn.fetchrow(ml._SQL_BOOK_READ, row["id"])
            assert (got["flow_base"], got["flow_last_net"], got["flow_last_at"]) == (9_000.0, 10_000.0, 1_700_000_000.5)
            opened = await conn.fetch(ml._SQL_BOOKS_OPEN)
            assert {r["flow_last_at"] for r in opened} == {1_700_000_000.5, 1_700_000_100.0}
            older = await conn.fetch(ml._SQL_BOOKS_OPEN_057)
            assert len(older) == 2 and "flow_last_at" not in older[0].keys() and "flow_base" in older[0].keys()
            assert len(await conn.fetch(ml._SQL_BOOKS_OPEN_056)) == 2
            assert (await conn.fetchrow(ml._SQL_BOOK_READ_057, row["id"]))["id"] == row["id"]
            assert (await conn.fetchrow(ml._SQL_BOOK_READ_056, row["id"]))["id"] == row["id"]
        finally:
            await _drop(admin, conn, name)
    _run(_go())


# ---------------------------------------------------- the docs, the rails

def test_e12b_the_docs_and_the_module_say_the_witness_the_clock_the_migration_and_the_vwap():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    sec = doc[doc.index("## 33. E12"):]
    for s in ("E12b", "flow_last_at", "058", "flow_fills_shrank", "flow_hold", "flow_clock_absent", "reducing_since",
              "witnessed_ratchet", "fills_clock", "RECON_VENUE_LAG_S", "S1 reconciler", "99,426.7", "MEDIUM-1", "LOW-1",
              "MEDIUM-3", "1 − p", "test_e12b_witness.py", "floors each token at 0",
              # the E12b fold: CRITICAL-1, HIGH-1 closed; MEDIUM-1 / LOW-1 / LOW-2 and the Q3 judgement said
              "The E12b fold (2026-09-08", "CRITICAL-1", "HIGH-1", "LOW-2", "adding_since", "restored_block",
              "flow_fills_grew", "8,181.8", "8,250", "182 fewer", "442 where a clean block gives 500", "legacy-NULL",
              "THE Q3 JUDGEMENT", "test_e12b_review_pins.py", "`flow_hold.cap`", "MIRROR_REST_TTL_S",
              "the fold's one widening",
              # the fold re-review: MEDIUM-1 (the second clock on adds), LOW-1 (the legacy-NULL add), LOW-2 (the cap)
              "The E12b fold re-review (2026-09-08", "2 h 13 min", "SECOND CLOCK ON ADDS", "2,777", "legacy-NULL add",
              "a sale that happened, late-known", "899 s before the clock is flow, 901 s the block"):
        assert s in sec, s
    assert "at most the poll lane's lag later — and when BOTH live lanes miss his SELL, the S1 reconciler's" in sec
    msrc = inspect.getsource(mi)
    for s in ("E12b", "reducing_since", "witnessed_ratchet", "fills_clock", "flow_fills_shrank", "MEDIUM-1", "built by E12b",
              "EVERY FILL THAT ADDS ON THE AXIS", "THE E12b FOLD (2026-09-08", "CRITICAL-1", "HIGH-1", "LOW-1", "LOW-2",
              "adding_since", "restored_block", "flow_fills_grew", "182 fewer", "442 where a clean block gives 500",
              "THE E12b FOLD RE-REVIEW (2026-09-08", "2 h 13 min", "NO SECOND CLOCK HERE", "2,777 at 0.90"):
        assert s in msrc, s
    wsrc = inspect.getsource(ml)
    for s in ("E12b", "flow_clock_absent", "RECON_VENUE_LAG_S", "flow_fills_shrank", "flow_hold", "_SQL_FLOW_CLOCK_GUARD",
              "THE WITNESS IS A REDUCING FILL OF HIS", "THE HOLD (E12b)", "THE WITNESSED EXIT", "CRITICAL-1",
              "THE RISE (the E12b fold, HIGH-1)", "flow_fills_grew", "review's Q3, judged right"):
        assert s in wsrc, s
    assert "99,426.7" in SQL_058.read_text() and "flow_clock_absent" in SQL_058.read_text()


def test_e12b_no_new_census_name_no_new_knob_and_the_e12_rails_stand():
    keys = ml.CENSUS_KEYS
    for k in ("flow_fills_shrank", "flow_hold", "flow_clock_absent", "flow_last_at"):
        assert k not in keys, k
    assert keys[-11:-8] == ("open_flow_only", "open_catchup", "flow_guard_unreadable")
    assert keys[-8:-4] == ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")
    src = inspect.getsource(ml) + inspect.getsource(mi) + inspect.getsource(rules) + inspect.getsource(le)
    assert "MIRROR_FLOW_CLOCK" not in src and "MIRROR_WITNESS" not in src and 'capped_env("MIRROR_FLOW' not in src
    assert "MIRROR_SHRANK" not in src and "MIRROR_HOLD" not in src
    # the hold is a plan-row verdict: no _mirror_stop carries it, no order kind is added
    wsrc = inspect.getsource(ml)
    assert '_mirror_stop("flow_fills_shrank"' not in wsrc and "_mirror_stop(mi.FLOW_FILLS_SHRANK" not in wsrc
    assert '"flow_hold"' not in inspect.getsource(le)
    # the E12 numbers untouched: the allowance, the late-row bound, the dust, the window
    assert rules.MIRROR_CATCHUP_TOL_CENTS == 2.0 and mi.LATE_FILL_S == 900.0 and mi.VENUE_LEDGER_TOL_SHARES == 1.0
    assert ml.FIRST_SIGHT_S == 60.0 and float(rules.MIRROR_EXIT_TOL) > 0.0
    # the served stats: the heartbeat keys are set only when a column is absent; the cap stands
    st = ml._new_stats()
    assert "flow_clock_absent" not in st and "flow_column_absent" not in st and len(st) <= api_app._DETAIL_MAX_KEYS
    # the fold's pins on the ratchet's source still read: the landed call is kept verbatim for the clockless row
    tsrc = inspect.getsource(ml._tick_book)
    assert 'mi.pre_existing_ratchet(book.get("flow_base"), book.get("flow_last_net"), fills_net)' in tsrc
    assert 'mi.reducing_since(fills, la, oa, book.get("flow_base"), clock)' in tsrc
    assert tsrc.index("mi.witnessed_ratchet(") < tsrc.index("_SQL_BOOK_FLOW_CLOCK") < tsrc.index(
        'book["flow_base"], book["flow_last_net"] = float(fb), float(fills_net)')


# --------------------------------------------------------------- the E12b fold
#
# (2026-09-08; the review's CRITICAL-1 -- the hold suppresses NEW reduces
# only, a standing exit keeps its E4 life capped at its qty -- and HIGH-1
# -- an unexplained RISE of the fills' net goes to the block, never the
# flow. The review's own harnesses are inverted in test_e12b_review_pins.py.)

def _standing_exit_world(monkeypatch, placed_ts=None):
    """His 2,750 sale already ratcheted (7,500 / 8,250, clocked at it),
    our reduce of 25 resting at his 0.70; the block's row re-read 500
    smaller (an unwitnessed fall: the hold)."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 9_500, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000),
                     _fill(M, "SELL", 2_750, 0.70, NOW - 200, detected_at=NOW - 195)],
              snap={M: 7_750.0, N: 0.0})
    b = _flow_book(p, ledger=100, flow_base=7_500.0, flow_last_net=8_250.0, flow_last_at=NOW - 195)
    o = p.add_order(b, side=SELL, wire=0.70, qty=25, kind="reduce", order_id="oid-exit", placed_ts=placed_ts)
    return p, b, o


def test_e12b_fold_the_witnessed_exit_keeps_its_e4_life_under_the_hold_capped_at_the_witnessed_share(monkeypatch):
    """The rest past MIRROR_REST_TTL_S is cancelled by step O under its
    own `ttl` BEFORE any plan (the tree's rule, not the hold's) -- and
    under the hold it is RE-PLACED for the witnessed share alone: our
    100 past the reference's 75 = 25 at his 0.70 (E4's `exit_out_of_tol`
    with the bid outside the cent; `rest_placed`), never the plan's 75.
    The bid inside the cent on the next tick: the take, 25 IOC, ledger
    75; then nothing witnessed is outstanding and the hold places
    nothing. The cap is the reference's, so it needs no standing order."""
    p, b, o = _standing_exit_world(monkeypatch, placed_ts=NOW - float(rules.MIRROR_REST_TTL_S) - 100)
    v = _Venue(bid=0.68, ask=0.71, held={SLUG: 100})
    v.rest("oid-exit", side="SELL", price=0.70, qty=25)
    st = _tick(p, v, http=_mkt(7_750.0))
    lp = b["last_plan"]
    assert p.orders[o["id"]]["state"] == "cancelled" and p.orders[o["id"]]["reason"] == "ttl", "step O's own TTL, before the plan"
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "rest_placed") == 1 and b["ledger_net"] == 100
    assert _places(v)[0][2:6] == (0.70, 25, True, "TIME_IN_FORCE_GOOD_TILL_CANCEL")
    assert (lp["flow_hold"]["kind"], lp["flow_hold"]["qty"], lp["flow_hold"]["cap"]) == ("reduce", 75, 25)
    assert (lp["side"], lp["qty"]) == (SELL, 25) and b["last_reason"] == "rest_placed" and "open_order" not in lp
    assert lp["flow_fills_shrank"]["witnessed"] == 0.0 and (b["flow_base"], b["flow_last_net"]) == (7_500.0, 8_250.0)
    v2 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(7_750.0))
    assert _census(st2, "exit_take") == 1 and _places(v2)[-1][2:6] == (0.69, 25, True, IOC) and b["ledger_net"] == 75
    assert len(_cancels(v2)) == 1 and b["last_plan"]["flow_hold"]["cap"] == 25
    v3 = _Venue(bid=0.69, ask=0.71, held={SLUG: 75}, ioc_fill=50.0)
    _tick(p, v3, now=NOW + 60, http=_mkt(7_750.0))
    assert not _places(v3) and "cap" not in b["last_plan"]["flow_hold"] and b["last_reason"] == "flow_fills_shrank"
    # by source: the cap is the reference's target; the re-plan after a replace is capped the same way, inside _act
    tsrc = inspect.getsource(ml._tick_book)
    assert 'mi.flow_net(book.get("flow_last_net"), fb)' in tsrc and 'wp = mi.plan(int(ref_tg["target"]), float(ledger)' in tsrc
    assert 'rules.leg_action(book.get("intent"), wp.side) == "reduce"' in tsrc, "a reducing side only, through mi.plan's bands"
    asrc = inspect.getsource(ml._act)
    assert 'p = mi.Plan(p.side, min(int(p.qty), int(hold["cap"])), p.price, p.reason,' in asrc
    assert asrc.index("p = mi.plan(target, seat_ledger, seat_venue,") < asrc.index('int(hold["cap"])')


def test_e12b_fold_an_unexplained_rise_of_the_fills_net_goes_to_the_block_never_the_flow(monkeypatch):
    """The mirror image of the witness, pure and on the planner: the adds
    on either axis since the clock; a rise they explain is flow; a rise
    they do not explain joins the block (additive, never past the net;
    a block of 0 takes the net's side); a rise with no adding fill is
    block whole (on target, nothing placed, no `flow_ratchet`); a rise
    part-explained buys his real 300 and blocks the phantom 500; his
    real add after a hold is flow (the 64 phantom we held nets against
    it: 136 bought, not 200); the landed rule (058 absent) shares the
    defect and stays as it is."""
    since = NOW - 300
    buy_m, sell_n = _fill(M, "BUY", 1_000, 0.72, NOW - 100), _fill(N, "SELL", 500, 0.30, NOW - 90)
    sell_m, buy_n = _fill(M, "SELL", 700, 0.70, NOW - 80), _fill(N, "BUY", 300, 0.30, NOW - 70)
    fills = [buy_m, sell_n, sell_m, buy_n]
    assert mi.adding_since(fills, M, N, 10_000.0, since) == 1_500.0 and mi.adding_since(fills, M, N, -10_000.0, since) == 1_000.0
    assert mi.adding_since(fills, M, N, 10_000.0, NOW - 100) == 500.0, "strictly after the clock"
    assert mi.adding_since(fills, M, N, 0.0, since) == 0.0 and mi.adding_since(fills, M, N, 10_000.0, None) == 0.0
    assert mi.adding_since(["x", {"asset": M, "side": "BUY", "size": "big", "ts": NOW}], M, N, 10_000.0, since) == 0.0
    # the poll lane's late add (stamped 400 s ago, ingested now) is an add; a row stamped past LATE_FILL_S is
    # his history (the fold re-review's MEDIUM-1: pinned below and in test_e12b_review_pins.py's f1)
    assert mi.adding_since([_fill(M, "BUY", 5, 0.5, NOW - 400, detected_at=NOW - 5)], M, N, 10_000.0, since) == 5.0
    assert mi.adding_since([_fill(M, "BUY", 5, 0.5, NOW - 3400, detected_at=NOW - 5)], M, N, 10_000.0, since) == 0.0
    r = mi.restored_block
    assert r(7_500.0, 8_250.0, 8_250.0, 0.0) == (7_500.0, None) and r(7_500.0, 8_250.0, 8_000.0, 0.0) == (7_500.0, None)
    assert r(7_500.0, 8_250.0, 9_000.0, 750.0) == (7_500.0, None) and r(7_500.0, 8_250.0, 9_000.0, 2_000.0) == (7_500.0, None)
    assert r(7_500.0, 8_250.0, 9_000.0, 0.0) == (8_250.0, {"from": 8_250.0, "to": 9_000.0, "unexplained": 750.0, "witnessed": 0.0})
    assert r(7_500.0, 8_250.0, 9_000.0, 300.0) == (7_950.0, {"from": 8_250.0, "to": 9_000.0, "unexplained": 450.0, "witnessed": 300.0})
    assert r(9_000.0, 8_250.0, 8_500.0, 0.0) == (8_500.0, {"from": 8_250.0, "to": 8_500.0, "unexplained": 250.0, "witnessed": 0.0})
    assert r(0.0, 1_000.0, 1_500.0, 0.0) == (500.0, {"from": 1_000.0, "to": 1_500.0, "unexplained": 500.0, "witnessed": 0.0})
    assert r(0.0, 0.0, 0.0, 0.0) == (0.0, None)
    assert r(-7_500.0, -8_250.0, -9_000.0, 0.0) == (-8_250.0, {"from": -8_250.0, "to": -9_000.0, "unexplained": 750.0, "witnessed": 0.0})
    assert r(-7_500.0, -8_250.0, -9_000.0, 750.0) == (-7_500.0, None)
    assert r(7_500.0, None, 8_000.0, 0.0) == (8_000.0, {"from": 7_500.0, "to": 8_000.0, "unexplained": 500.0, "witnessed": 0.0})
    assert r(7_500.0, 8_250.0, 9_000.0, "x")[0] == 8_250.0 and r(7_500.0, 8_250.0, 9_000.0, -5.0)[1]["witnessed"] == 0.0
    for bad in ((None, 1.0, 1.0, 0.0), (1.0, 1.0, None, 0.0), ("1", 1.0, 1.0, 0.0), (True, 1.0, 1.0, 0.0), (math.nan, 1.0, 1.0, 0.0)):
        assert r(*bad) == (None, None), bad
    assert mi.FLOW_FILLS_GREW == "flow_fills_grew"
    for name in ("adding_since", "restored_block", "FLOW_FILLS_GREW"):
        assert name in mi.__all__, name
    # (a) a rise with no adding fill: the block's row re-read 500 larger -- block whole, on target, nothing placed
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 10_500, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000)],
              snap={M: 11_500.0, N: 0.0})
    b = _flow_book(p)
    v = _Venue(bid=0.71, ask=0.73, held={SLUG: 100})
    st = _tick(p, v, http=_mkt(11_500.0))
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"]) == (10_500.0, 11_500.0, NOW - 3000, 100)
    lp = b["last_plan"]
    assert lp["flow_fills_grew"] == {"from": 11_000.0, "to": 11_500.0, "unexplained": 500.0, "witnessed": 0.0}
    assert "flow_ratchet" not in lp and "flow_fills_shrank" not in lp and "flow_hold" not in lp and lp["flow_net"] == 1_000.0
    assert not _places(v) and _census(st, "on_target") == 1
    assert _sent(p, "ml-book-flow")[0][1] == (b["id"], 10_500.0, 11_500.0, NOW - 3000)
    # (b) part-explained: his real 300 at 0.75 beside the phantom 500 -- 30 bought at his cent, the 500 to the block
    _rails_2026_09_06(monkeypatch)
    p2 = _pool(fills=[_fill(M, "BUY", 10_500, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000),
                      _fill(M, "BUY", 300, 0.75, NOW - 10, detected_at=NOW - 5)], snap={M: 11_800.0, N: 0.0})
    b2 = _flow_book(p2)
    v2 = _Venue(bid=0.75, ask=0.77, held={SLUG: 100})
    _tick(p2, v2, http=_mkt(11_800.0))
    assert (b2["flow_base"], b2["flow_last_net"], b2["flow_last_at"], b2["target"]) == (10_500.0, 11_800.0, NOW - 5, 130)
    assert b2["last_plan"]["flow_fills_grew"] == {"from": 11_000.0, "to": 11_800.0, "unexplained": 500.0, "witnessed": 300.0}
    assert _places(v2)[0][1:5] == (SLUG, 0.75, 30, False) and b2["last_plan"]["flow_net"] == 1_300.0
    # (c) his real add after a hold: the collapse held (reference 111,640 standing), then his 2,000 at 0.75
    p3, b3 = _collapse_world(monkeypatch)
    _collapse(p3)
    _tick(p3, _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164}), now=NOW + 30, http=_mkt(111_000.0))
    assert b3["last_reason"] == "flow_fills_shrank" and b3["flow_last_net"] == 111_640.0
    _collapse(p3, _fill(M, "BUY", 2_000, 0.75, NOW + 50, detected_at=NOW + 55))
    v3 = _Venue(bid=0.75, ask=0.77, held={SLUG: 1_164})
    st3 = _tick(p3, v3, now=NOW + 60, http=_mkt(113_000.0))
    assert (b3["flow_base"], b3["flow_last_net"], b3["flow_last_at"], b3["target"]) == (100_000.0, 113_000.0, NOW + 55, 1_300)
    lp3 = b3["last_plan"]
    assert not ({"flow_fills_grew", "flow_fills_shrank", "flow_hold", "flow_ratchet"} & set(lp3)) and lp3["flow_net"] == 13_000.0
    assert _places(v3)[0][1:5] == (SLUG, 0.75, 136, False) and _census(st3, "rest_placed") == 1
    # (d) the landed rule (058 absent) shares the defect and stays as it is: the correction is flow, 75 bought
    _rails_2026_09_06(monkeypatch)
    block, add = _fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000)
    p4 = _pool(fills=[block, add, _fill(M, "SELL", 2_750, 0.70, NOW - 100, detected_at=NOW - 95, source="poll")],
               snap={M: 8_250.0, N: 0.0})
    p4.no_flow_clock_column = True
    b4 = _flow_book(p4)
    _tick(p4, _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0), http=_mkt(8_250.0))
    assert (b4["flow_base"], b4["flow_last_net"], b4["ledger_net"]) == (7_500.0, 8_250.0, 75)
    p4.fills[:] = [block, add, _fill(M, "SELL", 2_000, 0.70, NOW - 100, detected_at=NOW + 20, source="chain")]
    p4.snap[M] = 9_000.0
    v4 = _Venue(bid=0.71, ask=0.73, held={SLUG: 75})
    _tick(p4, v4, now=NOW + 30, http=_mkt(9_000.0))
    assert (b4["flow_base"], b4["flow_last_net"], b4["target"]) == (7_500.0, 9_000.0, 150)
    assert _places(v4)[0][1:5] == (SLUG, 0.71, 75, False) and "flow_fills_grew" not in b4["last_plan"]


def test_e12b_fold2_an_adding_row_stamped_past_the_allowance_is_his_history_the_block_never_bought(monkeypatch):
    """The fold re-review's MEDIUM-1: the second clock on adds. Pure: an
    add stamped 899 s before the reference's clock is an add (flow), 901 s
    before it is his history (not counted: the rise goes to the block);
    an unstamped adding row is old; an unreadable allowance admits only a
    stamp at or after the clock; a stamp-only add (LOW-1) is read by its
    stamp as the tree behaves -- after the clock an add, before it the
    block. The open's is_flow and this rule share the ONE constant, by
    source and by signature. reducing_since keeps one clock: an
    old-stamped SELL inserted now is still a witness (the planner: his
    2,750 sale stamped 2 h ago and ingested now ratchets and reduces).
    The planner on the add: his backfilled 2,000 @0.33 restores the block
    to 12,000 and nothing is bought; the same row stamped 899 s before
    the clock is flow and 200 are bought at his cent."""
    since = NOW - 3000.0
    late = float(mi.LATE_FILL_S)
    inside = _fill(M, "BUY", 2_000, 0.33, since - (late - 1), detected_at=NOW - 5, source="backfill")
    outside = _fill(M, "BUY", 2_000, 0.33, since - (late + 1), detected_at=NOW - 5, source="backfill")
    assert mi.adding_since([inside], M, N, 10_000.0, since) == 2_000.0 and mi.adding_since([outside], M, N, 10_000.0, since) == 0.0
    assert mi.adding_since([_fill(M, "BUY", 2_000, 0.33, since - late, detected_at=NOW - 5)], M, N, 10_000.0, since) == 2_000.0, "the bound exactly"
    assert mi.adding_since([{"asset": M, "side": "BUY", "size": 5, "price": 0.5, "detected_at": NOW - 5}], M, N, 10_000.0, since) == 0.0
    assert mi.adding_since([inside], M, N, 10_000.0, since, late_s=None) == 0.0
    assert mi.adding_since([_fill(M, "BUY", 5, 0.5, since + 1, detected_at=NOW - 5)], M, N, 10_000.0, since, late_s="x") == 5.0
    assert mi.adding_since([_fill(M, "BUY", 5, 0.5, since + 5)], M, N, 10_000.0, since) == 5.0, "stamp only, after the clock"
    assert mi.adding_since([_fill(M, "BUY", 5, 0.5, since - 5)], M, N, 10_000.0, since) == 0.0, "stamp only, before it (LOW-1)"
    # the short axis reads the same second clock
    assert mi.adding_since([_fill(N, "BUY", 700, 0.7, since - (late + 1), detected_at=NOW - 5)], M, N, -10_000.0, since) == 0.0
    assert mi.adding_since([_fill(N, "BUY", 700, 0.7, since - 10, detected_at=NOW - 5)], M, N, -10_000.0, since) == 700.0
    # one constant: is_flow's and adding_since's allowance is LATE_FILL_S, by signature and by source
    assert inspect.signature(mi.adding_since).parameters["late_s"].default is mi.LATE_FILL_S
    assert inspect.signature(mi.is_flow).parameters["late_s"].default is mi.LATE_FILL_S
    assert "ts < at0 - late" in inspect.getsource(mi.adding_since) and "ts >= float(since) - late" in inspect.getsource(mi.is_flow)
    assert "late" not in inspect.getsource(mi.reducing_since).split('"""')[2], "reducing_since keeps one clock"
    assert mi.reducing_since([_fill(M, "SELL", 2_750, 0.70, NOW - 7200, detected_at=NOW - 5)], M, N, 10_000.0, since) == 2_750.0
    # the planner: the backfilled add restores the block; the same row inside the allowance is flow and bought
    for stamp, block, target, placed in ((NOW - 8000, 12_000.0, 100, []), (since - (late - 1), 10_000.0, 300, [(SLUG, 0.71, 200, False)])):
        _rails_2026_09_06(monkeypatch)
        fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000, detected_at=since),
                 _fill(M, "BUY", 2_000, 0.33, stamp, detected_at=NOW - 5, source="backfill")]
        p = _pool(fills=fills, snap={M: 13_000.0, N: 0.0})
        b = _flow_book(p, flow_last_at=since)
        v = _Venue(bid=0.71, ask=0.73, held={SLUG: 100})
        _tick(p, v, http=_mkt(13_000.0))
        assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"]) == (block, 13_000.0, NOW - 5, target), stamp
        assert [c[1:5] for c in _places(v)] == placed, stamp
        assert ("flow_fills_grew" in b["last_plan"]) == (block == 12_000.0), stamp
    # the planner on the old-stamped SELL: a witness -- his sale happened, late-known
    _rails_2026_09_06(monkeypatch)
    p2 = _pool(fills=[_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000),
                      _fill(M, "SELL", 2_750, 0.70, NOW - 7200, detected_at=NOW - 5, source="s1")], snap={M: 8_250.0, N: 0.0})
    b2 = _flow_book(p2)
    v2 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    _tick(p2, v2, http=_mkt(8_250.0))
    assert (b2["flow_base"], b2["flow_last_at"], b2["target"], b2["ledger_net"]) == (7_500.0, NOW - 5, 75, 75)
    assert _places(v2)[0][2:6] == (0.69, 25, True, IOC)
