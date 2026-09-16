#!/usr/bin/env python3
"""Inventory-closure learning. No outcome is computed on a fill nobody has."""
import ast
import unittest
from decimal import Decimal as D
from pathlib import Path

import inventory as INV
import maker_fill as MF
from test_maker_fill import clob, tick, window

SRC = Path(__file__).resolve().parent / "inventory.py"
TREE = ast.parse(SRC.read_text())

NI = INV.NOT_IDENTIFIED


T0 = "2026-09-16T16:00:00Z"
T1 = "2026-09-16T16:00:01Z"


def filled_long(size="10", ahead="0", price="0.54"):
    """A LONG inventory opened by a tape-supported counterfactual fill."""
    q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, size)
    q["QUEUE_AHEAD_ESTIMATE"] = D(ahead)
    q["QUOTE_TIME"] = T0
    f = MF.with_tape(q, [clob(T1, price, "50")], model="F1")
    assert MF.is_a_fill(f), f
    return q, f, INV.open_inventory(q, f, position_id="P1")


class TheModuleContactsNothing(unittest.TestCase):

    def test_no_http_client_is_imported(self):
        bad = {"httpx", "requests", "urllib", "urllib3", "http", "socket",
               "aiohttp"}
        for n in ast.walk(TREE):
            if isinstance(n, ast.Import):
                for a in n.names:
                    self.assertNotIn(a.name.split(".")[0], bad, a.name)
            if isinstance(n, ast.ImportFrom) and n.module:
                self.assertNotIn(n.module.split(".")[0], bad, n.module)


class InventoryOnlyOpensOnAnIdentifiedFill(unittest.TestCase):

    def test_a_refuted_fill_opens_nothing(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        f = MF.fill_status(q, MF.walk_after_entry(q, window(["0"])))
        with self.assertRaises(INV.FillNotIdentified):
            INV.open_inventory(q, f)

    def test_an_unknown_fill_opens_nothing_either(self):
        """UNKNOWN is refused as firmly as NOT_FILLED, which is the point."""
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        f = MF.fill_status(q, MF.walk_after_entry(q, window(["500"])))
        self.assertEqual(f["FILL_STATUS"], MF.UNKNOWN)
        with self.assertRaises(INV.FillNotIdentified):
            INV.open_inventory(q, f)

    def test_a_touched_but_unresolved_quote_opens_nothing(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        rows = window(["500"])
        rows[0]["ASK"] = "0.54"
        f = MF.fill_status(q, MF.walk_after_entry(q, rows))
        with self.assertRaises(INV.FillNotIdentified):
            INV.open_inventory(q, f)

    def test_a_tape_supported_fill_opens_one_sided_inventory(self):
        _, _, inv = filled_long()
        self.assertEqual(inv["INVENTORY_SIDE"], INV.LONG)
        self.assertEqual(inv["CONTRACTS"], D("10"))
        self.assertEqual(inv["ENTRY_PRICE"], D("0.54"))
        self.assertFalse(inv["ORDER_WAS_SUBMITTED"])
        self.assertEqual(inv["ACTUAL_POSITION"], NI)

    def test_a_filled_ask_leaves_us_short(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_ASK, "10")
        q["QUEUE_AHEAD_ESTIMATE"] = D("0")
        q["QUOTE_TIME"] = T0
        f = MF.with_tape(q, [clob(T1, "0.56", "50")], model="F1")
        self.assertEqual(INV.open_inventory(q, f)["INVENTORY_SIDE"], INV.SHORT)


class TheAggressiveCloseIsMeasured(unittest.TestCase):

    def test_displayed_depth_that_covers_our_size_gives_a_price(self):
        _, _, inv = filled_long(size="50")
        a = INV.aggressive_close(tick(1), inv)
        self.assertEqual(a["AGGRESSIVE_CLOSE_PRICE"], D("0.54"))
        self.assertTrue(a["PRICE_IS_MEASURED_NOT_HOPED"])

    def test_size_beyond_the_ladder_is_not_extrapolated(self):
        _, _, inv = filled_long(size="100000")
        a = INV.aggressive_close(tick(1), inv)
        self.assertEqual(a["AGGRESSIVE_CLOSE_PRICE"], NI)
        self.assertFalse(a["AGGRESSIVE_CLOSE_SIZE_COMPLETE"])

    def test_closing_a_long_walks_the_bids(self):
        _, _, inv = filled_long(size="150")     # 100 @0.54 then 50 @0.53
        a = INV.aggressive_close(tick(1), inv)
        self.assertEqual(a["AGGRESSIVE_CLOSE_PRICE"],
                         (D("100") * D("0.54") + D("50") * D("0.53")) / D("150"))

    def test_closing_a_short_walks_the_offers(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_ASK, "100")
        q["QUEUE_AHEAD_ESTIMATE"] = D("0")
        q["QUOTE_TIME"] = T0
        f = MF.with_tape(q, [clob(T1, "0.56", "500")], model="F1")
        inv = INV.open_inventory(q, f)
        a = INV.aggressive_close(tick(1), inv)
        self.assertEqual(a["AGGRESSIVE_CLOSE_PRICE"],
                         (D("80") * D("0.56") + D("20") * D("0.57")) / D("100"))


class ThePassiveCloseCarriesItsOwnVerdict(unittest.TestCase):

    def test_a_price_without_a_fill_is_not_achieved(self):
        _, _, inv = filled_long()
        p = INV.passive_close(inv, window(["500"]), tick(1))
        self.assertEqual(p["PASSIVE_CLOSE_PRICE"], D("0.56"))
        self.assertFalse(p["PASSIVE_CLOSE_ACHIEVED"])
        self.assertEqual(p["TIME_TO_OPPOSITE_FILL"], NI)
        self.assertEqual(p["PRICE_OF_OPPOSITE_FILL"], NI)

    def test_it_quotes_the_opposite_side_of_the_book(self):
        _, _, inv = filled_long()
        p = INV.passive_close(inv, window(["1"]), tick(1))
        self.assertEqual(p["QUOTE"]["SIDE"], MF.SIDE_ASK)


class TheEconomics(unittest.TestCase):

    def test_a_maker_round_trip_keeps_the_rebate_out_of_trading_net(self):
        _, _, inv = filled_long(size="100")
        e = INV.round_trip_economics(inv, "0.56", True,
                                     "2026-09-16T16:05:00Z")
        self.assertEqual(e["GROSS_SPREAD"], D("2.00"))
        self.assertEqual(e["TRADING_NET_EX_INCENTIVES"], D("2.00"))
        self.assertGreater(e["MAKER_REBATE"], 0)
        self.assertFalse(e["REALIZED"])
        # The liquidity-programme terms are NOT_IDENTIFIED on this venue, so
        # the TOTAL is not a number -- while the TRADING net, the figure that
        # decides whether the trade stands on its own, is.
        self.assertEqual(e["TOTAL_NET"], NI)
        self.assertIn("LIQUIDITY_INCENTIVE", e["INCENTIVE_MISSING_TERMS"])

    def test_measured_incentives_complete_the_total(self):
        _, _, inv = filled_long(size="100")
        e = INV.round_trip_economics(
            inv, "0.56", True, "2026-09-16T16:05:00Z",
            incentives={"LIQUIDITY_INCENTIVE": D("0"),
                        "OTHER_INCENTIVE": D("0")})
        self.assertEqual(e["TOTAL_NET"],
                         e["TRADING_NET_EX_INCENTIVES"] + e["MAKER_REBATE"])

    def test_a_taker_close_pays_the_fee_inside_trading_net(self):
        _, _, inv = filled_long(size="100")
        e = INV.round_trip_economics(inv, "0.56", False,
                                     "2026-09-16T16:05:00Z")
        self.assertLess(e["TRADING_NET_EX_INCENTIVES"], e["GROSS_SPREAD"])

    def test_a_rebate_that_rescues_a_losing_trade_is_flagged(self):
        _, _, inv = filled_long(size="100")
        e = INV.round_trip_economics(
            inv, "0.5399", True, "2026-09-16T16:05:00Z",
            incentives={"LIQUIDITY_INCENTIVE": D("0"),
                        "OTHER_INCENTIVE": D("0")})
        self.assertLess(e["TRADING_NET_EX_INCENTIVES"], 0)
        self.assertEqual(e["INCENTIVE_DEPENDENT"], "YES")

    def test_a_losing_trade_with_unmeasured_incentives_is_not_cleared(self):
        """'NO' would read as reassurance nobody has earned."""
        _, _, inv = filled_long(size="100")
        e = INV.round_trip_economics(inv, "0.5399", True,
                                     "2026-09-16T16:05:00Z")
        self.assertLess(e["TRADING_NET_EX_INCENTIVES"], 0)
        self.assertEqual(e["INCENTIVE_DEPENDENT"], NI)

    def test_an_unidentified_close_price_does_not_become_zero(self):
        _, _, inv = filled_long()
        e = INV.round_trip_economics(inv, NI, False, "2026-09-16T16:05:00Z")
        self.assertEqual(e["GROSS_SPREAD"], NI)
        self.assertEqual(e["TRADING_NET_EX_INCENTIVES"], NI)
        self.assertEqual(e["TOTAL_NET"], NI)

    def test_the_fee_regime_travels_with_the_row(self):
        _, _, inv = filled_long()
        e = INV.round_trip_economics(inv, "0.56", False,
                                     "2026-09-16T16:05:00Z")
        self.assertEqual(e["EXIT_FEE_REGIME"], "JUL2026")
        self.assertFalse(e["FEE_REGIME_STRADDLED"])

    def test_a_straddle_is_named_not_silently_pooled(self):
        _, _, inv = filled_long()
        e = INV.round_trip_economics(inv, "0.56", False,
                                     "2026-09-18T00:00:00Z")
        self.assertEqual(e["ENTRY_FEE_REGIME"], "JUL2026")
        self.assertEqual(e["EXIT_FEE_REGIME"], "SEP2026")
        self.assertTrue(e["FEE_REGIME_STRADDLED"])


class TheWholeOutcome(unittest.TestCase):

    def test_an_unclosed_position_reports_failure_not_a_number(self):
        _, _, inv = filled_long()
        o = INV.inventory_outcome(inv, window(["500", "500"]))
        self.assertTrue(o["FAILURE_TO_CLOSE"])
        self.assertEqual(o["TIME_TO_OPPOSITE_FILL"], NI)
        self.assertEqual(o["NET_SPREAD_CAPTURE"], NI)
        self.assertEqual(o["TOTAL_NET"], NI)
        self.assertEqual(o["PMUS_ACTION_AT_WINDOW_END"], "HOLD_INVENTORY")

    def test_the_aggressive_floor_is_identified_even_when_the_hope_is_not(self):
        _, _, inv = filled_long(size="50")
        o = INV.inventory_outcome(inv, window(["500", "500"]))
        self.assertEqual(o["AGGRESSIVE_CLOSE_PRICE_AT_WINDOW_END"], D("0.54"))
        self.assertNotEqual(o["AGGRESSIVE_CLOSE_TRADING_NET"], NI)
        self.assertTrue(o["AGGRESSIVE_CLOSE_IS_THE_MEASURED_FLOOR"])

    def test_settlement_is_never_reached_inside_a_tick_window(self):
        _, _, inv = filled_long()
        o = INV.inventory_outcome(inv, window(["1"]))
        self.assertEqual(o["SETTLEMENT_OUTCOME"], NI)
        self.assertFalse(o["SETTLEMENT_REACHABLE_IN_WINDOW"])

    def test_no_ticks_after_entry_is_a_reason_not_a_zero(self):
        _, _, inv = filled_long()
        o = INV.inventory_outcome(inv, [])
        self.assertEqual(o["WHY"], "NO_TICKS_AFTER_ENTRY")
        for f in ("TIME_TO_OPPOSITE_FILL", "NET_SPREAD_CAPTURE",
                  "CAPITAL_OCCUPANCY", "SETTLEMENT_OUTCOME"):
            self.assertEqual(o[f], NI, f)

    def test_capital_occupancy_is_dollars_times_seconds(self):
        _, _, inv = filled_long(size="100")
        o = INV.inventory_outcome(inv, window(["1", "1"]))
        self.assertEqual(o["CAPITAL_AT_RISK_USD"], D("54.00"))
        self.assertEqual(o["SECONDS_HELD"], D("6"))
        self.assertEqual(o["CAPITAL_OCCUPANCY"], D("324.00"))
        self.assertEqual(o["CAPITAL_OCCUPANCY_UNIT"], "USD_SECONDS")

    def test_markouts_are_signed_for_a_long_and_are_not_pnl(self):
        _, _, inv = filled_long()
        rows = window(["1", "1"])
        rows[0].update(BID="0.50", ASK="0.52",
                       BID_LADDER=[["0.50", "100"]], ASK_LADDER=[["0.52", "80"]])
        rows[1].update(BID="0.50", ASK="0.52",
                       BID_LADDER=[["0.50", "100"]], ASK_LADDER=[["0.52", "80"]])
        o = INV.inventory_outcome(inv, rows)
        self.assertEqual(o["INVENTORY_MARKOUT_30S"], D("-0.03"))
        self.assertTrue(o["MARKOUT_IS_NOT_PNL"])

    def test_every_named_outcome_field_is_present(self):
        from position_state import INVENTORY_OUTCOME_FIELDS
        _, _, inv = filled_long()
        o = INV.inventory_outcome(inv, window(["1"]))
        for f in INVENTORY_OUTCOME_FIELDS:
            self.assertIn(f, o, f)


class TheWhaleComparisonCarriesItsCaveat(unittest.TestCase):

    PRIORS = {"SWISSTONY_SENSITIVITY": {
        "MILESTONES_3": {"EVENTUAL_COMPLETION_RATE": 0.744,
                         "TIME_TO_25_PERCENT_EVENTUAL_S": 104.8,
                         "TIME_TO_50_PERCENT_EVENTUAL_S": 510.3,
                         "TIME_TO_75_PERCENT_EVENTUAL_S": 1789.5},
        "MILESTONES_4": {"EVENTUAL_COMPLETION_RATE": 0.750,
                         "TIME_TO_25_PERCENT_EVENTUAL_S": 141.1,
                         "TIME_TO_50_PERCENT_EVENTUAL_S": 856.1,
                         "TIME_TO_75_PERCENT_EVENTUAL_S": "BEYOND_3600S_GRID"}}}

    def test_comparing_without_saying_so_raises(self):
        with self.assertRaises(INV.EstimandMismatch):
            INV.compare_close_times(self.PRIORS, [100.0])

    def test_acknowledged_it_reports_both_and_refuses_the_inference(self):
        c = INV.compare_close_times(
            self.PRIORS, [100.0, 200.0, 300.0],
            estimand_difference_acknowledged=True)
        self.assertEqual(c["WHALE_PRIOR_EXPECTED_CLOSE_TIME"], 510.3)
        self.assertEqual(c["BETTOR_OBSERVED_COUNTERFACTUAL_CLOSE_TIME"], 200.0)
        self.assertFalse(c["ESTIMANDS_ARE_THE_SAME"])
        self.assertEqual(c["DIFFERENCE_IS_EVIDENCE_ABOUT_BETTOR_SPEED"], NI)

    def test_an_off_grid_milestone_stays_off_grid(self):
        p = INV.whale_prior_expected_close(self.PRIORS, "MILESTONES_4")
        self.assertEqual(p["TIME_TO_75_PERCENT_EVENTUAL_S"], NI)
        self.assertEqual(p["TIME_TO_75_PERCENT_EVENTUAL_S_RAW"],
                         "BEYOND_3600S_GRID")

    def test_both_prior_sets_are_readable_side_by_side(self):
        a = INV.whale_prior_expected_close(self.PRIORS, "MILESTONES_3")
        b = INV.whale_prior_expected_close(self.PRIORS, "MILESTONES_4")
        self.assertEqual(a["WHALE_PRIOR_EXPECTED_CLOSE_TIME"], 510.3)
        self.assertEqual(b["WHALE_PRIOR_EXPECTED_CLOSE_TIME"], 856.1)

    def test_missing_milestones_are_not_identified(self):
        p = INV.whale_prior_expected_close({}, "MILESTONES_3")
        self.assertEqual(p["WHALE_PRIOR_EXPECTED_CLOSE_TIME"], NI)

    def test_the_real_priors_file_reads_through_this_path(self):
        import json
        f = (Path(__file__).resolve().parent.parent / "evidence"
             / "whale_audit" / "whale_exit_priors_v1.json")
        if not f.exists():
            self.skipTest("priors not in this tree")
        p = INV.whale_prior_expected_close(json.loads(f.read_text()))
        self.assertAlmostEqual(p["WHALE_PRIOR_EXPECTED_CLOSE_TIME"],
                               510.31605, places=3)


class TheSummaryReportsNothingItCannotSupport(unittest.TestCase):

    def test_the_forbidden_fields_are_never_numbers(self):
        s = INV.summarise_inventory([])
        for f in ("PROFITABILITY", "WIN_RATE", "EXPECTED_MONTHLY_RETURN"):
            self.assertEqual(s[f], NI, f)

    def test_an_open_position_blocks_a_pooled_net(self):
        _, _, inv = filled_long()
        o = INV.inventory_outcome(inv, window(["500"]))
        self.assertEqual(INV.summarise_inventory([o])["POOLED_NET"], NI)

    def test_still_holding_is_counted(self):
        _, _, inv = filled_long()
        o = INV.inventory_outcome(inv, window(["500"]))
        s = INV.summarise_inventory([o])
        self.assertEqual(s["INVENTORY_POSITIONS_OPENED"], 1)
        self.assertEqual(s["STILL_HOLDING"], 1)
        self.assertEqual(s["CLOSED_PASSIVELY"], 0)


if __name__ == "__main__":
    unittest.main()


class TheInventoryClockStartsOnlyFromAnAdmittedFill(unittest.TestCase):

    def test_the_clock_is_stamped_by_the_fill_that_opened_it(self):
        q, f, inv = filled_long()
        self.assertEqual(inv["INVENTORY_START_TIME"], q["QUOTE_TIME"])
        self.assertEqual(inv["INVENTORY_CLOCK_STARTED_BY"], f["FILL_STATUS"])
        self.assertTrue(inv["CLOCK_MAY_START_ONLY_FROM_AN_ADMITTED_FILL"])
        self.assertIn(inv["INVENTORY_CLOCK_STARTED_BY"],
                      (MF.COUNTERFACTUAL_FILL_F0, MF.COUNTERFACTUAL_FILL_F1,
                       MF.COUNTERFACTUAL_FILL_F2))

    def test_no_clock_exists_without_one(self):
        q = MF.hypothetical_quote(tick(0), MF.SIDE_BID, "10")
        for w in (window(["0"]), window(["500"])):
            f = MF.fill_status(q, MF.walk_after_entry(q, w))
            with self.assertRaises(INV.FillNotIdentified):
                INV.open_inventory(q, f)

    def test_every_duration_is_measured_from_that_start(self):
        _, _, inv = filled_long(size="100")
        o = INV.inventory_outcome(inv, window(["1", "1"]))
        self.assertEqual(inv["INVENTORY_START_ELAPSED_S"],
                         inv["ENTRY_ELAPSED_S"])
        self.assertEqual(o["SECONDS_HELD"], D("6"))
