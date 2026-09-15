"""Two detectors see one economic exit and both act on it.

classify_exit reads the whale's exit as a complement BUY in the trade
feed, seconds after it happens. whale_exits reads the SAME exit as a
drop in his /positions snapshot, up to 120s later. Both call
mirror_exit; nothing correlated the two observations.

copy_exit_applied dedupes the trade-driven lane by trade_id. The
position lane carries no trade id, so that guard cannot see it. The
atomic status='exiting' claim serializes the callers without
deduplicating them: the first sale writes the row back to 'filled' with
the remainder, and the second claims that row and sells a fraction of
what is LEFT.

    He trims 30% of a position we hold 200 of.
    Trade lane:    sells 60, leaves 140.
    Position lane: reads the same 30% drop, sells 30% of 140 = 42.
    We are 51% out of a position he is 30% out of, having paid two
    spread crossings to get there.
"""

from __future__ import annotations

import inspect

import pytest

from sportsassets import live_executor as le


class TestTheArithmeticOfTheDefect:
    def test_applying_the_fraction_twice_overshoots(self):
        ours, frac = 200, 0.30
        first = int(ours * frac)
        left = ours - first
        second = int(left * frac)
        assert first + second == 102
        assert (first + second) / ours == pytest.approx(0.51, abs=0.005)
        assert first / ours == pytest.approx(0.30)

    def test_a_third_pass_would_overshoot_further(self):
        ours, frac, sold = 200, 0.30, 0
        for _ in range(3):
            n = int((ours - sold) * frac)
            sold += n
        assert sold / ours > 0.65


class TestThePositionLaneChecksTheOtherLane:
    def test_the_guard_exists_and_is_keyed_on_the_ROW(self):
        """The row is the only identifier both lanes share -- the trade
        lane has a trade id and the position lane does not."""
        src = inspect.getsource(le.mirror_exit)
        assert "WHERE row_id = $1" in src
        assert "copy_exit_applied" in src

    def test_it_applies_ONLY_when_there_is_no_trade_id(self):
        """A caller with a trade id was already deduped by the ledger
        check. Applying a time window to it as well would refuse his
        genuine second trade on the same market inside the window."""
        src = inspect.getsource(le.mirror_exit)
        i = src.index("WHERE row_id = $1")
        head = src[:i]
        assert head.rstrip().endswith("(") or "_xtid is None" in head[-600:]

    def test_the_window_is_bounded_and_tunable(self):
        assert le.EXIT_DEDUP_MINUTES > 0
        assert le.EXIT_DEDUP_MINUTES <= 60, \
            "too long a window starts refusing his genuine second trim"

    def test_the_window_covers_several_whale_exit_cycles(self):
        """whale_exits polls every 120s; the window has to be longer
        than the lag between a fill reaching the trade feed and reaching
        his positions payload."""
        from sportsassets.workers import whale_exits as we

        assert le.EXIT_DEDUP_MINUTES * 60 >= we.INTERVAL_S * 3

    def test_an_unreadable_ledger_REFUSES(self):
        """Not knowing whether the other lane already sold is not
        permission to sell again."""
        src = inspect.getsource(le.mirror_exit)
        i = src.index("WHERE row_id = $1")
        block = src[i:i + 700]
        assert "mx_exit_dedup_unreadable" in block

    def test_that_refusal_has_its_OWN_name(self):
        """Sharing mx_exit_ledger_unreadable with the trade-id check
        would leave the census unable to say which lane failed, and the
        two have different causes and different fixes."""
        src = inspect.getsource(le.mirror_exit)
        assert src.count('"mx_exit_ledger_unreadable"') == 1
        assert src.count('"mx_exit_dedup_unreadable"') == 1

    def test_a_recent_mirror_is_SETTLED_not_pending(self):
        """The exit really was applied -- by the other lane. Holding it
        pending would make whale_exits re-offer it every cycle
        forever."""
        assert "mx_exit_recently_applied" not in le.EXIT_PENDING_REASONS
        assert "mx_exit_dedup_unreadable" not in le.EXIT_PENDING_REASONS


class TestThePositionLaneRecordsItsOwnSales:
    def test_it_writes_a_ledger_row(self):
        """Otherwise the window above has nothing to find, and the lane
        cannot even dedupe against ITSELF."""
        src = inspect.getsource(le.mirror_exit)
        assert src.count("INSERT INTO copy_exit_applied") == 2

    def test_the_synthetic_key_cannot_collide_with_a_real_trade(self):
        """trade_id is the primary key. Real trade ids are positive, so
        the position lane uses the negated row id."""
        src = inspect.getsource(le.mirror_exit)
        assert '-int(row["id"])' in src

    def test_a_failed_record_does_not_undo_the_sale(self):
        src = inspect.getsource(le.mirror_exit)
        i = src.index('-int(row["id"])')
        head = src[max(0, i - 900):i]
        assert "except Exception" in head or "log.warning" in src[i:i + 600]


class TestTheCensusCanNameIt:
    def test_both_new_reasons_are_in_the_verdict_list(self):
        from sportsassets.api import app as A

        src = inspect.getsource(A.admin_exit_census)
        assert "mx_exit_recently_applied" in src
        assert "mx_exit_dedup_unreadable" in src

    def test_a_standing_count_means_the_detectors_are_racing(self):
        """It is not a defect on its own -- it is the position lane
        correctly declining. A persistent count is still worth seeing,
        which is why it is listed rather than suppressed."""
        from sportsassets.api import app as A

        src = inspect.getsource(A.admin_exit_census)
        i = src.index("mx_exit_recently_applied")
        assert "racing" in src[max(0, i - 400):i]
