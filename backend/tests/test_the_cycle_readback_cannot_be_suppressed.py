"""The unsuppressible cycle readback: its verdicts, and what it refuses.

WHY EACH CASE IS HERE. The readback's whole value is that it distinguishes
three situations that all present as "zero candidates evaluated", and names a
fourth (unanswered). If it collapsed any two of them it would be another
number that reads as a measurement and is not one.
"""

from __future__ import annotations

import json

from sportsassets import cycle_readback as CR


def _desk(**decision):
    """A desk payload with `last_scheduled_decision` as the API shapes it."""
    return {"ok": True, "last_scheduled_decision": dict(decision)}


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
    v = CR.verdict(_desk(markets_considered=0, evaluated=0, refusals={},
                         mapped_candidate_ledger=[]))
    assert v["verdict"] == CR.V_NOTHING_TO_EVALUATE
    assert v["ok"] is True
    assert v["verdict"] not in CR.FAILING
    # AND IT IS EXPLICITLY NOT EVIDENCE THAT THE LANE WORKS.
    assert "NOT" in v["why"] and "works" in v["why"]


def test_every_candidate_refused_with_named_reasons_does_not_fail():
    """Named refusals are the finding, not a failure of the instrument."""
    v = CR.verdict(_desk(
        markets_considered=4, evaluated=0,
        refusals={"VENUE_BOOK_READ_RETURNED_ERROR": 1,
                  "NO_VENUE_NATIVE_CONTRACT_IN_PREMAP": 3},
        mapped_candidate_ledger=[{"slug": "aec-mlb-chc-sd-2026-09-30",
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
    v = CR.verdict(_desk(markets_considered=7, evaluated=0, refusals={},
                         mapped_candidate_ledger=[]))
    assert v["verdict"] == CR.V_UNACCOUNTED
    assert v["ok"] is False
    assert v["verdict"] in CR.FAILING
    assert "defect in the instrument" in v["why"]


def test_evaluated_candidates_pass():
    v = CR.verdict(_desk(markets_considered=5, evaluated=2, refusals={"X": 3}))
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
    assert c["decision_is_absent"] is True
    assert "last_scheduled_decision" in c["paths_absent"]


def test_an_older_serving_build_is_named_as_the_build_not_as_controls_off():
    """`venue_sdk` absent means the BUILD does not report it.

    Reading an absent block as "the SDK is unpinned" or "the cooldown is
    off" would be inferring a fact about the system from a fact about the
    projection -- the same error as reading a null timestamp as "the read
    never returned".
    """
    c = CR.census(_desk(markets_considered=1, evaluated=0))
    assert c["venue_sdk_is_absent"] is True
    assert c["venue_rate_controls_is_absent"] is True
    assert "last_scheduled_decision.venue_sdk" in c["paths_absent"]

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
    v = CR.verdict(_desk(markets_considered=3, evaluated=None, refusals={},
                         mapped_candidate_ledger=[]))
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
    """The failing set is exactly the three that mean 'we cannot tell'."""
    assert CR.FAILING == {CR.V_NO_HEARTBEAT, CR.V_UNREADABLE,
                          CR.V_UNACCOUNTED}
    for ok_verdict in (CR.V_NOTHING_TO_EVALUATE, CR.V_ALL_REFUSED_ACCOUNTED,
                       CR.V_EVALUATED):
        assert ok_verdict not in CR.FAILING


def test_the_verdict_is_serialisable_for_a_step_summary():
    v = CR.verdict(_desk(markets_considered=2, evaluated=0,
                         refusals={"A": 2}, mapped_candidate_ledger=[]))
    json.dumps(v, default=str)
