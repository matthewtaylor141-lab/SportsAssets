"""THE EXECUTION AGENT IS RENAMED: EDDIE -> ARCHER (migration 266).

  * ARCHER / archer / Archer / "Head of Execution" is the canonical identity
    and the ONLY active execution agent: EDDIE is in no agent list, holds no
    identity, seat, slug, voice, permission or runner, and the database
    refuses a new EDDIE identity (or any new EDDIE row) beside ARCHER;
  * EDDIE survives only as a HISTORICAL ALIAS: rows written as 'EDDIE' stay
    exactly as written (append-only, never updated or deleted) and every
    reader that shows one names ARCHER with an explicit
    historical_alias: "EDDIE";
  * migration 266 is idempotent, its seed is generated from the code, and its
    rollback refuses while ARCHER has left any record;
  * ARCHER holds ZERO submit / cancel / credential / capital authority --
    exactly the allow and deny lists EDDIE held, enforced in code and in the
    database, under either name.

Against a migrated PostgreSQL database (RN1X_TEST_DSN) where marked; each
database test runs in one transaction that is rolled back.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest
from fastapi import Response

from sportsassets import slack_bridge as S
from sportsassets.agents import collaboration_loop as CL
from sportsassets.agents import identity as I
from sportsassets.agents import improvement_stages as IS
from sportsassets.agents import pos_authority as PA
from sportsassets.agents import registry as R

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "migrations"
UP = (MIG / "266_archer_execution_agent_rename.sql").read_text()
DOWN = (MIG / "rollback"
        / "266_archer_execution_agent_rename.down.sql").read_text()

#: EDDIE's tool permissions exactly as the registry held them before 266
#: (literal, so this test needs no history): ARCHER holds these, no more.
EDDIE_ALLOWED_BEFORE_266 = [
    "read.decisions", "read.books", "read.orders_fills", "read.intents",
    "read.valuations", "read.allocations", "read.findings",
    "write.execution_estimates", "write.candidate_reviews",
    "write.loop_findings", "write.agent_tasks"]
EDDIE_DENIED_BEFORE_266 = [
    "request.funded_entry", "dispatch.xavier_claim", "write.entry_decisions",
    "write.management_decisions", "write.agent_audits", "write.directives",
    "write.policy_candidates", "write.challenges", "read.all",
    "write.feature_promotion", "write.capital_allocation",
    "write.release_eligibility", "write.feature_registry",
    "write.feature_tournaments", "order.submit_direct", "order.cancel_direct",
    "deploy", "write.risk_limits", "write.credentials",
    "write.account_authority", "write.approvals",
    "write.submission_switches", "write.policy_activation", "promotion"]
#: the four powers the directive names: none, under either name
ZERO = {"submit": ("order.submit_direct", "order.submit", "submit.order",
                   "write.submission_switches"),
        "cancel": ("order.cancel_direct", "cancel.order"),
        "credential": ("write.credentials", "write.account_authority"),
        "capital": ("write.capital_allocation", "write.risk_limits",
                    "request.funded_entry")}


# ════════════════════════════════════════════════════════════════════
# 1 · ONE CANONICAL EXECUTION AGENT
# ════════════════════════════════════════════════════════════════════

def test_archer_is_the_canonical_identity():
    assert R.ARCHER == "ARCHER" and not hasattr(R, "EDDIE")
    assert R.IDENTITIES[R.ARCHER]["display_name"] == "Archer"
    assert R.IDENTITIES[R.ARCHER]["role"] == "HEAD_OF_EXECUTION"
    assert I.SLUGS[R.ARCHER] == "archer" and I.BY_SLUG["archer"] == "ARCHER"
    row = I.identity_row("archer")
    assert I.validate_identity(row) is None
    assert row["display_name"] == "Archer"
    assert row["title"] == "Head of Execution"
    assert row["authority_status"] == "SHADOW_ONLY"
    assert row["approved_by"] == I.PENDING and row["approved_at"] is None
    assert row["source_directive"] == "PM_DIRECTIVE_2026-10-05_ARCHER"
    assert I.VOICE_SPEC["ARCHER"]["voice_profile_id"] == "vp-archer-v1"


def test_archer_is_the_only_active_execution_agent():
    """EDDIE is in no agent list anywhere; exactly one agent (ARCHER) holds
    the execution role, the seat, the slug and the runner."""
    from sportsassets import agent_work_state as WS
    from sportsassets.agents import personas as P
    from sportsassets.api import command_floor as F
    lists = {"registry": R.AGENTS, "shadow": R.SHADOW_AGENTS,
             "identity": I.AGENTS, "personas": P.AGENTS,
             "work_state": WS.AGENTS, "loop": CL.SHADOW_PARTICIPANTS,
             "improvement": IS.AGENTS, "owners": IS.OWNER_AGENTS,
             "pos_authority": PA.AGENTS,
             "slack": tuple(a.upper() for a in S.AGENTS),
             "floor": tuple(s["agent"] for s in F.SEATS)}
    for name, agents in lists.items():
        assert "ARCHER" in agents, name
        assert "EDDIE" not in agents, name
    execution = [a for a, d in R.IDENTITIES.items()
                 if d["role"] == "HEAD_OF_EXECUTION"]
    assert execution == ["ARCHER"]
    assert [a for a in I.AGENTS
            if I.IDENTITY_SPEC[a]["title"] == "Head of Execution"] == \
        ["ARCHER"]
    seats = [s for s in F.SEATS if "execution" in str(
        s.get("role") or s.get("title") or "").lower()]
    assert [s["slug"] for s in seats] == ["archer"]
    assert "eddie" not in I.BY_SLUG and "eddie" not in P.SLUG_TO_AGENT
    # the alias is not an agent: no identity, no voice, no permission
    assert I.agent_of("eddie") is None and I.agent_of("EDDIE") is None
    assert "EDDIE" not in R.IDENTITIES and "EDDIE" not in I.VOICE_SPEC
    with pytest.raises(ValueError):
        I.identity_row("eddie")


def test_eddie_is_one_historical_alias_named_the_same_everywhere():
    assert R.EDDIE_ALIAS == "EDDIE"
    assert R.HISTORICAL_ALIASES == {"EDDIE": "ARCHER"}
    # the modules that may not import the registry carry the same map
    assert CL.HISTORICAL_ALIASES == R.HISTORICAL_ALIASES
    assert IS.HISTORICAL_ALIASES == R.HISTORICAL_ALIASES
    from sportsassets.api import command_floor as F
    from sportsassets.api import command_twin as CT
    assert CT.TWIN_ALIASES == R.HISTORICAL_ALIASES
    assert {k.upper(): v.upper() for k, v in F.SEAT_ALIASES.items()} == \
        R.HISTORICAL_ALIASES
    assert I.HISTORICAL_ALIASES == R.HISTORICAL_ALIASES
    assert {k.upper(): v.upper() for k, v in S.HISTORICAL_ALIASES.items()} \
        == R.HISTORICAL_ALIASES
    assert R.canonical_agent_id("eddie") == R.canonical_agent_id(" EDDIE ") \
        == "ARCHER"
    assert R.canonical_agent_id("scout") == "SCOUT"
    assert R.canonical_agent_id("") is None
    assert R.historical_alias("Eddie") == "EDDIE"
    assert R.historical_alias("ARCHER") is None
    assert R.ids_with_aliases("archer") == ["ARCHER", "EDDIE"]
    assert R.ids_with_aliases("DEREK") == ["DEREK"]


def test_a_historical_row_is_labelled_never_silently_relabelled():
    row = {"task_id": "t1", "assignee": "EDDIE", "created_by": "DEREK",
           "status": "OPEN"}
    got = R.label_aliases(row)
    assert got["assignee"] == "ARCHER"
    assert got["historical_alias"] == "EDDIE"
    assert got["historical_alias_columns"] == ["assignee"]
    assert row["assignee"] == "EDDIE"                # the input is unchanged
    assert R.label_aliases({"agent": "eddie"})["agent"] == "archer"
    plain = {"agent_id": "ARCHER"}
    assert R.label_aliases(plain) == plain           # nothing to label
    assert "historical_alias" not in R.label_aliases({"agent_id": "SCOUT"})


def test_the_old_module_paths_are_thin_shims_of_the_new():
    from sportsassets.agents import archer, archer_runner, eddie, \
        eddie_runner
    assert eddie is archer and eddie_runner is archer_runner
    src = (ROOT / "sportsassets" / "agents" / "eddie.py").read_text()
    assert "sys.modules[__name__] = _archer" in src and len(
        src.splitlines()) < 15


# ════════════════════════════════════════════════════════════════════
# 2 · ZERO AUTHORITY, EXACTLY AS EDDIE HELD (code)
# ════════════════════════════════════════════════════════════════════

def test_archer_holds_exactly_the_permissions_eddie_held():
    perms = R.IDENTITIES[R.ARCHER]["tool_permissions"]
    assert perms["allowed"] == EDDIE_ALLOWED_BEFORE_266
    assert perms["denied"] == EDDIE_DENIED_BEFORE_266
    assert perms["authority_status"] == "SHADOW_ONLY"
    assert perms["order_path"] is None
    assert PA.AUTHORITY_STATUS[R.ARCHER] == "SHADOW_ONLY"


@pytest.mark.parametrize("power", sorted(ZERO))
def test_archer_has_zero_submit_cancel_credential_capital_authority(power):
    for name in ("ARCHER", "archer", "EDDIE", "eddie"):
        for tool in ZERO[power]:
            assert not R.permits(name, tool), (name, tool)
            assert not PA.may(name, tool), (name, tool)
            assert not I.permits(name, tool), (name, tool)
    with pytest.raises(PA.NoAuthority):
        PA.assert_may(R.ARCHER, ZERO[power][0])
    for t in R.NEVER_GRANTED + R.SHADOW_DENIED + PA.FORBIDDEN_ACTIONS:
        assert not PA.may(R.ARCHER, t), t


def test_the_alias_is_refused_as_archer_and_grants_nothing():
    # the alias holds nothing of its own, not even Archer's reads
    for tool in EDDIE_ALLOWED_BEFORE_266:
        assert PA.may(R.ARCHER, tool), tool
        assert not PA.may("EDDIE", tool) and not R.permits("EDDIE", tool)
    got = PA.refuse_authority("eddie", "order.submit_direct")
    assert got["refusal"] == "ARCHER_HAS_NO_AUTHORITY"
    assert got["historical_alias"] == "EDDIE"
    assert got["authority"] == "SHADOW_ONLY"
    assert PA.refuse_authority("archer", "x")["refusal"] == \
        "ARCHER_HAS_NO_AUTHORITY"
    # every acting-identity label 217 matched as EDDIE is still matched --
    # as ARCHER -- and a person of either name is not
    for label in ("EDDIE", "agent:eddie", "slack:eddie", "eddie-bot",
                  "Eddie Execution", "ARCHER", "agent:archer", "archer-bot",
                  "archer execution", "role/archer"):
        assert PA.actor_of(label) == "ARCHER", label
    for person in ("Eddie Ruiz", "Archer Smith", "Freddie", "archery"):
        assert PA.actor_of(person) is None, person


@pytest.mark.asyncio
async def test_new_acting_declarations_use_archer_never_the_alias():
    class _Conn:
        sql = None

        async def execute(self, sql, *a):
            _Conn.sql = (sql, a)
    await PA.act_as(_Conn(), "archer")
    assert _Conn.sql[1] == ("ARCHER",)
    with pytest.raises(ValueError):
        await PA.act_as(_Conn(), "EDDIE")


# ════════════════════════════════════════════════════════════════════
# 3 · HISTORY STAYS AS RECORDED (pure guards)
# ════════════════════════════════════════════════════════════════════

def test_a_historical_finding_or_item_is_read_only():
    f = {"finding_id": "f1", "proposer": "EDDIE", "stage": "EVIDENCE",
         "evidence_refs": [{"kind": "agent_decisions", "id": "x"}],
         "created_at": 1.0}
    stages = [{"seq": 1, "stage": "EVIDENCE", "actor": "EDDIE", "at": 1.0}]
    for actor in ("ARCHER", "KAREN", "DEREK"):
        assert CL.check_advance(f, stages, {
            "stage": "CLOSED", "actor": actor, "at": 2.0,
            "body": {"reason": "x"}}) == CL.R_HISTORICAL_ALIAS
    # a new row naming the alias is not an agent's
    assert CL.check_advance(dict(f, proposer="DEREK"), stages, {
        "stage": "HYPOTHESIS", "actor": "EDDIE", "at": 2.0}) == \
        CL.R_UNKNOWN_AGENT
    it = {"item_id": "impr:x", "owner_agent": "EDDIE", "stage": "EVIDENCE"}
    assert IS.check_event(it, [], {"stage": "HYPOTHESIS", "actor": "ARCHER",
                                   "actor_class": IS.OWNER_AGENT}) == \
        IS.R_HISTORICAL_ALIAS
    assert IS.next_required(it, [])["code"] == "NONE_HISTORICAL_ALIAS"
    # the alias stays a MACHINE, never a human approver or reviewer
    assert IS.is_machine_actor("EDDIE") and IS.is_machine_actor("ARCHER")
    assert IS.is_machine_actor("archer-bot")
    from sportsassets import canonical_intent as CI
    for robot in ("archer", "eddie", "Archer:x"):
        assert CI.is_named_human(robot) is False, robot


def test_the_refusal_codes_are_classified():
    from sportsassets import refusal_taxonomy_table as T
    for code in ("ARCHER_HAS_NO_AUTHORITY", "ARCHER_NOT_DEPLOYED",
                 "ARCHER_AND_SCOUT_NEVER_MARK_RELEASE_ELIGIBILITY",
                 CL.R_HISTORICAL_ALIAS, IS.R_HISTORICAL_ALIAS,
                 "EDDIE_IS_A_HISTORICAL_ALIAS",
                 # historical records keep their codes, still classified
                 "EDDIE_HAS_NO_AUTHORITY", "EDDIE_NOT_DEPLOYED",
                 "EDDIE_AND_SCOUT_NEVER_MARK_RELEASE_ELIGIBILITY"):
        assert code in T.TABLE, code
    assert T.TABLE["EDDIE_HAS_NO_AUTHORITY"] == \
        T.TABLE["ARCHER_HAS_NO_AUTHORITY"]


# ════════════════════════════════════════════════════════════════════
# 4 · THE MIGRATION, ITS SEED AND ITS ROLLBACK (files)
# ════════════════════════════════════════════════════════════════════

def test_266_is_the_next_migration_and_its_seed_is_generated_from_code():
    nums = sorted(int(p.name[:3]) for p in MIG.glob("*.sql"))
    assert nums[-1] == 266 and nums.count(266) == 1
    row = I.identity_row("ARCHER")
    assert "$q$%s$q$" % row["content_sha"] in UP
    assert "$q$%s$q$" % I.voice_sha(I.VOICE_SPEC["ARCHER"]) in UP
    assert "'PENDING_OWNER_APPROVAL', NULL" in UP
    assert "WHERE NOT EXISTS" in UP and "BEGIN;" not in UP
    # it never rewrites or deletes a historical row
    low = UP.lower()
    assert "update agent" not in low and "delete from" not in low
    assert "'eddie'" in low and "'archer'" in low
    # every agent-id CHECK and function 265 widened is widened again
    for name in ("agent_identities_agent_id_check",
                 "agent_persona_versions_agent_id_check",
                 "agent_chat_conversations_agent_id_check",
                 "agent_slack_delivery_agent_check",
                 "agent_findings_proposer_ck", "agent_finding_stages_actor_ck",
                 "agent_identity_agent_ck",
                 "agent_identity_authority_per_agent_ck",
                 "agent_voice_profiles_agent_ck", "agent_conv_agents_ck",
                 "agent_work_requests_agent_ck", "improve_items_owner_ck",
                 "twin_scorecards_agent_ck", "pos_steps_order_ck",
                 "FUNCTION pos_agent_actor", "FUNCTION poslearn_is_agent_actor",
                 "FUNCTION improve_is_machine_actor",
                 "FUNCTION live_parity_named_human",
                 "FUNCTION improve_events_guard"):
        assert name in UP, name
    assert "(agent_id = 'ARCHER' AND authority_status = 'SHADOW_ONLY')" in UP
    assert "rollback refused" in DOWN
    # Adriana's immutable version 1 (265) still equals the code
    adr = (MIG / "265_adriana_arbitrage_agent.sql").read_text()
    assert "$q$%s$q$" % I.identity_row("ADRIANA")["content_sha"] in adr


def test_the_persisted_storage_names_are_kept_on_purpose():
    """The rename never renames storage: the tables, view, columns and the
    codes that key history keep the names 217 / 218 / 219 / 221 gave them."""
    from sportsassets import canonical_components as CC
    from sportsassets.agents import archer as A
    from sportsassets.twin import engine as TE
    assert A.VERSION == "EDDIE_EXECUTION_ESTIMATOR_V1"
    assert "eddie_execution_estimates" in (
        ROOT / "sportsassets" / "agents" / "archer.py").read_text()
    assert TE.REQUIRES == {"EDDIE_EXECUTION": "ARCHER",
                           "SCOUT_FEATURE": "SCOUT"}
    assert '"eddie": eddie' in (ROOT / "sportsassets"
                                / "canonical_components.py").read_text()
    assert callable(CC.eddie_component)


# ════════════════════════════════════════════════════════════════════
# 5 · SLACK, RUNNER, VOICE, API
# ════════════════════════════════════════════════════════════════════

BASE = {"SLACK_TEAM_ID": "T1", "SLACK_ALLOWED_CHANNEL_IDS": "C1",
        "SLACK_MANAGEMENT_USER_IDS": "U1", "SLACK_WORKROOM_CHANNEL_ID": "C1"}


def _slack_env(monkeypatch, values: dict):
    for k, v in BASE.items():
        monkeypatch.setenv(k, v)
    for a in ("DEREK", "XAVIER", "AUDREY", "KAREN", "ARCHER", "EDDIE",
              "SCOUT", "ADRIANA"):
        for f in ("BOT_TOKEN", "SIGNING_SECRET", "APP_ID"):
            monkeypatch.delenv("SLACK_%s_%s" % (a, f), raising=False)
    for k, v in values.items():
        monkeypatch.setenv(k, v)


def test_slack_fails_closed_without_archers_credentials(monkeypatch):
    _slack_env(monkeypatch, {})
    assert "archer" in S.AGENTS and "eddie" not in S.AGENTS
    got = S.dedicated_identity("archer")
    assert got["ok"] is False and got["configured"] is False
    assert got["why"] == "ARCHER_SLACK_APP_NOT_CONFIGURED"
    assert S.impersonation({"agent": "archer", "source_key": "q"}) == \
        "ARCHER_SLACK_APP_NOT_CONFIGURED_NOTHING_SENT"
    with pytest.raises(ValueError):
        S.settings("eddie")
    # a delivery queued under the alias before 266 is never sent
    assert S.impersonation({"agent": "eddie", "source_key": "q"}) == \
        "NOT_A_CURRENT_AGENT_NOTHING_SENT"
    # two of three historical values: still not configured, nothing sent
    _slack_env(monkeypatch, {"SLACK_EDDIE_BOT_TOKEN": "xoxb-e",
                             "SLACK_EDDIE_SIGNING_SECRET": "se"})
    assert S.dedicated_identity("archer")["ok"] is False


def test_slack_reads_archers_historical_values_only_as_a_fallback(
        monkeypatch):
    _slack_env(monkeypatch, {"SLACK_EDDIE_BOT_TOKEN": "xoxb-e",
                             "SLACK_EDDIE_SIGNING_SECRET": "se",
                             "SLACK_EDDIE_APP_ID": "AE"})
    cfg = S.settings("archer")
    assert (cfg["token"], cfg["secret"], cfg["app"]) == ("xoxb-e", "se", "AE")
    assert S.dedicated_identity("archer")["ok"] is True
    _slack_env(monkeypatch, {"SLACK_EDDIE_BOT_TOKEN": "xoxb-e",
                             "SLACK_EDDIE_SIGNING_SECRET": "se",
                             "SLACK_EDDIE_APP_ID": "AE",
                             "SLACK_ARCHER_BOT_TOKEN": "xoxb-a",
                             "SLACK_ARCHER_SIGNING_SECRET": "sa",
                             "SLACK_ARCHER_APP_ID": "AA"})
    cfg = S.settings("archer")
    assert (cfg["token"], cfg["secret"], cfg["app"]) == ("xoxb-a", "sa", "AA")
    # a value shared with another agent still refuses every delivery
    _slack_env(monkeypatch, {"SLACK_EDDIE_BOT_TOKEN": "xoxb-s",
                             "SLACK_EDDIE_SIGNING_SECRET": "se",
                             "SLACK_EDDIE_APP_ID": "AE",
                             "SLACK_SCOUT_BOT_TOKEN": "xoxb-s"})
    assert S.dedicated_identity("archer")["distinct"] is False
    # content keyed under the alias travels under no token but Archer's
    for a in ("derek", "xavier", "scout", "adriana"):
        assert S.impersonation({"agent": a, "source_key": "eddie:x"}) == \
            "IMPERSONATION_REFUSED_ARCHER_CONTENT_ON_ANOTHER_TOKEN"
    assert S.canonical_agent("Eddie") == "archer"
    assert S.canonical_agent("scout") == "scout"


def test_the_runner_switch_and_loop_health_honour_both_names(monkeypatch):
    from sportsassets import loop_health as LH
    from sportsassets.agents import archer_runner as AR
    spec = next(s for s in LH.INVENTORY if s["name"] ==
                "agents.archer_runner") if hasattr(LH, "INVENTORY") else None
    for env, want in (({}, True), ({"EDDIE_RUNNER_ENABLED": "0"}, False),
                      ({"ARCHER_RUNNER_ENABLED": "0"}, False),
                      ({"ARCHER_RUNNER_ENABLED": "1",
                        "EDDIE_RUNNER_ENABLED": "0"}, True)):
        monkeypatch.delenv("ARCHER_RUNNER_ENABLED", raising=False)
        monkeypatch.delenv("EDDIE_RUNNER_ENABLED", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert AR.enabled() is want, env
        if spec is not None:
            assert LH.armed(spec, env=env)[0] is want, env
    assert AR.SERVICE == "agent_archer"


def test_the_voice_survives_the_rename_and_stays_his_own(monkeypatch):
    from sportsassets.agents import personas as P
    prof = P.DEFAULT_PROFILES["ARCHER"]
    env = {"ELEVENLABS_VOICE_ID_EDDIE": "abcdefghij0123456789"}
    assert P.configured_voice_id("ARCHER", prof, env) == \
        "abcdefghij0123456789"
    env["ELEVENLABS_VOICE_ID_ARCHER"] = "ZYXWVUTSRQ9876543210"
    assert P.configured_voice_id("ARCHER", prof, env) == \
        "ZYXWVUTSRQ9876543210"
    # a voice EDDIE resolved before 266 is ARCHER's own, never "shared"
    latest = {"EDDIE": {"status": "RESOLVED", "voice_id": "v1",
                        "resolved_at": 1.0},
              "ARCHER": {"status": "RESOLVED", "voice_id": "v1",
                         "resolved_at": 2.0}}
    assert I.voice_claims(latest) == {"v1": "ARCHER"}


@pytest.mark.asyncio
async def test_the_old_routes_answer_with_alias_of_archer(monkeypatch):
    from sportsassets.api import agent_pages as AGP
    from sportsassets.api import agents_identity as AI
    from sportsassets.api import agents_pos as AP

    async def ws():
        return {"agent": {"agent_id": "ARCHER"}, "read_only": True}
    monkeypatch.setattr(AP, "_archer_workspace", ws)
    for fn in (AP.eddie_workspace_alias, AP.eddie_workspace_agents_alias):
        got = await fn(Response())
        assert got["alias_of"] == "archer"
        assert got["historical_alias"] == "EDDIE"
        assert got["agent"] == {"agent_id": "ARCHER"}
    assert AI._agent("eddie") == "ARCHER"
    assert AI._tag("eddie", {"x": 1}) == {"x": 1, "alias_of": "archer",
                                          "historical_alias": "EDDIE"}
    assert AI._tag("archer", {"x": 1}) == {"x": 1}
    paths = {getattr(r, "path", "") for r in AGP.router.routes}
    assert "/api/command/agents/eddie/page" in paths


# ════════════════════════════════════════════════════════════════════
# 6 · THE DATABASE
# ════════════════════════════════════════════════════════════════════

async def _tx():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    return conn, tx


async def _refused(conn, sql, *args, match=None):
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
async def test_the_migration_is_idempotent_and_seeds_archer_from_the_code():
    conn, tx = await _tx()
    try:
        before = await conn.fetchval(
            "SELECT count(*) FROM agent_identity_versions "
            " WHERE agent_id = 'EDDIE'")
        await conn.execute(UP)
        await conn.execute(UP)
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_identity_versions "
            " WHERE agent_id = 'ARCHER'") == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_voice_profiles "
            " WHERE agent_id = 'ARCHER'") == 1
        # EDDIE's recorded identity versions are untouched
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_identity_versions "
            " WHERE agent_id = 'EDDIE'") == before
        row = await I.current_identity(conn, "ARCHER")
        spec = I.identity_row("ARCHER")
        for k in I.IDENTITY_FIELDS + ("identity_version", "content_sha",
                                      "source_directive", "source_ref",
                                      "approved_by", "approved_at"):
            assert row[k] == spec[k], k
        vp = await I.current_voice_profile(conn, "ARCHER")
        for k in ("voice_profile_id", "provider", "provider_voice_alias",
                  "provider_voice_id", "locale", "speaking_rate",
                  "assignment", "style_instructions"):
            assert vp[k] == I.VOICE_SPEC["ARCHER"][k], k
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_trigger "
            " WHERE tgname = 'agent_historical_alias_trg'") >= 20
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_database_holds_one_execution_identity():
    from tests._pre_266 import as_written_before_266
    conn, tx = await _tx()
    try:
        await R.ensure_identities(conn)
        ids = {r["agent_id"] for r in await conn.fetch(
            "SELECT agent_id FROM agent_identities")}
        assert "ARCHER" in ids
        # the alias is never (re)activated beside him: no new identity,
        # status, heartbeat or task under it
        await _refused(conn, "INSERT INTO agent_identities (agent_id, "
                       " display_name, mandate) VALUES ('EDDIE','e','m') "
                       "ON CONFLICT (agent_id) DO UPDATE SET mandate='m'",
                       match="EDDIE_IS_A_HISTORICAL_ALIAS")
        got = await R.heartbeat(conn, "EDDIE", state="IDLE")
        assert got["refusal"] == R.R_UNKNOWN_AGENT
        assert (await R.create_task(conn, assignee="EDDIE", created_by="x",
                                    kind="k", title="t", spec={}))[
            "refusal"] == R.R_UNKNOWN_AGENT
        # a pre-266 EDDIE identity row is frozen: never updated or deleted
        await as_written_before_266(
            conn, "INSERT INTO agent_status (agent_id, state, activity) "
                  "VALUES ('EDDIE', 'IDLE', 'NOT_YET_RUN') "
                  "ON CONFLICT (agent_id) DO NOTHING", table="agent_status")
        await _refused(conn, "UPDATE agent_status SET state='EVALUATING' "
                             " WHERE agent_id='EDDIE'",
                       match="EDDIE_IS_A_HISTORICAL_ALIAS")
        await _refused(conn, "DELETE FROM agent_status WHERE "
                             " agent_id='EDDIE'",
                       match="EDDIE_IS_A_HISTORICAL_ALIAS")
        await _refused(conn, "DELETE FROM agent_identities WHERE "
                             " agent_id='EDDIE'",
                       match="EDDIE_IS_A_HISTORICAL_ALIAS")
        # every new agent-keyed record refuses the alias
        await _refused(conn, "INSERT INTO agent_runs (run_id, agent_id, "
                             " started_at) VALUES ('r266', 'EDDIE', now())",
                       match="EDDIE_IS_A_HISTORICAL_ALIAS")
        await _refused(conn, "INSERT INTO agent_slack_delivery (delivery_id,"
                             " agent, team_id, channel_id, source_key) "
                             "VALUES ('d266','eddie','T','C','s')",
                       match="EDDIE_IS_A_HISTORICAL_ALIAS")
        # the status reader keys exactly one execution seat
        st = await R.status_of(conn, "ARCHER")
        assert st["agent_id"] == "ARCHER" and st["role"] == \
            "HEAD_OF_EXECUTION"
        alias = await R.status_of(conn, "eddie")
        assert alias["agent_id"] == "ARCHER"
        assert alias["alias_of"] == "archer"
        assert alias["historical_alias"] == "EDDIE"
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_eddie_rows_stay_as_written_and_read_as_historical_alias():
    from sportsassets.agents import agent_memory as M
    from tests._pre_266 import as_written_before_266
    conn, tx = await _tx()
    try:
        await R.ensure_identities(conn)
        await as_written_before_266(
            conn, "INSERT INTO agent_tasks (task_id, assignee, created_by, "
                  " kind, title, spec, status, created_at, updated_at) "
                  "VALUES ('task-266-hist', 'EDDIE', 'EDDIE', 'T', 't', "
                  " '{}'::jsonb, 'OPEN', now(), now())", table="agent_tasks")
        await as_written_before_266(
            conn, "INSERT INTO agent_decisions (decision_ref, agent_id, kind,"
                  " decided_at) VALUES ('adr:266-hist', 'EDDIE', 'TEST', "
                  " now())", table="agent_decisions")
        # the stored rows are exactly as written
        assert await conn.fetchval(
            "SELECT assignee FROM agent_tasks WHERE task_id='task-266-hist'"
        ) == "EDDIE"
        # the readers name ARCHER and say so
        ts = [t for t in await R.tasks(conn, assignee="ARCHER", limit=500)
              if t["task_id"] == "task-266-hist"]
        assert len(ts) == 1
        assert ts[0]["assignee"] == "ARCHER"
        assert ts[0]["created_by"] == "ARCHER"
        assert ts[0]["historical_alias"] == "EDDIE"
        assert set(ts[0]["historical_alias_columns"]) == {"assignee",
                                                          "created_by"}
        one = await R.task(conn, "task-266-hist")
        assert one["task"]["historical_alias"] == "EDDIE"
        ds = [d for d in await R.decisions(conn, agent_id="archer",
                                           limit=500)
              if d["decision_ref"] == "adr:266-hist"]
        assert ds and ds[0]["agent_id"] == "ARCHER"
        assert ds[0]["historical_alias"] == "EDDIE"
        # his identity history: EDDIE's HQ2 version 1 (labelled), then his
        if await conn.fetchval("SELECT count(*) FROM agent_identity_versions"
                               " WHERE agent_id = 'EDDIE'"):
            vers = await I.identity_versions(conn, "ARCHER")
            assert vers[0]["historical_alias"] == "EDDIE"
            assert vers[0]["agent_id"] == "ARCHER"
            assert vers[0]["display_name"] == "Eddie"     # as recorded
            assert vers[-1]["display_name"] == "Archer"
            assert "historical_alias" not in vers[-1]
        # memory: a pre-266 handoff addressed to EDDIE is his inbox's
        assert isinstance(await M.handoffs_to(conn, "ARCHER"), list)
        # nothing was rewritten
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_tasks WHERE assignee='ARCHER' "
            "   AND task_id='task-266-hist'") == 0
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_database_refuses_archer_any_authority_under_either_name():
    conn, tx = await _tx()
    try:
        for v, want in (("agent:archer", "ARCHER"), ("archer-bot", "ARCHER"),
                        ("ARCHER", "ARCHER"), ("agent:eddie", "ARCHER"),
                        ("EDDIE", "ARCHER"), ("Archer Smith", None),
                        ("Eddie Ruiz", None)):
            assert await conn.fetchval("SELECT pos_agent_actor($1)", v) \
                == want, v
        assert await conn.fetchval(
            "SELECT improve_is_machine_actor('ARCHER')") is True
        assert await conn.fetchval(
            "SELECT improve_is_machine_actor('EDDIE')") is True
        assert await conn.fetchval(
            "SELECT poslearn_is_agent_actor('archer-bot')") is True
        assert await conn.fetchval(
            "SELECT live_parity_named_human('archer')") is False
        for name in ("ARCHER", "EDDIE"):
            sp = conn.transaction()
            await sp.start()
            await conn.execute(
                "SELECT set_config('bettor.acting_agent', $1, true)", name)
            await _refused(conn, "INSERT INTO paper_control (control_key, "
                                 "enabled, updated_by) VALUES ('x', true, "
                                 "'y')", match="ARCHER_HAS_NO_AUTHORITY")
            await sp.rollback()
        # named in an actor column, under the historical label
        await _refused(conn, "INSERT INTO execmirror_control (actor) "
                             "VALUES ('agent:eddie')",
                       match="ARCHER_HAS_NO_AUTHORITY")
        await R.ensure_identities(conn)
        t = await R.create_task(conn, assignee="ARCHER", created_by="ARCHER",
                                kind="T", title="t", spec={},
                                task_id="arc-266-task")
        assert t["ok"], t
        await _refused(conn, (
            "INSERT INTO agent_task_events (task_id, at, kind, actor, detail)"
            " VALUES ('arc-266-task', now(), 'APPROVED', 'ARCHER', "
            " '{\"status_to\": \"APPROVED\"}')"),
            match="ARCHER_HAS_NO_AUTHORITY")
        # his authority is fixed in the identity record too
        spec = I.identity_row("ARCHER")
        await _refused(conn, (
            "INSERT INTO agent_identity_versions (agent_id, identity_version,"
            " display_name, title, presentation, role, mission, "
            " personality_traits, communication_style, default_voice_profile,"
            " expertise_domains, decision_principles, may, may_not, "
            " signature, authority_status, content_sha, approved_by) "
            "VALUES ('ARCHER', 2, 'A', 't', 'MALE', 'r', 'm', '[]', 's', "
            " 'vp', '[]', '[]', '[]', '[\"x\"]', 'sig', "
            " 'ENTRY_REQUEST_THROUGH_GATED_PATH', $1, "
            " 'PENDING_OWNER_APPROVAL')"), spec["content_sha"])
        # the collaboration loop never lets him mark release eligibility
        await _refused(conn, (
            "INSERT INTO agent_finding_stages (finding_id, seq, stage, actor,"
            " at) VALUES ('none', 7, 'RELEASE_ELIGIBILITY', 'ARCHER', now())"
        ))
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_rollback_refuses_while_archer_has_records():
    import asyncpg
    conn, tx = await _tx()
    try:
        eddie_rows = await conn.fetchval(
            "SELECT count(*) FROM agent_identity_versions "
            " WHERE agent_id = 'EDDIE'")
        await R.ensure_identities(conn)
        await R.start_run(conn, R.ARCHER, "archer-run:266-down")
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.RaiseError, match="rollback refused"):
            await conn.execute(DOWN)
        await sp.rollback()
        # a task, a finding stage, a Slack delivery: each refuses it alone
        await conn.execute("ALTER TABLE agent_runs DISABLE TRIGGER "
                           "agent_runs_append_only_trg")
        await conn.execute("DELETE FROM agent_runs WHERE agent_id='ARCHER'")
        await conn.execute("ALTER TABLE agent_runs ENABLE TRIGGER "
                           "agent_runs_append_only_trg")
        sp = conn.transaction()
        await sp.start()
        await R.create_task(conn, assignee="ARCHER", created_by="DEREK",
                            kind="T", title="t", spec={},
                            task_id="arc-266-down")
        with pytest.raises(asyncpg.RaiseError, match="rollback refused"):
            await conn.execute(DOWN)
        await sp.rollback()
        # with none (his code-default persona version, which a chat may
        # have seeded, removed here as a record would be), it rolls back
        # (idempotent) and never touches EDDIE's rows; it re-applies
        await conn.execute("ALTER TABLE agent_persona_versions DISABLE "
                           "TRIGGER USER")
        await conn.execute("DELETE FROM agent_persona_versions WHERE "
                           " agent_id = 'ARCHER'")
        await conn.execute("ALTER TABLE agent_persona_versions ENABLE "
                           "TRIGGER USER")
        await conn.execute("DELETE FROM agent_chat_conversations WHERE "
                           " agent_id = 'ARCHER'")
        await conn.execute(DOWN)
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_identity_versions "
            " WHERE agent_id = 'ARCHER'") == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_identity_versions "
            " WHERE agent_id = 'EDDIE'") == eddie_rows
        assert await conn.fetchval(
            "SELECT pos_agent_actor('agent:eddie')") == "EDDIE"
        await conn.execute(UP)
        assert await conn.fetchval(
            "SELECT pos_agent_actor('agent:eddie')") == "ARCHER"
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_identity_versions "
            " WHERE agent_id = 'ARCHER'") == 1
    finally:
        await tx.rollback()
        await conn.close()


def test_every_eddie_seed_and_record_name_is_json_safe():
    # the manifests stay valid JSON under both names
    for name in ("archer-manifest.json", "eddie-manifest.json"):
        json.loads((ROOT.parent / "research" / name).read_text())
