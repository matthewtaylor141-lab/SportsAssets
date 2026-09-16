#!/usr/bin/env python3
"""The research audit is an artefact with rules, not an essay with citations."""
import json
import unittest

import research_audit as RA


class TheRegistryValidatesAgainstItsOwnRules(unittest.TestCase):

    def setUp(self):
        self.rep = RA.validate()

    def test_the_registry_is_valid(self):
        self.assertEqual(self.rep["REGISTRY_VALID"], "YES",
                         "%s %s" % (self.rep["PROBLEMS"],
                                    self.rep["DOCUMENT_PROBLEMS"]))

    def test_every_entry_id_appears_in_the_document(self):
        """The two deliverables may not drift apart. A registry that no longer
        describes the document it ships with is worse than neither."""
        self.assertEqual(self.rep["DOCUMENT_PROBLEMS"], [])

    def test_status_is_one_of_exactly_five_values(self):
        self.assertEqual(len(RA.STATUS_VALUES), 5)
        for r in RA.entries():
            self.assertIn(r["STATUS"], RA.STATUS_VALUES)

    def test_every_required_field_is_present_on_every_entry(self):
        for r in RA.entries():
            for f in RA.REQUIRED_FIELDS:
                self.assertIn(f, r, "%s missing %s" % (r.get("ID"), f))

    def test_ids_are_unique(self):
        ids = [r["ID"] for r in RA.entries()]
        self.assertEqual(len(ids), len(set(ids)))


class NothingHereAuthorisesATrade(unittest.TestCase):
    """The promotion gate is structural. It is not a constant to flip."""

    def test_no_entry_is_promotable_whatever_is_passed(self):
        for r in RA.entries():
            for evidence in (None, "a paper says so", {"BACKTEST_PNL": 1e9},
                             True, ["peer reviewed"]):
                ok, reason = RA.promotable(r, evidence)
                self.assertFalse(ok)
                self.assertIn("BETTOR_NATIVE_OUT_OF_SAMPLE", reason[0])

    def test_adopt_architecturally_never_means_a_number(self):
        self.assertEqual(RA.ADOPT_MEANS, "STRUCTURE_ONLY_NEVER_A_NUMBER")

    def test_the_module_is_read_only_and_contacts_nothing(self):
        self.assertTrue(RA.THIS_MODULE_CONTACTS_NOTHING)
        self.assertEqual(RA.ORDERS, 0)
        self.assertEqual(RA.CAPITAL, 0)
        self.assertEqual(RA.CREDENTIALS, "NONE")
        self.assertFalse(RA.mirror_live)

    def test_the_registry_says_so_too(self):
        reg = RA.load()
        self.assertEqual(reg["ORDERS"], 0)
        self.assertEqual(reg["CAPITAL"], 0)
        self.assertEqual(reg["VENUE_CONTACT"], 0)
        self.assertFalse(reg["mirror_live"])


class AChallengerMustBeRefutable(unittest.TestCase):

    def test_challengers_and_adoptions_name_a_validation_method(self):
        for r in RA.entries():
            if r["STATUS"] in RA.MUST_BE_FALSIFIABLE:
                vm = str(r["VALIDATION_METHOD"]).strip().upper()
                self.assertNotIn(vm, RA.NON_ANSWERS, r["ID"])

    def test_an_unfalsifiable_challenger_is_caught(self):
        reg = RA.load()
        reg["ENTRIES"] = [dict(reg["ENTRIES"][0], ID="X-99",
                               STATUS="SHADOW_CHALLENGER",
                               VALIDATION_METHOD="NOT_APPLICABLE")]
        rep = RA.validate(reg, document=RA.DOCUMENT)
        self.assertIn("UNFALSIFIABLE X-99", rep["PROBLEMS"])

    def test_a_sixth_status_is_caught(self):
        reg = RA.load()
        reg["ENTRIES"] = [dict(reg["ENTRIES"][0], STATUS="MOSTLY_ADOPTED")]
        rep = RA.validate(reg, document=RA.DOCUMENT)
        self.assertTrue(any(p.startswith("BAD_STATUS")
                            for p in rep["PROBLEMS"]))

    def test_an_id_missing_from_the_document_is_caught(self):
        reg = RA.load()
        reg["ENTRIES"] = [dict(reg["ENTRIES"][0], ID="NOT-IN-THE-DOC")]
        rep = RA.validate(reg, document=RA.DOCUMENT)
        self.assertIn("ID_MISSING_FROM_DOCUMENT NOT-IN-THE-DOC",
                      rep["DOCUMENT_PROBLEMS"])


class TheAuditCoversEveryStreamAndRefusesSomeThings(unittest.TestCase):

    STREAMS = ("FAIR_VALUE_AND_CALIBRATION", "TWO_FAIR_VALUES",
               "MAKER_QUOTING", "FILL_HAZARD", "ADVERSE_SELECTION",
               "ENTRY_ECONOMICS", "EXIT_ECONOMICS", "CAPITAL_ALLOCATION",
               "VALIDATION")

    def test_all_nine_streams_are_represented(self):
        seen = {r["STREAM"] for r in RA.entries()}
        for s in self.STREAMS:
            self.assertIn(s, seen)

    def test_the_audit_rejects_things_rather_than_adopting_everything(self):
        """An audit that adopts every idea it reviewed did not audit."""
        self.assertGreaterEqual(len(RA.by_status("REJECT")), 5)

    def test_full_kelly_is_rejected_by_name(self):
        names = {r["NAME"]: r for r in RA.by_status("REJECT")}
        self.assertIn("Full Kelly", names)
        self.assertIn("p is KNOWN", names["Full Kelly"]["BETTOR_APPLICABILITY"])

    def test_manufacturing_fills_from_touch_is_rejected_by_name(self):
        rejected = " ".join(r["NAME"] for r in RA.by_status("REJECT"))
        self.assertIn("touch", rejected)

    def test_in_sample_pnl_selection_is_rejected_by_name(self):
        rejected = " ".join(r["NAME"] for r in RA.by_status("REJECT"))
        self.assertIn("in-sample P&L", rejected)

    def test_the_headline_question_is_left_not_identified(self):
        ni = RA.by_status("NOT_IDENTIFIED")
        self.assertTrue(any("improves BETTOR" in r["NAME"] for r in ni),
                        "the audit must not imply it has proved an improvement")

    def test_something_is_testable_with_no_new_data(self):
        now = RA.testable_now()
        self.assertGreater(len(now), 0)
        self.assertIn("S2-03", [r["ID"] for r in now])


class TheFrozenSafeguardsAreNamedAndNotWeakened(unittest.TestCase):

    def test_the_registry_lists_the_safeguards_that_may_not_be_weakened(self):
        reg = RA.load()
        text = " ".join(reg["SAFEGUARDS_THAT_MAY_NOT_BE_WEAKENED"])
        for must in ("PROVENANCE", "THREE_VALUED_UNCERTAINTY", "CENSORING",
                     "FILL_IDENTIFICATION", "INCENTIVE_SEPARATION",
                     "REENTRY", "NO_TRADE", "FEE_REGIMES"):
            self.assertIn(must, text)

    def test_the_promotion_rule_is_stated(self):
        reg = RA.load()
        self.assertIn("BETTOR-NATIVE", reg["PROMOTION_RULE"].upper())


class TheRegistryIsWellFormedJson(unittest.TestCase):

    def test_it_round_trips(self):
        raw = RA.REGISTRY.read_text()
        self.assertEqual(json.loads(raw), RA.load())


if __name__ == "__main__":
    unittest.main()
