"""THE POSITION ROOM ENDPOINTS ON A REAL DATABASE. Every row is SYNTHETIC TEST
DATA written into a scratch account of the test database (tests/paper_harness
rules: nothing touches the live paper account).

The paper rows are produced by the paper ledger and simulator themselves
(submit, simulate, cancel), so the states the room shows are the states the
real state machine wrote. The route functions run inside their own READ ONLY
transaction with the statement timeout, exactly as served.
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException, Response

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import position_rooms as PR
from sportsassets.api import command_positions as CP
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
pytestmark = pytest.mark.asyncio


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


async def _premap(conn, slug, event, now):
    for side, team, abbr, tid, safe in (
            ("LONG", "boston red sox", "bos", 111, "red sox"),
            ("SHORT", "new york yankees", "nyy", 147, "yankees")):
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, event_title, "
            " market_slug, question, kind, line, side_norm, intent, "
            " team_abbr, team_name, team_safe_name, team_id, team_league, "
            " game_start, sports_type) VALUES ($1,$2,$3,$4,$5,'side','',$6,"
            " $7,$8,$9,$10,$11,'mlb',to_timestamp($12),"
            " 'baseball_team_full_game_winner')",
            "test-%s-%s" % (slug, side), event, "BOS Red Sox vs. NY Yankees",
            slug, "TEST FIXTURE", team, "ORDER_INTENT_BUY_%s" % side, abbr,
            team, safe, tid, now)


async def _seed(conn):
    tag = uuid.uuid4().hex[:8]
    slug = "aec-mlb-bos-nyy-test%s" % tag
    event = "mlb-bos-nyy-test%s" % tag
    now = time.time()
    await _premap(conn, slug, event, now)
    a = await H.new_account(conn, "rooms")
    g1, g2 = "paper_g_rooms_nyy_%s" % tag, "paper_g_rooms_bos_%s" % tag
    fee = H.flat_fee(0.0035)
    T = H.T0
    # ENTRY: YES Yankees (the contract's SHORT) 1000 @ 50c
    e = H.order(a, key="e", slug=slug, holding_side="SHORT", qty=1000,
                limit=0.50, group_id=g1, at=T)
    ge = await L.submit_order(conn, e, fee_fn=fee, now=T)
    await H.observe(conn, slug, T + 3, bids=[(0.50, 2000)],
                    offers=[(0.52, 2000)])
    r = await SIM.simulate_order(conn, ge["order"]["order_id"], now=T + 4,
                                 fee_fn=fee)
    assert r["state"] == "FILLED", r
    # STANDING PROTECTION: sell the Yankees at 70c, resting
    p = H.order(a, key="p", slug=slug, holding_side="SHORT", qty=1000,
                limit=0.70, direction="SELL", role="STANDING_PROTECTION",
                group_id=g1, order_type="RESTING", tif="GTD", delay=0.0,
                ttl=10 ** 8, at=T + 10, queue_ahead=0.0)
    gp = await L.submit_order(conn, p, fee_fn=fee, now=T + 10)
    assert gp["ok"], gp
    r = await SIM.simulate_order(conn, gp["order"]["order_id"], now=T + 11,
                                 fee_fn=fee)
    assert r["state"] == "RESTING", r
    # HEDGE: YES Red Sox (LONG) 400 @ 35c, partially filled (150)
    h = H.order(a, key="h", slug=slug, holding_side="LONG", qty=400,
                limit=0.35, role="HEDGE", group_id=g2, order_type="RESTING",
                tif="GTD", delay=0.0, ttl=10 ** 8, at=T + 20, queue_ahead=0.0)
    gh = await L.submit_order(conn, h, fee_fn=fee, now=T + 20)
    await H.observe(conn, slug, T + 21, offers=[(0.34, 150)])
    r = await SIM.simulate_order(conn, gh["order"]["order_id"], now=T + 22,
                                 fee_fn=fee)
    assert r["state"] == "PARTIALLY_FILLED", r
    # A CANCELLED Red Sox bid
    c = H.order(a, key="c", slug=slug, holding_side="LONG", qty=100,
                limit=0.30, role="HEDGE", group_id=g2, order_type="RESTING",
                tif="GTD", delay=0.0, ttl=10 ** 8, at=T + 25, queue_ahead=0.0)
    gc = await L.submit_order(conn, c, fee_fn=fee, now=T + 25)
    await SIM.request_cancel(conn, gc["order"]["order_id"], now=T + 26,
                             reason="TEST")
    r = await SIM.simulate_order(conn, gc["order"]["order_id"], now=T + 27,
                                 fee_fn=fee)
    assert r["state"] == "CANCELED", r
    # THE CURRENT BOOK (Red Sox 34/36 -> Yankees 64/66)
    await H.observe(conn, slug, T + 30, bids=[(0.34, 2500)],
                    offers=[(0.36, 1800)])
    # A PROPOSED Red Sox bid: Derek's ENTER decision with no order yet
    did = "paper_dec_rooms_%s" % tag
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, us_market_slug, holding_side, intent, verdict, "
        " internal_model, pinnacle, qualification_gaps, policy_version, "
        " simulator_version, proposed_qty, limit_price) VALUES ($1,$2,$3,"
        " now(),$4,'LONG','ORDER_INTENT_BUY_LONG','ENTER','{}','{}','[]',"
        " 'TEST','TEST',600,0.30)", did, a["session_id"], a["account_id"],
        slug)
    # XAVIER'S RECORD for the Yankees group (immutable; unique ids)
    await conn.execute(
        "INSERT INTO xavier_management_assessments (assessment_id, "
        " position_kind, group_id, review_id, assessed_at, trigger, "
        " evidence_state, probability, probability_source, "
        " probability_age_s, venue_economics, thesis_state, thesis_detail, "
        " alternatives, recommendation, discretionary_permitted, reallocate,"
        " policy) VALUES ($1,'PAPER',$2,$3,now(),'MARKET_EVENT',"
        " 'FRESH_CURRENT_PROBABILITY',0.63,'TEST_FIXTURE',4,'{}',"
        " 'NO_ENTRY_THESIS','{}',$4::jsonb,'HOLD',true,"
        " '{\"mode\": \"SHADOW\"}','{\"status\": \"READY\"}')",
        "xma:test%s" % tag, g1, "paperrev:test%s" % tag,
        json.dumps([{"action": "HOLD", "rankable": True, "value_usd": 630.0},
                    {"action": "EXIT", "rankable": True,
                     "value_usd": 624.0}]))
    # AN ACTUAL MIRROR of the entry, one real contract (1:1000)
    mid = "mirror_rooms_%s" % tag
    await conn.execute(
        "INSERT INTO execmirror_orders (mirror_id, group_id, role, "
        " us_market_slug, intent, order_type, tif, wire_price, live_qty, "
        " state, cum_qty, avg_px, fees_usd) VALUES ($1,$2,'ENTRY',$3,"
        " 'ORDER_INTENT_BUY_SHORT','MARKETABLE','IOC',0.50,1,'FILLED',1,"
        " 0.50,0.02)", mid, g1, slug)
    await conn.execute(
        "INSERT INTO execmirror_fills (fill_key, mirror_id, venue_order_id, "
        " group_id, us_market_slug, intent, qty, price, fee_usd) VALUES "
        " ($1,$2,'v-test',$3,$4,'ORDER_INTENT_BUY_SHORT',1,0.50,0.02)",
        "emf:%s" % tag, mid, g1, slug)
    await conn.execute(
        "INSERT INTO smalllive_handoffs (handoff_id, venue, group_id, "
        " us_market_slug, entry_mirror_id, opened_intent, live_held, "
        " live_bought, avg_entry_px, fees_usd, first_live_fill_at) VALUES "
        " ($1,'POLYMARKET',$2,$3,$4,'ORDER_INTENT_BUY_SHORT',1,1,0.50,0.02,"
        " now())", "slh:%s" % tag, g1, slug, mid)
    return {"a": a, "slug": slug, "event": event, "g1": g1, "g2": g2,
            "mid": mid, "did": did, "tag": tag}


async def _cleanup(conn, s):
    await conn.execute("DELETE FROM execmirror_fills WHERE mirror_id=$1",
                       s["mid"])
    await conn.execute("DELETE FROM execmirror_orders WHERE mirror_id=$1",
                       s["mid"])
    await conn.execute("DELETE FROM smalllive_handoffs WHERE handoff_id=$1",
                       "slh:%s" % s["tag"])
    await conn.execute("DELETE FROM us_premap WHERE market_slug=$1",
                       s["slug"])


@pg
async def test_the_rooms_and_the_room_on_a_real_database(monkeypatch):
    conn = await H.connect()
    s = None
    try:
        s = await _seed(conn)

        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(CP, "_pool", pool)
        monkeypatch.setattr(PR, "PAPER_ACCOUNT_ID", s["a"]["account_id"])
        key = "PAPER:EVT:" + s["event"]

        lst = await CP.position_rooms(Response(), book="paper")
        rows = {r["group_key"]: r for r in lst["venues"]["POLYMARKET"][
            "rooms"]}
        assert key in rows
        row = rows[key]
        assert row["legs"] == 2 and row["grouping"] == (
            "ESTABLISHED_EVENT_AND_SETTLEMENT_IDENTITY")
        assert row["orders_by_state"] == {"CANCELLED": 1, "FILLED": 1,
                                          "PARTIAL": 1, "PROPOSED": 1,
                                          "RESTING": 1}
        assert row["xavier"]["recommendation"] == "HOLD"
        assert set(lst["venues"]) == {"POLYMARKET"}

        room = await CP.position_room(key, Response())
        states = {o["order_ref"]: (o["state"], o["raw_state"])
                  for o in room["orders"]}
        assert sorted(v for v in states.values()) == sorted([
            ("FILLED", "FILLED"), ("RESTING", "RESTING"),
            ("PARTIAL", "PARTIALLY_FILLED"), ("CANCELLED", "CANCELED"),
            ("PROPOSED", "DECIDED_ENTER_NOT_ORDERED")])
        # PARITY with the paper ledger's own positions, leg by leg
        ledger = {(p["group_id"], p["holding_side"]): p
                  for p in await L.positions(conn, s["a"]["account_id"])}
        for lg in room["legs"]:
            p = ledger[(lg["group_id"], lg["holding_side"])]
            assert lg["holding"]["open_qty"] == pytest.approx(p["open_qty"])
            assert lg["holding"]["cost_basis_usd"] == pytest.approx(
                p["cost_basis_usd"])
            assert lg["holding"]["realized_pnl_usd"] == pytest.approx(
                p["realized_pnl_usd"])
        cur = {r["outcome"]: r for r in room["scenarios"]["current"]["rows"]}
        assert cur["new york yankees"]["payout_usd"] == 1000.0
        assert cur["boston red sox"]["payout_usd"] == 150.0
        prot = next(o for o in room["orders"] if o["state"] == "RESTING")
        assert prot["distance"]["distance"] == pytest.approx(0.06)
        assert prot["if_it_fills"]["realized_on_fill_usd"] == pytest.approx(
            1000 * (0.70 - 0.5035))
        # T0 books are old against the real clock: STALE, never fresh
        assert room["legs"][0]["current"]["freshness"] == "STALE"
        assert room["game_state"]["status"] == "UNAVAILABLE"
        assert room["archer"]["status"] in ("UNAVAILABLE", "NO_ESTIMATE")

        act = await CP.position_rooms(Response(), book="ACTUAL")
        assert set(act["venues"]) == {"POLYMARKET", "KALSHI"}
        akey = "ACTUAL-POLYMARKET:EVT:" + s["event"]
        arows = {r["group_key"]: r for r in act["venues"]["POLYMARKET"][
            "rooms"]}
        # the OPEN handoff makes the actual room active, venue-specific
        assert akey in arows and arows[akey]["venue"] == "POLYMARKET"
        assert not any(k.startswith("PAPER") for k in arows)
        aroom = await CP.position_room(akey, Response())
        assert [lg["holding"]["open_qty"] for lg in aroom["legs"]] == [1.0]
        assert aroom["economic"]["money_label"].startswith("REAL USD")

        with pytest.raises(HTTPException) as e:
            await CP.position_room("PAPER:EVT:no-such-event", Response())
        assert e.value.status_code == 404
        with pytest.raises(HTTPException) as e:
            await CP.position_room("garbage", Response())
        assert e.value.status_code == 400
        with pytest.raises(HTTPException) as e:
            await CP.position_rooms(Response(), book="BOTH")
        assert e.value.status_code == 400
    finally:
        if s:
            await _cleanup(conn, s)
        await conn.close()


@pg
async def test_the_read_wrapper_refuses_any_write(monkeypatch):
    conn = await H.connect()
    try:
        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(CP, "_pool", pool)
        before = await conn.fetchval("SELECT count(*) FROM us_premap")

        async def writer(c):
            await c.execute("INSERT INTO us_premap (identifier) VALUES "
                            "('must-not-be-written')")
        with pytest.raises(HTTPException) as e:
            await CP._read_only(writer)
        assert e.value.status_code == 503
        assert "ReadOnly" in e.value.detail["detail"]
        assert await conn.fetchval("SELECT count(*) FROM us_premap") == before

        async def timeout(c):
            return await c.fetchval(
                "SELECT setting::int FROM pg_settings "
                " WHERE name = 'statement_timeout'")
        assert await CP._read_only(timeout) == PR.STATEMENT_TIMEOUT_MS
    finally:
        await conn.close()
