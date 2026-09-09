"""FILL lane 3 (2026-09-08): the exit take NAMED -- exit_take_in_band /
cover_in_band at a code default of 0.01 (INERT) -- plus the fast gate's
order_open_his_exit count.

Owner decision D2: "exits stay within 1c" -- so this lane lands the exit
band's machinery with the band equal to the tolerance, where NOTHING
changes: rules.exit_terms' `take_band` == `take` and `cover_band` ==
`cover`, the tolerance branch of _act fires first, and no row is ever
written 'exit_take_in_band' / 'cover_in_band'. The second cent, if the
owner grants it, is then ONE reviewed constant change
(rules.MIRROR_EXIT_TAKE_BAND, capped_env floor 0.0: the environment may
only LOWER it; at or under the tolerance it is inert).

THE RULE AT A WIDER BAND (pinned at a monkeypatched 0.02, never the
default): on a long book's exit the bid past the take cent but at or
through the band cent (the lowest cent at or above his price less
max(tol, band)) sends ONE IOC at the band cent through lane 1's
_exit_take -- its withheld or unfilled quantity rests at his cent the
same tick, exactly as the tolerance IOC's does -- the row's decision
'exit_take_in_band', census exit_take_in_band, the plan's `exit_band`
{bid, ask, his, cents_off_his, at}; a short book's cover the mirror
image at `cover_band` through the cover's own IOC (S4's path, a partial
rests nothing this tick), decision 'cover_in_band', census
cover_in_band. Outside the band the exit is held as before,
`exit_out_of_tol` now carrying the band bound beside the floor / the
ceiling. THE FAST GATE: a woken REDUCING fill of his on a book with an
ENTRY rest standing is named `order_open_his_exit` -- a
fast_tick_skipped reason and a COUNT, never a cancel.

The rows (hard2/post_exits_1707.txt, the exits-paired preset at
17:07Z): book 334 cfb-smu-flst spread, his exit 0.549, our 358, filled
0.50 at a lag of 279 s (row 335); book 419 itf-olivar-hashimo, his
0.180, our 152, filled 0.09 at a lag of 3 s (row 336); book 285
lal-elc-rso total, his 0.840 in the preset, our exit average 0.718,
filled 0.05 (row 334); book 467 atp-serna-jianu, his 0.250, our 570,
filled 0.43 (row 333); book 347 wta-pareja-stefani SHORT 2,477 sh,
his exit 0.514, our cover placed and unfilled (row 338; book_347_1750
398: venue_ledger_disagree). Book 661 (closerows_1750 529, order 3632):
a SHORT book's flatten_paired BUY_LONG 609 @0.620 filled 89.24 then
cancelled `replace`. Book 611 (hourly_1737 865): LONG live, an ADD leg.

Driven against the worker file's fakes (its autouse rails are
imported), test_e14b's exit worlds, test_e18's moving venue, test_e9's
fast tick and test_e16's frozen fixtures.
"""
import importlib
import inspect
import pathlib
import re
import types

import pytest

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_e14_take_band import _UndefinedColumn
from tests.test_e14b_exit_take_rests import _exit_world, _opens
from tests.test_e16_freeze_two_reads import _pool as _e16_pool
from tests.test_e16_freeze_two_reads import _plan_exit, _thaw_off, _unread
from tests.test_e18_rest_life import _MovingVenue, _bbos, _inserts
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    BUY, CID, M, N, NOW, SELL, SHORT, SLUG, _NoClose, _Venue, _armed, _cancels, _census, _fill,
    _flip_world, _kinds, _mkt, _places, _pool, _run, _short_book, _shorts_on, _tick,
)

GTC_TIF = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
IOC_TIF = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
ROOT = pathlib.Path(__file__).resolve().parents[2]
NEW_NAMES = ("exit_take_in_band", "cover_in_band", "order_open_his_exit")
BAND_WORDS = ("exit_take_in_band", "cover_in_band")


@pytest.fixture(autouse=True)
def _band_default(_armed, monkeypatch):
    """The exit band at its CODE DEFAULT (0.01, inert) for every test
    here, pinned against the runner's environment; the tests of the
    band itself set 0.02 by name (`_band_two`)."""
    monkeypatch.setattr(rules, "MIRROR_EXIT_TAKE_BAND", 0.01)
    yield


def _band_two(monkeypatch):
    monkeypatch.setattr(rules, "MIRROR_EXIT_TAKE_BAND", 0.02)


def _decisions(p):
    return [a[20] for a in _inserts(p)]


def _no_band_word(p, st, b=None):
    """The inert pin: no band word on any row, no band count, and the
    plan's band cents equal E4's."""
    assert not any(d in BAND_WORDS for d in _decisions(p)), _decisions(p)
    assert _census(st, "exit_take_in_band") == 0 and _census(st, "cover_in_band") == 0
    if b is not None:
        lp = b["last_plan"]
        assert "exit_band" not in lp
        if "exit_take" in lp:
            assert lp["exit_take_band"] == lp["exit_take"] and lp["exit_band_floor"] == lp["exit_floor"]
        if "exit_cover" in lp:
            assert lp["exit_cover_band"] == lp["exit_cover"] and lp["exit_band_ceiling"] == lp["exit_ceiling"]


# ------------------------------------------------------------- (1) the rule

def test_x1_exit_terms_reads_a_band_at_call_time_and_is_inert_at_or_under_the_tolerance():
    assert rules.MIRROR_EXIT_TOL == 0.01 and rules.MIRROR_EXIT_TAKE_BAND == 0.01
    ex = rules.exit_terms(SELL, 0.549, tol=0.01, band=0.02)
    assert (ex["rest"], ex["take"], ex["take_band"]) == (0.55, 0.54, 0.53)
    assert ex["floor"] == pytest.approx(0.539) and ex["band_floor"] == pytest.approx(0.529)
    # band 0.0 / 0.01 / unreadable / negative / a bool: take_band == take (inert)
    for band in (0.0, 0.01, 0.005, "abc", float("nan"), -0.01, True, False, None):
        e = rules.exit_terms(SELL, 0.549, tol=0.01, band=band)
        assert e["take_band"] == e["take"] == 0.54 and e["band_floor"] == pytest.approx(0.539), band
        c = rules.exit_terms(BUY, 0.514, tol=0.01, band=band)
        assert c["cover_band"] == c["cover"] == 0.52 and c["band_ceiling"] == pytest.approx(0.524), band
    # a BUY (the cover) at his 0.514: cover 0.52, cover_band 0.53, rest 0.51
    cv = rules.exit_terms(BUY, 0.514, tol=0.01, band=0.02)
    assert (cv["cover"], cv["cover_band"], cv["rest"], cv["take"]) == (0.52, 0.53, 0.51, 0.52)
    assert cv["band_ceiling"] == pytest.approx(0.534)
    # E4's own cents are untouched whatever the band reads
    for band in (0.0, 0.01, 0.02, 0.05):
        e = rules.exit_terms(SELL, 0.549, band=band)
        assert (e["px"], e["floor"], e["rest"], e["take"]) == (0.549, pytest.approx(0.539), 0.55, 0.54), band
    # the module constant, read AT CALL TIME with none given
    assert rules.exit_terms(SELL, 0.549)["take_band"] == 0.54
    assert rules.exit_terms(SELL, 0.549, band=None) == rules.exit_terms(SELL, 0.549)
    assert inspect.signature(rules.exit_terms).parameters["band"].default is None
    # E4 LOW-6 kept: a tolerance the environment LOWERED tightens the take; the band at its
    # default never stands over it (the plan's bare max(tol, band) would have)
    assert rules.EXIT_BAND_INERT_AT == 0.01
    e0 = rules.exit_terms(SELL, 0.31, tol=0.0)
    assert (e0["take"], e0["take_band"], e0["band_floor"]) == (0.31, 0.31, 0.31)
    c0 = rules.exit_terms(BUY, 0.31, tol=0.0)
    assert (c0["cover"], c0["cover_band"], c0["band_ceiling"]) == (0.31, 0.31, 0.31)
    assert rules.exit_terms(SELL, 0.31, tol=0.0, band=0.02)["take_band"] == 0.29, "a band raised in code widens"


def test_x1_the_sweep_keeps_every_band_take_inside_max_tol_band_of_his_at_any_precision():
    for tol in (0.0, 0.005, 0.01, 0.02):
        for band in (0.0, 0.005, 0.01, 0.02, 0.03):
            # the band is inert at or under the tolerance's own default (0.01); above it max(tol, band)
            b = max(tol, band) if band > rules.EXIT_BAND_INERT_AT else tol
            for his_c in range(2, 100):
                for frac in (0.0, 0.0025, 0.005, 0.0095):
                    his = his_c / 100.0 + frac
                    if his >= 1.0:
                        continue
                    ex = rules.exit_terms(SELL, his, tol=tol, band=band)
                    assert ex is not None, (his, tol, band)
                    # the band take is never under the band floor, never over the take, never
                    # more than max(tol, band) under him at any precision
                    # (the ladder's top: E4's own take is capped at 0.99, and so is the band's)
                    assert ex["take_band"] >= min(ex["band_floor"], 0.99) - 1e-9 and ex["take_band"] <= ex["take"] + 1e-9, (his, tol, band)
                    assert ex["band_floor"] == pytest.approx(his - b), (his, tol, band)
                    assert his - ex["take_band"] <= b + 1e-9 or ex["take_band"] == ex["take"] == 0.99, (his, tol, band)
                    # (the ladder's bottom: a band floor under 0.01 is floored at 0.01, as E4's own floor is)
                    assert ex["take_band"] - max(ex["band_floor"], 0.01) < 0.01 + 1e-9, (his, tol, band)
                    if b <= tol:
                        assert ex["take_band"] == ex["take"], (his, tol, band)
                    cv = rules.exit_terms(BUY, his, tol=tol, band=band)
                    assert cv is not None, (his, tol, band)
                    assert cv["cover_band"] <= cv["band_ceiling"] + 1e-9 and cv["cover_band"] >= cv["cover"] - 1e-9, (his, tol, band)
                    assert cv["cover_band"] - his <= b + 1e-9 and cv["cover_band"] <= 0.99, (his, tol, band)
                    if b <= tol:
                        assert cv["cover_band"] == cv["cover"], (his, tol, band)
                    # the band take fires only with the bid at or over the band cent
                    assert rules.at_or_through(SELL, ex["take_band"], 0.99, ex["take_band"]) is True
                    assert rules.at_or_through(SELL, round(ex["take_band"] - 0.01, 2), 0.99, ex["take_band"]) is False


def test_x1_the_band_constant_only_lowers_from_the_environment(monkeypatch):
    src = inspect.getsource(rules)
    assert 'MIRROR_EXIT_TAKE_BAND = capped_env("MIRROR_EXIT_TAKE_BAND", 0.01, floor=0.0)' in src
    assert src.count('capped_env("MIRROR_EXIT_TAKE_BAND"') == 1 and 'min_wait_env("MIRROR_EXIT_TAKE' not in src
    assert '_env_float("MIRROR_EXIT_TAKE' not in src and "MIRROR_EXIT_TAKE_BAND" in rules.__all__
    # the tolerance is the rail it was
    assert 'MIRROR_EXIT_TOL = capped_env("MIRROR_EXIT_TOL", 0.01, floor=0.0)' in src
    try:
        for raw, want in (("0.05", 0.01), ("0.02", 0.01), ("0.005", 0.005), ("0", 0.0), ("0.01", 0.01),
                          ("abc", 0.01), ("inf", 0.01), ("", 0.01), ("-1", 0.0), ("1e400", 0.01)):
            monkeypatch.setenv("MIRROR_EXIT_TAKE_BAND", raw)
            mod = importlib.reload(rules)
            assert mod.MIRROR_EXIT_TAKE_BAND == want, (raw, mod.MIRROR_EXIT_TAKE_BAND)
            # at every value the environment can set, the band cent IS the take cent
            e = mod.exit_terms(mod.SELL, 0.549)
            assert e["take_band"] == e["take"] == 0.54, raw
            c = mod.exit_terms(mod.BUY, 0.514)
            assert c["cover_band"] == c["cover"] == 0.52, raw
    finally:
        monkeypatch.delenv("MIRROR_EXIT_TAKE_BAND", raising=False)
        importlib.reload(rules)


# --------------------------------------------------------- (2) the decision

def test_x1_order_decision_writes_the_two_band_words_on_an_exit_ioc_alone():
    assert rules.order_decision("reduce", True, False, True) == "exit_take_in_band"
    assert rules.order_decision("reduce", True, True, True) == "cover_in_band"
    assert rules.order_decision("add", True, True, True) == "cover_in_band", "a cover wins over the entry's word"
    assert rules.order_decision("add", True, False, True) == "take_in_band", "the entry's word stays lane 2's"
    # not an IOC: never a band word
    assert rules.order_decision("reduce", False, False, True) == "exit_rest"
    assert rules.order_decision("reduce", False, True, True) == "cover"
    assert rules.order_decision("add", False, False, True) == "rest"
    # an unreadable action, a truthy non-bool: the word the row carried before the band
    assert rules.order_decision(None, True, False, True) == "take"
    assert rules.order_decision("reduce", True, False, "yes") == "take"
    assert rules.order_decision("reduce", True, True, 1) == "cover"
    # E18's defaults, byte for byte
    assert rules.order_decision("add", False, False) == "rest" and rules.order_decision("add", True, False) == "take"
    assert rules.order_decision("reduce", False, False) == "exit_rest" and rules.order_decision("reduce", True, False) == "take"
    assert rules.order_decision("reduce", False, True) == rules.order_decision("reduce", True, True) == "cover"
    body = inspect.getsource(rules.order_decision).split('"""')[2]
    assert body.count('"exit_take_in_band"') == 1 and body.count('"cover_in_band"') == 1 and body.count('"take_in_band"') == 1
    # the words are rules.order_decision's alone: the worker never spells them into a row
    wsrc = inspect.getsource(ml._place_reserved)
    for w in BAND_WORDS:
        assert f'"{w}"' not in wsrc, w


# ---------------------------------------- (0) AT THE DEFAULT NOTHING CHANGES

def test_x1_at_the_default_the_e4_shapes_place_decide_and_count_exactly_as_before_the_lane(monkeypatch):
    """Books 334 / 419 / 285 (post_exits_1707 335 / 336 / 334) and the S4
    cover at the default band: the placements, the decisions and the
    census are the pre-lane expectations byte for byte (test_e14b's and
    test_mirror_live_worker's), and no 'exit_take_in_band' /
    'cover_in_band' row is ever written."""
    assert rules.MIRROR_EXIT_TAKE_BAND == 0.01
    # book 334: the tick's bid 0.54, the re-read 0.53 -> bid_moved -> the 358 rest at 0.55 (E14b)
    p, b = _exit_world(0.549, 358)
    v = _MovingVenue([(0.54, 0.56), (0.53, 0.56)], held={SLUG: 716}, ioc_fill=358.0)
    st = _tick(p, v, http=_mkt(358.0))
    assert [c[2:6] for c in _places(v)] == [(0.55, 358, True, GTC_TIF)] and _decisions(p) == ["exit_rest"]
    assert _census(st, "bid_moved") == 1 and _census(st, "exit_take_rested") == 1 and _census(st, "exit_take") == 0
    assert b["last_plan"]["exit_take_rested"] == {"take": 0.54, "rest": 0.55, "qty": 358, "filled": 0.0, "rested": 358}
    _no_band_word(p, st, b)
    # book 334 with the bid a cent further (0.53): HELD, the rest at 0.55, the band bound recorded = the floor
    p, b = _exit_world(0.549, 358)
    v = _Venue(bid=0.53, ask=0.56, held={SLUG: 716}, ioc_fill=358.0)
    st = _tick(p, v, http=_mkt(358.0))
    assert [c[2:6] for c in _places(v)] == [(0.55, 358, True, GTC_TIF)] and _decisions(p) == ["exit_rest"]
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "exit_take") == 0 and _bbos(v).count(SLUG) == 1
    assert b["last_plan"]["exit_out_of_tol"] == {"bid": 0.53, "ask": 0.56, "floor": 0.539, "band_floor": 0.539, "at": NOW}
    assert (b["last_plan"]["exit_take_band"], b["last_plan"]["exit_band_floor"]) == (0.54, 0.539)
    _no_band_word(p, st, b)
    # book 419: the bid 0.16 under the floor 0.17 -> no IOC, the rest at 0.18
    p, b = _exit_world(0.18, 152, entry=0.20)
    v = _Venue(bid=0.16, ask=0.19, held={SLUG: 304})
    st = _tick(p, v, http=_mkt(152.0))
    assert [c[2:6] for c in _places(v)] == [(0.18, 152, True, GTC_TIF)] and _decisions(p) == ["exit_rest"]
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "exit_take") == 0 and b["ledger_net"] == 304
    assert b["last_plan"]["exit_out_of_tol"] == {"bid": 0.16, "ask": 0.19, "floor": 0.17, "band_floor": 0.17, "at": NOW}
    _no_band_word(p, st, b)
    # book 285: bids 0.70 / 0.71 / 0.82 -> the rest at 0.84; 0.83 -> the one IOC at 0.83 (decision 'take')
    for bid in (0.70, 0.71, 0.82):
        p, b = _exit_world(0.84, 432, entry=0.80)
        v = _Venue(bid=bid, ask=0.86, held={SLUG: 864}, ioc_fill=432.0)
        st = _tick(p, v, http=_mkt(432.0))
        assert [c[2:6] for c in _places(v)] == [(0.84, 432, True, GTC_TIF)] and _decisions(p) == ["exit_rest"], bid
        assert _census(st, "exit_take") == 0 and _census(st, "exit_out_of_tol") == 1, bid
        _no_band_word(p, st, b)
    p, b = _exit_world(0.84, 432, entry=0.80)
    v = _Venue(bid=0.83, ask=0.86, held={SLUG: 864}, ioc_fill=432.0)
    st = _tick(p, v, http=_mkt(432.0))
    assert [c[2:6] for c in _places(v)] == [(0.83, 432, True, IOC_TIF)] and _decisions(p) == ["take"]
    assert _census(st, "exit_take") == 1 and b["ledger_net"] == 432
    _no_band_word(p, st, b)
    # the S4 cover: his 0.31, the ask at the ceiling 0.32 -> the one IOC, decision 'cover'; a partial rests nothing
    _shorts_on(monkeypatch)
    p, b, v = _flip_world(ioc_fill=100.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.32, 300, True, IOC_TIF)] and _decisions(p) == ["cover"]
    assert _census(st, "short_cover_take") == 1 and b["ledger_net"] == -200 and not _opens(p)
    _no_band_word(p, st, b)
    # the S4 cover held outside the ceiling: the rest at floor(his), the band bound = the ceiling
    p, b, v = _flip_world(ask=0.33)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.31, 300, True, GTC_TIF)] and _decisions(p) == ["cover"]
    assert _census(st, "short_cover_out_of_tol") == 1
    assert b["last_plan"]["exit_out_of_tol"] == {"bid": 0.30, "ask": 0.33, "ceiling": 0.32, "band_ceiling": 0.32, "at": NOW}
    _no_band_word(p, st, b)


# ------------------------------------------------------ (3) book 334's shape

def test_x1_book_334_at_band_two_the_standing_rest_is_cancelled_and_one_ioc_goes_at_the_band_cent(monkeypatch):
    """His 0.549: rest 0.55, take 0.54, take_band 0.53. A rest at 0.55
    standing, the bid at 0.53: the rest cancelled under 'take', ONE IOC
    at 0.53 for 358, decision exit_take_in_band, census
    exit_take_in_band, the plan's exit_band.cents_off_his 1.9."""
    _band_two(monkeypatch)
    p, b = _exit_world(0.549, 358)
    o = p.add_order(b, side=SELL, wire=0.55, qty=358, kind="reduce", placed_ts=NOW - 100)
    v = _Venue(bid=0.53, ask=0.56, held={SLUG: 716}, ioc_fill=358.0)
    v.rest("oid-1", "SELL", 0.55, 358)
    st = _tick(p, v, http=_mkt(358.0))
    lp = b["last_plan"]
    assert (lp["exit_take"], lp["exit_take_band"], lp["exit_rest"]) == (0.54, 0.53, 0.55)
    assert lp["exit_floor"] == 0.539 and lp["exit_band_floor"] == 0.529
    assert [c[1] for c in _cancels(v)] == ["oid-1"] and p.orders[o["id"]]["reason"] == "take"
    assert [c[2:6] for c in _places(v)] == [(0.53, 358, True, IOC_TIF)]
    assert _decisions(p) == ["exit_take_in_band"] and b["ledger_net"] == 358
    assert _census(st, "exit_take_in_band") == 1 and _census(st, "exit_take") == 1 and _census(st, "take_placed") == 1
    assert _census(st, "exit_out_of_tol") == 0 and _census(st, "cover_in_band") == 0 and _census(st, "exit_take_rested") == 0
    assert lp["exit_band"] == {"bid": 0.53, "ask": 0.56, "his": 0.549, "cents_off_his": 1.9, "at": NOW}
    assert lp["decision"] == "exit_take_in_band" and not _opens(p)
    take = next(x for x in p.orders.values() if x["tif"] == "IOC")
    assert (take["kind"], take["side"], take["wire"], take["qty"], take["his_level"], take["state"]) == (
        "take", SELL, 0.53, 358, 0.549, "filled")


def test_x1_book_334_at_band_two_the_re_read_withholds_the_band_ioc_and_the_rest_goes_the_same_tick(monkeypatch):
    """The same tick with the re-read's bid at 0.52: bid_moved {0.53,
    0.52, wire 0.53}, no IOC -- and lane 1's rule: the whole 358 rests
    at his cent 0.55 the same tick, decision exit_rest."""
    _band_two(monkeypatch)
    p, b = _exit_world(0.549, 358)
    p.add_order(b, side=SELL, wire=0.55, qty=358, kind="reduce", placed_ts=NOW - 100)
    v = _MovingVenue([(0.53, 0.56), (0.52, 0.56)], held={SLUG: 716}, ioc_fill=358.0)
    v.rest("oid-1", "SELL", 0.55, 358)
    st = _tick(p, v, http=_mkt(358.0))
    lp = b["last_plan"]
    assert [c[1] for c in _cancels(v)] == ["oid-1"]
    assert [c[2:6] for c in _places(v)] == [(0.55, 358, True, GTC_TIF)] and _decisions(p) == ["exit_rest"]
    assert lp["bid_moved"] == {"bid_at_plan": 0.53, "bid_at_send": 0.52, "wire": 0.53}
    assert _census(st, "bid_moved") == 1 and _census(st, "exit_take_in_band") == 1, "the decision is counted; the IOC withheld"
    assert _census(st, "exit_take") == 0 and _census(st, "exit_take_rested") == 1 and _census(st, "take_placed") == 0
    assert lp["exit_take_rested"] == {"take": 0.53, "rest": 0.55, "qty": 358, "filled": 0.0, "rested": 358}
    assert lp["exit_band"]["cents_off_his"] == 1.9 and b["ledger_net"] == 716
    opens = _opens(p)
    assert len(opens) == 1 and (opens[0]["wire"], opens[0]["qty"], opens[0]["tif"]) == (0.55, 358, "GTC")


def test_x1_book_334_at_the_default_the_same_tick_is_held_with_the_band_bound_on_the_plan():
    """The bid 0.53 at the default: no cancel, no IOC, exit_out_of_tol
    {bid 0.53, floor 0.539, band_floor 0.539}, the rest stands."""
    p, b = _exit_world(0.549, 358)
    p.add_order(b, side=SELL, wire=0.55, qty=358, kind="reduce", placed_ts=NOW - 100)
    v = _Venue(bid=0.53, ask=0.56, held={SLUG: 716}, ioc_fill=358.0)
    v.rest("oid-1", "SELL", 0.55, 358)
    st = _tick(p, v, http=_mkt(358.0))
    assert not _cancels(v) and not _places(v) and _bbos(v).count(SLUG) == 1
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "open_order_pending") == 1
    assert b["last_plan"]["exit_out_of_tol"] == {"bid": 0.53, "ask": 0.56, "floor": 0.539, "band_floor": 0.539, "at": NOW}
    assert b["last_plan"]["exit_take_band"] == 0.54 and "exit_band" not in b["last_plan"]
    _no_band_word(p, st, b)


# ------------------------------------------------------ (4) book 419's shape

def test_x1_book_419_at_band_two_the_take_then_the_band_take_then_the_hold(monkeypatch):
    """His 0.180: floor 0.17 / take 0.17, band_floor 0.16 / take_band
    0.16, rest 0.18. Bid 0.17: today's take (decision 'take'). Bid 0.16:
    the band IOC at 0.16 for 152, decision exit_take_in_band; it fills
    14 (the row's 0.09) and the 138 rest at 0.18 the same tick (lane
    1). Bid 0.15: held, exit_out_of_tol {floor 0.17, band_floor 0.16},
    the rest at 0.18."""
    _band_two(monkeypatch)
    p, b = _exit_world(0.18, 152, entry=0.20)
    v = _Venue(bid=0.17, ask=0.19, held={SLUG: 304}, ioc_fill=152.0)
    st = _tick(p, v, http=_mkt(152.0))
    assert [c[2:6] for c in _places(v)] == [(0.17, 152, True, IOC_TIF)] and _decisions(p) == ["take"]
    assert _census(st, "exit_take") == 1 and _census(st, "exit_take_in_band") == 0 and b["ledger_net"] == 152
    assert (b["last_plan"]["exit_take"], b["last_plan"]["exit_take_band"]) == (0.17, 0.16)
    p, b = _exit_world(0.18, 152, entry=0.20)
    v = _Venue(bid=0.16, ask=0.19, held={SLUG: 304}, ioc_fill=14.0)
    st = _tick(p, v, http=_mkt(152.0))
    assert [c[2:6] for c in _places(v)] == [(0.16, 152, True, IOC_TIF), (0.18, 138, True, GTC_TIF)]
    assert _decisions(p) == ["exit_take_in_band", "exit_rest"] and b["ledger_net"] == 290
    assert _census(st, "exit_take_in_band") == 1 and _census(st, "exit_take") == 1 and _census(st, "exit_take_rested") == 1
    assert b["last_plan"]["exit_take_rested"] == {"take": 0.16, "rest": 0.18, "qty": 152, "filled": 14.0, "rested": 138}
    assert b["last_plan"]["exit_band"] == {"bid": 0.16, "ask": 0.19, "his": 0.18, "cents_off_his": 2.0, "at": NOW}
    p, b = _exit_world(0.18, 152, entry=0.20)
    v = _Venue(bid=0.15, ask=0.19, held={SLUG: 304}, ioc_fill=152.0)
    st = _tick(p, v, http=_mkt(152.0))
    assert [c[2:6] for c in _places(v)] == [(0.18, 152, True, GTC_TIF)] and _decisions(p) == ["exit_rest"]
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "exit_take_in_band") == 0 and _bbos(v).count(SLUG) == 1
    assert b["last_plan"]["exit_out_of_tol"] == {"bid": 0.15, "ask": 0.19, "floor": 0.17, "band_floor": 0.16, "at": NOW}


# ------------------------------------------------------ (5) book 285's shape

def test_x1_book_285_the_band_is_measured_from_his_newest_reducing_fill_never_the_presets_vwap(monkeypatch):
    """His reducing fills step 0.84 then 0.72 (the preset's size-weighted
    0.840 against our 0.718): _his_level picks the NEWEST, so the band
    reads 0.72 -- floor 0.71 / take 0.71, band_floor 0.70 / take_band
    0.70. Bid 0.69: held. Bid 0.70: the band IOC at 0.70, never under.
    Bid 0.71: today's take."""
    _band_two(monkeypatch)

    def world():
        p = _pool(fills=[_fill(M, "BUY", 864, 0.80, NOW - 3000), _fill(M, "SELL", 216, 0.84, NOW - 1000),
                         _fill(M, "SELL", 216, 0.72, NOW - 500)],
                  snap={M: 432.0, N: 0.0})
        return p, p.add_book(ledger=864, avg_cost=0.80)
    p, b = world()
    v = _Venue(bid=0.69, ask=0.74, held={SLUG: 864}, ioc_fill=432.0)
    st = _tick(p, v, http=_mkt(432.0))
    lp = b["last_plan"]
    assert lp["exit_px"] == 0.72 and (lp["exit_take"], lp["exit_take_band"], lp["exit_rest"]) == (0.71, 0.70, 0.72)
    assert [c[2:6] for c in _places(v)] == [(0.72, 432, True, GTC_TIF)] and _census(st, "exit_out_of_tol") == 1
    assert lp["exit_out_of_tol"]["band_floor"] == 0.70 and _census(st, "exit_take_in_band") == 0
    p, b = world()
    v = _Venue(bid=0.70, ask=0.74, held={SLUG: 864}, ioc_fill=432.0)
    st = _tick(p, v, http=_mkt(432.0))
    assert [c[2:6] for c in _places(v)] == [(0.70, 432, True, IOC_TIF)] and _decisions(p) == ["exit_take_in_band"]
    assert _census(st, "exit_take_in_band") == 1 and b["ledger_net"] == 432
    assert min(c[2] for c in _places(v)) >= 0.70, "never a cent under his newest fill less the band"
    p, b = world()
    v = _Venue(bid=0.71, ask=0.74, held={SLUG: 864}, ioc_fill=432.0)
    st = _tick(p, v, http=_mkt(432.0))
    assert [c[2:6] for c in _places(v)] == [(0.71, 432, True, IOC_TIF)] and _decisions(p) == ["take"]
    assert _census(st, "exit_take_in_band") == 0 and _census(st, "exit_take") == 1


# ------------------------------------------------------ (6) book 467's shape

def test_x1_book_467_the_band_reads_the_long_legs_own_newest_reducing_fill_not_the_flattened_short_side(monkeypatch):
    """A SHORT episode's fills (his BUY then SELL of the other token,
    1 - 0.70 = 0.30 in long space) sit before the long episode's (his BUY
    1140 at 0.50, his SELL 570 at 0.25): the band prices off 0.25, the
    long leg's own newest reducing fill -- take_band 0.23 -- never the
    old episode's 0.30. The IOC fills 245 of 570 and the 325 rest at
    0.25 the same tick."""
    _band_two(monkeypatch)
    p = _pool(fills=[_fill(N, "BUY", 400, 0.72, NOW - 6000), _fill(N, "SELL", 400, 0.70, NOW - 5000),
                     _fill(M, "BUY", 1140, 0.50, NOW - 3000), _fill(M, "SELL", 570, 0.25, NOW - 1000)],
              snap={M: 570.0, N: 0.0})
    b = p.add_book(ledger=1140, avg_cost=0.50)
    v = _Venue(bid=0.23, ask=0.27, held={SLUG: 1140}, ioc_fill=245.0)
    st = _tick(p, v, http=_mkt(570.0))
    lp = b["last_plan"]
    assert lp["exit_px"] == 0.25 and (lp["exit_take"], lp["exit_take_band"], lp["exit_rest"]) == (0.24, 0.23, 0.25)
    assert [c[2:6] for c in _places(v)] == [(0.23, 570, True, IOC_TIF), (0.25, 325, True, GTC_TIF)]
    assert _decisions(p) == ["exit_take_in_band", "exit_rest"] and b["ledger_net"] == 895
    assert _census(st, "exit_take_in_band") == 1 and _census(st, "exit_take_rested") == 1
    assert lp["exit_take_rested"] == {"take": 0.23, "rest": 0.25, "qty": 570, "filled": 245.0, "rested": 325}


# ------------------------------------------------------ (7) book 347's shape

def test_x1_book_347_a_frozen_short_under_e16_is_unclocked_first_then_covers_in_band_on_his_witnessed_buy_back(monkeypatch):
    """SHORT, venue_ledger_disagree, his per-market read down. Tick 1: no
    clock on the prior plan -> `unclocked`, NOTHING sent (fail closed,
    unchanged). Tick 2: his SELL of 120 of the other token (a buy-back,
    0.30 in long space) after the clock -> the cover of 120 through
    _act: ceiling 0.31 / cover 0.31, band_ceiling 0.32 / cover_band 0.32;
    the ask at 0.32 -> `cover_in_band` IOC at 0.32 sized by _cover_qty
    on the frozen clamp `_frozen_venue` (the LEDGER -300 on E16's
    on-fill path, the walk unread), never more than the witnessed 120.
    At the default the same tick rests at floor(his) 0.30 (E16)."""
    _thaw_off(monkeypatch)
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500)]
    for band, want in ((0.02, "ioc"), (0.01, "rest")):
        monkeypatch.setattr(rules, "MIRROR_EXIT_TAKE_BAND", band)
        p = _e16_pool(fills=fills, snap=None)
        b = _short_book(p, ledger=-300, state="frozen", frozen_reason="venue_ledger_disagree", frozen_ts=NOW - 100)
        st = _tick(p, _NoClose(held={SLUG: -300}, bid=0.30, ask=0.32), http=_unread())
        assert _plan_exit(b) == {"held": "frozen_venue_unread", "why": "his_market_read"} and not _places(p and _NoClose())
        assert b["last_plan"]["frozen_witness"] == {"why": "his_market_read", "held": "unclocked"}
        assert _census(st, "cover_in_band") == 0 and _census(st, "frozen_reduce_on_fill") == 0
        p.fills = fills + [_fill(N, "SELL", 120, 0.70, NOW - 1000)]
        v2 = _NoClose(held={SLUG: -300}, bid=0.30, ask=0.32, ioc_fill=120.0)
        st2 = _tick(p, v2, now=NOW + 15, http=_unread())
        assert "close" not in _kinds(v2)
        lp = b["last_plan"]
        assert lp["exit_px"] == pytest.approx(0.30) and lp["exit_cover"] == 0.31 and lp["exit_rest"] == 0.30
        if want == "ioc":
            assert lp["exit_cover_band"] == 0.32 and lp["exit_band_ceiling"] == pytest.approx(0.32)
            assert [c[1:] for c in _places(v2)] == [(SLUG, 0.32, 120, True, IOC_TIF, SHORT, False, None)]
            assert _decisions(p) == ["cover_in_band"] and _census(st2, "cover_in_band") == 1
            assert _census(st2, "short_cover_take") == 1 and _census(st2, "frozen_reduce_on_fill") == 1
            assert lp["exit_band"] == {"bid": 0.30, "ask": 0.32, "his": pytest.approx(0.30), "cents_off_his": 2.0, "at": NOW + 15}
            assert b["ledger_net"] == -180 and b["state"] == "frozen"
        else:
            assert lp["exit_cover_band"] == 0.31 and lp["exit_band_ceiling"] == pytest.approx(0.31)
            assert [c[1:] for c in _places(v2)] == [(SLUG, 0.30, 120, True, GTC_TIF, SHORT, True, None)]
            assert _decisions(p) == ["cover"] and _census(st2, "cover_in_band") == 0
            assert _census(st2, "short_cover_rest") == 1 and _census(st2, "frozen_reduce_on_fill") == 1
            assert lp["exit_out_of_tol"] == {"bid": 0.30, "ask": 0.32, "ceiling": 0.31, "band_ceiling": 0.31, "at": NOW + 15}
            assert b["ledger_net"] == -300


# ------------------------------------------- (8) book 661's shape, (9) Martinez 534's

def test_x1_book_661_a_partially_filled_cover_rest_re_quoted_carries_its_booked_fill_into_the_band_ioc(monkeypatch):
    """A cover rest of 300 at floor(his) 0.31 filled 89.24 (the row's
    shape), his 0.31: cover 0.32, cover_band 0.33. The ask at 0.33
    (outside the ceiling, inside the band): the standing rest is
    cancelled, its 89.24 booked, and the band IOC goes at 0.33 for the
    remainder -- 300 - 89.24 floored, sized by _cover_qty on the leg --
    never for 300; decision cover_in_band."""
    _shorts_on(monkeypatch)
    _band_two(monkeypatch)
    p, b, v = _flip_world(ask=0.33, held={SLUG: -(300 - 89.24)}, ioc_fill=210.0)
    o = p.add_order(b, side=BUY, wire=0.31, qty=300, kind="flatten_paired", placed_ts=NOW - 100)
    v.rest("oid-1", "BUY", 0.31, 300, filled=89.24, avg=0.31)
    st = _tick(p, v)
    assert [c[1] for c in _cancels(v)] == ["oid-1"] and p.orders[o["id"]]["state"] == "cancelled"
    assert p.orders[o["id"]]["booked_filled"] == 89.24
    iocs = [c for c in _places(v) if c[5] == IOC_TIF]
    assert len(iocs) == 1 and iocs[0][2] == 0.33 and iocs[0][3] <= 211 and iocs[0][3] < 300, iocs
    assert "cover_in_band" in _decisions(p) and _census(st, "cover_in_band") == 1 and _census(st, "short_cover_take") == 1
    assert b["last_plan"]["exit_band"]["cents_off_his"] == 2.0 and b["last_plan"]["exit_cover_band"] == 0.33
    assert _census(st, "exit_take_rested") == 0, "the cover's partial rests nothing this tick (S4's path)"
    # THE REMAINDER, NOT THE PLAN (the review's HIGH-2; mutant M10): the same row with the
    # venue still reading the whole -300 at the walk. The standing row's 89.24 is booked at
    # the walk before the plan is made, so the plan flattens the ledger -300 + 89.24 =
    # -210.76 -> qty 211 (the venue -300 on the plan); the band IOC is sized on the ROW's
    # remainder 300 - 89.24 = 210.76 floored -> 210 through _cover_qty -- never the plan's
    # 211 (the mutant's size) and never the row's 300
    pw, bw, vw = _flip_world(ask=0.33, held={SLUG: -300}, ioc_fill=210.0)
    ow = pw.add_order(bw, side=BUY, wire=0.31, qty=300, kind="flatten_paired", placed_ts=NOW - 100)
    vw.rest("oid-1", "BUY", 0.31, 300, filled=89.24, avg=0.31)
    stw = _tick(pw, vw)
    assert bw["last_plan"]["qty"] == 211 and bw["last_plan"]["venue"] == -300.0
    assert pw.orders[ow["id"]]["booked_filled"] == 89.24 and pw.orders[ow["id"]]["state"] == "cancelled"
    iocw = [c for c in _places(vw) if c[5] == IOC_TIF]
    assert [(c[2], c[3]) for c in iocw] == [(0.33, 210)], iocw
    assert _decisions(pw) == ["cover_in_band"] and _census(stw, "cover_in_band") == 1
    # THE NO-REST COVER BAND SITE SIZES THROUGH _cover_qty TOO (the review's HIGH-2 sibling;
    # mutant M22): a live short, no rest standing, the ledger -300 and the standing row reading
    # 299.0 (one share under, inside VENUE_LEDGER_TOL_SHARES: no freeze) -> the plan asks 300,
    # the band IOC at 0.33 is min(300, leg 300, ceil(299.0)) = 299 -- never the plan's 300; the
    # tolerance IOC (ask 0.32 at the default) is 299 by the same clamp, decision 'cover'
    for band, ask, word in ((0.02, 0.33, "cover_in_band"), (0.01, 0.32, "cover")):
        monkeypatch.setattr(rules, "MIRROR_EXIT_TAKE_BAND", band)
        pn, bn, vn = _flip_world(ask=ask, held={SLUG: -299.0}, ioc_fill=299.0)
        pn.rows[bn["standing_row_id"]]["filled_shares"] = 299.0
        stn = _tick(pn, vn)
        assert bn["last_plan"]["qty"] == 300 and bn["state"] == "live"
        assert [c[2:6] for c in _places(vn)] == [(ask, 299, True, IOC_TIF)], _places(vn)
        assert _decisions(pn) == [word] and _census(stn, "cover_in_band") == (1 if word == "cover_in_band" else 0)
        assert _census(stn, "short_cover_take") == 1
    _band_two(monkeypatch)
    # Martinez 534's shape: a cover placed at the REST cent writes 'cover', never the band word
    p2, b2, v2 = _flip_world(ask=0.34)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.31, 300, True, GTC_TIF)] and _decisions(p2) == ["cover"]
    assert _census(st2, "cover_in_band") == 0 and _census(st2, "short_cover_rest") == 1
    assert b2["last_plan"]["exit_out_of_tol"] == {"bid": 0.30, "ask": 0.34, "ceiling": 0.32, "band_ceiling": 0.33, "at": NOW}


# ------------------------------------------------------ (10) book 611's shape

def test_x1_book_611_an_add_leg_never_sees_a_band(monkeypatch):
    _band_two(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 300, 0.52, NOW - 3000)])
    b = p.add_book(ledger=0)
    v = _Venue(bid=0.52, ask=0.55)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)] and _decisions(p) == ["rest"]
    lp = b["last_plan"]
    assert "exit_take_band" not in lp and "exit_band" not in lp and "exit_px" not in lp
    assert _census(st, "exit_take_in_band") == 0 and _census(st, "cover_in_band") == 0
    assert ml._exit_terms(types.SimpleNamespace(flatten_all=False), b, BUY, 0.52, {}) is None


# ---------------------------------------------- (11) the rails and the guards

class _Fake:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    async def __call__(self, t, book, r, kind, side, wire, qty, his_px, p, plan, tif="GTC", take_first=False,
                       in_band=False):
        self.calls.append((kind, side, wire, int(qty), tif, in_band))
        res, filled = self.results.pop(0)
        if res == "take":
            plan["take_qty"], plan["take_filled"] = int(qty), float(filled)
        return res


def _unit(monkeypatch, results, *, state="live", cancel_all=None, abandoned=False, nonterminal=(),
          ledger=300, held=300.0, qty=200, in_band=True):
    fake = _Fake(results)
    monkeypatch.setattr(ml, "_place", fake)
    stats = ml._new_stats()
    monkeypatch.setattr(ml, "_current_stats", stats)
    ex = rules.exit_terms(SELL, 0.31)
    book = {"id": 7, "state": state, "ledger_net": ledger, "_held": held, "intent": "ORDER_INTENT_BUY_LONG"}
    t = types.SimpleNamespace(cancel_all=cancel_all, abandoned=abandoned, nonterminal=set(nonterminal))
    r = types.SimpleNamespace(whale="rn1")
    plan = {}
    res = _run(ml._exit_take(t, book, r, mi.Plan(SELL, qty, 0.31, "reduce"), ex, qty, 0.31, plan, "reduce",
                             in_band=in_band))
    return res, fake.calls, plan, stats["census"]


def test_x1_a_frozen_tripped_abandoned_or_non_terminal_book_sends_no_rest_after_a_band_ioc_and_the_prices_are_the_terms(monkeypatch):
    _band_two(monkeypatch)
    ex = rules.exit_terms(SELL, 0.31)
    assert (ex["take"], ex["take_band"], ex["rest"]) == (0.30, 0.29, 0.31)
    for kw in ({"state": "frozen"}, {"cancel_all": "overfill"}, {"abandoned": True}, {"nonterminal": (7,)}):
        for res0, filled in (("bid_moved", 0.0), ("ioc_quote_unread", 0.0), ("take", 50.0)):
            res, calls, plan, census = _unit(monkeypatch, [(res0, filled)], **kw)
            assert res == res0 and calls == [("take", SELL, 0.29, 200, "IOC", True)], (kw, res0)
            assert "exit_take_rested" not in plan and census["exit_take_rested"] == 0, (kw, res0)
    # a live book: the band IOC at 0.29, the remainder at his cent 0.31 the same tick (lane 1)
    res, calls, plan, census = _unit(monkeypatch, [("take", 50.0), ("rest_placed", 0.0)], ledger=250, held=250.0)
    assert res == "rest_placed" and calls == [("take", SELL, 0.29, 200, "IOC", True), ("reduce", SELL, 0.31, 150, "GTC", False)]
    assert plan["exit_take_rested"] == {"take": 0.29, "rest": 0.31, "qty": 200, "filled": 50.0, "rested": 150}
    # without the band the same call prices at the take cent, in_band False on the wire
    res, calls, plan, census = _unit(monkeypatch, [("take", 200.0)], in_band=False)
    assert res == "take" and calls == [("take", SELL, 0.30, 200, "IOC", False)]
    # _exit_take prices off the terms alone: never the quote
    src = inspect.getsource(ml._exit_take)
    assert 'ex["take_band"] if in_band else ex["take"]' in src and 'ex["rest"]' in src
    assert "r.bid" not in src and "r.ask" not in src and "sell_wire" not in src
    # the band test reads the band cent only when it is strictly past the tolerance cent
    bsrc = inspect.getsource(ml._exit_band_at)
    assert "tb < take - 1e-9" in bsrc and "cb > cover + 1e-9" in bsrc
    r = types.SimpleNamespace(bid=0.53, ask=0.56)
    assert ml._exit_band_at(SELL, r, rules.exit_terms(SELL, 0.549, band=0.02)) is True
    assert ml._exit_band_at(SELL, r, rules.exit_terms(SELL, 0.549, band=0.01)) is False
    assert ml._exit_band_at(SELL, types.SimpleNamespace(bid=None, ask=0.56), rules.exit_terms(SELL, 0.549, band=0.02)) is False
    assert ml._exit_band_at(SELL, r, {"take": 0.54}) is False and ml._exit_band_at("X", r, {}) is False
    rb = types.SimpleNamespace(bid=0.50, ask=0.53)
    assert ml._exit_band_at(BUY, rb, rules.exit_terms(BUY, 0.514, band=0.02)) is True
    assert ml._exit_band_at(BUY, rb, rules.exit_terms(BUY, 0.514, band=0.01)) is False
    assert ml._exit_band_at(BUY, types.SimpleNamespace(bid=0.50, ask=None), rules.exit_terms(BUY, 0.514, band=0.02)) is False


def test_x1_the_band_sites_sit_after_the_tolerance_sites_and_the_witness_ratchet_and_re_read_are_untouched():
    """The band test is a second `elif` AFTER the tolerance test on both
    long-exit sites and both cover sites; the tolerance IOC's own lines
    are byte for byte E4's / E14b's / S4's; _ioc_reread, _exit_terms'
    rule, the E15 witness clause and the E12 ratchet name nothing of
    the band."""
    asrc = inspect.getsource(ml._act)
    tol_long_keep = 'if rules.at_or_through(SELL, r.bid, r.ask, ex["take"]):\n'
    tol_cover_keep = 'if rules.at_or_through(BUY, r.bid, r.ask, ex["cover"]):\n'
    assert asrc.count(tol_long_keep) == 2 and asrc.count(tol_cover_keep) == 2
    assert asrc.count("elif _exit_band_take(t, SELL, r, ex, plan):") == 2 and asrc.count("elif _exit_band_take(t, BUY, r, ex, plan):") == 2
    assert "_exit_band_at(" not in asrc, "the sites read the band through its count guard alone"
    for tol, band in ((tol_long_keep, "elif _exit_band_take(t, SELL, r, ex, plan):"), (tol_cover_keep, "elif _exit_band_take(t, BUY, r, ex, plan):")):
        i1 = asrc.index(tol)
        j1 = asrc.index(band)
        i2 = asrc.index(tol, i1 + 1)
        j2 = asrc.index(band, j1 + 1)
        assert i1 < j1 < i2 < j2, "each band site follows its own tolerance site"
    # the tolerance IOC's call lines, unchanged
    assert 'return await _exit_take(t, book, r, p, ex, left, his_px, plan, kind)\n' in asrc
    assert 'return await _exit_take(t, book, r, p, ex, qty, his_px, plan, kind)\n' in asrc
    assert 'return await _place(t, book, r, "take", BUY, ex["cover"], qty, his_px, p, plan, tif="IOC")\n' in asrc
    assert asrc.count('return await _place(t, book, r, "take", BUY, ex["cover"], left, his_px, p,\n') == 1
    # the band's own calls: through _exit_take on the long sites, the cover's IOC on the short
    assert asrc.count("in_band=True)") == 5 and asrc.count('ex["cover_band"]') == 2
    assert asrc.count("_exit_band_mark(t, r, SELL, ex, plan, w)") == 2 and asrc.count("_exit_band_mark(t, r, BUY, ex, plan, w)") == 2
    # untouched by the lane, by name
    for fn in (ml._ioc_reread, ml._flatten_send, ml._flatten_vanished, ml._frozen_exit, ml._frozen_reduce_on_fill,
               ml._s4_refusal, ml._cover_qty, ml._sell_qty, ml._entry_take, rules.take_allowed,
               rules.at_or_through, rules.rest_decision, rules.band_cent, rules.take_in_band):
        s = inspect.getsource(fn)
        for name in ("take_band", "cover_band", "exit_band", "_exit_band_at", "MIRROR_EXIT_TAKE_BAND"):
            assert not re.search(rf"(?<![\w]){name}(?![\w])", s), (fn.__name__, name)
    # E14's own reader names its own `take_band` (the entry's plan field) and nothing of the exit's
    tb = inspect.getsource(ml._take_band)
    for name in ("cover_band", "exit_band", "_exit_band_at", "MIRROR_EXIT_TAKE_BAND", "exit_take_in_band"):
        assert name not in tb, name
    for name in ("witnessed_ratchet", "reducing_since", "reducing_on"):
        assert "band" not in inspect.getsource(getattr(mi, name)), name
    # no wait added, the tolerance's line as it was, the day stop read by no site of the lane
    assert "MIRROR_EXIT_TAKE_BAND" not in inspect.getsource(ml), "the worker reads the band through exit_terms alone"
    for fn in (ml._exit_band_at, ml._exit_band_mark, ml._exit_take, ml._order_open_his_exit):
        s = inspect.getsource(fn)
        assert "loss_stop" not in s and "min_wait_env" not in s and "_env_float" not in s, fn.__name__


def test_x1_059_absent_sends_no_band_ioc_the_exit_is_held_by_name_and_the_rest_goes_uncounted(monkeypatch):
    """The review's HIGH-1 (FILL_plan section 4: 'an uncounted band take
    is not sent'). The E18 fixture that hides the 059 columns, the band
    at 0.02: book 419's shape with the bid 0.16 (inside the band, past
    the take) -> NO band IOC, `exit_out_of_tol` {floor 0.17, band_floor
    0.16}, the rest at 0.18 by the 050 INSERT (19 args), no band word,
    no band count, `exit_band_uncounted` on the plan; the S4 cover with
    the ask 0.33 (inside the band 0.33, over the ceiling 0.32) -> the
    rest at 0.31, `cover_in_band` 0; the tolerance take under the same
    absence goes as before; at the default nothing is stamped."""
    _band_two(monkeypatch)
    monkeypatch.setattr(ml, "_order_cols_absent_logged", False)
    p, b = _exit_world(0.18, 152, entry=0.20)
    p.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    v = _Venue(bid=0.16, ask=0.19, held={SLUG: 304}, ioc_fill=14.0)
    st = _tick(p, v, http=_mkt(152.0))
    assert st["order_cols_absent"] == "UndefinedColumnError"
    assert [c[2:6] for c in _places(v)] == [(0.18, 152, True, GTC_TIF)], "no band IOC without its word"
    assert [len(i) for i in _inserts(p)] == [19], "the 050 INSERT (no decision column): no band word can be written"
    assert not any(len(i) > 20 and i[20] in BAND_WORDS for i in _inserts(p))
    assert _census(st, "exit_take_in_band") == 0 and _census(st, "exit_out_of_tol") == 1 and _bbos(v).count(SLUG) == 1
    lp = b["last_plan"]
    assert lp["exit_out_of_tol"] == {"bid": 0.16, "ask": 0.19, "floor": 0.17, "band_floor": 0.16, "at": NOW}
    assert lp["exit_band_uncounted"] is True and "exit_band" not in lp and b["ledger_net"] == 304
    # the tolerance take under the same absence: today's IOC, as before this lane
    p2, b2 = _exit_world(0.18, 152, entry=0.20)
    p2.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    v2 = _Venue(bid=0.17, ask=0.19, held={SLUG: 304}, ioc_fill=152.0)
    st2 = _tick(p2, v2, http=_mkt(152.0))
    assert [c[2:6] for c in _places(v2)] == [(0.17, 152, True, IOC_TIF)] and "exit_band_uncounted" not in b2["last_plan"]
    # the S4 cover: the ask inside the band, the columns absent -> the rest at floor(his), no cover_in_band
    _shorts_on(monkeypatch)
    p3, b3, v3 = _flip_world(ask=0.33)
    p3.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    st3 = _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.31, 300, True, GTC_TIF)] and _census(st3, "cover_in_band") == 0
    assert b3["last_plan"]["exit_band_uncounted"] is True
    assert b3["last_plan"]["exit_out_of_tol"] == {"bid": 0.30, "ask": 0.33, "ceiling": 0.32, "band_ceiling": 0.33, "at": NOW}
    # at the default the guard is never reached: nothing stamped
    monkeypatch.setattr(rules, "MIRROR_EXIT_TAKE_BAND", 0.01)
    p4, b4 = _exit_world(0.18, 152, entry=0.20)
    p4.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    _tick(p4, _Venue(bid=0.16, ask=0.19, held={SLUG: 304}), http=_mkt(152.0))
    assert "exit_band_uncounted" not in b4["last_plan"]
    # the pure guard
    t = types.SimpleNamespace(order_cols=None)
    r = types.SimpleNamespace(bid=0.53, ask=0.56)
    plan = {}
    assert ml._exit_band_take(t, SELL, r, rules.exit_terms(SELL, 0.549, band=0.02), plan) is False and plan == {"exit_band_uncounted": True}
    plan = {}
    assert ml._exit_band_take(types.SimpleNamespace(order_cols=True), SELL, r, rules.exit_terms(SELL, 0.549, band=0.02), plan) is True and plan == {}
    plan = {}
    assert ml._exit_band_take(t, SELL, r, rules.exit_terms(SELL, 0.549, band=0.01), plan) is False and plan == {}


# ----------------------------------------------------------- the fast gate

def _rest_book(fills, ledger=0, ref_at=NOW - 100, order_kw=None):
    """A long book with an ENTRY rest standing (BUY 300 at 0.30) and E15's
    reference clock on its prior plan."""
    p = _pool(fills=fills)
    b = p.add_book(ledger=ledger, last_plan={"kind": "increase", "reduce_ref": {"target": 300, "at": ref_at}})
    p.add_order(b, **(order_kw or {}))
    v = _Venue()
    v.rest("oid-1", "SELL" if (order_kw or {}).get("side") == SELL else "BUY", 0.30, 300)
    return p, b, v


def test_x1_the_fast_gate_names_order_open_his_exit_on_his_reducing_fill_with_an_entry_rest_standing_and_cancels_nothing():
    fills = [_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "SELL", 100, 0.31, NOW - 5)]
    p, b, v = _rest_book(fills)
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {CID: "order_open_his_exit"} and _census(fs, "order_open_his_exit") == 1
    assert _census(fs, "fast_tick_skipped") == 1 and _census(fs, "fast_tick_placed") == 0
    assert not _cancels(v) and not _places(v) and _bbos(v) == [], "a count, never a cancel, never a read of the quote"
    assert len(_opens(p)) == 1 and b["open_order_id"] is not None
    # a flatten row standing (a reduce leg): `order_open` as before
    p2, b2, v2 = _rest_book(fills, ledger=300, order_kw={"side": SELL, "kind": "flatten_paired", "wire": 0.31})
    _walk()
    fs2 = _fast(p2, v2)
    assert _skips(fs2) == {CID: "order_open"} and _census(fs2, "order_open_his_exit") == 0 and not _cancels(v2)
    # book 611's shape: his net still GROWING on the wake (adds only after the clock): `order_open`
    p3, b3, v3 = _rest_book([_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "BUY", 50, 0.32, NOW - 5)])
    _walk()
    fs3 = _fast(p3, v3)
    assert _skips(fs3) == {CID: "order_open"} and _census(fs3, "order_open_his_exit") == 0
    # his sale clocked AT or BEFORE the reference: no witness, `order_open`
    p4, b4, v4 = _rest_book(fills, ref_at=NOW - 5)
    _walk()
    assert _skips(_fast(p4, v4)) == {CID: "order_open"}
    # no reference clock on the prior plan: `order_open`
    p5 = _pool(fills=fills)
    b5 = p5.add_book(ledger=0)
    p5.add_order(b5)
    v5 = _Venue()
    v5.rest("oid-1")
    _walk()
    assert _skips(_fast(p5, v5)) == {CID: "order_open"}


def test_x1_the_fast_gates_split_fails_closed_to_order_open_when_the_fills_cannot_be_read(monkeypatch):
    fills = [_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "SELL", 100, 0.31, NOW - 5)]
    p, b, v = _rest_book(fills)

    async def _boom(pool, whale, cid):
        raise RuntimeError("fills unreadable")
    monkeypatch.setattr(ml.ms, "his_fills", _boom)
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {CID: "order_open"} and _census(fs, "order_open_his_exit") == 0 and not _cancels(v)
    # the pure reading: every unreadable fact is False
    t = types.SimpleNamespace(fast_open={7: {"side": BUY, "tif": "GTC"}})
    book = {"id": 7, "intent": "ORDER_INTENT_BUY_LONG", "long_asset": M, "other_asset": N,
            "last_plan": {"reduce_ref": {"target": 300, "at": NOW - 100}}}
    assert ml._order_open_his_exit(t, book, fills) is True
    assert ml._order_open_his_exit(t, book, None) is False and ml._order_open_his_exit(t, book, "x") is False
    assert ml._order_open_his_exit(types.SimpleNamespace(fast_open={}), book, fills) is False
    assert ml._order_open_his_exit(types.SimpleNamespace(fast_open={7: {"side": SELL, "tif": "GTC"}}), book, fills) is False
    assert ml._order_open_his_exit(types.SimpleNamespace(fast_open={7: {"side": BUY, "tif": "IOC"}}), book, fills) is False
    assert ml._order_open_his_exit(t, {**book, "last_plan": {}}, fills) is False
    assert ml._order_open_his_exit(t, {**book, "last_plan": {"reduce_ref": {"at": "soon"}}}, fills) is False
    assert ml._order_open_his_exit(t, {**book, "last_plan": None}, fills) is False
    assert ml._order_open_his_exit(t, book, [fills[0]]) is False, "no reducing fill after the clock"
    # the gate itself: with no fills the clause is `order_open` byte for byte; the split reads fills only
    gsrc = inspect.getsource(ml._fast_gate)
    assert 'if fills is not None and _order_open_his_exit(t, book, fills):\n            return "order_open_his_exit"\n        return "order_open"' in gsrc
    gbody = gsrc.split('"""')[2]
    assert "take_band" not in gbody and "band_cent" not in gbody
    assert "_cancel_and_settle" not in gbody and "_place(" not in gbody, "the gate reads; it never cancels or places"
    fsrc = inspect.getsource(ml._fast_book)
    assert fsrc.index("_fast_gate(t, book)") < fsrc.index("ms.his_fills(t.pool") < fsrc.index("_fast_gate(t, book, fills)") \
        < fsrc.index('_mirror_stop("order_open_his_exit"') < fsrc.index("async with lk:")
    # LOW-1: the fills are read only behind an ENTRY rest -- a flatten row standing reads none
    reads = []

    async def _spy(pool, whale, cid):
        reads.append(cid)
        return fills
    monkeypatch.setattr(ml.ms, "his_fills", _spy)
    p6, b6, v6 = _rest_book(fills, ledger=300, order_kw={"side": SELL, "kind": "flatten_paired", "wire": 0.31})
    _walk()
    assert _skips(_fast(p6, v6)) == {CID: "order_open"} and reads == []
    p7, b7, v7 = _rest_book(fills)
    _walk()
    assert _skips(_fast(p7, v7)) == {CID: "order_open_his_exit"} and reads == [CID]
    assert "_cancel_and_settle" not in fsrc and "_place(" not in fsrc


# --------------------------------------------------- the shadow's exit census

def test_x1_the_shadow_exit_census_reports_the_bands_rate_and_unfilled_share_beside_the_rests(monkeypatch):
    from tests.test_mirror_shadow import HIS as SHADOW_HIS
    from tests.test_mirror_shadow import _Pool as _ShadowPool
    from tests.test_mirror_shadow import _nosleep

    def _rows(n, band=True):
        out = []
        for i in range(n):
            r = {"whale": "rn1", "condition_id": f"m{i}", "exit_kind": "reduced", "exit_plan": "rest",
                 "would_fill": i % 3 > 0, "would_qty": 20, "would_px": 0.4, "family": "moneyline",
                 "touched_s": float(i)}
            if band:
                # the band touched on every row the rest touched, and on every third miss
                r["would_fill_band"] = (i % 3 > 0) or (i % 9 == 0)
            out.append(r)
        return out
    b30 = ms.summarize_exit_rows(_rows(30))["whales"]["rn1"]
    assert b30["ready"] is True and b30["rate"] == round(20 / 30, 4) and b30["unfilled_usd_share"] == round(10 / 30, 4)
    assert b30["resolved_band"] == 30 and b30["fills_band"] == 24 and b30["rate_band"] == round(24 / 30, 4)
    assert b30["unfilled_usd_band"] == round(6 / 30, 4) and b30["unfilled_usd_band_usd"] == round(6 * 20 * 0.4, 2)
    assert b30["resolved_usd_band"] == round(30 * 20 * 0.4, 2) and b30["rate_band"] >= b30["rate"]
    # below the floor the estimates are None; the counts stay
    b29 = ms.summarize_exit_rows(_rows(29))["whales"]["rn1"]
    assert b29["rate_band"] is None and b29["unfilled_usd_band"] is None and b29["resolved_band"] == 29
    # rows with no band verdict (before the deploy): unjudged for the band, the rest's reading untouched
    b0 = ms.summarize_exit_rows(_rows(30, band=False))["whales"]["rn1"]
    assert b0["rate"] == round(20 / 30, 4) and b0["resolved_band"] == 0 and b0["rate_band"] is None
    assert b0["unfilled_usd_band"] is None and b0["fills_band"] == 0
    assert len(b30) < 40
    # exit_leg records the band cent beside the take cent (equal at the default)
    ev = {"kind": "reduced", "move": "m", "at": 1.0, "size": 1.0, "px_equiv": 0.32, "complement_px": 0.68,
          "net_before": 1.0, "net_after": 0.0}
    d = ms.exit_leg(ev, 100.0, 100.0, 34, mi.Plan("SELL_LONG", 66, 0.32, "r"), 0.32)
    assert (d["exit_floor"], d["exit_rest_px"], d["exit_take_px"], d["exit_take_band_px"], d["exit_band_floor"]) == (
        0.31, 0.32, 0.31, 0.31, 0.31)
    monkeypatch.setattr(rules, "MIRROR_EXIT_TAKE_BAND", 0.02)
    d2 = ms.exit_leg(ev, 100.0, 100.0, 34, mi.Plan("SELL_LONG", 66, 0.32, "r"), 0.32)
    assert (d2["exit_take_px"], d2["exit_take_band_px"], d2["exit_band_floor"]) == (0.31, 0.30, 0.30)
    assert "exit_take_band_px" not in ms.exit_leg(ev, 1.0, 1.0, 0, mi.Plan("SELL_LONG", 1, 0.3, "r"), None)
    # the judge: one band statement on the SELL side, inside the same life, never the live-compared column
    _nosleep(monkeypatch)

    class _P(_ShadowPool):
        def __init__(self):
            super().__init__(fills=SHADOW_HIS)

        async def execute(self, sql, *a):
            await super().execute(sql, *a)
            return "UPDATE 1" if "/* judge-sell */" in sql else "UPDATE 0"
    p = _P()
    cid = "0xcond"
    res, fil = _run(ms._write(p, {"whale": "rn1", "condition_id": cid, "bid": 0.29, "ask": 0.30, "detail": {}}))
    assert (res, fil) == (1, 1), "the band judge counts nothing into the long-only rate"
    band = [w for w in p.writes if "/* judge-band */" in w[0]]
    assert len(band) == 1 and band[0][1] == ("rn1", cid, 0.29, ms.JUDGE_TTL_S)
    sql = band[0][0]
    assert "would_side = 'SELL_LONG'" in sql and "(detail->>'exit_take_band_px')::float8 <= $3" in sql
    assert "detail->>'would_fill_band' IS NULL" in sql and "'would_fill_band', true" in sql
    assert "at >= now() - ($4::float8 * interval '1 second')" in sql
    assert "would_fill =" not in sql and "SET would_fill" not in sql
    # the bid unread: no band judge; both sides unread: nothing
    p2 = _P()
    _run(ms._write(p2, {"whale": "rn1", "condition_id": cid, "bid": None, "ask": 0.30, "detail": {}}))
    assert not [w for w in p2.writes if "/* judge-band */" in w[0]]
    p3 = _P()
    _run(ms._write(p3, {"whale": "rn1", "condition_id": cid, "bid": None, "ask": None, "detail": {}}))
    assert not [w for w in p3.writes if "/* judge-" in w[0]]
    # every SQL string this lane touched parses; the census reads the band's verdict, then the rest's
    import pglast
    pglast.parse_sql(ms._SQL_EXIT_CENSUS)
    pglast.parse_sql(sql)
    assert "CASE WHEN detail ? 'would_fill_band' THEN (detail->>'would_fill_band')::boolean" in ms._SQL_EXIT_CENSUS
    assert "WHEN detail ? 'exit_take_band_px' THEN would_fill" in ms._SQL_EXIT_CENSUS
    assert "judge-exit" not in inspect.getsource(ms)


# ---------------------------------------------- (12) the names, the docs, 059

def test_x1_the_census_names_sit_before_drift_smaller_open_the_pins_hold_and_every_name_is_emitted():
    keys = ml.CENSUS_KEYS
    # T2 (FILL lane 4, two names), FILL lane 5 (three) and E22 (FILL lane 22, four) landed after this lane and sit nearer the key (-16:-13 -> -25:-22)
    assert keys[-25:-22] == NEW_NAMES
    assert keys[-26] == "take_in_band" and keys[-27] == "exit_take_rested" and keys[-28] == "wrong_sign_hold"
    assert keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-1] == "cand_terminal_skipped" and keys[-8:-4] == ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")
    assert len(set(keys)) == len(keys)
    for k in NEW_NAMES:
        assert ml._new_stats()["census"][k] == 0, k
    assert {"exit_take_in_band", "cover_in_band"} <= ml.QUIET_EXIT_PLANS and "order_open_his_exit" not in ml.QUIET_EXIT_PLANS
    src = inspect.getsource(ml)
    assert '_mirror_stop("exit_take_in_band", whale)' in inspect.getsource(ml._exit_band_mark)
    assert '_mirror_stop("cover_in_band", whale)' in inspect.getsource(ml._exit_band_mark)
    assert '_mirror_stop("order_open_his_exit", book.get("whale"))' in inspect.getsource(ml._fast_book)
    for k in NEW_NAMES:
        assert src.count(f'_mirror_stop("{k}"') == 1, k
    # the worker reads no env of its own; the rail is rules' (capped_env, floor 0.0)
    assert '"MIRROR_EXIT_TAKE_BAND"' not in src
    # 059's comment stands as written; the docs restate its list with the two words
    sql = (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "('rest', 'take', 'cover', 'exit_rest'; 'take_in_band' is" in sql and "exit_take_in_band" not in sql


def test_x1_every_name_is_emitted_here(monkeypatch):
    test_x1_book_334_at_band_two_the_standing_rest_is_cancelled_and_one_ioc_goes_at_the_band_cent(monkeypatch)
    test_x1_book_661_a_partially_filled_cover_rest_re_quoted_carries_its_booked_fill_into_the_band_ioc(monkeypatch)
    test_x1_the_fast_gate_names_order_open_his_exit_on_his_reducing_fill_with_an_entry_rest_standing_and_cancels_nothing()


def test_x1_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. .*\(2026-09-08, FILL lane 3\)", doc, re.M), "the lane 3 section header"
    for k in NEW_NAMES + ("MIRROR_EXIT_TAKE_BAND", "take_band", "cover_band", "band_floor", "band_ceiling",
                          "exit_band", "exit_out_of_tol", "rate_band", "unfilled_usd_band", "D2",
                          "'rest', 'take', 'take_in_band', 'cover', 'exit_rest'",
                          "test_fill_x1_exit_band.py", "334", "419", "285", "467", "347", "661", "611"):
        assert k in doc, k
