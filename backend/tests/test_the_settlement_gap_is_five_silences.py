"""WHICH CONDITIONS LACK EVIDENCE, AND WHICH CONFLICT. Measured, not guessed.

TWO RETRACTIONS THIS FILE HOLDS IN PLACE.

I said the venue "does not publish" per-condition terms. I had tested eight
GUESSED paths; five 404s prove those paths wrong, not that no document
exists. `docs.polymarket.us/` answered 200 with 406 extractable words, so
the documentation site renders client-side and its contents were never read.

I also said one document would "clear 464 candidates". It would clear ONE
GATE. NO_ACTION_HAS_POSITIVE_NET_EDGE (445) and
EXECUTION_ESTIMATE_NOT_IDENTIFIED (442) are independent.

AND THE VENUE SAYS MORE THAN I CREDITED IT WITH. The real `description` is
183 characters, not the 380 I quoted from a different market, and the
comparison ACCEPTS two of its conditions. Nothing conflicts. The verdict is
UNKNOWN purely from silence, on five conditions, four of which are the
"started but did not finish normally" cases where a money line's action
actually turns.
"""

from __future__ import annotations

import json

from sportsassets import bettor_settlement_terms as ST

from .conftest import backend_path

FIXTURE = backend_path("tests", "fixtures",
                       "pmus_settled_market_2026_09_24_az_col.json")

SILENT = (
    "COMPLETED_IN_REGULATION",
    "CALLED_AND_GRADED_WITHOUT_RESUMPTION_AFTER_THE_MINIMUM",
    "STOPPED_BEFORE_THE_MINIMUM",
    "SUSPENDED_AND_RESUMED_WITHIN_THE_PUBLISHED_WINDOW",
    "SUSPENDED_TO_RESUME_BEYOND_THE_PUBLISHED_WINDOW",
)
MATCHED = ("DECIDED_AFTER_REGULATION",
           "POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED")


def _prose():
    with open(FIXTURE) as fh:
        return json.load(fh)["listing_market"]["description"]


def _compare():
    return ST.compare_prose(
        sport_family="baseball", market="h2h", venue_prose=_prose(),
        observed_at=1758800000.0, start_at=1758810000.0,
        quote_is_in_play=False, phase=ST.PHASE_REGULAR,
        game_format=ST.FMT_NINE)


def test_the_real_description_is_183_chars_not_380():
    """The number I reported came from a different market."""
    p = _prose()
    assert len(p) == 183, len(p)
    assert "including any extra innings" in p
    assert "void and stakes are returned" in p


def test_the_contract_carries_no_asset_price_terms():
    """The field that would naturally hold per-contract terms is null --
    which is a more specific question to ask the venue than "where are your
    rules"."""
    with open(FIXTURE) as fh:
        m = json.load(fh)["listing_market"]
    assert "assetPriceTerms" in m
    assert m["assetPriceTerms"] is None


def test_nothing_conflicts():
    """The distinction that matters: INCOMPATIBLE could not be fixed by
    more reading. UNKNOWN from silence can."""
    got = _compare()
    assert got["mismatched_conditions"] == []
    assert got["verdict"] == "UNKNOWN"


def test_two_conditions_already_agree():
    got = _compare()["per_condition"]
    for cond in MATCHED:
        assert got[cond]["verdict"] == "MATCH", (cond, got[cond])
        assert got[cond]["book_payout"] == got[cond]["venue_payout"]


def test_exactly_five_conditions_are_venue_silent():
    got = _compare()
    assert sorted(got["unstated_conditions"]) == sorted(SILENT), \
        got["unstated_conditions"]
    for cond in SILENT:
        row = got["per_condition"][cond]
        assert row["verdict"] == "VENUE_STATES_NO_RULE_FOR_THIS_CONDITION"
        # THE BOOK SIDE IS COMPLETE. The gap is one-sided, so the question
        # to ask is the venue's alone.
        assert row["book_payout"], cond
        assert row["venue_payout"] is None, cond


def test_the_book_side_is_complete_on_all_seven():
    got = _compare()["per_condition"]
    assert len(got) == 7
    assert all(r["book_payout"] for r in got.values())


def test_four_of_the_five_are_the_did_not_finish_normally_cases():
    """Not an arbitrary set: these are where a money line's action turns,
    and they are what a 183-character blurb does not address."""
    started_but_unfinished = [c for c in SILENT
                              if c != "COMPLETED_IN_REGULATION"]
    assert len(started_but_unfinished) == 4
    got = _compare()["per_condition"]
    for cond in started_but_unfinished:
        assert got[cond]["venue_payout"] is None


def test_the_reader_gap_candidate_is_recorded_and_not_applied():
    """COMPLETED_IN_REGULATION reads silent although the sentence plainly
    covers a game that finished in regulation -- `read_terms` attributed it
    to DECIDED_AFTER_REGULATION alone, on the "extra innings" phrase.

    ATTRIBUTING ONE SENTENCE TO TWO CONDITIONS IS A WIDENING OF SETTLEMENT
    COMPATIBILITY. It is left unapplied deliberately, so this asserts the
    CURRENT behaviour -- if somebody makes that change, this test fails and
    forces the decision to be a deliberate one.
    """
    got = _compare()["per_condition"]
    assert got["COMPLETED_IN_REGULATION"]["venue_payout"] is None
    assert got["DECIDED_AFTER_REGULATION"]["venue_payout"] == \
        ST.PAY_ON_FINAL


def test_silence_is_still_not_agreement():
    """The rule that produces UNKNOWN. It is not relaxed anywhere here."""
    assert _compare()["silence_is_not_agreement"] is True
