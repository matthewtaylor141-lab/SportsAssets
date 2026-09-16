#!/usr/bin/env python3
"""The Phase-2A harvest. Says NO plainly when the evidence is not there."""
import ast
import gzip
import json
import tempfile
import unittest
from pathlib import Path

import harvest as H

SRC = Path(__file__).resolve().parent / "harvest.py"
TREE = ast.parse(SRC.read_text())

NI = H.NOT_IDENTIFIED


def rows(n=40, slug="aec-nfl-a-b", traded_step=5):
    out = []
    for i in range(n):
        r = {"kind": "TICK", "slug": slug, "seq": i, "ELAPSED_S": float(i * 3),
             "RECEIPT_UTC": "2026-09-16T16:%02d:00Z" % (i % 60),
             "BID": "0.54", "ASK": "0.56", "BID_QTY": "100", "ASK_QTY": "80",
             "SPREAD": "0.02", "BID_LADDER": [["0.54", "100"]],
             "ASK_LADDER": [["0.56", "80"]],
             "SHARES_TRADED": str(1000 + i * traded_step),
             "DEPTH_LEVELS_BID": 1, "DEPTH_LEVELS_ASK": 1, "STATE": "OPEN"}
        if i:
            r["SHARES_TRADED_DELTA"] = str(traded_step)
            r["BID_CHANGED"] = False
            r["ASK_CHANGED"] = False
            r["DEPTH_CHANGED"] = False
            r["TIME_AT_BID_S"] = float(i * 3)
            r["TIME_AT_ASK_S"] = float(i * 3)
            r["QUOTE_LIFETIME_S"] = float(i * 3)
        out.append(r)
    return out


def write(rs, gz=False):
    d = Path(tempfile.mkdtemp())
    p = d / ("ticks.jsonl.gz" if gz else "ticks.jsonl")
    op = gzip.open if gz else open
    with op(p, "wt") as fh:
        for r in rs:
            fh.write(json.dumps(r) + "\n")
    return p


class TheHarvestReadsFilesAndNothingElse(unittest.TestCase):

    def test_no_http_client_is_imported(self):
        bad = {"httpx", "requests", "urllib", "urllib3", "http", "socket",
               "aiohttp"}
        for n in ast.walk(TREE):
            if isinstance(n, ast.Import):
                for a in n.names:
                    self.assertNotIn(a.name.split(".")[0], bad, a.name)
            if isinstance(n, ast.ImportFrom) and n.module:
                self.assertNotIn(n.module.split(".")[0], bad, n.module)

    def test_it_reads_plain_and_gzipped_captures(self):
        for gz in (False, True):
            self.assertEqual(len(H.read_capture(write(rows(5), gz=gz))), 5)

    def test_a_malformed_line_is_skipped_not_fatal(self):
        p = write(rows(3))
        with open(p, "a") as fh:
            fh.write("{not json\n")
        self.assertEqual(len(H.read_capture(p)), 3)


class TheLadderIsFourSeparateCounts(unittest.TestCase):

    def setUp(self):
        self.r = H.harvest(write(rows(40)))

    def test_quotes_are_evaluated_and_counted(self):
        self.assertEqual(self.r["HYPOTHETICAL_QUOTES"], 4)

    def test_no_fill_is_admitted_from_ticks(self):
        self.assertEqual(self.r["ADMITTED_COUNTERFACTUAL_FILLS"], 0)

    def test_trade_evidence_is_not_identified_from_ticks(self):
        self.assertEqual(self.r["TRADE_EVIDENCE"], 0)

    def test_every_unresolved_quote_carries_a_reason(self):
        w = self.r["D_EXECUTION_IDENTIFICATION"]["WHY_UNRESOLVED"]
        self.assertEqual(sum(w.values()), self.r["HYPOTHETICAL_QUOTES"])

    def test_a_quiet_market_lands_in_the_bound_insufficient_bucket(self):
        r = H.harvest(write(rows(40, traded_step=0)))
        self.assertEqual(r["D_EXECUTION_IDENTIFICATION"]["WHY_UNRESOLVED"]["VOLUME_BOUND_INSUFFICIENT"],
                         r["HYPOTHETICAL_QUOTES"])
        self.assertEqual(r["ADMITTED_COUNTERFACTUAL_FILLS"], 0)


class TheBottleneckIsStatedPlainly(unittest.TestCase):

    def test_it_answers_no_and_says_why(self):
        r = H.harvest(write(rows(40)))
        self.assertEqual(
            r["PUBLIC_TICK_DATA_SUFFICIENT_FOR_FILL_IDENTIFICATION"], "NO")
        d = r["D_EXECUTION_IDENTIFICATION"]
        self.assertIn("no execution evidence", d["WHY"])
        self.assertEqual(d["CONCLUSION"],
                         "PUBLIC_BOOK_SNAPSHOTS_CANNOT_IDENTIFY_FILL")
        self.assertEqual(d["NOT_THE_CONCLUSION"],
                         "WE_NEED_A_MORE_AGGRESSIVE_APPROXIMATION")

    def test_it_names_the_evidence_that_would_settle_it(self):
        r = H.harvest(write(rows(10)))
        self.assertIn("PUBLIC_EXECUTION_TAPE_WITH_TIMESTAMP_PRICE_QUANTITY",
                      r["D_EXECUTION_IDENTIFICATION"]["EVIDENCE_THAT_WOULD_IDENTIFY_A_FILL"])
        self.assertIn("MICRO-LIVE",
                      r["D_EXECUTION_IDENTIFICATION"]["NEXT_STEP"])

    def test_it_refuses_another_approximation_by_name(self):
        r = H.harvest(write(rows(10)))
        self.assertTrue(r["D_EXECUTION_IDENTIFICATION"]
                        ["DO_NOT_INVENT_ANOTHER_FILL_APPROXIMATION"])


class TheFieldAndTheBookAreBothReported(unittest.TestCase):

    def setUp(self):
        self.r = H.harvest(write(rows(40)))

    def test_the_two_halves_of_the_field_question_stay_apart(self):
        self.assertIn("MONOTONIC_WITHIN_MARKET", self.r["SHARES_TRADED_RUNTIME"])
        for v in self.r["SHARES_TRADED_VENUE_SEMANTICS"].values():
            self.assertEqual(v, NI)
        self.assertTrue(self.r["RUNTIME_BEHAVIOUR_IS_NOT_VENUE_SEMANTICS"])

    def test_the_status_stays_diagnostic_only(self):
        self.assertEqual(self.r["SHARES_TRADED_DELTA_STATUS"],
                         "CONSERVATIVE_DIAGNOSTIC_ONLY")

    def test_the_book_half_is_measured(self):
        b = self.r["B_BOOK_STRUCTURE"]
        self.assertEqual(b["MARKETS"], 1)
        self.assertNotEqual(b["ONE_TICK_SNAPSHOT_SHARE"], NI)
        self.assertEqual(b["TRUE_TIME_WEIGHTED_ONE_TICK_UPTIME"], NI)
        self.assertEqual(b["CLASS_B_SAMPLING_BIAS_DIRECTION"], NI)
        self.assertEqual(b["CLASS_A_SAMPLING_BIAS_DIRECTION"], "UNDERCOUNT")
        self.assertNotEqual(b["SPREAD_P50"], NI)
        self.assertTrue(b["FILL_RATE_NOT_PRESENT"])

    def test_the_capture_shape_is_reported(self):
        a = self.r["A_CAPTURE_QUALITY"]
        self.assertEqual(a["ROWS"], 40)
        self.assertEqual(a["MARKETS"], 1)
        self.assertEqual(a["SPORT_MIX"], {"nfl": 1})

    def test_the_forbidden_figures_are_absent_as_numbers(self):
        for k in ("PROFITABILITY", "WIN_RATE", "EXPECTED_MONTHLY_RETURN"):
            self.assertEqual(self.r[k], NI, k)
        self.assertEqual(self.r["ORDERS_PLACED"], 0)
        self.assertFalse(self.r["mirror_live"])


if __name__ == "__main__":
    unittest.main()


UNIVERSE = {
    "SELECTION": "HIGH_ACTIVITY at decision, stratified, frozen salted hash",
    "SELECTION_SALT": "BETA48-SHADOW-2026-09-16",
    "PER_STRATUM_SELECTED": {"nfl": 6, "cfb": 6, "mlb": 6, "ufc": 6},
    "AVAILABLE_PER_STRATUM": {"nfl": 327, "cfb": 120, "mlb": 61, "ufc": 25},
    "SOURCE_CENSUS_RUN": "35105863528",
    "FROZEN_BEFORE_ANY_TICK_WAS_SEEN": True,
    "NOT_SELECTED_BY_ACTIVITY_RANK": True,
}


def write_universe(u=None):
    d = Path(tempfile.mkdtemp())
    p = d / "tick_universe.json"
    p.write_text(json.dumps(u if u is not None else UNIVERSE))
    return p


class TwentyFourMarketsAreNotSevenEightyEight(unittest.TestCase):

    def setUp(self):
        self.a = H.harvest(write(rows(20)),
                           universe_path=write_universe())["A_CAPTURE_QUALITY"]
        self.s = self.a["SAMPLE_SCOPE"]

    def test_both_counts_are_stated_side_by_side(self):
        self.assertEqual(self.s["CANDIDATE_UNIVERSE"], 788)
        self.assertEqual(self.s["CAPTURED_MARKETS"], 1)

    def test_the_selection_rule_is_recorded_in_full(self):
        for k in ("CAPTURE_SELECTION_RULE", "SPORT_STRATIFICATION",
                  "EVENT_STRATIFICATION", "SELECTION_TIMESTAMP",
                  "SELECTION_FROZEN_BEFORE_CAPTURE"):
            self.assertNotEqual(self.s[k], NI, k)
        self.assertEqual(self.s["SELECTION_FROZEN_BEFORE_CAPTURE"], "YES")
        self.assertEqual(self.s["EVENT_STRATIFICATION"], "NONE_APPLIED")

    def test_frozen_and_unrigged_is_not_representative(self):
        self.assertEqual(self.s["CAPTURE_SAMPLE_REPRESENTATIVE_OF_788"],
                         "NOT_ESTABLISHED")
        self.assertEqual(self.s["EXTRAPOLATION_TO_THE_UNIVERSE"],
                         "NOT_PERFORMED")
        self.assertTrue(self.s["ANY_PERCENTAGE_HERE_DESCRIBES_THE_24"])

    def test_without_a_universe_file_the_rule_is_not_invented(self):
        s = H.harvest(write(rows(5)))["A_CAPTURE_QUALITY"]["SAMPLE_SCOPE"]
        self.assertEqual(s["CAPTURE_SELECTION_RULE"], NI)
        self.assertEqual(s["SELECTION_FROZEN_BEFORE_CAPTURE"], NI)
        self.assertEqual(s["CAPTURE_SAMPLE_REPRESENTATIVE_OF_788"],
                         "NOT_ESTABLISHED")


class MarketsFromOneEventAreNotIndependent(unittest.TestCase):

    def _capture(self):
        rs = []
        for slug in ("aec-nfl-a-b", "tsc-nfl-a-b-total", "aec-cfb-c-d"):
            rs.extend(rows(6, slug=slug))
        return write(rs)

    def test_the_clustering_is_reported(self):
        r = H.harvest(self._capture(),
                      event_of={"aec-nfl-a-b": "E1", "tsc-nfl-a-b-total": "E1",
                                "aec-cfb-c-d": "E2"})
        a = r["A_CAPTURE_QUALITY"]
        self.assertEqual(a["MARKETS"], 3)
        self.assertEqual(a["VALIDATED_DISTINCT_EVENTS"], 2)
        self.assertEqual(a["MARKETS_PER_EVENT"]["MAX"], 2)
        self.assertTrue(a["MARKETS_FROM_ONE_EVENT_ARE_NOT_INDEPENDENT"])
        self.assertFalse(a["MARKETS_TREATED_AS_INDEPENDENT"])

    def test_the_book_rates_come_back_in_both_weightings(self):
        r = H.harvest(self._capture(),
                      event_of={"aec-nfl-a-b": "E1", "tsc-nfl-a-b-total": "E1",
                                "aec-cfb-c-d": "E2"})
        b = r["B_BOOK_STRUCTURE"]
        self.assertEqual(b["WEIGHTING"], "MARKET_WEIGHTED")
        self.assertEqual(b["EVENTS_IN_WEIGHTING"], 2)
        self.assertIn("BOOK_UPDATE_RATE_OBSERVED_EVENT_WEIGHTED", b)

    def test_without_an_event_map_independence_is_not_assumed(self):
        a = H.harvest(self._capture())["A_CAPTURE_QUALITY"]
        self.assertEqual(a["VALIDATED_DISTINCT_EVENTS"], NI)
        self.assertFalse(a["MARKETS_TREATED_AS_INDEPENDENT"])
        self.assertEqual(a["MARKETS_WITH_UNRESOLVED_EVENT_IDENTITY"], 3)

    def test_an_event_count_is_still_not_a_capacity(self):
        r = H.harvest(self._capture(), event_of={"aec-nfl-a-b": "E1"})
        self.assertEqual(
            r["A_CAPTURE_QUALITY"]["DEPLOYABLE_CAPITAL_CAPACITY"], NI)


class TheQuotePathSectionIsAPricePath(unittest.TestCase):

    def test_move_through_is_reported_and_labelled(self):
        c = H.harvest(write(rows(20)))["C_HYPOTHETICAL_QUOTE_PATH"]
        self.assertIn("MOVE_THROUGH_OBSERVED", c)
        self.assertTrue(c["MOVE_THROUGH_IS_NOT_TRADE"])
        self.assertTrue(c["MOVE_THROUGH_IS_NOT_A_COUNTERFACTUAL_FILL"])

    def test_the_markouts_travel_with_it(self):
        c = H.harvest(write(rows(20)))["C_HYPOTHETICAL_QUOTE_PATH"]
        self.assertTrue(any(k.startswith("POST_QUOTE_BOOK_MARKOUT")
                            for k in c["POST_QUOTE_BOOK_MARKOUTS"]))


class TheCadenceIsReportedBeforeAnythingDerivedFromIt(unittest.TestCase):

    def test_section_a_carries_the_revisit_distribution(self):
        a = H.harvest(write(rows(20)))["A_CAPTURE_QUALITY"]
        c = a["REVISIT_CADENCE"]
        for k in ("MEDIAN_REVISIT_INTERVAL_S", "P10_REVISIT_INTERVAL_S",
                  "P90_REVISIT_INTERVAL_S", "MAX_REVISIT_INTERVAL_S"):
            self.assertNotEqual(c[k], NI, k)
        self.assertTrue(c["SAMPLING_IS_NOT_CONTINUOUS_OBSERVATION"])

    def test_duration_and_missingness_are_reported(self):
        a = H.harvest(write(rows(20)))["A_CAPTURE_QUALITY"]
        self.assertEqual(a["DURATION_S"], 57.0)
        self.assertIn("TICK_ERRORS", a["MISSINGNESS"])

    def test_realized_maker_economics_stay_unestablished(self):
        r = H.harvest(write(rows(20)))
        self.assertEqual(r["REALIZED_MAKER_ECONOMICS"], "NOT_ESTABLISHED")
