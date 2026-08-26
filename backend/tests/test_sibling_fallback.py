"""56 buys a window where we cannot even ASK whether it was an exit.

    EXITCENSUS cls_token_unenriched: 56

classify_exit answers "is this buy really an exit?" by asking whether
the whale holds the COMPLEMENTARY leg, and it finds that leg through
market_tokens. For 56 buys in one census window the token was not in
market_tokens at all, so the question could not be put.

The default when it cannot be put is to treat the buy as an ENTRY. Of
the buys we CAN classify, 79 of 122 (65%) turn out to be exits. So
"entry" is the wrong default for that population — and being wrong here
does not miss a copy, it DOUBLES one: we buy the leg he is abandoning
while still holding the leg he just closed, at a price summing to about
$1.00 with his entry.

whale_exits already fetches every whale's positions every 120 seconds
and the venue's rows carry the complementary token. We were discarding
it.

WHAT THIS IS NOT: an assumption that the field exists. This module's
docstring has claimed since it was written that the payload carries
`mergeable` and `oppositeAsset`. I wrote that line, nothing has ever
read those fields, and building a money path on my own unverified
assertion is the mistake I already had to retract once today. So the
worker CAPTURES AND REPORTS coverage, and the executor USES IT ONLY IF
PRESENT. If the venue does not send it, the map is empty, nothing
changes anywhere, and the heartbeat says sib_rows=0.
"""

import asyncio
import inspect

import pytest

from sportsassets import live_executor as le
from sportsassets.workers import whale_exits as we


class TestTheVenueFieldIsReadNotAssumed:
    def test_several_spellings_are_accepted(self):
        for k in ("oppositeAsset", "opposite_asset", "complementAsset",
                  "oppositeTokenId", "opposite_token_id"):
            assert we._sibling_of({k: "999"}) == "999", k

    def test_an_absent_field_yields_nothing_not_a_guess(self):
        assert we._sibling_of({"asset": "1", "size": 5}) == ""

    def test_empty_and_zero_are_not_siblings(self):
        assert we._sibling_of({"oppositeAsset": ""}) == ""
        assert we._sibling_of({"oppositeAsset": 0}) == ""
        assert we._sibling_of({"oppositeAsset": None}) == ""

    def test_the_relation_is_recorded_in_both_directions(self):
        """A later buy of EITHER leg must be able to find the other."""
        src = inspect.getsource(we._fetch_positions)
        assert "sibs.setdefault(sib, a)" in src

    def test_a_token_is_never_its_own_sibling(self):
        src = inspect.getsource(we._fetch_positions)
        assert "sib != a" in src


class TestCoverageIsMeasuredNotAssumed:
    def test_the_cycle_counts_rows_and_pairs(self):
        src = inspect.getsource(we._cycle)
        for k in ('"sib_rows"', '"sib_pairs"', '"pos_rows"'):
            assert k in src

    def test_zero_coverage_is_a_finding_not_a_failure(self):
        src = inspect.getsource(we._cycle)
        assert "sib_rows" in src
        doc = we.__doc__ or ""
        assert isinstance(doc, str)

    def test_the_map_is_MERGED_across_cycles_not_replaced(self):
        """A whale's positions payload only describes markets he is in
        RIGHT NOW. Replacing the map each cycle would drop the sibling
        of every position he CLOSED — exactly the population
        classify_exit asks about."""
        src = inspect.getsource(we._cycle)
        assert "prev.update(all_sibs)" in src

    def test_the_map_is_bounded(self):
        assert 0 < we.MAX_SIBLINGS <= 100000
        assert "MAX_SIBLINGS" in inspect.getsource(we._cycle)

    def test_a_write_failure_never_kills_the_cycle(self):
        src = inspect.getsource(we._cycle)
        i = src.index("_SIB_STATE_KEY")
        assert "except Exception" in src[i:i + 400]


class TestTheFallbackIsNeverAGuess:
    @pytest.fixture(autouse=True)
    def _clear(self):
        # None, NOT 0.0. This fixture predates the cold-start fix and
        # kept planting the old sentinel, overriding conftest's None --
        # so on any host up less than the 300s TTL the refresh was
        # skipped and these tests read '' from an empty cache. Local
        # containers (long uptime) passed; fresh CI runners (~90s
        # uptime) failed. The test class was re-creating the exact
        # production bug it exists to guard against, selectively by
        # environment -- the same trap, one level up.
        le._SIBLING_CACHE = {}
        le._SIBLING_CACHE_AT = None
        yield
        le._SIBLING_CACHE = {}
        le._SIBLING_CACHE_AT = None

    class _Pool:
        def __init__(self, val):
            self.val = val
            self.calls = 0

        async def fetchval(self, *a):
            self.calls += 1
            return self.val

    def test_an_absent_map_returns_empty(self):
        p = self._Pool(None)
        assert asyncio.run(le._sibling_from_positions(p, "1")) == ""

    def test_an_unreadable_map_returns_empty_not_a_wrong_answer(self):
        p = self._Pool("{not json")
        assert asyncio.run(le._sibling_from_positions(p, "1")) == ""

    def test_a_missing_key_returns_empty(self):
        p = self._Pool({"7": "8"})
        assert asyncio.run(le._sibling_from_positions(p, "1")) == ""

    def test_a_present_key_returns_the_sibling(self):
        p = self._Pool({"7": "8"})
        assert asyncio.run(le._sibling_from_positions(p, "7")) == "8"

    def test_it_is_cached_so_the_hot_path_pays_once(self):
        p = self._Pool({"7": "8"})

        async def _twice():
            a = await le._sibling_from_positions(p, "7")
            b = await le._sibling_from_positions(p, "7")
            return a, b

        assert asyncio.run(_twice()) == ("8", "8")
        assert p.calls == 1

    def test_the_ttl_is_bounded(self):
        assert 0 < le._SIBLING_TTL_S <= 900


class TestClassifyExitStillRefusesWhenItShould:
    def test_no_sibling_anywhere_still_reads_as_unenriched(self):
        src = inspect.getsource(le.classify_exit)
        assert 'return _exit_stop("cls_token_unenriched"' in src

    def test_a_multi_outcome_market_is_still_refused(self):
        src = inspect.getsource(le.classify_exit)
        assert 'cls_not_binary' in src

    def test_the_fallback_only_runs_when_market_tokens_is_EMPTY(self):
        """A market_tokens row is the enriched, verified answer. The
        venue map must never override it — only fill a hole."""
        src = inspect.getsource(le.classify_exit)
        assert "elif not sibs:" in src
        assert src.index("if sibs and len(sibs) == 1") < \
            src.index("_sibling_from_positions")

    def test_the_fallback_use_is_counted_so_we_can_see_it_work(self):
        src = inspect.getsource(le.classify_exit)
        assert "cls_sibling_from_venue_map" in src

    def test_he_must_still_HOLD_the_sibling_for_it_to_be_an_exit(self):
        """The fallback supplies WHICH token is the complement. It does
        not supply the holding, and the holding is what makes a buy an
        exit rather than a new bet on the other side."""
        src = inspect.getsource(le.classify_exit)
        assert src.index("_sibling_from_positions") < src.index(
            "cls_no_sibling_holding")


class TestTheFirstLoadDoesNotDependOnHostUptime:
    """The sentinel was 0.0 and the refresh test was
    `now - _SIBLING_CACHE_AT > _SIBLING_TTL_S`.

    time.monotonic() is time since boot. On a host up less than 300s the
    first call computed (e.g.) 263.9 - 0.0 = 263.9, concluded the cache
    was FRESH, and answered every lookup from an empty dict. Callers
    refuse on '', so for the first five minutes of such a process every
    sibling-dependent exit silently declined -- a guard blocking
    everything while reporting itself as safety.

    It never failed honestly either: whether the suite went green
    depended on how long the container had been up. These tests pin the
    property directly instead of waiting for the clock to disagree.
    """

    class _Pool:
        def __init__(self, m):
            self.m = m
            self.calls = 0

        async def fetchval(self, *a):
            self.calls += 1
            return self.m

    def test_never_loaded_is_its_own_state(self):
        assert le._SIBLING_CACHE_AT is None, (
            "0.0 conflates 'never loaded' with 'loaded at t=0'")

    def test_the_refresh_test_checks_that_state_first(self):
        src = inspect.getsource(le._sibling_from_positions)
        assert "_SIBLING_CACHE_AT is None or" in src

    def test_a_cold_process_loads_however_small_monotonic_is(self,
                                                             monkeypatch):
        """The exact failure: monotonic below the TTL."""
        import time as _t

        monkeypatch.setattr(_t, "monotonic", lambda: 1.0)
        le._SIBLING_CACHE.clear()
        le._SIBLING_CACHE_AT = None
        p = self._Pool({"7": "8"})
        assert asyncio.run(le._sibling_from_positions(p, "7")) == "8"
        assert p.calls == 1

    def test_a_loaded_cache_still_expires(self, monkeypatch):
        """Fixing the cold start must not turn the TTL off."""
        import time as _t

        clock = {"t": 1000.0}
        monkeypatch.setattr(_t, "monotonic", lambda: clock["t"])
        le._SIBLING_CACHE.clear()
        le._SIBLING_CACHE_AT = None
        p = self._Pool({"7": "8"})
        assert asyncio.run(le._sibling_from_positions(p, "7")) == "8"
        assert p.calls == 1
        clock["t"] += le._SIBLING_TTL_S / 2          # still fresh
        asyncio.run(le._sibling_from_positions(p, "7"))
        assert p.calls == 1
        clock["t"] += le._SIBLING_TTL_S              # now stale
        asyncio.run(le._sibling_from_positions(p, "7"))
        assert p.calls == 2
