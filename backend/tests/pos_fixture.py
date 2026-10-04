"""Synthetic rows for the profitability proofs (tests/test_profitability_*).

ALL DATA HERE IS SYNTHETIC TEST DATA written into a scratch test database
inside a transaction the test rolls back, under a fresh paper account so
other suites' rows never enter a measurement. Builds on intel_fixture
(decisions, fills, books) and adds what the economics read: entry orders
with a creation time and a cash reservation, settlements with a recorded
time, pre-map game starts, and an actual (execution mirror) position.
"""
from __future__ import annotations

import json
import time

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H

HOUR = 3600.0
DAY = 86400.0


async def premap(conn, slug, game_start):
    await conn.execute(
        "INSERT INTO us_premap (identifier, market_slug, side_norm, "
        " game_start, sports_type, team_name) VALUES ($1,$2,'HOME',"
        " to_timestamp($3),'baseball','Test Home')",
        "pos-" + F.uid(), slug, float(game_start))


async def order(conn, acct, *, group_id, slug, at, qty, price, role="ENTRY",
                direction="BUY", side="LONG", decision_id=None,
                reserved=None, state="FILLED"):
    oid = "paperord:" + F.uid()
    wire = price if side == "LONG" else round(1 - price, 6)
    res = (round(qty * price * 1.05, 6) if reserved is None else reserved) \
        if direction == "BUY" else 0.0
    rem = res if state in ("RESTING", "PENDING_SIMULATION") else 0.0
    await conn.execute(
        "INSERT INTO paper_orders (order_id, idempotency_key, account_id, "
        " session_id, group_id, role, direction, holding_side, intent, "
        " us_market_slug, fixture, label, order_type, time_in_force, "
        " allow_partial, qty, limit_price, wire_price, reserved_usd, "
        " reserved_remaining_usd, filled_qty, state, decision_id, "
        " decided_at, eligible_at, expires_at, simulator_version, strategy, "
        " created_at) VALUES ($1,$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'{}'::jsonb,"
        " 'MARKETABLE','IOC',true,$11,$12,$13,$14,$15,$16,$17,$18,"
        " to_timestamp($19),to_timestamp($19),to_timestamp($19 + 90),$20,"
        " $21,to_timestamp($19))",
        oid, acct["account_id"], acct["session_id"], group_id, role,
        direction, side, F.intent_of(direction, side), slug, "fx-" + slug,
        qty, price, wire, res, rem,
        qty if state == "FILLED" else 0, state, decision_id, float(at),
        F.SIM_VERSION, F.STRATEGY)
    return oid


async def settle(conn, acct, *, group_id, slug, side="LONG", qty, outcome,
                 ppc, at, recorded_at=None):
    sid = "papersettle:" + F.uid()
    pk = "paperpos:%s:%s:%s:%s" % (acct["account_id"], group_id, slug, side)
    await conn.execute(
        "INSERT INTO paper_settlements (settlement_id, account_id, "
        " position_key, settlement_event_key, version, group_id, "
        " us_market_slug, holding_side, qty, outcome, payout_per_contract, "
        " payout_usd, evidence, evidence_source, settled_at, recorded_at) "
        "VALUES ($1,$2,$3,'test',1,$4,$5,$6,$7,$8,$9,$10,'{}'::jsonb,"
        " 'TEST_EVIDENCE',to_timestamp($11),to_timestamp($12))",
        sid, acct["account_id"], pk, group_id, slug, side, qty, outcome, ppc,
        round(qty * ppc, 6), float(at),
        float(at if recorded_at is None else recorded_at))
    return sid


async def lag_history(conn, acct, *, now, n=6, lag_h=3.0):
    """n markets settled lag_h after their game start, ~20 days ago."""
    for i in range(n):
        slug = F.uid("pos-lag-")
        gs = now - 20 * DAY - i * HOUR
        await premap(conn, slug, gs)
        await settle(conn, acct, group_id="paper_group_" + F.uid(),
                     slug=slug, qty=1, outcome="WON", ppc=1.0,
                     at=gs + lag_h * HOUR)


async def settled_position(conn, acct, *, now, opened_ago_d=10.0, qty=100,
                           price=0.53, fee=1.0, p=0.62, outcome="WON",
                           reserved=60.0, game_after_h=2.0, lag_h=3.0):
    """Decision -> entry order (reserved) -> fill -> game -> settlement."""
    t0 = now - opened_ago_d * DAY
    d = await F.decision(conn, acct, at=t0 - 5, p=p, vwap=price - 0.01,
                         fees_usd=fee, qty=qty)
    g = "paper_group_" + F.uid()
    oid = await order(conn, acct, group_id=g, slug=d["slug"], at=t0,
                      qty=qty, price=price, decision_id=d["decision_id"],
                      reserved=reserved)
    fid = await F.fill(conn, acct, order_id=oid, group_id=g, slug=d["slug"],
                       qty=qty, price=price, fee=fee, at=t0 + 2)
    gs = t0 + game_after_h * HOUR
    await premap(conn, d["slug"], gs)
    ppc = 1.0 if outcome == "WON" else 0.0
    sid = await settle(conn, acct, group_id=g, slug=d["slug"], qty=qty,
                       outcome=outcome, ppc=ppc, at=gs + lag_h * HOUR)
    return {"decision": d, "group_id": g, "order_id": oid, "fill_id": fid,
            "settlement_id": sid, "t0": t0, "game_start": gs,
            "position_key": "paperpos:%s:%s:%s:LONG" % (
                acct["account_id"], g, d["slug"])}


async def sold_position(conn, acct, *, now, ago_d=5.0, qty=50, buy=0.40,
                        sell=0.45, fee=0.5, p=0.5, contract_outcome="LOST"):
    """Bought, sold out an hour later; the contract later settled (on
    another paper position) -- the hold-to-settlement counterfactual."""
    t0 = now - ago_d * DAY
    d = await F.decision(conn, acct, at=t0 - 5, p=p, vwap=buy, qty=qty)
    g = "paper_group_" + F.uid()
    oid = await order(conn, acct, group_id=g, slug=d["slug"], at=t0,
                      qty=qty, price=buy, decision_id=d["decision_id"])
    await F.fill(conn, acct, order_id=oid, group_id=g, slug=d["slug"],
                 qty=qty, price=buy, fee=fee, at=t0 + 2)
    so = await order(conn, acct, group_id=g, slug=d["slug"], at=t0 + HOUR,
                     qty=qty, price=sell, role="EXIT", direction="SELL")
    await F.fill(conn, acct, order_id=so, group_id=g, slug=d["slug"],
                 role="EXIT", direction="SELL", qty=qty, price=sell, fee=fee,
                 at=t0 + HOUR + 2)
    other = await H.new_account(conn, "posother", now=now - 30 * DAY)
    await settle(conn, other, group_id="paper_group_" + F.uid(),
                 slug=d["slug"], qty=1, outcome=contract_outcome,
                 ppc=1.0 if contract_outcome == "WON" else 0.0,
                 at=t0 + 6 * HOUR)
    return {"decision": d, "group_id": g, "t0": t0,
            "position_key": "paperpos:%s:%s:%s:LONG" % (
                acct["account_id"], g, d["slug"])}


async def open_position(conn, acct, *, now, qty=10, price=0.30, p=0.40,
                        game_in_h=5.0):
    t0 = now - HOUR
    d = await F.decision(conn, acct, at=t0 - 5, p=p, vwap=price, qty=qty)
    g = "paper_group_" + F.uid()
    oid = await order(conn, acct, group_id=g, slug=d["slug"], at=t0,
                      qty=qty, price=price, decision_id=d["decision_id"])
    await F.fill(conn, acct, order_id=oid, group_id=g, slug=d["slug"],
                 qty=qty, price=price, at=t0 + 2)
    await premap(conn, d["slug"], now + game_in_h * HOUR)
    return {"decision": d, "group_id": g,
            "position_key": "paperpos:%s:%s:%s:LONG" % (
                acct["account_id"], g, d["slug"])}


async def capacity_candidate(conn, acct, *, now, p=0.58, book=True):
    d = await F.decision(conn, acct, at=now - 600, p=p, verdict="REFUSE")
    if book:
        await F.book(conn, d["slug"], now - 610,
                     bids=((0.48, 200),),
                     offers=((0.50, 100), (0.55, 100), (0.60, 1000)))
    return d


async def actual_position(conn, *, now, qty=20, price=0.45, fee=0.2,
                          decision_id=None, slug=None):
    """An execution-mirror (ACTUAL) entry: intent -> mirror order -> fill."""
    g = "paper_group_" + F.uid()
    slug = slug or F.uid("pos-act-")
    mid = "mirror:" + F.uid()
    iid = "intent:" + F.uid()
    await conn.execute(
        "INSERT INTO execution_intents (intent_id, decision_id, strategy, "
        " us_market_slug, order_intent, group_id, order_type, time_in_force,"
        " paper_target_qty, wire_price, decided_at, live_eligible, "
        " actual_state, actual_mirror_id, policy_version, live_eligibility)"
        " VALUES ($1,$2,$3,$4,'ORDER_INTENT_BUY_LONG',$5,'MARKETABLE','IOC',"
        " $6,$7,to_timestamp($8),true,'SUBMITTED',$9,'TEST_POLICY',"
        " '{\"admission\": {\"verdict\": \"LIVE_ADMISSIBLE\"}}'::jsonb)",
        iid, decision_id or ("paperdec:" + F.uid()), F.STRATEGY, slug, g, qty,
        price, float(now - 2 * HOUR), mid)
    await conn.execute(
        "INSERT INTO execmirror_orders (mirror_id, group_id, role, strategy, "
        " us_market_slug, intent, order_type, tif, state, live_qty, cum_qty,"
        " execution_intent_id, accepted_at) VALUES ($1,$2,'ENTRY',$3,$4,"
        " 'ORDER_INTENT_BUY_LONG','MARKETABLE','IOC','FILLED',$5::int,"
        " $5::numeric,$6,to_timestamp($7))", mid, g, F.STRATEGY, slug,
        int(qty), iid,
        float(now - 2 * HOUR))
    fk = "fill:" + F.uid()
    await conn.execute(
        "INSERT INTO execmirror_fills (fill_key, mirror_id, venue_order_id, "
        " group_id, us_market_slug, intent, qty, price, fee_usd, "
        " observed_at, source) VALUES ($1,$2,'v-1',$3,$4,"
        " 'ORDER_INTENT_BUY_LONG',$5,$6,$7,to_timestamp($8),'TEST')",
        fk, mid, g, slug, qty, price, fee, float(now - 2 * HOUR + 5))
    return {"group_id": g, "slug": slug, "mirror_id": mid, "intent_id": iid,
            "fill_key": fk,
            "position_key": "actualpos:POLYMARKET_US:%s:%s:LONG" % (g, slug)}


async def scenario(conn, *, now=None):
    now = float(time.time() if now is None else now)
    acct = await H.new_account(conn, "posecon", now=now - 40 * DAY)
    await lag_history(conn, acct, now=now)
    a = await settled_position(conn, acct, now=now)
    b = await sold_position(conn, acct, now=now)
    c = await open_position(conn, acct, now=now)
    k = await capacity_candidate(conn, acct, now=now)
    nb = await capacity_candidate(conn, acct, now=now, book=False)
    await F.equity(conn, acct, at=now - 60, equity_usd=500050.0)
    return {"now": now, "acct": acct, "settled": a, "sold": b, "open": c,
            "cap": k, "cap_nobook": nb}


def j(v):
    return json.loads(v) if isinstance(v, str) else v
