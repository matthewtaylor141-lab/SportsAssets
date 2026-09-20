"""The order BETTOR did not place, and the fill it must never claim."""

import pytest

from sportsassets import bettor_shadow_execution as sx


def _quote(queue="500", hidden=None, priority=None):
    return sx.hypothetical_quote(
        decision_ts="2026-09-20T17:00:00Z", arrival_ts="2026-09-20T17:00:01Z",
        side="BID", price="0.48", quantity="100",
        book={"bid": "0.48", "ask": "0.52", "mid": "0.50"},
        displayed_size_at_level=queue,
        hidden_liquidity_status=hidden, priority_semantics=priority)


def _fully_observed(**over):
    """A quote plus the complete queue-evolution evidence §5 demands."""
    q = _quote(hidden=sx.HIDDEN_LIQUIDITY_IDENTIFIED,
               priority=sx.PRIORITY_IDENTIFIED)
    kwargs = dict(traded_through=False,
                  queue_dynamic_status=sx.QUEUE_DYNAMIC_OBSERVED,
                  depletion_from_trades="200",
                  depletion_from_cancellations="50",
                  addition_ahead="0",
                  effective_queue_ahead_min="250")
    kwargs.update(over)
    return q, kwargs


# ── the two names never touch ────────────────────────────────────────

def test_nothing_here_can_produce_an_actual_fill():
    q = _quote()
    assert q[sx.ACTUAL] == sx.NOT_IDENTIFIED
    assert sx.label_outcome(q, traded_through=True)[sx.ACTUAL] == \
        sx.NOT_IDENTIFIED
    assert sx.ORDER_PATH_EXISTS is False
    assert "placed no order" in sx.NEVER_AN_ACTUAL_FILL


def test_no_label_is_the_word_filled():
    """A positive outcome is COUNTERFACTUAL, and the name says so."""
    for label in sx.LABELS:
        assert label != "FILLED"
        assert label.startswith("COUNTERFACTUAL")


# ── §4: the T0 record is closed before the answer exists ─────────────

def test_the_t0_record_contains_nothing_about_the_future():
    q = _quote()
    assert q["COUNTERFACTUAL_FILL_STATUS"] == sx.NOT_IDENTIFIED
    assert q["EVIDENCE_CLASS"] == sx.NOT_IDENTIFIED
    for field in sx.OUTCOME_FIELDS:
        if field not in ("COUNTERFACTUAL_FILL_STATUS", "EVIDENCE_CLASS"):
            assert field not in q, field


def test_the_t0_record_is_sealed():
    """A later append must not be able to revise the order silently."""
    a = _quote()
    b = sx.hypothetical_quote(
        decision_ts="2026-09-20T17:00:00Z",
        arrival_ts="2026-09-20T17:00:01Z", side="BID", price="0.49",
        quantity="100", displayed_size_at_level="500")
    assert a["BOOK_SHA_AT_ARRIVAL"] != b["BOOK_SHA_AT_ARRIVAL"]


def test_availability_and_outcome_are_stored_apart():
    assert "hindsight" in sx.SEPARATION_RULE


# ── §2: the retraction is enforced, not merely described ─────────────

def test_the_lower_bound_claim_is_retracted_by_name():
    r = sx.RETRACTED_CLAIMS["QUEUE_AHEAD_IS_A_LOWER_BOUND"]
    assert r["status"] == "RETRACTED"
    assert "ignores queue depletion" in r["why"]
    assert "tests" in r["mustNotAppearIn"]


def test_the_retracted_sentence_is_nowhere_asserted_as_true():
    """It may appear ONLY inside the retraction record."""
    import inspect
    src = inspect.getsource(sx)
    needle = "can only lengthen the true queue"
    # No live string constant may carry it.
    for name, value in vars(sx).items():
        if isinstance(value, str) and name.isupper():
            assert needle not in value, name
    # Exactly two occurrences survive, and both are quotations inside a
    # retraction: the docstring block and the RETRACTED_CLAIMS record.
    assert src.count(needle) == 2
    assert "THAT CLAIM IS RETRACTED" in sx.__doc__
    assert needle in sx.RETRACTED_CLAIMS[
        "QUEUE_AHEAD_IS_A_LOWER_BOUND"]["claim"]


def test_the_old_label_name_is_retired_not_reused():
    assert "DEFINITELY_NOT_FILLED" not in sx.LABELS
    assert not hasattr(sx, "DEFINITELY_NOT_FILLED")
    assert "retired" in sx.RETIRED_LABELS["DEFINITELY_NOT_FILLED"]


def test_queue_ahead_at_t0_is_declared_not_a_bound():
    q = _quote()
    assert q["QUEUE_AHEAD_AT_T0_STATUS"] == "DISPLAYED_SNAPSHOT_AT_ARRIVAL"
    assert "NOT a permanent lower bound" in sx.QUEUE_AHEAD_AT_T0_IS_NOT_A_BOUND
    assert "cancellations ahead can shorten it" in \
        sx.QUEUE_AHEAD_AT_T0_IS_NOT_A_BOUND.lower()


# ── §3: the snapshot and the process are separate objects ────────────

def test_queue_is_two_objects_and_the_dynamic_one_defaults_to_unobserved():
    r = sx.label_outcome(_quote(), volume_at_or_through_price="200")
    assert r["QUEUE_AHEAD_AT_T0"] == "500"
    assert r["QUEUE_AHEAD_DYNAMIC_STATUS"] == sx.QUEUE_DYNAMIC_NOT_OBSERVED
    for field in ("QUEUE_DEPLETION_FROM_TRADES",
                  "QUEUE_DEPLETION_FROM_CANCELLATIONS",
                  "QUEUE_ADDITION_AHEAD"):
        assert r[field] == sx.NOT_IDENTIFIED


def test_hidden_liquidity_and_priority_default_to_not_identified():
    q = _quote()
    assert q["HIDDEN_LIQUIDITY_STATUS"] == sx.HIDDEN_LIQUIDITY_NOT_IDENTIFIED
    assert q["QUEUE_POSITION_STATUS"] == sx.PRIORITY_NOT_IDENTIFIED


# ── §4: the manufactured negative is gone ────────────────────────────

def test_less_volume_than_the_t0_queue_is_not_a_negative():
    """THE CORRECTED DEFECT. 200 traded against 500 displayed at T0 used
    to read as refuted. Cancellations ahead could have depleted the
    queue beneath that volume, so it resolves nothing."""
    r = sx.label_outcome(_quote(queue="500"),
                         volume_at_or_through_price="200")
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_NOT_IDENTIFIED
    assert r["EVIDENCE_CLASS"] == sx.INTERVAL_CENSORED
    assert r["reason"] == sx.REASON_INITIAL_QUEUE_ONLY
    assert "cancellations ahead" in r["why"]


def test_the_owners_counterexample_does_not_produce_a_negative():
    """T0 queue 500; 400 ahead cancel; 200 trade at our price."""
    r = sx.label_outcome(_quote(queue="500"),
                         volume_at_or_through_price="200")
    assert r["COUNTERFACTUAL_FILL_STATUS"] != sx.NO_FILL_SUPPORTED
    assert "QUEUE_DEPLETION_FROM_CANCELLATIONS" in r["missingForNegative"]


def test_the_reason_code_names_every_missing_piece():
    assert sx.REASON_INITIAL_QUEUE_ONLY == (
        "INITIAL_QUEUE_NOT_DEPLETED_BY_TRADE_VOLUME_BUT_QUEUE_"
        "CANCELLATION_AND_PRIORITY_EVOLUTION_NOT_IDENTIFIED")


# ── §5: the negative must be earned ──────────────────────────────────

def test_a_fully_observed_queue_evolution_supports_a_negative():
    q, kw = _fully_observed()
    r = sx.label_outcome(q, **kw)
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.NO_FILL_SUPPORTED
    assert r["EVIDENCE_CLASS"] == sx.NEGATIVE_SUPPORTED
    assert r["EFFECTIVE_QUEUE_AHEAD_MIN"] == "250"


@pytest.mark.parametrize("drop,field", [
    ({"queue_dynamic_status": sx.QUEUE_DYNAMIC_NOT_OBSERVED},
     "QUEUE_AHEAD_DYNAMIC_STATUS"),
    ({"depletion_from_cancellations": None},
     "QUEUE_DEPLETION_FROM_CANCELLATIONS"),
    ({"addition_ahead": None}, "QUEUE_ADDITION_AHEAD"),
    ({"effective_queue_ahead_min": None}, "EFFECTIVE_QUEUE_AHEAD_MIN"),
])
def test_every_negative_precondition_is_load_bearing(drop, field):
    q, kw = _fully_observed(**drop)
    r = sx.label_outcome(q, **kw)
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_NOT_IDENTIFIED
    assert field in r["missingForNegative"]


def test_unidentified_hidden_liquidity_blocks_the_negative():
    """We are not entitled to assume hidden liquidity points our way --
    that is the shape of the retracted claim."""
    q = _quote(hidden=None, priority=sx.PRIORITY_IDENTIFIED)
    _, kw = _fully_observed()
    r = sx.label_outcome(q, **kw)
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_NOT_IDENTIFIED
    assert "HIDDEN_LIQUIDITY_STATUS" in r["missingForNegative"]
    assert "retracted claim" in sx.WHY_HIDDEN_LIQUIDITY_IS_REQUIRED_FOR_A_NEGATIVE


def test_unidentified_priority_semantics_blocks_the_negative():
    q = _quote(hidden=sx.HIDDEN_LIQUIDITY_IDENTIFIED, priority=None)
    _, kw = _fully_observed()
    r = sx.label_outcome(q, **kw)
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_NOT_IDENTIFIED
    assert "QUEUE_POSITION_STATUS" in r["missingForNegative"]


def test_an_effective_queue_that_reached_zero_is_not_a_negative():
    q, kw = _fully_observed(effective_queue_ahead_min="0")
    r = sx.label_outcome(q, **kw)
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_NOT_IDENTIFIED


# ── §6: the positive, with its assumptions on the record ─────────────

def test_a_trade_through_supports_a_counterfactual_fill():
    r = sx.label_outcome(_quote(), traded_through=True)
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_SUPPORTED
    assert r["EVIDENCE_CLASS"] == sx.POSITIVE_SUPPORTED
    assert "cannot happen while a resting order" in r["why"]


def test_the_positive_carries_its_assumptions():
    r = sx.label_outcome(_quote(), traded_through=True)
    joined = " ".join(r["assumptions"])
    assert "NO_MARKET_IMPACT" in joined
    assert "NOT VERIFIED" in joined
    assert "CONTINUOUS_REST" in joined
    assert "PRICE_TIME_PRIORITY" in joined


def test_the_positive_never_crosses_into_an_actual_fill():
    r = sx.label_outcome(_quote(), traded_through=True)
    assert r[sx.ACTUAL] == sx.NOT_IDENTIFIED
    assert "never crosses" in sx.TRADE_THROUGH_IS_STILL_COUNTERFACTUAL


# ── §7: at-price volume is not a positive ────────────────────────────

def test_more_volume_than_the_queue_is_still_not_a_fill():
    """THE TRAP: volume exceeding the queue does not mean we were
    reached. Queue position governs and it is not observable."""
    r = sx.label_outcome(_quote(queue="500"),
                         volume_at_or_through_price="900")
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_NOT_IDENTIFIED
    assert r["EVIDENCE_CLASS"] == sx.INTERVAL_CENSORED
    assert "not identified" in r["why"]


def test_at_price_volume_is_declared_not_a_fill():
    assert "not positive fill evidence" in sx.AT_PRICE_VOLUME_IS_NOT_A_FILL


# ── §8: interval-censored evidence survives ──────────────────────────

def test_censored_and_empty_rows_are_distinguishable():
    censored = sx.label_outcome(_quote(), volume_at_or_through_price="900")
    empty = sx.label_outcome(_quote())
    assert censored["EVIDENCE_CLASS"] == sx.INTERVAL_CENSORED
    assert empty["EVIDENCE_CLASS"] == sx.EVIDENCE_NOT_IDENTIFIED
    # Same label -- neither may be asserted either way.
    assert censored["COUNTERFACTUAL_FILL_STATUS"] == \
        empty["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_NOT_IDENTIFIED


def test_censored_rows_are_not_negatives_and_are_not_discarded():
    r = sx.label_outcome(_quote(), volume_at_or_through_price="900")
    assert "Do not discard" in r["notANegative"]
    assert "not train on them as no-fills" in r["notANegative"]


def test_absent_evidence_is_not_identified_rather_than_not_filled():
    r = sx.label_outcome(_quote())
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_NOT_IDENTIFIED


def test_the_third_label_is_not_collapsible():
    assert len(sx.LABELS) == 3
    assert len(sx.EVIDENCE_CLASSES) == 4
    assert "would learn our guess" in sx.WHY_THREE_LABELS


def test_an_unknown_queue_cannot_refute():
    q = sx.hypothetical_quote(
        decision_ts="T0", arrival_ts="T1", side="BID", price="0.48",
        quantity="100", displayed_size_at_level=None)
    assert q["QUEUE_AHEAD_AT_T0"] == sx.NOT_IDENTIFIED
    r = sx.label_outcome(q, volume_at_or_through_price="1")
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_NOT_IDENTIFIED


# ── §9: identification is itself a selection ─────────────────────────

def test_every_row_carries_the_identification_selection_warning():
    for r in (sx.label_outcome(_quote(), traded_through=True),
              sx.label_outcome(_quote(), volume_at_or_through_price="200"),
              sx.label_outcome(_quote())):
        assert r["IDENTIFICATION_SELECTION_STATUS"] == \
            "PRESENT_NOT_YET_CORRECTED"
        assert "P(FILL | RESOLVABLE)" in r["identificationSelection"]


@pytest.mark.parametrize("field", sx.AT_T0_FIELDS)
def test_every_declared_t0_field_is_present(field):
    assert field in _quote()


@pytest.mark.parametrize("field", sx.QUEUE_FIELDS)
def test_every_declared_queue_field_reaches_the_outcome_row(field):
    assert field in sx.label_outcome(_quote())
