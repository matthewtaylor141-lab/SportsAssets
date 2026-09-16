#!/usr/bin/env python3
"""The eligibility screen, with its nesting and its limits asserted.

The tier definitions are prose in a preregistration until something checks
them over every market. These are that check.
"""
from __future__ import annotations

import ast
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import eligibility as E  # noqa: E402

NI = "NOT_IDENTIFIED"


def row(slug="aec-nfl-a-b-2026-09-19", bid="0.30", ask="0.31", tick=0.01,
        status="MARKET_STATUS_OPEN"):
    r = {"slug": slug, "status": status, "orderPriceMinTickSize": tick}
    if bid is not None:
        r["board_bestBidQuote"] = {"value": bid, "currency": "USD"}
    if ask is not None:
        r["board_bestAskQuote"] = {"value": ask, "currency": "USD"}
    return r


def book(last_trade="T0"):
    st = {"sharesTraded": "10"}
    if last_trade is not None:
        st["lastTradeSetTime"] = last_trade
    return {"marketData": {"bids": [], "offers": [], "stats": st}}


# A trivial injected clock: timestamps are plain numbers in these tests, so the
# module's refusal to do its own date arithmetic is visible.
def PARSE(ts):
    return float(ts)


class TheTiersAreNested(unittest.TestCase):
    """HIGH_ACTIVITY subset ACTIVE subset BROAD, for every market. This is the
    frozen interpretation; the alternative (independent tiers) is NOT the
    design, and if it ever became the design it would need its own freeze."""

    def test_the_nesting_holds_across_a_sweep(self):
        self.assertTrue(E.TIERS_ARE_NESTED)
        for spread_ticks in (0, 1, 2, 5, 6, 20):
            for age in (0, 59 * 60, 60 * 60, 61 * 60,
                        24 * 3600, 24 * 3600 + 1, 10 ** 7):
                for status in ("MARKET_STATUS_OPEN", "MARKET_STATUS_CLOSED"):
                    ask = D("0.30") + D("0.01") * spread_ticks
                    c = E.classify(row(bid="0.30", ask=str(ask),
                                       status=status),
                                   book("0"), now_s=age, parse_time=PARSE)
                    b, a, h = c["BROAD"], c["ACTIVE"], c["HIGH_ACTIVITY"]
                    if h is True:
                        self.assertIs(a, True, (spread_ticks, age, status))
                        self.assertIs(b, True, (spread_ticks, age, status))
                    if a is True:
                        self.assertIs(b, True, (spread_ticks, age, status))

    def test_a_wide_spread_fails_every_tier_however_recent_the_trade(self):
        c = E.classify(row(bid="0.30", ask="0.50"), book("0"), 1, PARSE)
        self.assertFalse(c["BROAD"])
        self.assertFalse(c["ACTIVE"])
        self.assertFalse(c["HIGH_ACTIVITY"])

    def test_the_cut_points_are_the_frozen_ones(self):
        self.assertEqual(E.MAX_SPREAD_TICKS_BROAD, 5)
        self.assertEqual(E.ACTIVE_RECENCY_S, 24 * 3600)
        self.assertEqual(E.HIGH_ACTIVITY_RECENCY_S, 60 * 60)

    def test_the_boundaries_are_inclusive_and_exact(self):
        exactly_5 = E.classify(row(bid="0.30", ask="0.35"))
        self.assertTrue(exactly_5["BROAD"])
        just_over = E.classify(row(bid="0.30", ask="0.36"))
        self.assertFalse(just_over["BROAD"])

        at_60m = E.classify(row(), book("0"), 60 * 60, PARSE)
        self.assertTrue(at_60m["HIGH_ACTIVITY"])
        past_60m = E.classify(row(), book("0"), 60 * 60 + 1, PARSE)
        self.assertFalse(past_60m["HIGH_ACTIVITY"])
        self.assertTrue(past_60m["ACTIVE"])

    def test_all_three_tiers_are_always_present_in_a_row(self):
        """A tier is never dropped from a result because it looked bad."""
        c = E.classify(row())
        for t in E.TIERS:
            self.assertIn(t, c, t)


class SpreadIsMeasuredInTicksNotCents(unittest.TestCase):
    """The board carries three tick sizes, so a screen in cents would be three
    different screens."""

    def test_a_one_tick_spread_on_a_fine_tick_is_one_tick(self):
        c = E.classify(row(bid="0.3000", ask="0.3010", tick=0.001))
        self.assertEqual(c["SPREAD_TICKS"], D("1"))
        self.assertTrue(c["BROAD"])

    def test_the_same_cent_spread_is_ten_ticks_on_a_fine_tick(self):
        c = E.classify(row(bid="0.30", ask="0.31", tick=0.001))
        self.assertEqual(c["SPREAD_TICKS"], D("10"))
        self.assertFalse(c["BROAD"], "10 ticks is outside BROAD")


class NotHavingLookedIsNotEvidence(unittest.TestCase):

    def test_without_a_stage_2_read_the_activity_tiers_are_unknown(self):
        c = E.classify(row())
        self.assertTrue(c["BROAD"])
        self.assertEqual(c["ACTIVE"], NI)
        self.assertEqual(c["HIGH_ACTIVITY"], NI)
        self.assertFalse(c["STAGE2_READ"])
        self.assertNotEqual(c["ACTIVE"], False)

    def test_a_market_that_never_traded_fails_rather_than_unknown(self):
        """We DID look, and there is no last trade. That is a failed activity
        screen, not an unobserved one."""
        c = E.classify(row(), book(last_trade=None), 1000, PARSE)
        self.assertTrue(c["STAGE2_READ"])
        self.assertEqual(c["TRADE_RECENCY_S"], NI)
        self.assertIs(c["ACTIVE"], False)
        self.assertIs(c["HIGH_ACTIVITY"], False)

    def test_an_unparseable_timestamp_is_not_treated_as_recent(self):
        def boom(ts):
            raise ValueError(ts)
        c = E.classify(row(), book("nonsense"), 1000, boom)
        self.assertEqual(c["TRADE_RECENCY_S"], NI)
        self.assertIs(c["HIGH_ACTIVITY"], False)


class RecencyIsNotARate(unittest.TestCase):
    """One timestamp gives TRADE_RECENCY. A market whose last trade was five
    minutes ago may still have a terrible long-run arrival rate -- one trade in
    a day, read five minutes after it."""

    def test_the_two_activity_levels_are_kept_apart(self):
        self.assertEqual(E.LEVEL_A_ACTIVITY,
                         "TRADE_RECENCY_FROM_ONE_BOOK_READ")
        self.assertEqual(E.LEVEL_B_ACTIVITY,
                         "OBSERVED_TRADE_COUNT_PER_ELAPSED_TIME")
        self.assertFalse(E.LEVEL_B_AVAILABLE)
        self.assertNotEqual(E.LEVEL_A_ACTIVITY, E.LEVEL_B_ACTIVITY)

    def test_a_row_never_carries_a_level_b_figure(self):
        c = E.classify(row(), book("0"), 60, PARSE)
        self.assertEqual(c["LEVEL_B_ACTIVITY"], NI)

    def test_the_module_estimates_no_rate_at_all(self):
        """Structural. These names must not exist to be read as rates."""
        src = (HERE / "eligibility.py").read_text()
        tree = ast.parse(src)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Attribute)}
        names |= {t.id for n in ast.walk(tree)
                  if isinstance(n, ast.Assign) for t in n.targets
                  if isinstance(t, ast.Name)}
        for banned in ("TRADES_PER_HOUR", "EXPECTED_INTERARRIVAL_TIME",
                       "POISSON_RATE", "QUEUE_CLEARING_TIME",
                       "ARRIVAL_RATE", "TRADE_RATE"):
            self.assertNotIn(banned, names, banned)

    def test_the_availability_flags_say_which_is_which(self):
        self.assertEqual(E.TRADE_RECENCY_AVAILABLE, "YES")
        self.assertEqual(E.TRADE_ARRIVAL_RATE_AVAILABLE, "NO")


class TheNorthStarIsNotManufactured(unittest.TestCase):

    def test_the_fill_dependent_terms_stay_unidentified(self):
        c = E.census([E.classify(row(), book("0"), 60, PARSE)])
        for f in ("EXPECTED_FILL_RATE", "EXPECTED_WAIT_TO_FILL",
                  "EXPECTED_CAPITAL_OCCUPANCY",
                  "EXPECTED_NET_PNL_PER_CAPITAL_DOLLAR_PER_HOUR"):
            self.assertEqual(c[f], NI, f)

    def test_a_recent_trade_does_not_produce_a_wait_estimate(self):
        """The exact substitution forbidden: LAST_TRADE_AGE -> fill rate."""
        c = E.census([E.classify(row(), book("0"), 1, PARSE)])
        self.assertEqual(c["HIGH_ACTIVITY_COUNT"], 1)
        self.assertEqual(c["EXPECTED_WAIT_TO_FILL"], NI)

    def test_economics_is_not_answerable_from_this_module(self):
        self.assertFalse(E.QUESTION_ECONOMICS_ANSWERABLE_HERE)
        self.assertNotEqual(E.QUESTION_ELIGIBILITY, E.QUESTION_ECONOMICS)
        self.assertNotEqual(E.QUESTION_GENERALIZATION, E.QUESTION_ELIGIBILITY)


class TheCensusIsRollingAndSaysSo(unittest.TestCase):

    def test_it_is_never_called_a_simultaneous_snapshot(self):
        c = E.census([], scan_start=0, scan_end=4600)
        self.assertEqual(c["OBSERVATION_TYPE"], "ROLLING_CENSUS")
        self.assertFalse(c["IS_SIMULTANEOUS_BOARD_SNAPSHOT"])
        self.assertEqual(c["ROLLING_CENSUS_DURATION_S"], 4600)
        self.assertIn("different wall-clock times", c["TEMPORAL_DRIFT_LABEL"])

    def test_the_four_counts_are_distinct_fields(self):
        """STAGE1_TWO_SIDED_COUNT and STAGE1_BROAD_SURVIVOR_COUNT are NOT
        synonyms; conflating them overstated the stage-2 budget by 32.9%."""
        rows = [row(bid="0.30", ask="0.31"),      # 1 tick  -> broad
                row(bid="0.30", ask="0.50"),      # 20 ticks-> two-sided only
                row(bid=None, ask=None)]          # not two-sided
        c = E.census([E.classify(r) for r in rows])
        self.assertEqual(c["STAGE1_INPUT_COUNT"], 3)
        self.assertEqual(c["STAGE1_TWO_SIDED_COUNT"], 2)
        self.assertEqual(c["STAGE1_BROAD_SURVIVOR_COUNT"], 1)
        self.assertNotEqual(c["STAGE1_TWO_SIDED_COUNT"],
                            c["STAGE1_BROAD_SURVIVOR_COUNT"])

    def test_stage2_reads_are_counted_separately_from_survivors(self):
        """The stage-2 budget must come from reads actually taken, not from
        the survivor count it was planned against."""
        c = E.census([E.classify(row()),                       # no read
                      E.classify(row(), book("0"), 60, PARSE)])  # read
        self.assertEqual(c["STAGE1_BROAD_SURVIVOR_COUNT"], 2)
        self.assertEqual(c["STAGE2_BOOK_READ_COUNT"], 1)
        self.assertEqual(c["ACTIVE_NOT_IDENTIFIED"], 1)


class ThisFileContactsNothing(unittest.TestCase):

    def test_it_imports_no_network_library_and_no_clock(self):
        """The clock is injected, so the module cannot acquire a timezone
        opinion of its own -- the C-6 / C-10 business-date trap."""
        tree = ast.parse((HERE / "eligibility.py").read_text())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods.update(x.name.split(".")[0] for x in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        self.assertLessEqual(mods, {"decimal", "__future__"}, mods)


if __name__ == "__main__":
    unittest.main(verbosity=2)


def bk(bid="0.30", ask="0.31", last_trade="0", state="MARKET_STATE_OPEN"):
    md = {"state": state, "stats": {"sharesTraded": "10"}}
    if bid is not None:
        md["bids"] = [{"px": {"value": bid}, "qty": "100"}]
    if ask is not None:
        md["offers"] = [{"px": {"value": ask}, "qty": "100"}]
    if last_trade is not None:
        md["stats"]["lastTradeSetTime"] = last_trade
    return {"marketData": md}


class TheDecisionScreenRecomputesFromTheArrivingBook(unittest.TestCase):
    """Stage 1 observes T0; stage 2 observes Ti up to 1.29 h later. Combining
    SPREAD_AT_T0 with TRADE_RECENCY_AT_Ti describes no market at any single
    decision time."""

    def test_a_market_that_widened_after_routing_fails_at_decision(self):
        r = row(bid="0.30", ask="0.31")           # 1 tick at T0 -> routed
        self.assertTrue(E.stage1(r)["BROAD"])
        d = E.decision_screen(r, bk(bid="0.30", ask="0.50"),  # 20 ticks at Ti
                              now_s=0, parse_time=PARSE,
                              book_transact_time="100",
                              book_receipt_time="101")
        self.assertEqual(d["CURRENT_SPREAD_TICKS_AT_TI"], D("20"))
        self.assertFalse(d["BROAD_AT_DECISION"])
        self.assertFalse(d["HIGH_ACTIVITY_AT_DECISION"])

    def test_the_stage1_spread_is_not_carried_forward(self):
        r = row(bid="0.30", ask="0.50")           # wide at T0
        d = E.decision_screen(r, bk(bid="0.30", ask="0.31"),  # tight at Ti
                              0, PARSE, book_transact_time="100",
                              book_receipt_time="101")
        self.assertEqual(d["CURRENT_SPREAD_TICKS_AT_TI"], D("1"))
        self.assertTrue(d["BROAD_AT_DECISION"])
        self.assertTrue(d["STAGE2_CURRENT_SPREAD_RECOMPUTED"])

    def test_a_market_closed_by_decision_time_fails(self):
        d = E.decision_screen(row(), bk(state="MARKET_STATE_CLOSED"),
                              0, PARSE, book_transact_time="100",
                              book_receipt_time="101")
        self.assertFalse(d["BROAD_AT_DECISION"])

    def test_the_decision_tiers_nest(self):
        for spread in (1, 6):
            for age in (30, 3600, 7200, 10 ** 6):
                ask = D("0.30") + D("0.01") * spread
                d = E.decision_screen(
                    row(), bk(ask=str(ask), last_trade="0"), 0, PARSE,
                    book_transact_time=str(age),
                    book_receipt_time=str(age))
                if d["HIGH_ACTIVITY_AT_DECISION"]:
                    self.assertTrue(d["ACTIVE_AT_DECISION"], (spread, age))
                if d["ACTIVE_AT_DECISION"]:
                    self.assertTrue(d["BROAD_AT_DECISION"], (spread, age))


class RecencyUsesPerRowClocks(unittest.TestCase):

    def test_both_clocks_are_computed_and_differ(self):
        d = E.decision_screen(row(), bk(last_trade="0"), 0, PARSE,
                              book_transact_time="100",
                              book_receipt_time="130")
        self.assertEqual(d["TRADE_RECENCY_AT_VENUE_SNAPSHOT"], 100)
        self.assertEqual(d["TRADE_RECENCY_AT_BETTOR_RECEIPT"], 130)
        self.assertNotEqual(d["TRADE_RECENCY_AT_VENUE_SNAPSHOT"],
                            d["TRADE_RECENCY_AT_BETTOR_RECEIPT"])

    def test_a_negative_age_is_a_defect_and_is_not_treated_as_active(self):
        """Clamping to zero would make a broken clock the strongest activity
        signal on the board, promoting exactly the wrong markets."""
        d = E.decision_screen(row(), bk(last_trade="500"), 0, PARSE,
                              book_transact_time="100",
                              book_receipt_time="100")
        self.assertEqual(d["TRADE_RECENCY_AT_VENUE_SNAPSHOT"], -400)
        self.assertTrue(d["NEGATIVE_RECENCY"])
        self.assertIs(d["LAST_TRADE_BEFORE_TRANSACT_TIME"], False)
        self.assertFalse(d["HIGH_ACTIVITY_AT_DECISION"])
        self.assertFalse(d["ACTIVE_AT_DECISION"])

    def test_the_ordering_invariant_is_reported_per_row(self):
        ok = E.decision_screen(row(), bk(last_trade="0"), 0, PARSE,
                               book_transact_time="100",
                               book_receipt_time="101")
        self.assertIs(ok["LAST_TRADE_BEFORE_TRANSACT_TIME"], True)
        self.assertFalse(ok["NEGATIVE_RECENCY"])

    def test_negative_rows_are_counted_not_hidden(self):
        rows = [E.decision_screen(row(), bk(last_trade=lt), 0, PARSE,
                                  book_transact_time="100",
                                  book_receipt_time="100")
                for lt in ("0", "500", "0")]
        c = E.census(rows)
        self.assertEqual(c["NEGATIVE_RECENCY_ROWS"], 1)
        self.assertIn("PER_ROW", c["TRADE_RECENCY_CLOCK"])
        self.assertIn("never a shared timestamp", c["TRADE_RECENCY_CLOCK"])


class TheResultIsPipelineYieldNotBoardPrevalence(unittest.TestCase):
    """A market quoting 6 ticks at T0 is never read at Ti and may have
    tightened since. No amount of stage-2 care recovers it."""

    def test_the_census_names_itself_a_pipeline_yield(self):
        c = E.census([])
        self.assertEqual(c["RESULT_KIND"], "PIPELINE_YIELD")
        self.assertTrue(c["STAGE1_REJECTS_ARE_NEVER_REEXAMINED"])

    def test_true_board_share_stays_unidentified(self):
        rows = [E.decision_screen(row(), bk(), 0, PARSE,
                                  book_transact_time="10",
                                  book_receipt_time="11")] * 3
        c = E.census(rows)
        self.assertEqual(c["TRUE_ELIGIBLE_MARKET_SHARE_OF_BOARD"], NI)
        self.assertEqual(c["TRUE_BOARD_ELIGIBLE_SHARE"], NI)

    def test_the_frame_is_the_observed_prefix_not_the_board(self):
        c = E.census([])
        self.assertEqual(c["SAMPLING_FRAME"], "OBSERVED_20K_PREFIX")
        self.assertEqual(c["FULL_BOARD_BOUNDARY_KNOWN"], "NO")
        self.assertEqual(c["GENERALIZES_TO_FULL_BOARD"], NI)
        self.assertNotEqual(c["GENERALIZES_TO_OBSERVED_PREFIX"],
                            c["GENERALIZES_TO_FULL_BOARD"])

    def test_yields_are_expressed_against_the_routed_frame(self):
        rows = [E.decision_screen(row(), bk(last_trade="0"), 0, PARSE,
                                  book_transact_time="30",
                                  book_receipt_time="31"),
                E.decision_screen(row(), bk(ask="0.50"), 0, PARSE,
                                  book_transact_time="30",
                                  book_receipt_time="31")]
        for r in rows:
            r["BROAD"] = True          # both were routed by stage 1
        c = E.census(rows)
        self.assertEqual(c["STAGE1_ROUTED_MARKETS"], 2)
        self.assertEqual(c["STAGE2_BROAD_AT_DECISION"], 1)
        self.assertEqual(c["PIPELINE_YIELD_BROAD"], "1/2")
        self.assertEqual(c["PIPELINE_YIELD_HIGH_ACTIVITY"], "1/2")
