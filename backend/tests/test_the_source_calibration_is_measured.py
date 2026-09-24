"""THE CALIBRATION IS MEASURED, AND A SHORTFALL IS A RESULT.

A table plus a hand-written passing row is not a measurement. These tests
pin the properties that make this one: the scope, the metric and the
acceptance criteria are fixed before any data is read; the probability
scored is the one recorded at decision time; twenty-four quotes about one
coin flip count once AND the two sides of one fixture count once; void,
unresolved and unverified events are excluded AND counted; and an
insufficient sample cannot produce the row the risk gate reads.

THREE OF THESE TESTS ARE THE REVIEW'S OWN COUNTEREXAMPLES, kept as tests
because they are the cases the released evaluator admitted:

  * 300 forecasts all saying 0.60 on fixtures that won 90% of the time
  * 150 fixtures counted as 300 "unique events" via both payouts
  * an outcome recorded by the uncorrected venue-side mapping
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_source_calibration as CAL
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

VERIFIED = "VENUE_SETTLEMENT_PRICE"


def _row(i, *, p=0.7, outcome=1, known=True, event=None, at=None,
         family="baseball", market="h2h", method=None, version=None,
         basis=VERIFIED, payout="HOME"):
    return {"id": i,
            "version": version or CAL.SOURCE_VERSION,
            "devig_method": method or CAL.SOURCE_METHOD,
            "sport_family": family, "market": market,
            "event_key": event or ("e%d" % i), "payout_event": payout,
            "probability": p, "outcome_known": known, "outcome": outcome,
            "outcome_basis": basis,
            "observed_at_epoch": float(i if at is None else at)}


#: Enough resolved fixtures that the held-out third still leaves the
#: declared evaluation minimum. The baseline costs sample, and the tests
#: pay it rather than lowering the bar.
def _enough(extra=6):
    return CAL.MIN_RESOLVED_EVENTS * 3 // 2 + extra


def _many(n, p, outcome_fn, basis=VERIFIED):
    return [_row(i, p=p, outcome=outcome_fn(i), event="ev%d" % i,
                 basis=basis)
            for i in range(n)]


# ── the declaration ──────────────────────────────────────────────────

def test_the_criteria_are_declared_and_the_ceiling_is_named_a_policy():
    a = CAL.ACCEPTANCE
    assert a["metric"] == "BRIER"
    assert a["all_required"] is True
    assert a["declared_before_any_data_was_read"] is True
    # FOUR CONDITIONS, NOT TWO. A ceiling and a sample size are satisfiable
    # by a badly miscalibrated forecaster on a book of favourites.
    assert set(a["conditions"]) == {
        "min_independent_fixtures", "brier_at_or_below_ceiling",
        "beats_the_held_out_baseline", "calibration_error_at_or_below"}
    # 0.25 IS A REFERENCE, NOT A BENCHMARK, and the declaration says which.
    assert a["reference_only"]["constant_half_brier"] == 0.25
    assert "NOT_A_UNIVERSAL_NO_SKILL_FLOOR" in \
        a["reference_only"]["what_it_is"]
    assert "POLICY_THRESHOLD" in a["the_ceiling_is"]
    # AND WHAT A PASS DOES NOT ESTABLISH IS PART OF THE DECLARATION.
    assert any("profitab" in s for s in a["what_a_pass_does_not_establish"])
    assert a["baseline"]["fitted_using_evaluation_outcomes"] is False
    assert CAL.SCOPE["source_version"] == CAL.SOURCE_VERSION
    assert CAL.SCOPE["market"] == "h2h"
    assert CAL.SCOPE["independent_unit"] == "ONE_FIXTURE"


def test_the_module_does_not_claim_to_measure_profitability():
    d = CAL.describe()
    assert d["measures_trading_profitability"] is False


def test_a_row_outside_the_declared_scope_is_excluded_and_counted():
    rows = ([_row(i) for i in range(5)]
            + [_row(100, market="spreads"), _row(101, family="tennis"),
               _row(102, method="multiplicative"),
               _row(103, version="SOME_OTHER_SOURCE_V9")])
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["rows_considered"] == 9
    assert got["rows_in_scope"] == 5
    assert got["rows_out_of_scope"] == 4


def test_no_rows_in_scope_is_its_own_status_not_a_score_of_zero():
    got = CAL.evaluate([_row(1, market="spreads")], measured_at=0.0)
    assert got["status"] == CAL.OUT_OF_SCOPE
    assert got["within_tolerance"] is False
    assert "score" not in got


# ── one FIXTURE, one observation ──────────────────────────────────────

def test_many_quotes_about_one_event_are_scored_once():
    """A source quoted every fifteen minutes for six hours produces
    twenty-four rows about one coin flip. Scoring all of them would report
    a sample twenty-four times larger than the evidence."""
    rows = [_row(i, event="same-event", at=i, p=0.6 + i * 0.01)
            for i in range(24)]
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["rows_in_scope"] == 24
    assert got["unique_fixtures"] == 1
    assert got["observations_collapsed"] == 23
    assert got["resolved_fixtures"] == 1


def test_the_probability_scored_is_the_first_one_recorded():
    """POINT IN TIME. Taking the last, or the best, would be choosing among
    a source's own revisions after seeing which way the event went."""
    rows = [_row(2, event="e", at=200.0, p=0.99),
            _row(1, event="e", at=100.0, p=0.55),
            _row(3, event="e", at=300.0, p=0.01)]
    picked = CAL.unique_fixtures(rows)
    assert len(picked) == 1
    assert list(picked.values())[0]["probability"] == 0.55


def test_both_sides_of_one_fixture_are_one_independent_observation():
    """THE REVIEW'S SECOND COUNTEREXAMPLE. 150 fixtures represented by both
    complementary payouts were counted as 300 "unique events". The two
    statements are p and 1-p against o and 1-o: identical squared errors
    and one coin flip's worth of information."""
    rows = []
    i = 0
    for f in range(150):
        rows.append(_row(i, p=0.70, outcome=1, event="f%d" % f,
                         payout="HOME", at=i))
        i += 1
        rows.append(_row(i, p=0.30, outcome=0, event="f%d" % f,
                         payout="NOT(HOME)", at=i))
        i += 1
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["payout_statements_in_scope"] == 300
    assert got["unique_fixtures"] == 150, (
        "two payout statements about one fixture are one fixture")
    assert got["resolved_fixtures"] == 150
    assert got["status"] == CAL.INSUFFICIENT
    assert got["within_tolerance"] is False


def test_which_payout_statement_survives_is_decided_by_the_clock():
    """Not by which one scored better. The earlier observation wins even
    when it is the losing side."""
    a = _row(1, event="e", at=10.0, p=0.7, outcome=0, payout="HOME")
    b = _row(2, event="e", at=20.0, p=0.3, outcome=1, payout="NOT(HOME)")
    picked = CAL.unique_fixtures([b, a])
    assert list(picked.values())[0]["id"] == 1


# ── resolved / void / unresolved / unverified ─────────────────────────

def test_void_unresolved_and_unverified_are_excluded_and_counted():
    rows = ([_row(i, outcome=1) for i in range(3)]
            + [dict(_row(10, known=False, outcome=None),
                    outcome_basis=CAL.VOID_BASIS),
               _row(11, known=True, outcome=7),
               _row(12, known=False, outcome=None, basis=None)])
    got = CAL.evaluate(rows, measured_at=0.0)
    c = got["outcome_counts"]
    assert c[CAL.RESOLVED] == 3
    assert c[CAL.VOID] == 1, "a void is established by its BASIS"
    assert c[CAL.UNRESOLVED] == 1
    assert c[CAL.UNVERIFIED] == 1, "an outcome of 7 is not a 0/1 truth"


def test_an_outcome_without_verified_provenance_is_not_scored():
    """THE REVIEW'S THIRD DEFECT, on the calibration side. Rows joined by
    the uncorrected venue-side mapping carry an outcome and no basis. They
    are wrong exactly on the short legs, so they are UNVERIFIED and
    excluded rather than quietly scored."""
    rows = [_row(i, outcome=1, basis=None) for i in range(5)]
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["outcome_counts"][CAL.UNVERIFIED] == 5
    assert got["outcome_counts"][CAL.RESOLVED] == 0
    assert got["resolved_fixtures"] == 0
    # AND AN INFERRED RESOLUTION IS NOT A VERIFIED ONE EITHER.
    inferred = [_row(i, outcome=1,
                     basis=loop.C_INFERRED) for i in range(5)]
    assert CAL.evaluate(inferred,
                        measured_at=0.0)["outcome_counts"][
                            CAL.UNVERIFIED] == 5


def test_a_resolved_event_with_no_probability_is_not_scored_as_zero():
    rows = [_row(1, p=None, outcome=1), _row(2, p=0.7, outcome=1)]
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["resolved_fixtures"] == 1
    assert got["outcome_counts"][CAL.UNRESOLVED] == 1
    assert got["outcome_counts"][CAL.RESOLVED] == 1


# ── the held-out baseline ─────────────────────────────────────────────

def test_the_baseline_is_fitted_on_fixtures_that_are_never_scored():
    n = _enough()
    rows = _many(n, 0.7, lambda i: 1 if i % 10 < 7 else 0)
    got = CAL.evaluate(rows, measured_at=0.0)
    split = got["baseline_split"]
    assert split["fit_fixtures"] + split["evaluation_fixtures"] == n
    assert split["fit_fixtures_are_scored"] is False
    assert split["order"].startswith("CHRONOLOGICAL")
    assert got["baseline"]["fitted_using_evaluation_outcomes"] is False
    assert got["scored_events"] == split["evaluation_fixtures"]
    # THE COST OF HOLDING OUT A BASELINE IS STATED, not hidden.
    assert split["total_resolved_fixtures_required"] > \
        CAL.MIN_RESOLVED_EVENTS


def test_without_enough_fixtures_to_fit_a_baseline_there_is_no_verdict():
    rows = _many(30, 0.7, lambda i: i % 2 == 0)
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["status"] == CAL.INSUFFICIENT
    assert got["baseline_established"] is False
    assert got["score"] is None
    assert "not a baseline" in got["why"]
    assert CAL.to_row(got, measured_by="t", window_start=0,
                      window_end=1)["write"] is False


def test_the_in_sample_base_rate_is_reported_and_named_inadmissible():
    n = _enough()
    rows = _many(n, 0.7, lambda i: 1 if i % 10 < 7 else 0)
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["in_sample_base_rate"] == pytest.approx(0.7, abs=0.03)
    assert "NOT_AN_ADMISSIBLE_BASELINE" in got["in_sample_base_rate_is"]


# ── the shortfall, which is the current answer ────────────────────────

def test_an_insufficient_sample_reports_the_shortfall_and_writes_nothing():
    rows = _many(200, 0.7, lambda i: 1 if i % 10 else 0)
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["status"] == CAL.INSUFFICIENT
    assert got["within_tolerance"] is False
    assert got["shortfall_events"] > 0
    assert got["shortfall_resolved_fixtures_total"] > 0
    # A PROVISIONAL SCORE IS REPORTED so collection can be watched, and it
    # is named provisional so nothing treats it as a verdict.
    assert got["score"] is not None
    assert got["score_is_provisional"] is True
    # AND IT CANNOT BECOME THE ROW THE RISK GATE READS.
    assert CAL.to_row(got, measured_by="t", window_start=0,
                      window_end=1)["write"] is False


def test_a_good_score_on_too_few_events_still_does_not_pass():
    """The sample minimum is not advisory: a perfect score on ten events is
    ten events."""
    rows = _many(10, 1.0, lambda i: 1)
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["status"] == CAL.INSUFFICIENT
    assert got["within_tolerance"] is False


# ── a completed verdict, and the counterexample it must refuse ────────

def test_a_forecast_that_ignores_the_base_rate_fails_despite_the_ceiling():
    """THE REVIEW'S FIRST COUNTEREXAMPLE, at full sample.

    Every forecast says 0.60 and 90% of the fixtures win. Brier is 0.18,
    comfortably inside the 0.24 ceiling -- and the source is wrong about
    every single probability it stated. A constant 0.90 scores 0.09.

    The released evaluator returned PASSED here. Two independent
    conditions now catch it: it does not beat the held-out baseline, and
    its reliability error is 0.30 against a bound of 0.05.
    """
    rows = _many(_enough(), 0.60, lambda i: 1 if i % 10 else 0)
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["status"] == CAL.FAILED, got["why"]
    assert got["within_tolerance"] is False
    assert got["score"] == pytest.approx(0.18, abs=0.02), (
        "the ceiling really is cleared -- that is the point")
    assert got["score"] <= CAL.BRIER_CEILING
    assert set(got["conditions_failed"]) == {
        "beats_the_held_out_baseline", "calibration_error_at_or_below"}
    assert got["baseline"]["probability"] == pytest.approx(0.9, abs=0.03)
    assert got["baseline_comparison"]["paired_mean_improvement"] < 0
    assert got["baseline_comparison"]["improvement_is_significant"] is False
    assert got["calibration"]["expected_calibration_error"] == \
        pytest.approx(0.30, abs=0.03)
    row = CAL.to_row(got, measured_by="t", window_start=0, window_end=1)
    assert row["write"] is True and row["within_tolerance"] is False


def test_a_calibrated_discriminating_source_passes_all_four_conditions():
    """A source that varies its probabilities, is right about them, and
    beats a constant forecast by more than sampling noise."""
    rows = []
    # DETERMINISTIC, not sampled: a fixed cycle of probabilities each
    # realised at exactly its stated frequency.
    grid = [(0.10, 10), (0.30, 10), (0.50, 10), (0.70, 10), (0.90, 10)]
    i = 0
    reps = (_enough() // 50) + 1
    for _ in range(reps):
        for p, k in grid:
            wins = int(round(p * k))
            for j in range(k):
                rows.append(_row(i, p=p, outcome=1 if j < wins else 0,
                                 event="ev%d" % i, at=i))
                i += 1
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["status"] == CAL.PASSED, got["why"]
    assert got["within_tolerance"] is True
    assert got["conditions_failed"] == []
    assert got["score_is_provisional"] is False
    # THE UNCERTAINTY IS REPORTED, not left to the reader to imagine.
    u = got["uncertainty"]
    assert u["brier_standard_error"] > 0
    assert u["brier_ci_low"] < got["score"] < u["brier_ci_high"]
    # THREE QUESTIONS, THREE ANSWERS.
    assert got["predictive_score"]["value"] == got["score"]
    d = got["predictive_score"]["decomposition"]
    # BRIER = RELIABILITY - RESOLUTION + UNCERTAINTY, so the three parts
    # must reconstruct the score they decompose.
    assert got["score"] == pytest.approx(
        d["reliability"] - d["resolution"] + d["uncertainty"], abs=1e-6)
    # AND A WELL-CALIBRATED SOURCE HAS A SMALL RELIABILITY TERM while
    # still carrying real resolution -- which is the distinction a single
    # Brier cannot make.
    assert d["reliability"] < 0.01
    assert d["resolution"] > 0.05
    assert got["calibration"]["measures"] == \
        "RELIABILITY_ONLY_NOT_DISCRIMINATION"
    assert got["trading_profitability"]["measured_here"] is False
    # AND THE COMPARISON IS PAIRED, WITH AN INTERVAL THAT EXCLUDES ZERO.
    c = got["baseline_comparison"]
    assert c["improvement_is_significant"] is True
    assert c["ci_low"] > 0
    row = CAL.to_row(got, measured_by="t", window_start=0, window_end=1)
    assert row["write"] is True and row["within_tolerance"] is True
    assert row["provenance"]["checks"]
    assert row["provenance"]["baseline"]["fitted_using_evaluation_outcomes"] \
        is False


def test_a_sufficient_badly_calibrated_sample_fails_and_is_still_written():
    """A FAILED verdict is a measurement and is recorded. What it must not
    do is open the gate."""
    rows = _many(_enough(), 0.9, lambda i: 1 if i % 10 < 4 else 0)
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["status"] == CAL.FAILED
    assert got["within_tolerance"] is False
    assert got["score"] > CAL.BRIER_CEILING
    row = CAL.to_row(got, measured_by="t", window_start=0, window_end=1)
    assert row["write"] is True
    assert row["within_tolerance"] is False


def test_a_result_from_another_evaluator_cannot_become_one_of_these_rows():
    """`to_row` is where a measurement becomes permission. A dict that
    carries a status and a score but not this evaluator's per-condition
    checks is not this evaluator's verdict."""
    forged = {"status": CAL.PASSED, "within_tolerance": True,
              "score": 0.10, "scored_events": 9999}
    assert CAL.to_row(forged, measured_by="t", window_start=0,
                      window_end=1)["write"] is False
    inconsistent = dict(forged, checks={"x": {"passed": False}},
                        conditions_failed=["x"])
    assert CAL.to_row(inconsistent, measured_by="t", window_start=0,
                      window_end=1)["write"] is False


def test_the_same_rows_give_the_same_answer():
    rows = _many(40, 0.7, lambda i: i % 3 == 0)
    a = CAL.evaluate(rows, measured_at=1.0)
    b = CAL.evaluate(list(reversed(rows)), measured_at=2.0)
    assert a["inputs_sha"] == b["inputs_sha"]
    assert a["provisional_brier"] == b["provisional_brier"]
    # AND DIFFERENT ROWS GIVE A DIFFERENT HASH -- a NEW fixture, not
    # another observation of one already there, which would be collapsed
    # and correctly leave the hash alone.
    extra = _row(999, p=0.5, outcome=1, event="a-new-fixture", at=999)
    c = CAL.evaluate(rows + [extra], measured_at=1.0)
    assert c["unique_fixtures"] == a["unique_fixtures"] + 1
    assert c["inputs_sha"] != a["inputs_sha"]
    # A SECOND QUOTE ON AN EXISTING FIXTURE CHANGES NOTHING.
    dupe = _row(1000, p=0.9, outcome=1, event=rows[0]["event_key"], at=5000)
    d = CAL.evaluate(rows + [dupe], measured_at=1.0)
    assert d["inputs_sha"] == a["inputs_sha"]


# ── the outcome join's settlement identity ───────────────────────────

def _res(price, status="RESOLVED"):
    return {"status": status, "settlement_price": price,
            "outcome": str(price)}


def test_the_settlement_maps_through_the_venue_side_not_the_complement():
    """THE REVIEW'S SECOND ITEM. `payout_is_complement` describes the
    PROBABILITY's source event and is FALSE on this lane even when the
    held exposure is the venue's SHORT side. Mapping settlement through it
    recorded outcome 1 for a short exposure that paid 0."""
    long_ = dict(buy_intent="ORDER_INTENT_BUY_LONG", ladder_side="ASK")
    short = dict(buy_intent="ORDER_INTENT_BUY_SHORT", ladder_side="BID")
    assert loop.outcome_from_settlement(_res(1.0), **long_)["outcome"] == 1
    assert loop.outcome_from_settlement(_res(1.0), **short)["outcome"] == 0
    assert loop.outcome_from_settlement(_res(0.0), **long_)["outcome"] == 0
    assert loop.outcome_from_settlement(_res(0.0), **short)["outcome"] == 1
    # THE MAPPING USED IS RECORDED, so a later reader can recheck it.
    assert loop.outcome_from_settlement(_res(1.0), **short)["side_map"] == \
        loop.SIDE_SHORT
    assert loop.outcome_from_settlement(_res(1.0), **long_)["basis"] == \
        loop.B_SETTLEMENT_PRICE


def test_a_venue_side_that_is_not_established_refuses_rather_than_guesses():
    got = loop.outcome_from_settlement(
        _res(1.0), buy_intent="ORDER_INTENT_BUY_SHORT", ladder_side="ASK")
    assert got["outcome"] is None
    assert got["class"] == loop.C_SIDE_UNKNOWN
    assert got["basis"] is None
    assert loop.venue_side_of_our_exposure(
        buy_intent="SOMETHING_ELSE", ladder_side="BID") is None


def test_four_non_outcomes_stay_four_different_answers():
    """A parse failure, a named winner, an inferred resolution and a
    confirmed void were all recorded as VOID -- permanently, because a
    joined row is never re-read."""
    long_ = dict(buy_intent="ORDER_INTENT_BUY_LONG", ladder_side="ASK")

    # A CONFIRMED VOID: the venue's own settlement endpoint, a parseable
    # price, and it paid neither side.
    void = loop.outcome_from_settlement(_res(0.5), **long_)
    assert void["outcome"] is None
    assert void["class"] == loop.B_CONFIRMED_VOID
    assert void["basis"] == loop.B_CONFIRMED_VOID

    # A NAMED WINNER is not a number and is not a void. `float()` used to
    # raise here and the caller recorded VOID for a resolved fixture.
    named = loop.outcome_from_settlement(
        {"status": "RESOLVED_DERIVED", "outcome": "Houston Astros"}, **long_)
    assert named["class"] == loop.C_INFERRED
    assert named["basis"] is None
    reported = loop.outcome_from_settlement(
        {"status": "RESOLVED", "outcome": "Houston Astros",
         "settlement_price": None}, **long_)
    assert reported["class"] == loop.C_NAMED_WINNER
    assert reported["basis"] is None
    assert reported["settlement_read"] == "Houston Astros"

    # AN UNPARSEABLE VALUE, and a missing one.
    for bad in (None, ""):
        got = loop.outcome_from_settlement(
            {"status": "RESOLVED", "outcome": bad,
             "settlement_price": None}, **long_)
        assert got["class"] == loop.C_UNPARSEABLE, bad
        assert got["basis"] is None

    # AND A PENDING READ IS NOT ANY OF THEM.
    pend = loop.outcome_from_settlement({"status": "PENDING"}, **long_)
    assert pend["class"].startswith("NOT_RESOLVED:")
    assert pend["basis"] is None


def test_an_inference_from_converged_prices_is_never_a_settlement():
    """RESOLVED_DERIVED means the market closed and its prices converged.
    The venue named nothing, so scoring a probability against it would
    make the calibration partly a measurement of our own inference."""
    got = loop.outcome_from_settlement(
        {"status": "RESOLVED_DERIVED", "outcome": "Seattle Mariners"},
        buy_intent="ORDER_INTENT_BUY_LONG", ladder_side="ASK")
    assert got["outcome"] is None
    assert got["basis"] is None
    assert got["class"] == loop.C_INFERRED
    assert got["class"] not in CAL.VERIFIED_BASES


def test_only_a_settled_or_confirmed_void_row_leaves_the_unjoined_queue():
    """THE QUEUE CONDITION AND THE SCOPE CONDITION ARE THE SAME CONDITION.
    A row leaves the join queue exactly when a basis is recorded for it,
    and `classify` scores it only when that basis is a verified one."""
    assert "outcome_basis IS NULL" in loop.UNJOINED_SQL
    assert "outcome_basis = $4" in loop.JOIN_RESOLVED_SQL
    assert "outcome_basis = $2" in loop.JOIN_VOID_SQL
    # THE ATTEMPT STAMP WRITES NO BASIS, deliberately.
    assert "outcome_basis" not in loop.JOIN_ATTEMPT_SQL.split("WHERE")[0]
    assert "settlement_read_at" in loop.JOIN_ATTEMPT_SQL


# ── against Postgres ─────────────────────────────────────────────────

@pg
@pytest.mark.asyncio
async def test_the_measurement_runs_against_the_real_table(monkeypatch):
    """END TO END on the real schema: the reader, the evaluator and the
    refusal to write an unearned row."""
    asyncpg = pytest.importorskip("asyncpg")

    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(open(
            "migrations/117_entry_lane_evidence_and_calibration.sql").read())
        await conn.execute(open(
            "migrations/118_outcome_join_provenance_and_audit.sql").read())
        exp = "CALIBRATION_TEST_EXPERIMENT"
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", exp)
        await conn.execute("DELETE FROM external_source_calibration "
                           "WHERE measured_by = 'PYTEST'")
        # Twelve fixtures, inserted WITHOUT an outcome and then joined by
        # UPDATE -- because the table's own CHECK enforces exactly that:
        # "an external valuation must be recorded BEFORE its outcome". A
        # calibration whose inputs could be written already-resolved would
        # not be measuring a forecast.
        ids = []
        for i in range(12):
            rid = await conn.fetchval(
                "INSERT INTO external_valuations (experiment_id, version, "
                "source_class, provider, book, devig_method, venue, "
                "condition_id, contract_selection, sport_family, market, "
                "event_key, probability, decision, admissible, refusals, "
                "why, order_submitted, observed_at, decided_at, "
                "payout_event, outcome_known, raw_odds, outcomes_priced, "
                "expected_outcomes) VALUES "
                "($1,$2,'EXTERNAL_BOOKMAKER_VALUATION','p','pinnacle',$3,"
                "'PMUS',$5,'HOME','baseball','h2h',$4,0.7,'NO_TRADE',"
                "FALSE,'{}','x',FALSE, now() - interval '3 days',"
                "now() - interval '3 days','HOME',FALSE,'{}'::jsonb,2,2) "
                "RETURNING id",
                exp, CAL.SOURCE_VERSION, CAL.SOURCE_METHOD,
                "ev%d" % i, "c%d" % i)
            ids.append(rid)
        # UNRESOLVED IS THE STATE BEFORE THE JOIN, and the measurement
        # says so rather than scoring a zero.
        import time as _t

        pre = await CAL.measure(conn, experiment_id=exp, days=90,
                                now=_t.time(), measured_by="PYTEST")
        assert pre["outcome_counts"][CAL.UNRESOLVED] == 12
        assert pre["resolved_fixtures"] == 0

        # NOW JOIN THE OUTCOMES, through the statement the loop uses --
        # WITH the provenance, because without it the evaluator correctly
        # refuses to score them.
        settled_at = _t.time()
        for k, rid in enumerate(ids):
            await conn.execute(
                loop.JOIN_RESOLVED_SQL, rid, 1 if k % 3 else 0, settled_at,
                "VENUE_SETTLEMENT_PRICE", loop.SIDE_LONG,
                "1" if k % 3 else "0")
        got = await CAL.measure(conn, experiment_id=exp, days=90,
                                now=_t.time(), measured_by="PYTEST")
        assert got["ran"] is True
        assert got["rows_in_scope"] == 12
        assert got["unique_fixtures"] == 12
        assert got["outcome_counts"][CAL.RESOLVED] == 12
        assert got["status"] == CAL.INSUFFICIENT
        assert got["written"] is False
        assert got["would_write"] is False
        # AND NO ROW EXISTS, so the gate is exactly as shut as before.
        n = await conn.fetchval(
            "SELECT count(*) FROM external_source_calibration "
            "WHERE measured_by = 'PYTEST'")
        assert n == 0

        # THE SAME TWELVE ROWS, JOINED WITHOUT PROVENANCE, ARE NOT SCORED.
        await conn.execute(
            "UPDATE external_valuations SET outcome_basis = NULL "
            "WHERE experiment_id = $1", exp)
        bare = await CAL.measure(conn, experiment_id=exp, days=90,
                                 now=_t.time(), measured_by="PYTEST")
        assert bare["outcome_counts"][CAL.UNVERIFIED] == 12
        assert bare["outcome_counts"][CAL.RESOLVED] == 0
    finally:
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = 'CALIBRATION_TEST_"
                           "EXPERIMENT'")
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_audit_reopens_rows_joined_by_the_uncorrected_mapping():
    """MIGRATION 118'S AUDIT, exercised. A row whose outcome was set while
    the complement mapping was in force is reopened with its prior value
    preserved, re-enters the join queue, and stays out of calibration
    until the corrected mapper records a basis for it."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_external_shadow as ext

    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(open(
            "migrations/117_entry_lane_evidence_and_calibration.sql").read())
        await conn.execute(open(
            "migrations/118_outcome_join_provenance_and_audit.sql").read())
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE condition_id = 'c-audit-1'")
        rid = await conn.fetchval(
            "INSERT INTO external_valuations (experiment_id, version, "
            "source_class, provider, book, devig_method, venue, "
            "condition_id, contract_selection, sport_family, market, "
            "event_key, probability, decision, admissible, refusals, why, "
            "order_submitted, observed_at, decided_at, payout_event, "
            "outcome_known, raw_odds, outcomes_priced, expected_outcomes, "
            "us_market_slug, buy_intent, ladder_side) VALUES "
            "($1,$2,'EXTERNAL_BOOKMAKER_VALUATION','p','pinnacle',$3,"
            "'PMUS','c-audit-1','HOME','baseball','h2h','ev-audit',0.7,"
            "'NO_TRADE',FALSE,'{}','x',FALSE, now() - interval '3 days',"
            "now() - interval '3 days','HOME',FALSE,'{}'::jsonb,2,2,"
            "'slug-audit','ORDER_INTENT_BUY_SHORT','BID') RETURNING id",
            ext.EXPERIMENT_ID, CAL.SOURCE_VERSION, CAL.SOURCE_METHOD)
        # THE WRONG ANSWER, as the released join would have written it: the
        # venue's YES settled at 1, the exposure is SHORT, and the old
        # mapper recorded 1 because payout_is_complement was false.
        import time as _t

        await conn.execute(ext.JOIN_OUTCOME, rid, 1, _t.time(), None)
        assert await conn.fetchval(
            "SELECT outcome FROM external_valuations WHERE id = $1",
            rid) == 1

        # THE AUDIT. Migration 118 is idempotent in shape but its UPDATE is
        # what reopens the row, so it is replayed here on purpose.
        await conn.execute(open(
            "migrations/118_outcome_join_provenance_and_audit.sql").read())
        row = await conn.fetchrow(
            "SELECT outcome_known, outcome, outcome_at, outcome_basis, "
            "outcome_audit FROM external_valuations WHERE id = $1", rid)
        assert row["outcome_known"] is False
        assert row["outcome"] is None and row["outcome_at"] is None
        assert row["outcome_basis"] is None, (
            "no basis means out of calibration scope")
        # THE PROVENANCE IS PRESERVED, which is what makes it an audit and
        # not a deletion.
        assert "prior outcome=1" in row["outcome_audit"]
        assert "payout_is_complement" in row["outcome_audit"]

        # AND IT IS BACK IN THE JOIN QUEUE.
        queued = await conn.fetch(loop.UNJOINED_SQL, ext.EXPERIMENT_ID, 50)
        assert rid in [q["id"] for q in queued]
        # THE QUEUE HANDS THE JOIN THE VENUE-SIDE IDENTITY IT NEEDS.
        me = [q for q in queued if q["id"] == rid][0]
        assert me["buy_intent"] == "ORDER_INTENT_BUY_SHORT"
        assert me["ladder_side"] == "BID"
        # THE CORRECTED MAPPER GIVES THE OTHER ANSWER.
        got = loop.outcome_from_settlement(
            {"status": "RESOLVED", "settlement_price": 1.0, "outcome": "1"},
            buy_intent=me["buy_intent"], ladder_side=me["ladder_side"])
        assert got["outcome"] == 0, "a short exposure on a YES that paid 1"
    finally:
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE condition_id = 'c-audit-1'")
        await conn.close()
