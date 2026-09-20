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
