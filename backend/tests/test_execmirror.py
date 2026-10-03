"""THE 1:1,000 EXECUTION MIRROR, proved before it touches a venue.

Pure rules: scaling and rounding, order shape, exit fractions, fills from
venue order records, ambiguous-submission matching, the P&L explanation.

Against Postgres (RN1X_TEST_DSN) with a FAKE venue (no network): duplicate
prevention, restart recovery, ambiguous submissions reconciled before any
retry, partial fills, cancellation races, rejected orders, insufficient
cash, minimum size, exit inventory (never selling what was not bought),
protection resized to live inventory, orphaned live inventory, emergency
stop (cancel / flatten), a changed account halting the lane, and the
cutover (nothing historical replayed).

All paper data here is synthetic and written to test accounts in the test
database; `paper_acct_main` is never read or written.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from decimal import Decimal

import pytest

from sportsassets import execmirror as M
from sportsassets import execmirror_probe as EP
from sportsassets import execmirror_view as V

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
KID, SEC = "kid-execmirror-test", "c2VjcmV0LWV4ZWNtaXJyb3ItdGVzdA=="


# ───────────────────────────── pure rules ─────────────────────────────

def test_scaling_rounds_to_the_nearest_whole_contract_and_records_it():
    exact, live, delta = M.scale_qty(2702, 1000)
    assert (exact, live) == (Decimal("2.702"), 3) and delta == Decimal("0.298")
    assert M.scale_qty(2500, 1000)[1] == 2          # half to even, unbiased
    assert M.scale_qty(3500, 1000)[1] == 4
    assert M.scale_qty(499, 1000)[1] == 0
    assert M.scale_qty(1000, 1000)[1:] == (1, Decimal(0))


def _paper(**k):
    base = {"order_id": "paper_o1", "group_id": "g1", "role": "ENTRY",
            "us_market_slug": "mlb-nyy-bos-2026-10-02",
            "intent": "ORDER_INTENT_BUY_LONG", "wire_price": Decimal("0.55"),
            "qty": Decimal("2000"), "order_type": "MARKETABLE",
            "time_in_force": "IOC",
            "expires_at": dt.datetime(2026, 10, 2, 20, 0, tzinfo=dt.timezone.utc)}
    base.update(k)
    return base


def test_a_buy_keeps_contract_intent_price_and_time_in_force():
    p = M.plan_buy(_paper(), scale=1000, buying_power=100, max_order_usd=25)
    assert p.state == "PLANNED" and p.live_qty == 2
    assert p.params == {"marketSlug": "mlb-nyy-bos-2026-10-02",
                        "intent": "ORDER_INTENT_BUY_LONG",
                        "type": "ORDER_TYPE_LIMIT",
                        "price": {"value": "0.5500", "currency": "USD"},
                        "quantity": 2, "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"}
    g = M.plan_buy(_paper(time_in_force="GTD", order_type="RESTING"), scale=1000,
                   buying_power=100, max_order_usd=25)
    assert g.params["tif"] == "TIME_IN_FORCE_GOOD_TILL_DATE"
    assert g.params["goodTillTime"].startswith("2026-10-02T20:00:00")
    assert g.params["participateDontInitiate"] is True
    f = M.plan_buy(_paper(time_in_force="FOK"), scale=1000, buying_power=100,
                   max_order_usd=25)
    assert f.params["tif"] == "TIME_IN_FORCE_FILL_OR_KILL"


def test_buy_exclusions_are_precise_and_never_round_up():
    assert M.plan_buy(_paper(qty=Decimal("400")), scale=1000, buying_power=100,
                      max_order_usd=25).exclusion == M.BELOW_VENUE_MINIMUM
    cap = M.plan_buy(_paper(qty=Decimal("60000")), scale=1000, buying_power=1000,
                     max_order_usd=25)
    assert cap.exclusion == M.ABOVE_ORDER_CAP and cap.live_qty == 60
    cash = M.plan_buy(_paper(), scale=1000, buying_power=0.50, max_order_usd=25)
    assert cash.exclusion == M.INSUFFICIENT_CASH
    # a short buy ties up the complement of the wire price
    s = M.plan_buy(_paper(intent="ORDER_INTENT_BUY_SHORT", wire_price=Decimal("0.80")),
                   scale=1000, buying_power=0.45, max_order_usd=25)
    assert s.state == "PLANNED" and s.detail["cost_usd"] == "0.40"


def test_exits_sell_the_same_fraction_of_live_inventory_and_never_more():
    sell = _paper(role="EXIT", intent="ORDER_INTENT_SELL_LONG", qty=Decimal("1000"))
    none = M.plan_sell(sell, scale=1000, paper_open_qty=2000, live_held=0,
                       live_committed=0, opened_intent="ORDER_INTENT_BUY_LONG")
    assert none.exclusion == M.NO_LIVE_INVENTORY
    half = M.plan_sell(sell, scale=1000, paper_open_qty=2000, live_held=3,
                       live_committed=0, opened_intent="ORDER_INTENT_BUY_LONG")
    assert half.state == "PLANNED" and half.live_qty == 2      # round(0.5*3)=2
    close = M.plan_sell(_paper(role="EXIT", intent="ORDER_INTENT_SELL_LONG",
                               qty=Decimal("2000")), scale=1000, paper_open_qty=2000,
                        live_held=1, live_committed=0,
                        opened_intent="ORDER_INTENT_BUY_LONG")
    assert close.live_qty == 1 and close.detail["closes"] is True
    committed = M.plan_sell(sell, scale=1000, paper_open_qty=2000, live_held=2,
                            live_committed=2, opened_intent="ORDER_INTENT_BUY_LONG")
    assert committed.exclusion == M.INVENTORY_COMMITTED
    wrong = M.plan_sell(sell, scale=1000, paper_open_qty=2000, live_held=2,
                        live_committed=0, opened_intent="ORDER_INTENT_BUY_SHORT")
    assert wrong.exclusion == M.INTENT_MISMATCH
    tiny = M.plan_sell(_paper(role="REDUCE", intent="ORDER_INTENT_SELL_LONG",
                              qty=Decimal("100")), scale=1000, paper_open_qty=5000,
                       live_held=5, live_committed=0,
                       opened_intent="ORDER_INTENT_BUY_LONG")
    assert tiny.exclusion == M.BELOW_VENUE_MINIMUM


def test_fills_come_from_increases_of_the_venue_order_record():
    assert M.fill_delta(0, None, 0, 0, None, 0) is None
    a = M.fill_delta(0, None, 0, 2, Decimal("0.55"), Decimal("0.02"))
    assert a == {"qty": Decimal(2), "price": Decimal("0.550000"), "fee": Decimal("0.02")}
    b = M.fill_delta(2, Decimal("0.55"), Decimal("0.02"), 3, Decimal("0.56"), Decimal("0.03"))
    assert b["qty"] == 1 and b["price"] == Decimal("0.580000") and b["fee"] == Decimal("0.01")


def test_an_ambiguous_submission_matches_only_its_own_order():
    row = {"us_market_slug": "m", "intent": "ORDER_INTENT_BUY_LONG",
           "wire_price": Decimal("0.55"), "live_qty": 2}
    good = {"id": "v1", "marketSlug": "m", "intent": "ORDER_INTENT_BUY_LONG",
            "quantity": 2, "price": {"value": "0.5500"}}
    assert M.match_unknown(row, [dict(good, quantity=3), good])["id"] == "v1"
    assert M.match_unknown(row, [dict(good, price={"value": "0.56"})]) is None


def test_the_pnl_difference_is_fully_explained():
    d = V.decompose(paper_pnl=Decimal("400"), paper_entry_qty=Decimal("2702"),
                    scale=1000, live_target_qty=3, live_filled_qty=2,
                    live_pnl=Decimal("0.27"), live_fees=Decimal("0.03"),
                    paper_fees=Decimal("10"))
    e = d["explained"]
    assert abs(sum(e.values()) - d["difference"]) < 1e-6
    assert d["expected_live_pnl"] == 0.4


def test_live_cash_flow_uses_collateral_space_for_shorts():
    fills = [{"qty": 2, "price": "0.55", "fee_usd": "0.01", "intent": "ORDER_INTENT_BUY_LONG"},
             {"qty": 2, "price": "0.60", "fee_usd": "0.01", "intent": "ORDER_INTENT_SELL_LONG"},
             {"qty": 1, "price": "0.80", "fee_usd": "0", "intent": "ORDER_INTENT_BUY_SHORT"}]
    assert V.live_cash_flow(fills) == Decimal("-0.12")


def test_training_orders_keep_their_disclosure():
    k = V.strategy_kind("PINNACLE_EXPLORATION_PAPER", {})
    assert k["kind"] == "TRAINING" and "negative expected value" in k["disclosure"]
    assert V.strategy_kind("DEREK_ENTRY_POLICY_V2", {})["kind"] == "INVESTMENT"


def test_the_lane_names_its_own_credential_only():
    import inspect
    src = inspect.getsource(M)
    assert "PMUS_KEY_ID" not in src and "pmus_key_id" not in src
    assert "from . import pmus" not in src and "import pmus" not in src


# ─────────────────────── fake venue (no network) ───────────────────────

class _Err(Exception):
    def __init__(self, status, msg="refused"):
        super().__init__(msg)
        self.status_code = status


class FakeVenue:
    def __init__(self):
        self.orders, self.placed, self.behaviour = {}, [], []
        self.bp = 100.0
        self.race, self.cancelled, self.closed = set(), [], []
        self.cancel_all_calls = 0
        self.book = {"bestBid": {"value": "0.40"}, "bestAsk": {"value": "0.45"}}
        self._n = 0

    def _new(self, params):
        self._n += 1
        vid = "v%d" % self._n
        self.orders[vid] = {"id": vid, "marketSlug": params["marketSlug"],
                            "intent": params["intent"], "quantity": params["quantity"],
                            "price": params["price"], "tif": params["tif"],
                            "state": "ORDER_STATE_NEW", "cumQuantity": 0,
                            "avgPx": None, "commissionNotionalTotalCollected": {"value": "0"}}
        return vid

    def fill(self, vid, qty, fee_per=0.01):
        o = self.orders[vid]
        o["cumQuantity"] = min(o["quantity"], o["cumQuantity"] + qty)
        o["avgPx"] = o["price"]
        o["commissionNotionalTotalCollected"] = {"value": "%.4f" % (o["cumQuantity"] * fee_per)}
        o["state"] = ("ORDER_STATE_FILLED" if o["cumQuantity"] >= o["quantity"]
                      else "ORDER_STATE_PARTIALLY_FILLED")

    def place(self, params):
        self.placed.append(params)
        b = self.behaviour.pop(0) if self.behaviour else {}
        if b.get("raise"):
            if b.get("create_anyway"):
                self._new(params)
            raise _Err(b["raise"], "venue said no")
        vid = self._new(params)
        if b.get("fill"):
            self.fill(vid, b["fill"])
        if params["tif"] in ("TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
                             "TIME_IN_FORCE_FILL_OR_KILL") and \
                self.orders[vid]["state"] != "ORDER_STATE_FILLED":
            self.orders[vid]["state"] = "ORDER_STATE_CANCELED"
        return {"id": vid, "executions": []}

    def cancel(self, vid, slug):
        if vid in self.race:
            self.fill(vid, 10 ** 6)
            raise _Err(400, "order already filled")
        self.cancelled.append(vid)
        self.orders[vid]["state"] = "ORDER_STATE_CANCELED"

    def cancel_all(self):
        self.cancel_all_calls += 1
        return {"canceledOrderIds": []}

    def close(self, slug, bips=300):
        self.closed.append(slug)
        return {"id": "close-" + slug}

    def order(self, vid):
        return dict(self.orders[vid])

    def open_orders(self, slugs=None):
        return [dict(o) for o in self.orders.values()
                if o["state"] in ("ORDER_STATE_NEW", "ORDER_STATE_PARTIALLY_FILLED")
                and (not slugs or o["marketSlug"] in slugs)]

    def balances(self):
        return [{"currency": "USD", "currentBalance": self.bp, "buyingPower": self.bp}]

    def positions(self):
        net = {}
        for o in self.orders.values():
            sgn = 1 if "BUY" in o["intent"] else -1
            net[o["marketSlug"]] = net.get(o["marketSlug"], 0) + sgn * o["cumQuantity"]
        return {s: {"netPosition": str(n), "cashValue": {"value": "%.2f" % (n * 0.5)},
                    "cost": {"value": "0"}, "expired": False}
                for s, n in net.items() if n}

    def bbo(self, slug):
        return dict(self.book)


# ─────────────────────────── database proofs ───────────────────────────

async def _conn():
    import asyncpg
    return await asyncpg.connect(DSN)


async def _setup(conn, monkeypatch, *, cap=25, ago_s=5):
    monkeypatch.setenv(EP.KEY_ID_ENV, KID)
    monkeypatch.setenv(EP.SECRET_ENV, SEC)
    from tests.paper_harness import new_account
    acct = await new_account(conn, "exm")
    await conn.execute("TRUNCATE execmirror_fills, execmirror_events, "
                       "execmirror_snapshots, execmirror_orders")
    await conn.execute(
        """UPDATE execmirror_control SET enabled = true, stopped = false,
             stop_done_at = NULL, flatten_on_stop = false,
             cutover_at = now() - make_interval(secs => $1),
             account_fingerprint = $2, baseline = '{}'::jsonb, max_order_usd = $3,
             scale = 1000 WHERE id = 1""", float(ago_s), EP.fingerprint(KID), cap)
    venue = FakeVenue()
    mirror = M.Mirror(lambda: venue, paper_account=acct["account_id"])
    return acct, venue, mirror


async def _paper_order(conn, acct, *, role="ENTRY", direction="BUY",
                       intent="ORDER_INTENT_BUY_LONG", qty=2000, wire=0.55,
                       tif="IOC", otype="MARKETABLE", group=None, state="PENDING_SIMULATION",
                       decided_offset_s=0.0, strategy="PINNACLE_COMPLETED_GAME_PAPER",
                       policy_version="PINNACLE_COMPLETED_GAME_PAPER_V2"):
    oid = "paper_%s" % uuid.uuid4().hex[:12]
    group = group or "paper_group_%s" % uuid.uuid4().hex[:8]
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=decided_offset_s)
    cols = dict(order_id=oid, idempotency_key=oid, account_id=acct["account_id"],
                session_id=acct["session_id"], group_id=group, role=role,
                direction=direction, holding_side="LONG", intent=intent,
                us_market_slug="mlb-test-%s" % group[-4:], order_type=otype,
                time_in_force=tif, allow_partial=True, qty=qty, limit_price=wire,
                wire_price=wire, state=state, decided_at=now, eligible_at=now,
                expires_at=now + dt.timedelta(minutes=30), simulator_version="TEST")
    if strategy:
        cols["strategy"] = strategy
    if policy_version:
        cols["label"] = json.dumps({"policy_version": policy_version})
    names = ", ".join(cols)
    ph = ", ".join("$%d" % (i + 1) for i in range(len(cols)))
    await conn.execute("INSERT INTO paper_orders (%s) VALUES (%s)" % (names, ph),
                       *cols.values())
    return {"order_id": oid, "group_id": group, "slug": cols["us_market_slug"]}


async def _paper_fill(conn, acct, po, *, qty, direction="BUY", price=0.55, role="ENTRY"):
    fid = "paper_f_%s" % uuid.uuid4().hex[:10]
    await conn.execute(
        """INSERT INTO paper_fills (fill_id, idempotency_key, order_id, account_id,
             session_id, group_id, role, direction, holding_side, us_market_slug,
             qty, price, wire_price, fee_usd, gross_usd, filled_at, basis,
             simulator_version)
           VALUES ($1,$1,$2,$3,$4,$5,$6,$7,'LONG',$8,$9,$10,$10,0,$11,now(),
                   'DEPTH_WALK_WITHIN_LIMIT','TEST')""",
        fid, po["order_id"], acct["account_id"], acct["session_id"], po["group_id"],
        role, direction, po["slug"], qty, price, qty * price)


async def _row(conn, paper_order_id):
    return dict(await conn.fetchrow(
        "SELECT * FROM execmirror_orders WHERE paper_order_id = $1", paper_order_id))


@pg
@pytest.mark.asyncio
async def test_an_entry_is_placed_once_and_its_fills_come_from_the_venue(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        po = await _paper_order(conn, acct, qty=2702)
        venue.behaviour = [{"fill": 2}]                 # IOC: 2 of 3 trade
        await mirror.tick(conn)
        await mirror.tick(conn)                         # duplicate prevention
        assert len(venue.placed) == 1
        assert venue.placed[0]["quantity"] == 3 and venue.placed[0]["intent"] == "ORDER_INTENT_BUY_LONG"
        r = await _row(conn, po["order_id"])
        assert r["state"] == "CANCELLED" and r["cum_qty"] == 2   # IOC remainder cancelled
        assert r["rounding_delta"] == Decimal("0.298000")
        fills = await conn.fetch("SELECT * FROM execmirror_fills WHERE mirror_id = $1",
                                 r["mirror_id"])
        assert [f["qty"] for f in fills] == [Decimal(2)]
        assert fills[0]["source"] == "VENUE_ORDER_RECORD"
        inv = await M.live_inventory(conn, po["group_id"])
        assert inv["held"] == 2 and inv["opened_intent"] == "ORDER_INTENT_BUY_LONG"
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_accepted_resting_order_is_not_a_fill(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        po = await _paper_order(conn, acct, tif="GTD", otype="RESTING")
        await mirror.tick(conn)
        r = await _row(conn, po["order_id"])
        assert r["state"] == "OPEN" and r["venue_order_id"] and r["cum_qty"] == 0
        assert await conn.fetchval("SELECT count(*) FROM execmirror_fills") == 0
        assert venue.placed[0]["participateDontInitiate"] is True
        venue.fill(r["venue_order_id"], 1)
        await mirror.tick(conn)
        r = await _row(conn, po["order_id"])
        assert r["state"] == "PARTIALLY_FILLED" and r["cum_qty"] == 1
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_orders_before_the_cutover_are_never_replayed(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch, ago_s=5)
        await _paper_order(conn, acct, decided_offset_s=-3600)
        old_exit = await _paper_order(conn, acct, role="EXIT", direction="SELL",
                                      intent="ORDER_INTENT_SELL_LONG", decided_offset_s=-3600)
        await mirror.tick(conn)
        assert venue.placed == []
        assert await conn.fetchval("SELECT count(*) FROM execmirror_orders") == 0
        new_exit = await _paper_order(conn, acct, role="EXIT", direction="SELL",
                                      intent="ORDER_INTENT_SELL_LONG", group=old_exit["group_id"])
        await mirror.tick(conn)
        assert venue.placed == []                       # no live inventory to sell
        assert (await _row(conn, new_exit["order_id"]))["exclusion"] == M.NO_LIVE_INVENTORY
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_rejected_order_is_recorded_and_not_retried(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        po = await _paper_order(conn, acct)
        venue.behaviour = [{"raise": 400}]
        await mirror.tick(conn)
        await mirror.tick(conn)
        r = await _row(conn, po["order_id"])
        assert r["state"] == "REJECTED" and len(venue.placed) == 1
        assert json.loads(r["error"])["status"] == 400
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_ambiguous_submission_is_reconciled_before_anything_is_retried(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        po = await _paper_order(conn, acct, tif="GTD", otype="RESTING")
        venue.behaviour = [{"raise": 504, "create_anyway": True}]   # lost response
        await mirror.tick(conn)
        r = await _row(conn, po["order_id"])
        assert r["state"] == "UNKNOWN" and r["venue_order_id"] is None
        await mirror.tick(conn)                         # recover: adopt, never resend
        r = await _row(conn, po["order_id"])
        assert r["state"] == "OPEN" and r["venue_order_id"] == "v1"
        assert len(venue.placed) == 1
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_restart_mid_submission_recovers_without_a_second_order(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        po = await _paper_order(conn, acct, tif="GTD", otype="RESTING")
        await mirror.plan_new(conn, await M.control(conn))
        # the process died after claiming and sending, before recording the id
        r = await _row(conn, po["order_id"])
        params = json.loads(r["detail"])["params"]
        venue.place(params)
        await conn.execute("UPDATE execmirror_orders SET state='SUBMITTING',"
                           " submit_started_at = now() - interval '2 minutes'"
                           " WHERE mirror_id = $1", r["mirror_id"])
        fresh = M.Mirror(lambda: venue, paper_account=acct["account_id"])
        await fresh.tick(conn)
        r = await _row(conn, po["order_id"])
        assert r["state"] == "OPEN" and r["venue_order_id"] == "v1"
        assert len(venue.placed) == 1
        # nothing resting after the grace window: rejected, not resent
        po2 = await _paper_order(conn, acct)
        await fresh.plan_new(conn, await M.control(conn))
        r2 = await _row(conn, po2["order_id"])
        await conn.execute("UPDATE execmirror_orders SET state='SUBMITTING',"
                           " submit_started_at = now() - interval '5 minutes'"
                           " WHERE mirror_id = $1", r2["mirror_id"])
        await fresh.recover(conn)
        r2 = await _row(conn, po2["order_id"])
        assert r2["state"] == "REJECTED"
        assert json.loads(r2["error"])["code"] == "NOT_FOUND_AFTER_RECONCILE"
        assert len(venue.placed) == 1
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_insufficient_cash_and_the_minimum_size_are_exclusions(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        venue.bp = 1.00
        a = await _paper_order(conn, acct, qty=4000, wire=0.60)   # $2.40 > $1.00
        b = await _paper_order(conn, acct, qty=300)               # 0.3 contract
        await mirror.tick(conn)
        assert (await _row(conn, a["order_id"]))["exclusion"] == M.INSUFFICIENT_CASH
        assert (await _row(conn, b["order_id"]))["exclusion"] == M.BELOW_VENUE_MINIMUM
        assert venue.placed == []
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_exits_follow_live_inventory_and_never_sell_what_was_not_bought(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        entry = await _paper_order(conn, acct, qty=4000)
        venue.behaviour = [{"fill": 1}]                 # live bought 1 of 4
        await mirror.tick(conn)
        await _paper_fill(conn, acct, entry, qty=4000)  # paper bought all 4,000
        g = entry["group_id"]
        half = await _paper_order(conn, acct, role="REDUCE", direction="SELL",
                                  intent="ORDER_INTENT_SELL_LONG", qty=2000, group=g)
        await mirror.tick(conn)
        r = await _row(conn, half["order_id"])
        # half of ONE live contract rounds to 0 (half-even): nothing to sell
        assert r["exclusion"] == M.BELOW_VENUE_MINIMUM
        close = await _paper_order(conn, acct, role="EXIT", direction="SELL",
                                   intent="ORDER_INTENT_SELL_LONG", qty=4000, group=g)
        venue.behaviour = [{"fill": 1}]
        await mirror.tick(conn)
        r = await _row(conn, close["order_id"])
        assert r["live_qty"] == 1 and venue.placed[-1]["intent"] == "ORDER_INTENT_SELL_LONG"
        assert (await M.live_inventory(conn, g))["held"] == 0
        again = await _paper_order(conn, acct, role="EXIT", direction="SELL",
                                   intent="ORDER_INTENT_SELL_LONG", qty=4000, group=g)
        await mirror.tick(conn)
        assert (await _row(conn, again["order_id"]))["exclusion"] == M.NO_LIVE_INVENTORY
        assert sum(p["quantity"] for p in venue.placed
                   if "SELL" in p["intent"]) == 1
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_protection_waits_for_live_inventory_and_resizes_with_it(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        entry = await _paper_order(conn, acct, qty=3000, tif="GTD", otype="RESTING")
        await mirror.tick(conn)
        e = await _row(conn, entry["order_id"])
        await _paper_fill(conn, acct, entry, qty=3000)
        prot = await _paper_order(conn, acct, role="STANDING_PROTECTION", direction="SELL",
                                  intent="ORDER_INTENT_SELL_LONG", qty=3000,
                                  group=entry["group_id"], tif="GTD", otype="RESTING",
                                  state="RESTING")
        await mirror.tick(conn)
        assert (await _row(conn, prot["order_id"]))["exclusion"] == M.NO_LIVE_INVENTORY
        venue.fill(e["venue_order_id"], 2)              # live entry fills 2 of 3
        await mirror.tick(conn)                         # fill recorded
        await mirror.tick(conn)                         # protection placed at 2
        sells = [p for p in venue.placed if "SELL" in p["intent"]]
        assert [p["quantity"] for p in sells] == [2]
        venue.fill(e["venue_order_id"], 1)              # the last contract fills
        for _ in range(4):
            await mirror.tick(conn)
        sells = [p for p in venue.placed if "SELL" in p["intent"]]
        assert [p["quantity"] for p in sells] == [2, 3]
        assert len(venue.cancelled) == 1                # the 2-lot was cancelled first
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_cancel_that_loses_the_race_to_a_fill_counts_the_fill(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        po = await _paper_order(conn, acct, tif="GTD", otype="RESTING")
        await mirror.tick(conn)
        r = await _row(conn, po["order_id"])
        venue.race.add(r["venue_order_id"])
        await conn.execute("UPDATE paper_orders SET state = 'CANCELED' WHERE order_id = $1",
                           po["order_id"])
        await mirror.tick(conn)
        r = await _row(conn, po["order_id"])
        assert r["state"] == "FILLED" and r["cum_qty"] == 2
        assert (await M.live_inventory(conn, po["group_id"]))["held"] == 2
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_live_inventory_left_after_the_paper_exit_is_closed(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        entry = await _paper_order(conn, acct, qty=2000)
        venue.behaviour = [{"fill": 2}]
        await mirror.tick(conn)
        await _paper_fill(conn, acct, entry, qty=2000)
        ex = await _paper_order(conn, acct, role="EXIT", direction="SELL",
                                intent="ORDER_INTENT_SELL_LONG", qty=2000,
                                group=entry["group_id"])
        venue.behaviour = [{"fill": 0}]                 # the live exit does not trade
        await mirror.tick(conn)
        await _paper_fill(conn, acct, ex, qty=2000, direction="SELL", role="EXIT")
        await conn.execute("UPDATE paper_orders SET state='FILLED' WHERE group_id=$1",
                           entry["group_id"])
        venue.behaviour = [{"fill": 2}]
        await mirror.tick(conn)
        await mirror.tick(conn)
        orphan = await conn.fetchrow("SELECT * FROM execmirror_orders WHERE role='ORPHAN_CLOSE'")
        assert orphan and orphan["live_qty"] == 2
        assert venue.placed[-1]["price"]["value"] == "0.4000"    # the venue bid
        assert (await M.live_inventory(conn, entry["group_id"]))["held"] == 0
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_emergency_stop_cancels_everything_and_flattens_on_request(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        a = await _paper_order(conn, acct, qty=2000, tif="GTD", otype="RESTING")
        b = await _paper_order(conn, acct, qty=2000)
        venue.behaviour = [{}, {"fill": 2}]
        await mirror.tick(conn)
        await conn.execute("UPDATE execmirror_control SET stopped = true,"
                           " flatten_on_stop = true WHERE id = 1")
        out = await mirror.tick(conn)
        assert out["state"] == "STOPPED" and out["cancelled"] == 1
        assert venue.cancel_all_calls == 1 and venue.closed == [b["slug"]]
        late = await _paper_order(conn, acct)
        await mirror.tick(conn)
        assert len(venue.placed) == 2                   # nothing new after a stop
        assert (await _row(conn, a["order_id"]))["state"] == "CANCELLED"
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_different_account_halts_the_lane(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        monkeypatch.setenv(EP.KEY_ID_ENV, "a-different-key")
        await _paper_order(conn, acct)
        out = await mirror.tick(conn)
        assert out["state"] == "HALTED_ACCOUNT_CHANGED" and venue.placed == []
        assert await conn.fetchval("SELECT enabled FROM execmirror_control") is False
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_management_view_shows_paper_beside_live(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        po = await _paper_order(conn, acct, qty=2702)
        venue.behaviour = [{"fill": 3}]
        await mirror.tick(conn)
        await _paper_fill(conn, acct, po, qty=2702)
        v = await V.view(conn)
        o = next(x for x in v["orders"] if x["paper_order_id"] == po["order_id"])
        assert o["expected_scaled_qty"] == 2.702 and o["live"]["qty"] == 3
        assert o["live"]["venue_order_id"] == "v1" and o["live"]["filled_qty"] == 3
        assert v["coverage"]["mirrored"] == 1
        assert v["title"] == "Live execution mirror · 1:1000"
        mk = next(m for m in v["pnl"]["markets"] if m["market"] == po["slug"])
        assert mk["live"]["entry_target_qty"] == 3
        e = mk["comparison"]["explained"]
        assert abs(sum(e.values()) - mk["comparison"]["difference"]) < 1e-5
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# LIVE ELIGIBILITY -- investment policy only, fail closed (owner, 2026-10-03)
# ═════════════════════════════════════════════════════════════════════

def test_only_the_allowlisted_investment_policy_version_is_live_eligible():
    ok, ev = M.live_eligibility({"strategy": "PINNACLE_COMPLETED_GAME_PAPER",
                                 "decision_policy_version": "PINNACLE_COMPLETED_GAME_PAPER_V2",
                                 "role": "ENTRY"})
    assert ok and ev["class"] == "INVESTMENT_POLICY"
    for strategy, cls in (("PINNACLE_EXPLORATION_PAPER", "EXPLORATION_RESEARCH_COST_PAPER_ONLY"),
                          ("PINNACLE_ONLY_PAPER_BENCHMARK", "BENCHMARK_RESEARCH_ONLY"),
                          ("PINNACLE_COMPLETED_GAME_MAKER_PAPER", "EXPERIMENT_NOT_PROMOTED"),
                          ("DEREK_ENTRY_POLICY_V2", "UNKNOWN_STRATEGY"),
                          ("SOMETHING_NEW", "UNKNOWN_STRATEGY"),
                          (None, "UNKNOWN_STRATEGY")):
        ok, ev = M.live_eligibility({"strategy": strategy, "role": "ENTRY",
                                     "decision_policy_version": "ANY"})
        assert not ok and ev["class"] == cls, (strategy, ev)
    # an un-promoted version of the investment policy is not eligible
    for version in ("PINNACLE_COMPLETED_GAME_PAPER_V3", None, ""):
        ok, ev = M.live_eligibility({"strategy": "PINNACLE_COMPLETED_GAME_PAPER",
                                     "decision_policy_version": version, "role": "ENTRY"})
        assert not ok and ev["class"] == "POLICY_VERSION_NOT_PROMOTED"
    # exploration exits are never eligible either
    ok, _ = M.live_eligibility({"strategy": "PINNACLE_EXPLORATION_PAPER", "role": "EXIT"})
    assert not ok


@pg
@pytest.mark.asyncio
async def test_an_exploration_paper_order_and_fill_never_reach_the_live_adapter(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        po = await _paper_order(conn, acct, qty=2702,
                                strategy="PINNACLE_EXPLORATION_PAPER",
                                policy_version="PINNACLE_EXPLORATION_PAPER_V3")
        await _paper_fill(conn, acct, po, qty=2702)
        ex = await _paper_order(conn, acct, role="EXIT", direction="SELL",
                                intent="ORDER_INTENT_SELL_LONG", qty=2702,
                                group=po["group_id"],
                                strategy="PINNACLE_EXPLORATION_PAPER",
                                policy_version="PINNACLE_EXPLORATION_PAPER_V3")
        for _ in range(3):
            await mirror.tick(conn)
        assert venue.placed == []                      # nothing ever sent
        for oid in (po["order_id"], ex["order_id"]):
            r = await _row(conn, oid)
            assert r["state"] == "EXCLUDED", r
            assert r["exclusion"] == M.STRATEGY_NOT_LIVE_ELIGIBLE
            assert r["live_qty"] == 0
            d = json.loads(r["detail"]) if isinstance(r["detail"], str) else r["detail"]
            assert d["live_eligibility"]["class"] == "EXPLORATION_RESEARCH_COST_PAPER_ONLY"
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_unknown_strategy_and_unpromoted_version_fail_closed(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        # (an unknown strategy name cannot even be written: the paper_orders
        # strategy CHECK refuses it; the pure test covers the unknown branch)
        unknown = await _paper_order(conn, acct, qty=2702,
                                     strategy="PINNACLE_ONLY_PAPER_BENCHMARK",
                                     policy_version="PINNACLE_ONLY_PAPER_BENCHMARK_V1")
        v3 = await _paper_order(conn, acct, qty=2702,
                                policy_version="PINNACLE_COMPLETED_GAME_PAPER_V3")
        none = await _paper_order(conn, acct, qty=2702, policy_version=None)
        await mirror.tick(conn)
        assert venue.placed == []
        for oid in (unknown["order_id"], v3["order_id"], none["order_id"]):
            r = await _row(conn, oid)
            assert r["state"] == "EXCLUDED" and r["exclusion"] == M.STRATEGY_NOT_LIVE_ELIGIBLE
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# PRE-SUBMIT REVALIDATION -- a stale intent is excluded, never re-decided
# ═════════════════════════════════════════════════════════════════════

def test_the_intent_age_bound_is_the_collectors_pinnacle_freshness_rule():
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    assert M.MAX_INTENT_AGE_S == LOOP.PINNACLE_MAX_AGE_S == 30.0


@pg
@pytest.mark.asyncio
async def test_an_intent_older_than_the_freshness_rule_is_excluded_not_sent(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch, ago_s=600)
        po = await _paper_order(conn, acct, qty=2702, decided_offset_s=-45.0)
        await mirror.tick(conn)
        assert venue.placed == []
        r = await _row(conn, po["order_id"])
        assert r["state"] == "EXCLUDED" and r["exclusion"] == M.INTENT_STALE
        d = json.loads(r["detail"]) if isinstance(r["detail"], str) else r["detail"]
        rv = d["revalidation"]
        assert rv["refused"] == M.INTENT_STALE and rv["intent_age_s"] > 30.0
        assert rv["max_intent_age_s"] == 30.0
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_paper_order_that_ended_before_submission_is_excluded(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        po = await _paper_order(conn, acct, qty=2702, state="CANCELED")
        await mirror.tick(conn)
        assert venue.placed == []
        r = await _row(conn, po["order_id"])
        assert r["state"] == "EXCLUDED" and r["exclusion"] == M.PAPER_ORDER_ENDED
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_fresh_qualified_intent_is_still_sent_once(monkeypatch):
    conn = await _conn()
    try:
        acct, venue, mirror = await _setup(conn, monkeypatch)
        po = await _paper_order(conn, acct, qty=2702, decided_offset_s=-2.0)
        venue.behaviour = [{"fill": 3}]
        await mirror.tick(conn)
        assert len(venue.placed) == 1
        r = await _row(conn, po["order_id"])
        assert r["state"] in ("FILLED", "OPEN", "PARTIALLY_FILLED", "CANCELLED")
        assert r["exclusion"] is None
    finally:
        await conn.close()
