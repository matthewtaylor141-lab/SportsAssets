"""Size by how far a trade sits above HIS OWN habit.

Owner, 2026-08-25:

  "if the average trade price for all our whales is $2k, and there is an
   order for $5k, I want to make sure our proportional trades reflect
   that. There is a reason that whale is putting more on that. In the
   counter side of the same logic, if the average position is $2k and
   the whale cashes out $1k in profit from a position, and then reenters
   at $500, I want our behavior to match the logic there."

What we had destroyed exactly that signal. The clip is capped at $250,
so his $5,000 conviction bet and his $250 routine one both arrived as
our $250 — every trade he made looked identical to us, and the most
informative thing in a whale's book (how much HE varies his own size)
was discarded at the last step.

Conviction is measured against HIS baseline, not ours and not the
roster's, so a $50-average whale putting on $200 reaches us as the same
2.5x as a $2k-average whale putting on $5k.
"""

import asyncio
import inspect

from sportsassets import live_executor as le


class TestTheOwnersNumbers:
    def test_five_thousand_against_a_two_thousand_average(self):
        assert le.conviction_multiple(5000, 2000) == 2.5

    def test_five_hundred_against_a_two_thousand_average(self):
        assert le.conviction_multiple(500, 2000) == 0.25

    def test_his_ordinary_size_is_neutral(self):
        assert le.conviction_multiple(2000, 2000) == 1.0

    def test_scale_free_across_whales(self):
        """A $50-average whale staking $125 has the same conviction as a
        $2,000-average whale staking $5,000. Measuring against his own
        habit is what makes the roster comparable."""
        assert le.conviction_multiple(125, 50) == le.conviction_multiple(
            5000, 2000)


class TestItIsBoundedBothWays:
    def test_an_outlier_cannot_claim_unbounded_size(self):
        assert le.conviction_multiple(1_000_000, 2000) == le.CONVICTION_MAX

    def test_dust_cannot_shrink_below_the_floor(self):
        assert le.conviction_multiple(1, 2000) == le.CONVICTION_MIN

    def test_the_bounds_are_sane(self):
        assert 0 < le.CONVICTION_MIN < 1 < le.CONVICTION_MAX


class TestNoSignalMeansNeutralNotAGuess:
    def test_no_history_is_neutral(self):
        assert le.conviction_multiple(5000, 0) == 1.0

    def test_a_zero_or_negative_trade_is_neutral(self):
        assert le.conviction_multiple(0, 2000) == 1.0
        assert le.conviction_multiple(-5, 2000) == 1.0

    def test_unparseable_inputs_are_neutral(self):
        assert le.conviction_multiple(None, 2000) == 1.0
        assert le.conviction_multiple("x", 2000) == 1.0
        assert le.conviction_multiple(5000, None) == 1.0


class TestTheAverageIsHisHabit:
    class _Pool:
        def __init__(self, n, med, raise_it=False):
            self._r = {"n": n, "med": med}
            self._raise = raise_it

        async def fetchrow(self, _sql, *_a):
            if self._raise:
                raise RuntimeError("db down")
            return self._r

    def _avg(self, n, med, whale="w", **kw):
        le._CONVICTION_CACHE.clear()
        return asyncio.run(le.whale_average_notional(
            self._Pool(n, med, **kw), whale))

    def test_a_thin_history_yields_no_average(self):
        """One or two trades is not a habit. Returning a number there
        would let a single fill define 'his usual size' forever."""
        assert self._avg(3, 2000.0) == 0.0

    def test_a_full_history_yields_the_median(self):
        assert self._avg(500, 2000.0) == 2000.0

    def test_a_database_failure_degrades_to_neutral(self):
        """This function sits ON the money path. Its whole contract is
        to degrade — the first version read the row OUTSIDE the guard
        and a row missing a key raised KeyError straight up the copy
        path, which 35 tests caught."""
        assert self._avg(500, 2000.0, raise_it=True) == 0.0

    def test_a_row_missing_its_keys_degrades_too(self):
        class _Bad:
            async def fetchrow(self, _sql, *_a):
                return {"unexpected": 1}

        le._CONVICTION_CACHE.clear()
        assert asyncio.run(le.whale_average_notional(_Bad(), "w")) == 0.0

    def test_an_empty_whale_needs_no_query(self):
        class _Boom:
            async def fetchrow(self, _sql, *_a):
                raise AssertionError("must not query for a blank whale")

        assert asyncio.run(le.whale_average_notional(_Boom(), "")) == 0.0

    def test_it_caches_so_the_copy_path_pays_once(self):
        calls = {"n": 0}

        class _Counting:
            async def fetchrow(self, _sql, *_a):
                calls["n"] += 1
                return {"n": 500, "med": 2000.0}

        le._CONVICTION_CACHE.clear()
        p = _Counting()
        for _ in range(5):
            asyncio.run(le.whale_average_notional(p, "w"))
        assert calls["n"] == 1


class TestTheQueryMeasuresTheRightThing:
    def test_it_uses_the_median_not_the_mean(self):
        """One $2M block in a book of $200 trades moves a mean enough to
        make every ordinary trade read as low conviction."""
        src = inspect.getsource(le.whale_average_notional)
        assert "percentile_cont(0.5)" in src
        assert "avg(" not in src

    def test_it_counts_entries_only(self):
        """These whales exit by BUYING the complement, so an exit is a
        BUY row. It is still an entry-shaped commitment of cash, which
        is what the baseline measures — but a SELL is not."""
        src = inspect.getsource(le.whale_average_notional)
        assert "t.side = 'BUY'" in src

    def test_it_uses_a_bounded_window(self):
        src = inspect.getsource(le.whale_average_notional)
        assert "interval '30 days'" in src


class TestItCannotBeUsedToOutbetHim:
    """The clamp is re-applied AFTER the multiply. Conviction may move
    us only WITHIN the envelope his own size defines — it can never make
    us stake more than he did, which is the thing the owner told us to
    stop doing this morning."""

    def test_the_mirror_clamp_is_reapplied_after_the_multiply(self):
        src = inspect.getsource(le.maybe_execute)
        i = src.index("per = per * _conv")
        assert "min(per, COPY_RATIO_MAX * his_notional)" in src[i:i + 400]

    def test_conviction_runs_before_the_share_count(self):
        src = inspect.getsource(le.maybe_execute)
        assert src.index("_conv = conviction_multiple") < src.index(
            "shares = float(int(per / limit))")

    def test_the_dust_floor_and_day_room_still_follow(self):
        src = inspect.getsource(le.maybe_execute)
        i = src.index("per = per * _conv")
        assert "COPY_MIN_CLIP_USD" in src[i:]
        assert "day_room" in src[i:]

    def test_a_neutral_multiple_changes_nothing(self):
        src = inspect.getsource(le.maybe_execute)
        assert "if _avg > 0 and _conv != 1.0:" in src
