"""A POOL BUILT ON A CLOSED EVENT LOOP IS NEVER HANDED OUT (RC6).

THE FULL-SUITE-ONLY FAILURE. tests/test_three_agents_form_one_linked_trace.py
passed alone and failed after earlier tests in one long single-process run.
Cause, reproduced on 412c4962: `db.get_pool()` returned the module's `_pool`
whatever event loop had built it. asyncio.run() -- one per test under
pytest-asyncio, one per worker restart through asyncio.run, one per sync
caller that runs a coroutine to completion -- closes its loop at the end, so
an earlier test that called get_pool() left a pool whose connections belong to
a dead loop; the next loop's first caller was handed it and `pool.acquire()`
raised RuntimeError('Event loop is closed') inside the API request handler
(the three-agents trace's first `/api/command/agents` read), and close_pool()
raised the same out of the test's cleanup.

THE PROOF IS DETERMINISTIC, NOT A RERUN. Each case below first runs a PRIOR
caller on ANOTHER event loop (its own asyncio.run, closed when it returns)
that builds the pool and leaves it, exactly as the earlier test did, then the
next caller on a fresh loop. The pair of pytest-asyncio tests at the end
repeats it the way the suite ran it: one test leaves the pool on its loop,
the next (a new loop) reads through the real API app.

Unchanged, and pinned here: a pool this module did not build (a stand-in a
caller put in `db._pool`) is handed out as before, and a pool on a loop that
is still OPEN is never dropped or replaced from another loop.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from sportsassets import db

DSN = os.environ.get("RN1X_TEST_DSN", "") or os.environ.get("DATABASE_URL", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN or DATABASE_URL")


@pytest.fixture
def isolated_pool(monkeypatch):
    """Every case starts and ends with no module pool, on the test DSN."""
    if DSN:
        monkeypatch.setenv("DATABASE_URL", DSN)
        from sportsassets import config
        config.settings.cache_clear()
    db._pool = None
    yield
    leftover = db._pool
    db._pool = None
    if leftover is not None:
        try:
            leftover.terminate()
        except Exception:                                      # noqa: BLE001
            pass


def _prior_caller_leaves_the_pool() -> object:
    """A caller on its OWN event loop builds the pool, uses it, and leaves
    it (no close_pool): asyncio.run closes that loop on return."""
    async def prior():
        pool = await db.get_pool()
        assert await pool.fetchval("SELECT 1") == 1
        return pool
    pool = asyncio.run(prior())
    assert db._pool is pool, "the prior caller's pool stayed in the module"
    return pool


@pg
@pytest.mark.usefixtures("isolated_pool")
def test_the_next_loop_gets_a_working_pool_not_the_dead_one():
    dead = _prior_caller_leaves_the_pool()

    async def after():
        pool = await db.get_pool()
        # the dead loop's pool is never handed out
        assert pool is not dead
        # and the one handed out works on THIS loop (on 412c4962 this read
        # raised RuntimeError('Event loop is closed'))
        assert await pool.fetchval("SELECT 2") == 2
        # the same pool for every later caller on this loop
        assert await db.get_pool() is pool
        await db.close_pool()
        assert db._pool is None
    asyncio.run(after())


@pg
@pytest.mark.usefixtures("isolated_pool")
def test_close_pool_after_its_loop_closed_does_not_raise():
    _prior_caller_leaves_the_pool()

    async def cleanup():
        # on 412c4962: RuntimeError('Event loop is closed') out of close()
        await db.close_pool()
        assert db._pool is None
    asyncio.run(cleanup())


@pg
@pytest.mark.usefixtures("isolated_pool")
def test_a_pool_on_a_live_other_loop_is_left_alone():
    """Only a CLOSED loop's pool is dropped: a pool whose loop still exists
    is never closed or replaced from another loop (unchanged behaviour)."""
    owner = asyncio.new_event_loop()
    try:
        pool = owner.run_until_complete(db.get_pool())

        async def other():
            return await db.get_pool()
        assert asyncio.run(other()) is pool
        assert db._pool is pool and not owner.is_closed()
        owner.run_until_complete(db.close_pool())
        assert db._pool is None
    finally:
        owner.close()


@pg
@pytest.mark.usefixtures("isolated_pool")
def test_asyncpg_pool_names_the_loop_it_was_built_on():
    """THE ATTRIBUTE THE RULE READS. db judges a pool by asyncpg's own
    `Pool._loop`; if an asyncpg upgrade moves it, this fails here (and the
    rule falls back to handing the pool out exactly as before)."""
    async def build():
        pool = await db.get_pool()
        return pool, asyncio.get_running_loop()
    pool, loop = asyncio.run(build())
    assert getattr(pool, "_loop", None) is loop
    assert loop.is_closed()


@pytest.mark.usefixtures("isolated_pool")
def test_a_stand_in_pool_is_handed_out_unchanged():
    """A pool that names no loop (a caller's or a test's stand-in) is never
    judged: handed out as before, on any loop."""
    stand_in = object()
    db._pool = stand_in

    async def read():
        return await db.get_pool()
    assert asyncio.run(read()) is stand_in
    assert asyncio.run(read()) is stand_in


@pg
@pytest.mark.usefixtures("isolated_pool")
def test_the_api_serves_after_a_prior_loop_left_the_pool(monkeypatch):
    """THE THREE-AGENTS FAILURE ITSELF, minimal: a prior loop leaves the
    pool, then the real API app answers a database-backed admin read on a
    fresh loop (on 412c4962: the handler's acquire raised 'Event loop is
    closed' and the read failed)."""
    _prior_caller_leaves_the_pool()
    from sportsassets.api import app as A
    from tests import test_agent_workspaces_show_runtime_records as WS

    async def read():
        c = await WS._client(monkeypatch, A)
        try:
            r = await c.get("/api/command/agents")
            return r.status_code, r.text[:300]
        finally:
            await c.aclose()
            await WS._close_pool()
    status, text = asyncio.run(read())
    assert status == 200, (status, text)


# ── THE SAME, THE WAY THE SUITE RAN IT: two pytest-asyncio tests, in order,
# with NOTHING reset between them (pytest runs a module's tests in file order;
# each async test gets its own event loop, closed when the test ends) ──
_LEFT: dict = {}


@pg
@pytest.mark.asyncio
async def test_suite_order_1_a_prior_test_leaves_the_pool_on_its_loop(
        monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    from sportsassets import config
    config.settings.cache_clear()
    await db.close_pool()
    pool = await db.get_pool()
    assert await pool.fetchval("SELECT 1") == 1
    _LEFT["pool"] = pool
    # no close_pool(): the next test starts on a new loop with it in place


@pg
@pytest.mark.asyncio
async def test_suite_order_2_the_next_test_reads_through_the_api(monkeypatch):
    assert _LEFT, "runs after test_suite_order_1 in file order"
    assert db._pool is _LEFT["pool"], "the prior test's pool is still there"
    from sportsassets.api import app as A
    from tests import test_agent_workspaces_show_runtime_records as WS
    c = await WS._client(monkeypatch, A)
    try:
        r = await c.get("/api/command/agents")
        assert r.status_code == 200, (r.status_code, r.text[:300])
    finally:
        await c.aclose()
        await WS._close_pool()
    assert db._pool is None
