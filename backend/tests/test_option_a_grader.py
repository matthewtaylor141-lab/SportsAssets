"""Option A ships with the instrument that judges it.

Owner order 2026-08-26: "let's go with A" -- give every whale a capture
tolerance so the FOK can fill above the whale's price.

The rule it replaces was an order (2026-08-12, same-or-better) that did
exactly what it said and cost us money anyway: the limit was his price
floored to the tick, so the book only came back to us when the market
had moved AGAINST him. Filling was conditioned on the whale being
wrong. Measured -- at_his, his OWN P&L on the subset we filled at HIS
prices, negative on all six whales (-$30,248) while price_drag was
positive on all six (+$13,051).

That rule survived two weeks because nothing graded it. This file pins
the grader's honesty properties:

  * the MARGINAL cohort is graded alone -- parity fills would have
    happened under the old rule too, and blending lets them drown the
    signal;
  * ROI is on SETTLED dollars -- dividing by dollars that include open
    positions understates the smaller cohort most, which is the one
    under judgement;
  * ONE COST DEFINITION. v1 split cohorts in SQL on raw `fill_price >
    his_price`. fill_price on a SHORT names the LONG leg while
    his_price is the whale's own side, so every short was cohorted by
    comparing two different legs -- the defect that inverted
    realized_pnl and fill_cash before both took an intent. The grader
    now cohorts through tolerance_cohort/cost_per_share, the same
    functions the executor uses, so the two cannot drift apart.
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


def _fill(**kw):
    """One live_orders row as the grader's query returns it."""
    base = {"whale": "rn1", "his": 0.47, "fp": 0.48, "staked": 100.0,
            "pnl": 5.0, "status": "settled",
            "intent": "ORDER_INTENT_BUY_LONG"}
    base.update(kw)
    return base


class TestTheCohortsAreNeverBlended:
    @pytest.mark.asyncio
    async def test_each_cohort_keeps_its_own_roi(self):
        pool = FakePool(
            # 1 marginal loser + 19 parity winners. Blended they read
            # +1.6%; split, the marginal cohort shows its own -8%.
            [_fill(fp=0.48, staked=500.0, pnl=-40.0)]
            + [_fill(fp=0.47, staked=500.0, pnl=10.0)
               for _ in range(19)])
        app_mod.get_pool = _const(pool)
        out = await app_mod.api_copy_tolerance()
        by = {r["cohort"]: r for r in out["rows"]}
        assert by["marginal"]["roi"] == -0.08
        assert by["parity"]["roi"] == pytest.approx(0.02, abs=1e-4)
        assert by["marginal"]["n"] == 1 and by["parity"]["n"] == 19

    def test_the_note_says_they_are_not_summed(self):
        src = inspect.getsource(app_mod.api_copy_tolerance)
        assert "Never summed together" in src


class TestShortsAreCohortedOnOurCost:
    """The bug the first version shipped with, pinned so it cannot
    return. A SHORT fill's fill_price names the LONG leg."""

    @pytest.mark.asyncio
    async def test_a_short_filled_at_parity_is_not_marginal(self):
        # Whale paid 0.30 for his side. We shorted; venue records the
        # LONG leg at 0.70, so OUR cost is 1 - 0.70 = 0.30 = parity.
        # Raw comparison read 0.70 > 0.30 and called it marginal.
        pool = FakePool([_fill(his=0.30, fp=0.70,
                               intent="ORDER_INTENT_BUY_SHORT")])
        app_mod.get_pool = _const(pool)
        out = await app_mod.api_copy_tolerance()
        assert [r["cohort"] for r in out["rows"]] == ["parity"]

    @pytest.mark.asyncio
    async def test_a_short_that_paid_over_is_marginal(self):
        # LONG leg at 0.68 -> our cost 0.32 vs his 0.30: 2c over.
        pool = FakePool([_fill(his=0.30, fp=0.68,
                               intent="ORDER_INTENT_BUY_SHORT")])
        app_mod.get_pool = _const(pool)
        out = await app_mod.api_copy_tolerance()
        row = out["rows"][0]
        assert row["cohort"] == "marginal"
        assert row["cents_over"] == pytest.approx(2.0, abs=0.01)

    def test_the_grader_uses_the_production_functions(self):
        src = inspect.getsource(app_mod.api_copy_tolerance)
        assert "tolerance_cohort(" in src
        assert "cost_per_share(" in src
        assert "CASE WHEN fill_price > his_price" not in src, (
            "the raw-leg SQL cohort split is back")


class TestRoiDenominator:
    @pytest.mark.asyncio
    async def test_roi_uses_settled_dollars_not_all_dollars(self):
        """Half the stake still open. Dividing by the full stake would
        halve the apparent ROI of the cohort being judged."""
        pool = FakePool([
            _fill(staked=1000.0, pnl=50.0, status="settled"),
            _fill(staked=1000.0, pnl=None, status="filled"),
        ])
        app_mod.get_pool = _const(pool)
        out = await app_mod.api_copy_tolerance()
        row = out["rows"][0]
        assert row["roi"] == 0.05
        assert row["staked"] == 2000.0
        assert row["settled_staked"] == 1000.0

    @pytest.mark.asyncio
    async def test_no_settled_dollars_is_no_verdict_not_zero(self):
        """A cohort with nothing settled has no ROI. Reporting 0.0
        would read as 'graded, and flat'."""
        pool = FakePool([_fill(status="filled", pnl=None)])
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
    @pytest.mark.asyncio
    async def test_cents_over_is_in_our_cost_not_the_raw_leg(self):
        """The tolerance is a CEILING -- the FOK fills at the book.
        cents_over is what we actually paid over him, in ONE
        denomination, or the number lies on every short."""
        pool = FakePool([_fill(his=0.47, fp=0.485)])
        app_mod.get_pool = _const(pool)
        out = await app_mod.api_copy_tolerance()
        assert out["rows"][0]["cents_over"] == pytest.approx(1.5, abs=0.01)

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
    def test_the_cohort_rule_is_the_production_function(self):
        """The endpoint imports and calls tolerance_cohort rather than
        restating its rule, so the executor and the grader cannot
        drift: a future fix to the one function reaches both."""
        src = inspect.getsource(app_mod.api_copy_tolerance)
        assert "tolerance_cohort(r[\"his\"], r[\"fp\"], r[\"intent\"])" \
            in src

    def test_long_boundary_semantics(self):
        assert le.tolerance_cohort(0.47, 0.48) == "marginal"
        assert le.tolerance_cohort(0.47, 0.47) == "parity"
        assert le.tolerance_cohort(0.47, 0.46) == "parity"

    def test_short_boundary_semantics(self):
        s = "ORDER_INTENT_BUY_SHORT"
        assert le.tolerance_cohort(0.30, 0.70, s) == "parity"
        assert le.tolerance_cohort(0.30, 0.68, s) == "marginal"
        assert le.tolerance_cohort(0.30, 0.72, s) == "parity"

    def test_missing_prices_are_not_guessed(self):
        assert le.tolerance_cohort(None, 0.48) == "unknown"
        assert le.tolerance_cohort(0.47, None) == "unknown"
        assert le.tolerance_cohort(0.47, "n/a") == "unknown"

    def test_a_zero_tolerance_whale_can_only_produce_parity(self):
        """If the tolerance is 0 the limit is his floored price, so no
        fill can land above him -- the marginal cohort must be empty
        for that whale. This is what makes the split a real experiment
        rather than a label."""
        lim = le.copy_limit_price("nobody", 0.474, fresh=False)
        assert lim == 0.47
        assert le.tolerance_cohort(0.474, lim) == "parity"
