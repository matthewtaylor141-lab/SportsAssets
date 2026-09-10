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
    "rest_decision": ("2f8b8feef14fbd62", rules.rest_decision), "room_scale": ("92e7b5e20e20f05b", rules.room_scale),
    "take_in_band": ("a185e3965cf3e0bb", rules.take_in_band), "buy_wire": ("861dd6ffd57e1283", rules.buy_wire),
    "sell_wire": ("a9cae307d9e6f2b2", rules.sell_wire), "buy_price": ("f9b961a63c4d2dc4", rules.buy_price),
    # E31 (FILL lane 31, 2026-09-10): the take paths this lane's band arm fed
    # are RETIRED BY CODE and their functions DELETED -- `_exit_take`,
    # `_ioc_reread`, `_entry_take`, `_take_band`, `_short_take_band`,
    # `_exit_band_at`, `_flatten_send` have no caller left, so the pin is
    # their ABSENCE (E31_GONE below); `_short_wire`, `_wire_for`,
    # `_place`, `_place_reserved` are re-cut for the maker wire, the
    # unconditional flag, the touch-bound re-read and the cross re-price
    "_room_qty": ("458b3fea5e2d235b", ml._room_qty), "_place_reserved": ("a83a3e9473eb7112", ml._place_reserved),
    "_place": ("f559a52bfb610ef3", ml._place), "_fast_gate": ("1932811194268668", ml._fast_gate),
    "_fast_book": ("286e6fa4663c3887", ml._fast_book), "_wire_for": ("ca4de29c9c20fd7d", ml._wire_for),
    "_short_wire": ("12afe5fcd5b0c248", ml._short_wire),
    "_flatten_vanished": ("7f27e3b041da0c76", ml._flatten_vanished),
    "_frozen_exit": ("ef478fabdfa2ccc0", ml._frozen_exit), "_exit_terms": ("10411ad7900e6542", ml._exit_terms),
    "_cover_qty": ("4662f515286becbe", ml._cover_qty),
    "_sell_qty": ("95533ab0c37193ae", ml._sell_qty),
}
E31_GONE = ("_exit_take", "_ioc_reread", "_entry_take", "_take_band", "_short_take_band",
            "_exit_band_at", "_exit_band_take", "_exit_band_mark", "_flatten_send")
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
# then the heartbeat value column widened 2,400 -> 8,000 characters in mirror-tick and hourly (2026-09-10 02:1xZ: the
# 246-name census JSON runs past 4,000 and the newest lanes' names sit at its tail) -- 2cb5a0a793839493 -> 7528f10180c12e27
# then the read-only `nfl-team` preset beside nfl-rows (2026-09-10 02:5xZ, the owner's NFL spreads order: the C6 team
# columns on every aec-nfl row, the asc 3.5 rows, the venue's NFL team records, his spread slugs over 14 days) --
# 7528f10180c12e27 -> 33505a5187599535; no existing preset changed
# then the read-only mirror-by-league and his-matched presets (2026-09-10 03:1xZ, the desk's forensic study of
# RN1's book measured on our own data: docs/rn1-book-anatomy.md) -- 33505a5187599535 -> 11933c8251b294dc
# E31 (FILL lane 31, 2026-09-10): the read-only `maker-rests` preset beside take-band (and its copy in
# the hourly after take-band) and the need_confirm `mirror-post-only-rearm` beside mirror-rearm --
# 11933c8251b294dc -> d98a89e0a421735d; every existing preset and the take-band statements byte for byte
# then the ops secrets commit (2026-09-10 13:51Z, 605cef3: `arg` and `render_key` read from the event
# payload on disk and masked before anything else, because the runner prints a step's env block and its
# rendered script in the log header first) moved the file and did NOT re-pin here, so this constant was
# already red on the clean tip -- d98a89e0a421735d -> e302a4af41e1f7c1, measured, no preset touched
# THE PAIR PROBE (2026-09-10): the read-only `pair-candidates` preset beside his-matched (the OPEN
# two-token markets with no mirror book, none of RN1's flow and ne-sea-2026-09-09 excluded by name, from
# which the desk picks the one market it buys both outcomes of) plus its one help token --
# e302a4af41e1f7c1 -> 4b792097e975775f; every existing preset and the take-band statements byte for byte
RENDER_OPS_SHA = "ebc90bdfefeb898d"
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
    assert src.count("capped_env(") == 26 and src.count("min_wait_env(") == 8
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
#
# RE-PINNED WHOLE at E31 (FILL lane 31, 2026-09-10; owner order ~03:3xZ "become a maker
# not taker ... mirror him to a tee"). Every take this lane widened is retired BY CODE:
# there is no `_take_band`, no `_short_take_band`, no `_entry_take` and no IOC on the
# money path, so the band's verdicts (`in_band` / `at_level` / `out` / `off` / `unread` /
# `uncounted` / `waiting`) never reach a plan and `take_in_band` is a declared zero.
# The two constants and the four pure helpers this lane ADDED are unmoved and stay
# tested above -- they are the record of the rule (docs 75) -- and no value of either
# constant can send an order that crosses, because nothing on the money path reads them.
#
# WHAT REPLACES THE BAND, on both sides, is rules.maker_wire:
#   a long add   min(buy_wire(his), ask - 0.01)     -- his cent inside the spread, or a
#                                                      tick under the ask
#   a short add  max(sell_wire(his), bid + 0.01)    -- his cent inside the spread, or a
#                                                      tick over the bid, through the
#                                                      executor's own _short_wire
# so on EVERY world this file drove the mirror now pays his cent or better and never
# crosses. The tables below keep this lane's own worlds, cent for cent.


def test_e31_the_long_add_never_pays_a_cent_over_him_at_any_band_position():
    """E27's four long worlds, re-pinned: whatever the ask, the rest is
    HIS cent while his cent is under the ask, and one tick UNDER the ask
    when it is not -- never the band cent, never a cent over him.

      ask 2c over (0.53 / 0.54): E27 sent an IOC at 0.54. Now 0.52.
      ask 1c over (0.52 / 0.53): E27 sent an IOC at 0.53. Now 0.52.
      ask 3c over (0.54 / 0.55): E27 rested at 0.52. Now 0.52 (unchanged).
      Martinez 534 (0.81 / 0.82, his 0.61): rested at 0.61 then and now.
      ask AT his cent (0.51 / 0.52): E27 took at 0.52. Now 0.51.
      ask THROUGH it (0.50 / 0.51): E27 took at 0.52. Now 0.50.

    The row's `ask_at_send` (059) is written only where the rest sat AT
    the touch bound and re-read the quote at the send (E31 D); the cents
    paid over his level, readable off every row, are zero or negative."""
    table = [
        # bid,  ask,  his,  wire, clause,     bbo reads, ask_at_send
        (0.53, 0.54, 0.52, 0.52, "his_cent", 1, None),
        (0.52, 0.53, 0.52, 0.52, "his_cent", 2, 0.53),
        (0.54, 0.55, 0.52, 0.52, "his_cent", 1, None),
        (0.81, 0.82, 0.61, 0.61, "his_cent", 1, None),
        (0.51, 0.52, 0.52, 0.51, "touch", 2, 0.52),
        (0.50, 0.51, 0.52, 0.50, "touch", 2, 0.51),
    ]
    for bid, ask, his, wire, clause, reads, at_send in table:
        p = _his_at(his)
        b = p.add_book(ledger=0)
        v = _Venue(bid=bid, ask=ask, ioc_fill=300.0)
        st = _tick(p, v)
        tag = (bid, ask, his)
        assert [c[2:6] for c in _places(v)] == [(wire, 300, False, GTC_TIF)], tag
        assert _places(v)[0][7] is True, tag
        assert _census(st, "take_in_band") == 0 and _census(st, "take_placed") == 0, tag
        assert _census(st, "take_at_his_level") == 0 and _census(st, "rest_placed") == 1, tag
        assert _census(st, "maker_rest_at_touch") == (1 if clause == "touch" else 0), tag
        assert b["ledger_net"] == 0 and b["open_order_id"] is not None, tag
        lp = b["last_plan"]
        assert lp["decision"] == "rest" and "take_band" not in lp, tag
        assert lp["maker"]["clause"] == clause and lp["maker"]["wire"] == wire, tag
        assert _bbos(v).count(SLUG) == reads, tag
        ins = _inserts(p)
        assert len(ins) == 1 and len(ins[0]) == 23, tag
        assert (ins[0][3], ins[0][4], ins[0][5], ins[0][8], ins[0][10], ins[0][11]) == (
            "increase", BUY, "GTC", his, wire, 300), tag
        assert ins[0][18] == "ORDER_INTENT_BUY_LONG" and ins[0][19] == at_send and ins[0][20] == "rest", tag
        # the cents paid OVER his floored cent: never positive
        assert round(ins[0][10] - (int(ins[0][8] * 100) / 100.0), 2) <= 0.0, tag


def test_e31_the_cheap_contract_and_the_mid_price_rest_at_his_cent_too():
    """E27's frac table (his 0.10, the floor a cent; his 0.30, 5% =
    1.5c) drove which asks were TAKEN. Every one of them now rests at
    his own cent, so the width the frac computes changes nothing that
    reaches the venue: 0.11 and 0.12 over his 0.10 both rest at 0.10,
    0.31 and 0.32 over his 0.30 both rest at 0.30. The pure helper's
    own table is unmoved and tested above."""
    for bid, ask, his, reads in ((0.10, 0.11, 0.10, 2), (0.11, 0.12, 0.10, 1),
                                 (0.30, 0.31, 0.30, 2), (0.31, 0.32, 0.30, 1)):
        p = _his_at(his)
        b = p.add_book(ledger=0)
        v = _Venue(bid=bid, ask=ask, ioc_fill=300.0)
        st = _tick(p, v)
        assert [c[2:6] for c in _places(v)] == [(his, 300, False, GTC_TIF)], (bid, ask, his)
        assert _census(st, "take_in_band") == 0 and _bbos(v).count(SLUG) == reads, (bid, ask, his)
        assert b["last_plan"]["maker"]["clause"] == "his_cent", (bid, ask, his)
        assert "take_band" not in b["last_plan"], (bid, ask, his)
    # the frac itself is unmoved: 5% of the cost, floored at a cent, at call time
    assert rules.take_band_width(0.10) == 0.01 and rules.take_band_width(0.30) == 0.015
    assert rules.take_band_width(0.52) == 0.02


def test_e31_no_value_of_the_two_constants_can_move_a_long_or_a_short_order(monkeypatch):
    """E27 pinned that MIRROR_TAKE_BAND at 0.01 made the long side lane
    2 byte for byte and at 0 made it today's rest. RE-PINNED at E31 as
    the RAIL it is now: at 0, at lane 2's 0.01, at this lane's 0.02 and
    at an absurd 0.25 the mirror sends the SAME post-only rest at his
    cent, on the long side and on the short. There is no reader left on
    the money path, so no shell can re-arm a take by raising it."""
    for band in (0.0, 0.01, 0.02, 0.25):
        monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", band)
        p, b, v = _long_world(bid=0.52, ask=0.53, ioc_fill=300.0)
        st = _tick(p, v)
        assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)], band
        assert _census(st, "take_in_band") == 0 and "take_band" not in b["last_plan"], band
        p2, b2, v2 = _long_world(bid=0.53, ask=0.54, ioc_fill=300.0)
        st2 = _tick(p2, v2)
        assert [c[2:6] for c in _places(v2)] == [(0.52, 300, False, GTC_TIF)], band
        assert _census(st2, "take_in_band") == 0, band
    for frac in (0.0, 0.05, 0.5):
        monkeypatch.setattr(rules, "MIRROR_TAKE_BAND_FRAC", frac)
        p3, b3, v3 = _long_world(bid=0.53, ask=0.54, ioc_fill=300.0)
        st3 = _tick(p3, v3)
        assert [c[2:6] for c in _places(v3)] == [(0.52, 300, False, GTC_TIF)], frac
        assert _census(st3, "take_in_band") == 0, frac
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.02)
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND_FRAC", 0.05)
    _shorts_on(monkeypatch)
    for band in (0.0, 0.02):
        monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", band)
        p4 = _short_at()
        v4 = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
        st4 = _tick(p4, v4, http=_short_http())
        b4 = next(iter(p4.books.values()))
        assert [c[1:6] for c in _places(v4)] == [(SLUG, 0.65, 300, False, GTC_TIF)], band
        assert _census(st4, "take_in_band") == 0 and "take_band" not in b4["last_plan"], band
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.02)


# -------------------------------------------------- the worker, the short add

def test_e31_the_short_add_rests_at_his_sell_cent_or_a_tick_over_the_bid(monkeypatch):
    """E27's short worlds, re-pinned. His level 0.65 in long space (he
    paid 0.35 for the other token). The short add is an OFFER of the
    contract, so the maker clamp reads the SELL side and the wire goes
    through the executor's own _short_wire, as it always did:

      bid 1c under (0.64 / 0.66): E27 sent a SELL IOC at 0.64 -- a cent
        UNDER him -- and rested the remainder at the ask's 0.66. Now ONE
        rest at HIS OWN cent 0.65, inside the spread.
      bid 2c under (0.63 / 0.66): E27 rested at 0.66. Now 0.65.
      bid AT his cent (0.65 / 0.66): E27 took at 0.65. Now 0.66, a tick
        over the bid (his cent would sit AT it).
      the locked book (0.66 / 0.66): E27 took at 0.66. Now 0.67.
      bid THROUGH his cent (0.66 / 0.67): E27 took at 0.65. Now 0.67.
      his 0.90 (a sub-cent width, `off` for E27): 0.90 then 0.90 now.
      his 0.9004 (the review's M3): E27 rested at 0.92. Now 0.91 -- the
        ceiling of HIS OWN price, a tick over the bid, never the ask."""
    _shorts_on(monkeypatch)
    table = [
        # bid,  ask,  his,     wire, clause,     bbo reads, ask_at_send
        (0.64, 0.66, 0.65, 0.65, "his_cent", SHORT_OPEN_READS + 2, 0.66),
        (0.63, 0.66, 0.65, 0.65, "his_cent", SHORT_OPEN_READS + 1, None),
        (0.65, 0.66, 0.65, 0.66, "touch", SHORT_OPEN_READS + 2, 0.66),
        (0.66, 0.66, 0.65, 0.67, "touch", SHORT_OPEN_READS + 2, 0.66),
        (0.66, 0.67, 0.65, 0.67, "touch", SHORT_OPEN_READS + 2, 0.67),
        (0.89, 0.91, 0.90, 0.90, "his_cent", SHORT_OPEN_READS + 2, 0.91),
        (0.90, 0.92, 0.9004, 0.91, "his_cent", SHORT_OPEN_READS + 2, 0.92),
    ]
    for bid, ask, his, wire, clause, reads, at_send in table:
        p = _short_at(his=his)
        v = _Venue(bid=bid, ask=ask, ioc_fill=300.0)
        st = _tick(p, v, http=_short_http())
        b = next(iter(p.books.values()))
        tag = (bid, ask, his)
        assert b["intent"] == SHORT and b["target"] == -300, tag
        assert [c[1:] for c in _places(v)] == [(SLUG, wire, 300, False, GTC_TIF, SHORT, True, None)], tag
        assert _census(st, "take_in_band") == 0 and _census(st, "take_placed") == 0, tag
        assert _census(st, "take_at_his_level") == 0 and _census(st, "rest_placed") == 1, tag
        assert _census(st, "short_open") == 1, tag
        assert _census(st, "maker_rest_at_touch") == (1 if clause == "touch" else 0), tag
        assert b["ledger_net"] == 0 and b["open_order_id"] is not None, tag
        lp = b["last_plan"]
        assert lp["decision"] == "rest" and lp["side"] == SELL and "take_band" not in lp, tag
        assert lp["maker"]["clause"] == clause, tag
        ins = _inserts(p)
        assert len(ins) == 1 and len(ins[0]) == 23, tag
        assert (ins[0][3], ins[0][4], ins[0][5], ins[0][8], ins[0][10], ins[0][11]) == (
            "increase", SELL, "GTC", his, wire, 300), tag
        assert ins[0][18] == SHORT and ins[0][19] == at_send and ins[0][20] == "rest", tag
        assert _bbos(v).count(SLUG) == reads, tag
        o = next(iter(p.orders.values()))
        assert (o["kind"], o["side"], o["tif"], o["intent"], o["wire"]) == ("increase", SELL, "GTC", SHORT, wire), tag
    # a TAKER lifting part of the fresh rest at create books it and leaves the rest standing
    p2 = _short_at()
    v2 = _Venue(bid=0.64, ask=0.66, ioc_fill=100.0, lift=100.0)
    st2 = _tick(p2, v2, http=_short_http())
    b2 = next(iter(p2.books.values()))
    assert [c[1:6] for c in _places(v2)] == [(SLUG, 0.65, 300, False, GTC_TIF)]
    assert b2["ledger_net"] == -100 and _census(st2, "maker_fill_at_create") == 1
    assert _census(st2, "post_only_block") == 0 and b2["open_order_id"] is not None
    # an ADD onto a held short (-100 held, his net -300): the 200 rests the same way
    p3 = _short_at()
    b3 = _short_book(p3, ledger=-100, avg=0.66)
    v3 = _Venue(bid=0.64, ask=0.66, ioc_fill=200.0, held={SLUG: -100})
    st3 = _tick(p3, v3, http=_short_http())
    assert [c[1:6] for c in _places(v3)] == [(SLUG, 0.65, 200, False, GTC_TIF)]
    assert _census(st3, "take_in_band") == 0 and _census(st3, "short_add") == 1 and b3["ledger_net"] == -100
    assert _inserts(p3)[0][20] == "rest" and "take_band" not in b3["last_plan"]


def test_e27_a_short_reduce_with_the_bid_inside_the_band_is_never_band_taken(monkeypatch):
    """His net moved up (-300 -> -100: a partial buy-back) on a short of
    300 at 0.66; the bid 0.64 sits inside what the ADD's band would be
    at his 0.65 -- but the plan is a REDUCE (a BUY cover): the cover
    path's own rule, no `take_band` on the plan, no `take_in_band`.
    Unchanged by E31 except that there is no band to be inside of."""
    _shorts_on(monkeypatch)
    p = _short_at(long_size=300.0, other_size=400.0)
    _s4_proved(p)
    b = _short_book(p, ledger=-300, avg=0.66)
    v = _Venue(bid=0.64, ask=0.68, held={SLUG: -300})
    st = _tick(p, v, http=_mkt(300.0, 400.0))
    assert b["target"] == -100 and b["last_plan"]["side"] == BUY and b["last_plan"]["qty"] == 200
    assert "take_band" not in b["last_plan"] and _census(st, "take_in_band") == 0
    assert [c[5] for c in _places(v)] == [GTC_TIF], "the cover rests; no IOC exists"
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
    # the untouched functions, hashed on 52e1d52 -- RE-PINNED at E31 where the lane moved them
    for name, (digest, fn) in UNTOUCHED.items():
        assert _sha(fn) == digest, name
    # none of the exit path names the entry band's words. E31 DELETED _exit_take,
    # _flatten_send, _entry_take, _ioc_reread, _take_band and _short_take_band, so the
    # readers that remain are checked instead -- including the maker wire itself
    for fn in (ml._flatten_vanished, ml._frozen_exit, ml._frozen_reduce_on_fill,
               ml._cover_qty, ml._sell_qty, ml._rest_reread, ml._wire_for, ml._short_wire,
               rules.exit_terms, rules.take_allowed, rules.at_or_through, rules.rest_decision,
               rules.admission, rules.open_catchup, rules.maker_wire, rules.maker_bound):
        s = inspect.getsource(fn)
        for name in ("take_band_width", "short_band_cent", "short_take_in_band", "MIRROR_TAKE_BAND_FRAC",
                     "_short_take_band"):
            assert not re.search(rf"(?<![\w]){name}(?![\w])", s), (fn.__name__, name)
    for gone in ("_exit_take", "_flatten_send", "_entry_take", "_ioc_reread", "_take_band", "_short_take_band"):
        assert not hasattr(ml, gone), gone


# ------------------------------------------------------ the paths, the guards

def test_e31_the_fast_wake_rests_on_both_sides_through_the_same_functions(monkeypatch):
    """E27 pinned that the fast wake band-took on both sides. It rests on
    both sides now, at the same cents the full tick sends."""
    # LONG: a woken flat long with the ask two cents over -> the rest at his cent 0.52
    p, b, v = _long_world(ioc_fill=300.0)
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {} and [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)]
    assert _census(fs, "fast_tick_placed") == 1 and _census(fs, "take_in_band") == 0
    assert b["ledger_net"] == 0 and b["last_plan"]["decision"] == "rest"
    assert "take_band" not in b["last_plan"] and _inserts(p)[0][20] == "rest"
    # SHORT: a woken flat short book (his 0.65) with the bid a cent under -> the rest at 0.65
    _shorts_on(monkeypatch)
    p2 = _short_at()
    b2 = _short_book(p2, ledger=0, avg=0.66)
    v2 = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    _walk()
    fs2 = _fast(p2, v2, http=_short_http())
    assert _skips(fs2) == {} and [c[1:6] for c in _places(v2)] == [(SLUG, 0.65, 300, False, GTC_TIF)]
    assert _census(fs2, "fast_tick_placed") == 1 and _census(fs2, "take_in_band") == 0
    assert b2["ledger_net"] == 0 and b2["last_plan"]["decision"] == "rest"
    assert "take_band" not in b2["last_plan"] and _inserts(p2)[0][20] == "rest"
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
    # the fast path reaches the wire through _tick_book -> _act alone: no band read of its own
    for fn in (ml._fast_book, ml._fast_gate, ml._fast_step_o, ml._fast_candidate):
        s = inspect.getsource(fn)
        for name in ("take_band", "band_cent", "_short_take_band", "take_band_width"):
            assert name not in s, (fn.__name__, name)


def test_e27_a_standing_rest_is_never_converted_on_the_full_tick_either(monkeypatch):
    """Unchanged by E31 in outcome and stronger in cause: there is no arm
    that could convert a standing rest into a cross, on either side."""
    # LONG: a rest at 0.52 standing 100 s, the ask 0.54: keep, no band
    p, b, v = _long_world(ioc_fill=300.0)
    o = p.add_order(b, wire=0.52, qty=300, placed_ts=NOW - 100)
    v.rest("oid-1", "BUY", 0.52, 300)
    st = _tick(p, v)
    assert not _cancels(v) and not _places(v) and p.orders[o["id"]]["state"] == "open"
    assert _census(st, "open_order_pending") == 1 and _census(st, "take_in_band") == 0
    assert "take_band" not in b["last_plan"] and b["last_plan"]["open_order"] == o["id"]
    # SHORT: a SELL rest at 0.66 standing 100 s, the bid 0.64. E27 kept it and band-took
    # nothing. E31 keeps it from being CROSSED and re-quotes it to HIS OWN cent 0.65
    # instead -- rules.maker_compare_wire moves a rest toward his level whenever the touch
    # leaves room, and 0.66 was the ask's cent, not his. A `replace_cent` to a post-only
    # GTC: still nothing crossing, still no band, and now at his price rather than above it
    _shorts_on(monkeypatch)
    p2 = _short_at()
    b2 = _short_book(p2, ledger=0, avg=0.66)
    o2 = p2.add_order(b2, side=SELL, wire=0.66, qty=300, placed_ts=NOW - 100)
    v2 = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    v2.rest("oid-1", "SELL", 0.66, 300, intent=SHORT)
    st2 = _tick(p2, v2, http=_short_http())
    assert [c[1] for c in _cancels(v2)] == ["oid-1"] and p2.orders[o2["id"]]["state"] == "cancelled"
    assert [c[1:6] for c in _places(v2)] == [(SLUG, 0.65, 300, False, GTC_TIF)]
    assert _census(st2, "take_in_band") == 0 and _census(st2, "take_placed") == 0
    assert b2["last_plan"]["replaced"] == "replace_cent" and b2["last_plan"]["replaced_by"] == "his_level"
    assert "take_band" not in b2["last_plan"]


def test_e31_the_re_read_re_prices_the_touch_bound_rest_on_both_sides(monkeypatch):
    """E27 pinned the IOC WITHHELD on both sides (`ask_moved` /
    `bid_moved`) when the re-read's quote left the band. RE-PINNED at
    E31: the re-read fires only where the rest sits AT the touch bound,
    and it RE-PRICES rather than withholds -- the rest always goes, and
    always a tick off the touch the fresher read gives."""
    # LONG at the bound (his 0.52 on a 0.52 / 0.53 book): the ask rises to 0.54 -> more
    # room toward him, and his own cent 0.52 is already the wire: nothing to re-quote
    p = _his_at(0.52)
    b = p.add_book(ledger=0)
    v = _MovingVenue([(0.52, 0.53), (0.52, 0.54)], bid=0.52, ask=0.53, ioc_fill=300.0)
    st = _tick(p, v)
    assert _bbos(v).count(SLUG) == 2 and [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)]
    assert _census(st, "take_in_band") == 0 and _census(st, "ask_moved") == 0 and _census(st, "take_placed") == 0
    assert "ask_moved" not in b["last_plan"] and b["last_plan"]["decision"] == "rest"
    assert b["last_plan"]["rest_quote_at_send"] == {"bid": 0.52, "ask": 0.54, "bid_at_plan": 0.52, "ask_at_plan": 0.53}
    assert b["ledger_net"] == 0 and st["ops"] == 1
    # the ask FALLS onto his cent: the rest follows the bound down, never through it
    p1 = _his_at(0.52)
    p1.add_book(ledger=0)
    v1 = _MovingVenue([(0.52, 0.53), (0.51, 0.52)], bid=0.52, ask=0.53, ioc_fill=300.0)
    _tick(p1, v1)
    assert [c[2:6] for c in _places(v1)] == [(0.51, 300, False, GTC_TIF)] and _inserts(p1)[0][19] == 0.52
    # SHORT at the bound (his 0.65 on a 0.65 / 0.66 book): the bid falls to 0.64, so his own
    # cent 0.65 becomes reachable and the rest goes there instead of the touch's 0.66
    _shorts_on(monkeypatch)
    p2 = _short_at()
    v2 = _MovingVenue([(0.65, 0.66)] * SHORT_OPEN_READS + [(0.65, 0.66), (0.64, 0.66)],
                      bid=0.65, ask=0.66, ioc_fill=300.0)
    st2 = _tick(p2, v2, http=_short_http())
    b2 = next(iter(p2.books.values()))
    assert _bbos(v2).count(SLUG) == SHORT_OPEN_READS + 2
    assert [c[1:6] for c in _places(v2)] == [(SLUG, 0.65, 300, False, GTC_TIF)]
    assert _census(st2, "take_in_band") == 0 and _census(st2, "bid_moved") == 0 and _census(st2, "take_placed") == 0
    assert "bid_moved" not in b2["last_plan"] and b2["last_plan"]["decision"] == "rest"
    assert b2["ledger_net"] == 0 and st2["ops"] == 1 and _inserts(p2)[0][20] == "rest"
    # the unread re-read on the short: the tick's own cent, named
    p5 = _short_at()
    v5 = _MovingVenue([(0.65, 0.66)] * SHORT_OPEN_READS + [(0.65, 0.66), (None, None)],
                      bid=0.65, ask=0.66, ioc_fill=300.0)
    st5 = _tick(p5, v5, http=_short_http())
    assert [c[1:6] for c in _places(v5)] == [(SLUG, 0.66, 300, False, GTC_TIF)]
    assert _census(st5, "rest_quote_unread") == 1 and _census(st5, "ioc_quote_unread") == 0
    # the call budget spent (a flat short book already open): no re-read, the tick's cent
    monkeypatch.setattr(rules, "MIRROR_VENUE_CALLS_PER_TICK", 0)
    p6 = _short_at()
    b6 = _short_book(p6, ledger=0, avg=0.66)
    v6 = _Venue(bid=0.65, ask=0.66, ioc_fill=300.0)
    st6 = _tick(p6, v6, http=_short_http())
    assert [c[1:6] for c in _places(v6)] == [(SLUG, 0.66, 300, False, GTC_TIF)]
    assert _census(st6, "rest_reread_capped") == 1 and _census(st6, "ioc_reread_capped") == 0
    assert _census(st6, "take_in_band") == 0 and _census(st6, "take_placed") == 0 and b6["ledger_net"] == 0
    assert b6["last_plan"]["rest_reread_capped"] == {"guard_calls": 0, "budget": 0} and _bbos(v6).count(SLUG) == 1


def test_e27_059_absent_takes_nothing_in_band_on_either_side(monkeypatch):
    """RE-PINNED at E31: the 059 columns' absence gated the band's take
    (`uncounted`); there is no take to gate, so the rest goes on both
    sides either way and the 050 INSERT writes it."""
    monkeypatch.setattr(ml, "_order_cols_absent_logged", False)
    p, b, v = _long_world(ioc_fill=300.0)
    p.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    st = _tick(p, v)
    assert st["order_cols_absent"] == "UndefinedColumnError"
    assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)] and _census(st, "take_in_band") == 0
    assert "take_band" not in b["last_plan"] and len(_inserts(p)[0]) == 19
    _shorts_on(monkeypatch)
    p2 = _short_at()
    p2.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    v2 = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    st2 = _tick(p2, v2, http=_short_http())
    b2 = next(iter(p2.books.values()))
    assert [c[1:6] for c in _places(v2)] == [(SLUG, 0.65, 300, False, GTC_TIF)] and _census(st2, "take_in_band") == 0
    assert "take_band" not in b2["last_plan"] and len(_inserts(p2)[0]) == 19


def test_e31_the_room_is_read_on_the_collateral_at_the_cent_that_goes_out(monkeypatch):
    """E27 read the room at the BAND cent on the short (the collateral 1
    - 0.64 = 0.36). E31 reads it at the cent that goes out -- his own
    0.65, collateral 0.35 -- so a room of $100 buys floor(100 / 0.35) =
    285 shares, not 277: the same _room_qty, the same clip, four cents of
    collateral a share cheaper. `over_room` follows the cent with it."""
    _shorts_on(monkeypatch)
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 100.0)
    p = _short_at()
    v = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    st = _tick(p, v, http=_short_http())
    b = next(iter(p.books.values()))
    assert [c[1:6] for c in _places(v)] == [(SLUG, 0.65, 285, False, GTC_TIF)] and _census(st, "take_in_band") == 0
    assert b["ledger_net"] == 0 and _inserts(p)[0][11] == 285
    # the room short of one share at the cent that goes out: `over_room`, nothing placed
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 0.34)      # under the 0.35 of collateral a share
    p3 = _short_at()
    v3 = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
    st3 = _tick(p3, v3, http=_short_http())
    assert _census(st3, "over_room") == 1 and not _places(v3) and _census(st3, "take_in_band") == 0
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1250.0)
    asrc = inspect.getsource(ml._act)
    assert "qty = _room_qty(t, p.qty, wire, intent)" in asrc, "the room at the cent that goes out"


def test_e31_a_lengthened_wait_is_documentary_on_the_short_side_too(monkeypatch):
    """E27 pinned that a lengthened MIRROR_TAKE_AFTER_S made the short
    band rest first, through rules.take_allowed. Nothing on the money
    path reads that wait any more: the rest goes on the first tick at his
    cent whatever it says. The unit read that named every verdict is
    retired with `_short_take_band`; the pure helpers it called stand and
    are read directly here."""
    _shorts_on(monkeypatch)
    for wait in (0.0, 20.0, 600.0):
        monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", wait)
        p = _short_at()
        v = _Venue(bid=0.64, ask=0.66, ioc_fill=300.0)
        st = _tick(p, v, http=_short_http())
        b = next(iter(p.books.values()))
        assert [c[1:6] for c in _places(v)] == [(SLUG, 0.65, 300, False, GTC_TIF)], wait
        assert _census(st, "take_in_band") == 0 and "take_band" not in b["last_plan"], wait
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 0.0)
    assert not hasattr(ml, "_short_take_band") and not hasattr(ml, "_take_band")
    # the pure helpers the reader called, unmoved: the short band cent, its `in band` test,
    # the ceiling of his cent and the wait -- the record of the rule (docs 75)
    assert rules.short_band_cent(0.65) == 0.64 and rules.short_band_cent(0.643) == 0.63
    assert rules.short_band_cent(0.90) is None, "a width under a cent: no band"
    assert rules.short_take_in_band(0.64, 0.66, 0.65, 0.64) is True
    assert rules.short_take_in_band(0.63, 0.66, 0.65, 0.64) is False
    assert rules.short_take_in_band(0.65, 0.66, 0.65, 0.64) is False, "at his cent is not `in band`"
    for bid in (None, 0.0, 1.0, 1.5, float("nan"), True):
        assert rules.short_take_in_band(bid, 0.66, 0.65, 0.64) is False, bid
    assert rules.sell_wire(0.643) == 0.65 and rules.take_band_width(0.65, SHORT) == 0.0175
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 20.0)
    assert rules.take_allowed(0.0, None, NOW, 0.64, 0.66, 0.64, SELL) is False
    assert rules.take_allowed(0.0, NOW - 25, NOW, 0.64, 0.66, 0.64, SELL) is True
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 0.0)


# ------------------------------------------------------ the names, the docs

def test_e27_no_census_name_no_migration_render_ops_hashed_as_left_and_the_sites_by_source():
    keys = ml.CENSUS_KEYS
    # no name added: the tail pins hold (test_e14's keys[-54] on 52e1d52 is keys[-58] over E25's four
    # names, 0e72120: 233 -> 237 keys; E19's keys[-13] / [-12]); E29 (FILL lane 29, the desk's exit ends the
    # book's adds: hand_exit / hand_held / hand_held_unread / hand_exit_write_failed before drift_smaller_open)
    # moves it once more: 237 -> 241, -48 -> -52 (pinned in the lane's worktree on 6c0830d; E28's five names land
    # between E25's and E29's and re-cut it by five more at landing)
    assert len(keys) == 257 and keys[-68] == "take_in_band" and keys[-13] == "drift_smaller_open"
    assert keys[-12] == "registered_no_increase" and keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    from tests.test_e31_maker_only import _code
    src = inspect.getsource(ml)
    code = _code(ml)                    # the code alone: a retired rule may still be NAMED in a paragraph
    for name in ("short_take_in_band", "take_band_short", "short_band", "take_tolerance"):
        assert f'"{name}"' not in src, name
    # RE-PINNED at E31 (FILL lane 31, 2026-09-10): this lane added no census name and E31 takes
    # none away -- what it takes away is the EMIT SITE of lane 2's `take_in_band`, which this lane
    # widened and made the short side share. There is none left, on either side, and the two
    # readers that made the decision are deleted with the take
    assert code.count('_mirror_stop("take_in_band", w)') == 0
    assert "take_in_band" in ml.CENSUS_KEYS and ml._new_stats()["census"]["take_in_band"] == 0
    assert not hasattr(ml, "_take_band") and not hasattr(ml, "_short_take_band")
    assert not hasattr(ml, "_entry_take") and not hasattr(ml, "_ioc_reread")
    asrc = _code(ml._act)
    for name in ("_take_band", "_short_take_band", "_entry_take", "in_band", "take_first"):
        assert name not in asrc, name
    assert 'tif="IOC"' not in asrc and "IMMEDIATE_OR_CANCEL" not in asrc
    # WHAT STANDS AT THE SITE: one wire, one placement, on both sides of both legs
    assert asrc.count("_wire_for (") >= 1 and "rules . maker_wire" in _code(ml._wire_for)
    # the worker reads the two constants through NOTHING at all now
    for bad in ('capped_env("MIRROR_TAKE_BAND', '_env_float("MIRROR_TAKE_BAND', "MIRROR_TAKE_BAND_FRAC = "):
        assert bad not in src, bad
    assert "MIRROR_TAKE_BAND" not in code and "take_band_width" not in code
    assert "short_band_cent" not in code and "short_take_in_band" not in code
    # the four pure helpers this lane ADDED stand in the rules module, unmoved and tested above
    for fn in ("take_band_width", "short_band_cent", "short_take_in_band", "band_cent"):
        assert callable(getattr(rules, fn)), fn
    assert "_num(MIRROR_TAKE_BAND), _num(MIRROR_TAKE_BAND_FRAC)" in inspect.getsource(rules.take_band_width)
    # the decision word is rules.order_decision's alone and it can no longer be asked for:
    # `is_take` is the literal False at the one call
    assert inspect.getsource(rules.order_decision).count('"take_in_band"') == 1
    psrc = inspect.getsource(ml._place_reserved)
    assert 'rules.order_decision(action, False, wire_intent == "ORDER_INTENT_SELL_SHORT")' in psrc
    assert "in_band=bool(in_band) and is_take" not in psrc and "is_take" not in _code(ml._place_reserved)
    # no migration (061 the newest); render-ops.yml as this lane leaves it (take-band's third statement); 059's
    # comment as lane 2 left it
    files = sorted(x.name for x in (ROOT / "backend" / "migrations").glob("*.sql"))
    assert files[-1] == "061_fill_answers_cause_orders_fast.sql"
    assert _sha_file(ROOT / ".github" / "workflows" / "render-ops.yml") == RENDER_OPS_SHA
    assert _sha_file(ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql") == MIGRATION_059_SHA
    sql = (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "'take_in_band' is" in sql and "reserved for the entry band" in sql


def test_e27_every_name_is_emitted_here_too(monkeypatch):
    """The lane adds no census name. RE-PINNED at E31: the hook ran the
    SHORT side's emit of `take_in_band`; the name is retired, so it runs
    the same short world and proves the census stays at ZERO on both
    sides (the worker file's coverage read carries the name in its
    `unreachable` set with lane 31 named)."""
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.02)
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND_FRAC", 0.05)
    test_e31_the_short_add_rests_at_his_sell_cent_or_a_tick_over_the_bid(monkeypatch)
    assert ml._MIRROR_CENSUS.get("take_in_band|rn1", 0) == 0
    assert ml._MIRROR_CENSUS.get("take_placed|rn1", 0) == 0


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
