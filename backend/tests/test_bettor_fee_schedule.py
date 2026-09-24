"""The published PMUS fee schedules.

The properties that matter are the ones the engine got WRONG: the shape
(p(1-p), not flat), the SIGN (maker is a rebate), the DATE (0.06 before
2026-09-17, 0.0695 after), and the two rounding policies.

One test pins this module against `research/run85_trackb_fees.py`, which
has carried the 2026-07-01 schedule since July. Duplicated arithmetic
that nothing cross-checks is how the two copies drift apart.
"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from decimal import Decimal  # noqa: E402

from sportsassets import bettor_fee_schedule as fs  # noqa: E402

D = Decimal
S07 = fs.PMUS_2026_07_01
S09 = fs.PMUS_2026_09_17


def _run85():
    """Load the research module by path; `research/` is not a package."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "..", "research", "run85_trackb_fees.py")
    path = os.path.normpath(path)
    if not os.path.exists(path):
        pytest.skip("research/run85_trackb_fees.py not present")
    spec = importlib.util.spec_from_file_location("run85_trackb_fees", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── the shape ────────────────────────────────────────────────────────

def test_the_fee_is_proportional_to_p_times_one_minus_p():
    """Not flat. This is the error a flat constant cannot express."""
    assert S09.exact(S09.theta_taker, 1, 0.50) == D("0.0695") * D("0.25")
    # A penny contract is nearly free; a coin flip costs the most.
    assert S09.exact(S09.theta_taker, 1, 0.02) < S09.exact(
        S09.theta_taker, 1, 0.50) / 10


def test_the_fee_peaks_at_one_half_and_vanishes_at_the_bounds():
    peak = S09.exact(S09.theta_taker, 1, D("0.5"))
    for p in ("0.01", "0.10", "0.30", "0.49", "0.51", "0.70", "0.99"):
        assert S09.exact(S09.theta_taker, 1, D(p)) < peak
    assert S09.exact(S09.theta_taker, 1, D("0")) == 0
    assert S09.exact(S09.theta_taker, 1, D("1")) == 0


def test_the_fee_is_symmetric_about_one_half():
    """rebate(a) == rebate(1-a), so a short leg may use either spelling.

    It does NOT make two legs of a pair equal: they sit at bid and
    1-ask, which differ by the spread.
    """
    for p in ("0.02", "0.17", "0.485", "0.4999"):
        assert (S09.exact(S09.theta_maker, 10, D(p))
                == S09.exact(S09.theta_maker, 10, D(1) - D(p)))


def test_the_fee_is_linear_in_contracts_before_rounding():
    assert (S09.exact(S09.theta_taker, 10, D("0.4"))
            == 10 * S09.exact(S09.theta_taker, 1, D("0.4")))


# ── the sign ─────────────────────────────────────────────────────────

def test_the_maker_side_is_a_rebate_not_a_charge():
    """The single largest input error in the evaluation so far."""
    assert S09.theta_maker < 0
    assert S09.maker_rebate(100, D("0.485")) < 0
    assert S09.maker_rebate_income(100, D("0.485")) > 0


def test_the_size_of_the_sign_error_at_the_quoted_price():
    """At p=0.485 the engine subtracted +0.01 where the venue pays.

    -0.0125 x 0.249775 = -0.00312 per contract received. The engine
    subtracted +0.01. The gap is 0.0131 per contract.
    """
    per = S09.exact(S09.theta_maker, 1, D("0.485"))
    assert per == D("-0.0125") * D("0.485") * D("0.515")
    engine_was = D("0.01")          # subtracted, as a charge
    swing = engine_was - per        # 0.01 - (-0.00312)
    assert D("0.0130") < swing < D("0.0132")


def test_fill_fee_returns_the_venue_sign_on_both_sides():
    """So no caller has to branch on which way fees go."""
    assert S09.fill_fee(100, D("0.5"), maker=False) > 0
    assert S09.fill_fee(100, D("0.5"), maker=True) < 0


# ── the dates ────────────────────────────────────────────────────────

def test_the_two_schedules_differ_on_the_taker_theta():
    assert S07.theta_taker == D("0.06")
    assert S09.theta_taker == D("0.0695")
    assert S07.theta_maker == S09.theta_maker == D("-0.0125")


def test_for_date_selects_the_schedule_in_force_on_that_day():
    assert fs.for_date("2026-07-01") is S07
    assert fs.for_date("2026-08-31") is S07
    assert fs.for_date("2026-09-16") is S07
    assert fs.for_date("2026-09-17") is S09      # inclusive of the day
    assert fs.for_date("2027-01-01") is S09


def test_a_fill_before_the_first_published_schedule_is_refused():
    """Not priced at the nearest schedule. There is no published fee."""
    with pytest.raises(ValueError) as e:
        fs.for_date("2026-06-30")
    assert "no published schedule covers" in str(e.value)


def test_for_date_requires_a_date_and_has_no_default():
    """A schedule without a date loses the 0.06/0.0695 distinction."""
    for bad in (None, "", "2026", 20260917):
        with pytest.raises(ValueError):
            fs.for_date(bad)


def test_a_long_but_malformed_date_is_refused_not_ranked():
    """THE GAP THE CASES ABOVE LEFT OPEN.

    All four of them fail on type or length and never reach the
    comparison. A string of ten or more characters did reach it, and
    the comparison is LEXICOGRAPHIC -- "not-a-date" sorts above every
    effective_from, so it silently selected the NEWEST schedule. That
    is the "apply 0.0695 to a July fill" failure arriving through a
    malformed date rather than a missing one.
    """
    for bad in ("not-a-date", "9999-99-99", "yyyy-mm-dd",
                "2026-13-01", "2026-02-30"):
        with pytest.raises(ValueError):
            fs.for_date(bad)
    # CONTROL: a real date must still resolve, or the guard above would
    # be indistinguishable from a function that refuses everything.
    assert fs.for_date("2026-09-20") is not None
    assert fs.for_date("2026-09-23T14:40:00+00:00") is not None


def test_an_august_fill_is_charged_the_july_theta():
    """The concrete consequence of the date being part of the schedule."""
    aug = fs.for_date("2026-08-15").taker_fee(1000, D("0.5"))
    sep = fs.for_date("2026-09-20").taker_fee(1000, D("0.5"))
    assert aug == D("15.00")        # 0.06 x 1000 x 0.25
    assert sep == D("17.38")        # 0.0695 x 1000 x 0.25 = 17.375
    assert sep > aug


# ── rounding ─────────────────────────────────────────────────────────

def test_rounding_is_bankers_to_the_cent():
    assert fs.bankers_cents(D("0.005")) == D("0.00")     # half to EVEN
    assert fs.bankers_cents(D("0.015")) == D("0.02")
    assert fs.bankers_cents(D("0.025")) == D("0.02")
    assert fs.bankers_cents(D("0.0151")) == D("0.02")


def test_the_cumulative_taker_adjustment_does_not_accumulate_rounding():
    """Three 1-contract fills at 0.50: 0.05 cumulative, 0.06 independent."""
    acc = S09.taker_accrual(D("0.5"))
    deltas = [acc.add_fill(1) for _ in range(3)]
    assert acc.charged == S09.taker_fee(3, D("0.5")) == D("0.05")
    assert sum(deltas) == D("0.05")
    # Each fill rounded on its own would have charged 0.02 three times.
    assert S09.taker_fee(1, D("0.5")) * 3 == D("0.06")
    assert deltas == [D("0.02"), D("0.01"), D("0.02")]


def test_the_cumulative_charge_never_double_charges_the_same_contracts():
    """Fill 3 then 7 and the account pays exactly what 10 cost."""
    acc = S09.taker_accrual(D("0.37"))
    a = acc.add_fill(3)
    b = acc.add_fill(7)
    assert a + b == S09.taker_fee(10, D("0.37"))
    assert acc.filled == 10


def test_the_july_schedule_rounds_each_taker_fill_independently():
    """The two sources disagree here, and both are implemented."""
    assert S07.taker_rounding == fs.PER_FILL_INDEPENDENT
    assert S09.taker_rounding == fs.CUMULATIVE_PER_ORDER
    acc = S07.taker_accrual(D("0.5"))
    deltas = [acc.add_fill(1) for _ in range(3)]
    # 0.06 x 1 x 0.25 = 0.015 -> banker's -> 0.02, three times = 0.06.
    assert deltas == [D("0.02")] * 3
    assert acc.charged == sum(deltas) == D("0.06")
    # The same three fills computed cumulatively would be
    # 0.06 x 3 x 0.25 = 0.045 -> 0.04. The policy is worth $0.02 here,
    # in the opposite direction to the 2026-09-17 case above -- which is
    # why neither is a rounding detail.
    assert S07.taker_fee(3, D("0.5")) == D("0.04")


def test_the_rounding_disagreement_is_recorded_not_resolved():
    d = S09.describe()
    assert "rounding_disagreement" in d
    assert "settled statement" in d["rounding_disagreement"]
    assert "rounding_disagreement" not in S07.describe()


def test_maker_rebates_round_independently_and_are_sub_additive():
    """Ten 1-contract fills can earn less than one 10-contract fill."""
    acc = S09.maker_accrual(D("0.485"))
    for _ in range(10):
        acc.add_fill(1)
    one_clip = S09.maker_rebate(10, D("0.485"))
    assert acc.credited == D("0.00")        # every fill rounded away
    assert one_clip == D("-0.03")
    assert abs(acc.credited) < abs(one_clip)


def test_a_small_maker_fill_can_round_its_entire_rebate_away():
    """The pilot's 1-contract quotes earn exactly nothing per fill."""
    assert S09.maker_rebate(1, D("0.445")) == D("0.00")
    assert S09.min_contracts_for_a_cent(D("0.5")) == 2
    assert S09.min_contracts_for_a_cent(D("0.01")) == 41


def test_min_contracts_for_a_cent_is_strict_at_the_boundary():
    """Exactly half a cent rounds to the even cent, which is zero."""
    n = S09.min_contracts_for_a_cent(D("0.5"))
    per = -S09.exact(S09.theta_maker, 1, D("0.5"))
    assert fs.bankers_cents(per * (n - 1)) < fs.CENT
    assert fs.bankers_cents(per * n) >= fs.CENT


# ── status, tier, provenance ─────────────────────────────────────────

def test_nothing_is_verified_as_applied():
    for s in fs.SCHEDULES:
        assert s.status == fs.PUBLISHED
        assert s.describe()["verified_applied"] is False


def test_no_volume_tier_is_assumed():
    for s in fs.SCHEDULES:
        assert s.volume_tier == fs.NOT_ESTABLISHED


def test_the_module_records_that_this_was_an_unconnected_module():
    """Not missing information. The distinction is the finding."""
    prior = fs.describe()["prior_implementation"]
    assert prior["module"] == "research/run85_trackb_fees.py"
    assert prior["since"] == "2026-07-01"
    assert "never imported" in prior["note"]


def test_a_corrected_schedule_is_not_an_edge():
    assert "NOT_IDENTIFIED" in (
        fs.describe()["supersedes_in_bettor_engine"]["does_not_follow"])


# ── cross-check against the module that had it right in July ─────────

def test_the_july_schedule_agrees_with_run85_exactly():
    """Duplicated arithmetic that nothing cross-checks drifts apart."""
    r = _run85()
    assert S07.theta_taker == r.THETA_TAKER
    assert S07.theta_maker == r.THETA_MAKER
    assert S07.effective_from == r.EFFECTIVE_DATE
    assert S07.source == r.SOURCE
    for c, p in ((1, "0.5"), (10, "0.485"), (37, "0.02"), (1000, "0.445")):
        ex_t, rd_t = r.taker_fee(c, D(p))
        assert S07.exact(S07.theta_taker, c, D(p)) == ex_t
        assert S07.taker_fee(c, D(p)) == rd_t
        ex_m, rd_m = r.maker_rebate(c, D(p))      # run85 reports income
        assert S07.maker_rebate_income(c, D(p)) == rd_m
        assert -S07.exact(S07.theta_maker, c, D(p)) == ex_m


def test_min_contracts_for_a_cent_agrees_with_run85():
    r = _run85()
    for p in ("0.5", "0.445", "0.01", "0.17"):
        assert (S07.min_contracts_for_a_cent(D(p))
                == r.min_contracts_for_a_cent(D(p)))
