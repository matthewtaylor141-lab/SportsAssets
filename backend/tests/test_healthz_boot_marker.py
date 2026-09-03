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
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from sportsassets import db as db_mod
from sportsassets.api import app as app_mod


class _FastPool:
    async def fetchval(self, sql, *a):
        return 1


class _HungPool:
    async def fetchval(self, sql, *a):
        await asyncio.Event().wait()  # never


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
        assert set(out) == {"ok", "db_ok", "commit", "rss_mb", "boot_id",
                            "uptime_s"}
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
        assert "jq -c '{rss_mb, db_ok, commit, boot_id, uptime_s}'" in src
        assert "jq -c '{rss_mb, db_ok, commit}'" not in src
