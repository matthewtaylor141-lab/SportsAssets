"""§15/§16: the two quantities the whole stack waits on."""

import pytest

from sportsassets import bettor_fair_value as fv
from sportsassets import bettor_p_fill as pf


# ── §16: independent fair value ──────────────────────────────────────

def test_no_model_is_validated():
    s = fv.status()
    assert s["anyValidated"] is False
    assert all(v == fv.NOT_VALIDATED for v in s["models"].values())


def test_the_champion_is_still_the_market():
    assert fv.CHAMPION == "B0_VENUE_PRICE"
    assert "out of sample" in fv.CHAMPION_IS_THE_MARKET


def test_the_v3_result_is_carried_with_its_numbers():
    v3 = fv.status("p_bettor_independent_v3")
    assert v3["incrementalSignalStatus"] == fv.NOT_DETECTED
    assert v3["deltaLogLossBlendMinusMarket"] < 0      # the blend is WORSE
    assert v3["calibratorSelected"] == "IDENTITY"
    assert v3["leakCheck"] == "CLEAN"


def test_not_detected_is_neither_validated_nor_refuted():
    """Both readings turn an inconclusive measurement into a conclusion."""
    assert "not evidence that no edge exists" in fv.NOT_DETECTED_IS_NOT_REFUTED
    assert "not evidence that one does" in fv.NOT_DETECTED_IS_NOT_REFUTED


def test_directional_actions_are_blocked_and_not_for_performance():
    d = fv.directional_permitted()
    assert d["permitted"] is False
    assert d["blocker"] == "FV_BETTOR_INDEPENDENT_NOT_VALIDATED"
    assert "no P&L is read here" in d["isNotPerformanceBased"]
    assert d["whatWouldLiftIt"]


def test_the_midpoint_never_becomes_a_belief():
    assert fv.fair_value()[fv.FV_BETTOR_INDEPENDENT] == fv.NOT_IDENTIFIED
    assert "only number present" in fv.MIDPOINT_IS_NOT_A_BELIEF


def test_the_method_findings_that_bind_future_versions_are_kept():
    m = fv.METHOD_FINDINGS
    # In-sample selection flatters the most flexible candidate.
    assert "0.5702" in m["SELECTION_IS_PART_OF_FITTING"]
    # The control bound nothing on that run and still stays.
    assert "0.00000" in m["KEEP_THE_LEAK_CONTROL"]
    assert "never adds signal" in m["CALIBRATION_CREATES_NO_INFORMATION"]


# ── §15: P_FILL ──────────────────────────────────────────────────────

def test_p_fill_is_not_identified_without_bettor_native_fills():
    p = pf.p_fill()
    assert p["P_FILL"] == pf.NOT_IDENTIFIED
    assert p["requiredSource"] == "BETTOR_NATIVE_ADMITTED_FILLS_REQUIRED"
    assert "never rested an order" in p["why"]


@pytest.mark.parametrize("substitute", [
    "WHALE_COMPLETION", "TOUCH", "PRICE_MOVE", "DISPLAYED_DEPTH"])
def test_each_forbidden_substitute_is_refused_by_name(substitute):
    assert substitute in pf.FORBIDDEN_AS_P_FILL
    assert pf.FORBIDDEN_AS_P_FILL[substitute]


def test_one_fill_does_not_become_a_measured_rate():
    """A ladder, not a switch."""
    p = pf.p_fill(bettor_native_fills=1)
    assert p["status"] == "PARTIALLY_IDENTIFIED"
    assert p["status"] != "IDENTIFIED"
    assert "one observation" in pf.ONE_FILL_DOES_NOT_IDENTIFY_IT


def test_maker_fill_is_actually_loaded():
    m = pf.machinery_status()
    assert m["status"] == "LOADED"
    assert m["ORDER_PATH_EXISTS"] is False
    assert m["ACTUAL_BETTOR_FILL"] == pf.NOT_IDENTIFIED


def test_ticks_refute_but_cannot_support():
    m = pf.machinery_status()
    assert m["TICKS_ALONE_SUPPORT_POSITIVE_FILL"] is False
    assert m["TICKS_ALONE_SUPPORT_REFUTATION"] is True
    assert m["POSITIVE_FILL_SUPPORT_REQUIRES"] == "EXECUTION_TAPE_JOIN"


def test_refutation_is_available_today_and_is_conservative():
    r = pf.refutation_available()
    assert r["available"] is True
    assert r["direction"] == "REFUTATION_ONLY"
    assert "LOWER bound on the true queue" in r["isAnUpperBound"]


def test_nothing_on_this_path_can_write_filled():
    m = pf.machinery_status()
    assert "FILLED" not in m["FILL_STATUSES"]
    assert all(s.startswith("COUNTERFACTUAL_FILL") or
               s in ("NOT_FILLED", "UNKNOWN") for s in m["FILL_STATUSES"])


def test_a_touch_is_not_a_fill_status():
    assert pf.machinery_status()["TOUCH_IS_NOT_A_FILL_STATUS"] is True


def test_the_accumulation_contract_is_prospective():
    c = pf.accumulation_contract()
    assert "leaves no trace to recover" in c["whyProspective"]
    assert "QUEUE_AHEAD_AT_INSERT" in c["perQuote"]
    # Observations about the market are stored under their own names.
    assert "TOUCHED" in c["perObservation"]
    assert "Neither is ever counted toward a fill rate" in \
        c["notRecordedAsFills"]
