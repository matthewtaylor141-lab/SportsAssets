"""KAREN (migration 207): A FIRST-CLASS AGENT IDENTITY WITH NO AUTHORITY.

  * the registry holds KAREN (display "Karen", role RED_TEAM_CHALLENGE, a
    mandate) with an explicit allow list (read.* evidence tools,
    write.challenges, write.agent_tasks) and an explicit DENY list naming
    every order, dispatch, limit, credential, account, approval, switch,
    deploy, policy-candidate, activation and promotion tool;
  * in code: registry.permits / karen.may refuse every one of them and
    karen.refuse_authority answers any such request with a named refusal;
    karen.py and karen_runner.py import no order / venue / execution path;
  * in the database: every approval / activation / promotion / release /
    control / decision-of-record table refuses Karen as the acting identity
    (a trigger), the task history refuses Karen moving a task to an approval
    or release state, and the collaboration loop admits Karen ONLY at the
    PEER_CHALLENGE stage.

Against a migrated PostgreSQL database (RN1X_TEST_DSN) where marked; each
database test runs in one transaction that is rolled back.
"""
from __future__ import annotations

import ast
import os
import pathlib

import pytest

from sportsassets.agents import karen as K
from sportsassets.agents import registry as R

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "migrations"
UP = (MIG / "207_karen_red_team.sql").read_text()
DOWN = (MIG / "rollback" / "207_karen_red_team.down.sql").read_text()

DENIED_BY_SPEC = (
    "order.submit_direct", "order.cancel_direct", "request.funded_entry",
    "dispatch.xavier_claim", "write.risk_limits", "write.credentials",
    "write.account_authority", "write.approvals",
    "write.submission_switches", "deploy", "write.policy_candidates",
    "write.policy_activation", "promotion")


# ════════════════════════════════════════════════════════════════════
# IDENTITY AND PERMISSIONS (code)
# ════════════════════════════════════════════════════════════════════

def test_karen_is_a_registered_identity_with_a_mandate():
    assert R.KAREN == "KAREN" and R.KAREN in R.AGENTS
    assert R.KAREN not in R.OPERATING_AGENTS
    ident = R.IDENTITIES[R.KAREN]
    assert ident["display_name"] == "Karen"
    assert ident["role"] == "RED_TEAM_CHALLENGE"
    assert "Challenge" in ident["mandate"] and "NO authority" in \
        ident["mandate"]
    perms = ident["tool_permissions"]
    assert perms["order_path"] is None
    assert not set(perms["allowed"]) & set(perms["denied"])
    assert set(perms["allowed"]) | set(perms["denied"]) <= set(R.TOOLS)


def test_karen_may_read_evidence_and_write_challenges_and_tasks_only():
    allowed = R.IDENTITIES[R.KAREN]["tool_permissions"]["allowed"]
    for tool in allowed:
        assert tool.startswith("read.") or tool in ("write.challenges",
                                                    "write.agent_tasks"), tool
        assert R.permits(R.KAREN, tool) and K.may(tool), tool
    for tool in ("read.decisions", "read.intents", "read.reviews",
                 "read.reconciliations", "read.valuations", "read.audits"):
        assert tool in allowed, tool
    # no blanket read.all: each evidence read is named
    assert not R.permits(R.KAREN, "read.all")
    assert not R.permits(R.KAREN, "read.credentials_vault")


def test_every_authority_is_denied_explicitly_and_refused():
    denied = R.IDENTITIES[R.KAREN]["tool_permissions"]["denied"]
    for tool in DENIED_BY_SPEC:
        assert tool in denied, tool
        assert not R.permits(R.KAREN, tool), tool
        assert not K.may(tool), tool
        with pytest.raises(K.KarenHasNoAuthority):
            K.assert_may(tool)
        got = K.refuse_authority(tool)
        assert got["ok"] is False and got["refusal"] == K.R_NO_AUTHORITY
    # dispatch.* / order.* / request.* wholesale, even names not yet invented
    for tool in ("dispatch.anything", "order.amend_direct",
                 "request.funded_exit"):
        assert not K.may(tool), tool
    # every never-granted tool is on every agent's deny list, Karen included
    for aid in R.AGENTS:
        for tool in R.NEVER_GRANTED:
            assert not R.permits(aid, tool), (aid, tool)
            assert tool in R.IDENTITIES[aid]["tool_permissions"]["denied"]
    # ...and nobody holds activation or promotion
    for aid in R.AGENTS:
        assert not R.permits(aid, "write.policy_activation")
        assert not R.permits(aid, "promotion")


def test_is_karen_matches_her_machine_identities_not_a_person_named_karen():
    for a in ("KAREN", "karen", " Karen ", "agent:karen", "slack:karen",
              "karen-bot", "Karen red team", "AGENTS/KAREN"):
        assert K.is_karen(a), a
    for a in ("Karen Smith", "OWNER", "DEREK", "", None, 7, "karenina"):
        assert not K.is_karen(a), a


_ORDER_MODULES = ("venue", "kalshi", "clob", "live_executor", "execution",
                  "bettor_funded_execution", "submission", "execmirror",
                  "bettor_xavier", "workers", "pmus", "pmx", "edge_gate")


@pytest.mark.parametrize("name", ["karen.py", "karen_runner.py"])
def test_karen_modules_import_no_order_or_venue_path(name):
    src = (ROOT / "sportsassets" / "agents" / name).read_text()
    tree = ast.parse(src)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    for mod in imported:
        for bad in _ORDER_MODULES:
            assert bad not in mod, (name, mod)
    low = src.lower()
    import re
    written = set(re.findall(r"(?:insert into|update|delete from)\s+"
                             r"([a-z_]+)", low)) - {"of"}       # FOR UPDATE OF
    assert written <= {"karen_challenges", "karen_challenge_events"}, written
    # it READS governance records (policy candidates, artifacts) to
    # challenge them; it never writes them (the written set above) and never
    # sends a message itself
    for forbidden in ("chat.postmessage", "paper_control", "httpx"):
        assert forbidden not in low, forbidden


# ════════════════════════════════════════════════════════════════════
# THE DATABASE (migration 207)
# ════════════════════════════════════════════════════════════════════

async def _tx():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    return conn, tx


async def _expect(conn, sql, *args, match=None):
    import asyncpg
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(asyncpg.PostgresError) as e:
            await conn.execute(sql, *args)
        if match:
            assert match in str(e.value), str(e.value)
    finally:
        await sp.rollback()


@pg
@pytest.mark.asyncio
async def test_the_identity_is_persisted_with_its_permissions():
    conn, tx = await _tx()
    try:
        await conn.execute(UP)                                # idempotent
        got = await R.ensure_identities(conn)
        assert got["ok"] is True and got["agents"]["KAREN"]["ok"], got
        st = await R.status_of(conn, R.KAREN)
        assert st["display_name"] == "Karen"
        assert st["role"] == "RED_TEAM_CHALLENGE"
        assert st["tool_permissions"] == \
            R.IDENTITIES[R.KAREN]["tool_permissions"]
        assert st["state"] == "IDLE" and st["activity"] == "NOT_YET_RUN"
        # the CHECK admits exactly the four agents
        await _expect(conn, "INSERT INTO agent_identities (agent_id, "
                      " display_name, mandate) VALUES ('MALLORY','m','m')")
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_every_approval_activation_promotion_and_control_table_refuses_karen():
    conn, tx = await _tx()
    try:
        guarded = {r["tbl"]: list(r["cols"]) for r in await conn.fetch(
            "SELECT * FROM karen_authority_guarded_tables()")}
        for must in ("agent_policy_versions", "agent_policy_artifacts",
                     "live_rule_artifacts", "improvement_candidates",
                     "improvement_releases", "paper_improvement_proposals",
                     "paper_policy_parameter_activations",
                     "paper_policy_parameter_versions", "bettor_funded_models",
                     "execmirror_control", "kalshi_smalllive_control",
                     "agent_slack_control_audit", "derek_entry_decisions"):
            assert must in guarded, must
        # the trigger is attached to every guarded table this database has,
        # with that table's acting-identity columns
        for tbl, cols in guarded.items():
            if await conn.fetchval("SELECT to_regclass($1)", tbl) is None:
                continue
            row = await conn.fetchrow(
                "SELECT tgenabled, pg_get_triggerdef(t.oid) AS d "
                "  FROM pg_trigger t WHERE tgrelid=$1::regclass "
                "   AND tgname='karen_no_authority_trg'", tbl)
            assert row is not None, tbl
            assert row["tgenabled"] in ("O", b"O"), tbl
            for c in cols:
                assert "'%s'" % c in row["d"], (tbl, c)
                exists = await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns WHERE "
                    " table_name=$1 AND column_name=$2", tbl, c)
                assert exists, (tbl, c)
        # FUNCTIONALLY, on real tables
        await R.ensure_identities(conn)
        for col in ("created_by", "approved_by"):
            await _expect(
                conn, "INSERT INTO agent_policy_versions (agent_id, "
                " policy_key, version, state, created_by, approved_by, "
                " approved_at) VALUES ('DEREK','k207','v1','CANDIDATE',$1,$2,"
                " now())",
                "KAREN" if col == "created_by" else "OWNER",
                "agent:karen" if col == "approved_by" else None,
                match="KAREN_HAS_NO_AUTHORITY")
        await conn.execute(
            "INSERT INTO agent_policy_versions (agent_id, policy_key, "
            " version, state, created_by) VALUES "
            " ('DEREK','k207','v1','CANDIDATE','AUDREY')")
        await _expect(conn, "UPDATE agent_policy_versions SET "
                      " state='ACTIVE', approved_by='Karen', approved_at=now()"
                      " WHERE policy_key='k207'",
                      match="KAREN_HAS_NO_AUTHORITY")
        await _expect(conn, "INSERT INTO agent_slack_control_audit (enabled,"
                      " actor) VALUES (true, 'karen')",
                      match="KAREN_HAS_NO_AUTHORITY")
        # a person named Karen is a person, not the agent
        await conn.execute("INSERT INTO agent_slack_control_audit (enabled, "
                           " actor) VALUES (false, 'Karen Smith')")
        for tbl in ("execmirror_control", "kalshi_smalllive_control"):
            if await conn.fetchval("SELECT count(*) FROM %s" % tbl):
                await _expect(conn, "UPDATE %s SET actor='KAREN'" % tbl,
                              match="KAREN_HAS_NO_AUTHORITY")
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_karen_cannot_move_a_task_to_approval_or_release():
    conn, tx = await _tx()
    try:
        await R.ensure_identities(conn)
        got = await R.create_task(conn, assignee=R.DEREK, created_by="KAREN",
                                  kind="CHALLENGE_FOLLOWUP", title="t",
                                  spec={}, task_id="task-k207")
        assert got["ok"] and got["created"], got       # write.agent_tasks
        for status in ("APPROVAL_READY", "APPROVED", "RELEASED",
                       "ROLLED_BACK"):
            res = await R.task_event(conn, "task-k207", kind="MOVE",
                                     actor="KAREN", detail={}, status=status)
            assert res["ok"] is False, status
        ok = await R.task_event(conn, "task-k207", kind="NOTE",
                                actor="KAREN", detail={"note": "evidence"},
                                status="WAITING")
        assert ok["ok"] is True, ok
        ok = await R.task_event(conn, "task-k207", kind="MOVE",
                                actor="OWNER", detail={}, status="APPROVED")
        assert ok["ok"] is True, ok
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_in_the_loop_karen_may_only_record_a_peer_challenge():
    conn, tx = await _tx()
    try:
        await R.ensure_identities(conn)
        await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " decided_at) VALUES ('adr:k207-1','DEREK','T',now())")
        await conn.execute(
            "INSERT INTO agent_findings (finding_id, proposer, title, "
            " statement, evidence_refs, evidence_window_end, created_at, "
            " updated_at) VALUES ('f207','DEREK','t','s',"
            " '[{\"kind\":\"agent_decisions\",\"id\":\"adr:k207-1\"}]',"
            " now(), now(), now())")
        refs = '[{"kind":"agent_decisions","id":"adr:k207-1"}]'
        # Karen cannot propose a finding
        await _expect(conn, "INSERT INTO agent_findings (finding_id, "
                      " proposer, title, statement, evidence_refs, "
                      " evidence_window_end, created_at, updated_at) VALUES "
                      " ('f207k','KAREN','t','s',$1::jsonb,now(),now(),now())",
                      refs)
        # nor record EVIDENCE for anyone
        await _expect(conn, "INSERT INTO agent_finding_stages (finding_id, "
                      " seq, stage, actor, at, evidence_refs) VALUES "
                      " ('f207',1,'EVIDENCE','KAREN',now(),$1::jsonb)", refs)
        await conn.execute(
            "INSERT INTO agent_finding_stages (finding_id, seq, stage, actor,"
            " at, evidence_refs) VALUES ('f207',1,'EVIDENCE','DEREK',now(),"
            " $1::jsonb)", refs)
        await conn.execute(
            "INSERT INTO agent_finding_stages (finding_id, seq, stage, actor,"
            " at, evidence_refs, body) VALUES ('f207',2,'HYPOTHESIS','DEREK',"
            " now(), $1::jsonb, '{\"hypothesis\":\"h\"}')", refs)
        # ...but she may challenge
        await conn.execute(
            "INSERT INTO agent_finding_stages (finding_id, seq, stage, actor,"
            " at, evidence_refs, body, outcome) VALUES ('f207',3,"
            " 'PEER_CHALLENGE','KAREN',now(),$1::jsonb,"
            " '{\"challenge\":\"c\"}','SUSTAINED')", refs)
        for seq, stage, extra in (
                (4, "BOUNDED_EXPERIMENT", ""),
                (99, "CLOSED", "")):
            await _expect(conn, "INSERT INTO agent_finding_stages (finding_id,"
                          " seq, stage, actor, at, body) VALUES ('f207',$1,$2,"
                          " 'KAREN', now(), '{\"reason\":\"r\"}')", seq, stage)
        # the code says the same, by name
        from sportsassets.agents import collaboration_loop as CL
        for stage in (CL.EVIDENCE, CL.HYPOTHESIS, CL.BOUNDED_EXPERIMENT,
                      CL.CANDIDATE_IMPROVEMENT, CL.INDEPENDENT_EVALUATION,
                      CL.RELEASE_ELIGIBILITY, CL.CLOSED):
            assert CL.check_advance({"proposer": "DEREK", "stage": None,
                                     "evidence_refs": []}, [],
                                    {"stage": stage, "actor": "KAREN"}) == \
                CL.R_CHALLENGER_ONLY, stage
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_207_is_idempotent_and_its_rollback_refuses_over_karen_records():
    import asyncpg
    conn, tx = await _tx()
    try:
        await conn.execute(UP)
        await conn.execute(UP)
        await R.ensure_identities(conn)
        await R.start_run(conn, R.KAREN, "karen-run:k207-down")
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(DOWN)
        await sp.rollback()
        # with no Karen record, the rollback runs, restores three agents and
        # is itself idempotent; the migration re-applies
        await conn.execute("ALTER TABLE agent_runs DISABLE TRIGGER "
                           "agent_runs_append_only_trg")
        await conn.execute("DELETE FROM agent_runs WHERE agent_id='KAREN'")
        await conn.execute("ALTER TABLE agent_runs ENABLE TRIGGER "
                           "agent_runs_append_only_trg")
        await conn.execute(DOWN)
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT to_regclass('karen_challenges')") is None
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_trigger WHERE "
            " tgname='karen_no_authority_trg'") == 0
        await conn.execute(UP)
        assert await conn.fetchval(
            "SELECT to_regclass('karen_challenges')") is not None
    finally:
        await tx.rollback()
        await conn.close()


def test_karen_has_a_role_brief_scoped_and_bounded_like_the_others():
    from sportsassets.agents import role_brief as B
    assert "KAREN" in B.SQL and "KAREN" in B.FOCUS
    sql = B.SQL["KAREN"]
    assert "account_id=$1" in sql and "LIMIT 101" in sql and "BETWEEN" in sql
    assert "karen_challenges" in sql
    assert "never resolve your own challenge" in B.FOCUS["KAREN"]
    rows = [dict(challenge_id="kch:%d" % i, challenged_at=100,
                 target_agent="DEREK" if i % 2 else "XAVIER",
                 target_kind="agent_decisions", target_id=str(i),
                 detector="D", severity="LOW",
                 state="OPEN" if i < 3 else "UPHELD") for i in range(5)]
    got = B.summarize("KAREN", rows, 1000, "acct")
    assert got["authority"] == "NONE"
    assert got["by_state"] == {"OPEN": 3, "UPHELD": 2}
    assert got["by_target"] == {"XAVIER": 3, "DEREK": 2}
    assert got["source_ids"][0] == "kch:0"
    assert "not a proven defect" in got["limitation"]
    empty = B.summarize("KAREN", [], 1000, "acct")
    assert empty["status"] == "NO_RECORDED_EVIDENCE"
