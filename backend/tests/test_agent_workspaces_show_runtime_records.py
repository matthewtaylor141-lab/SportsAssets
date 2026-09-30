"""PROOF 22 (UI), AFTER THE MERGE: THE WORKSPACES DRAW THE REAL RECORDS.

The companion of `test_agent_workspaces_render_the_contract.py`. That file
proves the pages draw the contract truthfully; this one proves they draw the
JSON the REAL agent routers build from the real database -- no stand-ins.

REQUIRES_AGENT_ROUTERS. The five endpoint modules are built by the core,
derek, xavier, audrey and chat streams in parallel with the UI stream, and do
not exist in the UI branch. This file therefore FAILS, by name, until they are
merged and `api/app.py` includes their routers and `agent_pages.router`. It is
deliberately NOT in `tools/capital_critical_tests.txt` on the UI branch; the
integrator adds it after merging. It never skips: a skipped proof is how an
unrendered record ships.

Each test reads the endpoints through the real app (ASGI, one event loop, the
real `require_command` and the real pool), checks the contract shape, and
runs the page's own render code under node against the returned JSON.
"""
import importlib
import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

REQUIRES_AGENT_ROUTERS = (
    "sportsassets.api.agents_core",
    "sportsassets.api.agents_derek",
    "sportsassets.api.agents_xavier",
    "sportsassets.api.agents_audrey",
    "sportsassets.api.agents_chat",
    "sportsassets.api.agent_pages",
)

DSN = os.environ.get("RN1X_TEST_DSN", "")
NODE = shutil.which("node")
STATUSES = ("OK", "EMPTY", "UNAVAILABLE")


class _Cfg:
    admin_token = "admin-secret-for-the-runtime-pages-test"
    desk_password = "desk"
    operator_password = "operator"
    command_read_password = "desk"
    funded_resolution_key = ""
    funded_resolution_operator = ""


def _require():
    missing = []
    for mod in REQUIRES_AGENT_ROUTERS:
        try:
            importlib.import_module(mod)
        except ImportError as exc:
            missing.append("%s (%s)" % (mod, exc))
    if missing:
        pytest.fail("REQUIRES_AGENT_ROUTERS: these endpoint modules are not in this "
                    "checkout: %s. This proof runs after the five agent streams are "
                    "merged; the integrator adds it to the critical list then."
                    % "; ".join(missing))
    if not DSN:
        pytest.fail("RN1X_TEST_DSN is not set: this proof reads the real database")
    if not NODE:
        pytest.fail("node is required: the page's own render code is executed")
    from sportsassets.api import agent_pages as P
    from sportsassets.api import app as A
    paths = set(_route_paths(A.app.routes))
    needed = [P.ENDPOINTS[k] for k in ("index", "derek", "xavier", "audrey")] + list(P.PAGE_PATHS.values())
    absent = [p for p in needed if p not in paths]
    if absent:
        pytest.fail("api/app.py does not include these routes: %s (include each agent "
                    "router and agent_pages.router)" % absent)
    # THE PAGE, NOT AN AGENT ROUTER'S /{id}, ANSWERS EACH PAGE PATH: the first
    # route the application itself matches is agent_pages' router.
    captured = {p: _first_match(A.app, p) for p in P.PAGE_PATHS.values()}
    wrong = {p: m for p, m in captured.items() if m is not P.router}
    if wrong:
        pytest.fail("these page paths are answered by another router first: %s" % wrong)
    return P, A


def _route_paths(routes, prefix=""):
    """Every path the application serves. FastAPI >= 0.140 keeps an included
    router as one wrapper route (`original_router` + `include_context`), so
    its paths are read from the router it wraps."""
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            ctx = getattr(r, "include_context", None)
            yield from _route_paths(inner.routes, prefix + (getattr(ctx, "prefix", "") or ""))
        elif getattr(r, "path", None) is not None:
            yield prefix + r.path


def _first_match(app, path):
    from starlette.routing import Match
    scope = {"type": "http", "path": path, "method": "GET", "root_path": ""}
    for r in app.routes:
        if r.matches(scope)[0] == Match.FULL:
            return getattr(r, "original_router", r)
    return None


def _node(P, kind, body):
    script = (P.render_js(kind) + "\n;(async function(){ var __out = await (async function(){\n"
              + body + "\n})(); process.stdout.write(JSON.stringify(__out)); })()"
              ".catch(function(e){ console.error(e && e.stack || e); process.exit(3); });")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


async def _client(monkeypatch, A):
    import httpx
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    monkeypatch.setenv("DATABASE_URL", DSN)
    from sportsassets import config
    config.settings.cache_clear()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=A.app),
                             base_url="http://testserver",
                             headers={"X-Admin-Token": _Cfg.admin_token})


async def _close_pool():
    from sportsassets.db import close_pool
    await close_pool()


@pytest.mark.parametrize("kind", ["derek", "xavier", "audrey"])
async def test_each_workspace_draws_the_real_endpoint_json(monkeypatch, kind):
    P, A = _require()
    c = await _client(monkeypatch, A)
    try:
        r = await c.get(P.ENDPOINTS[kind])
        assert r.status_code == 200, (kind, r.status_code, r.text[:400])
        j = r.json()
        page = await c.get(P.PAGE_PATHS[kind])
    finally:
        await c.aclose()
        await _close_pool()
    # the contract
    assert j["read_only"] is True and isinstance(j["read_at"], (int, float))
    assert isinstance(j.get("agent"), dict)
    for key in P.REQUIRED_SECTIONS[kind]:
        s = j["sections"][key]
        assert s["status"] in STATUSES, (kind, key, s)
        if s["status"] != "OK":
            assert s["why"], (kind, key, "an EMPTY or UNAVAILABLE section must name its reason")
        for ev in s.get("evidence") or []:
            assert ev.get("href", "/").startswith("/"), (kind, key, ev)
    # the page is served, and its own code draws every required section of the real JSON
    assert page.status_code == 200 and 'data-kind="%s"' % kind in page.text
    html = _node(P, kind, "return AG.workspace(%r, %s);" % (kind, json.dumps(j)))
    for key in P.REQUIRED_SECTIONS[kind]:
        m = re.search(r'id="s-%s" data-section="%s" data-status="(\w+)"' % (key, key), html)
        assert m, (kind, key)
        assert m.group(1) == j["sections"][key]["status"], (kind, key)
    # a shape the specialised renderer could not read is a contract mismatch to fix
    assert "has a shape the specialised view could not read" not in html


async def test_the_index_and_the_trace_draw_the_real_records(monkeypatch):
    P, A = _require()
    # The service registers the three identities when its writer starts; an
    # earlier test may have emptied the tables, so register them as it would.
    import asyncpg
    from sportsassets.agents import registry as R
    conn = await asyncpg.connect(DSN)
    try:
        await R.ensure_identities(conn)
    finally:
        await conn.close()
    c = await _client(monkeypatch, A)
    try:
        outs = {}
        for k in ("index", "derek", "xavier", "audrey"):
            r = await c.get(P.ENDPOINTS[k])
            assert r.status_code == 200, (k, r.status_code, r.text[:400])
            outs[k] = {"kind": "OK", "status": 200, "json": r.json()}
        page = await c.get(P.PAGE_PATHS["index"])
        demo = await c.get(P.PAGE_PATHS["demo"])
    finally:
        await c.aclose()
        await _close_pool()
    ix = outs["index"]["json"]
    assert isinstance(ix.get("agents"), list)
    ids = {str(a.get("agent_id", "")).upper() for a in ix["agents"]}
    assert {"DEREK", "XAVIER", "AUDREY"} <= ids, ids
    for a in ix["agents"]:
        # a named state, or none with the reason (never a borrowed success)
        assert a.get("state") in P.AGENT_STATES or (
            a.get("state") is None and a.get("why")), a
    html = _node(P, "index", "return AG.index(%s);" % json.dumps(ix))
    for a in ("DEREK", "XAVIER", "AUDREY"):
        assert 'data-agent="%s"' % a in html
    assert "The index returned no identity or status row" not in html
    trace = _node(P, "index", "return AG.trace.render(%s, null);" % json.dumps(outs))
    assert "Reads" in trace
    assert page.status_code == 200 and demo.status_code == 200
    assert P.REHEARSAL_LABEL in demo.text


async def test_an_empty_book_is_drawn_as_empty_not_as_management(monkeypatch):
    """On a database with no funded position Xavier's workspace says so plainly."""
    P, A = _require()
    c = await _client(monkeypatch, A)
    try:
        j = (await c.get(P.ENDPOINTS["xavier"])).json()
    finally:
        await c.aclose()
        await _close_pool()
    pos = j["sections"]["positions"]
    html = _node(P, "xavier", "return AG.workspace('xavier', %s);" % json.dumps(j))
    if pos["status"] == "EMPTY":
        assert "No position is owned by Xavier." in html
        assert "An empty pass is not management" in html
    else:
        assert pos["status"] in ("OK", "UNAVAILABLE")
