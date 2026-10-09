"""THE POSITION ROOMS HAVE NO AUTHORITY: GET only, a command session or 401,
read-only SQL inside a READ ONLY transaction, and no order, venue-submit,
funded or paper-writer module anywhere in their import graph.

Pattern: tests/test_order_route_census.py (derived from the source each run)
and tests/test_paper_records_cannot_reach_the_funded_path.py. Neither module
imports a paper module, so neither needs registering there; the walk below
fails if one ever does.
"""
from __future__ import annotations

import ast
import pathlib
import re

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
MODULES = ("position_rooms.py", "api/command_positions.py")
ROUTES = ("/api/command/positions/rooms",
          "/api/command/positions/room/PAPER:EVT:mlb-bos-nyy-2026-10-04")
#: Any module whose name contains one of these may place, cancel, submit,
#: fund or write paper records. None may be reached.
FORBIDDEN = ("execmirror", "live_executor", "pmus", "kalshi_orders",
             "kalshi_venue", "kalshi_account", "funded", "entry_execution",
             "execution_gate", "execution_intent", "venue_sdk", "clob",
             "submission", "paper", "calibration_execute", "capital_path",
             "actual_admission", "copy_sports", "positions_sync")


def _imports(path: pathlib.Path) -> set:
    """sportsassets-relative module paths imported anywhere in the file
    (top level AND inside functions)."""
    tree = ast.parse(path.read_text())
    pkg = path.parent.relative_to(ROOT.parent).parts   # ('sportsassets',..)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(pkg[:len(pkg) - node.level + 1])
                mod = base + (node.module.split(".") if node.module else [])
                if node.module:
                    out.add(".".join(mod))
                for a in node.names:
                    out.add(".".join(mod + [a.name]))
            elif (node.module or "").startswith("sportsassets"):
                out.add(node.module)
                for a in node.names:
                    out.add(node.module + "." + a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("sportsassets"):
                    out.add(a.name)
    return out


def _file_of(mod: str) -> pathlib.Path | None:
    rel = mod.split(".")[1:]
    if not rel:
        return None
    p = ROOT.joinpath(*rel).with_suffix(".py")
    if p.exists():
        return p
    p = ROOT.joinpath(*rel, "__init__.py")
    return p if p.exists() else None


def _closure() -> dict:
    seen: dict = {}
    todo = [ROOT / "position_rooms.py"]
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen[p] = _imports(p)
        for m in seen[p]:
            f = _file_of(m)
            if f is not None and f not in seen:
                todo.append(f)
    return seen


def test_the_import_graph_reaches_no_order_or_paper_writer_module():
    reached = {str(p.relative_to(ROOT)) for p in _closure()}
    bad = sorted(r for r in reached
                 if any(k in r for k in FORBIDDEN))
    assert not bad, bad
    # the module is the read model, and the closure is small and explicit
    assert reached == {
        "position_rooms.py", "bettor_book_snapshot.py",
        # Read-only durable simulated account selector; no writer imports.
        "simulated_account_context.py",
        "bettor_venue_native_identity.py", "bettor_venue_mapping.py",
        "bettor_venue_realism.py", "bettor_sport_mapping.py",
        "market_labels.py", "team_logos.py",
        # the shared PURE order-state mapping (no imports, no I/O):
        # tests/test_resting_is_not_protection.py pins it import-free
        "order_state_truth.py",
        # the read-time freshness truth of Xavier's recommendation (owner
        # P0, 2026-10-04): pure, stdlib only, imports no sportsassets module
        "xavier_freshness.py",
        # (R30C) the evidence-class label shown beside every Archer fill
        # probability in a room (the paper simulator's rate is never shown
        # as live execution quality): pure, stdlib only, imports no
        # sportsassets module (tests/test_execution_calibration.py pins it)
        "execution_evidence.py",
        # (P0 closeout) the canonical open-position rule: constants + SQL
        # text, stdlib only (tests/test_agent_work_state_authority.py pins
        # it import-free)
        "open_position_canon.py"}, sorted(reached)


def test_the_route_module_imports_only_auth_db_and_the_read_model():
    got = {m for m in _imports(ROOT / "api/command_positions.py")
           if m.count(".") >= 1}
    # require_read is the SAME session check every /api/command read uses
    assert got <= {"sportsassets.api.agents_core",
                   "sportsassets.api.agents_core.require_read",
                   "sportsassets.db", "sportsassets.db.get_pool",
                   "sportsassets.position_rooms"}, sorted(got)
    assert "sportsassets.position_rooms" in got


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


def test_the_route_runs_read_only_with_a_statement_timeout():
    src = (ROOT / "api/command_positions.py").read_text()
    assert "conn.transaction(readonly=True)" in src
    assert "SET LOCAL statement_timeout" in src
    from sportsassets import position_rooms as PR
    assert 0 < PR.STATEMENT_TIMEOUT_MS <= 30000


def test_get_only_and_401_without_a_session():
    from sportsassets.api import app as APP
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "").startswith("/api/command/positions"):
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert set(paths) == {"/api/command/positions/rooms",
                          "/api/command/positions/room/{group_key}"}
    for p, methods in paths.items():
        assert methods <= {"GET", "HEAD"}, (p, methods)
    client = TestClient(APP.app, raise_server_exceptions=False)
    for route in ROUTES:
        assert client.get(route).status_code == 401, route
        assert client.post(route).status_code in (401, 405), route
