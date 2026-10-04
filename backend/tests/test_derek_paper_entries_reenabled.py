"""MIGRATION 264: DEREK'S PAPER ENTRIES RE-ENABLED (PAPER ONLY), ON POSTGRES.

Owner decision 2026-10-04: PAPER_ENTRIES for DEREK_ENTRY_POLICY_V2 on, for
PAPER execution only, with migration 182's stale reason corrected; no
threshold, limit or live permission changes. Everything runs inside one
transaction that is rolled back, so the test database is left as found:

  * 264 flips the row only while it still carries migration 182's state,
    with a reason that names the owner decision and no longer names the
    disabled benchmark as the only entrant;
  * a later decision (anyone else's update) is never overridden; an absent
    row is inserted ON; 264 is idempotent;
  * the rollback turns it off with a true reason and only touches 264's row;
  * nothing else in paper_control changes, and the migration text touches
    no threshold / limit / live control;
  * paper_derek.entries_switch reads the row as enabled.
"""
from __future__ import annotations

import pathlib
import re

import asyncpg
import pytest

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP = (MIG / "264_derek_paper_entries_reenabled.sql").read_text()
DOWN = (MIG / "rollback" / "264_derek_paper_entries_reenabled.down.sql").read_text()
KEY = "PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2"
STALE = ("owner: only the PINNACLE_ONLY_PAPER_BENCHMARK may open new paper "
         "entries; the two-model strategy keeps recording its decisions")


def _code(sql: str) -> str:
    return "\n".join(l.split("--")[0] for l in sql.splitlines())


def test_the_migration_writes_one_control_row_and_nothing_else():
    code = _code(UP) + _code(DOWN)
    # only paper_control, only this key
    assert set(re.findall(r"(?:UPDATE|INSERT INTO|DELETE FROM|ALTER TABLE|DROP)\s+(\w+)", code, re.I)) == {"paper_control"}
    assert code.count(KEY) == 3
    # outside the quoted reason text, the SQL names no threshold, limit or live control
    bare = re.sub(r"'(?:[^']|'')*'", "''", code).lower()
    for word in ("threshold", "min_gross", "min_net", "limit", "cap_usd", "small_live", "funded", "live_mode", "sleeve"):
        assert word not in bare, word
    assert set(re.findall(r"set\s+(\w+)\s*=", bare)) <= {"enabled"}


async def _rows(conn):
    return {r["control_key"]: dict(r) for r in await conn.fetch(
        "SELECT control_key, enabled, why, updated_by FROM paper_control")}


@pg
@pytest.mark.asyncio
async def test_264_flips_only_migration_182s_row_and_rolls_back_honestly():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("DELETE FROM paper_control WHERE control_key = $1", KEY)
        await conn.execute(
            "INSERT INTO paper_control (control_key, enabled, why, updated_by) "
            "VALUES ($1, FALSE, $2, 'migration 182')", KEY, STALE)
        before = await _rows(conn)
        await conn.execute(UP)
        await conn.execute(UP)                                  # idempotent
        after = await _rows(conn)
        row = after[KEY]
        assert row["enabled"] is True and row["updated_by"] == "migration 264"
        assert "owner decision 2026-10-04" in row["why"] and "PAPER execution only" in row["why"]
        assert "only the PINNACLE_ONLY_PAPER_BENCHMARK may open" not in row["why"]
        assert {k: v for k, v in after.items() if k != KEY} == {k: v for k, v in before.items() if k != KEY}

        from sportsassets.agents import paper_derek as PD
        sw = await PD.entries_switch(conn)
        assert sw["enabled"] is True and sw["control_key"] == KEY

        await conn.execute(DOWN)
        row = (await _rows(conn))[KEY]
        assert row["enabled"] is False and row["updated_by"] == "rollback 264"
        assert "only the PINNACLE_ONLY_PAPER_BENCHMARK" not in row["why"]
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_264_never_overrides_a_later_decision_and_inserts_an_absent_row():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("DELETE FROM paper_control WHERE control_key = $1", KEY)
        await conn.execute(
            "INSERT INTO paper_control (control_key, enabled, why, updated_by) "
            "VALUES ($1, FALSE, 'a later operator decision', 'operator')", KEY)
        await conn.execute(UP)
        row = (await _rows(conn))[KEY]
        assert row["enabled"] is False and row["updated_by"] == "operator"
        await conn.execute(DOWN)                                # not 264's row: untouched
        assert (await _rows(conn))[KEY]["updated_by"] == "operator"

        await conn.execute("DELETE FROM paper_control WHERE control_key = $1", KEY)
        await conn.execute(UP)
        row = (await _rows(conn))[KEY]
        assert row["enabled"] is True and row["updated_by"] == "migration 264"
    finally:
        await tx.rollback()
        await conn.close()
