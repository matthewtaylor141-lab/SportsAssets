"""Three quarters of RSS was in none of the caches.

Measured on one process, no restart, 2026-08-25:

    MEMCENSUS rss=1424.2MB accounted=353.4MB unaccounted=1070.8MB
              archive 300171r @1181B marginal = 338.2MB

and, thirty seconds apart in the same probe, RSS moved
1,217.7 -> 1,808.2 MB. Not a leak — nothing retains it. Not a cache —
the caches total 353 MB. It is transient allocation that is freed and
never handed back, which is what glibc does when many threads contend
for the heap: each gets its own arena, up to 8 x ncores, and each
keeps its freed pages.

The archive parse runs in asyncio.to_thread workers, so heavy requests
land in different arenas and the process grows permanently. This
codebase has described that ratchet since August and answered it with
a one-shot malloc_trim, which reclaims but does not stop the spread.

Two changes: bound the arena count (M_ARENA_MAX), and trim on a timer
rather than only after a snapshot save, which is roughly never.

Neither is a guess dressed as a fix — the census reports whether the
cap took, so the next reading says so either way.
"""

import inspect

from sportsassets.api import app as app_mod


class TestTheCapIsApplied:
    def test_it_runs_at_import_not_in_lifespan(self):
        """Arenas are claimed by the thread pool before the first
        request. A cap applied in lifespan would be too late to bound
        what already exists."""
        src = inspect.getsource(app_mod)
        cap_at = src.index("_ARENA_STATUS = _cap_malloc_arenas()")
        assert cap_at < src.index("app = FastAPI(")

    def test_it_actually_took_on_this_platform(self):
        """glibc returns 1 from mallopt on success. On a non-glibc
        platform the status says unavailable and this is skipped —
        what must never happen is a silent claim of success."""
        status = app_mod._ARENA_STATUS
        assert isinstance(status, str) and status
        if status.startswith("unavailable"):
            return
        assert "arena_max=2" in status
        assert "rc=1" in status, f"mallopt did not take: {status}"

    def test_a_failure_is_reported_not_swallowed(self):
        src = inspect.getsource(app_mod._cap_malloc_arenas)
        assert "unavailable" in src
        assert "return" in src.split("except")[1]

    def test_the_census_publishes_whether_it_took(self):
        src = inspect.getsource(app_mod.api_memory_census)
        assert '"arena_cap"' in src


class TestTheTrimIsPeriodic:
    def test_a_trim_loop_is_started(self):
        src = inspect.getsource(app_mod.lifespan)
        assert "_trim_loop" in src
        assert "create_task(_trim_loop())" in src

    def test_it_is_cancelled_on_shutdown(self):
        src = inspect.getsource(app_mod.lifespan)
        assert "trim_task.cancel()" in src

    def test_a_failed_trim_does_not_kill_the_loop(self):
        src = inspect.getsource(app_mod.lifespan)
        blk = src[src.index("async def _trim_loop"):
                  src.index("trim_task =")]
        assert "except Exception" in blk
        assert "while True" in blk

    def test_it_does_not_block_the_event_loop(self):
        """malloc_trim walks every arena and can take real time. On the
        event loop that is a stall on every request in flight."""
        src = inspect.getsource(app_mod.lifespan)
        blk = src[src.index("async def _trim_loop"):
                  src.index("trim_task =")]
        assert "asyncio.to_thread(_malloc_trim)" in blk

    def test_the_interval_is_tunable_without_a_deploy(self):
        src = inspect.getsource(app_mod.lifespan)
        assert "API_TRIM_INTERVAL_S" in src


class TestItChangesNoBehaviour:
    """A memory fix that alters what the API returns is not a memory
    fix. Both changes are allocator-level and must stay that way."""

    def test_the_cap_only_calls_mallopt(self):
        src = inspect.getsource(app_mod._cap_malloc_arenas)
        assert "mallopt" in src
        for forbidden in ("pool.", "fetch", "execute", "requests", "httpx"):
            assert forbidden not in src

    def test_the_trim_loop_touches_no_data(self):
        src = inspect.getsource(app_mod.lifespan)
        blk = src[src.index("async def _trim_loop"):
                  src.index("trim_task =")]
        for forbidden in ("pool", "fetch", "execute", "_archive_cache"):
            assert forbidden not in blk
