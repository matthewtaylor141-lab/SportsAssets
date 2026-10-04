"""Shared fixtures for the R30B agents stream (migration 234): durable work
queues, scorecards, lesson usage and root-cause clusters.

ALL DATA IS SYNTHETIC TEST DATA in the SHAPES PRODUCTION WRITES (read-only
evidence, research-sql run 37226555657, 2026-10-04 19:00Z): a paper decision
carries the columns paper_benchmark writes; a stale-HOLD review carries the
measure keys production's reviews carry ({at, best_exit_at_review,
book_obs_id, disclosure, economics_label, exceptional_states, internal_model,
measure_is, p, p_internal, source, stale, strategy, void_applied, why} with
stale = true and no probability_age_s). Every scenario runs inside one
transaction that is rolled back.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import asyncpg
import pytest

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP = (MIG / "234_agent_operations.sql").read_text()
DOWN = (MIG / "rollback" / "234_agent_operations.down.sql").read_text()
NOW = 1_790_500_000.0
EXPLORATION = "PINNACLE_EXPLORATION_PAPER"


def j(v):
    return json.loads(v) if isinstance(v, str) else v


async def tx():
    conn = await asyncpg.connect(H.DSN)
    t = conn.transaction()
    await t.start()
    await conn.execute(UP)
    return conn, t


async def done(conn, t):
    await t.rollback()
    await conn.close()


async def expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


async def account(conn, tag="ops", now=NOW - 10 * 86400):
    return await H.new_account(conn, tag, now=now)


async def decision(conn, acct, *, at, slug=None, side="LONG",
                   verdict="ENTER", refusals=(), strategy=EXPLORATION,
                   p=0.6, qty=100, limit=0.40, did=None) -> str:
    """A paper decision row with paper_benchmark's columns."""
    did = did or "paperdec:%s" % uuid.uuid4().hex[:24]
    refusals = list(refusals)
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, us_market_slug, holding_side, intent, fixture, label, "
        " verdict, refusal, refusals, p_internal, internal_model, "
        " p_pinnacle, pinnacle, p_blended, proposed_qty, limit_price, "
        " qualification_gaps, policy_version, policy_decision, "
        " simulator_version, strategy, economics) VALUES ($1,$2,$3,"
        " to_timestamp($4),$5,$6,'ORDER_INTENT_BUY_LONG','fx-1','{}'::jsonb,"
        " $7,$8,$9,NULL,'{}'::jsonb,$10,'{}'::jsonb,$10,$11,$12,'[]'::jsonb,"
        " 'TEST_POLICY_V1','{}'::jsonb,'TEST',$13,'{}'::jsonb)",
        did, acct["session_id"], acct["account_id"], float(at),
        slug or "aec-test-%s" % uuid.uuid4().hex[:8], side, verdict,
        None if verdict == "ENTER" else (refusals[0] if refusals
                                         else "BELOW_MIN_GROSS_EDGE"),
        refusals, float(p), float(qty), float(limit), strategy)
    return did


async def stale_hold_review(conn, acct, *, group_id, at,
                            strategy=EXPLORATION, stale=True) -> str:
    """A Xavier HOLD review in production's pre-R30 shape (stale = true,
    no probability_age_s), as the HOLD_ON_STALE_PROBABILITY detector reads
    it."""
    rid = "paperrev:%s" % uuid.uuid4().hex[:24]
    measure = {"at": at, "best_exit_at_review": None, "book_obs_id": None,
               "disclosure": "TEST", "economics_label": "TEST",
               "exceptional_states": [], "internal_model": {},
               "measure_is": "HOLD_VALUE", "p": 0.55, "p_internal": None,
               "source": "PINNACLE_DEVIG_V1", "stale": bool(stale),
               "strategy": strategy, "void_applied": False,
               "why": "TEST_FIXTURE"}
    await conn.execute(
        "INSERT INTO paper_xavier_reviews (review_id, session_id, "
        " account_id, group_id, reviewed_at, trigger, recommendation, "
        " measure, alternatives, selection, exposure, strategy) VALUES "
        " ($1,$2,$3,$4,to_timestamp($5),'SCHEDULED_BACKSTOP','HOLD',"
        " $6::jsonb,'{}'::jsonb,'{}'::jsonb,'{}'::jsonb,$7)",
        rid, acct["session_id"], acct["account_id"], group_id, float(at),
        json.dumps(measure), strategy)
    return rid


async def requests(conn, kind=None, agent=None) -> list:
    rows = await conn.fetch(
        "SELECT r.*, (SELECT array_agg(e.state ORDER BY e.event_id) FROM "
        "       agent_work_request_events e WHERE e.request_id = "
        "       r.request_id) AS states, (SELECT row_to_json(e) FROM "
        "       agent_work_request_events e WHERE e.request_id = "
        "       r.request_id AND e.state IN ('COMPLETED', 'FAILED')) AS "
        "       terminal, EXISTS (SELECT 1 FROM agent_work_open o WHERE "
        "       o.request_id = r.request_id) AS is_open "
        "  FROM agent_work_requests r "
        " WHERE ($1::text IS NULL OR r.kind = $1) "
        "   AND ($2::text IS NULL OR r.agent_id = $2) "
        " ORDER BY r.enqueued_at, r.request_id", kind, agent)
    out = []
    for r in rows:
        d = dict(r)
        d["terminal"] = j(d["terminal"])
        d["detail"] = j(d["detail"])
        d["evidence_needed"] = j(d["evidence_needed"])
        out.append(d)
    return out


async def attempts(conn, request_id) -> list:
    return [dict(r) for r in await conn.fetch(
        "SELECT outcome, blocker, extract(epoch FROM at)::float8 AS at, "
        "       extract(epoch FROM next_attempt_at)::float8 AS nxt, detail "
        "  FROM agent_work_request_events WHERE request_id = $1 "
        "   AND state = 'ATTEMPTED' ORDER BY event_id", request_id)]
