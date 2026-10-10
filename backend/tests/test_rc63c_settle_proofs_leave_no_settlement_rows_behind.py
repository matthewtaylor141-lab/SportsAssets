"""CAPITAL-CRITICAL: THE SETTLE PROOFS LEAVE NO PAPER LEDGER ROWS BEHIND, AND
THE SETTLEMENT-EXCEPTION READ IS NOT MULTIPLIED BY THEM (RC6.3c, backend-tests
38076712880 on dd25c588).

WHAT FAILED. backend-tests 38076712880 (the RC6.3c candidate dd25c588, 24,364
tests in one process against one shared PostgreSQL) failed
tests/test_settlement_exception_risk.py::test_the_cost_rides_on_the_intent_and_
gates_nothing with the canonical decision's settlement_exception_risk component
recorded {status: UNAVAILABLE, why: COMPONENT_TIMEOUT_AT_DECISION_2.0S}: the
component's read, settlement_exception_risk.measure, did not finish inside
canonical_components.COMPONENT_TIMEOUT_S (2.0 s). The same test passed on every
earlier candidate (38062046653 on 5979416f: 1.18 s; 38062062302 on 4534b43f;
38061064163 on f971d665; 38050580296; 38041572081; 38021637282). The junit
report of the failing run shows WHERE the time went: the two other real-database
tests of the same file, each a bare call of measure, took 4.60 s and 4.32 s
against 0.23 s and 0.11 s on 5979416f -- the read itself had become ~4 s.

WHY. measure's PAPER_SQL is DISTINCT ON (position_key) over paper_settlements
with a LATERAL (SELECT ... FROM external_valuations x WHERE x.us_market_slug =
s.us_market_slug ORDER BY x.id DESC LIMIT 1) per settlement row, and
external_valuations has no index on us_market_slug: every settlement row costs
one scan of external_valuations (a backward walk of the primary key filtered
by slug, the WHOLE key when the slug has no valuation row), so the read is
(#paper_settlements rows) x (external_valuations heap). On a fresh migrated
database, 1,216 settlement rows x 20,000 valuation rows measured 6.7 s, x 5,000
measured 1.0 s, and 2 rows x 20,000 measured 0.01 s.

WHERE THE ROWS CAME FROM. tests/test_rc63c_settle_reads_outcomes_once.py (the
pass-hardening lane, merged into the candidate at ac38a116, 2,729 tests before
the failing one in collection order) seeds 1,215 settled positions under fresh
paper accounts through plain autocommit statements -- 600 + 600 closed
positions and the every-kind ledgers -- and its `purge` removed ONLY the
external_valuations rows under its slug prefixes: the paper_settlements (1,215),
paper_fills (1,225) and paper_orders (1,229) rows stayed committed in the
shared CI database, every settlement on a contract whose valuation row was
gone. The 171 files that ran next committed their own valuation rows, and
when the settlement-exception proof ran, PAPER_SQL walked those rows 1,215
times. Neither settlement_exception_risk.py nor its test changed between
5979416f and dd25c588; the hung pg_sleep statements of the pass-hardening
proofs are cancelled inside their own tests (longest observed 12 s, none
alive after the file) and hold no lock a SELECT waits on; CI variance was
not it (the slow tests were exactly the three that call measure).

THE FIX (test only; the victim's read is production code, pinned decision
logic, and is not changed here): the polluter cleans up what it wrote --
`purge` now also removes the orders, fills, settlements, non-funding ledger
entries and findings of the paper accounts the proof created. The proofs here
run the settle proofs' own tests in-process and pin that nothing of theirs
remains, and that the settlement-exception read sees the same number of
settlement rows -- and runs its valuation scan the same number of times --
before and after them. On ada9270c (before the fix) the counts grow by 600 and
by the every-kind ledgers' settlements, and the scan loop count with them.

SYNTHETIC rows on a scratch test database under fresh paper accounts; no
venue, no order authority, nothing of the live paper account.
"""
from __future__ import annotations

import json

import pytest

from sportsassets import settlement_exception_risk as SER
from tests import paper_harness as H
from tests import test_rc63c_settle_reads_outcomes_once as T

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

#: the paper ledger tables the settle proofs write rows into, by contract slug
LEDGER_TABLES = ("paper_settlements", "paper_fills", "paper_orders")


async def _rows_of_the_proofs(conn) -> dict:
    """Every row the settle proofs' slug prefix (T.SYN) still has in the
    paper ledger tables and in external_valuations, plus the number of
    settlement rows whose contract has no valuation row at all (the shape
    PAPER_SQL walks the whole table for)."""
    like = T.SYN + "%"
    out = {}
    for t in LEDGER_TABLES:
        out[t] = int(await conn.fetchval(
            "SELECT count(*) FROM %s WHERE us_market_slug LIKE $1" % t, like))
    out["external_valuations"] = int(await conn.fetchval(
        "SELECT count(*) FROM external_valuations WHERE us_market_slug LIKE $1",
        like))
    out["settlements_without_a_valuation_row"] = int(await conn.fetchval(
        "SELECT count(*) FROM paper_settlements s "
        " WHERE s.us_market_slug LIKE $1 AND NOT EXISTS ("
        "   SELECT 1 FROM external_valuations x "
        "    WHERE x.us_market_slug = s.us_market_slug)", like))
    return out


def _scans(plan, out=None):
    out = [] if out is None else out
    if "Relation Name" in plan:
        out.append(plan)
    for child in plan.get("Plans") or []:
        _scans(child, out)
    return out


async def _paper_sql_valuation_loops(conn) -> int:
    """How many times measure's PAPER_SQL scans external_valuations on this
    database right now: the sum of the actual loop counts of its
    external_valuations scan nodes (EXPLAIN ANALYZE, FORMAT JSON)."""
    raw = await conn.fetchval(
        "EXPLAIN (ANALYZE, FORMAT JSON) " + SER.PAPER_SQL, SER.MAX_MARKETS)
    plan = (json.loads(raw) if isinstance(raw, str) else raw)[0]["Plan"]
    nodes = [s for s in _scans(plan)
             if s["Relation Name"] == "external_valuations"]
    assert nodes, [s["Relation Name"] for s in _scans(plan)]
    return sum(int(s.get("Actual Loops") or 0) for s in nodes)


@pg
async def test_the_six_hundred_position_proof_leaves_no_ledger_rows_behind():
    conn = await H.connect()
    try:
        before = await _rows_of_the_proofs(conn)
        loops_before = await _paper_sql_valuation_loops(conn)
        settled_before = (await SER.measure(conn))["source"][
            "paper_settled_positions"]
        await T.test_the_settle_step_reads_outcome_rows_in_one_statement_for_six_hundred_positions()
        after = await _rows_of_the_proofs(conn)
        # ada9270c: paper_settlements +600, paper_fills +600, paper_orders +600,
        # settlements_without_a_valuation_row +600
        assert after == before, {"before": before, "after": after}
        # and the settlement-exception read is untouched by the proof: the
        # same settled positions, the same number of valuation scans
        t = await SER.measure(conn)
        assert t["ok"], t
        assert t["source"]["paper_settled_positions"] == settled_before
        assert await _paper_sql_valuation_loops(conn) == loops_before
    finally:
        await conn.close()


@pg
async def test_the_every_kind_proof_leaves_no_ledger_rows_behind(monkeypatch):
    conn = await H.connect()
    try:
        before = await _rows_of_the_proofs(conn)
        loops_before = await _paper_sql_valuation_loops(conn)
        await T.test_the_settle_step_books_the_same_outcomes_with_the_batched_read_as_with_the_per_slug_loop(
            monkeypatch)
        after = await _rows_of_the_proofs(conn)
        # ada9270c: the two accounts' settlements (the ledger's own, the
        # step's, the correction), fills and orders stay
        assert after == before, {"before": before, "after": after}
        assert await _paper_sql_valuation_loops(conn) == loops_before
    finally:
        await conn.close()


@pg
async def test_the_row_for_row_proof_leaves_no_valuation_rows_behind():
    conn = await H.connect()
    try:
        before = await _rows_of_the_proofs(conn)
        await T.test_the_batched_read_returns_each_contracts_rows_as_the_per_slug_read_did()
        assert await _rows_of_the_proofs(conn) == before
    finally:
        await conn.close()


@pg
async def test_purge_removes_exactly_the_accounts_rows_and_keeps_the_account():
    """`purge` with accounts: the account's orders, fills, settlements,
    non-funding ledger entries and findings go; the account, its session and
    its INITIAL_FUNDING entry stay (a fresh, funded, empty paper account); a
    neighbour account's rows are untouched."""
    conn = await H.connect()
    a = b = None
    pa, pb = T.SYN + "pa-", T.SYN + "pb-"
    try:
        a = await H.new_account(conn, "pg1", now=T.AT - 60_000.0)
        b = await H.new_account(conn, "pg2", now=T.AT - 60_000.0)
        await T.bulk_closed_positions(conn, a, 5, prefix=pa)
        await T.bulk_closed_positions(conn, b, 3, prefix=pb)
        ka = {"group_id": "paper_g_%s_x" % a["account_id"][-10:],
              "slug": pa + "ledger"}
        await T.entry(conn, a, group_id=ka["group_id"], slug=ka["slug"])
        await T.settled_through_the_ledger(
            conn, a, group_id=ka["group_id"], slug=ka["slug"], side="LONG",
            outcome="WON", at=T.AT - 1000.0)

        async def counts(acct):
            q = {"settlements": "SELECT count(*) FROM paper_settlements "
                                "WHERE account_id=$1",
                 "fills": "SELECT count(*) FROM paper_fills WHERE account_id=$1",
                 "orders": "SELECT count(*) FROM paper_orders "
                           "WHERE account_id=$1",
                 "ledger_other": "SELECT count(*) FROM paper_ledger WHERE "
                                 "account_id=$1 AND kind <> 'INITIAL_FUNDING'",
                 "funding": "SELECT count(*) FROM paper_ledger WHERE "
                            "account_id=$1 AND kind = 'INITIAL_FUNDING'",
                 "accounts": "SELECT count(*) FROM paper_accounts "
                             "WHERE account_id=$1",
                 "sessions": "SELECT count(*) FROM paper_sessions "
                             "WHERE account_id=$1"}
            return {k: int(await conn.fetchval(s, acct["account_id"]))
                    for k, s in q.items()}
        ca, cb = await counts(a), await counts(b)
        assert ca["settlements"] == 6 and ca["fills"] == 6 and \
            ca["orders"] == 6 and ca["ledger_other"] >= 1, ca
        assert cb["settlements"] == 3, cb
        await T.purge(conn, [pa], accounts=[a])
        ca2, cb2 = await counts(a), await counts(b)
        assert ca2 == dict(ca, settlements=0, fills=0, orders=0,
                           ledger_other=0), ca2
        assert ca2["funding"] == 1 and ca2["accounts"] == 1 and \
            ca2["sessions"] == 1
        assert cb2 == cb, (cb, cb2)
        assert await conn.fetchval(
            "SELECT count(*) FROM external_valuations WHERE us_market_slug "
            "LIKE $1", pa + "%") == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM external_valuations WHERE us_market_slug "
            "LIKE $1", pb + "%") == 3
        # accounts may be given as ids too
        await T.purge(conn, [pb], accounts=[b["account_id"]])
        assert (await counts(b))["settlements"] == 0
    finally:
        await T.purge(conn, [pa, pb], accounts=[x for x in (a, b) if x])
        await conn.close()
