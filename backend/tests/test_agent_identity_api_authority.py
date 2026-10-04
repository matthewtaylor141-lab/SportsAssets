"""CAPITAL-CRITICAL: AGENT IDENTITY, MEMORY AND EVENTS HOLD NO AUTHORITY.

Migration 224 and its read surfaces (api/agents_identity.py; agents/
identity.py, agent_memory.py, agent_context.py, agent_activity.py) make the
seven agents distinct software entities. This proves none of it can move
money, change a control or widen an agent's authority:

  §1 STATIC. The API module and the agent modules import only the standard
     library, FastAPI and an allow-list of pure / registry modules -- no
     order, venue, execution, ledger, paper, funded or submit module,
     directly or by name. The read modules' SQL holds no write or DDL
     keyword; the memory learner writes ONLY agent_memory_events (insert,
     plus the one superseded_by update), agent_conversation_messages and
     its own ingestion_state watermark. The only SET is the transaction-
     local statement timeout.
  §2 ROUTES. Every new route is GET only and answers 401 without a command
     session; the router declares no other method.
  §3 READ ONLY AT RUNTIME. The request path opens `BEGIN READ ONLY` with a
     bounded statement timeout; a write inside it is refused by Postgres and
     the identity card runs to completion inside it.
  §4 BUNDLES. Role-specific tool bundles differ per agent; Karen holds no
     authority tool, Scout no order-mutation tool, Eddie no capital or
     approval tool; Eddie stays SHADOW_ONLY, Scout RESEARCH_SHADOW_ONLY,
     Karen challenge-only; Xavier's bundle requires the current review
     identity. A bundle never widens the registry.
  §5 VOICE. The speech route refuses a voice another agent already speaks
     with (VOICE_UNAVAILABLE), never a silent fallback.
  §6 LISTED. This file is on the capital-critical list.
"""
from __future__ import annotations

import ast
import itertools
import os
import pathlib
import re
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
API = PKG / "api" / "agents_identity.py"
AGENT_MODULES = {
    "identity": PKG / "agents" / "identity.py",
    "agent_memory": PKG / "agents" / "agent_memory.py",
    "agent_context": PKG / "agents" / "agent_context.py",
    "agent_activity": PKG / "agents" / "agent_activity.py",
}
READ_ONLY_MODULES = (API, AGENT_MODULES["identity"],
                     AGENT_MODULES["agent_context"],
                     AGENT_MODULES["agent_activity"])
DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

STDLIB = {"__future__", "json", "time", "datetime", "typing", "hashlib",
          "copy", "os", "logging", "math", "sys"}
ALLOWED_OURS = {
    "sportsassets.agents.registry", "sportsassets.agents.role_brief",
    "sportsassets.agents.directives", "sportsassets.agents.personas",
    "sportsassets.agents.identity", "sportsassets.agents.agent_memory",
    "sportsassets.agents.agent_context", "sportsassets.agents.agent_activity",
    "sportsassets.xavier_freshness", "sportsassets.order_state_truth",
    "sportsassets.api.agents_core"}
FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution", "ledger", "simulator", "bettor_funded", "funded",
             "paper", "smalllive", "submit", "live_", "actual_admission",
             "pinnapi", "slack_bridge", "xavier_policy", "derek_policy",
             "karen_runner", "peer_responder", "intel.runner",
             "xavier_management", "bettor_xavier")
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM|"
                   r"TRUNCATE|ALTER\s+|DROP\s+|CREATE\s+|COPY\s+|GRANT\s+|"
                   r"SET\s+ROLE|SET\s+SESSION)", re.I)
MEMORY_WRITES = {"INSERT INTO agent_memory_events",
                 "UPDATE agent_memory_events SET superseded_by",
                 "INSERT INTO agent_conversation_messages",
                 "INSERT INTO ingestion_state"}


def _imports(path: pathlib.Path) -> set:
    pkg = ".".join(path.relative_to(ROOT).with_suffix("").parts[:-1])
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg.split(".")
                base = base[:len(base) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module
            as_file = ROOT / (mod.replace(".", "/") + ".py")
            if mod.split(".")[0] == "sportsassets" and not as_file.exists():
                out.update("%s.%s" % (mod, a.name) for a in node.names)
            else:
                out.add(mod)
    return out


def _strings(path: pathlib.Path) -> list:
    return [n.value for n in ast.walk(ast.parse(path.read_text()))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


# ── §1 static ────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [API] + list(AGENT_MODULES.values()),
                         ids=lambda p: p.name)
def test_the_modules_import_no_order_venue_ledger_paper_or_funded_module(
        path):
    for imp in _imports(path):
        top = imp.split(".")[0]
        if top == "sportsassets":
            assert imp in ALLOWED_OURS, (path.name, imp)
        else:
            assert top in STDLIB or top == "fastapi", (path.name, imp)
        assert not any(f in imp.lower() for f in FORBIDDEN), (path.name, imp)


def test_the_allowed_existing_modules_stay_clean_at_module_level():
    """registry / role_brief / directives / personas, which the new modules
    import, pull in no order, venue, funded or paper module at import time
    (registry's lazy model-version lookup is never called here)."""
    for name in ("role_brief", "directives", "personas"):
        tree = ast.parse((PKG / "agents" / ("%s.py" % name)).read_text())
        for node in tree.body:
            mods = []
            if isinstance(node, ast.ImportFrom):
                mods = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for m in mods:
                assert not any(f in m.lower() for f in FORBIDDEN), (name, m)
    src = (PKG / "agents" / "registry.py").read_text()
    for name in ("identity", "agent_memory", "agent_context",
                 "agent_activity"):
        text = AGENT_MODULES[name].read_text()
        for fn in ("ensure_identities", "heartbeat(", "start_run",
                   "create_task", "link_decision", "_model_version"):
            assert "R.%s" % fn not in text, (name, fn)
    assert "def permits" in src


@pytest.mark.parametrize("path", READ_ONLY_MODULES, ids=lambda p: p.name)
def test_the_read_modules_sql_is_select_only(path):
    sqls = [s for s in _strings(path)
            if re.search(r"\b(SELECT|FROM)\b", s)]
    for s in sqls:
        assert not WRITE.search(s), (path.name, s[:120])
    src = path.read_text()
    for x in re.findall(r"\bSET\s+[A-Z_]+[^\"']*", src):
        assert x.startswith("SET LOCAL statement_timeout"), (path.name, x)


def test_the_memory_learner_writes_only_memory_messages_and_its_watermark():
    src = AGENT_MODULES["agent_memory"].read_text()
    flat = re.sub(r"\s+", " ", " ".join(_strings(AGENT_MODULES[
        "agent_memory"])))
    writes = {m.group(0).strip() for m in re.finditer(
        r"\b(INSERT INTO [a-z_]+|UPDATE [a-z_]+ SET [a-z_]+|DELETE FROM "
        r"[a-z_]+|TRUNCATE|ALTER [A-Z]+|DROP [A-Z]+|CREATE [A-Z]+|"
        r"GRANT |COPY )", flat)}
    assert writes and writes <= MEMORY_WRITES, writes
    assert "SET LOCAL" not in src and "SET ROLE" not in src


def test_the_api_reads_inside_a_bounded_read_only_transaction():
    src = API.read_text()
    assert "transaction(readonly=True)" in src
    from sportsassets.api import agents_identity as A
    assert 0 < A.STATEMENT_TIMEOUT_MS <= 10000


# ── §2 routes ────────────────────────────────────────────────────────

NEW_PATHS = {"/api/command/agents/stream",
             "/api/command/agents/{agent}/identity",
             "/api/command/agents/{agent}/identity/versions",
             "/api/command/agents/{agent}/memories",
             "/api/command/agents/{agent}/experience",
             "/api/command/agents/{agent}/relationships",
             "/api/command/agents/{agent}/events",
             "/api/command/agents/{agent}/evaluation",
             "/api/command/agents/{agent}/context"}


def test_the_routes_are_get_only_and_require_a_command_session():
    from sportsassets.api import agents_identity as A
    from sportsassets.api import app as APP

    mine = {r.path: set(r.methods) for r in A.router.routes}
    assert set(mine) == NEW_PATHS
    for p, methods in mine.items():
        assert methods <= {"GET", "HEAD"}, (p, methods)
    registered, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") in NEW_PATHS:
            registered.setdefault(r.path, set()).update(
                getattr(r, "methods", set()) or set())
    assert set(registered) == NEW_PATHS
    for p, methods in registered.items():
        assert methods <= {"GET", "HEAD"}, (p, methods)
    client = TestClient(APP.app, raise_server_exceptions=False)
    for slug in ("derek", "xavier", "audrey", "karen", "allocator", "eddie",
                 "scout"):
        for leaf in ("identity", "identity/versions", "memories",
                     "experience", "relationships", "events", "evaluation",
                     "context"):
            route = "/api/command/agents/%s/%s" % (slug, leaf)
            assert client.get(route).status_code == 401, route
            assert client.post(route).status_code in (401, 405), route
    assert client.get("/api/command/agents/stream").status_code == 401
    assert client.post("/api/command/agents/stream").status_code in (401,
                                                                     405)


# ── §3 read only at runtime ──────────────────────────────────────────

class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


class _R:
    headers: dict = {}


@pg
async def test_the_request_path_is_a_read_only_transaction(monkeypatch):
    import asyncpg

    from sportsassets.api import agents_identity as A

    conn = await asyncpg.connect(DSN)
    try:
        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(A, "_pool", pool)

        async def write(c):
            assert await c.fetchval("SHOW transaction_read_only") == "on"
            assert await c.fetchval(
                "SELECT setting::int FROM pg_settings "
                " WHERE name='statement_timeout'") == A.STATEMENT_TIMEOUT_MS
            await c.execute(
                "INSERT INTO agent_memory_events (memory_id) VALUES ('x')")

        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await A._read_only(write)
        for slug in ("karen", "allocator", "xavier"):
            got = await A.agent_identity(slug, _R())
            assert got["read_only"] is True
            assert got["agent"]["slug"] == slug
        ev = await A.agents_stream(_R(), since=None, agent=None, limit=20)
        assert ev["rejected_without_durable_basis"] == 0
        for fn in (A.agent_experience, A.agent_relationships,
                   A.agent_evaluation, A.agent_context):
            assert (await fn("eddie", _R()))["agent"] == "EDDIE"
        m = await A.agent_memories("scout", _R(), kind=None, before=None,
                                   limit=10, include_superseded=True)
        assert m["agent"] == "SCOUT"
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as e:
            await A.agent_identity("mallory", _R())
        assert e.value.status_code == 404
    finally:
        await conn.close()


# ── §4 role-specific bundles and authority boundaries ────────────────

def test_every_agent_has_a_different_bundle():
    from sportsassets.agents import agent_context as AC
    from sportsassets.agents import identity as I
    bundles = {a: AC.bundle(a) for a in I.AGENTS}
    for a, b in itertools.combinations(I.AGENTS, 2):
        assert AC.fingerprint(bundles[a]) != AC.fingerprint(bundles[b]), (a,
                                                                          b)
        assert bundles[a]["tools"] != bundles[b]["tools"], (a, b)
    assert set(bundles["DEREK"]["tools"]) != set(bundles["AUDREY"]["tools"])
    assert "read.all" in bundles["AUDREY"]["tools"]
    assert "read.all" not in bundles["DEREK"]["tools"]


def test_karen_scout_eddie_boundaries_and_xavier_context():
    from sportsassets.agents import agent_context as AC
    from sportsassets.agents import identity as I
    from sportsassets.agents import registry as R
    k, s, e, x = (AC.bundle(a) for a in ("KAREN", "SCOUT", "EDDIE",
                                         "XAVIER"))
    assert not set(k["tools"]) & AC.AUTHORITY_TOOLS
    assert k["order_path"] is None
    assert k["authority_status"] == "CHALLENGE_ONLY_ZERO_AUTHORITY"
    assert not set(s["tools"]) & AC.ORDER_MUTATION_TOOLS
    assert s["order_path"] is None
    assert s["authority_status"] == "RESEARCH_SHADOW_ONLY"
    assert not set(e["tools"]) & AC.CAPITAL_APPROVAL_TOOLS
    assert not set(e["tools"]) & AC.ORDER_MUTATION_TOOLS
    assert e["authority_status"] == "SHADOW_ONLY"
    for t in AC.CAPITAL_APPROVAL_TOOLS:
        assert not I.permits("EDDIE", t), t
    for t in AC.ORDER_MUTATION_TOOLS:
        assert not I.permits("SCOUT", t) and not I.permits("KAREN", t), t
        assert not I.permits("CHIEF_ALLOCATOR", t), t
    assert x["requires"] == ["current_review_identity",
                             "filled_only_protection"]
    assert any("WAITING_FOR_FRESH_EVIDENCE" in r for r in x["rules"])
    assert any("only FILLED quantity is protection" in r for r in x["rules"])
    # no agent can activate itself, change limits / credentials, deploy,
    # approve itself or promote its own model
    for a in I.AGENTS:
        b = AC.bundle(a)
        assert set(R.NEVER_GRANTED) <= set(b["denied"])
        assert not set(b["tools"]) & set(R.NEVER_GRANTED)
        # a bundle never widens the registry
        for t in b["tools"]:
            assert I.permits(a, t), (a, t)
        if a != "CHIEF_ALLOCATOR":
            assert set(b["tools"]) <= set(
                R.IDENTITIES[a]["tool_permissions"]["allowed"])


def test_memory_and_messages_cannot_carry_authority():
    from sportsassets.agents import agent_memory as M
    c = {"agent_id": "EDDIE", "memory_kind": M.LESSON,
         "subject_type": "execution_calibration", "subject_id": "x",
         "summary": "s", "confidence": 0.9, "deriver": "t",
         "evidence_refs": [{"kind": "eddie_execution_outcomes", "id": "o"}]}
    for facts in ({"activate": True}, {"x": {"promotion": "model-v2"}},
                  {"small_live": "on"}, {"new_limit": 50},
                  {"approve": "self"}):
        assert M.validate_candidate(dict(c, facts=facts)) == M.R_AUTHORITY


# ── §5 the speech route never borrows another agent's voice ─────────

async def test_the_speech_route_refuses_a_voice_another_agent_speaks_with(
        monkeypatch):
    from sportsassets.agents import identity as I
    from sportsassets.api import agents_persona as AP

    latest = {"DEREK": {"agent_id": "DEREK", "status": "RESOLVED",
                        "voice_id": "LiamVoice0001", "resolved_at": 100.0}}

    async def fake_latest(conn):
        return dict(latest)
    monkeypatch.setattr(I, "latest_resolutions", fake_latest)
    pool = _Pool(None)
    res = {"status": "RESOLVED", "voice_id": "LiamVoice0001",
           "resolved_at": 200.0}
    assert await AP._voice_owner_conflict(pool, "SCOUT", res) == "DEREK"
    assert await AP._voice_owner_conflict(pool, "DEREK", res) is None
    other = {"status": "RESOLVED", "voice_id": "BrianVoice001"}
    assert await AP._voice_owner_conflict(pool, "SCOUT", other) is None

    async def broken(conn):
        raise RuntimeError("down")
    monkeypatch.setattr(I, "latest_resolutions", broken)
    assert await AP._voice_owner_conflict(pool, "SCOUT", res) == \
        "VOICE_OWNERSHIP_UNVERIFIED"

    # the chat response names the agent's OWN voice profile and the audio
    # status; a failed read is VOICE_UNAVAILABLE with the reason
    async def broken_voice(conn, agent, env=None):
        raise RuntimeError("down")
    monkeypatch.setattr(I, "read_voice", broken_voice)
    v = await AP._voice_response(pool, "SCOUT", {"message_id": "m1",
                                                 "answer": "a"})
    assert v["voice_profile_id"] == "vp-scout-v1" and v["agent"] == "SCOUT"
    assert v["audio"]["status"] == "VOICE_UNAVAILABLE"
    assert v["audio"]["reason"].startswith("VOICE_READ_FAILED")
    assert v["message_id"] == "m1" and v["text"] == "a"


# ── §6 listed ────────────────────────────────────────────────────────

def test_this_proof_is_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_agent_identity_api_authority.py" in listed.splitlines()
