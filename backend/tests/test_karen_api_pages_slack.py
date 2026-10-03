"""KAREN'S READ API, HER PAGE, THE AGENTS INDEX, AND HER SLACK IDENTITY.

  * GET /api/command/karen and /api/command/agents/karen (parity with the
    other agents) require the COMMAND read credential and return the profile,
    the current challenges and the metrics (null, never 0, when there is
    nothing to measure) inside the shared workspace contract; the peer
    response writes require the CONTROL credential;
  * the agents index lists Karen beside Derek, Xavier and Audrey;
  * /api/command/agents/karen/page is served only to the COMMAND credential
    and its OWN render code (run under node) draws every required section;
  * Slack: Karen speaks only under her own bot token. A token, app id or
    signing secret shared with another agent refuses her deliveries; Karen's
    content is never sent under another agent's token; her answers come from
    her records (no persona model); the bridge can be enabled without her.
"""
from __future__ import annotations

import json
import os

import asyncpg
import httpx
import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from sportsassets import slack_bridge as S
from sportsassets.agents import karen as K
from sportsassets.agents import registry as R
from sportsassets.api import agent_pages as P

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
T0 = 1_790_000_000.0


# ════════════════════════════════════════════════════════════════════
# THE READ API
# ════════════════════════════════════════════════════════════════════

def test_the_karen_routes_require_the_command_credentials():
    from sportsassets.api import app as A
    c = TestClient(A.app)
    for path in ("/api/command/karen", "/api/command/agents/karen",
                 "/api/command/karen/challenges",
                 "/api/command/karen/challenges/kch:x"):
        assert c.get(path).status_code == 401, path
    for w in ("respond", "resolve", "false-block", "improvement"):
        r = c.post("/api/command/karen/challenges/kch:x/%s" % w, json={})
        assert r.status_code in (401, 403), (w, r.status_code)
    from tests.test_agent_workspaces_show_runtime_records import (
        _first_match, _route_paths)
    paths = set(_route_paths(A.app.routes))
    for p in ("/api/command/karen", "/api/command/agents/karen",
              "/api/command/agents/karen/page",
              "/api/command/karen/challenges/{challenge_id}/respond"):
        assert p in paths, p
    # the page path is answered by the pages router, not an agent router
    assert _first_match(A.app, "/api/command/agents/karen/page") is P.router


@pg
@pytest.mark.asyncio
async def test_the_workspace_reads_profile_challenges_and_null_metrics(
        monkeypatch):
    from sportsassets import config, db
    from sportsassets.api import agents_core as AC
    from sportsassets.api import agents_karen as AK

    monkeypatch.setenv("DATABASE_URL", DSN)
    config.settings.cache_clear()
    await db.close_pool()
    conn = await asyncpg.connect(DSN)
    try:
        if await conn.fetchval("SELECT count(*) FROM karen_challenges"):
            pytest.skip("this database holds committed Karen challenges")
        got = await AK.karen_workspace(Response())
        same = await AK.karen_workspace_agents(Response())
        assert set(got) == set(same)
        assert got["read_only"] is True and got["production_effect"] == "NONE"
        prof = got["profile"]
        assert prof["agent_id"] == "KAREN" and prof["authority"] == "NONE"
        assert prof["role"] == "RED_TEAM_CHALLENGE"
        assert "write.approvals" in prof["tool_permissions"]["denied"]
        assert set(P.REQUIRED_SECTIONS["karen"]) <= set(got["sections"])
        assert got["challenges"]["status"] == "EMPTY"
        assert got["challenges"]["why"]
        for name, m in got["metrics"]["metrics"].items():
            assert m["value"] is None and m["numerator"] is None, name
            assert m["why"], name
        # THE AGENTS INDEX LISTS KAREN
        idx = await AC.agents_index(Response())
        assert [a["agent_id"] for a in idx["agents"]] == list(R.AGENTS)
        assert "KAREN" in [a["agent_id"] for a in idx["agents"]]
        lst = await AK.karen_challenges(Response(), state=None, target=None,
                                        limit=10)
        assert lst["challenges"]["status"] == "EMPTY"
        with pytest.raises(Exception) as e:
            await AK.karen_challenge("kch:none", Response())
        assert getattr(e.value, "status_code", None) == 404
        with pytest.raises(Exception) as e:
            await AK.karen_challenges(Response(), state="HAPPY", target=None,
                                      limit=10)
        assert getattr(e.value, "status_code", None) == 400
    finally:
        await conn.close()
        await db.close_pool()
        config.settings.cache_clear()


# ════════════════════════════════════════════════════════════════════
# THE PAGE
# ════════════════════════════════════════════════════════════════════

class _Cfg:
    admin_token = "admin-secret-k207"
    command_token = ""
    desk_token = ""


def _client(monkeypatch):
    from sportsassets.api import app as A
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    app = FastAPI()
    app.include_router(P.router)
    return TestClient(app, raise_server_exceptions=False)


def test_the_karen_page_is_served_like_the_other_agent_pages(monkeypatch):
    assert P.PAGE_PATHS["karen"] == "/api/command/agents/karen/page"
    assert P.ENDPOINTS["karen"] == "/api/command/karen"
    c = _client(monkeypatch)
    r = c.get(P.PAGE_PATHS["karen"])
    assert r.status_code == 401 and P.LOCKED_TEXT in r.text
    assert "AG.workspace" not in r.text
    r = c.get(P.PAGE_PATHS["karen"], headers={"X-Admin-Token":
                                              _Cfg.admin_token})
    assert r.status_code == 200 and 'data-kind="karen"' in r.text
    assert "no-store" in r.headers.get("cache-control", "")
    assert "connect-src 'self'" in r.headers["content-security-policy"]
    assert _Cfg.admin_token not in r.text
    # the plain nav links Karen; the index and the CC pages reach her page
    assert 'href="%s" aria-current="page"' % P.PAGE_PATHS["karen"] in r.text
    assert 'href="/api/command/agents/karen/page"' in P.page_html("derek")
    assert "'KAREN'" in P.page_html("index")
    for key in P.REQUIRED_SECTIONS["karen"]:
        assert "key: '%s'" % key in r.text, key


def test_the_page_render_code_draws_every_section_and_null_as_unknown():
    from tests.test_agent_workspaces_render_the_contract import (
        _agent, _card, _node, _sec)
    specs = _node("karen", "return AG.SPECS.karen.sections.map("
                           "function(s){return s.key;});")
    assert set(P.REQUIRED_SECTIONS["karen"]) <= set(specs)
    metrics = K.summarise_metrics([], set(), set())
    ch = {"challenge_id": "kch:1", "target_agent": "DEREK",
          "target_kind": "agent_decisions", "target_id": "adr:1",
          "detector": "DECISION_WITHOUT_EVIDENCE", "severity": "MEDIUM",
          "state": "OPEN", "claim": "cites nothing",
          "time_to_challenge_s": 60, "challenged_at": T0,
          "evidence": [{"kind": "agent_decisions", "id": "adr:1"}]}
    j = {"agent": _agent("KAREN"), "read_at": T0, "read_only": True,
         "sections": {
             "status": _sec({"state": "IDLE"}),
             "authority": _sec({"authority": "NONE"}),
             "current_challenges": _sec([ch]),
             "recent_challenges": _sec([], "EMPTY", "KAREN_HAS_RAISED_NO"),
             "metrics": _sec(metrics),
             "detectors": _sec({"interval_s": 300})}}
    html = _node("karen", "return AG.workspace('karen', %s);" % json.dumps(j))
    for key in P.REQUIRED_SECTIONS["karen"]:
        _card(html, key)
    st, body = _card(html, "metrics")
    assert st == "OK" and "UNKNOWN" in body
    assert "evidence_grounding" in body and "NO_CHALLENGE_HAS_BEEN_RAISED" \
        in body
    st, body = _card(html, "current_challenges")
    assert "kch:1" in body and "awaiting DEREK" in body
    st, _ = _card(html, "recent_challenges")
    assert st == "EMPTY"


# ════════════════════════════════════════════════════════════════════
# SLACK: HER OWN BOT, OR NOT AT ALL
# ════════════════════════════════════════════════════════════════════

BASE = {"SLACK_TEAM_ID": "T_TEST", "SLACK_ALLOWED_CHANNEL_IDS": "C_TEST",
        "SLACK_MANAGEMENT_USER_IDS": "U_TEST",
        "SLACK_WORKROOM_CHANNEL_ID": "C_TEST"}


def _env(monkeypatch, **agents):
    for k, v in BASE.items():
        monkeypatch.setenv(k, v)
    for a in ("DEREK", "XAVIER", "AUDREY", "KAREN"):
        for f in ("BOT_TOKEN", "SIGNING_SECRET", "APP_ID"):
            monkeypatch.delenv("SLACK_%s_%s" % (a, f), raising=False)
    for a, (tok, sec, app) in agents.items():
        monkeypatch.setenv("SLACK_%s_BOT_TOKEN" % a.upper(), tok)
        monkeypatch.setenv("SLACK_%s_SIGNING_SECRET" % a.upper(), sec)
        monkeypatch.setenv("SLACK_%s_APP_ID" % a.upper(), app)


THREE = {"derek": ("xoxb-d", "sd", "AD"), "xavier": ("xoxb-x", "sx", "AX"),
         "audrey": ("xoxb-a", "sa", "AA")}


def test_karen_is_a_bridge_identity_configured_by_her_own_env_names(
        monkeypatch):
    assert "karen" in S.AGENTS and "karen" not in S.REQUIRED_AGENTS
    _env(monkeypatch, **THREE)
    got = S.karen_identity()
    assert got == {"configured": False, "distinct": True, "ok": False,
                   "why": "KAREN_SLACK_APP_NOT_CONFIGURED"}
    _env(monkeypatch, **THREE, karen=("xoxb-k", "sk", "AK"))
    assert S.settings("karen")["token"] == "xoxb-k"
    assert S.karen_identity()["ok"] is True
    # SHARING ANY OF THEM WITH ANOTHER AGENT IS IMPERSONATION
    for shared in (("xoxb-a", "sk", "AK"), ("xoxb-k", "sd", "AK"),
                   ("xoxb-k", "sk", "AX")):
        _env(monkeypatch, **THREE, karen=shared)
        got = S.karen_identity()
        assert got["ok"] is False and got["distinct"] is False, shared
        assert got["why"].startswith("SHARES_"), got
        assert S.impersonation({"agent": "karen", "source_key": "q"}) == \
            "IMPERSONATION_REFUSED_KAREN_TOKEN_NOT_HER_OWN"
    _env(monkeypatch, **THREE, karen=("xoxb-k", "sk", "AK"))
    assert S.impersonation({"agent": "karen", "source_key": "q"}) is None
    # KAREN'S CONTENT NEVER GOES OUT UNDER ANOTHER AGENT'S TOKEN
    for a in ("derek", "xavier", "audrey"):
        assert S.impersonation({"agent": a,
                                "source_key": "karen:challenge:kch:1"}) == \
            "IMPERSONATION_REFUSED_KAREN_CONTENT_ON_ANOTHER_TOKEN"
        assert S.impersonation({"agent": a, "source_key": "review:1"}) is None


@pytest.fixture
async def pool(monkeypatch):
    if not DSN:
        pytest.skip("requires real Postgres")
    p = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
    async with p.acquire() as c:
        await c.execute("DELETE FROM agent_slack_delivery")
        await c.execute("UPDATE ingestion_state SET value="
                        "'{\"enabled\":true}' WHERE key=$1", S.CONTROL)
    yield p
    async with p.acquire() as c:
        await c.execute("DELETE FROM agent_slack_delivery")
        await c.execute("UPDATE ingestion_state SET value="
                        "'{\"enabled\":false}' WHERE key=$1", S.CONTROL)
    await p.close()


def _mention():
    return {"source": "Ev-k207", "channel": "C_TEST", "thread": "1.1",
            "text": "<@UK> what are you challenging?", "user": "U_TEST",
            "followup": False}


async def _queue_and_claim(pool):
    cfg = S.settings("karen")
    async with pool.acquire() as c:
        assert await S.admit(c, "karen", cfg, _mention()) == "QUEUED"
        return await S.claim(c)


@pytest.mark.asyncio
async def test_karen_answers_from_records_under_her_own_token(pool,
                                                              monkeypatch):
    _env(monkeypatch, **THREE, karen=("xoxb-k", "sk", "AK"))
    from sportsassets.agents import persona_chat as PC
    converse = AsyncMock(side_effect=AssertionError("no persona for Karen"))
    monkeypatch.setattr(PC, "converse", converse)
    sent = []

    async def transport(request):
        sent.append((request.headers["authorization"],
                     json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "ts": "9.9"})
    cls = httpx.AsyncClient
    monkeypatch.setattr(S.httpx, "AsyncClient", lambda **kw: cls(
        transport=httpx.MockTransport(transport), **kw))
    job = await _queue_and_claim(pool)
    assert job["agent"] == "karen"
    await S.process(pool, job)
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM agent_slack_delivery")
    assert row["state"] == "SENT", row["error_code"]
    assert len(sent) == 1 and sent[0][0] == "Bearer xoxb-k"
    assert "answered from records only" in sent[0][1]["text"]
    assert converse.await_count == 0


@pytest.mark.asyncio
async def test_a_shared_token_refuses_every_karen_delivery(pool,
                                                           monkeypatch):
    _env(monkeypatch, **THREE, karen=("xoxb-k", "sk", "AK"))
    job = await _queue_and_claim(pool)
    _env(monkeypatch, **THREE, karen=("xoxb-a", "sk", "AK"))  # Audrey's token
    calls = []

    async def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "ts": "1"})
    cls = httpx.AsyncClient
    monkeypatch.setattr(S.httpx, "AsyncClient", lambda **kw: cls(
        transport=httpx.MockTransport(transport), **kw))
    await S.process(pool, job)
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT state, error_code FROM "
                               "agent_slack_delivery")
    assert row["state"] == "FAILED"
    assert row["error_code"] == "IMPERSONATION_REFUSED_KAREN_TOKEN_NOT_HER_OWN"
    assert calls == []


@pg
@pytest.mark.asyncio
async def test_her_challenges_are_queued_only_as_herself(monkeypatch):
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await R.ensure_identities(conn)
        await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " decided_at) VALUES ('adr:k207-sl','XAVIER','T',now())")
        await conn.execute(
            "INSERT INTO karen_challenges (challenge_id, target_agent, "
            " target_kind, target_id, detector, claim, severity, "
            " evidence_refs, record_at, challenged_at) VALUES ('kch:sl',"
            " 'XAVIER','agent_decisions','adr:k207-sl','T','c','HIGH',"
            " '[{\"kind\":\"agent_decisions\",\"id\":\"adr:k207-sl\"}]',"
            " now(), now())")
        _env(monkeypatch, **THREE, karen=("xoxb-d", "sk", "AK"))  # shared
        await S.publish_karen_challenges(conn)
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_slack_delivery") == 0
        _env(monkeypatch, **THREE, karen=("xoxb-k", "sk", "AK"))
        await S.publish_karen_challenges(conn)
        await S.publish_karen_challenges(conn)                  # once
        rows = await conn.fetch("SELECT agent, source_key, answer, state "
                                "  FROM agent_slack_delivery")
        assert len(rows) == 1
        assert rows[0]["agent"] == "karen"
        assert rows[0]["source_key"] == "karen:challenge:kch:sl"
        assert "Karen cannot resolve her own challenge" in rows[0]["answer"]
        assert rows[0]["state"] == "READY"
        # the bridge can be enabled with the three; Karen is optional
        _env(monkeypatch, **THREE)
        st = await S.configure(conn, True, "Test manager")
        assert st["enabled"] is True
        assert st["karen_identity"]["ok"] is False
        assert st["agents"]["karen"]["configured"] is False
        # ...and the database refuses Karen switching the bridge herself
        with pytest.raises(asyncpg.PostgresError):
            async with conn.transaction():
                await S.configure(conn, False, "karen")
    finally:
        await tx.rollback()
        await conn.close()


@pytest.mark.asyncio
async def test_check_tokens_flags_two_agents_on_one_bot(monkeypatch):
    _env(monkeypatch, **THREE, karen=("xoxb-k", "sk", "AK"))

    async def transport(request):
        tok = request.headers["authorization"]
        user = "U_SAME" if tok in ("Bearer xoxb-k", "Bearer xoxb-a") \
            else "U_" + tok[-1]
        return httpx.Response(200, json={"ok": True, "user_id": user,
                                         "team_id": "T_TEST",
                                         "bot_id": "B" + user})
    cls = httpx.AsyncClient
    monkeypatch.setattr(S.httpx, "AsyncClient", lambda **kw: cls(
        transport=httpx.MockTransport(transport), **kw))
    got = await S.check_tokens()
    assert got["karen"]["shares_bot_user_with"] == ["audrey"]
    assert got["derek"]["shares_bot_user_with"] == []
    assert "xoxb-k" not in json.dumps(got)

