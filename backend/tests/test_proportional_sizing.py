"""Mirror the whale's size — the switch to proportional copying.

Owner order 2026-08-25: "cut it in half and make the switch to ensure
proportional trades and sells."

Halving the turnover target to 0.5x daily gives, at the measured 0.92%
fill rate, a ratio of 1.004 — i.e. take the size he takes. So the
policy is a mirror, bounded by the per-clip cap.

WHY A CLAMP RATHER THAN A DEFAULT. The config default is already
0.001, and production still placed $249.92 against a $3.46 trade —
72x — which needs an effective ratio above 70. The env overrides the
config and cannot be read from here, so the code refuses to exceed
COPY_RATIO_MAX whatever the env says. A clamp can only make a copy
smaller, which is why it is safe to apply blind.

The six real rows from 2026-08-24, before and after:

    his $3.46    was $249.92  (72.2x)  -> skipped, under the dust floor
    his $4.98    was $249.92  (50.2x)  -> skipped, under the dust floor
    his $29.04   was $249.60   (8.6x)  -> $29.04
    his $47.58   was $249.75   (5.2x)  -> $47.58
    his $98.69   was $249.78   (2.5x)  -> $98.69
    his $2907.32 was $249.75   (0.1x)  -> $250.00, the cap
"""

import pytest

from sportsassets.live_executor import (
    COPY_MIN_CLIP_USD, COPY_RATIO_MAX, plan_order,
)


def _usd(his_notional, his_price=0.50, ratio=1.0, cap=250.0):
    _limit, usd, _shares = plan_order(his_price, his_notional, ratio, cap,
                                      1.0, whole_units=True)
    return usd


class TestTheClampCannotBeBeatenByEnv:
    def test_a_runaway_env_ratio_is_clamped_to_a_mirror(self):
        """The production value was above 70. Whatever it is, it cannot
        make us bigger than he is."""
        assert _usd(100.0, ratio=72.0) <= 100.0 + 1e-9
        assert _usd(100.0, ratio=1000.0) <= 100.0 + 1e-9

    def test_a_small_env_ratio_is_still_honoured(self):
        """The clamp is a ceiling, not a floor — it must not INFLATE a
        deliberately conservative setting."""
        assert _usd(1000.0, ratio=0.10) == pytest.approx(100.0, abs=1.0)

    def test_the_ceiling_is_a_mirror(self):
        assert COPY_RATIO_MAX == 1.0


class TestTheSixRealRows:
    """(his_notional, his_price) from the receipts."""

    ROWS = [(3.46, 0.2286), (4.98, 0.3207), (29.04, 0.4825),
            (47.58, 0.3715), (98.69, 0.2331), (2907.32, 0.4523)]

    def test_no_copy_exceeds_his_own_size(self):
        for notional, price in self.ROWS:
            got = _usd(notional, his_price=price)
            assert got <= notional + 1e-9, (
                f"his ${notional} -> our ${got}: we must never be "
                f"bigger than the whale")

    def test_the_probes_fall_under_the_dust_floor(self):
        for notional, price in self.ROWS[:2]:
            assert _usd(notional, his_price=price) < COPY_MIN_CLIP_USD

    def test_his_conviction_trade_is_bounded_by_the_cap(self):
        got = _usd(2907.32, his_price=0.4523)
        assert got == pytest.approx(250.0, abs=1.0)

    def test_the_middle_rows_now_track_him(self):
        for notional, price in self.ROWS[2:5]:
            got = _usd(notional, his_price=price)
            assert got == pytest.approx(notional, rel=0.02), (
                f"his ${notional} should be mirrored, got ${got}")


class TestTheCapStillBinds:
    """The per-clip cap is a money gate the owner set explicitly. The
    proportional switch must not quietly raise it."""

    def test_no_ratio_can_exceed_the_cap(self):
        assert _usd(10_000.0, ratio=1.0, cap=250.0) <= 250.0

    def test_the_cap_wins_over_the_mirror(self):
        assert _usd(5_000.0, cap=250.0) == pytest.approx(250.0, abs=1.0)


class TestTheDustFloor:
    def test_the_floor_is_ten_dollars(self):
        assert COPY_MIN_CLIP_USD == 10.0

    def test_it_only_ever_removes_orders(self):
        """A floor must never round an order UP into existence."""
        assert _usd(4.0, his_price=0.25) < COPY_MIN_CLIP_USD
