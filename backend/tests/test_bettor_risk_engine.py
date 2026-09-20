"""The risk engine: what it blocks, what it must never block, and why."""

import pytest

from sportsassets import bettor_ev_actions as acts
from sportsassets import bettor_risk_engine as risk


@pytest.fixture
def declared_limit():
    """Temporarily predeclare one rail, then put it back."""
    rail = "MAX_MARKET_EXPOSURE"
    original = risk.RAILS[rail]["limit"]
    risk.RAILS[rail]["limit"] = "100"
    yield rail
    risk.RAILS[rail]["limit"] = original


# ── the asymmetry, which is the whole design ─────────────────────────

def test_unknown_risk_state_blocks_increasing_exposure():
    r = risk.evaluate("TAKE_YES")
    assert r["direction"] == "EXPOSURE_INCREASING"
    assert r["permitted"] is False
    assert r["railsNotPassed"], "no rail reported as failing"


def test_unknown_risk_state_never_blocks_reducing_exposure():
    """Blocking reduction on unmeasurable risk would trap the book."""
    r = risk.evaluate("DIRECT_EXIT")
    assert r["direction"] == "EXPOSURE_REDUCING"
    assert r["permitted"] is True
    assert "most needs reducing" in r["why"]


def test_not_being_blocked_is_not_a_reason_to_act():
    r = risk.evaluate("DIRECT_EXIT")
    assert "not a reason to do it" in r["stillSubjectTo"]


def test_neutral_actions_are_not_blocked():
    for action in ("HOLD", "NO_TRADE", "WAIT_REQUOTE"):
        r = risk.evaluate(action)
        assert r["direction"] == "EXPOSURE_NEUTRAL"
        assert r["permitted"] is True


# ── not-evaluable is not a pass ──────────────────────────────────────

def test_a_rail_without_a_limit_is_not_evaluable(declared_limit):
    assert risk.evaluate_rail("MAX_EVENT_EXPOSURE", "5")["verdict"] == \
        risk.NOT_EVALUABLE
    assert risk.evaluate_rail("MAX_EVENT_EXPOSURE", "5")["limit"] == \
        risk.NOT_PREDECLARED


def test_a_rail_without_a_measurement_is_not_evaluable(declared_limit):
    assert risk.evaluate_rail(declared_limit, None)["verdict"] == \
        risk.NOT_EVALUABLE


def test_a_declared_rail_passes_and_blocks_on_the_number(declared_limit):
    assert risk.evaluate_rail(declared_limit, "50")["verdict"] == risk.PASS
    assert risk.evaluate_rail(declared_limit, "150")["verdict"] == risk.BLOCK
    assert risk.evaluate_rail(declared_limit, "100")["verdict"] == risk.PASS


def test_the_three_verdicts_stay_distinct():
    assert len(set(risk.VERDICTS)) == 3
    assert "reports green while governing nothing" in \
        risk.NOT_EVALUABLE_IS_NOT_PASS


def test_one_unevaluable_rail_is_enough_to_block(declared_limit):
    """Every rail must pass; a single unknown is fatal to an increase."""
    observed = {name: "1" for name in risk.RAILS}
    state = {gate: True for gate in risk.STATE_GATES}
    r = risk.evaluate("TAKE_YES", observed=observed, state=state)
    # The other six rails still have no predeclared limit.
    assert r["permitted"] is False
    assert declared_limit not in r["railsNotPassed"]
    assert len(r["railsNotPassed"]) == len(risk.RAILS) - 1


# ── no limit is invented ─────────────────────────────────────────────

def test_no_rail_has_an_invented_limit():
    """An invented number would govern nothing while appearing to."""
    assert risk.report()["railsWithPredeclaredLimits"] == []
    for name, spec in risk.RAILS.items():
        assert spec["limit"] == risk.NOT_PREDECLARED, name


def test_the_refusal_is_separate_from_mirror_live():
    rep = risk.report()
    assert rep["anyExposureIncreaseCurrentlyPermitted"] is False
    assert "does not lift when the order path exists" in rep["why"]


# ── §7: two axes, because a hedge moves them opposite ────────────────

def test_a_hedge_raises_gross_and_lowers_directional():
    h = risk.exposure_effect("HEDGE")
    assert h["grossExposure"] == risk.INCREASE
    assert h["directionalExposure"] == risk.DECREASE
    assert h["increasesExposure"] is True
    assert h["reducesDirectionalRisk"] is True


def test_a_hedge_is_still_gated_despite_reducing_outcome_risk():
    """It occupies capital on a second leg, so the capital rails bind."""
    r = risk.evaluate("HEDGE")
    assert r["direction"] == "EXPOSURE_INCREASING"
    assert r["permitted"] is False


def test_completing_a_pair_is_treated_the_same_way():
    for action in ("TAKE_COMPLEMENT", "COMPLETE_PAIR", "POST_COMPLEMENT"):
        e = risk.exposure_effect(action)
        assert e["grossExposure"] == risk.INCREASE, action
        assert e["directionalExposure"] == risk.DECREASE, action


def test_a_direct_exit_reduces_both_axes():
    e = risk.exposure_effect("DIRECT_EXIT")
    assert e["grossExposure"] == risk.DECREASE
    assert e["directionalExposure"] == risk.DECREASE


def test_every_canonical_action_has_an_exposure_effect():
    for a in acts.ACTIONS:
        e = risk.exposure_effect(a)
        assert e["grossExposure"] != risk.NOT_IDENTIFIED, a
        assert e["directionalExposure"] != risk.NOT_IDENTIFIED, a


def test_the_two_axes_are_explained_where_a_reader_will_find_them():
    assert "misclassify it" in risk.TWO_AXES


# ── the state gates ──────────────────────────────────────────────────

def test_the_required_state_gates_exist():
    for gate in ("STALE_DATA", "UNRESOLVED_SETTLEMENT_SEMANTICS",
                 "OUT_OF_DISTRIBUTION", "MODEL_TRUST_DRIFT"):
        assert gate in risk.STATE_GATES


def test_an_unknown_gate_state_is_not_clear():
    r = risk.evaluate("TAKE_YES")
    gates = {g["gate"]: g for g in r["stateGates"]}
    assert gates["STALE_DATA"]["verdict"] == risk.NOT_EVALUABLE
    assert gates["STALE_DATA"]["clear"] is None


def test_an_explicitly_failed_gate_blocks(declared_limit):
    observed = {name: "1" for name in risk.RAILS}
    state = {gate: True for gate in risk.STATE_GATES}
    state["STALE_DATA"] = False
    r = risk.evaluate("TAKE_YES", observed=observed, state=state)
    assert "STALE_DATA" in r["gatesNotPassed"]
    assert r["permitted"] is False


def test_the_residual_rail_names_the_ferrari_failure():
    assert "Ferrari" in risk.RAILS["MAX_RESIDUAL_INVENTORY"]["why"]


# ── §18 wired, not merely built ──────────────────────────────────────

def _res_inventory():
    from sportsassets import bettor_inventory as binv
    return binv.inventory(
        [{"leg": "YES", "qty": "100", "price": "0.48"},
         {"leg": "NO", "qty": "60", "price": "0.49"}],
        identity_status="EXACT_ONE_TO_COMPLEMENT_BASKET")


def test_the_risk_gate_is_actually_consulted_by_the_bridge():
    """A risk module nobody calls is the failure this work began by fixing."""
    from sportsassets import bettor_ev_bridge as evb
    out = evb.evaluate({"bid": "0.48", "ask": "0.52", "mid": "0.50",
                        "readable": True}, fee="0.001",
                       inventory=_res_inventory())
    assert all("risk" in r for r in out["table"])
    # Every exposure-increasing action that is APPLICABLE here is
    # risk-blocked; inapplicable ones were never risk-evaluated at all.
    applicable = {r["action"] for r in out["table"]
                  if r["APPLICABILITY_STATUS"] == "APPLICABLE"}
    assert set(out["riskBlockedActions"]) >= (
        applicable & set(acts.EXPOSURE_INCREASING))
    assert "DIRECT_EXIT" in out["riskPermittedActions"]


def test_an_inapplicable_action_is_never_risk_permitted():
    """DIRECT_EXIT read permitted=true on a book holding nothing."""
    from sportsassets import bettor_ev_bridge as evb
    from sportsassets import bettor_inventory as binv
    flat = binv.inventory([], identity_status="EXACT_ONE_TO_COMPLEMENT_BASKET")
    out = evb.evaluate({"bid": "0.48", "ask": "0.52", "mid": "0.50",
                        "readable": True}, fee="0.001", inventory=flat)
    rows = {r["action"]: r for r in out["table"]}
    for action in ("DIRECT_EXIT", "HOLD", "MERGE", "COMPLETE_PAIR"):
        assert rows[action]["risk"]["permitted"] is False, action
        assert rows[action]["risk"]["RISK_STATUS"] == \
            "NOT_EVALUATED_NOT_APPLICABLE", action
        assert action not in out["riskPermittedActions"], action


def test_risk_and_economics_are_separate_verdicts():
    """An action can be risk-clear and economically unidentified."""
    from sportsassets import bettor_ev_bridge as evb
    out = evb.evaluate({"bid": "0.48", "ask": "0.52", "mid": "0.50",
                        "readable": True}, fee="0.001",
                       inventory=_res_inventory())
    rows = {r["action"]: r for r in out["table"]}
    merge = rows["MERGE"]
    assert merge["APPLICABILITY_STATUS"] == "APPLICABLE"
    assert merge["risk"]["permitted"] is True      # reduces gross exposure
    assert merge["status"] == evb.NOT_IDENTIFIED   # no venue mechanism


def test_the_two_modules_cannot_disagree_about_exposure():
    """They kept separate copies once and disagreed about COMPLETE_PAIR."""
    assert risk.EXPOSURE_EFFECT is acts.EXPOSURE_EFFECT
    assert "COMPLETE_PAIR" in acts.EXPOSURE_INCREASING
    derived = tuple(a for a in acts.CANONICAL_ACTIONS
                    if risk.exposure_effect(a)["increasesExposure"])
    assert derived == acts.EXPOSURE_INCREASING
