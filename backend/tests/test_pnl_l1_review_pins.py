"""PNL lane 1 review pins (2026-09-08): the at-or-better allowance (part
(a)) and D1's worse-side band (part (b)) driven through the WORKER'S
OWN TICK -- the money path -- on both axes, plus the review's findings
(r5b, r7, r8: strict xfails until the fold of 2026-09-08 closed them;
now plain pins) and one documented LOW.

Every pin here fails closed toward NOT trading: a short's block on the
worse side opens at 0 and places nothing; an unreadable axis at the
worker's call sizes 0; the whole-net target of an admitted block never
passes the $2,500 game cap; the axis the worker hands is the sign of
the target it just sized.
"""
import inspect

import pytest

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e12_catchup_side import LONG, SHORT, _admitted, _cu, _t
from tests.test_e12_flow_only import _one_book
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    M, N, NOW, SLUG, _armed, _census, _fill, _mkt, _places, _pool, _rails_2026_09_06, _shorts_on,
    _tick, _Venue,
)

SHORT_INTENT = "ORDER_INTENT_BUY_SHORT"


# ------------------------------------------------ the tick: a short's block

def _short_block_world(monkeypatch, bid, ask, size=5_000):
    """His other-token BUY of `size` at 0.72, 2.5 h old -- 0.28 on the
    axis, the whole net the block; nothing of his inside FIRST_SIGHT_S."""
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    fills = [_fill(N, "BUY", size, 0.72, NOW - 9000)]
    p = _pool(fills=fills, snap={M: 0.0, N: float(size)})
    return p, _Venue(bid=bid, ask=ask), _mkt(0.0, float(size))


def test_r1_a_shorts_block_with_the_mark_over_his_cost_opens_at_the_whole_net_target_and_sells(monkeypatch):
    """The mirror image through the tick: his 5,000 short at 0.28 on
    the axis, the mark 0.33 (5c OVER: better for a short). The book
    opens short at ratio x his whole net (-500), the verdict on its
    first plan is `at_or_better` with flow_base 0, the census counts
    the block bought under `open_catchup`, and ONE BUY_SHORT of 500
    goes out (a SELL of the contract on the wire)."""
    p, v, http = _short_block_world(monkeypatch, 0.32, 0.34)
    st = _tick(p, v, http=http)
    b = _one_book(p)
    assert b["intent"] == SHORT_INTENT and b["target"] == -500 and b["flow_base"] == 0.0
    cu = b["last_plan"]["catchup"]
    assert (cu["why"], cu["allowed"], cu["vwap"], cu["mark"]) == ("at_or_better", True, 0.28, 0.33)
    assert _census(st, "open_catchup") == 1 and _census(st, "open_flow_only") == 0
    pl = _places(v)
    assert len(pl) == 1 and pl[0][1] == SLUG and pl[0][3] == 500 and pl[0][6] == SHORT_INTENT
    assert v.orders[next(iter(v.orders))]["side"] == "SELL"


def test_r2_a_shorts_block_3c_under_his_cost_opens_flow_only_at_zero_and_places_nothing(monkeypatch):
    """The worse side of the short through the tick: the mark 0.25 is
    3c UNDER his 0.28 and the band on a 0.28 cost is max(2c, 2.8c) =
    2.8c: `flow_only`, the block (negative) stored, target 0, nothing
    on the wire, `open_flow_only` on the census."""
    p, v, http = _short_block_world(monkeypatch, 0.24, 0.26)
    st = _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["intent"], b["target"], b["flow_base"]) == (SHORT_INTENT, 0, -5_000.0)
    assert b["last_plan"]["catchup"]["why"] == "flow_only" and not _places(v)
    assert _census(st, "open_flow_only") == 1 and _census(st, "open_catchup") == 0


def test_r2b_a_shorts_block_2_5c_under_his_cost_is_within_the_band_through_the_tick(monkeypatch):
    """Inside the 2.8c band and past the 2c floor: the mark 0.255 (bid
    0.25 / ask 0.26) is `within_pct`, the short opens at -500."""
    p, v, http = _short_block_world(monkeypatch, 0.25, 0.26)
    st = _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["target"], b["flow_base"], b["last_plan"]["catchup"]["why"]) == (-500, 0.0, "within_pct")
    assert _census(st, "open_catchup") == 1 and len(_places(v)) == 1 and _places(v)[0][3] == 500


# ------------------------------------------------- the tick: a long's block

def _long_block_world(monkeypatch, size, px, bid, ask):
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", size, px, NOW - 9000)]
    p = _pool(fills=fills, snap={M: float(size), N: 0.0})
    return p, _Venue(bid=bid, ask=ask), _mkt(float(size))


def test_r3_an_at_or_better_block_is_sized_at_the_whole_net_and_never_past_the_2500_game_cap(monkeypatch):
    """His 100,000 at 0.50, the mark 0.45 (5c better): 10% is 10,000
    shares = $4,500 at the mark, so the whole-net target is the cap's
    2,500 / 0.45 = 5,555 -- never 10,000, never a dollar past $2,500.
    Every order the tick places is at or under his cent and inside it."""
    p, v, http = _long_block_world(monkeypatch, 100_000, 0.50, 0.44, 0.46)
    st = _tick(p, v, http=http)
    b = _one_book(p)
    assert b["last_plan"]["catchup"]["why"] == "at_or_better" and b["flow_base"] == 0.0
    assert b["target"] == 5_555 and b["target"] * 0.45 <= 2_500.0 and b["target"] < 10_000
    assert _census(st, "open_catchup") == 1
    # E4's arm, unchanged: the IOC take at his cent for the clip ($2,500 at 0.50 = 5,000), then the
    # rest for what the IOC left -- the whole target when it filled nothing; never more than the target
    pl = _places(v)
    assert [(c[2], c[3], c[5][14:17]) for c in pl] == [(0.50, 5_000, "IMM"), (0.44, 5_555, "GOO")]
    assert all(c[2] <= 0.50 for c in pl) and max(c[3] for c in pl) <= b["target"]
    assert sum(o["qty"] for o in p.orders.values() if o["state"] == "open") == 5_555
    # the IOC filled in full: the rest is the remainder, filled + resting = the target, never past it
    p2, v2, http2 = _long_block_world(monkeypatch, 100_000, 0.50, 0.44, 0.46)
    v2.ioc_fill = 5_000.0
    _tick(p2, v2, http=http2)
    b2 = _one_book(p2)
    assert b2["ledger_net"] == 5_000 and [(c[3], c[5][14:17]) for c in _places(v2)] == [(5_000, "IMM"), (555, "GOO")]
    assert b2["ledger_net"] + sum(o["qty"] for o in p2.orders.values() if o["state"] == "open") == 5_555
    assert rules.MIRROR_NET_CAP_USD == 2_500.0 and mi.MARKET_NET_CAP_USD == 2_500.0


def test_r4_a_block_exactly_5c_over_a_50c_cost_is_within_pct_at_the_whole_net_and_6c_is_flow_only(monkeypatch):
    """D1's band through the tick, at its edge: his 20,000 at 0.50, the
    mark 0.55 (bid 0.54 / ask 0.56) is exactly 5c over: `within_pct`,
    target 2,000, one BUY of 2,000 at or under the mark. A hair past the
    cap (mark 0.56) opens flow-only at 0 with nothing on the wire."""
    p, v, http = _long_block_world(monkeypatch, 20_000, 0.50, 0.54, 0.56)
    st = _tick(p, v, http=http)
    b = _one_book(p)
    assert (b["target"], b["flow_base"], b["last_plan"]["catchup"]["why"]) == (2_000, 0.0, "within_pct")
    assert _census(st, "open_catchup") == 1
    pl = _places(v)
    assert len(pl) == 1 and pl[0][1:4] == (SLUG, pl[0][2], 2_000) and pl[0][2] <= 0.55
    p2, v2, http2 = _long_block_world(monkeypatch, 20_000, 0.50, 0.55, 0.57)
    st2 = _tick(p2, v2, http=http2)
    b2 = _one_book(p2)
    assert (b2["target"], b2["flow_base"], b2["last_plan"]["catchup"]["why"]) == (0, 20_000.0, "flow_only")
    assert not _places(v2) and _census(st2, "open_flow_only") == 1


# ------------------------------------------ the worker's call, fail closed

def test_r5_the_axis_the_worker_hands_is_the_sign_of_the_target_and_a_falsy_unread_axis_sizes_nothing():
    fills = [_fill(M, "BUY", 19_400, 0.549, NOW - 9000)]
    for axis in (None, 0):
        cu, target = ml._open_flow(_t(), fills, M, N, axis, 19_400.0, 0.50, 0.10, 1_940, 2_500.0, True)
        assert (cu["why"], cu["allowed"], cu["flow_base"], target) == ("axis_unread", False, 19_400.0, 0), axis
    # the axis the worker hands is the sign of the target it just sized: a bool, never a guess
    src = inspect.getsource(ml._tick_candidate)
    assert "short = target < 0" in src
    assert src.index("short = target < 0") < src.index("_open_flow(t, fills, la, oa, short, fills_net, r.mark")


def test_r5b_FINDING_an_unreadable_axis_at_the_workers_call_never_sizes_the_whole_net():
    """Review HIGH-1, folded 2026-09-08: _open_flow's clamp read the AXIS
    as a bool, so an unreadable axis whose truthiness disagreed with the
    target's sign handed back the WHOLE-NET target on an `axis_unread`
    verdict (the block bought). Latent (the caller hands target < 0).
    Fixed: `axis_unread` returns 0 outright, and the clamp is on the
    target's sign."""
    fills = [_fill(M, "BUY", 19_400, 0.549, NOW - 9000)]
    for axis in (1, "short", [1]):
        cu, target = ml._open_flow(_t(), fills, M, N, axis, 19_400.0, 0.50, 0.10, 1_940, 2_500.0, True)
        assert (cu["why"], cu["allowed"], target) == ("axis_unread", False, 0), axis
    sfills = [_fill(N, "BUY", 5_000, 0.72, NOW - 9000)]
    for axis in (None, 0, ""):
        cu, target = ml._open_flow(_t(), sfills, M, N, axis, -5_000.0, 0.33, 0.10, -500, 2_500.0, True)
        assert (cu["why"], cu["allowed"], target) == ("axis_unread", False, 0), axis


def test_r6_the_band_at_its_float_edges_on_both_axes():
    # a 0.35 cost: 3.5c band. 3.5c over exactly is within; 3.6c is not; the short mirrors it under
    assert _cu(11_000.0, 0.385, 0.10, 10_000.0, 0.35, LONG)["why"] == "within_pct"
    assert _admitted(_cu(11_000.0, 0.386, 0.10, 10_000.0, 0.35, LONG)) == (False, 10_000.0, "flow_only")
    assert _cu(-11_000.0, 0.315, 0.10, -10_000.0, 0.35, SHORT)["why"] == "within_pct"
    assert _admitted(_cu(-11_000.0, 0.314, 0.10, -10_000.0, 0.35, SHORT)) == (False, -10_000.0, "flow_only")
    # the same marks on the other axis are the better side
    assert _cu(-11_000.0, 0.386, 0.10, -10_000.0, 0.35, SHORT)["why"] == "at_or_better"
    assert _cu(11_000.0, 0.314, 0.10, 10_000.0, 0.35, LONG)["why"] == "at_or_better"
    # a 0.99 cost: the cap, and the ladder's top is still a mark
    assert _cu(11_000.0, 0.99, 0.10, 10_000.0, 0.95, LONG)["why"] == "within_pct"
    assert _cu(11_000.0, 0.99, 0.10, 10_000.0, 0.93, LONG)["why"] == "flow_only"


# ---------------------------------------------------------- the findings

def test_r7_FINDING_an_off_ladder_vwap_is_vwap_unread_never_at_or_better():
    """Review MEDIUM-1, folded 2026-09-08: an off-ladder finite vwap (a
    price no fill could carry) was read as his cost and the better side
    admitted any mark against it; E12's symmetric tolerance refused it.
    Fixed: his cost must read 0 < vwap < 1, else `vwap_unread` on both
    sides."""
    for v in (1.5, 55.0, 100.0):
        assert _cu(11_000.0, 0.50, 0.10, 10_000.0, v, LONG)["why"] == "vwap_unread", v
    for v in (-0.5, -55.0):
        assert _cu(-11_000.0, 0.50, 0.10, -10_000.0, v, SHORT)["why"] == "vwap_unread", v


def test_r8_FINDING_tol_lowered_from_the_env_still_bounds_the_worse_side():
    """Review MEDIUM-2, folded 2026-09-08: MIRROR_CATCHUP_TOL_CENTS
    lowered from the environment (the only worse-side knob before D1)
    no longer bounded the worse side -- TOL 1 still admitted 4.9c over
    a 0.50 cost. Fixed: a tolerance under its 2c default IS the band
    (_catchup_band_cents); an env lowering lowers."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rules, "MIRROR_CATCHUP_TOL_CENTS", 1.0)
        assert _cu(21_000.0, 0.549, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only"
        assert _cu(21_000.0, 0.511, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only"
        assert _cu(-21_000.0, 0.451, 0.10, -20_000.0, 0.50, SHORT)["why"] == "flow_only"


def test_r9_LOW_the_shorts_percentage_is_read_on_the_axis_price_not_on_what_he_paid():
    """Documented, not fixed (review LOW-1): a short built at 0.80 on
    the axis is his BUY of the other token at 0.20; the band is 10% of
    the AXIS price (8c, capped 5c), so a mark 5c under is admitted --
    25% of the 0.20 he paid. The dollar bound (5c x shares) holds; the
    program's text is 'MIRROR_CATCHUP_PCT x vwap' on the axis."""
    assert _cu(-11_000.0, 0.75, 0.10, -10_000.0, 0.80, SHORT)["why"] == "within_pct"
    assert _cu(-11_000.0, 0.749, 0.10, -10_000.0, 0.80, SHORT)["why"] == "flow_only"
    assert rules._catchup_band_cents(2.0, 0.80) == 5.0
