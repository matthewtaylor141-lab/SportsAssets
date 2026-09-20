"""A 30-SECOND MARKOUT ON A 61-SECOND GRID IS NOT A MEASUREMENT.

Owner directive 2026-09-20, from an independent production query of
direct institutional L2 arrival spacing:

    P50 ~= 60.95s   P95 ~= 63.98s   MAX ~= 64.70s

    "30S_MARKOUT_STATUS =
     UNOBSERVABLE_AT_CURRENT_DIRECT_L2_CAPTURE_FREQUENCY.
     Do not tune a tolerance around the results. Preserve all existing
     30S rows append-only, but classify the current 30S measurements
     as not eligible for experimental performance conclusions... Do
     not delete or rewrite them. Do not convert them to zero."

THREE THINGS THIS FILE PINS, IN THE ORDER THEY MATTER:

  1. THE VERDICT IS STRUCTURAL, NOT FITTED. The rule is
     `tolerance < horizon`, a property of the horizon's own admissible
     window. Nothing in it was chosen by looking at what the 30S
     numbers came out as, and the tests below prove that by moving the
     inputs and watching the verdict follow -- a fitted threshold
     would not survive a faster grid.

  2. NO ROW IS TOUCHED. The classification happens when the rows are
     READ. There is no UPDATE, no DELETE, no zero-fill anywhere on
     this path, and the tests scan the source for them.

  3. AN INELIGIBLE HORIZON IS NOT A FLAT ONE. A position marked only
     at 30S must read as UNMARKED, with no P&L at all. Letting it read
     as a zero-P&L trade would put a fabricated flat result into the
     win/loss count of an experiment that has never had a measurable
     outcome.
"""

from __future__ import annotations

import pytest

from sportsassets import shadow_experimental_markouts as mk
from sportsassets import shadow_markout_observability as ob
from sportsassets.api import command_experimental as ce


# ── 1. the verdicts themselves, and the reason each carries ──────────


def test_thirty_seconds_is_unobservable_on_this_capture_grid():
    """The directive's own finding, in the directive's own words."""
    v = ob.by_horizon()["30S"]
    assert v["status"] == ob.UNOBSERVABLE
    assert v["status"] == "UNOBSERVABLE_AT_CURRENT_DIRECT_L2_CAPTURE_FREQUENCY"
    assert v["eligibleForPerformance"] is False


def test_the_reason_thirty_is_unobservable_is_the_zero_elapsed_window():
    """Not "the numbers looked bad" -- the admissible window opens at
    the decision instant, so a 30S markout may legitimately be the
    entry book wearing a later label."""
    v = ob.by_horizon()["30S"]
    assert v["earliestElapsedS"] == 0.0
    assert v["toleranceS"] >= v["horizonS"]
    assert "decision instant" in v["why"]


def test_sixty_and_three_hundred_stay_observable():
    """The correction is narrow. It removes one horizon, not scoring."""
    by = ob.by_horizon()
    assert by["60S"]["status"] == ob.OBSERVABLE
    assert by["300S"]["status"] == ob.OBSERVABLE
    assert ob.observable_horizons() == ("60S", "300S")
    assert ob.unobservable_horizons() == ("30S",)


def test_sixty_is_reported_as_marginal_rather_than_quietly_passed():
    """60S is resolvable but its 60s window is narrower than the
    63.98s P95 grid, so a 60S markout can honestly find no book. A
    reader comparing 60S with 300S is told which one is marginal."""
    by = ob.by_horizon()
    assert by["60S"]["captureGuaranteed"] is False
    assert "NOT guaranteed" in by["60S"]["why"]
    assert by["300S"]["captureGuaranteed"] is True
    assert by["300S"]["why"] is None


def test_the_measured_cadence_travels_with_the_verdict():
    """The numbers are data with provenance, not a tunable. Anyone
    reading the verdict can see what it was derived from."""
    cap = ob.by_horizon()["30S"]["capture"]
    assert cap["p50S"] == pytest.approx(60.95)
    assert cap["p95S"] == pytest.approx(63.98)
    assert cap["maxS"] == pytest.approx(64.70)
    assert cap["regime"] == "DIRECT_INSTITUTIONAL_WORKER"
    assert cap["measuredAt"] == "2026-09-20"


# ── 2. structural, not fitted ────────────────────────────────────────


def test_the_rule_is_the_tolerance_against_the_horizon_and_nothing_else():
    """THE ANTI-FITTING PIN. Same 30s horizon, a tolerance below it:
    the verdict flips to OBSERVABLE with no edit to this module. A
    threshold chosen to make the 30S numbers fail could not do that."""
    assert ob.observability(30, 10)["status"] == ob.OBSERVABLE
    assert ob.observability(30, 29.9)["status"] == ob.OBSERVABLE
    assert ob.observability(30, 30)["status"] == ob.UNOBSERVABLE
    assert ob.observability(30, 31)["status"] == ob.UNOBSERVABLE


def test_a_faster_feed_makes_thirty_seconds_observable_without_an_edit():
    """"If books ever arrive every two seconds the floor can come
    down." Proven rather than asserted: with a 2s tolerance against a
    2s grid, 30S is both resolvable and guaranteed."""
    v = ob.observability(30, 2, capture_p50_s=2.0, capture_p95_s=2.5)
    assert v["status"] == ob.OBSERVABLE
    assert v["captureGuaranteed"] is True
    assert v["eligibleForPerformance"] is True


def code_only(obj) -> str:
    """The CODE, with every comment, docstring and string literal gone.

    A module that explains in prose why it never reads a price contains
    the word "price" in that explanation. The claim being tested is
    about what the code READS, not about what the comments SAY.
    """
    import inspect
    import io
    import textwrap
    import tokenize

    kept = []
    src = textwrap.dedent(inspect.getsource(obj))
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER):
            kept.append(tok.string)
    return " ".join(kept).lower()


def test_the_verdict_does_not_read_a_price_a_signal_or_a_pnl():
    """A data-quality rule that consulted the results would be a
    performance filter wearing a quality label."""
    src = code_only(ob.observability)
    for forbidden in ("pnl", "price", "signal", "vwap", "markout_usd",
                      "wins", "losses"):
        assert forbidden not in src, forbidden


def test_the_lanes_frozen_tolerance_is_what_produces_the_verdict():
    """The verdict is computed from the lane's real tolerance
    function, not from a number re-typed here. Change the tolerance
    and the observability moves with it -- which is also why the
    tolerance must not be tuned."""
    assert ob.by_horizon()["30S"]["toleranceS"] == mk.tolerance_s(30)
    assert ob.by_horizon()["300S"]["toleranceS"] == mk.tolerance_s(300)


# ── 3. no row is rewritten ───────────────────────────────────────────


def test_nothing_on_the_observability_path_writes_to_the_ledger():
    """"Do not delete or rewrite them. Do not convert them to zero."
    The classification is read-time, so the module contains no write
    of any kind."""
    import inspect

    src = inspect.getsource(ob).lower()
    for stmt in ("update ", "delete ", "insert ", "truncate", "alter "):
        assert stmt not in src, (
            "%r appears in the observability module; existing 30S rows "
            "are preserved append-only and only re-classified when "
            "read" % stmt)


def test_the_thirty_second_rows_are_still_counted_in_coverage():
    """Preserved, not hidden. The coverage tile still shows how many
    30S markouts exist -- it just says they are not eligible."""
    assert "'30S'" not in ce._MARKED          # excluded from P&L...
    assert "GROUP BY horizon" in ce._MARKOUT_COVERAGE   # ...counted here
    assert "WHERE" not in ce._MARKOUT_COVERAGE.upper(), (
        "the coverage tile filters nothing: every 30S row ever written "
        "is still visible, carrying its observability verdict")


# ── 4. an ineligible horizon is not a flat trade ─────────────────────


def test_performance_statements_admit_only_the_observable_horizons():
    assert "m.horizon IN ('%s')" % ce._PERFORMANCE_HORIZONS in ce._MARKED
    assert "'60S'" in ce._MARKED and "'300S'" in ce._MARKED
    assert "'30S'" not in ce._MARKED, (
        "a 30S markout must not be able to become a P&L figure")


def test_the_performance_gate_is_derived_from_the_module_not_typed_in():
    """If 30S ever becomes observable the statement follows, and if a
    fourth horizon is added it is admitted or excluded on its own
    merits rather than by whoever edits the SQL."""
    for name in ob.observable_horizons():
        assert name in ce._PERFORMANCE_HORIZONS
    for name in ob.unobservable_horizons():
        assert name not in ce._PERFORMANCE_HORIZONS


class MarkedOnlyAtThirty:
    """A position whose ONLY markout is the ineligible one."""

    async def fetch(self, sql, *args):
        if "bettor_experimental_positions" in sql and "GROUP BY" in sql:
            return [{"experiment_id": "X1C_NULL_CONTROL", "status": "OPEN",
                     "n": 1, "notional": 805.0, "qty": 2500.0}]
        if "d.control_id" in sql:
            return []
        if "d.latency_regime" in sql and "trades" in sql:
            # What the gated statement yields: the trade exists, it has
            # no eligible markout, so it is UNMARKED and has no P&L.
            return [{"experiment_id": "X1C_NULL_CONTROL",
                     "latency_regime": "DIRECT_INSTITUTIONAL_WORKER",
                     "trades": 1, "decisions": 1, "entry_played": 805.0,
                     "unfilled": 195.0, "intended": 1000.0,
                     "marked_n": 0, "unmarked_n": 1,
                     "pnl_exec": None, "pnl_mid": None,
                     "wins": 0, "losses": 0, "pnl_today": None,
                     "last_decision": None}]
        return []

    async def fetchrow(self, sql, *args):
        return None


@pytest.mark.asyncio
async def test_a_position_marked_only_at_thirty_reads_unmarked_not_flat():
    """THE DEFECT THIS PREVENTS. Counting an ineligible markout as a
    result would put a manufactured flat trade into the win/loss
    record of an experiment that has never had a measurable outcome."""
    out = await ce.summary(MarkedOnlyAtThirty())
    row = out["regimes"][0]
    assert row["markedPositions"] == 0
    assert row["unmarkedPositions"] == 1
    assert row["netShadowPnlUsd"] is None
    # NO WIN RATE EITHER. A flat markout counted as a result would
    # read as a 0%-win, 1-sample record for an experiment that has
    # never had a measurable outcome.
    assert row["winRate"] is None
    assert row["winRateSample"] == 0
    # And the trade itself is NOT erased -- it happened.
    assert row["trades"] == 1
    assert row["entryNotionalPlayedUsd"] == 805.0


@pytest.mark.asyncio
async def test_the_panel_states_which_horizons_a_number_may_come_from():
    out = await ce.summary(MarkedOnlyAtThirty())
    assert out["performanceHorizons"] == ["60S", "300S"]
    assert out["markoutObservability"]["30S"]["status"] == ob.UNOBSERVABLE
    assert out["markoutObservability"]["30S"][
        "eligibleForPerformance"] is False
    assert out["markoutObservability"]["60S"][
        "eligibleForPerformance"] is True
    assert out["captureCadence"]["p50S"] == pytest.approx(60.95)


def test_the_excess_thirty_second_markout_is_unknown_not_a_number():
    """X1 minus the control at 30S would be a difference of two
    quantities neither of which is measurable. It is refused, with the
    reason attached, rather than printed."""
    r = {"common_n": 4, "x1_pnl": 30.0, "ctl_pnl": 10.0,
         "x1_entry": 1000.0, "ctl_entry": 1000.0,
         "x1_h30": 5.0, "ctl_h30": 2.0,
         "x1_h60": 7.0, "ctl_h60": 1.0,
         "x1_h300": 9.0, "ctl_h300": -1.0}
    out = ce.vs_control(r)
    assert out["EXCESS_30S_MARKOUT"] is None
    assert out["EXCESS_30S_MARKOUT_STATUS"] == ob.UNOBSERVABLE
    # The observable horizons are unaffected by the correction.
    assert out["EXCESS_60S_MARKOUT"] == 6.0
    assert out["EXCESS_300S_MARKOUT"] == 10.0
