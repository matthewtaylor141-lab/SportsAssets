"""CAPITAL-CRITICAL: THE LEARNING LAYER (migration 218) HAS NO AUTHORITY.

  §1 STATIC. Every module in sportsassets/poslearn imports only the
     standard library, its own package, the shadow intel helpers it reuses
     (common, calibration, sizing, reads) and the pure calibration
     measurement -- over the TRANSITIVE import closure. No venue, order,
     execution, paper-ledger, funded or agent-execution module is reachable.
  §2 WRITES. Every SQL write in the package targets a poslearn_* table and
     lives in store.py; NO module anywhere in sportsassets writes
     poslearn_human_approvals (the human record is an operator action).
  §3 INDEPENDENCE. Audrey's evaluation imports none of the runner's scoring
     code; Karen's review imports only the pure helpers.
  §4 NOTHING IN PRODUCTION READS IT. No module outside the package and its
     read routes names a poslearn_* table; only api/app.py (arming) and the
     read routes import the package.
  §5 THE READS are GET only, return 401 without a command session, and read
     only poslearn_* tables.
  §6 THE RUNNER is kill-switchable (POS_LEARN=off), armed in the lifespan,
     bounded, single-instance (advisory lock) and failure-isolated.
  §7 THE SCHEMA stores SHADOW only, never applied, production_effect NONE;
     the migration is idempotent and its rollback drops only poslearn_*.
  §8 these proofs are on the capital-critical list.
"""
from __future__ import annotations

import ast
import pathlib
import re
import time
from contextlib import asynccontextmanager

import asyncpg
import pytest
from fastapi.testclient import TestClient

from sportsassets.poslearn import runner as RUN

try:
    from tests import poslearn_fixture as P
except ImportError:                                             # pragma: no cover
    import poslearn_fixture as P

pg = pytest.mark.skipif(not P.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
MODULES = sorted((PKG / "poslearn").glob("*.py"))
API = PKG / "api" / "command_learning_os.py"

STDLIB = {"__future__", "asyncio", "datetime", "hashlib", "json", "logging",
          "math", "os", "random", "time", "uuid", "zoneinfo", "typing",
          "contextlib", "dataclasses", "decimal", "collections", "bisect"}
ALLOWED_PROJECT = {"sportsassets", "sportsassets.intel",
                   "sportsassets.intel.common", "sportsassets.intel.calibration",
                   "sportsassets.intel.sizing", "sportsassets.intel.reads",
                   "sportsassets.bettor_source_calibration",
                   "sportsassets.bettor_pinnacle_devig"}
FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution", "bettor_paper", "paper_", "funded", "smalllive",
             "order", "live_", "actual_admission", "pinnapi_owner",
             "pinnapi_feed_runtime", "submit", "allocator", "xavier",
             "derek", "audrey_intel")
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE|"
                   r"ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+TABLE|COPY)\s+"
                   r"([A-Za-z_][A-Za-z0-9_]*)")


def _module_name(path):
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path):
    mod = _module_name(path)
    pkg = mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg.split(".")
                base = base[:len(base) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module
                                          else []))
            else:
                target = node.module
            out.add(target)
            for a in node.names:
                out.add("%s.%s" % (target, a.name))
    return out


def _path_of(name):
    p = ROOT / pathlib.Path(*name.split("."))
    if p.with_suffix(".py").exists():
        return p.with_suffix(".py")
    if (p / "__init__.py").exists():
        return p / "__init__.py"
    return None


def _sql_writes(path):
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for kw, table in WRITE.findall(node.value):
                if kw.upper() == "UPDATE" and table.upper() == "SET":
                    continue
                out.append((" ".join(kw.split()).upper(), table))
    return out


# ── §1 ──────────────────────────────────────────────────────────────

def test_the_transitive_import_closure_holds_no_venue_or_order_module():
    seen, todo = set(), [_module_name(p) for p in MODULES]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        path = _path_of(name)
        if path is None:
            continue
        for imp in _imports(path):
            top = imp.split(".")[0]
            if top != "sportsassets":
                assert top in STDLIB, (name, imp)
                continue
            if _path_of(imp) is None:
                continue
            # the shadow intel layer is itself pinned shadow-only by
            # tests/test_intel_is_shadow_only.py; its modules are reusable
            assert (imp.startswith("sportsassets.poslearn")
                    or imp.startswith("sportsassets.intel.")
                    or imp in ALLOWED_PROJECT), (name, imp)
            todo.append(imp)
    reached = {s for s in seen if s.startswith("sportsassets")}
    assert "sportsassets.intel.calibration" in reached      # reused
    assert "sportsassets.intel.sizing" in reached           # reused
    for name in reached:
        leaf = name.rsplit(".", 1)[-1]
        assert not any(f in leaf for f in FORBIDDEN), name


def test_eddie_is_an_interface_never_an_import():
    for p in MODULES:
        for imp in _imports(p):
            assert "eddie" not in imp.lower(), (p.name, imp)
    src = (PKG / "poslearn" / "reads.py").read_text()
    assert "to_regclass('eddie_execution_estimates')" in src


def test_no_marco_identifier_remains():
    for p in MODULES + [API]:
        assert "marco" not in p.read_text().lower(), p.name


# ── §2 ──────────────────────────────────────────────────────────────

def test_every_sql_write_targets_the_layers_own_tables_from_the_store():
    found = 0
    for path in MODULES:
        for kw, table in _sql_writes(path):
            found += 1
            assert table.startswith("poslearn_"), (path.name, kw, table)
            assert kw in ("INSERT INTO", "UPDATE", "DELETE FROM"), (
                path.name, kw, table)
            assert table != "poslearn_human_approvals", path.name
            if path.name != "store.py":
                raise AssertionError("a write outside store.py: %s %s %s"
                                     % (path.name, kw, table))
    assert found >= 12


def test_no_code_path_in_the_repository_writes_a_human_approval():
    pat = re.compile(r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"
                     r"poslearn_human_approvals", re.I)
    for p in PKG.rglob("*.py"):
        assert not pat.search(p.read_text(errors="ignore")), p
    for p in (ROOT / "tools").rglob("*.py"):
        assert not pat.search(p.read_text(errors="ignore")), p


# ── §3 ──────────────────────────────────────────────────────────────

def test_audrey_recomputes_without_the_runners_scoring_code():
    imps = _imports(PKG / "poslearn" / "audrey_review.py")
    assert {i.split(".")[0] for i in imps} <= STDLIB, imps
    kar = _imports(PKG / "poslearn" / "karen_review.py")
    assert {i for i in kar if i.startswith("sportsassets")} <= {
        "sportsassets.poslearn", "sportsassets.poslearn.common"}, kar


# ── §4 ──────────────────────────────────────────────────────────────

def test_nothing_outside_the_layer_reads_or_imports_it():
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (poslearn_\w+)",
                            P.UP))
    assert len(tables) == 12
    for p in PKG.rglob("*.py"):
        if p.parent == PKG / "poslearn" or p == API:
            continue
        text = p.read_text(errors="ignore")
        for t in tables:
            assert t not in text, (p, t)
        if "poslearn" in text:
            assert p == PKG / "api" / "app.py", p
    app = (PKG / "api" / "app.py").read_text()
    assert "from ..poslearn import runner as _POSLEARN" in app


# ── §5 ──────────────────────────────────────────────────────────────

ROUTES = ("/api/command/tournament/models", "/api/command/tournament/agents",
          "/api/command/profitability/edge-confidence",
          "/api/command/profitability/avoidance", "/api/command/experiments")


def test_the_routes_are_get_only_and_require_a_command_session():
    from sportsassets.api import app as APP

    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") in ROUTES:
            paths.setdefault(r.path, set()).update(
                getattr(r, "methods", set()) or set())
    assert set(ROUTES) == set(paths)
    for p, methods in paths.items():
        assert methods <= {"GET", "HEAD"}, (p, methods)
    client = TestClient(APP.app, raise_server_exceptions=False)
    for route in ROUTES:
        assert client.get(route).status_code == 401, route
        assert client.post(route).status_code in (401, 405), route


def test_the_read_routes_read_only_the_layers_tables():
    assert not _sql_writes(API)
    src = API.read_text()
    tables = set(re.findall(r"(?<!epoch )\bFROM\s+([a-z_]+)", src))
    assert tables and all(t.startswith("poslearn_") for t in tables), tables


# ── §6 ──────────────────────────────────────────────────────────────

def test_the_runner_is_kill_switchable_bounded_and_armed(monkeypatch):
    monkeypatch.setenv("POS_LEARN", "off")
    assert RUN.enabled() is False
    monkeypatch.setenv("POS_LEARN", "0")
    assert RUN.enabled() is False
    monkeypatch.delenv("POS_LEARN")
    assert RUN.enabled() is True
    app_src = (PKG / "api" / "app.py").read_text()
    assert "_POSLEARN.run(_cap_pool)" in app_src
    assert "poslearn_task" in app_src.split("tasks = [t for t in (")[1][:500]
    from sportsassets.intel import runner as INTEL
    assert RUN.LOCK_KEY != INTEL.LOCK_KEY
    assert RUN.CYCLE_S >= 300 and RUN.STATEMENT_TIMEOUT_MS <= 30000
    assert RUN.MAX_CAPTURE <= 1000


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pg
async def test_one_runner_at_a_time():
    holder = await asyncpg.connect(P.DSN)
    other = await asyncpg.connect(P.DSN)
    try:
        assert await holder.fetchval("SELECT pg_try_advisory_lock($1)",
                                     RUN.LOCK_KEY)
        got = await RUN._one(_Pool(other))
        assert got == {"ran": False,
                       "why": "STANDBY_ANOTHER_RUNNER_HOLDS_LOCK"}
    finally:
        await holder.execute("SELECT pg_advisory_unlock($1)", RUN.LOCK_KEY)
        await holder.close()
        await other.close()


@pg
async def test_a_failing_component_does_not_stop_the_cycle(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("synthetic capture failure")

    monkeypatch.setattr(RUN, "capture", boom)
    conn, tr = await P.open_tx()
    try:
        before = await P.protected_counts(conn)
        got = await RUN.run_cycle(conn, now=time.time())
        c = got["components"]
        assert c["CAPTURE"] == "FAILED"
        assert c["REGISTER"] == c["MODEL_TOURNAMENT"] == "OK"
        assert c["AGENT_TOURNAMENT"] == c["EXPERIMENTS"] == "OK"
        err = await conn.fetchval(
            "SELECT error FROM poslearn_runs WHERE run_id=$1 AND "
            " component='CAPTURE'", got["run_id"])
        assert "synthetic capture failure" in err
        assert await conn.fetchval(
            "SELECT status FROM poslearn_runs WHERE run_id=$1 AND "
            " component='CYCLE'", got["run_id"]) == "FAILED"
        assert await P.protected_counts(conn) == before
    finally:
        await P.close_tx(conn, tr)


@pg
async def test_without_the_migration_the_runner_idles():
    conn = await asyncpg.connect(P.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(P.DOWN)
        assert await RUN.run_cycle(conn) == {
            "ran": False, "why": "MIGRATION_218_NOT_APPLIED"}
    finally:
        await tr.rollback()
        await conn.close()


# ── §7 ──────────────────────────────────────────────────────────────

@pg
async def test_the_schema_stores_shadow_only_and_never_applied():
    conn, tr = await P.open_tx()
    try:
        for sql in (
                "INSERT INTO poslearn_snapshots (run_id, component, "
                " computed_at, payload, version, label) VALUES ('r', "
                " 'AVOIDANCE', now(), '{\"label\":\"SHADOW\"}', 'v', 'LIVE')",
                "INSERT INTO poslearn_snapshots (run_id, component, "
                " computed_at, payload, version) VALUES ('r', 'AVOIDANCE', "
                " now(), '{\"label\":\"LIVE\"}', 'v')",
                "INSERT INTO poslearn_runs (run_id, component, started_at, "
                " status, version) VALUES ('r', 'ORDERS', now(), 'OK', 'v')"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(sql)
            await sp.rollback()
        cols = await conn.fetch(
            "SELECT table_name FROM information_schema.columns "
            " WHERE table_name LIKE 'poslearn\\_%' AND column_name='label'")
        assert len(cols) == 12
        checks = await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS d FROM "
            " pg_constraint WHERE conname LIKE 'poslearn%effect_ck' "
            "    OR conname = 'poslearn_fc_never_applied_ck'")
        assert len(checks) >= 5
        assert all("'NONE'" in c["d"] or "false" in c["d"] for c in checks)
    finally:
        await P.close_tx(conn, tr)


@pg
async def test_migration_218_is_idempotent_and_its_rollback_drops_only_it():
    stmts = [ln for ln in P.DOWN.splitlines() if ln.startswith("DROP ")]
    assert stmts and all(re.match(r"DROP (TABLE|FUNCTION) IF EXISTS "
                                  r"poslearn_", ln) for ln in stmts), stmts
    conn = await asyncpg.connect(P.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        before = await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relkind = 'r' "
            "   AND relname NOT LIKE 'poslearn\\_%' "
            "   AND relnamespace = 'public'::regnamespace")
        await conn.execute(P.UP)
        await conn.execute(P.UP)
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relname LIKE "
            " 'poslearn\\_%' AND relkind = 'r'") == 12
        await conn.execute(P.DOWN)
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relname LIKE "
            " 'poslearn\\_%' AND relkind = 'r'") == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_proc WHERE proname LIKE 'poslearn\\_%'"
        ) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relkind = 'r' "
            "   AND relname NOT LIKE 'poslearn\\_%' "
            "   AND relnamespace = 'public'::regnamespace") == before
        await conn.execute(P.UP)
    finally:
        await tr.rollback()
        await conn.close()


# ── §8 ──────────────────────────────────────────────────────────────

def test_these_proofs_are_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    for name in ("test_poslearn_authority.py", "test_poslearn_tournaments.py",
                 "test_poslearn_models_and_meta.py",
                 "test_poslearn_overfitting_defences.py"):
        assert "tests/%s" % name in listed.splitlines(), name
