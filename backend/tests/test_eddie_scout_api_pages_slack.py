"""EDDIE'S AND SCOUT'S READ API, PAGES, DESKS, PERSONAS, ROLE BRIEFS AND
SLACK IDENTITIES (migration 217).

  * /api/command/eddie, /api/command/scout, /api/command/agents/{eddie,
    scout}, the estimate and review reads need the COMMAND read credential,
    have NO write route, and return the shared workspace contract; the
    agents index lists both;
  * /api/command/agents/{eddie,scout}/page is served only to the COMMAND
    credential; its render code (run under node) draws every required
    section and the desk draws every value from the record -- NOT MEASURED
    with the reason when absent -- with no submit / trade affordance;
  * the management URLs /eddie and /scout reach the pages through the shell;
  * personas and role briefs ground each in its own records;
  * Slack: each speaks only under its own bot token; a token, app id or
    signing secret shared with ANY other agent refuses its deliveries; its
    content never goes out under another agent's token; it answers from its
    records only.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

import asyncpg
import httpx
import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from sportsassets import slack_bridge as S
from sportsassets.agents import registry as R
from sportsassets.api import agent_desks as DK
from sportsassets.api import agent_pages as P

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
REPO = pathlib.Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
T0 = 1_790_000_000.0


# ════════════════════════════════════════════════════════════════════
# THE READ API
# ════════════════════════════════════════════════════════════════════

def test_the_routes_require_the_command_credential_and_none_writes():
    from sportsassets.api import app as A
    c = TestClient(A.app)
    paths = ("/api/command/eddie", "/api/command/agents/eddie",
             "/api/command/eddie/estimates",
             "/api/command/eddie/estimates/eex:x", "/api/command/scout",
             "/api/command/agents/scout", "/api/command/pos/reviews",
             "/api/command/agents/eddie/page",
             "/api/command/agents/scout/page")
    for path in paths:
        assert c.get(path).status_code == 401, path
    from tests.test_agent_workspaces_show_runtime_records import _route_paths
    routes = set(_route_paths(A.app.routes))
    for p in paths:
        p = p.replace("eex:x", "{estimate_id}")
        assert p in routes, p
    for route in A.app.routes:
        path = getattr(route, "path", "")
        if path.startswith(("/api/command/eddie", "/api/command/scout",
                            "/api/command/pos")):
            assert getattr(route, "methods", {"GET"}) <= {"GET", "HEAD"}, \
                path


async def _ws(monkeypatch):
    from sportsassets import config, db
    monkeypatch.setenv("DATABASE_URL", DSN)
    config.settings.cache_clear()
    await db.close_pool()


@pg
@pytest.mark.asyncio
async def test_the_workspaces_follow_the_contract(monkeypatch):
    from sportsassets import config, db
    from sportsassets.api import agents_core as AC
    from sportsassets.api import agents_pos as AP
    await _ws(monkeypatch)
    try:
        e = await AP.eddie_workspace(Response())
        same = await AP.eddie_workspace_agents(Response())
        assert set(e) == set(same)
        assert e["read_only"] is True and e["production_effect"] == "NONE"
        assert e["profile"]["authority"] == "SHADOW_ONLY"
        assert "hard_rule" in e["profile"]
        assert set(P.REQUIRED_SECTIONS["eddie"]) <= set(e["sections"])
        for k, sec in e["sections"].items():
            assert sec["status"] in ("OK", "EMPTY", "UNAVAILABLE"), k
            if sec["status"] != "OK":
                assert sec["why"], k
        assert e["desk"]["affordances"] == "READ_ONLY_NO_SUBMIT_NO_TRADE"
        for name, m in e["metrics"]["metrics"].items():
            assert (m["value"] is None) == (m["status"] == "UNAVAILABLE"), \
                name
            if m["value"] is None:
                assert m["why"], name
        s = await AP.scout_workspace(Response())
        assert s["profile"]["authority"] == "RESEARCH_SHADOW_ONLY"
        assert set(P.REQUIRED_SECTIONS["scout"]) <= set(s["sections"])
        assert s["profile"]["evaluator"] == "CALIBRATION_ENGINE"
        idx = await AC.agents_index(Response())
        ids = [a["agent_id"] for a in idx["agents"]]
        assert ids == list(R.AGENTS) and "EDDIE" in ids and "SCOUT" in ids
        r = await AP.pos_reviews(Response(), limit=5)
        assert r["steps"] == ["DEREK_CANDIDATE", "KAREN_CHALLENGE",
                              "SCOUT_EVIDENCE", "EDDIE_EXECUTION_ESTIMATE",
                              "ALLOCATOR_RANKING", "AUDREY_RISK_CHECK",
                              "XAVIER_MANAGEMENT_PLAN"]
        with pytest.raises(Exception) as ex:
            await AP.eddie_estimates(Response(), recommendation="BUY",
                                     limit=5)
        assert getattr(ex.value, "status_code", None) == 400
        with pytest.raises(Exception) as ex:
            await AP.eddie_estimate("eex:none", Response())
        assert getattr(ex.value, "status_code", None) == 404
        # the connection the reads use is read only
        pool = await db.get_pool()
        async with AP._ro(pool) as conn:
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute("INSERT INTO scout_sources (source_id) "
                                   " VALUES ('x')")
        async with pool.acquire() as conn:
            assert await conn.fetchval(
                "SHOW default_transaction_read_only") == "off"
    finally:
        await db.close_pool()
        config.settings.cache_clear()


# ════════════════════════════════════════════════════════════════════
# THE PAGES AND DESKS
# ════════════════════════════════════════════════════════════════════

class _Cfg:
    admin_token = "admin-secret-p217"
    command_token = ""
    desk_token = ""


def _client(monkeypatch):
    from sportsassets.api import app as A
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    app = FastAPI()
    app.include_router(P.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("kind", ["eddie", "scout"])
def test_the_page_is_served_like_the_other_agent_pages(monkeypatch, kind):
    assert P.PAGE_PATHS[kind] == "/api/command/agents/%s/page" % kind
    assert P.ENDPOINTS[kind] == "/api/command/%s" % kind
    c = _client(monkeypatch)
    r = c.get(P.PAGE_PATHS[kind])
    assert r.status_code == 401 and P.LOCKED_TEXT in r.text
    r = c.get(P.PAGE_PATHS[kind], headers={"X-Admin-Token":
                                           _Cfg.admin_token})
    assert r.status_code == 200 and 'data-kind="%s"' % kind in r.text
    assert _Cfg.admin_token not in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    html = r.text
    for key in P.REQUIRED_SECTIONS[kind]:
        assert "key: '%s'" % key in html, key
    # the desk: every panel the acceptance standard names, the 3D loader
    # behind the capability checks, the authority in words
    for key, _label in DK.PANELS[kind]:
        assert 'data-desk-field="%s"' % key in html, key
    assert "import('%s')" % P.ENDPOINTS["characters"] in html
    for probe in ("hardwareConcurrency", "saveData", "deviceMemory",
                  "webgl", "prefers-reduced-motion: reduce"):
        assert probe in html, probe
    assert DK.DESK_META[kind]["authority"] in html
    # NO AUTHORITY AFFORDANCE on the desk
    desk = html[html.index('id="desk"'):html.index('id="talk"')]
    assert "<button" not in desk and "<form" not in desk
    for word in (">submit", ">trade", ">execute", ">approve", ">promote",
                 ">place order", ">buy", ">sell"):
        assert word not in desk.lower(), word
    assert 'href="%s"' % P.PAGE_PATHS[kind] in P.page_html("index") or \
        "'%s'" % kind.upper() in P.page_html("index")


def _node(kind, body):
    if not NODE:
        pytest.fail("node is required to execute the pages' render code")
    script = (P.render_js(kind) + DK.DESK_JS
              + "\n;(function(){ var __out = (function(){\n" + body
              + "\n})(); process.stdout.write(JSON.stringify(__out)); })();")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, text=True,
                             timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _sec(data, status="OK", why=None):
    return {"status": status, "why": why, "data": data, "evidence": []}


EDDIE_DESK = {
    "agent": "EDDIE", "name": "Eddie", "role": "Head of Execution",
    "authority": "SHADOW_ONLY",
    "heartbeat": {"state": "DECISION_RECORDED",
                  "last_heartbeat_at": T0 - 30, "activity": "x"},
    "current_task": "ESTIMATED 1 CANDIDATE(S) (SHADOW)", "alerts": [],
    "current_analysis": {
        "estimate_id": "eex:1", "candidate": {
            "decision_id": "d1", "us_market_slug": "m", "holding_side":
            "LONG", "proposed_qty": 100.0, "limit_price": 0.56},
        "recommendation": "WAIT", "reason": "EXECUTION_ECONOMICS_UNMEASURED",
        "execution_policy": "TAKER_MARKETABLE", "theoretical_edge_pp": 0.1,
        "expected_net_executable_edge_pp": None,
        "expected_fill_probability": 0.78, "expected_slippage_pp": 0.0,
        "expected_capital_hours": None, "max_executable_qty": 1000.0,
        "book": {"mid": 0.515, "best_acquisition": 0.55, "best_exit": 0.48,
                 "walk": {"vwap": 0.55, "filled_qty": 100.0}},
        "microstructure": {"spread": {"status": "MEASURED"},
                           "time_to_event": {"status": "UNAVAILABLE",
                                             "why": "NO_EVENT_START"}},
        "unmeasured": {"net_executable_edge": "COMPONENTS_UNMEASURED",
                       "capital_hours": "NO_TIME_TO_FILL"}},
    "predicted_vs_realized": None, "hypothesis": "h", "recent_finding": None,
    "economic_score": {"name": "incremental_pnl_vs_naive_usd", "value": None,
                       "why": "NO_FILLED_CANDIDATE"},
    "affordances": "READ_ONLY_NO_SUBMIT_NO_TRADE"}


def test_the_desk_draws_every_value_from_the_record_and_names_nulls():
    j = {"agent": {"agent_id": "EDDIE", "state": "DECISION_RECORDED",
                   "last_heartbeat_at": T0 - 30,
                   "cadence": {"target_interval_s": 300}},
         "read_at": T0, "desk": EDDIE_DESK}
    f = _node("eddie", "return DESK.fields('eddie', %s);" % json.dumps(j))
    for key, _ in DK.PANELS["eddie"]:
        assert key in f, key
    assert "WAIT" in f["analysis"] and "eex:1" in f["analysis"]
    assert "NOT MEASURED" in f["capital_hours"]
    assert "NO_TIME_TO_FILL" in f["capital_hours"]
    assert "NOT MEASURED" in f["economic"] and "NO_FILLED_CANDIDATE" in \
        f["economic"]
    assert "0.550" in f["microstructure"] and "NO_EVENT_START" in \
        f["microstructure"]
    assert "NOT MEASURED" in f["predicted_realized"]
    modes = _node("eddie", """
      var a = {state: 'DECISION_RECORDED', last_heartbeat_at: %f, cadence: {target_interval_s: 300}};
      return [DESK.modeOf(a, %f).mode, DESK.modeOf(a, %f).mode,
              DESK.modeOf({state: 'FAILED', last_heartbeat_at: %f}, %f).mode,
              DESK.modeOf(null, %f).mode, DESK.modeOf({state: 'EVALUATING', last_heartbeat_at: %f}, %f).mode];
    """ % (T0 - 30, T0, T0 + 5000, T0, T0, T0, T0, T0))
    assert modes == ["monitoring", "unavailable", "unavailable",
                     "unavailable", "reviewing"]
    # no desk record: every panel says so, nothing is invented
    f = _node("eddie", "return DESK.fields('eddie', {read_at: 1});")
    assert f.get("_unavailable")
    scout = {"heartbeat": {}, "alerts": [], "active_searches": [],
             "data_sources": [{"source_id": "w", "name": "Weather",
                               "licensing_class": "UNKNOWN",
                               "compliance_passed": False}],
             "features_under_test": [], "experiments": [],
             "validated_rejected": [], "incremental_predictive_value": {
                 "value": None, "why": "NO_TOURNAMENT"},
             "economic_score": {"value": None, "why": "NO_ADOPTED"}}
    f = _node("scout", "return DESK.fields('scout', %s);" % json.dumps(
        {"read_at": T0, "desk": scout}))
    for key, _ in DK.PANELS["scout"]:
        assert key in f, key
    assert "REFUSED" in f["sources"] and "NO_TOURNAMENT" in f["predictive"]
    assert "NOT MEASURED" in f["heartbeat"]


@pytest.mark.parametrize("kind", ["eddie", "scout"])
def test_the_render_code_draws_every_required_section(kind):
    specs = _node(kind, "return AG.SPECS[%r].sections.map(function(s)"
                        "{return s.key;});" % kind)
    assert set(P.REQUIRED_SECTIONS[kind]) <= set(specs)


def test_the_3d_desks_are_original_and_read_no_data():
    src = (REPO / "backend" / "sportsassets" / "assets" / "agents" /
           "cc_characters.js").read_text()
    assert "eddie: {" in src and "scout: {" in src
    assert "desk: 'execution'" in src and "desk: 'research'" in src
    assert "DEPTH AT DECISION" in src and "SPORTS FEED" in src
    assert "WEATHER" in src and "cc:desk" in src
    assert "fetch(" not in src and "/api/" not in src
    assert ".glb" not in src.split("THE DESKS")[1][:20000]


def test_the_management_urls_reach_the_pages_through_the_shell():
    shell = (REPO / "frontend" / "public" / "command" /
             "agent.html").read_text()
    toml = (REPO / "netlify.toml").read_text()
    for kind in ("eddie", "scout"):
        assert 'href="/%s" data-agent="%s"' % (kind, kind) in shell
        assert "%s: 1" % kind in shell
        m = re.search(r'from = "https://command\.bettortoken\.com/%s"\s+'
                      r'to = "/command/agent\.html"\s+status = 200\s+'
                      r'force = true' % kind, toml)
        assert m, kind
        # above the command-host catch-all
        assert toml.index('command.bettortoken.com/%s"' % kind) < \
            toml.index('command.bettortoken.com/*"')


# ════════════════════════════════════════════════════════════════════
# PERSONAS AND ROLE BRIEFS
# ════════════════════════════════════════════════════════════════════

def test_personas_are_versioned_profiles_grounded_in_their_records():
    from sportsassets.agents import persona_chat as PC
    from sportsassets.agents import personas as PS
    for aid, slug, words in (("EDDIE", "eddie", ("fast", "precise",
                                                  "controlled",
                                                  "institutional")),
                             ("SCOUT", "scout", ("research-minded",
                                                 "validation"))):
        assert aid in PS.AGENTS and PS.agent_of(slug) == aid
        p = PS.default_profile(aid)
        body = {k: p[k] for k in PS.PROFILE_FIELDS}
        assert PS.validate_profile(body) is None
        assert PS.screens_authority(body) == []
        text = (p["persona_text"] + " ".join(p["style_rules"])).lower()
        for w in words:
            assert w in text, (aid, w)
        assert aid in PC.SOURCE_ORDER and aid in PC.REFUSAL_VOICE
        assert all(s.startswith(slug) for s in PC.SOURCE_ORDER[aid])


def test_role_briefs_are_scoped_and_bounded():
    from sportsassets.agents import role_brief as B
    for aid in ("EDDIE", "SCOUT"):
        sql = B.SQL[aid]
        assert "$1" in sql and "LIMIT 101" in sql and "BETWEEN" in sql
        assert aid in B.FOCUS
    assert "eddie_execution_estimates" in B.SQL["EDDIE"]
    assert "d.account_id=$1" in B.SQL["EDDIE"]
    assert "<= 0" in B.FOCUS["EDDIE"]
    assert "never validate or promote your own feature" in B.FOCUS["SCOUT"]
    got = B.summarize("EDDIE", [dict(estimate_id="eex:%d" % i,
                                     recommendation="WAIT" if i else "SKIP_"
                                     "EXECUTION") for i in range(3)],
                      1000, "acct")
    assert got["authority"] == "SHADOW_ONLY"
    assert got["by_recommendation"] == {"SKIP_EXECUTION": 1, "WAIT": 2}
    got = B.summarize("SCOUT", [], 1000, "acct")
    assert got["status"] == "NO_RECORDED_EVIDENCE"


@pg
@pytest.mark.asyncio
async def test_their_facts_come_only_from_their_records():
    from sportsassets.agents import persona_facts as PF
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        e = await PF.gather(conn, question="what are you estimating?",
                            agent="EDDIE")
        s = await PF.gather(conn, question="which features?", agent="SCOUT")
        for b, prefix in ((e, "eddie_"), (s, "scout_")):
            assert all(f["source"].startswith(prefix) for f in b["facts"])
            assert b["paper"]["present"] is False
    finally:
        await tx.rollback()
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# SLACK: THEIR OWN BOTS, OR NOT AT ALL
# ════════════════════════════════════════════════════════════════════

BASE = {"SLACK_TEAM_ID": "T_TEST", "SLACK_ALLOWED_CHANNEL_IDS": "C_TEST",
        "SLACK_MANAGEMENT_USER_IDS": "U_TEST",
        "SLACK_WORKROOM_CHANNEL_ID": "C_TEST"}
FOUR = {"derek": ("xoxb-d", "sd", "AD"), "xavier": ("xoxb-x", "sx", "AX"),
        "audrey": ("xoxb-a", "sa", "AA"), "karen": ("xoxb-k", "sk", "AK")}


def _env(monkeypatch, **agents):
    for k, v in BASE.items():
        monkeypatch.setenv(k, v)
    for a in ("DEREK", "XAVIER", "AUDREY", "KAREN", "EDDIE", "SCOUT"):
        for f in ("BOT_TOKEN", "SIGNING_SECRET", "APP_ID"):
            monkeypatch.delenv("SLACK_%s_%s" % (a, f), raising=False)
    for a, (tok, sec, app) in agents.items():
        monkeypatch.setenv("SLACK_%s_BOT_TOKEN" % a.upper(), tok)
        monkeypatch.setenv("SLACK_%s_SIGNING_SECRET" % a.upper(), sec)
        monkeypatch.setenv("SLACK_%s_APP_ID" % a.upper(), app)


@pytest.mark.parametrize("agent", ["eddie", "scout"])
def test_a_dedicated_identity_configured_by_its_own_env_names(monkeypatch,
                                                              agent):
    assert agent in S.AGENTS and agent not in S.REQUIRED_AGENTS
    other = "scout" if agent == "eddie" else "eddie"
    _env(monkeypatch, **FOUR)
    got = S.dedicated_identity(agent)
    assert got == {"configured": False, "distinct": True, "ok": False,
                   "why": "%s_SLACK_APP_NOT_CONFIGURED" % agent.upper()}
    assert S.impersonation({"agent": agent, "source_key": "q"}) == \
        "%s_SLACK_APP_NOT_CONFIGURED_NOTHING_SENT" % agent.upper()
    own = ("xoxb-" + agent, "s" + agent, "A" + agent.upper())
    _env(monkeypatch, **FOUR, **{agent: own})
    assert S.settings(agent)["token"] == own[0]
    assert S.dedicated_identity(agent)["ok"] is True
    assert S.impersonation({"agent": agent, "source_key": "q"}) is None
    # SHARING ANY VALUE WITH ANY OTHER AGENT (Karen and each other
    # included) REFUSES EVERY DELIVERY
    for shared in (("xoxb-k", own[1], own[2]), (own[0], "sd", own[2]),
                   (own[0], own[1], "AX")):
        _env(monkeypatch, **FOUR, **{agent: shared})
        got = S.dedicated_identity(agent)
        assert got["ok"] is False and got["distinct"] is False, shared
        assert got["why"].startswith("SHARES_"), got
        assert S.impersonation({"agent": agent, "source_key": "q"}) == \
            "IMPERSONATION_REFUSED_%s_TOKEN_NOT_ITS_OWN" % agent.upper()
    _env(monkeypatch, **FOUR, **{agent: own, other: own})
    assert S.dedicated_identity(agent)["distinct"] is False
    # ITS CONTENT NEVER GOES OUT UNDER ANOTHER AGENT'S TOKEN
    _env(monkeypatch, **FOUR, **{agent: own})
    for a in ("derek", "xavier", "audrey", "karen", other):
        assert S.impersonation({"agent": a, "source_key":
                                "%s:review:pcr:1" % agent}) == \
            "IMPERSONATION_REFUSED_%s_CONTENT_ON_ANOTHER_TOKEN" % \
            agent.upper()
    # Karen's identity check now also refuses a value shared with them
    _env(monkeypatch, **FOUR, **{agent: ("xoxb-k", "x", "y")})
    assert S.karen_identity()["distinct"] is False


def test_the_manifests_and_the_one_admin_step_per_app():
    for agent, name in (("eddie", "Eddie"), ("scout", "Scout")):
        m = json.loads((REPO / "research" / ("%s-manifest.json" % agent))
                       .read_text())
        assert m["features"]["bot_user"]["display_name"] == name
        assert m["settings"]["event_subscriptions"]["request_url"].endswith(
            "/api/integrations/slack/%s/events" % agent)
        assert len(m["display_information"]["description"]) <= 140
        assert set(m["oauth_config"]["scopes"]["bot"]) == {
            "app_mentions:read", "chat:write", "chat:write.public"}
    doc = (REPO / "research" / "eddie_scout_slack_setup.md").read_text()
    assert doc.count("-- THE ACTION") == 2
    for v in ("SLACK_EDDIE_BOT_TOKEN", "SLACK_EDDIE_SIGNING_SECRET",
              "SLACK_EDDIE_APP_ID", "SLACK_SCOUT_BOT_TOKEN",
              "SLACK_SCOUT_SIGNING_SECRET", "SLACK_SCOUT_APP_ID"):
        assert v in doc, v


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


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["eddie", "scout"])
async def test_answers_from_records_under_its_own_token(pool, monkeypatch,
                                                        agent):
    own = ("xoxb-" + agent, "s" + agent, "A" + agent.upper())
    _env(monkeypatch, **FOUR, **{agent: own})
    from sportsassets.agents import persona_chat as PC
    converse = AsyncMock(side_effect=AssertionError("no persona model"))
    monkeypatch.setattr(PC, "converse", converse)
    sent = []

    async def transport(request):
        sent.append((request.headers["authorization"],
                     json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "ts": "9.9"})
    cls = httpx.AsyncClient
    monkeypatch.setattr(S.httpx, "AsyncClient", lambda **kw: cls(
        transport=httpx.MockTransport(transport), **kw))
    cfg = S.settings(agent)
    async with pool.acquire() as c:
        assert await S.admit(c, agent, cfg, {
            "source": "Ev-p217-" + agent, "channel": "C_TEST",
            "thread": "1.1", "text": "<@UE> status?", "user": "U_TEST",
            "followup": False}) == "QUEUED"
        job = await S.claim(c)
    assert job["agent"] == agent
    await S.process(pool, job)
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM agent_slack_delivery WHERE "
                               " agent=$1", agent)
    assert row["state"] == "SENT", row["error_code"]
    assert len(sent) == 1 and sent[0][0] == "Bearer " + own[0]
    assert "answered from records only" in sent[0][1]["text"]
    assert converse.await_count == 0
