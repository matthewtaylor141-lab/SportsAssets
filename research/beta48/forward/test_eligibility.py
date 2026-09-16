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
        opinion of its own -- the C-6 / C-10 business-date trap.

        hashlib is permitted: it is deterministic, does no I/O, and is how the
        audit interleave is made reproducible. What must stay out is anything
        that could read a network or a wall clock."""
        tree = ast.parse((HERE / "eligibility.py").read_text())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods.update(x.name.split(".")[0] for x in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        self.assertLessEqual(mods, {"decimal", "hashlib", "__future__"},
                             mods)
        for banned in ("httpx", "requests", "urllib", "socket", "time",
                       "datetime", "zoneinfo"):
            self.assertNotIn(banned, mods, banned)


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


class EventIdentityIsFrozenAndDerived(unittest.TestCase):
    """The venue publishes NO event identifier -- eventSlug, eventId, event,
    gameId, conditionId and five others are all 0/20,000 on the captured raw
    objects. So the key is derived, and the derivation is frozen before
    sampling rather than invented after seeing a sample's composition."""

    def test_the_prefix_is_dropped_and_the_date_retained(self):
        self.assertEqual(E.event_key("aec-ufc-alomen-iwobar-2026-09-19"),
                         "ufc-alomen-iwobar-2026-09-19")

    def test_a_market_and_its_props_share_one_event(self):
        a = E.event_key("aec-ufc-alomen-iwobar-2026-09-19")
        b = E.event_key("astatc-ufc-alomen-iwobar-2026-09-19-mof-ko")
        c = E.event_key("astatc-ufc-alomen-iwobar-2026-09-19-mov-f1-dec")
        self.assertEqual(a, b)
        self.assertEqual(b, c)

    def test_different_events_do_not_collide(self):
        self.assertNotEqual(E.event_key("aec-ufc-a-b-2026-09-19"),
                            E.event_key("aec-ufc-a-b-2026-09-26"))

    def test_a_slug_with_no_date_has_no_event_key(self):
        """None, not a fabricated singleton. A market we cannot place in an
        event must not silently become its own independent event."""
        self.assertIsNone(E.event_key("some-market-without-a-date"))
        self.assertIsNone(E.event_key(None))

    def test_the_source_is_recorded_as_derived(self):
        self.assertEqual(E.VENUE_EVENT_IDENTIFIER_PRESENT, "NO")
        self.assertEqual(E.EVENT_ID_SOURCE, "DERIVED_FROM_SLUG_DATE_PREFIX")
        self.assertTrue(E.EVENT_ID_DERIVATION_FROZEN)


class TheTwoEstimandsAreNeverCollapsed(unittest.TestCase):

    def _rows(self):
        # One event with four markets, three eligible; one event with a single
        # market, not eligible. Market-weighted says 3/5; event-weighted says
        # (0.75 + 0)/2 = 0.375. The numbers MUST differ.
        rows = []
        for i, ok in enumerate((True, True, True, False)):
            rows.append({"slug": "aec-nfl-a-b-2026-09-19-p%d" % i, "X": ok})
        rows.append({"slug": "aec-nfl-c-d-2026-09-20", "X": False})
        return rows

    # The weighting MACHINERY is still correct and still tested; it is the KEY
    # that failed validation. So these run it with an explicit level-B key,
    # which is the only way the gate lets it run at all.
    _B = dict(key=lambda c: E.market_family_key(c.get("slug")),
              level=E.IDENTITY_LEVEL_B)

    def test_market_and_event_weighting_give_different_answers(self):
        out = E.by_event(self._rows(), "X", **self._B)
        self.assertEqual(out["RAW_MARKET_N"], 5)
        self.assertEqual(out["UNIQUE_EVENT_N"], 2)
        self.assertAlmostEqual(out["MARKET_WEIGHTED_RESULT"], 3 / 5)
        self.assertAlmostEqual(out["EVENT_WEIGHTED_RESULT"], (0.75 + 0.0) / 2)
        self.assertNotAlmostEqual(out["MARKET_WEIGHTED_RESULT"],
                                  out["EVENT_WEIGHTED_RESULT"])

    def test_the_independent_sample_size_is_events_not_markets(self):
        out = E.by_event(self._rows(), "X", **self._B)
        self.assertEqual(out["INDEPENDENT_SAMPLE_SIZE"], 2)
        self.assertNotEqual(out["INDEPENDENT_SAMPLE_SIZE"],
                            out["RAW_MARKET_N"])

    def test_event_weighting_aggregates_within_event_first(self):
        self.assertIn("WITHIN_EVENT_FIRST",
                      E.by_event([], "X", **self._B)["WEIGHTING"])
        self.assertTrue(E.ESTIMANDS_ARE_NEVER_COLLAPSED)

    def test_markets_without_an_event_key_are_counted_not_folded_in(self):
        rows = self._rows() + [{"slug": "no-date", "X": True}]
        out = E.by_event(rows, "X", **self._B)
        self.assertEqual(out["MARKETS_WITHOUT_EVENT_ID"], 1)
        self.assertEqual(out["UNIQUE_EVENT_N"], 2)

    def test_underpowered_is_said_rather_than_precision_manufactured(self):
        self.assertEqual(E.inference_status(2, validated=True), "UNDERPOWERED")
        self.assertEqual(E.inference_status(1456, validated=True),
                         "EVENT_CLUSTERED_INFERENCE_PERMITTED")


class FailureHasAReasonCode(unittest.TestCase):

    def test_missing_and_stale_are_different_codes(self):
        """A market that never traded and one that traded two days ago fail
        the same tier for completely different reasons."""
        missing = E.decision_screen(row(), bk(last_trade=None), 0, PARSE,
                                    book_transact_time="100",
                                    book_receipt_time="101")
        stale = E.decision_screen(row(), bk(last_trade="0"), 0, PARSE,
                                  book_transact_time=str(48 * 3600),
                                  book_receipt_time=str(48 * 3600))
        self.assertEqual(missing["PRIMARY_FAIL_REASON"],
                         E.FAIL_NO_TRADE_TIMESTAMP)
        self.assertEqual(stale["PRIMARY_FAIL_REASON"], E.FAIL_STALE_TRADE)
        self.assertNotEqual(missing["PRIMARY_FAIL_REASON"],
                            stale["PRIMARY_FAIL_REASON"])

    def test_a_negative_age_is_its_own_code_not_staleness(self):
        d = E.decision_screen(row(), bk(last_trade="500"), 0, PARSE,
                              book_transact_time="100",
                              book_receipt_time="100")
        self.assertEqual(d["PRIMARY_FAIL_REASON"], E.FAIL_INVALID_CLOCK)
        self.assertTrue(d["LAST_TRADE_SET_TIME_INVALID_FUTURE"])

    def test_the_primary_reason_is_deterministic_under_multiple_failures(self):
        d = E.decision_screen(row(), bk(ask="0.90", last_trade=None,
                                        state="MARKET_STATE_CLOSED"),
                              0, PARSE, book_transact_time="100",
                              book_receipt_time="101")
        self.assertIn(E.FAIL_CLOSED, d["FAIL_REASONS"])
        self.assertIn(E.FAIL_SPREAD, d["FAIL_REASONS"])
        self.assertIn(E.FAIL_NO_TRADE_TIMESTAMP, d["FAIL_REASONS"])
        self.assertEqual(d["PRIMARY_FAIL_REASON"], E.FAIL_CLOSED)

    def test_a_passing_market_has_no_reason(self):
        d = E.decision_screen(row(), bk(last_trade="0"), 0, PARSE,
                              book_transact_time="60", book_receipt_time="61")
        self.assertIsNone(d["PRIMARY_FAIL_REASON"])
        self.assertEqual(d["FAIL_REASONS"], [])


class TheAuditIsInterleavedNotAppended(unittest.TestCase):
    """Putting audit markets at the end of a 1.29 h scan gives each the maximum
    time to tighten, so the false-negative rate would measure scan latency
    rather than routing error."""

    def test_audit_rows_are_spread_through_the_scan(self):
        routed = ["r%03d" % i for i in range(200)]
        audit = ["a%03d" % i for i in range(20)]
        sched = E.audit_schedule(routed, audit, salt="S")
        pos = [r["position"] for r in sched if r["lane"] == "AUDIT"]
        self.assertEqual(len(pos), 20)
        # Not clustered at either end: audit reads appear in the first and the
        # last third of the scan.
        self.assertLess(min(pos), len(sched) // 3)
        self.assertGreater(max(pos), 2 * len(sched) // 3)

    def test_the_schedule_is_deterministic_and_salt_dependent(self):
        a = E.audit_schedule(["r1", "r2"], ["a1"], salt="S")
        b = E.audit_schedule(["r2", "r1"], ["a1"], salt="S")
        self.assertEqual([x["slug"] for x in a], [x["slug"] for x in b])
        c = E.audit_schedule(["r1", "r2"], ["a1"], salt="OTHER")
        self.assertEqual(len(a), len(c))

    def test_every_market_appears_exactly_once(self):
        sched = E.audit_schedule(["r1", "r2"], ["a1"], salt="S")
        self.assertEqual(sorted(x["slug"] for x in sched),
                         ["a1", "r1", "r2"])


class CapacityIsGrossAndNet(unittest.TestCase):
    """Thirty eligible props on one NFL game are not thirty independent
    capital opportunities."""

    def test_markets_and_events_are_counted_separately(self):
        rows = [{"slug": "aec-nfl-a-b-2026-09-19-p%d" % i,
                 "HIGH_ACTIVITY_AT_DECISION": True} for i in range(30)]
        rows.append({"slug": "aec-nfl-c-d-2026-09-20",
                     "HIGH_ACTIVITY_AT_DECISION": True})
        cap = E.capacity(rows)
        self.assertEqual(cap["MARKET_LEVEL_GROSS_CAPACITY"], 31)
        self.assertEqual(cap["FAMILY_LEVEL_COUNT"], 2)
        self.assertEqual(cap["MAX_FAMILY_EXPOSURE"], 30)
        self.assertNotEqual(cap["MARKET_LEVEL_GROSS_CAPACITY"],
                            cap["FAMILY_LEVEL_COUNT"])

    def test_independence_is_not_inferred_from_differing_slugs(self):
        cap = E.capacity([])
        self.assertFalse(cap["EVENT_INDEPENDENCE_INFERRED_FROM_DIFFERING_SLUGS"])

    def test_capacity_is_a_count_and_not_money(self):
        cap = E.capacity([])
        self.assertEqual(cap["EXPECTED_NET_PNL_PER_CAPITAL_DOLLAR_PER_HOUR"],
                         NI)


# ---------------------------------------------------------------------------
# CL-22 .. CL-26: THE EVENT KEY FAILED VALIDATION, AND THE CODE SAYS SO
# ---------------------------------------------------------------------------

def _mkt(slug, start=None, teams=(), provs=(), mtype="SPORTS_MARKET_TYPE_PROP",
         title=None):
    sides = []
    for i, t in enumerate(teams):
        sides.append({"teamId": t, "team": {
            "id": t, "name": "T%s" % t,
            "providerIds": [{"provider": "PROVIDER_SPORTRADAR",
                             "providerId": p} for p in (provs[i:i + 1] or ())]}})
    while len(sides) < 2:
        sides.append({"description": "Yes"})
    return {"slug": slug, "gameStartTime": start, "marketSides": sides,
            "sportsMarketTypeV2": mtype, "title": title or slug}


class TheDerivedKeyIsLabelledAsUnvalidated(unittest.TestCase):
    """The count 1,456 is a provisional heuristic, not a measurement."""

    def test_the_method_is_named_a_heuristic(self):
        self.assertEqual(E.EVENT_KEY_METHOD, "DERIVED_HEURISTIC_V1")

    def test_the_key_is_not_validated(self):
        self.assertFalse(E.EVENT_KEY_VALIDATED)

    def test_the_1456_count_is_labelled_provisional(self):
        self.assertEqual(E.UNIQUE_EVENTS_1456, "PROVISIONAL_HEURISTIC_COUNT")

    def test_the_independent_sample_size_is_not_identified(self):
        self.assertEqual(E.INDEPENDENT_SAMPLE_SIZE, NI)

    def test_the_order_of_magnitude_claim_is_retracted(self):
        self.assertEqual(E.SAMPLE_OVERSTATEMENT_MAGNITUDE, NI)

    def test_the_broad_conclusion_survives_the_retraction(self):
        # The direction is kept; only the quantity is withdrawn. Retracting a
        # number is not licence to swing to the opposite error.
        self.assertTrue(E.MARKETS_ARE_NOT_INDEPENDENT)
        self.assertTrue(E.DIFFERENT_SLUGS_DO_NOT_IMPLY_INDEPENDENT_EVENTS)

    def test_no_source_line_states_the_order_of_magnitude_claim(self):
        # Structural, not a substring scan for the phrase itself: the file is
        # allowed to RECORD that the claim was retracted. What it may not do is
        # bind a live name to a magnitude.
        import ast
        tree = ast.parse(open(E.__file__).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if (isinstance(t, ast.Name)
                            and "OVERSTATEMENT" in t.id
                            and isinstance(node.value, ast.Constant)):
                        self.assertEqual(node.value.value, NI)


class TheTwoKeysAreSeparateFields(unittest.TestCase):
    """MARKET_FAMILY_KEY and UNDERLYING_EVENT_KEY are different claims."""

    def test_the_family_key_exists_and_is_the_old_derivation(self):
        self.assertEqual(E.market_family_key("aec-ufc-alomen-iwobar-2026-09-19"),
                         "ufc-alomen-iwobar-2026-09-19")
        self.assertEqual(
            E.market_family_key("astatc-ufc-alomen-iwobar-2026-09-19-mof-ko"),
            "ufc-alomen-iwobar-2026-09-19")

    def test_event_key_is_kept_as_an_alias_so_no_caller_changes_meaning(self):
        self.assertIs(E.event_key, E.market_family_key)

    def test_a_proved_single_contest_reaches_level_b(self):
        ms = [_mkt("aec-nfl-det-buf-2026-09-17", "2026-09-17T00:20:00Z",
                   teams=(1, 2), provs=("u1", "u2")) for _ in range(5)]
        key, level = E.underlying_event_key(ms)
        self.assertEqual(level, E.IDENTITY_LEVEL_B)
        self.assertNotEqual(key, NI)

    def test_a_season_family_is_level_c_and_yields_no_event_key(self):
        # The `nfl-2027-01-10` shape: one naming block, 32 participant sets.
        ms = [_mkt("tec-nfl-2027-01-10-t%d" % i, "2026-09-10T00:00:00Z",
                   teams=(i,), provs=("u%d" % i,),
                   mtype="SPORTS_MARKET_TYPE_FUTURE") for i in range(32)]
        key, level = E.underlying_event_key(ms)
        self.assertEqual(level, E.IDENTITY_LEVEL_C)
        self.assertEqual(key, NI)

    def test_a_family_label_is_never_returned_dressed_as_an_event_id(self):
        for ms in ([_mkt("tec-cfb-wins-2026-11-28-a",
                         "2026-08-27T16:00:00Z",
                         mtype="SPORTS_MARKET_TYPE_FUTURE")],
                   [_mkt("x-2026-01-01")], []):
            key, level = E.underlying_event_key(ms)
            if level != E.IDENTITY_LEVEL_B:
                self.assertEqual(key, NI)

    def test_a_missing_start_time_on_every_row_is_not_one_start_time(self):
        # Caught by the roster-collapse test rather than by reading the code:
        # `{None}` has length 1, so a naive check promoted a cluster with NO
        # start time at all to level B.
        ms = [_mkt("aec-nfl-a-b-2026-09-17", None, teams=(1, 2),
                   provs=("u1", "u2")) for _ in range(4)]
        key, level = E.underlying_event_key(ms)
        self.assertNotEqual(level, E.IDENTITY_LEVEL_B)
        self.assertEqual(key, NI)

    def test_differing_start_times_alone_break_level_b(self):
        ms = [_mkt("aec-nfl-a-b-2026-09-17", "2026-09-17T00:00:00Z",
                   teams=(1, 2), provs=("u1", "u2")),
              _mkt("aec-nfl-a-b-2026-09-17", "2026-09-17T19:00:00Z",
                   teams=(1, 2), provs=("u1", "u2"))]
        _, level = E.underlying_event_key(ms)
        self.assertNotEqual(level, E.IDENTITY_LEVEL_B)


class ThePurityTest(unittest.TestCase):
    """Absence of evidence is UNKNOWN, never PURE."""

    def test_an_over_merged_cluster_is_caught(self):
        ms = [_mkt("tec-nfl-2027-01-10-t%d" % i, "2026-09-10T00:00:00Z",
                   teams=(i,), provs=("u%d" % i,),
                   mtype="SPORTS_MARKET_TYPE_FUTURE") for i in range(32)]
        out = E.cluster_purity(ms)
        self.assertEqual(out["OVERMERGED_CLUSTERS"], 1)
        self.assertEqual(out["MARKETS_IN_OVERMERGED_CLUSTERS"], 32)
        self.assertEqual(out["ROWS"][0]["DISTINCT_PARTICIPANT_PAIRS"], 32)

    def test_a_real_game_cluster_is_pure(self):
        ms = [_mkt("aec-nfl-det-buf-2026-09-17", "2026-09-17T00:20:00Z",
                   teams=(1, 2), provs=("u1", "u2")) for _ in range(9)]
        out = E.cluster_purity(ms)
        self.assertEqual(out["PURE_CLUSTERS"], 1)
        self.assertEqual(out["OVERMERGED_CLUSTERS"], 0)

    def test_a_cluster_with_no_identity_evidence_is_unknown_not_pure(self):
        ms = [_mkt("tec-cfb-wins-2026-11-28-%d" % i, "2026-08-27T16:00:00Z",
                   mtype="SPORTS_MARKET_TYPE_FUTURE") for i in range(6)]
        out = E.cluster_purity(ms)
        self.assertEqual(out["UNKNOWN_CLUSTERS"], 1)
        self.assertEqual(out["PURE_CLUSTERS"], 0)

    def test_both_over_merge_rates_are_reported(self):
        ms = ([_mkt("tec-nfl-2027-01-10-t%d" % i, "2026-09-10T00:00:00Z",
                    teams=(i,), provs=("u%d" % i,),
                    mtype="SPORTS_MARKET_TYPE_FUTURE") for i in range(32)]
              + [_mkt("aec-nfl-det-buf-2026-09-17", "2026-09-17T00:20:00Z",
                      teams=(1, 2), provs=("u1", "u2"))])
        out = E.cluster_purity(ms)
        self.assertEqual(out["CLUSTERS_TESTED"], 2)
        self.assertAlmostEqual(out["OVERMERGE_RATE_BY_CLUSTER"], 0.5)
        self.assertAlmostEqual(out["OVERMERGE_RATE_BY_MARKET"], 32 / 33)
        # By cluster and by market are DIFFERENT numbers and neither stands in
        # for the other.
        self.assertNotAlmostEqual(out["OVERMERGE_RATE_BY_CLUSTER"],
                                  out["OVERMERGE_RATE_BY_MARKET"])

    def test_markets_with_no_key_are_counted_not_dropped_silently(self):
        out = E.cluster_purity([_mkt("no-date-here")])
        self.assertEqual(out["MARKETS_WITH_NO_DERIVABLE_KEY"], 1)
        self.assertEqual(out["CLUSTERS_TESTED"], 0)


class TheEvidenceHierarchyIsFourLevels(unittest.TestCase):

    def test_all_four_levels_are_named(self):
        self.assertEqual(len(E.IDENTITY_LEVELS), 4)
        for lvl in E.IDENTITY_LEVELS:
            self.assertTrue(lvl[0] in "ABCD" and lvl[1] == "_")

    def test_level_a_is_zero_on_this_venue_and_recorded_as_such(self):
        self.assertEqual(E.OBSERVED_PREFIX_LEVEL_A_CLUSTERS, 0)
        self.assertEqual(E.VENUE_EVENT_IDENTIFIER_PRESENT, "NO")

    def test_an_explicit_venue_identifier_would_be_level_a(self):
        # The probe is live, not decorative: ship the field and it is used.
        ms = [{"slug": "x-2026-01-01", "eventId": "EV-7",
               "marketSides": []} for _ in range(3)]
        key, level = E.underlying_event_key(ms)
        self.assertEqual(level, E.IDENTITY_LEVEL_A)
        self.assertEqual(key, "EV-7")

    def test_the_observed_prefix_counts_are_recorded(self):
        self.assertEqual(E.OBSERVED_PREFIX_LEVEL_B_CLUSTERS, 72)
        self.assertEqual(E.OBSERVED_PREFIX_LEVEL_B_MARKETS, 4783)
        self.assertEqual(E.OBSERVED_PREFIX_NO_KEY_MARKETS, 487)


class BoundsNotAPointEstimate(unittest.TestCase):

    def test_the_exact_count_is_not_identified(self):
        self.assertEqual(E.EXACT_INDEPENDENT_EVENT_N, NI)

    def test_the_bounds_bracket_the_retracted_heuristic_count(self):
        self.assertLess(E.EVENT_COUNT_LOWER_BOUND, E.EVENT_COUNT_UPPER_BOUND)
        self.assertLessEqual(E.EVENT_COUNT_LOWER_BOUND, 1456)
        self.assertGreaterEqual(E.EVENT_COUNT_UPPER_BOUND, 1456)

    def test_the_upper_bound_is_flagged_as_a_ceiling(self):
        # Retracting 1,456 must NOT become "every market is independent".
        self.assertTrue(E.EVENT_COUNT_UPPER_BOUND_IS_A_CEILING_NOT_AN_ESTIMATE)

    def test_the_proven_count_is_smaller_than_both_bounds(self):
        self.assertLess(E.PROVEN_DISTINCT_EVENT_N, E.EVENT_COUNT_LOWER_BOUND)

    def test_bounds_are_computed_not_just_declared(self):
        ms = ([_mkt("aec-nfl-det-buf-2026-09-17", "2026-09-17T00:20:00Z",
                    teams=(1, 2), provs=("u1", "u2")) for _ in range(9)]
              + [_mkt("tec-nfl-2027-01-10-t%d" % i, "2026-09-10T00:00:00Z",
                      teams=(i,), provs=("u%d" % i,),
                      mtype="SPORTS_MARKET_TYPE_FUTURE") for i in range(4)])
        out = E.event_count_bounds(ms)
        self.assertEqual(out["PROVEN_DISTINCT_EVENT_N"], 1)
        self.assertEqual(out["EXACT_INDEPENDENT_EVENT_N"], NI)
        self.assertEqual(out["EVENT_COUNT_LOWER_BOUND"], 2)
        # 1 proved cluster + the 4 unproven futures markets presumed distinct.
        self.assertEqual(out["EVENT_COUNT_UPPER_BOUND"], 5)
        self.assertLess(out["EVENT_COUNT_LOWER_BOUND"],
                        out["EVENT_COUNT_UPPER_BOUND"])

    def test_two_keys_on_one_roster_and_date_collapse_but_only_when_proved(self):
        # Same league token, same settlement date, identical roster -> proved.
        proved = [_mkt("tec-nfl-2027-01-10-t%d" % i, None, teams=(1, 2),
                       provs=("u1", "u2"), mtype="SPORTS_MARKET_TYPE_FUTURE")
                  for i in range(2)]
        proved += [_mkt("tec-nfl-wins-2027-01-10-t%d" % i, None, teams=(1, 2),
                        provs=("u1", "u2"), mtype="SPORTS_MARKET_TYPE_FUTURE")
                   for i in range(2)]
        self.assertEqual(
            E.event_count_bounds(proved)["KEYS_REMOVED_BY_PROVED_COLLAPSE"], 1)

    def test_a_shared_league_and_date_alone_never_collapses(self):
        # The ushrmov trap: `ushrmov-al-01-2026-11-03` and
        # `ushrmov-al-03-2026-11-03` share a league token and a date and are
        # DIFFERENT CONTESTS. With no team ids to test, nothing collapses.
        rows = [_mkt("paccc-ushrmov-al-01-2026-11-03-%d" % i,
                     mtype="SPORTS_MARKET_TYPE_FUTURE") for i in range(3)]
        rows += [_mkt("paccc-ushrmov-al-03-2026-11-03-%d" % i,
                      mtype="SPORTS_MARKET_TYPE_FUTURE") for i in range(3)]
        out = E.event_count_bounds(rows)
        self.assertEqual(out["KEYS_REMOVED_BY_PROVED_COLLAPSE"], 0)
        self.assertEqual(out["EVENT_COUNT_LOWER_BOUND"], 2)


class EventWeightedInferenceIsOff(unittest.TestCase):

    def test_calling_by_event_without_a_validated_key_raises(self):
        with self.assertRaises(E.EventKeyNotValidated):
            E.by_event([{"slug": "aec-nfl-a-b-2026-09-19", "X": True}], "X")

    def test_a_level_c_key_does_not_unlock_it(self):
        with self.assertRaises(E.EventKeyNotValidated):
            E.by_event([], "X", level=E.IDENTITY_LEVEL_C)
        with self.assertRaises(E.EventKeyNotValidated):
            E.by_event([], "X", level=E.IDENTITY_LEVEL_D)

    def test_the_refusal_names_why(self):
        try:
            E.by_event([], "X")
        except E.EventKeyNotValidated as exc:
            self.assertIn("EVENT_WEIGHTED_INFERENCE_DISABLED", str(exc))
            self.assertIn("DERIVED_HEURISTIC_V1", str(exc))
            self.assertIn(NI, str(exc))

    def test_a_level_b_key_switches_it_back_on(self):
        # The weighting machinery is intact; the defect was the key.
        out = E.by_event([{"slug": "aec-nfl-a-b-2026-09-19", "X": True}], "X",
                         level=E.IDENTITY_LEVEL_B)
        self.assertEqual(out["UNIQUE_EVENT_N"], 1)
        self.assertEqual(out["IDENTITY_LEVEL"], E.IDENTITY_LEVEL_B)

    def test_inference_status_reports_unvalidated_not_underpowered(self):
        # A big count does not repair an unvalidated key, and "UNDERPOWERED"
        # would wrongly imply the identity was sound and only the size small.
        self.assertEqual(E.inference_status(99999), "EVENT_IDENTITY_UNVALIDATED")
        self.assertEqual(E.inference_status(2), "EVENT_IDENTITY_UNVALIDATED")


class OpportunityIsNotIndependentCapacity(unittest.TestCase):

    def test_capacity_returns_both_names_and_conflates_neither(self):
        rows = [{"slug": "aec-nfl-a-b-2026-09-19-p%d" % i,
                 "HIGH_ACTIVITY_AT_DECISION": True} for i in range(30)]
        cap = E.capacity(rows)
        self.assertEqual(cap["OPPORTUNITY_COUNT"], 30)
        self.assertEqual(cap["INDEPENDENT_CAPACITY"], NI)
        self.assertEqual(cap["INDEPENDENT_CAPACITY_LOWER_BOUND"], 1)
        self.assertEqual(cap["INDEPENDENT_CAPACITY_UPPER_BOUND"], 30)

    def test_capacity_reports_the_key_is_unvalidated(self):
        self.assertFalse(E.capacity([])["EVENT_KEY_VALIDATED"])


class TheAuditTimingCheckMeasuresElapsedTime(unittest.TestCase):
    """A fair POSITION is not a measurement of a fair TIME."""

    def _obs(self, routed, audit):
        return ([{"lane": "ROUTED", "elapsed_s": e} for e in routed]
                + [{"lane": "AUDIT", "elapsed_s": e} for e in audit])

    def test_the_percentiles_are_reported_for_both_lanes(self):
        out = E.audit_elapsed_report(self._obs(range(0, 100), range(0, 100, 5)))
        for k in ("ROUTED_ELAPSED_P10", "ROUTED_ELAPSED_P50",
                  "ROUTED_ELAPSED_P90", "AUDIT_ELAPSED_P10",
                  "AUDIT_ELAPSED_P50", "AUDIT_ELAPSED_P90"):
            self.assertIn(k, out)
            self.assertNotEqual(out[k], NI)

    def test_fairness_is_not_asserted_from_the_schedule(self):
        out = E.audit_elapsed_report([])
        self.assertFalse(out["SCHEDULE_FAIRNESS_ASSERTED_FROM_POSITION"])
        self.assertTrue(out["ELAPSED_MEASURED"])

    def test_an_interleaved_scan_reads_as_comparable(self):
        out = E.audit_elapsed_report(self._obs(range(0, 100), range(0, 100, 4)))
        self.assertTrue(out["TIMING_COMPARABLE"])

    def test_an_audit_lane_read_late_is_caught_despite_a_fair_position(self):
        # This is precisely what asserting first/last thirds could not see: a
        # stall pushes the audit lane's WALL-CLOCK late even from a fair slot.
        out = E.audit_elapsed_report(self._obs(range(0, 100), range(80, 100)))
        self.assertFalse(out["TIMING_COMPARABLE"])
        self.assertIn("confounded with scan drift", out["TIMING_DEFECT"])

    def test_an_empty_lane_is_not_identified_rather_than_comparable(self):
        out = E.audit_elapsed_report(self._obs(range(0, 10), []))
        self.assertEqual(out["AUDIT_ELAPSED_P50"], NI)
        self.assertEqual(out["TIMING_COMPARABLE"], NI)

    def test_the_schedule_and_the_report_are_different_functions(self):
        sched = E.audit_schedule(["a", "b"], ["c"], "salt")
        self.assertEqual(len(sched), 3)
        self.assertEqual({r["lane"] for r in sched}, {"ROUTED", "AUDIT"})
