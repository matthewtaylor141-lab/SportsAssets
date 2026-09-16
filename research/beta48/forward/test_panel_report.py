#!/usr/bin/env python3
"""C-8. The panel's observation unit, pinned so the duplication cannot return.

THE DEFECT. The venue runs several concurrent liquidity programmes on ONE
market -- a UFC fight carries moneyline early/day_of/live at target 20,000 AND
props day_of/live at target 1,000, all active at the same instant. The first
panel emitted ONE ROW PER PROGRAMME, so the same book snapshot was fetched and
recorded up to five times, and the report counted each copy as an independent
observation. Every market then entered the statistics weighted by how many
programmes it happened to carry.

That is the same failure as C-5 (a rotating token recorded 267 periods 200
times) pointed at a different join, and it is the fourth time in this
programme that a duplication guard existed in one place and was missing in
another. These tests are the guard in the place it was missing.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import panel_report as R


def _book(bid_qty="100", ask_qty="100"):
    return {"marketData": {
        "bids": [{"px": {"value": "0.3200"}, "qty": bid_qty},
                 {"px": {"value": "0.3100"}, "qty": "50"}],
        "offers": [{"px": {"value": "0.3300"}, "qty": ask_qty},
                   {"px": {"value": "0.3400"}, "qty": "50"}],
        "stats": {"sharesTraded": "10", "lastTradeSetTime": "T0",
                  "openInterest": "5"}}}


def _old_shape(slug, rnd, target, program_id, mono=1):
    """A row as segments before the fix wrote them: one per PROGRAMME."""
    return {"kind": "PANEL", "round": rnd, "slug": slug, "tick": 0.01,
            "PROGRAM_TYPE": "liquidityProgram", "PROGRAM_ID": program_id,
            "REWARD_POOL": 500, "TARGET_SIZE": target,
            "DISCOUNT_FACTOR": 0.3, "PROGRAM_PERIOD": "live",
            "INSTRUMENT_STATE": "INSTRUMENT_STATE_OPEN",
            "leg": {"http_status": 200, "body": _book(),
                    "local_request_monotonic_ns": mono,
                    "local_request_wall_utc": "2026-09-16T02:00:00+00:00"}}


def _new_shape(slug, rnd, targets, mono=1):
    """A row as the fixed collector writes them: one per MARKET."""
    return {"kind": "PANEL", "round": rnd, "slug": slug, "tick": 0.01,
            "PROGRAMS": [{"PROGRAM_TYPE": "liquidityProgram",
                          "PROGRAM_ID": "p%s" % t, "REWARD_POOL": 500,
                          "TARGET_SIZE": t, "DISCOUNT_FACTOR": 0.3,
                          "PROGRAM_PERIOD": "live",
                          "INSTRUMENT_STATE": "INSTRUMENT_STATE_OPEN"}
                         for t in targets],
            "PROGRAMS_N": len(targets),
            "DISTINCT_TARGET_SIZES": sorted(targets),
            "leg": {"http_status": 200, "body": _book(),
                    "local_request_monotonic_ns": mono,
                    "local_request_wall_utc": "2026-09-16T02:00:00+00:00"}}


class OneBookReadIsOneObservation(unittest.TestCase):

    def test_five_programme_rows_for_one_book_are_not_five_observations(self):
        rows = [_old_shape("s", 0, 20000, "ml_early"),
                _old_shape("s", 0, 20000, "ml_dayof"),
                _old_shape("s", 0, 20000, "ml_live"),
                _old_shape("s", 0, 1000, "props_dayof"),
                _old_shape("s", 0, 1000, "props_live")]
        scored = R.score_rows(rows)
        self.assertEqual(len(scored), 2, "one per DISTINCT target, not per "
                                         "programme row")
        self.assertEqual(sorted(r["TARGET_SIZE"] for r in scored),
                         [1000, 20000])

    def test_the_new_row_shape_gives_the_same_answer_as_the_old(self):
        """The correction has to apply to evidence already on disk, so both
        shapes must read identically. A fix that only works on data not yet
        captured leaves the wrong number standing in the segments we have."""
        old = R.score_rows([_old_shape("s", 0, 20000, "a"),
                            _old_shape("s", 0, 20000, "b"),
                            _old_shape("s", 0, 1000, "c")])
        new = R.score_rows([_new_shape("s", 0, [20000, 1000])])
        key = "BID_QUOTE_1_TICKS_BACK_SCORE_ELIGIBILITY"
        self.assertEqual(sorted((r["TARGET_SIZE"], r[key]) for r in old),
                         sorted((r["TARGET_SIZE"], r[key]) for r in new))


class TargetSizesAreNeverPooled(unittest.TestCase):

    def test_two_targets_are_summarised_separately(self):
        scored = R.score_rows([_new_shape("s", 0, [20000, 1000])])
        a = R.summarize([r for r in scored if r["TARGET_SIZE"] == 1000], 1000)
        b = R.summarize([r for r in scored if r["TARGET_SIZE"] == 20000], 20000)
        self.assertEqual(a["TARGET_SIZE"], 1000)
        self.assertEqual(b["TARGET_SIZE"], 20000)
        self.assertEqual(a["PANEL_OBSERVATIONS"], 1)
        self.assertEqual(b["PANEL_OBSERVATIONS"], 1)

    def test_eligibility_really_does_depend_on_the_target(self):
        """If it did not, pooling would be harmless and this whole correction
        would be pedantry. It is not: the same book qualifies at one target
        and does not at the other."""
        rows = [_new_shape("s", 0, [100, 100000])]
        scored = R.score_rows(rows)
        by = {r["TARGET_SIZE"]:
              r["BID_QUOTE_1_TICKS_BACK_SCORE_ELIGIBILITY"] for r in scored}
        self.assertNotEqual(by[100], by[100000], by)

    def test_the_summary_carries_both_the_unit_and_the_read_count(self):
        scored = R.score_rows([_new_shape("s", r, [1000]) for r in range(3)])
        out = R.summarize(scored, 1000)
        self.assertEqual(out["PANEL_BOOK_READS"], 3)
        self.assertEqual(out["PANEL_MARKETS"], 1)
        self.assertIn("PER_TARGET", out["OBSERVATION_UNIT"].replace(
            "PER_TARGET", "PER_TARGET"))


class GeneralizabilityIsStatedNotAssumed(unittest.TestCase):

    def test_a_single_event_family_panel_is_named_as_one(self):
        rows = [_new_shape("aec-ufc-a-b-2026-09-19", 0, [1000]),
                _new_shape("aec-ufc-c-d-2026-09-19", 0, [1000])]
        self.assertEqual(R.sport_concentration(rows), [("ufc", 2)])

    def test_a_mixed_panel_reports_its_mix(self):
        rows = [_new_shape("aec-ufc-a-b-2026-09-19", 0, [1000]),
                _new_shape("aec-nfl-c-d-2026-09-19", 0, [1000])]
        self.assertEqual(sorted(R.sport_concentration(rows)),
                         [("nfl", 1), ("ufc", 1)])


class TheStatsBlockIsNotPromotedToATape(unittest.TestCase):
    """The book payload carries a traded-shares counter. It is real evidence
    and it is NOT an aggressor-tagged tape; both halves are pinned."""

    def test_the_counter_is_read_and_the_interval_frequency_reported(self):
        rows = [_new_shape("s", 0, [1000], mono=0),
                _new_shape("s", 1, [1000], mono=30_000_000_000)]
        rows[1]["leg"]["body"]["marketData"]["stats"]["sharesTraded"] = "99"
        out = R.tape_observables(rows)
        self.assertEqual(out["TRADE_COUNTER_PRESENT_IN_BOOK_PAYLOAD"], "YES")
        self.assertEqual(out["CONSECUTIVE_SNAPSHOT_PAIRS"], 1)
        self.assertIn("1/1", out["ANY_TRADE_IN_INTERVAL_FREQUENCY"])

    def test_it_never_claims_aggressor_or_passive_fill(self):
        out = R.tape_observables([_new_shape("s", 0, [1000])])
        self.assertEqual(out["TRADE_AGGRESSOR_AVAILABLE"], "NO")
        self.assertEqual(out["PASSIVE_FILL_ATTRIBUTION_AVAILABLE"], "NO")
        self.assertEqual(out["TRADE_COUNTER_IS_A_TAPE"], "NO")

    def test_an_unchanged_counter_is_not_reported_as_a_trade(self):
        rows = [_new_shape("s", 0, [1000], mono=0),
                _new_shape("s", 1, [1000], mono=30_000_000_000)]
        out = R.tape_observables(rows)
        self.assertIn("0/1", out["ANY_TRADE_IN_INTERVAL_FREQUENCY"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
