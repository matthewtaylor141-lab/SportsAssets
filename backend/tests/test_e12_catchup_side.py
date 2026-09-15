"""PNL lane 1 (2026-09-08): E12's catch-up allowance reads the SIDE of
his cost -- a mark at or better than his cost on the book's axis is
admitted at any distance; only the worse side is a tolerance.

Owner (~11:50Z): "Fix all 3 of these immediately. I want to know when
he makes money we make money. This needs to be right." hard2/
PNL_program.md lane 1: `abs(m - v) <= tol` refused a mark 5c UNDER his
cost on a long exactly as a mark 5c over -- book 266 (his vwap
0.503-0.549, mark 0.50, |m - v| up to 4.9c > 2c) would have opened
flow-only although the mark was better than his cost. Proportionality
is the objective; a better mark carries all of his edge and no premium.

Part (a): `at_or_better` (long m <= v, short m >= v, contract space,
the axis mi.vwap_of reads both tokens onto). Part (b) -- OWNER DECISION
D1 = YES (2026-09-08 ~13:3xZ, "make the changes, and get it live and
running immediately"): with the axis handed in the WORSE side's band is
min(MIRROR_CATCHUP_MAX_CENTS 5, max(MIRROR_CATCHUP_TOL_CENTS 2,
MIRROR_CATCHUP_PCT 0.10 x vwap)) cents; a mark past the 2c floor but
within the band is `within_pct` (`within_tol` stays for the floor);
both knobs capped_env, the env may only LOWER the band. Every verdict
here fails closed toward NOT buying the block: an axis handed in
unreadable is `axis_unread`; the E12 five-argument call keeps E12's
symmetric flat 2c verdict (test_e12_flow_only.py and
test_e12b_witness.py stand byte-unchanged; one E12 review pin whose
first world assumed the flat 2c on a 0.61 cost is re-pinned at 6c and
says why).

Mutants these pins kill: <= flipped to >= on the long (the 5c-under
and 3c-over rows disagree); the short's mirror dropped (the short 5c
over row); axis_unread treated as allowed (the None row); the worker
not handing the axis (the source pin and the _open_flow drive); the
allowance read as a tolerance (tol 0 still admits the better side);
the band's cap dropped (0.80 cost at 6c); the band's floor dropped
(0.20 cost at 2c); the band granted to the five-argument call (the
0.50 cost 4.9c over without the axis); the pct read as a fraction of
a cent (0.50 cost at 4.9c); the env allowed to raise (PCT 0.5, MAX 9).
"""
import inspect
import math
import types

import pytest

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_mirror_live_worker import M, N, NOW, _fill

CATCHUP_KEYS = {"vwap", "mark", "tol", "allowed", "flow_base", "why"}
LONG, SHORT = False, True


def _cu(net, mark, ratio, block, vwap, short):
    return rules.open_catchup(net, mark, ratio, block, vwap, short=short)


def _admitted(out):
    return (out["allowed"], out["flow_base"], out["why"])


# ------------------------------------------------------------ the long axis

def test_long_mark_5c_under_his_cost_is_admitted_at_or_better_book_266():
    # book 266: his vwap over the block 0.549, the mark 0.50 -- 4.9c BETTER
    out = _cu(19_400.0, 0.50, 0.10, 19_400.0, 0.549, LONG)
    assert set(out) == CATCHUP_KEYS, "no new key on the plan row"
    assert out == {"vwap": 0.549, "mark": 0.50, "tol": 2.0, "allowed": True, "flow_base": 0.0,
                   "why": "at_or_better"}
    # any distance: 20c under is still his edge and no premium
    assert _admitted(_cu(19_400.0, 0.35, 0.10, 19_400.0, 0.549, LONG)) == (True, 0.0, "at_or_better")


def test_long_mark_equal_to_his_cost_is_at_or_better():
    assert _admitted(_cu(11_000.0, 0.29, 0.10, 10_000.0, 0.29, LONG)) == (True, 0.0, "at_or_better")
    # the float epsilon the tolerance reads with, and no wider: a hair over is the worse side
    assert _cu(11_000.0, 0.29 + 5e-10, 0.10, 10_000.0, 0.29, LONG)["why"] == "at_or_better"
    assert _cu(11_000.0, 0.29 + 1e-6, 0.10, 10_000.0, 0.29, LONG)["why"] == "within_tol"


def test_long_mark_1c_over_his_cost_is_within_tol_as_e12_pinned():
    assert _admitted(_cu(11_000.0, 0.30, 0.10, 10_000.0, 0.29, LONG)) == (True, 0.0, "within_tol")
    assert _cu(11_000.0, 0.31, 0.10, 10_000.0, 0.29, LONG)["why"] == "within_tol", "exactly 2c is within"


def test_long_mark_3c_over_his_29c_cost_is_flow_only_past_the_band():
    # on a 0.29 cost D1's band is max(2c, 10% x 0.29 = 2.9c) = 2.9c: 3c over is past it
    out = _cu(11_000.0, 0.32, 0.10, 10_000.0, 0.29, LONG)
    assert _admitted(out) == (False, 10_000.0, "flow_only")
    assert _cu(11_000.0, 0.3201, 0.10, 10_000.0, 0.29, LONG)["why"] == "flow_only"
    # the row's `tol` is the floor knob, 2.0 (the band is derived from the row's vwap)
    assert out["tol"] == 2.0 and rules.MIRROR_CATCHUP_TOL_CENTS == 2.0
    # 2.5c over is inside the 2.9c band and past the 2c floor: within_pct
    assert _admitted(_cu(11_000.0, 0.315, 0.10, 10_000.0, 0.29, LONG)) == (True, 0.0, "within_pct")


# ----------------------------------------------------------- the short axis

def test_short_mark_5c_over_his_cost_is_at_or_better_in_contract_space():
    # E12b's row: his BUYs of the other token at 0.72 read as 0.28 on the axis; the mark 0.33
    # is 5c OVER -- a short is built by selling, so over his cost is BETTER (flow_only under E12)
    out = _cu(-5_500.0, 0.33, 0.10, -5_000.0, 0.28, SHORT)
    assert _admitted(out) == (True, 0.0, "at_or_better")
    assert out["vwap"] == 0.28 and out["mark"] == 0.33
    # the same numbers on the LONG axis are the worse side, 5c out: the axis decides, not the distance
    assert _admitted(_cu(5_500.0, 0.33, 0.10, 5_000.0, 0.28, LONG)) == (False, 5_000.0, "flow_only")


def test_short_mark_3c_under_his_cost_is_flow_only():
    out = _cu(-5_500.0, 0.25, 0.10, -5_000.0, 0.28, SHORT)
    assert _admitted(out) == (False, -5_000.0, "flow_only"), "a short's block is handed back negative"
    # and the same 3c on the long axis would have been at_or_better: the mirror is real
    assert _cu(5_500.0, 0.25, 0.10, 5_000.0, 0.28, LONG)["why"] == "at_or_better"


def test_short_mark_1c_under_his_cost_is_within_tol_and_equal_is_at_or_better():
    assert _admitted(_cu(-5_500.0, 0.27, 0.10, -5_000.0, 0.28, SHORT)) == (True, 0.0, "within_tol")
    assert _cu(-5_500.0, 0.26, 0.10, -5_000.0, 0.28, SHORT)["why"] == "within_tol", "exactly 2c"
    assert _cu(-5_500.0, 0.28, 0.10, -5_000.0, 0.28, SHORT)["why"] == "at_or_better"
    assert _cu(-5_500.0, 0.28 - 5e-10, 0.10, -5_000.0, 0.28, SHORT)["why"] == "at_or_better"


# ------------------------------------------------------- fails closed, named

def test_an_unreadable_axis_is_axis_unread_and_never_admits():
    for axis in (None, 1, 0, "short", "long", 1.0, [], object()):
        out = _cu(11_000.0, 0.20, 0.10, 10_000.0, 0.29, axis)
        assert _admitted(out) == (False, 10_000.0, "axis_unread"), axis
        assert set(out) == CATCHUP_KEYS
    # a short's block on an unreadable axis: the block itself, negative, nothing bought
    assert _admitted(_cu(-5_500.0, 0.33, 0.10, -5_000.0, 0.28, None)) == (False, -5_000.0, "axis_unread")
    # named in the docstring beside the others
    assert "`axis_unread`" in (rules.open_catchup.__doc__ or "")


def test_vwap_unread_is_unchanged_with_the_axis_handed_in():
    for axis in (LONG, SHORT):
        out = _cu(11_000.0, 0.20, 0.10, 10_000.0, None, axis)
        assert _admitted(out) == (False, 10_000.0, "vwap_unread"), axis
        assert _cu(11_000.0, 0.20, 0.10, 10_000.0, "0.29", axis)["why"] == "vwap_unread", "a string is not read"
        assert _cu(11_000.0, 0.20, 0.10, 10_000.0, float("nan"), axis)["why"] == "vwap_unread"


def test_small_bet_is_unchanged_the_exact_copy_under_ten_dollars_copies_whole():
    for axis in (LONG, SHORT):
        assert _admitted(_cu(12.0, 0.72, 1.0, 10.0, 0.29, axis)) == (True, 0.0, "small_bet"), axis
        assert _admitted(_cu(-12.0, 0.10, 1.0, -10.0, 0.29, axis)) == (True, 0.0, "small_bet"), axis
    # and ahead of an unreadable axis: the ratio decides before the mark is compared
    assert _cu(12.0, 0.72, 1.0, 10.0, 0.29, None)["why"] == "small_bet"


def test_tol_lowered_to_zero_still_admits_at_or_better_the_allowance_is_not_a_tolerance():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rules, "MIRROR_CATCHUP_TOL_CENTS", 0.0)
        out = _cu(19_400.0, 0.50, 0.10, 19_400.0, 0.549, LONG)
        assert _admitted(out) == (True, 0.0, "at_or_better") and out["tol"] == 0.0
        assert _cu(11_000.0, 0.29, 0.10, 10_000.0, 0.29, LONG)["why"] == "at_or_better"
        assert _cu(-5_500.0, 0.33, 0.10, -5_000.0, 0.28, SHORT)["why"] == "at_or_better"
        # the worse side is the tolerance, and at 0 it is never
        assert _admitted(_cu(11_000.0, 0.30, 0.10, 10_000.0, 0.29, LONG)) == (False, 10_000.0, "tol_zero")
        assert _cu(-5_500.0, 0.27, 0.10, -5_000.0, 0.28, SHORT)["why"] == "tol_zero"
        # the E12 five-argument call at tol 0 is E12's: tol_zero even at equality
        assert rules.open_catchup(11_000.0, 0.29, 0.10, 10_000.0, 0.29)["why"] == "tol_zero"
        mp.setattr(rules, "MIRROR_CATCHUP_TOL_CENTS", -3.0)
        assert _cu(19_400.0, 0.50, 0.10, 19_400.0, 0.549, LONG)["why"] == "at_or_better"
    doc = rules.open_catchup.__doc__ or ""
    assert "ALLOWANCE, NOT A TOLERANCE" in doc and "BEFORE `tol_zero`" in doc
    # the env can only LOWER the worse side's tolerance (capped_env); nothing widens the better side
    src = inspect.getsource(rules)
    assert 'capped_env("MIRROR_CATCHUP_TOL_CENTS", 2.0, floor=0.0)' in src


def test_block_unread_is_unchanged_his_whole_net_stands_in_and_nothing_is_bought():
    for axis in (LONG, SHORT, None):
        out = _cu(11_000.0, 0.20, 0.10, None, 0.29, axis)
        assert _admitted(out) == (False, 11_000.0, "block_unread"), axis
    assert _cu(None, 0.20, 0.10, None, 0.29, LONG)["flow_base"] is None
    assert _cu(11_000.0, 0.20, 0.10, "10000", 0.29, LONG)["why"] == "block_unread"


def test_a_nan_or_off_ladder_mark_is_mark_unread_never_at_or_better():
    for axis in (LONG, SHORT):
        assert _admitted(_cu(11_000.0, float("nan"), 0.10, 10_000.0, 0.29, axis)) == (False, 10_000.0, "mark_unread")
        assert _cu(11_000.0, None, 0.10, 10_000.0, 0.29, axis)["why"] == "mark_unread"
        assert _cu(11_000.0, float("inf"), 0.10, 10_000.0, 0.29, axis)["why"] == "mark_unread"
    # off the ladder on the BETTER side of his cost is still no mark: 0.001 under a long at 0.29,
    # 0.999 over a short at 0.28 -- the ladder is 0.01..0.99
    assert _cu(11_000.0, 0.001, 0.10, 10_000.0, 0.29, LONG)["why"] == "mark_unread"
    assert _cu(11_000.0, 1e-320, 0.10, 10_000.0, 0.29, LONG)["why"] == "mark_unread"
    assert _cu(-5_500.0, 0.999, 0.10, -5_000.0, 0.28, SHORT)["why"] == "mark_unread"
    assert _cu(-5_500.0, 1.0, 0.10, -5_000.0, 0.28, SHORT)["why"] == "mark_unread"


def test_no_block_is_unchanged_flow_is_his_net_on_either_axis():
    for axis in (LONG, SHORT, None):
        assert _admitted(_cu(1_000.0, 0.72, 0.10, 0.0, None, axis)) == (True, 0.0, "no_block"), axis


def test_the_verdicts_stand_in_order_block_no_block_small_bet_axis_then_the_mark():
    # every refusal ahead of the mark: block_unread > no_block > small_bet > axis_unread > (at_or_better)
    # > tol_zero > vwap_unread > mark_unread > within_tol > flow_only
    assert _cu(11_000.0, 0.20, 1.0, None, None, None)["why"] == "block_unread"
    assert _cu(11_000.0, 0.20, 1.0, 0.0, None, None)["why"] == "no_block"
    assert _cu(11_000.0, 0.20, 1.0, 10_000.0, None, None)["why"] == "small_bet"
    assert _cu(11_000.0, 0.20, 0.10, 10_000.0, None, None)["why"] == "axis_unread"
    assert _cu(11_000.0, 0.20, 0.10, 10_000.0, 0.29, LONG)["why"] == "at_or_better"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rules, "MIRROR_CATCHUP_TOL_CENTS", 0.0)
        assert _cu(11_000.0, 0.20, 0.10, 10_000.0, None, LONG)["why"] == "tol_zero", "unchanged: tol before vwap"
        assert _cu(11_000.0, None, 0.10, 10_000.0, 0.29, LONG)["why"] == "tol_zero"
    assert _cu(11_000.0, 0.20, 0.10, 10_000.0, None, LONG)["why"] == "vwap_unread"
    assert _cu(11_000.0, None, 0.10, 10_000.0, 0.29, LONG)["why"] == "mark_unread"
    assert _cu(11_000.0, 0.30, 0.10, 10_000.0, 0.29, LONG)["why"] == "within_tol"
    assert _cu(11_000.0, 0.315, 0.10, 10_000.0, 0.29, LONG)["why"] == "within_pct"
    assert _cu(11_000.0, 0.35, 0.10, 10_000.0, 0.29, LONG)["why"] == "flow_only"
    order = [f"`{w}`" for w in ("block_unread", "no_block", "small_bet", "axis_unread", "at_or_better",
                                "tol_zero", "vwap_unread", "mark_unread", "within_tol", "within_pct",
                                "flow_only")]
    doc = rules.open_catchup.__doc__ or ""
    assert [doc.index(w) for w in order] == sorted(doc.index(w) for w in order)


def test_the_e12_five_argument_call_keeps_e12s_symmetric_verdict_never_wider():
    # the E12 and E12b pins, verbatim: the short 5c over stays flow_only WITHOUT the axis
    assert rules.open_catchup(-5_500.0, 0.33, 0.10, -5_000.0, 0.28)["why"] == "flow_only"
    assert rules.open_catchup(-13_000.0, 0.31, 0.10, -12_000.0, 0.28)["flow_base"] == -12_000.0
    # and a long 5c under stays flow_only without the axis: nothing the old call gets is new
    assert rules.open_catchup(19_400.0, 0.50, 0.10, 19_400.0, 0.549)["why"] == "flow_only"
    assert rules.open_catchup(11_000.0, 0.30, 0.10, 10_000.0, 0.29)["why"] == "within_tol"
    assert rules.open_catchup(11_000.0, 0.29, 0.10, 10_000.0, 0.29)["why"] == "within_tol"
    # the sentinel is not a bool and is not None: only its absence keeps E12's call
    assert rules._AXIS_NOT_GIVEN is not None and not isinstance(rules._AXIS_NOT_GIVEN, bool)
    sig = inspect.signature(rules.open_catchup)
    assert list(sig.parameters) == ["net", "mark", "ratio", "block", "vwap", "short"]
    assert sig.parameters["short"].default is rules._AXIS_NOT_GIVEN


def test_mark_at_or_better_is_the_one_comparison_and_reads_the_axis():
    assert rules._mark_at_or_better(0.50, 0.549, False) and not rules._mark_at_or_better(0.50, 0.549, True)
    assert rules._mark_at_or_better(0.33, 0.28, True) and not rules._mark_at_or_better(0.33, 0.28, False)
    assert rules._mark_at_or_better(0.29, 0.29, False) and rules._mark_at_or_better(0.29, 0.29, True)
    assert not math.isnan(0.29)


# ----------------------------------------------------------- the worker's call

def _t():
    return types.SimpleNamespace(flow_col=True, flow_clock_col=True, now=NOW)


def test_the_open_hands_the_books_axis_to_the_verdict_source_pin():
    src = inspect.getsource(ml._open_flow)
    assert "rules.open_catchup(net, mark, ratio, block, vwap, short=short)" in src
    assert src.count("rules.open_catchup(") == 1
    # the census: an at-or-better open is a block bought (`open_catchup`), no new name
    csrc = inspect.getsource(ml._tick_candidate)
    assert 'cu.get("why") in ("small_bet", "within_tol", "at_or_better", "within_pct")' in csrc
    for w in ("at_or_better", "axis_unread", "within_pct"):
        assert w not in ml.CENSUS_KEYS, w


def test_the_open_admits_a_long_block_whose_mark_is_under_his_cost_at_the_whole_net_target():
    since = NOW - ml.FIRST_SIGHT_S
    fills = [_fill(M, "BUY", 19_400, 0.549, NOW - 9000)]          # book 266's block, 2.5 h old
    assert mi.pre_existing_block(19_400.0, fills, M, N, since) == 19_400.0
    assert mi.vwap_of(fills, M, N, before=since) == 0.549
    cu, target = ml._open_flow(_t(), fills, M, N, False, 19_400.0, 0.50, 0.10, 1_940, 2_500.0, True)
    assert (cu["why"], cu["allowed"], cu["flow_base"]) == ("at_or_better", True, 0.0)
    assert target == 1_940, "ratio x his whole net, as before E12 (the game cap is the caller's)"
    # the same block with the mark 3.1c OVER his cost: D1's band on a 0.549 cost is 5c (10% = 5.49c,
    # capped), so it is within_pct and bought at the whole-net target (part (a) pinned this flow_only
    # under the flat 2c; re-pinned under D1 = YES)
    cu2, target2 = ml._open_flow(_t(), fills, M, N, False, 19_400.0, 0.58, 0.10, 1_940, 2_500.0, True)
    assert (cu2["why"], cu2["allowed"], cu2["flow_base"], target2) == ("within_pct", True, 0.0, 1_940)
    # 5.1c over is past the cap: flow only, nothing at open (the flow is 0)
    cu3, target3 = ml._open_flow(_t(), fills, M, N, False, 19_400.0, 0.60, 0.10, 1_940, 2_500.0, True)
    assert (cu3["why"], cu3["allowed"], cu3["flow_base"], target3) == ("flow_only", False, 19_400.0, 0)


def test_the_open_admits_a_short_block_whose_mark_is_over_his_cost_on_its_axis():
    since = NOW - ml.FIRST_SIGHT_S
    fills = [_fill(N, "BUY", 5_000, 0.72, NOW - 9000)]            # his other-token BUY: 0.28 on the axis
    assert mi.vwap_of(fills, M, N, short=True, before=since) == 0.28
    cu, target = ml._open_flow(_t(), fills, M, N, True, -5_000.0, 0.33, 0.10, -500, 2_500.0, True)
    assert (cu["why"], cu["allowed"], cu["flow_base"], target) == ("at_or_better", True, 0.0, -500)
    # 3c under on the short: flow only
    cu2, target2 = ml._open_flow(_t(), fills, M, N, True, -5_000.0, 0.25, 0.10, -500, 2_500.0, True)
    assert (cu2["why"], cu2["flow_base"], target2) == ("flow_only", -5_000.0, 0)
    # the old-rule book (057 columns absent) is untouched: column_absent, the whole-net target
    t0 = types.SimpleNamespace(flow_col=False, flow_clock_col=False, now=NOW)
    cu3, target3 = ml._open_flow(t0, fills, M, N, True, -5_000.0, 0.25, 0.10, -500, 2_500.0, True)
    assert (cu3["why"], cu3["allowed"], target3) == ("column_absent", True, -500)


# ------------------------------------- part (b): the worse side's band (D1 = YES)

_KEEP = object()


def _band(mp, pct=_KEEP, mx=_KEEP, tol=_KEEP):
    # a knob is set to WHATEVER is handed (None included: the unreadable-knob pins); _KEEP leaves it
    if pct is not _KEEP:
        mp.setattr(rules, "MIRROR_CATCHUP_PCT", pct)
    if mx is not _KEEP:
        mp.setattr(rules, "MIRROR_CATCHUP_MAX_CENTS", mx)
    if tol is not _KEEP:
        mp.setattr(rules, "MIRROR_CATCHUP_TOL_CENTS", tol)


def test_d1_the_knobs_are_capped_env_ten_percent_and_five_cents_and_only_lower():
    assert rules.MIRROR_CATCHUP_PCT == 0.10 and rules.MIRROR_CATCHUP_MAX_CENTS == 5.0
    assert rules.MIRROR_CATCHUP_TOL_CENTS == 2.0, "the floor of the band is E12's tolerance"
    src = inspect.getsource(rules)
    assert 'MIRROR_CATCHUP_PCT = capped_env("MIRROR_CATCHUP_PCT", 0.10, floor=0.0)' in src
    assert 'MIRROR_CATCHUP_MAX_CENTS = capped_env("MIRROR_CATCHUP_MAX_CENTS", 5.0, floor=0.0)' in src
    assert src.count('capped_env("MIRROR_CATCHUP_PCT"') == 1 and src.count('capped_env("MIRROR_CATCHUP_MAX_CENTS"') == 1
    assert "MIRROR_CATCHUP_PCT" in rules.__all__ and "MIRROR_CATCHUP_MAX_CENTS" in rules.__all__
    # the band, pure: min(cap, max(floor, pct x vwap)) in cents on the contract price
    assert rules._catchup_band_cents(2.0, 0.50) == 5.0
    assert rules._catchup_band_cents(2.0, 0.80) == 5.0, "capped, not 8c"
    assert abs(rules._catchup_band_cents(2.0, 0.20) - 2.0) < 1e-12, "10% of 0.20 is the floor"
    assert rules._catchup_band_cents(2.0, 0.10) == 2.0 and rules._catchup_band_cents(2.0, 0.0) == 2.0
    assert abs(rules._catchup_band_cents(2.0, 0.35) - 3.5) < 1e-12
    doc = rules.open_catchup.__doc__ or ""
    assert "`within_pct`" in doc and "D1 = YES" in doc and "2026-09-08" in doc


def test_d1_a_50c_cost_has_a_5c_band_admits_4_9c_over_and_refuses_5_1c():
    # 10% of 0.50 is 5c, the cap is 5c: the band is 5c
    out = _cu(21_000.0, 0.549, 0.10, 20_000.0, 0.50, LONG)
    assert set(out) == CATCHUP_KEYS, "no new key on the plan row"
    assert out == {"vwap": 0.50, "mark": 0.549, "tol": 2.0, "allowed": True, "flow_base": 0.0,
                   "why": "within_pct"}
    assert _admitted(_cu(21_000.0, 0.551, 0.10, 20_000.0, 0.50, LONG)) == (False, 20_000.0, "flow_only")
    assert _cu(21_000.0, 0.55, 0.10, 20_000.0, 0.50, LONG)["why"] == "within_pct", "exactly 5c is within"
    assert _cu(21_000.0, 0.55 + 1e-6, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only"
    # the floor keeps its own name: 2c over is within_tol, 2.1c over is the band's
    assert _cu(21_000.0, 0.52, 0.10, 20_000.0, 0.50, LONG)["why"] == "within_tol"
    assert _cu(21_000.0, 0.521, 0.10, 20_000.0, 0.50, LONG)["why"] == "within_pct"


def test_d1_a_20c_cost_keeps_the_2c_floor_as_today():
    # 10% of 0.20 is 2c: the band IS the floor, and nothing is within_pct
    assert _admitted(_cu(11_000.0, 0.22, 0.10, 10_000.0, 0.20, LONG)) == (True, 0.0, "within_tol")
    assert _cu(11_000.0, 0.21, 0.10, 10_000.0, 0.20, LONG)["why"] == "within_tol"
    assert _admitted(_cu(11_000.0, 0.221, 0.10, 10_000.0, 0.20, LONG)) == (False, 10_000.0, "flow_only")
    assert _cu(11_000.0, 0.23, 0.10, 10_000.0, 0.20, LONG)["why"] == "flow_only"
    # under the floor's cost (0.10: 1c) the floor still stands at 2c
    assert _cu(11_000.0, 0.12, 0.10, 10_000.0, 0.10, LONG)["why"] == "within_tol"
    assert _cu(11_000.0, 0.121, 0.10, 10_000.0, 0.10, LONG)["why"] == "flow_only"


def test_d1_an_80c_cost_is_capped_at_5c_not_8c():
    assert _admitted(_cu(11_000.0, 0.849, 0.10, 10_000.0, 0.80, LONG)) == (True, 0.0, "within_pct")
    assert _cu(11_000.0, 0.85, 0.10, 10_000.0, 0.80, LONG)["why"] == "within_pct"
    assert _admitted(_cu(11_000.0, 0.851, 0.10, 10_000.0, 0.80, LONG)) == (False, 10_000.0, "flow_only")
    assert _cu(11_000.0, 0.86, 0.10, 10_000.0, 0.80, LONG)["why"] == "flow_only", "8c would admit 6c"
    assert _cu(11_000.0, 0.88, 0.10, 10_000.0, 0.80, LONG)["why"] == "flow_only"


def test_d1_env_pct_lowered_to_5_percent_narrows_the_band():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MIRROR_CATCHUP_PCT", "0.05")
        assert rules.capped_env("MIRROR_CATCHUP_PCT", 0.10, floor=0.0) == 0.05
        _band(mp, pct=0.05)
        # 5% of 0.50 is 2.5c: 2.4c within_pct, 2.6c flow_only
        assert _cu(21_000.0, 0.524, 0.10, 20_000.0, 0.50, LONG)["why"] == "within_pct"
        assert _admitted(_cu(21_000.0, 0.526, 0.10, 20_000.0, 0.50, LONG)) == (False, 20_000.0, "flow_only")
        assert _cu(21_000.0, 0.549, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only"
        # 5% of 0.80 is 4c, under the cap
        assert _cu(11_000.0, 0.839, 0.10, 10_000.0, 0.80, LONG)["why"] == "within_pct"
        assert _cu(11_000.0, 0.841, 0.10, 10_000.0, 0.80, LONG)["why"] == "flow_only"
        # at 0 the band is the floor alone
        _band(mp, pct=0.0)
        assert _cu(21_000.0, 0.52, 0.10, 20_000.0, 0.50, LONG)["why"] == "within_tol"
        assert _cu(21_000.0, 0.521, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only"


def test_d1_env_pct_raised_to_50_percent_stays_10_percent():
    with pytest.MonkeyPatch.context() as mp:
        for raw, want in (("0.5", 0.10), ("1", 0.10), ("junk", 0.10), ("inf", 0.10), ("-1", 0.0),
                          ("0", 0.0), ("0.05", 0.05)):
            mp.setenv("MIRROR_CATCHUP_PCT", raw)
            assert rules.capped_env("MIRROR_CATCHUP_PCT", 0.10, floor=0.0) == want, raw
        mp.setenv("MIRROR_CATCHUP_PCT", "0.5")
        _band(mp, pct=rules.capped_env("MIRROR_CATCHUP_PCT", 0.10, floor=0.0))
        assert rules.MIRROR_CATCHUP_PCT == 0.10
        # 50% of 0.50 would be 25c; the band is 5c
        assert _cu(21_000.0, 0.551, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only"
        assert _cu(21_000.0, 0.60, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only"
        # and 50% of 0.20 would be 10c; the band is the 2c floor
        assert _cu(11_000.0, 0.221, 0.10, 10_000.0, 0.20, LONG)["why"] == "flow_only"


def test_d1_env_max_lowered_to_3_cents_caps_the_band_at_3():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MIRROR_CATCHUP_MAX_CENTS", "3")
        assert rules.capped_env("MIRROR_CATCHUP_MAX_CENTS", 5.0, floor=0.0) == 3.0
        _band(mp, mx=3.0)
        assert _cu(21_000.0, 0.529, 0.10, 20_000.0, 0.50, LONG)["why"] == "within_pct"
        assert _cu(21_000.0, 0.53, 0.10, 20_000.0, 0.50, LONG)["why"] == "within_pct"
        assert _admitted(_cu(21_000.0, 0.531, 0.10, 20_000.0, 0.50, LONG)) == (False, 20_000.0, "flow_only")
        assert _cu(11_000.0, 0.831, 0.10, 10_000.0, 0.80, LONG)["why"] == "flow_only"
        # a cap under the floor narrows the band under it: the env may only lower (1c: 1.5c over refused)
        _band(mp, mx=1.0)
        assert _cu(21_000.0, 0.515, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only"
        assert _cu(21_000.0, 0.509, 0.10, 20_000.0, 0.50, LONG)["why"] == "within_tol"
        # at 0 there is no worse side at all; the better side is untouched
        _band(mp, mx=0.0)
        assert _cu(21_000.0, 0.501, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only"
        assert _cu(21_000.0, 0.50, 0.10, 20_000.0, 0.50, LONG)["why"] == "at_or_better"


def test_d1_env_max_raised_to_9_cents_stays_5():
    with pytest.MonkeyPatch.context() as mp:
        for raw, want in (("9", 5.0), ("5.5", 5.0), ("junk", 5.0), ("inf", 5.0), ("-2", 0.0), ("3", 3.0)):
            mp.setenv("MIRROR_CATCHUP_MAX_CENTS", raw)
            assert rules.capped_env("MIRROR_CATCHUP_MAX_CENTS", 5.0, floor=0.0) == want, raw
        mp.setenv("MIRROR_CATCHUP_MAX_CENTS", "9")
        _band(mp, mx=rules.capped_env("MIRROR_CATCHUP_MAX_CENTS", 5.0, floor=0.0))
        assert rules.MIRROR_CATCHUP_MAX_CENTS == 5.0
        assert _cu(11_000.0, 0.86, 0.10, 10_000.0, 0.80, LONG)["why"] == "flow_only"
        assert _cu(11_000.0, 0.849, 0.10, 10_000.0, 0.80, LONG)["why"] == "within_pct"


def test_d1_the_short_mirror_on_the_worse_side_reads_the_band_under_his_cost():
    # a short's worse side is UNDER his cost; a 0.50 cost has a 5c band
    assert _admitted(_cu(-21_000.0, 0.451, 0.10, -20_000.0, 0.50, SHORT)) == (True, 0.0, "within_pct")
    assert _cu(-21_000.0, 0.45, 0.10, -20_000.0, 0.50, SHORT)["why"] == "within_pct"
    assert _admitted(_cu(-21_000.0, 0.449, 0.10, -20_000.0, 0.50, SHORT)) == (False, -20_000.0, "flow_only")
    assert _cu(-21_000.0, 0.48, 0.10, -20_000.0, 0.50, SHORT)["why"] == "within_tol", "the floor keeps its name"
    assert _cu(-21_000.0, 0.479, 0.10, -20_000.0, 0.50, SHORT)["why"] == "within_pct"
    # capped on a 0.80 cost: 4.9c under within, 5.1c under flow only
    assert _cu(-11_000.0, 0.751, 0.10, -10_000.0, 0.80, SHORT)["why"] == "within_pct"
    assert _cu(-11_000.0, 0.749, 0.10, -10_000.0, 0.80, SHORT)["why"] == "flow_only"
    # the floor on a 0.20 cost: 2c under within_tol, 2.1c flow only
    assert _cu(-11_000.0, 0.18, 0.10, -10_000.0, 0.20, SHORT)["why"] == "within_tol"
    assert _cu(-11_000.0, 0.179, 0.10, -10_000.0, 0.20, SHORT)["why"] == "flow_only"
    # the same numbers on the LONG axis are the better side: the axis decides, not the distance
    assert _cu(21_000.0, 0.451, 0.10, 20_000.0, 0.50, LONG)["why"] == "at_or_better"
    assert _cu(21_000.0, 0.449, 0.10, 20_000.0, 0.50, LONG)["why"] == "at_or_better"
    # and a short's mark OVER his cost by 6c is the better side, not the band
    assert _cu(-21_000.0, 0.56, 0.10, -20_000.0, 0.50, SHORT)["why"] == "at_or_better"


def test_d1_the_at_or_better_side_is_unaffected_by_the_band_and_its_knobs():
    with pytest.MonkeyPatch.context() as mp:
        for pct, mx in ((0.10, 5.0), (0.0, 5.0), (0.10, 0.0), (0.0, 0.0), (0.05, 1.0)):
            _band(mp, pct=pct, mx=mx)
            assert _cu(21_000.0, 0.40, 0.10, 20_000.0, 0.50, LONG)["why"] == "at_or_better", (pct, mx)
            assert _cu(21_000.0, 0.50, 0.10, 20_000.0, 0.50, LONG)["why"] == "at_or_better", (pct, mx)
            assert _cu(-21_000.0, 0.60, 0.10, -20_000.0, 0.50, SHORT)["why"] == "at_or_better", (pct, mx)
            assert _cu(19_400.0, 0.50, 0.10, 19_400.0, 0.549, LONG)["why"] == "at_or_better", (pct, mx)


def test_d1_a_nan_vwap_is_vwap_unread_the_band_is_never_read_on_nothing():
    for axis in (LONG, SHORT):
        assert _admitted(_cu(21_000.0, 0.52, 0.10, 20_000.0, float("nan"), axis)) == (False, 20_000.0, "vwap_unread")
        assert _cu(21_000.0, 0.52, 0.10, 20_000.0, None, axis)["why"] == "vwap_unread"
        assert _cu(21_000.0, 0.52, 0.10, 20_000.0, "0.50", axis)["why"] == "vwap_unread"
        assert _cu(21_000.0, 0.52, 0.10, 20_000.0, float("inf"), axis)["why"] == "vwap_unread"
    # and a NaN mark on the worse side of a readable cost is mark_unread, never within_pct
    assert _cu(21_000.0, float("nan"), 0.10, 20_000.0, 0.50, LONG)["why"] == "mark_unread"


def test_d1_tol_lowered_to_zero_closes_the_band_too_never_catch_up_means_never():
    with pytest.MonkeyPatch.context() as mp:
        _band(mp, tol=0.0)
        # tol_zero is judged ahead of the band: 4.9c over on a 0.50 cost is NOT within_pct
        assert _admitted(_cu(21_000.0, 0.549, 0.10, 20_000.0, 0.50, LONG)) == (False, 20_000.0, "tol_zero")
        assert _cu(21_000.0, 0.501, 0.10, 20_000.0, 0.50, LONG)["why"] == "tol_zero"
        assert _cu(-21_000.0, 0.451, 0.10, -20_000.0, 0.50, SHORT)["why"] == "tol_zero"
        # the better side still stands
        assert _cu(21_000.0, 0.499, 0.10, 20_000.0, 0.50, LONG)["why"] == "at_or_better"
        # tol lowered to 1: the band IS 1c on every cost (re-pinned at the review fold, 2026-09-08,
        # MEDIUM-2: before D1 the env's TOL was the worse side's only handle and 1 meant 1c; a rail
        # the operator lowered is never widened to max(1c, 10%) by the deploy -- env may only lower)
        _band(mp, tol=1.0)
        assert _cu(21_000.0, 0.51, 0.10, 20_000.0, 0.50, LONG)["why"] == "within_tol"
        assert _cu(21_000.0, 0.511, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only"
        assert _cu(21_000.0, 0.549, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only"
        assert _cu(21_000.0, 0.551, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only"
        assert _cu(-21_000.0, 0.49, 0.10, -20_000.0, 0.50, SHORT)["why"] == "within_tol"
        assert _cu(-21_000.0, 0.489, 0.10, -20_000.0, 0.50, SHORT)["why"] == "flow_only"
        # and on a 0.20 cost the same 1c: never max(1c, 2c); the pct is not read under a lowered tol
        assert _cu(11_000.0, 0.21, 0.10, 10_000.0, 0.20, LONG)["why"] == "within_tol"
        assert _cu(11_000.0, 0.211, 0.10, 10_000.0, 0.20, LONG)["why"] == "flow_only"
        assert _cu(11_000.0, 0.22, 0.10, 10_000.0, 0.20, LONG)["why"] == "flow_only"
        assert rules._catchup_band_cents(1.0, 0.50) == 1.0 and rules._catchup_band_cents(1.9, 0.50) == 1.9
        # the better side still stands under a lowered tol
        assert _cu(21_000.0, 0.45, 0.10, 20_000.0, 0.50, LONG)["why"] == "at_or_better"
        # at the default (2.0, never raised by the env) the band is D1's: 4.9c over a 0.50 cost admitted
        _band(mp, tol=2.0)
        assert _cu(21_000.0, 0.549, 0.10, 20_000.0, 0.50, LONG)["why"] == "within_pct"
        assert rules._CATCHUP_TOL_DEFAULT_CENTS == 2.0


def test_d1_the_five_argument_call_keeps_e12s_flat_2c_the_band_needs_the_axis():
    # E12's call has no axis and no worse side: 4.9c over on a 0.50 cost stays flow_only
    assert rules.open_catchup(21_000.0, 0.549, 0.10, 20_000.0, 0.50)["why"] == "flow_only"
    assert rules.open_catchup(21_000.0, 0.521, 0.10, 20_000.0, 0.50)["why"] == "flow_only"
    assert rules.open_catchup(21_000.0, 0.52, 0.10, 20_000.0, 0.50)["why"] == "within_tol"
    assert rules.open_catchup(-21_000.0, 0.451, 0.10, -20_000.0, 0.50)["why"] == "flow_only"
    # the E12 review pin re-pinned at 6c: 3c over a 0.61 cost is within_pct WITH the axis only
    assert rules.open_catchup(10_000.0, 0.64, 0.10, 10_000.0, 0.61)["why"] == "flow_only"
    assert _cu(10_000.0, 0.64, 0.10, 10_000.0, 0.61, LONG)["why"] == "within_pct"
    assert _cu(10_000.0, 0.67, 0.10, 10_000.0, 0.61, LONG)["why"] == "flow_only"
    # within_pct never appears without the axis
    for m in (0.521, 0.53, 0.549, 0.55):
        assert rules.open_catchup(21_000.0, m, 0.10, 20_000.0, 0.50)["why"] == "flow_only", m


def test_d1_a_knob_that_does_not_read_grants_no_widening():
    with pytest.MonkeyPatch.context() as mp:
        for pct, mx in ((None, 5.0), ("0.1", 5.0), (float("nan"), 5.0), (0.10, None), (0.10, "5"),
                        (0.10, float("nan")), (None, None)):
            _band(mp, pct=pct, mx=mx)
            assert _cu(21_000.0, 0.549, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only", (pct, mx)
            assert _cu(21_000.0, 0.52, 0.10, 20_000.0, 0.50, LONG)["why"] == "within_tol", (pct, mx)
            assert _cu(21_000.0, 0.521, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only", (pct, mx)
            assert _cu(21_000.0, 0.499, 0.10, 20_000.0, 0.50, LONG)["why"] == "at_or_better", (pct, mx)
        # a negative cap (never from capped_env) is a band under zero: nothing on the worse side
        _band(mp, pct=0.10, mx=-1.0)
        assert _cu(21_000.0, 0.501, 0.10, 20_000.0, 0.50, LONG)["why"] == "flow_only"


def test_d1_the_open_admits_a_block_within_the_band_at_the_whole_net_target_and_counts_it_bought():
    since = NOW - ml.FIRST_SIGHT_S
    fills = [_fill(M, "BUY", 20_000, 0.50, NOW - 9000)]
    assert mi.vwap_of(fills, M, N, before=since) == 0.50
    # 4.9c over on a 0.50 cost: within_pct, the whole-net target (ratio x his net, the game cap the caller's)
    cu, target = ml._open_flow(_t(), fills, M, N, False, 20_000.0, 0.549, 0.10, 2_000, 2_500.0, True)
    assert (cu["why"], cu["allowed"], cu["flow_base"], target) == ("within_pct", True, 0.0, 2_000)
    # 5.1c over: flow only at 0
    cu2, target2 = ml._open_flow(_t(), fills, M, N, False, 20_000.0, 0.551, 0.10, 2_000, 2_500.0, True)
    assert (cu2["why"], cu2["allowed"], cu2["flow_base"], target2) == ("flow_only", False, 20_000.0, 0)
    # the short's mirror through the worker: his other-token BUY at 0.50 is 0.50 on the axis
    sfills = [_fill(N, "BUY", 20_000, 0.50, NOW - 9000)]
    assert mi.vwap_of(sfills, M, N, short=True, before=since) == 0.50
    cu3, target3 = ml._open_flow(_t(), sfills, M, N, True, -20_000.0, 0.451, 0.10, -2_000, 2_500.0, True)
    assert (cu3["why"], cu3["flow_base"], target3) == ("within_pct", 0.0, -2_000)
    cu4, target4 = ml._open_flow(_t(), sfills, M, N, True, -20_000.0, 0.449, 0.10, -2_000, 2_500.0, True)
    assert (cu4["why"], cu4["flow_base"], target4) == ("flow_only", -20_000.0, 0)
    # the census: a within_pct open is a block bought (`open_catchup`), no new census name
    csrc = inspect.getsource(ml._tick_candidate)
    assert '"within_pct")' in csrc and csrc.index('"within_pct")') < csrc.index('_mirror_stop("open_catchup", w)')
    assert "within_pct" not in ml.CENSUS_KEYS


def test_d1_the_docs_name_the_decision_the_knobs_and_the_verdict():
    import pathlib
    doc = (pathlib.Path(ml.__file__).resolve().parents[3] / "docs" / "mirror-coverage.md").read_text()
    assert "## 35. PNL lane 1" in doc
    for s in ("D1 = YES", "MIRROR_CATCHUP_PCT", "MIRROR_CATCHUP_MAX_CENTS", "`within_pct`",
              "min(MIRROR_CATCHUP_MAX_CENTS, max(MIRROR_CATCHUP_TOL_CENTS, MIRROR_CATCHUP_PCT"):
        assert s in doc, s
