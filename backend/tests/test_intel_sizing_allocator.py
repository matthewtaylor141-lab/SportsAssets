"""CAPITAL-CRITICAL (SHADOW): EVIDENCE-QUALITY SIZING AND THE CHIEF
ALLOCATOR -- computed by hand, persisted beside the real sizes, never applied.

  §1 the shadow size: Kelly on the conservative edge, every evidence
     multiplier, the binding cap; unmeasured inputs take declared
     conservative defaults and say so; no edge or NO_TRADE -> 0
  §2 the allocator: ranked by risk-adjusted EV, correlation penalty, game /
     sport / capacity / budget caps, existing ACTUAL exposure, opportunity
     cost, non-positive or unmeasured EV funded at 0 -- with reasons
"""
from __future__ import annotations

import pytest

from sportsassets.intel import allocator as AL
from sportsassets.intel import sizing as SZ

CAL = {"half_width": 0.03, "miscalibration": 0.01, "n": 300,
       "unmeasured": {}}


def _size(**kw):
    base = dict(p=0.62, cost=0.50, fee_pc=0.01, depth_qty=1000.0,
                cal_unc=CAL, exec_sd=0.01, exec_n=50, void_upper=0.05,
                same_game_open=1, drawdown_pct=5.0, regime="NORMAL")
    base.update(kw)
    return SZ.shadow_size(**base)


# ── §1 sizing ────────────────────────────────────────────────────────

def test_the_shadow_size_by_hand():
    s = _size()
    f = s["factors"]
    assert f["calibration_uncertainty"] == pytest.approx(0.04)
    assert f["p_conservative"] == pytest.approx(0.58)
    assert f["cost_conservative"] == pytest.approx(0.52)
    assert f["net_edge_conservative"] == pytest.approx(0.06)
    assert f["kelly_fraction_of_full"] == pytest.approx(0.125)
    assert f["base_usd"] == pytest.approx(31.25)
    assert f["settlement_factor"] == pytest.approx(0.95)
    assert f["correlation_factor"] == pytest.approx(0.5)
    assert f["sample_size_factor"] == pytest.approx(1.0)
    assert f["drawdown_factor"] == pytest.approx(0.75)
    assert f["evidence_multiplier"] == pytest.approx(0.35625)
    assert s["shadow_usd"] == pytest.approx(11.1328, abs=1e-4)
    assert s["shadow_qty"] == pytest.approx(22.2656, abs=1e-4)
    assert s["binding_constraint"] == "KELLY_X_EVIDENCE"
    assert s["applied"] is False and s["unmeasured"] == {}


def test_the_caps_bind():
    s = _size(depth_qty=40.0, same_game_open=0, drawdown_pct=0.0,
              void_upper=0.0)
    assert s["binding_constraint"] == "LIQUIDITY"
    assert s["shadow_usd"] == pytest.approx(0.25 * 40 * 0.5)
    s = _size(p=0.95, depth_qty=1e6, same_game_open=0, drawdown_pct=0.0,
              void_upper=0.0)
    assert s["binding_constraint"] == "PER_POSITION_CAP"
    assert s["shadow_usd"] == pytest.approx(100.0)


def test_unmeasured_inputs_take_conservative_defaults_and_say_so():
    s = _size(cal_unc={"half_width": None, "n": 0,
                       "unmeasured": {"half_width": "NO_BAND"}},
              exec_sd=None, exec_n=0, void_upper=None, drawdown_pct=None,
              regime=None, depth_qty=None)
    um = s["unmeasured"]
    assert um["calibration_uncertainty"].startswith("NO_BAND")
    for k in ("execution_uncertainty", "settlement_confidence", "drawdown",
              "regime", "liquidity_cap"):
        assert k in um, k
    f = s["factors"]
    assert f["calibration_uncertainty"] == SZ.UNMEASURED_CAL_UNCERTAINTY
    assert f["sample_size_factor"] == pytest.approx(0.1)
    # 0.62 - 0.10 = 0.52 against 0.50 + 0.01 + 0.02 = 0.53: no edge left
    assert s["shadow_usd"] == 0.0
    assert s["binding_constraint"] == "NO_EDGE_AFTER_EVIDENCE_UNCERTAINTY"


def test_no_trade_regime_sizes_zero_and_missing_inputs_are_null():
    assert _size(regime="NO_TRADE")["shadow_usd"] == 0.0
    s = _size(p=None)
    assert s["shadow_usd"] is None
    assert s["unmeasured"]["shadow_usd"] == (
        "DECISION_LACKS_PROBABILITY_OR_COST")


def test_a_decision_row_sits_beside_paper_and_actual_size():
    dec = {"decision_id": "d1", "strategy": "S", "us_market_slug": "m",
           "p_pinnacle": 0.62, "limit_price": 0.56, "proposed_qty": 5000,
           "label": {"event_key": "G"},
           "economics": {"depth_within_limit": 5000.0,
                         "acquisition": {"vwap": 0.52, "fees_usd": 50.0}}}
    risk = {"exposure": {"game": [{"key": "G", "positions": 2}]},
            "equity": {"current_drawdown_pct": 0.0}}
    row = SZ.size_decision(dec, cal_report=None, exec_sd=None, exec_n=0,
                           risk_paper=risk, regime="NORMAL",
                           actual={"live_qty": 5, "why": None})
    assert row["paper_qty"] == 5000 and row["paper_usd"] == pytest.approx(
        2600.0)
    assert row["actual_qty"] == 5 and row["actual_usd"] == pytest.approx(2.6)
    assert row["factors"]["same_game_open_positions"] == 2
    assert row["applied"] is False and row["label"] == "SHADOW"
    none = SZ.size_decision(dec, cal_report=None, exec_sd=None, exec_n=0,
                            risk_paper=None, regime=None, actual=None)
    assert none["actual_qty"] is None
    assert none["unmeasured"]["actual_qty"] == (
        "NO_EXECUTION_INTENT_FOR_THIS_DECISION")
    assert none["unmeasured"]["correlation"] == "NO_RISK_REPORT_THIS_CYCLE"


# ── §2 the allocator ─────────────────────────────────────────────────

def _c(cid, ev, cap, game, sport="s", kind="NEW_DECISION"):
    return {"candidate_id": cid, "candidate_kind": kind, "ev": ev,
            "ev_why": None if ev is not None else "NO_PROBABILITY",
            "capacity_usd": cap, "game": game, "sport": sport,
            "calibration_uncertainty": 0.05, "unmeasured": {}}


def _run(cands, **kw):
    base = dict(game_open={}, actual_game_exposure={}, drawdown_factor=1.0,
                regime_factor=1.0)
    base.update(kw)
    res = AL.allocate(cands, **base)
    return res, {c["candidate_id"]: c for c in res["ranked"]}


def test_ranked_allocation_under_the_game_cap():
    res, by = _run([_c("A", 0.10, 50, "G1"), _c("B", 0.08, 300, "G1"),
                    _c("C", 0.05, 100, "G2"), _c("D", -0.01, 100, "G3"),
                    _c("E", None, 100, "G4")])
    assert [c["candidate_id"] for c in res["ranked"]] == [
        "A", "B", "C", "D", "E"]
    assert by["A"]["shadow_usd"] == 50 and by["A"]["binding_constraint"] == (
        "CAPACITY")
    assert by["B"]["shadow_usd"] == pytest.approx(200.0)
    assert by["B"]["binding_constraint"] == "GAME_CAP"
    assert by["C"]["shadow_usd"] == 100
    assert by["D"]["shadow_usd"] == 0.0
    assert by["D"]["binding_constraint"] == "NON_POSITIVE_RISK_ADJUSTED_EV"
    assert by["E"]["binding_constraint"] == "UNMEASURED_EV"
    assert any(r.startswith("NOT_FUNDED:NO_PROBABILITY")
               for r in by["E"]["reasons"])
    assert res["allocated_usd"] == pytest.approx(350.0)
    assert by["A"]["shadow_weight"] == pytest.approx(0.05)
    assert by["A"]["opportunity_cost_per_dollar"] == 0.0
    assert by["D"]["opportunity_cost_per_dollar"] is None


def test_budget_regime_existing_exposure_and_opportunity_cost():
    res, by = _run([_c("A", 0.10, 100, "G1"), _c("C", 0.05, 400, "G2"),
                    _c("F", 0.02, 10, "G3")],
                   actual_game_exposure={"G2": 200.0}, regime_factor=0.25)
    assert res["budget_usd"] == pytest.approx(250.0)
    assert by["C"]["shadow_usd"] == pytest.approx(50.0)   # 250 - 200 actual
    assert by["C"]["binding_constraint"] == "GAME_CAP"
    assert any("EXISTING_ACTUAL_EXPOSURE" in r for r in by["C"]["reasons"])
    assert by["F"]["shadow_usd"] == pytest.approx(10.0)
    res, by = _run([_c("A", 0.10, 100, "G1"), _c("F", 0.02, 10, "G3")],
                   regime_factor=0.1)
    assert by["A"]["shadow_usd"] == pytest.approx(100.0)
    assert by["F"]["shadow_usd"] == 0.0
    assert by["F"]["binding_constraint"] == "SLEEVE_BUDGET"
    assert by["A"]["opportunity_cost_per_dollar"] == pytest.approx(0.02)


def test_correlation_penalty_and_sport_cap():
    res, by = _run([_c("A", 0.05, 100, "G1"), _c("B", 0.04, 100, "G2")],
                   game_open={"G1": 3})
    assert by["A"]["score"] == pytest.approx(0.02)
    assert [c["candidate_id"] for c in res["ranked"]] == ["B", "A"]
    assert any("CORRELATED_WITH_3" in r for r in by["A"]["reasons"])
    pos = _c("P", 0.05, 100, "G1", kind="OPEN_POSITION")
    _, byp = _run([pos], game_open={"G1": 1})
    assert byp["P"]["same_game_open"] == 0          # not penalised for itself
    cands = [_c("S%d" % i, 0.1, 250, "G%d" % i) for i in range(4)]
    _, by = _run(cands)
    assert sum(c["shadow_usd"] for c in by.values()) == pytest.approx(600.0)
    assert by["S2"]["binding_constraint"] == "SPORT_CAP"


def test_candidates_from_sizing_and_open_positions():
    now = 10_000.0
    sizing = [{"decision_id": "d1", "us_market_slug": "m1",
               "shadow_usd": 12.0, "unmeasured": {},
               "factors": {"net_ev_per_dollar_conservative": 0.08,
                           "calibration_uncertainty": 0.04,
                           "caps_usd": {"LIQUIDITY": 30.0}}},
              {"decision_id": "old", "us_market_slug": "m0",
               "shadow_usd": 5.0, "unmeasured": {}, "factors": {}}]
    decs = {"d1": {"decided_at": now - 60, "label": {"event_key": "G1"}},
            "old": {"decided_at": now - 3 * 86400}}
    opens = [{"group_id": "g1", "us_market_slug": "m2", "game": "G2",
              "sport": "baseball", "exposure_usd": 2500.0},
             {"group_id": "g2", "us_market_slug": "m3", "game": "G3",
              "sport": "baseball", "exposure_usd": 100.0}]
    measures = {"g1": {"p": 0.70, "best_exit_at_review": 0.50}}
    cands = AL.candidates_from(sizing, decs, opens, measures,
                               cal_report=None, now=now)
    by = {c["candidate_id"]: c for c in cands}
    assert set(by) == {"decision:d1", "position:g1", "position:g2"}
    assert by["decision:d1"]["ev"] == 0.08
    assert by["decision:d1"]["game"] == "G1"
    # (0.70 - 0.10 default uncertainty - 0.50) / 0.50
    assert by["position:g1"]["ev"] == pytest.approx(0.2)
    assert by["position:g1"]["capacity_usd"] == pytest.approx(5.0)
    assert "calibration_uncertainty" in by["position:g1"]["unmeasured"]
    assert by["position:g2"]["ev"] is None
    assert by["position:g2"]["ev_why"] == (
        "NO_CURRENT_PROBABILITY_FOR_THE_HELD_CONTRACT")


def test_a_shared_team_is_correlation_too():
    a = dict(_c("A", 0.05, 100, "G1"), team="Cubs")
    b = dict(_c("B", 0.05, 100, "G2"), team="Mets")
    _, by = _run([a, b], team_open={"Cubs": 2})
    assert by["A"]["same_team_open"] == 2
    assert by["A"]["score"] == pytest.approx(0.03)
    assert by["B"]["score"] == pytest.approx(0.05)
    assert any("SAME_TEAM" in r for r in by["A"]["reasons"])
