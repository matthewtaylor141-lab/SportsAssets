"""CAPITAL-HOUR ECONOMICS: the arithmetic, the fail-closed nulls, and the
counterfactual that is never summed with the real book.

Pure tests over hand-built position records (no database): every number
below is worked out by hand in the comment beside it.
"""
from __future__ import annotations

import pytest

from sportsassets.profitability import economics as EC

H = 3600.0
T = 1_790_000_000.0


def _pos(**kw):
    p = {"book": "PAPER", "position_key": "paperpos:a:g:m:LONG",
         "venue": "PAPER_SIMULATED", "group_id": "g", "us_market_slug": "m",
         "holding_side": "LONG", "strategy": "S",
         "events": [{"t": T + 2, "kind": "BUY", "qty": 100, "price": 0.53,
                     "fee_usd": 1.0, "ref": "f1"}],
         "reservation": {"t": T, "usd": 60.0, "ref": "o1"},
         "settlement": {"t": T + 5 * H, "payout_per_contract": 1.0,
                        "outcome": "WON", "ref": "s1", "basis": "X"},
         "probability": 0.62, "probability_basis": "P_PINNACLE",
         "event_start_at": T + 2 * H, "event_start_basis": "US_PREMAP"}
    p.update(kw)
    return p


#: six settlements recorded well before T, each 3h after its game start
LAGS = [(T - 10 * 86400 + i, 3 * H) for i in range(6)]


def test_a_settled_position_capital_hours_and_profit():
    e = EC.compute_position(_pos(), lag_samples=LAGS)
    assert e["state"] == "CLOSED"
    assert e["capital_committed_usd"] == pytest.approx(54.0)   # 53 + 1 fee
    # reservation 60 x 2s, then 54 from T+2 to T+5h
    ch = 60 * 2 / H + 54 * (5 * H - 2) / H
    assert e["capital_hours"] == pytest.approx(ch, abs=1e-6)
    assert e["net_profit_usd"] == pytest.approx(46.0)          # 100 - 54
    assert e["realized_profit_per_capital_hour"] == pytest.approx(46 / ch,
                                                                  rel=1e-6)
    assert e["expected_net_profit_usd"] == pytest.approx(8.0)  # 62 - 54
    # expected release = game start + 3h lag = T+5h; from the lock at T
    assert e["expected_release_at"] == pytest.approx(T + 5 * H)
    assert e["expected_capital_hours"] == pytest.approx(54 * 5.0)
    assert e["expected_profit_per_capital_hour"] == pytest.approx(8 / 270.0)
    assert e["time_committed_h"] == pytest.approx(5.0)
    assert e["unmeasured"] == {}
    assert e["detail"]["capital_path_basis"] == (
        "PRE_FILL_RESERVATION+OPEN_COST_BASIS")


def test_a_sale_releases_capital_at_average_cost():
    ev = [{"t": T, "kind": "BUY", "qty": 100, "price": 0.40, "fee_usd": 0},
          {"t": T + H, "kind": "SELL", "qty": 50, "price": 0.50,
           "fee_usd": 0},
          {"t": T + 2 * H, "kind": "SELL", "qty": 50, "price": 0.30,
           "fee_usd": 0}]
    e = EC.compute_position(_pos(events=ev, reservation=None,
                                 settlement=None), lag_samples=LAGS)
    assert e["state"] == "CLOSED"
    assert e["released_at"] == T + 2 * H
    # 40 for 1h, then 20 for 1h
    assert e["capital_hours"] == pytest.approx(60.0)
    assert e["net_profit_usd"] == pytest.approx(25 + 15 - 40)  # 0.0
    assert e["net_profit_usd"] == 0.0          # a MEASURED zero, not null
    assert e["detail"]["release"] == "FULL_EXIT_BY_SALE"


def test_an_open_position_is_time_invariant_between_events():
    p = _pos(settlement=None)
    a = EC.compute_position(p, lag_samples=LAGS)
    b = EC.compute_position(p, lag_samples=LAGS)
    assert a["state"] == "OPEN"
    assert a["capital_hours"] == pytest.approx(60 * 2 / H)  # to last event
    assert a["open_cost_basis_usd"] == pytest.approx(54.0)  # still accruing
    assert {k: v for k, v in a.items() if k != "segments"} == \
        {k: v for k, v in b.items() if k != "segments"}
    assert a["net_profit_usd"] is None
    assert a["unmeasured"]["net_profit_usd"] == EC.R_OPEN
    assert a["unmeasured"]["realized_profit_per_capital_hour"] == EC.R_OPEN


# ── fail-closed: missing or invalid inputs are null with a reason ────

def test_no_probability_is_null_with_its_reason_never_zero():
    e = EC.compute_position(_pos(probability=None), lag_samples=LAGS)
    assert e["expected_net_profit_usd"] is None
    assert e["unmeasured"]["expected_net_profit_usd"] == EC.R_NO_P
    assert e["expected_profit_per_capital_hour"] is None
    assert e["unmeasured"]["expected_profit_per_capital_hour"] == EC.R_NO_P
    assert e["net_profit_usd"] == pytest.approx(46.0)    # realized still is


def test_no_event_start_or_no_lag_leaves_expected_capital_hours_null():
    e = EC.compute_position(_pos(event_start_at=None), lag_samples=LAGS)
    assert e["expected_capital_hours"] is None
    assert e["unmeasured"]["expected_capital_hours"] == EC.R_NO_START
    e = EC.compute_position(_pos(), lag_samples=LAGS[:4])
    assert e["unmeasured"]["expected_capital_hours"] == EC.R_NO_LAG


def test_the_expected_release_uses_no_look_ahead():
    """Settlements recorded AFTER the first fill are not used for its
    expected release: the expectation is the one available at entry."""
    late = [(T + 86400 + i, 3 * H) for i in range(10)]
    e = EC.compute_position(_pos(), lag_samples=late)
    assert e["expected_capital_hours"] is None
    assert e["unmeasured"]["expected_capital_hours"] == EC.R_NO_LAG
    assert e["detail"]["lag_samples_at_entry"] == 0


def test_no_buy_fill_is_unavailable_everywhere():
    e = EC.compute_position(_pos(events=[{"t": T, "kind": "SELL", "qty": 5,
                                          "price": 0.5}]), lag_samples=LAGS)
    for k in ("net_profit_usd", "capital_hours", "capital_committed_usd",
              "expected_net_profit_usd"):
        assert e[k] is None and e["unmeasured"][k] == EC.R_NO_BUY


def test_invalid_events_are_dropped_not_guessed():
    ev = [{"t": None, "kind": "BUY", "qty": 100, "price": 0.5},
          {"t": T, "kind": "BUY", "qty": "nan", "price": 0.5},
          {"t": T, "kind": "BUY", "qty": 10, "price": 0.5, "fee_usd": 0}]
    e = EC.compute_position(_pos(events=ev, reservation=None),
                            lag_samples=LAGS)
    assert e["bought_qty"] == 10.0
    assert e["capital_committed_usd"] == pytest.approx(5.0)


def test_portfolio_without_account_capital_is_unavailable_not_zero():
    e = EC.compute_position(_pos(settlement=None), lag_samples=LAGS)
    rep = EC.portfolio([e], book="PAPER", now=T + H, account_capital=None,
                       open_reservations_usd=0.0, lag_samples=LAGS)
    assert rep["capital_utilization"] is None
    assert rep["unmeasured"]["capital_utilization"] == (
        "NO_ACCOUNT_CAPITAL_RECORD")
    assert rep["idle_capital_usd"] is None
    assert rep["capital_locked_usd"] == pytest.approx(54.0)


# ── the portfolio ───────────────────────────────────────────────────

def test_the_portfolio_capital_picture():
    closed = EC.compute_position(_pos(), lag_samples=LAGS)
    opened = EC.compute_position(_pos(
        position_key="paperpos:a:g2:m2:LONG", settlement=None,
        events=[{"t": T + 6 * H, "kind": "BUY", "qty": 10, "price": 0.30,
                 "fee_usd": 0}], reservation=None,
        event_start_at=T + 6.5 * H), lag_samples=LAGS)
    now = T + 7 * H
    rep = EC.portfolio([closed, opened], book="PAPER", now=now,
                       account_capital=1000.0, account_capital_basis="test",
                       open_reservations_usd=5.0, lag_samples=LAGS)
    assert rep["capital_locked_positions_usd"] == pytest.approx(3.0)
    assert rep["capital_locked_usd"] == pytest.approx(8.0)       # + 5 resv
    assert rep["idle_capital_usd"] == pytest.approx(992.0)
    # the open position's game started 30 min ago: waiting for settlement
    assert rep["capital_waiting_for_settlement_usd"] == pytest.approx(3.0)
    # expected release T+9.5h is 2.5h away
    assert rep["release_schedule"]["WITHIN_6H"]["capital_usd"] == \
        pytest.approx(3.0)
    assert rep["capital_overdue_settlement_usd"] == 0.0
    assert rep["REALIZED_PROFIT_PER_CAPITAL_HOUR"] == pytest.approx(
        46.0 / closed["capital_hours"], rel=1e-6)
    # expected over both positions opened in the window
    exp = (8.0 + (10 * 0.62 - 3.0)) / (270.0 + 3.0 * 3.5)
    assert rep["EXPECTED_PROFIT_PER_CAPITAL_HOUR"] == pytest.approx(exp,
                                                                    rel=1e-6)
    win_h = 30 * 24.0
    ch = closed["capital_hours"] + 3.0 * 1.0
    assert rep["capital_hours_in_window"] == pytest.approx(ch, rel=1e-6)
    assert rep["capital_utilization"] == pytest.approx(ch / (1000 * win_h),
                                                       rel=1e-6)
    assert rep["summed_with_other_book"] is False


# ── the counterfactual: labelled, separate, never summed ─────────────

def _sold():
    return _pos(position_key="paperpos:a:g3:m3:LONG", settlement=None,
                reservation=None,
                events=[{"t": T, "kind": "BUY", "qty": 50, "price": 0.40,
                         "fee_usd": 0.5},
                        {"t": T + H, "kind": "SELL", "qty": 50,
                         "price": 0.45, "fee_usd": 0.5}],
                contract_settlement={"t": T + 6 * H,
                                     "payout_per_contract": 0.0,
                                     "outcome": "LOST", "ref": "s9"})


def test_the_hold_to_settlement_counterfactual_is_its_own_book():
    real = EC.compute_position(_sold(), lag_samples=LAGS)
    cf = EC.counterfactual_hold(_sold(), lag_samples=LAGS)
    assert real["book"] == "PAPER" and real["counterfactual_kind"] is None
    assert cf["book"] == "COUNTERFACTUAL"
    assert cf["counterfactual_kind"] == "HOLD_TO_SETTLEMENT"
    assert cf["basis_book"] == "PAPER"
    assert cf["basis_position_key"] == real["position_key"]
    assert cf["position_key"].startswith("cf:HOLD_TO_SETTLEMENT:")
    assert real["net_profit_usd"] == pytest.approx(22.0 - 20.5)   # 1.5
    assert cf["net_profit_usd"] == pytest.approx(-20.5)           # held, lost
    assert cf["capital_hours"] == pytest.approx(20.5 * 6)


def test_no_counterfactual_without_a_sale_or_a_known_settlement():
    assert EC.counterfactual_hold(_pos(), lag_samples=LAGS) is None
    s = _sold()
    s["contract_settlement"] = None
    assert EC.counterfactual_hold(s, lag_samples=LAGS) is None


def test_paper_actual_and_counterfactual_are_never_summed():
    real = EC.compute_position(_sold(), lag_samples=LAGS)
    cf = EC.counterfactual_hold(_sold(), lag_samples=LAGS)
    act = EC.compute_position(_pos(book="ACTUAL",
                                   position_key="actualpos:V:g:m:LONG"),
                              lag_samples=LAGS)
    allrows = [real, cf, act]
    paper = EC.portfolio([e for e in allrows if e["book"] == "PAPER"],
                         book="PAPER", now=T + 10 * H, account_capital=100.0,
                         lag_samples=LAGS)
    assert paper["realized_net_profit_usd"] == pytest.approx(1.5)
    assert paper["closed_positions"] == 1
    summ = EC.counterfactual_summary(allrows)
    assert summ["book"] == "COUNTERFACTUAL" and summ["positions"] == 1
    assert summ["net_profit_usd"] == pytest.approx(-20.5)
    assert summ["summed_with_other_book"] is False
    # a portfolio is built from ONE book's rows: the runner filters by book
    # and the counterfactual never enters a real book's total
    import inspect

    from sportsassets.profitability import runner as RUN
    src = inspect.getsource(RUN.run_cycle)
    assert 'e["book"] == book' in src
    assert 'loaded["econ"]' in src and "EC.counterfactual_summary(" \
        "loaded[\"cf\"])" in src
