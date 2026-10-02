"""INSTITUTIONAL REPORTING ON THE PAPER ACCOUNT (sportsassets.paper_institutional).

The PURE computations on synthetic data -- NAV, drawdown, returns, the
Sharpe-style formula, profit factor and the closed-position statistics, the
attribution split that keeps TRAINING apart from INVESTMENT, the daily
statement, the reconciliation checks and the CSV writer -- and, throughout,
that an UNAVAILABLE figure never becomes 0.

The DB-backed proofs at the end are optional: they run only when
RN1X_TEST_DSN names a test database (a scratch paper account is seeded there
through the real ledger functions) and are skipped otherwise.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import math
import os

import pytest

from sportsassets import paper_institutional as PI

UTC = dt.timezone.utc
EXPLORE = "PINNACLE_EXPLORATION_PAPER"
CG = "PINNACLE_COMPLETED_GAME_PAPER"
META = {EXPLORE: {"kind": "TRAINING", "title": "Exploration",
                  "disclosure": "negative expected value is a research cost"},
        CG: {"kind": "EXPERIMENTAL_BENCHMARK", "title": "Completed game"}}


# ── sections and numbers ─────────────────────────────────────────────
def test_sections_and_sums_never_turn_unavailable_into_zero():
    assert PI.unavailable("WHY") == {"status": "UNAVAILABLE",
                                     "reason": "WHY", "data": None}
    assert PI.ok([])["status"] == "OK"
    assert PI.sum_or_none([1.0, 2.0]) == 3.0
    assert PI.sum_or_none([1.0, None, 2.0]) is None
    assert PI.num(None) is None and PI.num("x") is None
    assert PI.num(float("nan")) is None
    assert PI.ratio(1, 0) is None and PI.pct(None, 5) is None


# ── NAV ──────────────────────────────────────────────────────────────
def test_nav_is_cash_plus_marked_positions_and_unavailable_with_any_unmarked():
    got = PI.compute_nav(400_000.0, [{"marked_value_usd": 60_000.0},
                                     {"marked_value_usd": 45_500.5}])
    assert got["nav_usd"] == 505_500.5 and got["unmarked"] == 0
    got = PI.compute_nav(400_000.0, [{"marked_value_usd": 60_000.0},
                                     {"marked_value_usd": None}])
    assert got["nav_usd"] is None                       # never cash + 0
    assert got["marked_only_usd"] == 460_000.0
    assert got["unmarked"] == 1 and "NO_AVAILABLE_MARK" in got["reason"]
    assert PI.compute_nav(None, [])["nav_usd"] is None
    assert PI.compute_nav(500_000, [])["nav_usd"] == 500_000.0


# ── drawdown ─────────────────────────────────────────────────────────
def test_drawdown_peak_to_trough_skips_unavailable_points():
    pts = [500_000, 510_000, 495_000, 505_000, None, 520_000, 515_000]
    d = PI.drawdown(pts, initial_peak=500_000)
    assert d["high_water_mark_usd"] == 520_000
    assert d["max_drawdown_usd"] == 15_000
    assert math.isclose(d["max_drawdown_pct"], 15_000 / 510_000 * 100,
                        abs_tol=1e-6)
    assert d["current_drawdown_usd"] == 5_000
    assert d["points_skipped_unavailable"] == 1 and d["points_used"] == 6
    # starting capital is the first peak: an immediate loss is a drawdown
    d = PI.drawdown([490_000], initial_peak=500_000)
    assert d["max_drawdown_usd"] == 10_000
    # nothing available: no current NAV, never 0
    d = PI.drawdown([None, None], initial_peak=500_000)
    assert d["current_nav_usd"] is None and d["current_drawdown_usd"] is None


# ── returns, volatility, Sharpe ──────────────────────────────────────
def test_period_return_accounts_for_funding_and_unavailable_nav():
    assert PI.period_return(0.0, 500_000, 505_000) == pytest.approx(0.01)
    assert PI.period_return(505_000, 0, 500_000) == pytest.approx(
        -5_000 / 505_000)
    assert PI.period_return(None, 0, 500_000) is None
    assert PI.period_return(500_000, 0, None) is None
    assert PI.period_return(0.0, 0.0, 10.0) is None     # no base


def test_sharpe_formula_mean_over_sample_stdev_times_sqrt_365():
    rs = [0.01, -0.02, 0.03, None]
    s = PI.return_stats(rs)
    have = [0.01, -0.02, 0.03]
    mean = sum(have) / 3
    sd = math.sqrt(sum((r - mean) ** 2 for r in have) / 2)
    assert s["n_returns"] == 3 and s["n_unavailable"] == 1
    assert s["mean_daily_return"] == pytest.approx(mean)
    assert s["stdev_daily_return"] == pytest.approx(sd)
    assert s["volatility_annualised"] == pytest.approx(sd * math.sqrt(365))
    assert s["sharpe_ratio"] == pytest.approx(mean / sd * math.sqrt(365),
                                              rel=1e-6)
    assert "sqrt(365)" in s["sharpe_formula"]
    assert "PAPER" in s["sharpe_formula"]
    # too few returns / zero volatility: the ratio is not stated (not 0)
    one = PI.return_stats([0.01])
    assert one["sharpe_ratio"] is None and one["reason"]
    flat = PI.return_stats([0.0, 0.0, 0.0])
    assert flat["sharpe_ratio"] is None and "ZERO_VOLATILITY" in flat["reason"]
    none = PI.return_stats([None])
    assert none["mean_daily_return"] is None and none["reason"]
    assert PI.cumulative_return([0.1, None, -0.1]) == pytest.approx(-0.01)
    assert PI.cumulative_return([None]) is None


# ── closed-position statistics ───────────────────────────────────────
def _pos(pk, realized, open_qty=0.0, strategy=CG, **kw):
    return dict({"position_key": pk, "us_market_slug": "m-" + pk,
                 "strategy": strategy, "open_qty": open_qty,
                 "realized_pnl_usd": realized}, **kw)


def test_trade_stats_profit_factor_win_rate_expectancy_closed_only():
    ps = [_pos("a", 100.0, settlement={"outcome": "WON"}),
          _pos("b", -50.0, settlement={"outcome": "LOST"}),
          _pos("c", 0.0), _pos("d", 30.0),
          _pos("open", 999.0, open_qty=10.0)]               # ignored: open
    t = PI.trade_stats(ps)
    assert t["closed_positions"] == 4
    assert (t["wins"], t["losses"], t["flat"]) == (2, 1, 1)
    assert t["win_rate"] == 0.5
    assert t["average_win_usd"] == 65.0 and t["average_loss_usd"] == -50.0
    assert t["profit_factor"] == pytest.approx(130.0 / 50.0)
    assert t["expectancy_usd"] == 20.0
    assert t["best_position"]["position_key"] == "a"
    assert t["worst_position"]["position_key"] == "b"
    assert t["settled"] == 2
    only_wins = PI.trade_stats([_pos("a", 10.0)])
    assert only_wins["profit_factor"] is None
    assert only_wins["profit_factor_reason"] == "UNDEFINED_NO_LOSING_POSITION"
    empty = PI.trade_stats([_pos("open", 5.0, open_qty=1.0)])
    assert empty["win_rate"] is None and empty["expectancy_usd"] is None


# ── attribution: training kept apart ─────────────────────────────────
def test_attribution_keeps_training_separate_with_its_disclosure():
    rows = [  # bettor_paper_ops.pnl_by_strategy shape
        {"strategy": CG, "realized_pnl_usd": 1_000.0,
         "unrealized_pnl_usd": 200.0, "unrealized_marked_only_usd": 200.0,
         "unmarked_positions": 0, "open_positions": 2, "closed_positions": 5,
         "open_cost_basis_usd": 900.0, "fees_usd": 40.0},
        {"strategy": EXPLORE, "realized_pnl_usd": -300.0,
         "unrealized_pnl_usd": None, "unrealized_marked_only_usd": -20.0,
         "unmarked_positions": 1, "open_positions": 3, "closed_positions": 7,
         "open_cost_basis_usd": 250.0, "fees_usd": 12.0}]
    strat = PI.attribution_by_strategy(rows, META)
    by = {r["strategy"]: r for r in strat}
    assert by[CG]["book"] == PI.INVESTMENT
    assert by[EXPLORE]["book"] == PI.TRAINING
    assert by[EXPLORE]["negative_ev_disclosure"] == PI.TRAINING_DISCLOSURE
    assert "NEGATIVE" in PI.TRAINING_DISCLOSURE
    assert by[CG]["negative_ev_disclosure"] is None
    assert by[CG]["net_pnl_usd"] == 1_200.0
    assert by[EXPLORE]["net_pnl_usd"] is None          # unmarked: not -300
    books = PI.books_from_strategies(strat)
    assert set(books) == {PI.INVESTMENT, PI.TRAINING}
    inv, tr = books[PI.INVESTMENT], books[PI.TRAINING]
    # the investment book holds ONLY investment strategies
    assert inv["strategies"] == [CG] and tr["strategies"] == [EXPLORE]
    assert inv["realized_pnl_usd"] == 1_000.0         # training not netted
    assert tr["realized_pnl_usd"] == -300.0
    assert inv["unrealized_pnl_usd"] == 200.0
    assert tr["unrealized_pnl_usd"] is None
    assert tr["unrealized_marked_only_usd"] == -20.0
    assert tr["disclosure"] == PI.TRAINING_DISCLOSURE
    assert inv["disclosure"] is None


def test_book_of_classifies_exploration_as_training():
    assert PI.book_of(EXPLORE, META) == PI.TRAINING
    assert PI.book_of(EXPLORE, {}) == PI.TRAINING      # by name, fallback
    assert PI.book_of(CG, META) == PI.INVESTMENT
    assert PI.book_of("DEREK_ENTRY_POLICY_V2", {}) == PI.INVESTMENT


def test_group_pnl_by_key_and_book_unmarked_is_none():
    ps = [_pos("a", 10.0, league="mlb", book=PI.INVESTMENT),
          _pos("b", 5.0, open_qty=3.0, league="mlb", book=PI.INVESTMENT,
               cost_basis_usd=2.0, unrealized_pnl_usd=None),
          _pos("c", -4.0, league="mlb", book=PI.TRAINING),
          _pos("d", 1.0, open_qty=1.0, league=None, book=PI.INVESTMENT,
               cost_basis_usd=0.5, unrealized_pnl_usd=0.25)]
    g = PI.group_pnl(ps, lambda p: p.get("league"), label="league")
    by = {(r["league"], r["book"]): r for r in g}
    assert by[("mlb", PI.INVESTMENT)]["realized_pnl_usd"] == 15.0
    assert by[("mlb", PI.INVESTMENT)]["unrealized_pnl_usd"] is None
    assert by[("mlb", PI.INVESTMENT)]["net_pnl_usd"] is None
    assert by[("mlb", PI.TRAINING)]["realized_pnl_usd"] == -4.0
    assert by[("UNIDENTIFIED", PI.INVESTMENT)]["unrealized_pnl_usd"] == 0.25


def test_decision_type_attribution_splits_settlement_and_management():
    events = [(1.0, "a", CG, 40.0, "SETTLEMENT", "SETTLEMENT"),
              (2.0, "b", CG, -5.0, "SALE", "STANDING_PROTECTION"),
              (3.0, "c", EXPLORE, -8.0, "SETTLEMENT", "SETTLEMENT")]
    rows = PI.by_decision_type(events, [{"strategy": CG,
                                         "unrealized_pnl_usd": None}], META)
    by = {(r["agent"], r["decision_type"], r["book"]): r for r in rows}
    assert by[("Derek", "ENTRY_HELD_TO_SETTLEMENT", PI.INVESTMENT)][
        "realized_pnl_usd"] == 40.0
    assert by[("Xavier", "STANDING_PROTECTION_SALE", PI.INVESTMENT)][
        "realized_pnl_usd"] == -5.0
    assert by[("Derek", "ENTRY_HELD_TO_SETTLEMENT", PI.TRAINING)][
        "realized_pnl_usd"] == -8.0
    assert by[("Derek", "ENTRY_OPEN_MARKED", PI.INVESTMENT)][
        "unrealized_pnl_usd"] is None


# ── realized events, daily series, periods ───────────────────────────
def test_realized_events_sum_to_the_positions_realized_pnl():
    pos = [{"position_key": "pk1", "strategy": CG, "open_qty": 0.0,
            "avg_cost_per_contract_incl_fees": 0.5, "settled_qty": 60.0,
            "realized_pnl_usd": (39.0 - 0.5 * 40) + (60.0 - 0.5 * 60),
            "settlement": {"outcome": "WON", "payout_usd": 60.0,
                           "settled_at": 2_000.0}}]
    sales = [{"position_key": "pk1", "qty": 40.0, "gross_usd": 40.0,
              "fee_usd": 1.0, "filled_at": 1_000.0, "role": "EXIT"}]
    ev = PI.realized_events(pos, sales)
    assert sum(e[3] for e in ev) == pytest.approx(pos[0]["realized_pnl_usd"])
    assert {e[4] for e in ev} == {"SALE", "SETTLEMENT"}


def test_open_count_at_day_ends_sweeps_events():
    tz = UTC
    d1, d2, d3 = dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)
    t = lambda d, h: dt.datetime.combine(d, dt.time(h), tz).timestamp()  # noqa: E731
    ev = [(t(d1, 10), "a", 5.0), (t(d2, 9), "a", -5.0),
          (t(d2, 12), "b", 2.0)]
    got = PI.open_count_at_day_ends(ev, [d1, d2, d3], tz)
    assert got == {d1: 1, d2: 1, d3: 1}


def test_daily_statement_bases_and_unavailable_days():
    tz = UTC
    days = [dt.date(2026, 9, 1) + dt.timedelta(days=i) for i in range(5)]
    rows = PI.build_daily(
        days=days, tz=tz,
        cash_eod={days[0]: 500_000.0, days[1]: 499_000.0,
                  days[3]: 501_000.0},
        open_eod={days[0]: 0, days[1]: 1, days[2]: 1, days[3]: 0,
                  days[4]: 1},
        snaps={days[1]: {"equity_usd": 499_900.0,
                         "unrealized_pnl_usd": -100.0}},
        realized_by_day={days[3]: 1_200.0}, fees_by_day={days[1]: 3.0},
        flows_by_day={days[0]: 500_000.0},
        live={"nav_usd": 501_500.0, "unrealized_usd": 500.0},
        starting_capital=500_000.0)
    r = {x["date"]: x for x in rows}
    k = [d.isoformat() for d in days]
    assert r[k[0]]["nav_basis"] == "CASH_NO_OPEN_POSITIONS"
    assert r[k[0]]["net_pnl_usd"] == 0.0                # funding is not P&L
    assert r[k[0]]["daily_return"] == 0.0
    assert r[k[1]]["nav_basis"] == "MARKED_SNAPSHOT"
    assert r[k[1]]["net_pnl_usd"] == -100.0
    assert r[k[1]]["fees_usd"] == 3.0
    # day 3: positions open, no complete snapshot -> UNAVAILABLE, never 0
    assert r[k[2]]["nav_usd"] is None and r[k[2]]["nav_basis"] == "UNAVAILABLE"
    assert r[k[2]]["net_pnl_usd"] is None
    assert r[k[2]]["daily_return"] is None
    assert r[k[2]]["nav_unavailable_reason"]
    # day 4: exact cash, but the prior NAV is unknown: no net, no return
    assert r[k[3]]["nav_usd"] == 501_000.0
    assert r[k[3]]["net_pnl_usd"] is None and r[k[3]]["daily_return"] is None
    assert r[k[3]]["realized_pnl_usd"] == 1_200.0
    assert r[k[3]]["unrealized_change_usd"] is None
    # today: live
    assert r[k[4]]["nav_basis"] == "LIVE" and r[k[4]]["net_pnl_usd"] == 500.0
    assert r[k[4]]["unrealized_change_usd"] == 500.0
    assert r[k[4]]["high_water_mark_usd"] == 501_500.0
    # a period whose start NAV is unavailable has no net P&L
    p = PI.period_pnl(rows, days[3])
    assert p["net_pnl_usd"] is None and p["reason"]
    assert p["realized_pnl_usd"] == 1_200.0
    p = PI.period_pnl(rows, days[4])
    assert p["net_pnl_usd"] == 500.0
    allp = PI.period_pnl(rows, None)
    assert allp["net_pnl_usd"] == 1_500.0 and allp["funding_usd"] == 500_000.0
    # live NAV unavailable today
    rows2 = PI.build_daily(days=days[:1], tz=tz, cash_eod={}, open_eod={},
                           snaps={}, realized_by_day={}, fees_by_day={},
                           flows_by_day={days[0]: 500_000.0},
                           live={"nav_usd": None, "unrealized_usd": None,
                                 "reason": "LIVE_MARKS_INCOMPLETE"})
    assert rows2[0]["nav_usd"] is None and rows2[0]["net_pnl_usd"] is None
    assert rows2[0]["nav_unavailable_reason"] == "LIVE_MARKS_INCOMPLETE"


def test_period_start_boundaries():
    d = dt.date(2026, 10, 2)              # a Friday
    assert PI.period_start(d, "1D") == d
    assert PI.period_start(d, "WTD") == dt.date(2026, 9, 28)
    assert PI.period_start(d, "MTD") == dt.date(2026, 10, 1)
    assert PI.period_start(d, "YTD") == dt.date(2026, 1, 1)
    assert PI.period_start(d, "ALL") is None


# ── risk ─────────────────────────────────────────────────────────────
def test_exposure_concentration_and_gross_net():
    opn = [{"position_key": "a", "us_market_slug": "m1", "holding_side": "LONG",
            "open_qty": 100.0, "cost_basis_usd": 50.0,
            "marked_value_usd": 60.0, "league": "mlb", "book": PI.INVESTMENT},
           {"position_key": "b", "us_market_slug": "m1",
            "holding_side": "SHORT", "open_qty": 40.0, "cost_basis_usd": 20.0,
            "marked_value_usd": 18.0, "league": "mlb", "book": PI.TRAINING},
           {"position_key": "c", "us_market_slug": "m2", "holding_side": "LONG",
            "open_qty": 10.0, "cost_basis_usd": 5.0, "marked_value_usd": None,
            "league": "nfl", "book": PI.INVESTMENT}]
    gx = PI.gross_net_exposure(opn)
    assert gx["gross_exposure_usd"] is None             # one unmarked
    gx = PI.gross_net_exposure(opn[:2])
    assert gx["gross_exposure_usd"] == 78.0
    assert gx["locked_both_sides_usd"] == 40.0
    assert gx["net_exposure_usd"] == 38.0
    ex = PI.exposures(opn, 1_000.0, key_fn=lambda p: p["league"],
                      label="league")
    by = {r["league"]: r for r in ex}
    assert by["mlb"]["marked_value_usd"] == 78.0
    assert by["mlb"]["books"] == [PI.INVESTMENT, PI.TRAINING]
    assert by["nfl"]["marked_value_usd"] is None        # unmarked: not 0
    assert by["nfl"]["pct_basis"] == "COST_BASIS_UNMARKED"
    assert by["nfl"]["pct_of_nav"] == 0.5
    c = PI.concentration(opn, 1_000.0)
    assert c["largest"][0]["position_key"] == "a"
    assert c["largest_pct_of_nav"] == 6.0
    assert c["largest"][2]["value_basis"] == "COST_BASIS_UNMARKED"
    assert PI.concentration(opn, None)["largest_pct_of_nav"] is None


# ── reconciliation checks ────────────────────────────────────────────
def test_reconciliation_checks_list_each_discrepancy():
    fills = [{"fill_id": "f1", "direction": "BUY", "gross_usd": 10.0,
              "fee_usd": 0.2, "ledger_entries": 1, "ledger_kind": "FILL",
              "ledger_cash_delta_usd": -10.2, "ledger_seq": 5},
             {"fill_id": "f2", "direction": "SELL", "gross_usd": 8.0,
              "fee_usd": 0.1, "ledger_entries": 1, "ledger_kind": "SALE",
              "ledger_cash_delta_usd": 8.0, "ledger_seq": 6},
             {"fill_id": "f3", "direction": "BUY", "gross_usd": 1.0,
              "fee_usd": 0.0, "ledger_entries": 0}]
    d = PI.fill_ledger_discrepancies(fills)
    assert [x["fill_id"] for x in d] == ["f2", "f3"]
    assert d[0]["check"] == "LEDGER_CASH_EQUALS_FILL_CASH"
    assert d[1]["check"] == "ONE_LEDGER_ENTRY_PER_FILL"
    pos = [{"position_key": "p", "settlement": {"payout_usd": 60.0}},
           {"position_key": "q", "settlement": {"payout_usd": 0.0}}]
    s = PI.settlement_discrepancies(pos, {"p": 60.0})
    assert [x["position_key"] for x in s] == ["q"]      # missing ledger credit
    assert PI.reservation_discrepancy(10.0, 10.0) == []
    assert PI.reservation_discrepancy(10.0, None)        # unreadable: listed
    assert PI.order_fill_discrepancies([{"order_id": "o", "filled_qty": 5,
                                         "fills_qty": 4}])
    assert PI.position_discrepancies([{"position_key": "x", "bought_qty": 1,
                                       "sold_qty": 2, "settled_qty": 0,
                                       "open_qty": -1}])


# ── CSV ──────────────────────────────────────────────────────────────
def test_csv_writes_unavailable_never_zero_and_carries_the_basis():
    txt = PI.to_csv([{"a": 1.5, "b": None}, {"a": 0.0, "b": "x"}],
                    [("A", "a"), ("B", "b"), ("C", lambda r: r.get("a"))],
                    title="T", as_of=1_790_300_000.0)
    lines = txt.splitlines()
    assert lines[0] == "# " + PI.BASIS
    assert lines[1].startswith("# T · as of 2026-")
    assert lines[2] == "A,B,C"
    assert lines[3] == "1.5,UNAVAILABLE,1.5"
    assert lines[4] == "0.0,x,0.0"                     # a real zero stays 0


def test_basis_text_is_the_management_statement():
    assert PI.BASIS == ("Paper account — simulated execution on live market "
                        "data; reported to institutional standards")
    base = PI.base(1.0, "paper_acct_main")
    for k in ("as_of", "account_id", "basis"):
        assert k in base


# ═════════════════════════════════════════════════════════════════════
# OPTIONAL: AGAINST A TEST DATABASE (RN1X_TEST_DSN), SKIPPED OTHERWISE
# ═════════════════════════════════════════════════════════════════════

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN not set")


def _sections(body: dict) -> dict:
    return {k: v for k, v in body.items()
            if isinstance(v, dict) and "status" in v}


@pg
def test_every_report_on_a_seeded_paper_account_reads_and_reconciles():
    from tests import paper_harness as H
    from tests import paper_ops_seed as SEED
    from sportsassets import bettor_paper_ledger as L

    async def run():
        conn = await H.connect()
        try:
            acct = await H.new_account(conn, "instrep", now=H.T0)
            await SEED.seed(conn, acct, t0=H.T0)
            a, now = acct["account_id"], H.T0 + 200.0
            bal = await L.balances(conn, a, now=now)
            out = {
                "summary": await PI.summary_report(conn, account_id=a,
                                                   now=now),
                "pnl": await PI.pnl_report(conn, account_id=a, now=now),
                "performance": await PI.performance_report(
                    conn, account_id=a, now=now),
                "positions": await PI.positions_report(conn, account_id=a,
                                                       now=now),
                "blotter": await PI.blotter_report(conn, account_id=a,
                                                   now=now, limit=2),
                "attribution": await PI.attribution_report(
                    conn, account_id=a, now=now),
                "risk": await PI.risk_report(conn, account_id=a, now=now),
                "reconciliation": await PI.reconciliation_report(
                    conn, account_id=a, now=now)}
            csvs = {n: await PI.export_csv(conn, n, account_id=a, now=now)
                    for n in PI.EXPORTS}
            fills = await conn.fetchval(
                "SELECT count(*) FROM paper_fills WHERE account_id=$1", a)
            return bal, out, csvs, int(fills)
        finally:
            await conn.close()
    bal, out, csvs, n_fills = asyncio.run(run())
    for name, body in out.items():
        assert body["basis"] == PI.BASIS and body["account_id"]
        assert body["as_of"]
        for k, sec in _sections(body).items():
            assert sec["status"] == "OK", (name, k, sec.get("reason"))
    s = out["summary"]["summary"]["data"]
    # the reports use the ledger's own accounting
    assert s["nav"]["nav_usd"] == bal["total_equity_usd"]
    assert s["pnl"]["realized_pnl_usd"] == bal["realized_pnl_usd"]
    assert s["capital"]["net_deposits_usd"] == 500_000.0
    assert s["exposure"]["open_positions"] == len(bal["open_positions"])
    pos = out["positions"]["positions"]["data"]
    assert pos["count"] == len(bal["open_positions"])
    for p in pos["positions"]:
        assert p["book"] in PI.BOOKS and "protection_orders" in p
    tr = out["blotter"]["trades"]
    assert tr["total"] == n_fills and len(tr["data"]) <= 2
    att = out["attribution"]["attribution"]["data"]
    assert set(att["by_book"]) == {PI.INVESTMENT, PI.TRAINING}
    assert sum(r["realized_pnl_usd"] for r in att["by_strategy"]) == \
        pytest.approx(bal["realized_pnl_usd"], abs=1e-6)
    rec = out["reconciliation"]["reconciliation"]["data"]
    assert rec["status"] == "RECONCILED", rec["discrepancies"]
    for name, (fname, text) in csvs.items():
        assert fname.endswith(".csv") and text.startswith("# " + PI.BASIS)


@pg
def test_report_routes_need_the_command_credential(monkeypatch):
    import asyncpg
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from sportsassets.api import app as A
    from sportsassets.api import paper_reports_routes as R

    class _Cfg:
        admin_token = "admin-secret-for-the-paper-report-routes"
        desk_password = "desk"
        operator_password = "operator"
        command_read_password = "desk"
        funded_resolution_key = ""
        funded_resolution_operator = ""
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    pools = {}

    async def _pool():
        loop = asyncio.get_running_loop()
        if loop not in pools:
            pools[loop] = await asyncpg.create_pool(DSN, min_size=1,
                                                    max_size=2)
        return pools[loop]
    monkeypatch.setattr(R, "_pool", _pool)
    app = FastAPI()
    app.include_router(R.router)
    c = TestClient(app, raise_server_exceptions=False)
    paths = ["/api/command/paper/reports/" + p for p in (
        "summary", "pnl", "performance", "positions", "blotter",
        "attribution", "risk", "reconciliation", "export/daily_pnl.csv")]
    for p in paths:
        assert c.get(p).status_code == 401, p
    hdr = {"X-Admin-Token": _Cfg.admin_token}
    for p in paths[:-1]:
        r = c.get(p, headers=hdr)
        assert r.status_code == 200, (p, r.text)
        body = r.json()
        assert body["basis"] == PI.BASIS and "as_of" in body
        for k, sec in _sections(body).items():
            assert sec["status"] in ("OK", "UNAVAILABLE"), (p, k)
            if sec["status"] != "OK":
                assert sec["reason"], (p, k)
    r = c.get(paths[-1], headers=hdr)
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
    assert c.get("/api/command/paper/reports/export/nope.csv",
                 headers=hdr).status_code == 404
    assert c.get("/api/command/paper/reports/pnl?period=BAD",
                 headers=hdr).status_code == 422
