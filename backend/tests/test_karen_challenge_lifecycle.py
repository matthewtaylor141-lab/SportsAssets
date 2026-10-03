"""KAREN'S CHALLENGES (migration 207): GROUNDED, PEER-ANSWERED, NEVER
SELF-RESOLVED -- in code AND in the database.

  * a challenge cites at least one evidence record that EXISTS, and the
    challenged record must exist (no ungrounded challenge; the CHECK refuses
    an empty reference list even to a caller that skips the module);
  * OPEN -> RESPONDED by the TARGET agent only (not Karen, not another
    agent) -> UPHELD (the target may concede, or a third party) | REJECTED
    (never by the target, never by Karen); WITHDRAWN only by Karen; no
    resolution before the response; nothing recorded twice;
  * the record is fixed at creation and never deleted; the history is
    append-only;
  * a REFUTED loop challenge (Karen's PEER_CHALLENGE stage through the
    collaboration loop) is blocking and is assessed for false block, never
    by Karen; an UPHELD challenge links its improvement, never by Karen.

Each test runs in one transaction, rolled back.
"""
from __future__ import annotations

import json
import os

import pytest

from sportsassets.agents import collaboration_loop as CL
from sportsassets.agents import karen as K
from sportsassets.agents import registry as R

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

T0 = 1_790_000_000.0
D1 = {"kind": "agent_decisions", "id": "adr:k207-lc-1"}
D2 = {"kind": "agent_decisions", "id": "adr:k207-lc-2"}
D3 = {"kind": "agent_decisions", "id": "adr:k207-lc-3"}


def _rec(**kw):
    return dict({"target_agent": "DEREK", "target_kind": "agent_decisions",
                 "target_id": D1["id"], "detector": "T", "claim": "c",
                 "severity": "MEDIUM", "evidence_refs": [D1],
                 "record_at": T0, "at": T0 + 5}, **kw)


# ════════════════════════════════════════════════════════════════════
# PURE GUARDS
# ════════════════════════════════════════════════════════════════════

def test_the_open_guards_refuse_by_name():
    assert K.check_open(_rec()) is None
    assert K.check_open(_rec(target_agent="KAREN")) == K.R_UNKNOWN_TARGET
    assert K.check_open(_rec(target_agent="NOBODY")) == K.R_UNKNOWN_TARGET
    assert K.check_open(_rec(evidence_refs=[])) == K.R_UNGROUNDED
    assert K.check_open(_rec(evidence_refs=None)) == K.R_UNGROUNDED
    assert K.check_open(_rec(evidence_refs=[{"kind": "agent_decisions"}])) \
        == CL.R_BAD_REF
    assert K.check_open(_rec(evidence_refs=[{"kind": "nope", "id": "1"}])) \
        == CL.R_UNKNOWN_KIND
    assert K.check_open(_rec(severity="URGENT")) == K.R_BAD_SEVERITY
    assert K.check_open(_rec(claim="  ")) == K.R_TEXT
    assert K.check_open(_rec(record_at=T0 + 10)) == K.R_TIME
    assert K.check_open(_rec(target_kind="made_up")) == CL.R_UNKNOWN_KIND
    for k in ("approved_by", "activate", "max_order_usd", "promotion",
              "order"):
        assert K.check_open(_rec(body={"x": {k: 1}})) == K.R_AUTHORITY_KEYS


# ════════════════════════════════════════════════════════════════════
# POSTGRES
# ════════════════════════════════════════════════════════════════════

async def _tx():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    await R.ensure_identities(conn)
    for i, r in enumerate((D1, D2, D3)):
        await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " decided_at) VALUES ($1,$2,'TEST',to_timestamp($3)) "
            "ON CONFLICT DO NOTHING", r["id"],
            ("DEREK", "XAVIER", "AUDREY")[i], T0)
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


async def _open(conn, **kw):
    args = dict(target_agent="DEREK", target_kind="agent_decisions",
                target_id=D1["id"], detector="DECISION_WITHOUT_EVIDENCE",
                claim="decision cites nothing", severity="MEDIUM",
                evidence_refs=[D1], record_at=T0, at=T0 + 60)
    args.update(kw)
    return await K.open_challenge(conn, **args)


@pg
@pytest.mark.asyncio
async def test_a_challenge_is_grounded_idempotent_and_fixed():
    conn, tx = await _tx()
    try:
        got = await _open(conn)
        assert got["ok"] and got["created"] and got["state"] == "OPEN", got
        cid = got["challenge_id"]
        again = await _open(conn)
        assert again["ok"] and again["created"] is False
        assert again["challenge_id"] == cid
        one = await K.challenge(conn, cid)
        c = one["challenge"]
        assert c["challenger"] == "KAREN" and c["target_agent"] == "DEREK"
        assert c["evidence_refs"] == [D1] and c["time_to_challenge_s"] == 60
        assert [e["kind"] for e in one["events"]] == ["OPENED"]
        assert one["production_effect"] == "NONE"
        # THE GROUNDING RULE: a reference to nothing, or no reference at all,
        # is refused -- in code ...
        bad = await _open(conn, target_id=D2["id"], evidence_refs=[
            {"kind": "agent_decisions", "id": "adr:does-not-exist"}])
        assert bad["refusal"] == K.R_UNGROUNDED, bad
        bad = await _open(conn, target_id=D2["id"], evidence_refs=[])
        assert bad["refusal"] == K.R_UNGROUNDED, bad
        bad = await _open(conn, target_id="adr:missing-target")
        assert bad["refusal"] == K.R_TARGET_MISSING, bad
        bad = await _open(conn, target_id=D2["id"], target_agent="KAREN")
        assert bad["refusal"] == K.R_UNKNOWN_TARGET
        # ... and in the database, for a caller that skips the module
        ins = ("INSERT INTO karen_challenges (challenge_id, target_agent, "
               " target_kind, target_id, detector, claim, severity, "
               " evidence_refs, record_at, challenged_at) VALUES "
               " ($1,$2,'agent_decisions',$3,'X','c','LOW',$4::jsonb,"
               " to_timestamp($5),to_timestamp($5))")
        await _expect(conn, ins, "k1", "DEREK", D2["id"], "[]", T0)
        await _expect(conn, ins, "k1", "DEREK", D2["id"],
                      '[{"kind":"agent_decisions"}]', T0)
        await _expect(conn, ins, "k1", "KAREN", D2["id"],
                      json.dumps([D2]), T0)                 # never herself
        # a valid row through the same path is accepted
        await conn.execute(
            "INSERT INTO karen_challenges (challenge_id, target_agent, "
            " target_kind, target_id, detector, claim, severity, "
            " evidence_refs, record_at, challenged_at) VALUES ('k2','XAVIER',"
            " 'agent_decisions',$1,'Y','c','LOW',$2::jsonb,"
            " to_timestamp($3),to_timestamp($3))", D2["id"],
            json.dumps([D2]), T0)
        # a challenge cannot start anywhere but OPEN
        await _expect(conn, "INSERT INTO karen_challenges (challenge_id, "
                      " target_agent, target_kind, target_id, detector, "
                      " claim, severity, evidence_refs, record_at, "
                      " challenged_at, state) VALUES ('k3','XAVIER',"
                      " 'agent_decisions',$1,'Z','c','LOW',$2::jsonb,"
                      " to_timestamp($3),to_timestamp($3),'UPHELD')",
                      D2["id"], json.dumps([D2]), T0)
        # FIXED AT CREATION; NEVER DELETED; HISTORY APPEND-ONLY
        for col, val in (("claim", "'other'"), ("severity", "'HIGH'"),
                         ("target_agent", "'XAVIER'"),
                         ("evidence_refs", "'[]'::jsonb"),
                         ("blocked", "true")):
            await _expect(conn, "UPDATE karen_challenges SET %s=%s WHERE "
                          " challenge_id=$1" % (col, val), cid)
        await _expect(conn, "DELETE FROM karen_challenges WHERE "
                      " challenge_id=$1", cid)
        await _expect(conn, "UPDATE karen_challenge_events SET actor='X'")
        await _expect(conn, "DELETE FROM karen_challenge_events")
        # no production effect, no authority keys
        await _expect(conn, "UPDATE karen_challenges SET "
                      " production_effect='LIVE' WHERE challenge_id=$1", cid)
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_peer_response_is_the_targets_and_karen_never_resolves():
    conn, tx = await _tx()
    try:
        cid = (await _open(conn))["challenge_id"]
        # NOT RESOLVED BEFORE THE TARGET ANSWERS
        got = await K.resolve(conn, cid, resolver="AUDREY", outcome="UPHELD",
                              reason="r", at=T0 + 70)
        assert got["refusal"] == K.R_NEEDS_RESPONSE
        # ONLY THE TARGET ANSWERS
        for who, why in (("KAREN", K.R_KAREN_CANNOT_RESPOND),
                         ("agent:karen", K.R_KAREN_CANNOT_RESPOND),
                         ("XAVIER", K.R_NOT_THE_TARGET),
                         ("AUDREY", K.R_NOT_THE_TARGET),
                         ("OWNER", K.R_NOT_THE_TARGET)):
            got = await K.respond(conn, cid, agent=who, stance="DISPUTE",
                                  response="no", at=T0 + 70)
            assert got["refusal"] == why, (who, got)
        got = await K.respond(conn, cid, agent="DEREK", stance="MAYBE",
                              response="x", at=T0 + 70)
        assert got["refusal"] == K.R_BAD_STANCE
        got = await K.respond(conn, cid, agent="derek", stance="DISPUTE",
                              response="the decision is indexed elsewhere",
                              evidence_refs=[D2], at=T0 + 70)
        assert got["ok"] and got["state"] == "RESPONDED", got
        # ...once
        got = await K.respond(conn, cid, agent="DEREK", stance="CONCEDE",
                              response="x", at=T0 + 71)
        assert got["refusal"] == K.R_WRONG_STATE
        # KAREN NEVER RESOLVES HER OWN CHALLENGE (code)
        for who in ("KAREN", "karen", "slack:karen"):
            for outcome in ("UPHELD", "REJECTED"):
                got = await K.resolve(conn, cid, resolver=who,
                                      outcome=outcome, reason="r",
                                      at=T0 + 80)
                assert got["refusal"] == K.R_SELF_RESOLUTION, (who, got)
        # ... nor in the database
        await _expect(conn, "UPDATE karen_challenges SET state='UPHELD', "
                      " outcome='UPHELD', outcome_reason='r', "
                      " resolved_by='KAREN', resolved_at=to_timestamp($2) "
                      " WHERE challenge_id=$1", cid, T0 + 80)
        # THE TARGET MAY NOT REJECT A CHALLENGE AGAINST ITSELF
        got = await K.resolve(conn, cid, resolver="DEREK", outcome="REJECTED",
                              reason="mine is fine", at=T0 + 80)
        assert got["refusal"] == K.R_TARGET_CANNOT_REJECT
        await _expect(conn, "UPDATE karen_challenges SET state='REJECTED', "
                      " outcome='REJECTED', outcome_reason='r', "
                      " resolved_by='DEREK', resolved_at=to_timestamp($2) "
                      " WHERE challenge_id=$1", cid, T0 + 80)
        # a third party rejects it
        got = await K.resolve(conn, cid, resolver="AUDREY",
                              outcome="REJECTED",
                              reason="the index entry links elsewhere",
                              at=T0 + 90)
        assert got["ok"] and got["state"] == "REJECTED", got
        # FINISHED IS FINISHED
        got = await K.resolve(conn, cid, resolver="OWNER", outcome="UPHELD",
                              reason="r", at=T0 + 95)
        assert got["refusal"] == K.R_WRONG_STATE
        got = await K.withdraw(conn, cid, reason="r", at=T0 + 95)
        assert got["refusal"] == K.R_WRONG_STATE
        await _expect(conn, "UPDATE karen_challenges SET state='OPEN' "
                      " WHERE challenge_id=$1", cid)
        await _expect(conn, "UPDATE karen_challenges SET resolved_by='OWNER'"
                      " WHERE challenge_id=$1", cid)
        events = [e["kind"] for e in (await K.challenge(conn, cid))["events"]]
        assert events == ["OPENED", "RESPONDED", "REJECTED"]
        # THE TARGET CONCEDES: UPHELD
        cid2 = (await _open(conn, target_agent="XAVIER", target_id=D2["id"],
                            evidence_refs=[D2]))["challenge_id"]
        assert (await K.respond(conn, cid2, agent="XAVIER", stance="CONCEDE",
                                response="yes, a defect",
                                at=T0 + 70))["ok"]
        got = await K.resolve(conn, cid2, resolver="XAVIER",
                              outcome="UPHELD", reason="conceded",
                              at=T0 + 75)
        assert got["ok"] and got["state"] == "UPHELD", got
        # ONLY KAREN WITHDRAWS
        cid3 = (await _open(conn, target_agent="AUDREY", target_id=D3["id"],
                            evidence_refs=[D3]))["challenge_id"]
        got = await K.withdraw(conn, cid3, reason="r", at=T0 + 70,
                               actor="AUDREY")
        assert got["refusal"] == K.R_ONLY_KAREN_WITHDRAWS
        got = await K.withdraw(conn, cid3, reason="my evidence was wrong",
                               at=T0 + 70)
        assert got["ok"] and got["state"] == "WITHDRAWN"
        # no state can be skipped in the database
        cid4 = (await _open(conn, detector="OTHER"))["challenge_id"]
        await _expect(conn, "UPDATE karen_challenges SET state='UPHELD', "
                      " outcome='UPHELD', outcome_reason='r', "
                      " resolved_by='OWNER', resolved_at=to_timestamp($2) "
                      " WHERE challenge_id=$1", cid4, T0 + 80)
        await _expect(conn, "UPDATE karen_challenges SET state='RESPONDED', "
                      " response_stance='CONCEDE', response='x', "
                      " responded_by='AUDREY', responded_at=to_timestamp($2)"
                      " WHERE challenge_id=$1", cid4, T0 + 80)
    finally:
        await tx.rollback()
        await conn.close()


async def _loop_finding(conn) -> str:
    got = await CL.open_finding(
        conn, proposer="DEREK", title="stale books after 21:00",
        statement="entries refused for stale books cluster after 21:00",
        evidence_refs=[D1], evidence_window_end=T0, at=T0 + 10)
    assert got["ok"], got
    fid = got["finding_id"]
    got = await CL.record_hypothesis(conn, fid, actor="DEREK",
                                     hypothesis="a later read helps",
                                     evidence_refs=[D2], at=T0 + 20)
    assert got["ok"], got
    return fid


@pg
@pytest.mark.asyncio
async def test_karen_challenges_in_the_loop_and_a_refuted_challenge_is_assessed_for_false_block():
    conn, tx = await _tx()
    try:
        fid = await _loop_finding(conn)
        # only a finding at its HYPOTHESIS stage
        got = await K.challenge_finding(conn, "afnd:none", claim="c",
                                        outcome="REFUTED",
                                        evidence_refs=[D3], at=T0 + 30)
        assert got["refusal"] == CL.R_NO_SUCH_FINDING
        got = await K.challenge_finding(conn, fid, claim="c",
                                        outcome="REFUTED", evidence_refs=[],
                                        at=T0 + 30)
        assert got["refusal"] == K.R_UNGROUNDED
        got = await K.challenge_finding(
            conn, fid, claim="the hypothesis rests on a defective record",
            outcome="REFUTED", evidence_refs=[D3], at=T0 + 30,
            severity="HIGH")
        assert got["ok"] and got["blocked"] is True, got
        cid = got["challenge_id"]
        loop = await CL.finding(conn, fid)
        st = loop["stages"][-1]
        assert st["stage"] == "PEER_CHALLENGE" and st["actor"] == "KAREN"
        assert st["outcome"] == "REFUTED"
        c = (await K.challenge(conn, cid))["challenge"]
        assert c["target_agent"] == "DEREK" and c["finding_id"] == fid
        assert c["target_kind"] == "agent_findings" and c["blocked"] is True
        # a REFUTED hypothesis can only close (203's guard, unchanged)
        got = await CL.register_experiment(
            conn, fid, actor="DEREK", design="x",
            metric={"name": "m", "direction": "INCREASE", "threshold": 0.1},
            stopping_rule={"max_samples": 10}, at=T0 + 40)
        assert got["refusal"] == CL.R_REFUTED
        # false block: only once the challenge is finished, never by Karen
        got = await K.assess_false_block(conn, cid, assessor="AUDREY",
                                         false_block=True,
                                         evidence_refs=[D1], at=T0 + 50)
        assert got["refusal"] == K.R_NOT_FINISHED
        assert (await K.respond(conn, cid, agent="DEREK", stance="DISPUTE",
                                response="the record was fine",
                                at=T0 + 50))["ok"]
        assert (await K.resolve(conn, cid, resolver="AUDREY",
                                outcome="REJECTED", reason="record fine",
                                at=T0 + 60))["ok"]
        got = await K.assess_false_block(conn, cid, assessor="KAREN",
                                         false_block=False,
                                         evidence_refs=[D1], at=T0 + 70)
        assert got["refusal"] == K.R_SELF_RESOLUTION
        got = await K.assess_false_block(conn, cid, assessor="AUDREY",
                                         false_block=True,
                                         evidence_refs=[D1], at=T0 + 70)
        assert got["ok"] and got["false_block"] is True, got
        got = await K.assess_false_block(conn, cid, assessor="OWNER",
                                         false_block=False,
                                         evidence_refs=[D1], at=T0 + 80)
        assert got["refusal"] == K.R_ALREADY
        await _expect(conn, "UPDATE karen_challenges SET false_block=false "
                      " WHERE challenge_id=$1", cid)
        # a non-blocking challenge has no false-block result
        cid2 = (await _open(conn))["challenge_id"]
        got = await K.assess_false_block(conn, cid2, assessor="AUDREY",
                                         false_block=True,
                                         evidence_refs=[D1], at=T0 + 80)
        assert got["refusal"] == K.R_NOT_BLOCKING
        # a second loop challenge on the same finding is refused by the loop
        got = await K.challenge_finding(conn, fid, claim="again",
                                        outcome="REFUTED",
                                        evidence_refs=[D3], at=T0 + 90)
        assert got["ok"] is False
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_upheld_challenge_links_its_improvement_never_by_karen():
    conn, tx = await _tx()
    try:
        fid = await _loop_finding(conn)
        cid = (await _open(conn))["challenge_id"]
        got = await K.link_improvement(conn, cid, actor="AUDREY", at=T0 + 70,
                                       finding_id=fid)
        assert got["refusal"] == K.R_NOT_UPHELD
        await K.respond(conn, cid, agent="DEREK", stance="CONCEDE",
                        response="yes", at=T0 + 70)
        await K.resolve(conn, cid, resolver="DEREK", outcome="UPHELD",
                        reason="conceded", at=T0 + 71)
        got = await K.link_improvement(conn, cid, actor="KAREN", at=T0 + 72,
                                       finding_id=fid)
        assert got["refusal"] == K.R_SELF_RESOLUTION
        got = await K.link_improvement(conn, cid, actor="AUDREY", at=T0 + 72,
                                       finding_id="afnd:none")
        assert got["refusal"] == K.R_NO_IMPROVEMENT
        got = await K.link_improvement(conn, cid, actor="AUDREY", at=T0 + 72,
                                       finding_id=fid,
                                       impact={"approved_by": "x"})
        assert got["refusal"] == K.R_AUTHORITY_KEYS
        got = await K.link_improvement(conn, cid, actor="AUDREY", at=T0 + 72,
                                       finding_id=fid,
                                       impact={"note": "experiment opened"})
        assert got["ok"], got
        await _expect(conn, "UPDATE karen_challenges SET "
                      " improvement_linked_by='OWNER' WHERE challenge_id=$1",
                      cid)
        events = [e["kind"] for e in (await K.challenge(conn, cid))["events"]]
        assert events == ["OPENED", "RESPONDED", "UPHELD",
                          "IMPROVEMENT_LINKED"]
    finally:
        await tx.rollback()
        await conn.close()
