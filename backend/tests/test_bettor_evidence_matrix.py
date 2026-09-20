"""§C. The frozen contract, graded against feeds we actually measured."""

import pytest

from sportsassets import bettor_evidence_matrix as em
from sportsassets import bettor_shadow_execution as sx


def test_every_negative_requirement_appears_in_the_matrix():
    """The matrix must cover sx.NEGATIVE_REQUIRES, not a paraphrase."""
    graded = {r["REQUIRED_FACT"] for r in em.NEGATIVE_MATRIX}
    for req in sx.NEGATIVE_REQUIRES:
        key = (req.split(" ")[0].replace("==", "").strip()
               if " " in req else req)
        assert any(key in g or g in req.replace(" ", "_")
                   for g in graded), req
    assert len(em.NEGATIVE_MATRIX) == len(sx.NEGATIVE_REQUIRES)


def test_every_row_carries_all_seven_columns():
    for m in (em.POSITIVE_MATRIX, em.NEGATIVE_MATRIX):
        for r in m:
            for col in ("REQUIRED_FACT", "SOURCE", "OBSERVED", "GRADE",
                        "SUFFICIENT_FOR_POSITIVE",
                        "SUFFICIENT_FOR_NEGATIVE", "LIMITATION"):
                assert col in r, (r.get("REQUIRED_FACT"), col)
            assert r["LIMITATION"], r["REQUIRED_FACT"]


def test_the_positive_is_blocked_by_exactly_one_unobservable_fact():
    """Blocks execute away from the book and the tape has no flag."""
    p = em.positive_identifiability()
    assert p["POSITIVE_LABEL_IDENTIFIABILITY"] == em.NO
    assert p["unobservableRequiredFacts"] == [
        "PRINT_WAS_A_CLOB_EXECUTION_NOT_A_BLOCK"]
    assert p["theSingleMissingFact"] == "PRINT_WAS_A_CLOB_EXECUTION_NOT_A_BLOCK"
    assert p["status"] == em.CONTRACT_NOT_IDENTIFIABLE


def test_the_print_side_is_derived_from_price_not_from_an_aggressor():
    """Corrected on review: too strict in the pessimistic direction."""
    row = next(r for r in em.POSITIVE_MATRIX
               if r["REQUIRED_FACT"] == "PRINT_WAS_ON_THE_SIDE_THAT_WOULD_CONSUME_US")
    assert row["GRADE"] == em.DERIVED
    assert row["SOURCE"] == em.SOURCE_TAPE
    assert "price priority" in row["LIMITATION"]
    assert "does NOT extend to prints AT our price" in row["LIMITATION"]


def test_queue_ahead_is_not_a_requirement_of_the_trade_through_positive():
    row = next(r for r in em.POSITIVE_MATRIX
               if r["REQUIRED_FACT"] == "QUEUE_AHEAD_AT_T0")
    assert row["SUFFICIENT_FOR_POSITIVE"] == "NOT_REQUIRED_FOR_THIS_LABEL"


def test_the_negative_is_blocked_by_five_unobservable_facts():
    n = em.negative_identifiability()
    assert n["NEGATIVE_LABEL_IDENTIFIABILITY"] == em.NO
    assert "QUEUE_DEPLETION_FROM_CANCELLATIONS_IDENTIFIED" in \
        n["unobservableRequiredFacts"]
    assert len(n["unobservableRequiredFacts"]) == 5
    assert "SNAPSHOTS" in n["why"]
    assert "FIVE of the seven" in n["why"]


def test_the_verdict_is_derived_from_the_matrix_not_asserted():
    """An UNOBSERVABLE row forces NO; an ASSUMED row forces PARTIALLY."""
    clean = (em._row("X", em.SOURCE_BOOK, True, em.EXACT, em.YES,
                     em.YES, "none"),)
    assumed = clean + (em._row("Y", em.SOURCE_NONE, False, em.ASSUMED,
                               em.PARTIAL, em.PARTIAL, "assumed"),)
    unobs = assumed + (em._row("Z", em.SOURCE_NONE, False,
                               em.UNOBSERVABLE, em.NO, em.NO, "gone"),)
    assert em._verdict(clean, "x")[0] == em.YES
    assert em._verdict(assumed, "x")[0] == em.PARTIAL
    assert em._verdict(unobs, "x")[0] == em.NO


def test_no_requirement_was_edited_to_reach_the_verdict():
    m = em.matrix()
    assert "not loosened to produce labels" in m["contractUnchanged"]
    # The contract's own requirement tuple is untouched.
    assert len(sx.NEGATIVE_REQUIRES) == 7


def test_the_price_grid_mismatch_is_measured_not_assumed():
    g = em.PRICE_GRID_MISMATCH["measured"]
    assert g["threeDecimalPrints"] == 350153
    assert g["twoDecimalPrints"] == 417644
    assert "answered by our choice" in \
        em.PRICE_GRID_MISMATCH["isNotFixableByRounding"]


def test_block_trades_are_measured_as_unpublished():
    assert em.BLOCK_TRADES_NOT_SEPARABLE["measured"][
        "blockTradePageHttpStatus"] == 404
    assert em.FEEDS_MEASURED[em.SOURCE_TAPE][
        "blockTradesPublishedRowByRow"] is False


def test_the_third_label_still_works_and_is_not_a_failure():
    m = em.matrix()
    assert "INTERVAL_CENSORED" in m["theThirdLabelStillWorks"]
    assert "honest answer" in m["theThirdLabelStillWorks"]


def test_what_would_change_it_changes_evidence_never_the_contract():
    w = em.what_would_change_it()
    assert "aggressor or side column" in w[
        "PRINT_WAS_ON_THE_SIDE_THAT_WOULD_CONSUME_US"]
    assert "out of scope" in w["THE_ONE_THAT_WOULD_SETTLE_EVERYTHING"]
    for v in w.values():
        assert "loosen" not in v.lower()


# ── the tape/market join, measured ───────────────────────────────────

def test_the_join_key_is_venue_native_and_the_zero_is_retention():
    j = em.TAPE_MARKET_JOIN
    assert j["TAPE_MARKET_JOIN_STATUS"] == \
        "KEY_CONFIRMED_OVERLAP_NOT_YET_AVAILABLE"
    assert "no price matching" in j["keyIsVenueNative"]
    assert "RETENTION, NOT NAMESPACE" in j["whyZero"]
    assert j["measured"]["bettorObservationsDated20260918"] == 0
    assert j["measured"]["bettorObservationsDated20260920"] == 7145


def test_shape_agreement_is_not_reported_as_proof():
    j = em.TAPE_MARKET_JOIN
    assert j["measured"]["matchedPremap"] == 0
    assert "NOT_PROVEN rather than assumed" in j["whatWasNotProven"]


def test_the_join_is_on_the_matrix():
    assert "TAPE_MARKET_JOIN" in em.matrix()
