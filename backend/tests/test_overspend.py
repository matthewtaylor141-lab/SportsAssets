"""The per-fill clip caps what we ASK to spend, not what the venue takes.

`plan_order` bounds requested_usd = shares * limit, and every sizing
test in the suite pins that bound. None of them can see the other half:
what the venue actually charged. The 24h aggregate on 2026-08-25 read
avg_filled $363.32 against avg_req $249.73 on a live, verified whale —
1.45x the authorized clip — which under the code as written should be
impossible, since an IOC buy cannot fill above its own limit.

Either the venue can do it or the number is broken. Both are defects,
and asserting which one without measuring is how the wrong-side
incident survived two weeks. These pin the detector that decides.
"""

import pytest

from sportsassets.live_executor import (
    OVERSPEND_TOLERANCE, is_overspend, overspend_ratio,
)


class TestRatio:
    def test_a_fill_at_the_limit_consumes_exactly_the_clip(self):
        # 781 contracts @ 0.32 = $249.92 against a $249.92 request
        assert overspend_ratio(249.92, 781, 0.32) == 1.0

    def test_a_better_fill_consumes_less(self):
        assert overspend_ratio(250.0, 781, 0.30) == pytest.approx(0.9372,
                                                                 abs=1e-4)

    def test_a_partial_fill_consumes_less(self):
        assert overspend_ratio(250.0, 400, 0.32) == pytest.approx(0.512,
                                                                 abs=1e-4)

    def test_the_observed_anomaly_is_flagged(self):
        # avg_req $249.73 -> avg_filled $363.32 is ratio 1.455
        assert overspend_ratio(249.73, 1135, 0.32) == pytest.approx(
            1.4545, abs=1e-3)

    def test_nothing_to_judge_reads_none_not_zero(self):
        """A None ratio must never be mistaken for 'within budget' by a
        caller doing `ratio < 1` — hence None, not 0.0."""
        assert overspend_ratio(0, 100, 0.5) is None
        assert overspend_ratio(250.0, 0, 0.5) is None
        assert overspend_ratio(250.0, 100, None) is None
        assert overspend_ratio(None, 100, 0.5) is None


class TestVerdict:
    def test_an_honest_fill_is_not_an_overspend(self):
        assert is_overspend(249.92, 781, 0.32) is False
        assert is_overspend(250.0, 781, 0.30) is False
        assert is_overspend(250.0, 400, 0.32) is False

    def test_the_venue_taking_more_than_authorized_trips(self):
        assert is_overspend(249.73, 1135, 0.32) is True
        assert is_overspend(250.0, 1000, 0.30) is True

    def test_whole_unit_rounding_does_not_cry_wolf(self):
        """A whole-cent limit against a whole-contract count leaves sub-
        cent dust; the tolerance absorbs it and nothing else."""
        assert is_overspend(100.0, 100, 1.005) is False
        assert is_overspend(100.0, 100, 1.02) is True

    def test_the_tolerance_is_a_cent_not_a_licence(self):
        """A wide tolerance would quietly authorize real overspend — pin
        it so a future edit has to argue with this test."""
        assert OVERSPEND_TOLERANCE == 1.01

    def test_no_fill_is_never_an_overspend(self):
        assert is_overspend(250.0, 0, None) is False
        assert is_overspend(250.0, 0, 0.32) is False
