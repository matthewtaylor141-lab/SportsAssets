"""The analytics settle pass reads ONLY the archived resolutions it grades.

Production 2026-10-08: workers OOM-killed at 01:33:57Z (2 GiB). Every ~9.5
min the analytics cycle fetched EVERY archived position resolution -- 99,120
payloads, 609 MB of positionResolution JSON (read back 04:20Z) -- and
json-parsed each, to grade 52 live_orders rows on 47 slugs: a ~600 MB burst
46-70 s after `positions persist`. The filter and the field extraction now
run in the database; the grading is unchanged (latest resolution per slug
wins; realized = afterPosition, else beforePosition)."""
from __future__ import annotations

import asyncio
import json

import pytest

from sportsassets.analytics import engine
from sportsassets.api import pmus_account


class _Pool:
    def __init__(self, rows, archive):
        self._rows = [dict(r) for r in rows]
        self._archive = archive
        self.archive_calls = []
        self.updates = []

    async def fetch(self, sql, *args):
        if "pmus_activity_archive" in sql:
            self.archive_calls.append((sql, args))
            want = set(args[0]) if args else None
            return [dict(r) for r in self._archive
                    if want is None or r["slug"] in want]
        if "status = 'cashed_out'" in sql:
            return []
        if "FROM live_orders" in sql:
            return [dict(r) for r in self._rows]
        return []

    async def fetchval(self, sql, *args):
        if "pmus_activity_archive" in sql:
            return len(self._archive)
        return None

    async def execute(self, sql, *args):
        self.updates.append(args)
        return "UPDATE 1"


def _row(i, slug, filled=100.0):
    return {"id": i, "slug": slug, "whale": "w", "filled_usd": filled,
            "pnl": 0.0, "status": "filled"}


def _arch(slug, ts, after=None, before=None):
    j = (lambda v: None if v is None else json.dumps({"value": str(v)}))
    return {"slug": slug, "after_realized": j(after),
            "before_realized": j(before), "ts": ts}


def _run(monkeypatch, pool):
    async def _truth(_from_day):
        return {}
    monkeypatch.setattr(pmus_account, "resolution_truth", _truth)
    return asyncio.run(engine._settle_pmus_from_venue(
        pool, rescore_since="2026-08-01"))


def test_only_the_graded_slugs_are_requested_and_only_two_fields(monkeypatch):
    archive = [_arch("atc-a", 1.0, after=5)] + [
        _arch("other-%d" % i, 2.0, after=1) for i in range(1000)]
    pool = _Pool([_row(1, "atc-a")], archive)
    summary = _run(monkeypatch, pool)
    (sql, args), = pool.archive_calls
    assert args == (["atc-a"],)
    assert "= ANY($1::text[])" in sql
    # the whole payload / positionResolution object is never selected
    assert "AS pr" not in sql and "SELECT payload " not in sql
    assert "after_realized" in sql and "before_realized" in sql
    assert summary["archive"]["scanned"] == 1
    assert summary["archive"]["slugs_requested"] == 1
    assert summary["archive"]["table_total"] == 1001
    assert summary["settled"] == 1


def test_grading_is_unchanged_latest_wins_after_else_before(monkeypatch):
    pool = _Pool([_row(1, "atc-a"), _row(2, "atc-b")], [
        _arch("atc-a", 1.0, after=3.0),
        _arch("atc-a", 9.0, after=7.5),          # latest wins
        _arch("atc-b", 4.0, after=None, before=-2.25),   # before fallback
    ])
    summary = _run(monkeypatch, pool)
    assert summary["settled"] == 2
    written = [x for a in pool.updates for x in a
               if isinstance(x, float)]
    assert any(x == pytest.approx(7.5) for x in written), written
    assert any(x == pytest.approx(-2.25) for x in written), written
    assert not any(x == pytest.approx(3.0) for x in written), written


def test_a_slug_with_no_archived_resolution_stays_ungraded(monkeypatch):
    pool = _Pool([_row(1, "atc-none")], [_arch("atc-x", 1.0, after=1)])
    summary = _run(monkeypatch, pool)
    assert summary["settled"] == 0 and pool.updates == []


def test_run_cycle_reports_rss_by_step(monkeypatch):
    calls = []

    async def _noop(*a, **k):
        calls.append(1)
        return []

    async def _zero(*a, **k):
        return 0
    monkeypatch.setattr(engine, "rebuild_positions", _noop)
    monkeypatch.setattr(engine, "compute_rollups", lambda s, now: [])
    monkeypatch.setattr(engine, "persist_rollups", _noop)
    monkeypatch.setattr(engine, "validate_against_leaderboard", _noop)
    monkeypatch.setattr(engine, "settle_engine_fills", _zero)
    monkeypatch.setattr(engine, "settle_ai_trades", _zero)
    monkeypatch.setattr(engine, "pool_settle_live", _zero)
    out = asyncio.run(engine.run_cycle())
    assert list(out["rss_mb_by_step"]) == [
        "start", "rebuild_positions", "compute_rollups", "persist_rollups",
        "validate_against_leaderboard", "settle_engine_fills",
        "settle_ai_trades", "pool_settle_live"]
