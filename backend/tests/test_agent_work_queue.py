"""MIGRATION 226 AND XAVIER'S FRESH-EVIDENCE WORK QUEUE, on Postgres
(owner R30: "WAITING_FOR_FRESH_EVIDENCE must actively enqueue ...").

Every scenario runs inside ONE transaction that is rolled back:

  §1 MIGRATION. 226 is idempotent (run twice); the requests and their
     lifecycle are append-only (UPDATE / DELETE refused); the CHECKs refuse a
     bad kind, reason, expiry, a book / probability request without a slug, a
     game-state request without a fixture identity, a COMPLETED without its
     evidence, a FAILED without its reason, an event before ENQUEUED and any
     event after a terminal one; the open slot is one per (agent, position,
     kind), cannot be updated, and is removed only by the terminal event;
     the rollback refuses while any request exists and drops cleanly when
     none does.
  §2 ONE CURRENT REVIEW PER POSITION. The pointer advances in the review
     insert's own transaction (trigger), newest wins, never moves back, is
     never deleted, references the review row; ACTUAL positions point at
     their assessment; the history stays append-only.
  §3 THE QUEUE THROUGH THE REAL REVIEW PATH (paper_xavier.review_group ->
     work_queue.after_review): a stale review enqueues PROBABILITY,
     VENUE_BOOK and MANAGEMENT_REASSESSMENT (GAME_STATE only with a fixture
     identity) with ENQUEUED events; a second stale review is deduplicated;
     a fresh valuation completes PROBABILITY with its valuation id, the
     priority book read (paper_runtime.step_books) completes VENUE_BOOK with
     its observation id, the re-review is dispatched once evidence landed
     and is COMPLETED by the next review's id -- which, on fresh evidence,
     decides (EXIT here); a failed read FAILS its request; an unanswered
     request expires FAILED and backs off; a closed position's requests
     fail.
"""
from __future__ import annotations

import json
import pathlib
import time
import uuid

import asyncpg
import pytest

from sportsassets import xavier_freshness as XF
from sportsassets.agents import paper_runtime as PR
from sportsassets.agents import paper_xavier as PX
from sportsassets.agents import work_queue as WQ

from tests import paper_harness as H
from tests import test_xavier_review_probability_freshness as XR

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP = (MIG / "226_agent_work_queue.sql").read_text()
DOWN = (MIG / "rollback" / "226_agent_work_queue.down.sql").read_text()
AT = XR.AT


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


async def _tx():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    await conn.execute(UP)
    return conn, tx


REQ = ("INSERT INTO agent_work_requests (request_id, agent_id, kind, "
       " position_kind, group_id, us_market_slug, fixture_identity, reason, "
       " batch_id, enqueued_at, expires_at) VALUES ($1,'XAVIER',$2,'PAPER',"
       " $3,$4,$5,$6,'b',to_timestamp($7),to_timestamp($8))")
EV = ("INSERT INTO agent_work_request_events (request_id, state, at, "
      " evidence_table, evidence_id, failure) VALUES ($1,$2,now(),$3,$4,$5)")
OPEN = ("INSERT INTO agent_work_open (agent_id, position_kind, group_id, "
        " kind, request_id, opened_at) VALUES ('XAVIER','PAPER',$1,$2,$3,"
        " now())")


# ═════════════════════════════════════════════════════════════════════
# §1 THE MIGRATION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_226_is_idempotent_append_only_checked_and_guarded():
    conn, tx = await _tx()
    try:
        await conn.execute(UP)                                   # twice
        g = "g-mig-%s" % uuid.uuid4().hex[:8]
        W = "WAITING_FOR_FRESH_EVIDENCE"
        await conn.execute(REQ, "r1", "PROBABILITY", g, "slug", None, W,
                           AT, AT + 300)
        # CHECKs
        E = asyncpg.CheckViolationError
        await _expect(conn, E, REQ, "r2", "SOMETHING", g, "s", None, W,
                      AT, AT + 300)
        await _expect(conn, E, REQ, "r2", "PROBABILITY", g, "s", None,
                      "HOLD", AT, AT + 300)
        await _expect(conn, E, REQ, "r2", "PROBABILITY", g, "s", None, W,
                      AT, AT + 7200)                     # > one hour
        await _expect(conn, E, REQ, "r2", "PROBABILITY", g, "s", None, W,
                      AT, AT)                            # expires at once
        await _expect(conn, E, REQ, "r2", "VENUE_BOOK", g, None, None, W,
                      AT, AT + 300)                      # no slug
        await _expect(conn, E, REQ, "r2", "GAME_STATE", g, "s", None, W,
                      AT, AT + 300)                      # no fixture id
        await conn.execute(REQ, "r3", "GAME_STATE", g, "s",
                           "fixture_metadata:c1", W, AT, AT + 300)
        # append-only requests
        X = asyncpg.IntegrityConstraintViolationError
        await _expect(conn, X, "UPDATE agent_work_requests SET reason="
                      "'MANAGEMENT_UNAVAILABLE_STALE_INPUT' WHERE "
                      "request_id='r1'")
        await _expect(conn, X, "DELETE FROM agent_work_requests WHERE "
                      "request_id='r1'")
        # lifecycle order
        await _expect(conn, X, EV, "r1", "DISPATCHED", None, None, None)
        await conn.execute(EV, "r1", "ENQUEUED", None, None, None)
        await _expect(conn, asyncpg.UniqueViolationError, EV, "r1",
                      "ENQUEUED", None, None, None)
        await _expect(conn, E, EV, "r1", "COMPLETED", None, None, None)
        await _expect(conn, E, EV, "r1", "COMPLETED", "t", None, None)
        await _expect(conn, E, EV, "r1", "FAILED", None, None, None)
        await _expect(conn, E, EV, "r1", "DISPATCHED", "t", "1", None)
        await conn.execute(OPEN, g, "PROBABILITY", "r1")
        # one open per (agent, position, kind)
        await conn.execute(EV, "r3", "ENQUEUED", None, None, None)
        await _expect(conn, asyncpg.UniqueViolationError, OPEN, g,
                      "PROBABILITY", "r3")
        await conn.execute(OPEN, g, "GAME_STATE", "r3")
        await _expect(conn, X, "UPDATE agent_work_open SET opened_at=now()"
                      " WHERE request_id='r1'")
        await _expect(conn, X, "DELETE FROM agent_work_open "
                      "WHERE request_id='r1'")           # not terminal
        await conn.execute(EV, "r1", "DISPATCHED", None, None, None)
        await conn.execute(EV, "r1", "COMPLETED", "external_valuations",
                           "77", None)
        # the terminal event closed the slot, in the same statement
        assert await conn.fetchval("SELECT count(*) FROM agent_work_open "
                                   " WHERE request_id='r1'") == 0
        await _expect(conn, X, EV, "r1", "FAILED", None, None, "late")
        await _expect(conn, X, "UPDATE agent_work_request_events SET "
                      "failure='x' WHERE request_id='r1'")
        await _expect(conn, X, "DELETE FROM agent_work_request_events "
                      "WHERE request_id='r1'")
        # rollback refuses while any request exists
        await _expect(conn, asyncpg.RaiseError, DOWN)
        sp = conn.transaction()
        await sp.start()
        try:
            await conn.execute("SET LOCAL session_replication_role = "
                               "replica")
            for t in ("agent_work_open", "agent_work_request_events",
                      "agent_work_requests"):
                await conn.execute("DELETE FROM %s" % t)
            await conn.execute("SET LOCAL session_replication_role = "
                               "origin")
            await conn.execute(DOWN)
            for t in ("agent_work_requests", "agent_work_request_events",
                      "agent_work_open", "xavier_current_review"):
                assert await conn.fetchval("SELECT to_regclass($1)",
                                           t) is None, t
            # the review tables keep their own triggers, lose only ours
            trg = {r["tgname"] for r in await conn.fetch(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = "
                "'paper_xavier_reviews'::regclass AND NOT tgisinternal")}
            assert "paper_xavier_reviews_append_only_trg" in trg
            assert "paper_xavier_reviews_current_trg" not in trg
            await conn.execute(UP)                   # and it comes back
            assert await conn.fetchval(
                "SELECT to_regclass('xavier_current_review')") is not None
        finally:
            await sp.rollback()
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §2 EXACTLY ONE CURRENT REVIEW PER POSITION
# ═════════════════════════════════════════════════════════════════════

REV = ("INSERT INTO paper_xavier_reviews (review_id, session_id, account_id,"
       " group_id, reviewed_at, trigger, recommendation, alternatives, "
       " exposure, selection) VALUES ($1,$2,$3,$4,to_timestamp($5),"
       " 'SCHEDULED_BACKSTOP',$6,'{}'::jsonb,'{}'::jsonb,$7::jsonb)")


@pg
async def test_one_current_review_per_position_is_structural():
    conn, tx = await _tx()
    try:
        a = await H.new_account(conn, "cur", now=AT - 100)
        g = "paper_g_cur_%s" % uuid.uuid4().hex[:8]
        args = (a["session_id"], a["account_id"], g)

        async def rev(rid, at, rec="WAITING_FOR_FRESH_EVIDENCE"):
            await conn.execute(REV, rid, *args, at, rec, json.dumps(
                {"recommendation_state": XF.S_WAITING}))

        async def cur():
            return await conn.fetch(
                "SELECT * FROM xavier_current_review WHERE "
                " position_kind='PAPER' AND group_id=$1", g)
        await rev("paperrev:b", AT)
        (c,) = await cur()
        assert c["paper_review_id"] == "paperrev:b" and c["advances"] == 1
        assert c["recommendation_state"] == XF.S_WAITING
        await rev("paperrev:c", AT + 10, "HOLD")
        (c,) = await cur()
        assert c["paper_review_id"] == "paperrev:c" and c["advances"] == 2
        assert c["recommendation"] == "HOLD"
        await rev("paperrev:a", AT + 5)            # older: never current
        await rev("paperrev:0", AT + 10)           # same instant, lower id
        (c,) = await cur()
        assert c["paper_review_id"] == "paperrev:c"
        await rev("paperrev:d", AT + 10)           # same instant, higher id
        (c,) = await cur()
        assert c["paper_review_id"] == "paperrev:d"
        # every review row is kept (history), one current
        assert await conn.fetchval("SELECT count(*) FROM paper_xavier_reviews"
                                   " WHERE group_id=$1", g) == 5
        X = asyncpg.IntegrityConstraintViolationError
        await _expect(conn, X, "UPDATE xavier_current_review SET "
                      "reviewed_at = reviewed_at - interval '1 s' "
                      "WHERE group_id=$1", g)
        await _expect(conn, X, "DELETE FROM xavier_current_review "
                      "WHERE group_id=$1", g)
        await _expect(conn, X, "UPDATE xavier_current_review SET "
                      "group_id='other' WHERE group_id=$1", g)
        await _expect(conn, asyncpg.CheckViolationError,
                      "UPDATE xavier_current_review SET assessment_id="
                      "NULL, paper_review_id=NULL WHERE group_id=$1", g)
        await _expect(conn, asyncpg.ForeignKeyViolationError,
                      "UPDATE xavier_current_review SET paper_review_id="
                      "'paperrev:nope' WHERE group_id=$1", g)
        await _expect(conn, asyncpg.UniqueViolationError,
                      "INSERT INTO xavier_current_review (position_kind, "
                      " group_id, review_table, paper_review_id, reviewed_at)"
                      " VALUES ('PAPER',$1,'paper_xavier_reviews',"
                      " 'paperrev:a',now())", g)
        # an ACTUAL position's current review is its newest assessment; a
        # PAPER assessment never moves the paper pointer
        asm = ("INSERT INTO xavier_management_assessments (assessment_id, "
               " position_kind, group_id, review_id, assessed_at, trigger, "
               " evidence_state, venue_economics, thesis_state, "
               " thesis_detail, alternatives, recommendation, "
               " discretionary_permitted, reallocate, policy, "
               " recommendation_state) VALUES ($1,$2,$3,'rv',"
               " to_timestamp($4),'SCHEDULED_BACKSTOP',"
               " 'STALE_ENTRY_TIME_PROBABILITY','{}'::jsonb,'NO_ENTRY_THESIS',"
               " '{}'::jsonb,'[]'::jsonb,'WAITING_FOR_FRESH_EVIDENCE',false,"
               " '{\"mode\": \"SHADOW\"}'::jsonb,"
               " '{\"status\": \"READY_FOR_OWNER_APPROVAL\"}'::jsonb,"
               " 'WAITING_FOR_FRESH_EVIDENCE')")
        ga = "grp_actual_%s" % uuid.uuid4().hex[:8]
        await conn.execute(asm, "xa:1", "ACTUAL", ga, AT)
        await conn.execute(asm, "xa:2", "ACTUAL", ga, AT + 5)
        await conn.execute(asm, "xa:0", "ACTUAL", ga, AT - 5)
        await conn.execute(asm, "xa:p", "PAPER", g, AT + 99)
        (c,) = await conn.fetch("SELECT * FROM xavier_current_review WHERE "
                                " position_kind='ACTUAL' AND group_id=$1", ga)
        assert c["assessment_id"] == "xa:2" and c["paper_review_id"] is None
        assert c["review_table"] == "xavier_management_assessments"
        (c,) = await cur()
        assert c["paper_review_id"] == "paperrev:d"
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §3 THE QUEUE THROUGH THE REAL REVIEW PATH
# ═════════════════════════════════════════════════════════════════════

async def _requests(conn, g):
    return {r["kind"]: dict(r) for r in await conn.fetch(
        "SELECT r.*, (SELECT array_agg(e.state ORDER BY e.event_id) FROM "
        "  agent_work_request_events e WHERE e.request_id = r.request_id) "
        "  AS states, (SELECT e.evidence_table || ':' || e.evidence_id "
        "  FROM agent_work_request_events e WHERE e.request_id = "
        "  r.request_id AND e.state='COMPLETED') AS evidence, "
        "  (SELECT e.failure FROM agent_work_request_events e WHERE "
        "  e.request_id = r.request_id AND e.state='FAILED') AS failure "
        "  FROM agent_work_requests r WHERE r.group_id=$1 "
        " ORDER BY r.enqueued_at", g)}


class _MD:
    """A market-data client stand-in: one canned book per slug."""

    def __init__(self, books, at):
        self.books, self.at, self.calls = books, at, []
        self.mutation_attempts = 0

    async def read_book(self, slug, **kw):
        self.calls.append(slug)
        md = self.books.get(slug)
        if md is None:
            return {"marketData": None, "error": "NO_BOOK_FIXTURE",
                    "observed_at": self.at}
        return {"marketData": md, "observed_at": self.at}


@pg
async def test_a_stale_review_enqueues_and_fresh_evidence_closes_the_loop():
    conn, tx = await _tx()
    try:
        a, g, slug = await XR._held(conn, "awq", entry_age_s=3600)
        t1 = AT + 100                     # the review's book is 102 s old
        ctx = XR._ctx(a, t1)
        await PX.review_group(conn, ctx, g, trigger=PX.T_BACKSTOP)
        rv = await conn.fetchrow(
            "SELECT * FROM paper_xavier_reviews WHERE group_id=$1", g)
        assert rv["recommendation"] == XF.REC_WAITING
        rq = await _requests(conn, g)
        # the synthetic entry valuation's condition id is a fixture identity
        # with no fixture row persisted: GAME_STATE is owed too
        assert set(rq) == set(WQ.KINDS)
        assert rq[WQ.K_GAME_STATE]["fixture_identity"] == \
            "fixture_metadata:%s" % slug
        for k, r in rq.items():
            assert r["states"] == ["ENQUEUED"], k
            assert r["reason"] == XF.REC_WAITING
            assert r["batch_id"] == rv["review_id"]
            assert r["source_id"] == rv["review_id"]
            assert r["us_market_slug"] == slug
            assert _j(r["detail"])["account_id"] == a["account_id"]
            assert r["expires_at"].timestamp() == pytest.approx(
                t1 + WQ.REQUEST_TTL_S)
        # the pointer is this review, and the review stays WAITING: nothing
        # discretionary on stale evidence (no EXIT / REDUCE order)
        assert await conn.fetchval(
            "SELECT paper_review_id FROM xavier_current_review WHERE "
            " group_id=$1", g) == rv["review_id"]
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND role "
            " IN ('EXIT','REDUCE')", g) == 0
        # a second stale review: deduplicated, nothing new
        await PX.review_group(conn, XR._ctx(a, t1 + 20), g,
                              trigger=PX.T_BACKSTOP)
        assert {k: r["request_id"] for k, r in (await _requests(
            conn, g)).items()} == {k: r["request_id"] for k, r in rq.items()}
        assert await conn.fetchval(
            "SELECT count(*) FROM xavier_current_review WHERE group_id=$1",
            g) == 1
        # ── the drain dispatches the held re-evaluation (no scheduler in
        # this process: the answer is recorded by name) ──
        dctx = dict(XR._ctx(a, t1 + 30), schedule_reassessment=None)
        d1 = await WQ.drain(conn, dctx)
        assert d1["dispatched"] >= 1, d1
        p = (await _requests(conn, g))[WQ.K_PROBABILITY]
        assert p["states"] == ["ENQUEUED", "DISPATCHED"]
        disp = _j(await conn.fetchval(
            "SELECT detail FROM agent_work_request_events WHERE "
            " request_id=$1 AND state='DISPATCHED'", p["request_id"]))
        assert disp["answer"]["queued"] is False and disp["answer"]["reason"]
        # ── the priority book read (step_books consumes the list first) ──
        md = _MD({slug: H.md(bids=[(0.80, 100)], offers=[(0.82, 100)])},
                 t1 + 40)
        bctx = dict(XR._ctx(a, t1 + 40), market_data=md, books_read=0,
                    deadline=time.monotonic() + 30)
        got = await PR.step_books(conn, bctx)
        assert md.calls[0] == slug
        assert got["work_queue"]["completed"] == 1, got
        b = (await _requests(conn, g))[WQ.K_VENUE_BOOK]
        assert b["states"] == ["ENQUEUED", "COMPLETED"]
        assert b["evidence"] == "paper_book_observations:%s" % \
            got["obs"][slug]["obs_id"]
        # ── a fresh valuation of the contract lands ──
        vid = await XR._reading(conn, slug, decided_at=t1 + 45,
                                pin_age_s=3.0, p=0.71)
        asked = []
        d2 = await WQ.drain(conn, dict(XR._ctx(a, t1 + 50),
                                       schedule_reassessment=asked.append))
        assert d2["completed"] >= 1, d2
        p = (await _requests(conn, g))[WQ.K_PROBABILITY]
        assert p["states"][-1] == "COMPLETED"
        assert p["evidence"] == "external_valuations:%s" % vid
        # the re-review is dispatched once evidence landed (once)
        assert asked == [[slug]]
        await WQ.drain(conn, dict(XR._ctx(a, t1 + 55),
                                  schedule_reassessment=asked.append))
        assert asked == [[slug]]
        # ── the re-review: fresh evidence decides; it closes the loop ──
        await PX.review_group(conn, XR._ctx(a, t1 + 52), g,
                              trigger=PX.T_VALUATION)
        rv2 = await conn.fetchrow(
            "SELECT * FROM paper_xavier_reviews WHERE group_id=$1 "
            " ORDER BY reviewed_at DESC LIMIT 1", g)
        assert _j(rv2["measure"])["evidence_state"] == XF.E_FRESH
        assert rv2["recommendation"] == "EXIT"   # 0.80 bid beats 0.71
        r = (await _requests(conn, g))[WQ.K_REASSESS]
        assert r["states"] == ["ENQUEUED", "DISPATCHED", "COMPLETED"]
        assert r["evidence"] == "paper_xavier_reviews:%s" % rv2["review_id"]
        assert await conn.fetchval(
            "SELECT paper_review_id FROM xavier_current_review WHERE "
            " group_id=$1", g) == rv2["review_id"]
        # only the game state is still owed (no fixture row was acquired)
        assert [r["kind"] for r in await conn.fetch(
            "SELECT kind FROM agent_work_open WHERE group_id=$1", g)] == [
            WQ.K_GAME_STATE]
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_unanswered_requests_expire_back_off_and_failed_reads_fail():
    conn, tx = await _tx()
    try:
        a, g, slug = await XR._held(conn, "awqx", entry_age_s=3600)
        t1 = AT + 100
        await PX.review_group(conn, XR._ctx(a, t1), g,
                              trigger=PX.T_BACKSTOP)
        rq = await _requests(conn, g)
        # the venue read fails: VENUE_BOOK FAILED, naming the read
        got = await WQ.complete_book_reads(
            conn, {slug: {"obs_id": 1, "error": "HTTP_503"}}, at=t1 + 5)
        assert got == {"completed": 0, "failed": 1}
        assert (await _requests(conn, g))[WQ.K_VENUE_BOOK]["failure"] == \
            WQ.F_BOOK_READ
        # nothing else arrives: past the TTL the drain FAILS what is open
        t2 = t1 + WQ.REQUEST_TTL_S + 1
        d = await WQ.drain(conn, XR._ctx(a, t2))
        assert d["expired"] >= 3, d      # (other accounts' too: every TTL)
        now = await _requests(conn, g)
        assert now[WQ.K_PROBABILITY]["failure"] == WQ.F_EXPIRED
        assert now[WQ.K_GAME_STATE]["failure"] == WQ.F_EXPIRED
        assert now[WQ.K_REASSESS]["failure"] == WQ.F_NO_EVIDENCE
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_work_open WHERE group_id=$1", g) == 0
        # the next stale review is inside the failure backoff: nothing new
        await PX.review_group(conn, XR._ctx(a, t2 + 10), g,
                              trigger=PX.T_BACKSTOP)
        assert {k: r["request_id"] for k, r in (await _requests(
            conn, g)).items()} == {k: r["request_id"] for k, r in
                                   rq.items()}
        # after the backoff it asks again (a new request, a new batch)
        t3 = t2 + WQ.FAILURE_BACKOFF_BASE_S + 5
        await PX.review_group(conn, XR._ctx(a, t3), g,
                              trigger=PX.T_BACKSTOP)
        n = await conn.fetchval(
            "SELECT count(*) FROM agent_work_requests WHERE group_id=$1 "
            "   AND kind='PROBABILITY'", g)
        assert n == 2
        assert WQ.backoff_s([{"state": "FAILED"}] * 3) == \
            WQ.FAILURE_BACKOFF_BASE_S * 4
        assert WQ.backoff_s([{"state": "FAILED"}] * 9) == \
            WQ.FAILURE_BACKOFF_MAX_S
        assert WQ.backoff_s([{"state": "COMPLETED"}, {"state": "FAILED"}]) \
            == WQ.REENQUEUE_AFTER_COMPLETED_S
        assert WQ.backoff_s([]) is None
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_game_state_needs_a_fixture_identity_and_completes_on_a_current_row():
    conn, tx = await _tx()
    try:
        a, g, slug = await XR._held(conn, "awqg", entry_age_s=3600)
        cid = "0xcond%s" % uuid.uuid4().hex[:12]
        await conn.execute(
            "UPDATE external_valuations SET condition_id=$1 WHERE id = ("
            " SELECT d.valuation_id FROM paper_orders o JOIN paper_decisions"
            " d ON d.decision_id=o.decision_id WHERE o.group_id=$2 "
            " AND o.role='ENTRY')", cid, g)
        fx = ("INSERT INTO fixture_metadata (condition_id, play_has_begun, "
              " source, source_url, retrieved_at, reader_version) VALUES "
              " ($1, false, 'TEST', 'test://fixture', to_timestamp($2), "
              " 'TEST') ON CONFLICT (condition_id) DO UPDATE SET "
              " retrieved_at = EXCLUDED.retrieved_at")
        t1 = AT + 100
        await conn.execute(fx, cid, t1 - 3600)            # an hour old
        await PX.review_group(conn, XR._ctx(a, t1), g,
                              trigger=PX.T_BACKSTOP)
        gs = (await _requests(conn, g))[WQ.K_GAME_STATE]
        assert gs["fixture_identity"] == "fixture_metadata:%s" % cid
        assert gs["states"] == ["ENQUEUED"]
        # not current yet: the drain dispatches (the held re-evaluation
        # re-acquires the fixture row) and leaves it open
        await WQ.drain(conn, XR._ctx(a, t1 + 10))
        assert (await _requests(conn, g))[WQ.K_GAME_STATE]["states"] == [
            "ENQUEUED", "DISPATCHED"]
        # the row is re-acquired: COMPLETED with that row as evidence
        await conn.execute(fx, cid, t1 + 15)
        await WQ.drain(conn, XR._ctx(a, t1 + 20))
        gs = (await _requests(conn, g))[WQ.K_GAME_STATE]
        assert gs["states"][-1] == "COMPLETED"
        assert gs["evidence"].startswith("fixture_metadata:%s@" % cid)
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_closed_position_fails_its_requests_and_fresh_reviews_enqueue_nothing():
    conn, tx = await _tx()
    try:
        a, g, slug = await XR._held(conn, "awqc", entry_age_s=3600)
        await XR._reading(conn, slug, decided_at=AT - 3, pin_age_s=5.0,
                          p=0.95)
        await PX.review_group(conn, XR._ctx(a, AT), g,
                              trigger=PX.T_BACKSTOP)
        assert (await conn.fetchval(
            "SELECT recommendation FROM paper_xavier_reviews WHERE "
            " group_id=$1", g)) == "HOLD"
        assert await _requests(conn, g) == {}        # fresh: nothing owed
        # an open request whose position has closed is FAILED by name
        got = await WQ.enqueue(
            conn, kind=WQ.K_PROBABILITY, group_id="paper_g_gone_x",
            reason=XF.REC_WAITING, at=AT, batch_id="b-gone", slug="gone",
            detail={"account_id": a["account_id"]})
        assert got["enqueued"], got
        d = await WQ.drain(conn, XR._ctx(a, AT + 1))
        assert d["closed_positions"] == 1, d
        assert (await _requests(conn, "paper_g_gone_x"))[
            WQ.K_PROBABILITY]["failure"] == WQ.F_CLOSED
        # and without the migration the hook is a named no-op
        assert (await WQ.after_review(
            _NoSchema(), {}, group_id="g", pos={}, review_id="r",
            recommendation=XF.REC_WAITING, measure={}))["refusal"] == \
            WQ.R_NO_SCHEMA
    finally:
        await tx.rollback()
        await conn.close()


class _NoSchema:
    async def fetchval(self, sql, *a):
        return False


@pg
async def test_the_floor_reads_xavier_from_the_current_review_and_the_queue():
    from sportsassets import agent_work_state as AWS
    conn, tx = await _tx()
    try:
        a, g, slug = await XR._held(conn, "awqf", entry_age_s=3600)
        t1 = AT + 100
        await PX.review_group(conn, XR._ctx(a, t1), g,
                              trigger=PX.T_BACKSTOP)
        got = await AWS.read_facts(conn, now=t1 + 5)
        x = got["facts"]["XAVIER"]
        assert x["open_positions"] >= 1
        (p,) = [p for p in x["positions"] if p["group_id"] == g]
        rid = await conn.fetchval(
            "SELECT paper_review_id FROM xavier_current_review WHERE "
            " group_id=$1", g)
        assert p["current_review"]["review_id"] == rid
        assert p["current_review"]["management_state"] == XF.S_WAITING
        assert set(p["requests"]) == set(WQ.KINDS)
        # its book is 107 s old (< 300 s): WAITING, not blocked
        assert AWS.position_class(p, market=AWS.market_status(
            x["market"], t1 + 5), now=t1 + 5)[0] == AWS.P_WAITING
        st = AWS.derive("XAVIER", x, now=t1 + 5)
        assert st["state"] != AWS.IDLE
        assert st["counts"]["open_requests_by_kind"][WQ.K_PROBABILITY] >= 1
    finally:
        await tx.rollback()
        await conn.close()
