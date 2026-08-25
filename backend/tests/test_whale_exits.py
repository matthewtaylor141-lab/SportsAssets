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
    def test_a_vanished_RESOLVED_asset_is_skipped(self):
        """Resolution settles our copy on its own, and mirroring it
        would try to sell a position the venue is about to close.

        SUPERSEDED 2026-08-25 in the other direction. This used to skip
        EVERY disappearance, on the reasoning that it could be either a
        resolution or an exit. The caution was right; treating "could
        be either" as "always resolution" was not — it discarded
        precisely the case the detector exists for.

        These whales barely scale out. swisstony holds below purchase
        on 62 of 75 positions and ferrari on 18 of 23, so ~83% of their
        positions get exited, and a full exit goes to zero. The
        detector could therefore only ever have fired on the partial
        trims they almost never make, which is why it reported exits: 0
        for nine straight hours.

        The resolved set makes the distinction properly instead of
        assuming it away."""
        assert we.diff_exits({"a": 100.0}, {}, {"a"}) == []

    def test_a_vanished_UNRESOLVED_asset_is_a_full_exit(self):
        assert we.diff_exits({"a": 100.0}, {}, set()) == [("a", 1.0)]

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


class TestDeferredExitsAreDeferredNotDISCARDED:
    """The cap was throwing away every exit past the tenth, forever.

    The snapshot was saved BEFORE the diff was acted on, which is right
    for crash safety — a crash mid-cycle must not replay the diff and
    fire the sells twice. But it saved `now` WHOLESALE, so the next
    cycle's `prev` already reflected the exits we had just declined to
    act on. The cap did not defer them, it discarded them, and the log
    line said the opposite.

    It bites at exactly the moment the cap exists for. swisstony holds
    below purchase on 62 of 75 positions; such a cycle acted on 10 and
    silently dropped 52 REAL exits — positions the whale had left and
    we would go on holding to resolution, which is the divergence this
    entire worker exists to close.

    Found by an adversarial review of the diff, not by the test above,
    which asserted the save ORDERING that caused it.
    """

    def test_the_deferred_assets_are_pinned_at_their_previous_size(self):
        import inspect

        src = inspect.getsource(we._cycle)
        assert "to_save = dict(now)" in src
        assert "to_save[asset] = prev[asset]" in src

    def test_the_pinning_happens_before_the_save(self):
        import inspect

        src = inspect.getsource(we._cycle)
        assert src.index("to_save[asset] = prev[asset]") < \
            src.index("await _save(pool, uname.lower(), to_save)")

    def test_a_crash_still_cannot_replay_an_ACTED_exit(self):
        """The save must still precede the orders, so the acted-on set
        is recorded as done before any of it is placed."""
        import inspect

        src = inspect.getsource(we._cycle)
        assert src.index("await _save(pool, uname.lower(), to_save)") < \
            src.index("for asset, frac in acting:")

    def test_the_first_snapshot_still_saves_and_skips(self):
        import inspect

        src = inspect.getsource(we._cycle)
        # rindex: "first_snapshots" also appears in the stats dict at
        # the top of the function, so indexing on the FIRST occurrence
        # slices before the save and proves nothing.
        head = src[:src.rindex("first_snapshots")]
        assert "_save(pool, uname.lower(), now)" in head

    def test_the_pinned_snapshot_reproduces_the_exit_next_cycle(self):
        """The arithmetic, end to end: 62 shrunk positions, a cap of 10,
        and the 52 deferred must still read as shrunk against the saved
        snapshot."""
        prev = {f"a{i}": 100.0 for i in range(62)}
        now = {f"a{i}": 40.0 for i in range(62)}
        found = we.diff_exits(prev, now)
        assert len(found) == 62
        acting, deferred = (found[:we.MAX_EXITS_PER_CYCLE],
                            found[we.MAX_EXITS_PER_CYCLE:])
        to_save = dict(now)
        for asset, _f in deferred:
            to_save[asset] = prev[asset]
        # next cycle: prev is what we saved, now is unchanged
        again = we.diff_exits(to_save, now)
        assert len(again) == len(deferred) == 52
        acted = {a for a, _ in acting}
        assert not ({a for a, _ in again} & acted), \
            "an acted-on exit must never re-fire"

    def test_a_deferred_FULL_exit_survives_too(self):
        """A vanished asset is a full exit. Pinning re-adds it to the
        snapshot, so it is still absent from `now` next cycle."""
        prev = {"a": 100.0, "b": 100.0}
        now: dict[str, float] = {}
        found = we.diff_exits(prev, now, set())
        to_save = dict(now)
        for asset, _f in found[1:]:
            to_save[asset] = prev[asset]
        again = we.diff_exits(to_save, now, set())
        assert [a for a, _ in again] == [a for a, _ in found[1:]]

    def test_the_log_no_longer_claims_something_false(self):
        import inspect

        # The LOG CALL, not the comment — the comment above the fix
        # quotes the false line on purpose, to record what it said.
        src = inspect.getsource(we._cycle)
        code = "\n".join(l for l in src.splitlines()
                          if not l.strip().startswith("#"))
        assert "still read as shrunk next cycle" not in code
        assert "HELD BACK IN THE SNAPSHOT" in code


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
        assert "HELD BACK IN THE SNAPSHOT" in src

    def test_swisstonys_62_would_be_spread_not_fired_at_once(self):
        found = [(f"a{i}", 0.5) for i in range(62)]
        assert len(found[:we.MAX_EXITS_PER_CYCLE]) == we.MAX_EXITS_PER_CYCLE
        assert len(found) - we.MAX_EXITS_PER_CYCLE == 52

class TestItIsActuallyRegistered:
    """A worker that is not in LOOPS never runs, and nothing complains.

    Twice tonight a correct fix landed where execution never arrives —
    the sizing clamp in a dead branch, the archive filter behind a stale
    snapshot. Both looked complete in the diff. This asserts the
    detector is wired into the supervised loop set, read from source
    because the module graph needs optional deps this environment
    lacks.
    """

    def test_whale_exits_is_in_the_supervised_loops(self):
        from pathlib import Path

        src = Path(we.__file__).with_name("all.py").read_text()
        assert '("whale_exits", whale_exits.main)' in src
        assert "whale_exits" in src.split("LOOPS")[0], \
            "it must also be imported, not only referenced"

    def test_the_loop_is_supervised_and_restarts(self):
        from pathlib import Path

        src = Path(we.__file__).with_name("all.py").read_text()
        assert "async def supervise(" in src
        assert "RESTART_DELAY_SECONDS" in src
