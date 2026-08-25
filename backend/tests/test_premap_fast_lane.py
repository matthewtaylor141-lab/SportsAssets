"""The fast lane: the same sweep, aimed narrowly and run often.

WHY IT EXISTS. The unmapped census attributed 400 sampled mapping
misses to six causes. Two of them, together 36.6%, are the same gap
wearing different names:

    resolves                    101/400 (25.3%)  the row maps NOW but
                                                 did not when he filled
    type_prefix_filter_emptied   45/400 (11.3%)  the event was captured
                                                 but that market type
                                                 was not yet

The full sweep walks up to 40 pages over now-12h..now+96h every 1800
seconds. A market listed shortly before tip-off is therefore invisible
to the executor for up to half an hour — which is exactly the half hour
in which these whales trade it. Nothing was broken; the calendar was
simply read too rarely near the moment it matters.

WHAT THE FAST LANE IS NOT. It is not a second way to build a premap
row. The wrong-side incident was caused by a resolution path that could
invent a side, and the property that makes premap safe is that every
row comes from the venue's own expansion of the market it belongs to.
`fast_refresh` is `refresh` with different aim — same `_market_rows`,
same `_upsert`, same everything. These tests pin that, and pin the four
authorities the narrow lane is deliberately denied: the unwindowed
variant rungs, the markets fallback, the prune, and the `premap_last`
state key.
"""

import asyncio
import inspect

import pytest

from sportsassets.workers import premap


EV = {"slug": "atc-mlb-nyy-bos-2026-08-25-nyy",
      "title": "New York Yankees vs. Boston Red Sox",
      "markets": [{"slug": "atc-mlb-nyy-bos-2026-08-25-nyy",
                   "question": "New York Yankees vs. Boston Red Sox",
                   "marketSides": [
                       {"identifier": "atc-mlb-nyy-bos-2026-08-25-nyy",
                        "description": "Yankees"},
                       {"identifier": "atc-mlb-nyy-bos-2026-08-25-bos",
                        "description": "Red Sox"}]}]}


class _Harness:
    """A venue and a database that record what was asked of them."""

    def __init__(self, *, pages=1, live=True):
        self.queries = []          # every events.list query
        self.market_calls = []     # the degraded fallback
        self.writes = []           # identifiers upserted
        self.deletes = []          # prune statements
        self.state = {}            # ingestion_state key -> payload
        self._pages = pages
        self._live = live

    # --- venue -------------------------------------------------------
    def _events_list(self, q):
        self.queries.append(dict(q))
        if not self._live:
            # a board with no live inline markets: the "junk catalog"
            return {"events": [{"slug": "old-2025", "title": "Old",
                                "markets": [{"slug": "old", "closed": True}]}]}
        n = len(self.queries) - 1        # probe is call 0
        if n > self._pages:
            return {"events": []}
        return {"events": [EV] * premap.PAGE_LIMIT}

    def _markets_list(self, q):
        self.market_calls.append(dict(q))
        return {"markets": []}

    def client(self):
        h = self

        class _E:
            def list(self, q):
                return h._events_list(q)

        class _M:
            def list(self, q):
                return h._markets_list(q)

        class _C:
            events, markets = _E(), _M()

        return _C()

    # --- database ----------------------------------------------------
    def pool(self):
        h = self

        class _P:
            async def execute(self, sql, *a):
                if "us_premap" in sql and "INSERT" in sql:
                    h.writes.append(a[0])
                elif "DELETE FROM us_premap" in sql:
                    h.deletes.append(sql)
                elif "ingestion_state" in sql:
                    h.state[a[0]] = a[1]
                return "DELETE 0"

            async def fetchval(self, *a):
                return None

            async def fetch(self, *a):
                return []

        return _P()

    def install(self, monkeypatch):
        monkeypatch.setattr(premap.pmus, "_get_client", lambda: self.client())
        monkeypatch.setattr(premap, "get_pool",
                            lambda: asyncio.sleep(0, result=self.pool()))
        monkeypatch.setattr(premap, "_ensure_table",
                            lambda pool: asyncio.sleep(0))
        monkeypatch.setattr(premap, "LIST_PACING_S", 0.0)
        return self


class TestItIsTheSameSweep:
    """Not a parallel implementation — the identical code path."""

    def test_fast_refresh_delegates_to_refresh(self):
        src = inspect.getsource(premap.fast_refresh)
        assert "return await refresh(" in src, \
            "the fast lane must call refresh, not reimplement it"
        # and nothing else: no row building of its own. Read the CODE,
        # not the docstring — which names both on purpose.
        code = src.replace(premap.fast_refresh.__doc__ or "", "")
        assert "_market_rows" not in code
        assert "_upsert" not in code

    def test_there_is_exactly_one_row_builder_call_site(self):
        """Wrong-side-by-construction holds because every premap row is
        produced by _market_rows from the venue's own side expansion.
        Two builders would be two chances to invent a side."""
        src = inspect.getsource(premap)
        # inside refresh: the events path and the markets fallback.
        # live_rows_for_market is the side-echo verifier, not a writer.
        bodies = inspect.getsource(premap.refresh)
        assert bodies.count("_market_rows(") == 2
        assert src.count("await _upsert(") == 2

    def test_the_rows_written_carry_the_venues_own_identifiers(self,
                                                              monkeypatch):
        h = _Harness().install(monkeypatch)
        asyncio.run(premap.fast_refresh())
        assert h.writes, "the fast lane writes rows"
        assert set(h.writes) <= {"atc-mlb-nyy-bos-2026-08-25-nyy",
                                 "atc-mlb-nyy-bos-2026-08-25-bos"}, \
            "identifiers come from the venue's marketSides, not from us"


class TestTheWindowIsNarrowAndRealised:
    def test_the_constants_are_narrower_than_the_full_sweep(self):
        assert premap.FAST_WINDOW_BACK_H < 12.0
        assert premap.FAST_WINDOW_FWD_H < 96.0
        assert premap.FAST_MAX_PAGES < premap.MAX_EVENT_PAGES
        assert premap.FAST_REFRESH_SECONDS < premap.REFRESH_SECONDS

    def test_the_window_reaches_the_venue(self, monkeypatch):
        """A constant that never reaches a query is a constant that
        does nothing — the shape of half of today's dead fixes."""
        import datetime as dt

        h = _Harness().install(monkeypatch)
        asyncio.run(premap.fast_refresh())
        q = h.queries[0]
        assert "startTimeMin" in q and "startTimeMax" in q
        lo = dt.datetime.strptime(q["startTimeMin"], "%Y-%m-%dT%H:%M:%SZ")
        hi = dt.datetime.strptime(q["startTimeMax"], "%Y-%m-%dT%H:%M:%SZ")
        span = (hi - lo).total_seconds() / 3600.0
        assert span == pytest.approx(
            premap.FAST_WINDOW_BACK_H + premap.FAST_WINDOW_FWD_H, abs=0.05)

    def test_the_full_sweep_window_is_unchanged(self, monkeypatch):
        import datetime as dt

        h = _Harness().install(monkeypatch)
        asyncio.run(premap.refresh())
        q = h.queries[0]
        lo = dt.datetime.strptime(q["startTimeMin"], "%Y-%m-%dT%H:%M:%SZ")
        hi = dt.datetime.strptime(q["startTimeMax"], "%Y-%m-%dT%H:%M:%SZ")
        assert (hi - lo).total_seconds() / 3600.0 == pytest.approx(108.0,
                                                                   abs=0.05)

    def test_the_page_budget_is_honoured(self, monkeypatch):
        h = _Harness(pages=99).install(monkeypatch)
        asyncio.run(premap.fast_refresh())
        # probe + FAST_MAX_PAGES pages; page 0 reuses the probe's rows
        assert len(h.queries) == premap.FAST_MAX_PAGES
        assert len(h.queries) < premap.MAX_EVENT_PAGES


class TestTheAuthoritiesItIsDenied:
    def test_it_never_tries_an_unwindowed_variant(self, monkeypatch):
        """Rungs 2 and 3 carry no start-time bound, and PREMAP-GT
        established that a bare board leads with a stale 2025 catalog.
        With 8 pages of budget and a 180-second cadence, falling
        through would spend the entire lane on last year's games."""
        h = _Harness(live=False).install(monkeypatch)
        summary = asyncio.run(premap.fast_refresh())
        assert len(h.queries) == 1, "one rung tried, not three"
        assert all("startTimeMin" in q for q in h.queries)
        assert summary["mode"] == "fast/failed"
        assert summary["err"], "the failure stays on the record"

    def test_the_full_sweep_still_walks_the_whole_ladder(self,
                                                         monkeypatch):
        h = _Harness(live=False).install(monkeypatch)
        asyncio.run(premap.refresh())
        assert len(h.queries) == 3, "the full sweep keeps its fallbacks"

    def test_it_never_runs_the_degraded_markets_fallback(self,
                                                          monkeypatch):
        h = _Harness(live=False).install(monkeypatch)
        asyncio.run(premap.fast_refresh())
        assert h.market_calls == [], (
            "markets-mode rows are keyed off each market's own question "
            "and the upsert spreads them table-wide")

    def test_it_never_prunes(self, monkeypatch):
        """Staleness is a table-wide judgement and this lane sees a
        14-hour slice of a 108-hour calendar."""
        h = _Harness().install(monkeypatch)
        asyncio.run(premap.fast_refresh())
        assert h.writes, "it did write rows — the prune was reachable"
        assert h.deletes == []

    def test_the_full_sweep_still_prunes(self, monkeypatch):
        h = _Harness().install(monkeypatch)
        asyncio.run(premap.refresh())
        assert h.deletes, "the full sweep keeps its prune"


class TestItCannotClobberTheFullSweepsRecord:
    """Every probe, dashboard and alert reads `premap_last`, and each
    was written against the full sweep's row counts. A 14-hour sweep
    reporting there would read as a collapsed full sweep every three
    minutes — an instrument reporting on a population it never
    measured, which is the failure mode that has cost the most today."""

    def test_the_fast_lane_writes_its_own_key(self, monkeypatch):
        h = _Harness().install(monkeypatch)
        asyncio.run(premap.fast_refresh())
        assert "premap_last_fast" in h.state
        assert "premap_last" not in h.state

    def test_the_full_sweep_writes_the_canonical_key(self, monkeypatch):
        h = _Harness().install(monkeypatch)
        asyncio.run(premap.refresh())
        assert "premap_last" in h.state
        assert "premap_last_fast" not in h.state

    def test_the_summary_names_its_own_lane(self, monkeypatch):
        h = _Harness().install(monkeypatch)
        assert asyncio.run(premap.fast_refresh())["lane"] == "fast"
        assert asyncio.run(premap.refresh())["lane"] == "full"


class TestBothLanesActuallyRun:
    """Twice today a correct fix landed where execution never arrives.
    A lane that is written but not scheduled is exactly that."""

    def test_main_runs_both(self):
        src = inspect.getsource(premap.main)
        assert "_full_loop()" in src and "_fast_loop()" in src
        assert "asyncio.gather" in src, (
            "gather, not create_task — a detached lane that dies stops "
            "sweeping while the heartbeat still says premap is up")

    def test_the_fast_lane_yields_to_a_full_sweep_in_flight(self):
        src = inspect.getsource(premap._fast_loop)
        assert "_SWEEP_LOCK.locked()" in src
        assert "fast_refresh()" in src
        full = inspect.getsource(premap._full_loop)
        assert "async with _SWEEP_LOCK" in full

    def test_the_skip_is_a_skip_and_not_a_wait(self, monkeypatch):
        """Waiting would queue a fast cycle behind a multi-minute full
        sweep and fire it the instant that sweep finished — the one
        moment the window is guaranteed already covered."""
        calls = []
        monkeypatch.setattr(premap, "fast_refresh",
                            lambda: calls.append(1) or asyncio.sleep(0))

        async def _one_cycle():
            await premap._SWEEP_LOCK.acquire()
            try:
                task = asyncio.create_task(premap._fast_loop())
                await asyncio.sleep(0.05)
                task.cancel()
            finally:
                premap._SWEEP_LOCK.release()

        asyncio.run(_one_cycle())
        assert calls == [], "it skipped rather than blocking on the lock"

    def test_premap_is_in_the_supervised_loop_set(self):
        from pathlib import Path

        src = Path(premap.__file__).with_name("all.py").read_text()
        assert '("premap", premap.main)' in src
