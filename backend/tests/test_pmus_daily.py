"""Per-day settled P&L for the system cohort.

The cohort scorecard is cumulative, which cannot answer "how did the engine
do yesterday" without subtracting two readings taken at arbitrary times —
and those boundaries never line up with a calendar day. Every answer given
that way is approximate in a direction nobody can see.
"""

import time

import pytest
from datetime import datetime, timezone

from sportsassets.api.pmus_account import _act_ts, _daily


@pytest.fixture(autouse=True)
def _whole_history(monkeypatch):
    """These fixtures are August settlements; the display epoch (owner
    order 2026-09-02, September 1st) would floor them all away. The
    aggregation is tested over the whole history here; the floor itself
    is pinned by test_the_display_epoch_floors_the_card."""
    from sportsassets.api import track_record as _tr
    monkeypatch.setattr(_tr, "DISPLAY_EPOCH", "2020-01-01")


def _row(realized, cost=1.0, ago_s=3600, settled=True, dated=True):
    r = {"settled": settled, "cost": cost, "realized": realized}
    if dated:
        r["settled_at"] = time.time() - ago_s
    return r


def test_settlements_bucket_by_reporting_day():
    rows = [_row(3.0, ago_s=3600), _row(-1.0, ago_s=3600),
            _row(-1.0, ago_s=90_000)]
    days = _daily(rows)
    assert [d["settled"] for d in days] == [2, 1]
    assert days[0]["wins"] == 1 and days[0]["losses"] == 1
    assert days[0]["realized"] == 2.0
    assert days[0]["roi"] == 1.0


def test_regression_0130z_settlement_lands_on_the_prior_et_day():
    """The reporting day is Eastern (track_record.RECORD_TZ):
    2026-08-05T01:30Z is Aug 4, 9:30pm ET and belongs on the Aug 4 row.
    UTC bucketing put it on Aug 5 — the exact defect the track record's
    calendar fixed (owner report 2026-08-05)."""
    ts = datetime(2026, 8, 5, 1, 30, tzinfo=timezone.utc).timestamp()
    days = _daily([{"settled": True, "cost": 5.0, "realized": 2.5,
                    "settled_at": ts}], days=100_000)
    assert [d["day"] for d in days] == ["2026-08-04"]
    assert days[0]["realized"] == 2.5


def test_open_positions_are_not_pnl():
    """A position that has not resolved has realized nothing. Counting it
    would book profit we do not have."""
    assert _daily([_row(0.0, settled=False)]) == []


def test_an_undated_settlement_is_reported_undated_not_guessed_into_today():
    """A missing timestamp landing silently in today would overstate today
    and understate whichever day it belonged to — invisibly, both ways."""
    days = _daily([_row(2.0, dated=False), _row(1.0, ago_s=3600)])
    assert days[-1]["day"] == "undated"
    assert days[-1]["realized"] == 2.0
    assert days[0]["realized"] == 1.0


def test_days_read_newest_first():
    rows = [_row(1.0, ago_s=3600), _row(1.0, ago_s=90_000),
            _row(1.0, ago_s=180_000)]
    days = [d["day"] for d in _daily(rows)]
    assert days == sorted(days, reverse=True)


def test_old_settlements_fall_outside_the_window():
    assert _daily([_row(5.0, ago_s=30 * 86_400)], days=7) == []


def test_timestamps_are_read_in_whatever_field_carries_them():
    """Providers move this key between payload versions."""
    when = datetime(2026, 8, 1, 10, 49, 33, tzinfo=timezone.utc)
    assert _act_ts({"createdAt": "2026-08-01T10:49:33Z"}) == when.timestamp()
    assert _act_ts({"timestamp": when.timestamp()}) == when.timestamp()
    # Milliseconds must not be read as seconds — that lands the settlement
    # in the year 58,000 and silently outside every window.
    assert _act_ts({"timestamp": when.timestamp() * 1000}) == when.timestamp()
    assert _act_ts({}) == 0.0
