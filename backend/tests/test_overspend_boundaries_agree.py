"""The ask guard admitted what the breaker treated as a defect.

One decision -- how much overspend is acceptable -- was encoded in two
literals seven hundred lines apart, and they disagreed.

The pre-trade ask guard ADMITS a fill whose ask is up to 1.08x our
limit, and says so in its own comment: "worst case this now permits is
an 8% overspend on a clip -- about $20 -- against losing every copy". It
was widened to exactly that after `ask > limit` made the sleeve sterile
within the hour.

The post-fill breaker then halted the ENTIRE sleeve -- every whale,
every entry, and every exit, since it gates the top of both
maybe_execute and mirror_exit -- above 1.01x. Its record self-clears
backwards only, so the halt stands until a human edits the database.

The three production refusals the 1.08 threshold was raised to admit are
recorded verbatim in tests/test_ask_guard.py. Every one of them passes
the guard and trips the breaker. And the whole premise of the ask guard
is that our limit is NOT enforced at execution -- "the order behaves
like a market IOC and takes the book" -- so a fill at the admitted ask
is the EXPECTED outcome. The expected outcome bricked the sleeve.

Whichever premise about limit enforcement is true, one of those two
numbers was wrong. Nothing in the suite connected them. This file is
that connection.
"""

from __future__ import annotations

import inspect

import pytest

from sportsassets import live_executor as le

# THE REAL FUNCTION, captured at import.
#
# conftest neutralizes le.overspend_halt for the whole suite -- the stub
# pools answer every fetchval truthily, so the breaker would read
# "tripped" and short-circuit every gate test. Binding the module
# attribute here would test the neutralizer instead of the breaker,
# which is precisely the class of defect this file exists about.
_REAL_OVERSPEND_HALT = le.overspend_halt

# The three production refusals, verbatim from test_ask_guard.py:
# (limit we authorized, ask the venue quoted).
HONEST = [(0.57, 0.59), (0.88, 0.89), (0.84, 0.85)]
# The wrong-side population the breaker was actually built for. Ratios
# from the incident record in live_executor's own comment block.
WRONG_SIDE = [(0.23, 0.89), (0.32, 0.6853), (0.37, 0.65),
              (0.45, 0.56), (0.48, 0.55)]


def _ratio(limit, ask):
    """What a 1-for-1 fill at the ask costs against what we authorized.

    Expressed through the production predicate's own denominator: one
    share at `limit` is the authorization, one share at `ask` is the
    spend.
    """
    return le.overspend_ratio(limit, 1.0, ask)


class TestTheTwoBoundariesCannotDisagree:
    def test_the_halt_is_never_below_what_we_authorize(self):
        """The invariant. A fill at or under what the PRE-TRADE guard
        admitted cannot be evidence that something went wrong -- it is
        the thing we asked for."""
        assert le.OVERSPEND_HALT_RATIO >= le.ASK_TOLERANCE

    def test_the_ask_guard_reads_the_shared_constant(self):
        """Restating the env inline is how they came to disagree."""
        src = inspect.getsource(le.maybe_execute)
        assert "_tol = ASK_TOLERANCE" in src
        assert 'os.getenv("LIVE_ASK_TOLERANCE_PCT"' not in src

    def test_tightening_the_ask_guard_tightens_the_halt(self, monkeypatch):
        """max(), not a plain env read: the halt follows the guard down
        and cannot be configured below it."""
        assert le.OVERSPEND_HALT_RATIO == max(
            le.ASK_TOLERANCE,
            float(__import__("os").environ.get(
                "LIVE_OVERSPEND_HALT_RATIO", "1.08")))

    def test_halting_strictly_implies_recording(self):
        """Nothing may halt that was not also stamped on its row."""
        assert le.OVERSPEND_HALT_RATIO >= le.OVERSPEND_TOLERANCE
        for r in (1.005, 1.02, 1.05, 1.09, 1.2, 3.9):
            if le.is_halting_overspend(100.0, 1.0, r * 100.0):
                assert le.is_overspend(100.0, 1.0, r * 100.0)


class TestEveryHonestFillUsedToBrickTheSleeve:
    @pytest.mark.parametrize("limit,ask", HONEST)
    def test_the_ask_guard_admits_it(self, limit, ask):
        assert ask <= limit * le.ASK_TOLERANCE, \
            "the guard would refuse an honest copy for nothing"

    @pytest.mark.parametrize("limit,ask", HONEST)
    def test_the_old_breaker_would_have_halted_on_it(self, limit, ask):
        """The defect, stated as arithmetic against the OLD constant."""
        assert _ratio(limit, ask) > le.OVERSPEND_TOLERANCE

    @pytest.mark.parametrize("limit,ask", HONEST)
    def test_it_no_longer_halts_the_sleeve(self, limit, ask):
        assert not le.is_halting_overspend(limit, 1.0, ask), \
            f"one ordinary copy at {limit}->{ask} stops every copy and " \
            f"every mirror exit until someone opens the database"

    @pytest.mark.parametrize("limit,ask", HONEST)
    def test_it_is_STILL_recorded_on_the_row(self, limit, ask):
        """Nothing became invisible. The band between recording and
        halting is stamped and logged; it just does not brick."""
        assert le.is_overspend(limit, 1.0, ask)


class TestTheDefectItWasBuiltForStillHalts:
    @pytest.mark.parametrize("limit,fill", WRONG_SIDE)
    def test_the_wrong_side_population_is_caught_in_full(self, limit, fill):
        """Moving the halt boundary must not cost a single detection.
        These are the five rows that prompted the breaker."""
        assert le.is_halting_overspend(limit, 1.0, fill), \
            f"the incident row {limit}->{fill} no longer halts"

    def test_the_two_populations_do_not_overlap(self):
        """Why a boundary between them exists at all: worst honest
        1.035, cheapest real 1.146."""
        worst_honest = max(_ratio(a, b) for a, b in HONEST)
        cheapest_real = min(_ratio(a, b) for a, b in WRONG_SIDE)
        assert worst_honest < cheapest_real
        assert worst_honest < le.OVERSPEND_HALT_RATIO < cheapest_real, \
            f"the boundary {le.OVERSPEND_HALT_RATIO} is not between " \
            f"{worst_honest:.3f} and {cheapest_real:.3f}"

    def test_the_pre_trade_guard_refuses_them_first(self):
        """Defence in depth: the breaker is what catches the case the
        ask guard failed to prevent, not the only thing standing there."""
        for limit, fill in WRONG_SIDE:
            assert fill > limit * le.ASK_TOLERANCE


class TestABreakerRecordThatDisprovesItself:
    @pytest.mark.asyncio
    async def test_a_false_positive_record_self_clears(self):
        """The sleeve sat halted from 2026-08-25 19:20:51Z on a record
        reading ratio 0.957 -- an UNDERSPEND, asked $67.68 and filled
        $64.80 -- written by a breaker comparing a short's cost against
        a long's denomination. That bug is fixed; the record it left
        could only be cleared by hand."""
        pool = _Pool({"at": "2026-08-25T19:20:51", "ratio": 0.957,
                      "asked": 67.68, "spent": 64.80})
        assert await _REAL_OVERSPEND_HALT(pool) is None
        assert pool.deleted, "the halt record was left in place"

    @pytest.mark.asyncio
    async def test_a_record_inside_the_authorized_band_self_clears(self):
        pool = _Pool({"at": "2026-08-26T12:00:00", "ratio": 1.035})
        assert await _REAL_OVERSPEND_HALT(pool) is None

    @pytest.mark.asyncio
    async def test_a_REAL_breach_is_untouched(self):
        """The clause must not become a way out of a genuine halt."""
        pool = _Pool({"at": "2026-08-26T12:00:00", "ratio": 1.146})
        assert await _REAL_OVERSPEND_HALT(pool) is not None
        assert not pool.deleted

    @pytest.mark.asyncio
    async def test_a_record_with_no_ratio_is_untouched(self):
        pool = _Pool({"at": "2026-08-26T12:00:00", "why": "something"})
        assert await _REAL_OVERSPEND_HALT(pool) is not None

    @pytest.mark.asyncio
    async def test_a_non_numeric_ratio_is_untouched(self):
        pool = _Pool({"at": "2026-08-26T12:00:00", "ratio": "lots"})
        assert await _REAL_OVERSPEND_HALT(pool) is not None

    @pytest.mark.asyncio
    async def test_an_unreadable_breaker_still_counts_as_TRIPPED(self):
        class Broken:
            async def fetchval(self, *a):
                raise RuntimeError("db down")

        assert await _REAL_OVERSPEND_HALT(Broken()) is not None

    @pytest.mark.asyncio
    async def test_no_record_is_no_halt(self):
        assert await _REAL_OVERSPEND_HALT(_Pool(None)) is None


class _Pool:
    def __init__(self, rec):
        self.rec = rec
        self.deleted = False

    async def fetchval(self, *_a):
        import json

        return None if self.rec is None else json.dumps(self.rec)

    async def execute(self, sql, *_a):
        if "DELETE" in sql:
            self.deleted = True
