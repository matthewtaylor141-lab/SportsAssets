"""THE PINNACLE_ONLY_PAPER_BENCHMARK, PAPER ONLY, END TO END.

An EXPERIMENTAL paper execution benchmark on the stored de-vigged Pinnacle
probability alone (agents/paper_benchmark.py). ALL MARKET DATA HERE IS
SYNTHETIC: valuations are written by `paper_live_fixture.valuation`, books by
a substituted transport behind the REAL read-only PaperMarketDataClient, and
the clock is fixed (the pass instant). Scratch test accounts only; nothing is
written into the live paper account `paper_acct_main`.

  isolation   the benchmark imports no funded / live / order-submitting
              module, no funded module imports it, importing it loads none,
              and the market-data client stays read-only (0 mutations)
  lifecycle   qualifying valuation -> benchmark ENTER (p_internal NULL) ->
              labelled paper order -> simulated fill after the delay -> cash
              movement on the one ledger -> Xavier handoff and review on the
              benchmark's own measure -> Audrey's labelled section and fill
              audit -> settlement exactly once
  accounting  reservation, partial fill, IOC cancel, named no-fill expiry and
              settlement keep ledger_consistent; no funding is added;
              balances equal the ledger sums
  refusals    edge 4.9 pp, EV <= 0 after fees, stale Pinnacle, settlement
              terms unmatched, payout outcome unmatched, no book -- each
              recorded with its named refusal and shortfall
  coexist     both strategies decide the same valuation, once each
  switch      off (flag unset, or kill-switch row off) = no benchmark rows
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_paper_guard as G
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
FEE = H.flat_fee(0.01)
ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
BENCH = ROOT / "agents" / "paper_benchmark.py"


async def _nosleep(_):
    return None


async def _pass(conn, acct, transport, now, fee=FEE, **kw):
    transport.t = max(transport.t, float(now))
    return await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=kw.pop("client", None)
                               or PL.client(transport),
                               config=acct["config"], force=True,
                               fee_fn=fee, sleep=_nosleep, **kw)


async def _decisions(conn, acct, vid) -> dict:
    return {r["strategy"]: r for r in await conn.fetch(
        "SELECT * FROM paper_decisions WHERE session_id=$1 "
        "   AND valuation_id=$2", acct["session_id"], vid)}


async def _ledger_matches_balances(conn, acct_id, now) -> dict:
    b = await L.balances(conn, acct_id, now=now)
    s = await conn.fetchrow(
        "SELECT sum(cash_delta_usd) AS c, sum(reserved_delta_usd) AS r, "
        "       count(*) FILTER (WHERE kind='INITIAL_FUNDING') AS f "
        "  FROM paper_ledger WHERE account_id=$1", acct_id)
    assert b["ledger_consistent"] is True
    assert b["cash_usd"] == pytest.approx(float(s["c"]))
    assert b["reserved_usd"] == pytest.approx(float(s["r"]))
    assert int(s["f"]) == 1, "funding is added exactly once, never again"
    return b


@pytest.fixture
def bench_on(monkeypatch):
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    PB._CONTEXT_CACHE.clear()


# ═════════════════════════════════════════════════════════════════════
# 1 · ISOLATION: UNREACHABLE FROM REAL MONEY
# ═════════════════════════════════════════════════════════════════════

FORBIDDEN = ("funded", "entry_execution", "entry_inventory", "live",
             "adapter", "submission", "order_router", "polymarket",
             "ext_pinnacle_loop", "workers", "risk_engine", "market_stream")
ALLOWED_IMPORTS = {"__future__", "annotations", "asyncio", "hashlib", "json", "math", "os",
                   "time", "typing", "bettor_paper_ledger",
                   "bettor_paper_simulator", "derek_policy", "paper_derek"}


def _imports(path: pathlib.Path) -> list:
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            out.append(node.module or "")
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
    return out


def test_the_benchmark_imports_only_paper_and_pure_policy_modules():
    names = _imports(BENCH)
    # every import statement in the file, top level AND inside functions
    leaves = {n.split(".")[-1] for n in names if n}
    assert leaves <= ALLOWED_IMPORTS | {"Any"}, sorted(leaves - ALLOWED_IMPORTS)
    bad = [n for n in names if any(f in (n or "").lower() for f in FORBIDDEN)]
    assert not bad, bad


def test_importing_the_benchmark_loads_no_funded_or_submission_module():
    code = ("import sys; import sportsassets.agents.paper_benchmark; "
            "print('\\n'.join(sorted(m for m in sys.modules "
            "if m.startswith('sportsassets'))))")
    got = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=str(ROOT.parent), check=True)
    loaded = got.stdout.split()
    assert "sportsassets.agents.paper_benchmark" in loaded
    bad = [m for m in loaded if any(f in m for f in FORBIDDEN)]
    assert not bad, bad


def test_no_funded_module_reaches_the_benchmark():
    """The other direction: only paper modules, the paper read model / API
    and the paper runtime name the benchmark; no funded module does."""
    users = set()
    for p in ROOT.rglob("*.py"):
        if p == BENCH:
            continue
        if any("paper_benchmark" in (n or "") for n in _imports(p)):
            users.add(str(p.relative_to(ROOT)))
    assert users <= {"agents/paper_runtime.py", "agents/paper_xavier.py",
                     "agents/paper_audrey.py", "bettor_paper_readmodel.py"}, \
        sorted(users)
    assert not any("funded" in u for u in users)


def test_the_constants_and_the_disclosure():
    assert PB.EXPERIMENT_ID == ext.EXPERIMENT_ID
    assert PB.STRATEGY == "PINNACLE_ONLY_PAPER_BENCHMARK"
    assert PB.TWO_MODEL_STRATEGY == "DEREK_ENTRY_POLICY_V2"
    assert "NOT evidence of qualified or proven profitability" in PB.DISCLOSURE
    assert PB.MIN_EDGE == 0.05 and PB.MIN_EDGE_PP == 5.0
    assert PB.BOOK_CURRENCY["verdict"] == "NOT_ESTABLISHED"


def test_every_level_used_clears_5pp_and_4_9pp_does_not():
    lv = [{"price": 0.60, "wire": 0.60, "qty": 100.0},
          {"price": 0.61, "wire": 0.61, "qty": 100.0}]
    at5 = PB.size_within_edge(lv, p=0.65, consumed={}, target_usd=5000,
                              cap_usd=5000, fee_per_contract_max=0.01)
    assert at5["limit"] == 0.60 and at5["qty"] == 100   # exactly 5.0 pp
    at49 = PB.size_within_edge(lv, p=0.649, consumed={}, target_usd=5000,
                               cap_usd=5000, fee_per_contract_max=0.01)
    assert at49["qty"] == 0 and at49["limit"] is None
    e = PB.level_edges(lv, 0.649)
    assert e[0]["edge_pp"] == pytest.approx(4.9) and not e[0]["clears_5pp"]


def test_switch_reads_the_environment(monkeypatch):
    monkeypatch.delenv(PB.ENV_FLAG, raising=False)
    assert PB.env_on() is False and PR._benchmark_env_on() is False
    assert "benchmark" not in [n for n, _ in PR.default_steps()]
    for v in ("on", "1", "true", "yes", "ON"):
        monkeypatch.setenv(PB.ENV_FLAG, v)
        assert PB.env_on() and PR._benchmark_env_on()
    names = [n for n, _ in PR.default_steps()]
    assert names.index("derek") < names.index("benchmark") < \
        names.index("simulate_after_delay")


# ═════════════════════════════════════════════════════════════════════
# 2 · THE LIFECYCLE, THROUGH THE REAL PAPER PASS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_lifecycle_enter_order_fill_cash_xavier_audrey_settle(bench_on):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        funded_before = await H.funded_table_counts(conn)
        acct = await PL.new_account(conn, "bench", now=now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62)
        slug = v["slug"]
        t = PL.Transport(now)
        # 12 pp, 6 pp, 2 pp: the limit is 0.56, never the 0.60 level
        t.set(slug, offers=[(0.50, 3000), (0.56, 2000), (0.60, 5000)],
              bids=[(0.48, 5000)])
        client = PL.client(t)
        p1 = await _pass(conn, acct, t, now, client=client)
        assert p1["ran"] and not p1["errors"], p1["errors"]
        assert p1["steps"]["benchmark"]["decisions_recorded"] >= 1
        assert p1["mutation_attempts"] == 0 and client.mutation_attempts == 0

        # ── BOTH STRATEGIES ON THE SAME VALUATION, ONE RECORD EACH ──────
        ds = await _decisions(conn, acct, v["valuation_id"])
        assert set(ds) == {"DEREK_ENTRY_POLICY_V2", PB.STRATEGY}
        assert ds["DEREK_ENTRY_POLICY_V2"]["refusal"] == PD.R_NO_RESEARCH_MODEL
        d = ds[PB.STRATEGY]
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
        assert d["decision_id"].startswith("paperbench:")
        assert d["policy_version"] == PB.VERSION
        # NEVER AN INTERNAL MODEL; PINNACLE STAYS IN ITS OWN FIELDS
        assert d["p_internal"] is None and d["p_blended"] is None
        im = H.j(d["internal_model"])
        assert im["available"] is False and im["p"] is None and im["reason"]
        assert d["p_pinnacle"] == pytest.approx(0.62)
        pin = H.j(d["pinnacle"])
        assert pin["qualification"] == "FRESH" and pin["age_s"] <= 30.0
        assert pin["contract_match"]["established"] is True
        assert pin["displayed_quote_used_as_price"] is False
        # LABEL, DISCLOSURE, P5
        lab = H.j(d["label"])
        assert lab["strategy"] == PB.STRATEGY and lab["disclosure"]
        assert lab["book_currency"] == "NOT_ESTABLISHED"
        book = H.j(d["book"])
        assert book["book_currency"]["verdict"] == "NOT_ESTABLISHED"
        assert book["basis"] == \
            "OBSERVED_PAPER_BOOK_LEVELS_NOT_THE_VALUATION_QUOTE"
        econ = H.j(d["economics"])
        assert econ["limit_price"] == 0.56 and econ["proposed_qty"] == 5000
        assert econ["best_level_edge_pp"] == pytest.approx(12.0)
        walk = econ["acquisition"]["walk"]
        assert [(w["price"], w["take"]) for w in walk] == [(0.50, 3000.0),
                                                           (0.56, 2000.0)]
        assert all(w["edge_pp"] >= 5.0 - 1e-9 for w in walk)
        assert econ["acquisition"]["fees_usd"] == pytest.approx(50.0)
        assert econ["acquisition"]["expected_net_profit_usd"] == \
            pytest.approx(3000 * 0.12 + 2000 * 0.06 - 50.0)
        gaps = {g["gap"]: g["status"] for g in H.j(d["qualification_gaps"])}
        assert gaps[PB.GAP_NOT_EVIDENCE] == "DISCLOSED"
        assert gaps[PD.GAP_P5] == "OPEN"
        assert H.j(d["policy_decision"])["strategy"] == PB.STRATEGY

        # ── THE PAPER ORDER, LABELLED, NAMING THE DECISION ──────────────
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                " decision_id=$1", d["decision_id"])
        assert o["strategy"] == PB.STRATEGY
        assert H.j(o["label"])["strategy"] == PB.STRATEGY
        assert o["role"] == "ENTRY" and float(o["limit_price"]) == 0.56
        assert float(o["qty"]) == 5000 and o["state"] == "FILLED"
        assert d["recorded_at"] <= o["created_at"]
        # ── THE SIMULATED FILL AFTER THE DELAY ─────────────────────────
        fills = await conn.fetch("SELECT * FROM paper_fills WHERE "
                                 " order_id=$1 ORDER BY price", o["order_id"])
        assert [(float(f["qty"]), float(f["price"])) for f in fills] == [
            (3000.0, 0.50), (2000.0, 0.56)]
        assert all(L._epoch(f["book_observed_at"]) >=
                   L._epoch(o["eligible_at"]) for f in fills)
        assert {H.j(f["label"])["strategy"] for f in fills} == {PB.STRATEGY}
        assert {f["event_source"] for f in fills} == {"SIMULATOR"}
        # ── CASH ON THE ONE LEDGER ─────────────────────────────────────
        b = await _ledger_matches_balances(conn, acct["account_id"], now + 5)
        assert b["cash_usd"] == pytest.approx(500000.0 - 2620.0 - 50.0)
        assert b["reserved_usd"] == 0.0
        kinds = await H.ledger_kinds(conn, acct["account_id"])
        assert kinds[:4] == ["INITIAL_FUNDING", "ORDER_SUBMITTED", "FILL",
                             "FILL"]
        pos = await L.positions(conn, acct["account_id"])
        assert len(pos) == 1 and pos[0]["label"]["strategy"] == PB.STRATEGY
        # ── XAVIER: HANDOFF AND REVIEW ON THE BENCHMARK'S OWN MEASURE ──
        h = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                " group_id=$1", o["group_id"])
        assert h["strategy"] == PB.STRATEGY and h["owner"] == "XAVIER"
        assert float(h["confirmed_qty"]) == 5000
        r = await conn.fetchrow("SELECT * FROM paper_xavier_reviews WHERE "
                                " group_id=$1 ORDER BY reviewed_at",
                                o["group_id"])
        assert r["trigger"] == "FIRST_FILL"
        m = H.j(r["measure"])
        assert m["strategy"] == PB.STRATEGY and m["p_internal"] is None
        assert m["source"] == "PINNACLE_ONLY_CURRENT"
        assert m["p"] == pytest.approx(0.62)
        mg = await conn.fetch("SELECT strategy FROM paper_orders WHERE "
                              " group_id=$1 AND role <> 'ENTRY'",
                              o["group_id"])
        assert mg and {x["strategy"] for x in mg} == {PB.STRATEGY}
        # ── AUDREY SEES IT ─────────────────────────────────────────────
        rep = await conn.fetchrow(
            "SELECT * FROM paper_audrey_reports WHERE session_id=$1 "
            " ORDER BY version DESC LIMIT 1", acct["session_id"])
        assert rep["reconciles"] is True
        body = H.j(rep["report"])
        sec = body["pinnacle_only_paper_benchmark"]
        assert sec["strategy"] == PB.STRATEGY and sec["disclosure"]
        assert sec["fills"]["fills"] == 2
        assert sec["fills"]["acquisition_usd"] == pytest.approx(2670.0)
        assert sec["handoffs_to_xavier_total"] == 1
        assert all(x["strategy"] == PB.STRATEGY for x in sec["decisions"])
        # Derek's decision figures are the two-model strategy's alone
        assert {x["reason"] for x in body["decisions"]} == {
            PD.R_NO_RESEARCH_MODEL}
        f = await conn.fetchrow(
            "SELECT * FROM paper_audrey_findings WHERE session_id=$1 "
            "   AND kind='PINNACLE_ONLY_PAPER_BENCHMARK_FILL_AUDITED'",
            acct["session_id"])
        assert f is not None and f["severity"] == "INFO"
        fd = H.j(f["detail"])
        assert fd["passed"] is True and fd["ledger_fill_entries"] == 2
        assert fd["handed_to_xavier"] is True
        # ── SETTLEMENT, EXACTLY ONCE ───────────────────────────────────
        await PL.settle_valuation(conn, v["valuation_id"], outcome=1)
        p2 = await _pass(conn, acct, t, now + 120, client=client)
        assert not p2["errors"], p2["errors"]
        assert p2["steps"]["settle"]["settled"] == 1
        p3 = await _pass(conn, acct, t, now + 240, client=client)
        assert p3["steps"]["settle"]["settled"] == 0
        b = await _ledger_matches_balances(conn, acct["account_id"], now + 300)
        assert b["cash_usd"] == pytest.approx(500000.0 - 2670.0 + 5000.0)
        assert b["reserved_usd"] == 0.0 and not b["open_positions"]
        # a replayed pass decides nothing twice, for either strategy
        n = await conn.fetchval("SELECT count(*) FROM paper_decisions WHERE "
                                " session_id=$1 AND valuation_id=$2",
                                acct["session_id"], v["valuation_id"])
        assert n == 2
        assert client.mutation_attempts == 0
        h2 = await S.health(conn, acct["session_id"])
        assert h2["mutation_attempts"] == 0
        assert await H.funded_table_counts(conn) == funded_before
    finally:
        await PL.drop_today_run(conn, now)
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · ACCOUNTING THROUGH THE PER-VALUATION HOOK: PARTIAL, CANCEL, NO-FILL
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_hook_partial_fill_cancel_and_named_no_fill_keep_the_ledger(
        bench_on, monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        monkeypatch.setenv(S.ENV_FLAG, "on")
        acct = await PL.new_account(conn, "benchacc", now=now)
        va = await PL.valuation(conn, decided_at=now - 2, p_pin=0.62)
        vb = await PL.valuation(conn, decided_at=now - 2, p_pin=0.62)
        t = PL.Transport(now)
        t.set(va["slug"], offers=[(0.50, 1000)], bids=[(0.48, 1000)])
        t.set(vb["slug"], offers=[(0.50, 1000)], bids=[(0.48, 1000)])
        # the decision reads land inside the 2 s delay (as with a live
        # clock), so neither decision book can serve as the fill book
        t.step = 0.1
        client = PL.client(t)
        sched = []
        got = {}
        for v in (va, vb):
            got[v["slug"]] = await PR.decide_valuation(
                conn, valuation_id=v["valuation_id"], now=now,
                market_data=client, account_id=acct["account_id"],
                fee_fn=FEE, schedule_fill=lambda: sched.append(1) or {
                    "scheduled": True})
        for v in (va, vb):
            g = got[v["slug"]]
            assert g["refusal"] == PD.R_NO_RESEARCH_MODEL      # two-model
            assert g["benchmark"]["verdict"] == "ENTER", g["benchmark"]
            assert g["benchmark"]["order_id"]
            assert g["mutation_attempts"] == 0
        assert len(sched) == 2
        b = await _ledger_matches_balances(conn, acct["account_id"], now)
        # reservations only: cash untouched, 2 x (1000 x 0.50 + 10 fees)
        assert b["cash_usd"] == 500000.0
        assert b["reserved_usd"] == pytest.approx(1020.0)
        # after the delay: A's book thinned (partial; IOC remainder
        # canceled), B's book has nothing within the limit (named no-fill)
        t.set(va["slug"], offers=[(0.50, 400)], bids=[(0.48, 1000)])
        t.set(vb["slug"], offers=[(0.70, 1000)], bids=[(0.48, 1000)])
        p = await _pass(conn, acct, t, now + 5, client=client)
        assert not p["errors"], p["errors"]
        oa = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                 " order_id=$1",
                                 got[va["slug"]]["benchmark"]["order_id"])
        ob = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                 " order_id=$1",
                                 got[vb["slug"]]["benchmark"]["order_id"])
        assert oa["strategy"] == ob["strategy"] == PB.STRATEGY
        assert oa["state"] == "CANCELED" and float(oa["filled_qty"]) == 400
        assert oa["terminal_reason"] == "IOC_REMAINDER_CANCELED"
        assert ob["state"] == "EXPIRED" and float(ob["filled_qty"]) == 0
        assert ob["terminal_reason"] == "NO_DISPLAYED_LIQUIDITY_WITHIN_THE_" \
            "LIMIT"
        assert float(oa["reserved_remaining_usd"]) == 0.0
        assert float(ob["reserved_remaining_usd"]) == 0.0
        b = await _ledger_matches_balances(conn, acct["account_id"], now + 6)
        assert b["reserved_usd"] == 0.0
        assert b["cash_usd"] == pytest.approx(500000.0 - 200.0 - 4.0)
        kinds = await H.ledger_kinds(conn, acct["account_id"])
        assert kinds.count("RESERVATION_RELEASED") == 2
        assert kinds.count("INITIAL_FUNDING") == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_handoffs WHERE account_id=$1 "
            "   AND strategy=$2", acct["account_id"], PB.STRATEGY) == 1
    finally:
        await PL.drop_today_run(conn, now)
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · REFUSALS ARE RECORDS, WITH THEIR NAMED SHORTFALLS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_refusals_are_recorded_by_name_with_their_shortfall(bench_on):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "benchref", now=now)
        t = PL.Transport(now)
        edge49 = await PL.valuation(conn, decided_at=now - 5, p_pin=0.649)
        t.set(edge49["slug"], offers=[(0.60, 1000)], bids=[(0.58, 1000)])
        stale = await PL.valuation(conn, decided_at=now - 5, p_pin=0.62,
                                   pin_age_s=600)
        t.set(stale["slug"], offers=[(0.40, 1000)])
        unsettled = await PL.valuation(conn, decided_at=now - 5, p_pin=0.62,
                                       compatibility="INCOMPATIBLE")
        t.set(unsettled["slug"], offers=[(0.40, 1000)])
        unknown = await PL.valuation(conn, decided_at=now - 5, p_pin=0.62,
                                     compatibility="NOT_COMPARED")
        t.set(unknown["slug"], offers=[(0.40, 1000)])
        outcome = await PL.valuation(conn, decided_at=now - 5, p_pin=0.62)
        await conn.execute("UPDATE external_valuations SET payout_event="
                           "'AWAY' WHERE id=$1", outcome["valuation_id"])
        t.set(outcome["slug"], offers=[(0.40, 1000)])
        nobook = await PL.valuation(conn, decided_at=now - 5, p_pin=0.62)
        # (no book fixture for nobook: the read returns NO_BOOK_FIXTURE)
        p = await _pass(conn, acct, t, now)
        assert not p["errors"], p["errors"]

        async def bench(v):
            return (await _decisions(conn, acct, v["valuation_id"]))[
                PB.STRATEGY]
        d = await bench(edge49)
        assert d["verdict"] == "REFUSE" and d["refusal"] == PB.R_EDGE
        sh = H.j(d["economics"])["shortfall"]
        assert sh["edge_pp"] == pytest.approx(4.9)
        assert sh["edge_threshold_pp"] == 5.0
        assert sh["edge_shortfall_pp"] == pytest.approx(0.1)
        assert d["p_internal"] is None and d["p_blended"] is None
        d = await bench(stale)
        assert d["refusal"] == "PROBABILITY_EVIDENCE_STALE"
        sh = H.j(d["economics"])["shortfall"]
        assert sh["pinnacle_age_s"] > 30.0 and sh["pinnacle_limit_s"] == 30.0
        assert d["book_obs_id"] is None          # no book read for it
        d = await bench(unsettled)
        assert d["refusal"] == "SETTLEMENT_NOT_SUPPORTED"
        d = await bench(unknown)
        assert d["refusal"] == "SETTLEMENT_NOT_SUPPORTED"
        d = await bench(outcome)
        assert d["refusal"] == PB.R_OUTCOME
        d = await bench(nobook)
        assert d["refusal"] == PB.R_NO_BOOK
        # the readback's "top candidates by edge" has the 4.9 pp shortfall
        top = await conn.fetchrow(
            "SELECT valuation_id, (economics->>'best_level_edge_pp')::float8 "
            "  AS e FROM paper_decisions WHERE session_id=$1 AND strategy=$2"
            "   AND economics->>'best_level_edge_pp' IS NOT NULL "
            " ORDER BY 2 DESC LIMIT 1", acct["session_id"], PB.STRATEGY)
        assert top["valuation_id"] == edge49["valuation_id"]
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE account_id=$1",
            acct["account_id"]) == 0
        b = await _ledger_matches_balances(conn, acct["account_id"], now)
        assert b["cash_usd"] == 500000.0 and b["reserved_usd"] == 0.0
    finally:
        await PL.drop_today_run(conn, now)
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_ev_not_positive_after_the_simulators_fees_is_refused(bench_on):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "benchev", now=now)
        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 5, p_pin=0.62)
        t.set(v["slug"], offers=[(0.56, 1000)], bids=[(0.54, 1000)])
        # 6 pp of edge; a 7-cent-per-contract fee function (the one the
        # simulator would charge) leaves 1000 x 0.06 - 70 = -10 USD
        p = await _pass(conn, acct, t, now, fee=H.flat_fee(0.07))
        assert not p["errors"], p["errors"]
        d = (await _decisions(conn, acct, v["valuation_id"]))[PB.STRATEGY]
        assert d["refusal"] == "NET_EV_NOT_POSITIVE_AFTER_FEES"
        econ = H.j(d["economics"])
        assert econ["best_level_edge_pp"] == pytest.approx(6.0)
        assert econ["shortfall"]["ev_after_fees_usd"] == pytest.approx(-10.0)
        assert econ["acquisition"]["fees_usd"] == pytest.approx(70.0)
        assert await conn.fetchval("SELECT count(*) FROM paper_orders WHERE "
                                   " account_id=$1", acct["account_id"]) == 0
    finally:
        await PL.drop_today_run(conn, now)
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · THE SWITCH: OFF = NO BENCHMARK ROWS, AS BEFORE
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_switch_off_writes_no_benchmark_rows(monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        monkeypatch.delenv(PB.ENV_FLAG, raising=False)
        acct = await PL.new_account(conn, "benchoff", now=now)
        v = await PL.valuation(conn, decided_at=now - 5, p_pin=0.62)
        t = PL.Transport(now)
        t.set(v["slug"], offers=[(0.40, 1000)])
        p = await _pass(conn, acct, t, now)
        assert "benchmark" not in p["steps"] and not p["errors"]
        assert t.calls == []          # the two-model path reads no book
        ds = await _decisions(conn, acct, v["valuation_id"])
        assert set(ds) == {"DEREK_ENTRY_POLICY_V2"}
        # the per-valuation hook answers exactly as before (no benchmark key)
        monkeypatch.setenv(S.ENV_FLAG, "on")
        v2 = await PL.valuation(conn, decided_at=now - 5, p_pin=0.62)
        g = await PR.decide_valuation(
            conn, valuation_id=v2["valuation_id"], now=now,
            market_data=PL.client(t), account_id=acct["account_id"],
            fee_fn=FEE, schedule_fill=lambda: {"scheduled": False})
        assert set(g) == {"decision_id", "verdict", "refusal", "order_id",
                          "duplicate", "deferred", "fill_pass", "decided",
                          "mutation_attempts"}
        # flag on, kill-switch row off: still nothing
        monkeypatch.setenv(PB.ENV_FLAG, "on")
        await conn.execute("UPDATE paper_control SET enabled=FALSE WHERE "
                           " control_key=$1", PB.CONTROL_KEY)
        try:
            p2 = await _pass(conn, acct, t, now + 60)
            assert p2["steps"]["benchmark"]["enabled"] is False
            assert p2["steps"]["benchmark"]["refusal"] == PB.R_CONTROL_OFF
            g2 = await PR.decide_valuation(
                conn, valuation_id=v2["valuation_id"], now=now,
                market_data=PL.client(t), account_id=acct["account_id"],
                fee_fn=FEE, schedule_fill=lambda: {"scheduled": False})
            assert g2["benchmark"] == {"decided": False,
                                       "why": PB.R_CONTROL_OFF}
        finally:
            await conn.execute("UPDATE paper_control SET enabled=TRUE WHERE "
                               " control_key=$1", PB.CONTROL_KEY)
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_decisions WHERE session_id=$1 "
            "   AND strategy=$2", acct["session_id"], PB.STRATEGY) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE account_id=$1",
            acct["account_id"]) == 0
    finally:
        await PL.drop_today_run(conn, now)
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_migration_182_keeps_existing_rows_on_the_two_model_label():
    conn = await H.connect()
    try:
        cols = {r["table_name"]: r["column_default"] for r in await conn.fetch(
            "SELECT table_name, column_default FROM information_schema."
            "columns WHERE column_name='strategy' AND table_name IN "
            "('paper_decisions','paper_orders','paper_handoffs')")}
        assert set(cols) == {"paper_decisions", "paper_orders",
                             "paper_handoffs"}
        assert all("DEREK_ENTRY_POLICY_V2" in v for v in cols.values())
        idx = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes WHERE indexname="
            "'paper_decisions_one_per_valuation_strategy_idx'")
        assert "(session_id, valuation_id, strategy)" in idx
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE indexname="
            "'paper_decisions_one_per_valuation_idx'") == 0
        row = await conn.fetchrow("SELECT enabled FROM paper_control WHERE "
                                  " control_key=$1", PB.CONTROL_KEY)
        assert row is not None and row["enabled"] is True
        # the live account is untouched: one funding entry, no new kind
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_ledger WHERE account_id=$1 "
            "   AND kind='INITIAL_FUNDING'", L.ACCOUNT_ID) <= 1
    finally:
        await conn.close()


def test_the_guard_still_refuses_every_mutation():
    c = G.PaperMarketDataClient(lambda slug: {"marketData": None})
    with pytest.raises(G.PaperVenueMutationRefused):
        c.place_order({"x": 1})
    assert c.mutation_attempts == 1


def test_readback_sql_is_read_only():
    p = pathlib.Path(__file__).resolve().parents[2] / "research" / \
        "pinnacle_benchmark_readback.sql"
    text = p.read_text()
    body = "\n".join(line.split("--", 1)[0] for line in text.splitlines())
    import re
    words = ("insert", "update", "delete", "drop", "alter", "truncate",
             "grant", "revoke", "create", "copy", "vacuum", "reindex",
             "refresh", "call", "do", "merge", "lock")
    found = [w for w in words if re.search(r"\b%s\b" % w, body, re.I)]
    assert not found, found
    assert "PINNACLE_ONLY_PAPER_BENCHMARK" in body
