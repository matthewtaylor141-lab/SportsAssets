"""THE STANDING-ORDER HARNESS: the XH venue with RESTING orders.

A helper, not a test module. It extends `test_xavier_manages_positions_
through_the_scheduled_path` (XH) -- the same account written through the
real writers, the same catalogue, the same substitution of ONLY the venue
transport (`pmus._get_client`), the settlement probe and the book-currency
seam -- with a venue that can REST an order:

  * a GOOD_TILL_DATE / GOOD_TILL_CANCEL create matches what is crossable now
    and rests the remainder (state NEW or PARTIALLY_FILLED, `leavesQuantity`);
  * `fill(order_id, qty)` is a counterparty trading against the rest (an
    execution with the venue's own id); `hide_executions_after` withholds the
    newest executions from the order read (a DELAYED fill report);
  * a cancel moves the order to PENDING_CANCEL (`cancel_mode="PENDING"`) or
    straight to CANCELED (`"IMMEDIATE"`); `finish_cancel` ends a pending one;
  * `expire(order_id)` is the venue's GTD expiry;
  * `order_update(order_id)` builds the private websocket's
    `orderSubscriptionUpdate` message for the newest execution.

Candidates, rankings, decisions, valuations and the standing-order policy's
own evaluation are never substituted. EVERYTHING IS SYNTHETIC.
"""
from __future__ import annotations

import copy
import json
import time

from sportsassets import bettor_xavier_standing_orders as SPO
from tests import test_xavier_manages_positions_through_the_scheduled_path as XH

#: A SECOND HEDGE INSTRUMENT on the same game: BOS -2.5 / NYY +2.5.
SIB2 = "asc-mlb-bos-nyy-2026-10-06-neg-2pt5"
HEDGE_CID = XH.HEDGE_CID                         # NYY +1.5 (SHORT)
HEDGE2_CID = SIB2 + "#ORDER_INTENT_BUY_SHORT"   # NYY +2.5 (SHORT)
APPROVER = "owner@test (SYNTHETIC APPROVAL OF A STANDING ORDER POLICY)"
GTD = "TIME_IN_FORCE_GOOD_TILL_DATE"
GTC = "TIME_IN_FORCE_GOOD_TILL_CANCEL"


class RestingVenue(XH.Venue):
    """XH's venue, plus resting orders and their lifecycle (SYNTHETIC)."""

    def __init__(self, *, books, holdings):
        super().__init__(books=books, holdings=holdings)
        self.cancel_mode = "IMMEDIATE"
        self.raise_after_create = None
        # one market's settlement prose, changed (SYNTHETIC)
        self.prose_by_slug: dict = {}
        self.hide_executions_after: dict = {}   # order id -> n executions shown
        self.cancels: list = []
        v = self
        base_create = self.client.orders.create

        class _Orders(type(self.client.orders)):
            def create(self, params):
                tif = (params or {}).get("tif")
                if tif not in (GTD, GTC):
                    return base_create(params)
                v.sent.append(("create", dict(params)))
                v.creates += 1
                if v.raise_on_create is not None:
                    raise v.raise_on_create
                got = v._rest(params)
                if v.raise_after_create is not None:
                    # THE ORDER REACHED THE VENUE AND THE ANSWER WAS LOST.
                    raise v.raise_after_create
                return got

            def list(self, params=None):
                v.sent.append(("orders.list", dict(params or {})))
                rows = [copy.deepcopy(o["record"]["order"])
                        for o in v.orders.values()
                        if o["record"]["order"]["state"] in (
                            "ORDER_STATE_NEW", "ORDER_STATE_PARTIALLY_FILLED",
                            "ORDER_STATE_PENDING_CANCEL")]
                return {"orders": rows}

            def retrieve(self, order_id):
                v.sent.append(("orders.retrieve", order_id))
                o = v.orders.get(str(order_id))
                if o is None:
                    return None
                rec = copy.deepcopy(o["record"])
                n = v.hide_executions_after.get(str(order_id))
                if n is not None:
                    rec["executions"] = rec["executions"][:n]
                return rec

            def cancel(self, order_id, body=None):
                v.sent.append(("orders.cancel", order_id))
                v.cancels.append(str(order_id))
                o = v.orders.get(str(order_id))
                if o is None:
                    raise RuntimeError("404 no such order")
                od = o["record"]["order"]
                if od["state"] in ("ORDER_STATE_FILLED",
                                   "ORDER_STATE_CANCELED",
                                   "ORDER_STATE_EXPIRED"):
                    return {}
                if v.cancel_mode == "PENDING":
                    od["state"] = "ORDER_STATE_PENDING_CANCEL"
                else:
                    od["state"] = "ORDER_STATE_CANCELED"
                    od["leavesQuantity"] = 0
                return {}

        class _Markets(type(self.client.markets)):
            def list(self, params=None):
                got = super().list(params)
                for row in got.get("markets") or []:
                    if row.get("slug") in v.prose_by_slug:
                        row["description"] = v.prose_by_slug[row["slug"]]
                return got

        class _Client:
            markets = _Markets()
            orders = _Orders()
            portfolio = self.client.portfolio
        self.client = _Client()

    # ── the venue's own lifecycle ────────────────────────────────────
    def _rest(self, params):
        slug = params.get("marketSlug")
        intent = params.get("intent")
        limit = float((params.get("price") or {}).get("value") or 0)
        qty = int(params.get("quantity") or 0)
        b = self.books.get(slug) or {"bids": [], "offers": []}
        if intent == "ORDER_INTENT_BUY_LONG":
            levels = [(p, q) for p, q in b["offers"] if p <= limit + 1e-9]
        else:
            levels = [(p, q) for p, q in b["bids"] if p >= limit - 1e-9]
        take = min(qty, sum(q for _, q in levels))
        oid = "venue-rest-%d" % self.creates
        order = {"id": oid, "marketSlug": slug, "intent": intent,
                 "price": {"value": "%.2f" % limit, "currency": "USD"},
                 "quantity": qty, "cumQuantity": 0, "leavesQuantity": qty,
                 "tif": params.get("tif"),
                 "goodTillTime": params.get("goodTillTime"),
                 "state": "ORDER_STATE_NEW"}
        self.orders[oid] = {"record": {"order": order, "executions": []}}
        execs = [{"id": "vx-%s-new" % oid, "type": "EXECUTION_TYPE_NEW",
                  "lastShares": "0",
                  "order": {"id": oid, "state": "ORDER_STATE_NEW"}}]
        left = take
        for p, q in levels:
            if left <= 0:
                break
            t = min(q, left)
            execs.append(self._execute(oid, t, p))
            left -= t
        return {"id": oid, "executions": copy.deepcopy(execs)}

    def _execute(self, oid, qty, px):
        o = self.orders[oid]["record"]
        od = o["order"]
        n = len(o["executions"]) + 1
        od["cumQuantity"] = int(od["cumQuantity"]) + int(qty)
        od["leavesQuantity"] = int(od["quantity"]) - int(od["cumQuantity"])
        full = od["leavesQuantity"] <= 0
        if od["state"] != "ORDER_STATE_PENDING_CANCEL" or full:
            od["state"] = ("ORDER_STATE_FILLED" if full
                           else "ORDER_STATE_PARTIALLY_FILLED")
        ex = {"id": "vx-%s-%d" % (oid, n), "type": "EXECUTION_TYPE_FILL"
              if full else "EXECUTION_TYPE_PARTIAL_FILL",
              "lastPx": {"value": "%.2f" % float(px), "currency": "USD"},
              "lastShares": str(int(qty)),
              "order": {"id": oid, "state": od["state"],
                        "cumQuantity": od["cumQuantity"],
                        "leavesQuantity": od["leavesQuantity"]}}
        o["executions"].append(ex)
        slug, intent = od["marketSlug"], od["intent"]
        net, cost = self.holdings.get(slug, (0.0, 0.0))
        if intent == "ORDER_INTENT_BUY_SHORT":
            net, cost = net - qty, cost + (1.0 - float(px)) * qty
        else:
            net, cost = net + qty, cost + float(px) * qty
        self.holdings[slug] = (net, cost)
        return copy.deepcopy(ex)

    def fill(self, oid, qty, px=None):
        """A COUNTERPARTY TRADES AGAINST OUR REST at its limit (SYNTHETIC)."""
        od = self.orders[oid]["record"]["order"]
        assert od["state"] in ("ORDER_STATE_NEW",
                               "ORDER_STATE_PARTIALLY_FILLED",
                               "ORDER_STATE_PENDING_CANCEL"), od["state"]
        q = min(int(qty), int(od["leavesQuantity"]))
        return self._execute(oid, q, float(px if px is not None
                                           else od["price"]["value"]))

    def finish_cancel(self, oid):
        od = self.orders[oid]["record"]["order"]
        if od["state"] in ("ORDER_STATE_PENDING_CANCEL",
                           "ORDER_STATE_NEW", "ORDER_STATE_PARTIALLY_FILLED"):
            od["state"] = "ORDER_STATE_CANCELED"
            od["leavesQuantity"] = 0

    def expire(self, oid):
        od = self.orders[oid]["record"]["order"]
        od["state"] = "ORDER_STATE_EXPIRED"
        od["leavesQuantity"] = 0

    def state(self, oid):
        return dict(self.orders[oid]["record"]["order"])

    def resting(self):
        return [o["record"]["order"] for o in self.orders.values()
                if o["record"]["order"]["state"] in (
                    "ORDER_STATE_NEW", "ORDER_STATE_PARTIALLY_FILLED",
                    "ORDER_STATE_PENDING_CANCEL")]

    def rest_creates(self):
        return [p for k, p in self.sent if k == "create"
                and p.get("tif") in (GTD, GTC)]

    def order_update(self, oid, *, execution_index=-1):
        """The private websocket's order update for one execution, in the
        SDK's `orderSubscriptionUpdate` shape."""
        rec = self.orders[oid]["record"]
        ex = copy.deepcopy(rec["executions"][execution_index])
        ex["order"] = copy.deepcopy(rec["order"])
        return {"requestId": "ws-test",
                "subscriptionType": "SUBSCRIPTION_TYPE_ORDER",
                "orderSubscriptionUpdate": {"execution": ex}}

    def order_snapshot(self):
        return {"requestId": "ws-test",
                "subscriptionType": "SUBSCRIPTION_TYPE_ORDER_SNAPSHOT",
                "orderSubscriptionSnapshot": {
                    "orders": [copy.deepcopy(o["record"]["order"])
                               for o in self.orders.values()], "eof": True}}


async def catalogue_two_hedges(conn):
    """XH's catalogue plus the NYY +2.5 run line (a second candidate)."""
    await XH.catalogue(conn)
    for ident, side, abbr, signed, intent in (
            (SIB2 + ":bos", "yes", "bos", "-2.5", "ORDER_INTENT_BUY_LONG"),
            (SIB2 + ":nyy", "no", "nyy", "+2.5", "ORDER_INTENT_BUY_SHORT")):
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, event_title,"
            " market_slug, question, kind, line, side_norm, intent, signed,"
            " team_abbr, team_name, sports_type, game_start)"
            " VALUES ($1,$2,$3,$4,$5,'side',$6,$7,$8,$9,$10,$11,$12,"
            "         now() + interval '3 hours')",
            ident, XH.EVENT, "Boston Red Sox vs. New York Yankees", SIB2,
            "Who will win?", "2.5", side, intent, signed, abbr, abbr,
            "baseball_team_full_game_spread")


def books(*, held_bids=((0.52, 400),), hedge_bid=0.30, hedge2_bid=None,
          other_side_offer=0.90):
    """HOLD wins at these prices: the held side bids 0.52 (an exit barely
    above the basis), the NYY +1.5 costs 0.70 now (1 - 0.30) -- worth less
    than HOLD -- so nothing is taken, and a standing order at the
    protective price rests below the market."""
    out = XH.books(held_bids=list(held_bids), hedge_bid=hedge_bid,
                   other_side_offer=other_side_offer)
    if hedge2_bid is not None:
        out[SIB2] = {"bids": [(hedge2_bid, 500)],
                     "offers": [(other_side_offer, 500)]}
    return out


async def enable_policy(conn, **params) -> dict:
    """THE OWNER'S ACTIVATION of the standing-order policy (a person's write
    through the real writer)."""
    got = await SPO.activate_version(
        conn, version="STANDING-TEST-V1",
        params=dict({"enabled": True}, **params),
        approved_by=APPROVER, created_by="standing-order-test")
    assert got["ok"], got
    return got


async def clean(conn):
    """XH's clean, and this policy's own rows for the account."""
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        for t in ("bettor_standing_order_events",
                  "bettor_standing_capacity_reservations",
                  "bettor_hedge_group_selection",
                  "bettor_standing_order_plans"):
            await conn.execute("DELETE FROM %s WHERE account_id=$1" % t,
                               XH.ACCT)
        await conn.execute(
            "DELETE FROM agent_policy_versions WHERE agent_id='XAVIER' "
            "   AND policy_key=$1", SPO.POLICY_KEY)
    await XH.clean(conn)
    await conn.execute("DELETE FROM us_premap WHERE market_slug=$1", SIB2)


async def start(conn, *, p=0.55, qty=10, price=0.50, two_hedges=True,
                enable=True, **policy):
    """The XH account, catalogue (with the second hedge), held entry,
    approved conditional model and HOLD's probability -- and the owner's
    activation of the standing-order policy."""
    await clean(conn)
    auth = await XH.authorize(conn)
    if two_hedges:
        await catalogue_two_hedges(conn)
    else:
        await XH.catalogue(conn)
    await XH.held_position(conn, qty=qty, price=price)
    from tests import approved_conditional_model as ACM
    await ACM.approve(conn)
    await XH.probability(conn, p=p)
    if enable:
        await enable_policy(conn, **policy)
    return auth


async def plans(conn):
    return [dict(r) for r in await conn.fetch(
        "SELECT * FROM bettor_standing_order_plans WHERE account_id=$1 "
        " ORDER BY created_at", XH.ACCT)]


async def hedge_intents(conn):
    return [dict(r) for r in await conn.fetch(
        "SELECT intent_id, state, us_market_slug, order_intent, "
        "       quantity::float8 AS quantity, venue_order_id, "
        "       coalesce(residual_qty,0)::float8 AS residual, decision_ref "
        "  FROM bettor_funded_intents WHERE account_id=$1 "
        "   AND leg_role='HEDGE' ORDER BY created_at", XH.ACCT)]


async def event_kinds(conn):
    return [r["event_kind"] for r in await conn.fetch(
        "SELECT event_kind FROM bettor_standing_order_events "
        " WHERE account_id=$1 ORDER BY event_id", XH.ACCT)]


async def ledger_hedge_filled(conn):
    return float(await conn.fetchval(
        "SELECT coalesce(sum(f.qty),0)::float8 FROM bettor_funded_fills f "
        "  JOIN bettor_funded_intents i ON i.intent_id=f.intent_id "
        " WHERE i.account_id=$1 AND i.leg_role='HEDGE' "
        "   AND f.direction='ENTRY'", XH.ACCT))


async def group(conn, gid="grp:" + XH.HELD_ID):
    return await SPO.group_state(conn, group_id=gid,
                                 primary_intent_id=XH.HELD_ID)


def standing_step(out):
    s = XH.step_of(out) or {}
    return s.get("standing_order") or {}


def dumps(x):
    return json.dumps(x, default=str, indent=1)[:4000]


def now():
    return time.time()
