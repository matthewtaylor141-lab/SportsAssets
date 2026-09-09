"""E14 (2026-09-08; FILL program lane 2): the entry takes inside a ONE-cent
band at first sight, named `take_in_band`.

Owner (~17:45Z, verbatim): "We are being filled on his losers and
missing his winners. That is adverse selection at the fill, not a
sizing bug ... E14 (take at once when the ask is inside the band) is
the lane for it and it is still pending". Decision D1 (a): one cent,
live from the deploy, with the 24 h gate.

THE RULE. On a LONG book, on a tick where NO order of ours stands (the
open, the tick after a re-quote cancel, the fast tick's wake), an entry
whose ask is ABOVE his cent but no more than rules.MIRROR_TAKE_BAND
(0.01, capped_env floor 0.0: env may only LOWER it, 0 = off) over his
UNROUNDED price sends ONE IOC limited at rules.band_cent(his) =
buy_wire(his + band), re-read at the send as every IOC is (E18), the
unfilled remainder resting at his cent as today. An ask at or under his
cent takes as today (decision 'take'); above the band cent rests as
today ('rest'). A rest already standing is NEVER converted (the keep
branch is E18's queue position; lane 6, gated); a short book's add is
never band-taken; a reduce never reads the band. Row decision
'take_in_band' (the word migration 059 reserved); plan `take_band`
{his_cent, band, band_cent, bid, ask, verdict}; census `take_in_band`.

The rows (hard2/hourly_1737.txt 1746-1747): 1,922 entry rests, 1,827
with a band, 661 filled (34.4%), 16.0% at or through his cent, 42.2%
inside 1c, band median 2.0c; 557 IOC takes, 364 filled (65.4%). The
shapes: book 544 / 611 (verify_1750 564: BUY 235 / 2,400 / 74 @0.520
at his 0.520; hourly_1737 1726: 611's 16 IOCs expired 0), Martinez 534
(his 0.61, the market 0.81 / 0.82), book 347's SHORT (book_347_1750
398), book 661's standing rest (closerows_1750 529, order 3632 -- a
SHORT book's `flatten_paired` cover rest, 609 @0.620 filled 89.24 then
cancelled `replace`; the long-entry rest at 0.62 is the plan's stand-in
for 'a rest standing, the ask a cent above').

Driven against the worker file's fakes (its autouse rails are
imported), test_e18_rest_life's moving venue and test_e9_fast_path's
fast tick.
"""
import hashlib
import importlib
import inspect
import pathlib
import re
import types

import pytest

from sportsassets.analytics import mirror as mi  # noqa: F401 -- the plan's module, read by the pins
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e18_rest_life import _MovingVenue, _bbos, _decisions, _inserts
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _UndefinedColumn, _Venue, _armed, _cancels, _census, _fill, _places,
    _pool, _run, _short_book, _short_http, _short_world, _shorts_on, _tick,
)

GTC_TIF = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
IOC_TIF = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
ROOT = pathlib.Path(__file__).resolve().parents[2]
NEW_NAMES = ("take_in_band",)
# rules.admission and the open's catch-up allowance are NOT this lane's
# (the band is not an admission allowance): their source at ab2525c,
# hashed -- the reviewer re-derives it with `git show ab2525c:backend/
# sportsassets/analytics/mirror_live_rules.py`
ADMISSION_SHA = "a10630d6d3a3a62c"
OPEN_CATCHUP_SHA = "6b9e8f2ffe1d3538"
CATCHUP_LINES = (
    'MIRROR_CATCHUP_TOL_CENTS = capped_env("MIRROR_CATCHUP_TOL_CENTS", 2.0, floor=0.0)',
    'MIRROR_CATCHUP_PCT = capped_env("MIRROR_CATCHUP_PCT", 0.10, floor=0.0)',
    'MIRROR_CATCHUP_MAX_CENTS = capped_env("MIRROR_CATCHUP_MAX_CENTS", 5.0, floor=0.0)',
)


def _sha(obj) -> str:
    return hashlib.sha256(inspect.getsource(obj).encode()).hexdigest()[:16]


@pytest.fixture(autouse=True)
def _band_on(_armed, monkeypatch):
    """The band ON at the code default (0.01) for every test here. The
    worker file's fixture world (`_armed`, imported above) holds it at 0
    -- its default quote sits exactly a cent above his cent, so E4's
    rest-first pins there would read the band IOC first -- and every
    test of the band sets it explicitly, the convention that file names.
    Pinned against the environment: the runner's MIRROR_TAKE_BAND, if
    any, is not what these tests read."""
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.01)
    yield


def _his_at(px, size=300):
    """His one BUY of the long token at `px`, 3,000 s ago (the E18 take
    world's shape); the book flat, the plan wants `size` at ratio 1.0."""
    return _pool(fills=[_fill(M, "BUY", size, px, NOW - 3000)])


def _band_world(bid=0.52, ask=0.53, ioc_fill=300.0, his=0.52, **kw):
    """Book 544 / 611's shape: his 0.520, the book flat, the tick's quote
    bid 0.52 / ask 0.53 -- the ask exactly one cent above his cent."""
    p = _his_at(his)
    b = p.add_book(ledger=0)
    v = _Venue(bid=bid, ask=ask, ioc_fill=ioc_fill, **kw)
    return p, b, v


def _band_field(b):
    return b["last_plan"]["take_band"]


# ------------------------------------------------------------- the rule

def test_e14_band_cent_is_his_floor_cent_plus_the_band_and_none_under_a_cent():
    assert rules.MIRROR_TAKE_BAND == 0.01
    # his 0.479 -> 0.489 -> 0.48; his 0.471 -> 0.481 -> 0.48: never more
    # than the band over his UNROUNDED price (0.48 <= 0.481)
    assert rules.band_cent(0.479) == 0.48 and rules.band_cent(0.471) == 0.48
    assert rules.band_cent(0.52) == 0.53 and rules.band_cent(0.30) == 0.31
    assert rules.band_cent(0.479) <= 0.479 + 0.01 and rules.band_cent(0.471) <= 0.471 + 0.01
    # a band under a cent is no band: 0.472 + 0.005 = 0.477 floors to his own 0.47
    assert rules.band_cent(0.472, 0.005) is None
    assert rules.band_cent(0.479, 0.005) == 0.48, "0.484 floors to 0.48, a cent above his 0.47"
    # band 0 (the lane off), negative, NaN, inf, a bool, a string: None
    for band in (0, 0.0, -0.01, -1, float("nan"), float("inf"), True, "0.01", None):
        if band is None:
            continue
        assert rules.band_cent(0.52, band) is None, band
    # his price not a price in (0, 1), or a bool: None
    for his in (None, 0, 0.0, 1.0, 1.5, -0.2, True, "0.52", float("nan")):
        assert rules.band_cent(his) is None, his
    # the sum with no cent on the ladder: None (never a cent above 0.99)
    assert rules.band_cent(0.99) is None and rules.band_cent(0.995) is None
    assert rules.band_cent(0.985) == 0.99
    # the sweep: at every cent of his the band cent is exactly one above, and
    # at every 6-decimal price it is at most the band over him
    for c in range(1, 99):
        his = c / 100.0
        assert rules.band_cent(his) == round(his + 0.01, 2), his
    for i in range(1, 990):
        his = round(i / 1000.0 + 0.0004, 6)
        bc = rules.band_cent(his)
        assert bc is None or (rules.buy_wire(his) < bc <= his + 0.01 + 1e-9), (his, bc)


def test_e14_take_in_band_is_strictly_above_his_cent_and_at_or_under_the_band_cent():
    hc, bc = 0.47, 0.48
    assert rules.take_in_band(0.46, 0.47, hc, bc) is False, "at his cent: the at-level take, never the band"
    assert rules.take_in_band(0.46, 0.46, hc, bc) is False, "through his cent: the at-level take"
    assert rules.take_in_band(0.46, 0.48, hc, bc) is True
    assert rules.take_in_band(0.46, 0.49, hc, bc) is False, "above the band cent: the rest"
    for ask in (None, 0.0, -0.0, 1e-12, 1.0, 1.5, 1e308, float("nan"), True, "0.48"):
        assert rules.take_in_band(0.46, ask, hc, bc) is False, ask
    # the bid is not read
    for bid in (None, 0.0, 1.0, float("nan"), 0.49):
        assert rules.take_in_band(bid, 0.48, hc, bc) is True, bid
    # a cent nobody read, or a band cent not above his cent: never in band
    assert rules.take_in_band(0.46, 0.48, None, bc) is False
    assert rules.take_in_band(0.46, 0.48, hc, None) is False
    assert rules.take_in_band(0.46, 0.48, hc, 0.47) is False and rules.take_in_band(0.46, 0.48, hc, 0.46) is False
    assert rules.take_in_band(0.46, 0.48, 0.0, bc) is False and rules.take_in_band(0.46, 0.48, hc, 1.0) is False
    # the definition, in the module's own words
    src = inspect.getsource(rules.take_in_band)
    assert "not at_or_through(BUY, bid, ask, hc)" in src and "at_or_through(BUY, bid, ask, bc)" in src


def test_e14_the_band_constant_only_lowers_from_the_environment(monkeypatch):
    src = inspect.getsource(rules)
    assert 'MIRROR_TAKE_BAND = capped_env("MIRROR_TAKE_BAND", 0.01, floor=0.0)' in src
    assert src.count('capped_env("MIRROR_TAKE_BAND"') == 1 and 'min_wait_env("MIRROR_TAKE_BAND' not in src
    assert '_env_float("MIRROR_TAKE_BAND' not in src
    for name in ("MIRROR_TAKE_BAND", "band_cent", "take_in_band"):
        assert name in rules.__all__, name
    try:
        # env may only LOWER: 0.02 lands on the default (the ceiling); 0.005
        # and 0 are honoured; junk / inf are the default; a NEGATIVE value
        # lands on the FLOOR (capped_env: "an override under the floor lands
        # on the floor, never past it") -- 0.0, the band off
        for raw, want in (("0.02", 0.01), ("0.005", 0.005), ("0", 0.0), ("0.01", 0.01), ("junk", 0.01),
                          ("inf", 0.01), ("", 0.01), ("-1", 0.0), ("1e400", 0.01)):
            monkeypatch.setenv("MIRROR_TAKE_BAND", raw)
            mod = importlib.reload(rules)
            assert mod.MIRROR_TAKE_BAND == want, (raw, mod.MIRROR_TAKE_BAND)
            # band_cent reads the constant at call time
            if want == 0.01:
                assert mod.band_cent(0.52) == 0.53
            elif want == 0.005:
                assert mod.band_cent(0.52) is None and mod.band_cent(0.529) == 0.53
            else:
                assert mod.band_cent(0.52) is None, "the band off: no cent, the rest as today"
    finally:
        monkeypatch.delenv("MIRROR_TAKE_BAND", raising=False)
        importlib.reload(rules)
    assert rules.MIRROR_TAKE_BAND == 0.01 and rules.band_cent(0.52) == 0.53
    # MIRROR_TAKE_AFTER_S and MIRROR_EXIT_TOL untouched
    assert rules.MIRROR_TAKE_AFTER_S == 0.0 and rules.MIRROR_EXIT_TOL == 0.01
    assert 'MIRROR_TAKE_AFTER_S = min_wait_env("MIRROR_TAKE_AFTER_S", 0.0)' in src
    # the decisions' words: 'take_in_band' only on an add's IOC in band
    assert rules.order_decision("add", True, False, True) == "take_in_band"
    # FILL lane 3 (2026-09-08) re-pinned: a short cover's IOC in band is 'cover_in_band' and a
    # reduce's IOC in band 'exit_take_in_band' -- the exit band's own words; neither is the entry's
    assert rules.order_decision("add", True, True, True) == "cover_in_band", "a short cover wins"
    assert rules.order_decision("reduce", True, False, True) == "exit_take_in_band", "a reduce's IOC is never the entry band's"
    assert rules.order_decision("add", False, False, True) == "rest", "not an IOC: never 'take_in_band'"
    assert rules.order_decision("reduce", False, False, True) == "exit_rest"
    assert rules.order_decision("add", True, False, "yes") == "take", "in_band is a bool, not a truthy string"
    # every path with the default is E18's, byte for byte
    assert rules.order_decision("add", False, False) == "rest" and rules.order_decision("add", True, False) == "take"
    assert rules.order_decision("reduce", False, False) == "exit_rest" and rules.order_decision("reduce", True, False) == "take"
    assert rules.order_decision("reduce", False, True) == rules.order_decision("reduce", True, True) == "cover"


# --------------------------------------------------- the worker, the shapes

def test_e14_book_544s_shape_first_sight_sends_one_ioc_at_the_band_cent_and_rests_the_remainder():
    """Book 544 / 611 (verify_1750 564: BUY 235 / 2,400 / 74 @0.520 at his
    0.520; 611's 16 IOCs expired 0, hourly_1737 1726): his 0.52, bid 0.52
    / ask 0.53 -> ONE IOC at 0.53, `take_in_band`, the row's decision
    'take_in_band', wire 0.53, his_level 0.52, `take_first`."""
    # (a) fills whole: no rest
    p, b, v = _band_world(ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.53, 300, False, IOC_TIF)]
    assert _census(st, "take_in_band") == 1 and _census(st, "take_first") == 1 and _census(st, "take_placed") == 1
    assert _census(st, "take_at_his_level") == 0 and _census(st, "rest_placed") == 0 and _census(st, "ask_moved") == 0
    assert b["ledger_net"] == 300 and b["open_order_id"] is None
    lp = b["last_plan"]
    assert lp["decision"] == "take_in_band"
    assert lp["take_band"] == {"his_cent": 0.52, "band": 0.01, "band_cent": 0.53, "bid": 0.52, "ask": 0.53,
                               "verdict": "in_band"}
    assert lp["take_qty"] == 300 and lp["take_filled"] == 300.0
    ins = _inserts(p)
    assert len(ins) == 1 and len(ins[0]) == 22
    assert (ins[0][3], ins[0][5], ins[0][8], ins[0][10], ins[0][11]) == ("take", "IOC", 0.52, 0.53, 300)
    assert ins[0][16] == 0.53 and ins[0][19] == 0.53 and ins[0][20] == "take_in_band" and ins[0][21] == str(NOW - 3000)
    # the cents paid over him, readable off the row: wire - floor(his_level * 100) / 100 = 0.01
    assert round(ins[0][10] - (int(ins[0][8] * 100) / 100.0), 2) == 0.01
    assert _bbos(v).count(SLUG) == 2, "the tick's read and the re-read before the send"
    # (b) fills part: the remainder rests at buy_price(0.52, 0.52) = 0.52, decision 'rest'
    p2, b2, v2 = _band_world(ioc_fill=100.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.53, 300, False, IOC_TIF), (0.52, 200, False, GTC_TIF)]
    assert b2["ledger_net"] == 100 and b2["open_order_id"] is not None
    assert _census(st2, "take_in_band") == 1 and _census(st2, "rest_placed") == 1
    ins2 = _inserts(p2)
    assert [(a[5], a[10], a[11], a[19], a[20]) for a in ins2] == [("IOC", 0.53, 300, 0.53, "take_in_band"),
                                                                ("GTC", 0.52, 200, None, "rest")]
    assert b2["last_plan"]["decision"] == "rest", "the plan's decision names the row placed last"
    assert b2["last_plan"]["take_band"]["verdict"] == "in_band"
    # (c) expires 0 (611's shape): the whole quantity rests at 0.52
    p3, b3, v3 = _band_world(ioc_fill=0.0)
    st3 = _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.53, 300, False, IOC_TIF), (0.52, 300, False, GTC_TIF)]
    assert b3["ledger_net"] == 0 and _census(st3, "take_in_band") == 1 and _census(st3, "rest_placed") == 1
    assert _inserts(p3)[1][20] == "rest" and st3["ops"] == 2
    # the one open order is the rest at his cent, never a second IOC
    assert [o["tif"] for o in p3.orders.values() if o["state"] == "open"] == ["GTC"]


def test_e14_the_re_read_withholds_the_band_ioc_when_the_ask_left_the_band(monkeypatch):
    """The re-read's ask above the band cent -> `ask_moved` {0.53, 0.54,
    wire 0.53}, no IOC, the whole quantity rests at 0.52, one op spent;
    the unread re-read -> `ioc_quote_unread` -> the rest; the guard budget
    at 0 -> `ioc_reread_capped` -> the rest. The band is judged on the
    tick's quote AND on the re-read: both must hold."""
    p = _his_at(0.52)
    b = p.add_book(ledger=0)
    v = _MovingVenue([(0.52, 0.53), (0.52, 0.54)], bid=0.52, ask=0.53, ioc_fill=300.0)
    st = _tick(p, v)
    assert _bbos(v).count(SLUG) == 2
    assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)], "no IOC; the rest as today"
    assert _census(st, "take_in_band") == 1, "the decision is counted where it is made"
    assert _census(st, "ask_moved") == 1 and _census(st, "take_placed") == 0 and _census(st, "rest_placed") == 1
    lp = b["last_plan"]
    assert lp["ask_moved"] == {"ask_at_plan": 0.53, "ask_at_send": 0.54, "wire": 0.53}
    assert lp["ioc_quote_at_send"] == {"bid": 0.52, "ask": 0.54, "bid_at_plan": 0.52, "ask_at_plan": 0.53}
    assert lp["take_band"]["verdict"] == "in_band" and lp["decision"] == "rest"
    assert b["ledger_net"] == 0 and st["ops"] == 1, "the withheld IOC spent no op"
    ins = _inserts(p)
    assert len(ins) == 1 and ins[0][5] == "GTC" and ins[0][19] is None and ins[0][20] == "rest"
    # the re-read at his cent exactly (through the band): the IOC goes at the band cent
    p1 = _his_at(0.52)
    b1 = p1.add_book(ledger=0)
    v1 = _MovingVenue([(0.52, 0.53), (0.51, 0.52)], bid=0.52, ask=0.53, ioc_fill=300.0)
    _tick(p1, v1)
    assert [c[2:6] for c in _places(v1)] == [(0.53, 300, False, IOC_TIF)] and _inserts(p1)[0][19] == 0.52
    assert b1["last_plan"]["decision"] == "take_in_band"
    # the unread re-read
    p2 = _his_at(0.52)
    b2 = p2.add_book(ledger=0)
    v2 = _MovingVenue([(0.52, 0.53), (None, None)], bid=0.52, ask=0.53, ioc_fill=300.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.52, 300, False, GTC_TIF)] and _census(st2, "ioc_quote_unread") == 1
    assert _census(st2, "take_in_band") == 1 and _census(st2, "take_placed") == 0 and b2["ledger_net"] == 0
    # the call budget spent: no re-read, no IOC, the rest
    monkeypatch.setattr(rules, "MIRROR_VENUE_CALLS_PER_TICK", 0)
    p3 = _his_at(0.52)
    b3 = p3.add_book(ledger=0)
    v3 = _MovingVenue([(0.52, 0.53), (0.52, 0.53)], bid=0.52, ask=0.53, ioc_fill=300.0)
    st3 = _tick(p3, v3)
    assert _bbos(v3).count(SLUG) == 1 and [c[2:6] for c in _places(v3)] == [(0.52, 300, False, GTC_TIF)]
    assert _census(st3, "ioc_reread_capped") == 1 and _census(st3, "take_placed") == 0 and b3["ledger_net"] == 0
    assert b3["last_plan"]["ioc_reread_capped"] == {"guard_calls": 0, "budget": 0}


def test_e14_an_ask_at_his_cent_takes_at_his_level_never_in_band():
    p, b, v = _band_world(bid=0.51, ask=0.52, ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, IOC_TIF)]
    assert _census(st, "take_at_his_level") == 1 and _census(st, "take_in_band") == 0
    assert _inserts(p)[0][20] == "take" and b["last_plan"]["decision"] == "take"
    assert b["last_plan"]["take_band"] == {"his_cent": 0.52, "band": 0.01, "band_cent": 0.53, "bid": 0.51,
                                           "ask": 0.52, "verdict": "at_level"}
    # through his cent: the same
    p2, b2, v2 = _band_world(bid=0.50, ask=0.51, ioc_fill=300.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.52, 300, False, IOC_TIF)] and _census(st2, "take_in_band") == 0
    assert b2["last_plan"]["take_band"]["verdict"] == "at_level"


def test_e14_martinez_534s_shape_rests_out_of_band_and_the_admission_is_untouched():
    """Martinez 534 (hourly_1737 1172; task 71): his 0.61, the market
    0.81 / 0.82 -> verdict `out`, no IOC, the rest at buy_price(0.61,
    0.81) = 0.61, decision 'rest'. And the band is NOT a catch-up
    allowance: rules.admission and MIRROR_CATCHUP_TOL_CENTS / _PCT /
    _MAX_CENTS are byte for byte ab2525c's."""
    p = _his_at(0.61)
    b = p.add_book(ledger=0)
    v = _Venue(bid=0.81, ask=0.82, ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.61, 300, False, GTC_TIF)]
    assert _census(st, "take_in_band") == 0 and _census(st, "take_placed") == 0 and _census(st, "rest_placed") == 1
    assert _bbos(v).count(SLUG) == 1, "no re-read: no IOC was decided"
    assert b["last_plan"]["take_band"] == {"his_cent": 0.61, "band": 0.01, "band_cent": 0.62, "bid": 0.81,
                                           "ask": 0.82, "verdict": "out"}
    assert _inserts(p)[0][20] == "rest" and b["last_plan"]["decision"] == "rest"
    # two cents above: out as well (the band is one cent, never two)
    p2 = _his_at(0.52)
    b2 = p2.add_book(ledger=0)
    v2 = _Venue(bid=0.53, ask=0.54, ioc_fill=300.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.52, 300, False, GTC_TIF)] and _census(st2, "take_in_band") == 0
    assert b2["last_plan"]["take_band"]["verdict"] == "out"
    # the source pins: the admission and the open's catch-up allowance untouched
    assert _sha(rules.admission) == ADMISSION_SHA and _sha(rules.open_catchup) == OPEN_CATCHUP_SHA
    src = inspect.getsource(rules)
    for line in CATCHUP_LINES:
        assert src.count(line) == 1, line
    for fn in (rules.admission, rules.open_catchup, ml._tick_candidate, ml._ioc_reread, rules.exit_terms,
               rules.rest_decision, rules.take_allowed, rules.at_or_through, rules.buy_price):
        s = inspect.getsource(fn)
        for name in ("band_cent", "take_in_band", "MIRROR_TAKE_BAND", "take_band"):
            # a whole word: open_catchup's own `_catchup_band_cents` is not the lane's;
            # FILL lane 3 (2026-09-08) gave exit_terms its OWN `take_band` key (the
            # exit's band cent, the plan's name for it) -- the entry band's three
            # names stay absent from it
            if fn is rules.exit_terms and name == "take_band":
                continue
            assert not re.search(rf"(?<![\w]){name}(?![\w])", s), (fn.__name__, name)


def test_e14_book_347s_short_shape_rests_at_the_short_wire_never_in_band(monkeypatch):
    """Book 347 (aec-wta-julpar-lucste, ORDER_INTENT_BUY_SHORT; book_347_1750
    398: 30 `replace` cancels, 0 filled): a SELL_LONG add plan on a short
    book with the contract bid a cent inside the short wire -> the rest
    at _short_wire (0.32), decision 'rest', no `take_in_band`, no
    `take_band` on the plan (the band is a long book's read)."""
    _shorts_on(monkeypatch)
    p = _short_world()
    v = _Venue(bid=0.31, ask=0.32)
    st = _tick(p, v, http=_short_http())
    b = next(iter(p.books.values()))
    assert b["intent"] == "ORDER_INTENT_BUY_SHORT" and b["target"] == -300
    assert [c[1:6] for c in _places(v)] == [(SLUG, 0.32, 300, False, GTC_TIF)]
    assert _census(st, "take_in_band") == 0 and _census(st, "take_placed") == 0 and _census(st, "rest_placed") == 1
    assert _census(st, "short_open") == 1
    assert "take_band" not in b["last_plan"] and b["last_plan"]["decision"] == "rest"
    assert _inserts(p)[0][20] == "rest" and _inserts(p)[0][18] == "ORDER_INTENT_BUY_SHORT"
    # an ADD onto a held short: the same
    p2 = _short_world()
    b2 = _short_book(p2, ledger=-100)
    v2 = _Venue(bid=0.31, ask=0.32, held={SLUG: -100})
    st2 = _tick(p2, v2, http=_short_http())
    assert [c[1:6] for c in _places(v2)] == [(SLUG, 0.32, 200, False, GTC_TIF)]
    assert _census(st2, "take_in_band") == 0 and _census(st2, "short_add") == 1 and "take_band" not in b2["last_plan"]
    # the S4 cover's decision word stands (FILL lane 3 re-pinned: in band it is the cover's own band word)
    assert rules.order_decision("reduce", True, True, True) == "cover_in_band"
    assert rules.order_decision("reduce", True, True, False) == "cover"


def test_e14_book_661s_shape_a_standing_rest_is_never_converted_and_the_requote_band_takes():
    """Book 661 (closerows_1750 529, order 3632: a rest partially filled
    then replaced -- a SHORT book's `flatten_paired` cover rest, 609
    @0.620 filled 89.24 then cancelled `replace`; the long-entry rest
    here is the plan's stand-in for that shape): a rest standing at
    0.62 with his 0.62 and the ask at
    0.63 -> `keep`, `resting_above_level` + `take_refused_price` as
    today, NO band take, no `take_band` on the plan (the keep branch is
    lane 6, gated). After a `replace_cent` cancel the re-quote at his
    NEW cent DOES band-take when the ask is one cent above it: the
    cancel spent the replace budget, the band IOC is not a cancel."""
    p = _his_at(0.62)
    b = p.add_book(ledger=0)
    o = p.add_order(b, wire=0.62, qty=300, placed_ts=NOW - 100)
    v = _Venue(bid=0.62, ask=0.63, ioc_fill=300.0)
    v.rest("oid-1", "BUY", 0.62, 300)
    st = _tick(p, v)
    assert not _cancels(v) and not _places(v) and p.orders[o["id"]]["state"] == "open"
    assert _census(st, "open_order_pending") == 1 and _census(st, "resting_above_level") == 1
    assert _census(st, "take_refused_price") == 1 and _census(st, "take_in_band") == 0
    assert "take_band" not in b["last_plan"] and b["last_plan"]["open_order"] == o["id"]
    assert _bbos(v).count(SLUG) == 1
    # the re-quote: the rest stands at his OLD cent 0.60, 100 s old; his
    # level now reads 0.62 -> the wire moves two cents -> `replace_cent` ->
    # the re-plan on the no-order path -> the at-level test fails (0.63 >
    # 0.62) -> the band IOC at 0.63
    p2 = _his_at(0.62)
    b2 = p2.add_book(ledger=0)
    o2 = p2.add_order(b2, wire=0.60, qty=300, placed_ts=NOW - 100)
    v2 = _Venue(bid=0.62, ask=0.63, ioc_fill=300.0)
    v2.rest("oid-1", "BUY", 0.60, 300)
    st2 = _tick(p2, v2)
    assert [c[1] for c in _cancels(v2)] == ["oid-1"] and p2.orders[o2["id"]]["reason"] == "replace"
    assert _decisions(p2) == [(o2["id"], "replace_cent")] and b2["last_plan"]["replaced"] == "replace_cent"
    assert [c[2:6] for c in _places(v2)] == [(0.63, 300, False, IOC_TIF)]
    assert _census(st2, "take_in_band") == 1 and _census(st2, "take_at_his_level") == 0 and st2["requotes"] == 1
    assert b2["last_plan"]["take_band"]["verdict"] == "in_band" and b2["last_plan"]["decision"] == "take_in_band"
    assert b2["ledger_net"] == 300 and _inserts(p2)[0][20] == "take_in_band" and _inserts(p2)[0][8] == 0.62
    # the same re-quote with the replace budget spent: `replace_capped`, the
    # rest kept, no band IOC (the band never spends the budget, the cancel does)
    p3 = _his_at(0.62)
    b3 = p3.add_book(ledger=0)
    for _ in range(int(rules.MIRROR_MAX_REPLACES_PER_HOUR)):
        p3.add_order(b3, state="cancelled", reason="replace", done_at=NOW - 100, order_id=None)
    p3.add_order(b3, wire=0.60, qty=300, placed_ts=NOW - 100)
    v3 = _Venue(bid=0.62, ask=0.63, ioc_fill=300.0)
    v3.rest("oid-1", "BUY", 0.60, 300)
    st3 = _tick(p3, v3)
    assert _census(st3, "replace_capped") == 1 and not _cancels(v3) and not _places(v3)
    assert _census(st3, "take_in_band") == 0


def test_e14_a_band_ioc_refused_ops_capped_rests_on_the_requote_credit(monkeypatch):
    """Book 661's re-quote shape at the ops budget's last op (E2 review
    round 3, LOW-6 meets the band): the rest at his OLD cent 0.60 is
    cancelled `replace_cent` (the tick's one op, the credit granted),
    the band IOC at 0.63 is `ops_capped`, and the whole plannable
    quantity RESTS at his cent 0.62 on the credit -- the cohort's book
    is not left bare for the tick with its rest cancelled. One op, no
    IOC, `take_in_band` 1 (the decision), `ops_capped` 1, `rest_placed`
    1, the row's decision 'rest', the plan's verdict `in_band`."""
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 1)
    p = _his_at(0.62)
    b = p.add_book(ledger=0)
    o = p.add_order(b, wire=0.60, qty=300, placed_ts=NOW - 100)
    v = _Venue(bid=0.62, ask=0.63, ioc_fill=300.0)
    v.rest("oid-1", "BUY", 0.60, 300)
    st = _tick(p, v)
    assert [c[1] for c in _cancels(v)] == ["oid-1"] and p.orders[o["id"]]["reason"] == "replace"
    assert _decisions(p) == [(o["id"], "replace_cent")]
    assert [c[2:6] for c in _places(v)] == [(0.62, 300, False, GTC_TIF)], "no IOC went; the rest did, on the credit"
    assert st["ops"] == 1 and _census(st, "ops_capped") == 1 and _census(st, "rest_placed") == 1
    assert _census(st, "take_in_band") == 1 and _census(st, "take_placed") == 0 and _census(st, "take_first") == 0
    assert b["ledger_net"] == 0 and len([x for x in p.orders.values() if x["state"] == "open"]) == 1
    assert b["last_plan"]["take_band"]["verdict"] == "in_band" and b["last_plan"]["decision"] == "rest"
    assert _inserts(p)[0][5] == "GTC" and _inserts(p)[0][10] == 0.62 and _inserts(p)[0][20] == "rest"
    # the mechanism, by name: the credit rest reads nothing of `in_band`
    src = inspect.getsource(ml._entry_take)
    assert 'if (res == "ops_capped" and book["id"] in t.requote_credit\n' in src


def test_e14_the_fast_tick_band_takes_on_the_wake_and_skips_an_open_order():
    p, b, v = _band_world(ioc_fill=300.0)
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {} and [c[2:6] for c in _places(v)] == [(0.53, 300, False, IOC_TIF)]
    assert _census(fs, "fast_tick_placed") == 1 and _census(fs, "take_in_band") == 1
    assert b["ledger_net"] == 300 and b["last_plan"]["decision"] == "take_in_band"
    assert b["last_plan"]["take_band"]["verdict"] == "in_band" and _inserts(p)[0][20] == "take_in_band"
    # a rest standing: `order_open`, nothing read, nothing placed
    p2, b2, v2 = _band_world(ioc_fill=300.0)
    p2.add_order(b2, wire=0.52, qty=300, placed_ts=NOW - 20)
    v2.rest("oid-1", "BUY", 0.52, 300)
    _walk()
    fs2 = _fast(p2, v2)
    assert _skips(fs2) == {CID: "order_open"} and _bbos(v2) == [] and not _places(v2)
    assert _census(fs2, "take_in_band") == 0 and "cancel" not in [c[0] for c in v2.calls]
    # _fast_gate is untouched by the lane
    assert "take_band" not in inspect.getsource(ml._fast_gate) and "band_cent" not in inspect.getsource(ml._fast_gate)


def test_e14_a_lengthened_wait_makes_the_band_rest_first(monkeypatch):
    """env MIRROR_TAKE_AFTER_S=20 (min_wait_env: only lengthened): the
    band reads the wait through rules.take_allowed exactly as the
    at-level take does. With no rest and no arm the plan has not waited
    -> verdict `waiting`, the rest at his cent; a rest 25 s old with the
    ask a cent above -> the keep branch, `resting_above_level`, never
    the band (the keep branch is not this lane's); an arm older than
    the wait on a book NOT at his level is cleared, so the band waits."""
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 20.0)
    p, b, v = _band_world(ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)]
    assert _census(st, "take_in_band") == 0 and _census(st, "take_placed") == 0 and _census(st, "rest_placed") == 1
    assert b["last_plan"]["take_band"]["verdict"] == "waiting" and b["last_plan"]["decision"] == "rest"
    # a rest 25 s old at his cent, the ask a cent above: kept, held by name
    p2, b2, v2 = _band_world(ioc_fill=300.0)
    o2 = p2.add_order(b2, wire=0.52, qty=300, placed_ts=NOW - 25)
    v2.rest("oid-1", "BUY", 0.52, 300)
    st2 = _tick(p2, v2)
    assert not _cancels(v2) and not _places(v2) and p2.orders[o2["id"]]["state"] == "open"
    assert _census(st2, "resting_above_level") == 1 and _census(st2, "take_refused_price") == 1
    assert _census(st2, "take_in_band") == 0 and "take_band" not in b2["last_plan"]
    # the same rest with the ask AT his cent (a locked book, the wire
    # unchanged): the at-level take off the 25 s rest, as today under E2
    p3, b3, v3 = _band_world(bid=0.52, ask=0.52, ioc_fill=300.0)
    p3.add_order(b3, wire=0.52, qty=300, placed_ts=NOW - 25)
    v3.rest("oid-1", "BUY", 0.52, 300)
    st3 = _tick(p3, v3)
    assert [c[1] for c in _cancels(v3)] == ["oid-1"] and [c[2:6] for c in _places(v3)] == [(0.52, 300, False, IOC_TIF)]
    assert _census(st3, "take_at_his_level") == 1 and _census(st3, "take_in_band") == 0
    # an arm 25 s old and no rest, the book not at his level: the arm is
    # cleared (`take_disarmed`), the band waits, the rest goes
    p4, b4, v4 = _band_world(ioc_fill=300.0)
    b4["take_armed_ts"] = NOW - 25
    st4 = _tick(p4, v4)
    assert [c[2:6] for c in _places(v4)] == [(0.52, 300, False, GTC_TIF)] and b4["take_armed_ts"] is None
    assert _census(st4, "take_in_band") == 0 and b4["last_plan"]["take_band"]["verdict"] == "waiting"
    # the wait's contract: the band's take_allowed is the same call the at-level take makes
    src = inspect.getsource(ml._take_band)
    assert 'rules.take_allowed(0.0, book.get("take_armed_ts"), t.now, r.bid, r.ask, bc, BUY)' in src


def test_e14_the_room_is_read_at_the_band_cent(monkeypatch):
    """The room one cent short at the band cent: `over_room`, nothing
    placed (the take-first's own rule); a room of $100 sizes the band IOC
    at floor(100 / 0.53) = 188, never at his cent's 192. The clip and
    the game cap are read by the same _room_qty as every entry."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 0.525)     # one share at 0.52, none at 0.53
    p, b, v = _band_world(ioc_fill=300.0)
    st = _tick(p, v)
    assert _census(st, "over_room") == 1 and not _places(v) and not p.orders
    assert _census(st, "take_in_band") == 0 and b["last_plan"]["take_band"]["verdict"] == "in_band"
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 100.0)
    p2, b2, v2 = _band_world(ioc_fill=300.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.53, 188, False, IOC_TIF)] and _census(st2, "take_in_band") == 1
    assert b2["last_plan"]["take_qty"] == 188 and b2["ledger_net"] == 188 and _inserts(p2)[0][11] == 188
    # the IOC sized at the band cent expires 0: the rest is TODAY's rest at
    # his cent, floor(100 / 0.52) = 192, not the IOC's 188 (the review's
    # LOW-1: the plan's quantity goes in, _place_reserved's room re-read
    # at his cent sizes it)
    p3, b3, v3 = _band_world(ioc_fill=0.0)
    st3 = _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.53, 188, False, IOC_TIF), (0.52, 192, False, GTC_TIF)]
    assert _census(st3, "take_in_band") == 1 and _census(st3, "rest_placed") == 1 and b3["ledger_net"] == 0
    # and a partial fill's remainder is the plan's unfilled, clipped by the
    # room left at his cent: 300 - 88 filled = 212 wanted, (100 - 88 x 0.53
    # = 53.36) / 0.52 = 102 shares of room
    p4, b4, v4 = _band_world(ioc_fill=88.0)
    _tick(p4, v4)
    assert [c[2:6] for c in _places(v4)] == [(0.53, 188, False, IOC_TIF), (0.52, 102, False, GTC_TIF)]
    assert b4["ledger_net"] == 88
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1250.0)
    src = inspect.getsource(ml._act)
    assert "qty = _room_qty(t, p.qty, band, intent)" in src, "the room at the band cent"
    assert "rules.room_scale(int(qty), wire, rules.MIRROR_CLIP_USD, t.day_room, t.total_room" in inspect.getsource(ml._room_qty)
    assert rules.MIRROR_CLIP_USD == 2500.0


def test_e14_059_absent_takes_nothing_in_band_and_rests(monkeypatch):
    """The E18 fixture that hides the 059 columns: verdict `uncounted`,
    no band IOC (an uncounted band take is not allowed), the rest by the
    050 INSERT, `order_cols_absent` on the heartbeat as today."""
    monkeypatch.setattr(ml, "_order_cols_absent_logged", False)
    p, b, v = _band_world(ioc_fill=300.0)
    p.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    st = _tick(p, v)
    assert st["order_cols_absent"] == "UndefinedColumnError" and st["status"] == "ok"
    assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)]
    assert _census(st, "take_in_band") == 0 and _census(st, "take_placed") == 0 and _census(st, "rest_placed") == 1
    assert b["last_plan"]["take_band"]["verdict"] == "uncounted" and b["last_plan"]["decision"] == "rest"
    ins = _inserts(p)
    assert len(ins) == 1 and len(ins[0]) == 19 and _decisions(p) == []
    assert _bbos(v).count(SLUG) == 1, "no re-read: no IOC was decided"
    # the fast tick agrees
    _walk()
    p2, b2, v2 = _band_world(ioc_fill=300.0)
    p2.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    fs = _fast(p2, v2)
    assert fs["order_cols_absent"] == "UndefinedColumnError" and _skips(fs) == {}
    assert [c[2:6] for c in _places(v2)] == [(0.52, 300, False, GTC_TIF)] and _census(fs, "take_in_band") == 0
    assert b2["last_plan"]["take_band"]["verdict"] == "uncounted"
    # an ask at his cent under the same absence: today's take, as before this lane
    p3, b3, v3 = _band_world(bid=0.51, ask=0.52, ioc_fill=300.0)
    p3.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    st3 = _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.52, 300, False, IOC_TIF)] and _census(st3, "take_at_his_level") == 1
    assert b3["last_plan"]["take_band"]["verdict"] == "at_level"


def test_e14_the_band_off_and_his_cent_unreadable_rest_as_today(monkeypatch):
    """MIRROR_TAKE_BAND at 0 (the operator's one line): verdict `off`,
    today's behaviour byte for byte; his cent unreadable: `unread`."""
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.0)
    p, b, v = _band_world(ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)] and _census(st, "take_in_band") == 0
    assert b["last_plan"]["take_band"] == {"his_cent": 0.52, "band": 0.0, "band_cent": None, "bid": 0.52,
                                           "ask": 0.53, "verdict": "off"}
    assert _inserts(p)[0][20] == "rest"
    # 0.005: under a cent, no band
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.005)
    p2, b2, v2 = _band_world(ioc_fill=300.0)
    _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.52, 300, False, GTC_TIF)]
    assert b2["last_plan"]["take_band"]["band_cent"] is None and b2["last_plan"]["take_band"]["verdict"] == "off"
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.01)
    # the at-level take with the band off: as today
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.0)
    p3, b3, v3 = _band_world(bid=0.51, ask=0.52, ioc_fill=300.0)
    st3 = _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.52, 300, False, IOC_TIF)] and _census(st3, "take_at_his_level") == 1
    assert b3["last_plan"]["take_band"]["verdict"] == "at_level"
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.01)
    # a one-sided or impossible ask (None, 1.0) is refused BEFORE any plan,
    # by name (`no_mark`): nothing placed, no band read -- fails closed
    # upstream of the band, as today
    for ask in (None, 1.0):
        p5, b5, v5 = _band_world(bid=0.52, ask=ask, ioc_fill=300.0)
        st5 = _tick(p5, v5)
        assert _census(st5, "no_mark") == 1 and not _places(v5) and _census(st5, "take_in_band") == 0, ask
        assert "take_band" not in b5["last_plan"] and b5["last_reason"] == "no_mark", ask
    # the read itself, every verdict, at the unit (the tick's quote handed
    # in as the worker hands it): the ask unreadable or off the ladder is
    # `unread` and never a cent
    t = types.SimpleNamespace(order_cols=True, now=NOW)
    book, plan = {"take_armed_ts": None}, {}

    def _read(bid, ask, his=0.52, cols=True):
        t.order_cols = cols
        plan.clear()
        got = ml._take_band(t, book, types.SimpleNamespace(bid=bid, ask=ask), his, plan)
        return got, plan["take_band"]["verdict"], plan["take_band"]
    for ask in (None, 0.0, 1.0, 1.5, float("nan"), True):
        assert _read(0.52, ask)[:2] == (None, "unread"), ask
    assert _read(0.52, 0.53, his=None)[:2] == (None, "unread")
    assert _read(0.52, 0.53, his=True)[:2] == (None, "unread")
    assert _read(0.51, 0.52)[:2] == (None, "at_level") and _read(0.50, 0.51)[:2] == (None, "at_level")
    assert _read(0.53, 0.54)[:2] == (None, "out")
    assert _read(0.52, 0.53, cols=False)[:2] == (None, "uncounted") and _read(0.52, 0.53, cols=None)[:2] == (None, "uncounted")
    got, verdict, tb = _read(0.52, 0.53)
    assert (got, verdict) == (0.53, "in_band")
    assert tb == {"his_cent": 0.52, "band": 0.01, "band_cent": 0.53, "bid": 0.52, "ask": 0.53, "verdict": "in_band"}
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.0)
    assert _read(0.52, 0.53)[:2] == (None, "off") and _read(0.51, 0.52)[:2] == (None, "at_level")
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.01)
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 20.0)
    assert _read(0.52, 0.53)[:2] == (None, "waiting")
    book["take_armed_ts"] = NOW - 25
    assert _read(0.52, 0.53)[:2] == (0.53, "in_band"), "an arm older than the wait has waited (take_allowed)"
    book["take_armed_ts"] = NOW - 5
    assert _read(0.52, 0.53)[:2] == (None, "waiting")
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 0.0)
    # the reduce leg never reads the band (a SELL plan: no `take_band` on the plan)
    p6 = _pool(fills=[_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "SELL", 200, 0.31, NOW - 1000)],
               snap={M: 100.0, N: 0.0})
    b6 = p6.add_book(ledger=300, avg_cost=0.31)
    v6 = _Venue(bid=0.29, ask=0.32, held={SLUG: 300})
    st6 = _tick(p6, v6)
    assert [c[2:6] for c in _places(v6)] == [(0.31, 200, True, GTC_TIF)] and "take_band" not in b6["last_plan"]
    assert _census(st6, "take_in_band") == 0 and _inserts(p6)[0][20] == "exit_rest"


# ------------------------------------------------------ the names, the docs

def test_e14_the_census_name_sits_before_drift_smaller_open_and_the_pins_hold():
    keys = ml.CENSUS_KEYS
    assert keys.count("take_in_band") == 1
    # FILL lane 3 (2026-09-08) placed its three names between this one and E19's key;
    # T2 (FILL lane 4) its two after those; FILL lane 5 its three after those
    assert keys[keys.index("take_in_band") + 1] == "exit_take_in_band"
    assert keys[keys.index("take_in_band") + 4] == "fill_answer_write_failed"
    assert keys[keys.index("take_in_band") + 6] == "he_holds"
    # E22 (FILL lane 22) placed its four names after FILL lane 5's (+9 -> +13)
    assert keys[keys.index("take_in_band") + 13] == "drift_smaller_open"
    # landed after E20 (`wrong_sign_hold`) and E14b (`exit_take_rested`), which sit before it by the same convention
    assert keys[keys.index("take_in_band") - 1] == "exit_take_rested"
    assert keys[keys.index("take_in_band") - 2] == "wrong_sign_hold"
    assert keys[keys.index("take_in_band") - 3] == "adopt_prior_venue_settled"
    # FILL lane 3 (three names), T2 (two), FILL lane 5 (three) and E22 (FILL lane 22, four) placed theirs after this one (-14 -> -26)
    assert keys[-26] == "take_in_band" and keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    assert ml._new_stats()["census"]["take_in_band"] == 0
    # the one emit site, at the decision, beside the at-level take's
    src = inspect.getsource(ml)
    assert src.count('_mirror_stop("take_in_band", w)') == 1 and '_mirror_stop("take_in_band", w)' in inspect.getsource(ml._act)
    # the worker reads the band through the rules module alone: no env knob of its own
    assert 'capped_env("MIRROR_TAKE_BAND' not in src and '_env_float("MIRROR_TAKE_BAND' not in src
    assert "rules.MIRROR_TAKE_BAND" in inspect.getsource(ml._take_band) and "rules.band_cent(his_px)" in inspect.getsource(ml._take_band)
    # the band arm sits AFTER the at-level take and BEFORE the rest, on the no-order path
    asrc = inspect.getsource(ml._act)
    i_lvl = asrc.index('_mirror_stop("take_at_his_level", w)\n                return await _entry_take(')
    i_band = asrc.index('_mirror_stop("take_in_band", w)')
    i_rest = asrc.index('return await _place(t, book, r, "increase", p.side, wire, qty, his_px, p, plan)')
    assert i_lvl < i_band < i_rest
    # the entry's one band site; FILL lane 3 (2026-09-08) added the exit band's four (1 -> 5), each
    # on a reduce leg -- the entry's stays the one _entry_take call
    assert "in_band=True" in asrc and asrc.count("in_band=True") == 5
    assert asrc.count("_entry_take(t, book, r, p, band, wire, qty, his_px, plan, first=True,\n                                     in_band=True)") == 1
    # the at-level take's own test line reads nothing of the band: it fires
    # FIRST and unchanged (the review's M23, pinned by text)
    assert 'if rules.take_allowed(0.0, book.get("take_armed_ts"), t.now, r.bid, r.ask, take_lvl, p.side):\n' in asrc
    # the band's rests hand _place_reserved the PLAN's quantity (the review's LOW-1)
    esrc = inspect.getsource(ml._entry_take)
    assert "rest_qty = int(p.qty) if in_band else qty\n" in esrc and esrc.count("rest_px, rest_qty, his_px") == 2
    assert "float(rest_qty) - float(_num(plan.get(\"take_filled\"))" in esrc
    # the decision word is written by _place_reserved through rules.order_decision alone
    psrc = inspect.getsource(ml._place_reserved)
    assert "in_band=bool(in_band) and is_take" in psrc
    assert 'return "take_in_band"' not in psrc and '"take_in_band" if' not in psrc, "the word is rules.order_decision's alone"
    assert inspect.getsource(rules.order_decision).count('"take_in_band"') == 1
    assert ml.IOC_SKIPPED == ("ask_moved", "bid_moved", "ioc_reread_capped", "ioc_quote_unread")
    # the verdict words, all of them, in _take_band
    tsrc = inspect.getsource(ml._take_band)
    for verdict in ("unread", "at_level", "off", "uncounted", "out", "waiting", "in_band"):
        assert f'tb["verdict"] = "{verdict}"' in tsrc, verdict


def test_e14_every_name_is_emitted_here(monkeypatch):
    """The lane's one name, driven with the band ON (the worker file's
    coverage read imports this and runs it under its own rails)."""
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.01)
    test_e14_book_544s_shape_first_sight_sends_one_ioc_at_the_band_cent_and_rests_the_remainder()


def test_e14_the_docs_name_the_rule_and_the_gate():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E14 \(2026-09-08, FILL lane 2\)", doc, re.M), "the E14 section header"
    for k in NEW_NAMES + ("MIRROR_TAKE_BAND", "band_cent", "take_band", "at_level", "in_band", "uncounted",
                          "unread", "waiting", "ask_moved", "take_in_band", "test_e14_take_band.py",
                          "'rest', 'take', 'cover', 'exit_rest'", "MIRROR_TAKE_BAND=0", "n_resolved >= 50",
                          "ci95", "479", "42.2%", "16.0%", "65.4%", "1,922"):
        assert k in doc, k
    # the migration's comment is not rewritten (a landed migration stands); the docs restate its list
    sql = (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "'take_in_band' is" in sql and "reserved for the entry band" in sql
