"""MIGRATION 301 §1 AND THE SEVEN AGENTS' DURABLE WORK QUEUES, on Postgres
(owner R30 program section 17).

  §1 MIGRATION. 301 is idempotent; a 226-shaped insert (Xavier's evidence
     requests, work_queue.py unchanged) gains its terms from the trigger;
     the pairing CHECK refuses a kind its agent does not own, a collaborator
     who is the owner, an empty evidence list; an ATTEMPT names its outcome
     and a later next attempt (and its blocker when BLOCKED); nothing
     follows a terminal event; Eddie's and Scout's sessions may enqueue
     their own work (the queue is not authority-guarded) but may write
     neither governance table; the 217 registry of 301 is a superset of
     225's; the rollback refuses while 301 records exist and drops cleanly
     when none do.
  §2 THE PRODUCERS THROUGH THE REAL WRITERS: Eddie's runner (estimates /
     outcomes, a refusal as a BLOCKED attempt), Karen's runner (deferred
     detector candidates -> investigations completed by the challenge that
     later opens; bounded per detector and failed once the record leaves
     her window), the peer responder (challenge answers and evaluations,
     the evaluation depending on the response), Derek's stale-refused
     candidates through his REAL decision path (paper_derek.decide_one:
     refused stale -> an item; re-decided stale -> an attempt; decided on
     fresh evidence -> COMPLETED; PASSIVE, never an enqueued
     reacquisition), Scout's runner (research questions), Allie's
     allocation reviews (an OK run completes, a failed run blocks), Audrey's
     reconciliations and her open findings through the real audit writer
     (paper_audrey.finding -> a follow-up; paper_audrey.open_task ->
     COMPLETED).
  §3 OVERDUE, EXPIRY AND THE ATTEMPT SCHEDULE: an item's SLA counts from
     when the work arose (a backlog older than the queue is overdue at
     once); past its horizon it is FAILED and re-enqueued while still
     pending; an attempt is recorded only when its scheduled next attempt
     is due (an unchanged outcome backs off, a change resets), so every
     recorded next attempt is true and every kind fits the database's
     per-request bound.
  §4 THE WORK STATE FROM THE QUEUE, read from the database: Eddie HANDOFF on
     an untouched item, never IDLE while an item is open -- not when another
     agent's backlog exceeds the read bound, not when the queue read fails.

R30B REVIEW NOTES. The Derek proof had used hand-built decisions and pinned
his candidate items as an enqueued reacquisition; nothing acquires their
evidence, so it now pins HANDOFF_PENDING through his real decision path. The
rollback proof's shared-database case is a visible SKIP, never a silent
return.
"""
from __future__ import annotations

import re
import uuid

import asyncpg
import pytest

from sportsassets import agent_work_state as AWS
from sportsassets.agents import agent_work as AW
from sportsassets.agents import eddie_runner as ER
from sportsassets.agents import karen as K
from sportsassets.agents import karen_runner as KR
from sportsassets.agents import peer_responder as PRS
from sportsassets.agents import registry as R
from sportsassets.agents import scout_runner as SR

from tests import agent_ops_fixture as F

pg = F.pg
NOW = F.NOW

REQ = ("INSERT INTO agent_work_requests (request_id, agent_id, kind, "
       " position_kind, group_id, reason, batch_id, enqueued_at, "
       " expires_at, due_at, evidence_needed, collaborator, "
       " next_attempt_at) VALUES ($1,$2,$3,$4,'s1',$5,'b',"
       " to_timestamp($6),to_timestamp($6 + 3600),to_timestamp($6 + 600),"
       " $7::jsonb,$8,to_timestamp($6))")
EV = ("INSERT INTO agent_work_request_events (request_id, state, at, "
      " outcome, blocker, next_attempt_at, evidence_table, evidence_id, "
      " failure) VALUES ($1,$2,to_timestamp($3),$4,$5,to_timestamp($6),"
      " $7,$8,$9)")


async def _decision_ref(conn, agent="DEREK"):
    ref = "adr:r30b-%s" % uuid.uuid4().hex[:10]
    await conn.execute(
        "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
        " decided_at) VALUES ($1,$2,'TEST',to_timestamp($3))", ref, agent,
        NOW - 7200)
    return ref


# ═════════════════════════════════════════════════════════════════════
# §1 THE MIGRATION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_301_is_idempotent_checked_and_fills_226_rows():
    conn, tx = await F.tx()
    try:
        await conn.execute(F.UP)                                 # twice
        E = asyncpg.CheckViolationError
        X = asyncpg.IntegrityConstraintViolationError
        ev = '["EXECUTION_ESTIMATE"]'
        await conn.execute(REQ, "q1", "EDDIE", "EXECUTION_ESTIMATE",
                           "DECISION", "ENTER_WITHOUT_ESTIMATE", NOW, ev,
                           "DEREK")
        # a kind its agent does not own; the wrong subject kind
        await F.expect(conn, E, REQ, "q2", "EDDIE", "CANDIDATE_FRESH_EVIDENCE",
                       "MARKET", "REFUSED_ON_STALE_PROBABILITY", NOW, ev,
                       None)
        await F.expect(conn, E, REQ, "q2", "EDDIE", "EXECUTION_ESTIMATE",
                       "MARKET", "ENTER_WITHOUT_ESTIMATE", NOW, ev, None)
        # the owner as its own collaborator; no evidence named
        await F.expect(conn, E, REQ, "q2", "EDDIE", "EXECUTION_ESTIMATE",
                       "DECISION", "ENTER_WITHOUT_ESTIMATE", NOW, ev,
                       "EDDIE")
        await F.expect(conn, E, REQ, "q2", "EDDIE", "EXECUTION_ESTIMATE",
                       "DECISION", "ENTER_WITHOUT_ESTIMATE", NOW, "[]",
                       None)
        # a 226-shaped insert (work_queue.py's own columns) gains its terms
        await conn.execute(
            "INSERT INTO agent_work_requests (request_id, agent_id, kind, "
            " position_kind, group_id, us_market_slug, reason, batch_id, "
            " enqueued_at, expires_at) VALUES ('x1','XAVIER','PROBABILITY',"
            " 'PAPER','g','s','WAITING_FOR_FRESH_EVIDENCE','b',"
            " to_timestamp($1),to_timestamp($1 + 300))", NOW)
        r = await conn.fetchrow(
            "SELECT due_at = expires_at AS sla_is_expiry, evidence_needed, "
            "       blocker, next_attempt_at = enqueued_at AS next_now "
            "  FROM agent_work_requests WHERE request_id='x1'")
        assert r["sla_is_expiry"] and r["next_now"]
        assert F.j(r["evidence_needed"]) == ["PROBABILITY"]
        assert r["blocker"] == "WAITING_FOR_FRESH_EVIDENCE"
        # attempts: outcome, a later next attempt, a blocker when BLOCKED
        await conn.execute(EV, "q1", "ENQUEUED", NOW, None, None, None,
                           None, None, None)
        await F.expect(conn, E, EV, "q1", "ATTEMPTED", NOW, "NO_CHANGE",
                       None, NOW, None, None, None)          # next == now
        await F.expect(conn, E, EV, "q1", "ATTEMPTED", NOW, "BLOCKED",
                       None, NOW + 60, None, None, None)     # no blocker
        await F.expect(conn, E, EV, "q1", "ATTEMPTED", NOW, "MAYBE",
                       None, NOW + 60, None, None, None)
        await F.expect(conn, E, EV, "q1", "DISPATCHED", NOW, "PROGRESSED",
                       None, NOW + 60, None, None, None)
        await conn.execute(EV, "q1", "ATTEMPTED", NOW, "BLOCKED",
                           "DB_REFUSED", NOW + 60, None, None, None)
        await conn.execute(EV, "q1", "ATTEMPTED", NOW + 1, "PROGRESSED",
                           None, NOW + 61, None, None, None)  # repeatable
        await conn.execute(EV, "q1", "FAILED", NOW + 2, None, None, None,
                           None, None, "TEST")
        await F.expect(conn, X, EV, "q1", "ATTEMPTED", NOW + 3, "PROGRESSED",
                       None, NOW + 90, None, None, None)     # after terminal
        # append-only stays append-only
        await F.expect(conn, X, "UPDATE agent_work_requests SET blocker='x'"
                       " WHERE request_id='q1'")
    finally:
        await F.done(conn, tx)


@pg
async def test_eddie_and_scout_enqueue_their_own_work_but_no_governance_row():
    from sportsassets.agents import pos_authority as PA
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        for agent, kind, scope, reason in (
                ("EDDIE", "EXECUTION_ESTIMATE", "DECISION",
                 "ENTER_WITHOUT_ESTIMATE"),
                ("SCOUT", "RESEARCH_QUESTION", "FEATURE",
                 "FEATURE_UNDER_TEST")):
            async with conn.transaction():
                await PA.act_as(conn, agent)
                await conn.execute(REQ, "own-" + agent, agent, kind, scope,
                                   reason, NOW, '["X"]', None)
            sp = conn.transaction()
            await sp.start()
            try:
                await PA.act_as(conn, agent)
                try:
                    await conn.execute(
                        "INSERT INTO improvement_clusters (cluster_id, "
                        " cluster_key, source, finding_class, target_agent,"
                        " owner_agent, title, first_seen_at, opened_at, "
                        " opened_by) VALUES "
                        " ('rcc:000000000000000000000001','KAREN|D|XAVIER',"
                        " 'KAREN','D','XAVIER','XAVIER','t',now(),now(),"
                        " 'IMPROVEMENT_CLUSTERS_V1')")
                    await conn.execute(
                        "INSERT INTO improvement_cluster_events (cluster_id,"
                        " kind, status_to, actor, actor_class, recorded_by, "
                        " at) VALUES ('rcc:000000000000000000000001',"
                        " 'OPENED','OPEN','IMPROVEMENT_CLUSTERS_V1',"
                        " 'RUNNER','IMPROVEMENT_CLUSTERS_V1',now())")
                    raise AssertionError("governance write passed")
                except asyncpg.PostgresError as e:
                    assert "HAS_NO_AUTHORITY" in str(e), e
            finally:
                await sp.rollback()
        guarded = {r["tbl"] for r in await conn.fetch(
            "SELECT tbl FROM pos_agents_authority_guarded_tables()")}
        assert {"agent_lesson_supersessions",
                "improvement_cluster_events"} <= guarded
        assert not guarded & {"agent_work_requests",
                              "agent_work_request_events", "agent_work_open"}
        for t in ("agent_lesson_supersessions", "improvement_cluster_events"):
            assert await conn.fetchval(
                "SELECT count(*) FROM pg_trigger WHERE tgrelid=$1::regclass "
                "   AND tgname='aa_pos_agents_no_authority_trg'", t) == 1
    finally:
        await F.done(conn, tx)


def _registry_tables(sql_text: str) -> list:
    body = sql_text.split("CREATE OR REPLACE FUNCTION "
                          "pos_agents_authority_guarded_tables()")[-1]
    body = body.split("$$;")[0]
    return re.findall(r"\('([a-z_]+)',", body)


def test_the_301_registry_is_a_superset_of_225s_and_its_rollback_restores_it():
    """INTEGRATION GUARD: 301 re-declares 217's registry; it must keep every
    table 225 (which the intent stream may extend in place) declares, and its
    rollback must restore exactly 225's list."""
    m225 = (F.MIG / "225_live_parity.sql").read_text()
    t225 = _registry_tables(m225)
    t301 = _registry_tables(F.UP)
    down = _registry_tables(F.DOWN)
    assert t225 and set(t225) <= set(t301), set(t225) - set(t301)
    assert set(t301) - set(t225) == {"agent_lesson_supersessions",
                                     "improvement_cluster_events"}
    assert down == t225


@pg
async def test_the_rollback_refuses_with_records_and_drops_cleanly_without():
    conn, tx = await F.tx()
    try:
        await conn.execute(REQ, "rb1", "EDDIE", "EXECUTION_ESTIMATE",
                           "DECISION", "ENTER_WITHOUT_ESTIMATE", NOW,
                           '["X"]', None)
        sp = conn.transaction()
        await sp.start()
        try:
            try:
                await conn.execute(F.DOWN)
                raise AssertionError("rollback passed with records")
            except asyncpg.PostgresError as e:
                assert "ROLLBACK_301_REFUSED" in str(e)
        finally:
            await sp.rollback()
    finally:
        await F.done(conn, tx)
    conn = await asyncpg.connect(F.H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        if await conn.fetchval("SELECT count(*) FROM agent_work_requests "
                               " WHERE kind NOT IN ('PROBABILITY',"
                               " 'VENUE_BOOK','GAME_STATE',"
                               " 'MANAGEMENT_REASSESSMENT')") or \
                await conn.fetchval("SELECT count(*) FROM improvement_"
                                    "clusters"):
            # a shared database holding committed 301 records: the rollback
            # refuses by design (proven above); the drop-and-reapply half
            # needs a database without them -- a visible skip, never a pass
            pytest.skip("the database holds committed 301 records: the "
                        "drop half of the rollback proof needs none")
        await conn.execute(F.DOWN)
        assert await conn.fetchval(
            "SELECT to_regclass('improvement_clusters')") is None
        assert await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE "
            " table_name='agent_work_requests' AND column_name='due_at'") == 0
        await conn.execute(F.UP)
        assert await conn.fetchval(
            "SELECT to_regclass('agent_lesson_supersessions')") is not None
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_224s_rollback_still_applies_with_301_present():
    """(R30B review) 301's supersession CHECK had called 224's
    agent_memory_refs_grounded, so 224's rollback failed with
    DependentObjectsStillExist (the 224 test swallows errors, so nothing
    saw it). 301 now grounds refs with its own copy."""
    down224 = (F.MIG / "rollback" /
               "224_agent_identity_memory.down.sql").read_text()
    conn, tx = await F.tx()
    try:
        if await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM agent_identity_versions "
                " WHERE identity_version > 1 "
                "    OR approved_by <> 'PENDING_OWNER_APPROVAL')"):
            pytest.skip("this database holds a later identity version or "
                        "an approval: 224's rollback refuses by design")
        await conn.execute(down224)          # raises on any 301 dependency
        assert await conn.fetchval(
            "SELECT to_regclass('agent_memory_events')") is None
        assert await conn.fetchval(
            "SELECT to_regclass('agent_lesson_supersessions') IS NOT NULL")
        assert await conn.fetchval(
            "SELECT to_regprocedure('agent_ops_refs_grounded(jsonb)') "
            "IS NOT NULL")
        assert "agent_memory_refs_grounded" not in F.UP.split(
            "CREATE TABLE IF NOT EXISTS agent_lesson_supersessions")[1]
    finally:
        await F.done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# §2 THE PRODUCERS THROUGH THE REAL WRITERS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_eddies_estimates_and_a_refusal_through_his_runner(monkeypatch):
    from sportsassets.agents import eddie as E
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "awqe")
        # Eddie's producer reads every decision of his lookback: run at a time
        # no other proof writes at (a shared database keeps their rows)
        t = F.ISOLATED
        d1 = await F.decision(conn, a, at=t - 120)
        d2 = await F.decision(conn, a, at=t - 110)
        # the decisions are owed estimates: two EXECUTION_ESTIMATE items
        got = await AW.sync_for(conn, "eddie_runner", now=t)
        assert got["kinds"][AW.K_ESTIMATE]["enqueued"] == 2, got
        rq = {r["group_id"]: r for r in await F.requests(
            conn, AW.K_ESTIMATE)}
        assert set(rq) == {d1, d2}
        r1 = rq[d1]
        assert r1["agent_id"] == "EDDIE" and r1["collaborator"] == "DEREK"
        assert r1["evidence_needed"] == ["EXECUTION_ESTIMATE",
                                         "DECISION_TIME_BOOK"]
        assert r1["due_at"].timestamp() == (t - 120) + 600   # from decided
        # the runner refuses d2 and estimates d1
        real = E.record_estimate

        async def record(conn_, est):
            if est["decision_id"] == d2:
                return {"ok": False, "refusal": "HARD_RULE_VIOLATION_REFUSED"}
            return await real(conn_, est)
        monkeypatch.setattr(E, "record_estimate", record)
        s = await ER.pass_once(conn, now=t + 300)
        assert s["refused"] == {d2: "HARD_RULE_VIOLATION_REFUSED"}, s
        rq = {r["group_id"]: r for r in await F.requests(
            conn, AW.K_ESTIMATE)}
        assert rq[d1]["terminal"]["state"] == "COMPLETED"
        assert rq[d1]["terminal"]["evidence_table"] == \
            "eddie_execution_estimates"
        assert rq[d1]["terminal"]["evidence_id"] == E.estimate_id_for(d1)
        assert rq[d2]["is_open"]
        att = await F.attempts(conn, rq[d2]["request_id"])
        assert [x["outcome"] for x in att] == ["BLOCKED"]
        assert att[0]["blocker"] == "HARD_RULE_VIOLATION_REFUSED"
        assert att[0]["nxt"] == (t + 300) + ER.INTERVAL_S
    finally:
        await F.done(conn, tx)


@pg
async def test_karens_deferred_candidates_become_investigations(monkeypatch):
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        refs = [await _decision_ref(conn) for _ in range(3)]
        det = "DECISION_WITHOUT_EVIDENCE"
        dets = [(det, KR.detect_decision_without_evidence)]
        monkeypatch.setattr(KR, "MAX_NEW_PER_PASS", 1)
        s = await KR.pass_once(conn, now=NOW, detectors=dets)
        assert len(s["opened"]) == 1, s
        inv = await F.requests(conn, AW.K_INVESTIGATION, "KAREN")
        assert len(inv) == 2, inv
        assert {r["blocker"] for r in inv} == {"PASS_BUDGET_EXHAUSTED"}
        assert {r["collaborator"] for r in inv} == {"DEREK"}
        assert {r["source_table"] for r in inv} == {"agent_decisions"}
        assert {r["source_id"] for r in inv} <= set(refs)
        # the next pass opens them: each item COMPLETED by its challenge
        monkeypatch.setattr(KR, "MAX_NEW_PER_PASS", 12)
        s = await KR.pass_once(conn, now=NOW + 300, detectors=dets)
        inv = await F.requests(conn, AW.K_INVESTIGATION, "KAREN")
        done = [r for r in inv if r["terminal"]]
        assert len(done) == 2 and all(
            r["terminal"]["state"] == "COMPLETED"
            and r["terminal"]["evidence_table"] == "karen_challenges"
            for r in done), inv
        # a capped detector defers what it finds instead of going silent
        monkeypatch.setattr(KR, "MAX_OPEN_PER_DETECTOR", 0)
        await _decision_ref(conn)
        s = await KR.pass_once(conn, now=NOW + 600, detectors=dets)
        assert s["refused"][det] == "OPEN_CHALLENGE_CAP_REACHED"
        open_inv = [r for r in await F.requests(conn, AW.K_INVESTIGATION)
                    if r["is_open"]]
        assert [r["blocker"] for r in open_inv] == [
            "OPEN_CHALLENGE_CAP_REACHED"]
    finally:
        await F.done(conn, tx)


@pg
async def test_challenge_answers_and_evaluations_through_the_peer_responder():
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        ref = await _decision_ref(conn)
        target = {"kind": "agent_decisions", "id": ref}
        # a challenge the rule CAN be re-applied to: answered and evaluated
        # in one pass (its item, enqueued first, completes)
        ok = await K.open_challenge(
            conn, target_agent="DEREK", target_kind="agent_decisions",
            target_id=ref, detector="DECISION_WITHOUT_EVIDENCE",
            claim="no evidence", severity="MEDIUM", evidence_refs=[target],
            record_at=NOW - 7200, at=NOW - 3600)
        assert ok["ok"], ok
        # a MANUAL challenge (no rule to re-apply): a person must answer
        man = await K.open_challenge(
            conn, target_agent="DEREK", target_kind="agent_decisions",
            target_id=ref, detector="MANUAL_REVIEW", claim="look again",
            severity="LOW", evidence_refs=[target], record_at=NOW - 7200,
            at=NOW - 3600)
        assert man["ok"], man
        got = await AW.sync_for(conn, "peer_responder", now=NOW)
        assert got["kinds"][AW.K_RESPONSE]["enqueued"] == 2, got
        resp = {r["group_id"]: r for r in await F.requests(
            conn, AW.K_RESPONSE)}
        assert resp[ok["challenge_id"]]["collaborator"] == "KAREN"
        assert resp[ok["challenge_id"]]["agent_id"] == "DEREK"
        # SLA from challenged_at: an hour after NOW - 3600 -> due NOW, and
        # the item is overdue a second later
        due = resp[ok["challenge_id"]]["due_at"].timestamp()
        assert due == NOW
        st = AWS.derive("DEREK", {"queue": [
            AWS.queue_item(r, NOW + 1) for r in await conn.fetch(
                AWS.OPEN_ITEMS_SQL, "DEREK", None, 100)]}, now=NOW + 1)
        assert st["state"] == AWS.HANDOFF and st["queue"]["overdue"] == 2
        s = await PRS.pass_once(conn, now=NOW + 60)
        assert ok["challenge_id"] in s["responses"]["DEREK"]["conceded"]
        assert man["challenge_id"] in s["responses"]["DEREK"]["skipped"]
        resp = {r["group_id"]: r for r in await F.requests(
            conn, AW.K_RESPONSE)}
        assert resp[ok["challenge_id"]]["terminal"]["state"] == "COMPLETED"
        assert resp[ok["challenge_id"]]["terminal"]["evidence_id"] == \
            ok["challenge_id"]
        att = await F.attempts(conn, resp[man["challenge_id"]]["request_id"])
        assert att[-1]["blocker"] == "RULE_NOT_REAPPLICABLE_A_PERSON_ANSWERS"
        # a person answers the manual one (DISPUTE): Audrey's evaluation is
        # owed, depending on the answered response item
        got = await K.respond(conn, man["challenge_id"], agent="DEREK",
                              stance="DISPUTE", response="it is fine",
                              at=NOW + 120, evidence_refs=[target])
        assert got["ok"], got
        await AW.sync_for(conn, "peer_responder", now=NOW + 180)
        resp = {r["group_id"]: r for r in await F.requests(
            conn, AW.K_RESPONSE)}
        assert resp[man["challenge_id"]]["terminal"]["state"] == "COMPLETED"
        ev = await F.requests(conn, AW.K_EVALUATION)
        assert len(ev) == 1 and ev[0]["agent_id"] == "AUDREY"
        assert ev[0]["depends_on"] == resp[man["challenge_id"]]["request_id"]
        assert ev[0]["collaborator"] == "DEREK"
        got = await K.resolve(conn, man["challenge_id"], resolver="AUDREY",
                              outcome="REJECTED", reason="it was fine",
                              at=NOW + 240, evidence_refs=[target])
        assert got["ok"], got
        await AW.sync_for(conn, "peer_responder", now=NOW + 300)
        ev = await F.requests(conn, AW.K_EVALUATION)
        assert ev[0]["terminal"]["state"] == "COMPLETED"
        assert ev[0]["terminal"]["detail"]["outcome"] == "REJECTED"
    finally:
        await F.done(conn, tx)


@pg
async def test_dereks_stale_candidates_are_owed_work_through_his_decisions():
    """Derek's REAL decision path (paper_derek.decide_one) on synthetic
    Pinnacle valuations: refused on a stale probability -> an item; the
    candidate re-decided and refused stale again -> an attempt; decided on
    fresh evidence -> COMPLETED by that decision. The items are PASSIVE: no
    acquisition path reads them, so Derek is HANDOFF_PENDING, never WAITING
    (R30B review: they had been counted as an enqueued reacquisition)."""
    from tests import paper_live_fixture as PL
    conn, tx = await F.tx()
    try:
        t0 = F.ISOLATED
        a = await PL.new_account(conn, "awqd", now=t0 - 600)
        slug = "%sawqd-%s" % (PL.SYN, uuid.uuid4().hex[:8])
        tr = PL.Transport(t0)
        tr.set(slug, offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        # a valuation a minute old at the decision: refused STALE
        d0 = await F.derek_decides(conn, a, tr, slug=slug, valuation_at=t0,
                                   pin_age_s=5.0, decide_at=t0 + 60)
        assert "PROBABILITY_EVIDENCE_STALE" in d0["refusals"], d0
        ctx = {"now": t0 + 90, "agent_work_any_account": True}
        AW._LAST_STEP.clear()
        got = await AW.step(conn, ctx)
        assert got["ran"] and got["kinds"][AW.K_CANDIDATE]["enqueued"] == 1
        it = [r for r in await F.requests(conn, AW.K_CANDIDATE)
              if r["us_market_slug"] == slug][0]
        assert it["agent_id"] == "DEREK"
        assert it["source_id"] == d0["decision_id"]
        assert it["blocker"] == "PROBABILITY_EVIDENCE_STALE"
        assert it["evidence_needed"][0] == "FRESH_PINNACLE_PROBABILITY"
        assert it["detail"]["strategy"] == d0["strategy"]
        assert it["due_at"].timestamp() == \
            d0["decided_at"].timestamp() + 900          # SLA from decided
        # the read an acquisition path WOULD consume has no caller: PASSIVE
        assert slug in await AW.hot_candidate_slugs(conn)
        assert AW.K_CANDIDATE in AW.PASSIVE_KINDS

        async def state(at):
            mine = [AWS.queue_item(r, at) for r in await conn.fetch(
                AWS.OPEN_ITEMS_SQL, "DEREK", [AW.K_CANDIDATE], 100)]
            return AWS.derive("DEREK", {"queue": mine, "market": {}}, now=at)
        st = await state(t0 + 91)
        assert st["state"] == AWS.HANDOFF and st["queue"]["reacquisition"] == 0
        assert st["basis"][-1]["kind"] == AWS.K_QUEUE_UNTOUCHED
        # the paper-pass step is throttled
        assert (await AW.step(conn, dict(ctx, now=t0 + 95)))["why"] == \
            "NOT_DUE"
        # re-decided on a still stale valuation: an ATTEMPT (WAITING)
        d1 = await F.derek_decides(conn, a, tr, slug=slug,
                                   valuation_at=t0 + 100, pin_age_s=5.0,
                                   decide_at=t0 + 160)
        assert "PROBABILITY_EVIDENCE_STALE" in d1["refusals"]
        await AW.sync_for(conn, "paper_pass", now=t0 + 200)
        att = await F.attempts(conn, it["request_id"])
        assert [x["outcome"] for x in att] == ["WAITING_FOR_FRESH_EVIDENCE"]
        assert F.j(att[0]["detail"])["decision_id"] == d1["decision_id"]
        await AW.sync_for(conn, "paper_pass", now=t0 + 260)
        assert len(await F.attempts(conn, it["request_id"])) == 1  # no news
        # waiting on evidence nobody is asked to acquire: a hand-off
        st = await state(t0 + 261)
        assert st["state"] == AWS.HANDOFF
        assert st["basis"][-1]["kind"] == AWS.K_NOT_ENQUEUED
        # decided on FRESH evidence (refused for something else): COMPLETED
        d2 = await F.derek_decides(conn, a, tr, slug=slug,
                                   valuation_at=t0 + 300, pin_age_s=2.0,
                                   decide_at=t0 + 301)
        assert not set(d2["refusals"] or []) & set(AW.STALE_REFUSALS), d2
        await AW.sync_for(conn, "paper_pass", now=t0 + 360)
        it = [r for r in await F.requests(conn, AW.K_CANDIDATE)
              if r["us_market_slug"] == slug][0]
        assert it["terminal"]["state"] == "COMPLETED"
        assert it["terminal"]["evidence_id"] == d2["decision_id"]
        assert slug not in await AW.hot_candidate_slugs(conn)
        # a non-main account's pass does nothing
        AW._LAST_STEP.clear()
        got = await AW.step(conn, {"now": t0 + 999, "account_id": "x"})
        assert got == {"ran": False, "why": "NOT_THE_MAIN_PAPER_ACCOUNT"}
    finally:
        await F.done(conn, tx)


@pg
async def test_karens_investigations_stay_bounded_as_her_window_moves(
        monkeypatch):
    """(R30B review) A capped detector deferred its oldest records every
    pass, the window moved, and an immutable stale HOLD kept its rule true:
    items piled up until their 7-day expiry. Now at most
    MAX_OPEN_INVESTIGATIONS_PER_DETECTOR are open, and an item whose record
    left the detector's lookback is FAILED (RECORD_LEFT_THE_DETECTOR_
    WINDOW). Karen's REAL runner writes every item; the reviews are the
    hand-built pre-R30 stale HOLD shape (agent_ops_fixture)."""
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        T = F.ISOLATED + 30 * 86400
        a = await F.account(conn, "awqk", now=T - 8 * 86400)
        p = await F.position(conn, a, at=T - 7.5 * 86400)
        t = T - 6.9 * 86400
        while t < T + 2.5 * 86400:              # one every 30 minutes
            await F.stale_hold_review(conn, a, group_id=p["group_id"], at=t)
            t += 1800
        dets = [("HOLD_ON_STALE_PROBABILITY",
                 KR.detect_hold_on_stale_probability)]
        monkeypatch.setattr(KR, "MAX_OPEN_PER_DETECTOR", 0)   # capped
        monkeypatch.setattr(KR, "MAX_NEW_PER_DETECTOR", 40)
        cap = AW.MAX_OPEN_INVESTIGATIONS_PER_DETECTOR
        assert AW.KINDS[AW.K_INVESTIGATION]["open_cap"] == ("detector", cap)
        s = await KR.pass_once(conn, now=T, detectors=dets)
        assert s["refused"]["HOLD_ON_STALE_PROBABILITY"] == \
            "OPEN_CHALLENGE_CAP_REACHED"
        rep = s["work_queue"]["kinds"][AW.K_INVESTIGATION]
        assert rep["enqueued"] == cap and rep["capped"] == 40 - cap, rep
        # the window moves six hours a pass for two days
        reqs: list = []
        for k in range(1, 9):
            now = T + k * 0.25 * 86400
            await KR.pass_once(conn, now=now, detectors=dets)
            reqs = await F.requests(conn, AW.K_INVESTIGATION, "KAREN")
            live = [r for r in reqs if r["is_open"]]
            assert len(live) <= cap, (k, len(live))
            assert all(float(r["detail"]["arose_at"]) >= now - KR.LOOKBACK_S
                       for r in live), k
        failed = [r["terminal"] for r in reqs
                  if r["terminal"] and r["terminal"]["state"] == "FAILED"]
        assert failed and {f["failure"] for f in failed} == {
            AW.F_LEFT_DETECTOR}
        assert len(reqs) - len(failed) == len([r for r in reqs
                                               if r["is_open"]])
        # the copied constants are Karen's own
        assert AW.KAREN_LOOKBACK_S == KR.LOOKBACK_S
        assert AW.MAX_OPEN_INVESTIGATIONS_PER_DETECTOR == 25
        assert AW.AUDIT_OPEN_AFTER_S == KR.AUDIT_OPEN_AFTER_S
    finally:
        await F.done(conn, tx)


@pg
async def test_scouts_research_questions_through_his_runner():
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        s = await SR.pass_once(conn, now=NOW)
        rq = await F.requests(conn, AW.K_RESEARCH, "SCOUT")
        n = await conn.fetchval(
            "SELECT count(*) FROM scout_features f JOIN "
            " scout_feature_tournaments t USING (feature_id) WHERE "
            " f.state='UNDER_TEST' AND t.verdict IS NULL")
        assert len([r for r in rq if r["is_open"]]) == n, s
        if not n:
            return
        r0 = rq[0]
        assert r0["blocker"] == "AWAITING_PROSPECTIVE_SAMPLES"
        assert r0["evidence_needed"] == ["PROSPECTIVE_SAMPLES",
                                         "SETTLED_OUTCOMES",
                                         "EVALUATOR_VERDICT"]
        att = await F.attempts(conn, r0["request_id"])
        assert att and att[-1]["outcome"] in ("WAITING_FOR_FRESH_EVIDENCE",
                                              "PROGRESSED")
        detail = F.j(att[-1]["detail"])
        assert set(detail) == {"samples", "settled", "min_sample"}
        st = await AWS.read_work_states(conn, now=NOW + 1)
        assert st["states"]["SCOUT"]["state"] in (AWS.WORKING, AWS.WAITING)
        assert st["states"]["SCOUT"]["state"] != AWS.IDLE
    finally:
        await F.done(conn, tx)


@pg
async def test_allies_allocation_reviews_complete_on_her_runs():
    conn, tx = await F.tx()
    try:
        a = await F.account(conn, "awqa")

        async def run(rid, started, status):
            await conn.execute(
                "INSERT INTO intel_runs (run_id, component, started_at, "
                " finished_at, status, version) VALUES ($1,'ALLOCATOR',"
                " to_timestamp($2),to_timestamp($2 + 1),$3,'TEST')",
                rid, started, status)
        await run("intelrun:r30b-1", NOW - 600, "OK")
        d = await F.decision(conn, a, at=NOW - 300)
        await AW.sync_for(conn, "paper_pass", now=NOW)
        it = [r for r in await F.requests(conn, AW.K_ALLOCATION)
              if r["group_id"] == d][0]
        assert it["agent_id"] == "CHIEF_ALLOCATOR"
        assert it["collaborator"] == "DEREK"
        # a failed run after the decision: BLOCKED, naming it
        await run("intelrun:r30b-2", NOW + 10, "FAILED")
        await AW.sync_for(conn, "paper_pass", now=NOW + 60)
        att = await F.attempts(conn, it["request_id"])
        assert att[-1]["blocker"] == "ALLOCATOR_RUN_FAILED"
        # the next OK run completes it (no allocation row: said so)
        await run("intelrun:r30b-3", NOW + 600, "OK")
        await AW.sync_for(conn, "paper_pass", now=NOW + 700)
        it = [r for r in await F.requests(conn, AW.K_ALLOCATION)
              if r["group_id"] == d][0]
        assert it["terminal"]["state"] == "COMPLETED"
        assert it["terminal"]["evidence_table"] == "intel_runs"
        assert it["terminal"]["detail"]["allocated"] is False
    finally:
        await F.done(conn, tx)


@pg
async def test_audreys_reconciliations_complete_when_matched():
    conn, tx = await F.tx()
    try:
        g = "smalllive-g-%s" % uuid.uuid4().hex[:8]
        await conn.execute(
            "INSERT INTO smalllive_reconciliations (group_id, venue, "
            " reconciled_at, status, discrepancies, chain) VALUES ($1,"
            " 'POLYMARKET_US',to_timestamp($2),'DISCREPANCY',"
            " '[{\"field\": \"qty\"}]'::jsonb,'{}'::jsonb)", g, NOW - 7200)
        await AW.sync_for(conn, "paper_pass", now=NOW)
        it = [r for r in await F.requests(conn, AW.K_RECONCILIATION)
              if r["group_id"] == g][0]
        assert it["agent_id"] == "AUDREY" and it["collaborator"] == "XAVIER"
        assert it["due_at"].timestamp() == NOW - 3600       # overdue at once
        await conn.execute(
            "UPDATE smalllive_reconciliations SET status='MATCHED', "
            " reconciled_at=to_timestamp($2) WHERE group_id=$1", g, NOW + 30)
        await AW.sync_for(conn, "paper_pass", now=NOW + 60)
        it = [r for r in await F.requests(conn, AW.K_RECONCILIATION)
              if r["group_id"] == g][0]
        assert it["terminal"]["state"] == "COMPLETED"
    finally:
        await F.done(conn, tx)


@pg
async def test_audreys_open_findings_are_her_followups_through_the_writer():
    """(R30B review) Audrey's audit backlog never reached her queue: an open
    WARNING / CRITICAL finding with no improvement task (the class Karen
    upholds as AUDIT_DISCREPANCY_LEFT_OPEN) is now her
    AUDIT_FINDING_FOLLOWUP, written by the real audit writer
    (paper_audrey.finding) and completed by the real task hook
    (paper_audrey.open_task)."""
    from sportsassets.agents import paper_audrey as PA
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        t0 = F.ISOLATED + 90 * 86400
        a = await F.account(conn, "awqf", now=t0 - 3 * 86400)
        found = t0 - 2 * 86400
        ctx = {"session_id": a["session_id"], "account_id": a["account_id"],
               "now": found}
        f = await PA.finding(conn, ctx, kind="LEDGER_INCONSISTENT",
                             subject=a["account_id"], severity="CRITICAL",
                             detail={"last_sequence": 7}, scope="7")
        info = await PA.finding(conn, ctx, kind="R30B_INFO_ONLY",
                                subject="x", severity="INFO", detail={})
        assert f["new"] and info["new"]
        got = await AW.sync(conn, [AW.K_AUDIT_FINDING], now=t0)
        assert got["kinds"][AW.K_AUDIT_FINDING]["enqueued"] >= 1, got
        rq = [r for r in await F.requests(conn, AW.K_AUDIT_FINDING)
              if r["group_id"] in (f["finding_id"], info["finding_id"])]
        assert [r["group_id"] for r in rq] == [f["finding_id"]]   # not INFO
        r0 = rq[0]
        assert r0["agent_id"] == "AUDREY" and r0["position_kind"] == "RECORD"
        assert r0["blocker"] == "NO_IMPROVEMENT_TASK_LINKED"
        assert r0["evidence_needed"] == ["IMPROVEMENT_TASK_LINKED"]
        assert r0["source_table"] == "paper_audrey_findings"
        assert r0["detail"]["finding_kind"] == "LEDGER_INCONSISTENT"
        # SLA: Karen's own open-after bound from when it was found
        assert r0["due_at"].timestamp() == found + KR.AUDIT_OPEN_AFTER_S
        mine = [AWS.queue_item(r, t0 + 1) for r in await conn.fetch(
            AWS.OPEN_ITEMS_SQL, "AUDREY", [AW.K_AUDIT_FINDING], 100)]
        st = AWS.derive("AUDREY", {"queue": mine}, now=t0 + 1)
        assert st["state"] == AWS.HANDOFF and st["queue"]["overdue"] >= 1
        # the improvement task is opened by the real hook: COMPLETED by it
        task = await PA.open_task(conn, dict(ctx, now=t0 + 10), f,
                                  detail={"why": "R30B proof"})
        assert task["ok"], task
        await AW.sync(conn, [AW.K_AUDIT_FINDING], now=t0 + 60)
        r0 = [r for r in await F.requests(conn, AW.K_AUDIT_FINDING)
              if r["group_id"] == f["finding_id"]][0]
        assert r0["terminal"]["state"] == "COMPLETED"
        assert r0["terminal"]["evidence_table"] == "agent_tasks"
        assert r0["terminal"]["evidence_id"] == task["task_id"]
    finally:
        await F.done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# §3 OVERDUE, EXPIRY AND THE ATTEMPT SCHEDULE
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_attempts_follow_the_schedule_they_record():
    """(R30B review) Every 60 s paper-pass sync had written an attempt
    whatever next_attempt_at said. Now: Audrey's triage of a root-cause
    cluster (retry 3600 s) synced every minute for eight hours records an
    attempt only when its scheduled next attempt is due, the interval
    doubling while the answer is unchanged (3600, 7200, 14400, then the
    21600 s ceiling); each recorded next attempt is exactly when the next
    attempt happened; a changed answer (a fix linked) resets the interval."""
    from sportsassets.agents import improvement_clusters as IC
    conn, tx = await F.tx()
    try:
        t0 = F.ISOLATED + 60 * 86400
        a = await F.account(conn, "awqt", now=t0 - 86400)
        kind = "R30B_SCHEDULE_T%s" % uuid.uuid4().hex[:6]
        for i in range(2):
            await F.audrey_finding(conn, a, kind=kind, at=t0 - 7200 + i)
        cid = IC.cluster_id_for(IC.cluster_key("AUDREY", kind, None))
        assert cid in (await IC.refresh(conn, now=t0))["opened"]
        for k in range(8 * 60 + 1):
            await AW.sync(conn, [AW.K_ROOT_CAUSE], now=t0 + 60.0 * k)
        it = [r for r in await F.requests(conn, AW.K_ROOT_CAUSE)
              if r["group_id"] == cid][0]
        att = await F.attempts(conn, it["request_id"])
        assert [x["at"] - t0 for x in att] == [60.0, 3660.0, 10860.0,
                                               25260.0], att
        assert {x["blocker"] for x in att} == {"AWAITING_ENGINEERING_FIX"}
        for prev, nxt in zip(att, att[1:]):
            assert prev["nxt"] == nxt["at"]              # truthful
        assert att[-1]["nxt"] - att[-1]["at"] == AW.MAX_RETRY_BACKOFF_S
        # a changed answer (a person links a fix: WAITING on post-fix
        # evidence) is recorded at once, the interval back to its base
        ok = await IC.link_fix(conn, cid, actor="Test Engineer",
                               commit_sha="b" * 40, effective_at=t0 + 30000,
                               at=t0 + 30000)
        assert ok["ok"], ok
        await AW.sync(conn, [AW.K_ROOT_CAUSE], now=t0 + 30060)
        att = await F.attempts(conn, it["request_id"])
        assert att[-1]["outcome"] == "WAITING_FOR_FRESH_EVIDENCE"
        assert att[-1]["at"] == t0 + 30060
        assert att[-1]["nxt"] - att[-1]["at"] == \
            AW.KINDS[AW.K_ROOT_CAUSE]["retry_s"]
    finally:
        await F.done(conn, tx)


def test_the_attempt_plan_and_the_database_bound():
    base = {"last_attempt": "ATTEMPTED", "outcome": "BLOCKED",
            "last_attempt_blocker": "X", "last_attempt_at": NOW,
            "next_attempt_at": NOW + 600}
    plan = AW.attempt_plan
    # never attempted: due at once
    assert plan({}, at=NOW, outcome="BLOCKED", blocker="X",
                base_s=600) == {"next_in_s": 600}
    # unchanged: not before its scheduled time; then the interval doubles
    assert plan(base, at=NOW + 599, outcome="BLOCKED", blocker="X",
                base_s=600) is None
    assert plan(base, at=NOW + 600, outcome="BLOCKED", blocker="X",
                base_s=600) == {"next_in_s": 1200}
    far = dict(base, next_attempt_at=NOW + 20000)
    assert plan(far, at=NOW + 20000, outcome="BLOCKED", blocker="X",
                base_s=600) == {"next_in_s": AW.MAX_RETRY_BACKOFF_S}
    # changed (outcome or blocker): once the base interval has passed,
    # back to the base
    assert plan(far, at=NOW + 300, outcome="PROGRESSED", blocker=None,
                base_s=600) is None
    assert plan(far, at=NOW + 600, outcome="BLOCKED", blocker="Y",
                base_s=600) == {"next_in_s": 600}
    # a BLOCKED attempt with no blocker is recorded as UNSPECIFIED_BLOCKER:
    # the same again is unchanged
    unspec = dict(base, last_attempt_blocker="UNSPECIFIED_BLOCKER")
    assert plan(unspec, at=NOW + 10, outcome="BLOCKED", blocker=None,
                base_s=600) is None
    # EVERY KIND FITS THE DATABASE'S PER-REQUEST BOUND: at most one attempt
    # per base interval over the item's whole horizon
    bound = int(re.search(r"state = 'ATTEMPTED'\)\s*>= (\d+)",
                          F.UP).group(1))
    for k in AW.KINDS:
        assert AW.max_attempts(k) < bound, (k, AW.max_attempts(k), bound)



@pg
async def test_an_item_past_its_horizon_fails_and_is_enqueued_again():
    conn, tx = await F.tx()
    try:
        g = "smalllive-x-%s" % uuid.uuid4().hex[:8]
        await conn.execute(
            "INSERT INTO smalllive_reconciliations (group_id, venue, "
            " reconciled_at, status, chain) VALUES ($1,'POLYMARKET_US',"
            " to_timestamp($2),'DISCREPANCY','{}'::jsonb)", g, NOW)
        await AW.sync_for(conn, "paper_pass", now=NOW + 10)
        later = NOW + 10 + AW.KINDS[AW.K_RECONCILIATION]["ttl_s"] + 1
        got = await AW.sync_for(conn, "paper_pass", now=later)
        assert got["kinds"][AW.K_RECONCILIATION]["expired"] >= 1
        items = [r for r in await F.requests(conn, AW.K_RECONCILIATION)
                 if r["group_id"] == g]
        assert [r["terminal"]["failure"] for r in items
                if r["terminal"]] == [AW.F_EXPIRED]
        again = [r for r in items if r["is_open"]]
        assert len(again) == 1                 # still pending: enqueued again
        # its SLA is still counted from when the discrepancy arose
        assert again[0]["due_at"].timestamp() == NOW + 3600
    finally:
        await F.done(conn, tx)


def test_terms_count_the_sla_from_when_the_work_arose():
    t = AW.terms(AW.K_RESPONSE, at=NOW, arose_at=NOW - 7200)
    assert t["due_at"] == NOW - 3600 and t["expires_at"] == NOW + 7 * 86400
    t = AW.terms(AW.K_ESTIMATE, at=NOW, arose_at=None)
    assert t["due_at"] == NOW + 600
    # never beyond the horizon, never further back than the database allows
    t = AW.terms(AW.K_RESEARCH, at=NOW, arose_at=NOW)
    assert t["due_at"] == NOW + 14 * 86400 <= t["expires_at"]
    t = AW.terms(AW.K_RESPONSE, at=NOW, arose_at=NOW - 90 * 86400)
    assert t["due_at"] > NOW - 30 * 86400
    long = AW.subject_key("x" * 300, "y")
    assert len(long) <= AW.MAX_SUBJECT and long == AW.subject_key(
        "x" * 300, "y")


@pg
async def test_a_long_queue_never_hides_an_agents_work(monkeypatch):
    """(R30B review) One global LIMIT over every open item had let Karen's
    backlog push Scout's item out of the read and derive Scout IDLE while he
    owned it, and a failed read was taken as an empty queue. Now the counts
    are unbounded and the items bounded PER AGENT: Scout's item is read
    whatever Karen's backlog; Karen, whose items exceed the bound, is never
    IDLE and her blocker card is UNAVAILABLE (QUEUE_READ_LIMITED); a queue
    read that fails yields no IDLE and no made-up NO_BLOCKER."""
    from sportsassets.agents import agent_scorecards as S
    conn, tx = await F.tx()
    try:
        t0 = F.ISOLATED + 120 * 86400
        monkeypatch.setattr(AWS, "MAX_QUEUE_ITEMS", 3)
        tag = uuid.uuid4().hex[:6]
        for i in range(4):
            got = await AW.enqueue_item(
                conn, kind=AW.K_INVESTIGATION, agent="KAREN",
                subject="rec-%s-%d" % (tag, i), at=t0,
                arose_at=t0 - 5 * 86400, source_table="paper_xavier_reviews",
                source_id="r%d" % i, batch_id="b",
                blocker="OPEN_CHALLENGE_CAP_REACHED",
                detail={"detector": "D", "target_kind": "paper_xavier_reviews",
                        "target_id": "r%d" % i})
            assert got["enqueued"], got
        got = await AW.enqueue_item(
            conn, kind=AW.K_RESEARCH, agent="SCOUT", subject="feat-" + tag,
            at=t0, arose_at=t0, source_table="scout_feature_tournaments",
            source_id="t", batch_id="b", blocker="AWAITING_PROSPECTIVE_SAMPLES")
        assert got["enqueued"], got
        st = await AWS.read_work_states(conn, now=t0 + 1)
        sc, ka = st["states"]["SCOUT"], st["states"]["KAREN"]
        assert sc["state"] != AWS.IDLE and sc["queue"]["open"] >= 1, sc
        assert ka["state"] != AWS.IDLE
        assert ka["queue"]["open"] >= 4 and ka["queue"]["read"] == 3
        assert ka["queue"]["limited"] is True
        assert st["sections"]["work.queue"]["limited"]["KAREN"] >= 4

        async def blocker_cards():
            got = await S.scorecards(conn, now=t0 + 1,
                                     agents=("KAREN", "SCOUT"))
            return {c["agent"]: [m for m in c["metrics"]
                                 if m["metric"] == "unresolved_blocker_age"][0]
                    for c in got["agents"]}
        b = await blocker_cards()
        assert b["KAREN"]["status"] == S.UNAVAILABLE
        assert b["KAREN"]["reason"].startswith("QUEUE_READ_LIMITED"), b
        assert b["SCOUT"]["status"] == S.MEASURED            # its blocker
        # the queue read FAILS: never IDLE, never NO_BLOCKER
        monkeypatch.setattr(AWS, "OPEN_ITEMS_BY_AGENT_SQL",
                            "SELECT 1 / 0 AS x WHERE $1::int IS NOT NULL")
        st = await AWS.read_work_states(conn, now=t0 + 1)
        assert st["sections"]["work.queue"]["status"] == "UNAVAILABLE"
        for agent in ("KAREN", "SCOUT"):
            s_ = st["states"][agent]
            assert s_["state"] != AWS.IDLE, (agent, s_)
            if s_["state"] is None:
                assert s_["basis"][-1]["why"].startswith(
                    AWS.R_QUEUE_UNREADABLE)
        b = await blocker_cards()
        for agent in ("KAREN", "SCOUT"):
            assert b[agent]["status"] == S.UNAVAILABLE, b[agent]
            assert b[agent]["reason"].startswith("READ_FAILED"), b[agent]
    finally:
        await F.done(conn, tx)


def test_every_kind_is_owned_and_produced():
    assert set(AW.KINDS) == set(AW.PRODUCERS)
    produced = {k for ks in AW.RUNNER_KINDS.values() for k in ks}
    assert produced == set(AW.KINDS)
    owners = {a for s in AW.KINDS.values() for a in s["agents"]}
    # every agent but Xavier owns a 301 kind; Xavier owns 226's and his
    # challenge answers / evaluations. (R30 tails integration) ADRIANA, the
    # eighth SHADOW agent (migration 265), joined the work-state registry
    # after R30B and owns no durable-queue kind: her census work is her own
    # run, so she is never derived WAITING (no enqueued reacquisition)
    assert owners == set(AW.AGENTS) - {"ADRIANA"}
    assert "ADRIANA" in AW.AGENTS
    for k, s in AW.KINDS.items():
        assert s["sla_s"] <= s["ttl_s"] <= 30 * 86400, k
        assert s["evidence"], k
    # the stale refusal codes are the decision policy's own
    from sportsassets.agents import derek_policy as DP
    from sportsassets.agents import eddie as E
    assert set(AW.STALE_REFUSALS) == {DP.R_STALE, DP.R_FRESHNESS_UNKNOWN}
    assert AW.EDDIE_ESTIMATOR_VERSION == E.VERSION
    assert AW.EDDIE_LOOKBACK_S == ER.LOOKBACK_S
    from sportsassets import bettor_paper_ledger as L
    assert AW.MAIN_PAPER_ACCOUNT == L.ACCOUNT_ID
    assert dict(AW.EVALUATOR_FOR) == dict(K.EVALUATOR_FOR)


# ═════════════════════════════════════════════════════════════════════
# §4 THE WORK STATE FROM THE QUEUE, READ FROM THE DATABASE
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_work_states_read_the_queue():
    conn, tx = await F.tx()
    try:
        a = await F.account(conn, "awqs")
        await F.decision(conn, a, at=NOW - 30)
        await AW.sync_for(conn, "eddie_runner", now=NOW)
        st = await AWS.read_work_states(conn, now=NOW + 1)
        assert st["sections"]["work.queue"]["status"] == "OK"
        ed = st["states"]["EDDIE"]
        assert ed["state"] in (AWS.HANDOFF, AWS.BLOCKED), ed
        assert ed["queue"]["by_kind"].get(AW.K_ESTIMATE, 0) >= 1
        for agent, s in st["states"].items():
            f = (await AWS.read_facts(conn, now=NOW + 1))["facts"][agent]
            assert AWS.invariant_holds(agent, f, s["state"]), (agent, s)
        view = await AW.queue_view(conn, now=NOW + 1)
        assert view["EDDIE"]["summary"]["open"] >= 1
        assert all(i["owner"] == "EDDIE" for i in view["EDDIE"]["items"])
        assert all({"blocker", "due_at", "dependency", "last_attempt_at",
                    "next_attempt_at", "evidence_needed", "collaborator",
                    "overdue"} <= set(i) for i in view["EDDIE"]["items"])
    finally:
        await F.done(conn, tx)
