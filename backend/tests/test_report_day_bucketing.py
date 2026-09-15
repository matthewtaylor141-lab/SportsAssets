"""Reporting-day bucketing (regression, 2026-08-21): the platform's
reporting day is US/Eastern (track_record.RECORD_TZ — a settlement
after 8pm ET was landing on the wrong calendar day, owner report
2026-08-05). The PDF report surfaces were still bucketing by UTC:
period bounds at UTC midnight and daily rows by UTC date, so
2026-08-05T01:30Z (Aug 4, 9:30pm ET) money wore Aug 5's box and
month/week boundaries cut 4-5 hours early."""

from datetime import date, datetime, time, timedelta, timezone

from sportsassets.api.reports import (
    RECORD_TZ,
    _record_day,
    group_daily_et,
    period_bounds,
)


def test_period_bounds_are_eastern_midnights():
    start, end, label = period_bounds("monthly", date(2026, 8, 21))
    assert start == datetime.combine(date(2026, 8, 1), time.min,
                                     tzinfo=RECORD_TZ)
    assert end == datetime.combine(date(2026, 8, 21), time.max,
                                   tzinfo=RECORD_TZ)
    # ET midnight, not UTC midnight: Aug 1 00:00 ET is 04:00Z.
    assert start.astimezone(timezone.utc) == datetime(
        2026, 8, 1, 4, 0, tzinfo=timezone.utc)
    assert "August 2026" in label


def test_weekly_is_seven_eastern_days_inclusive():
    start, end, _ = period_bounds("weekly", date(2026, 8, 21))
    assert start == datetime.combine(date(2026, 8, 15), time.min,
                                     tzinfo=RECORD_TZ)
    assert end.date() == date(2026, 8, 21)
    assert end - start > timedelta(days=6)


def test_late_evening_et_settlement_stays_in_its_period():
    start, end, _ = period_bounds("monthly", date(2026, 8, 31))
    # Aug 4, 9:30pm ET is stamped 2026-08-05T01:30Z: August money.
    assert start <= datetime(2026, 8, 5, 1, 30, tzinfo=timezone.utc) <= end
    # Jul 31, 10pm ET is stamped 02:00Z Aug 1: JULY money — outside.
    assert not (start <= datetime(2026, 8, 1, 2, 0,
                                  tzinfo=timezone.utc) <= end)
    # Aug 31, 11:30pm ET is stamped 03:30Z Sep 1: still August — inside.
    assert start <= datetime(2026, 9, 1, 3, 30, tzinfo=timezone.utc) <= end


def test_regression_0130z_settlement_lands_on_aug_4():
    """The canonical case: 2026-08-05T01:30:00Z = Aug 4, 9:30pm ET must
    bucket on 2026-08-04 in the reports' daily grouping."""
    days = group_daily_et(
        [(datetime(2026, 8, 5, 1, 30, tzinfo=timezone.utc), 25.0)], [])
    assert [d["date"] for d in days] == ["2026-08-04"]
    assert days[0]["pnl"] == 25.0


def test_events_either_side_of_utc_midnight_share_one_et_day():
    evs = [
        (datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc), 5.0),   # 7pm ET
        (datetime(2026, 8, 5, 1, 30, tzinfo=timezone.utc), 7.0),   # 9:30pm ET
    ]
    days = group_daily_et(evs, [])
    assert len(days) == 1
    assert days[0]["date"] == "2026-08-04"
    assert days[0]["pnl"] == 12.0


def test_record_day_string_is_eastern():
    assert _record_day(
        datetime(2026, 8, 5, 1, 30, tzinfo=timezone.utc)) == "2026-08-04"
    assert _record_day(
        datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc)) == "2026-08-05"
