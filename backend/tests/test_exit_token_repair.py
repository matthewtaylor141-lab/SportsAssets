"""1,337 exit signals were discarded for want of one venue call.

classify_exit tells an exit from a fresh bet by finding the SIBLING
token -- the complementary leg -- through a market_tokens self-join. When
that join is empty it fell back to the sibling map whale_exits publishes,
and if that missed too it gave up: cls_token_unenriched, 1,337 of them,
as many as we successfully classified.

Both of those sources are OUR OWN caches. The venue could always answer:
gamma.lookup_token_live issues GET /markets?clob_token_ids=<tid>&limit=1,
parse_market reads clobTokenIds -- BOTH legs -- plus conditionId, and
upsert_market writes every token into market_tokens, the exact table the
join reads. It was already production-tested and already running on the
ingestion path. It had simply never been called on the exit path.

WHY THE TOKENS WERE MISSING rather than merely late: a trade arriving
with its own condition_id is stamped enriched_at at INSERT, so it never
calls Gamma and its market never enters market_tokens. The trade-driven
repair lane then selected WHERE enriched_at IS NULL -- permanently
excluding exactly those rows. The lane built to repair that population
was the one thing that could not see it.
"""

from __future__ import annotations

import inspect

from sportsassets import live_executor as le
from sportsassets.ingestion import pipeline


def _code(fn) -> str:
    """Source with comments stripped.

    These assertions are about what the CODE does. Three of them first
    windowed from the first mention of `lookup_token_live`, which is in
    the comment explaining the fix, so they read prose instead of the
    call -- the same instrument-cannot-see-its-subject mistake this
    repository keeps paying for.
    """
    return "\n".join(l for l in inspect.getsource(fn).splitlines()
                      if not l.strip().startswith("#"))


class TestClassifyExitAsksTheVenue:
    def test_it_calls_lookup_token_live(self):
        assert "_gamma.lookup_token_live(" in _code(le.classify_exit)

    def test_it_re_runs_the_join_afterwards(self):
        """The call's value is that upsert_market persists both legs --
        so the join, not the call's return, is what resolves it."""
        src = _code(le.classify_exit)
        i = src.index("_gamma.lookup_token_live(")
        after = src[i:]
        assert "JOIN market_tokens s USING (condition_id)" in after

    def test_it_is_bounded(self):
        """This is the copy hot path."""
        src = _code(le.classify_exit)
        i = src.index("_gamma.lookup_token_live(")
        assert "asyncio.wait_for" in src[max(0, i - 200):i]
        assert le.TOKEN_LOOKUP_TIMEOUT_S <= 5.0

    def test_a_failure_falls_through_to_the_old_fallback(self):
        """Best-effort: the venue map remains exactly as it was."""
        src = _code(le.classify_exit)
        after = src[src.index("_gamma.lookup_token_live("):]
        assert "_sibling_from_positions" in after
        assert "cls_token_unenriched" in after

    def test_it_still_refuses_when_nothing_answers(self):
        """A wrong sibling SELLS THE WRONG THING. No answer means no
        classification, which is the pre-existing contract."""
        src = inspect.getsource(le.classify_exit)
        assert 'return _exit_stop("cls_token_unenriched"' in src

    def test_ambiguity_still_refuses(self):
        """Exactly one sibling or nothing -- a multi-outcome market has
        no single complement and must not be guessed at."""
        src = _code(le.classify_exit)
        i = src.index("_gamma.lookup_token_live(")
        assert "len(_re) == 1" in src[i:]

    def test_the_new_source_is_named_in_the_census(self):
        """Otherwise there is no way to tell whether it ever helped."""
        assert "cls_sibling_from_gamma" in inspect.getsource(le.classify_exit)


class TestTheBackfillCanSeeItsOwnPopulation:
    def test_it_is_keyed_on_market_tokens_coverage(self):
        src = inspect.getsource(pipeline)
        assert "LEFT JOIN market_tokens mt ON mt.token_id = t.asset" in src
        assert "WHERE mt.token_id IS NULL" in src

    def test_it_no_longer_selects_on_enriched_at(self):
        """enriched_at is stamped at INSERT for pre-enriched trades, so
        that selector excluded the very rows needing repair."""
        # CODE only: the comment above the fix quotes the old selector
        # on purpose, to record what it used to be. And enriched_at is
        # still legitimately WRITTEN at upsert -- what must not survive
        # is selecting the repair population on it.
        assert "enriched_at IS NULL" not in _code(
            pipeline.backfill_unenriched)

    def test_dead_tokens_are_still_excluded_in_the_SELECT(self):
        """Pre-existing invariant: they were CLOGGING the window."""
        src = inspect.getsource(pipeline)
        assert "NOT (t.asset = ANY($2::text[]))" in src

    def test_it_groups_by_asset(self):
        """One token is traded many times; one repair fixes them all,
        so the window should not be spent on duplicates."""
        src = inspect.getsource(pipeline)
        assert "GROUP BY t.asset" in src

    def test_the_reported_backlog_matches_the_selector(self):
        """A backlog counting a different population than the lane
        drains is how a queue looks stuck while it is working."""
        src = inspect.getsource(pipeline)
        i = src.index("SELECT count(*) FROM (SELECT DISTINCT t.asset")
        assert "WHERE mt.token_id IS NULL" in src[i:i + 300]
