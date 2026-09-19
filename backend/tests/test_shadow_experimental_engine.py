"""THE T0 SEAL AND THE ARRIVAL WALK, pinned apart.

Owner directive 2026-09-19 22:4xZ §6/§7/§9/§10.

THE FAILURES THESE PREVENT:

  LOOKAHEAD. If anything observed after T0 could reach the decision,
  every result this lane ever produces is worthless and nothing in the
  numbers would show it. `seal()` and `execute()` are separate calls,
  the seal is hashed, and a seal that does not re-derive refuses to
  execute at all.

  A MODEL OUTPUT ERASED BY AN EXECUTION PROBLEM. §9: "Do not convert it
  to NO_TRADE after seeing the signal." A BUY_NO that cannot be
  executed must still be recorded as BUY_NO, or the later question --
  was the model right when we could not act? -- has no evidence behind
  it.

  A CONTROL ON A DIFFERENT POPULATION. X1C gated differently from X1
  would compare two different tapes and call the difference edge.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sportsassets import shadow_exec_contract as ec
from sportsassets import shadow_experiment_registry as reg
from sportsassets import shadow_experimental_engine as eng
from sportsassets import shadow_identity as ident
from sportsassets import shadow_l2 as l2

NOW = datetime(2026, 9, 19, 23, 20, tzinfo=timezone.utc)
RISING = [0.36, 0.38, 0.40]
FALLING = [0.44, 0.42, 0.40]
FLAT = [0.400, 0.4005, 0.4002]

BINDING_YES = {"verdict": ident.EXACT_ONE_TO_ONE, "executionEligible": True,
               "identityBindingSha": "idsha",
               "institutional": {"symbol": "astatc-x-laf"}}
BINDING_NO_PENDING = {"verdict": ident.STRUCTURAL_COMPLEMENT_PENDING,
                      "executionEligible": False, "retailLeg": "no"}


def micro(leg="yes", **kw):
    base = {"readable": True, "bid": 0.40, "ask": 0.41, "mid": 0.405,
            "spreadRelative": 0.024}
    base.update(kw)
    return l2.bind_leg(base, leg)


def opportunity(leg="yes", **kw):
    return {"bettorOpportunityId": "opp1", "symbol": "astatc-x-laf",
            "outcomeLeg": leg, "observedAt": NOW,
            "microstructure": micro(leg, **kw)}


def sealed(experiment=reg.X1, series=RISING, **kw):
    return eng.seal(experiment=experiment, opportunity=opportunity(),
                    mid_series=series, binding=BINDING_YES, at=NOW, **kw)


def evidence(offers=None, **kw):
    book = l2.book_from(
        {"symbol": "astatc-x-laf", "state": l2.STATE_OPEN,
         "transactTime": "2026-09-19T23:19:50Z",
         "bids": [{"px": "40", "qty": "500"}],
         "offers": offers if offers is not None
         else [{"px": "41", "qty": "30000"}, {"px": "42", "qty": "20000"}]},
        price_scale=100, qty_scale=100, request_id="rq")
    book.update({"l2EvidenceId": "l2ev_1",
                 "latencyRegime": "GITHUB_BRIDGE",
                 "bridgeLatencyMs": 612000.0})
    book.update(kw)
    return book


# ── §6: exactly one action, from the frozen rule ─────────────────────


def test_a_rising_series_buys_yes_and_a_falling_one_buys_no():
    assert sealed(series=RISING)["action"] == eng.BUY_YES
    assert sealed(series=FALLING)["action"] == eng.BUY_NO


def test_drift_inside_the_frozen_threshold_is_no_trade():
    out = sealed(series=FLAT)
    assert out["action"] == eng.NO_TRADE
    assert out["why"]


def test_a_book_wider_than_the_frozen_max_is_no_trade():
    out = eng.seal(experiment=reg.X1,
                   opportunity=opportunity(spreadRelative=0.80),
                   mid_series=RISING, binding=BINDING_YES, at=NOW)
    assert out["action"] == eng.NO_TRADE
    assert out["why"] == "SPREAD_ABOVE_FROZEN_MAX"


def test_every_seal_records_the_model_output_and_strength():
    out = sealed()
    assert out["modelOutput"] is not None
    assert out["signalStrength"] == pytest.approx(0.04)
    assert out["experimentSha"] == reg.X1["experimentSha"]
    assert out["intendedNotionalUsd"] == 1000.0


# ── §7: no lookahead, and the seal is tamper-evident ─────────────────


def test_the_seal_hash_re_derives():
    out = sealed()
    assert eng.seal_sha(out) == out["sealSha"]


def test_a_seal_edited_after_t0_cannot_be_executed():
    """THE MOST IMPORTANT TEST HERE. Future data may score a decision
    and may never change it."""
    from datetime import timedelta
    for field, value in (("action", eng.BUY_NO), ("signalStrength", 99.0),
                         ("intendedNotionalUsd", 5000.0),
                         ("decisionTimestamp", NOW + timedelta(minutes=5)),
                         ("featuresSha", "0000000000000000")):
        tampered = dict(sealed())
        tampered[field] = value
        with pytest.raises(eng.EngineRefusal) as exc:
            eng.execute(tampered, evidence=evidence(), binding=BINDING_YES)
        assert "altered after T0" in str(exc.value)


def test_execute_returns_a_separate_object_and_never_mutates_the_seal():
    s = sealed()
    before = dict(s)
    out = eng.execute(s, evidence=evidence(), binding=BINDING_YES,
                      arrival_at=NOW)
    assert s == before
    assert "action" not in out


def test_the_features_hash_covers_the_series_it_decided_on():
    a = sealed(series=RISING)["featuresSha"]
    b = sealed(series=[0.36, 0.38, 0.41])["featuresSha"]
    assert a != b


# ── §2/§3: only corrected, YES-bound rows may be evaluated ───────────


def test_a_duplicated_leg_row_is_refused_as_an_input():
    opp = opportunity()
    opp["microstructure"]["featureSourceVersion"] = \
        l2.FEATURE_SOURCE_VERSION_DUPLICATED
    with pytest.raises(eng.EngineRefusal) as exc:
        eng.seal(experiment=reg.X1, opportunity=opp, mid_series=RISING,
                 binding=BINDING_YES, at=NOW)
    assert "corrected" in str(exc.value)


def test_a_no_leg_row_is_refused_because_its_book_is_not_its_own():
    with pytest.raises(eng.EngineRefusal):
        eng.seal(experiment=reg.X1, opportunity=opportunity("no"),
                 mid_series=RISING, binding=BINDING_YES, at=NOW)


def test_an_unarmed_experiment_cannot_seal():
    with pytest.raises(eng.EngineRefusal):
        eng.seal(experiment=reg.X3, opportunity=opportunity(),
                 mid_series=RISING, binding=BINDING_YES, at=NOW)


# ── §9: the action survives an execution it cannot have ──────────────


def test_a_buy_no_blocked_on_identity_keeps_its_action():
    """"Do not convert it to NO_TRADE after seeing the signal." """
    s = sealed(series=FALLING)
    assert s["action"] == eng.BUY_NO
    out = eng.execute(s, evidence=evidence(), binding=BINDING_NO_PENDING,
                      arrival_at=NOW)
    assert out["executionStatus"] == eng.BLOCKED_IDENTITY
    assert s["action"] == eng.BUY_NO          # unchanged on the seal
    assert out["executedNotionalUsd"] is None


def test_a_no_trade_is_not_an_execution_failure():
    out = eng.execute(sealed(series=FLAT), evidence=evidence(),
                      binding=BINDING_YES, arrival_at=NOW)
    assert out["executionStatus"] == eng.NO_EXECUTION_INTENDED


def test_an_invalid_input_blocks_without_touching_the_action():
    s = sealed()
    out = eng.execute(s, evidence=evidence(), binding=BINDING_YES,
                      input_blockers=["STALE_DATA"])
    assert out["executionStatus"] == eng.BLOCKED_INPUT
    assert s["action"] == eng.BUY_YES


# ── §10: the $1,000 walk against real institutional depth ────────────


def test_the_thousand_dollar_walk_fills_what_the_book_had():
    out = eng.execute(sealed(), evidence=evidence(), binding=BINDING_YES,
                      arrival_at=NOW)
    assert out["executionStatus"] == eng.PARTIAL
    assert out["executedNotionalUsd"] == pytest.approx(207.0, abs=0.01)
    assert out["unfilledNotionalUsd"] == pytest.approx(793.0, abs=0.01)
    assert out["filledQty"] == pytest.approx(500.0)
    assert out["vwap"] == pytest.approx(0.414, abs=1e-3)
    # "If only $126 can execute: EXECUTED = $126, not $1,000."
    assert out["executedNotionalUsd"] + out["unfilledNotionalUsd"] \
        == pytest.approx(1000.0)


def test_an_execution_opens_a_position():
    out = eng.execute(sealed(), evidence=evidence(), binding=BINDING_YES,
                      arrival_at=NOW)
    assert out["positionId"]
    assert out["positionId"] == eng.position_id(
        out["experimentalDecisionId"])


def test_no_arrival_depth_creates_no_pnl_bearing_trade():
    """§4: "If no valid arrival L2 exists: SHADOW_EXECUTION =
    NOT_IDENTIFIED and no P&L-bearing trade is created." """
    out = eng.execute(sealed(), evidence=evidence(offers=[]),
                      binding=BINDING_YES, arrival_at=NOW)
    assert out["executionStatus"] == eng.NOT_IDENTIFIED
    assert out["positionId"] is None
    # NOT $0.00. Unfilled means the book was walked and gave nothing;
    # not-identified means it could not be walked, and reporting a zero
    # would assert a measurement nobody made.
    assert out["executedNotionalUsd"] is None
    assert out["unfilledNotionalUsd"] is None


def test_the_execution_carries_its_contract_and_its_latency_regime():
    out = eng.execute(sealed(), evidence=evidence(), binding=BINDING_YES,
                      arrival_at=NOW)
    assert out["executionContract"] == ec.CONTRACT_VERSION
    assert out["executionContractSha"] == ec.CONTRACT_SHA
    assert out["latencyRegime"] == "GITHUB_BRIDGE"
    # "Do not manufacture latency" -- the bridge's real figure travels
    assert out["observedArrivalLatencyMs"] == 612000.0
    assert out["l2EvidenceId"] == "l2ev_1"
    assert out["l2BookSha"]


# ── §7/§9: the control runs beside the candidate ─────────────────────


def test_the_control_sees_the_same_eligible_population_as_x1():
    """A control gated differently would compare two different tapes."""
    for series in (RISING, FALLING, FLAT):
        wide = opportunity(spreadRelative=0.80)
        x1 = eng.seal(experiment=reg.X1, opportunity=wide,
                      mid_series=series, binding=BINDING_YES, at=NOW)
        x1c = eng.seal(experiment=reg.X1C, opportunity=wide,
                       mid_series=series, binding=BINDING_YES, at=NOW)
        # both refused by the SAME book gate
        assert x1["action"] == x1c["action"] == eng.NO_TRADE


def test_the_control_is_always_long_where_it_is_eligible():
    for series in (RISING, FALLING, FLAT):
        assert sealed(reg.X1C, series)["action"] == eng.BUY_YES


def test_a_population_id_changes_when_its_membership_changes():
    """§7: X1 and the control must be scored on the SAME set."""
    a = eng.population_id(["o1", "o2"], "t", "r")
    assert a == eng.population_id(["o2", "o1"], "t", "r")   # order-free
    assert a != eng.population_id(["o1", "o2", "o3"], "t", "r")
    assert a != eng.population_id(["o1"], "t", "r")


def test_every_decision_is_shadow_and_says_so():
    s = sealed()
    assert s["notDecisionGrade"] is True
    assert s["realOrderSubmitted"] is False
    assert s["capitalAtRisk"] == 0
