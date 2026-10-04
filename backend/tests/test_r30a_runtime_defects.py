"""R30A RUNTIME DEFECTS: the research tick names its failing step, and every
heartbeat writer serializes what JSON cannot carry.

PRODUCTION EVIDENCE (render-ops `logs`, 2026-10-04 15:46-18:47Z):
  * API: `agent research tick failed: TimeoutError` x17, with nothing saying
    which of the tick's bounded steps failed; the tracebacks of the same
    seconds are asyncpg Pool._acquire (the pool six-held by single-writer
    loops -- see test_r30a_pool_starvation_root_cause.py for that fix).
  * workers: `bettor_state: heartbeat write failed: TypeError: Object of type
    datetime is not JSON serializable` on EVERY bettor_state tick (~ every
    100 s) on the deployed 191b299. dea1b2e (in this branch, not yet deployed)
    fixed db.heartbeat; the guard below proves no OTHER heartbeat writer can
    fail the same way.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import warnings
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

PKG = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"


# ── the research tick names the step that failed ───────────────────────

def test_a_starved_claim_is_reported_as_the_claim_step(monkeypatch):
    from sportsassets.agents import capability_runtime as R
    from sportsassets.agents import capability_work as W

    @asynccontextmanager
    async def ok_conn():
        yield MagicMock()

    @asynccontextmanager
    async def starved():
        await asyncio.sleep(10)          # a pool with no free slot
        yield MagicMock()

    calls = {"n": 0}

    def acquire(*a, **kw):
        calls["n"] += 1
        # CONTROL and ADMIT get a connection; CLAIM waits on a dry pool
        return ok_conn() if calls["n"] <= 2 else starved()

    pool = MagicMock()
    pool.acquire = acquire
    monkeypatch.setattr(W, "schema", AsyncMock(return_value=True))
    monkeypatch.setattr(W, "control", AsyncMock(return_value={"enabled": True}))
    monkeypatch.setattr(R, "admit", AsyncMock(return_value=0))
    real_timeout = asyncio.timeout
    monkeypatch.setattr(R.asyncio, "timeout",
                        lambda s: real_timeout(min(s, 0.05)))

    async def main():
        with pytest.raises(R.TickPhaseFailed) as got:
            await R.tick(pool)
        return got.value
    err = asyncio.run(main())
    assert err.phase == "CLAIM"
    assert isinstance(err.cause, TimeoutError)


def test_the_run_loop_logs_the_phase(monkeypatch, caplog):
    from sportsassets.agents import capability_runtime as R

    async def failing(pool):
        raise R.TickPhaseFailed("HEARTBEAT", TimeoutError())

    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
        raise asyncio.CancelledError

    monkeypatch.setattr(R, "tick", failing)
    monkeypatch.setattr(R.asyncio, "sleep", fake_sleep)

    async def get_pool():
        return object()

    with caplog.at_level("WARNING"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(R.run(get_pool))
    assert "agent research tick failed in HEARTBEAT: TimeoutError" in caplog.text


# ── every heartbeat writer can carry a datetime ────────────────────────

def _heartbeat_writers():
    """(file, function) for every function whose name names a heartbeat /
    beat and that serializes with json.dumps."""
    out = []
    for path in PKG.rglob("*.py"):
        if "vendor" in path.parts:
            continue
        try:
            with warnings.catch_warnings():
                # a pre-existing '\\d' in a docstring elsewhere is not ours
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name.lower()
            if "heartbeat" not in name and not name.endswith("beat"):
                continue
            dumps = [c for c in ast.walk(node)
                     if isinstance(c, ast.Call)
                     and isinstance(c.func, ast.Attribute)
                     and c.func.attr == "dumps"]
            if dumps:
                out.append((path, node, dumps))
    return out


def test_every_heartbeat_writer_serializes_non_json_values():
    """A bare json.dumps in a heartbeat raised on the first datetime in the
    detail (bettor_state, every tick). Every heartbeat writer's dumps must
    carry a `default=` (db.heartbeat_json's, or default=str)."""
    writers = _heartbeat_writers()
    assert len(writers) >= 6, "the scan found too few writers to be real"
    bare = []
    for path, node, dumps in writers:
        for c in dumps:
            if not any(k.arg == "default" for k in c.keywords):
                bare.append("%s:%s %s" % (path.relative_to(PKG), c.lineno,
                                          node.name))
    assert not bare, "heartbeat writers with a bare json.dumps: %s" % bare


def test_service_heartbeats_has_one_writer():
    """Only db.heartbeat writes service_heartbeats, so its one serializer
    (db.heartbeat_json) covers every service beat."""
    writers = []
    for path in PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "INSERT INTO service_heartbeats" in text:
            writers.append(path.relative_to(PKG).as_posix())
    assert writers == ["db.py"]


def test_db_heartbeat_carries_the_production_bettor_state_shape():
    from sportsassets import db as DB

    class _Con:
        def __init__(self):
            self.args = None

        async def execute(self, sql, *args, **kw):
            self.args = args

    con = _Con()
    stats = {"bucket": datetime(2026, 10, 4, 17, 0, tzinfo=UTC),
             "captured": 12, "refused": {"NO_BOOK": 3}}
    asyncio.run(DB.heartbeat("bettor_state", "ok", stats, con=con))
    assert '"bucket": "2026-10-04T17:00:00+00:00"' in con.args[2]
