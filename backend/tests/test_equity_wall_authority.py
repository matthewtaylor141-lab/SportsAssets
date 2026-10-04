"""CAPITAL-CRITICAL: THE LIVE EQUITY WALL HAS NO AUTHORITY.

  §1 THE ROUTES. /api/command/equity/* are GET only and answer 401 without a
     COMMAND session (the same require_read -> require_command as every
     /api/command read).
  §2 STATIC. api/command_equity.py imports only the standard library,
     FastAPI, the shared read dependency (agents_core) and the paper ledger's
     read function -- over its TRANSITIVE project imports no order, venue,
     execution, executor, funded or submit module is reachable (the paper
     ledger's lazy fee import of the funded book is a pure fee table and is
     named below). It makes no network call and holds no SQL write.
  §3 RUNTIME. The live read and the curve run inside a READ ONLY
     transaction (SHOW transaction_read_only = on) under a statement timeout.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

try:
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
MODULE = PKG / "api" / "command_equity.py"
ROUTES = ("/api/command/equity/live", "/api/command/equity/curve")

STDLIB = {"__future__", "asyncio", "datetime", "hashlib", "json", "math",
          "os", "time", "zoneinfo"}
ALLOWED_PROJECT = {"sportsassets.api.agents_core",
                   "sportsassets.bettor_paper_ledger",
                   # (223) the paper sleeves reader: SELECTs over the paper
                   # records + paper_sleeve_classifications; its one write
                   # (the backstop) is never called from here
                   "sportsassets.bettor_paper_sleeves",
                   # SMALL LIVE -- BETTOR ORIGINATED status: pure derivation
                   # + SELECTs, no venue / order / execution import
                   "sportsassets.bettor_originated_status"}
FORBIDDEN = ("execmirror", "kalshi_venue", "kalshi_orders", "kalshi_account",
             "pmus", "clob", "executor", "execution", "funded_execution",
             "funded_management", "order", "submit", "smalllive", "live_",
             "venue_sdk", "httpx", "requests", "urllib", "aiohttp")
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM|"
                   r"TRUNCATE|ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+TABLE|"
                   r"COPY\s|GRANT|pg_notify|nextval)", re.I)


# ── §1 routes ────────────────────────────────────────────────────────

def test_the_routes_are_get_only_and_require_a_command_session():
    from sportsassets.api import app as APP

    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "").startswith("/api/command/equity"):
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert set(ROUTES) == set(paths), paths
    for p, methods in paths.items():
        assert methods <= {"GET", "HEAD"}, (p, methods)
    client = TestClient(APP.app, raise_server_exceptions=False)
    for route in ROUTES:
        assert client.get(route).status_code == 401, route
        assert client.post(route).status_code in (401, 405), route
    assert client.get("/api/command/equity/curve?book=BOTH").status_code in (
        401, 422)


# ── §2 static ────────────────────────────────────────────────────────

def _imports(path: pathlib.Path) -> set:
    rel = path.relative_to(ROOT).with_suffix("")
    pkg = ".".join(rel.parts[:-1])
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
                cand = "%s.%s" % (target, a.name)
                if (ROOT / pathlib.Path(*cand.split("."))).with_suffix(
                        ".py").exists():
                    out.add(cand)
    return out


def test_the_module_imports_nothing_with_authority():
    imps = _imports(MODULE)
    for imp in imps:
        top = imp.split(".")[0]
        if top == "sportsassets":
            if imp == "sportsassets.api" or imp == "sportsassets":
                continue
            assert imp in ALLOWED_PROJECT, imp
        else:
            assert top in STDLIB or top == "fastapi", imp
        leaf = imp.rsplit(".", 1)[-1]
        assert not any(f in leaf for f in FORBIDDEN), imp
    assert "sportsassets.bettor_paper_ledger" in imps


def test_the_paper_ledger_reached_from_here_is_a_reader_for_this_purpose():
    """bettor_paper_ledger is the paper book's own module (shared by every
    paper read model). Its module-level imports are the standard library and
    the paper limits table; its only funded import is the lazy fee table in
    default_fee_fn, which balances() never calls."""
    src = (PKG / "bettor_paper_ledger.py").read_text()
    tree = ast.parse(src)
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = set()
    for n in top:
        if isinstance(n, ast.ImportFrom):
            names.add((n.module or "") + ":" + ",".join(a.name for a in n.names))
        else:
            names.update(a.name for a in n.names)
    assert not any(f in x for x in names for f in
                   ("execmirror", "pmus", "executor", "submit", "funded")), names
    bal = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
               and n.name == "balances")
    called = {getattr(c.func, "id", None) or getattr(c.func, "attr", None)
              for c in ast.walk(bal) if isinstance(c, ast.Call)}
    assert "default_fee_fn" not in called and "submit_order" not in called


def test_the_module_holds_no_sql_write_and_no_network_call():
    tree = ast.parse(MODULE.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not WRITE.search(node.value), node.value[:80]
    src = MODULE.read_text()
    for word in ("httpx", "aiohttp", "urlopen", "requests.", ".post(",
                 "account_snapshot(", "submit_", "cancel_", "place_order"):
        assert word not in src, word
    # credential VALUES are never read into a response: presence only
    assert "os.environ" in src and "KALSHI_API_KEY_ID" in src
    assert re.search(r"return bool\(str\(env\.get\(KALSHI_KEY_ENV\)", src)


def test_app_registers_the_router_and_this_proof_is_capital_critical():
    app_src = (PKG / "api" / "app.py").read_text()
    assert "from .command_equity import router as _command_equity_router" \
        in app_src
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_equity_wall_authority.py" in listed.splitlines()


# ── §3 runtime ───────────────────────────────────────────────────────

@pg
async def test_every_read_runs_read_only_under_a_statement_timeout(
        monkeypatch):
    import asyncpg
    from sportsassets.api import command_equity as E

    seen = []
    real = {"paper": E.read_paper, "pm": E.read_pm, "kalshi": E.read_kalshi,
            "curve": E.paper_curve}

    def spy(name):
        async def fn(conn, **kw):
            seen.append((name,
                         await conn.fetchval("SHOW transaction_read_only"),
                         await conn.fetchval("SHOW statement_timeout")))
            return await real[name](conn, **kw)
        return fn

    for name, attr in (("paper", "read_paper"), ("pm", "read_pm"),
                       ("kalshi", "read_kalshi"), ("curve", "paper_curve")):
        monkeypatch.setattr(E, attr, spy(name))
    conn = await asyncpg.connect(H.DSN)
    try:
        assert not conn.is_in_transaction()
        got = await E.live_payload(conn)
        await E.curve_payload(conn, book="PAPER", venue=None, window="1d")
        assert not conn.is_in_transaction()
    finally:
        await conn.close()
    assert {s[0] for s in seen} == {"paper", "pm", "kalshi", "curve"}
    for name, ro, to in seen:
        assert ro == "on", name
        assert to == "%ds" % (E.STATEMENT_TIMEOUT_MS // 1000), (name, to)
    assert got["authority"] == "NONE" and got["read_only"] is True
