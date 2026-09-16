#!/usr/bin/env python3
"""Tests for the thirteen-term forward ledger.

The ledger's whole job is to refuse three specific moves:
  * summing an incentive that was never earned,
  * crediting a channel out of a programme that cannot have paid it,
  * reporting TOTAL_NET without the trading figure beside it.
Each has a test that fails if the refusal is removed.
"""
from __future__ import annotations

import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger as L


class EverythingStartsUnknown(unittest.TestCase):

    def test_a_fresh_ledger_asserts_nothing(self):
        led = L.new_ledger()
        self.assertEqual(set(led.values()), {"NOT_IDENTIFIED"})
        self.assertEqual(len(led), 13)

    def test_the_thirteen_terms_are_exactly_these(self):
        self.assertEqual(set(L.ALL_TERMS), {
            "TRADING_EDGE", "SPREAD_CAPTURE", "MAKER_REBATE", "TAKER_FEE",
            "TAKER_FEE_REBATE", "ADVERSE_SELECTION", "INVENTORY_PNL",
            "EXIT_HEDGE_COST", "LIQUIDITY_INCENTIVE", "FILL_INCENTIVE",
            "VOLUME_INCENTIVE", "NEGOTIATED_MM_INCENTIVE", "TOTAL_NET_PNL"})

    def test_zero_and_not_identified_are_different_claims(self):
        """Zero says 'we measured none'. NOT_IDENTIFIED says 'we cannot see
        it'. A total may rest on the first and never on the second."""
        led = L.new_ledger()
        L.credit(led, "TRADING_EDGE", D("0"), verified=True)
        t = L.totals(led)
        self.assertNotIn("TRADING_EDGE", t["TRADING_TERMS_NOT_IDENTIFIED"])
        self.assertIn("SPREAD_CAPTURE", t["TRADING_TERMS_NOT_IDENTIFIED"])

    def test_the_total_is_never_derived_by_hand(self):
        with self.assertRaises(ValueError):
            L.credit(L.new_ledger(), "TOTAL_NET_PNL", D("5"), verified=True)

    def test_an_unknown_term_raises(self):
        with self.assertRaises(ValueError):
            L.credit(L.new_ledger(), "VIBES", D("1"), verified=True)


class AnUnverifiedAmountIsNotIncome(unittest.TestCase):

    def test_an_unverified_credit_leaves_the_term_unknown(self):
        led = L.new_ledger()
        L.credit(led, "MAKER_REBATE", D("100"), verified=False)
        self.assertEqual(led["MAKER_REBATE"], "NOT_IDENTIFIED")

    def test_a_scheduled_incentive_is_refused_not_caveated(self):
        """A caveat does not survive being summed, so the refusal happens at
        the point of entry."""
        led = L.new_ledger()
        L.credit_incentive(led, "LIQUIDITY_INCENTIVE", D("999"),
                           verified=False, basis="RESTING_PRESENCE")
        self.assertEqual(led["LIQUIDITY_INCENTIVE"], "NOT_IDENTIFIED")
        self.assertEqual(L.totals(led)["INCENTIVE_CONTRIBUTION"], D("0"))


class ChannelsAreNeverBlended(unittest.TestCase):

    def test_a_volume_programme_cannot_pay_a_resting_quote(self):
        """VOLUME_INCENTIVE is paid on TAKER-SIDE notional. Crediting it to
        resting presence would pay a maker out of a taker programme."""
        led = L.new_ledger()
        L.credit_incentive(led, "VOLUME_INCENTIVE", D("50"),
                           verified=True, basis="RESTING_PRESENCE")
        self.assertEqual(led["VOLUME_INCENTIVE"], "NOT_IDENTIFIED")

    def test_the_same_amount_on_the_right_basis_is_accepted(self):
        led = L.new_ledger()
        L.credit_incentive(led, "VOLUME_INCENTIVE", D("50"),
                           verified=True, basis="TAKER_SIDE_NOTIONAL")
        self.assertEqual(led["VOLUME_INCENTIVE"], D("50"))

    def test_liquidity_and_fill_are_different_channels(self):
        """Liquidity pays for resting; fill pays for passive EXECUTION and so
        carries adverse selection that liquidity does not."""
        led = L.new_ledger()
        L.credit_incentive(led, "LIQUIDITY_INCENTIVE", D("10"), True,
                           "RESTING_PRESENCE")
        L.credit_incentive(led, "FILL_INCENTIVE", D("10"), True,
                           "RESTING_PRESENCE")      # wrong basis for fill
        self.assertEqual(led["LIQUIDITY_INCENTIVE"], D("10"))
        self.assertEqual(led["FILL_INCENTIVE"], "NOT_IDENTIFIED")

    def test_the_negotiated_channel_can_never_be_credited(self):
        """Its basis is NOT_IDENTIFIED, so no basis can match it."""
        led = L.new_ledger()
        for basis in ("RESTING_PRESENCE", "PASSIVE_EXECUTION",
                      "TAKER_SIDE_NOTIONAL", "NOT_IDENTIFIED"):
            L.credit_incentive(led, "NEGOTIATED_MM_INCENTIVE", D("1000"),
                               True, basis)
            self.assertEqual(led["NEGOTIATED_MM_INCENTIVE"], "NOT_IDENTIFIED")


class TheThreeTotals(unittest.TestCase):

    def _complete_trading(self, **over):
        led = L.new_ledger()
        vals = {"TRADING_EDGE": D("0"), "SPREAD_CAPTURE": D("10"),
                "MAKER_REBATE": D("1"), "TAKER_FEE": D("0"),
                "TAKER_FEE_REBATE": D("0"), "ADVERSE_SELECTION": D("-4"),
                "INVENTORY_PNL": D("0"), "EXIT_HEDGE_COST": D("-2")}
        vals.update(over)
        for k, v in vals.items():
            L.credit(led, k, v, verified=True)
        return led

    def test_trading_net_excludes_every_incentive(self):
        led = self._complete_trading()
        L.credit_incentive(led, "LIQUIDITY_INCENTIVE", D("500"), True,
                           "RESTING_PRESENCE")
        t = L.totals(led)
        self.assertEqual(t["TRADING_NET_EX_INCENTIVES"], D("5"))
        self.assertEqual(t["INCENTIVE_CONTRIBUTION"], D("500"))
        self.assertEqual(t["TOTAL_NET"], D("505"))

    def test_an_incentive_cannot_hide_a_losing_trade(self):
        led = self._complete_trading(ADVERSE_SELECTION=D("-40"))
        L.credit_incentive(led, "LIQUIDITY_INCENTIVE", D("500"), True,
                           "RESTING_PRESENCE")
        t = L.totals(led)
        self.assertLess(t["TRADING_NET_EX_INCENTIVES"], 0)     # it IS losing
        self.assertGreater(t["TOTAL_NET"], 0)                  # it LOOKS fine
        self.assertEqual(t["EDGE_SOURCE"], "B_INCENTIVES_ONLY")

    def test_a_missing_trading_term_is_named_not_treated_as_zero(self):
        led = L.new_ledger()
        L.credit(led, "SPREAD_CAPTURE", D("10"), verified=True)
        t = L.totals(led)
        self.assertFalse(t["TRADING_NET_IS_COMPLETE"])
        self.assertIn("ADVERSE_SELECTION", t["TRADING_TERMS_NOT_IDENTIFIED"])


class WhereTheMoneyCameFrom(unittest.TestCase):

    def _led(self, adverse, incentive):
        led = L.new_ledger()
        for k, v in (("TRADING_EDGE", D("0")), ("SPREAD_CAPTURE", D("10")),
                     ("MAKER_REBATE", D("1")), ("TAKER_FEE", D("0")),
                     ("TAKER_FEE_REBATE", D("0")),
                     ("ADVERSE_SELECTION", D(str(adverse))),
                     ("INVENTORY_PNL", D("0")),
                     ("EXIT_HEDGE_COST", D("0"))):
            L.credit(led, k, v, verified=True)
        if incentive:
            L.credit_incentive(led, "LIQUIDITY_INCENTIVE", D(str(incentive)),
                               True, "RESTING_PRESENCE")
        return led

    def test_A_is_edge_only(self):
        self.assertEqual(L.totals(self._led(-4, 0))["EDGE_SOURCE"],
                         "A_MARKET_MAKING_EDGE")

    def test_B_is_incentives_only(self):
        self.assertEqual(L.totals(self._led(-40, 500))["EDGE_SOURCE"],
                         "B_INCENTIVES_ONLY")

    def test_C_is_both(self):
        self.assertEqual(L.totals(self._led(-4, 500))["EDGE_SOURCE"],
                         "C_BOTH")

    def test_a_losing_total_is_neither(self):
        self.assertEqual(L.totals(self._led(-400, 5))["EDGE_SOURCE"],
                         "NEITHER_TOTAL_NOT_POSITIVE")

    def test_the_question_is_refused_while_the_edge_is_unmeasured(self):
        """'It came from edge' cannot be claimed from a partial edge."""
        led = L.new_ledger()
        L.credit(led, "SPREAD_CAPTURE", D("10"), verified=True)
        self.assertEqual(L.totals(led)["EDGE_SOURCE"], "NOT_IDENTIFIED")


class Rendering(unittest.TestCase):

    def test_the_render_always_shows_the_trading_figure_and_the_basis(self):
        led = L.new_ledger()
        L.credit_incentive(led, "LIQUIDITY_INCENTIVE", D("5"), True,
                           "RESTING_PRESENCE")
        out = L.render(led)
        self.assertIn("TRADING_NET_EX_INCENTIVES", out)
        self.assertIn("TOTAL_NET", out)
        self.assertIn("basis=TAKER_SIDE_NOTIONAL", out)
        self.assertIn("UNMEASURED TRADING TERMS", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
