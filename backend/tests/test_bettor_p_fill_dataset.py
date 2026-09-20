"""§9. The P_FILL dataset, and the selection it must not hide."""

import pytest

from sportsassets import bettor_p_fill_dataset as ds
from sportsassets import bettor_shadow_execution as sx


def _quote(queue="500", spread="0.04", hidden=None, priority=None):
    return sx.hypothetical_quote(
        decision_ts="2026-09-20T17:00:00Z", arrival_ts="2026-09-20T17:00:01Z",
        side="BID", price="0.48", quantity="100",
        book={"bid": "0.48", "ask": "0.52", "mid": "0.50",
              "spread": spread, "availableDepth": "1200"},
        displayed_size_at_level=queue,
        hidden_liquidity_status=hidden, priority_semantics=priority)


def _positive(spread="0.01"):
    q = _quote(spread=spread)
    return ds.row(q, sx.label_outcome(q, traded_through=True),
                  time_to_supported_fill="12.5",
                  adverse_selection_after_supported_fill="-0.003",
                  markouts={"1s": "-0.001", "30s": "-0.004"})


def _negative(spread="0.08"):
    q = _quote(spread=spread, hidden=sx.HIDDEN_LIQUIDITY_IDENTIFIED,
               priority=sx.PRIORITY_IDENTIFIED)
    return ds.row(q, sx.label_outcome(
        q, traded_through=False,
        queue_dynamic_status=sx.QUEUE_DYNAMIC_OBSERVED,
        depletion_from_trades="200", depletion_from_cancellations="50",
        addition_ahead="0", effective_queue_ahead_min="250"))


def _censored(spread="0.01"):
    q = _quote(spread=spread)
    return ds.row(q, sx.label_outcome(q, volume_at_or_through_price="900"))


def _empty(spread="0.08"):
    q = _quote(spread=spread)
    return ds.row(q, sx.label_outcome(q))


# ── §9: the field list is fixed before the first row ─────────────────

def test_the_field_list_is_exactly_what_the_directive_named():
    assert ds.ROW_FIELDS == (
        "QUOTE_PRICE", "SIDE", "SIZE", "QUEUE_AHEAD_AT_T0", "SPREAD",
        "DEPTH", "BOOK_IMBALANCE", "VOLATILITY", "TIME_TO_EVENT",
        "TRADE_THROUGH_STATUS", "TRADE_AT_PRICE_VOLUME",
        "QUEUE_DEPLETION_EVIDENCE", "CANCELLATION_EVIDENCE",
        "QUEUE_PRIORITY_EVIDENCE",
        "OUTCOME_CLASS", "OUTCOME_IDENTIFICATION_STATUS",
        "TIME_TO_SUPPORTED_FILL",
        "ADVERSE_SELECTION_AFTER_SUPPORTED_FILL", "MARKOUTS")


@pytest.mark.parametrize("field", ds.ROW_FIELDS)
def test_every_field_is_on_every_row(field):
    for r in (_positive(), _negative(), _censored(), _empty()):
        assert field in r, field


def test_absence_is_written_not_omitted():
    """A missing key and a missing measurement read alike to a model,
    and only one of them is a fact about the market."""
    r = _empty()
    assert r["BOOK_IMBALANCE"] == ds.NOT_IDENTIFIED
    assert r["VOLATILITY"] == ds.NOT_IDENTIFIED
    assert r["TIME_TO_EVENT"] == ds.NOT_IDENTIFIED


def test_features_come_from_the_sealed_t0_record():
    r = _positive()
    assert r["QUOTE_PRICE"] == "0.48"
    assert r["SIDE"] == "BID"
    assert r["SIZE"] == "100"
    assert r["QUEUE_AHEAD_AT_T0"] == "500"
    assert r["DEPTH"] == "1200"
    assert r["BOOK_SHA_AT_ARRIVAL"]


# ── the three queue columns never collapse into one ──────────────────

def test_depletion_and_cancellation_are_separate_columns():
    r = _negative()
    assert r["QUEUE_DEPLETION_EVIDENCE"] == "200"
    assert r["CANCELLATION_EVIDENCE"] == "50"
    assert r["QUEUE_PRIORITY_EVIDENCE"] == sx.PRIORITY_IDENTIFIED
    assert "collapsed depletion and cancellation" in ds.WHY_THREE_QUEUE_COLUMNS


def test_a_row_with_depletion_but_no_cancellation_is_visibly_short():
    r = _censored()
    assert r["CANCELLATION_EVIDENCE"] == ds.NOT_IDENTIFIED
    assert r["OUTCOME_IDENTIFICATION_STATUS"] != sx.NEGATIVE_SUPPORTED


# ── class and status are two columns ─────────────────────────────────

def test_class_and_status_are_not_the_same_column():
    c, e = _censored(), _empty()
    assert c["OUTCOME_CLASS"] == e["OUTCOME_CLASS"] == sx.FILL_NOT_IDENTIFIED
    assert c["OUTCOME_IDENTIFICATION_STATUS"] == sx.INTERVAL_CENSORED
    assert e["OUTCOME_IDENTIFICATION_STATUS"] == sx.EVIDENCE_NOT_IDENTIFIED


def test_post_fill_quantities_exist_only_on_a_supported_positive():
    p = _positive()
    assert p["TIME_TO_SUPPORTED_FILL"] == "12.5"
    assert p["MARKOUTS"]["30s"] == "-0.004"
    for r in (_negative(), _censored(), _empty()):
        assert r["TIME_TO_SUPPORTED_FILL"] == ds.NOT_IDENTIFIED
        assert r["ADVERSE_SELECTION_AFTER_SUPPORTED_FILL"] == ds.NOT_IDENTIFIED
        assert r["MARKOUTS"] == ds.NOT_IDENTIFIED


def test_post_fill_quantities_are_refused_even_when_offered():
    """A caller passing markouts on a censored row does not get them
    written: there is no fill for them to be after."""
    q = _quote()
    r = ds.row(q, sx.label_outcome(q, volume_at_or_through_price="900"),
               time_to_supported_fill="9", markouts={"1s": "0.01"})
    assert r["TIME_TO_SUPPORTED_FILL"] == ds.NOT_IDENTIFIED
    assert r["MARKOUTS"] == ds.NOT_IDENTIFIED


def test_at_price_volume_is_a_feature_never_an_outcome():
    r = _censored()
    assert r["TRADE_AT_PRICE_VOLUME"] == "900"
    assert r["OUTCOME_CLASS"] == sx.FILL_NOT_IDENTIFIED
    assert "not positive fill evidence" in r["atPriceVolumeIsNotAFill"]


def test_no_row_can_carry_an_actual_fill():
    for r in (_positive(), _negative(), _censored(), _empty()):
        assert r[sx.ACTUAL] == ds.NOT_IDENTIFIED


# ── §13's counts ─────────────────────────────────────────────────────

def test_counts_come_from_the_evidence_class():
    c = ds.counts([_positive(), _negative(), _censored(), _empty()])
    assert c["P_FILL_IDENTIFIED_POSITIVES"] == 1
    assert c["P_FILL_IDENTIFIED_NEGATIVES"] == 1
    assert c["P_FILL_INTERVAL_CENSORED"] == 1
    assert c["P_FILL_NOT_IDENTIFIED"] == 2
    assert c["P_FILL_NOT_IDENTIFIED_NO_EVIDENCE"] == 1


def test_a_censored_row_is_never_counted_as_a_negative():
    c = ds.counts([_censored(), _censored(), _censored()])
    assert c["P_FILL_IDENTIFIED_NEGATIVES"] == 0
    assert c["P_FILL_NOT_IDENTIFIED"] == 3


def test_an_empty_dataset_counts_to_zero_not_to_a_rate():
    c = ds.counts([])
    assert c["rows"] == 0
    assert c["P_FILL_IDENTIFIED_POSITIVES"] == 0


# ── §9: the selection must be MEASURED ───────────────────────────────

def test_the_selection_is_measured_per_stratum():
    """Positives concentrate in tight spreads, negatives in wide ones --
    which is exactly the selection the diagnostic exists to expose."""
    rows = [_positive("0.01"), _censored("0.01"), _censored("0.01"),
            _negative("0.08"), _negative("0.08")]
    s = ds.selection_diagnostics(rows)
    assert s["identified"] == 3
    assert s["IDENTIFICATION_RATE"] == "0.6"
    tight = s["byStratum"]["BID|SPREAD_TIGHT"]
    wide = s["byStratum"]["BID|SPREAD_WIDE"]
    assert tight["identificationRate"] != wide["identificationRate"]
    assert s["IDENTIFICATION_RATE_SPREAD_ACROSS_STRATA"] != ds.NOT_IDENTIFIED


def test_a_spread_across_strata_is_explained_not_just_reported():
    s = ds.selection_diagnostics([_positive("0.01"), _negative("0.08")])
    assert "P(FILL | RESOLVABLE)" in s["whatASpreadMeans"]
    assert "before the first" in s["strataArePreDeclared"]


def test_an_empty_dataset_has_no_identification_rate():
    s = ds.selection_diagnostics([])
    assert s["IDENTIFICATION_RATE"] == ds.NOT_IDENTIFIED
    assert s["FILL_SELECTION_STATUS"] == ds.NOT_IDENTIFIED


# ── the frozen prior travels separately ──────────────────────────────

def test_fill_selection_effect_is_not_a_row_column():
    assert "FILL_SELECTION_EFFECT" not in ds.ROW_FIELDS
    assert "different quantity" in ds.FILL_SELECTION_EFFECT_IS_NOT_A_COLUMN
    assert "isNotAColumn" in ds.fill_selection_effect()


# ── the training contract fails closed ───────────────────────────────

def test_no_rows_means_no_fit():
    t = ds.training_contract([])
    assert t["mayFit"] is False
    assert "NO_ROWS" in t["blockers"]
    assert "SELECTION_NOT_MEASURED" in t["blockers"]


def test_censored_rows_alone_cannot_open_a_fit():
    t = ds.training_contract([_censored(), _censored()])
    assert t["mayFit"] is False
    assert "NO_IDENTIFIED_POSITIVES" in t["blockers"]
    assert "NO_IDENTIFIED_NEGATIVES" in t["blockers"]


def test_only_supported_labels_are_admissible_as_training_targets():
    t = ds.training_contract([_positive(), _negative()])
    assert t["labelsAdmissibleAsPositive"] == [sx.POSITIVE_SUPPORTED]
    assert t["labelsAdmissibleAsNegative"] == [sx.NEGATIVE_SUPPORTED]
    assert sx.INTERVAL_CENSORED in t["labelsNeverAdmissibleAsNegative"]


def test_a_fit_opens_only_with_both_kinds_and_a_measured_selection():
    t = ds.training_contract([_positive(), _negative(), _censored()])
    assert t["mayFit"] is True
    assert t["blockers"] == []
    assert t["selection"]["FILL_SELECTION_STATUS"] == "MEASURED_NOT_CORRECTED"


def test_the_contract_carries_the_selection_warning():
    t = ds.training_contract([_positive(), _negative()])
    assert "P(FILL | RESOLVABLE)" in t["selectionMustBeModelled"]
    assert "Do not discard" in t["censoredRowsAreKept"]


def test_a_dataset_row_creates_no_inventory():
    assert "not a position" in ds.describe()["createsNoInventory"]
