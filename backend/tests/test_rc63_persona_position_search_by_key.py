"""rc6.3 capability: the persona's position search reads records by key.

PRODUCTION (research-sql runs 38008493448, 38009039152 and 38009425966,
SELECT only). Every capability review is scoped to a position (a handoff's
group and decision, a settlement's group), and for a scoped question the
persona fact gather searched EVERY paper table by serialising each row to
JSON text and matching the id inside it (`to_jsonb(x)::text LIKE '%id%'`),
which reads and detoasts whole tables: paper_decisions is 3,235 MB,
paper_xavier_reviews 1,640 MB. EXPLAIN ANALYZE of one real handoff context:
the Xavier current-decision read 26,498 ms (pg_stat_statements: 611 calls,
mean 12.9 s), the paper_decisions search 13,594 ms; for one settlement
context paper_book_observations 9,637 ms and paper_order_events 7,404 ms,
both matching nothing. Those four reads alone are ~57 s, past the worker's
55 s budget: 1,138 reviews ended TimeoutError (XAVIER 1,081, DEREK 57) at
55.1-67.7 s, and the median completed review had grown 24 s -> 53 s
between 2026-10-02 and 10-06 as the tables grew. The same reads by key:
2.85 ms (Xavier) and 4.30 ms (paper_decisions, primary key).

THE FIX. A context id is matched on the key columns a table has
(persona_facts.ID_COLUMNS: group_id, decision_id, intent_id ...), by
equality, or on a text column that names it (REF_COLUMNS: an Audrey
finding's subject, a position key), by substring; a table with none of them is listed in `checked` as
NO_KEY_COLUMN_FOR_CONTEXT_ID instead of scanned; Xavier's current decision
is read through paper_xavier_reviews (group_id, reviewed_at) and
paper_orders (decision_id). A question naming a TEAM is still searched by
the record text (unchanged).

These tests run the REAL persona_facts.gather on a real Postgres database
against a scratch paper position and record every statement it issues. On
the base implementation the gather issues the JSON-text scans and the first
test fails.

Requires RN1X_TEST_DSN. Every write is inside one transaction that is
rolled back.
"""
from __future__ import annotations

import asyncio
import time

import asyncpg
import pytest

from sportsassets.agents import persona_facts as PF
from tests import agent_ops_fixture as O
from tests import paper_harness as H

pytestmark = pytest.mark.skipif(not H.DSN, reason="requires RN1X_TEST_DSN")

TEXT_SCAN = "to_jsonb(x)::text"


class Recording:
    """An asyncpg connection that records the SQL of every statement."""

    def __init__(self, conn):
        self.conn, self.sql = conn, []

    def transaction(self, **kw):
        return self.conn.transaction(**kw)

    def __getattr__(self, name):
        attr = getattr(self.conn, name)
        if name in ("fetch", "fetchrow", "fetchval", "execute"):
            async def wrapped(sql, *args, **kw):
                self.sql.append(str(sql))
                return await attr(sql, *args, **kw)
            return wrapped
        return attr


async def _scoped_gather(context_of, *, agent="XAVIER", question=None):
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = time.time()
        acct = await O.account(conn, "cap63k")
        did = await O.decision(conn, acct, at=now - 7200)
        pos = await O.position(conn, acct, at=now - 7000, decision_id=did)
        rid = await O.stale_hold_review(conn, acct, group_id=pos["group_id"],
                                        at=now - 600, stale=False)
        # an Audrey finding NAMES the position in its subject column
        fid = "paperfind:cap63k%s" % pos["group_id"][-10:]
        await conn.execute(
            "INSERT INTO paper_audrey_findings (finding_id, session_id, "
            " account_id, found_at, kind, severity, subject, detail) VALUES "
            " ($1,$2,$3,to_timestamp($4),'STALE_MARK','WARNING',$5,"
            " '{}'::jsonb)", fid, acct["session_id"], acct["account_id"],
            now - 300, pos["position_key"])
        rec = Recording(conn)
        bundle = await PF.gather(
            rec, question=question or ("Give a full analysis of this "
                                       "assigned research task."),
            context=context_of(did, pos), now=now, agent=agent)
        return bundle, rec.sql, {"decision_id": did, "review_id": rid,
                                 "finding_id": fid, **pos}
    finally:
        await tx.rollback()
        await conn.close()


def test_a_position_named_by_id_is_read_by_key_never_by_a_text_scan():
    bundle, sql, ids = asyncio.run(_scoped_gather(
        lambda did, pos: {"position_id": pos["group_id"],
                          "decision_id": did}))
    scans = [s for s in sql if TEXT_SCAN in s]
    assert not scans, scans[:3]
    facts = bundle["facts"]
    # the position's own records are found, by key
    assert any(f["source"] == "paper_orders" and f["record_id"] ==
               ids["order_id"] for f in facts)
    assert any(f["source"] == "paper_decisions" and f["field"] ==
               "decision_id" and f["value"] == ids["decision_id"]
               for f in facts)
    assert any(f["source"] == "paper_fills" for f in facts)
    # Xavier's CURRENT decision for the group, from its own review
    assert any(f["source"] == "paper_xavier_reviews" and f["record_id"] ==
               ids["review_id"] and f["field"] ==
               "current_management_decision" for f in facts)
    # a record that NAMES the position (Audrey's finding subject is its
    # position key) is found by substring on that one text column
    assert any(f["source"] == "paper_audrey_findings" and f["field"] ==
               "finding_id" and f["value"] == ids["finding_id"]
               for f in facts)
    # a table that cannot hold the record by key is named, not scanned
    checked = {c["source"]: c["status"] for c in bundle["checked"]}
    assert checked.get("paper_book_observations") == PF.NO_KEY_COLUMN
    assert checked.get("paper_order_events") == PF.NO_KEY_COLUMN
    assert bundle["found"] is True


def test_a_settlement_context_reads_its_group_by_key():
    """The production settlement shape: the group id alone."""
    bundle, sql, ids = asyncio.run(_scoped_gather(
        lambda did, pos: {"position_id": pos["group_id"]}, agent="AUDREY"))
    assert not [s for s in sql if TEXT_SCAN in s]
    assert any(f["source"] == "paper_orders" and f["record_id"] ==
               ids["order_id"] for f in bundle["facts"])
    assert any(f["source"] == "paper_xavier_reviews" and f["record_id"] ==
               ids["review_id"] for f in bundle["facts"])


def test_the_by_key_xavier_read_finds_the_group_a_decision_opened():
    """A decision id alone reaches the group its orders opened."""
    bundle, sql, ids = asyncio.run(_scoped_gather(
        lambda did, pos: {"decision_id": did}))
    assert not [s for s in sql if TEXT_SCAN in s]
    assert any(f["source"] == "paper_xavier_reviews" and f["record_id"] ==
               ids["review_id"] for f in bundle["facts"])


def test_a_team_named_question_is_still_searched_by_text():
    """Unchanged: a subject (a team) has no key, so its terms are matched
    on the record text."""
    bundle, sql, _ = asyncio.run(_scoped_gather(
        lambda did, pos: {}, question="Walk me through the Yankees "
                                       "position."))
    assert any(TEXT_SCAN in s and "ILIKE" in s for s in sql)
