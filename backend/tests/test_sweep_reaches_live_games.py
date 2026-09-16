"""Every slot went to games already played.

Run 33426256819, the first pass where the queue was observable:

    SWEEPQUEUE candidates=10938 processed=150 attempted=150 deferred=10788
    SWEEPMIX pool={"past":10293,"future":0,"undated":0,"today_tomorrow":645}
    SWEEPMIX head={"past":150,"future":0,"undated":0,"today_tomorrow":0}

Not "mostly past" — 150 of 150, every two minutes, spent on finished
games, while the 645 candidates for today and tomorrow were never
reached once. Two causes, both here:

  * the game-date filter bounded the date only from ABOVE
    (<= current_date + 1), so a played game stayed a candidate for the
    whole 7-day trade window, and rejected rows are deliberately
    retryable and never stop being candidates.
  * the sort is ascending on the date string, so among survivors the
    STALEST sorts first — the exact opposite of the "nearest game
    first ... settle soonest" the comment above it promises.

This is directly upstream of the exit funnel's 97.6%
mx_no_position_of_ours: we cannot mirror a sale on a position the
entry queue never reached.
"""
import datetime as dt
import inspect
import re

import pytest

from sportsassets.workers import copy_sweep as cs


def _sql():
    return inspect.getsource(cs.sweep_once)


TODAY = dt.date.today()


def _row(slug):
    return {"market_slug": slug, "event_slug": None}


# THE REAL KEY, IMPORTED — NOT REBUILT.
#
# The first version of this file re-implemented the sort inside the
# test. It passed against a deliberately broken production sort,
# because the test only ever agreed with itself. `sweep_sort_key` was
# hoisted to module level precisely so these assertions can drive the
# code that actually runs.
from sportsassets.workers.copy_sweep import sweep_sort_key as _key


class TestPlayedGamesLeaveTheCandidatePool:
    def test_the_date_is_bounded_from_below_as_well_as_above(self):
        assert "BETWEEN current_date - 1 AND current_date + 1" in _sql(), (
            "the filter still bounds the game date only from above, so "
            "a finished game stays a candidate for 7 days")

    def test_the_slack_is_one_day_not_zero(self):
        """The slug's date is the LOCAL game date, compared here in
        UTC. A hard `>= current_date` would drop late games that are
        still live after midnight UTC."""
        assert "current_date - 1" in _sql()
        assert "current_date - 7" not in _sql(), (
            "a week of slack re-admits the backlog this removes")

    def test_undated_slugs_still_pass(self):
        """You cannot defer what you cannot date, and the pool showed
        0 undated — removing them would be an unmeasured change."""
        s = _sql()
        i = s.index("BETWEEN current_date - 1")
        assert "IS NULL" in s[max(0, i - 400):i]


class TestALiveGameOutranksAFinishedOne:
    def test_todays_game_sorts_ahead_of_an_older_one(self):
        old = (TODAY - dt.timedelta(days=1)).isoformat()
        now = TODAY.isoformat()
        rows = [_row(f"a-{old}"), _row(f"b-{now}")]
        assert [r["market_slug"] for r in sorted(rows, key=_key)] == \
            [f"b-{now}", f"a-{old}"]

    def test_tomorrow_still_sorts_ahead_of_yesterday(self):
        y = (TODAY - dt.timedelta(days=1)).isoformat()
        tm = (TODAY + dt.timedelta(days=1)).isoformat()
        rows = [_row(f"a-{y}"), _row(f"b-{tm}")]
        assert sorted(rows, key=_key)[0]["market_slug"] == f"b-{tm}"

    def test_among_live_games_the_nearest_still_goes_first(self):
        """The original intent is preserved, not replaced."""
        now = TODAY.isoformat()
        tm = (TODAY + dt.timedelta(days=1)).isoformat()
        rows = [_row(f"a-{tm}"), _row(f"b-{now}")]
        assert sorted(rows, key=_key)[0]["market_slug"] == f"b-{now}"

    def test_the_head_of_a_mixed_queue_is_no_longer_all_past(self):
        """The regression in one shape: 150 slots, a pool like the
        measured one. Before, the head was 150/150 past."""
        y = (TODAY - dt.timedelta(days=1)).isoformat()
        now = TODAY.isoformat()
        rows = ([_row(f"old{i}-{y}") for i in range(300)]
                + [_row(f"live{i}-{now}") for i in range(60)])
        head = sorted(rows, key=_key)[:150]
        live = sum(1 for r in head if now in r["market_slug"])
        assert live == 60, (
            f"only {live} of the 60 live games reached the head")

    def test_an_undated_row_never_outranks_a_live_game(self):
        now = TODAY.isoformat()
        rows = [_row("nodate-market"), _row(f"b-{now}")]
        assert sorted(rows, key=_key)[0]["market_slug"] == f"b-{now}"


class TestTheDisclosureSurvivesTheFix:
    """The measurement is what proved this; it must not be removed by
    the change it justified."""

    def test_candidates_is_still_the_pool(self):
        assert '"candidates": _total_candidates' in _sql()

    def test_the_mixes_are_still_reported(self):
        assert '"pool_mix": _pool_mix' in _sql()
        assert '"head_mix": _head_mix' in _sql()

    def test_deferred_is_still_reported(self):
        assert '"deferred_to_next_pass": deferred' in _sql()
