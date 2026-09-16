#!/usr/bin/env python3
"""The single-rate operational confirmation: a YES that is actually reachable,
and every way it can still come back NO.

The point of this module is the separation. A test that only checked "clean run
-> YES" would pass under the old deadlocked design too, so the tests that
matter here are the ones that pin what does NOT gate.
"""
import ast
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

import rate_confirm as RC

NI = RC.NOT_IDENTIFIED


class FakeResp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}


class FakeHttp:
    """Replays a status sequence, then 200s. No socket is opened."""

    def __init__(self, statuses, headers=None):
        self.statuses = list(statuses)
        self.headers = headers or {}
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        s = self.statuses.pop(0) if self.statuses else 200
        return FakeResp(s, dict(self.headers) if s == 429 else {})


def no_sleep(saved):
    saved.append(RC.RP.time.sleep)
    RC.RP.time.sleep = lambda s: None


def clean(rate="0.25", **over):
    """A result row that clears every criterion, before overrides."""
    req = RC.min_requests(float(rate))
    row = {
        "RATE_RPS": rate,
        "RATES_IN_THIS_RUN": 1,
        "NO_PRIOR_HIGHER_RATE_IN_THIS_WORKFLOW": True,
        "REQUESTS": req,
        "DURATION_S": RC.min_duration_s(float(rate)) + 1.0,
        "SUCCESSES": req,
        "HTTP_429": 0,
        "OTHER_FAILURES": 0,
        "RETRY_AFTER_OBSERVED": [],
        "LAST_429_AT_S": None,
        "GLOBAL_BACKOFF_EVENTS": 0,
        "SUCCESSES_AFTER_LAST_429": 0,
        "POLL_ORDER_STARVATION": "NO",
        "POLL_ORDER_FAIRNESS": "PASS",
        "MIN_SUCCESS_SHARE_BY_MARKET": D("1.0"),
        "MAX_SUCCESS_SHARE_BY_MARKET": D("1.0"),
    }
    row.update(over)
    return row


class TheTwoQuestionsAreSeparate(unittest.TestCase):
    """The correction itself. Validation must not wait on identification."""

    def test_a_clean_run_validates_while_the_mechanism_stays_unidentified(self):
        v = RC.validate(clean())
        self.assertEqual(v["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "YES")
        self.assertEqual(v["VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED"], NI)
        self.assertEqual(v["RATE_LIMIT_WINDOW_SEMANTICS"], NI)

    def test_no_check_consults_the_venue_mechanism(self):
        v = RC.validate(clean())
        for name in v["CHECKS"]:
            self.assertNotIn("MECHANISM", name)
            self.assertNotIn("WINDOW", name)
            self.assertNotIn("INDEPENDEN", name)

    def test_the_separation_is_asserted_in_the_output(self):
        v = RC.validate(clean())
        self.assertTrue(
            v["OPERATIONAL_VALIDATION_DOES_NOT_REQUIRE_MECHANISM_IDENTIFICATION"])

    def test_this_is_named_as_operational_not_identification(self):
        v = RC.validate(clean())
        self.assertEqual(v["THIS_IS"], "SINGLE_RATE_OPERATIONAL_CONFIRMATION")
        self.assertEqual(v["THIS_IS_NOT"], "VENUE_RATE_LIMIT_IDENTIFICATION")


class TheLabelsItRefuses(unittest.TestCase):

    def test_a_validated_rate_is_not_called_the_venue_maximum(self):
        v = RC.validate(clean())
        for bad in ("VENUE_MAXIMUM_SAFE_RATE", "VENUE_RATE_LIMIT_KNOWN",
                    "RATE_LIMIT_WINDOW_IDENTIFIED", "MAXIMUM_SUSTAINABLE_RATE"):
            self.assertNotIn(bad, v)
            self.assertIn(bad, v["REFUSED_LABELS"])

    def test_faster_rates_are_reported_as_untested(self):
        self.assertEqual(RC.validate(clean())["FASTER_RATES"],
                         "UNTESTED_IN_THIS_RUN")

    def test_the_scope_of_the_claim_is_stated(self):
        v = RC.validate(clean())
        self.assertIn("0.25", v["WHAT_THIS_VALIDATES"])
        self.assertIn("venue's limiter", v["WHAT_THIS_DOES_NOT_VALIDATE"])


class TheEvidenceFloorIsBothFloors(unittest.TestCase):

    def test_a_slow_rate_is_bound_by_the_request_count(self):
        """N requests span N-1 intervals, so OCCUPYING 1,200 s at 0.25 rps
        takes 301 requests, not 300. That off-by-one cost a dispatched run."""
        self.assertEqual(RC.min_duration_s(0.25), 1200.0)
        self.assertEqual(RC.min_requests(0.25), 301)
        self.assertEqual(RC.planned_paced_exposure_s(300, 0.25), 1196.0)
        self.assertEqual(RC.planned_paced_exposure_s(301, 0.25), 1200.0)

    def test_the_required_count_is_ceil_duration_times_rate_plus_one(self):
        self.assertEqual(RC.requests_for_exposure(0.25, 1200.0), 301)
        self.assertEqual(RC.requests_for_exposure(2.0, 900.0), 1801)
        self.assertEqual(RC.requests_for_exposure(0.125, 2400.0), 301)

    def test_a_very_slow_rate_needs_a_very_long_run(self):
        self.assertEqual(RC.min_duration_s(0.125), 2400.0)

    def test_a_fast_rate_is_bound_by_the_clock_not_the_count(self):
        """At 2 rps, 300 requests take 150 s. The 900 s floor binds, and the
        request floor rises with it -- a three-minute burst buys nothing."""
        self.assertEqual(RC.min_duration_s(2.0), 900.0)
        self.assertEqual(RC.min_requests(2.0), 1801)

    def test_a_zero_or_negative_rate_is_refused(self):
        with self.assertRaises(ValueError):
            RC.min_duration_s(0)

    def test_too_few_requests_fails(self):
        v = RC.validate(clean(REQUESTS=100))
        self.assertEqual(v["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")
        self.assertIn("REQUESTS_MEET_FLOOR", v["FAILED_CHECKS"])

    def test_too_short_a_run_fails(self):
        v = RC.validate(clean(NOMINAL_PACED_EXPOSURE_S=300.0))
        self.assertIn("PACED_EXPOSURE_MEETS_FLOOR", v["FAILED_CHECKS"])

    def test_wall_clock_may_not_stand_in_for_paced_exposure(self):
        """A 60 s backoff pushes wall clock over the floor. The verdict must
        not move: a refusal may never help a rate pass."""
        v = RC.validate(clean(NOMINAL_PACED_EXPOSURE_S=1196.0,
                              WALL_CLOCK_ELAPSED_S=1256.0,
                              DURATION_S=1256.0))
        self.assertIn("PACED_EXPOSURE_MEETS_FLOOR", v["FAILED_CHECKS"])
        self.assertEqual(v["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")


class TheDispatchCheckRefusesAnImpossibleRun(unittest.TestCase):

    def test_a_plan_that_clears_its_floor_may_dispatch(self):
        g = RC.dispatch_check(0.25, 301, timeout_s=5400)
        self.assertEqual(g["CONFIRMATION_MAY_DISPATCH"], "YES")
        self.assertEqual(g["PLANNED_PACED_EXPOSURE_S"], 1200.0)

    def test_the_run_that_was_cancelled_would_now_be_refused(self):
        """300 requests at 0.25 rps. Dispatched on the old arithmetic,
        cancelled before it started. The gate now says no at the door."""
        g = RC.dispatch_check(0.25, 300, timeout_s=5100)
        self.assertEqual(g["CONFIRMATION_MAY_DISPATCH"], "NO")
        self.assertEqual(g["PLANNED_PACED_EXPOSURE_S"], 1196.0)
        self.assertIn("PLANNED_REQUESTS_MEET_FLOOR", g["FAILED_CHECKS"])
        self.assertIn("PLANNED_PACED_EXPOSURE_MEETS_FLOOR", g["FAILED_CHECKS"])

    def test_a_plan_with_too_few_requests_is_refused_at_the_door(self):
        g = RC.dispatch_check(0.25, 120, timeout_s=5400)
        self.assertEqual(g["CONFIRMATION_MAY_DISPATCH"], "NO")
        self.assertIn("PLANNED_REQUESTS_MEET_FLOOR", g["FAILED_CHECKS"])

    def test_a_sha_mismatch_refuses_the_dispatch(self):
        ok = RC.dispatch_check(0.25, 301, 5400, dispatch_sha="a" * 40,
                               executed_sha="a" * 40)
        bad = RC.dispatch_check(0.25, 301, 5400, dispatch_sha="a" * 40,
                                executed_sha="b" * 40)
        self.assertEqual(ok["CONFIRMATION_MAY_DISPATCH"], "YES")
        self.assertEqual(bad["CONFIRMATION_MAY_DISPATCH"], "NO")
        self.assertIn("DISPATCH_SHA_EQUALS_EXECUTED_SHA", bad["FAILED_CHECKS"])

    def test_a_job_timeout_shorter_than_the_run_is_refused(self):
        """1,200 s of paced requests inside a 600 s job would be cut off and
        then read as a result."""
        g = RC.dispatch_check(0.25, 301, timeout_s=600)
        self.assertEqual(g["CONFIRMATION_MAY_DISPATCH"], "NO")
        self.assertIn("JOB_TIMEOUT_COVERS_THE_RUN", g["FAILED_CHECKS"])

    def test_the_required_floors_are_printed_with_the_refusal(self):
        g = RC.dispatch_check(0.125, 100)
        self.assertEqual(g["MIN_PACED_EXPOSURE_S_REQUIRED"], 2400.0)
        self.assertEqual(g["MIN_REQUESTS_REQUIRED"], 301)


class TheThresholdIsFrozenBeforeTheRun(unittest.TestCase):

    def test_the_target_is_zero(self):
        self.assertEqual(RC.TARGET_429_RATE, D("0"))
        self.assertTrue(RC.THRESHOLD_FROZEN_BEFORE_THE_RUN)

    def test_a_perfectly_clean_run_validates(self):
        self.assertEqual(
            RC.validate(clean())["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"],
            "YES")

    def test_one_isolated_honoured_refusal_is_inside_the_allowance(self):
        v = RC.validate(clean(HTTP_429=1, RETRY_AFTER_OBSERVED=["10"],
                              GLOBAL_BACKOFF_EVENTS=1,
                              SUCCESSES_AFTER_LAST_429=40,
                              LAST_429_AT_S=100.0))
        self.assertEqual(v["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "YES")

    def test_two_refusals_are_a_pattern_and_fail(self):
        v = RC.validate(clean(HTTP_429=2, RETRY_AFTER_OBSERVED=["10"],
                              GLOBAL_BACKOFF_EVENTS=2,
                              SUCCESSES_AFTER_LAST_429=40,
                              LAST_429_AT_S=100.0))
        self.assertIn("REFUSALS_WITHIN_FROZEN_ALLOWANCE", v["FAILED_CHECKS"])
        self.assertIn("pattern", v["ALLOWANCE_FAILURE_REASON"])

    def test_a_refusal_without_a_retry_after_fails(self):
        v = RC.validate(clean(HTTP_429=1, RETRY_AFTER_OBSERVED=[],
                              GLOBAL_BACKOFF_EVENTS=1,
                              SUCCESSES_AFTER_LAST_429=40,
                              LAST_429_AT_S=100.0))
        self.assertIn("REFUSALS_WITHIN_FROZEN_ALLOWANCE", v["FAILED_CHECKS"])

    def test_a_refusal_in_the_final_tenth_fails(self):
        """A refusal at the end is the beginning of a trend the run stopped
        before showing."""
        v = RC.validate(clean(HTTP_429=1, RETRY_AFTER_OBSERVED=["10"],
                              GLOBAL_BACKOFF_EVENTS=1,
                              SUCCESSES_AFTER_LAST_429=2,
                              LAST_429_AT_S=1190.0))
        self.assertIn("REFUSALS_WITHIN_FROZEN_ALLOWANCE", v["FAILED_CHECKS"])
        self.assertIn("final tenth", v["ALLOWANCE_FAILURE_REASON"])

    def test_the_allowance_conditions_ship_with_the_report(self):
        v = RC.validate(clean())
        self.assertIn("AT_MOST_ONE_429_IN_THE_WHOLE_RUN",
                      v["ALLOWANCE_CONDITIONS"])


class ARateThatWorksByStarvingMarketsIsNotValidated(unittest.TestCase):

    def test_starvation_fails_the_run(self):
        v = RC.validate(clean(POLL_ORDER_STARVATION="YES",
                              MIN_SUCCESS_SHARE_BY_MARKET=D("0.3")))
        self.assertEqual(v["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")
        self.assertIn("NO_POLL_ORDER_STARVATION", v["FAILED_CHECKS"])

    def test_the_share_spread_is_computed_per_market(self):
        s = RC._starvation({"a": {"ATTEMPTS": 10, "SUCCESSES": 10},
                            "b": {"ATTEMPTS": 10, "SUCCESSES": 3}}, 20)
        self.assertEqual(s["MAX_SUCCESS_SHARE_BY_MARKET"], D(1))
        self.assertEqual(s["MIN_SUCCESS_SHARE_BY_MARKET"], D("0.3"))
        self.assertEqual(s["SUCCESS_SHARE_SPREAD"], D("0.7"))
        self.assertEqual(s["POLL_ORDER_STARVATION"], "YES")
        self.assertEqual(s["STARVED_MARKETS"], ["b"])

    def test_an_even_read_is_not_starvation(self):
        s = RC._starvation({"a": {"ATTEMPTS": 10, "SUCCESSES": 10},
                            "b": {"ATTEMPTS": 10, "SUCCESSES": 10}}, 20)
        self.assertEqual(s["POLL_ORDER_STARVATION"], "NO")

    def test_no_markets_at_all_is_not_identified_rather_than_clean(self):
        s = RC._starvation({})
        self.assertEqual(s["POLL_ORDER_STARVATION"], NI)

    def test_the_floor_is_derived_from_the_frozen_allowance(self):
        """Every failure the run is permitted, landing on one market. At 300
        requests over 6 markets: 1 + 3 = 4 misses, 50 attempts -> 46/50."""
        self.assertEqual(RC.max_allowed_misses(300), 4)
        self.assertEqual(RC.starvation_floor(50, 300), D("0.92"))

    def test_one_unlucky_read_is_not_called_starvation(self):
        s = RC._starvation({"a": {"ATTEMPTS": 50, "SUCCESSES": 50},
                            "b": {"ATTEMPTS": 50, "SUCCESSES": 49}}, 300)
        self.assertEqual(s["POLL_ORDER_STARVATION"], "NO")


class NoMarketLeadsEveryCycle(unittest.TestCase):
    """Equal attempts is not enough. The first slot of a cycle is the one that
    spends whatever budget the venue refilled, and a fixed order hands it to the
    same slug every time -- which is how run 35120338223 chose its entire
    surviving sample."""

    SLUGS = ["a", "b", "c", "d", "e", "f"]

    def test_the_start_index_advances_each_cycle(self):
        o = RC.rotation_order(self.SLUGS, 12)
        self.assertEqual(o[:6], ["a", "b", "c", "d", "e", "f"])
        self.assertEqual(o[6:], ["b", "c", "d", "e", "f", "a"])

    def test_attempts_stay_equal_across_markets(self):
        o = RC.rotation_order(self.SLUGS, 300)
        counts = {s: o.count(s) for s in self.SLUGS}
        self.assertEqual(set(counts.values()), {50})

    def test_the_lead_slot_is_shared(self):
        f = RC.rotation_fairness(RC.rotation_order(self.SLUGS, 300), self.SLUGS)
        self.assertEqual(f["POLL_ORDER_FAIRNESS"], "PASS")
        self.assertLessEqual(f["CYCLE_LEAD_IMBALANCE"], 1)

    def test_a_fixed_order_would_fail_the_fairness_test(self):
        """The thing we are not doing, shown failing."""
        fixed = [self.SLUGS[i % 6] for i in range(300)]
        f = RC.rotation_fairness(fixed, self.SLUGS)
        self.assertEqual(f["POLL_ORDER_FAIRNESS"], "FAIL")
        self.assertEqual(f["CYCLE_LEAD_COUNTS"]["a"], 50)
        self.assertEqual(f["CYCLE_LEAD_COUNTS"]["f"], 0)

    def test_an_unfair_order_fails_validation(self):
        v = RC.validate(clean(POLL_ORDER_FAIRNESS="FAIL"))
        self.assertEqual(v["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")
        self.assertIn("POLL_ORDER_IS_FAIR", v["FAILED_CHECKS"])


class TheRefusalMustBeRecoveredFrom(unittest.TestCase):
    """The allowance conditions the order added: reads resume, nothing
    cascades, no market pays for it."""

    def setUp(self):
        self._saved = []
        no_sleep(self._saved)

    def tearDown(self):
        RC.RP.time.sleep = self._saved[0]

    def test_a_refusal_with_no_successful_reads_after_it_fails(self):
        v = RC.validate(clean(HTTP_429=1, RETRY_AFTER_OBSERVED=["10"],
                              GLOBAL_BACKOFF_EVENTS=1,
                              SUCCESSES_AFTER_LAST_429=0,
                              LAST_429_AT_S=100.0))
        self.assertIn("REFUSALS_WITHIN_FROZEN_ALLOWANCE", v["FAILED_CHECKS"])
        self.assertIn("did not resume", v["ALLOWANCE_FAILURE_REASON"])

    def test_a_refusal_not_answered_by_a_global_backoff_fails(self):
        v = RC.validate(clean(HTTP_429=1, RETRY_AFTER_OBSERVED=["10"],
                              GLOBAL_BACKOFF_EVENTS=0,
                              SUCCESSES_AFTER_LAST_429=40,
                              LAST_429_AT_S=100.0))
        self.assertIn("global backoff", v["ALLOWANCE_FAILURE_REASON"])

    def test_a_refusal_that_starved_a_market_fails(self):
        v = RC.validate(clean(HTTP_429=1, RETRY_AFTER_OBSERVED=["10"],
                              GLOBAL_BACKOFF_EVENTS=1,
                              SUCCESSES_AFTER_LAST_429=40,
                              LAST_429_AT_S=100.0,
                              POLL_ORDER_STARVATION="YES"))
        self.assertEqual(v["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")

    def test_the_preferred_outcome_is_zero(self):
        self.assertEqual(RC.validate(clean())["PREFERRED_OUTCOME"],
                         "HTTP_429 = 0")

    def test_the_resume_counter_resets_on_each_refusal(self):
        """A run whose last request is a 429 has zero successes after it."""
        rep = RC.confirm(tempfile.mkdtemp(), ["a", "b"],
                         FakeHttp([200, 200, 200, 429], {"Retry-After": "10"}),
                         "1.0", requests=4)
        self.assertEqual(rep["SUCCESSES_AFTER_LAST_429"], 0)


class ItIsOneRateInOneRun(unittest.TestCase):

    def test_more_than_one_rate_in_the_run_fails(self):
        v = RC.validate(clean(RATES_IN_THIS_RUN=2))
        self.assertIn("SINGLE_RATE_RUN", v["FAILED_CHECKS"])

    def test_a_prior_higher_rate_in_the_workflow_fails(self):
        v = RC.validate(clean(NO_PRIOR_HIGHER_RATE_IN_THIS_WORKFLOW=False))
        self.assertIn("NO_PRIOR_HIGHER_RATE_IN_THIS_WORKFLOW",
                      v["FAILED_CHECKS"])


class TheRunItself(unittest.TestCase):

    def setUp(self):
        self._saved = []
        no_sleep(self._saved)

    def tearDown(self):
        RC.RP.time.sleep = self._saved[0]

    def test_every_criterion_is_reported(self):
        out = tempfile.mkdtemp()
        rep = RC.confirm(out, ["a", "b"], FakeHttp([200] * 8), "1.0",
                         requests=8)
        for k in RC.OPERATIONAL_CRITERIA:
            self.assertIn(k, rep, k)

    def test_the_rotation_is_even(self):
        rep = RC.confirm(tempfile.mkdtemp(), ["a", "b", "c"],
                         FakeHttp([200] * 9), "1.0", requests=9)
        self.assertEqual(set(rep["PER_MARKET_ATTEMPTS"].values()), {3})
        self.assertEqual(rep["POLL_ORDER_FAIRNESS"], "PASS")

    def test_the_request_interval_is_the_reciprocal_of_the_rate(self):
        rep = RC.confirm(tempfile.mkdtemp(), ["a"], FakeHttp([200] * 4),
                         "0.25", requests=4)
        self.assertEqual(rep["REQUEST_INTERVAL_S"], 4.0)

    def test_other_failures_are_reported_per_market(self):
        rep = RC.confirm(tempfile.mkdtemp(), ["a", "b"],
                         FakeHttp([200, 500, 200, 200]), "1.0", requests=4)
        self.assertEqual(rep["PER_MARKET_OTHER_FAILURES"]["b"], 1)
        self.assertEqual(rep["PER_MARKET_OTHER_FAILURES"]["a"], 0)

    def test_a_short_run_is_reported_and_refused(self):
        """The run still produces its numbers; it just does not validate."""
        rep = RC.confirm(tempfile.mkdtemp(), ["a"], FakeHttp([200] * 8), "1.0",
                         requests=8)
        self.assertEqual(rep["SUCCESSES"], 8)
        self.assertEqual(rep["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")
        self.assertIn("REQUESTS_MEET_FLOOR", rep["FAILED_CHECKS"])

    def test_a_429_backs_off_globally_and_is_counted(self):
        http = FakeHttp([200, 429, 200, 200], headers={"Retry-After": "10"})
        rep = RC.confirm(tempfile.mkdtemp(), ["a", "b"], http, "1.0",
                         requests=4)
        self.assertEqual(rep["HTTP_429"], 1)
        self.assertEqual(rep["GLOBAL_BACKOFF_EVENTS"], 1)
        self.assertEqual(rep["RETRY_AFTER_OBSERVED"], ["10"])

    def test_the_rows_are_written_for_audit(self):
        out = tempfile.mkdtemp()
        RC.confirm(out, ["a"], FakeHttp([200] * 4), "1.0", requests=4)
        rows = (Path(out) / "confirm_rows.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 4)

    def test_the_report_is_sealed_to_disk(self):
        out = tempfile.mkdtemp()
        RC.confirm(out, ["a"], FakeHttp([200] * 4), "1.0", requests=4)
        self.assertTrue((Path(out) / "confirm_report.json").exists())

    def test_the_rendered_block_ends_on_the_decision(self):
        rep = RC.confirm(tempfile.mkdtemp(), ["a"], FakeHttp([200] * 4), "1.0",
                         requests=4)
        text = RC.render(rep)
        self.assertTrue(text.strip().endswith(
            "COLLECTOR_RATE_OPERATIONALLY_VALIDATED = NO"))
        self.assertIn("VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED", text)


class TheCapabilityBoundary(unittest.TestCase):
    """Proved by AST, not by grep. This class of error has recurred."""

    def setUp(self):
        self.tree = ast.parse(Path(RC.__file__).read_text())

    def test_no_order_verb_is_called_anywhere(self):
        banned = {"post", "put", "patch", "delete", "place_order",
                  "create_order", "submit"}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = (f.attr if isinstance(f, ast.Attribute)
                        else getattr(f, "id", None))
                self.assertNotIn(name, banned, name)

    def test_the_only_http_verb_is_get(self):
        verbs = {n.func.attr for n in ast.walk(self.tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr in ("get", "post", "put", "delete", "patch")}
        self.assertLessEqual(verbs - {"get"}, set())

    def test_the_flags_are_set(self):
        self.assertTrue(RC.READ_ONLY)
        self.assertFalse(RC.ORDER_PATH_EXISTS)
        self.assertEqual(RC.CREDENTIAL_PATH, "NONE")
        self.assertFalse(RC.mirror_live)

    def test_the_host_is_imported_never_retyped(self):
        self.assertEqual(RC.HOST, RC.RP.HOST)
        src = Path(RC.__file__).read_text()
        self.assertNotIn("https://gateway.", src)


if __name__ == "__main__":
    unittest.main()
