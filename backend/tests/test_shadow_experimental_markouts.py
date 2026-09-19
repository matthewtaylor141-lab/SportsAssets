"""THE MARKOUTS, pinned where a late book could pass as a timely one.

Owner directive 2026-09-19 22:4xZ §12.

THE FAILURES THESE PREVENT:

  A BOOK EIGHT MINUTES LATE PRICED AS A 60-SECOND MARKOUT. That is not
  a noisy measurement of the thing we wanted; it is a different
  measurement wearing its label. Every horizon carries a frozen
  tolerance, and a book outside it is NOT_IDENTIFIED with its lag
  recorded -- which is itself the finding about this latency regime.

  A MID MARKOUT PASSED OFF AS WHAT WE COULD HAVE GOT OUT. On a thin
  book those differ by a lot. Both travel, always, and the executable
  one covers only the quantity the bid side could actually absorb.

  A MARKOUT THAT REWRITES THE DECISION IT MEASURES. Markouts are their
  own append, keyed back. The decision row is never touched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import shadow_experimental_markouts as mk
from sportsassets import shadow_l2 as l2

T0 = datetime(2026, 9, 19, 23, 40, tzinfo=timezone.utc)
POSITION = {"qty": 500.0, "vwap": 0.414}


def book(bids=None, offers=None, **kw):
    out = l2.book_from(
        {"symbol": "sym", "state": l2.STATE_OPEN,
         "transactTime": "2026-09-19T23:41:00Z",
         "bids": bids if bids is not None
         else [{"px": "44", "qty": "60000"}],
         "offers": offers if offers is not None
         else [{"px": "46", "qty": "60000"}]},
        price_scale=100, qty_scale=100)
    out.update({"l2BookSha": "bk16", "l2EvidenceId": "l2ev_1",
                "latencyRegime": "GITHUB_BRIDGE"})
    out.update(kw)
    return out


def take(horizon="60S", seconds=60, at=None, **kw):
    at = T0 + timedelta(seconds=seconds) if at is None else at
    # `now` is always past the target, so these exercise the MEASUREMENT
    # and not the not-yet-mature branch -- a book may legitimately be
    # observed a little BEFORE the target and still be the nearest one.
    target = mk.target_at(T0, seconds)
    return mk.markout(horizon=horizon, horizon_s=seconds, decision_at=T0,
                      position=POSITION, observed_at=at,
                      now=max(at, target) + timedelta(seconds=1),
                      **({"book": book()} | kw))


# ── the tolerance is what makes a markout a markout ──────────────────


def test_the_frozen_tolerances_are_half_the_horizon_with_a_floor():
    assert mk.tolerance_s(300) == 150.0
    assert mk.tolerance_s(60) == 30.0
    # the floor: half of 30s would be a hair trigger
    assert mk.tolerance_s(30) == 30.0


def test_a_book_inside_the_tolerance_is_the_markout():
    out = take(at=T0 + timedelta(seconds=75))
    assert out["status"] == mk.OBSERVED
    assert out["observedLagMs"] == pytest.approx(15000.0)


def test_a_book_minutes_late_is_not_this_horizons_markout():
    """THE ONE THAT MATTERS UNDER THE BRIDGE. Ten-minute cadence, a
    60-second horizon: the nearest book is usually far outside."""
    out = take(at=T0 + timedelta(minutes=8))
    assert out["status"] == mk.NOT_IDENTIFIED
    assert out["midMarkoutUsd"] is None
    assert out["executableMarkoutUsd"] is None
    assert "outside the 30s tolerance" in out["why"]
    # THE LAG IS STILL RECORDED. The miss is the evidence.
    assert out["observedLagMs"] == pytest.approx(420000.0)


def test_a_book_early_but_inside_the_tolerance_is_accepted():
    """Nearest, not next: the honest nearest observation to T+300 may
    be a little before it, and 150s early is inside the tolerance."""
    out = take("300S", 300, at=T0 + timedelta(seconds=200))
    assert out["status"] == mk.OBSERVED
    assert out["observedLagMs"] < 0


def test_an_unelapsed_horizon_is_not_written_at_all():
    """The row is unique per (decision, horizon): filing NOT_YET_MATURE
    would block the real answer later."""
    out = mk.markout(horizon="300S", horizon_s=300, decision_at=T0,
                     position=POSITION, book=book(),
                     observed_at=T0 + timedelta(seconds=10),
                     now=T0 + timedelta(seconds=10))
    assert out["status"] == mk.NOT_YET_MATURE


def test_no_book_at_all_is_not_identified_and_not_zero():
    out = mk.markout(horizon="60S", horizon_s=60, decision_at=T0,
                     position=POSITION, book=None, observed_at=None,
                     now=T0 + timedelta(minutes=5))
    assert out["status"] == mk.NOT_IDENTIFIED
    assert out["midMarkoutUsd"] is None


# ── two numbers, never collapsed ─────────────────────────────────────


def test_both_marks_are_taken_and_they_are_not_the_same_number():
    out = take()
    # mid 0.45 against an entry of 0.414 on 500 contracts
    assert out["markPrice"] == pytest.approx(0.45)
    assert out["midMarkoutUsd"] == pytest.approx(500 * (0.45 - 0.414))
    # the exit walks the BID at 0.44, not the mid
    assert out["exitVwap"] == pytest.approx(0.44)
    assert out["executableMarkoutUsd"] == pytest.approx(
        500 * (0.44 - 0.414))
    assert out["executableMarkoutUsd"] < out["midMarkoutUsd"]


def test_a_bid_side_that_absorbs_only_part_marks_only_that_part():
    """"Never invent liquidity." Valuing the remainder at the last
    price would do exactly that."""
    out = take(book=book(bids=[{"px": "44", "qty": "12000"}]))
    assert out["exitableQty"] == pytest.approx(120.0)
    assert out["executableMarkoutUsd"] == pytest.approx(
        120 * (0.44 - 0.414))
    assert out["positionQty"] == 500.0        # the coverage is visible


def test_an_empty_bid_side_marks_the_quote_but_not_an_exit():
    out = take(book=book(bids=[]))
    # one-sided: no mid either, so nothing was measurable
    assert out["status"] == mk.NOT_IDENTIFIED
    assert out["executableMarkoutUsd"] is None


def test_a_one_sided_book_with_bids_still_yields_an_exit_mark():
    out = take(book=book(offers=[]))
    assert out["status"] == mk.OBSERVED
    assert out["midMarkoutUsd"] is None       # no mid without both sides
    assert out["executableMarkoutUsd"] == pytest.approx(
        500 * (0.44 - 0.414))


def test_a_loss_is_reported_as_a_loss():
    out = take(book=book(bids=[{"px": "30", "qty": "60000"}],
                         offers=[{"px": "32", "qty": "60000"}]))
    assert out["midMarkoutUsd"] < 0
    assert out["executableMarkoutUsd"] < 0


# ── the row says which book it came from ─────────────────────────────


def test_an_observed_markout_names_its_book_and_its_regime():
    out = take()
    assert out["l2BookSha"] == "bk16"
    assert out["l2EvidenceId"] == "l2ev_1"
    assert out["latencyRegime"] == "GITHUB_BRIDGE"
    assert out["targetAt"] == T0 + timedelta(seconds=60)
    assert out["entryVwap"] == 0.414
