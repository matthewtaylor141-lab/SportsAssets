"""CAPITAL-CRITICAL (R30C): LIVE EXECUTION CALIBRATION, THE THREE EVIDENCE
CLASSES KEPT APART.

  §1 THE RULE (pure). PAPER_SIMULATION, LIVE_SHADOW and ACTUAL are separate
     classes. A LIVE estimate uses ACTUAL when it is measured; otherwise it
     is labelled DERIVED_FROM_LIVE_SHADOW / DERIVED_FROM_PAPER_SIMULATION and
     its interval is WIDER than the class's own -- for a mean never below a
     declared floor, so a zero-variance class never gives a zero-width LIVE
     interval. The shadow's fill / slippage / cancel / recovery figures are
     tautological at live scale and never a LIVE estimate. ACTUAL with no
     venue fill is UNAVAILABLE with its reason -- never a zero. Intervals are
     event-clustered; MEASURED needs >= MIN_N INDEPENDENT EVENTS and an
     interval (orders on one event are one observation).
  §2 EVERY LIVE-PATH ESTIMATE STATES WHAT IT WAS FITTED ON: Archer's estimate
     (and so the canonical intent's archer component), the Opportunity Score
     V1's P(fill), Allie's executable net, the external-valuation lane's
     coverage, the twin's books and the micro-calibration lane -- each
     module's own declaration, pinned to execution_evidence.
  §3 THE READ MODEL over production-shaped rows written by the REAL writers
     (canonical intent, the PAPER adapter's simulated order, the SMALL LIVE
     SHADOW proposal, the books): the three classes side by side with n and
     intervals; the shadow proposal priced on the observed book; adverse
     selection at +30 s / +5 min; cancel and recovery; nothing pooled.
  §4 THE DATABASE (migration 300): a venue event can never name a SHADOW
     execution; the rollback refuses while tournament scores exist.
  §5 THE ROUTE is GET only, behind a command session, READ ONLY, and its
     modules hold no write and no order/venue/funded import -- directly or
     in the modules a request loads at run time (calibration_execute /
     calibration_store included); its reads are bounded and say so.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import subprocess
import sys
import time
import uuid

import asyncpg
import pytest

from sportsassets import execution_calibration as EC
from sportsassets import execution_evidence as EE
from sportsassets.agents import archer as E
from sportsassets.api import command_execution_calibration as XC

from tests import paper_harness as H
from tests import r30c_exec_fixture as F

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
MIG = ROOT / "migrations"
UP = (MIG / "300_execution_calibration_and_score_tournament.sql").read_text()
DOWN = (MIG / "rollback" /
        "300_execution_calibration_and_score_tournament.down.sql").read_text()


# ── §1 the rule (pure) ───────────────────────────────────────────────

def test_the_classes_are_three_and_actual_is_unmeasured_with_a_reason():
    assert EE.CLASSES == ("PAPER_SIMULATION", "LIVE_SHADOW", "ACTUAL")
    assert EE.LIVE_PRECEDENCE == ("ACTUAL", "LIVE_SHADOW", "PAPER_SIMULATION")
    assert EE.TRANSFER_PENALTY["ACTUAL"] == 1.0
    # the shadow's smaller penalty applies only where it measures something
    # (adverse selection): its fill-family figures are never eligible
    assert EE.TRANSFER_PENALTY["LIVE_SHADOW"] < \
        EE.TRANSFER_PENALTY["PAPER_SIMULATION"]
    assert EE.LIVE_ELIGIBLE[EE.FILL_FAMILY] == ("ACTUAL", "PAPER_SIMULATION")
    assert EE.LIVE_ELIGIBLE[EE.MARKOUT_FAMILY] == EE.LIVE_PRECEDENCE
    for m in EC.METRICS:
        assert EC.live_eligible(EE.LIVE_SHADOW, m) is m.startswith(
            "adverse_selection"), m
    assert "TAUTOLOGICAL_AT_LIVE_SCALE" in EE.R_SHADOW_TAUTOLOGICAL
    assert EE.MEAN_TRANSFER_FLOOR[EE.USD_PER_CONTRACT] > 0
    assert "SHADOW" in EE.R_NO_ACTUAL and "small_live_order_events" in \
        EE.R_NO_ACTUAL
    for cls in (EE.PAPER_SIMULATION, EE.LIVE_SHADOW, EE.NO_FILL_EVIDENCE):
        assert "NOT_PROOF_OF_LIVE_EXECUTION" in EE.LIVE_USE[cls]
    p = EE.provenance(EE.PAPER_SIMULATION, basis="x", n=40)
    assert p["is_proof_of_live_execution"] is False
    assert p["actual"] == {"status": "UNMEASURED", "why": EE.R_NO_ACTUAL}
    with pytest.raises(ValueError):
        EE.provenance("SOMETHING_ELSE", basis="x")


def test_clustered_outcomes_never_buy_a_narrower_interval():
    ys = [1, 1, 1, 1, 0, 0, 0, 0] * 5
    solo = EE.clustered_proportion(ys)
    # the same outcomes, every event's four orders moving together
    cl = [i // 4 for i in range(len(ys))]
    tied = EE.clustered_proportion(ys, cl)
    assert solo["value"] == tied["value"] == 0.5
    assert tied["deff"] > 1.0 and tied["n_eff"] < solo["n_eff"]
    assert (tied["ci_high"] - tied["ci_low"]) > (solo["ci_high"]
                                                 - solo["ci_low"])
    assert tied["clusters"] == 10 and solo["clusters"] == 40
    m = EE.clustered_mean([0.01, 0.02, 0.03, -0.01], ["a", "a", "b", "c"])
    assert m["clusters"] == 3 and m["ci_low"] < m["value"] < m["ci_high"]
    assert EE.clustered_mean([0.01], ["a"])["why"] == \
        "FEWER_THAN_TWO_INDEPENDENT_EVENTS"
    assert EE.clustered_proportion([])["value"] is None
    # forty orders on ONE event are one observation of that event: no
    # clustered interval, no effective sample, never MEASURED (R30C review:
    # it used to fall back to n_eff = 40, narrower than 40 independent
    # events)
    one = EE.clustered_proportion([1] * 30 + [0] * 10, ["ev"] * 40)
    assert one["value"] == 0.75 and one["clusters"] == 1
    assert one["n_eff"] is None and one["ci_low"] is None
    assert one["why"] == "FEWER_THAN_TWO_INDEPENDENT_EVENTS"
    assert EE.status_of(one) == "INSUFFICIENT_SAMPLE"
    assert EE.choose_live({EE.PAPER_SIMULATION: one},
                          kind="proportion")["status"] == "UNAVAILABLE"
    m1 = EE.clustered_mean([0.01] * 40, ["ev"] * 40)
    assert EE.status_of(m1) == "INSUFFICIENT_SAMPLE"
    # and MIN_N counts INDEPENDENT EVENTS, not orders: 40 orders on 10
    # events are below the floor of 20
    ten = EE.clustered_proportion(ys, cl)
    assert ten["n"] == 40 and ten["clusters"] == 10
    assert EE.status_of(ten) == "INSUFFICIENT_SAMPLE"
    assert "FEWER_THAN_20_INDEPENDENT_EVENTS (events=10, n=40)" == \
        EE.insufficient_why(ten)
    assert EE.t_crit_95(1) == 12.706 and EE.t_crit_95(200) == \
        pytest.approx(1.972, abs=1e-3)


def _stat(value, n, lo, hi):
    return {"value": value, "n": n, "n_eff": n, "ci_low": lo, "ci_high": hi,
            "clusters": n}


def test_a_live_estimate_uses_actual_when_measured_else_is_labelled_and_wider():
    paper = _stat(0.9, 400, 0.87, 0.93)
    shadow = _stat(0.97, 200, 0.94, 0.99)
    actual = _stat(0.8, 25, 0.6, 0.92)
    got = EE.choose_live({EE.PAPER_SIMULATION: paper, EE.LIVE_SHADOW: shadow,
                          EE.ACTUAL: actual}, kind="proportion")
    assert got["fitted_on"] == EE.ACTUAL and got["is_proof_of_live_execution"]
    assert got["transfer_penalty"] == 1.0
    # no ACTUAL: derived from shadow, labelled, wider than its own interval
    got = EE.choose_live({EE.PAPER_SIMULATION: paper, EE.LIVE_SHADOW: shadow,
                          EE.ACTUAL: {"value": None, "n": 0,
                                      "why": EE.R_NO_ACTUAL}},
                         kind="proportion")
    assert got["fitted_on"] == EE.LIVE_SHADOW
    assert got["live_use"] == EE.LIVE_USE[EE.LIVE_SHADOW]
    assert got["is_proof_of_live_execution"] is False
    lo, hi = EE.wilson(0.97, 200)
    assert got["live_ci_low"] < lo and got["live_ci_high"] >= hi - 1e-9
    assert got["not_used"][EE.ACTUAL] == EE.R_NO_ACTUAL
    # shadow below the sample floor is never used: simulation, wider still
    got = EE.choose_live({EE.PAPER_SIMULATION: paper,
                          EE.LIVE_SHADOW: _stat(1.0, 5, 0.6, 1.0),
                          EE.ACTUAL: {}}, kind="proportion")
    assert got["fitted_on"] == EE.PAPER_SIMULATION
    assert "FEWER_THAN_20" in got["not_used"][EE.LIVE_SHADOW]
    lo, hi = EE.wilson(0.9, 400)
    assert got["live_ci_low"] < lo and got["live_ci_high"] > hi
    # a mean widens by sqrt(K)
    got = EE.choose_live({EE.PAPER_SIMULATION: _stat(0.01, 100, 0.0, 0.02)},
                         kind="mean")
    assert got["live_ci_low"] == pytest.approx(0.01 - 0.01 * 3)
    assert got["live_ci_high"] == pytest.approx(0.01 + 0.01 * 3)
    # nothing measured: UNAVAILABLE, never a zero
    got = EE.choose_live({}, kind="proportion")
    assert got["status"] == "UNAVAILABLE" and got["value"] is None
    # a class not eligible for the metric is never used, and says why
    got = EE.choose_live({EE.PAPER_SIMULATION: paper, EE.LIVE_SHADOW: shadow},
                         kind="proportion",
                         eligible=EE.LIVE_ELIGIBLE[EE.FILL_FAMILY],
                         ineligible_why={
                             EE.LIVE_SHADOW: EE.R_SHADOW_TAUTOLOGICAL})
    assert got["fitted_on"] == EE.PAPER_SIMULATION
    assert got["not_used"][EE.LIVE_SHADOW] == EE.R_SHADOW_TAUTOLOGICAL
    # a MEASURED live estimate always carries a live interval
    assert got["live_ci_low"] is not None and got["live_ci_high"] is not None


def test_a_zero_variance_class_never_yields_a_zero_width_live_interval():
    """R30C review: slippage at live scale is exactly 0 on every order, and
    the LIVE interval used to be [0.0, 0.0] -- shadow / simulated execution
    quality shown with no uncertainty. The declared floor (one $0.01 tick
    per contract) keeps it honest; ACTUAL alone is not widened."""
    flat = _stat(0.0, 50, 0.0, 0.0)
    for cls in (EE.PAPER_SIMULATION, EE.LIVE_SHADOW):
        w = EE.widen(flat, cls, kind="mean", floor_unit=EE.USD_PER_CONTRACT)
        assert w["live_ci_low"] == pytest.approx(-0.01)
        assert w["live_ci_high"] == pytest.approx(0.01)
        got = EE.choose_live({cls: flat}, kind="mean",
                             floor_unit=EE.USD_PER_CONTRACT)
        assert got["status"] == "MEASURED"
        assert got["live_ci_high"] - got["live_ci_low"] >= 0.02 - 1e-12
    w = EE.widen(flat, EE.ACTUAL, kind="mean", floor_unit=EE.USD_PER_CONTRACT)
    assert w["live_ci_low"] == w["live_ci_high"] == 0.0
    # Archer's markout view: a zero-spread history still gets a live width
    mv = EE.mean_view(0.004, 0.0, 40, EE.PAPER_SIMULATION)
    assert mv["class_ci_low"] == mv["class_ci_high"] == 0.004
    assert mv["live_ci_high"] - mv["live_ci_low"] == pytest.approx(0.02)
    # a quantity share has its own floor (five points)
    w = EE.widen(_stat(1.0, 50, 1.0, 1.0), EE.PAPER_SIMULATION, kind="mean",
                 floor_unit=EE.FRACTION)
    assert w["live_ci_low"] == pytest.approx(0.95)


def test_summarise_never_reports_a_zero_for_an_unmeasured_class():
    out = EC.summarise(EE.ACTUAL, [], {}, unavailable_why=EE.R_NO_ACTUAL)
    assert out["status"] == "UNMEASURED" and out["orders"] == 0
    # an empty ACTUAL class proves nothing about live execution
    assert out["is_proof_of_live_execution"] is False
    for m in EC.METRICS:
        assert out["metrics"][m]["value"] is None, m
        assert out["metrics"][m]["status"] == "UNAVAILABLE", m
        assert out["metrics"][m]["why"] == EE.R_NO_ACTUAL, m


def _obs(i, *, full=True, cluster=None, opp=None, t=0.0, adv=0.01):
    return {"cls": EE.PAPER_SIMULATION, "intent_id": "cdi_%d" % i,
            "opportunity_id": opp or "opp_%d" % i,
            "cluster": cluster or "ev_%d" % i, "decided_at": t,
            "terminal": True, "requested_qty": 100.0,
            "filled_qty": 100.0 if full else 0.0, "full": full, "any": full,
            "vwap": 0.505 if full else None, "limit": 0.52,
            "best_at_decision": 0.50,
            "adverse": {30.0: adv, 300.0: 2 * adv} if full else {},
            "cancelled_remainder": not full}


def test_summarise_measures_each_metric_with_n_and_intervals():
    obs = [_obs(i, full=(i % 5 != 0), t=float(i)) for i in range(40)]
    EC.mark_recoveries(obs)
    out = EC.summarise(EE.PAPER_SIMULATION, obs, {"PAPER_REFUSED:x": 2})
    m = out["metrics"]
    assert out["orders"] == 40 and out["independent_events"] == 40
    assert m["fill_rate"]["independent_events"] == 40
    assert m["fill_rate"]["value"] == pytest.approx(32 / 40)
    assert m["fill_rate"]["status"] == "MEASURED"
    assert m["fill_rate"]["ci_low"] < 0.8 < m["fill_rate"]["ci_high"]
    assert m["slippage_vs_limit_pp"]["value"] == pytest.approx(-0.015)
    assert m["slippage_vs_decision_best_pp"]["value"] == pytest.approx(0.005)
    assert m["adverse_selection_30s_pp"]["value"] == pytest.approx(0.01)
    assert m["adverse_selection_300s_pp"]["value"] == pytest.approx(0.02)
    assert m["cancel_rate"]["value"] == pytest.approx(8 / 40)
    # 8 cancelled, none of whose opportunity was retried: recovery measured 0
    # on n=8 -> shown, but below the floor (INSUFFICIENT_SAMPLE)
    assert m["recovery_rate"]["n"] == 8
    assert m["recovery_rate"]["status"] == "INSUFFICIENT_SAMPLE"
    assert m["recovery_rate"]["why"].startswith(
        "FEWER_THAN_20_INDEPENDENT_EVENTS")
    assert out["excluded"] == {"PAPER_REFUSED:x": 2}
    live = EC.live_estimates({EE.PAPER_SIMULATION: out,
                              EE.ACTUAL: EC.summarise(
                                  EE.ACTUAL, [], {},
                                  unavailable_why=EE.R_NO_ACTUAL)})
    assert live["fill_rate"]["fitted_on"] == EE.PAPER_SIMULATION
    assert live["fill_rate"]["is_proof_of_live_execution"] is False
    assert live["fill_rate"]["not_used"][EE.ACTUAL] == EE.R_NO_ACTUAL
    # slippage is exactly -0.015 on every fill (zero variance): the LIVE
    # interval still has the declared width
    sl = live["slippage_vs_limit_pp"]
    assert sl["status"] == "MEASURED"
    assert sl["live_ci_high"] - sl["live_ci_low"] >= 0.02 - 1e-9


def test_the_shadow_fill_figures_are_shown_but_never_the_live_estimate():
    """R30C review: production sizes every ENTER within the decision book's
    depth, so the live-size shadow walk of that same book fills by
    construction -- a 100% shadow fill rate must never become the LIVE fill
    rate over a 50% simulated one."""
    paper = [_obs(i, full=(i % 2 == 0), t=float(i)) for i in range(24)]
    shadow = [dict(_obs(i, full=True, t=float(i)), cls=EE.LIVE_SHADOW)
              for i in range(24)]
    for o in shadow:
        o["vwap"], o["best_at_decision"] = 0.50, 0.50
    EC.mark_recoveries(paper)
    EC.mark_recoveries(shadow)
    classes = {EE.PAPER_SIMULATION: EC.summarise(EE.PAPER_SIMULATION, paper,
                                                 {}),
               EE.LIVE_SHADOW: EC.summarise(EE.LIVE_SHADOW, shadow, {}),
               EE.ACTUAL: EC.summarise(EE.ACTUAL, [], {},
                                       unavailable_why=EE.R_NO_ACTUAL)}
    sf = classes[EE.LIVE_SHADOW]["metrics"]["fill_rate"]
    assert sf["value"] == 1.0 and sf["status"] == "MEASURED"   # shown...
    assert sf["live_eligible"] is False                       # ...never used
    assert sf["tautological_at_live_scale"] is True
    assert sf["evidence_kind"] == EE.NO_FILL_EVIDENCE
    sa = classes[EE.LIVE_SHADOW]["metrics"]["adverse_selection_30s_pp"]
    assert sa["live_eligible"] is True and "note" not in sa
    assert classes[EE.PAPER_SIMULATION]["metrics"]["fill_rate"][
        "live_eligible"] is True
    live = EC.live_estimates(classes)
    assert live["fill_rate"]["fitted_on"] == EE.PAPER_SIMULATION
    assert live["fill_rate"]["value"] == pytest.approx(0.5)
    assert live["fill_rate"]["not_used"][EE.LIVE_SHADOW] == \
        EE.R_SHADOW_TAUTOLOGICAL
    for m in ("any_fill_rate", "qty_fill_share", "slippage_vs_limit_pp",
              "slippage_vs_decision_best_pp", "cancel_rate"):
        assert live[m]["fitted_on"] != EE.LIVE_SHADOW, m
    # the market's move after the decision IS measured by the shadow
    assert live["adverse_selection_30s_pp"]["fitted_on"] == EE.LIVE_SHADOW
    assert live["adverse_selection_30s_pp"]["transfer_floor"] == 0.01


def test_a_later_fill_of_the_same_opportunity_is_a_recovery():
    a = _obs(1, full=False, opp="opp_x", t=0.0)
    b = _obs(2, full=True, opp="opp_x", t=30.0)
    c = _obs(3, full=False, opp="opp_y", t=0.0)
    d = _obs(4, full=True, opp="opp_y", t=EC.RECOVERY_WINDOW_S + 5)
    obs = [a, b, c, d]
    EC.mark_recoveries(obs)
    assert a["recovered"] is True and c["recovered"] is False
    assert b["recovered"] is None


# ── §2 every LIVE-path estimate states what it was fitted on ─────────

def _book(offers, bids, at, obs_id=9):
    return {"obs_id": obs_id, "observed_at": at, "tick": None,
            "market_state": None, "offers": H.md(offers=offers)["offers"],
            "bids": H.md(bids=bids)["bids"]}


def test_archers_estimate_carries_its_evidence_class_and_a_wider_live_interval():
    T = 1_791_000_000.0
    h = F.archer_history(40)
    assert h["evidence_class"] == EE.PAPER_SIMULATION
    assert h["adverse_selection"][E.TAKER]["sd_pp"] is not None
    cand = {"decision_id": "paper_d_x", "decided_at": T,
            "us_market_slug": "m", "holding_side": "LONG",
            "proposed_qty": 1000, "limit_price": 0.56, "p_blended": 0.62,
            "economics": {"acquisition": {"fees_usd": 10.0}}}
    est = E.estimate(cand, _book([(0.52, 5000)], [(0.50, 5000)], T - 5), h,
                     now=T + 1)
    ev = est["execution_evidence"]
    assert ev == est["inputs"]["execution_evidence"]       # persisted too
    assert ev["fitted_on"] == EE.PAPER_SIMULATION
    assert ev["is_proof_of_live_execution"] is False
    assert ev["actual"]["why"] == EE.R_NO_ACTUAL
    assert ev["fitted_on_by_input"]["fill_probability"] == EE.PAPER_SIMULATION
    assert ev["fitted_on_by_input"]["slippage"] == EE.NO_FILL_EVIDENCE
    fp = ev["fill_probability"]
    assert fp["value"] == pytest.approx(est["expected_fill_probability"])
    assert fp["n"] == 40
    # clustered by fixture: 40 orders on 20 fixtures
    assert fp["clusters"] == 20 and fp["n_eff"] <= 40
    assert fp["ci_method"] == "WILSON_AT_CLUSTER_EFFECTIVE_N"
    assert fp["live_ci_low"] < fp["class_ci_low"] <= fp["value"]
    assert fp["live_ci_high"] >= fp["class_ci_high"]
    mk = ev["adverse_selection_pp"]
    assert mk["ci_method"] == "CLUSTER_ROBUST_MEAN_T"
    assert mk["live_ci_low"] < mk["class_ci_low"] < mk["class_ci_high"] < \
        mk["live_ci_high"]
    # the WIDER live interval of the EV and net edge (not only a label)
    li = ev["live_interval"]
    assert li["status"] == "MEASURED"
    ev_pt = est["expected_executable_ev_usd"]
    assert li["expected_executable_ev_usd"]["low"] < ev_pt < \
        li["expected_executable_ev_usd"]["high"]
    net = est["expected_net_executable_edge_pp"]
    assert li["expected_net_executable_edge_pp"]["low"] < net
    # the canonical intent's archer component carries both
    from sportsassets import canonical_components as CC
    comp = CC.eddie_component(est)
    assert comp["execution_evidence"]["fitted_on"] == EE.PAPER_SIMULATION
    assert comp["live_interval"]["status"] == "MEASURED"
    assert comp["live_interval"]["expected_executable_ev_usd"] == \
        li["expected_executable_ev_usd"]
    assert comp["live_interval"]["expected_fill_probability"] == {
        "low": fp["live_ci_low"], "high": fp["live_ci_high"]}
    # an unmeasured history is labelled, not bounded by invention
    est2 = E.estimate(cand, _book([(0.52, 5000)], [(0.50, 5000)], T - 5),
                      E.summarise_history([], [], []), now=T + 1)
    assert est2["execution_evidence"]["fill_probability"]["value"] is None
    assert est2["execution_evidence"]["fill_probability"]["why"]
    assert est2["execution_evidence"]["live_interval"]["status"] == \
        "UNAVAILABLE"
    assert CC.eddie_component(est2)["live_interval"]["status"] == \
        "UNAVAILABLE"


def test_an_unmeasured_fill_rate_publishes_no_interval():
    """R30C review: 13 terminal orders (below Archer's MIN_HISTORY 20) gave
    `value: null` beside a class interval [0.667, 0.986] and a LIVE interval
    re-derived from the numerator. A rate declared unmeasured carries no
    interval at all."""
    h = F.archer_history(13, fill_share=11 / 13)
    fr = h["fill_rate"][E.TAKER]
    assert fr["value"] is None and fr["numerator"] == 11
    ev = E.execution_evidence(h, E.TAKER)
    fp = ev["fill_probability"]
    assert fp["value"] is None and fp["why"].startswith("TERMINAL_ORDERS")
    for k in ("class_ci_low", "class_ci_high", "live_ci_low",
              "live_ci_high"):
        assert fp[k] is None, k
    mk = ev["adverse_selection_pp"]
    assert mk["value"] is None and mk["live_ci_low"] is None
    assert EE.proportion_view(None, 11, 13, EE.PAPER_SIMULATION)[
        "class_ci_low"] is None


def test_every_declaration_is_pinned_to_the_evidence_classes():
    from sportsassets import bettor_entry_execution as EX
    from sportsassets import calibration_execute as CEX
    from sportsassets import calibration_store as CST
    from sportsassets.lost_opportunity import score as SC
    from sportsassets.twin import common as TC
    assert SC.EXECUTION_EVIDENCE_CLASS == EE.PAPER_SIMULATION
    assert SC.EXECUTION_EVIDENCE_LIVE_USE == EE.LIVE_USE[EE.PAPER_SIMULATION]
    assert EX.EXECUTION_EVIDENCE_CLASS == EE.NO_FILL_EVIDENCE
    assert EX.EXECUTION_EVIDENCE["live_use"] == EE.LIVE_USE[EE.NO_FILL_EVIDENCE]
    assert EX.EXECUTION_EVIDENCE["is_proof_of_live_execution"] is False
    assert EX.EXECUTION_EVIDENCE["live_interval"]["status"] == "UNAVAILABLE"
    assert CST.EXECUTION_EVIDENCE_CLASS == EE.ACTUAL
    assert CEX.EXECUTION_EVIDENCE_CLASS == EE.ACTUAL
    # the calibration lane's declarations are RESTATED in the route's
    # registry (a module a request reaches never imports the venue submit /
    # cancel module) -- pinned equal here, the only place both are imported
    assert EC.CALIBRATION_LANE_EXECUTION_EVIDENCE_CLASS == {
        "calibration_store": CST.EXECUTION_EVIDENCE_CLASS,
        "calibration_execute": CEX.EXECUTION_EVIDENCE_CLASS}
    assert E.EXECUTION_EVIDENCE_CLASS == EE.PAPER_SIMULATION
    assert TC.EXECUTION_EVIDENCE_CLASS == EC.TWIN_EXECUTION_EVIDENCE_CLASS
    assert TC.ACTUAL_BOOK_PATH == EC.TWIN_ACTUAL_BOOK_PATH
    rows = EC.estimates_in_use()
    mods = " ".join(r["module"] for r in rows)
    for m in ("agents/archer.py", "lost_opportunity/score.py",
              "opportunity_score_v2.py", "allie_capital", "twin/common.py",
              "bettor_entry_execution.py", "calibration_store.py",
              "calibration_execute.py"):
        assert m in mods, m
    for r in rows:
        assert r["proof_of_live_execution"] is False, r
        assert r["actual_on_the_canonical_path"]["why"] == EE.R_NO_ACTUAL
        vals = (list(r["fitted_on"].values()) if isinstance(
            r["fitted_on"], dict) else [r["fitted_on"]])
        assert all(v in EE.FITTED_ON_VALUES for v in vals), r


def test_every_displayed_archer_fill_probability_says_what_it_was_fitted_on():
    """R30C review: Archer's agent page, desk and position rooms showed the
    paper fill probability with no evidence class. Each read model now
    carries the label (from the estimate's own execution_evidence, or
    inferred PAPER_SIMULATION for an estimate recorded before R30C, said
    so), and each renderer draws it beside the number."""
    from sportsassets import position_rooms as PR
    from sportsassets.api import agent_desks as AD
    from sportsassets.api import agent_pages as AP
    T = 1_791_000_000.0
    est = E.estimate({"decision_id": "paper_d_lbl", "decided_at": T,
                      "us_market_slug": "m", "holding_side": "LONG",
                      "proposed_qty": 1000, "limit_price": 0.56,
                      "p_blended": 0.62,
                      "economics": {"acquisition": {"fees_usd": 10.0}}},
                     _book([(0.52, 5000)], [(0.50, 5000)], T - 5),
                     F.archer_history(40), now=T + 1)
    stored = {k: est[k] for k in ("estimate_id", "decision_id",
                                  "expected_fill_probability")}
    stored["inputs"] = json.dumps(est["inputs"])      # as jsonb comes back
    lbl = EE.fill_probability_label(stored)
    assert lbl["fitted_on"] == EE.PAPER_SIMULATION
    assert lbl["is_proof_of_live_execution"] is False
    assert lbl["live_ci_low"] == est["execution_evidence"][
        "fill_probability"]["live_ci_low"]
    legacy = EE.fill_probability_label({"inputs": {"walk": None}})
    assert legacy["fitted_on"] == EE.PAPER_SIMULATION
    assert legacy["basis"].startswith("LABEL_INFERRED_ESTIMATE_PREDATES")
    # the API rows (agent pages), the desk and the rooms carry it
    row = E._with_links(dict(stored, inputs=est["inputs"],
                             evidence_refs=[]))
    assert row["fill_probability_evidence"]["fitted_on"] == \
        EE.PAPER_SIMULATION
    room = PR.archer_view(True, [dict(stored, inputs=est["inputs"],
                                     estimated_at=T)])
    assert room["estimates"][0]["fill_probability_evidence"][
        "fitted_on"] == EE.PAPER_SIMULATION
    src = (PKG / "agents" / "archer.py").read_text()
    assert '"fill_probability_evidence": EE.fill_probability_label(cur)' in \
        src
    # the renderers draw it beside the number
    assert "fill_probability_evidence" in AD.DESK_JS
    assert "'<div class=\"s\">fitted on '" in AD.DESK_JS
    assert "label: 'Fill fitted on'" in AP.POS_JS


def test_the_v1_score_and_the_entry_lane_label_their_execution_inputs():
    from sportsassets import bettor_entry_execution as EX
    from sportsassets.lost_opportunity import score as SC
    got = SC.score({"status": "MEASURED", "executable_opportunity_dollars": 10,
                    "executable_capacity_usd": 100, "decided_at": 0.0,
                    "event_start_at": 3600.0}, fill_probability=0.8,
                   fill_basis="b", fill_source=SC.ARCHER,
                   idle_capital_usd=1e6,
                   lag_samples=[(-10.0 - i, 3600.0) for i in range(6)])
    ex = got["components"]["EXECUTION_CONFIDENCE"]
    assert ex["evidence_class"] == EE.PAPER_SIMULATION
    assert "NOT_PROOF_OF_LIVE_EXECUTION" in ex["live_use"]
    none = SC.score({"status": "MEASURED", "executable_opportunity_dollars": 10,
                     "executable_capacity_usd": 100})
    assert none["components"]["EXECUTION_CONFIDENCE"]["evidence_class"] is None
    out = EX.estimate(ladder=None, fair_value=None, fee_fn=None)
    assert out["ok"] is False
    assert out["execution_evidence"]["fitted_on"] == EE.NO_FILL_EVIDENCE


def test_the_shadow_proposal_is_walked_by_the_simulators_own_walk():
    md = H.md(offers=[(0.50, 1), (0.51, 5)], bids=[(0.48, 10)])
    got = XC.shadow_fill(md, holding_side="LONG", qty=3, limit=0.52,
                         time_in_force="IOC")
    assert got["filled_qty"] == 3 and got["best"] == pytest.approx(0.50)
    assert got["vwap"] == pytest.approx((0.50 + 2 * 0.51) / 3)
    beyond = XC.shadow_fill(md, holding_side="LONG", qty=3, limit=0.49,
                            time_in_force="IOC")
    assert beyond["filled_qty"] == 0 and beyond["vwap"] is None
    fok = XC.shadow_fill(md, holding_side="LONG", qty=50, limit=0.52,
                         time_in_force="FOK")
    assert fok["filled_qty"] == 0                 # all or none
    assert XC.side_mid(md, "LONG") == pytest.approx(0.49)
    assert XC.shadow_fill(None, holding_side="LONG", qty=1, limit=0.5,
                          time_in_force="IOC")["why"] == \
        "DECISION_BOOK_UNREADABLE"


# ── §3 the read model over rows the real writers wrote ───────────────

@pg
async def test_three_classes_side_by_side_over_real_writer_rows():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await F.prepare(conn)
        acct = await H.new_account(conn, "xcal")
        T = time.time()
        tag = uuid.uuid4().hex[:8]
        a = await F.decision(conn, acct, T=T, slug="test-xcal-a-%s" % tag,
                             event="ev-a-%s" % tag,
                             offers=[(0.50, 1500), (0.52, 1000)],
                             bids=[(0.48, 2000)])
        r = await F.fill(conn, a, at=T + 4, offers=[(0.50, 1500),
                                                    (0.52, 1000)],
                         bids=[(0.48, 2000)])
        assert r["state"] == "FILLED", r
        # an opportunity SIZED WITHIN its decision book's depth (as the
        # real sizer does) whose book MOVED beyond the limit before the
        # simulator's fill; then re-evaluated
        b = await F.decision(conn, acct, T=T + 5, slug="test-xcal-b-%s" % tag,
                             event="ev-b-%s" % tag, offers=[(0.50, 3000)],
                             bids=[(0.48, 3000)])
        r = await F.fill(conn, b, at=T + 9, offers=[(0.60, 3000)],
                         bids=[(0.58, 3000)])
        assert r["state"] in ("EXPIRED", "CANCELED"), r
        c = await F.decision(conn, acct, T=T + 20,
                             slug="test-xcal-b-%s" % tag,
                             event="ev-b-%s" % tag,
                             offers=[(0.50, 3000)], bids=[(0.48, 3000)])
        r = await F.fill(conn, c, at=T + 24, offers=[(0.50, 3000)],
                         bids=[(0.48, 3000)])
        assert r["state"] == "FILLED", r
        # the books 30 s and 5 min later: the mid fell (adverse to a buyer)
        for slug in ("test-xcal-a-%s" % tag, "test-xcal-b-%s" % tag):
            await H.observe(conn, slug, T + 40, bids=[(0.46, 2000)],
                            offers=[(0.48, 2000)])
            await H.observe(conn, slug, T + 315, bids=[(0.44, 2000)],
                            offers=[(0.46, 2000)])
        mine = {a["intent"]["intent_id"], b["intent"]["intent_id"],
                c["intent"]["intent_id"]}
        p_obs, p_ex, p_rd = await XC.paper_observations(conn, since=T - 2,
                                                        until=T + 30)
        s_obs, s_ex, s_rd = await XC.shadow_observations(conn, since=T - 2,
                                                         until=T + 30)
        a_obs, _a_ex, integrity, a_rd = await XC.actual_observations(
            conn, since=T - 2, until=T + 1000)
        assert p_rd["truncated"] is False and s_rd["truncated"] is False
        assert p_rd["kept"] == "MOST_RECENT_FIRST" and not a_rd["truncated"]
        p = {o["intent_id"]: o for o in p_obs if o["intent_id"] in mine}
        s = {o["intent_id"]: o for o in s_obs if o["intent_id"] in mine}
        assert set(p) == set(s) == mine
        assert integrity == 0 and not [o for o in a_obs
                                       if o["intent_id"] in mine]
        ia, ib, ic = (a["intent"]["intent_id"], b["intent"]["intent_id"],
                      c["intent"]["intent_id"])
        # PAPER_SIMULATION: the simulator walked 2000 through two levels
        assert p[ia]["full"] and p[ia]["filled_qty"] == 2000
        assert p[ia]["vwap"] == pytest.approx((1500 * 0.50 + 500 * 0.52)
                                              / 2000)
        assert p[ia]["limit"] == pytest.approx(0.52)
        assert p[ia]["best_at_decision"] == pytest.approx(0.50)
        # mid at the fill book .49 -> .47 at +30 s -> .45 at +5 min
        assert p[ia]["adverse"][30.0] == pytest.approx(0.02)
        assert p[ia]["adverse"][300.0] == pytest.approx(0.04)
        assert p[ib]["any"] is False and p[ib]["cancelled_remainder"]
        assert p[ib]["recovered"] is True            # c filled the same opp
        assert p[ic]["opportunity_id"] == p[ib]["opportunity_id"]
        # LIVE_SHADOW: the live-size order (2000 / 1000) on the decision book
        assert s[ia]["requested_qty"] == 2 and s[ia]["full"]
        assert s[ia]["vwap"] == pytest.approx(0.50)
        assert s[ia]["adverse"][30.0] == pytest.approx(0.02)
        # ...and b, which the simulator could not fill, "fills" in the
        # shadow: it walks the very book that sized it (tautological)
        assert s[ib]["full"] and not s[ib]["cancelled_remainder"]
        assert s[ib]["vwap"] == pytest.approx(s[ib]["best_at_decision"])
        assert s[ib]["recovered"] is None
        # the classes summarised apart, the ACTUAL class unmeasured by name
        own_p = EC.summarise(EE.PAPER_SIMULATION, list(p.values()), p_ex)
        own_s = EC.summarise(EE.LIVE_SHADOW, list(s.values()), s_ex)
        own_a = EC.summarise(EE.ACTUAL, [], {},
                             unavailable_why=EE.R_NO_ACTUAL)
        assert own_p["metrics"]["fill_rate"]["value"] == pytest.approx(2 / 3)
        assert own_p["metrics"]["fill_rate"]["n"] == 3
        assert own_p["metrics"]["fill_rate"]["clusters"] == 2   # 2 events
        assert own_p["metrics"]["fill_rate"]["status"] == \
            "INSUFFICIENT_SAMPLE"
        assert own_s["metrics"]["slippage_vs_limit_pp"]["value"] == \
            pytest.approx(-0.02)
        assert own_s["metrics"]["fill_rate"]["value"] == 1.0
        assert own_s["metrics"]["fill_rate"]["live_eligible"] is False
        assert own_s["metrics"]["fill_rate"]["note"] == \
            EE.R_SHADOW_TAUTOLOGICAL
        assert own_s["metrics"]["cancel_rate"]["value"] == 0.0
        assert own_p["metrics"]["cancel_rate"]["value"] == \
            pytest.approx(1 / 3)
        assert own_a["metrics"]["fill_rate"]["why"] == EE.R_NO_ACTUAL
        live = EC.live_estimates({EE.PAPER_SIMULATION: own_p,
                                  EE.LIVE_SHADOW: own_s, EE.ACTUAL: own_a})
        # three orders are below every class's floor: no LIVE estimate is
        # claimed from them, and each class says why
        assert live["fill_rate"]["status"] == "UNAVAILABLE"
        assert live["fill_rate"]["not_used"][EE.ACTUAL] == EE.R_NO_ACTUAL
        # the whole report (bounded reads, READ ONLY in the route)
        rep = await XC.report(conn, since=T - 2, until=T + 30, now=T + 400)
        assert set(rep["classes"]) == set(EE.CLASSES)
        assert rep["pooled_across_classes"] is False
        assert rep["classes"]["ACTUAL"]["status"] == "UNMEASURED"
        assert rep["classes"]["ACTUAL"]["integrity_violations"] == 0
        assert rep["classes"]["LIVE_SHADOW"]["is_proof_of_live_execution"] \
            is False
        assert rep["rule_sha"] == EE.RULE_SHA
        assert rep["truncated"] is False
        assert rep["classes"]["PAPER_SIMULATION"]["read"]["cap"] == \
            XC.MAX_INTENTS
        assert rep["archer_fit"]["status"] == "OK"
        assert rep["archer_fit"]["fitted_on"] == EE.PAPER_SIMULATION
        # nothing reached a venue
        assert await conn.fetchval(
            "SELECT count(*) FROM small_live_order_events") == 0
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_production_sized_decisions_never_make_the_shadow_the_live_fill_rate():
    """24 production-shaped decisions through the real writers, each SIZED
    WITHIN its decision book's depth; half find the book moved before the
    simulator's fill. The shadow "fills" all 24 (it walks the book that
    sized it); the LIVE fill rate is the simulator's, labelled and wider,
    and the shadow's 100% is shown with its reason, never as live quality.
    The books 30 s / 5 min after each decision are real venue evidence:
    there the shadow is eligible."""
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await F.prepare(conn)
        acct = await H.new_account(conn, "xcalt")
        T0 = time.time() - 120
        tag = uuid.uuid4().hex[:6]
        ids = set()
        for i in range(24):
            T = T0 + i * 2
            slug = "test-xcalt-%s-%d" % (tag, i)
            d = await F.decision(conn, acct, T=T, slug=slug,
                                 event="ev-xcalt-%s-%d" % (tag, i),
                                 offers=[(0.50, 3000)], bids=[(0.48, 3000)])
            ids.add(d["intent"]["intent_id"])
            later = [(0.50, 3000)] if i % 2 == 0 else [(0.60, 3000)]
            r = await F.fill(conn, d, at=T + 4, offers=later,
                             bids=[(0.48, 3000)])
            assert r["state"] == ("FILLED" if i % 2 == 0 else
                                  r["state"] if r["state"] in (
                                      "EXPIRED", "CANCELED") else "?"), r
            await H.observe(conn, slug, T + 35, bids=[(0.47, 3000)],
                            offers=[(0.49, 3000)])
            await H.observe(conn, slug, T + 305, bids=[(0.46, 3000)],
                            offers=[(0.48, 3000)])
        p_obs, p_ex, _ = await XC.paper_observations(conn, since=T0 - 1,
                                                     until=T0 + 60)
        s_obs, s_ex, _ = await XC.shadow_observations(conn, since=T0 - 1,
                                                      until=T0 + 60)
        p_obs = [o for o in p_obs if o["intent_id"] in ids]
        s_obs = [o for o in s_obs if o["intent_id"] in ids]
        assert len(p_obs) == len(s_obs) == 24
        classes = {
            EE.PAPER_SIMULATION: EC.summarise(EE.PAPER_SIMULATION, p_obs,
                                              p_ex),
            EE.LIVE_SHADOW: EC.summarise(EE.LIVE_SHADOW, s_obs, s_ex),
            EE.ACTUAL: EC.summarise(EE.ACTUAL, [], {},
                                    unavailable_why=EE.R_NO_ACTUAL)}
        pf = classes[EE.PAPER_SIMULATION]["metrics"]["fill_rate"]
        sf = classes[EE.LIVE_SHADOW]["metrics"]["fill_rate"]
        assert pf["value"] == pytest.approx(0.5) and pf["clusters"] == 24
        assert sf["value"] == 1.0 and sf["status"] == "MEASURED"
        assert sf["live_eligible"] is False
        assert classes[EE.LIVE_SHADOW]["metrics"][
            "slippage_vs_decision_best_pp"]["value"] == 0.0
        live = EC.live_estimates(classes)
        lf = live["fill_rate"]
        assert lf["fitted_on"] == EE.PAPER_SIMULATION
        assert lf["value"] == pytest.approx(0.5)
        assert lf["live_ci_low"] < pf["ci_low"] and \
            lf["live_ci_high"] > pf["ci_high"]
        assert lf["not_used"][EE.LIVE_SHADOW] == EE.R_SHADOW_TAUTOLOGICAL
        # the shadow's zero slippage never becomes a LIVE figure: the paper
        # slippage rests on 12 filled events (< 20) -> no LIVE estimate
        assert live["slippage_vs_decision_best_pp"]["status"] == \
            "UNAVAILABLE"
        # adverse selection after the decision: mid .49 -> .48 at +30 s
        sa = live["adverse_selection_30s_pp"]
        assert sa["fitted_on"] == EE.LIVE_SHADOW
        assert sa["value"] == pytest.approx(0.01)
        assert sa["live_ci_high"] - sa["live_ci_low"] >= 0.02 - 1e-9
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_bounded_read_keeps_the_newest_and_says_it_truncated(
        monkeypatch):
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await F.prepare(conn)
        acct = await H.new_account(conn, "xcalcap")
        T = time.time()
        tag = uuid.uuid4().hex[:6]
        made = []
        for i in range(3):
            d = await F.decision(conn, acct, T=T + i,
                                 slug="test-xcalcap-%s-%d" % (tag, i),
                                 event="ev-cap-%s-%d" % (tag, i),
                                 offers=[(0.50, 3000)], bids=[(0.48, 3000)])
            r = await F.fill(conn, d, at=T + i + 4, offers=[(0.50, 3000)],
                             bids=[(0.48, 3000)])
            assert r["state"] == "FILLED", r
            made.append(d["intent"]["intent_id"])
        monkeypatch.setattr(XC, "MAX_INTENTS", 2)
        obs, ex, rd = await XC.paper_observations(conn, since=T - 0.5,
                                                  until=T + 2.5)
        assert rd["truncated"] is True and rd["cap"] == 2
        assert rd["rows_in_window"] == 3 and rd["rows_read"] == 2
        assert {o["intent_id"] for o in obs} == set(made[1:])   # the newest
        assert ex["NOT_READ_OLDER_THAN_THE_2_MOST_RECENT"] == 1
        rep = await XC.report(conn, since=T - 0.5, until=T + 2.5,
                              now=T + 10)
        assert rep["truncated"] is True
        assert rep["classes"]["PAPER_SIMULATION"]["read"]["truncated"]
    finally:
        await tx.rollback()
        await conn.close()


# ── §4 the database ──────────────────────────────────────────────────

async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


@pg
async def test_a_venue_event_can_never_name_a_shadow_execution():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        await conn.execute(UP)                         # idempotent
        await F.prepare(conn)
        acct = await H.new_account(conn, "xcalv")
        tag = uuid.uuid4().hex[:8]
        d = await F.decision(conn, acct, T=time.time(),
                             slug="test-xcalv-%s" % tag, event="ev-%s" % tag,
                             offers=[(0.50, 3000)], bids=[(0.48, 3000)])
        eid = await conn.fetchval(
            "SELECT execution_id FROM canonical_intent_executions WHERE "
            " intent_id = $1 AND adapter = 'SMALL_LIVE'",
            d["intent"]["intent_id"])
        assert eid
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO small_live_order_events (execution_id,
                           venue_order_id, state, cum_qty, source,
                           venue_record) VALUES ($1, 'v-1', 'FILLED', 2,
                           'VENUE_ORDER_RECORD', '{}')""", eid)
        peid = await conn.fetchval(
            "SELECT execution_id FROM canonical_intent_executions WHERE "
            " intent_id = $1 AND adapter = 'PAPER'",
            d["intent"]["intent_id"])
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO small_live_order_events (execution_id,
                           venue_order_id, state, cum_qty, source,
                           venue_record) VALUES ($1, 'v-2', 'FILLED', 2,
                           'VENUE_ORDER_RECORD', '{}')""", peid)
        # each refusal stays separately provable: 300's guard is an AFTER
        # constraint trigger, so on a row 225's CHECK refuses (a PAPER
        # source) the refusal is 225's own sloe_source_ck -- not the 300
        # message (R30C review: a BEFORE trigger used to answer first)
        for eid_, want, cname in (
                (eid, "ACTUAL_FILL_ON_A_NON_LIVE_EXECUTION", None),
                (eid, None, "sloe_source_ck")):
            sp = conn.transaction()
            await sp.start()
            try:
                with pytest.raises(asyncpg.CheckViolationError) as ei:
                    await conn.execute(
                        """INSERT INTO small_live_order_events (execution_id,
                             venue_order_id, state, cum_qty, source,
                             venue_record) VALUES ($1, 'v-3', 'FILLED', 2,
                             $2, '{}')""", eid_,
                        "VENUE_ORDER_RECORD" if want else "PAPER_FILL")
                if want:
                    assert want in str(ei.value)
                else:
                    assert ei.value.constraint_name == cname
            finally:
                await sp.rollback()
        assert await conn.fetchval(
            "SELECT tgconstraint <> 0 FROM pg_trigger WHERE tgname = "
            "'small_live_order_events_live_only_trg'") is True
        assert await conn.fetchval(
            "SELECT count(*) FROM small_live_order_events") == 0
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_300_rollback_refuses_over_scores_and_drops_only_its_objects():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        await F.prepare(conn)
        acct = await H.new_account(conn, "xcalr")
        tag = uuid.uuid4().hex[:8]
        d = await F.decision(conn, acct, T=time.time(),
                             slug="test-xcalr-%s" % tag, event="ev-%s" % tag,
                             offers=[(0.50, 3000)], bids=[(0.48, 3000)])
        assert d["tournament_recorded"] is True
        await _expect(conn, asyncpg.exceptions.RaiseError, DOWN)
        drops = [ln for ln in DOWN.splitlines() if ln.startswith("DROP ")]
        assert drops and all(
            "opportunity" in ln or "small_live_order_event_live_only" in ln
            for ln in drops), drops
    finally:
        await tx.rollback()
        await conn.close()
    # with no score, the rollback drops 300's objects cleanly (applied into
    # a scratch schema first on the search path, inside a rolled-back
    # transaction, so the shared table and its rows are untouched)
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("CREATE SCHEMA r30c_rollback_probe")
        await conn.execute(
            "SET LOCAL search_path = r30c_rollback_probe, public")
        await conn.execute(UP)
        assert await conn.fetchval(
            "SELECT to_regclass('r30c_rollback_probe."
            "opportunity_score_tournament') IS NOT NULL")
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT to_regclass('r30c_rollback_probe."
            "opportunity_score_tournament') IS NULL")
        assert await conn.fetchval(
            "SELECT to_regclass('public.canonical_decision_intents') "
            "IS NOT NULL")
        assert await conn.fetchval(
            "SELECT to_regclass('public.small_live_order_events') IS NOT NULL")
        await conn.execute(UP)                 # and it re-applies
    finally:
        await tx.rollback()
        await conn.close()


# ── §5 the route and the modules ─────────────────────────────────────

WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+FROM|"
                   r"TRUNCATE|ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+TABLE)\b",
                   re.I)
FORBIDDEN = ("execmirror", "kalshi", "pmus", "venue", "clob", "executor",
             "execution_intent", "execution_gate", "bettor_funded", "submit",
             "live_parity", "live_executor", "smalllive",
             "calibration_execute", "calibration_store")


def _imports(path) -> set:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_the_modules_hold_no_write_and_no_order_or_venue_import():
    for rel in ("api/command_execution_calibration.py",
                "execution_calibration.py", "execution_evidence.py"):
        for node in ast.walk(ast.parse((PKG / rel).read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not WRITE.search(node.value), (rel, node.value[:60])
        for imp in _imports(PKG / rel):
            leaf = imp.rsplit(".", 1)[-1]
            assert not any(f in leaf for f in FORBIDDEN), (rel, imp)
    # the evidence rule is pure: standard library only
    assert _imports(PKG / "execution_evidence.py") <= {
        "__future__", "annotations", "hashlib", "json", "math"}


#: what a request to the route may never load, transitively, at run time
RUNTIME_FORBIDDEN = re.compile(
    r"execmirror|kalshi|pmus|venue|clob|executor|execution_intent|"
    r"execution_gate|funded|submit|live_parity|smalllive|small_live|"
    r"calibration_execute|calibration_store|mirror")


def test_no_module_a_request_loads_reaches_a_venue_or_execution_module():
    """R30C review: estimates_in_use imported calibration_execute (the venue
    submit / cancel module, "WIRED TO NOTHING") one hop outside api/, past
    every text-scan guard. This checks the RUN-TIME import closure: a fresh
    interpreter imports the route, builds its estimate registry and loads
    what archer_fit and the envelope load, and lists every sportsassets
    module now in sys.modules."""
    code = (
        "import sys, json\n"
        "import sportsassets.api.command_execution_calibration as X\n"
        "X.EC.estimates_in_use()\n"
        "from sportsassets.agents import archer\n"
        "from sportsassets.profitability import common\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m.startswith('sportsassets'))))\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    mods = json.loads(out.stdout.strip().splitlines()[-1])
    assert "sportsassets.api.command_execution_calibration" in mods
    bad = [m for m in mods if RUNTIME_FORBIDDEN.search(m)]
    assert bad == [], bad


def test_the_route_is_get_only_and_requires_a_command_session():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") == XC.PATH:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert paths and all(m <= {"GET", "HEAD"} for m in paths.values()), paths
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(XC.PATH).status_code == 401
    assert client.post(XC.PATH).status_code in (401, 405)
    src = (PKG / "api" / "command_execution_calibration.py").read_text()
    assert "readonly=not nested" in src and "statement_timeout" in src
    assert json.dumps(EE.RULE)                    # serializable as shown
