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
        w = self.r["WHY_UNRESOLVED"]
        self.assertEqual(sum(w.values()), self.r["HYPOTHETICAL_QUOTES"])

    def test_a_quiet_market_lands_in_the_bound_insufficient_bucket(self):
        r = H.harvest(write(rows(40, traded_step=0)))
        self.assertEqual(r["WHY_UNRESOLVED"]["VOLUME_BOUND_INSUFFICIENT"],
                         r["HYPOTHETICAL_QUOTES"])
        self.assertEqual(r["ADMITTED_COUNTERFACTUAL_FILLS"], 0)


class TheBottleneckIsStatedPlainly(unittest.TestCase):

    def test_it_answers_no_and_says_why(self):
        r = H.harvest(write(rows(40)))
        self.assertEqual(
            r["PUBLIC_TICK_DATA_SUFFICIENT_FOR_FILL_IDENTIFICATION"], "NO")
        self.assertIn("no execution evidence", r["WHY"])

    def test_it_names_the_evidence_that_would_settle_it(self):
        r = H.harvest(write(rows(10)))
        self.assertIn("PUBLIC_EXECUTION_TAPE_WITH_TIMESTAMP_PRICE_QUANTITY",
                      r["EVIDENCE_THAT_WOULD_IDENTIFY_A_FILL"])
        self.assertIn("MICRO-LIVE", r["NEXT_STEP"])

    def test_it_refuses_another_approximation_by_name(self):
        r = H.harvest(write(rows(10)))
        self.assertTrue(r["DO_NOT_INVENT_ANOTHER_FILL_APPROXIMATION"])


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
        b = self.r["BOOK"]
        self.assertEqual(b["MARKETS"], 1)
        self.assertNotEqual(b["ONE_TICK_UPTIME"], NI)
        self.assertNotEqual(b["SPREAD_P50"], NI)
        self.assertTrue(b["FILL_RATE_NOT_PRESENT"])

    def test_the_capture_shape_is_reported(self):
        self.assertEqual(self.r["ROWS"], 40)
        self.assertEqual(self.r["MARKETS"], 1)
        self.assertEqual(self.r["SPORT_MIX"], {"nfl": 1})

    def test_the_forbidden_figures_are_absent_as_numbers(self):
        for k in ("PROFITABILITY", "WIN_RATE", "EXPECTED_MONTHLY_RETURN"):
            self.assertEqual(self.r[k], NI, k)
        self.assertEqual(self.r["ORDERS_PLACED"], 0)
        self.assertFalse(self.r["mirror_live"])


if __name__ == "__main__":
    unittest.main()
