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
    cover at the default band: no 'exit_take_in_band' / 'cover_in_band' row
    is ever written and the band cents on the plan equal E4's.

    RE-PINNED AT E31 (FILL lane 31, 2026-09-10). The band is INERT at the
    default and stays inert -- that is what this test is for and it still
    holds. What moved is the EXECUTION under it: the tolerance IOC these
    worlds used to send at the take cent is retired with `_exit_take`, so
    every world here places the SAME post-only rest at his own cent and
    nothing crosses. Per world:

      334 (his 0.549, rest 0.55): the moving venue's IOC is gone, so the
          re-read that produced `bid_moved` / `exit_take_rested` is gone
          with it -- the rest at 0.55 was always what went out and now it
          is ALL that goes out. The touch-bound re-read (E31 C) still runs
          because 0.55 IS bid + a tick on the first read, so the bbo count
          stays 2 where a moving venue is used;
      285 (his 0.84) at a bid of 0.83: the ONE IOC at 0.83 -- a cent under
          him on the tolerance -- becomes the rest at 0.84, HIS cent. This
          is the mandate's own line: we sell at his price or not at all;
      the S4 flip: the IOC at the ceiling 0.32, THROUGH the ask, becomes
          the cover rest at min(buy_wire(0.31), 0.32 - MAKER_TICK) = 0.31.

    `exit_take` / `short_cover_take` / `exit_take_rested` / `bid_moved` are
    declared zeros; `rest_placed` / `flatten_rested` / `short_cover_rest`
    count in their place, with `maker_fill_at_create` wherever this world's
    taker lifts the fresh rest. E4's held record `exit_out_of_tol` still
    rides the placement road and still carries the BAND BOUND beside the
    floor / the ceiling -- which is the half of this lane the worker still
    reads, and the half this test most needs to keep."""
    assert rules.MIRROR_EXIT_TAKE_BAND == 0.01
    # book 334: the rest at 0.55 (his own cent), whatever the moving venue's second read says
    p, b = _exit_world(0.549, 358)
    v = _MovingVenue([(0.54, 0.56), (0.53, 0.56)], held={SLUG: 716}, ioc_fill=358.0, lift=358.0)
    st = _tick(p, v, http=_mkt(358.0))
    assert [c[2:6] for c in _places(v)] == [(0.55, 358, True, GTC_TIF)] and _decisions(p) == ["exit_rest"]
    assert [c[7] for c in _places(v)] == [True]
    assert _census(st, "bid_moved") == 0 and _census(st, "exit_take_rested") == 0 and _census(st, "exit_take") == 0
    assert _census(st, "rest_placed") == 1 and "exit_take_rested" not in b["last_plan"]
    assert b["last_plan"]["maker"] == {"wire": 0.55, "bound": 0.55, "his_cent": 0.55, "clause": "his_cent",
                                       "side": SELL, "bid": 0.54, "ask": 0.56, "at": NOW, "hint": None}
    _no_band_word(p, st, b)
    # book 334 with the bid a cent further (0.53): the same rest at 0.55, the band bound recorded = the floor
    p, b = _exit_world(0.549, 358)
    v = _Venue(bid=0.53, ask=0.56, held={SLUG: 716}, ioc_fill=358.0, lift=358.0)
    st = _tick(p, v, http=_mkt(358.0))
    assert [c[2:6] for c in _places(v)] == [(0.55, 358, True, GTC_TIF)] and _decisions(p) == ["exit_rest"]
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "exit_take") == 0 and _bbos(v).count(SLUG) == 1
    assert b["last_plan"]["exit_out_of_tol"] == {"bid": 0.53, "ask": 0.56, "floor": 0.539, "band_floor": 0.539, "at": NOW}
    assert (b["last_plan"]["exit_take_band"], b["last_plan"]["exit_band_floor"]) == (0.54, 0.539)
    _no_band_word(p, st, b)
    # book 419: the bid 0.16 under the floor 0.17 -> the rest at 0.18
    p, b = _exit_world(0.18, 152, entry=0.20)
    v = _Venue(bid=0.16, ask=0.19, held={SLUG: 304})
    st = _tick(p, v, http=_mkt(152.0))
    assert [c[2:6] for c in _places(v)] == [(0.18, 152, True, GTC_TIF)] and _decisions(p) == ["exit_rest"]
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "exit_take") == 0 and b["ledger_net"] == 304
    assert b["last_plan"]["exit_out_of_tol"] == {"bid": 0.16, "ask": 0.19, "floor": 0.17, "band_floor": 0.17, "at": NOW}
    _no_band_word(p, st, b)
    # book 285: bids 0.70 / 0.71 / 0.82 -> the rest at 0.84, and E31: 0.83 too. The one IOC
    # at 0.83 (decision 'take') is retired; at every bid the exit rests at HIS cent 0.84
    for bid in (0.70, 0.71, 0.82):
        p, b = _exit_world(0.84, 432, entry=0.80)
        v = _Venue(bid=bid, ask=0.86, held={SLUG: 864}, ioc_fill=432.0, lift=432.0)
        st = _tick(p, v, http=_mkt(432.0))
        assert [c[2:6] for c in _places(v)] == [(0.84, 432, True, GTC_TIF)] and _decisions(p) == ["exit_rest"], bid
        assert _census(st, "exit_take") == 0 and _census(st, "exit_out_of_tol") == 1, bid
        _no_band_word(p, st, b)
    p, b = _exit_world(0.84, 432, entry=0.80)
    v = _Venue(bid=0.83, ask=0.86, held={SLUG: 864}, ioc_fill=432.0, lift=432.0)
    st = _tick(p, v, http=_mkt(432.0))
    # E31: (0.83, 432, True, IOC_TIF) / decision 'take' / exit_take 1 -> the rest at HIS 0.84
    assert [c[2:6] for c in _places(v)] == [(0.84, 432, True, GTC_TIF)] and _decisions(p) == ["exit_rest"]
    assert _census(st, "exit_take") == 0 and _census(st, "rest_placed") == 1 and b["ledger_net"] == 432
    assert _census(st, "maker_fill_at_create") == 1, "the taker at 0.83 lifted our 0.84 rest and paid the spread"
    _no_band_word(p, st, b)
    # the S4 cover: his 0.31, the ask at the ceiling 0.32. E31: the IOC at 0.32 THROUGH the
    # ask becomes the post-only rest at 0.31, decision 'cover' either way
    _shorts_on(monkeypatch)
    p, b, v = _flip_world(ioc_fill=100.0, lift=100.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.31, 300, True, GTC_TIF)] and _decisions(p) == ["cover"]
    assert _census(st, "short_cover_take") == 0 and _census(st, "short_cover_rest") == 1
    assert b["ledger_net"] == -200 and len(_opens(p)) == 1, "the partial's remainder stands as the rest it is"
    _no_band_word(p, st, b)
    # the S4 cover held outside the ceiling: the rest at his cent, the band bound = the ceiling
    p, b, v = _flip_world(ask=0.33)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.31, 300, True, GTC_TIF)] and _decisions(p) == ["cover"]
    assert _census(st, "short_cover_out_of_tol") == 1
    assert b["last_plan"]["exit_out_of_tol"] == {"bid": 0.30, "ask": 0.33, "ceiling": 0.32, "band_ceiling": 0.32, "at": NOW}
    _no_band_word(p, st, b)


# ------------------------------------------------------ (3) book 334's shape

def test_x1_book_334_at_band_two_the_standing_rest_is_cancelled_and_one_ioc_goes_at_the_band_cent(monkeypatch):
    """His 0.549: rest 0.55, take 0.54, take_band 0.53. A rest at 0.55
    standing, the bid at 0.53.

    RE-PINNED AT E31 (FILL lane 31, 2026-09-10). THE WHOLE SUBJECT OF THIS
    TEST IS RETIRED: `_exit_band_at`, `_exit_band_take`, `_exit_band_mark`
    and `_exit_take` are DELETED and no arm of `_act` can send an IOC, so
    the band cent 0.53 never reaches a wire at any value of the rail. What
    stands at its site is the pin this file most needs to keep: THE BAND
    CANNOT MOVE AN ORDER. At band two, on the very tick that used to cancel
    the rest and cross at 0.53, the rest at his own cent 0.55 STANDS --
    nothing cancelled, nothing placed, `open_order_pending` -- and the band
    is visible only where it always belonged, on the RECORD: exit_take_band
    0.53, exit_band_floor 0.529, and `exit_out_of_tol` carrying band_floor
    beside the floor. The taker who reaches 0.55 fills us there instead."""
    _band_two(monkeypatch)
    p, b = _exit_world(0.549, 358)
    o = p.add_order(b, side=SELL, wire=0.55, qty=358, kind="reduce", placed_ts=NOW - 100)
    v = _Venue(bid=0.53, ask=0.56, held={SLUG: 716}, ioc_fill=358.0, lift=358.0)
    v.rest("oid-1", "SELL", 0.55, 358)
    st = _tick(p, v, http=_mkt(358.0))
    lp = b["last_plan"]
    # the terms are byte for byte E4's + lane 3's: exit_terms is untouched by E31
    assert (lp["exit_take"], lp["exit_take_band"], lp["exit_rest"]) == (0.54, 0.53, 0.55)
    assert lp["exit_floor"] == 0.539 and lp["exit_band_floor"] == 0.529
    # E31: the cancel under 'take' and the IOC at 0.53 are both gone
    assert not _cancels(v) and p.orders[o["id"]]["state"] == "open" and p.orders[o["id"]]["reason"] is None
    assert not _places(v) and _decisions(p) == [] and b["ledger_net"] == 716
    assert _census(st, "exit_take_in_band") == 0 and _census(st, "exit_take") == 0 and _census(st, "take_placed") == 0
    assert _census(st, "open_order_pending") == 1 and _census(st, "cover_in_band") == 0
    assert _census(st, "exit_out_of_tol") == 1, "the band's own bound, on E4's held record"
    assert lp["exit_out_of_tol"] == {"bid": 0.53, "ask": 0.56, "floor": 0.539, "band_floor": 0.529, "at": NOW}
    assert "exit_band" not in lp, "no band mark: the mark left with _exit_band_mark"
    assert [(x["wire"], x["qty"], x["tif"]) for x in _opens(p)] == [(0.55, 358, "GTC")]
    assert not any(x["tif"] == "IOC" for x in p.orders.values()), "no IOC row on any road"
    for gone in ("_exit_band_at", "_exit_band_take", "_exit_band_mark", "_exit_take"):
        assert not hasattr(ml, gone), gone


def test_x1_book_334_at_band_two_the_re_read_withholds_the_band_ioc_and_the_rest_goes_the_same_tick(monkeypatch):
    """The same tick with a venue whose SECOND read is 0.52.

    RE-PINNED AT E31: the withholding this test pinned was `_ioc_reread`'s
    -- the band IOC's own re-read, which named `bid_moved` and rested the
    withheld quantity the same tick. `_ioc_reread` is deleted with the IOC.
    The rest at his cent 0.55 is now the ONLY thing that ever went out, and
    on this tick it is not even re-placed: it stands. So the venue's second
    quote is never read at all -- ONE bbo read, where the IOC's re-read made
    two -- and that is the honest pin at this site: E31's own touch-bound
    re-read (E31 C) runs immediately before a SEND, and this tick sends
    nothing. Whatever the second read would have said, no order crosses."""
    _band_two(monkeypatch)
    p, b = _exit_world(0.549, 358)
    p.add_order(b, side=SELL, wire=0.55, qty=358, kind="reduce", placed_ts=NOW - 100)
    v = _MovingVenue([(0.53, 0.56), (0.52, 0.56)], held={SLUG: 716}, ioc_fill=358.0, lift=358.0)
    v.rest("oid-1", "SELL", 0.55, 358)
    st = _tick(p, v, http=_mkt(358.0))
    lp = b["last_plan"]
    assert not _cancels(v) and not _places(v) and _decisions(p) == []
    assert _bbos(v).count(SLUG) == 1, "2 -> 1: no send, so no re-read"
    assert "bid_moved" not in lp and _census(st, "bid_moved") == 0
    assert _census(st, "exit_take_in_band") == 0 and _census(st, "exit_take") == 0
    assert _census(st, "exit_take_rested") == 0 and _census(st, "take_placed") == 0
    assert "exit_take_rested" not in lp and b["ledger_net"] == 716
    assert lp["exit_out_of_tol"] == {"bid": 0.53, "ask": 0.56, "floor": 0.539, "band_floor": 0.529, "at": NOW}
    opens = _opens(p)
    assert len(opens) == 1 and (opens[0]["wire"], opens[0]["qty"], opens[0]["tif"]) == (0.55, 358, "GTC")
    assert not hasattr(ml, "_ioc_reread"), "E31: the IOC's re-read is deleted with the IOC"
    # what stands at its site: the touch-bound re-read, made before a SEND alone
    assert "await _bbo(t, r.slug, book=True)" in inspect.getsource(ml._rest_reread)
    assert "wire = await _rest_reread(" in inspect.getsource(ml._place_reserved)


def test_x1_book_334_at_the_default_the_same_tick_is_held_with_the_band_bound_on_the_plan():
    """The bid 0.53 at the default: no cancel, no IOC, exit_out_of_tol
    {bid 0.53, floor 0.539, band_floor 0.539}, the rest stands."""
    p, b = _exit_world(0.549, 358)
    p.add_order(b, side=SELL, wire=0.55, qty=358, kind="reduce", placed_ts=NOW - 100)
    v = _Venue(bid=0.53, ask=0.56, held={SLUG: 716}, ioc_fill=358.0, lift=358.0)
    v.rest("oid-1", "SELL", 0.55, 358)
    st = _tick(p, v, http=_mkt(358.0))
    assert not _cancels(v) and not _places(v) and _bbos(v).count(SLUG) == 1
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "open_order_pending") == 1
    assert b["last_plan"]["exit_out_of_tol"] == {"bid": 0.53, "ask": 0.56, "floor": 0.539, "band_floor": 0.539, "at": NOW}
    assert b["last_plan"]["exit_take_band"] == 0.54 and "exit_band" not in b["last_plan"]
    _no_band_word(p, st, b)


# ------------------------------------------------------ (4) book 419's shape

def test_x1_book_419_at_band_two_the_take_then_the_band_take_then_the_hold(monkeypatch):
    """His 0.180: floor 0.17 / take 0.17, band_floor 0.16 / take_band 0.16,
    rest 0.18. Bids 0.17, 0.16 and 0.15.

    RE-PINNED AT E31: all three bids now send THE SAME ORDER -- the
    post-only rest at HIS cent 0.18 -- where lane 3 sent today's take at
    0.17, the band IOC at 0.16 (plus the withheld 138 resting at 0.18) and
    a hold. The band cent 0.16 and the tolerance cent 0.17 survive on the
    plan's record and reach no wire. What separates the three bids now is
    only what E4's held record says and how much of the rest a taker
    lifts."""
    _band_two(monkeypatch)
    # bid 0.17 (at the tolerance cent): the rest at 0.18, the whole 152 lifted there
    p, b = _exit_world(0.18, 152, entry=0.20)
    v = _Venue(bid=0.17, ask=0.19, held={SLUG: 304}, ioc_fill=152.0, lift=152.0)
    st = _tick(p, v, http=_mkt(152.0))
    assert [c[2:6] for c in _places(v)] == [(0.18, 152, True, GTC_TIF)] and _decisions(p) == ["exit_rest"]
    assert _census(st, "exit_take") == 0 and _census(st, "exit_take_in_band") == 0 and b["ledger_net"] == 152
    assert _census(st, "maker_fill_at_create") == 1 and _census(st, "exit_out_of_tol") == 0
    assert (b["last_plan"]["exit_take"], b["last_plan"]["exit_take_band"]) == (0.17, 0.16)
    # bid 0.16 (inside the band, past the tolerance): the SAME rest at 0.18. Where lane 3
    # crossed at 0.16 for 152 and rested the 138 remainder, the 152 rest stands and the
    # taker lifts 14 of it AT 0.18 -- the ledger reaches the same 290, two cents better
    p, b = _exit_world(0.18, 152, entry=0.20)
    v = _Venue(bid=0.16, ask=0.19, held={SLUG: 304}, ioc_fill=14.0, lift=14.0)
    st = _tick(p, v, http=_mkt(152.0))
    assert [c[2:6] for c in _places(v)] == [(0.18, 152, True, GTC_TIF)]
    assert _decisions(p) == ["exit_rest"] and b["ledger_net"] == 290
    assert _census(st, "exit_take_in_band") == 0 and _census(st, "exit_take") == 0 and _census(st, "exit_take_rested") == 0
    assert _census(st, "partial_fill") == 1 and "exit_take_rested" not in b["last_plan"]
    assert [(x["wire"], x["qty"], x["tif"]) for x in _opens(p)] == [(0.18, 152, "GTC")], \
        "the remainder is the SAME order, holding its queue -- never a second rest"
    assert "exit_band" not in b["last_plan"] and _census(st, "maker_fill_at_create") == 1
    assert b["last_plan"]["exit_out_of_tol"] == {"bid": 0.16, "ask": 0.19, "floor": 0.17, "band_floor": 0.16, "at": NOW}
    # bid 0.15 (outside the band): held by E4's record, the rest at 0.18, one bbo read
    p, b = _exit_world(0.18, 152, entry=0.20)
    v = _Venue(bid=0.15, ask=0.19, held={SLUG: 304}, ioc_fill=152.0, lift=152.0)
    st = _tick(p, v, http=_mkt(152.0))
    assert [c[2:6] for c in _places(v)] == [(0.18, 152, True, GTC_TIF)] and _decisions(p) == ["exit_rest"]
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "exit_take_in_band") == 0 and _bbos(v).count(SLUG) == 1
    assert b["last_plan"]["exit_out_of_tol"] == {"bid": 0.15, "ask": 0.19, "floor": 0.17, "band_floor": 0.16, "at": NOW}


# ------------------------------------------------------ (5) book 285's shape

def test_x1_book_285_the_band_is_measured_from_his_newest_reducing_fill_never_the_presets_vwap(monkeypatch):
    """His reducing fills step 0.84 then 0.72 (the preset's size-weighted
    0.840 against our 0.718): _his_level picks the NEWEST, so the terms
    read 0.72 -- floor 0.71 / take 0.71, band_floor 0.70 / take_band 0.70.

    RE-PINNED AT E31 AND THE SUBJECT SURVIVES INTACT: the level his exit is
    priced from is still HIS NEWEST REDUCING FILL and never the preset's
    VWAP -- it is now on the WIRE and not only on the record, because the
    exit rests AT that level. At every one of the three bids (0.69 outside
    the band, 0.70 at the band cent, 0.71 at the tolerance cent) the order
    is the same post-only rest at 0.72, and `min(place price) >= 0.70`
    holds a fortiori: the exit never leaves 0.72."""
    _band_two(monkeypatch)

    def world():
        p = _pool(fills=[_fill(M, "BUY", 864, 0.80, NOW - 3000), _fill(M, "SELL", 216, 0.84, NOW - 1000),
                         _fill(M, "SELL", 216, 0.72, NOW - 500)],
                  snap={M: 432.0, N: 0.0})
        return p, p.add_book(ledger=864, avg_cost=0.80)
    for bid, tol_rec in ((0.69, True), (0.70, True), (0.71, False)):
        p, b = world()
        v = _Venue(bid=bid, ask=0.74, held={SLUG: 864}, ioc_fill=432.0, lift=432.0)
        st = _tick(p, v, http=_mkt(432.0))
        lp = b["last_plan"]
        assert lp["exit_px"] == 0.72 and (lp["exit_take"], lp["exit_take_band"], lp["exit_rest"]) == (0.71, 0.70, 0.72), bid
        assert [c[2:6] for c in _places(v)] == [(0.72, 432, True, GTC_TIF)], bid
        assert _decisions(p) == ["exit_rest"] and b["ledger_net"] == 432, bid
        assert _census(st, "exit_take_in_band") == 0 and _census(st, "exit_take") == 0, bid
        assert (_census(st, "exit_out_of_tol") == 1) is tol_rec, bid
        assert min(c[2] for c in _places(v)) >= 0.72, "never a cent under his newest fill AT ALL"
        assert lp["exit_band_floor"] == 0.70 and "exit_band" not in lp, bid


# ------------------------------------------------------ (6) book 467's shape

def test_x1_book_467_the_band_reads_the_long_legs_own_newest_reducing_fill_not_the_flattened_short_side(monkeypatch):
    """A SHORT episode's fills (his BUY then SELL of the other token,
    1 - 0.70 = 0.30 in long space) sit before the long episode's (his BUY
    1140 at 0.50, his SELL 570 at 0.25): the exit prices off 0.25, the long
    leg's own newest reducing fill, never the old episode's 0.30.

    RE-PINNED AT E31: the level is unchanged and the subject stands. The
    band IOC at 0.23 is retired, so the 570 rests at 0.25 -- HIS cent --
    and the taker lifts 245 of that one order. The ledger reaches the same
    895 without a second rest and without crossing two cents."""
    _band_two(monkeypatch)
    p = _pool(fills=[_fill(N, "BUY", 400, 0.72, NOW - 6000), _fill(N, "SELL", 400, 0.70, NOW - 5000),
                     _fill(M, "BUY", 1140, 0.50, NOW - 3000), _fill(M, "SELL", 570, 0.25, NOW - 1000)],
              snap={M: 570.0, N: 0.0})
    b = p.add_book(ledger=1140, avg_cost=0.50)
    v = _Venue(bid=0.23, ask=0.27, held={SLUG: 1140}, ioc_fill=245.0, lift=245.0)
    st = _tick(p, v, http=_mkt(570.0))
    lp = b["last_plan"]
    assert lp["exit_px"] == 0.25 and (lp["exit_take"], lp["exit_take_band"], lp["exit_rest"]) == (0.24, 0.23, 0.25)
    assert [c[2:6] for c in _places(v)] == [(0.25, 570, True, GTC_TIF)]
    assert _decisions(p) == ["exit_rest"] and b["ledger_net"] == 895
    assert _census(st, "exit_take_in_band") == 0 and _census(st, "exit_take_rested") == 0
    assert _census(st, "partial_fill") == 1 and "exit_take_rested" not in lp
    assert [(x["wire"], x["qty"], x["tif"]) for x in _opens(p)] == [(0.25, 570, "GTC")]
    assert lp["exit_out_of_tol"] == {"bid": 0.23, "ask": 0.27, "floor": 0.24, "band_floor": 0.23, "at": NOW}


# ------------------------------------------------------ (7) book 347's shape

def test_x1_book_347_a_frozen_short_under_e16_is_unclocked_first_then_covers_in_band_on_his_witnessed_buy_back(monkeypatch):
    """SHORT, venue_ledger_disagree, his per-market read down. Tick 1: no
    clock on the prior plan -> `unclocked`, NOTHING sent (fail closed,
    unchanged). Tick 2: his SELL of 120 of the other token (a buy-back,
    0.30 in long space) after the clock -> the cover of 120 through _act,
    sized by _cover_qty on the frozen clamp `_frozen_venue` (the LEDGER
    -300 on E16's on-fill path, the walk unread), never more than the
    witnessed 120.

    RE-PINNED AT E31: the band arm is retired, so the two arms this test
    compared -- `cover_in_band` at band two (an IOC at 0.32, AT the ask)
    and the rest at floor(his) 0.30 at the default -- are now ONE. At both
    band values the frozen book covers with the same post-only rest at
    min(buy_wire(his 0.30), ask 0.32 - MAKER_TICK) = 0.30, decision
    'cover', census `short_cover_rest`, and the taker lifts the 120 there:
    ledger -180 at BOTH values, where lane 3 reached -180 only by crossing.
    THAT IS THE PIN THIS TEST NOW CARRIES: no value of MIRROR_EXIT_TAKE_BAND
    changes a single byte of what a frozen short sends. E16's freeze, the
    witness clamp, the quantity and the book's state are untouched."""
    _thaw_off(monkeypatch)
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500)]
    seen = []
    for band, ceiling in ((0.02, 0.32), (0.01, 0.31)):
        monkeypatch.setattr(rules, "MIRROR_EXIT_TAKE_BAND", band)
        p = _e16_pool(fills=fills, snap=None)
        b = _short_book(p, ledger=-300, state="frozen", frozen_reason="venue_ledger_disagree", frozen_ts=NOW - 100)
        st = _tick(p, _NoClose(held={SLUG: -300}, bid=0.30, ask=0.32), http=_unread())
        assert _plan_exit(b) == {"held": "frozen_venue_unread", "why": "his_market_read"}
        assert b["last_plan"]["frozen_witness"] == {"why": "his_market_read", "held": "unclocked"}
        assert _census(st, "cover_in_band") == 0 and _census(st, "frozen_reduce_on_fill") == 0
        p.fills = fills + [_fill(N, "SELL", 120, 0.70, NOW - 1000)]
        v2 = _NoClose(held={SLUG: -300}, bid=0.30, ask=0.32, ioc_fill=120.0, lift=120.0)
        st2 = _tick(p, v2, now=NOW + 15, http=_unread())
        assert "close" not in _kinds(v2)
        lp = b["last_plan"]
        # E4's / lane 3's terms are on the record at both band values, unchanged
        assert lp["exit_px"] == pytest.approx(0.30) and lp["exit_cover"] == 0.31 and lp["exit_rest"] == 0.30
        assert lp["exit_cover_band"] == ceiling and lp["exit_band_ceiling"] == pytest.approx(ceiling), band
        # and the ORDER is the same at both: one post-only rest at his own cent, 120 shares
        assert [c[1:] for c in _places(v2)] == [(SLUG, 0.30, 120, True, GTC_TIF, SHORT, True, None)], band
        assert _decisions(p) == ["cover"] and _census(st2, "cover_in_band") == 0, band
        assert _census(st2, "short_cover_take") == 0 and _census(st2, "short_cover_rest") == 1, band
        assert _census(st2, "frozen_reduce_on_fill") == 1 and "exit_band" not in lp, band
        assert lp["exit_out_of_tol"] == {"bid": 0.30, "ask": 0.32, "ceiling": 0.31,
                                         "band_ceiling": ceiling, "at": NOW + 15}, band
        assert b["ledger_net"] == -180 and b["state"] == "frozen", band
        seen.append((tuple(c[1:] for c in _places(v2)), b["ledger_net"], _decisions(p)))
    assert seen[0] == seen[1], "the band cannot move a frozen short's cover, its size or its ledger"


# ------------------------------------------- (8) book 661's shape, (9) Martinez 534's

def test_x1_book_661_a_partially_filled_cover_rest_re_quoted_carries_its_booked_fill_into_the_band_ioc(monkeypatch):
    """A cover rest of 300 at floor(his) 0.31 filled 89.24 (the row's
    shape), his 0.31: cover 0.32, cover_band 0.33, the ask at 0.33.

    RE-PINNED AT E31. The band IOC is retired, and with it the re-quote
    this test was named for: the ask reaching 0.33 is NOT a reason to
    cancel a rest that already sits at his own cent. `maker_compare_wire`
    returns the standing 0.31 (a BUY rest is never re-quoted with a rising
    ask that has not made room toward him), so the rest STANDS, its 89.24
    is booked at the walk exactly as before, and the queue this whole lane
    exists to keep is kept. The review's HIGH-2 / mutant M10 pin -- THE
    SIZE COMES FROM THE ROW'S REMAINDER, NEVER THE PLAN'S QUANTITY -- is
    kept below at the site where a cover is actually SENT: the no-rest
    cover, sized by _cover_qty at min(plan 300, leg, ceil(299.0)) = 299 at
    BOTH band values (mutant M22's site), where lane 3 could only pin it
    on an IOC."""
    _shorts_on(monkeypatch)
    _band_two(monkeypatch)
    p, b, v = _flip_world(ask=0.33, held={SLUG: -(300 - 89.24)}, ioc_fill=210.0, lift=210.0)
    o = p.add_order(b, side=BUY, wire=0.31, qty=300, kind="flatten_paired", placed_ts=NOW - 100)
    v.rest("oid-1", "BUY", 0.31, 300, filled=89.24, avg=0.31)
    st = _tick(p, v)
    # E31: no cancel, no IOC -- the rest at his cent stands and keeps its place in the queue
    assert not _cancels(v) and not _places(v) and p.orders[o["id"]]["state"] == "open"
    assert p.orders[o["id"]]["booked_filled"] == 89.24, "the partial is still booked at the walk"
    assert _census(st, "partial_fill") == 1 and _census(st, "open_order_pending") == 1
    assert _census(st, "cover_in_band") == 0 and _census(st, "short_cover_take") == 0
    assert not any(x["tif"] == "IOC" for x in p.orders.values())
    assert b["last_plan"]["exit_cover_band"] == 0.33 and "exit_band" not in b["last_plan"]
    assert b["last_plan"]["maker"]["wire"] == 0.31 and b["last_plan"]["maker"]["clause"] == "his_cent"
    assert b["last_plan"]["exit_out_of_tol"] == {"bid": 0.30, "ask": 0.33, "ceiling": 0.32,
                                                 "band_ceiling": 0.33, "at": NOW}
    # the ask at 0.33 leaves his own cent 0.31 reachable, so the maker wire IS the standing
    # wire and there is no cent to move to; and a wire that HAS moved past him would still
    # not drag the rest up with a rising ask
    assert rules.maker_wire(BUY, 0.31, 0.30, 0.33) == 0.31
    assert rules.maker_compare_wire(BUY, 0.31, 0.31, 0.31) == 0.31, "the standing wire is the maker wire"
    assert _census(st, "exit_take_rested") == 0, "no second rest: the one order keeps its queue"
    # THE SAME ROW WITH THE VENUE STILL READING THE WHOLE -300 AT THE WALK: the standing
    # row's 89.24 is booked before the plan is made, so the plan flattens -300 + 89.24 =
    # -210.76 -> qty 211 against a venue of -300. E31: the rest still stands; the plan's
    # quantity is the record it always was and no order is sent on it
    pw, bw, vw = _flip_world(ask=0.33, held={SLUG: -300}, ioc_fill=210.0, lift=210.0)
    ow = pw.add_order(bw, side=BUY, wire=0.31, qty=300, kind="flatten_paired", placed_ts=NOW - 100)
    vw.rest("oid-1", "BUY", 0.31, 300, filled=89.24, avg=0.31)
    stw = _tick(pw, vw)
    assert bw["last_plan"]["qty"] == 211 and bw["last_plan"]["venue"] == -300.0
    assert pw.orders[ow["id"]]["booked_filled"] == 89.24 and pw.orders[ow["id"]]["state"] == "open"
    assert not _places(vw) and not _cancels(vw) and _decisions(pw) == []
    assert _census(stw, "cover_in_band") == 0 and _census(stw, "venue_ledger_suspect") == 1
    # THE SIZING SURVIVES WHERE AN ORDER IS SENT (the review's HIGH-2 sibling; mutant M22):
    # a live short, no rest standing, the ledger -300 and the standing row reading 299.0 (one
    # share under, inside VENUE_LEDGER_TOL_SHARES: no freeze) -> the plan asks 300 and the
    # cover is min(300, leg 300, ceil(299.0)) = 299, never the plan's 300. E31: it is now a
    # post-only REST at min(buy_wire(his 0.31), ask - MAKER_TICK) = 0.31 at BOTH band values,
    # where lane 3 crossed at 0.33 / 0.32 -- so the band cannot change the cent OR the size
    sent = []
    for band, ask in ((0.02, 0.33), (0.01, 0.32)):
        monkeypatch.setattr(rules, "MIRROR_EXIT_TAKE_BAND", band)
        pn, bn, vn = _flip_world(ask=ask, held={SLUG: -299.0}, ioc_fill=299.0, lift=299.0)
        pn.rows[bn["standing_row_id"]]["filled_shares"] = 299.0
        stn = _tick(pn, vn)
        assert bn["last_plan"]["qty"] == 300 and bn["state"] == "live"
        assert [c[2:6] for c in _places(vn)] == [(0.31, 299, True, GTC_TIF)], _places(vn)
        assert _decisions(pn) == ["cover"] and _census(stn, "cover_in_band") == 0
        assert _census(stn, "short_cover_take") == 0 and _census(stn, "short_cover_rest") == 1
        sent.append([c[1:] for c in _places(vn)])
    assert sent[0] == sent[1], "no value of the band moves the cover's cent or its size"
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
    """E31: `_place`'s signature lost `take_first` / `in_band` / `on_add`
    with the take arms, so the stand-in that records what _act asks for
    records the shape that is left -- and the `tif` it records is the one
    _place refuses anything but."""

    def __init__(self, results):
        self.results, self.calls = list(results), []

    async def __call__(self, t, book, r, kind, side, wire, qty, his_px, p, plan, tif="GTC"):
        self.calls.append((kind, side, wire, int(qty), tif))
        res, filled = self.results.pop(0)
        return res


def test_x1_a_frozen_tripped_abandoned_or_non_terminal_book_sends_no_rest_after_a_band_ioc_and_the_prices_are_the_terms(monkeypatch):
    """RE-PINNED AT E31 (FILL lane 31, 2026-09-10). THE WHOLE SUBJECT IS
    RETIRED. This test drove `ml._exit_take` -- the band IOC followed, on a
    live book, by the withheld quantity resting at his cent the same tick
    -- and pinned that on a FROZEN, TRIPPED, ABANDONED or NON-TERMINAL book
    the second rest is never sent. `_exit_take`, `_exit_band_at`,
    `_exit_band_take` and `_exit_band_mark` are all deleted, and there is
    no first order for a second one to follow: an exit is ONE post-only
    rest.

    What stands at the site is stronger, and it is what this test now
    pins:
      (a) the four guards did not go with the arm -- they live at the ONE
          choke point every order passes, `_place` (t.cancel_all,
          t.abandoned) and `_act` (frozen, non-terminal), BEFORE any op, and
          `_place` refuses a non-GTC tif by name before all of them;
      (b) the exit's cent is `rules.maker_wire`'s, off his level and the
          TOUCH -- no line of `_wire_for` reads ex["take"], ex["take_band"]
          or ex["rest"], which is the mirror of the old pin that _exit_take
          read the terms and never the quote;
      (c) `exit_take_rested` can no longer be written at all: one exit,
          one order."""
    _band_two(monkeypatch)
    ex = rules.exit_terms(SELL, 0.31)
    assert (ex["take"], ex["take_band"], ex["rest"]) == (0.30, 0.29, 0.31), "exit_terms is untouched by E31"
    for gone in ("_exit_take", "_exit_band_at", "_exit_band_take", "_exit_band_mark", "_ioc_reread"):
        assert not hasattr(ml, gone), gone
    # (a) the guards, at the one choke point
    psrc = inspect.getsource(ml._place)
    assert psrc.index('if tif != "GTC":') < psrc.index("if t.cancel_all:") < psrc.index("if t.abandoned:")
    assert psrc.index('_mirror_stop("ioc_refused", w)') < psrc.index("if t.cancel_all:")
    asrc = inspect.getsource(ml._act)
    assert 'if book["id"] in t.nonterminal:' in asrc and 'plan["rest_cause"] = "frozen"' in asrc
    assert asrc.index('if book["id"] in t.nonterminal:') < asrc.index("wire = await _wire_for(p, his_px, r") \
        if "wire = await _wire_for(p, his_px, r" in asrc else True
    # driven: a tripped tick and an abandoned tick place NOTHING, by name, at any band
    for kw, want in (({"cancel_all": "overfill"}, "overfill"), ({"abandoned": True}, "tick_abandoned")):
        stats = ml._new_stats()
        monkeypatch.setattr(ml, "_current_stats", stats)
        t = types.SimpleNamespace(cancel_all=kw.get("cancel_all"), abandoned=kw.get("abandoned", False),
                                  nonterminal=set(), stats=stats)
        book = {"id": 7, "state": "live", "ledger_net": 300, "_held": 300.0, "intent": "ORDER_INTENT_BUY_LONG",
                "whale": "rn1"}
        r = types.SimpleNamespace(whale="rn1")
        plan = {}
        res = _run(ml._place(t, book, r, "reduce", SELL, 0.31, 200, 0.31, mi.Plan(SELL, 200, 0.31, "reduce"), plan))
        assert res == want, kw
        assert "ioc_refused" not in plan, kw
    # and a non-GTC tif is refused BEFORE either of them, on a tick that is neither
    stats = ml._new_stats()
    monkeypatch.setattr(ml, "_current_stats", stats)
    t = types.SimpleNamespace(cancel_all="overfill", abandoned=True, nonterminal={7}, stats=stats)
    plan = {}
    res = _run(ml._place(t, {"id": 7, "state": "live", "whale": "rn1"}, types.SimpleNamespace(whale="rn1"),
                         "take", SELL, 0.29, 200, 0.31, mi.Plan(SELL, 200, 0.31, "reduce"), plan, tif="IOC"))
    assert res == "ioc_refused" and stats["census"]["ioc_refused"] == 1
    assert plan["ioc_refused"] == {"tif": "IOC", "side": SELL, "wire": 0.29, "kind": "take"}
    # (b) the exit's cent comes from the maker clamp, never from a cent of exit_terms
    from tests.test_e31_maker_only import _code
    wsrc = inspect.getsource(ml._wire_for)
    assert "rules.maker_wire(side, his_px, r.bid, r.ask" in wsrc
    code = _code(ml._wire_for)
    for cent in ('ex [ "take" ]', 'ex [ "take_band" ]', 'ex [ "rest" ]', 'ex [ "cover_band" ]'):
        assert cent not in code, cent
    # (c) no second rest exists to be counted
    assert ml._new_stats()["census"]["exit_take_rested"] == 0
    assert 'plan["exit_take_rested"]' not in inspect.getsource(ml)
    assert '_mirror_stop("exit_take_rested"' not in inspect.getsource(ml)


def test_x1_the_band_sites_sit_after_the_tolerance_sites_and_the_witness_ratchet_and_re_read_are_untouched():
    """RE-PINNED AT E31 (FILL lane 31, 2026-09-10). This test pinned the
    SHAPE of the band's four sites in `_act`: a second `elif
    _exit_band_take(...)` after each of the two long-exit and two cover
    tolerance sites, each band call through `in_band=True`, each marked by
    `_exit_band_mark`. Every one of those sites left `_act` with the take
    arms, so counting them is no longer a statement about anything. What
    is left to pin -- and what this test now pins -- is that the band, and
    E4's tolerance with it, survive ONLY AS THE RECORD, at ONE site, and
    that the ratchet, the witness and the re-read still name nothing of
    the band:

      * the four `at_or_through` tolerance tests collapse to ONE, on the
        held record's guard, read through `side_at` / `bound_cent` so the
        long exit and the cover share it;
      * `_exit_held` -- which carries band_floor / band_ceiling onto the
        plan -- is called on THREE roads (the merged keep branch, the
        cover's placement road, the long exit's placement road);
      * the worker reads no band constant of its own, exactly as before:
        `rules.exit_terms` is its only reader;
      * `_ioc_reread` is gone and `_rest_reread` stands at its site, and
        neither the ratchet nor the witness ever named the band."""
    asrc = inspect.getsource(ml._act)
    # the four band sites and their marks: gone with the arms
    for gone in ("_exit_band_take(t, SELL, r, ex, plan)", "_exit_band_take(t, BUY, r, ex, plan)",
                 "_exit_band_at(", "_exit_band_mark(", "in_band=True)", 'ex["cover_band"]',
                 'ex["take_band"]', "_exit_take(", 'tif="IOC"'):
        assert gone not in asrc, gone
    for gone in ("_exit_band_at", "_exit_band_take", "_exit_band_mark", "_exit_take", "_ioc_reread",
                 "_take_band", "_short_take_band", "_entry_take", "_flatten_send"):
        assert not hasattr(ml, gone), gone
    # ONE tolerance test, shared by the long exit and the cover through side_at / bound_cent
    assert asrc.count("side_at = SELL if long_exit else BUY") == 1
    assert asrc.count('bound_cent = ex["take"] if long_exit else ex["cover"]') == 1
    assert asrc.count("if not rules.at_or_through(side_at, r.bid, r.ask, bound_cent):") == 1
    # E4's held record -- the band's own bound on the plan -- on all three roads
    assert asrc.count("_exit_held(t, r, ex, plan, w)") == 3
    hsrc = inspect.getsource(ml._exit_held)
    assert 'band_key = "band_floor" if "floor" in ex else "band_ceiling"' in hsrc
    # the worker reads no band constant of its own; exit_terms is the one reader
    assert "MIRROR_EXIT_TAKE_BAND" not in inspect.getsource(ml)
    assert '"MIRROR_EXIT_TAKE_BAND"' not in inspect.getsource(ml)
    assert 'MIRROR_EXIT_TAKE_BAND = capped_env("MIRROR_EXIT_TAKE_BAND", 0.01, floor=0.0)' in inspect.getsource(rules)
    # untouched by lane 3, and still untouched: no band word anywhere near them
    for fn in (ml._flatten_vanished, ml._frozen_exit, ml._frozen_reduce_on_fill,
               ml._s4_refusal, ml._cover_qty, ml._sell_qty, ml._rest_reread, rules.take_allowed,
               rules.at_or_through, rules.rest_decision, rules.band_cent, rules.take_in_band):
        s = inspect.getsource(fn)
        for name in ("take_band", "cover_band", "exit_band", "_exit_band_at", "MIRROR_EXIT_TAKE_BAND"):
            assert not re.search(rf"(?<![\w]){name}(?![\w])", s), (fn.__name__, name)
    for name in ("witnessed_ratchet", "reducing_since", "reducing_on"):
        assert "band" not in inspect.getsource(getattr(mi, name)), name
    # the re-read: E18's IOC re-read is gone, E31's touch-bound re-read stands at its site and
    # names nothing of the band, no wait and no day stop
    for fn in (ml._rest_reread, ml._exit_held, ml._order_open_his_exit, ml._wire_for):
        s = inspect.getsource(fn)
        assert "loss_stop" not in s and "min_wait_env" not in s and "_env_float" not in s, fn.__name__


def test_x1_059_absent_sends_no_band_ioc_the_exit_is_held_by_name_and_the_rest_goes_uncounted(monkeypatch):
    """The review's HIGH-1 (FILL_plan section 4: 'an uncounted band take is
    not sent'). The E18 fixture that hides the 059 columns, the band at
    0.02.

    RE-PINNED AT E31 (FILL lane 31, 2026-09-10). The rule this test guards
    -- NO BAND TAKE WITHOUT ITS WORD -- is kept by a stronger fact: there
    is no band take at any time, with or without the columns, so the guard
    `_exit_band_take` and its `exit_band_uncounted` stamp are deleted with
    it. What must still hold at this site, and is pinned below, is that the
    059 ABSENCE ITSELF is survived: the exit places the same post-only rest
    at his cent 0.18, the row goes out through the 050 INSERT (19 args, no
    decision column), the probe's name reaches the heartbeat, and no band
    word is written on any row on any road. The two shapes the test used to
    tell apart (the band IOC and the tolerance IOC) are now one rest."""
    _band_two(monkeypatch)
    monkeypatch.setattr(ml, "_order_cols_absent_logged", False)
    # the bid 0.16, inside the band and past the tolerance: no band IOC -- and no IOC at all
    p, b = _exit_world(0.18, 152, entry=0.20)
    p.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    v = _Venue(bid=0.16, ask=0.19, held={SLUG: 304}, ioc_fill=14.0, lift=14.0)
    st = _tick(p, v, http=_mkt(152.0))
    assert st["order_cols_absent"] == "UndefinedColumnError"
    assert [c[2:6] for c in _places(v)] == [(0.18, 152, True, GTC_TIF)], "no band IOC, and no take of any kind"
    assert [len(i) for i in _inserts(p)] == [19], "the 050 INSERT (no decision column): no band word can be written"
    assert not any(len(i) > 20 and i[20] in BAND_WORDS for i in _inserts(p))
    assert _census(st, "exit_take_in_band") == 0 and _census(st, "exit_out_of_tol") == 1 and _bbos(v).count(SLUG) == 1
    lp = b["last_plan"]
    assert lp["exit_out_of_tol"] == {"bid": 0.16, "ask": 0.19, "floor": 0.17, "band_floor": 0.16, "at": NOW}
    # E31: `exit_band_uncounted` went with `_exit_band_take`; there is no band send to withhold
    assert "exit_band_uncounted" not in lp and "exit_band" not in lp and b["ledger_net"] == 290
    assert not hasattr(ml, "_exit_band_take") and 'plan["exit_band_uncounted"]' not in inspect.getsource(ml)
    # the bid AT the tolerance cent under the same absence: the SAME rest, where lane 3 sent
    # today's IOC at 0.17. The columns' absence changes neither cent
    p2, b2 = _exit_world(0.18, 152, entry=0.20)
    p2.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    v2 = _Venue(bid=0.17, ask=0.19, held={SLUG: 304}, ioc_fill=152.0, lift=152.0)
    st2 = _tick(p2, v2, http=_mkt(152.0))
    assert [c[2:6] for c in _places(v2)] == [(0.18, 152, True, GTC_TIF)]
    assert "exit_band_uncounted" not in b2["last_plan"] and [len(i) for i in _inserts(p2)] == [19]
    # the S4 cover: the ask inside the band, the columns absent -> the rest at his cent
    _shorts_on(monkeypatch)
    p3, b3, v3 = _flip_world(ask=0.33)
    p3.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    st3 = _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.31, 300, True, GTC_TIF)] and _census(st3, "cover_in_band") == 0
    assert "exit_band_uncounted" not in b3["last_plan"]
    assert b3["last_plan"]["exit_out_of_tol"] == {"bid": 0.30, "ask": 0.33, "ceiling": 0.32, "band_ceiling": 0.33, "at": NOW}
    # at the default, the same: nothing stamped, the same rest
    monkeypatch.setattr(rules, "MIRROR_EXIT_TAKE_BAND", 0.01)
    p4, b4 = _exit_world(0.18, 152, entry=0.20)
    p4.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    v4 = _Venue(bid=0.16, ask=0.19, held={SLUG: 304})
    _tick(p4, v4, http=_mkt(152.0))
    assert "exit_band_uncounted" not in b4["last_plan"]
    assert [c[2:6] for c in _places(v4)] == [(0.18, 152, True, GTC_TIF)]
    # THE GUARD THAT REPLACES THE PURE ONE: `_place` refuses a non-GTC tif before an op, a
    # read, a row or a venue call -- so no take can be sent whether or not its word could be
    # written. The 059 columns gate the row's decision, never the order
    psrc = inspect.getsource(ml._place)
    assert psrc.index('if tif != "GTC":') < psrc.index("if t.cancel_all:")
    assert "_ioc_refused_log(book, kind, side, wire, tif)" in psrc


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
    # book 611's shape: his net still GROWING on the wake (adds only after the clock): never this
    # lane's name. Before E21 (FILL lane 10) the wake was `order_open`; E21 admits the ADDING wake
    # behind rules.MIRROR_FAST_ADD_REPLAN (the rest 30 s old is kept under the floor, nothing
    # cancelled) -- re-pinned under the switch OFF (today byte for byte) and ON (admitted)
    p3, b3, v3 = _rest_book([_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "BUY", 50, 0.32, NOW - 5)])
    _walk()
    rules.MIRROR_FAST_ADD_REPLAN = False
    try:
        fs3 = _fast(p3, v3)
    finally:
        rules.MIRROR_FAST_ADD_REPLAN = True
    assert _skips(fs3) == {CID: "order_open"} and _census(fs3, "order_open_his_exit") == 0
    # (the same shape with his add at his cent 0.31 -- the ask 0.32 above it, so no take -- and the
    # per-market read agreeing with his 350: the rest 30 s old is kept under the floor)
    p3b, b3b, v3b = _rest_book([_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "BUY", 50, 0.31, NOW - 5)])
    _walk()
    fs3b = _fast(p3b, v3b, http=_mkt(350.0))
    assert _skips(fs3b) == {} and _census(fs3b, "order_open_his_exit") == 0 and _census(fs3b, "fast_his_add") == 1
    assert _census(fs3b, "kept_min_life") == 1 and not _cancels(v3b) and not _places(v3b)
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
    # the gate itself: with no fills the clause is `order_open` byte for byte; the split reads fills only.
    # E21 (FILL lane 10) re-pinned the clause's text: this lane's exit test still runs FIRST, and the
    # adding wake's admission sits after it behind rules.MIRROR_FAST_ADD_REPLAN, else `order_open`
    gsrc = inspect.getsource(ml._fast_gate)
    assert ('if fills is not None and _order_open_his_exit(t, book, fills):\n            return "order_open_his_exit"\n'
            '        if not (fills is not None and rules.MIRROR_FAST_ADD_REPLAN and _order_open_his_add(t, book, fills)):\n'
            '            return "order_open"') in gsrc
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
    # T2 (FILL lane 4, two names), FILL lane 5 (three), E22 (FILL lane 22, four) and FILL lane 11 (one) landed after this lane and sit nearer the key (-16:-13 -> -26:-23) -- FILL lane 16 (one name) and E21 (FILL lane 10, six) landed first, so every index past this lane's six moved by seven more
    # E23 (FILL lane 23) placed its six names nearer the key (-26:-23 -> -32:-29, -27 / -28 / -29 -> -33 / -34 / -35)
    # FILL lane 24 (E24, the desk's hand) placed its four names nearer the key (-39:-36 -> -43:-40, -40 / -41 / -42 -> -44 / -45 / -46)
    assert keys[-67:-64] == NEW_NAMES
    assert keys[-68] == "take_in_band" and keys[-69] == "exit_take_rested" and keys[-70] == "wrong_sign_hold"
    assert keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-1] == "cand_terminal_skipped" and keys[-8:-4] == ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")
    assert len(set(keys)) == len(keys)
    for k in NEW_NAMES:
        assert ml._new_stats()["census"][k] == 0, k
    assert {"exit_take_in_band", "cover_in_band"} <= ml.QUIET_EXIT_PLANS and "order_open_his_exit" not in ml.QUIET_EXIT_PLANS
    src = inspect.getsource(ml)
    # E31 (FILL lane 31, 2026-09-10): the two BAND names lose their emit site with
    # `_exit_band_mark` and become DECLARED ZEROS -- they keep their place on CENSUS_KEYS and
    # in QUIET_EXIT_PLANS as the record of the rule they counted, and no line writes them.
    # `order_open_his_exit` is the FAST GATE's name, which this lane does not touch: its one
    # emit site stands and it is still emitted (test_x1_every_name_is_emitted_here drives it)
    assert not hasattr(ml, "_exit_band_mark") and not hasattr(ml, "_exit_band_take")
    for k in BAND_WORDS:
        assert src.count(f'_mirror_stop("{k}"') == 0, k
        assert ml._new_stats()["census"][k] == 0, k
    assert '_mirror_stop("order_open_his_exit", book.get("whale"))' in inspect.getsource(ml._fast_book)
    assert src.count('_mirror_stop("order_open_his_exit"') == 1
    # the worker reads no env of its own; the rail is rules' (capped_env, floor 0.0)
    assert '"MIRROR_EXIT_TAKE_BAND"' not in src
    # 059's comment stands as written; the docs restate its list with the two words
    sql = (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "('rest', 'take', 'cover', 'exit_rest'; 'take_in_band' is" in sql and "exit_take_in_band" not in sql


def test_x1_every_name_is_emitted_here(monkeypatch):
    """RE-PINNED AT E31: the lane's three names are no longer three emitted
    names. `exit_take_in_band` and `cover_in_band` are DECLARED ZEROS (their
    emit site left with `_exit_band_mark`), so the two worlds below are
    driven for the REST that stands where the band take stood, and the one
    name still emitted here is the fast gate's `order_open_his_exit`, which
    this lane does not touch. Driving all three is kept: the worker file's
    coverage read imports this."""
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
