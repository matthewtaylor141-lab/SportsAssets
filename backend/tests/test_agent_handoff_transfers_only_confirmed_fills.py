"""PROOF 3 -- THE DEREK -> XAVIER HANDOFF TRANSFERS ONLY CONFIRMED QUANTITY.

Every fill here enters through the production book writer
(`bettor_funded_book.ingest_fills`), which calls `agents.handoff.on_fills`
after its fills transaction commits. The management path is the real review
path (`bettor_funded_pair_cycle.pass_once` with Xavier's record, claim and
revalidation), with the venue substituted at `pmus._get_client` only (the
harness of test_xavier_decides_each_group_once.py). ALL DATA SYNTHETIC.

  * a 30-of-100 partial fill gives Xavier 30 confirmed and 70 outstanding;
    later fills update the SAME row; there is never a second owner;
  * restart replay (on_fills / reconcile_unowned repeatedly, a redelivered
    fill, a hook that never ran or raised) cannot duplicate ownership, and a
    handoff failure never fails fill ingestion;
  * two concurrent consumers on separate connections produce one set of rows;
  * a rejected, unfilled, merely acknowledged or merely recorded order, an
    exit, and a Xavier-acquired leg create no handoff;
  * a fill arriving during a management decision invalidates the stale plan
    through the real revalidation, nothing is sent on it, and the fill lands
    on the same handoff row.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_xavier as XV
from sportsassets.agents import handoff as AH

from tests import agents_core_harness as H
from tests import test_the_scheduled_pair_lifecycle as SPL
from tests import test_xavier_manages_every_position_through_the_scheduled_pass as XM

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

T0 = 1_790_200_000.0
IID = "ag3-entry"


async def _handoff(conn, iid=IID):
    r = await conn.fetchrow(
        "SELECT * FROM agent_position_handoffs WHERE entry_intent_id=$1", iid)
    return None if r is None else {k: (float(v) if hasattr(v, "as_tuple")
                                       else v) for k, v in dict(r).items()}


async def _counts(conn):
    return (await conn.fetchval("SELECT count(*) FROM agent_position_handoffs"),
            await conn.fetchval("SELECT count(*) FROM agent_handoff_fills"))


async def _recorded(conn, iid=IID, qty=100):
    """An entry intent recorded through the book's writer (nothing sent)."""
    got = await FB.record_intent(
        conn, intent_id=iid, account_id=SPL.ACCT, venue=SPL.VENUE,
        venue_class="FUNDED", us_market_slug=SPL.SLUG_PRIMARY,
        event_key=SPL.EVENT, order_intent=FX.LONG, limit_price=SPL.PRIMARY_PX,
        quantity=qty, collateral_usd=FX.collateral_for(SPL.PRIMARY_PX, qty,
                                                       FX.LONG),
        effective_digest="d", payout_event=SPL.PAYS_ON, held_is_long=True,
        portfolio_group_id=None, leg_role="PRIMARY",
        group_structure="INDIRECT_MIDDLE")
    assert got.get("ok"), got
    return got


def _fill(q, vid, px=None):
    return {"qty": float(q), "price": px or SPL.PRIMARY_PX,
            "venue_fill_id": vid}


async def _start(conn):
    await XM.spl_clean(conn)
    await H.clean_agents(conn)
    await SPL._seed(conn)


async def _end(conn):
    await XM.spl_clean(conn)
    await H.clean_agents(conn)


# ════════════════════════════════════════════════════════════════════
# PARTIAL FILLS: CONFIRMED QUANTITY ONLY, ONE ROW, LATER FILLS UPDATE IT
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_partial_fill_transfers_only_confirmed_quantity_and_later_fills_update_the_same_owner():
    conn = await XM._connect()
    try:
        await _start(conn)
        await _recorded(conn)
        # RECORDED AND ACKNOWLEDGED: nothing is held, nobody owns anything.
        assert await _handoff(conn) is None
        await FB.record_acknowledgement(conn, IID, venue_order_id="vo-ag3",
                                        status="open")
        assert await _handoff(conn) is None
        assert (await AH.on_fills(conn, intent_id=IID, now=T0))[
            "skipped"] == AH.S_NO_CONFIRMED_FILL
        assert await _handoff(conn) is None

        # 30 OF 100 FILL: Xavier owns 30, 70 are the entry's open obligation.
        await FB.ingest_fills(conn, IID, [_fill(30, "vf-a")], at=T0)
        h = await _handoff(conn)
        assert (h["ordered_qty"], h["confirmed_qty"], h["outstanding_qty"]) \
            == (100.0, 30.0, 70.0)
        assert h["from_agent"] == "DEREK" and h["owner_agent"] == "XAVIER"
        assert h["first_fill_id"] == h["last_fill_id"] == "fvf:vo-ag3:vf-a"
        assert h["portfolio_group_id"] == "grp:" + IID
        first_at = h["handoff_at"]

        # 50 MORE, THEN THE LAST 20: the SAME row, never a second owner.
        await FB.ingest_fills(conn, IID, [_fill(50, "vf-b")], at=T0 + 10)
        h = await _handoff(conn)
        assert (h["confirmed_qty"], h["outstanding_qty"]) == (80.0, 20.0)
        assert h["last_fill_id"] == "fvf:vo-ag3:vf-b"
        assert h["handoff_at"] == first_at, "ownership began at the first fill"
        await FB.ingest_fills(conn, IID, [_fill(20, "vf-c")], at=T0 + 20)
        h = await _handoff(conn)
        assert await conn.fetchval(
            "SELECT state FROM bettor_funded_intents WHERE intent_id=$1",
            IID) == "FILLED"
        assert (h["confirmed_qty"], h["outstanding_qty"]) == (100.0, 0.0)
        assert await _counts(conn) == (1, 3)
        # THE MIRROR EQUALS THE LEDGER, fill for fill
        assert sorted(r["fill_id"] for r in await conn.fetch(
            "SELECT fill_id FROM agent_handoff_fills")) == sorted(
            r["fill_id"] for r in await conn.fetch(
                "SELECT fill_id FROM bettor_funded_fills WHERE intent_id=$1",
                IID))
        # THE READER SHOWS IT WITH ITS EVIDENCE
        rows = await AH.handoffs(conn, entry_intent_id=IID)
        assert len(rows) == 1 and len(rows[0]["fills"]) == 3
        assert rows[0]["position_open"] is True
        assert {e["kind"] for e in rows[0]["evidence"]} == {
            "bettor_funded_intents", "bettor_funded_fills"}
    finally:
        await _end(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_partial_entry_the_venue_cancels_stops_counting_outstanding_on_the_next_pass():
    conn = await XM._connect()
    try:
        await _start(conn)
        await XM._entry(conn, intent_id=IID, qty=100, filled=30)
        assert (await _handoff(conn))["outstanding_qty"] == 70.0
        # THE VENUE ENDS THE ORDER AFTER 30 (its own record, balanced)
        got = await FB.record_venue_terminal(
            conn, IID, venue_state="canceled", venue_filled=30.0, leaves=0.0,
            ledger_filled=30.0)
        assert got["applied"] is True, got
        rep = await AH.reconcile_unowned(conn, now=T0)
        assert rep["refreshed"] == 1 and rep["created"] == 0, rep
        h = await _handoff(conn)
        assert (h["confirmed_qty"], h["outstanding_qty"]) == (30.0, 0.0)
        assert (await AH.reconcile_unowned(conn, now=T0 + 1))["examined"] == 0
    finally:
        await _end(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# RESTART REPLAY CANNOT DUPLICATE OWNERSHIP; A HOOK FAILURE FAILS NOTHING
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_restart_replay_and_redelivery_cannot_duplicate_ownership():
    conn = await XM._connect()
    try:
        await _start(conn)
        await XM._entry(conn, intent_id=IID, qty=100, filled=30)
        before = await _handoff(conn)
        # A REDELIVERY OF THE SAME EXECUTION, replays of the hook, and the
        # servicing pass's repair, again and again -- across a "restart"
        # (a new connection).
        for _ in range(3):
            await FB.ingest_fills(conn, IID, [_fill(30, "vf-" + IID)],
                                  at=T0 + 5)
            await AH.on_fills(conn, intent_id=IID, now=T0 + 5)
            assert (await AH.reconcile_unowned(conn, now=T0 + 5))[
                "examined"] == 0
        await conn.close()
        conn = await XM._connect()
        for _ in range(2):
            got = await AH.on_fills(conn, intent_id=IID, now=T0 + 6)
            assert got["created"] is False and got["fills_recorded"] == 0
        assert await _counts(conn) == (1, 1)
        after = await _handoff(conn)
        assert after["confirmed_qty"] == before["confirmed_qty"] == 30.0
        assert after["handoff_at"] == before["handoff_at"]

        # THE HOOK NEVER RAN (a crash between the fills commit and the hook):
        # the servicing pass's repair gives the inventory exactly one owner.
        await H.clean_agents(conn)
        rep = await AH.reconcile_unowned(conn, now=T0 + 7)
        assert rep["created"] == 1 and rep["repaired"] == [IID], rep
        assert rep["owned_open_positions"] == 1
        assert (await AH.reconcile_unowned(conn, now=T0 + 8))["examined"] == 0
        assert await _counts(conn) == (1, 1)
    finally:
        await _end(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_handoff_failure_never_fails_or_rolls_back_fill_ingestion(
        monkeypatch):
    conn = await XM._connect()
    try:
        await _start(conn)
        await _recorded(conn)
        await FB.record_acknowledgement(conn, IID, venue_order_id="vo-ag3",
                                        status="open")

        async def _raises(conn_, **kw):
            await conn_.execute("SELECT 1/0")        # a real SQL error
        monkeypatch.setattr(AH, "on_fills", _raises)
        got = await FB.ingest_fills(conn, IID, [_fill(30, "vf-a")], at=T0)
        assert got["ok"] is True and len(got["written"]) == 1, got
        # INSIDE A CALLER'S TRANSACTION TOO: the failed hook's savepoint
        # rolls back alone and the caller's transaction commits the fill.
        async with conn.transaction():
            got = await FB.ingest_fills(conn, IID, [_fill(20, "vf-b")],
                                        at=T0 + 1)
            assert got["ok"] is True and got["filled_qty_from_the_ledger"] \
                == 50.0
        assert await conn.fetchval(
            "SELECT coalesce(sum(qty),0)::float8 FROM bettor_funded_fills "
            " WHERE intent_id=$1", IID) == 50.0
        assert await conn.fetchval(
            "SELECT state FROM bettor_funded_intents WHERE intent_id=$1",
            IID) == "PARTIALLY_FILLED"
        assert await _handoff(conn) is None
        monkeypatch.undo()
        # THE NEXT SERVICING PASS REPAIRS IT, from the ledger
        rep = await AH.reconcile_unowned(conn, now=T0 + 2)
        assert rep["created"] == 1, rep
        h = await _handoff(conn)
        assert (h["confirmed_qty"], h["outstanding_qty"]) == (50.0, 50.0)
    finally:
        await _end(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# CONCURRENT CONSUMERS: EXACTLY ONE SET OF ROWS
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_concurrent_consumers_on_separate_connections_produce_one_set_of_rows():
    a, b = await XM._connect(), await XM._connect()
    try:
        await _start(a)
        await _recorded(a)
        await FB.record_acknowledgement(a, IID, venue_order_id="vo-ag3",
                                        status="open")
        # THE LEDGER GETS TWO FILLS WITH THE HOOK SUPPRESSED, so the only
        # writers of the handoff are the two concurrent consumers below.
        await a.execute(
            "INSERT INTO bettor_funded_fills (fill_id, intent_id, "
            " venue_order_id, venue_fill_id, at, qty, price, cash_usd, "
            " fee_usd, fee_basis, direction) VALUES "
            " ('fvf:vo-ag3:c1',$1,'vo-ag3','c1',to_timestamp($2),30,0.62,"
            "  18.6,0,'TEST','ENTRY'),"
            " ('fvf:vo-ag3:c2',$1,'vo-ag3','c2',to_timestamp($2+1),20,0.62,"
            "  12.4,0,'TEST','ENTRY')", IID, T0)
        for _ in range(5):
            got = await asyncio.gather(AH.on_fills(a, intent_id=IID, now=T0),
                                       AH.on_fills(b, intent_id=IID, now=T0))
            assert sum(1 for g in got if g.get("created")) <= 1
        assert await _counts(a) == (1, 2)
        h = await _handoff(a)
        assert (h["confirmed_qty"], h["outstanding_qty"]) == (50.0, 50.0)
        # AND THE TWO SERVICING-SIDE REPAIRS AT ONCE, from nothing
        await H.clean_agents(a)
        r1, r2 = await asyncio.gather(AH.reconcile_unowned(a, now=T0),
                                      AH.reconcile_unowned(b, now=T0))
        assert r1["created"] + r2["created"] == 1, (r1, r2)
        assert await _counts(a) == (1, 2)
    finally:
        await _end(a)
        await a.close()
        await b.close()


# ════════════════════════════════════════════════════════════════════
# NO FILL, NO INVENTORY; EXITS AND XAVIER'S OWN LEGS TRANSFER NOTHING
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_rejected_or_unfilled_order_creates_no_inventory_and_no_owner():
    conn = await XM._connect()
    try:
        await _start(conn)
        # REFUSED BEFORE ANYTHING LEFT
        await _recorded(conn, "ag3-abandoned", qty=10)
        await FB.abandon_before_send(conn, "ag3-abandoned", "TEST_REFUSAL")
        # SENT, AND THE VENUE REJECTED / CANCELLED IT WITH NOTHING FILLED
        await _recorded(conn, "ag3-rejected", qty=10)
        await FB.record_acknowledgement(conn, "ag3-rejected",
                                        venue_order_id="vo-rej",
                                        status="open")
        got = await FB.record_venue_terminal(
            conn, "ag3-rejected", venue_state="rejected", venue_filled=0.0,
            leaves=0.0, ledger_filled=0.0)
        assert got["applied"] is True, got
        # AN EMPTY DELIVERY and an execution the venue did not name
        await FB.ingest_fills(conn, "ag3-rejected", [], at=T0)
        await FB.ingest_fills(conn, "ag3-rejected",
                              [{"qty": 5.0, "price": 0.6}], at=T0)
        for iid in ("ag3-abandoned", "ag3-rejected"):
            got = await AH.on_fills(conn, intent_id=iid, now=T0)
            assert got["handoff"] is None, got
        rep = await AH.reconcile_unowned(conn, now=T0)
        assert rep["examined"] == 0 and rep["repaired"] == [], rep
        assert await _counts(conn) == (0, 0)
        assert (await AH.on_fills(conn, intent_id="nope", now=T0))[
            "refusal"] == AH.R_NO_SUCH_INTENT
    finally:
        await _end(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_exit_fills_and_a_leg_xavier_acquired_transfer_nothing():
    conn = await XM._connect()
    try:
        await _start(conn)
        await XM._entry(conn, intent_id=IID, qty=10, filled=10)
        assert (await _handoff(conn))["confirmed_qty"] == 10.0
        # AN EXIT OF IT, FILLED: management's own order, not a new owner
        await conn.execute(
            "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
            " venue_class, us_market_slug, event_key, order_intent, "
            " limit_price, quantity, collateral_usd, effective_digest, state,"
            " kind, parent_intent_id, venue_order_id) VALUES ('ag3-exit',$1,"
            " $2,'FUNDED',$3,$4,$5,0.7,4,0,'d','ACKNOWLEDGED','EXIT',$6,"
            " 'vo-ag3-exit')", SPL.ACCT, SPL.VENUE, SPL.SLUG_PRIMARY,
            SPL.EVENT, FX.LONG, IID)
        await FB.ingest_fills(conn, "ag3-exit", [_fill(4, "vf-x", 0.7)],
                              direction="EXIT", at=T0)
        assert (await AH.on_fills(conn, intent_id="ag3-exit", now=T0))[
            "skipped"] == AH.S_NOT_AN_ENTRY
        h = await _handoff(conn)
        assert h["confirmed_qty"] == 10.0, "an exit does not change ownership"
        assert await _counts(conn) == (1, 1)
        # A HEDGE LEG XAVIER ACQUIRED through its claim path names its
        # decision on the intent: it is already Xavier's.
        await conn.execute(
            "UPDATE bettor_funded_intents SET decision_ref = decision_ref || "
            " '{\"xavier_decision_id\": \"xd-1\"}'::jsonb WHERE intent_id=$1",
            IID)
        assert (await AH.on_fills(conn, intent_id=IID, now=T0))[
            "skipped"] == AH.S_XAVIER_ORIGINATED
    finally:
        await _end(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# A FILL DURING A MANAGEMENT DECISION INVALIDATES THE STALE PLAN
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_fill_during_a_management_decision_invalidates_the_stale_plan_and_lands_on_the_same_owner(
        monkeypatch):
    """The entry is working: 6 of 10 filled, Xavier owns 6. Xavier reviews it
    and chooses an EXIT; a further fill of 2 is ingested (through the book,
    so through the handoff hook) at the claim -- after the decision was
    valued, before the send. The real revalidation refuses the stale plan:
    no venue order call happens, the claim is spent NOT_SENT, and the fill
    lands on the SAME handoff row (8 confirmed, 2 outstanding)."""
    conn = await XM._connect()
    try:
        await _start(conn)
        sent, client = XM.venue(monkeypatch, order_id="venue-exit")
        await XM._entry(conn, intent_id=IID, qty=10, filled=6)
        h = await _handoff(conn)
        assert (h["confirmed_qty"], h["outstanding_qty"]) == (6.0, 4.0)
        real_claim = XV.claim_dispatch
        injected: list = []

        async def _fill_arrives_then_claim(conn_, **kw):
            if not injected:
                injected.append(await FB.ingest_fills(
                    conn_, IID, [_fill(2, "vf-late")], at=T0 + 30))
            return await real_claim(conn_, **kw)

        monkeypatch.setattr(XV, "claim_dispatch", _fill_arrives_then_claim)
        got = await PC.pass_once(conn, account_id=SPL.ACCT, venue=SPL.VENUE,
                                 pair_inputs=XM.exit_supplier(),
                                 venue_positions=SPL.EMPTY_VENUE)
        s = next(x for x in got["considered"] if x["intent_id"] == IID)
        assert injected and s["decision"]["action"] == "EXIT", s
        assert s["refusal"] == XV.R_POSITION_CHANGED, s
        assert "intent_filled" in {c["what"] for c in
                                   s["revalidation"]["changed"]}
        # NO VENUE ORDER CALL HAPPENED, AND NO EXIT INTENT EXISTS
        assert H.order_calls(sent) == [], sent
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE kind='EXIT' "
            " AND parent_intent_id=$1", IID) == 0
        rec = (await XM.records(conn, IID))[0]
        assert (await XV.execution_state(
            conn, xavier_decision_id=rec["xavier_decision_id"]))[
            "status"] == XV.X_NOT_SENT
        # THE LATE FILL IS XAVIER'S, ON THE SAME ROW
        h = await _handoff(conn)
        assert (h["confirmed_qty"], h["outstanding_qty"]) == (8.0, 2.0)
        assert await _counts(conn) == (1, 2)
    finally:
        await _end(conn)
        await conn.close()
