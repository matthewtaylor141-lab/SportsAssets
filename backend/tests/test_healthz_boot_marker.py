"""/healthz carries a boot marker and a bounded DB probe (2026-09-03 API
restarts behind the 502s).

Every 502 in the probes was Render's own page for 10-55 s on every
route, after which the same `commit` answered from an RSS 0.6-0.8 GB
lower. `commit` is the deploy's RENDER_GIT_COMMIT and survives a
restart, so "same process" was unprovable. boot_id and uptime_s make it
a reading. The SELECT 1 gets a 2 s ceiling so a saturated pool reports
db_ok false instead of hanging the check the platform restarts on.

The probe reads db._pool directly and never calls get_pool() (hotfix
review): with the DB down at boot the pool is None, and a check that
went through get_pool() started a fresh create_pool per call and
cancelled it mid-connect under the ceiling -- a stranded half-open
connection per check. No pool is db_ok false, at once.

The payload also carries `pool` (2026-09-05): the pool's own counters
{size, idle, max, min}, read synchronously from db._pool by
db.pool_stats() -- the one reader of those counters, so the handler
carries no copy of the reads -- with no await and no get_pool().
{"ok": true, "db_ok": false} on its own could not tell a saturated
pool from a slow database from the edge; size == max with idle == 0 is
the first, idle > 0 with db_ok false is the second. The field is
informational and fails closed: null without a pool, null when any
counter raises, and the check answers either way.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import pytest

from sportsassets import db as db_mod
from sportsassets.api import app as app_mod


class _FastPool:
    async def fetchval(self, sql, *a):
        return 1


class _HungPool:
    async def fetchval(self, sql, *a):
        await asyncio.Event().wait()  # never


class _Counters:
    """asyncpg's four pool counters as the real Pool exposes them: plain
    synchronous methods returning ints. A handler that awaited them
    would raise on the int, be caught, and read as null -- which the
    shape test then fails."""

    def __init__(self, size, idle, max_size, min_size):
        self._size, self._idle = size, idle
        self._max, self._min = max_size, min_size

    def get_size(self):
        return self._size

    def get_idle_size(self):
        return self._idle

    def get_max_size(self):
        return self._max

    def get_min_size(self):
        return self._min


class _FastPoolWithCounters(_Counters, _FastPool):
    pass


class _HungPoolWithCounters(_Counters, _HungPool):
    pass


def _pool(monkeypatch, pool):
    """Install `pool` as the process's pool and make get_pool() a trap:
    the probe must read what the process has, never build one."""
    monkeypatch.setattr(db_mod, "_pool", pool)

    async def _never():
        raise AssertionError("/healthz must not call get_pool()")

    monkeypatch.setattr(app_mod, "get_pool", _never)
    monkeypatch.setattr(db_mod, "get_pool", _never)


class TestTheBootMarker:
    def test_boot_id_is_eight_hex_and_constant_across_calls(self, monkeypatch):
        _pool(monkeypatch, _FastPool())
        a = asyncio.run(app_mod.healthz())
        b = asyncio.run(app_mod.healthz())
        assert re.fullmatch(r"[0-9a-f]{8}", a["boot_id"])
        assert a["boot_id"] == b["boot_id"] == app_mod._BOOT_ID
        assert a["ok"] is True and a["db_ok"] is True

    def test_uptime_is_non_negative_and_climbs(self, monkeypatch):
        _pool(monkeypatch, _FastPool())
        a = asyncio.run(app_mod.healthz())
        time.sleep(0.25)  # uptime_s is rounded to a tenth
        b = asyncio.run(app_mod.healthz())
        assert a["uptime_s"] >= 0
        assert b["uptime_s"] > a["uptime_s"]
        assert app_mod._BOOT_TS <= time.time()

    def test_the_old_fields_are_still_there(self, monkeypatch):
        _pool(monkeypatch, _FastPool())
        out = asyncio.run(app_mod.healthz())
        assert set(out) == {"ok", "db_ok", "pool", "commit", "rss_mb",
                            "boot_id", "uptime_s"}
        assert out["rss_mb"] is None or out["rss_mb"] > 0


class TestTheDbProbeIsBounded:
    def test_a_hung_pool_reports_db_ok_false_within_three_seconds(
            self, monkeypatch):
        _pool(monkeypatch, _HungPool())
        t0 = time.monotonic()
        out = asyncio.run(app_mod.healthz())
        assert time.monotonic() - t0 < 3.0
        assert out["db_ok"] is False
        assert out["ok"] is True, "the DB is a field, never a veto"
        assert re.fullmatch(r"[0-9a-f]{8}", out["boot_id"])

    def test_no_pool_is_db_ok_false_at_once_and_never_calls_get_pool(
            self, monkeypatch):
        """The DB-down-at-boot shape: db._pool is None. The old probe
        went through get_pool(), which started create_pool and was
        cancelled mid-connect by the ceiling on every check."""
        _pool(monkeypatch, None)
        calls: list[str] = []

        async def _trap():
            calls.append("get_pool")
            await asyncio.Event().wait()

        monkeypatch.setattr(app_mod, "get_pool", _trap)
        monkeypatch.setattr(db_mod, "get_pool", _trap)
        t0 = time.monotonic()
        out = asyncio.run(app_mod.healthz())
        assert time.monotonic() - t0 < 3.0
        assert out["db_ok"] is False and out["ok"] is True
        assert calls == []
        assert db_mod._pool is None, "the check builds no pool"

    def test_a_live_pool_is_probed_with_select_1_not_rebuilt(self, monkeypatch):
        seen: list[str] = []

        class _Recording:
            async def fetchval(self, sql, *a):
                seen.append(sql)
                return 1

        pool = _Recording()
        _pool(monkeypatch, pool)
        out = asyncio.run(app_mod.healthz())
        assert out["db_ok"] is True
        assert seen == ["SELECT 1"]
        assert db_mod._pool is pool

    def test_a_failing_pool_still_answers(self, monkeypatch):
        class _Broken:
            async def fetchval(self, sql, *a):
                raise ConnectionError("pool exhausted")

        _pool(monkeypatch, _Broken())
        out = asyncio.run(app_mod.healthz())
        assert out["db_ok"] is False and out["ok"] is True

    def test_the_handler_names_no_get_pool(self):
        import inspect

        src = inspect.getsource(app_mod.healthz)
        assert "await get_pool(" not in src
        assert "_db._pool" in src


_COUNTER_NAMES = ("get_size", "get_idle_size", "get_max_size",
                  "get_min_size")


class TestThePoolField:
    """`pool` is {size, idle, max, min} from db._pool's own counters:
    no await, no get_pool(), null when there is no pool or a counter
    raises, and never a reason for the check not to answer."""

    def test_the_shape_is_the_four_counters(self, monkeypatch):
        _pool(monkeypatch, _FastPoolWithCounters(3, 1, 10, 1))
        out = asyncio.run(app_mod.healthz())
        assert out["pool"] == {"size": 3, "idle": 1, "max": 10, "min": 1}
        assert all(type(v) is int for v in out["pool"].values())
        assert out["ok"] is True and out["db_ok"] is True

    def test_a_saturated_pool_reads_size_max_idle_zero_db_ok_false(
            self, monkeypatch):
        """The 2026-09-05 shape, now legible from the edge: every
        connection out, none idle, the SELECT 1 losing its 2 s race.
        The counters are read without an acquire, so a pool that hangs
        the probe still reports them, and the check still answers."""
        _pool(monkeypatch, _HungPoolWithCounters(10, 0, 10, 1))
        t0 = time.monotonic()
        out = asyncio.run(app_mod.healthz())
        assert time.monotonic() - t0 < 3.0
        assert out["pool"] == {"size": 10, "idle": 0, "max": 10, "min": 1}
        assert out["pool"]["size"] == out["pool"]["max"]
        assert out["pool"]["idle"] == 0
        assert out["db_ok"] is False and out["ok"] is True

    def test_idle_with_db_ok_false_is_the_database_not_the_pool(
            self, monkeypatch):
        """The other reading: a free connection was there and the
        SELECT 1 still lost -- the database itself is slow or dead."""
        _pool(monkeypatch, _HungPoolWithCounters(4, 2, 10, 1))
        out = asyncio.run(app_mod.healthz())
        assert out["pool"]["idle"] > 0
        assert out["db_ok"] is False and out["ok"] is True

    def test_no_pool_is_null_and_the_key_is_still_present(self, monkeypatch):
        _pool(monkeypatch, None)
        out = asyncio.run(app_mod.healthz())
        assert "pool" in out
        assert out["pool"] is None
        assert out["db_ok"] is False and out["ok"] is True

    def test_a_pool_without_counters_is_null_not_an_error(self, monkeypatch):
        """A pool-shaped object that lacks the counters (the older
        stubs above, or anything that is not an asyncpg Pool) reads as
        null while the SELECT 1 still runs."""
        _pool(monkeypatch, _FastPool())
        out = asyncio.run(app_mod.healthz())
        assert out["pool"] is None
        assert out["db_ok"] is True and out["ok"] is True

    @pytest.mark.parametrize("broken", _COUNTER_NAMES)
    def test_any_counter_raising_is_null_with_ok_true_and_db_ok_true(
            self, monkeypatch, broken):
        """Fail closed, one counter at a time: whichever of the four
        raises, the whole field is null (never a partial dict), the
        check answers ok, and the SELECT 1 still ran to db_ok true --
        the field cannot block the probe it sits next to."""
        pool = _FastPoolWithCounters(3, 1, 10, 1)

        def _boom():
            raise RuntimeError(f"{broken} exploded")

        monkeypatch.setattr(pool, broken, _boom)
        _pool(monkeypatch, pool)
        out = asyncio.run(app_mod.healthz())
        assert out["pool"] is None
        assert out["ok"] is True
        assert out["db_ok"] is True

    def test_counters_are_read_before_the_probe_not_after(self, monkeypatch):
        """The field is the pool as the check found it, not as the
        SELECT 1 left it: every counter is read before the probe's
        acquire, so the reading is not skewed by the check's own
        connection."""
        seen: list[str] = []

        class _Ordered(_Counters):
            async def fetchval(self, sql, *a):
                seen.append(sql)
                return 1

        pool = _Ordered(3, 1, 10, 1)
        for name in _COUNTER_NAMES:
            real = getattr(pool, name)
            monkeypatch.setattr(
                pool, name, (lambda n=name, r=real: (seen.append(n), r())[1]))
        _pool(monkeypatch, pool)
        out = asyncio.run(app_mod.healthz())
        assert out["pool"] == {"size": 3, "idle": 1, "max": 10, "min": 1}
        assert "SELECT 1" in seen
        probe_at = seen.index("SELECT 1")
        for name in _COUNTER_NAMES:
            assert name in seen, name
            assert seen.index(name) < probe_at, name

    def test_counters_come_from_pool_stats_and_never_via_get_pool(self):
        """Source pin: the field is db.pool_stats() -- called, not
        awaited, and the one place the four counters are read, so the
        handler carries no inline copy of them -- and get_pool() is
        still never called. An awaited counter would be an
        acquire-shaped wait on the one check the platform restarts on."""
        import inspect

        src = inspect.getsource(app_mod.healthz)
        assert "_db.pool_stats()" in src
        assert re.search(r"await\s+[\w.]*pool_stats\(", src) is None
        assert not inspect.iscoroutinefunction(db_mod.pool_stats)
        for name in _COUNTER_NAMES:
            assert f".{name}(" not in src, f"{name} read inline in healthz"
        assert "await get_pool(" not in src
        assert "await _db.get_pool(" not in src
        assert "_db._pool" in src

    def test_the_field_is_pool_stats_output_verbatim(self, monkeypatch):
        """Behavioural side of the same pin: whatever db.pool_stats()
        answers is what the payload carries, so the handler cannot
        drift from it (a stand-in that returns a marker dict)."""
        marker = {"size": 5, "idle": 4, "max": 10, "min": 1}
        calls: list[int] = []

        def _stats():
            calls.append(1)
            return dict(marker)

        _pool(monkeypatch, _FastPool())
        monkeypatch.setattr(db_mod, "pool_stats", _stats)
        out = asyncio.run(app_mod.healthz())
        assert out["pool"] == marker
        assert calls == [1]


def test_the_export_route_is_the_plain_dict_return():
    """The hotfix's first cut re-serialized /api/venue-export-raw in a
    worker; the review measured HEAD's dict return faster and smaller
    on the deployed FastAPI (response_model=dict takes the bytes fast
    path, no jsonable_encoder). The route stays as it was."""
    import inspect

    assert inspect.signature(app_mod.api_venue_export_raw).return_annotation \
        in ("dict", dict)
    src = inspect.getsource(app_mod.api_venue_export_raw)
    assert "return await venue_export_raw(since)" in src
    assert "to_thread" not in src and "Response(" not in src


class TestTheWatchLinePrintsTheMarker:
    def test_memory_watch_reads_boot_id_and_uptime(self):
        yml = Path(__file__).resolve().parents[2] / ".github" / "workflows" \
            / "engine-diagnostic.yml"
        src = yml.read_text()
        assert "jq -c '{rss_mb, db_ok, pool, commit, boot_id, uptime_s}'" \
            in src
        # Both directions of the pin: the line without `pool` is gone.
        assert "jq -c '{rss_mb, db_ok, commit, boot_id, uptime_s}'" not in src
        assert "jq -c '{rss_mb, db_ok, commit}'" not in src
