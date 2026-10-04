"""THE FIVE NORTH-STAR METRICS AND THE MONTHLY REVENUE ENGINE.

Statuses are honest (UNAVAILABLE / INSUFFICIENT_SAMPLE / UNPROVEN / MEASURED),
books are never summed, the forecast is UNPROVEN until its own scored
forecasts validate it, and it uses nothing released after it was issued
(out of sample by construction). Pure tests (no database).
"""
from __future__ import annotations

import pytest

from sportsassets.profitability import forecast as FC
from sportsassets.profitability import metrics as MT

DAY = 86400.0
NOW = 1_790_000_000.0 + 0.5 * DAY


def _row(i, net, *, book="PAPER", cap=100.0, ch=50.0, exp=2.0, days_ago=1.0,
         state="CLOSED"):
    t = NOW - days_ago * DAY
    return {"book": book, "position_key": "%s:%d" % (book, i),
            "state": state, "released_at": t if state == "CLOSED" else None,
            "last_event_at": t, "opened_at": t - 3600.0,
            "first_fill_at": t - 3600.0,
            "net_profit_usd": net if state == "CLOSED" else None,
            "capital_committed_usd": cap, "capital_hours": ch,
            "expected_net_profit_usd": exp,
            "predicted_edge_per_dollar": None if exp is None else exp / cap,
            "realized_edge_per_dollar": None if net is None else net / cap,
            "segments": [(t - 3600.0, t, cap)], "open_cost_basis_usd": 0.0}


def _by(ms):
    return {m["metric"]: m for m in ms}


# ── metrics ──────────────────────────────────────────────────────────

def test_nothing_closed_is_unavailable_with_a_reason_never_zero():
    ms = _by(MT.compute([_row(1, None, state="OPEN")], book="PAPER",
                        now=NOW, lookback_days=90))
    assert set(ms) == set(MT.METRICS)
    for m in ms.values():
        assert m["status"] == "UNAVAILABLE", m
        assert m["value"] is None and m["why"]
        assert m["sample_n"] == 0


def test_a_small_sample_is_shown_but_marked_insufficient():
    rows = [_row(i, 5.0) for i in range(3)]
    ms = _by(MT.compute(rows, book="PAPER", now=NOW, lookback_days=90))
    m = ms["REALIZED_NET_EDGE"]
    assert m["status"] == "INSUFFICIENT_SAMPLE"
    assert m["value"] == pytest.approx(0.05) and m["sample_n"] == 3
    assert ms["PROFIT_PER_CAPITAL_HOUR"]["value"] == pytest.approx(0.1)


def test_measured_metrics_carry_a_ci_period_unit_and_freshness():
    rows = [_row(i, 10.0 if i % 3 else -5.0, days_ago=1 + i * 0.5)
            for i in range(40)]
    ms = _by(MT.compute(rows, book="PAPER", now=NOW, lookback_days=90))
    m = ms["REALIZED_NET_EDGE"]
    net = sum(r["net_profit_usd"] for r in rows if r["released_at"]
              >= NOW - 30 * DAY)
    n = sum(1 for r in rows if r["released_at"] >= NOW - 30 * DAY)
    assert m["status"] == "MEASURED" and m["sample_n"] == n
    assert m["value"] == pytest.approx(net / (100.0 * n))
    assert m["ci_low"] <= m["value"] <= m["ci_high"]
    assert m["ci_level"] == 0.90
    assert m["period"]["kind"] == "TRAILING_DAYS"
    assert m["unit"] and m["data_as_of"] == max(r["last_event_at"]
                                                for r in rows)
    again = _by(MT.compute(rows, book="PAPER", now=NOW, lookback_days=90))
    assert again["REALIZED_NET_EDGE"]["ci_low"] == m["ci_low"]  # seeded


def test_max_drawdown_is_the_peak_to_trough_of_realized_pnl():
    nets = [10, -15, 5, -20, 30]
    rows = [_row(i, v, days_ago=10 - i) for i, v in enumerate(nets)]
    m = _by(MT.compute(rows, book="PAPER", now=NOW,
                       lookback_days=90))["MAX_DRAWDOWN"]
    assert m["value"] == pytest.approx(30.0)
    assert m["ci_low"] is None and m["ci_why"].startswith("PATH_STATISTIC")
    assert m["higher_is_better"] is False


def test_edge_calibration_is_unproven_without_predictions():
    rows = [_row(i, 5.0, exp=None) for i in range(5)]
    m = _by(MT.compute(rows, book="PAPER", now=NOW,
                       lookback_days=90))["EDGE_CALIBRATION"]
    assert m["status"] == "UNPROVEN" and m["value"] is None
    assert m["why"] == MT.R_NO_PRED
    rows = [_row(i, 5.0, exp=-1.0) for i in range(5)]
    m = _by(MT.compute(rows, book="PAPER", now=NOW,
                       lookback_days=90))["EDGE_CALIBRATION"]
    assert m["status"] == "UNPROVEN" and m["why"] == MT.R_NONPOS_PRED


def test_edge_calibration_realization_ratio_and_slope():
    rows = [_row(i, 1.0 + i * 0.1, exp=2.0 + i * 0.2) for i in range(10)]
    m = _by(MT.compute(rows, book="PAPER", now=NOW,
                       lookback_days=90))["EDGE_CALIBRATION"]
    assert m["value"] == pytest.approx(0.5)              # half realized
    assert m["detail"]["ols_slope_realized_on_predicted"] == pytest.approx(
        0.5)
    assert len(m["detail"]["reliability_quintiles"]) == 5


def test_probability_of_a_positive_30_day_pnl_needs_history():
    rows = [_row(i, 3.0, days_ago=2 + i) for i in range(40)]
    m = _by(MT.compute(rows, book="PAPER", now=NOW,
                       lookback_days=90))["PROB_POSITIVE_ROLLING_30D_PNL"]
    assert m["value"] == pytest.approx(1.0)
    assert m["status"] == "MEASURED"
    assert m["detail"]["empirical_positive_window_share"] == 1.0
    short = [_row(i, 3.0, days_ago=2 + i) for i in range(5)]
    m = _by(MT.compute(short, book="PAPER", now=NOW,
                       lookback_days=90))["PROB_POSITIVE_ROLLING_30D_PNL"]
    assert m["status"] == "INSUFFICIENT_SAMPLE"
    assert m["detail"]["empirical_positive_window_share"] is None


def test_days_without_a_closure_are_measured_zeros():
    rows = [_row(1, 5.0, days_ago=5), _row(2, -1.0, days_ago=2)]
    s = MT.daily_series(rows, now=NOW, lookback_start=NOW - 90 * DAY)
    assert len(s) == 5 and [v for _, v in s].count(0.0) == 3


def test_books_are_never_summed():
    rows = ([_row(i, 10.0) for i in range(3)]
            + [_row(10 + i, -50.0, book="ACTUAL") for i in range(2)]
            + [_row(20, 999.0, book="COUNTERFACTUAL")])
    p = _by(MT.compute(rows, book="PAPER", now=NOW, lookback_days=90))
    a = _by(MT.compute(rows, book="ACTUAL", now=NOW, lookback_days=90))
    assert p["REALIZED_NET_EDGE"]["sample_n"] == 3
    assert p["REALIZED_NET_EDGE"]["value"] == pytest.approx(0.1)
    assert a["REALIZED_NET_EDGE"]["value"] == pytest.approx(-0.5)
    assert all(m["book"] == "PAPER" for m in p.values())


def test_trend_direction_and_improvement():
    cur = {"metric": "REALIZED_NET_EDGE", "value": 0.2}
    assert MT.trend(cur, None)["direction"] == "UNAVAILABLE"
    t = MT.trend(cur, {"value": 0.1, "computed_at": 1})
    assert t["direction"] == "UP" and t["improving"] is True
    dd = MT.trend({"metric": "MAX_DRAWDOWN", "value": 50.0},
                  {"value": 20.0})
    assert dd["direction"] == "UP" and dd["improving"] is False
    cal = MT.trend({"metric": "EDGE_CALIBRATION", "value": 0.9},
                   {"value": 0.5})
    assert cal["improving"] is True
    assert MT.trend(cur, {"value": None})["direction"] == "UNAVAILABLE"
    assert MT.trend(cur, {"value": 0.2001})["direction"] == "FLAT"


# ── forecast ─────────────────────────────────────────────────────────

def _history(n_days=40, per_day=1, net=lambda i: 3.0 if i % 4 else -4.0):
    return [_row(d * 10 + k, net(d), days_ago=1.5 + d)
            for d in range(n_days) for k in range(per_day)]


def test_too_little_history_is_unavailable_with_no_numbers():
    fc = FC.build(_history(5), book="PAPER", now=NOW, lookback_days=90)
    assert fc["status"] == "UNAVAILABLE"
    assert fc["why"] == "FEWER_THAN_14_DAYS_OF_REALIZED_HISTORY"
    for k in ("expected_pnl_usd", "p10_pnl_usd", "prob_positive",
              "worst_modelled_drawdown_usd", "capital_hours_required"):
        assert fc[k] is None and fc["unmeasured"][k] == fc["why"]


def test_a_forecast_is_unproven_reproducible_and_ordered():
    hist = _history()
    a = FC.build(hist, book="PAPER", now=NOW, lookback_days=90)
    b = FC.build(hist, book="PAPER", now=NOW, lookback_days=90)
    assert a["status"] == "UNPROVEN"
    assert a["why"].startswith("UNPROVEN_UNTIL_FORWARD_CALIBRATION")
    assert a["seed"] == b["seed"] and a["p50_pnl_usd"] == b["p50_pnl_usd"]
    assert a["p10_pnl_usd"] <= a["p50_pnl_usd"] <= a["p90_pnl_usd"]
    assert 0.0 <= a["prob_positive"] <= 1.0
    assert (0.0 <= a["expected_max_drawdown_usd"]
            <= a["p95_max_drawdown_usd"] <= a["worst_modelled_drawdown_usd"])
    assert a["capital_required_usd"] > 0 and a["capital_hours_required"] > 0
    assert a["turnover_required_usd"] > 0
    assert a["capacity_ceiling_usd"] is None
    assert a["unmeasured"]["capacity_ceiling_usd"] == (
        "NO_MEASURED_DAILY_EXECUTABLE_OPPORTUNITY")
    assert len(a["quantiles"]) == 19
    c = FC.build(hist, book="PAPER", now=NOW, lookback_days=90,
                 capacity_daily=10.0, fill_probability=0.5)
    assert c["capacity_ceiling_usd"] == pytest.approx(150.0)


def test_the_forecast_uses_nothing_released_after_it_was_issued():
    hist = _history()
    a = FC.build(hist, book="PAPER", now=NOW, lookback_days=90)
    future = hist + [_row(999, 1e6, days_ago=-3)]          # after NOW
    b = FC.build(future, book="PAPER", now=NOW, lookback_days=90)
    assert a["inputs_sha256"] == b["inputs_sha256"]
    assert a["expected_pnl_usd"] == b["expected_pnl_usd"]


def test_the_forecast_reads_only_its_own_book():
    hist = _history()
    noise = [dict(r, book="ACTUAL", net_profit_usd=-1e4) for r in hist]
    cf = [dict(r, book="COUNTERFACTUAL", net_profit_usd=1e4) for r in hist]
    a = FC.build(hist, book="PAPER", now=NOW, lookback_days=90)
    b = FC.build(hist + noise + cf, book="PAPER", now=NOW, lookback_days=90)
    assert a["inputs_sha256"] == b["inputs_sha256"]


def test_scoring_and_forward_validation():
    fc = FC.build(_history(), book="PAPER", now=NOW, lookback_days=90)
    fc["forecast_id"] = "f1"
    s = FC.score(fc, realized_pnl=fc["p50_pnl_usd"], realized_positions=30,
                 now=NOW + 31 * DAY)
    assert s["inside_p10_p90"] is True
    assert s["pit"] == pytest.approx(0.5, abs=0.06)
    assert s["brier_positive"] == pytest.approx(
        (fc["prob_positive"] - (1.0 if fc["p50_pnl_usd"] > 0 else 0.0)) ** 2)
    low = FC.score(fc, realized_pnl=fc["p10_pnl_usd"] - 1e6,
                   realized_positions=1, now=NOW + 31 * DAY)
    assert low["inside_p10_p90"] is False and low["pit"] < 0.05
    good = [{"inside_p10_p90": i % 5 != 0, "brier_positive": 0.1}
            for i in range(12)]
    assert FC.validation(good[:11])["validated"] is False
    v = FC.validation(good)
    assert v["validated"] is True and v["coverage"] == pytest.approx(
        9 / 12.0)
    assert FC.validation([])["why"] == "NO_FORECAST_SCORED_YET"
    ok = FC.build(_history(), book="PAPER", now=NOW, lookback_days=90,
                  scores=good)
    assert ok["status"] == "FORWARD_VALIDATED" and ok["why"] is None
    bad = [{"inside_p10_p90": True, "brier_positive": 0.1}] * 12
    assert FC.validation(bad)["validated"] is False   # 100% is too wide
