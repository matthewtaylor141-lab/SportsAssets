"""§4: split the 464 into the four classes, from the comparisons themselves.

`classify_census` reads refusal CODE counts, and the code every one of the
464 carries does not say which side was silent -- so all 464 landed in C4,
"unresolved for a reason none of the above names". That was read as an
external blocker. It is not: `bettor_settlement_terms.compare` has always
decided per condition between a mismatch, a venue silence, a book silence
and a mutual silence, and those four are exactly C1/C3/C2/C4.

The load-bearing test is `test_the_464s_recorded_shape_decomposes_to_c3`:
it reconstructs the shape the register records for the 464 (seven
applicable conditions for a baseball money line, the book side captured
with citations, the venue's 380-character description stating one) and
shows the class is C3 -- our read of a publication that exists -- rather
than C4.
"""

import pytest

from sportsassets import bettor_settlement_terms as ST
from sportsassets import settlement_taxonomy as TX


WINDOW = {"from": "2026-09-26T00:00:00Z", "to": "2026-09-27T00:00:00Z"}
BUILDS = ["ad95d69"]


def _per(**verdicts):
    return {c: {"verdict": v} for c, v in verdicts.items()}


# ═════════════════════════════════════════════════════════════════════
# THE FOUR VERDICTS MAP ONTO THE FOUR CLASSES
# ═════════════════════════════════════════════════════════════════════

def test_a_mismatch_is_an_established_payout_conflict():
    r = TX.classify_comparison(_per(C=ST.V_MISMATCH))
    assert r["candidate_class"] == TX.ESTABLISHED_PAYOUT_CONFLICT


def test_a_venue_silence_is_c3_not_c4():
    r = TX.classify_comparison(_per(C=ST.V_VENUE_SILENT))
    assert r["candidate_class"] == TX.VENUE_RULE_OR_SCOPE_NOT_HELD


def test_a_book_silence_is_c2_not_c4():
    r = TX.classify_comparison(_per(C=ST.V_BOOK_SILENT))
    assert r["candidate_class"] == TX.BOOK_RULE_NOT_HELD


def test_a_mutual_silence_is_the_only_one_that_is_genuinely_c4():
    r = TX.classify_comparison(_per(C=ST.V_BOTH_SILENT))
    assert r["candidate_class"] == TX.OTHER_UNRESOLVED


def test_a_match_is_not_a_refusal_and_enters_no_class():
    r = TX.classify_comparison(_per(C=ST.V_MATCH))
    assert r["candidate_class"] is None
    assert r["established_conditions"] == ["C"]
    assert r["conditions_by_class"] == {}


# ═════════════════════════════════════════════════════════════════════
# PRECEDENCE
# ═════════════════════════════════════════════════════════════════════

def test_an_established_conflict_dominates_every_silence():
    """No amount of capture dissolves a conflict, so it must win."""
    r = TX.classify_comparison(_per(A=ST.V_MISMATCH, B=ST.V_VENUE_SILENT,
                                    C=ST.V_BOOK_SILENT, D=ST.V_BOTH_SILENT))
    assert r["candidate_class"] == TX.ESTABLISHED_PAYOUT_CONFLICT


def test_needing_both_sides_is_its_own_class_not_forced_into_one():
    r = TX.classify_comparison(_per(A=ST.V_VENUE_SILENT, B=ST.V_BOOK_SILENT))
    assert r["candidate_class"] == TX.NEEDS_BOTH_SIDES
    assert r["candidate_class"] not in (TX.BOOK_RULE_NOT_HELD,
                                        TX.VENUE_RULE_OR_SCOPE_NOT_HELD)


def test_needing_both_ranks_worse_than_needing_one():
    p = TX.CANDIDATE_PRECEDENCE
    assert p.index(TX.NEEDS_BOTH_SIDES) < p.index(
        TX.VENUE_RULE_OR_SCOPE_NOT_HELD)
    assert p.index(TX.NEEDS_BOTH_SIDES) < p.index(TX.BOOK_RULE_NOT_HELD)


def test_a_mutual_silence_beside_a_one_sided_one_reports_the_one_sided():
    """The capture that unblocks it is the named side's."""
    r = TX.classify_comparison(_per(A=ST.V_VENUE_SILENT, B=ST.V_BOTH_SILENT))
    assert r["candidate_class"] == TX.VENUE_RULE_OR_SCOPE_NOT_HELD
    assert r["conditions_by_class"][TX.OTHER_UNRESOLVED] == ["B"]


def test_an_unrecognised_verdict_is_named_not_folded_into_c4():
    r = TX.classify_comparison({"A": {"verdict": "SOMETHING_NEW"}})
    assert r["candidate_class"] == TX.UNCLASSIFIED
    assert r["unrecognised_verdicts"] == {"A": "SOMETHING_NEW"}
    assert TX.OTHER_UNRESOLVED not in r["conditions_by_class"]


# ═════════════════════════════════════════════════════════════════════
# THE 464's RECORDED SHAPE
# ═════════════════════════════════════════════════════════════════════

def test_baseball_h2h_has_the_seven_conditions_the_register_records():
    conds = ST.applicable_conditions(sport_family="baseball", market="h2h")
    assert len(conds) == 7


def test_the_464s_recorded_shape_decomposes_to_c3():
    """Book side captured; venue's 380-char description states one of seven.

    This is the correction: the class is C3 -- the VENUE's rule is not
    established, which is our read of a publication that exists -- not
    C4, which would mean we cannot name why it is open.
    """
    conds = ST.applicable_conditions(sport_family="baseball", market="h2h")
    book = {c: "ACTION" for c in conds}
    venue = {conds[0]: "ACTION"}
    cmp_ = ST.compare(book=book, venue=venue, conditions=conds)
    assert cmp_["verdict"] == ST.UNKNOWN
    assert len(cmp_["unstated_conditions"]) == 6

    r = TX.classify_comparison(cmp_["per_condition"])
    assert r["candidate_class"] == TX.VENUE_RULE_OR_SCOPE_NOT_HELD
    assert len(r["conditions_by_class"][
        TX.VENUE_RULE_OR_SCOPE_NOT_HELD]) == 6
    assert r["established_conditions"] == [conds[0]]


def test_the_code_based_census_still_puts_that_same_shape_in_c4():
    """The two disagree, and the comparison-based one is the correct one."""
    census = TX.classify_census(
        {"VOID_ABANDONMENT_RULE_NOT_ESTABLISHED": 464},
        candidates=464, window=WINDOW, builds=BUILDS)
    assert census["ok"] is True
    assert TX.OTHER_UNRESOLVED in census["by_class"]
    assert census["established_payout_conflicts_at_most"] == 0


def test_a_whole_census_of_the_464_shape_lands_in_c3():
    conds = ST.applicable_conditions(sport_family="baseball", market="h2h")
    book = {c: "ACTION" for c in conds}
    venue = {conds[0]: "ACTION"}
    per = ST.compare(book=book, venue=venue,
                     conditions=conds)["per_condition"]
    out = TX.decompose_census([per] * 464, window=WINDOW, builds=BUILDS)
    assert out["ok"] is True
    assert out["candidates"] == 464
    assert out["by_candidate_class"] == {
        TX.VENUE_RULE_OR_SCOPE_NOT_HELD: 464}
    assert out["by_condition_class"][
        TX.VENUE_RULE_OR_SCOPE_NOT_HELD][conds[1]] == 464


# ═════════════════════════════════════════════════════════════════════
# THE CENSUS ITSELF
# ═════════════════════════════════════════════════════════════════════

def test_every_candidate_lands_in_exactly_one_candidate_class():
    rows = [_per(A=ST.V_MISMATCH),
            _per(A=ST.V_VENUE_SILENT),
            _per(A=ST.V_BOOK_SILENT),
            _per(A=ST.V_BOTH_SILENT),
            _per(A=ST.V_VENUE_SILENT, B=ST.V_BOOK_SILENT)]
    out = TX.decompose_census(rows, window=WINDOW, builds=BUILDS)
    assert sum(out["by_candidate_class"].values()) == 5
    assert out["by_candidate_class"] == {
        TX.ESTABLISHED_PAYOUT_CONFLICT: 1,
        TX.VENUE_RULE_OR_SCOPE_NOT_HELD: 1,
        TX.BOOK_RULE_NOT_HELD: 1,
        TX.OTHER_UNRESOLVED: 1,
        TX.NEEDS_BOTH_SIDES: 1,
    }


def test_the_condition_census_counts_cells_not_candidates():
    """One candidate with six venue-silent conditions is six cells, one row."""
    rows = [_per(**{"c%d" % i: ST.V_VENUE_SILENT for i in range(6)})]
    out = TX.decompose_census(rows, window=WINDOW, builds=BUILDS)
    assert out["by_candidate_class"] == {TX.VENUE_RULE_OR_SCOPE_NOT_HELD: 1}
    assert sum(out["by_condition_class"][
        TX.VENUE_RULE_OR_SCOPE_NOT_HELD].values()) == 6


def test_fully_established_candidates_are_counted_separately():
    out = TX.decompose_census([_per(A=ST.V_MATCH, B=ST.V_MATCH)],
                              window=WINDOW, builds=BUILDS)
    assert out["candidates_with_every_condition_established"] == 1
    assert out["by_candidate_class"] == {}


def test_the_counts_are_exact_rather_than_upper_bounds():
    out = TX.decompose_census([_per(A=ST.V_VENUE_SILENT)],
                              window=WINDOW, builds=BUILDS)
    assert "exactly one candidate class" in out[
        "counts_are_exact_not_upper_bounds"]


def test_a_census_without_a_window_is_refused():
    out = TX.decompose_census([_per(A=ST.V_VENUE_SILENT)], builds=BUILDS)
    assert out["ok"] is False
    assert out["refusal"] == "CENSUS_WINDOW_NOT_STATED"


def test_a_census_without_builds_is_refused():
    out = TX.decompose_census([_per(A=ST.V_VENUE_SILENT)], window=WINDOW)
    assert out["ok"] is False
    assert out["refusal"] == "CENSUS_BUILDS_NOT_STATED"


def test_the_remedies_travel_with_the_census():
    out = TX.decompose_census([_per(A=ST.V_VENUE_SILENT)],
                              window=WINDOW, builds=BUILDS)
    assert TX.VENUE_RULE_OR_SCOPE_NOT_HELD in out["remedies"]


# ═════════════════════════════════════════════════════════════════════
# WHAT RECLASSIFICATION DOES NOT ESTABLISH
# ═════════════════════════════════════════════════════════════════════

def test_reclassification_is_labelled_as_an_evidence_state_not_a_forecast():
    r = TX.classify_comparison(_per(A=ST.V_VENUE_SILENT))
    assert r["this_is"] == "A_CLASSIFICATION_OF_OUR_EVIDENCE_STATE"
    assert "may resolve compatible" in r["this_is_not"]
    assert "C1 conflict" in r["this_is_not"]


def test_the_census_states_that_nothing_is_admitted_by_reclassifying():
    out = TX.decompose_census([_per(A=ST.V_VENUE_SILENT)],
                              window=WINDOW, builds=BUILDS)
    note = out["what_reclassification_does_not_mean"]
    assert "does not admit a candidate" in note
    assert "does not lower any qualification requirement" in note


def test_moving_out_of_c4_does_not_change_the_number_of_admissions():
    """The decomposition is diagnostic. It admits nothing.

    Both the code census and the comparison census describe candidates
    that REFUSE. A candidate in C3 is as refused as one in C4.
    """
    conds = ST.applicable_conditions(sport_family="baseball", market="h2h")
    book = {c: "ACTION" for c in conds}
    venue = {conds[0]: "ACTION"}
    cmp_ = ST.compare(book=book, venue=venue, conditions=conds)
    assert cmp_["verdict"] != ST.COMPATIBLE
    r = TX.classify_comparison(cmp_["per_condition"])
    assert r["candidate_class"] == TX.VENUE_RULE_OR_SCOPE_NOT_HELD
    # Still refused. The class names the remedy, not an admission.
    assert cmp_["verdict"] == ST.UNKNOWN
