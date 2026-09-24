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

    # THE PREFIX IS NOW BUILT, NOT A LITERAL. Three namespaces are
    # served -- shadow, desk and learning -- so this used to assert on
    # the string "'/api/command/shadow/'" and had gone stale: the
    # literal disappeared when the desk namespace was added, and the
    # test failed for a reason that had nothing to do with the safety
    # property it names. Assert the property instead.
    #
    # The property is that the namespace is a BOUNDED ALLOWLIST chosen
    # in this file, never a value derived from a response, a query
    # parameter or the URL -- because a namespace under caller control
    # would let the built path escape /api/command/.
    assert "'/api/command/' + (ns || 'shadow') + '/'" in SHADOW_JS
    callers = set(re.findall(r"return pull\([^,]+,\s*'([a-z]+)'",
                             SHADOW_JS))
    assert callers, "no namespaced caller found — vacuous"
    assert callers <= {"shadow", "desk", "learning"}, callers
    # And no caller may pass a namespace it computed.
    assert not re.search(r"return pull\([^,]+,\s*[^'\s)]", SHADOW_JS)


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


# ── THE RN1-SEEDED MANAGEMENT TAB ───────────────────────────────────
#
# The audit recorded "no published trace route" for this experiment, and
# the brief asked for a view showing "actual operation, decisions,
# results and remaining blockers". These check the four routes exist and
# that the page draws all four sections -- in particular that the
# BLOCKERS panel is not conditional on the experiment being empty.

RN1X_ROUTES = ("/api/command/rn1x/overview", "/api/command/rn1x/positions",
               "/api/command/rn1x/learning")


def test_the_rn1x_routes_are_registered():
    paths = {getattr(r, "path", "") for r in APP.app.routes}
    for route in RN1X_ROUTES:
        assert route in paths, route
    assert any(p.startswith("/api/command/rn1x/trace/") for p in paths), paths


def test_the_rn1x_routes_require_a_command_session(client):
    for route in RN1X_ROUTES:
        res = client.get(route)
        assert res.status_code in (401, 403), (route, res.status_code)


def test_rn1x_is_a_tab_and_has_a_renderer():
    assert "['rn1x', 'RN1 seeded management']" in SHADOW_JS
    assert "rn1x: rn1xTab," in SHADOW_JS
    assert "function rn1xTab()" in SHADOW_JS


def test_the_rn1x_cache_key_is_namespaced():
    """Three namespaces now serve a route called `overview`.

    An unkeyed cache would paint the desk's replay numbers under the
    rn1x heading -- one mode's figures beneath another mode's title,
    which is the exact error the mode separation exists to prevent.
    """
    assert "pullRn1x" in SHADOW_JS
    assert "return pull(path, 'rn1x', 'rn1x/' + path);" in SHADOW_JS
    for key in ("'rn1x/overview'", "'rn1x/learning'", "'rn1x/positions'"):
        assert key in SHADOW_JS, key


def test_the_rn1x_blockers_panel_is_not_conditional():
    """A screen that hides its blockers once rows arrive is how "the loss
    exit has never been available" stops being visible.

    The blockers section is built unconditionally and concatenated into
    every return, so it renders while the experiment is running and
    writing. This asserts over the source because that is where the
    property lives.
    """
    body = SHADOW_JS[SHADOW_JS.index("function rn1xTab()"):]
    body = body[:body.index("\n  const VIEW = {")]
    # built with no guard around it
    assert "const blockers = `<section" in body
    # and present in the full-render return
    tail = body[body.rindex("return "):]
    assert "blockers" in tail, tail
    # the empty-state branch must not be the ONLY place it appears
    assert body.count("blockers") >= 3, body.count("blockers")


def test_the_rn1x_tab_draws_all_four_required_sections():
    body = SHADOW_JS[SHADOW_JS.index("function rn1xTab()"):]
    body = body[:body.index("\n  const VIEW = {")]
    for needed in ("Is it operating?", "What is persisted",
                   "Decisions by operating state", "Remaining blockers",
                   "Learning cycle", "Seeded positions"):
        assert needed in body, needed


def test_the_rn1x_tab_never_prints_a_bare_zero_for_an_unknown():
    """`zeroOk` prints a real 0 and NOT IDENTIFIED for absent.

    The counts on this tab are genuinely zero for a long while, so they
    must use the formatter that can tell 0 from unknown rather than a
    raw interpolation that would render `undefined`.
    """
    body = SHADOW_JS[SHADOW_JS.index("function rn1xTab()"):]
    body = body[:body.index("\n  const VIEW = {")]
    for field in ("c.positions", "c.decisions", "c.orders", "c.fills",
                  "c.outcomes"):
        assert "zeroOk(%s)" % field in body, field


# ── ITEM 5: SEPARATED STATUSES, LIVE BADGE, CLICK PATH ──────────────

def test_the_statuses_route_is_registered_and_guarded(client):
    paths = {getattr(r, "path", "") for r in APP.app.routes}
    assert "/api/command/rn1x/statuses" in paths
    assert client.get("/api/command/rn1x/statuses").status_code in (401, 403)


def test_all_eight_statuses_are_declared_separately():
    """Seven, plus the EXTERNAL source as its own eighth.

    `independent_ev_entries` is the INTERNAL settlement-model path, which
    refuses for want of a qualified model. `external_valuation` is a
    bookmaker's de-vigged price -- a different source class, blocked for a
    different reason (its credential is not on this service). One badge
    over both would hide which of the two moved, which is the exact defect
    the separated statuses exist to prevent.
    """
    from sportsassets.api import command_rn1x as CR

    assert CR.STATUS_KEYS == (
        "historical_replay", "prospective_rn1_management",
        "independent_ev_entries", "pairing", "second_half_loss_exit",
        "accounting_health", "learning_evaluation", "external_valuation",
        # ORDER BOOK and P&L as their own tiles: resting size, partial
        # fills, cancellations and remaining inventory were reachable only
        # by clicking into one position's trace.
        "order_book_state", "shadow_pnl",
        # FITTING, separate from the COMPARATOR. `learning_evaluation`
        # re-runs policies and fits nothing; `model_fitting` is the
        # prepare/fit/predict/join/evaluate pipeline. One tile over both
        # would let a policy comparison read as model training.
        "model_fitting")
    # and the two belief paths are NOT the same key
    assert "independent_ev_entries" != "external_valuation"
    # nor are the two learning paths
    assert "learning_evaluation" != "model_fitting"


def test_the_live_badge_distinguishes_live_armed_and_stopped():
    """A LIVE badge must identify what is live.

    Three states, not two: "running and producing", "running and
    producing nothing yet" and "not running" are different claims.
    """
    from sportsassets.api import command_rn1x as CR

    live = CR._live(True, True, what="x", why="y")
    armed = CR._live(True, False, what="x", why="y")
    stopped = CR._live(False, False, what="x", why="y")
    assert live["badge"] == "LIVE" and live["live"] is True
    assert armed["badge"] == "ARMED" and armed["producing"] is False
    assert stopped["badge"] == "STOPPED" and stopped["live"] is False
    # every badge names its subject
    for b in (live, armed, stopped):
        assert b["what"] and b["why"]


def test_the_ui_renders_every_status_tile_and_the_click_path():
    body = SHADOW_JS[SHADOW_JS.index("function rn1xTab()"):]
    body = body[:body.index("\n  const VIEW = {")]
    for key in ("historical_replay", "prospective_rn1_management",
                "independent_ev_entries", "pairing",
                "second_half_loss_exit", "accounting_health",
                "learning_evaluation"):
        assert key in body, key
    # the click path's six stages
    for stage in ("Source event", "The three clocks", "Decisions",
                  "Orders", "Modelled fills", "Inventory and outcome"):
        assert stage in body, stage
    # and the row is clickable, with a handler that reads it
    assert "data-rn1x-pos" in SHADOW_JS
    assert "state.rn1xPosition" in SHADOW_JS
    assert "closest('[data-rn1x-pos]')" in SHADOW_JS


def test_the_learning_tile_cannot_describe_itself_as_fitting():
    """The screen renders the module's own words, so it cannot be
    more generous than the module."""
    from sportsassets import bettor_rn1x_learn as L

    assert L.DESCRIPTION["what_it_is"] == "A POLICY COMPARATOR"
    assert L.DESCRIPTION["fits"] == []
    assert L.DESCRIPTION["changes"] == []
    assert "NOTHING IS FITTED" in L.DESCRIPTION["fits_note"]
