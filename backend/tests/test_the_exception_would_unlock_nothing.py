"""WOULD THE TIMING EXCEPTION UNLOCK ANYTHING? The tests behind the answer.

CORRECT CASES MUST PASS AND INCORRECT CASES MUST REFUSE. A module that answered
"NO" to everything would prove nothing, so the admitting case is tested first and
tested hardest: a candidate whose ONLY refusals are venue-book clock refusals,
with every unreached requirement independently satisfied, MUST come back as
unlocked. Everything after that is the refusing half.
"""

from __future__ import annotations

import pytest

from sportsassets import bettor_admission_policy as AP
from sportsassets import bettor_external_shadow as EXT
from sportsassets import candidate_assessment as CA


def _satisfied():
    """Every requirement the shadow lane never reaches, independently met."""
    return {name: {"satisfied": True, "evidence": "test fixture"}
            for name in CA.NOT_REACHED_BY_THIS_LANE}


# ── 1 · THE CORRECT CASE: the exception DOES unlock this one ──────────

def test_a_candidate_blocked_ONLY_on_the_venue_book_clock_would_be_unlocked():
    """THE POSITIVE CONTROL. Without this the module's NO is worthless: it would
    be refusing regardless of the evidence."""
    got = CA.assess([{"refusals": ["VENUE_BOOK_STALE"], "admissible": False}],
                    source="test", window_description="unit",
                    external_state=_satisfied())
    assert got["ok"] is True
    cf = got["if_only_the_timing_exception_were_accepted"]
    assert cf["candidates_whose_lane_refusals_are_ALL_waived"] == 1
    assert cf["candidates_that_would_actually_be_admitted"] == 1
    assert cf["refusals_on_EVERY_candidate_that_the_exception_cannot_waive"] == []
    assert got["does_the_exception_unlock_anything"] is True
    # AND IT IS STILL A COUNTERFACTUAL. Even in the positive case nothing is
    # enabled and nothing is written.
    assert "COUNTERFACTUAL COUNT and not an admission" in got["the_answer"]
    w = got["and_nothing_here_was_enabled_or_written"]
    assert w["policy_admission_enabled"] is False
    assert w["orders_submitted"] == 0
    assert w["hypothetical_admissions_persisted"] == 0


def test_the_policy_exception_is_never_enabled_by_running_the_assessment():
    before = AP.POLICY_ADMISSION_ENABLED
    CA.assess([{"refusals": ["VENUE_BOOK_STALE"]}], source="t",
              window_description="u", external_state=_satisfied())
    assert AP.POLICY_ADMISSION_ENABLED is before is False
    assert AP.available()["refusal"] == AP.R_NOT_ENABLED


# ── 2 · ONE UNWAIVABLE REFUSAL IS ENOUGH TO BLOCK ────────────────────

@pytest.mark.parametrize("code,requirement", [
    ("VOID_ABANDONMENT_RULE_NOT_ESTABLISHED", CA.SETTLEMENT),
    ("OVERTIME_RULE_NOT_ESTABLISHED", CA.SETTLEMENT),
    ("NO_QUALIFIED_MODEL", CA.PROBABILITY),
    ("INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED", CA.PROBABILITY),
    ("VENUE_CONTRACT_PERIOD_NOT_ESTABLISHED", CA.IDENTITY),
    ("NO_ACTION_HAS_POSITIVE_NET_EDGE", CA.NET_EDGE),
    ("EXECUTION_ESTIMATE_NOT_IDENTIFIED", CA.NET_EDGE),
    ("RISK_GATE_BLOCKED", CA.RAILS),
    ("NO_RAIL_HEADROOM_FOR_ANY_POSITION", CA.RAILS),
])
def test_each_other_requirement_survives_the_exception_SEPARATELY(code,
                                                                 requirement):
    """The user asked for each requirement kept apart, so each is tested apart.
    A candidate carrying the venue-book clock refusal AND this one is still
    blocked, and the residual is reported under the right requirement."""
    got = CA.assess([{"refusals": ["VENUE_BOOK_STALE", code]}],
                    source="test", window_description="unit",
                    external_state=_satisfied())
    cf = got["if_only_the_timing_exception_were_accepted"]
    assert cf["candidates_that_would_actually_be_admitted"] == 0
    assert cf["residual_refusals_by_code"] == {code: 1}
    assert cf["residual_blockers_by_requirement"] == {requirement: 1}
    assert got["does_the_exception_unlock_anything"] is False


def test_the_provider_quote_rule_is_NOT_waived_although_it_shares_the_stage():
    """QUOTE_STALE sits in 2_FRESHNESS with the venue-book codes, and waiving
    the stage wholesale would silently extend a venue-book assumption over the
    ODDS PROVIDER's own published 30 s rule -- which the owner is not being
    asked to accept."""
    assert "QUOTE_STALE" not in CA.waived_refusal_codes()
    assert "QUOTE_STALE" in CA.NOT_WAIVED_THOUGH_IN_THE_SAME_STAGE
    got = CA.assess([{"refusals": ["QUOTE_STALE"]}], source="t",
                    window_description="u", external_state=_satisfied())
    cf = got["if_only_the_timing_exception_were_accepted"]
    assert cf["residual_refusals_by_code"] == {"QUOTE_STALE": 1}
    assert cf["candidates_that_would_actually_be_admitted"] == 0


# ── 3 · AN UNREACHED REQUIREMENT IS NOT A SATISFIED ONE ──────────────

@pytest.mark.parametrize("missing", sorted(CA.NOT_REACHED_BY_THIS_LANE))
def test_an_unreached_requirement_BLOCKS_when_it_is_not_independently_met(
        missing):
    """The shadow lane emits no refusal for these, so their silence must not
    read as a pass. Each one alone is enough to block."""
    state = _satisfied()
    state[missing] = {"satisfied": False, "evidence": "test: not met"}
    got = CA.assess([{"refusals": ["VENUE_BOOK_STALE"]}], source="t",
                    window_description="u", external_state=state)
    cf = got["if_only_the_timing_exception_were_accepted"]
    assert missing in cf["unreached_requirements_that_block"]
    assert cf["candidates_that_would_actually_be_admitted"] == 0
    assert cf["requirements_this_lane_never_reached"][missing][
        "reached_by_this_lane"] is False


def test_UNKNOWN_is_treated_as_blocking_and_not_as_a_pass():
    """`satisfied=None` -- a read that failed, or a requirement nobody asked
    about -- blocks. Fail-closed, and the same rule as everywhere else here."""
    for state in (None, {}, {CA.CALIBRATION: {"satisfied": None}}):
        got = CA.assess([{"refusals": ["VENUE_BOOK_STALE"]}], source="t",
                        window_description="u", external_state=state)
        cf = got["if_only_the_timing_exception_were_accepted"]
        assert cf["candidates_that_would_actually_be_admitted"] == 0
        assert CA.CALIBRATION in cf["unreached_requirements_that_block"]


# ── 4 · THE EXCEPTION'S SCOPE IS DERIVED, NOT ASSERTED ───────────────

def test_the_exception_can_bear_on_MARKET_DATA_CURRENCY_AND_NOTHING_ELSE():
    """Proved against the lane's own stage table, so a code added to another
    stage cannot quietly fall inside the waived set."""
    pw = CA.proves_it_waives_nothing_else()
    assert pw["requirements_the_exception_can_bear_on"] == [CA.CURRENCY]
    assert pw["and_that_set_must_be_exactly"] == [CA.CURRENCY]
    for stage, row in pw["by_stage"].items():
        if row["requirement"] != CA.CURRENCY:
            assert row["fully_outside_the_exception"], stage
            assert row["codes_the_exception_could_waive"] == []


def test_every_lane_stage_maps_to_a_named_requirement():
    """A stage added to `bettor_external_shadow.STAGES` with no requirement here
    would report UNMAPPED rather than being silently dropped -- and must not
    exist in the first place."""
    assert CA.unmapped_stages() == ()
    for stage in EXT.STAGE_ORDER:
        assert stage in CA.REQUIREMENT_OF_STAGE


# ── 5 · NO SOURCE IS A REFUSAL, NOT A CLEAN BILL OF HEALTH ───────────

@pytest.mark.parametrize("rows", [None, [], ()])
def test_an_empty_census_REFUSES_instead_of_reporting_zero_blockers(rows):
    got = CA.assess(rows, source="t", window_description="u")
    assert got["ok"] is False
    assert got["refusal"] == CA.R_NO_SOURCE
    assert "not 'zero blockers'" in got["why"]


# ── 6 · THE COUNTS-ONLY PATH, AND WHAT IT REFUSES TO CONCLUDE ────────

#: The recorded 24-hour census, EXT_PINNACLE_DEVIG_V1_SHADOW, as reported in
#: `research/SHADOW_LOOP_STATE.md` -- 464 candidates, 0 admissible.
RECORDED = {
    "VOID_ABANDONMENT_RULE_NOT_ESTABLISHED": 464,
    "NO_ACTION_HAS_POSITIVE_NET_EDGE": 445,
    "EXECUTION_ESTIMATE_NOT_IDENTIFIED": 442,
    "OVERTIME_RULE_NOT_ESTABLISHED": 233,
    "RISK_GATE_BLOCKED": 231,
    "SIZING_POLICY_NOT_APPLICABLE": 209,
    "NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT": 138,
    "INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED": 131,
    "NO_QUALIFIED_MODEL": 131,
    "QUOTE_STALE": 131,
}


def test_the_recorded_census_answers_NO_on_a_one_hundred_percent_refusal():
    got = CA.assess_from_counts(RECORDED, candidates=464, source="recorded",
                               window_description="24 h",
                               external_state=_satisfied())
    cf = got["if_only_the_timing_exception_were_accepted"]
    assert cf["refusals_on_EVERY_candidate_that_the_exception_cannot_waive"] == [
        "VOID_ABANDONMENT_RULE_NOT_ESTABLISHED"]
    assert cf["candidates_that_would_be_admitted"] == 0
    assert got["does_the_exception_unlock_anything"] is False
    assert got["refusal"] is None
    assert got["the_answer"].startswith("NO.")


def test_counts_short_of_one_hundred_percent_return_INDETERMINATE_not_yes():
    """THE HONEST LIMIT OF THE COUNTS-ONLY PATH. Per-code counts do not give the
    intersection of refusal sets, so without a code on every candidate the
    module says it cannot tell -- which is not a 'yes'."""
    got = CA.assess_from_counts({"NO_QUALIFIED_MODEL": 99}, candidates=100,
                               source="t", window_description="u",
                               external_state=_satisfied())
    assert got["refusal"] == CA.R_INDETERMINATE
    assert got["does_the_exception_unlock_anything"] is None
    assert got["if_only_the_timing_exception_were_accepted"][
        "candidates_that_would_be_admitted"] is None
    assert "INDETERMINATE" in got["the_answer"]
    assert "not a 'yes'" in got["the_answer"]


def test_the_counts_path_reports_an_UPPER_BOUND_per_requirement_not_a_sum():
    """Two codes sharing a requirement can land on one candidate, so summing
    them would overstate. The max is the most that is defensible."""
    got = CA.assess_from_counts(
        {"VOID_ABANDONMENT_RULE_NOT_ESTABLISHED": 10,
         "OVERTIME_RULE_NOT_ESTABLISHED": 10}, candidates=10, source="t",
        window_description="u", external_state=_satisfied())
    at_most = got["candidates_failing_each_requirement_AT_MOST"]
    assert at_most[CA.SETTLEMENT] == 10          # not 20
