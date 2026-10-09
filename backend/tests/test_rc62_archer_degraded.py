"""(RC6.2 D6g) A DESK / LOOP WHOSE EVERY RUN ERRORS IS NEVER SHOWN HEALTHY.

Production: agent_status ARCHER runs 1,017, errors 1,017, last_error
'results:TimeoutError'; each run still finished IDLE / NO_NEW_CANDIDATE, so
the floor desk read IDLE, and the agent_archer service heartbeat said 'ok'
(with detail phase_errors {results: TimeoutError}), so loop health read
HEALTHY. The Archer results-phase timeout fix (e5b12392) is already an
ancestor of rc6/int-62; this is the display truth:

  desk   runs > 0 and errors >= runs -> WAITING with `degraded` (from the
         recorded runs / errors / last_error), even over a recent output
  loop   newest success beat carries non-empty phase_errors -> DEGRADED
         (named), and a capital-critical DEGRADED loop is not healthy
"""
from __future__ import annotations

from sportsassets import loop_health as LH
from sportsassets.api import command_floor as FL

NOW = 1_800_000_000.0
ARCHER_ALL_ERRORED = {"state": "IDLE", "activity": "NO_NEW_CANDIDATE",
                      "runs": 1017, "errors": 1017,
                      "last_error": "results:TimeoutError",
                      "last_run_started_at": NOW - 60,
                      "last_run_finished_at": NOW - 33}


def _desk(status, signals=()):
    return FL.derive_state("ARCHER", now=NOW, deployed=True,
                           deploy_why=None, heartbeat_at=NOW - 30,
                           stale_s=900.0, status=status,
                           signals=list(signals))


def test_every_run_errored_is_degraded_not_idle():
    s = _desk(ARCHER_ALL_ERRORED)
    assert s["state"] == "WAITING", s
    assert s["degraded"] == {"why": "EVERY_RECORDED_RUN_ERRORED",
                             "runs": 1017, "errors": 1017,
                             "last_error": "results:TimeoutError"}
    assert "1017 of 1017" in s["detail"]
    assert "results:TimeoutError" in s["detail"]


def test_a_recent_output_does_not_hide_the_error_rate():
    s = _desk(ARCHER_ALL_ERRORED, [{"at": NOW - 10, "hint": "WORKING_ON",
                                    "label": "Estimated execution"}])
    assert s["state"] == "WAITING" and s["degraded"]


def test_a_run_that_succeeded_is_not_degraded():
    st = dict(ARCHER_ALL_ERRORED, errors=1016)
    assert FL.run_errors_degraded(st) is None
    assert _desk(st)["state"] == "IDLE"
    assert FL.run_errors_degraded({"runs": 0, "errors": 0}) is None
    assert FL.run_errors_degraded(None) is None


def _archer_spec():
    return next(s for s in LH.INVENTORY if s["name"] ==
                "agents.archer_runner")


def test_loop_health_reads_a_phase_errored_success_beat_as_degraded():
    spec = _archer_spec()
    label = "service_heartbeats:agent_archer"
    facts = {"success_at": [(NOW - 30, label)], "sources_read": [label],
             "sources_missing": [], "beat_status": "ok",
             "phase_errors": {"results": "TimeoutError"},
             "phase_errors_source": label}
    v = LH.classify(spec, facts, now=NOW)
    assert v["status"] == LH.DEGRADED, v
    assert v["why"] == "LATEST_RUN_PHASE_ERRORS:results:TimeoutError"
    assert v["phase_errors"] == {"results": "TimeoutError"}
    clean = {k: v for k, v in facts.items()
             if not k.startswith("phase_errors")}
    assert LH.classify(spec, clean, now=NOW)["status"] == LH.HEALTHY


def test_heartbeat_phase_errors_parse():
    assert LH.beat_phase_errors('{"results": "TimeoutError"}') == {
        "results": "TimeoutError"}
    assert LH.beat_phase_errors("{}") == {}
    assert LH.beat_phase_errors(None) == {}
    assert LH.beat_phase_errors("not json") == {}
    assert LH.beat_phase_errors({"a": None}) == {}


def test_loop_health_read_carries_the_heartbeat_phase_errors():
    """Real Postgres: the production-shaped agent_archer beat ('ok', detail
    phase_errors {results: TimeoutError}) reads DEGRADED through LH.read;
    the same beat with phase_errors {} reads HEALTHY."""
    import asyncio
    import json
    import os
    import time

    import asyncpg
    import pytest

    dsn = os.environ.get("RN1X_TEST_DSN", "")
    if not dsn:
        pytest.skip("needs RN1X_TEST_DSN")

    async def one(pe):
        c = await asyncpg.connect(dsn)
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute(
                "INSERT INTO service_heartbeats (service, status, detail, "
                "beat_at) VALUES ('agent_archer', 'ok', $1::jsonb, now()) "
                "ON CONFLICT (service) DO UPDATE SET status = 'ok', "
                "detail = EXCLUDED.detail, beat_at = now()",
                json.dumps({"status": "NO_NEW_CANDIDATE", "phase_errors": pe,
                            "estimated": 0}))
            body = await LH.read(c, now=time.time(),
                                 env={"ARCHER_RUNNER_ENABLED": "1"})
        finally:
            await tx.rollback()
            await c.close()
        return next(lp for lp in body["loops"]
                    if lp["name"] == "agents.archer_runner")

    bad = asyncio.run(one({"results": "TimeoutError"}))
    assert bad["status"] == LH.DEGRADED, bad
    assert bad["why"] == "LATEST_RUN_PHASE_ERRORS:results:TimeoutError"
    ok = asyncio.run(one({}))
    assert ok["status"] == LH.HEALTHY, ok


def test_the_agent_status_contract_never_reads_a_degraded_loop_green():
    from sportsassets import agent_status_contract as A
    from tests import test_agent_status_contract as T

    v = T._lv("agents.archer_runner", "api", status=LH.DEGRADED,
              why="LATEST_RUN_PHASE_ERRORS:results:TimeoutError")
    got = T._loop("agents.archer_runner", "api", v)
    assert got["status"] == A.DEGRADED, got["status_reasons"]
    assert ("LOOP_DEGRADED:LATEST_RUN_PHASE_ERRORS:results:TimeoutError"
            in got["status_reasons"])
