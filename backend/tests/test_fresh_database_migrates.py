"""A BRAND-NEW DATABASE MIGRATES WITH PRODUCTION'S RUNNER AND NOTHING ELSE.

Before: migration 031 altered `us_premap`, which no migration creates (the
collector's workers/premap._ensure_table built it in production), so
`python -m sportsassets.scripts.migrate` on an empty database stopped at 031
with `relation "us_premap" does not exist`, and CI pre-created the table by
hand. migrate.bootstrap_collector_tables now builds it when absent.

This test CREATEs a database on the local server, runs the real runner as a
subprocess (exactly as start.sh does), and requires schema_migrations to hold
every file. It also pins the rollback convention: from 137 on, every
migration ships migrations/rollback/<name>.down.sql.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import subprocess
import sys
import uuid

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
MIG = BACKEND / "migrations"
ROLLBACK_FROM = 137
DSN_BASE = (os.environ.get("RN1X_TEST_DSN") or os.environ.get("DATABASE_URL")
            or "postgresql://postgres@127.0.0.1:5432/postgres")


def _files():
    return sorted(p.name for p in MIG.glob("*.sql"))


def test_every_migration_from_137_has_a_rollback():
    missing = [n for n in _files()
               if int(n.split("_", 1)[0]) >= ROLLBACK_FROM
               and not (MIG / "rollback" / n.replace(".sql", ".down.sql")).is_file()]
    assert missing == [], "migrations without a rollback: %s" % missing


def test_every_rollback_names_a_migration():
    names = {n[:-4] for n in _files()}
    orphans = [p.name for p in (MIG / "rollback").glob("*.down.sql")
               if p.name[:-len(".down.sql")] not in names]
    assert orphans == []


def test_migration_versions_are_unique():
    nums = [n.split("_", 1)[0] for n in _files()]
    dup = sorted({x for x in nums if nums.count(x) > 1})
    assert dup == [], "duplicate migration numbers: %s" % dup


@pytest.fixture(scope="module")
def fresh_db():
    asyncpg = pytest.importorskip("asyncpg")

    name = "fresh_mig_" + uuid.uuid4().hex[:10]

    async def _create():
        try:
            admin = await asyncpg.connect(DSN_BASE, timeout=4)
        except Exception:                  # noqa: BLE001 -- no server: skip, never fake
            return False
        try:
            await admin.execute(f'CREATE DATABASE "{name}"')
        finally:
            await admin.close()
        return True

    if not asyncio.run(_create()):
        pytest.skip("no local postgres for the fresh-database migration")
    dsn = DSN_BASE.rsplit("/", 1)[0] + "/" + name
    yield dsn

    async def _drop():
        a = await asyncpg.connect(DSN_BASE, timeout=4)
        await a.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await a.close()

    asyncio.run(_drop())


def _migrate(dsn):
    env = dict(os.environ, DATABASE_URL=dsn)
    return subprocess.run(
        [sys.executable, "-m", "sportsassets.scripts.migrate"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=1200)


def _q(dsn, sql):
    import asyncpg

    async def go():
        c = await asyncpg.connect(dsn, timeout=4)
        try:
            return await c.fetchval(sql)
        finally:
            await c.close()
    return asyncio.run(go())


def test_an_empty_database_applies_every_migration(fresh_db):
    assert _q(fresh_db, "SELECT to_regclass('us_premap') IS NULL") is True
    r = _migrate(fresh_db)
    assert r.returncode == 0, r.stderr[-4000:]
    assert "bootstrap: created us_premap" in r.stderr
    assert _q(fresh_db, "SELECT count(*) FROM schema_migrations") == len(_files())
    assert _q(fresh_db, "SELECT count(*) FROM schema_migrations "
                        " WHERE content_sha IS NULL") == 0
    # the premap columns later migrations depend on are there
    assert _q(fresh_db, "SELECT count(*) FROM information_schema.columns "
                        " WHERE table_name = 'us_premap' AND column_name IN "
                        " ('listing_state', 'signed', 'team_abbr')") == 3


def test_a_second_run_is_a_no_op(fresh_db):
    before = _q(fresh_db, "SELECT count(*) FROM schema_migrations")
    r = _migrate(fresh_db)
    assert r.returncode == 0, r.stderr[-4000:]
    assert "bootstrap: created us_premap" not in r.stderr
    assert "applying" not in r.stderr
    assert _q(fresh_db, "SELECT count(*) FROM schema_migrations") == before


def test_the_new_slack_rollbacks_apply_twice(fresh_db):
    import asyncpg

    async def go():
        c = await asyncpg.connect(fresh_db, timeout=4)
        try:
            for name in ("191_slack_requested_by", "190_slack_agent_bridge"):
                sql = (MIG / "rollback" / f"{name}.down.sql").read_text()
                await c.execute(sql)
                await c.execute(sql)          # idempotent
            gone = await c.fetchval(
                "SELECT to_regclass('agent_slack_delivery') IS NULL")
            # re-apply 190 + 191 so the module database is whole again
            for name in ("190_slack_agent_bridge", "191_slack_requested_by"):
                await c.execute((MIG / f"{name}.sql").read_text())
            return gone
        finally:
            await c.close()
    assert asyncio.run(go()) is True
