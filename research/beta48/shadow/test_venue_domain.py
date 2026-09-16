#!/usr/bin/env python3
"""The global venue-access domain, and the audit that must stay true.

The load-bearing test here is the LIVE one: it runs the discovery against this
repository and requires that every venue-touching workflow sits in the one
domain. A collector added next month fails this test instead of silently
contaminating the next rate measurement -- which is exactly how
run85-phase2-capture came to be running throughout the ladder.
"""
import unittest
from datetime import datetime
from pathlib import Path

import venue_domain as VD

NI = VD.NOT_IDENTIFIED
ROOT = Path(__file__).resolve().parents[3]


class TheLiveRepositoryAudit(unittest.TestCase):

    def setUp(self):
        self.a = VD.audit(ROOT)

    def test_every_venue_touching_workflow_is_in_the_one_domain(self):
        self.assertEqual(self.a["WORKFLOWS_OUTSIDE_THE_DOMAIN"], {})
        self.assertEqual(self.a["DOMAIN_AUDIT"], "PASS")

    def test_the_collectors_that_actually_collided_are_in_it(self):
        known = self.a["KNOWN_VENUE_TOUCHING_WORKFLOWS"]
        for n in ("beta48-rate-confirm", "beta48-rate-pilot",
                  "beta48-forward-capture", "beta48-shadow-tick",
                  "run85-phase2-capture", "venue-probe"):
            self.assertIn(n, known, n)

    def test_the_list_is_discovered_not_written_down(self):
        self.assertTrue(self.a["DISCOVERED_NOT_ASSUMED"])
        self.assertGreaterEqual(self.a["VENUE_TOUCHING_COUNT"], 18)

    def test_every_member_says_why_it_is_venue_touching(self):
        for n in self.a["KNOWN_VENUE_TOUCHING_WORKFLOWS"]:
            self.assertTrue(self.a["WHY_EACH_IS_VENUE_TOUCHING"][n], n)


class TheHostBoundaryIsNarrow(unittest.TestCase):
    """polymarket.com is a different service behind a different limiter.
    Sweeping it in would serialise ops tooling for no gain."""

    def test_the_other_polymarket_hosts_are_excluded_by_name(self):
        for h in ("data-api.polymarket.com", "lb-api.polymarket.com",
                  "user-pnl-api.polymarket.com"):
            self.assertIn(h, VD.NOT_THE_SAME_LIMITER)

    def test_ops_tooling_is_not_dragged_into_the_domain(self):
        known = VD.audit(ROOT)["KNOWN_VENUE_TOUCHING_WORKFLOWS"]
        for n in ("render-ops", "whale-ledger-census", "whale-full-history"):
            self.assertNotIn(n, known, n)

    def test_indirect_paths_are_named_not_domained(self):
        a = VD.audit(ROOT)
        self.assertIn("pair-probe", a["INDIRECT_VENUE_WORKFLOWS"])
        self.assertEqual(a["INDIRECT_COLLECTOR_ISOLATION"], "NOT_ESTABLISHED")
        self.assertNotIn("pair-probe", a["KNOWN_VENUE_TOUCHING_WORKFLOWS"])


class TheIsolationVerdict(unittest.TestCase):

    KNOWN = ["beta48-rate-confirm", "run85-phase2-capture",
             "beta48-forward-capture"]

    def test_an_empty_field_is_isolation(self):
        i = VD.isolation([], self.KNOWN, self_run_id="1")
        self.assertEqual(i["DIRECT_RESEARCH_COLLECTOR_ISOLATION"],
                         "ESTABLISHED")
        self.assertEqual(i["ACTIVE_DIRECT_CONFLICTS"], 0)
        self.assertEqual(i["PENDING_DIRECT_CONFLICTS"], 0)

    def test_the_exact_collision_that_happened_is_caught(self):
        """run85-phase2-capture in_progress while we start."""
        i = VD.isolation(
            [{"name": "run85-phase2-capture", "id": 35122016351,
              "status": "in_progress"}], self.KNOWN, self_run_id="1")
        self.assertEqual(i["DIRECT_RESEARCH_COLLECTOR_ISOLATION"],
                         "NOT_ESTABLISHED")
        self.assertEqual(i["ACTIVE_DIRECT_CONFLICTS"], 1)
        self.assertEqual(i["CONFLICTS"][0]["NAME"], "run85-phase2-capture")

    def test_our_own_run_does_not_count_against_us(self):
        i = VD.isolation([{"name": "beta48-rate-confirm", "id": 99,
                           "status": "in_progress"}], self.KNOWN,
                         self_run_id=99)
        self.assertEqual(i["DIRECT_RESEARCH_COLLECTOR_ISOLATION"],
                         "ESTABLISHED")

    def test_a_queued_collector_counts_as_PENDING_not_active(self):
        """Idle cannot mean in_progress == 0: a pending member can be
        displaced by a newer queue, and ours could be the one displaced."""
        i = VD.domain_idle([{"name": "beta48-forward-capture", "id": 7,
                             "status": "queued"}], self.KNOWN, self_run_id="1")
        self.assertEqual(i["ACTIVE_DIRECT_CONFLICTS"], 0)
        self.assertEqual(i["PENDING_DIRECT_CONFLICTS"], 1)
        self.assertEqual(i["DOMAIN_IDLE"], "NO")
        self.assertIn("displace", i["WHY_NOT_IDLE"])

    def test_idle_requires_both_counts_at_zero(self):
        i = VD.domain_idle([], self.KNOWN, self_run_id="1")
        self.assertEqual(i["DOMAIN_IDLE"], "YES")
        self.assertIsNone(i["WHY_NOT_IDLE"])

    def test_an_unrelated_workflow_does_not_count(self):
        i = VD.isolation([{"name": "render-ops", "id": 7,
                           "status": "in_progress"}], self.KNOWN,
                         self_run_id="1")
        self.assertEqual(i["DIRECT_RESEARCH_COLLECTOR_ISOLATION"],
                         "ESTABLISHED")

    def test_a_completed_run_does_not_count(self):
        i = VD.isolation([{"name": "run85-phase2-capture", "id": 7,
                           "status": "completed"}], self.KNOWN,
                         self_run_id="1")
        self.assertEqual(i["DIRECT_RESEARCH_COLLECTOR_ISOLATION"],
                         "ESTABLISHED")


class TwoClassesNeverCollapsed(unittest.TestCase):
    """Class A is controlled and observable. Class B is neither. A single
    combined verdict would assert B on the strength of A."""

    def test_the_combined_label_is_refused_even_when_class_a_is_clean(self):
        i = VD.isolation([], ["x"], self_run_id="1")
        self.assertEqual(i["DIRECT_RESEARCH_COLLECTOR_ISOLATION"],
                         "ESTABLISHED")
        self.assertEqual(i["BETTOR_COLLECTOR_ISOLATION"],
                         "REFUSED_SCOPE_NOT_ESTABLISHED_FOR_INDIRECT_TRAFFIC")
        self.assertEqual(i["ALL_BETTOR_PMUS_TRAFFIC_ISOLATED"], "NO")

    def test_indirect_load_stays_unestablished_and_unmeasured(self):
        i = VD.isolation([], ["x"], self_run_id="1")
        self.assertEqual(i["INDIRECT_BETTOR_PMUS_LOAD_ISOLATION"],
                         "NOT_ESTABLISHED")
        self.assertEqual(i["INDIRECT_CONFOUND_MAGNITUDE"], NI)

    def test_not_seeing_indirect_traffic_is_not_zero_traffic(self):
        i = VD.isolation([], ["x"], self_run_id="1")
        self.assertIn("not evidence that there was none", i["ABSENCE_IS_NOT_ZERO"])


class TheStartGateAndTheDuringCheckAreDifferentRules(unittest.TestCase):
    """The idle window buys a CLEAN START, not a container. The confirmation
    does not have to finish before the next cron; once running, the domain
    queues later collectors behind it."""

    KNOWN = ["beta48-rate-confirm", "run85-phase2-capture",
             "beta48-forward-capture"]
    PENDING = [{"name": "beta48-forward-capture", "id": 7, "status": "queued"}]
    RUNNING = [{"name": "run85-phase2-capture", "id": 8,
                "status": "in_progress"}]

    def test_pending_blocks_the_start(self):
        i = VD.domain_idle(self.PENDING, self.KNOWN, self_run_id="9")
        self.assertEqual(i["DOMAIN_IDLE"], "NO")

    def test_pending_is_not_a_confound_during_the_run(self):
        """A collector queued behind us makes no request. Failing the run for
        that would punish the domain for working."""
        d = VD.during_run_conflict(self.PENDING, self.KNOWN, self_run_id="9")
        self.assertEqual(d["DIRECT_CONFLICT_STARTED_DURING_RUN"], "NO")
        self.assertEqual(d["PENDING_COLLECTORS_DURING_RUN"],
                         ["beta48-forward-capture"])
        self.assertTrue(d["PENDING_IS_NOT_A_CONFOUND"])

    def test_a_running_collector_during_the_run_is_a_confound(self):
        d = VD.during_run_conflict(self.RUNNING, self.KNOWN, self_run_id="9")
        self.assertEqual(d["DIRECT_CONFLICT_STARTED_DURING_RUN"], "YES")
        self.assertEqual(d["DIRECT_CONFLICTS_RUNNING_DURING_RUN"],
                         ["run85-phase2-capture"])

    def test_the_two_semantics_are_named_apart(self):
        self.assertEqual(VD.GATE_SEMANTICS_AT_START, "RUNNING_OR_PENDING_BLOCKS")
        self.assertEqual(VD.GATE_SEMANTICS_DURING_RUN,
                         "ONLY_RUNNING_IS_A_CONFOUND")

    def test_supersession_of_another_job_is_an_ops_issue(self):
        d = VD.during_run_conflict([], self.KNOWN, self_run_id="9")
        self.assertIn("NOT_A_CONFIRMATION_FAULT", d["PENDING_SUPERSESSION_IS"])

    def test_the_start_audit_names_the_collectors_not_just_a_count(self):
        i = VD.isolation(self.RUNNING + self.PENDING, self.KNOWN,
                         self_run_id="9")
        self.assertEqual(i["KNOWN_DIRECT_PMUS_COLLECTORS_ACTIVE"],
                         ["run85-phase2-capture"])
        self.assertEqual(i["KNOWN_DIRECT_PMUS_COLLECTORS_PENDING"],
                         ["beta48-forward-capture"])

    def test_the_audit_scans_names_not_group_membership(self):
        """run85's live run carries its OLD group, so membership would have
        missed it entirely."""
        self.assertIn("NOT_GROUP_MEMBERSHIP",
                      VD.isolation([], self.KNOWN, self_run_id="9")["AUDIT_SCANS"])


class WhatWeMayNotClaim(unittest.TestCase):

    def test_absolute_venue_isolation_is_refused(self):
        i = VD.isolation([], ["x"], self_run_id="1")
        self.assertEqual(i["NO_OTHER_CLIENT_ANYWHERE_IS_USING_THE_VENUE"],
                         "REFUSED_NOT_KNOWABLE")
        self.assertIn("not the venue's global traffic",
                      i["WHY_ABSOLUTE_IS_REFUSED"])

    def test_repository_isolation_is_the_claim_we_do_make(self):
        i = VD.isolation([], ["x"], self_run_id="1")
        self.assertEqual(i["NO_OTHER_KNOWN_BETTOR_GITHUB_COLLECTOR_RUNNING"],
                         "YES")


class TheQueueingLimitIsRecorded(unittest.TestCase):
    """GitHub keeps one PENDING run per group; a newer queue displaces it. That
    is a property of the platform, and pretending otherwise would let a
    confirmation vanish silently."""

    def test_the_limitation_travels_with_the_audit(self):
        a = VD.audit(ROOT)
        self.assertEqual(a["PENDING_RUN_MAY_BE_SUPERSEDED"], "YES")
        self.assertIn("cancels the older pending one",
                      a["WHY_PENDING_IS_NOT_SAFE"])
        self.assertIn("IDLE_DOMAIN", a["MITIGATION"])

    def test_the_domain_queues_rather_than_cancels(self):
        for n in VD.audit(ROOT)["KNOWN_VENUE_TOUCHING_WORKFLOWS"]:
            t = (ROOT / ".github/workflows" / ("%s.yml" % n)).read_text()
            self.assertIn("cancel-in-progress: false", t, n)


if __name__ == "__main__":
    unittest.main()


class ARunningWorkflowIsNotProvenVenueContact(unittest.TestCase):
    """Two levels of evidence, never collapsed into one claim.

    A job is RUNNING through checkout, install, tests, provenance and upload,
    none of which touches the venue. So a job-interval overlap is a POSSIBLE
    direct overlap; only the other run's own sealed request times make it a
    CONFIRMED one.
    """

    WS, WE = "2026-09-16T18:00:00Z", "2026-09-16T18:20:00Z"
    KNOWN = ["run85-phase2-capture"]
    LB = 340 * 60

    def _job(self, start, end, created="2026-09-16T10:00:00Z", rid=1):
        return {"name": "run85-phase2-capture", "id": rid, "status": "completed",
                "conclusion": "success", "created_at": created,
                "run_started_at": start, "updated_at": end}

    def _audit(self, runs, **kw):
        kw.setdefault("more_pages", {"*": False})
        kw.setdefault("pages_fetched", {"*": 1})
        kw.setdefault("lookback_s", self.LB)
        return VD.overlap_audit(self.WS, self.WE, runs, self.KNOWN,
                                self_run_id="9", **kw)

    def test_a_job_interval_overlap_alone_is_only_possible(self):
        a = self._audit([self._job("2026-09-16T18:05:00Z",
                                   "2026-09-16T18:10:00Z")])
        self.assertEqual(a["POSSIBLE_DIRECT_WORKFLOW_OVERLAP"], "YES")
        self.assertEqual(a["CONFIRMED_DIRECT_REQUEST_OVERLAP"], NI)
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], "POSSIBLE")
        self.assertEqual(a["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"],
                         "NOT_ESTABLISHED_POSSIBLE_OVERLAP")

    def test_the_fallback_is_labelled_a_conservative_proxy(self):
        a = self._audit([self._job("2026-09-16T18:05:00Z",
                                   "2026-09-16T18:10:00Z")])
        self.assertEqual(a["LEVEL_B_LABEL"],
                         "CONSERVATIVE_WORKFLOW_INTERVAL_PROXY")
        self.assertEqual(a["DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW"][0][
            "VENUE_REQUEST_TIMES"], NI)

    def test_no_get_timestamps_are_ever_synthesized(self):
        a = self._audit([self._job("2026-09-16T18:05:00Z",
                                   "2026-09-16T18:10:00Z")])
        row = a["DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW"][0]
        self.assertNotIn("OTHER_FIRST_VENUE_GET_TIME", row)
        self.assertTrue(a["DO_NOT_SYNTHESIZE_GET_TIMESTAMPS"])
        self.assertTrue(VD.DO_NOT_SYNTHESIZE_GET_TIMESTAMPS)

    def test_sealed_request_times_confirm_the_overlap(self):
        a = self._audit([self._job("2026-09-16T17:50:00Z",
                                   "2026-09-16T18:30:00Z")],
                        venue_windows={"1": ("2026-09-16T18:05:00Z",
                                             "2026-09-16T18:10:00Z")})
        self.assertEqual(a["CONFIRMED_DIRECT_REQUEST_OVERLAP"], "YES")
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], "YES")
        self.assertEqual(a["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"],
                         "NO_CONFIRMED")
        self.assertEqual(a["DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW"][0][
            "EVIDENCE_LEVEL"], "A_SEALED_VENUE_REQUEST_TIMES")

    def test_sealed_request_times_outside_the_window_clear_the_job(self):
        """Level A cuts both ways: the job ran through our window, its reads
        did not."""
        a = self._audit([self._job("2026-09-16T17:50:00Z",
                                   "2026-09-16T18:30:00Z")],
                        venue_windows={"1": ("2026-09-16T17:51:00Z",
                                             "2026-09-16T17:55:00Z")})
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], "NO")
        self.assertEqual(a["CONFIRMED_DIRECT_REQUEST_OVERLAP"], "NO")
        self.assertEqual(a["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"],
                         "YES")

    def test_the_four_conflict_values_are_distinct(self):
        clean = self._audit([])
        possible = self._audit([self._job("2026-09-16T18:05:00Z",
                                          "2026-09-16T18:10:00Z")])
        confirmed = self._audit(
            [self._job("2026-09-16T17:50:00Z", "2026-09-16T18:30:00Z")],
            venue_windows={"1": ("2026-09-16T18:05:00Z",
                                 "2026-09-16T18:10:00Z")})
        thin = self._audit([], more_pages={"*": True})
        vals = [x["DIRECT_CONFLICT_STARTED_DURING_RUN"]
                for x in (confirmed, possible, clean, thin)]
        self.assertEqual(vals, ["YES", "POSSIBLE", "NO", NI])
        isos = [x["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"]
                for x in (confirmed, possible, clean, thin)]
        self.assertEqual(isos, ["NO_CONFIRMED", "NOT_ESTABLISHED_POSSIBLE_OVERLAP",
                                "YES", NI])
        self.assertEqual(len(set(isos)), 4)


class CoverageIsPerWorkflowAndPagedToIt(unittest.TestCase):
    """One collector's history reaching back says nothing about another's."""

    WS, WE = "2026-09-16T18:00:00Z", "2026-09-16T18:20:00Z"
    KNOWN = ["run85-phase2-capture", "beta48-forward-capture"]
    LB = 340 * 60

    def _audit(self, runs, **kw):
        kw.setdefault("lookback_s", self.LB)
        return VD.overlap_audit(self.WS, self.WE, runs, self.KNOWN,
                                self_run_id="9", **kw)

    def _row(self, a, name):
        return {c["WORKFLOW_NAME"]: c
                for c in a["DIRECT_RUN_HISTORY_COVERAGE"]}[name]

    def test_every_known_collector_gets_its_own_row(self):
        a = self._audit([], more_pages={"*": True}, pages_fetched={"*": 2})
        self.assertEqual(sorted(c["WORKFLOW_NAME"]
                                for c in a["DIRECT_RUN_HISTORY_COVERAGE"]),
                         sorted(self.KNOWN))
        for c in a["DIRECT_RUN_HISTORY_COVERAGE"]:
            self.assertEqual(c["HISTORY_PAGES_FETCHED"], 2)
            self.assertEqual(c["MORE_PAGES_AVAILABLE"], "YES")
            self.assertEqual(c["COVERS_EVIDENCE_WINDOW"], "NO")

    def test_history_reaching_past_the_anchor_covers_that_workflow_only(self):
        deep = {"name": "run85-phase2-capture", "id": 3, "status": "completed",
                "conclusion": "success", "created_at": "2026-09-16T09:00:00Z",
                "run_started_at": "2026-09-16T09:00:00Z",
                "updated_at": "2026-09-16T09:30:00Z"}
        a = self._audit([deep], more_pages={"*": True})
        self.assertEqual(self._row(a, "run85-phase2-capture")[
            "COVERS_EVIDENCE_WINDOW"], "YES")
        self.assertEqual(self._row(a, "beta48-forward-capture")[
            "COVERS_EVIDENCE_WINDOW"], "NO")
        self.assertEqual(a["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "NO")

    def test_an_exhausted_api_covers_every_workflow(self):
        """No more pages means nothing older exists -- including for a
        workflow with no runs at all."""
        a = self._audit([], more_pages={"*": False})
        self.assertEqual(a["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "YES")
        for c in a["DIRECT_RUN_HISTORY_COVERAGE"]:
            self.assertEqual(c["COVERS_EVIDENCE_WINDOW"], "YES")

    def test_unknown_paging_is_not_assumed_complete(self):
        a = self._audit([])
        self.assertEqual(self._row(a, "run85-phase2-capture")[
            "MORE_PAGES_AVAILABLE"], NI)
        self.assertEqual(a["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "NO")
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], NI)

    def test_the_anchor_is_a_full_job_timeout_before_the_window(self):
        a = self._audit([], more_pages={"*": False})
        ws = datetime.fromisoformat(a["EVIDENCE_WINDOW"][0])
        anchor = datetime.fromisoformat(a["COVERAGE_ANCHOR"])
        self.assertEqual((ws - anchor).total_seconds(), self.LB)

    def test_the_lookback_is_read_from_the_workflows_not_guessed(self):
        """A run cannot outlive its own timeout-minutes, so the longest one is
        the lookback -- and run85 is created five hours before it overlaps."""
        self.assertGreaterEqual(VD.max_job_timeout_s(ROOT), 300 * 60)

    def test_a_detection_outranks_incomplete_coverage(self):
        hit = {"name": "run85-phase2-capture", "id": 4, "status": "completed",
               "conclusion": "success", "created_at": "2026-09-16T17:59:00Z",
               "run_started_at": "2026-09-16T18:05:00Z",
               "updated_at": "2026-09-16T18:10:00Z"}
        a = self._audit([hit], more_pages={"*": True})
        self.assertEqual(a["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "NO")
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], "POSSIBLE")
        self.assertTrue(a["COVERAGE_GATES_ONLY_THE_NEGATIVE"])
