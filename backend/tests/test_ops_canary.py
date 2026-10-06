"""GET /api/command/canary and sportsassets.ops_canary: the production canary
without a production DSN.

Static: the route is GET-only behind a COMMAND session, the modules import no
order / venue / execution / ledger / paper module, every SQL string is a
SELECT, the request runs in a READ ONLY transaction, the shared constants
match the modules they mirror, and scripts/bettor_canary.py and
research/p1_canary.sql use the same logic and are read-only.

Real Postgres (a database CREATEd here and migrated with production's own
runner): a seeded two-boot journal reads PASS on every check, and each
failure the canary exists to catch -- a regressed cursor, a checkpoint not
written by the running boot, a stale or refused retention cycle, a live
order after the cutover, a lost checkpointed record -- reads FAIL.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from sportsassets import ops_canary as OC

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SRCS = [ROOT / "sportsassets" / "ops_canary.py",
        ROOT / "sportsassets" / "api" / "command_canary.py"]
FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution", "ledger", "simulator", "funded", "paper_",
             "smalllive", "order", "submit", "live_", "actual_admission",
             "pinnapi", "render")
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM|"
                   r"TRUNCATE|ALTER\s+|DROP\s+|CREATE\s+|COPY\s+|GRANT\s+|"
                   r"SET\s+ROLE|SET\s+SESSION)", re.I)
DSN_BASE = (os.environ.get("RN1X_TEST_DSN") or os.environ.get("DATABASE_URL")
            or "")


def _imports(path):
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(("." * node.level) + (node.module or ""))
            out.update(a.name for a in node.names)
    return out


# ── static ───────────────────────────────────────────────────────────

def test_the_canary_imports_no_order_venue_ledger_or_paper_module():
    for p in SRCS:
        for imp in _imports(p):
            assert not any(f in imp.lower() for f in FORBIDDEN), (p.name, imp)


def test_every_sql_string_is_a_select():
    n = 0
    for p in SRCS:
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and re.search(r"\b(SELECT|FROM)\b", node.value):
                n += 1
                assert not WRITE.search(node.value), node.value[:120]
    assert n >= 10
    src = SRCS[1].read_text()
    assert "transaction(readonly=True)" in src
    sets = re.findall(r"\bSET\s+[A-Z_]+[^\"']*", src)
    assert sets and all(s.startswith("SET LOCAL statement_timeout")
                        for s in sets)
    for p in SRCS:
        assert "open(" not in p.read_text()


def test_the_shared_constants_match_what_they_mirror():
    from sportsassets import bettor_live_store as st
    from sportsassets.workers import retention as R
    assert OC.LANE == st.LANE
    assert OC.SETTLE_RESOLVED == st.SETTLE_RESOLVED
    assert OC.RETENTION_TABLES == tuple((t, c, f) for t, c, f, _e in R.TABLES)
    assert OC.RETENTION_EVERY_S == R.EVERY_S
    for name, table, _ts, _p in OC.LIVE_ORDER_SOURCES:
        assert re.match(r"^[a-z_]+$", table), name


def test_the_script_uses_the_shared_logic():
    src = (REPO / "scripts" / "bettor_canary.py").read_text()
    assert "from sportsassets import ops_canary as OC" in src
    assert "OC.restart_verdict(" in src
    assert "OC.journal_no_order(" in src
    assert "OC.snapshot(" in src


def test_the_research_sql_is_select_only():
    sql = (REPO / "research" / "p1_canary.sql").read_text()
    body = re.sub(r"--[^\n]*", "", sql)
    stmts = [s.strip() for s in body.split(";") if s.strip()]
    assert len(stmts) >= 6
    for s in stmts:
        assert re.match(r"^(SELECT|WITH)\b", s, re.I), s[:80]
        assert not WRITE.search(s), s[:120]


def test_the_route_is_get_only_and_requires_a_command_session():
    from sportsassets.api import app as APP
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "").startswith("/api/command/canary"):
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert set(paths) == {"/api/command/canary"}
    assert paths["/api/command/canary"] <= {"GET", "HEAD"}
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get("/api/command/canary").status_code == 401
    assert client.post("/api/command/canary").status_code in (401, 405)


def test_partial_before_scalars_are_refused():
    from fastapi import HTTPException
    from sportsassets.api import command_canary as C
    assert C.parse_before(None, None, None, None) is None
    with pytest.raises(HTTPException):
        C.parse_before(5, None, 3, None)
    with pytest.raises(HTTPException):
        C.parse_before(5, 4, 3, "ok,bad boot;")
    assert C.parse_before(5, 4, 3, "a,b")["boots"] == ["a", "b"]


@pytest.mark.parametrize("before,after,still,exp,want", [
    ({"boot_ids": ["a"], "journal_rows": 5, "cursors": 2},
     {"boot_ids": ["a"], "journal_rows": 9, "cursors": 2}, 5, 5, OC.FAIL),
    ({"boot_ids": ["a"], "journal_rows": 5, "cursors": 2},
     {"boot_ids": ["a", "b"], "journal_rows": 9, "cursors": 2}, 5, 5, OC.PASS),
    ({"boot_ids": ["a"], "journal_rows": 5, "cursors": 2},
     {"boot_ids": ["a", "b"], "journal_rows": 9, "cursors": 2}, 4, 5, OC.FAIL),
    ({"boot_ids": ["a"], "journal_rows": 5, "cursors": 2},
     {"boot_ids": ["a", "b"], "journal_rows": 9, "cursors": 1}, 5, 5, OC.FAIL),
])
def test_the_restart_rule(before, after, still, exp, want):
    assert OC.restart_verdict(before, after, still_present=still,
                              expected_present=exp)[0] == want


def test_this_proof_is_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_ops_canary.py" in listed.splitlines()


# ── real Postgres ────────────────────────────────────────────────────

SHA = "a" * 40


@pytest.fixture(scope="module")
def db():
    asyncpg = pytest.importorskip("asyncpg")
    if not DSN_BASE:
        pytest.skip("needs RN1X_TEST_DSN / DATABASE_URL")
    name = "ops_canary_" + uuid.uuid4().hex[:10]

    async def create():
        try:
            a = await asyncpg.connect(DSN_BASE, timeout=4)
        except Exception:                  # noqa: BLE001 -- no server: skip
            return False
        try:
            await a.execute(f'CREATE DATABASE "{name}"')
        finally:
            await a.close()
        return True

    if not asyncio.run(create()):
        pytest.skip("no local postgres")
    dsn = DSN_BASE.rsplit("/", 1)[0] + "/" + name
    r = subprocess.run([sys.executable, "-m", "sportsassets.scripts.migrate"],
                       cwd=ROOT, env=dict(os.environ, DATABASE_URL=dsn),
                       capture_output=True, text=True, timeout=1200)
    assert r.returncode == 0, r.stderr[-3000:]

    from sportsassets import bettor_live_store as st

    async def seed():
        c = await asyncpg.connect(dsn)
        try:
            await c.execute(st.DDL)
            await _seed(c)
        finally:
            await c.close()
    asyncio.run(seed())
    yield dsn

    async def drop():
        a = await asyncpg.connect(DSN_BASE, timeout=4)
        await a.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await a.close()
    asyncio.run(drop())


async def _seed(c):
    lane = OC.LANE
    i = 0
    for boot, ages in (("bootA", (600, 590, 580)), ("bootB", (60, 50, 40))):
        for age in ages:
            for m in ("m1", "m2"):
                i += 1
                await c.execute(
                    "INSERT INTO bettor_live_journal (lane, boot_id, "
                    " record_key, loop_version, kind, market_id, source_ts, "
                    " status, selected, record, written_at) VALUES "
                    "($1, $2, $3, 'v1', 'DECISION', $4, "
                    " to_char((now() - make_interval(secs => $5)) AT TIME ZONE "
                    "  'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
                    " 'DECIDED', 'NO_TRADE', $6::jsonb, "
                    " now() - make_interval(secs => $5))",
                    lane, boot, "k%03d" % i, m, float(age),
                    json.dumps({"executed": False, "size_contracts": 0}))
    for m in ("m1", "m2"):
        await c.execute(
            "INSERT INTO bettor_live_cursor (lane, market_id, first_seen_at, "
            " last_seen_at, last_source_ts) VALUES ($1, $2, "
            " extract(epoch FROM now()) - 700, extract(epoch FROM now()) - 30,"
            " to_char((now() - interval '35 seconds') AT TIME ZONE 'UTC', "
            "  'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))", lane, m)
    await c.execute(
        "INSERT INTO bettor_live_ledger (lane, boot_id, loop_version, "
        " schema_version, snapshot) VALUES ($1, 'bootB', 'v1', 1, '{}')", lane)
    await c.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ('workers_boot', $1) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        json.dumps({"commit": SHA[:7], "commit_sha": SHA, "at": "now",
                    "venue_writes": "LOCKED"}))
    await c.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ('retention_last', "
        " jsonb_build_object('at', to_char(now() AT TIME ZONE 'UTC', "
        "  'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'), 'refused', null, "
        "  'deleted_total', 0, 'tables', jsonb_build_object("
        "  'ai_trades', jsonb_build_object('keep_days', 97), "
        "  'copy_probes', jsonb_build_object('keep_days', 37)))) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value")
    hid = await c.fetchval(
        "INSERT INTO live_parity_hook_installs (process, commit_sha, hooks) "
        "VALUES ('workers', $1, ARRAY['decision']) RETURNING install_id", SHA)
    await c.execute(
        "INSERT INTO live_parity_cutover (release_sha, api_sha, workers_sha, "
        " migrations, decision_logic_hash, decision_logic_files, "
        " hook_install_id, small_live_mode, small_live_halted, "
        " capital_activated, evidence, recorded_by) VALUES ($1, $1, $1, "
        " ARRAY['225','226'], $2, '{}'::jsonb, $3, 'SHADOW', false, false, "
        " '{}'::jsonb, 'Matt Taylor')", SHA, "b" * 64, hid)


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self, timeout=None):
        yield self.conn


async def _read(dsn, **kw):
    import asyncpg
    from sportsassets.api import command_canary as C
    conn = await asyncpg.connect(dsn)
    try:
        async def pool():
            return _Pool(conn)
        orig = C._pool
        C._pool = pool
        try:
            class Resp:
                headers: dict = {}
            return await C.canary(Resp(), **({"before_max_id": None,
                                               "before_rows": None,
                                               "before_cursors": None,
                                               "before_boots": None} | kw))
        finally:
            C._pool = orig
    finally:
        await conn.close()


async def _exec(dsn, *sqls):
    import asyncpg
    c = await asyncpg.connect(dsn)
    try:
        for s in sqls:
            await c.execute(s)
    finally:
        await c.close()


def test_the_request_path_is_read_only(db):
    import asyncpg
    from sportsassets.api import command_canary as C

    async def go():
        conn = await asyncpg.connect(db)
        try:
            async def pool():
                return _Pool(conn)
            orig, C._pool = C._pool, pool
            try:
                async def write(c):
                    assert await c.fetchval("SHOW transaction_read_only") == "on"
                    await c.execute("INSERT INTO ingestion_state (key, value) "
                                    "VALUES ('x_canary_probe', '{}'::jsonb)")
                with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                    await C._read_only(write)
            finally:
                C._pool = orig
        finally:
            await conn.close()
    asyncio.run(go())


def test_a_healthy_two_boot_database_passes_every_check(db):
    out = asyncio.run(_read(db))
    assert out["read_only"] is True
    assert out["checks"] == {"checkpoint_by_current_boot": OC.PASS,
                             "cursors_never_regressed": OC.PASS,
                             "retention": OC.PASS, "no_order": OC.PASS}, (
        json.dumps({k: out[k] for k in ("checkpoint", "cursors", "retention",
                                        "no_order")}, default=str)[:3000])
    assert out["verdict"] == OC.PASS
    boots = [b["boot_id"] for b in out["boots"]["journal"]["newest_first"]]
    assert boots == ["bootB", "bootA"]
    assert out["no_order"]["small_live"]["effective"] == "SHADOW"
    assert out["cursors"]["restart"] == {
        "from_boot": "bootA", "to_boot": "bootB",
        "previous_last_at": out["cursors"]["restart"]["previous_last_at"],
        "current_first_at": out["cursors"]["restart"]["current_first_at"],
        "writers_overlapped": False}
    cmp_ = out["checkpoint"]["compare_after_restart"]
    assert cmp_["before_rows"] == 12 and cmp_["before_cursors"] == 2


def test_a_checkpoint_compared_restart(db):
    async def first_boot_scalars():
        import asyncpg
        c = await asyncpg.connect(db)
        try:
            return await c.fetchrow(
                "SELECT max(id) AS m, count(*) AS n FROM bettor_live_journal "
                " WHERE boot_id = 'bootA'")
        finally:
            await c.close()
    r = asyncio.run(first_boot_scalars())
    out = asyncio.run(_read(db, before_max_id=r["m"], before_rows=r["n"],
                            before_cursors=2, before_boots="bootA"))
    assert out["restart"]["state"] == OC.PASS, out["restart"]
    assert out["restart"]["new_boots"] == ["bootB"]
    # the same scalars with no boot change: no restart, FAIL
    out = asyncio.run(_read(db, before_max_id=r["m"], before_rows=r["n"],
                            before_cursors=2, before_boots="bootA,bootB"))
    assert out["restart"]["state"] == OC.FAIL
    # more checkpointed rows claimed than survive: records were LOST
    out = asyncio.run(_read(db, before_max_id=r["m"], before_rows=r["n"] + 1,
                            before_cursors=2, before_boots="bootA"))
    assert out["restart"]["state"] == OC.FAIL
    assert out["verdict"] == OC.FAIL


def test_a_regressed_cursor_fails(db):
    asyncio.run(_exec(db,
        "UPDATE bettor_live_cursor SET last_source_ts = '2020-01-01T00:00:00Z'"
        " WHERE market_id = 'm1'"))
    try:
        out = asyncio.run(_read(db))
        assert out["checks"]["cursors_never_regressed"] == OC.FAIL
        assert out["cursors"]["behind_journal_source_ts"] == 1
    finally:
        asyncio.run(_exec(db,
            "UPDATE bettor_live_cursor SET last_source_ts = to_char((now() - "
            "interval '35 seconds') AT TIME ZONE 'UTC', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') WHERE market_id = 'm1'"))
    asyncio.run(_exec(db, "DELETE FROM bettor_live_cursor WHERE market_id='m2'"))
    try:
        out = asyncio.run(_read(db))
        assert out["cursors"]["missing_cursor"] == 1
        assert out["checks"]["cursors_never_regressed"] == OC.FAIL
    finally:
        asyncio.run(_exec(db,
            "INSERT INTO bettor_live_cursor (lane, market_id, last_seen_at, "
            " last_source_ts) VALUES ('%s', 'm2', extract(epoch FROM now()) - "
            "30, to_char((now() - interval '35 seconds') AT TIME ZONE 'UTC', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))" % OC.LANE))


def test_a_checkpoint_from_an_earlier_boot_fails(db):
    asyncio.run(_exec(db, "UPDATE bettor_live_ledger SET boot_id = 'bootA'"))
    try:
        out = asyncio.run(_read(db))
        assert out["checks"]["checkpoint_by_current_boot"] == OC.FAIL
    finally:
        asyncio.run(_exec(db, "UPDATE bettor_live_ledger SET boot_id='bootB'"))


def test_stale_or_refused_retention_fails(db):
    good = ("UPDATE ingestion_state SET value = jsonb_set(value, '{at}', "
            "to_jsonb(to_char(now() AT TIME ZONE 'UTC', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'))) WHERE key='retention_last'")
    asyncio.run(_exec(db,
        "UPDATE ingestion_state SET value = jsonb_set(value, '{at}', "
        "'\"2020-01-01T00:00:00+00:00\"') WHERE key = 'retention_last'"))
    try:
        assert asyncio.run(_read(db))["checks"]["retention"] == OC.FAIL
    finally:
        asyncio.run(_exec(db, good))
    asyncio.run(_exec(db,
        "UPDATE ingestion_state SET value = jsonb_set(value, '{refused}', "
        "'\"clock skew\"') WHERE key = 'retention_last'"))
    try:
        assert asyncio.run(_read(db))["checks"]["retention"] == OC.FAIL
    finally:
        asyncio.run(_exec(db,
            "UPDATE ingestion_state SET value = jsonb_set(value, "
            "'{refused}', 'null') WHERE key = 'retention_last'"))
    assert asyncio.run(_read(db))["checks"]["retention"] == OC.PASS


def test_a_live_order_after_the_cutover_fails(db):
    asyncio.run(_exec(db,
        "INSERT INTO live_orders (asset, side, his_price, limit_price, "
        " requested_usd, requested_shares) VALUES ('tok', 'BUY', 0.5, 0.51, "
        " 1, 2)"))
    try:
        out = asyncio.run(_read(db))
        assert out["checks"]["no_order"] == OC.FAIL
        assert out["no_order"]["live_orders"]["live_orders"]["since_cutover"] == 1
    finally:
        asyncio.run(_exec(db, "DELETE FROM live_orders"))
    assert asyncio.run(_read(db))["checks"]["no_order"] == OC.PASS


def test_an_unlocked_worker_is_not_established(db):
    asyncio.run(_exec(db,
        "UPDATE ingestion_state SET value = jsonb_set(value, "
        "'{venue_writes}', '\"NOT_LOCKED\"') WHERE key = 'workers_boot'"))
    try:
        out = asyncio.run(_read(db))
        assert out["checks"]["no_order"] == OC.UNKNOWN
        assert out["verdict"] == OC.UNKNOWN
    finally:
        asyncio.run(_exec(db,
            "UPDATE ingestion_state SET value = jsonb_set(value, "
            "'{venue_writes}', '\"LOCKED\"') WHERE key = 'workers_boot'"))


def test_the_research_sql_runs_on_the_same_database(db):
    """research/p1_canary.sql executes, statement by statement, inside a
    READ ONLY transaction against the seeded database."""
    import asyncpg
    sql = (REPO / "research" / "p1_canary.sql").read_text()
    body = re.sub(r"--[^\n]*", "", sql)
    stmts = [s.strip() for s in body.split(";") if s.strip()]

    async def go():
        c = await asyncpg.connect(db)
        try:
            async with c.transaction(readonly=True):
                return [await c.fetch(s) for s in stmts]
        finally:
            await c.close()
    res = asyncio.run(go())
    assert all(r is not None for r in res)
    flat = {k: v for rows in res for row in rows for k, v in dict(row).items()}
    assert flat.get("cursor_regressions") == 0
    assert flat.get("live_orders_since_cutover") == 0
    assert flat.get("small_live_effective") == "SHADOW"
