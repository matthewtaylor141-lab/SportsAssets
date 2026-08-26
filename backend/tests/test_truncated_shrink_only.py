"""A truncated position book forfeited the whole whale.

_fetch_positions pages the venue to POSITIONS_MAX and raises when the
venue is still handing back full pages. The caller then skipped that
whale entirely -- and the census showed truncated_books=4 against
whales=3, so MOST of the roster was skipped on every cycle,
permanently. The position-diff lane, built precisely for whales who
exit by merging rather than selling, had detected exactly zero exits.

The forfeit is right for a VANISH and far too strong for a SHRINK:

  vanish  an asset absent from `now` may simply sit past the read cut,
          so treating it as a 100% exit fires a real sell order on a
          position the whale still holds.
  shrink  an asset present in BOTH reads was observed twice and its
          size fell by a measured amount. Nothing beyond the cut
          changes that.

So the partial book now travels on the exception and the caller acts on
shrinks only.
"""

from __future__ import annotations

import inspect

from sportsassets.workers import whale_exits as we


class TestTheExceptionCarriesTheBook:
    def test_it_takes_the_partial_book(self):
        e = we.TruncatedPositions("x", {"a": 5.0}, {"a": "b"}, 7)
        assert e.book == {"a": 5.0} and e.sibs == {"a": "b"} and e.seen == 7

    def test_it_defaults_to_empty_rather_than_None(self):
        e = we.TruncatedPositions("x")
        assert e.book == {} and e.sibs == {} and e.seen == 0

    def test_the_money_consequence_is_still_documented(self):
        assert "real sell orders" in (we.TruncatedPositions.__doc__ or "")

    def test_the_fetch_passes_the_book_through(self):
        src = inspect.getsource(we._fetch_positions)
        assert "raise TruncatedPositions(" in src
        assert "out, sibs, seen)" in src


class TestOnlyShrinksSurviveATruncatedRead:
    def test_a_vanish_is_never_an_exit_on_a_partial_book(self):
        """The whole safety property. `not_an_exit` is every gone asset,
        so diff_exits can only return shrinks."""
        prev = {"held": 100.0, "past_the_cut": 50.0}
        now = {"held": 40.0}
        gone = [a for a in prev if a not in now]
        found = we.diff_exits(prev, now, set(gone))
        assert [a for a, _ in found] == ["held"]
        assert "past_the_cut" not in {a for a, _ in found}

    def test_the_shrink_keeps_its_measured_fraction(self):
        prev, now = {"held": 100.0}, {"held": 40.0}
        assert we.diff_exits(prev, now, set()) == [("held", 0.6)]

    def test_the_cycle_takes_the_shrink_only_branch(self):
        src = inspect.getsource(we._cycle)
        assert "if partial:" in src
        i = src.index("if partial:")
        block = src[i:i + 700]
        assert "diff_exits(prev, now, set(gone))" in block

    def test_skipped_vanishes_are_counted(self):
        src = inspect.getsource(we._cycle)
        assert "partial_vanished_skipped" in src

    def test_the_counter_is_always_present(self):
        src = inspect.getsource(we._cycle)
        head = src[:src.index("all_sibs")]
        assert '"partial_vanished_skipped": 0' in head


class TestAPartialReadNeverBecomesTheSnapshot:
    def test_it_keeps_what_it_could_not_see(self):
        """Saving `now` wholesale after a truncated read makes every
        unread position look VANISHED next cycle -- a read limit turned
        into a fleet of false full exits."""
        src = inspect.getsource(we._cycle)
        assert "to_save = dict(prev) if partial else {}" in src
        assert "to_save.update(now)" in src

    def test_a_full_read_still_starts_from_now_alone(self):
        """A complete book must not inherit stale assets."""
        src = inspect.getsource(we._cycle)
        i = src.index("to_save = dict(prev) if partial else {}")
        assert "else {}" in src[i:i + 60]

    def test_the_arithmetic_end_to_end(self):
        prev = {"a": 100.0, "b": 100.0, "c": 100.0}
        now = {"a": 40.0}                      # b, c past the cut
        gone = [x for x in prev if x not in now]
        to_save = dict(prev)
        to_save.update(now)
        assert to_save == {"a": 40.0, "b": 100.0, "c": 100.0}
        # next cycle, still truncated, nothing new: no phantom exits
        again = we.diff_exits(to_save, now, set(gone))
        assert [x for x, _ in again] == []


class TestTheTwoReadersAgree:
    def test_the_ceiling_matches_positions_sync(self):
        """Two readers disagreeing about what 'his whole book' means is
        how one of them ends up wrong."""
        from pathlib import Path

        import sportsassets.workers.whale_exits as _we

        other = (Path(_we.__file__).resolve().parents[1]
                 / "positions_sync.py").read_text()
        assert f"MAX_POSITIONS_PER_WALLET = {we.POSITIONS_MAX}" in other
