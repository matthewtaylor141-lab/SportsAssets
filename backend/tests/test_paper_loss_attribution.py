"""THE PAPER LOSS, ATTRIBUTED BY CAUSE (closeout; history immutable).

The realized PAPER loss split on the reconciled additive identity
(intel.attribution) plus the parts it does not carry: spread paid against
the entry book's own mid, model error vs variance (binomial z), the loss on
positions Xavier never had a complete packet for, missed exits (EXIT ranked
on an incomplete packet, then held), fixture correlation, and stale entries.
Nothing is estimated; an unmeasurable part is null with its reason."""
from __future__ import annotations

import math

from sportsassets import paper_loss_attribution as PLA


def _row(g, *, q=100.0, p=0.6, v=0.55, pi=0.0, pnl=-55.0, outcome="LOST",
         strategy="S", sells=0, fees=1.0):
    return {"group_id": g, "entry_qty": q, "p_decision": p, "fill_vwap": v,
            "payoff_per_contract": pi, "settlement_outcome": outcome,
            "realized_pnl_usd": pnl, "fees_usd": fees, "model_edge_usd": 1.0,
            "slippage_usd": 0.5, "execution_edge_usd": -1.5,
            "management_usd": 0.0, "settlement_usd": 0.0,
            "outcome_variance_usd": q * (pi - p), "strategy": strategy,
            "sell_fills": sells, "reconciles": True}


def test_components_coverage_missed_exits_and_correlation():
    rows = [_row("g1"), _row("g2", pi=1.0, pnl=44.0, outcome="WON"),
            _row("g3", strategy="T"),
            dict(_row("g4"), realized_pnl_usd=None)]      # open: excluded
    out = PLA.build(
        rows, coverage={"g1": (5, 0), "g2": (4, 4), "g3": (3, 1)},
        missed={"g1": {"exit_value_usd": 40.0, "review_id": "r1"}},
        spreads={"g1": 1.0, "g2": 0.5},
        fixtures={"g1": "fx-a", "g2": "fx-a", "g3": "fx-b"},
        stale_entries=0)
    assert out["closed_positions"] == 3 and out["open_positions_excluded"] == 1
    assert out["components"]["realized_pnl_usd"]["usd"] == -66.0
    assert out["components"]["spread_paid_usd"] == {
        "usd": 1.5, "measured_positions": 2, "why_unmeasured": None}
    assert out["loss_on_positions_xavier_could_not_manage_usd"] == -55.0
    assert out["management_coverage"]["ALWAYS_COMPLETE"]["positions"] == 1
    mx = out["missed_exits"]
    assert mx["positions"] == 1 and mx["exit_minus_hold_usd"] == 40.0
    c = out["correlation"]
    assert c["multi_position_fixtures"] == 1
    assert c["multi_position_pnl_usd"] == -11.0
    assert out["by_strategy"]["T"]["positions"] == 1
    assert out["adverse_selection"]["usd"] is None


def test_model_error_vs_variance_is_a_binomial_z():
    rows = [_row("g%d" % i, q=10.0, p=0.5, pi=0.0) for i in range(50)]
    mv = PLA.model_vs_variance(rows)
    # every one lost at p = 0.5: -250 against se sqrt(50 * 100 * .25)
    assert mv["outcome_minus_model_usd"] == -250.0
    assert math.isclose(mv["binomial_se_usd"], round(math.sqrt(1250), 2))
    assert mv["verdict"] == "MODEL_PROBABILITIES_MISCALIBRATED"
    half = [_row("w%d" % i, q=10.0, p=0.5, pi=float(i % 2))
            for i in range(50)]
    assert PLA.model_vs_variance(half)["verdict"] == \
        "CONSISTENT_WITH_VARIANCE"


def test_the_mid_is_in_cost_space_for_either_side():
    from tests import paper_harness as H
    m = H.md(bids=[(0.40, 100)], offers=[(0.44, 100)])
    long_mid = PLA.mid_cost(m["bids"], m["offers"], "LONG")
    short_mid = PLA.mid_cost(m["bids"], m["offers"], "SHORT")
    assert long_mid is not None and short_mid is not None
    assert math.isclose(long_mid, 0.42, abs_tol=1e-9)
    assert math.isclose(short_mid, 0.58, abs_tol=1e-9)
    assert PLA.mid_cost(m["bids"], [], "LONG") is None


def test_the_read_runs_on_the_real_schema():
    import asyncio
    import os
    import time

    import asyncpg
    import pytest
    dsn = os.environ.get("RN1X_TEST_DSN")
    if not dsn:
        pytest.skip("needs RN1X_TEST_DSN")

    async def go():
        c = await asyncpg.connect(dsn)
        try:
            return await PLA.read(c, now=time.time())
        finally:
            await c.close()
    out = asyncio.run(go())
    assert out["version"] == PLA.VERSION
    assert "components" in out and "management_coverage" in out
