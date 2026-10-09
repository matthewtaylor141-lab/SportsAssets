"""THE FEE POLICY, AGAINST THE VENUE'S OWN PUBLISHED NUMBERS.

The page was retrieved on the GitHub runner on 2026-09-27 (the build container's
egress denies that host) and the quotes are preserved in
`research/evidence/VENUE_FEE_POLICY_2026-09-27.md`.

WHY THESE VECTORS ARE THE STRONGEST AVAILABLE. They are the venue's own
"Standard Fee Schedule by Price" table -- price, trade value, taker pays, maker
receives, for a 100-lot -- transcribed from the retrieved page. They were NOT
computed by this module, which is the whole point: the `min(p, 1-p)` defect
survived its first review because the tests computed their expected answers with
the implementation under test.

The module was WRONG on rounding and this file would have caught it. Every
half-cent tie in the table resolves to banker's rounding, and under the previous
ROUND_HALF_UP several of these rows disagree.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sportsassets import calibration_fees as CF

#: THE PUBLISHED TABLE, transcribed verbatim from the retrieved page.
#: (price, trade_value_100_lot, taker_pays_100_lot, maker_receives_100_lot)
PUBLISHED_100_LOT = [
    ("0.01", "1", "0.07", "0.01"),
    ("0.02", "2", "0.14", "0.02"),
    ("0.03", "3", "0.20", "0.04"),
    ("0.04", "4", "0.27", "0.05"),
    ("0.05", "5", "0.33", "0.06"),
    ("0.06", "6", "0.39", "0.07"),
    ("0.07", "7", "0.45", "0.08"),
    ("0.08", "8", "0.51", "0.09"),
    ("0.09", "9", "0.57", "0.10"),
    ("0.10", "10", "0.63", "0.11"),
    ("0.11", "11", "0.68", "0.12"),
    ("0.12", "12", "0.73", "0.13"),
    ("0.13", "13", "0.79", "0.14"),
]


# ── the published table, row by row ─────────────────────────────────

@pytest.mark.parametrize("price,value,taker,maker", PUBLISHED_100_LOT)
def test_every_published_row_reproduces_exactly(price, value, taker, maker):
    got = CF.expected_fee(price, 100, CF.ROLE_TAKER)
    assert got["BLOCKER"] is None, got
    assert got["FEE"] == Decimal(taker), (
        "price %s: the page says the taker pays %s on a 100-lot" % (price, taker))
    reb = CF.expected_fee(price, 100, CF.ROLE_MAKER)
    assert -reb["FEE"] == Decimal(maker), (
        "price %s: the page says the maker receives %s" % (price, maker))
    # AND THE TRADE VALUE, so a transcription error in the table would show.
    assert Decimal(price) * 100 == Decimal(value)


def test_the_page_s_own_worked_example_reproduces():
    """From the page: "Buyer (taker): 0.0695 x 1,000 x 0.50 x 0.50 = -$17.38
    Seller (maker): 0.0125 x 1,000 x 0.50 x 0.50 = +$3.12"."""
    assert CF.expected_fee("0.50", 1000, CF.ROLE_TAKER)["FEE"] == Decimal("17.38")
    assert CF.expected_fee("0.50", 1000, CF.ROLE_MAKER)["FEE"] == Decimal("-3.12")


def test_theta_max_at_the_half_dollar_matches_the_page():
    """"Theta Max (p = $0.50) Taker Fee 0.0695 $1.74, Maker Rebate -0.0125
    -$0.31" -- a 100-lot."""
    assert CF.expected_fee("0.50", 100, CF.ROLE_TAKER)["FEE"] == Decimal("1.74")
    assert CF.expected_fee("0.50", 100, CF.ROLE_MAKER)["FEE"] == Decimal("-0.31")


def test_a_small_trade_can_round_to_zero_as_the_page_says():
    """"On small trades (low quantity or prices near $0.00 or $1.00), the fee can
    round down to $0.00." A zero here is CORRECT, not a missing fee."""
    got = CF.expected_fee("0.01", 1, CF.ROLE_TAKER)
    assert got["FEE"] == Decimal("0.00")
    assert got["BLOCKER"] is None, "zero is an answer, not a refusal"


# ── the rounding mode the page states ───────────────────────────────

def test_the_mode_is_the_published_one_and_the_audit_was_right():
    assert CF.ROUNDING_IMPLEMENTED == "ROUND_HALF_EVEN"
    u = CF.ROUNDING_UNRECONCILED
    assert u["verified"] is True
    assert u["resolved_on"] == "2026-09-27"
    assert "banker's rounding (round half to even)" in u["the_published_sentence"]
    assert "this module was wrong" in u["outcome"]


def test_the_old_mode_would_have_disagreed_with_the_page():
    """THE DEFECT, DEMONSTRATED. Every discriminating vector resolves to the
    half-even column, so the previous implementation over-collected a cent on
    each of them."""
    for q, price, raw, hu, he in CF.ROUNDING_DISCRIMINATORS:
        got = CF.expected_fee(price, q, CF.ROLE_TAKER)
        assert got["FEE"] == Decimal(he), (q, price)
        assert got["FEE_IF_HALF_UP"] == Decimal(hu)
        assert got["FEE"] != got["FEE_IF_HALF_UP"]


# ── the cumulative taker-fill adjustment ────────────────────────────

def test_the_cumulative_cap_holds_an_order_below_the_sum_of_its_fills():
    """THE PUBLISHED ALGORITHM, AND WHY IT IS NOT EITHER OBVIOUS SHORTCUT.

    Three fills of 40 at $0.50. Each fill's own banker's rounding is $0.70, so
    summing independent per-fill roundings gives $2.10. The cumulative exact fee
    is $2.085, whose banker's rounding is $2.08 -- and the page says the total
    collected "never exceeds" that. So the order pays $2.08 and the adjustment
    lands on the fills that would breach the cap.
    """
    got = CF.order_fees("0.50", [40, 40, 40])
    assert got["BLOCKER"] is None
    assert got["cumulative_exact"] == Decimal("2.08500000")
    assert got["cumulative_cap"] == Decimal("2.08")
    assert got["TOTAL"] == Decimal("2.08")
    assert got["total_never_exceeds_the_cap"] is True
    # NEITHER SHORTCUT WOULD HAVE GIVEN THIS.
    naive_sum = sum(Decimal("0.70") for _ in range(3))
    assert naive_sum == Decimal("2.10") and got["TOTAL"] < naive_sum
    # THE ADJUSTMENT IS VISIBLE PER FILL, so a reconciliation can match the
    # venue's own sequence rather than only its total.
    assert [f["collected"] for f in got["per_fill"]] == [
        Decimal("0.70"), Decimal("0.69"), Decimal("0.69")]
    assert [f["adjusted"] for f in got["per_fill"]] == [False, True, True]


def test_a_single_fill_is_the_ordinary_case_and_the_cap_never_binds():
    one = CF.order_fees("0.50", [120])
    assert one["TOTAL"] == CF.expected_fee("0.50", 120, CF.ROLE_TAKER)["FEE"]
    assert one["per_fill"][0]["adjusted"] is False


def test_the_running_total_never_exceeds_the_cap_at_any_point():
    """The invariant the sentence states, checked FILL BY FILL rather than only
    at the end -- the cap is a running one."""
    for fills in ([10, 10, 10, 10], [1] * 9, [33, 33, 34], [7, 120, 3]):
        got = CF.order_fees("0.37", fills)
        assert got["BLOCKER"] is None
        for f in got["per_fill"]:
            assert f["cumulative_collected"] <= f["cumulative_cap"], (fills, f)
        assert got["TOTAL"] <= got["cumulative_cap"]


def test_maker_rebates_are_per_fill_and_carry_no_cap():
    """"Maker rebates are computed per fill, independently." A separate function
    precisely so the taker cap cannot be generalised onto this side."""
    got = CF.maker_rebates("0.50", [40, 40, 40])
    assert got["algorithm"] == "PER_FILL_INDEPENDENT_NO_CUMULATIVE_CAP"
    assert "independently" in got["the_published_sentence"]
    each = [f["rebate"] for f in got["per_fill"]]
    assert got["TOTAL"] == sum(each), "no cap is applied on this side"
    assert "cumulative_cap" not in got


# ── the coefficient is per sport, with effective dates ──────────────

def test_theta_is_per_sport_and_the_table_tennis_change_is_represented():
    """"The Table Tennis taker fee coefficient becomes 0.10" -- "Both of the
    following take effect at 12:00 AM ET on Wednesday, October 7, 2026" (the
    published page as re-read on 2026-10-09; on 2026-09-27 it read 11:59 PM ET
    September 30, and that superseded instant is kept, labelled, in
    CF.TABLE_TENNIS_SUPERSEDED_INSTANT). A single constant would have silently
    mispriced it from that instant."""
    assert CF.taker_coefficient(None, "2026-09-27T00:00:00Z") == Decimal("0.0695")
    assert CF.taker_coefficient("TABLE_TENNIS",
                                "2026-09-27T00:00:00Z") == Decimal("0.0695")
    assert CF.taker_coefficient("TABLE_TENNIS",
                                "2026-10-08T00:00:00Z") == Decimal("0.10")
    # BETWEEN THE SUPERSEDED AND THE PUBLISHED INSTANT the venue charged the
    # exchange-wide coefficient: no 0.10 before 12:00 AM ET Oct 7.
    assert CF.taker_coefficient("TABLE_TENNIS",
                                "2026-10-01T03:59:00Z") == Decimal("0.0695")
    assert CF.taker_coefficient("TABLE_TENNIS",
                                "2026-10-02T00:00:00Z") == Decimal("0.0695")
    # THE BOUNDARY, both sides of it. 12:00 AM ET (EDT) Wed = 04:00Z Wed.
    assert CF.taker_coefficient("TABLE_TENNIS",
                                "2026-10-07T03:59:00Z") == Decimal("0.0695")
    assert CF.taker_coefficient("TABLE_TENNIS",
                                "2026-10-07T04:00:00Z") == Decimal("0.10")
    assert CF.TABLE_TENNIS_SUPERSEDED_INSTANT["instant"] == \
        "2026-10-01T03:59:00Z"
    assert CF.SCHEDULE_EFFECTIVE_EXCHANGE_WIDE == "2026-09-25T04:00:00Z"


def test_an_unknown_sport_falls_back_to_the_exchange_wide_coefficient():
    assert CF.taker_coefficient("SOMETHING_NEW",
                                "2026-09-27T00:00:00Z") == Decimal("0.0695")


# ── what is deliberately NOT implemented ────────────────────────────

def test_a_combo_refuses_rather_than_being_priced_with_the_standard_curve():
    """"The taker side of a combo trade uses a separate fee curve." Pricing one
    with the standard curve would UNDERSTATE the charge."""
    got = CF.combo_fee()
    assert got["FEE"] is None
    assert got["BLOCKER"] == CF.R_COMBO_CURVE_NOT_IMPLEMENTED
    assert "0.04" in got["published_curve"]
    assert "understate" in got["why"]


def test_exactness_is_claimed_only_for_what_was_verified():
    assert CF.FEE_ARITHMETIC_IS_EXACT is True
    assert "standard taker curve" in CF.FEE_ARITHMETIC_EXACTNESS_COVERS
    excl = " ".join(CF.FEE_ARITHMETIC_EXACTNESS_EXCLUDES)
    assert "combo" in excl
    assert "LATER weekly payment" in excl, (
        "the tiered rebate is paid later and must never be netted into a charge")
    assert "price_scale" in excl


def test_execution_report_units_are_not_dollars():
    u = CF.EXECUTION_REPORT_UNITS
    assert u["is_not_dollars"] is True
    assert u["field"] == "commission_notional_collected"
    assert set(u["decode_requires"]) == {"price_scale",
                                         "fractional_quantity_scale"}
    assert "wrong by the scale factor" in u["consequence"]
