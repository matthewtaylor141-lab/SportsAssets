"""Exit detection from positions, because these whales never sell.

The evidence that produced this worker:

    SELLTRUTH swisstony  the data API's own trade feed: 0 sells
    SIDES     swisstony  860,326 buys / 0 sells across all three sources
    POSTRUTH  swisstony  62 of 75 held positions BELOW what he bought
    POSTRUTH  ferrari    18 of 23

The owner said these accounts take profit before settlement; our data
said they had never sold once. Both were true — they close by MERGING
complementary outcomes, which is not a trade and appears in no feed.
The venue's positions payload names the mechanism itself: it carries
`mergeable` and `oppositeAsset`.

diff_exits is pure so the rule can be tested without a venue, and the
cases it REFUSES matter more than the ones it catches.
"""

import pytest

from sportsassets.workers import whale_exits as we


class TestOnlyShrinksCount:
    def test_a_shrink_is_an_exit_with_its_fraction(self):
        assert we.diff_exits({"a": 100.0}, {"a": 25.0}) == [("a", 0.75)]

    def test_a_full_close_that_still_appears_reads_as_100_percent(self):
        assert we.diff_exits({"a": 100.0}, {"a": 0.0}) == [("a", 1.0)]

    def test_growth_is_not_an_exit(self):
        """An increase is a new entry; the normal copy path owns that."""
        assert we.diff_exits({"a": 100.0}, {"a": 180.0}) == []

    def test_unchanged_is_not_an_exit(self):
        assert we.diff_exits({"a": 100.0}, {"a": 100.0}) == []


class TestWhatItDeliberatelyRefuses:
    def test_a_vanished_asset_is_skipped(self):
        """Could be an exit, could be a resolved market. Resolution
        settles our copy on its own, and mirroring it would try to sell
        a position that no longer exists — so a disappearance is never
        treated as an exit."""
        assert we.diff_exits({"a": 100.0}, {}) == []

    def test_a_new_asset_is_not_an_exit(self):
        assert we.diff_exits({}, {"a": 50.0}) == []

    def test_noise_is_below_the_floor(self):
        """Rounding in the venue's size field must not fire an order."""
        assert we.diff_exits({"a": 100.0}, {"a": 99.0}) == []

    def test_a_zero_prior_cannot_divide(self):
        assert we.diff_exits({"a": 0.0}, {"a": 0.0}) == []


class TestTheFirstSnapshotIsSilent:
    def test_the_cycle_skips_a_whale_with_no_prior_state(self):
        """Diffing against nothing reads every holding as a fresh exit.
        On first run that would fire a full close on every position we
        hold — the single most expensive possible bug in this file."""
        import inspect

        src = inspect.getsource(we._cycle)
        assert "if not prev:" in src
        assert "first_snapshots" in src
        # and it must save BEFORE deciding, so a crash mid-cycle cannot
        # replay the same diff next time
        assert src.index("_save(pool") < src.index("if not prev:")


class TestTheEmittedEvent:
    def test_it_carries_the_measured_fraction(self):
        import inspect

        src = inspect.getsource(we._cycle)
        assert '"side": "SELL"' in src
        assert '"closed_frac": frac' in src

    def test_mirror_exit_accepts_a_supplied_fraction(self):
        """The ledger path can never fire for these whales — `sold` is 0
        across 860k trades — so the fraction has to come from the
        position diff."""
        import inspect

        from sportsassets import live_executor as le

        src = inspect.getsource(le.mirror_exit)
        assert 'payload.get("closed_frac")' in src

    def test_a_bad_fraction_refuses_rather_than_guessing(self):
        import inspect

        from sportsassets import live_executor as le

        src = inspect.getsource(le.mirror_exit)
        assert "except (TypeError, ValueError):" in src

class TestTheFirstActiveCycleIsBounded:
    """swisstony holds less than he bought on 62 of 75 positions.

    The first cycle with a previous snapshot to diff against would
    otherwise fire 62 real sell orders back to back — from a worker
    written the same night, on a night where two of my confident fixes
    turned out to do nothing at all. A brand-new component that places
    real orders must not be able to place sixty before anyone sees the
    first.

    The remainder is not lost: the position still reads below its
    recorded size next cycle, so it is picked up two minutes later.
    This bounds the blast radius of a bug, not the work.
    """

    def test_the_cap_is_small(self):
        assert 0 < we.MAX_EXITS_PER_CYCLE <= 25

    def test_the_cycle_slices_to_the_cap(self):
        import inspect

        src = inspect.getsource(we._cycle)
        assert "found[:MAX_EXITS_PER_CYCLE]" in src

    def test_the_overflow_is_reported_not_silently_dropped(self):
        """A silent cap reads as 'only 10 exits happened', which is the
        kind of quiet truncation that made other numbers tonight lie."""
        import inspect

        src = inspect.getsource(we._cycle)
        assert "deferred" in src
        assert "the rest still read as shrunk next cycle" in src

    def test_swisstonys_62_would_be_spread_not_fired_at_once(self):
        found = [(f"a{i}", 0.5) for i in range(62)]
        assert len(found[:we.MAX_EXITS_PER_CYCLE]) == we.MAX_EXITS_PER_CYCLE
        assert len(found) - we.MAX_EXITS_PER_CYCLE == 52
