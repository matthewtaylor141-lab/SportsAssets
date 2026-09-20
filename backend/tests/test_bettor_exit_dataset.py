"""§9. The exit learning dataset: what was knowable, kept from what happened."""

import pytest

from sportsassets import bettor_applicability as applic
from sportsassets import bettor_exit_dataset as xd


IDENTIFIED = ("COMPLEMENT_IDENTITY", "COMPLEMENT_ASK", "FEE", "EXIT_BID")


def _state(inventory_state="YES_RESIDUAL", identified=IDENTIFIED):
    return xd.state_at_t0(
        t0_timestamp="2026-09-20T18:00:00Z", market_id="m1",
        inventory_state=inventory_state, residual_leg="YES",
        residual_qty="10", residual_basis="0.48", matched_qty="40",
        capital_in_residual="4.80",
        book={"bid": "0.47", "ask": "0.51", "spread": "0.04"},
        complement={"bid": "0.49", "ask": "0.53",
                    "identityStatus": "IDENTIFIED"},
        time_to_event="3600", age_of_residual_s="120",
        identified=identified)


# ── §9: the labelled action set ──────────────────────────────────────

def test_the_labelled_actions_are_the_directives_ten():
    assert xd.LABELLED_ACTIONS == (
        "HOLD_5S", "HOLD_15S", "HOLD_30S", "HOLD_60S",
        "POST_COMPLEMENT", "TAKE_COMPLEMENT", "DIRECT_EXIT", "HEDGE",
        "COMPLETE_PAIR_AND_MERGE", "HOLD_TO_SETTLEMENT")


def test_every_labelled_action_has_an_availability_verdict_at_t0():
    s = _state()
    assert set(s["ACTION_AVAILABLE_AT_T0"]) == set(xd.LABELLED_ACTIONS)


# ── the vocabulary gaps are exposed, not mapped ──────────────────────

@pytest.mark.parametrize("horizon", xd.HOLD_HORIZONS)
def test_a_hold_horizon_is_not_canonical_hold(horizon):
    gap = xd.VOCABULARY_GAPS[horizon]
    assert gap["canonical"] == "HOLD"
    assert gap["relationship"] == "HORIZON_PARAMETERISED_NOT_EQUIVALENT"
    assert "carries no horizon" in gap["why"]


def test_complete_pair_and_merge_is_a_composite_of_two_actions():
    gap = xd.VOCABULARY_GAPS["COMPLETE_PAIR_AND_MERGE"]
    assert gap["canonical"] == ("COMPLETE_PAIR", "MERGE")
    assert "which half failed" in gap["why"]
    assert "independently" in gap["why"]


def test_the_composite_reports_both_halves_when_evaluable():
    s = _state(identified=IDENTIFIED + ("MATCHED_QTY", "MERGE_MECHANISM"))
    v = s["ACTION_AVAILABLE_AT_T0"]["COMPLETE_PAIR_AND_MERGE"]
    assert v["AVAILABILITY_STATUS"] == xd.AVAILABLE
    assert set(v["halves"]) == {"COMPLETE_PAIR", "MERGE"}


def test_the_gaps_are_declared_as_gaps():
    assert "opposite of what a vocabulary table is for" in \
        xd.GAPS_ARE_EXPOSED_NOT_MAPPED
    v = _state()["ACTION_AVAILABLE_AT_T0"]["HOLD_5S"]
    assert v["applicabilityGate"] == "HOLD"
    assert "vocabularyGap" in v


# ── three statuses, never two ────────────────────────────────────────

def test_applicable_but_not_evaluable_is_its_own_status():
    """HOLD_TO_SETTLEMENT is possible whenever we own a leg, and its
    economics are not identified. That is not a zero and not a loss."""
    v = _state()["ACTION_AVAILABLE_AT_T0"]["HOLD_TO_SETTLEMENT"]
    assert v["AVAILABILITY_STATUS"] == xd.NOT_EVALUABLE
    assert v["APPLICABILITY_STATUS"] == applic.APPLICABLE
    assert v["preconditionsMissing"] == ["SETTLEMENT_SEMANTICS"]
    assert "not a zero" in v["whyNotZero"].lower() or \
        "zero" in v["whyNotZero"]


def test_not_applicable_is_distinct_from_not_evaluable():
    """From FLAT there is no residual, so residual management actions
    cannot exist at all -- a different fact from missing economics."""
    s = _state(inventory_state="FLAT")
    v = s["ACTION_AVAILABLE_AT_T0"]["DIRECT_EXIT"]
    assert v["AVAILABILITY_STATUS"] == xd.NOT_APPLICABLE
    assert "preconditionsMissing" not in v


def test_an_unknown_state_is_not_forced_into_flat():
    s = _state(inventory_state=applic.STATE_NOT_IDENTIFIED)
    for v in s["ACTION_AVAILABLE_AT_T0"].values():
        assert v["AVAILABILITY_STATUS"] != xd.AVAILABLE


def test_an_evaluable_action_needs_every_precondition():
    s = _state(identified=("EXIT_BID",))
    v = s["ACTION_AVAILABLE_AT_T0"]["DIRECT_EXIT"]
    assert v["AVAILABILITY_STATUS"] == xd.NOT_EVALUABLE
    assert v["preconditionsMissing"] == ["FEE"]


def test_all_three_statuses_carry_their_meanings():
    assert set(xd.STATUS_MEANINGS) == {
        xd.AVAILABLE, xd.NOT_EVALUABLE, xd.NOT_APPLICABLE}


# ── the T0 record is sealed before the answer exists ─────────────────

def test_the_t0_record_contains_no_outcome_field():
    s = _state()
    for f in xd.OUTCOME_FIELDS:
        assert f not in s, f


@pytest.mark.parametrize("field", xd.AT_T0_FIELDS)
def test_every_declared_t0_field_is_present(field):
    assert field in _state()


def test_the_t0_record_is_sealed():
    a = _state()
    b = xd.state_at_t0(t0_timestamp="2026-09-20T18:00:00Z", market_id="m1",
                       inventory_state="YES_RESIDUAL", residual_leg="YES",
                       residual_qty="11", residual_basis="0.48",
                       book={"bid": "0.47", "ask": "0.51"})
    assert a["STATE_SHA_AT_T0"] != b["STATE_SHA_AT_T0"]


def test_the_outcome_row_is_keyed_to_the_sealed_state():
    s = _state()
    r = xd.label_action(s, "DIRECT_EXIT", fields_read=("BID_AT_T0",),
                        counterfactual_pnl="-0.010")
    assert r["STATE_SHA_AT_T0"] == s["STATE_SHA_AT_T0"]


def test_availability_and_outcome_are_stored_apart():
    assert "hindsight" in xd.SEPARATION_RULE


# ── the leak check refuses rather than warns ─────────────────────────

def test_reading_an_outcome_field_refuses_the_label():
    s = _state()
    r = xd.label_action(s, "DIRECT_EXIT",
                        fields_read=("BID_AT_T0", "REALISED_BOOK_PATH"),
                        counterfactual_pnl="0.500")
    assert r["refused"] == "T0_LEAK"
    assert r["COUNTERFACTUAL_OUTCOME_LATER"] == xd.NOT_IDENTIFIED
    assert r["leakCheck"]["fieldsOutsideT0"] == ["REALISED_BOOK_PATH"]
    assert "COUNTERFACTUAL_PNL" not in r


def test_reading_any_undeclared_field_refuses_too():
    """Not only outcome fields: anything outside the T0 set."""
    r = xd.label_action(_state(), "DIRECT_EXIT",
                        fields_read=("BID_AT_T0", "SOME_OTHER_SIGNAL"))
    assert r["refused"] == "T0_LEAK"
    assert r["leakCheck"]["fieldsOutsideT0"] == ["SOME_OTHER_SIGNAL"]


def test_a_clean_read_passes():
    lc = xd.leak_check(("BID_AT_T0", "ASK_AT_T0", "RESIDUAL_QTY"))
    assert lc["LEAK_CHECK"] == "CLEAN"
    assert lc["fieldsOutsideT0"] == []


def test_the_leak_rule_refuses_rather_than_warns():
    assert "a refusal, not a warning" in xd.LEAK_RULE


# ── "where each action was actually evaluable" ───────────────────────

def test_a_non_evaluable_action_gets_no_number():
    r = xd.label_action(_state(), "HOLD_TO_SETTLEMENT",
                        counterfactual_pnl="0.050")
    assert r["refused"] == "NOT_AVAILABLE_AND_EVALUABLE_AT_T0"
    assert r["COUNTERFACTUAL_OUTCOME_LATER"] == xd.NOT_IDENTIFIED
    assert "COUNTERFACTUAL_PNL" not in r


def test_a_non_applicable_action_gets_no_number_either():
    r = xd.label_action(_state(inventory_state="FLAT"), "DIRECT_EXIT",
                        counterfactual_pnl="0.050")
    assert r["refused"] == "NOT_AVAILABLE_AND_EVALUABLE_AT_T0"
    assert r["AVAILABILITY_STATUS_AT_T0"] == xd.NOT_APPLICABLE


def test_an_evaluable_action_is_labelled():
    r = xd.label_action(_state(), "DIRECT_EXIT",
                        fields_read=("BID_AT_T0", "RESIDUAL_QTY"),
                        counterfactual_pnl="-0.010",
                        counterfactual_capital_hours="0.16",
                        counterfactual_residual_remaining="0",
                        outcome_timestamp="2026-09-20T18:00:30Z")
    assert r["COUNTERFACTUAL_OUTCOME_LATER"] == "APPENDED"
    assert r["COUNTERFACTUAL_PNL"] == "-0.010"
    assert r["COUNTERFACTUAL_RESIDUAL_REMAINING"] == "0"


def test_an_unknown_label_is_refused_rather_than_scored():
    r = xd.label_action(_state(), "SELL_EVERYTHING", counterfactual_pnl="1")
    assert r["refused"] == "UNKNOWN_LABEL"
    assert "COUNTERFACTUAL_PNL" not in r


def test_the_evaluable_set_buckets_every_label():
    e = xd.evaluable_set(_state())
    total = sum(len(v) for v in e["byStatus"].values())
    assert total == len(xd.LABELLED_ACTIONS)
    assert "DIRECT_EXIT" in e["EVALUABLE"]
    assert "HOLD_TO_SETTLEMENT" not in e["EVALUABLE"]


# ── the dataset creates nothing ──────────────────────────────────────

def test_every_state_is_prospective_and_says_so():
    s = _state()
    assert "No order was placed" in s["prospectiveNotReal"]
    assert "mandate is not frozen" in s["prospectiveNotReal"]
