"""PNL lane 3+4 (E15) -- the adversarial review's pins (2026-09-08).

Two kinds, said on each: KILL pins kill a mutant the lane's own tests
let live; DEFECT pins pin the code AS IT STANDS at a finding of the
review (the assertion is the defect, so the fold flips it), each named
by its finding. Driven on the real planner through the worker file's
fakes, exactly as the lane's pins are.
"""
import time

from sportsassets.analytics import mirror_live_rules as rules
from tests.test_e9_fast_path import _fast, _walk
from tests.test_e15_witnessed_reduce import WALK, _long_world
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    BUY, M, N, NOW, SELL, SHORT, SLUG, _armed, _cancels, _census, _fill, _mkt, _places, _pool,
    _rails_2026_09_06, _short_book, _shorts_on, _tick, _Venue,
)


# --------------------------------------------------------------- KILL pins

def test_r_kill_a_stale_flip_witness_reopens_flow_only(monkeypatch):
    """M11: `_flip_since` drops a witness older than mi.LATE_FILL_S. The
    lane's flip harness, tick 3 at NOW + 1,000 (the witness `at` NOW):
    the reopen is flow-only at 0, never -50 on a 1,000 s old witness."""
    _shorts_on(monkeypatch)
    # his cost on the short axis 0.36 (the lane's reading-alone pin's
    # prices, moved with it on landing: 500 No at 0.52), six cents off the
    # 0.30 mark -- past lane 1's D1 band (3.6c), so the block is never
    # admitted by the catch-up allowance and only the witness can size the reopen
    p, b = _long_world(monkeypatch, _fill(N, "BUY", 500, 0.52, NOW - 10, detected_at=NOW - 8),
                       _fill(M, "SELL", 1_000, 0.30, NOW - 9, detected_at=NOW - 7), net=-500.0)
    p.snap[N], p.snap[M] = 500.0, 0.0
    v1 = _Venue(bid=0.29, ask=0.31, held={SLUG: 100}, ioc_fill=100.0)
    _tick(p, v1, http=_mkt(0.0, 500.0))
    assert b["last_plan"]["flip_witness"]["at"] == NOW
    v2 = _Venue(bid=0.29, ask=0.31, held={SLUG: 0})
    v2.orders = v1.orders
    _tick(p, v2, now=NOW + 30, http=_mkt(0.0, 500.0))
    assert b["state"] == "closed"
    v3 = _Venue(bid=0.29, ask=0.31, held={SLUG: 0})
    v3.orders = v1.orders
    p.snap_at = NOW + 1000 - 40
    _tick(p, v3, now=NOW + 1000, http=_mkt(0.0, 500.0))
    b2 = [x for x in p.books.values() if x["id"] != b["id"]][0]
    assert b2["target"] == 0 and "flip_reopen" not in b2["last_plan"]["catchup"] and not _places(v3)
    assert b2["last_plan"]["catchup"]["why"] == "flow_only"


def test_r_kill_the_per_market_read_this_tick_is_never_explained_by_the_walks_clock(monkeypatch):
    """M14: the drift explanation reads the whole-book walk alone
    (`drift_src == 'book'`). The per-market read is stamped THIS tick:
    a fresh read of 1,000 against fills of 1,200 (his chain add of 200
    inside the walk's age) is `drift`, refused, nothing explained."""
    add = _fill(M, "BUY", 200, 0.31, NOW - 20, detected_at=NOW - 15, source="chain")
    p, b = _long_world(monkeypatch, add, net=1_000.0, ledger=80)
    v = _Venue(bid=0.31, ask=0.33, held={SLUG: 80})
    st = _tick(p, v, http=_mkt(1_000.0))
    lp = b["last_plan"]
    assert lp["drift_src"] == "market" and "drift_fills_explain" not in lp
    assert not _places(v) and _census(st, "drift") == 1 and b["ledger_net"] == 80


def test_r_kill_a_walk_younger_than_one_tick_is_not_explained_even_by_an_add_after_it(monkeypatch):
    """M13: the lane's young-walk pin passes for the wrong reason (its
    add is OLDER than the 10 s walk, so nothing explains it under any
    age rule). A 10 s walk of 1,000 with his chain add of 200 clocked
    AFTER it (fills 1,200): still refused `drift` -- the rule reads the
    walk only when it is older than POLL_S.

    The stamps are the WALL clock's at test time, never the module's
    NOW (fold 2026-09-08): ms.snapshot_sizes measures the walk's age
    against time.time(), and NOW is stamped at import -- inside the
    full suite it is minutes old by the time this pin runs, the "10 s"
    walk read older than one tick, and the add explained it (the rule
    right, the pin wrong: one placement of 40 in the fold's first full
    run)."""
    t0 = time.time()
    add = _fill(M, "BUY", 200, 0.31, t0 - 8, detected_at=t0 - 5, source="chain")
    p, b = _long_world(monkeypatch, add, net=1_000.0, ledger=80, snap_at=t0 - 10)
    v = _Venue(bid=0.31, ask=0.33, held={SLUG: 80})
    st = _tick(p, v, now=t0, http=WALK)
    assert not _places(v) and _census(st, "drift") == 1 and "drift_fills_explain" not in b["last_plan"]


def test_r_kill_an_over_explanation_explains_nothing():
    """M4: adds after the walk LARGER than the rise (300 against a delta
    of 200: the fills also carry a fall the walk has not seen) are not
    an explanation -- the arithmetic must match to the share."""
    from sportsassets.analytics import mirror as mi
    chain = _fill(M, "BUY", 300, 0.31, NOW - 20, detected_at=NOW - 15, source="chain")
    assert mi.drift_explained([chain], M, N, False, 1_200.0, 1_000.0, NOW - 40) is None
    assert mi.drift_explained([chain], M, N, True, -1_200.0, -1_000.0, NOW - 40) is None


def test_r_kill_the_burst_reads_the_references_clock_not_the_ticks(monkeypatch):
    """M20: 278's burst with the reference AFTER both legs (the last plan
    counted them: clock NOW - 5): no burst, no override -- the cover is
    unwitnessed and holds; nothing is named `flip_burst_net`."""
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    p = _pool(fills=[_fill(N, "BUY", 4_980, 0.11, NOW - 3000),
                     _fill(M, "BUY", 4_415, 0.09, NOW - 10, detected_at=NOW - 9),
                     _fill(N, "BUY", 1_414, 0.90, NOW - 9, detected_at=NOW - 8)],
              snap={M: 4_415.0, N: 6_394.0})
    b = _short_book(p, ledger=-400, avg=0.11, ratio=0.10, last_plan={"target": -498, "at": NOW - 5.0})
    v = _Venue(bid=0.09, ask=0.11, held={SLUG: -400})
    _tick(p, v, http=_mkt(4_415.0, 6_394.0))
    lp = b["last_plan"]
    assert "flip_burst_net" not in lp and lp["reduce_unwitnessed"]["cause"] == "fills"
    assert not _places(v) and b["ledger_net"] == -400


def test_r_kill_451s_shape_on_the_short_side_holds_the_ratio_step(monkeypatch):
    """The mirror image of the lane's 451 pin: an exact-copy SHORT of 16
    on his 16 No; he adds 100 No ($70 of collateral > $20): the ratio
    steps, the target reads -11 under our -16 -- and NOTHING is covered:
    no fill of his reduced the leg. Held, named `ratio_stepped`."""
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    p = _pool(fills=[_fill(N, "BUY", 16, 0.61, NOW - 3000),
                     _fill(N, "BUY", 100, 0.61, NOW - 20, detected_at=NOW - 15)],
              snap={M: 0.0, N: 116.0})
    b = _short_book(p, ledger=-16, avg=0.39, ratio=1.0, last_plan={"target": -16, "at": NOW - 30.0})
    v = _Venue(bid=0.38, ask=0.40, held={SLUG: -16})
    st = _tick(p, v, http=_mkt(0.0, 116.0))
    assert b["ratio"] == 0.10 and b["target"] == -11 and b["ledger_net"] == -16
    assert not _places(v) and not _cancels(v) and _census(st, "ratio_stepped") == 1
    assert b["last_plan"]["reduce_unwitnessed"] == {"from": -16, "to": -11, "cause": "ratio_stepped"}


# ------------------------------------------------------------- DEFECT pins

def test_r_defect_h1_the_venue_fresher_than_the_fills_is_refused_drift_not_sized(monkeypatch):
    """HIGH-1 (named either way, as the brief asks): Martinez 12:08-12:12Z
    -- the trade feed LAGS the venue (fills 11,974 vs venue 25,104,
    drift 0.52). The lane sizes an increase on the fills' net only when
    the fills stand ABOVE the walk (mi.drift_explained: delta > 0); the
    mirror image -- the walk above the fills, no fill of his explaining
    it -- is REFUSED `drift` exactly as today, nothing sized on either
    reading. Pinned as it stands: the increase of 20 the fills' own net
    asks for (ledger 80 on his 1,000) does not go out either."""
    p, b = _long_world(monkeypatch, net=1_200.0, ledger=80)
    v = _Venue(bid=0.31, ask=0.33, held={SLUG: 80})
    st = _tick(p, v, http=WALK)
    lp = b["last_plan"]
    assert lp["drift"] > float(rules.MIRROR_DRIFT_MAX) and "drift_fills_explain" not in lp
    assert not _places(v) and _census(st, "drift") == 1 and b["ledger_net"] == 80
    assert b["target"] == 100, "the smaller reading (his fills) sizes the target; the increase is refused"


def test_r_defect_m1_the_deploy_seed_admits_the_landed_rules_unwitnessed_reduce(monkeypatch):
    """MEDIUM-1: a plan written before E15 seeds `reduce_ref` from its
    OWN target. A book whose last landed plan was a reduce sized on a
    lagging reading (target 70 under a ledger of 100, no fill of his
    reducing the leg) reads target 70 against reference 70 -- no fall --
    and the reduce of 30 GOES OUT on the first E15 tick: the landed
    rule's sale, admitted once by the seed. Pinned as it stood; FOLDED
    2026-09-08: a prior plan of kind `reduce` with no reference seeds
    the reference at the LEDGER (100, the prior plan's own clock) --
    hold what we hold -- so the fall to 70 is unwitnessed, held and
    named `reading`; nothing is sold until his reducing fill lands."""
    p, b = _long_world(monkeypatch, net=700.0, last_plan={"kind": "reduce", "target": 70, "at": NOW - 30.0})
    v = _Venue(bid=0.30, ask=0.32, held={SLUG: 100}, ioc_fill=30.0)
    _tick(p, v, http=WALK)
    lp = b["last_plan"]
    assert lp["reduce_ref"] == {"target": 100, "at": NOW - 30.0}, "seeded at the ledger, the prior plan's clock"
    assert lp["reduce_unwitnessed"] == {"from": 100, "to": 70, "cause": "reading"}
    assert not _places(v) and b["ledger_net"] == 100 and b["last_reason"] == "reduce_unwitnessed"


def test_r_defect_m2_the_fills_axis_override_also_sizes_an_increase_past_the_fresh_reading(monkeypatch):
    """MEDIUM-2: the override (a reducing fill of his after the reference
    -> `net_sized = fills_net_all`) is not scoped to the reduce it was
    built for. He buys 300 and sells 100 as we watch (fills 1,200); the
    per-market read this tick says 1,150 (drift 4.2 %, inside the 5 %
    gate: increases allowed, sized on the READING under the landed
    rule -> 115). Now the burst overrides the reading and the increase
    is 20 to 120 -- 5 shares past the venue's own count of him. Pinned
    as it stood; FOLDED 2026-09-08: the override is scoped to the reduce
    it was built for (the target on the fills' net at or under the
    ledger on the leg); the fills' 120 asks for an INCREASE, so the
    reading keeps the plan: target 115, the increase of 15. The burst
    is still named on the plan."""
    p, b = _long_world(monkeypatch, _fill(M, "BUY", 300, 0.31, NOW - 20, detected_at=NOW - 15),
                       _fill(M, "SELL", 100, 0.31, NOW - 10, detected_at=NOW - 5), net=1_150.0)
    v = _Venue(bid=0.31, ask=0.33, held={SLUG: 100})
    _tick(p, v, http=_mkt(1_150.0))
    lp = b["last_plan"]
    assert lp["flip_burst_net"] == {"adds": 300.0, "reductions": 100.0, "net": 200.0}
    assert lp["net"] == 1_150.0 and lp["drift"] < float(rules.MIRROR_DRIFT_MAX)
    assert b["target"] == 115 and [c[3] for c in _places(v)] == [15], "the reading's increase, never the fills' 20"
    assert "reduce_unwitnessed" not in lp


def test_r_the_fast_tick_explains_the_walks_drift_the_same_way(monkeypatch):
    """The fast tick is the same function AND the same walk stamp: the
    woken book's tick reads the walk through _snapshot, so
    `snap_read_at` is stamped and his chain add explains the drift --
    the increase of 20 goes out on the fast path exactly as on the
    full tick (or, if the stamp is absent, refuses: this pin says which)."""
    add = _fill(M, "BUY", 200, 0.31, NOW - 20, detected_at=NOW - 15, source="chain")
    p, b = _long_world(monkeypatch, add, net=1_000.0)
    _walk({SLUG: 100})
    v = _Venue(bid=0.31, ask=0.33, held={SLUG: 100})
    _fast(p, v, http=WALK)
    lp = b["last_plan"]
    assert "drift_fills_explain" in lp and b["target"] == 120
    assert [c[3] for c in _places(v)] == [20]
