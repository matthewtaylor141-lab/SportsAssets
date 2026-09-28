"""The unsuppressible cycle readback: its verdicts, and what it refuses.

WHY EACH CASE IS HERE. The readback's whole value is that it distinguishes
three situations that all present as "zero candidates evaluated", and names a
fourth (unanswered). If it collapsed any two of them it would be another
number that reads as a measurement and is not one.
"""

from __future__ import annotations

import json

from sportsassets import cycle_readback as CR


def _desk(*, at=1_790_000_000.0, considered=None, evaluated=None,
          refusals=None, ledger=None, **controls):
    """A desk payload shaped like `/api/command/bettor/desk` ACTUALLY is.

    NOT like `command_center`. The first working run of this module returned
    NO_SCHEDULED_CYCLE_HEARTBEAT because it read `last_scheduled_decision` at
    the top level -- a key the desk route does not have. The route loads the
    heartbeat itself and spreads it across `controls` and `opportunities`, so
    the fixture is built that way and a test cannot pass against a shape
    production does not serve.
    """
    return {"ok": True,
            "controls": dict({"last_cycle_at": at,
                              "cycle_state": "RAN",
                              "cycle_label": "L",
                              "build_identity": {"sha": "abc1234"}},
                             **controls),
            "opportunities": {"markets_considered": considered,
                              "evaluated": evaluated,
                              "refusals": refusals,
                              "first_refusal_per_mapped_candidate": ledger,
                              "funnel": None, "venue_errors": None,
                              "odds_freshness": None}}


# ═════════════════════════════════════════════════════════════════════
# THE FOUR OUTCOMES, KEPT APART
# ═════════════════════════════════════════════════════════════════════

def test_an_absent_heartbeat_is_unanswered_not_idle():
    """No `last_scheduled_decision` means the question was not answered.

    Reporting it as "nothing happened" would let a build that does not write
    the row look like a quiet lane -- and it fails the job, because a
    diagnostic that cannot see anything must not read as green.
    """
    v = CR.verdict({"ok": True})
    assert v["verdict"] == CR.V_NO_HEARTBEAT
    assert v["ok"] is False
    assert v["verdict"] in CR.FAILING
    assert "unanswered" in v["why"]


def test_an_empty_funnel_is_a_state_of_the_world_and_does_not_fail():
    """No fixtures at all is not a broken system.

    Failing on it would train the reader to ignore this job, which is the
    surest way to make an unsuppressible diagnostic useless.
    """
    v = CR.verdict(_desk(considered=0, evaluated=0, refusals={}, ledger=[]))
    assert v["verdict"] == CR.V_NOTHING_TO_EVALUATE
    assert v["ok"] is True
    assert v["verdict"] not in CR.FAILING
    # AND IT IS EXPLICITLY NOT EVIDENCE THAT THE LANE WORKS.
    assert "NOT" in v["why"] and "works" in v["why"]


def test_every_candidate_refused_with_named_reasons_does_not_fail():
    """Named refusals are the finding, not a failure of the instrument."""
    v = CR.verdict(_desk(
        considered=4, evaluated=0,
        refusals={"VENUE_BOOK_READ_RETURNED_ERROR": 1,
                  "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP": 3},
        ledger=[{"slug": "aec-mlb-chc-sd-2026-09-30",
                 "refusal": "VENUE_BOOK_READ_RETURNED_ERROR"}]))
    assert v["verdict"] == CR.V_ALL_REFUSED_ACCOUNTED
    assert v["ok"] is True
    assert v["refusal_total"] == 4
    assert v["mapped_candidates"] == 1


def test_considered_but_unaccounted_is_a_defect_and_fails():
    """THE ONE CASE THAT FAILS: the lane is not measuring itself.

    Markets entered the funnel, none was evaluated, and the census names no
    refusal and lists no candidate. That is a defect in the INSTRUMENT, and
    it is exactly the condition that made `evaluated 0` with an empty
    refusal list read as "nothing happened".
    """
    v = CR.verdict(_desk(considered=7, evaluated=0, refusals={}, ledger=[]))
    assert v["verdict"] == CR.V_UNACCOUNTED
    assert v["ok"] is False
    assert v["verdict"] in CR.FAILING
    assert "defect in the instrument" in v["why"]


def test_evaluated_candidates_pass():
    v = CR.verdict(_desk(considered=5, evaluated=2, refusals={"X": 3}))
    assert v["verdict"] == CR.V_EVALUATED
    assert v["ok"] is True
    assert v["evaluated"] == 2


# ═════════════════════════════════════════════════════════════════════
# AN ABSENT PATH IS NAMED, NOT PRINTED AS EMPTY
# ═════════════════════════════════════════════════════════════════════

def test_an_absent_field_is_reported_by_name_not_as_a_blank():
    """The four-wrong-JSON-paths lesson, encoded.

    An empty column from a wrong path is indistinguishable from an absent
    field, so the reader must be told WHICH key was missing.
    """
    c = CR.census({"ok": True})
    assert c["cycle_at_is_absent"] is True
    assert c["desk_has_a_cycle_section"] is False
    assert "controls" in c["paths_absent"]


def test_an_older_serving_build_is_named_as_the_build_not_as_controls_off():
    """`venue_sdk` absent means the BUILD does not report it.

    Reading an absent block as "the SDK is unpinned" or "the cooldown is
    off" would be inferring a fact about the system from a fact about the
    projection -- the same error as reading a null timestamp as "the read
    never returned".
    """
    c = CR.census(_desk(considered=1, evaluated=0))
    assert c["venue_sdk_is_absent"] is True
    assert c["venue_rate_controls_is_absent"] is True
    assert "controls.venue_sdk" in c["paths_absent"]

    # And when the build DOES report it, it comes through intact.
    c2 = CR.census(_desk(venue_sdk={"pinned": "1.0.2", "installed": "1.0.2",
                     "pinned_matches_installed": True}))
    assert c2["venue_sdk_is_absent"] is False
    assert c2["venue_sdk"]["pinned_matches_installed"] is True


def test_a_non_numeric_count_does_not_become_zero():
    """A string or None count must not silently read as 0.

    `evaluated: null` with markets considered is the UNACCOUNTED case; if
    None coerced to 0 it would be indistinguishable from a measured zero.
    """
    v = CR.verdict(_desk(considered=3, evaluated=None, refusals={}, ledger=[]))
    assert v["evaluated"] is None
    assert v["verdict"] == CR.V_UNACCOUNTED


# ═════════════════════════════════════════════════════════════════════
# IT CANNOT MUTATE ANYTHING
# ═════════════════════════════════════════════════════════════════════

def test_the_readback_issues_only_a_GET_and_no_write_verb():
    """CONTAINMENT, by AST and by source.

    This module is run OUTSIDE the gated job so a fixture failure cannot
    suppress it. That is only safe because it cannot arm, seed or submit.
    An `always()` around a step that mutates would run the mutation on a
    failed run, which is the opposite of a diagnostic -- so the absence of
    every write verb is asserted rather than intended.
    """
    import ast
    import inspect

    src = inspect.getsource(CR)
    tree = ast.parse(src)

    methods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords or []:
                if kw.arg == "method" and isinstance(kw.value, ast.Constant):
                    methods.add(kw.value.value)
    assert methods == {"GET"}, (
        "the readback issues %r; it must be a read only" % (sorted(methods),))

    for verb in ('"POST"', "'POST'", '"PUT"', '"DELETE"', '"PATCH"'):
        assert verb not in src, "a write verb appears in the readback: %s" % verb
    # And no arming or seeding vocabulary reaches it.
    for word in ("submit_fok", "orders.create", "seed_acceptance",
                 "arm_external", "run_full"):
        assert word not in src, "the readback reaches %s" % word


def test_the_desk_path_is_the_read_only_command_route():
    """The route it reads is one `require_command` grants read-only."""
    assert CR.DESK_PATH == "/api/command/bettor/desk"
    assert "/api/command/" in CR.DESK_PATH


def test_a_failed_read_is_unreadable_rather_than_a_healthy_lane():
    """An HTTP error is the diagnostic failing, not the lane passing."""
    v = {"verdict": CR.V_UNREADABLE}
    assert v["verdict"] in CR.FAILING


def test_only_measurement_failures_fail_the_job():
    """The failing set is exactly those that mean 'we cannot tell'."""
    assert CR.FAILING == {CR.V_NO_HEARTBEAT, CR.V_UNREADABLE,
                          CR.V_UNACCOUNTED, CR.V_SERVICE_UNREACHABLE}
    for ok_verdict in (CR.V_NOTHING_TO_EVALUATE, CR.V_ALL_REFUSED_ACCOUNTED,
                       CR.V_EVALUATED):
        assert ok_verdict not in CR.FAILING


def test_a_dead_service_and_a_slow_desk_are_different_verdicts():
    """FOUND BY RUNNING IT: one timeout message could not tell them apart.

    The first live run printed only `TimeoutError reading
    /api/command/bettor/desk` at exactly the 60 s limit it was given. That
    cannot distinguish a service that is down from a desk slower than the
    limit allowed -- and the two send a reader to different places. `/healthz`
    is asked first and cheaply so the expensive read's timeout is a statement
    about the DESK.
    """
    assert CR.V_SERVICE_UNREACHABLE != CR.V_UNREADABLE
    assert CR.HEALTH_TIMEOUT_S < CR.DESK_TIMEOUT_S
    assert CR.DESK_TIMEOUT_S >= 120, (
        "the desk assembles sixteen sections on a possibly-cold service; a "
        "limit that always trips makes this job establish nothing")
    assert CR.HEALTH_PATH == "/healthz"


def test_the_desk_read_is_retried_once_but_a_4xx_is_not():
    """Bounded, and it does not retry an answer.

    A cold start is the common cause of a first-attempt failure, so one
    retry is worth it. A 4xx is an answer about our credential or the route
    and will not change -- retrying it would spend time to be told the same
    thing, the same reasoning as not retrying a 404 book read.
    """
    calls = []

    def fake_get(api, path, *, token=None, timeout, parse_json=True):
        calls.append(path)
        return {"ok": False, "status": 401, "why": "HTTP 401"}

    import sportsassets.cycle_readback as mod
    real = mod._get
    try:
        mod._get = fake_get
        got = mod.fetch("https://api.test", "t", sleep=lambda _s: None)
    finally:
        mod._get = real
    assert got["attempts"] == 1, "a 401 must not be retried"
    assert got["why_not_retried"]
    assert CR.DESK_ATTEMPTS == 2


def test_a_cold_first_attempt_is_retried_and_succeeds():
    seq = [{"ok": False, "status": None, "why": "TimeoutError"},
           {"ok": True, "status": 200, "body": {"ok": True}}]

    def fake_get(api, path, *, token=None, timeout, parse_json=True):
        return seq.pop(0)

    import sportsassets.cycle_readback as mod
    real = mod._get
    try:
        mod._get = fake_get
        got = mod.fetch("https://api.test", "t", sleep=lambda _s: None)
    finally:
        mod._get = real
    assert got["ok"] is True
    assert got["attempts"] == 2


def test_the_verdict_is_serialisable_for_a_step_summary():
    v = CR.verdict(_desk(considered=2, evaluated=0, refusals={"A": 2}, ledger=[]))
    json.dumps(v, default=str)


def test_every_path_the_readback_reads_is_one_the_desk_route_writes():
    """THE TEST THAT WOULD HAVE CAUGHT THE WRONG PATHS BEFORE A RUN DID.

    Twice now a readback of mine has used JSON paths that do not exist. The
    first printed empty columns and I could not tell; the second returned
    NO_SCHEDULED_CYCLE_HEARTBEAT and I read it as production's answer for one
    run. Both times the cause was the same: the reader's idea of the payload
    was never checked against the writer's.

    So it is checked here, against the ROUTE'S OWN SOURCE. Every leaf key the
    readback looks for must appear as a literal in `bettor_desk`, and the
    section it hangs under must be a key the route assigns. This cannot prove
    the nesting is right on its own, which is why the fixture in this file is
    built in the route's shape as well -- but it does catch a key that simply
    is not there, which is what happened.
    """
    import inspect

    from sportsassets.api import app as A
    from sportsassets import cycle_readback as CR

    src = inspect.getsource(A.bettor_desk)

    # The two sections the readback hangs everything under.
    for section in ("controls", "opportunities"):
        assert 'out["%s"]' % section in src, (
            "the desk route no longer assigns out[%r]; every readback path "
            "under it is now wrong" % section)

    paths = [CR.P_CYCLE_AT, CR.P_CYCLE_STATE, CR.P_CYCLE_LABEL, CR.P_WRITER,
             CR.P_CONSIDERED, CR.P_EVALUATED, CR.P_REFUSALS, CR.P_LEDGER,
             CR.P_FUNNEL, CR.P_VENUE_ERRORS, CR.P_LATENCY,
             CR.P_VENUE_SDK, CR.P_RATE_CONTROLS, CR.P_PACER_LANES]
    missing = []
    for section, leaf in paths:
        assert section in ("controls", "opportunities"), (section, leaf)
        if '"%s"' % leaf not in src:
            missing.append("%s.%s" % (section, leaf))
    assert not missing, (
        "the readback reads keys the desk route never writes: %r. An absent "
        "key yields an empty census, which reads exactly like a quiet lane"
        % (missing,))


def test_the_readback_does_not_read_a_command_center_shaped_payload():
    """The specific wrong assumption, pinned so it cannot come back.

    `bettor_funded_book.command_center` does carry
    `last_scheduled_decision` -- which is why the mistake was plausible --
    but the desk route does not embed it. A payload in that shape must NOT
    satisfy this readback, because if it did, the test suite would be
    agreeing with a shape production does not serve.
    """
    from sportsassets import cycle_readback as CR

    command_center_shaped = {
        "last_scheduled_decision": {
            "at": 1_790_000_000.0, "cycle_state": "RAN",
            "markets_considered": 4, "evaluated": 0, "refusals": {"X": 4},
        }
    }
    v = CR.verdict(command_center_shaped)
    assert v["verdict"] == CR.V_NO_HEARTBEAT, (
        "a command_center-shaped payload was accepted; the readback is "
        "reading the wrong route's shape again")
    assert "controls" in v["paths_absent"]
