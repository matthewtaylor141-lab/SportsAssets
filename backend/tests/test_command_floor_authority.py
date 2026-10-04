"""CAPITAL-CRITICAL: THE TRADING FLOOR READ HAS NO AUTHORITY.

GET /api/command/floor and /api/command/floor/{agent} (api/command_floor.py)
aggregate every agent's real rows for the management floor page. This proves
the boundary, the way test_intel_is_shadow_only.py does for the intel reads:

  §1 STATIC. The module imports only the standard library, FastAPI and the
     command read dependency (agents_core._pool / require_read) -- no
     order, venue, execution, ledger, paper-trading, funded or submit
     module, directly or by name. Its SQL text contains no write or DDL
     keyword (INSERT / UPDATE / DELETE / TRUNCATE / ALTER / DROP / CREATE /
     COPY / GRANT / SET ROLE), and the only SET is the transaction-local
     statement timeout.
  §2 ROUTES. Both routes are GET only and answer 401 without a command
     session.
  §3 READ ONLY AT RUNTIME. The request path opens `BEGIN READ ONLY` with a
     bounded statement timeout: a write attempted inside it is refused by
     Postgres, and the full aggregate runs to completion inside it.
  §4 LISTED. This file is on the capital-critical list.
"""
from __future__ import annotations

import ast
import os
import pathlib
import re
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "sportsassets" / "api" / "command_floor.py"
DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

STDLIB = {"__future__", "json", "time", "datetime", "typing"}
ALLOWED = {"fastapi", "sportsassets.api.agents_core"}
FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution", "ledger", "simulator", "bettor_funded", "funded",
             "paper_", "smalllive", "order", "submit", "live_",
             "actual_admission", "pinnapi", "slack_bridge", "xavier_policy",
             "derek_policy", "karen_runner", "peer_responder", "intel.runner")
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM|"
                   r"TRUNCATE|ALTER\s+|DROP\s+|CREATE\s+|COPY\s+|GRANT\s+|"
                   r"SET\s+ROLE|SET\s+SESSION)", re.I)


def _imports() -> set:
    out = set()
    for node in ast.walk(ast.parse(SRC.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = "sportsassets.api".split(".")
                base = base[:len(base) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module
            out.add(mod)
            for a in node.names:
                out.add("%s.%s" % (mod, a.name))
    return out


def _sql_strings() -> list:
    return [n.value for n in ast.walk(ast.parse(SRC.read_text()))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and re.search(r"\b(SELECT|FROM)\b", n.value)]


# ── §1 static ────────────────────────────────────────────────────────

def test_the_floor_imports_no_order_venue_ledger_or_paper_module():
    imps = _imports()
    for imp in imps:
        top = imp.split(".")[0]
        if top == "sportsassets":
            mod = imp if imp.count(".") <= 2 else imp.rsplit(".", 1)[0]
            assert mod in ALLOWED or imp in {
                "sportsassets.api.agents_core._pool",
                "sportsassets.api.agents_core.require_read"}, imp
        else:
            assert top in STDLIB or top in ALLOWED, imp
        leaf = imp.lower()
        assert not any(f in leaf for f in FORBIDDEN), imp
    assert "sportsassets.api.agents_core.require_read" in imps


def test_the_floor_sql_is_select_only():
    sqls = _sql_strings()
    assert len(sqls) >= 20                  # the scan sees the module's SQL
    for s in sqls:
        assert not WRITE.search(s), s[:120]
    src = SRC.read_text()
    sets = re.findall(r"\bSET\s+[A-Z_]+[^\"']*", src)
    assert sets and all(x.startswith("SET LOCAL statement_timeout")
                        or x.startswith("SET TRANSACTION") for x in sets), sets
    assert "transaction(readonly=True)" in src
    from sportsassets.api import command_floor as FL
    assert 0 < FL.STATEMENT_TIMEOUT_MS <= 10000


# ── §2 routes ────────────────────────────────────────────────────────

def test_the_routes_are_get_only_and_require_a_command_session():
    from sportsassets.api import app as APP

    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "").startswith("/api/command/floor"):
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert set(paths) == {"/api/command/floor", "/api/command/floor/{agent}"}
    for p, methods in paths.items():
        assert methods <= {"GET", "HEAD"}, (p, methods)
    client = TestClient(APP.app, raise_server_exceptions=False)
    for route in ("/api/command/floor", "/api/command/floor/derek",
                  "/api/command/floor/allocator"):
        assert client.get(route).status_code == 401, route
        assert client.post(route).status_code in (401, 405), route


# ── §3 read only at runtime ──────────────────────────────────────────

class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pg
async def test_the_request_path_is_a_read_only_transaction(monkeypatch):
    import asyncpg

    from sportsassets.api import command_floor as FL

    conn = await asyncpg.connect(DSN)
    try:
        async def pool():
            return _Pool(conn)

        monkeypatch.setattr(FL, "_pool", pool)

        async def write(c):
            assert await c.fetchval("SHOW transaction_read_only") == "on"
            assert await c.fetchval(
                "SELECT setting::int FROM pg_settings "
                " WHERE name='statement_timeout'") == FL.STATEMENT_TIMEOUT_MS
            await c.execute("INSERT INTO agent_tasks (task_id) VALUES ('x')")

        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await FL._read_only(write)

        class R:
            headers: dict = {}
        floor = await FL.floor_index(R())
        assert floor["read_only"] is True and len(floor["agents"]) == 7
        assert all(a["state"] in FL.STATES for a in floor["agents"])
        detail = await FL.floor_agent("karen", R())
        assert detail["agent"]["agent"] == "KAREN"
        assert detail["agent"]["authority"]["level"] == "NONE_ZERO_AUTHORITY"
    finally:
        await conn.close()


# ── §4 listed ────────────────────────────────────────────────────────

def test_this_proof_is_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_command_floor_authority.py" in listed.splitlines()
