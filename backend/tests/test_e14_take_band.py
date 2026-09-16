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

E27 (2026-09-09, FILL lane 27; owner order ~21:1xZ "lets take it
immediately with a tolerance") widened the band's DEFAULT to 0.02
capped at 5% of the cost per share and gave the SHORT add its own read
(tests/test_e27_take_tolerance.py). The fixture below holds
MIRROR_TAKE_BAND at 0.01 by name, under which rules.take_band_width
reads exactly 0.01 at every long price (min(0.01, max(0.01, 5% x his))
= 0.01), so every behavioural pin here is lane 2's byte for byte: the
same cents, the same rows, the same verdicts. Re-pinned where the
DEFAULT or the plan's SHAPE is read: the source line (0.01 -> 0.02),
the env-reload table (junk / inf / blank / 0.02 / 1e400 now land on
0.02, the ceiling), the plan's `take_band` dict (E27 adds `frac`
{his_px, cost, width} and `band` is the width, None when off), and
book 347's short shape (the short add now carries the read, verdict
`at_level` on that shape: the bid 0.31 sits over his sell cent 0.28,
so today's rest at the short wire governs and nothing is band-taken).
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
    # E27 (FILL lane 27) re-pinned: the DEFAULT is 0.02 (the owner's tolerance,
    # 2026-09-09 ~21:1xZ); the rail's shape is lane 2's -- capped_env, floor 0.0
    assert 'MIRROR_TAKE_BAND = capped_env("MIRROR_TAKE_BAND", 0.02, floor=0.0)' in src
    assert src.count('capped_env("MIRROR_TAKE_BAND"') == 1 and 'min_wait_env("MIRROR_TAKE_BAND' not in src
    assert '_env_float("MIRROR_TAKE_BAND' not in src
    for name in ("MIRROR_TAKE_BAND", "band_cent", "take_in_band"):
        assert name in rules.__all__, name
    try:
        # env may only LOWER: 0.03 lands on the default (the ceiling, 0.02 since
        # E27); 0.02, 0.01, 0.005 and 0 are honoured; junk / inf are the
        # default; a NEGATIVE value lands on the FLOOR (capped_env: "an
        # override under the floor lands on the floor, never past it") -- 0.0,
        # the band off. E27 re-pinned: ("0.02", 0.01) -> ("0.02", 0.02) and
        # ("0.03", 0.02); junk / inf / blank / 1e400 0.01 -> 0.02
        for raw, want in (("0.03", 0.02), ("0.02", 0.02), ("0.005", 0.005), ("0", 0.0), ("0.01", 0.01),
                          ("junk", 0.02), ("inf", 0.02), ("", 0.02), ("-1", 0.0), ("1e400", 0.02)):
            monkeypatch.setenv("MIRROR_TAKE_BAND", raw)
            mod = importlib.reload(rules)
            assert mod.MIRROR_TAKE_BAND == want, (raw, mod.MIRROR_TAKE_BAND)
            # band_cent reads the constant at call time (through E27's
            # take_band_width: at 0.01 lane 2's cent, at 0.02 two cents over
            # his 0.52 -- 5% of 0.52 is 2.6c, above the cap)
            if want == 0.01:
                assert mod.band_cent(0.52) == 0.53
            elif want == 0.02:
                assert mod.band_cent(0.52) == 0.54
            elif want == 0.005:
                assert mod.band_cent(0.52) is None and mod.band_cent(0.529) == 0.53
            else:
                assert mod.band_cent(0.52) is None, "the band off: no cent, the rest as today"
    finally:
        monkeypatch.delenv("MIRROR_TAKE_BAND", raising=False)
        importlib.reload(rules)
    assert rules.MIRROR_TAKE_BAND == 0.02 and rules.band_cent(0.52) == 0.54
    # lane 2's band, one line of the environment away (E27's rail table)
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.01)
    assert rules.band_cent(0.52) == 0.53
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

def test_e14_book_544s_shape_first_sight_rests_the_whole_300_at_his_cent_inside_the_spread():
    """Book 544 / 611 (verify_1750 564: BUY 235 / 2,400 / 74 @0.520 at his
    0.520; 611's 16 IOCs expired 0, hourly_1737 1726): his 0.52, bid 0.52
    / ask 0.53.

    RE-PINNED at E31 (FILL lane 31, 2026-09-10; owner order ~03:3xZ
    "become a maker not taker ... mirror him to a tee"). This lane pinned
    ONE IOC at the band cent 0.53 with the row's decision 'take_in_band'.
    E31 retired every take by code: the maker wire is min(buy_wire(0.52),
    0.53 - 0.01) = 0.52 -- HIS OWN CENT, one tick under the ask -- and it
    goes out as a post-only GTC. So this shape now pays 0.52, not 0.53,
    and pays no spread at all. Because that cent IS the touch bound the
    rest re-reads the quote at the send (E31 D), which is where E18's IOC
    re-read stood: the two bbo reads are unchanged.

    The plan's `take_band` dict is gone with the band's reader; the plan's
    own record is `maker` {wire, bound, his_cent, clause, ...}, clause
    'his_cent'. Census: `take_in_band` / `take_first` / `take_placed` 1 ->
    0, `rest_placed` 0 -> 1."""
    # (a) nothing lifts the rest: the whole 300 stands
    p, b, v = _band_world(ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)]
    assert _places(v)[0][7] is True, "post-only on the wire"
    assert _census(st, "take_in_band") == 0 and _census(st, "take_first") == 0 and _census(st, "take_placed") == 0
    assert _census(st, "take_at_his_level") == 0 and _census(st, "rest_placed") == 1 and _census(st, "ask_moved") == 0
    assert b["ledger_net"] == 0 and b["open_order_id"] is not None
    lp = b["last_plan"]
    assert lp["decision"] == "rest" and "take_band" not in lp
    assert lp["maker"] == {"wire": 0.52, "bound": 0.52, "his_cent": 0.52, "clause": "his_cent",
                           "side": rules.BUY, "bid": 0.52, "ask": 0.53, "at": NOW, "hint": None}
    assert "reread" not in lp["maker"], "the re-read recomputed the SAME cent: nothing was re-quoted"
    assert "take_qty" not in lp and "take_filled" not in lp
    ins = _inserts(p)
    # FILL lane 9 (061): the 059 shape plus `fast` (false: a full tick) as the twenty-third argument
    assert len(ins) == 1 and len(ins[0]) == 23 and ins[0][22] is False
    assert (ins[0][3], ins[0][5], ins[0][8], ins[0][10], ins[0][11]) == ("increase", "GTC", 0.52, 0.52, 300)
    assert ins[0][16] == 0.53 and ins[0][19] == 0.53 and ins[0][20] == "rest" and ins[0][21] == str(NOW - 3000)
    # the cents paid over him, readable off the row: wire - floor(his_level * 100) / 100 = 0.00
    assert round(ins[0][10] - (int(ins[0][8] * 100) / 100.0), 2) == 0.00, "never a cent over him"
    assert _bbos(v).count(SLUG) == 2, "the tick's read and the touch-bound rest's re-read before the send"
    # (b) a TAKER lifts 100 of the fresh rest at create (order 153's shape, with the
    # venue's `aggressor` reading False: a maker fill): the ledger 100, the SAME one
    # row, no second send -- where the IOC's unfilled remainder used to rest beside it
    p2, b2, v2 = _band_world(ioc_fill=100.0, lift=100.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.52, 300, False, GTC_TIF)]
    assert b2["ledger_net"] == 100 and b2["open_order_id"] is not None
    assert _census(st2, "take_in_band") == 0 and _census(st2, "rest_placed") == 1
    assert _census(st2, "maker_fill_at_create") == 1 and _census(st2, "post_only_block") == 0
    ins2 = _inserts(p2)
    assert [(a[5], a[10], a[11], a[19], a[20]) for a in ins2] == [("GTC", 0.52, 300, 0.53, "rest")]
    assert b2["last_plan"]["decision"] == "rest"
    # (c) 611's shape (the IOCs that expired 0) has no equivalent: a rest that
    # nothing lifts simply STANDS -- one row, one op, one open order
    p3, b3, v3 = _band_world(ioc_fill=0.0)
    st3 = _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.52, 300, False, GTC_TIF)]
    assert b3["ledger_net"] == 0 and _census(st3, "take_in_band") == 0 and _census(st3, "rest_placed") == 1
    assert _inserts(p3)[0][20] == "rest" and st3["ops"] == 1
    assert [o["tif"] for o in p3.orders.values() if o["state"] == "open"] == ["GTC"]


def test_e14_the_touch_bound_rest_re_reads_and_never_crosses_when_the_ask_moves(monkeypatch):
    """RE-PINNED at E31. E14 pinned here that the band IOC was WITHHELD
    (`ask_moved`) when the re-read's ask left the band. There is no IOC:
    the rest at the touch bound is RE-PRICED on the fresher quote and
    goes out, and it can never end up at or through the ask.

      - the ask RISES 0.53 -> 0.54: the bound rises to 0.53, min(his 0.52,
        0.53) is still his own 0.52 -- nothing to re-quote, the rest goes
        at 0.52 (where E14 sent nothing and rested next);
      - the ask FALLS to 0.52 (through his cent): the bound falls to 0.51
        and the rest goes at 0.51, a tick under it -- where E14 sent an
        IOC AT 0.53 and paid the spread;
      - the re-read unreadable: `rest_quote_unread` (E18's
        `ioc_quote_unread` at the same site), the rest at the tick's own
        cent 0.52;
      - the budget spent: `rest_reread_capped`, the same rest.
    """
    p = _his_at(0.52)
    b = p.add_book(ledger=0)
    v = _MovingVenue([(0.52, 0.53), (0.52, 0.54)], bid=0.52, ask=0.53, ioc_fill=300.0)
    st = _tick(p, v)
    assert _bbos(v).count(SLUG) == 2
    assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)], "no IOC; the rest at his cent"
    assert _census(st, "take_in_band") == 0, "the band's name is a declared zero from E31"
    assert _census(st, "ask_moved") == 0 and _census(st, "take_placed") == 0 and _census(st, "rest_placed") == 1
    lp = b["last_plan"]
    assert "ask_moved" not in lp and "take_band" not in lp
    assert lp["rest_quote_at_send"] == {"bid": 0.52, "ask": 0.54, "bid_at_plan": 0.52, "ask_at_plan": 0.53}
    assert lp["maker"]["clause"] == "his_cent" and lp["decision"] == "rest"
    assert b["ledger_net"] == 0 and st["ops"] == 1
    ins = _inserts(p)
    assert len(ins) == 1 and ins[0][5] == "GTC" and ins[0][19] == 0.54 and ins[0][20] == "rest"
    # the ask through his cent on the re-read: the rest follows the bound DOWN, never crosses
    p1 = _his_at(0.52)
    b1 = p1.add_book(ledger=0)
    v1 = _MovingVenue([(0.52, 0.53), (0.51, 0.52)], bid=0.52, ask=0.53, ioc_fill=300.0)
    _tick(p1, v1)
    assert [c[2:6] for c in _places(v1)] == [(0.51, 300, False, GTC_TIF)] and _inserts(p1)[0][19] == 0.52
    assert b1["last_plan"]["decision"] == "rest" and b1["last_plan"]["maker"]["clause"] == "touch"
    # the unread re-read
    p2 = _his_at(0.52)
    b2 = p2.add_book(ledger=0)
    v2 = _MovingVenue([(0.52, 0.53), (None, None)], bid=0.52, ask=0.53, ioc_fill=300.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.52, 300, False, GTC_TIF)] and _census(st2, "rest_quote_unread") == 1
    assert _census(st2, "take_in_band") == 0 and _census(st2, "take_placed") == 0 and b2["ledger_net"] == 0
    assert _census(st2, "ioc_quote_unread") == 0, "the retired name stays a declared zero"
    # the call budget spent: no re-read, the tick's own cent
    monkeypatch.setattr(rules, "MIRROR_VENUE_CALLS_PER_TICK", 0)
    p3 = _his_at(0.52)
    b3 = p3.add_book(ledger=0)
    v3 = _MovingVenue([(0.52, 0.53), (0.52, 0.53)], bid=0.52, ask=0.53, ioc_fill=300.0)
    st3 = _tick(p3, v3)
    assert _bbos(v3).count(SLUG) == 1 and [c[2:6] for c in _places(v3)] == [(0.52, 300, False, GTC_TIF)]
    assert _census(st3, "rest_reread_capped") == 1 and _census(st3, "take_placed") == 0 and b3["ledger_net"] == 0
    assert b3["last_plan"]["rest_reread_capped"] == {"guard_calls": 0, "budget": 0}


def test_e14_an_ask_at_his_cent_rests_one_tick_under_it_and_never_crosses():
    """RE-PINNED at E31. The ask arriving AT his cent 0.52 was E4's
    at-level take: ONE IOC at 0.52, decision 'take'. E31 makes the same
    arrival a rest one tick under the ask -- min(buy_wire(0.52), 0.52 -
    0.01) = 0.51, clause 'touch' -- which is a cent BETTER than his own
    level and never crosses. The ask THROUGH his cent (0.51) rests at
    0.50 by the same clamp. Both are `maker_rest_at_touch`, the class the
    maker-rests preset reads."""
    p, b, v = _band_world(bid=0.51, ask=0.52, ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.51, 300, False, GTC_TIF)]
    assert _census(st, "take_at_his_level") == 0 and _census(st, "take_in_band") == 0
    assert _census(st, "maker_rest_at_touch") == 1 and _census(st, "rest_placed") == 1
    assert _inserts(p)[0][20] == "rest" and b["last_plan"]["decision"] == "rest"
    assert "take_band" not in b["last_plan"]
    assert b["last_plan"]["maker"]["clause"] == "touch" and b["last_plan"]["maker"]["bound"] == 0.51
    # through his cent: the same clamp, a cent lower
    p2, b2, v2 = _band_world(bid=0.50, ask=0.51, ioc_fill=300.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.50, 300, False, GTC_TIF)] and _census(st2, "take_in_band") == 0
    assert b2["last_plan"]["maker"]["clause"] == "touch" and _census(st2, "maker_rest_at_touch") == 1


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
    # E31: the band's dict is gone with its reader; the plan's record is the maker's
    assert "take_band" not in b["last_plan"]
    assert b["last_plan"]["maker"] == {"wire": 0.61, "bound": 0.81, "his_cent": 0.61, "clause": "his_cent",
                                       "side": rules.BUY, "bid": 0.81, "ask": 0.82, "at": NOW, "hint": None}
    assert _inserts(p)[0][20] == "rest" and b["last_plan"]["decision"] == "rest"
    # two cents above: out as well (the band is one cent, never two)
    p2 = _his_at(0.52)
    b2 = p2.add_book(ledger=0)
    v2 = _Venue(bid=0.53, ask=0.54, ioc_fill=300.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.52, 300, False, GTC_TIF)] and _census(st2, "take_in_band") == 0
    assert "take_band" not in b2["last_plan"] and b2["last_plan"]["maker"]["clause"] == "his_cent"
    # the source pins: the admission and the open's catch-up allowance untouched
    assert _sha(rules.admission) == ADMISSION_SHA and _sha(rules.open_catchup) == OPEN_CATCHUP_SHA
    src = inspect.getsource(rules)
    for line in CATCHUP_LINES:
        assert src.count(line) == 1, line
    # E31: `_ioc_reread` is gone with the IOC; `_rest_reread` stands at its site and, like it,
    # reads nothing of the band -- and so does the maker wire itself
    for fn in (rules.admission, rules.open_catchup, ml._tick_candidate, ml._rest_reread, rules.exit_terms,
               rules.rest_decision, rules.take_allowed, rules.at_or_through, rules.buy_price,
               rules.maker_wire, rules.maker_bound, rules.maker_compare_wire, ml._wire_for):
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
    at _short_wire (0.32), decision 'rest', no `take_in_band`. E27 (FILL
    lane 27) re-pinned it once (the short add carrying the band's read),
    and its review again (at level the short took AT HIS CENT first: ONE
    SELL IOC at 0.28 beside the rest).

    RE-PINNED at E31 (FILL lane 31, 2026-09-10): both takes are gone. The
    short add is an OFFER of the contract, so the maker clamp reads the
    SELL side -- max(sell_wire(his 0.28), bid 0.31 + 0.01) = 0.32, which
    is the short wire this test has pinned since lane 2 and the rest row
    is byte for byte the one it always pinned. What goes away is the IOC
    beside it: ONE order, not two, `take_placed` / `take_at_his_level` 1
    -> 0, `short_open` 2 -> 1, and no `take_band` on the plan."""
    _shorts_on(monkeypatch)
    p = _short_world()
    v = _Venue(bid=0.31, ask=0.32)
    st = _tick(p, v, http=_short_http())
    b = next(iter(p.books.values()))
    assert b["intent"] == "ORDER_INTENT_BUY_SHORT" and b["target"] == -300
    assert [c[1:6] for c in _places(v)] == [(SLUG, 0.32, 300, False, GTC_TIF)]
    assert _census(st, "take_in_band") == 0 and _census(st, "take_placed") == 0 and _census(st, "rest_placed") == 1
    assert _census(st, "take_at_his_level") == 0 and _census(st, "short_open") == 1, "one order, one door"
    assert "take_band" not in b["last_plan"] and b["last_plan"]["decision"] == "rest"
    assert [(a[5], a[10], a[18], a[20]) for a in _inserts(p)] == [("GTC", 0.32, "ORDER_INTENT_BUY_SHORT", "rest")]
    # an ADD onto a held short: the same
    p2 = _short_world()
    b2 = _short_book(p2, ledger=-100)
    v2 = _Venue(bid=0.31, ask=0.32, held={SLUG: -100})
    st2 = _tick(p2, v2, http=_short_http())
    assert [c[1:6] for c in _places(v2)] == [(SLUG, 0.32, 200, False, GTC_TIF)]
    assert _census(st2, "take_in_band") == 0 and _census(st2, "short_add") == 1 and _census(st2, "take_at_his_level") == 0
    assert "take_band" not in b2["last_plan"]
    # the S4 cover's decision word stands (FILL lane 3 re-pinned: in band it is the cover's own band word)
    assert rules.order_decision("reduce", True, True, True) == "cover_in_band"
    assert rules.order_decision("reduce", True, True, False) == "cover"


def test_e14_book_661s_shape_a_standing_rest_is_never_converted_and_the_requote_band_takes():
    """Book 661 (closerows_1750 529, order 3632: a rest partially filled
    then replaced -- a SHORT book's `flatten_paired` cover rest, 609
    @0.620 filled 89.24 then cancelled `replace`; the long-entry rest
    here is the plan's stand-in for that shape): a rest standing at
    0.62 with his 0.62 and the ask at
    0.63 -> `keep`, `resting_above_level` as today, NO take of any kind
    and no `take_band` on the plan.

    RE-PINNED at E31 (FILL lane 31, 2026-09-10): `take_refused_price` was
    the take arm's own refusal and is retired with it (a declared zero);
    `resting_above_level` stays -- under a maker it is the NORMAL state of
    a rest waiting at his cent, and the record still names it. The second
    half pinned that the re-quote after a `replace_cent` cancel BAND-TOOK
    at 0.63; now it re-quotes to his new cent 0.62 as a post-only GTC, a
    cent better and never through the ask."""
    p = _his_at(0.62)
    b = p.add_book(ledger=0)
    o = p.add_order(b, wire=0.62, qty=300, placed_ts=NOW - 100)
    v = _Venue(bid=0.62, ask=0.63, ioc_fill=300.0)
    v.rest("oid-1", "BUY", 0.62, 300)
    st = _tick(p, v)
    assert not _cancels(v) and not _places(v) and p.orders[o["id"]]["state"] == "open"
    assert _census(st, "open_order_pending") == 1 and _census(st, "resting_above_level") == 1
    assert _census(st, "take_refused_price") == 0 and _census(st, "take_in_band") == 0
    assert "take_band" not in b["last_plan"] and b["last_plan"]["open_order"] == o["id"]
    assert _bbos(v).count(SLUG) == 1
    # the re-quote: the rest stands at his OLD cent 0.60, 100 s old; his
    # level now reads 0.62 -> the wire moves two cents -> `replace_cent` ->
    # the re-plan on the no-order path -> E31's maker wire min(0.62, 0.63
    # - 0.01) = 0.62, his own cent, a post-only GTC (E14 sent an IOC at 0.63)
    p2 = _his_at(0.62)
    b2 = p2.add_book(ledger=0)
    o2 = p2.add_order(b2, wire=0.60, qty=300, placed_ts=NOW - 100)
    v2 = _Venue(bid=0.62, ask=0.63, ioc_fill=300.0)
    v2.rest("oid-1", "BUY", 0.60, 300)
    st2 = _tick(p2, v2)
    assert [c[1] for c in _cancels(v2)] == ["oid-1"] and p2.orders[o2["id"]]["reason"] == "replace"
    assert _decisions(p2) == [(o2["id"], "replace_cent")] and b2["last_plan"]["replaced"] == "replace_cent"
    assert [c[2:6] for c in _places(v2)] == [(0.62, 300, False, GTC_TIF)]
    assert _census(st2, "take_in_band") == 0 and _census(st2, "take_at_his_level") == 0 and st2["requotes"] == 1
    assert "take_band" not in b2["last_plan"] and b2["last_plan"]["decision"] == "rest"
    assert b2["last_plan"]["replaced_by"] == "his_level", "E31: HIS cent moved the wire, not the touch"
    assert b2["ledger_net"] == 0 and _inserts(p2)[0][20] == "rest" and _inserts(p2)[0][8] == 0.62
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
    assert _census(st3, "take_in_band") == 0 and _census(st3, "take_placed") == 0


def test_e14_the_requote_rest_goes_out_on_the_credit_at_the_ops_budgets_last_op(monkeypatch):
    """Book 661's re-quote shape at the ops budget's last op (E2 review
    round 3, LOW-6): the rest at his OLD cent 0.60 is cancelled
    `replace_cent` (the tick's one op, the credit granted) and the
    plannable quantity RESTS at his cent 0.62 on the credit -- the
    cohort's book is not left bare for the tick with its rest cancelled.

    RE-PINNED at E31 (FILL lane 31, 2026-09-10): E14 pinned a band IOC
    refused `ops_capped` between the cancel and that rest. There is no
    IOC, so nothing is refused: `ops_capped` 1 -> 0 and `take_in_band`
    1 -> 0. The credit itself, the one op, the row and the cent are
    exactly what this test always pinned."""
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 1)
    p = _his_at(0.62)
    b = p.add_book(ledger=0)
    o = p.add_order(b, wire=0.60, qty=300, placed_ts=NOW - 100)
    v = _Venue(bid=0.62, ask=0.63, ioc_fill=300.0)
    v.rest("oid-1", "BUY", 0.60, 300)
    st = _tick(p, v)
    assert [c[1] for c in _cancels(v)] == ["oid-1"] and p.orders[o["id"]]["reason"] == "replace"
    assert _decisions(p) == [(o["id"], "replace_cent")]
    assert [c[2:6] for c in _places(v)] == [(0.62, 300, False, GTC_TIF)], "the rest went, on the credit"
    assert st["ops"] == 1 and _census(st, "ops_capped") == 0 and _census(st, "rest_placed") == 1
    assert _census(st, "take_in_band") == 0 and _census(st, "take_placed") == 0 and _census(st, "take_first") == 0
    assert b["ledger_net"] == 0 and len([x for x in p.orders.values() if x["state"] == "open"]) == 1
    assert "take_band" not in b["last_plan"] and b["last_plan"]["decision"] == "rest"
    assert _inserts(p)[0][5] == "GTC" and _inserts(p)[0][10] == 0.62 and _inserts(p)[0][20] == "rest"
    # the mechanism, by name: the credit is claimed in _place_reserved's slot, once, by the book
    assert not hasattr(ml, "_entry_take"), "E31: the take's own wrapper is gone"
    src = inspect.getsource(ml._place)
    assert 'if book["id"] in t.requote_credit' in src and "_OpSlot(t, credited=True)" in src


def test_e14_the_fast_tick_rests_at_his_cent_on_the_wake_and_skips_an_open_order():
    """RE-PINNED at E31: the fast tick's wake sends the same maker rest
    the full tick does (0.52, a post-only GTC), where E14 sent the band
    IOC at 0.53. The wake, the skip and the gate are unchanged."""
    p, b, v = _band_world(ioc_fill=300.0)
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {} and [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)]
    assert _census(fs, "fast_tick_placed") == 1 and _census(fs, "take_in_band") == 0
    assert b["ledger_net"] == 0 and b["last_plan"]["decision"] == "rest"
    assert "take_band" not in b["last_plan"] and _inserts(p)[0][20] == "rest"
    # a rest standing: `order_open`, nothing read, nothing placed
    p2, b2, v2 = _band_world(ioc_fill=300.0)
    p2.add_order(b2, wire=0.52, qty=300, placed_ts=NOW - 20)
    v2.rest("oid-1", "BUY", 0.52, 300)
    _walk()
    fs2 = _fast(p2, v2)
    assert _skips(fs2) == {CID: "order_open"} and _bbos(v2) == [] and not _places(v2)
    assert _census(fs2, "take_in_band") == 0 and "cancel" not in [c[0] for c in v2.calls]
    # _fast_gate is untouched by the lane -- and by E31
    assert "take_band" not in inspect.getsource(ml._fast_gate) and "band_cent" not in inspect.getsource(ml._fast_gate)
    assert "maker" not in inspect.getsource(ml._fast_gate)


def test_e14_a_lengthened_wait_is_documentary_and_the_rest_goes_out_at_his_cent(monkeypatch):
    """RE-PINNED at E31 (FILL lane 31, 2026-09-10). E14 pinned that the
    band read the WAIT through rules.take_allowed exactly as the at-level
    take did, so MIRROR_TAKE_AFTER_S=20 made the band `waiting` and rested
    first. No path of the worker reads take_allowed or `take_armed_ts`
    any more (docs 75: the rails that governed a take are documentary),
    so a lengthened wait changes NOTHING -- the maker rest goes out at his
    cent 0.52 on the first tick, whatever the wait says, and there is no
    `take_band` verdict to read.

    That is the direction the rails allow: E31 can only make the mirror
    LESS aggressive, and a rest at his own cent that never crosses is not
    something a wait needs to hold back."""
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 20.0)
    p, b, v = _band_world(ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)]
    assert _census(st, "take_in_band") == 0 and _census(st, "take_placed") == 0 and _census(st, "rest_placed") == 1
    assert "take_band" not in b["last_plan"] and b["last_plan"]["decision"] == "rest"
    # a rest 25 s old at his cent, the ask a cent above: kept, the market never came to him
    p2, b2, v2 = _band_world(ioc_fill=300.0)
    o2 = p2.add_order(b2, wire=0.52, qty=300, placed_ts=NOW - 25)
    v2.rest("oid-1", "BUY", 0.52, 300)
    st2 = _tick(p2, v2)
    assert not _cancels(v2) and not _places(v2) and p2.orders[o2["id"]]["state"] == "open"
    assert _census(st2, "resting_above_level") == 1 and _census(st2, "take_refused_price") == 0
    assert _census(st2, "take_in_band") == 0 and "take_band" not in b2["last_plan"]
    # the same rest with the ask AT his cent (a locked book): E14 cancelled it and
    # took at 0.52. E31 KEEPS it -- rules.maker_compare_wire refuses to re-quote a
    # rest DOWN with a falling ask (the ask coming to the rest is the rest being
    # filled), and nothing crosses to meet it
    p3, b3, v3 = _band_world(bid=0.52, ask=0.52, ioc_fill=300.0)
    o3 = p3.add_order(b3, wire=0.52, qty=300, placed_ts=NOW - 25)
    v3.rest("oid-1", "BUY", 0.52, 300)
    st3 = _tick(p3, v3)
    assert not _cancels(v3) and not _places(v3) and p3.orders[o3["id"]]["state"] == "open"
    assert _census(st3, "take_at_his_level") == 0 and _census(st3, "take_in_band") == 0
    # an arm 25 s old and no rest: the arm is still cleared (`take_disarmed`, E31
    # leaves the disarm where it was so no stale arm survives in the rows), and the
    # rest goes at his cent as on every other tick
    p4, b4, v4 = _band_world(ioc_fill=300.0)
    b4["take_armed_ts"] = NOW - 25
    st4 = _tick(p4, v4)
    assert [c[2:6] for c in _places(v4)] == [(0.52, 300, False, GTC_TIF)] and b4["take_armed_ts"] is None
    assert _census(st4, "take_in_band") == 0 and "take_band" not in b4["last_plan"]
    # the wait's contract: NO path of the worker reads it any more
    from tests.test_e31_maker_only import _code
    assert not hasattr(ml, "_take_band") and not hasattr(ml, "_entry_take")
    assert "rules.take_allowed" not in _code(ml), "the wait is documentary from E31"


def test_e14_the_room_is_read_at_the_maker_cent(monkeypatch):
    """RE-PINNED at E31 (FILL lane 31, 2026-09-10). E14 read the room at
    the BAND cent 0.53 -- the cent the IOC would pay -- so a room of $100
    sized 188 shares, not his cent's 192. The maker rest goes out at his
    own cent 0.52, so the room is read there and the size is 192: the
    same _room_qty, the same clip, the same game cap, one cent lower and
    four shares more for the same dollars. The `over_room` refusal
    follows the cent down with it: at $0.525 of day room ONE share fits
    at 0.52 (E14's world had none at 0.53), so the world that pins the
    refusal is the one that leaves no share at 0.52 either."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 0.51)      # no share at 0.52
    p, b, v = _band_world(ioc_fill=300.0)
    st = _tick(p, v)
    assert _census(st, "over_room") == 1 and not _places(v) and not p.orders
    assert _census(st, "take_in_band") == 0 and "take_band" not in b["last_plan"]
    # E14's own figure ($0.525: no share at the band cent 0.53, one at 0.52) is no
    # longer `over_room` -- the share fits at the cent that goes out -- and is
    # refused a cent lower down, by the SMALLEST-ORDER rule instead (U12c FIX-2:
    # under rules.MIRROR_MIN_ORDER_USD of notional). Still nothing sent, by a
    # different and more accurate name
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 0.525)
    p1, b1, v1 = _band_world(ioc_fill=300.0)
    st1 = _tick(p1, v1)
    assert not _places(v1) and _census(st1, "over_room") == 0
    assert b1["last_reason"] is not None and b1["last_reason"] != "over_room", b1["last_reason"]
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 100.0)
    p2, b2, v2 = _band_world(ioc_fill=300.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.52, 192, False, GTC_TIF)] and _census(st2, "take_in_band") == 0
    assert b2["ledger_net"] == 0 and _inserts(p2)[0][11] == 192 and _census(st2, "rest_placed") == 1
    # a TAKER lifting 88 of that rest at create books 88 and leaves the rest standing:
    # ONE row, no second sizing (E14 sized the IOC at 0.53 and re-sized the remainder)
    p4, b4, v4 = _band_world(ioc_fill=88.0, lift=88.0)
    _tick(p4, v4)
    assert [c[2:6] for c in _places(v4)] == [(0.52, 192, False, GTC_TIF)]
    assert b4["ledger_net"] == 88 and len(_inserts(p4)) == 1
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1250.0)
    src = inspect.getsource(ml._act)
    assert "qty = _room_qty(t, p.qty, wire, intent)" in src, "the room at the cent that goes out"
    assert "rules.room_scale(int(qty), wire, rules.MIRROR_CLIP_USD, t.day_room, t.total_room" in inspect.getsource(ml._room_qty)
    assert rules.MIRROR_CLIP_USD == 2500.0


def test_e14_059_absent_rests_and_writes_the_050_insert(monkeypatch):
    """The E18 fixture that hides the 059 columns. E14 pinned verdict
    `uncounted` here -- an uncounted band take was not allowed -- and the
    rest by the 050 INSERT.

    RE-PINNED at E31 (FILL lane 31, 2026-09-10): there is no take to
    withhold, so the columns' absence changes nothing about WHAT is sent;
    the rest at his cent 0.52 goes out either way and the 050 INSERT
    writes it, `order_cols_absent` on the heartbeat as today. The
    touch-bound re-read is not gated on the columns either, so the two
    bbo reads stand (E14 read one: no IOC had been decided)."""
    monkeypatch.setattr(ml, "_order_cols_absent_logged", False)
    p, b, v = _band_world(ioc_fill=300.0)
    p.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    st = _tick(p, v)
    assert st["order_cols_absent"] == "UndefinedColumnError" and st["status"] == "ok"
    assert [c[2:6] for c in _places(v)] == [(0.52, 300, False, GTC_TIF)]
    assert _census(st, "take_in_band") == 0 and _census(st, "take_placed") == 0 and _census(st, "rest_placed") == 1
    assert "take_band" not in b["last_plan"] and b["last_plan"]["decision"] == "rest"
    ins = _inserts(p)
    assert len(ins) == 1 and len(ins[0]) == 19 and _decisions(p) == []
    assert _bbos(v).count(SLUG) == 2, "the touch-bound re-read is not gated on the columns"
    # the fast tick agrees
    _walk()
    p2, b2, v2 = _band_world(ioc_fill=300.0)
    p2.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    fs = _fast(p2, v2)
    assert fs["order_cols_absent"] == "UndefinedColumnError" and _skips(fs) == {}
    assert [c[2:6] for c in _places(v2)] == [(0.52, 300, False, GTC_TIF)] and _census(fs, "take_in_band") == 0
    assert "take_band" not in b2["last_plan"]
    # an ask at his cent under the same absence: the rest a tick under it, never a take
    p3, b3, v3 = _band_world(bid=0.51, ask=0.52, ioc_fill=300.0)
    p3.raise_on.append(("ml-order-cols-guard", _UndefinedColumn('column "ask_at_send" does not exist')))
    st3 = _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.51, 300, False, GTC_TIF)] and _census(st3, "take_at_his_level") == 0
    assert _census(st3, "maker_rest_at_touch") == 1


def test_e14_the_band_constant_is_documentary_and_the_pure_helpers_stand(monkeypatch):
    """RE-PINNED at E31 (FILL lane 31, 2026-09-10). E14 pinned here that
    MIRROR_TAKE_BAND at 0 was "today's behaviour byte for byte" and read
    every verdict out of the worker's `_take_band`. That reader is gone
    with the take, so what is pinned instead is the two halves of what
    survives:

      (1) THE CONSTANT CANNOT MOVE AN ORDER. At 0, at 0.005, at its own
          default and at E27's 0.02 the mirror sends the SAME post-only
          rest at his cent 0.52 -- there is no reader of the width left on
          the money path, so no value of it can send a cent over him.
      (2) THE PURE HELPERS STAND, tested, as the record of the rule they
          governed (docs 75): rules.take_band_width, rules.band_cent,
          rules.take_in_band and rules.take_allowed are unmoved, and this
          test reads every one of them directly where E14 read them
          through the worker.

    The upstream fail-closed refusals are unchanged: an ask that is None
    or 1.0 is `no_mark` before any plan, and a reduce leg never reads a
    band."""
    from tests.test_e31_maker_only import _code
    src = _code(ml)                 # the code alone: a retired rule may still be NAMED in a paragraph
    assert not hasattr(ml, "_take_band") and not hasattr(ml, "_short_take_band")
    assert "rules.take_band_width" not in src and "rules.band_cent" not in src
    assert "rules.take_in_band" not in src and "rules.short_take_in_band" not in src
    for band in (0.0, 0.005, 0.01, 0.02, 0.25):
        monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", band)
        pb, bb, vb = _band_world(ioc_fill=300.0)
        stb = _tick(pb, vb)
        assert [c[2:6] for c in _places(vb)] == [(0.52, 300, False, GTC_TIF)], band
        assert _census(stb, "take_in_band") == 0 and "take_band" not in bb["last_plan"], band
        assert _inserts(pb)[0][20] == "rest", band
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.01)
    # the ask AT his cent, at every width: the rest a tick under it, never a take
    for band in (0.0, 0.01, 0.02):
        monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", band)
        p3, b3, v3 = _band_world(bid=0.51, ask=0.52, ioc_fill=300.0)
        st3 = _tick(p3, v3)
        assert [c[2:6] for c in _places(v3)] == [(0.51, 300, False, GTC_TIF)], band
        assert _census(st3, "take_at_his_level") == 0 and _census(st3, "take_placed") == 0, band
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.01)
    # a one-sided or impossible ask (None, 1.0) is refused BEFORE any plan,
    # by name (`no_mark`): nothing placed -- fails closed upstream, as today
    for ask in (None, 1.0):
        p5, b5, v5 = _band_world(bid=0.52, ask=ask, ioc_fill=300.0)
        st5 = _tick(p5, v5)
        assert _census(st5, "no_mark") == 1 and not _places(v5) and _census(st5, "take_in_band") == 0, ask
        assert "take_band" not in b5["last_plan"] and b5["last_reason"] == "no_mark", ask
    # (2) the pure helpers, read where E14 read the worker's `_take_band`
    assert rules.take_band_width(0.52) == 0.01 and rules.band_cent(0.52) == 0.53
    assert rules.take_in_band(0.52, 0.53, 0.52, 0.53) is True, "the ask at the band cent is in band"
    assert rules.take_in_band(0.53, 0.54, 0.52, 0.53) is False, "a cent past it is not"
    for ask in (None, 0.0, 1.0, 1.5, float("nan"), True):
        assert rules.take_in_band(0.52, ask, 0.52, 0.53) is False, ask
    assert rules.band_cent(None) is None and rules.band_cent(True) is None
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.0)
    assert rules.take_band_width(0.52) is None and rules.band_cent(0.52, None) is None
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.01)
    # the wait, at the unit: unmoved, and read by nothing on the money path
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 20.0)
    assert rules.take_allowed(0.0, None, NOW, 0.52, 0.53, 0.53, rules.BUY) is False
    assert rules.take_allowed(0.0, NOW - 25, NOW, 0.52, 0.53, 0.53, rules.BUY) is True
    assert rules.take_allowed(0.0, NOW - 5, NOW, 0.52, 0.53, 0.53, rules.BUY) is False
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 0.0)
    # the reduce leg never reads a band (a SELL plan: no `take_band` on the plan)
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
    # E22 (FILL lane 22) placed its four names after FILL lane 5's (+9 -> +13); FILL lane 11 its one after those;
    # E23 (FILL lane 23) its six after that one (+14 -> +20)
    assert keys[keys.index("take_in_band") + 13] == "cand_market_closed_db"
    # FILL lane 16 (+14), E21 / FILL lane 10 (+15 .. +20) and E23 / FILL lane 23 (+21 .. +26) placed theirs after FILL lane 11's; the key at +27
    assert keys[keys.index("take_in_band") + 14] == "turn_woke_fast"
    assert keys[keys.index("take_in_band") + 15] == "fast_order_open"
    assert keys[keys.index("take_in_band") + 21] == "cancel_fill_late"
    # FILL lane 24 (E24, the desk's hand) placed its four names nearer the key between E23's six and the key (+27 -> +31; -40 -> -44)
    assert keys[keys.index("take_in_band") + 27] == "hand_explained"
    # E25 (FILL lane 25) placed its four exit-confirmation names between E24's four and the key (+31 -> +35; -44 -> -48)
    assert keys[keys.index("take_in_band") + 31] == "exit_unconfirmed"
    # E28 (FILL lane 28): five names before E19's (the walk's re-read and the ledger guard); + 35 -> + 40
    assert keys[keys.index("take_in_band") + 35] == "walk_row_moved"
    # E29 (FILL lane 29): four names before drift_smaller_open, after E28's five; + 40 -> + 44
    assert keys[keys.index("take_in_band") + 40] == "hand_exit"
    # E30 (FILL lane 30): one name (post_only_backoff) before drift_smaller_open, after E29's four; + 44 -> + 45
    assert keys[keys.index("take_in_band") + 44] == "post_only_backoff"
    # E31 (FILL lane 31): ten names (maker_rest_at_touch .. rest_quote_unread)
    # after E30's one and before drift_smaller_open; + 45 -> + 55
    assert keys[keys.index("take_in_band") + 45] == "maker_rest_at_touch"
    assert keys[keys.index("take_in_band") + 54] == "rest_quote_unread"
    assert keys[keys.index("take_in_band") + 55] == "drift_smaller_open"
    # landed after E20 (`wrong_sign_hold`) and E14b (`exit_take_rested`), which sit before it by the same convention
    assert keys[keys.index("take_in_band") - 1] == "exit_take_rested"
    assert keys[keys.index("take_in_band") - 2] == "wrong_sign_hold"
    assert keys[keys.index("take_in_band") - 3] == "adopt_prior_venue_settled"
    # FILL lane 3 (three names), T2 (two), FILL lane 5 (three), E22 (FILL lane 22, four), FILL lane 11 (one) and E23 (FILL lane 23, six) placed theirs after this one (-14 -> -33) -- FILL lane 16 (one name) and E21 (FILL lane 10, six) landed first, so every index past this lane's six moved by seven more
    assert keys[-68] == "take_in_band" and keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    assert ml._new_stats()["census"]["take_in_band"] == 0
    # RE-PINNED at E31 (FILL lane 31, 2026-09-10). This lane's name is RETIRED: it stays
    # DECLARED in CENSUS_KEYS (every from-end index above is unmoved by that) and reads a
    # documentary zero for the life of the mirror, because the arm that emitted it is gone.
    # What is pinned in its place is that no emit site is left ANYWHERE, and that the file
    # has no reader of the band on the money path
    from tests.test_e31_maker_only import _code
    src = inspect.getsource(ml)
    code = _code(ml)
    assert '_mirror_stop("take_in_band"' not in code, "no emit site is left"
    assert "take_in_band" in src, "the name stays in the record (CENSUS_KEYS, the paragraphs)"
    assert "take_in_band" in ml.CENSUS_KEYS and ml._new_stats()["census"]["take_in_band"] == 0
    # the worker reads the band through nothing at all: no env knob, no rules call
    assert 'capped_env("MIRROR_TAKE_BAND' not in src and '_env_float("MIRROR_TAKE_BAND' not in src
    assert "MIRROR_TAKE_BAND" not in code and "take_band_width" not in code and "band_cent" not in code
    # E27's own reader is gone with it; the pure helpers stand, unmoved, in the rules module
    assert not hasattr(ml, "_take_band") and not hasattr(ml, "_short_take_band")
    wsrc = inspect.getsource(rules.take_band_width)
    assert "_num(MIRROR_TAKE_BAND), _num(MIRROR_TAKE_BAND_FRAC)" in wsrc, "both constants read at call time"
    # WHAT STANDS WHERE THE BAND ARM STOOD, on the no-order path of _act: the one maker
    # rest, priced by _wire_for and sent by _place -- no take arm between the plan and it
    asrc = _code(ml._act)
    assert "_entry_take" not in asrc and "_exit_take" not in asrc and "take_first" not in asrc
    assert "in_band=True" not in asrc and 'tif="IOC"' not in asrc
    i_wire = asrc.index("_wire_for (")
    i_rest = asrc.index('return await _place ( t , book , r , "increase" , p . side , wire , qty , his_px , p , plan )')
    assert i_wire < i_rest
    # the decision word: rules.order_decision still KNOWS 'take_in_band' (the record of the
    # rule) and _place_reserved can no longer ask for it -- is_take is the literal False
    assert inspect.getsource(rules.order_decision).count('"take_in_band"') == 1
    assert rules.order_decision("add", True, False, in_band=True) == "take_in_band"
    psrc = inspect.getsource(ml._place_reserved)
    assert "in_band=bool(in_band) and is_take" not in psrc and "is_take" not in _code(ml._place_reserved)
    assert 'rules.order_decision(action, False, wire_intent == "ORDER_INTENT_SELL_SHORT")' in psrc
    assert 'return "take_in_band"' not in psrc and '"take_in_band" if' not in psrc
    assert ml.REST_REREAD_SKIPPED == ("rest_reread_capped", "rest_quote_unread")
    assert not hasattr(ml, "IOC_SKIPPED")


def test_e14_the_name_is_retired_and_can_no_longer_be_emitted(monkeypatch):
    """RE-PINNED at E31 (FILL lane 31, 2026-09-10). E14's driver ran book
    544's shape to emit `take_in_band` once, for the worker file's
    coverage read. The name is retired: the shape that emitted it now
    rests, so the driver's job is the opposite -- run the same worlds and
    prove the census stays at ZERO. The worker file's coverage read
    carries `take_in_band` in its `unreachable` set with this lane named,
    which is what keeps the read honest."""
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.01)
    test_e14_book_544s_shape_first_sight_rests_the_whole_300_at_his_cent_inside_the_spread()
    assert ml._MIRROR_CENSUS.get("take_in_band|rn1", 0) == 0
    assert ml._MIRROR_CENSUS.get("take_placed|rn1", 0) == 0


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
