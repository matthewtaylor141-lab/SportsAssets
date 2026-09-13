#!/usr/bin/env python3
"""Offline tests for the Run 85 Phase 2C selection rule. Contacts nothing.

The rule is the deliverable of Phase 2C, so it is the thing that must be
tested: determinism, the forced asymmetry coverage, the one-market-per-event
cap, and the bins being drawn from the OBSERVED distribution rather than from
thresholds carried in from somewhere else.

Run:  python3 -m pytest research/test_run85_phase2c.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

_s = importlib.util.spec_from_file_location(
    "p2c", Path(__file__).with_name("run85_phase2c.py"))
P = importlib.util.module_from_spec(_s)
_s.loader.exec_module(P)


def cand(slug, ev, mid="0.50", spread="0.002", tick="0.001",
         bidN="100", askN="100", bid_depth="1000", ask_depth="1000",
         tte=None, changed=False):
    import math
    return {
        "market_slug": slug, "event_id": ev, "event_slug": "ev%s" % ev,
        "mid": mid, "best_bid": mid, "best_ask": mid, "spread": spread,
        "tick_size": tick, "spread_ticks": P.spread_ticks(Decimal(spread), tick),
        "touch_bid_notional": bidN, "touch_ask_notional": askN,
        "bid_depth_usd": bid_depth, "ask_depth_usd": ask_depth,
        "depth_log_ratio": math.log10(float(ask_depth) / float(bid_depth)),
        "time_to_event_s": tte, "book_changed": changed,
    }


# ------------------------------------------------------------ binning
def test_spread_is_measured_in_the_markets_own_ticks():
    assert P.spread_ticks(Decimal("0.003"), "0.001") == 3
    assert P.spread_ticks(Decimal("0.01"), "0.01") == 1
    assert P.spread_ticks(Decimal("0.006"), "0.001") == 6


def test_spread_ticks_refuses_to_guess_without_a_tick():
    assert P.spread_ticks(Decimal("0.003"), None) is None
    assert P.spread_ticks(None, "0.001") is None


def test_bins_come_from_the_observed_distribution_not_fixed_cuts():
    """Same market, two different populations, two different bins. If the cuts
    were imported constants this test could not pass."""
    cheap = [cand("a", 1, mid="0.10"), cand("b", 2, mid="0.20"),
             cand("c", 3, mid="0.30")]
    rich = [cand("a", 1, mid="0.10"), cand("b", 2, mid="0.80"),
            cand("c", 3, mid="0.90")]
    P.assign_bins(cheap)
    P.assign_bins(rich)
    assert cheap[2]["price_bin"] == "P_HIGH"     # 0.30 is top of ITS pool
    assert rich[0]["price_bin"] == "P_LOW"       # 0.10 is bottom of ITS pool


def test_asymmetry_bins_split_bid_heavy_balanced_ask_heavy():
    f = [cand("a", 1, bid_depth="10000", ask_depth="100"),
         cand("b", 2, bid_depth="1000", ask_depth="1000"),
         cand("c", 3, bid_depth="100", ask_depth="10000")]
    P.assign_bins(f)
    assert [x["asym_bin"] for x in f] == ["BID_HEAVY", "BALANCED", "ASK_HEAVY"]


def test_time_to_event_bins_and_the_unknown_case():
    f = [cand("a", 1, tte=3600), cand("b", 2, tte=3 * 86400),
         cand("c", 3, tte=30 * 86400), cand("d", 4, tte=None)]
    P.assign_bins(f)
    assert [x["tte_bin"] for x in f] == ["T_LT_24H", "T_1_7D", "T_GT_7D", "UNKNOWN"]


def test_activity_bin_reflects_observed_change():
    f = [cand("a", 1, changed=True), cand("b", 2, changed=False)]
    P.assign_bins(f)
    assert [x["act_bin"] for x in f] == ["ACTIVE", "QUIET"]


# ---------------------------------------------------------- selection
def test_selection_is_deterministic_under_input_reordering():
    f = [cand("m%d" % i, i, mid="0.%02d" % (i * 7),
              bid_depth=str(100 * (i + 1)), ask_depth=str(50 * (i + 1)))
         for i in range(9)]
    P.assign_bins(f)
    a, _ = P.select(list(f), 4)
    b, _ = P.select(list(reversed(f)), 4)
    assert [x["market_slug"] for x in a] == [x["market_slug"] for x in b]


def test_one_market_per_event_is_enforced():
    f = [cand("a", 1), cand("b", 1), cand("c", 1)]
    P.assign_bins(f)
    chosen, _ = P.select(f, 3)
    assert len(chosen) == 1


def test_asymmetry_coverage_is_forced_before_size_ranked_filling():
    """Twelve look-alike ask-heavy markets in one big regime, plus one
    bid-heavy and one balanced. Size ranking alone would take the big group
    first and never reach the other two sides."""
    f = [cand("z%02d" % i, 100 + i, bid_depth="100", ask_depth="10000")
         for i in range(12)]
    f.append(cand("bid_heavy", 1, bid_depth="10000", ask_depth="100"))
    f.append(cand("balanced", 2, bid_depth="1000", ask_depth="1000"))
    P.assign_bins(f)
    chosen, _ = P.select(f, 3)
    assert {x["asym_bin"] for x in chosen} == {"BID_HEAVY", "BALANCED", "ASK_HEAVY"}
    assert any(x["selected_because"].startswith("S5_") for x in chosen)


def test_the_cap_is_respected():
    f = [cand("m%d" % i, i, bid_depth=str(10 ** (i % 4 + 1))) for i in range(20)]
    P.assign_bins(f)
    assert len(P.select(f, 2)[0]) == 2
    assert len(P.select(f, 6)[0]) <= 6


def test_every_selected_market_records_why_it_was_selected():
    f = [cand("a", 1, bid_depth="10000", ask_depth="100"), cand("b", 2)]
    P.assign_bins(f)
    chosen, _ = P.select(f, 2)
    assert all(x.get("selected_because") for x in chosen)


def test_selection_never_mutates_the_candidate_pool():
    f = [cand("a", 1), cand("b", 2)]
    P.assign_bins(f)
    before = [dict(x) for x in f]
    P.select(f, 2)
    assert f == before


# --------------------------------------------------------- diversity
def test_diversity_is_insufficient_when_the_cohort_collapses_to_one_bin():
    f = [cand("a", 1, bid_depth="100", ask_depth="10000"),
         cand("b", 2, bid_depth="100", ask_depth="10000")]
    P.assign_bins(f)
    v, _ = P.diversity_verdicts(f, f)
    assert v["DEPTH_ASYMMETRY_DIVERSITY"] == "INSUFFICIENT"


def test_diversity_is_sufficient_when_two_bins_are_covered():
    f = [cand("a", 1, bid_depth="10000", ask_depth="100"),
         cand("b", 2, bid_depth="100", ask_depth="10000")]
    P.assign_bins(f)
    v, _ = P.diversity_verdicts(f, f)
    assert v["DEPTH_ASYMMETRY_DIVERSITY"] == "SUFFICIENT"


def test_a_dimension_the_venue_never_supplies_reads_not_identified():
    """Phase 2B's empty liquidity slots must not come back as INSUFFICIENT --
    absent is a different answer from present-and-narrow."""
    f = [cand("a", 1, tte=None), cand("b", 2, tte=None)]
    P.assign_bins(f)
    v, _ = P.diversity_verdicts(f, f)
    assert v["TIME_TO_EVENT_DIVERSITY"] == "NOT_IDENTIFIED"


# ----------------------------------------------------------- cadence
def test_the_cadence_table_is_the_arithmetic_the_docstring_states():
    assert P.cadence_table() == [(1, 2.0), (2, 4.0), (3, 6.0),
                                 (4, 8.0), (5, 10.0), (6, 12.0)]


def test_the_fast_tier_cap_preserves_the_five_second_markout():
    assert 2.0 * P.FAST_TIER_CAP <= 5.0


def test_the_survey_tier_cap_preserves_thirty_and_sixty():
    assert 2.0 * P.SURVEY_TIER_CAP <= 30.0


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(vars().items())
           if n.startswith("test_") and callable(f)]
    bad = []
    for n, f in fns:
        try:
            f()
        except Exception as exc:                       # noqa: BLE001
            bad.append((n, exc))
    for n, exc in bad:
        print("FAIL %s: %r" % (n, exc))
    print("%d passed, %d failed" % (len(fns) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)
