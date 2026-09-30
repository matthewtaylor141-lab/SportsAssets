"""THE COLLECTOR USES THE RELEASED PASS LIMIT, INSIDE HARD BOUNDS.

A released COLLECTION_PASS_LIMIT version (agents.improvement, pre-authorized,
canaried, rollback-able) must actually change how many candidates the
scheduled collector attempts, and a rollback must actually change it back;
a value outside the hard bounds is refused and the code constant applies.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from sportsassets import bettor_pair_observations as PO
from sportsassets.agents import registry as REG
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN") or os.environ.get("DATABASE_URL")
pg = pytest.mark.skipif(not DSN, reason="needs a database")  # noqa: E501 -- the gate always sets it


async def _with_conn(fn):
    import asyncpg
    c = await asyncpg.connect(DSN)
    try:
        await REG.ensure_identities(c, code_version="test")
        await c.execute("DELETE FROM agent_policy_versions WHERE "
                        " agent_id='DEREK' AND policy_key='collection.pass_limit'")
        return await fn(c)
    finally:
        await c.execute("DELETE FROM agent_policy_versions WHERE "
                        " agent_id='DEREK' AND policy_key='collection.pass_limit'")
        await c.close()


async def _activate(c, version, value):
    await c.execute(
        "UPDATE agent_policy_versions SET state='RETIRED' WHERE "
        " agent_id='DEREK' AND policy_key='collection.pass_limit' "
        " AND state='ACTIVE'")
    await c.execute(
        "INSERT INTO agent_policy_versions (agent_id, policy_key, version, "
        " params, state, created_by, approved_by, approved_at) VALUES "
        " ('DEREK','collection.pass_limit',$1,$2::jsonb,'ACTIVE','AUDREY',"
        " 'PREAUTHORIZED:COLLECTION_PASS_LIMIT', now())", version,
        '{"candidates_per_pass": %d}' % value)


@pg
def test_the_code_default_is_used_and_labelled_when_nothing_is_released():
    async def go(c):
        got = await loop._collection_pass_limit(c)
        assert got["candidates_per_pass"] == PO.CANDIDATES_PER_PASS
        assert got["source"] == "CODE_DEFAULT" and got["version"] is None
    asyncio.run(_with_conn(go))


@pg
def test_a_released_value_is_used_and_a_rollback_restores_the_prior_one():
    async def go(c):
        await _activate(c, "imp-a", 5)
        got = await loop._collection_pass_limit(c)
        assert (got["candidates_per_pass"], got["version"]) == (5, "imp-a")
        assert got["source"] != "CODE_DEFAULT"
        await _activate(c, "imp-prior", 3)          # the rollback's effect
        got = await loop._collection_pass_limit(c)
        assert (got["candidates_per_pass"], got["version"]) == (3, "imp-prior")
    asyncio.run(_with_conn(go))


@pg
def test_a_value_outside_the_hard_bounds_is_refused():
    async def go(c):
        await _activate(c, "imp-wild", 50)
        got = await loop._collection_pass_limit(c)
        assert got["candidates_per_pass"] == PO.CANDIDATES_PER_PASS
        assert got["refused_value"] == 50 and "bounds" in got["why"]
    asyncio.run(_with_conn(go))


def test_the_scheduled_pass_passes_the_selected_limit_to_the_collector():
    import inspect
    src = inspect.getsource(loop._pair_observation_pass)
    assert "per_pass=pass_limit[\"candidates_per_pass\"]" in src
