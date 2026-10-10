"""(RC6.2 D6g) A DESK / LOOP WHOSE EVERY RUN ERRORS IS NEVER SHOWN HEALTHY.

Production: agent_status ARCHER runs 1,017, errors 1,017, last_error
'results:TimeoutError'; each run still finished IDLE / NO_NEW_CANDIDATE, so
the floor desk read IDLE, and the agent_archer service heartbeat said 'ok'
(with detail phase_errors {results: TimeoutError}), so loop health read
HEALTHY. The Archer results-phase timeout fix (e5b12392) is already an
ancestor of rc6/int-62; this is the display truth:

  desk   the newest FINISHED Archer run (agent_runs), inside the desk's
         heartbeat window, recorded phase errors (or ended FAILED) ->
         WAITING with detail "DEGRADED: newest finished run recorded phase
         errors <phase>:<err> · K of the last N finished runs errored" and
         the run as `degraded`; it outranks a recent output, a run in
         progress still reads as one, a clean newest run clears it. The
         lifetime agent_status counters are never shown as "N of M runs
         errored" (errors also counts non-run heartbeats).
  loop   newest success beat carries non-empty phase_errors -> DEGRADED
         (named), and a capital-critical DEGRADED loop is not healthy
  contract  agent_status_contract never reads that loop GREEN
"""
from __future__ import annotations

import json
import os
import time

import pytest

from sportsassets import loop_health as LH
from sportsassets.api import command_floor as FL

NOW = 1_800_000_000.0
DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
ARCHER_ALL_ERRORED = {"state": "IDLE", "activity": "NO_NEW_CANDIDATE",
                      "runs": 1017, "errors": 1017,
                      "last_error": "results:TimeoutError",
                      "last_run_started_at": NOW - 60,
                      "last_run_finished_at": NOW - 33}


def _desk(status, signals=(), degraded=None):
    return FL.derive_state("ARCHER", now=NOW, deployed=True,
                           deploy_why=None, heartbeat_at=NOW - 30,
                           stale_s=900.0, status=status,
                           signals=list(signals), degraded=degraded)


def _run(i, at, phase_errors=None, outcome="NO_NEW_CANDIDATE"):
    return {"run_id": "archer-run:%d" % i, "started_at": at - 27,
            "finished_at": at, "outcome": outcome,
            "phase_errors": json.dumps(phase_errors or {})}


def test_lifetime_counters_never_read_as_every_run_errored():
    # (RC6.2 be-truth review) agent_status runs/errors are lifetime counters
    # and `errors` also counts non-run heartbeats (e.g. a gate hook raising),
    # so the desk never presents them as "N of M runs errored".
    for st in (ARCHER_ALL_ERRORED, dict(ARCHER_ALL_ERRORED, runs=1, errors=2)):
        s = _desk(st)
        assert "degraded" not in s, s
        assert "runs errored" not in s["detail"], s
        assert "EVERY_RECORDED_RUN_ERRORED" not in repr(s), s


def test_the_desk_reads_degraded_from_the_newest_finished_runs_errors():
    """One clean run, then three runs whose results phase timed out (the
    archer_runner summary shape): the newest finished run is the truth."""
    te = {"results": "TimeoutError"}
    runs = [_run(4, NOW - 33, te), _run(3, NOW - 333, te),
            _run(2, NOW - 633, te), _run(1, NOW - 933)]
    d = FL.run_degraded(runs, now=NOW, window_s=900.0)
    assert d == {"why": "LATEST_RUN_PHASE_ERRORS", "source": "agent_runs",
                 "run_id": "archer-run:4", "finished_at": NOW - 33,
                 "outcome": "NO_NEW_CANDIDATE", "phase_errors": te,
                 "errored_runs": 3, "finished_runs_read": 4,
                 "rule": d["rule"]}
    est = {"at": NOW - 40, "hint": "WORKING_ON", "label": "Estimated",
           "ref": {"kind": "eddie_execution_estimates", "id": "e1"}}
    s = _desk(dict(ARCHER_ALL_ERRORED, runs=4, errors=3), [est], d)
    assert s["state"] == "WAITING", s
    assert s["detail"] == ("DEGRADED: newest finished run recorded phase "
                           "errors results:TimeoutError · 3 of the last 4 "
                           "finished runs errored")
    assert s["activity_basis"] == "LATEST_RUN_PHASE_ERRORS"
    assert s["basis"] == [{"kind": "agent_runs", "id": "archer-run:4",
                           "at": NOW - 33, "why": "LATEST_RUN_PHASE_ERRORS"}]
    assert s["degraded"] is d and s["since"] == NOW - 33
    # a run in progress is still shown as the run in progress
    busy = _desk(dict(ARCHER_ALL_ERRORED, state="EVALUATING",
                      last_run_started_at=NOW - 10), (), d)
    assert busy["state"] == "WORKING_ON", busy
    assert busy["activity_basis"] == "RUN_IN_PROGRESS"


def test_a_clean_or_old_newest_run_is_not_degraded():
    te = {"results": "TimeoutError"}
    # the newest run is clean: errors before it are history, not state
    assert FL.run_degraded([_run(2, NOW - 33), _run(1, NOW - 333, te)],
                           now=NOW, window_s=900.0) is None
    # the newest errored run finished outside the desk's window
    assert FL.run_degraded([_run(1, NOW - 1000, te)], now=NOW,
                           window_s=900.0) is None
    assert FL.run_degraded([], now=NOW, window_s=900.0) is None
    # a FAILED outcome with no phase recorded is named as such
    f = FL.run_degraded([_run(1, NOW - 33, outcome="FAILED")], now=NOW,
                        window_s=900.0)
    assert f["why"] == "LATEST_RUN_FAILED" and f["phase_errors"] == {}
    assert FL.degraded_detail(f) == ("DEGRADED: newest finished run ended "
                                     "FAILED · 1 of the last 1 finished "
                                     "runs errored")


def test_run_phase_errors_parse():
    assert FL.run_phase_errors('{"results": "TimeoutError"}') == {
        "results": "TimeoutError"}
    assert FL.run_phase_errors({"a": None, "b": ""}) == {}
    assert FL.run_phase_errors("{}") == {}
    assert FL.run_phase_errors(None) == {}
    assert FL.run_phase_errors("not json") == {}
    assert FL.RUN_ERROR_DESKS == ("ARCHER",)


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


@pg
async def test_loop_health_read_carries_the_heartbeat_phase_errors():
    """Real Postgres: the production-shaped agent_archer beat ('ok', detail
    phase_errors {results: TimeoutError}) reads DEGRADED through LH.read;
    the same beat with phase_errors {} reads HEALTHY. Runs on the test's own
    event loop (pytest-asyncio), never a second loop of its own."""
    import asyncpg

    async def one(pe):
        c = await asyncpg.connect(DSN)
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

    bad = await one({"results": "TimeoutError"})
    assert bad["status"] == LH.DEGRADED, bad
    assert bad["why"] == "LATEST_RUN_PHASE_ERRORS:results:TimeoutError"
    ok = await one({})
    assert ok["status"] == LH.HEALTHY, ok


def _stub_archer_phases(monkeypatch, results_error: dict):
    """Every phase of archer_runner.pass_once answers at once with nothing
    to do, except the results phase, which raises TimeoutError while
    results_error["on"] -- production's failure. The run, its summary, the
    agent heartbeat and the agent_archer service beat are archer_runner's
    own writes."""
    from sportsassets.agents import agent_work as AW
    from sportsassets.agents import archer as E
    from sportsassets.agents import pos_workflow as W

    async def hist(conn, **kw):
        return {"stub": True}

    async def none_(conn, **kw):
        return []

    async def outcomes(conn, **kw):
        return {"recorded": []}

    async def sync_for(conn, *a, **kw):
        return {}

    async def assemble(conn, decision_id, **kw):
        return {"ok": True, "written": False}

    async def attach(conn, **kw):
        if results_error["on"]:
            raise TimeoutError
        return {"attached": 0}

    monkeypatch.setattr(E, "history_stats", hist)
    monkeypatch.setattr(E, "candidates", none_)
    monkeypatch.setattr(E, "record_outcomes", outcomes)
    monkeypatch.setattr(AW, "sync_for", sync_for)
    monkeypatch.setattr(W, "assemble", assemble)
    monkeypatch.setattr(W, "attach_results", attach)


@pg
async def test_the_floor_desk_reads_archers_errored_runs_end_to_end(
        monkeypatch):
    """Real Postgres, archer_runner.pass_once's own writes: one clean pass,
    then three passes whose results phase times out. The floor desk reads
    WAITING 'DEGRADED: ...' (base: IDLE NO_NEW_CANDIDATE) and agrees with
    loop health (DEGRADED). A gate-hook error heartbeat (errors > runs)
    adds nothing. One clean pass after them clears the desk and the loop."""
    import asyncpg

    from sportsassets.agents import archer_runner as AR
    from sportsassets.agents import registry as R

    results_error = {"on": False}
    _stub_archer_phases(monkeypatch, results_error)
    t0 = time.time()
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await R.ensure_identities(conn)
        # this scenario owns Archer's runs and status row (agent_runs is
        # append-only: its trigger is bypassed inside this rolled-back
        # transaction only)
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("DELETE FROM agent_runs WHERE agent_id='ARCHER'")
        await conn.execute("SET LOCAL session_replication_role = origin")
        await conn.execute("DELETE FROM agent_status WHERE agent_id='ARCHER'")

        clean = await AR.pass_once(conn, now=t0 - 1500)
        assert clean["phase_errors"] == {}, clean
        results_error["on"] = True
        for k in (900, 600, 300):
            s = await AR.pass_once(conn, now=t0 - k)
            assert s["phase_errors"] == {"results": "TimeoutError"}, s
        # a gate hook raising: an error heartbeat with no run (errors > runs)
        await R.heartbeat(conn, R.ARCHER, state=R.S_IDLE,
                          activity="NO_NEW_CANDIDATE", now=t0 - 250,
                          error="TimeoutError")
        st = await conn.fetchrow(
            "SELECT runs, errors FROM agent_status WHERE agent_id='ARCHER'")
        assert (st["runs"], st["errors"]) == (4, 4)

        floor = await FL.build_floor(conn, now=t0)
        desk = next(a for a in floor["agents"] if a["agent"] == "ARCHER")
        assert desk["deployed"], desk
        assert desk["state"] == "WAITING", (desk["state"],
                                            desk["state_detail"])
        assert desk["state_detail"] == (
            "DEGRADED: newest finished run recorded phase errors "
            "results:TimeoutError · 3 of the last 4 finished runs errored")
        dg = desk["degraded"]
        assert dg["why"] == "LATEST_RUN_PHASE_ERRORS"
        assert dg["phase_errors"] == {"results": "TimeoutError"}
        assert dg["outcome"] == "NO_NEW_CANDIDATE"
        assert t0 - 301 <= dg["finished_at"] <= t0 - 200
        newest = await conn.fetchval(
            "SELECT run_id FROM agent_runs WHERE agent_id='ARCHER' "
            " AND finished_at IS NOT NULL ORDER BY started_at DESC LIMIT 1")
        assert dg["run_id"] == newest
        assert desk["state_basis"][0]["kind"] == "agent_runs"
        assert "runs errored" not in json.dumps(desk["status_row"])
        assert floor["sections"]["agent_runs.ARCHER"]["status"] == "OK"
        # only Archer's desk reads his runs; no other desk is degraded
        assert [a["agent"] for a in floor["agents"] if a["degraded"]] == [
            "ARCHER"]
        loop = next(lp for lp in (await LH.read(
            conn, now=time.time(), env={"ARCHER_RUNNER_ENABLED": "1"}))[
                "loops"] if lp["name"] == "agents.archer_runner")
        assert loop["status"] == LH.DEGRADED, loop
        assert loop["why"] == "LATEST_RUN_PHASE_ERRORS:results:TimeoutError"

        # the next pass is clean: the desk and the loop clear
        results_error["on"] = False
        await AR.pass_once(conn, now=t0 - 60)
        floor = await FL.build_floor(conn, now=t0)
        desk = next(a for a in floor["agents"] if a["agent"] == "ARCHER")
        assert desk["degraded"] is None, desk["degraded"]
        # IDLE NO_NEW_CANDIDATE, unless another test's recent estimate or
        # review edge in this shared database makes it a recent output
        assert desk["state"] != "WAITING", (desk["state"],
                                            desk["state_detail"])
        assert "DEGRADED" not in desk["state_detail"]
        if desk["activity_basis"] is None:
            assert (desk["state"], desk["state_detail"]) == (
                "IDLE", "NO_NEW_CANDIDATE")
        loop = next(lp for lp in (await LH.read(
            conn, now=time.time(), env={"ARCHER_RUNNER_ENABLED": "1"}))[
                "loops"] if lp["name"] == "agents.archer_runner")
        assert loop["status"] == LH.HEALTHY, loop
    finally:
        await tx.rollback()
        await conn.close()


def test_the_autonomy_block_counts_a_degraded_loop():
    """pos_os autonomy: a DEGRADED loop is expected to run, is neither alive
    (HEALTHY) nor stale (UNHEALTHY), and is counted in its own share."""
    from sportsassets.pos_os import autonomy as AU

    lb = AU.loops_block([{"loops": [
        {"name": "a", "status": "HEALTHY"},
        {"name": "agents.archer_runner", "status": "DEGRADED"},
        {"name": "c", "status": "UNHEALTHY"},
        {"name": "d", "status": "DISABLED"}]}])
    assert lb["expected_running"] == 3
    assert (lb["alive_share"], lb["stale_share"], lb["degraded_share"]) == (
        AU.C.share(1, 3), AU.C.share(1, 3), AU.C.share(1, 3))
    assert AU.loops_block([{"loops": [{"name": "a", "status": "HEALTHY"}]}])[
        "degraded_share"] == 0.0


def test_the_agent_status_contract_never_reads_a_degraded_loop_green():
    from sportsassets import agent_status_contract as A
    from tests import test_agent_status_contract as T

    v = T._lv("agents.archer_runner", "api", status=LH.DEGRADED,
              why="LATEST_RUN_PHASE_ERRORS:results:TimeoutError")
    got = T._loop("agents.archer_runner", "api", v)
    assert got["status"] == A.DEGRADED, got["status_reasons"]
    assert ("LOOP_DEGRADED:LATEST_RUN_PHASE_ERRORS:results:TimeoutError"
            in got["status_reasons"])
