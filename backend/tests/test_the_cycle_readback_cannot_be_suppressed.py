"""The unsuppressible cycle readback: the properties that are route-agnostic.

WHERE THE VERDICT SEMANTICS LIVE. They moved to
`test_the_cycle_readback_has_no_false_green.py`, which builds its fixtures in
the shape `/api/command/rn1x/statuses` actually serves. The cases that used to
be here were written against a desk-shaped payload -- and a test agreeing with
a shape production does not serve is worse than no test, because it passes.

WHAT STAYS HERE: the properties that hold whatever route is read -- that this
module can only GET, that a dead service and a slow route are different
verdicts, that the read is retried once but a 4xx is not, and that only
measurement failures fail the job.

WHY EACH CASE IS HERE. The readback's whole value is that it distinguishes
three situations that all present as "zero candidates evaluated", and names a
fourth (unanswered). If it collapsed any two of them it would be another
number that reads as a measurement and is not one.
"""

from __future__ import annotations

import json

from sportsassets import cycle_readback as CR


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
    v = CR.verdict({"statuses": {"external_valuation": {"last_cycle": {
        "at": 1_790_000_000.0, "state": "LIVE", "markets_considered": 2,
        "evaluated": 0, "refusals": {"A": 2},
        "mapped_candidate_ledger": []}}}})
    json.dumps(v, default=str)

def test_the_reader_is_pinned_to_the_route_that_returns_the_whole_row():
    """THE PATH-AGREEMENT TEST, RE-AIMED AT THE ROUTE ACTUALLY USED.

    The previous version checked the readback's keys against `bettor_desk`'s
    source -- the right idea aimed at the wrong route. `/api/command/rn1x/
    statuses` loads the heartbeat WHOLE, so the keys to agree on are the
    LOOP'S own field names, and the check is against the loop, which writes
    them.

    This also removes the dependency that made the desk version fragile:
    basic EV diagnosis no longer needs a projection to be deployed first.
    """
    import inspect

    from sportsassets import cycle_readback as CR
    from sportsassets.api import command_rn1x as CRN
    from sportsassets.workers import ext_pinnacle_loop as LOOP

    # The route loads the row under the path the reader expects.
    ev_src = inspect.getsource(CRN)
    assert 'out["last_cycle"]' in ev_src
    assert CR.P_CYCLE == ("statuses", "external_valuation", "last_cycle")
    assert CR.STATUSES_PATH == "/api/command/rn1x/statuses"
    # And it loads it from the key the loop writes, not a different row.
    assert "HEARTBEAT_KEY" in ev_src
    assert LOOP.HEARTBEAT_KEY == "ext_pinnacle_last_cycle"

    # Every field the reader names is one the loop actually writes. A DOTTED
    # name is a NESTED field: its leaf is written by the digest that builds
    # the block, not by `_heartbeat` itself, so both sources are searched and
    # the nesting is spelled out in the constant rather than assumed.
    #
    # THIS CHECK ALREADY EARNED ITS KEEP: the reader named `pacer_lanes` at
    # top level and the loop writes it inside `venue_rate_controls`. The test
    # caught it before a production run did, which is the whole point of
    # pinning the reader against the writer instead of against my memory.
    writes = (inspect.getsource(LOOP._heartbeat)
              + inspect.getsource(LOOP._rate_control_digest)
              + inspect.getsource(LOOP._venue_sdk_digest))
    for field in (CR.F_AT, CR.F_STATE, CR.F_LABEL, CR.F_WRITER,
                  CR.F_CONSIDERED, CR.F_EVALUATED, CR.F_WRITTEN,
                  CR.F_REFUSALS, CR.F_LEDGER, CR.F_VENUE_SDK,
                  CR.F_RATE_CONTROLS, CR.F_PACER_LANES):
        leaf = field.rsplit(".", 1)[-1]
        assert '"%s"' % leaf in writes, (
            "the reader names %r and nothing in the loop writes %r"
            % (field, leaf))
        if "." in field:
            parent = field.rsplit(".", 1)[0]
            assert '"%s"' % parent in writes, (
                "the reader nests %r under %r and the loop does not write "
                "that parent" % (leaf, parent))


def test_a_desk_shaped_payload_is_refused():
    """The reader must not accept the shape it used to read.

    If a desk-shaped payload satisfied it, the suite would be agreeing with a
    route this module no longer calls -- the same trap as before, reversed.
    """
    from sportsassets import cycle_readback as CR

    desk_shaped = {"controls": {"last_cycle_at": 1_790_000_000.0},
                   "opportunities": {"markets_considered": 4,
                                     "evaluated": 0,
                                     "refusals": {"X": 4}}}
    v = CR.verdict(desk_shaped)
    assert v["verdict"] == CR.V_NO_HEARTBEAT
    assert "statuses" in " ".join(v["paths_absent"])
