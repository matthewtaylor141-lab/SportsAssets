"""THE BASELINE MUST BE FIXED BEFORE THE OUTCOMES IT IS SCORED AGAINST.

`evaluate` compared the model against `sum(y)/len(y)` -- the base rate of
the evaluation labels. That is an oracle: it is computed from the outcomes
being scored, so it cannot be beaten by chance and the comparison means
nothing. The prior is now stored ON EACH PREDICTION at prediction time
(migration 107, `baseline_p`), taken from the training base rate of the fit
that produced the model.

These tests pin that, plus the three other claims the evaluation makes
about itself: clustering reported, out-of-sample checked rather than
asserted, and the target named as cohort behaviour rather than profit.
"""
import inspect
import pathlib

import pytest

from sportsassets import bettor_model_inventory as inv
from sportsassets.workers import rn1x_model_loop as ML


def test_the_evaluation_no_longer_uses_the_label_base_rate_as_a_baseline():
    src = inspect.getsource(ML.evaluate)
    assert "baseline_rate=base" not in src, (
        "the old oracle comparison is back")
    # the descriptive statistic may be reported, but must be labelled
    assert "observed_outcome_rate_is_not_the_baseline" in src
    assert "oracle" in src


def test_the_prior_comes_from_the_stored_column():
    src = inspect.getsource(ML.evaluate)
    assert 'r.get("baseline_p")' in src
    assert "rows_without" in src, "uncovered rows must be counted"
    # and uncovered rows are NOT given a substitute
    assert "NO_ROW_CARRIES_A_PRIOR_FIXED_BEFORE_ITS_OUTCOME" in src


def test_the_prior_is_written_at_prediction_time_from_the_fit():
    src = inspect.getsource(ML.predict)
    assert "baseline_p=fitted.get(\"base_rate\")" in src
    assert "TRAINING_BASE_RATE_UNCENSORED_AT_FIT" in src


def test_the_ledger_never_fills_a_prior_in_afterwards():
    src = inspect.getsource(inv.record_prediction)
    assert "baseline_p" in src
    assert "never filled in afterwards" in src or "NULL when the caller" in src
    # no UPDATE of baseline_p anywhere in the module
    whole = inspect.getsource(inv)
    assert "SET baseline_p" not in whole


def test_the_migration_leaves_existing_rows_null():
    sql = pathlib.Path(
        "migrations/107_prediction_prior_fixed_before_the_outcome.sql"
    ).read_text()
    assert "baseline_p double precision" in sql
    assert "UPDATE rn1x_model_predictions" not in sql, (
        "back-filling a prior invents a number that was never used")
    # the two columns must be set together or not at all
    assert "(baseline_p IS NULL) = (baseline_basis IS NULL)" in sql


def test_clustering_is_reported_so_n_is_not_read_as_a_sample_size():
    src = inspect.getsource(ML.evaluate)
    for field in ("unique_conditions", "largest_cluster",
                  "rows_in_multi_row_conditions", "unique_accounts"):
        assert field in src, field
    assert "NOT independent evidence" in src


def test_out_of_sample_is_checked_against_the_labels_own_evidence():
    src = inspect.getsource(ML.evaluate)
    assert "LABEL_EVIDENCE_SQL" in src
    assert "verified_label_evidence_postdates_prediction" in src
    assert "an assertion" in src
    # the check must look at the window the label is defined over
    assert "predicted_at_s" in ML.LABEL_EVIDENCE_SQL or "$4" in ML.LABEL_EVIDENCE_SQL
    assert "outcome_index <> $3" in ML.LABEL_EVIDENCE_SQL


def test_a_violation_flips_the_out_of_sample_verdict():
    """`is_out_of_sample` must follow the check, not the flag."""
    src = inspect.getsource(ML.evaluate)
    assert '"is_out_of_sample": (not violations)' in src


def test_the_target_is_named_and_not_confused_with_profit():
    src = inspect.getsource(ML.evaluate)
    assert "NOT settlement" in src
    assert "NOT our fill" in src
    assert "not_a_profit_claim" in src


def test_metrics_include_both_log_loss_and_brier():
    src = inspect.getsource(ML.evaluate)
    assert "M.log_loss(p, y)" in src
    assert "M.brier(p, y)" in src
    assert "M.calibration(p, y" in src
