"""CAPITAL-CRITICAL: THE INTELLIGENCE LAYER IS SHADOW -- IT CANNOT REACH A
VENUE, AN ORDER, A LIMIT OR A PRODUCTION PROBABILITY, AND IT WRITES ONLY ITS
OWN TABLES.

  §1 STATIC. Every module in sportsassets/intel (and Audrey's recompute)
     imports only the standard library, its own package and the pure
     calibration measurement -- checked over the TRANSITIVE import closure.
     No venue, order, execution, ledger-writing or agent-execution module is
     reachable. Every SQL write in their source targets an intel_* table
     (Audrey: also paper_audrey_findings, the existing alerts mechanism).
  §2 THE DATABASE. Migration 208 stores label 'SHADOW' only, and a sizing /
     regime row can never be applied.
  §3 RUNTIME. A full cycle over synthetic rows changes no order, fill,
     ledger, settlement, decision, valuation, control, execution or funded
     table; every intel row it writes is SHADOW.
  §4 FAILURE ISOLATION. A component that raises is recorded FAILED and the
     rest of the cycle still runs.
  §5 THE READS. /api/command/intel/* are GET only, require a command
     session, and answer SHADOW-labelled envelopes; no run -> EMPTY with a
     reason, not zeros.
  §6 THE ARMING. The runner is a kill-switchable task in the API lifespan;
     these tests are on the capital-critical list.
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

from sportsassets.intel import attribution as AT
from sportsassets.intel import risk as RK
from sportsassets.intel import runner as RUN

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
INTEL = sorted((PKG / "intel").glob("*.py"))
AUDREY = PKG / "agents" / "audrey_intel_risk.py"
SHADOW_MODULES = INTEL + [AUDREY]

STDLIB = {"__future__", "asyncio", "datetime", "hashlib", "json", "logging",
          "math", "os", "random", "time", "uuid", "zoneinfo", "typing",
          "contextlib", "dataclasses", "decimal", "collections"}
ALLOWED_PROJECT = {"sportsassets.bettor_source_calibration",
                   "sportsassets.bettor_pinnacle_devig",
                   "sportsassets.agents", "sportsassets.agents."
                   "audrey_intel_risk", "sportsassets", "sportsassets.intel"}
FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution_intent", "execution_gate", "bettor_paper_ledger",
             "bettor_paper_simulator", "bettor_funded", "paper_derek",
             "paper_xavier", "paper_maker", "smalllive", "order", "live_",
             "actual_admission", "pinnapi_owner", "pinnapi_feed_runtime",
             "submit")
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE|"
                   r"ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+TABLE|COPY)\s+"
                   r"([A-Za-z_][A-Za-z0-9_]*)")


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path: pathlib.Path) -> set:
    """Absolute module names imported anywhere in the file (incl. inside
    functions), relative imports resolved."""
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
            for a in node.names:      # `from . import x` imports module x
                out.add("%s.%s" % (target, a.name))
    return out


def _path_of(name: str):
    p = ROOT / pathlib.Path(*name.split("."))
    if (p.with_suffix(".py")).exists():
        return p.with_suffix(".py")
    if (p / "__init__.py").exists():
        return p / "__init__.py"
    return None


# ── §1 static ────────────────────────────────────────────────────────

def test_the_transitive_import_closure_holds_no_venue_or_order_module():
    seen, todo = set(), [_module_name(p) for p in SHADOW_MODULES]
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
                continue                  # `from x import name` (not a module)
            assert (imp.startswith("sportsassets.intel")
                    or imp in ALLOWED_PROJECT), (name, imp)
            todo.append(imp)
    reached = {s for s in seen if s.startswith("sportsassets")}
    assert "sportsassets.bettor_source_calibration" in reached
    for name in reached:
        leaf = name.rsplit(".", 1)[-1]
        assert not any(f in leaf for f in FORBIDDEN), name


def _sql_writes(path: pathlib.Path) -> list:
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for kw, table in WRITE.findall(node.value):
                if kw.upper() == "UPDATE" and table.upper() == "SET":
                    continue                     # ON CONFLICT ... DO UPDATE
                out.append((" ".join(kw.split()).upper(), table))
    return out


def test_every_sql_write_targets_the_layers_own_tables():
    found = 0
    for path in SHADOW_MODULES:
        allowed = ("paper_audrey_findings",) if path == AUDREY else ()
        for kw, table in _sql_writes(path):
            found += 1
            assert table.startswith("intel_") or table in allowed, (
                path.name, kw, table)
            assert kw in ("INSERT INTO", "UPDATE", "DELETE FROM"), (
                path.name, kw, table)
    assert found >= 10                       # the scan sees the store's SQL
    for path in INTEL:
        if path.name not in ("store.py",):
            assert not _sql_writes(path), path.name


def test_the_read_routes_read_only_intel_tables():
    src = (PKG / "api" / "command_intel.py").read_text()
    assert not _sql_writes(PKG / "api" / "command_intel.py")
    tables = set(re.findall(r"(?<!epoch )\bFROM\s+([a-z_]+)", src))
    assert tables and all(t.startswith("intel_") for t in tables), tables


# ── §2 the database ──────────────────────────────────────────────────

@pg
async def test_the_schema_only_stores_shadow_and_never_applied():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        for sql in (
                "INSERT INTO intel_snapshots (run_id, component, computed_at,"
                " payload, version, label) VALUES ('r', 'RISK', now(), '{}',"
                " 'v', 'LIVE')",
                "INSERT INTO intel_sizing (decision_id, run_id, computed_at,"
                " factors, applied) VALUES ('d', 'r', now(), '{}', true)",
                "INSERT INTO intel_snapshots (run_id, component, book, "
                " computed_at, payload, version) VALUES ('r', 'RISK', "
                " 'PAPER_PLUS_ACTUAL', now(), '{}', 'v')",
                "INSERT INTO intel_allocations (run_id, candidate_id, "
                " computed_at, rank, candidate_kind, shadow_weight, "
                " shadow_usd, reasons) VALUES ('r', 'c', now(), 1, "
                " 'NEW_DECISION', 1.5, 10, '[]')"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(sql)
            await sp.rollback()
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_migration_208_is_idempotent_and_its_rollback_drops_only_intel():
    up = (ROOT / "migrations" / "208_shadow_investment_intelligence.sql"
          ).read_text()
    down = (ROOT / "migrations" / "rollback" /
            "208_shadow_investment_intelligence.down.sql").read_text()
    stmts = [ln for ln in down.splitlines() if ln.startswith("DROP ")]
    assert stmts and all(re.match(r"DROP (TABLE|FUNCTION) IF EXISTS intel_",
                                  ln) for ln in stmts), stmts
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(up)
        await conn.execute(up)
        await conn.execute(down)
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relname LIKE 'intel\\_%' "
            "   AND relkind = 'r'") == 0
        await conn.execute(up)
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_class WHERE relname LIKE 'intel\\_%' "
            "   AND relkind = 'r'") == 9
    finally:
        await tr.rollback()
        await conn.close()


# ── §3 runtime ───────────────────────────────────────────────────────

async def _seed(conn, now):
    acct = await H.new_account(conn, "intelrun", now=now - 86400)
    exp = F.uid("INTEL_RUN_EXP_")
    vid = await F.valuation(conn, experiment_id=exp, at=now - 4000, p=0.62,
                            outcome=1)
    d = await F.decision(conn, acct, at=now - 600, p=0.62, vwap=0.52,
                         fees_usd=1.0, qty=100, depth=500.0,
                         valuation_id=vid)
    g = await F.position(conn, acct, slug=d["slug"], qty=100, price=0.53,
                         fee=1.0, at=now - 590, decision_id=d["decision_id"],
                         event_key=d["event_key"])
    await F.book(conn, d["slug"], now - 30, bids=((0.55, 100),),
                 offers=((0.57, 100),))
    await F.equity(conn, acct, at=now - 60, equity_usd=500050.0)
    return acct, exp, d, g


@pg
async def test_a_full_cycle_writes_only_shadow_rows():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct, exp, d, g = await _seed(conn, now)
        before = await F.protected_counts(conn)
        got = await RUN.run_cycle(conn, now=now,
                                  account_id=acct["account_id"],
                                  experiment_id=exp, slug_prefix="intel-mkt-",
                                  include_actual=False)
        after = await F.protected_counts(conn)
        assert got["ran"] is True, got
        assert set(got["components"].values()) <= {"OK"}, got
        assert before == after
        rid = got["run_id"]
        rows = await conn.fetch(
            "SELECT component, status FROM intel_runs WHERE run_id=$1", rid)
        assert {r["component"] for r in rows} >= {
            "CYCLE", "CALIBRATION", "ATTRIBUTION", "RISK", "AUDREY_RISK",
            "REGIME", "SIZING", "ALLOCATOR"}
        attr = await conn.fetchrow(
            "SELECT * FROM intel_attribution WHERE subject_id=$1", g)
        assert attr["label"] == "SHADOW"
        assert attr["model_edge_usd"] == pytest.approx(10.0)
        assert attr["slippage_usd"] == pytest.approx(1.0)
        siz = await conn.fetchrow(
            "SELECT * FROM intel_sizing WHERE decision_id=$1",
            d["decision_id"])
        assert siz["applied"] is False and siz["label"] == "SHADOW"
        assert siz["paper_qty"] == 100
        alloc = await conn.fetch(
            "SELECT * FROM intel_allocations WHERE run_id=$1", rid)
        assert {a["candidate_kind"] for a in alloc} == {
            "NEW_DECISION", "OPEN_POSITION"}
        assert sum(a["shadow_usd"] for a in alloc) <= 1000.0
        assert all(a["label"] == "SHADOW" for a in alloc)
        checks = await conn.fetch(
            "SELECT agrees FROM intel_audrey_risk_checks WHERE run_id=$1",
            rid)
        assert checks and all(c["agrees"] for c in checks)
        reg = await conn.fetchrow(
            "SELECT * FROM intel_regime_states WHERE run_id=$1", rid)
        assert reg["applied"] is False
        assert reg["authority"] == "SHADOW_NO_AUTHORITY"
        snaps = await conn.fetch(
            "SELECT component, book, payload->>'label' AS label "
            "  FROM intel_snapshots WHERE run_id=$1", rid)
        assert {(s["component"], s["book"]) for s in snaps} >= {
            ("CALIBRATION", "NONE"), ("RISK", "PAPER"), ("REGIME", "NONE"),
            ("ALLOCATOR", "NONE"), ("ATTRIBUTION", "NONE"),
            ("SIZING", "NONE")}
        assert all(s["label"] == "SHADOW" for s in snaps)
    finally:
        await tr.rollback()
        await conn.close()


# ── §4 failure isolation ─────────────────────────────────────────────

@pg
async def test_a_failing_component_does_not_stop_the_cycle(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(AT, "load_paper", boom)
    monkeypatch.setattr(RK, "paper_report", boom)
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acct, exp, d, g = await _seed(conn, now)
        got = await RUN.run_cycle(conn, now=now,
                                  account_id=acct["account_id"],
                                  experiment_id=exp, include_actual=False)
        c = got["components"]
        assert c["ATTRIBUTION"] == "FAILED" and c["RISK"] == "FAILED"
        assert c["AUDREY_RISK"] == "SKIPPED"
        assert c["CALIBRATION"] == c["REGIME"] == c["SIZING"] == "OK"
        assert c["ALLOCATOR"] == "OK"
        err = await conn.fetchval(
            "SELECT error FROM intel_runs WHERE run_id=$1 AND "
            " component='ATTRIBUTION'", got["run_id"])
        assert "synthetic failure" in err
        cyc = await conn.fetchval(
            "SELECT status FROM intel_runs WHERE run_id=$1 AND "
            " component='CYCLE'", got["run_id"])
        assert cyc == "FAILED"
        siz = await conn.fetchrow(
            "SELECT unmeasured FROM intel_sizing WHERE decision_id=$1",
            d["decision_id"])
        assert "NO_RISK_REPORT_THIS_CYCLE" in siz["unmeasured"]
    finally:
        await tr.rollback()
        await conn.close()


# ── §5 the reads ─────────────────────────────────────────────────────

ROUTES = ("/api/command/intel", "/api/command/intel/allocator",
          "/api/command/intel/calibration", "/api/command/intel/attribution",
          "/api/command/intel/sizing", "/api/command/intel/risk",
          "/api/command/intel/regime")


def test_the_routes_are_get_only_and_require_a_command_session():
    from sportsassets.api import app as APP

    paths, stack = {}, list(APP.app.routes)
    while stack:                  # included routers may be wrapped
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "").startswith("/api/command/intel"):
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert set(ROUTES) <= set(paths)
    for p, methods in paths.items():
        assert methods <= {"GET", "HEAD"}, (p, methods)
    client = TestClient(APP.app, raise_server_exceptions=False)
    for route in ROUTES:
        assert client.get(route).status_code == 401, route


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pg
async def test_the_reads_serve_shadow_envelopes(monkeypatch):
    from sportsassets.api import command_intel as CI

    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        async def pool():
            return _Pool(conn)

        monkeypatch.setattr(CI, "_pool", pool)
        await conn.execute("DELETE FROM intel_snapshots")
        await conn.execute("DELETE FROM intel_runs")
        empty = await CI.intel_allocator()
        assert empty["status"] == "EMPTY" and empty["why"] == (
            "NO_SHADOW_RUN_YET")
        assert empty["data"] is None and empty["label"] == "SHADOW"
        now = time.time()
        acct, exp, d, g = await _seed(conn, now)
        await RUN.run_cycle(conn, now=now, account_id=acct["account_id"],
                            experiment_id=exp, include_actual=False)
        for fn in (CI.intel_index, CI.intel_allocator, CI.intel_calibration,
                   CI.intel_regime):
            got = await (fn(history=5) if fn is CI.intel_regime else fn())
            assert got["status"] == "OK", (fn.__name__, got.get("why"))
            assert got["label"] == "SHADOW"
            assert got["authority"] == "SHADOW_NO_AUTHORITY"
        risk = await CI.intel_risk()
        assert risk["summed_across_books"] is False
        assert risk["data"]["PAPER"]["data"]["book"] == "PAPER"
        assert risk["data"]["ACTUAL"]["status"] == "EMPTY"
        assert risk["audrey_checks"]
        attr = await CI.intel_attribution(book="PAPER", limit=10)
        assert attr["status"] == "OK" and attr["rows"][0]["label"] == "SHADOW"
        siz = await CI.intel_sizing(limit=10)
        assert siz["applied"] is False and siz["rows"]
        cal = await CI.intel_calibration()
        assert cal["production_probabilities_modified"] is False
    finally:
        await tr.rollback()
        await conn.close()


# ── §6 arming and the list ───────────────────────────────────────────

def test_the_runner_is_kill_switchable_and_armed_in_the_lifespan(
        monkeypatch):
    monkeypatch.setenv("INTEL_SHADOW", "off")
    assert RUN.enabled() is False
    monkeypatch.delenv("INTEL_SHADOW")
    assert RUN.enabled() is True
    app_src = (PKG / "api" / "app.py").read_text()
    assert "from ..intel import runner as _INTEL" in app_src
    assert "_INTEL.run(_cap_pool)" in app_src
    assert "intel_task" in app_src.split("tasks = [t for t in (")[1][:400]
    assert RUN.CYCLE_S >= 300 and RUN.STATEMENT_TIMEOUT_MS <= 30000


def test_these_proofs_are_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    for name in ("test_intel_is_shadow_only.py", "test_intel_calibration.py",
                 "test_intel_attribution.py", "test_intel_risk.py",
                 "test_intel_regime.py", "test_intel_sizing_allocator.py"):
        assert "tests/%s" % name in listed.splitlines(), name
