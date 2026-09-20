"""§10. Distributions, not BUY/SELL -- and no policy until evidence."""

import pytest

from sportsassets import bettor_applicability as applic
from sportsassets import bettor_exit_dataset as xd
from sportsassets import bettor_exit_ml as ml


def _state(identified=("EXIT_BID", "FEE")):
    return xd.state_at_t0(
        t0_timestamp="2026-09-20T18:00:00Z", market_id="m1",
        inventory_state="YES_RESIDUAL", residual_leg="YES",
        residual_qty="10", residual_basis="0.48", matched_qty="40",
        capital_in_residual="4.80",
        book={"bid": "0.47", "ask": "0.51", "spread": "0.04",
              "availableDepth": "900"},
        complement={"bid": "0.49", "ask": "0.53",
                    "identityStatus": "IDENTIFIED"},
        time_to_event="3600", age_of_residual_s="120",
        identified=identified)


# ── no policy, and it cannot be talked into one ──────────────────────

def test_no_exit_policy_is_trained_or_active():
    assert ml.POLICY_STATUS == "POLICY_NOT_TRAINED"
    assert ml.POLICY_VERSION == ml.NOT_IDENTIFIED
    assert ml.POLICY_ACTIVE is False
    assert ml.TRAINING_ROWS == 0


def test_promotion_is_refused_on_every_named_condition():
    p = ml.promote()
    assert p["promoted"] is False
    assert set(p["blockers"]) == set(ml.PROMOTION_REQUIRES)


def test_rows_alone_do_not_promote_a_policy():
    p = ml.promote(rows=10_000,
                   actions_with_outcomes=xd.LABELLED_ACTIONS)
    assert p["promoted"] is False
    assert "OUT_OF_SAMPLE_EVALUATION" in p["blockers"]
    assert "LEAK_CHECK_CLEAN" in p["blockers"]
    assert "fitted on a leaked dataset" in p["oneDatasetDoesNotMakeAPolicy"]


def test_an_action_with_no_outcomes_blocks_promotion_and_is_named():
    p = ml.promote(rows=10, out_of_sample=True, leak_check="CLEAN",
                   selection_measured=True, owner_approval_token="X",
                   actions_with_outcomes=("DIRECT_EXIT", "HEDGE"))
    assert p["promoted"] is False
    assert "HOLD_5S" in p["actionsWithoutOutcomes"]
    assert "DIRECT_EXIT" not in p["actionsWithoutOutcomes"]


def test_a_fully_satisfied_promotion_is_still_not_activation():
    p = ml.promote(rows=10, out_of_sample=True, leak_check="CLEAN",
                   selection_measured=True, owner_approval_token="X",
                   actions_with_outcomes=xd.LABELLED_ACTIONS)
    assert p["promoted"] is True
    assert p["POLICY_ACTIVE"] is False
    assert "separate owner decision" in p["promotionIsNotActivation"]


# ── §10: the twenty-eight inputs ─────────────────────────────────────

def test_every_declared_input_is_the_directives():
    for name in ("LEG", "BASIS", "QUANTITY", "MATCHED_QTY", "RESIDUAL_QTY",
                 "RESIDUAL_AGE", "BID", "ASK", "SPREAD", "DEPTH",
                 "IMBALANCE", "RECENT_MOVE", "VOLATILITY",
                 "COMPLEMENT_PRICE", "PAIR_BASIS", "PAIR_MARGIN",
                 "COMPLETION_HAZARD", "TIME_TO_COMPLETION", "FV_BETTOR",
                 "FV_UNCERTAINTY", "P_FILL", "ADVERSE_SELECTION",
                 "TOXICITY", "TIME_TO_EVENT", "MARKET_STATE",
                 "CAPITAL_HOURS", "EVENT_EXPOSURE", "CORRELATED_EXPOSURE"):
        assert name in ml.INPUTS, name
    assert len(ml.INPUTS) == 28


@pytest.mark.parametrize("name", ml.INPUTS)
def test_every_input_appears_in_the_vector(name):
    assert name in ml.features(_state())["inputs"]


def test_inputs_come_from_the_sealed_t0_state():
    v = ml.features(_state())["inputs"]
    assert v["LEG"] == "YES"
    assert v["BID"] == "0.47"
    assert v["SPREAD"] == "0.04"
    assert v["RESIDUAL_AGE"] == "120"


def test_a_missing_input_is_not_imputed():
    f = ml.features(_state())
    assert f["inputs"]["VOLATILITY"] == ml.NOT_IDENTIFIED
    assert "VOLATILITY" in f["inputsMissing"]
    assert "dressed as data" in f["noImputation"]
    for name in f["inputsMissing"]:
        assert f["inputs"][name] == ml.NOT_IDENTIFIED
        assert f["inputs"][name] != "0"


def test_structurally_unavailable_inputs_are_still_named_as_inputs():
    """A feature list that quietly drops what is missing is how a model
    ends up trained on whatever happened to be easy."""
    for name in ("P_FILL", "FV_BETTOR", "FV_UNCERTAINTY"):
        assert name in ml.INPUTS
        assert name in ml.STRUCTURALLY_UNAVAILABLE
    f = ml.features(_state())
    assert "P_FILL" in f["structurallyUnavailable"]
    assert "quietly drops" in f["featureListKeepsItsGaps"]


def test_completion_hazard_is_marked_as_a_prior_not_p_fill():
    why = ml.STRUCTURALLY_UNAVAILABLE["COMPLETION_HAZARD"]
    assert "STRUCTURAL_COMPLETION_PRIOR" in why
    assert "NOT BETTOR P_FILL" in why


def test_a_supplied_input_overrides_the_state():
    f = ml.features(_state(), VOLATILITY="0.02", IMBALANCE="-0.3")
    assert f["inputs"]["VOLATILITY"] == "0.02"
    assert "VOLATILITY" not in f["inputsMissing"]


# ── §10: the outputs are a distribution, never a verdict ─────────────

def test_the_six_outputs_are_the_directives():
    assert ml.OUTPUTS == ("EXPECTED_NET_DOLLARS", "LOWER_CONFIDENCE_VALUE",
                          "UPPER_CONFIDENCE_VALUE", "DOWNSIDE_TAIL",
                          "EXPECTED_CAPITAL_RELEASE",
                          "EXPECTED_CAPITAL_HOURS")


def test_every_applicable_action_returns_all_six_outputs():
    p = ml.predict(_state())
    d = p["predictions"]["DIRECT_EXIT"]
    for o in ml.OUTPUTS:
        assert o in d, o


def test_nothing_returns_a_single_recommended_action():
    p = ml.predict(_state())
    for key in ("recommendation", "recommendedAction", "decision",
                "BUY", "SELL", "bestAction"):
        assert key not in p
    assert "no code path that returns a single recommended action" in \
        p["isNotAVerdict"]


def test_the_mean_alone_is_not_the_answer():
    assert "same mean and different tails" in ml.NOT_A_VERDICT
    assert "DOWNSIDE_TAIL" in ml.OUTPUTS


def test_ranking_belongs_to_the_caller():
    p = ml.predict(_state())
    assert "risk gate and the allocator" in p["rankingIsTheCallersJob"]


# ── an untrained policy returns absence, not numbers ─────────────────

def test_an_untrained_policy_returns_named_absence_for_every_output():
    d = ml.predict(_state())["predictions"]["DIRECT_EXIT"]
    assert d["PREDICTION_STATUS"] == "POLICY_NOT_TRAINED"
    for o in ml.OUTPUTS:
        assert d[o] == ml.NOT_IDENTIFIED
        assert d[o] != "0"
    assert "0 rows" in d["why"]


def test_a_non_applicable_action_gets_no_distribution_at_all():
    """A zero-valued distribution would rank. This one carries none."""
    p = ml.predict(_state())
    hedge = p["predictions"]["HEDGE"]
    assert hedge["PREDICTION_STATUS"] == applic.NOT_EVALUATED
    for o in ml.OUTPUTS:
        assert o not in hedge


def test_every_labelled_action_gets_a_row():
    p = ml.predict(_state())
    assert set(p["predictions"]) == set(xd.LABELLED_ACTIONS)


def test_no_state_means_no_applicability_and_no_numbers():
    p = ml.predict()
    for label, d in p["predictions"].items():
        assert d["AVAILABILITY_STATUS_AT_T0"] == ml.NOT_IDENTIFIED
        for o in ml.OUTPUTS:
            assert d[o] == ml.NOT_IDENTIFIED


def test_the_interface_exists_before_the_model_on_purpose():
    assert "load-bearing everywhere downstream" in \
        ml.WHY_INTERFACE_BEFORE_MODEL


def test_describe_carries_the_promotion_refusal():
    d = ml.describe()
    assert d["promotionRefusal"]["promoted"] is False
    assert d["POLICY_ACTIVE"] is False
