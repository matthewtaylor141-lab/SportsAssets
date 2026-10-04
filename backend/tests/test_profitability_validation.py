"""PROFITABILITY VALIDATION PER SLEEVE (profitability/validation.py and
GET /api/command/profitability/validation).

  §1 THE METRICS (pure): every metric carries value + unit + basis + sample
     size + window, or UNAVAILABLE with a precise reason -- never a
     manufactured zero; realized / unrealized / fees / slippage / drawdown /
     capital-hours / profit per capital-hour / turnover / opportunity-score
     calibration / false-refusal rate / execution-quality delta /
     management-value delta each compute exactly on known rows.
  §2 THE SCOPE (pure): an unclassified group is never INVESTMENT; FORWARD
     membership is the group's ENTRY time, so a pre-cutover position never
     becomes forward evidence however late it resolves.
  §3 THE VERDICT (pure): the pre-declared rule -- NOT_ESTABLISHED /
     NEGATIVE / POSITIVE_BUT_INSUFFICIENT_SAMPLE / SUPPORTED_BY_FORWARD_
     EVIDENCE -- and SUPPORTED never without every check; it reads the
     INVESTMENT sleeve's FORWARD positions only (TRAINING, BENCHMARK and
     UNCLASSIFIED wins cannot make it SUPPORTED).
  §4 THE AUTHORITY: the route is GET only, 401 without a command session,
     holds no SQL write and imports nothing with authority.
  §5 THE DATABASE: over a seeded multi-sleeve paper book the endpoint
     answers per sleeve, forward vs all-time, inside the caller's
     transaction, and writes nothing.
ALL DATA HERE IS SYNTHETIC TEST DATA.
"""
from __future__ import annotations

import ast
import json
import pathlib
import random
import re
import time
import uuid

import pytest

from sportsassets.api import command_validation as CV
from sportsassets.profitability import common as C
from sportsassets.profitability import validation as V

try:
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
NOW = 1_792_000_000.0
SINCE = NOW - 10 * 86400.0
WIN = {"kind": "FORWARD", "start": SINCE, "end": NOW, "since": SINCE,
       "hours": (NOW - SINCE) / 3600.0}


def _pos(gid, *, sleeve="INVESTMENT", at=SINCE + 3600, realized=0.0,
         open_qty=0.0, unreal=None, marked=False, cost=0.0, bf=0.0, sf=0.0,
         acq=100.0, gross=None, released=None, key=None):
    return {"position_key": key or "paperpos:a:%s:m:LONG" % gid,
            "group_id": gid, "sleeve": sleeve, "first_fill_at": at,
            "released_at": (released if released is not None else
                            (at + 3600 if open_qty <= 0 else None)),
            "open_qty": open_qty, "realized_pnl_usd": realized,
            "unrealized_pnl_usd": unreal, "marked": marked,
            "cost_basis_usd": cost, "buy_fees_usd": bf, "sale_fees_usd": sf,
            "acquisition_cost_usd": acq,
            "gross_traded_usd": acq if gross is None else gross}


# ── §1 the metric record ─────────────────────────────────────────────

def test_an_unmeasured_metric_is_unavailable_with_a_reason_never_zero():
    m = V.metric("FEES_USD", None, n=0, basis="b", window=WIN)
    assert m["value"] is None and m["status"] == C.UNAVAILABLE
    assert m["why"] == "NOT_MEASURED"
    m = V.unavailable("TURNOVER", "WHY_X", window=WIN)
    assert (m["value"], m["status"], m["why"]) == (None, C.UNAVAILABLE,
                                                   "WHY_X")
    for k in ("unit", "basis", "sample_n", "window", "status", "why"):
        assert k in m
    m = V.metric("REALIZED_NET_USD", 5.0, n=3, basis="b", window=WIN)
    assert m["status"] == C.INSUFFICIENT and m["why"].startswith("FEWER")
    m = V.metric("REALIZED_NET_USD", 5.0, n=30, basis="b", window=WIN)
    assert m["status"] == C.MEASURED and m["why"] is None


def test_every_metric_has_a_unit():
    assert set(V.UNITS) == set(V.METRICS)


def test_the_sleeves_are_the_classifier_sleeves():
    from sportsassets import bettor_paper_sleeves as SL
    assert V.SLEEVES == SL.SLEEVES


def test_t_critical_values_and_lower_bounds():
    assert V.t_crit_95(1) == 6.314 and V.t_crit_95(30) == 1.697
    assert abs(V.t_crit_95(31) - 1.696) < 2e-3
    assert abs(V.t_crit_95(120) - 1.658) < 2e-3
    assert V.t_crit_95(10_000) == pytest.approx(1.6449, abs=1e-3)
    with pytest.raises(ValueError):
        V.t_crit_95(0)
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    lb = V.mean_lower_bounds(xs, seed=7)
    sd = (2.5) ** 0.5
    assert lb["mean"] == 3.0
    assert lb["t_lower_95"] == pytest.approx(3.0 - 2.132 * sd / 5 ** 0.5,
                                             abs=1e-6)
    assert 1.0 <= lb["bootstrap_lower_95"] <= 3.0
    assert V.mean_lower_bounds(xs, seed=7) == lb          # deterministic
    one = V.mean_lower_bounds([4.0], seed=1)
    assert one["t_lower_95"] is None and one["why"]


def test_spearman():
    assert V.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert V.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert V.spearman([1, 2, 3], [5, 5, 5]) is None
    assert V.spearman([1, 2], [1, 2]) is None
    assert V.ranks([3, 1, 3, 2]) == [3.5, 1.0, 3.5, 2.0]


# ── §1 the ledger metrics ────────────────────────────────────────────

def test_realized_unrealized_fees_and_drawdown():
    pos = [_pos("g1", realized=10.0, bf=0.5, sf=0.25, released=SINCE + 10),
           _pos("g2", realized=-4.0, bf=0.5, released=SINCE + 20),
           _pos("g3", realized=6.0, bf=0.5, released=SINCE + 30),
           _pos("g4", open_qty=100, cost=40.0, unreal=5.0, marked=True,
                realized=1.0, bf=0.2),
           _pos("g5", open_qty=10, cost=7.0, marked=False, bf=0.1)]
    m = V.ledger_metrics(pos, window=WIN, seed=1)
    r = m["REALIZED_NET_USD"]
    assert r["value"] == 13.0 and r["sample_n"] == 5
    assert r["detail"]["resolved_net_usd"] == 12.0
    assert r["detail"]["realized_on_open_positions_usd"] == 1.0
    assert (r["detail"]["wins"], r["detail"]["losses"]) == (2, 1)
    per = m["RESOLVED_NET_PER_POSITION_USD"]
    assert per["value"] == pytest.approx(4.0) and per["sample_n"] == 3
    u = m["UNREALIZED_USD"]
    assert u["value"] == 5.0 and u["status"] == C.MEASURED
    assert u["detail"]["unmarked_positions"] == 1
    assert u["detail"]["unmarked_cost_basis_usd"] == 7.0
    assert u["detail"]["complete"] is False and u["why"]
    f = m["FEES_USD"]
    assert f["value"] == pytest.approx(2.05)
    assert f["detail"]["sale_fees_usd"] == 0.25
    d = m["MAX_DRAWDOWN_USD"]
    assert d["value"] == 4.0
    assert d["detail"]["cumulative_net_usd"] == 12.0


def test_no_position_makes_every_ledger_metric_unavailable():
    m = V.ledger_metrics([], window=WIN, seed=1)
    for v in m.values():
        assert v["value"] is None and v["why"] == V.R_NO_POSITIONS


def test_unrealized_unmarked_and_measured_zero():
    m = V.ledger_metrics([_pos("g", open_qty=5, cost=2.0)], window=WIN,
                         seed=1)
    assert m["UNREALIZED_USD"]["value"] is None
    assert m["UNREALIZED_USD"]["why"] == "NO_OPEN_POSITION_HAS_A_MARK"
    assert m["RESOLVED_NET_PER_POSITION_USD"]["why"] == V.R_NO_RESOLVED
    assert m["MAX_DRAWDOWN_USD"]["why"] == V.R_NO_RESOLVED
    m = V.ledger_metrics([_pos("g", realized=1.0)], window=WIN, seed=1)
    z = m["UNREALIZED_USD"]
    assert z["value"] == 0.0 and z["basis"].startswith("MEASURED_ZERO")


# ── §1 capital ───────────────────────────────────────────────────────

def test_capital_hours_profit_per_capital_hour_and_turnover():
    pos = [_pos("g1", realized=10.0, gross=300.0),
           _pos("g2", realized=-2.0, gross=100.0),
           _pos("g3", open_qty=5, cost=10.0, gross=50.0),
           _pos("g4", realized=1.0, gross=999.0)]          # no econ row
    econ = [{"position_key": pos[0]["position_key"], "state": "CLOSED",
             "net_profit_usd": 10.0, "capital_hours": 100.0},
            {"position_key": pos[1]["position_key"], "state": "CLOSED",
             "net_profit_usd": -2.0, "capital_hours": 60.0},
            {"position_key": pos[2]["position_key"], "state": "OPEN",
             "net_profit_usd": None, "capital_hours": 80.0}]
    m = V.capital_metrics(pos, econ, window=WIN)
    assert m["CAPITAL_HOURS"]["value"] == 240.0
    assert m["CAPITAL_HOURS"]["detail"][
        "positions_without_capital_hours"] == 1
    assert m["PROFIT_PER_CAPITAL_HOUR"]["value"] == pytest.approx(8 / 160)
    assert m["PROFIT_PER_CAPITAL_HOUR"]["sample_n"] == 2
    t = m["TURNOVER"]
    hours = WIN["hours"]
    avg = 240.0 / hours
    assert t["value"] == pytest.approx(450.0 / avg)
    assert t["detail"]["gross_traded_usd"] == 450.0       # g4 excluded
    assert t["detail"]["turnover_per_day"] == pytest.approx(
        (450.0 / avg) / (hours / 24.0))
    assert "EXCLUDED" in t["why"]


def test_capital_metrics_unavailable_paths():
    m = V.capital_metrics([_pos("g")], [], window=WIN,
                          source_why="MIGRATION_216_NOT_APPLIED")
    assert all(v["why"] == "MIGRATION_216_NOT_APPLIED" for v in m.values())
    m = V.capital_metrics([_pos("g")], [], window=WIN)
    assert m["CAPITAL_HOURS"]["why"] == \
        "NO_POSITION_HAS_MEASURED_CAPITAL_HOURS"
    assert m["TURNOVER"]["value"] is None
    m = V.capital_metrics([], [], window=WIN)
    assert all(v["why"] == V.R_NO_POSITIONS for v in m.values())
    p = _pos("g")
    m = V.capital_metrics([p], [{"position_key": p["position_key"],
                                 "state": "CLOSED", "net_profit_usd": 1.0,
                                 "capital_hours": 5.0}],
                          window=dict(WIN, hours=0.0))
    assert m["TURNOVER"]["why"] == "WINDOW_HAS_NO_DURATION"


def test_slippage():
    attr = [{"group_id": "g1", "slippage_usd": 1.5, "slippage_pc": 0.015},
            {"group_id": "g2", "slippage_usd": None, "slippage_pc": None},
            {"group_id": "other", "slippage_usd": 99.0}]
    m = V.slippage_metric(attr, {"g1", "g2"}, window=WIN)
    assert m["value"] == 1.5 and m["sample_n"] == 1
    assert m["detail"]["attribution_rows"] == 2 and "EXCLUDED" in m["why"]
    assert V.slippage_metric(attr, {"zz"}, window=WIN)["value"] is None
    m = V.slippage_metric(attr, {"g2"}, window=WIN)
    assert m["why"] == "SLIPPAGE_UNMEASURED_ON_EVERY_ATTRIBUTION_ROW"
    m = V.slippage_metric(attr, {"g1"}, window=WIN, source_why="M208")
    assert m["why"] == "M208"


# ── §1 opportunity-score calibration ─────────────────────────────────

def _scored(n, *, f=lambda i: float(i)):
    pos, scores = [], []
    for i in range(n):
        g = "c%d" % i
        pos.append(_pos(g, realized=f(i), acq=100.0))
        scores.append({"decision_id": "d%d" % i, "group_id": g,
                       "opportunity_score": float(i) / 100.0})
    return pos, scores


def test_calibration_is_unavailable_below_its_minimum_sample():
    pos, scores = _scored(V.CAL_MIN_N - 1)
    m = V.calibration_metric(scores, pos, {p["group_id"] for p in pos},
                             window=WIN)
    assert m["value"] is None and m["status"] == C.UNAVAILABLE
    assert m["why"] == "FEWER_THAN_%d_SCORED_ENTERED_RESOLVED_CANDIDATES" \
        % V.CAL_MIN_N
    assert m["sample_n"] == V.CAL_MIN_N - 1


def test_calibration_buckets_rank_correlation_and_monotonicity():
    pos, scores = _scored(50)
    # one more scored candidate, entered but still open: not resolved
    pos.append(_pos("open1", open_qty=3, cost=1.0))
    scores.append({"decision_id": "dx", "group_id": "open1",
                   "opportunity_score": 9.0})
    # an unscored-group score row is ignored (not in the sleeve)
    scores.append({"decision_id": "dy", "group_id": "elsewhere",
                   "opportunity_score": 1.0})
    groups = {p["group_id"] for p in pos}
    m = V.calibration_metric(scores, pos, groups, window=WIN)
    assert m["value"] == pytest.approx(1.0) and m["sample_n"] == 50
    d = m["detail"]
    assert d["bucket_kind"] == "DECILES" and len(d["buckets"]) == 10
    assert all(b["n"] == 5 for b in d["buckets"])
    assert d["monotone_non_decreasing"] is True
    assert d["adjacent_rise_share"] == 1.0
    assert d["scored_entered_unresolved"] == 1
    assert d["buckets"][0]["mean_realized_edge_per_dollar"] == \
        pytest.approx(2.0 / 100.0)
    # an inverted score: rho -1, not monotone
    pos, scores = _scored(25, f=lambda i: -float(i))
    m = V.calibration_metric(scores, pos, {p["group_id"] for p in pos},
                             window=WIN)
    assert m["value"] == pytest.approx(-1.0)
    assert m["detail"]["monotone_non_decreasing"] is False
    assert m["detail"]["bucket_kind"] == "5_QUANTILE_BUCKETS"
    m = V.calibration_metric(scores, pos, set(), window=WIN,
                             source_why="MIGRATION_220_NOT_APPLIED")
    assert m["why"] == "MIGRATION_220_NOT_APPLIED"


def test_calibration_with_constant_realized_net_has_no_rank_order():
    pos, scores = _scored(30, f=lambda i: 1.0)
    m = V.calibration_metric(scores, pos, {p["group_id"] for p in pos},
                             window=WIN)
    assert m["value"] is None
    assert m["why"] == "SCORE_OR_REALIZED_NET_IS_CONSTANT_NO_RANK_ORDER"


# ── §1 false-refusal rate ────────────────────────────────────────────

REFUSALS = (
    [{"classification": "FALSE_REFUSAL", "sleeve": "INVESTMENT",
      "decided_at": SINCE + 10, "hypothetical_pnl_usd": 3.0}]
    + [{"classification": "GOOD_REFUSAL", "sleeve": "INVESTMENT",
        "decided_at": SINCE + 10}] * 3
    + [{"classification": "UNKNOWABLE", "sleeve": "INVESTMENT",
        "decided_at": SINCE + 10}] * 2
    + [{"classification": "FALSE_REFUSAL", "sleeve": "INVESTMENT",
        "decided_at": SINCE - 10, "hypothetical_pnl_usd": 1.0}]
    + [{"classification": "GOOD_REFUSAL", "sleeve": "TRAINING",
        "decided_at": SINCE + 10}]
    + [{"classification": "FALSE_REFUSAL", "sleeve": None,
        "decided_at": SINCE + 10}])


def test_false_refusal_rate_excludes_unknowable_and_is_per_sleeve():
    m = V.false_refusal_metric(REFUSALS, window=WIN, sleeve="INVESTMENT",
                               since=SINCE)
    assert m["value"] == pytest.approx(0.25) and m["sample_n"] == 4
    assert m["detail"]["unknowable"] == 2
    assert m["detail"]["unknowable_excluded_from_rate"] is True
    assert m["detail"]["false_refusal_hypothetical_pnl_usd"] == 3.0
    assert m["status"] == C.INSUFFICIENT
    allt = V.false_refusal_metric(REFUSALS, window=WIN, sleeve="INVESTMENT",
                                  since=None)
    assert allt["value"] == pytest.approx(2 / 5)
    t = V.false_refusal_metric(REFUSALS, window=WIN, sleeve="TRAINING",
                               since=SINCE)
    assert t["value"] == 0.0 and t["sample_n"] == 1   # a measured 0 of 1
    book = V.false_refusal_metric(REFUSALS, window=WIN, sleeve=None,
                                  since=SINCE)
    assert book["value"] == pytest.approx(2 / 6)
    assert book["detail"]["unattributed_refusals"] == 1
    assert "BOOK-WIDE" in book["basis"]


def test_false_refusal_rate_unavailable_paths():
    only_unk = [{"classification": "UNKNOWABLE", "sleeve": "INVESTMENT",
                 "decided_at": SINCE + 1}]
    m = V.false_refusal_metric(only_unk, window=WIN, sleeve="INVESTMENT",
                               since=SINCE)
    assert m["value"] is None
    assert m["why"] == "NO_DECIDED_REFUSAL_ONLY_UNKNOWABLE"
    m = V.false_refusal_metric([], window=WIN, sleeve="BENCHMARK",
                               since=SINCE)
    assert m["why"] == "NO_CLASSIFIED_REFUSAL_IN_THE_WINDOW"
    m = V.false_refusal_metric([], window=WIN, sleeve="BENCHMARK",
                               since=SINCE, source_why="M220")
    assert m["why"] == "M220"


# ── §1 execution quality and management value ───────────────────────

def test_execution_quality_delta():
    outs = [{"group_id": "g1", "realized_execution_loss_pp": 0.03,
             "predicted_execution_loss_pp": 0.02,
             "naive_execution_loss_pp": 0.05, "filled_qty": 100},
            {"group_id": "g2", "realized_execution_loss_pp": 0.01,
             "predicted_execution_loss_pp": 0.02,
             "naive_execution_loss_pp": None, "filled_qty": 50},
            {"group_id": "g3", "realized_execution_loss_pp": None,
             "predicted_execution_loss_pp": 0.02},
            {"group_id": "zz", "realized_execution_loss_pp": 9.0,
             "predicted_execution_loss_pp": 0.0}]
    m = V.execution_metric(outs, {"g1", "g2", "g3"}, window=WIN)
    assert m["value"] == pytest.approx(0.0) and m["sample_n"] == 2
    d = m["detail"]
    assert d["outcomes"] == 3
    assert d["mean_realized_minus_naive_pp"] == pytest.approx(-0.02)
    assert d["qty_weighted_realized_minus_predicted_usd"] == pytest.approx(
        0.01 * 100 - 0.01 * 50)
    assert d["mean_abs_error_pp"] == pytest.approx(0.01)
    m = V.execution_metric(outs, {"g3"}, window=WIN)
    assert m["value"] is None and "BOTH" in m["why"]
    assert V.execution_metric(outs, set(), window=WIN)["value"] is None
    assert V.execution_metric(outs, {"g1"}, window=WIN,
                              source_why="M217")["why"] == "M217"


def test_management_value_delta():
    def va(g, hold, ex, status="FINAL"):
        inc = {V.HOLD_KEY: ({"available": True, "pnl_usd": hold}
                            if hold is not None else
                            {"available": False, "why": "pending"}),
               V.EXIT_KEY: ({"available": True, "pnl_usd": ex}
                            if ex is not None else
                            {"available": False, "why": "no exit"})}
        return {"group_id": g, "status": status, "incremental": inc}
    rows = [va("g1", 2.0, -1.0), va("g2", -0.5, None),
            va("g3", None, 4.0, "PENDING_SETTLEMENT_FOR_HOLD_COUNTERFACTUAL"),
            va("zz", 100.0, 100.0)]
    m = V.management_metric(rows, {"g1", "g2", "g3"}, window=WIN)
    assert m["value"] == pytest.approx(1.5) and m["sample_n"] == 2
    d = m["detail"]
    assert d["theses"] == 3 and d["final"] == 2
    assert d["hold_incremental_unavailable"] == 1
    assert d["actual_minus_immediate_exit_usd"] == pytest.approx(3.0)
    assert d["exit_incremental_unavailable"] == 1
    m = V.management_metric(rows, {"g3"}, window=WIN)
    assert m["value"] is None and "HOLD_TO_SETTLEMENT" in m["why"]
    assert V.management_metric(rows, set(), window=WIN)["value"] is None


# ── §2 the scope ─────────────────────────────────────────────────────

def test_unclassified_is_never_investment_and_forward_is_by_entry():
    pos = [_pos("inv", sleeve="INVESTMENT"),
           _pos("none", sleeve=None), _pos("odd", sleeve="PRODUCTION"),
           _pos("trn", sleeve="TRAINING"),
           # a group entered before the cutover with a later second leg:
           # its ENTRY decides, so it is not forward
           _pos("old", at=SINCE - 50, key="k-old-1"),
           _pos("old", at=SINCE + 50, key="k-old-2"),
           _pos("pre", at=SINCE - 1, released=NOW - 1)]
    assert V.scope_groups(pos, "INVESTMENT", SINCE) == {"inv"}
    assert V.scope_groups(pos, "INVESTMENT", None) == {"inv", "old", "pre"}
    assert V.scope_groups(pos, "UNCLASSIFIED", None) == {"none", "odd"}
    assert V.scope_groups(pos, "TRAINING", SINCE) == {"trn"}


def test_position_rows_from_the_ledger():
    allp = [{"position_key": "pk1", "group_id": "g1", "open_qty": 0.0,
             "realized_pnl_usd": 4.0, "acquisition_cost_usd": 50.5,
             "buy_fees_usd": 0.5, "sale_fees_usd": 0.25,
             "sale_proceeds_net_usd": 54.25, "first_fill_at": 10.0,
             "last_fill_at": 20.0, "settlement": None,
             "cost_basis_usd": 0.0, "strategy": "X"},
            {"position_key": "pk2", "group_id": "g2", "open_qty": 10.0,
             "realized_pnl_usd": 0.0, "acquisition_cost_usd": 4.0,
             "buy_fees_usd": 0.0, "sale_fees_usd": 0.0,
             "sale_proceeds_net_usd": 0.0, "first_fill_at": 11.0,
             "last_fill_at": 11.0, "settlement": None,
             "cost_basis_usd": 4.0},
            {"position_key": "pk3", "group_id": "g3", "open_qty": 0.0,
             "realized_pnl_usd": -3.0, "acquisition_cost_usd": 3.0,
             "buy_fees_usd": 0.0, "sale_fees_usd": 0.0,
             "sale_proceeds_net_usd": 0.0, "first_fill_at": 12.0,
             "last_fill_at": 12.0,
             "settlement": {"settled_at": 99.0, "outcome": "LOST"},
             "cost_basis_usd": 0.0}]
    views = [{"position_key": "pk2", "mark": {"price": 0.5},
              "unrealized_pnl_usd": 1.0}]
    classes = {"g1": {"sleeve": "INVESTMENT", "strategy": "S"},
               "g3": {"sleeve": "TRAINING", "strategy": "T"}}
    rows = {r["position_key"]: r for r in CV.position_rows(allp, views,
                                                            classes)}
    assert rows["pk1"]["sleeve"] == "INVESTMENT"
    assert rows["pk1"]["gross_traded_usd"] == pytest.approx(50.0 + 54.5)
    assert rows["pk1"]["released_at"] == 20.0
    assert rows["pk2"]["sleeve"] == "UNCLASSIFIED"        # no durable row
    assert rows["pk2"]["marked"] is True
    assert rows["pk2"]["unrealized_pnl_usd"] == 1.0
    assert rows["pk2"]["released_at"] is None
    assert rows["pk3"]["released_at"] == 99.0


# ── §3 the verdict ───────────────────────────────────────────────────

def _resolved(xs, *, sleeve="INVESTMENT", at=SINCE + 60):
    return [_pos("v%d_%s" % (i, uuid.uuid4().hex[:4]), sleeve=sleeve,
                 at=at + i, realized=x, released=at + i + 30)
            for i, x in enumerate(xs)]


def _supported_sample():
    rng = random.Random(3)
    return [round(5.0 + rng.uniform(-2.0, 2.0), 2) for _ in range(40)]


def test_verdict_not_established_negative_and_zero():
    v = V.verdict_rule([], since=SINCE, cutover=SINCE)
    assert v["verdict"] == V.NOT_ESTABLISHED
    assert v["why"] == "NO_RESOLVED_FORWARD_INVESTMENT_POSITION"
    v = V.verdict_rule([_pos("o", open_qty=5, unreal=50.0, marked=True)],
                       since=SINCE, cutover=SINCE)
    assert v["verdict"] == V.NOT_ESTABLISHED      # open gains prove nothing
    v = V.verdict_rule(_resolved([3.0, -5.0]), since=SINCE, cutover=SINCE)
    assert v["verdict"] == V.NEGATIVE
    v = V.verdict_rule(_resolved([2.0, -2.0]), since=SINCE, cutover=SINCE)
    assert v["verdict"] == V.NOT_ESTABLISHED
    assert v["why"] == "FORWARD_REALIZED_NET_IS_ZERO"


def test_verdict_positive_but_insufficient_below_the_minimum():
    v = V.verdict_rule(_resolved([5.0] * 10), since=SINCE, cutover=SINCE)
    assert v["verdict"] == V.POSITIVE_BUT_INSUFFICIENT
    assert "MIN_RESOLVED" in v["failed_checks"]
    assert v["claim"].startswith("NO profitability claim")


def test_verdict_supported_only_when_every_check_passes():
    xs = _supported_sample()
    v = V.verdict_rule(_resolved(xs), since=SINCE, cutover=SINCE, seed=1)
    assert v["verdict"] == V.SUPPORTED, v["failed_checks"]
    assert all(v["checks"].values()) and not v["failed_checks"]
    assert v["evidence"]["t_lower_95"] > 0
    assert v["evidence"]["bootstrap_lower_95"] > 0
    assert v["rule"] is V.VERDICT_RULE
    assert set(v["checks"]) == set(V.VERDICT_RULE["checks"])


def test_verdict_refuses_supported_on_each_failed_check():
    xs = _supported_sample()
    # 1 · a window starting before the R30 cutover is not forward evidence
    v = V.verdict_rule(_resolved(xs), since=SINCE - 1, cutover=SINCE)
    assert v["verdict"] == V.POSITIVE_BUT_INSUFFICIENT
    assert v["failed_checks"] == ["FORWARD_ONLY"]
    # 2 · a positive mean whose lower bounds are not positive
    noisy = [100.0, -95.0] * 20 + [10.0]
    v = V.verdict_rule(_resolved(noisy), since=SINCE, cutover=SINCE)
    assert v["verdict"] == V.POSITIVE_BUT_INSUFFICIENT
    assert "T_LOWER_BOUND_POSITIVE" in v["failed_checks"]
    # 3 · drawdown beyond the stated bound
    dd = xs + [-(V.DRAWDOWN_BOUND_USD + 1.0)] + [V.DRAWDOWN_BOUND_USD] * 3
    v = V.verdict_rule(_resolved(dd), since=SINCE, cutover=SINCE)
    assert v["verdict"] != V.SUPPORTED
    assert "DRAWDOWN_WITHIN_BOUND" in v["failed_checks"]
    # 4 · marked open losses that outweigh the realized gains
    pos = _resolved(xs) + [_pos("open", open_qty=100, unreal=-10_000.0,
                                marked=True)]
    v = V.verdict_rule(pos, since=SINCE, cutover=SINCE)
    assert v["verdict"] == V.POSITIVE_BUT_INSUFFICIENT
    assert v["failed_checks"] == ["NET_INCLUDING_MARKED_OPEN_POSITIVE"]


def test_verdict_never_supported_without_its_evidence_random_sweep():
    rng = random.Random(11)
    for _ in range(150):
        n = rng.randrange(0, 60)
        xs = [rng.gauss(rng.uniform(-3, 6), rng.uniform(0.5, 30))
              for _ in range(n)]
        since = SINCE - rng.choice((0, 0, 5))
        v = V.verdict_rule(_resolved(xs), since=since, cutover=SINCE,
                           seed=rng.randrange(10 ** 6))
        assert v["verdict"] in V.VERDICTS
        if v["verdict"] == V.SUPPORTED:
            assert n >= V.VERDICT_MIN_RESOLVED and sum(xs) > 0
            assert since >= SINCE
            assert v["evidence"]["t_lower_95"] > 0
            assert v["evidence"]["bootstrap_lower_95"] > 0
            assert v["evidence"]["max_drawdown_usd"] <= V.DRAWDOWN_BOUND_USD
        else:
            assert v["claim"].startswith("NO profitability claim")


def test_activation_evidence_is_the_investment_sleeve_forward_only():
    """TRAINING, BENCHMARK and UNCLASSIFIED wins -- and pre-cutover
    INVESTMENT wins -- cannot make the verdict SUPPORTED."""
    xs = _supported_sample()
    data = {"positions": (
        _resolved(xs, sleeve="TRAINING") + _resolved(xs, sleeve="BENCHMARK")
        + _resolved(xs, sleeve="UNCLASSIFIED")
        + _resolved(xs, sleeve="INVESTMENT", at=SINCE - 86400.0)
        + _resolved([2.0, -1.0], sleeve="INVESTMENT"))}
    out = V.compute(data, now=NOW, since=SINCE, cutover=SINCE)
    v = out["profitability_verdict"]
    assert v["sleeve"] == "INVESTMENT" and v["window"] == "FORWARD"
    assert v["evidence"]["resolved_positions"] == 2
    assert v["verdict"] == V.POSITIVE_BUT_INSUFFICIENT
    s = out["sleeves"]
    assert [k for k in s if s[k]["activation_evidence"]] == ["INVESTMENT"]
    inv = s["INVESTMENT"]["windows"]
    assert inv["FORWARD"]["metrics"]["REALIZED_NET_USD"]["value"] == 1.0
    assert inv["ALL_TIME"]["metrics"]["REALIZED_NET_USD"]["value"] == \
        pytest.approx(1.0 + sum(xs))
    assert s["UNCLASSIFIED"]["windows"]["FORWARD"]["metrics"][
        "REALIZED_NET_USD"]["value"] == pytest.approx(sum(xs))
    # with the same evidence in the INVESTMENT sleeve itself: SUPPORTED
    data = {"positions": _resolved(xs, sleeve="INVESTMENT")}
    out = V.compute(data, now=NOW, since=SINCE, cutover=SINCE)
    assert out["profitability_verdict"]["verdict"] == V.SUPPORTED


def test_the_report_shape_every_sleeve_window_and_metric():
    data = {"positions": [_pos("g", realized=1.0)], "refusals": REFUSALS}
    out = V.compute(data, now=NOW, since=SINCE, cutover=SINCE,
                    since_source="QUERY",
                    sources={"econ": "MIGRATION_216_NOT_APPLIED"})
    assert set(out["sleeves"]) == set(V.SLEEVES)
    for s, body in out["sleeves"].items():
        assert set(body["windows"]) == set(V.WINDOWS)
        for w in body["windows"].values():
            assert set(w["metrics"]) == set(V.METRICS)
            for m in w["metrics"].values():
                assert m["unit"] and "basis" in m and "sample_n" in m
                assert m["window"]["kind"] in V.WINDOWS
                if m["value"] is None:
                    assert m["status"] == C.UNAVAILABLE and m["why"], m
    cap = out["sleeves"]["INVESTMENT"]["windows"]["FORWARD"]["metrics"]
    assert cap["CAPITAL_HOURS"]["why"] == "MIGRATION_216_NOT_APPLIED"
    assert cap["SLIPPAGE_USD"]["why"].startswith("NO_ATTRIBUTION")
    assert set(out["book_wide"]["FALSE_REFUSAL_RATE"]) == set(V.WINDOWS)
    assert out["since_source"] == "QUERY"
    assert json.dumps(out)                                 # serialisable


def test_the_cutover_default_and_its_override():
    assert V.cutover_epoch({}) == (V.R30_CUTOVER_EPOCH,
                                   "DEFAULT_R30_CUTOVER")
    assert V.cutover_epoch({V.CUTOVER_ENV: "123.5"}) == (
        123.5, "ENV_" + V.CUTOVER_ENV)
    assert V.cutover_epoch({V.CUTOVER_ENV: "junk"})[1] == \
        "DEFAULT_R30_CUTOVER"


# ── §4 authority ─────────────────────────────────────────────────────

FORBIDDEN = ("execmirror", "kalshi", "pmus", "clob", "executor", "execution",
             "funded", "order", "submit", "venue", "live_")
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+"
                   r"FROM|TRUNCATE|ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+"
                   r"TABLE)", re.I)


def _imports(path):
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_the_route_is_get_only_and_requires_a_command_session():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") == CV.PATH:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert paths and all(m <= {"GET", "HEAD"} for m in paths.values()), paths
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(CV.PATH).status_code == 401
    assert client.get(CV.PATH + "?since=1").status_code == 401
    assert client.post(CV.PATH).status_code in (401, 405)


def test_the_modules_hold_no_write_and_no_authority_import():
    for rel in ("api/command_validation.py", "profitability/validation.py"):
        tree = ast.parse((PKG / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not WRITE.search(node.value), (rel, node.value[:60])
        for imp in _imports(PKG / rel):
            leaf = imp.rsplit(".", 1)[-1]
            assert not any(f in leaf for f in FORBIDDEN), (rel, imp)
    # the pure module does no I/O at all
    imps = _imports(PKG / "profitability" / "validation.py")
    assert imps <= {"__future__", "annotations", "math", "os", "random", "",
                    "common"}, imps


# ── §5 the database ──────────────────────────────────────────────────

SIM_VERSION = "PAPER_SIM_V1"


async def _position(conn, acct, *, strategy, slug, qty, price, at,
                    fee=0.0, outcome=None, payout=None, settle_at=None):
    gid = "paper_group_val_%s" % uuid.uuid4().hex[:10]
    oid = "paperord:val%s" % uuid.uuid4().hex[:10]
    await conn.execute(
        "INSERT INTO paper_orders (order_id, idempotency_key, account_id, "
        " session_id, group_id, role, direction, holding_side, intent, "
        " us_market_slug, fixture, label, order_type, time_in_force, "
        " allow_partial, qty, limit_price, wire_price, filled_qty, state, "
        " decided_at, eligible_at, expires_at, simulator_version, strategy, "
        " terminal_at) VALUES ($1,$1,$2,$3,$4,'ENTRY','BUY','LONG',"
        " 'ORDER_INTENT_BUY_LONG',$5,$6,'{}'::jsonb,'MARKETABLE','IOC',true,"
        " $7,$8,$8,$7,'FILLED',to_timestamp($9),to_timestamp($9),"
        " to_timestamp($9 + 90),$10,$11,to_timestamp($9 + 2))",
        oid, acct["account_id"], acct["session_id"], gid, slug, "fx-" + slug,
        qty, price, float(at), SIM_VERSION, strategy)
    fid = "paperfill:val%s" % uuid.uuid4().hex[:10]
    await conn.execute(
        "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
        " account_id, session_id, group_id, role, direction, holding_side, "
        " us_market_slug, fixture, label, qty, price, wire_price, fee_usd, "
        " gross_usd, filled_at, basis, simulator_version, strategy) VALUES "
        " ($1,$1,$2,$3,$4,$5,'ENTRY','BUY','LONG',$6,$7,'{}'::jsonb,$8,$9,"
        " $9,$10,$11,to_timestamp($12),'DEPTH_WALK_WITHIN_LIMIT',$13,$14)",
        fid, oid, acct["account_id"], acct["session_id"], gid, slug,
        "fx-" + slug, qty, price, fee, round(qty * price, 6), float(at) + 2,
        SIM_VERSION, strategy)
    if outcome is not None:
        pk = "paperpos:%s:%s:%s:LONG" % (acct["account_id"], gid, slug)
        await conn.execute(
            "INSERT INTO paper_settlements (settlement_id, account_id, "
            " position_key, settlement_event_key, version, group_id, "
            " us_market_slug, holding_side, qty, outcome, "
            " payout_per_contract, payout_usd, evidence, evidence_source, "
            " settled_at) VALUES ($1,$2,$3,'test',1,$4,$5,'LONG',$6,$7,$8,"
            " $9,'{}'::jsonb,'TEST_EVIDENCE',to_timestamp($10))",
            "papersettle:val%s" % uuid.uuid4().hex[:10], acct["account_id"],
            pk, gid, slug, qty, outcome, payout, round(qty * payout, 6),
            float(settle_at))
    return gid, "paperpos:%s:%s:%s:LONG" % (acct["account_id"], gid, slug)


async def _econ(conn, pk, gid, *, state, net, ch, now):
    unm = {"expected_net_profit_usd": "TEST",
           "expected_capital_hours": "TEST",
           "realized_profit_per_capital_hour": "TEST",
           "expected_profit_per_capital_hour": "TEST"}
    if net is None:
        unm["net_profit_usd"] = "OPEN"
    await conn.execute(
        "INSERT INTO pos_position_economics (econ_id, book, position_key, "
        " revision, run_id, computed_at, group_id, state, capital_hours, "
        " net_profit_usd, unmeasured, content_sha256, version) VALUES "
        " ($1,'PAPER',$2,1,'run-val',to_timestamp($3),$4,$5,$6,$7,$8::jsonb,"
        " 'sha','TEST')", "econ-val-" + uuid.uuid4().hex[:10], pk, now, gid,
        state, ch, net, json.dumps(unm))


async def _refusal(conn, *, cls, strategy, at):
    rid = "lol-val-" + uuid.uuid4().hex[:10]
    await conn.execute(
        "INSERT INTO lol_ledger (ledger_id, decision_ref, source, "
        " classifier_version, run_id, classified_at, decided_at, strategy, "
        " classification, reason, defect, attribution, "
        " decision_time_net_ev_usd, settlement_evidence_id, "
        " settlement_basis, settled_at, settlement_outcome, "
        " hypothetical_pnl_usd, content_sha256) VALUES ($1,$1,"
        " 'PAPER_DECISION','LOL_CLASSIFIER_V1','r',to_timestamp($2),"
        " to_timestamp($2),$3,$4,'x',$5,$6,$7,'s','b',to_timestamp($2 + 60),"
        " 'WON',$8,'h')", rid, float(at), strategy, cls,
        "DEFECT_X" if cls == "FALSE_REFUSAL" else None,
        "DEFECT" if cls == "FALSE_REFUSAL" else "THRESHOLD",
        1.0 if cls == "FALSE_REFUSAL" else -1.0,
        2.5 if cls == "FALSE_REFUSAL" else 0.0)


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return _Ctx()


@pg
async def test_the_endpoint_over_a_seeded_multi_sleeve_book(monkeypatch):
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_sleeves as SL
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        since = now - 3600.0
        a = await H.new_account(conn, "val", now=now - 3 * 86400)
        acct = a["account_id"]
        inv, trn = "PINNACLE_COMPLETED_GAME_PAPER", \
            "PINNACLE_EXPLORATION_PAPER"
        # pre-cutover INVESTMENT win: ALL_TIME only
        await _position(conn, a, strategy=inv, slug=acct + ":old", qty=100,
                        price=0.40, at=now - 7200, outcome="WON",
                        payout=1.0, settle_at=now - 30)
        # forward INVESTMENT: a win (fee 1.0), a loss, an open marked one
        g1, pk1 = await _position(conn, a, strategy=inv, slug=acct + ":w",
                                  qty=100, price=0.50, at=now - 1800,
                                  fee=1.0, outcome="WON", payout=1.0,
                                  settle_at=now - 600)
        g2, pk2 = await _position(conn, a, strategy=inv, slug=acct + ":l",
                                  qty=50, price=0.40, at=now - 1700,
                                  outcome="LOST", payout=0.0,
                                  settle_at=now - 500)
        g3, pk3 = await _position(conn, a, strategy=inv, slug=acct + ":o",
                                  qty=10, price=0.30, at=now - 1600)
        await H.observe(conn, acct + ":o", now - 20, bids=[(0.35, 10)],
                        offers=[(0.37, 10)])
        # forward TRAINING: a big win that must not reach INVESTMENT
        await _position(conn, a, strategy=trn, slug=acct + ":t", qty=1000,
                        price=0.10, at=now - 1500, outcome="WON",
                        payout=1.0, settle_at=now - 400)
        classes = await SL.classifications(conn, acct)
        assert {c["sleeve"] for c in classes.values()} == {"INVESTMENT",
                                                           "TRAINING"}
        await _econ(conn, pk1, g1, state="CLOSED", net=49.0, ch=10.0,
                    now=now)
        await _econ(conn, pk2, g2, state="CLOSED", net=-20.0, ch=5.0,
                    now=now)
        await _econ(conn, pk3, g3, state="OPEN", net=None, ch=1.0, now=now)
        await conn.execute(
            "INSERT INTO intel_attribution (book, subject_id, group_id, "
            " run_id, computed_at, slippage_usd, slippage_pc) VALUES "
            " ('PAPER',$1,$2,'r',now(),0.75,0.0075)", "val-" + g1, g1)
        monkeypatch.setattr(L, "ACCOUNT_ID", acct)

        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(CV, "_pool", pool)
        CV._CACHE.clear()
        before = await CV.profitability_validation(since=since)
        assert before["status"] == "OK", before["why"]
        for cls, st in (("FALSE_REFUSAL", inv), ("GOOD_REFUSAL", inv),
                        ("GOOD_REFUSAL", inv), ("UNKNOWABLE", inv),
                        ("GOOD_REFUSAL", trn), ("FALSE_REFUSAL", None)):
            await _refusal(conn, cls=cls, strategy=st, at=now - 900)
        counts = await conn.fetchval(
            "SELECT count(*) FROM paper_fills WHERE account_id = $1", acct)
        CV._CACHE.clear()
        got = await CV.profitability_validation(since=since)
        assert got["label"] == "RESEARCH"
        assert got["authority"] == "SHADOW_NO_AUTHORITY"
        assert got["status"] == "OK", got["why"]
        d = got["data"]
        assert d["since"] == since and d["since_source"] == "QUERY"
        fw = d["sleeves"]["INVESTMENT"]["windows"]["FORWARD"]["metrics"]
        at = d["sleeves"]["INVESTMENT"]["windows"]["ALL_TIME"]["metrics"]
        # win: 100 * 1.0 - (50 + 1 fee) = 49; loss: -20
        assert fw["REALIZED_NET_USD"]["value"] == pytest.approx(29.0)
        assert at["REALIZED_NET_USD"]["value"] == pytest.approx(29.0 + 60.0)
        assert fw["FEES_USD"]["value"] == pytest.approx(1.0)
        assert fw["UNREALIZED_USD"]["value"] == pytest.approx(
            10 * 0.35 - 3.0)
        assert fw["MAX_DRAWDOWN_USD"]["value"] == pytest.approx(20.0)
        assert fw["CAPITAL_HOURS"]["value"] == pytest.approx(16.0)
        assert fw["PROFIT_PER_CAPITAL_HOUR"]["value"] == pytest.approx(
            29.0 / 15.0)
        assert fw["TURNOVER"]["value"] is not None
        assert fw["TURNOVER"]["detail"]["gross_traded_usd"] == \
            pytest.approx(50.0 + 20.0 + 3.0)
        assert fw["SLIPPAGE_USD"]["value"] == pytest.approx(0.75)
        assert fw["OPPORTUNITY_SCORE_CALIBRATION"]["value"] is None
        assert fw["EXECUTION_QUALITY_DELTA_PP"]["value"] is None
        assert fw["MANAGEMENT_VALUE_DELTA_USD"]["value"] is None
        # refusals: the deltas this test added (the ledger is book-wide)
        b_fr = before["data"]["sleeves"]["INVESTMENT"]["windows"][
            "FORWARD"]["metrics"]["FALSE_REFUSAL_RATE"]["detail"]
        a_fr = fw["FALSE_REFUSAL_RATE"]["detail"]
        assert a_fr["false_refusals"] - b_fr["false_refusals"] == 1
        assert a_fr["good_refusals"] - b_fr["good_refusals"] == 2
        assert a_fr["unknowable"] - b_fr["unknowable"] == 1
        bw_b = before["data"]["book_wide"]["FALSE_REFUSAL_RATE"]["FORWARD"]
        bw_a = d["book_wide"]["FALSE_REFUSAL_RATE"]["FORWARD"]
        assert bw_a["detail"]["unattributed_refusals"] - \
            bw_b["detail"]["unattributed_refusals"] == 1
        # TRAINING's win is its own and never INVESTMENT's
        tw = d["sleeves"]["TRAINING"]["windows"]["FORWARD"]["metrics"]
        assert tw["REALIZED_NET_USD"]["value"] == pytest.approx(900.0)
        assert d["sleeves"]["UNCLASSIFIED"]["windows"]["ALL_TIME"][
            "positions"] == 0
        v = d["profitability_verdict"]
        assert v["sleeve"] == "INVESTMENT" and v["window"] == "FORWARD"
        assert v["evidence"]["resolved_positions"] == 2
        assert v["verdict"] == V.POSITIVE_BUT_INSUFFICIENT
        assert v["verdict"] != V.SUPPORTED
        # read only: nothing was written by the read
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_fills WHERE account_id = $1",
            acct) == counts
        # the default since is the configured cutover
        CV._CACHE.clear()
        dflt = await CV.profitability_validation(since=None)
        assert dflt["data"]["since"] == V.cutover_epoch()[0]
        assert dflt["data"]["since_source"] in (
            "DEFAULT_R30_CUTOVER", "ENV_" + V.CUTOVER_ENV)
    finally:
        await tr.rollback()
        await conn.close()
        CV._CACHE.clear()
