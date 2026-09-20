"""§12. A fill label is not a permission to create inventory."""

import pytest

from sportsassets import bettor_ev_actions as acts
from sportsassets import bettor_shadow_mandate as sm


# ── the gate is shut ─────────────────────────────────────────────────

def test_the_mandate_is_proposed_not_frozen_and_not_active():
    assert sm.MANDATE_STATUS == \
        "PROPOSED_AWAITING_EVIDENCE_AND_OWNER_APPROVAL"
    assert sm.MANDATE_FROZEN is False
    assert sm.MANDATE_ACTIVE is False


def test_no_shadow_position_and_no_real_activity():
    p = sm.proposal()
    assert p["BETTOR_EV_SHADOW_POSITIONS"] == 0
    assert p["BETTOR_EV_REAL_ORDER_ACTIVITY"] == "NONE"
    assert p["BETTOR_EV_REAL_CAPITAL_AT_RISK"] == 0


def test_a_supported_fill_label_does_not_create_inventory():
    """THE §12 REQUIREMENT AS A CODE PATH. The strongest label BETTOR
    can produce still reaches a refusal."""
    r = sm.may_create_inventory(label="COUNTERFACTUAL_FILL_SUPPORTED")
    assert r["mayCreate"] is False
    assert "MANDATE_NOT_FROZEN" in r["blockers"]
    assert "MANDATE_NOT_ACTIVE" in r["blockers"]
    assert "says nothing about whether we wanted the quote" in \
        r["whyALabelIsNotAPermission"]


@pytest.mark.parametrize("label", [
    "COUNTERFACTUAL_NO_FILL_SUPPORTED",
    "COUNTERFACTUAL_FILL_NOT_IDENTIFIED",
    None,
])
def test_no_other_label_creates_inventory_either(label):
    assert sm.may_create_inventory(label=label)["mayCreate"] is False


# ── §12: all ten parameters, and the one that is not resolved ────────

def test_all_ten_parameters_are_named():
    assert len(sm.REQUIRED_PARAMETERS) == 10
    for p in ("ACTIONS_THAT_MAY_CREATE_SHADOW_INVENTORY",
              "STANDARD_NOTIONAL", "MAX_MARKET_EXPOSURE",
              "MAX_EVENT_EXPOSURE", "MAX_RESIDUAL_INVENTORY",
              "MAX_CAPITAL_DEPLOYED", "MAX_CAPITAL_HOURS",
              "PAIRING_RULE", "EXIT_RULE", "NO_TRADE_RULE"):
        assert p in sm.REQUIRED_PARAMETERS


def test_every_required_parameter_has_a_proposal():
    assert sm.proposal()["parametersAbsent"] == []


def test_the_exit_rule_is_unresolved_and_says_so():
    assert sm.proposal()["parametersPresentButUnresolved"] == ["EXIT_RULE"]
    assert sm.PROPOSED["EXIT_RULE"]["rule"].startswith("NOT_IDENTIFIED")
    assert "should not be created" in sm.WHY_EXIT_RULE_BLOCKS_THE_FREEZE


# ── the freeze fails closed, and each refusal is named ───────────────

def test_a_freeze_without_owner_approval_is_refused():
    f = sm.freeze()
    assert f["frozen"] is False
    assert "OWNER_APPROVAL_ABSENT" in f["blockers"]


def test_owner_approval_alone_does_not_freeze_an_unresolved_mandate():
    f = sm.freeze(owner_approval_token="APPROVED")
    assert f["frozen"] is False
    assert "OWNER_APPROVAL_ABSENT" not in f["blockers"]
    assert "PARAMETERS_UNRESOLVED" in f["blockers"]
    assert f["parametersUnresolved"] == ["EXIT_RULE"]


# ── §6: evidence is a gate approval cannot satisfy ───────────────────

def test_the_engine_has_measured_nothing_and_the_gate_says_so():
    e = sm.evidence_status()
    assert e["EVIDENCE_SUFFICIENT"] is False
    assert set(e["missing"]) == set(sm.EVIDENCE_REQUIRED)
    assert e["observed"]["P_FILL_DATASET_ROWS"] == 0
    assert e["observed"]["EXIT_LEARNING_DATASET_ROWS"] == 0


def test_an_approval_token_cannot_satisfy_the_evidence_gate():
    """THE §6 REQUIREMENT. Approval is a judgement about a system, and
    with nothing measured there is no system to judge."""
    f = sm.freeze(owner_approval_token="APPROVED")
    assert "EVIDENCE_INSUFFICIENT" in f["blockers"]
    assert "cannot be satisfied by an approval token" in \
        f["whyEvidenceIsASeparateGate"]


def test_evidence_alone_cannot_freeze_it_either():
    """Both gates clear on their own terms or neither does."""
    full = sm.evidence_status(p_fill_rows=500, p_fill_positives=20,
                              p_fill_negatives=15, exit_rows=40,
                              maker_actions_priced=3,
                              allocator_candidates_ranked=2)
    assert full["EVIDENCE_SUFFICIENT"] is True
    f = sm.freeze(evidence=full)
    assert f["frozen"] is False
    assert "OWNER_APPROVAL_ABSENT" in f["blockers"]
    assert "EVIDENCE_INSUFFICIENT" not in f["blockers"]


def test_partial_evidence_names_exactly_what_is_still_missing():
    e = sm.evidence_status(p_fill_rows=500, p_fill_positives=20,
                           p_fill_negatives=0, exit_rows=40,
                           maker_actions_priced=3,
                           allocator_candidates_ranked=1)
    assert e["EVIDENCE_SUFFICIENT"] is False
    assert e["missing"] == ["P_FILL_IDENTIFIED_NEGATIVES_ABOVE_ZERO"]


def test_a_missing_parameter_is_its_own_refusal():
    params = {k: v for k, v in sm.PROPOSED.items() if k != "PAIRING_RULE"}
    f = sm.freeze(owner_approval_token="APPROVED", parameters=params)
    assert "REQUIRED_PARAMETERS_ABSENT" in f["blockers"]
    assert f["parametersAbsent"] == ["PAIRING_RULE"]


def _evidence_ok():
    return sm.evidence_status(p_fill_rows=500, p_fill_positives=20,
                              p_fill_negatives=15, exit_rows=40,
                              maker_actions_priced=3,
                              allocator_candidates_ranked=2)


def test_a_complete_approved_mandate_freezes_with_a_sha():
    params = dict(sm.PROPOSED)
    params["EXIT_RULE"] = {"rule": "EXIT_AT_PAIR_COMPLETION_OR_SETTLEMENT"}
    f = sm.freeze(owner_approval_token="APPROVED", parameters=params,
                  evidence=_evidence_ok())
    assert f["frozen"] is True
    assert f["MANDATE_STATUS"] == "FROZEN"
    assert len(f["MANDATE_SHA"]) == 16


def test_the_sha_moves_when_a_limit_moves():
    base = dict(sm.PROPOSED)
    base["EXIT_RULE"] = {"rule": "EXIT_AT_PAIR_COMPLETION"}
    a = sm.freeze(owner_approval_token="X", parameters=base,
                  evidence=_evidence_ok())
    moved = dict(base)
    moved["MAX_RESIDUAL_INVENTORY"] = {"unpairedLegs": 50}
    b = sm.freeze(owner_approval_token="X", parameters=moved,
                  evidence=_evidence_ok())
    assert a["MANDATE_SHA"] != b["MANDATE_SHA"]
    assert "backtest of the limits" in a["chosenBeforeResults"]


def test_a_frozen_mandate_still_needs_activation():
    params = dict(sm.PROPOSED)
    params["EXIT_RULE"] = {"rule": "EXIT_AT_PAIR_COMPLETION"}
    f = sm.freeze(owner_approval_token="APPROVED", parameters=params,
                  evidence=_evidence_ok())
    r = sm.may_create_inventory(label="COUNTERFACTUAL_FILL_SUPPORTED",
                                mandate=f)
    assert r["mayCreate"] is False
    assert r["blockers"] == ["MANDATE_NOT_ACTIVE"]


# ── the proposal is reviewable, which means it must be real ──────────

def test_the_creating_actions_exist_in_the_canonical_action_set():
    c = sm.creating_actions_are_real_actions()
    assert c["allExist"] is True
    assert c["unknownToTheActionSet"] == []


def test_only_passive_entry_actions_may_create_inventory():
    named = sm.PROPOSED["ACTIONS_THAT_MAY_CREATE_SHADOW_INVENTORY"]
    for a in named:
        assert a.startswith("MAKE_")
        assert acts.EXPOSURE_EFFECT[a][0] == "INCREASE"


def test_the_residual_cap_is_the_experiment_not_furniture():
    assert "measure what" in sm.PROPOSED["MAX_RESIDUAL_INVENTORY"]["why"]
    assert "Ferrari" in sm.WHY_THE_LIMITS_ARE_THE_EXPERIMENT


def test_the_pairing_rule_is_venue_native_identity_only():
    r = sm.PROPOSED["PAIRING_RULE"]
    assert "VENUE_NATIVE_ID_MATCH" in r["rule"]
    assert "no price matching" in r["why"]


def test_no_trade_is_not_hold():
    assert "never recorded as HOLD" in sm.PROPOSED["NO_TRADE_RULE"]["rule"]


def test_the_proposal_carries_a_sha_so_an_edit_is_visible():
    assert len(sm.PROPOSAL_SHA) == 16
    assert sm.proposal()["PROPOSAL_SHA"] == sm.PROPOSAL_SHA
