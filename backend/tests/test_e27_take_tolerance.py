"""E27 (2026-09-09; FILL program lane 27): the entry takes at once inside
a band of TWO cents capped at 5% of the cost per share, on a LONG add
and -- for the first time -- on a SHORT add; the remainder rests at his
cent as today; exits untouched.

Owner (2026-09-09 ~21:1xZ, item 1 of the seven-item list "the only
thing that stands between us and him by portional standpoint"),
verbatim: "1. Yes, lets take it immediately with a tolerance that you
feel wont impact profitability".

THE TOLERANCE, from the rows on file (FILL_R5_counterfactuals.md 75-78,
at the missed classes' +3.46% ROI at his price; fills-missed 20:22Z,
h2022_db 2086-2089: missed_replace +0.0208, missed_open +0.0371,
partial +0.0237 agree): a 1c band converts 38.2% of the unfilled rests
for +130 of P&L, 2c 57.7% for +52, 4c 80.6% for -276, 5c 87.1% for
-440 -- so 2c is the widest band whose converted dollars stay
non-negative after the spread, and the band is CAPPED at 5% of the cost
per share so a cheap contract never pays a fifth of its stake for a
fill. The LONG side keeps lane 2's 1c floor (D1 is never narrowed); the
SHORT side has no floor (a band under a cent is no band).

THE RULE. rules.take_band_width(his, intent): long min(MIRROR_TAKE_BAND,
max(0.01, MIRROR_TAKE_BAND_FRAC x his)); short min(MIRROR_TAKE_BAND,
MIRROR_TAKE_BAND_FRAC x (1 - his)). rules.band_cent(his) reads the long
width when no band is given (every lane 2 caller reads the new default
through it); rules.short_band_cent(his) = sell_wire(his - band),
strictly under his sell cent; rules.short_take_in_band(bid, ask,
his_cent, band_cent) = the bid strictly under his cent and at or above
the band cent, the ask not read. The worker: mirror_live._take_band
stamps the width as `band` and `frac` {his_px, cost, width};
mirror_live._short_take_band mirrors it on a short book's add with the
SELL-side reads; _act's band arm sends the short's ONE SELL IOC through
the same _entry_take call (decision 'take_in_band', the SAME word --
the row's side / intent tell the sides apart), sized on the collateral
at the band cent, re-read on the bid, the remainder resting at the
short wire as today. THE SHORT'S AT-LEVEL TAKE (the review's HIGH-1):
the bid at or over his sell cent but under the short wire (the plan's
verdict at_level, where the lane as built rested at the ask's cent)
sends ONE SELL IOC at his cent through the same _entry_take (decision
'take', census take_at_his_level), behind the 059 columns, the same
wait, off with MIRROR_TAKE_BAND at 0; the locked book still takes at
the wire first.

THE RAILS. MIRROR_TAKE_BAND = capped_env(0.02, floor 0.0) and
MIRROR_TAKE_BAND_FRAC = capped_env(0.05, floor 0.0): the environment may
only LOWER either; 0.01 is lane 2's long band byte for byte; 0 is the
band off on both sides. No env_switch, no migration, no census name, no
new decision word; render-ops.yml gains take-band's THIRD read-only
statement, the short add's own (decision, bucket) table, in both places
(the review's HIGH-2; test_render_ops_take_band pins it).

Driven against the worker file's fakes (its autouse rails are
imported), test_e18_rest_life's moving venue and test_e9_fast_path's
fast tick, on the long world of lane 2 (his 0.52) and a short world at
his 0.65 in long space (he bought the other token at 0.35).
"""
import hashlib
import importlib
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import types

import pytest

from sportsassets.analytics import mirror as mi  # noqa: F401 -- the plan's module, read by the pins
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e18_rest_life import _MovingVenue, _bbos, _inserts
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _UndefinedColumn, _Venue, _armed, _cancels, _census, _fill, _his, _mkt, _places,
    _pool, _run, _s4_proved, _short_book, _short_world, _shorts_on, _tick,
)

BUY, SELL = rules.BUY, rules.SELL
SHORT = rules.ORDER_INTENT_SHORT
GTC_TIF = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
IOC_TIF = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
ROOT = pathlib.Path(__file__).resolve().parents[2]
VERDICTS = ("unread", "at_level", "off", "uncounted", "out", "waiting", "in_band")
# the functions the lane does NOT touch, hashed on 52e1d52 (the tip this
# lane was built on): the exit path, the re-read, the entry take's
# body, the short wire, the room, the admission and the catch-up
UNTOUCHED = {
    "exit_terms": ("2e4cd4a9edeb10a9", rules.exit_terms), "admission": ("a10630d6d3a3a62c", rules.admission),
    "open_catchup": ("6b9e8f2ffe1d3538", rules.open_catchup), "order_decision": ("b22c4fbc29d68662", rules.order_decision),
    "take_allowed": ("dc3079622052b6ff", rules.take_allowed), "at_or_through": ("4aece58b61ee21bc", rules.at_or_through),
    "rest_decision": ("b1962f2cbb21c6c4", rules.rest_decision), "room_scale": ("92e7b5e20e20f05b", rules.room_scale),
    "take_in_band": ("a185e3965cf3e0bb", rules.take_in_band), "buy_wire": ("861dd6ffd57e1283", rules.buy_wire),
    "sell_wire": ("a9cae307d9e6f2b2", rules.sell_wire), "buy_price": ("f9b961a63c4d2dc4", rules.buy_price),
    "_exit_take": ("750acd709c826566", ml._exit_take), "_ioc_reread": ("cd3dbab5e5819257", ml._ioc_reread),
    "_entry_take": ("2266c2b346674491", ml._entry_take), "_short_wire": ("25efb8c189941579", ml._short_wire),
    "_room_qty": ("458b3fea5e2d235b", ml._room_qty), "_place_reserved": ("6c83b8e547c83e0a", ml._place_reserved),
    "_place": ("ab568476817cf795", ml._place), "_fast_gate": ("1932811194268668", ml._fast_gate),
    "_fast_book": ("286e6fa4663c3887", ml._fast_book), "_wire_for": ("a2d57ccd3742dc61", ml._wire_for),
    "_flatten_send": ("001aa6d18a24e943", ml._flatten_send), "_flatten_vanished": ("22930dc6e3e85816", ml._flatten_vanished),
    "_frozen_exit": ("ef478fabdfa2ccc0", ml._frozen_exit), "_exit_terms": ("10411ad7900e6542", ml._exit_terms),
    "_exit_band_at": ("1297e55ea2a61f35", ml._exit_band_at), "_cover_qty": ("4662f515286becbe", ml._cover_qty),
    "_sell_qty": ("95533ab0c37193ae", ml._sell_qty),
}
# migration 059 on 52e1d52 (sha256[:16]): untouched. render-ops.yml AS THIS LANE LEAVES IT (the review's HIGH-2
# fold: take-band's third statement, the SHORT add's own table, both places) -- 2ea1e1f7fd9a35b0 on 52e1d52,
# a7fcb124867ad420 as the lane landed (4ee4e08); 6969bae6aa1536c1 after the mirror-tick preset (and the hourly's
# copy) gained the chain_listener and poller heartbeats (2026-09-09 23:4xZ, the read of FILL lane 26's sweep
# block and page_overflow: no preset read service_heartbeats for either worker) -- the take-band statements this
# lane pins are byte for byte the same; test_render_ops_take_band.py reads them by text.
# 791309dcc6841f2c then 0b09a1ed2074c6fd (full-game tails, the ne-sea rows verbatim) after the read-only `nfl-rows` preset (2026-09-10 00:4xZ, the owner's NFL question: his
# NFL rows with the shadow's verdict, the venue's league-code census on the Sep 10-15 dates, the '-nfl-'
# suffix shapes, the team-code witness) -- no existing preset changed.
# E29 (FILL lane 29, 2026-09-10): the mirror-hand-release preset beside mirror-register and its help-arm token --
# 0b09a1ed2074c6fd -> 2cb5a0a793839493 at landing over E28 and the nfl-rows read (pinned in the lane's worktree
# on 6c0830d as 6969bae6aa1536c1 -> 296c4007776a0b82); every read-only preset and the take-band statements byte
# for byte the same
RENDER_OPS_SHA = "2cb5a0a793839493"
MIGRATION_059_SHA = "a17a94df3a646918"
EXIT_LINES = (
    'MIRROR_EXIT_TAKE_BAND = capped_env("MIRROR_EXIT_TAKE_BAND", 0.01, floor=0.0)',
    "EXIT_BAND_INERT_AT = 0.01",
    'MIRROR_EXIT_TOL = capped_env("MIRROR_EXIT_TOL", 0.01, floor=0.0)',
)


def _sha(obj) -> str:
    return hashlib.sha256(inspect.getsource(obj).encode()).hexdigest()[:16]


def _sha_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


@pytest.fixture(autouse=True)
def _tolerance_on(_armed, monkeypatch):
    """The band at the code defaults (0.02 capped at 5%) for every test
    here, by name -- the worker file's fixture world holds the band at 0
    (its default quote sits a cent above his cent) and test_e14 holds
    it at 0.01; pinned against the runner's environment either way."""
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.02)
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND_FRAC", 0.05)
    yield


def _his_at(px, size=300):
    """His one BUY of the long token at `px`, 3,000 s ago; the book flat."""
    return _pool(fills=[_fill(M, "BUY", size, px, NOW - 3000)])


def _long_world(bid=0.53, ask=0.54, ioc_fill=300.0, his=0.52, **kw):
    """Lane 2's book 544 shape widened: his 0.52, the book flat, the ask
    TWO cents above his cent (the band cent 0.54)."""
    p = _his_at(his)
    b = p.add_book(ledger=0)
    v = _Venue(bid=bid, ask=ask, ioc_fill=ioc_fill, **kw)
    return p, b, v


def _short_at(his=0.65, long_size=100.0, other_size=400.0, **kw):
    """His net NEGATIVE on the fixture market with his last move the
    other token at 1 - `his`: his level for our short is `his` in long
    space (0.65: he paid 0.35 for the other token), the collateral a
    share 1 - his. ratio 1.0 x -300 is the target."""
    kw.setdefault("fills", _his(long_size, other_size=other_size, other_px=round(1.0 - his, 6)))
    kw.setdefault("snap", {M: long_size, N: other_size})
    return _short_world(**kw)


def _short_http():
    return _mkt(100.0, 400.0)


def _band_field(b):
    return b["last_plan"]["take_band"]


# the short world opens its book through the candidate stage on the first
# tick, whose market read is one quote read BEFORE the book's own (the
# long world pre-adds its flat book): every quote count on the short world
# carries it, and a _MovingVenue sequence on the short world spends its
# first pair there
SHORT_OPEN_READS = 1


# ------------------------------------------------------------- the rule

def test_e27_the_width_table_long_and_short_at_the_default_and_under_the_lowered_rails(monkeypatch):
    assert (rules.MIRROR_TAKE_BAND, rules.MIRROR_TAKE_BAND_FRAC) == (0.02, 0.05)
    # LONG: his 0.10 -> the 1c floor (0.11); 0.30 -> 0.015 -> 0.315 floors to 0.31 (a cent over);
    # 0.42 -> 0.021 capped at 0.02 -> 0.44; 0.80 -> 0.02 -> 0.82; 0.995 -> no cent on the ladder
    assert rules.take_band_width(0.10) == 0.01 and rules.band_cent(0.10) == 0.11
    assert rules.take_band_width(0.30) == 0.015 and rules.band_cent(0.30) == 0.31
    assert rules.take_band_width(0.42) == 0.02 and rules.band_cent(0.42) == 0.44
    assert rules.take_band_width(0.80) == 0.02 and rules.band_cent(0.80) == 0.82
    assert rules.take_band_width(0.995) == 0.02 and rules.band_cent(0.995) is None
    assert rules.band_cent(0.52) == 0.54 and rules.band_cent(0.479) == 0.49 and rules.band_cent(0.471) == 0.49
    # SHORT: his 0.65 -> 5% of 0.35 = 1.75c -> sell_wire(0.6325) = 0.64; 0.50 -> 2c -> 0.48;
    # 0.90 -> 0.5c -> None (under a cent: no band); 0.95 -> 0.25c -> None
    assert rules.take_band_width(0.65, SHORT) == 0.0175 and rules.short_band_cent(0.65) == 0.64
    assert rules.take_band_width(0.50, SHORT) == 0.02 and rules.short_band_cent(0.50) == 0.48
    assert rules.take_band_width(0.90, SHORT) == 0.005 and rules.short_band_cent(0.90) is None
    assert rules.take_band_width(0.95, SHORT) == 0.0025 and rules.short_band_cent(0.95) is None
    assert rules.short_band_cent(0.28) == 0.26 and rules.short_band_cent(0.32) == 0.30
    # the intent is the BOOK's: anything but the exact BUY_SHORT spelling is the long formula
    for intent in (None, "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_SELL_SHORT", True, "short"):
        assert rules.take_band_width(0.65, intent) == 0.02, intent
    # the sweep: at every cent of his the long width is inside [0.01, 0.02] and the cent is at
    # most the width over him; the short cent is at most the width under him and under his cent
    for c in range(1, 99):
        his = c / 100.0
        w = rules.take_band_width(his)
        assert 0.01 <= w <= 0.02 + 1e-12 and abs(w - min(0.02, max(0.01, 0.05 * his))) < 1e-9, his
        bc = rules.band_cent(his)
        assert bc is None or (rules.buy_wire(his) < bc <= his + w + 1e-9), (his, bc)
        ws = rules.take_band_width(his, SHORT)
        assert abs(ws - min(0.02, 0.05 * (1 - his))) < 1e-9, his
        sc = rules.short_band_cent(his)
        assert sc is None or (his - ws - 1e-9 <= sc < rules.sell_wire(his)), (his, sc)
        if ws < 0.01:
            assert sc is None, "a band under a cent is no band on the short side"
    for i in range(1, 990):
        his = round(i / 1000.0 + 0.0004, 6)
        bc, sc = rules.band_cent(his), rules.short_band_cent(his)
        assert bc is None or (rules.buy_wire(his) < bc <= his + rules.take_band_width(his) + 1e-9), (his, bc)
        assert sc is None or (his - rules.take_band_width(his, SHORT) - 1e-9 <= sc < rules.sell_wire(his)), (his, sc)
    # the same at MIRROR_TAKE_BAND 0.01: the LONG side is lane 2's cents byte for byte (the
    # width is exactly 0.01 at every price); the SHORT side is 1c where 5% of its collateral
    # admits a cent (his 0.65: 0.64; 0.50: 0.49) and no band where it does not (0.90)
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.01)
    for c in range(1, 99):
        his = c / 100.0
        assert rules.take_band_width(his) == 0.01 and rules.band_cent(his) == round(his + 0.01, 2), his
    assert rules.band_cent(0.479) == rules.band_cent(0.471) == 0.48 and rules.band_cent(0.10) == 0.11
    assert rules.band_cent(0.30) == 0.31 and rules.band_cent(0.42) == 0.43 and rules.band_cent(0.80) == 0.81
    assert rules.short_band_cent(0.65) == 0.64 and rules.short_band_cent(0.50) == 0.49
    assert rules.take_band_width(0.90, SHORT) == 0.005 and rules.short_band_cent(0.90) is None
    # at 0.005 (a lowering under a cent): honoured, never raised to the floor -- lane 2's own pins
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.005)
    assert rules.take_band_width(0.52) == 0.005 and rules.band_cent(0.52) is None and rules.band_cent(0.529) == 0.53
    assert rules.take_band_width(0.65, SHORT) == 0.005 and rules.short_band_cent(0.65) is None
    # at 0: None everywhere -- the band off on both sides
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.0)
    for his in (0.10, 0.30, 0.42, 0.52, 0.65, 0.80):
        assert rules.take_band_width(his) is None and rules.band_cent(his) is None, his
        assert rules.take_band_width(his, SHORT) is None and rules.short_band_cent(his) is None, his
    # MIRROR_TAKE_BAND_FRAC at 0: the long side the 1c floor alone, the short side None
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.02)
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND_FRAC", 0.0)
    for his in (0.10, 0.30, 0.42, 0.52, 0.80):
        assert rules.take_band_width(his) == 0.01 and rules.band_cent(his) == round(his + 0.01, 2), his
        assert rules.take_band_width(his, SHORT) is None and rules.short_band_cent(his) is None, his
    # a lowered frac narrows the cheap contracts first: 2% of 0.52 is 1.04c (long), 2% of 0.35 is 0.7c (short: no band)
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND_FRAC", 0.02)
    assert rules.take_band_width(0.52) == 0.0104 and rules.band_cent(0.52) == 0.53
    assert rules.take_band_width(0.65, SHORT) == 0.007 and rules.short_band_cent(0.65) is None
    assert rules.take_band_width(0.50, SHORT) == 0.01 and rules.short_band_cent(0.50) == 0.49


def test_e27_the_width_and_the_cents_fail_closed_on_every_unreadable_input(monkeypatch):
    # his price not a price in (0, 1), or a bool: None on both sides
    for his in (None, 0, 0.0, 1.0, 1.5, -0.2, True, "0.52", float("nan"), float("inf")):
        assert rules.take_band_width(his) is None and rules.take_band_width(his, SHORT) is None, his
        assert rules.band_cent(his) is None and rules.short_band_cent(his) is None, his
    # an explicit band: 0 / negative / NaN / inf / a bool / a string -> None; under a cent -> None on the short
    for band in (0, 0.0, -0.01, -1, float("nan"), float("inf"), True, "0.02"):
        assert rules.band_cent(0.52, band) is None and rules.short_band_cent(0.65, band) is None, band
    assert rules.short_band_cent(0.65, 0.005) is None and rules.short_band_cent(0.65, 0.0099) is None
    assert rules.short_band_cent(0.65, 0.01) == 0.64 and rules.short_band_cent(0.65, 0.02) == 0.63
    assert rules.band_cent(0.479, 0.005) == 0.48, "lane 2's long rule stands: a sub-cent band that crosses a cent"
    # the difference with no cent on the ladder, or not strictly under his sell cent
    assert rules.short_band_cent(0.01) is None and rules.short_band_cent(0.02) is None
    assert rules.short_band_cent(0.995) is None, "sell_wire caps at 0.99 = his own cent"
    # the constants unreadable / at or under 0 -> None (off); the frac unreadable -> None on both sides
    for bad in (None, "0.02", True, float("nan"), float("inf"), -0.01, 0.0):
        monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", bad)
        assert rules.take_band_width(0.52) is None and rules.take_band_width(0.65, SHORT) is None, bad
        assert rules.band_cent(0.52) is None and rules.short_band_cent(0.65) is None, bad
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.02)
    for bad in (None, "0.05", True, float("nan"), float("inf")):
        monkeypatch.setattr(rules, "MIRROR_TAKE_BAND_FRAC", bad)
        assert rules.take_band_width(0.52) is None and rules.take_band_width(0.65, SHORT) is None, bad
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND_FRAC", -1.0)
    assert rules.take_band_width(0.52) == 0.01 and rules.take_band_width(0.65, SHORT) is None, "a negative frac is 0"
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND_FRAC", 0.05)
    # the review's surviving mutant M3 (`b < 0.01 - 1e-9` -> `b <= 0.0`): the short side's no-floor rule at
    # the prices where the subtraction CROSSES a cent -- every whole-cent pin above passed the mutant
    assert rules.short_band_cent(0.651, 0.005) is None and rules.short_band_cent(0.6501, 0.0099) is None
    assert rules.take_band_width(0.9004, SHORT) == 0.00498 and rules.short_band_cent(0.9004) is None
    for i in range(1, 990):
        his = round(i / 1000.0 + 0.0004, 6)
        if rules.take_band_width(his, SHORT) < 0.01:
            assert rules.short_band_cent(his) is None, his


def test_e27_short_take_in_band_is_the_bid_strictly_under_his_cent_and_at_or_above_the_band_cent():
    hc, bc = 0.65, 0.64
    assert rules.short_take_in_band(0.65, 0.66, hc, bc) is False, "at his cent: today's rule, never the band"
    assert rules.short_take_in_band(0.66, 0.67, hc, bc) is False, "through his cent: today's rule"
    assert rules.short_take_in_band(0.64, 0.66, hc, bc) is True
    assert rules.short_take_in_band(0.63, 0.66, hc, bc) is False, "under the band cent: the rest"
    for bid in (None, 0.0, -0.0, 1e-12, 1.0, 1.5, 1e308, float("nan"), True, "0.64"):
        assert rules.short_take_in_band(bid, 0.66, hc, bc) is False, bid
    # the ask is not read
    for ask in (None, 0.0, 1.0, float("nan"), 0.63, 0.99):
        assert rules.short_take_in_band(0.64, ask, hc, bc) is True, ask
    # a cent nobody read, or a band cent not UNDER his cent: never in band
    assert rules.short_take_in_band(0.64, 0.66, None, bc) is False
    assert rules.short_take_in_band(0.64, 0.66, hc, None) is False
    assert rules.short_take_in_band(0.64, 0.66, hc, 0.65) is False and rules.short_take_in_band(0.64, 0.66, hc, 0.66) is False
    assert rules.short_take_in_band(0.64, 0.66, 0.0, bc) is False and rules.short_take_in_band(0.64, 0.66, hc, 0.0) is False
    assert rules.short_take_in_band(0.64, 0.66, 1.0, bc) is False and rules.short_take_in_band(0.64, 0.66, hc, 1.0) is False
    # the definition, in the module's own words; the long rule untouched
    src = inspect.getsource(rules.short_take_in_band)
    assert "not at_or_through(SELL, bid, ask, hc)" in src and "at_or_through(SELL, bid, ask, bc)" in src
    assert "bc < hc - 1e-9" in src
    assert _sha(rules.take_in_band) == UNTOUCHED["take_in_band"][0]
    # the decision word: the same 'take_in_band' on an add's IOC in band whatever the side (the row's
    # side / intent tell them apart); a cover, a reduce, a rest never
    assert rules.order_decision("add", True, False, True) == "take_in_band"
    assert rules.order_decision("add", True, True, True) == "cover_in_band"
    assert rules.order_decision("reduce", True, False, True) == "exit_take_in_band"
    assert rules.order_decision("add", False, False, True) == "rest"


# ------------------------------------------------------------- the rails

def test_e27_the_two_rails_only_lower_from_the_environment_in_a_fresh_interpreter():
    """Lane 0a's pattern: a fresh interpreter under the env, reading the
    constant it actually built (the reload in-process is test_e14's)."""
    src = inspect.getsource(rules)
    assert 'MIRROR_TAKE_BAND = capped_env("MIRROR_TAKE_BAND", 0.02, floor=0.0)' in src
    assert 'MIRROR_TAKE_BAND_FRAC = capped_env("MIRROR_TAKE_BAND_FRAC", 0.05, floor=0.0)' in src
    assert src.count('capped_env("MIRROR_TAKE_BAND"') == 1 and src.count('capped_env("MIRROR_TAKE_BAND_FRAC"') == 1
    for bad in ('min_wait_env("MIRROR_TAKE_BAND', 'env_switch("MIRROR_TAKE_BAND', '_env_float("MIRROR_TAKE_BAND',
                'unbounded_env("MIRROR_TAKE_BAND'):
        assert bad not in src, bad
    for name in ("MIRROR_TAKE_BAND", "MIRROR_TAKE_BAND_FRAC", "take_band_width", "band_cent", "short_band_cent",
                 "take_in_band", "short_take_in_band"):
        assert name in rules.__all__, name
    # the rail count: E27 adds ONE downward-only rail (capped_env 22 -> 23 on 52e1d52; landed over E25's
    # three rails and two waits, 0e72120: 25 -> 26, min_wait_env 7); no wait, no switch
    assert src.count("capped_env(") == 26 and src.count("min_wait_env(") == 7
    code = ("import json, sys; from sportsassets.analytics import mirror_live_rules as r;"
            " print(json.dumps([r.MIRROR_TAKE_BAND, r.MIRROR_TAKE_BAND_FRAC, r.band_cent(0.52),"
            " r.short_band_cent(0.65), r.band_cent(0.10), r.short_band_cent(0.90)]))")
    cases = (
        # (MIRROR_TAKE_BAND, MIRROR_TAKE_BAND_FRAC) -> (band, frac, long 0.52, short 0.65, long 0.10, short 0.90)
        ((None, None), (0.02, 0.05, 0.54, 0.64, 0.11, None)),
        (("0.03", None), (0.02, 0.05, 0.54, 0.64, 0.11, None)),     # a raise lands on the ceiling
        (("0.01", None), (0.01, 0.05, 0.53, 0.64, 0.11, None)),     # lane 2's band, one line away
        (("0", None), (0.0, 0.05, None, None, None, None)),        # the band off on both sides
        (("junk", None), (0.02, 0.05, 0.54, 0.64, 0.11, None)),
        (("-1", None), (0.0, 0.05, None, None, None, None)),       # under the floor lands on the floor
        ((None, "0.10"), (0.02, 0.05, 0.54, 0.64, 0.11, None)),    # a raise of the frac lands on 5%
        ((None, "0.02"), (0.02, 0.02, 0.53, None, 0.11, None)),    # 2% of 0.52 = 1.04c; 2% of 0.35 = 0.7c: no band
        ((None, "0"), (0.02, 0.0, 0.53, None, 0.11, None)),        # the long floor alone; the short off
        ((None, "junk"), (0.02, 0.05, 0.54, 0.64, 0.11, None)),
    )
    for (band, frac), want in cases:
        env = {k: v for k, v in os.environ.items() if k not in ("MIRROR_TAKE_BAND", "MIRROR_TAKE_BAND_FRAC")}
        if band is not None:
            env["MIRROR_TAKE_BAND"] = band
        if frac is not None:
            env["MIRROR_TAKE_BAND_FRAC"] = frac
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True,
                             cwd=str(ROOT / "backend"))
        assert json.loads(out.stdout.strip()) == list(want), (band, frac, out.stdout, out.stderr[-300:])
    # the exit's constants are byte for byte lane 3's (D2: exits within 1c, the exit band inert at 0.01)
    for line in EXIT_LINES:
        assert src.count(line) == 1, line
    assert rules.exit_terms(SELL, 0.549)["take_band"] == rules.exit_terms(SELL, 0.549)["take"] == 0.54
    assert rules.exit_terms(BUY, 0.31)["cover_band"] == rules.exit_terms(BUY, 0.31)["cover"] == 0.32
    assert rules.MIRROR_TAKE_AFTER_S == 0.0 and rules.MIRROR_CLIP_USD == 2500.0


# --------------------------------------------------- the worker, the long add

def test_e27_a_long_add_with_the_ask_two_cents_over_his_cent_sends_one_ioc_at_the_band_cent():
    """His 0.52, bid 0.53 / ask 0.54 -> ONE IOC at 0.54 (the band cent:
    0.52 + 0.02), `take_in_band`, the row's decision 'take_in_band',
    wire 0.54, his_level 0.52; the remainder rests at his cent 0.52."""
    p, b, v = _long_world(ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.54, 300, False, IOC_TIF)]
    assert _census(st, "take_in_band") == 1 and _census(st, "take_first") == 1 and _census(st, "take_placed") == 1
    assert _census(st, "take_at_his_level") == 0 and _census(st, "rest_placed") == 0
    assert b["ledger_net"] == 300 and b["open_order_id"] is None
    lp = b["last_plan"]
    assert lp["decision"] == "take_in_band"
    assert lp["take_band"] == {"his_cent": 0.52, "band": 0.02, "band_cent": 0.54, "bid": 0.53, "ask": 0.54,
                               "frac": {"his_px": 0.52, "cost": 0.52, "width": 0.02}, "verdict": "in_band"}
    ins = _inserts(p)
    assert len(ins) == 1 and len(ins[0]) == 23
    assert (ins[0][3], ins[0][4], ins[0][5], ins[0][8], ins[0][10], ins[0][11]) == ("take", BUY, "IOC", 0.52, 0.54, 300)
    assert ins[0][18] == "ORDER_INTENT_BUY_LONG" and ins[0][19] == 0.54 and ins[0][20] == "take_in_band"
    # the cents paid over him, readable off the row: 0.02, never more
    assert round(ins[0][10] - (int(ins[0][8] * 100) / 100.0), 2) == 0.02
    assert _bbos(v).count(SLUG) == 2, "the tick's read and the re-read before the send"
    # a partial fill: the remainder rests at buy_price(0.52, 0.53) = 0.52, decision 'rest'
    p2, b2, v2 = _long_world(ioc_fill=100.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.54, 300, False, IOC_TIF), (0.52, 200, False, GTC_TIF)]
    assert b2["ledger_net"] == 100 and _census(st2, "rest_placed") == 1
    assert [(a[5], a[10], a[11], a[20]) for a in _inserts(p2)] == [("IOC", 0.54, 300, "take_in_band"),
                                                                  ("GTC", 0.52, 200, "rest")]
    # expires 0: the whole quantity rests at his cent, one open GTC order, two ops
    p3, b3, v3 = _long_world(ioc_fill=0.0)
    st3 = _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.54, 300, False, IOC_TIF), (0.52, 300, False, GTC_TIF)]
    assert b3["ledger_net"] == 0 and st3["ops"] == 2 and _inserts(p3)[1][20] == "rest"
    assert [o["tif"] for o in p3.orders.values() if o["state"] == "open"] == ["GTC"]
    # the ask ONE cent over (lane 2's shape): still in band, the IOC limited at the band cent 0.54
    p4, b4, v4 = _long_world(bid=0.52, ask=0.53, ioc_fill=300.0)
    st4 = _tick(p4, v4)
    assert [c[2:6] for c in _places(v4)] == [(0.54, 300, False, IOC_TIF)] and _census(st4, "take_in_band") == 1
    assert b4["last_plan"]["take_band"]["verdict"] == "in_band" and _inserts(p4)[0][10] == 0.54


def test_e27_a_long_add_with_the_ask_three_cents_over_rests_as_today_and_at_or_under_his_cent_takes_at_level():
    # three cents over: out, the rest at buy_price(0.52, 0.54) = 0.52, no re-read
    p, b, v = _long_world(bid=0.54, ask=0.55, ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)]
    assert _census(st, "take_in_band") == 0 and _census(st, "take_placed") == 0 and _census(st, "rest_placed") == 1
    assert _bbos(v).count(SLUG) == 1 and b["last_plan"]["take_band"]["verdict"] == "out"
    assert b["last_plan"]["take_band"]["band_cent"] == 0.54 and _inserts(p)[0][20] == "rest"
    # Martinez 534's shape (his 0.61, the market 0.81 / 0.82): out, as under lane 2
    p1 = _his_at(0.61)
    b1 = p1.add_book(ledger=0)
    v1 = _Venue(bid=0.81, ask=0.82, ioc_fill=300.0)
    st1 = _tick(p1, v1)
    assert [c[2:6] for c in _places(v1)] == [(0.61, 300, False, GTC_TIF)]
    assert b1["last_plan"]["take_band"]["verdict"] == "out" and b1["last_plan"]["take_band"]["band_cent"] == 0.63
    assert _census(st1, "take_in_band") == 0
    # at his cent: the at-level take FIRST, decision 'take', never the band
    p2, b2, v2 = _long_world(bid=0.51, ask=0.52, ioc_fill=300.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.52, 300, False, IOC_TIF)]
    assert _census(st2, "take_at_his_level") == 1 and _census(st2, "take_in_band") == 0
    assert _inserts(p2)[0][20] == "take" and b2["last_plan"]["take_band"]["verdict"] == "at_level"
    # through his cent: the same
    p3, b3, v3 = _long_world(bid=0.50, ask=0.51, ioc_fill=300.0)
    st3 = _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.52, 300, False, IOC_TIF)] and _census(st3, "take_in_band") == 0
    assert b3["last_plan"]["take_band"]["verdict"] == "at_level"


def test_e27_the_cheap_contract_floors_at_a_cent_and_the_mid_price_caps_at_the_frac():
    """His 0.10: 5% is 0.5c, the floor is 1c -> the ask at 0.11 takes at
    0.11, at 0.12 rests. His 0.30: 5% is 1.5c -> the ask at 0.31 takes
    at 0.31 (0.315 floors to it), at 0.32 rests -- never a fifth of the
    stake for a fill."""
    p = _his_at(0.10)
    b = p.add_book(ledger=0)
    v = _Venue(bid=0.10, ask=0.11, ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.11, 300, False, IOC_TIF)] and _census(st, "take_in_band") == 1
    assert b["last_plan"]["take_band"]["band"] == 0.01 and b["last_plan"]["take_band"]["frac"] == {"his_px": 0.1, "cost": 0.1, "width": 0.01}
    p2 = _his_at(0.10)
    b2 = p2.add_book(ledger=0)
    v2 = _Venue(bid=0.11, ask=0.12, ioc_fill=300.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.10, 300, False, GTC_TIF)] and _census(st2, "take_in_band") == 0
    assert b2["last_plan"]["take_band"]["verdict"] == "out"
    p3 = _his_at(0.30)
    b3 = p3.add_book(ledger=0)
    v3 = _Venue(bid=0.30, ask=0.31, ioc_fill=300.0)
    st3 = _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.31, 300, False, IOC_TIF)] and _census(st3, "take_in_band") == 1
    assert b3["last_plan"]["take_band"] == {"his_cent": 0.30, "band": 0.015, "band_cent": 0.31, "bid": 0.30, "ask": 0.31,
                                            "frac": {"his_px": 0.30, "cost": 0.30, "width": 0.015}, "verdict": "in_band"}
    p4 = _his_at(0.30)
    b4 = p4.add_book(ledger=0)
    v4 = _Venue(bid=0.31, ask=0.32, ioc_fill=300.0)
    st4 = _tick(p4, v4)
    assert [c[2:6] for c in _places(v4)] == [(0.30, 300, False, GTC_TIF)] and _census(st4, "take_in_band") == 0
    assert b4["last_plan"]["take_band"]["verdict"] == "out"


def test_e27_under_mirror_take_band_0_01_the_long_side_is_lane_2_byte_for_byte(monkeypatch):
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.01)
    p, b, v = _long_world(bid=0.52, ask=0.53, ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.53, 300, False, IOC_TIF)] and _census(st, "take_in_band") == 1
    assert b["last_plan"]["take_band"] == {"his_cent": 0.52, "band": 0.01, "band_cent": 0.53, "bid": 0.52, "ask": 0.53,
                                           "frac": {"his_px": 0.52, "cost": 0.52, "width": 0.01}, "verdict": "in_band"}
    assert _inserts(p)[0][10] == 0.53 and round(_inserts(p)[0][10] - (int(_inserts(p)[0][8] * 100) / 100.0), 2) == 0.01
    # two cents over under the 1c band: out, as lane 2 pinned it
    p2, b2, v2 = _long_world(bid=0.53, ask=0.54, ioc_fill=300.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.52, 300, False, GTC_TIF)] and _census(st2, "take_in_band") == 0
    assert b2["last_plan"]["take_band"]["verdict"] == "out" and b2["last_plan"]["take_band"]["band_cent"] == 0.53
    # at 0 on both sides: the rest, verdict `off`, band None
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.0)
    p3, b3, v3 = _long_world(bid=0.52, ask=0.53, ioc_fill=300.0)
    st3 = _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.52, 300, False, GTC_TIF)] and _census(st3, "take_in_band") == 0
    assert b3["last_plan"]["take_band"]["verdict"] == "off" and b3["last_plan"]["take_band"]["band"] is None
    _shorts_on(monkeypatch)
    p4 = _short_at()
    v4 = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    st4 = _tick(p4, v4, http=_short_http())
    b4 = next(iter(p4.books.values()))
    assert [c[1:6] for c in _places(v4)] == [(SLUG, 0.66, 300, False, GTC_TIF)] and _census(st4, "take_in_band") == 0
    assert b4["last_plan"]["take_band"]["verdict"] == "off" and b4["last_plan"]["take_band"]["band"] is None


# -------------------------------------------------- the worker, the short add

def test_e27_a_short_add_with_the_bid_a_cent_under_his_cent_sends_one_sell_ioc_at_the_band_cent(monkeypatch):
    """His level 0.65 in long space (he paid 0.35 for the other token),
    the bid 0.64 / ask 0.66 -> ONE SELL IOC at 0.64 (sell_wire(0.65 -
    0.0175) = 0.64) for 300, intent BUY_SHORT, `take_in_band`, the row's
    decision 'take_in_band', wire 0.64, his_level 0.65; the remainder
    rests at the short wire 0.66 (ceil(max(his, ask)): today's rest,
    byte for byte) with decision 'rest'."""
    _shorts_on(monkeypatch)
    p = _short_at()
    v = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    st = _tick(p, v, http=_short_http())
    b = next(iter(p.books.values()))
    assert b["intent"] == SHORT and b["target"] == -300
    assert [c[1:] for c in _places(v)] == [(SLUG, 0.64, 300, False, IOC_TIF, SHORT, False, None)]
    assert _census(st, "take_in_band") == 1 and _census(st, "take_first") == 1 and _census(st, "take_placed") == 1
    assert _census(st, "take_at_his_level") == 0 and _census(st, "rest_placed") == 0 and _census(st, "short_open") == 1
    assert b["ledger_net"] == -300 and b["open_order_id"] is None
    lp = b["last_plan"]
    assert lp["decision"] == "take_in_band" and lp["side"] == SELL
    assert lp["take_band"] == {"his_cent": 0.65, "band": 0.0175, "band_cent": 0.64, "bid": 0.64, "ask": 0.66,
                               "frac": {"his_px": 0.65, "cost": 0.35, "width": 0.0175}, "verdict": "in_band"}
    ins = _inserts(p)
    assert len(ins) == 1 and len(ins[0]) == 23
    assert (ins[0][3], ins[0][4], ins[0][5], ins[0][8], ins[0][10], ins[0][11]) == ("take", SELL, "IOC", 0.65, 0.64, 300)
    assert ins[0][18] == SHORT and ins[0][20] == "take_in_band"
    # the cents given up under him, readable off the row: ceil(his_level) - wire = 0.01, never more than the width
    assert round((-(-ins[0][8] * 100 // 1) / 100.0) - ins[0][10], 2) == 0.01
    assert _bbos(v).count(SLUG) == SHORT_OPEN_READS + 2, "the tick's read and the re-read before the send"
    o = next(iter(p.orders.values()))
    assert (o["kind"], o["side"], o["tif"], o["intent"], o["wire"], o["state"]) == ("take", SELL, "IOC", SHORT, 0.64, "filled")
    # a partial fill (100): the remainder 200 rests at the short wire 0.66, decision 'rest'
    p2 = _short_at()
    v2 = _Venue(bid=0.64, ask=0.66, ioc_fill=100.0)
    st2 = _tick(p2, v2, http=_short_http())
    b2 = next(iter(p2.books.values()))
    assert [c[1:6] for c in _places(v2)] == [(SLUG, 0.64, 300, False, IOC_TIF), (SLUG, 0.66, 200, False, GTC_TIF)]
    assert b2["ledger_net"] == -100 and b2["open_order_id"] is not None
    assert _census(st2, "take_in_band") == 1 and _census(st2, "rest_placed") == 1
    assert [(a[5], a[10], a[11], a[18], a[20]) for a in _inserts(p2)] == [("IOC", 0.64, 300, SHORT, "take_in_band"),
                                                                         ("GTC", 0.66, 200, SHORT, "rest")]
    assert b2["last_plan"]["decision"] == "rest" and b2["last_plan"]["take_band"]["verdict"] == "in_band"
    # expires 0: the whole 300 rests at the short wire 0.66, one open GTC order, two ops
    p3 = _short_at()
    v3 = _Venue(bid=0.64, ask=0.66, ioc_fill=0.0)
    st3 = _tick(p3, v3, http=_short_http())
    b3 = next(iter(p3.books.values()))
    assert [c[1:6] for c in _places(v3)] == [(SLUG, 0.64, 300, False, IOC_TIF), (SLUG, 0.66, 300, False, GTC_TIF)]
    assert b3["ledger_net"] == 0 and st3["ops"] == 2 and _inserts(p3)[1][20] == "rest"
    assert [o["tif"] for o in p3.orders.values() if o["state"] == "open"] == ["GTC"]
    # an ADD onto a held short (-100 held, his net -300): the IOC for the 200, the same word
    p4 = _short_at()
    b4 = _short_book(p4, ledger=-100, avg=0.66)
    v4 = _Venue(bid=0.64, ask=0.66, ioc_fill=200.0, held={SLUG: -100})
    st4 = _tick(p4, v4, http=_short_http())
    assert [c[1:6] for c in _places(v4)] == [(SLUG, 0.64, 200, False, IOC_TIF)]
    assert _census(st4, "take_in_band") == 1 and _census(st4, "short_add") == 1 and b4["ledger_net"] == -300
    assert _inserts(p4)[0][20] == "take_in_band" and b4["last_plan"]["take_band"]["verdict"] == "in_band"


def test_e27_a_short_add_with_the_bid_two_cents_under_rests_out_and_at_or_over_his_cent_is_todays_rule(monkeypatch):
    _shorts_on(monkeypatch)
    # the bid two cents under (0.63): out -- the rest at the short wire 0.66, no re-read
    p = _short_at()
    v = _Venue(bid=0.63, ask=0.66, ioc_fill=300.0)
    st = _tick(p, v, http=_short_http())
    b = next(iter(p.books.values()))
    assert [c[1:6] for c in _places(v)] == [(SLUG, 0.66, 300, False, GTC_TIF)]
    assert _census(st, "take_in_band") == 0 and _census(st, "take_placed") == 0 and _census(st, "rest_placed") == 1
    assert _bbos(v).count(SLUG) == SHORT_OPEN_READS + 1 and b["last_plan"]["take_band"]["verdict"] == "out"
    assert b["last_plan"]["take_band"]["band_cent"] == 0.64 and _inserts(p)[0][20] == "rest"
    # the bid AT his cent (0.65): `at_level` -- the short's at-level take (the review's HIGH-1 fold):
    # ONE SELL IOC at his cent, decision 'take', never the band's word (was today's rest at 0.66)
    p2 = _short_at()
    v2 = _Venue(bid=0.65, ask=0.66, ioc_fill=300.0)
    st2 = _tick(p2, v2, http=_short_http())
    b2 = next(iter(p2.books.values()))
    assert [c[1:6] for c in _places(v2)] == [(SLUG, 0.65, 300, False, IOC_TIF)] and _census(st2, "take_in_band") == 0
    assert _census(st2, "take_at_his_level") == 1
    assert b2["last_plan"]["take_band"]["verdict"] == "at_level" and _inserts(p2)[0][20] == "take"
    # a locked book at 0.66 / 0.66 (the bid at the short wire): the at-level take FIRST,
    # decision 'take' at the wire, never the band
    p3 = _short_at()
    v3 = _Venue(bid=0.66, ask=0.66, ioc_fill=300.0)
    st3 = _tick(p3, v3, http=_short_http())
    b3 = next(iter(p3.books.values()))
    assert [c[1:6] for c in _places(v3)] == [(SLUG, 0.66, 300, False, IOC_TIF)]
    assert _census(st3, "take_at_his_level") == 1 and _census(st3, "take_in_band") == 0
    assert _inserts(p3)[0][20] == "take" and b3["last_plan"]["take_band"]["verdict"] == "at_level"
    # his 0.90 (the width 0.5c, under a cent): `off` -- no band, the rest as today, whatever the bid
    p4 = _short_at(his=0.90)
    v4 = _Venue(bid=0.89, ask=0.91, ioc_fill=300.0)
    st4 = _tick(p4, v4, http=_short_http())
    b4 = next(iter(p4.books.values()))
    assert [c[1:6] for c in _places(v4)] == [(SLUG, 0.91, 300, False, GTC_TIF)] and _census(st4, "take_in_band") == 0
    assert b4["last_plan"]["take_band"]["verdict"] == "off" and b4["last_plan"]["take_band"]["band"] == 0.005
    assert b4["last_plan"]["take_band"]["frac"] == {"his_px": 0.90, "cost": 0.10, "width": 0.005}
    # the sub-cent width at a FRACTIONAL level (the review's M3): his 0.9004 (the width 0.00498), the bid 0.90
    # under his sell cent 0.91 -> `off`, the rest at ceil(max(his, ask)) = 0.92, never an IOC at 0.90
    p5 = _short_at(his=0.9004)
    v5 = _Venue(bid=0.90, ask=0.92, ioc_fill=300.0)
    st5 = _tick(p5, v5, http=_short_http())
    b5 = next(iter(p5.books.values()))
    assert [c[1:6] for c in _places(v5)] == [(SLUG, 0.92, 300, False, GTC_TIF)] and _census(st5, "take_in_band") == 0
    assert b5["last_plan"]["take_band"]["verdict"] == "off" and b5["last_plan"]["take_band"]["band"] == 0.00498


def test_e27_a_short_reduce_with_the_bid_inside_the_band_is_never_band_taken(monkeypatch):
    """His net moved up (-300 -> -100: a partial buy-back) on a short of
    300 at 0.66; the bid 0.64 sits inside what the ADD's band would be
    at his 0.65 -- but the plan is a REDUCE (a BUY cover): the cover
    path's own rule, no `take_band` on the plan, no `take_in_band`."""
    _shorts_on(monkeypatch)
    p = _short_at(long_size=300.0, other_size=400.0)
    _s4_proved(p)
    b = _short_book(p, ledger=-300, avg=0.66)
    v = _Venue(bid=0.64, ask=0.68, held={SLUG: -300})
    st = _tick(p, v, http=_mkt(300.0, 400.0))
    assert b["target"] == -100 and b["last_plan"]["side"] == BUY and b["last_plan"]["qty"] == 200
    assert "take_band" not in b["last_plan"] and _census(st, "take_in_band") == 0
    assert [c[5] for c in _places(v)] == [GTC_TIF], "the cover rests (the ask 0.68 is past the ceiling); no IOC"
    assert _inserts(p)[0][20] == "cover" and _inserts(p)[0][18] == "ORDER_INTENT_SELL_SHORT"
    assert b["last_plan"]["decision"] == "cover"


def test_e27_a_long_exit_is_untouched_and_the_exit_path_is_hashed_against_the_tip():
    # the reduce leg never reads the band (a SELL plan: no `take_band` on the plan), decision 'exit_rest'
    p6 = _pool(fills=[_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "SELL", 200, 0.31, NOW - 1000)],
               snap={M: 100.0, N: 0.0})
    b6 = p6.add_book(ledger=300, avg_cost=0.31)
    v6 = _Venue(bid=0.29, ask=0.32, held={SLUG: 300})
    st6 = _tick(p6, v6)
    assert [c[2:6] for c in _places(v6)] == [(0.31, 200, True, GTC_TIF)] and "take_band" not in b6["last_plan"]
    assert _census(st6, "take_in_band") == 0 and _inserts(p6)[0][20] == "exit_rest"
    assert b6["last_plan"]["exit_take_band"] == b6["last_plan"]["exit_take"] == 0.30, "lane 3's band inert"
    # the untouched functions, hashed on 52e1d52
    for name, (digest, fn) in UNTOUCHED.items():
        assert _sha(fn) == digest, name
    # none of the exit path names the entry band's words
    for fn in (ml._exit_take, ml._flatten_send, ml._flatten_vanished, ml._frozen_exit, ml._frozen_reduce_on_fill,
               ml._cover_qty, ml._sell_qty, ml._entry_take, ml._ioc_reread, rules.exit_terms, rules.take_allowed,
               rules.at_or_through, rules.rest_decision, rules.admission, rules.open_catchup):
        s = inspect.getsource(fn)
        for name in ("take_band_width", "short_band_cent", "short_take_in_band", "MIRROR_TAKE_BAND_FRAC",
                     "_short_take_band"):
            assert not re.search(rf"(?<![\w]){name}(?![\w])", s), (fn.__name__, name)
    # the short's reader names nothing of the exit's
    ssrc = inspect.getsource(ml._short_take_band)
    for name in ("cover_band", "exit_band", "_exit_band_at", "MIRROR_EXIT_TAKE_BAND", "exit_take_in_band", "cover_in_band"):
        assert name not in ssrc, name


# ------------------------------------------------------ the paths, the guards

def test_e27_the_fast_wake_band_takes_on_both_sides_through_the_same_functions(monkeypatch):
    # LONG: a woken flat long with the ask two cents over -> the band IOC at 0.54 on the wake
    p, b, v = _long_world(ioc_fill=300.0)
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {} and [c[2:6] for c in _places(v)] == [(0.54, 300, False, IOC_TIF)]
    assert _census(fs, "fast_tick_placed") == 1 and _census(fs, "take_in_band") == 1
    assert b["ledger_net"] == 300 and b["last_plan"]["decision"] == "take_in_band"
    assert b["last_plan"]["take_band"]["verdict"] == "in_band" and _inserts(p)[0][20] == "take_in_band"
    # SHORT: a woken flat short book (his 0.65) with the bid a cent under -> the SELL IOC at 0.64
    _shorts_on(monkeypatch)
    p2 = _short_at()
    b2 = _short_book(p2, ledger=0, avg=0.66)
    v2 = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    _walk()
    fs2 = _fast(p2, v2, http=_short_http())
    assert _skips(fs2) == {} and [c[1:6] for c in _places(v2)] == [(SLUG, 0.64, 300, False, IOC_TIF)]
    assert _census(fs2, "fast_tick_placed") == 1 and _census(fs2, "take_in_band") == 1
    assert b2["ledger_net"] == -300 and b2["last_plan"]["decision"] == "take_in_band"
    assert b2["last_plan"]["take_band"]["verdict"] == "in_band" and _inserts(p2)[0][20] == "take_in_band"
    assert _inserts(p2)[0][18] == SHORT and _inserts(p2)[0][22] is True, "the row records the fast path (061)"
    # a rest standing on either side: `order_open`, nothing read, nothing placed, never converted
    p3, b3, v3 = _long_world(ioc_fill=300.0)
    p3.add_order(b3, wire=0.52, qty=300, placed_ts=NOW - 20)
    v3.rest("oid-1", "BUY", 0.52, 300)
    _walk()
    fs3 = _fast(p3, v3)
    assert _skips(fs3) == {CID: "order_open"} and _bbos(v3) == [] and not _places(v3) and _census(fs3, "take_in_band") == 0
    p4 = _short_at()
    b4 = _short_book(p4, ledger=0, avg=0.66)
    p4.add_order(b4, side=SELL, wire=0.66, qty=300, placed_ts=NOW - 20)
    v4 = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    v4.rest("oid-1", "SELL", 0.66, 300, intent=SHORT)
    _walk()
    fs4 = _fast(p4, v4, http=_short_http())
    assert _skips(fs4) == {CID: "order_open"} and not _places(v4) and _census(fs4, "take_in_band") == 0
    # the fast path reaches the band through _tick_book -> _act alone: no band read of its own
    for fn in (ml._fast_book, ml._fast_gate, ml._fast_step_o, ml._fast_candidate):
        s = inspect.getsource(fn)
        for name in ("take_band", "band_cent", "_short_take_band", "take_band_width"):
            assert name not in s, (fn.__name__, name)


def test_e27_a_standing_rest_is_never_converted_on_the_full_tick_either(monkeypatch):
    # LONG: a rest at 0.52 standing 100 s, the ask 0.54 (inside the widened band): keep, no band
    p, b, v = _long_world(ioc_fill=300.0)
    o = p.add_order(b, wire=0.52, qty=300, placed_ts=NOW - 100)
    v.rest("oid-1", "BUY", 0.52, 300)
    st = _tick(p, v)
    assert not _cancels(v) and not _places(v) and p.orders[o["id"]]["state"] == "open"
    assert _census(st, "open_order_pending") == 1 and _census(st, "take_in_band") == 0
    assert "take_band" not in b["last_plan"] and b["last_plan"]["open_order"] == o["id"]
    # SHORT: a SELL rest at 0.66 standing 100 s, the bid 0.64 (inside the short's band): keep, no band
    _shorts_on(monkeypatch)
    p2 = _short_at()
    b2 = _short_book(p2, ledger=0, avg=0.66)
    o2 = p2.add_order(b2, side=SELL, wire=0.66, qty=300, placed_ts=NOW - 100)
    v2 = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    v2.rest("oid-1", "SELL", 0.66, 300, intent=SHORT)
    st2 = _tick(p2, v2, http=_short_http())
    assert not _cancels(v2) and not _places(v2) and p2.orders[o2["id"]]["state"] == "open"
    assert _census(st2, "open_order_pending") == 1 and _census(st2, "take_in_band") == 0
    assert "take_band" not in b2["last_plan"]


def test_e27_the_re_read_withholds_the_ioc_on_both_sides_when_the_quote_left_the_band(monkeypatch):
    # LONG: the re-read's ask above the band cent -> `ask_moved` {0.54, 0.55, wire 0.54}, the rest at 0.52
    p = _his_at(0.52)
    b = p.add_book(ledger=0)
    v = _MovingVenue([(0.53, 0.54), (0.53, 0.55)], bid=0.53, ask=0.54, ioc_fill=300.0)
    st = _tick(p, v)
    assert _bbos(v).count(SLUG) == 2 and [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)]
    assert _census(st, "take_in_band") == 1 and _census(st, "ask_moved") == 1 and _census(st, "take_placed") == 0
    assert b["last_plan"]["ask_moved"] == {"ask_at_plan": 0.54, "ask_at_send": 0.55, "wire": 0.54}
    assert b["last_plan"]["decision"] == "rest" and b["ledger_net"] == 0 and st["ops"] == 1
    # SHORT: the re-read's bid under the band cent -> `bid_moved` {0.64, 0.63, wire 0.64}, the rest at 0.66
    _shorts_on(monkeypatch)
    p2 = _short_at()
    v2 = _MovingVenue([(0.64, 0.66)] * SHORT_OPEN_READS + [(0.64, 0.66), (0.63, 0.66)], bid=0.64, ask=0.66, ioc_fill=300.0)
    st2 = _tick(p2, v2, http=_short_http())
    b2 = next(iter(p2.books.values()))
    assert _bbos(v2).count(SLUG) == SHORT_OPEN_READS + 2 and [c[1:6] for c in _places(v2)] == [(SLUG, 0.66, 300, False, GTC_TIF)]
    assert _census(st2, "take_in_band") == 1 and _census(st2, "bid_moved") == 1 and _census(st2, "take_placed") == 0
    assert b2["last_plan"]["bid_moved"] == {"bid_at_plan": 0.64, "bid_at_send": 0.63, "wire": 0.64}
    assert b2["last_plan"]["take_band"]["verdict"] == "in_band" and b2["last_plan"]["decision"] == "rest"
    assert b2["ledger_net"] == 0 and st2["ops"] == 1 and _inserts(p2)[0][20] == "rest"
    # the re-read at the band cent exactly: the IOC goes (both sides)
    p3 = _his_at(0.52)
    p3.add_book(ledger=0)
    v3 = _MovingVenue([(0.53, 0.54), (0.53, 0.54)], bid=0.53, ask=0.54, ioc_fill=300.0)
    _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.54, 300, False, IOC_TIF)]
    p4 = _short_at()
    v4 = _MovingVenue([(0.64, 0.66)] * SHORT_OPEN_READS + [(0.64, 0.66), (0.64, 0.67)], bid=0.64, ask=0.66, ioc_fill=300.0)
    _tick(p4, v4, http=_short_http())
    assert [c[1:6] for c in _places(v4)] == [(SLUG, 0.64, 300, False, IOC_TIF)]
    # the unread re-read and the spent call budget: the rest, on the short too
    p5 = _short_at()
    v5 = _MovingVenue([(0.64, 0.66)] * SHORT_OPEN_READS + [(0.64, 0.66), (None, None)], bid=0.64, ask=0.66, ioc_fill=300.0)
    st5 = _tick(p5, v5, http=_short_http())
    assert [c[1:6] for c in _places(v5)] == [(SLUG, 0.66, 300, False, GTC_TIF)] and _census(st5, "ioc_quote_unread") == 1
    # the call budget spent (a flat short book already open: the candidate stage's own read is capped by
    # the same budget, so the shape needs the book): no re-read, no IOC, the rest
    monkeypatch.setattr(rules, "MIRROR_VENUE_CALLS_PER_TICK", 0)
    p6 = _short_at()
    b6 = _short_book(p6, ledger=0, avg=0.66)
    v6 = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    st6 = _tick(p6, v6, http=_short_http())
    assert [c[1:6] for c in _places(v6)] == [(SLUG, 0.66, 300, False, GTC_TIF)] and _census(st6, "ioc_reread_capped") == 1
    assert _census(st6, "take_in_band") == 1 and _census(st6, "take_placed") == 0 and b6["ledger_net"] == 0
    assert b6["last_plan"]["ioc_reread_capped"] == {"guard_calls": 0, "budget": 0} and _bbos(v6).count(SLUG) == 1


def test_e27_059_absent_takes_nothing_in_band_on_either_side(monkeypatch):
    monkeypatch.setattr(ml, "_order_cols_absent_logged", False)
    p, b, v = _long_world(ioc_fill=300.0)
    p.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    st = _tick(p, v)
    assert st["order_cols_absent"] == "UndefinedColumnError"
    assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)] and _census(st, "take_in_band") == 0
    assert b["last_plan"]["take_band"]["verdict"] == "uncounted" and len(_inserts(p)[0]) == 19
    _shorts_on(monkeypatch)
    p2 = _short_at()
    p2.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    v2 = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    st2 = _tick(p2, v2, http=_short_http())
    b2 = next(iter(p2.books.values()))
    assert [c[1:6] for c in _places(v2)] == [(SLUG, 0.66, 300, False, GTC_TIF)] and _census(st2, "take_in_band") == 0
    assert b2["last_plan"]["take_band"]["verdict"] == "uncounted" and len(_inserts(p2)[0]) == 19
    assert _bbos(v2).count(SLUG) == SHORT_OPEN_READS + 1, "no re-read: no IOC was decided"


def test_e27_the_room_is_read_on_the_collateral_at_the_band_cent_on_the_short(monkeypatch):
    """A room of $100: the SELL IOC at 0.64 is sized floor(100 / 0.36) =
    277 on its collateral (1 - the band cent); expiring 0, the rest at
    0.66 is today's rest, floor(100 / 0.34) = 294 -- the plan's quantity
    goes in and _place_reserved's own re-read at the wire sizes it."""
    _shorts_on(monkeypatch)
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 100.0)
    p = _short_at()
    v = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    st = _tick(p, v, http=_short_http())
    b = next(iter(p.books.values()))
    assert [c[1:6] for c in _places(v)] == [(SLUG, 0.64, 277, False, IOC_TIF)] and _census(st, "take_in_band") == 1
    assert b["last_plan"]["take_qty"] == 277 and b["ledger_net"] == -277
    p2 = _short_at()
    v2 = _Venue(bid=0.64, ask=0.66, ioc_fill=0.0)
    _tick(p2, v2, http=_short_http())
    assert [c[1:6] for c in _places(v2)] == [(SLUG, 0.64, 277, False, IOC_TIF), (SLUG, 0.66, 294, False, GTC_TIF)]
    # the room short at the band cent: `over_room`, nothing placed (the take-first's own rule)
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 0.35)      # a share at 0.66 (0.34), none at 0.64 (0.36)
    p3 = _short_at()
    v3 = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    st3 = _tick(p3, v3, http=_short_http())
    assert _census(st3, "over_room") == 1 and not _places(v3) and _census(st3, "take_in_band") == 0
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1250.0)
    asrc = inspect.getsource(ml._act)
    assert "qty = _room_qty(t, p.qty, band, intent)" in asrc, "the room at the band cent, the book's intent"


def test_e27_a_lengthened_wait_makes_the_short_band_rest_first_and_the_unit_read_names_every_verdict(monkeypatch):
    _shorts_on(monkeypatch)
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 20.0)
    p = _short_at()
    v = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    st = _tick(p, v, http=_short_http())
    b = next(iter(p.books.values()))
    assert [c[1:6] for c in _places(v)] == [(SLUG, 0.66, 300, False, GTC_TIF)] and _census(st, "take_in_band") == 0
    assert b["last_plan"]["take_band"]["verdict"] == "waiting"
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 0.0)
    # the read itself, every verdict, at the unit (the tick's quote handed in as the worker hands it)
    t = types.SimpleNamespace(order_cols=True, now=NOW)
    book, plan = {"take_armed_ts": None}, {}

    def _read(bid, ask, his=0.65, cols=True):
        t.order_cols = cols
        plan.clear()
        got = ml._short_take_band(t, book, types.SimpleNamespace(bid=bid, ask=ask), his, plan)
        return got, plan["take_band"]["verdict"], plan["take_band"]
    for bid in (None, 0.0, 1.0, 1.5, float("nan"), True):
        assert _read(bid, 0.66)[:2] == (None, "unread"), bid
    for his in (None, True, 0.0, 1.0, 1.5, "0.65"):
        got, verdict, tb = _read(0.64, 0.66, his=his)
        assert (got, verdict) == (None, "unread") and tb["his_cent"] is None and tb["frac"]["cost"] is None, his
    assert _read(0.65, 0.66)[:2] == (None, "at_level") and _read(0.66, 0.67)[:2] == (None, "at_level")
    assert _read(0.63, 0.66)[:2] == (None, "out")
    assert _read(0.64, 0.66, cols=False)[:2] == (None, "uncounted") and _read(0.64, 0.66, cols=None)[:2] == (None, "uncounted")
    got, verdict, tb = _read(0.64, 0.66)
    assert (got, verdict) == (0.64, "in_band")
    assert tb == {"his_cent": 0.65, "band": 0.0175, "band_cent": 0.64, "bid": 0.64, "ask": 0.66,
                  "frac": {"his_px": 0.65, "cost": 0.35, "width": 0.0175}, "verdict": "in_band"}
    assert _read(0.64, None)[:2] == (0.64, "in_band"), "the ask is not read on the short side"
    # his cent is CEILED (sell_wire): his 0.643 -> 0.65, so the bid 0.64 is under it and in band at the cent
    # 0.63 (sell_wire(0.643 - 0.01785)); a floored cent (0.64) would have read the bid at level
    assert _read(0.64, 0.66, his=0.643)[:2] == (0.63, "in_band") and _read(0.65, 0.66, his=0.643)[1] == "at_level"
    assert _read(0.89, 0.91, his=0.90)[:2] == (None, "off"), "a width under a cent: no band"
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.0)
    assert _read(0.64, 0.66)[:2] == (None, "off") and _read(0.65, 0.66)[:2] == (None, "at_level")
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.02)
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 20.0)
    assert _read(0.64, 0.66)[:2] == (None, "waiting")
    book["take_armed_ts"] = NOW - 25
    assert _read(0.64, 0.66)[:2] == (0.64, "in_band"), "an arm older than the wait has waited (take_allowed)"
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 0.0)
    # the long reader's frac beside its verdicts (E27's stamps on lane 2's read)
    book["take_armed_ts"] = None
    plan.clear()
    assert ml._take_band(t, book, types.SimpleNamespace(bid=0.53, ask=0.54), 0.52, plan) == 0.54
    assert plan["take_band"]["frac"] == {"his_px": 0.52, "cost": 0.52, "width": 0.02}
    plan.clear()
    assert ml._take_band(t, book, types.SimpleNamespace(bid=0.53, ask=0.54), None, plan) is None
    assert plan["take_band"]["verdict"] == "unread" and plan["take_band"]["frac"] == {"his_px": None, "cost": None, "width": None}


def test_e27_a_short_add_with_the_bid_at_or_over_his_sell_cent_takes_at_his_cent_first(monkeypatch):
    """The review's HIGH-1: the bid AT his sell cent (0.65) is inside the
    band (0c under him) and the lane as built left it to today's rest at
    the ask's cent 0.66 while a bid a cent WORSE (0.64) was taken -- the
    band paid a cent for the fill it refused for free. Now: ONE SELL IOC
    at his cent (decision 'take', census take_at_his_level, verdict
    at_level), the remainder resting at the short wire 0.66 as today;
    through his cent (bid 0.66 / ask 0.67) the same IOC at 0.65; the
    locked book still takes at the wire FIRST; the re-read's bid under
    his cent withholds it (bid_moved) and the whole quantity rests; the
    059 columns absent -> no IOC, the rest; MIRROR_TAKE_BAND 0 -> today's
    rest (the operator's one line back); a lengthened wait -> the rest;
    the fast wake takes the same way."""
    _shorts_on(monkeypatch)
    p = _short_at()
    v = _Venue(bid=0.65, ask=0.66, ioc_fill=300.0)
    st = _tick(p, v, http=_short_http())
    b = next(iter(p.books.values()))
    assert [c[1:6] for c in _places(v)] == [(SLUG, 0.65, 300, False, IOC_TIF)]
    assert _census(st, "take_at_his_level") == 1 and _census(st, "take_in_band") == 0 and _census(st, "take_first") == 1
    assert b["ledger_net"] == -300 and b["last_plan"]["decision"] == "take"
    assert b["last_plan"]["take_band"]["verdict"] == "at_level" and b["last_plan"]["take_band"]["his_cent"] == 0.65
    ins = _inserts(p)
    assert (ins[0][3], ins[0][4], ins[0][5], ins[0][8], ins[0][10], ins[0][18], ins[0][20]) == ("take", SELL, "IOC", 0.65, 0.65, SHORT, "take")
    assert _bbos(v).count(SLUG) == SHORT_OPEN_READS + 2, "the tick's read and the re-read before the send"
    # a partial fill: the remainder rests at the short wire 0.66, decision 'rest'
    p2 = _short_at()
    v2 = _Venue(bid=0.65, ask=0.66, ioc_fill=100.0)
    _tick(p2, v2, http=_short_http())
    assert [c[1:6] for c in _places(v2)] == [(SLUG, 0.65, 300, False, IOC_TIF), (SLUG, 0.66, 200, False, GTC_TIF)]
    assert [(a[10], a[20]) for a in _inserts(p2)] == [(0.65, "take"), (0.66, "rest")]
    # the room is read on the collateral AT HIS CENT (the fold's mutant M27: read at the wire it survived): $100 ->
    # floor(100 / 0.35) = 285 at 0.65 (294 at the 0.66 wire); expiring 0, the at-level take's remainder -- the IOC's
    # own 285, E4's rule (_entry_take's rest_qty is the IOC's quantity off the band path) -- rests at 0.66; $0.34 of
    # room buys a share at the wire (0.34) and none at his cent (0.35) -> `over_room`, nothing placed
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 100.0)
    p2b = _short_at()
    v2b = _Venue(bid=0.65, ask=0.66, ioc_fill=0.0)
    st2b = _tick(p2b, v2b, http=_short_http())
    assert [c[1:6] for c in _places(v2b)] == [(SLUG, 0.65, 285, False, IOC_TIF), (SLUG, 0.66, 285, False, GTC_TIF)]
    assert _census(st2b, "take_at_his_level") == 1 and next(iter(p2b.books.values()))["last_plan"]["take_qty"] == 285
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 0.34)
    p2c = _short_at()
    v2c = _Venue(bid=0.65, ask=0.66, ioc_fill=300.0)
    st2c = _tick(p2c, v2c, http=_short_http())
    assert _census(st2c, "over_room") == 1 and not _places(v2c) and _census(st2c, "take_at_his_level") == 0
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1250.0)
    # through his cent (bid 0.66 / ask 0.67): the same IOC at his cent, never above it
    p3 = _short_at()
    v3 = _Venue(bid=0.66, ask=0.67, ioc_fill=300.0)
    st3 = _tick(p3, v3, http=_short_http())
    assert [c[1:6] for c in _places(v3)] == [(SLUG, 0.65, 300, False, IOC_TIF)] and _census(st3, "take_at_his_level") == 1
    # the locked book at 0.66 / 0.66: the take at the WIRE first, as before
    p4 = _short_at()
    v4 = _Venue(bid=0.66, ask=0.66, ioc_fill=300.0)
    _tick(p4, v4, http=_short_http())
    assert [c[1:6] for c in _places(v4)] == [(SLUG, 0.66, 300, False, IOC_TIF)]
    # the re-read's bid under his cent: bid_moved, the whole 300 rests at 0.66
    p5 = _short_at()
    v5 = _MovingVenue([(0.65, 0.66)] * SHORT_OPEN_READS + [(0.65, 0.66), (0.64, 0.66)], bid=0.65, ask=0.66, ioc_fill=300.0)
    st5 = _tick(p5, v5, http=_short_http())
    b5 = next(iter(p5.books.values()))
    assert [c[1:6] for c in _places(v5)] == [(SLUG, 0.66, 300, False, GTC_TIF)] and _census(st5, "bid_moved") == 1
    assert b5["last_plan"]["bid_moved"] == {"bid_at_plan": 0.65, "bid_at_send": 0.64, "wire": 0.65} and b5["ledger_net"] == 0
    # the 059 columns absent: no IOC (an uncounted take is not sent), the rest by the 050 INSERT
    monkeypatch.setattr(ml, "_order_cols_absent_logged", False)
    p6 = _short_at()
    p6.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    v6 = _Venue(bid=0.65, ask=0.66, ioc_fill=300.0)
    st6 = _tick(p6, v6, http=_short_http())
    assert [c[1:6] for c in _places(v6)] == [(SLUG, 0.66, 300, False, GTC_TIF)] and _census(st6, "take_at_his_level") == 0
    assert len(_inserts(p6)[0]) == 19
    # MIRROR_TAKE_BAND 0: the lane off on the short side, today's rest byte for byte
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.0)
    p7 = _short_at()
    v7 = _Venue(bid=0.65, ask=0.66, ioc_fill=300.0)
    st7 = _tick(p7, v7, http=_short_http())
    assert [c[1:6] for c in _places(v7)] == [(SLUG, 0.66, 300, False, GTC_TIF)] and _census(st7, "take_at_his_level") == 0
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.02)
    # a lengthened wait: rest first
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 20.0)
    p8 = _short_at()
    v8 = _Venue(bid=0.65, ask=0.66, ioc_fill=300.0)
    st8 = _tick(p8, v8, http=_short_http())
    assert [c[1:6] for c in _places(v8)] == [(SLUG, 0.66, 300, False, GTC_TIF)] and _census(st8, "take_at_his_level") == 0
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 0.0)
    # the fast wake: the same take on the woken flat short
    p9 = _short_at()
    b9 = _short_book(p9, ledger=0, avg=0.66)
    v9 = _Venue(bid=0.65, ask=0.66, ioc_fill=300.0)
    _walk()
    fs9 = _fast(p9, v9, http=_short_http())
    assert _skips(fs9) == {} and [c[1:6] for c in _places(v9)] == [(SLUG, 0.65, 300, False, IOC_TIF)]
    assert _census(fs9, "take_at_his_level") == 1 and b9["ledger_net"] == -300
    # the arm by text: after the band's IOC, before the rest; the IOC through the one _entry_take, decision 'take'
    asrc = inspect.getsource(ml._act)
    i_band = asrc.index('_mirror_stop("take_in_band", w)')
    i_arm = asrc.index('tb.get("verdict") == "at_level"')
    i_rest = asrc.index('return await _place(t, book, r, "increase", p.side, wire, qty, his_px, p, plan)')
    assert i_band < i_arm < i_rest
    assert "return await _entry_take(t, book, r, p, lvl, wire, qty, his_px, plan, first=True)" in asrc
    assert '(_num(rules.MIRROR_TAKE_BAND) or 0.0) > 0.0' in asrc and 't.order_cols is True and' in asrc


# ------------------------------------------------------ the names, the docs

def test_e27_no_census_name_no_migration_render_ops_hashed_as_left_and_the_sites_by_source():
    keys = ml.CENSUS_KEYS
    # no name added: the tail pins hold (test_e14's keys[-44] on 52e1d52 is keys[-48] over E25's four
    # names, 0e72120: 233 -> 237 keys; E19's keys[-13] / [-12]); E29 (FILL lane 29, the desk's exit ends the
    # book's adds: hand_exit / hand_held / hand_held_unread / hand_exit_write_failed before drift_smaller_open)
    # moves it once more: 237 -> 241, -48 -> -52 (pinned in the lane's worktree on 6c0830d; E28's five names land
    # between E25's and E29's and re-cut it by five more at landing)
    assert len(keys) == 246 and keys[-57] == "take_in_band" and keys[-13] == "drift_smaller_open"
    assert keys[-12] == "registered_no_increase" and keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    src = inspect.getsource(ml)
    for name in ("short_take_in_band", "take_band_short", "short_band", "take_tolerance"):
        assert f'"{name}"' not in src, name
    # the one emit site for take_in_band, counting both sides, on the no-order path's band arm
    assert src.count('_mirror_stop("take_in_band", w)') == 1
    asrc = inspect.getsource(ml._act)
    assert 'if p.side == BUY and not short and rules.leg_action(book.get("intent"), p.side) == "add":\n' \
           '            band = _take_band(t, book, r, his_px, plan)\n' \
           '        elif p.side == SELL and short and rules.leg_action(book.get("intent"), p.side) == "add":\n' \
           '            band = _short_take_band(t, book, r, his_px, plan)\n' in asrc
    assert asrc.count("_short_take_band(") == 1 and asrc.count("_take_band(") == 2
    # the same _entry_take call carries both sides' IOC: one site, in_band=True, first=True
    assert asrc.count("_entry_take(t, book, r, p, band, wire, qty, his_px, plan, first=True,\n                                     in_band=True)") == 1
    assert asrc.count("in_band=True") == 5, "the entry's one band site and lane 3's four exit sites"
    # the arm sits AFTER the at-level take and BEFORE the rest
    i_lvl = asrc.index('_mirror_stop("take_at_his_level", w)\n                return await _entry_take(')
    i_band = asrc.index('_mirror_stop("take_in_band", w)')
    i_rest = asrc.index('return await _place(t, book, r, "increase", p.side, wire, qty, his_px, p, plan)')
    assert i_lvl < i_band < i_rest
    # the at-level take's own test line reads nothing of the band (it fires FIRST, unchanged)
    assert 'if rules.take_allowed(0.0, book.get("take_armed_ts"), t.now, r.bid, r.ask, take_lvl, p.side):\n' in asrc
    # the short reader: the SELL-side reads, the same verdict words, the plan's one key
    ssrc = inspect.getsource(ml._short_take_band)
    for verdict in VERDICTS:
        assert f'tb["verdict"] = "{verdict}"' in ssrc, verdict
    assert "rules.sell_wire(hp)" in ssrc and "rules.short_band_cent(his_px)" in ssrc
    assert "rules.take_band_width(his_px, ORDER_INTENT_SHORT)" in ssrc
    assert "rules.at_or_through(SELL, r.bid, r.ask, his_cent)" in ssrc
    assert "rules.short_take_in_band(r.bid, r.ask, his_cent, bc)" in ssrc
    assert 'rules.take_allowed(0.0, book.get("take_armed_ts"), t.now, r.bid, r.ask, bc, SELL)' in ssrc
    assert 'plan["take_band"] = tb' in ssrc and ssrc.count('plan["take_band"]') == 1
    tsrc = inspect.getsource(ml._take_band)
    assert "rules.take_band_width(his_px)" in tsrc and "rules.band_cent(his_px)" in tsrc and '"frac"' in tsrc
    # the worker reads the constants through the rules module alone: no env knob of its own
    for bad in ('capped_env("MIRROR_TAKE_BAND', '_env_float("MIRROR_TAKE_BAND', "MIRROR_TAKE_BAND_FRAC = "):
        assert bad not in src, bad
    # the decision word is rules.order_decision's alone; the 059 columns gate it
    psrc = inspect.getsource(ml._place_reserved)
    assert "in_band=bool(in_band) and is_take" in psrc and inspect.getsource(rules.order_decision).count('"take_in_band"') == 1
    # no migration (061 the newest); render-ops.yml as this lane leaves it (take-band's third statement); 059's
    # comment as lane 2 left it
    files = sorted(x.name for x in (ROOT / "backend" / "migrations").glob("*.sql"))
    assert files[-1] == "061_fill_answers_cause_orders_fast.sql"
    assert _sha_file(ROOT / ".github" / "workflows" / "render-ops.yml") == RENDER_OPS_SHA
    assert _sha_file(ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql") == MIGRATION_059_SHA
    sql = (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "'take_in_band' is" in sql and "reserved for the entry band" in sql


def test_e27_every_name_is_emitted_here_too(monkeypatch):
    """The lane adds no census name; the convention's hook still runs
    the emit of `take_in_band` on the SHORT side under this file's
    rails (the worker file's coverage read imports lane 2's)."""
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.02)
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND_FRAC", 0.05)
    test_e27_a_short_add_with_the_bid_a_cent_under_his_cent_sends_one_sell_ioc_at_the_band_cent(monkeypatch)


def test_e27_the_docs_name_the_rule_the_rails_the_short_and_059s_list():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## 69\. .*\(2026-09-09, FILL lane 27\)", doc, re.M), "the E27 section header"
    sec = doc.split("## 69. ")[1]
    for k in ("MIRROR_TAKE_BAND", "MIRROR_TAKE_BAND_FRAC", "take_band_width", "short_band_cent", "short_take_in_band",
              "_short_take_band", "take_band", "frac", "take_in_band", "in_band", "at_level", "unread", "off",
              "uncounted", "out", "waiting", "bid_moved", "ask_moved", "test_e27_take_tolerance.py",
              "'rest', 'take', 'take_in_band', 'cover', 'exit_rest'", "38.2%", "57.7%", "80.6%", "87.1%", "+130",
              "+52", "-276", "-440", "+3.46%", "0.65", "0.64", "1.75c", "lets take it immediately with a tolerance",
              "capped_env", "MIRROR_TAKE_BAND=0", "D2", "E12", "open_catchup", "paid_med_c", "$0"):
        assert k in sec, k
