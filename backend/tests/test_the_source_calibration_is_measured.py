"""THE CALIBRATION IS MEASURED, AND A SHORTFALL IS A RESULT.

A table plus a hand-written passing row is not a measurement. These tests
pin the properties that make this one: the scope, the metric and the
acceptance criterion are fixed before any data is read; the probability
scored is the one recorded at decision time; twenty-four quotes about one
coin flip count once; void and unresolved events are excluded AND counted;
and an insufficient sample cannot produce the row the risk gate reads.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_source_calibration as CAL
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")


def _row(i, *, p=0.7, outcome=1, known=True, event=None, at=None,
         family="baseball", market="h2h", method=None, version=None):
    return {"id": i,
            "version": version or CAL.SOURCE_VERSION,
            "devig_method": method or CAL.SOURCE_METHOD,
            "sport_family": family, "market": market,
            "event_key": event or ("e%d" % i), "payout_event": "HOME",
            "probability": p, "outcome_known": known, "outcome": outcome,
            "observed_at_epoch": float(i if at is None else at)}


# ── the declaration ──────────────────────────────────────────────────

def test_the_criterion_is_declared_and_says_where_its_number_comes_from():
    a = CAL.ACCEPTANCE
    assert a["metric"] == "BRIER"
    assert a["both_required"] is True
    assert a["declared_before_any_data_was_read"] is True
    # A CEILING WITHOUT A BENCHMARK IS A TASTE. The no-skill reference is
    # part of the declaration so the ceiling can be judged against it.
    assert a["reference_no_skill_brier"] == 0.25
    assert a["ceiling"] < a["reference_no_skill_brier"]
    assert a["min_resolved_events"] >= 100
    assert CAL.SCOPE["source_version"] == CAL.SOURCE_VERSION
    assert CAL.SCOPE["market"] == "h2h"


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


# ── one event, one observation ────────────────────────────────────────

def test_many_quotes_about_one_event_are_scored_once():
    """A source quoted every fifteen minutes for six hours produces
    twenty-four rows about one coin flip. Scoring all of them would report
    a sample twenty-four times larger than the evidence."""
    rows = [_row(i, event="same-event", at=i, p=0.6 + i * 0.01)
            for i in range(24)]
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["rows_in_scope"] == 24
    assert got["unique_events"] == 1
    assert got["observations_collapsed"] == 23
    assert got["scored_events"] == 1


def test_the_probability_scored_is_the_first_one_recorded():
    """POINT IN TIME. Taking the last, or the best, would be choosing among
    a source's own revisions after seeing which way the event went."""
    rows = [_row(2, event="e", at=200.0, p=0.99),
            _row(1, event="e", at=100.0, p=0.55),
            _row(3, event="e", at=300.0, p=0.01)]
    picked = CAL.unique_events(rows)
    assert len(picked) == 1
    assert list(picked.values())[0]["probability"] == 0.55


def test_the_same_fixture_on_two_payout_events_is_two_statements():
    a = dict(_row(1, event="e"), payout_event="HOME")
    b = dict(_row(2, event="e"), payout_event="NOT(HOME)")
    assert len(CAL.unique_events([a, b])) == 2


# ── resolved / void / unresolved ──────────────────────────────────────

def test_void_and_unresolved_are_excluded_and_counted():
    rows = ([_row(i, outcome=1) for i in range(3)]
            + [_row(10, known=True, outcome=None),
               _row(11, known=True, outcome=7),
               _row(12, known=False, outcome=None)])
    got = CAL.evaluate(rows, measured_at=0.0)
    c = got["outcome_counts"]
    assert c[CAL.RESOLVED] == 3
    assert c[CAL.VOID] == 2, "no 0/1 truth, so not scoreable"
    assert c[CAL.UNRESOLVED] == 1
    assert got["scored_events"] == 3


def test_a_resolved_event_with_no_probability_is_not_scored_as_zero():
    rows = [_row(1, p=None, outcome=1), _row(2, p=0.7, outcome=1)]
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["scored_events"] == 1
    assert got["outcome_counts"][CAL.UNRESOLVED] == 1
    assert got["outcome_counts"][CAL.RESOLVED] == 1


# ── the shortfall, which is the current answer ────────────────────────

def test_an_insufficient_sample_reports_the_shortfall_and_writes_nothing():
    rows = [_row(i, p=0.7, outcome=1 if i % 10 else 0) for i in range(50)]
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["status"] == CAL.INSUFFICIENT
    assert got["within_tolerance"] is False
    assert got["shortfall_events"] == CAL.MIN_RESOLVED_EVENTS - 50
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
    rows = [_row(i, p=1.0, outcome=1) for i in range(10)]
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["score"] == 0.0
    assert got["status"] == CAL.INSUFFICIENT
    assert got["within_tolerance"] is False


# ── a completed verdict, both ways ────────────────────────────────────

def _many(n, p, outcome_fn):
    return [_row(i, p=p, outcome=outcome_fn(i), event="ev%d" % i)
            for i in range(n)]


def test_a_sufficient_well_calibrated_sample_passes_and_beats_base_rate():
    # p=0.7 stated, and 70% of them happen. Brier = 0.7*0.09 + 0.3*0.49
    # = 0.21, inside the 0.24 ceiling.
    n = CAL.MIN_RESOLVED_EVENTS + 20
    rows = _many(n, 0.7, lambda i: 1 if i % 10 < 7 else 0)
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["status"] == CAL.PASSED, got["why"]
    assert got["within_tolerance"] is True
    assert got["score"] == pytest.approx(0.21, abs=0.01)
    assert got["score_is_provisional"] is False
    assert got["base_rate"] == pytest.approx(0.7, abs=0.02)
    assert got["beats_base_rate"] is False, (
        "a constant 0.7 IS the base rate here, so it should not claim to "
        "beat it -- and the report says so rather than implying skill")
    row = CAL.to_row(got, measured_by="t", window_start=0, window_end=1)
    assert row["write"] is True and row["within_tolerance"] is True


def test_a_sufficient_badly_calibrated_sample_fails_and_is_still_written():
    """A FAILED verdict is a measurement and is recorded. What it must not
    do is open the gate."""
    n = CAL.MIN_RESOLVED_EVENTS + 20
    rows = _many(n, 0.9, lambda i: 1 if i % 10 < 4 else 0)
    got = CAL.evaluate(rows, measured_at=0.0)
    assert got["status"] == CAL.FAILED
    assert got["within_tolerance"] is False
    assert got["score"] > CAL.BRIER_CEILING
    row = CAL.to_row(got, measured_by="t", window_start=0, window_end=1)
    assert row["write"] is True
    assert row["within_tolerance"] is False


def test_the_same_rows_give_the_same_answer():
    rows = _many(40, 0.7, lambda i: i % 3 == 0)
    a = CAL.evaluate(rows, measured_at=1.0)
    b = CAL.evaluate(list(reversed(rows)), measured_at=2.0)
    assert a["inputs_sha"] == b["inputs_sha"]
    assert a["score"] == b["score"]
    # AND DIFFERENT ROWS GIVE A DIFFERENT HASH -- a NEW event, not another
    # observation of one already there, which would be collapsed and
    # correctly leave the hash alone.
    extra = dict(_row(999, p=0.5, outcome=1), event_key="a-new-event")
    c = CAL.evaluate(rows + [extra], measured_at=1.0)
    assert c["unique_events"] == a["unique_events"] + 1
    assert c["inputs_sha"] != a["inputs_sha"]


# ── the outcome join's leg arithmetic ─────────────────────────────────

def test_a_short_leg_is_scored_against_the_complement():
    """The venue's settlement price is about ITS OWN yes side. A contract
    acquired as BUY_SHORT pays on the complement, so scoring it against
    the raw price would be exactly wrong on half the sample."""
    assert loop.outcome_for_leg(1.0, payout_is_complement=False) == 1
    assert loop.outcome_for_leg(1.0, payout_is_complement=True) == 0
    assert loop.outcome_for_leg(0.0, payout_is_complement=False) == 0
    assert loop.outcome_for_leg(0.0, payout_is_complement=True) == 1


def test_a_price_that_is_neither_zero_nor_one_is_a_void_not_a_fraction():
    for bad in (0.5, 0.42, "0.5", None, "", "nonsense"):
        assert loop.outcome_for_leg(bad, payout_is_complement=False) is None


# ── against Postgres ─────────────────────────────────────────────────

@pg
@pytest.mark.asyncio
async def test_the_measurement_runs_against_the_real_table(monkeypatch):
    """END TO END on the real schema: the reader, the evaluator and the
    refusal to write an unearned row."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_external_shadow as ext

    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(open(
            "migrations/117_entry_lane_evidence_and_calibration.sql").read())
        exp = "CALIBRATION_TEST_EXPERIMENT"
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", exp)
        await conn.execute("DELETE FROM external_source_calibration "
                           "WHERE measured_by = 'PYTEST'")
        # Twelve events, inserted WITHOUT an outcome and then joined by
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
        import time as _t0

        pre = await CAL.measure(conn, experiment_id=exp, days=90,
                                now=_t0.time(), measured_by="PYTEST")
        assert pre["outcome_counts"][CAL.UNRESOLVED] == 12
        assert pre["scored_events"] == 0
        # NOW JOIN THE OUTCOMES, through the statement the loop uses.
        from sportsassets import bettor_external_shadow as _e

        # THE SETTLEMENT INSTANT MUST FOLLOW THE DECISION, and the table
        # has a CHECK that says so: an outcome recorded before the
        # valuation it resolves would be look-ahead in the schema.
        import time as _t

        settled_at = _t.time()
        for k, rid in enumerate(ids):
            await conn.execute(_e.JOIN_OUTCOME, rid,
                               1 if k % 3 else 0, settled_at, None)
        got = await CAL.measure(conn, experiment_id=exp, days=90,
                                now=_t.time(), measured_by="PYTEST")
        assert got["ran"] is True
        assert got["rows_in_scope"] == 12
        assert got["unique_events"] == 12
        assert got["status"] == CAL.INSUFFICIENT
        assert got["shortfall_events"] == CAL.MIN_RESOLVED_EVENTS - 12
        assert got["written"] is False
        assert got["would_write"] is False
        # AND NO ROW EXISTS, so the gate is exactly as shut as before.
        n = await conn.fetchval(
            "SELECT count(*) FROM external_source_calibration "
            "WHERE measured_by = 'PYTEST'")
        assert n == 0
    finally:
        await conn.close()
