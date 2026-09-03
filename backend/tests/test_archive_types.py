"""The API was OOM-killed holding rows it never reads.

Render killed sportsassets-api on a Pro instance. Measured RSS ran
1,032-1,762 MB against 317,681 archived rows.

build() consumes exactly two activity types — it filters to
ACTIVITY_TYPE_TRADE at one call site and ACTIVITY_TYPE_POSITION_RESOLUTION
at the other. Everything else the account ever produced (deposits,
conversions, order lifecycle events) was fetched from Postgres,
JSON-parsed, slimmed into a dict, and held for the life of the process
WITHOUT EVER BEING READ.

Two fixes, both about not keeping what cannot be used:

  * the hydration cursor filters on payload->>'type', so those rows are
    never fetched, never parsed, never retained.
  * _archived_ids seeded every id ever — 317,681 strings — to skip
    re-serializing known rows. The venue's activity feed is a sliding
    ~1,200-row window, so an old id can never reappear. It is now
    seeded from a week, and ON CONFLICT DO NOTHING remains the real
    guard: a miss costs one wasted serialization, never a duplicate.

The TABLE still holds full fidelity. Only the working set shrank.

The danger with a filter like this is silent drift: build() grows a
third dependency, the filter does not, and the record quietly loses
rows. These tests fail if the code and the constant disagree.
"""

import inspect

from sportsassets.api import track_record as tr


class TestTheFilterMatchesWhatBuildReads:
    def test_the_constant_names_both_types(self):
        assert set(tr.ARCHIVE_TYPES) == {
            "ACTIVITY_TYPE_TRADE", "ACTIVITY_TYPE_POSITION_RESOLUTION"}

    def test_every_type_build_checks_is_in_the_filter(self):
        """The anti-drift test. Scan the module for activity types the
        code actually branches on and assert the filter covers them —
        so adding a dependency without widening ARCHIVE_TYPES fails
        here rather than silently dropping rows from the record."""
        import re

        src = inspect.getsource(tr)
        found = set(re.findall(r'"(ACTIVITY_TYPE_[A-Z_]+)"', src))
        missing = found - set(tr.ARCHIVE_TYPES)
        assert not missing, (
            f"build() branches on {missing} but the hydration filter "
            f"drops them — the record would silently lose those rows")

    def test_slim_still_handles_both(self):
        trade = {"id": "a", "type": "ACTIVITY_TYPE_TRADE", "timestamp": 1,
                 "trade": {"marketSlug": "s", "qty": 2, "price": 0.5,
                           "side": "BUY", "realizedPnl": 1.0}}
        res = {"id": "b", "type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
               "timestamp": 2, "positionResolution": {
                   "marketSlug": "s", "beforePosition": {},
                   "afterPosition": {}}}
        assert tr._slim(trade)["trade"]["marketSlug"] == "s"
        assert "positionResolution" in tr._slim(res)


class TestTheHydrationActuallyFilters:
    def test_the_cursor_filters_on_type(self):
        src = inspect.getsource(tr)
        assert "payload->>'type' = ANY($2::text[])" in src, (
            "the filter must run in SQL — filtering in Python would "
            "still fetch and parse every row, which is the cost")

    def test_the_filter_uses_the_shared_constant(self):
        src = inspect.getsource(tr)
        assert "list(ARCHIVE_TYPES)" in src


class TestTheIdSeedIsBounded:
    def test_the_seed_is_windowed(self):
        src = inspect.getsource(tr)
        assert "WHERE ts IS NULL OR ts > $1" in src
        assert "_ARCHIVED_ID_WINDOW_S" in src

    def test_the_window_is_far_wider_than_the_venue_feed(self):
        """The feed pages ~1,200 rows. A week is orders of magnitude
        more than can scroll back into view."""
        assert tr._ARCHIVED_ID_WINDOW_S >= 24 * 3600

    def test_a_miss_cannot_duplicate_a_row(self):
        """The set is an optimization; ON CONFLICT is the guard."""
        src = inspect.getsource(tr)
        assert "ON CONFLICT" in src

class TestTheSnapshotIsVersionedWithTheFilter:
    """The filter shipped and changed NOTHING for a full deploy cycle.

    _hydrate_all short-circuits on a complete, fresh snapshot and
    returns its rows without touching the table — so boot kept loading
    317,681 rows written before the filter existed, and the filtered
    cursor was never reached. archive_rows stayed at 317,681 and RSS at
    1.6-1.9 GB with the fix deployed.

    A snapshot is only valid for the ARCHIVE_TYPES that produced it, so
    the key has to version with them. This is the same shape as the
    proportional-sizing miss earlier tonight: correct code placed where
    execution never arrives.
    """

    def test_the_key_is_v2(self):
        # v2 retired the pre-filter snapshots. v3 (2026-09-02) retired the
        # snapshots whose slim rows carry no resolution time, and was
        # ROLLED BACK on 2026-09-03: the full re-hydrate (531,313 rows vs
        # the v2 snapshot's 302,901) put the API process in the memory
        # band where its heavy endpoints answer 502. The key returns to
        # v3 only with a memory-safe archive (see the comment above
        # _SNAP_KEY).
        assert tr._SNAP_KEY.endswith("_v2"), (
            "the v3 re-hydrate does not fit the API process; see the "
            "rollback note above _SNAP_KEY before bumping again")

    def test_the_key_carries_a_version_at_all(self):
        import re

        assert re.search(r"_v\d+$", tr._SNAP_KEY), (
            "an unversioned snapshot key cannot express that the "
            "meaning of its contents changed")

    def test_the_short_circuit_still_exists(self):
        """Not arguing the fast path away — it is what keeps boot cheap.
        The point is only that it must not serve rows from a different
        filter generation."""
        import inspect

        src = inspect.getsource(tr._hydrate_all)
        assert "snap[\"complete\"]" in src
