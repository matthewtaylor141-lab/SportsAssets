"""The reward calculation, pinned against the venue's own worked example.

THE DOCUMENTATION SUPPLIES A WORKED EXAMPLE and it is the only external
check available, so it is used as one. With a Discount Factor of 0.30
and 1,000 contracts at each of four price levels:

    at best   0.30^0 x 1000 = 1000     ->  1000 / 1417 = 70.6%
    1 tick    0.30^1 x 1000 =  300
    2 ticks   0.30^2 x 1000 =   90
    3 ticks   0.30^3 x 1000 =   27     ->    27 / 1417 =  1.9%

Two same-level regression tests exist because my first implementation
walked ORDERS rather than PRICE LEVELS, which excluded us from our own
price whenever a competitor happened to be listed first -- the exact
same-level-exclusion error the module was written to correct.

Run:  python -m pytest research/beta48/test_bettor_incentive_score.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bettor_incentive_score as inc                           # noqa: E402

DOC = inc.Program("doc-example", "p", "daily_event",
                  reward_pool=100.0, discount_factor=0.30, target_size=1)


def test_the_venues_own_worked_example_reproduces():
    """1000 at each of four levels, DF 0.30 -> 70.6% and 1.9%."""
    prog = inc.Program("x", "p", "daily_event", 100.0, 0.30, target_size=4000)
    levels = [(0.50, 1000.0), (0.49, 1000.0), (0.48, 1000.0), (0.47, 1000.0)]
    ok, total, scored = inc.side_scores(levels, "BID", prog)
    assert ok
    assert round(total, 1) == 1417.0
    assert round(scored[0][2] / total, 3) == 0.706     # at the best price
    assert round(scored[3][2] / total, 3) == 0.019     # three ticks away


def test_discount_compounds_per_tick():
    """Ticks are measured from the BEST PRICE IN THE BOOK, so the test
    book must contain that best price -- a single level is always its
    own best and would always score at 0 ticks."""
    prog = inc.Program("x", "p", "daily_event", 100.0, 0.30, target_size=4000)
    levels = [(0.50, 1000.0), (0.49, 1000.0), (0.48, 1000.0), (0.47, 1000.0)]
    _, _, scored = inc.side_scores(levels, "BID", prog)
    got = {round(px, 2): round(sc, 1) for px, _, sc in scored}
    assert got == {0.50: 1000.0, 0.49: 300.0, 0.48: 90.0, 0.47: 27.0}


# ── same-level participation: the regression that matters ────────────

def test_a_same_level_competitor_does_not_exclude_us():
    """A competitor resting Target Size AT OUR OWN PRICE shares the
    level with us. Our share falls; it does not become zero."""
    prog = inc.Program("x", "p", "daily_event", 50.0, 0.25, target_size=500)
    r = inc.snapshot_share([(0.50, 500.0)], "BID", prog, 0.50, 100)
    assert r["qualifies"]
    assert abs(r["share"] - 100.0 / 600.0) < 1e-9, r


def test_order_within_a_level_cannot_change_the_answer():
    """If the walk were by ORDER rather than by LEVEL, listing the
    competitor first would exclude us from our own price."""
    prog = inc.Program("x", "p", "daily_event", 50.0, 0.25, target_size=500)
    a = inc.snapshot_share([(0.50, 400.0), (0.49, 300.0)], "BID",
                           prog, 0.50, 100)
    b = inc.snapshot_share([(0.49, 300.0), (0.50, 400.0)], "BID",
                           prog, 0.50, 100)
    assert abs(a["share"] - b["share"]) < 1e-12
    assert a["share"] > 0.0


def test_worse_price_exclusion_is_real_and_separate():
    """Target Size met at STRICTLY BETTER prices does exclude us."""
    prog = inc.Program("x", "p", "daily_event", 50.0, 0.25, target_size=500)
    r = inc.snapshot_share([(0.51, 600.0)], "BID", prog, 0.50, 100)
    assert r["qualifies"] is True          # the side qualifies
    assert r["share"] == 0.0               # but we are outside the walk


# ── our own order changes the book ───────────────────────────────────

def test_we_can_create_eligibility_in_a_thin_market():
    prog = inc.Program("x", "p", "daily_event", 50.0, 0.25, target_size=500)
    r = inc.snapshot_share([(0.50, 450.0)], "BID", prog, 0.50, 100)
    assert r["created_eligibility"] is True
    assert abs(r["share"] - 100.0 / 550.0) < 1e-9


def test_a_side_below_target_size_does_not_qualify_at_all():
    prog = inc.Program("x", "p", "daily_event", 50.0, 0.25, target_size=500)
    r = inc.snapshot_share([(0.50, 100.0)], "BID", prog, 0.50, 100)
    assert r["qualifies"] is False
    assert r["share"] == 0.0


# ── a snapshot is not a day ──────────────────────────────────────────

def test_a_single_snapshot_is_never_multiplied_by_a_pool():
    """One snapshot at 20% share must not pay 20% of the pool when the
    side qualifies in only half the period."""
    prog = inc.Program("x", "p", "daily_event", 50.0, 0.25, target_size=500)
    snaps = ([{"BID": [(0.50, 400.0)], "ASK": []}] * 50
             + [{"BID": [(0.50, 10.0)], "ASK": []}] * 50)
    out = inc.period_reward(snaps, prog, {"BID": (0.50, 100), "ASK": None})
    assert out["qualifying_side_snapshots"] == 50
    assert out["qualifying_uptime"] == 0.25          # 50 of 2 x 100
    assert out["reward_gross"] < 50.0 * 0.2 + 1e-9


def test_the_two_allocation_assumptions_can_disagree():
    """They diverge when our share differs between the sides. Reporting
    only one of them would be reporting an undocumented choice as fact."""
    prog = inc.Program("x", "p", "daily_event", 50.0, 0.25, target_size=500)
    snaps = ([{"BID": [(0.50, 600.0)], "ASK": [(0.52, 900.0)]}] * 20
             + [{"BID": [(0.50, 600.0)], "ASK": [(0.52, 50.0)]}] * 80)
    q = {"BID": (0.50, 100), "ASK": (0.52, 100)}
    a = inc.period_reward(snaps, prog, q, allocation=inc.ALLOC_POOLED)
    b = inc.period_reward(snaps, prog, q, allocation=inc.ALLOC_PER_SIDE)
    assert a["reward_gross"] != b["reward_gross"]


def test_uptime_is_reported_not_assumed():
    prog = inc.Program("x", "p", "daily_event", 50.0, 0.25, target_size=500)
    snaps = [{"BID": [(0.50, 600.0)], "ASK": [(0.52, 600.0)]}] * 10
    out = inc.period_reward(snaps, prog, {"BID": (0.50, 100),
                                          "ASK": (0.52, 100)})
    assert out["qualifying_uptime"] == 1.0
    assert out["snapshots_seen"] == 10
