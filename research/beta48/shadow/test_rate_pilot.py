#!/usr/bin/env python3
"""The rate-limit pilot. GET only, one global pacer, headers never interpreted."""
import ast
import json
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

import rate_pilot as RP

SRC = Path(__file__).resolve().parent / "rate_pilot.py"
TREE = ast.parse(SRC.read_text())

NI = RP.NOT_IDENTIFIED


class FakeResponse:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}


class FakeHttp:
    """Answers a scripted sequence of statuses. Records every call."""

    def __init__(self, script, headers=None):
        self.script = list(script)
        self.headers = headers or {}
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        s = self.script.pop(0) if self.script else 200
        return FakeResponse(s, self.headers)


def no_sleep(monkey):
    """The pilot sleeps between requests; tests must not."""
    monkey.append(RP.time.sleep)
    RP.time.sleep = lambda *_a, **_k: None


class TheBoundaryIsProvedNotPromised(unittest.TestCase):

    def test_only_get_is_called_on_the_client(self):
        methods = set()
        for n in ast.walk(TREE):
            if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load):
                methods.add(n.attr)
        for verb in ("post", "put", "patch", "delete", "request", "send"):
            self.assertNotIn(verb, methods, verb)

    def test_no_credential_word_appears_anywhere(self):
        text = SRC.read_text().lower()
        for word in ("api_key", "apikey", "secret", "private_key", "signer",
                     "wallet", "authorization"):
            self.assertNotIn(word, text, word)

    def test_no_hostname_is_hand_written(self):
        for n in ast.walk(TREE):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                self.assertNotIn("://", n.value)

    def test_the_host_is_the_collector_s_host(self):
        import collect as C
        self.assertEqual(RP.HOST, C.HOST)

    def test_the_module_declares_what_it_is(self):
        self.assertTrue(RP.READ_ONLY)
        self.assertFalse(RP.ORDER_PATH_EXISTS)
        self.assertEqual(RP.CREDENTIAL_PATH, "NONE")
        self.assertFalse(RP.mirror_live)


class OneGlobalSchedulerNoBursts(unittest.TestCase):

    def setUp(self):
        self._saved = []
        no_sleep(self._saved)

    def tearDown(self):
        RP.time.sleep = self._saved[0]

    def test_a_429_pauses_every_market_not_just_its_own(self):
        p = RP.GlobalPacer("2.0")
        before = p.paused_until
        p.back_off(None)
        self.assertGreater(p.paused_until, before)
        self.assertEqual(p.pauses, 1)

    def test_retry_after_is_honoured_when_present(self):
        p = RP.GlobalPacer("2.0")
        out = p.back_off("7")
        self.assertEqual(out["BACKOFF_S"], 7.0)
        self.assertEqual(out["BACKOFF_SOURCE"], "RETRY_AFTER_HEADER")

    def test_an_absent_retry_after_falls_back_to_our_own_schedule(self):
        out = RP.GlobalPacer("2.0").back_off(None)
        self.assertEqual(out["BACKOFF_SOURCE"], "OUR_OWN_SCHEDULE")
        self.assertEqual(out["BACKOFF_S"], RP.DEFAULT_BACKOFF_S)

    def test_an_unparseable_retry_after_does_not_crash_or_get_believed(self):
        out = RP.GlobalPacer("2.0").back_off("soon")
        self.assertEqual(out["BACKOFF_SOURCE"], "OUR_OWN_SCHEDULE")

    def test_a_huge_retry_after_is_capped(self):
        out = RP.GlobalPacer("2.0").back_off("99999")
        self.assertEqual(out["BACKOFF_S"], RP.MAX_BACKOFF_S)

    def test_there_is_no_per_market_retry_loop(self):
        """The starvation mechanism, absent by construction: run_rate visits
        each slug in rotation and never re-queues a refused one."""
        http = FakeHttp([429, 200, 200, 200])
        r = RP.run_rate(http, ["a", "b", "c", "d"], "1.0", requests=4)
        self.assertEqual(len(http.calls), 4)
        self.assertEqual(len({u for u in http.calls}), 4)
        self.assertEqual(r["HTTP_429_COUNT"], 1)

    def test_the_rotation_spreads_requests_across_markets(self):
        http = FakeHttp([200] * 6)
        RP.run_rate(http, ["a", "b", "c"], "1.0", requests=6)
        self.assertEqual(len({u for u in http.calls}), 3)


class HeadersAreReportedNeverInterpreted(unittest.TestCase):

    def setUp(self):
        self._saved = []
        no_sleep(self._saved)

    def tearDown(self):
        RP.time.sleep = self._saved[0]

    def test_a_known_looking_header_is_captured_verbatim(self):
        h = RP.observed_headers({"X-RateLimit-Remaining": "17",
                                 "Content-Type": "application/json"})
        self.assertEqual(h, {"x-ratelimit-remaining": "17"})

    def test_no_semantics_are_attached_to_it(self):
        self.assertEqual(RP.RATE_LIMIT_SEMANTICS, NI)
        self.assertTrue(RP.HEADERS_ARE_REPORTED_NOT_INTERPRETED)

    def test_the_report_carries_the_headers_and_the_refusal_together(self):
        http = FakeHttp([200] * 4, headers={"X-RateLimit-Limit": "60"})
        rep = RP.pilot(tempfile.mkdtemp(), ["a"], http, ladder=("1.0",),
                       requests=4)
        self.assertEqual(rep["RATE_LIMIT_HEADERS_SEEN"],
                         {"x-ratelimit-limit": "60"})
        self.assertTrue(rep["RATE_LIMIT_HEADERS_FOUND"])
        self.assertEqual(rep["RATE_LIMIT_SEMANTICS"], NI)

    def test_an_absent_header_set_is_reported_as_absent(self):
        http = FakeHttp([200] * 4)
        rep = RP.pilot(tempfile.mkdtemp(), ["a"], http, ladder=("1.0",),
                       requests=4)
        self.assertFalse(rep["RATE_LIMIT_HEADERS_FOUND"])


class TheRateIsNotChosenForBeingFast(unittest.TestCase):

    def test_the_fastest_passing_rate_is_taken_and_no_faster(self):
        rows = [{"RATE_RPS": "0.25", "PASSES": True},
                {"RATE_RPS": "0.5", "PASSES": True},
                {"RATE_RPS": "1.0", "PASSES": False},
                {"RATE_RPS": "2.0", "PASSES": True}]
        s = RP.select_rate(rows)
        self.assertEqual(s["SUSTAINABLE_RATE_SELECTED"], "0.5")
        self.assertIn("first rate whose 429 share exceeded",
                      s["SELECTION_REASON"])

    def test_a_pass_after_a_failure_is_not_taken(self):
        """2.0 passing after 1.0 failed is noise, and taking it would be
        choosing the rate that flattered the result."""
        rows = [{"RATE_RPS": "1.0", "PASSES": False},
                {"RATE_RPS": "2.0", "PASSES": True}]
        self.assertEqual(RP.select_rate(rows)["SUSTAINABLE_RATE_SELECTED"], NI)

    def test_no_passing_rate_blocks_the_capture(self):
        s = RP.select_rate([{"RATE_RPS": "0.25", "PASSES": False}])
        self.assertEqual(s["SUSTAINABLE_RATE_SELECTED"], NI)
        self.assertFalse(s["CAPTURE_MAY_PROCEED"])
        self.assertIn("slower rung", s["SELECTION_REASON"])

    def test_every_rate_passing_says_the_next_rung_is_untested(self):
        s = RP.select_rate([{"RATE_RPS": "0.25", "PASSES": True},
                            {"RATE_RPS": "0.5", "PASSES": True}])
        self.assertEqual(s["SUSTAINABLE_RATE_SELECTED"], "0.5")
        self.assertIn("UNTESTED", s["SELECTION_REASON"])

    def test_the_flag_says_it_outright(self):
        s = RP.select_rate([{"RATE_RPS": "0.5", "PASSES": True}])
        self.assertTrue(s["NOT_CHOSEN_FOR_BEING_FASTEST"])


class TheLadderAndTheReport(unittest.TestCase):

    def setUp(self):
        self._saved = []
        no_sleep(self._saved)

    def tearDown(self):
        RP.time.sleep = self._saved[0]

    def test_a_clean_rate_passes(self):
        r = RP.run_rate(FakeHttp([200] * 10), ["a"], "1.0", requests=10)
        self.assertEqual(r["SUCCESSFUL_REQUESTS"], 10)
        self.assertEqual(r["HTTP_429_SHARE"], D(0))
        self.assertTrue(r["PASSES"])

    def test_a_single_429_in_ten_fails_the_one_percent_threshold(self):
        r = RP.run_rate(FakeHttp([200] * 9 + [429]), ["a"], "1.0", requests=10)
        self.assertEqual(r["HTTP_429_COUNT"], 1)
        self.assertFalse(r["PASSES"])

    def test_other_failures_are_counted_apart_from_429s(self):
        r = RP.run_rate(FakeHttp([500, 200, 200, 200]), ["a"], "1.0",
                        requests=4)
        self.assertEqual(r["OTHER_FAILURES"], 1)
        self.assertEqual(r["HTTP_429_COUNT"], 0)

    def test_the_ladder_runs_ascending_and_totals_add_up(self):
        http = FakeHttp([200] * 20)
        rep = RP.pilot(tempfile.mkdtemp(), ["a", "b"], http,
                       ladder=("0.25", "0.5"), requests=5)
        self.assertEqual(rep["TESTED_REQUEST_RATES"], ["0.25", "0.5"])
        self.assertEqual(rep["PILOT_REQUESTS"], 10)
        self.assertEqual(rep["SUCCESSFUL_REQUESTS"], 10)
        self.assertEqual(rep["HTTP_429_RATE"], D(0))

    def test_the_rows_and_report_are_written_to_disk(self):
        d = tempfile.mkdtemp()
        RP.pilot(d, ["a"], FakeHttp([200] * 4), ladder=("1.0",), requests=4)
        self.assertEqual(len(list(Path(d).glob("pilot_rows.jsonl"))), 1)
        rep = json.loads((Path(d) / "pilot_report.json").read_text())
        self.assertEqual(rep["ORDERS_PLACED"], 0)
        self.assertEqual(rep["CREDENTIALS"], "NONE")
        self.assertIs(rep["mirror_live"], False)

    def test_the_report_names_the_scheduler_and_the_absent_retry_loop(self):
        rep = RP.pilot(tempfile.mkdtemp(), ["a"], FakeHttp([200] * 4),
                       ladder=("1.0",), requests=4)
        self.assertEqual(rep["SCHEDULER"], "SINGLE_GLOBAL_PACER_NO_CONCURRENCY")
        self.assertEqual(rep["PER_MARKET_RETRY_LOOP"], "NONE_BY_CONSTRUCTION")

    def test_a_transport_error_is_recorded_not_raised(self):
        class Boom:
            def get(self, *_a, **_k):
                raise OSError("connection reset")
        r = RP.probe(Boom(), RP.GlobalPacer("1.0"), "a")
        self.assertIsNone(r["status"])
        self.assertIn("OSError", r["error"])


if __name__ == "__main__":
    unittest.main()


class RungsAreNotKnownToBeIndependent(unittest.TestCase):

    def setUp(self):
        self._saved = []
        no_sleep(self._saved)

    def tearDown(self):
        RP.time.sleep = self._saved[0]

    def test_the_window_semantics_are_not_identified(self):
        self.assertEqual(RP.RATE_LIMIT_WINDOW_SEMANTICS, NI)
        self.assertEqual(RP.SEQUENTIAL_RUNG_CARRYOVER_POSSIBLE, "YES")
        self.assertEqual(RP.RUNG_RESULT_INDEPENDENT, NI)

    def test_independence_is_never_established_even_with_a_cooldown(self):
        rep = RP.pilot(tempfile.mkdtemp(), ["a"], FakeHttp([200] * 8),
                       ladder=("0.25", "0.5"), requests=4)
        self.assertEqual(rep["PILOT_RUNG_INDEPENDENCE"], "NOT_ESTABLISHED")
        self.assertTrue(rep["COOLDOWN_BETWEEN_RUNGS"])

    def test_no_cooldown_is_named_as_a_second_reason(self):
        rep = RP.pilot(tempfile.mkdtemp(), ["a"], FakeHttp([200] * 8),
                       ladder=("0.25", "0.5"), requests=4, cooldown=False)
        self.assertEqual(rep["PILOT_RUNG_INDEPENDENCE"],
                         "NOT_ESTABLISHED_NO_COOLDOWN_BETWEEN_RUNGS")

    def test_the_selection_carries_the_carryover_warning(self):
        rep = RP.pilot(tempfile.mkdtemp(), ["a"], FakeHttp([200] * 8),
                       ladder=("0.25", "0.5"), requests=4)
        self.assertEqual(rep["SEQUENTIAL_RUNG_CARRYOVER_POSSIBLE"], "YES")
        self.assertIn("neither be excluded nor measured",
                      rep["WHY_NOT_INDEPENDENT"])


class TheCooldownNeverClaimsACleanStart(unittest.TestCase):

    def test_a_valid_retry_after_is_route_a(self):
        c = RP.rung_cooldown("12")
        self.assertEqual(c["COOLDOWN_S"], 12.0)
        self.assertEqual(c["COOLDOWN_BASIS"], "RETRY_AFTER_HONOURED_GLOBALLY")

    def test_an_observed_reset_is_route_b(self):
        c = RP.rung_cooldown(None, reset_timing=45)
        self.assertEqual(c["COOLDOWN_BASIS"], "OBSERVED_RESET_TIMING")

    def test_otherwise_route_c_waits_and_says_nothing_is_established(self):
        c = RP.rung_cooldown(None)
        self.assertEqual(c["COOLDOWN_S"], RP.FROZEN_COOLDOWN_S)
        self.assertEqual(c["COOLDOWN_BASIS"], "FROZEN_CONSERVATIVE_COOLDOWN")
        self.assertEqual(c["CLEAN_SERVER_RATE_LIMIT_STATE"], "NOT_ESTABLISHED")

    def test_an_unparseable_retry_after_falls_through_to_route_c(self):
        self.assertEqual(RP.rung_cooldown("soon")["COOLDOWN_BASIS"],
                         "FROZEN_CONSERVATIVE_COOLDOWN")

    def test_the_refusal_to_reverse_engineer_is_named(self):
        self.assertTrue(RP.DO_NOT_REVERSE_ENGINEER_HEADERS_TO_CLAIM_A_CLEAN_START)


class TheLadderScreensAndNominatesAndNothingElse(unittest.TestCase):
    """The ladder's own limits, stated by the ladder.

    The version this replaces required SUSTAINED *and* rung independence before
    a capture could proceed, while stating that rung independence can never be
    established. Those two rules together are a lock with no key. The tests
    below pin the fix: the ladder screens and nominates, its confirmation field
    is a refusal rather than a NOT_IDENTIFIED that reads like a pending value,
    and the reason it gives names a route that can actually return YES.
    """

    def setUp(self):
        self._saved = []
        no_sleep(self._saved)

    def tearDown(self):
        RP.time.sleep = self._saved[0]

    def test_a_rung_is_not_refused_or_refused_and_never_confirmed(self):
        r = RP.run_rate(FakeHttp([200] * 40), ["a"], "1.0", requests=40)
        self.assertTrue(r["PASSES"])
        self.assertEqual(r["RUNG_VERDICT"], "NOT_REFUSED")
        self.assertFalse(r["RUNG_MAY_CONFIRM"])
        self.assertNotIn("SUSTAINED", r)

    def test_a_refused_rung_says_refused(self):
        r = RP.run_rate(FakeHttp([200] * 9 + [429]), ["a"], "1.0", requests=10)
        self.assertEqual(r["RUNG_VERDICT"], "REFUSED")

    def test_the_ladder_may_screen_and_nominate(self):
        self.assertEqual(set(RP.LADDER_MAY),
                         {"REJECT_OBVIOUSLY_UNSAFE_RATES",
                          "NOMINATE_A_CONSERVATIVE_RATE_CANDIDATE"})

    def test_the_ladder_may_not_confirm_a_sustainable_rate(self):
        self.assertIn("CONFIRM_A_SUSTAINABLE_RATE", RP.LADDER_MAY_NOT)
        s = RP.select_rate([{"RATE_RPS": "0.5", "PASSES": True}])
        self.assertEqual(s["SUSTAINABLE_RATE_CONFIRMED"],
                         "REFUSED_THE_LADDER_MAY_NOT_CONFIRM")

    def test_the_three_reportable_outputs_are_present(self):
        s = RP.select_rate([{"RATE_RPS": "0.25", "PASSES": True},
                            {"RATE_RPS": "0.5", "PASSES": False}])
        self.assertEqual(s["UNSAFE_RATES"], ["0.5"])
        self.assertEqual(s["CANDIDATE_RATE"], "0.25")
        self.assertIn("RECOMMENDED_HEADROOM_RATE", s)

    def test_every_refused_rung_lands_in_unsafe_rates(self):
        s = RP.select_rate([{"RATE_RPS": "0.25", "PASSES": True},
                            {"RATE_RPS": "0.5", "PASSES": False},
                            {"RATE_RPS": "1.0", "PASSES": False},
                            {"RATE_RPS": "2.0", "PASSES": False}])
        self.assertEqual(s["UNSAFE_RATES"], ["0.5", "1.0", "2.0"])

    # ---- the deadlock itself, pinned so it cannot come back ----------------

    def test_the_two_questions_are_separate_fields(self):
        s = RP.select_rate([{"RATE_RPS": "0.5", "PASSES": True}])
        self.assertEqual(s["VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED"], NI)
        self.assertEqual(s["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"], "NO")

    def test_mechanism_identification_is_not_a_precondition(self):
        self.assertTrue(
            RP.OPERATIONAL_VALIDATION_DOES_NOT_REQUIRE_MECHANISM_IDENTIFICATION)
        s = RP.select_rate([{"RATE_RPS": "0.5", "PASSES": True}])
        self.assertTrue(s["INDEPENDENCE_IS_NOT_A_PRECONDITION_OF_VALIDATION"])

    def test_the_refusal_names_a_route_that_can_return_yes(self):
        """A gate that cannot open is a defect. This one says what opens it."""
        s = RP.select_rate([{"RATE_RPS": "0.5", "PASSES": True}])
        self.assertFalse(s["CAPTURE_MAY_PROCEED"])
        self.assertEqual(s["CONFIRMATION_ROUTE"],
                         "SINGLE_RATE_OPERATIONAL_CONFIRMATION")
        self.assertIn("rate_confirm", s["WHY_CAPTURE_MAY_NOT_PROCEED"])

    def test_rung_independence_no_longer_changes_any_verdict(self):
        """The old code turned ESTABLISHED into an authorisation. Nothing may
        hinge on a value that is never produced."""
        a = RP.select_rate([{"RATE_RPS": "0.5", "PASSES": True}],
                           "NOT_ESTABLISHED")
        b = RP.select_rate([{"RATE_RPS": "0.5", "PASSES": True}], "ESTABLISHED")
        for k in ("CANDIDATE_RATE", "SUSTAINABLE_RATE_CONFIRMED",
                  "CAPTURE_MAY_PROCEED", "RECOMMENDED_HEADROOM_RATE",
                  "COLLECTOR_RATE_OPERATIONALLY_VALIDATED"):
            self.assertEqual(a[k], b[k], k)

    def test_the_per_rung_metrics_are_all_reported(self):
        r = RP.run_rate(FakeHttp([200] * 4), ["a"], "1.0", requests=4)
        for k in ("REQUESTS", "DURATION_S", "SUCCESSFUL_REQUESTS", "HTTP_429",
                  "OTHER_FAILURES", "P50_RESPONSE_LATENCY",
                  "P90_RESPONSE_LATENCY", "P99_RESPONSE_LATENCY",
                  "VALID_RETRY_AFTER_COUNT", "TIME_TO_FIRST_429"):
            self.assertIn(k, r, k)

    def test_time_to_first_429_is_recorded_when_one_arrives(self):
        r = RP.run_rate(FakeHttp([200, 200, 429, 200]), ["a"], "1.0",
                        requests=4)
        self.assertNotEqual(r["TIME_TO_FIRST_429"], NI)
        self.assertEqual(r["HTTP_429"], 1)

    def test_a_clean_rung_has_no_time_to_first_429(self):
        r = RP.run_rate(FakeHttp([200] * 4), ["a"], "1.0", requests=4)
        self.assertEqual(r["TIME_TO_FIRST_429"], NI)

    def test_valid_retry_after_headers_are_counted_apart_from_junk(self):
        http = FakeHttp([429, 429], headers={"Retry-After": "not-a-number"})
        r = RP.run_rate(http, ["a"], "1.0", requests=2)
        self.assertEqual(r["VALID_RETRY_AFTER_COUNT"], 0)


class TheCaptureRateHasHeadroom(unittest.TestCase):

    def test_the_recommendation_is_one_rung_below_the_candidate(self):
        s = RP.select_rate([{"RATE_RPS": "0.25", "PASSES": True},
                            {"RATE_RPS": "0.5", "PASSES": True},
                            {"RATE_RPS": "1.0", "PASSES": False}])
        self.assertEqual(s["CANDIDATE_RATE"], "0.5")
        self.assertEqual(s["RECOMMENDED_HEADROOM_RATE"], "0.25")
        self.assertTrue(s["RECOMMENDED_RATE_HAS_HEADROOM"])

    def test_a_candidate_on_the_slowest_rung_has_no_headroom_rate_at_all(self):
        """This is the pilot's actual shape, and it must NOT quietly resolve to
        the candidate: 0.25 is the slowest rung tested, so no rate below it has
        been measured, and inventing one from the ladder's spacing would be a
        number chosen after seeing the result."""
        s = RP.select_rate([{"RATE_RPS": "0.25", "PASSES": True},
                            {"RATE_RPS": "0.5", "PASSES": False}])
        self.assertEqual(s["CANDIDATE_RATE"], "0.25")
        self.assertEqual(s["RECOMMENDED_HEADROOM_RATE"], NI)
        self.assertEqual(s["RECOMMENDED_CAPTURE_RATE"], NI)
        self.assertFalse(s["RECOMMENDED_RATE_HAS_HEADROOM"])
        self.assertIn("new measurement", s["HEADROOM_NOTE"])

    def test_the_objective_is_recorded_as_data_not_throughput(self):
        s = RP.select_rate([{"RATE_RPS": "0.5", "PASSES": True}])
        self.assertEqual(s["OBJECTIVE"],
                         "BALANCED_COMPLETE_DATA_NOT_MAXIMUM_REQUEST_THROUGHPUT")
        self.assertEqual(s["HEADROOM_RULE"],
                         "ONE_LADDER_RUNG_BELOW_THE_FASTEST_PASSING_RATE")
