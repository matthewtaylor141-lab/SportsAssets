"""CAPITAL-CRITICAL (R30A, owner audit 2026-10-04 P0 #5): EVERY
PRODUCTION-CONFIDENCE NUMBER IS INVESTMENT-ONLY -- A TRAINING WIN OR LOSS
CANNOT MOVE ANY OF THEM.

THE DEFECT. Migration 223 gave every paper position group one durable sleeve
(INVESTMENT / TRAINING / BENCHMARK / UNCLASSIFIED), but the Profitability
OS's top-level numbers were still computed per BOOK: the north-star metrics,
the 30-day forecast, the 24H/7D/30D horizon forecasts, the CAPACITY rates
behind the forecast ceiling and the Opportunity Score, the Opportunity
Score's calibration component, the twin's evidence ladder and the quality
scorecard's forward-sample verdict all pooled every paper strategy. An
exploration loss or win moved the statistics a live-capital decision reads.

THE PROOFS.
  §1 PINS. The sleeve map every non-paper module carries is the classifier's
     (bettor_paper_sleeves / migration 223); the executable book-freshness
     standard is the entry decision's own bound (paper_benchmark).
  §2 PURE. Adding TRAINING (and BENCHMARK and UNCLASSIFIED) positions -- a
     huge win and a huge loss -- leaves every INVESTMENT metric, forecast,
     horizon forecast, sleeve profit, ladder input and forward-sample verdict
     byte-for-byte unchanged, while the TRAINING figures move and are
     labelled research. A row with no sleeve is never INVESTMENT. Every
     scoped figure carries book, sleeve, strategy and policy_version(s).
  §3 CAPACITY AT EXECUTABLE FRESHNESS. A book older than the strategy's
     entry bound (or observed after the decision, not its own) is
     UNAVAILABLE for production capacity, never re-priced; the research
     aggregate keeps it, labelled research.
  §4 THE REAL CYCLE (Postgres). Positions written by the real paper ledger
     (submit -> simulate -> settle; the entry trigger classifies them): a
     TRAINING win and loss added between two cycles change no INVESTMENT
     metric row, no production capacity rate and no INVESTMENT profit line;
     the TRAINING rows appear separately; the routes serve INVESTMENT as
     data.PAPER.
  §5 THE SCHEMA (migration 227). A new metric / forecast row must name its
     scope; a TRAINING row can never be labelled PRODUCTION_CONFIDENCE; the
     rollback refuses while scoped rows exist and applies cleanly otherwise.
  §6 THE CONSUMERS. The Opportunity Score reads the INVESTMENT calibration
     only; the twin ladder and the quality scorecard count INVESTMENT only.
"""
from __future__ import annotations

import json
import pathlib
import time
import uuid
from contextlib import asynccontextmanager

import asyncpg
import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import bettor_paper_sleeves as SL
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import quality_scorecard as QS
from sportsassets.lost_opportunity import horizons as HZ
from sportsassets.profitability import capacity as CP
from sportsassets.profitability import common as C
from sportsassets.profitability import economics as EC
from sportsassets.profitability import forecast as FC
from sportsassets.profitability import metrics as MT
from sportsassets.profitability import runner as RUN
from sportsassets.profitability import validation as V
from sportsassets.twin import runner as TRUN

try:
    from tests import paper_harness as H
    from tests import pos_fixture as P
except ImportError:                                             # pragma: no cover
    import paper_harness as H
    import pos_fixture as P

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
UP = (ROOT / "migrations" / "227_investment_only_confidence.sql").read_text()
DOWN = (ROOT / "migrations" / "rollback" /
        "227_investment_only_confidence.down.sql").read_text()
DAY = 86400.0
HOUR = 3600.0
NOW = 1_800_000_000.0
CG = "PINNACLE_COMPLETED_GAME_PAPER"
EXPLORE = "PINNACLE_EXPLORATION_PAPER"
BENCH = "PINNACLE_ONLY_PAPER_BENCHMARK"
FEE = H.flat_fee(0.01)


# ── §1 the pins ──────────────────────────────────────────────────────

def test_every_sleeve_map_is_the_classifiers():
    assert C.SLEEVES == SL.SLEEVES == V.SLEEVES
    assert C.STRATEGY_SLEEVE == SL.STRATEGY_SLEEVE
    assert TRUN.STRATEGY_SLEEVE == SL.STRATEGY_SLEEVE
    assert QS.STRATEGY_SLEEVE == SL.STRATEGY_SLEEVE
    assert QS.SLEEVES == SL.SLEEVES
    assert set(C.INVESTMENT_STRATEGIES) == {
        s for s, v in SL.STRATEGY_SLEEVE.items() if v == SL.INVESTMENT}
    assert C.PRODUCTION_SLEEVE == SL.INVESTMENT


def test_the_executable_freshness_standard_is_the_entry_rule():
    assert CP.EXECUTABLE_BOOK_MAX_AGE_S == PB.BOOK_MAX_AGE_S
    # every paper strategy is named, and each one's bound is its entry rule
    assert set(CP.EXECUTABLE_BOOK_MAX_AGE_BY_STRATEGY) == set(
        SL.STRATEGY_SLEEVE)
    assert set(CP.EXECUTABLE_BOOK_MAX_AGE_BY_STRATEGY.values()) == {
        PB.BOOK_MAX_AGE_S}
    # the research bound stays what it was, and it is NOT executable
    assert CP.MAX_BOOK_AGE_S > CP.EXECUTABLE_BOOK_MAX_AGE_S


# ── §2 pure: a TRAINING win or loss moves no production number ───────

def _row(i, net, *, sleeve, book="PAPER", strategy=CG, days_ago=1.0,
         cap=100.0, ch=50.0, exp=2.0, pv="PINNACLE_COMPLETED_GAME_PAPER_V3"):
    rel = NOW - days_ago * DAY
    return {"book": book, "position_key": "%s:%s:%d" % (book, sleeve, i),
            "group_id": "g-%s-%d" % (sleeve, i), "state": "CLOSED",
            "strategy": strategy, "sleeve": sleeve, "policy_version": pv,
            "classifier_version": "PAPER_SLEEVE_V1",
            "net_profit_usd": net, "capital_committed_usd": cap,
            "capital_hours": ch, "expected_net_profit_usd": exp,
            "predicted_edge_per_dollar": exp / cap,
            "realized_edge_per_dollar": None if net is None else net / cap,
            "released_at": rel, "last_event_at": rel,
            "opened_at": rel - DAY, "first_fill_at": rel - DAY,
            "segments": [(rel - DAY, rel, cap)]}


def _investment():
    return [_row(i, (3.0 if i % 3 else -2.0), sleeve="INVESTMENT",
                 days_ago=1 + i * 0.7) for i in range(40)]


def _contamination():
    """A TRAINING win and loss far larger than the investment book, a
    BENCHMARK win, and positions with no / an unknown sleeve."""
    rows = [_row(100, 5000.0, sleeve="TRAINING", strategy=EXPLORE,
                 cap=4000.0, ch=900.0),
            _row(101, -7000.0, sleeve="TRAINING", strategy=EXPLORE,
                 cap=7000.0, ch=900.0, days_ago=2.0),
            _row(102, 800.0, sleeve="BENCHMARK", strategy=BENCH)]
    nos = _row(103, 9999.0, sleeve="INVESTMENT", strategy=CG)
    nos.pop("sleeve")                      # no sleeve: UNCLASSIFIED
    odd = _row(104, -9999.0, sleeve="investment", strategy=CG)  # unknown
    return rows + [nos, odd]


def _strip(ms):
    return {m["metric"]: {k: m[k] for k in (
        "value", "sample_n", "ci_low", "ci_high", "status", "why",
        "data_as_of", "detail")} for m in ms}


def test_a_training_win_or_loss_cannot_move_any_investment_metric():
    inv = _investment()
    base = MT.compute(inv, book="PAPER", now=NOW, lookback_days=90,
                      sleeve="INVESTMENT")
    mixed = MT.compute(inv + _contamination(), book="PAPER", now=NOW,
                       lookback_days=90, sleeve="INVESTMENT")
    assert _strip(base) == _strip(mixed)
    assert all(m["value"] is not None for m in base)
    # the training figures exist, separately, and say they are research
    tr = {m["metric"]: m for m in MT.compute(
        inv + _contamination(), book="PAPER", now=NOW, lookback_days=90,
        sleeve="TRAINING")}
    assert tr["REALIZED_NET_EDGE"]["sample_n"] == 2
    assert tr["REALIZED_NET_EDGE"]["value"] == pytest.approx(-2000.0 / 11000)
    assert tr["REALIZED_NET_EDGE"]["confidence_scope"] == C.RESEARCH_SCOPE
    # the pooled (pre-227) book-wide figure WOULD have moved: the defect
    pooled = {m["metric"]: m for m in MT.compute(
        inv + _contamination(), book="PAPER", now=NOW, lookback_days=90)}
    assert pooled["REALIZED_NET_EDGE"]["value"] != \
        _strip(base)["REALIZED_NET_EDGE"]["value"]
    assert pooled["REALIZED_NET_EDGE"]["confidence_scope"] == \
        C.RESEARCH_SCOPE


def test_every_scoped_metric_names_its_book_sleeve_strategy_and_policy():
    rows = _investment() + _contamination()
    for sleeve, strategy in C.scopes(rows, book="PAPER"):
        for m in MT.compute(rows, book="PAPER", now=NOW, lookback_days=90,
                            sleeve=sleeve, strategy=strategy):
            assert m["book"] == "PAPER" and m["sleeve"] == sleeve
            assert m["strategy"] == strategy
            assert isinstance(m["policy_versions"], list)
            assert "policy_version" in m and "classifier_version" in m
            assert m["confidence_scope"] == (
                C.PRODUCTION if sleeve == "INVESTMENT" else C.RESEARCH_SCOPE)
    inv = MT.compute(rows, book="PAPER", now=NOW, lookback_days=90,
                     sleeve="INVESTMENT")[0]
    assert inv["policy_version"] == "PINNACLE_COMPLETED_GAME_PAPER_V3"
    assert inv["policy_versions"] == ["PINNACLE_COMPLETED_GAME_PAPER_V3"]
    # the per-strategy scope of a TRAINING strategy is TRAINING, research
    scopes = C.scopes(rows, book="PAPER")
    assert ("TRAINING", EXPLORE) in scopes
    assert ("UNCLASSIFIED", CG) in scopes          # the sleeve-less rows
    assert ("INVESTMENT", "ALL") in scopes


def test_unclassified_is_never_investment():
    rows = _contamination()
    inv = [r for r in rows if C.in_scope(r, sleeve="INVESTMENT")]
    assert inv == []
    assert C.sleeve_of({"sleeve": None}) == "UNCLASSIFIED"
    assert C.sleeve_of({"sleeve": "investment"}) == "UNCLASSIFIED"
    assert C.sleeve_of({}) == "UNCLASSIFIED"
    assert C.strategy_sleeve(None) == "UNCLASSIFIED"
    assert C.strategy_sleeve("SOMETHING_NEW") == "UNCLASSIFIED"
    assert C.confidence_scope("UNCLASSIFIED") == C.RESEARCH_SCOPE


def _history(sleeve, *, strategy=CG, n=40, net=lambda i: 2.0):
    return [_row(i, net(i), sleeve=sleeve, strategy=strategy,
                 days_ago=1 + i * 0.5) for i in range(n)]


def test_a_training_win_or_loss_cannot_move_the_investment_forecasts():
    inv = _history("INVESTMENT", net=lambda i: 2.0 if i % 4 else -3.0)
    tr = _history("TRAINING", strategy=EXPLORE,
                  net=lambda i: 900.0 if i % 2 else -1200.0)
    a = FC.build(inv, book="PAPER", now=NOW, lookback_days=90,
                 sleeve="INVESTMENT", capacity_daily=10.0,
                 fill_probability=0.5)
    b = FC.build(inv + tr + _contamination(), book="PAPER", now=NOW,
                 lookback_days=90, sleeve="INVESTMENT", capacity_daily=10.0,
                 fill_probability=0.5)
    assert a["status"] == b["status"] == C.UNPROVEN
    assert a["inputs_sha256"] == b["inputs_sha256"]
    for k in ("expected_pnl_usd", "p10_pnl_usd", "p50_pnl_usd",
              "p90_pnl_usd", "prob_positive", "expected_max_drawdown_usd",
              "capital_required_usd", "turnover_required_usd",
              "capacity_ceiling_usd", "sample_positions", "quantiles"):
        assert a[k] == b[k], k
    assert b["sleeve"] == "INVESTMENT"
    assert b["confidence_scope"] == C.PRODUCTION
    t = FC.build(inv + tr, book="PAPER", now=NOW, lookback_days=90,
                 sleeve="TRAINING",
                 capacity_why="CAPACITY_IS_MEASURED_FOR_THE_PAPER_"
                              "INVESTMENT_SLEEVE_ONLY")
    assert t["sample_positions"] == 40 and t["expected_pnl_usd"] != \
        a["expected_pnl_usd"]
    assert t["confidence_scope"] == C.RESEARCH_SCOPE
    assert t["capacity_ceiling_usd"] is None
    assert "INVESTMENT_SLEEVE_ONLY" in t["unmeasured"]["capacity_ceiling_usd"]
    # the horizon forecasts, the same way
    for key, days in HZ.HORIZONS:
        ha = HZ.build(inv, book="PAPER", horizon=key, days=days, now=NOW,
                      lookback_days=90, sleeve="INVESTMENT")
        hb = HZ.build(inv + tr + _contamination(), book="PAPER", horizon=key,
                      days=days, now=NOW, lookback_days=90,
                      sleeve="INVESTMENT")
        assert ha["inputs_sha256"] == hb["inputs_sha256"], key
        for k in ("expected_pnl_usd", "p10_pnl_usd", "p90_pnl_usd",
                  "prob_positive", "expected_turnover_usd",
                  "expected_capital_hours", "sample_positions"):
            assert ha[k] == hb[k], (key, k)
        assert hb["sleeve"] == "INVESTMENT"
        assert hb["confidence_scope"] == C.PRODUCTION


def test_a_training_win_or_loss_cannot_move_the_investment_profit_lines():
    inv = _investment()
    a = EC.sleeve_profit(inv, book="PAPER", sleeve="INVESTMENT", now=NOW)
    b = EC.sleeve_profit(inv + _contamination(), book="PAPER",
                         sleeve="INVESTMENT", now=NOW)
    assert a == b
    assert a["realized_net_profit_usd"] is not None
    assert a["confidence_scope"] == C.PRODUCTION
    t = EC.sleeve_profit(inv + _contamination(), book="PAPER",
                         sleeve="TRAINING", now=NOW)
    assert t["realized_net_profit_usd"] == pytest.approx(-2000.0)
    assert t["confidence_scope"] == C.RESEARCH_SCOPE


def test_a_training_win_or_loss_cannot_move_the_twin_ladder_inputs():
    pos = [{"group_id": "gi%d" % i, "realized_pnl_usd": 1.0} for i in
           range(5)] + [{"group_id": "gt%d" % i, "realized_pnl_usd": -50.0}
                        for i in range(5)]
    closed = {"PAPER": [dict(p, sleeve="INVESTMENT" if p["group_id"]
                             .startswith("gi") else "TRAINING")
                        for p in pos], "ACTUAL": [
        {"group_id": "ga", "realized_pnl_usd": 3.0}]}
    sleeves = {"gi%d" % i: "INVESTMENT" for i in range(5)}
    sleeves.update({"gt%d" % i: "TRAINING" for i in range(5)})
    got = TRUN.ladder_inputs(pos, closed, [], sleeves)
    assert [p["group_id"] for p in got["paper_closed"]] == [
        "gi%d" % i for i in range(5)]
    assert [p["group_id"] for p in got["paper_positions"]] == [
        "gi%d" % i for i in range(5)]
    assert got["actual_closed"] == []          # no sleeve: UNCLASSIFIED
    assert got["scope"]["by_sleeve"]["paper_closed"] == {
        "INVESTMENT": 5, "TRAINING": 5}
    assert got["scope"]["confidence_scope"] == C.PRODUCTION


def test_a_training_win_or_loss_cannot_move_the_forward_sample_verdict():
    start = QS.FORWARD_START
    inv = [{"opened_at": start + i * HOUR, "closed_at": start + i * HOUR + 60,
            "fixture": "fx-i%d" % i, "us_market_slug": "s-i%d" % i,
            "realized_pnl_usd": 1.0, "sleeve": "INVESTMENT"}
           for i in range(4)]
    tr = [dict(r, fixture="fx-t%d" % i, realized_pnl_usd=-500.0,
               sleeve="TRAINING") for i, r in enumerate(inv)]
    now = start + 10 * DAY
    a = QS.forward_verdict([r for r in inv if r["sleeve"] == "INVESTMENT"],
                           now=now)
    b = QS.forward_verdict([r for r in inv + tr
                            if r["sleeve"] == "INVESTMENT"], now=now)
    assert a == b and a["positions"] == 4


# ── §3 capacity at the strategy's executable freshness ────────────────

def test_executable_freshness_is_the_entry_rules_bound():
    b = CP.EXECUTABLE_BOOK_MAX_AGE_S
    own = CP.executable_fresh(strategy=CG, book_obs_id=7,
                              decision_book_obs_id=7, book_age_s=-0.4,
                              recorded_age_s=b - 1)
    assert own["fresh"] and own["source"] == CP.OWN_BOOK
    assert own["age_basis"] == "paper_decisions.book.age_at_decision_s"
    stale_own = CP.executable_fresh(strategy=CG, book_obs_id=7,
                                    decision_book_obs_id=7, book_age_s=1.0,
                                    recorded_age_s=b + 0.5)
    assert not stale_own["fresh"]
    assert stale_own["why"] == CP.R_NOT_EXECUTABLE_FRESH
    no_rec = CP.executable_fresh(strategy=CG, book_obs_id=7,
                                 decision_book_obs_id=7, book_age_s=-(b - 1))
    assert no_rec["fresh"]                         # its own book, |age|
    latest = CP.executable_fresh(strategy=CG, book_obs_id=8,
                                 decision_book_obs_id=None, book_age_s=b - 2)
    assert latest["fresh"] and latest["source"] == CP.LATEST_BOOK
    old = CP.executable_fresh(strategy=CG, book_obs_id=8,
                              decision_book_obs_id=None, book_age_s=290.0)
    assert not old["fresh"] and old["why"] == CP.R_NOT_EXECUTABLE_FRESH
    later = CP.executable_fresh(strategy=CG, book_obs_id=8,
                                decision_book_obs_id=7, book_age_s=-3.0)
    assert not later["fresh"] and later["why"] == CP.R_BOOK_AFTER_DECISION
    none = CP.executable_fresh(strategy=CG, book_obs_id=None,
                               decision_book_obs_id=None, book_age_s=None)
    assert not none["fresh"] and none["why"] == CP.R_NO_BOOK
    unknown = CP.executable_fresh(strategy=CG, book_obs_id=8,
                                  decision_book_obs_id=None, book_age_s=None)
    assert unknown["why"] == CP.R_NO_BOOK_AGE


def _cap_row(cid, *, strategy=CG, age=2.0, opp=10.0, slug=None,
             decided=NOW - 600):
    return {"candidate_id": cid, "us_market_slug": slug or "m-" + cid,
            "holding_side": "LONG", "status": "MEASURED", "why": None,
            "strategy": strategy, "book_obs_id": 1, "book_age_s": age,
            "decision_book_obs_id": None, "decision_book_age_s": None,
            "theoretical_opportunity_dollars": opp * 2,
            "executable_opportunity_dollars": opp,
            "executable_capacity_usd": opp * 10,
            "capacity_ceiling_usd": opp * 20, "decided_at": decided}


def test_production_capacity_counts_only_fresh_investment_books():
    rows = [_cap_row("a"), _cap_row("b", age=150.0),        # 300 s research
            _cap_row("c", strategy=EXPLORE, opp=500.0),     # TRAINING
            _cap_row("d", strategy=BENCH, opp=300.0)]       # BENCHMARK
    research = CP.aggregate(rows, rates={})
    assert research["EXECUTABLE_OPPORTUNITY_DOLLARS"] == pytest.approx(820.0)
    prod_rows = [CP.executable_view(r) for r in rows
                 if C.strategy_sleeve(r["strategy"]) == "INVESTMENT"]
    prod = CP.aggregate(prod_rows, rates={}, scope={"sleeve": "INVESTMENT"})
    assert prod["EXECUTABLE_OPPORTUNITY_DOLLARS"] == pytest.approx(10.0)
    assert prod["measured_markets"] == 1
    assert prod["unavailable_by_reason"] == {CP.R_NOT_EXECUTABLE_FRESH: 1}
    stale = CP.executable_view(_cap_row("b", age=150.0))
    assert stale["status"] == C.UNAVAILABLE
    assert stale["executable_opportunity_dollars"] is None   # never a number
    assert stale["executable_freshness"]["bound_s"] == \
        CP.EXECUTABLE_BOOK_MAX_AGE_S


# ── §4 the real cycle ────────────────────────────────────────────────

@asynccontextmanager
async def _txn():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


async def _settled(conn, acct, *, strategy, at, qty, price, outcome):
    """A position written by the REAL paper ledger: an ENTRY order carrying
    its strategy (the migration-223 entry trigger classifies its group),
    filled by the simulator on a recorded book, settled once."""
    slug = "r30a-io-%s" % uuid.uuid4().hex[:10]
    g = "paper_group_%s" % uuid.uuid4().hex[:12]
    o = dict(H.order(acct, key=uuid.uuid4().hex[:8], slug=slug, qty=qty,
                     limit=price, at=at, group_id=g, fixture="fx-" + slug),
             strategy=strategy)
    got = await L.submit_order(conn, o, fee_fn=FEE, now=at)
    assert got["ok"], got
    await H.observe(conn, slug, at + 3, offers=[(price, qty * 2)],
                    bids=[(round(price - 0.02, 2), qty * 2)])
    sim = await SIM.simulate_order(conn, got["order"]["order_id"],
                                   now=at + 4, fee_fn=FEE)
    assert not sim.get("refusal"), sim
    st = await L.settle(conn, account_id=acct["account_id"], group_id=g,
                        slug=slug, holding_side="LONG",
                        settlement_event_key="ev-" + slug, outcome=outcome,
                        evidence={}, evidence_source="TEST_EVIDENCE",
                        at=at + 6 * HOUR)
    assert st.get("ok", True), st
    return g


async def _production(conn):
    rows = await conn.fetch(
        "SELECT DISTINCT ON (metric) metric, value, sample_n, ci_low, "
        "       ci_high, status, why, content_sha256, run_id "
        "  FROM pos_metric_observations WHERE book = 'PAPER' "
        "   AND sleeve = 'INVESTMENT' AND strategy = 'ALL' "
        " ORDER BY metric, computed_at DESC")
    return {r["metric"]: dict(r) for r in rows}


async def _snapshot(conn, component, book):
    return P.j(await conn.fetchval(
        "SELECT payload FROM pos_snapshots WHERE component = $1 AND book = $2"
        " ORDER BY computed_at DESC LIMIT 1", component, book))


@pg
async def test_the_real_cycle_keeps_training_out_of_every_production_number(
        monkeypatch):
    from sportsassets.api import command_profitability as CPR
    now = time.time()
    async with _txn() as conn:
        acct = await H.new_account(conn, "r30aio", now=now - 40 * DAY)
        await P.lag_history(conn, acct, now=now)
        groups = []
        for i, (outcome, px) in enumerate((("WON", 0.40), ("WON", 0.55),
                                           ("LOST", 0.45))):
            groups.append(await _settled(
                conn, acct, strategy=CG, at=now - (5 + i) * DAY, qty=50,
                price=px, outcome=outcome))
        sleeves = {r["group_id"]: r["sleeve"] for r in await conn.fetch(
            "SELECT group_id, sleeve FROM paper_sleeve_current_v "
            " WHERE group_id = ANY($1::text[])", groups)}
        assert set(sleeves.values()) == {"INVESTMENT"}, sleeves
        first = await RUN.run_cycle(conn, now=now,
                                    account_id=acct["account_id"],
                                    include_actual=False)
        assert set(first["components"].values()) == {"OK"}, first
        before = await _production(conn)
        assert set(before) == set(MT.METRICS)
        assert before["REALIZED_NET_EDGE"]["sample_n"] == 3
        cap1 = await _snapshot(conn, "CAPACITY", "NONE")
        capital1 = await _snapshot(conn, "CAPITAL", "PAPER")

        # ── a TRAINING win and a TRAINING loss, both large ─────────────
        tg = [await _settled(conn, acct, strategy=EXPLORE,
                             at=now - 4 * DAY, qty=900, price=0.30,
                             outcome="WON"),
              await _settled(conn, acct, strategy=EXPLORE,
                             at=now - 3 * DAY, qty=1500, price=0.60,
                             outcome="LOST")]
        assert {r["sleeve"] for r in await conn.fetch(
            "SELECT sleeve FROM paper_sleeve_current_v "
            " WHERE group_id = ANY($1::text[])", tg)} == {"TRAINING"}
        second = await RUN.run_cycle(conn, now=now + 60,
                                     account_id=acct["account_id"],
                                     include_actual=False)
        assert set(second["components"].values()) == {"OK"}, second
        after = await _production(conn)
        # NOT ONE INVESTMENT METRIC MOVED -- and none was even re-written
        for m in MT.METRICS:
            for k in ("value", "sample_n", "ci_low", "ci_high", "status",
                      "why", "content_sha256"):
                assert before[m][k] == after[m][k], (m, k)
            assert after[m]["run_id"] == first["run_id"], m
        # ...while the TRAINING sleeve's rows appear, separately, research
        tr = {r["metric"]: r for r in await conn.fetch(
            "SELECT * FROM pos_metric_observations WHERE run_id = $1 "
            "   AND book = 'PAPER' AND sleeve = 'TRAINING' "
            "   AND strategy = 'ALL'", second["run_id"])}
        assert tr["REALIZED_NET_EDGE"]["sample_n"] == 2
        assert tr["REALIZED_NET_EDGE"]["confidence_scope"] == \
            "RESEARCH_NOT_PRODUCTION_CONFIDENCE"
        # the production capacity rates count INVESTMENT orders only; the
        # research rates see the training entries
        cap2 = await _snapshot(conn, "CAPACITY", "NONE")
        assert cap2["rates"]["fill_probability"]["n"] == \
            cap1["rates"]["fill_probability"]["n"] == 3
        assert cap2["research"]["rates"]["fill_probability"]["n"] == 5
        assert cap2["scope"]["confidence_scope"] == "PRODUCTION_CONFIDENCE"
        assert cap2["research"]["scope"]["confidence_scope"] == \
            "RESEARCH_NOT_PRODUCTION_CONFIDENCE"
        # the account's accounting moved; the INVESTMENT profit did not
        capital2 = await _snapshot(conn, "CAPITAL", "PAPER")

        def _profit(d):                    # the window moves with `now`
            return {k: v for k, v in d.items() if k != "window_start"}
        assert _profit(capital2["production_confidence"]) == \
            _profit(capital1["production_confidence"])
        assert capital2["by_sleeve"]["TRAINING"]["realized_sample"] == 2
        assert capital2["confidence_scope"] == \
            "ACCOUNTING_ALL_SLEEVES_NOT_PRODUCTION_CONFIDENCE"
        # the routes serve the INVESTMENT sleeve as the production figure

        class _Pool:
            @asynccontextmanager
            async def acquire(self):
                yield conn

        async def pool():
            return _Pool()
        monkeypatch.setattr(CPR, "_pool", pool)
        ns = await CPR.profitability_north_star()
        assert ns["data"]["PAPER"]["REALIZED_NET_EDGE"]["value"] == \
            pytest.approx(before["REALIZED_NET_EDGE"]["value"])
        assert ns["data"]["PAPER"]["REALIZED_NET_EDGE"]["sleeve"] == \
            "INVESTMENT"
        assert ns["by_sleeve"]["PAPER"]["TRAINING"]["REALIZED_NET_EDGE"][
            "sample_n"] == 2
        assert ns["production_confidence_scope"]["sleeve"] == "INVESTMENT"
        fc = await CPR.profitability_forecast(history=0)
        assert fc["data"]["PAPER"]["sleeve"] == "INVESTMENT"
        assert fc["data"]["PAPER"]["confidence_scope"] == \
            "PRODUCTION_CONFIDENCE"
        assert fc["by_sleeve"]["PAPER"]["TRAINING"]["confidence_scope"] == \
            "RESEARCH_NOT_PRODUCTION_CONFIDENCE"


@pg
async def test_the_opportunity_score_reads_the_investment_calibration_only():
    from sportsassets.lost_opportunity import reads as LR
    now = time.time()
    async with _txn() as conn:
        ins = ("INSERT INTO pos_metric_observations (observation_id, run_id,"
               " book, metric, computed_at, value, sample_n, status, why, "
               " content_sha256, version, sleeve, strategy, policy_versions,"
               " confidence_scope) VALUES ($1,'r','PAPER','EDGE_CALIBRATION',"
               " to_timestamp($2),$3,40,'MEASURED',NULL,'s','v',$4,'ALL',"
               " '{}',$5)")
        await conn.execute(ins, "m-inv-" + uuid.uuid4().hex[:6], now - 100,
                           0.8, "INVESTMENT", "PRODUCTION_CONFIDENCE")
        await conn.execute(ins, "m-tr-" + uuid.uuid4().hex[:6], now - 50,
                           -3.0, "TRAINING",
                           "RESEARCH_NOT_PRODUCTION_CONFIDENCE")
        ctx = await LR.score_context(conn, cands=[], since=now - 200)
        vals = [v["value"] for _, v in ctx["calibration"]]
        assert vals and all(v == pytest.approx(0.8) for v in vals[-1:])
        assert -3.0 not in vals


@pg
async def test_the_twin_and_the_scorecard_count_investment_only():
    now = time.time()
    async with _txn() as conn:
        acct = await H.new_account(conn, "r30aio2", now=now - 40 * DAY)
        start = QS.FORWARD_START
        gi = await _settled(conn, acct, strategy=CG, at=now - 2 * DAY,
                            qty=10, price=0.40, outcome="WON")
        gt = await _settled(conn, acct, strategy=EXPLORE, at=now - 2 * DAY,
                            qty=10, price=0.40, outcome="LOST")
        for g, pnl in ((gi, 6.0), (gt, -4.0)):
            await conn.execute(
                "INSERT INTO position_postmortems (book, position_key, venue,"
                " group_id, us_market_slug, fixture, strategy, opened_at, "
                " closed_at, realized_pnl_usd, unexplained_usd, "
                " decomposition_complete, components, detail, content_sha, "
                " version, computed_at) VALUES ('PAPER',$1,'POLYMARKET_US',"
                " $2,$3,$4,NULL,to_timestamp($5),to_timestamp($6),$7,$7,"
                " false,'{}'::jsonb,'{}'::jsonb,'s','v',now())",
                "pp:" + g, g, "s-" + g, "fx-" + g, max(start, now - 2 * DAY),
                now - DAY, pnl)
        closed = await TRUN._closed_rows(conn, [], [])
        mine = {r["group_id"]: r["sleeve"] for r in closed["PAPER"]
                if r["group_id"] in (gi, gt)}
        assert mine == {gi: "INVESTMENT", gt: "TRAINING"}
        li = TRUN.ladder_inputs([], closed, [], {})
        assert gt not in {r["group_id"] for r in li["paper_closed"]}
        assert gi in {r["group_id"] for r in li["paper_closed"]}
        got = {m["id"]: m for m in await QS.profitability_metrics(conn, now)}
        fp = got["forward_sample_paper"]
        assert fp["detail"]["sleeve"] == "INVESTMENT"
        assert fp["detail"]["confidence_scope"] == "PRODUCTION_CONFIDENCE"
        tr = fp["detail"]["other_sleeves"]["TRAINING"]
        assert tr["confidence_scope"] == "RESEARCH_NOT_PRODUCTION_CONFIDENCE"
        rows = await QS._postmortem_rows(conn, "PAPER")
        by = {}
        for r in rows:
            by.setdefault(r["sleeve"], []).append(r)
        # the investment verdict equals the verdict over INVESTMENT rows only
        assert {k: v for k, v in fp["detail"].items() if k in (
            "positions", "mean_pnl_per_position_usd")} == {
            k: v for k, v in QS.forward_verdict(
                by.get("INVESTMENT", []), now=now).items()
            if k in ("positions", "mean_pnl_per_position_usd")}


# ── §5 the schema (migration 227) ────────────────────────────────────

@pg
async def test_migration_227_refuses_unscoped_rows_and_mislabelled_training():
    async with _txn() as conn:
        await conn.execute(UP)                         # idempotent
        await conn.execute(UP)
        base = ("INSERT INTO pos_metric_observations (observation_id, run_id,"
                " book, metric, computed_at, value, sample_n, status, "
                " content_sha256, version, sleeve, strategy, policy_versions,"
                " confidence_scope) VALUES ($1,'r','PAPER','MAX_DRAWDOWN',"
                " now(),1.0,1,'INSUFFICIENT_SAMPLE','s','v',$2,$3,$4,$5)")
        await conn.execute(base, "ok-1", "INVESTMENT", "ALL", [],
                           "PRODUCTION_CONFIDENCE")
        await conn.execute(base, "ok-2", "TRAINING", "ALL", [],
                           "RESEARCH_NOT_PRODUCTION_CONFIDENCE")
        for i, args in enumerate((
                (None, "ALL", [], "RESEARCH_NOT_PRODUCTION_CONFIDENCE"),
                ("INVESTMENT", None, [], "PRODUCTION_CONFIDENCE"),
                ("INVESTMENT", "ALL", None, "PRODUCTION_CONFIDENCE"),
                ("INVESTMENT", "ALL", [], None),
                ("TRAINING", "ALL", [], "PRODUCTION_CONFIDENCE"),
                ("UNCLASSIFIED", "ALL", [], "PRODUCTION_CONFIDENCE"),
                ("investment", "ALL", [], "RESEARCH_NOT_PRODUCTION_CONFIDENCE"),
                ("INVESTMENT", "ALL", [], "LIVE"))):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(base, "bad-%d" % i, *args)
            await sp.rollback()
        fc = ("INSERT INTO pos_forecasts (forecast_id, book, run_id, "
              " issued_at, issued_day, horizon_start, horizon_end, "
              " horizon_days, method, status, why, inputs_sha256, version, "
              " sleeve, strategy, policy_versions, confidence_scope) VALUES "
              " ($1,'PAPER','r',now(),current_date,now(),now() + interval "
              " '30 days',30,'m','UNAVAILABLE','X','s','v',$2,'ALL','{}',$3)")
        await conn.execute(fc, "fc-inv", "INVESTMENT", "PRODUCTION_CONFIDENCE")
        await conn.execute(fc, "fc-tr", "TRAINING",
                           "RESEARCH_NOT_PRODUCTION_CONFIDENCE")
        # one per scope per day: a second INVESTMENT forecast the same day
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(fc, "fc-inv-2", "INVESTMENT",
                               "PRODUCTION_CONFIDENCE")
        await sp.rollback()
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO pos_forecasts (forecast_id, book, run_id, "
                " issued_at, issued_day, horizon_start, horizon_end, "
                " horizon_days, method, status, why, inputs_sha256, version)"
                " VALUES ('fc-legacy','PAPER','r',now(),current_date,now(),"
                " now() + interval '30 days',30,'m','UNAVAILABLE','X','s',"
                " 'v')")
        await sp.rollback()
        # the rollback refuses while a scoped row exists
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.RaiseError, match="rollback refused"):
            await conn.execute(DOWN)
        await sp.rollback()


@pg
async def test_migration_227_rollback_applies_cleanly_and_reapplies():
    async with _txn() as conn:
        # no scoped row in this transaction's view of the test database
        # (other proofs roll theirs back) -> the rollback applies
        n = await conn.fetchval(
            "SELECT (SELECT count(*) FROM pos_metric_observations WHERE "
            "        sleeve IS NOT NULL) + (SELECT count(*) FROM "
            "        pos_forecasts WHERE sleeve IS NOT NULL) + (SELECT "
            "        count(*) FROM lol_horizon_forecasts WHERE sleeve IS NOT"
            "        NULL)")
        if n:
            pytest.skip("this database holds committed scoped rows")
        await conn.execute(DOWN)
        cols = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            " WHERE table_name = 'pos_metric_observations'")}
        assert "sleeve" not in cols and "confidence_scope" not in cols
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_constraint "
            " WHERE conname = 'pos_fc_one_per_day'") == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_constraint "
            " WHERE conname = 'lol_hfc_one_per_day'") == 1
        await conn.execute(DOWN)                       # idempotent
        await conn.execute(UP)
        cols = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            " WHERE table_name = 'pos_forecasts'")}
        assert {"sleeve", "strategy", "policy_versions",
                "classifier_version", "confidence_scope"} <= cols
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_constraint "
            " WHERE conname = 'pos_fc_one_per_day'") == 0


def test_the_migration_is_research_only_and_writes_no_row():
    up = UP.upper()
    for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "TRUNCATE"):
        assert verb not in up, verb
    assert "NOT VALID" in up
    assert "PRODUCTION_IS_INVESTMENT" in up
