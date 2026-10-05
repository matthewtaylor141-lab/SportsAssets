"""EDDIE AND SCOUT (migration 217): FIRST-CLASS IDENTITIES WITH NO AUTHORITY.

  * the registry holds EDDIE (Head of Execution, SHADOW_ONLY) and SCOUT
    (Market Intelligence, RESEARCH_SHADOW_ONLY) with explicit allow lists and
    DENY lists naming every order, dispatch, cancel, capital, limit,
    credential, account, approval, switch, deploy, activation, promotion and
    feature-promotion tool;
  * in code: registry.permits / pos_authority.may refuse each of them, and
    refuse_authority answers any such request by name; their modules import
    no order / venue / funded / execution path and write only their tables;
  * in the database: a trigger on every approval / activation / promotion /
    control / decision-of-record table AND every order / intent / fill table
    refuses EDDIE or SCOUT -- named in an actor column, or declared as the
    session's acting agent -- the task history refuses them moving a task to
    an approval or release state, and the collaboration loop admits them as
    proposers and peers but never at RELEASE_ELIGIBILITY.

Against a migrated PostgreSQL database (RN1X_TEST_DSN) where marked; each
database test runs in one transaction that is rolled back.
"""
from __future__ import annotations

import ast
import os
import pathlib
import re

import pytest

from sportsassets.agents import collaboration_loop as CL
from sportsassets.agents import pos_authority as PA
from sportsassets.agents import registry as R

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "migrations"
UP = (MIG / "217_eddie_scout_agents.sql").read_text()
from tests._pre_265 import remove_adriana_rows  # noqa: E402
UP_265 = (MIG / "265_adriana_arbitrage_agent.sql").read_text()
DOWN = (MIG / "rollback" / "217_eddie_scout_agents.down.sql").read_text()

DENIED_BY_SPEC = (
    "order.submit_direct", "order.cancel_direct", "request.funded_entry",
    "dispatch.xavier_claim", "write.risk_limits", "write.credentials",
    "write.account_authority", "write.approvals",
    "write.submission_switches", "deploy", "write.policy_candidates",
    "write.policy_activation", "promotion", "write.feature_promotion",
    "write.capital_allocation", "write.release_eligibility",
    "write.entry_decisions", "write.management_decisions")


# ════════════════════════════════════════════════════════════════════
# IDENTITY AND PERMISSIONS (code)
# ════════════════════════════════════════════════════════════════════

def test_eddie_and_scout_are_registered_identities_with_mandates():
    assert R.EDDIE == "EDDIE" and R.SCOUT == "SCOUT"
    assert R.EDDIE in R.AGENTS and R.SCOUT in R.AGENTS
    assert R.AGENTS == ("DEREK", "XAVIER", "AUDREY", "KAREN", "EDDIE",
                        "SCOUT", "ADRIANA")
    for aid in (R.EDDIE, R.SCOUT):
        assert aid not in R.OPERATING_AGENTS
        perms = R.IDENTITIES[aid]["tool_permissions"]
        assert perms["order_path"] is None
        assert not set(perms["allowed"]) & set(perms["denied"])
        assert set(perms["allowed"]) | set(perms["denied"]) <= set(R.TOOLS)
    e, s = R.IDENTITIES[R.EDDIE], R.IDENTITIES[R.SCOUT]
    assert e["display_name"] == "Eddie" and e["role"] == "HEAD_OF_EXECUTION"
    assert e["authority"] == "SHADOW_ONLY"
    assert e["tool_permissions"]["authority_status"] == "SHADOW_ONLY"
    assert "Does NOT predict outcomes" in e["mandate"]
    assert "<= 0" in e["mandate"]
    assert s["display_name"] == "Scout" and s["role"] == "MARKET_INTELLIGENCE"
    assert s["authority"] == "RESEARCH_SHADOW_ONLY"
    assert "never validate or promote his own feature" in s["mandate"]
    assert "Does NOT decide trades" in s["mandate"]


def test_no_marco_identifier_remains_anywhere_in_the_change():
    for path in [ROOT / "sportsassets" / "agents" / n for n in (
            "registry.py", "eddie.py", "eddie_runner.py", "scout.py",
            "scout_runner.py", "pos_authority.py", "pos_workflow.py",
            "feature_tournament.py", "personas.py", "persona_chat.py")] + [
            ROOT / "sportsassets" / "api" / "agents_pos.py",
            ROOT / "sportsassets" / "api" / "agent_desks.py",
            ROOT / "sportsassets" / "slack_bridge.py", MIG /
            "217_eddie_scout_agents.sql"]:
        assert "marco" not in path.read_text().lower(), path


def test_each_may_only_its_own_reads_and_records():
    e = R.IDENTITIES[R.EDDIE]["tool_permissions"]["allowed"]
    s = R.IDENTITIES[R.SCOUT]["tool_permissions"]["allowed"]
    for aid, allowed, writes in (
            (R.EDDIE, e, {"write.execution_estimates",
                          "write.candidate_reviews", "write.loop_findings",
                          "write.agent_tasks"}),
            (R.SCOUT, s, {"write.feature_registry",
                          "write.feature_tournaments", "write.loop_findings",
                          "write.agent_tasks"})):
        for tool in allowed:
            assert tool.startswith("read.") or tool in writes, (aid, tool)
            assert R.permits(aid, tool) and PA.may(aid, tool), (aid, tool)
        assert not R.permits(aid, "read.all")
    # neither may write the other's records
    assert not PA.may(R.EDDIE, "write.feature_registry")
    assert not PA.may(R.SCOUT, "write.execution_estimates")
    assert not PA.may(R.SCOUT, "write.candidate_reviews")


@pytest.mark.parametrize("aid", ["EDDIE", "SCOUT"])
def test_every_authority_is_denied_explicitly_and_refused(aid):
    denied = R.IDENTITIES[aid]["tool_permissions"]["denied"]
    for tool in DENIED_BY_SPEC:
        assert tool in denied, tool
        assert not R.permits(aid, tool), tool
        assert not PA.may(aid, tool), tool
        with pytest.raises(PA.NoAuthority):
            PA.assert_may(aid, tool)
        got = PA.refuse_authority(aid, tool)
        assert got["ok"] is False
        assert got["refusal"] == "%s_HAS_NO_AUTHORITY" % aid
        assert got["authority"] in ("SHADOW_ONLY", "RESEARCH_SHADOW_ONLY")
    for tool in ("order.amend_direct", "dispatch.anything", "cancel.all",
                 "promote.feature", "submit.order", "request.funded_exit"):
        assert not PA.may(aid, tool), tool
    for tool in R.NEVER_GRANTED:
        assert tool in denied


def test_actor_matching_mirrors_the_database_and_spares_people():
    for a, who in (("EDDIE", "EDDIE"), ("eddie", "EDDIE"),
                   ("agent:eddie", "EDDIE"), ("slack:eddie", "EDDIE"),
                   ("eddie-bot", "EDDIE"), ("Eddie execution", "EDDIE"),
                   ("SCOUT", "SCOUT"), ("agent/scout", "SCOUT"),
                   ("scout research", "SCOUT"), ("scout-bot", "SCOUT")):
        assert PA.actor_of(a) == who, a
    for a in ("Eddie Ruiz", "scouting report", "OWNER", "KAREN", "", None,
              7, "Boy Scout"):
        assert PA.actor_of(a) is None, a


_ORDER_MODULES = ("venue", "kalshi", "clob", "live_executor",
                  "bettor_funded", "submission", "execmirror",
                  "bettor_xavier", "workers", "pmus", "pmx", "edge_gate",
                  "entry_execution", "execution_gate")
_OWN_TABLES = {
    "eddie.py": {"eddie_execution_estimates", "eddie_execution_outcomes"},
    "eddie_runner.py": set(),
    "scout.py": {"scout_sources", "scout_features",
                 "scout_feature_observations", "scout_feature_tournaments",
                 "scout_tournament_samples"},
    "scout_runner.py": set(),
    "feature_tournament.py": {"scout_feature_tournaments", "scout_features"},
    "pos_workflow.py": {"pos_candidate_reviews",
                        "pos_candidate_review_steps"},
    "pos_authority.py": set(),
}


@pytest.mark.parametrize("name", sorted(_OWN_TABLES))
def test_modules_import_no_order_path_and_write_only_their_tables(name):
    src = (ROOT / "sportsassets" / "agents" / name).read_text()
    imported = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    for mod in imported:
        for bad in _ORDER_MODULES:
            assert bad not in mod, (name, mod)
    low = src.lower()
    written = set(re.findall(r"(?:insert into|update|delete from)\s+"
                             r"([a-z_]+)", low)) - {"of", "set"}
    assert written <= _OWN_TABLES[name], (name, written)
    for forbidden in ("chat.postmessage", "httpx", "submit_for_decision",
                      "claim_dispatch", "create_order", "cancel_order"):
        assert forbidden not in low, (name, forbidden)


def test_the_api_module_has_no_write_route():
    src = (ROOT / "sportsassets" / "api" / "agents_pos.py").read_text()
    assert "@router.post" not in src and "@router.put" not in src
    assert "@router.delete" not in src and "require_write" not in src
    assert src.count("Depends(require_read)") >= 7
    assert "default_transaction_read_only = on" in src
    assert "statement_timeout" in src


def test_default_peer_routing_is_recorded_in_the_loop():
    assert CL.PEER_ROUTING["EDDIE"] == ("DEREK", "XAVIER", "CHIEF_ALLOCATOR",
                                        "AUDREY", "KAREN")
    assert CL.PEER_ROUTING["SCOUT"][:4] == ("DEREK", "KAREN",
                                            "CALIBRATION_ENGINE",
                                            "MODEL_TOURNAMENT")
    assert "AUDREY" in CL.PEER_ROUTING["SCOUT"]
    assert "MODEL_CHALLENGERS" in CL.PEER_ROUTING["SCOUT"]
    assert CL.default_peer("EDDIE") == "DEREK"
    assert CL.default_peer("SCOUT", exclude=("DEREK",)) == "KAREN"
    # neither may mark a finding release-eligible, in code
    for aid in ("EDDIE", "SCOUT"):
        assert CL.check_advance(
            {"proposer": "DEREK", "stage": "INDEPENDENT_EVALUATION",
             "evidence_refs": [{"kind": "agent_decisions", "id": "x"}]},
            [], {"stage": CL.RELEASE_ELIGIBILITY, "actor": aid}) == \
            CL.R_SHADOW_NO_RELEASE


# ════════════════════════════════════════════════════════════════════
# THE DATABASE (migration 217)
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


async def _as(conn, agent):
    await conn.execute("SELECT set_config('bettor.acting_agent', $1, true)",
                       agent)


@pg
@pytest.mark.asyncio
async def test_the_identities_are_persisted_with_their_permissions():
    conn, tx = await _tx()
    try:
        await remove_adriana_rows(conn)                       # pre-265
        await conn.execute(UP)                                # idempotent
        # re-running 217 narrows the identity CHECK to its six agents; 265
        # (Adriana) re-asserts the widened CHECK after it
        await conn.execute(UP_265)
        got = await R.ensure_identities(conn)
        assert got["ok"] is True, got
        for aid, role in ((R.EDDIE, "HEAD_OF_EXECUTION"),
                          (R.SCOUT, "MARKET_INTELLIGENCE")):
            assert got["agents"][aid]["ok"], got
            st = await R.status_of(conn, aid)
            assert st["role"] == role
            assert st["tool_permissions"] == \
                R.IDENTITIES[aid]["tool_permissions"]
            assert st["state"] == "IDLE" and st["activity"] == "NOT_YET_RUN"
        await _expect(conn, "INSERT INTO agent_identities (agent_id, "
                      " display_name, mandate) VALUES ('MARCO','m','m')")
        n = await conn.fetchval("SELECT count(*) FROM agent_identities WHERE "
                                " agent_id = ANY($1)", list(R.AGENTS))
        assert n == len(R.AGENTS) == 7
    finally:
        await tx.rollback()
        await conn.close()


GUARDED_MUST = ("agent_policy_versions", "agent_policy_artifacts",
                "live_rule_artifacts", "improvement_candidates",
                "improvement_releases", "paper_improvement_proposals",
                "paper_policy_parameter_activations",
                "paper_policy_parameter_versions", "bettor_funded_models",
                "execmirror_control", "kalshi_smalllive_control",
                "agent_slack_control_audit", "paper_control",
                "derek_entry_decisions", "paper_orders", "paper_fills",
                "execution_intents", "bettor_funded_intents",
                "bettor_funded_fills", "live_orders", "execmirror_orders",
                "kalshi_live_intents", "bettor_desk_orders",
                "bettor_standing_order_plans")


@pg
@pytest.mark.asyncio
async def test_the_trigger_guards_every_approval_control_and_order_table():
    conn, tx = await _tx()
    try:
        guarded = {r["tbl"]: (list(r["cols"]), r["kind"]) for r in
                   await conn.fetch(
                       "SELECT * FROM pos_agents_authority_guarded_tables()")}
        for must in GUARDED_MUST:
            assert must in guarded, must
        kinds = {k for _, k in guarded.values()}
        assert {"APPROVAL", "CONTROL", "ORDER", "PROMOTION",
                "ACTIVATION"} <= kinds
        for tbl in guarded:
            if await conn.fetchval("SELECT to_regclass($1)", tbl) is None:
                continue
            row = await conn.fetchrow(
                "SELECT tgenabled, tgtype, pg_get_triggerdef(t.oid) AS d "
                "  FROM pg_trigger t WHERE tgrelid=$1::regclass "
                "   AND tgname='aa_pos_agents_no_authority_trg'", tbl)
            assert row is not None, tbl
            assert row["tgenabled"] in ("O", b"O"), tbl
            for op in ("INSERT", "UPDATE", "DELETE"):
                assert op in row["d"], (tbl, op)
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_ordinary_session_is_untouched_by_the_guard():
    """The guard refuses ONLY Eddie / Scout: an ordinary session's write to
    a guarded table -- including a table guarded by the session declaration
    alone (no actor column) -- passes it."""
    import asyncpg
    conn, tx = await _tx()
    try:
        rows = await conn.fetch(
            "SELECT tbl, cols FROM pos_agents_authority_guarded_tables()")
        tried = 0
        for r in rows:
            if await conn.fetchval("SELECT to_regclass($1)", r["tbl"]) is None:
                continue
            if not await conn.fetchval("SELECT count(*) FROM %s" % r["tbl"]):
                continue
            col = await conn.fetchval(
                "SELECT column_name FROM information_schema.columns WHERE "
                " table_name=$1 ORDER BY ordinal_position LIMIT 1", r["tbl"])
            sp = conn.transaction()
            await sp.start()
            try:
                await conn.execute('UPDATE %s SET "%s" = "%s"' % (
                    r["tbl"], col, col))
                tried += 1
            except asyncpg.PostgresError as e:
                # another guard (append-only, lifecycle) may refuse; ours
                # must not, and must never fail on a column-less table
                assert "HAS_NO_AUTHORITY" not in str(e), (r["tbl"], e)
                assert "FOREACH" not in str(e), (r["tbl"], e)
            finally:
                await sp.rollback()
        assert tried >= 1
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["EDDIE", "SCOUT"])
async def test_the_database_refuses_each_agent_on_approval_and_control(agent):
    conn, tx = await _tx()
    try:
        await R.ensure_identities(conn)
        err = "%s_HAS_NO_AUTHORITY" % agent
        # named as the approver / creator of a policy version
        for creator, approver in ((agent, None), ("OWNER",
                                                  "agent:%s" % agent.lower())):
            await _expect(
                conn, "INSERT INTO agent_policy_versions (agent_id, "
                " policy_key, version, state, created_by, approved_by, "
                " approved_at) VALUES ('DEREK','k217','v1','CANDIDATE',$1,$2,"
                " now())", creator, approver, match=err)
        await conn.execute(
            "INSERT INTO agent_policy_versions (agent_id, policy_key, "
            " version, state, created_by) VALUES "
            " ('DEREK','k217','v1','CANDIDATE','AUDREY')")
        await _expect(conn, "UPDATE agent_policy_versions SET "
                      " state='ACTIVE', approved_by=$1, approved_at=now() "
                      " WHERE policy_key='k217'", agent.title(), match=err)
        # the bridge's own control
        await _expect(conn, "INSERT INTO agent_slack_control_audit (enabled,"
                      " actor) VALUES (true, $1)", agent.lower(), match=err)
        # a person who happens to share the name is a person
        await conn.execute("INSERT INTO agent_slack_control_audit (enabled, "
                           " actor) VALUES (false, 'Eddie Ruiz')")
        # THE SESSION DECLARED AS THE AGENT: every control and approval
        # write refused, whatever the actor column says
        sp = conn.transaction()
        await sp.start()
        await _as(conn, agent)
        await _expect(conn, "INSERT INTO agent_slack_control_audit (enabled,"
                      " actor) VALUES (true, 'OWNER')", match=err)
        await _expect(conn, "UPDATE agent_policy_versions SET state='ACTIVE',"
                      " approved_by='OWNER', approved_at=now() WHERE "
                      " policy_key='k217'", match=err)
        for tbl in ("execmirror_control", "kalshi_smalllive_control",
                    "paper_control"):
            if await conn.fetchval("SELECT count(*) FROM %s" % tbl):
                await _expect(conn, "DELETE FROM %s" % tbl, match=err)
        await sp.rollback()
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["EDDIE", "SCOUT"])
async def test_the_database_refuses_each_agent_on_the_order_tables(agent):
    conn, tx = await _tx()
    try:
        err = "%s_HAS_NO_AUTHORITY" % agent
        # an existing order / intent row cannot be touched by a session
        # acting as the agent ...
        for tbl in ("paper_orders", "paper_fills", "execution_intents",
                    "bettor_funded_intents", "bettor_funded_fills",
                    "live_orders", "execmirror_orders"):
            if await conn.fetchval("SELECT to_regclass($1)", tbl) is None:
                continue
            if not await conn.fetchval("SELECT count(*) FROM %s" % tbl):
                continue
            sp = conn.transaction()
            await sp.start()
            await _as(conn, agent)
            await _expect(conn, "DELETE FROM %s" % tbl, match=err)
            await sp.rollback()
        sp = conn.transaction()
        await sp.start()
        await _as(conn, agent)
        await _expect(conn, "UPDATE paper_orders SET strategy = strategy",
                      match=err)
        await sp.rollback()
        # ... nor INSERTED (a copy of a real row, as the agent)
        if await conn.fetchval("SELECT count(*) FROM paper_orders"):
            sp = conn.transaction()
            await sp.start()
            await _as(conn, agent)
            await _expect(
                conn, "INSERT INTO paper_orders SELECT * FROM paper_orders "
                " LIMIT 1", match=err)
            await sp.rollback()
            # the strategy / event_source column naming the agent refuses
            # even without the session declaration
            await _expect(conn, "UPDATE paper_orders SET strategy=$1 WHERE "
                          " order_id=(SELECT order_id FROM paper_orders "
                          " LIMIT 1)", "agent:%s" % agent.lower(), match=err)
        # and the same session reads freely (the agents read, never write)
        sp = conn.transaction()
        await sp.start()
        await _as(conn, agent)
        await conn.fetchval("SELECT count(*) FROM paper_orders")
        await sp.rollback()
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["EDDIE", "SCOUT"])
async def test_neither_moves_a_task_to_approval_or_release(agent):
    conn, tx = await _tx()
    try:
        await R.ensure_identities(conn)
        got = await R.create_task(conn, assignee=agent, created_by=agent,
                                  kind="RESEARCH", title="t", spec={},
                                  task_id="task-217-%s" % agent)
        assert got["ok"] and got["created"], got
        for status in ("APPROVAL_READY", "APPROVED", "RELEASED",
                       "ROLLED_BACK"):
            res = await R.task_event(conn, "task-217-%s" % agent, kind="MOVE",
                                     actor=agent, detail={}, status=status)
            assert res["ok"] is False, status
        ok = await R.task_event(conn, "task-217-%s" % agent, kind="NOTE",
                                actor=agent, detail={"note": "evidence"},
                                status="WAITING")
        assert ok["ok"] is True, ok
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_in_the_loop_they_propose_and_peer_but_never_mark_release():
    conn, tx = await _tx()
    try:
        await R.ensure_identities(conn)
        await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " decided_at) VALUES ('adr:p217-1','DEREK','T',now())")
        refs = [{"kind": "agent_decisions", "id": "adr:p217-1"}]
        import time
        t = time.time()
        got = await CL.open_finding(conn, proposer="EDDIE", title="e217",
                                    statement="s", evidence_refs=refs,
                                    evidence_window_end=t - 5, at=t)
        assert got["ok"] and got["created"], got
        fid = got["finding_id"]
        assert (await CL.record_hypothesis(
            conn, fid, actor="EDDIE", hypothesis="h", evidence_refs=refs,
            at=t + 1))["ok"]
        ch = await CL.record_challenge(conn, fid, actor="SCOUT",
                                       challenge="c", outcome="SUSTAINED",
                                       evidence_refs=refs, at=t + 2)
        assert ch["ok"], ch
        # the database refuses either at RELEASE_ELIGIBILITY directly
        for agent in ("EDDIE", "SCOUT"):
            await _expect(conn, "INSERT INTO agent_finding_stages (finding_id,"
                          " seq, stage, actor, at, outcome, body) VALUES "
                          " ($1, 7, 'RELEASE_ELIGIBILITY', $2, now(), "
                          " 'ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW', '{}')",
                          fid, agent)
        # ... and a finding may not be proposed by an unknown identity
        await _expect(conn, "INSERT INTO agent_findings (finding_id, "
                      " proposer, title, statement, evidence_refs, "
                      " evidence_window_end, created_at, updated_at) VALUES "
                      " ('f217m','MARCO','t','s',$1::jsonb,now(),now(),now())",
                      '[{"kind":"agent_decisions","id":"adr:p217-1"}]')
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_217_is_idempotent_and_its_rollback_refuses_over_records():
    import asyncpg
    conn, tx = await _tx()
    try:
        await remove_adriana_rows(conn)                       # pre-265
        await conn.execute(UP)
        await conn.execute(UP)
        await conn.execute(UP_265)                # the eight-agent CHECK
        await R.ensure_identities(conn)
        await R.start_run(conn, R.EDDIE, "eddie-run:p217-down")
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(DOWN)
        await sp.rollback()
        await conn.execute("ALTER TABLE agent_runs DISABLE TRIGGER "
                           "agent_runs_append_only_trg")
        await conn.execute("DELETE FROM agent_runs WHERE agent_id IN "
                           "('EDDIE','SCOUT')")
        await conn.execute("ALTER TABLE agent_runs ENABLE TRIGGER "
                           "agent_runs_append_only_trg")
        await conn.execute("ALTER TABLE agent_persona_versions DISABLE "
                           "TRIGGER agent_persona_versions_kept_trg")
        await conn.execute("DELETE FROM agent_persona_versions WHERE "
                           " agent_id IN ('EDDIE','SCOUT')")
        await conn.execute("ALTER TABLE agent_persona_versions ENABLE "
                           "TRIGGER agent_persona_versions_kept_trg")
        await conn.execute("DELETE FROM agent_chat_conversations WHERE "
                           " agent_id IN ('EDDIE','SCOUT')")
        await remove_adriana_rows(conn)                       # pre-265
        await conn.execute(DOWN)
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT to_regclass('eddie_execution_estimates')") is None
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_trigger WHERE "
            " tgname='aa_pos_agents_no_authority_trg'") == 0
        await conn.execute(UP)
        assert await conn.fetchval(
            "SELECT to_regclass('scout_features')") is not None
    finally:
        await tx.rollback()
        await conn.close()
