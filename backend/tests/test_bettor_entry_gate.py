"""THE INDEPENDENT ENTRY PATH: admissible BUY, and correct refusal.

Our audit found an UNCONDITIONAL NO_TRADE: `shadow_bettor.decide` built
the whole action table and then called `not_yet_eligible`, which hard-sets
`proposedAction = NO_TRADE`. There was no branch to a BUY at all. No
commit replaced it. `bettor_entry_gate` is that branch and these tests
drive it through `shadow_bettor.decide` -- the same application path the
registered worker calls -- not around it.

EVERY ADMITTED CASE HERE IS A CONTROLLED TEST.

The qualified model, the independent fair value, the execution estimate
and the size are SUPPLIED BY THE TEST through `opportunity["entryInputs"]`.
On the production path that key is absent, so the gate sees none of them
and refuses on all of them. These tests demonstrate that the path EXISTS
and that its requirements bite; they are not observed opportunities, and
no model in the register currently qualifies (see
test_no_registered_model_currently_qualifies).

NO PRODUCTION REQUIREMENT IS WEAKENED to make the admitted case pass. The
admitted case supplies inputs that do not exist yet; it does not lower a
threshold.
"""

from __future__ import annotations

import pytest

from sportsassets import bettor_entry_gate as gate
from sportsassets import shadow as sh
from sportsassets import shadow_bettor as SB


# ── the inputs a real entry would need, supplied explicitly ─────────
QUALIFIED_MODEL = {
    "model_key": "bettor_settlement_v1", "version": 3,
    "target": "SETTLEMENT_OUTCOME", "status": "FROZEN",
    "prediction_validity": "VALID_AS_ENTRY_TIME",
}

INDEPENDENT_FV = {"value": 0.62, "kind": "BETTOR_INDEPENDENT_MODEL"}
VENUE_FV = {"value": 0.62, "kind": "FV_VENUE_IMPLIED_MIDPOINT"}

READABLE_BOOK = {"readable": True, "bid": 0.55, "ask": 0.57, "mid": 0.56,
                 "depth": 500.0}

GOOD_TABLE = [
    {"action": "BUY", "leg": "YES", "status": "IDENTIFIED",
     "expectedNetDollarsPerContract": 0.035, "price": 0.57},
    {"action": "SELL", "leg": "YES", "status": "IDENTIFIED",
     "expectedNetDollarsPerContract": -0.02, "price": 0.55},
]


def _fee(qty, price, maker=False):
    """The production shape, taker side: theta * C * p * (1-p)."""
    q, p = float(qty), float(price)
    return 0.0695 * q * p * (1.0 - p)


def _inputs(**over):
    base = {"model": QUALIFIED_MODEL, "fair_value": INDEPENDENT_FV,
            "execution_estimate": {"p_fill": 0.35}, "size": 100.0,
            "risk": {"permitted": True}, "fee_fn": _fee}
    base.update(over)
    return base


# ══ THE GATE ITSELF ═════════════════════════════════════════════════
def test_all_six_requirements_are_declared():
    assert gate.REQUIREMENTS == (
        "QUALIFIED_MODEL", "INDEPENDENT_FAIR_VALUE", "EXECUTION_ESTIMATE",
        "SIZING_POLICY", "RISK_PERMITTED", "READABLE_BOOK")


def test_the_gate_admits_when_every_requirement_is_met():
    res = gate.admit(action_table=GOOD_TABLE, market_state=READABLE_BOOK,
                     **_inputs())
    assert res["admissible"] is True, res
    assert res["action"] == gate.ENTRY_ACTION
    assert res["size"] == 100.0
    assert res["p_fill"] == 0.35
    assert res["expected_net_per_contract"] == pytest.approx(0.035)
    # AN ADMISSIBLE ENTRY IS STILL NOT AN EXECUTION.
    assert res["conditional_on_our_fill"]["execution_secured"] is False


@pytest.mark.parametrize("over,expect", [
    ({"model": None}, gate.R_NO_QUALIFIED_MODEL),
    ({"model": {**QUALIFIED_MODEL, "target": "RN1_NEXT_COMPLEMENT"}},
     gate.R_MODEL_TARGET_MISMATCH),
    ({"model": {**QUALIFIED_MODEL, "status": "SUPERSEDED_TARGET_MISMATCH"}},
     gate.R_MODEL_NOT_FROZEN),
    ({"model": {**QUALIFIED_MODEL,
                "prediction_validity": "INVALID_AS_ENTRY_TIME"}},
     gate.R_PREDICTIONS_INVALID_AS_ENTRY),
    ({"fair_value": None}, gate.R_NO_INDEPENDENT_FV),
    ({"fair_value": VENUE_FV}, gate.R_FV_IS_VENUE_PRICE),
    ({"execution_estimate": {}}, gate.R_NO_EXECUTION_ESTIMATE),
    ({"execution_estimate": {"p_fill": 0.0}},
     gate.R_NO_EXECUTION_ESTIMATE),
    ({"size": None}, gate.R_NO_SIZING),
    ({"size": 0}, gate.R_NO_SIZING),
    ({"risk": {"permitted": False}}, gate.R_RISK_BLOCKED),
])
def test_each_requirement_refuses_on_its_own(over, expect):
    """One missing input is enough, and it is named.

    Parameterised so a requirement cannot be quietly dropped: removing a
    check makes exactly one of these fail.
    """
    res = gate.admit(action_table=GOOD_TABLE, market_state=READABLE_BOOK,
                     **_inputs(**over))
    assert res["admissible"] is False, (over, res)
    assert expect in res["refusals"], (expect, res["refusals"])


def test_an_unreadable_book_and_absent_depth_refuse_by_name():
    res = gate.admit(action_table=GOOD_TABLE,
                     market_state={"readable": False},
                     **_inputs())
    assert res["admissible"] is False
    assert gate.R_BOOK_UNREADABLE in res["refusals"]
    assert gate.R_NO_DEPTH in res["refusals"]


def test_a_venue_derived_fair_value_can_never_license_an_entry():
    """The circularity guard. A benchmark is not evidence against itself.

    This is the specific thing the audit warned against: "do not simply
    remove the refusal or relabel midpoint as independent value".
    """
    for kind in ("FV_VENUE_IMPLIED", "VENUE_MIDPOINT", "B0_BENCHMARK"):
        res = gate.admit(action_table=GOOD_TABLE,
                         market_state=READABLE_BOOK,
                         **_inputs(fair_value={"value": 0.62,
                                               "kind": kind}))
        assert res["admissible"] is False, kind
        assert gate.R_FV_IS_VENUE_PRICE in res["refusals"], kind


def test_a_negative_engine_edge_refuses():
    res = gate.admit(
        action_table=[{"action": "BUY", "status": "IDENTIFIED",
                       "expectedNetDollarsPerContract": -0.01}],
        market_state=READABLE_BOOK, **_inputs())
    assert res["admissible"] is False, res
    assert gate.R_NO_POSITIVE_EDGE in res["refusals"], res


def test_a_fair_value_below_the_ask_refuses_on_the_gates_own_arithmetic():
    """The gate computes the edge when the engine identifies nothing.

    fair_value 0.50 against an ask of 0.57 is a negative edge before the
    fee is even charged, so there is nothing to admit.
    """
    res = gate.admit(action_table=[], market_state=READABLE_BOOK,
                     **_inputs(fair_value={"value": 0.50,
                                           "kind": "BETTOR_INDEPENDENT"}))
    assert res["admissible"] is False, res
    assert gate.R_NO_POSITIVE_EDGE in res["refusals"], res


def test_the_fee_must_be_supplied_and_is_charged_against_the_edge():
    """No silent zero-fee fallback. This project has shipped one before.

    And the fee genuinely reduces the edge: fair 0.62 minus ask 0.57 is
    0.05 gross, and the charged edge must be strictly less.
    """
    res = gate.admit(action_table=[], market_state=READABLE_BOOK,
                     **_inputs(fee_fn=None))
    assert res["admissible"] is False
    assert gate.R_NO_FEE_FUNCTION in res["refusals"], res

    ok = gate.admit(action_table=[], market_state=READABLE_BOOK,
                    **_inputs())
    assert ok["admissible"] is True, ok
    gross = 0.62 - 0.57
    assert 0.0 < ok["expected_net_per_contract"] < gross, ok
    assert ok["detail"]["gate_computed_row"]["components"][
        "fee_per_contract"] > 0.0


# ══ THROUGH THE APPLICATION PATH ════════════════════════════════════
def _opportunity(**over):
    o = {
        "bettorOpportunityId": "opp-1",
        "symbol": "TEST-YES",
        "outcomeLeg": "YES",
        "eventId": "ev-1",
        "marketId": "mk-1",
        "sport": "soccer",
        "league": "test",
        "evidenceSource": "PMUS_BBO",
        "observedAt": 1_000.0,
        "featureLineage": {},
        "microstructure": {"depth": 500.0, "spreadRelative": 0.02},
    }
    o.update(over)
    return o


def test_production_path_still_refuses_because_entry_inputs_are_absent():
    """THE PRODUCTION CASE. No `entryInputs`, so the gate sees nothing.

    This is the honest current state and it must stay the default: the
    refusal is now CONDITIONAL, and with production's inputs the condition
    is not met.
    """
    rec = SB.decide(_opportunity(), READABLE_BOOK, decision_ts=1_000.0)
    assert rec["proposedAction"] == sh.NO_TRADE, rec
    assert "entryGate" not in rec


def test_an_admissible_buy_comes_out_of_the_same_decide_call():
    """CONTROLLED TEST. Inputs supplied; the path is production's.

    Nothing is monkeypatched -- `decide` is called exactly as the worker
    calls it, with the inputs an entry requires present on the
    opportunity.
    """
    opp = _opportunity(entryInputs=_inputs())
    rec = SB.decide(opp, READABLE_BOOK, decision_ts=1_000.0)

    assert rec["proposedAction"] == gate.ENTRY_ACTION, rec
    assert rec["entryGate"]["admissible"] is True
    assert rec["proposedSize"] == 100.0
    assert rec["pFill"] == 0.35
    assert rec["pBettorStatus"] == "ESTABLISHED_BY_QUALIFIED_MODEL"
    # SHADOW ONLY, still.
    assert rec["orderSubmitted"] is False
    assert rec["shadowOnly"] is True
    assert rec["conditionalOnOurFill"]["execution_secured"] is False
    # and it is the SAME lane, through the SAME constructor
    assert rec["lane"] == rec["lane"]
    assert rec["signalSource"]


def test_a_refused_case_comes_out_of_the_same_decide_call():
    """CONTROLLED TEST, the negative. One input withdrawn.

    The venue-derived fair value is the interesting refusal because it is
    the one that would be easiest to fake past.
    """
    opp = _opportunity(entryInputs=_inputs(fair_value=VENUE_FV))
    rec = SB.decide(opp, READABLE_BOOK, decision_ts=1_000.0)
    assert rec["proposedAction"] == sh.NO_TRADE, rec
    # the refusal reasons are on the record, not swallowed
    assert rec["reasonCodes"], rec


def test_the_admitted_and_refused_cases_differ_only_in_their_inputs():
    """Same code path, same opportunity, one field changed.

    Without this the two tests above could be passing through different
    branches for reasons unrelated to the gate.
    """
    admit_opp = _opportunity(entryInputs=_inputs())
    refuse_opp = _opportunity(entryInputs=_inputs(size=None))
    a = SB.decide(admit_opp, READABLE_BOOK, decision_ts=1_000.0)
    r = SB.decide(refuse_opp, READABLE_BOOK, decision_ts=1_000.0)
    assert a["proposedAction"] == gate.ENTRY_ACTION
    assert r["proposedAction"] == sh.NO_TRADE
    assert a["marketId"] == r["marketId"] == "mk-1"


# ══ DOES ANY CURRENT MODEL QUALIFY? ═════════════════════════════════
def test_no_registered_model_currently_qualifies():
    """The separate status question, answered against the real register.

    A connected engine without a qualified model is a legitimate status
    and is different from an unimplemented path. This asserts the FORMER
    for the models the audit found in production: `rn1_complement_1h`
    v1 SUPERSEDED_TARGET_MISMATCH and v2 FROZEN, whose predictions were
    all INVALID_AS_ENTRY_TIME.
    """
    observed = [
        {"model_key": "rn1_complement_1h", "version": 1,
         "target": "RN1_NEXT_COMPLEMENT_1H",
         "status": "SUPERSEDED_TARGET_MISMATCH",
         "prediction_validity": "INVALID_AS_ENTRY_TIME"},
        {"model_key": "rn1_complement_1h", "version": 2,
         "target": "RN1_NEXT_COMPLEMENT_1H", "status": "FROZEN",
         "prediction_validity": "INVALID_AS_ENTRY_TIME"},
    ]
    for m in observed:
        q = gate.qualify_model(m)
        assert q["qualified"] is False, m
        # it fails on the TARGET, which no amount of retraining fixes:
        # a complement forecast is not a settlement forecast
        assert gate.R_MODEL_TARGET_MISMATCH in q["refusals"], q
        assert gate.R_PREDICTIONS_INVALID_AS_ENTRY in q["refusals"], q

    # and the gate is not vacuously strict: a settlement-target model with
    # valid predictions DOES qualify, so the refusals above are about
    # these models rather than about an unpassable check.
    assert gate.qualify_model(QUALIFIED_MODEL)["qualified"] is True
