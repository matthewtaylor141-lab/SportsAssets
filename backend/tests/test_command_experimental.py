"""THE EXPERIMENTAL PANEL, pinned where a screenshot could mislead.

Owner directive 2026-09-19 21:2xZ §11/§13.

THE FAILURES THESE PREVENT:

  AN UNMEASURED POSITION PRINTED AS FLAT. Under the GitHub bridge most
  short-horizon markouts miss their tolerance. A panel that counted
  those as 0 would turn a lane of unknowns into a lane of flat trades
  and the P&L would look reassuringly small rather than absent.

  TWO LATENCY REGIMES AVERAGED. A CI-runner book minutes after the
  decision and a persistent worker's milliseconds after it are
  different execution environments; one number across them describes
  neither.

  A LEADERBOARD READ TOO EARLY. "Do not rank/promote on tiny samples."

  A READ FAILURE RENDERED AS ZERO. The endpoint must refuse with a
  named reason, never return an empty page of zeros.
"""

from __future__ import annotations

import pytest

from sportsassets.api import command_experimental as CX


class FakePool:
    """Answers each of the panel's five statements by shape."""

    def __init__(self, headline=(), by_exp=(), positions=(), refusals=(),
                 coverage=(), tape=(), raise_on=None):
        self.headline, self.by_exp = list(headline), list(by_exp)
        self.positions, self.refusals = list(positions), list(refusals)
        self.coverage, self.tape = list(coverage), list(tape)
        self.raise_on = raise_on

    async def fetch(self, sql, *args):
        if self.raise_on and self.raise_on in sql:
            raise RuntimeError("relation does not exist")
        # THE LEADERBOARD IS THE ONE CARRYING control_id. Discriminated
        # on that rather than on the GROUP BY text, because the headline
        # now groups by experiment_id too (owner 2026-09-20: X1 and its
        # null control must never be summed into one row) and the two
        # statements' GROUP BY clauses overlap.
        if "d.control_id" in sql:
            return self.by_exp
        if "GROUP BY d.experiment_id, d.latency_regime" in sql:
            return self.headline
        if "both_sides" in sql:
            return []
        if "bettor_experimental_positions" in sql:
            return self.positions
        if "WHERE position_id IS NULL" in sql:
            return self.refusals
        if "bettor_experimental_markouts" in sql:
            return self.coverage
        if "ORDER BY d.decision_timestamp DESC" in sql:
            return self.tape
        raise AssertionError("unexpected statement: %.60s" % sql)


def headline(**kw):
    base = {"experiment_id": "X1_SHORT_HORIZON_DIRECTION",
            "latency_regime": "GITHUB_BRIDGE", "trades": 4, "decisions": 9,
            "entry_played": 828.0, "unfilled": 3172.0, "intended": 4000.0,
            "marked_n": 2, "unmarked_n": 2, "pnl_exec": 26.0,
            "pnl_mid": 36.0, "wins": 2, "losses": 0, "pnl_today": 26.0,
            "last_decision": None}
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_unmarked_positions_are_reported_and_never_counted_flat():
    """THE ONE THAT MATTERS UNDER THE BRIDGE."""
    out = await CX.summary(FakePool(headline=[headline()]))
    r = out["regimes"][0]
    assert r["markedPositions"] == 2
    assert r["unmarkedPositions"] == 2
    # the P&L covers the marked ones ONLY, and the payload says so
    assert r["netShadowPnlUsd"] == 26.0
    assert r["trades"] == 4
    assert "MARK_TO_OBSERVED_MARKOUT" in out["environment"]["pnlBasis"]


@pytest.mark.asyncio
async def test_a_lane_with_no_measured_mark_reports_none_not_zero():
    out = await CX.summary(FakePool(headline=[
        headline(marked_n=0, unmarked_n=4, pnl_exec=None, pnl_mid=None,
                 wins=0, losses=0, pnl_today=None)]))
    r = out["regimes"][0]
    assert r["netShadowPnlUsd"] is None      # NOT 0.0
    assert r["todayPnlUsd"] is None
    assert r["winRate"] is None
    assert r["returnOnEntryNotional"] is None
    assert r["unmarkedPositions"] == 4


@pytest.mark.asyncio
async def test_the_two_regimes_are_separate_rows_with_no_total():
    out = await CX.summary(FakePool(headline=[
        headline(),
        headline(latency_regime="PERSISTENT_INSTITUTIONAL_WORKER",
                 pnl_exec=-4.0)]))
    assert [r["latencyRegime"] for r in out["regimes"]] == [
        "GITHUB_BRIDGE", "PERSISTENT_INSTITUTIONAL_WORKER"]
    # no pooled figure anywhere on the payload
    assert "total" not in out and "netShadowPnlUsd" not in out
    assert "never pooled" in out["environment"]["regimeNote"]


@pytest.mark.asyncio
async def test_a_tiny_sample_is_printed_and_marked_unrankable():
    """§11: "Do not rank/promote on tiny samples." """
    rows = [{"experiment_id": "X1_SHORT_HORIZON_DIRECTION",
             "latency_regime": "GITHUB_BRIDGE", "control_id": None,
             "trades": 2, "entry_played": 414.0, "marked_n": 2,
             "pnl_exec": 26.0, "wins": 2, "no_trades": 7}]
    out = await CX.summary(FakePool(headline=[headline()], by_exp=rows))
    row = out["leaderboard"][0]
    assert row["markedPositions"] == 2
    assert row["rankable"] is False
    assert row["sufficientSample"] is False
    assert row["netShadowPnlUsd"] == 26.0      # printed, not hidden
    assert out["regimes"][0]["sufficientSample"] is False


@pytest.mark.asyncio
async def test_a_sample_above_the_floor_becomes_rankable():
    rows = [{"experiment_id": "X1_SHORT_HORIZON_DIRECTION",
             "latency_regime": "GITHUB_BRIDGE", "control_id": None,
             "trades": 40, "entry_played": 9000.0,
             "marked_n": CX.MIN_SAMPLE_FOR_RANKING,
             "pnl_exec": 120.0, "wins": 21, "no_trades": 60}]
    out = await CX.summary(FakePool(headline=[headline()], by_exp=rows))
    assert out["leaderboard"][0]["rankable"] is True


@pytest.mark.asyncio
async def test_the_control_is_labelled_as_one():
    rows = [{"experiment_id": "X1C_NULL_CONTROL",
             "latency_regime": "GITHUB_BRIDGE",
             "control_id": "X1_SHORT_HORIZON_DIRECTION",
             "trades": 2, "entry_played": 414.0, "marked_n": 2,
             "pnl_exec": 26.0, "wins": 2, "no_trades": 0}]
    out = await CX.summary(FakePool(headline=[headline()], by_exp=rows))
    assert out["leaderboard"][0]["isControlFor"] == \
        "X1_SHORT_HORIZON_DIRECTION"


@pytest.mark.asyncio
async def test_an_unreadable_ledger_refuses_by_name_and_is_not_zero():
    with pytest.raises(CX.RetrievalIncomplete) as exc:
        # The headline statement's GROUP BY gained experiment_id when
        # X1 and its null control were separated; the REFUSAL behaviour
        # is unchanged, only the string this fixture keys on moved.
        await CX.summary(
            FakePool(raise_on="GROUP BY d.experiment_id, d.latency_regime"))
    assert exc.value.what == "experimental headline"
    assert "relation does not exist" in exc.value.cause


@pytest.mark.asyncio
async def test_the_disclosure_is_on_the_payload_not_in_the_ui_alone():
    out = await CX.summary(FakePool())
    env = out["environment"]
    assert env["disclosureLines"] == ["EXPERIMENTAL SHADOW",
                                      "NO REAL CAPITAL",
                                      "NOT VALIDATED PERFORMANCE"]
    assert env["notDecisionGrade"] is True
    assert env["realOrderActivity"] == "NONE"
    assert env["capitalAtRisk"] == 0
    assert "NOT the decision-grade lane" in env["separateLaneNote"]


@pytest.mark.asyncio
async def test_the_tape_keeps_the_action_apart_from_the_execution():
    """"Do not convert it to NO_TRADE after seeing the signal" holds on
    the screen too."""
    rows = [{"experimental_decision_id": "xdec_1",
             "experiment_id": "X1_SHORT_HORIZON_DIRECTION",
             "market_id": "sym", "outcome_leg": "no", "action": "BUY_NO",
             "execution_status": "BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE",
             "decision_timestamp": None, "arrival_timestamp": None,
             "signal_strength": -0.04, "intended_notional_usd": 1000.0,
             "executed_notional_usd": None, "unfilled_notional_usd": None,
             "filled_qty": None, "vwap": None,
             "identity_binding_status": "AMBIGUOUS",
             "latency_regime": "GITHUB_BRIDGE",
             "observed_arrival_latency_ms": None, "l2_book_sha": None,
             "l2_evidence_id": None, "position_id": None,
             "eligible_population_id": "pop_1"}]
    out = await CX.tape(FakePool(tape=rows))
    d = out["decisions"][0]
    assert d["action"] == "BUY_NO"
    assert d["executionStatus"] == "BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE"
    # a blocked execution reports NO notional, not a zero
    assert d["executedNotionalUsd"] is None
    assert d["unfilledNotionalUsd"] is None


@pytest.mark.asyncio
async def test_markout_misses_are_on_the_panel_rather_than_hidden():
    coverage = [{"horizon": "30S", "status": "NOT_IDENTIFIED", "n": 12,
                 "median_lag_ms": 420000.0, "tolerance_ms": 30000.0},
                {"horizon": "300S", "status": "OBSERVED", "n": 4,
                 "median_lag_ms": 60000.0, "tolerance_ms": 150000.0}]
    out = await CX.summary(FakePool(headline=[headline()],
                                    coverage=coverage))
    misses = [c for c in out["markoutCoverage"]
              if c["status"] == "NOT_IDENTIFIED"]
    assert misses and misses[0]["n"] == 12
    assert misses[0]["medianLagMs"] == 420000.0
    assert misses[0]["toleranceMs"] == 30000.0
