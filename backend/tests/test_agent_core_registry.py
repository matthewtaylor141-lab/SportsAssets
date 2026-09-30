"""THE AGENT REGISTRY (migration 152): IDENTITIES, PERMISSIONS, HEARTBEATS,
TASKS, POLICY VERSIONS, THE DECISION INDEX AND THE READ API.

Against a migrated PostgreSQL database (RN1X_TEST_DSN). Controlled clocks:
every write passes `now=`. Stable ids throughout.
"""
from __future__ import annotations

import os
import pathlib

import pytest

from sportsassets.agents import registry as R

from tests import agents_core_harness as H

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

T0 = 1_790_100_000.0


async def _connect():
    import asyncpg
    return await asyncpg.connect(DSN)


# ════════════════════════════════════════════════════════════════════
# PERMISSIONS: EXPLICIT, AND NO AGENT HOLDS WHAT NONE MAY HOLD
# ════════════════════════════════════════════════════════════════════

def test_each_agent_holds_exactly_its_own_order_path_and_audrey_holds_none():
    assert R.permits(R.DEREK, "request.funded_entry")
    assert not R.permits(R.DEREK, "dispatch.xavier_claim")
    assert R.permits(R.XAVIER, "dispatch.xavier_claim")
    assert not R.permits(R.XAVIER, "request.funded_entry")
    for tool in ("request.funded_entry", "dispatch.xavier_claim",
                 "order.submit_direct", "order.cancel_direct", "deploy",
                 "write.risk_limits", "write.approvals",
                 "write.submission_switches"):
        assert not R.permits(R.AUDREY, tool), tool
    # AUDREY READS EVERYTHING and writes audits, tasks, directives, candidates
    for tool in ("read.funded_book", "read.catalogue", "read.xavier_records",
                 "write.agent_audits", "write.agent_tasks",
                 "write.directives", "write.policy_candidates"):
        assert R.permits(R.AUDREY, tool), tool
    # NOBODY holds risk limits, credentials, account authority, approvals,
    # deploy, the switches or a direct order call.
    for aid in R.AGENTS:
        for tool in R.NEVER_GRANTED:
            assert not R.permits(aid, tool), (aid, tool)
            assert tool in R.IDENTITIES[aid]["tool_permissions"]["denied"]
        perms = R.IDENTITIES[aid]["tool_permissions"]
        assert not set(perms["allowed"]) & set(perms["denied"]), aid
        assert set(perms["allowed"]) | set(perms["denied"]) <= set(R.TOOLS)
    assert R.IDENTITIES[R.AUDREY]["tool_permissions"]["order_path"] is None
    assert not R.permits("NOBODY", "read.all")


@pg
@pytest.mark.asyncio
async def test_identities_and_permissions_are_persisted_with_versions():
    conn = await _connect()
    try:
        await H.clean_agents(conn)
        got = await R.ensure_identities(
            conn, code_version={"module": "w", "source_sha256_12": "abc123",
                                "build": None, "pid": 1})
        assert got["ok"] is True, got
        rows = {r["agent_id"]: dict(r) for r in await conn.fetch(
            "SELECT * FROM agent_identities")}
        assert set(rows) == {"DEREK", "XAVIER", "AUDREY"}
        for aid, row in rows.items():
            perms = R._j(row["tool_permissions"])
            assert perms == R.IDENTITIES[aid]["tool_permissions"]
            assert row["code_version"] == "w@abc123"
            assert row["policy_version"] == R.SOURCE_CODE_DEFAULT
            assert row["model_version"]
            assert row["mandate"] == R.IDENTITIES[aid]["mandate"]
        # A NEVER-RUN AGENT SAYS SO; it does not inherit a success.
        for aid in R.AGENTS:
            st = await H.status(conn, aid)
            assert st["state"] == "IDLE" and st["activity"] == "NOT_YET_RUN"
            assert st["runs"] == 0 and st["last_heartbeat_at"] is None
        # AN ACTIVE POLICY NAMES ITSELF IN THE IDENTITY'S VERSION
        await conn.execute(
            "INSERT INTO agent_policy_versions (agent_id, policy_key, "
            " version, params, state, created_by, approved_by, approved_at)"
            " VALUES ('DEREK','entry','v7','{}'::jsonb,'ACTIVE','t','OWNER',"
            " now())")
        await R.ensure_identities(conn, code_version=None)
        row = await conn.fetchrow(
            "SELECT policy_version, code_version FROM agent_identities "
            " WHERE agent_id='DEREK'")
        assert row["policy_version"] == "entry@v7"
        assert row["code_version"] == "w@abc123", "None keeps the known one"
        st = await R.status_of(conn, R.DEREK)
        assert st["role"] == "DISCOVERY_AND_ENTRY"
        assert st["tool_permissions"]["order_path"] == "request.funded_entry"
    finally:
        await H.clean_agents(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# HEARTBEAT: NEVER RAISES, COUNTS RUNS AND ERRORS
# ════════════════════════════════════════════════════════════════════

class _Broken:
    def transaction(self):
        raise ConnectionError("the database went away (test)")

    async def execute(self, *a):
        raise ConnectionError("the database went away (test)")


@pytest.mark.asyncio
async def test_heartbeat_never_raises_on_any_input():
    for conn in (object(), _Broken(), None):
        got = await R.heartbeat(conn, R.XAVIER, state=R.S_IDLE, now=T0)
        assert got["ok"] is False and got["refusal"] == R.R_WRITE_FAILED
    assert (await R.heartbeat(object(), "NOBODY", state=R.S_IDLE))[
        "refusal"] == R.R_UNKNOWN_AGENT
    assert (await R.heartbeat(object(), R.DEREK, state="HAPPY"))[
        "refusal"] == R.R_UNKNOWN_STATE


@pg
@pytest.mark.asyncio
async def test_heartbeat_records_state_runs_and_errors_and_survives_a_closed_connection():
    conn = await _connect()
    try:
        await H.clean_agents(conn)
        # NO IDENTITY YET: the heartbeat registers the agent itself.
        got = await R.heartbeat(conn, R.XAVIER, state=R.S_EVALUATING,
                                activity="PASS", now=T0,
                                run={"started_at": T0})
        assert got["ok"] is True, got
        await R.heartbeat(conn, R.XAVIER, state=R.S_FAILED,
                          activity="SERVICING_RAISED", now=T0 + 5,
                          error="RuntimeError: boom",
                          run={"finished_at": T0 + 5, "elapsed_s": 5.0})
        st = await H.status(conn, "XAVIER")
        assert st["state"] == "FAILED" and st["runs"] == 1
        assert st["errors"] == 1 and "RuntimeError" in st["last_error"]
        assert st["last_heartbeat_at"].timestamp() == pytest.approx(T0 + 5)
        assert float(st["last_run_elapsed_s"]) == 5.0
        # INSIDE A CALLER'S TRANSACTION, a failing heartbeat cannot abort it
        async with conn.transaction():
            await conn.execute("CREATE TEMP TABLE hb_probe (x int)")
            bad = await R.heartbeat(conn, R.XAVIER, state=R.S_IDLE,
                                    now=T0 + 6, waiting_on={"x": object()})
            await conn.execute("INSERT INTO hb_probe VALUES (1)")
        assert await conn.fetchval("SELECT count(*) FROM hb_probe") == 1
        assert bad["ok"] is True       # default=str serialises the object
        c2 = await _connect()
        await c2.close()
        closed = await R.heartbeat(c2, R.XAVIER, state=R.S_IDLE, now=T0)
        assert closed["ok"] is False
    finally:
        await H.clean_agents(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_run_is_finished_once_and_never_deleted():
    import asyncpg
    conn = await _connect()
    try:
        await H.clean_agents(conn)
        assert (await R.start_run(conn, R.AUDREY, "run-reg-1", now=T0,
                                  summary={"a": 1}))["created"] is True
        assert (await R.start_run(conn, R.AUDREY, "run-reg-1",
                                  now=T0))["created"] is False
        assert (await R.finish_run(conn, R.AUDREY, "run-reg-1", outcome="OK",
                                   now=T0 + 1))["finished"] is True
        assert (await R.finish_run(conn, R.AUDREY, "run-reg-1", outcome="X",
                                   now=T0 + 2))["finished"] is False
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute("UPDATE agent_runs SET outcome='Y' "
                               " WHERE run_id='run-reg-1'")
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute("DELETE FROM agent_runs")
        assert await conn.fetchval(
            "SELECT outcome FROM agent_runs WHERE run_id='run-reg-1'") == "OK"
    finally:
        await H.clean_agents(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# TASKS: IDEMPOTENT, WITH AN APPEND-ONLY HISTORY
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_task_creation_is_idempotent_and_its_history_append_only():
    import asyncpg
    conn = await _connect()
    try:
        await H.clean_agents(conn)
        kw = dict(assignee="audrey", created_by="OWNER", kind="INVESTIGATE",
                  title="why did Derek skip 12 entries", spec={"n": 12},
                  directive_id="dir-1", task_id="task-reg-1", now=T0)
        one = await R.create_task(conn, **kw)
        two = await R.create_task(conn, **dict(kw, title="changed", now=T0 + 9))
        assert one["created"] is True and two["created"] is False
        assert two["task"]["title"] == "why did Derek skip 12 entries"
        assert two["task"]["assignee"] == "AUDREY"
        assert two["task"]["status"] == "OPEN"
        ev = await R.task_event(conn, "task-reg-1", kind="STARTED",
                                actor="AUDREY", detail={"x": 1},
                                status="IN_PROGRESS", now=T0 + 10)
        assert ev["ok"] and ev["status"] == "IN_PROGRESS"
        assert (await R.task_event(conn, "task-reg-1", kind="K", actor="A",
                                   detail={}, status="DONE"))[
            "refusal"] == R.R_UNKNOWN_STATUS
        assert (await R.task_event(conn, "task-none", kind="K", actor="A",
                                   detail={}))["refusal"] == R.R_NO_SUCH_TASK
        got = await R.task(conn, "task-reg-1")
        assert [e["kind"] for e in got["events"]] == ["CREATED", "STARTED"]
        assert got["events"][1]["detail"]["status_from"] == "OPEN"
        assert got["task"]["status"] == "IN_PROGRESS"
        assert got["task"]["evidence_links"][0]["href"].endswith(
            "/tasks/task-reg-1")
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute("UPDATE agent_task_events SET actor='X'")
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute("DELETE FROM agent_task_events")
        assert (await R.create_task(conn, **dict(kw, assignee="NOBODY")))[
            "refusal"] == R.R_UNKNOWN_AGENT
        listed = await R.tasks(conn, assignee="AUDREY")
        assert [t["task_id"] for t in listed] == ["task-reg-1"]
    finally:
        await H.clean_agents(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# POLICY VERSIONS: ONE ACTIVE PER KEY, AND THE DEFAULT SAYS IT IS ONE
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_one_active_policy_per_key_and_the_code_default_is_labelled():
    import asyncpg
    conn = await _connect()
    try:
        await H.clean_agents(conn)
        await R.ensure_identities(conn)
        dflt = await R.active_policy(conn, R.DEREK, "entry",
                                     default={"min_edge": 0.02})
        assert dflt["source"] == R.SOURCE_CODE_DEFAULT
        assert dflt["params"] == {"min_edge": 0.02}
        ins = ("INSERT INTO agent_policy_versions (agent_id, policy_key, "
               " version, params, state, created_by, approved_by, "
               " approved_at) VALUES ('DEREK','entry',$1,$2::jsonb,$3,'t',"
               " $4, CASE WHEN $4::text IS NULL THEN NULL ELSE now() END)")
        await conn.execute(ins, "v1", '{"min_edge": 0.03}', "ACTIVE", "OWNER")
        await conn.execute(ins, "v2", '{"min_edge": 0.05}', "CANDIDATE", None)
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(ins, "v3", "{}", "ACTIVE", "OWNER")
        # AN ACTIVE POLICY MUST NAME ITS APPROVER
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "UPDATE agent_policy_versions SET state='ACTIVE' "
                " WHERE version='v2' AND approved_by IS NULL")
        got = await R.active_policy(conn, R.DEREK, "entry", default={})
        assert got["source"] == R.SOURCE_ACTIVE_POLICY
        assert got["version"] == "v1" and got["params"] == {"min_edge": 0.03}
        assert got["approved_by"] == "OWNER"
        # ANOTHER KEY, ANOTHER AGENT: independent
        await conn.execute(
            "INSERT INTO agent_policy_versions (agent_id, policy_key, version,"
            " state, created_by, approved_by, approved_at) VALUES "
            " ('DEREK','coverage','v1','ACTIVE','t','OWNER',now()),"
            " ('XAVIER','entry','v1','ACTIVE','t','OWNER',now())")
        # A FAILED READ FALLS BACK, LABELLED
        c2 = await _connect()
        await c2.close()
        down = await R.active_policy(c2, R.DEREK, "entry", default={"a": 1})
        assert down["source"] == R.SOURCE_CODE_DEFAULT and down["read_error"]
    finally:
        await H.clean_agents(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_decision_index_is_idempotent_and_links_evidence():
    conn = await _connect()
    try:
        await H.clean_agents(conn)
        kw = dict(agent_id="derek", kind="ENTRY_VERDICT", subject="aec-x",
                  decided_at=T0, verdict="SKIP", summary={"why": "edge"},
                  evidence_refs=[{"kind": "external_valuations", "id": "7",
                                  "href": "/api/command/x"}])
        a = await R.link_decision(conn, **kw)
        b = await R.link_decision(conn, **kw)
        assert a["created"] is True and b["created"] is False
        assert a["decision_ref"] == b["decision_ref"]
        assert (await R.link_decision(conn, **dict(kw, evidence_refs="7")))[
            "refusal"] == R.R_EVIDENCE_NOT_A_LIST
        rows = await R.decisions(conn, agent_id="DEREK")
        assert len(rows) == 1 and rows[0]["evidence"][0]["id"] == "7"
        assert await R.decisions(conn, agent_id="XAVIER") == []
    finally:
        await H.clean_agents(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# THE MIGRATION AND ITS ROLLBACK
# ════════════════════════════════════════════════════════════════════

MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"


@pg
@pytest.mark.asyncio
async def test_the_migration_is_idempotent_and_its_rollback_refuses_over_rows():
    import asyncpg
    conn = await _connect()
    try:
        await H.clean_agents(conn)
        up = (MIG / "152_agent_core.sql").read_text()
        await conn.execute(up)            # a re-run is a no-op
        await R.ensure_identities(conn)
        down = (MIG / "rollback" / "152_agent_core.down.sql").read_text()
        tr = conn.transaction()
        await tr.start()
        try:
            with pytest.raises(asyncpg.RaiseError) as e:
                await conn.execute(down)
            assert "not rolled back" in str(e.value)
        finally:
            await tr.rollback()
        assert await conn.fetchval("SELECT count(*) FROM agent_identities") \
            == 3
    finally:
        await H.clean_agents(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# THE READ API
# ════════════════════════════════════════════════════════════════════

def test_the_agent_routes_require_the_command_credential():
    from fastapi.testclient import TestClient
    from sportsassets.api import app as A

    c = TestClient(A.app)
    for path in ("/api/command/agents", "/api/command/agents/tasks",
                 "/api/command/agents/tasks/t1",
                 "/api/command/agents/handoffs",
                 "/api/command/agents/decisions?agent=derek"):
        assert c.get(path).status_code == 401, path


@pg
@pytest.mark.asyncio
async def test_the_index_and_readers_are_truthful_about_empty_and_present(
        monkeypatch):
    from fastapi import Response
    from sportsassets.api import agents_core as AC

    conn = await _connect()
    try:
        await H.clean_agents(conn)
        got = await AC.agents_index(Response())
        assert [a["agent_id"] for a in got["agents"]] == list(R.AGENTS)
        assert all(a["state"] is None and a["why"] for a in got["agents"])
        assert got["handoffs"]["status"] == "EMPTY"
        assert got["handoffs"]["why"]
        assert got["tasks"]["status"] == "EMPTY" and got["read_only"] is True
        await R.ensure_identities(conn)
        await R.create_task(conn, assignee=R.XAVIER, created_by="OWNER",
                            kind="REVIEW", title="t", spec={},
                            task_id="task-api-1", now=T0)
        got = await AC.agents_index(Response())
        assert {a["state"] for a in got["agents"]} == {"IDLE"}
        assert got["tasks"]["status"] == "OK"
        assert got["tasks"]["evidence"][0]["id"] == "task-api-1"
        one = await AC.agents_task("task-api-1", Response())
        assert one["events"][0]["kind"] == "CREATED"
        with pytest.raises(Exception) as e:
            await AC.agents_task("task-none", Response())
        assert getattr(e.value, "status_code", None) == 404
        dec = await AC.agents_decisions(Response(), agent="xavier", limit=5)
        assert dec["decisions"]["status"] == "EMPTY"
        with pytest.raises(Exception) as e:
            await AC.agents_decisions(Response(), agent="bob", limit=5)
        assert getattr(e.value, "status_code", None) == 400

        # A FAILED READ IS UNAVAILABLE, NEVER EMPTY
        from sportsassets.agents import handoff as AH

        async def _boom(*a, **k):
            raise RuntimeError("read failed (test)")
        monkeypatch.setattr(AH, "handoffs", _boom)
        got = await AC.agents_handoffs(Response(), entry_intent_id=None,
                                       limit=5)
        assert got["handoffs"]["status"] == "UNAVAILABLE"
        assert got["handoffs"]["why"] == "RuntimeError"
    finally:
        await H.clean_agents(conn)
        await conn.close()
        from sportsassets import db
        await db.close_pool()
