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


async def _no_foreign_valuation_in_the_pass_window(conn, now) -> None:
    """THE PASS DECIDES EVERY ENTRY-EXPERIMENT VALUATION IN ITS WINDOW.

    `paper_pass` (and its backstops) decide each valuation of the experiment
    decided within `valuation_lookback_s` (1800 s) of the pass instant, in
    the proof's own session, whoever wrote it. A proof whose session-wide
    figures are its own valuation's alone therefore needs that window to
    hold none but its own. A row another proof left there is decided too:
    one more decision per strategy, and a STALE or NO_RESEARCH_MODEL refusal
    in the Audrey report depending only on how old the leftover reading is.
    Checked after this proof's purge, before it writes its own valuation,
    and named here rather than surfacing as a count."""
    lookback = float(PL.config()["entry"]["valuation_lookback_s"])
    rows = await conn.fetch(
        "SELECT id, us_market_slug, decided_at FROM external_valuations "
        " WHERE experiment_id = $1 AND us_market_slug IS NOT NULL "
        "   AND decided_at > to_timestamp($2) ORDER BY id",
        ext.EXPERIMENT_ID, float(now) - lookback)
    assert not rows, (
        "another proof left entry-experiment valuations inside this pass's "
        "window; their writer must remove them: %s"
        % [(r["id"], r["us_market_slug"], str(r["decided_at"]))
           for r in rows])


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
def bench_on(monkeypatch, new_strategies_off):
    # THE STRICT POLICY ALONE: its own row switched ON for the proof (its
    # migrated state since 184 is off), the completed-game policy's OFF, so
    # every count here is the strict policy's (the completed-game proofs are
    # in test_completed_game_paper_policy.py). Both rows go back to their
    # migrated state afterwards.
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    PL.set_policy_control(PB.CONTROL_KEY, True)
    PL.set_policy_control(PB.CG_POLICY["control_key"], False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    PB._CONTEXT_CACHE.clear()
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)


# ═════════════════════════════════════════════════════════════════════
# 1 · ISOLATION: UNREACHABLE FROM REAL MONEY
# ═════════════════════════════════════════════════════════════════════

FORBIDDEN = ("funded", "entry_execution", "entry_inventory", "live",
             "adapter", "submission", "order_router", "polymarket",
             "ext_pinnacle_loop", "workers", "risk_engine", "market_stream")
ALLOWED_IMPORTS = {"__future__", "annotations", "asyncio", "hashlib", "json", "math", "os", "re",
                   "time", "typing", "bettor_paper_ledger",
                   "bettor_paper_simulator", "bettor_settlement_terms", "derek_policy",
                   "paper_derek",
                   # the owner's capital policy for the main PAPER account:
                   # pure dict transforms (imports only copy), no I/O, no
                   # funded or venue path
                   "bettor_paper_limits",
                   # ONE DECISION -> PAPER + ACTUAL: the registry holding the
                   # executing process's decision hook. It imports NOTHING and
                   # does no I/O (pinned below); the benchmark never imports
                   # an execution, venue or funded module through it.
                   "decision_hooks",
                   # R30A: the NFL settlement evidence and tie conversion.
                   # Standard library only (math, re, datetime, zoneinfo --
                   # pinned below): cited quotes, a binomial interval and a
                   # date read, no I/O and no execution, venue or funded path.
                   "bettor_nfl_settlement",
                   # R30A: the canonical decision intent the benchmark builds
                   # before its hook and whose validity window it checks
                   # before the paper submit. canonical_intent is PURE: it
                   # imports only the standard library (pinned below), so no
                   # execution, venue or funded module is reachable through
                   # it.
                   "canonical_intent",
                   # P0 incident: the NCAAF settlement evidence and its
                   # identity conversion. Standard library (re) plus the pure
                   # NFL module above (pinned below): cited quotes and a
                   # clause reader, no I/O and no execution, venue or funded
                   # path.
                   "bettor_ncaaf_settlement",
                   # R30A P0 incident: the LINE-MARKET family proofs the
                   # completed-game match re-runs for a spread / total / team
                   # total row. Pure (no socket, database, venue, funded or
                   # order path): it imports only the de-vig and the feed
                   # cache's pure reader (pinned below).
                   "bettor_market_family",
                   # (P0 incident) the gross-edge input validation: pure
                   # arithmetic over the row, the book and the fee function
                   # it is handed; imports only the de-vig arithmetic and the
                   # book level parser (pinned in
                   # tests/test_gross_edge_inputs_are_validated.py)
                   "gross_edge_inputs",
                   # (305) PAPER CAPITAL AUTHORITY: the capital evidence the
                   # benchmark's ENTER carries to the ledger and the refusal
                   # census it appends; paper-only, refuse-only, no venue,
                   # order-execution or funded import (pinned in
                   # tests/test_capital_authority.py)
                   "bettor_capital_authority",
                   # (RC6 xavier-records) the provider fixture handed to the
                   # held read: one bounded identity read of the valuation
                   # table, standard library only (pinned in
                   # tests/test_rc6_xavier_held_fixture.py); no venue, order,
                   # execution or funded path
                   "xavier_held_fixture"}


def _imports(path: pathlib.Path) -> list:
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            out.append(node.module or "")
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
    return out


def test_the_canonical_intent_module_is_pure():
    """R30A: the benchmark may import canonical_intent only because it
    imports nothing beyond the standard library."""
    from sportsassets import canonical_intent as CI
    # ROUND_FLOOR (decimal, standard library): R30A review -- the validity
    # window is floored to the millisecond (canonical_intent._floor_ms),
    # never rounded up past the 30 s rule
    stdlib = {"__future__", "annotations", "datetime", "decimal", "hashlib",
              "json", "math", "re", "time", "typing", "uuid", "Decimal",
              "Any", "ROUND_HALF_UP", "ROUND_FLOOR", "InvalidOperation"}
    leaves = {n.split(".")[-1] for n in _imports(pathlib.Path(CI.__file__))
              if n}
    assert leaves <= stdlib, sorted(leaves - stdlib)


def test_the_benchmark_imports_only_paper_and_pure_policy_modules():
    names = _imports(BENCH)
    # every import statement in the file, top level AND inside functions
    leaves = {n.split(".")[-1] for n in names if n}
    assert leaves <= ALLOWED_IMPORTS | {"Any"}, sorted(leaves - ALLOWED_IMPORTS)
    bad = [n for n in names if any(f in (n or "").lower() for f in FORBIDDEN)]
    assert not bad, bad


def test_the_line_market_family_is_pure():
    from sportsassets import bettor_market_family as MF
    names = _imports(pathlib.Path(MF.__file__))
    leaves = {n.split(".")[-1] for n in names if n}
    assert leaves <= {"__future__", "annotations", "hashlib", "math", "re",
                      "unicodedata", "typing", "Optional",
                      "bettor_pinnacle_devig", "pinnapi_feed"}, leaves
    bad = [n for n in names if any(f in (n or "").lower() for f in FORBIDDEN)]
    assert not bad, bad


def test_the_decision_hook_registry_imports_nothing():
    from sportsassets import decision_hooks
    assert _imports(pathlib.Path(decision_hooks.__file__)) == []
    assert decision_hooks.DECISION_HOOK is None or callable(decision_hooks.DECISION_HOOK)


def test_the_nfl_settlement_module_imports_only_the_standard_library():
    from sportsassets import bettor_nfl_settlement
    imports = set(_imports(pathlib.Path(bettor_nfl_settlement.__file__)))
    assert imports <= {"__future__", "annotations", "math", "re", "datetime",
                       "zoneinfo", "ZoneInfo"}, imports


def test_the_ncaaf_settlement_module_imports_only_pure_modules():
    from sportsassets import bettor_ncaaf_settlement
    imports = set(_imports(pathlib.Path(bettor_ncaaf_settlement.__file__)))
    # `from . import bettor_nfl_settlement` reads as module "" + its name;
    # that module is itself pinned to the standard library above
    assert imports <= {"__future__", "annotations", "re", "",
                       "bettor_nfl_settlement"}, imports


def test_owner_limits_helper_is_pure_configuration_only():
    from sportsassets import bettor_paper_limits
    imports = set(_imports(pathlib.Path(bettor_paper_limits.__file__)))
    assert imports <= {"copy", "deepcopy"}, imports


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
    # bettor_paper_ops.py: the management pages' paper read model (read-only,
    # imports only paper modules)
    # agents/persona_facts.py: the agents' chat reads the ACTIVE entry
    # threshold (cg_parameters, read-only) so each answer states the policy
    # the paper decision path actually runs
    # agents/paper_maker.py, agents/paper_explore.py: the maker-entry policy
    # and the bounded exploration strategy (migration 189), paper modules
    # whose decisions reuse the benchmark's match, book and attempt helpers
    # agents/paper_derek.py (P1): the priced settlement-difference policy
    # reuses the benchmark's completed-game match and venue conversion
    assert users <= {"agents/paper_runtime.py", "agents/paper_xavier.py",
                     "agents/paper_derek.py",
                     "agents/paper_audrey.py", "bettor_paper_readmodel.py",
                     "bettor_paper_ops.py", "agents/persona_facts.py",
                     "agents/paper_maker.py", "agents/paper_explore.py"}, \
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
        await _no_foreign_valuation_in_the_pass_window(conn, now)
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
        assert r["trigger"] == "FIRST_FILL" and r["strategy"] == PB.STRATEGY
        assert pos[0]["strategy"] == PB.STRATEGY
        assert pos[0]["open_qty"] == float(h["confirmed_qty"]) == 5000.0
        assert {f["strategy"] for f in fills} == {PB.STRATEGY}
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
        # ── WHAT MANAGEMENT READS: strategy, explanation, balance, handoff
        from sportsassets import bettor_paper_readmodel as RM
        dp = await RM.derek_payload(conn, account_id=acct["account_id"],
                                    now=now + 5)
        rows = {x["strategy"]: x for x in dp["opportunities"]["data"]
                if x["valuation_id"] == v["valuation_id"]}
        assert set(rows) == {"DEREK_ENTRY_POLICY_V2", PB.STRATEGY}
        assert "PINNACLE_ONLY_PAPER_BENCHMARK" in rows[PB.STRATEGY][
            "explanation"] and "not evidence" in rows[PB.STRATEGY][
            "explanation"]
        assert rows[PB.STRATEGY]["explanation"].startswith("ENTER")
        bp = await RM.benchmark_payload(conn, account_id=acct["account_id"],
                                        now=now + 5)
        assert bp["disclosure"] == PB.DISCLOSURE
        assert bp["decisions"]["status"] == "OK"
        assert bp["handoffs"]["data"][0]["group_id"] == o["group_id"]
        assert {e["kind"] for e in bp["ledger"]["data"]} >= {
            "ORDER_SUBMITTED", "FILL"}
        xp = await RM.xavier_payload(conn, account_id=acct["account_id"],
                                     now=now + 5)
        assert xp["positions"]["data"][0]["strategy"] == PB.STRATEGY
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
async def test_switch_off_writes_no_benchmark_rows(monkeypatch,
                                                    new_strategies_off):
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
        prior = await conn.fetchval("SELECT enabled FROM paper_control "
                                    " WHERE control_key=$1", PB.CONTROL_KEY)
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
            # back to the row's state before this proof (off since 184)
            await conn.execute("UPDATE paper_control SET enabled=$2 WHERE "
                               " control_key=$1", PB.CONTROL_KEY, prior)
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
        row = await conn.fetchrow("SELECT enabled, updated_by FROM "
                                  " paper_control WHERE control_key=$1",
                                  PB.CONTROL_KEY)
        # the row 182 inserted is still there; since 184 its NEW ENTRIES are
        # off (one active entry experiment: the completed-game policy)
        assert row is not None
        assert row["enabled"] is False and row["updated_by"] == \
            "migration 184"
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



# ═════════════════════════════════════════════════════════════════════
# 6 · OWNER PROOFS: KEYS, ONE LEDGER UNDER CONCURRENCY, REPEATS AND
#     RESTARTS, BOTH STRATEGIES RECONCILED, ENTRY SWITCH, ISOLATION
# ═════════════════════════════════════════════════════════════════════

SUBMITTERS = ("bettor_funded_execution", "bettor_entry_execution",
              "bettor_funded_management", "bettor_funded_activation",
              "live_executor", "calibration_execute", "calibration_adapter",
              "bettor_shadow_loop", "bettor_test_venue_executor", "pmus",
              "pmx")


def test_no_paper_module_imports_a_submission_path():
    mods = [p for p in ROOT.rglob("*.py")
            if p.name.startswith("bettor_paper") or p.name.startswith("paper_")
            or (p.parent.name == "api" and p.name == "command_paper.py")]
    assert BENCH in mods and len(mods) >= 10
    bad = {}
    for p in mods:
        leaves = {n.split(".")[-1] for n in _imports(p) if n}
        hit = sorted(leaves & set(SUBMITTERS))
        if hit:
            bad[str(p.relative_to(ROOT))] = hit
    assert not bad, bad


def test_every_key_is_strategy_specific():
    sid, vid = "paper_session_X", 4242
    d2, db = PD.decision_id_for(sid, vid), PB.decision_id_for(sid, vid)
    assert d2 != db and db.startswith("paperbench:")
    g2, gb = PD.group_id_for(d2), PB.group_id_for(db)
    assert g2 != gb and gb.startswith("paperbenchgrp:")
    k2, kb = "%s:ENTRY" % d2, "%s:ENTRY" % db
    assert L.order_id_for(k2) != L.order_id_for(kb)
    for slug in ("same-market",):
        assert L.position_key(account_id="a", group_id=g2, slug=slug,
                              holding_side="LONG") != \
            L.position_key(account_id="a", group_id=gb, slug=slug,
                           holding_side="LONG")
    # fill keys embed the order id; handoff keys hash the group
    assert "%s:obs1:0.500000" % L.order_id_for(k2) != \
        "%s:obs1:0.500000" % L.order_id_for(kb)


@pg
async def test_both_strategies_reserving_concurrently_never_overcommit():
    """ONE SHARED CASH LEDGER: a two-model and a benchmark entry, each
    reserving $800 against $1,000 available, submitted at the same instant
    on two connections -- the account row lock serializes them, exactly one
    reserves, the other is refused INSUFFICIENT; repeated 6 times."""
    import asyncio
    c0, c1, c2 = await H.connect(), await H.connect(), await H.connect()
    try:
        a = await H.new_account(c0, "benchconc")
        # the filler sits on its OWN fixture: $499k on the entries' fixture
        # would (rightly) trip the canonical event-exposure lock (Red Team
        # Closeout V1, 125k) before the cash serialization under test
        filler = H.order(a, key="fill", qty=998000, limit=0.50,
                         role="HEDGE", slug=a["account_id"] + ":filler",
                         fixture="fx-conc-filler")
        got = await L.submit_order(c0, filler, fee_fn=H.zero_fee, now=H.T0)
        assert got["ok"], got
        cs = await L.cash_state(c0, a["account_id"])
        assert float(cs["available"]) == 1000.0
        for i in range(6):
            o2 = H.order(a, key="tm-%d" % i, qty=1600, limit=0.50,
                         slug=a["account_id"] + ":m%d" % i,
                         group_id="papergrp:conc%d" % i)
            ob = dict(H.order(a, key="bm-%d" % i, qty=1600, limit=0.50,
                              slug=a["account_id"] + ":m%d" % i,
                              group_id="paperbenchgrp:conc%d" % i),
                      strategy=PB.STRATEGY)
            r2, rb = await asyncio.gather(
                L.submit_order(c1, o2, fee_fn=H.zero_fee, now=H.T0 + i),
                L.submit_order(c2, ob, fee_fn=H.zero_fee, now=H.T0 + i))
            oks = [r for r in (r2, rb) if r.get("ok")]
            refused = [r for r in (r2, rb) if not r.get("ok")]
            assert len(oks) == 1 and len(refused) == 1, (r2, rb)
            assert refused[0]["refusal"] == L.R_INSUFFICIENT
            cs = await L.cash_state(c0, a["account_id"])
            assert float(cs["available"]) == pytest.approx(200.0)
            assert cs["running_balance_agrees"] is True
            # release the winner so the next round starts from $1,000
            await L.release_remainder(c0, order_id=oks[0]["order"]["order_id"],
                                      reason="TEST_ROUND_END", at=H.T0 + i)
        s = await c0.fetchrow(
            "SELECT count(*) FILTER (WHERE kind='ORDER_SUBMITTED') AS sub, "
            "       min(cash_after_usd - reserved_after_usd) AS min_avail "
            "  FROM paper_ledger WHERE account_id=$1", a["account_id"])
        assert int(s["sub"]) == 7 and float(s["min_avail"]) >= 0.0
        strat = await c0.fetch(
            "SELECT strategy, count(*) AS n FROM paper_orders WHERE "
            " account_id=$1 AND role='ENTRY' GROUP BY 1", a["account_id"])
        assert sum(int(r["n"]) for r in strat) == 6
        b = await _ledger_matches_balances(c0, a["account_id"], H.T0 + 10)
        assert b["cash_usd"] == 500000.0
    finally:
        for c in (c0, c1, c2):
            await c.close()


async def _counts(conn, acct) -> dict:
    q = {"decisions": "SELECT count(*) FROM paper_decisions WHERE "
                      "session_id=$1",
         "orders": "SELECT count(*) FROM paper_orders WHERE session_id=$1",
         "fills": "SELECT count(*) FROM paper_fills WHERE session_id=$1",
         "ledger": "SELECT count(*) FROM paper_ledger l JOIN paper_accounts a"
                   " ON a.account_id=l.account_id JOIN paper_sessions s ON "
                   " s.account_id=a.account_id WHERE s.session_id=$1",
         "handoffs": "SELECT count(*) FROM paper_handoffs WHERE "
                     "session_id=$1",
         "reviews": "SELECT count(*) FROM paper_xavier_reviews WHERE "
                    "session_id=$1"}
    return {k: int(await conn.fetchval(v, acct["session_id"]))
            for k, v in q.items()}


@pg
async def test_repeated_valuations_and_restarts_duplicate_nothing(
        bench_on, monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        await _no_foreign_valuation_in_the_pass_window(conn, now)
        monkeypatch.setenv(S.ENV_FLAG, "on")
        acct = await PL.new_account(conn, "benchrst", now=now)
        v = await PL.valuation(conn, decided_at=now - 2, p_pin=0.62)
        t = PL.Transport(now)
        t.step = 0.1
        t.set(v["slug"], offers=[(0.50, 1000)], bids=[(0.48, 1000)])

        async def hook(c):
            return await PR.decide_valuation(
                c, valuation_id=v["valuation_id"], now=now,
                market_data=PL.client(t), account_id=acct["account_id"],
                fee_fn=FEE, schedule_fill=lambda: {"scheduled": False})
        g1 = await hook(conn)
        assert g1["benchmark"]["verdict"] == "ENTER"
        s0 = await _counts(conn, acct)
        assert s0 == {"decisions": 2, "orders": 1, "fills": 0, "ledger": 2,
                      "handoffs": 0, "reviews": 0}
        # THE SAME VALUATION AGAIN: the in-cycle hook, then the backstop
        g2 = await hook(conn)
        assert g2["duplicate"] is True and g2["benchmark"]["duplicate"]
        row = await conn.fetchrow("SELECT * FROM external_valuations WHERE "
                                  " id=$1", v["valuation_id"])
        ctx = {"session_id": acct["session_id"],
               "account_id": acct["account_id"], "config": acct["config"],
               "market_data": PL.client(t), "books_read": 0, "now": now + 1,
               "deadline": time.monotonic() + 30, "fee_fn": FEE}
        rec = await PB.decide_one(conn, ctx, dict(row))
        assert rec.get("duplicate") is True and not rec.get("order_id")
        assert await _counts(conn, acct) == s0
        # RESTART 1: after the order was submitted, before any fill
        await conn.close()
        conn = await H.connect()
        PR._LOCK.update(lock=None, loop=None)
        PB._CONTEXT_CACHE.clear()
        PD._CONTEXT_CACHE.clear()
        cut = [s for s in PR.default_steps()
               if s[0] in ("books", "simulate", "derek", "benchmark",
                           "simulate_after_delay")]
        p1 = await _pass(conn, acct, t, now + 5, steps=cut)
        assert not p1["errors"], p1["errors"]
        s1 = await _counts(conn, acct)
        assert s1 == dict(s0, fills=1, ledger=3)        # one FILL entry
        p1b = await _pass(conn, acct, t, now + 6, steps=cut)
        assert not p1b["errors"] and await _counts(conn, acct) == s1
        # RESTART 2: after the fill, before the handoff
        await conn.close()
        conn = await H.connect()
        PR._LOCK.update(lock=None, loop=None)
        p2 = await _pass(conn, acct, t, now + 10)
        assert not p2["errors"], p2["errors"]
        s2 = await _counts(conn, acct)
        assert s2["handoffs"] == 1 and s2["fills"] == 1
        assert s2["decisions"] == 2 and s2["ledger"] == 3
        assert s2["reviews"] == 1                       # FIRST_FILL
        p3 = await _pass(conn, acct, t, now + 10)       # replayed
        assert not p3["errors"] and await _counts(conn, acct) == s2
        h = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                " session_id=$1", acct["session_id"])
        assert h["strategy"] == PB.STRATEGY
        assert float(h["confirmed_qty"]) == 1000.0
        b = await _ledger_matches_balances(conn, acct["account_id"], now + 11)
        assert b["cash_usd"] == pytest.approx(500000.0 - 500.0 - 10.0)
    finally:
        await PL.drop_today_run(conn, now)
        await PL.purge_everything(conn)
        await conn.close()


def _fake_research_model(monkeypatch, p_internal=0.66, low_at_or_above=None):
    """A stand-in research model for the TWO-MODEL strategy (its real fit is
    proven in test_paper_vertical_slice_derek_to_audrey); here only so both
    strategies can hold positions in one account."""
    model = {"ok": True, "refusal": None, "model": {},
             "model_id": "test-fake-research-model", "model_version": "v1",
             "approval_status": "CANDIDATE", "label": PD.MODEL_LABEL,
             "created_at": 0.0, "promoted": False,
             "provenance_verified": True,
             "features": ["acquisition_price", "payout_is_complement"]}

    async def research_model(conn, *, at, verify=True):
        return dict(model)

    def score(m, *, price, payout_is_complement):
        # optionally a LOW reading at or above a price, so the two-model
        # strategy declines those books and the benchmark can hold them
        p = (0.30 if low_at_or_above is not None
             and float(price) >= float(low_at_or_above) else p_internal)
        return {"ok": True, "p": p, "feature_sha": "test",
                "features": {"acquisition_price": price,
                             "payout_is_complement": 0.0},
                "feature_basis": "test stand-in"}
    monkeypatch.setattr(PD, "research_model", research_model)
    monkeypatch.setattr(PD, "score", score)
    PD._CONTEXT_CACHE.clear()


@pg
async def test_two_model_entries_switch_off_records_but_places_nothing(
        bench_on, monkeypatch):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        _fake_research_model(monkeypatch)
        prev = await PL.two_model_entries(conn, False)
        try:
            acct = await PL.new_account(conn, "benchsw", now=now)
            v = await PL.valuation(conn, decided_at=now - 5, p_pin=0.62)
            t = PL.Transport(now)
            t.set(v["slug"], offers=[(0.50, 3000), (0.56, 2000)],
                  bids=[(0.48, 5000)])
            p = await _pass(conn, acct, t, now)
            assert not p["errors"], p["errors"]
        finally:
            await PL.restore_two_model_entries(conn, prev)
        ds = await _decisions(conn, acct, v["valuation_id"])
        d2 = ds["DEREK_ENTRY_POLICY_V2"]
        assert d2["verdict"] == "REFUSE"
        assert d2["refusal"] == PD.R_ENTRIES_DISABLED == \
            "STRATEGY_ENTRIES_DISABLED"
        pd2 = H.j(d2["policy_decision"])
        assert pd2["admitted"] is True                  # the evidence stays
        assert pd2["entries_switch"]["enabled"] is False
        assert d2["p_internal"] is not None             # it decided fully
        assert ds[PB.STRATEGY]["verdict"] == "ENTER"
        orders = await conn.fetch("SELECT strategy FROM paper_orders WHERE "
                                  " account_id=$1 AND role='ENTRY'",
                                  acct["account_id"])
        assert [o["strategy"] for o in orders] == [PB.STRATEGY]
    finally:
        await PL.drop_today_run(conn, now)
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_both_strategies_purchases_sales_settlements_reconcile(
        bench_on, monkeypatch):
    """Both strategies hold positions on one account (the two-model entry
    switch on for this proof) -- on DIFFERENT games, because a strategy never
    duplicates another's exposure (migration 184 rule): the two-model
    strategy enters markets A2/B2 and the benchmark is refused there by name;
    the benchmark enters A1/B1, which the two-model strategy declines. Four
    groups, four handoffs with the right strategy, quantity and position; the
    A markets' protections are sold by a strict cross (SALE), the B markets
    settle (SETTLEMENT); every figure reconciles to the one shared ledger, per
    strategy and in total."""
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        _fake_research_model(monkeypatch, low_at_or_above=0.55)
        prev = await PL.two_model_entries(conn, True)
        try:
            cfg = PL.config(entry={"target_order_usd": 2000.0})
            acct = await PL.new_account(conn, "benchboth", now=now, cfg=cfg)
            va = await PL.valuation(conn, decided_at=now - 5, p_pin=0.62)
            vb = await PL.valuation(conn, decided_at=now - 5, p_pin=0.62)
            va2 = await PL.valuation(conn, decided_at=now - 5, p_pin=0.62)
            vb2 = await PL.valuation(conn, decided_at=now - 5, p_pin=0.62)
            t = PL.Transport(now)
            for v in (va, vb):           # the benchmark's: 7 pp at 0.55
                t.set(v["slug"], offers=[(0.55, 6000), (0.56, 6000)],
                      bids=[(0.48, 5000)])
            for v in (va2, vb2):         # the two-model strategy's
                t.set(v["slug"], offers=[(0.50, 6000), (0.54, 6000)],
                      bids=[(0.48, 5000)])
            client = PL.client(t)
            p1 = await _pass(conn, acct, t, now, client=client)
            assert not p1["errors"], p1["errors"]
            orders = await conn.fetch(
                "SELECT * FROM paper_orders WHERE account_id=$1 AND "
                " role='ENTRY' ORDER BY strategy, us_market_slug",
                acct["account_id"])
            assert len(orders) == 4
            by = {o["us_market_slug"]: o["strategy"] for o in orders}
            assert by == {va["slug"]: PB.STRATEGY, vb["slug"]: PB.STRATEGY,
                          va2["slug"]: "DEREK_ENTRY_POLICY_V2",
                          vb2["slug"]: "DEREK_ENTRY_POLICY_V2"}
            for v in (va2, vb2):
                d = (await _decisions(conn, acct, v["valuation_id"]))[
                    PB.STRATEGY]
                assert d["refusal"] == PB.R_CROSS_STRATEGY
            assert {o["state"] for o in orders} == {"FILLED"}
            for o in orders:
                fl = await conn.fetch("SELECT * FROM paper_fills WHERE "
                                      " order_id=$1", o["order_id"])
                assert {f["strategy"] for f in fl} == {o["strategy"]}
                assert sum(float(f["qty"]) for f in fl) == float(o["qty"])
                assert all(L._epoch(f["book_observed_at"]) >=
                           L._epoch(o["eligible_at"]) for f in fl)
                h = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE "
                                        " group_id=$1", o["group_id"])
                assert h["strategy"] == o["strategy"]
                assert float(h["confirmed_qty"]) == float(o["qty"])
                pos = [p for p in await L.positions(conn, acct["account_id"])
                       if p["group_id"] == o["group_id"]]
                assert len(pos) == 1 and pos[0]["strategy"] == o["strategy"]
                assert pos[0]["open_qty"] == float(o["qty"])
                r = await conn.fetchrow(
                    "SELECT strategy, measure FROM paper_xavier_reviews "
                    " WHERE group_id=$1", o["group_id"])
                assert r["strategy"] == o["strategy"]
                if o["strategy"] == PB.STRATEGY:
                    assert H.j(r["measure"])["strategy"] == PB.STRATEGY
                else:
                    assert H.j(r["measure"])["source"] == "CURRENT_BLEND"
            mid = await _ledger_matches_balances(conn, acct["account_id"],
                                                 now + 5)
            assert mid["reserved_usd"] == 0.0
            # A markets: a strict cross sells every protection; B: settle WON
            for v in (va, va2):
                t.set(v["slug"], offers=[(0.82, 100)],
                      bids=[(0.80, 100000)])
            for v in (vb, vb2):
                await PL.settle_valuation(conn, v["valuation_id"], outcome=1)
            p2 = await _pass(conn, acct, t, now + 120, client=client)
            assert not p2["errors"], p2["errors"]
        finally:
            await PL.restore_two_model_entries(conn, prev)
        sales = await conn.fetch(
            "SELECT f.strategy, f.qty, f.price FROM paper_fills f WHERE "
            " f.account_id=$1 AND f.direction='SELL'", acct["account_id"])
        assert sorted(x["strategy"] for x in sales) == sorted(
            ["DEREK_ENTRY_POLICY_V2", PB.STRATEGY]), sales
        b = await _ledger_matches_balances(conn, acct["account_id"],
                                           now + 130)
        assert b["reserved_usd"] == 0.0 and not b["open_positions"]
        assert b["available_usd"] == pytest.approx(b["cash_usd"])
        per = {r["strategy"]: r for r in await conn.fetch(
            "SELECT o.strategy, sum(l.cash_delta_usd) AS cash, "
            "       sum(l.reserved_delta_usd) AS res, "
            "       array_agg(DISTINCT l.kind) AS kinds "
            "  FROM paper_ledger l JOIN (SELECT DISTINCT group_id, strategy "
            "    FROM paper_orders WHERE account_id=$1) o "
            "    ON o.group_id = l.group_id WHERE l.account_id=$1 "
            " GROUP BY 1", acct["account_id"])}
        assert set(per) == {"DEREK_ENTRY_POLICY_V2", PB.STRATEGY}
        for k in per.values():
            assert set(k["kinds"]) == {"ORDER_SUBMITTED", "FILL", "SALE",
                                       "SETTLEMENT"}, k["kinds"]
            assert float(k["res"]) == 0.0
        total = 500000.0 + sum(float(k["cash"]) for k in per.values())
        assert b["cash_usd"] == pytest.approx(total)
        rep = await conn.fetchrow(
            "SELECT * FROM paper_audrey_reports WHERE session_id=$1 "
            " ORDER BY version DESC LIMIT 1", acct["session_id"])
        assert rep["reconciles"] is True
        sec = H.j(rep["report"])["pinnacle_only_paper_benchmark"]
        assert sec["handoffs_to_xavier_total"] == 2
        assert client.mutation_attempts == 0
        assert (await S.health(conn, acct["session_id"]))[
            "mutation_attempts"] == 0
    finally:
        await PL.drop_today_run(conn, now)
        await PL.purge_everything(conn)
        await conn.close()
