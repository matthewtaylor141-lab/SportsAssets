"""Option A ships with the instrument that judges it.

Owner order 2026-08-26: "let's go with A" -- give every whale a capture
tolerance so the FOK can fill above the whale's price.

The rule it replaces was not an accident, it was an order (2026-08-12,
same-or-better) that did exactly what it said and cost us money anyway:
the limit was his price floored to the tick, so the book only came back
to us when the market had moved AGAINST him. Filling was conditioned on
the whale being wrong. Measured -- at_his, his OWN P&L on the subset we
filled at HIS prices, negative on all six whales (-$30,248) while
price_drag was positive on all six (+$13,051). We filled cheaper than he
did and still lost.

That rule survived two weeks because nothing graded it. So this file
exists before the first tolerance dollar is spent, and it pins the two
properties that make the grade honest:

  * the MARGINAL cohort is graded alone. A fill at or below his price
    would have happened under the old rule too; only fills ABOVE his
    price exist because of Option A. Blending them is how a change gets
    graded as harmless -- parity fills dominate the count.
  * ROI is on SETTLED dollars. Dividing realised P&L by dollars that
    include open positions understates every cohort and understates the
    smaller one most -- which here is the cohort under judgement.
"""

from __future__ import annotations

import inspect

import pytest

from sportsassets import live_executor as le
from sportsassets.api import app as app_mod


class FakePool:
    def __init__(self, rows):
        self.rows = rows
        self.args = None

    async def fetch(self, sql, *args):
        self.sql, self.args = sql, args
        return self.rows


def _const(v):
    async def _f():
        return v
    return _f


def _row(**kw):
    base = {"whale": "rn1", "cohort": "marginal", "n": 10, "settled": 10,
            "staked": 1000.0, "pnl": 50.0, "settled_staked": 1000.0,
            "cents_over": 1.4}
    base.update(kw)
    return base


class TestTheCohortsAreNeverBlended:
    def test_the_query_groups_by_cohort(self):
        src = inspect.getsource(app_mod.api_copy_tolerance)
        assert "fill_price > his_price THEN 'marginal'" in src
        assert "GROUP BY 1, 2" in src

    @pytest.mark.asyncio
    async def test_each_cohort_keeps_its_own_roi(self):
        pool = FakePool([
            _row(cohort="marginal", n=5, pnl=-40.0,
                 staked=500.0, settled_staked=500.0),
            _row(cohort="parity", n=95, pnl=200.0,
                 staked=9500.0, settled_staked=9500.0),
        ])
        app_mod.get_pool = _const(pool)
        out = await app_mod.api_copy_tolerance()
        by = {r["cohort"]: r for r in out["rows"]}
        assert by["marginal"]["roi"] == -0.08
        assert by["parity"]["roi"] == 0.0211
        # the blended number would have been +0.016 -- a losing Option A
        # reported as a winner, because parity is 95% of the rows
        assert by["marginal"]["roi"] < 0 < by["parity"]["roi"]

    def test_the_note_says_they_are_not_summed(self):
        src = inspect.getsource(app_mod.api_copy_tolerance)
        assert "Never summed together" in src


class TestRoiDenominator:
    @pytest.mark.asyncio
    async def test_roi_uses_settled_dollars_not_all_dollars(self):
        """Half the stake still open. Dividing by the full stake would
        halve the apparent ROI of the cohort being judged."""
        pool = FakePool([_row(pnl=50.0, staked=2000.0,
                              settled_staked=1000.0)])
        app_mod.get_pool = _const(pool)
        out = await app_mod.api_copy_tolerance()
        assert out["rows"][0]["roi"] == 0.05

    @pytest.mark.asyncio
    async def test_no_settled_dollars_is_no_verdict_not_zero(self):
        """A cohort with nothing settled has no ROI. Reporting 0.0
        would read as 'graded, and flat'."""
        pool = FakePool([_row(settled=0, pnl=0.0, settled_staked=0.0)])
        app_mod.get_pool = _const(pool)
        out = await app_mod.api_copy_tolerance()
        assert out["rows"][0]["roi"] is None


class TestTheWindow:
    @pytest.mark.asyncio
    async def test_it_defaults_to_the_day_option_a_shipped(self):
        pool = FakePool([])
        app_mod.get_pool = _const(pool)
        await app_mod.api_copy_tolerance()
        assert str(pool.args[0]) == "2026-08-26"

    def test_grading_across_the_policy_change_is_the_error_it_avoids(self):
        src = inspect.getsource(app_mod.api_copy_tolerance)
        assert "since_day" in src
        assert "OLD rule" in src


class TestItMeasuresWhatWePaid:
    def test_cents_over_is_reported(self):
        """The tolerance is a CEILING, not a payment -- the FOK fills at
        the book. What we actually paid is an empirical question and
        this is the column that answers it."""
        src = inspect.getsource(app_mod.api_copy_tolerance)
        assert "(fill_price - his_price) * 100" in src

    @pytest.mark.asyncio
    async def test_the_other_sleeves_are_excluded(self):
        src = inspect.getsource(app_mod.api_copy_tolerance)
        assert "'manual', 'underdog'" in src

    @pytest.mark.asyncio
    async def test_unfilled_rows_cannot_enter_the_grade(self):
        """A rejected or unfilled row has no fill price and no P&L;
        letting one in would dilute every cohort it touched."""
        src = inspect.getsource(app_mod.api_copy_tolerance)
        assert "status IN ('filled', 'settled', 'cashed_out')" in src
        assert "filled_usd > 0" in src


class TestTheGraderAgreesWithTheExecutor:
    def test_the_cohort_rule_is_the_same_on_both_sides(self):
        """The SQL says fill_price > his_price; live_executor's
        tolerance_cohort must say the same thing, or the endpoint grades
        a population the code never produced."""
        src = inspect.getsource(app_mod.api_copy_tolerance)
        assert "fill_price > his_price" in src
        assert le.tolerance_cohort(0.47, 0.48) == "marginal"
        assert le.tolerance_cohort(0.47, 0.47) == "parity"

    def test_a_zero_tolerance_whale_can_only_produce_parity(self):
        """If the tolerance is 0 the limit is his floored price, so no
        fill can land above him -- the marginal cohort must be empty for
        that whale. This is the property that makes the split a real
        experiment rather than a label."""
        lim = le.copy_limit_price("nobody", 0.474, fresh=False)
        assert lim == 0.47
        assert le.tolerance_cohort(0.474, lim) == "parity"
