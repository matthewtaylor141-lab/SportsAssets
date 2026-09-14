#!/usr/bin/env python3
"""Offline tests for the verified PMUS fee rules. Contacts nothing.

Every worked example the primary documentation gives is pinned here, including
both banker's-rounding ties. If the venue changes the schedule these fail
loudly rather than drifting.

Run:  python3 -m pytest research/test_run85_trackb_fees.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal as D
from pathlib import Path

_s = importlib.util.spec_from_file_location(
    "tbfees", Path(__file__).with_name("run85_trackb_fees.py"))
F = importlib.util.module_from_spec(_s)
_s.loader.exec_module(F)


# ------------------------------------------- the documented worked examples
def test_the_three_documented_examples_reproduce_exactly():
    for contracts, price, taker, maker in (
            (1000, "0.10", "5.40", "1.12"),
            (1000, "0.65", "13.65", "2.84"),
            (1000, "0.50", "15.00", "3.12")):
        assert F.taker_fee(contracts, price)[1] == D(taker), (price, "taker")
        assert F.maker_rebate(contracts, price)[1] == D(maker), (price, "maker")


def test_the_documented_per_hundred_figures_reproduce():
    assert F.taker_fee(100, "0.50")[1] == D("1.50")
    assert F.maker_rebate(100, "0.50")[1] == D("0.31")


def test_bankers_rounding_breaks_ties_to_even_not_upward():
    """1.125 -> 1.12 and 3.125 -> 3.12 are the documented outputs. Ordinary
    half-up rounding would give 1.13 and 3.13 and both examples would fail."""
    assert F.bankers_cents(D("1.125")) == D("1.12")
    assert F.bankers_cents(D("3.125")) == D("3.12")
    assert F.bankers_cents(D("0.015")) == D("0.02")     # ties go to even
    assert F.bankers_cents(D("0.025")) == D("0.02")


def test_the_maker_coefficient_is_a_rebate_not_a_charge():
    assert F.THETA_MAKER < 0
    assert F.maker_rebate(1000, "0.50")[0] > 0


def test_the_sealed_venue_field_is_the_taker_coefficient():
    """feeCoefficient = 0.06 on all 51,566 sealed market rows matches THETA
    for the taker and must never be applied to a maker."""
    assert F.THETA_TAKER == D("0.06")
    assert F.THETA_MAKER != D("0.06")


# ------------------------------------------------------------- symmetry
def test_a_leg_at_p_and_a_leg_at_one_minus_p_earn_the_same_rebate():
    """p(1-p) is symmetric, and under B-1 the short leg IS priced 1 - the long
    leg, so no separate short-side fee model is needed."""
    for p in ("0.10", "0.25", "0.37", "0.605"):
        lo = F.maker_rebate(1000, p)[0]
        hi = F.maker_rebate(1000, str(1 - D(p)))[0]
        assert lo == hi, p


def test_the_rebate_peaks_at_the_midpoint():
    mid = F.rebate_per_contract("0.50")
    for p in ("0.01", "0.10", "0.25", "0.75", "0.99"):
        assert F.rebate_per_contract(p) < mid, p


# ------------------------------------------------------------- rounding
def test_a_small_fill_rounds_its_rebate_away_to_nothing():
    """THE SMALL-CLIP TRAP. One contract at 0.50 earns $0.003125, which is
    under half a cent and rounds to zero."""
    assert F.maker_rebate(1, "0.50")[1] == D("0.00")
    assert F.maker_rebate(1, "0.50")[0] > 0        # the exact value is not zero


def test_the_cent_threshold_rises_sharply_as_price_leaves_the_middle():
    assert F.min_contracts_for_a_cent("0.50") == 2
    assert F.min_contracts_for_a_cent("0.01") > 40
    assert (F.min_contracts_for_a_cent("0.01")
            > F.min_contracts_for_a_cent("0.25")
            > F.min_contracts_for_a_cent("0.50"))


def test_fragmenting_the_same_volume_can_destroy_the_rebate_entirely():
    """100 contracts at 0.01 in one fill earns a cent; the same 100 contracts
    in 100 one-contract fills earns nothing, because each fill rounds alone."""
    r = F.fragmentation(100, "0.01", 100)
    assert r["rounded_if_one_fill"] > 0
    assert r["rounded_if_fragmented"] == 0
    assert r["lost_to_rounding"] == r["rounded_if_one_fill"]


def test_exact_and_rounded_are_always_reported_together():
    r = F.maker_pair(100, "0.60", "0.605")
    assert "exact_unrounded_maker_rebate" in r
    assert "actual_rounded_maker_rebate" in r


# ------------------------------------------------------- the pair budget
def test_the_pair_budget_is_spread_plus_both_rebates_and_nothing_else():
    r = F.maker_pair(1000, "0.50", "0.51")
    assert r["displayed_pair_edge"] == D("0.01") * 1000
    assert r["budget_exact"] == (r["displayed_pair_edge"]
                                 + r["exact_unrounded_maker_rebate"])


def test_no_taker_fee_is_ever_subtracted_from_a_maker_pair():
    """Subtracting 0.06 from a maker would be the exact error the verified
    schedule rules out."""
    r = F.maker_pair(1000, "0.50", "0.51")
    taker = F.taker_fee(1000, "0.50")[0]
    assert r["budget_exact"] > r["displayed_pair_edge"]      # rebate ADDS
    assert r["budget_exact"] != r["displayed_pair_edge"] - taker


def test_the_budget_is_never_called_profit():
    r = F.maker_pair(1000, "0.50", "0.51")
    for k in ("maker_fill_probability", "maker_pair_completion_probability",
              "adverse_selection", "incomplete_pair_cost",
              "expected_economic_edge"):
        assert r[k] == "NOT_IDENTIFIED", k


def test_near_the_midpoint_the_rebate_can_exceed_the_displayed_spread():
    """At a one-tick 0.005 spread around 0.50 the two rebates are worth more
    than the spread itself -- which is why the maker coefficient changes the
    sign of the question and had to be verified rather than assumed."""
    r = F.maker_pair(1000, "0.4975", "0.5025")
    assert r["exact_unrounded_maker_rebate"] > r["displayed_pair_edge"]


def test_deep_in_the_tail_the_rebate_is_negligible_against_the_spread():
    r = F.maker_pair(1000, "0.005", "0.010")
    assert r["exact_unrounded_maker_rebate"] < r["displayed_pair_edge"] / 10


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
