"""CAPITAL-CRITICAL: THE PROFITABILITY OS VIEW OBSERVES AND RECOMMENDS --
IT CANNOT REACH A VENUE, AN ORDER, A CAP, A SIZE OR A GATE, AND IT WRITES
NOTHING.

  §1 IMPORTS. The transitive import closure of sportsassets/pos_os and of
     api/command_profitability_os.py holds only the standard library, the
     package itself and named pure / SELECT-only modules; no venue, order,
     execution, ledger, paper, funded, sizing or allocator module is
     reachable.
  §2 SQL. No statement in the package or the route writes (INSERT, UPDATE,
     DELETE, TRUNCATE, ALTER, DROP, CREATE, COPY, GRANT); every statement
     reads.py issues is a SELECT; the route's only SET is SET LOCAL
     statement_timeout inside a READ ONLY transaction.
  §3 NOTHING CONSUMES IT. No production module imports pos_os except the
     route; app.py registers the route.
  §4 BOOKKEEPING. Every R_ code the package defines is classified in the
     refusal taxonomy; these tests are on the capital-critical list.
"""
from __future__ import annotations

import ast
import pathlib
import re
import warnings

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
OS_FILES = sorted((PKG / "pos_os").glob("*.py"))
API = PKG / "api" / "command_profitability_os.py"
STDLIB = {"__future__", "asyncio", "datetime", "hashlib", "json", "logging",
          "math", "os", "random", "time", "uuid", "zoneinfo", "typing",
          "contextlib", "dataclasses", "decimal", "collections", "copy",
          "statistics", "re", "socket", "functools", "pathlib"}
#: the named project modules the view may reach, each pure or SELECT-only
ALLOWED_PROJECT = {"sportsassets.simulated_account_context",
    "sportsassets", "sportsassets.api",
    "sportsassets.profitability", "sportsassets.profitability.common",
    "sportsassets.intel", "sportsassets.intel.common",
    "sportsassets.intel.attribution",
    # SELECT-only, pinned by tests/test_intel_is_shadow_only.py (reached
    # through intel.attribution's loader, never called from here)
    "sportsassets.intel.reads",
    "sportsassets.refusal_taxonomy", "sportsassets.refusal_taxonomy_table",
    # the loop-health READER (its SELECTs through _try savepoints); its
    # recorder is never called from here. db/config come with it.
    "sportsassets.loop_health", "sportsassets.db", "sportsassets.config",
}
FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution_intent", "execution_gate", "paper", "bettor",
             "funded", "derek", "xavier", "karen", "maker", "smalllive",
             "order", "live_", "admission", "pinnapi", "submit", "sizing",
             "allocator", "runtime", "ledger", "simulator", "risk_engine")
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM|"
                   r"TRUNCATE|ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+TABLE|"
                   r"CREATE\s+TEMP|COPY\s+[a-z_]+|GRANT\s)", re.I)


def _module_name(path):
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path):
    mod = _module_name(path)
    pkg = mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]
    out = set()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
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


def _strings(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def _sql(path):
    """String constants that look like SQL statements."""
    return [s for s in _strings(path)
            if re.search(r"\b(SELECT|FROM|INSERT|UPDATE|DELETE|SET)\b", s)
            and not s.startswith(("THE ", "\"\"\""))]


# ── §1 imports ───────────────────────────────────────────────────────

def test_no_venue_order_paper_or_execution_module_is_reachable():
    seen, todo = set(), [_module_name(p) for p in OS_FILES + [API]]
    api = _module_name(API)
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
            if top in ("fastapi", "asyncpg", "pydantic", "pydantic_settings"
                       ) and name in (api, "sportsassets.db",
                                      "sportsassets.config"):
                continue
            if top != "sportsassets":
                assert top in STDLIB, (name, imp)
                continue
            if _path_of(imp) is None:
                continue
            if name == api and imp == "sportsassets.api.agents_core":
                continue      # require_read / _pool: the shared auth seam
            assert imp.startswith("sportsassets.pos_os") or \
                imp in ALLOWED_PROJECT, (name, imp)
            todo.append(imp)
    reached = {s for s in seen if s.startswith("sportsassets")}
    assert "sportsassets.pos_os.reads" in reached
    assert "sportsassets.intel.attribution" in reached
    for name in reached:
        leaf = name.rsplit(".", 1)[-1]
        assert not any(f in leaf for f in FORBIDDEN), name


# ── §2 SQL ───────────────────────────────────────────────────────────

def test_no_statement_in_the_view_writes_anything():
    for path in OS_FILES + [API]:
        for s in _strings(path):
            assert not WRITE.search(s), (path.name, s[:120])


def test_every_statement_the_loaders_issue_is_a_select():
    stmts = [s for s in _sql(PKG / "pos_os" / "reads.py")
             if s.lstrip().upper().startswith(("SELECT", "WITH", "UPDATE",
                                               "INSERT", "DELETE"))]
    assert len(stmts) >= 15
    for s in stmts:
        assert s.lstrip().upper().startswith("SELECT"), s[:120]
    others = [p for p in OS_FILES if p.name != "reads.py"]
    for p in others:
        for s in _strings(p):
            assert not re.match(r"\s*(SELECT|WITH)\s", s), (p.name, s[:80])


def test_the_route_reads_inside_a_bounded_read_only_transaction():
    src = API.read_text()
    assert "transaction(readonly=True)" in src
    sets = re.findall(r"SET\s+[A-Za-z_ ]+", " ".join(_strings(API)))
    assert sets and all(s.startswith("SET LOCAL statement_timeout")
                        for s in sets), sets
    from sportsassets.api import command_profitability_os as OS
    assert 0 < OS.STATEMENT_TIMEOUT_MS <= 10000
    assert "require_read" in src and "@router.get(" in src
    assert not re.search(r"@router\.(post|put|patch|delete)", src)


# ── §3 nothing consumes it ───────────────────────────────────────────

def test_no_production_module_consumes_the_view():
    offenders = []
    for p in PKG.rglob("*.py"):
        rel = p.relative_to(PKG)
        if rel.parts[0] == "pos_os" or str(rel) == \
                "api/command_profitability_os.py":
            continue
        for imp in _imports(p):
            if "pos_os" in imp.split("."):
                offenders.append((str(rel), imp))
    assert not offenders, offenders
    app = (PKG / "api" / "app.py").read_text()
    assert "from .command_profitability_os import" in app
    assert "app.include_router(_command_profitability_os_router)" in app


def test_recommendations_are_never_applied():
    from sportsassets.pos_os import capital as CAP
    from sportsassets.pos_os import champion as CH
    assert CAP.optimizer([])["applied"] is False
    assert CAP.optimizer([])["authority"] == "RECOMMENDATION_ONLY"
    assert CH.strategy_tournament([])["promotes"] is False


# ── §4 bookkeeping ───────────────────────────────────────────────────

def test_every_r_code_of_the_view_is_classified():
    from sportsassets import refusal_taxonomy_table as TT
    from sportsassets.pos_os import common as C
    codes = {v for k, v in vars(C).items() if k.startswith("R_")}
    assert len(codes) == 6
    for c in codes:
        assert c in TT.TABLE, c
        assert TT.TABLE[c][0] == "SOFTWARE", c


def test_these_tests_are_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    for f in ("tests/test_pos_os_authority.py",
              "tests/test_pos_os_components.py", "tests/test_pos_os_db.py"):
        assert f in listed.splitlines(), f
