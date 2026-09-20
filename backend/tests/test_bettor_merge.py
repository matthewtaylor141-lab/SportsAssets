"""§12: is there a merge, and on which venue?"""

from sportsassets import bettor_merge as mg


def test_the_answer_differs_by_venue():
    """A capability confirmed on one venue is not evidence about the other."""
    assert mg.RETAIL_NATIVE_MERGE_AVAILABLE == "NO"
    assert mg.INSTITUTIONAL_NATIVE_MERGE_AVAILABLE == mg.NOT_IDENTIFIED
    assert "blend retail and institutional" in mg.describe()["whyTheyDiffer"]


def test_pmus_is_not_identified_not_no():
    """'Not observed' and 'does not exist' are different claims."""
    assert mg.PMUS_NATIVE_MERGE_AVAILABLE == mg.NOT_IDENTIFIED
    inst = mg.availability(mg.INSTITUTIONAL)
    assert "different claims" in inst["notTheSameAsNo"]


def test_a_market_data_module_proves_nothing_about_the_venue():
    inst = mg.availability(mg.INSTITUTIONAL)
    assert "ORDER_SUBMISSION_IMPLEMENTATION = NONE" in inst["why"]
    assert "not evidence about the venue" in mg.SILENCE_IS_NOT_EVIDENCE


def test_retail_needs_no_merge_because_the_venue_nets():
    r = mg.availability(mg.RETAIL)
    assert r["mechanism"] == "VENUE_AUTO_NETS_AT_FILL"
    assert "REALISED AT THE SECOND FILL" in r["equivalentLifecycle"]


def test_auto_netting_does_not_license_recording_a_hedge_as_a_sale():
    """§3's confound, named at its source."""
    r = mg.availability(mg.RETAIL)
    assert "NO fees and NO queue position" in r["doesNotLicense"]


def test_merge_is_blocked_on_the_institutional_venue():
    p = mg.merge_permitted(mg.INSTITUTIONAL)
    assert p["permitted"] is False
    assert "no capital is released" in p["noFictionalAction"]


def test_merge_is_blocked_on_retail_too_but_for_a_different_reason():
    p = mg.merge_permitted(mg.RETAIL)
    assert p["permitted"] is False
    assert "already nets at fill" in p["why"]


def test_matched_inventory_stays_distinct_either_way():
    assert "never nets the legs" in mg.MATCHED_IS_STILL_DISTINCT


def test_an_unknown_venue_is_not_identified():
    assert mg.availability("SOMEWHERE_ELSE")[
        "PMUS_NATIVE_MERGE_AVAILABLE"] == mg.NOT_IDENTIFIED


# ── §12: the capital recycling chain is a different question ─────────

def test_the_chain_is_the_directives_five_steps():
    assert mg.RECYCLING_CHAIN == (
        "PAIR_COMPLETE", "MERGE_OR_NET", "RELEASE_CAPITAL",
        "RECORD_LOCKED_PAIR_PNL", "CAPITAL_RETURNS_TO_ALLOCATOR")


def test_retail_has_no_merge_call_and_recycles_capital_anyway():
    """THE CONFLATION THIS SEPARATES. An allocator reading merge
    availability as capital availability would conclude retail capital
    never returns, which is the opposite of the truth."""
    assert mg.merge_permitted(mg.RETAIL)["permitted"] is False
    r = mg.capital_recycling(mg.RETAIL)
    assert r["CAPITAL_RECYCLING_AVAILABLE"] == mg.YES
    assert r["stepsNotIdentified"] == []
    assert "second fill" in r["why"]
    assert "opposite of the truth" in r["mergeCallIsNotCapitalRecycling"]


def test_institutional_recycling_breaks_at_the_merge_step():
    r = mg.capital_recycling(mg.INSTITUTIONAL)
    assert r["CAPITAL_RECYCLING_AVAILABLE"] == mg.NOT_IDENTIFIED
    assert r["byStep"]["PAIR_COMPLETE"] == "AVAILABLE"
    assert r["byStep"]["MERGE_OR_NET"] == mg.NOT_IDENTIFIED
    assert "MERGE_OR_NET" in r["stepsNotIdentified"]


def test_institutional_recycling_is_not_identified_rather_than_no():
    r = mg.capital_recycling(mg.INSTITUTIONAL)
    assert r["CAPITAL_RECYCLING_AVAILABLE"] != mg.NO
    assert r["CAPITAL_RECYCLING_AVAILABLE"] != "0"
    assert "not zero and it is not NO" in r["why"]


def test_pair_pnl_is_recorded_independently_of_the_merge():
    """§12: keep matched-pair accounting independent from merge
    execution."""
    row = mg.CHAIN_BY_VENUE[mg.INSTITUTIONAL]
    assert row["RECORD_LOCKED_PAIR_PNL"] == \
        "AVAILABLE_INDEPENDENTLY_OF_MERGE"
    assert "does not wait on the merge" in row["pnlIsStillRecordable"]


def test_the_two_venues_are_never_answered_together():
    assert mg.capital_recycling(mg.RETAIL)["CAPITAL_RECYCLING_AVAILABLE"] \
        != mg.capital_recycling(
            mg.INSTITUTIONAL)["CAPITAL_RECYCLING_AVAILABLE"]


def test_an_unknown_venue_is_not_identified():
    r = mg.capital_recycling("SOMEWHERE_ELSE")
    assert r["CAPITAL_RECYCLING_AVAILABLE"] == mg.NOT_IDENTIFIED


def test_the_broken_chain_is_named_as_the_ferrari_failure():
    assert "deployable capital" in mg.FERRARI_LESSON
    assert "MERGE_OR_NET" in mg.FERRARI_LESSON
