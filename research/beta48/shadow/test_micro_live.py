#!/usr/bin/env python3
"""MICRO_LIVE_V1: the harness that must not be able to submit.

These tests are adversarial about one thing above all others. It is easy to
write an execution system that is safe because a flag says so, and that flag is
one careless edit from being true. So the tests below do not merely check that
LIVE_ORDER_SUBMISSION reads DISABLED; they check that there is no submit
function to call, no venue host in the module, no credential read, and no
import of anything that could place an order.

The second theme is fail-closed. Every gate is tested with its input MISSING,
not merely wrong, because the dangerous default is the one that treats silence
as consent.

Nothing here contacts a venue.
"""
import inspect
import unittest
from pathlib import Path

import micro_live as ML
import micro_live_ev as MEV
import micro_live_recon as MRC
import micro_live_rehearse as MRH
import micro_live_risk as MR
import micro_live_telemetry as MT

NOT_IDENTIFIED = "NOT_IDENTIFIED"
MODULES = (ML, MEV, MR, MRC, MT, MRH)


class ItCannotSubmit(unittest.TestCase):

    def test_the_mode_constants_are_what_they_claim(self):
        self.assertEqual(ML.MICRO_LIVE_MODE, "DRY_RUN_NO_SUBMIT")
        self.assertEqual(ML.LIVE_ORDER_SUBMISSION, "DISABLED")
        self.assertIs(ML.LIVE_EXECUTION_ENABLED, False)
        self.assertIs(ML.MIRROR_LIVE, False)
        self.assertEqual(ML.CREDENTIALS, "NONE")

    def test_there_is_no_submit_function_anywhere(self):
        """Not a disabled one. None."""
        for m in MODULES:
            for name in vars(m):
                self.assertNotIn(name.lower(),
                                 ("submit", "submit_order", "place_order",
                                  "send_order", "submit_fok"), m.__name__)

    def test_no_module_imports_anything_that_can_trade(self):
        forbidden = ("httpx", "requests", "websocket", "pmx", "live_executor",
                     "urllib.request", "boto3")
        for m in MODULES:
            src = Path(m.__file__).read_text()
            for f in forbidden:
                self.assertNotIn("import %s" % f, src,
                                 "%s imports %s" % (m.__name__, f))

    def test_no_venue_host_or_credential_appears(self):
        for m in MODULES:
            src = Path(m.__file__).read_text().lower()
            for bad in ("polymarket.us", "https://", "api_key", "private_key",
                        "secret", "bearer "):
                self.assertNotIn(bad, src, "%s contains %r" % (m.__name__, bad))


class TheLifecycle(unittest.TestCase):

    def life(self):
        return ML.Lifecycle("D-test", clock=lambda: "2026-09-17T11:00:00+00:00")

    def test_the_happy_path_ends_at_would_submit(self):
        L = self.life()
        for to in ("EV_EVALUATED", "ADMISSION_PASS", "ORDER_PROPOSED",
                   "RISK_APPROVED", "DRY_RUN_READY", "WOULD_SUBMIT"):
            L.transition(to, "test")
        self.assertEqual(L.state, "WOULD_SUBMIT")
        self.assertTrue(L.audit()["IS_TERMINAL"])

    def test_would_submit_has_no_successor_here(self):
        L = self.life()
        for to in ("EV_EVALUATED", "ADMISSION_PASS", "ORDER_PROPOSED",
                   "RISK_APPROVED", "DRY_RUN_READY", "WOULD_SUBMIT"):
            L.transition(to, "test")
        with self.assertRaises(ML.SubmissionBlocked):
            L.transition("SUBMITTED", "test")

    def test_every_live_state_is_defined_and_refused(self):
        for s in ML.LIVE_STATES:
            L = self.life()
            with self.assertRaises(ML.SubmissionBlocked, msg=s):
                L.transition(s, "test")

    def test_an_undefined_transition_is_refused(self):
        L = self.life()
        with self.assertRaises(ML.IllegalTransition):
            L.transition("RISK_APPROVED", "skipping the middle")

    def test_a_transition_without_a_reason_is_refused(self):
        L = self.life()
        with self.assertRaises(ML.IllegalTransition):
            L.transition("EV_EVALUATED", "")

    def test_every_transition_is_timestamped_and_explained(self):
        L = self.life()
        L.transition("EV_EVALUATED", "receipt built")
        a = L.audit()
        self.assertTrue(a["EVERY_TRANSITION_TIMESTAMPED"])
        self.assertTrue(a["EVERY_TRANSITION_HAS_A_REASON"])
        self.assertEqual(a["TRANSITIONS"][-1]["FROM"], "CANDIDATE")


class TheKillSwitchFailsClosed(unittest.TestCase):

    def test_the_default_is_blocked(self):
        r = ML.kill_switch()
        self.assertEqual(r["EXECUTION_PERMITTED"], "NO")
        self.assertTrue(r["BLOCKED"])

    def test_an_unevaluated_condition_blocks(self):
        """The hole this closes: a gate that only stops what it was asked."""
        partial = {"STALE_BOOK": False}
        r = ML.kill_switch(partial, live_execution_enabled=True)
        self.assertEqual(r["EXECUTION_PERMITTED"], "NO")
        self.assertIn("MISSING_PRICE", r["UNEVALUATED_CONDITIONS"])
        self.assertTrue(r["AN_UNEVALUATED_CONDITION_BLOCKS"])

    def test_every_named_condition_blocks_on_its_own(self):
        for c in ML.FAIL_CLOSED_CONDITIONS:
            conds = ML.all_conditions_clear()
            conds[c] = True
            r = ML.kill_switch(conds, live_execution_enabled=True)
            self.assertEqual(r["EXECUTION_PERMITTED"], "NO", c)
            self.assertIn(c, r["TRIPPED_CONDITIONS"])

    def test_all_clear_still_needs_live_execution_enabled(self):
        r = ML.kill_switch(ML.all_conditions_clear(),
                           live_execution_enabled=False)
        self.assertEqual(r["EXECUTION_PERMITTED"], "NO")

    def test_all_clear_and_enabled_is_the_only_yes(self):
        r = ML.kill_switch(ML.all_conditions_clear(),
                           live_execution_enabled=True)
        self.assertEqual(r["EXECUTION_PERMITTED"], "YES")

    def test_an_unrecognised_condition_blocks_rather_than_being_ignored(self):
        conds = ML.all_conditions_clear()
        conds["SOMETHING_NEW"] = False
        r = ML.kill_switch(conds, live_execution_enabled=True)
        self.assertEqual(r["EXECUTION_PERMITTED"], "NO")
        self.assertIn("SOMETHING_NEW", r["UNRECOGNISED_CONDITIONS"])


class Idempotency(unittest.TestCase):

    def test_the_same_decision_yields_the_same_id(self):
        a = ML.decision_id("E", "M", "BUY", "t")
        b = ML.decision_id("E", "M", "BUY", "t")
        self.assertEqual(a, b)

    def test_a_different_decision_yields_a_different_id(self):
        a = ML.decision_id("E", "M", "BUY", "t")
        b = ML.decision_id("E", "M", "SELL", "t")
        self.assertNotEqual(a, b)

    def test_a_repeated_intent_returns_the_existing_record(self):
        """The retry that once produced two resting orders."""
        reg = ML.IntentRegistry()
        iid = ML.order_intent_id("D-1", "0.50", "10", "MAKER_QUOTE")
        first = reg.register(iid, {"SLUG": "s"})
        second = reg.register(iid, {"SLUG": "s"})
        self.assertTrue(first["CREATED_NEW"])
        self.assertFalse(second["CREATED_NEW"])
        self.assertTrue(second["IDEMPOTENT_HIT"])
        self.assertEqual(len(reg), 1)

    def test_the_client_order_id_is_derived_so_the_venue_can_reject_a_dupe(self):
        iid = ML.order_intent_id("D-1", "0.50", "10", "MAKER_QUOTE")
        self.assertEqual(ML.client_order_id(iid), ML.client_order_id(iid))

    def test_all_four_id_kinds_exist(self):
        self.assertEqual(sorted(ML.ID_KINDS),
                         ["CLIENT_ORDER_ID", "DECISION_ID", "ORDER_INTENT_ID",
                          "POSITION_ID"])


class TheEvReceipt(unittest.TestCase):

    def full(self, **over):
        kw = dict(event_id="E", market_id="M", market_slug="s", side="BUY",
                  order_type="MAKER_QUOTE", limit_price="0.50", quantity="10",
                  book_timestamp="t", decision_timestamp="t2",
                  fair_value="0.55", fair_value_status="MODELLED",
                  p_fill_value="0.40",
                  p_fill_source="BETTOR_NATIVE_ADMITTED_FILLS",
                  p_fill_horizon="60", p_fill_status="ESTIMATED",
                  expected_fees="0.10", expected_rebates="0.02",
                  expected_adverse_selection="0.05",
                  expected_inventory_cost="0.01",
                  expected_capital_occupancy_cost="0.01",
                  no_fill_state="NO_EXPOSURE_CARRIED", ev_if_no_fill="0")
        kw.update(over)
        return MEV.receipt(**kw)

    def test_a_complete_receipt_can_decide_trade(self):
        r = self.full()
        self.assertEqual(r["DECISION"], MEV.TRADE)
        self.assertEqual(r["NOT_IDENTIFIED_TERMS"], [])

    def test_a_missing_ev_critical_term_forces_no_trade(self):
        for term, kw in (("FAIR_VALUE", {"fair_value": NOT_IDENTIFIED}),
                         ("EXPECTED_FEES", {"expected_fees": NOT_IDENTIFIED}),
                         ("P_FILL_VALUE", {"p_fill_value": NOT_IDENTIFIED}),
                         ("EV_IF_NO_FILL", {"ev_if_no_fill": NOT_IDENTIFIED})):
            r = self.full(**kw)
            self.assertEqual(r["DECISION"], MEV.NO_TRADE, term)
            self.assertIn(term, r["NOT_IDENTIFIED_TERMS"], term)

    def test_an_unknown_is_never_replaced_by_zero(self):
        r = self.full(expected_adverse_selection=NOT_IDENTIFIED)
        self.assertEqual(r["EV_IF_FILL"], NOT_IDENTIFIED)
        self.assertIn("EXPECTED_ADVERSE_SELECTION", r["UNCERTAINTY_TERMS"])
        self.assertIn("zero", r["UNKNOWNS_ARE_NOT_ZERO"])

    def test_a_whale_completion_rate_cannot_be_p_fill(self):
        """The forbidden shortcut, refused by the Day-1 guard itself."""
        r = self.full(p_fill_source="WHALE_COMPLETION_RATE")
        self.assertFalse(r["P_FILL_SOURCE_ACCEPTED"])
        self.assertEqual(r["DECISION"], MEV.NO_TRADE)

    def test_trading_and_incentive_ev_stay_separate(self):
        r = self.full()
        self.assertNotEqual(r["TRADING_EV_EX_INCENTIVES"], NOT_IDENTIFIED)
        self.assertNotEqual(r["INCENTIVE_EV"], NOT_IDENTIFIED)
        self.assertNotEqual(r["TRADING_EV_EX_INCENTIVES"], r["INCENTIVE_EV"])

    def test_the_no_fill_branch_is_declared_not_assumed_zero(self):
        r = self.full()
        self.assertNotEqual(r["NO_FILL_BRANCH"], NOT_IDENTIFIED)

    def test_every_required_receipt_field_is_present(self):
        r = self.full()
        for f in ("EVENT_ID", "MARKET_ID", "MARKET_SLUG", "SIDE", "ORDER_TYPE",
                  "LIMIT_PRICE", "QUANTITY", "NOTIONAL", "BOOK_TIMESTAMP",
                  "DECISION_TIMESTAMP", "FAIR_VALUE", "FAIR_VALUE_STATUS",
                  "PRICE_EDGE", "EV_MAKER", "EV_TAKER", "EV_NO_TRADE",
                  "P_FILL_SOURCE", "P_FILL_VALUE", "P_FILL_HORIZON",
                  "P_FILL_STATUS", "EV_IF_FILL", "EV_IF_NO_FILL",
                  "EXPECTED_FEES", "EXPECTED_REBATES",
                  "EXPECTED_ADVERSE_SELECTION", "EXPECTED_INVENTORY_COST",
                  "EXPECTED_CAPITAL_OCCUPANCY_COST",
                  "TRADING_EV_EX_INCENTIVES", "INCENTIVE_EV",
                  "TOTAL_EXPECTED_EV", "SELECTED_ACTION", "REJECTED_ACTIONS",
                  "WHY_SELECTED", "WHY_REJECTED"):
            self.assertIn(f, r, f)


class TheRiskGates(unittest.TestCase):

    def test_all_fifteen_gates_are_declared(self):
        self.assertEqual(len(MR.GATES), 15)
        for g in ("MAX_ORDER_NOTIONAL", "MAX_EVENT_EXPOSURE",
                  "MAX_MARKET_EXPOSURE", "MAX_TOTAL_OPEN_INVENTORY",
                  "MAX_ONE_SIDED_INVENTORY", "MAX_SIMULTANEOUS_POSITIONS",
                  "MAX_DAILY_LOSS", "MAX_SESSION_LOSS",
                  "MAX_UNREALIZED_DRAWDOWN", "MAX_INVENTORY_AGE",
                  "MAX_SLIPPAGE", "MAX_BOOK_STALENESS", "MIN_EXPECTED_EV",
                  "MIN_EV_MARGIN_OVER_UNCERTAINTY", "MIN_LIQUIDITY_DEPTH"):
            self.assertIn(g, MR.GATES, g)

    def test_every_limit_starts_unset_and_unset_blocks(self):
        r = MR.evaluate(MR.unset_limits(), {})
        self.assertEqual(r["RISK_VERDICT"], "RISK_REJECTED")
        self.assertEqual(len(r["LIMITS_NOT_SET"]), 15)
        self.assertEqual(r["LIVE_SUBMISSION"], "BLOCKED")

    def test_an_authorized_limit_with_no_observation_still_blocks(self):
        lim = MR.unset_limits()
        lim["MAX_ORDER_NOTIONAL"] = {"VALUE": "5", "UNIT": "USD",
                                     "BOUNDS": "x",
                                     "AUTHORIZATION_STATUS": "AUTHORIZED"}
        r = MR.evaluate(lim, {})
        self.assertIn("MAX_ORDER_NOTIONAL", r["NOT_EVALUATED"])
        self.assertTrue(r["AN_UNEVALUATED_GATE_BLOCKS"])

    def test_a_breach_is_reported_as_a_breach(self):
        lim = MR.unset_limits()
        lim["MAX_ORDER_NOTIONAL"] = {"VALUE": "5", "UNIT": "USD",
                                     "BOUNDS": "x",
                                     "AUTHORIZATION_STATUS": "AUTHORIZED"}
        r = MR.evaluate(lim, {"MAX_ORDER_NOTIONAL": "6"})
        self.assertIn("MAX_ORDER_NOTIONAL", r["BREACHED"])

    def test_a_minimum_breaches_downward(self):
        lim = MR.unset_limits()
        lim["MIN_EXPECTED_EV"] = {"VALUE": "0.10", "UNIT": "USD",
                                  "BOUNDS": "x",
                                  "AUTHORIZATION_STATUS": "AUTHORIZED"}
        r = MR.evaluate(lim, {"MIN_EXPECTED_EV": "0.01"})
        self.assertIn("MIN_EXPECTED_EV", r["BREACHED"])

    def test_the_hypothetical_view_authorizes_nothing(self):
        hyp = {g: "1000000" for g in MR.GATES}
        obs = {g: "1" for g in MR.GATES}
        r = MR.would_pass_if_authorized(MR.unset_limits(), obs, hyp)
        self.assertTrue(r["THIS_IS_HYPOTHETICAL"])
        self.assertEqual(r["REAL_AUTHORIZATION_STATUS"], "NOT_SET")
        self.assertEqual(r["LIVE_SUBMISSION"], "BLOCKED")

    def test_passing_every_gate_never_unblocks_submission(self):
        hyp = {g: "1000000" for g in MR.GATES if g.startswith("MAX_")}
        hyp.update({g: "0" for g in MR.GATES if g.startswith("MIN_")})
        obs = {g: "1" for g in MR.GATES}
        r = MR.would_pass_if_authorized(MR.unset_limits(), obs, hyp)
        self.assertEqual(r["RISK_VERDICT"], "RISK_APPROVED")
        self.assertEqual(r["LIVE_SUBMISSION"], "BLOCKED")


class Reconciliation(unittest.TestCase):

    def test_a_missing_acknowledgement_freezes(self):
        r = MRC.reconcile(intent={"QUANTITY": "10", "CLIENT_ORDER_ID": "C"},
                          ack=None)
        self.assertIn("MISSING_ACKNOWLEDGEMENT", r["FREEZING_FINDINGS"])
        self.assertTrue(r["EXECUTION_FROZEN"])

    def test_a_duplicate_fill_is_caught_by_id(self):
        r = MRC.reconcile(intent={"QUANTITY": "10", "CLIENT_ORDER_ID": "C"},
                          ack={"ACKNOWLEDGED_SIZE": "10"},
                          fills=[{"id": "f1", "qty": "5"},
                                 {"id": "f1", "qty": "5"}])
        self.assertIn("DUPLICATE_FILL", r["FREEZING_FINDINGS"])

    def test_an_overfill_freezes(self):
        r = MRC.reconcile(intent={"QUANTITY": "10", "CLIENT_ORDER_ID": "C"},
                          ack={"ACKNOWLEDGED_SIZE": "10"},
                          fills=[{"id": "f1", "qty": "12"}])
        self.assertIn("OVERFILL", r["FREEZING_FINDINGS"])

    def test_a_partial_fill_is_reported_and_does_not_freeze(self):
        r = MRC.reconcile(intent={"QUANTITY": "10", "CLIENT_ORDER_ID": "C"},
                          ack={"ACKNOWLEDGED_SIZE": "10"},
                          fills=[{"id": "f1", "qty": "4"}],
                          ledger={"POSITION": "4"})
        self.assertEqual([f["CLASS"] for f in r["FINDINGS"]], ["PARTIAL_FILL"])
        self.assertFalse(r["EXECUTION_FROZEN"])

    def test_a_late_fill_after_cancel_freezes(self):
        r = MRC.reconcile(intent={"QUANTITY": "10", "CLIENT_ORDER_ID": "C"},
                          ack={"ACKNOWLEDGED_SIZE": "10"},
                          fills=[{"id": "f1", "qty": "10",
                                  "at": "2026-09-17T11:00:05+00:00"}],
                          ledger={"POSITION": "10"},
                          cancel_requested_at="2026-09-17T11:00:00+00:00")
        self.assertIn("LATE_FILL_AFTER_CANCEL_REQUEST", r["FREEZING_FINDINGS"])

    def test_a_ledger_disagreement_freezes(self):
        r = MRC.reconcile(intent={"QUANTITY": "10", "CLIENT_ORDER_ID": "C"},
                          ack={"ACKNOWLEDGED_SIZE": "10"},
                          fills=[{"id": "f1", "qty": "10"}],
                          ledger={"POSITION": "3"})
        self.assertIn("POSITION_MISMATCH", r["FREEZING_FINDINGS"])

    def test_an_unexpected_fill_with_no_intent_freezes(self):
        r = MRC.reconcile(intent=None, fills=[{"id": "f1", "qty": "3"}])
        self.assertIn("UNEXPECTED_FILL", r["FREEZING_FINDINGS"])

    def test_every_mismatch_class_is_declared(self):
        self.assertEqual(len(MRC.MISMATCH_CLASSES), 8)

    def test_a_clean_reconciliation_is_not_permission(self):
        r = MRC.reconcile(intent={"QUANTITY": "10", "CLIENT_ORDER_ID": "C"},
                          ack={"ACKNOWLEDGED_SIZE": "10"},
                          fills=[{"id": "f1", "qty": "10"}],
                          ledger={"POSITION": "10"})
        self.assertTrue(r["RECONCILED"])
        self.assertIn("does not say an order may be sent",
                      r["RECONCILIATION_IS_NOT_PERMISSION"])


class Telemetry(unittest.TestCase):

    def test_every_declared_field_exists_and_starts_absent(self):
        b = MT.blank()
        for f in MT.ALL_FIELDS:
            self.assertEqual(b[f], NOT_IDENTIFIED, f)

    def test_the_markout_horizons_are_declared_in_advance(self):
        self.assertEqual(list(MT.MARKOUT_HORIZONS_S), [5, 30, 60, 300])
        for f in ("MARKOUT_5S", "MARKOUT_30S", "MARKOUT_60S", "MARKOUT_300S"):
            self.assertIn(f, MT.ALL_FIELDS)

    def test_incomplete_telemetry_blocks_further_execution(self):
        c = MT.completeness(MT.blank())
        self.assertTrue(c["TELEMETRY_FAILURE"])
        self.assertTrue(c["BLOCKS_FURTHER_EXECUTION"])

    def test_the_comparison_refuses_when_a_side_is_missing(self):
        c = MT.comparison(MT.blank())
        self.assertEqual(c["COMPARISON_STATUS"], "NOT_AVAILABLE")
        self.assertEqual(c["PREDICTION_ERROR"], NOT_IDENTIFIED)

    def test_one_observation_is_never_a_result(self):
        row = MT.blank()
        row["PREDICTED_EV_AT_SEND"] = "0.10"
        row["REALIZED_TOTAL_PNL"] = "0.30"
        c = MT.comparison(row)
        self.assertEqual(c["COMPARISON_STATUS"], "AVAILABLE")
        self.assertAlmostEqual(c["PREDICTION_ERROR"], 0.20, places=9)
        self.assertTrue(c["ONE_OBSERVATION_IS_NOT_A_RESULT"])
        self.assertEqual(c["REALIZED_MAKER_ECONOMICS"], "NOT_ESTABLISHED")


class TheRehearsal(unittest.TestCase):

    def rows(self, n=12):
        return [{"kind": "TICK", "MARKET_SLUG": "m%d" % i,
                 "EVENT_ID": "EV%d" % (i // 2), "MARKET_ID": "id%d" % i,
                 "RECEIPT_UTC": "2026-09-17T11:00:00+00:00",
                 "BID": "0.50", "ASK": "0.52", "BID_QTY": "10",
                 "ASK_QTY": "10"} for i in range(n)]

    def test_ten_candidates_rehearse_end_to_end(self):
        r = MRH.rehearse(self.rows(), limit=10)
        self.assertEqual(r["CANDIDATES_TESTED"], 10)
        self.assertEqual(len(r["RESULTS"]), 10)

    def test_no_order_is_ever_sent(self):
        r = MRH.rehearse(self.rows(), limit=10)
        self.assertEqual(r["ORDERS_SENT"], 0)
        for x in r["RESULTS"]:
            self.assertFalse(x["ORDER_SENT"])
            self.assertIn(x["VERDICT"], ("WOULD_SUBMIT", "WOULD_NOT_SUBMIT"))

    def test_every_candidate_carries_both_receipts_and_an_audit(self):
        r = MRH.rehearse(self.rows(), limit=10)
        for x in r["RESULTS"]:
            self.assertIn("EV_RECEIPT", x)
            self.assertIn("RISK_RECEIPT", x)
            self.assertIn("LIFECYCLE", x)
            self.assertTrue(x["LIFECYCLE"]["EVERY_TRANSITION_TIMESTAMPED"])

    def test_size_is_not_authorized(self):
        r = MRH.rehearse(self.rows(), limit=10)
        for x in r["RESULTS"]:
            self.assertEqual(x["PROPOSED_SIZE"], "SIZE_NOT_AUTHORIZED")

    def test_the_quote_is_passive_and_never_crosses(self):
        r = MRH.rehearse(self.rows(), limit=3)
        for x in r["RESULTS"]:
            self.assertTrue(x["PASSIVE_ONLY"])
            self.assertEqual(x["ORDER_TYPE"], "MAKER_QUOTE")
            if x["PROPOSED_PRICE"] != NOT_IDENTIFIED:
                self.assertLess(float(x["PROPOSED_PRICE"]), 0.52)

    def test_todays_rehearsal_correctly_refuses_every_candidate(self):
        """No fair value, no admitted-fill P_FILL: NO_TRADE is the honest
        answer, and a TRADE here would be an invented number."""
        r = MRH.rehearse(self.rows(), limit=10)
        self.assertEqual(r["TRADE_DECISIONS"], 0)
        self.assertEqual(r["NO_TRADE_DECISIONS"], 10)
        self.assertEqual(r["WOULD_SUBMIT"], 0)

    def test_the_kill_switch_is_reported_and_blocking(self):
        r = MRH.rehearse(self.rows(), limit=2)
        self.assertEqual(r["KILL_SWITCH"]["EXECUTION_PERMITTED"], "NO")

    def test_candidates_are_taken_at_first_sight_not_best_spread(self):
        rows = self.rows(3)
        rows[1]["BID"], rows[1]["ASK"] = "0.10", "0.90"   # a flattering spread
        src = inspect.getsource(MRH.candidates_from_rows)
        self.assertIn("NEVER BY SPREAD", src)


if __name__ == "__main__":
    unittest.main()
