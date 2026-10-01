"""PROOF: THE FINISHED PAPER MANAGEMENT VIEWS (owner directive, round 2).

Against a SEEDED SYNTHETIC PAPER SESSION in the real test database
(tests/paper_ops_seed.py: the real ledger, simulator, settlement and learning
functions) and the pages' own JavaScript (node), this file pins:

  1. ONE ACCOUNT, EVERY PAGE. The homepage overview and the Derek, Xavier and
     Audrey operations reads carry the SAME account section, from the one
     function (bettor_paper_ops.account_section -> ledger balances()), and the
     agent pages (CC.ops.accountFigures) and the homepage (paper.js,
     BTPaper.figures) format it to the SAME seven figures.
  2. SIGNED OUT IS A SIGN-IN PROMPT. A 401 or 403 from any read is LOCKED; the
     pages show SIGN-IN REQUIRED with a link to the existing sign-in (the
     COMMAND unlock), never an unexplained feed failure or RECONNECTING; the
     stream's errors are probed with a plain read for exactly that reason;
     reconnect and the 15 s polling fallback stay.
  3. DEREK: readable market names from the decision's recorded instrument
     label (participants, title) -- never a slug as the name; no logos (none
     is licensed in this repository); every entry order and decision with
     purchase price, Pinnacle probability, edge, fees and the refusal reason
     in plain words; strategies separated.
  4. XAVIER: the positions he owns, the current exit recommendation (his
     latest review), exposure, ordinary-completion and exceptional-settlement
     scenario payoffs (probabilities UNMEASURED), and REALIZED profit kept
     apart from the conditional, remaining risk.
  5. AUDREY: the reconciled ledger, her event-driven audits (the learning
     record), performance per strategy, and the improvement proposals with
     their evaluation status (INSUFFICIENT_FORWARD_DATA stated honestly) and
     activation state.
  6. The footer credits the licensed Rocketbox models.

ALL DATA SYNTHETIC, on scratch test accounts. RN1X_TEST_DSN and node are
REQUIRED where used (absence fails).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_ops as OPS
from sportsassets.api import agent_cc_ops as CCO
from sportsassets.api import agent_cc_page as CCP
from sportsassets.api import agent_pages as P

from tests import paper_harness as H
from tests import paper_ops_seed as SEED
from tests.test_command_centre_agent_pages import NODE, _node

NOW = H.T0 + 200.0
KINDS = ("derek", "xavier", "audrey")
FRONT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "command"


async def _seeded(tag):
    if not H.DSN:
        pytest.fail("RN1X_TEST_DSN is not set: these proofs read the real database")
    conn = await H.connect()
    acct = await H.new_account(conn, tag, now=H.T0)
    got = await SEED.seed(conn, acct, t0=H.T0)
    return conn, acct, got


async def _all(conn, acct):
    a = acct["account_id"]
    return {"derek": await OPS.derek_operations(conn, account_id=a, now=NOW),
            "xavier": await OPS.xavier_operations(conn, account_id=a, now=NOW),
            "audrey": await OPS.audrey_operations(conn, account_id=a, now=NOW),
            "home": await OPS.overview(conn, account_id=a, now=NOW)}


def _j(v):
    return json.loads(json.dumps(v, default=str))


def _paper_js_figures(acct: dict) -> list:
    """Run the homepage's paper.js under a minimal DOM stand-in and return
    BTPaper.figures(acct) -- the homepage's own formatting of the account."""
    if not NODE:
        pytest.fail("node is required: these tests execute the pages' own code")
    stub = r"""
      function el() { return {setAttribute: function () {}, appendChild: function () {}, innerHTML: '', style: {}, parentNode: null, className: '', id: ''}; }
      var body = el(); body.insertBefore = function () {}; body.firstChild = null;
      global.document = {createElement: el, body: body, getElementById: function () { return null; },
                         addEventListener: function () {}, hidden: false};
      global.window = global; global.addEventListener = function () {};
      global.fetch = function () { return new Promise(function () {}); };
      global.setInterval = function () { return 0; };
    """
    script = stub + (FRONT / "paper.js").read_text() + (
        "\nprocess.stdout.write(JSON.stringify(window.BTPaper.figures(%s)));" % json.dumps(acct))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ═════════════════════════════════════════════════════════════════════
# 1 · ONE ACCOUNT, THE SAME FIGURES ON ALL FOUR PAGES
# ═════════════════════════════════════════════════════════════════════

async def test_all_four_pages_show_the_same_account_from_the_one_read():
    conn, acct, got = await _seeded("one")
    try:
        r = await _all(conn, acct)
        b = await L.balances(conn, acct["account_id"], now=NOW)
    finally:
        await conn.close()
    secs = {k: v["account"] for k, v in r.items()}
    # the same section on every page, from the one function, OK
    assert all(s["status"] == "OK" for s in secs.values()), secs
    first = secs["home"]["data"]
    for k, s in secs.items():
        assert s["data"] == first, k
    assert first["source"].startswith("bettor_paper_ledger.balances(%s)" % acct["account_id"])
    # and those are the ledger's own figures
    for key, _label in CCO.ACCOUNT_FIGURES:
        assert first[key] == b[key], key
    # the three stamps are on every page too, from three records
    for k, v in r.items():
        assert set(v["freshness"]) == {"server_read", "agent_heartbeat", "ledger"}, k
    # the agent pages and the homepage format it to the SAME seven figures
    pages = _node("derek", "return CC.ops.accountFigures(%s);" % json.dumps(_j(first)))
    home = _paper_js_figures(_j(first))
    assert pages == home
    assert [x["key"] for x in pages] == [k for k, _l in CCO.ACCOUNT_FIGURES]
    assert [x["label"] for x in pages] == ["Cash", "Reserved", "Available", "Position value",
                                           "Equity", "Realized P&L", "Unrealized P&L"]
    assert pages[0]["value"] == "$499,986.80" and pages[5]["value"] == "+$187.20"
    # a figure the ledger does not state is NOT STATED on both, never $0
    nulled = dict(_j(first), total_equity_usd=None, unrealized_pnl_usd=None)
    p2 = _node("xavier", "return CC.ops.accountFigures(%s);" % json.dumps(nulled))
    assert p2 == _paper_js_figures(nulled)
    assert p2[4]["value"] == "NOT STATED" and p2[6]["value"] == "NOT STATED"


@pytest.mark.parametrize("kind", KINDS)
def test_every_agent_page_carries_the_account_strip_and_the_stamps(kind):
    html = P.page_html(kind)
    main = html[html.index('<main id="cc-main"'):]
    assert main.index('id="cc-signin"') < main.index('id="paper-acct"') < main.index('id="paper-fresh"') \
        < main.index('id="cc-stage"')
    assert 'id="cc-signin" role="alert" hidden' in main
    js = CCP.PAPER_BOOT_JS
    assert "O.accountStrip(OPS)" in js and "$('paper-acct')" in js
    app = (FRONT / "paper.js").read_text()
    # the homepage reads its figures from the same overview read, and a
    # ledger frame re-reads it rather than patching the figures
    assert '"/api/command/paper/overview"' in app and "readOverview().then(render, render); }, 800)" in app
    assert "running_balances" not in app


def test_a_rendered_account_strip_shows_the_seven_figures_or_the_reason():
    acct = {"cash_usd": 499000.0, "reserved_usd": 0.0, "available_usd": 499000.0,
            "open_position_value_usd": None, "total_equity_usd": None, "realized_pnl_usd": 0.0,
            "unrealized_pnl_usd": None, "equity_basis": "INCOMPLETE", "last_sequence": 9}
    got = _node("audrey", """
      var a = %s, O = CC.ops;
      return {ok: O.accountStrip({json: {account: {status: 'OK', why: null, data: a}}}),
              un: O.accountStrip({json: {account: {status: 'UNAVAILABLE', why: 'RuntimeError: X', data: null}}}),
              lk: O.accountStrip({json: null, fail: {state: O.SIGNIN, why: '401'}})};
    """ % json.dumps(acct))
    assert got["ok"]["status"] == "OK" and got["ok"]["html"].count("data-acct7=") == 7
    assert got["ok"]["html"].count('<span class="ns">NOT STATED</span>') == 3
    assert got["un"]["status"] == "UNAVAILABLE" and "RuntimeError: X" in got["un"]["html"]
    assert "$" not in got["un"]["html"] and "$" not in got["lk"]["html"]
    assert got["lk"]["status"] == "SIGN-IN REQUIRED"


# ═════════════════════════════════════════════════════════════════════
# 2 · SIGNED OUT: A SIGN-IN PROMPT, NEVER AN UNEXPLAINED FAILURE
# ═════════════════════════════════════════════════════════════════════

def test_401_and_403_are_both_sign_in_and_the_prompt_links_the_sign_in():
    got = _node("derek", """
      function f(st) { return function () { return Promise.resolve({status: st, ok: false, json: function () { return Promise.resolve({}); }}); }; }
      return {a: await AG.load('/api/command/paper/operations?agent=derek', f(401)),
              b: await AG.load('/api/command/paper/operations?agent=derek', f(403)),
              c: await AG.load('/api/command/paper/operations?agent=derek', f(503)),
              framed: CC.ops.signinHtml(true), plain: CC.ops.signinHtml(false, 'the stream answered 401'),
              fail: CC.ops.failure({kind: 'LOCKED', status: 403}, '/x')};
    """)
    assert got["a"]["kind"] == "LOCKED" and got["b"]["kind"] == "LOCKED" and got["b"]["status"] == 403
    assert got["c"]["kind"] == "UNAVAILABLE"                       # an outage is not a sign-in
    assert "SIGN-IN REQUIRED" in got["framed"] and 'href="/" target="_top"' in got["framed"]
    assert "Sign in to COMMAND" in got["framed"] and "retrying every 15 s" in got["framed"]
    assert 'href="%s"' % "/api/command/bettor/desk/page" in got["plain"] and "the stream answered 401" in got["plain"]
    assert got["fail"]["state"] == "SIGN-IN REQUIRED"


def test_the_pages_probe_a_failing_stream_before_saying_reconnecting():
    js = CCP.PAPER_BOOT_JS
    # an EventSource cannot see a 401: its error asks a plain read first
    assert "probeAuth().then(function (gone) { if (gone) schedule(); });" in js
    assert "conn('SIGN-IN REQUIRED'" in js and "signin(true" in js and "signin(false)" in js
    # reconnect with backoff and the polling fallback are kept
    assert "Math.min(30000, 1000 * Math.pow(2, retry++))" in js and "POLL_MS = 15000" in js
    paper = (FRONT / "paper.js").read_text()
    assert "function probeAuth()" in paper and "probeAuth();" in paper
    assert "data-paper-signin" in paper and "window.BTUnlock.open()" in paper
    assert '"SIGN-IN REQUIRED"' in paper and "POLL_MS = 15000" in paper
    unlock = (FRONT / "unlock.js").read_text()
    assert "window.BTUnlock = {" in unlock and "panel(submit)" in unlock
    app = (FRONT / "app.js").read_text()
    assert "data-action=\"signin\"" in app and "BTUnlock.open()" in app
    assert "SIGN-IN REQUIRED</span>Your COMMAND session is missing or has expired" in app
    shell = (FRONT / "agent.html").read_text()
    assert "Sign in to COMMAND" in shell and 'a.href = "/"' in shell


# ═════════════════════════════════════════════════════════════════════
# 3 · DEREK: NAMES, PRICES, PLAIN WORDS, STRATEGIES APART
# ═════════════════════════════════════════════════════════════════════

async def test_derek_names_markets_prices_orders_and_refusals_in_plain_words():
    conn, acct, got = await _seeded("names")
    try:
        d = await OPS.derek_operations(conn, account_id=acct["account_id"], now=NOW)
    finally:
        await conn.close()
    cg = next(s for s in d["strategies"] if s["strategy"] == SEED.CG)
    rows = cg["recent"]["data"]
    for r in rows:
        m = r["market"]
        assert m["source"] == "decision label" and m["logo"] is None
        assert m["title"] and " at " in m["title"] and m["selection"].endswith("to win")
        assert r["us_market_slug"] not in (m["title"] + m["selection"])
    stale = next(r for r in rows if r["refusal"] == "PINNACLE_NOT_FRESH")
    assert stale["refusal_words"] == "the Pinnacle price was too old"
    near = next(r for r in rows if r["refusal"] == "BELOW_MIN_GROSS_EDGE")
    assert near["refusal_words"] == "the edge was below the 5-point minimum"
    enter = next(r for r in rows if r["verdict"] == "ENTER")
    assert enter["purchase_price"] == 0.5 and enter["fees_usd"] == 0.4 and enter["p_pinnacle"] == 0.58
    orders = cg["orders"]["data"]
    assert cg["orders"]["status"] == "OK" and len(orders) == 2
    for o in orders:
        assert o["avg_fill_price"] == 0.5 and o["fees_usd"] == 0.4 and o["edge_pp"] == 7.4
        assert o["p_pinnacle"] == 0.58 and o["market"]["selection"]
    v2 = next(s for s in d["strategies"] if s["strategy"] == SEED.V2)
    assert v2["orders"]["status"] == "EMPTY"                       # entries switched off: no order, named
    assert "logo" in d["logos"] and "no licensed team logo" in d["logos"]
    page = _node("derek", "return CC.ops.panels('derek', {json: %s, okAt: 1, fail: null});" % json.dumps(_j(d)))
    bench = page["p-ops-bench"]["html"]
    names = re.findall(r"data-market-name>(.*?)</div>", bench)
    assert names and all("seed-" not in n for n in names)          # never a slug as the name
    assert "Los Angeles Dodgers to win" in bench or "New York Yankees to win" in bench
    assert "the Pinnacle price was too old" in bench and 'title="PINNACLE_NOT_FRESH"' in bench
    txt = re.sub(r"<[^>]+>", "", bench)
    assert "Entry orders (every one, newest first)" in txt and "purchase price $0.50" in txt
    assert "fees $0.40" in txt and "Pinnacle 0.580" in txt and "edge +7.40 pp" in txt
    assert "<img" not in bench                                       # no logo, licensed or not
    assert "CONDITIONAL · EXPERIMENTAL" in bench and "DEREK_ENTRY_POLICY_V2" not in re.sub(
        r'<details class="tech">.*?</details>', "", bench, flags=re.S).replace(
        "only the PINNACLE_ONLY_PAPER_BENCHMARK may open new paper entries", "")


# ═════════════════════════════════════════════════════════════════════
# 4 · XAVIER: OWNED POSITIONS, RECOMMENDATION, EXPOSURE, SCENARIOS
# ═════════════════════════════════════════════════════════════════════

async def test_xavier_shows_owned_positions_scenarios_and_realized_apart_from_risk():
    conn, acct, got = await _seeded("owned")
    try:
        x = await OPS.xavier_operations(conn, account_id=acct["account_id"], now=NOW)
    finally:
        await conn.close()
    own = {p["group_id"]: p for p in x["owned_positions"]["data"]}
    assert len(own) == 3
    cg2, strict = own[got["entries"]["cg2"]["group_id"]], own[got["entries"]["strict"]["group_id"]]
    # the open completed-game position: recommendation, exposure, scenarios
    assert cg2["status"] == "OPEN" and cg2["recommendation"]["recommendation"] == "HOLD"
    assert cg2["recommendation"]["trigger"] == "SCHEDULED_BACKSTOP"     # his LATEST review
    rm = cg2["remaining"]
    assert rm["open_qty"] == 400.0 and rm["cost_basis_usd"] == 200.4
    assert rm["ordinary_completion"]["win_usd"] == 199.6 and rm["ordinary_completion"]["lose_usd"] == -200.4
    assert rm["economics_label"] == "CONDITIONAL_EXPERIMENTAL_NOT_RISK_ADJUSTED"
    assert rm["conditional_ev_at_decision_usd"] == 18.0
    sc = rm["exceptional_settlement"]
    assert len(sc) == 2 and {c["probability"] for c in sc} == {"UNMEASURED"}
    assert all(c["payoff_range_usd"] == [-200.4, 199.6] for c in sc)
    assert rm["open_management_orders"] == 1
    assert cg2["realized"]["realized_pnl_usd"] == 0.0
    # the settled strict position: realized, and no remaining risk
    assert strict["status"] == "SETTLED: WON" and strict["remaining"] is None
    assert strict["realized"]["realized_pnl_usd"] > 0
    ex = x["exposure"]["data"]
    assert ex["max_loss_usd"] == -200.4 and ex["max_gain_usd"] == 199.6 and ex["open_positions"] == 1
    page = _node("xavier", "return CC.ops.panels('xavier', {json: %s, okAt: 1, fail: null});" % json.dumps(_j(x)))
    h = page["p-ops-handoffs"]["html"]
    assert h.count("Realized profit (booked on the ledger)") == 3
    assert h.count("Remaining risk (conditional, not realized)") == 1
    assert h.count('<b class="unm">UNMEASURED</b>') == 2
    assert "Current recommendation: <b>HOLD</b>" in h and "data-exposure" in h
    assert "wins +$199.60 · loses −$200.40" in h


# ═════════════════════════════════════════════════════════════════════
# 5 · AUDREY: RECONCILED P&L, EVENT AUDITS, PERFORMANCE, PROPOSALS
# ═════════════════════════════════════════════════════════════════════

async def test_audrey_reads_reconciliation_event_audits_performance_and_proposals():
    conn, acct, got = await _seeded("audit")
    try:
        a = await OPS.audrey_operations(conn, account_id=acct["account_id"], now=NOW)
    finally:
        await conn.close()
    rc = a["reconciliation"]["data"]
    assert a["reconciliation"]["status"] == "OK" and rc["reconciled"] is True and not rc["failed_checks"]
    assert len(rc["checks"]) == 8
    perf = {r["strategy"]: r for r in a["performance_by_strategy"]["data"]}
    assert perf[SEED.STRICT]["won"] == 1 and perf[SEED.STRICT]["realized_pnl_usd"] > 0
    assert perf[SEED.CG]["settled"] == {"SETTLED_AT_VENUE_PRICE": 1} and perf[SEED.CG]["open_positions"] == 1
    assert perf[SEED.V2]["decisions"] == 4 and perf[SEED.V2]["entry_orders"] == 0
    kinds = {e["kind"] for e in a["event_audits"]["data"]}
    assert {"PAPER_EVENT_FIRST_FILL", "PAPER_EVENT_HANDOFF", "PAPER_EVENT_SETTLEMENT",
            "PAPER_EVENT_SETTLED_AT_VENUE_PRICE"} <= kinds
    lr = a["learning"]["data"]
    # The owner's 5.0 pp floor (round 3 of the learning work): Derek's
    # near-miss lesson cannot propose a LOWER threshold, so no proposal
    # exists for him -- the read must say so (no data), never invent one.
    dk = lr["agents"]["DEREK"]["data"]
    assert dk is None or dk.get("proposal") is None or \
        dk["evaluation"]["status"] == "INSUFFICIENT_FORWARD_DATA"
    assert dk is None or dk.get("active") in (False, None)
    page = _node("audrey", "return CC.ops.panels('audrey', {json: %s, okAt: 1, fail: null});" % json.dumps(_j(a)))
    assert 'data-reconciled="yes"' in page["p-ops-account"]["html"] and "LEDGER RECONCILED" in page["p-ops-account"]["html"]
    assert "8 of 8 checks pass" in page["p-ops-account"]["html"]
    assert page["p-ops-performance"]["html"].count("data-perf=") == 3
    assert "Event-driven audits (one per event)" in page["p-ops-findings"]["html"]
    lh = page["p-ops-learning"]["html"]
    # no proposal below the owner's 5.0 pp floor is ever shown (none exists)
    assert "Forward records only" in lh
    assert "from 5 to 4" not in lh and "to 4 percentage points" not in lh
    assert "activation: <b>ACTIVE</b>" not in lh
    # a learning read that fails is UNAVAILABLE by name, never an empty list
    bad = _node("audrey", "return CC.ops.learningPanel({learning: {status: 'UNAVAILABLE', why: 'MIGRATION_185_IS_NOT_APPLIED', data: null}});")
    assert bad["status"] == "UNAVAILABLE" and "MIGRATION_185_IS_NOT_APPLIED" in bad["html"]


# ═════════════════════════════════════════════════════════════════════
# 6 · THE CHARACTER CREDIT, AND RAW IDS UNDER TECHNICAL DETAILS
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("kind", KINDS)
def test_the_footer_credits_the_licensed_rocketbox_models(kind):
    html = P.page_html(kind)
    foot = re.search(r'<p class="foot">(.*?)</p>', html, re.S).group(1)
    assert "licensed Microsoft Rocketbox models" in foot and "original stylised illustration" not in foot
    assert "Rocketbox" in foot                          # the per-model credit follows (credit_html)


def test_raw_identifiers_sit_under_technical_details():
    d = {"decision_id": "paperdec:abc", "us_market_slug": "aec-mlb-nyy-bos-2026-10-02", "intent": "ORDER_INTENT_BUY_LONG",
         "strategy": SEED.CG, "verdict": "REFUSE", "refusal": "PINNACLE_NOT_FRESH",
         "refusal_words": "the Pinnacle price was too old",
         "market": {"title": "New York Yankees at Boston Red Sox", "selection": "New York Yankees to win", "source": "decision label"}}
    html = _node("derek", "return CC.ops.decRow(%s);" % json.dumps(d))
    visible = re.sub(r"<[^>]+>", " ", re.sub(r'<details class="tech">.*?</details>', "", html, flags=re.S))
    assert "aec-mlb-nyy-bos-2026-10-02" not in visible and "paperdec:abc" not in visible
    assert "New York Yankees to win" in visible and "the Pinnacle price was too old" in visible
    tech = re.search(r'<details class="tech">(.*?)</details>', html, re.S).group(1)
    assert 'data-copy="aec-mlb-nyy-bos-2026-10-02"' in tech and 'data-copy="paperdec:abc"' in tech
