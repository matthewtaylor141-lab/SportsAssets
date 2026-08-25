"""Coverage is the whole game, and two bugs were eating it.

The owner's argument, and it is correct: copy verified-profitable
accounts faithfully at same-or-better prices and our per-dollar return
converges to theirs. Missing some trades adds variance, not bias — the
sample washes it out. What breaks the argument is not missing SOME
trades; it is missing 99.45% of them.

Measured fill rate: 0.55%. One trade in two hundred.

No sizing rule and no exit rule touches that number. These two do.

    NOSLUG          22,330 rejected rows, 22,327 with no token at all
                    = 971 permanently dead copy attempts per day
    VANISHED EXITS  every FULL exit — the only kind these whales
                    actually make — silently discarded
"""

import asyncio
import inspect

from sportsassets.ingestion import pipeline
from sportsassets.workers import whale_exits as we


class TestEnrichmentLands:
    """The chain leg sees a fill before anyone knows what market it
    belongs to, so it writes condition_id / outcome / outcome_index
    NULL. The hourly enrichment pass fetches exactly those fields and
    re-INSERTs the same dedupe_key — and DO NOTHING dropped it. Every
    hour. A row with a NULL condition_id can never map, never resolve a
    sibling, and never be classified as an entry or an exit."""

    def test_the_conflict_clause_fills_holes(self):
        src = inspect.getsource(pipeline)
        assert "ON CONFLICT (dedupe_key) DO UPDATE SET" in src
        assert "ON CONFLICT (dedupe_key) DO NOTHING" not in src

    def test_it_can_only_ever_fill_a_hole_never_overwrite(self):
        """COALESCE keeps the FIRST non-null value, so a late pass
        cannot rewrite what the chain observed."""
        src = inspect.getsource(pipeline)
        for col in ("condition_id", "outcome", "outcome_index",
                    "market_title", "market_slug", "event_slug"):
            assert f"COALESCE(trades.{col}," in src, col

    def test_the_money_fields_are_not_updatable_at_all(self):
        """price, size, side and notional are what money was staked
        against. A re-ingest must not be able to move them."""
        src = inspect.getsource(pipeline)
        upd = src[src.index("DO UPDATE SET"):src.index("RETURNING id")]
        for forbidden in ("price", "size", "side", "notional",
                          "whale_id", "asset"):
            assert f"{forbidden} =" not in upd, forbidden

    def test_a_refill_cannot_fire_a_duplicate_copy(self):
        """THE TRAP IN THIS FIX. `row is None` WAS the entire duplicate
        test; DO UPDATE returns a row every time, so without the insert
        flag every hourly enrichment pass would republish and re-copy a
        fill we already acted on. Buying coverage with duplicate orders
        is not a trade worth making."""
        src = inspect.getsource(pipeline)
        assert "(xmax = 0) AS was_insert" in src
        assert 'if not row["was_insert"]:' in src

    def test_the_duplicate_guard_precedes_the_fanout(self):
        src = inspect.getsource(pipeline)
        assert src.index('if not row["was_insert"]:') < src.index(
            "publish(CH_TRADES_NEW")

    def test_an_enrichment_still_returns_the_id(self):
        """The row is real and callers want it — only the fan-out is
        suppressed."""
        src = inspect.getsource(pipeline)
        i = src.index('if not row["was_insert"]:')
        assert "return trade_id" in src[i:i + 120]


class TestAVanishedPositionIsAnExit:
    """The old rule skipped disappearances because a resolved market
    also vanishes. The caution was right; treating "could be either" as
    "always resolution" was not — it discarded exactly the case the
    feature exists for.

    swisstony holds below purchase on 62 of 75 positions, ferrari on 18
    of 23. Roughly 83% of their positions get exited, and every one that
    reached zero was invisible."""

    def test_a_vanished_unresolved_position_is_a_full_exit(self):
        assert we.diff_exits({"A": 100.0}, {}, set()) == [("A", 1.0)]

    def test_a_vanished_RESOLVED_position_is_still_skipped(self):
        """Mirroring a resolution would sell a position the venue is
        about to settle for us."""
        assert we.diff_exits({"A": 100.0}, {}, {"A"}) == []

    def test_shrinks_still_work_as_before(self):
        assert we.diff_exits({"A": 100.0}, {"A": 40.0}, set()) == [
            ("A", 0.6)]

    def test_growth_is_not_an_exit(self):
        assert we.diff_exits({"A": 100.0}, {"A": 150.0}, set()) == []

    def test_noise_below_the_floor_is_still_ignored(self):
        assert we.diff_exits({"A": 100.0}, {"A": 99.0}, set()) == []

    def test_a_missing_resolved_set_is_treated_as_the_old_behaviour(self):
        """Called without the set, nothing is known to be resolved — so
        a disappearance reads as an exit. The CALLER is responsible for
        passing a real set, and the caller's failure path is tested
        below."""
        assert we.diff_exits({"A": 100.0}, {}) == [("A", 1.0)]


class TestTheCallerFailsTowardSafety:
    def test_a_failed_resolution_lookup_skips_every_disappearance(self):
        """An empty resolved set would turn every settlement into a sell
        against a position the venue already closed. The query failing
        must forfeit coverage, not risk orders."""
        src = inspect.getsource(we._cycle)
        assert "resolved = None" in src
        assert "diff_exits(prev, now, set(gone))" in src

    def test_it_only_asks_about_positions_that_vanished(self):
        src = inspect.getsource(we._cycle)
        assert "gone = [a for a in prev if a not in now]" in src
        assert "if gone:" in src

    def test_it_asks_the_markets_table_not_a_guess(self):
        src = inspect.getsource(we._cycle)
        assert "COALESCE(m.resolved, false) = true" in src
