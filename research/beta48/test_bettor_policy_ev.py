"""Tests for the BETTOR policy EV model.

Every test asserts a CONSEQUENCE -- cash, a refusal, a named missing
input -- not that a function was called.

Run:  python -m pytest research/beta48/test_bettor_policy_ev.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bettor_policy_ev as m          # noqa: E402


# ── the fee arithmetic, against the VERIFIED schedule ──────────────────
class TestTheFeeIsTheVenuesFee:
    def test_the_maker_leg_is_a_credit_and_the_taker_leg_a_charge(self):
        assert m.fee(0.50, 100, maker=True) > 0
        assert m.fee(0.50, 100, maker=False) < 0

    def test_the_maximum_rebate_is_at_p_one_half(self):
        best = max(m.fee(p / 100.0, 10_000, maker=True)
                   for p in range(1, 100))
        assert best == m.fee(0.50, 10_000, maker=True)

    def test_bankers_rounding_per_fill_is_not_a_rounding_detail(self):
        # A one-contract fill at the documented median mid earns NOTHING,
        # because the raw rebate is under half a cent and rounds to zero.
        assert m.fee(0.445, 1, maker=True) == 0.0
        # Ten contracts earn three cents. The same trade, ten times the
        # size, is not ten times the rebate.
        assert m.fee(0.445, 10, maker=True) == 0.03
        assert m.fee(0.445, 1, maker=True) * 10 != m.fee(0.445, 10,
                                                         maker=True)

    def test_a_thousand_one_lots_earn_less_than_one_thousand_lot(self):
        many = sum(m.fee(0.445, 1, maker=True) for _ in range(1000))
        once = m.fee(0.445, 1000, maker=True)
        assert many == 0.0 and once > 0.0


# ── the refusal to invent an input ─────────────────────────────────────
class TestItRefusesToInventAnInput:
    def setup_method(self):
        self.b = m.Book("x", 0.20, 0.30)
        self.i = m.Inventory()

    def test_quoting_is_not_identified_and_says_exactly_what_is_missing(self):
        v = m.incremental_ev("QUOTE_OFFER", self.b, self.i)
        assert v.ev is None and v.identified is False
        assert set(v.missing) == {m.U_P_FILL, m.U_AS_FILL, m.U_REBATE}

    def test_supplying_the_inputs_identifies_it_and_nothing_else_does(self):
        v = m.incremental_ev("QUOTE_OFFER", self.b, self.i,
                             p_fill=0.1, as_fill=-0.009,
                             rebate_eligible=True, contracts=100)
        assert v.identified and v.ev is not None

    def test_the_policy_does_nothing_when_it_cannot_price_the_action(self):
        act, v = m.policy_passive_maker(self.b, self.i)
        assert act == "DO_NOTHING"
        assert "REFUSED" in v.note

    def test_crossing_alone_needs_a_fair_value_we_do_not_have(self):
        v = m.incremental_ev("CROSS_BUY", self.b, self.i)
        assert v.ev is None
        assert v.missing == ["FV_BETTOR_INDEPENDENT"]

    def test_a_one_sided_book_prices_nothing(self):
        v = m.incremental_ev("CROSS_PAIR", m.Book("x", None, 0.30), self.i)
        assert v.ev is None and v.missing == ["TWO_SIDED_BOOK"]


# ── the decidable class ────────────────────────────────────────────────
class TestTheTakerPairIsDecidableWithoutAFillModel:
    def test_it_is_identified_with_no_optional_inputs_at_all(self):
        v = m.incremental_ev("CROSS_PAIR", m.Book("x", 0.20, 0.30),
                             m.Inventory(), contracts=100)
        assert v.identified and v.ev is not None

    def test_the_basis_is_one_plus_the_spread_exactly(self):
        for bid, ask in ((0.20, 0.30), (0.60, 0.605), (0.01, 0.99)):
            v = m.incremental_ev("CROSS_PAIR", m.Book("x", bid, ask),
                                 m.Inventory())
            assert abs(v.terms["basis"] - (1.0 + (ask - bid))) < 1e-12

    def test_a_positive_spread_loses_money_before_a_fee_is_charged(self):
        v = m.incremental_ev("CROSS_PAIR", m.Book("x", 0.20, 0.30),
                             m.Inventory(), contracts=100)
        assert v.ev < 0
        assert v.terms["fees"] < 0            # and the fee makes it worse

    def test_the_policy_declines_every_pair_that_costs_par_or_more(self):
        act, _ = m.policy_taker_pair(m.Book("x", 0.20, 0.30), m.Inventory())
        assert act == "DO_NOTHING"

    def test_it_would_take_a_pair_that_was_genuinely_below_par(self):
        # crossed book: ask below bid. Not observed on this venue -- the
        # point is that the policy is capable of saying yes, so its "no"
        # is a measurement and not a hard-coded refusal.
        act, v = m.policy_taker_pair(m.Book("x", 0.60, 0.30), m.Inventory())
        assert act == "CROSS_PAIR" and v.ev > 0


# ── no double counting ─────────────────────────────────────────────────
class TestTheSpreadIsNeverCountedTwice:
    def test_completing_a_pair_subtracts_the_sunk_first_leg(self):
        b = m.Book("x", 0.40, 0.45)
        inv = m.Inventory(contracts=100, avg_cost=0.45)
        v = m.incremental_ev("COMPLETE_PAIR", b, inv)
        # payout 100, complement costs (1-0.40)*100 = 60, already paid 45
        assert abs(v.terms["already_paid"] - 45.0) < 1e-9
        assert v.ev < 100.0 - 60.0          # the sunk leg is subtracted

    def test_the_pair_ev_equals_its_two_legs_and_carries_no_spread_term(self):
        b = m.Book("x", 0.20, 0.30)
        v = m.incremental_ev("CROSS_PAIR", b, m.Inventory(), contracts=100)
        legs = 100.0 - (b.ask + (1.0 - b.bid)) * 100.0
        assert abs((v.ev - v.terms["fees"]) - legs) < 1e-9
        assert "spread" not in v.terms

    def test_doing_nothing_is_exactly_zero_and_is_the_baseline(self):
        v = m.incremental_ev("DO_NOTHING", m.Book("x", 0.2, 0.3),
                             m.Inventory())
        assert v.ev == 0.0 and v.identified


# ── the tick is the market's, not a constant ───────────────────────────
class TestTheTickComesFromTheMarket:
    def test_a_half_cent_market_is_quoted_on_its_own_grid(self):
        b = m.Book("x", 0.600, 0.610, tick=0.005)
        v = m.incremental_ev("QUOTE_OFFER", b, m.Inventory(),
                             p_fill=0.1, as_fill=-0.001, rebate_eligible=True)
        assert v.terms["tick_used"] == 0.005
        assert abs(v.terms["quote_price"] - 0.605) < 1e-9

    def test_a_book_one_tick_wide_is_quoted_AT_the_touch_not_through_it(self):
        b = m.Book("x", 0.600, 0.605, tick=0.005)
        v = m.incremental_ev("QUOTE_OFFER", b, m.Inventory(),
                             p_fill=0.1, as_fill=-0.001, rebate_eligible=True)
        assert v.terms["quote_price"] == 0.605
        assert v.terms["at_touch"] is True

    def test_assuming_a_cent_would_quote_through_a_half_cent_book(self):
        # the defect the holdout found: without the market's own tick the
        # offer lands at 0.595, INSIDE the bid, which is not a quote.
        b = m.Book("x", 0.600, 0.605)       # no tick -> 0.01 fallback
        v = m.incremental_ev("QUOTE_OFFER", b, m.Inventory(),
                             p_fill=0.1, as_fill=-0.001, rebate_eligible=True)
        assert v.terms["quote_price"] == 0.605     # falls back to the touch
        assert b.t == m.DEFAULT_TICK


# ── the stress bound keeps its scope ───────────────────────────────────
class TestTheStressBoundIsNotPromotedToAnExpectation:
    def test_the_rn1_deficit_still_loses_after_the_pmus_rebate(self):
        b = m.Book("x", 0.35, 0.36)
        v = m.incremental_ev("QUOTE_OFFER", b, m.Inventory(), contracts=100,
                             p_fill=1.0, as_fill=-0.0090,
                             rebate_eligible=True)
        assert v.ev < 0
        rebate = v.terms["rebate_if_filled"]
        assert 0 < rebate < 0.90            # covers part of the 90c deficit

    def test_a_rebate_cannot_rescue_a_negative_trade_on_its_own(self):
        b = m.Book("x", 0.49, 0.50)
        worst = m.incremental_ev("QUOTE_OFFER", b, m.Inventory(),
                                 contracts=100, p_fill=1.0, as_fill=-0.0090,
                                 rebate_eligible=True)
        assert worst.ev < 0


class TestTheModuleDescribesItsOwnLimits:
    def test_kalshi_is_absent_and_says_so(self):
        assert "ABSENT" in m.describe()["kalshi"]

    def test_the_three_unidentified_inputs_are_named(self):
        assert set(m.describe()["unidentified_inputs"]) == {
            m.U_P_FILL, m.U_AS_FILL, m.U_REBATE}

    def test_the_fee_schedule_is_the_verified_one(self):
        """THE REGIME MOVED AND THIS TEST PINNED THE OLD ONE.

        theta_taker went 0.06 -> 0.0695 at 2026-09-17T03:59Z. This
        assertion held the engine on the JUL2026 coefficient, so every
        taker fee it computed after the cutover was 13.7% too small --
        favouring exactly the policies that exit inventory as takers.
        """
        f = m.describe()["fee_schedule"]
        assert f["theta_taker"] == 0.0695 and f["theta_maker"] == -0.0125
        assert f["status"] == "VERIFIED"

    def test_it_reproduces_all_five_published_fee_examples(self):
        """https://docs.polymarket.us/fees, fetched 2026-09-22.

        These are the venue's own worked examples. If the coefficient
        or the rounding is wrong, they do not reproduce.
        """
        for n, p, taker, maker in ((1000, 0.10, -6.26, +1.12),
                                   (1000, 0.65, -15.81, +2.84),
                                   (1000, 0.30, -14.60, +2.62),
                                   (1000, 0.90, -6.26, +1.12),
                                   (1000, 0.50, -17.38, +3.12)):
            assert abs(m.fee(p, n, maker=False) - taker) < 0.011
            assert abs(m.fee(p, n, maker=True) - maker) < 0.011

    def test_the_taker_regime_is_selected_by_the_fills_timestamp(self):
        """The capture straddles the cutover, so one constant is wrong
        for one side of it whichever value it takes."""
        before = 1789200000.0          # 2026-09-13
        after = 1789800000.0           # 2026-09-20
        assert m.theta_taker_at(before) == m.THETA_TAKER_JUL2026
        assert m.theta_taker_at(after) == m.THETA_TAKER_SEP2026
        assert m.theta_taker_at(None) == m.THETA_TAKER_SEP2026
        # and the fee actually differs across it
        assert (abs(m.fee(0.50, 1000, maker=False, at_epoch=before))
                < abs(m.fee(0.50, 1000, maker=False, at_epoch=after)))

    def test_a_multi_level_sweep_is_capped_at_the_cumulative_rounding(self):
        """The published rule: per-fill rounding, "adjusted so that the
        total commission collected across the order's fills never
        exceeds the banker's rounding of the cumulative exact fee. The
        adjustment can only reduce a fill's charge, never increase it."

        Per-fill rounding alone OVER-charges a sweep; this is the cap.
        """
        fills = [(0.13, 1)] * 10
        capped = m.taker_fee_for_order(fills)
        per_fill = sum(m.fee(p, n, maker=False) for p, n in fills)
        assert capped >= per_fill, "the cap may only reduce the charge"
        # a case where per-fill rounding genuinely over-charges
        many = [(0.50, 1)] * 7
        assert m.taker_fee_for_order(many) >= sum(
            m.fee(p, n, maker=False) for p, n in many)
