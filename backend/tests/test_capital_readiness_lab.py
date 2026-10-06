import math

from sportsassets.capital_readiness import adversarial_committee as V
from sportsassets.capital_readiness import agent_championship as A
from sportsassets.capital_readiness import alpha_decay as D
from sportsassets.capital_readiness import epistemic as E
from sportsassets.capital_readiness import portfolio_twin as P
from sportsassets.capital_readiness import readiness as R
from sportsassets.capital_readiness import scale_twin as S
from sportsassets.capital_readiness import shadow_court as C


def test_epistemic_is_fail_closed_and_can_only_shrink():
    weak = E.confidence(calibration_n=5, execution_n=100, brier=0.10,
                        calibration_slope=1.0, regime_novelty=1,
                        ood_score=1, data_quality=1)
    assert weak["confidence_factor"] == 0
    strong = E.confidence(calibration_n=300, execution_n=100, brier=0.01,
                          calibration_slope=1.0, regime_novelty=1,
                          ood_score=1, data_quality=1)
    assert 0 < strong["confidence_factor"] <= 1
    assert E.shrink(100, strong) <= 100


def test_shadow_court_always_includes_cash_and_uses_lower_bound():
    epi = {"confidence_factor": 1.0}
    court = C.judge(decision_id="d1", chosen="TAKER", alternatives=[
        {"name": "TAKER", "expected_net_usd": 10, "capital_usd": 100,
         "expected_hold_hours": 1, "uncertainty_sigma_usd": 20,
         "epistemic": epi},
        {"name": "MAKER", "expected_net_usd": 8, "capital_usd": 100,
         "expected_hold_hours": 1, "uncertainty_sigma_usd": 1,
         "epistemic": epi},
    ])
    assert court["shadow_winner"] == "MAKER"
    assert any(a["name"] == "CASH" for a in court["alternatives"])
    assert court["disagreement"] is True


def test_shadow_court_chooses_cash_when_no_positive_lower_bound():
    court = C.judge(decision_id="d2", chosen="TAKER", alternatives=[
        {"name": "TAKER", "expected_net_usd": 1, "capital_usd": 100,
         "expected_hold_hours": 1, "uncertainty_sigma_usd": 10,
         "epistemic": {"confidence_factor": 0.5}},
    ])
    assert court["shadow_winner"] == "CASH"


def test_agent_championship_requires_positive_ci_and_sample():
    rows = [{"agent": "Xavier", "management_alpha_usd": 2.0} for _ in range(25)]
    rep = A.evaluate(rows)
    assert rep["agents"]["XAVIER"]["economically_positive"] is True
    rows2 = [{"agent": "Derek", "discovery_alpha_usd": 1 if i % 2 else -1}
             for i in range(25)]
    rep2 = A.evaluate(rows2)
    assert rep2["agents"]["DEREK"]["economically_positive"] is False


def test_scale_twin_never_recommends_above_capacity():
    rep = S.evaluate(base_capital_usd=1000, base_expected_ev_usd=50,
                     capacity_usd=50000, edge_decay_per_1000_usd=0.0001,
                     uncertainty_sigma_usd=1)
    assert rep["recommended_capital_usd"] <= 50000
    above = [r for r in rep["rows"] if r["capital_usd"] > 50000]
    assert above and all(r["status"] == "ABOVE_MEASURED_CAPACITY" for r in above)


def test_readiness_hard_red_forces_zero_capital():
    gates = {g: True for g in R.HARD_GATES}
    gates["freshness_gte_95"] = False
    rep = R.score(gates=gates,
                  scale_twin={"recommended_capital_usd": 100000})
    assert rep["readiness_status"] == "RED"
    assert rep["recommended_capital_usd"] == 0
    assert "freshness_gte_95" in rep["blocking_gates"]


def test_readiness_green_does_not_grant_live_authority():
    gates = {g: True for g in R.HARD_GATES}
    rep = R.score(gates=gates,
                  scale_twin={"recommended_capital_usd": 25000})
    assert rep["readiness_status"] == "GREEN"
    assert rep["recommended_capital_usd"] == 25000
    assert rep["live_authority_granted"] is False
    assert rep["owner_promotion_required"] is True


def test_court_outcome_scores_regret_without_using_oracle_as_authority():
    court = C.judge(decision_id="d3", chosen="MAKER", alternatives=[
        {"name": "MAKER", "expected_net_usd": 10, "capital_usd": 100,
         "expected_hold_hours": 1, "uncertainty_sigma_usd": 1,
         "epistemic": {"confidence_factor": 1}},
        {"name": "TAKER", "expected_net_usd": 8, "capital_usd": 100,
         "expected_hold_hours": 1, "uncertainty_sigma_usd": 1,
         "epistemic": {"confidence_factor": 1}},
    ])
    s = C.score_outcome(court, {"MAKER": 5, "TAKER": 7, "CASH": 0})
    assert s["oracle_regret_usd"] == 2
    assert s["oracle_is_diagnostic_only"] is True


def test_alpha_decay_refuses_small_samples_and_measures_decay():
    assert D.estimate([{"signal_age_s": 1, "realized_alpha_per_dollar": .1}])["status"] == "UNAVAILABLE"
    rows=[]
    for i in range(30):
        rows.append({"signal_age_s": i*60, "realized_alpha_per_dollar": .10 if i < 15 else .04})
    rep=D.estimate(rows)
    assert rep["status"] == "OK" and rep["decay_detected"] is True
    assert rep["half_life_s"] > 0


def test_portfolio_twin_reports_tail_loss():
    rep=P.evaluate([{"portfolio_pnl_usd": x} for x in (-100,-50,0,20,30,40,50,60,70,80,90,100,110,120,130,140,150,160,170,180)])
    assert rep["status"] == "OK"
    assert rep["var_loss_usd"] >= 0
    assert rep["cvar_loss_usd"] >= rep["var_loss_usd"]


def test_adversarial_committee_only_reduces_confidence():
    clean=V.review({})
    assert clean["veto"] is False and clean["confidence_multiplier"] == 1.0
    warn=V.review({"thin_sample": True})
    assert warn["veto"] is False and 0 <= warn["confidence_multiplier"] < 1
    bad=V.review({"settlement_ambiguous": True})
    assert bad["veto"] is True and bad["confidence_multiplier"] == 0
