"""E12 (2026-09-08): the mirror follows his FLOW from first sight; the
block he built before we saw the market is never bought unless the mark
is within MIRROR_CATCHUP_TOL_CENTS of his cost over it.

Owner (verbatim): "How do we make sure we go up when he goes up and we go
down when he loses. I want this to be proportional and ensure that we
aren't screwing ourselves in the management of the same trades"; "10%
of what he puts on everything he takes". The 03:22Z paired day: on the
losers we opened late our average cost sat far above his (Khantiw/Hillel
ours 0.670 vs his 0.477; Richard/Kitthac 0.71 vs 0.287) because the open
sized ratio x his WHOLE net and priced it at his NEWEST cent.

THE RULE, EXACTLY (docs/mirror-to-a-tee-program.md). Decision 13: "(A)
follow only his flow from first sight (Rule LE, pro-rata ratchet); (B)
build r x his_net into his open book at his newest level (today's
code); ... RECOMMENDATION: (A) now ...; (B) never." D25, the ratchet's
words: "Amended to PRO-RATA on reductions: `block_t = block_{t-1} x
net_t/net_{t-1}` when net falls, unchanged on increases, 0 on a
crossing to <= 0." The doc's words win over the brief's peak-based
formula (the two differ after a reduction followed by an increase:
pinned below).

Driven end to end against the worker file's fakes (its autouse rails
are imported); the migration against a scratch Postgres where one
answers (skips visibly otherwise -- text is not proof).
"""
import importlib
import inspect
import json
import logging
import math
import os
import pathlib
import re
import uuid

import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.api import app as api_app
from sportsassets.scripts import migrate
from sportsassets.workers import mirror_live as ml
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _armed, _cancels, _census, _fill, _gone, _kinds, _mkt, _places, _pool,
    _rails_2026_09_06, _run, _shadow_row, _shorts_on, _tick, _Venue,
)

GTC_TIF = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
IOC_TIF = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
MIG_DIR = pathlib.Path(migrate.MIGRATIONS_DIR)
SQL_057 = MIG_DIR / "057_mirror_books_flow.sql"
ROOT = pathlib.Path(__file__).resolve().parents[2]
CATCHUP_KEYS = {"vwap", "mark", "tol", "allowed", "flow_base", "why"}
DSN_BASE = os.environ.get(
    "MIRROR_SQL_PIN_DSN",
    os.environ.get("S1_SQL_PIN_DSN",
                   "postgresql://sportsassets:sportsassets@localhost:5432/postgres"))


def _block_and_add(add_px=0.71, block=10_000.0, add=1_000.0, add_at=None):
    """His fills as the tick holds them at first sight: the block he
    built at 0.29 while nobody watched (2.5 h old), and the add that
    woke the market (10 s old, inside FIRST_SIGHT_S)."""
    return [_fill(M, "BUY", block, 0.29, NOW - 9000),
            _fill(M, "BUY", add, add_px, NOW - 10 if add_at is None else add_at)]


def _late_world(monkeypatch, fills=None, net=11_000.0, bid=0.71, ask=0.73, **venue):
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_block_and_add() if fills is None else fills, snap={M: float(net), N: 0.0})
    return p, _Venue(bid=bid, ask=ask, **venue), _mkt(float(net))


def _flow_book(p, ledger=100, flow_base=10_000.0, flow_last_net=11_000.0, **over):
    """A book opened under E12 on the block-and-add world. E12b: it
    carries the reference's clock (migration 058) as a book opened after
    058 does -- the add's ingest clock, NOW - 3000, so a reducing fill
    stamped since is a witness; `flow_last_at=None` in `over` is a book
    opened between 057 and 058 (the landed rule until its first write)."""
    over.setdefault("avg_cost", 0.71)
    over.setdefault("flow_last_at", NOW - 3000.0)
    return p.add_book(ledger=ledger, ratio=0.10, flow_base=flow_base, flow_last_net=flow_last_net,
                      **over)


def _one_book(p):
    assert len(p.books) == 1
    return next(iter(p.books.values()))


def _sent(p, tag):
    return [(s, a) for k, s, a in p.sent if tag in s]


# ------------------------------------------------------------ the arithmetic

def test_e12_the_tolerance_constant_only_lowers_from_the_environment(monkeypatch):
    assert rules.MIRROR_CATCHUP_TOL_CENTS == 2.0
    src = inspect.getsource(rules)
    assert src.count('capped_env("MIRROR_CATCHUP_TOL_CENTS"') == 1
    assert 'MIRROR_CATCHUP_TOL_CENTS = capped_env("MIRROR_CATCHUP_TOL_CENTS", 2.0, floor=0.0)' in src
    assert "MIRROR_CATCHUP_TOL_CENTS" in rules.__all__ and "open_catchup" in rules.__all__
    try:
        for raw, want in (("1", 1.0), ("0", 0.0), ("5", 2.0), ("-3", 0.0), ("junk", 2.0),
                          ("inf", 2.0), ("1.5", 1.5)):
            monkeypatch.setenv("MIRROR_CATCHUP_TOL_CENTS", raw)
            mod = importlib.reload(rules)
            assert mod.MIRROR_CATCHUP_TOL_CENTS == want, (raw, mod.MIRROR_CATCHUP_TOL_CENTS)
    finally:
        monkeypatch.delenv("MIRROR_CATCHUP_TOL_CENTS", raising=False)
        importlib.reload(rules)
    assert rules.MIRROR_CATCHUP_TOL_CENTS == 2.0
    # the first-sight window is two full ticks, derived from POLL_S, no knob
    assert ml.FIRST_SIGHT_S == 60.0 and ml.FIRST_SIGHT_S == 2.0 * ml.POLL_S
    assert "FIRST_SIGHT_S = 2.0 * POLL_S" in inspect.getsource(ml)
    assert "MIRROR_FIRST_SIGHT" not in inspect.getsource(ml)


def test_e12_the_block_at_first_sight_is_his_net_less_the_fills_inside_the_window():
    fills = _block_and_add()
    since = NOW - ml.FIRST_SIGHT_S
    assert mi.pre_existing_block(11_000.0, fills, M, N, since) == 10_000.0
    # every fill old: the whole net is the block; `since` None reads all as old
    assert mi.pre_existing_block(11_000.0, fills, M, N, NOW + 1) == 11_000.0
    assert mi.pre_existing_block(11_000.0, fills, M, N, None) == 11_000.0
    # every fill inside the window: nothing pre-existing
    assert mi.pre_existing_block(11_000.0, fills, M, N, NOW - 10_000) == 0.0
    # clamped to the axis: a net under the recent flow reads a block of 0, never negative
    recent = [_fill(M, "BUY", 1_000, 0.5, NOW - 5), _fill(M, "SELL", 500, 0.5, NOW - 4)]
    assert mi.pre_existing_block(400.0, recent, M, N, since) == 0.0
    # the other token moves the axis the other way; a short's block is negative
    fills_s = [_fill(N, "BUY", 12_000, 0.72, NOW - 9000), _fill(N, "BUY", 1_000, 0.70, NOW - 10)]
    assert mi.pre_existing_block(-13_000.0, fills_s, M, N, since) == -12_000.0
    # BOTH clocks decide (the fold, HIGH-1): a poll row stamped 4 min ago
    # and ingested now is flow (inside LATE_FILL_S of the window's start);
    # a backfilled row stamped 4,000 s ago and ingested now is the block
    late = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000),
            _fill(M, "BUY", 1_000, 0.71, NOW - 240, detected_at=NOW - 5)]
    assert mi.pre_existing_block(11_000.0, late, M, N, since) == 10_000.0
    assert mi.fill_clock(late[1]) == NOW - 5 and mi.fill_clock(late[0]) == NOW - 9000
    old = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000),
           _fill(M, "BUY", 1_000, 0.71, NOW - 4000, detected_at=NOW - 5)]
    assert mi.pre_existing_block(11_000.0, old, M, N, since) == 11_000.0
    assert mi.fill_clock(old[1]) == NOW - 5 and not mi.is_flow(old[1], since)
    # an unclocked fill is old (the block's side); an unreadable net is None
    assert mi.pre_existing_block(500.0, [{"asset": M, "side": "BUY", "size": 500}], M, N, since) == 500.0
    assert mi.fill_clock({"asset": M}) is None and mi.fill_clock("x") is None
    for bad in (None, "11000", True, math.nan, math.inf):
        assert mi.pre_existing_block(bad, fills, M, N, since) is None


def test_e12_flow_net_is_his_net_less_the_block_on_the_blocks_side_of_zero():
    assert mi.flow_net(13_000.0, 12_000.0) == 1_000.0
    assert mi.flow_net(11_500.0, 12_000.0) == 0.0, "never under the block"
    assert mi.flow_net(13_000.0, 0.0) == 13_000.0 and mi.flow_net(0.0, 12_000.0) == 0.0
    assert mi.flow_net(-500.0, 12_000.0) == -500.0, "a crossing: his whole net (the sign flip's)"
    assert mi.flow_net(-13_000.0, -12_000.0) == -1_000.0 and mi.flow_net(-11_000.0, -12_000.0) == 0.0
    for bad in ((None, 1.0), (1.0, None), ("1", 1.0), (True, 1.0), (math.nan, 1.0), (1.0, math.inf)):
        assert mi.flow_net(*bad) is None, bad
    for name in ("fill_clock", "pre_existing_block", "flow_net", "pre_existing_ratchet", "vwap_of"):
        assert name in mi.__all__


def test_e12_the_ratchet_is_d25s_words_pro_rata_on_reductions_unchanged_on_increases_zero_on_a_crossing():
    """`block_t = block_{t-1} x net_t/net_{t-1}` when net falls,
    unchanged on increases, 0 on a crossing to <= 0."""
    # 12,000 -> 13,000 (he adds): unchanged
    assert mi.pre_existing_ratchet(12_000.0, 12_000.0, 13_000.0) == 12_000.0
    # 13,000 -> 11,000 (he trims 15.4%): 12,000 x 11/13
    b = mi.pre_existing_ratchet(12_000.0, 13_000.0, 11_000.0)
    assert b == round(12_000.0 * 11 / 13, 6) == 10_153.846154
    assert mi.flow_net(11_000.0, b) == round(11_000.0 - b, 6) == 846.153846
    # D25's own number: 12,000 -> 11,000 is a block of 11,000 (8.3% less), not 50%
    assert mi.pre_existing_ratchet(12_000.0, 12_000.0, 11_000.0) == 11_000.0
    # his 25% sale on a block of 10,000 under a net of 11,000: block 7,500
    assert mi.pre_existing_ratchet(10_000.0, 11_000.0, 8_250.0) == 7_500.0
    assert mi.flow_net(8_250.0, 7_500.0) == 750.0, "a 25% reduce of the 1,000 flow"
    # UNCHANGED ON INCREASES after a reduction: the ratcheted block stays
    # (the brief's peak formula would restore the block's share; the doc wins)
    assert mi.pre_existing_ratchet(7_500.0, 8_250.0, 14_000.0) == 7_500.0
    assert mi.flow_net(14_000.0, 7_500.0) == 6_500.0
    # a crossing to <= 0: 0, and it stays 0
    assert mi.pre_existing_ratchet(12_000.0, 12_000.0, 0.0) == 0.0
    assert mi.pre_existing_ratchet(12_000.0, 12_000.0, -5.0) == 0.0
    assert mi.pre_existing_ratchet(0.0, 12_000.0, 13_000.0) == 0.0
    # the short axis: the block is negative and falls toward zero
    assert mi.pre_existing_ratchet(-12_000.0, -13_000.0, -11_000.0) == round(-12_000.0 * 11 / 13, 6)
    assert mi.pre_existing_ratchet(-12_000.0, -12_000.0, -13_000.0) == -12_000.0
    assert mi.pre_existing_ratchet(-12_000.0, -12_000.0, 40.0) == 0.0
    # no reference net: the block itself stands in (the state at open)
    assert mi.pre_existing_ratchet(12_000.0, None, 11_000.0) == 11_000.0
    assert mi.pre_existing_ratchet(12_000.0, None, 13_000.0) == 12_000.0
    # never past the net (a stale reference)
    assert mi.pre_existing_ratchet(12_000.0, 6_000.0, 5_000.0) == 5_000.0
    for bad in ((None, 1.0, 1.0), (1.0, 1.0, None), ("1", 1.0, 1.0), (True, 1.0, 1.0), (math.nan, 1.0, 1.0)):
        assert mi.pre_existing_ratchet(*bad) is None, bad


def test_e12_his_cost_over_the_block_is_the_size_weighted_buy_price_on_the_axis():
    fills = _block_and_add()
    since = NOW - ml.FIRST_SIGHT_S
    assert mi.vwap_of(fills, M, N, before=since) == 0.29, "the block's fills alone"
    assert mi.vwap_of(fills, M, N) == round((10_000 * 0.29 + 1_000 * 0.71) / 11_000, 6)
    # SELLs and the other token's BUYs are not his cost on a long book
    mixed = fills + [_fill(M, "SELL", 500, 0.9, NOW - 8000), _fill(N, "BUY", 500, 0.9, NOW - 8000)]
    assert mi.vwap_of(mixed, M, N, before=since) == 0.29
    # a short book reads his other-token BUYs at 1 - p
    fills_s = [_fill(N, "BUY", 12_000, 0.72, NOW - 9000), _fill(N, "BUY", 1_000, 0.70, NOW - 10)]
    assert mi.vwap_of(fills_s, M, N, short=True, before=since) == 0.28
    assert mi.vwap_of(fills_s, M, N, before=since) is None, "a long book reads no other-token BUY"
    # nothing readable: None
    assert mi.vwap_of([], M, N) is None
    assert mi.vwap_of([{"asset": M, "side": "BUY", "size": "x", "price": 0.3}], M, N) is None
    assert mi.vwap_of([{"asset": M, "side": "BUY", "size": 10, "price": 1.0}], M, N) is None


def test_e12_the_catch_up_verdict_in_order_and_its_persisted_names():
    ok = rules.open_catchup(11_000.0, 0.30, 0.10, 10_000.0, 0.29)
    assert set(ok) == CATCHUP_KEYS
    assert ok == {"vwap": 0.29, "mark": 0.30, "tol": 2.0, "allowed": True, "flow_base": 0.0, "why": "within_tol"}
    assert rules.open_catchup(11_000.0, 0.31, 0.10, 10_000.0, 0.29)["why"] == "within_tol", "exactly 2c is within"
    no = rules.open_catchup(11_000.0, 0.72, 0.10, 10_000.0, 0.29)
    assert (no["allowed"], no["flow_base"], no["why"]) == (False, 10_000.0, "flow_only")
    assert rules.open_catchup(11_000.0, 0.3201, 0.10, 10_000.0, 0.29)["why"] == "flow_only"
    # the exact copy under $10 copies whole, whatever the mark or the tolerance
    sb = rules.open_catchup(12.0, 0.72, 1.0, 10.0, 0.29)
    assert (sb["allowed"], sb["flow_base"], sb["why"]) == (True, 0.0, "small_bet")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rules, "MIRROR_CATCHUP_TOL_CENTS", 0.0)
        assert rules.open_catchup(12.0, 0.72, 1.0, 10.0, 0.29)["why"] == "small_bet"
        tz = rules.open_catchup(11_000.0, 0.29, 0.10, 10_000.0, 0.29)
        assert (tz["allowed"], tz["flow_base"], tz["why"], tz["tol"]) == (False, 10_000.0, "tol_zero", 0.0)
        mp.setattr(rules, "MIRROR_CATCHUP_TOL_CENTS", 1.0)
        assert rules.open_catchup(11_000.0, 0.30, 0.10, 10_000.0, 0.29)["why"] == "within_tol"
        assert rules.open_catchup(11_000.0, 0.3101, 0.10, 10_000.0, 0.29)["why"] == "flow_only"
    # nothing pre-existing: flow is his net
    nb = rules.open_catchup(1_000.0, 0.72, 0.10, 0.0, None)
    assert (nb["allowed"], nb["flow_base"], nb["why"]) == (True, 0.0, "no_block")
    # unreadable, fail closed toward NOT buying
    assert rules.open_catchup(11_000.0, 0.30, 0.10, 10_000.0, None)["why"] == "vwap_unread"
    assert rules.open_catchup(11_000.0, None, 0.10, 10_000.0, 0.29)["why"] == "mark_unread"
    assert rules.open_catchup(11_000.0, 1e-320, 0.10, 10_000.0, 0.29)["why"] == "mark_unread"
    bu = rules.open_catchup(11_000.0, 0.30, 0.10, None, 0.29)
    assert (bu["allowed"], bu["flow_base"], bu["why"]) == (False, 11_000.0, "block_unread")
    assert rules.open_catchup(None, 0.30, 0.10, None, 0.29)["flow_base"] is None
    # a short's block is negative and is handed back as it is
    assert rules.open_catchup(-13_000.0, 0.31, 0.10, -12_000.0, 0.28)["flow_base"] == -12_000.0


# ------------------------------------------------------------------ the open

def test_e12_a_market_he_built_at_29_and_adds_to_at_71_at_first_sight_opens_with_no_catch_up_order(monkeypatch):
    """The finding's shape: 10,000 built at 0.29 hours ago, 1,000 added
    at 0.71 as we look, the mark 0.72. No catch-up: the book opens with
    the block 10,000 stored, the target ratio x 1,000 = 100, and the
    add is mirrored at HIS cent (E4's entry: buy_price(0.71, bid 0.71))
    -- never the 1,100 at 0.71 the old rule sent."""
    p, v, http = _late_world(monkeypatch)
    st = _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["flow_base"], b["flow_last_net"], b["target"], b["ratio"]) == (10_000.0, 11_000.0, 100, 0.10)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][1:5] == (SLUG, 0.71, 100, False) and pl[0][5] == GTC_TIF and pl[0][7] is True
    assert _census(st, "open_flow_only") == 1 and _census(st, "open_catchup") == 0
    assert _census(st, "rest_placed") == 1 and _census(st, "target_zero") == 0
    lp = b["last_plan"]
    assert lp["catchup"] == {"vwap": 0.29, "mark": 0.72, "tol": 2.0, "allowed": False,
                             "flow_base": 10_000.0, "why": "flow_only"}
    assert lp["flow_base"] == 10_000.0 and lp["flow_net"] == 1_000.0 and "flow_ratchet" not in lp
    ins = _sent(p, "INSERT INTO mirror_books")
    # E12b: the INSERT carries the reference's clock (the add's ingest clock, the newest the reference counted)
    assert len(ins) == 1 and "flow_last_net, flow_last_at)" in ins[0][0] and ins[0][1][12:15] == (10_000.0, 11_000.0, NOW - 10)
    rec = [r for r in ml._RECENT if r.get("what") == "opened"][-1]
    assert rec["flow_base"] == 10_000.0 and rec["catchup"] == "flow_only" and rec["target"] == 100
    # the refusal row's context judged the whole net (admission's figure)
    assert not p.cand_refusals
    # the next tick: on target, the block stands, nothing more is bought
    v2 = _Venue(bid=0.71, ask=0.73, held={SLUG: 0})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=http)
    assert b["flow_base"] == 10_000.0 and b["target"] == 100 and len(_places(v2)) == 0
    assert _census(st2, "open_flow_only") == 0


def test_e12_the_same_market_with_the_mark_within_two_cents_of_his_cost_catches_up_in_full(monkeypatch):
    """The mark at 0.30 against his 0.29 over the block: allowed, the
    book opens on his whole net (1,100 at his 0.30 / the 0.29 bid),
    flow_base 0 -- the old figures, now by a verdict the row names."""
    p, v, http = _late_world(monkeypatch, fills=_block_and_add(add_px=0.30), bid=0.29, ask=0.31)
    st = _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (0.0, 11_000.0, 1_100)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][1:5] == (SLUG, 0.29, 1_100, False) and pl[0][5] == GTC_TIF
    assert _census(st, "open_catchup") == 1 and _census(st, "open_flow_only") == 0
    lp = b["last_plan"]
    assert lp["catchup"] == {"vwap": 0.29, "mark": 0.30, "tol": 2.0, "allowed": True,
                             "flow_base": 0.0, "why": "within_tol"}
    assert lp["flow_base"] == 0.0 and lp["flow_net"] == 11_000.0
    ins = _sent(p, "INSERT INTO mirror_books")
    assert ins[0][1][12:14] == (0.0, 11_000.0), "a block of 0 IS stored: the verdict, not NULL"


def test_e12_the_tolerance_lowered_to_zero_never_catches_up(monkeypatch):
    p, v, http = _late_world(monkeypatch, fills=_block_and_add(add_px=0.30), bid=0.29, ask=0.31)
    monkeypatch.setattr(rules, "MIRROR_CATCHUP_TOL_CENTS", 0.0)
    st = _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["flow_base"], b["target"]) == (10_000.0, 100)
    assert _places(v)[0][1:5] == (SLUG, 0.29, 100, False)
    assert _census(st, "open_flow_only") == 1 and _census(st, "open_catchup") == 0
    assert b["last_plan"]["catchup"]["why"] == "tol_zero" and b["last_plan"]["catchup"]["tol"] == 0.0


def test_e12_an_under_ten_dollar_market_is_an_exact_copy_whatever_the_mark_or_the_tolerance(monkeypatch):
    """His 12 sh at 0.72 ($8.64): ratio 1.0, copied whole at his cent --
    the block of 10 at 0.29 included (the premium is cents)."""
    fills = _block_and_add(block=10.0, add=2.0)
    for tol in (2.0, 0.0):
        p, v, http = _late_world(monkeypatch, fills=fills, net=12.0)
        monkeypatch.setattr(rules, "MIRROR_CATCHUP_TOL_CENTS", tol)
        st = _tick(p, v, http=http)
        b = _one_book(p)
        assert (b["ratio"], b["flow_base"], b["target"]) == (1.0, 0.0, 12), tol
        assert _places(v)[0][1:5] == (SLUG, 0.71, 12, False), tol
        assert _census(st, "open_catchup") == 1 and _census(st, "open_flow_only") == 0, tol
        assert b["last_plan"]["catchup"]["why"] == "small_bet" and b["last_plan"]["catchup"]["allowed"] is True


def test_e12_a_market_with_nothing_pre_existing_opens_as_before_and_names_no_block(monkeypatch):
    """Every fill of his inside the window: flow IS his net; the book
    opens on it and neither census name is counted (nothing was
    admitted or refused: `no_block` on the row)."""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 1_000, 0.71, NOW - 20), _fill(M, "BUY", 500, 0.72, NOW - 5)]
    p = _pool(fills=fills, snap={M: 1_500.0, N: 0.0})
    v = _Venue(bid=0.72, ask=0.74)
    st = _tick(p, v, http=_mkt(1_500.0))
    b = _one_book(p)
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (0.0, 1_500.0, 150)
    assert _places(v)[0][1:5] == (SLUG, 0.72, 150, False)
    assert _census(st, "open_catchup") == 0 and _census(st, "open_flow_only") == 0
    assert b["last_plan"]["catchup"]["why"] == "no_block" and b["last_plan"]["catchup"]["vwap"] is None


def test_e12_a_flow_only_open_names_target_zero_only_on_the_whole_net(monkeypatch):
    """Admission judges his whole net: a block with nothing to hold is
    `target_zero` as before; a block worth holding opens the book even
    though the flow target at open is 0 (no order, the book waits)."""
    _rails_2026_09_06(monkeypatch)
    # FILL lane 0a (2026-09-08): the side band is a rail the environment can only lower, so the
    # conftest's widened env no longer reaches the worker; this world's block sits 44c under the
    # mark (E12's subject, not the band's), so the band is widened here, by name
    monkeypatch.setattr(rules, "LIVE_SIDE_PRICE_BAND_MAX", 2.0)
    p = _pool(fills=[_fill(M, "BUY", 10_000, 0.29, NOW - 9000)], snap={M: 10_000.0, N: 0.0})
    v = _Venue(bid=0.71, ask=0.73)
    st = _tick(p, v, http=_mkt(10_000.0))
    b = _one_book(p)
    assert (b["flow_base"], b["target"]) == (10_000.0, 0) and not _places(v)
    assert _census(st, "open_flow_only") == 1 and _census(st, "target_zero") == 0
    lp = b["last_plan"]
    assert lp["flow_wait"] is True and lp["close"] == "not_due" and "flat_since" not in lp
    assert _census(st, "on_target") == 1 and b["state"] == "live"
    # nothing to hold at all: refused by name, no book, as before
    p2 = _pool(fills=[_fill(M, "BUY", 5, 0.29, NOW - 9000)], snap={M: 5.0, N: 0.0})
    monkeypatch.setattr(rules, "MIRROR_SMALL_BET_USD", 0.0)
    st2 = _tick(p2, _Venue(bid=0.71, ask=0.73), http=_mkt(5.0))
    assert not p2.books and _census(st2, "target_zero") == 1


# --------------------------------------------------------------- the ratchet

def test_e12_his_25_percent_sale_after_a_flow_only_open_is_our_25_percent_reduce_at_his_price(monkeypatch):
    """Block 10,000 under a net of 11,000, we hold 100 (his 1,000 add at
    10%). He sells 2,750 (25%) at 0.70: the block ratchets to 7,500
    (10,000 x 8,250 / 11,000), the flow to 750, the target to 75 -- a
    SELL of 25, taken at once with the bid within MIRROR_EXIT_TOL of his
    0.70 (E4's exit), the row carrying the ratchet."""
    _rails_2026_09_06(monkeypatch)
    fills = _block_and_add(add_at=NOW - 3000) + [_fill(M, "SELL", 2_750, 0.70, NOW - 100)]
    p = _pool(fills=fills, snap={M: 8_250.0, N: 0.0})
    b = _flow_book(p)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    st = _tick(p, v, http=_mkt(8_250.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (7_500.0, 8_250.0, 75)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][2:6] == (0.69, 25, True, IOC_TIF)
    assert _census(st, "exit_take") == 1 and _census(st, "filled_take") == 1 and b["ledger_net"] == 75
    lp = b["last_plan"]
    assert lp["flow_ratchet"] == {"from": 10_000.0, "to": 7_500.0}
    assert lp["flow_base"] == 7_500.0 and lp["flow_net"] == 750.0 and lp["kind"] == "reduce"
    assert lp["exit_px"] == 0.70 and lp["exit_px_src"] == "his_fill"
    flow = _sent(p, "ml-book-flow")
    # E12b: the write carries the reference's clock -- the newest ingest clock the reference counted (his SELL's)
    assert len(flow) == 1 and flow[0][1] == (b["id"], 7_500.0, 8_250.0, NOW - 100) and b["flow_last_at"] == NOW - 100
    # the write comes BEFORE the plan's write, as the ratio step's does
    tags = [s for k, s, a in p.sent if "ml-book-flow" in s or "ml-book-plan" in s]
    assert "ml-book-flow" in tags[0] and "ml-book-plan" in tags[-1]
    # outside the cent: the rest at his cent, the reduce held (E4's names)
    p2 = _pool(fills=fills, snap={M: 8_250.0, N: 0.0})
    b2 = _flow_book(p2)
    v2 = _Venue(bid=0.68, ask=0.71, held={SLUG: 100})
    st2 = _tick(p2, v2, http=_mkt(8_250.0))
    assert b2["target"] == 75 and _places(v2)[0][2:6] == (0.70, 25, True, GTC_TIF)
    assert _census(st2, "exit_out_of_tol") == 1 and b2["ledger_net"] == 100


def test_e12_his_full_exit_is_our_full_exit_and_the_block_is_zero(monkeypatch):
    _rails_2026_09_06(monkeypatch)
    fills = _block_and_add(add_at=NOW - 3000) + [_fill(M, "SELL", 11_000, 0.70, NOW - 100)]
    p = _pool(fills=fills, snap={M: 0.0, N: 0.0})
    b = _flow_book(p)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=100.0)
    st = _tick(p, v, http=_gone())
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (0.0, 0.0, 0)
    assert b["last_plan"]["kind"] == "flatten_vanished" and b["last_plan"]["exit_px_src"] == "his_fill"
    assert [c[2:6] for c in _places(v)] == [(0.69, 100, True, IOC_TIF)] and _census(st, "exit_take") == 1
    assert b["ledger_net"] == 0 and b["last_plan"]["flow_ratchet"] == {"from": 10_000.0, "to": 0.0}
    assert b["last_plan"].get("flow_wait") is None, "the block is gone: the flat clock is the close's"


def test_e12_his_add_moves_the_reference_net_and_leaves_the_block_where_it_is(monkeypatch):
    """flow_last_net follows his net every read; the block never rises."""
    _rails_2026_09_06(monkeypatch)
    fills = _block_and_add(add_at=NOW - 3000) + [_fill(M, "BUY", 2_000, 0.75, NOW - 10)]
    p = _pool(fills=fills, snap={M: 13_000.0, N: 0.0})
    b = _flow_book(p)
    v = _Venue(bid=0.75, ask=0.77, held={SLUG: 100})
    st = _tick(p, v, http=_mkt(13_000.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (10_000.0, 13_000.0, 300)
    assert _places(v)[0][1:5] == (SLUG, 0.75, 200, False) and _census(st, "rest_placed") == 1
    lp = b["last_plan"]
    assert "flow_ratchet" not in lp and lp["flow_net"] == 3_000.0
    assert _sent(p, "ml-book-flow")[0][1] == (b["id"], 10_000.0, 13_000.0, NOW - 10)    # E12b: the clock with it
    # nothing moved: nothing written
    p2 = _pool(fills=_block_and_add(add_at=NOW - 3000), snap={M: 11_000.0, N: 0.0})
    b2 = _flow_book(p2)
    _tick(p2, _Venue(bid=0.71, ask=0.73, held={SLUG: 100}), http=_mkt(11_000.0))
    assert b2["target"] == 100 and not _sent(p2, "ml-book-flow")


def test_e12_an_increase_after_a_reduction_keeps_the_ratcheted_block_the_docs_words_not_the_briefs_peak(monkeypatch):
    """Block 7,500 after his 25% sale (net 8,250); he buys 5,750 back
    (net 14,000). D25: unchanged on increases -- block 7,500, flow 6,500,
    target 650. Under the brief's `ratio x flow x his_net / his_peak_net`
    the block's share would be restored; the doc's words win."""
    _rails_2026_09_06(monkeypatch)
    fills = (_block_and_add(add_at=NOW - 3000) + [_fill(M, "SELL", 2_750, 0.70, NOW - 1000),
                                                  _fill(M, "BUY", 5_750, 0.75, NOW - 10)])
    p = _pool(fills=fills, snap={M: 14_000.0, N: 0.0})
    b = _flow_book(p, ledger=75, flow_base=7_500.0, flow_last_net=8_250.0)
    v = _Venue(bid=0.75, ask=0.77, held={SLUG: 75})
    _tick(p, v, http=_mkt(14_000.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (7_500.0, 14_000.0, 650)
    assert _places(v)[0][1:5] == (SLUG, 0.75, 575, False)


def test_e12_a_flow_only_book_waiting_on_his_flow_never_runs_the_flat_clock(monkeypatch):
    """Flat at a flow target of 0 while his block stands: `flow_wait`
    on every plan (the read's and the quiet skip's), no `flat_since`,
    the book live past MIRROR_FLAT_CLOSE_S, never `closed_cancelled`.
    When he leaves, the block is 0 and the ordinary close runs."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 11_000, 0.29, NOW - 9000)], snap={M: 11_000.0, N: 0.0})
    b = _flow_book(p, ledger=0, flow_base=11_000.0, flow_last_net=11_000.0, avg_cost=None)
    closes = 0
    reasons = []
    # the read, then the quiet skips up to the rotation's due tick (E11: QUIET_EVERY_TICKS, 9 --
    # read off the constant, as the E6 pins are), then the read again past MIRROR_FLAT_CLOSE_S
    q = int(ml.QUIET_EVERY_TICKS)
    dts = [30.0 * i for i in range(q)] + [float(rules.MIRROR_FLAT_CLOSE_S) + 100]
    for i, dt in enumerate(dts):
        now = NOW + dt
        p.snap_at = now - 40
        st = _tick(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 0}), now=now, http=_mkt(11_000.0))
        lp = b["last_plan"]
        assert b["state"] == "live" and lp["flow_wait"] is True and "flat_since" not in lp, (i, lp)
        assert lp["close"] == "not_due" and b["target"] == 0, (i, lp)
        closes += _census(st, "closed_cancelled") + _census(st, "closed_cashed_out")
        reasons.append(b["last_reason"])
    assert closes == 0 and st["books_live"] == 1
    # the read (on target), the rotation's quiet skips, the read again on the due tick: every plan waited
    assert len(reasons) == q + 1 and q >= 2
    assert all(r == "book_quiet_skipped" for r in reasons[1:q]), reasons
    assert "book_quiet_skipped" not in (reasons[0], reasons[q]), reasons
    # he leaves: the block is 0, the flat clock starts and the close follows the old rule
    p2 = _pool(fills=[_fill(M, "BUY", 11_000, 0.29, NOW - 9000), _fill(M, "SELL", 11_000, 0.70, NOW - 100)],
               snap={M: 0.0, N: 0.0})
    b2 = _flow_book(p2, ledger=0, flow_base=11_000.0, flow_last_net=11_000.0, avg_cost=None)
    _tick(p2, _Venue(bid=0.69, ask=0.71, held={SLUG: 0}), http=_gone())
    assert b2["flow_base"] == 0.0 and b2["last_plan"].get("flow_wait") is None


def test_e12_the_sign_flip_keeps_its_rule_on_a_flow_book(monkeypatch):
    """His net crosses against the book: the block is 0 (a crossing),
    the flow target is his whole net's sign, and the flip flattens."""
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    fills = _block_and_add(add_at=NOW - 3000) + [_fill(M, "SELL", 11_000, 0.70, NOW - 100),
                                                  _fill(N, "BUY", 500, 0.30, NOW - 90)]
    p = _pool(fills=fills, snap={M: 0.0, N: 500.0})
    b = _flow_book(p)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100})
    st = _tick(p, v, http=_mkt(0.0, 500.0))
    lp = b["last_plan"]
    assert lp["sign_flip"] is True and b["target"] == 0 and b["flow_base"] == 0.0
    assert lp["kind"] == "flatten_paired" and _census(st, "sign_flip") == 1
    # the flip's SELL IOC (filled nothing), then E14b's same-tick rest of the 100 at his cent
    assert [c[2:6] for c in _places(v)] == [(0.69, 100, True, IOC_TIF), (0.70, 100, True, GTC_TIF)]


# ------------------------------------------------ the old rule, byte for byte

def test_e12_an_existing_book_with_flow_base_null_behaves_exactly_as_before():
    """The fixture world's book (his 300 at 0.31, bid 0.30): target 300,
    the rest at 0.30, and nothing of E12 on the plan or the wire."""
    p = _pool()
    b = p.add_book(ledger=0)
    assert b["flow_base"] is None and b["flow_last_net"] is None
    v = _Venue(bid=0.30, ask=0.32)
    st = _tick(p, v)
    assert b["target"] == 300 and _places(v)[0][1:5] == (SLUG, 0.30, 300, False)
    lp = b["last_plan"]
    assert not ({"flow_base", "flow_net", "flow_ratchet", "flow_wait", "catchup"} & set(lp))
    assert not _sent(p, "ml-book-flow") and _census(st, "open_flow_only") == 0 == _census(st, "open_catchup")
    assert b["flow_base"] is None
    # a book whose block was admitted (0 stored) sizes the same and says so
    p2 = _pool()
    b2 = p2.add_book(ledger=0, flow_base=0.0, flow_last_net=300.0)
    v2 = _Venue(bid=0.30, ask=0.32)
    _tick(p2, v2)
    assert b2["target"] == 300 and _places(v2)[0][1:5] == (SLUG, 0.30, 300, False)
    assert b2["last_plan"]["flow_base"] == 0.0 and b2["last_plan"]["flow_net"] == 300.0
    assert b2["last_plan"].get("flow_wait") is None and not _sent(p2, "ml-book-flow")


def test_e12_the_shadow_is_compared_on_the_whole_net_arithmetic_so_a_flow_book_never_trips_it(monkeypatch):
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_block_and_add(add_at=NOW - 3000), snap={M: 11_000.0, N: 0.0})
    b = _flow_book(p)
    p.shadow.append(_shadow_row(1_100, 0.10, 11_000.0))
    st = _tick(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 100}), http=_mkt(11_000.0))
    assert b["target"] == 100 and _census(st, "shadow_live_disagree") == 0 and _census(st, "shadow_check_skipped") == 0
    # a genuine disagreement of the shadow's own arithmetic still trips
    p2 = _pool(fills=_block_and_add(add_at=NOW - 3000), snap={M: 11_000.0, N: 0.0})
    _flow_book(p2)
    p2.shadow.append(_shadow_row(1_050, 0.10, 11_000.0, raw=1_050.0))
    st2 = _tick(p2, _Venue(bid=0.71, ask=0.73, held={SLUG: 100}), http=_mkt(11_000.0))
    assert _census(st2, "shadow_live_disagree") == 1


# ------------------------------------------------- the column, the migration

def test_e12_the_column_absent_is_the_old_rule_on_every_book_said_on_the_heartbeat_and_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(ml, "_flow_absent_logged", False)
    p, v, http = _late_world(monkeypatch)
    p.no_flow_column = True
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, v, http=http)
    assert st["flow_column_absent"] == "UndefinedColumnError" and "flow_guard_unreadable" not in st
    assert _census(st, "flow_guard_unreadable") == 0 and st["status"] == "ok"
    b = _one_book(p)
    assert b["flow_base"] is None and b["target"] == 1_100, "the old rule: his whole net at his newest cent"
    assert _places(v)[0][1:5] == (SLUG, 0.71, 1_100, False)
    assert b["last_plan"]["catchup"]["why"] == "column_absent" and b["last_plan"]["catchup"]["flow_base"] is None
    assert "flow_base" not in b["last_plan"] and _census(st, "open_flow_only") == 0 == _census(st, "open_catchup")
    ins = _sent(p, "INSERT INTO mirror_books")
    assert len(ins) == 1 and "flow" not in ins[0][0] and len(ins[0][1]) == 12
    assert all("flow_base" not in s for k, s, a in p.sent if "ml-flow-guard" not in s), "the 056 statements"
    lines = [r for r in caplog.records if "migration 057" in r.getMessage()]
    assert len(lines) == 1
    # a second tick, and the fast tick, say nothing more; step O reads the
    # book with its open rest through the 056 statement
    st2 = _tick(p, v, now=NOW + 30, http=http)
    assert len([r for r in caplog.records if "migration 057" in r.getMessage()]) == 1
    assert st2["flow_column_absent"] == "UndefinedColumnError" and _census(st2, "book_error") == 0
    _walk()
    fs = _fast(p, v, http=http, now=NOW + 40)
    assert fs["flow_column_absent"] == "UndefinedColumnError" and _skips(fs) == {CID: "order_open"}


def test_e12_the_probe_failing_for_any_other_reason_refuses_the_tick_by_name(monkeypatch):
    p, v, http = _late_world(monkeypatch)
    p.raise_on.append(("ml-flow-guard", RuntimeError("db")))
    st = _tick(p, v, http=http)
    assert _census(st, "flow_guard_unreadable") == 1 and st["status"] == "degraded"
    assert st["flow_guard_unreadable"] == "RuntimeError" and not p.books and not _places(v)
    assert "ml-books-open" not in " ".join(s for k, s, a in p.sent), "refused before any book read"
    _walk()
    ml._MIRROR_CENSUS.clear()
    fs = _fast(p, v, http=http)
    assert _skips(fs) == {CID: "flow_guard_unreadable"} and not p.books and _census(fs, "flow_guard_unreadable") == 1
    # the order of the prelude: the table guard, the intent guard, then this one
    src = inspect.getsource(ml._tick)
    assert src.index("_SQL_TABLE_GUARD") < src.index("_SQL_INTENT_GUARD") < src.index("_flow_guard(t, stats)")
    fsrc = inspect.getsource(ml._fast_tick)
    assert fsrc.index("_SQL_INTENT_GUARD") < fsrc.index("_flow_guard(t, stats)") < fsrc.index("_sql_books_open(t)")


def test_e12_the_fast_tick_opens_the_woken_market_under_the_same_verdict(monkeypatch):
    p, v, http = _late_world(monkeypatch)
    _walk()
    fs = _fast(p, v, http=http)
    b = _one_book(p)
    assert (b["flow_base"], b["target"]) == (10_000.0, 100) and _places(v)[0][1:5] == (SLUG, 0.71, 100, False)
    assert _census(fs, "open_flow_only") == 1 and _census(fs, "fast_tick_placed") == 1 and _skips(fs) == {}


def test_e12_the_open_refuses_a_block_against_the_leg_or_without_its_net_and_sends_the_056_insert_without_one():
    p = _pool()
    kw = dict(whale="rn1", cid=CID, slug=SLUG, long_asset=M, other_asset=N, ratio=0.10,
              anchor_usd=50.0, his_level=0.71, target=100, map_source="ledger", game_key="g")
    r = _run(le._open_mirror_book(p, **kw, flow_base=-1.0, flow_last_net=11_000.0))
    assert r["refusal"] == "open_failed:ValueError" and not p.books
    r = _run(le._open_mirror_book(p, **kw, flow_base=10_000.0))
    assert r["refusal"] == "open_failed:ValueError" and not p.books
    r = _run(le._open_mirror_book(p, **kw, flow_base=math.nan, flow_last_net=1.0))
    assert r["refusal"] == "open_failed:ValueError" and not p.books
    r = _run(le._open_mirror_book(p, **{**kw, "target": -10}, intent=rules.ORDER_INTENT_SHORT,
                                  flow_base=5.0, flow_last_net=-10.0))
    assert r["refusal"] == "open_failed:ValueError", "a short's block is negative"
    r = _run(le._open_mirror_book(p, **kw))
    assert r["ok"] and p.books[r["book_id"]]["flow_base"] is None
    ins = _sent(p, "INSERT INTO mirror_books")
    assert len(ins) == 1 and len(ins[0][1]) == 12 and "flow" not in ins[0][0]
    assert "$13, $14" in le._MIRROR_BOOK_INSERT_FLOW_SQL and "flow_base, flow_last_net)" in le._MIRROR_BOOK_INSERT_FLOW_SQL
    assert "flow" not in le._MIRROR_BOOK_INSERT_SQL


def test_e12_057_exists_sorts_after_056_and_is_two_nullable_add_column_if_not_exists_with_no_default():
    assert SQL_057.exists()
    files = [x.name for x in sorted(MIG_DIR.glob("*.sql"))]
    i = files.index("056_mirror_registered_positions.sql")
    assert files[i + 1] == "057_mirror_books_flow.sql" and sum(f.startswith("057_") for f in files) == 1
    assert files[i + 2] == "058_mirror_books_flow_clock.sql"    # E12b
    assert files[i + 3] == "059_mirror_orders_send_record.sql"    # E18 (PNL lane 6)
    assert files[i + 4] == "060_mirror_fill_answers.sql"    # T2 (FILL lane 4): the per-fill record
    assert files[i + 5] == "061_fill_answers_cause_orders_fast.sql" == files[-1]    # FILL lane 9: the record's columns, last
    sql = SQL_057.read_text()
    assert sql.splitlines()[0].startswith("-- 057: MIRROR BOOKS FLOW BASE (E12, 2026-09-08")
    body = "\n".join(ln.split("--", 1)[0] for ln in sql.splitlines())
    stmts = [" ".join(s.split()) for s in body.split(";") if s.strip()]
    assert stmts == [
        "ALTER TABLE mirror_books ADD COLUMN IF NOT EXISTS flow_base DOUBLE PRECISION NULL",
        "ALTER TABLE mirror_books ADD COLUMN IF NOT EXISTS flow_last_net DOUBLE PRECISION NULL",
    ]
    up = " ".join(stmts).upper()
    assert "DEFAULT" not in up and "NOT NULL" not in up and "CREATE " not in up and "DROP " not in up
    assert "mirror_orders" not in " ".join(stmts) and "live_orders" not in " ".join(stmts)
    # applied on boot: the API's start.sh runs the sorted glob before serving
    start = (ROOT / "backend" / "start.sh").read_text()
    assert "python -m sportsassets.scripts.migrate" in start
    assert start.index("scripts.migrate") < start.index("exec uvicorn")
    assert 'sorted(MIGRATIONS_DIR.glob("*.sql"))' in pathlib.Path(migrate.__file__).read_text()
    # the worker's guard and reads name the columns; the 056 shapes do not
    assert "flow_base" in ml._SQL_BOOK_COLS and "flow_last_net" in ml._SQL_BOOK_COLS
    assert "flow" not in ml._SQL_BOOK_COLS_056 and "his_level" not in ml._SQL_BOOK_COLS
    assert ml._SQL_FLOW_GUARD == "SELECT flow_base, flow_last_net FROM mirror_books LIMIT 0 /* ml-flow-guard */"
    for name in ("_SQL_BOOKS_OPEN", "_SQL_BOOK_READ"):
        assert "flow_base" in getattr(ml, name) and "flow_base" not in getattr(ml, name + "_056")
    src = inspect.getsource(ml)
    # E12b: the six read sites go through the pair that reads BOTH probes (056 / 057 / 058 shapes);
    # E23 (FILL lane 23) added a seventh -- the cancel re-read's book read (_cancel_reread), through
    # the same pair (3 -> 4 fetchrow sites)
    assert src.count("t.pool.fetch(_sql_books_open(t))") == 3 and src.count("fetchrow(_sql_book_read(t),") == 4
    assert "t.pool.fetch(_SQL_BOOKS_OPEN)" not in src and "fetchrow(_SQL_BOOK_READ," not in src
    assert not re.findall(r"_SQL_BOOKS_OPEN(?:_057)? if t\.flow_col", src) and "_SQL_BOOK_READ if t.flow_col" not in src
    # the mirror_orders CHECK stands: no new order kind
    sql_047 = (MIG_DIR / "047_mirror_live.sql").read_text()
    assert "kind IN ('increase','reduce','flatten_paired','flatten_vanished','take','adjust')" in sql_047
    assert not re.search(r"\bkind\b", sql)


def test_e12_057_parses_as_postgres_sql():
    pglast = pytest.importorskip("pglast")
    from pglast.enums import AlterTableType, ConstrType
    stmts = pglast.parse_sql(SQL_057.read_text())
    assert len(stmts) == 2 and all(type(s.stmt).__name__ == "AlterTableStmt" for s in stmts)
    seen = []
    for s in stmts:
        n = s.stmt
        assert n.relation.relname == "mirror_books" and len(n.cmds) == 1
        cmd = n.cmds[0]
        assert cmd.subtype == AlterTableType.AT_AddColumn and cmd.missing_ok is True
        col = cmd.def_
        types = {c.contype for c in (col.constraints or [])}
        assert ConstrType.CONSTR_NULL in types and ConstrType.CONSTR_NOTNULL not in types
        assert ConstrType.CONSTR_DEFAULT not in types
        seen.append((col.colname, [x.sval for x in col.typeName.names][-1]))
    assert seen == [("flow_base", "float8"), ("flow_last_net", "float8")]


async def _scratch():
    asyncpg = pytest.importorskip("asyncpg")
    try:
        admin = await asyncpg.connect(DSN_BASE, timeout=4)
    except Exception:  # noqa: BLE001 — no local PG: skip, never fake
        pytest.skip("no local postgres for the E12 migration pin")
    name = "e12_flow_" + uuid.uuid4().hex[:10]
    await admin.execute(f'CREATE DATABASE "{name}"')
    conn = await asyncpg.connect(DSN_BASE.rsplit("/", 1)[0] + "/" + name, timeout=4)
    return admin, conn, name


async def _drop(admin, conn, name):
    await conn.close()
    await admin.execute(f'DROP DATABASE "{name}"')
    await admin.close()


def test_e12_057_applies_on_a_real_postgres_twice_and_the_worker_statements_run_against_it():
    """The migration applied on boot and idempotent: 047's table (with a
    live_orders stub for its FK), then 057 twice; the columns nullable
    double precision with no default; the guard's error before 057 is
    the absence rules.column_missing reads; after it the flow INSERT,
    the 057 read, the ratchet's UPDATE and the 056 read all run."""
    async def _go():
        admin, conn, name = await _scratch()
        try:
            # the stub 047's FK and its partial index on live_orders need
            await conn.execute("CREATE TABLE live_orders (id bigserial PRIMARY KEY, us_market_slug text, lane text)")
            await conn.execute((MIG_DIR / "047_mirror_live.sql").read_text())
            try:
                await conn.fetch(ml._SQL_FLOW_GUARD)
            except Exception as exc:  # noqa: BLE001 — the absence, as Postgres answers it
                assert rules.column_missing(exc, "flow_base"), exc
                assert type(exc).__name__ == "UndefinedColumnError"
            else:
                raise AssertionError("the guard read a column 047 never declared")
            await conn.execute(SQL_057.read_text())
            await conn.execute(SQL_057.read_text())          # idempotent
            cols = await conn.fetch(
                "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'mirror_books' AND column_name IN ('flow_base', 'flow_last_net') "
                "ORDER BY column_name")
            assert [tuple(c) for c in cols] == [("flow_base", "double precision", "YES", None),
                                                ("flow_last_net", "double precision", "YES", None)]
            assert await conn.fetch(ml._SQL_FLOW_GUARD) == []
            row = await conn.fetchrow(le._MIRROR_BOOK_INSERT_FLOW_SQL, "rn1", CID, SLUG, "g", M, N,
                                      rules.ORDER_INTENT, "ledger", 0.10, 50.0, 0.71, 100, 10_000.0, 11_000.0)
            old = await conn.fetchrow(le._MIRROR_BOOK_INSERT_SQL, "rn1", CID, SLUG + "-2", "g", M, N,
                                      rules.ORDER_INTENT, "ledger", 0.10, 50.0, 0.71, 100)
            # the 057 shapes (E12b's own pin runs 058 and the clock shapes)
            got = await conn.fetchrow(ml._SQL_BOOK_READ_057, row["id"])
            assert (got["flow_base"], got["flow_last_net"]) == (10_000.0, 11_000.0)
            got_old = await conn.fetchrow(ml._SQL_BOOK_READ_057, old["id"])
            assert got_old["flow_base"] is None and got_old["flow_last_net"] is None, "NULL = the old rule"
            await conn.execute(ml._SQL_BOOK_FLOW, row["id"], 7_500.0, 8_250.0)
            got = await conn.fetchrow(ml._SQL_BOOK_READ_057, row["id"])
            assert (got["flow_base"], got["flow_last_net"]) == (7_500.0, 8_250.0)
            opened = await conn.fetch(ml._SQL_BOOKS_OPEN_057)
            assert {r["id"] for r in opened} == {row["id"], old["id"]}
            assert {r["flow_base"] for r in opened} == {7_500.0, None}
            older = await conn.fetch(ml._SQL_BOOKS_OPEN_056)
            assert len(older) == 2 and "flow_base" not in older[0].keys()
            assert (await conn.fetchrow(ml._SQL_BOOK_READ_056, row["id"]))["id"] == row["id"]
        finally:
            await _drop(admin, conn, name)
    _run(_go())


# ------------------------------------------------------ the names, the docs

def test_e12_the_census_names_sit_past_the_served_prefix_before_e9s_four_and_integ_is_untouched():
    keys = ml.CENSUS_KEYS
    new = ("open_flow_only", "open_catchup", "flow_guard_unreadable")
    assert keys[-11:-8] == new, "before E9's four (the E9 pin holds keys[-8:-4]; the brief's 'before E7's pair' yields to it)"
    assert keys[-8:-4] == ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")
    assert keys[-4:] == ("cand_no_mark_skipped", "cand_memo_released", "book_quiet_skipped", "cand_terminal_skipped")
    assert keys[-12] == "registered_no_increase" and len(set(keys)) == len(keys)
    for k in new:
        assert keys.index(k) >= api_app._DETAIL_MAX_KEYS and ml._new_stats()["census"][k] == 0
        assert k not in ml._INTEG_CENSUS_KEYS
    assert len(ml._INTEG_CENSUS_KEYS) + len(ml._INTEG_STAT_KEYS) < api_app._DETAIL_MAX_KEYS
    assert len(ml._new_stats()) <= api_app._DETAIL_MAX_KEYS
    st = ml._new_stats()
    served = api_app._sanitize_detail(st)
    assert "_truncated_keys" not in served and len(st["integ"]) < api_app._DETAIL_MAX_KEYS
    # the persisted names, by source: the row, the plan, the recent list
    src = inspect.getsource(ml)
    for s in ('plan["catchup"] = cu', 'plan.update(flow_base=float(fb), flow_net=flow)',
              'plan["flow_ratchet"] = {"from": was, "to": float(fb)}', 'plan["flow_wait"] = True',
              "flow_base=cu.get(\"flow_base\"), catchup=cu.get(\"why\")"):
        assert s in src, s


def test_e12_the_ratchet_runs_before_the_target_and_is_written_before_the_book_moves():
    src = inspect.getsource(ml._tick_book)
    i_step = src.index("rules.step_ratio(book.get(\"ratio\")")
    i_ratchet = src.index("mi.pre_existing_ratchet(")
    i_write = src.index("_SQL_BOOK_FLOW")
    i_move = src.index('book["flow_base"], book["flow_last_net"] = float(fb), float(fills_net)')
    i_target = src.index("tg = rules.mirror_target(book.get(\"ratio\"), net_sized")
    i_shadow = src.index("_shadow_check(t, book, arith, net")
    assert i_step < i_ratchet < i_write < i_move < i_target < i_shadow
    assert src.count("net_sized, r.mark, MIRROR_ANCHOR_CLIP_USD") == 2, "the target and the room's target"
    assert "whole = rules.mirror_target(book.get(\"ratio\"), net, r.mark" in src
    # a failed write ratchets nothing in memory: the book is its own error
    p = _pool(fills=_block_and_add(add_at=NOW - 3000) + [_fill(M, "SELL", 2_750, 0.70, NOW - 100)],
              snap={M: 8_250.0, N: 0.0})
    b = _flow_book(p)
    p.raise_on.append(("ml-book-flow", RuntimeError("db")))
    st = _tick(p, _Venue(bid=0.69, ask=0.71, held={SLUG: 100}), http=_mkt(8_250.0))
    assert _census(st, "book_error") == 1 and b["flow_base"] == 10_000.0 and b["target"] is None
    # the open: the whole-net target is admission's, the flow target the book's
    csrc = inspect.getsource(ml._tick_candidate)
    assert csrc.index('_mirror_stop("target_zero", w)') < csrc.index("_open_flow(t, fills") < csrc.index("le._open_mirror_book(")
    assert csrc.index("le._open_mirror_book(") < csrc.index('_mirror_stop("open_flow_only", w)')


def test_e12_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert "## 33. E12" in doc
    for s in ("flow_base", "flow_last_net", "MIRROR_CATCHUP_TOL_CENTS", "open_flow_only", "open_catchup",
              "flow_guard_unreadable", "block_t = block_{t-1}", "057"):
        assert s in doc, s
    program = (ROOT / "docs" / "mirror-to-a-tee-program.md").read_text()
    assert "(B) never" in program and "block_t = block_{t-1} × net_t/net_{t-1}" in program


def test_e12_every_name_is_emitted_here(monkeypatch, caplog):
    """The three names, for the worker file's every-name census."""
    ml._MIRROR_CENSUS.clear()
    p, v, http = _late_world(monkeypatch)
    st = _tick(p, v, http=http)
    assert _census(st, "open_flow_only") == 1
    p2, v2, http2 = _late_world(monkeypatch, fills=_block_and_add(add_px=0.30), bid=0.29, ask=0.31)
    st2 = _tick(p2, v2, http=http2)
    assert _census(st2, "open_catchup") == 1
    p3, v3, http3 = _late_world(monkeypatch)
    p3.raise_on.append(("ml-flow-guard", RuntimeError("db")))
    st3 = _tick(p3, v3, http=http3)
    assert _census(st3, "flow_guard_unreadable") == 1
    assert json.dumps(st3, default=str)
