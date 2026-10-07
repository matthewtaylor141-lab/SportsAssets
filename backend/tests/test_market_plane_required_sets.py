"""CAPITAL-CRITICAL (market data only): THE PRIORITY UNIVERSE'S HELD TIER IS
THE OPEN POSITIONS -- A SETTLED POSITION LEAVES IT, AND THE REQUIRED SETS
ARE RE-APPLIED EVERY PASS.

Production 2026-10-07: populate.HELD_SQL counted bought - sold and ignored
settlements, so 123 contracts were "held" for 30 open positions (101 with no
book for 6 h). The priority-universe freshness rate was computed over them.

  * HELD_SQL is the canonical open-position rule (bought - sold - settled);
  * apply_required promotes a newly held / evaluated market now and demotes
    one that is neither to its venue-activity priority now (not at the next
    full pass); nothing is demoted when a required-set read failed."""
from __future__ import annotations

import time
import uuid

import asyncpg
import pytest

from sportsassets.market_plane import populate as POP
from sportsassets.open_position_canon import CANONICAL_OPEN_POSITIONS_SQL

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


def test_held_is_the_canonical_open_position_rule():
    assert CANONICAL_OPEN_POSITIONS_SQL in POP.HELD_SQL
    assert "paper_settlements" in POP.HELD_SQL


async def _row(conn, slug, prio, start):
    await conn.execute(
        "INSERT INTO market_plane_registry (contract_id, venue, sport, "
        " family, period, active, desired_subscription, priority, "
        " required_reason, event_start, updated_at) VALUES ($1, "
        " 'POLYMARKET_US', 'football', 'WINNER', 'FULL_EVENT', true, true, "
        " $2, 'OPEN_PAPER_POSITION', to_timestamp($3), now())",
        slug, prio, start)


@pg
async def test_required_sets_are_reapplied_every_pass():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = time.time()
        tag = uuid.uuid4().hex[:8]
        settled, still, newly, cand = ("rq-%s-%s" % (k, tag) for k in (
            "settled", "still", "newly", "cand"))
        await _row(conn, settled, POP.P_HELD, now + 3600)     # soon, core
        await _row(conn, still, POP.P_HELD, now + 3600)
        await _row(conn, newly, POP.P_REST, now + 30 * 86400)
        await _row(conn, cand, POP.P_REST, now + 30 * 86400)
        # pass the sets as populate would; scope the demotion to these rows
        # by giving the rest of the table's priority members as required
        others = {r["contract_id"] for r in await conn.fetch(
            "SELECT contract_id FROM market_plane_registry WHERE priority "
            " <= $1 AND NOT (contract_id = ANY($2::text[]))",
            POP.P_CANDIDATE, [settled, still, newly, cand])}
        got = await POP.apply_required(conn, {still, newly} | others, {cand},
                                       at=now)
        pr = {r["contract_id"]: (r["priority"], r["required_reason"])
              for r in await conn.fetch(
                  "SELECT contract_id, priority, required_reason FROM "
                  " market_plane_registry WHERE contract_id = "
                  " ANY($1::text[])", [settled, still, newly, cand])}
        assert pr[still] == (POP.P_HELD, "OPEN_PAPER_POSITION")
        assert pr[newly] == (POP.P_HELD, "OPEN_PAPER_POSITION")
        assert pr[cand] == (POP.P_CANDIDATE, "EVALUATED_CANDIDATE")
        # the settled market leaves the priority tier now: a core full-event
        # market starting within 48 h -> P_CORE_SOON
        assert pr[settled] == (POP.P_CORE_SOON, "VENUE_ACTIVE")
        assert got["promoted"] >= 2 and got["demoted"] >= 1
        # a failed required-set read demotes nothing
        await conn.execute("UPDATE market_plane_registry SET priority = 0 "
                           " WHERE contract_id = $1", settled)
        got = await POP.apply_required(conn, set(), set(), at=now,
                                       both_read=False)
        assert got["demotion_skipped"] == "A_REQUIRED_SET_READ_FAILED"
        assert await conn.fetchval(
            "SELECT priority FROM market_plane_registry WHERE "
            " contract_id = $1", settled) == POP.P_HELD
    finally:
        await tx.rollback()
        await conn.close()


def test_this_proof_is_capital_critical():
    from pathlib import Path
    listed = (Path(__file__).resolve().parents[1] / "tools" /
              "capital_critical_tests.txt").read_text().splitlines()
    assert "tests/test_market_plane_required_sets.py" in listed
