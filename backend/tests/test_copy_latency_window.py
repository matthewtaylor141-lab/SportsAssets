"""How fast are we copying RIGHT NOW is a different question from
how fast we copied in August.

Owner, 2026-08-26, on my quoting lat_med=187.2s as current: "I believe
the 187 is for the whole month (when we had longer latency). Now we are
quick on latency."

He is right, and the instrument says so in two places:

  * api_edge_decay's window is since_day, DEFAULTING TO 2026-08-01, and
    the hourly probe calls it with no argument -- so every lat_med the
    probe has ever printed is month-to-date.
  * it selects `status = 'settled'` only. A copy placed today cannot
    appear until its market resolves, so the figure is structurally
    incapable of describing today no matter what window is passed.

I quoted it as current anyway and built a diagnosis on top of it. This
file pins the replacement so the same mistake needs a deliberate edit.
"""

from __future__ import annotations

import inspect

import pytest

from sportsassets.api import app as app_mod


class FakePool:
    def __init__(self, rows):
        self.rows = rows
        self.args = None

    async def fetch(self, sql, *args):
        self.sql, self.args = sql, args
        return self.rows


class TestTheOldMetricIsWhatItIs:
    def test_edge_decay_defaults_to_month_to_date(self):
        sig = inspect.signature(app_mod.api_edge_decay)
        assert sig.parameters["since_day"].default == "2026-08-01"

    def test_edge_decay_counts_settled_rows_only(self):
        """The half that matters most: no window argument can make a
        settled-only query describe today."""
        src = inspect.getsource(app_mod.api_edge_decay)
        assert "status = 'settled'" in src

    def test_the_new_endpoint_does_neither(self):
        # CODE, NOT PROSE. The docstring explains the settled-only
        # defect at length, so a bare substring search found my own
        # explanation and failed. Reading commentary as if it were
        # behaviour is the same class of error as the metric itself.
        src = _code(app_mod.api_copy_latency)
        # The endpoint legitimately COUNTS settled rows in a FILTER
        # clause -- a bare substring ban flagged that and was wrong. The
        # property that matters is that the WHERE clause does not
        # RESTRICT the population to settled rows.
        where = src[src.index("FROM live_orders"):]
        where = where[:where.index("GROUP BY")]
        assert "settled" not in where, (
            "the window query still restricts to settled rows, so it "
            "cannot describe copies placed today")
        assert "make_interval(hours =>" in where
        assert "reaction_s" in src


class TestTheWindow:
    @pytest.mark.asyncio
    async def test_the_hours_argument_reaches_the_query(self):
        pool = FakePool([])
        app_mod.get_pool = _const(pool)
        await app_mod.api_copy_latency(hours=6)
        assert pool.args == (6,)

    def test_it_defaults_to_a_day_not_a_month(self):
        # Calling the coroutine directly bypasses FastAPI's dependency
        # resolution, so `hours` arrives as the Query object itself --
        # asserting on the returned value would grade the stub, not the
        # default. Read the declared default instead.
        sig = inspect.signature(app_mod.api_copy_latency)
        assert sig.parameters["hours"].default.default == 24

    def test_the_bound_allows_a_long_look_back_too(self):
        """Comparing 24h against 60d in one instrument is the whole
        point -- if they disagree, the disagreement IS the finding."""
        sig = inspect.signature(app_mod.api_copy_latency)
        bounds = {type(m).__name__: getattr(m, type(m).__name__.lower())
                  for m in sig.parameters["hours"].default.metadata}
        assert bounds["Le"] == 24 * 60
        assert bounds["Ge"] == 1


class TestFreshShare:
    def test_reclaimed_copies_are_counted_separately(self):
        """copy_sweep's reclaim calls maybe_execute with reaction=None,
        so those rows carry NULL reaction_s and vanish from every
        percentile. A fast median over a small fresh minority is not a
        fast sleeve."""
        src = inspect.getsource(app_mod.api_copy_latency)
        assert "count(reaction_s)" in src
        assert "fresh_share" in src

    @pytest.mark.asyncio
    async def test_fresh_share_is_timed_over_total(self):
        pool = FakePool([{
            "whale": "rn1", "n": 100, "n_timed": 25, "filled": 40,
            "settled": 10, "rejected": 50, "unfilled": 10,
            "p50": 1.5, "p90": 9.0, "p99": 40.0, "worst": 91.0,
            "under_5s": 20, "under_30s": 24}])
        app_mod.get_pool = _const(pool)
        out = await app_mod.api_copy_latency(hours=24)
        assert out["whales"][0]["fresh_share"] == 0.25

    @pytest.mark.asyncio
    async def test_no_rows_does_not_divide_by_zero(self):
        pool = FakePool([{
            "whale": "x", "n": 0, "n_timed": 0, "filled": 0,
            "settled": 0, "rejected": 0, "unfilled": 0,
            "p50": None, "p90": None, "p99": None, "worst": None,
            "under_5s": 0, "under_30s": 0}])
        app_mod.get_pool = _const(pool)
        out = await app_mod.api_copy_latency(hours=24)
        assert out["whales"][0]["fresh_share"] is None
        assert out["whales"][0]["p50"] is None


class TestItAnswersTheQuestionAsked:
    def test_it_reports_the_fast_buckets(self):
        """"Are we inside a second" needs a count, not a median: a
        median of 2s with a p99 of 400s is a different sleeve from a
        median of 2s with a p99 of 6s."""
        src = inspect.getsource(app_mod.api_copy_latency)
        assert "under_5s" in src and "under_30s" in src
        assert "0.99" in src

    def test_it_covers_every_status_not_just_the_ones_that_worked(self):
        """A rejection for stale-signal is a latency FACT. Counting only
        fills would hide exactly the slow tail we are hunting."""
        src = inspect.getsource(app_mod.api_copy_latency)
        for st in ("filled", "settled", "rejected", "unfilled"):
            assert f"status = '{st}'" in src

    def test_the_other_sleeves_are_excluded(self):
        """manual and underdog do not copy a whale and have no reaction
        to measure; blending them would move the copy sleeve's number."""
        src = inspect.getsource(app_mod.api_copy_latency)
        assert "'manual', 'underdog'" in src


def _code(fn) -> str:
    """Source with the docstring removed -- tests must read what the
    function DOES, not what it says about itself."""
    src = inspect.getsource(fn)
    doc = fn.__doc__
    return src.replace(doc, "") if doc else src


def _const(v):
    async def _f():
        return v
    return _f
