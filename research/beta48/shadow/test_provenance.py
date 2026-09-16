#!/usr/bin/env python3
"""Evidence-run provenance: the executed code must be the authorised code."""
import tempfile
import unittest
from pathlib import Path

import provenance as PV

NI = PV.NOT_IDENTIFIED
A = "a" * 40
B = "b" * 40


class TheShaMustMatch(unittest.TestCase):

    def test_matching_shas_pass(self):
        r = PV.check(A, A)
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "PASS")
        self.assertEqual(r["FAILED_CHECKS"], [])

    def test_a_mismatch_fails_and_says_why(self):
        r = PV.check(A, B)
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL")
        self.assertIn("DISPATCH_SHA_EQUALS_EXECUTED_SHA", r["FAILED_CHECKS"])
        self.assertIn("not the code that was authorised", r["WHY_INVALID"])

    def test_a_missing_sha_is_not_a_pass(self):
        """Absent must never compare equal to absent."""
        for d, e in ((None, None), (A, None), (None, A), ("", "")):
            self.assertEqual(PV.check(d, e)["EVIDENCE_RUN_VALIDITY"], "FAIL")

    def test_an_abbreviated_sha_still_pins_the_checkout(self):
        self.assertEqual(PV.check(A[:7], A)["EVIDENCE_RUN_VALIDITY"], "PASS")
        self.assertEqual(PV.check(A[:7], B)["EVIDENCE_RUN_VALIDITY"], "FAIL")

    def test_a_branch_name_is_not_a_sha(self):
        """The whole defect was a branch ref standing in for a commit."""
        r = PV.check("claude/session-njaewf", A)
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL")
        self.assertEqual(r["DISPATCH_SHA"], NI)

    def test_case_does_not_defeat_the_comparison(self):
        self.assertEqual(PV.check(A.upper(), A)["EVIDENCE_RUN_VALIDITY"],
                         "PASS")


class TheWorkflowFileIsReportedNotGated(unittest.TestCase):
    """The workflow FILE comes from the ref, not from our checkout, so it can
    legitimately differ. Seeing that is the point; blocking on it is not."""

    def test_a_differing_ref_sha_is_reported_but_still_passes(self):
        r = PV.check(A, A, workflow_ref_sha=B)
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "PASS")
        self.assertEqual(r["WORKFLOW_FILE_AND_CODE_SAME_COMMIT"], "NO")

    def test_the_same_commit_is_reported_too(self):
        r = PV.check(A, A, workflow_ref_sha=A)
        self.assertEqual(r["WORKFLOW_FILE_AND_CODE_SAME_COMMIT"], "YES")

    def test_an_absent_ref_is_not_identified_rather_than_no(self):
        r = PV.check(A, A)
        self.assertEqual(r["WORKFLOW_FILE_AND_CODE_SAME_COMMIT"], NI)


class TheArtifactHashes(unittest.TestCase):

    def test_a_file_hashes_stably(self):
        d = Path(tempfile.mkdtemp())
        f = d / "x.json"
        f.write_text('{"a": 1}')
        self.assertEqual(PV.file_sha256(f), PV.file_sha256(f))

    def test_a_changed_file_hashes_differently(self):
        d = Path(tempfile.mkdtemp())
        f = d / "x.json"
        f.write_text('{"a": 1}')
        first = PV.file_sha256(f)
        f.write_text('{"a": 2}')
        self.assertNotEqual(first, PV.file_sha256(f))

    def test_a_missing_file_is_not_identified_not_empty(self):
        self.assertEqual(PV.file_sha256("/nonexistent/x"), NI)

    def test_every_provenance_field_is_carried(self):
        r = PV.check(A, A, B, "wf", "cfg", "out")
        for f in PV.PROVENANCE_FIELDS:
            self.assertIn(f, r, f)
        self.assertEqual(r["CONFIG_SHA"], "cfg")
        self.assertEqual(r["DATA_OUTPUT_SHA"], "out")


class TheReceipt(unittest.TestCase):

    def test_it_persists_and_ends_on_the_verdict(self):
        d = Path(tempfile.mkdtemp())
        r = PV.check(A, A)
        PV.receipt(d / "prov.json", r)
        self.assertTrue((d / "prov.json").is_file())
        self.assertTrue(PV.render(r).strip().endswith(
            "EVIDENCE_RUN_VALIDITY              = PASS"))

    def test_the_refusal_of_mutable_refs_is_stated(self):
        r = PV.check(A, A)
        self.assertTrue(r["MUTABLE_REF_IS_NOT_ALLOWED_FOR_EVIDENCE"])
        self.assertIn("chosen after the experiment is authorised",
                      r["WHY_IMMUTABLE"])

    def test_it_contacts_nothing(self):
        self.assertTrue(PV.THIS_MODULE_CONTACTS_NOTHING)
        src = Path(PV.__file__).read_text()
        self.assertNotIn("httpx", src)
        self.assertNotIn("requests.get", src)


if __name__ == "__main__":
    unittest.main()
