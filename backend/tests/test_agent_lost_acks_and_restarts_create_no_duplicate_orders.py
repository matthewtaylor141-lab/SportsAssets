"""PROOF 11 -- LOST ACKNOWLEDGEMENTS AND RESTART RECOVERY CREATE NO DUPLICATE
ORDERS, AND AN EMPTY OPEN-ORDERS RESPONSE DOES NOT RESOLVE AN UNRESOLVED SEND.

Through the real scheduled orchestration: `cycle()` and the servicing task's
own pass (`_service_once(source=SERVICING_TASK)`), production suppliers,
venue transport substituted (XH harness). The substituted venue's
`orders.list` ALWAYS answers an empty list -- the answer that must not be
read as "nothing is working". A restart is a closed connection, every
process-local cache dropped, a fresh servicing state and a fresh execution
lock. ALL DATA SYNTHETIC.
"""
from __future__ import annotations

import os

import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_xavier as XV
from sportsassets.workers import ext_pinnacle_loop as L

from tests import agents_core_harness as H
from tests import test_xavier_manages_positions_through_the_scheduled_path as XH

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


def _restart(monkeypatch):
    XH.reset_process_state()
    monkeypatch.setattr(L, "_SERVICING", L._servicing_state())
    monkeypatch.setattr(L, "_EXEC_LOCK", {"lock": None, "loop": None})


async def _task_pass(conn):
    import time
    return await L._service_once(conn, now=time.time(),
                                 source=L.SOURCE_SERVICING_TASK)


@pg
@pytest.mark.asyncio
async def test_a_lost_hedge_answer_survives_restarts_and_servicing_passes_without_a_second_order(
        monkeypatch):
    conn = await XH._connect()
    try:
        await H.clean_agents(conn)
        await XH.start(conn, p=0.55)
        venue = XH.Venue(books=XH.books(held_bids=XH.PROFIT_LADDER,
                                        hedge_bid=0.55),
                         holdings={XH.HELD: (10.0, 5.0)})
        venue.raise_on_create = TimeoutError("the answer never came back")
        XH.substitute(monkeypatch, venue)
        one = await XH.run_cycle(conn)
        s1 = XH.step_of(one)
        assert s1["acquisition"]["refusal"] == FX.R_LOST_ACKNOWLEDGEMENT
        assert len(venue.creates_sent()) == 1
        hedge = await conn.fetchrow(
            "SELECT intent_id, state FROM bettor_funded_intents WHERE "
            " account_id=$1 AND leg_role='HEDGE'", XH.ACCT)
        assert hedge["state"] == "UNRESOLVED"
        # THE LOST ANSWER IS NO OWNERSHIP: nothing filled, no handoff row;
        # and Xavier is RECOVERING, naming what it owes.
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_position_handoffs WHERE "
            " entry_intent_id=$1", hedge["intent_id"]) == 0
        st = await H.status(conn, "XAVIER")
        assert st["state"] == "RECOVERING", st

        # ── RESTART, THEN THE SERVICING TASK, THEN THE CYCLE, TWICE ──
        venue.raise_on_create = None          # the venue answers again
        for _ in range(2):
            await conn.close()
            _restart(monkeypatch)
            conn = await XH._connect()
            res = await _task_pass(conn)
            assert res["ran"] is True
            assert res["funded_service"]["pair_cycle"][
                "resubmitted_anything"] is False
            two = await XH.run_cycle(conn)
            assert two["funded_servicing"]["pair_cycle"][
                "resubmitted_anything"] is False
        # NOTHING RESENT, BY ANY PATH
        assert len(venue.creates_sent()) == 1, venue.sent
        # THE EMPTY OPEN-ORDERS ANSWER WAS READ, AND RESOLVED NOTHING
        assert any(k == "orders.list" for k, _ in venue.sent), venue.sent
        assert await conn.fetchval(
            "SELECT state FROM bettor_funded_intents WHERE intent_id=$1",
            hedge["intent_id"]) == "UNRESOLVED"
        exp = await FB.exposure(conn, account_id=XH.ACCT, venue=XH.VENUE)
        assert any(r["us_market_slug"] == XH.SIB
                   for r in exp["outstanding_orders"]), exp
        rec = (await XH.xavier_records(conn))[0]
        assert rec["responsibility_state"] == XV.ORDER_UNRESOLVED
        inv = await conn.fetchrow(
            "SELECT state FROM bettor_funded_investigations WHERE "
            " intent_id=$1", hedge["intent_id"])
        assert inv is not None and inv["state"] == "OPEN"
        st = await H.status(conn, "XAVIER")
        assert st["state"] == "RECOVERING", st
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1"
            " AND leg_role='HEDGE'", XH.ACCT) == 1
    finally:
        await XH.clean(conn)
        await H.clean_agents(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_lost_entry_answer_is_never_resolved_by_an_empty_order_list_or_resent(
        monkeypatch):
    """Derek's own entry: committed, sent, and the answer lost. The venue's
    open-orders list is empty on every pass. The intent stays UNRESOLVED
    with its exposure counted, no order is sent by any pass or restart, no
    ownership transfers (nothing filled), and Xavier is RECOVERING."""
    conn = await XH._connect()
    try:
        await H.clean_agents(conn)
        await XH.clean(conn)
        await XH.authorize(conn)
        await XH.catalogue(conn)
        got = await FB.record_intent(
            conn, intent_id=XH.HELD_ID, account_id=XH.ACCT, venue=XH.VENUE,
            venue_class=XH.FA.VENUE_FUNDED, us_market_slug=XH.HELD,
            event_key=XH.EVENT, order_intent=FX.LONG, limit_price=0.50,
            quantity=10, collateral_usd=FX.collateral_for(0.50, 10, FX.LONG),
            effective_digest="d-xc", payout_event=XH.PAYS_ON,
            held_is_long=True, portfolio_group_id=None, leg_role="PRIMARY",
            group_structure="INDIRECT_MIDDLE")
        assert got.get("ok"), got
        await FB.mark_send_attempted(conn, XH.HELD_ID)
        await FB.mark_unresolved(conn, XH.HELD_ID,
                                 reason="the answer never came back")
        venue = XH.Venue(books=XH.books(held_bids=XH.PROFIT_LADDER),
                         holdings={})
        XH.substitute(monkeypatch, venue)
        for _ in range(2):
            await XH.run_cycle(conn)
            await _task_pass(conn)
            await conn.close()
            _restart(monkeypatch)
            conn = await XH._connect()
        await _task_pass(conn)
        assert H.order_calls(venue.sent) == [], venue.sent
        assert any(k == "orders.list" for k, _ in venue.sent), venue.sent
        row = await conn.fetchrow(
            "SELECT state, residual_qty::float8 AS r FROM "
            " bettor_funded_intents WHERE intent_id=$1", XH.HELD_ID)
        assert row["state"] == "UNRESOLVED", dict(row)
        exp = await FB.exposure(conn, account_id=XH.ACCT, venue=XH.VENUE)
        assert any(r["intent_id"] == XH.HELD_ID
                   for r in exp["outstanding_orders"]), exp
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_position_handoffs") == 0
        st = await H.status(conn, "XAVIER")
        assert st["state"] == "RECOVERING", st
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1",
            XH.ACCT) == 1
    finally:
        await XH.clean(conn)
        await H.clean_agents(conn)
        await conn.close()
