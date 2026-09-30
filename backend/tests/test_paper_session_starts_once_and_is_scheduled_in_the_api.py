"""THE PAPER SESSION STARTS EXACTLY ONCE, RESUMES ON RESTART, AND IS DRIVEN
FROM THE API PROCESS'S EXISTING SCHEDULE.

  * two concurrent ensure_session calls on a fresh account produce ONE active
    session and ONE INITIAL_FUNDING entry of exactly 500000.00;
  * a later call (a restart) resumes that session; its config and simulator
    version are frozen by the database;
  * the pass runs only when BOTH the PAPER_SESSION flag and the control row
    are on; with the flag on and the row off, the attempt is refused by
    name and still writes a database heartbeat; with the flag off nothing is
    scheduled at all;
  * the scheduling hook returns at once (a background task on its own pool
    connection) and never raises into the caller.
"""
from __future__ import annotations

import asyncio
import json

import asyncpg
import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets.agents import paper_runtime as PR
from sportsassets.agents import runtime as RT

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


@pg
async def test_two_concurrent_starts_make_one_session_and_one_funding():
    acct = H.new_account_id("once")
    c1, c2 = await H.connect(), await H.connect()
    try:
        r1, r2 = await asyncio.gather(
            S.ensure_session(c1, account_id=acct, now=H.T0),
            S.ensure_session(c2, account_id=acct, now=H.T0 + 0.5))
        assert r1["ok"] and r2["ok"]
        assert r1["session_id"] == r2["session_id"]
        n_sess = await c1.fetchval(
            "SELECT count(*) FROM paper_sessions WHERE account_id=$1", acct)
        fund = await c1.fetch(
            "SELECT cash_delta_usd FROM paper_ledger WHERE account_id=$1 "
            "   AND kind='INITIAL_FUNDING'", acct)
        assert n_sess == 1
        assert [str(r["cash_delta_usd"]) for r in fund] == ["500000.000000"]
        # A RESTART RESUMES; the frozen config cannot be changed.
        r3 = await S.ensure_session(c1, account_id=acct, now=H.T0 + 3600)
        assert r3["resumed"] is True and r3["session_id"] == r1["session_id"]
        with pytest.raises(asyncpg.RaiseError):
            await c1.execute(
                "UPDATE paper_sessions SET simulator_version='OTHER' "
                " WHERE session_id=$1", r1["session_id"])
        with pytest.raises(asyncpg.RaiseError):
            await c1.execute(
                "UPDATE paper_sessions SET config='{}'::jsonb "
                " WHERE session_id=$1", r1["session_id"])
    finally:
        await c1.close()
        await c2.close()


@pg
async def test_the_flag_and_the_control_row_both_gate_every_pass(monkeypatch):
    conn = await H.connect()
    try:
        monkeypatch.delenv(S.ENV_FLAG, raising=False)
        got = await PR.paper_pass(conn, now=H.T0)
        assert got["ran"] is False and got["why"] == S.R_ENV_OFF
        # flag on, row off -> refused by name
        monkeypatch.setenv(S.ENV_FLAG, "on")
        await conn.execute("UPDATE paper_control SET enabled=FALSE "
                           " WHERE control_key='PAPER_SESSION'")
        try:
            got = await PR.paper_pass(conn, now=H.T0)
            assert got["ran"] is False and got["why"] == S.R_CONTROL_OFF
        finally:
            await conn.execute("UPDATE paper_control SET enabled=TRUE "
                               " WHERE control_key='PAPER_SESSION'")
    finally:
        await conn.close()


@pg
async def test_the_hook_schedules_a_background_pass_that_writes_a_db_heartbeat(monkeypatch):
    pools = []

    async def get_pool():
        if not pools:
            pools.append(await asyncpg.create_pool(H.DSN, min_size=1,
                                                   max_size=2))
        return pools[0]

    conn = await H.connect()
    try:
        # FLAG OFF: nothing is scheduled, no task, no write
        monkeypatch.delenv(S.ENV_FLAG, raising=False)
        got = RT.paper_pass_hook(trigger="SERVICING_TASK", get_pool=get_pool)
        assert got == {"scheduled": False, "why": S.R_ENV_OFF}
        # FLAG ON, CONTROL ROW OFF: scheduled, refused by name, heartbeat
        monkeypatch.setenv(S.ENV_FLAG, "on")
        await conn.execute("UPDATE paper_control SET enabled=FALSE "
                           " WHERE control_key='PAPER_SESSION'")
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           PR.HEARTBEAT_KEY)
        try:
            got = RT.paper_pass_hook(trigger="SERVICING_TASK",
                                     get_pool=get_pool)
            assert got["scheduled"] is True
            # a second call while it runs is coalesced, never doubled
            again = RT.paper_pass_hook(trigger="COLLECTION_CYCLE",
                                       get_pool=get_pool)
            assert again["scheduled"] is False
            await PR._TASK["task"]
        finally:
            await conn.execute("UPDATE paper_control SET enabled=TRUE "
                               " WHERE control_key='PAPER_SESSION'")
        hb = await conn.fetchval("SELECT value FROM ingestion_state "
                                 " WHERE key=$1", PR.HEARTBEAT_KEY)
        hb = json.loads(hb) if isinstance(hb, str) else hb
        assert hb["ran"] is False and hb["why"] == S.R_CONTROL_OFF
        assert hb["trigger"] == "SERVICING_TASK"
    finally:
        await conn.close()
        for p in pools:
            await p.close()


@pg
async def test_the_account_payload_carries_the_session_brief():
    from sportsassets.api import command_paper as CP
    conn = await H.connect()
    try:
        body = await CP.account_payload(conn)
        s = body["session"]
        assert set(s) >= {"active", "reason", "session_id", "started_at",
                          "starting_cash_usd", "last_heartbeat_at",
                          "real_money_submission"}
        assert s["real_money_submission"] == "DISABLED"
        assert s["starting_cash_usd"] == 500000.0
        if not s["active"]:
            assert s["reason"]
        acct = body["account"]["data"]
        for k in ("cash_usd", "reserved_usd", "available_usd",
                  "open_position_value_usd", "total_equity_usd",
                  "realized_pnl_usd", "unrealized_pnl_usd",
                  "last_updated_at", "last_sequence", "stale_marks"):
            assert k in acct, k
        assert acct["account_id"] == L.ACCOUNT_ID
    finally:
        await conn.close()
