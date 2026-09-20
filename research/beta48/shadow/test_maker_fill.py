#!/usr/bin/env python3
"""The counterfactual maker-fill model. A touch is never a fill, structurally."""
import ast
import unittest
from decimal import Decimal as D
from pathlib import Path

import maker_fill as MF

SRC = Path(__file__).resolve().parent / "maker_fill.py"
TREE = ast.parse(SRC.read_text())


def tick(seq, bid="0.54", ask="0.56", bid_qty="100", ask_qty="80",
         traded="1000", delta="0", slug="m", elapsed=None, levels=None):
    e = float(seq * 3) if elapsed is None else elapsed
    row = {
        "kind": "TICK", "slug": slug, "seq": seq, "ELAPSED_S": e,
        "RECEIPT_UTC": "2026-09-16T16:%02d:00Z" % (seq % 60),
        "BID": bid, "ASK": ask, "BID_QTY": bid_qty, "ASK_QTY": ask_qty,
        "SPREAD": str(D(ask) - D(bid)),
        "BID_LADDER": levels or [[bid, bid_qty], ["0.53", "200"]],
        "ASK_LADDER": [[ask, ask_qty], ["0.57", "150"]],
        "SHARES_TRADED": traded, "DEPTH_LEVELS_BID": 2,
        "DEPTH_LEVELS_ASK": 2, "STATE": "OPEN",
    }
    if seq > 0:
        row["SHARES_TRADED_DELTA"] = delta
        row["TRADE_OCCURRED"] = D(delta) > 0 if delta != MF.NOT_IDENTIFIED \
            else MF.NOT_IDENTIFIED
    return row


def window(deltas, bid="0.54", ask="0.56", slug="m"):
    return [tick(i + 1, bid=bid, ask=ask, delta=d, slug=slug)
            for i, d in enumerate(deltas)]


class TheModuleContactsNothing(unittest.TestCase):
    """Proved from the AST, not from a substring scan of the prose."""

    def test_no_http_client_is_imported(self):
        bad = {"httpx", "requests", "urllib", "urllib3", "http", "socket",
               "aiohttp"}
        for n in ast.walk(TREE):
            if isinstance(n, ast.Import):
                for a in n.names:
                    self.assertNotIn(a.name.split(".")[0], bad, a.name)
            if isinstance(n, ast.ImportFrom) and n.module:
                self.assertNotIn(n.module.split(".")[0], bad, n.module)

    def test_no_url_literal_anywhere(self):
        for n in ast.walk(TREE):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                self.assertNotIn("://", n.value)


class TicksAloneNeverSupportAFill(unittest.TestCase):
    """The identification result, proved structurally rather than promised."""

    def _fn(self, name):
        for n in ast.walk(TREE):
            if isinstance(n, ast.FunctionDef) and n.name == name:
                return n
        raise AssertionError("no such function: %s" % name)

    def test_fill_status_has_no_branch_that_returns_a_fill(self):
        fn = self._fn("fill_status")
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        for forbidden in ("COUNTERFACTUAL_FILL_F0", "COUNTERFACTUAL_FILL_F1",
                          "COUNTERFACTUAL_FILL_F2", "STATUS_FOR_MODEL"):
            self.assertNotIn(forbidden, names)

    def test_only_with_tape_reads_the_positive_verdict(self):
        fn = self._fn("with_tape")
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        self.assertIn("STATUS_FOR_MODEL", names)

    def test_the_flag_says_so_too(self):
        self.assertFalse(MF.TICKS_ALONE_SUPPORT_POSITIVE_FILL)
        self.assertTrue(MF.TICKS_ALONE_SUPPORT_REFUTATION)
        self.assertEqual(MF.POSITIVE_FILL_SUPPORT_REQUIRES,
                         "EXECUTION_TAPE_JOIN")


class TheQuote(unittest.TestCase):

    def test_resting_at_the_touch_joins_the_displayed_queue(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        self.assertEqual(q["QUOTE_PRICE"], D("0.54"))
        self.assertEqual(q["QUEUE_AHEAD_ESTIMATE"], D("100"))
        self.assertTrue(q["QUEUE_AHEAD_IS_A_LOWER_BOUND"])
        self.assertFalse(q["ORDER_WAS_SUBMITTED"])

    def test_price_improvement_puts_nothing_ahead_of_us(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10", improve_ticks=1)
        self.assertEqual(q["QUOTE_PRICE"], D("0.55"))
        self.assertEqual(q["QUEUE_AHEAD_ESTIMATE"], D("0"))
        self.assertTrue(q["PRICE_IMPROVED"])

    def test_an_ask_improvement_moves_down_not_up(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_ASK, "10", improve_ticks=1)
        self.assertEqual(q["QUOTE_PRICE"], D("0.55"))

    def test_a_float_size_is_refused_never_coerced(self):
        with self.assertRaises(TypeError):
            MF.hypothetical_quote(tick(0), MF.SIDE_BID, 10.0)

    def test_a_price_off_the_captured_ladder_is_unknown_not_zero(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10", price="0.40")
        self.assertEqual(q["QUEUE_AHEAD_ESTIMATE"], MF.NOT_IDENTIFIED)

    def test_a_bad_side_raises(self):
        with self.assertRaises(ValueError):
            MF.hypothetical_quote(tick(0), "BUY", "10")


class TheWalk(unittest.TestCase):

    def test_the_whole_market_is_credited_to_our_level(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        w = MF.walk_after_entry(q, window(["5", "7", "0"]))
        self.assertEqual(w["MAXIMAL_ATTRIBUTION_AT_OUR_PRICE"], D("12"))
        self.assertEqual(w["VOLUME_AT_OUR_PRICE"], MF.NOT_IDENTIFIED)

    def test_one_unreadable_delta_makes_the_window_unreadable(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        w = MF.walk_after_entry(q, window(["5", MF.NOT_IDENTIFIED, "3"]))
        self.assertEqual(w["MAXIMAL_ATTRIBUTION_AT_OUR_PRICE"],
                         MF.NOT_IDENTIFIED)

    def test_a_hole_is_not_treated_as_zero(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        w = MF.walk_after_entry(q, window([MF.NOT_IDENTIFIED]))
        self.assertNotEqual(w["MAXIMAL_ATTRIBUTION_AT_OUR_PRICE"], D("0"))

    def test_rows_for_another_slug_are_dropped(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        w = MF.walk_after_entry(q, window(["5"], slug="other"))
        self.assertEqual(w["TICKS_IN_WINDOW"], 0)

    def test_the_horizon_bounds_the_window(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        rows = window(["1", "1", "1", "1", "1"])
        self.assertEqual(
            MF.walk_after_entry(q, rows, horizon_s=6)["TICKS_IN_WINDOW"], 2)

    def test_an_ask_coming_down_to_our_bid_is_a_touch_only(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        rows = window(["500"])
        rows[0]["ASK"] = "0.54"
        w = MF.walk_after_entry(q, rows)
        self.assertTrue(w["TOUCHED_OUR_PRICE"])
        self.assertTrue(w["TOUCH_IS_NOT_A_FILL"])
        self.assertEqual(MF.fill_status(q, w)["FILL_STATUS"], MF.UNKNOWN)

    def test_a_touch_with_no_volume_is_still_not_a_fill(self):
        # The market came to our price and the volume field did not move. The
        # touch is evidence of NOTHING -- and neither is the still field, until
        # its semantics are known. So: UNKNOWN, with the bound named.
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        rows = window(["0"])
        rows[0]["ASK"] = "0.54"
        w = MF.walk_after_entry(q, rows)
        r = MF.fill_status(q, w)
        self.assertTrue(w["TOUCHED_OUR_PRICE"])
        self.assertEqual(r["FILL_STATUS"], MF.UNKNOWN)
        self.assertFalse(MF.is_a_fill(r))

    def test_excursions_are_signed_for_the_side_we_would_hold(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        rows = window(["1", "1"])
        rows[0].update(BID="0.50", ASK="0.52")     # mid 0.51, adverse for long
        rows[1].update(BID="0.60", ASK="0.62")     # mid 0.61, favourable
        w = MF.walk_after_entry(q, rows)
        self.assertEqual(w["MAX_ADVERSE_EXCURSION"], D("-0.03"))
        self.assertEqual(w["MAX_FAVORABLE_EXCURSION"], D("0.07"))


# What `tick_semantics` would need to be told before a volume bound may carry a
# verdict. NONE of this is established for this venue -- these fixtures exist to
# exercise the arithmetic behind the gate, not to assert it is open.
SEM_OK = {"CUMULATIVE_OR_INTERVAL": "CUMULATIVE",
          "MARKET_WIDE_OR_SIDE_SPECIFIC": "MARKET_WIDE",
          "RESET_BOUNDARY": "KNOWN_AND_AVOIDABLE_IN_WINDOW"}
RUN_OK = {"MONOTONIC_WITHIN_MARKET": True, "RESET_EVENTS_OBSERVED": 0,
          "NEGATIVE_DELTAS_OBSERVED": 0}


class TheVolumeBoundIsGated(unittest.TestCase):
    """SHARES_TRADED may not carry a hard refutation on an unproven field."""

    def _status(self, deltas, size="10", model="F1", improve=None, **kw):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, size,
                                  improve_ticks=improve)
        w = MF.walk_after_entry(q, window(deltas))
        return MF.fill_status(q, w, model=model, **kw)

    def test_by_default_a_failing_bound_is_not_a_refutation(self):
        r = self._status(["50", "40"])          # 90 < 100 ahead
        self.assertTrue(r["AGGREGATE_VOLUME_BOUND_ARITHMETIC"])
        self.assertEqual(r["FILL_STATUS"], MF.UNKNOWN)
        self.assertEqual(r["FILL_REFUTATION_STATUS"], MF.NOT_IDENTIFIED)
        self.assertEqual(r["WHY"],
                         "AGGREGATE_VOLUME_UPPER_BOUND_INSUFFICIENT")
        self.assertFalse(r["EQUIVALENT_TO_EXECUTION_EVIDENCE"])

    def test_a_still_field_is_not_a_quiet_market(self):
        """The dangerous case: a lazy feed would refute every model."""
        for m in MF.MODELS:
            r = self._status(["0", "0"], model=m)
            self.assertEqual(r["FILL_STATUS"], MF.UNKNOWN, m)
            self.assertEqual(r["FILL_REFUTATION_STATUS"], MF.NOT_IDENTIFIED)

    def test_runtime_evidence_alone_does_not_open_the_gate(self):
        r = self._status(["50"], runtime=RUN_OK)
        self.assertEqual(r["FILL_STATUS"], MF.UNKNOWN)
        self.assertIn("CUMULATIVE_OR_INTERVAL", r["SEMANTICS_MISSING"])

    def test_venue_semantics_alone_do_not_open_it_either(self):
        r = self._status(["50"], semantics=SEM_OK)
        self.assertEqual(r["FILL_STATUS"], MF.UNKNOWN)

    def test_a_misbehaving_capture_keeps_it_shut(self):
        bad = dict(RUN_OK, RESET_EVENTS_OBSERVED=1)
        r = self._status(["50"], semantics=SEM_OK, runtime=bad)
        self.assertEqual(r["FILL_STATUS"], MF.UNKNOWN)

    def test_proven_not_filled_is_not_an_output_of_this_programme(self):
        import tick_semantics as TS
        self.assertEqual(TS.PROVEN_NOT_FILLED,
                         "REFUSED_NOT_AN_OUTPUT_OF_THIS_PROGRAMME")
        self.assertNotIn("PROVEN_NOT_FILLED", MF.FILL_STATUSES)


class TheArithmeticBehindTheGate(unittest.TestCase):
    """Exercised with both halves supplied, so the bound itself stays pinned."""

    def _status(self, deltas, size="10", model="F1", improve=None):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, size,
                                  improve_ticks=improve)
        w = MF.walk_after_entry(q, window(deltas))
        return MF.fill_status(q, w, model=model, semantics=SEM_OK,
                              runtime=RUN_OK)

    def test_no_volume_at_all_refutes_every_model(self):
        for m in MF.MODELS:
            r = self._status(["0", "0"], model=m)
            self.assertEqual(r["FILL_STATUS"], MF.NOT_FILLED, m)
            self.assertEqual(r["WHY"], "NO_VOLUME_TRADED_IN_WINDOW")

    def test_volume_below_the_queue_does_not_refute_f1(self):
        """THE CORRECTED DEFECT. This used to assert NOT_FILLED.

        `maximal <= ahead` compares window volume against queue-ahead AT
        INSERT. A shortfall is not proof we were never reached: 400 of
        the 500 ahead may have CANCELLED, leaving 100 for 200 of volume
        to clear. Ticks cannot separate a cancellation from a trade, so
        the gate stays shut even with the volume field's semantics
        established.
        """
        r = self._status(["50", "40"])          # 90 < 100 displayed at T0
        self.assertEqual(r["FILL_STATUS"], MF.UNKNOWN)
        self.assertEqual(r["WHY"], MF.QUEUE_EVOLUTION_REFUTATION_INSUFFICIENT)
        self.assertEqual(r["QUEUE_DEPLETION_FROM_CANCELLATIONS"],
                         MF.NOT_IDENTIFIED)
        self.assertTrue(MF.QUEUE_AHEAD_AT_T0_IS_NOT_A_BOUND_ON_THE_INTERVAL)

    def test_the_only_surviving_tick_refutation_is_no_volume_at_all(self):
        """Cancellations ahead do not fill anybody, so a window with no
        trades refutes at every queue position. That one survives."""
        r = self._status(["0", "0"])
        self.assertEqual(r["FILL_STATUS"], MF.NOT_FILLED)
        self.assertEqual(r["WHY"], "NO_VOLUME_TRADED_IN_WINDOW")

    def test_even_then_it_is_not_execution_evidence(self):
        r = self._status(["50"])
        self.assertFalse(r["EQUIVALENT_TO_EXECUTION_EVIDENCE"])

    def test_volume_above_the_queue_is_unknown_not_a_fill(self):
        r = self._status(["500"])
        self.assertEqual(r["FILL_STATUS"], MF.UNKNOWN)
        self.assertEqual(r["WHY"], "NO_PER_PRICE_ATTRIBUTION_WITHOUT_TAPE")
        self.assertFalse(MF.is_a_fill(r))

    def test_f1_refuted_implies_f0_refuted(self):
        for deltas in (["0"], ["10"], ["99"], ["100"]):
            f1 = self._status(deltas, model="F1")
            f0 = self._status(deltas, model="F0")
            if f1["FILL_STATUS"] == MF.NOT_FILLED:
                self.assertEqual(f0["FILL_STATUS"], MF.NOT_FILLED, deltas)

    def test_the_f0_f1_arithmetic_gap_survives_but_no_longer_refutes(self):
        """105 clears a T0 queue of 100 but not that queue PLUS our 10, so
        the ARITHMETIC still separates the models. Neither reaches a
        verdict: both are queue-based and queue evolution is unobserved."""
        f1 = self._status(["105"], model="F1")
        f0 = self._status(["105"], model="F0")
        self.assertFalse(f1["AGGREGATE_VOLUME_BOUND_ARITHMETIC"])
        self.assertTrue(f0["AGGREGATE_VOLUME_BOUND_ARITHMETIC"])
        self.assertEqual(f1["FILL_STATUS"], MF.UNKNOWN)
        self.assertEqual(f0["FILL_STATUS"], MF.UNKNOWN)
        self.assertEqual(f0["WHY"], MF.QUEUE_EVOLUTION_REFUTATION_INSUFFICIENT)

    def test_f2_is_not_refuted_by_the_queue_because_cancels_clear_it(self):
        r = self._status(["50"], model="F2")
        self.assertEqual(r["FILL_STATUS"], MF.UNKNOWN)
        self.assertFalse(r["AGGREGATE_VOLUME_BOUND_ARITHMETIC"])

    def test_an_unreadable_window_is_unknown_not_refuted(self):
        r = self._status([MF.NOT_IDENTIFIED])
        self.assertEqual(r["FILL_STATUS"], MF.UNKNOWN)
        self.assertEqual(r["WHY"], "WINDOW_UNREADABLE")

    def test_price_improvement_gives_up_the_only_leverage_ticks_have(self):
        # Nothing ahead of us, so only "no volume at all" can refute.
        self.assertEqual(self._status(["1"], improve=1)["FILL_STATUS"],
                         MF.UNKNOWN)
        self.assertEqual(self._status(["0"], improve=1)["FILL_STATUS"],
                         MF.NOT_FILLED)

    def test_an_unknown_model_raises(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        w = MF.walk_after_entry(q, window(["1"]))
        with self.assertRaises(ValueError):
            MF.fill_status(q, w, model="F9")


class TheThreeEventsAreDistinct(unittest.TestCase):

    def test_the_ladder_is_ordered_and_each_rung_requires_more(self):
        self.assertEqual(MF.EVENT_LADDER,
                         (MF.TOUCH, MF.TRADE_EVIDENCE, MF.COUNTERFACTUAL_FILL))
        self.assertTrue(MF.TOUCH_IS_NOT_TRADE_EVIDENCE)
        self.assertTrue(MF.TRADE_EVIDENCE_IS_NOT_A_COUNTERFACTUAL_FILL)
        self.assertEqual(len({MF.TOUCH, MF.TRADE_EVIDENCE,
                              MF.COUNTERFACTUAL_FILL}), 3)

    def test_ticks_give_a_touch_and_nothing_above_it(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        rows = window(["500"])
        rows[0]["ASK"] = "0.54"
        r = MF.fill_status(q, MF.walk_after_entry(q, rows))
        self.assertTrue(r["TOUCH"])
        self.assertEqual(r["TRADE_EVIDENCE"], MF.NOT_IDENTIFIED)
        self.assertEqual(r["COUNTERFACTUAL_FILL"], MF.NOT_IDENTIFIED)

    def test_a_tape_can_give_trade_evidence_without_a_fill(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        q["QUEUE_AHEAD_ESTIMATE"] = D("1000")
        q["QUOTE_TIME"] = 0
        r = MF.with_tape(q, [clob(1, "0.54", "50")], model="F1")
        self.assertTrue(r["TRADE_EVIDENCE"])      # somebody traded at our price
        self.assertFalse(r["COUNTERFACTUAL_FILL"])   # the queue was too long
        self.assertEqual(r["FILL_STATUS"], MF.NOT_FILLED)

    def test_a_block_at_our_price_is_not_trade_evidence(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        q["QUEUE_AHEAD_ESTIMATE"] = D("0")
        q["QUOTE_TIME"] = 0
        row = clob(1, "0.54", "500")
        row["execution_type"] = "BLOCK_EXECUTION"
        r = MF.with_tape(q, [row], model="F1")
        self.assertFalse(r["TRADE_EVIDENCE"])
        self.assertTrue(MF.BLOCK_IS_NOT_TRADE_EVIDENCE)

    def test_an_untyped_print_is_not_trade_evidence(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        q["QUEUE_AHEAD_ESTIMATE"] = D("0")
        q["QUOTE_TIME"] = 0
        r = MF.with_tape(q, [{"time": 1, "symbol": "m", "price": "0.54",
                              "qty": "500"}], model="F1")
        self.assertFalse(r["TRADE_EVIDENCE"])
        self.assertTrue(MF.UNKNOWN_EXECUTION_TYPE_IS_NOT_TRADE_EVIDENCE)

    def test_the_summary_counts_all_three_separately(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        q["QUEUE_AHEAD_ESTIMATE"] = D("1000")
        q["QUOTE_TIME"] = 0
        s = MF.summarise_fills([MF.with_tape(q, [clob(1, "0.54", "50")])])
        self.assertEqual(s["TOUCHES"], 1)
        self.assertEqual(s["TRADE_EVIDENCE"], 1)
        self.assertEqual(s["ADMITTED_COUNTERFACTUAL_FILLS"], 0)
        self.assertFalse(s["TRADE_EVIDENCE_COUNTED_AS_FILL"])


class ATouchIsNeverPromoted(unittest.TestCase):

    def test_the_promotion_function_raises_by_name(self):
        with self.assertRaises(MF.TouchPromotion):
            MF.promote_touch_to_fill()

    def test_is_a_fill_is_false_on_a_touched_unknown(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        rows = window(["500"])
        rows[0]["ASK"] = "0.54"
        w = MF.walk_after_entry(q, rows)
        r = MF.fill_status(q, w)
        self.assertTrue(r["TOUCHED_OUR_PRICE"])
        self.assertFalse(MF.is_a_fill(r))

    def test_a_status_that_is_not_a_status_raises(self):
        with self.assertRaises(ValueError):
            MF.is_a_fill({"FILL_STATUS": "FILLED"})

    def test_the_word_filled_is_not_a_status(self):
        self.assertNotIn("FILLED", MF.FILL_STATUSES)


def clob(time, price, qty, symbol="m"):
    """A tape print the venue itself typed. See the class docstring below."""
    return {"time": time, "symbol": symbol, "price": price, "qty": qty,
            "execution_type": "CLOB_EXECUTION"}


class TheTapeJoinIsTheOnlyPositiveRoute(unittest.TestCase):
    """And it has a precondition of its own.

    `fill_model_v2.classify_execution` reaches CLOB only through a venue
    execution-type flag or a block index. An unflagged, unindexed print is
    UNKNOWN_EXECUTION_TYPE, which depletes no queue -- so a raw tape supports
    nothing at all, however much volume it shows.
    """

    def _quote(self, size="10", ahead="0"):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, size)
        q["QUEUE_AHEAD_ESTIMATE"] = D(ahead)
        q["QUOTE_TIME"] = 0
        return q

    def test_an_untyped_tape_supports_nothing_however_big(self):
        q = self._quote(ahead="0")
        tape = [{"time": 1, "symbol": "m", "price": "0.54", "qty": "99999"}]
        r = MF.with_tape(q, tape, model="F1")
        self.assertEqual(r["FILL_STATUS"], MF.NOT_FILLED)
        self.assertEqual(r["UNKNOWN_TYPE_VOLUME_AT_PRICE"], D("99999"))
        self.assertEqual(r["TRADED_VOLUME_AT_PRICE"], D("0"))

    def test_a_block_index_makes_an_unlisted_print_clob(self):
        q = self._quote(ahead="0")
        tape = [{"time": 1, "symbol": "m", "price": "0.54", "qty": "50"}]
        r = MF.with_tape(q, tape, model="F1", block_index=set())
        self.assertEqual(r["FILL_STATUS"], MF.COUNTERFACTUAL_FILL_F1)

    def test_a_tape_that_clears_the_queue_supports_f1(self):
        q = self._quote(ahead="0")
        r = MF.with_tape(q, [clob(1, "0.54", "50")], model="F1")
        self.assertEqual(r["FILL_STATUS"], MF.COUNTERFACTUAL_FILL_F1)
        self.assertTrue(MF.is_a_fill(r))
        self.assertEqual(r["ACTUAL_BETTOR_FILL"], MF.NOT_IDENTIFIED)

    def test_a_tape_short_of_the_queue_does_not(self):
        q = self._quote(ahead="100")
        self.assertEqual(
            MF.with_tape(q, [clob(1, "0.54", "50")], model="F1")["FILL_STATUS"],
            MF.NOT_FILLED)

    def test_a_tape_that_only_traded_through_us_is_not_a_fill(self):
        q = self._quote(ahead="0")
        r = MF.with_tape(q, [clob(1, "0.50", "500")], model="F1")
        self.assertEqual(r["FILL_STATUS"], MF.NOT_FILLED)
        self.assertEqual(r["F3_TOUCH_ONLY"], "YES")
        self.assertFalse(MF.is_a_fill(r))

    def test_a_block_print_at_our_price_fills_nothing(self):
        q = self._quote(ahead="0")
        row = clob(1, "0.54", "500")
        row["execution_type"] = "BLOCK_EXECUTION"
        r = MF.with_tape(q, [row], model="F1")
        self.assertEqual(r["FILL_STATUS"], MF.NOT_FILLED)
        self.assertEqual(r["BLOCK_VOLUME_AT_PRICE"], D("500"))

    def test_f0_needs_the_queue_and_our_whole_size(self):
        q = self._quote(size="100", ahead="0")
        self.assertEqual(
            MF.with_tape(q, [clob(1, "0.54", "50")], model="F0")["FILL_STATUS"],
            MF.NOT_FILLED)
        self.assertEqual(
            MF.with_tape(q, [clob(1, "0.54", "150")],
                         model="F0")["FILL_STATUS"],
            MF.COUNTERFACTUAL_FILL_F0)

    def test_an_incomplete_quote_refuses_the_join(self):
        q = self._quote()
        q["QUOTE_PRICE"] = MF.NOT_IDENTIFIED
        r = MF.with_tape(q, [])
        self.assertEqual(r["FILL_STATUS_BASIS"],
                         "TAPE_JOIN_REFUSED_INCOMPLETE_QUOTE")
        self.assertFalse(MF.is_a_fill(r))

    def test_the_verdict_is_read_from_the_tape_module_not_rederived(self):
        import fill_model_v2 as FM
        q = self._quote(ahead="0")
        tape = [clob(1, "0.54", "50")]
        mine = MF.with_tape(q, tape, model="F1")
        theirs = FM.evaluate(
            FM.Quote(0, "m", "BID", D("0.54"), D("10"), D("0")), tape)
        self.assertEqual(theirs["COUNTERFACTUAL_FILL_SUPPORTED_F1"], "YES")
        self.assertEqual(mine["FILL_EVIDENCE"]["TRADED_VOLUME_AT_PRICE"],
                         theirs["TRADED_VOLUME_AT_PRICE"])


class TheSummaryRefusesARateItCannotHave(unittest.TestCase):

    def _rows(self, statuses):
        out = []
        for s in statuses:
            out.append({"FILL_STATUS": s, "WHY":
                        "NO_PER_PRICE_ATTRIBUTION_WITHOUT_TAPE"
                        if s == MF.UNKNOWN else "x"})
        return out

    def test_any_unknown_makes_the_rate_not_identified(self):
        s = MF.summarise_fills(self._rows([MF.NOT_FILLED, MF.UNKNOWN]))
        self.assertEqual(s["FILL_RATE"], MF.NOT_IDENTIFIED)
        self.assertFalse(s["UNKNOWN_COUNTED_AS_MISS"])

    def test_a_complete_denominator_gives_a_rate(self):
        s = MF.summarise_fills(self._rows(
            [MF.NOT_FILLED, MF.COUNTERFACTUAL_FILL_F1]))
        self.assertEqual(s["FILL_RATE"], D("1") / D("2"))

    def test_the_forbidden_fields_are_never_numbers(self):
        s = MF.summarise_fills(self._rows([MF.UNKNOWN]))
        self.assertEqual(s["PROFITABILITY"], MF.NOT_IDENTIFIED)
        self.assertEqual(s["WIN_RATE"], MF.NOT_IDENTIFIED)


if __name__ == "__main__":
    unittest.main()
