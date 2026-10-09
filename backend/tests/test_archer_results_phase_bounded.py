"""ARCHER'S RESULTS PHASE IS BOUNDED (agents.pos_workflow.attach_results).

PRODUCTION (read-only research-sql, 2026-10-09): the phase recorded
TimeoutError on 265 of 265 Archer passes in 24 h, every pass since
2026-10-05 22:25 (run 37936367236; agent_status ARCHER runs 957 = errors 957
in run 37932587274), so no step-4 / step-7 result was attached since. Its
step-7 walk joined paper_orders to paper_xavier_reviews under ORDER BY
reviewed_at LIMIT 1, and the planner walked the whole reviewed_at index
(163k reviews, 1.6 GB) for EVERY step-7 row whose decision has no order --
3815 of them (run 37936489884, EXPLAIN). On the same shape locally the walk
took 423 s; the per-group probe takes 30 ms.

  §1 the walk finishes inside a statement timeout on that shape (many
     reviews over few groups, most step-7 decisions with no order);
  §2 it attaches exactly what it attached before: the EARLIEST review of any
     of the decision's order groups, once; a step with a result, an
     ANSWERED step or a decision with no order is left alone.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

CLEAN = ("paper_xavier_reviews", "pos_candidate_reviews",
         "pos_candidate_review_steps", "paper_orders",
         "eddie_execution_outcomes")
REFS = '[{"kind": "paper_decisions", "id": "x"}]'


async def _clean(c):
    await c.execute("SET LOCAL session_replication_role = replica")
    for t in CLEAN:
        await c.execute("DELETE FROM %s" % t)


async def _step7(c, rid, decision, status="NOT_APPLICABLE"):
    await c.execute(
        "INSERT INTO pos_candidate_reviews (review_id, decision_id, "
        " opened_at) VALUES ($1, $2, now())", rid, decision)
    await c.execute(
        "INSERT INTO pos_candidate_review_steps (review_id, seq, step, agent,"
        " question, status, evidence_refs, response, at) VALUES ($1, 7, "
        " 'XAVIER_MANAGEMENT_PLAN', 'XAVIER', 'q', $2, $3::jsonb, 'r', now())",
        rid, status, REFS)


async def _order(c, oid, decision, group):
    await c.execute(
        "INSERT INTO paper_orders (order_id, idempotency_key, account_id, "
        " session_id, group_id, role, direction, holding_side, intent, "
        " us_market_slug, order_type, time_in_force, allow_partial, qty, "
        " limit_price, wire_price, state, decided_at, eligible_at, "
        " expires_at, simulator_version, decision_id, strategy, "
        " event_source) VALUES ($1, $1, 'a', 's', $2, 'ENTRY', 'BUY', "
        " 'LONG', 'BUY_YES', 'slug', 'MARKETABLE', 'IOC', true, 1, 0.5, "
        " 0.5, 'FILLED', now(), now(), now(), 'v', $3, "
        " 'DEREK_ENTRY_POLICY_V2', 'SIMULATOR')", oid, group, decision)


async def _review(c, rid, group, age_s, rec="HOLD"):
    await c.execute(
        "INSERT INTO paper_xavier_reviews (review_id, session_id, "
        " account_id, group_id, reviewed_at, trigger, alternatives, "
        " exposure, recommendation) VALUES ($1, 's', 'a', $2, "
        " now() - make_interval(secs => $3), 'FILL_EVENT', '[]', '{}', $4)",
        rid, group, float(age_s), rec)


def _run(body):
    import asyncpg

    async def main():
        c = await asyncpg.connect(DSN)
        tx = c.transaction()
        await tx.start()
        try:
            return await body(c)
        finally:
            await tx.rollback()
            await c.close()
    return asyncio.run(main())


@pg
def test_the_step7_walk_is_bounded_on_the_production_shape():
    """Many reviews over few groups; most step-7 decisions hold no order and
    so have no match. The walk must finish inside a statement timeout far
    below the phase's 25 s bound."""
    from sportsassets.agents import pos_workflow as W

    async def body(c):
        await _clean(c)
        # 40 groups x 1000 reviews (production: 163k reviews, ~800 a group)
        await c.execute(
            "INSERT INTO paper_xavier_reviews (review_id, session_id, "
            " account_id, group_id, reviewed_at, trigger, alternatives, "
            " exposure, recommendation) SELECT 'paper-xr' || i, 's', 'a', "
            " 'g' || (i % 40), now() - make_interval(secs => i), "
            " 'FILL_EVENT', '[]', '{}', 'HOLD' "
            " FROM generate_series(1, 40000) i")
        # 1200 step-7 rows; only the LAST 50 decisions hold an order
        await c.execute(
            "INSERT INTO pos_candidate_reviews (review_id, decision_id, "
            " opened_at) SELECT 'r' || lpad(i::text, 5, '0'), 'dec' || i, "
            " now() FROM generate_series(1, 1200) i")
        await c.execute(
            "INSERT INTO pos_candidate_review_steps (review_id, seq, step, "
            " agent, question, status, evidence_refs, response, at) "
            " SELECT 'r' || lpad(i::text, 5, '0'), 7, "
            " 'XAVIER_MANAGEMENT_PLAN', 'XAVIER', 'q', 'NOT_APPLICABLE', "
            " $1::jsonb, 'r', now() FROM generate_series(1, 1200) i", REFS)
        for i in range(1151, 1201):
            await _order(c, "paper-o%d" % i, "dec%d" % i, "g%d" % (i % 40))
        await c.execute("SET LOCAL session_replication_role = origin")
        for t in CLEAN[:4]:
            await c.execute("ANALYZE %s" % t)
        await c.execute("SET LOCAL statement_timeout = '5s'")
        t0 = time.monotonic()
        got = await W.attach_results(c, now=time.time(), limit=20)
        elapsed = time.monotonic() - t0
        n = await c.fetchval(
            "SELECT count(*) FROM pos_candidate_review_steps "
            " WHERE seq = 7 AND result IS NOT NULL")
        return got, elapsed, n

    got, elapsed, n = _run(body)
    assert got == {"attached": 20}
    assert n == 20
    assert elapsed < 5.0, elapsed


@pg
def test_the_step7_result_is_the_earliest_review_of_any_order_group():
    from sportsassets.agents import pos_workflow as W

    async def body(c):
        await _clean(c)
        # dec-a: two orders in two groups; the earliest review is gB's
        await _step7(c, "r-a", "dec-a")
        await _order(c, "paper-oa1", "dec-a", "gA")
        await _order(c, "paper-oa2", "dec-a", "gB")
        await _review(c, "paper-xa-new", "gA", 100, "HOLD")
        await _review(c, "paper-xa-old", "gA", 500, "EXIT")
        await _review(c, "paper-xb-oldest", "gB", 900, "REDUCE")
        # dec-b: no order -> left alone
        await _step7(c, "r-b", "dec-b")
        # dec-c: an ANSWERED step -> left alone even with an order + review
        await _step7(c, "r-c", "dec-c", status="ANSWERED")
        await _order(c, "paper-oc", "dec-c", "gC")
        await _review(c, "paper-xc", "gC", 50)
        # dec-d: an order whose group has no review yet -> left alone
        await _step7(c, "r-d", "dec-d", status="NO_RECORD")
        await _order(c, "paper-od", "dec-d", "gD")
        await c.execute("SET LOCAL session_replication_role = origin")
        first = await W.attach_results(c, now=time.time())
        again = await W.attach_results(c, now=time.time())
        rows = {r["review_id"]: r["result"] for r in await c.fetch(
            "SELECT review_id, result FROM pos_candidate_review_steps "
            " WHERE seq = 7")}
        return first, again, rows

    first, again, rows = _run(body)
    assert first == {"attached": 1}
    assert again == {"attached": 0}                 # once each
    res = json.loads(rows["r-a"])
    assert res == {"kind": "paper_xavier_reviews", "id": "paper-xb-oldest",
                   "recommendation": "REDUCE"}
    assert rows["r-b"] is None and rows["r-c"] is None and rows["r-d"] is None
