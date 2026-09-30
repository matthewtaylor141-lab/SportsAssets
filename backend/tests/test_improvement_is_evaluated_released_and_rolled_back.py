"""THE GOVERNED IMPROVEMENT LOOP (agents/improvement.py, migration 155).

  * PROOF 16: training and prospective evaluation stay separate -- a
    fixture-level holdout, outcome-availability cutoffs, no fixture in
    both, and a row whose outcome arrived after the training boundary is
    never trained on.
  * PROOF 20: a harmful candidate is rejected by its evaluation (a harm
    metric breach) and CANNOT be approved -- by the function, the route or
    a direct write; a permitted PRE-AUTHORIZED release activates a new
    policy version under a canary and is ROLLED BACK (the prior version
    reactivated) when the canary shows harm; self-approval and protected
    keys are refused by name, in Python and in the database.
  * THE HOLDOUT BUDGET: a holdout is not searched until a variant wins.

All evidence rows are SYNTHETIC test rows dated in 2032.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re

import pytest

from sportsassets.agents import improvement as IMP
from tests import audrey_helpers as H

pg = H.pg
#: SYNTHETIC: a Tuesday in 2032.
T = 1963000000.0          # 2032-03-16T...Z
DAY = H.DAY


@pytest.fixture()
async def db():
    if not H.DSN:
        pytest.skip("needs RN1X_TEST_DSN")
    async with H.connect() as c:
        if not await IMP.has_schema(c):
            pytest.skip("migration 155 is not in this database")
        await H.ensure_core_tables(c)
        await H.purge(c)
        yield c
        await H.purge(c)


def _names(holdout: bool, n: int, prefix: str) -> list:
    out, i = [], 0
    while len(out) < n:
        f = "%s%s-%d" % (H.PFX, prefix, i)
        if IMP.assign_holdout(f, salt=IMP.DEREK_HOLDOUT_SALT,
                              percent=IMP.DEREK_HOLDOUT_PERCENT) == holdout:
            out.append(f)
        i += 1
    return out


# ═════════════════════════════════════════════════════════════════════
# 0 · PURE
# ═════════════════════════════════════════════════════════════════════

def test_the_protected_keys_are_the_same_in_python_and_in_the_database():
    sql = (pathlib.Path(__file__).resolve().parents[1] / "migrations"
           / "155_audrey_audit.sql").read_text()
    block = sql.split("improvement_no_protected_key_ck")[1].split(
        "::text[]")[0]
    in_db = re.findall(r"'([^']+)'", block)
    assert sorted(in_db) == sorted(IMP.PROTECTED_KEYS)
    for k in ("risk_limits", "credentials", "account_authority",
              "approval_controls", "FUNDED_SUBMISSION_ENABLED",
              "evaluation_criteria"):
        assert k in IMP.PROTECTED_KEYS


def test_the_registry_says_which_classes_may_release_unattended():
    reg = IMP.describe_registry()["classes"]
    pre = sorted(n for n, c in reg.items() if c["pre_authorized"])
    assert pre == ["COLLECTION_PASS_LIMIT", "REPORT_THRESHOLD"]
    assert reg["COLLECTION_PASS_LIMIT"]["bounds"] == {
        "candidates_per_pass": [1, 10, True]}
    for n, c in reg.items():
        if not c["pre_authorized"]:
            assert c["unattended_release"] == "NEVER: STOPS_AT_APPROVAL_READY"
    assert IMP.check_class("RISK_LIMIT")["refusal"] == IMP.R_PROTECTED_CLASS
    assert IMP.check_class("NOPE")["refusal"] == IMP.R_UNKNOWN_CLASS
    assert IMP.protected_touched(["funded_submission_enabled", "x"]) == [
        "FUNDED_SUBMISSION_ENABLED"]
    cls = IMP.CHANGE_CLASSES["COLLECTION_PASS_LIMIT"]
    assert IMP.check_params(cls, {"candidates_per_pass": 11})[
        "refusal"] == IMP.R_OUT_OF_BOUNDS
    assert IMP.check_params(cls, {"candidates_per_pass": 2.5})[
        "refusal"] == IMP.R_OUT_OF_BOUNDS
    assert IMP.check_params(cls, {"max_position_usd": 5})[
        "refusal"] == IMP.R_PROTECTED_KEY


def test_proof16_training_and_holdout_are_separate_with_no_leakage():
    tb, eb = T, T + 10 * DAY
    tr = _names(False, 3, "tr")
    ho = _names(True, 3, "ho")
    rows = [
        # trained: decided and settled before the boundary
        {"fixture": tr[0], "decided_at": tb - 5 * DAY,
         "outcome_at": tb - 4 * DAY, "outcome": 1},
        # decided before, LEARNED AFTER the boundary: not trainable
        {"fixture": tr[1], "decided_at": tb - DAY, "outcome_at": tb + DAY,
         "outcome": 0},
        # no outcome at all: not zero, not anything
        {"fixture": tr[2], "decided_at": tb - DAY, "outcome_at": None,
         "outcome": None},
        # holdout: decided after, settled by the evaluation boundary
        {"fixture": ho[0], "decided_at": tb + DAY,
         "outcome_at": tb + 2 * DAY, "outcome": 1},
        # holdout fixture decided before the boundary: excluded
        {"fixture": ho[1], "decided_at": tb - DAY,
         "outcome_at": tb - DAY / 2, "outcome": 1},
        # holdout settled after the evaluation boundary: excluded
        {"fixture": ho[2], "decided_at": tb + DAY,
         "outcome_at": eb + DAY, "outcome": 1},
        # the TRAINING fixture decided again after the boundary is not a
        # holdout row either
        {"fixture": tr[0], "decided_at": tb + DAY,
         "outcome_at": tb + 2 * DAY, "outcome": 1}]
    sp = IMP.split_rows(rows, training_boundary=tb, evaluation_boundary=eb,
                        salt=IMP.DEREK_HOLDOUT_SALT,
                        percent=IMP.DEREK_HOLDOUT_PERCENT)
    assert sp["training_fixtures"] == [tr[0]]
    assert sp["holdout_fixtures"] == [ho[0]]
    assert not set(sp["training_fixtures"]) & set(sp["holdout_fixtures"])
    assert all(r["outcome_at"] <= tb for r in sp["training"])
    assert all(r["decided_at"] > tb and r["outcome_at"] <= eb
               for r in sp["holdout"])
    ex = sp["excluded"]
    assert ex["outcome_after_training_boundary"] == 1
    assert ex["outcome_unknown"] == 1
    assert ex["holdout_fixture_decided_before_boundary"] == 1
    assert ex["outcome_after_evaluation_boundary"] == 1
    assert ex["non_holdout_after_boundary"] == 1


def test_the_judgement_puts_harm_before_gain():
    j = IMP.judge({"a": 5.0, "h": -1.0, "n": 50},
                  success={"a": {">=": 1.0}, "min_n": 30},
                  harm={"h": {">=": 0.0}}, min_key="min_n", n_key="n")
    assert j["verdict"] == IMP.V_HARM
    assert j["harm_breaches"][0]["metric"] == "h"
    j = IMP.judge({"a": 5.0, "h": 1.0, "n": 5},
                  success={"a": {">=": 1.0}, "min_n": 30},
                  harm={"h": {">=": 0.0}}, min_key="min_n", n_key="n")
    assert j["verdict"] == IMP.V_INSUFFICIENT


# ═════════════════════════════════════════════════════════════════════
# 1 · DEREK THRESHOLD: REPLAY, HOLDOUT, HARM, NO APPROVAL
# ═════════════════════════════════════════════════════════════════════

async def _seed_derek(c, *, tb, low_edge_outcome, high_edge_outcome,
                      n_train=40, n_hold=40):
    """Per fixture one refused row with edge 0.005 (only the edge refused
    it) and one with edge 0.015 (BUY-able today), settled as stated. One
    extra row per training fixture decided before the boundary but LEARNED
    after it (outcome 1 -- if leaked it would lift the training mean)."""
    fx = _names(False, n_train, "dt") + _names(True, n_hold, "dh")
    for i, f in enumerate(fx):
        hold = i >= n_train
        dec = (tb + DAY + i * 60) if hold else (tb - 5 * DAY + i * 60)
        out_at = dec + 3600
        await H.valuation(c, fixture=f, decided=dec, p=0.6, price=0.5,
                          cost=0.5, edge=0.005, outcome=low_edge_outcome,
                          outcome_at=out_at)
        await H.valuation(c, fixture=f, decided=dec + 1, p=0.6, price=0.5,
                          cost=0.5, edge=0.015, outcome=high_edge_outcome,
                          outcome_at=out_at)
        if not hold:
            await H.valuation(c, fixture=f, decided=tb - 60 + i, p=0.6,
                              price=0.5, cost=0.5, edge=0.005, outcome=1,
                              outcome_at=tb + 3600)
    return fx


async def _task(c, tid, cls, spec=None, assignee="DEREK"):
    got = await IMP.create_task(
        c, assignee=assignee, created_by="AUDREY", kind=IMP.TASK_KIND,
        title="test task %s" % tid, spec=dict({"change_class": cls},
                                             **(spec or {})),
        task_id=tid, now=T)
    assert got.get("ok", True), got
    return tid


@pg
async def test_proof20_a_harmful_candidate_is_rejected_and_cannot_be_approved(
        db, monkeypatch):
    import asyncpg
    tb, eb = T, T + 10 * DAY
    # LOW-EDGE ROWS LOSE: lowering the threshold to 0 selects losers
    await _seed_derek(db, tb=tb, low_edge_outcome=0, high_edge_outcome=0)
    tid = await _task(db, "audt-harm", "DEREK_ENTRY_THRESHOLD", {
        "variants": [0.0], "training_boundary": tb,
        "evaluation_boundary": eb, "training_start": tb - 30 * DAY})
    out = await IMP.run_due(db, now=eb + 60)
    assert out["ok"], out
    res = out["advanced"][0]
    assert res["verdict"] == IMP.V_HARM, res
    c = await IMP.candidate(db, res["candidate_id"])
    assert c["state"] == "REJECTED"
    assert c["evaluation"]["verdict"] == IMP.V_HARM
    assert c["evaluated_by"] == IMP.EVALUATOR_REPLAY != c["proposed_by"]
    assert c["evaluation"]["judgement"]["harm_breaches"][0]["metric"] == \
        "fixture_mean_pnl_per_contract"
    # PROOF 16 IN THE STORED TRIAL: no fixture in both, and the leaked-
    # looking rows (decided before, learned after) were excluded
    hm = c["evaluation"]["metrics"]
    assert hm["fixtures_in_both"] == 0
    assert hm["excluded"]["outcome_after_training_boundary"] == 40
    assert hm["evidence_category"] == IMP.KNOWN_SETTLEMENT
    assert hm["could_have_filled"] == "UNPROVEN"
    trials = await IMP.trials(db, candidate_id=res["candidate_id"])
    assert [t["segment"] for t in trials] == ["TRAINING", "HOLDOUT"]
    train = trials[0]["metrics"]
    # trained on 40 fixtures whose outcomes were known at the boundary:
    # the 40 late-learned winners are NOT in the training mean
    assert train["fixtures"] == 40
    assert train["fixture_mean_pnl_per_contract"] == pytest.approx(-0.5)
    assert (await IMP.read_task(db, tid))["status"] == "REJECTED"
    # ── IT CANNOT BE APPROVED: function, database, re-scoring ─────────
    got = await IMP.approve(db, res["candidate_id"], approver="owner@desk",
                            credential_role="admin", now=eb + 120)
    assert got["refusal"] == IMP.R_NOT_PASSED
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await db.execute(
            "UPDATE improvement_candidates SET approved_by='owner@desk', "
            " approved_at=now() WHERE candidate_id=$1", res["candidate_id"])
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db.execute(
            "UPDATE improvement_candidates SET evaluation="
            " '{\"verdict\":\"PASS\"}'::jsonb WHERE candidate_id=$1",
            res["candidate_id"])
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db.execute("UPDATE improvement_candidates SET "
                         " state='APPROVAL_READY' WHERE candidate_id=$1",
                         res["candidate_id"])
    # ...and not through the route either
    from tests.test_audrey_audits_derek_and_xavier_daily import _Cfg, _client
    cl = _client(monkeypatch)
    r = await asyncio.to_thread(
        cl.post, "/api/command/agents/audrey/candidates/%s/approve"
        % res["candidate_id"], headers={"X-Admin-Token": _Cfg.admin_token},
        json={"approver": "owner@desk"})
    assert r.status_code == 409
    assert r.json()["detail"]["refusal"] == IMP.R_NOT_PASSED


@pg
async def test_a_passing_threshold_stops_at_approval_ready_and_a_person_approves(
        db, monkeypatch):
    import asyncpg
    tb, eb = T, T + 10 * DAY
    # LOW-EDGE ROWS LOSE, HIGHER-EDGE ROWS WIN: raising the threshold to
    # 0.01+ keeps the winners -- the current 0.01 already does; a variant
    # of 0.0 would add the losers. Candidate 0.012 ties the baseline.
    await _seed_derek(db, tb=tb, low_edge_outcome=0, high_edge_outcome=1)
    tid = await _task(db, "audt-pass", "DEREK_ENTRY_THRESHOLD", {
        "variants": [0.0, 0.012], "training_boundary": tb,
        "evaluation_boundary": eb, "training_start": tb - 30 * DAY})
    out = await IMP.run_due(db, now=eb + 60)
    res = out["advanced"][0]
    assert res["verdict"] == IMP.V_PASS, res
    c = await IMP.candidate(db, res["candidate_id"])
    assert c["params"] == {"min_net_edge_per_contract": 0.012}
    # NOT PRE-AUTHORIZED: it stops for a person, nothing is released
    assert c["state"] == "APPROVAL_READY"
    assert c["release_scope"] == IMP.SCOPE_APPROVAL
    assert await db.fetchval("SELECT count(*) FROM improvement_releases") == 0
    assert (await IMP.read_task(db, tid))["status"] == "APPROVAL_READY"
    # the variant not selected is recorded and rejected
    others = await db.fetch(
        "SELECT state, evaluation->>'verdict' AS v FROM "
        " improvement_candidates WHERE task_id=$1 AND candidate_id<>$2",
        tid, res["candidate_id"])
    assert [(o["state"], o["v"]) for o in others] == [
        ("REJECTED", IMP.V_NOT_SELECTED)]
    # ── SELF-APPROVAL AND AGENT APPROVAL, REFUSED BY NAME ─────────────
    for who, why in (("DEREK", IMP.R_AGENT_APPROVER),
                     (IMP.EVALUATOR_REPLAY, IMP.R_AGENT_APPROVER),
                     ("", IMP.R_NO_APPROVER)):
        got = await IMP.approve(db, res["candidate_id"], approver=who,
                                credential_role="admin", now=eb + 90)
        assert got["refusal"] == why, (who, got)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await db.execute(
            "UPDATE improvement_candidates SET approved_by=proposed_by, "
            " approved_at=now() WHERE candidate_id=$1", res["candidate_id"])
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await db.execute(
            "UPDATE improvement_candidates SET approved_by=evaluated_by, "
            " approved_at=now() WHERE candidate_id=$1", res["candidate_id"])
    # ── A PERSON, WITH THE CONTROL CREDENTIAL, THROUGH THE ROUTE ──────
    from tests.test_audrey_audits_derek_and_xavier_daily import _Cfg, _client
    cl = _client(monkeypatch)
    url = "/api/command/agents/audrey/candidates/%s/approve" % res[
        "candidate_id"]
    r = await asyncio.to_thread(cl.post, url, json={"approver": "owner"})
    assert r.status_code in (401, 403)
    r = await asyncio.to_thread(
        cl.post, url, headers={"X-Admin-Token": _Cfg.admin_token},
        json={"approver": "owner@desk", "statement": "read the replay"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approved_by"] == "owner@desk"
    assert body["credential_role"] == "admin" and body["deployed"] is False
    c = await IMP.candidate(db, res["candidate_id"])
    assert c["state"] == "APPROVED" and c["approved_by"] == "owner@desk"
    # APPROVAL DOES NOT DEPLOY: a CANDIDATE policy row, nothing ACTIVE
    pv = await db.fetch("SELECT state, approved_by FROM "
                        " agent_policy_versions WHERE policy_key=$1",
                        "derek.entry_threshold")
    assert [(p["state"], p["approved_by"]) for p in pv] == [
        ("CANDIDATE", "owner@desk")]
    ev = await db.fetchval(
        "SELECT detail FROM improvement_events WHERE candidate_id=$1 "
        " AND kind='APPROVED'", res["candidate_id"])
    assert json.loads(ev)["credential_role"] == "admin"
    # approval is written once
    again = await IMP.approve(db, res["candidate_id"], approver="other",
                              credential_role="admin", now=eb + 200)
    assert again["refusal"] == IMP.R_NOT_APPROVAL_READY


# ═════════════════════════════════════════════════════════════════════
# 2 · THE PRE-AUTHORIZED RELEASE AND ITS ROLLBACK
# ═════════════════════════════════════════════════════════════════════

async def _sample(c, n, at, *, stopped=False, elapsed=20.0, attempted=3):
    await c.execute(
        "INSERT INTO audrey_collection_samples (sample_id, source, pass_id, "
        " pass_at, sampled_at, configured_per_pass, offered, attempted, "
        " not_attempted, limit_per_pass_left, skipped_recently_refused, "
        " stopped_for_deadline, elapsed_s, budget_s) VALUES ($1,'TEST',$1,"
        " $2,$2,3,8,$3,'{}'::jsonb,$4,0,$5,$6,120)",
        "%ss-%d" % (H.PFX, n), H.ts(at), attempted, 8 - attempted, stopped,
        elapsed)


@pg
async def test_proof20_a_preauthorized_release_is_canaried_and_rolled_back(db):
    tb, eb = T, T + 2 * DAY
    for i in range(10):
        await _sample(db, i, tb - DAY + i * 600)          # training
    for i in range(10):
        await _sample(db, 100 + i, tb + 3600 + i * 600)   # holdout
    tid = await _task(db, "audt-passlimit", "COLLECTION_PASS_LIMIT", {
        "training_boundary": tb, "evaluation_boundary": eb,
        "training_start": tb - 3 * DAY})
    out = await IMP.run_due(db, now=eb + 60)
    res = out["advanced"][0]
    assert res["verdict"] == IMP.V_PASS, res
    c = await IMP.candidate(db, res["candidate_id"])
    # 3 -> 5: the largest step the canary allows, within [1, 10]
    assert c["params"] == {"candidates_per_pass": 5}
    assert c["state"] == "CANARY"
    assert c["approved_by"] == "PRE_AUTHORIZED:COLLECTION_PASS_LIMIT"
    assert c["evaluation"]["evidence_category"] == IMP.SIMULATED
    rel = res["release"]
    assert rel["ok"] is True
    pv = {r["version"]: r["state"] for r in await db.fetch(
        "SELECT version, state FROM agent_policy_versions "
        " WHERE agent_id='DEREK' AND policy_key='collection.pass_limit'")}
    assert pv[rel["to_version"]] == "ACTIVE"
    assert pv[rel["from_version"]] == "RETIRED"
    assert rel["from_version"].startswith("code-default-")
    cur = await IMP.current_policy(db, IMP.CHANGE_CLASSES[
        "COLLECTION_PASS_LIMIT"])
    assert cur["params"] == {"candidates_per_pass": 5}
    assert (await IMP.read_task(db, tid))["status"] == "RELEASED"
    # every variant's trial is kept, the rejected one included
    n_trials = await db.fetchval(
        "SELECT count(*) FROM improvement_trials WHERE task_id=$1", tid)
    assert n_trials == 3                  # 4 and 5 on training, 5 holdout
    # ── THE CANARY: two deadline stops after the release -> rollback ──
    await _sample(db, 500, eb + 120, stopped=True, elapsed=130.0)
    await _sample(db, 501, eb + 720, stopped=True, elapsed=131.0)
    later = await IMP.run_due(db, now=eb + 900)
    rb = later["canaries"]["rolled_back"]
    assert len(rb) == 1 and rb[0]["reactivated"] == rel["from_version"]
    pv = {r["version"]: r["state"] for r in await db.fetch(
        "SELECT version, state FROM agent_policy_versions "
        " WHERE agent_id='DEREK' AND policy_key='collection.pass_limit'")}
    assert pv == {rel["from_version"]: "ACTIVE",
                  rel["to_version"]: "REJECTED"}
    r = await db.fetchrow("SELECT * FROM improvement_releases "
                          " WHERE release_id=$1", rel["release_id"])
    assert r["state"] == "ROLLED_BACK" and "CANARY_HARM" in \
        r["rollback_reason"]
    assert (await IMP.candidate(db, res["candidate_id"]))["state"] == \
        "ROLLED_BACK"
    assert (await IMP.read_task(db, tid))["status"] == "ROLLED_BACK"
    cur = await IMP.current_policy(db, IMP.CHANGE_CLASSES[
        "COLLECTION_PASS_LIMIT"])
    assert cur["params"] == {"candidates_per_pass": 3}
    # a rolled-back release cannot be rolled back twice
    assert (await IMP.rollback(db, rel["release_id"], reason="again",
                               actor="t", now=eb + 999))["refusal"] == \
        IMP.R_NO_SUCH_RELEASE


@pg
async def test_a_pass_limit_that_would_breach_the_pass_budget_is_rejected(db):
    tb, eb = T, T + 2 * DAY
    # passes already at 110 of 120 s with 3 attempts: any extra attempt
    # (~36.7 s each) breaches the budget
    for i in range(10):
        await _sample(db, i, tb - DAY + i * 600, elapsed=110.0)
    for i in range(10):
        await _sample(db, 100 + i, tb + 3600 + i * 600, elapsed=110.0)
    tid = await _task(db, "audt-passlimit-harm", "COLLECTION_PASS_LIMIT", {
        "training_boundary": tb, "evaluation_boundary": eb,
        "training_start": tb - 3 * DAY})
    out = await IMP.run_due(db, now=eb + 60)
    assert out["advanced"][0]["verdict"] == "NO_VARIANT_PASSED_TRAINING"
    rows = await db.fetch(
        "SELECT state, evaluation->>'verdict' AS v FROM "
        " improvement_candidates WHERE task_id=$1", tid)
    assert {(r["state"], r["v"]) for r in rows} == {("REJECTED", IMP.V_HARM)}
    assert await db.fetchval("SELECT count(*) FROM improvement_trials "
                             " WHERE task_id=$1", tid) == 2
    assert await db.fetchval("SELECT count(*) FROM improvement_releases") == 0
    assert (await IMP.read_task(db, tid))["status"] == "REJECTED"


# ═════════════════════════════════════════════════════════════════════
# 3 · PROTECTED KEYS, PROTECTED CLASSES, THE HOLDOUT BUDGET
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_protected_keys_and_classes_are_refused_by_name(db, monkeypatch):
    import asyncpg
    base = dict(task_id="audt-prot", proposed_by="AUDREY", hypothesis="h",
                evidence={}, affected_behavior="b", now=T)
    got = await IMP.propose(db, change_class="CODE_CHANGE", diff="x",
                            touched_keys=["max_position_usd"], **base)
    assert got["refusal"] == IMP.R_PROTECTED_KEY
    assert got["protected"] == ["max_position_usd"]
    got = await IMP.propose(db, change_class="COLLECTION_PASS_LIMIT",
                            params={"FUNDED_SUBMISSION_ENABLED": True},
                            **base)
    assert got["refusal"] == IMP.R_PROTECTED_KEY
    got = await IMP.propose(db, change_class="RISK_LIMIT", diff="x", **base)
    assert got["refusal"] == IMP.R_PROTECTED_CLASS
    assert await db.fetchval("SELECT count(*) FROM improvement_candidates") \
        == 0
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await db.execute(
            "INSERT INTO improvement_candidates (candidate_id, task_id, "
            " assigned_agent, change_class, change_kind, hypothesis, "
            " affected_behavior, touched_keys, release_scope, "
            " rollback_procedure, proposed_by) VALUES ('c','t','AUDREY',"
            " 'CODE_CHANGE','CODE','h','b',ARRAY['credentials'],"
            " 'REQUIRES_APPROVAL','revert','AUDREY')")
    # A PROTECTED CLASS THAT REACHED THE TABLE SOME OTHER WAY is refused
    # at approval, by the route
    await db.execute(
        "INSERT INTO improvement_candidates (candidate_id, task_id, "
        " assigned_agent, change_class, change_kind, hypothesis, "
        " affected_behavior, release_scope, rollback_procedure, "
        " proposed_by, evaluated_by, evaluation, state) VALUES "
        " ('audt-riskcand','audt-t','AUDREY','RISK_LIMIT','CODE','h','b',"
        " 'REQUIRES_APPROVAL','revert','someone','EVALUATOR:X',"
        " '{\"verdict\":\"PASS\"}'::jsonb,'APPROVAL_READY')")
    from tests.test_audrey_audits_derek_and_xavier_daily import _Cfg, _client
    cl = _client(monkeypatch)
    r = await asyncio.to_thread(
        cl.post, "/api/command/agents/audrey/candidates/audt-riskcand/"
        "approve", headers={"X-Admin-Token": _Cfg.admin_token},
        json={"approver": "owner@desk"})
    assert r.status_code == 403
    assert r.json()["detail"]["refusal"] == IMP.R_PROTECTED_CLASS
    # SELF-APPROVAL of a person-proposed code candidate
    got = await IMP.propose(db, change_class="CODE_CHANGE",
                            diff="--- a/x\n+++ b/x\n", **dict(
                                base, proposed_by="alice@desk",
                                task_id="audt-self"),
                            artifact_ref="improve/audt-self@abc")
    cid = got["candidate_id"]
    await IMP.record_evaluation(
        db, candidate_id=cid, evaluated_by=IMP.EVALUATOR_SANDBOX_REVIEW,
        state="APPROVAL_READY", evaluation={"verdict": "PASS"}, now=T)
    got = await IMP.approve(db, cid, approver="alice@desk",
                            credential_role="operator", now=T + 1)
    assert got["refusal"] == IMP.R_SELF_APPROVAL
    # THE EVALUATOR IS NEVER THE PROPOSER, in the database too
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await db.execute(
            "INSERT INTO improvement_candidates (candidate_id, task_id, "
            " assigned_agent, change_class, change_kind, hypothesis, "
            " affected_behavior, release_scope, rollback_procedure, "
            " proposed_by, evaluated_by, evaluation) VALUES ('audt-selfev',"
            " 't','AUDREY','CODE_CHANGE','CODE','h','b','REQUIRES_APPROVAL',"
            " 'revert','bob','bob','{\"verdict\":\"PASS\"}'::jsonb)")


@pg
async def test_a_holdout_is_not_searched_past_its_budget(db):
    import asyncpg
    got = await IMP.propose(
        db, task_id="audt-budget", change_class="COLLECTION_PASS_LIMIT",
        proposed_by="DEREK", hypothesis="h", evidence={},
        affected_behavior="b", params={"candidates_per_pass": 4}, now=T)
    cid = got["candidate_id"]
    await IMP.ensure_holdout(db, holdout_id="audt-h1", description="t",
                             rule={"kind": "TEST"}, budget=2)
    for k in range(2):
        tr = await IMP.record_trial(
            db, candidate_id=cid, task_id="audt-budget", segment="HOLDOUT",
            holdout_id="audt-h1", variant={"k": k}, metrics={},
            verdict=IMP.V_NO_GAIN, evidence_category=IMP.SIMULATED,
            training_boundary=T + k, evaluation_boundary=T + 10 + k, now=T)
        assert tr["ok"], tr
    third = await IMP.record_trial(
        db, candidate_id=cid, task_id="audt-budget", segment="HOLDOUT",
        holdout_id="audt-h1", variant={"k": 3}, metrics={},
        verdict=IMP.V_PASS, evidence_category=IMP.SIMULATED,
        training_boundary=T + 3, evaluation_boundary=T + 13, now=T)
    assert third["refusal"] == IMP.R_HOLDOUT_BUDGET
    assert third["usage"] == {"holdout_id": "audt-h1", "exists": True,
                              "budget": 2, "used": 2, "remaining": 0}
    with pytest.raises(asyncpg.exceptions.RaiseError,
                       match="HOLDOUT_BUDGET_EXHAUSTED"):
        await db.execute(
            "INSERT INTO improvement_trials (trial_id, candidate_id, task_id, "
            " segment, holdout_id, variant, evidence_category, metrics, "
            " verdict, evaluated_by) VALUES ('x',$1,'audt-budget','HOLDOUT',"
            " 'audt-h1','{}'::jsonb,'SIMULATED_WITH_DISCLOSED_ASSUMPTIONS',"
            " '{}'::jsonb,'PASS','EVALUATOR:X')", cid)
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db.execute("DELETE FROM improvement_trials "
                         " WHERE candidate_id=$1", cid)


@pg
async def test_a_code_task_waits_for_the_sandbox(db):
    tid = await _task(db, "audt-code", "XAVIER_EVIDENCE_CAPTURE",
                      assignee="XAVIER")
    out = await IMP.run_due(db, now=T)
    assert out["advanced"][0]["waiting_on"] == IMP.NEEDS_SANDBOX
    t = await IMP.read_task(db, tid)
    assert t["status"] == "WAITING"
    ev = (await IMP.task_events(db, tid, limit=1))[0]
    assert ev["detail"]["waiting_on"] == IMP.NEEDS_SANDBOX
    assert "improvement_sandbox.py --task-id %s" % tid in ev["detail"]["run"]
    # an unknown class is cancelled by name, not guessed
    bad = await _task(db, "audt-bad", "RISK_LIMIT")
    await IMP.run_due(db, now=T + 60)
    assert (await IMP.read_task(db, bad))["status"] == "CANCELLED"
