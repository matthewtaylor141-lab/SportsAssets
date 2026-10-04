"""THE IMPROVEMENT BOARD SAYS ONLY WHAT THE LEDGER RECORDS
(GET /api/command/improvements and /{id}; agents/improvement_stages.py).

  §1 PURE RULES. next_required names who must act at every stage (e.g.
     "AWAITING HUMAN APPROVAL ...", "AWAITING INDEPENDENT EVALUATION
     (AUDREY)", a protected item's "+ HUMAN REVIEWER" and "SECOND
     INDEPENDENT REVIEW (1 OF 2)"); the pure guard refuses what the database
     refuses; the machine-actor predicate matches the database's; the
     protected classification is conservative; a Slack line carries ids,
     the trail link and the no-authority sentence.
  §2 THE BOARD over a fixture ledger (rolled back): one card per item with
     stage, owner, evidence count, protected lock, preserved disagreement and
     next actor; stage columns counted; runner freshness from its run row.
  §3 THE TRAIL: every event with its source / evidence links, the patch as a
     reference only, the canonical path with reached / current flags, the
     disagreement with both positions and its resolution (never consensus),
     each cited record checked to exist.
"""
from __future__ import annotations

import json
import os

import pytest

from sportsassets.agents import improvement_stages as S
from sportsassets.api import command_improvements as CI
from tests import test_improvement_pipeline_db as DBT

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


def _ev(stage, actor, cls, n, **kw):
    return dict(event_id=n, stage=stage, actor=actor, actor_class=cls,
                is_transition=True, **kw)


def _item(stage, *, areas=()):
    return {"item_id": "impr:" + "a" * 24, "owner_agent": "DEREK",
            "stage": stage, "requires_human_review": bool(areas),
            "required_independent_reviews": 2 if areas else 1,
            "protected_areas": list(areas)}


# ── §1 pure ──────────────────────────────────────────────────────────

def test_next_required_names_who_must_act():
    lbl = lambda st, ev=(), **kw: S.next_required(_item(st, **kw),
                                                  list(ev))["label"]
    assert lbl(S.EVIDENCE) == "AWAITING HYPOTHESIS (DEREK)"
    assert lbl(S.HYPOTHESIS) == "AWAITING PEER CHALLENGE (KAREN + XAVIER)"
    k = _ev(S.PEER_CHALLENGE, "KAREN", S.CHALLENGER, 3)
    p = _ev(S.PEER_CHALLENGE, "XAVIER", S.PEER_AGENT, 4)
    assert lbl(S.PEER_CHALLENGE, [k]) == "AWAITING PEER CHALLENGE (XAVIER)"
    assert lbl(S.PEER_CHALLENGE, [p]) == "AWAITING PEER CHALLENGE (KAREN)"
    assert lbl(S.PEER_CHALLENGE, [k, p]) == "AWAITING OWNER RESPONSE (DEREK)"
    assert lbl(S.OWNER_RESPONSE).startswith("AWAITING EXPERIMENT / CANDIDATE")
    assert lbl(S.EXPERIMENT) == "AWAITING INDEPENDENT EVALUATION (AUDREY)"
    assert lbl(S.EXPERIMENT, areas=["SETTLEMENT"]) == \
        "AWAITING INDEPENDENT EVALUATION (AUDREY + HUMAN REVIEWER)"
    one = _ev(S.INDEPENDENT_EVALUATION, "AUDREY", S.INDEPENDENT_EVALUATOR, 7,
              outcome="PASS")
    assert lbl(S.INDEPENDENT_EVALUATION, [one], areas=["RISK"]) == \
        "AWAITING SECOND INDEPENDENT REVIEW (1 OF 2)"
    two = _ev(S.INDEPENDENT_EVALUATION, "Rita Reviewer", S.HUMAN, 8,
              outcome="PASS")
    assert lbl(S.INDEPENDENT_EVALUATION, [one, two], areas=["RISK"]) == \
        "AWAITING HUMAN REVIEW (PROTECTED AREA)"
    assert lbl(S.INDEPENDENT_EVALUATION, [one]) == \
        "AWAITING ELIGIBILITY MARK (AUDREY)"
    fail = _ev(S.INDEPENDENT_EVALUATION, "XAVIER", S.INDEPENDENT_EVALUATOR,
               9, outcome="FAIL")
    assert lbl(S.INDEPENDENT_EVALUATION, [one, fail]).startswith(
        "EVALUATION FAILED")
    assert lbl(S.ELIGIBLE_CHANGE).startswith("AWAITING HUMAN APPROVAL")
    assert S.next_required(_item(S.ELIGIBLE_CHANGE), [])["actor_class"] == \
        S.HUMAN
    assert lbl(S.CONTROLLED_RELEASE).startswith("AWAITING FORWARD RESULT")
    deg = _ev(S.FORWARD_RESULT, "AUDREY", S.INDEPENDENT_EVALUATOR, 10,
              outcome="DEGRADED")
    assert lbl(S.FORWARD_RESULT, [deg]) == \
        "AWAITING ROLLBACK DECISION (HUMAN)"
    assert lbl(S.CLOSED).startswith("CLOSED")
    # Eddie's evaluator is Audrey; Audrey's is Xavier
    it = dict(_item(S.EXPERIMENT), owner_agent="AUDREY")
    assert S.next_required(it, [])["label"] == \
        "AWAITING INDEPENDENT EVALUATION (XAVIER)"


def test_the_pure_guard_refuses_what_the_database_refuses():
    it = _item(S.EXPERIMENT)
    hyp = _ev(S.HYPOTHESIS, "DEREK", S.OWNER_AGENT, 2)
    exp = _ev(S.EXPERIMENT, "Jane Engineer", S.ENGINEERING, 6)
    ev = [hyp, exp]
    src = {"kind": "agent_decisions", "id": "x"}
    new = lambda **kw: dict({"stage": S.INDEPENDENT_EVALUATION,
                             "outcome": "PASS", "source_ref": src}, **kw)
    assert S.check_event(it, ev, new(actor="DEREK",
                                     actor_class=S.INDEPENDENT_EVALUATOR)) \
        == S.R_BAD_CLASS
    assert S.check_event(it, ev, new(actor="Jane Engineer",
                                     actor_class=S.HUMAN)) == S.R_SELF_REVIEW
    assert S.check_event(it, ev, new(actor="KAREN",
                                     actor_class=S.INDEPENDENT_EVALUATOR)) \
        == S.R_BAD_CLASS
    assert S.check_event(it, ev, new(actor="AUDREY",
                                     actor_class=S.INDEPENDENT_EVALUATOR)) \
        is None
    assert S.check_event(it, ev, {"stage": S.ELIGIBLE_CHANGE,
                                  "actor": "AUDREY", "source_ref": src,
                                  "actor_class": S.INDEPENDENT_EVALUATOR}) \
        == S.R_SKIPPED
    assert S.check_event(it, ev, {"stage": S.HYPOTHESIS, "actor": "DEREK",
                                  "actor_class": S.OWNER_AGENT,
                                  "source_ref": src}) == S.R_BACKWARDS
    assert S.check_event(it, ev, {"stage": S.INDEPENDENT_EVALUATION,
                                  "actor": "AUDREY",
                                  "actor_class": S.INDEPENDENT_EVALUATOR}) \
        == S.R_NO_SOURCE
    prot = _item(S.INDEPENDENT_EVALUATION, areas=["CREDENTIALS"])
    passes = [_ev(S.INDEPENDENT_EVALUATION, "AUDREY", S.INDEPENDENT_EVALUATOR,
                  7, outcome="PASS"),
              _ev(S.INDEPENDENT_EVALUATION, "XAVIER", S.INDEPENDENT_EVALUATOR,
                  8, outcome="PASS")]
    assert S.check_event(prot, passes[:1], {
        "stage": S.ELIGIBLE_CHANGE, "actor": "Matt Taylor",
        "actor_class": S.HUMAN}) == S.R_NEEDS_PASS
    assert S.check_event(prot, passes, {
        "stage": S.ELIGIBLE_CHANGE, "actor": "AUDREY", "source_ref": src,
        "actor_class": S.INDEPENDENT_EVALUATOR}) == S.R_NEEDS_HUMAN
    assert S.check_event(prot, passes, {
        "stage": S.ELIGIBLE_CHANGE, "actor": "Matt Taylor",
        "actor_class": S.HUMAN}) is None
    resp = _item(S.PEER_CHALLENGE)
    assert S.check_event(resp, [_ev(S.PEER_CHALLENGE, "KAREN", S.CHALLENGER,
                                    3)], {
        "stage": S.OWNER_RESPONSE, "actor": "DEREK", "source_ref": src,
        "actor_class": S.OWNER_AGENT}) == S.R_NEEDS_BOTH


def test_protected_classification_is_conservative():
    areas, basis = S.classify_protected("RECONCILIATION_DISCREPANCY_OPEN")
    assert areas == ["ACCOUNTING"] and basis["ACCOUNTING"] == "reconcil"
    assert "SETTLEMENT" in S.classify_protected("settlement_supported")[0]
    assert "EXECUTION_AUTHORIZATION" in S.classify_protected(
        "ENTRY_WITHOUT_PROBABILITY", "derek_entry_decisions")[0]
    assert "CREDENTIALS" in S.classify_protected("api key rotation")[0]
    assert "LIVE_CAPITAL_LIMITS" in S.classify_protected(
        "smalllive mirror cap")[0]
    assert S.classify_protected("mapped", "ABSENT_DOWNSTREAM") == ([], {})
    assert S.required_reviews(["RISK"]) == 2 and S.required_reviews([]) == 1


def test_a_slack_line_carries_ids_links_and_no_authority():
    it = dict(_item(S.HYPOTHESIS), title="Coverage incident",
              evidence_refs=[{"kind": "coverage_collapse_alerts",
                              "id": "cca:1"}])
    ev = {"event_id": 5, "stage": S.HYPOTHESIS, "from_stage": S.EVIDENCE,
          "actor": "DEREK", "actor_class": S.OWNER_AGENT,
          "body": {"hypothesis": "a mapping defect"},
          "source_ref": {"kind": "agent_finding_stages", "id": "9"}}
    txt = S.slack_post(it, ev, S.next_required(it, [ev]))
    assert "impr:" + "a" * 24 in txt and "EVIDENCE -> HYPOTHESIS" in txt
    assert "agent_finding_stages 9" in txt and '"a mapping defect"' in txt
    assert "AWAITING PEER CHALLENGE" in txt
    assert "https://command.bettortoken.com/improvements?item=" in txt
    assert "nothing is approved, merged, deployed or activated" in txt


@pg
async def test_the_machine_actor_predicate_matches_the_database():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        for v in ("Matt Taylor", "matt@bettortoken.com", "Rita Reviewer",
                  "Abbot", "claude", "SYSTEM", "release-bot", "agent:derek",
                  "Derek-agent", "github-actions[bot]", "deploy-runner",
                  "IMPROVEMENT_PIPELINE", "ci", "", "Karen red team",
                  "scout research", "Eddie Ruiz", "pipeline:x", "Scheduler"):
            db = await conn.fetchval("SELECT improve_is_machine_actor($1)", v)
            assert db == S.is_machine_actor(v), v
    finally:
        await conn.close()


# ── §2 / §3 the board and the trail over a fixture ledger ───────────

async def _fixture(conn):
    """A protected item at ELIGIBLE_CHANGE awaiting a human approval, with
    a preserved dissent; a plain item at EVIDENCE."""
    a = await DBT._item(conn, "impr:" + "2" * 24, areas=("SETTLEMENT",))
    await DBT._to_experiment(conn, a)
    await DBT._ev(conn, a, "INDEPENDENT_EVALUATION", "AUDREY",
                  "INDEPENDENT_EVALUATOR", 7, outcome="PASS",
                  source_ref=DBT.D3)
    await DBT._ev(conn, a, "INDEPENDENT_EVALUATION", "Rita Reviewer",
                  "HUMAN", 8, outcome="PASS", body={"reason": "ok"})
    await DBT._ev(conn, a, "ELIGIBLE_CHANGE", "Matt Taylor", "HUMAN", 9)
    await conn.execute(
        "INSERT INTO improve_disagreements (disagreement_id, item_id, stage,"
        " parties, positions, opened_at, recorded_by, state, resolved_by, "
        " resolution, resolved_at, resolution_ref) VALUES ('dis:x',$1,"
        " 'OWNER_RESPONSE',ARRAY['KAREN','DEREK'],$2::jsonb,"
        " to_timestamp($3),'IMPROVEMENT_PIPELINE','DECIDED','AUDREY',"
        " 'upheld; the dissent stands on the record',to_timestamp($3)+"
        " interval '1 minute',$4::jsonb)", a, DBT._pos(), DBT.T0,
        json.dumps(DBT.D3))
    b = await DBT._item(conn, "impr:" + "3" * 24, owner="XAVIER")
    await DBT._ev(conn, b, "EVIDENCE", "IMPROVEMENT_PIPELINE", "RUNNER", 1)
    await conn.execute(
        "INSERT INTO improve_runs (run_id, started_at, finished_at, status, "
        " summary, version) VALUES ('improve-run:t', now(), now(), 'OK', "
        " '{}'::jsonb, 'TEST')")
    return a, b


@pg
async def test_the_board_cards_and_the_item_trail():
    conn, tx = await DBT._tx()
    try:
        a, b = await _fixture(conn)
        board = await CI.build_board(conn, limit=50)
        assert board["available"] is True and board["read_only"] is True
        cards = {c["item_id"]: c for c in board["items"]}
        ca, cb = cards[a], cards[b]
        assert ca["stage"] == "ELIGIBLE_CHANGE" and ca["owner_agent"] == \
            "DEREK"
        assert ca["protected"]["is_protected"] is True
        assert ca["protected"]["areas"] == ["SETTLEMENT"]
        assert ca["protected"]["required_independent_reviews"] == 2
        assert ca["next_required"]["code"] == "AWAITING_HUMAN_APPROVAL"
        assert ca["disagreements"]["decided"] == 1
        assert ca["disagreements"]["open"] == 0
        assert ca["reviews"] == {"passes": 2, "fails": 0, "required": 2,
                                 "reviewers": {"AUDREY": "PASS",
                                               "RITA REVIEWER": "PASS"}}
        assert ca["patch"]["commit_sha"] == DBT.SHA
        assert ca["patch"]["reference_only"] is True
        assert ca["evidence_count"] == 1
        assert ca["last_transition"]["stage"] == "ELIGIBLE_CHANGE"
        assert cb["stage"] == "EVIDENCE"
        assert cb["next_required"]["label"] == "AWAITING HYPOTHESIS (XAVIER)"
        assert cb["protected"]["is_protected"] is False
        cols = {s["stage"]: s["count"] for s in board["stages"]}
        assert cols["ELIGIBLE_CHANGE"] >= 1 and cols["EVIDENCE"] >= 1
        assert board["counts"]["awaiting_human"] >= 1
        assert board["counts"]["dissent_preserved"] >= 1
        assert board["runner"]["status"] == "OK"
        assert board["runner"]["stale"] is False
        only = await CI.build_board(conn, stage="EVIDENCE", owner="XAVIER")
        assert [c["item_id"] for c in only["items"]] == [b]

        d = await CI.build_item(conn, a)
        assert d["available"] is True
        assert [p["stage"] for p in d["path"]] == list(S.STAGES)
        reached = {p["stage"]: p["reached"] for p in d["path"]}
        assert reached["ELIGIBLE_CHANGE"] and not reached[
            "CONTROLLED_RELEASE"]
        assert [p["stage"] for p in d["path"] if p["current"]] == [
            "ELIGIBLE_CHANGE"]
        stages = [t["stage"] for t in d["trail"]]
        assert stages[:3] == ["EVIDENCE", "HYPOTHESIS", "PEER_CHALLENGE"]
        exp = [t for t in d["trail"] if t["stage"] == "EXPERIMENT"][0]
        assert exp["patch"]["pr_url"] == "https://github.com/o/r/pull/12"
        assert exp["patch"]["reference_only"] is True
        ev0 = d["trail"][0]
        assert ev0["source"]["kind"] == "agent_decisions"
        dis = d["disagreements"][0]
        assert dis["state"] == "DECIDED" and dis["consensus"] is False
        assert dis["open"] is False
        assert [p["party"] for p in dis["positions"]] == ["KAREN", "DEREK"]
        assert dis["resolution_source"]["id"] == DBT.D3["id"]
        assert d["evidence"] == [dict(S.link(DBT.D1), exists=True)]
        assert d["item"]["problem_statement"] == "p"
        assert "never pushes, merges or deploys" in d["disclosure"]
        assert await CI.build_item(conn, "impr:" + "f" * 24) is None
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_an_absent_migration_reads_absent_not_empty():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("ALTER TABLE improve_items RENAME TO "
                           " improve_items_hidden")
        board = await CI.build_board(conn)
        assert board["available"] is False
        assert board["items"] == []
        assert board["sections"]["items"]["status"] == "ABSENT"
        assert "migration 221" in board["unavailable_why"]
    finally:
        await tx.rollback()
        await conn.close()
