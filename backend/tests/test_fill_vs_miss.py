"""Fill-vs-miss grading: the direct test of the copy thesis.

Owner 2026-08-12: "same or better price -> same or better margin". That
claim is only testable if the MISSED cohort (copies the price rule
refused) is scored at HIS price against the real resolution. The grade
shipped broken — asyncpg returns JSONB as a string, so `resolved_prices`
was indexed as characters, float('[') threw, and the endpoint returned
nothing for hours while looking merely empty. These tests pin the decode
and the counterfactual arithmetic.
"""

import json

import pytest

from sportsassets.api.grading import grade_rows


def _row(**kw):
    base = {"whale": "rn1", "status": "unfilled", "his_price": 0.50,
            "req_usd": 100.0, "filled_usd": None, "pnl": None,
            "outcome_index": 0, "resolved_prices": None}
    base.update(kw)
    return base


def _grade(rows):
    return {"whales": grade_rows(rows)}


def test_jsonb_string_payout_is_decoded_not_indexed_as_characters():
    """The regression: prices arrive as the STRING '[1, 0]'."""
    out = _grade([
        _row(his_price=0.50, req_usd=100.0, outcome_index=0,
             resolved_prices=json.dumps([1, 0])),
    ])
    b = out["whales"]["rn1"]
    assert b["missed_resolved"] == 1, "a resolved miss must be graded"
    assert b["missed_unresolved"] == 0
    # $100 at 50c buys 200 contracts; each pays $1 -> $200 back, +$100.
    assert b["missed_pnl"] == pytest.approx(100.0)
    assert b["missed_roi"] == pytest.approx(1.0)


def test_losing_miss_scores_minus_the_stake():
    out = _grade([
        _row(his_price=0.40, req_usd=50.0, outcome_index=1,
             resolved_prices=json.dumps([1, 0])),
    ])
    b = out["whales"]["rn1"]
    assert b["missed_resolved"] == 1
    assert b["missed_pnl"] == pytest.approx(-50.0)
    assert b["missed_roi"] == pytest.approx(-1.0)


def test_native_list_payout_still_works():
    """A pool that DOES register a JSONB codec must grade identically."""
    out = _grade([
        _row(his_price=0.25, req_usd=100.0, outcome_index=0,
             resolved_prices=[1, 0]),
    ])
    assert out["whales"]["rn1"]["missed_pnl"] == pytest.approx(300.0)


def test_unresolved_and_garbage_payouts_are_counted_not_crashed():
    out = _grade([
        _row(resolved_prices=None),
        _row(resolved_prices="not json at all"),
        _row(resolved_prices=json.dumps({"yes": 1})),   # object, not array
        _row(resolved_prices=json.dumps([1, 0]), outcome_index=None),
        _row(resolved_prices=json.dumps([1, 0]), outcome_index=7),  # out of range
        _row(resolved_prices=json.dumps([1, 0]), his_price=None),
    ])
    b = out["whales"]["rn1"]
    assert b["missed_n"] == 6
    assert b["missed_resolved"] == 0
    assert b["missed_unresolved"] == 6
    assert b["missed_roi"] is None


def test_filled_cohort_grades_on_realized_pnl_only():
    """The name was right and the assertion was not.

    filled_staked used to accumulate EVERY filled row, open ones
    included, while filled_pnl accumulated only settled ones -- so
    unrealized stake sat in the denominator with zero in the numerator
    and filled_roi came out halved here. The missed cohort has no such
    contamination: missed_staked is incremented only inside the
    resolved branch. Two cohorts, two denominators, and this number
    decides the price-tolerance rule.
    """
    out = _grade([
        _row(status="settled", filled_usd=100.0, pnl=12.0),
        _row(status="filled", filled_usd=100.0, pnl=None),   # open, no grade yet
    ])
    b = out["whales"]["rn1"]
    assert b["filled_n"] == 2
    assert b["filled_settled"] == 1
    assert b["filled_open"] == 1
    assert b["filled_staked"] == pytest.approx(100.0), \
        "open stake in the denominator halves the measured fill ROI"
    assert b["filled_pnl"] == pytest.approx(12.0)
    assert b["filled_roi"] == pytest.approx(0.12)
    # Still reported, just never used as a ROI denominator.
    assert b["filled_staked_all"] == pytest.approx(200.0)


def test_the_two_cohorts_use_the_same_denominator_rule():
    """The bias was not that either number was wrong on its own -- it
    was that they were computed differently and then compared."""
    out = _grade([
        _row(status="settled", filled_usd=100.0, pnl=10.0),
        _row(status="filled", filled_usd=900.0, pnl=None),
        _row(status="unfilled", req_usd=100.0, his_price=0.5,
             resolved_prices=json.dumps([1, 0]), outcome_index=0),
        _row(status="unfilled", req_usd=900.0, his_price=0.5,
             resolved_prices=None),
    ])
    b = out["whales"]["rn1"]
    assert b["filled_staked"] == pytest.approx(100.0)
    assert b["missed_staked"] == pytest.approx(100.0), \
        "unresolved rows must be excluded from BOTH sides or neither"
    assert b["filled_open"] == 1 and b["missed_unresolved"] == 1


def test_a_cashed_out_copy_counts_as_FILLED():
    """'cashed_out' is what mirror_exit writes on a copy exited at a
    profit. It was missing from the feeding query, so every winning
    exited copy vanished from the filled cohort -- and had it reached
    this function it would have fallen through and been graded a MISS,
    which is worse than being dropped."""
    out = _grade([
        _row(status="cashed_out", filled_usd=100.0, pnl=43.0),
    ])
    b = out["whales"]["rn1"]
    assert b["filled_n"] == 1
    assert b["missed_n"] == 0, "a profitable exit graded as a miss"
    assert b["filled_pnl"] == pytest.approx(43.0)
    assert b["filled_roi"] == pytest.approx(0.43)


def test_the_query_actually_selects_cashed_out_rows():
    """Naming the status in grade_rows is inert if the rows never
    arrive -- the two halves of this fix are only correct together."""
    import inspect

    from sportsassets.api import app as A

    src = inspect.getsource(A)
    i = src.index("lo.status IN ('filled', 'settled', 'unfilled'")
    assert "'cashed_out'" in src[i:i + 200]


def test_whales_are_graded_separately():
    out = _grade([
        _row(whale="rn1", status="settled", filled_usd=100.0, pnl=10.0),
        _row(whale="swisstony", status="settled", filled_usd=100.0, pnl=-5.0),
    ])
    assert out["whales"]["rn1"]["filled_roi"] == pytest.approx(0.10)
    assert out["whales"]["swisstony"]["filled_roi"] == pytest.approx(-0.05)
