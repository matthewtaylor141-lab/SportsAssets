"""E12b review pins (2026-09-08): the ratchet's witness (a reducing fill
of his clocked after the reference), the hold, the clock, the VWAP on
both tokens -- attacked at their edges on the real planner through the
worker file's fakes, as the E12 / E12b pins are. Two pins are FINDINGS
and say so in their docstrings; they pin the tree's behaviour so the
fix is visible when it lands.
"""
import inspect

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e12_flow_only import _flow_book, _sent
from tests.test_e12b_witness import OLD, _collapse, _collapse_world
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    BUY, CID, M, N, NOW, SELL, SLUG, _armed, _cancels, _census, _fill, _mkt, _places, _pool,
    _rails_2026_09_06, _tick, _Venue,
)

IOC = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
GTC = "TIME_IN_FORCE_GOOD_TILL_CANCEL"


# ---------------------------------------------------------- Q1: the witness

def test_r1_the_witness_boundary_equal_clock_no_ingest_clock_and_mixed_clocks():
    """A reducing fill clocked EXACTLY at the reference is no witness
    (strictly after: the reference counted every fill at or before its
    clock). A reducing fill with no ingest clock witnesses by its stamp.
    MIXED CLOCKS (LOW): the reference's clock is an INGEST stamp (his
    add's detected_at, the venue time + the lag) while a stamp-only
    SELL is compared by its VENUE time -- a SELL made after his add but
    inside the lag is missed until a later fill of his moves the clock;
    real rows always carry detected_at (pipeline.py stamps every
    insert), so this is the NULL-column case only."""
    ref = NOW - 100.0
    at_ref = _fill(M, "SELL", 1_000, 0.70, NOW - 500, detected_at=ref)
    after = _fill(M, "SELL", 1_000, 0.70, NOW - 500, detected_at=ref + 1e-3)
    assert mi.reducing_since([at_ref], M, N, 10_000.0, ref) == 0.0
    assert mi.reducing_since([after], M, N, 10_000.0, ref) == 1_000.0
    stamp_only = _fill(M, "SELL", 1_000, 0.70, ref + 5)               # no detected_at: the stamp decides
    assert mi.reducing_since([stamp_only], M, N, 10_000.0, ref) == 1_000.0
    # his add at venue time 100 ingested at 102 (the reference's clock); his SELL at venue time 101, stamp only
    add = _fill(M, "BUY", 1_000, 0.71, 100.0, detected_at=102.0)
    assert mi.fills_clock([add]) == 102.0
    late_sell = _fill(M, "SELL", 1_000, 0.70, 101.0)
    assert mi.reducing_since([add, late_sell], M, N, 10_000.0, 102.0) == 0.0, "mixed clocks: missed"
    assert mi.reducing_since([add, _fill(M, "SELL", 1_000, 0.70, 101.0, detected_at=103.0)], M, N, 10_000.0, 102.0) == 1_000.0
    # the short book's mirror image, and nothing on the wrong axis
    assert mi.reducing_since([_fill(M, "BUY", 400, 0.30, NOW, detected_at=NOW)], M, N, -10_000.0, ref) == 400.0
    assert mi.reducing_since([_fill(N, "SELL", 400, 0.70, NOW, detected_at=NOW)], M, N, -10_000.0, ref) == 400.0
    assert mi.reducing_since([_fill(N, "BUY", 400, 0.70, NOW, detected_at=NOW)], M, N, -10_000.0, ref) == 0.0
    assert mi.reducing_since([_fill(N, "SELL", 400, 0.70, NOW, detected_at=NOW)], M, N, 10_000.0, ref) == 0.0


def test_r2_a_sale_and_a_readd_in_one_tick_apply_the_fall_and_under_mirror_the_readd(monkeypatch):
    """He sells 3,000 and re-buys 2,000 inside one tick (net 11,000 ->
    10,000; witnessed 3,000 > the fall 1,000): the fall is what is
    applied -- block 9,090.9, target 90, our 10 sold (his sale was 27 %
    of his net; ours 10 % of ours: never more than his proportion). Read
    over two ticks the same fills give block 7,272.7 then flow 2,727 ->
    target 272: the one-tick path holds 182 fewer -- D25's path
    dependence, fail-closed toward not buying, stated."""
    one = mi.witnessed_ratchet(10_000.0, 11_000.0, 10_000.0, 3_000.0)
    assert one == (round(10_000 * 10 / 11, 6), None)
    two_a, _ = mi.witnessed_ratchet(10_000.0, 11_000.0, 8_000.0, 3_000.0)
    two_b, _ = mi.witnessed_ratchet(two_a, 8_000.0, 10_000.0, 0.0)
    assert (two_a, two_b) == (round(10_000 * 8 / 11, 6), round(10_000 * 8 / 11, 6))
    assert rules.mirror_target(0.10, mi.flow_net(10_000.0, one[0]), 0.72, 2500.0, cap_usd=2500.0)["target"] == 90
    assert rules.mirror_target(0.10, mi.flow_net(10_000.0, two_b), 0.72, 2500.0, cap_usd=2500.0)["target"] == 272
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000),
             _fill(M, "SELL", 3_000, 0.70, NOW - 100, detected_at=NOW - 95),
             _fill(M, "BUY", 2_000, 0.72, NOW - 90, detected_at=NOW - 85)]
    p = _pool(fills=fills, snap={M: 10_000.0, N: 0.0})
    b = _flow_book(p)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=10.0)
    _tick(p, v, http=_mkt(10_000.0))
    assert (b["flow_base"], b["flow_last_net"], b["target"]) == (round(10_000 * 10 / 11, 6), 10_000.0, 90)
    assert _places(v)[0][2:6] == (0.69, 10, True, IOC) and "flow_fills_shrank" not in b["last_plan"]


def test_r3_finding_an_inflated_reducing_leg_corrected_by_its_chain_row_reads_the_correction_as_flow_and_buys(monkeypatch):
    """WAS THE FINDING (HIGH-1; shared with the landed rule). The poll
    lane's SELL leg reads 2,750 (a duplicated match) and witnesses a
    25 % ratchet: block 7,500, our 25 sold. Its chain row lands at the
    true 2,000 and the leg collapses: the fills' net RISES 8,250 ->
    9,000 with no adding fill of his; D25 left the block at 7,500, the
    flow read 1,500 and the book BOUGHT 75 at his cent (69 of the
    block). THE FOLD: the mirror image of the witness (mi.adding_since /
    mi.restored_block) -- the rise no adding fill of his explains goes
    to the BLOCK: 8,250, the reference 9,000 with the chain row's clock,
    `flow_fills_grew` named, no `flow_ratchet`, target 75 = held,
    nothing bought. The true path (a 2,000 sale witnessed once) is block
    8,181.8, target 81: the additive restore holds 6 fewer, fail-closed
    (a pro-rata undo would BUY those 6 on the correction). The name is
    the review's; the pin is the fix."""
    _rails_2026_09_06(monkeypatch)
    block, add = _fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000)
    leg = _fill(M, "SELL", 2_750, 0.70, NOW - 100, detected_at=NOW - 95, source="poll")
    p = _pool(fills=[block, add, leg], snap={M: 8_250.0, N: 0.0})
    b = _flow_book(p)
    v1 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    _tick(p, v1, http=_mkt(8_250.0))
    assert (b["flow_base"], b["flow_last_net"], b["ledger_net"]) == (7_500.0, 8_250.0, 75)
    # the chain row: the same tx's true size, the poll leg collapsed away
    p.fills[:] = [block, add, _fill(M, "SELL", 2_000, 0.70, NOW - 100, detected_at=NOW + 20, source="chain")]
    p.snap[M] = 9_000.0
    v2 = _Venue(bid=0.71, ask=0.73, held={SLUG: 75})
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(9_000.0))
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"]) == (8_250.0, 9_000.0, NOW + 20, 75)
    lp = b["last_plan"]
    assert lp["flow_fills_grew"] == {"from": 8_250.0, "to": 9_000.0, "unexplained": 750.0, "witnessed": 0.0}
    assert "flow_ratchet" not in lp and "flow_fills_shrank" not in lp and "flow_hold" not in lp and lp["flow_net"] == 750.0
    assert not _places(v2) and _census(st2, "on_target") == 1 and b["ledger_net"] == 75
    assert _sent(p, "ml-book-flow")[-1][1] == (b["id"], 8_250.0, 9_000.0, NOW + 20)
    true_block = round(10_000 * 9_000 / 11_000, 6)
    assert rules.mirror_target(0.10, mi.flow_net(9_000.0, true_block), 0.72, 2500.0, cap_usd=2500.0)["target"] == 81
    assert rules.mirror_target(0.10, mi.flow_net(9_000.0, 8_250.0), 0.72, 2500.0, cap_usd=2500.0)["target"] == 75


# ------------------------------------------------------------- Q2: the hold

def _rest_world(monkeypatch, block_row):
    """The builder's world: his 2,750 sale already ratcheted (7,500 /
    8,250, clocked at it), our reduce of 25 resting at his 0.70 with
    the bid out of tolerance; `block_row` is the block's row as the
    next read holds it (10,000 = no collapse; 9,500 = a collapse)."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", block_row, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000),
                     _fill(M, "SELL", 2_750, 0.70, NOW - 200, detected_at=NOW - 195)],
              snap={M: float(block_row - 1_750), N: 0.0})
    b = _flow_book(p, ledger=100, flow_base=7_500.0, flow_last_net=8_250.0, flow_last_at=NOW - 195)
    o = p.add_order(b, side=SELL, wire=0.70, qty=25, kind="reduce", order_id="oid-exit")
    return p, b, o


def test_r4_finding_the_sticky_hold_stalls_the_take_of_his_witnessed_exit_while_the_bid_is_within_tolerance(monkeypatch):
    """WAS THE FINDING (CRITICAL-1). CONTROL: no collapse, the bid
    comes to 0.69 (within MIRROR_EXIT_TOL of his 0.70): the resting
    reduce is taken IOC at once -- E4's exit. UNDER THE HOLD the block's
    row re-reads 500 smaller (an unwitnessed fall) and the same bid:
    `flow_hold` = "rest" kept the rest but skipped `_act`, so the take
    never fired for as long as he did not sell again. THE FOLD: the
    hold suppresses NEW reduces only -- the standing reduce keeps its
    whole E4 life through `_act`, capped at its qty: the take fires on
    the first tick (25 IOC at 0.69, never the plan's 75), the hold and
    the shrink still named; the ticks after, with no rest standing and
    the fall still unwitnessed, place nothing. The name is the review's;
    the pin is the fix."""
    p, b, o = _rest_world(monkeypatch, 10_000)
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    v.rest("oid-exit", side="SELL", price=0.70, qty=25)
    st = _tick(p, v, http=_mkt(8_250.0))
    assert _census(st, "exit_take") == 1 and b["ledger_net"] == 75 and "flow_hold" not in b["last_plan"]
    assert [c[:2] for c in _cancels(v)] == [("cancel", "oid-exit")] and _places(v)[0][2:6] == (0.69, 25, True, IOC)
    # the hold: the take fires on the first tick, capped at the rest's 25
    p2, b2, o2 = _rest_world(monkeypatch, 9_500)
    v2 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    v2.rest("oid-exit", side="SELL", price=0.70, qty=25)
    st2 = _tick(p2, v2, http=_mkt(7_750.0))
    lp = b2["last_plan"]
    assert _census(st2, "exit_take") == 1 and _places(v2)[0][2:6] == (0.69, 25, True, IOC)
    assert [c[:2] for c in _cancels(v2)] == [("cancel", "oid-exit")] and b2["ledger_net"] == 75
    assert (lp["flow_hold"]["kind"], lp["flow_hold"]["qty"], lp["flow_hold"]["cap"]) == ("reduce", 75, 25)
    assert lp["open_order"] == o2["id"] and (lp["side"], lp["qty"]) == (SELL, 25) and b2["flow_base"] == 7_500.0
    assert lp["flow_fills_shrank"] == {"from": 8_250.0, "to": 7_750.0, "unexplained": 500.0, "witnessed": 0.0}
    # the ticks after: nothing rests, the fall is still unwitnessed -- the reduce of 50 is held, nothing placed
    for i in (1, 2):
        v3 = _Venue(bid=0.69, ask=0.71, held={SLUG: 75}, ioc_fill=50.0)
        st3 = _tick(p2, v3, now=NOW + 30 * i, http=_mkt(7_750.0))
        lp3 = b2["last_plan"]
        assert _census(st3, "exit_take") == 0 and not _places(v3) and not _cancels(v3), i
        assert (lp3["flow_hold"]["kind"], lp3["flow_hold"]["qty"]) == ("reduce", 50) and "cap" not in lp3["flow_hold"], i
        assert b2["last_reason"] == "flow_fills_shrank" and b2["ledger_net"] == 75 and b2["flow_base"] == 7_500.0, i
    assert abs(0.69 - 0.70) <= float(rules.MIRROR_EXIT_TOL) + 1e-9
    # by source: the hold's standing exit goes through _act with its capped plan; no branch skips it
    src = inspect.getsource(ml._tick_book)
    assert 'elif flow_hold == "rest":' not in src and 'flow_hold = "rest"' in src
    assert 'plan["flow_hold"]["cap"] = wp.qty' in src and 'wp = mi.plan(int(ref_tg["target"]), float(ledger)' in src
    assert "reason = await _act(t, book, r, p, kind, his_px, plan, cancel_reason) or reason" in src


def test_r5_two_ticks_of_collapses_then_his_sale_apply_the_witnessed_share_and_name_the_whole_remainder(monkeypatch):
    p, b = _collapse_world(monkeypatch)
    _collapse(p)
    _tick(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164}), now=NOW + 30, http=_mkt(111_000.0))
    assert b["last_plan"]["flow_fills_shrank"]["unexplained"] == 640.0 and b["flow_last_net"] == 111_640.0
    # a second collapse: the same tx's chain row re-read 500 smaller still (another legs' mismatch)
    p.fills[:] = [OLD, _fill(M, "BUY", 10_500, 0.71, NOW - 100, source="chain")]
    p.snap[M] = 110_500.0
    _tick(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 1_164}), now=NOW + 60, http=_mkt(110_500.0))
    assert b["last_plan"]["flow_fills_shrank"] == {"from": 111_640.0, "to": 110_500.0, "unexplained": 1_140.0, "witnessed": 0.0}
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"]) == (100_000.0, 111_640.0, NOW - 99) and not _sent(p, "ml-book-flow")
    # his sale: the witnessed 2,750 applied against the standing reference, the whole 1,140 named
    p.fills[:] = [OLD, _fill(M, "BUY", 10_500, 0.71, NOW - 100, source="chain"),
                  _fill(M, "SELL", 2_750, 0.70, NOW + 80, detected_at=NOW + 85)]
    p.snap[M] = 107_750.0
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 1_164}, ioc_fill=200.0)
    _tick(p, v, now=NOW + 90, http=_mkt(107_750.0))
    fb = round(100_000 * (111_640 - 2_750) / 111_640, 6)
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"]) == (fb, 107_750.0, NOW + 85)
    assert b["last_plan"]["flow_fills_shrank"] == {"from": 111_640.0, "to": 107_750.0, "unexplained": 1_140.0, "witnessed": 2_750.0}
    assert b["target"] == int(rules.mirror_target(0.10, 107_750.0 - fb, 0.70, 2500.0, cap_usd=2500.0)["target"])
    assert _places(v)[0][2:6] == (0.69, 1_164 - b["target"], True, IOC)


def test_r6_the_residual_block_after_a_part_witnessed_crossing_under_mirrors_his_rebuy():
    """The collapse's 640 and his full exit in one tick: net_t' = 640,
    block 573.3, fills' net 0 -> flow 0, our full exit. He re-buys
    5,000: the reference is 0, the block stays 573.3 (unchanged on
    increases), flow 4,426.7 -> target 442 where a clean block gives
    500: 58 shares under-mirrored, fail-closed toward not buying."""
    fb, shrank = mi.witnessed_ratchet(100_000.0, 111_640.0, 0.0, 111_000.0)
    assert fb == round(100_000 * 640 / 111_640, 6) and shrank["unexplained"] == 640.0
    assert mi.flow_net(0.0, fb) == 0.0
    later, none = mi.witnessed_ratchet(fb, 0.0, 5_000.0, 0.0)
    assert (later, none) == (fb, None)
    assert rules.mirror_target(0.10, mi.flow_net(5_000.0, later), 0.72, 2500.0, cap_usd=2500.0)["target"] == 442
    assert rules.mirror_target(0.10, 5_000.0, 0.72, 2500.0, cap_usd=2500.0)["target"] == 500


# ------------------------------------------------------------ Q3: the money

def test_r7_the_money_on_the_sale_plus_collapse_harness():
    """The reduce to the corrected target sells 93 = his 2.46 % of our
    1,164 (28.7) plus the 64 phantom the legs' mismatch had us hold; the
    alternative (the witnessed share only) sells 29 and keeps the 64.
    Difference: 64 shares at his 0.69 = $44.16 realised now against
    $45.44 of cost -- $1.28 of realised loss on the phantom -- versus
    $44.16 of non-proportional exposure held to his next sale or the
    settlement (0 or 1: -$45.44 or +$18.56)."""
    fb = round(100_000 * (111_640 - 2_750) / 111_640, 6)
    target = rules.mirror_target(0.10, 108_250.0 - fb, 0.70, 2500.0, cap_usd=2500.0)["target"]
    assert target == 1_071 and 1_164 - target == 93
    witnessed_share = round(1_164 * 2_750 / 111_640, 1)
    assert witnessed_share == 28.7 and 93 - 29 == 64
    assert round(64 * 0.69, 2) == 44.16 and round(64 * 0.71, 2) == 45.44 and round(64 * (0.71 - 0.69), 2) == 1.28
    assert round(64 * (1.0 - 0.71), 2) == 18.56


# ------------------------------------------------------------- Q4: the clock

def test_r8_the_reference_clock_reads_a_stamp_only_newest_fill_and_the_row_carries_it_across_a_restart(monkeypatch):
    """fills_clock: a newest fill with no ingest clock is read by its
    stamp; the tick's clock only when nothing reads. A row read back
    with its clock (a fresh process) witnesses his sale against it."""
    assert mi.fills_clock([_fill(M, "BUY", 1, 0.5, 100.0, detected_at=105.0), _fill(M, "BUY", 1, 0.5, 110.0)]) == 110.0
    assert mi.fills_clock([{"asset": M, "side": "BUY", "size": 1}], 42.0) == 42.0 and mi.fills_clock([]) is None
    assert mi.fills_clock([_fill(M, "BUY", 1, 0.5, 100.0, detected_at=105.0)], 999.0) == 105.0
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000, detected_at=NOW - 2995),
             _fill(M, "SELL", 2_750, 0.70, NOW - 100, detected_at=NOW - 95)]
    p = _pool(fills=fills, snap={M: 8_250.0, N: 0.0})
    b = _flow_book(p, flow_last_at=NOW - 2995)          # the row as a restarted process reads it
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=25.0)
    _tick(p, v, http=_mkt(8_250.0))
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["ledger_net"]) == (7_500.0, 8_250.0, NOW - 95, 75)
    assert _sent(p, "ml-book-flow")[0][1] == (b["id"], 7_500.0, 8_250.0, NOW - 95)


# --------------------------------------------------------------- Q5: the vwap

def test_r9_the_vwap_on_both_tokens_at_the_edges():
    """A long built by BUYing Yes and SELLing No reads both at their
    long-axis price; a reducing fill never enters; a short's mirror
    image; the block's fills only."""
    since = NOW - 60
    fills = [_fill(M, "BUY", 5_000, 0.29, NOW - 9000), _fill(N, "SELL", 5_000, 0.60, NOW - 8000),
             _fill(M, "SELL", 1_000, 0.90, NOW - 7000), _fill(N, "BUY", 1_000, 0.05, NOW - 6000),
             _fill(M, "BUY", 1_000, 0.71, NOW - 10)]
    assert mi.vwap_of(fills, M, N, before=since) == round((5_000 * 0.29 + 5_000 * 0.40) / 10_000, 6) == 0.345
    assert mi.vwap_of(fills, M, N) == round((5_000 * 0.29 + 5_000 * 0.40 + 1_000 * 0.71) / 11_000, 6)
    short_fills = [_fill(N, "BUY", 4_000, 0.72, NOW - 9000), _fill(M, "SELL", 4_000, 0.30, NOW - 8000),
                   _fill(N, "SELL", 500, 0.80, NOW - 7000), _fill(M, "BUY", 500, 0.25, NOW - 6000)]
    assert mi.vwap_of(short_fills, M, N, short=True, before=since) == round((4_000 * 0.28 + 4_000 * 0.30) / 8_000, 6)
    # the same fills read on a LONG book: his two reducing fills of the short are ADDS on the long axis
    assert mi.vwap_of(short_fills, M, N, before=since) == round((500 * 0.20 + 500 * 0.25) / 1_000, 6) == 0.225
    assert mi.vwap_of([_fill(N, "SELL", 5_000, 0.60, NOW - 8000)], M, N, before=since) == 0.40
    # the catch-up on the mixed build, pure: admitted at 0.36, flow-only at 0.38
    assert rules.open_catchup(10_000.0, 0.36, 0.10, 10_000.0, 0.345)["why"] == "within_tol"
    assert rules.open_catchup(10_000.0, 0.38, 0.10, 10_000.0, 0.345)["why"] == "flow_only"
    # a block of other-token SELLs alone never nets long: net_positions floors each token at 0
    pos = mi.net_positions([_fill(N, "SELL", 5_000, 0.60, NOW - 8000)])
    assert pos.get(M, 0.0) == 0.0 and pos.get(N, 0.0) == 0.0 and mi.his_net(pos.get(M, 0.0), pos.get(N, 0.0)) == 0.0


# ------------------------------------------------------ the E12b fold re-review
#
# (2026-09-08; CRITICAL-1 / HIGH-1 as folded, the two widenings attacked.
# No xfail: each pin states the tree's behaviour; the one finding says so.)

def test_f1_finding_a_backfilled_or_reconciled_adding_row_of_his_history_is_read_as_flow_and_bought(monkeypatch):
    """WAS THE FINDING (MEDIUM-1 of the fold re-review). `adding_since`
    read the ingest clock alone: a BUY row stamped 2 h 13 min ago that
    the backfill (or the S1 reconciler) inserts now carried detected_at
    after the reference's clock, so the rise it made was "explained" and
    the book BOUGHT 200 at his current cent -- his history, bought late,
    by the road E12's is_flow closes at the open. THE FOLD: is_flow's
    second clock on adds (the one constant LATE_FILL_S, 900 s) -- the
    row is not an add he made since the reference: the rise is
    unexplained, the block restored to 12,000 (the reference 13,000, the
    clock the row's), `flow_fills_grew` named, target 100 = held,
    nothing bought. The name is the review's; the pin is the fix."""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000, detected_at=NOW - 2995),
             _fill(M, "BUY", 2_000, 0.33, NOW - 8000, detected_at=NOW - 5, source="backfill")]
    p = _pool(fills=fills, snap={M: 13_000.0, N: 0.0})
    b = _flow_book(p, flow_last_at=NOW - 2995)
    v = _Venue(bid=0.71, ask=0.73, held={SLUG: 100})
    st = _tick(p, v, http=_mkt(13_000.0))
    assert (b["flow_base"], b["flow_last_net"], b["flow_last_at"], b["target"]) == (12_000.0, 13_000.0, NOW - 5, 100)
    lp = b["last_plan"]
    assert lp["flow_fills_grew"] == {"from": 11_000.0, "to": 13_000.0, "unexplained": 2_000.0, "witnessed": 0.0}
    assert not _places(v) and _census(st, "on_target") == 1 and "flow_ratchet" not in lp and lp["flow_net"] == 1_000.0
    assert _sent(p, "ml-book-flow")[0][1] == (b["id"], 12_000.0, 13_000.0, NOW - 5)
    assert mi.adding_since(fills, M, N, 10_000.0, NOW - 2995) == 0.0, "his history, late-known: not an add since the reference"
    assert not mi.is_flow(fills[2], NOW - 60), "at the open the same row is the block"


def test_f2_a_legacy_null_clock_add_with_an_old_stamp_is_absorbed_into_the_block_and_never_followed():
    """LOW. A real add of his whose row has no detected_at and a stamp at
    or before the reference's clock is not an add `adding_since` sees:
    the rise goes to the block whole and stays there (the reference
    moves to the fills' net, nothing restores the flow later). Every
    live insert stamps detected_at, so this is the legacy-NULL case."""
    ref = NOW - 100.0
    old_add = _fill(M, "BUY", 2_000, 0.71, ref - 5)                  # stamp only, at or before the clock
    assert mi.adding_since([old_add], M, N, 10_000.0, ref) == 0.0
    fb, grew = mi.restored_block(10_000.0, 11_000.0, 13_000.0, 0.0)
    assert (fb, grew) == (12_000.0, {"from": 11_000.0, "to": 13_000.0, "unexplained": 2_000.0, "witnessed": 0.0})
    assert mi.flow_net(13_000.0, fb) == 1_000.0
    later, none = mi.restored_block(fb, 13_000.0, 13_000.0, 0.0)
    assert (later, none) == (12_000.0, None)
    recent_add = _fill(M, "BUY", 2_000, 0.71, ref + 5)                # stamp only, after the clock: counted
    assert mi.adding_since([recent_add], M, N, 10_000.0, ref) == 2_000.0


def test_f3_the_holds_cap_is_the_outstanding_of_the_last_witnessed_state_after_a_partial_fill(monkeypatch):
    """His 25 % sale witnessed, our IOC for 25 fills 10 (ledger 90); the
    next tick a collapse lands: the hold's reduce is the 15 still
    outstanding from the reference's target of 75 -- never the
    collapse's, never more than the witnessed share -- rested at his
    cent with the bid out of tolerance."""
    _rails_2026_09_06(monkeypatch)
    block, add = _fill(M, "BUY", 10_000, 0.29, NOW - 9000), _fill(M, "BUY", 1_000, 0.71, NOW - 3000)
    sale = _fill(M, "SELL", 2_750, 0.70, NOW - 100, detected_at=NOW - 95)
    p = _pool(fills=[block, add, sale], snap={M: 8_250.0, N: 0.0})
    b = _flow_book(p)
    v1 = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=10.0)
    _tick(p, v1, http=_mkt(8_250.0))
    assert (b["flow_base"], b["flow_last_net"], b["ledger_net"], b["target"]) == (7_500.0, 8_250.0, 90, 75)
    p.fills[:] = [_fill(M, "BUY", 9_500, 0.29, NOW - 9000), add, sale]     # the block's row 500 smaller
    p.snap[M] = 7_750.0
    v2 = _Venue(bid=0.68, ask=0.71, held={SLUG: 90})
    _tick(p, v2, now=NOW + 30, http=_mkt(7_750.0))
    lp = b["last_plan"]
    assert (b["flow_base"], b["flow_last_net"], b["ledger_net"]) == (7_500.0, 8_250.0, 90)
    assert lp["flow_fills_shrank"]["witnessed"] == 0.0 and lp["flow_hold"]["cap"] == 15 and (lp["side"], lp["qty"]) == (SELL, 15)
    assert _places(v2)[0][2:6] == (0.70, 15, True, GTC)


def test_f4_a_witnessed_full_exit_under_a_hold_is_the_full_exit(monkeypatch):
    """A collapse holds the book (the reference standing); his SELL of
    everything then lands with the data API confirming him gone: the
    witnessed share is his whole net, the fall applied, our 100 taken."""
    from tests.test_mirror_live_worker import _gone
    _rails_2026_09_06(monkeypatch)
    add = _fill(M, "BUY", 1_000, 0.71, NOW - 3000)
    p = _pool(fills=[_fill(M, "BUY", 9_500, 0.29, NOW - 9000), add], snap={M: 10_500.0, N: 0.0})
    b = _flow_book(p)
    _tick(p, _Venue(bid=0.71, ask=0.73, held={SLUG: 100}), http=_mkt(10_500.0))
    assert b["last_reason"] == "flow_fills_shrank" and (b["flow_base"], b["flow_last_net"]) == (10_000.0, 11_000.0)
    p.fills[:] = [_fill(M, "BUY", 9_500, 0.29, NOW - 9000), add, _fill(M, "SELL", 10_500, 0.70, NOW + 10, detected_at=NOW + 15)]
    p.snap[M] = 0.0
    v = _Venue(bid=0.69, ask=0.71, held={SLUG: 100}, ioc_fill=100.0)
    st = _tick(p, v, now=NOW + 30, http=_gone())
    assert b["ledger_net"] == 0 and b["target"] == 0 and _census(st, "exit_take") == 1
    assert _places(v)[0][2:6] == (0.69, 100, True, IOC) and b["last_plan"]["kind"] == "flatten_vanished"


def test_f5_the_references_target_moves_with_the_mark_on_a_capped_book_the_caps_own_rule():
    """LOW, stated: the reference's target is read at THIS tick's mark,
    so on a book at the per-event cap a mark that rose since the
    reference lowers the share target and the hold would let that
    cap-driven reduce through as if witnessed -- the cap's own rule on
    any tick (E1), not the hold's; below the cap the target is the
    flow's and does not move with the mark."""
    flow = 30_000.0
    at_80 = rules.mirror_target(0.10, flow, 0.80, 2500.0, cap_usd=2500.0)["target"]
    at_90 = rules.mirror_target(0.10, flow, 0.90, 2500.0, cap_usd=2500.0)["target"]
    assert at_80 == 3_000 and at_90 == 2_777 and at_80 - at_90 == 223
    assert rules.mirror_target(0.10, 1_000.0, 0.80, 2500.0, cap_usd=2500.0)["target"] == 100 == \
        rules.mirror_target(0.10, 1_000.0, 0.90, 2500.0, cap_usd=2500.0)["target"]
