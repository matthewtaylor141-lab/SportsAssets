"""KAREN, PART TWO (migration 212): HER PERSONA, THE AUTOMATIC PEER
RESPONSE AND THE INDEPENDENT EVALUATION, THE NEW CHALLENGE TARGETS, THE
COMMAND CENTRE VIEW AND THE #agent-workroom PATH.

  * Persona: Karen is in the versioned persona system (agents-personas-v1);
    aggressive, skeptical, contrarian, satirical, evidence-obsessed, with
    "What are we missing?" and "Prove it."; politics has zero influence and
    she attacks methods, never people. Her chat facts are ONLY her challenge
    records and the evidence they cite -- for the records-only answer and
    for what a language model is given.
  * Peer response: the TARGET's rule-based responder re-applies the SAME
    rule: CONCEDE when it still holds, DISPUTE (with evidence) when not. The
    independent evaluator (Audrey for Derek / Xavier / the Chief Allocator,
    Xavier for Audrey) resolves: never Karen, never the target, and the
    database refuses a disputed challenge resolved by its target.
  * Targets: the Chief Allocator (shadow allocator tables, skipped cleanly
    when absent), large allocations, new policy candidates, and artifacts
    READY_FOR_OWNER_APPROVAL without evidence references.
  * The Command Centre shows, per challenge: target agent, target decision,
    evidence, category, severity, state, peer response, independent
    evaluation, false-block outcome and downstream impact.
  * Slack: #agent-workroom posts go out as Karen only, under her own token,
    and nothing is queued or sent before her app is configured.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

import asyncpg
import pytest

from sportsassets import slack_bridge as S
from sportsassets.agents import karen as K
from sportsassets.agents import karen_runner as KR
from sportsassets.agents import peer_responder as PR
from sportsassets.agents import persona_chat as PC
from sportsassets.agents import persona_facts as PF
from sportsassets.agents import personas as P
from sportsassets.agents import registry as R
from sportsassets.api import agent_pages as AP

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
UP = (ROOT / "migrations" / "212_karen_persona_peer_review.sql").read_text()
DOWN = (ROOT / "migrations" / "rollback" /
        "212_karen_persona_peer_review.down.sql").read_text()
T0 = 1_790_000_000.0


# ════════════════════════════════════════════════════════════════════
# PERSONA (pure)
# ════════════════════════════════════════════════════════════════════

def test_karen_is_a_versioned_persona_with_her_lines_and_her_limits():
    assert "KAREN" in P.AGENTS and P.agent_of("karen") == "KAREN"
    assert P.VERSION == "agent-personas-v1"
    prof = P.DEFAULT_PROFILES["KAREN"]
    assert P.validate_profile(prof) is None
    assert P.screens_authority(prof) == []
    text = prof["persona_text"]
    for word in ("aggressive", "skeptical", "contrarian", "funny",
                 "evidence obsession", "What are we missing?", "Prove it."):
        assert word in text, word
    assert "political ideology has zero influence" in text
    assert "never people" in text
    avoid = " ".join(prof["avoid"])
    assert "No politics" in avoid and "Never mocks" in avoid
    assert "Never answers from anything but her challenge records" in avoid
    assert prof["voice_profile"]["provider"] == "elevenlabs"
    sp = PC.system_prompt(dict(prof, version=1))
    assert "Karen" in sp and "red team" in sp and "FIXED RULES" in sp
    assert "karen" in PC.describe()["agents"]
    for table in (PC.OPENERS, PC.PLAIN_OPENERS, PC.SOURCE_ORDER, PC.LEADS,
                  PC.MISSING_LEAD, PC.TIME_NOTE, PC.REFUSAL_VOICE):
        assert "KAREN" in table
    assert PC.OPENERS["KAREN"][0] == "What are we missing?"


def test_the_role_brief_carries_her_lines_and_her_limits():
    from sportsassets.agents import role_brief as B
    f = B.FOCUS["KAREN"]
    assert '"What are we missing?"' in f and '"Prove it."' in f
    assert "never people" in f and "zero influence" in f
    assert "independent evaluator decides" in f


def test_karen_modules_never_answer_or_evaluate_for_anyone():
    for name in ("karen.py", "karen_runner.py"):
        src = (ROOT / "sportsassets" / "agents" / name).read_text()
        assert "peer_responder" not in src, name
        assert "K.respond(" not in src and "K.resolve(" not in src, name
    src = (ROOT / "sportsassets" / "agents" / "peer_responder.py").read_text()
    assert "INSERT INTO" not in src and "UPDATE karen" not in src
    for ev_target, ev in K.EVALUATOR_FOR.items():
        assert ev not in (ev_target, K.KAREN)
        assert PR.evaluator_for(ev_target) == ev
    assert K.EVALUATOR_FOR["AUDREY"] == "XAVIER"
    assert K.EVALUATOR_FOR["DEREK"] == K.EVALUATOR_FOR["XAVIER"] == "AUDREY"
    assert K.EVALUATOR_FOR["CHIEF_ALLOCATOR"] == "AUDREY"


# ════════════════════════════════════════════════════════════════════
# POSTGRES
# ════════════════════════════════════════════════════════════════════

async def _tx():
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    await R.ensure_identities(conn)
    return conn, tx


async def _expect(conn, sql, *args, match=None):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(asyncpg.PostgresError) as e:
            await conn.execute(sql, *args)
        if match:
            assert match in str(e.value), str(e.value)
    finally:
        await sp.rollback()


async def _decision(conn, ref, agent="DEREK", refs="[]", at=T0):
    await conn.execute(
        "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
        " decided_at, evidence_refs) VALUES ($1,$2,'T',to_timestamp($3),"
        " $4::jsonb)", ref, agent, at, refs)


@pg
@pytest.mark.asyncio
async def test_her_chat_facts_are_only_her_records_and_their_evidence(
        monkeypatch):
    conn, tx = await _tx()
    try:
        await _decision(conn, "adr:k2-chat")
        got = await K.open_challenge(
            conn, target_agent="DEREK", target_kind="agent_decisions",
            target_id="adr:k2-chat", detector="DECISION_WITHOUT_EVIDENCE",
            claim="cites nothing. Prove it.", severity="MEDIUM",
            evidence_refs=[{"kind": "agent_decisions", "id": "adr:k2-chat"}],
            record_at=T0, at=T0 + 60)
        assert got["ok"], got
        b = await PF.gather(conn, question="What are you challenging?",
                            context={}, now=T0, agent="KAREN")
        sources = {f["source"] for f in b["facts"]}
        assert sources and all(s.startswith("karen_") for s in sources), \
            sources
        assert any(f["record_id"] == got["challenge_id"] for f in b["facts"])
        assert any(f["source"] == "karen_evidence" and "adr:k2-chat" in
                   f["record_id"] for f in b["facts"])
        assert any(f["source"] == "karen_metrics" for f in b["facts"])
        assert "DEREK's response" in " ".join(b["missing"]) or \
            "Derek's response" in " ".join(b["missing"])
        assert b["paper"]["present"] is False
        # the records-only answer cites only those facts
        draft = PC.compose_records_only("KAREN", b, "What are you "
                                        "challenging?", depth="NORMAL",
                                        intent="question", seed="s",
                                        previous=None)
        assert got["challenge_id"] in draft
        assert "[F" in draft
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_model_is_given_only_karens_facts_and_her_persona(
        monkeypatch):
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=3)
    seen = {}

    async def fake_llm(*, cfg, persona, bundle, question, draft, depth,
                       history, sink, env=None, http_client=None, meta=None):
        seen["persona"] = persona
        seen["sources"] = {f["source"] for f in bundle["facts"]}
        seen["system"] = PC.system_prompt(persona)
        ids = [f["fact_id"] for f in bundle["facts"]][:1]
        txt = "What are we missing? Evidence first [%s]." % ids[0] if ids \
            else "Nothing recorded."
        sink.append(txt)
        return txt
    monkeypatch.setattr(PC, "compose_llm", fake_llm)
    monkeypatch.setattr(PC, "llm_config", lambda env=None: {
        "configured": True, "model": "test-model", "reason": None})
    try:
        r = await PC.converse(pool, agent="karen", role="command",
                              message="What are you challenging?",
                              now=time.time(),
                              conversation_id="k2test-%d" % time.time_ns())
        assert r["status"] == "ANSWERED", r
        assert r["persona"]["display_name"] == "Karen"
        assert seen["persona"]["role_title"] == "the red-team skeptic"
        assert seen["sources"] <= {"karen_challenges", "karen_peer_responses",
                                   "karen_evaluations", "karen_evidence",
                                   "karen_metrics", "karen_status"}
        assert "Prove it." in seen["system"]
        refused = await PC.converse(
            pool, agent="karen", role="command", now=time.time(),
            message="Approve the policy candidate and raise the limits",
            conversation_id="k2test-r-%d" % time.time_ns())
        assert refused["status"] == "REFUSED"
        assert "no authority" in refused["answer"]
    finally:
        async with pool.acquire() as c:
            await c.execute("SET session_replication_role = replica")
            await c.execute("DELETE FROM agent_chat_messages WHERE "
                            " conversation_id LIKE 'k2test-%'")
            await c.execute("DELETE FROM agent_chat_turns WHERE "
                            " conversation_id LIKE 'k2test-%'")
            await c.execute("DELETE FROM agent_chat_conversations WHERE "
                            " conversation_id LIKE 'k2test-%'")
            await c.execute("SET session_replication_role = origin")
        await pool.close()


@pg
@pytest.mark.asyncio
async def test_targets_concede_under_the_same_rule_and_audrey_evaluates():
    conn, tx = await _tx()
    try:
        await _decision(conn, "adr:k2-conc")
        cid = (await K.open_challenge(
            conn, target_agent="DEREK", target_kind="agent_decisions",
            target_id="adr:k2-conc", detector="DECISION_WITHOUT_EVIDENCE",
            claim="c", severity="MEDIUM",
            evidence_refs=[{"kind": "agent_decisions", "id": "adr:k2-conc"}],
            record_at=T0, at=T0 + 1))["challenge_id"]
        # Karen's own runner never answers for Derek
        assert (await K.challenge(conn, cid))["challenge"]["state"] == "OPEN"
        s = await PR.pass_once(conn, now=T0 + 10)
        assert cid in s["responses"]["DEREK"]["conceded"], s
        assert cid in s["evaluations"]["AUDREY"]["upheld"], s
        c = (await K.challenge(conn, cid))["challenge"]
        assert c["state"] == "UPHELD"
        assert c["peer_response"]["by"] == "DEREK"
        assert c["peer_response"]["stance"] == "CONCEDE"
        assert c["peer_response"]["evidence_refs"] == [
            {"kind": "agent_decisions", "id": "adr:k2-conc"}]
        ie = c["independent_evaluation"]
        assert ie["status"] == "RECORDED" and ie["by"] == "AUDREY"
        assert ie["independent"] is True and ie["evidence_refs"]
        runs = await conn.fetch("SELECT agent_id, outcome FROM agent_runs "
                                " WHERE run_id LIKE 'peer-response:%'")
        assert {r["agent_id"] for r in runs} == {"DEREK", "AUDREY"}
        # a manual (non-rule) challenge is left for a person
        await _decision(conn, "adr:k2-man")
        man = (await K.open_challenge(
            conn, target_agent="XAVIER", target_kind="agent_decisions",
            target_id="adr:k2-man", detector="MANUAL_REVIEW", claim="c",
            severity="LOW",
            evidence_refs=[{"kind": "agent_decisions", "id": "adr:k2-man"}],
            record_at=T0, at=T0 + 1))["challenge_id"]
        s = await PR.pass_once(conn, now=T0 + 20)
        assert man in s["responses"]["XAVIER"]["skipped"]
        assert (await K.challenge(conn, man))["challenge"]["state"] == "OPEN"
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_cured_record_is_disputed_and_xavier_evaluates_audrey():
    conn, tx = await _tx()
    try:
        await _decision(conn, "adr:k2-aud", agent="AUDREY")
        await _decision(conn, "adr:k2-cure", agent="AUDREY",
                        refs='[{"kind":"x","id":"1"}]')
        cid = (await K.open_challenge(
            conn, target_agent="AUDREY", target_kind="agent_decisions",
            target_id="adr:k2-aud", detector="DECISION_WITHOUT_EVIDENCE",
            claim="cites nothing", severity="MEDIUM",
            evidence_refs=[{"kind": "agent_decisions", "id": "adr:k2-aud"}],
            record_at=T0, at=T0 + 1))["challenge_id"]
        assert await KR.rule_holds(conn, "DECISION_WITHOUT_EVIDENCE",
                                   "agent_decisions", "adr:k2-aud") is True
        # the decision index is completed: the record is cured
        await conn.execute(
            "UPDATE agent_decisions SET evidence_refs="
            " '[{\"kind\":\"agent_decisions\",\"id\":\"adr:k2-cure\"}]'"
            " WHERE decision_ref='adr:k2-aud'")
        assert await KR.rule_holds(conn, "DECISION_WITHOUT_EVIDENCE",
                                   "agent_decisions", "adr:k2-aud") is False
        s = await PR.pass_once(conn, now=T0 + 10)
        assert cid in s["responses"]["AUDREY"]["disputed"], s
        assert cid in s["evaluations"]["XAVIER"]["rejected"], s
        c = (await K.challenge(conn, cid))["challenge"]
        assert c["state"] == "REJECTED"
        assert c["peer_response"]["stance"] == "DISPUTE"
        assert c["resolved_by"] == "XAVIER"
        # THE DATABASE: a disputed challenge is never resolved by its target
        # (nor by Karen), even by a caller that skips the module
        await _decision(conn, "adr:k2-aud2", agent="AUDREY")
        c2 = (await K.open_challenge(
            conn, target_agent="AUDREY", target_kind="agent_decisions",
            target_id="adr:k2-aud2", detector="DECISION_WITHOUT_EVIDENCE",
            claim="cites nothing", severity="HIGH",
            evidence_refs=[{"kind": "agent_decisions", "id": "adr:k2-aud2"}],
            record_at=T0, at=T0 + 1))["challenge_id"]
        assert (await K.respond(conn, c2, agent="AUDREY", stance="DISPUTE",
                                response="no", at=T0 + 2))["ok"]
        got = await K.resolve(conn, c2, resolver="AUDREY", outcome="UPHELD",
                              reason="mine", at=T0 + 3)
        assert got["refusal"] == K.R_NOT_INDEPENDENT
        await _expect(conn, "UPDATE karen_challenges SET state='UPHELD', "
                      " outcome='UPHELD', outcome_reason='r', "
                      " resolved_by='AUDREY', resolved_at=to_timestamp($2) "
                      " WHERE challenge_id=$1", c2, T0 + 3,
                      match="dispute_independent")
        # the category is fixed; the evaluation evidence is recorded once
        await _expect(conn, "UPDATE karen_challenges SET category='OTHER' "
                      " WHERE challenge_id=$1", cid)
        await _expect(conn, "UPDATE karen_challenges SET "
                      " resolution_evidence_refs='[{\"kind\":\"x\",\"id\":"
                      "\"y\"}]'::jsonb WHERE challenge_id=$1", cid)
    finally:
        await tx.rollback()
        await conn.close()


_ALLOC_INSERT = (
    "INSERT INTO intel_allocations (run_id, candidate_id, computed_at, rank,"
    " candidate_kind, decision_id, group_id, us_market_slug, score, "
    " net_ev_per_dollar, shadow_weight, shadow_usd, binding_constraint, "
    " reasons, inputs) VALUES ($1,$2,to_timestamp($3),1,$4,$5,$6,'slug',"
    " 0.1,0.05,0.01,$7,NULL,$8::jsonb,$9::jsonb)")


async def _alloc(conn, run, cand, at, usd, *, reasons=None, inputs=None):
    kind = "NEW_DECISION" if cand.startswith("decision:") else \
        "OPEN_POSITION"
    await conn.execute(
        _ALLOC_INSERT, run, cand, at, kind,
        cand.split(":", 1)[1] if kind == "NEW_DECISION" else None,
        cand.split(":", 1)[1] if kind == "OPEN_POSITION" else None, usd,
        json.dumps(reasons if reasons is not None else ["SPORT_CAP"]),
        json.dumps(inputs if inputs is not None else {"game": "g"}))


@pg
@pytest.mark.asyncio
async def test_the_chief_allocator_is_challenged_once_per_candidate_on_the_real_208_schema():
    conn, tx = await _tx()
    try:
        now = time.time()
        # THE REAL TABLE (migration 208): composite (run_id, candidate_id)
        pk = await conn.fetch(
            "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON "
            " a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) WHERE "
            " i.indrelid='intel_allocations'::regclass AND i.indisprimary")
        assert {r["attname"] for r in pk} == {"run_id", "candidate_id"}
        assert KR.ALLOCATOR_TABLE == "intel_allocations"
        assert set(K.CL.EVIDENCE_KINDS["intel_allocations"]) == {
            "intel_allocations", "candidate_id"}
        await conn.execute("DELETE FROM intel_allocations")
        # run 1 and run 2 rewrite the same candidates (600 s apart)
        for run, at in (("run-1", now - 700), ("run-2", now - 100)):
            await _alloc(conn, run, "decision:dec_noev", at, 40.0)
            await _alloc(conn, run, "position:grp_big", at, 5000.0,
                         inputs={"game": "g", "decision_id": "dec_src"})
            await _alloc(conn, run, "decision:dec_str", at, 30.0,
                         reasons=["SPORT_CAP", "decision:dec_str"])
            await _alloc(conn, run, "decision:dec_zero", at, 0.0)
        dets = (("ALLOCATION_WITHOUT_EVIDENCE",
                 KR.detect_allocation_without_evidence),
                ("LARGE_ALLOCATION", KR.detect_large_allocation))
        s = await KR.pass_once(conn, now=now, detectors=dets)
        assert s["detector_errors"] == {}, s
        rows = {(r["detector"], r["target_id"]): r
                for r in await K.challenges(conn, limit=20)}
        # ONE challenge per stable candidate, keyed on candidate_id (never
        # run_id); evidence in reasons / inputs counts; $0 is no allocation
        assert set(rows) == {("ALLOCATION_WITHOUT_EVIDENCE",
                              "decision:dec_noev"),
                             ("LARGE_ALLOCATION", "position:grp_big")}, rows
        for c in rows.values():
            assert c["target_agent"] == "CHIEF_ALLOCATOR"
            assert c["target_kind"] == "intel_allocations"
            assert c["category"] == "ALLOCATION_RISK"
            assert "run-" not in c["target_id"]
            assert c["evidence_refs"] == [{"kind": "intel_allocations",
                                           "id": c["target_id"]}]
        assert rows[("LARGE_ALLOCATION", "position:grp_big")]["body"][
            "latest_run_id"] == "run-2"
        # run 3 rewrites everything again: still no new challenge
        await _alloc(conn, "run-3", "decision:dec_noev", now - 10, 41.0)
        await _alloc(conn, "run-3", "position:grp_big", now - 10, 5100.0,
                     inputs={"decision_id": "dec_src"})
        s = await KR.pass_once(conn, now=now + 1, detectors=dets)
        assert s["opened"] == [], s
        # the LATEST row decides: run 4 gives dec_noev evidence, so its
        # responder disputes and Audrey rejects; grp_big is still large, so
        # it concedes and Audrey upholds
        await _alloc(conn, "run-4", "decision:dec_noev", now - 5, 41.0,
                     inputs={"valuation_id": 123})
        assert await KR.rule_holds(conn, "ALLOCATION_WITHOUT_EVIDENCE",
                                   "intel_allocations",
                                   "decision:dec_noev") is False
        assert await KR.rule_holds(conn, "LARGE_ALLOCATION",
                                   "intel_allocations",
                                   "position:grp_big") is True
        s = await PR.pass_once(conn, now=now + 5)
        r = s["responses"]["CHIEF_ALLOCATOR"]
        noev = rows[("ALLOCATION_WITHOUT_EVIDENCE",
                     "decision:dec_noev")]["challenge_id"]
        big = rows[("LARGE_ALLOCATION", "position:grp_big")]["challenge_id"]
        assert r["disputed"] == [noev] and r["conceded"] == [big], r
        got = {c: (await K.challenge(conn, c))["challenge"]
               for c in (noev, big)}
        assert got[noev]["state"] == "REJECTED"
        assert got[big]["state"] == "UPHELD"
        assert {g["resolved_by"] for g in got.values()} == {"AUDREY"}
        assert "CHIEF_ALLOCATOR" not in PR.EVALUATORS
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_allocator_detectors_skip_cleanly_without_migration_208():
    conn, tx = await _tx()
    try:
        await conn.execute("ALTER TABLE intel_allocations RENAME TO "
                           "intel_allocations_k3_hidden")
        now = time.time()
        assert await KR.detect_allocation_without_evidence(conn, now, 5) == []
        assert await KR.detect_large_allocation(conn, now, 5) == []
        assert await KR.rule_holds(conn, "LARGE_ALLOCATION",
                                   "intel_allocations", "decision:x") is None
        s = await KR.pass_once(conn, now=now, detectors=(
            ("ALLOCATION_WITHOUT_EVIDENCE",
             KR.detect_allocation_without_evidence),))
        assert s["detector_errors"] == {} and s["opened"] == []
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_policy_candidates_and_ready_artifacts_without_evidence_are_challenged():
    conn, tx = await _tx()
    try:
        now = time.time()
        await conn.execute(
            "INSERT INTO agent_policy_versions (agent_id, policy_key, "
            " version, params, state, created_by, created_at) VALUES "
            " ('DEREK','entry','k2v1','{}'::jsonb,'CANDIDATE','AUDREY',"
            "  to_timestamp($1)),"
            " ('DEREK','entry','k2v2', $2::jsonb,'CANDIDATE','AUDREY',"
            "  to_timestamp($1))", now - 60,
            json.dumps({"evidence_refs": [{"kind": "agent_decisions",
                                           "id": "x"}]}))
        got = await KR.detect_policy_candidate_without_evidence(conn, now, 5)
        ids = [c["target_id"] for c in got]
        assert "DEREK|entry|k2v1" in ids and "DEREK|entry|k2v2" not in ids
        c = [c for c in got if c["target_id"] == "DEREK|entry|k2v1"][0]
        assert c["target_agent"] == "AUDREY"            # who created it
        arts = await KR.detect_policy_artifact_ready_without_evidence(
            conn, now, 5)
        if await conn.fetchval("SELECT count(*) FROM agent_policy_artifacts "
                               " WHERE status='READY_FOR_OWNER_APPROVAL' "
                               "   AND NOT document ? 'evidence_refs'"):
            assert arts and all(a["category"] if "category" in a else True
                                for a in arts)
            assert arts[0]["target_agent"] in K.TARGETS
        s = await KR.pass_once(conn, now=now, detectors=(
            ("POLICY_CANDIDATE_WITHOUT_EVIDENCE",
             KR.detect_policy_candidate_without_evidence),
            ("POLICY_ARTIFACT_READY_WITHOUT_EVIDENCE",
             KR.detect_policy_artifact_ready_without_evidence),
            ("LIVE_RULE_READY_WITHOUT_EVIDENCE",
             KR.detect_live_rule_ready_without_evidence)))
        assert s["detector_errors"] == {}, s
        cats = {r["category"] for r in await K.challenges(conn, limit=20)}
        assert cats == {"GOVERNANCE_GAP"}
        # every governance challenge is grounded in the record it names
        met = (await K.metrics(conn))["metrics"]["evidence_grounding"]
        assert met["numerator"] == met["denominator"] >= 1
        # Karen never touched the candidate (no authority, read only)
        assert await conn.fetchval(
            "SELECT state FROM agent_policy_versions WHERE version='k2v1'") \
            == "CANDIDATE"
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_212_is_idempotent_and_its_rollback_refuses_over_its_records():
    conn, tx = await _tx()
    try:
        # a database that applied this file under its first name (207a)
        # carries the old-named guard: re-applying as 212 replaces it
        await conn.execute(
            "CREATE OR REPLACE FUNCTION karen_challenges_207a_guard() "
            "RETURNS trigger AS $$ BEGIN RETURN NEW; END $$ LANGUAGE plpgsql;"
            "CREATE TRIGGER karen_challenges_207a_guard_trg BEFORE INSERT OR "
            "UPDATE ON karen_challenges FOR EACH ROW EXECUTE FUNCTION "
            "karen_challenges_207a_guard()")
        await conn.execute(UP)
        await conn.execute(UP)
        trg = {r["tgname"] for r in await conn.fetch(
            "SELECT tgname FROM pg_trigger WHERE tgrelid="
            "'karen_challenges'::regclass AND NOT tgisinternal")}
        assert "karen_challenges_212_guard_trg" in trg
        assert "karen_challenges_207a_guard_trg" not in trg
        assert await conn.fetchval(
            "SELECT to_regprocedure('karen_challenges_207a_guard()')") is None
        await conn.execute("INSERT INTO agent_persona_versions (agent_id, "
                           " version, display_name, role_title, perspective, "
                           " persona_text, voice_profile, content_sha, "
                           " created_by, reason) VALUES ('KAREN', 99, 'K', "
                           " 'r', 'p', 't', '{\"provider\":\"elevenlabs\"}',"
                           " 'sha', 't', 'r')")
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(DOWN)
        await sp.rollback()
    finally:
        await tx.rollback()
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# THE COMMAND CENTRE
# ════════════════════════════════════════════════════════════════════

FIELDS = ("Target agent", "Target decision", "Evidence", "Category",
          "Severity", "State", "Peer response", "Independent evaluation",
          "False-block outcome", "Downstream impact")


def test_the_command_centre_shows_every_field_per_challenge():
    from tests.test_agent_workspaces_render_the_contract import (
        _agent, _card, _node, _sec)
    c = {"challenge_id": "kch:2", "target_agent": "XAVIER",
         "target_kind": "paper_xavier_reviews", "target_id": "rev-9",
         "target_decision": {"kind": "paper_xavier_reviews", "id": "rev-9"},
         "detector": "HOLD_ON_STALE_PROBABILITY", "category": "STALE_INPUT",
         "severity": "HIGH", "state": "UPHELD", "claim": "stale. Prove it.",
         "evidence_refs": [{"kind": "paper_xavier_reviews", "id": "rev-9"}],
         "peer_response": {"by": "XAVIER", "stance": "CONCEDE",
                           "response": "yes", "evidence_refs": [
                               {"kind": "paper_xavier_reviews",
                                "id": "rev-9"}]},
         "independent_evaluation": {"status": "RECORDED", "outcome": "UPHELD",
                                    "by": "AUDREY", "reason": "re-checked",
                                    "independent": True, "evidence_refs": [
                                        {"kind": "paper_xavier_reviews",
                                         "id": "rev-9"}]},
         "false_block_outcome": "NOT_BLOCKING",
         "downstream": {"status": "IMPROVEMENT_LINKED", "finding_id": "afnd:1",
                        "linked_by": "AUDREY", "impact": {"note": "ok"}},
         "time_to_challenge_s": 5, "challenged_at": T0}
    j = {"agent": _agent("KAREN"), "read_at": T0, "read_only": True,
         "sections": {"current_challenges": _sec([c])}}
    html = _node("karen", "return AG.workspace('karen', %s);" % json.dumps(j))
    st, body = _card(html, "current_challenges")
    for label in FIELDS:
        assert ">%s<" % label in body, label
    for value in ("XAVIER", "rev-9", "STALE_INPUT", "HIGH", "CONCEDE",
                  "AUDREY", "re-checked", "not blocking", "afnd:1"):
        assert value in body, value


def test_her_page_carries_an_original_portrait_her_persona_and_the_chat():
    h = AP.page_html("karen")
    svg = AP.KAREN_PORTRAIT_SVG
    assert svg.startswith("<svg") and "PROVE IT." in svg
    assert "WHAT ARE WE MISSING?" in svg
    assert "http" not in svg and "<image" not in svg   # no external asset
    assert 'id="karen-hero"' in h and svg in h
    assert "AI AGENT" in h and "zero influence" in h and "never people" in h
    assert 'id="talk" data-agent="karen"' in h
    assert "only from her challenge records" in h
    assert "Karen can question and challenge;" in h


# ════════════════════════════════════════════════════════════════════
# SLACK: #agent-workroom AS KAREN, OR NOTHING
# ════════════════════════════════════════════════════════════════════

BASE = {"SLACK_TEAM_ID": "T_TEST", "SLACK_ALLOWED_CHANNEL_IDS": "C_WORK",
        "SLACK_MANAGEMENT_USER_IDS": "U_TEST",
        "SLACK_WORKROOM_CHANNEL_ID": "C_WORK"}
THREE = {"derek": ("xoxb-d", "sd", "AD"), "xavier": ("xoxb-x", "sx", "AX"),
         "audrey": ("xoxb-a", "sa", "AA")}


def _env(monkeypatch, **agents):
    for k, v in BASE.items():
        monkeypatch.setenv(k, v)
    for a in ("DEREK", "XAVIER", "AUDREY", "KAREN"):
        for f in ("BOT_TOKEN", "SIGNING_SECRET", "APP_ID"):
            monkeypatch.delenv("SLACK_%s_%s" % (a, f), raising=False)
    for a, (tok, sec, app) in agents.items():
        monkeypatch.setenv("SLACK_%s_BOT_TOKEN" % a.upper(), tok)
        monkeypatch.setenv("SLACK_%s_SIGNING_SECRET" % a.upper(), sec)
        monkeypatch.setenv("SLACK_%s_APP_ID" % a.upper(), app)


@pg
@pytest.mark.asyncio
async def test_the_workroom_path_posts_as_karen_only_and_nothing_before_setup(
        monkeypatch):
    conn, tx = await _tx()
    try:
        await conn.execute("DELETE FROM agent_slack_delivery")
        await _decision(conn, "adr:k2-sl")
        cid = (await K.open_challenge(
            conn, target_agent="DEREK", target_kind="agent_decisions",
            target_id="adr:k2-sl", detector="DECISION_WITHOUT_EVIDENCE",
            claim="c", severity="LOW",
            evidence_refs=[{"kind": "agent_decisions", "id": "adr:k2-sl"}],
            record_at=time.time() - 30, at=time.time()))["challenge_id"]
        # BEFORE THE ADMIN STEP: nothing queued
        _env(monkeypatch, **THREE)
        await S.publish_karen_challenges(conn)
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_slack_delivery") == 0
        assert S.impersonation({"agent": "karen", "source_key": "q"}) == \
            "KAREN_SLACK_APP_NOT_CONFIGURED_NOTHING_SENT"
        # a shared token: still nothing
        _env(monkeypatch, **THREE, karen=("xoxb-a", "sk", "AK"))
        await S.publish_karen_challenges(conn)
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_slack_delivery") == 0
        # HER OWN APP: the challenge, then (after the peer response and the
        # evaluation) the outcome -- to #agent-workroom, as karen
        _env(monkeypatch, **THREE, karen=("xoxb-k", "sk", "AK"))
        await S.publish_karen_challenges(conn)
        await PR.pass_once(conn, now=time.time() + 1)
        await S.publish_karen_challenges(conn)
        await S.publish_karen_challenges(conn)                  # once each
        rows = await conn.fetch("SELECT agent, channel_id, source_key, "
                                " answer FROM agent_slack_delivery "
                                " ORDER BY source_key")
        assert [r["source_key"] for r in rows] == [
            "karen:challenge:" + cid, "karen:outcome:" + cid]
        assert {r["agent"] for r in rows} == {"karen"}
        assert {r["channel_id"] for r in rows} == {"C_WORK"}
        assert "independent evaluation" in rows[1]["answer"]
        assert "AUDREY" in rows[1]["answer"] or "Audrey" in rows[1]["answer"]
    finally:
        await tx.rollback()
        await conn.close()


def test_the_setup_note_states_one_admin_action():
    doc = (ROOT.parent / "research" / "karen_slack_setup.md").read_text()
    assert doc.count("**THE ACTION") == 1
    for name in ("SLACK_KAREN_BOT_TOKEN", "SLACK_KAREN_SIGNING_SECRET",
                 "SLACK_KAREN_APP_ID", "research/karen-manifest.json",
                 "#agent-workroom"):
        assert name in doc, name
    m = json.loads((ROOT.parent / "research" / "karen-manifest.json")
                   .read_text())
    assert "chat:write.public" in m["oauth_config"]["scopes"]["bot"]
    assert m["settings"]["event_subscriptions"]["request_url"].endswith(
        "/api/integrations/slack/karen/events")
