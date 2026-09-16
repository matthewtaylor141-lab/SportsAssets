#!/usr/bin/env python3
"""The capability labels, asserted so they cannot drift into optimism.

Each test here guards one specific way a label could quietly become a claim we
have not earned.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import venue_capabilities as V


class CombosCostIsKnownAndBenefitIsNot(unittest.TestCase):

    def test_the_premium_is_verified_and_dated(self):
        self.assertEqual(V.UPCOMING_COMBO_TAKER_COST_PREMIUM,
                         "VERIFIED_FROM_PRIMARY_DOCS")

    def test_the_benefit_is_unresolved_and_not_recorded_as_zero(self):
        """Recording an unmeasured benefit as zero is the same error as
        recording an unmeasured cost as zero, pointed the other way."""
        for f in ("COMBO_ORPHAN_RISK_REDUCTION", "COMBO_EXECUTION_ATOMICITY",
                  "COMBO_PARTIAL_FILL_BEHAVIOR",
                  "COMBO_NET_VALUE_VS_SINGLE_LEG_EXECUTION"):
            v = getattr(V, f)
            self.assertEqual(v, "NOT_IDENTIFIED", f)
            self.assertNotIn(v, (0, 0.0, False, "NONE", "ZERO"), f)

    def test_a_verified_cost_does_not_settle_the_net_value(self):
        self.assertNotEqual(V.UPCOMING_COMBO_TAKER_COST_PREMIUM,
                            V.COMBO_NET_VALUE_VS_SINGLE_LEG_EXECUTION)

    def test_drop_copy_attribution_is_marked_relayed_not_captured(self):
        self.assertIn("RELAYED", V.COMBO_EXECUTION_ATTRIBUTION_SOURCE)


class TheWebsocketStaysBlocked(unittest.TestCase):

    def test_high_value_does_not_imply_permission(self):
        """The two are independent, and this is the test that says so."""
        self.assertEqual(V.WEBSOCKET_DATA_VALUE, "HIGH")
        self.assertEqual(V.SAFE_TO_CONNECT_WITH_EXISTING_TRADING_CAPABLE_KEY,
                         "NO")
        self.assertFalse(V.WEBSOCKET_CONNECTED)

    def test_no_documented_retail_read_only_scope(self):
        self.assertEqual(V.RETAIL_READ_ONLY_KEY_SCOPE_DOCUMENTED, "NO")
        self.assertEqual(V.READ_ONLY_CREDENTIAL_ISOLATION, "NOT_IDENTIFIED")

    def test_all_three_isolation_routes_are_open_questions(self):
        self.assertEqual(len(V.WEBSOCKET_ISOLATION_ROUTES), 3)
        for k, v in V.WEBSOCKET_ISOLATION_ROUTES.items():
            self.assertEqual(v, "NOT_IDENTIFIED", k)

    def test_the_cost_of_not_having_it_is_written_down(self):
        """So the blocker is a stated trade-off, not a silent omission."""
        self.assertIn("ADVERSE_SELECTION", V.WEBSOCKET_WOULD_MEASURE)
        self.assertIn("TOUCH_TO_TRADE", V.WEBSOCKET_WOULD_MEASURE)
        self.assertIn("TRADE_AGGRESSOR", V.WEBSOCKET_WOULD_MEASURE)


class CollateralIsCapitalNotAlpha(unittest.TestCase):

    def test_it_is_classified_as_a_multiplier(self):
        self.assertEqual(V.COLLATERAL_RETURN_CLASSIFICATION,
                         "CAPITAL_EFFICIENCY_MULTIPLIER")
        self.assertFalse(V.COLLATERAL_RETURN_IS_ALPHA_SOURCE)

    def test_no_magnitude_is_claimed_before_the_pages_are_captured(self):
        for f in ("MARGIN_WITHOUT_OFFSET", "MARGIN_WITH_OFFSET",
                  "BUYING_POWER_FREED", "CAPITAL_REUSE_ALLOWED",
                  "CLOSE_POSITION_COLLATERAL_REQUIREMENT",
                  "NET_PNL_PER_WORKING_CAPITAL_DOLLAR_PER_HOUR"):
            self.assertEqual(getattr(V, f), "NOT_IDENTIFIED", f)

    def test_all_three_reuse_restrictions_are_carried(self):
        self.assertEqual(len(V.CAPITAL_REUSE_RESTRICTIONS), 3)
        self.assertIn("NOT_IN_SAME_EVENT_THAT_GENERATED_IT",
                      V.CAPITAL_REUSE_RESTRICTIONS)
        self.assertIn("CLOSE_MAY_BE_REJECTED_IF_FREED_CAPITAL_DEPLOYED",
                      V.CAPITAL_REUSE_RESTRICTIONS)

    def test_the_close_obligation_hazard_is_recorded_as_binding(self):
        """Freed capital spent elsewhere can make an offsetting leg
        uncloseable exactly when the book is under stress. The allocator has
        to be built against that, so it is a flag, not a footnote."""
        self.assertTrue(V.FREED_CAPITAL_MAY_CREATE_CLOSE_OBLIGATION)
        self.assertTrue(V.ALLOCATOR_MUST_RESERVE_UNWIND_CAPACITY)


class NothingHereActivatesAnything(unittest.TestCase):

    def test_the_module_holds_labels_and_no_behaviour(self):
        """It cannot connect, sign or trade because it contains no executable
        code at all. Proved structurally, not by word-scan.

        A banned-word scan is the wrong test here and was tried first: it fails
        on `SAFE_TO_CONNECT_...`, which is the very label that records the
        prohibition. The same mistake as scanning a docstring for the words it
        uses to promise a guarantee.

        The structural claim is strictly stronger anyway. A module whose body
        is nothing but literal assignments cannot call anything, so it cannot
        open a socket however it is named.
        """
        import ast
        src = (Path(__file__).resolve().parent
               / "venue_capabilities.py").read_text()
        tree = ast.parse(src)

        self.assertEqual(
            [n for n in ast.walk(tree)
             if isinstance(n, (ast.Import, ast.ImportFrom))
             and not (isinstance(n, ast.ImportFrom)
                      and n.module == "__future__")],
            [], "a labels module imports nothing")
        self.assertEqual(
            [n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef))],
            [], "a labels module defines no functions or classes")

        # THE STRONG ONE: not a single call, await, comprehension or control
        # flow node anywhere in the file.
        forbidden = (ast.Call, ast.Await, ast.Lambda, ast.If, ast.For,
                     ast.While, ast.With, ast.Try, ast.ListComp, ast.DictComp,
                     ast.SetComp, ast.GeneratorExp)
        offenders = [type(n).__name__ for n in ast.walk(tree)
                     if isinstance(n, forbidden)]
        self.assertEqual(offenders, [],
                         "executable constructs in a labels module: %s"
                         % offenders)

        # Every top-level statement is an assignment (or the docstring).
        for node in tree.body:
            self.assertIsInstance(
                node, (ast.Assign, ast.AnnAssign, ast.Expr, ast.ImportFrom),
                "unexpected top-level statement: %s" % type(node).__name__)


if __name__ == "__main__":
    unittest.main(verbosity=2)
