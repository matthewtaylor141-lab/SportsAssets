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

    def _job(self, start, end, created="2026-09-16T10:00:00Z", rid=1):
        return {"name": "run85-phase2-capture", "id": rid, "status": "completed",
                "conclusion": "success", "created_at": created,
                "run_started_at": start, "updated_at": end}

    def _audit(self, runs, **kw):
        kw.setdefault("more_pages", {"*": False})
        kw.setdefault("pages_fetched", {"*": 1})
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


class AJobTimeoutBoundsExecutionNotQueueAge(unittest.TestCase):
    """The correction that cancelled the created-at lookback.

        CREATED -> QUEUED (unbounded) -> STARTS -> runs <= timeout-minutes

    The timeout bounds only the last leg, so a run created fifteen hours before
    the window can still begin EXECUTING inside it. Overlap is therefore judged
    on the job execution interval, and negative coverage has no finite
    created-at shortcut.
    """

    WS, WE = "2026-09-16T18:00:00Z", "2026-09-16T18:20:00Z"
    KNOWN = ["run85-phase2-capture"]
    # 340 minutes before the window start is 12:20. The run below is created
    # at 03:00 -- nine hours older than any such cutoff.
    LONG_QUEUED = {"name": "run85-phase2-capture", "id": 42,
                   "status": "completed", "conclusion": "success",
                   "created_at": "2026-09-16T03:00:00Z",
                   "run_started_at": "2026-09-16T18:05:00Z",
                   "updated_at": "2026-09-16T18:12:00Z"}

    def _audit(self, runs, **kw):
        kw.setdefault("more_pages", {"*": False})
        return VD.overlap_audit(self.WS, self.WE, runs, self.KNOWN,
                                self_run_id="9", **kw)

    def test_a_long_queued_run_executing_inside_the_window_is_detected(self):
        a = self._audit([self.LONG_QUEUED])
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], "POSSIBLE")
        self.assertEqual(a["STAGE_1_JOB_INTERVAL_CANDIDATES"], 1)
        self.assertEqual(a["DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW"][0][
            "JOB_EXECUTION_INTERVAL"][0], "2026-09-16T18:05:00+00:00")

    def test_a_created_at_cutoff_would_have_missed_it(self):
        """The rule the previous revision used, run against the same row. If
        this ever passes as a detection rule, the audit has regressed."""
        from datetime import datetime, timedelta
        ws = datetime.fromisoformat(self.WS.replace("Z", "+00:00"))
        cutoff = ws - timedelta(seconds=340 * 60)
        created = datetime.fromisoformat(
            self.LONG_QUEUED["created_at"].replace("Z", "+00:00"))
        self.assertLess(created, cutoff)          # excluded by created-at
        self.assertEqual(                          # detected anyway
            self._audit([self.LONG_QUEUED])["DIRECT_OVERLAP_COUNT"], 1)

    def test_the_overlap_interval_is_named_and_is_not_creation(self):
        a = self._audit([self.LONG_QUEUED])
        self.assertEqual(a["OVERLAP_INTERVAL"], "ACTUAL_JOB_EXECUTION_INTERVAL")
        self.assertEqual(a["NOT_THE_OVERLAP_INTERVAL"], "RUN_CREATION_INTERVAL")
        self.assertEqual(VD.TIMEOUT_BOUNDS, "EXECUTION_NOT_QUEUE_AGE")

    def test_a_run_created_inside_but_executing_after_the_window_is_clear(self):
        late = dict(self.LONG_QUEUED, id=43,
                    created_at="2026-09-16T18:05:00Z",
                    run_started_at="2026-09-16T19:00:00Z",
                    updated_at="2026-09-16T19:10:00Z")
        a = self._audit([late])
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], "NO")
        self.assertEqual(a["STAGE_1_JOB_INTERVAL_CANDIDATES"], 0)

    def test_a_missing_execution_start_widens_rather_than_narrows(self):
        """No run_started_at -> fall back to creation, which can only
        over-report. The substitution is labelled, never silent."""
        r = dict(self.LONG_QUEUED, id=44, run_started_at=None,
                 created_at="2026-09-16T18:10:00Z")
        a = self._audit([r])
        row = a["DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW"][0]
        self.assertEqual(row["JOB_INTERVAL_SOURCE"],
                         "RUN_CREATION_TIME_AS_START_PROXY_WIDER_NOT_NARROWER")

    def test_the_timeout_helper_disclaims_being_a_coverage_proof(self):
        self.assertTrue(VD.MAX_JOB_TIMEOUT_IS_NOT_A_COVERAGE_PROOF)
        self.assertIn("not a proof about execution",
                      VD.WHY_CREATED_AT_LOOKBACK_IS_REFUSED)


class TheAuditIsTwoStage(unittest.TestCase):
    """Stage 1 on metadata finds candidates; stage 2 seeks request times for
    those alone. A run that never executed during the window cannot have made a
    request during it, so its artifacts are never fetched."""

    WS, WE = "2026-09-16T18:00:00Z", "2026-09-16T18:20:00Z"
    KNOWN = ["run85-phase2-capture"]

    def _job(self, rid, start, end):
        return {"name": "run85-phase2-capture", "id": rid, "status": "completed",
                "conclusion": "success", "created_at": "2026-09-16T17:00:00Z",
                "run_started_at": start, "updated_at": end}

    def _audit(self, runs, **kw):
        kw.setdefault("more_pages", {"*": False})
        return VD.overlap_audit(self.WS, self.WE, runs, self.KNOWN,
                                self_run_id="9", **kw)

    def test_only_overlapping_jobs_become_stage_two_candidates(self):
        a = self._audit([
            self._job(1, "2026-09-16T18:05:00Z", "2026-09-16T18:10:00Z"),
            self._job(2, "2026-09-16T10:00:00Z", "2026-09-16T11:00:00Z"),
        ])
        self.assertEqual(a["STAGE_1_JOB_INTERVAL_CANDIDATES"], 1)
        self.assertEqual(a["DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW"][0]["ID"],
                         "1")
        self.assertEqual(list(VD.AUDIT_STAGES), a["AUDIT_STAGES"])

    def test_stage_two_evidence_is_counted_and_named(self):
        a = self._audit(
            [self._job(1, "2026-09-16T17:50:00Z", "2026-09-16T18:30:00Z")],
            venue_windows={"1": ("2026-09-16T18:05:00Z",
                                 "2026-09-16T18:10:00Z")})
        self.assertEqual(a["STAGE_2_CANDIDATES_WITH_SEALED_REQUEST_TIMES"], 1)
        self.assertEqual(a["DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW"][0][
            "REQUEST_TIME_EVIDENCE"], "SEALED")

    def test_a_candidate_cleared_by_its_own_request_times_is_recorded(self):
        a = self._audit(
            [self._job(1, "2026-09-16T17:50:00Z", "2026-09-16T18:30:00Z")],
            venue_windows={"1": ("2026-09-16T17:51:00Z",
                                 "2026-09-16T17:55:00Z")})
        self.assertEqual(a["DIRECT_OVERLAP_COUNT"], 0)
        self.assertEqual(len(a["STAGE_2_CANDIDATES_CLEARED_BY_REQUEST_TIMES"]),
                         1)
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], "NO")


class CoverageIsPerWorkflowAndProvedOnlyByExhaustion(unittest.TestCase):
    """One collector's history reaching back says nothing about another's, and
    nothing short of an exhausted history proves the negative."""

    WS, WE = "2026-09-16T18:00:00Z", "2026-09-16T18:20:00Z"
    KNOWN = ["run85-phase2-capture", "beta48-forward-capture"]

    def _audit(self, runs, **kw):
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
            for k in ("WORKFLOW_NAME", "HISTORY_PAGES_FETCHED",
                      "EARLIEST_RUN_TIME_FETCHED", "LATEST_RUN_TIME_FETCHED",
                      "EARLIEST_JOB_START_FETCHED", "MORE_PAGES_AVAILABLE",
                      "COVERS_EVIDENCE_WINDOW"):
                self.assertIn(k, c)
            self.assertEqual(c["HISTORY_PAGES_FETCHED"], 2)
            self.assertEqual(c["COVERS_EVIDENCE_WINDOW"], "NOT_ESTABLISHED")

    def test_coverage_is_exhausted_per_workflow_not_globally(self):
        a = self._audit([], more_pages={"run85-phase2-capture": False,
                                        "beta48-forward-capture": True})
        self.assertEqual(self._row(a, "run85-phase2-capture")[
            "COVERS_EVIDENCE_WINDOW"], "YES")
        self.assertEqual(self._row(a, "beta48-forward-capture")[
            "COVERS_EVIDENCE_WINDOW"], "NOT_ESTABLISHED")
        self.assertEqual(a["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "NO")
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], NI)

    def test_deep_history_short_of_exhaustion_does_not_cover(self):
        """The created-at shortcut is gone: an old run in hand proves nothing
        about an older one still off the page."""
        deep = {"name": "run85-phase2-capture", "id": 3, "status": "completed",
                "conclusion": "success", "created_at": "2026-09-15T01:00:00Z",
                "run_started_at": "2026-09-15T01:00:00Z",
                "updated_at": "2026-09-15T01:30:00Z"}
        a = self._audit([deep], more_pages={"*": True})
        self.assertEqual(self._row(a, "run85-phase2-capture")[
            "COVERS_EVIDENCE_WINDOW"], "NOT_ESTABLISHED")
        self.assertEqual(a["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "NO")

    def test_an_exhausted_api_covers_that_workflow(self):
        a = self._audit([], more_pages={"*": False})
        self.assertEqual(a["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "YES")
        for c in a["DIRECT_RUN_HISTORY_COVERAGE"]:
            self.assertEqual(c["COVERS_EVIDENCE_WINDOW"], "YES")
            self.assertEqual(c["COVERAGE_ROUTE"], "API_HISTORY_EXHAUSTED")

    def test_unknown_paging_is_not_assumed_complete(self):
        a = self._audit([])
        self.assertEqual(self._row(a, "run85-phase2-capture")[
            "HISTORY_EXHAUSTED"], NI)
        self.assertEqual(a["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "NO")
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], NI)

    def test_route_b_is_named_and_refused_not_approximated(self):
        a = self._audit([], more_pages={"*": True})
        self.assertEqual(a["COVERAGE_REQUIRES"], "API_HISTORY_EXHAUSTED")
        self.assertEqual(a["COVERAGE_ROUTE_B_STATUS"],
                         "NOT_ESTABLISHED_QUEUE_DELAY_IS_UNBOUNDED")

    def test_a_detection_outranks_incomplete_coverage(self):
        hit = {"name": "run85-phase2-capture", "id": 4, "status": "completed",
               "conclusion": "success", "created_at": "2026-09-16T17:59:00Z",
               "run_started_at": "2026-09-16T18:05:00Z",
               "updated_at": "2026-09-16T18:10:00Z"}
        a = self._audit([hit], more_pages={"*": True})
        self.assertEqual(a["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "NO")
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], "POSSIBLE")
        self.assertTrue(a["COVERAGE_GATES_ONLY_THE_NEGATIVE"])


class TheCleanNegativeStandard(unittest.TestCase):
    """ISOLATION_THROUGHOUT_RUN = YES needs all three: no confirmed overlap, no
    possible overlap, and complete coverage. Any shortfall is NOT_IDENTIFIED
    and the confirmation may not certify the rate."""

    WS, WE = "2026-09-16T18:00:00Z", "2026-09-16T18:20:00Z"
    KNOWN = ["run85-phase2-capture"]
    K = "DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"

    def _audit(self, runs, **kw):
        return VD.overlap_audit(self.WS, self.WE, runs, self.KNOWN,
                                self_run_id="9", **kw)

    def test_all_three_conditions_give_yes(self):
        a = self._audit([], more_pages={"*": False})
        self.assertEqual(a["CONFIRMED_DIRECT_REQUEST_OVERLAP"], "NO")
        self.assertEqual(a["POSSIBLE_DIRECT_WORKFLOW_OVERLAP"], "NO")
        self.assertEqual(a["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "YES")
        self.assertEqual(a[self.K], "YES")

    def test_incomplete_coverage_alone_withholds_the_yes(self):
        a = self._audit([], more_pages={"*": True})
        self.assertEqual(a["POSSIBLE_DIRECT_WORKFLOW_OVERLAP"], "NO")
        self.assertEqual(a[self.K], NI)


class BothEndpointsFailTowardsDetection(unittest.TestCase):
    """A missing timestamp may WIDEN an execution interval and may never narrow
    one. Narrowing turns a real overlap into a clean run, which is the single
    error this audit exists to prevent.

    The start endpoint was handled when execution intervals landed. This pins
    the other one: a job that has STARTED and has no completion time must stay
    in the overlap test, open-ended, not be dropped.
    """

    WS, WE = "2026-09-16T18:00:00Z", "2026-09-16T18:20:00Z"
    KNOWN = ["run85-phase2-capture"]

    def _audit(self, runs, **kw):
        kw.setdefault("more_pages", {"*": False})
        return VD.overlap_audit(self.WS, self.WE, runs, self.KNOWN,
                                self_run_id="9", **kw)

    def _active(self, started, rid=1, created="2026-09-16T12:00:00Z"):
        """Still running: no conclusion, and completion is genuinely absent."""
        return {"name": "run85-phase2-capture", "id": rid,
                "status": "in_progress", "conclusion": None,
                "created_at": created, "run_started_at": started,
                "updated_at": None}

    def test_an_active_job_started_before_the_window_still_overlaps(self):
        """Started 17:00, still in_progress, completed_at null. It has been
        reading across our whole window."""
        a = self._audit([self._active("2026-09-16T17:00:00Z")])
        self.assertEqual(a["DIRECT_OVERLAP_COUNT"], 1)
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], "POSSIBLE")

    def test_an_active_job_started_during_the_window_still_overlaps(self):
        a = self._audit([self._active("2026-09-16T18:05:00Z")])
        self.assertEqual(a["DIRECT_OVERLAP_COUNT"], 1)
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], "POSSIBLE")

    def test_an_active_job_started_after_the_window_does_not_overlap_it(self):
        """A run that began after 18:20 cannot have contaminated 18:00-18:20,
        however long it goes on running."""
        a = self._audit([self._active("2026-09-16T19:30:00Z")])
        self.assertEqual(a["STAGE_1_JOB_INTERVAL_CANDIDATES"], 0)
        self.assertEqual(a["DIRECT_CONFLICT_STARTED_DURING_RUN"], "NO")

    def test_an_open_end_is_reported_as_open_never_as_the_window_edge(self):
        a = self._audit([self._active("2026-09-16T17:00:00Z")])
        row = a["DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW"][0]
        self.assertEqual(row["JOB_END_SOURCE"], "OPEN_ENDED_STILL_RUNNING")
        self.assertEqual(row["JOB_EXECUTION_INTERVAL"][1],
                         "OPEN_ENDED_STILL_RUNNING")
        self.assertNotIn("18:20", row["JOB_EXECUTION_INTERVAL"][1])

    def test_a_completed_run_missing_its_completion_time_is_open_ended_too(self):
        r = {"name": "run85-phase2-capture", "id": 2, "status": "completed",
             "conclusion": "success", "created_at": "2026-09-16T12:00:00Z",
             "run_started_at": "2026-09-16T17:00:00Z", "updated_at": None}
        a = self._audit([r])
        row = a["DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW"][0]
        self.assertEqual(row["JOB_END_SOURCE"],
                         "OPEN_ENDED_COMPLETION_TIMESTAMP_MISSING")
        self.assertEqual(a["DIRECT_OVERLAP_COUNT"], 1)

    def test_the_narrowing_substitutions_are_refused_by_name(self):
        a = self._audit([self._active("2026-09-16T17:00:00Z")])
        self.assertEqual(sorted(a["END_SUBSTITUTIONS_REFUSED"]),
                         ["END_EQUALS_CREATED", "END_EQUALS_START",
                          "END_NULL_THEN_DROP_THE_ROW"])
        # each refused substitution would have ended this run at or before
        # 17:00 and dropped it from an 18:00-18:20 window
        self.assertEqual(a["DIRECT_OVERLAP_COUNT"], 1)

    def test_the_three_policies_are_reported(self):
        a = self._audit([])
        self.assertEqual(a["MISSING_START_POLICY"],
                         "SUBSTITUTE_CREATION_TIME_WIDENS_NEVER_NARROWS")
        self.assertEqual(a["MISSING_END_ACTIVE_POLICY"],
                         "OPEN_ENDED_THROUGH_THE_WINDOW_ROW_NEVER_DROPPED")
        self.assertEqual(a["MISSING_END_COMPLETED_POLICY"],
                         "OPEN_ENDED_THROUGH_THE_WINDOW_ROW_NEVER_DROPPED")

    def test_a_level_a_row_on_a_running_job_does_not_print_a_fake_end(self):
        """Sealed request times classify it; the job's own end is still open."""
        a = self._audit([self._active("2026-09-16T17:00:00Z")],
                        venue_windows={"1": ("2026-09-16T18:05:00Z",
                                             "2026-09-16T18:10:00Z")})
        row = a["DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW"][0]
        self.assertEqual(row["EVIDENCE_LEVEL"], "A_SEALED_VENUE_REQUEST_TIMES")
        self.assertEqual(row["JOB_EXECUTION_INTERVAL"][1],
                         "OPEN_ENDED_STILL_RUNNING")
        self.assertEqual(a["CONFIRMED_DIRECT_REQUEST_OVERLAP"], "YES")
