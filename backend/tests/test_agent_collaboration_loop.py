"""THE AGENTS' COLLABORATION LOOP (migration 203): EVIDENCE -> HYPOTHESIS ->
PEER CHALLENGE -> BOUNDED EXPERIMENT -> CANDIDATE -> INDEPENDENT EVALUATION ->
RELEASE ELIGIBILITY, with every guard enforced in code AND in the database:

  * no agent challenges or evaluates its own finding (or its own candidate);
  * no stage is skipped, repeated or moved back;
  * an ungrounded finding cannot exist or advance;
  * the experiment is PAPER_ONLY with a pre-registered metric and stopping
    rule; the evaluation uses data not used to form the hypothesis, after
    pre-registration, inside the stopping rule, and a PASS meets the metric;
  * release eligibility only marks ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW after a
    PASS; nothing in the loop activates a policy or changes a limit.
"""
from __future__ import annotations

import json
import pathlib

import asyncpg
import pytest

from sportsassets.agents import collaboration_loop as CL
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "migrations"
UP = (MIG / "203_agent_collaboration_loop.sql").read_text()
DOWN = (MIG / "rollback" / "203_agent_collaboration_loop.down.sql"
        ).read_text()

T0 = 1_790_000_000.0
DAY = 86400.0
E1 = {"kind": "agent_decisions", "id": "adr:loop-test-1"}
E2 = {"kind": "agent_decisions", "id": "adr:loop-test-2"}
E3 = {"kind": "agent_decisions", "id": "adr:loop-test-3"}
EF = {"kind": "agent_decisions", "id": "adr:loop-test-forward"}
METRIC = {"name": "paper_net_ev_per_review_usd", "direction": "INCREASE",
          "threshold": 0.01}
STOP = {"max_duration_s": 14 * DAY, "max_samples": 500}


# ════════════════════════════════════════════════════════════════════
# PURE GUARDS
# ════════════════════════════════════════════════════════════════════

def _f(**kw):
    return dict({"finding_id": "f", "proposer": "DEREK", "stage": None,
                 "evidence_refs": [E1], "evidence_window_end": T0,
                 "created_at": T0 + 10}, **kw)


def _stages(n: int, over: dict | None = None) -> list:
    """The first n stages of a well-formed finding."""
    rows = [
        {"seq": 1, "stage": CL.EVIDENCE, "actor": "DEREK", "at": T0 + 10,
         "evidence_refs": [E1], "body": {}},
        {"seq": 2, "stage": CL.HYPOTHESIS, "actor": "DEREK", "at": T0 + 20,
         "evidence_refs": [E2], "body": {"hypothesis": "h"}},
        {"seq": 3, "stage": CL.PEER_CHALLENGE, "actor": "AUDREY",
         "at": T0 + 30, "outcome": "SUSTAINED", "evidence_refs": [E1],
         "body": {"challenge": "c"}},
        {"seq": 4, "stage": CL.BOUNDED_EXPERIMENT, "actor": "DEREK",
         "at": T0 + 40, "scope": "PAPER_ONLY", "metric": METRIC,
         "stopping_rule": STOP, "body": {}},
        {"seq": 5, "stage": CL.CANDIDATE_IMPROVEMENT, "actor": "DEREK",
         "at": T0 + 50, "body": {"description": "d", "change": {"x": 1}}},
        {"seq": 6, "stage": CL.INDEPENDENT_EVALUATION, "actor": "XAVIER",
         "at": T0 + 8 * DAY, "outcome": "PASS", "evidence_refs": [EF],
         "data_start": T0 + DAY, "data_end": T0 + 7 * DAY,
         "body": {"metric_value": 0.02, "samples": 100}},
    ][:n]
    for r in rows:
        r.update((over or {}).get(r["seq"], {}))
    return rows


def _eval(**kw):
    return dict({"stage": CL.INDEPENDENT_EVALUATION, "actor": "XAVIER",
                 "at": T0 + 8 * DAY, "outcome": "PASS",
                 "evidence_refs": [EF], "data_start": T0 + DAY,
                 "data_end": T0 + 7 * DAY,
                 "body": {"metric_value": 0.02, "samples": 100}}, **kw)


def test_the_happy_path_passes_every_guard():
    for n in range(1, 7):
        nxt = _stages(n + 1)[-1] if n < 6 else None
        if nxt is None:
            break
        new = dict(nxt)
        assert CL.check_advance(_f(stage=_stages(n)[-1]["stage"]),
                                _stages(n), new) is None, new["stage"]
    rel = {"stage": CL.RELEASE_ELIGIBILITY, "actor": "AUDREY",
           "at": T0 + 9 * DAY, "body": {}}
    assert CL.check_advance(_f(), _stages(6), rel) is None


def test_no_stage_can_be_skipped():
    exp = dict(_stages(4)[-1])
    assert CL.check_advance(_f(), _stages(2), exp) == CL.R_SKIPPED
    assert CL.check_advance(_f(), _stages(4), _eval()) == CL.R_SKIPPED
    rel = {"stage": CL.RELEASE_ELIGIBILITY, "actor": "AUDREY",
           "at": T0 + 9 * DAY, "body": {}}
    assert CL.check_advance(_f(), _stages(3), rel) == CL.R_SKIPPED
    # nor repeated
    assert CL.check_advance(_f(), _stages(3), dict(_stages(3)[-1])) \
        == CL.R_SKIPPED
    # a closed finding takes nothing more
    assert CL.check_advance(_f(stage=CL.CLOSED), _stages(2),
                            dict(_stages(3)[-1])) == CL.R_CLOSED


def test_no_agent_can_challenge_or_evaluate_its_own_finding():
    ch = dict(_stages(3)[-1], actor="DEREK")
    assert CL.check_advance(_f(), _stages(2), ch) == CL.R_SELF_CHALLENGE
    assert CL.check_advance(_f(), _stages(5), _eval(actor="DEREK")) \
        == CL.R_SELF_EVALUATION
    # nor the candidate's author, when that is not the proposer
    st = _stages(5, {5: {"actor": "XAVIER"}})
    assert CL.check_advance(_f(), st, _eval(actor="XAVIER")) \
        == CL.R_SELF_EVALUATION
    assert CL.check_advance(_f(), st, _eval(actor="AUDREY")) is None
    rel = {"stage": CL.RELEASE_ELIGIBILITY, "actor": "DEREK",
           "at": T0 + 9 * DAY, "body": {}}
    assert CL.check_advance(_f(), _stages(6), rel) == CL.R_SELF_RELEASE
    # the hypothesis is the proposer's
    hy = dict(_stages(2)[-1], actor="AUDREY")
    assert CL.check_advance(_f(), _stages(1), hy) == CL.R_PROPOSER_ONLY
    assert CL.check_advance(_f(), _stages(2),
                            dict(_stages(3)[-1], actor="BOB")) \
        == CL.R_UNKNOWN_AGENT


def test_ungrounded_findings_cannot_advance():
    assert CL.normalise_refs([])["refusal"] == CL.R_UNGROUNDED
    assert CL.normalise_refs(None)["refusal"] == CL.R_UNGROUNDED
    assert CL.normalise_refs([{"kind": "agent_decisions"}])["refusal"] \
        == CL.R_BAD_REF
    assert CL.normalise_refs([{"kind": "a_blog_post", "id": "x"}]
                             )["refusal"] == CL.R_UNKNOWN_KIND
    hy = dict(_stages(2)[-1])
    assert CL.check_advance(_f(evidence_refs=[]), _stages(1), hy) \
        == CL.R_UNGROUNDED
    assert CL.check_advance(_f(), _stages(1), dict(hy, evidence_refs=[])) \
        == CL.R_UNGROUNDED
    ch = dict(_stages(3)[-1], evidence_refs=[])
    assert CL.check_advance(_f(), _stages(2), ch) == CL.R_UNGROUNDED
    assert CL.check_advance(_f(), _stages(5), _eval(evidence_refs=[])) \
        == CL.R_UNGROUNDED


def test_the_experiment_is_paper_only_and_pre_registered():
    exp = dict(_stages(4)[-1])
    assert CL.check_advance(_f(), _stages(3), dict(exp, scope="FUNDED")) \
        == CL.R_NOT_PAPER
    assert CL.check_advance(_f(), _stages(3), dict(exp, metric=None)) \
        == CL.R_NO_METRIC
    assert CL.check_advance(_f(), _stages(3), dict(
        exp, metric=dict(METRIC, direction="BETTER"))) == CL.R_NO_METRIC
    assert CL.check_advance(_f(), _stages(3), dict(exp, stopping_rule={})) \
        == CL.R_NO_STOP
    assert CL.check_advance(_f(), _stages(3), dict(
        exp, stopping_rule={"max_duration_s": 365 * DAY})) == CL.R_NO_STOP
    refuted = _stages(3, {3: {"outcome": "REFUTED"}})
    assert CL.check_advance(_f(), refuted, exp) == CL.R_REFUTED


def test_the_evaluation_is_independent_of_the_hypothesis_data():
    st = _stages(5)
    # data overlapping the evidence window
    assert CL.check_advance(_f(evidence_window_end=T0 + 2 * DAY), st,
                            _eval()) == CL.R_REUSED_DATA
    # citing the evidence the finding / hypothesis was formed on
    assert CL.check_advance(_f(), st, _eval(evidence_refs=[E1])) \
        == CL.R_REUSED_DATA
    assert CL.check_advance(_f(), st, _eval(evidence_refs=[EF, E2])) \
        == CL.R_REUSED_DATA
    # data from before the experiment was registered
    late = _stages(5, {4: {"at": T0 + 2 * DAY}, 5: {"at": T0 + 2 * DAY}})
    assert CL.check_advance(_f(), late, _eval()) == CL.R_BEFORE_REGISTRATION
    # outside the stopping rule
    assert CL.check_advance(_f(), st, _eval(data_end=T0 + 20 * DAY,
                                            at=T0 + 21 * DAY)) \
        == CL.R_OUTSIDE_STOP
    assert CL.check_advance(_f(), st, _eval(
        body={"metric_value": 0.02, "samples": 501})) == CL.R_OUTSIDE_STOP
    # future data, and an inverted window
    assert CL.check_advance(_f(), st, _eval(at=T0 + 6 * DAY)) \
        == CL.R_BAD_WINDOW
    assert CL.check_advance(_f(), st, _eval(data_start=T0 + 7 * DAY,
                                            data_end=T0 + DAY)) \
        == CL.R_BAD_WINDOW
    # a PASS that misses the pre-registered metric
    assert CL.check_advance(_f(), st, _eval(
        body={"metric_value": 0.001, "samples": 100})) \
        == CL.R_PASS_MISSES_METRIC
    assert CL.check_advance(_f(), st, _eval(
        outcome="FAIL", body={"metric_value": 0.001, "samples": 100})) \
        is None


def test_release_eligibility_needs_a_pass_and_changes_nothing():
    rel = {"stage": CL.RELEASE_ELIGIBILITY, "actor": "AUDREY",
           "at": T0 + 9 * DAY, "body": {}}
    for out in ("FAIL", "INCONCLUSIVE"):
        st = _stages(6, {6: {"outcome": out}})
        assert CL.check_advance(_f(), st, rel) == CL.R_NOT_PASSED
    assert CL.ELIGIBLE == "ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW"


def test_the_loop_cannot_carry_a_limit_approval_or_activation():
    cand = dict(_stages(5)[-1], body={"description": "d", "change": {
        "nested": [{"risk_limits": {"max": 5}}]}})
    assert CL.check_advance(_f(), _stages(4), cand) == CL.R_AUTHORITY
    for k in ("activate", "approved_by", "max_order_usd", "active"):
        cand = dict(_stages(5)[-1], body={"description": "d",
                                          "change": {k: True}})
        assert CL.check_advance(_f(), _stages(4), cand) == CL.R_AUTHORITY, k
    for k in CL.AUTHORITY_KEYS:
        assert "'%s'" % k in UP, k


def test_the_module_writes_only_the_loop_tables_and_sends_nothing():
    src = (ROOT / "sportsassets" / "agents" / "collaboration_loop.py"
           ).read_text()
    low = src.lower()
    import re
    written = set(re.findall(r"(?:insert into|update|delete from)\s+"
                             r"([a-z_]+)", low))
    assert written <= {"agent_findings", "agent_finding_stages"}, written
    for forbidden in ("agent_policy_versions", "paper_control",
                      "agent_policy_artifacts", "activate_version",
                      "slack", "chat.postmessage"):
        assert forbidden not in low.replace(
            "never written", ""), forbidden
    assert "import registry" not in src and "from . import" not in src


def test_the_read_routes_require_the_command_credential():
    from fastapi.testclient import TestClient
    from sportsassets.api import app as A
    c = TestClient(A.app)
    for p in ("/api/command/agents/findings",
              "/api/command/agents/findings/afnd:x"):
        assert c.get(p).status_code == 401, p


# ════════════════════════════════════════════════════════════════════
# POSTGRES (one transaction, rolled back)
# ════════════════════════════════════════════════════════════════════

async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


async def _seed(conn):
    await conn.execute(UP)
    await conn.execute(UP)                                       # idempotent
    for a in ("DEREK", "XAVIER", "AUDREY"):
        await conn.execute(
            "INSERT INTO agent_identities (agent_id, display_name, mandate) "
            "VALUES ($1,$1,'m') ON CONFLICT (agent_id) DO NOTHING", a)
    for r in (E1, E2, E3, EF):
        await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " decided_at) VALUES ($1,'DEREK','TEST',to_timestamp($2)) "
            "ON CONFLICT DO NOTHING", r["id"], T0)


async def _snapshot(conn) -> dict:
    out = {}
    for t, q in (
            ("apv", "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY "
                    "t::text), '')) FROM agent_policy_versions t"),
            ("pip", "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY "
                    "t::text), '')) FROM paper_improvement_proposals t"),
            ("ctl", "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY "
                    "t::text), '')) FROM paper_control t"),
            ("ident", "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY "
                      "t::text), '')) FROM agent_identities t")):
        out[t] = await conn.fetchval(q)
    return out


async def _run_to_candidate(conn, fid_title="loop finding", *,
                            candidate_actor="DEREK", upto=5) -> str:
    got = await CL.open_finding(
        conn, proposer="DEREK", title=fid_title,
        statement="entries refused for stale books cluster after 21:00",
        evidence_refs=[E1], evidence_window_end=T0, at=T0 + 10)
    assert got["ok"] and got["created"], got
    fid = got["finding_id"]
    steps = (
        lambda: CL.record_hypothesis(conn, fid, actor="DEREK",
                                     hypothesis="a later book read helps",
                                     evidence_refs=[E2], at=T0 + 20),
        lambda: CL.record_challenge(conn, fid, actor="AUDREY",
                                    challenge="is it the venue's latency?",
                                    outcome="SUSTAINED", evidence_refs=[E3],
                                    at=T0 + 30),
        lambda: CL.register_experiment(conn, fid, actor="DEREK",
                                       design="paper A/B", metric=METRIC,
                                       stopping_rule=STOP, at=T0 + 40),
        lambda: CL.record_candidate(conn, fid, actor=candidate_actor,
                                    description="re-read the book once",
                                    change={"paper_book_reread": True},
                                    at=T0 + 50))
    for step in steps[:upto - 1]:
        res = await step()
        assert res["ok"], res
    return fid


@pg
@pytest.mark.asyncio
async def test_a_finding_moves_through_every_stage_and_changes_nothing():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await _seed(conn)
        before = await _snapshot(conn)
        fid = await _run_to_candidate(conn)
        # replay of the open writes nothing
        again = await CL.open_finding(
            conn, proposer="DEREK", title="loop finding",
            statement="entries refused for stale books cluster after 21:00",
            evidence_refs=[E1], evidence_window_end=T0, at=T0 + 10)
        assert again == {"ok": True, "refusal": None, "finding_id": fid,
                         "created": False}
        # self-evaluation refused (code)
        bad = await CL.record_evaluation(
            conn, fid, actor="DEREK", outcome="PASS", data_start=T0 + DAY,
            data_end=T0 + 7 * DAY, evidence_refs=[EF], at=T0 + 8 * DAY,
            metric_value=0.02, samples=100)
        assert bad["refusal"] == CL.R_SELF_EVALUATION
        # release before evaluation refused (no skipping)
        bad = await CL.mark_release_eligible(conn, fid, actor="AUDREY",
                                             at=T0 + 8 * DAY)
        assert bad["refusal"] == CL.R_SKIPPED
        ok = await CL.record_evaluation(
            conn, fid, actor="XAVIER", outcome="PASS", data_start=T0 + DAY,
            data_end=T0 + 7 * DAY, evidence_refs=[EF], at=T0 + 8 * DAY,
            metric_value=0.02, samples=100)
        assert ok["ok"], ok
        rel = await CL.mark_release_eligible(conn, fid, actor="AUDREY",
                                             at=T0 + 9 * DAY, note="n")
        assert rel["ok"] and rel["outcome"] == CL.ELIGIBLE, rel
        got = await CL.finding(conn, fid)
        assert got["finding"]["stage"] == CL.RELEASE_ELIGIBILITY
        assert [s["stage"] for s in got["stages"]] == list(CL.STAGES)
        assert {s["production_effect"] for s in got["stages"]} == {"NONE"}
        assert got["finding"]["production_effect"] == "NONE"
        # NOTHING OUTSIDE THE LOOP CHANGED: no policy version, proposal,
        # control or identity was written by any stage
        assert await _snapshot(conn) == before
        listed = await CL.findings(conn, stage=CL.RELEASE_ELIGIBILITY)
        assert fid in [f["finding_id"] for f in listed]
        # nothing follows eligibility but CLOSED
        assert (await CL.close(conn, fid, actor="AUDREY", reason="released "
                               "review done", at=T0 + 10 * DAY))["ok"]
        bad = await CL.record_hypothesis(conn, fid, actor="DEREK",
                                         hypothesis="x", evidence_refs=[E2],
                                         at=T0 + 11 * DAY)
        assert bad["refusal"] == CL.R_CLOSED
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_code_refuses_unknown_or_missing_evidence():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await _seed(conn)
        got = await CL.open_finding(
            conn, proposer="XAVIER", title="t", statement="s",
            evidence_refs=[{"kind": "agent_decisions", "id": "adr:none"}],
            evidence_window_end=T0, at=T0 + 1)
        assert got["refusal"] == CL.R_REF_NOT_FOUND
        got = await CL.open_finding(
            conn, proposer="XAVIER", title="t", statement="s",
            evidence_refs=[], evidence_window_end=T0, at=T0 + 1)
        assert got["refusal"] == CL.R_UNGROUNDED
        # a candidate's linked proposal must exist (paper only, inactive)
        fid = await _run_to_candidate(conn, "second", upto=4)
        bad = await CL.record_candidate(conn, fid, actor="DEREK",
                                        description="d", change={"x": 1},
                                        at=T0 + 50,
                                        proposal_id="paper-no-such")
        assert bad["refusal"] == CL.R_PROPOSAL
        # a stage citing a record that does not exist is refused
        f2 = await CL.open_finding(
            conn, proposer="AUDREY", title="third", statement="s",
            evidence_refs=[E2], evidence_window_end=T0, at=T0 + 1)
        bad = await CL.record_hypothesis(
            conn, f2["finding_id"], actor="AUDREY", hypothesis="h",
            evidence_refs=[{"kind": "paper_agent_lessons",
                            "id": "paper-none"}], at=T0 + 2)
        assert bad["refusal"] == CL.R_REF_NOT_FOUND
        bad = await CL.record_hypothesis(
            conn, "afnd:none", actor="AUDREY", hypothesis="h",
            evidence_refs=[E2], at=T0 + 2)
        assert bad["refusal"] == CL.R_NO_SUCH_FINDING
    finally:
        await tx.rollback()
        await conn.close()


STAGE_SQL = (
    "INSERT INTO agent_finding_stages (finding_id, seq, stage, actor, at, "
    " body, evidence_refs, outcome, scope, metric, stopping_rule, "
    " data_start, data_end, production_effect) VALUES ($1,$2,$3,$4,"
    " to_timestamp($5),$6::jsonb,$7::jsonb,$8,$9,$10::jsonb,$11::jsonb,"
    " to_timestamp($12),to_timestamp($13),$14)")


def _raw(fid, seq, stage, actor, at, *, body=None, refs=None, outcome=None,
         scope=None, metric=None, stop=None, ds=None, de=None,
         effect="NONE"):
    return (STAGE_SQL, fid, seq, stage, actor, at, json.dumps(body or {}),
            json.dumps(refs or []), outcome, scope,
            None if metric is None else json.dumps(metric),
            None if stop is None else json.dumps(stop), ds, de, effect)


@pg
@pytest.mark.asyncio
async def test_the_database_enforces_every_guard_without_the_module():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await _seed(conn)
        RE, CV = asyncpg.RaiseError, asyncpg.CheckViolationError
        FI = ("INSERT INTO agent_findings (finding_id, proposer, title, "
              " statement, evidence_refs, evidence_window_end, created_at, "
              " updated_at, stage, stage_seq, production_effect) VALUES "
              " ($1,'DEREK','t','s',$2::jsonb,to_timestamp($3::float8),"
              " to_timestamp($3::float8 + 10),to_timestamp($3::float8 + 10),"
              " $4,$5,$6)")
        # UNGROUNDED FINDINGS CANNOT EXIST
        for refs in ("[]", '[{"kind":"agent_decisions"}]', '[{"id":"x"}]',
                     '{"kind":"a","id":"b"}', '["x"]'):
            await _expect(conn, CV, FI, "fx", refs, T0, None, 0, "NONE")
        # a finding cannot be born at a stage, or with a production effect
        await _expect(conn, RE, FI, "fx", json.dumps([E1]), T0,
                      "RELEASE_ELIGIBILITY", 7, "NONE")
        await _expect(conn, CV, FI, "fx", json.dumps([E1]), T0, None, 0,
                      "ACTIVATE_POLICY")
        await conn.execute(FI, "f1", json.dumps([E1]), T0, None, 0, "NONE")
        a = T0 + 10
        # nothing before EVIDENCE; EVIDENCE is the proposer's
        await _expect(conn, RE, *_raw("f1", 2, "HYPOTHESIS", "DEREK", a,
                                      body={"hypothesis": "h"}, refs=[E2]))
        await _expect(conn, RE, *_raw("f1", 1, "EVIDENCE", "AUDREY", a,
                                      refs=[E1]))
        await _expect(conn, CV, *_raw("f1", 1, "EVIDENCE", "DEREK", a))
        await conn.execute(*_raw("f1", 1, "EVIDENCE", "DEREK", a, refs=[E1]))
        assert await conn.fetchval(
            "SELECT stage FROM agent_findings WHERE finding_id='f1'") \
            == "EVIDENCE"
        # NO SKIPPING, NO REPEATING
        await _expect(conn, RE, *_raw("f1", 3, "PEER_CHALLENGE", "AUDREY", a,
                                      body={"challenge": "c"}, refs=[E3],
                                      outcome="SUSTAINED"))
        await _expect(conn, (RE, asyncpg.UniqueViolationError),
                      *_raw("f1", 1, "EVIDENCE", "DEREK", a, refs=[E1]))
        # a mislabelled seq is refused
        await _expect(conn, (RE, CV), *_raw("f1", 3, "HYPOTHESIS", "DEREK", a,
                                      body={"hypothesis": "h"}, refs=[E2]))
        # hypothesis: proposer only, grounded, non-empty
        await _expect(conn, RE, *_raw("f1", 2, "HYPOTHESIS", "XAVIER", a,
                                      body={"hypothesis": "h"}, refs=[E2]))
        await _expect(conn, CV, *_raw("f1", 2, "HYPOTHESIS", "DEREK", a,
                                      body={"hypothesis": "h"}))
        await _expect(conn, CV, *_raw("f1", 2, "HYPOTHESIS", "DEREK", a,
                                      refs=[E2]))
        # time cannot run backwards
        await _expect(conn, RE, *_raw("f1", 2, "HYPOTHESIS", "DEREK", a - 5,
                                      body={"hypothesis": "h"}, refs=[E2]))
        await conn.execute(*_raw("f1", 2, "HYPOTHESIS", "DEREK", a + 1,
                                 body={"hypothesis": "h"}, refs=[E2]))
        # NO SELF-CHALLENGE
        await _expect(conn, RE, *_raw("f1", 3, "PEER_CHALLENGE", "DEREK",
                                      a + 2, body={"challenge": "c"},
                                      refs=[E3], outcome="SUSTAINED"))
        await _expect(conn, CV, *_raw("f1", 3, "PEER_CHALLENGE", "AUDREY",
                                      a + 2, body={"challenge": "c"},
                                      refs=[E3], outcome="MAYBE"))
        await conn.execute(*_raw("f1", 3, "PEER_CHALLENGE", "AUDREY", a + 2,
                                 body={"challenge": "c"}, refs=[E3],
                                 outcome="SUSTAINED"))
        # THE EXPERIMENT: PAPER ONLY, PRE-REGISTERED METRIC AND STOP RULE
        for kw in ({"scope": "FUNDED", "metric": METRIC, "stop": STOP},
                   {"scope": None, "metric": METRIC, "stop": STOP},
                   {"scope": "PAPER_ONLY", "metric": None, "stop": STOP},
                   {"scope": "PAPER_ONLY", "metric": {"name": "m"},
                    "stop": STOP},
                   {"scope": "PAPER_ONLY", "metric": METRIC, "stop": {}},
                   {"scope": "PAPER_ONLY", "metric": METRIC,
                    "stop": {"max_duration_s": 400 * DAY}}):
            await _expect(conn, CV, *_raw("f1", 4, "BOUNDED_EXPERIMENT",
                                          "DEREK", a + 3, **kw))
        await conn.execute(*_raw("f1", 4, "BOUNDED_EXPERIMENT", "DEREK",
                                 a + 3, scope="PAPER_ONLY", metric=METRIC,
                                 stop=STOP))
        # THE CANDIDATE CANNOT CARRY A LIMIT, APPROVAL OR ACTIVATION, AND NO
        # ROW CAN DECLARE A PRODUCTION EFFECT
        for change in ({"risk_limits": {"x": 1}}, {"activate": True},
                       {"approved_by": "x"}, {"max_order_usd": 9}):
            await _expect(conn, CV, *_raw(
                "f1", 5, "CANDIDATE_IMPROVEMENT", "DEREK", a + 4,
                body={"description": "d", "change": change}))
        await _expect(conn, CV, *_raw(
            "f1", 5, "CANDIDATE_IMPROVEMENT", "DEREK", a + 4,
            body={"description": "d", "change": {"x": 1},
                  "activation": "now"}))
        await _expect(conn, CV, *_raw(
            "f1", 5, "CANDIDATE_IMPROVEMENT", "DEREK", a + 4,
            body={"description": "d", "change": {"x": 1}},
            effect="POLICY_ACTIVATED"))
        await conn.execute(*_raw("f1", 5, "CANDIDATE_IMPROVEMENT", "AUDREY",
                                 a + 4, body={"description": "d",
                                              "change": {"x": 1}}))
        # INDEPENDENT EVALUATION
        ok = {"body": {"metric_value": 0.02, "samples": 100}, "refs": [EF],
              "outcome": "PASS", "ds": T0 + DAY, "de": T0 + 7 * DAY}
        ev_at = T0 + 8 * DAY
        await _expect(conn, RE, *_raw("f1", 6, "INDEPENDENT_EVALUATION",
                                      "DEREK", ev_at, **ok))     # proposer
        await _expect(conn, RE, *_raw("f1", 6, "INDEPENDENT_EVALUATION",
                                      "AUDREY", ev_at, **ok))    # candidate
        await _expect(conn, RE, *_raw("f1", 6, "INDEPENDENT_EVALUATION",
                                      "XAVIER", ev_at,
                                      **dict(ok, ds=T0 - DAY)))  # old data
        await _expect(conn, RE, *_raw("f1", 6, "INDEPENDENT_EVALUATION",
                                      "XAVIER", ev_at,
                                      **dict(ok, ds=T0 + 5)))    # pre-reg
        for refs in ([E1], [EF, E2]):                            # reused
            await _expect(conn, RE, *_raw("f1", 6, "INDEPENDENT_EVALUATION",
                                          "XAVIER", ev_at,
                                          **dict(ok, refs=refs)))
        await _expect(conn, CV, *_raw("f1", 6, "INDEPENDENT_EVALUATION",
                                      "XAVIER", ev_at, **dict(ok, refs=[])))
        await _expect(conn, RE, *_raw("f1", 6, "INDEPENDENT_EVALUATION",
                                      "XAVIER", T0 + 30 * DAY,
                                      **dict(ok, de=T0 + 29 * DAY)))  # stop
        await _expect(conn, RE, *_raw("f1", 6, "INDEPENDENT_EVALUATION",
                                      "XAVIER", ev_at, **dict(
                                          ok, body={"metric_value": 0.02,
                                                    "samples": 900})))
        await _expect(conn, RE, *_raw("f1", 6, "INDEPENDENT_EVALUATION",
                                      "XAVIER", ev_at, **dict(
                                          ok, body={"metric_value": 0.0,
                                                    "samples": 10})))
        await _expect(conn, CV, *_raw("f1", 6, "INDEPENDENT_EVALUATION",
                                      "XAVIER", T0 + 6 * DAY, **ok))  # future
        await conn.execute(*_raw("f1", 6, "INDEPENDENT_EVALUATION", "XAVIER",
                                 ev_at, **dict(ok, outcome="FAIL", body={
                                     "metric_value": 0.0, "samples": 10})))
        # RELEASE ELIGIBILITY NEEDS A PASS
        await _expect(conn, RE, *_raw("f1", 7, "RELEASE_ELIGIBILITY",
                                      "XAVIER", ev_at + 1,
                                      outcome="ELIGIBLE_FOR_HUMAN_RELEASE_"
                                              "REVIEW"))
        # STAGES ARE APPEND-ONLY; THE FINDING IS FIXED; ITS STAGE FOLLOWS
        # ITS ROWS ONLY
        await _expect(conn, RE, "UPDATE agent_finding_stages SET "
                                "outcome='PASS' WHERE seq=6")
        await _expect(conn, RE, "DELETE FROM agent_finding_stages")
        await _expect(conn, RE, "DELETE FROM agent_findings")
        await _expect(conn, RE, "UPDATE agent_findings SET title='x'")
        await _expect(conn, RE, "UPDATE agent_findings SET "
                                "stage='RELEASE_ELIGIBILITY', stage_seq=7")
        await _expect(conn, RE, "UPDATE agent_findings SET "
                                "stage='HYPOTHESIS', stage_seq=2")
        await conn.execute(*_raw("f1", 99, "CLOSED", "AUDREY", ev_at + 2,
                                 body={"reason": "failed forward test"}))
        await _expect(conn, RE, *_raw("f1", 7, "RELEASE_ELIGIBILITY",
                                      "XAVIER", ev_at + 3,
                                      outcome="ELIGIBLE_FOR_HUMAN_RELEASE_"
                                              "REVIEW"))

        # A PASSED FINDING: the proposer cannot mark it; another agent can
        await conn.execute(FI, "f2", json.dumps([E1]), T0, None, 0, "NONE")
        rows = [
            _raw("f2", 1, "EVIDENCE", "DEREK", a, refs=[E1]),
            _raw("f2", 2, "HYPOTHESIS", "DEREK", a, body={"hypothesis": "h"},
                 refs=[E2]),
            _raw("f2", 3, "PEER_CHALLENGE", "XAVIER", a,
                 body={"challenge": "c"}, refs=[E3], outcome="SUSTAINED"),
            _raw("f2", 4, "BOUNDED_EXPERIMENT", "DEREK", a,
                 scope="PAPER_ONLY", metric=METRIC, stop=STOP),
            _raw("f2", 5, "CANDIDATE_IMPROVEMENT", "DEREK", a,
                 body={"description": "d", "change": {"x": 1}}),
            _raw("f2", 6, "INDEPENDENT_EVALUATION", "AUDREY", ev_at, **ok)]
        for r in rows:
            await conn.execute(*r)
        rel = dict(outcome="ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW")
        await _expect(conn, RE, *_raw("f2", 7, "RELEASE_ELIGIBILITY",
                                      "DEREK", ev_at + 1, **rel))
        await _expect(conn, CV, *_raw("f2", 7, "RELEASE_ELIGIBILITY",
                                      "XAVIER", ev_at + 1,
                                      outcome="RELEASED"))
        await conn.execute(*_raw("f2", 7, "RELEASE_ELIGIBILITY", "XAVIER",
                                 ev_at + 1, **rel))
        assert await conn.fetchval(
            "SELECT stage FROM agent_findings WHERE finding_id='f2'") \
            == "RELEASE_ELIGIBILITY"

        # A REFUTED HYPOTHESIS IS NOT EXPERIMENTED ON
        await conn.execute(FI, "f3", json.dumps([E1]), T0, None, 0, "NONE")
        for r in rows[:2]:
            await conn.execute(r[0], "f3", *r[2:])
        await conn.execute(*_raw("f3", 3, "PEER_CHALLENGE", "XAVIER", a,
                                 body={"challenge": "c"}, refs=[E3],
                                 outcome="REFUTED"))
        await _expect(conn, RE, *_raw("f3", 4, "BOUNDED_EXPERIMENT", "DEREK",
                                      a, scope="PAPER_ONLY", metric=METRIC,
                                      stop=STOP))
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_203_is_idempotent_and_its_rollback_refuses_over_findings():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await _seed(conn)
        got = await CL.open_finding(
            conn, proposer="AUDREY", title="t", statement="s",
            evidence_refs=[E1], evidence_window_end=T0, at=T0 + 1)
        assert got["ok"]
        await _expect(conn, asyncpg.RaiseError, DOWN)
        sp = conn.transaction()
        await sp.start()
        # the findings are part of the record: only an empty loop drops
        await conn.execute("ALTER TABLE agent_finding_stages DISABLE "
                           "TRIGGER agent_finding_stages_guard_trg")
        await conn.execute("ALTER TABLE agent_findings DISABLE TRIGGER "
                           "agent_findings_guard_trg")
        await conn.execute("DELETE FROM agent_finding_stages")
        await conn.execute("DELETE FROM agent_findings")
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT to_regclass('agent_findings')") is None
        assert await conn.fetchval(
            "SELECT to_regclass('agent_finding_stages')") is None
        await conn.execute(DOWN)                                 # idempotent
        got = await CL.open_finding(
            conn, proposer="AUDREY", title="t", statement="s",
            evidence_refs=[E1], evidence_window_end=T0, at=T0 + 1)
        assert got["refusal"] == CL.R_NO_SCHEMA
        await conn.execute(UP)                              # re-applies
        await sp.rollback()
    finally:
        await tx.rollback()
        await conn.close()
