"""CAPITAL-CRITICAL: THE LAB-F DRIFT SENTINEL IS SHADOW RESEARCH -- IT CANNOT
REACH A VENUE, AN ORDER, A SIZE, A LIMIT, A THRESHOLD, A POLICY OR CAPITAL,
AND NO DECISION PATH CAN REACH IT.

  §1 BOTH DIRECTIONS, STATIC. Every module in sportsassets/lab imports only the
     standard library and its own package (transitive closure); the read
     route imports only FastAPI, the standard library, the command auth /
     pool helper and the lab package. NO module outside the lab and its
     route imports the lab -- except api/app.py's one router registration --
     so nothing on a decision, order, execution, risk or policy path can read
     a drift status or a confidence modifier.
  §2 WRITES. The only SQL writes in the lab are INSERTs into migration 244's
     lab_drift_* tables, from drift_store.py; the reader, the sentinel, the
     runner's read path and the route write nothing. The route runs in a
     READ ONLY transaction under a statement timeout.
  §3 THE ROUTE is GET only and requires a command session.
  §4 THE SCHEMA says SHADOW_RESEARCH_ONLY / production_effect NONE on every
     table, refuses UPDATE / DELETE / TRUNCATE, and a work item can never be
     recorded as enqueued.
  §5 These proofs are on the capital-critical list.
"""
from __future__ import annotations

import ast
import pathlib
import re
import warnings

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
LAB = sorted((PKG / "lab").glob("*.py"))
ROUTE = PKG / "api" / "command_lab_drift.py"
APP = PKG / "api" / "app.py"
MIG = ROOT / "migrations" / "244_lab_drift_sentinel.sql"
LAB_TABLES = ("lab_drift_runs", "lab_drift_findings", "lab_drift_tasks")
STDLIB = {"__future__", "argparse", "asyncio", "bisect", "datetime",
          "hashlib", "json", "math", "os", "random", "sys", "time",
          "typing"}
#: imported lazily inside the CLI's live path only (the DB driver)
THIRD_PARTY_ALLOWED = {"asyncpg"}
FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution", "bettor_paper", "bettor_funded", "paper_",
             "smalllive", "small_live", "order", "live_", "actual_admission",
             "pinnapi", "submit", "sizing", "allocator", "runtime", "desk",
             "decision_hooks", "canonical", "allie", "risk", "funded")
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE|"
                   r"ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+TABLE|COPY)\s+"
                   r"([A-Za-z_][A-Za-z0-9_]*)")


def _module_name(path: pathlib.Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _parse(path: pathlib.Path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ast.parse(path.read_text())


def _imports(path: pathlib.Path) -> set:
    mod = _module_name(path)
    pkg = mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]
    out = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg.split(".")
                base = base[:len(base) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module
                                          else []))
                out.add(target)
                for a in node.names:
                    out.add(target + "." + a.name)
            else:
                out.add(node.module)
                for a in node.names:
                    out.add("%s.%s" % (node.module, a.name))
    return out


def _path_of(name: str):
    p = ROOT / pathlib.Path(*name.split("."))
    if p.with_suffix(".py").exists():
        return p.with_suffix(".py")
    if (p / "__init__.py").exists():
        return p / "__init__.py"
    return None


def _sql_writes(path: pathlib.Path) -> list:
    out = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for kw, table in WRITE.findall(node.value):
                if kw.upper() == "UPDATE" and table.upper() == "SET":
                    continue
                out.append((" ".join(kw.split()).upper(), table))
    return out


# ── §1 both directions ───────────────────────────────────────────────

def test_the_lab_imports_only_the_standard_library_and_itself():
    seen, todo = set(), [_module_name(p) for p in LAB]
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
                assert top in STDLIB or top in THIRD_PARTY_ALLOWED, (name,
                                                                     imp)
                continue
            if _path_of(imp) is None:
                continue                      # a name imported from a module
            assert imp == "sportsassets" or imp.startswith(
                "sportsassets.lab"), (name, imp)
            todo.append(imp)
    for name in seen:
        if name.startswith("sportsassets"):
            leaf = name.rsplit(".", 1)[-1]
            assert not any(f in leaf for f in FORBIDDEN), name


def test_the_route_imports_nothing_with_authority():
    for imp in _imports(ROUTE):
        top = imp.split(".")[0]
        if top != "sportsassets":
            assert top in STDLIB | {"fastapi"}, imp
            continue
        if _path_of(imp) is None:
            continue
        assert imp in ("sportsassets.api.agents_core",) or imp.startswith(
            "sportsassets.lab"), imp


def test_no_decision_order_or_policy_path_imports_the_lab():
    allowed = {p.resolve() for p in LAB} | {ROUTE.resolve()}
    offenders = []
    for path in PKG.rglob("*.py"):
        if path.resolve() in allowed:
            continue
        try:
            imps = _imports(path)
        except SyntaxError:                                     # pragma: no cover
            continue
        for imp in imps:
            if imp.startswith("sportsassets.lab") or \
                    imp.startswith("sportsassets.api.command_lab_drift"):
                offenders.append((str(path.relative_to(ROOT)), imp))
    # the ONE importer: the API's router registration
    assert {o[0] for o in offenders} == {"sportsassets/api/app.py"}, offenders
    lines = [ln.strip() for ln in APP.read_text().splitlines()
             if "command_lab_drift" in ln]
    assert lines == [
        "from .command_lab_drift import router as _command_lab_drift_router",
        "app.include_router(_command_lab_drift_router)",
        'log.warning("lab drift: api.command_lab_drift not loaded", '
        'exc_info=True)']


# ── §2 writes ────────────────────────────────────────────────────────

def test_the_only_writes_are_inserts_into_the_lab_tables_from_the_store():
    found = 0
    for path in LAB:
        for kw, table in _sql_writes(path):
            found += 1
            assert path.name == "drift_store.py", (path.name, kw, table)
            assert kw == "INSERT INTO", (path.name, kw, table)
            assert table in LAB_TABLES, (path.name, kw, table)
    assert found == 4                               # run, finding, 2 tasks
    assert not _sql_writes(ROUTE)


def test_the_readers_are_select_only_and_the_route_read_only():
    from sportsassets.lab import drift_reads as R
    sqls = [v for k, v in vars(R).items() if k.startswith("Q_")
            and isinstance(v, str)] + list(R.Q_ACTUAL_FILLS.values())
    assert len(sqls) >= 10
    for q in sqls:
        assert re.match(r"\s*(SELECT|WITH)\b", q), q[:60]
        assert not WRITE.search(q), q[:60]
    src = ROUTE.read_text()
    assert "transaction(readonly=not nested)" in src
    assert "SET LOCAL statement_timeout" in src
    assert "tr.rollback()" in src


# ── §3 the route ─────────────────────────────────────────────────────

def test_the_route_is_get_only_and_requires_a_command_session():
    from sportsassets.api import app as A
    from sportsassets.api import command_lab_drift as CLD
    paths, stack = {}, list(A.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") == CLD.PATH:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert set(paths) == {"/api/command/lab/drift"}
    assert paths[CLD.PATH] <= {"GET", "HEAD"}
    client = TestClient(A.app, raise_server_exceptions=False)
    assert client.get(CLD.PATH).status_code == 401
    assert client.post(CLD.PATH).status_code in (401, 404, 405)


# ── §4 the schema ────────────────────────────────────────────────────

def test_every_lab_table_is_shadow_append_only_and_never_enqueues():
    raw = MIG.read_text()
    sql = re.sub(r"--[^\n]*", "", raw)
    for t in LAB_TABLES:
        assert "CREATE TABLE IF NOT EXISTS %s" % t in sql
        assert "'%s'" % t in sql                     # in the trigger loop
    assert sql.count("authority = 'SHADOW_RESEARCH_ONLY'") == 3
    assert sql.count("production_effect = 'NONE'") == 2
    assert "BEFORE UPDATE OR DELETE ON %I" in sql
    assert "BEFORE TRUNCATE ON %I" in sql
    assert "'NOT_ENQUEUED_RETURNED_FOR_LATER_INTEGRATION'" in sql
    assert "coalesce(agent_id, '') IN ('AUDREY', 'SCOUT')" in sql
    assert "agent_work_requests" not in sql
    down = (ROOT / "migrations" / "rollback" /
            "244_lab_drift_sentinel.down.sql").read_text()
    for t in LAB_TABLES:
        assert "DROP TABLE IF EXISTS %s;" % t in down
    assert "rollback refused" in down
    dropped = set(re.findall(r"DROP TABLE IF EXISTS ([a-z_]+)", down))
    assert dropped == set(LAB_TABLES)


# ── §5 the list ──────────────────────────────────────────────────────

def test_these_proofs_are_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    for name in ("test_lab_is_shadow_only.py", "test_lab_drift_reads_db.py",
                 "test_lab_drift_sentinel.py"):
        assert "tests/%s" % name in listed.splitlines(), name
