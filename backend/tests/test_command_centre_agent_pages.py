"""PROOF: THE COMMAND CENTRE PAGES FOR DEREK, XAVIER AND AUDREY.

What this pins, against the real page router, the real `require_command`, the
pages' own JavaScript (run under node) and -- for the reads -- the real
database:

  * each page answers 200 to a COMMAND session (cookie) or the admin header,
    and the locked 401 page (no page code) to anybody else;
  * each page links the other two (persistent navigation);
  * the data module and the character module are SEPARATE: the data panels,
    the 2D fallback portrait and a <noscript> are in the HTML itself, the
    inline data script never imports the character, and the character module
    fetches no data;
  * the static route serves the vendored three.js build (and its licence and
    the character module) with the right content type and caching, from an
    allowlist, and the added weight stays under 1.5 MB;
  * reduced motion: CSS, the loader's check and the module's no-loop path;
  * the character's mode follows the status record, and a stale heartbeat is
    UNAVAILABLE;
  * Derek's history route paginates, searches on the server and returns the
    eight field groups; the combined estimate is read from the record, never
    recomputed; the owner's worked example renders exactly;
  * Xavier's standing orders are UNAVAILABLE by name when the records are not
    in this build; the payoff demonstration is computed by the backend's own
    whole-position code, fully and partially hedged, labelled DEMONSTRATION;
  * Audrey's chat shows FALLBACK MODE (conversational mode pending) with no
    provider key, and never a simulated exchange.

node and RN1X_TEST_DSN are REQUIRED where used: their absence fails loudly
rather than skipping (a skipped proof is how an unrendered record ships).
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from sportsassets.api import agent_cc_page as CCP
from sportsassets.api import agent_pages as P
from sportsassets.api import agents_cc_reads as CR

NODE = shutil.which("node")
DSN = os.environ.get("RN1X_TEST_DSN", "")
KINDS = ("derek", "xavier", "audrey")
STATIC = Path(P.__file__).resolve().parents[1] / "assets" / "agents"
PREFIX = "cc-ui-test-"


class _Cfg:
    admin_token = "admin-secret-for-the-command-centre-pages-test"
    desk_password = "desk"
    operator_password = "operator"
    command_read_password = "desk"
    funded_resolution_key = ""
    funded_resolution_operator = ""
    session_epoch = "1"


def _app(monkeypatch):
    from fastapi import FastAPI
    from sportsassets.api import app as A
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    app = FastAPI()
    app.include_router(P.router)
    app.include_router(CR.router)
    return app, A


def _client(monkeypatch):
    from starlette.testclient import TestClient
    app, A = _app(monkeypatch)
    return TestClient(app, raise_server_exceptions=False), A


def _node(kind, body, timeout=30):
    if not NODE:
        pytest.fail("node is required: these tests execute the pages' own code")
    script = (P.render_js(kind) + "\n;(async function(){ var __out = await (async function(){\n"
              + body + "\n})(); process.stdout.write(JSON.stringify(__out)); })()"
              ".catch(function(e){ console.error(e && e.stack || e); process.exit(3); });")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, text=True, timeout=timeout)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _scripts(html):
    classic = re.findall(r"<script>(.*?)</script>", html, re.S)
    module = re.findall(r'<script type="module">(.*?)</script>', html, re.S)
    return classic, module


# ═════════════════════════════════════════════════════════════════════
# ACCESS, NAVIGATION, SEPARATION
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("kind", KINDS)
def test_each_page_is_200_to_a_command_session_and_401_without(monkeypatch, kind):
    c, A = _client(monkeypatch)
    path = P.PAGE_PATHS[kind]
    r = c.get(path)
    assert r.status_code == 401
    assert P.LOCKED_TEXT in r.text
    assert "cc_characters" not in r.text and "CC.onWorkspace" not in r.text
    r = c.get(path, headers={"X-Admin-Token": _Cfg.admin_token})
    assert r.status_code == 200 and 'data-kind="%s"' % kind in r.text
    token, _ = A.mint_desk_token()
    c.cookies.set("bt_command", token)
    r = c.get(path)
    assert r.status_code == 200 and 'data-kind="%s"' % kind in r.text
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    assert "script-src 'self' 'unsafe-inline'" in csp
    assert "no-store" in r.headers["cache-control"]
    assert _Cfg.admin_token not in r.text and token not in r.text


@pytest.mark.parametrize("kind", KINDS)
def test_each_page_links_the_other_two_and_names_every_role(monkeypatch, kind):
    c, _ = _client(monkeypatch)
    html = c.get(P.PAGE_PATHS[kind], headers={"X-Admin-Token": _Cfg.admin_token}).text
    nav = re.search(r'<nav class="cc-nav" aria-label="Agents">(.*?)</nav>', html, re.S).group(1)
    for other in KINDS:
        assert 'href="%s"' % P.PAGE_PATHS[other] in nav, other
        assert CCP.CC_META[other]["role"].replace("&", "&amp;") in nav
    assert 'href="%s" aria-current="page"' % P.PAGE_PATHS[kind] in nav
    assert "Discovery &amp; Entry" in nav and "Portfolio Management" in nav \
        and "Audit &amp; Intelligence" in nav
    # identified as an AI agent, in text, not only in the drawing
    assert "AI AGENT" in html and "is an AI agent" in html


@pytest.mark.parametrize("kind", KINDS)
def test_the_data_module_and_the_character_module_are_separate(monkeypatch, kind):
    c, _ = _client(monkeypatch)
    html = c.get(P.PAGE_PATHS[kind], headers={"X-Admin-Token": _Cfg.admin_token}).text
    classic, module = _scripts(html)
    data = [c for c in classic if "AG.workspace" in c]
    assert len(data) == 1 and len(module) == 1 and len(classic) == 2
    data, loader = data[0], module[0]
    # the data containers are in the HTML itself, whatever the canvas does
    assert 'id="cc-panels" data-cc-data' in html and 'id="app"' in html
    for pid, _t, _s, _c in CCP._PANELS[kind]:
        assert 'id="%s"' % pid in html, pid
    assert "<noscript>" in html and 'id="cc-fallback"' in html and "<svg" in html
    # the data script draws the records and never loads the character
    assert "AG.workspace" in data and "CC.onWorkspace" in data
    assert "cc_characters" not in data and "three.module" not in data
    # the loader is the only thing that imports it, after capability checks
    assert "import('%s')" % P.ENDPOINTS["characters"] in loader
    for probe in ("hardwareConcurrency", "saveData", "deviceMemory", "webgl",
                  "prefers-reduced-motion: reduce"):
        assert probe in loader, probe
    # and the character module reads no data of its own
    src = (STATIC / "cc_characters.js").read_text()
    assert "fetch(" not in src and "XMLHttpRequest" not in src
    assert "/api/" not in src
    assert "cc:mode" in src


def test_the_static_route_serves_the_vendored_three_js(monkeypatch):
    c, A = _client(monkeypatch)
    # nothing under /api/command answers an anonymous caller
    assert c.get("/api/command/agents/static/three.module.min.js").status_code == 401
    token, _ = A.mint_desk_token()
    c.cookies.set("bt_command", token)
    for name in ("three.module.min.js", "three.core.min.js", "cc_characters.js"):
        r = c.get("/api/command/agents/static/" + name)
        assert r.status_code == 200, name
        assert r.headers["content-type"].startswith("text/javascript"), name
        assert r.content == (STATIC / name).read_bytes()
        assert r.headers["x-content-type-options"] == "nosniff"
    r = c.get("/api/command/agents/static/three.module.min.js")
    assert "immutable" in r.headers["cache-control"]
    assert b"Three.js Authors" in r.content[:200] and b"SPDX-License-Identifier: MIT" in r.content[:200]
    assert b'from"./three.core.min.js"' in r.content
    lic = c.get("/api/command/agents/static/THREE_LICENSE.txt")
    assert lic.status_code == 200 and lic.headers["content-type"].startswith("text/plain")
    assert "The MIT License" in lic.text and "three.js authors" in lic.text
    for bad in ("app.py", "..%2Fapi%2Fapp.py", "bt_mark.png", "nothing.js"):
        assert c.get("/api/command/agents/static/" + bad).status_code == 404, bad
    total = sum((STATIC / n).stat().st_size for n in P.STATIC_FILES)
    assert total < 1_500_000, total
    assert P.THREE_VERSION == "0.185.1"


def test_reduced_motion_is_honoured_everywhere():
    html = P.page_html("derek")
    assert "@media (prefers-reduced-motion:reduce)" in html
    _c, module = _scripts(html)
    assert "reducedMotion: reduced" in module[0]
    src = (STATIC / "cc_characters.js").read_text()
    # no animation loop with reduced motion: one static frame per change
    assert "if (reduced || dead || raf" in src
    assert "if (reduced || S.paused) still();" in src


# ═════════════════════════════════════════════════════════════════════
# THE CHARACTER'S MODE FOLLOWS THE STATUS RECORD
# ═════════════════════════════════════════════════════════════════════

def test_the_mode_follows_agent_status_and_a_stale_heartbeat_is_unavailable():
    now = 1_790_000_000
    got = _node("derek", """
      var now = %d;
      function a(st, age, extra) { var x = {agent_id: 'DEREK', state: st, last_heartbeat_at: now - age, activity: 'x'}; for (var k in (extra || {})) x[k] = extra[k]; return x; }
      return [CC.modeOf(a('IDLE', 30), now), CC.modeOf(a('EVALUATING', 30), now), CC.modeOf(a('BLOCKED', 30), now),
              CC.modeOf(a('WAITING_FOR_EVIDENCE', 30), now), CC.modeOf(a('RECOVERING', 30), now),
              CC.modeOf(a('IDLE', 5000), now), CC.modeOf(a('FAILED', 30), now), CC.modeOf(null, now),
              CC.modeOf({agent_id: 'DEREK', state: null, why: 'NOT_REGISTERED'}, now),
              CC.modeOf(a('IDLE', 5000, {cadence: {review_interval_s: 3600}}), now),
              CC.modeOf(a('DECISION_RECORDED', 30), now)];
    """ % now)
    modes = [g["mode"] for g in got]
    assert modes == ["monitoring", "reviewing", "waiting", "waiting", "reviewing",
                     "unavailable", "unavailable", "unavailable", "unavailable",
                     "monitoring", "monitoring"]
    assert "heartbeat stale" in got[5]["why"]
    assert "FAILED" in got[6]["why"]


def test_an_unavailable_agent_gets_the_banner_and_no_gestures():
    html = P.page_html("xavier")
    assert 'data-cc-mode="unavailable"' in html          # until a read says otherwise
    assert CCP.UNAVAILABLE_BANNER in html and 'role="alert"' in html
    src = (STATIC / "cc_characters.js").read_text()
    assert "never animate an offline agent" in src
    assert "if (off) S.gesture = null;" in src
    # once the offline pose has settled the loop stops: no breathing, no drift
    assert "AN OFFLINE AGENT IS NOT ANIMATED" in src
    assert "if (S.mode === 'unavailable' && S.offSettled)" in src
    assert "const br = (instant || off) ? 0" in src


# ═════════════════════════════════════════════════════════════════════
# DEREK: THE FIELDS, THE COMBINED ESTIMATE, THE WORKED EXAMPLE
# ═════════════════════════════════════════════════════════════════════

def _worked_example(**over):
    d = {"decision_id": "DRK-WE-1", "fixture": "event:nyy-bos",
         "us_market_slug": "aec-mlb-nyy-bos", "side": "ORDER_INTENT_BUY_LONG",
         "decided_at": "2026-09-30T18:00:00+00:00",
         "policy_version": "DEREK_ENTRY_POLICY_V2",
         "pinnacle_p": 0.58, "pinnacle_at": "2026-09-30T17:59:58+00:00",
         "model_p": 0.60, "model_version": "entry-m1",
         "model_at": "2026-09-30T17:59:57+00:00", "blended_p": 0.59,
         "executable_price": 0.5, "qty": 2000, "gross_edge_pp": 0.09,
         "expected_gross_profit_usd": 180.0, "fees_usd": 14.0,
         "expected_net_profit_usd": 166.0, "expected_net_roi": 0.1637,
         "verdict": "ENTER", "checks": [
             {"check": "fixture_participants_date_side_period", "status": "PASS",
              "blocks": "POLICY", "detail": "LONG Yankees",
              "evidence": {"period": "FULL_GAME", "period_basis": "venue rules",
                           "payout_event": "Yankees"}}]}
    d.update(over)
    return d


def test_the_worked_example_renders_exactly_from_a_record():
    """$1,000 on Yankees at $0.50 is 2,000 contracts; internal 0.60, Pinnacle
    0.58, blended 0.59, gross edge 9 pp, expected profit before fees $180,
    expected return 18%."""
    intent = {"intent_id": "FI-WE-1", "account_id": "acct", "limit_price": 0.5,
              "quantity": 2000, "collateral_usd": 1000.0, "state": "FILLED",
              "created_at": "2026-09-30T18:00:01+00:00"}
    fills = {"filled_qty": 2000.0, "filled_notional": 1000.0, "fill_fees": 14.0,
             "fill_ids": ["f1"]}
    row = CR.normalise_row(decision=_worked_example(), intent=intent, fills=fills,
                           alternatives=[])
    assert set(CR.FIELD_GROUPS) <= set(row)
    assert row["purchase"]["quantity"] == 2000 and row["purchase"]["price"] == 0.5
    assert row["purchase"]["dollars_committed"] == 1000.0
    assert row["purchase"]["dollars_filled"] == 1000.0
    assert row["internal"]["probability"] == 0.60 and row["pinnacle"]["fair_probability"] == 0.58
    assert row["combined"]["value"] == 0.59 and row["combined"]["source"] == "blended_p"
    assert row["combined"]["policy_version"] == "DEREK_ENTRY_POLICY_V2"
    assert row["economics"]["gross_edge_pp"] == pytest.approx(9.0)
    assert row["economics"]["expected_gross_profit_usd"] == 180.0
    assert row["economics"]["expected_return_before_fees"] == pytest.approx(0.18)
    assert row["execution"]["average_price"] == 0.5 and row["execution"]["remaining_qty"] == 0
    html = _node("derek", "return CC.derekRow(%s, 1790000000);" % json.dumps(row))
    for s in ("2,000 contracts", "$1,000.00", "0.600", "0.580", "0.590",
              "+9.00 pp", "$180.00", "18.0%", "DEREK_ENTRY_POLICY_V2", "FULL_GAME"):
        assert s in html, s
    # three different quantities, three different presentations
    assert '<span class="qty-prob">0.590</span>' in html
    assert '<span class="qty-pp">+9.00 pp</span>' in html
    assert '<span class="qty-ret">18.0%</span>' in html


def test_the_combined_estimate_is_read_from_the_record_never_recomputed():
    # a recorded blend that is NOT (0.60 + 0.58) / 2: the page must show it
    row = CR.normalise_row(decision=_worked_example(blended_p=0.61), intent=None)
    assert row["combined"]["value"] == 0.61
    # the same, carried inside a check's evidence
    d = _worked_example()
    d.pop("blended_p")
    d["checks"] = d["checks"] + [{"check": "blended_min_gross_edge", "status": "PASS",
                                  "blocks": "POLICY", "detail": "9 pp >= 5 pp",
                                  "evidence": {"combined_p": 0.593,
                                               "combination": "BLENDED_AVERAGE"}}]
    row = CR.normalise_row(decision=d, intent=None)
    assert row["combined"]["value"] == 0.593
    assert row["combined"]["source"] == "checks[blended_min_gross_edge].evidence.combined_p"
    assert row["combined"]["combination_rule"] == "BLENDED_AVERAGE"
    # a V1 record carries none: "not recorded", with the reason, never a number
    d = _worked_example(policy_version="DEREK_ENTRY_POLICY_V1")
    d.pop("blended_p")
    row = CR.normalise_row(decision=d, intent=None)
    assert row["combined"]["value"] is None
    assert "carries no combined estimate" in row["not_recorded"]["combined"]["value"]
    assert "DEREK_ENTRY_POLICY_V1" in row["not_recorded"]["combined"]["value"]
    # a decision with no order commits nothing and says so
    assert row["purchase"]["dollars_committed"] is None
    assert row["not_recorded"]["purchase"]["dollars_committed"].startswith(
        "no entry order names this decision")
    html = _node("derek", "return CC.derekRow(%s, 1790000000);" % json.dumps(row))
    assert "not recorded" in html and "carries no combined estimate" in html


def test_an_order_with_no_derek_decision_is_listed_with_its_reasons():
    row = CR.normalise_row(decision=None, intent={
        "intent_id": "FI-X", "account_id": "acct", "limit_price": 0.4,
        "quantity": 10, "collateral_usd": 4.0, "state": "UNRESOLVED",
        "unresolved_reason": "lost acknowledgement", "us_market_slug": "m",
        "event_key": "e", "order_intent": "ORDER_INTENT_BUY_LONG"},
        fills={"filled_qty": None, "filled_notional": None, "fill_ids": []})
    assert row["execution"]["order_state"] == "UNRESOLVED"
    assert row["execution"]["remaining_is_live"] is True
    assert row["not_recorded"]["internal"]["probability"] == \
        "this order names no Derek entry decision"
    assert any(b["blocks"] == "ORDER" for b in row["explanation"]["blocking_condition"])


# ═════════════════════════════════════════════════════════════════════
# XAVIER: STANDING ORDERS AND THE PAYOFF DEMONSTRATION
# ═════════════════════════════════════════════════════════════════════

def test_the_payoff_demonstration_fully_hedged_is_the_owners_table():
    got = CR.payoff_demonstration(2000)
    rows = [(o["outcome"], o["payout_usd"], o["profit_before_fees_usd"])
            for o in got["outcomes"]]
    assert rows == [("Yankees win by 3+", 2000.0, 200.0),
                    ("Yankees win by 1-2", 4000.0, 2200.0),
                    ("Red Sox win", 2000.0, 200.0)]
    assert got["cost"]["total_before_fees_usd"] == 1800.0
    assert got["minimum_ordinary_settlement_profit_before_fees_usd"] == 200.0
    assert got["both_win_profit_before_fees_usd"] == 2200.0
    assert got["unpaired_qty"] == 0 and got["fully_protected"] is True
    assert got["production"] is False and "DEMONSTRATION" in got["label"]
    assert got["computed_by"] == "sportsassets.agents.xavier_ladder.group_table"
    assert got["is_a_settlement_payoff_floor_not_realised_cash"] is True
    assert [e["state"] for e in got["exceptional_settlement"]] == ["VOID"]
    html = _node("xavier", "return CC.payoff(%s).html;" % json.dumps(got))
    for s in ("$2,000.00", "$4,000.00", "+$200.00", "+$2,200.00", "DEMONSTRATION",
              "FULL (confirmed fills)", "not realised cash"):
        assert s in html, s


def test_a_partial_hedge_recomputes_the_whole_position():
    got = CR.payoff_demonstration(1000)
    rows = {o["outcome"]: o["profit_before_fees_usd"] for o in got["outcomes"]}
    assert rows == {"Yankees win by 3+": 600.0, "Yankees win by 1-2": 1600.0,
                    "Red Sox win": -400.0}
    assert got["minimum_ordinary_settlement_profit_before_fees_usd"] == -400.0
    assert got["unpaired_qty"] == 1000 and got["matched_qty"] == 1000
    assert got["fully_protected"] is False
    html = _node("xavier", "return CC.payoff(%s).html;" % json.dumps(got))
    assert "PARTIAL" in html and "FULL (confirmed fills)" not in html
    assert "−$400.00" in html


def test_standing_orders_absent_render_unavailable_and_alternatives_monitored():
    j = {"status": "UNAVAILABLE", "why": CR.STANDING_NOT_IN_BUILD,
         "detail": "these tables are absent here: bettor_standing_order_plans",
         "rule": "at most ONE live-or-potentially-live protective hedge order per group",
         "monitored_alternatives": [{"label": CR.MONITORED, "is_a_venue_order": False,
                                     "candidate_id": "RS+3.5", "expected_net_usd": 1.2,
                                     "href": "/api/command/agents/xavier/decisions/XD-1"}]}
    got = _node("xavier", "return CC.standing(%s);" % json.dumps(j))
    assert got["status"] == "UNAVAILABLE"
    assert "standing-order records not in this build" in got["html"]
    assert "MONITORED ALTERNATIVE" in got["html"] and "RS+3.5" in got["html"]
    assert 'this is not "no orders"' in got["html"]


def test_the_recommendation_says_execution_is_not_permitted_and_ticks_only_on_change():
    ws = {"read_at": 1_790_000_600, "sections": {
        "reviews": {"status": "OK", "data": {"current": [{
            "xavier_decision_id": "XD-2", "portfolio_group_id": "PG-1",
            "decided_at": 1_790_000_000, "next_review_at": 1_790_000_900,
            "chosen_action": "HOLD", "execution_eligibility": "NOT_DISPATCHED",
            "evidence": [{"kind": "bettor_xavier_decisions", "id": "XD-2",
                          "href": "/api/command/agents/xavier/decisions/XD-2"}]}]}},
        "alternatives": {"status": "OK", "data": [{"xavier_decision_id": "XD-2", "alternatives": [
            {"action": "HOLD", "expected_net_usd": 2.0, "worst_case_established_usd": -10.0,
             "increment_vs_hold_usd": 0.0},
            {"action": "ACQUIRE_INDIRECT_HEDGE", "candidate_id": "RS+2.5",
             "increment_vs_hold_usd": -0.5, "worst_case_established_usd": 1.0,
             "blocker": "EVIDENCE_EXPIRED"}]}]},
        "execution": {"status": "OK", "data": {"submission_switches": {
            "FUNDED_SUBMISSION_ENABLED": False}}}}}
    got = _node("xavier", """
      var ws = %s;
      var first = CC.recommendation(ws, null, ws.read_at);
      var same = CC.recommendation(ws, first.key, ws.read_at);
      var changed = CC.recommendation(ws, ['XD-1@1789999000'], ws.read_at);
      return [first.html, same.html, changed.html];
    """ % json.dumps(ws))
    assert "EXECUTION NOT PERMITTED" in got[0] and "funded submission is disabled" in got[0]
    assert "unchanged for 10 min" in got[1] and 'data-tick="UNCHANGED"' in got[1]
    assert 'data-tick="CHANGED"' in got[2]
    assert "EVIDENCE_EXPIRED" in got[0] and "no live game feed" in got[0]


# ═════════════════════════════════════════════════════════════════════
# AUDREY: THE CHAT'S PROVIDER STATE
# ═════════════════════════════════════════════════════════════════════

def test_the_chat_shows_fallback_mode_when_the_provider_key_is_absent(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AUDREY_PROVIDER", raising=False)
    c, _ = _client(monkeypatch)
    html = c.get(P.PAGE_PATHS["audrey"], headers={"X-Admin-Token": _Cfg.admin_token}).text
    assert 'data-provider="PENDING"' in html
    assert "FALLBACK MODE" in html and "CONVERSATIONAL MODE PENDING" in html
    assert "NO_ANTHROPIC_API_KEY" in html
    assert 'id="chat-form"' in html and "SCRIPTED EXCHANGE" not in html
    for q in CCP.SUGGESTED_QUESTIONS:
        assert q in html
    # with a key present the banner no longer claims fallback, and never shows it
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key-000")
    html = c.get(P.PAGE_PATHS["audrey"], headers={"X-Admin-Token": _Cfg.admin_token}).text
    assert "sk-test-not-a-real-key-000" not in html
    assert 'data-provider="PENDING"' not in html or "ANTHROPIC_SDK_NOT_INSTALLED" in html


def test_the_directive_tree_links_directives_tasks_candidates_and_trials():
    ws = {"read_at": 1_790_000_000, "sections": {
        "directives": {"status": "OK", "data": [{"directive_id": "D-1", "state": "CONFIRMED",
                                                "objective": "Prioritize reducing unpaired exposure."}]},
        "tasks": {"status": "OK", "data": [{"task_id": "T-1", "directive_id": "D-1",
                                            "assignee": "XAVIER", "status": "EVALUATING",
                                            "title": "reduce unpaired", "evidence": [
                                                {"kind": "agent_tasks", "id": "T-1",
                                                 "href": "/api/command/agents/tasks/T-1"}]}]},
        "candidates": {"status": "OK", "data": [{"candidate_id": "C-1", "task_id": "T-1",
                                                 "state": "EVALUATING"}]},
        "evaluations": {"status": "OK", "data": {"trials": [{"trial_id": "TR-1",
                                                             "candidate_id": "C-1",
                                                             "outcome": "PASSED"}]}},
        "releases": {"status": "EMPTY", "data": {"releases": []}, "why": "none"}}}
    got = _node("audrey", "return CC.directiveTree(%s, 1790000000);" % json.dumps(ws))
    assert got["status"] == "OK"
    for s in ("D-1", "T-1", "EVALUATING", "C-1", "PASSED", "not approved",
              "release: none", "Open improvement tasks: <b>1</b>"):
        assert s in got["html"], s


# ═════════════════════════════════════════════════════════════════════
# THE READS, AGAINST THE REAL DATABASE
# ═════════════════════════════════════════════════════════════════════

def _need_db():
    if not DSN:
        pytest.fail("RN1X_TEST_DSN is not set: these proofs read the real database")


async def _aclient(monkeypatch):
    import httpx
    from sportsassets import config
    app, _A = _app(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", DSN)
    config.settings.cache_clear()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://testserver")


async def _close_pool():
    from sportsassets.db import close_pool
    await close_pool()


async def _cleanup(conn):
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("DELETE FROM bettor_funded_fills WHERE fill_id LIKE $1", PREFIX + "%")
        await conn.execute("DELETE FROM bettor_funded_intents WHERE intent_id LIKE $1", PREFIX + "%")
        await conn.execute("DELETE FROM derek_entry_decisions WHERE decision_id LIKE $1", PREFIX + "%")


async def _seed(conn):
    """Three synthetic Derek decisions on one synthetic fixture, and their
    orders in several states. Inserted with triggers off (replica) because
    the ledgers are append-only and the test removes them afterwards."""
    await _cleanup(conn)
    live = int(await conn.fetchval(
        "SELECT count(*) FROM bettor_funded_intents "
        " WHERE bettor_funded_intent_is_live(state)"))
    ev = json.dumps({"period": "FULL_GAME", "period_basis": "synthetic", "payout_event": "home"})
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        for i, (verdict, refusal) in enumerate([("ENTER", None), ("ENTER", None),
                                                ("REFUSE", "BELOW_MIN_EDGE")]):
            await conn.execute(
                "INSERT INTO derek_entry_decisions (decision_id, fixture, us_market_slug, side, "
                " decided_at, policy_version, pinnacle_p, pinnacle_at, model_p, model_version, "
                " model_at, executable_price, qty, gross_edge_pp, expected_gross_profit_usd, "
                " fees_usd, expected_net_profit_usd, expected_net_roi, checks, verdict, refusal, "
                " evidence, decided_by) VALUES ($1, $2, $3, 'ORDER_INTENT_BUY_LONG', "
                " now() - make_interval(secs => $4), 'DEREK_ENTRY_POLICY_V1', 0.58, now(), 0.6, "
                " 'm1', now(), 0.5, 100, 0.09, 9.0, 0.7, 8.3, 0.16, $5::jsonb, $6, $7, "
                " '{}'::jsonb, 'AFTER_CYCLE')",
                "%sD%d" % (PREFIX, i), PREFIX + "fixture", "%smarket-%d" % (PREFIX, i),
                float(10 * (i + 1)),
                json.dumps([{"check": "fixture_participants_date_side_period",
                             "status": "PASS", "blocks": "POLICY", "detail": "ok",
                             "evidence": json.loads(ev)}]), verdict, refusal)
        states = [("FILLED", 0), ("CANCELLED", 1), ("REJECTED", 2)]
        if live == 0:
            states.append(("PARTIALLY_FILLED", 0))
        for j, (state, di) in enumerate(states):
            iid = "%sI%d" % (PREFIX, j)
            await conn.execute(
                "INSERT INTO bettor_funded_intents (intent_id, account_id, venue, venue_class, "
                " us_market_slug, event_key, order_intent, limit_price, quantity, "
                " collateral_usd, effective_digest, decision_ref, state, kind, "
                " portfolio_group_id, leg_role, venue_order_id) VALUES ($1, 'cc-acct', "
                " 'pmus', 'FUNDED', $2, $3, 'ORDER_INTENT_BUY_LONG', 0.5, 100, 50, 'd', "
                " $4::jsonb, $5, 'ENTRY', $6, $7, $8)",
                iid, "%smarket-%d" % (PREFIX, di), PREFIX + "event",
                json.dumps({"derek_decision_id": "%sD%d" % (PREFIX, di)}), state,
                PREFIX + "group" if state == "PARTIALLY_FILLED" else None,
                "PRIMARY" if state == "PARTIALLY_FILLED" else None, PREFIX + "vo-%d" % j)
            qty = {"FILLED": 100, "PARTIALLY_FILLED": 40, "CANCELLED": 30}.get(state)
            if qty:
                await conn.execute(
                    "INSERT INTO bettor_funded_fills (fill_id, intent_id, venue_order_id, "
                    " venue_fill_id, at, qty, price, cash_usd, fee_usd, fee_basis, direction) "
                    " VALUES ($1, $2, $3, $4, now(), $5, 0.48, $6, 0.1, 'synthetic', 'ENTRY')",
                    "%sF%d" % (PREFIX, j), iid, PREFIX + "vo-%d" % j, "vf-%d" % j, qty,
                    -0.48 * qty)
    return live == 0


async def test_derek_history_route_paginates_searches_and_returns_the_fields(monkeypatch):
    _need_db()
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        partial_seeded = await _seed(conn)
        c = await _aclient(monkeypatch)
        h = {"X-Admin-Token": _Cfg.admin_token}
        try:
            assert (await c.get(CR.DEREK_ORDERS)).status_code == 401
            r = await c.get(CR.DEREK_ORDERS, params={"view": "decisions", "q": PREFIX,
                                                     "page_size": 2}, headers=h)
            assert r.status_code == 200, r.text[:400]
            j = r.json()
            assert j["read_only"] is True and j["status"] == "OK"
            assert j["total"] == 3 and j["pages"] == 2 and len(j["rows"]) == 2
            for row in j["rows"]:
                for g, fields in CR.FIELD_GROUPS.items():
                    assert g in row, g
                    for f in fields:
                        assert f in row[g] or f in row["not_recorded"].get(g, {}), (g, f)
                assert row["evidence"] and row["evidence"][0]["href"].startswith("/api/command/")
                # the shared label: no catalogue row for a synthetic market,
                # so a named warning -- and never the raw slug as the label
                assert row["label"]["primary"] != row["instrument"]["market"]
                assert "METADATA_INCOMPLETE: no catalogue row for this market" in \
                    row["label"]["warnings"]
            # newest first; the second page holds the oldest
            p2 = (await c.get(CR.DEREK_ORDERS, params={"view": "decisions", "q": PREFIX,
                                                       "page_size": 2, "page": 2},
                              headers=h)).json()
            assert [x["record"]["decision_id"] for x in p2["rows"]] == [PREFIX + "D2"]
            # alternatives considered: the other decisions on the same fixture
            alts = j["rows"][0]["explanation"]["alternatives_considered"]
            assert {a["decision_id"] for a in alts} <= {PREFIX + "D0", PREFIX + "D1", PREFIX + "D2"}
            assert len(alts) == 2
            # server-side search narrows to one record
            one = (await c.get(CR.DEREK_ORDERS, params={"view": "decisions",
                                                        "q": PREFIX + "market-1"},
                               headers=h)).json()
            assert one["total"] == 1 and one["rows"][0]["record"]["decision_id"] == PREFIX + "D1"
            refused = (await c.get(CR.DEREK_ORDERS, params={"view": "decisions", "q": PREFIX,
                                                            "verdict": "REFUSE"},
                                   headers=h)).json()
            assert refused["total"] == 1
            assert refused["rows"][0]["explanation"]["blocking_condition"][0]["refusal"] == \
                "BELOW_MIN_EDGE"
            # orders by state, each with its execution fields
            filled = (await c.get(CR.DEREK_ORDERS, params={"view": "orders", "q": PREFIX,
                                                           "state": "filled"},
                                  headers=h)).json()
            assert filled["total"] == 1
            ex = filled["rows"][0]["execution"]
            assert ex["order_state"] == "FILLED" and ex["filled_qty"] == 100
            assert ex["average_price"] == pytest.approx(0.48) and ex["remaining_qty"] == 0
            assert filled["rows"][0]["purchase"]["dollars_committed"] == 50.0
            assert filled["rows"][0]["purchase"]["dollars_filled"] == pytest.approx(48.0)
            assert filled["rows"][0]["record"]["decision_id"] == PREFIX + "D0"
            canc = (await c.get(CR.DEREK_ORDERS, params={"view": "orders", "q": PREFIX,
                                                         "state": "cancelled"},
                                headers=h)).json()
            assert canc["rows"][0]["execution"]["filled_qty"] == 30
            assert canc["rows"][0]["execution"]["remaining_is_live"] is False
            rej = (await c.get(CR.DEREK_ORDERS, params={"view": "orders", "q": PREFIX,
                                                        "state": "rejected"},
                               headers=h)).json()
            assert rej["total"] == 1 and rej["rows"][0]["execution"]["order_state"] == "REJECTED"
            allo = (await c.get(CR.DEREK_ORDERS, params={"view": "orders", "q": PREFIX},
                                headers=h)).json()
            assert allo["total"] == (4 if partial_seeded else 3)
            assert allo["counts"]["orders_by_state"]["FILLED"] >= 1
            if partial_seeded:
                part = (await c.get(CR.DEREK_ORDERS, params={"view": "orders", "q": PREFIX,
                                                             "state": "partial"},
                                    headers=h)).json()
                assert part["rows"][0]["execution"]["remaining_qty"] == 60
                assert part["rows"][0]["execution"]["remaining_is_live"] is True
            opp = (await c.get(CR.DEREK_ORDERS, params={"view": "opportunities", "q": PREFIX},
                               headers=h)).json()
            assert opp["total"] == 2 and all(r["explanation"]["verdict"] == "ENTER"
                                             for r in opp["rows"])
            none = (await c.get(CR.DEREK_ORDERS, params={"view": "decisions",
                                                         "q": PREFIX + "no-such-thing"},
                                headers=h)).json()
            assert none["status"] == "EMPTY" and none["why"]
            assert (await c.get(CR.DEREK_ORDERS, params={"view": "bogus"},
                                headers=h)).status_code == 422
        finally:
            await c.aclose()
            await _close_pool()
    finally:
        try:
            await _cleanup(conn)
            left = await conn.fetchval(
                "SELECT (SELECT count(*) FROM derek_entry_decisions WHERE decision_id LIKE $1)"
                " + (SELECT count(*) FROM bettor_funded_intents WHERE intent_id LIKE $1)"
                " + (SELECT count(*) FROM bettor_funded_fills WHERE fill_id LIKE $1)",
                PREFIX + "%")
            assert left == 0
        finally:
            await conn.close()


async def test_xavier_standing_orders_are_unavailable_when_the_records_are_absent(monkeypatch):
    _need_db()
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        present = [t for t in CR.STANDING_TABLES
                   if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t)]
    finally:
        await conn.close()
    c = await _aclient(monkeypatch)
    try:
        assert (await c.get(CR.XAVIER_STANDING)).status_code == 401
        r = await c.get(CR.XAVIER_STANDING, headers={"X-Admin-Token": _Cfg.admin_token})
        assert r.status_code == 200, r.text[:400]
        j = r.json()
    finally:
        await c.aclose()
        await _close_pool()
    assert isinstance(j["monitored_alternatives"], list)
    assert all(m["label"] == "MONITORED ALTERNATIVE" and m["is_a_venue_order"] is False
               for m in j["monitored_alternatives"])
    if len(present) < len(CR.STANDING_TABLES):
        # THIS BUILD: migration 157 is not merged, so the section says so
        assert j["status"] == "UNAVAILABLE"
        assert j["why"] == "standing-order records not in this build"
        assert j["groups"] == []
    else:
        assert j["status"] in ("OK", "EMPTY") and (j["status"] == "OK" or j["why"])
        for g in j["groups"]:
            assert g["invariant_holds"] is True


async def test_the_payoff_and_performance_routes_answer_read_only(monkeypatch):
    _need_db()
    c = await _aclient(monkeypatch)
    h = {"X-Admin-Token": _Cfg.admin_token}
    try:
        assert (await c.get(CR.XAVIER_PAYOFF)).status_code == 401
        p = (await c.get(CR.XAVIER_PAYOFF, params={"hedge_filled": 1000}, headers=h)).json()
        assert p["unpaired_qty"] == 1000 and p["production"] is False
        assert (await c.get(CR.AUDREY_PERFORMANCE)).status_code == 401
        r = await c.get(CR.AUDREY_PERFORMANCE, headers=h)
        assert r.status_code == 200, r.text[:400]
        j = r.json()
    finally:
        await c.aclose()
        await _close_pool()
    assert j["read_only"] is True and "ONE company P&L" in j["authority"]
    assert j["status"] in ("OK", "EMPTY", "UNAVAILABLE")
    if j["status"] != "OK":
        assert j["why"]
    for b in j.get("funded_books") or []:
        assert b["book_class"] == "FUNDED_REAL_MONEY"
        assert set(b["periods"]) == {"daily", "weekly", "monthly", "cumulative"}
    for b in j.get("demonstration_books") or []:
        assert b["label"] == "DEMONSTRATION"


def test_periods_come_from_the_books_own_realised_curve():
    now = 1_790_000_000.0
    curve = [{"at": now - 3600, "net_usd": 5.0}, {"at": now - 3 * 86400, "net_usd": -2.0},
             {"at": now - 20 * 86400, "net_usd": 1.5}, {"at": now - 90 * 86400, "net_usd": 10.0}]
    got = CR.periods_from_curve(curve, now=now)
    assert got["daily"]["realised_usd"] == 5.0
    assert got["weekly"]["realised_usd"] == 3.0
    assert got["monthly"]["realised_usd"] == 4.5
    assert got["cumulative"]["realised_usd"] == 14.5 and got["cumulative"]["booked_results"] == 4


# ═════════════════════════════════════════════════════════════════════
# MANAGEMENT-FRIENDLY LABELS: ONE SHARED SERVER RESOLVER
# ═════════════════════════════════════════════════════════════════════

from sportsassets import market_labels as ML  # noqa: E402


def _cat(slug, sides, **common):
    base = dict(identifier=slug, event_slug=slug, market_slug=slug, kind="side",
                line="", signed="", game_start="2026-10-02T23:05:00+00:00")
    base.update(common)
    return [dict(base, **s) for s in sides]


MLB = dict(event_title="Yankees vs. Red Sox", question="Yankees vs. Red Sox",
           team_league="mlb", sports_type="baseball_team_full_game_moneyline")


def test_label_moneyline_with_size_line():
    rows = _cat("aec-mlb-nyy-bos-2026-10-02", [
        {"side_norm": "yankees", "intent": ML.LONG, "team_name": "yankees", "team_abbr": "nyy", "team_id": 147},
        {"side_norm": "red sox", "intent": ML.SHORT, "team_name": "red sox", "team_abbr": "bos", "team_id": 111}], **MLB)
    got = ML.resolve(rows, market_slug="aec-mlb-nyy-bos-2026-10-02", intent=ML.LONG,
                     invested=1000, avg_price=0.5, contracts=2000)
    assert got["primary"] == "Yankees Moneyline"
    assert got["secondary"] == "Yankees vs. Red Sox · MLB · Full Game · Oct 2, 2026"
    assert got["size"] == "$1,000 invested · 50¢ average price · 2,000 contracts"
    assert got["complete"] is True and got["warnings"] == []
    assert got["badge"]["logo"] is None and got["badge"]["initials"] == "NYY"
    assert "not bundled" in got["badge"]["note"]
    assert got["technical"]["market_slug"] == "aec-mlb-nyy-bos-2026-10-02"
    # the SHORT intent buys the other side, whose own row names its team
    assert ML.resolve(rows, market_slug="aec-mlb-nyy-bos-2026-10-02",
                      intent=ML.SHORT)["primary"] == "Red Sox Moneyline"


def test_label_run_line_and_point_spread():
    rl = _cat("asc-mlb-nyy-bos-2026-10-02-rl", [
        {"side_norm": "red sox 2 5", "signed": "+2.5", "intent": ML.LONG, "team_name": "red sox"},
        {"side_norm": "yankees 2 5", "signed": "-2.5", "intent": ML.SHORT, "team_name": "yankees"}],
        **dict(MLB, sports_type="baseball_team_full_game_spread"))
    assert ML.resolve(rl, market_slug="asc-mlb-nyy-bos-2026-10-02-rl",
                      intent=ML.LONG)["primary"] == "Red Sox +2.5 Runs"
    assert ML.resolve(rl, market_slug="asc-mlb-nyy-bos-2026-10-02-rl",
                      intent=ML.SHORT)["primary"] == "Yankees -2.5 Runs"
    fb = _cat("asc-nfl-chi-car-2026-10-04", [
        {"side_norm": "panthers 7 5", "signed": "+7.5", "intent": ML.LONG, "team_name": "panthers"},
        {"side_norm": "bears 7 5", "signed": "-7.5", "intent": ML.SHORT, "team_name": "bears"}],
        event_title="Bears vs. Panthers", team_league="nfl", sports_type="football_team_full_game_spread")
    got = ML.resolve(fb, market_slug="asc-nfl-chi-car-2026-10-04", intent=ML.LONG)
    assert got["primary"] == "Panthers +7.5 Points"
    assert got["secondary"].startswith("Panthers vs. Bears · NFL · Full Game")


def test_label_a_no_position_is_never_the_yes_side():
    yn = _cat("atc-mlb-nyy-bos-2026-10-02-nyy", [
        {"side_norm": "yes", "intent": ML.LONG}, {"side_norm": "no", "intent": ML.SHORT}],
        question="Will the Yankees win?", event_title="Yankees vs. Red Sox",
        sports_type="baseball_team_full_game_moneyline")
    no = ML.resolve(yn, market_slug="atc-mlb-nyy-bos-2026-10-02-nyy", intent=ML.SHORT)
    yes = ML.resolve(yn, market_slug="atc-mlb-nyy-bos-2026-10-02-nyy", intent=ML.LONG)
    assert no["side"]["is_no"] is True and no["primary"].startswith("NO — ")
    assert yes["side"]["is_no"] is False and yes["primary"].startswith("YES — ")
    assert no["primary"] != yes["primary"]
    # a one-row proposition bought SHORT is its NO side too
    one = ML.resolve(yn[:1], market_slug="atc-mlb-nyy-bos-2026-10-02-nyy", intent=ML.SHORT)
    assert one["side"]["is_no"] is True and one["primary"].startswith("NO — ")


def test_label_first_five_and_map_markets_are_distinguished():
    f5 = _cat("aec-mlb-nyy-bos-2026-10-02-f5", [
        {"side_norm": "yankees", "intent": ML.LONG, "team_name": "yankees"},
        {"side_norm": "red sox", "intent": ML.SHORT, "team_name": "red sox"}],
        **dict(MLB, sports_type="baseball_team_first_five_innings_moneyline"))
    got = ML.resolve(f5, market_slug="aec-mlb-nyy-bos-2026-10-02-f5", intent=ML.LONG)
    assert got["period"] == "FIRST_FIVE" and "First 5 Innings" in got["secondary"]
    mp = _cat("astatc-cs2-ice-nemi-2026-09-09-map1", [
        {"side_norm": "ice", "intent": ML.LONG, "team_name": "ice"},
        {"side_norm": "nemiga", "intent": ML.SHORT, "team_name": "nemiga"}],
        event_title="ICE vs Nemiga", question="Map 1 Winner: ICE vs Nemiga",
        sports_type="esports_map_winner")
    got = ML.resolve(mp, market_slug="astatc-cs2-ice-nemi-2026-09-09-map1", intent=ML.LONG)
    assert got["period"] == "MAP_1" and got["primary"] == "Ice Map Winner"
    assert "Map 1" in got["secondary"] and "Esports" in got["secondary"]
    assert got["primary"] != "astatc-cs2-ice-nemi-2026-09-09-map1"


def test_label_incomplete_metadata_is_named_never_invented_never_hidden():
    slug = "astatc-cs2-ice-nemi-2026-09-09-map1"
    got = ML.resolve([], market_slug=slug, intent=ML.LONG, invested=10,
                     avg_price=0.42, contracts=24)
    assert got["complete"] is False and ML.W_NO_ROW in got["warnings"]
    assert got["primary"] and got["primary"] != slug
    assert got.get("team") is None
    assert got["size"] == "$10 invested · 42¢ average price · 24 contracts"
    partial = _cat("aec-x-y-2026-10-02", [{"side_norm": "", "intent": ML.LONG}],
                   question="Winner of X v Y", event_title="X v Y")
    got = ML.resolve(partial, market_slug="aec-x-y-2026-10-02", intent=ML.LONG)
    assert "METADATA_INCOMPLETE: team not resolved" in got["warnings"]
    assert got["primary"] == "Winner of X v Y"          # the venue's own text
    assert ML.text_label(got).startswith("Winner of X v Y")


def test_no_raw_slug_is_ever_a_primary_label_on_the_pages():
    slug = "astatc-cs2-ice-nemi-2026-09-09-map1"
    lbl = ML.resolve([], market_slug=slug, intent=ML.LONG)
    row = CR.normalise_row(decision=_worked_example(us_market_slug=slug), intent=None,
                           label=lbl)
    derek = _node("derek", "return CC.derekRow(%s, 1790000000);" % json.dumps(row))
    ws = {"read_at": 1_790_000_000, "sections": {
        "positions": {"status": "OK", "data": {"groups": [{
            "group": "PG-1", "primary_qty": 10, "hedge_qty": 0, "matched_qty": 0,
            "residual_unpaired_qty": 10, "book": {},
            "legs": [{"intent_id": "FI-1", "leg_role": "PRIMARY", "us_market_slug": slug}]}],
            "handoffs": [], "current_exposure": {}}},
        "reviews": {"status": "EMPTY", "why": "none", "data": None}}}
    xav = _node("xavier", "return CC.positionCards(%s, 1790000000).html;" % json.dumps(ws))
    for html in (derek, xav):
        primaries = re.findall(r'data-primary-label>(.*?)</div>', html, re.S)
        assert primaries, "no primary label rendered"
        for p in primaries:
            assert slug not in p, p
        # the raw identifier is still there, under Technical details, copyable
        tech = re.findall(r'<details class="tech">(.*?)</details>', html, re.S)
        assert any(slug in t and 'data-copy="%s"' % slug in t for t in tech)
    assert ML.W_NO_ROW in derek


async def test_the_labels_route_reads_the_venue_catalogue(monkeypatch):
    _need_db()
    import asyncpg
    slug = PREFIX + "aec-mlb-nyy-bos-2026-10-02"
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DELETE FROM us_premap WHERE market_slug = $1", slug)
        for side, intent, team, abbr in (("yankees", ML.LONG, "yankees", "nyy"),
                                         ("red sox", ML.SHORT, "red sox", "bos")):
            await conn.execute(
                "INSERT INTO us_premap (identifier, event_slug, event_title, market_slug, "
                " question, kind, line, side_norm, intent, signed, team_abbr, team_name, "
                " team_league, game_start, sports_type) VALUES ($1, $1, 'Yankees vs. Red Sox', "
                " $1, 'Yankees vs. Red Sox', 'side', '', $2, $3, '', $4, $5, 'mlb', "
                " '2026-10-02T23:05:00Z', 'baseball_team_full_game_moneyline')",
                slug, side, intent, abbr, team)
        c = await _aclient(monkeypatch)
        try:
            assert (await c.get(CR.LABELS, params={"item": "slug:%s|%s" % (slug, ML.LONG)})
                    ).status_code == 401
            r = await c.get(CR.LABELS, params=[("item", "slug:%s|%s" % (slug, ML.LONG)),
                                               ("item", "slug:%s|%s" % (slug, ML.SHORT)),
                                               ("item", "intent:no-such-intent")],
                            headers={"X-Admin-Token": _Cfg.admin_token})
            assert r.status_code == 200, r.text[:300]
            j = r.json()
        finally:
            await c.aclose()
            await _close_pool()
        assert j["labels"]["slug:%s|%s" % (slug, ML.LONG)]["primary"] == "Yankees Moneyline"
        assert j["labels"]["slug:%s|%s" % (slug, ML.SHORT)]["primary"] == "Red Sox Moneyline"
        assert j["unresolved_items"] == ["intent:no-such-intent"]
    finally:
        await conn.execute("DELETE FROM us_premap WHERE market_slug = $1", slug)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# FRAMED BY THE MANAGEMENT SHELL
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("kind", KINDS)
def test_pages_may_be_framed_by_the_same_origin_only(monkeypatch, kind):
    c, _ = _client(monkeypatch)
    r = c.get(P.PAGE_PATHS[kind], headers={"X-Admin-Token": _Cfg.admin_token})
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in csp
    # the model's Meshopt decoder is WebAssembly; JavaScript eval stays refused
    assert "'wasm-unsafe-eval'" in csp and "'unsafe-eval'" not in csp.replace("'wasm-unsafe-eval'", "")
    # embedded model textures decode through this document's blob: URLs; no remote origin
    assert "connect-src 'self' blob:;" in csp and "img-src 'self' data: blob:;" in csp
    assert "http" not in csp and "*" not in csp
    assert "x-frame-options" not in {k.lower() for k in r.headers}
    assert "DENY" not in csp
    html = r.text
    # the framed switch runs first and needs neither module
    assert html.index(CCP.FRAMED_JS.strip()[:40]) < html.index('class="top cc-top"')
    assert "window.top !== window.self" in CCP.FRAMED_JS
    assert "classList.add('cc-framed')" in CCP.FRAMED_JS
    assert "a.setAttribute('target', '_top')" in CCP.FRAMED_JS
    assert ".cc-framed .cc-top .brand,.cc-framed .cc-nav,.cc-framed .cc-sub{display:none}" in html
    for other in KINDS:
        # unframed: the API page path, no target; framed: /<agent>, target _top
        assert re.search(r'<a href="%s"[^>]*data-framed-href="/%s"' % (
            re.escape(P.PAGE_PATHS[other]), other), html), other
    assert 'target="_top"' not in html.split("<main")[0].split("</script>", 1)[1]


def test_the_framed_switch_retargets_links_under_a_dom(monkeypatch):
    """Execute the switch itself under node with a minimal DOM stand-in, framed
    and unframed."""
    if not NODE:
        pytest.fail("node is required")
    script = """
      function mk(framed) {
        var classes = [], links = [{a: {href: '/api/command/agents/xavier/page', 'data-framed-href': '/xavier'}}];
        links.forEach(function (l) { l.getAttribute = function (k) { return l.a[k]; }; l.setAttribute = function (k, v) { l.a[k] = v; }; });
        var self = {}; var win = {self: self, top: framed ? {} : self};
        var doc = {readyState: 'complete', documentElement: {classList: {add: function (c) { classes.push(c); }}},
                   querySelectorAll: function () { return links; }, addEventListener: function () {}};
        new Function('window', 'document', %s)(win, doc);
        return {classes: classes, link: links[0].a};
      }
      process.stdout.write(JSON.stringify([mk(true), mk(false)]));
    """ % json.dumps(CCP.FRAMED_JS)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr
    framed, plain = json.loads(out.stdout)
    assert framed["classes"] == ["cc-framed"]
    assert framed["link"]["href"] == "/xavier" and framed["link"]["target"] == "_top"
    assert plain["classes"] == [] and plain["link"]["href"] == "/api/command/agents/xavier/page"
    assert "target" not in plain["link"]



@pytest.mark.parametrize("kind", KINDS)
def test_every_character_canvas_is_labelled_a_placeholder(monkeypatch, kind):
    c, _ = _client(monkeypatch)
    html = c.get(P.PAGE_PATHS[kind], headers={"X-Admin-Token": _Cfg.admin_token}).text
    assert CCP.PLACEHOLDER_TAG == "PLACEHOLDER CHARACTER — final model pending"
    assert '<span class="cc-placeholder" id="cc-placeholder" data-placeholder>%s</span>' % CCP.PLACEHOLDER_TAG in html
    assert 'aria-label="Placeholder character, final model pending' in html
    assert CCP.PLACEHOLDER_TAG + ". " in html                     # the text alternative
    assert "Placeholder character, final model pending: animated 3D stand-in for" in html   # kept on mode change


# ═════════════════════════════════════════════════════════════════════
# THE LICENSED-CHARACTER PIPELINE (glTF 2.0 binary)
# ═════════════════════════════════════════════════════════════════════

GLTF_TEST = Path(__file__).resolve().parent / "assets" / "gltf"


def _node_mjs(body, timeout=60):
    if not NODE:
        pytest.fail("node is required: the pipeline's own module is executed")
    src = ("const A = %s;\nconst THREE = await import(A + 'three.module.min.js');\n"
           "const av = await import(A + 'cc_avatar.js');\nconst {GLTFLoader} = await import(A + 'GLTFLoader.js');\n"
           "import fs from 'fs';\nconst out = await (async () => {\n%s\n})();\n"
           "process.stdout.write(JSON.stringify(out));\n") % (json.dumps(str(STATIC) + "/"), body)
    src = "import fs from 'fs';\n" + src.replace("import fs from 'fs';\n", "")
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as f:
        f.write(src)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, text=True, timeout=timeout)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_bones_resolve_from_mixamo_vrm_and_explicit_overrides():
    got = _node_mjs("""
      function rig(names) { const root = new THREE.Group(); let p = root; for (const n of names) { const b = new THREE.Bone(); b.name = n; p.add(b); p = b; } return root; }
      const mix = av.resolveBones(rig(['mixamorigHips', 'mixamorigSpine', 'mixamorigSpine1', 'mixamorigSpine2', 'mixamorigNeck', 'mixamorigHead', 'mixamorigLeftEye']));
      const vrm = av.resolveBones(rig(['J_Bip_C_Hips', 'J_Bip_C_Spine', 'J_Bip_C_Chest', 'J_Bip_C_UpperChest', 'J_Bip_C_Neck', 'J_Bip_C_Head']));
      const hum = av.resolveBones(rig(['Hips', 'Spine', 'Chest', 'Neck', 'Head']));
      const odd = av.resolveBones(rig(['torso_joint_1', 'torso_joint_2', 'torso_joint_3', 'neck_joint_1', 'neck_joint_2']));
      const fixed = av.resolveBones(rig(['torso_joint_1', 'torso_joint_2', 'torso_joint_3', 'neck_joint_1', 'neck_joint_2']),
        {hips: 'torso_joint_1', spine: 'torso_joint_2', chest: 'torso_joint_3', neck: 'neck_joint_1', head: 'neck_joint_2'});
      return {mix: [mix.missing, mix.source], vrm: [vrm.missing, vrm.source], hum: hum.missing, odd: odd.missing, fixed: fixed.missing, arkit: av.ARKIT_52.length};
    """)
    assert got["mix"][0] == [] and got["mix"][1]["chest"] == "mixamorigSpine2" and got["mix"][1]["leftEye"] == "mixamorigLeftEye"
    assert got["vrm"][0] == [] and got["vrm"][1]["chest"] == "J_Bip_C_UpperChest"
    assert got["hum"] == [] and got["fixed"] == []
    assert got["odd"] == ["hips", "spine", "chest", "neck", "head"]
    assert got["arkit"] == 52


def test_arkit_blendshapes_resolve_by_name_namespace_and_override():
    got = _node_mjs("""
      const m = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1));
      m.morphTargetDictionary = {'blendShape1.eyeBlinkLeft': 0, 'EyeBlinkRight': 1, 'jawOpen': 2, 'mouthSmileLeft': 3};
      m.morphTargetInfluences = [0, 0, 0, 0];
      const g = new THREE.Group(); g.add(m);
      const a = av.resolveBlendshapes(g);
      const buf = fs.readFileSync(%s); const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
      const cube = await new GLTFLoader().parseAsync(ab, '');
      const b = av.resolveBlendshapes(cube.scene, {eyeBlinkLeft: '0', jawOpen: '1'});
      return {a: a.names.sort(), amissing: a.missing.length, b: b.names.sort()};
    """ % json.dumps(str(GLTF_TEST / "AnimatedMorphCube.glb")))
    assert got["a"] == ["eyeBlinkLeft", "eyeBlinkRight", "jawOpen", "mouthSmileLeft"]
    assert got["amissing"] == 48
    assert got["b"] == ["eyeBlinkLeft", "jawOpen"]


def test_the_controller_blinks_breathes_turns_speaks_and_stills_offline():
    got = _node_mjs("""
      const names = ['hips', 'spine', 'chest', 'neck', 'head'];
      const bones = {}; for (const n of names) { const b = new THREE.Bone(); b.name = n; bones[n] = b; }
      const mesh = {morphTargetInfluences: [0, 0, 0, 0]};
      const shapes = {eyeBlinkLeft: [{mesh, index: 0}], eyeBlinkRight: [{mesh, index: 1}], jawOpen: [{mesh, index: 2}]};
      const c = new av.AvatarController(bones, shapes, {mode: 'monitoring', seed: 3});
      const dt = 1 / 60; let blinkFrames = 0, runs = [], run = 0, maxChest = 0, jawBefore = 0, jawDuring = 0, jawAfter = 0;
      for (let i = 0; i < 60 * 120; i++) {
        c.update(dt);
        if (mesh.morphTargetInfluences[0] > 0) { run++; blinkFrames++; } else if (run) { runs.push(run); run = 0; }
        maxChest = Math.max(maxChest, Math.abs(bones.chest.rotation.x));
        jawBefore = Math.max(jawBefore, mesh.morphTargetInfluences[2]);
      }
      c.setSpeaking(true); let jawTextOnly = 0; for (let i = 0; i < 120; i++) { c.update(dt); jawTextOnly = Math.max(jawTextOnly, mesh.morphTargetInfluences[2]); }
      for (let i = 0; i < 180; i++) { c.setSpeech({amplitude: 0.5 + 0.5 * Math.sin(i / 5)}); c.update(dt); jawDuring = Math.max(jawDuring, mesh.morphTargetInfluences[2]); }
      c.setSpeech(null); c.setSpeaking(false); c.update(dt); jawAfter = mesh.morphTargetInfluences[2];
      const hipsPos = bones.hips.position.toArray();
      c.setMode('unavailable'); let n = 0; while (c.update(dt) && n < 60 * 60) n++;
      const frozen = JSON.stringify(names.map((k) => bones[k].rotation.toArray()));
      const again = c.update(dt);
      return {blinks: c.stats.blinks, runs, maxYawDeg: c.stats.maxHeadYaw * 180 / Math.PI, maxBreath: c.stats.maxBreath,
              maxChest, saccades: c.stats.saccades, postures: c.stats.postureShifts, jawBefore, jawDuring, jawAfter,
              hipsPos, jawTextOnly, settledIn: n * dt, again, unchanged: frozen === JSON.stringify(names.map((k) => bones[k].rotation.toArray())),
              offBlink: mesh.morphTargetInfluences[0]};
    """)
    assert 20 <= got["blinks"] <= 60, got["blinks"]                      # every 2-6 s over 120 s
    durations = [r / 60.0 for r in got["runs"]]
    assert durations and all(0.1 <= d <= 0.2 for d in durations), durations    # about 150 ms
    assert got["maxYawDeg"] <= 12.0 + 1e-9
    assert 0.004 <= got["maxBreath"] <= 0.006                             # milliradians, not bobbing
    assert got["maxChest"] < 0.02 and got["hipsPos"] == [0, 0, 0]
    assert got["saccades"] > 20 and got["postures"] >= 5
    assert got["jawBefore"] == 0 and got["jawAfter"] == 0
    assert got["jawTextOnly"] == 0                  # a rendered reply alone is not speech
    assert got["jawDuring"] > 0.2                   # real audio amplitude moves the jaw
    assert got["settledIn"] < 30 and got["again"] is False and got["unchanged"] is True
    assert got["offBlink"] == 0.45                                         # eyes lowered, still


def test_the_pipeline_loads_a_licensed_test_asset_and_drives_its_skeleton():
    got = _node_mjs("""
      const buf = fs.readFileSync(%s); const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
      const g = await new GLTFLoader().parseAsync(ab, '');
      const r = av.resolveBones(g.scene, {hips: 'torso_joint_1', spine: 'torso_joint_2', chest: 'torso_joint_3', neck: 'neck_joint_1', head: 'neck_joint_2'});
      const rest = r.bones.head.rotation.toArray();
      const c = new av.AvatarController(r.bones, {}, {mode: 'monitoring', seed: 5});
      let maxDev = 0; for (let i = 0; i < 600; i++) { c.update(1 / 60); maxDev = Math.max(maxDev, Math.abs(r.bones.head.rotation.y - rest[1])); }
      return {missing: r.missing, skinned: g.scene.getObjectByName('Proxy').isSkinnedMesh, maxDevDeg: maxDev * 180 / Math.PI};
    """ % json.dumps(str(GLTF_TEST / "RiggedFigure.glb")))
    assert got["missing"] == [] and got["skinned"] is True
    assert 0 < got["maxDevDeg"] <= 12.0


def test_test_assets_are_licensed_recorded_and_never_shipped():
    for name, spdx, who in (("RiggedFigure", "CC-BY-4.0", "Cesium"), ("AnimatedMorphCube", "CC0-1.0", "Microsoft")):
        assert (GLTF_TEST / (name + ".glb")).is_file()
        lic = (GLTF_TEST / (name + ".LICENSE.md")).read_text()
        assert 'SPDX license identifier: "%s"' % spdx in lic
        assert who in (GLTF_TEST / (name + ".README.md")).read_text()
    # what IS shipped is a licensed manifest entry, never a test asset
    shipped = sorted(p.name for p in STATIC.rglob("*") if p.suffix in (".glb", ".gltf"))
    listed = json.loads((STATIC / "models" / "manifest.json").read_text())["characters"]
    admitted = sorted(e["model"] for e in listed.values() if e.get("model") and e.get("test_asset") is False)
    assert shipped == admitted, (shipped, admitted)
    test_bytes = {(GLTF_TEST / f).read_bytes() for f in ("RiggedFigure.glb", "AnimatedMorphCube.glb")}
    assert not any((STATIC / "models" / n).read_bytes() in test_bytes for n in shipped)


def test_the_manifest_admits_only_complete_licensed_non_test_models(monkeypatch, tmp_path):
    for k in KINDS:
        a = P.character_asset(k)
        assert a["framing"] in ("face", "chest", "waist") and a["lighting"].startswith("cinematic_")
        if k == "derek":                                          # the Rocketbox candidate
            assert a["model"] == "/api/command/agents/static/models/derek_candidate.glb"
            assert a["license"]["spdx"] == "MIT" and "Rocketbox" in a["license"]["from"]
            assert a["candidate_label"] == "CANDIDATE MODEL (Rocketbox, MIT) — under evaluation"
            assert a["hide_materials"] == ["m002_opacity"]        # no opacity map was supplied
        else:
            assert a["model"] is None and a["why"]                # still the tagged placeholder
    shutil.copy(GLTF_TEST / "RiggedFigure.glb", tmp_path / "derek.glb")
    (tmp_path / "derek.LICENSE.txt").write_text("licence text")
    entry = {"model": "derek.glb", "license_file": "derek.LICENSE.txt", "license_spdx": "LicenseRef-Test",
             "licensed_from": "test", "test_asset": False, "framing": "chest", "lighting": "cinematic_warm",
             "bones": {"head": "neck_joint_2"}}
    def manifest(e):
        (tmp_path / "manifest.json").write_text(json.dumps({"characters": {"derek": e}}))
    monkeypatch.setattr(P, "MODELS_DIR", tmp_path)
    manifest(entry)
    got = P.character_asset("derek")
    assert got["model"] == "/api/command/agents/static/models/derek.glb"
    assert got["license"]["file"].endswith("derek.LICENSE.txt") and got["bones"] == {"head": "neck_joint_2"}
    manifest(dict(entry, test_asset=True))
    assert P.character_asset("derek")["model"] is None
    manifest({k: v for k, v in entry.items() if k != "test_asset"})
    assert P.character_asset("derek")["model"] is None
    manifest(dict(entry, license_file="absent.txt"))
    assert "licence file" in P.character_asset("derek")["why"]
    manifest(dict(entry, model="../derek.glb"))
    assert P.character_asset("derek")["model"] is None
    # the route serves the complete entry's files, and nothing else
    manifest(entry)
    c, _ = _client(monkeypatch)
    url = "/api/command/agents/static/models/derek.glb"
    assert c.get(url).status_code == 401
    r = c.get(url, headers={"X-Admin-Token": _Cfg.admin_token})
    assert r.status_code == 200 and r.headers["content-type"] == "model/gltf-binary"
    assert r.content[:4] == b"glTF"
    assert c.get("/api/command/agents/static/models/manifest.json",
                 headers={"X-Admin-Token": _Cfg.admin_token}).status_code == 404
    html = c.get(P.PAGE_PATHS["derek"], headers={"X-Admin-Token": _Cfg.admin_token}).text
    assert "/api/command/agents/static/models/derek.glb" in html        # handed to the loader


def test_the_loader_prefers_a_licensed_model_and_falls_back_to_the_placeholder():
    html = P.page_html("audrey")
    _c, module = _scripts(html)
    loader = module[0]
    assert "asset.model ? import('%s')" % P.ENDPOINTS["avatar"] in loader
    assert "m.mountAvatar(stage, asset" in loader and "return placeholder();" in loader
    assert 'data-cc-asset="{&quot;framing&quot;: &quot;waist&quot;' in html
    assert ".cc-real-model .cc-placeholder{display:none}" in html
    src = (STATIC / "cc_avatar.js").read_text()
    for need in ("Math.min(window.devicePixelRatio || 1, 1.5)", "document.hidden", "ema > 20",
                 "ACESFilmicToneMapping", "studioEnvironment", "setMeshoptDecoder", "window.__ccFps"):
        assert need in src, need
    assert "fetch(" not in src and "/api/" not in src



def test_the_rocketbox_derek_candidate_carries_what_the_pipeline_needs():
    """The converted model's own names, and the pipeline resolving them."""
    import struct
    glb = (STATIC / "models" / "derek_candidate.glb").read_bytes()
    assert glb[:4] == b"glTF" and len(glb) < 8_000_000
    n = struct.unpack("<I", glb[12:16])[0]
    j = json.loads(glb[20:20 + n])
    targets = j["meshes"][0]["extras"]["targetNames"]
    joints = [j["nodes"][i]["name"] for i in j["skins"][0]["joints"]]
    assert len(targets) == 175 and len(joints) == 80
    assert "EXT_meshopt_compression" in j["extensionsUsed"]
    assert {m["name"] for m in j["materials"]} == {"m002_body", "m002_head", "m002_opacity"}
    assert all(m["pbrMetallicRoughness"].get("metallicRoughnessTexture") and m.get("normalTexture") for m in j["materials"])
    lic = (STATIC / "models" / "ROCKETBOX_LICENSE.md").read_text()
    assert lic.startswith("MIT License") and "Copyright (c) 2020 Microsoft" in lic
    got = _node_mjs("""
      const targets = %s, joints = %s;
      const root = new THREE.Group(); for (const n of joints) { const b = new THREE.Bone(); b.name = n.replace(/ /g, '_'); root.add(b); }
      const m = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1)); m.morphTargetDictionary = {}; targets.forEach((t, i) => { m.morphTargetDictionary[t] = i; });
      m.morphTargetInfluences = targets.map(() => 0); root.add(m);
      const b = av.resolveBones(root), s = av.resolveBlendshapes(root), v = av.resolveVisemes(root);
      return {missing: b.missing, source: b.source, arkit: s.names.length, arkitMissing: s.missing, blink: s.shapes.eyeBlinkLeft.map((x) => targets[x.index]), visemes: Object.keys(v).sort()};
    """ % (json.dumps(targets), json.dumps(joints)))
    assert got["missing"] == []
    assert got["source"]["head"] == "Bip01_Head" and got["source"]["chest"] == "Bip01_Spine2"
    assert got["source"]["leftEye"] == "Bip01_LEye"
    assert got["arkit"] == 52 and got["arkitMissing"] == []
    assert got["blink"] == ["AK_09_EyeBlinkLeft"]
    assert got["visemes"] == sorted(["sil", "PP", "FF", "TH", "DD", "kk", "CH", "SS", "nn", "RR", "aa", "E", "I", "O", "U"])


def test_the_candidate_is_labelled_and_credited_on_the_derek_page(monkeypatch):
    c, _ = _client(monkeypatch)
    html = c.get(P.PAGE_PATHS["derek"], headers={"X-Admin-Token": _Cfg.admin_token}).text
    assert "derek_candidate.glb" in html and "CANDIDATE MODEL (Rocketbox, MIT)" in html
    assert "Character model: Microsoft Rocketbox Male_Adult_01 (MIT, © 2020 Microsoft)" in html
    assert ".cc-real-model.cc-candidate .cc-placeholder{display:block}" in html
    r = c.get("/api/command/agents/static/models/derek_candidate.glb", headers={"X-Admin-Token": _Cfg.admin_token})
    assert r.status_code == 200 and r.headers["content-type"] == "model/gltf-binary"
    lic = c.get("/api/command/agents/static/models/ROCKETBOX_LICENSE.md", headers={"X-Admin-Token": _Cfg.admin_token})
    assert lic.status_code == 200 and "Copyright (c) 2020 Microsoft" in lic.text
    for k in ("xavier", "audrey"):
        h = c.get(P.PAGE_PATHS[k], headers={"X-Admin-Token": _Cfg.admin_token}).text
        assert "Rocketbox" not in h and CCP.PLACEHOLDER_TAG in h
