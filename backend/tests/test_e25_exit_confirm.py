"""E25 (2026-09-09, FILL lane 25; task 93): an exit sized from a SUDDEN
DROP in our reading of his net is confirmed against his venue position
before it fires -- a drop the venue does not show waits one tick
(`exit_unconfirmed`), a drop it shows fires the same tick
(`exit_confirmed`), a drop that persists past MIRROR_EXIT_CONFIRM_MAX_TICKS
held ticks fires anyway (`exit_confirm_expired`), and a held drop that
reverses in our reading before it fired is counted `exit_flap_averted`.
Entries are untouched: a rise is never judged.

THE ROWS (hard2/book_1177_1740.txt). Book 1177
aec-wta-qinzhe-eleryb-2026-09-08, ORDER_INTENT_BUY_LONG ratio 0.1, opened
16:30:26Z (row 4). The shadow's judged plans (rows 148-153): his_net
8,597.6 at 17:15:05, 8,473.8 at 17:15:57, 3,262.8 at 17:16:34 / 17:17:30 /
17:18:11, 8,473.8 again at 17:19:09 and on every tick until 13,559.8 at
17:34:03 (row 132); the ledger 719 -> 326 -> 847 (rows 148-152). The
orders (rows 22-24): 7238 increase 140 @0.31 cancelled `drift` at
17:14:38 (859 - 719 = 140: the target before the drop was 859); 7250
take SELL_LONG 393 IOC filled 393 @0.2950 at 17:16:29 (719 - 326 = 393:
the reduce the drop sized); 7257 take BUY_LONG 521 IOC filled 521 @0.29
at 17:18:51 (847 - 326 = 521: the re-buy on the recovery). The drop by
the rule's own reference (the plan before): 8,473.8 (17:15:57, row 152)
-> 3,262.8 (17:16:34, row 151) = 5,211.0 shares, 61.5 % of 8,473.8, 37 s
apart -- exactly the one s1 row fills-vs-venue names (fvv_1743 row 10:
s1_rows 1 / 5,211.0). THE FIXTURE below uses the plan before THAT
(17:15:05, 8,597.6, 89 s before) as PLAN_1715: a valid plan 89 s before
the drop, its drop 5,334.8 (62 % of 8,597.6) mixing in the 123.8 real step
of 17:15:05 -> 17:15:57; both readings are sudden, the verdicts are the
same. fills-vs-venue at 17:43Z (fvv_1743 row 10): our reading 15,370.9 =
the venue's mkt_long 46,225.8 - mkt_other 30,854.9 = 15,370.9 to the
share -- the venue's own position record never showed the drop. THE REAL
CUT (rows 102-107, the his_net series recomputed from the trade rows with
the duplicate poll 2,880 dropped): 11,393.3 before 17:04:57, 8,513.3 after
the chain's 2,880 (a 25.3 % step), -1,566.7 after the poller's 4,869 +
5,211 at 17:08:24 (12,960 = 113.8 % of 11,393.3, a crossing) -- the book
asked to reduce 302 and filled 5 (7213 / 7217, rows 14-15) then flattened
297 + 654 (7223 / 7228, rows 17, 19): REAL, and the venue shows it; this
lane never delays it beyond one tick when the venue confirms -- and a
crossing is a SIGN FLIP, whose flatten is never held at all (the flip
close is never guarded: FILL_plan_C section 4; FILL_plan 120).

The worlds are the worker suite's (test_mirror_live_worker) on the E15
harness: a long book at ratio 0.1, the whole-book walk STALE (NOW - 1000,
never the position source) so the per-market read (`_mkt`) is the
venue's word and `WALK` (no per-market answer) is a snapshot that could
not be read; his fills carry the reading.
"""
from __future__ import annotations

import hashlib
import inspect
import pathlib
import re
import types

import pytest

from sportsassets import live_executor as le
from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e9_fast_path import _fast, _skips, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    BUY, CID, M, N, NOW, SELL, SLUG, _armed, _cancels, _census, _fill, _Http, _mkt, _places, _pool,
    _rails_2026_09_06, _short_book, _shorts_on, _tick, _Venue,
)

IOC = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
GTC = "TIME_IN_FORCE_GOOD_TILL_CANCEL"   # E31 (FILL lane 31): the only tif the money path sends
ROOT = pathlib.Path(__file__).resolve().parents[2]
NEW_NAMES = ("exit_unconfirmed", "exit_confirmed", "exit_confirm_expired", "exit_flap_averted")
WALK = _Http(rows=[])                     # no per-market answer: the snapshot cannot be read

# book 1177's figures (the module docstring)
NET_1715 = 8597.6                          # the 17:15:05Z plan's reading (row 153)
NET_DROP = 3262.8                          # the 17:16:34Z reading (row 151)
NET_BACK = 8473.8                          # the 17:19:09Z reading (row 148)
DROP = 5334.8                              # 8,597.6 - 3,262.8
LEDGER_1177 = 719                          # the ledger before the drop (row 153)
TARGET_1715 = 859                          # 7238's increase of 140 over 719
PLAN_1715 = {"kind": "increase", "reason": "drift", "target": TARGET_1715, "at": NOW - 89.0, "net": NET_1715,
             "reduce_ref": {"target": TARGET_1715, "at": NOW - 100.0}}
BUILT = _fill(M, "BUY", NET_1715, 0.31, NOW - 3000)                           # his long as the reading held it
DROP_FILL = _fill(N, "BUY", DROP, 0.70, NOW - 10, detected_at=NOW - 5, source="s1")   # the row that made the drop
BACK_FILL = _fill(N, "BUY", 123.8, 0.70, NOW - 10, detected_at=NOW - 5)      # the reading back at 8,473.8
# the 17:04:57Z cut
NET_1704 = 11393.3
CUT = 12960.0
NET_AFTER_CUT = -1566.7
LEDGER_1704 = 951                          # 654 + 297: what the book flattened (rows 17, 19)
PLAN_1704 = {"kind": "increase", "reason": "on target", "target": 1139, "at": NOW - 33.0, "net": NET_1704,
             "reduce_ref": {"target": 1139, "at": NOW - 100.0}}


def _world(monkeypatch, fills, last_plan, ledger=LEDGER_1177, ratio=0.10):
    """A long book of `ledger` at ratio 0.1 on the stale walk; `fills` are
    the tick's reading of him; `last_plan` the plan before."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=list(fills), snap={M: NET_1715, N: 0.0}, snap_at=NOW - 1000)
    b = p.add_book(ledger=ledger, ratio=ratio, avg_cost=0.3127, last_plan=dict(last_plan))
    return p, b


def _recent(what, book=None):
    return [x for x in ml._RECENT if x["what"] == what and (book is None or x["book"] == book)]


def _sha(fn):
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


# ------------------------------------------------------------ (1) the rules, pure

def test_e25_the_drop_on_the_days_figures_the_flap_is_sudden_and_the_real_steps_are_judged_right():
    d = rules.his_net_drop(NET_1715, NET_DROP, 89.0)
    assert d == rules.ExitDrop(-5334.8, 5334.8, 2579.28, True), "the fixture's shape: 62 % in 89 s from 17:15:05"
    # the rule's own reference is the plan before: 17:15:57's 8,473.8 (row 152), 37 s before the drop
    assert rules.his_net_drop(NET_BACK, NET_DROP, 37.0) == rules.ExitDrop(-5211.0, 5211.0, 2542.14, True), \
        "the flap by the plan before: 5,211.0 / 61.5 % in 37 s -- the s1 row fills-vs-venue names"
    assert rules.his_net_drop(NET_1715, NET_BACK, 52.0) == rules.ExitDrop(-123.8, 123.8, 2579.28, False)
    assert rules.his_net_drop(NET_1704, 8513.3, 33.0).sudden is False, "the chain's 2,880: 25.3 %, under 30 %"
    assert rules.his_net_drop(NET_1704, NET_AFTER_CUT, 30.0) == rules.ExitDrop(-12960.0, 12960.0, 3417.99, True)
    assert rules.his_net_drop(-7234.2, -1836.2, 51.0, short=True).sudden is True, "1156's 16:12Z cut, 75 %"
    assert rules.his_net_drop(-26476.8, -18176.8, 56.0, short=True).sudden is True, "1138's 31 %"
    assert rules.his_net_drop(-31861.3, -24488.4, 54.0, short=True).sudden is False, "1075's 23 %"


def test_e25_a_rise_is_never_a_drop_and_the_short_axis_is_the_mirror_image():
    assert rules.his_net_drop(NET_DROP, NET_BACK, 30.0) == rules.ExitDrop(5211.0, 0.0, 1000.0, False)
    assert rules.his_net_drop(-3000.0, -8000.0, 30.0, short=True).drop == 0.0, "a short growing is a rise"
    assert rules.his_net_drop(-8000.0, -3000.0, 30.0, short=True) == rules.ExitDrop(-5000.0, 5000.0, 2400.0, True)
    assert rules.his_net_drop(-8000.0, -3000.0, 30.0, short=False).drop == 0.0, "the same figures on a long: a rise"


def test_e25_the_threshold_is_the_larger_of_the_shares_floor_and_the_share_of_the_reference():
    assert rules.his_net_drop(2000.0, 0.0, 10.0).threshold == 1000.0, "the floor over 30 % of 2,000"
    assert rules.his_net_drop(2000.0, 0.0, 10.0).sudden is True
    assert rules.his_net_drop(2000.0, 1100.0, 10.0).sudden is False, "900 under the 1,000 floor"
    assert rules.his_net_drop(2000.0, 900.0, 10.0, shares=500.0).sudden is True, "a lowered floor judges it"
    assert rules.his_net_drop(NET_1715, NET_DROP, 89.0, pct=0.7).sudden is False, "a raised share would not -- and the rail cannot raise it"
    assert rules.his_net_drop(NET_1715, NET_DROP, 89.0, pct=0.0).threshold == 1000.0


def test_e25_the_window_and_the_unreadables():
    assert rules.his_net_drop(NET_1715, NET_DROP, 600.0).sudden is True, "at the window exactly"
    assert rules.his_net_drop(NET_1715, NET_DROP, 600.1).sudden is False, "past it: an old reference, not sudden"
    assert rules.his_net_drop(NET_1715, NET_DROP, 601.0, window_s=900.0).sudden is True, "a lengthened window"
    for bad in (None, "x", True, float("nan"), float("inf")):
        assert rules.his_net_drop(bad, NET_DROP, 10.0) is None and rules.his_net_drop(NET_1715, bad, 10.0) is None
        assert rules.his_net_drop(NET_1715, NET_DROP, bad) is None
    assert rules.his_net_drop(NET_1715, NET_DROP, -1.0) is None, "a negative elapsed time is no reading"
    assert rules.his_net_drop(NET_1715, NET_DROP, 10.0, shares="x") is None


def test_e25_the_confirmation_reads_the_venues_figure_on_the_leg_within_the_tolerance_of_the_drop():
    assert rules.exit_confirmed(NET_DROP, DROP, NET_1715) is False, "the venue still at the old net"
    assert rules.exit_confirmed(NET_DROP, DROP, NET_DROP) is True, "the venue at the new reading"
    assert rules.exit_confirmed(NET_DROP, DROP, 1000.0) is True, "the venue below it: he sold more"
    assert rules.exit_confirmed(NET_DROP, DROP, NET_DROP + 0.5 * DROP) is True, "half the drop shown: at the tolerance"
    assert rules.exit_confirmed(NET_DROP, DROP, NET_DROP + 0.5 * DROP + 1.0) is False
    assert rules.exit_confirmed(NET_DROP, DROP, NET_DROP + 0.5 * DROP + 1.0, tol_pct=0.6) is True
    assert rules.exit_confirmed(NET_DROP, DROP, NET_1715, tol_pct=0.0) is False
    assert rules.exit_confirmed(NET_AFTER_CUT, CUT, NET_AFTER_CUT) is True, "the 17:04:57Z cut: the venue shows the crossing"
    assert rules.exit_confirmed(-3000.0, 5000.0, -3000.0, short=True) is True
    assert rules.exit_confirmed(-3000.0, 5000.0, -8000.0, short=True) is False, "a short the venue still holds whole"
    assert rules.exit_confirmed(-3000.0, 5000.0, -5500.0, short=True) is True
    for bad in (None, "x", True, float("nan")):
        assert rules.exit_confirmed(NET_DROP, DROP, bad) is None, "unread is never a confirmation"
        assert rules.exit_confirmed(bad, DROP, NET_DROP) is None and rules.exit_confirmed(NET_DROP, bad, NET_DROP) is None
    assert rules.exit_confirmed(NET_DROP, -1.0, NET_DROP) is None


def test_e25_the_rails_point_one_way_each_toward_more_confirmation(monkeypatch):
    assert (rules.MIRROR_EXIT_CONFIRM_SHARES, rules.MIRROR_EXIT_CONFIRM_PCT, rules.MIRROR_EXIT_CONFIRM_S,
            rules.MIRROR_EXIT_CONFIRM_TOL_PCT, rules.MIRROR_EXIT_CONFIRM_MAX_TICKS) == (1000.0, 0.30, 600.0, 0.5, 3)
    assert isinstance(rules.MIRROR_EXIT_CONFIRM_MAX_TICKS, int)
    src = inspect.getsource(rules)
    assert 'MIRROR_EXIT_CONFIRM_SHARES = capped_env("MIRROR_EXIT_CONFIRM_SHARES", 1000.0, floor=1.0)' in src
    assert 'MIRROR_EXIT_CONFIRM_PCT = capped_env("MIRROR_EXIT_CONFIRM_PCT", 0.30, floor=0.0)' in src
    assert 'MIRROR_EXIT_CONFIRM_S = min_wait_env("MIRROR_EXIT_CONFIRM_S", 600.0)' in src
    assert 'MIRROR_EXIT_CONFIRM_TOL_PCT = capped_env("MIRROR_EXIT_CONFIRM_TOL_PCT", 0.5, floor=0.0)' in src
    assert 'MIRROR_EXIT_CONFIRM_MAX_TICKS = int(min_wait_env("MIRROR_EXIT_CONFIRM_MAX_TICKS", 3))' in src
    assert 'env_switch("MIRROR_EXIT_CONFIRM' not in src, "no switch: OFF is not a state"
    # lowered thresholds honoured, raised ones refused (capped_env: the default is the ceiling)
    monkeypatch.setenv("MIRROR_EXIT_CONFIRM_SHARES", "500")
    assert rules.capped_env("MIRROR_EXIT_CONFIRM_SHARES", 1000.0, floor=1.0) == 500.0
    monkeypatch.setenv("MIRROR_EXIT_CONFIRM_SHARES", "5000")
    assert rules.capped_env("MIRROR_EXIT_CONFIRM_SHARES", 1000.0, floor=1.0) == 1000.0
    monkeypatch.setenv("MIRROR_EXIT_CONFIRM_SHARES", "0")
    assert rules.capped_env("MIRROR_EXIT_CONFIRM_SHARES", 1000.0, floor=1.0) == 1.0, "the floor: one share"
    monkeypatch.setenv("MIRROR_EXIT_CONFIRM_PCT", "0.1")
    assert rules.capped_env("MIRROR_EXIT_CONFIRM_PCT", 0.30, floor=0.0) == 0.1
    monkeypatch.setenv("MIRROR_EXIT_CONFIRM_PCT", "0.9")
    assert rules.capped_env("MIRROR_EXIT_CONFIRM_PCT", 0.30, floor=0.0) == 0.30
    monkeypatch.setenv("MIRROR_EXIT_CONFIRM_TOL_PCT", "0.2")
    assert rules.capped_env("MIRROR_EXIT_CONFIRM_TOL_PCT", 0.5, floor=0.0) == 0.2
    monkeypatch.setenv("MIRROR_EXIT_CONFIRM_TOL_PCT", "0.9")
    assert rules.capped_env("MIRROR_EXIT_CONFIRM_TOL_PCT", 0.5, floor=0.0) == 0.5
    # the window and the wait only lengthened (min_wait_env)
    monkeypatch.setenv("MIRROR_EXIT_CONFIRM_S", "1200")
    assert rules.min_wait_env("MIRROR_EXIT_CONFIRM_S", 600.0) == 1200.0
    monkeypatch.setenv("MIRROR_EXIT_CONFIRM_S", "60")
    assert rules.min_wait_env("MIRROR_EXIT_CONFIRM_S", 600.0) == 600.0
    monkeypatch.setenv("MIRROR_EXIT_CONFIRM_MAX_TICKS", "6")
    assert int(rules.min_wait_env("MIRROR_EXIT_CONFIRM_MAX_TICKS", 3)) == 6
    monkeypatch.setenv("MIRROR_EXIT_CONFIRM_MAX_TICKS", "1")
    assert int(rules.min_wait_env("MIRROR_EXIT_CONFIRM_MAX_TICKS", 3)) == 3
    monkeypatch.setenv("MIRROR_EXIT_CONFIRM_MAX_TICKS", "junk")
    assert int(rules.min_wait_env("MIRROR_EXIT_CONFIRM_MAX_TICKS", 3)) == 3
    for name in ("MIRROR_EXIT_CONFIRM_SHARES", "MIRROR_EXIT_CONFIRM_PCT", "MIRROR_EXIT_CONFIRM_S",
                 "MIRROR_EXIT_CONFIRM_TOL_PCT", "MIRROR_EXIT_CONFIRM_MAX_TICKS", "his_net_drop", "exit_confirmed",
                 "ExitDrop"):
        assert name in rules.__all__, name


# ------------------------------------------------------------ (2) the reference and the snapshot

def test_e25_the_reference_is_the_plan_befores_reading_or_the_skips_carried_one():
    assert ml._exit_ref({"net": NET_1715, "at": NOW - 89.0}) == (NET_1715, NOW - 89.0)
    assert ml._exit_ref({"kind": "no_plan", "at": NOW, "exit_ref": {"net": NET_1715, "at": NOW - 300.0}}) == (NET_1715, NOW - 300.0)
    assert ml._exit_ref({"kind": "no_plan", "at": NOW}) is None, "a plan with no reading"
    assert ml._exit_ref({}) is None and ml._exit_ref({"net": "x", "at": NOW}) is None
    assert ml._exit_ref({"net": NET_1715}) is None, "no clock: no window can be judged"
    assert ml._exit_ref({"exit_ref": "junk"}) is None and ml._exit_ref({"exit_ref": {"net": 1.0}}) is None
    # the skip carries it, by source
    tb = inspect.getsource(ml._tick_book)
    assert 'plan["exit_ref"] = {"net": er[0], "at": er[1]}' in tb
    assert tb.index('plan["exit_ref"]') < tb.index("_SQL_BOOK_SKIP")


def test_e25_the_snapshot_counts_only_when_fresh_stamped_and_read_after_the_newest_fill():
    t = ml._Tick(pool=None, pmus=None, http=None, now=NOW, stats=ml._new_stats())
    r = types.SimpleNamespace(snap_market_fresh=True, mkt_net=NET_1715, whale="rn1", cid=CID)
    fills = [DROP_FILL]
    assert ml._exit_snap(t, r, fills)["why"] == "snap_unstamped", "fresh but no instant recorded"
    t.mkt_at[("rn1", CID)] = NOW + 2.0
    sn = ml._exit_snap(t, r, fills)
    assert sn["snap"] == NET_1715 and sn["snap_age_s"] == 2.0 and sn["fills_at"] == NOW - 5 and sn["why"] is None
    t.mkt_at[("rn1", CID)] = NOW - 5 - float(le._ORPHAN_SKEW_S) - 1.0
    assert ml._exit_snap(t, r, fills)["why"] == "snap_before_fill", "older than the fill that made the drop"
    t.mkt_at[("rn1", CID)] = NOW - 5 - float(le._ORPHAN_SKEW_S)
    assert ml._exit_snap(t, r, fills)["snap"] == NET_1715, "inside the skew allowance"
    r2 = types.SimpleNamespace(snap_market_fresh=None, mkt_net=None, whale="rn1", cid=CID)
    assert ml._exit_snap(t, r2, fills)["why"] == "snap_unread"
    # the per-market read stamps its instant on the fresh path only
    ms = inspect.getsource(ml._market_snap)
    assert "t.mkt_at[key] = float(ts)" in ms and ms.index("t.mkt_at[key]") > ms.index("out = (True, lo, ot, mi.his_net(lo, ot))")


# ------------------------------------------------------------ (3) the planner: book 1177's shape

def test_e25_book_1177s_flap_is_held_unconfirmed_then_averted_with_no_order_and_the_ledger_at_719(monkeypatch):
    """17:16:34Z: the reading 3,262.8 against the plan before's 8,597.6
    (a 62 % drop in 89 s); the venue's own position still 8,597.6 -> the
    393-share sale is HELD. 17:19:09Z: the reading back at 8,473.8 -> the
    flap averted; the increase it asks for is refused as any increase
    without a fresh read is; nothing bought, nothing sold, 719 held."""
    p, b = _world(monkeypatch, [BUILT, DROP_FILL], PLAN_1715)
    v = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    st = _tick(p, v, http=_mkt(NET_1715, 0.0))
    assert not _places(v) and not _cancels(v) and b["ledger_net"] == LEDGER_1177 and b["target"] == 326
    assert _census(st, "exit_unconfirmed") == 1 and all(_census(st, k) == 0 for k in NEW_NAMES[1:])
    lp = b["last_plan"]
    ec = lp["exit_confirm"]
    assert ec["verdict"] == "unconfirmed" and ec["ticks"] == 1 and ec["kind"] == "reduce"
    assert (ec["prev"], ec["net"], ec["drop"], ec["threshold"], ec["snap"]) == (NET_1715, NET_DROP, DROP, 2579.28, NET_1715)
    assert ec["since"] == NOW - 89.0 and ec["prev_target"] == TARGET_1715 and ec["held"]["qty"] == 393
    assert ec["snap_why"] is None and ec["snap_age_s"] is not None
    assert b["last_reason"] == "exit_unconfirmed" and lp["reason"] == "exit_unconfirmed" and lp["qty"] == 0
    assert lp["net"] == NET_DROP and lp["drift"] > float(rules.MIRROR_DRIFT_MAX), "the smaller-of-two reading, as today"
    assert lp["reduce_ref"] == {"target": TARGET_1715, "at": NOW - 100.0}, "a held plan is not a witnessed state"
    assert "reduce_unwitnessed" not in lp, "E15 admitted the reduce first (his 5,334.8 BUY of the other token witnesses it)"
    rec = _recent("exit_unconfirmed", b["id"])
    assert rec and rec[-1]["prev"] == NET_1715 and rec[-1]["net"] == NET_DROP and rec[-1]["qty"] == 393
    # 17:19:09Z: the reading recovers; the snapshot cannot be read this tick
    p.fills = [BUILT, BACK_FILL]
    v2 = _Venue(bid=0.32, ask=0.33, held={SLUG: LEDGER_1177})
    st2 = _tick(p, v2, now=NOW + 155, http=WALK)
    assert not _places(v2) and not _cancels(v2) and b["ledger_net"] == LEDGER_1177
    assert _census(st2, "exit_flap_averted") == 1 and _census(st2, "exit_unconfirmed") == 0
    ec2 = b["last_plan"]["exit_confirm"]
    assert ec2["verdict"] == "averted" and (ec2["prev"], ec2["net"], ec2["drop"], ec2["ticks"]) == (NET_1715, NET_BACK, 123.8, 1)
    assert ec2["since"] == NOW - 89.0 and ec2["snap"] is None
    assert b["last_reason"] == "snapshot_stale" and b["target"] == 847
    av = _recent("exit_flap_averted", b["id"])
    assert av and av[-1]["prev"] == NET_1715 and av[-1]["net"] == NET_BACK and av[-1]["ledger"] == LEDGER_1177
    # the tick after: no hold stands, the reference is the last plan's own reading
    st3 = _tick(p, _Venue(bid=0.32, ask=0.33, held={SLUG: LEDGER_1177}), now=NOW + 200, http=WALK)
    assert "exit_confirm" not in b["last_plan"] and all(_census(st3, k) == 0 for k in NEW_NAMES)


def test_e25_a_held_drop_fires_the_tick_the_venue_confirms_it_one_tick_late_never_more(monkeypatch):
    p, b = _world(monkeypatch, [BUILT, DROP_FILL], PLAN_1715)
    v = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    st = _tick(p, v, http=_mkt(NET_1715, 0.0))
    assert not _places(v) and _census(st, "exit_unconfirmed") == 1 and b["ledger_net"] == LEDGER_1177
    v2 = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    st2 = _tick(p, v2, now=NOW + 45, http=_mkt(NET_DROP, 0.0))
    assert _census(st2, "exit_confirmed") == 1 and _census(st2, "exit_unconfirmed") == 0
    # RE-PINNED at E31 (FILL lane 31, 2026-09-10): the confirmed exit is a post-only
    # GTC rest at his cent, not an IOC at the take cent, and it books here because the
    # venue lifts it at create (`lift`, `aggressor` False: a maker fill). The 393 shares,
    # the ledger 326 and everything this file pins about the CONFIRMATION are unchanged
    assert len(_places(v2)) == 1 and _places(v2)[0][3] == 393 and _places(v2)[0][5] == GTC
    assert b["ledger_net"] == 326 and _census(st2, "exit_take") == 0 and _census(st2, "rest_placed") == 1
    ec = b["last_plan"]["exit_confirm"]
    assert ec["verdict"] == "confirmed" and ec["ticks"] == 2 and ec["snap"] == NET_DROP and ec["prev"] == NET_1715
    assert b["last_plan"]["reduce_ref"] == {"target": 326, "at": NOW - 5}, "the reference moves with the fired exit"


def test_e25_the_1704_cut_the_venue_shows_is_a_sign_flip_and_fires_as_today(monkeypatch):
    """His 12,960 buy of the other token read 11,393.3 -> -1,566.7 (a
    crossing: the sign flip's paired flatten); the flip close is never
    guarded (FILL_plan_C section 4; FILL_plan 120: the flip is the reopen
    mechanism) -- the flatten goes out this tick with nothing of this
    rule's on it, the venue's read never asked."""
    _shorts_on(monkeypatch)
    p, b = _world(monkeypatch, [_fill(M, "BUY", NET_1704, 0.31, NOW - 3000),
                                _fill(N, "BUY", CUT, 0.70, NOW - 10, detected_at=NOW - 5)],
                  PLAN_1704, ledger=LEDGER_1704)
    v = _Venue(bid=0.29, ask=0.31, held={SLUG: LEDGER_1704}, ioc_fill=float(LEDGER_1704), lift=float(LEDGER_1704))
    st = _tick(p, v, http=_mkt(NET_1704, CUT))
    lp = b["last_plan"]
    assert lp["sign_flip"] is True and lp["kind"] == "flatten_paired" and b["ledger_net"] == 0
    assert len(_places(v)) == 1 and _places(v)[0][3] == LEDGER_1704 and _places(v)[0][5] == GTC
    # the flip close is never guarded (plan_C section 4; FILL_plan 120): nothing of this rule's on a sign flip
    assert "exit_confirm" not in lp and all(_census(st, k) == 0 for k in NEW_NAMES)


def test_e25_the_1704_cut_with_the_venue_one_tick_behind_holds_the_target_zero_flatten_then_the_crossing_flips_as_today(monkeypatch):
    """The venue a tick behind: the readings part on the SIGN and today's
    smaller-of-two reads him at 0 -- a target-0 paired flatten on the SAME
    side, not a flip: held. The next tick the venue shows the crossing,
    the reading is -1,566.7, a SIGN FLIP: the flatten fires as today."""
    _shorts_on(monkeypatch)
    p, b = _world(monkeypatch, [_fill(M, "BUY", NET_1704, 0.31, NOW - 3000),
                                _fill(N, "BUY", CUT, 0.70, NOW - 10, detected_at=NOW - 5)],
                  PLAN_1704, ledger=LEDGER_1704)
    v = _Venue(bid=0.29, ask=0.31, held={SLUG: LEDGER_1704}, ioc_fill=float(LEDGER_1704), lift=float(LEDGER_1704))
    st = _tick(p, v, http=_mkt(NET_1704, 0.0))
    assert not _places(v) and b["ledger_net"] == LEDGER_1704 and _census(st, "exit_unconfirmed") == 1
    lp = b["last_plan"]
    # the venue and his fills part on the SIGN: today's smaller-of-two reads him at 0 (net 0, no flip yet)
    # and plans the paired flatten -- a drop of the whole 11,393.3, held until the venue shows the crossing
    assert lp["net"] == 0.0 and "sign_flip" not in lp and lp["exit_confirm"]["verdict"] == "unconfirmed"
    assert lp["exit_confirm"]["kind"] == "flatten_paired" and lp["exit_confirm"]["snap"] == NET_1704
    assert lp["exit_confirm"]["drop"] == NET_1704 and lp["exit_confirm"]["held"]["qty"] == LEDGER_1704
    assert b["last_reason"] == "exit_unconfirmed" and b["state"] == "live"
    v2 = _Venue(bid=0.29, ask=0.31, held={SLUG: LEDGER_1704}, ioc_fill=float(LEDGER_1704), lift=float(LEDGER_1704))
    st2 = _tick(p, v2, now=NOW + 45, http=_mkt(NET_1704, CUT))
    # the reading now crosses: a SIGN FLIP -- the flip close is never guarded, the flatten fires as today
    assert b["last_plan"]["sign_flip"] is True and b["ledger_net"] == 0 and len(_places(v2)) == 1
    assert all(_census(st2, k) == 0 for k in NEW_NAMES)
    assert b["last_plan"]["exit_confirm"]["ticks"] == 1, "the hold's record rides onto the closing plan unmoved"


def test_e25_a_snapshot_that_cannot_be_read_holds_three_ticks_then_the_exit_fires_expired(monkeypatch):
    p, b = _world(monkeypatch, [BUILT, DROP_FILL], PLAN_1715)
    for i, now in enumerate((NOW, NOW + 45, NOW + 90), start=1):
        v = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
        st = _tick(p, v, now=now, http=WALK)
        assert not _places(v) and b["ledger_net"] == LEDGER_1177, i
        assert _census(st, "exit_unconfirmed") == 1 and _census(st, "exit_confirm_expired") == 0, i
        ec = b["last_plan"]["exit_confirm"]
        assert ec["verdict"] == "unconfirmed" and ec["ticks"] == i and ec["snap"] is None and ec["snap_why"] == "snap_unread"
        assert ec["prev"] == NET_1715 and ec["since"] == NOW - 89.0, "the reference stands across the hold"
        assert b["last_plan"]["reduce_ref"] == {"target": TARGET_1715, "at": NOW - 100.0}
    assert int(rules.MIRROR_EXIT_CONFIRM_MAX_TICKS) == 3
    v4 = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    st4 = _tick(p, v4, now=NOW + 135, http=WALK)
    assert _census(st4, "exit_confirm_expired") == 1 and _census(st4, "exit_unconfirmed") == 0
    assert len(_places(v4)) == 1 and _places(v4)[0][3] == 393 and b["ledger_net"] == 326
    ec = b["last_plan"]["exit_confirm"]
    assert ec["verdict"] == "expired" and ec["ticks"] == 4 and ec["snap"] is None
    ex = _recent("exit_confirm_expired", b["id"])
    assert ex and ex[-1]["ticks"] == 4 and ex[-1]["qty"] == 393
    # the tick after: no hold, nothing judged
    st5 = _tick(p, _Venue(bid=0.295, ask=0.30, held={SLUG: 326}), now=NOW + 180, http=WALK)
    assert "exit_confirm" not in b["last_plan"] and all(_census(st5, k) == 0 for k in NEW_NAMES)


def test_e25_a_lengthened_wait_holds_longer_never_shorter(monkeypatch):
    monkeypatch.setattr(rules, "MIRROR_EXIT_CONFIRM_MAX_TICKS", 4)
    p, b = _world(monkeypatch, [BUILT, DROP_FILL], PLAN_1715)
    for i, now in enumerate((NOW, NOW + 45, NOW + 90, NOW + 135), start=1):
        v = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
        _tick(p, v, now=now, http=WALK)
        assert not _places(v) and b["last_plan"]["exit_confirm"]["ticks"] == i
    v5 = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    st5 = _tick(p, v5, now=NOW + 180, http=WALK)
    assert _census(st5, "exit_confirm_expired") == 1 and b["ledger_net"] == 326


def test_e25_a_drop_under_the_threshold_is_todays_reduce_with_no_hold_and_no_name(monkeypatch):
    plan = {**PLAN_1715, "target": 900, "reduce_ref": {"target": 900, "at": NOW - 100.0}}
    p, b = _world(monkeypatch, [BUILT, BACK_FILL], plan, ledger=900)
    v = _Venue(bid=0.295, ask=0.30, held={SLUG: 900}, ioc_fill=53.0, lift=53.0)
    st = _tick(p, v, http=_mkt(NET_BACK, 0.0))
    assert b["target"] == 847 and len(_places(v)) == 1 and _places(v)[0][3] == 53 and b["ledger_net"] == 847
    assert "exit_confirm" not in b["last_plan"] and all(_census(st, k) == 0 for k in NEW_NAMES)


def test_e25_a_rise_is_never_judged_the_entry_goes_as_today(monkeypatch):
    plan = {"kind": "reduce", "reason": "take", "target": 326, "at": NOW - 52.0, "net": NET_DROP,
            "reduce_ref": {"target": 326, "at": NOW - 60.0}}
    p, b = _world(monkeypatch, [BUILT], plan, ledger=326)
    v = _Venue(bid=0.30, ask=0.31, held={SLUG: 326}, ioc_fill=533.0, lift=533.0)
    st = _tick(p, v, http=_mkt(NET_1715, 0.0))
    assert b["target"] == 859 and len(_places(v)) == 1 and _places(v)[0][6] == "ORDER_INTENT_BUY_LONG"
    assert b["ledger_net"] > 326
    assert "exit_confirm" not in b["last_plan"] and all(_census(st, k) == 0 for k in NEW_NAMES)


def test_e25_e15s_witness_is_asked_first_a_reading_drop_with_no_fill_of_his_holds_under_its_own_name(monkeypatch):
    """The per-market read alone falls to 3,262.8 (no fill of his made
    it): E15 holds the reduce `reduce_unwitnessed: reading` and E25 never
    judges it -- the drop is judged AFTER E15 admits, never instead."""
    p, b = _world(monkeypatch, [BUILT], PLAN_1715)
    v = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    st = _tick(p, v, http=_mkt(NET_DROP, 0.0))
    lp = b["last_plan"]
    assert not _places(v) and b["ledger_net"] == LEDGER_1177 and lp["net"] == NET_DROP
    assert lp["reduce_unwitnessed"] == {"from": TARGET_1715, "to": 326, "cause": "reading"}
    assert "exit_confirm" not in lp and all(_census(st, k) == 0 for k in NEW_NAMES)
    assert b["last_reason"] == "reduce_unwitnessed"


def test_e25_the_witnessed_exit_resting_before_the_drop_keeps_its_life_under_the_hold(monkeypatch):
    """A 40-share reduce of his earlier witnessed sale rests at his cent
    (the plan before: target 719 under a ledger of 759); the phantom drop
    then asks for 433: held -- and the rest of 40 STANDS, the plan handed
    over capped at the plan before's 719 (E15's construction); with the
    bid inside the cent it is taken."""
    plan = {**PLAN_1715, "kind": "reduce", "reason": "exit_out_of_tol", "target": LEDGER_1177,
            "reduce_ref": {"target": LEDGER_1177, "at": NOW - 100.0}}
    p, b = _world(monkeypatch, [BUILT, DROP_FILL], plan, ledger=759)
    o = p.add_order(b, side=SELL, wire=0.30, qty=40, kind="reduce", order_id="oid-exit")
    v = _Venue(bid=0.28, ask=0.31, held={SLUG: 759})
    v.rest("oid-exit", side="SELL", price=0.30, qty=40)
    st = _tick(p, v, http=_mkt(NET_1715, 0.0))
    ec = b["last_plan"]["exit_confirm"]
    assert ec["verdict"] == "unconfirmed" and ec["cap"] == 40 and ec["held"]["qty"] == 433
    assert _census(st, "exit_unconfirmed") == 1 and b["last_plan"]["open_order"] == o["id"]
    assert (b["last_plan"]["side"], b["last_plan"]["qty"]) == (SELL, 40), "the witnessed 40, never the phantom 433"
    assert not _cancels(v) and not _places(v) and p.orders[o["id"]]["state"] == "open" and b["ledger_net"] == 759
    assert _census(st, "exit_out_of_tol") == 1
    # the bid arrives inside the cent. E25 pinned the rest CANCELLED and taken for the 40
    # here. RE-PINNED at E31 (FILL lane 31, 2026-09-10): a bid arriving at a standing rest
    # is the rest being filled, not a reason to cross for it -- rules.maker_compare_wire
    # refuses to re-quote a SELL rest toward a bid that came to it -- so the 40 STANDS at
    # 0.30 and the taker who came to that cent lifts it there. Nothing is cancelled,
    # nothing is sent, the ledger is unmoved, and the phantom 433 is still held
    v2 = _Venue(bid=0.295, ask=0.31, held={SLUG: 759}, ioc_fill=40.0, lift=40.0)
    v2.rest("oid-exit", side="SELL", price=0.30, qty=40)
    st2 = _tick(p, v2, now=NOW + 45, http=_mkt(NET_1715, 0.0))
    assert _census(st2, "exit_take") == 0 and b["ledger_net"] == 759 and not _places(v2)
    assert not _cancels(v2) and _census(st2, "open_order_pending") == 1
    assert _census(st2, "exit_unconfirmed") == 1 and b["last_plan"]["exit_confirm"]["ticks"] == 2


def test_e25_the_hold_cancels_a_resting_add_under_its_name_and_the_row_carries_the_name(monkeypatch):
    p, b = _world(monkeypatch, [BUILT, DROP_FILL], PLAN_1715)
    o = p.add_order(b, side=BUY, wire=0.31, qty=140, kind="increase", order_id="oid-add")
    v = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    v.rest("oid-add", side="BUY", price=0.31, qty=140)
    st = _tick(p, v, http=_mkt(NET_1715, 0.0))
    assert [c[:2] for c in _cancels(v)] == [("cancel", "oid-add")] and p.orders[o["id"]]["state"] == "cancelled"
    assert not _places(v) and b["ledger_net"] == LEDGER_1177 and _census(st, "exit_unconfirmed") == 1
    assert b["last_reason"] == "exit_unconfirmed" and "cap" not in b["last_plan"]["exit_confirm"]


def test_e25_a_plan_with_no_reading_before_it_judges_no_drop_todays_path(monkeypatch):
    """A restart onto a row whose last plan carries no `net` (a
    market_unreadable tick): no reference, the reduce fires as today."""
    p, b = _world(monkeypatch, [BUILT, DROP_FILL], {"kind": "no_plan", "at": NOW - 89.0, "market_unreadable": True,
                                                    "reduce_ref": {"target": TARGET_1715, "at": NOW - 100.0}})
    v = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    st = _tick(p, v, http=_mkt(NET_1715, 0.0))
    assert len(_places(v)) == 1 and b["ledger_net"] == 326 and "exit_confirm" not in b["last_plan"]
    assert all(_census(st, k) == 0 for k in NEW_NAMES)


def test_e25_the_reference_rides_a_quiet_skip_and_the_drop_after_it_is_judged(monkeypatch):
    """A quiet book (on target, no fill of his inside HOT_S) skipped on
    the rotation carries the last read's reading as `exit_ref`; the fill
    that lands after the skip makes it hot, and its drop is judged against
    that reading, not against the skip's empty plan."""
    p, b = _world(monkeypatch, [_fill(M, "BUY", NET_1715, 0.31, NOW - 3000)],
                  {**PLAN_1715, "kind": "increase", "reason": "on target", "target": LEDGER_1177,
                   "reduce_ref": {"target": LEDGER_1177, "at": NOW - 100.0}}, ledger=LEDGER_1177)
    b["ratio"] = LEDGER_1177 / NET_1715                 # on target at the read
    v = _Venue(bid=0.30, ask=0.31, held={SLUG: LEDGER_1177})
    st = _tick(p, v, http=_mkt(NET_1715, 0.0))
    assert b["last_reason"] == "on target" and "exit_ref" not in b["last_plan"] and b["last_plan"]["net"] == NET_1715
    st2 = _tick(p, _Venue(bid=0.30, ask=0.31, held={SLUG: LEDGER_1177}), now=NOW + 45, http=_mkt(NET_1715, 0.0))
    assert b["last_reason"] == "book_quiet_skipped" and b["last_plan"]["exit_ref"] == {"net": NET_1715, "at": NOW}
    p.fills = [_fill(M, "BUY", NET_1715, 0.31, NOW - 3000),
               _fill(N, "BUY", DROP, 0.70, NOW + 50, detected_at=NOW + 55, source="s1")]
    v3 = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=500.0, lift=500.0)
    st3 = _tick(p, v3, now=NOW + 90, http=_mkt(NET_1715, 0.0))
    assert not _places(v3) and _census(st3, "exit_unconfirmed") == 1
    assert b["last_plan"]["exit_confirm"]["prev"] == NET_1715 and b["last_plan"]["exit_confirm"]["since"] == NOW


def test_e25_the_fast_tick_plans_the_same_function_and_holds_the_same_way(monkeypatch):
    p, b = _world(monkeypatch, [BUILT, DROP_FILL], PLAN_1715)
    _walk({SLUG: LEDGER_1177})
    v = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    fs = _fast(p, v, http=_mkt(NET_1715, 0.0))
    assert _skips(fs) == {} and not _places(v) and b["ledger_net"] == LEDGER_1177
    assert b["last_reason"] == "exit_unconfirmed" and _census(fs, "exit_unconfirmed") == 1
    assert "await _tick_book(t, fresh)" in inspect.getsource(ml._fast_book)


# ------------------------------------------------------------ (3b) the review's pins (FILL_L25_review.md)

def test_e25_a_market_unreadable_tick_inside_a_hold_carries_the_hold_and_his_next_row_does_not_sell_the_phantom(monkeypatch):
    """A markets-row blip (MAJOR C's shape) inside a hold: the unreadable
    plan carries the hold and the reference; E15 holds the next readable
    tick on its own reset reference; his next reducing row after the blip
    (a 10-share BUY of the other token) lets E15 admit -- and the reduce is
    judged against 8,597.6 again: held, ticks 2, nothing sold. Without the
    carry that tick sold 394 @0.29 with none of this rule's names
    (the review's HIGH-2)."""
    p, b = _world(monkeypatch, [BUILT, DROP_FILL], PLAN_1715)
    v = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    _tick(p, v, http=_mkt(NET_1715, 0.0))
    assert not _places(v) and b["last_plan"]["exit_confirm"]["ticks"] == 1
    p.raise_on.append(("ml-market", RuntimeError("blip")))
    v2 = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    st2 = _tick(p, v2, now=NOW + 45, http=_mkt(NET_1715, 0.0))
    assert b["last_reason"] == "market_unreadable" and not _places(v2) and _census(st2, "market_unreadable") == 1
    lp2 = b["last_plan"]
    assert lp2["market_unreadable"] is True and lp2["exit_ref"] == {"net": NET_DROP, "at": NOW}
    assert lp2["exit_confirm"]["verdict"] == "unconfirmed" and lp2["exit_confirm"]["ticks"] == 1
    assert all(_census(st2, k) == 0 for k in NEW_NAMES)
    p.raise_on.clear()
    v3 = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    st3 = _tick(p, v3, now=NOW + 90, http=_mkt(NET_1715, 0.0))
    assert not _places(v3) and b["last_reason"] == "reduce_unwitnessed", "E15's reset reference holds first"
    assert b["last_plan"]["exit_confirm"]["ticks"] == 1, "the record rides on through E15's hold"
    p.fills = [BUILT, DROP_FILL, _fill(N, "BUY", 10.0, 0.70, NOW + 100, detected_at=NOW + 100, source="s1")]
    v4 = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=400.0, lift=400.0)
    st4 = _tick(p, v4, now=NOW + 135, http=_mkt(NET_1715, 0.0))
    assert not _places(v4) and b["ledger_net"] == LEDGER_1177 and _census(st4, "exit_unconfirmed") == 1
    ec4 = b["last_plan"]["exit_confirm"]
    assert ec4["verdict"] == "unconfirmed" and ec4["ticks"] == 2 and ec4["prev"] == NET_1715


def test_e25_a_standing_hold_is_never_re_judged_on_the_window(monkeypatch):
    """The window (MIRROR_EXIT_CONFIRM_S 600 s) is judged ONCE, when the
    drop is first seen. A hold whose ticks carry it past 600 s from the
    reference is still the same drop: held, never 'not sudden', never
    averted into the 393-share sale (the review's HIGH-3, mutant M18)."""
    p, b = _world(monkeypatch, [BUILT, DROP_FILL], PLAN_1715)
    ticks = (NOW, NOW + 300, NOW + 520)
    assert ticks[-1] - (NOW - 89.0) > float(rules.MIRROR_EXIT_CONFIRM_S), "the third held tick is past the window"
    for i, now in enumerate(ticks, start=1):
        v = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
        st = _tick(p, v, now=now, http=WALK)
        assert not _places(v) and b["ledger_net"] == LEDGER_1177, i
        assert _census(st, "exit_unconfirmed") == 1 and _census(st, "exit_flap_averted") == 0, i
        ec = b["last_plan"]["exit_confirm"]
        assert ec["verdict"] == "unconfirmed" and ec["ticks"] == i and ec["prev"] == NET_1715, i


def test_e25_a_short_books_cover_sized_from_a_sudden_fall_of_his_short_is_held_on_its_own_leg(monkeypatch):
    """Book 1156's axis (book_1156_1624 rows 87 / 86): his short -7,234.2
    read back to -1,836.2 -- a fall of 5,398 on the SHORT leg, the net
    rising toward zero -- with the venue still showing the whole short:
    the cover of 540 (723 -> 183) is held `exit_unconfirmed`. Read on the
    long leg the same figures are a RISE and the cover fires (the
    review's HIGH-4, mutant M22)."""
    _shorts_on(monkeypatch)
    _rails_2026_09_06(monkeypatch)
    held_short = _fill(N, "BUY", 7234.2, 0.57, NOW - 3000)
    cut = _fill(M, "BUY", 5398.0, 0.43, NOW - 10, detected_at=NOW - 5, source="s1")
    p = _pool(fills=[held_short, cut], snap={M: 0.0, N: 7234.2}, snap_at=NOW - 1000)
    plan = {"kind": "increase", "reason": "on target", "target": -723, "at": NOW - 51.0, "net": -7234.2,
            "reduce_ref": {"target": -723, "at": NOW - 100.0}}
    b = _short_book(p, ledger=-723, avg=0.43, ratio=0.10, last_plan=plan)
    v = _Venue(bid=0.36, ask=0.38, held={SLUG: -723}, ioc_fill=540.0, lift=540.0)
    st = _tick(p, v, http=_mkt(0.0, 7234.2))
    assert not _places(v) and b["ledger_net"] == -723
    assert _census(st, "exit_unconfirmed") == 1 and all(_census(st, k) == 0 for k in NEW_NAMES[1:])
    ec = b["last_plan"]["exit_confirm"]
    assert ec["verdict"] == "unconfirmed" and ec["kind"] == "reduce"
    assert (ec["prev"], ec["net"], ec["drop"], ec["snap"]) == (-7234.2, -1836.2, 5398.0, -7234.2)
    assert ec["threshold"] == round(0.3 * 7234.2, 6) and b["last_reason"] == "exit_unconfirmed"
    assert rules.his_net_drop(-7234.2, -1836.2, 51.0, short=True).sudden
    assert not rules.his_net_drop(-7234.2, -1836.2, 51.0, short=False).sudden


def test_e25_a_fall_at_the_threshold_exactly_is_not_past_it():
    """`drop > thr`, never `>=`: 1,000 on a reference of 2,000 is AT the
    floor, not past it (the review's MEDIUM-1, mutant M2)."""
    assert rules.his_net_drop(2000.0, 1000.0, 10.0) == rules.ExitDrop(-1000.0, 1000.0, 1000.0, False)
    assert rules.his_net_drop(2000.0, 1000.0, 10.0, pct=0.5) == rules.ExitDrop(-1000.0, 1000.0, 1000.0, False)
    assert rules.his_net_drop(2000.0, 999.999999, 10.0).sudden is True, "one millionth past it: sudden"


def test_e25_the_hold_rides_through_a_tick_that_judged_no_exit_and_the_next_reduce_is_still_judged_against_the_reading_before_the_drop(monkeypatch):
    """Tick 1 holds; tick 2 E15 holds on its own (the same low reading,
    no reducing fill of his after the reference); tick 3 his fill is back
    and E15 admits -- the record rode through, so the reference is still
    8,597.6 and the 393 stays held. With the carry gone the reference
    would be tick 2's own 3,262.8, no drop, and the 393 sells (the
    review's MEDIUM-2, mutant M17)."""
    p, b = _world(monkeypatch, [BUILT, DROP_FILL], PLAN_1715)
    v = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    st = _tick(p, v, http=_mkt(NET_1715, 0.0))
    assert not _places(v) and _census(st, "exit_unconfirmed") == 1 and b["last_plan"]["exit_confirm"]["ticks"] == 1
    p.fills = [_fill(M, "BUY", NET_DROP, 0.31, NOW - 3000)]        # the same low reading, no witness after the reference
    v2 = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    st2 = _tick(p, v2, now=NOW + 45, http=_mkt(NET_1715, 0.0))
    lp2 = b["last_plan"]
    assert not _places(v2) and b["ledger_net"] == LEDGER_1177
    assert lp2["reduce_unwitnessed"] == {"from": TARGET_1715, "to": 326, "cause": "fills"}
    assert all(_census(st2, k) == 0 for k in NEW_NAMES), "E15's hold: nothing of this rule's counted"
    assert lp2["exit_confirm"]["verdict"] == "unconfirmed" and lp2["exit_confirm"]["ticks"] == 1
    assert lp2["exit_confirm"]["prev"] == NET_1715, "the record rides on, unmoved"
    p.fills = [BUILT, DROP_FILL]                                     # his fill back in the read: E15 admits
    v3 = _Venue(bid=0.295, ask=0.30, held={SLUG: LEDGER_1177}, ioc_fill=393.0, lift=393.0)
    st3 = _tick(p, v3, now=NOW + 90, http=_mkt(NET_1715, 0.0))
    assert not _places(v3) and b["ledger_net"] == LEDGER_1177 and _census(st3, "exit_unconfirmed") == 1
    ec3 = b["last_plan"]["exit_confirm"]
    assert ec3["verdict"] == "unconfirmed" and ec3["ticks"] == 2 and ec3["prev"] == NET_1715 and ec3["net"] == NET_DROP


def test_e25_the_frozen_exit_the_act_and_the_vanish_keep_their_readers_byte_for_byte():
    """Hashed against 09b35cd (the tip this lane was built on): the exit
    machinery this lane does not touch. `_act` re-cut at landing over the
    per-trade cap (52e1d52, docs 67: an add's standing rest compared against
    the clipped plan, p_cmp -- an entry change, not this lane's):
    42e08939276dcd0a -> bbe3cae4167edb41; then E27 (FILL lane 27, docs 69:
    the take's tolerance, the short's band arm and at-level take in _act)
    -> 59efb48ba79f793c.

    RE-PINNED at E31 (FILL lane 31, 2026-09-10: every order a post-only
    rest that never crosses). Two of the ten moved and one is DELETED:
      _act              59efb48ba79f793c -> 2e7043299fbf834a (the six take arms gone,
                        and the fold's `maker_no_cent` hold: a standing rest is never
                        cancelled because this tick has no cent -- review CRITICAL-2)
      _flatten_vanished 22930dc6e3e85816 -> 7f27e3b041da0c76 (the slippage leg gone;
                                            the unpriced vanish rests at the touch)
      _exit_take        750acd709c826566 -> DELETED with the exit's IOC
    The other seven -- the frozen exit, the close, the two fast readers,
    the gone-confirmation and the two readings this lane's rule is built
    on -- are byte for byte 09b35cd's through six lanes."""
    for name, digest in {"_act": "2e7043299fbf834a",
                         "_flatten_vanished": "7f27e3b041da0c76", "_frozen_exit": "ef478fabdfa2ccc0",
                         "_maybe_close_episode": "59e28ff01f960660", "_fast_gate": "1932811194268668",
                         "_fast_book": "286e6fa4663c3887", "_confirm_gone": "61e425dcf38e1285",
                         "_net_for": "7d9cea6364462ac6", "_drift_for": "0b124feab3c8f6a3"}.items():
        assert _sha(getattr(ml, name)) == digest, name
    assert not hasattr(ml, "_exit_take"), "E31: the exit take's wrapper is gone"


# ------------------------------------------------------------ (4) the census place, the emit sites, no knob

def test_e25_the_census_place_the_emit_sites_and_no_knob_no_migration_no_decision_word():
    keys = ml.CENSUS_KEYS
    assert keys[-37:-33] == NEW_NAMES and keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    assert keys[-41:-37] == ("hand_explained", "hand_adopted", "hand_unread", "hand_ambiguous")
    assert keys[-47:-41] == ("cancel_fill_late", "cancel_fill_unread", "disagree_fill_adopted", "disagree_fill_unread",
                             "disagree_fill_unexplained", "disagree_fill_ambiguous")
    # E29 (FILL lane 29): four names before drift_smaller_open, 237 -> 241 (this block -17:-13 -> -21:-17); E28's five then land between E25's and E29's: 246 keys, this block -26:-22
    assert keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys) and len(keys) == 257
    assert all(ml._new_stats()["census"][k] == 0 for k in NEW_NAMES)
    tb = inspect.getsource(ml._tick_book)
    for name in NEW_NAMES:
        assert tb.count(f'_mirror_stop("{name}", w)') == 1, name
    # the judgement's place: after E12b's hold and E15's witness, inside the reduce branch, never on an add
    assert tb.index('_mirror_stop("exit_unconfirmed", w)') > tb.index("cancel_reason = mi.REDUCE_UNWITNESSED")
    assert tb.index('_mirror_stop("exit_unconfirmed", w)') > tb.index("cancel_reason = mi.FLOW_FILLS_SHRANK")
    assert 'and plan.get("flow_hold") is None and plan.get(mi.REDUCE_UNWITNESSED) is None' in tb
    assert 'and kind in ("reduce", "flatten_paired") and plan.get("sign_flip") is not True' in tb and "not t.flatten_all" in tb
    assert all(k not in ml._INTEG_CENSUS_KEYS for k in NEW_NAMES), "a hold is not an integrity name"
    assert "rules.his_net_drop(ec_ref[0], net," in tb and 'rules.exit_confirmed(net, exit_drop.drop, sn["snap"], short=short)' in tb
    assert "ec_ticks > int(rules.MIRROR_EXIT_CONFIRM_MAX_TICKS)" in tb
    assert 'elif exit_held and ref is not None:' in tb, "the reference stands under the hold"
    # no switch, no migration, no decision word (059 as section 47 left it), no new skip key
    assert '"MIRROR_EXIT_CONFIRM' not in inspect.getsource(ml), "the worker reads the rails through rules, never the environment"
    assert "os.environ" not in inspect.getsource(ml._tick_book)
    assert sorted(x.name for x in (ROOT / "backend" / "migrations").glob("*.sql"))[-1].startswith("064_")  # re-pinned 2026-09-12 (run 83.3): run 83.3's 064 is the newest; this lane still adds none
    assert "exit_confirm" not in (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()
    assert "exit_ref" not in ml._SKIP_CARRIED and "exit_confirm" not in ml._SKIP_CARRIED


def test_e25_every_name_is_emitted_here(monkeypatch):
    """The lane's four names, each driven once (the worker file's coverage
    read imports this)."""
    test_e25_book_1177s_flap_is_held_unconfirmed_then_averted_with_no_order_and_the_ledger_at_719(monkeypatch)
    test_e25_a_held_drop_fires_the_tick_the_venue_confirms_it_one_tick_late_never_more(monkeypatch)
    test_e25_a_snapshot_that_cannot_be_read_holds_three_ticks_then_the_exit_fires_expired(monkeypatch)


def test_e25_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E25 -- .* \(2026-09-09, FILL lane 25\)", doc, re.M), "the E25 section header"
    sec = doc[doc.index("## 66. E25"):]
    for k in NEW_NAMES + ("his_net_drop", "exit_confirmed", "MIRROR_EXIT_CONFIRM_SHARES", "MIRROR_EXIT_CONFIRM_PCT",
                          "MIRROR_EXIT_CONFIRM_S", "MIRROR_EXIT_CONFIRM_TOL_PCT", "MIRROR_EXIT_CONFIRM_MAX_TICKS",
                          "exit_confirm", "exit_ref", "_exit_snap", "_exit_ref", "1177", "8,597.6", "3,262.8",
                          "8,473.8", "5,334.8", "11,393.3", "-1,566.7", "12,960", "7250", "7257", "393", "521",
                          "test_e25_exit_confirm.py", "capped_env", "min_wait_env", "reduce_unwitnessed",
                          "flow_hold", "flatten_vanished", "_frozen_exit"):
        assert k in sec, k
