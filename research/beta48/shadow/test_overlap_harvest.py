#!/usr/bin/env python3
"""REGRESSION: THE OVERLAP AUDIT THAT NEVER RAN.

Run 35180590124 measured 0.25 rps cleanly -- 301 requests, 301 successes, zero
429s, 1200 s of paced exposure -- and sealed VENUE_REQUEST_WINDOWS = {}.

That empty object reads as "no direct collector overlapped the evidence
window". It meant something else entirely: the harvest guarded the audit on
FIRST_VENUE_GET_TIME in confirm_report.json, `confirm()` never wrote that key,
the guard fell through, and an empty overlap object was sealed. AUDIT_SKIPPED,
not NO_OVERLAP -- and the two are the same bytes.

The defect class is the one this programme keeps finding in itself: a check
that cannot fail resolving silently to the reassuring answer. Here the check
did not merely pass; it was never attempted, and nothing in the artifact said
so.

These tests contact nothing and need no venue.
"""
import json
import unittest
from pathlib import Path

import rate_confirm as RC
import reharvest_overlap as RH
import venue_domain as VD

WINDOW_START = "2026-09-17T04:05:48+00:00"
WINDOW_END = "2026-09-17T04:25:48+00:00"
KNOWN = ["beta48-forward-capture", "beta48-rate-confirm", "run85-phase2a"]


def run_row(rid, name, started, ended, status="completed"):
    return {"id": rid, "name": name, "status": status,
            "conclusion": "success", "created_at": started,
            "run_started_at": started, "updated_at": ended}


def exhausted(names=KNOWN):
    return ({n: False for n in names}, {n: 1 for n in names})


class A_RowsButNoWindow(unittest.TestCase):
    """A. Rows exist and the window is missing. The audit must not skip
    silently, and isolation must not read YES."""

    def setUp(self):
        more, pages = exhausted()
        self.r = VD.overlap_harvest(None, None, [], KNOWN, row_count=301,
                                    more_pages=more, pages_fetched=pages)

    def test_the_audit_is_marked_not_executed(self):
        self.assertEqual(self.r["AUDIT_EXECUTED"], "NO")
        self.assertEqual(self.r["OVERLAP_AUDIT_STATUS"],
                         "NOT_EXECUTED_MISSING_WINDOW")

    def test_isolation_cannot_read_yes_on_an_audit_that_did_not_run(self):
        self.assertEqual(
            self.r["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"],
            "NOT_IDENTIFIED")

    def test_the_windows_field_is_not_an_empty_object(self):
        """The exact shape that misled. {} must never be the answer here."""
        self.assertNotEqual(self.r["VENUE_REQUEST_WINDOWS"], {})
        self.assertEqual(self.r["VENUE_REQUEST_WINDOWS"], "NOT_IDENTIFIED")

    def test_it_says_the_rows_existed(self):
        self.assertEqual(self.r["ROW_COUNT"], 301)
        self.assertIn("301", self.r["WHY_NOT_EXECUTED"])

    def test_no_rows_is_a_different_status_from_no_window(self):
        r = VD.overlap_harvest(None, None, [], KNOWN, row_count=0)
        self.assertEqual(r["OVERLAP_AUDIT_STATUS"], "NOT_EXECUTED_NO_ROWS")
        self.assertEqual(
            r["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"],
            "NOT_IDENTIFIED")


class B_WindowDerivedFromTheRows(unittest.TestCase):
    """B. The window is derivable from the rows the run wrote, and once it is,
    the audit executes."""

    ROWS = [{"SEQ": 0, "RECEIPT_UTC": WINDOW_START},
            {"SEQ": 1, "RECEIPT_UTC": "2026-09-17T04:15:48+00:00"},
            {"SEQ": 2, "RECEIPT_UTC": WINDOW_END}]

    def test_the_window_comes_from_the_receipts(self):
        self.assertEqual(RH.derive_window(self.ROWS),
                         (WINDOW_START, WINDOW_END))

    def test_out_of_order_receipts_widen_never_narrow(self):
        shuffled = [self.ROWS[2], self.ROWS[0], self.ROWS[1]]
        self.assertEqual(RH.derive_window(shuffled), (WINDOW_START, WINDOW_END))

    def test_no_receipts_is_absent_not_a_zero_length_window(self):
        f, l = RH.derive_window([{"SEQ": 0}])
        self.assertEqual((f, l), ("NOT_IDENTIFIED", "NOT_IDENTIFIED"))

    def test_with_the_window_the_audit_executes(self):
        more, pages = exhausted()
        r = VD.overlap_harvest(WINDOW_START, WINDOW_END, [], KNOWN,
                               row_count=3, more_pages=more,
                               pages_fetched=pages)
        self.assertEqual(r["AUDIT_EXECUTED"], "YES")
        self.assertEqual(r["OVERLAP_AUDIT_STATUS"], "EXECUTED")
        self.assertEqual(r["EVIDENCE_WINDOW"], [WINDOW_START, WINDOW_END])

    def test_the_writer_now_emits_the_window_keys(self):
        """The other half of the mismatch: `confirm()` must write what the
        harvest reads."""
        import inspect
        src = inspect.getsource(RC.confirm)
        self.assertIn('"FIRST_VENUE_GET_TIME"', src)
        self.assertIn('"LAST_VENUE_GET_TIME"', src)
        self.assertIn("RECEIPT_UTC", src)


class C_NoOverlapWithCompleteHistory(unittest.TestCase):
    """C. No direct job overlapped and the history is exhausted -> YES."""

    def setUp(self):
        more, pages = exhausted()
        runs = [  # both well clear of the window
            run_row(1, "beta48-forward-capture",
                    "2026-09-17T02:00:00+00:00", "2026-09-17T02:30:00+00:00"),
            run_row(2, "run85-phase2a",
                    "2026-09-17T05:00:00+00:00", "2026-09-17T05:30:00+00:00"),
        ]
        self.r = VD.overlap_harvest(WINDOW_START, WINDOW_END, runs, KNOWN,
                                    row_count=301, more_pages=more,
                                    pages_fetched=pages)

    def test_isolation_throughout_the_run_is_yes(self):
        self.assertEqual(
            self.r["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"],
            "YES")

    def test_nothing_overlapped(self):
        self.assertEqual(self.r["STAGE_1_JOB_INTERVAL_CANDIDATES"], 0)
        self.assertEqual(self.r["DIRECT_OVERLAP_COUNT"], 0)
        self.assertEqual(self.r["POSSIBLE_DIRECT_WORKFLOW_OVERLAP"], "NO")

    def test_an_executed_empty_result_says_so_by_name(self):
        """EMPTY_AFTER_AUDIT is a finding. {} was an absence wearing its
        clothes."""
        self.assertEqual(self.r["VENUE_REQUEST_WINDOWS"], "EMPTY_AFTER_AUDIT")
        self.assertNotEqual(self.r["VENUE_REQUEST_WINDOWS"], {})

    def test_incomplete_history_cannot_reach_yes(self):
        """Coverage gates only the negative -- and it still does."""
        more, pages = exhausted()
        more["run85-phase2a"] = True          # the API had more to give
        r = VD.overlap_harvest(WINDOW_START, WINDOW_END, [], KNOWN,
                               row_count=301, more_pages=more,
                               pages_fetched=pages)
        self.assertEqual(r["AUDIT_EXECUTED"], "YES")
        self.assertEqual(r["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "NO")
        self.assertEqual(
            r["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"],
            "NOT_IDENTIFIED")


class D_AnOverlappingJob(unittest.TestCase):
    """D. A direct job DID execute inside the window. Level A clears it only
    with that run's own sealed request times; otherwise it stays Level B."""

    RUNS = [run_row(77, "beta48-forward-capture",
                    "2026-09-17T04:10:00+00:00", "2026-09-17T04:20:00+00:00")]

    def harvest(self, venue_windows=None):
        more, pages = exhausted()
        return VD.overlap_harvest(WINDOW_START, WINDOW_END, self.RUNS, KNOWN,
                                  row_count=301, more_pages=more,
                                  pages_fetched=pages,
                                  venue_windows=venue_windows)

    def test_level_b_an_overlapping_job_blocks_the_yes(self):
        r = self.harvest()
        self.assertEqual(r["DIRECT_OVERLAP_COUNT"], 1)
        self.assertEqual(r["POSSIBLE_DIRECT_WORKFLOW_OVERLAP"], "YES")
        self.assertNotEqual(
            r["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"], "YES")

    def test_level_a_sealed_request_times_outside_the_window_clear_it(self):
        r = self.harvest({"77": ["2026-09-17T04:10:05+00:00",
                                 "2026-09-17T04:10:30+00:00"]})
        self.assertEqual(r["AUDIT_EXECUTED"], "YES")
        self.assertEqual(r["VENUE_REQUEST_WINDOWS"],
                         {"77": ["2026-09-17T04:10:05+00:00",
                                 "2026-09-17T04:10:30+00:00"]})

    def test_a_running_job_with_no_end_stays_open_ended(self):
        runs = [run_row(78, "run85-phase2a", "2026-09-17T04:10:00+00:00",
                        None, status="in_progress")]
        more, pages = exhausted()
        r = VD.overlap_harvest(WINDOW_START, WINDOW_END, runs, KNOWN,
                               row_count=301, more_pages=more,
                               pages_fetched=pages)
        self.assertGreaterEqual(r["DIRECT_OVERLAP_COUNT"], 1)
        self.assertNotEqual(
            r["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"], "YES")


class TheFrozenRulesAreNotTouched(unittest.TestCase):
    """The wrapper decides only WHETHER the audit runs and how its silence is
    labelled. Every overlap rule underneath it is the frozen one."""

    def test_the_wrapper_delegates_to_overlap_audit_verbatim(self):
        more, pages = exhausted()
        direct = VD.overlap_audit(WINDOW_START, WINDOW_END, D_AnOverlappingJob.
                                  RUNS, KNOWN, more_pages=more,
                                  pages_fetched=pages)
        wrapped = VD.overlap_harvest(WINDOW_START, WINDOW_END,
                                     D_AnOverlappingJob.RUNS, KNOWN,
                                     row_count=301, more_pages=more,
                                     pages_fetched=pages)
        for k, v in direct.items():
            if k == "VENUE_REQUEST_WINDOWS":
                continue                       # the one field this adds status
            self.assertEqual(wrapped[k], v, k)

    def test_the_wrapper_adds_only_status_fields(self):
        more, pages = exhausted()
        direct = VD.overlap_audit(WINDOW_START, WINDOW_END, [], KNOWN,
                                  more_pages=more, pages_fetched=pages)
        wrapped = VD.overlap_harvest(WINDOW_START, WINDOW_END, [], KNOWN,
                                     row_count=1, more_pages=more,
                                     pages_fetched=pages)
        self.assertEqual(
            sorted(set(wrapped) - set(direct)),
            ["AUDIT_EXECUTED", "A_SKIPPED_AUDIT_IS_NOT_A_CLEAN_ONE",
             "OVERLAP_AUDIT_STATUS", "ROW_COUNT", "VENUE_REQUEST_WINDOWS",
             "WHY_STATUS_EXISTS"])


class TheReHarvestArtifact(unittest.TestCase):
    """The amended artifact must name itself as derived, and must not be able
    to restate the operational measurements."""

    def test_it_declares_its_derivation_and_that_it_sent_nothing(self):
        self.assertEqual(RH.DERIVATION_TYPE,
                         "OFFLINE_REHARVEST_FROM_ALREADY_SEALED_EVIDENCE")
        self.assertEqual(RH.THIS_IS_NOT, "A_SECOND_RUN")

    def test_the_rate_measurements_are_carried_not_recomputed(self):
        for k in ("REQUESTS", "SUCCESSES", "HTTP_429", "OTHER_FAILURES",
                  "DURATION_S", "RATE_RPS", "EVIDENCE_RUN_VALIDITY"):
            self.assertIn(k, RH.CARRIED_VERBATIM, k)

    def test_it_contacts_nothing(self):
        src = Path(RH.__file__).read_text()
        for forbidden in ("httpx", "requests.get", "urlopen", "curl"):
            self.assertNotIn(forbidden, src, forbidden)


if __name__ == "__main__":
    unittest.main()
