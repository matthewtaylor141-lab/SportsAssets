"""THE EXPERIMENTAL LANE, PINNED WHERE IT COULD DO DAMAGE.

Owner directive 2026-09-19 21:2xZ. The tests below are grouped by the
failure each one prevents, because that is the only reason any of them
exist:

  1. THE DECISION-GRADE LANE STOPS WRITING. The V2 code sha is
     ENFORCED; an edit to a boundary symbol fails it closed. This lane
     imports those symbols and must never touch them.
  2. A REFUSAL VOCABULARY THAT QUIETLY REBUILDS THE STRICT LANE. If
     "unproven" ever becomes a blocker here, the experimental lane
     measures nothing and reports zero trades exactly as before.
  3. A RULE EDITED UNDER A RUNNING EXPERIMENT. §4's whole guarantee.
  4. AN INVALID INPUT MEASURED AS THOUGH IT WERE EVIDENCE.
  5. A MANUFACTURED P_BETTOR. §3's explicit prohibition.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import shadow as sh
from sportsassets import shadow_bettor_codesha as cs
from sportsassets import shadow_bettor_policy as bpol
from sportsassets import shadow_experiment_registry as reg
from sportsassets import shadow_experiment_signals as sig
from sportsassets import shadow_experiments as xp

NOW = datetime(2026, 9, 19, 21, 30, tzinfo=timezone.utc)


def _state(**kw):
    base = {"readable": True, "bid": 0.48, "ask": 0.52, "mid": 0.50,
            "spread": 0.04, "spreadRelative": 0.08, "stalenessMs": 500}
    base.update(kw)
    return base


def _book():
    return {"bids": [(0.48, 1000)], "asks": [(0.52, 1000)]}


# ── 1. THE DECISION-GRADE LANE MUST KEEP WRITING ─────────────────────


def test_the_experimental_lane_did_not_move_the_v2_code_sha():
    """THE MOST IMPORTANT TEST IN THIS FILE.

    BETTOR_EV_SHADOW_V2's code sha is enforced: if the running digest
    stops matching the frozen one, the decision-grade worker stops
    writing decisions entirely. Every symbol in that boundary lives in
    shadow_bettor.py, shadow_lanes.py and shadow.py -- all three of
    which this lane imports from. An accidental edit to `assert_lineage`
    or `bettor_lane_decision` while building an experiment would take
    the primary lane down, and nothing else in the suite would say so.
    """
    assert cs.semantic_code_sha() == bpol.POLICY_CODE_SHA


def test_the_experimental_lane_is_not_in_the_decision_grade_lane_tuple():
    """§1: "Preserve the current strict lane. Do not weaken it."

    lanes.LANES gates register_specialist and the decision-grade
    lineage check. Widening it would let an experimental row be written
    through a decision-grade path by a later caller.
    """
    from sportsassets import shadow_lanes as lanes
    assert xp.EXPERIMENTAL_LANE not in lanes.LANES
    assert xp.DECISION_GRADE_LANE in lanes.LANES
    assert xp.EXPERIMENTAL_LANE != xp.DECISION_GRADE_LANE


def test_no_experimental_artefact_can_claim_decision_grade():
    for e in reg.EXPERIMENTS:
        assert e["notDecisionGrade"] is True
        assert e["realOrderSubmissionEnabled"] is False
        assert e["capitalAtRisk"] == 0
        assert "NOT DECISION-GRADE" in e["disclosure"]
        assert "NO REAL CAPITAL" in e["disclosure"]


# ── 2. "UNPROVEN" IS NEVER A BLOCKER HERE ────────────────────────────


def test_the_decision_grade_refusals_are_not_input_blockers():
    """§5: "MODEL NOT YET PROVEN ... is allowed in EXPERIMENTAL SHADOW."

    If any of these ever joined INPUT_BLOCKERS the experimental lane
    would refuse for the same reasons the strict lane does, produce zero
    trades, and answer the question it was built to answer with silence.
    """
    for reason in xp.NOT_BLOCKERS_HERE:
        assert reason not in xp.INPUT_BLOCKERS


def test_a_clean_input_with_an_unproven_model_is_not_blocked():
    assert xp.input_blockers_for(
        observed_at=NOW, now=NOW, symbol="mkt-1",
        market_state=_state(), executable_book=_book()) == []


# ── 3. A RULE EDITED UNDER A RUNNING EXPERIMENT ──────────────────────


def test_every_declared_experiment_verifies_against_its_own_hash():
    for check in reg.verify_all():
        assert check["matches"], check


def test_editing_any_rule_moves_the_experiment_hash():
    """§4: "A later change creates a new version."

    Asserting the rule is not testing it: each field is actually
    mutated and the hash recomputed.
    """
    for field in ("directionRule", "entryRule", "exitRule", "pairingRule",
                  "cashoutRule", "horizon", "target", "modelVersion",
                  "intendedNotionalUsd", "latencyAssumption",
                  "startTimestamp"):
        edited = dict(reg.X1)
        edited[field] = "EDITED" if not isinstance(
            edited[field], (int, float)) else edited[field] + 1
        assert xp.experiment_sha(edited) != reg.X1["experimentSha"], field
        assert xp.verify(edited)["matches"] is False


def test_a_declaration_missing_a_frozen_rule_is_refused():
    """A rule added after an outcome is known is not a rule."""
    for omit in ("exit_rule", "entry_rule", "direction_rule",
                 "pairing_rule", "cashout_rule", "target"):
        kwargs = dict(
            experiment_id="XT", policy_version="v", model_version="m",
            feature_set=("f",), target="t", horizon="60S",
            direction_rule="d", entry_rule="e", exit_rule="x",
            pairing_rule="p", cashout_rule="c",
            latency_assumption="l", start_timestamp="2026-09-19T00:00:00Z")
        kwargs[omit] = ""
        with pytest.raises(xp.ExperimentRefusal) as exc:
            xp.declare(**kwargs)
        assert "§4" in str(exc.value) or "frozen" in str(exc.value)


def test_a_control_must_name_what_it_controls_for():
    with pytest.raises(xp.ExperimentRefusal):
        xp.declare(
            experiment_id="XC", role=xp.CONTROL, control_for=None,
            policy_version="v", model_version="m", feature_set=(),
            target="t", horizon="60S", direction_rule="d", entry_rule="e",
            exit_rule="x", pairing_rule="p", cashout_rule="c",
            latency_assumption="l", start_timestamp="2026-09-19T00:00:00Z")


def test_an_undeclared_horizon_is_refused():
    """The markout columns are fixed; an experiment whose horizon has no
    column would have no measurable target."""
    with pytest.raises(xp.ExperimentRefusal):
        xp.declare(
            experiment_id="XH", policy_version="v", model_version="m",
            feature_set=("f",), target="t", horizon="47S",
            direction_rule="d", entry_rule="e", exit_rule="x",
            pairing_rule="p", cashout_rule="c", latency_assumption="l",
            start_timestamp="2026-09-19T00:00:00Z")


def test_the_start_timestamp_is_a_literal_not_a_clock_read():
    """A start stamped at import changes every boot, so the hash would
    change every boot and every row would claim a different
    experiment."""
    import pathlib
    src = pathlib.Path(reg.__file__).read_text()
    assert 'START_UTC = "2026-09-19T21:30:00Z"' in src
    first = reg.X1["experimentSha"]
    import importlib
    importlib.reload(reg)
    assert reg.X1["experimentSha"] == first


# ── 4. AN INVALID INPUT IS NEVER MEASURED AS EVIDENCE ────────────────


def test_each_input_blocker_fires_on_its_own_cause():
    cases = {
        xp.I_MISSING_MARKET_IDENTITY: dict(symbol=None),
        xp.I_INVALID_TIMESTAMP: dict(observed_at=None),
        xp.I_STALE_DATA: dict(
            observed_at=NOW - timedelta(seconds=xp.MAX_STALENESS_S + 30)),
        xp.I_UNREADABLE_BOOK: dict(
            market_state={"readable": False, "whyUnreadable": "halted"}),
        xp.I_OUT_OF_DOMAIN: dict(domain_ok=False),
        xp.I_NO_EXECUTABLE_DEPTH: dict(executable_book=None),
    }
    for blocker, override in cases.items():
        kwargs = dict(observed_at=NOW, now=NOW, symbol="mkt-1",
                      market_state=_state(), executable_book=_book())
        kwargs.update(override)
        assert blocker in xp.input_blockers_for(**kwargs), blocker


def test_a_missing_required_feature_blocks():
    assert xp.I_MISSING_REQUIRED_FEATURE in xp.input_blockers_for(
        observed_at=NOW, now=NOW, symbol="m", market_state=_state(),
        executable_book=_book(), features={"bidSize": None},
        required_features=("bidSize",))


def test_a_feature_present_but_not_identified_is_missing():
    """NOT_IDENTIFIED is the absence of a measurement, not a value."""
    assert xp.I_MISSING_REQUIRED_FEATURE in xp.input_blockers_for(
        observed_at=NOW, now=NOW, symbol="m", market_state=_state(),
        executable_book=_book(),
        features={"bidSize": sh.NOT_IDENTIFIED},
        required_features=("bidSize",))


def test_a_timestamp_from_the_future_is_invalid_not_fresh():
    """A clock fault must not read as the best data we have."""
    assert xp.I_INVALID_TIMESTAMP in xp.input_blockers_for(
        observed_at=NOW + timedelta(minutes=5), now=NOW, symbol="m",
        market_state=_state(), executable_book=_book())


def test_a_blocked_input_cannot_produce_a_trading_action():
    with pytest.raises(xp.ExperimentRefusal) as exc:
        xp.experimental_decision(
            experiment=reg.X1, symbol="m", observed_at=NOW,
            proposed_action=sh.BUY, features={"microstructure": {}},
            input_blockers=[xp.I_STALE_DATA])
    assert "measurement of nothing" in str(exc.value)


def test_a_blocked_input_may_still_produce_a_no_trade():
    """The refusal is itself prospective evidence and is recorded."""
    d = xp.experimental_decision(
        experiment=reg.X1, symbol="m", observed_at=NOW,
        proposed_action=sh.NO_TRADE, features={"microstructure": {}},
        input_blockers=[xp.I_STALE_DATA])
    assert d["inputValid"] is False
    assert d["inputBlockers"] == [xp.I_STALE_DATA]


# ── 5. P_BETTOR IS NEVER MANUFACTURED ────────────────────────────────


def test_an_experimental_trade_does_not_manufacture_p_bettor():
    """§3: "If P_BETTOR is not established: P_BETTOR remains
    NOT_ESTABLISHED." The action is justified by the frozen hypothesis
    named on the row, not by a belief this lane does not hold."""
    from sportsassets import shadow_lanes as lanes
    d = xp.experimental_decision(
        experiment=reg.X1, symbol="m", observed_at=NOW,
        proposed_action=sh.BUY, direction=sig.LONG,
        features={"microstructure": {"mid": 0.5}},
        signal=sig.short_horizon_direction([0.50, 0.52, 0.54]))
    assert d["pBettor"] is None
    assert d["pBettorStatus"] == lanes.NOT_ESTABLISHED
    assert d["justification"] == "FROZEN_CANDIDATE_HYPOTHESIS"
    assert d["experimentSha"] == reg.X1["experimentSha"]
    assert d["notDecisionGrade"] is True


def test_the_independence_wall_still_holds_in_this_lane():
    """More permissive about EVIDENCE OF EDGE; not about reading RN1.
    A candidate fed an RN1 feature would be measuring RN1, and the
    benchmark would sit inside the thing it benchmarks."""
    from sportsassets import shadow_lanes as lanes
    with pytest.raises(lanes.LaneViolation):
        xp.experimental_decision(
            experiment=reg.X1, symbol="m", observed_at=NOW,
            proposed_action=sh.NO_TRADE,
            features={"theirLevel": 0.42},
            declared_provenances={"theirLevel": lanes.PROV_RN1_ACTION})


def test_an_undeclared_provenance_fails_closed_rather_than_passing():
    """Ambiguity is refused, not resolved by guessing."""
    from sportsassets import shadow_lanes as lanes
    with pytest.raises(lanes.LaneViolation):
        xp.experimental_decision(
            experiment=reg.X1, symbol="m", observed_at=NOW,
            proposed_action=sh.NO_TRADE, features={"mystery": 1},
            declared_provenances={"mystery": "SOMETHING_NOBODY_DECLARED"})


def test_provenance_is_declared_not_sniffed_from_the_feature_name():
    """A name-sniffing rule "misses signal_17 and catches
    rn1_free_indicator". A feature merely NAMED like RN1's, declared
    with an independent provenance, is admitted -- the declaration is
    what carries the claim."""
    d = xp.experimental_decision(
        experiment=reg.X1, symbol="m", observed_at=NOW,
        proposed_action=sh.NO_TRADE, features={"rn1_free_indicator": 1},
        declared_provenances={
            "rn1_free_indicator": sig.M1_PROVENANCE})
    assert d["rn1FeaturesUsed"] is False


# ── 6. THE SIGNALS REFUSE RATHER THAN GUESS ──────────────────────────


def test_every_unarmed_signal_returns_not_identified_without_its_feature():
    """A signal that returned 0.0 on missing input would be measuring
    the frequency of missing data."""
    assert sig.microprice(bid=0.4, ask=0.6)["status"] == sh.NOT_IDENTIFIED
    assert sig.order_flow_imbalance(
        {"bid": 0.4, "ask": 0.6}, {"bid": 0.41, "ask": 0.6}
    )["status"] == sh.NOT_IDENTIFIED
    assert sig.external_disagreement(
        venue_mid=0.5)["status"] == sh.NOT_IDENTIFIED
    assert sig.relative_value(leg_ask=0.45)["status"] == sh.NOT_IDENTIFIED


def test_the_direction_score_needs_real_samples():
    assert sig.short_horizon_direction([])["status"] == sh.NOT_IDENTIFIED
    assert sig.short_horizon_direction(
        [0.5, 0.51])["status"] == sh.NOT_IDENTIFIED
    assert sig.short_horizon_direction(
        [0.5, 0.52, 0.54])["direction"] == sig.LONG
    assert sig.short_horizon_direction(
        [0.54, 0.52, 0.50])["direction"] == sig.SHORT


def test_drift_inside_the_frozen_threshold_is_flat_and_not_actionable():
    out = sig.short_horizon_direction([0.500, 0.5005, 0.5002])
    assert out["direction"] == sig.FLAT
    assert out["actionable"] is False


def test_a_wide_book_is_refused_by_m1s_own_gate():
    """The midpoint of a 30-cent spread is not a price anything traded
    at; a direction read from it measures the spread."""
    assert sig.m1_gate(_state(spreadRelative=0.80)) == "SPREAD_ABOVE_FROZEN_MAX"
    assert sig.m1_gate(_state()) is None
    assert sig.m1_gate({}) == "SPREAD_NOT_IDENTIFIED"


def test_the_null_control_is_deterministic():
    """A coin-flipping control would make every comparison a question
    about the seed, and could not be replayed from the ledger."""
    assert {sig.null_control()["direction"] for _ in range(20)} == {sig.LONG}


# ── 7. READINESS IS HONEST IN BOTH DIRECTIONS ────────────────────────


def test_the_armed_set_is_exactly_what_the_captured_evidence_supports():
    """§3 applied both ways: no invented model, and no silently dropped
    one. A candidate the directive named that cannot be computed is on
    the register saying which feature it waits for."""
    assert {e["experimentId"] for e in reg.armed()} == {
        "X1_SHORT_HORIZON_DIRECTION", "X1C_NULL_CONTROL"}
    awaiting = {e["experimentId"]: e for e in reg.awaiting()}
    assert set(awaiting) == {"X2_RELATIVE_VALUE", "X3_MICROPRICE",
                             "X4_ORDER_FLOW_IMBALANCE",
                             "X5_EXTERNAL_DISAGREEMENT"}
    for e in awaiting.values():
        assert e["requiredFeatures"], e["experimentId"]
        assert e["notes"]


def test_every_armed_candidate_has_a_control():
    controls = {e["controlFor"] for e in reg.EXPERIMENTS
                if e["role"] == xp.CONTROL}
    for e in reg.armed():
        if e["role"] != xp.CONTROL:
            assert e["experimentId"] in controls, e["experimentId"]


def test_the_control_trades_the_same_tape_as_its_candidate():
    """§9: a control that traded a different tape would compare
    nothing."""
    assert reg.X1C["horizon"] == reg.X1["horizon"]
    assert reg.X1C["intendedNotionalUsd"] == reg.X1["intendedNotionalUsd"]
    assert reg.X1C["exitRule"] == reg.X1["exitRule"]
    assert reg.X1C["featureSet"] == []


def test_the_notional_comes_from_the_frozen_sizing_policy():
    """§6 / §15: retyping $1,000 here is how two $1,000s drift apart."""
    from sportsassets import shadow_bettor_sizing as sizing
    for e in reg.EXPERIMENTS:
        assert e["intendedNotionalUsd"] == float(
            sizing.STANDARD_BETTOR_SHADOW_NOTIONAL_USD)
        assert e["sizingPolicyVersion"] == sizing.SIZING_POLICY_VERSION


def test_the_registry_report_never_claims_real_activity():
    r = reg.registry_report()
    assert r["realOrderActivity"] == "NONE"
    assert r["realCapitalAtRisk"] == 0
    assert r["notDecisionGrade"] is True
    assert r["hashesVerified"] is True
    assert r["shadowMode"] is True
