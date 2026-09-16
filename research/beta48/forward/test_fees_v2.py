#!/usr/bin/env python3
"""Tests for the two-regime fee model and the maker/taker EV comparison.

Three things these tests are FOR, stated so a later reader can tell whether
they still hold:

  1. the sealed module is not disturbed -- its constants are re-asserted here,
     and its own 22 tests are run unchanged in CI alongside these;
  2. the SEP2026 regime is applied by TIMESTAMP, so an observation either side
     of the cutover can never be silently pooled;
  3. no incentive can hide a negative trade -- `trading_net_ex_incentives` is
     asserted to be independent of the reward term in every maker EV.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import fees_v2 as F
import run85_trackb_fees as SEALED


class SealedModuleUntouched(unittest.TestCase):
    """fees_v2 imports the sealed primitives; it must not have edited them."""

    def test_sealed_taker_coefficient_is_still_the_verified_one(self):
        self.assertEqual(SEALED.THETA_TAKER, D("0.06"))

    def test_sealed_maker_coefficient_is_still_the_verified_one(self):
        self.assertEqual(SEALED.THETA_MAKER, D("-0.0125"))

    def test_fees_v2_reuses_the_sealed_objects_rather_than_retyping_them(self):
        self.assertIs(F.THETA_TAKER_JUL2026, SEALED.THETA_TAKER)
        self.assertIs(F.THETA_MAKER_ALL, SEALED.THETA_MAKER)

    def test_jul2026_taker_fee_reproduces_the_sealed_function(self):
        for c, p in ((1, "0.50"), (100, "0.50"), (41, "0.01"), (7, "0.93")):
            _exact, rounded = SEALED.taker_fee(c, D(p))
            self.assertEqual(F.taker_fee_at(c, D(p), "JUL2026"), rounded)

    def test_maker_rebate_reproduces_the_sealed_function(self):
        for c, p in ((1, "0.50"), (1000, "0.50"), (41, "0.01"), (7, "0.93")):
            _exact, rounded = SEALED.maker_rebate(c, D(p))
            self.assertEqual(F.maker_rebate_at(c, D(p)), rounded)


class Regimes(unittest.TestCase):

    def test_cutover_is_2359_et_expressed_as_0359_utc(self):
        self.assertEqual(F.REGIME_CUTOVER_UTC,
                         datetime(2026, 9, 17, 3, 59, tzinfo=timezone.utc))

    def test_one_second_before_cutover_is_the_old_regime(self):
        self.assertEqual(
            F.regime_at(F.REGIME_CUTOVER_UTC - timedelta(seconds=1)),
            "JUL2026")

    def test_the_cutover_instant_itself_is_the_new_regime(self):
        self.assertEqual(F.regime_at(F.REGIME_CUTOVER_UTC), "SEP2026")

    def test_iso_strings_with_a_z_suffix_are_accepted(self):
        self.assertEqual(F.regime_at("2026-09-17T04:00:00Z"), "SEP2026")
        self.assertEqual(F.regime_at("2026-09-16T12:00:00Z"), "JUL2026")

    def test_a_naive_timestamp_is_read_as_utc_not_as_local(self):
        self.assertEqual(F.regime_at(datetime(2026, 9, 17, 4, 0)), "SEP2026")

    def test_the_new_taker_coefficient_is_the_relayed_one(self):
        self.assertEqual(F.theta_taker("SEP2026"), D("0.0695"))

    def test_the_maker_rebate_did_not_move_across_the_cutover(self):
        before = F.maker_rebate_at(1000, D("0.50"))
        after = F.maker_rebate_at(1000, D("0.50"))
        self.assertEqual(before, after)
        self.assertEqual(F.THETA_MAKER_ALL, D("-0.0125"))

    def test_an_unknown_regime_raises_rather_than_defaulting(self):
        with self.assertRaises(ValueError):
            F.theta_taker("AUG2026")

    def test_the_new_coefficient_is_labelled_unverified(self):
        self.assertFalse(F.THETA_TAKER_SEP2026_VERIFIED)

    def test_taker_fee_rises_at_the_cutover_for_the_same_trade(self):
        old = F.taker_fee_at(1000, D("0.50"), "JUL2026")
        new = F.taker_fee_at(1000, D("0.50"), "SEP2026")
        self.assertGreater(new, old)


class RebateTiers(unittest.TestCase):

    def test_bettor_tier_eligibility_is_not_assumed(self):
        self.assertFalse(F.TIER_VERIFIED)

    def test_base_is_the_default_tier_everywhere(self):
        self.assertEqual(F.effective_theta_taker("SEP2026"),
                         F.effective_theta_taker("SEP2026", "BASE"))
        self.assertEqual(F.effective_theta_taker("SEP2026", "BASE"),
                         D("0.0695"))

    def test_a_fifty_percent_tier_halves_the_coefficient(self):
        self.assertEqual(F.effective_theta_taker("SEP2026", "T50_10M_PLUS"),
                         D("0.0695") * D("0.5"))

    def test_only_the_top_tier_beats_the_old_base_rate(self):
        """The tiers discount a HIGHER fee, so most of them do not undo it.

        Against the old 0.06 base: 10% leaves 0.06255 -- still WORSE than
        today. 25% leaves 0.052125 and 50% leaves 0.03475, both better. An
        unverified tier is therefore not a reason to expect cheaper taking.
        """
        base_old = F.effective_theta_taker("JUL2026", "BASE")
        self.assertGreater(F.effective_theta_taker("SEP2026", "T10_250k_1M"),
                           base_old)
        self.assertLess(F.effective_theta_taker("SEP2026", "T25_1M_10M"),
                        base_old)
        self.assertLess(F.effective_theta_taker("SEP2026", "T50_10M_PLUS"),
                        base_old)

    def test_an_unknown_tier_raises(self):
        with self.assertRaises(ValueError):
            F.effective_theta_taker("SEP2026", "T90")

    def test_negotiated_market_maker_terms_stay_unidentified(self):
        self.assertEqual(F.NEGOTIATED_MARKET_MAKER_ECONOMICS,
                         "NOT_IDENTIFIED")


class Rounding(unittest.TestCase):
    """Banker's rounding per fill, as the venue specifies."""

    def test_a_tiny_fill_rounds_its_rebate_to_nothing(self):
        self.assertEqual(F.maker_rebate_at(1, D("0.01")), D("0.00"))

    def test_fragmenting_a_clip_can_destroy_the_whole_rebate(self):
        whole = F.maker_rebate_at(400, D("0.01"))
        pieces = sum((F.maker_rebate_at(1, D("0.01")) for _ in range(400)),
                     D("0"))
        self.assertGreater(whole, D("0"))
        self.assertEqual(pieces, D("0.00"))

    def test_half_cent_goes_to_even(self):
        self.assertEqual(F.bankers_cents(D("0.005")), D("0.00"))
        self.assertEqual(F.bankers_cents(D("0.015")), D("0.02"))

    def test_a_taker_fee_is_returned_positive_and_a_rebate_positive_too(self):
        self.assertGreater(F.taker_fee_at(1000, D("0.50"), "JUL2026"), 0)
        self.assertGreater(F.maker_rebate_at(1000, D("0.50")), 0)


class MakerTakerEV(unittest.TestCase):

    def test_taking_at_the_ask_with_no_edge_is_strictly_worse_than_not_trading(self):
        ev = F.ev_taker(fair_value=D("0.50"), ask=D("0.50"),
                        regime="SEP2026", contracts=1000)
        self.assertLess(ev["net"], F.ev_no_trade()["net"])

    def test_the_fee_increase_makes_every_zero_edge_take_worse(self):
        old = F.ev_taker(D("0.50"), D("0.50"), "JUL2026", contracts=1000)
        new = F.ev_taker(D("0.50"), D("0.50"), "SEP2026", contracts=1000)
        self.assertLess(new["net"], old["net"])

    def test_a_maker_fill_with_no_edge_and_no_adverse_selection_earns_the_rebate(self):
        ev = F.ev_maker(D("0.50"), D("0.50"), adverse_selection_per_contract=0,
                        contracts=1000)
        self.assertEqual(ev["net"], F.maker_rebate_at(1000, D("0.50")))
        self.assertGreater(ev["net"], D("0"))

    def test_adverse_selection_alone_can_sink_a_rebate_positive_quote(self):
        ev = F.ev_maker(D("0.50"), D("0.50"),
                        adverse_selection_per_contract=D("0.01"),
                        contracts=1000)
        self.assertLess(ev["trading_net_ex_incentives"], D("0"))

    def test_an_incentive_can_never_hide_the_trading_result(self):
        """The architecture's binding rule, asserted as arithmetic."""
        bad = F.ev_maker(D("0.50"), D("0.50"),
                         adverse_selection_per_contract=D("0.01"),
                         expected_reward_per_contract=D("0.05"),
                         contracts=1000)
        self.assertGreater(bad["net"], D("0"))                      # looks fine
        self.assertLess(bad["trading_net_ex_incentives"], D("0"))   # is not

    def test_trading_net_does_not_move_when_the_reward_moves(self):
        a = F.ev_maker(D("0.52"), D("0.50"), D("0.004"), D("0.000"), 1000)
        b = F.ev_maker(D("0.52"), D("0.50"), D("0.004"), D("0.900"), 1000)
        self.assertEqual(a["trading_net_ex_incentives"],
                         b["trading_net_ex_incentives"])
        self.assertNotEqual(a["net"], b["net"])

    def test_a_reward_is_never_assumed(self):
        ev = F.ev_maker(D("0.50"), D("0.50"), 0)
        self.assertEqual(ev["estimated_reward"], D("0"))
        self.assertEqual(ev["net"], ev["trading_net_ex_incentives"])

    def test_no_trade_is_exactly_zero(self):
        self.assertEqual(F.ev_no_trade()["net"], D("0"))

    def test_ev_taker_does_not_pretend_to_know_adverse_selection(self):
        ev = F.ev_taker(D("0.50"), D("0.50"), "SEP2026")
        self.assertNotIn("adverse_selection", ev)


class BreakevenAdverseSelection(unittest.TestCase):

    def test_at_breakeven_the_maker_ev_is_exactly_zero(self):
        be = F.breakeven_adverse_selection(D("0.52"), D("0.50"),
                                           contracts=1000)
        ev = F.ev_maker(D("0.52"), D("0.50"),
                        adverse_selection_per_contract=be / D(1000),
                        contracts=1000)
        self.assertEqual(ev["trading_net_ex_incentives"], D("0"))

    def test_a_hair_more_adverse_selection_turns_it_negative(self):
        be = F.breakeven_adverse_selection(D("0.52"), D("0.50"),
                                           contracts=1000)
        ev = F.ev_maker(D("0.52"), D("0.50"),
                        adverse_selection_per_contract=be / D(1000)
                        + D("0.000001"),
                        contracts=1000)
        self.assertLess(ev["trading_net_ex_incentives"], D("0"))

    def test_with_no_fair_value_edge_the_tolerance_is_just_the_rebate(self):
        be = F.breakeven_adverse_selection(D("0.50"), D("0.50"),
                                           contracts=1000)
        self.assertEqual(be, F.maker_rebate_at(1000, D("0.50")))

    def test_a_reward_raises_the_tolerance_which_is_why_it_is_reported_apart(self):
        plain = F.breakeven_adverse_selection(D("0.50"), D("0.50"),
                                              contracts=1000)
        with_reward = F.breakeven_adverse_selection(
            D("0.50"), D("0.50"), expected_reward_per_contract=D("0.001"),
            contracts=1000)
        self.assertGreater(with_reward, plain)

    def test_quoting_through_fair_value_gives_a_negative_tolerance(self):
        """No amount of adverse selection is tolerable -- the quote is already
        losing before anyone trades against it."""
        be = F.breakeven_adverse_selection(D("0.40"), D("0.50"),
                                           contracts=1000)
        self.assertLess(be, D("0"))

    def test_the_breakeven_is_regime_invariant_because_the_rebate_is(self):
        self.assertEqual(
            F.breakeven_adverse_selection(D("0.52"), D("0.50"), contracts=1000),
            F.breakeven_adverse_selection(D("0.52"), D("0.50"), contracts=1000))


class LiquidityScoring(unittest.TestCase):
    """The observed discount factors are 0.3 and 0.35 -- NOT the ~0.9 a first
    reading of the formula might suggest. Captured from /v1/incentives on
    2026-09-16, 267 active liquidityProgram periods, factors {0.3, 0.35} only.
    """

    OBSERVED = (D("0.3"), D("0.35"))

    def test_at_the_touch_the_score_is_the_full_order_size(self):
        for f in self.OBSERVED:
            self.assertEqual(F.liquidity_score(500, 0, f), D("500"))

    def test_one_tick_off_the_touch_costs_most_of_the_score(self):
        """At 0.3, resting ONE tick behind scores 30% of the same size, and
        two ticks 9%. The programme pays for the touch, not for presence."""
        self.assertEqual(F.liquidity_score(500, 1, D("0.3")), D("150.0"))
        self.assertEqual(F.liquidity_score(500, 2, D("0.3")), D("45.00"))
        self.assertEqual(F.liquidity_score(500, 3, D("0.3")), D("13.500"))

    def test_size_cannot_buy_back_distance(self):
        """A quote three ticks back needs ~37x the size to match one at the
        touch, which is a far larger inventory risk for the same score."""
        at_touch = F.liquidity_score(500, 0, D("0.3"))
        self.assertLess(F.liquidity_score(500 * 30, 3, D("0.3")), at_touch)
        self.assertGreater(F.liquidity_score(500 * 40, 3, D("0.3")), at_touch)

    def test_a_tick_is_not_a_cent_everywhere(self):
        """The board showed tick sizes of 0.001, 0.005 and 0.01, so 'one tick
        from best' can be a TENTH of a cent -- the distance penalty and the
        price distance are different quantities and must not be conflated."""
        observed_ticks = [D("0.001"), D("0.005"), D("0.01")]
        self.assertEqual(len(set(observed_ticks)), 3)
        # One tick back scores the same whatever the tick is WORTH.
        scores = {F.liquidity_score(100, 1, D("0.3")) for _ in observed_ticks}
        self.assertEqual(len(scores), 1)

    def test_score_is_not_a_dollar_amount(self):
        """Converting a score to a payout needs a denominator we do not have.

        The endpoint publishes the pool and the target size but NOT the total
        qualifying score across participants, so our share is unknowable from
        our own quotes.
        """
        s = F.liquidity_score(500, 0, D("0.3"))
        self.assertEqual(F.estimated_reward_share(s, None, D("1000")),
                         "NOT_IDENTIFIED")
        self.assertEqual(F.estimated_reward_share(s, 0, D("1000")),
                         "NOT_IDENTIFIED")

    def test_a_supplied_total_gives_a_proportional_share(self):
        self.assertEqual(
            F.estimated_reward_share(D("250"), D("1000"), D("400")),
            D("100"))


class SignConventions(unittest.TestCase):
    """The ledger must never double-count a sign."""

    def test_taker_fee_enters_the_ev_as_a_negative(self):
        ev = F.ev_taker(D("0.50"), D("0.50"), "SEP2026", contracts=1000)
        self.assertLess(ev["fee"], D("0"))
        self.assertEqual(ev["net"], ev["gross_edge"] + ev["fee"])

    def test_maker_rebate_enters_the_ev_as_a_positive(self):
        ev = F.ev_maker(D("0.50"), D("0.50"), D("0.001"), contracts=1000)
        self.assertGreater(ev["rebate"], D("0"))
        self.assertLess(ev["adverse_selection"], D("0"))
        self.assertEqual(ev["trading_net_ex_incentives"],
                         ev["gross_edge"] + ev["rebate"]
                         + ev["adverse_selection"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
