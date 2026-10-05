"""R30C RISK EVIDENCE HAS NO AUTHORITY: GET only, a command session or 401,
read-only SQL inside a READ ONLY transaction, and no order, venue-submit,
funded or paper-writer module anywhere in the import graph of the measured
settlement-exception table, the correlation graph or their route module.

Pattern: tests/test_position_rooms_authority.py (the walk is derived from the
source each run). Neither module imports a paper module, so neither needs
registering in tests/test_paper_records_cannot_reach_the_funded_path.py; the
walk below fails if one ever does.
"""
from __future__ import annotations

import ast
import pathlib
import re

from fastapi.testclient import TestClient

from tests.test_position_rooms_authority import FORBIDDEN, _file_of, _imports

ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
MODULES = ("settlement_exception_risk.py", "correlation_graph.py",
           "api/command_risk_evidence.py")
ROUTES = ("/api/command/settlement-exception-risk",
          "/api/command/correlation-graph")


def _closure(start: str) -> set:
    seen: dict = {}
    todo = [ROOT / start]
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen[p] = _imports(p)
        for m in seen[p]:
            f = _file_of(m)
            if f is not None and f not in seen:
                todo.append(f)
    return {str(p.relative_to(ROOT)) for p in seen}


def test_the_import_graph_reaches_no_order_or_paper_writer_module():
    for m in MODULES[:2]:
        reached = _closure(m)
        bad = sorted(r for r in reached if any(k in r for k in FORBIDDEN))
        assert not bad, (m, bad)
    # the measured table is pure but for its read: it reaches only the
    # settlement readers (R30A's NFL clause reader is one of them:
    # bettor_settlement_terms reads the NFL tie clause through it; it
    # imports no paper, live, funded or execution module)
    assert _closure("settlement_exception_risk.py") == {
        "settlement_exception_risk.py", "bettor_settlement_clauses.py",
        "bettor_settlement_terms.py", "bettor_venue_settlement.py",
        "bettor_nfl_settlement.py"}, \
        sorted(_closure("settlement_exception_risk.py"))


def test_the_route_module_imports_only_auth_db_and_the_read_models():
    got = {m for m in _imports(ROOT / "api/command_risk_evidence.py")
           if m.count(".") >= 1}
    # require_read is the SAME session check every /api/command read uses
    assert got <= {"sportsassets.api.agents_core",
                   "sportsassets.api.agents_core.require_read",
                   "sportsassets.db", "sportsassets.db.get_pool",
                   "sportsassets.settlement_exception_risk",
                   "sportsassets.correlation_graph"}, sorted(got)


def test_the_canonical_component_reaches_no_execution_module():
    """canonical_components is imported by the EXECUTING process's decision
    hook; the new component must not widen it toward an order path."""
    reached = _closure("settlement_exception_risk.py")
    for k in ("execmirror", "live_parity", "execution", "funded", "paper"):
        assert not any(k in r for r in reached), (k, sorted(reached))


def test_no_mutating_statement_in_the_sql():
    verbs = re.compile(r"\b(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE|"
                       r"GRANT|REVOKE|COPY|LOCK|NOTIFY|VACUUM|MERGE)\b|"
                       r"\bFOR\s+UPDATE\b|\bnextval\b")
    for m in MODULES:
        tree = ast.parse((ROOT / m).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and re.search(r"\b(SELECT|FROM)\b", node.value):
                assert not verbs.search(node.value), (m, node.value[:120])


def test_the_routes_run_read_only_with_a_statement_timeout():
    src = (ROOT / "api/command_risk_evidence.py").read_text()
    assert "conn.transaction(readonly=True)" in src
    assert "SET LOCAL statement_timeout" in src
    from sportsassets.api import command_risk_evidence as API
    assert 0 < API.STATEMENT_TIMEOUT_MS <= 30000


def test_get_only_and_401_without_a_session():
    from sportsassets.api import app as APP
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") in ROUTES:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert set(paths) == set(ROUTES)
    for p, methods in paths.items():
        assert methods <= {"GET", "HEAD"}, (p, methods)
    client = TestClient(APP.app, raise_server_exceptions=False)
    for route in ROUTES:
        assert client.get(route).status_code == 401, route
        assert client.post(route).status_code in (401, 405), route
