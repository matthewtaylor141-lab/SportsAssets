"""SINGLE-WRITER OWNERSHIP, pinned where it was previously only claimed.

The acceptance checklist said "advisory lock per loop; each loop has its
own" for all four loops. Two of them -- `ext_pinnacle_loop` and
`rn1x_model_loop`, both added in this round -- took no lock at all, and
each acquired a POOLED connection per cycle, which is the shape that
cannot hold a session-scoped lock even once one is added. These tests pin
the four properties that make the claim true:

    1 · every loop that writes takes a lock;
    2 · the four keys are distinct, so no loop is silently a standby of
        another;
    3 · the lock is taken on ONE connection held for the loop's life, not
        acquired per cycle;
    4 · a loop that loses the contention retries, so a standby can take
        over -- the desk's standby never asked again and could not.

They are AST/source checks on purpose: a comment claiming any of this
would satisfy a grep, and the last four rounds of this delivery were
mostly claims that outran their evidence.
"""
import ast
import inspect
import os

import pytest

from sportsassets.api import command_rn1x as CR
from sportsassets.workers import ext_pinnacle_loop as EXT
from sportsassets.workers import rn1x_learn_loop as LRN
from sportsassets.workers import rn1x_model_loop as MOD
from sportsassets.workers import rn1x_shadow as SHD

LOOPS = (SHD, LRN, EXT, MOD)


def test_every_writing_loop_has_a_lock_key():
    for m in LOOPS:
        assert isinstance(getattr(m, "LOCK_KEY", None), int), (
            "%s writes and must own a writer lock" % m.__name__)


def test_the_four_keys_are_distinct():
    keys = [m.LOCK_KEY for m in LOOPS]
    assert len(set(keys)) == len(keys), (
        "a shared key makes one loop a standby of another: %r" % (keys,))


def test_the_command_centre_maps_exactly_those_keys():
    """The tile must report the real keys, not a hand-copied list that
    drifts the first time a loop is added."""
    assert set(CR.WRITER_LOCKS) == {m.LOCK_KEY for m in LOOPS}
    for m in LOOPS:
        assert m.__name__.split(".")[-1] in CR.WRITER_LOCKS[m.LOCK_KEY]


def _run_fn(module):
    tree = ast.parse(inspect.getsource(module))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run":
            return node
    raise AssertionError("%s has no run()" % module.__name__)


def test_the_lock_is_taken_on_one_connection_not_per_cycle():
    """A session advisory lock returned to the pool is released.

    So `pool.acquire()` must appear ONCE in run(), and the cycle call must
    be inside that block -- which is exactly what the two new loops got
    wrong before this test existed.
    """
    for m in LOOPS:
        src = ast.unparse(_run_fn(m))
        assert "pg_try_advisory_lock" in src, m.__name__
        assert src.count("pool.acquire()") == 1, (
            "%s must hold one connection for the loop's life" % m.__name__)
        # the lock is asked for BEFORE any cycle runs
        assert src.index("pg_try_advisory_lock") < src.index("cycle("), (
            "%s must not write before it owns the lock" % m.__name__)


def test_a_standby_asks_again():
    """`if not acquired: sleep forever` is the defect this repeats past.

    The contention must be a loop, so a standby can become the writer when
    the holder goes away.
    """
    for m in LOOPS:
        fn = _run_fn(m)
        whiles = [n for n in ast.walk(fn) if isinstance(n, ast.While)]
        asking = [n for n in whiles
                  if "pg_try_advisory_lock" in ast.unparse(n.test)]
        assert asking, (
            "%s must re-ask for the lock in the loop test, not once"
            % m.__name__)
        assert any("sleep" in ast.unparse(n) for n in asking), (
            "%s must wait between attempts" % m.__name__)


def test_the_tile_does_not_overclaim_what_an_advisory_lock_does():
    """An advisory lock binds only the processes that ask for it. Saying
    otherwise would turn a coordination mechanism into a guarantee."""
    src = inspect.getsource(CR._writer_ownership_status)
    assert "advisory_is_not_mandatory" in src
    assert "not a guarantee" in src


def test_more_than_one_granted_holder_is_a_check_not_an_ok():
    """Two writers on one key is the failure this exists to surface, so it
    must not be reported as healthy."""
    src = inspect.getsource(CR._writer_ownership_status)
    tree = ast.parse(src.lstrip())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "doubled" == ast.unparse(node.test):
            found = "CHECK" in ast.unparse(node.body)
    assert found, "a doubled holder must badge CHECK"


# ── the standby, demonstrated rather than asserted ───────────────────

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")


@pg
def test_a_second_process_becomes_a_standby_and_writes_nothing():
    """CONTROLLED, through the loop's own run().

    Another connection holds ...034 first. `ext_pinnacle_loop.run` must
    then never reach a cycle: it must write the standby heartbeat and wait.
    The AST checks above say the code is shaped right; this says it
    behaves right, which is a different claim.
    """
    import asyncio
    import json

    import asyncpg

    async def main():
        holder = await asyncpg.connect(DSN, timeout=5)
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=3)
        try:
            got = await holder.fetchval(
                "SELECT pg_try_advisory_lock($1)", EXT.LOCK_KEY)
            assert got is True, "the holder must own the lock first"
            await pool.execute("DELETE FROM ingestion_state WHERE key = $1",
                               EXT.HEARTBEAT_KEY)

            cycles = []

            async def _never(_conn):
                cycles.append(1)
                return {"ran": True, "state": "SHOULD_NOT_RUN"}

            orig = EXT.cycle
            EXT.cycle = _never
            try:
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(EXT.run(lambda: pool), timeout=2.0)
            finally:
                EXT.cycle = orig

            assert not cycles, (
                "the standby ran a cycle: it would have spent provider "
                "credits and written the holder's observations")
            raw = await pool.fetchval(
                "SELECT value::text FROM ingestion_state WHERE key = $1",
                EXT.HEARTBEAT_KEY)
            assert raw, "a standby must say so rather than going quiet"
            beat = json.loads(raw)
            assert beat["state"] == "STANDBY_NOT_THE_WRITER"
            assert "ANOTHER_PROCESS_HOLDS_THE_WRITER_LOCK" in beat["refusals"]
        finally:
            await holder.execute("SELECT pg_advisory_unlock($1)",
                                 EXT.LOCK_KEY)
            await holder.close()
            await pool.close()

    asyncio.run(main())


@pg
def test_the_lock_is_released_when_the_holder_disconnects():
    """A standby that can never take over is not a standby.

    pg_try_advisory_lock is session-scoped, so the holder's disconnect must
    free the key -- which is what makes the retry loop above meaningful.
    """
    import asyncio

    import asyncpg

    async def main():
        a = await asyncpg.connect(DSN, timeout=5)
        assert await a.fetchval("SELECT pg_try_advisory_lock($1)",
                                MOD.LOCK_KEY) is True
        b = await asyncpg.connect(DSN, timeout=5)
        try:
            assert await b.fetchval("SELECT pg_try_advisory_lock($1)",
                                    MOD.LOCK_KEY) is False
            await a.close()
            for _ in range(50):
                if await b.fetchval("SELECT pg_try_advisory_lock($1)",
                                    MOD.LOCK_KEY):
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError(
                    "the key never freed: a standby could never take over")
        finally:
            await b.execute("SELECT pg_advisory_unlock_all()")
            await b.close()

    asyncio.run(main())


# ── the writer declares its own code identity ────────────────────────

def test_each_writer_reports_what_code_it_is_running():
    """A deploy id says what the service was ASKED to run. Verifying a fix
    by reading it assumes the restart happened and the import succeeded.
    The writer's own digest does not assume either."""
    for m in (EXT, MOD):
        ident = m._code_identity()
        assert ident["module"].endswith(m.__name__.split(".")[-1])
        assert isinstance(ident["source_sha256_12"], str)
        assert len(ident["source_sha256_12"]) == 12
        assert isinstance(ident["pid"], int)
    # two different modules cannot share a digest
    assert (EXT._code_identity()["source_sha256_12"]
            != MOD._code_identity()["source_sha256_12"])


def test_the_digest_changes_when_the_source_changes(monkeypatch):
    """Otherwise it is decoration: a fingerprint that does not move cannot
    tell a deployed fix from an un-deployed one."""
    before = EXT._code_identity()["source_sha256_12"]
    import inspect as _i

    real = _i.getsource
    monkeypatch.setattr(
        _i, "getsource",
        lambda obj: real(obj) + "\n# a change\n")
    after = EXT._code_identity()["source_sha256_12"]
    assert after != before
