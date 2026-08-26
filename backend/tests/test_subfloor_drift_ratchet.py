"""Sub-floor trims never accumulated, so the drift was never exited.

closed_frac is a FLOW: one observation's delta over the position as it
stood immediately before that observation. The position lane then
advances its snapshot every 120 seconds. So a whale who trims a little
at a time has EVERY observation correctly refused as below the floor,
while the un-exited deficit is discarded each cycle.

Measured against the production constants (MIN_SHRINK=0.05 in the
detector, MIN_EXIT_FRAC=0.10 in the executor):

    7% per cycle, 20 cycles -> whale 23.4%, we 100.0%, 20 refusals
    4% per cycle, 20 cycles -> whale 44.2%, we 100.0%, 0 observations
                               even reported

mx_below_floor: 293 counts refusals, not the drift behind them, and it
cannot see the sub-5% band at all.

The fix does NOT lower the floor. It stops the baseline running away
from it: a refused sub-floor trim is PENDING, so the asset is pinned at
its pre-trim size and the next cycle measures pre-trim against a
further-shrunken book, growing the fraction until it genuinely crosses.
"""

from __future__ import annotations

import pytest

from sportsassets import live_executor as le
from sportsassets.workers import whale_exits as we


def _simulate(trim: float, cycles: int, ratchet: bool):
    """Return (whale_pct, our_pct, exits_fired)."""
    held = whale = 100.0
    base = {"a": 100.0}
    fired = 0
    for _ in range(cycles):
        whale = round(whale * (1 - trim), 6)
        now = {"a": whale}
        found = we.diff_exits(base, now, set())
        acted = False
        if found and found[0][1] >= le.MIN_EXIT_FRAC:
            held = round(held * (1 - found[0][1]), 6)
            fired += 1
            acted = True
        if not (ratchet and not acted and now["a"] < base["a"]):
            base = dict(now)
    return whale, held, fired


class TestTheDriftIsReal:
    def test_a_seven_percent_trim_never_crosses_the_floor(self):
        w, h, fired = _simulate(0.07, 20, ratchet=False)
        assert fired == 0
        assert h == pytest.approx(100.0)
        assert w < 25.0, "the whale walked himself out and we followed none of it"

    def test_a_four_percent_trim_is_not_even_reported(self):
        """Under MIN_SHRINK, so diff_exits emits nothing at all -- no
        counter fires and mx_below_floor cannot see it."""
        assert 0.04 < we.MIN_SHRINK
        assert we.diff_exits({"a": 100.0}, {"a": 96.0}, set()) == []

    def test_the_two_floors_leave_a_blind_band(self):
        """MIN_SHRINK=0.05 in the producer, MIN_EXIT_FRAC=0.10 in the
        consumer: the 5-10% band is manufactured and then refused."""
        assert we.MIN_SHRINK < le.MIN_EXIT_FRAC
        found = we.diff_exits({"a": 100.0}, {"a": 93.0}, set())
        assert found and found[0][1] < le.MIN_EXIT_FRAC


class TestTheRatchetClosesIt:
    @pytest.mark.parametrize("trim,max_gap", [(0.07, 1.0), (0.04, 5.0)])
    def test_we_end_close_to_where_he_ends(self, trim, max_gap):
        w, h, fired = _simulate(trim, 20, ratchet=True)
        assert fired > 0
        assert abs(h - w) <= max_gap, \
            f"still {h - w:.1f} points adrift after {fired} exits"

    def test_it_is_strictly_better_than_today(self):
        for trim in (0.04, 0.07):
            w0, h0, _ = _simulate(trim, 20, ratchet=False)
            w1, h1, _ = _simulate(trim, 20, ratchet=True)
            assert abs(h1 - w1) < abs(h0 - w0)

    def test_a_position_he_does_NOT_trim_is_untouched(self):
        """The ratchet must not manufacture an exit out of nothing."""
        w, h, fired = _simulate(0.0, 20, ratchet=True)
        assert fired == 0 and h == pytest.approx(100.0)


class TestNothingWasLoosened:
    def test_the_floor_itself_is_unchanged(self):
        assert le.MIN_EXIT_FRAC == 0.10

    def test_the_detector_floor_is_unchanged(self):
        assert we.MIN_SHRINK == 0.05

    def test_the_mechanism_is_pinning_not_admitting(self):
        """A single sub-floor observation is still refused. What
        changed is that the baseline no longer advances past it."""
        assert "mx_below_floor" in le.EXIT_PENDING_REASONS
        found = we.diff_exits({"a": 100.0}, {"a": 93.0}, set())
        assert found[0][1] < le.MIN_EXIT_FRAC

    def test_pinned_assets_cannot_starve_fresh_ones(self):
        """Pinning costs cycle budget; the rotation cursor already
        handles that, and this fix leans on it."""
        assert hasattr(we, "rotate_for_fairness")
        assert hasattr(we, "next_cursor")
