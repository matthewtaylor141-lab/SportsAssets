#!/usr/bin/env python3
"""CORRECTION 9. The probe, pinned against the actual files.

These tests read `evidence/blobs_v3/` and would fail if a future edit restored
the claim that blobs_v3 "unlocks" the cell space, or if the files themselves
were replaced by a richer extraction without the documents being updated to
match. The second case is the point: if per-position rows ever DO arrive, the
first test fails loudly rather than the documents quietly staying wrong.
"""
import json
import unittest
from pathlib import Path

import probe_blobs_v3 as P

HERE = Path(__file__).resolve().parent
DOCS = ("WHALE_NATIVE_EV_BRIDGE_V1.md", "BETTOR_DAY1_EV_ARCHITECTURE.md",
        "MANAGEMENT_EV_EXPLAINER.md")


class TheFilesAreAggregateNotPerPosition(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.docs = P.load_all()
        cls.probe = P.build()

    def test_six_accounts_are_present_and_two_are_outside_the_prior(self):
        self.assertEqual(len(self.docs), 6)
        self.assertEqual(self.probe["ACCOUNTS_NOT_IN_THE_FOUR_ACCOUNT_PRIOR"],
                         ["kch123", "w2c33"])

    def test_no_file_contains_a_row_bearing_list(self):
        """~80 KB cannot hold 259,271 position rows, and none is there."""
        for acct, d in self.docs.items():
            self.assertEqual(P.row_bearing_lists(d), [], acct)

    def test_the_top_level_keys_are_five_aggregate_blocks(self):
        self.assertEqual(self.probe["TOP_LEVEL_KEYS"], [
            "CENSUS", "MERGE_PNL_BY_OPEN_BAND",
            "PNL_BY_OPEN_BAND_ALL_CHANNELS", "REFERENCE_ACCOUNT_COMPLETION",
            "REFERENCE_ACCOUNT_REPLAY"])

    def test_the_source_is_a_trade_tape_so_there_is_no_book(self):
        self.assertIn("activity?type=TRADE", self.probe["SOURCE_ENDPOINT"])


class EveryProbedFieldIsAnswered(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.probe = P.build()
        cls.by_field = {r["FIELD"]: r for r in cls.probe["FIELD_REPORT"]}

    def test_every_field_the_correction_names_is_reported(self):
        for f in P.PROBED_FIELDS:
            self.assertIn(f, self.by_field, f)

    def test_all_fourteen_are_absent(self):
        for f, r in self.by_field.items():
            self.assertEqual(r["STATUS"], P.ABSENT, f)
            self.assertEqual(r["COVERAGE_PCT"], 0.0, f)

    def test_the_market_and_event_verdicts_distinguish_count_from_identity(self):
        self.assertIn("COUNT", self.by_field["MARKET_ID"]["SOURCE"])
        self.assertIn("not one slug", self.by_field["MARKET_ID"]["WHY"])
        self.assertIn("DISTINCT_CONDITIONS equals DISTINCT_MARKET_SLUGS",
                      self.by_field["EVENT_ID"]["WHY"])

    def test_the_sport_field_names_its_collision_behaviour(self):
        r = self.by_field["SPORT"]
        self.assertIn("MANY-TO-ONE", r["UNIQUENESS_OR_COLLISIONS"])
        self.assertIn("truncated", r["JOIN_KEY"])
        self.assertIn("TOP-25", r["WHY"])


class TheCellVerdict(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.probe = P.build()

    def test_the_unlocks_claim_is_refuted_by_the_files(self):
        self.assertEqual(self.probe["CLAIM_VERDICT"], "REFUTED_BY_THE_FILES")
        self.assertEqual(self.probe["BLOBS_V3_RICH_CELL_STATUS"],
                         "NOT_AVAILABLE_AGGREGATE_ONLY")
        self.assertEqual(self.probe["EVENT_LEVEL_CLUSTERING_FROM_BLOBS_V3"],
                         P.NOT_IDENTIFIED)
        self.assertEqual(self.probe["EVENT_BLOCKED_BOOTSTRAP_FROM_BLOBS_V3"],
                         P.NOT_IDENTIFIED)

    def test_the_constructible_cells_are_full_coverage_marginals(self):
        cells = {c["CELL"] for c in self.probe["CELLS_GENUINELY_CONSTRUCTIBLE"]}
        self.assertEqual(cells, {"ACCOUNT x PRICE_BAND",
                                 "ACCOUNT x FILL_SIZE_BUCKET",
                                 "ACCOUNT x ISO_WEEK"})
        for c in self.probe["CELLS_GENUINELY_CONSTRUCTIBLE"]:
            self.assertGreaterEqual(c["MIN_COVERAGE_PCT"], 99.9, c["CELL"])

    def test_the_question_title_table_is_refused_with_its_coverage(self):
        refused = self.probe["CELLS_REFUSED"]
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["CELL"],
                         "ACCOUNT x TRUNCATED_QUESTION_TITLE")
        self.assertLess(refused[0]["MIN_COVERAGE_PCT"], 25)
        self.assertIn("not a taxonomy", refused[0]["WHY_REFUSED"])

    def test_the_two_new_cells_are_reported_as_marginals_not_a_grid(self):
        self.assertEqual(
            self.probe["NEW_CELLS_BLOBS_V3_ADDS_OVER_whale_exit_priors_v1"],
            ["ACCOUNT x FILL_SIZE_BUCKET", "ACCOUNT x ISO_WEEK"])
        self.assertEqual(self.probe["NEW_CELLS_ARE_STILL"],
                         "ACCOUNT_LEVEL_MARGINALS_NOT_A_CROSS_PRODUCT")

    def test_the_real_recovery_path_is_named_as_a_re_extraction(self):
        self.assertEqual(self.probe["THAT_WORK_REQUIRES"],
                         "AN_UPSTREAM_RE_EXTRACTION_NOT_VENUE_ACCESS")


class TheDocumentsNoLongerClaimTheUnlock(unittest.TestCase):
    """The prose is part of the deliverable, so it is pinned like code."""

    def test_no_document_says_blobs_v3_unlocks_the_cell_space(self):
        for name in DOCS:
            text = (HERE / name).read_text().lower()
            if "blobs_v3" not in text:
                continue
            for phrase in ("unlocks the sport", "unlocks the cell",
                           "enables event-level clustering"):
                self.assertNotIn(phrase, text, "%s: %r" % (name, phrase))

    def test_the_probe_artefact_is_committed_and_matches_the_files(self):
        p = HERE / "BLOBS_V3_PROBE_V1.json"
        self.assertTrue(p.exists())
        on_disk = json.loads(p.read_text())
        self.assertEqual(on_disk["BLOBS_V3_RICH_CELL_STATUS"],
                         P.build()["BLOBS_V3_RICH_CELL_STATUS"])


class TheProbeTouchesNothing(unittest.TestCase):

    def test_read_only(self):
        self.assertEqual(P.VENUE_CONTACT, 0)
        self.assertEqual(P.ORDERS, 0)
        self.assertEqual(P.CAPITAL, 0)
        self.assertEqual(P.CREDENTIALS, "NONE")
        self.assertFalse(P.mirror_live)


if __name__ == "__main__":
    unittest.main()
