"""THE OPPORTUNITY QUESTION HAS TO BE ANSWERABLE FROM THE REPORT.

WHAT WAS MISSING, AND IT MADE ME INFER THINGS. Three numbers the census
already had were summed over BOTH supported sports, so none of them could
answer the question that was actually asked -- "does the other supported
sport have a tradable payoff, and if not, where does it stop?":

  * `markets_considered` was one count over both venue labels.
  * `refusals` was one dictionary over all three provider sports, so a
    sport contributing NOTHING looked the same as a sport contributing
    candidates that then refused. Those are different findings with
    different remedies.
  * "how many candidates showed POSITIVE net edge" was never printed. It
    had to be derived from two negative counts and a total, and a derived
    zero is not a measurement.

So the funnel is counted per provider sport, the universe per venue label,
the settlement verdict per sport, and the positive-edge count is its own
number -- including when it is zero. None of it is read by any gate.

AND THE EDGE'S EXECUTION IS NAMED. Every edge in this census assumes
CROSSING the ask at the TAKER fee. A passive/maker strategy has different
fill assumptions and pays a different fee side; none of its numbers belong
here, and the census says so rather than leaving a reader to assume.
"""

from __future__ import annotations

import inspect

from sportsassets import bettor_external_shadow as EXT
from sportsassets.workers import ext_pinnacle_loop as LOOP


def _row(*, family, edge, refusals=("4_SETTLEMENT_SCOPE",),
         admissible=False, levels=0, ok=False):
    return {"refusals": list(refusals), "admissible": admissible,
            "sport_family": family, "estimate_ok": ok,
            "levels_taken": levels, "vwap": None,
            "executable_price": 0.6, "probability": 0.5, "edge": edge}


def test_the_positive_edge_count_is_its_own_number():
    rep = EXT.stage_report([_row(family="baseball", edge=-0.01),
                            _row(family="baseball", edge=0.02),
                            _row(family="soccer", edge=None)])
    assert rep["positive_edge"] == 1
    # A row with NO edge is not a negative edge. It is an absent
    # measurement, and it gets its own counter.
    assert rep["edge_not_computed"] == 1
    assert rep["negative_edge_without_a_walk"] == 1
    assert rep["negative_edge_with_a_walk"] == 0


def test_a_sport_that_priced_nothing_is_distinguishable_from_one_that_lost():
    rep = EXT.stage_report([_row(family="baseball", edge=-0.01),
                            _row(family="soccer", edge=None)])
    by = rep["by_sport"]
    assert set(by) == {"baseball", "soccer"}
    # BASEBALL WAS PRICED AND LOST.
    assert by["baseball"]["priced"] == 1
    assert by["baseball"]["negative_or_zero_edge"] == 1
    assert by["baseball"]["positive_edge"] == 0
    # SOCCER WAS NEVER PRICED AT ALL. Zero positive edges here is the
    # absence of a measurement, not a measured loss.
    assert by["soccer"]["priced"] == 0
    assert by["soccer"]["negative_or_zero_edge"] == 0
    assert by["soccer"]["candidates"] == 1


def test_the_census_names_the_execution_it_assumes():
    rep = EXT.stage_report([])
    assert rep["execution_mode"] == "CROSSING_THE_ASK_AT_THE_TAKER_FEE"
    assert "PASSIVE" in rep["excludes"] and "MAKER" in rep["excludes"]
    # AND THE LANE REALLY DOES CROSS. The fee is taken on the taker side,
    # so the census's claim is not merely a label.
    src = inspect.getsource(LOOP.cycle)
    assert "maker=False" in src


def test_the_settlement_coverage_is_grouped_by_sport():
    # One "0 COMPATIBLE" over two sports cannot say whether the second was
    # examined and refused or never reached the comparison.
    assert "sport_family" in EXT.SETTLEMENT_COVERAGE
    assert "GROUP BY 1, 2" in EXT.SETTLEMENT_COVERAGE
    assert "sport_family" in EXT.STAGE_CENSUS


def test_the_cycle_reports_the_universe_and_the_funnel():
    src = inspect.getsource(LOOP.cycle)
    assert '"venue_universe_by_label": universe' in src
    assert '"funnel_by_provider_sport": funnel' in src
    # PER-SPORT COUNTS AT THE THREE EARLY GATES, so "mapped 0" is
    # attributable to a sport rather than to the aggregate.
    for key in ("provider_events", "with_pinnacle_h2h",
                "mapped_to_a_venue_contract",
                "venue_markets_open_and_fresh"):
        assert key in src, key
    # AND THE UNIVERSE IS COUNTED FROM THE ROWS, which requires the column.
    assert "sport, updated_at" in LOOP.MARKETS_SQL


def test_the_heartbeat_carries_the_funnel_so_a_reader_need_not_rerun():
    src = inspect.getsource(LOOP._heartbeat)
    assert "venue_universe_by_label" in src
    assert "funnel_by_provider_sport" in src


def test_the_funnel_is_reporting_only_and_no_gate_reads_it():
    """A report that feeds a decision stops being a report."""
    whole = inspect.getsource(LOOP)
    for name in ("funnel_by_provider_sport", "venue_universe_by_label"):
        for line in whole.splitlines():
            if name not in line:
                continue
            # It may be WRITTEN (assignment, dict literal, heartbeat) but
            # never READ into a condition.
            assert not line.strip().startswith(("if ", "elif ", "while ")), \
                line
