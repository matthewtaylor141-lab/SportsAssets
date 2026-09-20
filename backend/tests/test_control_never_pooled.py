"""X1 AND ITS NULL CONTROL ARE NEVER ONE NUMBER.

Owner directive 2026-09-20, on the first genuine production shadow
execution:

    "DO NOT CALL IT THE FIRST BETTOR X1 TRADE. X1C is the frozen NULL
    CONTROL whose rule is always LONG. The distinction is
    load-bearing."

THE DEFECT THIS PINS, which the control's first position made live.
`_HEADLINE` grouped by `latency_regime` ALONE, so trades, entry
notional, P&L, return and win rate were summed across
X1_SHORT_HORIZON_DIRECTION and X1C_NULL_CONTROL. The moment X1C opened
xpos_b0fdb1ef... for $805, that combined figure WAS the panel's
headline -- a number a reader would take as BETTOR EV performance when
every dollar of it belonged to an always-long counterfactual.

The same pooling existed in the positions tile and in the funnel's fill
buckets. All three are split here, and the tests below fail if any of
them is ever re-pooled.
"""

from __future__ import annotations

import pytest

from sportsassets import shadow_experimental_store as xstore
from sportsassets.api import command_experimental as ce

X1 = "X1_SHORT_HORIZON_DIRECTION"
X1C = "X1C_NULL_CONTROL"


# ── the statements themselves must carry the experiment ──────────────


def test_the_headline_groups_by_experiment_not_only_by_regime():
    """THE REGRESSION GUARD. Grouped by regime alone this statement
    sums the model and its control into one headline."""
    assert "d.experiment_id" in ce._HEADLINE
    group_by = ce._HEADLINE.split("GROUP BY")[1]
    assert "d.experiment_id" in group_by, (
        "experiment_id must be in the GROUP BY, or X1 and X1C are "
        "summed into one set of trades, notional and P&L")


def test_the_positions_tile_groups_by_experiment_too():
    assert "experiment_id" in ce._POSITIONS.split("GROUP BY")[1], (
        "a capital-deployed figure that adds the control's notional to "
        "the model's describes a portfolio nobody runs")


def test_every_performance_statement_names_the_experiment():
    """Any statement that sums money must say whose money it is."""
    for name in ("_HEADLINE", "_BY_EXPERIMENT", "_POSITIONS"):
        sql = getattr(ce, name)
        if "sum(" not in sql.lower():
            continue
        assert "experiment_id" in sql, name


# ── the rendered panel keeps them apart and says which is which ──────


class OnePool:
    """Returns one fixture row set for whichever statement is asked."""

    def __init__(self, headline=(), positions=(), compare=None):
        self.headline = headline
        self.positions = positions
        self.compare = compare

    async def fetch(self, sql, *args):
        if "bettor_experimental_positions" in sql and "GROUP BY" in sql:
            return list(self.positions)
        # The leaderboard statement is the one carrying control_id; the
        # headline is the other. Discriminated on that rather than on a
        # substring both of them contain.
        if "d.control_id" in sql:
            return []
        if "d.latency_regime" in sql and "trades" in sql:
            return list(self.headline)
        return []

    async def fetchrow(self, sql, *args):
        if "both_sides" in sql:
            return self.compare
        return None


def headline_row(experiment, *, trades=1, entry=805.0, pnl=12.0):
    return {"experiment_id": experiment, "latency_regime":
            "DIRECT_INSTITUTIONAL_WORKER", "trades": trades,
            "decisions": trades, "entry_played": entry, "unfilled": 195.0,
            "intended": 1000.0, "marked_n": 1, "unmarked_n": 0,
            "pnl_exec": pnl, "pnl_mid": pnl, "wins": 1, "losses": 0,
            "pnl_today": pnl, "last_decision": None}


@pytest.mark.asyncio
async def test_the_control_gets_its_own_tile_and_is_labelled_as_one():
    """A reader seeing "TRADES 1 / PLAYED $805" must be able to tell
    whether that is the model or the counterfactual."""
    pool = OnePool(headline=[headline_row(X1C, entry=805.0),
                             headline_row(X1, trades=0, entry=0.0, pnl=None)])
    out = await ce.summary(pool)

    rows = {r["experimentId"]: r for r in out["regimes"]}
    assert set(rows) == {X1, X1C}
    assert rows[X1C]["isNullControl"] is True
    assert rows[X1C]["portfolio"] == "X1C NULL CONTROL"
    assert rows[X1C]["isBettorEvPerformance"] is False
    assert rows[X1]["isNullControl"] is False
    assert rows[X1]["portfolio"] == "X1 MODEL"
    assert rows[X1]["isBettorEvPerformance"] is True


@pytest.mark.asyncio
async def test_the_panel_never_emits_a_combined_figure():
    """The production shape: the control has traded, the model has not.
    No figure anywhere on the panel may be 1 trade or $805 attributed
    to the lane as a whole."""
    pool = OnePool(headline=[headline_row(X1C, trades=1, entry=805.0),
                             headline_row(X1, trades=0, entry=0.0, pnl=None)])
    out = await ce.summary(pool)

    x1 = [r for r in out["regimes"] if r["experimentId"] == X1][0]
    assert x1["trades"] == 0
    assert x1["entryNotionalPlayedUsd"] == 0.0
    # And the panel states the rule in words, for whoever reads the
    # JSON without reading this test.
    assert "never summed" in out["neverCombined"]
    assert "not BETTOR EV performance" in out["neverCombined"]


@pytest.mark.asyncio
async def test_positions_are_reported_per_portfolio():
    pool = OnePool(positions=[
        {"experiment_id": X1C, "status": "OPEN", "n": 1,
         "notional": 805.0, "qty": 2500.0}])
    out = await ce.summary(pool)
    assert out["positions"][0]["experimentId"] == X1C
    assert out["positions"][0]["isNullControl"] is True


# ── the excess is a difference on common support ─────────────────────


def test_the_excess_is_a_difference_and_never_a_sum():
    r = {"common_n": 4, "x1_pnl": 30.0, "ctl_pnl": 10.0,
         "x1_entry": 1000.0, "ctl_entry": 1000.0,
         "x1_h30": 5.0, "ctl_h30": 2.0,
         "x1_h60": 7.0, "ctl_h60": 1.0,
         "x1_h300": 9.0, "ctl_h300": -1.0}
    out = ce.vs_control(r)
    assert out["COMMON_SUPPORT_N"] == 4
    assert out["X1_EXCESS_PNL"] == 20.0           # 30 - 10, never 40
    assert out["X1_EXCESS_RETURN"] == pytest.approx(0.02)
    # 30S IS NOT A DIFFERENCE ANY MORE. Owner's measurement of the
    # direct L2 cadence (P50 ~61s) made that horizon unobservable, so
    # an excess built from it would be arithmetic on two numbers
    # neither of which describes 30 seconds. Pinned in
    # test_markout_observability.py; here only so this file is not
    # asserting the superseded expectation.
    assert out["EXCESS_30S_MARKOUT"] is None
    assert out["EXCESS_60S_MARKOUT"] == 6.0
    assert out["EXCESS_300S_MARKOUT"] == 10.0
    # The inputs travel with the difference so it can be checked.
    assert out["x1"]["pnlUsd"] == 30.0
    assert out["control"]["pnlUsd"] == 10.0


def test_an_unmeasured_side_makes_the_excess_unknown_not_zero():
    """A missing markout is not a markout of nothing. Subtracting an
    absence would manufacture an edge."""
    r = {"common_n": 2, "x1_pnl": 10.0, "ctl_pnl": None,
         "x1_entry": 500.0, "ctl_entry": 500.0,
         "x1_h30": 4.0, "ctl_h30": None,
         "x1_h60": None, "ctl_h60": 2.0,
         "x1_h300": None, "ctl_h300": None}
    out = ce.vs_control(r)
    assert out["X1_EXCESS_PNL"] is None
    assert out["EXCESS_30S_MARKOUT"] is None
    assert out["EXCESS_60S_MARKOUT"] is None
    assert out["EXCESS_300S_MARKOUT"] is None


def test_common_support_is_empty_until_both_sides_have_acted():
    out = ce.vs_control(None)
    assert out["COMMON_SUPPORT_N"] == 0
    assert out["X1_EXCESS_PNL"] is None


def test_the_comparison_pairs_on_the_same_opportunity():
    """An excess over two non-overlapping sets is not an excess. The
    pairing key is the shared observation id and the statement demands
    BOTH sides present."""
    assert "experimental_observation_id" in ce._VS_CONTROL
    assert "both_sides" in ce._VS_CONTROL
    having = ce._VS_CONTROL.split("HAVING")[1]
    assert "$1" in having and "$2" in having, (
        "common support must require a row from EACH experiment")


def test_the_excess_markouts_compare_like_horizon_with_like():
    """X1's 30S against the control's 30S -- never X1's 300S against
    the control's 30S because one of them happened to land last."""
    for h in ("'30S'", "'60S'", "'300S'"):
        assert h in ce._MARKED_WITH_HORIZONS, h


# ── the funnel splits its fill buckets too ───────────────────────────


class FunnelPool:
    def __init__(self, rows):
        self.rows = rows

    async def fetchrow(self, sql, *args):
        if "bettor_identity_bindings" in sql:
            return {}
        return {"opportunities": 0, "decisions": len(self.rows),
                "x1_eligible": 0, "no_trade_spread": 0, "no_trade_signal": 0,
                "buy_yes": 0, "buy_no": 0, "buy_blocked_identity": 0,
                "buy_blocked_stale_book": 0, "buy_executed": 0,
                "partial_fills": 0, "full_fills": 0, "buy_unaccounted": 0,
                "action_unaccounted": 0}

    async def fetch(self, sql, *args):
        return list(self.rows)


@pytest.mark.asyncio
async def test_the_funnel_reports_fills_under_the_experiment_that_made_them():
    """Counted across the lane, BUY_EXECUTED would have read 1 the
    moment the control traded, and a reader would take that as the
    model trading."""
    rows = [
        {"experiment_id": X1C, "decisions": 1, "no_trade_spread": 0,
         "no_trade_signal": 0, "buy_yes": 1, "buy_no": 0,
         "buy_blocked_identity": 0, "buy_blocked_stale_book": 0,
         "buy_executed": 1, "partial_fills": 1, "full_fills": 0,
         "entry_notional": 805.0},
        {"experiment_id": X1, "decisions": 1, "no_trade_spread": 1,
         "no_trade_signal": 0, "buy_yes": 0, "buy_no": 0,
         "buy_blocked_identity": 0, "buy_blocked_stale_book": 0,
         "buy_executed": 0, "partial_fills": 0, "full_fills": 0,
         "entry_notional": 0.0},
    ]
    out = await xstore.funnel(FunnelPool(rows))
    by = out["byExperiment"]
    assert by[X1C]["BUY_EXECUTED"] == 1
    assert by[X1C]["IS_NULL_CONTROL"] is True
    assert by[X1C]["ENTRY_NOTIONAL_USD"] == 805.0
    assert by[X1]["BUY_EXECUTED"] == 0
    assert by[X1]["NO_TRADE_SPREAD"] == 1
    assert by[X1]["ENTRY_NOTIONAL_USD"] == 0.0
    assert "never summed" in out["neverCombined"]


@pytest.mark.asyncio
async def test_the_funnel_by_experiment_statement_groups_by_experiment():
    assert "GROUP BY experiment_id" in xstore.FUNNEL_BY_EXPERIMENT_SQL


# ── the control is still scored, prospectively ───────────────────────


def test_the_control_is_scored_not_excluded():
    """"Continue scoring the current X1C position prospectively." The
    control is separated from the model, NOT dropped: it must keep
    receiving its own 30/60/300s markouts."""
    from sportsassets import shadow_experimental_markouts as mk

    src = mk.MARKOUT_SUBJECTS_SQL if hasattr(mk, "MARKOUT_SUBJECTS_SQL") \
        else xstore.MARKOUT_SUBJECTS_SQL
    lowered = src.lower()
    assert "x1c" not in lowered, (
        "the markout sweep must not exclude the control -- it is a "
        "research portfolio that is scored, not ignored")
    assert "experiment_id <>" not in lowered
    assert "experiment_id !=" not in lowered
