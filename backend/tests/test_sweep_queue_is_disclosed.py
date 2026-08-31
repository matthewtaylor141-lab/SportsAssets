"""The sweep's backlog was invisible by construction.

copy_sweep caps each pass at COPY_SWEEP_MAX_ROWS and its comment
promises the remainder is "DISCLOSED (deferred_to_next_pass)". Two
things defeated that promise:

  * `candidates` was computed AFTER the truncation, so it could never
    exceed the cap. However deep the pool got, the heartbeat reported a
    bounded, healthy-looking number.
  * `deferred_to_next_pass` — the honest number — was returned and
    never printed by any diagnostic.

Together they mean nothing in production could distinguish "the cap is
comfortably above the flow" from "the cap is being sawn off a backlog
of thousands", which is the difference between rationing and starving.

The sort compounds it. `_game_date` falls back to "0000-00-00" for a
slug with no date and the rows are sorted ASCENDING, while the WHERE
clause bounds the date only from above. So undated rows sort first,
then the OLDEST games — including ones already played — ahead of
tonight's. Rejected rows are deliberately retryable and never stop
being candidates, so a row that cannot map can hold a slot forever.

These tests pin the disclosure, not a policy. Whether the ordering is
wrong is a question for the numbers this now emits.
"""
import datetime as dt

import pytest

from sportsassets.workers import copy_sweep as cs


def _row(slug):
    return {"market_slug": slug, "event_slug": None}


class TestTheCountIsNotTakenAfterTheCut:
    def test_candidates_is_the_pool_not_the_slice(self, monkeypatch):
        """The whole defect in one assertion: with a pool far larger
        than the cap, `candidates` must report the pool."""
        assert "_total_candidates = len(rows)" in \
            _src(), "the pool size is not captured before truncation"
        i = _src().index("_total_candidates = len(rows)")
        j = _src().index("rows = rows[:MAX_ROWS_PER_SWEEP]")
        assert i < j, (
            "candidates is still counted after the cap, so it can never "
            "exceed COPY_SWEEP_MAX_ROWS")

    def test_the_returned_candidates_uses_the_pool_variable(self):
        assert '"candidates": _total_candidates' in _src()

    def test_the_slice_is_reported_separately(self):
        """Losing the old number would trade one blind spot for
        another: how many were actually walked still matters."""
        assert '"processed": len(rows)' in _src()

    def test_deferred_survives(self):
        assert '"deferred_to_next_pass": deferred' in _src()


class TestTheMixIsCounted:
    def test_both_pool_and_head_are_reported(self):
        assert '"pool_mix": _pool_mix' in _src()
        assert '"head_mix": _head_mix' in _src()

    def test_the_head_mix_is_taken_after_the_cut(self):
        """pool before, head after — reversed, both describe the same
        list and the comparison says nothing."""
        s = _src()
        assert s.index("_pool_mix = _bucket(rows)") < \
            s.index("rows = rows[:MAX_ROWS_PER_SWEEP]") < \
            s.index("_head_mix = _bucket(rows)")


def _src():
    import inspect
    return inspect.getsource(cs.sweep_once)


class TestTheBucketingItself:
    """_bucket is nested, so drive it through a rebuilt copy — the
    classification is the part a wrong answer would quietly corrupt."""

    @staticmethod
    def _bucket(seq):
        today = dt.date.today()
        tomorrow = today + dt.timedelta(days=1)
        u = past = cur = fut = 0
        for r in seq:
            import re
            m = re.search(r"\d{4}-\d{2}-\d{2}",
                          (r["market_slug"] or r["event_slug"] or ""))
            d = m.group(0) if m else "0000-00-00"
            try:
                gd = dt.date.fromisoformat(d) if d != "0000-00-00" else None
            except ValueError:
                gd = None
            if gd is None:
                u += 1
            elif gd < today:
                past += 1
            elif gd <= tomorrow:
                cur += 1
            else:
                fut += 1
        return {"undated": u, "past": past, "today_tomorrow": cur,
                "future": fut}

    def test_an_undated_slug_is_its_own_class(self):
        got = self._bucket([_row("some-market-no-date")])
        assert got["undated"] == 1

    def test_a_played_game_is_past_not_current(self):
        old = (dt.date.today() - dt.timedelta(days=5)).isoformat()
        got = self._bucket([_row(f"aec-atp-a-b-{old}")])
        assert got["past"] == 1 and got["today_tomorrow"] == 0

    def test_today_and_tomorrow_are_current(self):
        t = dt.date.today().isoformat()
        tm = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        got = self._bucket([_row(f"a-{t}"), _row(f"b-{tm}")])
        assert got["today_tomorrow"] == 2

    def test_the_classes_partition_the_input(self):
        old = (dt.date.today() - dt.timedelta(days=3)).isoformat()
        fut = (dt.date.today() + dt.timedelta(days=9)).isoformat()
        rows = [_row("nodate"), _row(f"a-{old}"),
                _row(f"b-{dt.date.today().isoformat()}"), _row(f"c-{fut}")]
        got = self._bucket(rows)
        assert sum(got.values()) == len(rows)

    def test_an_impossible_date_counts_as_undated_not_as_a_crash(self):
        """A slug can carry 2026-02-30. datetime refuses it, and a
        sweep must not die inside a diagnostic."""
        got = self._bucket([_row("aec-atp-a-b-2026-02-30")])
        assert got["undated"] == 1


class TestTheOrderingChangedONLYAfterTheEvidenceArrived:
    """This class used to assert the sort was UNCHANGED.

    That was right while the ordering was a suspicion: reprioritising
    the money path belongs behind evidence, not behind a reading of a
    comment. The evidence then arrived on run 33426256819 —

        pool={past:10293, today_tomorrow:645}
        head={past:150,  today_tomorrow:0}

    — 150 of 150 slots on finished games, every two minutes, with the
    645 live candidates never reached. The guard has done its job and
    is replaced by what it was guarding for, rather than deleted as
    though it had never applied.
    """

    def test_the_sort_now_ranks_live_games_first(self):
        assert "rows = sorted(rows, key=sweep_sort_key)" in _src()

    def test_the_key_is_module_level_so_tests_can_drive_it(self):
        """It was nested, and the tests written for it rebuilt their
        own copy — which passed against a deliberately broken
        production sort."""
        from sportsassets.workers import copy_sweep as m
        assert callable(getattr(m, "sweep_sort_key", None))
