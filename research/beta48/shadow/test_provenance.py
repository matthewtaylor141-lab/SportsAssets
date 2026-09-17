#!/usr/bin/env python3
"""Evidence-run provenance: the executed code must be the authorised code."""
import tempfile
import unittest
from pathlib import Path

import provenance as PV

NI = PV.NOT_IDENTIFIED
PENDING = PV.PENDING
A = "a" * 40
B = "b" * 40


def full(dispatch=A, executed=A, **over):
    """A complete runtime record: matching SHAs plus every provenance
    recording. EVIDENCE_RUN_VALIDITY needs all four aspects, not just the
    code comparison."""
    kw = dict(workflow_ref_sha=A, workflow_file_sha="wf",
              config_sha="cfg", data_output_sha="out",
              executed_sha_source="RUNNER_GIT_REV_PARSE")
    kw.update(over)
    return PV.check(dispatch, executed, **kw)


class TheShaMustMatch(unittest.TestCase):

    def test_matching_shas_pass(self):
        r = full()
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "PASS")
        self.assertEqual(r["FAILED_CHECKS"], [])
        self.assertEqual(r["CODE_PROVENANCE_VALIDITY"], "PASS")

    def test_a_mismatch_fails_and_says_why(self):
        r = full(executed=B)
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL")
        self.assertIn("DISPATCH_SHA_EQUALS_EXECUTED_SHA", r["FAILED_CHECKS"])
        self.assertIn("not the code that was authorised", r["WHY_INVALID"])

    def test_a_missing_sha_is_not_a_pass(self):
        """Absent must never compare equal to absent."""
        for d, e in ((None, None), (A, None), (None, A), ("", "")):
            self.assertEqual(full(d, e)["EVIDENCE_RUN_VALIDITY"], "FAIL")

    def test_an_abbreviated_sha_still_pins_the_checkout(self):
        self.assertEqual(full(A[:7], A)["EVIDENCE_RUN_VALIDITY"], "PASS")
        self.assertEqual(full(A[:7], B)["EVIDENCE_RUN_VALIDITY"], "FAIL")

    def test_a_branch_name_is_not_a_sha(self):
        """The whole defect was a branch ref standing in for a commit."""
        r = full("claude/session-njaewf", A)
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL")
        self.assertEqual(r["DISPATCH_SHA"], NI)

    def test_case_does_not_defeat_the_comparison(self):
        self.assertEqual(full(A.upper(), A)["EVIDENCE_RUN_VALIDITY"], "PASS")


class TheWorkflowFileIsReportedNotGated(unittest.TestCase):
    """The workflow FILE comes from the ref, not from our checkout, so it can
    legitimately differ. Seeing that is the point; blocking on it is not."""

    def test_a_differing_ref_sha_is_reported_but_still_passes(self):
        r = full(workflow_ref_sha=B)
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "PASS")
        self.assertEqual(r["WORKFLOW_FILE_AND_CODE_SAME_COMMIT"], "NO")

    def test_the_same_commit_is_reported_too(self):
        r = full(workflow_ref_sha=A)
        self.assertEqual(r["WORKFLOW_FILE_AND_CODE_SAME_COMMIT"], "YES")

    def test_an_absent_ref_is_not_identified_rather_than_no(self):
        r = full(workflow_ref_sha=None)
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
        r = full(workflow_ref_sha=B)
        for f in PV.PROVENANCE_FIELDS:
            self.assertIn(f, r, f)
        self.assertEqual(r["CONFIG_SHA"], "cfg")
        self.assertEqual(r["DATA_OUTPUT_SHA"], "out")


class TheReceipt(unittest.TestCase):

    def test_it_persists_and_ends_on_the_verdict(self):
        d = Path(tempfile.mkdtemp())
        r = full()
        PV.receipt(d / "prov.json", r)
        self.assertTrue((d / "prov.json").is_file())
        self.assertTrue(PV.render(r).strip().endswith(
            "EVIDENCE_RUN_VALIDITY              = PASS"))

    def test_the_refusal_of_mutable_refs_is_stated(self):
        r = full()
        self.assertTrue(r["MUTABLE_REF_IS_NOT_ALLOWED_FOR_EVIDENCE"])
        self.assertIn("chosen after the experiment is authorised",
                      r["WHY_IMMUTABLE"])

    def test_it_contacts_nothing(self):
        self.assertTrue(PV.THIS_MODULE_CONTACTS_NOTHING)
        src = Path(PV.__file__).read_text()
        self.assertNotIn("httpx", src)
        self.assertNotIn("requests.get", src)


class AnExpectationIsNotAnObservation(unittest.TestCase):
    """The pre-dispatch gate cannot claim EVIDENCE_RUN_VALIDITY = PASS, and it
    is structurally unable to: there is no executed-SHA parameter to pass an
    expectation into."""

    def test_the_pre_dispatch_gate_leaves_validity_pending(self):
        g = PV.pre_dispatch_gate(A, "wf", "cfg")
        self.assertEqual(g["PRE_DISPATCH_PROVENANCE_GATE"], "PASS")
        self.assertEqual(g["EVIDENCE_RUN_VALIDITY"], PENDING)
        self.assertEqual(g["EXECUTED_SHA_ACTUAL"], PENDING)
        self.assertEqual(g["DATA_OUTPUT_SHA"], PENDING)

    def test_the_expected_sha_is_labelled_expected(self):
        g = PV.pre_dispatch_gate(A, "wf", "cfg")
        self.assertEqual(g["EXPECTED_EXECUTED_SHA"], A)
        self.assertEqual(g["DISPATCH_SHA_VALID_FORMAT"], "YES")

    def test_there_is_no_way_to_pass_an_expectation_as_an_observation(self):
        """Not a convention -- a signature. The parameter does not exist."""
        import inspect
        params = inspect.signature(PV.pre_dispatch_gate).parameters
        for p in params:
            self.assertNotIn("executed", p)

    def test_a_malformed_dispatch_sha_fails_the_gate(self):
        g = PV.pre_dispatch_gate("claude/session-njaewf", "wf", "cfg")
        self.assertEqual(g["PRE_DISPATCH_PROVENANCE_GATE"], "FAIL")
        self.assertEqual(g["EVIDENCE_RUN_VALIDITY"], PENDING)

    def test_a_missing_config_hash_fails_the_gate(self):
        g = PV.pre_dispatch_gate(A, "wf", None)
        self.assertIn("CONFIG_SHA_PRESENT", g["FAILED_CHECKS"])

    def test_an_unobserved_executed_sha_fails_at_runtime(self):
        """If the source is not a runner observation, the run is not valid."""
        r = full(executed_sha_source="EXPECTED")
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL")
        self.assertIn("EXECUTED_SHA_WAS_OBSERVED_NOT_EXPECTED",
                      r["FAILED_CHECKS"])

    def test_the_gate_block_prints_pending_not_pass(self):
        text = PV.render(PV.pre_dispatch_gate(A, "wf", "cfg"))
        self.assertIn("EXPECTED_EXECUTED_SHA", text)
        self.assertTrue(text.strip().endswith("= PENDING_EXECUTION"))


class FourAspectsNotOne(unittest.TestCase):

    def test_a_missing_output_hash_fails_though_the_code_matches(self):
        r = full(data_output_sha=None)
        self.assertEqual(r["CODE_PROVENANCE_VALIDITY"], "PASS")
        self.assertEqual(r["DATA_OUTPUT_PROVENANCE"], "NOT_RECORDED")
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL")
        self.assertIn("required provenance record is missing", r["WHY_INVALID"])

    def test_a_missing_config_hash_fails_though_the_code_matches(self):
        r = full(config_sha=None)
        self.assertEqual(r["CONFIG_PROVENANCE"], "NOT_RECORDED")
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL")

    def test_a_code_mismatch_is_named_as_the_code_aspect(self):
        r = full(executed=B)
        self.assertEqual(r["CODE_PROVENANCE_VALIDITY"], "FAIL")
        self.assertEqual(r["WORKFLOW_PROVENANCE"], "RECORDED")
        self.assertIn("not the code that was authorised", r["WHY_INVALID"])

    def test_all_four_aspects_are_separate_fields(self):
        r = full()
        for a in PV.PROVENANCE_ASPECTS:
            self.assertIn(a, r, a)


def pre_get(dispatch=A, executed=A, **over):
    """The runner's pre-GET record: SHAs observed, rows not yet written."""
    kw = dict(workflow_ref_sha=A, workflow_file_sha="wf", config_sha="cfg",
              executed_sha_source="RUNNER_GIT_REV_PARSE")
    kw.update(over)
    return PV.pre_get_gate(dispatch, executed, **kw)


class TheImpossibleProvenanceGate(unittest.TestCase):
    """REGRESSION. Run 35175681195 aborted before its first venue GET with
    EVIDENCE_RUN_VALIDITY = FAIL while CODE_PROVENANCE_VALIDITY was PASS and
    FAILED_CHECKS was empty.

    The workflow called the HARVEST verdict at step 6, which requires
    DATA_OUTPUT_PROVENANCE = RECORDED. Step 8 writes those rows, and the CLI
    had no flag to pass a data hash anyway, so the gate could not open for any
    run at that revision -- a gate with no key. These tests pin the three
    states that together prevent it coming back.
    """

    # ---- PRE-GET: no data hash exists yet, and that is not a failure -----
    def test_pre_get_passes_as_pending_with_no_data_output_sha(self):
        r = pre_get()
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], PV.PENDING)
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "PENDING_EXECUTION")
        self.assertEqual(r["PRE_GET_PROVENANCE_GATE"], "PASS")
        self.assertEqual(r["DATA_OUTPUT_SHA"], PV.NOT_IDENTIFIED)
        self.assertEqual(r["DATA_OUTPUT_PROVENANCE"], "NOT_RECORDED")
        self.assertNotIn("WHY_INVALID", r)

    def test_pre_get_still_proves_the_code_provenance_in_full(self):
        """The relaxation is ONLY the aggregate verdict. Every code check
        that ran at harvest runs here, on the same observed checkout."""
        r = pre_get()
        self.assertEqual(r["CODE_PROVENANCE_VALIDITY"], "PASS")
        self.assertEqual(r["EXECUTED_SHA_ACTUAL"], A)
        self.assertEqual(r["DISPATCH_SHA"], A)
        self.assertEqual(r["FAILED_CHECKS"], [])
        for c in ("DISPATCH_SHA_PRESENT", "EXECUTED_SHA_ACTUAL_PRESENT",
                  "EXECUTED_SHA_WAS_OBSERVED_NOT_EXPECTED",
                  "DISPATCH_SHA_EQUALS_EXECUTED_SHA"):
            self.assertTrue(r["CHECKS"][c], c)

    def test_pre_get_fails_on_a_code_mismatch(self):
        """No weakening: the wrong checkout is refused before any venue GET."""
        r = pre_get(executed=B)
        self.assertEqual(r["CODE_PROVENANCE_VALIDITY"], "FAIL")
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL")
        self.assertEqual(r["PRE_GET_PROVENANCE_GATE"], "FAIL")
        self.assertNotEqual(r["EVIDENCE_RUN_VALIDITY"], PV.PENDING)

    def test_pre_get_fails_on_an_expected_rather_than_observed_sha(self):
        r = pre_get(executed_sha_source="DISPATCHER_ECHO")
        self.assertFalse(r["CHECKS"]["EXECUTED_SHA_WAS_OBSERVED_NOT_EXPECTED"])
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL")

    def test_pre_get_fails_on_missing_workflow_or_config_provenance(self):
        for kw in ({"workflow_file_sha": None}, {"config_sha": None}):
            r = pre_get(**kw)
            self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL", kw)

    # ---- POST-RUN: the rows exist, and the full verdict can pass ---------
    def test_post_run_full_check_passes_once_the_data_hash_exists(self):
        r = full()
        self.assertEqual(r["DATA_OUTPUT_PROVENANCE"], "RECORDED")
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "PASS")

    # ---- POST-RUN WITHOUT ROWS: still FAIL, exactly as before ------------
    def test_post_run_without_a_data_hash_still_fails(self):
        """The pre-GET change must not let a run become valid without final
        output provenance. This is the harvest path, unchanged."""
        r = full(data_output_sha=None)
        self.assertEqual(r["CODE_PROVENANCE_VALIDITY"], "PASS")
        self.assertEqual(r["DATA_OUTPUT_PROVENANCE"], "NOT_RECORDED")
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL")

    def test_pre_get_can_never_report_a_run_valid(self):
        """PENDING_EXECUTION is not PASS, and no argument reaches PASS here."""
        for kw in ({}, {"workflow_file_sha": "wf2"}, {"config_sha": "cfg2"}):
            r = pre_get(**kw)
            self.assertNotEqual(r["EVIDENCE_RUN_VALIDITY"], "PASS", kw)
        self.assertTrue(pre_get()["PRE_GET_CANNOT_MAKE_A_RUN_VALID"])
        self.assertEqual(pre_get()["FINAL_VALIDITY_STILL_REQUIRES"],
                         list(PV.PROVENANCE_ASPECTS))

    def test_pre_get_takes_no_data_output_argument_at_all(self):
        """Structural, not remembered: there is no parameter through which a
        data hash could be supplied early and read back as evidence."""
        import inspect
        self.assertNotIn("data_output_sha",
                         inspect.signature(PV.pre_get_gate).parameters)

    def test_the_harvest_verdict_still_requires_all_four_aspects(self):
        r = full()
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "PASS")
        for a in PV.PROVENANCE_ASPECTS[1:]:
            self.assertEqual(r[a], "RECORDED", a)


if __name__ == "__main__":
    unittest.main()
