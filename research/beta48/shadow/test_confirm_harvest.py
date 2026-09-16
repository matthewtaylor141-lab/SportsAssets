#!/usr/bin/env python3
"""The confirmation harvest: the literal duration floor, and three properties
that are never collapsed into one.
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import confirm_harvest as CH
import rate_confirm as RC

NI = CH.NOT_IDENTIFIED
T0 = datetime(2026, 9, 16, 18, 3, 15, tzinfo=timezone.utc)


def sealed(outdir, requests=300, rps="0.25", statuses=None, slugs=None,
           interval=4.0, latency=0.03, order=None, backoff_s=0.0, prov=None,
           history="clean", more_pages=False, venue_windows=None):
    """Write a sealed confirmation exactly as the workflow would.

    backoff_s adds a forced pause AFTER each 429, exactly as a global backoff
    honouring Retry-After would stretch the wall clock.
    """
    slugs = slugs or ["a", "b", "c", "d", "e", "f"]
    order = order or RC.rotation_order(slugs, requests)
    statuses = statuses or [200] * requests
    rows, t = [], 0.0
    for i in range(requests):
        rows.append({
            "slug": order[i], "SEQ": i, "status": statuses[i],
            "RECEIPT_UTC": (T0 + timedelta(seconds=t)).isoformat(),
            "LATENCY_S": latency, "HEADERS": {}, "RATE": rps,
            "error": None if statuses[i] == 200 else "http_%d" % statuses[i],
        })
        t += interval + (backoff_s if statuses[i] == 429 else 0.0)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "confirm_rows.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    (out / "confirm_report.json").write_text(json.dumps({
        "RATE_RPS": rps, "DURATION_S": (requests - 1) * interval + latency,
        "RETRY_AFTER_COUNT": sum(1 for s in statuses if s == 429),
        "RETRY_AFTER_OBSERVED": ["10"] if 429 in statuses else [],
        "GLOBAL_BACKOFF_EVENTS": sum(1 for s in statuses if s == 429),
    }))
    if prov is not None:
        (out / "provenance.json").write_text(json.dumps(prov))
    if history is not None:
        # A normal sealed run carries run history PAGED TO EXHAUSTION, so
        # every known collector's coverage is established. Without history at
        # all, isolation-throughout is NOT_IDENTIFIED and the run cannot
        # validate -- which is the point, so it must be supplied deliberately.
        # `more_pages=True` is the thin-history case: the fetch stopped with
        # runs still unread.
        runs = history
        if history == "clean":
            runs = [{"name": "run85-phase2-capture", "id": 99,
                     "status": "completed", "conclusion": "success",
                     "created_at": "2026-09-16T09:00:00Z",
                     "run_started_at": "2026-09-16T09:00:00Z",
                     "updated_at": "2026-09-16T10:00:00Z"}]
        (out / "run_history.json").write_text(json.dumps({
            "workflow_runs": runs,
            "HISTORY_PAGES_FETCHED": 1,
            "MORE_PAGES_AVAILABLE": bool(more_pages),
            "VENUE_REQUEST_WINDOWS": venue_windows or {},
        }))
    return str(out)


SHA = "2c3b261f9d61e12aaa26ff5942f6cf481d7e43a1"


def legacy_prov(outdir=None, **over):
    """A v1 artifact, exactly as commit 2c3b261 writes it: EXECUTED_SHA, no
    EXECUTED_SHA_ACTUAL, no four-aspect split."""
    d = {"DISPATCH_SHA": SHA, "EXECUTED_SHA": SHA, "WORKFLOW_REF_SHA": SHA,
         "WORKFLOW_FILE_SHA": "wfhash", "CONFIG_SHA": "cfghash",
         "DATA_OUTPUT_SHA": "outhash", "EVIDENCE_RUN_VALIDITY": "PASS"}
    d.update(over)
    return d


class TheOffByOneIsRealAndDecides(unittest.TestCase):
    """300 requests at 0.25 rps span 299 intervals, not 300."""

    def test_the_pacing_semantics_are_n_minus_one(self):
        self.assertEqual(CH.planned_elapsed_s(300, 0.25), 1196.0)
        self.assertEqual(CH.planned_elapsed_s(301, 0.25), 1200.0)

    def test_the_request_count_that_actually_occupies_the_floor(self):
        self.assertEqual(CH.requests_for_duration(0.25, 1200.0), 301)
        self.assertEqual(CH.requests_for_duration(2.0, 900.0), 1801)

    def test_a_perfect_300_request_run_still_fails_the_duration_floor(self):
        """Every request succeeded. It elapsed 1,196.03 s. That is NOT 1,200."""
        r = CH.harvest(sealed(tempfile.mkdtemp()))
        self.assertEqual(r["REQUESTS"], 300)
        self.assertEqual(r["SUCCESSES"], 300)
        self.assertEqual(r["HTTP_429"], 0)
        self.assertAlmostEqual(r["NOMINAL_PACED_EXPOSURE_S"], 1196.0, 2)
        self.assertTrue(r["REQUESTS_MEET_FLOOR"])
        self.assertFalse(r["DURATION_MEETS_FLOOR"])
        self.assertEqual(r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")
        self.assertIn("DURATION_SUPPORT_FLOOR_NOT_MET", r["FAIL_REASON"])

    def test_1196_is_not_rounded_to_1200(self):
        r = CH.harvest(sealed(tempfile.mkdtemp()))
        self.assertTrue(r["DURATION_WAS_NOT_ROUNDED"])
        self.assertEqual(r["MIN_PACED_EXPOSURE_S_REQUIRED"], 1200.0)
        self.assertLess(r["NOMINAL_PACED_EXPOSURE_S"], 1200.0)

    def test_the_verdict_is_structural_not_a_venue_result(self):
        r = CH.harvest(sealed(tempfile.mkdtemp()))
        self.assertTrue(r["DURATION_VERDICT_IS_STRUCTURAL"])
        self.assertIn("299 intervals", r["WHY_STRUCTURAL"])

    def test_a_failed_duration_proposes_no_capture_rate(self):
        r = CH.harvest(sealed(tempfile.mkdtemp()))
        self.assertEqual(r["PROPOSED_SUBSTANTIVE_CAPTURE_RATE"], NI)

    def test_301_requests_clears_the_floor(self):
        r = CH.harvest(sealed(tempfile.mkdtemp(), requests=301))
        self.assertGreaterEqual(r["NOMINAL_PACED_EXPOSURE_S"], 1200.0)
        self.assertTrue(r["DURATION_MEETS_FLOOR"])
        self.assertEqual(r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "YES")
        self.assertEqual(r["PROPOSED_SUBSTANTIVE_CAPTURE_RATE"], "0.25 RPS")

    def test_too_few_requests_fails_on_its_own_reason(self):
        r = CH.harvest(sealed(tempfile.mkdtemp(), requests=100))
        self.assertIn("REQUEST_SUPPORT_FLOOR_NOT_MET", r["FAIL_REASON"])

    def test_the_run_report_is_re_derived_not_trusted(self):
        """The sealed DURATION_S is reported beside the derived spans, and the
        verdict uses the paced exposure."""
        r = CH.harvest(sealed(tempfile.mkdtemp()))
        self.assertIn("RUN_REPORTED_DURATION_S", r)
        self.assertEqual(r["SUPPORT_CRITERION_USES"], "NOMINAL_PACED_EXPOSURE_S")

    def test_the_timestamp_uncertainty_is_stated(self):
        r = CH.harvest(sealed(tempfile.mkdtemp()))
        self.assertEqual(r["TIMESTAMP_RESOLUTION_S"], 1.0)
        for k in ("FIRST_REQUEST_START", "LAST_REQUEST_START",
                  "LAST_REQUEST_COMPLETION"):
            self.assertIn(k, r, k)


class NothingMayRescueTheSupportFloor(unittest.TestCase):
    """The floor measures SUSTAINED OPERATION AT THE RATE. Two things stretch a
    wall clock without adding a second of that evidence, and neither may carry
    a run over the line."""

    def test_a_venue_backoff_does_not_buy_exposure(self):
        """A 60 s Retry-After pause pushes wall-clock past 1,200 s. The paced
        exposure does not move, and the verdict does not move either. A refusal
        must never help a rate pass."""
        st = [200] * 300
        st[100] = 429
        r = CH.harvest(sealed(tempfile.mkdtemp(), statuses=st, backoff_s=60.0))
        self.assertGreater(r["WALL_CLOCK_ELAPSED_S"], 1200.0)
        self.assertAlmostEqual(r["NOMINAL_PACED_EXPOSURE_S"], 1196.0, 2)
        self.assertAlmostEqual(r["FORCED_SUSPENSION_S"], 60.0, 2)
        self.assertFalse(r["DURATION_MEETS_FLOOR"])
        self.assertEqual(r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")
        self.assertIn("DURATION_SUPPORT_FLOOR_NOT_MET", r["FAIL_REASON"])

    def test_the_exclusion_is_stated_with_its_reason(self):
        r = CH.harvest(sealed(tempfile.mkdtemp()))
        self.assertTrue(r["BACKOFF_EXCLUDED_FROM_SUPPORT"])
        self.assertTrue(r["LATENCY_EXCLUDED_FROM_SUPPORT"])
        self.assertIn("may not help a rate pass", r["WHY_BACKOFF_IS_EXCLUDED"])

    def test_latency_does_not_buy_exposure_either(self):
        """A pathological 5 s final latency would put completion-minus-start
        past 1,200. Exposure measures STARTS, so it cannot."""
        r = CH.harvest(sealed(tempfile.mkdtemp(), latency=5.0))
        self.assertGreater(r["WALL_CLOCK_ELAPSED_S"], 1200.0)
        self.assertAlmostEqual(r["NOMINAL_PACED_EXPOSURE_S"], 1196.0, 2)
        self.assertEqual(r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")

    def test_a_long_runner_stall_does_not_buy_exposure(self):
        """Not every pause is a 429. Capping each interval at its nominal value
        excludes stalls without needing to know what caused them."""
        st = [200] * 300
        st[150] = 500                      # not a refusal, still a long gap
        r = CH.harvest(sealed(tempfile.mkdtemp(), statuses=st, backoff_s=0.0))
        self.assertAlmostEqual(r["NOMINAL_PACED_EXPOSURE_S"], 1196.0, 2)


class TheTwoQuestionsAreReportedApart(unittest.TestCase):
    """What this run can answer, and what it cannot."""

    def test_a_clean_short_run_answers_one_and_refuses_the_other(self):
        r = CH.harvest(sealed(tempfile.mkdtemp()))
        self.assertEqual(r["OPERATIONALLY_CLEAN_OVER_THE_REQUESTS_ISSUED"],
                         "YES")
        self.assertEqual(r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")
        self.assertEqual(r["OPERATIONAL_FAILURES"], None)
        self.assertEqual(r["FAIL_REASON"], ["DURATION_SUPPORT_FLOOR_NOT_MET"])

    def test_the_duration_floor_is_absent_from_the_cleanliness_question(self):
        """Otherwise the two questions are one question wearing two names."""
        r = CH.harvest(sealed(tempfile.mkdtemp()))
        self.assertNotIn("DURATION_SUPPORT_FLOOR_NOT_MET",
                         r["OPERATIONAL_FAILURES"] or [])

    def test_a_venue_failure_shows_in_both(self):
        st = [429] * 300
        r = CH.harvest(sealed(tempfile.mkdtemp(), statuses=st))
        self.assertEqual(r["OPERATIONALLY_CLEAN_OVER_THE_REQUESTS_ISSUED"],
                         "NO")
        self.assertIn("REFUSALS_EXCEED_FROZEN_ALLOWANCE",
                      r["OPERATIONAL_FAILURES"])
        self.assertIn("REFUSALS_EXCEED_FROZEN_ALLOWANCE", r["FAIL_REASON"])


class TheCorrectedConfirmationIsOfferedOnlyWhenEarned(unittest.TestCase):

    def test_a_clean_but_short_run_earns_the_corrected_proposal(self):
        r = CH.harvest(sealed(tempfile.mkdtemp()))
        self.assertEqual(r["PROPOSE_REPEAT_AT_THIS_RATE"], "YES")
        self.assertEqual(r["CORRECTED_CONFIRMATION_RATE"], "0.25 RPS")
        self.assertEqual(r["CORRECTED_CONFIRMATION_REQUESTS"], 301)
        self.assertEqual(r["MIN_PACED_EXPOSURE_S"], 1200.0)
        self.assertEqual(r["CORRECTED_REQUEST_INTERVAL_S"], 4.0)
        self.assertTrue(r["CORRECTED_CONFIRMATION_IS_NOT_DISPATCHED"])

    def test_a_run_the_venue_refused_earns_no_repeat(self):
        """Repeating 0.25 rps merely to fix our own off-by-one would re-ask a
        question the venue already answered."""
        r = CH.harvest(sealed(tempfile.mkdtemp(), statuses=[429] * 300))
        self.assertEqual(r["PROPOSE_REPEAT_AT_THIS_RATE"], "NO")
        self.assertEqual(r["CORRECTED_CONFIRMATION_RATE"], NI)
        self.assertIn("adverse answer", r["WHY_NOT_PROPOSED"])

    def test_a_validated_run_needs_no_repeat(self):
        r = CH.harvest(sealed(tempfile.mkdtemp(), requests=301))
        self.assertEqual(r["PROPOSE_REPEAT_AT_THIS_RATE"], "NO")
        self.assertIn("already validated", r["WHY_NOT_PROPOSED"])


class ThreePropertiesNeverCollapsed(unittest.TestCase):

    SLUGS = ["a", "b", "c", "d", "e", "f"]

    def test_fairness_reads_no_outcome_at_all(self):
        """Same ordering, opposite outcomes -- identical fairness verdict."""
        good = sealed(tempfile.mkdtemp())
        bad = sealed(tempfile.mkdtemp(), statuses=[429] * 300)
        a, b = CH.harvest(good), CH.harvest(bad)
        self.assertEqual(a["POLL_ORDER_FAIRNESS"], b["POLL_ORDER_FAIRNESS"])
        self.assertEqual(a["CYCLE_LEAD_COUNTS"], b["CYCLE_LEAD_COUNTS"])
        self.assertFalse(a["FAIRNESS_READS_OUTCOMES"])

    def test_a_fair_schedule_can_still_read_badly(self):
        """The case the guard exists for: perfect rotation, poor coverage.
        Every 429 lands on one market, and fairness must still say PASS."""
        order = RC.rotation_order(self.SLUGS, 300)
        st = [429 if order[i] == "c" else 200 for i in range(300)]
        r = CH.harvest(sealed(tempfile.mkdtemp(), statuses=st))
        self.assertEqual(r["POLL_ORDER_FAIRNESS"], "PASS")
        self.assertEqual(r["SUCCESS_COVERAGE_BALANCED"], "NO")
        self.assertEqual(r["POLL_ORDER_STARVATION"], "YES")
        self.assertEqual(r["STARVED_MARKETS"], ["c"])

    def test_a_privileged_order_fails_fairness_though_coverage_is_perfect(self):
        """The mirror case: every market reads 100%, ordering still rigged."""
        fixed = [self.SLUGS[i % 6] for i in range(300)]
        r = CH.harvest(sealed(tempfile.mkdtemp(), order=fixed))
        self.assertEqual(r["SUCCESS_COVERAGE_BALANCED"], "YES")
        self.assertEqual(r["POLL_ORDER_STARVATION"], "NO")
        self.assertEqual(r["POLL_ORDER_FAIRNESS"], "FAIL")
        self.assertIn("POLL_ORDER_NOT_FAIR", r["FAIL_REASON"])

    def test_the_three_verdicts_are_three_fields(self):
        r = CH.harvest(sealed(tempfile.mkdtemp()))
        for k in ("POLL_ORDER_FAIRNESS", "SUCCESS_COVERAGE_BALANCED",
                  "POLL_ORDER_STARVATION"):
            self.assertIn(k, r, k)
        self.assertIn("property of the schedule",
                      r["THESE_ARE_DIFFERENT_PROPERTIES"])

    def test_fairness_counts_leads_attempts_and_positions(self):
        r = CH.harvest(sealed(tempfile.mkdtemp()))
        self.assertEqual(set(r["ATTEMPT_COUNTS"].values()), {50})
        self.assertLessEqual(r["CYCLE_LEAD_IMBALANCE"], 1)
        self.assertLessEqual(r["POSITION_IN_CYCLE_IMBALANCE"], 1)

    def test_per_market_carries_all_four_counts_and_the_share(self):
        order = RC.rotation_order(["a", "b"], 4)
        st = [200, 500, 200, 200]
        r = CH.harvest(sealed(tempfile.mkdtemp(), requests=4, slugs=["a", "b"],
                              statuses=st, order=order))
        for d in r["PER_MARKET"].values():
            for k in ("ATTEMPTS", "SUCCESSES", "HTTP_429", "OTHER_FAILURES",
                      "SUCCESS_SHARE"):
                self.assertIn(k, d, k)


class TheRefusalRulesStillApply(unittest.TestCase):

    def test_many_refusals_fail_even_on_a_long_enough_run(self):
        st = [200] * 301
        for i in (10, 40, 90):
            st[i] = 429
        r = CH.harvest(sealed(tempfile.mkdtemp(), requests=301, statuses=st))
        self.assertEqual(r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")
        self.assertIn("REFUSALS_EXCEED_FROZEN_ALLOWANCE", r["FAIL_REASON"])

    def test_a_run_ending_on_a_refusal_did_not_resume(self):
        st = [200] * 300 + [429]
        r = CH.harvest(sealed(tempfile.mkdtemp(), requests=301, statuses=st))
        self.assertEqual(r["SUCCESSES_AFTER_LAST_429"], 0)
        self.assertIn("READS_DID_NOT_RESUME_AFTER_THE_REFUSAL", r["FAIL_REASON"])

    def test_one_isolated_recovered_refusal_still_validates(self):
        st = [200] * 301
        st[50] = 429
        r = CH.harvest(sealed(tempfile.mkdtemp(), requests=301, statuses=st))
        self.assertEqual(r["HTTP_429"], 1)
        self.assertGreater(r["SUCCESSES_AFTER_LAST_429"], 0)
        self.assertEqual(r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "YES")


class TheSchemaGapIsRecordedNotSmoothedOver(unittest.TestCase):
    """The executing commit writes v1; this harvester is v2. A reader must see
    that a field was RENAMED in translation, not invented."""

    def _harvest(self, **over):
        rows_dir = sealed(tempfile.mkdtemp(), requests=301,
                          prov=legacy_prov(**over))
        return CH.harvest(rows_dir), rows_dir

    def test_both_schema_versions_are_reported(self):
        r, _ = self._harvest()
        self.assertEqual(r["EXECUTION_PROVENANCE_SCHEMA_VERSION"], 1)
        self.assertEqual(r["HARVEST_PROVENANCE_SCHEMA_VERSION"], 2)
        self.assertEqual(r["LEGACY_PROVENANCE_FALLBACK_USED"], "YES")

    def test_the_field_mapping_is_explicit(self):
        r, _ = self._harvest()
        self.assertIn("EXECUTED_SHA -> EXECUTED_SHA_ACTUAL",
                      r["LEGACY_FIELD_MAPPING"])
        self.assertEqual(r["EXECUTED_SHA_ACTUAL"], SHA)

    def test_a_v2_artifact_reports_no_fallback(self):
        rows_dir = sealed(tempfile.mkdtemp(), requests=301, prov=dict(
            legacy_prov(), EXECUTED_SHA_ACTUAL=SHA,
            EXECUTED_SHA_SOURCE="RUNNER_GIT_REV_PARSE"))
        r = CH.harvest(rows_dir)
        self.assertEqual(r["EXECUTION_PROVENANCE_SCHEMA_VERSION"], 2)
        self.assertEqual(r["LEGACY_PROVENANCE_FALLBACK_USED"], "NO")
        self.assertIsNone(r["LEGACY_FIELD_MAPPING"])

    def test_each_aspect_names_its_source(self):
        r, _ = self._harvest()
        self.assertEqual(r["CODE_PROVENANCE_SOURCE"],
                         "SEALED_RUNNER_OBSERVED_GIT_REV_PARSE_HEAD")
        self.assertEqual(r["CONFIG_PROVENANCE_SOURCE"], "SEALED_CONFIG_HASH")
        self.assertEqual(r["WORKFLOW_PROVENANCE_SOURCE"],
                         "SEALED_WORKFLOW_FILE_HASH")
        self.assertIn("SEALED_OUTPUT_HASH_WRITTEN_BY_THE_RUN",
                      r["DATA_OUTPUT_PROVENANCE_SOURCE"])
        self.assertTrue(r["NOT_INFERRED_FROM_THE_CURRENT_REPOSITORY"])


class ARehashIsNotOriginalProvenance(unittest.TestCase):
    """The defect this class exists to prevent: a harvest-time rehash standing
    in for a sealed hash the run never wrote."""

    def test_a_missing_sealed_output_hash_stays_not_recorded(self):
        """The rows file is present and hashable. That does NOT make the
        output provenance recorded."""
        rows_dir = sealed(tempfile.mkdtemp(), requests=301,
                          prov=legacy_prov(DATA_OUTPUT_SHA=None))
        r = CH.harvest(rows_dir)
        self.assertNotEqual(r["HARVEST_RECOMPUTED_DATA_SHA"], NI)
        self.assertEqual(r["SEALED_DATA_OUTPUT_SHA"], NI)
        self.assertEqual(r["DATA_OUTPUT_PROVENANCE"], "NOT_RECORDED")
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL")

    def test_the_three_hash_facts_are_separate_fields(self):
        rows_dir = sealed(tempfile.mkdtemp(), requests=301,
                          prov=legacy_prov())
        r = CH.harvest(rows_dir)
        self.assertEqual(r["SEALED_DATA_OUTPUT_SHA"], "outhash")
        self.assertNotEqual(r["HARVEST_RECOMPUTED_DATA_SHA"], "outhash")
        self.assertEqual(r["DATA_OUTPUT_SHA_MATCHES_SEALED_RECORD"], "NO")
        self.assertTrue(r["REHASH_IS_VERIFICATION_NOT_PROVENANCE"])

    def test_a_matching_rehash_is_reported_as_verification(self):
        d = tempfile.mkdtemp()
        sealed(d, requests=301, prov=legacy_prov())
        real = CH.PV.file_sha256(Path(d) / "confirm_rows.jsonl")
        sealed(d, requests=301, prov=legacy_prov(DATA_OUTPUT_SHA=real))
        r = CH.harvest(d)
        self.assertEqual(r["DATA_OUTPUT_SHA_MATCHES_SEALED_RECORD"], "YES")
        self.assertIn("VERIFIED_BY_HARVEST_REHASH",
                      r["DATA_OUTPUT_PROVENANCE_SOURCE"])
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "PASS")

    def test_a_missing_config_hash_is_not_read_off_the_repository(self):
        rows_dir = sealed(tempfile.mkdtemp(), requests=301,
                          prov=legacy_prov(CONFIG_SHA=None))
        r = CH.harvest(rows_dir)
        self.assertEqual(r["CONFIG_PROVENANCE"], "NOT_RECORDED")
        self.assertEqual(r["CONFIG_PROVENANCE_SOURCE"],
                         "ABSENT_FROM_EXECUTED_ARTIFACT")
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL")

    def test_no_provenance_file_at_all_is_not_identified(self):
        r = CH.harvest(sealed(tempfile.mkdtemp(), requests=301))
        self.assertEqual(r["EXECUTION_PROVENANCE_SCHEMA_VERSION"], NI)
        self.assertEqual(r["EVIDENCE_RUN_VALIDITY"], "FAIL")


class TheOutputShape(unittest.TestCase):

    def test_the_block_ends_on_the_proposed_rate(self):
        text = CH.render(CH.harvest(sealed(tempfile.mkdtemp())))
        last = text.strip().splitlines()[-1]
        self.assertTrue(last.startswith("PROPOSED_SUBSTANTIVE_CAPTURE_RATE"))
        self.assertTrue(last.endswith("= NOT_IDENTIFIED"))

    def test_both_spans_are_printed_so_neither_can_be_mistaken(self):
        text = CH.render(CH.harvest(sealed(tempfile.mkdtemp())))
        self.assertIn("NOMINAL_PACED_EXPOSURE_S", text)
        self.assertIn("WALL_CLOCK_ELAPSED_S", text)
        self.assertIn("FORCED_SUSPENSION_S", text)

    def test_the_mechanism_is_reported_and_does_not_gate(self):
        r = CH.harvest(sealed(tempfile.mkdtemp(), requests=301))
        self.assertEqual(r["VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED"], NI)
        self.assertTrue(r["MECHANISM_DOES_NOT_GATE_THIS"])
        self.assertEqual(r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "YES")

    def test_it_contacts_nothing(self):
        self.assertTrue(CH.THIS_MODULE_CONTACTS_NOTHING)
        src = Path(CH.__file__).read_text()
        self.assertNotIn("httpx", src)
        self.assertNotIn("https://", src)


if __name__ == "__main__":
    unittest.main()


class IsolationThroughoutComesFromIntervalOverlap(unittest.TestCase):
    """An end-of-run snapshot is not an audit of the interval.

        confirmation 18:03:15 - 18:23:15
        collector    18:08:00 - 18:15:00

    Nothing is running at 18:23:15, and the experiment was contaminated for
    seven minutes.
    """

    W_START, W_END = "2026-09-16T10:00:00Z", None   # history reaches back

    def _dir(self, runs, more_pages=False, venue_windows=None):
        return sealed(tempfile.mkdtemp(), requests=301, prov=legacy_prov(),
                      history=runs, more_pages=more_pages,
                      venue_windows=venue_windows)

    def _run(self, start, end, name="run85-phase2-capture", status="completed"):
        return {"name": name, "id": 1, "status": status, "conclusion": "success",
                "created_at": self.W_START, "run_started_at": start,
                "updated_at": end}

    def test_a_collector_that_began_and_ended_inside_the_window_is_caught(self):
        """LEVEL B. The job interval overlaps, so this is a POSSIBLE direct
        overlap -- and it still fails the confirmation. What it is NOT is an
        observation of venue contact: the job could have spent those seven
        minutes in checkout and tests."""
        r = CH.harvest(self._dir([self._run("2026-09-16T18:08:00Z",
                                            "2026-09-16T18:15:00Z")]))
        self.assertEqual(r["DIRECT_CONFLICT_STARTED_DURING_RUN"], "POSSIBLE")
        self.assertEqual(r["DIRECT_OVERLAP_COUNT"], 1)
        self.assertEqual(r["POSSIBLE_DIRECT_WORKFLOW_OVERLAP"], "YES")
        self.assertEqual(r["CONFIRMED_DIRECT_REQUEST_OVERLAP"], NI)
        self.assertEqual(
            r["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"],
            "NOT_ESTABLISHED_POSSIBLE_OVERLAP")
        self.assertIn("POSSIBLE_DIRECT_WORKFLOW_OVERLAP", r["FAIL_REASON"])
        self.assertNotIn("DIRECT_PMUS_COLLECTOR_OVERLAP", r["FAIL_REASON"])
        self.assertEqual(r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")

    def test_level_b_is_labelled_a_conservative_proxy(self):
        r = CH.harvest(self._dir([self._run("2026-09-16T18:08:00Z",
                                            "2026-09-16T18:15:00Z")]))
        row = r["DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW"][0]
        self.assertEqual(row["EVIDENCE_LEVEL"],
                         "B_CONSERVATIVE_WORKFLOW_INTERVAL_PROXY")
        self.assertEqual(row["VENUE_REQUEST_TIMES"], NI)
        self.assertTrue(r["DO_NOT_SYNTHESIZE_GET_TIMESTAMPS"])

    def test_level_a_sealed_request_times_make_the_overlap_confirmed(self):
        """The other run recorded when it actually read the venue."""
        r = CH.harvest(self._dir(
            [self._run("2026-09-16T18:00:00Z", "2026-09-16T18:30:00Z")],
            venue_windows={"1": ["2026-09-16T18:08:00Z",
                                 "2026-09-16T18:15:00Z"]}))
        self.assertEqual(r["CONFIRMED_DIRECT_REQUEST_OVERLAP"], "YES")
        self.assertEqual(r["DIRECT_CONFLICT_STARTED_DURING_RUN"], "YES")
        self.assertEqual(
            r["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"],
            "NO_CONFIRMED")
        self.assertIn("DIRECT_PMUS_COLLECTOR_OVERLAP", r["FAIL_REASON"])
        self.assertEqual(r["DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW"][0][
            "EVIDENCE_LEVEL"], "A_SEALED_VENUE_REQUEST_TIMES")

    def test_level_a_request_times_can_also_clear_a_job_that_overlapped(self):
        """The job ran through our window; its reads did not. Sealed times are
        evidence in BOTH directions -- that is what makes them level A."""
        r = CH.harvest(self._dir(
            [self._run("2026-09-16T18:00:00Z", "2026-09-16T18:30:00Z")],
            venue_windows={"1": ["2026-09-16T18:00:05Z",
                                 "2026-09-16T18:02:00Z"]}))
        self.assertEqual(r["DIRECT_CONFLICT_STARTED_DURING_RUN"], "NO")
        self.assertEqual(r["CONFIRMED_DIRECT_REQUEST_OVERLAP"], "NO")
        self.assertEqual(
            r["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"], "YES")

    def test_the_two_failing_verdicts_are_never_the_same_string(self):
        """A confirmed overlap and an unresolved one are different facts."""
        seen = CH.harvest(self._dir(
            [self._run("2026-09-16T18:00:00Z", "2026-09-16T18:30:00Z")],
            venue_windows={"1": ["2026-09-16T18:08:00Z",
                                 "2026-09-16T18:15:00Z"]}))
        maybe = CH.harvest(self._dir([self._run("2026-09-16T18:08:00Z",
                                                "2026-09-16T18:15:00Z")]))
        K = "DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"
        self.assertNotEqual(seen[K], maybe[K])
        self.assertEqual({seen[K], maybe[K]},
                         {"NO_CONFIRMED", "NOT_ESTABLISHED_POSSIBLE_OVERLAP"})

    def test_the_raw_observations_are_not_discarded(self):
        r = CH.harvest(self._dir([self._run("2026-09-16T18:08:00Z",
                                            "2026-09-16T18:15:00Z")]))
        self.assertTrue(r["RAW_OBSERVATIONS_RETAINED"])
        self.assertEqual(r["REQUESTS"], 301)
        self.assertEqual(r["SUCCESSES"], 301)

    def test_a_run_wholly_before_the_window_is_not_an_overlap(self):
        r = CH.harvest(self._dir([self._run("2026-09-16T10:00:00Z",
                                            "2026-09-16T11:00:00Z")]))
        self.assertEqual(r["DIRECT_CONFLICT_STARTED_DURING_RUN"], "NO")
        self.assertEqual(
            r["DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN"], "YES")
        self.assertEqual(r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "YES")

    def test_a_queued_run_that_never_executed_is_not_load(self):
        q = self._run("2026-09-16T18:08:00Z", "2026-09-16T18:08:00Z",
                      status="queued")
        q["conclusion"] = None
        r = CH.harvest(self._dir([q]))
        self.assertEqual(r["DIRECT_CONFLICT_STARTED_DURING_RUN"], "NO")

    def test_thin_history_cannot_conclude_a_clean_run(self):
        """Coverage gates the NEGATIVE only."""
        late = dict(self._run("2026-09-16T19:00:00Z", "2026-09-16T19:10:00Z"),
                    created_at="2026-09-16T19:00:00Z")
        r = CH.harvest(self._dir([late], more_pages=True))
        self.assertEqual(r["DIRECT_CONFLICT_STARTED_DURING_RUN"], NI)
        self.assertEqual(r["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "NO")
        self.assertIn("ISOLATION_THROUGHOUT_RUN_NOT_IDENTIFIED",
                      r["FAIL_REASON"])
        self.assertEqual(r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")

    def test_coverage_is_reported_per_workflow(self):
        """One collector's history reaching back says nothing about another's.
        Every known direct collector gets its own row."""
        r = CH.harvest(self._dir([], more_pages=True))
        rows = r["DIRECT_RUN_HISTORY_COVERAGE"]
        names = [c["WORKFLOW_NAME"] for c in rows]
        self.assertEqual(sorted(names), sorted(set(names)))
        self.assertGreater(len(rows), 1)
        for c in rows:
            for k in ("WORKFLOW_NAME", "HISTORY_PAGES_FETCHED",
                      "EARLIEST_RUN_TIME_FETCHED", "LATEST_RUN_TIME_FETCHED",
                      "MORE_PAGES_AVAILABLE", "COVERS_EVIDENCE_WINDOW"):
                self.assertIn(k, c)
            self.assertEqual(c["COVERS_EVIDENCE_WINDOW"], "NO")
        self.assertEqual(r["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "NO")

    def test_one_uncovered_workflow_fails_the_whole_coverage_claim(self):
        """COMPLETE means every known collector, not most of them."""
        deep = dict(self._run("2026-09-16T09:00:00Z", "2026-09-16T09:30:00Z"),
                    created_at="2026-09-16T09:00:00Z")
        r = CH.harvest(self._dir([deep], more_pages=True))
        rows = {c["WORKFLOW_NAME"]: c for c in r["DIRECT_RUN_HISTORY_COVERAGE"]}
        self.assertEqual(rows["run85-phase2-capture"][
            "COVERS_EVIDENCE_WINDOW"], "YES")
        self.assertEqual(r["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "NO")

    def test_the_lookback_is_a_job_timeout_not_the_window_start(self):
        """A five-hour capture created long before the window still overlaps
        it, so history must reach back a full job timeout -- paging only to the
        window start would miss exactly that collision."""
        r = CH.harvest(self._dir([], more_pages=True))
        ws = datetime.fromisoformat(r["EVIDENCE_WINDOW"][0])
        anchor = datetime.fromisoformat(r["COVERAGE_ANCHOR"])
        self.assertGreater(r["COVERAGE_LOOKBACK_S"], 3600)
        self.assertEqual((ws - anchor).total_seconds(),
                         r["COVERAGE_LOOKBACK_S"])

    def test_a_confirmed_overlap_outranks_thin_coverage(self):
        """Seeing it beats not being able to rule it out."""
        hit = dict(self._run("2026-09-16T18:00:00Z", "2026-09-16T18:30:00Z"),
                   created_at="2026-09-16T18:07:00Z")
        r = CH.harvest(self._dir([hit], more_pages=True,
                                 venue_windows={"1": ["2026-09-16T18:08:00Z",
                                                      "2026-09-16T18:15:00Z"]}))
        self.assertEqual(r["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "NO")
        self.assertEqual(r["DIRECT_CONFLICT_STARTED_DURING_RUN"], "YES")

    def test_a_possible_overlap_also_outranks_thin_coverage(self):
        hit = dict(self._run("2026-09-16T18:08:00Z", "2026-09-16T18:15:00Z"),
                   created_at="2026-09-16T18:07:00Z")
        r = CH.harvest(self._dir([hit], more_pages=True))
        self.assertEqual(r["DIRECT_RUN_HISTORY_COVERAGE_COMPLETE"], "NO")
        self.assertEqual(r["DIRECT_CONFLICT_STARTED_DURING_RUN"], "POSSIBLE")

    def test_no_history_at_all_cannot_establish_isolation(self):
        d = sealed(tempfile.mkdtemp(), requests=301, prov=legacy_prov(),
                   history=None)
        r = CH.harvest(d)
        self.assertEqual(r["DIRECT_CONFLICT_STARTED_DURING_RUN"], NI)
        self.assertEqual(r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")

    def test_the_window_starts_at_the_first_get_not_at_dispatch(self):
        r = CH.harvest(self._dir([]))
        self.assertEqual(r["FIRST_VENUE_GET_TIME"], "2026-09-16T18:03:15+00:00")
        self.assertTrue(r["EVIDENCE_WINDOW_EXCLUDES_DISPATCH_TIME"])

    def test_the_snapshot_is_labelled_visibility_not_proof(self):
        r = CH.harvest(self._dir([]))
        self.assertTrue(r["END_SNAPSHOT_IS_VISIBILITY_NOT_PROOF"])
        self.assertEqual(r["EVIDENCE_VERDICT_FROM"],
                         "RUN_HISTORY_OVERLAP_NOT_END_OF_RUN_SNAPSHOT")
