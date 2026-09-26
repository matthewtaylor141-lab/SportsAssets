"""NO_TRADE MEANS TWO DIFFERENT THINGS AND THE CENSUS COULD NOT TELL THEM APART.

A cycle that ends NO_TRADE is either

  * the engine EVALUATED every candidate and declined them -- no positive
    edge, a rail with no headroom, a stated payout conflict, a quote whose
    age was measured and too old. A working engine reporting nothing to do.

  * the engine COULD NOT EVALUATE them -- a book it never read, a fair value
    it could not establish, a contract whose period was never bound. Not an
    absence of opportunity: an absence of an answer.

Those need opposite actions, so they must never render the same. This file
pins the classification, the cycle verdict, and the two rules that keep it
honest: a genuine payout conflict stays a DECISION, and an unknown refusal
code is reported as DRIFT rather than folded into either bucket.
"""

from __future__ import annotations

from sportsassets import bettor_external_shadow as ext

PROD_CENSUS = (
    [["VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE"]] * 24
    + [["QUOTE_STALE"]] * 19
    + [["VENUE_BOOK_READ_RETURNED_ERROR"]] * 4
    + [["NO_VENUE_NATIVE_CONTRACT_IN_PREMAP"]] * 2
    + [["VENUE_QUOTE_STALE"]] * 2
    + [["VENUE_BOOK_READ_FAILED"]] * 2)


def test_a_measured_stale_quote_is_a_decision_not_an_inability():
    """The clock was read and the price was too old. Nothing was missing."""
    for code in ("QUOTE_STALE", "VENUE_BOOK_STALE", "VENUE_QUOTE_STALE"):
        assert ext.evaluability([code]) == ext.DECIDED, code
    # BUT AN UNMEASURED CLOCK IS AN INABILITY, and that is the whole point
    # of keeping the two apart.
    assert ext.evaluability(["ONE_CLOCK_IS_NOT_MEASURED"]) == \
        ext.COULD_NOT_EVALUATE


def test_a_stated_payout_conflict_stays_a_decision():
    """THE RULE THAT MUST NOT BE RELAXED. Both sides published a rule and the
    payouts differ. It is a refusal on real evidence, and reclassifying it as
    a capability gap would make the funnel look better by pretending the
    conflict was our ignorance."""
    for code in ("SETTLEMENT_TERMS_CONFLICT",
                 "VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE",
                 "OVERTIME_RULE_CONFLICTS_WITH_BOOK_RULE"):
        assert ext.evaluability([code]) == ext.DECIDED, code
    # while a rule NOBODY stated is an inability: more reading could fix it
    for code in ("VOID_ABANDONMENT_RULE_NOT_ESTABLISHED",
                 "OVERTIME_RULE_NOT_ESTABLISHED",
                 "SETTLEMENT_SCOPE_NOT_ESTABLISHED",
                 "UNRESOLVED_SETTLEMENT_SEMANTICS"):
        assert ext.evaluability([code]) == ext.COULD_NOT_EVALUATE, code


def test_a_venue_or_provider_failure_is_named_as_theirs():
    for code in ("VENUE_BOOK_READ_FAILED", "VENUE_BOOK_READ_RETURNED_ERROR",
                 "VENUE_BOOK_NOT_READ", "NO_VENUE_CONTRACT_FOR_EVENT",
                 "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP",
                 "VENUE_DOES_NOT_LIST_THIS_FIXTURE"):
        assert ext.evaluability([code]) == ext.EXTERNAL_DEPENDENCY, code


def test_the_worst_case_wins_when_a_candidate_carries_several():
    """An edge computed from an input that was never established is not an
    edge, so the inability outranks the decision."""
    assert ext.evaluability(["NO_ACTION_HAS_POSITIVE_NET_EDGE",
                             "INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED"]) == \
        ext.COULD_NOT_EVALUATE
    assert ext.evaluability(["NO_ACTION_HAS_POSITIVE_NET_EDGE",
                             "VENUE_BOOK_READ_FAILED"]) == \
        ext.EXTERNAL_DEPENDENCY
    assert ext.evaluability(["QUOTE_STALE", "A_CODE_NOBODY_DECLARED"]) == \
        ext.EVALUABILITY_UNCLASSIFIED
    assert ext.evaluability([]) is None


def test_every_classified_code_is_one_this_lane_can_actually_emit():
    """A classification table that drifts from the lane is worse than none.
    Every key must appear in the lane's own stage map or in the modules that
    raise it."""
    import pathlib

    root = pathlib.Path(ext.__file__).resolve().parent
    blob = "\n".join(f.read_text(errors="ignore") for f in root.rglob("*.py")
                     if "__pycache__" not in str(f))
    missing = [c for c in ext.EVALUABILITY_OF if '"%s"' % c not in blob]
    assert missing == [], missing


def test_the_cycle_verdict_separates_the_two_cases():
    # EVERY candidate judged: a working engine with nothing to do
    v = ext.cycle_evaluability([["QUOTE_STALE"]] * 3)
    assert v["verdict"] == ext.V_NO_OPPORTUNITY
    assert v["evaluated_to_a_judgement"] == 3
    assert v["could_not_be_evaluated"] == 0

    # NOTHING judged, all on someone else's input
    v = ext.cycle_evaluability([["VENUE_BOOK_READ_FAILED"]] * 3)
    assert v["verdict"] == ext.V_BLOCKED_EXTERNALLY
    assert "not a statement about opportunity" in v["why"]

    # NOTHING judged, all on our own missing inputs
    v = ext.cycle_evaluability([["VENUE_MARKET_SCOPE_NOT_ESTABLISHED"]] * 3)
    assert v["verdict"] == ext.V_EVALUATION_INCOMPLETE

    # an admission outranks everything
    v = ext.cycle_evaluability([[], ["QUOTE_STALE"]])
    assert v["verdict"] == ext.V_ADMITTED
    assert v["admitted"] == 1

    # nothing even reached the mapping
    v = ext.cycle_evaluability([])
    assert v["verdict"] == ext.V_NOTHING_REACHED_EVALUATION

    # AND A DRIFTED CODE IS ITS OWN VERDICT -- never somebody else's fault
    # and never "no opportunity".
    v = ext.cycle_evaluability([["A_CODE_NOBODY_DECLARED"]])
    assert v["verdict"] == ext.V_CLASSIFICATION_HAS_DRIFTED
    assert v["unclassified_codes"] == ["A_CODE_NOBODY_DECLARED"]


def test_the_real_production_census_reads_proportionately():
    """THE CENSUS FROM THE LAST COMPLETED PRODUCTION CYCLE. 45 of 53
    candidates were judged and 8 could not be evaluated, so neither "no
    opportunity" nor "blocked" is the honest headline -- both counts are."""
    v = ext.cycle_evaluability(PROD_CENSUS)
    assert v["candidates"] == 53
    assert v["admitted"] == 0
    assert v["evaluated_to_a_judgement"] == 45
    assert v["could_not_be_evaluated"] == 8
    assert v["counts"][ext.EXTERNAL_DEPENDENCY] == 8
    assert v["counts"][ext.COULD_NOT_EVALUATE] == 0
    assert v["verdict"] == ext.V_NO_OPPORTUNITY_WITH_GAPS
    assert "that part of the engine worked" in v["why"]
    assert "NOTHING about opportunity" in v["why"]


def test_the_desk_reports_the_verdict_with_its_counts():
    import inspect

    from sportsassets.api import app as A

    src = inspect.getsource(A.bettor_desk)
    assert "ext.cycle_evaluability(" in src
    assert 'opps["evaluability"]' in src
    assert 'opps["per_candidate_evaluability"]' in src
    # and it says why the counts are there
    assert "could not evaluate" in src
