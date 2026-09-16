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
        """The retail family and the institutional family answer this question
        DIFFERENTLY, and the answer for one is not the answer for the other."""
        self.assertEqual(V.RETAIL_READ_ONLY_KEY_SCOPE_DOCUMENTED, "NO")
        self.assertEqual(V.RETAIL_API_KEY_READ_ONLY_SCOPE, "NOT_DOCUMENTED")
        self.assertEqual(V.INSTITUTIONAL_AUTH0_READ_ONLY_SCOPE, "DOCUMENTED")
        self.assertNotEqual(V.RETAIL_API_KEY_READ_ONLY_SCOPE,
                            V.INSTITUTIONAL_AUTH0_READ_ONLY_SCOPE)

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


class TheTwoAccessFamiliesAreNeverCollapsed(unittest.TestCase):
    """Averaging a DOCUMENTED capability with an undocumented one hides the
    documented one. That is what the old single field did."""

    def test_they_are_separate_and_differ(self):
        self.assertEqual(V.RETAIL_API_KEY_READ_ONLY_SCOPE, "NOT_DOCUMENTED")
        self.assertEqual(V.INSTITUTIONAL_AUTH0_READ_ONLY_SCOPE, "DOCUMENTED")
        self.assertNotEqual(V.RETAIL_API_KEY_READ_ONLY_SCOPE,
                            V.INSTITUTIONAL_AUTH0_READ_ONLY_SCOPE)

    def test_the_old_averaged_field_is_gone(self):
        self.assertFalse(hasattr(V, "READ_ONLY_CREDENTIAL_ISOLATION"))

    def test_the_retail_socket_stays_unusable_despite_its_value(self):
        self.assertEqual(V.RETAIL_MARKET_WS_DATA_VALUE, "VERY_HIGH")
        self.assertEqual(V.SAFE_TO_USE_RETAIL_TRADING_CAPABLE_KEY, "NO")
        self.assertEqual(V.RETAIL_READ_ONLY_KEY_SCOPE_DOCUMENTED, "NO")

    def test_the_retail_trade_fields_are_marked_relayed_not_captured(self):
        """The retail API pages are absent from the venue's own llms.txt index
        and from all 339 pages we fetched."""
        self.assertEqual(V.RETAIL_WS_TRADE_FIELDS_SOURCE,
                         "RELAYED_NOT_CAPTURED")


class TheGrpcPathAsCaptured(unittest.TestCase):

    def test_the_stream_needs_neither_participant_id_nor_kyc(self):
        self.assertEqual(V.MARKET_DATA_STREAM_REQUIRES_PARTICIPANT_ID, "NO")
        self.assertEqual(V.MARKET_DATA_STREAM_REQUIRES_KYC, "NO")

    def test_both_hosts_are_recorded_and_distinct(self):
        self.assertIn("preprod", V.GRPC_HOST_PREPROD)
        self.assertIn("prod", V.GRPC_HOST_PROD)
        self.assertNotEqual(V.GRPC_HOST_PREPROD, V.GRPC_HOST_PROD)
        self.assertTrue(V.GRPC_TLS_REQUIRED)

    def test_the_symbol_cap_and_the_all_instruments_option_are_unreconciled(self):
        """An empty symbol list is documented to subscribe to ALL instruments
        while a separate warning caps a stream at 1000. Whether empty bypasses
        or truncates is not stated, so it is not decided here."""
        self.assertEqual(V.SYMBOL_LIMIT_PER_STREAM, 1000)
        self.assertEqual(V.EMPTY_SYMBOL_LIST_VS_1000_CAP, "NOT_IDENTIFIED")

    def test_the_order_stopgap_is_not_a_market_data_latency_figure(self):
        """A 5-second stopgap on INBOUND ORDERS is the only latency number in
        339 pages. Reading it as a feed SLA would invent a measurement."""
        self.assertEqual(V.MARKET_DATA_LATENCY_SLA, "NOT_IDENTIFIED")
        self.assertIn("order path only", V.ORDER_LATENCY_STOPGAP)

    def test_the_premium_l2_scope_has_no_documented_price(self):
        self.assertEqual(V.L2_SCOPE_COMMERCIAL_TERMS, "NOT_IDENTIFIED")
        self.assertEqual(V.TIME_TO_OBTAIN_ACCESS, "NOT_IDENTIFIED")


class RfqExecutedIsNotAFill(unittest.TestCase):
    """Locked against the optimistic reading, from captured bytes."""

    def test_executed_does_not_mean_filled(self):
        self.assertFalse(V.RFQ_QUOTE_EXECUTED_EQUALS_FILL)
        self.assertEqual(V.COMBO_ATOMIC_FILL_GUARANTEE,
                         "NO_EVIDENCE_NOT_ESTABLISHED")

    def test_a_sequenced_submission_is_not_an_atom(self):
        """'Paired orders are submitted maker first, then requester.'"""
        self.assertTrue(V.COMBO_PAIRED_SUBMISSION_IS_SEQUENCED)

    def test_orphan_elimination_is_refused_as_an_assumption(self):
        self.assertFalse(V.COMBO_ELIMINATES_ORPHAN_RISK_AS_ASSUMPTION)

    def test_but_the_benefit_is_still_not_recorded_as_zero(self):
        """Refusing the ASSUMPTION is not the same as concluding the benefit
        is nil. Both errors are available and both are refused."""
        self.assertEqual(V.COMBO_ORPHAN_RISK_REDUCTION, "NOT_IDENTIFIED")


class TheMarketMakerProgramme(unittest.TestCase):

    def test_the_programme_exists_but_its_terms_do_not_transfer(self):
        self.assertEqual(V.NEGOTIATED_MM_PROGRAM_EXISTS, "VERIFIED")
        self.assertEqual(V.NEGOTIATED_MARKET_MAKER_TERMS, "NOT_IDENTIFIED")

    def test_public_incentives_are_not_the_ceiling(self):
        self.assertFalse(V.PUBLIC_INCENTIVES_ARE_ECONOMIC_CEILING)

    def test_five_reward_channels_all_separate(self):
        self.assertEqual(len(V.REWARD_CHANNELS), 5)
        self.assertEqual(len(set(V.REWARD_CHANNELS)), 5)
        self.assertIn("NEGOTIATED_MM_REWARD", V.REWARD_CHANNELS)
        for c in V.REWARD_CHANNELS[:4]:
            self.assertTrue(c.startswith("PUBLIC_"), c)


class ExchangeRiskControlsDoNotReplaceOurs(unittest.TestCase):

    def test_the_three_venue_controls_are_required(self):
        self.assertEqual(V.EXCHANGE_POST_ONLY, "REQUIRED_FOR_MAKER_MODE")
        self.assertEqual(V.SELF_MATCH_PREVENTION, "REQUIRED")
        self.assertEqual(V.MASS_QUOTE_PROTECTION, "REQUIRED_WHERE_AVAILABLE")

    def test_our_own_kill_switches_remain_an_independent_layer(self):
        """A control we do not operate is not a control we can trust to fire
        on our schedule."""
        self.assertTrue(V.OWN_KILL_SWITCHES_STILL_REQUIRED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
