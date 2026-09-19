"""THE COMMAND SHADOW CONTRACT: read-only, and honest about nothing.

Owner directive 2026-09-19: "COMMAND reads the actual append-only
shadow store. If there are zero decisions: SHOW ZERO. If listening has
started: SHOW LISTENING. If an engine is blocked: SHOW THE BLOCKER. If
data is stale: SHOW STALE. If a metric is unknown: SHOW NOT IDENTIFIED."

The most valuable test in this file is the one that proves a FAILED
READ does not become a page of zeros. That is the failure mode that
would actually mislead somebody: a dashboard confidently reporting no
activity during an outage looks exactly like a dashboard reporting a
quiet day.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from sportsassets import shadow as sh
from sportsassets import shadow_lanes as lanes
from sportsassets.api import app as APP
from sportsassets.api import command_shadow as CS

ROOT = pathlib.Path(__file__).resolve().parents[2]
FRONT = ROOT / "frontend" / "public" / "command"
SHADOW_JS = (FRONT / "shadow.js").read_text()
APP_JS = (FRONT / "app.js").read_text()
INDEX = (FRONT / "index.html").read_text()

ROUTES = ("/api/command/shadow/summary", "/api/command/shadow/decisions",
          "/api/command/shadow/positions", "/api/command/shadow/executions",
          "/api/command/shadow/equity", "/api/command/shadow/comparison",
          "/api/command/shadow/health")


@pytest.fixture()
def client():
    return TestClient(APP.app, raise_server_exceptions=False)


# ── access ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES + ("/api/command/shadow/trades/x",))
def test_every_shadow_route_requires_a_command_session(client, route):
    assert client.get(route).status_code == 401


def test_the_shadow_routes_are_all_reads(client):
    """No POST, PUT, PATCH or DELETE exists on any of them -- not
    refused at runtime, ABSENT. A route that does not exist cannot be
    reached by a bug in an auth check."""
    paths = {r.path: getattr(r, "methods", set()) for r in APP.app.routes
             if "/api/command/shadow" in getattr(r, "path", "")}
    assert paths, "the shadow routes are not registered"
    for path, methods in paths.items():
        assert methods <= {"GET", "HEAD"}, (path, methods)


# ── the failure that must never look like zero ───────────────────────


class _Boom:
    async def fetchrow(self, *a, **k):
        raise RuntimeError("connection reset")

    fetch = fetchval = fetchrow


@pytest.mark.anyio
async def test_an_unreadable_ledger_is_named_not_flattened():
    for fn in (CS.summary, CS.positions, CS.executions, CS.comparison,
               CS.equity, CS.health):
        with pytest.raises(CS.RetrievalIncomplete) as exc:
            await fn(_Boom())
        assert exc.value.reason, fn.__name__


def test_a_retrieval_failure_becomes_503_and_says_why():
    inc = CS.RetrievalIncomplete("SHADOW_COUNTS_UNREAD", "RuntimeError")
    http = APP._shadow_unavailable(inc)
    assert http.status_code == 503
    assert http.detail["reason"] == "SHADOW_COUNTS_UNREAD"
    assert "not zero" in http.detail["note"]


# ── the environment block ────────────────────────────────────────────


def test_the_disclosure_is_produced_server_side():
    env = CS.environment()
    assert env["disclosureLines"] == ["SHADOW TRADING", "NO REAL CAPITAL",
                                      "COUNTERFACTUAL / SIMULATED EXECUTION"]
    assert env["shadowMode"] is True
    assert env["realOrderSubmissionEnabled"] is False
    assert env["capitalAtRisk"] == 0
    assert env["mirrorLive"] is False
    assert env["realOrderActivity"] == "NONE"
    assert env["disclosure"] == sh.DISCLOSURE


def test_the_environment_carries_no_credential():
    env = CS.environment()
    blob = repr(env).lower()
    for forbidden in ("key", "token", "secret", "password", "client_id"):
        assert forbidden not in blob, forbidden


# ── no combined P&L, ever ────────────────────────────────────────────


def test_the_api_never_offers_a_combined_headline_number():
    src = (ROOT / "backend" / "sportsassets" / "api"
           / "command_shadow.py").read_text()
    assert '"combinedPnl": None' in src
    assert '"combined": None' in src
    # and the two lanes are named separately everywhere
    assert lanes.RN1_SHADOW in src and lanes.BETTOR_EV_SHADOW in src


def test_only_actual_fill_is_ever_marked_realizable():
    assert sh.REALIZABLE_CLASSES == frozenset({sh.ACTUAL_FILL})
    src = (ROOT / "backend" / "sportsassets" / "api"
           / "command_shadow.py").read_text()
    assert "REALIZABLE_CLASSES" in src


# ── the front end holds no fixture ───────────────────────────────────


def test_the_shadow_ui_has_no_demo_or_fixture_path():
    """"No invented activity. No synthetic P&L presented as shadow
    performance. No fake trades to make the screen impressive." The
    cheapest way to guarantee that is for no fixture to exist."""
    for forbidden in ("demoSnapshot", "ILLUSTRATIVE", "Math.random",
                      "FIXTURE", "sampleRows", "placeholderRows"):
        assert forbidden not in SHADOW_JS, forbidden


def test_the_shadow_ui_can_place_nothing():
    for forbidden in ("createOrder", "placeOrder", "submitOrder",
                      "cancelOrder", "method: 'POST'", 'method: "POST"',
                      "PRIVATE_KEY", "apiKey", "Authorization"):
        assert forbidden not in SHADOW_JS, forbidden


def test_the_shadow_ui_fetches_only_command_paths_through_the_guard():
    """core.endpoint() is what stops COMMAND being pointed at a third
    party. Every fetch in this module goes through it."""
    fetches = re.findall(r"fetch\(\s*([^,]+)", SHADOW_JS)
    assert fetches, "no fetch found — the check would pass vacuously"
    for target in fetches:
        assert "C.endpoint(" in target, target
    assert "'/api/command/shadow/'" in SHADOW_JS


def test_the_ui_distinguishes_zero_from_unreadable():
    assert "LISTENING" in SHADOW_JS
    assert "FEED UNAVAILABLE" in SHADOW_JS
    assert "NOT IDENTIFIED" in SHADOW_JS
    assert "not a reading of zero" in SHADOW_JS


def test_source_freshness_is_not_the_browser_clock():
    assert "LAST SOURCE UPDATE" in SHADOW_JS
    assert "LAST UI UPDATE" in SHADOW_JS
    assert "lastSourceTimestamp" in SHADOW_JS


def test_the_disclosure_is_rendered_on_every_tab():
    for tab in ("function overview", "function decisionsTab",
                "function positionsTab", "function executionTab",
                "function pairingTab", "function comparisonTab",
                "function auditTab", "function performanceTab"):
        start = SHADOW_JS.index(tab)
        body = SHADOW_JS[start:start + 1400]
        assert "disclosure(" in body, tab


# ── COMMAND's existing navigation stays intact ───────────────────────


def test_shadow_is_a_top_level_destination():
    assert "['shadow','Shadow']" in APP_JS
    assert "shadow:shadowPage" in APP_JS
    assert '<script src="shadow.js">' in INDEX
    assert 'href="shadow.css"' in INDEX


def test_every_pre_existing_command_view_is_still_navigable():
    for view in ("command", "portfolio", "orders", "decisions", "risk",
                 "models", "reports", "audit", "investor"):
        assert "['%s'," % view in APP_JS, view
        assert "%s:" % view in APP_JS or "%sPage" % view in APP_JS, view


def test_the_sidebar_sections_still_split_cleanly():
    """The sidebar slices the nav at 6. SHADOW was placed inside the
    OPERATIONS half deliberately, so neither section lost a member."""
    line = next(l for l in APP_JS.splitlines() if l.startswith("const nav="))
    ids = re.findall(r"\['([a-z]+)',", line)
    assert ids[:6] == ["command", "portfolio", "orders", "decisions",
                       "shadow", "risk"]
    assert ids[6:] == ["models", "reports", "audit", "investor"]


def test_the_shadow_module_is_loaded_before_app_js():
    def tag(name):
        # the SCRIPT TAG, not the first mention: the comment above the
        # tag names app.js, and matching prose would pass vacuously
        return INDEX.index('<script src="%s">' % name)
    assert tag("shadow.js") < tag("app.js")
    assert tag("core.js") < tag("shadow.js")


def test_the_shadow_view_stops_polling_when_it_is_left():
    assert "window.BTShadow?.stop()" in APP_JS
