"""Cutting a whale made his open positions un-exitable.

mirror_exit gated on LIVE_VERIFIED_WHALES -- an ENTRY gate -- applied to
the EXIT path. The moment a whale leaves the roster, every position we
already copied from him becomes permanently unsellable: we hold it to
resolution against a whale who has already left it. That is the exact
divergence the exit leg exists to close, and it fires on every roster
change rather than once.

Live right now for four cut whales: homerunhazard, swisstony, 0x2c33 and
kch123.

Selling cannot create exposure. mirror_exit only reduces a position, and
its row query scopes to a live_orders row already 'filled' for that whale
and asset -- there is nothing to sell unless we genuinely bought it from
him.

Second defect in the same file, and this one was mine. The partial-full-
exit branch returns mx_partial_full_exit, a PENDING reason, so the
position lane brings the residual round again. The ledger INSERT sat
ABOVE that return, so the retry hit mx_exit_recently_applied (position
lane, inside EXIT_DEDUP_MINUTES) or mx_exit_already_mirrored (trade lane)
and was refused -- and mx_exit_recently_applied is SETTLED, so the
snapshot advanced and the residual was lost. Both halves shipped within
an hour of each other and the retry was never once reachable.
"""

from __future__ import annotations

import inspect

import pytest

from sportsassets import live_executor as le


class TestACutWhalesPositionCanStillBeSold:
    def test_mirror_exit_uses_the_named_set(self):
        """Read production's own definition, not a copy rebuilt here.
        The first version of these tests rebuilt the set and did not
        fail when the bug was restored."""
        src = inspect.getsource(le.mirror_exit)
        assert "exitable_whales()" in src

    def test_the_set_is_strictly_wider_than_the_verified_set(self):
        verified = le._whale_set("LIVE_VERIFIED_WHALES")
        assert le.exitable_whales() > verified, \
            "the exit gate is the entry gate again"

    @pytest.mark.parametrize("whale", sorted(le.COPY_CUT_WHALES))
    def test_every_cut_whale_is_still_exitable(self, whale):
        assert whale in le.exitable_whales(), \
            f"{whale} was cut and his open copies cannot be sold"

    def test_every_verified_whale_is_still_exitable(self):
        for w in le._whale_set("LIVE_VERIFIED_WHALES"):
            assert w in le.exitable_whales()

    def test_a_whale_never_rostered_is_still_refused(self):
        """Not opened to any string -- the leak was cut whales, not
        unknown ones."""
        assert "somebody_we_never_copied" not in le.exitable_whales()

    def test_exiting_a_cut_whale_is_counted(self):
        """A standing count means we are still unwinding a cut book,
        which should not be silent."""
        src = inspect.getsource(le.mirror_exit)
        assert "mx_exit_of_cut_whale" in src

    def test_it_is_recorded_not_refused(self):
        """The census note must not be a `return`."""
        src = inspect.getsource(le.mirror_exit)
        i = src.index("mx_exit_of_cut_whale")
        line = src[:i].rsplit("\n", 1)[-1] + src[i:].split("\n", 1)[0]
        assert "return" not in line


class TestTheResidualRetryIsActuallyReachable:
    def test_the_partial_return_precedes_the_ledger_write(self):
        src = inspect.getsource(le.mirror_exit)
        assert src.index('return _exit_done("mx_partial_full_exit"') \
            < src.index("RECORD IT, now that a sale actually happened"), \
            "recording the exit as applied makes its own retry refuse"

    def test_an_incomplete_exit_writes_no_ledger_row(self):
        """The whole point: an unrecorded incomplete exit is exactly
        what should be re-offered."""
        src = inspect.getsource(le.mirror_exit)
        head = src[:src.index('return _exit_done("mx_partial_full_exit"')]
        assert "INSERT INTO copy_exit_applied" not in head

    def test_a_COMPLETE_exit_still_records(self):
        src = inspect.getsource(le.mirror_exit)
        tail = src[src.index('return _exit_done("mx_partial_full_exit"'):]
        assert tail.count("INSERT INTO copy_exit_applied") == 2, \
            "both lanes must still record a finished exit"

    def test_the_branch_exists_exactly_once(self):
        """It was moved, not duplicated."""
        src = inspect.getsource(le.mirror_exit)
        assert src.count('return _exit_done("mx_partial_full_exit"') == 1

    def test_it_is_still_a_pending_reason(self):
        assert "mx_partial_full_exit" in le.EXIT_PENDING_REASONS

    def test_the_reason_that_would_block_the_retry_is_settled(self):
        """mx_exit_recently_applied being SETTLED is correct -- it is
        why writing the ledger early was fatal rather than merely
        slow."""
        assert "mx_exit_recently_applied" not in le.EXIT_PENDING_REASONS
