"""A trim too small to buy a whole share was an exit we threw away.

Owner, 2026-08-26: "We need to capture exits, based on the case study I
did, RN1 has the largest edge on dollar spend (when sells are included)
we need to capitalize on as much of that as possible."

`qty = int(ours * closed_frac)` truncates. A 4-share remainder against a
20% trim computed int(0.8) = 0, and the qty<=0 branch then returned
mx_venue_holds_nothing -- a reason that says the venue holds NOTHING
while the venue was holding all four shares. Two defects stacked:

  * the exit was dropped, and mx_venue_holds_nothing is not a pending
    reason, so whale_exits ADVANCED past it and the drift was gone for
    good; and
  * the census counted it as a ledger/venue disagreement, so the one
    instrument that could have shown this was pointing at the wrong
    thing. An instrument that cannot see its subject is the failure
    mode that has cost the most here.

Remainders are exactly where this bites: sell 80% of a 20-share
position, 4 shares are left, and every subsequent partial rounds to
nothing.
"""

from __future__ import annotations

import inspect

from sportsassets import live_executor as le
from sportsassets.api import app as api_app


class TestTheArithmetic:
    def test_a_small_remainder_no_longer_truncates_to_nothing(self):
        assert int(4 * 0.20 + 0.5) == 1
        assert int(4 * 0.20) == 0, "the old rule, kept here as the contrast"

    def test_the_overshoot_is_bounded_by_one_share(self):
        for ours in (1, 3, 7, 40, 263, 500):
            for frac in (0.10, 0.25, 0.333, 0.5, 0.66, 0.94):
                q = int(ours * frac + 0.5)
                assert q - ours * frac < 0.5 + 1e-9
                assert 0 <= q <= ours

    def test_it_is_half_up_not_bankers(self):
        """round() is banker's: round(2.5) is 2 and round(3.5) is 4. A
        sizing rule whose answer depends on the parity of the share
        count is not a rule."""
        assert round(10 * 0.25) == 2          # what round() would do
        assert int(10 * 0.25 + 0.5) == 3      # what we do
        assert int(14 * 0.25 + 0.5) == 4

    def test_production_uses_the_half_up_form(self):
        src = inspect.getsource(le.mirror_exit)
        assert "int(ours * closed_frac + 0.5)" in src
        assert "qty = int(ours * closed_frac)\n" not in src


class TestTheReason:
    def _zero_branch(self) -> str:
        src = inspect.getsource(le.mirror_exit)
        head = src.index("qty = int(ours * closed_frac + 0.5)")
        block = src[head:]
        return block[:block.index("# FULL EXIT")]

    def test_rounding_to_zero_is_not_reported_as_an_empty_venue(self):
        block = self._zero_branch()
        assert "mx_exit_rounds_to_zero" in block
        assert "if ours > 1:" in block
        # and the rounding reason is decided FIRST, so the false one
        # cannot claim the case
        assert (block.index("mx_exit_rounds_to_zero")
                < block.index("mx_venue_holds_nothing"))

    def test_the_true_empty_venue_reason_survives(self):
        """ours == 0 really is a ledger/venue disagreement and must keep
        its own name."""
        assert "mx_venue_holds_nothing" in self._zero_branch()

    def test_it_is_pending_so_the_ratchet_can_accumulate(self):
        """Not pending means whale_exits advances the snapshot and the
        un-exited drift is discarded -- the same defect that hid the
        sub-floor trims."""
        assert "mx_exit_rounds_to_zero" in le.EXIT_PENDING_REASONS

    def test_the_census_can_name_it(self):
        src = inspect.getsource(api_app)
        assert "mx_exit_rounds_to_zero" in src

    def test_a_single_share_is_not_pinned(self):
        """No partial can round one share up -- it would need 50%, and a
        1-share position at that fraction is what FULL_EXIT_FRAC
        flattens. Pinning it would hold a retry slot that can never
        resolve: a guard that blocks forever while reporting itself as
        safety."""
        assert int(1 * 0.49 + 0.5) == 0
        assert "if ours > 1:" in self._zero_branch()

    def test_the_claim_is_released_before_either_return(self):
        """A held 'exiting' row that never sells is a position stranded
        outside both the copy path and the exit path."""
        block = self._zero_branch()
        assert block.index("_release_exit_claim") < block.index(
            "mx_exit_rounds_to_zero")


class TestTheRatchetConverges:
    def test_repeated_trims_eventually_cross_one_share(self):
        """Pinning is only worth anything if the pinned asset resolves.
        Cumulative fraction against a pinned pre-trim size, 4 shares,
        a whale trimming 7% an observation."""
        ours, cum, sold = 4, 0.0, 0
        for _ in range(20):
            cum = 1 - (1 - cum) * (1 - 0.07)
            if cum < le.MIN_EXIT_FRAC:
                continue
            q = int(ours * cum + 0.5)
            if q > 0:
                sold = q
                break
        assert sold >= 1, "the ratchet never crosses a whole share"

    def test_the_real_loss_was_a_reason_that_advanced_the_snapshot(self):
        """The honest statement of the defect, after checking my own
        first draft of it.

        I first wrote that truncation "never crossed a whole share".
        That is false: under a ratchet the cumulative fraction reaches
        1/4 and int(4 * 0.252) is 1. Truncation crosses at 25% where
        half-up crosses at 12.5% -- later, not never.

        The loss was never the arithmetic on its own. It was that the
        truncated case returned mx_venue_holds_nothing, which is NOT a
        pending reason, so whale_exits advanced the snapshot and there
        was no cumulative fraction to reach anything. A single 20% trim
        on a 4-share remainder -- above MIN_EXIT_FRAC, so the sub-floor
        ratchet never sees it either -- was simply discarded.
        """
        assert int(4 * 0.252) == 1, "truncation does cross, eventually"
        assert 0.20 >= le.MIN_EXIT_FRAC, (
            "if it were sub-floor the below-floor ratchet would catch it")
        assert "mx_venue_holds_nothing" not in le.EXIT_PENDING_REASONS
        assert "mx_exit_rounds_to_zero" in le.EXIT_PENDING_REASONS

    def test_half_up_crosses_sooner_than_truncation(self):
        """Which is the whole benefit, stated at its real size."""
        ours = 4
        first_halfup = min(f for f in (i / 1000 for i in range(1, 1000))
                           if int(ours * f + 0.5) > 0)
        first_trunc = min(f for f in (i / 1000 for i in range(1, 1000))
                          if int(ours * f) > 0)
        assert first_halfup < first_trunc
        assert first_halfup <= 0.13 and first_trunc >= 0.25
