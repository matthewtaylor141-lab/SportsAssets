"""PROOF 10 -- A FAILED DECISION RECORD, A DUPLICATE CLAIM, A STALE PLAN AND
CHANGED INVENTORY EACH STOP THE SEND.

THROUGH THE REAL SCHEDULED ORCHESTRATION: every case drives
`ext_pinnacle_loop.cycle(conn)` (servicing -> `manage(defer_dispatch=True)` ->
`pass_once` -> Xavier's record -> claim -> revalidation -> send) with the
production suppliers, against a migrated database and a bound funded account
written through the real writers -- the harness of
test_xavier_manages_positions_through_the_scheduled_path.py (XH), whose
demonstration (a) sends exactly one acquisition on this fixture (the positive
control below re-proves it). Only the venue transport is substituted.

THE ASSERTION IN EVERY CASE: NO VENUE ORDER CALL HAPPENED (no preview, no
create, no cancel reached the substituted adapter). ALL DATA SYNTHETIC.
"""
from __future__ import annotations

import os
import time
import types

import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_xavier as XV

from tests import agents_core_harness as H
from tests import test_xavier_manages_positions_through_the_scheduled_path as XH

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


async def _scenario_a(conn, monkeypatch, *, qty=10, fill=None):
    """XH demonstration (a): 10 held at 0.50, HOLD on 0.55, the hedge wins and
    is dispatched. `fill` < `qty` leaves the entry order working."""
    await H.clean_agents(conn)
    await XH.clean(conn)
    await XH.authorize(conn)
    await XH.catalogue(conn)
    if fill is None:
        await XH.held_position(conn, qty=qty)
    else:
        got = await FB.record_intent(
            conn, intent_id=XH.HELD_ID, account_id=XH.ACCT, venue=XH.VENUE,
            venue_class=XH.FA.VENUE_FUNDED, us_market_slug=XH.HELD,
            event_key=XH.EVENT, order_intent=FX.LONG, limit_price=0.50,
            quantity=qty, collateral_usd=FX.collateral_for(0.50, qty, FX.LONG),
            effective_digest="d-xc", payout_event=XH.PAYS_ON,
            held_is_long=True, portfolio_group_id=None, leg_role="PRIMARY",
            group_structure="INDIRECT_MIDDLE")
        assert got.get("ok"), got
        await FB.record_acknowledgement(conn, XH.HELD_ID,
                                        venue_order_id="venue-entry-xc",
                                        status="open")
        await FB.ingest_fills(conn, XH.HELD_ID, [
            {"qty": float(fill), "price": 0.50,
             "venue_fill_id": "vf-entry-xc"}])
    from tests import approved_conditional_model as ACM
    await ACM.approve(conn)
    await XH.probability(conn, p=0.55)
    held = float(fill if fill is not None else qty)
    venue = XH.Venue(books=XH.books(held_bids=XH.PROFIT_LADDER,
                                    hedge_bid=0.55),
                     holdings={XH.HELD: (held, held * 0.50)})
    XH.substitute(monkeypatch, venue)
    return venue


async def _end(conn):
    await XH.clean(conn)
    await H.clean_agents(conn)
    await conn.close()


@pg
@pytest.mark.asyncio
async def test_control_the_unfaulted_scheduled_cycle_sends_the_one_acquisition(
        monkeypatch):
    conn = await XH._connect()
    try:
        venue = await _scenario_a(conn, monkeypatch)
        out = await XH.run_cycle(conn)
        assert XH.step_of(out)["decision"]["action"] == PC.ACTION_ACQUIRE
        assert len(venue.creates_sent()) == 1, venue.sent
        # Xavier's truthful state: it recorded a decision this pass.
        st = await H.status(conn, "XAVIER")
        assert st["state"] == "DECISION_RECORDED", st
    finally:
        await _end(conn)


@pg
@pytest.mark.asyncio
async def test_a_decision_record_that_does_not_persist_stops_the_send(
        monkeypatch):
    conn = await XH._connect()
    try:
        venue = await _scenario_a(conn, monkeypatch)

        async def _fails(*a, **k):
            raise ConnectionError("the decision write failed (test)")
        monkeypatch.setattr(XV, "record_decision", _fails)
        out = await XH.run_cycle(conn)
        assert out["funded_servicing"]["ok"] is True
        step = XH.step_of(out)
        assert step["decision"]["action"] == PC.ACTION_ACQUIRE, step
        assert step["refusal"] == XV.R_XAVIER_RECORD_NOT_PERSISTED, step
        assert H.order_calls(venue.sent) == [], venue.sent
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1 "
            " AND intent_id <> $2", XH.ACCT, XH.HELD_ID) == 0
        # AN EMPTY RECORD IS NOT MANAGEMENT: owned, and nothing recorded.
        st = await H.status(conn, "XAVIER")
        assert st["state"] == "WAITING_FOR_EVIDENCE", st
        assert st["activity"] == "OWNED_INVENTORY_NO_DECISION_RECORDED"
    finally:
        await _end(conn)


@pg
@pytest.mark.asyncio
async def test_a_duplicate_dispatch_claim_stops_the_send(monkeypatch):
    """The claim is taken by someone else first (a replay, a concurrent
    review): this pass's claim is refused by the database and nothing is
    reserved or sent."""
    conn = await XH._connect()
    try:
        venue = await _scenario_a(conn, monkeypatch)
        real = XV.claim_dispatch
        answers: list = []

        async def _someone_claimed_first(conn_, **kw):
            answers.append(await real(conn_, **kw))       # the other claimant
            answers.append(await real(conn_, **kw))       # this pass
            return answers[-1]
        monkeypatch.setattr(XV, "claim_dispatch", _someone_claimed_first)
        out = await XH.run_cycle(conn)
        step = XH.step_of(out)
        assert answers[0]["claimed"] is True
        assert answers[1]["claimed"] is False
        assert answers[1]["refusal"] == XV.R_ALREADY_CLAIMED
        assert step["refusal"] == XV.R_DISPATCH_NOT_CLAIMED, step
        assert H.order_calls(venue.sent) == [], venue.sent
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_leg_reservations WHERE "
            " group_id=$1", "grp:" + XH.HELD_ID) == 0
    finally:
        await _end(conn)


@pg
@pytest.mark.asyncio
async def test_a_plan_whose_inputs_expired_before_the_send_is_not_sent(
        monkeypatch):
    """The real clock at the send is past the plan's `inputs_expire_at`
    (the pass took too long): refused at the last point where nothing is
    written, and no venue call happens."""
    conn = await XH._connect()
    try:
        venue = await _scenario_a(conn, monkeypatch)
        late = time.time() + 86400.0
        monkeypatch.setattr(FX, "time", types.SimpleNamespace(
            time=lambda: late, monotonic=time.monotonic, sleep=time.sleep))
        out = await XH.run_cycle(conn)
        step = XH.step_of(out)
        assert step["decision"]["action"] == PC.ACTION_ACQUIRE, step
        assert step["acquisition"]["refusal"] == \
            FX.R_INPUTS_EXPIRED_AT_SEND, step["acquisition"]
        assert H.order_calls(venue.sent) == [], venue.sent
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1 "
            " AND leg_role='HEDGE'", XH.ACCT) == 0
    finally:
        await _end(conn)


@pg
@pytest.mark.asyncio
async def test_inventory_that_changes_after_the_decision_stops_the_send(
        monkeypatch):
    """The held entry is still working (10 of 12 filled). Xavier values the
    acquisition on 10; the last 2 fill -- ingested through the book (and so
    handed to Xavier) -- after the decision and before the send. The
    revalidation refuses the stale plan and no venue call happens."""
    conn = await XH._connect()
    try:
        venue = await _scenario_a(conn, monkeypatch, qty=12, fill=10)
        # THE VENUE'S OWN RECORD OF THE WORKING ENTRY, so recovery reads it
        # as working (10 of 12, nothing new) rather than unknown.
        venue.orders["venue-entry-xc"] = {"record": {
            "order": {"id": "venue-entry-xc", "marketSlug": XH.HELD,
                      "intent": FX.LONG,
                      "price": {"value": "0.50", "currency": "USD"},
                      "quantity": 12, "cumQuantity": 10,
                      "leavesQuantity": 2,
                      "state": "ORDER_STATE_PARTIALLY_FILLED"},
            "executions": [{"id": "vf-entry-xc",
                            "type": "EXECUTION_TYPE_FILL",
                            "lastPx": {"value": "0.50", "currency": "USD"},
                            "lastShares": 10}]}}
        real = XV.claim_dispatch
        injected: list = []

        async def _fill_then_claim(conn_, **kw):
            if not injected:
                injected.append(await FB.ingest_fills(conn_, XH.HELD_ID, [
                    {"qty": 2.0, "price": 0.50,
                     "venue_fill_id": "vf-entry-late"}]))
            return await real(conn_, **kw)
        monkeypatch.setattr(XV, "claim_dispatch", _fill_then_claim)
        out = await XH.run_cycle(conn)
        step = XH.step_of(out)
        assert injected, step
        assert step["refusal"] == XV.R_POSITION_CHANGED, step
        changed = {c["what"] for c in step["revalidation"]["changed"]}
        assert "residual:%s" % XH.HELD_ID in changed, changed
        assert H.order_calls(venue.sent) == [], venue.sent
        # THE CHANGED INVENTORY IS XAVIER'S, ON ONE HANDOFF ROW
        h = await conn.fetchrow(
            "SELECT confirmed_qty::float8 AS c, outstanding_qty::float8 AS o "
            "  FROM agent_position_handoffs WHERE entry_intent_id=$1",
            XH.HELD_ID)
        assert (h["c"], h["o"]) == (12.0, 0.0)
    finally:
        await _end(conn)
