"""The order BETTOR did not place, and the fill it must never claim."""

import pytest

from sportsassets import bettor_shadow_execution as sx


def _quote(queue="500"):
    return sx.hypothetical_quote(
        decision_ts="2026-09-20T17:00:00Z", arrival_ts="2026-09-20T17:00:01Z",
        side="BID", price="0.48", quantity="100",
        book={"bid": "0.48", "ask": "0.52", "mid": "0.50"},
        displayed_size_at_level=queue)


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
    assert sx.FILL_SUPPORTED.startswith("COUNTERFACTUAL")


# ── §4: the T0 record is closed before the answer exists ─────────────

def test_the_t0_record_contains_nothing_about_the_future():
    q = _quote()
    assert q["COUNTERFACTUAL_FILL_STATUS"] == sx.NOT_IDENTIFIED
    for field in sx.OUTCOME_FIELDS:
        if field != "COUNTERFACTUAL_FILL_STATUS":
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


# ── §5: three labels, and the third is the honest one ────────────────

def test_a_trade_through_supports_a_counterfactual_fill():
    r = sx.label_outcome(_quote(), traded_through=True)
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_SUPPORTED
    assert "cannot happen while a resting order" in r["why"]


def test_less_volume_than_the_queue_refutes_arithmetically():
    r = sx.label_outcome(_quote(queue="500"),
                         volume_at_or_through_price="200")
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.DEFINITELY_NOT_FILLED
    assert "No queue model reaches it" in r["why"]


def test_more_volume_than_the_queue_is_still_not_a_fill():
    """THE TRAP: volume exceeding the queue does not mean we were
    reached. Queue position governs and it is not observable."""
    r = sx.label_outcome(_quote(queue="500"),
                         volume_at_or_through_price="900")
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_NOT_IDENTIFIED
    assert "not identified" in r["why"]


def test_absent_evidence_is_not_identified_rather_than_not_filled():
    r = sx.label_outcome(_quote())
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_NOT_IDENTIFIED


def test_the_third_label_is_not_collapsible():
    assert len(sx.LABELS) == 3
    assert "would learn our guess" in sx.WHY_THREE_LABELS


# ── the queue bound, and why the refutation is conservative ──────────

def test_queue_ahead_is_declared_a_lower_bound():
    q = _quote()
    assert q["QUEUE_AHEAD_STATUS"] == "LOWER_BOUND_FROM_DISPLAYED_SIZE"
    assert "hidden liquidity" in sx.QUEUE_AHEAD_IS_A_LOWER_BOUND


def test_an_unknown_queue_cannot_refute():
    q = sx.hypothetical_quote(
        decision_ts="T0", arrival_ts="T1", side="BID", price="0.48",
        quantity="100", displayed_size_at_level=None)
    assert q["QUEUE_AHEAD_ESTIMATE"] == sx.NOT_IDENTIFIED
    r = sx.label_outcome(q, volume_at_or_through_price="1")
    assert r["COUNTERFACTUAL_FILL_STATUS"] == sx.FILL_NOT_IDENTIFIED


@pytest.mark.parametrize("field", sx.AT_T0_FIELDS)
def test_every_declared_t0_field_is_present(field):
    assert field in _quote()
