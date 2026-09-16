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


class Level4ProvesCounterfactualNotActualFill(unittest.TestCase):
    """MBO is dramatically stronger than L2 and is still not evidence about an
    order that was never submitted. Both halves pinned."""

    def test_the_counterfactual_terms_are_yes(self):
        L = V.LEVEL_4_MBO_WITHOUT_BETTOR_ORDER
        for f in ("TOUCH", "SPREAD", "DEPTH", "DEPTH_DEPLETION",
                  "QUEUE_AHEAD_AT_HYPOTHETICAL_ENTRY", "TRADE_AGGRESSOR",
                  "COUNTERFACTUAL_PASSIVE_FILL"):
            self.assertEqual(L[f], "YES", f)

    def test_the_actual_bettor_terms_are_no(self):
        """These three close only when BETTOR submits real passive orders.
        No feed, at any level, closes them."""
        L = V.LEVEL_4_MBO_WITHOUT_BETTOR_ORDER
        for f in ("ACTUAL_BETTOR_PASSIVE_FILL",
                  "ACTUAL_BETTOR_ORDER_ACCEPTANCE_LATENCY",
                  "ACTUAL_BETTOR_FILL_PROBABILITY"):
            self.assertEqual(L[f], "NO", f)

    def test_counterfactual_and_actual_are_different_fields(self):
        L = V.LEVEL_4_MBO_WITHOUT_BETTOR_ORDER
        self.assertNotEqual(L["COUNTERFACTUAL_PASSIVE_FILL"],
                            L["ACTUAL_BETTOR_PASSIVE_FILL"])
        self.assertNotEqual(V.FIX_MBO_GIVES, V.FIX_MBO_DOES_NOT_GIVE)

    def test_true_queue_position_carries_its_condition(self):
        """It holds only while the documented matching semantics are the ones
        in force; an unconditional YES would be a stronger claim than the
        documentation supports."""
        v = V.LEVEL_4_MBO_WITHOUT_BETTOR_ORDER[
            "TRUE_QUEUE_POSITION_FOR_HYPOTHETICAL_ORDER"]
        self.assertTrue(v.startswith("YES_CONDITIONAL_ON"), v)

    def test_the_micro_live_gate_is_recorded_and_not_authorized(self):
        self.assertTrue(V.MICRO_LIVE_REQUIRED_FOR_FINAL_EXECUTION_VALIDATION)
        self.assertFalse(V.MICRO_LIVE_AUTHORIZED)


class UnaggregatedGrpcIsNotLevel4(unittest.TestCase):

    def test_it_exists_and_its_semantics_do_not(self):
        self.assertEqual(V.GRPC_UNAGGREGATED_EXISTS, "YES")
        self.assertEqual(V.GRPC_UNAGGREGATED_EXACT_SEMANTICS, "NOT_IDENTIFIED")

    def test_no_documented_order_identity_or_timestamp(self):
        self.assertEqual(V.GRPC_UNAGGREGATED_HAS_ORDER_ID, "NO_DOCUMENTED_FIELD")
        self.assertEqual(V.GRPC_UNAGGREGATED_HAS_ORDER_TIMESTAMP,
                         "NO_DOCUMENTED_FIELD")
        self.assertEqual(V.GRPC_UNAGGREGATED_GIVES_TRUE_TIME_PRIORITY,
                         "NOT_ESTABLISHED")

    def test_the_adjective_raw_does_not_promote_it(self):
        """"Receive raw order book" is one adjective; LEVEL_4 needs OrderID and
        a per-order timestamp, and neither is a documented field."""
        self.assertFalse(V.GRPC_UNAGGREGATED_IS_LEVEL_4)
        self.assertFalse(V.GRPC_ORDER_IDENTITY_MAY_BE_INFERRED)

    def test_the_resolving_experiment_is_specified_before_the_credential(self):
        for f in ("NUMBER_OF_ENTRIES_PER_PRICE", "SUM_QTY_BY_PRICE",
                  "ORDERING_STABILITY", "ENTRY_CHURN", "UPDATE_FREQUENCY",
                  "TRANSACT_TIME", "RECONCILIATION_TO_AGGREGATED_BOOK"):
            self.assertIn(f, V.GRPC_UNAGGREGATED_EXPERIMENT)
        self.assertEqual(V.GRPC_UNAGGREGATED_ANSWER, "NOT_IDENTIFIED")


class ADocumentedCapabilityIsNotAGrant(unittest.TestCase):

    def test_the_scope_list_and_its_enforcement(self):
        for s in ("read:marketdata", "read:l2marketdata", "read:instruments",
                  "read:orders", "write:orders", "read:reports",
                  "read:positions", "read:dropcopy"):
            self.assertIn(s, V.DOCUMENTED_SCOPES, s)
        self.assertEqual(V.ORDER_SUBMISSION_REQUIRES, "write:orders")
        self.assertEqual(V.MARKET_DATA_STREAMING_REQUIRES, "read:marketdata")
        self.assertEqual(V.SCOPE_ENFORCEMENT, "STRICT_SERVER_SIDE")

    def test_what_the_venue_documents_is_not_what_bettor_holds(self):
        self.assertEqual(V.VENUE_ENFORCED_READ_ONLY_CAPABILITY, "DOCUMENTED")
        self.assertEqual(V.CAPABILITY_DOCUMENTED, "YES")
        self.assertEqual(V.BETTOR_GRANTED_SCOPE, "NOT_IDENTIFIED")
        self.assertEqual(V.BETTOR_CREDENTIAL_INSTALLED, "NO")
        self.assertEqual(V.BETTOR_CONNECTION_AUTHORIZED, "NO")

    def test_the_capability_never_stands_in_for_the_grant(self):
        self.assertNotEqual(V.CAPABILITY_DOCUMENTED, V.BETTOR_GRANTED_SCOPE)


class ThePublicTapeIsAFileNotAFeed(unittest.TestCase):
    """Captured from our own 340-page bytes, and it differs from the relay in
    three ways that each change the design."""

    def test_the_four_columns_and_the_three_absences(self):
        for f in ("TAPE_EXECUTION_TIMESTAMP", "TAPE_EXECUTION_PRICE",
                  "TAPE_EXECUTION_QUANTITY", "TAPE_SYMBOL"):
            self.assertEqual(getattr(V, f), "YES", f)
        for f in ("TAPE_TRADE_SIDE", "TAPE_AGGRESSOR",
                  "TAPE_PARTICIPANT_IDENTITY"):
            self.assertEqual(getattr(V, f), "NO", f)
        self.assertEqual(V.PUBLIC_TIME_SALES_AUTH_REQUIRED, "NO")

    def test_it_is_a_daily_file_and_not_a_realtime_endpoint(self):
        self.assertEqual(V.TAPE_DELIVERY, "DAILY_CSV_FILE_DOWNLOAD")
        self.assertFalse(V.TAPE_IS_AN_API)
        self.assertFalse(V.TAPE_IS_REALTIME)

    def test_the_url_may_not_be_built_from_the_filename_convention(self):
        """A convention is not an address. Guessing a path on a host we have
        never fetched turns a 404 into a false negative about the venue."""
        self.assertEqual(V.TAPE_DOWNLOAD_URL_DOCUMENTED, "NO")
        self.assertFalse(V.TAPE_URL_MAY_BE_CONSTRUCTED_FROM_CONVENTION)

    def test_the_business_date_is_not_a_utc_day(self):
        """C-6 in a new place: a 5pm-ET reporting day joined to UTC snapshots
        misaligns the tape by up to seven hours, invisibly."""
        self.assertEqual(V.TAPE_BUSINESS_DATE_CUTOVER_ET,
                         "17:00 America/New_York")
        self.assertFalse(V.TAPE_BUSINESS_DATE_IS_A_UTC_DAY)

    def test_the_three_open_questions_stay_open(self):
        for f in ("TAPE_TIMESTAMP_PRECISION", "TAPE_PUBLICATION_LATENCY",
                  "TAPE_SYMBOL_JOINS_TO_MARKET_SLUG"):
            self.assertEqual(getattr(V, f), "NOT_IDENTIFIED", f)
        self.assertTrue(V.TAPE_JOIN_IS_THE_SINGLE_POINT_OF_FAILURE)

    def test_the_tape_moves_no_actual_bettor_term(self):
        for f in ("ACTUAL_BETTOR_FILL",
                  "ACTUAL_BETTOR_FILL_PROBABILITY_FROM_TAPE",
                  "TRADE_AGGRESSOR_FROM_TAPE",
                  "ACTUAL_BETTOR_QUEUE_POSITION_FROM_TAPE"):
            self.assertEqual(getattr(V, f), "NOT_IDENTIFIED", f)


class PriceTimePriorityCarriesItsException(unittest.TestCase):

    def test_the_current_rule(self):
        self.assertEqual(V.CURRENT_MATCHING_PRIORITY, "PRICE_TIME")
        self.assertEqual(V.BETTER_PRICE_PRIORITY, "YES")
        self.assertEqual(V.SAME_PRICE_TIME_PRIORITY, "YES")

    def test_it_is_not_treated_as_universal_and_permanent(self):
        self.assertEqual(V.MATCHING_ALGORITHM_EXCEPTION_POSSIBLE, "YES")
        self.assertEqual(V.CONTRACT_SPECIFIC_ALGORITHM_OVERRIDE_POSSIBLE,
                         "YES")
        self.assertEqual(V.CHECK_PRODUCT_SPECIFIC_MATCHING_NOTICE, "REQUIRED")
        self.assertEqual(V.PRODUCT_NOTICE_CHECK_REQUIRED_BEFORE_LIVE, "YES")


class TheTargetSizeConflictIsPreserved(unittest.TestCase):
    """Two official pages describe the same parameter incompatibly. Silently
    rewriting one to match the other would erase a live risk to every
    depth-panel figure, because the readings disagree about which orders
    score at all."""

    def test_both_readings_are_kept_and_they_differ(self):
        self.assertEqual(V.TARGET_SIZE_GENERIC_PAGE, "MAXIMUM_DESCRIPTION")
        self.assertEqual(V.TARGET_SIZE_DETAILED_LIQUIDITY_PAGE,
                         "MINIMUM_AGGREGATE_THRESHOLD")
        self.assertNotEqual(V.TARGET_SIZE_GENERIC_PAGE,
                            V.TARGET_SIZE_DETAILED_LIQUIDITY_PAGE)

    def test_the_conflict_is_flagged_and_unresolved(self):
        self.assertEqual(V.DOC_CONFLICT_TARGET_SIZE, "YES")
        self.assertEqual(V.DOC_CONFLICT_TARGET_SIZE_RESOLVED_BY,
                         "NOT_IDENTIFIED")

    def test_we_implement_the_page_that_states_the_procedure(self):
        self.assertEqual(V.TARGET_SIZE_IMPLEMENTED_FROM,
                         "DETAILED_LIQUIDITY_PROGRAM_PAGE")

    def test_the_share_question_stays_unanswerable_from_public_data(self):
        self.assertEqual(V.ACTUAL_REWARD_SHARE, "NOT_IDENTIFIED")


class MassQuoteProtectionIsNotAnInventoryCap(unittest.TestCase):

    def test_the_breaching_execution_still_completes(self):
        """So one sweep can fill MORE than the threshold before any cancel."""
        self.assertFalse(V.MQP_IS_HARD_MAX_FILL_LIMIT)
        self.assertTrue(V.MQP_TRIGGERING_EXECUTION_COMPLETES)
        self.assertTrue(V.MQP_REMAINING_QUOTES_CANCEL_AFTER_TRIGGER)
        self.assertFalse(V.MQP_IS_SUFFICIENT_AS_SOLE_INVENTORY_CAP)

    def test_our_seven_own_controls_are_named(self):
        for c in ("POSITION_LIMITS", "EVENT_LIMITS", "CORRELATION_LIMITS",
                  "LOSS_LIMITS", "STALE_DATA_KILL", "QUOTE_AGE_LIMIT",
                  "INVENTORY_KILL"):
            self.assertIn(c, V.BETTOR_OWN_REQUIRED_CONTROLS, c)

    def test_post_only_is_required_and_nothing_is_submitted(self):
        self.assertEqual(V.MAKER_MODE_POST_ONLY_CONTROL, "REQUIRED")
        self.assertEqual(V.POST_ONLY_FIELD, "participateDontInitiate")
        self.assertTrue(V.TAKER_EXECUTION_REQUIRES_EXPLICIT_ENGINE_SELECTION)
        self.assertFalse(V.MICRO_LIVE_AUTHORIZED)


class TheSportsProbeIsSpecifiedNotAssumed(unittest.TestCase):

    def test_it_is_a_spec_and_measures_join_rates_only(self):
        self.assertEqual(V.SPORTS_COVERAGE_PROBE_STATUS,
                         "SPECIFIED_NOT_BUILT")
        for f in ("SPORT_JOIN_RATE", "PROVIDER_ID_JOIN_RATE",
                  "LIVE_STATE_JOIN_RATE", "SCORE_JOIN_RATE"):
            self.assertIn(f, V.SPORTS_COVERAGE_PROBE_MEASURES, f)

    def test_schema_presence_is_not_response_presence(self):
        """Documentation suggesting richer event data is not evidence that a
        field comes back. Runtime capture decides."""
        self.assertFalse(V.SPORTS_LIVE_STATE_FIELDS_ASSUMED_PRESENT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
