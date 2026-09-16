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
           interval=4.0, latency=0.03, order=None, backoff_s=0.0):
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
    return str(out)


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
