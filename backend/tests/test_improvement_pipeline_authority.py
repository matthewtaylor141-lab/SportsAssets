"""CAPITAL-CRITICAL: THE IMPROVEMENT PIPELINE HAS NO ORDER, DEPLOY OR MERGE
AUTHORITY (migration 221).

  §1 THE RUNNER (agents/improvement_pipeline.py) imports only the standard
     library, its pure rule module and (lazily) the service heartbeat -- no
     order, venue, execution, ledger, paper, funded or Slack transport
     module, no subprocess / socket / HTTP / git. Its SQL writes only the
     improve_* tables. It never names a HUMAN or ENGINEERING actor class, a
     CONTROLLED_RELEASE / FORWARD_RESULT / ROLLED_BACK stage, or a patch /
     gate / release / rollback column: those are a person's, and the
     database refuses them in the runner's session anyway.
  §2 THE RULES (agents/improvement_stages.py) are pure: no I/O import.
  §3 NO OTHER CODE PATH writes the ledger: across sportsassets/, only the
     runner inserts into improve_* (the Slack bridge only reads it), and no
     module anywhere inserts a CONTROLLED_RELEASE.
  §4 THE READ API (api/command_improvements.py) imports only stdlib,
     FastAPI, the command read dependency and the pure rules; its SQL is
     SELECT only; its two routes are GET only and answer 401 without a
     session; at runtime the request path is `BEGIN READ ONLY` with a bounded
     statement timeout (a write inside it is refused).
  §5 THE SLACK DIGEST (slack_bridge.publish_improvement_posts) only queues
     READY rows in agent_slack_delivery through the per-agent path: Karen /
     Eddie / Scout content carries their dedicated source-key prefix and is
     queued only when their own identity is configured.
  §6 LISTED on the capital-critical list.
"""
from __future__ import annotations

import ast
import inspect
import os
import pathlib
import re
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
RUNNER = PKG / "agents" / "improvement_pipeline.py"
RULES = PKG / "agents" / "improvement_stages.py"
API = PKG / "api" / "command_improvements.py"
DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution", "ledger", "simulator", "bettor_funded", "funded",
             "paper_", "smalllive", "order", "submit", "live_",
             "actual_admission", "pinnapi", "slack_bridge", "subprocess",
             "socket", "httpx", "requests", "urllib", "http.client", "git",
             "shutil", "pty", "multiprocessing", "paramiko", "asyncssh")
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM|"
                   r"TRUNCATE|ALTER\s+|DROP\s+|CREATE\s+|COPY\s+|GRANT\s+|"
                   r"SET\s+ROLE|SET\s+SESSION)", re.I)
WRITE_TARGET = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"
                          r"([a-z_]+)", re.I)


def _imports(path: pathlib.Path, package: str) -> set:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[:len(base) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module
            out.add(mod)
            for a in node.names:
                out.add("%s.%s" % (mod, a.name))
    return out


def _strings(path: pathlib.Path) -> list:
    tree = ast.parse(path.read_text())
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and \
                    isinstance(getattr(node.body[0], "value", None),
                               ast.Constant):
                doc_nodes.add(id(node.body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in doc_nodes]


def _sql(path) -> list:
    return [s for s in _strings(path)
            if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|FROM)\b", s)]


def _code(path) -> str:
    """The source without docstrings and comments."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and \
                    isinstance(getattr(node.body[0], "value", None),
                               ast.Constant):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


# ── §1 the runner ────────────────────────────────────────────────────

RUNNER_STDLIB = {"__future__", "asyncio", "json", "logging", "os", "time",
                 "uuid", "contextlib", "datetime"}


def test_the_runner_imports_no_order_deploy_network_or_process_module():
    imps = _imports(RUNNER, "sportsassets.agents")
    for imp in imps:
        top = imp.split(".")[0]
        if top == "sportsassets":
            assert imp in {"sportsassets.agents",
                           "sportsassets.agents.improvement_stages",
                           "sportsassets", "sportsassets.db"}, imp
        else:
            assert top in RUNNER_STDLIB, imp
        assert not any(f in imp.lower() for f in FORBIDDEN), imp
    code = _code(RUNNER)
    for bad in ("os.system", "os.popen", "os.exec", "os.spawn", "subprocess",
                "create_subprocess", "eval(", "exec(", "__import__",
                "importlib", "open(", "git ", "git push", "gh ", "merge(",
                "deploy(", "push("):
        assert bad not in code, bad


def test_the_runner_writes_only_the_improvement_ledger():
    sqls = _sql(RUNNER)
    assert len(sqls) >= 15
    targets = set()
    for s in sqls:
        for t in WRITE_TARGET.findall(s):
            targets.add(t.lower())
        assert not re.search(r"\b(TRUNCATE|ALTER|DROP|CREATE|GRANT|COPY)\b",
                             s, re.I), s[:120]
    assert targets == {"improve_items", "improve_events",
                       "improve_disagreements", "improve_runs"}, targets
    for s in sqls:
        assert "patch_" not in s and "gate_receipt" not in s and \
            "release_ref" not in s and "rollback_ref" not in s, s[:120]


def test_the_runner_never_names_a_human_step():
    tree = ast.parse(RUNNER.read_text())
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for forbidden in ("HUMAN", "ENGINEERING", "CONTROLLED_RELEASE",
                      "FORWARD_RESULT", "ROLLED_BACK"):
        assert forbidden not in names, forbidden
    lits = set(_strings(RUNNER))
    for forbidden in ("HUMAN", "ENGINEERING", "CONTROLLED_RELEASE",
                      "FORWARD_RESULT", "ROLLED_BACK"):
        assert forbidden not in lits, forbidden
    from sportsassets.agents import improvement_stages as S
    assert set(S.RUNNER_STAGES).isdisjoint({S.CONTROLLED_RELEASE,
                                            S.FORWARD_RESULT, S.ROLLED_BACK})
    assert S.PERMITTED[S.CONTROLLED_RELEASE] == (S.HUMAN,)
    # the pure guard refuses the runner a human row by name
    item = {"owner_agent": "DEREK", "stage": S.ELIGIBLE_CHANGE,
            "required_independent_reviews": 1}
    assert S.check_event(item, [], {
        "stage": S.CONTROLLED_RELEASE, "actor": "Matt Taylor",
        "actor_class": S.HUMAN}, runner=True) == S.R_RUNNER_HUMAN
    # and the runner declares itself to the database on every write
    src = inspect.getsource(__import__(
        "sportsassets.agents.improvement_pipeline",
        fromlist=["x"]).runner_tx)
    assert "bettor.improvement_runner" in src and "'on', true" in src


def test_the_runner_has_a_kill_switch_and_bounds(monkeypatch):
    from sportsassets.agents import improvement_pipeline as P
    assert P.enabled()
    for off in ("0", "false", "off", "no"):
        monkeypatch.setenv(P.ENV_KILL, off)
        assert not P.enabled()
    assert 0 < P.MAX_NEW_ITEMS_PER_PASS <= 50
    assert 0 < P.SOURCE_LIMIT <= 200 and 0 < P.PASS_TIMEOUT_S <= 300
    assert 0 < P.STATEMENT_TIMEOUT_MS <= 20000


# ── §2 the rules ─────────────────────────────────────────────────────

def test_the_rules_module_is_pure():
    imps = _imports(RULES, "sportsassets.agents")
    assert {i.split(".")[0] for i in imps} <= {"__future__", "hashlib",
                                               "json", "re"}, imps
    assert not _sql(RULES)


# ── §3 no other writer ──────────────────────────────────────────────

def test_only_the_runner_writes_the_ledger_and_nobody_releases():
    writers = set()
    for p in PKG.rglob("*.py"):
        txt = p.read_text(errors="ignore")
        if re.search(r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+improve_",
                     txt, re.I):
            writers.add(p.relative_to(PKG).as_posix())
        assert not re.search(r"INSERT\s+INTO\s+improve_events[^;]*"
                             r"CONTROLLED_RELEASE", txt, re.I | re.S), p
    assert writers == {"agents/improvement_pipeline.py"}, writers


# ── §4 the read API ─────────────────────────────────────────────────

def test_the_api_imports_only_the_read_dependency_and_pure_rules():
    imps = _imports(API, "sportsassets.api")
    allowed = {"sportsassets.api.agents_core",
               "sportsassets.api.agents_core._pool",
               "sportsassets.api.agents_core.require_read",
               "sportsassets.agents",
               "sportsassets.agents.improvement_stages"}
    for imp in imps:
        top = imp.split(".")[0]
        if top == "sportsassets":
            assert imp in allowed, imp
        else:
            assert top in {"__future__", "json", "re", "time", "datetime",
                           "fastapi"}, imp
        assert not any(f in imp.lower() for f in FORBIDDEN), imp
    assert "sportsassets.api.agents_core.require_read" in imps


def test_the_api_sql_is_select_only():
    sqls = _sql(API)
    assert len(sqls) >= 6
    for s in sqls:
        assert not WRITE.search(s), s[:120]
    src = API.read_text()
    sets = re.findall(r"\bSET\s+[A-Z_]+[^\"']*", src)
    assert sets and all(x.startswith("SET LOCAL statement_timeout")
                        for x in sets), sets
    assert "transaction(readonly=True)" in src
    from sportsassets.api import command_improvements as CI
    assert 0 < CI.STATEMENT_TIMEOUT_MS <= 10000


def test_the_routes_are_get_only_and_require_a_command_session():
    from sportsassets.api import app as APP

    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "").startswith("/api/command/improvements"):
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert set(paths) == {"/api/command/improvements",
                          "/api/command/improvements/{item_id}"}
    for p, methods in paths.items():
        assert methods <= {"GET", "HEAD"}, (p, methods)
    client = TestClient(APP.app, raise_server_exceptions=False)
    for route in ("/api/command/improvements",
                  "/api/command/improvements/impr:" + "0" * 24):
        assert client.get(route).status_code == 401, route
        for verb in (client.post, client.put, client.delete, client.patch):
            assert verb(route).status_code in (401, 405), route


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pg
async def test_the_request_path_is_a_read_only_transaction(monkeypatch):
    import asyncpg

    from sportsassets.api import command_improvements as CI

    conn = await asyncpg.connect(DSN)
    try:
        async def pool():
            return _Pool(conn)

        monkeypatch.setattr(CI, "_pool", pool)

        async def write(c):
            assert await c.fetchval("SHOW transaction_read_only") == "on"
            assert await c.fetchval(
                "SELECT setting::int FROM pg_settings "
                " WHERE name='statement_timeout'") == CI.STATEMENT_TIMEOUT_MS
            await c.execute("INSERT INTO improve_runs (run_id, started_at, "
                            " finished_at, status, version) VALUES "
                            " ('x', now(), now(), 'OK', 'x')")

        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await CI._read_only(write)

        class R:
            headers: dict = {}
        board = await CI.improvements_index(R(), stage=None, owner=None,
                                            limit=50)
        assert board["read_only"] is True and board["available"] is True
        assert [s["stage"] for s in board["stages"]][:9] == [
            "EVIDENCE", "HYPOTHESIS", "PEER_CHALLENGE", "OWNER_RESPONSE",
            "EXPERIMENT", "INDEPENDENT_EVALUATION", "ELIGIBLE_CHANGE",
            "CONTROLLED_RELEASE", "FORWARD_RESULT"]
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as e:
            await CI.improvements_item("impr:" + "0" * 24, R())
        assert e.value.status_code == 404
        with pytest.raises(HTTPException) as e:
            await CI.improvements_item("../etc", R())
        assert e.value.status_code == 404
        with pytest.raises(HTTPException) as e:
            await CI.improvements_index(R(), stage="DEPLOYED", owner=None,
                                        limit=5)
        assert e.value.status_code == 400
    finally:
        await conn.close()


# ── §5 the Slack digest ─────────────────────────────────────────────

def test_the_slack_digest_uses_the_per_agent_path_only():
    from sportsassets import slack_bridge as B
    src = inspect.getsource(B.publish_improvement_posts)
    writes = WRITE_TARGET.findall(src)
    assert set(w.lower() for w in writes) == {"agent_slack_delivery"}, writes
    assert "'READY'" in src and "dedicated_identity(agent)['ok']" in src
    assert "DEDICATED[agent]+'improve:'" in src
    assert "is_transition" in src
    assert "httpx" not in src and "chat.postMessage" not in src
    assert B.IMPROVE_POSTS_PER_PASS <= 3 and B.IMPROVE_POSTS_PER_HOUR <= 20
    claim = inspect.getsource(B.claim)
    assert "publish_improvement_posts(conn)" in claim
    from sportsassets.agents import improvement_stages as S
    for who, cls, agent in (("KAREN", S.CHALLENGER, "karen"),
                            ("EDDIE", S.OWNER_AGENT, "eddie"),
                            ("IMPROVEMENT_PIPELINE", S.RUNNER, "audrey"),
                            ("Matt Taylor", S.HUMAN, "audrey")):
        assert S.slack_agent({"actor": who, "actor_class": cls}) == agent


# ── §6 listed ───────────────────────────────────────────────────────

def test_these_proofs_are_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    for f in ("tests/test_improvement_pipeline_authority.py",
              "tests/test_improvement_pipeline_db.py"):
        assert f in listed.splitlines(), f
