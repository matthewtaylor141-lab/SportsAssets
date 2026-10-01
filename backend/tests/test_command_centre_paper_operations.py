"""PROOF: THE MANAGEMENT SITE LEADS WITH THE ACTIVE PAPER EXPERIMENT.

The owner's acceptance failure (signed in on mobile Safari): /derek showed
zero decisions while the paper session held hundreds; /xavier showed
FUNDED_LANE_NOT_CONFIGURED; /audrey "no funded book"; the homepage
DISCONNECTED / FEED UNAVAILABLE -- funded-system status rendered under a
paper banner. This file pins the fix against a SEEDED SYNTHETIC PAPER SESSION
in the real test database (tests/paper_ops_seed.py, through the real ledger,
simulator and settlement functions) and the pages' own JavaScript (node):

  * the operational read (bettor_paper_ops) returns the session's records:
    Derek's strategies APART (the original two-model research policy, then
    each EXPERIMENTAL benchmark with its policy version, its disclosure and
    -- for the completed-game policy -- its CONDITIONAL / EXPERIMENTAL
    economics label), counts, refusals, recent decisions with verdict,
    refusal, edge and explanation, and the eligibility funnel from each
    decision's own conditions; every handoff to Xavier with its strategy, his
    reviews, standing protection orders, settlements (SETTLED_AT_VENUE_PRICE
    included) and what is pending; Audrey's account, P&L by strategy,
    findings and daily report; THREE separate freshness stamps;
  * the pages render that output: paper first, strategies separated, the
    funded system in its own collapsed "Funded system (inactive)" section
    after it, never the headline;
  * A FAILED READ IS UNAVAILABLE WITH ITS REASON, NEVER A ZERO: a failing
    query, a failed GET, a 401 (SIGN-IN REQUIRED) and an absent schema;
  * the homepage COMMAND app and the agent shell (frontend files) lead with
    the paper experiment, label the funded feed, and recover on a phone.

ALL DATA SYNTHETIC, on scratch test accounts; nothing touches the live paper
account. RN1X_TEST_DSN and node are REQUIRED where used (absence fails).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_ops as OPS
from sportsassets.api import agent_cc_ops as CCO
from sportsassets.api import agent_cc_page as CCP
from sportsassets.api import agent_pages as P

from tests import paper_harness as H
from tests import paper_ops_seed as SEED
from tests.test_command_centre_agent_pages import _Cfg, _node

DSN = H.DSN
NOW = H.T0 + 200.0
KINDS = ("derek", "xavier", "audrey")
FRONT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "command"


def _need_db():
    if not DSN:
        pytest.fail("RN1X_TEST_DSN is not set: these proofs read the real database")


async def _seeded(tag="ops"):
    _need_db()
    conn = await H.connect()
    acct = await H.new_account(conn, tag, now=H.T0)
    got = await SEED.seed(conn, acct, t0=H.T0)
    return conn, acct, got


def _j(v):
    return json.loads(json.dumps(v, default=str))


def _strat(payload, key):
    return next(s for s in payload["strategies"] if s["strategy"] == key)


# ═════════════════════════════════════════════════════════════════════
# THE OPERATIONAL READ AGAINST A SEEDED PAPER SESSION
# ═════════════════════════════════════════════════════════════════════

async def test_derek_strategies_are_counted_apart_with_versions_labels_and_funnel():
    conn, acct, got = await _seeded("derek")
    try:
        d = await OPS.derek_operations(conn, account_id=acct["account_id"], now=NOW)
    finally:
        await conn.close()
    assert [s["strategy"] for s in d["strategies"]] == [SEED.V2, SEED.STRICT, SEED.CG]
    assert [s["kind"] for s in d["strategies"]] == ["ORIGINAL_RESEARCH", "EXPERIMENTAL_BENCHMARK",
                                                    "EXPERIMENTAL_BENCHMARK"]
    v2, strict, cg = (_strat(d, k) for k in (SEED.V2, SEED.STRICT, SEED.CG))
    # counts, each strategy on its own: never one of the others' decisions
    assert v2["counts"]["status"] == "OK" and v2["counts"]["data"]["decisions"] == 4
    assert v2["counts"]["data"]["enter"] == 0 and v2["counts"]["data"]["refuse"] == 4
    assert strict["counts"]["data"]["decisions"] == 3 and strict["counts"]["data"]["enter"] == 1
    assert cg["counts"]["data"]["decisions"] == 3 and cg["counts"]["data"]["enter"] == 2
    for s in (v2, strict, cg):
        ids = {r["decision_id"] for r in s["recent"]["data"]}
        assert ids == set(got["decisions"][s["strategy"]]), s["strategy"]
        assert {r["strategy"] for r in s["recent"]["data"]} == {s["strategy"]}
    # versions and labels: from the policy module, and on the records
    assert cg["counts"]["data"]["policy_versions"] == ["PINNACLE_COMPLETED_GAME_PAPER_V1"]
    assert strict["counts"]["data"]["policy_versions"] == ["PINNACLE_ONLY_PAPER_BENCHMARK_V1"]
    assert cg["version"] == "PINNACLE_COMPLETED_GAME_PAPER_V1" and cg["label"] == "CONDITIONAL · EXPERIMENTAL"
    assert cg["economics_label"] == "CONDITIONAL_EXPERIMENTAL_NOT_RISK_ADJUSTED"
    assert "CONDITIONAL" in cg["disclosure"] and "NOT evidence of qualified" in strict["disclosure"]
    assert v2["label"] == "RESEARCH" and v2["economics_label"] is None
    # recent decisions carry verdict, refusal, edge and the explanation
    enter = next(r for r in cg["recent"]["data"] if r["verdict"] == "ENTER")
    assert enter["edge_pp"] == 7.4 and enter["net_ev_usd"] == 18.0
    assert enter["explanation"].startswith("ENTER (conditional on ordinary completion)")
    assert "conditional, experimental" in enter["explanation"]
    ref = next(r for r in v2["recent"]["data"] if r["refusal"] == "STRATEGY_ENTRIES_DISABLED")
    assert "paper entry switch is off" in ref["explanation"]
    assert {r["refusal"]: r["n"] for r in v2["refusals"]["data"]} == {
        "INTERNAL_MODEL_NOT_QUALIFIED": 2, "BELOW_MIN_EDGE": 1, "STRATEGY_ENTRIES_DISABLED": 1}
    # THE FUNNEL, from each decision's own conditions, then the pipeline
    st = {x["stage"]: x for x in cg["funnel"]["data"]["stages"]}
    assert st["decisions recorded"]["n"] == 3
    assert st["contract_outcome_settlement_match"]["n"] == 3
    assert st["pinnacle_fresh_at_the_decision_instant"]["n"] == 2
    assert st["pinnacle_fresh_at_the_decision_instant"]["failed_here"] == 1
    assert st["positive_ev_after_fees"]["n"] == 2
    assert st["verdict ENTER"]["n"] == 2 and st["paper entry orders"]["n"] == 2
    assert st["orders with a simulated fill"]["n"] == 2 and st["handed to Xavier"]["n"] == 2
    sv = {x["stage"]: x for x in strict["funnel"]["data"]["stages"]}
    assert sv["contract_outcome_settlement_match"]["n"] == 2          # one stopped at the match
    assert sv["edge_at_least_5pp_at_every_level_used"]["n"] == 1      # one more at the edge
    v2f = {x["stage"]: x for x in v2["funnel"]["data"]["stages"]}
    assert v2f["internal_model_qualified"]["n"] == 2 and v2f["paper entry orders"]["n"] == 0
    # the switches, read as they are
    keys = {r["control_key"]: r["enabled"] for r in d["controls"]["data"]}
    assert keys["PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2"] is False


async def test_xavier_sees_every_handoff_his_reviews_protection_settlements_and_pending():
    conn, acct, got = await _seeded("xavier")
    try:
        x = await OPS.xavier_operations(conn, account_id=acct["account_id"], now=NOW)
    finally:
        await conn.close()
    hs = x["handoffs"]["data"]
    assert x["handoffs"]["status"] == "OK" and len(hs) == 3
    assert sorted(h["strategy"] for h in hs) == [SEED.CG, SEED.CG, SEED.STRICT]
    assert all(h["us_market_slug"] and h["confirmed_qty"] == 400.0 for h in hs)
    assert {r["strategy"]: r["n"] for r in x["handoff_counts"]["data"]} == {SEED.CG: 2, SEED.STRICT: 1}
    assert len(x["reviews"]["data"]) == 4 and {r["strategy"] for r in x["reviews"]["data"]} == {SEED.CG, SEED.STRICT}
    prot = [o for o in x["standing_orders"]["data"] if o["role"] == "STANDING_PROTECTION"]
    assert len(prot) == 1 and prot[0]["open"] is True and prot[0]["strategy"] == SEED.CG
    outcomes = {(r["strategy"], r["outcome"]): r["n"] for r in x["settlement_counts"]["data"]}
    assert outcomes == {(SEED.STRICT, "WON"): 1, (SEED.CG, "SETTLED_AT_VENUE_PRICE"): 1}
    pend = x["pending_settlements"]
    assert pend["status"] == "OK" and pend["data"]["count"] == 1
    assert pend["data"]["positions"][0]["group_id"] == got["entries"]["cg2"]["group_id"]
    assert pend["data"]["last_settle_step"]["waiting"] == 1
    assert pend["data"]["last_settle_step"]["settled_at_venue_price"] == 1
    assert len(x["positions"]["data"]) == 1


async def test_audrey_reads_the_account_pnl_by_strategy_findings_and_report():
    conn, acct, got = await _seeded("audrey")
    try:
        a = await OPS.audrey_operations(conn, account_id=acct["account_id"], now=NOW)
        b = await L.balances(conn, acct["account_id"], now=NOW)
    finally:
        await conn.close()
    ac = a["account"]["data"]
    for k in ("cash_usd", "reserved_usd", "available_usd", "total_equity_usd", "realized_pnl_usd",
              "unrealized_pnl_usd", "last_sequence"):
        assert ac[k] == b[k], k
    rows = {r["strategy"]: r for r in a["pnl_by_strategy"]["data"]}
    assert set(rows) == {SEED.STRICT, SEED.CG}                   # no position, no row: never a zero row
    assert round(sum(r["realized_pnl_usd"] for r in rows.values()), 6) == b["realized_pnl_usd"]
    assert rows[SEED.STRICT]["closed_positions"] == 1 and rows[SEED.STRICT]["realized_pnl_usd"] > 0
    assert rows[SEED.CG]["open_positions"] == 1 and rows[SEED.CG]["closed_positions"] == 1
    assert rows[SEED.CG]["unrealized_pnl_usd"] == b["unrealized_pnl_usd"]
    assert rows[SEED.CG]["kind"] == "EXPERIMENTAL_BENCHMARK"
    assert a["daily_report"]["status"] == "OK" and a["daily_report"]["data"]["reconciles"] is True
    assert a["daily_report"]["data"]["benchmark_sections"] == ["pinnacle_completed_game_paper"]
    assert len(a["findings"]["data"]) == 2
    assert {r["severity"] for r in a["finding_counts"]["data"]} == {"INFO", "WARNING"}


async def test_the_three_freshness_stamps_come_from_three_different_records():
    conn, acct, got = await _seeded("fresh")
    try:
        o = await OPS.overview(conn, account_id=acct["account_id"], now=NOW)
        last = await conn.fetchrow("SELECT max(seq) AS s, max(committed_at) AS at FROM paper_ledger "
                                   " WHERE account_id=$1", acct["account_id"])
    finally:
        await conn.close()
    fr = o["freshness"]
    assert fr["server_read"]["data"]["at"] == NOW
    hb = fr["agent_heartbeat"]["data"]
    assert fr["agent_heartbeat"]["status"] == "OK" and hb["heartbeat_at"] == H.T0 + 120
    assert hb["passes"] == 1 and hb["stale"] is False and "paper_session_health" in hb["source"]
    lg = fr["ledger"]["data"]
    assert lg["sequence"] == int(last["s"]) and lg["committed_at"] == L._epoch(last["at"])
    assert lg["committed_at"] != hb["heartbeat_at"] != NOW              # three records, three times
    # the homepage's agents: strategies apart, Xavier and Audrey from their own tables
    by = o["agents"]["derek"]["data"]["by_strategy"]
    assert by[SEED.V2]["decisions"] == 4 and by[SEED.V2]["kind"] == "ORIGINAL_RESEARCH"
    assert by[SEED.CG]["enter"] == 2 and by[SEED.CG]["kind"] == "EXPERIMENTAL_BENCHMARK"
    xv = o["agents"]["xavier"]["data"]
    assert xv["handoffs"] == 3 and xv["pending_settlement"] == 1 and xv["open_management_orders"] == 1
    assert o["agents"]["audrey"]["data"]["findings"] == 2
    assert o["account"]["data"]["cash_usd"] == got["balances"]["cash_usd"]


class _Failing:
    """A connection whose reads of one table fail: the read must say so."""

    def __init__(self, conn, needle):
        self._c, self._n = conn, needle

    def __getattr__(self, k):
        f = getattr(self._c, k)
        if k not in ("fetch", "fetchrow", "fetchval"):
            return f

        async def g(sql, *a, **kw):
            if self._n in sql:
                raise ConnectionError("SYNTHETIC_READ_FAILURE on " + self._n)
            return await f(sql, *a, **kw)
        return g


async def test_a_failing_read_is_unavailable_with_its_reason_never_a_zero():
    conn, acct, _got = await _seeded("fail")
    try:
        d = await OPS.derek_operations(_Failing(conn, "paper_decisions"), account_id=acct["account_id"], now=NOW)
        x = await OPS.xavier_operations(_Failing(conn, "paper_settlements"), account_id=acct["account_id"], now=NOW)
        o = await OPS.overview(_Failing(conn, "paper_ledger"), account_id=acct["account_id"], now=NOW)
    finally:
        await conn.close()
    for s in d["strategies"]:
        for part in ("counts", "recent", "refusals", "funnel"):
            assert s[part]["status"] == "UNAVAILABLE", (s["strategy"], part)
            assert "SYNTHETIC_READ_FAILURE" in s[part]["why"] and s[part]["data"] is None
    assert d["controls"]["status"] == "OK"                  # an unrelated read is unaffected
    assert x["settlement_counts"]["status"] == "UNAVAILABLE" and x["settlements"]["data"] is None
    assert x["handoffs"]["status"] == "OK"
    assert o["freshness"]["ledger"]["status"] == "UNAVAILABLE"
    assert o["freshness"]["agent_heartbeat"]["status"] == "OK"
    # and the page renders it as UNAVAILABLE with the reason: no count, no zero
    got = _node("derek", """
      var D = %s, ok = function (j) { return {json: j, okAt: 1, fail: null}; };
      return {panels: CC.ops.panels('derek', ok(D)), kpis: CC.ops.kpis('derek', ok(D))};
    """ % json.dumps(_j(d)))
    for pid in ("p-ops-bench", "p-ops-research"):
        p = got["panels"][pid]
        assert p["status"] == "UNAVAILABLE", pid
        assert "SYNTHETIC_READ_FAILURE" in p["html"] and "not a zero" in p["html"]
        assert '<div class="lbl">Decisions</div><div class="v">' not in p["html"]
    assert [k["value"] for k in got["kpis"][:3]] == ["UNAVAILABLE"] * 3


async def test_an_absent_schema_is_unavailable_by_name_on_the_route(monkeypatch):
    """The routes answer UNAVAILABLE MIGRATION_171_IS_NOT_APPLIED without the
    paper tables, and the page renders it so."""
    from sportsassets.api import command_paper as CP

    class NoSchema:
        async def fetchval(self, sql, *a):
            assert "to_regclass" in sql
            return False

    class Pool:
        def acquire(self):
            import contextlib

            @contextlib.asynccontextmanager
            async def a():
                yield NoSchema()
            return a()

    async def pool():
        return Pool()
    monkeypatch.setattr(CP, "_pool", pool)
    r = await CP.paper_operations(agent="xavier", limit=25)
    assert r["operations"] == {"status": "UNAVAILABLE", "why": "MIGRATION_171_IS_NOT_APPLIED", "data": None}
    ov = await CP.paper_overview()
    assert ov["overview"]["status"] == "UNAVAILABLE"
    got = _node("xavier", """
      var o = {kind: 'OK', json: %s}, f = CC.ops.failure(o, '/api/command/paper/operations?agent=xavier');
      return {f: f, p: CC.ops.panels('xavier', {json: null, fail: f})};
    """ % json.dumps(r))
    assert got["f"] == {"state": "UNAVAILABLE", "why": "MIGRATION_171_IS_NOT_APPLIED"}
    assert all(v["status"] == "UNAVAILABLE" and "MIGRATION_171_IS_NOT_APPLIED" in v["html"]
               for v in got["p"].values())


async def test_the_operations_route_serves_the_session_to_a_command_session_only(monkeypatch):
    import httpx
    from fastapi import FastAPI
    from sportsassets import config
    from sportsassets.api import app as A
    from sportsassets.api import command_paper as CP
    from sportsassets.db import close_pool
    conn, acct, _got = await _seeded("route")
    await conn.close()
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    monkeypatch.setenv("DATABASE_URL", DSN)
    config.settings.cache_clear()
    monkeypatch.setattr(L, "ACCOUNT_ID", acct["account_id"])
    app = FastAPI()
    app.include_router(CP.router)
    token, _ = A.mint_desk_token()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/api/command/paper/operations?agent=derek")).status_code == 401
            assert (await c.get("/api/command/paper/overview")).status_code == 401
            c.cookies.set("bt_command", token)
            assert (await c.get("/api/command/paper/operations?agent=funded")).status_code == 422
            r = await c.get("/api/command/paper/operations?agent=derek")
            assert r.status_code == 200
            j = r.json()
            assert j["agent"] == "derek" and j["account_id"] == acct["account_id"]
            assert [s["counts"]["data"]["decisions"] for s in j["strategies"]] == [4, 3, 3]
            for k in ("xavier", "audrey"):
                r = await c.get("/api/command/paper/operations?agent=" + k)
                assert r.status_code == 200 and r.json()["agent"] == k
            o = (await c.get("/api/command/paper/overview")).json()
            assert o["agents"]["xavier"]["data"]["handoffs"] == 3
    finally:
        await close_pool()


# ═════════════════════════════════════════════════════════════════════
# THE PAGES: PAPER FIRST, STRATEGIES APART, FUNDED SECONDARY
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("kind", KINDS)
def test_every_page_leads_with_the_paper_view_and_keeps_the_funded_system_secondary(kind):
    html = P.page_html(kind)
    main = html[html.index('<main id="cc-main"'):]
    # the order: the paper banner, the three stamps, the hero, the paper
    # operations, the paper account and ledger, THEN the funded section
    order = ['id="paper-banner"', 'id="paper-fresh"', 'id="cc-stage"', 'id="paper-ops"',
             'id="p-paper-account"', '<details class="cc-funded" id="funded">']
    pos = [main.index(x) for x in order]
    assert pos == sorted(pos), list(zip(order, pos))
    for pid, _t, _s in CCO.OPS_PANELS[kind]:
        assert main.index('id="%s"' % pid) < main.index('id="funded"'), pid
    # every funded panel, its key figures and the funded workspace record sit
    # INSIDE the funded section, labelled inactive
    funded = main[main.index('<details class="cc-funded"'):main.index("</details>", main.index('id="full-record"'))]
    assert "Funded system (inactive)" in funded and 'id="cc-funded-kpis"' in funded
    for pid, _t, _s, _c in CCP._PANELS[kind]:
        assert 'id="%s"' % pid in funded, pid
    assert 'id="app"' in funded and 'id="cc-funded-state"' in funded
    assert '<details class="cc-funded" id="funded" open' not in html     # collapsed by default
    # the brief's key figures are the paper session's
    brief = main[main.index('id="cc-brief"'):main.index('id="paper-ops"')]
    assert 'id="cc-kpis"' in brief and "Paper session &#183; key figures" in brief
    # three stamps, by name
    for label in ("Last successful server read", "Last agent heartbeat (paper runtime)",
                  "Last ledger transaction"):
        assert label in html, label
    assert "'paper_operations': '/api/command/paper/operations'" in html
    # still one data script and one module (the character stays separate)
    assert len(re.findall(r"<script>(.*?)</script>", html, re.S)) == 2
    assert "500,000" not in html and "500000" not in html


@pytest.mark.parametrize("kind", KINDS)
def test_no_label_or_status_text_sits_on_the_character_figure(kind):
    html = P.page_html(kind)
    stage = re.search(r'<section class="cc-stage".*?</section>', html, re.S).group(0)
    # the figure area holds the canvas, the 2D portrait and a screen-reader
    # text only: the name, status, placeholder caption and pause control are
    # outside it (above or below)
    for outside in ('class="cc-ov"', 'class="cc-st"', 'id="cc-placeholder"', 'id="cc-fbnote"',
                    'id="cc-pause"', "<noscript>"):
        assert outside not in stage, outside
        assert outside in html, outside
    wrap = html[html.index('<div class="cc-stagewrap"'):html.index('id="cc-brief"')]
    assert wrap.index('class="cc-ov"') < wrap.index('class="cc-stage"') < wrap.index('class="cc-st"')
    css = CCO.OPS_CSS
    assert ".cc-stagewrap:has(.cc-real-model) .cc-placeholder{display:none}" in css
    assert ".cc-stagewrap:has(.cc-3d-on) .fbnote{display:none}" in css
    assert ".cc-stagewrap .cc-st{position:static" in css


def test_the_page_boot_polls_without_the_stream_and_recovers_on_a_phone():
    js = CCP.PAPER_BOOT_JS
    assert "POLL_MS = 15000" in js and "streamOpen() ? LIVE_MS : POLL_MS" in js
    assert "addEventListener('visibilitychange'" in js and "addEventListener('pageshow'" in js
    assert "e && e.persisted" in js and "addEventListener('online'" in js
    assert "Math.min(30000, 1000 * Math.pow(2, retry++))" in js       # exponential backoff
    assert "E.paper_operations + '?agent='" in js
    assert "credentials: 'same-origin'" in P.CORE_JS                 # every read is same-origin
    assert "setInterval(function () { if (!document.hidden) load(); }, 60000)" not in js


async def test_the_pages_render_the_seeded_session_strategies_apart():
    conn, acct, _got = await _seeded("render")
    try:
        d = await OPS.derek_operations(conn, account_id=acct["account_id"], now=NOW)
        x = await OPS.xavier_operations(conn, account_id=acct["account_id"], now=NOW)
        a = await OPS.audrey_operations(conn, account_id=acct["account_id"], now=NOW)
    finally:
        await conn.close()
    st = "function st(j) { return {json: j, okAt: j.as_of, tryAt: j.as_of, fail: null, stream: 'LIVE'}; }"
    got = _node("derek", st + """
      var D = %s, X = %s, A = %s, now = D.as_of;
      return {d: CC.ops.panels('derek', st(D)), dk: CC.ops.kpis('derek', st(D)), x: CC.ops.panels('xavier', st(X)),
              xk: CC.ops.kpis('xavier', st(X)), a: CC.ops.panels('audrey', st(A)), ak: CC.ops.kpis('audrey', st(A)),
              fresh: CC.ops.fresh(st(D), now), mode: CC.ops.mode(st(D), now),
              stale: CC.ops.mode(st(D), now + 3600)};
    """ % (json.dumps(_j(d)), json.dumps(_j(x)), json.dumps(_j(a))))
    bench, research = got["d"]["p-ops-bench"]["html"], got["d"]["p-ops-research"]["html"]
    assert got["d"]["p-ops-bench"]["status"] == "OK" and got["d"]["p-ops-research"]["status"] == "OK"
    # SEPARATED: each panel carries only its own strategies' blocks and rows
    assert 'data-strategy="PINNACLE_ONLY_PAPER_BENCHMARK" data-kind="EXPERIMENTAL_BENCHMARK"' in bench
    assert 'data-strategy="PINNACLE_COMPLETED_GAME_PAPER" data-kind="EXPERIMENTAL_BENCHMARK"' in bench
    assert 'data-strategy="DEREK_ENTRY_POLICY_V2"' not in bench
    assert 'data-strategy="DEREK_ENTRY_POLICY_V2" data-kind="ORIGINAL_RESEARCH"' in research
    assert "PINNACLE_" not in re.sub(r"<details class=\"tech\">.*?</details>", "", research, flags=re.S).replace(
        "only the PINNACLE_ONLY_PAPER_BENCHMARK may open new paper entries", "")
    assert bench.count('<details class="dec"') == 6 and research.count('<details class="dec"') == 4
    # versions and labels, the CG policy's conditional / experimental label
    assert "PINNACLE_COMPLETED_GAME_PAPER_V1" in bench and "PINNACLE_ONLY_PAPER_BENCHMARK_V1" in bench
    assert "CONDITIONAL · EXPERIMENTAL" in bench and "CONDITIONAL_EXPERIMENTAL_NOT_RISK_ADJUSTED" in bench
    assert "Eligibility funnel" in bench and "handed to Xavier" in bench and "stopped here" in bench
    assert "ENTER (conditional on ordinary completion)" in bench and "+7.40 pp" in bench and "+$18.00" in bench
    assert "STRATEGY_ENTRIES_DISABLED" in research and "Its paper entry switch is <b>OFF</b>" in research
    assert [k["value"] for k in got["dk"][:3]] == ["4", "3", "3"]
    # Xavier: every handoff with its strategy, reviews, protection, settlements, pending
    xh = got["x"]["p-ops-handoffs"]["html"]
    assert xh.count('<div class="orow" data-group=') == 3 and xh.count('data-strategy="PINNACLE_COMPLETED_GAME_PAPER"') == 2
    assert '<span class="ostat WON">WON</span>' in xh and '<span class="ostat SETTLED_AT_VENUE_PRICE">' in xh
    assert "OPEN · 400 held" in xh
    assert "STANDING_PROTECTION" in got["x"]["p-ops-protection"]["html"]
    xs = got["x"]["p-ops-settlements"]["html"]
    assert "SETTLED_AT_VENUE_PRICE" in xs and "PENDING" in xs and "waiting 1" in xs
    assert got["x"]["p-ops-reviews"]["html"].count('data-review=') == 4
    assert [k["value"] for k in got["xk"]] == ["3", "1", "1", "2"]
    # Audrey: the account, P&L by strategy, findings, the daily report
    aa = got["a"]["p-ops-account"]["html"]
    assert 'data-acct="cash_usd"' in aa and 'data-pnl="PINNACLE_ONLY_PAPER_BENCHMARK"' in aa
    assert 'data-pnl="PINNACLE_COMPLETED_GAME_PAPER"' in aa and 'data-pnl="DEREK_ENTRY_POLICY_V2"' not in aa
    assert got["a"]["p-ops-findings"]["html"].count("data-finding=") == 2
    assert "reconciles" in got["a"]["p-ops-report"]["html"]
    assert got["ak"][3]["label"] == "Latest daily report"
    assert got["ak"][3]["value"] == a["daily_report"]["data"]["report_day"]
    # the stamps and the pose follow the paper runtime, not the read
    f = got["fresh"]
    assert f["heartbeat"]["chip"] == "RECENT" and "1 pass(es)" in f["heartbeat"]["sub"]
    assert "an unchanged balance is not a stale one" in f["ledger"]["sub"]
    assert "A recent read is not agent health." in f["read"]["sub"]
    assert got["mode"]["mode"] == "monitoring" and got["stale"]["mode"] == "unavailable"
    assert "heartbeat stale" in got["stale"]["why"]


def test_a_failed_or_unauthenticated_read_is_never_drawn_as_zero():
    got = _node("audrey", """
      var u = '/api/command/paper/operations?agent=audrey', O = CC.ops;
      var locked = O.failure({kind: 'LOCKED', status: 401}, u), down = O.failure({kind: 'UNAVAILABLE', why: 'HTTP 503 · SYNTHETIC'}, u),
          gone = O.failure({kind: 'NOT_DEPLOYED', status: 404}, u);
      var old = {as_of: 1790000000, account: {status: 'OK', why: null, data: {cash_usd: 499000, reserved_usd: 0, available_usd: 499000, total_equity_usd: null, realized_pnl_usd: 0, unrealized_pnl_usd: null, equity_basis: 'INCOMPLETE'}},
                 freshness: {agent_heartbeat: {status: 'UNAVAILABLE', why: 'X', data: null}, ledger: {status: 'EMPTY', why: 'NO_LEDGER', data: null}}};
      return {locked: locked, down: down, gone: gone,
              lp: O.panels('audrey', {json: null, fail: locked}), lk: O.kpis('audrey', {json: null, fail: locked}),
              lf: O.fresh({json: null, fail: locked}, 1790000000),
              dp: O.panels('audrey', {json: null, fail: down}), dk: O.kpis('audrey', {json: null, fail: down}),
              keep: O.panels('audrey', {json: old, okAt: 1790000000, fail: down}), kf: O.fresh({json: old, okAt: 1790000000, fail: down}, 1790000030)};
    """)
    assert got["locked"]["state"] == "SIGN-IN REQUIRED" and "401" in got["locked"]["why"]
    assert got["gone"]["state"] == "UNAVAILABLE" and "404" in got["gone"]["why"]
    for p in got["lp"].values():
        assert p["status"] == "SIGN-IN REQUIRED" and "SIGN-IN REQUIRED" in p["html"] and "$" not in p["html"]
    assert got["lk"] == [{"label": "Paper view", "value": "SIGN-IN REQUIRED", "sub": got["lk"][0]["sub"]}]
    assert {s["value"] for k, s in got["lf"].items() if k != "conn"} == {"SIGN-IN REQUIRED"}
    for p in got["dp"].values():
        assert p["status"] == "UNAVAILABLE" and "HTTP 503 · SYNTHETIC" in p["html"]
        assert not re.search(r"\$\d|>0<", p["html"])
    assert got["dk"][0]["value"] == "UNAVAILABLE"
    # a later failed poll keeps the last good read ON SCREEN, says so, and
    # still never invents a figure: equity stays NOT STATED, not $0
    k = got["keep"]["p-ops-account"]["html"]
    assert "LAST READ FAILED · UNAVAILABLE: HTTP 503 · SYNTHETIC" in k and "$499,000.00" in k
    assert '<div data-acct="total_equity_usd"><div class="lbl">Total equity</div><div class="v"><span class="ns">NOT STATED</span>' in k
    assert got["kf"]["heartbeat"]["value"] == "UNAVAILABLE" and got["kf"]["ledger"]["value"] == "NO LEDGER ENTRY"
    assert got["kf"]["read"]["chip"] == "UNAVAILABLE" and "30 s ago" in got["kf"]["read"]["value"]


# ═════════════════════════════════════════════════════════════════════
# THE FRONTEND (Netlify) FILES: THE HOMEPAGE AND THE AGENT SHELL
# ═════════════════════════════════════════════════════════════════════

def test_the_homepage_leads_with_the_paper_experiment_and_labels_the_funded_feed():
    paper = (FRONT / "paper.js").read_text()
    app = (FRONT / "app.js").read_text()
    index = (FRONT / "index.html").read_text()
    assert '<script src="paper.js"></script>' in index
    # the reads, same origin, and the three stamps by name
    for s in ('"/api/command/paper/account?entries=1"', '"/api/command/paper/overview"',
              '"/api/command/paper/stream"', 'credentials: "same-origin"',
              "Last successful server read", "Last agent heartbeat (paper runtime)",
              "Last ledger transaction", "an unchanged balance is not a stale one",
              "A recent read is not agent health."):
        assert s in paper, s
    # live on a phone: backoff, a 15 s polling fallback, visibility and bfcache
    for s in ("POLL_MS = 15000", "open() ? LIVE_MS : POLL_MS", "Math.min(30000, 1000 * Math.pow(2, retry++))",
              'addEventListener("visibilitychange"', 'addEventListener("pageshow"', "e.persisted",
              'addEventListener("online"'):
        assert s in paper, s
    # a failed read is never a figure
    assert '"SIGN-IN REQUIRED"' in paper and "This is not a zero balance." in paper
    assert "500,000" not in paper and "500000" not in paper
    # the overview: the paper panel first, the funded system collapsed and labelled
    ov = app[app.index("function overview()"):app.index("function filters(")]
    assert ov.index("paper-home-slot") < ov.index('class="funded-section"') < ov.index("kpis()")
    assert "Funded system (inactive)" in ov
    assert "FUNDED SYSTEM (INACTIVE) · FEED UNAVAILABLE" in app
    assert "'FUNDED · '+f" in app and "PAPER · LIVE" in app
    assert "window.BTPaper?.mount(document.getElementById('paper-home-slot'))" in app
    css = (FRONT / "command.css").read_text()
    assert ".funded-section" in css and "#paper-strip{position:static" in css


def test_the_agent_shell_navigation_does_not_clip_at_phone_width():
    html = (FRONT / "agent.html").read_text()
    assert "overflow-x:auto" in html and "@media (max-width:640px)" in html
    assert 'href="/derek"' in html and 'href="/xavier"' in html and 'href="/audrey"' in html
    assert 'href="/"' in html                                   # back to the homepage
    assert "SIGN-IN REQUIRED" in html and 'credentials: "same-origin"' in html
    toml = (FRONT.parents[2] / "netlify.toml").read_text()
    for a in KINDS:
        assert 'from = "https://command.bettortoken.com/%s"' % a in toml
