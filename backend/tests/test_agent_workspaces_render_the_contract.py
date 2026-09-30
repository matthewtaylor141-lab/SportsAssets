"""PROOF 22 (UI): THE AGENT WORKSPACES DRAW THE CONTRACT TRUTHFULLY.

The three Command Centre workspaces (Derek, Xavier, Audrey), the index and
the product demo are presentation of the workspace JSON contract. These tests
execute the pages' OWN render code under node -- the exact JavaScript each
page ships -- against contract-shaped records, and drive the page routes
through the real `agent_pages.router` with the real `require_command`.

What they pin:
  * every required section name from the contract is rendered, by name;
  * reads are same-origin with credentials, and only reach the agent JSON;
  * a 401 shows the locked state with a link to the desk, a 404 says the
    endpoint is not deployed in this build, any other failure is UNAVAILABLE;
  * EMPTY carries its reason and is not styled as success; UNAVAILABLE
    carries the failed read's reason; a missing section is MISSING;
    UNKNOWN is never rendered as zero;
  * only the eight truthful agent states are drawn as states;
  * Xavier with nothing owned says so plainly, with the servicing count;
  * search completeness, the selected policy and the (not dispatched)
    shadow comparison are shown; an incomplete search is "best among
    examined (N of M)";
  * the trace view follows ids across the four reads;
  * the demo carries the rehearsal label, the standalone copy makes no
    network call and matches its generator, and the served demo's
    production mode reads only same-origin agent JSON.

The JSON endpoints themselves are other streams' modules; the companion file
`test_agent_workspaces_show_runtime_records.py` runs these pages against the
real routers once they are merged (REQUIRES_AGENT_ROUTERS).

node is REQUIRED (it is on the CI image). Its absence fails loudly rather
than skipping: a skipped truthfulness test is how an untested page ships.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from sportsassets.api import agent_pages as P

NOW = 1_790_000_000
NODE = shutil.which("node")
REPO = Path(__file__).resolve().parents[2]


def _node(kind, body, timeout=30):
    """Run `body` (an async function body returning a JSON-able value) after
    the page's own render code for `kind`."""
    if not NODE:
        pytest.fail("node is required: these tests execute the pages' own "
                    "render code; install node rather than skipping")
    script = (P.render_js(kind) + "\n;(async function(){ var __out = await (async function(){\n"
              + body + "\n})(); process.stdout.write(JSON.stringify(__out)); })()"
              ".catch(function(e){ console.error(e && e.stack || e); process.exit(3); });")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, text=True,
                             timeout=timeout)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _sec(data, status="OK", why=None, evidence=None):
    return {"status": status, "why": why, "data": data,
            "evidence": evidence if evidence is not None else []}


def _agent(agent_id, state="IDLE", **kw):
    a = {"agent_id": agent_id, "display_name": agent_id.title(),
         "mandate": "mandate of " + agent_id, "policy_version": "p1",
         "model_version": "m1", "code_version": "c1", "state": state,
         "activity": None, "last_heartbeat_at": NOW - 30, "runs": 10,
         "errors": 0, "cadence": {"measured_interval_s": 30.0}}
    a.update(kw)
    return a


EV = [{"kind": "agent_decisions", "id": "DRK-1",
       "href": "/api/command/agents/derek/decisions/DRK-1"}]


def _derek():
    return {"agent": _agent("DEREK", "EVALUATING", activity="evaluating NYY @ BOS"),
            "read_at": NOW, "read_only": True, "sections": {
        "status": _sec({"state": "EVALUATING"}),
        "versions": _sec({"policy_version": "p1", "model_version": "m1", "code_version": "c1"}),
        "coverage": _sec({"categories": [
            {"category": "MLB_MONEYLINE", "count": 214, "supported": True},
            {"category": "PLAYER_PROPS", "count": 1204, "supported": False,
             "reason": "no reference price"}]}),
        "subscription": _sec(None, "UNAVAILABLE", "RuntimeError: feed down"),
        "opportunity_queue": _sec([{
            "subject": "NYY @ BOS", "side": "YES",
            "pinnacle_probability": 0.6, "pinnacle_at": NOW - 4,
            "internal_probability": 0.6, "internal_at": NOW - 3,
            "qualification": "QUALIFIED", "price": 0.5, "gross_edge_pp": 10.0,
            "net_ev_usd": 0.0826, "roi": 0.165, "size_qty": 100,
            "blockers": [{"dependency_class": c, "reason": "r-" + c}
                         for c in P.DEPENDENCY_CLASSES],
            "evidence": EV}]),
        "decisions": _sec([], "EMPTY", "no entry decision recorded: nothing has qualified"),
        "plans_fills": _sec([{"intent_id": "FI-1", "ordered_qty": 100,
                              "acknowledged_at": NOW - 60, "filled_qty": 30,
                              "outstanding_qty": 70}]),
        "handoffs": _sec([{"entry_intent_id": "FI-1", "portfolio_group_id": "PG-1",
                           "owner_agent": "XAVIER", "ordered_qty": 100,
                           "confirmed_qty": 30, "outstanding_qty": 70}]),
        "latency": _sec({"p50_ms": 120, "p90_ms": None}),
        "performance": _sec({"realised_net_usd": None}),
        # "collection" deliberately absent -> MISSING
    }}


SEARCH = {"discovered": 7, "examined": 5,
          "excluded": [{"contract": "+5.5", "reason": "no book"}],
          "unexamined": [{"contract": "+6.5", "reason": "request budget"}],
          "why_ended": "request budget reached"}
DECISION = {"portfolio_group_id": "PG-1", "xavier_decision_id": "XD-9",
            "chosen_action": "HEDGE +1.5",
            "reasoning": {"xavier_ladder": {"search_completeness": SEARCH},
                          "selected_policy": {"policy_key": "mgmt", "version": "1.4.0",
                                              "source": "ACTIVE"},
                          "shadow_comparison": {
                              "policy": {"policy_key": "mgmt", "version": "1.5.0-rc1",
                                         "source": "CANDIDATE"},
                              "would_choose": "EXIT", "ev_given_up_usd": 1.10,
                              "downside_improved_usd": 6.40}}}


def _xavier(positions_empty=True):
    positions = (_sec([], "EMPTY", "no funded position exists") if positions_empty
                 else _sec([dict(DECISION, basis_usd=15.53, primary_qty=30,
                                 hedge_qty=25, residual_qty=5)]))
    return {"agent": _agent("XAVIER"), "read_at": NOW, "read_only": True, "sections": {
        "status": _sec({"state": "IDLE"}),
        "versions": _sec({"policy_version": "p1"}),
        "positions": positions,
        "reviews": _sec([DECISION, {"portfolio_group_id": "PG-2", "chosen_action": "HOLD"}]),
        "ladder": _sec({"rows": [{"line": "+1.5", "price": 0.6}]}),
        "alternatives": _sec({"groups": [dict(DECISION, alternatives=[
            {"action": "HOLD", "expected_net_pnl_usd": 2.47, "worst_case_usd": -15.53},
            {"action": "HEDGE +1.5", "expected_net_pnl_usd": 1.55,
             "increment_vs_hold_usd": -0.92, "worst_case_usd": -5.95,
             "unpaired_qty": 5, "selected": True, "eligible": True}])]}),
        "payout_tables": _sec([{"alternative": "HOLD", "states": [
            {"state": "RS win", "probability": 0.4, "net_usd": -15.53}]}]),
        "execution": _sec([{"at": NOW - 5, "kind": "FILL", "qty": 25}]),
        "recovery": _sec([], "EMPTY", "nothing to recover"),
        "performance": _sec({"management_contribution_usd": None}),
        "servicing_cadence": _sec({"runs": 17, "passes_with_nothing": 17,
                                   "measured_interval_s": 30.2}),
    }}


def _audrey():
    return {"agent": _agent("AUDREY"), "read_at": NOW, "read_only": True, "sections": {
        "status": _sec({"state": "IDLE"}),
        "versions": _sec({"policy_version": "p1"}),
        "daily_reports": _sec([{"report_date": "2026-10-01", "summary": "one fixture"}]),
        "findings": _sec([
            {"agent": "DEREK", "title": "fill rate", "severity": "INFO"},
            {"agent": "XAVIER", "title": "bound cost", "xavier_decision_id": "XD-9",
             "directive_id": "D-1",
             "later_discovered": [{"contract": "+7.5", "reason": "listed after decision"}]}]),
        "outcomes": _sec({"categories": {"ACTUAL": {"count": 1, "net_usd": -0.95},
                                         "OBSERVED_ALTERNATIVE": {"count": 3},
                                         "MODELLED_ESTIMATE": {"count": 3}}}),
        "cohort_quality": _sec({"observations": 412, "independent_cohorts": 57}),
        "tasks": _sec([{"task_id": "T-1", "title": "reduce drawdown", "assignee": "XAVIER",
                        "created_by": "AUDREY", "status": "OPEN", "directive_id": "D-1"}]),
        "candidates": _sec([{"candidate_id": "C-1", "task_id": "T-1",
                             "state": "APPROVAL_READY", "proposer": "XAVIER",
                             "evaluator": "XAVIER"}]),
        "evaluations": _sec([], "EMPTY", "no candidate has been evaluated"),
        "releases": _sec([{"release_id": "R-1", "state": "CANARY", "canary_state": "RUNNING",
                           "proposer": "XAVIER", "evaluator": "AUDREY",
                           "approver": "operator"}]),
        "directives": _sec([{"directive_id": "D-1", "state": "PROPOSED",
                             "text": "Prioritize reducing drawdown without increasing capital limits."}]),
        "conversations": _sec([], "EMPTY", "no conversation yet"),
        "provider": _sec({"mode": "RECORDS_ONLY",
                          "disclosure": "answered from records; AI provider not configured"}),
    }}


FIXTURES = {"derek": _derek, "xavier": _xavier, "audrey": _audrey}


def _render(kind, js_json):
    return _node(kind, "return AG.workspace(%r, %s);" % (kind, json.dumps(js_json)))


def _card(html, key):
    m = re.search(r'<section class="card[^"]*" id="s-%s" data-section="%s" '
                  r'data-status="(\w+)">(.*?)</section>' % (key, key), html, re.S)
    assert m, "section %s not rendered" % key
    return m.group(1), m.group(2)


# ── contract section names ───────────────────────────────────────────
@pytest.mark.parametrize("kind", ["derek", "xavier", "audrey"])
def test_every_required_section_is_rendered_by_name(kind):
    page_html = P.page_html(kind)
    specs = _node(kind, "return AG.SPECS[%r].sections.map(function(s){return s.key;});" % kind)
    assert set(P.REQUIRED_SECTIONS[kind]) <= set(specs), set(P.REQUIRED_SECTIONS[kind]) - set(specs)
    for key in P.REQUIRED_SECTIONS[kind]:
        assert "key: '%s'" % key in page_html
    # rendered against a record, every required card is present, by id
    rendered = _render(kind, FIXTURES[kind]())
    for key in P.REQUIRED_SECTIONS[kind]:
        _card(rendered, key)


def test_an_unknown_extra_section_is_still_drawn():
    j = _derek()
    j["sections"]["new_thing"] = _sec({"x": 1})
    st, body = _card(_render("derek", j), "new_thing")
    assert st == "OK" and "x" in body


# ── transport: same-origin, credentials, locked, not deployed ────────
ALLOWED_PATHS = set(P.ENDPOINTS.values()) | set(P.PAGE_PATHS.values()) | {P.DESK_PAGE}


def _allowed(u):
    # a literal is a known path, or the stem the page completes into one
    return u in ALLOWED_PATHS or any(a.startswith(u) for a in ALLOWED_PATHS)


def _url_literals(text):
    return set(re.findall(r"""['"](/api/[^'"\s]*)['"]""", text))


@pytest.mark.parametrize("kind", ["index", "derek", "xavier", "audrey"])
def test_pages_read_only_same_origin_agent_json_with_credentials(kind):
    html = P.page_html(kind)
    assert "credentials: 'same-origin'" in html
    for u in _url_literals(html):
        assert _allowed(u), u
    assert not re.search(r"https?://|['\"]//", html), "absolute URL in page"
    low = html.lower()
    for forbidden in ("x-admin-token", "x-desk-token", "localstorage", "sessionstorage",
                      "type=\"password\"", "bt_control", "bt_command"):
        assert forbidden not in low, forbidden


def test_a_401_is_the_locked_state_with_a_link_to_the_desk():
    got = _node("derek", """
      var seen = null;
      var o = await AG.load('/api/command/agents/derek', function (u, opts) { seen = opts; return Promise.resolve({status: 401, ok: false, json: function () { return Promise.resolve({}); }}); });
      return {kind: o.kind, opts: seen, html: AG.gate(o, '/api/command/agents/derek')};""")
    assert got["kind"] == "LOCKED"
    assert got["opts"]["credentials"] == "same-origin" and got["opts"]["cache"] == "no-store"
    assert P.LOCKED_TEXT in got["html"]
    assert 'href="%s"' % P.DESK_PAGE in got["html"]


def test_a_404_says_not_deployed_and_other_failures_are_unavailable():
    got = _node("xavier", """
      function f(st, body) { return function () { return Promise.resolve({status: st, ok: st < 300, json: function () { return Promise.resolve(body); }}); }; }
      var a = await AG.load('/api/command/agents/xavier', f(404, {detail: 'Not Found'}));
      var b = await AG.load('/api/command/agents/xavier', f(503, {detail: {reason: 'NO_DATABASE_POOL'}}));
      var c = await AG.load('/api/command/agents/xavier', function () { return Promise.reject(new TypeError('offline')); });
      return [a.kind, AG.gate(a, '/api/command/agents/xavier'), b.kind, AG.gate(b, '/api/command/agents/xavier'), c.kind, c.why];""")
    assert got[0] == "NOT_DEPLOYED" and "Not deployed in this build" in got[1]
    assert got[2] == "UNAVAILABLE" and "NO_DATABASE_POOL" in got[3] and "503" in got[3]
    assert got[4] == "UNAVAILABLE" and "TypeError" in got[5]


# ── truthful section states ──────────────────────────────────────────
def test_empty_carries_its_reason_and_is_not_styled_as_success():
    html = _render("derek", _derek())
    st, body = _card(html, "decisions")
    assert st == "EMPTY"
    assert "no entry decision recorded: nothing has qualified" in body
    assert "pill st-EMPTY" in body and "st-OK" not in body
    assert "This is not a result" in body
    # EMPTY pill is neutral in the stylesheet, not the OK colour
    assert ".st-EMPTY{color:var(--neutral)" in P.BASE_CSS


def test_unavailable_names_the_failed_read_and_missing_is_missing():
    html = _render("derek", _derek())
    st, body = _card(html, "subscription")
    assert st == "UNAVAILABLE" and "RuntimeError: feed down" in body
    st, body = _card(html, "collection")
    assert st == "MISSING" and "returned no &#39;collection&#39; section" in body


def test_an_empty_or_unavailable_section_without_a_reason_says_so():
    j = _derek()
    j["sections"]["latency"] = {"status": "EMPTY", "why": None, "data": None, "evidence": []}
    st, body = _card(_render("derek", j), "latency")
    assert st == "EMPTY" and "the record named no reason" in body


def test_unknown_is_never_rendered_as_zero():
    html = _render("derek", _derek())
    _, perf = _card(html, "performance")
    assert "UNKNOWN" in perf and "$0.00" not in perf
    _, lat = _card(html, "latency")
    assert "UNKNOWN" in lat
    got = _node("derek", "return [AG.fmt('net_ev_usd', null), AG.fmt('count', undefined), AG.fmt('net_ev_usd', 0)];")
    assert "UNKNOWN" in got[0] and "UNKNOWN" in got[1] and "$0.00" in got[2]


def test_only_the_eight_truthful_states_are_drawn_as_states():
    got = _node("derek", "return {states: AG.STATES, ok: AG.STATES.map(function(s){return AG.stateBadge(s);}), bad: AG.stateBadge('THINKING'), none: AG.stateBadge(null)};")
    assert tuple(got["states"]) == P.AGENT_STATES
    for s, h in zip(got["states"], got["ok"]):
        assert 'sb-%s"' % s in h
    assert "UNRECOGNISED" in got["bad"] and "sb-THINKING" not in got["bad"]
    assert "NO STATUS RECORDED" in got["none"]


def test_contemplating_is_the_persisted_activity_never_invented():
    html = _render("xavier", _xavier())   # activity None
    assert "No activity is recorded on agent_status. Nothing is inferred in its place." in html
    html = _render("derek", _derek())
    assert "evaluating NYY @ BOS" in html and "persisted evaluation state" in html


def test_every_figure_links_its_evidence_and_only_same_origin():
    html = _render("derek", _derek())
    _, q = _card(html, "opportunity_queue")
    assert '<a class="ev" href="/api/command/agents/derek/decisions/DRK-1">' in q
    got = _node("derek", "return AG.evidence([{kind:'x', id:1, href:'https://evil.example/x'}, {kind:'y', id:2, href:'//evil'}]);")
    assert "href" not in got and "ev-nolink" in got


# ── Derek ────────────────────────────────────────────────────────────
def test_derek_keeps_probabilities_and_the_four_economic_quantities_apart():
    _, q = _card(_render("derek", _derek()), "opportunity_queue")
    for head in ("Pinnacle (reference)", "Internal (model)", "Qualification",
                 "Gross edge", "Net EV / contract", "ROI", "Sizing"):
        assert head in q, head
    assert "+10.00 pp" in q and "$0.0826" in q and "16.5%" in q and ">100<" in q
    assert q.count("0.600") == 2         # two probabilities, each shown
    for c in P.DEPENDENCY_CLASSES:       # every blocker carries its class
        assert "dep dep-%s" % c in q


def test_derek_distinguishes_acknowledgement_from_fill_and_marks_unsupported():
    html = _render("derek", _derek())
    _, pf = _card(html, "plans_fills")
    assert "it is not a fill" in pf and 'title="30 filled of 100 ordered"' in pf
    _, cov = _card(html, "coverage")
    assert "PLAYER_PROPS" in cov and "UNSUPPORTED" in cov


# ── Xavier ───────────────────────────────────────────────────────────
def test_xavier_with_nothing_owned_says_so_plainly():
    st, body = _card(_render("xavier", _xavier()), "positions")
    assert st == "EMPTY"
    assert "No position is owned by Xavier." in body
    assert "The servicing task ran 17 times with nothing to service" in body
    assert "An empty pass is not management" in body
    assert "SELECTED" not in body


def test_xavier_positions_show_basis_quantities_and_residual():
    st, body = _card(_render("xavier", _xavier(positions_empty=False)), "positions")
    assert st == "OK"
    for label in ("Basis", "Current exposure", "Primary qty", "Hedge qty",
                  "Residual (unpaired) qty", "Current review", "Next review", "Selected action"):
        assert label in body
    assert "$15.53" in body and "HEDGE +1.5" in body


def test_xavier_alternatives_table_has_every_column_and_marks_the_selection():
    _, alt = _card(_render("xavier", _xavier()), "alternatives")
    for col in ("Expected net P&amp;L", "Increment vs HOLD", "Worst case", "P(net profit)",
                "P(both win)", "Capital required", "Capital duration", "Executable qty",
                "Unpaired qty", "Fees", "Compatibility", "Evidence age", "Eligibility / refusal"):
        assert col in alt, col
    assert alt.count("SELECTED</span>") == 1


def test_an_incomplete_search_is_best_among_examined_never_best_available():
    html = _render("xavier", _xavier())
    _, alt = _card(html, "alternatives")
    assert "best among examined (5 of 7)" in alt
    assert "request budget reached" in alt and "no book" in alt
    _, rev = _card(html, "reviews")
    assert "best among examined (5 of 7)" in rev
    assert "search completeness not recorded" in rev      # the second review
    _, lad = _card(html, "ladder")
    assert "search completeness not recorded" in lad
    assert "best available" not in html
    complete = _node("xavier", "return AG.R.searchInfo({discovered: 4, examined: 4, unexamined: []}).label;")
    assert complete == "search complete: all 4 discovered contracts examined"


def test_selected_policy_and_shadow_comparison_are_side_by_side_not_dispatched():
    _, alt = _card(_render("xavier", _xavier()), "alternatives")
    assert "Selected policy · dispatched decision" in alt
    assert "mgmt @ 1.4.0" in alt and "ACTIVE" in alt
    assert 'data-shadow="NOT_DISPATCHED"' in alt and "NOT DISPATCHED" in alt
    assert "mgmt @ 1.5.0-rc1" in alt and "Would choose <b>EXIT</b>" in alt
    assert "$1.10" in alt and "$6.40" in alt


# ── Audrey ───────────────────────────────────────────────────────────
def test_audrey_keeps_the_four_evidence_categories_visibly_distinct():
    _, out = _card(_render("audrey", _audrey()), "outcomes")
    for cat in ("ACTUAL", "OBSERVED", "MODELLED", "UNRESOLVED"):
        assert 'class="cat cat-%s" data-cat="%s"' % (cat, cat) in out
    assert re.search(r'data-cat="UNRESOLVED">.*?UNKNOWN', out, re.S)   # not reported, not 0
    css = P.BASE_CSS
    assert ".cat-ACTUAL{border:2px solid" in css and ".cat-MODELLED{border:2px dashed" in css
    assert ".cat-UNRESOLVED{border:2px dotted" in css


def test_audrey_audits_show_the_search_limitation_and_later_discoveries():
    _, f = _card(_render("audrey", _audrey()), "findings")
    assert "search completeness not recorded" in f
    assert "Not considered at decision time" in f and "+7.5" in f


def test_writes_say_they_need_the_operator_credential():
    html = _render("audrey", _audrey())
    _, d = _card(html, "directives")
    assert "requires the OPERATOR credential" in d
    assert 'data-write="/api/command/agents/audrey/directives/D-1/confirm"' in d
    assert 'data-write="/api/command/agents/audrey/directives/D-1/cancel"' in d
    _, c = _card(html, "candidates")
    assert "requires the OPERATOR credential" in c and "SEPARATION: proposer = evaluator" in c
    got = _node("audrey", """
      var seen = {};
      function f(st) { return function (u, o) { seen.o = o; return Promise.resolve({status: st, ok: st < 300, json: function () { return Promise.resolve({}); }}); }; }
      var a = await AG.write('/api/command/agents/audrey/directives/D-1/confirm', f(403), {});
      var o = seen.o;
      var b = await AG.write('/x', f(404), {});
      return [a.kind, a.text, o.method, o.credentials, b.kind];""")
    assert got[0] == "NEEDS_OPERATOR" and "OPERATOR credential" in got[1]
    assert got[2] == "POST" and got[3] == "same-origin" and got[4] == "NOT_DEPLOYED"


def test_chat_answers_show_provider_mode_disclosure_and_citations():
    got = _node("audrey", """
      var a = AG.chat.answer({answer: 'Xavier held.', citations: [{kind: 'bettor_xavier_decisions', id: 'XD-9', href: '/api/command/xavier/XD-9'}],
                              provider: {mode: 'RECORDS_ONLY', disclosure: 'answered from records; AI provider not configured'}});
      var b = AG.chat.answer({answer: 'no provider field'});
      var seen = {};
      var c = await AG.chat.ask(function (u, o) { seen.u = u; seen.o = o; return Promise.resolve({status: 404, ok: false, json: function () { return Promise.resolve({}); }}); }, 'hi', null);
      return [a, b, c.kind, c.html, seen.u, seen.o.credentials, seen.o.method];""")
    assert "Provider mode: RECORDS_ONLY" in got[0]
    assert "answered from records; AI provider not configured" in got[0]
    assert '<a class="ev" href="/api/command/xavier/XD-9">' in got[0]
    assert "Provider mode not reported by the service" in got[1]
    assert got[2] == "NOT_DEPLOYED" and "not deployed in this build" in got[3]
    assert got[4] == P.ENDPOINTS["chat"] and got[5] == "same-origin" and got[6] == "POST"
    page = P.page_html("audrey")
    assert 'id="chat-form"' in page and "SCRIPTED EXCHANGE" not in page


# ── index ────────────────────────────────────────────────────────────
def test_index_shows_all_three_agents_even_when_one_row_is_missing():
    j = {"read_at": NOW, "agents": [_agent("DEREK", "BLOCKED", activity="waiting on owner"),
                                    _agent("XAVIER")],
         "handoffs": {"status": "EMPTY", "why": "no fill has been confirmed", "data": [], "evidence": []},
         "tasks": [{"task_id": "T-1", "assignee": "DEREK", "status": "OPEN", "title": "calibrate"}]}
    html = _node("index", "return AG.index(%s);" % json.dumps(j))
    for a in ("DEREK", "XAVIER", "AUDREY"):
        assert 'data-agent="%s"' % a in html
    assert "The index returned no identity or status row for AUDREY." in html
    assert "sb-BLOCKED" in html and "calibrate" in html
    assert "no fill has been confirmed" in html and "st-card-EMPTY" in html


# ── trace ────────────────────────────────────────────────────────────
def _outs():
    ok = lambda j: {"kind": "OK", "json": j}   # noqa: E731
    return {"index": ok({"read_at": NOW, "agents": [], "handoffs": [], "tasks": []}),
            "derek": ok({"read_at": NOW, "sections": {
                "decisions": _sec([{"decision_ref": "DRK-1", "verdict": "ENTER",
                                    "evidence": [{"kind": "bettor_funded_intents", "id": "FI-1",
                                                  "href": "/api/x/FI-1"}]}]),
                "handoffs": _sec([{"entry_intent_id": "FI-1", "portfolio_group_id": "PG-1"}])}}),
            "xavier": ok(_xavier()),
            "audrey": ok(_audrey())}


def test_the_trace_follows_ids_from_entry_to_candidate():
    got = _node("audrey", "var h = AG.trace.render(%s, 'entry_intent_id:FI-1'); return h;" % json.dumps(_outs()))
    counts = dict(re.findall(r'data-stage="(\w+)" data-count="(\d+)"', got))
    assert all(int(counts[s]) >= 1 for s in ("DEREK_DECISION", "HANDOFF", "XAVIER_DECISION",
                                               "AUDIT", "DIRECTIVE", "TASK", "CANDIDATE")), counts
    assert "linked by entry_intent_id = FI-1" in got
    assert "linked by directive_id = D-1" in got or "linked by task_id = T-1" in got


def test_the_trace_is_truthful_about_links_it_cannot_follow():
    outs = _outs()
    outs["audrey"] = {"kind": "NOT_DEPLOYED", "status": 404}
    got = _node("derek", "return AG.trace.render(%s, 'entry_intent_id:FI-1');" % json.dumps(outs))
    assert "Audrey endpoint is not deployed in this build: this link cannot be followed." in got
    nothing = _node("derek", "return AG.trace.render({index: {kind: 'LOCKED'}}, null);")
    assert "Nothing to trace yet." in nothing and "This is not a result." in nothing


@pytest.mark.parametrize("kind", ["index", "derek", "xavier", "audrey"])
def test_every_page_offers_the_trace_view(kind):
    html = P.page_html(kind)
    assert 'id="trace-btn"' in html and 'id="trace"' in html and "AG.trace.render" in html


def test_ids_render_as_trace_links():
    got = _node("derek", "return [AG.fmt('entry_intent_id', 'FI-1'), AG.fmt('task_id', 'T 1')];")
    assert got[0] == '<a class="trl" href="#trace=entry_intent_id:FI-1" title="trace the records linked to this id">FI-1</a>'
    assert "#trace=task_id:T%201" in got[1]


# ── routes ───────────────────────────────────────────────────────────
class _Cfg:
    admin_token = "admin-secret-for-the-agent-pages-test"
    desk_password = "desk"
    operator_password = "operator"
    command_read_password = "desk"
    funded_resolution_key = ""
    funded_resolution_operator = ""


def _client(monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from sportsassets.api import app as A
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    app = FastAPI()
    app.include_router(P.router)
    return TestClient(app, raise_server_exceptions=False)


def test_pages_are_served_only_to_the_command_credential(monkeypatch):
    c = _client(monkeypatch)
    for kind, path in P.PAGE_PATHS.items():
        r = c.get(path)
        assert r.status_code == 401, path
        assert P.LOCKED_TEXT in r.text and 'href="%s"' % P.DESK_PAGE in r.text
        assert "AG.workspace" not in r.text and "DemoProd" not in r.text   # no page code leaks
        r = c.get(path, headers={"X-Admin-Token": _Cfg.admin_token})
        assert r.status_code == 200, path
        assert r.headers["content-type"].startswith("text/html")
        csp = r.headers.get("content-security-policy", "")
        assert "connect-src 'self'" in csp and "default-src 'none'" in csp
        assert "no-store" in r.headers.get("cache-control", "")
        assert _Cfg.admin_token not in r.text          # no secret in any page


def test_every_route_is_the_page_it_names(monkeypatch):
    c = _client(monkeypatch)
    h = {"X-Admin-Token": _Cfg.admin_token}
    for kind in ("derek", "xavier", "audrey", "index"):
        r = c.get(P.PAGE_PATHS[kind], headers=h)
        assert 'data-kind="%s"' % kind in r.text
    r = c.get(P.PAGE_PATHS["demo"], headers=h)
    assert P.REHEARSAL_LABEL in r.text and "window.DemoProd" in r.text


# ── the demo ─────────────────────────────────────────────────────────
DEMO_FILE = REPO / "research" / "demo" / "three_agents_demo.html"


def test_the_committed_standalone_demo_is_the_generated_one():
    assert DEMO_FILE.read_text(encoding="utf-8") == P.demo_html(standalone=True), \
        "regenerate research/demo/three_agents_demo.html from agent_pages.demo_html(standalone=True)"


@pytest.mark.parametrize("standalone", [True, False], ids=["standalone", "served"])
def test_the_demo_is_labelled_rehearsal_on_every_frame(standalone):
    html = P.demo_html(standalone=standalone)
    assert P.REHEARSAL_LABEL in html
    # 14 chapters, each with a scene and an Inspect explanation
    assert html.count("scene: function ()") == 14 and html.count("inspect: '") == 14
    # every rehearsal frame appends the label (frameLabel), and the fixed banner carries it
    assert "return '<div class=\"framelab\"><span>' + LABEL" in html
    assert '<div class="banner" id="banner" role="status">' + P.REHEARSAL_LABEL in html
    # the scripted chat is labelled and never presented as the service
    assert re.search(r"SCRIPTED EXCHANGE (\\u00b7|\u00b7) NOT THE AUDREY SERVICE", html)
    assert re.search(r"AUDREY (\\u00b7|\u00b7) SCRIPTED", html)
    # sound is off by default, and reduced motion is respected
    assert 'id="soundBtn" type="button" aria-pressed="false"' in html
    assert "prefers-reduced-motion" in html and "AudioContext" in html


def test_the_standalone_demo_makes_no_network_call():
    html = P.demo_html(standalone=True)
    for api in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon",
                "import(", "DemoProd = ", "window.DemoProd = ", "AG.load"):
        assert api not in html, api
    assert not re.search(r"https?://", html)
    assert not re.search(r"""(src|href)=["'](?!#)""", html.replace('href="#"', "")), "external reference"
    assert "var SERVED = false;" in html
    assert 'data-mode="production"' not in html


def test_the_served_demo_production_mode_reads_only_same_origin_agent_json():
    html = P.demo_html(standalone=False)
    assert 'data-mode="production"' in html and "var SERVED = true;" in html
    prod = P.DEMO_PROD_JS
    assert "AG.load(URLS[k], window.fetch.bind(window))" in prod
    assert "POST" not in prod and "http" not in prod
    assert re.search(r"URLS = \{index: AG.ENDPOINTS.index, derek: AG.ENDPOINTS.derek, "
                     r"xavier: AG.ENDPOINTS.xavier, audrey: AG.ENDPOINTS.audrey\}", prod)
    for u in _url_literals(html):
        assert _allowed(u), u
    assert not re.search(r"https?://", html)
    # production frames never carry scripted figures: the flying token is off
    assert "if (c && c.flow && S.mode !== 'production')" in html
    # empty production state is stated, with the servicing count
    assert "AG.R.positionsEmpty(sec, {sections: j.sections || {}, url: url})" in prod
    assert "Reading a record is not sending an order." in prod


def test_the_demo_economics_are_the_stated_ones():
    js = P.DEMO_JS
    start, end = js.index("  var THETA = 0.0695;"), js.index("  // ── reveal helpers")
    body = js[start:end] + """
      return {edge: ENTRY.edgePP, gross: ENTRY.gross, ret: ENTRY.grossRet, net: ENTRY.netEV, basis: BASIS,
        chosen: CHOSEN.name, chosenE: CHOSEN.E, chosenWorst: CHOSEN.worst, unpaired: CHOSEN.unp,
        single: RUNGS[0].single, dbl: RUNGS[0].dbl, pair: RUNGS[0].pair, p45both: RUNGS[3].pboth,
        e45: RUNGS[3].alt.E, e25: RUNGS[1].alt.E, w45: RUNGS[3].alt.worst, holdE: HOLD.E, holdY4: HOLD.net.Y4,
        realised: CHOSEN.net.Y4, cand: CAND_CHOSEN.name};"""
    got = _node("derek", "var esc = function (x) { return x; };\n" + body)
    assert abs(got["edge"] - 10) < 1e-9 and abs(got["gross"] - 0.10) < 1e-9
    assert abs(got["ret"] - 0.20) < 1e-9 and abs(got["net"] - 0.082625) < 1e-9
    assert got["basis"] == 15.53
    assert abs(got["pair"] - 1.10) < 1e-9 and abs(got["single"] + 0.10) < 1e-9 and abs(got["dbl"] - 0.90) < 1e-9
    assert got["chosen"] == "Red Sox +1.5 × 25" and got["unpaired"] == 5
    assert got["chosenE"] == 1.55 and got["chosenWorst"] == -5.95
    # +4.5 overlaps more (P(both) 0.456) but is not better: lower EV, worse worst case
    assert abs(got["p45both"] - 0.456) < 1e-9 and got["e45"] < got["e25"] and got["w45"] < got["chosenWorst"]
    assert got["holdE"] == 2.47 and got["realised"] == -0.95 and got["holdY4"] == 14.47
    assert got["cand"].startswith("EXIT")
