"""CAPITAL-CRITICAL: THE IMPROVEMENT PIPELINE'S DATABASE GUARDS (migration 221).

The ledger enforces the canonical workflow in Postgres, independently of the
runner (agents/improvement_pipeline.py) -- a caller that skips the module is
refused by the triggers and CHECKs:

  §1 THE FULL PATH. EVIDENCE -> HYPOTHESIS -> PEER CHALLENGE (Karen + a peer)
     -> OWNER RESPONSE -> EXPERIMENT (candidate patch as TEXT) -> INDEPENDENT
     EVALUATION -> ELIGIBLE CHANGE -> CONTROLLED RELEASE (human, exact-SHA
     gate receipt) -> FORWARD RESULT -> ROLLED BACK, each written by its
     permitted actor class; the item's stage follows its transitions.
  §2 NO SELF-APPROVAL. The owner, the hypothesis author and the patch
     author never evaluate or mark eligible; Karen, Eddie and Scout never
     evaluate; ELIGIBLE_CHANGE needs an independent PASS and no FAIL.
  §3 PROTECTED AREAS. requires_human_review and two independent reviews are
     forced by CHECK; one PASS is not enough; an agent cannot mark a
     protected item eligible; the classification only escalates.
  §4 FORWARD ONLY. No skipped stage, no going back, once-only stages once,
     the owner responds only after BOTH Karen and a peer challenged,
     terminal is terminal, append-only (UPDATE / DELETE / TRUNCATE refused),
     the item's stage cannot be set by hand.
  §5 HUMAN APPROVER REQUIRED. CONTROLLED_RELEASE refuses an agent, a system
     / bot name, a recorder who is not the approver, the patch author, a
     wrong or missing commit SHA, and any HUMAN row in a session that
     declared itself the improvement runner.
  §6 PROVENANCE AND NO AUTHORITY. Cited records must exist; no authority /
     deploy / merge key in a body; a malformed PR URL or a patch reference at
     the wrong stage is refused.
  §7 DISAGREEMENTS ARE PRESERVED. Positions never change; a party cannot
     decide its own disagreement; only a party concedes; consensus only on a
     concession; a resolution is recorded once; nothing is deleted.
  §8 THE MIGRATION is idempotent; its rollback touches only improve_* and
     refuses while a human step or a disagreement is on the ledger.

ALL DATA IS SYNTHETIC TEST DATA, inside a transaction that is rolled back.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
T0 = 1_790_000_000.0
D1 = {"kind": "agent_decisions", "id": "adr:imp221-1"}
D2 = {"kind": "agent_decisions", "id": "adr:imp221-2"}
D3 = {"kind": "agent_decisions", "id": "adr:imp221-3"}
D4 = {"kind": "agent_decisions", "id": "adr:imp221-4"}
SHA = "a" * 40
OTHER_SHA = "b" * 40


async def _tx():
    import asyncpg

    from sportsassets.agents import registry as R
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    await R.ensure_identities(conn)
    for i, r in enumerate((D1, D2, D3, D4)):
        await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " decided_at) VALUES ($1,$2,'TEST',to_timestamp($3)) "
            "ON CONFLICT DO NOTHING", r["id"],
            ("DEREK", "XAVIER", "AUDREY", "DEREK")[i], T0)
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


async def _item(conn, iid="impr:" + "1" * 24, *, areas=(), owner="DEREK",
                reviews=None):
    areas = list(areas)
    await conn.execute(
        "INSERT INTO improve_items (item_id, source_kind, source_key, "
        " source_ref, title, problem_statement, statement_basis, owner_agent,"
        " evidence_refs, protected_areas, requires_human_review, "
        " required_independent_reviews, created_by, created_at, updated_at)"
        " VALUES ($1,'AGENT_FINDING',$1,$2::jsonb,'t','p',"
        " 'RUNNER_SUMMARY_OF_CITED_RECORDS',$3,$4::jsonb,$5::text[],$6,$7,"
        " 'IMPROVEMENT_PIPELINE',to_timestamp($8),to_timestamp($8))",
        iid, json.dumps(D1), owner, json.dumps([D1]), areas, bool(areas),
        reviews or (2 if areas else 1), T0)
    return iid


EV_SQL = (
    "INSERT INTO improve_events (item_id, stage, stage_seq, actor, "
    " actor_class, at, body, source_ref, evidence_refs, stance, outcome, "
    " experiment_refs, patch_branch, patch_commit_sha, patch_pr_url, "
    " tests_ref, gate_receipt_ref, gate_receipt_sha, release_ref, "
    " monitoring_start, monitoring_end, forward_result, rollback_ref, "
    " recorded_by) VALUES ($1,$2,improve_stage_seq($2),$3,$4,"
    " to_timestamp($5),$6::jsonb,$7::jsonb,$8::jsonb,$9,$10,$11::jsonb,$12,"
    " $13,$14,$15,$16,$17,$18,to_timestamp($19),to_timestamp($20),"
    " $21::jsonb,$22,$23)")

AGENT_CLASSES = ("RUNNER", "OWNER_AGENT", "PEER_AGENT", "CHALLENGER",
                 "INDEPENDENT_EVALUATOR")
DEFAULT_BODY = {"HYPOTHESIS": {"hypothesis": "h"},
                "PEER_CHALLENGE": {"challenge": "c"},
                "OWNER_RESPONSE": {"response": "r"},
                "CLOSED": {"reason": "done"},
                "ROLLED_BACK": {"reason": "degraded"}}


def _args(iid, stage, actor, cls, at, **kw):
    src = kw.pop("source_ref", D1 if cls in AGENT_CLASSES else None)
    return (iid, stage, actor, cls, T0 + at,
            json.dumps(kw.pop("body", DEFAULT_BODY.get(stage, {}))),
            json.dumps(src) if src else None,
            json.dumps(kw.pop("evidence_refs", [])), kw.pop("stance", None),
            kw.pop("outcome", None),
            json.dumps(kw["experiment_refs"]) if kw.get("experiment_refs")
            else None, kw.pop("patch_branch", None),
            kw.pop("patch_commit_sha", None), kw.pop("patch_pr_url", None),
            kw.pop("tests_ref", None), kw.pop("gate_receipt_ref", None),
            kw.pop("gate_receipt_sha", None), kw.pop("release_ref", None),
            kw.pop("monitoring_start", None), kw.pop("monitoring_end", None),
            json.dumps(kw["forward_result"]) if kw.get("forward_result")
            else None, kw.pop("rollback_ref", None),
            kw.pop("recorded_by", None) or (
                actor if cls in ("HUMAN", "ENGINEERING")
                else "IMPROVEMENT_PIPELINE"))


async def _ev(conn, iid, stage, actor, cls, at, **kw):
    await conn.execute(EV_SQL, *_args(iid, stage, actor, cls, at, **kw))


async def _no(conn, iid, stage, actor, cls, at, match=None, **kw):
    await _expect(conn, EV_SQL, *_args(iid, stage, actor, cls, at, **kw),
                  match=match)


async def _stage(conn, iid):
    return await conn.fetchval("SELECT stage FROM improve_items "
                               " WHERE item_id=$1", iid)


async def _to_experiment(conn, iid, *, patch=True):
    await _ev(conn, iid, "EVIDENCE", "IMPROVEMENT_PIPELINE", "RUNNER", 1)
    await _ev(conn, iid, "HYPOTHESIS", "DEREK", "OWNER_AGENT", 2)
    await _ev(conn, iid, "PEER_CHALLENGE", "KAREN", "CHALLENGER", 3,
              stance="CHALLENGES", source_ref=D2)
    await _ev(conn, iid, "PEER_CHALLENGE", "XAVIER", "PEER_AGENT", 4,
              stance="SUSTAINED", source_ref=D3)
    await _ev(conn, iid, "OWNER_RESPONSE", "DEREK", "OWNER_AGENT", 5,
              stance="REVISE", source_ref=D2)
    if patch:
        await _ev(conn, iid, "EXPERIMENT", "Jane Engineer", "ENGINEERING", 6,
                  patch_branch="claude/fix-coverage", patch_commit_sha=SHA,
                  patch_pr_url="https://github.com/o/r/pull/12",
                  tests_ref="tests/test_fix.py::test_it")


# ── §1 the full path ─────────────────────────────────────────────────

@pg
async def test_the_full_canonical_path_with_a_human_release_and_rollback():
    conn, tx = await _tx()
    try:
        iid = await _item(conn)
        await _to_experiment(conn, iid)
        assert await _stage(conn, iid) == "EXPERIMENT"
        await _ev(conn, iid, "INDEPENDENT_EVALUATION", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 7, outcome="PASS", source_ref=D3)
        await _ev(conn, iid, "ELIGIBLE_CHANGE", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 8, source_ref=D3)
        await _ev(conn, iid, "CONTROLLED_RELEASE", "Matt Taylor", "HUMAN", 9,
                  gate_receipt_ref="gate run 4711", gate_receipt_sha=SHA,
                  release_ref="render deploy dep-123")
        await _ev(conn, iid, "FORWARD_RESULT", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 100, outcome="DEGRADED",
                  source_ref=D3, monitoring_start=T0 + 10,
                  monitoring_end=T0 + 90,
                  forward_result={"metric": "coverage_ratio", "value": 0.2})
        await _ev(conn, iid, "ROLLED_BACK", "Jane Engineer", "ENGINEERING",
                  101, rollback_ref="revert commit " + OTHER_SHA)
        assert await _stage(conn, iid) == "ROLLED_BACK"
        rows = await conn.fetch(
            "SELECT stage, is_transition, from_stage FROM improve_events "
            " WHERE item_id=$1 ORDER BY event_id", iid)
        assert [r["stage"] for r in rows if r["is_transition"]] == [
            "EVIDENCE", "HYPOTHESIS", "PEER_CHALLENGE", "OWNER_RESPONSE",
            "EXPERIMENT", "INDEPENDENT_EVALUATION", "ELIGIBLE_CHANGE",
            "CONTROLLED_RELEASE", "FORWARD_RESULT", "ROLLED_BACK"]
        # the second peer challenge is not a transition (one Slack line)
        assert sum(1 for r in rows if r["stage"] == "PEER_CHALLENGE"
                   and not r["is_transition"]) == 1
        assert {r["production_effect"] for r in await conn.fetch(
            "SELECT production_effect FROM improve_events")} == {"NONE"}
        # terminal: nothing follows a rollback
        await _no(conn, iid, "CLOSED", "Matt Taylor", "HUMAN", 102,
                  match="terminal")
    finally:
        await tx.rollback()
        await conn.close()


# ── §2 no self-approval ──────────────────────────────────────────────

@pg
async def test_no_proposer_or_author_evaluates_or_marks_its_own_change():
    conn, tx = await _tx()
    try:
        iid = await _item(conn)
        await _to_experiment(conn, iid)
        # the owner, as an evaluator agent or as a "human"
        await _no(conn, iid, "INDEPENDENT_EVALUATION", "DEREK",
                  "INDEPENDENT_EVALUATOR", 7, outcome="PASS",
                  match="cannot evaluate")
        await _no(conn, iid, "INDEPENDENT_EVALUATION", "DEREK", "HUMAN", 7,
                  outcome="PASS", match="not a human")
        # the patch author
        await _no(conn, iid, "INDEPENDENT_EVALUATION", "Jane Engineer",
                  "HUMAN", 7, outcome="PASS", match="proposed or authored")
        # Karen, Eddie and Scout never evaluate
        for who in ("KAREN", "EDDIE", "SCOUT"):
            await _no(conn, iid, "INDEPENDENT_EVALUATION", who,
                      "INDEPENDENT_EVALUATOR", 7, outcome="PASS")
        # an evaluation must come first; an INCONCLUSIVE is not a PASS
        await _no(conn, iid, "ELIGIBLE_CHANGE", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 7, match="skipped")
        await _ev(conn, iid, "INDEPENDENT_EVALUATION", "XAVIER",
                  "INDEPENDENT_EVALUATOR", 7, outcome="INCONCLUSIVE",
                  source_ref=D2)
        await _no(conn, iid, "ELIGIBLE_CHANGE", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 7, source_ref=D3,
                  match="ELIGIBLE_CHANGE needs")
        await _ev(conn, iid, "INDEPENDENT_EVALUATION", "XAVIER",
                  "INDEPENDENT_EVALUATOR", 7, outcome="FAIL", source_ref=D1)
        await _ev(conn, iid, "INDEPENDENT_EVALUATION", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 8, outcome="PASS", source_ref=D3)
        # a reviewer's latest outcome is FAIL
        await _no(conn, iid, "ELIGIBLE_CHANGE", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 9, source_ref=D3,
                  match="FAIL")
        # the owner can never mark it, even as the "owner agent"
        await _no(conn, iid, "ELIGIBLE_CHANGE", "DEREK", "OWNER_AGENT", 9)
        await _ev(conn, iid, "INDEPENDENT_EVALUATION", "XAVIER",
                  "INDEPENDENT_EVALUATOR", 9, outcome="PASS", source_ref=D4)
        await _no(conn, iid, "ELIGIBLE_CHANGE", "Jane Engineer", "HUMAN", 10,
                  match="proposed or authored")
        await _ev(conn, iid, "ELIGIBLE_CHANGE", "XAVIER",
                  "INDEPENDENT_EVALUATOR", 10, source_ref=D4)
        assert await _stage(conn, iid) == "ELIGIBLE_CHANGE"
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_the_hypothesis_author_never_evaluates():
    conn, tx = await _tx()
    try:
        iid = await _item(conn, owner="XAVIER")
        await _ev(conn, iid, "EVIDENCE", "IMPROVEMENT_PIPELINE", "RUNNER", 1)
        await _no(conn, iid, "HYPOTHESIS", "DEREK", "OWNER_AGENT", 2,
                  match="not the owner")
        await _no(conn, iid, "HYPOTHESIS", "IMPROVEMENT_PIPELINE", "RUNNER",
                  2, match="HYPOTHESIS is the owner")
        await _ev(conn, iid, "HYPOTHESIS", "XAVIER", "OWNER_AGENT", 2)
        await _no(conn, iid, "PEER_CHALLENGE", "XAVIER", "PEER_AGENT", 3,
                  stance="SUSTAINED", match="PEER_AGENT")
        await _no(conn, iid, "PEER_CHALLENGE", "XAVIER", "OWNER_AGENT", 3,
                  stance="SUSTAINED")
    finally:
        await tx.rollback()
        await conn.close()


# ── §3 protected areas ───────────────────────────────────────────────

@pg
async def test_a_protected_area_needs_two_reviews_and_a_human():
    conn, tx = await _tx()
    try:
        # the CHECK forces the human review and two reviews
        await _expect(conn, (
            "INSERT INTO improve_items (item_id, source_kind, source_key, "
            " source_ref, title, problem_statement, statement_basis, "
            " owner_agent, evidence_refs, protected_areas, "
            " requires_human_review, required_independent_reviews, "
            " created_by, created_at, updated_at) VALUES ('impr:%s',"
            " 'AGENT_FINDING','k',$1::jsonb,'t','p','HUMAN','DEREK',"
            " $2::jsonb,ARRAY['SETTLEMENT'],false,1,'Matt Taylor',now(),"
            " now())" % ("9" * 24)), json.dumps(D1), json.dumps([D1]),
            match="improve_items_protected_ck")
        iid = await _item(conn, areas=("SETTLEMENT", "ACCOUNTING"))
        await _to_experiment(conn, iid)
        await _ev(conn, iid, "INDEPENDENT_EVALUATION", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 7, outcome="PASS", source_ref=D3)
        await _no(conn, iid, "ELIGIBLE_CHANGE", "Matt Taylor", "HUMAN", 8,
                  match="needs 2")
        # the same reviewer twice is still one review
        await _ev(conn, iid, "INDEPENDENT_EVALUATION", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 8, outcome="PASS", source_ref=D4)
        await _no(conn, iid, "ELIGIBLE_CHANGE", "Matt Taylor", "HUMAN", 9,
                  match="needs 2")
        await _ev(conn, iid, "INDEPENDENT_EVALUATION", "Rita Reviewer",
                  "HUMAN", 9, outcome="PASS", body={"reason": "read the diff"})
        # two passes, but an agent cannot mark a protected item eligible
        await _no(conn, iid, "ELIGIBLE_CHANGE", "XAVIER",
                  "INDEPENDENT_EVALUATOR", 10, source_ref=D2,
                  match="a HUMAN records its eligibility")
        await _ev(conn, iid, "ELIGIBLE_CHANGE", "Matt Taylor", "HUMAN", 10)
        assert await _stage(conn, iid) == "ELIGIBLE_CHANGE"
        # the classification escalates, never relaxes
        await _expect(conn, "UPDATE improve_items SET protected_areas='{}', "
                            " requires_human_review=false WHERE item_id=$1",
                      iid, match="only be escalated")
        await _expect(conn, "UPDATE improve_items SET "
                            " required_independent_reviews=1 "
                            " WHERE item_id=$1", iid)
        await conn.execute(
            "UPDATE improve_items SET protected_areas = protected_areas || "
            " ARRAY['RISK'], required_independent_reviews = 3 "
            " WHERE item_id=$1", iid)
    finally:
        await tx.rollback()
        await conn.close()


# ── §4 forward only ──────────────────────────────────────────────────

@pg
async def test_stages_move_forward_only_and_are_never_skipped():
    conn, tx = await _tx()
    try:
        iid = await _item(conn)
        await _no(conn, iid, "HYPOTHESIS", "DEREK", "OWNER_AGENT", 1,
                  match="skipped")
        await _no(conn, iid, "CLOSED", "IMPROVEMENT_PIPELINE", "RUNNER", 1,
                  match="EVIDENCE first")
        await _ev(conn, iid, "EVIDENCE", "IMPROVEMENT_PIPELINE", "RUNNER", 1)
        await _no(conn, iid, "PEER_CHALLENGE", "KAREN", "CHALLENGER", 2,
                  stance="CHALLENGES", match="skipped")
        await _ev(conn, iid, "HYPOTHESIS", "DEREK", "OWNER_AGENT", 2)
        await _no(conn, iid, "HYPOTHESIS", "DEREK", "OWNER_AGENT", 3,
                  source_ref=D4, match="recorded once")
        await _ev(conn, iid, "PEER_CHALLENGE", "KAREN", "CHALLENGER", 3,
                  stance="CHALLENGES", source_ref=D2)
        # going back to EVIDENCE is refused
        await _no(conn, iid, "EVIDENCE", "IMPROVEMENT_PIPELINE", "RUNNER", 4,
                  source_ref=D3, match="only move forward")
        # the owner answers only after Karen AND a peer challenged
        await _no(conn, iid, "OWNER_RESPONSE", "DEREK", "OWNER_AGENT", 4,
                  stance="DISPUTE", source_ref=D2, match="BOTH")
        await _ev(conn, iid, "PEER_CHALLENGE", "AUDREY", "PEER_AGENT", 4,
                  stance="REFUTED", source_ref=D3)
        # an event cannot predate the one before it
        await _no(conn, iid, "OWNER_RESPONSE", "DEREK", "OWNER_AGENT", 3.5,
                  stance="DISPUTE", source_ref=D2, match="predate")
        await _ev(conn, iid, "OWNER_RESPONSE", "DEREK", "OWNER_AGENT", 5,
                  stance="DISPUTE", source_ref=D2)
        # the same source cited twice at the same stage is one record
        await _no(conn, iid, "OWNER_RESPONSE", "DEREK", "OWNER_AGENT", 6,
                  stance="DISPUTE", source_ref=D2)
        # ROLLED_BACK only after a release
        await _no(conn, iid, "ROLLED_BACK", "Jane Engineer", "ENGINEERING",
                  6, rollback_ref="x", match="released")
        # append-only, and the stage cannot be set by hand
        await _expect(conn, "UPDATE improve_events SET actor='XAVIER' "
                            " WHERE item_id=$1", iid, match="append-only")
        await _expect(conn, "DELETE FROM improve_events WHERE item_id=$1",
                      iid, match="append-only")
        await _expect(conn, "UPDATE improve_items SET stage='EXPERIMENT', "
                            " stage_seq=5 WHERE item_id=$1", iid,
                      match="no transition event")
        await _expect(conn, "UPDATE improve_items SET title='x' "
                            " WHERE item_id=$1", iid, match="fixed")
        await _expect(conn, "DELETE FROM improve_items WHERE item_id=$1",
                      iid, match="never deleted")
        await _expect(conn, "TRUNCATE improve_events CASCADE",
                      match="TRUNCATE refused")
        await _ev(conn, iid, "CLOSED", "DEREK", "OWNER_AGENT", 7,
                  source_ref=D4)
        await _no(conn, iid, "EXPERIMENT", "Jane Engineer", "ENGINEERING", 8,
                  tests_ref="t", match="terminal")
    finally:
        await tx.rollback()
        await conn.close()


# ── §5 the human approver ───────────────────────────────────────────

@pg
async def test_a_controlled_release_needs_a_human_and_the_exact_sha():
    conn, tx = await _tx()
    try:
        iid = await _item(conn)
        await _to_experiment(conn, iid)
        await _ev(conn, iid, "INDEPENDENT_EVALUATION", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 7, outcome="PASS", source_ref=D3)
        await _ev(conn, iid, "ELIGIBLE_CHANGE", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 8, source_ref=D3)
        rel = dict(gate_receipt_ref="gate 1", gate_receipt_sha=SHA,
                   release_ref="deploy 1")
        await _no(conn, iid, "CONTROLLED_RELEASE", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 9, source_ref=D3,
                  match="HUMAN approver", **rel)
        for machine in ("claude", "SYSTEM", "release-bot", "agent:derek",
                        "Derek-agent", "github-actions[bot]", "deploy-runner",
                        "IMPROVEMENT_PIPELINE", "ci", "  "):
            await _no(conn, iid, "CONTROLLED_RELEASE", machine, "HUMAN", 9,
                      match="not a human" if machine.strip() else None,
                      **rel)
        await _no(conn, iid, "CONTROLLED_RELEASE", "Matt Taylor", "HUMAN", 9,
                  recorded_by="IMPROVEMENT_PIPELINE", match="not a human",
                  **rel)
        await _no(conn, iid, "CONTROLLED_RELEASE", "Matt Taylor", "HUMAN", 9,
                  gate_receipt_ref="gate 1", gate_receipt_sha=OTHER_SHA,
                  release_ref="deploy 1", match="exact SHA")
        await _no(conn, iid, "CONTROLLED_RELEASE", "Matt Taylor", "HUMAN", 9,
                  gate_receipt_ref="gate 1", release_ref="deploy 1",
                  match="exact SHA")
        await _no(conn, iid, "CONTROLLED_RELEASE", "Jane Engineer", "HUMAN",
                  9, match="patch author", **rel)
        # the runner's session can never record a human step
        sp = conn.transaction()
        await sp.start()
        await conn.execute("SELECT set_config('bettor.improvement_runner',"
                           " 'on', true)")
        await _no(conn, iid, "CONTROLLED_RELEASE", "Matt Taylor", "HUMAN", 9,
                  match="improvement runner cannot", **rel)
        await sp.rollback()
        await _ev(conn, iid, "CONTROLLED_RELEASE", "Matt Taylor", "HUMAN", 9,
                  **rel)
        await _no(conn, iid, "CONTROLLED_RELEASE", "Matt Taylor", "HUMAN", 10,
                  match="recorded once", **rel)
        # a monitoring window starts at or after the release
        await _no(conn, iid, "FORWARD_RESULT", "Matt Taylor", "HUMAN", 20,
                  outcome="HELD", monitoring_start=T0 + 8,
                  monitoring_end=T0 + 19, forward_result={"n": 1},
                  match="after the release")
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_no_release_without_a_recorded_patch_sha():
    conn, tx = await _tx()
    try:
        iid = await _item(conn)
        await _to_experiment(conn, iid, patch=False)
        await _ev(conn, iid, "EXPERIMENT", "DEREK", "OWNER_AGENT", 6,
                  source_ref=D4, experiment_refs=[D4])
        await _ev(conn, iid, "INDEPENDENT_EVALUATION", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 7, outcome="PASS", source_ref=D3)
        await _ev(conn, iid, "ELIGIBLE_CHANGE", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 8, source_ref=D3)
        await _no(conn, iid, "CONTROLLED_RELEASE", "Matt Taylor", "HUMAN", 9,
                  gate_receipt_ref="g", gate_receipt_sha=SHA,
                  release_ref="r", match="no candidate patch")
    finally:
        await tx.rollback()
        await conn.close()


# ── §6 provenance and no authority ──────────────────────────────────

@pg
async def test_cited_records_must_exist_and_no_authority_key_rides_along():
    conn, tx = await _tx()
    try:
        ghost = {"kind": "agent_decisions", "id": "adr:does-not-exist"}
        await _expect(conn, (
            "INSERT INTO improve_items (item_id, source_kind, source_key, "
            " source_ref, title, problem_statement, statement_basis, "
            " owner_agent, evidence_refs, protected_areas, "
            " requires_human_review, required_independent_reviews, "
            " created_by, created_at, updated_at) VALUES ('impr:%s',"
            " 'AGENT_FINDING','k',$1::jsonb,'t','p','AGENT_RECORD','DEREK',"
            " $2::jsonb,'{}',false,1,'IMPROVEMENT_PIPELINE',now(),now())"
            % ("8" * 24)), json.dumps(D1), json.dumps([D1, ghost]),
            match="does not exist")
        await _expect(conn, (
            "INSERT INTO improve_items (item_id, source_kind, source_key, "
            " source_ref, title, problem_statement, statement_basis, "
            " owner_agent, evidence_refs, protected_areas, "
            " requires_human_review, required_independent_reviews, "
            " created_by, created_at, updated_at) VALUES ('impr:%s',"
            " 'AGENT_FINDING','k',$1::jsonb,'t','p','AGENT_RECORD','DEREK',"
            " $2::jsonb,'{}',false,1,'IMPROVEMENT_PIPELINE',now(),now())"
            % ("7" * 24)), json.dumps({"kind": "made_up", "id": "1"}),
            json.dumps([D1]), match="does not exist")
        iid = await _item(conn)
        await _no(conn, iid, "EVIDENCE", "IMPROVEMENT_PIPELINE", "RUNNER", 1,
                  source_ref=ghost, match="does not exist")
        await _no(conn, iid, "EVIDENCE", "IMPROVEMENT_PIPELINE", "RUNNER", 1,
                  source_ref=None, match="provenance")
        for key in ("deploy", "merge", "push", "approved_by", "order",
                    "max_order_usd", "activation"):
            await _no(conn, iid, "EVIDENCE", "IMPROVEMENT_PIPELINE", "RUNNER",
                      1, body={"note": {key: True}},
                      match="improve_events_body_ck")
        await _ev(conn, iid, "EVIDENCE", "IMPROVEMENT_PIPELINE", "RUNNER", 1)
        # a patch reference belongs to EXPERIMENT, and is well-formed
        await _no(conn, iid, "HYPOTHESIS", "DEREK", "OWNER_AGENT", 2,
                  patch_commit_sha=SHA, match="fields_by_stage")
        await _ev(conn, iid, "HYPOTHESIS", "DEREK", "OWNER_AGENT", 2)
        await _ev(conn, iid, "PEER_CHALLENGE", "KAREN", "CHALLENGER", 3,
                  stance="CHALLENGES", source_ref=D2)
        await _ev(conn, iid, "PEER_CHALLENGE", "XAVIER", "PEER_AGENT", 4,
                  stance="SUSTAINED", source_ref=D3)
        await _ev(conn, iid, "OWNER_RESPONSE", "DEREK", "OWNER_AGENT", 5,
                  stance="CONCEDE", source_ref=D2)
        for bad in (dict(patch_pr_url="https://evil.example/pull/1"),
                    dict(patch_commit_sha="abc123"),
                    dict(patch_branch="-rf /"),
                    dict(patch_pr_url="https://github.com/o/r/pull/1; rm")):
            await _no(conn, iid, "EXPERIMENT", "Jane Engineer",
                      "ENGINEERING", 6, tests_ref="t",
                      match="improve_events_patch_ck", **bad)
        # an experiment names something to evaluate
        await _no(conn, iid, "EXPERIMENT", "Jane Engineer", "ENGINEERING", 6,
                  match="improve_events_experiment_ck")
    finally:
        await tx.rollback()
        await conn.close()


# ── §7 disagreements ─────────────────────────────────────────────────

DIS_SQL = (
    "INSERT INTO improve_disagreements (disagreement_id, item_id, stage, "
    " parties, positions, opened_at, recorded_by, state, resolved_by, "
    " resolution, resolved_at, resolution_ref, consensus) VALUES ($1,$2,"
    " 'OWNER_RESPONSE',ARRAY['KAREN','DEREK'],$3::jsonb,to_timestamp($4),"
    " 'IMPROVEMENT_PIPELINE',$5,$6,$7,to_timestamp($8),$9::jsonb,$10)")


def _pos():
    return json.dumps([
        {"party": "KAREN", "position": "the record is defective",
         "source_ref": D2},
        {"party": "DEREK", "position": "the record is sound",
         "source_ref": D1}])


@pg
async def test_disagreements_are_preserved_and_never_called_consensus():
    conn, tx = await _tx()
    try:
        iid = await _item(conn)
        args = lambda did, st="OPEN", by=None, res=None, ref=None, c=False: (
            did, iid, _pos(), T0, st, by, res, T0 + 5 if by else None,
            json.dumps(ref) if ref else None, c)
        await conn.execute(DIS_SQL, *args("dis:1"))
        # the positions are preserved
        await _expect(conn, "UPDATE improve_disagreements SET positions="
                            "'[]'::jsonb WHERE disagreement_id='dis:1'",
                      match="preserved")
        await _expect(conn, "UPDATE improve_disagreements SET parties="
                            "ARRAY['KAREN','XAVIER'] "
                            " WHERE disagreement_id='dis:1'",
                      match="preserved")
        # a party cannot decide; only a party concedes; consensus only on
        # a concession
        await _expect(conn, "UPDATE improve_disagreements SET state="
                            "'DECIDED', resolved_by='DEREK', resolution='x',"
                            " resolved_at=now(), resolution_ref=$1::jsonb "
                            " WHERE disagreement_id='dis:1'",
                      json.dumps(D3), match="cannot decide")
        await _expect(conn, "UPDATE improve_disagreements SET state="
                            "'CONCEDED', resolved_by='AUDREY', resolution="
                            "'x', resolved_at=now(), resolution_ref="
                            "$1::jsonb WHERE disagreement_id='dis:1'",
                      json.dumps(D3), match="only a party concedes")
        await _expect(conn, "UPDATE improve_disagreements SET state="
                            "'DECIDED', resolved_by='AUDREY', resolution="
                            "'x', resolved_at=now(), resolution_ref="
                            "$1::jsonb, consensus=true "
                            " WHERE disagreement_id='dis:1'",
                      json.dumps(D3), match="consensus")
        # an agent's resolution cites its record
        await _expect(conn, "UPDATE improve_disagreements SET state="
                            "'DECIDED', resolved_by='AUDREY', resolution="
                            "'x', resolved_at=now() "
                            " WHERE disagreement_id='dis:1'",
                      match="cites its record")
        await conn.execute(
            "UPDATE improve_disagreements SET state='DECIDED', "
            " resolved_by='AUDREY', resolution='upheld by evaluation', "
            " resolved_at=now(), resolution_ref=$1::jsonb "
            " WHERE disagreement_id='dis:1'", json.dumps(D3))
        r = await conn.fetchrow("SELECT * FROM improve_disagreements "
                                " WHERE disagreement_id='dis:1'")
        assert r["consensus"] is False and r["state"] == "DECIDED"
        assert len(json.loads(r["positions"])) == 2      # both kept
        await _expect(conn, "UPDATE improve_disagreements SET resolution="
                            "'rewritten' WHERE disagreement_id='dis:1'",
                      match="already DECIDED")
        await _expect(conn, "DELETE FROM improve_disagreements",
                      match="never deleted")
        # a position must cite an existing record; one party is no dispute
        await _expect(conn, DIS_SQL, "dis:2", iid, json.dumps([
            {"party": "KAREN", "position": "x",
             "source_ref": {"kind": "agent_decisions", "id": "nope"}},
            {"party": "DEREK", "position": "y", "source_ref": D1}]), T0,
            "OPEN", None, None, None, None, False,
            match="cites no existing record")
        await _expect(conn, DIS_SQL.replace("ARRAY['KAREN','DEREK']",
                                            "ARRAY['KAREN','karen']"),
                      *args("dis:3"), match="parties_ck")
        # a concession by a party may be consensus
        await conn.execute(DIS_SQL, *args("dis:4", "CONCEDED", "DEREK",
                                          "conceded", D2, True))
    finally:
        await tx.rollback()
        await conn.close()


# ── §8 the migration ─────────────────────────────────────────────────

@pg
async def test_migration_221_is_idempotent_and_its_rollback_is_scoped():
    up = (MIG / "221_improvement_pipeline.sql").read_text()
    down = (MIG / "rollback" / "221_improvement_pipeline.down.sql"
            ).read_text()
    assert "improvement_events" not in down.replace(
        "migration 155's improvement_* tables", "")
    conn, tx = await _tx()
    try:
        await conn.execute(up)
        await conn.execute(up)
        await conn.execute(down)
        for t in ("improve_items", "improve_events", "improve_disagreements",
                  "improve_runs"):
            assert await conn.fetchval("SELECT to_regclass($1)", t) is None
        # migration 155's own improvement_events is untouched
        assert await conn.fetchval(
            "SELECT to_regclass('improvement_events')") is not None
        await conn.execute(up)
        iid = await _item(conn)
        await _to_experiment(conn, iid)             # a human/engineering row
        await _expect(conn, down, match="rollback refused")
    finally:
        await tx.rollback()
        await conn.close()
