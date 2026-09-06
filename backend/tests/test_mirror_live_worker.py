"""MIRROR LIVE, PHASE P1, step 9: the reconciler worker (owner order
2026-09-02, "go for it, let's get this working"). Driven end to end
with fakes -- no venue, no database -- through workers.mirror_live
.tick_once: a stateful pool that holds mirror_books, mirror_orders,
the standing live_orders rows, ingestion_state and the markets in
memory (every worker statement carries an `ml-<name>` tag and is
dispatched on it; the executor's ledger primitives are modelled by
their own text, as tests/test_mirror_live_ledger does), with
acquire()/transaction() context managers that roll the tables back on
a raise; and a stateful venue that rests, fills, cancels and lists
orders by the real adapter's rules (ok means FILLED; a resting GTC is
ok=False with an id).

What is pinned, in the spec's section 7.9 order: SAFE cancels and never
places; exits-only never increases; an unreadable DB switch is
exits-only; every global guard refuses by name and cancels; an
unreadable positions walk / open-orders list / protected set abandons
the tick with nothing placed; the table guard; one open order per book
(the unique index); the 'placing' row with its pre_ids exists BEFORE
submit_fok; the id is persisted before any sleep or cancel; a raised
create runs the fingerprint search with pre_ids, the protected set and
every ledger id excluded and freezes on nothing found; adoption by
fingerprint; trade-log booking by ORDER and exact size only; a cancel
failing twice is 'unknown', frozen, no placement; venue != ledger
freezes and cancels and names the row after three ticks; manual shares
are explained; the wrong-sign trip; a partial fill booked exactly once
across two ticks with a crash between the read and the write; TTL
expiry cancels and re-plans; the take only after the wait AND at or
through, at the same wire, IOC, once; a post-only 400 arms the take
and a 429 does not; post_only_ignored disables the flag; a paired-out
target of 0 rests at max(1 - q, ask) and never markets; a confirmed
vanish rests, then mirror_exit's sole/co-held rules; an unreadable
bid names no_bid_for_flatten; market close cancels, 'closing', then
'closed' on settled; flat + live keeps the row 'filled' at 0; a 429
backs off; ops are capped per tick; a book's ratio does not move when
refresh_ratios changes; a removed whale's book still reduces; every
census key is emitted at least once across this file; the shadow's
own no-orders test still passes; the LOOPS registration. Section 13
pins the step-9 worker review's thirteen findings (owner order
2026-09-02, "go for it, let's get this working"): a trip mid-tick is
cancel-only from there; the admin flatten sells in exits mode; an
unreadable markets read holds, never 'closing'; a closed book's rest
is cancelled and an ops-capped cancel never closes a book; a stale
take arm never takes a fresh rest; a flatten rest of an earlier vanish
never skips the rest-first rule; the flat clock drops while held; the
SELL wire is priced off his unrounded equivalent; a lost CLOSE is
reconciled from the venue's position; every venue read is paced; a
live legacy row of any age refuses admission; a flat book closes on a
confirmed vanish; a refused resting BUY is cancelled under the
refusal's name. The same section pins the re-review's six minors: a
lost order clears the take arm; the first post-only refusal starts
the take clock (the fake's arm keeps the tick's clock and the
worker's COALESCE); the first rest of a vanish starts the slippage
clock through a re-quote; a candidate's unreadable market is
`market_unreadable`; a lost CLOSE is sized off this tick's walk and
refused by name without one; a closed book's rest is cancelled
'closed' whatever froze it. Section 14 pins the residuals that
re-review left (task 7) and the to-a-tee program's Phase 7 rung 1
seam: the take arm's evidence is bounded -- cleared when the book
leaves his level with no rest standing, refused by name past twice
the wait so the book rests first; a CLOSING book's residual rest is
cancelled 'closing', never under its stale freeze; _place hands
take_arms the raw dict, so the venue's 200-REJECTED refusal shape arms
the take and every earlier shape reads as it did.
"""
import ast
import asyncio
import copy
import inspect
import math
import json
import logging
import pathlib
import re
import time

import pytest

from sportsassets import copy_sports, edge_gate, ratelimit
from sportsassets import live_executor as le
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_mirror_shadow import CID, M, N, SLUG, _fill, _nosleep
from tests.test_mirror_shadow import _Pool as _ShadowPool

NOW = time.time()
INTENT = "ORDER_INTENT_BUY_LONG"
BUY, SELL = rules.BUY, rules.SELL
HIS_SLUG = "atp-nakashi-michels-2026-09-02"
GAME_KEY = le._us_game_key(SLUG)


def _flat(s: str) -> str:
    return " ".join(s.split())


def _run(coro):
    return asyncio.run(coro)


class _Unique(Exception):
    """asyncpg's UniqueViolationError as the ledger tests fake it."""


class _Undefined(Exception):
    """asyncpg's UndefinedTableError: the relation does not exist."""


class _UndefinedColumn(Exception):
    """asyncpg's UndefinedColumnError: the column does not exist -- what
    Postgres answers a statement naming mirror_orders.intent before
    migration 050 is applied."""


_Unique.__name__ = "UniqueViolationError"
_Undefined.__name__ = "UndefinedTableError"
_UndefinedColumn.__name__ = "UndefinedColumnError"


def _his(long_size=300.0, long_px=0.31, other_size=0.0, other_px=0.72, sold=0.0):
    """His fills on the fixture market: a BUY of the long token, an
    optional BUY of the other token (his pair completion), an optional
    SELL of the long token."""
    fs = [_fill(M, "BUY", long_size, long_px, NOW - 3000)]
    if other_size:
        fs.append(_fill(N, "BUY", other_size, other_px, NOW - 2000))
    if sold:
        fs.append(_fill(M, "SELL", sold, long_px, NOW - 1000))
    return fs


def _ratio_fills(n=12):
    """Twelve markets with a $50 opening burst each: ratio 1.0."""
    return [{"condition_id": f"c{i}", "asset": f"t{i}", "side": "BUY", "size": 100.0,
             "price": 0.5, "ts": 1000.0 + i} for i in range(n)]


# --------------------------------------------------------------- the pool

class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        p = self.conn.pool
        p.tx_events.append("begin")
        self.snap = copy.deepcopy((p.books, p.orders, p.rows, p.state))
        return self

    async def __aexit__(self, et, ev, tb):
        p = self.conn.pool
        if et is None:
            p.tx_events.append("commit")
        else:
            p.tx_events.append("rollback")
            # restored IN PLACE, row by row, so a test's reference to a
            # book or an order still reads the pool's row after a rollback
            for cur, snap in zip((p.books, p.orders, p.rows), self.snap[:3]):
                for k in list(cur):
                    if k not in snap:
                        del cur[k]
                for k, row in snap.items():
                    if k in cur:
                        cur[k].clear()
                        cur[k].update(row)
                    else:
                        cur[k] = row
            p.state.clear()
            p.state.update(self.snap[3])
        return False


class _Conn:
    def __init__(self, pool):
        self.pool = pool

    def transaction(self):
        return _Tx(self)

    async def fetchrow(self, sql, *a):
        return self.pool._run("fetchrow", sql, a)

    async def fetchval(self, sql, *a):
        return self.pool._run("fetchval", sql, a)

    async def execute(self, sql, *a):
        return self.pool._run("execute", sql, a)


class _Acquire:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return _Conn(self.pool)

    async def __aexit__(self, *exc):
        return False


class _Pool(_ShadowPool):
    """The worker's whole database, in memory."""

    def __init__(self, fills=None, snap=None, snap_at=None, snap_partial=False,
                 ratio_fills=None, conds=None, mapped=True, map_rows=None):
        super().__init__(fills=fills, snap=snap, snap_at=snap_at,
                         whales_ratio_fills=ratio_fills, conds=conds, mapped=mapped)
        self.snap_partial = snap_partial
        self.map_rows = map_rows
        self.books, self.orders, self.rows = {}, {}, {}
        self.state = {"mirror_live": True, "side_echo_last": {"ok": 1}}
        self.markets = {CID: {"closed": False, "resolved": False, "resolved_prices": None}}
        self.token_index = {M: 1, N: 0}
        self.token_cid = {M: CID, N: CID}
        self.whale_address = {"rn1": "0xabc"}
        self.kalshi = set()
        self.manual_shares = {}
        self.shadow = []
        self.reaper_touched = 0
        self.raise_on = []
        self.hide_orders = set()
        self.tables_absent = False
        # THE DATABASE BEFORE MIGRATION 050: every statement that names
        # mirror_orders.intent -- the guard, the day read, the open-orders
        # read, the INSERT -- is UndefinedColumnError, the way Postgres
        # answers it, not the guard alone (an absence faked on the guard
        # only let the 050 day read go out against a column that was
        # not there, unseen: P2 rung S0 review, migration lens)
        self.no_intent_column = False
        self.lost_24h = 0.0
        self.caps = {"day": 0.0, "total": 0.0}
        self.sent = []
        self.tx_events = []
        self.ids = {"book": 40, "order": 700, "row": 900}
        self.clock = NOW          # the tick's clock: what the real INSERT's now() stamps on placed_at

    def acquire(self):
        return _Acquire(self)

    # -- builders --------------------------------------------------------
    @staticmethod
    def _book_dict(bid, ledger=0, target=None, state="live", ratio=1.0, gross_buy=0.0,
                   avg_cost=None, opened_ts=None, standing_row_id=None):
        return {"id": bid, "whale": "rn1", "condition_id": CID, "us_market_slug": SLUG,
                "game_key": GAME_KEY, "long_asset": M, "other_asset": N, "intent": INTENT,
                "map_source": "ledger", "ratio": ratio, "anchor_usd": 50.0,
                "standing_row_id": standing_row_id, "episode": 1, "flat_reopens": 0,
                "state": state, "frozen_reason": None, "frozen_ts": None, "frozen_ticks": 0,
                "target": target, "target_raw": None, "his_net": None, "ledger_net": ledger,
                "venue_net": None, "open_order_id": None, "take_armed_ts": None,
                "last_reason": None, "last_plan": None, "gross_buy_usd": gross_buy,
                "gross_sell_usd": 0.0, "peak_exposure_usd": gross_buy, "avg_cost": avg_cost,
                "realized_pnl": 0.0, "settled_pnl": None, "own_book_pnl": None,
                "settle_disagree": None, "opened_ts": NOW - 600 if opened_ts is None else opened_ts,
                "updated_ts": NOW - 30, "closed_at": None}

    def add_book(self, ledger=0, target=None, state="live", ratio=1.0, gross_buy=None,
                 avg_cost=None, opened_ts=None, standing_status="filled", **over):
        self.ids["book"] += 1
        self.ids["row"] += 1
        bid, rid = self.ids["book"], self.ids["row"]
        gross_buy = (ledger * (avg_cost or 0.31)) if gross_buy is None else gross_buy
        b = self._book_dict(bid, ledger, target, state, ratio, gross_buy,
                            avg_cost or (0.31 if ledger else None), opened_ts, rid)
        b.update(over)
        self.books[bid] = b
        self.rows[rid] = {"id": rid, "status": standing_status, "lane": "mirror",
                          "whale_username": "rn1", "asset": b["long_asset"],
                          "condition_id": b["condition_id"],
                          "us_market_slug": b["us_market_slug"], "order_id": None, "his_price": 0.31,
                          "limit_price": 0.31, "requested_usd": 0.0, "requested_shares": 300.0,
                          "filled_shares": float(ledger), "fill_price": b["avg_cost"],
                          "filled_usd": gross_buy, "orig_shares": float(ledger), "pnl": None,
                          "error": None, "settled_at": None, "placed_ts": b["opened_ts"],
                          "raw": {"lane": "mirror", "preview": {"intent": INTENT},
                                  "mirror": {"book_id": bid, "episode": 1}, "adds": []}}
        return b

    def add_order(self, book, side=BUY, wire=0.30, qty=300, order_id="oid-1", state="open",
                  placed_ts=None, kind=None, booked=0.0, tif="GTC", pre_ids=(), **over):
        self.ids["order"] += 1
        oid = self.ids["order"]
        # the 050 column, as the worker writes it for THIS book and plan
        # side (the wire-side map): a long book's BUY is BUY_LONG, its
        # SELL is SELL_LONG; a short book's SELL is BUY_SHORT, its BUY
        # SELL_SHORT. `intent=` in `over` names it outright
        ws = rules.wire_side(book.get("intent"), side)
        o = {"id": oid, "book_id": book["id"], "whale": book["whale"], "us_market_slug": SLUG,
             "kind": kind or ("increase" if side == BUY else "reduce"), "side": side, "tif": tif,
             "post_only": True, "good_till": None, "his_level": 0.31, "price": wire,
             "wire": wire, "qty": qty, "order_id": order_id, "state": state,
             "venue_state": None, "filled": booked, "booked_filled": booked, "avg_px": None,
             "cash_usd": 0.0, "realized": 0.0, "maker": None, "taker_at_placement": False,
             "pre_ids": list(pre_ids), "target_at_place": None, "ledger_at_place": None,
             "bid_at_place": None, "ask_at_place": None, "reason": None, "receipt": None,
             "placed_ts": NOW - 30 if placed_ts is None else placed_ts, "done_at": None,
             "intent": ws[0] if ws else "ORDER_INTENT_BUY_LONG"}
        o.update(over)
        self.orders[oid] = o
        if state in ("placing", "open", "unknown"):
            book["open_order_id"] = oid
        return o

    def add_row(self, **over):
        """A non-mirror live_orders row (a legacy copy, the desk...)."""
        self.ids["row"] += 1
        rid = self.ids["row"]
        r = {"id": rid, "status": "filled", "lane": None, "whale_username": "swisstony",
             "asset": "tok-x", "us_market_slug": "other-slug", "order_id": None,
             "filled_shares": 10.0, "placed_ts": NOW - 100, "error": None, "raw": {}}
        r.update(over)
        self.rows[rid] = r
        return r

    # -- reads the shadow helpers make ----------------------------------
    async def fetch(self, sql, *a):
        s = _flat(sql)
        if "FROM live_orders WHERE asset = ANY($1::text[])" in s and self.map_rows is not None:
            return list(self.map_rows)
        if "ml-" in s or "mirror_orders" in s or "= 'manual'" in s:
            return self._run("fetch", sql, a)
        return await super().fetch(sql, *a)

    async def fetchval(self, sql, *a):
        if "ingestion_state" in sql and a and str(a[0]).startswith("whale_positions_raw:"):
            if self.snap is None:
                return None
            return json.dumps({"at": self.snap_at, "partial": self.snap_partial,
                               "sizes": self.snap})
        return self._run("fetchval", sql, a)

    async def fetchrow(self, sql, *a):
        return self._run("fetchrow", sql, a)

    async def execute(self, sql, *a):
        return self._run("execute", sql, a)

    # -- the dispatch ----------------------------------------------------
    def _nonterminal(self, book_id):
        return [o for o in self.orders.values()
                if o["book_id"] == book_id and o["state"] in ("placing", "open", "unknown")]

    def _live_row(self, rid):
        r = self.rows.get(rid)
        if r is None or r["status"] != "filled" or r["lane"] != "mirror":
            return None
        return r

    def _run(self, kind, sql, a):  # noqa: C901 — one dispatcher, by tag
        s = _flat(sql)
        self.sent.append((kind, s, a))
        for needle, exc in self.raise_on:
            if needle in s:
                raise exc
        if self.no_intent_column and "mirror_orders" in s and "intent" in s:
            raise _UndefinedColumn('column "intent" does not exist')
        if "ml-table-guard" in s:
            if self.tables_absent:
                raise _Undefined('relation "mirror_books" does not exist')
            return []
        if "SELECT value FROM ingestion_state" in s:
            v = self.state.get(a[0])
            return json.dumps(v) if isinstance(v, (dict, list, bool)) else v
        if "ml-state-write" in s or "INSERT INTO ingestion_state" in s:
            self.state[a[0]] = json.loads(a[1])
            return "INSERT 0 1"
        if "ml-orders-open" in s:
            rows = [dict(o) for o in sorted(self.orders.values(), key=lambda o: (o["placed_ts"], o["id"]))
                    if o["state"] in ("placing", "open", "unknown") and o["id"] not in self.hide_orders]
            # the projection the statement asks for: the 047 shape names
            # no o.intent, so the rows it hands back carry no such key
            if "o.intent" not in s:
                for o in rows:
                    o.pop("intent", None)
            return rows
        if "ml-books-open" in s:
            return [dict(b) for b in sorted(self.books.values(), key=lambda b: (b["updated_ts"], b["id"]))
                    if b["state"] != "closed"]
        if "ml-book-read" in s:
            b = self.books.get(a[0])
            return dict(b) if b else None
        if "ml-books-count" in s:
            return {"live": sum(1 for b in self.books.values() if b["state"] != "closed"),
                    "today": sum(1 for b in self.books.values() if b["opened_ts"] > NOW - 86400)}
        if "ml-mirror-day" in s:
            # READ FROM THE STATEMENT, not restated: the side, the
            # window and the states the CASE clause names are the
            # statement's own text, so a test of the day cap proves the
            # worker's SQL and a narrowed or widened predicate fails the
            # test that pins it. Two columns, as the worker reads them:
            # `filled`, the cash of every such row, and `open`, the
            # unfilled remainder at the wire of the rows in the states
            # the clause names. A CASE clause this fake does not model
            # is an AssertionError, never a silent fall back to cash
            # alone (the first cut's fake did that, and a test against
            # it proved nothing about the clause)
            side = re.search(r"\bside = '([^']*)'", s).group(1)
            hours = int(re.search(r"placed_at > now\(\) - interval '(\d+) hours'", s).group(1))
            # THE 050 SHAPE (P2 rung S0): the predicate names the wire
            # intent beside the plan side -- a long BUY row, or a short
            # OPEN / ADD row (intent BUY_SHORT, plan side SELL_LONG) --
            # and the resting remainder is priced at the collateral a
            # share, `1 - wire` on a BUY_SHORT row. A row this fake
            # holds without the column reads the column's DEFAULT,
            # exactly as Postgres would read a row written before 050
            pred = re.search(r"WHERE \(\(side = '([^']*)' AND intent = '([^']*)'\) "
                             r"OR intent = '([^']*)'\)", s)

            def _intent(o):
                return o.get("intent") or "ORDER_INTENT_BUY_LONG"

            def _in(o):
                if pred is None:
                    return o["side"] == side
                return ((o["side"] == pred.group(1) and _intent(o) == pred.group(2))
                        or _intent(o) == pred.group(3))
            rows = [o for o in self.orders.values()
                    if _in(o) and o["placed_ts"] > self.clock - hours * 3600]
            filled = sum(o["cash_usd"] for o in rows)
            resting = 0.0
            if "CASE" in s:
                m = re.search(r"CASE WHEN state IN \(([^)]*)\) THEN "
                              r"\(qty - COALESCE\(booked_filled, 0\)\) \* "
                              r"(wire|\(CASE WHEN intent = '([^']*)' THEN 1 - wire ELSE wire END\))"
                              r" ELSE 0 END", s)
                assert m, f"ml-mirror-day: a CASE clause this fake does not model: {s}"
                live = {x.strip().strip("'") for x in m.group(1).split(",")}
                short_intent = m.group(3)

                def _px(o):
                    return (1.0 - o["wire"]) if (short_intent and _intent(o) == short_intent) else o["wire"]
                resting = sum((o["qty"] - o["booked_filled"]) * _px(o)
                              for o in rows if o["state"] in live)
            if kind == "fetchrow":
                return {"filled": filled, "open": resting}
            # fetchval is the FIRST column: `filled` when the statement
            # names two, the one sum when it names one
            return filled if " AS filled" in s else filled + resting
        if "ml-loss-sum" in s:
            return {"lost": sum(b["realized_pnl"] for b in self.books.values())
                    + sum(b["settled_pnl"] or 0.0 for b in self.books.values() if b["state"] == "closed"),
                    "books": len(self.books)}
        if "ml-replaces" in s:
            # the reasons and the tifs the statement names, read from
            # its text: `reason = 'replace'` was the original predicate,
            # `reason IN ('replace', 'take') AND tif IN ('GTC', 'GTD')`
            # the pre-flight's amendment
            m = re.search(r"reason IN \(([^)]*)\)", s)
            reasons = ({x.strip().strip("'") for x in m.group(1).split(",")} if m
                       else {re.search(r"reason = '([^']*)'", s).group(1)})
            m = re.search(r"tif IN \(([^)]*)\)", s)
            tifs = {x.strip().strip("'") for x in m.group(1).split(",")} if m else None
            return sum(1 for o in self.orders.values()
                       if o["book_id"] == a[0] and o["reason"] in reasons and o["done_at"]
                       and (tifs is None or o["tif"] in tifs))
        if "ml-flatten-since" in s:
            # the worker's predicate: the FIRST flatten rest of THIS vanish
            # -- still standing or placed at/after the vanish began ($2)
            ts = [o["placed_ts"] for o in self.orders.values()
                  if o["book_id"] == a[0] and o["kind"] == "flatten_vanished" and o["tif"] in ("GTC", "GTD")
                  and (o["state"] in ("placing", "open", "unknown") or o["placed_ts"] >= a[1])]
            return min(ts) if ts else None
        if "ml-order-insert" in s:
            if self._nonterminal(a[0]):
                raise _Unique('duplicate key value violates unique constraint '
                              '"mirror_orders_one_open_per_book"')
            self.ids["order"] += 1
            oid = self.ids["order"]
            self.orders[oid] = {
                "id": oid, "book_id": a[0], "whale": a[1], "us_market_slug": a[2], "kind": a[3],
                "side": a[4], "tif": a[5], "post_only": a[6], "good_till": a[7], "his_level": a[8],
                "price": a[9], "wire": a[10], "qty": a[11], "order_id": None, "state": "placing",
                "venue_state": None, "filled": 0.0, "booked_filled": 0.0, "avg_px": None,
                "cash_usd": 0.0, "realized": 0.0, "maker": None, "taker_at_placement": False,
                "pre_ids": json.loads(a[12]), "target_at_place": a[13], "ledger_at_place": a[14],
                "bid_at_place": a[15], "ask_at_place": a[16], "reason": a[17], "receipt": None,
                "placed_ts": self.clock, "done_at": None,
                # the 050 column: the worker's nineteenth parameter, else
                # the column's DEFAULT (the 047-shaped INSERT)
                "intent": a[18] if len(a) > 18 else "ORDER_INTENT_BUY_LONG"}
            return oid
        if "ml-order-persist" in s:
            o = self.orders[a[0]]
            o.update(order_id=a[1], state="open", venue_state=a[2], receipt=json.loads(a[3]))
            return "UPDATE 1"
        if "ml-order-adopt" in s:
            o = self.orders[a[0]]
            if o["order_id"] is None:
                o.update(order_id=a[1], state="open", reason=a[2])
            return "UPDATE 1"
        if "ml-order-state" in s:
            o = self.orders[a[0]]
            o.update(state=a[1], venue_state=a[2], reason=a[3], maker=a[4])
            if a[5] is not None:
                o["order_id"] = a[5]
            if a[1] in ("filled", "cancelled", "expired", "rejected", "lost"):
                o["done_at"] = NOW
            return "UPDATE 1"
        if "ml-order-cursor" in s:
            o = self.orders[a[0]]
            if abs(float(o["booked_filled"]) - float(a[4])) > 1e-9:
                return "UPDATE 0"
            o.update(booked_filled=o["booked_filled"] + a[1], filled=a[2], avg_px=a[3])
            return "UPDATE 1"
        if "ml-order-cash" in s:
            o = self.orders[a[0]]
            o.update(cash_usd=o["cash_usd"] + a[1], realized=o["realized"] + a[2],
                     taker_at_placement=o["taker_at_placement"] or bool(a[3]))
            return "UPDATE 1"
        if "ml-order-reason" in s:
            self.orders[a[0]]["reason"] = a[1]
            return "UPDATE 1"
        if "ml-order-dust" in s:
            # the worker's statement: the receipt JSON merged with the
            # order's cumulative dust, never a column of its own
            o = self.orders[a[0]]
            o["receipt"] = {**(o.get("receipt") or {}), "dust_total": a[1]}
            return "UPDATE 1"
        if "ml-adds-seq" in s:
            r = self.rows.get(a[0]) or {}
            return sum(1 for x in (r.get("raw") or {}).get("adds", []) if x.get("order_id") == a[1])
        if "ml-standing-read" in s:
            r = self.rows.get(a[0])
            return None if r is None else {k: r.get(k) for k in
                                           ("status", "lane", "filled_shares", "fill_price", "pnl", "raw")}
        if "ml-standing-name" in s:
            r = self._live_row(a[0])
            if r is not None:
                r["raw"].setdefault("mirror", {})["named"] = True
            return "UPDATE 1"
        if "ml-ledger-ids" in s:
            return [{"order_id": r["order_id"]} for r in self.rows.values()
                    if r["us_market_slug"] == a[0] and r["order_id"]]
        if "ml-manual-shares" in s:
            return self.manual_shares.get(a[0], 0.0)
        if "ml-legacy-row" in s:
            # the worker's predicate: a LIVE row of any age, a NAMED error
            # row inside 48 h
            return any((r.get("lane") or "") != "mirror"
                       and (r["asset"] == a[0] or r["us_market_slug"] == a[1]
                            or (a[2] and a[2] in r["us_market_slug"]))
                       and (r["status"] in ("filled", "submitting", "exiting")
                            or (r["status"] == "error" and str(r.get("error") or "").startswith(
                                ("venue holds a POSITION", "ORPHAN FILL RECORDED",
                                 "venue has no record of order"))
                                and r["placed_ts"] > NOW - 48 * 3600))
                       for r in self.rows.values())
        if "ml-slug-recent" in s:
            return any((r.get("lane") or "") != "mirror" and r["us_market_slug"] == a[0]
                       and r["status"] not in ("rejected", "unfilled") and r["placed_ts"] > NOW - 3600
                       for r in self.rows.values())
        if "ml-underdog" in s:
            return any(r.get("whale_username") == "underdog" and r["asset"] in a[0]
                       and r["status"] in ("filled", "submitting", "exiting") for r in self.rows.values())
        if "ml-kalshi" in s:
            return 1 if a[0] in self.kalshi else None
        if "ml-market" in s:
            return self.markets.get(a[0])
        if "ml-token-index" in s:
            return self.token_index.get(a[0])
        if "ml-sibling-token" in s:
            return next((tok for tok, cid in self.token_cid.items()
                         if cid == a[0] and tok != a[1]), None)
        if "ml-whale-address" in s:
            return self.whale_address.get(a[0])
        if "ml-shadow-latest" in s:
            rows = [r for r in self.shadow if r["whale"] == a[0] and r["condition_id"] == a[1]]
            return max(rows, key=lambda r: r["at_ts"]) if rows else None
        if "ml-reaper-touched" in s:
            return self.reaper_touched
        if "ml-book-plan" in s:
            b = self.books[a[0]]
            b.update(target=a[1], target_raw=a[2], his_net=a[3], his_long=a[4], his_other=a[5],
                     snap_long=a[6], snap_other=a[7], drift=a[8], his_level=a[9], venue_net=a[10],
                     last_reason=a[11], last_plan=json.loads(a[12]), updated_ts=NOW)
            return "UPDATE 1"
        if "ml-book-freeze" in s:
            b = self.books[a[0]]
            if b["state"] in ("live", "frozen"):
                b.update(frozen_reason=(b["frozen_reason"] if b["state"] == "frozen" else a[1]),
                         state="frozen", frozen_ts=b["frozen_ts"] or NOW,
                         frozen_ticks=b["frozen_ticks"] + 1, last_reason=a[1])
            return "UPDATE 1"
        if "ml-book-thaw" in s:
            b = self.books[a[0]]
            if b["state"] == "frozen":
                b.update(state="live", frozen_reason=None, frozen_ts=None)
            return "UPDATE 1"
        if "ml-book-state" in s:
            b = self.books[a[0]]
            b.update(state=a[1], last_reason=a[2])
            if a[1] == "closed":
                b["closed_at"] = NOW
            return "UPDATE 1"
        if "ml-book-open-order" in s:
            self.books[a[0]]["open_order_id"] = a[1]
            return "UPDATE 1"
        if "ml-book-ratio" in s:
            self.books[a[0]]["ratio"] = a[1]
            return "UPDATE 1"
        if "ml-book-arm" in s:
            # the worker's COALESCE: an arm already set is kept; a new
            # one is stamped with the tick's clock (the real now()). The
            # fake once stamped the fixture NOW on every arm, which hid
            # the re-stamping the re-review's minor 2 found
            b = self.books[a[0]]
            b["take_armed_ts"] = (b["take_armed_ts"] or self.clock) if a[1] else None
            return "UPDATE 1"
        if "ml-book-ledger-buy" in s:
            self.books[a[0]].update(ledger_net=a[1], avg_cost=a[2], gross_buy_usd=a[3],
                                    peak_exposure_usd=a[4])
            return "UPDATE 1"
        if "ml-book-ledger-sell" in s:
            self.books[a[0]].update(ledger_net=a[1], gross_sell_usd=a[2], realized_pnl=a[3])
            return "UPDATE 1"
        if "ml-book-settled" in s:
            self.books[a[0]].update(settled_pnl=a[1], own_book_pnl=a[2], settle_disagree=a[3],
                                    state="closed", last_reason=a[4], closed_at=NOW)
            return "UPDATE 1"
        if "ml-book-reopens" in s:
            self.books[a[0]]["flat_reopens"] = a[1]
            return "UPDATE 1"
        # -- the executor's own statements ----------------------------
        if "INSERT INTO mirror_books" in s:
            if any(b["whale"] == a[0] and b["us_market_slug"] == a[2] and b["state"] != "closed"
                   for b in self.books.values()):
                raise _Unique('duplicate key value violates unique constraint '
                              '"mirror_books_one_open_per_market"')
            self.ids["book"] += 1
            bid = self.ids["book"]
            episode = 1 + sum(1 for b in self.books.values()
                              if b["whale"] == a[0] and b["us_market_slug"] == a[2])
            b = self._book_dict(bid, ledger=0, ratio=a[8], opened_ts=NOW)
            b.update(whale=a[0], condition_id=a[1], us_market_slug=a[2], game_key=a[3],
                     long_asset=a[4], other_asset=a[5], intent=a[6], map_source=a[7],
                     anchor_usd=a[9], his_level=a[10], target=a[11], episode=episode,
                     updated_ts=NOW)
            self.books[bid] = b
            return {"id": bid, "episode": episode}
        if "INSERT INTO live_orders" in s:
            if any(r["asset"] == a[1] and r["status"] in ("filled", "settled")
                   and r.get("whale_username") not in ("manual", "underdog") for r in self.rows.values()):
                raise _Unique('duplicate key value violates unique constraint '
                              '"live_orders_one_fill_per_asset"')
            self.ids["row"] += 1
            rid = self.ids["row"]
            self.rows[rid] = {"id": rid, "status": "filled", "lane": "mirror", "whale_username": a[0],
                              "asset": a[1], "condition_id": a[2], "us_market_slug": a[3],
                              "order_id": None, "his_price": a[4], "limit_price": a[4],
                              "requested_usd": 0.0, "requested_shares": float(a[5]),
                              "filled_shares": 0.0, "fill_price": None, "filled_usd": 0.0,
                              "orig_shares": 0.0, "pnl": None, "error": None, "settled_at": None,
                              "placed_ts": NOW, "raw": json.loads(a[6])}
            return rid
        if "SET standing_row_id = $2" in s:
            self.books[a[0]]["standing_row_id"] = a[1]
            return "UPDATE 1"
        if "/* mirror-sell */" in s:
            r = self._live_row(a[0])
            return None if r is None else {"fill_price": r["fill_price"],
                                           "filled_shares": r["filled_shares"]}
        if "SET fill_price = CASE" in s:
            rid, q, px, usd, wire_usd, adds_json, oid, seq = a
            r = self._live_row(rid)
            if r is None:
                return None
            adds = list(r["raw"].get("adds") or [])
            if any(x.get("order_id") == oid and x.get("seq") == seq for x in adds):
                return None
            fs = float(r["filled_shares"])
            if fs + q > 0 and px is not None:
                r["fill_price"] = ((r["fill_price"] or 0.0) * fs + px * q) / (fs + q)
            r["filled_shares"] = fs + q
            r["filled_usd"] = (r["filled_usd"] or 0.0) + usd
            r["requested_usd"] = (r["requested_usd"] or 0.0) + wire_usd
            r["orig_shares"] = (r["orig_shares"] if r["orig_shares"] is not None else fs) + q
            r["raw"] = {**r["raw"], "adds": adds + json.loads(adds_json)}
            return {"filled_shares": r["filled_shares"], "fill_price": r["fill_price"],
                    "filled_usd": r["filled_usd"]}
        if "GREATEST(filled_shares - $2::float8, 0)" in s:
            r = self._live_row(a[0])
            if r is None:
                return None
            r["filled_shares"] = max(float(r["filled_shares"]) - a[1], 0.0)
            r["pnl"] = (r["pnl"] or 0.0) + a[2]
            return {"filled_shares": r["filled_shares"], "pnl": r["pnl"]}
        if "SET status='cashed_out'" in s or "SET status='cancelled'" in s:
            r = self._live_row(a[0])
            if r is None or float(r["filled_shares"]) != 0.0:
                return None
            r["status"] = "cashed_out" if "cashed_out" in s else "cancelled"
            if r["status"] == "cancelled":
                r["error"] = a[1]
            return {"id": a[0]}
        if "= 'manual'" in s:
            return [{"order_id": r["order_id"]} for r in self.rows.values()
                    if r.get("whale_username") == "manual" and r["order_id"]]
        if "FROM mirror_orders WHERE order_id IS NOT NULL" in s:
            return [{"order_id": o["order_id"]} for o in self.orders.values() if o["order_id"]]
        if "sum(pnl)" in s:
            return self.lost_24h
        if "NOT IN ('manual', 'underdog')" in s and kind == "fetchrow":
            return dict(self.caps)
        if "SELECT condition_id FROM market_tokens WHERE token_id" in s:
            return self.token_cid.get(a[0])
        return None if kind != "fetch" else []


# -------------------------------------------------------------- the venue

class _Portfolio:
    def __init__(self, held, raise_walk=False):
        self.held, self.raise_walk = held, raise_walk

    def positions(self, q):
        if self.raise_walk:
            raise RuntimeError("429")
        return {"positions": {s: {"netPosition": v} for s, v in self.held.items()},
                "nextCursor": "", "eof": True}


class _Client:
    def __init__(self, portfolio):
        self.portfolio = portfolio


class _Venue:
    """A venue that rests, fills, cancels and lists by the adapter's
    rules: a rest is ok=False with an id; a status read applies any
    scripted fill; a cancel of a done order is refused."""

    def __init__(self, bid=0.30, ask=0.32, held=None, raise_walk=False, raise_bbo=False,
                 open_raises=False, status_raises=False, status_none=False, cancel_ok=True,
                 place=None, place_raises=None, rest_on_raise=False, trades=None,
                 trades_raise=False, close=None, flatten_bid=0.29, extra_open=None,
                 ioc_fill=0.0, fills=None, state="MARKET_STATE_OPEN", states=None):
        self.bid, self.ask = bid, ask
        # the venue's own market state on every quote read (bbo_read),
        # as the venue spells it; `states` overrides it per slug
        self.state, self.states = state, dict(states or {})
        self.portfolio = _Portfolio(held or {}, raise_walk)
        self.raise_bbo, self.open_raises = raise_bbo, open_raises
        self.status_raises, self.status_none, self.cancel_ok = status_raises, status_none, cancel_ok
        self.place, self.place_raises, self.rest_on_raise = place, place_raises, rest_on_raise
        self.trades, self.trades_raise = list(trades or []), trades_raise
        self.close, self.flatten_bid = close, flatten_bid
        self.extra_open = list(extra_open or [])
        self.ioc_fill = ioc_fill
        self.fills = dict(fills or {})        # oid -> (filled, avg)
        self.orders = {}
        self.calls = []
        self.n = 0

    def _get_client(self):
        return _Client(self.portfolio)

    def _bbo_quotes(self, client, slug):
        self.calls.append(("bbo", slug))
        if self.raise_bbo:
            raise RuntimeError("venue down")
        return self.bid, self.ask

    def bbo_read(self, client, slug):
        """pmus.bbo_read's shape: a read on which every feed raised is
        NAMED in `error`, never raised; the state rides beside the
        quotes."""
        self.calls.append(("bbo", slug))
        if self.raise_bbo:
            return {"bid": None, "ask": None, "state": None, "error": "RuntimeError"}
        return {"bid": self.bid, "ask": self.ask,
                "state": self.states.get(slug, self.state), "error": None}

    def rest(self, oid, side="BUY", price=0.30, qty=300, slug=SLUG, created=None, state="new",
             filled=0.0, avg=None):
        self.orders[oid] = {"order_id": oid, "us_market_slug": slug, "side": side, "price": price,
                            "quantity": float(qty), "filled_shares": filled, "avg_px": avg,
                            "state": state, "created_at": NOW if created is None else created,
                            "tif": "GOOD_TILL_CANCEL"}
        return self.orders[oid]

    def _norm(self, o):
        return {**o, "leaves": max(0.0, o["quantity"] - o["filled_shares"])}

    def open_orders(self, slugs=None):
        self.calls.append(("open_orders", slugs))
        if self.open_raises:
            raise RuntimeError("list down")
        out = [self._norm(o) for o in self.orders.values()
               if o["state"] in ("new", "open", "partially_filled")]
        return out + list(self.extra_open)

    def order_status(self, oid):
        self.calls.append(("status", oid))
        if self.status_raises:
            raise RuntimeError("status down")
        if self.status_none or oid not in self.orders:
            return None
        o = self.orders[oid]
        if oid in self.fills:
            f, avg = self.fills[oid]
            o["filled_shares"], o["avg_px"] = f, avg
            if f >= o["quantity"]:
                o["state"] = "filled"
        return self._norm(o)

    def cancel_order(self, oid, slug):
        self.calls.append(("cancel", oid, slug))
        if not self.cancel_ok:
            return {"ok": False, "error": "boom"}
        o = self.orders.get(oid)
        if o is None or o["state"] in ("filled", "cancelled", "canceled", "expired"):
            return {"ok": False, "error": "order is not open"}
        o["state"] = "cancelled"
        return {"ok": True}

    def submit_fok(self, slug, price, qty, sell=False, tif="TIME_IN_FORCE_FILL_OR_KILL",
                   intent=None, post_only=False, good_till=None):
        self.calls.append(("place", slug, price, qty, sell, tif, intent, post_only, good_till))
        self.n += 1
        oid = f"oid-{self.n}"
        # the venue books a BUY_SHORT as ORDER_SIDE_SELL of the contract
        # (short-truth 6/6, 50/50): the resting order's side is what the
        # open-orders read hands back. Every existing caller's shape --
        # no intent, or BUY_LONG -- rests as it did
        venue_side = "SELL" if (sell or intent == "ORDER_INTENT_BUY_SHORT") else "BUY"
        if self.place_raises is not None:
            if self.rest_on_raise:
                self.rest(oid, venue_side, price, qty, slug)
            raise self.place_raises
        if self.place is not None:
            return self.place(self, oid, slug, price, qty, sell, tif, intent, post_only, good_till)
        if tif == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL":
            f = min(float(self.ioc_fill), float(qty))
            return {"ok": f > 0, "order_id": oid, "status": "filled" if f >= qty else "canceled",
                    "fill_price": price if f > 0 else None, "filled_shares": f,
                    "raw": {"response": {"id": oid}}}
        self.rest(oid, venue_side, price, qty, slug)
        return {"ok": False, "order_id": oid, "status": "new", "fill_price": None,
                "filled_shares": 0.0, "raw": {"response": {"id": oid}}}

    def recent_trades(self, slug, since_ts, max_pages=3):
        self.calls.append(("trades", slug, since_ts))
        if self.trades_raise:
            raise RuntimeError("activities down")
        return list(self.trades)

    def close_position(self, slug, *, slippage_bips):
        self.calls.append(("close", slug, slippage_bips))
        if self.close is not None:
            return self.close
        return {"ok": True, "order_id": "close-1", "status": "filled", "fill_price": 0.29,
                "filled_shares": 300.0, "raw": {}}

    def slug_bid(self, slug, long_leg=None):
        """The flatten's bid read. It honours the slug's state (None
        unless OPEN): the real slug_bid reads through _bbo_quotes and
        knows no state, which is exactly why the worker must refuse a
        non-OPEN slug BEFORE it asks for a bid."""
        self.calls.append(("slug_bid", slug, long_leg))
        if self.states.get(slug, self.state) != "MARKET_STATE_OPEN":
            return None
        return self.flatten_bid


class _Http:
    """The data API's `/positions` for THIS market: the rows the whale
    holds on the condition's two tokens. Two callers read it and they
    must be handed one world, not two: `_confirm_gone` (is this leg
    gone?) and, since Phase 1, `market_positions` (what does he hold on
    both tokens?). The default is the fixture market's ordinary state --
    300 of the long token, none of the other -- which is what
    `_pool`'s default fills and default whole-book snapshot both say. A
    test that needs him GONE says so with `_gone()`; one that needs a
    pair says so with its own rows."""

    def __init__(self, rows=None, status=200):
        self.rows = rows if rows is not None else [
            {"conditionId": CID, "asset": M, "size": 300},
            {"conditionId": CID, "asset": N, "size": 0}]
        self.status = status
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        http = self

        class _R:
            status_code = http.status

            def json(self):
                return http.rows
        return _R()


def _mkt(long_size=300.0, other_size=0.0, cid=CID):
    """The data API answering FOR THIS MARKET with both tokens named.
    `sizeThreshold=0`, so a leg he has merged down to nothing comes back
    as a row of size 0 rather than as an absence."""
    return _Http(rows=[{"conditionId": cid, "asset": M, "size": long_size},
                       {"conditionId": cid, "asset": N, "size": other_size}])


def _gone(other=0.0):
    """The venue answering for this market with the long leg at zero:
    what `_confirm_gone` reads as gone and what the per-market read
    reads as a net of `-other`."""
    return _Http(rows=[{"conditionId": CID, "asset": M, "size": 0},
                       {"conditionId": CID, "asset": N, "size": other}])


class _NoThrottle:
    """ratelimit.Throttle with the wait taken out."""

    async def wait(self):
        return None


def _kinds(v):
    return [c[0] for c in v.calls]


def _places(v):
    return [c for c in v.calls if c[0] == "place"]


def _cancels(v):
    return [c for c in v.calls if c[0] == "cancel"]


# ------------------------------------------------------------- fixtures

SEEN: set = set()
_RAN = {"n": 0}


@pytest.fixture(autouse=True)
def _armed(monkeypatch):
    """Every test starts armed for a full tick on the fixture market;
    tests flip what they pin. Records every census name emitted."""
    _RAN["n"] += 1
    _nosleep(monkeypatch)
    monkeypatch.setattr(ml, "pace", lambda s=ms.READ_PACING_S: 0.0)
    # the per-market read waits on the process-wide data-API throttle
    # (whale_exits.market_positions); the wait is real seconds and this
    # file drives hundreds of ticks
    monkeypatch.setattr(ratelimit, "_throttle", _NoThrottle())

    async def _s(s):
        _armed.slept.append(s)
    _armed.slept = []
    monkeypatch.setattr(ml, "_sleep", _s)
    monkeypatch.setenv("PMUS_MIRROR", "on")
    monkeypatch.setenv("PMUS_MIRROR_WHALES", "rn1")
    monkeypatch.setenv("MIRROR_WHALES", "rn1")
    monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "off")
    monkeypatch.setenv("LIVE_VERIFIED_WHALES", "")
    monkeypatch.delenv("PMUS_MIRROR_POST_ONLY", raising=False)
    monkeypatch.delenv("PMUS_MIRROR_GTD", raising=False)
    monkeypatch.setattr(le, "active_venue", lambda: "polymarket-us")
    monkeypatch.setattr(le, "_REST_RESERVED_USD", 0.0)
    # THE FIXTURE WORLD'S RAILS, pinned through the rules module (which
    # the worker reads at call time) so this file is hermetic against
    # the runner's environment AND against the code defaults moving.
    # The world below was built on the P1/P2 rails -- ratio 1.0 for
    # his $93 fixture position, a $1,250 day cap, the short knob off,
    # the short share cap at one -- and every pin in it reads them. The
    # code defaults since 2026-09-06 (U12/U12b/U12c) are 10% above a $10
    # bet, no day cap, shorts on, no short share cap: the rules tests
    # pin THOSE, and the tests in this file that exercise the new rules
    # set them explicitly (section 18). A test that wants the knob on
    # says so (_shorts_on). Every file that imports this fixture is
    # covered with it
    monkeypatch.setattr(rules, "MIRROR_SHORTS", False)
    monkeypatch.setattr(rules, "MIRROR_SHORT_MAX_SHARES", 1)
    monkeypatch.setattr(rules, "MIRROR_RATIO", 1.0)
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1250.0)
    monkeypatch.setitem(edge_gate._cache, "err", None)     # conftest seeds the rest
    monkeypatch.setattr(ml, "_POST_ONLY_OK", True)
    monkeypatch.setattr(ml, "_backoff_until", 0.0)
    monkeypatch.setattr(ml, "_last_tick_at", 0.0)
    # the mode the worker holds for a backed-off tick: module globals
    # that outlive a test, reset so no test inherits another's mode
    monkeypatch.setattr(ml, "_last_mode", None)
    monkeypatch.setattr(ml, "_last_whales", [])
    monkeypatch.setattr(ml, "_unmapped_until", {})
    monkeypatch.setattr(ml, "_BOOK_LOCKS", {})
    monkeypatch.setattr(ms, "_ratio_cache", {"at": 0.0, "by_whale": {}})
    monkeypatch.setattr(ms, "_unmapped_until", {})
    ml._WOKEN.clear()
    ml._MIRROR_CENSUS.clear()
    ml._RECENT.clear()

    async def _held(slug):
        return 300, 0.31
    monkeypatch.setattr(le, "_pm_held", _held)
    orig = ml._mirror_stop

    def _stop(reason, whale=None):
        SEEN.add(ml._family(reason))
        return orig(reason, whale)
    monkeypatch.setattr(ml, "_mirror_stop", _stop)
    yield


def _pool(**kw):
    kw.setdefault("fills", _his())
    kw.setdefault("snap", {M: 300.0, N: 0.0})
    kw.setdefault("snap_at", NOW - 40)
    kw.setdefault("ratio_fills", _ratio_fills())
    return _Pool(**kw)


def _tick(pool, venue, now=NOW, http=None, keep_backoff=False):
    """One tick. Every abandon backs the loop off for BACKOFF_S, so a
    test that drives several ticks at one clock clears it between
    them unless it is the backoff it reads."""
    if not keep_backoff:
        ml._backoff_until = 0.0
    pool.clock = now
    return _run(ml.tick_once(pool, venue, http if http is not None else _Http(), now_ts=now))


def _census(stats, key):
    return stats["census"].get(key, 0)


# ------------------------------------------------ 0. the module contract

def test_module_constants_and_the_census_shape():
    assert ml.POLL_S == 30.0 and ml.WAKE_MIN_GAP_S == 5.0
    src = inspect.getsource(ml)
    # every cap is imported from the rules module, never restated
    for restated in ("MIRROR_REST_TTL_S =", "MIRROR_TAKE_AFTER_S =", "MIRROR_MAX_LIVE_BOOKS =",
                     "MIRROR_DAY_USD =", "MIRROR_NET_CAP_USD =", "MIRROR_MAX_ORDER_OPS_PER_TICK ="):
        assert restated not in src, restated
    # read through the rules module at call time, never bound at import:
    # tests/test_mirror_live_rules reloads that module, and a cap or a
    # dataclass bound here would be the stale one after it
    assert ml.rules is rules and "rules.MIRROR_REST_TTL_S" in src and "rules.AdmissionFacts(" in src
    assert not hasattr(ml, "MIRROR_REST_TTL_S") and not hasattr(ml, "AdmissionFacts")
    assert ml.ORDER_INTENT == "ORDER_INTENT_BUY_LONG"
    # P2 rung S0: the short intent and the ONE knob come from the rules
    # module -- the intent as the same object, the knob read through the
    # module at call time, neither restated here
    assert ml.ORDER_INTENT_SHORT is rules.ORDER_INTENT_SHORT == "ORDER_INTENT_BUY_SHORT"
    assert "rules.MIRROR_SHORTS" in src and "MIRROR_SHORTS =" not in src
    assert not hasattr(ml, "MIRROR_SHORTS")
    stats = ml._new_stats()
    assert set(ml.CENSUS_KEYS) <= set(stats["census"]) and all(v == 0 for v in stats["census"].values())
    assert len(set(ml.CENSUS_KEYS)) == len(ml.CENSUS_KEYS)


def test_notify_is_tolerant_and_the_tick_reads_the_woken_market_first():
    ml.notify(None)
    ml.notify("")
    ml.notify("0xc")
    ml.notify(123)
    assert ml._WOKEN == {"0xc", "123"} and ml._WAKE.is_set()
    p = _pool()
    v = _Venue()
    st = _tick(p, v)
    assert st["woken"] == ["0xc", "123"] and not ml._WOKEN and not ml._WAKE.is_set()


def test_registered_in_loops_after_mirror_shadow_with_the_money_comment():
    launcher = pathlib.Path(ml.__file__).with_name("all.py").read_text()
    assert '("mirror_live", mirror_live.main)' in launcher
    assert launcher.index('("mirror_shadow", mirror_shadow.main)') < launcher.index('("mirror_live", mirror_live.main)')
    assert "MONEY" in launcher.split('("mirror_live", mirror_live.main)')[0].rsplit('("mirror_shadow"', 1)[1]
    assert "mirror_live" in launcher.split("LOOPS")[0], "import missing"


def test_the_shadow_still_never_touches_an_order():
    from tests.test_mirror_shadow import test_the_shadow_never_touches_an_order
    test_the_shadow_never_touches_an_order()


# --------------------------------------------------- 1. SAFE / exits / DB

def test_safe_mode_cancels_every_open_mirror_order_and_never_places(monkeypatch):
    monkeypatch.delenv("PMUS_MIRROR")
    p = _pool()
    b = p.add_book(ledger=0, target=300)
    o = p.add_order(b)
    v = _Venue()
    v.rest("oid-1")
    st = _tick(p, v)
    assert st["mode"] == "safe" and _census(st, "mode_env_off") >= 1
    assert _cancels(v) and not _places(v)
    assert p.orders[o["id"]]["state"] == "cancelled" and b["open_order_id"] is None
    assert "bbo" not in _kinds(v), "a SAFE tick reads no market"
    # nothing open: a SAFE tick touches the venue not at all
    v2 = _Venue()
    st2 = _tick(_pool(), v2)
    assert v2.calls == [] and st2["mode"] == "safe"


def test_exits_only_never_increases_but_reduces(monkeypatch):
    monkeypatch.setenv("PMUS_MIRROR", "exits")
    p = _pool()
    p.add_book(ledger=0, target=None)
    v = _Venue()
    st = _tick(p, v)
    assert st["mode"] == "exits" and not _places(v) and _census(st, "mode_env_off") >= 1
    # he reduced to 100 (snapshot) while we hold 300: the SELL still goes out
    p2 = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    p2.add_book(ledger=300)
    v2 = _Venue(held={SLUG: 300})
    _tick(p2, v2)
    pl = _places(v2)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][3] == 200


def test_db_switch_false_absent_or_unreadable_is_exits_only():
    for state, name in ((False, "mode_db_off"), (None, "mode_db_off"), ("garbage", "mode_db_unreadable")):
        p = _pool()
        p.state["mirror_live"] = state
        b = p.add_book(ledger=0)
        p.add_order(b)
        v = _Venue()
        v.rest("oid-1")
        st = _tick(p, v)
        assert st["mode"] == "exits" and _census(st, name) >= 1, (state, st["census"])
        assert _cancels(v) and not _places(v)          # a resting BUY is cancelled
    p = _pool()
    p.raise_on.append(("SELECT value FROM ingestion_state", RuntimeError("db down")))
    st = _tick(p, _Venue())
    assert st["mode"] == "exits" and _census(st, "mode_db_unreadable") >= 1


def test_db_narrowing_and_demotion_gate_increases_only():
    p = _pool()
    p.state["mirror_live_whales"] = ["someoneelse"]
    st = _tick(p, _Venue())
    assert _census(st, "mode_db_off") >= 1 and not p.books
    p = _pool()
    p.state["mirror_live_whales"] = "garbage"
    st = _tick(p, _Venue())
    assert _census(st, "whales_unreadable") >= 1 and not p.books
    p = _pool()
    p.state["mirror_live_demoted"] = ["rn1"]
    st = _tick(p, _Venue())
    assert _census(st, "demoted") >= 1 and not p.books
    p = _pool()
    p.raise_on.append(("SELECT value FROM ingestion_state", RuntimeError("blip")))
    st = _tick(p, _Venue())
    assert st["mode"] == "exits"


def test_no_venue_armed_is_a_safe_tick(monkeypatch):
    monkeypatch.setattr(le, "active_venue", lambda: None)
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b)
    v = _Venue()
    v.rest("oid-1")
    st = _tick(p, v)
    assert _census(st, "no_venue") >= 1 and st["mode"] == "safe" and _cancels(v) and not _places(v)


# ------------------------------------------------------- 2. the guards G

@pytest.mark.parametrize("arm, name", [
    ("probe", "probe_disabled"), ("halt", "halted"), ("pause", "paused"), ("overspend", "overspend_halt")])
def test_every_global_guard_refuses_by_name_and_cancels(monkeypatch, arm, name):
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b)
    v = _Venue()
    v.rest("oid-1")
    if arm == "probe":
        class _S:
            copy_probe_enabled = False
            live_max_daily_usd = 11000.0
            live_max_total_usd = 1e12
        monkeypatch.setattr(ml, "settings", lambda: _S())
    elif arm == "halt":
        monkeypatch.setenv("LIVE_COPY_HALT", "on")
    elif arm == "pause":
        p.state["live_trading_paused"] = True
    else:
        async def _tripped(pool):
            return {"why": "breach", "at": "2099-01-01T00:00:00Z", "ratio": 5.0}
        monkeypatch.setattr(le, "overspend_halt", _tripped)     # the conftest neutralizes it
    st = _tick(p, v)
    assert _census(st, name) >= 1, st["census"]
    assert _cancels(v) and not _places(v) and "bbo" not in _kinds(v)


@pytest.mark.parametrize("arm, name", [
    ("breaker", "loss_breaker"), ("breaker_unreadable", "loss_breaker_unreadable"),
    ("room", "no_budget_room"), ("room_raises", "no_budget_room"), ("day", "mirror_day_cap"),
    ("stop", "mirror_loss_stop"), ("stop_present", "mirror_loss_stop")])
def test_every_increase_only_guard_refuses_by_name_cancels_buys_and_lets_a_reduce_through(
        monkeypatch, arm, name):
    p = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    b = p.add_book(ledger=300)                       # target 100 < ledger: a reduce
    b2 = p.add_book(ledger=0, us_market_slug="aec-atp-other-2026-09-02", condition_id="0xother")
    p.add_order(b2)                                  # a resting BUY on another book
    v = _Venue(held={SLUG: 300})
    v.rest("oid-1")
    if arm == "breaker":
        p.lost_24h = -1e6
    elif arm == "breaker_unreadable":
        p.raise_on.append(("sum(pnl)", RuntimeError("db")))
    elif arm == "room":
        async def _room(pool, cfg):
            return 0.0, 0.0
        monkeypatch.setattr(le, "_copy_day_room", _room)
    elif arm == "room_raises":
        async def _room(pool, cfg):
            raise RuntimeError("ledger down")
        monkeypatch.setattr(le, "_copy_day_room", _room)
    elif arm == "day":
        p.add_order(b, state="filled", cash_usd=2000.0, order_id="old")
    elif arm == "stop":
        # one dollar past the stop, whatever the stop is ($1,000 since
        # the owner's 2026-09-05 decision; $250 before)
        b["realized_pnl"] = -(rules.MIRROR_LOSS_STOP_USD + 1.0)
    else:
        p.state["mirror_loss_stop"] = {"at": "x"}
    st = _tick(p, v)
    assert _census(st, name) >= 1, st["census"]
    assert ("cancel", "oid-1", SLUG) in v.calls                # the BUY rest is gone
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True, pl                # the reduce still goes out
    if arm == "stop":
        assert p.state["mirror_loss_stop"]["sum"] == -(rules.MIRROR_LOSS_STOP_USD + 1.0)


def test_mirror_flatten_forces_the_vanish_path_on_every_live_book():
    p = _pool()
    p.state["mirror_flatten"] = True
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v)
    assert _census(st, "mirror_flatten") >= 1 and _census(st, "flatten_vanished") >= 1
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][3] == 300
    assert [o for o in p.orders.values() if o["book_id"] == b["id"]][0]["kind"] == "flatten_vanished"


# --------------------------------------------------------- 3. the reads R

def test_positions_none_open_orders_raise_protected_none_abandon_with_nothing_placed():
    p = _pool()
    st = _tick(p, _Venue(raise_walk=True))
    assert st["abandoned"] and _census(st, "positions_unreadable") >= 1 and not p.books
    assert _census(st, "tick_abandoned") >= 1
    p = _pool()
    st = _tick(p, _Venue(open_raises=True))
    assert st["abandoned"] and _census(st, "open_orders_unreadable") >= 1 and not p.books
    p = _pool()
    p.raise_on.append(("= 'manual'", RuntimeError("db")))
    st = _tick(p, _Venue())
    assert st["abandoned"] and _census(st, "protected_ids_unreadable") >= 1 and not p.books
    # the abandon backs off: the next tick inside the window does nothing
    st2 = _tick(_pool(), _Venue(), now=NOW + 1, keep_backoff=True)
    assert st2["skipped_backoff"]


def test_an_unreadable_read_settles_what_is_at_the_venue_before_it_abandons():
    """The three unreadable-read returns used to sit ABOVE step O, so a
    read we could not make left our own live rests standing: unbooked and
    un-TTL'd for the whole backoff. The tick must reconcile first and
    abandon second -- and the abandon must still happen when the
    reconcile itself fails."""
    for venue_kw, name in ((dict(raise_walk=True), "positions_unreadable"),
                           (dict(open_raises=True), "open_orders_unreadable")):
        p = _pool()
        b = p.add_book(ledger=0)
        p.add_order(b, side=BUY, wire=0.31, qty=100, state="open", order_id="oid-1",
                    placed_ts=NOW - 5)
        st = _tick(p, _Venue(**venue_kw))
        assert st["abandoned"] and _census(st, name) >= 1
        assert st.get("orders_open") == 1, "step O ran before the abandon"
        assert "reconcile_skipped" not in st

    # a reconcile that itself fails is named, and the tick still abandons
    p = _pool()
    p.raise_on.append(("ml-orders-open", RuntimeError("db")))
    st = _tick(p, _Venue(raise_walk=True))
    assert st["abandoned"] and _census(st, "positions_unreadable") >= 1
    assert st.get("reconcile_skipped") == "RuntimeError"


def test_tables_absent_refuses_by_name_and_never_crashes():
    p = _pool()
    p.tables_absent = True
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "tables_absent") == 1 and st["status"] == "degraded"
    assert v.calls == [] and not p.books
    src = pathlib.Path(ml.__file__).read_text()
    assert "tables_absent" in src and "mirror_books" in src


# ------------------------------------------------------- 4. placing rules

def test_a_candidate_opens_a_book_and_rests_post_only_gtc_at_his_level_or_the_bid():
    p = _pool()
    v = _Venue(bid=0.30, ask=0.32)
    st = _tick(p, v)
    assert len(p.books) == 1
    b = next(iter(p.books.values()))
    row = p.rows[b["standing_row_id"]]
    assert row["status"] == "filled" and row["lane"] == "mirror" and row["filled_shares"] == 0
    assert b["ratio"] == 1.0 and b["target"] == 300
    pl = _places(v)
    assert len(pl) == 1
    _, slug, price, qty, sell, tif, intent, post_only, good_till = pl[0]
    assert (slug, price, qty, sell) == (SLUG, 0.30, 300, False)
    assert tif == "TIME_IN_FORCE_GOOD_TILL_CANCEL" and intent == INTENT
    assert post_only is True and good_till is None
    assert price <= 0.31 and price <= 0.30, "never above him, never above the bid"
    o = [o for o in p.orders.values() if o["book_id"] == b["id"]][0]
    assert o["state"] == "open" and o["order_id"] == "oid-1" and o["post_only"] is True
    assert _census(st, "rest_placed") == 1 and st["placed_rest"] == 1


def test_one_open_order_per_book_is_the_unique_index():
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b)                       # open, but hidden from the listing: a race
    p.hide_orders.add(o["id"])
    v = _Venue()
    v.rest("oid-1")
    st = _tick(p, v)
    assert not _places(v) and _census(st, "open_order_pending") >= 1
    assert sum(1 for x in p.orders.values() if x["state"] in ("placing", "open", "unknown")) == 1


def test_the_placing_row_with_pre_ids_exists_before_submit_fok():
    p = _pool()
    seen = {}

    def _place(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        rows = [o for o in p.orders.values() if o["state"] == "placing"]
        seen["placing"] = [dict(o) for o in rows]
        v.rest(oid, "BUY", price, qty)
        return {"ok": False, "order_id": oid, "status": "new", "fill_price": None,
                "filled_shares": 0.0, "raw": {}}
    v = _Venue(place=_place, extra_open=[{"order_id": "owner-1", "us_market_slug": SLUG, "side": "BUY",
                                          "price": 0.30, "quantity": 300.0, "filled_shares": 0.0,
                                          "leaves": 300.0, "state": "new", "created_at": NOW - 900}])
    _tick(p, v)
    assert len(seen["placing"]) == 1
    row = seen["placing"][0]
    assert row["order_id"] is None and row["pre_ids"] == ["owner-1"] and row["wire"] == 0.30
    assert row["kind"] == "increase" and row["side"] == BUY and row["tif"] == "GTC"


def test_the_id_is_persisted_immediately_before_any_sleep_or_cancel():
    p = _pool()
    v = _Venue()
    _tick(p, v)
    tags = [s for k, s, a in p.sent if "ml-order-insert" in s or "ml-order-persist" in s]
    assert [("insert" if "insert" in t else "persist") for t in tags] == ["insert", "persist"]
    i_ins = next(i for i, (k, s, a) in enumerate(p.sent) if "ml-order-insert" in s)
    i_per = next(i for i, (k, s, a) in enumerate(p.sent) if "ml-order-persist" in s)
    assert i_per == i_ins + 1, "nothing between the venue's id and its persist"
    assert _armed.slept == [] and not _cancels(v)
    o = next(iter(p.orders.values()))
    assert o["order_id"] == "oid-1" and o["receipt"] == {"response": {"id": "oid-1"}}


def test_a_raised_create_searches_the_book_with_every_exclusion_and_freezes_placement_lost():
    p = _pool()
    p.add_row(us_market_slug=SLUG, order_id="copy-1", status="cashed_out", asset="tok-z",
              placed_ts=NOW - 7200)
    b0 = p.add_book(ledger=0, us_market_slug="aec-atp-zz-2026-09-02", condition_id="0xzz",
                    long_asset="tok-zz", other_asset="tok-zy")
    p.add_order(b0, order_id="mirror-other", state="open")
    fp = {"side": "BUY", "price": 0.30, "quantity": 300.0, "filled_shares": 0.0, "leaves": 300.0,
          "state": "new", "created_at": NOW, "us_market_slug": SLUG}
    v = _Venue(place_raises=TimeoutError("read timed out"),
               extra_open=[{**fp, "order_id": "owner-1"},         # on the book before us: a pre_id
                           {**fp, "order_id": "copy-1"},          # a ledger id on the slug
                           {**fp, "order_id": "mirror-other"}])   # a protected id
    v.rest("mirror-other", slug="aec-atp-zz-2026-09-02")
    st = _tick(p, v)
    b = [x for x in p.books.values() if x["us_market_slug"] == SLUG][0]
    o = [x for x in p.orders.values() if x["book_id"] == b["id"]][0]
    assert o["state"] == "placing" and o["order_id"] is None and "owner-1" in o["pre_ids"]
    assert b["state"] == "frozen" and b["frozen_reason"] == "placement_lost"
    assert _census(st, "placement_lost") >= 1
    assert ("open_orders", [SLUG]) in v.calls, "the book was searched after the raise"
    # next tick: nothing new on that book while its row is non-terminal
    v2 = _Venue(extra_open=v.extra_open)
    st2 = _tick(p, v2, now=NOW + 10)
    assert not _places(v2) and _census(st2, "open_order_pending") >= 1


def test_adoption_by_fingerprint_after_a_lost_response_and_in_step_o():
    p = _pool()
    v = _Venue(place_raises=TimeoutError("read timed out"), rest_on_raise=True)
    st = _tick(p, v)
    o = next(iter(p.orders.values()))
    assert o["state"] == "open" and o["order_id"] == "oid-1" and _census(st, "rest_placed") == 1
    b = next(iter(p.books.values()))
    assert b["state"] == "live"
    # step O: a 'placing' row with no id older than a minute is adopted from the venue's book
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    o2 = p2.add_order(b2, order_id=None, state="placing", placed_ts=NOW - 90, pre_ids=["owner-0"])
    v2 = _Venue()
    v2.rest("venue-7", created=NOW - 85)
    _tick(p2, v2)
    assert p2.orders[o2["id"]]["order_id"] == "venue-7" and p2.orders[o2["id"]]["state"] == "open"
    assert ("status", "venue-7") in v2.calls
    # two matching bids: ambiguous, frozen, nothing adopted
    p3 = _pool()
    b3 = p3.add_book(ledger=0)
    p3.add_order(b3, order_id=None, state="placing", placed_ts=NOW - 90)
    v3 = _Venue()
    v3.rest("a", created=NOW - 85)
    v3.rest("b", created=NOW - 84)
    st3 = _tick(p3, v3)
    assert b3["state"] == "frozen" and b3["frozen_reason"] == "lost_ambiguous"
    assert _census(st3, "lost_ambiguous") >= 1 and not _places(v3)


def test_trade_log_booking_by_order_and_exact_size_only():
    p = _pool()
    p.add_row(us_market_slug=SLUG, order_id="copy-9", status="filled", asset="tok-z")
    b = p.add_book(ledger=0)
    o = p.add_order(b, order_id=None, state="placing", placed_ts=NOW - 90)
    good = {"qty": 300.0, "price": 0.30, "side": "BUY", "ts": NOW - 80, "realized_pnl": 0.0,
            "order_id": "lost-1", "order_qty": 300.0, "order_price": 0.30}
    v = _Venue(trades=[{**good, "order_qty": 250.0, "order_id": "not-ours"},   # wrong order size
                       {**good, "order_id": "copy-9"},                          # a ledger id
                       {**good, "order_id": None},                               # no order named
                       {**good, "side": "SELL", "order_id": "sell-1"},           # wrong side
                       good])
    st = _tick(p, v)
    oo = p.orders[o["id"]]
    assert oo["order_id"] == "lost-1" and oo["state"] == "filled" and oo["booked_filled"] == 300.0
    assert b["ledger_net"] == 300 and p.rows[b["standing_row_id"]]["filled_shares"] == 300.0
    assert _census(st, "filled_rest") == 1
    # nothing in the log: frozen placement_lost; past the window the order is lost
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    o2 = p2.add_order(b2, order_id=None, state="placing", placed_ts=NOW - 30 * 60)
    st2 = _tick(p2, _Venue())
    assert p2.orders[o2["id"]]["state"] == "lost" and _census(st2, "order_lost") == 1
    assert _census(st2, "placement_lost") == 1
    assert b2["state"] == "live", "venue == ledger (nothing filled): the book thaws"
    # the log unreadable: the row is left as it is
    p3 = _pool()
    b3 = p3.add_book(ledger=0)
    o3 = p3.add_order(b3, order_id=None, state="placing", placed_ts=NOW - 90)
    _tick(p3, _Venue(trades_raise=True))
    assert p3.orders[o3["id"]]["state"] == "placing" and b3["state"] == "live"


def test_a_cancel_failing_twice_is_unknown_frozen_and_places_nothing():
    p = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    b = p.add_book(ledger=300)
    p.add_order(b, side=BUY)                 # a BUY that must go: he reduced
    v = _Venue(held={SLUG: 300}, cancel_ok=False)
    v.rest("oid-1")
    st = _tick(p, v)
    assert len(_cancels(v)) == 2 and _kinds(v).count("status") >= 3
    o = next(iter(p.orders.values()))
    assert o["state"] == "unknown" and b["state"] == "frozen" and b["frozen_reason"] == "cancel_pending"
    assert not _places(v) and _census(st, "cancel_pending") == 1
    # an order_status that raises is order_state_unknown, frozen, nothing new
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    p2.add_order(b2)
    st2 = _tick(p2, _Venue(status_raises=True))
    assert b2["frozen_reason"] == "order_state_unknown" and _census(st2, "order_state_unknown") == 1


# ------------------------------------------------------- 5. the freeze P

def test_venue_ledger_disagree_freezes_cancels_and_names_the_row_after_three_ticks():
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b)
    v = _Venue(held={SLUG: 50})
    v.rest("oid-1")
    st = _tick(p, v)
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree"
    assert _cancels(v) and not _places(v) and _census(st, "venue_ledger_disagree") == 1
    assert st["books_live"] == 1
    row = p.rows[b["standing_row_id"]]
    for i in range(3):
        _tick(p, _Venue(held={SLUG: 50}), now=NOW + 30 * (i + 1))
    assert b["frozen_ticks"] == 4 and row["raw"]["mirror"].get("named") is True
    assert row["status"] == "filled" and row["error"] is None, "named, never an error row"
    # agreement thaws it; a stale freeze degrades the heartbeat first
    st_old = _tick(p, _Venue(held={SLUG: 50}), now=NOW + 700)
    assert st_old["status"] == "degraded"
    _tick(p, _Venue(held={}), now=NOW + 800)
    assert b["state"] == "live"


def test_manual_desk_shares_on_the_slug_are_explained_not_frozen():
    p = _pool()
    p.manual_shares[SLUG] = 50.0
    b = p.add_book(ledger=0)
    v = _Venue(held={SLUG: 50})
    _tick(p, v)
    assert b["state"] == "live" and _places(v)


def test_a_wrong_sign_venue_net_trips_mirror_live_off_with_a_receipt():
    p = _pool()
    b = p.add_book(ledger=10)
    p.add_order(b)
    v = _Venue(held={SLUG: -5})
    v.rest("oid-1")
    st = _tick(p, v)
    assert p.state["mirror_live"] is False and p.state["mirror_live_trip"]["why"] == "wrong_sign_trip"
    assert b["state"] == "frozen" and b["frozen_reason"] == "wrong_sign_trip"
    assert _cancels(v) and not _places(v) and _census(st, "wrong_sign_trip") == 1


# ------------------------------------------------------ 6. the booking E

def test_a_partial_fill_delta_is_booked_exactly_once_across_two_ticks_with_a_crash_between():
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b, qty=300)
    v = _Venue(fills={"oid-1": (100.0, 0.30)})
    v.rest("oid-1")
    # tick 1: the write fails between the venue read and the commit
    p.raise_on.append(("ml-book-ledger-buy", RuntimeError("connection reset")))
    st1 = _tick(p, v)
    row = p.rows[b["standing_row_id"]]
    assert p.orders[o["id"]]["booked_filled"] == 0.0 and b["ledger_net"] == 0
    assert row["filled_shares"] == 0.0 and row["raw"]["adds"] == []
    assert p.tx_events[-1] == "rollback" and _census(st1, "write_failed") >= 1
    assert p.orders[o["id"]]["state"] == "unknown", "an unbooked fill is never finalized"
    assert b["frozen_reason"] == "write_failed"
    # tick 2: booked once
    p.raise_on.clear()
    st2 = _tick(p, v, now=NOW + 30)
    assert p.orders[o["id"]]["booked_filled"] == 100.0 and b["ledger_net"] == 100
    assert row["filled_shares"] == 100.0 and len(row["raw"]["adds"]) == 1
    add = row["raw"]["adds"][0]
    assert add["order_id"] == "oid-1" and add["seq"] == 0 and add["usd"] == 30.0 and "ts" in add
    assert add["maker"] is True and b["avg_cost"] == 0.30 and b["gross_buy_usd"] == 30.0
    assert _census(st2, "partial_fill") == 1 and st2["partial_fills"] == 1
    # tick 3: the same reading books nothing more
    v3 = _Venue(fills={"oid-1": (100.0, 0.30)}, held={SLUG: 100})
    v3.orders = v.orders
    _tick(p, v3, now=NOW + 60)
    assert p.orders[o["id"]]["booked_filled"] == 100.0 and len(row["raw"]["adds"]) == 1
    assert b["ledger_net"] == 100 and p.orders[o["id"]]["cash_usd"] == 30.0


def test_a_sell_past_the_ledger_books_the_ledger_freezes_overfill_and_trips_live_off():
    p = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    b = p.add_book(ledger=300)
    o = p.add_order(b, side=SELL, wire=0.33, qty=200, kind="reduce")
    v = _Venue(held={SLUG: 300}, fills={"oid-1": (400.0, 0.33)})
    v.rest("oid-1", "SELL", 0.33, 200)
    st = _tick(p, v)
    assert b["ledger_net"] == 0 and b["frozen_reason"] == "overfill"
    assert p.state["mirror_live"] is False and _census(st, "overfill") == 1
    assert p.rows[b["standing_row_id"]]["filled_shares"] == 0.0
    assert p.orders[o["id"]]["realized"] == pytest.approx((0.33 - 0.31) * 300, abs=1e-6)


def test_a_standing_row_that_is_not_live_freezes_row_not_live():
    p = _pool()
    b = p.add_book(ledger=0, standing_status="exiting")
    st = _tick(p, _Venue())
    assert b["frozen_reason"] == "row_not_live" and _census(st, "row_not_live") == 1


# -------------------------------------------------------- 7. TTL and take

def test_ttl_expiry_cancels_and_replans_a_fresh_rest():
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b, placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)
    v = _Venue()
    v.rest("oid-1")
    st = _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    assert p.orders[o["id"]]["state"] == "cancelled" and _census(st, "cancelled_unfilled") == 1
    pl = _places(v)
    assert len(pl) == 1 and pl[0][2] == 0.30 and st["requotes"] == 1
    new = [x for x in p.orders.values() if x["id"] != o["id"]][0]
    assert new["state"] == "open" and new["order_id"] == "oid-1" and b["open_order_id"] == new["id"]


def test_a_plan_that_moved_replaces_and_a_plan_that_did_not_keeps():
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, wire=0.28)                # rests a cent under the plan's 0.30
    v = _Venue()
    v.rest("oid-1", price=0.28)
    st = _tick(p, v)
    assert _cancels(v) and _places(v) and st["requotes"] == 1
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    p2.add_order(b2, wire=0.30)
    v2 = _Venue()
    v2.rest("oid-1")
    st2 = _tick(p2, v2)
    assert not _cancels(v2) and not _places(v2) and _census(st2, "open_order_pending") == 1


def test_replaces_are_capped_per_hour():
    p = _pool()
    b = p.add_book(ledger=0)
    for _ in range(rules.MIRROR_MAX_REPLACES_PER_HOUR):
        p.add_order(b, state="cancelled", reason="replace", done_at=NOW - 100, order_id=None)
    p.add_order(b, wire=0.28)
    v = _Venue()
    v.rest("oid-1", price=0.28)
    st = _tick(p, v)
    assert _census(st, "replace_capped") == 1 and not _cancels(v) and not _places(v)


def test_the_take_fires_only_after_the_wait_and_at_or_through_at_the_same_wire_ioc_once():
    # not yet waited: no take, whatever the book does
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, placed_ts=NOW - 60)
    v = _Venue(ask=0.30)
    v.rest("oid-1")
    _tick(p, v)
    assert not _cancels(v) and not _places(v)
    # waited, but the market never came to him: held under target
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, placed_ts=NOW - rules.MIRROR_TAKE_AFTER_S - 1)
    v = _Venue(ask=0.32)
    v.rest("oid-1")
    st = _tick(p, v)
    assert not _places(v) and _census(st, "resting_above_level") == 1
    # waited AND at/through: cancel the rest, ONE IOC at the SAME wire
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, placed_ts=NOW - rules.MIRROR_TAKE_AFTER_S - 1)
    v = _Venue(ask=0.30, ioc_fill=300.0)
    v.rest("oid-1")
    st = _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    pl = _places(v)
    assert len(pl) == 1
    _, slug, price, qty, sell, tif, intent, post_only, good_till = pl[0]
    assert (price, qty, sell, tif, post_only) == (0.30, 300, False, "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", False)
    assert intent == INTENT and _census(st, "take_placed") == 1 and _census(st, "filled_take") == 1
    take = [x for x in p.orders.values() if x["kind"] == "take"][0]
    assert take["state"] == "filled" and take["tif"] == "IOC" and take["maker"] is False
    assert b["ledger_net"] == 300 and b["take_armed_ts"] is None
    # once: the next tick is on target, no second take
    v2 = _Venue(ask=0.30, ioc_fill=300.0, held={SLUG: 300})
    st2 = _tick(p, v2, now=NOW + 30)
    assert not _places(v2) and _census(st2, "on_target") == 1


def test_a_post_only_400_arms_the_take_and_a_429_does_not():
    def _reject(code):
        def _place(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
            return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                    "filled_shares": 0.0, "raw": {"status_code": code, "error": f"{code} refused"}}
        return _place
    p = _pool()
    b = p.add_book(ledger=0)
    st = _tick(p, _Venue(place=_reject(400)))
    assert _census(st, "post_only_rejected") == 1 and b["take_armed_ts"] == NOW
    o = next(iter(p.orders.values()))
    assert o["state"] == "rejected" and o["order_id"] is None and not st["abandoned"]
    # the armed take fires after the wait, at or through, as ONE IOC at the wire
    v = _Venue(ask=0.30, ioc_fill=300.0)
    st2 = _tick(p, v, now=NOW + rules.MIRROR_TAKE_AFTER_S + 1)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" and pl[0][2] == 0.30
    assert _census(st2, "take_placed") == 1 and b["take_armed_ts"] is None
    # a 429 arms nothing and backs off
    p3 = _pool()
    b3 = p3.add_book(ledger=0)
    st3 = _tick(p3, _Venue(place=_reject(429)))
    assert b3["take_armed_ts"] is None and st3["abandoned"] and _census(st3, "rate_limited") == 1


def test_post_only_ignored_disables_the_flag_for_the_process():
    def _place(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        assert post_only is True
        return {"ok": True, "order_id": oid, "status": "filled", "fill_price": price,
                "filled_shares": float(qty), "raw": {}}
    p = _pool()
    st = _tick(p, _Venue(place=_place))
    assert _census(st, "post_only_ignored") == 1 and ml._POST_ONLY_OK is False
    assert st["status"] == "degraded" and st["post_only"] is False
    b = next(iter(p.books.values()))
    o = next(iter(p.orders.values()))
    assert b["ledger_net"] == 300 and o["taker_at_placement"] is True and o["state"] == "filled"
    assert _census(st, "filled_take") == 1
    # the next placement in this process carries no flag
    p2 = _pool()
    v2 = _Venue()
    _tick(p2, v2)
    assert _places(v2)[0][7] is False


def test_a_refused_create_and_a_429_in_its_text_back_off():
    def _place(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "preview_mismatch", "fill_price": None,
                "filled_shares": 0.0, "raw": {"error": "HTTP 429 Too Many Requests"}}
    p = _pool()
    st = _tick(p, _Venue(place=_place))
    assert _census(st, "place_refused") == 1 and _census(st, "rate_limited") == 1 and st["abandoned"]
    assert next(iter(p.orders.values()))["state"] == "rejected"
    assert _tick(_pool(), _Venue(), now=NOW + 1, keep_backoff=True)["skipped_backoff"]


# ---------------------------------------------------------- 8. flattens F

def test_paired_out_target_zero_rests_at_max_one_minus_q_or_the_ask_and_never_markets(monkeypatch):
    monkeypatch.setattr(le, "sell_limit_price", lambda *a, **k: pytest.fail("slippage path taken"))
    p = _pool(fills=_his(300, other_size=300, other_px=0.72), snap={M: 300.0, N: 300.0})
    p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32)
    st = _tick(p, v)
    pl = _places(v)
    assert len(pl) == 1
    _, slug, price, qty, sell, tif, intent, post_only, _gt = pl[0]
    assert (price, qty, sell, tif) == (0.32, 300, True, "TIME_IN_FORCE_GOOD_TILL_CANCEL")
    assert price >= 1 - 0.72 and price >= 0.32
    assert "close" not in _kinds(v) and "slug_bid" not in _kinds(v)
    o = next(iter(p.orders.values()))
    assert o["kind"] == "flatten_paired" and _census(st, "flatten_rested") == 1
    # his equivalent above the ask: the rest sits at his equivalent
    p2 = _pool(fills=_his(300, other_size=300, other_px=0.60), snap={M: 300.0, N: 300.0})
    p2.add_book(ledger=300)
    v2 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32)
    _tick(p2, v2)
    assert _places(v2)[0][2] == 0.40
    # unfilled at TTL: cancelled, counted reduce_unfilled, re-quoted
    v3 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32)
    v3.orders = v.orders
    st3 = _tick(p, v3, now=NOW + rules.MIRROR_REST_TTL_S + 1)
    assert _census(st3, "reduce_unfilled") == 1 and _places(v3)


def test_a_confirmed_vanish_rests_then_follows_the_sole_and_coheld_rules(monkeypatch):
    p = _pool(fills=_his(300, sold=300), snap=None)         # gone by fills; no snapshot
    b = p.add_book(ledger=300)
    http = _gone()                                           # the data API: the long leg is 0
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v, http=http)
    assert http.calls and http.calls[0][1]["market"] == CID
    assert _census(st, "flatten_vanished") == 1 and _census(st, "flatten_rested") == 1
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][5] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
    o = next(iter(p.orders.values()))
    assert o["kind"] == "flatten_vanished" and "close" not in _kinds(v)
    # the rest stood MIRROR_FLATTEN_REST_S unfilled: sole holder -> close_position with the bound
    v2 = _Venue(held={SLUG: 300})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + rules.MIRROR_FLATTEN_REST_S + 1, http=http)
    assert ("cancel", "oid-1", SLUG) in v2.calls
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v2.calls and "slug_bid" not in _kinds(v2)
    assert b["ledger_net"] == 0 and st2["flattened"] == 1
    # co-held -- the desk's 200 explained shares beside our 300 -- one IOC at
    # sell_limit_price(bid) for OUR quantity, never close_position
    async def _held(slug):
        return 500, 0.31
    monkeypatch.setattr(le, "_pm_held", _held)
    p3 = _pool(fills=_his(300, sold=300), snap=None)
    p3.manual_shares[SLUG] = 200.0
    b3 = p3.add_book(ledger=300)
    p3.add_order(b3, side=SELL, wire=0.32, kind="flatten_vanished",
                 placed_ts=NOW - rules.MIRROR_FLATTEN_REST_S - 1)
    v3 = _Venue(held={SLUG: 500}, flatten_bid=0.29, ioc_fill=300.0)
    v3.rest("oid-1", "SELL", 0.32, 300, created=NOW - 400)
    _tick(p3, v3, http=_gone())
    assert "close" not in _kinds(v3) and ("slug_bid", SLUG, True) in v3.calls
    ioc = [c for c in _places(v3) if c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert len(ioc) == 1 and ioc[0][2] == le.sell_limit_price(0.29) and ioc[0][3] == 300 and ioc[0][4] is True
    assert b3["ledger_net"] == 0


def test_a_vanish_the_data_api_will_not_confirm_is_treated_as_paired():
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v, http=_Http(status=500))
    assert _census(st, "vanish_unconfirmed") == 1 and _census(st, "flatten_vanished") == 0
    assert next(iter(p.orders.values()))["kind"] == "flatten_paired" and b["ledger_net"] == 300


def test_an_unreadable_bid_names_no_bid_for_flatten(monkeypatch):
    async def _held(slug):
        return 500, 0.31
    monkeypatch.setattr(le, "_pm_held", _held)
    p = _pool(fills=_his(300, sold=300), snap=None)
    p.manual_shares[SLUG] = 200.0
    b = p.add_book(ledger=300)
    p.add_order(b, side=SELL, wire=0.32, kind="flatten_vanished",
                placed_ts=NOW - rules.MIRROR_FLATTEN_REST_S - 1)
    v = _Venue(held={SLUG: 500}, flatten_bid=None)
    v.rest("oid-1", "SELL", 0.32, 300, created=NOW - 400)
    st = _tick(p, v, http=_gone())
    assert _census(st, "no_bid_for_flatten") == 1
    assert not [c for c in _places(v) if c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert "close" not in _kinds(v) and b["ledger_net"] == 300


# ------------------------------------------------------------ 9. close M

def test_market_close_cancels_marks_closing_then_closed_on_settled_with_the_cross_check():
    p = _pool()
    b = p.add_book(ledger=300)
    p.add_order(b, side=SELL, wire=0.33, qty=100, kind="reduce")
    p.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    v = _Venue(held={SLUG: 300})
    v.rest("oid-1", "SELL", 0.33, 100)
    st = _tick(p, v)
    assert _cancels(v) and not _places(v) and b["state"] == "closing"
    assert _census(st, "market_closed") == 1 and "bbo" not in _kinds(v)
    # the venue settles the standing row: the book closes, its own figure checked
    row = p.rows[b["standing_row_id"]]
    row.update(status="settled", pnl=300 * (1.0 - 0.31))
    p.markets[CID] = {"closed": True, "resolved": True, "resolved_prices": [0, 1]}
    st2 = _tick(p, _Venue(held={SLUG: 300}), now=NOW + 60)
    assert b["state"] == "closed" and b["settled_pnl"] == pytest.approx(207.0)
    assert b["own_book_pnl"] == pytest.approx(207.0) and b["settle_disagree"] is False
    assert st2["closed_books"] == 1
    # a venue figure the book cannot reproduce is named
    p3 = _pool()
    b3 = p3.add_book(ledger=300)
    p3.rows[b3["standing_row_id"]].update(status="settled", pnl=150.0)
    p3.markets[CID] = {"closed": True, "resolved": True, "resolved_prices": [0, 1]}
    st3 = _tick(p3, _Venue(held={SLUG: 300}))
    assert b3["settle_disagree"] is True and _census(st3, "book_settle_disagree") == 1
    # a never-filled book on a closed market closes 'cancelled' and frees the claim
    p4 = _pool()
    b4 = p4.add_book(ledger=0, gross_buy=0.0)
    p4.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    st4 = _tick(p4, _Venue())
    assert b4["state"] == "closed" and p4.rows[b4["standing_row_id"]]["status"] == "cancelled"
    assert _census(st4, "closed_cancelled") == 1
    assert p4.rows[b4["standing_row_id"]]["error"] == le.MIRROR_NO_FILL_CLOSE_TEXT


def test_flat_and_live_keeps_the_row_filled_at_zero_until_the_flat_close():
    p = _pool(fills=_his(300, other_size=300), snap={M: 300.0, N: 300.0})   # paired out: target 0
    b = p.add_book(ledger=0, gross_buy=93.0, avg_cost=0.31)
    row = p.rows[b["standing_row_id"]]
    v = _Venue()
    st = _tick(p, v)
    assert row["status"] == "filled" and row["filled_shares"] == 0.0 and b["state"] == "live"
    assert not _places(v) and _census(st, "on_target") == 1
    assert b["last_plan"]["flat_since"] == NOW and b["last_plan"]["close"] == "not_due"
    _tick(p, _Venue(), now=NOW + rules.MIRROR_FLAT_CLOSE_S - 1)
    assert row["status"] == "filled" and b["state"] == "live"
    st3 = _tick(p, _Venue(), now=NOW + rules.MIRROR_FLAT_CLOSE_S + 1)
    assert row["status"] == "cashed_out" and b["state"] == "closed"
    assert _census(st3, "closed_cashed_out") == 1 and st3["closed_books"] == 1
    # he re-leans on the still-live market: a NEW book, episode 2, counted as a reopen
    p.fills = _his(600)
    p.snap = {M: 600.0, N: 0.0}
    v4 = _Venue()
    _tick(p, v4, now=NOW + rules.MIRROR_FLAT_CLOSE_S + 60)
    new = [x for x in p.books.values() if x["state"] != "closed"]
    assert len(new) == 1 and new[0]["episode"] == 2 and new[0]["flat_reopens"] == 1
    assert _places(v4)


# ------------------------------------------------ 10. caps and the ratio

def test_ops_are_capped_per_tick(monkeypatch):
    monkeypatch.delenv("PMUS_MIRROR")
    p = _pool()
    v = _Venue()
    for i in range(rules.MIRROR_MAX_ORDER_OPS_PER_TICK + 2):
        b = p.add_book(ledger=0, us_market_slug=f"aec-atp-b{i}-2026-09-02", condition_id=f"0x{i}")
        p.add_order(b, order_id=f"o{i}", us_market_slug=f"aec-atp-b{i}-2026-09-02")
        v.rest(f"o{i}", slug=f"aec-atp-b{i}-2026-09-02")
    st = _tick(p, v)
    assert len(_cancels(v)) == rules.MIRROR_MAX_ORDER_OPS_PER_TICK
    assert _census(st, "ops_capped") == 2 and st["ops"] == rules.MIRROR_MAX_ORDER_OPS_PER_TICK


def test_the_room_scales_the_quantity_and_names_over_room(monkeypatch):
    """The rooms scale the rest and name `over_room` under one share
    (U12b: the sleeve's DAILY room no longer binds the mirror -- see
    section 18 -- so the sub-share room here is the mirror's own day
    room, lowered from the environment, and the scaling room is the
    sleeve's TOTAL)."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 0.1)
    p = _pool()
    st = _tick(p, _Venue())
    assert _census(st, "over_room") == 1 and not [o for o in p.orders.values()]
    assert _census(st, "mirror_day_cap") == 0, "nothing filled: the block is not set, the room is"
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1250.0)

    async def _room2(pool, cfg):
        return 1e9, 30.0
    monkeypatch.setattr(le, "_copy_day_room", _room2)
    p2 = _pool()
    v2 = _Venue()
    _tick(p2, v2)
    assert _places(v2)[0][3] == 100                # $30 of room at 0.30 is 100 shares


def test_a_books_ratio_does_not_move_when_refresh_ratios_changes():
    p = _pool()
    b = p.add_book(ledger=0, ratio=0.5)
    v = _Venue()
    _tick(p, v)
    assert b["target"] == 150 and _places(v)[0][3] == 150      # 0.5 x 300, not the fresh 1.0


def test_a_removed_whales_book_still_reduces_and_never_increases(monkeypatch):
    monkeypatch.setenv("PMUS_MIRROR_WHALES", "")
    p = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][3] == 200
    p2 = _pool()
    p2.add_book(ledger=0)
    v2 = _Venue()
    st2 = _tick(p2, v2)
    assert not _places(v2) and _census(st2, "mode_env_off") >= 1


def test_the_drift_rule_refuses_increases_and_reduces_from_the_smaller_reading():
    # fresh and drifted, an increase wanted: refused by name
    p = _pool(fills=_his(300), snap={M: 200.0, N: 0.0})
    p.add_book(ledger=100)
    v = _Venue(held={SLUG: 100})
    st = _tick(p, v, http=_Http(status=500))     # no per-market read: the walk carries it
    assert _census(st, "drift") >= 1 and not _places(v)
    # fresh and drifted, a reduction: sized from the SMALLER reading (200, not 300)
    p = _pool(fills=_his(300), snap={M: 200.0, N: 0.0})
    p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v, http=_Http(status=500))           # again: the whole-book rule
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][3] == 100
    # stale: no increase; a reduce proceeds on derived data
    p2 = _pool(fills=_his(300), snap={M: 300.0, N: 0.0}, snap_at=NOW - 900)
    p2.add_book(ledger=0)
    st2 = _tick(p2, _Venue(), http=_Http(status=500))
    assert _census(st2, "snapshot_stale") >= 1 and not p2.orders


def test_the_shadow_live_instrument_names_an_arithmetic_divergence_only():
    p = _pool()
    p.add_book(ledger=0, ratio=1.0)
    # the shadow's STATED raw (299) diverges from its own 1.0 x 300: named
    p.shadow.append(_shadow_row(299, 1.0, 300.0, raw=299.0))
    st = _tick(p, _Venue())
    assert _census(st, "shadow_live_disagree") == 1
    p2 = _pool()
    p2.add_book(ledger=0, ratio=0.5)
    p2.shadow.append(_shadow_row(300, 1.0, 300.0))
    st2 = _tick(p2, _Venue())
    assert _census(st2, "shadow_live_disagree") == 0, "300 at 1.0 scaled to the 0.5 book is 150: agrees"
    # a different NET is still not compared
    p3 = _pool()
    p3.add_book(ledger=0, ratio=1.0)
    p3.shadow.append(_shadow_row(299, 1.0, 299.0))
    st3 = _tick(p3, _Venue())
    assert _census(st3, "shadow_live_disagree") == 0, "different inputs are not compared"


def _shadow_row(target, ratio, his_net, raw=None, capped=False, at_ts=None):
    """A mirror_shadow row as the live read hands it back: the shadow's
    stated raw defaults to ratio x net (its own arithmetic)."""
    if raw is None and isinstance(ratio, (int, float)) and not isinstance(ratio, bool):
        raw = ratio * his_net
    return {"whale": "rn1", "condition_id": CID, "target": target, "ratio": ratio,
            "his_net": his_net, "target_raw": raw, "capped": capped,
            "at_ts": NOW - 10 if at_ts is None else at_ts}


def test_the_shadow_live_instrument_scales_the_shadows_target_to_the_books_ratio(monkeypatch):
    """Review of U12c, FIX-3 and FIX-3b: compared only at equal ratios
    a 0.10 book was never checked; compared on capped or truncated
    OUTPUTS ordinary books were falsely named (a P2 integrity counter).
    The comparison is on RAW arithmetic scaled to the book's ratio,
    then the live cap and truncation. 2,400 at 1.0 against a 0.10 book
    at 240 agrees; a shadow whose stated raw diverges from its own
    ratio x net (3,000 against 2,400) is named; a CAPPED shadow (his
    24,000 sh @ 0.50: 5,000 at 1.0) against the uncapped 0.10 book at
    2,400 agrees; a shadow at 0.058 whose target truncated to 0
    against an exact-copy book at 16 agrees; a row without the fields
    is skipped by name, never a disagree."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_his(2400), snap={M: 2400.0, N: 0.0})
    b = p.add_book(ledger=0, ratio=0.10)
    p.shadow.append(_shadow_row(2400, 1.0, 2400.0))
    st = _tick(p, _Venue(), http=_mkt(2400.0, 0.0))
    assert b["target"] == 240 and _census(st, "shadow_live_disagree") == 0
    assert _census(st, "shadow_check_skipped") == 0
    # the shadow's arithmetic off: a stated raw of 3,000 against its own
    # 1.0 x 2,400 scales to 300 against the live 240 -- named
    p2 = _pool(fills=_his(2400), snap={M: 2400.0, N: 0.0})
    b2 = p2.add_book(ledger=0, ratio=0.10)
    p2.shadow.append(_shadow_row(3000, 1.0, 2400.0, raw=3000.0))
    st2 = _tick(p2, _Venue(), http=_mkt(2400.0, 0.0))
    assert b2["target"] == 240 and _census(st2, "shadow_live_disagree") == 1
    assert any(x["what"] == "shadow_live_disagree" and x.get("expected") == 300 for x in ml._RECENT)
    # (a) the CAPPED shadow: his 24,000 sh @ 0.50 at ratio 1.0 is 5,000
    # ($2,500), stored raw 5000.0 and capped True; the 0.10 book is
    # 2,400 uncapped ($1,200) -- the shadow's raw is 1.0 x 24,000, and
    # 2,400 agrees
    p3 = _pool(fills=_his(24000, long_px=0.50), snap={M: 24000.0, N: 0.0})
    b3 = p3.add_book(ledger=0, ratio=0.10, avg_cost=0.50)
    p3.shadow.append(_shadow_row(5000, 1.0, 24000.0, raw=5000.0, capped=True))
    st3 = _tick(p3, _Venue(bid=0.49, ask=0.51), http=_mkt(24000.0, 0.0))
    assert b3["target"] == 2400 and _census(st3, "shadow_live_disagree") == 0, st3["census"]
    # (b) the exact-copy book: his 16 sh @ 0.50, the shadow at 0.058
    # truncated its 0.928 to 0; the book at 1.0 targets 16 -- agrees
    p4 = _pool(fills=_his(16, long_px=0.50), snap={M: 16.0, N: 0.0})
    b4 = p4.add_book(ledger=16, ratio=1.0, avg_cost=0.50)
    p4.shadow.append(_shadow_row(0, 0.058, 16.0, raw=0.928))
    st4 = _tick(p4, _Venue(bid=0.49, ask=0.51, held={SLUG: 16}), http=_mkt(16.0, 0.0))
    assert b4["target"] == 16 and _census(st4, "shadow_live_disagree") == 0, st4["census"]
    # the same cap the live book applies: a 0.10 book on his 240,000
    # against a CAPPED 0.058 shadow (13,920 raw, stored 5,000) -- the
    # scaled raw is 24,000, capped to 5,000 like the live target, agrees
    p5 = _pool(fills=_his(240000, long_px=0.50), snap={M: 240000.0, N: 0.0})
    b5 = p5.add_book(ledger=0, ratio=0.10, avg_cost=0.50)
    p5.shadow.append(_shadow_row(5000, 0.058, 240000.0, raw=5000.0, capped=True))
    st5 = _tick(p5, _Venue(bid=0.49, ask=0.51), http=_mkt(240000.0, 0.0))
    assert b5["target"] == 5000 and _census(st5, "shadow_live_disagree") == 0, st5["census"]
    # a row that lacks the fields, or a ratio that is not positive, is
    # SKIPPED by name -- never a disagree
    for row in (dict(_shadow_row(3000, 1.0, 2400.0), target_raw=None),
                dict(_shadow_row(3000, 1.0, 2400.0), capped=None),
                _shadow_row(3000, None, 2400.0), _shadow_row(3000, 0.0, 2400.0),
                _shadow_row(3000, -1.0, 2400.0), _shadow_row(3000, "x", 2400.0)):
        p6 = _pool(fills=_his(2400), snap={M: 2400.0, N: 0.0})
        p6.add_book(ledger=0, ratio=0.10)
        p6.shadow.append(row)
        st6 = _tick(p6, _Venue(), http=_mkt(2400.0, 0.0))
        assert _census(st6, "shadow_live_disagree") == 0, row
        assert _census(st6, "shadow_check_skipped") == 1 and st6["integ"]["shadow_check_skipped"] == 1, row
    # a different net is not compared, silently, as before
    p7 = _pool(fills=_his(2400), snap={M: 2400.0, N: 0.0})
    p7.add_book(ledger=0, ratio=0.10)
    p7.shadow.append(_shadow_row(3000, 1.0, 3000.0))
    st7 = _tick(p7, _Venue(), http=_mkt(2400.0, 0.0))
    assert _census(st7, "shadow_live_disagree") == 0 and _census(st7, "shadow_check_skipped") == 0
    # the short door is the live one: knob off, the shadow's negative
    # raw against a live 0 agrees
    p8 = _pool(fills=_his(100, other_size=400, other_px=0.72), snap={M: 100.0, N: 400.0})
    b8 = p8.add_book(ledger=0, ratio=1.0)
    p8.shadow.append(_shadow_row(-300, 1.0, -300.0))
    st8 = _tick(p8, _Venue(), http=_mkt(100.0, 400.0))
    assert b8["target"] == 0 and _census(st8, "shadow_live_disagree") == 0
    assert "shadow_live_disagree" in rules.P2_INTEGRITY_COUNTERS


def test_the_reaper_isolation_instrument_reads_zero_and_names_a_touch():
    p = _pool()
    st = _tick(p, _Venue())
    assert st["reaper_touched_mirror"] == 0
    p.reaper_touched = 1
    st2 = _tick(p, _Venue())
    assert _census(st2, "reaper_touched_mirror") == 1 and st2["status"] == "degraded"


# ------------------------------------------------------- 11. admission A

@pytest.mark.parametrize("arm, name", [
    ("family", "family"), ("per_side", "per_side_unsupported"), ("closed", "market_closed"),
    ("far", "game_too_far_out"), ("mapping", "mapping"), ("edge", "edge_gate"), ("cell", "cell_gate"),
    ("clip", "clip_zero"), ("legacy", "legacy_row"), ("recent", "slug_recent_copy"),
    ("underdog", "underdog_coholds"), ("venue", "venue_already_holds"), ("kalshi", "kalshi_claimed"),
    ("band", "side_band"), ("stale", "snapshot_stale"), ("drift", "drift"), ("max", "max_books"),
    ("count_unreadable", "books_unreadable"),
    ("first", "first_fill_gate"), ("asset", "asset_claimed"), ("exists", "book_exists"),
    ("unmapped", "unmapped"), ("ratio", "no_ratio"), ("quote", "no_quote"), ("level", "no_price"),
    ("short", "short_side_refused")])
def test_every_admission_clause_refuses_a_new_book_by_name(monkeypatch, arm, name):
    kw = {}
    v = _Venue()
    if arm == "family":
        # a crypto-kind slug: the one family the grammar names that is
        # never a book (owner decision 2026-09-05 admitted every SPORTS
        # family, totals included -- a tsc- slug no longer refuses here)
        kw["map_rows"] = [{"asset": M, "us_market_slug": "cpc-btc-100k-2026-09-02", "intent": INTENT}]
    elif arm == "per_side":
        kw["map_rows"] = [{"asset": M, "us_market_slug": "aec-atp-a-2026-09-02", "intent": INTENT},
                          {"asset": N, "us_market_slug": "aec-atp-b-2026-09-02", "intent": INTENT}]
    elif arm == "far":
        kw["map_rows"] = [{"asset": M, "us_market_slug": "aec-atp-a-b-2030-01-01", "intent": INTENT}]
    elif arm == "mapping":
        monkeypatch.setenv("LIVE_MAPPING_QUARANTINE", "on")
    elif arm == "edge":
        monkeypatch.setattr(edge_gate, "verdict", lambda w: (False, "unfunded"))
    elif arm == "cell":
        monkeypatch.setattr(copy_sports, "copy_verdict", lambda *a, **k: "cell_not_allowed")
    elif arm == "clip":
        monkeypatch.setattr(le, "per_fill_usd", lambda *a, **k: 0.0)
    elif arm == "venue":
        v = _Venue(held={SLUG: 5})
    elif arm == "band":
        monkeypatch.setenv("LIVE_SIDE_PRICE_BAND", "0.15")      # the conftest widens it to 2.0
        v = _Venue(bid=0.58, ask=0.60)
    elif arm == "stale":
        kw["snap_at"] = NOW - 900
    elif arm == "drift":
        kw["snap"] = {M: 200.0, N: 0.0}
    elif arm == "max":
        # a FINITE cap, lowered (the default is unbounded since U12)
        monkeypatch.setattr(rules, "MIRROR_MAX_LIVE_BOOKS", 0.0)
    elif arm == "count_unreadable":
        # U12: the count itself unreadable refuses by its own name,
        # with no cap in force -- fail closed, never "no cap"
        assert rules.MIRROR_MAX_LIVE_BOOKS == rules.MIRROR_MAX_BOOKS_PER_DAY == math.inf
        kw["_count_raises"] = True
    elif arm == "unmapped":
        kw["mapped"] = False
    elif arm == "ratio":
        # the live ratio is a constant since U12b (open_ratio): too few
        # markets for the shadow's reading no longer refuses a book, and
        # `no_ratio` can fire only on a non-finite or non-positive constant
        kw["ratio_fills"] = _ratio_fills(3)
        monkeypatch.setattr(rules, "MIRROR_RATIO", 0.0)
    elif arm == "quote":
        v = _Venue(raise_bbo=True)
    elif arm == "level":
        kw["fills"] = [_fill(M, "BUY", 300.0, 0.0, NOW - 3000)]
    elif arm == "short":
        kw["fills"] = [_fill(N, "BUY", 300.0, 0.72, NOW - 3000)]
        kw["snap"] = {M: 0.0, N: 300.0}
    count_raises = kw.pop("_count_raises", False)
    p = _pool(**kw)
    if count_raises:
        p.raise_on.append(("ml-books-count", RuntimeError("count down")))
    if arm == "closed":
        p.markets[CID]["closed"] = True
    elif arm == "legacy":
        p.add_row(asset=M, status="filled")
    elif arm == "recent":
        p.add_row(us_market_slug=SLUG, status="rejected")
        p.add_row(us_market_slug=SLUG, status="cashed_out")
    elif arm == "underdog":
        p.add_row(asset=N, whale_username="underdog", status="filled")
    elif arm == "kalshi":
        p.kalshi.add(M)
    elif arm == "first":
        p.state["side_echo_last"] = {"ok": 0}
    elif arm == "asset":
        p.raise_on.append(("INSERT INTO live_orders", _Unique("live_orders_one_fill_per_asset")))
    elif arm == "exists":
        p.raise_on.append(("INSERT INTO mirror_books", _Unique("mirror_books_one_open_per_market")))
    # THE TWO WHOLE-BOOK ARMS. `snapshot_stale` and `drift` are clauses
    # about the WALK, and P1's admission clause reads either sight of him
    # -- a fresh per-market read satisfies it on its own -- so these two
    # arms are driven with the data API down. That is the clause working,
    # not a hole: every other arm keeps the default fresh read.
    http = _Http(status=500) if arm in ("stale", "drift") else None
    if arm == "short":
        http = _mkt(0.0, 300.0)                # the venue agrees: he is on the other side
    st = _tick(p, v, http=http)
    assert _census(st, name) >= 1, (arm, st["census"])
    assert not [b for b in p.books.values() if b["state"] != "closed"] and not _places(v)
    if arm in ("asset", "exists"):
        assert p.tx_events and p.tx_events[-1] == "rollback"


def _many_books(p, n):
    """`n` live books on `n` distinct markets, each with its own token
    pair (the ledger's one-fill-per-asset rule would otherwise name the
    candidate `asset_claimed`), beside the fixture candidate. His
    fixture fills are on M alone, so on these markets he holds nothing
    and a ledger of 0 is on target: each book costs the tick one quote
    read and places no order. Returns their slugs."""
    slugs = []
    for i in range(n):
        cid, slug = f"0xbook{i}", f"aec-atp-p{i}-q{i}-2026-09-06"
        p.markets[cid] = {"closed": False, "resolved": False, "resolved_prices": None}
        p.add_book(ledger=0, target=0, condition_id=cid, us_market_slug=slug,
                   long_asset=f"tokL{i}", other_asset=f"tokO{i}", game_key=le._us_game_key(slug))
        slugs.append(slug)
    return slugs


def test_the_read_budget_is_the_candidates_own_so_live_books_never_cap_new_ones():
    """U12 (owner order 2026-09-06: "I don't want to cap books opened at
    all"). Every live book is walked first and each quote read charged
    `t.reads`; candidates then ran only while `t.reads` was under
    MAX_MARKETS_PER_TICK (20). With 20+ live books no candidate was
    ever read: a count cap by another road. Twenty-five live books,
    one candidate: every book is read (exits must be managed), the
    candidate is read too, a twenty-sixth book opens, and the tick is
    not `capped_tick`. `t.reads` stays the total for the stats line;
    `tick_s` is the wall time an operator watches as books grow."""
    assert ms.MAX_MARKETS_PER_TICK == 20
    p = _pool()
    slugs = _many_books(p, 25)
    v = _Venue()
    st = _tick(p, v)
    bbos = [c[1] for c in v.calls if c[0] == "bbo"]
    # 25 book reads, the candidate's, and the new book's own plan read;
    # `books_live` counts the new book too, ticked in the same tick
    assert st["books_live"] == 26 and len(bbos) == 27 and st["reads"] == 27
    assert all(s in bbos for s in slugs), "every live book read, unbounded"
    assert SLUG in bbos, "the candidate was read behind 25 books"
    assert st.get("capped_tick") is not True and not st["abandoned"]
    assert len(p.books) == 26 and [c[1] for c in _places(v)] == [SLUG], (
        "and it opened a book", {k: n for k, n in st["census"].items() if n}, st["recent"][-3:])
    assert _census(st, "max_books") == 0 and _census(st, "books_unreadable") == 0
    assert _census(st, "on_target") >= 25, "the 25 books planned nothing and cost one read each"
    assert _census(st, "ops_capped") == 0 and st["ops"] == 1, "one placement: the new book's rest"
    # the candidates' OWN budget still binds, under the same name: 20
    # candidates read behind the 25 books, the twenty-first not
    p2 = _pool(conds=[f"c{i}" for i in range(21)])
    _many_books(p2, 25)
    v2 = _Venue()
    st2 = _tick(p2, v2)
    assert st2["capped_tick"] is True and st2["reads"] == 25 + 20
    # the tick's wall time is published, 1 dp, on every tick
    assert isinstance(st["tick_s"], float) and st["tick_s"] >= 0.0 and st["tick_s"] == round(st["tick_s"], 1)
    assert "tick_s" in ml._new_stats() and ml._new_stats()["tick_s"] is None
    # the tick's own counters say what was charged where
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW, stats=ml._new_stats())
    _run(ml._bbo(t, SLUG, book=True))
    _run(ml._bbo(t, SLUG))
    assert (t.reads, t.cand_reads) == (2, 1), "a book's read is never charged to the candidates"
    src = inspect.getsource(ml._tick)
    assert "t.cand_reads >= ms.MAX_MARKETS_PER_TICK" in src and "t.reads >= ms.MAX_MARKETS_PER_TICK" not in src
    assert ms.MAX_MARKETS_PER_TICK == 20, "the budget itself is not raised"


def test_the_starred_clauses_are_rechecked_on_every_increase(monkeypatch):
    p = _pool()
    b = p.add_book(ledger=0)
    monkeypatch.setattr(edge_gate, "verdict", lambda w: (False, "unfunded"))
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "edge_gate") == 1 and not _places(v) and b["last_reason"].startswith("edge_gate:")
    monkeypatch.setattr(edge_gate, "verdict", lambda w: (True, "ok"))
    monkeypatch.setattr(le, "per_fill_usd", lambda *a, **k: 0.0)
    st2 = _tick(p, _Venue(), now=NOW + 30)
    assert _census(st2, "clip_zero") == 1 and b["ratio"] == 1.0, "a clip cut never re-rates"


def test_step_m_runs_before_the_plan_so_a_closing_book_never_increases():
    p = _pool()
    b = p.add_book(ledger=0, state="closing")
    v = _Venue()
    _tick(p, v)
    assert not _places(v) and "bbo" not in _kinds(v) and b["state"] == "closing"
    src = inspect.getsource(ml._tick_book)
    assert src.index("STEP M BEFORE ANY PLAN") < src.index("rules.mirror_target(")


def test_the_dead_bands_and_hysteresis_are_named(monkeypatch):
    # the dollar band is $0 since 2026-09-06 (U12c: small bets copy
    # whole), so a 1-share move on a 301-share target is refused by the
    # 2% hysteresis, not the band; the band's own name is driven under
    # an explicit $5, where a 10-share ($3.10) move clears hysteresis
    # (6.2 shares) and is banded
    assert ml.mi.MIN_MOVE_USD == 0.0
    p = _pool(fills=_his(301), snap={M: 301.0, N: 0.0})
    p.add_book(ledger=300)
    st = _tick(p, _Venue(held={SLUG: 300}), http=_mkt(301.0, 0.0))
    assert _census(st, "hysteresis") == 1 and _census(st, "dead_band") == 0
    monkeypatch.setattr(ml.mi, "MIN_MOVE_USD", 5.0)
    p2 = _pool(fills=_his(310), snap={M: 310.0, N: 0.0})
    p2.add_book(ledger=300)
    st2 = _tick(p2, _Venue(held={SLUG: 300}), http=_mkt(310.0, 0.0))
    assert _census(st2, "dead_band") == 1 and _census(st2, "hysteresis") == 0
    monkeypatch.setattr(ml.mi, "MIN_MOVE_USD", 0.0)
    p3 = _pool(fills=_his(600), snap={M: 600.0, N: 0.0})
    p3.add_book(ledger=0)
    st3 = _tick(p3, _Venue(bid=None, ask=0.32), http=_mkt(600.0, 0.0))
    assert _census(st3, "no_price") == 1 and not p3.orders


def test_an_expired_gtd_rest_and_a_plan_write_failure_are_named():
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, tif="GTD")
    v = _Venue()
    v.rest("oid-1", state="expired")
    st = _tick(p, v)
    assert _census(st, "expired") == 1 and next(iter(p.orders.values()))["state"] == "expired"
    p2 = _pool()
    p2.add_book(ledger=0)
    p2.raise_on.append(("ml-book-plan", RuntimeError("db")))
    st2 = _tick(p2, _Venue())
    assert _census(st2, "book_error") == 1 and st2["status"] == "ok"


def test_gtd_and_post_only_env_switches(monkeypatch):
    monkeypatch.setenv("PMUS_MIRROR_GTD", "on")
    monkeypatch.setenv("PMUS_MIRROR_POST_ONLY", "off")
    p = _pool()
    v = _Venue()
    _tick(p, v)
    pl = _places(v)[0]
    assert pl[7] is False and pl[8] is not None and pl[8].endswith("Z")
    assert next(iter(p.orders.values()))["tif"] == "GTD"


# ------------------------------------------ 13. the step-9 review's findings

_OTHER = dict(us_market_slug="aec-atp-other-2026-09-02", condition_id="0xother",
              long_asset="tok-o1", other_asset="tok-o2")
_ZZ = dict(us_market_slug="aec-atp-zz-2026-09-02", condition_id="0xzz",
           long_asset="tok-zz", other_asset="tok-zy")
_LIVE = {"closed": False, "resolved": False, "resolved_prices": None}


def test_a_trip_mid_tick_makes_the_rest_of_the_tick_cancel_only():
    """MAJOR A. A wrong-sign trip on the first book planned, a BUY plan
    and a resting BUY (kept by step O) on the second, and the fixture
    market a candidate with a target of 300: nothing is placed, no book
    opens, every open order is cancelled under the trip's name."""
    p = _pool()
    a = p.add_book(ledger=10, updated_ts=NOW - 100, **_ZZ)
    b = p.add_book(ledger=0, updated_ts=NOW - 50, **_OTHER)
    p.markets["0xzz"] = dict(_LIVE)
    p.markets["0xother"] = dict(_LIVE)
    ob = p.add_order(b, order_id="oid-b", us_market_slug=_OTHER["us_market_slug"])
    v = _Venue(held={_ZZ["us_market_slug"]: -5})
    v.rest("oid-b", slug=_OTHER["us_market_slug"])
    st = _tick(p, v)
    assert p.state["mirror_live"] is False and _census(st, "wrong_sign_trip") == 1
    assert a["frozen_reason"] == "wrong_sign_trip"
    assert not _places(v), "a placement in the tick that tripped live off"
    assert len(p.books) == 2, "a candidate opened a book after the trip"
    assert ("cancel", "oid-b", _OTHER["us_market_slug"]) in v.calls
    assert p.orders[ob["id"]]["state"] == "cancelled" and p.orders[ob["id"]]["reason"] == "wrong_sign_trip"
    assert not [o for o in p.orders.values() if o["state"] in ("placing", "open", "unknown")]
    # an OVERFILL in step O: book B's older rest was reconciled (kept)
    # before book A's sale past the ledger tripped live off
    p = _pool()
    a = p.add_book(ledger=300, **_ZZ)
    b = p.add_book(ledger=0, **_OTHER)
    p.markets["0xzz"] = dict(_LIVE)
    p.markets["0xother"] = dict(_LIVE)
    p.add_order(a, side=SELL, wire=0.33, qty=200, kind="reduce", order_id="oid-a",
                us_market_slug=_ZZ["us_market_slug"], placed_ts=NOW - 30)
    ob = p.add_order(b, order_id="oid-b", us_market_slug=_OTHER["us_market_slug"], placed_ts=NOW - 60)
    v = _Venue(held={_ZZ["us_market_slug"]: 300}, fills={"oid-a": (400.0, 0.33)})
    v.rest("oid-a", "SELL", 0.33, 200, slug=_ZZ["us_market_slug"])
    v.rest("oid-b", slug=_OTHER["us_market_slug"])
    st = _tick(p, v)
    assert p.state["mirror_live"] is False and _census(st, "overfill") == 1
    assert a["frozen_reason"] == "overfill" and a["ledger_net"] == 0
    assert not _places(v) and len(p.books) == 2 and "bbo" not in _kinds(v), "a book was planned after the trip"
    assert p.orders[ob["id"]]["state"] == "cancelled" and p.orders[ob["id"]]["reason"] == "overfill"
    assert not [o for o in p.orders.values() if o["state"] in ("placing", "open", "unknown")]


def test_the_admin_flatten_lever_sells_in_exits_mode(monkeypatch):
    """MAJOR B. With the DB switch false, absent or malformed -- the
    state every trip leaves -- or PMUS_MIRROR=exits, mirror_flatten is
    read and the held book's SELL goes out."""
    for arm in (False, None, "garbage", "env"):
        if arm == "env":
            monkeypatch.setenv("PMUS_MIRROR", "exits")
        p = _pool()
        if arm != "env":
            p.state["mirror_live"] = arm
        p.state["mirror_flatten"] = True
        b = p.add_book(ledger=300)
        v = _Venue(held={SLUG: 300})
        st = _tick(p, v)
        assert st["mode"] == "exits" and _census(st, "mirror_flatten") == 1, (arm, st["census"])
        pl = _places(v)
        assert len(pl) == 1 and pl[0][4] is True and pl[0][3] == 300, arm
        o = next(x for x in p.orders.values() if x["book_id"] == b["id"])
        assert o["kind"] == "flatten_vanished" and _census(st, "flatten_vanished") == 1


def test_an_unreadable_market_read_cancels_holds_and_never_makes_the_book_closing():
    """MAJOR C. He reduced to 100 while we hold 300; the markets read
    raises for one tick: the rest is cancelled by name, the book HELD
    with no plan and never 'closing'; the next readable tick sells."""
    p = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    b = p.add_book(ledger=300)
    o = p.add_order(b, side=SELL, wire=0.33, qty=100, kind="reduce")
    p.raise_on.append(("ml-market", RuntimeError("blip")))
    v = _Venue(held={SLUG: 300})
    v.rest("oid-1", "SELL", 0.33, 100)
    st = _tick(p, v)
    assert b["state"] == "live" and b["last_reason"] == "market_unreadable"
    assert _census(st, "market_unreadable") == 1 and _census(st, "market_closed") == 0
    assert _cancels(v) == [("cancel", "oid-1", SLUG)] and p.orders[o["id"]]["reason"] == "market_unreadable"
    assert not _places(v) and "bbo" not in _kinds(v) and b["last_plan"]["kind"] == "no_plan"
    p.raise_on.clear()
    v2 = _Venue(held={SLUG: 300})
    _tick(p, v2, now=NOW + 30)
    pl = _places(v2)
    assert b["state"] == "live" and len(pl) == 1 and pl[0][4] is True and pl[0][3] == 200
    # the row absent, and a reading that is not False, are the same refusal
    for mk in (None, {"closed": None, "resolved": False, "resolved_prices": None}):
        p3 = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
        b3 = p3.add_book(ledger=300)
        if mk is None:
            del p3.markets[CID]
        else:
            p3.markets[CID] = mk
        st3 = _tick(p3, _Venue(held={SLUG: 300}))
        assert b3["state"] == "live" and _census(st3, "market_unreadable") == 1 and not p3.orders, mk
    # a POSITIVE reading still closes (test_market_close_... pins the rest)
    p4 = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    b4 = p4.add_book(ledger=300)
    p4.markets[CID] = {"closed": False, "resolved": True, "resolved_prices": [0, 1]}
    st4 = _tick(p4, _Venue(held={SLUG: 300}))
    assert b4["state"] == "closing" and _census(st4, "market_closed") == 1


def test_an_open_order_on_a_closed_book_is_cancelled_and_an_ops_capped_cancel_never_closes_a_book(monkeypatch):
    """minor 1. A rest on a CLOSED book is cancelled under that name;
    a cancel the ops budget refused leaves the order in place, so the
    settled path does not close the book over it -- the next tick's
    budget cancels, then closes."""
    p = _pool()
    b = p.add_book(ledger=0, state="closed", standing_status="cashed_out")
    o = p.add_order(b)
    v = _Venue()
    v.rest("oid-1")
    _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    assert p.orders[o["id"]]["state"] == "cancelled" and p.orders[o["id"]]["reason"] == "closed"
    # the closed book stays closed with nothing on it; the still-live
    # market is a candidate again (episode 2), which is its own business
    assert b["state"] == "closed" and not [x for x in p.orders.values()
                                           if x["book_id"] == b["id"] and x["state"] != "cancelled"]
    # frozen, then closed (the re-review's minor 6): the settle and the
    # episode close never clear frozen_reason, so the cancel's name is
    # the book's state, never the stale freeze
    p = _pool()
    b = p.add_book(ledger=0, state="closed", standing_status="cashed_out",
                   frozen_reason="venue_ledger_disagree", frozen_ts=NOW - 600, frozen_ticks=4)
    o = p.add_order(b)
    v = _Venue()
    v.rest("oid-1")
    _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    assert p.orders[o["id"]]["state"] == "cancelled" and p.orders[o["id"]]["reason"] == "closed"
    # a book still FROZEN cancels under the freeze's name, as before
    p = _pool()
    b = p.add_book(ledger=0, state="frozen", frozen_reason="placement_lost", frozen_ts=NOW - 60)
    o = p.add_order(b)
    v = _Venue()
    v.rest("oid-1")
    _tick(p, v)
    assert p.orders[o["id"]]["state"] == "cancelled" and p.orders[o["id"]]["reason"] == "placement_lost"
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 1)
    p = _pool()
    a = p.add_book(ledger=0, updated_ts=NOW - 100)
    p.add_order(a, order_id="oid-a", placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)   # the one op: a TTL cancel
    b = p.add_book(ledger=0, standing_status="settled", updated_ts=NOW - 10, **_ZZ)
    p.markets["0xzz"] = {"closed": True, "resolved": True, "resolved_prices": [0, 1]}
    ob = p.add_order(b, order_id="oid-b", placed_ts=NOW - 30, us_market_slug=_ZZ["us_market_slug"])
    v = _Venue()
    v.rest("oid-a")
    v.rest("oid-b", slug=_ZZ["us_market_slug"])
    st = _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-a", SLUG)] and _census(st, "ops_capped") >= 1
    assert b["state"] != "closed" and p.orders[ob["id"]]["state"] == "open", "closed over a resting order"
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 6)
    v2 = _Venue()
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30)
    assert ("cancel", "oid-b", _ZZ["us_market_slug"]) in v2.calls
    assert p.orders[ob["id"]]["state"] == "cancelled" and b["state"] == "closed" and st2["closed_books"] == 1


def test_a_stale_take_arm_never_takes_a_fresh_rest_and_is_cleared_by_a_rest_or_a_finish():
    """minor 2. An hour-old arm and a rest placed 5 s ago with the ask
    at the wire: no IOC before MIRROR_TAKE_AFTER_S of the REST; a rest
    the venue accepts, and an order that finishes, clear the arm."""
    p = _pool()
    b = p.add_book(ledger=0, take_armed_ts=NOW - 3600)
    o = p.add_order(b, placed_ts=NOW - 5)
    v = _Venue(bid=0.30, ask=0.30, ioc_fill=300)
    v.rest("oid-1")
    st = _tick(p, v)
    assert not _places(v) and not _cancels(v) and _census(st, "open_order_pending") == 1
    assert p.orders[o["id"]]["state"] == "open"
    v2 = _Venue(bid=0.30, ask=0.30, ioc_fill=300)
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW - 5 + rules.MIRROR_TAKE_AFTER_S + 1)
    ioc = [c for c in _places(v2) if c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert len(ioc) == 1 and _census(st2, "take_placed") == 1 and b["take_armed_ts"] is None
    p2 = _pool()
    b2 = p2.add_book(ledger=0, take_armed_ts=NOW - 30)
    v3 = _Venue()
    _tick(p2, v3)
    assert _places(v3)[0][5] == "TIME_IN_FORCE_GOOD_TILL_CANCEL" and b2["take_armed_ts"] is None
    p3 = _pool()
    b3 = p3.add_book(ledger=0, take_armed_ts=NOW - 30)
    o3 = p3.add_order(b3)
    v4 = _Venue(fills={"oid-1": (300.0, 0.30)}, held={SLUG: 300})
    v4.rest("oid-1")
    _tick(p3, v4)
    assert p3.orders[o3["id"]]["state"] == "filled" and b3["take_armed_ts"] is None
    # a LOST placement clears the arm too (the re-review's minor 1): an
    # hour-old arm and a 'placing' row past the window with nothing on
    # the book -> 'lost', the book thaws, a GTC rest goes out at the
    # wire the ask sits on, never an IOC
    p4 = _pool()
    b4 = p4.add_book(ledger=0, take_armed_ts=NOW - 3600)
    o4 = p4.add_order(b4, order_id=None, state="placing", placed_ts=NOW - le._LOST_FILL_WINDOW_S - 61)
    v5 = _Venue(bid=0.30, ask=0.30, ioc_fill=300)
    st5 = _tick(p4, v5)
    assert p4.orders[o4["id"]]["state"] == "lost" and _census(st5, "order_lost") == 1
    assert b4["take_armed_ts"] is None and b4["state"] == "live"
    assert [c[5] for c in _places(v5)] == ["TIME_IN_FORCE_GOOD_TILL_CANCEL"] and _places(v5)[0][2] == 0.30
    assert _census(st5, "take_placed") == 0 and _census(st5, "rest_placed") == 1 and b4["ledger_net"] == 0


def test_the_first_post_only_refusal_starts_the_take_clock_under_the_thirty_second_poll():
    """re-review minor 2. A book that keeps crossing at the 30 s poll:
    every post-only 400 once re-stamped the arm, and _act reads the
    arm before it re-places, so the arm was always 30 s old and the
    take never fired. The FIRST refusal starts the clock: no IOC
    before MIRROR_TAKE_AFTER_S of it, then exactly one, at the same
    wire, and the book is on target."""
    def _reject(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        if tif == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL":
            return {"ok": True, "order_id": oid, "status": "filled", "fill_price": price,
                    "filled_shares": float(qty), "raw": {}}
        return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                "filled_shares": 0.0, "raw": {"status_code": 400, "error": "400 crossing"}}
    p = _pool()
    b = p.add_book(ledger=0)
    wait = float(rules.MIRROR_TAKE_AFTER_S)
    iocs = []
    for i in range(int(wait // 30) + 3):
        now = NOW + 30 * i
        # the venue holds what the ledger holds (venue == ledger after
        # the take fills, so the last ticks read on_target, not a freeze)
        v = _Venue(bid=0.30, ask=0.30, place=_reject, held={SLUG: int(b["ledger_net"])})
        st = _tick(p, v, now=now)
        ioc = [c for c in _places(v) if c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
        if now - NOW < wait:
            assert not ioc and b["take_armed_ts"] == NOW and b["ledger_net"] == 0, i
            assert _census(st, "post_only_rejected") == 1 and _census(st, "take_placed") == 0, i
        iocs += [(now, c) for c in ioc]
    assert len(iocs) == 1
    at, c = iocs[0]
    assert at - NOW >= wait and c[2] == 0.30 and c[3] == 300
    assert b["ledger_net"] == 300 and b["take_armed_ts"] is None
    assert _census(st, "on_target") == 1 and not _places(v)
    sql = _flat(ml._SQL_BOOK_ARM)
    assert "take_armed_at = CASE WHEN $2 THEN COALESCE(take_armed_at, now()) ELSE NULL END" in sql


def test_a_flatten_rest_from_an_earlier_vanish_never_skips_the_rest_first_rule():
    """minor 3. A flatten rest cancelled two hours ago (he came back)
    is not this vanish's rest: a fresh rest first, close_position only
    after MIRROR_FLATTEN_REST_S of it; the plan carries the vanish
    clock. And within THIS vanish the FIRST rest is the clock (the
    re-review's minor 3): a re-quote at +200 s does not restart it, so
    the slippage path runs at +301 s, not +501 s."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = p.add_book(ledger=300)
    p.add_order(b, side=SELL, wire=0.32, qty=300, kind="flatten_vanished", state="cancelled",
                placed_ts=NOW - 7200, done_at=NOW - 7000, order_id=None)
    gone = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0}])
    v = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32)
    st = _tick(p, v, http=gone)
    assert "close" not in _kinds(v) and _census(st, "flatten_rested") == 1
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][5] == "TIME_IN_FORCE_GOOD_TILL_CANCEL" and pl[0][2] == 0.32
    assert b["last_plan"]["vanish_since"] == NOW and b["last_plan"]["kind"] == "flatten_vanished"
    # the ask moved: the rest is re-quoted at +200 (a second rest of
    # this vanish, placed at +200), the vanish clock unchanged
    v2 = _Venue(held={SLUG: 300}, bid=0.33, ask=0.34)
    v2.orders, v2.n = v.orders, v.n            # the same book, the id counter carried
    st2 = _tick(p, v2, now=NOW + 200, http=gone)
    assert ("cancel", "oid-1", SLUG) in v2.calls and "close" not in _kinds(v2) and st2["requotes"] == 1
    pl2 = _places(v2)
    assert len(pl2) == 1 and pl2[0][5] == "TIME_IN_FORCE_GOOD_TILL_CANCEL" and pl2[0][2] == 0.34
    assert b["last_plan"]["vanish_since"] == NOW
    rests = sorted(o["placed_ts"] for o in p.orders.values()
                   if o["kind"] == "flatten_vanished" and o["tif"] == "GTC" and o["placed_ts"] >= NOW)
    assert rests == [NOW, NOW + 200]
    v3 = _Venue(held={SLUG: 300}, bid=0.33, ask=0.34)
    v3.orders = v.orders
    _tick(p, v3, now=NOW + rules.MIRROR_FLATTEN_REST_S - 1, http=gone)
    assert "close" not in _kinds(v3) and not _cancels(v3) and b["last_plan"]["vanish_since"] == NOW
    v4 = _Venue(held={SLUG: 300}, bid=0.33, ask=0.34)
    v4.orders = v.orders
    st4 = _tick(p, v4, now=NOW + rules.MIRROR_FLATTEN_REST_S + 1, http=gone)
    assert ("cancel", "oid-2", SLUG) in v4.calls and ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v4.calls
    assert b["ledger_net"] == 0 and st4["flattened"] == 1
    sql = _flat(ml._SQL_FLATTEN_REST_SINCE)
    assert "min(extract(epoch FROM placed_at))" in sql and "max(" not in sql
    assert "state IN ('placing', 'open', 'unknown') OR placed_at >= to_timestamp($2)" in sql


def test_flat_since_is_dropped_while_the_book_is_held_or_the_target_is_above_zero():
    """minor 4. A flat clock from an earlier flat spell is dropped the
    moment the target reads above zero or the ledger holds shares, so
    a re-flattened book waits the full MIRROR_FLAT_CLOSE_S again."""
    p = _pool(snap_at=NOW - 900)                       # stale: the increase is refused, nothing rests
    b = p.add_book(ledger=0, gross_buy=90.0, avg_cost=0.30, last_plan={"flat_since": NOW - 3 * 3600})
    _tick(p, _Venue(), http=_Http(status=500))     # and no per-market read either
    assert "flat_since" not in b["last_plan"] and b["state"] == "live" and b["last_plan"]["target"] == 300
    p.fills, p.snap, p.snap_at = _his(300, other_size=300), {M: 300.0, N: 300.0}, NOW
    pair = _mkt(300.0, 300.0)                          # the same pair at the venue
    st2 = _tick(p, _Venue(), now=NOW + 30, http=pair)
    assert b["state"] == "live" and b["last_plan"]["flat_since"] == NOW + 30
    assert b["last_plan"]["close"] == "not_due" and _census(st2, "on_target") == 1
    _tick(p, _Venue(), now=NOW + 30 + rules.MIRROR_FLAT_CLOSE_S - 1, http=pair)
    assert b["state"] == "live"
    st4 = _tick(p, _Venue(), now=NOW + 30 + rules.MIRROR_FLAT_CLOSE_S + 1, http=pair)
    assert b["state"] == "closed" and _census(st4, "closed_cashed_out") == 1
    p5 = _pool(fills=_his(300, other_size=300), snap={M: 300.0, N: 300.0})
    b5 = p5.add_book(ledger=300, last_plan={"flat_since": NOW - 3 * 3600})
    _tick(p5, _Venue(held={SLUG: 300}), http=_mkt(300.0, 300.0))
    assert "flat_since" not in b5["last_plan"] and b5["state"] == "live" and b5["last_plan"]["target"] == 0


def test_the_sell_wire_is_priced_off_his_unrounded_equivalent():
    """minor 5. His other-token BUY at 0.47996 is 0.52004 to him: the
    SELL rests at 0.53, never the 4-place 0.52 a cent under him; the
    worker's level selection agrees with the shadow's to four places."""
    p = _pool(fills=_his(300, other_size=300, other_px=0.47996), snap={M: 300.0, N: 300.0})
    p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32)
    _tick(p, v)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][2] == 0.53
    o = next(iter(p.orders.values()))
    assert o["his_level"] == pytest.approx(0.52004) and o["wire"] == 0.53 and o["kind"] == "flatten_paired"
    assert ms.his_level(p.fills, M, N, reducing=True) == 0.52, "the shadow's figure is rounded at source"
    assert rules.sell_price(0.52, 0.32) == 0.52 and rules.sell_price(0.52004, 0.32) == 0.53
    for fills in (_his(), _his(300, sold=200), _his(300, other_size=300, other_px=0.6),
                  _his(300, other_size=300, other_px=0.47996, sold=100),
                  [_fill(M, "BUY", 300.0, 0.0, NOW - 3000)], []):
        for reducing in (False, True):
            ours, theirs = ml._his_level(fills, M, N, reducing), ms.his_level(fills, M, N, reducing)
            assert (ours is None) == (theirs is None) and (ours is None or round(ours, 4) == theirs), (fills, reducing)


def test_a_lost_close_response_is_named_and_reconciled_from_the_venue_position(monkeypatch):
    """minor 6. close_position raises on the sole-holder path: the row
    is 'placing' with the 0.0 wire the INSERT wrote, never searched by
    fingerprint, no book_error on any tick. The next tick reads the
    venue's position: shares gone -> booked from the trade log by
    position and the row adopted; nothing gone -> 'lost' by name past
    the window and the flatten runs again; a close_failed status is
    the same lost response, never a refusal."""
    owner = {"order_id": "owner-2", "us_market_slug": SLUG, "side": "SELL", "price": 0.5,
             "quantity": 10.0, "filled_shares": 0.0, "leaves": 10.0, "state": "new", "created_at": NOW}

    def _book():
        p = _pool(fills=_his(300, sold=300), snap=None)
        b = p.add_book(ledger=300, last_plan={"kind": "flatten_vanished", "vanish_since": NOW - 400})
        p.add_order(b, side=SELL, wire=0.32, qty=300, kind="flatten_vanished", state="cancelled",
                    placed_ts=NOW - 400, done_at=NOW - 10, order_id=None)
        return p, b

    class _V(_Venue):
        def close_position(self, slug, *, slippage_bips):
            self.calls.append(("close", slug, slippage_bips))
            self.extra_open.append(dict(owner))        # lands after the tick's open read: outside pre_ids
            raise TimeoutError("read timed out")
    gone = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0}])
    p, b = _book()
    v = _V(held={SLUG: 300})
    st = _tick(p, v, http=gone)
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v.calls
    row = next(x for x in p.orders.values() if x["tif"] == "CLOSE")
    assert row["state"] == "placing" and row["order_id"] is None and row["wire"] == 0.0
    assert b["state"] == "frozen" and b["frozen_reason"] == "placement_lost"
    assert _census(st, "placement_lost") == 1 and _census(st, "book_error") == 0
    assert [c for c in v.calls if c[0] == "open_orders"] == [("open_orders", None)], "a close has no fingerprint"
    # (a) the position dropped to 0: the close executed -- the one
    # unknown seller's fills price it, the row is adopted and filled,
    # the flat book then closes on the confirmed vanish
    async def _held0(slug):
        return 0, None
    monkeypatch.setattr(le, "_pm_held", _held0)
    sell = {"side": "SELL", "ts": NOW + 1, "order_id": "close-9", "order_qty": None, "order_price": None}
    v2 = _Venue(held={SLUG: 0}, extra_open=[owner],
                trades=[{**sell, "qty": 200.0, "price": 0.29}, {**sell, "qty": 100.0, "price": 0.28},
                        {**sell, "side": "BUY", "qty": 50.0, "price": 0.5, "order_id": "owner-2"}])
    st2 = _tick(p, v2, now=NOW + 90, http=gone)
    assert _census(st2, "book_error") == 0 and ("trades", SLUG, NOW - 30) in v2.calls
    assert row["state"] == "filled" and row["order_id"] == "close-9" and row["booked_filled"] == 300.0
    assert row["avg_px"] == pytest.approx(86.0 / 300.0, abs=1e-6)
    assert row["reason"] == "booked from the position and the trade log"
    assert b["ledger_net"] == 0 and _census(st2, "filled_take") == 1 and st2["flattened"] == 1
    assert b["state"] == "closed" and _census(st2, "closed_cashed_out") == 1
    # (b) nothing left the account: frozen by name inside the window,
    # 'lost' past it, the book thaws (venue == ledger) and flattens again
    async def _held300(slug):
        return 300, 0.31
    monkeypatch.setattr(le, "_pm_held", _held300)
    p, b = _book()
    v = _V(held={SLUG: 300})
    _tick(p, v, http=gone)
    row = next(x for x in p.orders.values() if x["tif"] == "CLOSE")
    st2 = _tick(p, _Venue(held={SLUG: 300}), now=NOW + 90, http=gone)
    assert row["state"] == "placing" and b["frozen_reason"] == "placement_lost" and _census(st2, "book_error") == 0
    v3 = _Venue(held={SLUG: 300})
    t3 = NOW + le._LOST_FILL_WINDOW_S + 61
    st3 = _tick(p, v3, now=t3, http=gone)
    assert row["state"] == "lost" and row["reason"] == "order_lost" and _census(st3, "order_lost") == 1
    # the frozen ticks wrote no vanish plan, so the vanish begins afresh:
    # a rest first (the old cancelled rest is not this vanish's), the
    # close after the rest's own wait
    assert b["state"] == "live" and "close" not in _kinds(v3)
    assert [c[5] for c in _places(v3)] == ["TIME_IN_FORCE_GOOD_TILL_CANCEL"] and b["last_plan"]["vanish_since"] == t3
    v4 = _Venue(held={SLUG: 300})
    v4.orders = v3.orders
    _tick(p, v4, now=t3 + rules.MIRROR_FLATTEN_REST_S + 1, http=gone)
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v4.calls and b["ledger_net"] == 0 and b["state"] == "closed"
    # (c) the adapter's close_failed (an exception inside the call) is a
    # lost response too: the row stays 'placing', never 'rejected'
    p, b = _book()
    v = _Venue(held={SLUG: 300}, close={"ok": False, "order_id": None, "status": "close_failed",
                                         "fill_price": None, "filled_shares": 0.0,
                                         "raw": {"error": "timeout", "slug": SLUG}})
    st = _tick(p, v, http=gone)
    row = next(x for x in p.orders.values() if x["tif"] == "CLOSE")
    assert row["state"] == "placing" and b["frozen_reason"] == "placement_lost"
    assert _census(st, "place_refused") == 0 and _census(st, "placement_lost") == 1
    # (d) a lost placement whose fill the venue never priced is refused
    # by name, never a TypeError (the CLOSE row has no cent of its own)
    o = {"id": 1, "whale": "rn1", "wire": 0.0, "booked_filled": 0.0, "qty": 300, "side": SELL}
    p4 = _pool()
    b4 = p4.add_book(ledger=300)
    t = ml._Tick(pool=p4, pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    out = _run(ml._book_delta(t, o, b4, {"state": "filled", "filled_shares": 300.0, "avg_px": None}, maker=False))
    assert out == "no_price" and b4["frozen_reason"] == "no_price" and b4["ledger_net"] == 300


def test_every_venue_read_goes_through_the_pacer(monkeypatch):
    """minor 7. The lost-response search (open orders by slug) and the
    co-held flatten's bid are venue READS behind the pacer like every
    other: one pace call per read."""
    paced = []
    monkeypatch.setattr(ml, "pace", lambda s=ms.READ_PACING_S: paced.append(s))
    reads = ("open_orders", "status", "trades", "slug_bid")
    p = _pool()
    v = _Venue(place_raises=TimeoutError("t"), rest_on_raise=True)
    _tick(p, v)
    assert ("open_orders", [SLUG]) in v.calls
    assert len(paced) == len([c for c in v.calls if c[0] in reads]) == 2   # the account's list, then the slug's
    paced.clear()

    async def _held(slug):
        return 500, 0.31
    monkeypatch.setattr(le, "_pm_held", _held)
    p3 = _pool(fills=_his(300, sold=300), snap=None)
    p3.manual_shares[SLUG] = 200.0
    b3 = p3.add_book(ledger=300)
    p3.add_order(b3, side=SELL, wire=0.32, kind="flatten_vanished",
                 placed_ts=NOW - rules.MIRROR_FLATTEN_REST_S - 1)
    v3 = _Venue(held={SLUG: 500}, flatten_bid=0.29, ioc_fill=300.0)
    v3.rest("oid-1", "SELL", 0.32, 300, created=NOW - 400)
    _tick(p3, v3, http=_gone())
    assert ("slug_bid", SLUG, True) in v3.calls and b3["ledger_net"] == 0
    assert len(paced) == len([c for c in v3.calls if c[0] in reads])
    src = inspect.getsource(ml)
    assert "to_thread(t.pmus.open_orders" not in src and "to_thread(t.pmus.slug_bid" not in src


def test_a_live_legacy_row_of_any_age_refuses_admission_and_a_named_error_row_ages_out():
    """minor 8. A three-day-old per-fill position on the other outcome
    (the same slug) still holds its claim; only the named error rows
    age out of the 48 h window."""
    p = _pool()
    p.add_row(us_market_slug=SLUG, asset=N, status="filled", placed_ts=NOW - 3 * 86400)
    st = _tick(p, _Venue())
    assert _census(st, "legacy_row") == 1 and not p.books
    for age, refused in ((3600, True), (3 * 86400, False)):
        p = _pool()
        p.add_row(us_market_slug=SLUG, asset="tok-z", status="error", placed_ts=NOW - age,
                  error="ORPHAN FILL RECORDED: 300 @ 0.30")
        st = _tick(p, _Venue())
        assert (_census(st, "legacy_row") == 1) is refused and bool(p.books) is not refused, age
    sql = _flat(ml._SQL_LEGACY_ROW)
    assert sql.count("interval '48 hours'") == 1
    assert "OR error LIKE 'venue has no record of order%') AND placed_at > now() - interval '48 hours'))) /* ml-legacy-row */" in sql
    assert "status IN ('filled', 'submitting', 'exiting') OR (status = 'error'" in sql


def test_a_flat_book_closes_on_a_confirmed_vanish_without_waiting_the_flat_hour():
    """minor 9. Ledger 0, target 0, fills reading him gone: the
    mirror's own confirmation closes the episode now (spec 1c); an
    unconfirmed vanish still waits the flat hour."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = p.add_book(ledger=0, gross_buy=93.0, avg_cost=0.31)
    row = p.rows[b["standing_row_id"]]
    http = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0}])
    st = _tick(p, _Venue(), http=http)
    assert http.calls and b["state"] == "closed" and row["status"] == "cashed_out"
    assert _census(st, "closed_cashed_out") == 1 and b["last_plan"]["close"] == "cashed_out"
    assert _census(st, "on_target") == 1 and b["last_plan"]["kind"] is None
    p2 = _pool(fills=_his(300, sold=300), snap=None)
    b2 = p2.add_book(ledger=0, gross_buy=93.0, avg_cost=0.31)
    st2 = _tick(p2, _Venue(), http=_Http(status=500))
    assert b2["state"] == "live" and b2["last_plan"]["close"] == "not_due" and _census(st2, "closed_cashed_out") == 0
    assert b2["last_plan"]["flat_since"] == NOW


def test_a_refused_resting_buy_is_cancelled_under_the_refusals_name(monkeypatch):
    """minor 10. Snapshot 100 against fills 300 is `drift`: the resting
    BUY is cancelled under that name, never the unlisted no_plan; a
    stale snapshot and a re-check clause name themselves the same way."""
    p = _pool(snap={M: 100.0, N: 0.0})
    b = p.add_book(ledger=0)
    o = p.add_order(b)
    v = _Venue()
    v.rest("oid-1")
    st = _tick(p, v, http=_Http(status=500))     # no per-market read: the walk carries it
    assert p.orders[o["id"]]["state"] == "cancelled" and p.orders[o["id"]]["reason"] == "drift"
    assert _census(st, "drift") == 1 and not _places(v)
    p2 = _pool(snap_at=NOW - 900)
    b2 = p2.add_book(ledger=0)
    o2 = p2.add_order(b2)
    v2 = _Venue()
    v2.rest("oid-1")
    _tick(p2, v2, http=_Http(status=500))
    assert p2.orders[o2["id"]]["state"] == "cancelled" and p2.orders[o2["id"]]["reason"] == "snapshot_stale"
    monkeypatch.setattr(edge_gate, "verdict", lambda w: (False, "unfunded"))
    p3 = _pool()
    b3 = p3.add_book(ledger=0)
    o3 = p3.add_order(b3)
    v3 = _Venue()
    v3.rest("oid-1")
    _tick(p3, v3)
    assert p3.orders[o3["id"]]["state"] == "cancelled" and p3.orders[o3["id"]]["reason"] == "edge_gate:unfunded"


def test_a_candidate_with_no_readable_markets_row_is_named_market_unreadable():
    """re-review minor 4. A candidate whose markets row is absent or
    could not be read is `market_unreadable`, the name the existing-
    book path gives the same reading -- never `market_closed`, the
    rules module's fail-closed default, which read as a settled game.
    No book, no order."""
    for shape in ("absent", "raises"):
        p = _pool()
        if shape == "absent":
            del p.markets[CID]
        else:
            p.raise_on.append(("ml-market", RuntimeError("blip")))
        v = _Venue()
        st = _tick(p, v)
        assert _census(st, "market_unreadable") == 1 and _census(st, "market_closed") == 0, shape
        assert not p.books and not p.orders and not _places(v), shape
    # a readable closed row is still market_closed, by the rules module
    p = _pool()
    p.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    st = _tick(p, _Venue())
    assert _census(st, "market_closed") == 1 and _census(st, "market_unreadable") == 0 and not p.books


def test_a_lost_close_is_sized_off_this_ticks_positions_walk_never_a_second_one(monkeypatch):
    """re-review minor 5. The lost CLOSE row reads the sold shares from
    the tick's ONE paced positions walk (step R): no le._pm_held -- a
    whole-account walk outside venue_pace -- on any tick the row
    stands. A tick with no walk (SAFE reconciles orders before step R)
    refuses by name and leaves the row: nothing booked, nothing
    placed."""
    held_calls = []

    async def _never(slug):
        held_calls.append(slug)
        raise AssertionError("a second positions walk")
    monkeypatch.setattr(le, "_pm_held", _never)
    gone = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0}])
    sell = {"side": "SELL", "ts": NOW - 80, "order_id": "close-9", "order_qty": None, "order_price": None}

    def _shape():
        p = _pool(fills=_his(300, sold=300), snap=None)
        b = p.add_book(ledger=300, state="frozen", frozen_reason="placement_lost", frozen_ts=NOW - 100)
        o = p.add_order(b, side=SELL, wire=0.0, qty=300, kind="flatten_vanished", tif="CLOSE",
                        order_id=None, state="placing", placed_ts=NOW - 90)
        v = _Venue(held={SLUG: 0}, trades=[{**sell, "qty": 300.0, "price": 0.29}])
        walks = []
        orig = v.portfolio.positions
        v.portfolio.positions = lambda q: walks.append(q) or orig(q)
        return p, b, o, v, walks
    p, b, o, v, walks = _shape()
    st = _tick(p, v, http=gone)
    assert not held_calls and len(walks) == 1 and _census(st, "book_error") == 0
    assert o["state"] == "filled" and o["order_id"] == "close-9" and o["booked_filled"] == 300.0
    assert b["ledger_net"] == 0 and b["state"] == "closed" and _census(st, "closed_cashed_out") == 1
    # the venue still holds the shares: nothing sold, frozen by name,
    # still off this tick's walk
    p, b, o, v, walks = _shape()
    v.portfolio.held = {SLUG: 300}
    st = _tick(p, v, http=gone)
    assert not held_calls and len(walks) == 1 and o["state"] == "placing" and b["ledger_net"] == 300
    assert b["frozen_reason"] == "placement_lost" and "trades" not in _kinds(v)
    # no walk this tick: SAFE
    monkeypatch.delenv("PMUS_MIRROR")
    p, b, o, v, walks = _shape()
    st = _tick(p, v, http=gone)
    assert st["mode"] == "safe" and not walks and not held_calls
    assert _census(st, "positions_unreadable") == 1 and _census(st, "book_error") == 0
    assert o["state"] == "placing" and b["ledger_net"] == 300 and b["state"] == "frozen"
    assert not _places(v) and "trades" not in _kinds(v)
    src = inspect.getsource(ml._reconcile_lost_close)
    assert "le._pm_held(" not in src and "t.positions.get(slug.lower()" in src


# ------------------ 14. the minors re-review's residuals and the Phase 7 seam

def _refusal(raw, order_id=None):
    """A venue whose every rest is a post-only refusal carrying `raw`
    (the adapter's `post_only_rejected` with the facts in raw), and
    whose every IOC fills at the wire."""
    def _place(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        if tif == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL":
            return {"ok": True, "order_id": oid, "status": "filled", "fill_price": price,
                    "filled_shares": float(qty), "raw": {}}
        return {"ok": False, "order_id": (oid if order_id else None), "status": "post_only_rejected",
                "fill_price": None, "filled_shares": 0.0, "raw": dict(raw)}
    return _place


_SHAPE_200 = {"status_code": 200, "order_state": "ORDER_STATE_REJECTED",
              "execution_type": "EXECUTION_TYPE_REJECTED", "post_only_cross": True,
              "reject_reason": "post_only_cross", "text": None}


def _room_holder(monkeypatch, day):
    """The MIRROR'S OWN day room, adjustable between ticks: 0.1 is a
    room the clip cannot fit (over_room), a large one fits everything.
    It was the sleeve's day room; that no longer binds the mirror
    (U12b), so `room["day"]` now sets rules.MIRROR_DAY_USD, which the
    worker reads at call time, and the sleeve answers with no ceiling."""
    class _Room(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            monkeypatch.setattr(rules, "MIRROR_DAY_USD", float(value))

    room = _Room()
    room["day"] = day

    async def _room(pool, cfg):
        return 1e9, 1e9
    monkeypatch.setattr(le, "_copy_day_room", _room)
    return room


def test_the_venues_200_refusal_shape_arms_the_take_and_the_400_shapes_read_as_before():
    """The worker seam of the to-a-tee program's Phase 7 rung 1: _place
    hands take_arms the RAW DICT, so the venue's second post-only
    refusal shape (a 200 whose order came back REJECTED with an
    execution of type REJECTED; the adapter's _post_only_cross) arms
    the take, and the rejected row names the order the venue minted.
    Every shape the adapter produced before reads as it did: a 400
    dict arms, a 429 dict does not, an empty raw does not (None did
    not), a 200 dict without the flag does not (the bare 200 did not)."""
    src = inspect.getsource(ml._place)
    assert "rules.take_arms(raw if isinstance(raw, dict) else code)" in src
    p = _pool()
    b = p.add_book(ledger=0)
    st = _tick(p, _Venue(bid=0.30, ask=0.30, place=_refusal(_SHAPE_200, order_id=True)))
    assert _census(st, "post_only_rejected") == 1 and b["take_armed_ts"] == NOW
    o = next(iter(p.orders.values()))
    assert o["state"] == "rejected" and o["reason"] == "post_only_rejected:200"
    assert o["order_id"] == "oid-1", "the 200 shape minted an order; the row names it"
    assert not st["abandoned"] and _census(st, "take_placed") == 0
    # the armed take fires after the wait, at or through, as ONE IOC
    v = _Venue(bid=0.30, ask=0.30, ioc_fill=300.0)
    st2 = _tick(p, v, now=NOW + rules.MIRROR_TAKE_AFTER_S + 1)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" and pl[0][2] == 0.30
    assert _census(st2, "take_placed") == 1 and b["take_armed_ts"] is None and b["ledger_net"] == 300
    # the shapes the adapter produced before, exactly as before
    for raw, arms, oid in (({"status_code": 400, "error": "400 crossing"}, True, None),
                           ({"status_code": 429, "error": "429 slow down"}, False, None),
                           ({}, False, None),
                           ({"status_code": 200}, False, None),
                           ({"status_code": 200, "post_only_cross": True}, False, None),
                           ({"status_code": "400"}, False, None)):
        p2 = _pool()
        b2 = p2.add_book(ledger=0)
        st3 = _tick(p2, _Venue(bid=0.30, ask=0.30, place=_refusal(raw)))
        assert _census(st3, "post_only_rejected") == 1, raw
        assert (b2["take_armed_ts"] == NOW) is arms, raw
        o2 = next(iter(p2.orders.values()))
        assert o2["state"] == "rejected" and o2["order_id"] is oid, raw
        assert o2["reason"] == f"post_only_rejected:{raw.get('status_code')}", raw


def test_a_take_arm_older_than_twice_the_wait_is_refused_by_name_and_the_book_rests_first(monkeypatch):
    """Task 7, residual 1. The arm is read in _act BEFORE the room and
    the clip, so it survived every tick where _act never reached
    _place: armed at T0 by a crossing refusal, three ticks the room
    refused (over_room, no placement), and at +3600 -- the book still
    crossing, the room back -- one IOC went out with no rest ever at
    the level. Now: within twice the wait the arm stands through the
    refused ticks (the take window), past it the arm is stale by name
    (`take_arm_stale`), the book RESTS FIRST as a post-only GTC at the
    wire and no IOC is placed."""
    room = _room_holder(monkeypatch, 1e9)
    p = _pool()
    b = p.add_book(ledger=0)
    st = _tick(p, _Venue(bid=0.30, ask=0.30, place=_refusal({"status_code": 400})))
    assert b["take_armed_ts"] == NOW and _census(st, "post_only_rejected") == 1
    room["day"] = 0.1
    for dt in (30, rules.MIRROR_TAKE_AFTER_S + 30, 2 * rules.MIRROR_TAKE_AFTER_S):
        v = _Venue(bid=0.30, ask=0.30, ioc_fill=300.0)
        st = _tick(p, v, now=NOW + dt)
        assert not _places(v) and _census(st, "over_room") == 1, dt
        assert b["take_armed_ts"] == NOW and _census(st, "take_arm_stale") == 0, dt
    room["day"] = 1e9
    p.snap_at = NOW + 3600 - 40
    v = _Venue(bid=0.30, ask=0.30, ioc_fill=300.0)
    st = _tick(p, v, now=NOW + 3600)
    pl = _places(v)
    assert [c[5] for c in pl] == ["TIME_IN_FORCE_GOOD_TILL_CANCEL"], pl
    assert pl[0][2] == 0.30 and pl[0][7] is True, "a post-only rest at the wire, never an IOC"
    assert _census(st, "take_arm_stale") == 1 and _census(st, "take_placed") == 0
    assert _census(st, "rest_placed") == 1 and b["take_armed_ts"] is None and b["ledger_net"] == 0
    assert any(x["what"] == "take_disarmed" and x.get("why") == "take_arm_stale" for x in ml._RECENT)
    # the bound is a multiplier of the rules' wait, never a wait of its own
    assert ml.TAKE_ARM_STALE_WAITS == 2
    src = inspect.getsource(ml._act)
    assert "float(TAKE_ARM_STALE_WAITS) * float(rules.MIRROR_TAKE_AFTER_S)" in src


def test_a_take_arm_is_cleared_when_the_book_leaves_his_level_and_the_next_refusal_restarts_the_clock(monkeypatch):
    """Task 7, residual 1, the other bound. Armed at T0 by a crossing
    refusal; at +30 the ask has left the wire and the room refuses the
    clip (nothing placed): the arm is cleared, because the crossing it
    witnessed has ended. At +60 the book crosses again and the venue
    refuses again: the arm is stamped +60 (the COALESCE has nothing to
    keep), so no IOC goes out at +120 off the T0 clock, and exactly one
    at +60 plus the wait."""
    room = _room_holder(monkeypatch, 1e9)
    p = _pool()
    b = p.add_book(ledger=0)
    _tick(p, _Venue(bid=0.30, ask=0.30, place=_refusal({"status_code": 400})))
    assert b["take_armed_ts"] == NOW
    room["day"] = 0.1
    v = _Venue(bid=0.30, ask=0.32, ioc_fill=300.0)
    st = _tick(p, v, now=NOW + 30)
    assert not _places(v) and _census(st, "over_room") == 1
    assert b["take_armed_ts"] is None and _census(st, "take_arm_stale") == 0
    assert any(x["what"] == "take_disarmed" and x.get("why") == "market_away" for x in ml._RECENT)
    room["day"] = 1e9
    wait = float(rules.MIRROR_TAKE_AFTER_S)
    st = _tick(p, _Venue(bid=0.30, ask=0.30, place=_refusal({"status_code": 400})), now=NOW + 60)
    assert b["take_armed_ts"] == NOW + 60 and _census(st, "post_only_rejected") == 1
    v = _Venue(bid=0.30, ask=0.30, place=_refusal({"status_code": 400}))
    st = _tick(p, v, now=NOW + wait + 1)
    assert not [c for c in _places(v) if c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert b["take_armed_ts"] == NOW + 60 and b["ledger_net"] == 0
    v = _Venue(bid=0.30, ask=0.30, place=_refusal({"status_code": 400}))
    st = _tick(p, v, now=NOW + 60 + wait + 1)
    ioc = [c for c in _places(v) if c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert len(ioc) == 1 and ioc[0][2] == 0.30 and _census(st, "take_placed") == 1
    assert b["ledger_net"] == 300 and b["take_armed_ts"] is None
    # a standing rest is never touched by either bound: the rest's own
    # age is the wait, as before
    p2 = _pool()
    b2 = p2.add_book(ledger=0, take_armed_ts=NOW - 3600)
    o2 = p2.add_order(b2, placed_ts=NOW - 5)
    v2 = _Venue(bid=0.30, ask=0.32)
    v2.rest("oid-1")
    st2 = _tick(p2, v2)
    assert not _places(v2) and not _cancels(v2) and _census(st2, "open_order_pending") == 1
    assert p2.orders[o2["id"]]["state"] == "open" and b2["take_armed_ts"] == NOW - 3600


def test_a_closing_books_rest_is_cancelled_closing_never_under_its_stale_freeze():
    """Task 7, residual 2 (the minor-6 pin extended to 'closing'). A
    book frozen venue_ledger_disagree whose market then closed is
    'closing' with the freeze's name still on it (step M's write never
    clears frozen_reason); its residual rest -- the cancel step M sent
    was ops-capped, and this tick's step O is the one that lands -- is
    cancelled under the book's STATE, never the stale freeze. A book
    still FROZEN cancels under the freeze's name, as before."""
    p = _pool()
    b = p.add_book(ledger=0, state="closing", frozen_reason="venue_ledger_disagree",
                   frozen_ts=NOW - 600, frozen_ticks=4)
    o = p.add_order(b)
    v = _Venue()
    v.rest("oid-1")
    st = _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    assert p.orders[o["id"]]["state"] == "cancelled" and p.orders[o["id"]]["reason"] == "closing"
    assert b["state"] == "closing" and not _places(v) and _census(st, "market_closed") == 0
    p = _pool()
    b = p.add_book(ledger=0, state="frozen", frozen_reason="venue_ledger_disagree",
                   frozen_ts=NOW - 600, frozen_ticks=4)
    o = p.add_order(b)
    v = _Venue()
    v.rest("oid-1")
    _tick(p, v)
    assert p.orders[o["id"]]["state"] == "cancelled"
    assert p.orders[o["id"]]["reason"] == "venue_ledger_disagree"
    src = inspect.getsource(ml._reconcile_open)
    assert 'in ("closed", "closing")' in src and 'cancel_reason = str(book["state"])' in src


def test_the_whole_slug_close_needs_a_certain_sole_holding(monkeypatch):
    """close_position closes the WHOLE slug and is the only order this
    worker sends with no clamp to its own book, so "am I the sole holder"
    must be certain. The old test was `ledger >= int(held)` -- and
    _pm_held FLOORS the venue's number before the worker sees it, so a
    foreign holding of any fraction under one share read as sole and our
    close took it with us. The fraction is in the tick's own paced walk;
    both readings must now agree. NOTE the fraction is faked on the VENUE
    (the walk), not on _pm_held, which cannot return one."""
    def _book():
        p = _pool(fills=_his(300, sold=300), snap=None)
        b = p.add_book(ledger=300, last_plan={"kind": "flatten_vanished", "vanish_since": NOW - 400})
        p.add_order(b, side=SELL, wire=0.32, qty=300, kind="flatten_vanished", state="cancelled",
                    placed_ts=NOW - 400, done_at=NOW - 10, order_id=None)
        return p, b
    gone = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0}])

    # someone else holds half a share: the walk sees 300.5, _pm_held 300.
    # No whole-slug close -- AND the book must still be able to leave, by
    # the co-held IOC: refusing outright strands it (and the admin
    # flatten, which lands in this same function) on every later tick too
    p, b = _book()
    v = _Venue(held={SLUG: 300.5})
    st = _tick(p, v, http=gone)
    assert not [c for c in v.calls if c[0] == "close"], "a fraction is not sole"
    assert _census(st, "flatten_holding_disagrees") >= 1
    assert [c for c in v.calls if c[0] == "place"], "a co-held slug still exits by IOC"

    # the two sources disagree the other way: the fresh read is larger
    async def _held301(slug):
        return 301, 0.31
    monkeypatch.setattr(le, "_pm_held", _held301)
    p, b = _book()
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v, http=gone)
    assert not [c for c in v.calls if c[0] == "close"]
    assert _census(st, "flatten_holding_disagrees") >= 1

    # both readings at our own ledger: still the sole holder, one walk
    async def _held300(slug):
        return 300, 0.31
    monkeypatch.setattr(le, "_pm_held", _held300)
    p, b = _book()
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v, http=gone)
    assert [c for c in v.calls if c[0] == "close"], "an exact match is sole"
    assert _census(st, "flatten_holding_disagrees") == 0
    # ONE fresh whole-account read on this path, not two: the second was
    # a 50-page walk outside the pacer, immediately before the most
    # dangerous order the worker sends (round-one review)
    src = inspect.getsource(ml._flatten_vanished)
    assert src.count("le._pm_held(") == 1


# ------------------------------------ 15. PHASE 1, WIRED, and one seam
#
# P1: the per-market position read (`whale_exits.market_positions`, which
# existed with no caller anywhere in `sportsassets/` and is the single
# reason the mirror opens no book), the drift fact from the NET rule, and
# `last_fresh_agreed` asserted by the worker instead of hard-coded True.
# Plus the fail-open seam in the same file and the same tick: a BUY that
# fills above its own wire, booked silently, inflating avg_cost and the
# day's spend.
#
# R7 -- WIDENING `_SQL_MANUAL_SHARES` TO EVERY NON-MIRROR LANE -- IS NOT
# HERE, and its tests were deleted with it rather than left asserting a
# behaviour the worker no longer has. Both shapes that were built were
# driven into a defect (unsigned against a signed venue net; signed by
# the two token ids and so dropping the desk's own `asset='slug:<slug>'`
# rows, which is a REGRESSION that freezes a live book for ever). The
# reason is written beside the query in the worker.

def _reading(**kw):
    """A _Reading with every field named, for the pure helpers."""
    base = dict(whale="rn1", cid=CID, slug=SLUG, la=M, oa=N, fills=[], his_long=0.0,
                his_other=0.0, snap={}, snap_age=None, snap_partial=False, fresh_read=False,
                fresh=False, snap_long=None, snap_other=None, bid=0.30, ask=0.32, mark=0.31,
                venue=0.0, manual=0.0, market={"closed": False, "resolved": False},
                market_live=True)
    base.update(kw)
    return ml._Reading(**base)


def _pos_calls(http):
    return [c for c in http.calls if c[0] == "/positions"]


def test_read_market_calls_market_positions_once_per_book_per_tick():
    """One read per book and per candidate per tick. The candidate below
    opens a book and the new book is planned in the SAME tick, so
    `_read_market` runs twice for one market; the venue is asked once."""
    now = time.time()
    p = _pool(snap=None)              # the whole-book walk reads nothing of him
    http, v = _mkt(300.0, 0.0), _Venue()
    st = _tick(p, v, now=now, http=http)
    assert len(p.books) == 1, "the per-market read is what opens a book at all"
    calls = _pos_calls(http)
    assert len(calls) == 1
    assert calls[0][1]["market"] == CID and calls[0][1]["sizeThreshold"] == 0
    assert calls[0][1]["user"] == "0xabc"
    assert st["snap_market_reads"] == 1 and st["snap_market_fresh_reads"] == 1
    assert st["snap_market_planned"] == 1


def test_a_one_sided_holding_is_a_complete_reading_of_the_market():
    """THE COMMON CASE, and the one 'both tokens came back' refused. A
    whale who has only ever held the long token of a condition has ONE
    row; `market_positions` calls that answer complete and reads the
    absent leg as 0.0, and it can -- two tokens, limit=100, every row
    from another condition refused by the callee, so an absent leg is a
    zero and not an unknown. Refusing it left P1 opening books only where
    he had touched BOTH tokens, which is the gate's own denominator."""
    now = time.time()
    one = _Http(rows=[{"conditionId": CID, "asset": M, "size": 300}])
    p = _pool(snap=None)
    st = _tick(p, _Venue(), now=now, http=one)
    assert len(p.books) == 1, "a plain directional position opens a book"
    plan = next(iter(p.books.values()))["last_plan"]
    assert plan["snap_market_fresh"] is True and plan["drift"] == 0.0
    assert plan["mkt_long"] == 300.0 and plan["mkt_other"] == 0.0
    assert st["snap_market_fresh_reads"] == 1 and _census(st, "snapshot_stale") == 0

    # the other side of the same coin: only the OTHER token came back,
    # so the long leg is the zero and his net is negative
    other = _Http(rows=[{"conditionId": CID, "asset": N, "size": 40}])
    p2 = _pool(fills=[_fill(N, "BUY", 40.0, 0.72, NOW - 3000)], snap=None)
    st2 = _tick(p2, _Venue(), now=now, http=other)
    assert st2["snap_market_fresh_reads"] == 1
    assert not p2.books and _census(st2, "short_side_refused") >= 1

    # AND THE REFUSAL THAT STAYS: an answer naming NEITHER token of this
    # condition is not a reading of this market. Reading it would say
    # "he is flat" about a market we never saw.
    neither = _Http(rows=[{"asset": "tok-elsewhere", "size": 900}])
    p3 = _pool(snap=None)
    st3 = _tick(p3, _Venue(), now=now, http=neither)
    assert not p3.books and _census(st3, "snap_market_unreadable") >= 1
    assert _census(st3, "snapshot_stale") >= 1 and st3["snap_market_fresh_reads"] == 0


def test_snap_market_fresh_is_set_only_on_a_fresh_complete_read():
    now = time.time()
    p = _pool(snap=None)
    st = _tick(p, _Venue(), now=now, http=_mkt(300.0, 0.0))
    assert len(p.books) == 1 and _census(st, "snapshot_stale") == 0
    b = next(iter(p.books.values()))
    assert b["last_plan"]["snap_market_fresh"] is True

    # a stamp outside the freshness window: no book, and the discarded
    # read is COUNTED rather than falling silently out of every counter
    p3 = _pool(snap=None)
    st3 = _tick(p3, _Venue(), now=now - ms.SNAP_MAX_AGE_S - 100, http=_mkt(300.0, 0.0))
    assert not p3.books and _census(st3, "snapshot_stale") >= 1
    assert _census(st3, "snap_market_stale") >= 1 and st3["snap_market_stale"] >= 1
    assert st3["snap_market_reads"] >= 1 and st3["snap_market_fresh_reads"] == 0
    assert _census(st3, "snap_market_unreadable") == 0, "read, and not a refusal to read"


def test_the_freshness_half_of_the_fact_is_our_own_clock_and_says_so():
    """The window is `t.now - ts` where `ts` is `time.time()` taken
    INSIDE `market_positions` as the read lands -- our clock, not the
    venue's. So it bounds a tick that has been running longer than
    SNAP_MAX_AGE_S, and a clock that jumped; it cannot catch venue-side
    staleness, and in a normal tick the fact measures COMPLETENESS. This
    is pinned so that nobody quotes `fresh_complete_share` against §0's
    freshness baseline without reading it."""
    doc = ml._market_snap.__doc__
    assert "FRESHNESS HALF IS STRUCTURAL" in doc and "COMPLETENESS" in doc
    now = time.time()
    # forward and backward: the magnitude is what is tested, both ways
    for skew in (-(ms.SNAP_MAX_AGE_S + 100), ms.SNAP_MAX_AGE_S + 100):
        p = _pool(snap=None)
        st = _tick(p, _Venue(), now=now + skew, http=_mkt(300.0, 0.0))
        assert not p.books and st["snap_market_stale"] >= 1, skew


def test_an_unreadable_market_read_refuses_that_market_and_not_the_tick():
    """A None or raising read refuses THAT MARKET under its own name and
    never abandons the tick -- no backoff, no miss streak, and every
    other book in the tick unaffected."""
    now = time.time()
    for http in (_Http(status=500), _Http(rows=[])):
        p = _pool(snap=None)
        st = _tick(p, _Venue(), now=now, http=http)
        assert not p.books
        assert _census(st, "snap_market_unreadable") >= 1
        assert _census(st, "snapshot_stale") >= 1
        assert st["abandoned"] is False and st["status"] == "ok"

    class _Boom:
        calls: list = []

        async def get(self, path, params=None):
            raise RuntimeError("data api down")

    p2 = _pool(snap=None)
    b = p2.add_book(ledger=100)
    v = _Venue(held={SLUG: 100})
    st2 = _tick(p2, v, now=now, http=_Boom())
    assert _census(st2, "snap_market_unreadable") >= 1
    assert st2["abandoned"] is False, "a market we cannot see is not a tick we abandon"
    assert b["state"] == "live" and not _places(v), "held, never increased on an unread market"


def test_a_slow_market_read_is_bounded_named_and_refuses_only_that_market(monkeypatch):
    """A data API that is SLOW rather than down raises nothing: with no
    per-read timeout the tick simply took minutes, with nothing
    reconciled, no TTL cancelled, live rests standing and no census name
    anywhere. The client's 25 s timeout is per-request and shared with
    `_confirm_gone`; it is not a bound on this read."""
    now = time.time()
    monkeypatch.setattr(ml, "_SNAP_READ_TIMEOUT_S", 0.01)

    async def _slow(*a, **kw):
        await asyncio.sleep(0.2)
        return {"by_asset": {M: 300.0}, "long": 300.0, "complete": True, "ts": now}
    monkeypatch.setattr(ml.whale_exits, "market_positions", _slow)
    p = _pool(snap=None)
    b = p.add_book(ledger=100)
    v = _Venue(held={SLUG: 100})
    st = _tick(p, v, now=now)
    assert st["snap_market_slow"] == 1 and _census(st, "snap_market_unreadable") >= 1
    assert st["abandoned"] is False and b["state"] == "live" and not _places(v)
    assert ml._SNAP_READ_TIMEOUT_S == 0.01
    src = inspect.getsource(ml._market_snap)
    assert "asyncio.wait_for" in src and "_SNAP_READ_TIMEOUT_S" in src


def test_the_per_market_read_has_its_own_budget_and_never_shortens_the_walk(monkeypatch):
    """THE CAP COUNTS MARKETS. `t.reads` is what the candidate walk
    breaks on and it was one BBO read per market, so charging a second
    read per market to it silently halved the markets a tick considers --
    and that number is the denominator of P1's own gate. Two read
    classes, two budgets of the same bounded size."""
    now = time.time()
    p = _pool(snap=None)
    http, v = _mkt(300.0, 0.0), _Venue()
    st = _tick(p, v, now=now, http=http)
    bbos = len([c for c in v.calls if c[0] == "bbo"])
    assert st["reads"] == bbos, "t.reads is the venue quote total and nothing else"
    assert st["snap_market_reads"] == 1 and st["reads"] >= 1
    assert "t.reads >= ms.MAX_MARKETS_PER_TICK" not in inspect.getsource(ml._market_snap)

    # its own budget still binds, under its own name -- for a CANDIDATE
    # (U12: the budget is the candidates'; a book is never refused by
    # it). At the tick level the candidate walk's own quote budget stops
    # a candidate FIRST (a candidate's per-market read follows its quote
    # read one for one, so `t.cand_mkt_reads` never reaches the cap
    # before `t.cand_reads` does): with the budget at 0 no candidate is
    # read at all, by the name `capped_tick`
    monkeypatch.setattr(ms, "MAX_MARKETS_PER_TICK", 0)
    p2 = _pool(snap=None)
    http2, v2 = _mkt(300.0, 0.0), _Venue()
    st2 = _tick(p2, v2, now=now, http=http2)
    assert st2["capped_tick"] is True and not _pos_calls(http2) and "bbo" not in _kinds(v2)
    assert st2["snap_market_planned"] == 0 and not p2.books and not _places(v2)
    # the per-market clause is the defence behind that, and it is
    # driven at the function's own level: a candidate past the budget
    # is refused, not read, under its own name
    t2 = ml._Tick(pool=_pool(snap=None), pmus=_Venue(), http=_mkt(300.0, 0.0), now=now,
                  stats=ml._new_stats())
    t2.cand_mkt_reads = ms.MAX_MARKETS_PER_TICK
    prior = ml._current_stats
    ml._current_stats = t2.stats
    try:
        assert _run(ml._market_snap(t2, "rn1", CID, M, N)) == (None, None, None, None)
    finally:
        ml._current_stats = prior
    assert not _pos_calls(t2.http), "past the budget the market is refused, not read"
    assert t2.stats["snap_market_reads"] == 0 and t2.stats["snap_market_capped"] == 1
    assert _census(t2.stats, "snap_market_capped") == 1
    assert _census(t2.stats, "snap_market_unreadable") == 0, "budget pressure is not unreadability"
    assert t2.mkt_reads == 0 and t2.cand_mkt_reads == ms.MAX_MARKETS_PER_TICK
    # and the same tick reads a BOOK's market past it, charging the
    # total and never the candidates' budget
    t3 = ml._Tick(pool=_pool(snap=None), pmus=_Venue(), http=_mkt(300.0, 0.0), now=now,
                  stats=ml._new_stats())
    t3.cand_mkt_reads = ms.MAX_MARKETS_PER_TICK
    ml._current_stats = t3.stats
    try:
        assert _run(ml._market_snap(t3, "rn1", CID, M, N, book=True))[0] is True
    finally:
        ml._current_stats = prior
    assert _pos_calls(t3.http) and t3.stats["snap_market_fresh_reads"] == 1
    assert (t3.mkt_reads, t3.cand_mkt_reads) == (1, ms.MAX_MARKETS_PER_TICK)
    assert t3.stats["snap_market_capped"] == 0

    # AND A BOOK IS READ PAST IT (U12): a book whose whole-book walk is
    # not fresh plans its reduces and its flatten on this read alone,
    # so a shared budget was a count cap on books by another road --
    # the twenty-first live book had no reading and no managed exit
    p3 = _pool(snap=None)
    b = p3.add_book(ledger=0)
    http3, v3 = _mkt(300.0, 0.0), _Venue()
    st3 = _tick(p3, v3, now=now, http=http3)
    assert _pos_calls(http3), "the book's per-market read is never budgeted away"
    assert st3["snap_market_reads"] == 1 and st3["snap_market_fresh_reads"] == 1
    assert st3["snap_market_capped"] == 0 and _census(st3, "snap_market_capped") == 0
    assert b["state"] == "live" and _places(v3), "and the book plans on it"


def test_the_snapshot_counters_carry_a_denominator_that_does_not_flatter_us(monkeypatch):
    """`fresh / reads` excludes exactly the failures -- the budget cap, a
    market whose ids we could not form, a skipped tick -- so it reads
    HIGHER than the share §3b M4 gates on. `snap_market_planned` counts
    every market the tick asked about, before any refusal."""
    now = time.time()
    for arm in ("ok", "capped", "no_address", "no_sibling"):
        p = _pool(snap=None)
        # a planned market whatever the walk does; the sibling id lives
        # on the BOOK row, so that is where its absence is set. The
        # capped arm is a CANDIDATE's (U12: the budget is the
        # candidates'; a book's read is never refused by it)
        if arm != "capped":
            p.add_book(ledger=0, **({"other_asset": None} if arm == "no_sibling" else {}))
        monkeypatch.setattr(ms, "MAX_MARKETS_PER_TICK", 20)
        if arm == "no_address":
            p.whale_address = {}
        if arm == "capped":
            # at the tick level the candidate walk's own budget stops a
            # candidate before its per-market read (see the budget test
            # above), so the clause is driven at the function's level
            t = ml._Tick(pool=p, pmus=_Venue(), http=_mkt(300.0, 0.0), now=now,
                         stats=ml._new_stats())
            t.cand_mkt_reads = ms.MAX_MARKETS_PER_TICK
            prior = ml._current_stats
            ml._current_stats = t.stats
            try:
                _run(ml._market_snap(t, "rn1", CID, M, N))
            finally:
                ml._current_stats = prior
            st = t.stats
        else:
            st = _tick(p, _Venue(), now=now, http=_mkt(300.0, 0.0))
        planned = st["snap_market_planned"]
        assert planned >= 1, arm
        assert planned == (st["snap_market_reads"] + st["snap_market_capped"]
                           + st["snap_market_no_ids"] + st["snap_market_skipped"]), (arm, st)
        if arm == "ok":
            assert st["snap_market_fresh_reads"] == 1
        else:
            assert st["snap_market_fresh_reads"] == 0, arm
            assert _census(st, "snap_market_capped") + _census(st, "snap_market_no_ids") >= 1, arm


def test_a_market_with_no_sibling_token_is_refused_before_the_read_is_spent():
    """Without the sibling id the other leg is unknown, not zero, and no
    net can be formed. It burned a budget slot and a data-API throttle
    and returned with no name at all."""
    now = time.time()
    p = _pool(snap=None)
    p.token_cid = {M: CID}                      # no sibling token id anywhere
    http = _mkt(300.0, 0.0)
    st = _tick(p, _Venue(), now=now, http=http)
    assert not _pos_calls(http), "refused before the read"
    assert st["snap_market_reads"] == 0 and st["snap_market_no_ids"] >= 1
    assert _census(st, "snap_market_no_ids") >= 1 and not p.books


def test_a_closed_market_and_an_abandoning_tick_spend_no_venue_read():
    """Reads spent before the cheap refusals. A candidate on a resolved
    market is refused by `market_closed` whatever the read says, and an
    abandoning tick plans nothing -- both used to pay for a read first,
    and the second returned with no counter and no name."""
    now = time.time()
    p = _pool(snap=None)
    p.markets[CID]["closed"] = True
    http = _mkt(300.0, 0.0)
    st = _tick(p, _Venue(), now=now, http=http)
    assert not _pos_calls(http) and st["snap_market_planned"] == 0
    assert _census(st, "market_closed") >= 1

    # the skipped return has a name of its own
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=now, stats=ml._new_stats())
    out = _run(ml._market_snap(t, "rn1", CID, M, N))
    assert out == (None, None, None, None)
    assert t.stats["snap_market_skipped"] == 1 and t.stats["snap_market_planned"] == 1


def test_the_whale_address_is_read_once_per_whale_per_tick():
    now = time.time()
    p = _pool(snap=None)
    p.add_book(ledger=0)
    _tick(p, _Venue(), now=now, http=_mkt(300.0, 0.0))
    reads = [q for q in p.sent if "ml-whale-address" in q[1]]
    assert len(reads) == 1, reads


def test_drift_is_the_net_rule_so_a_merged_pair_reads_zero():
    """His fills say +5,000 Yes and +4,700 No; he merged 4,700 pairs
    on-chain and the venue shows 300 and 0. The per-token rule reads
    |5,000 - 300| / 5,000 = 0.94 and refuses every increase on that
    market for the life of the book; the net reads 0."""
    now = time.time()
    assert rules.drift_rule(5000.0, 300.0, True, False).refusal == "drift"
    assert rules.drift_net_rule(5000.0, 4700.0, 300.0, 0.0) == 0.0

    d, src = ml._drift_for(_reading(his_long=5000.0, his_other=4700.0, snap_market_fresh=True,
                                    mkt_long=300.0, mkt_other=0.0, mkt_net=300.0))
    assert src == "market" and d.drift == 0.0 and d.increase_ok is True
    d2, src2 = ml._drift_for(_reading(his_long=5000.0, his_other=4700.0, snap_long=300.0,
                                      snap_other=0.0, fresh_read=True, fresh=True))
    assert src2 == "book" and d2.refusal == "drift" and d2.increase_ok is False

    # end to end: the same market, the same numbers, a book that opens
    p = _pool(fills=_his(5000, other_size=4700), snap={M: 300.0, N: 0.0}, snap_at=now - 40)
    _tick(p, _Venue(), now=now, http=_mkt(300.0, 0.0))
    assert len(p.books) == 1, "a merged pair leg is not a lifelong drift lock-out"
    plan = next(iter(p.books.values()))["last_plan"]
    assert plan["drift"] == 0.0 and plan["drift_src"] == "market"
    assert plan["snap_net_book"] == 300.0, "the walk's reading is recorded beside it"
    # and with the read refused, the fallback is the per-token rule
    p2 = _pool(fills=_his(5000, other_size=4700), snap={M: 300.0, N: 0.0}, snap_at=now - 40)
    st2 = _tick(p2, _Venue(), now=now, http=_Http(status=500))
    assert not p2.books and _census(st2, "drift") >= 1


def test_last_fresh_agreed_is_a_read_and_not_a_literal():
    """`rules.drift_rule` says the WORKER must assert `last_fresh_agreed`
    and the default is False -- the SMALLER of two disagreeing readings.
    It was the literal True at both call sites."""
    tree = ast.parse(inspect.getsource(ml))
    sites = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name in ("drift_rule", "drift_net_rule"):
            sites += 1
            for kw in node.keywords:
                if kw.arg == "last_fresh_agreed":
                    assert not isinstance(kw.value, ast.Constant), ast.dump(kw.value)
    assert sites >= 1, "the drift rules are still called"
    through = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
               and getattr(n.func, "id", "") == "_drift_for"]
    assert len(through) == 2, "both former call sites go through the asserting helper"

    # what it asserts: the per-market net against the fills-derived net
    agreed = _reading(his_long=300.0, snap_market_fresh=True, mkt_long=300.0,
                      mkt_other=0.0, mkt_net=300.0)
    assert ml._fresh_agreed(agreed) is True
    assert ml._fresh_agreed(_reading(his_long=300.0, snap_market_fresh=True, mkt_long=299.5,
                                     mkt_other=0.0, mkt_net=299.5)) is True
    assert ml._fresh_agreed(_reading(his_long=300.0, snap_market_fresh=True, mkt_long=298.0,
                                     mkt_other=0.0, mkt_net=298.0)) is False
    assert ml._fresh_agreed(_reading(his_long=300.0)) is False, "no read is not agreement"


def test_the_fallback_asserts_the_agreement_it_read_and_it_is_false_by_construction(monkeypatch):
    """THE PIN THAT AN AST SHAPE COULD NOT MAKE. The old pin asserted
    only that the keyword was not an `ast.Constant`, which `(1 == 1)`
    satisfies; nothing in the suite would have noticed the literal coming
    back. This one reads the VALUE the worker passes, over every tick
    shape this file drives.

    And it records the truth about that value rather than the programme's
    claim for it: `_market_snap` returns either (None, None, None, None)
    or (True, lo, ot, net), so the fallback branch -- entered exactly when
    `snap_market_fresh is not True` -- is entered exactly when there is no
    per-market net, and `_fresh_agreed` is False there BY CONSTRUCTION.
    The 'smaller of two disagreeing readings' the programme feared losing
    is carried by `_drift_for`'s drifted arm and by `drift_rule` itself;
    the test below drives that."""
    seen = []
    real = rules.drift_rule

    def _spy(*a, **kw):
        seen.append(kw.get("last_fresh_agreed", "ABSENT"))
        return real(*a, **kw)
    monkeypatch.setattr(rules, "drift_rule", _spy)
    now = time.time()
    for pool_kw, http in (
            (dict(snap={M: 300.0, N: 0.0}), _Http(status=500)),
            (dict(snap={M: 200.0, N: 0.0}), _Http(status=500)),
            (dict(snap=None), _Http(status=500)),
            (dict(snap={M: 300.0, N: 0.0}, snap_at=NOW - 900), _Http(status=500)),
            (dict(snap={M: 300.0, N: 0.0}), _mkt(300.0, 0.0)),
            (dict(snap={M: 300.0, N: 0.0}), _mkt(200.0, 0.0))):
        p = _pool(**pool_kw)
        p.add_book(ledger=300)
        _tick(p, _Venue(held={SLUG: 300}), now=now, http=http)
    assert seen, "the fallback is still reached"
    assert set(seen) == {False}, seen


def test_the_per_market_net_sizes_the_reduction_from_the_smaller_reading():
    """The property the literal True would have deleted, driven where it
    actually lives: the DRIFTED market arm. On a fresh disagreement the
    SMALLER reading sizes the sale, so we never keep holding a position
    he may already have left. Changing that `"smaller"` to `"derived"`
    fails here, which the ast pin could never see."""
    now = time.time()
    d, src = ml._drift_for(_reading(his_long=300.0, snap_market_fresh=True, mkt_long=200.0,
                                    mkt_other=0.0, mkt_net=200.0))
    assert src == "market" and d.increase_ok is False and d.reduce_from == "smaller"
    assert ml._net_for(_reading(his_long=300.0, snap_market_fresh=True, mkt_long=200.0,
                                mkt_other=0.0, mkt_net=200.0), d) == (200.0, 200.0)

    p = _pool(fills=_his(300), snap=None)
    p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v, now=now, http=_mkt(200.0, 0.0))
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][3] == 100, "300 down to 200, not held"


def test_the_unusable_per_market_reading_answers_what_the_rules_answer():
    """The `d is None` arm read `"derived" if agreed else "smaller"`,
    while `drift_rule` returns `"smaller"` unconditionally for a reading
    that is not a size -- before it ever looks at `last_fresh_agreed`.
    Unreachable from the worker (`net_positions` floors every token at
    0.0 and `_market_snap` refuses a negative leg), so the divergence was
    never seen; two rules for one question is corrected, not guarded."""
    # the arm where the two answers PARTED: a negative leg (no number can
    # be made) whose net nonetheless agrees with the derived net inside a
    # share, so the old `"derived" if agreed else "smaller"` said derived
    assert rules.drift_net_rule(-5.0, 0.0, 0.0, 5.0) is None
    forced = _reading(his_long=-5.0, his_other=0.0, snap_market_fresh=True, mkt_long=0.0,
                      mkt_other=5.0, mkt_net=-5.0)
    assert ml._fresh_agreed(forced) is True, "the agreement clause says yes here"
    d, src = ml._drift_for(forced)
    assert src == "market"
    assert (d.increase_ok, d.reduce_from, d.refusal, d.drift) == (False, "smaller",
                                                                  "snapshot_stale", None)
    assert d == rules.drift_rule(-5.0, 0.0, True, False), "one answer, not two"
    # and the worker cannot reach it: a negative leg never leaves _market_snap
    now = time.time()
    p = _pool(snap=None)
    st = _tick(p, _Venue(), now=now,
               http=_Http(rows=[{"conditionId": CID, "asset": M, "size": -5}]))
    assert not p.books and _census(st, "snap_market_unreadable") >= 1


def test_the_plan_row_carries_the_readings_the_target_was_sized_from():
    """A book that cannot be audited back to the reading that sized it is
    a book nobody can grade. The two legs, the whole-book net beside the
    per-market one, and the agreement the drift rule was handed."""
    now = time.time()
    p = _pool(fills=_his(300), snap={M: 290.0, N: 0.0}, snap_at=now - 40)
    p.add_book(ledger=0)
    _tick(p, _Venue(), now=now, http=_mkt(300.0, 0.0))
    plan = next(iter(p.books.values()))["last_plan"]
    assert plan["mkt_long"] == 300.0 and plan["mkt_other"] == 0.0
    assert plan["snap_net"] == 300.0 and plan["snap_net_book"] == 290.0
    assert plan["fresh_agreed"] is True and plan["drift_src"] == "market"
    assert plan["snap_market_fresh"] is True


# --- seam 1: a fill above the wire -------------------------------------

def test_a_fill_above_the_wire_trips_and_freezes():
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, wire=0.30, qty=300)
    v = _Venue(fills={"oid-1": (300.0, 0.32)})
    v.rest("oid-1", price=0.30, qty=300)
    st = _tick(p, v)
    assert _census(st, "mirror_overspend") >= 1
    assert p.state["mirror_live"] is False
    assert p.state["mirror_live_trip"]["why"] == "mirror_overspend"
    assert b["state"] == "frozen" and b["frozen_reason"] == "mirror_overspend"
    assert b["ledger_net"] == 300, "the shares are ours whatever the venue charged"
    assert not _places(v)
    # the line itself: half a tick of tolerance, and a whole cent is over it
    row = {"side": BUY, "tif": "GTC", "wire": 0.30}
    assert ml._overspend_of(row, {"avg_px": 0.30}) is False
    assert ml._overspend_of(row, {"avg_px": 0.305}) is False, "the half-cent grid"
    assert ml._overspend_of(row, {"avg_px": 0.31}) is True
    # and the number it reads is the ORDER'S CUMULATIVE AVERAGE, which is
    # what the venue gives us: a big tranche at the wire dilutes a later
    # one above it. Said out loud because §3b M10 wants at_or_better at
    # 1.00 EXACT and whoever computes it must use this predicate and
    # print `overspend_uncheckable` beside it.
    assert "CUMULATIVE" in ml._overspend_of.__doc__


def test_a_fill_at_create_above_the_wire_trips_before_the_post_only_latch():
    """A post-only order the venue crossed anyway is precisely the fill
    most likely to be above the wire, and `_place` books it BEFORE the
    latch runs -- so the check must run on that call."""
    def _place(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": True, "order_id": oid, "status": "filled",
                "fill_price": round(price + 0.02, 2), "filled_shares": float(qty), "raw": {}}
    p = _pool()
    st = _tick(p, _Venue(place=_place))
    assert _census(st, "mirror_overspend") >= 1 and p.state["mirror_live"] is False
    assert _census(st, "post_only_ignored") == 1
    src = inspect.getsource(ml._place)
    assert src.index("_book_delta") < src.index("_POST_ONLY_OK = False")


def test_a_close_row_never_trips_overspend(monkeypatch):
    """A CLOSE row's wire is deliberately 0.0, not None, so a naive
    `avg_px > wire` is true for EVERY vanish flatten."""
    assert ml._overspend_of({"side": SELL, "tif": "CLOSE", "wire": 0.0},
                            {"avg_px": 0.29}) is False
    assert ml._overspend_of({"side": BUY, "tif": "CLOSE", "wire": 0.0},
                            {"avg_px": 0.29}) is False
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = p.add_book(ledger=300, last_plan={"kind": "flatten_vanished", "vanish_since": NOW - 400})
    p.add_order(b, side=SELL, wire=0.32, qty=300, kind="flatten_vanished", state="cancelled",
                placed_ts=NOW - 400, done_at=NOW - 10, order_id=None)
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v, http=_gone())
    assert [c for c in v.calls if c[0] == "close"]
    assert _census(st, "mirror_overspend") == 0 and p.state["mirror_live"] is True


def test_an_unreadable_avg_px_counts_but_does_not_trip():
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, wire=0.30, qty=300)
    v = _Venue(fills={"oid-1": (300.0, None)}, held={SLUG: 300})
    v.rest("oid-1", price=0.30, qty=300)
    st = _tick(p, v)
    assert _census(st, "overspend_uncheckable") >= 1
    assert p.state["mirror_live"] is True and b["state"] != "frozen"
    assert b["ledger_net"] == 300 and b["avg_cost"] == 0.30, "booked at the wire, as before"
    assert ml._overspend_of({"side": BUY, "tif": "GTC", "wire": None}, {"avg_px": 0.31}) is None
    assert ml._overspend_of({"side": BUY, "tif": "GTC", "wire": 0.30}, {"avg_px": None}) is None


# --- the gate's own instrument ------------------------------------------

def test_the_gate_counters_survive_the_health_endpoints_sanitizer():
    """The probe lines that grade this unit read `/api/health/services`,
    and that endpoint publishes the heartbeat through `_sanitize_detail`,
    which caps EVERY dict at 40 keys. `census` carries ~98, so
    `.detail.census.<name>` is a REAL number for the first 40 names in
    CENSUS_KEYS order and a STRUCTURAL ZERO for every name after them --
    `snapshot_stale`, every `snap_market_*` name, `drift`,
    `venue_ledger_disagree`, `wrong_sign_trip`, `order_lost`,
    `post_only_ignored` and `mirror_flatten` are all past the cap. A gate
    line that reads a counter it can never read anything but zero from
    prints a pass that was never measured, which is worse than printing
    nothing. `integ` is the projection that survives, and it is asserted
    here against the REAL sanitizer, not a copy of it.

    Driven on a tick that really freezes `venue_ledger_disagree`."""
    from sportsassets.api import app as api_app
    p = _pool()
    b = p.add_book(ledger=100)
    st = _tick(p, _Venue(held={SLUG: 400}))
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree"
    assert _census(st, "venue_ledger_disagree") == 1
    served = api_app._sanitize_detail(st)
    # the defect, driven: the endpoint truncates the census and the name
    # the gate stops on is one of the names it drops
    assert served["census"]["_truncated_keys"] > 0
    assert "venue_ledger_disagree" not in served["census"]
    assert "snapshot_stale" not in served["census"]
    # and the served block carries the same tick's real number
    assert served["integ"]["venue_ledger_disagree"] == 1
    assert served["integ"]["snap_market_planned"] == st["snap_market_planned"] >= 1
    assert set(served["integ"]) == set(st["integ"]), "no key of it is dropped"
    assert len(st["integ"]) < api_app._DETAIL_MAX_KEYS
    # it is a PROJECTION of the counters, never a second place one is kept
    assert "integ" not in ml.CENSUS_KEYS
    assert all(k in ml.CENSUS_KEYS for k in ml._INTEG_CENSUS_KEYS)
    zero = ml._new_stats()
    assert set(zero["integ"]) == set(st["integ"]) and set(zero["integ"].values()) == {0}
    # THE CAP MOVED (2026-09-05): `venue_halted` took index 24 of
    # CENSUS_KEYS and pushed `side_band` to index 40, past the cap, so
    # the served census dropped an admission clause it used to carry.
    # Both ride on `integ`: the one the new key displaced, and the new
    # key itself, so an operator reading the halted venue reads a real
    # number off the served surface
    assert "side_band" not in served["census"], "the defect, driven: past the cap"
    assert served["integ"]["side_band"] == 0 and served["integ"]["venue_halted"] == 0
    assert "side_band" in ml._INTEG_CENSUS_KEYS and "venue_halted" in ml._INTEG_CENSUS_KEYS
    assert isinstance(served["integ"]["side_band"], int) and isinstance(served["integ"]["venue_halted"], int)
    # `ledger_dust` (2026-09-06) was appended LAST in CENSUS_KEYS, past
    # the cap by construction, so it rides on `integ` the same way
    assert "ledger_dust" not in served["census"] and "ledger_dust" in ml._INTEG_CENSUS_KEYS
    assert served["integ"]["ledger_dust"] == 0 and isinstance(served["integ"]["ledger_dust"], int)
    # AND THE TOP LEVEL IS CAPPED AT 40 TOO. `integ` must never be the
    # key that gets dropped. It is written in `_new_stats`, and every
    # conditional key the tick adds later (`capped_tick`,
    # `venue_positions`, `reaper_touched_error`, `abandon_reason`, ...)
    # APPENDS after the base block, so `integ` can only be truncated if
    # the base block itself grows past the cap. That is what is pinned.
    order = list(ml._new_stats())
    assert order.index("integ") < api_app._DETAIL_MAX_KEYS
    assert len(order) <= api_app._DETAIL_MAX_KEYS, "the base block is over the cap"
    assert "_truncated_keys" not in served, "the top level itself is not truncated"


# ---------------- 15. the pre-flight before "switch on the mirror system 100%"

@pytest.mark.parametrize("tif", ["GTC", "GTD"])
def test_the_takes_cancel_spends_the_replace_budget_and_the_take_is_refused_at_the_cap(tif):
    """The adversarial pre-flight of 2026-09-05: _SQL_REPLACES counted
    reason = 'replace' alone, the take arm cancels under reason 'take'
    and then rests anew off the no-order path, so a book could cycle
    rest -> wait -> take -> rest about thirty times an hour bounded
    only by the ops budget. Now (1) MIRROR_MAX_REPLACES_PER_HOUR
    cancels under reason 'take' refuse the next REPLACE decision
    'replace_capped'; (2) the same count refuses the next TAKE
    'take_capped' BEFORE its cancel, the rest kept standing -- the
    take never passed through the replace branch, so widening the
    count alone left the cycle unbounded; (3) the IOC a take places
    is its own row under reason 'take' and does NOT count: counting
    it would charge one re-quote twice. Under EITHER rest tif: a GTD
    rest (the PMUS_MIRROR_GTD flag's) cancelled by a take is a
    re-quote as much as a GTC one, so a count narrowed to GTC alone
    must fail the GTD case here. tests/test_mirror_live_day_cap pins
    the statement's text by its tag."""
    cap = rules.MIRROR_MAX_REPLACES_PER_HOUR
    # (1) a replace decision (the rest is a cent under the plan)
    p = _pool()
    b = p.add_book(ledger=0)
    for _ in range(cap):
        p.add_order(b, state="cancelled", reason="take", tif=tif, done_at=NOW - 100, order_id=None)
    p.add_order(b, wire=0.28)
    v = _Venue()
    v.rest("oid-1", price=0.28)
    st = _tick(p, v)
    assert _census(st, "replace_capped") == 1 and not _cancels(v) and not _places(v)
    # (2) a take decision: the rest has stood the wait and the ask is
    # at the wire; at the cap the take is refused by name, nothing is
    # cancelled, nothing placed, and the rest still stands
    p = _pool()
    b = p.add_book(ledger=0)
    for _ in range(cap):
        p.add_order(b, state="cancelled", reason="take", tif=tif, done_at=NOW - 100, order_id=None)
    o = p.add_order(b, placed_ts=NOW - rules.MIRROR_TAKE_AFTER_S - 1)
    v = _Venue(ask=0.30, ioc_fill=300.0)
    v.rest("oid-1")
    st = _tick(p, v)
    assert _census(st, "take_capped") == 1 and _census(st, "take_placed") == 0
    assert not _cancels(v) and not _places(v)
    assert p.orders[o["id"]]["state"] == "open" and b["open_order_id"] == o["id"]
    # one under the cap: the take goes out as before, and its cancel
    # is the count's (cap)th row, so the NEXT take is the refused one
    p = _pool()
    b = p.add_book(ledger=0)
    for _ in range(cap - 1):
        p.add_order(b, state="cancelled", reason="take", tif=tif, done_at=NOW - 100, order_id=None)
    p.add_order(b, placed_ts=NOW - rules.MIRROR_TAKE_AFTER_S - 1)
    v = _Venue(ask=0.30, ioc_fill=300.0)
    v.rest("oid-1")
    st = _tick(p, v)
    assert _census(st, "take_placed") == 1 and _census(st, "take_capped") == 0
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    # (3) IOC rows under reason 'take' (the takes themselves) are not
    # re-quotes: with `cap` of them and no cancel, the replace goes out
    p = _pool()
    b = p.add_book(ledger=0)
    for _ in range(cap):
        p.add_order(b, state="filled", reason="take", kind="take", tif="IOC", done_at=NOW - 100,
                    order_id=None)
    p.add_order(b, wire=0.28)
    v = _Venue()
    v.rest("oid-1", price=0.28)
    st = _tick(p, v)
    assert _census(st, "replace_capped") == 0 and _cancels(v) and _places(v)
    assert "take_capped" in ml.CENSUS_KEYS


def test_an_unreadable_requote_count_is_the_cap_for_the_replace_and_for_the_take():
    """The fail-closed rail _requotes_this_hour carries (review of the
    first cut, 2026-09-05: it moved into the helper and nothing pinned
    it): a count the pool cannot read IS the cap, so the replace
    decision is refused 'replace_capped' and the take 'take_capped',
    nothing cancelled, nothing placed, the rest standing. A helper
    that read an unreadable count as zero would let both go out."""
    # the replace decision (the rest is a cent under the plan)
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b, wire=0.28)
    p.raise_on.append(("ml-replaces", RuntimeError("db down")))
    v = _Venue()
    v.rest("oid-1", price=0.28)
    st = _tick(p, v)
    assert _census(st, "replace_capped") == 1 and not _cancels(v) and not _places(v)
    assert p.orders[o["id"]]["state"] == "open" and b["open_order_id"] == o["id"]
    assert st["requotes"] == 0
    # the take decision (the rest stood the wait, the ask at the wire)
    p = _pool()
    b = p.add_book(ledger=0)
    o = p.add_order(b, placed_ts=NOW - rules.MIRROR_TAKE_AFTER_S - 1)
    p.raise_on.append(("ml-replaces", RuntimeError("db down")))
    v = _Venue(ask=0.30, ioc_fill=300.0)
    v.rest("oid-1")
    st = _tick(p, v)
    assert _census(st, "take_capped") == 1 and _census(st, "take_placed") == 0
    assert not _cancels(v) and not _places(v)
    assert p.orders[o["id"]]["state"] == "open" and b["open_order_id"] == o["id"]
    assert [x for x in p.sent if "ml-replaces" in x[1]], "the count was asked for"


# ------------------ 16. the quote read names the venue's market STATE

HALTED = "MARKET_STATE_HALTED"


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_a_halted_venue_abandons_the_tick_as_venue_halted_with_the_venues_own_word(caplog):
    """2026-09-05, 22:29Z on: the venue answered HTTP 200 with
    `state: MARKET_STATE_HALTED` and empty quotes on every market for
    five hours, and the mirror abandoned every tick `no_quote` -- the
    same word it uses for an UNREADABLE book, so an operator could not
    tell "the venue is halted" from "our reads are failing". Three
    candidates on a halted venue: three reads, each `venue_halted`,
    never `no_quote`; the third abandons the tick under that name, the
    WARNING carries the state string, the tick publishes the state,
    and the mode line prints both. Nothing is placed."""
    assert ms.MISS_STREAK_ABANDON == 3
    p = _pool(conds=["c1", "c2", "c3"])
    v = _Venue(state=HALTED)
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        st = _tick(p, v)
        ml._mode_line(st, ml.MODE_LINE_EVERY_TICKS)
    assert st["abandoned"] and st["abandon_reason"] == "venue_halted" and st["status"] == "degraded"
    assert _census(st, "venue_halted") == 3 and _census(st, "no_quote") == 0, st["census"]
    assert _census(st, "tick_abandoned") == 1 and st["reads"] == 3
    assert [c for c in v.calls if c[0] == "bbo"] == [("bbo", SLUG)] * 3
    assert st["venue_state"] == HALTED
    assert not p.books and not _places(v)
    assert ml._backoff_until == NOW + ms.BACKOFF_S
    assert ("mirror_live: tick abandoned (venue_halted: MARKET_STATE_HALTED), backing off 60.0s"
            in _warnings(caplog))
    assert not [w for w in _warnings(caplog) if "unreadable" in w], "a halted venue is readable"
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")][0]
    assert " venue=MARKET_STATE_HALTED abandon=venue_halted stats={" in line
    assert line.startswith("mirror_live mode=on whales=['rn1'] ")
    assert "venue_halted" in ml.CENSUS_KEYS and "venue_halted" in st["census"]
    # the venue resumes: OPEN with a quote on the next tick is a book, on its own
    p2, v2 = _pool(), _Venue()
    st2 = _tick(p2, v2, now=NOW + ms.BACKOFF_S + 1)
    assert not st2["abandoned"] and st2["venue_state"] == "MARKET_STATE_OPEN"
    assert _census(st2, "venue_halted") == 0 and p2.books and _places(v2)


def test_an_unreadable_book_is_still_no_quote_with_the_existing_warning(caplog):
    p = _pool(conds=["c1", "c2", "c3"])
    v = _Venue(raise_bbo=True)
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, v)
    assert st["abandoned"] and st["abandon_reason"] == "no_quote"
    assert _census(st, "no_quote") == 3 and _census(st, "venue_halted") == 0, st["census"]
    assert st["venue_state"] is None, "a read that failed carries no state"
    ws = _warnings(caplog)
    assert ws.count(f"mirror_live: BBO for {SLUG} unreadable (RuntimeError)") == 3
    assert "mirror_live: tick abandoned (no_quote), backing off 60.0s" in ws
    assert not p.books and not _places(v)


def test_an_open_empty_book_is_no_quote_and_a_closed_quoted_book_is_venue_halted():
    # an OPEN market with no makers: no_quote, exactly as before
    p = _pool()
    v = _Venue(bid=None, ask=None)
    st = _tick(p, v)
    assert _census(st, "no_quote") == 1 and _census(st, "venue_halted") == 0, st["census"]
    assert st["venue_state"] == "MARKET_STATE_OPEN" and not st["abandoned"]
    assert not p.books and not _places(v)
    # the SDK's typed shape, no state at all: the same empty-open reading
    p = _pool()
    st = _tick(p, _Venue(bid=None, ask=None, state=None))
    assert _census(st, "no_quote") == 1 and _census(st, "venue_halted") == 0 and st["venue_state"] is None
    # a settled market with a stale resting book (the probe's CLOSED
    # payload: bid 0.01, ask 0.20): a quote on a non-OPEN market is NOT
    # a quote to trade on
    p = _pool()
    v = _Venue(bid=0.01, ask=0.20, state="MARKET_STATE_CLOSED")
    st = _tick(p, v)
    assert _census(st, "venue_halted") == 1 and _census(st, "no_quote") == 0, st["census"]
    assert st["venue_state"] == "MARKET_STATE_CLOSED" and not st["abandoned"]
    assert not p.books and not p.orders and not _places(v), "never placed on"
    # a quoted OPEN market on the same fixture DOES open a book: the refusal above was the state
    p = _pool()
    v = _Venue(bid=0.01, ask=0.20)
    _tick(p, v)
    assert p.books
    # a QUOTED read with no state at all (the SDK-typed shape, which
    # promises no `state`): an OPEN book, never a halt -- a book opens
    # and a rest is placed, nothing is counted venue_halted
    p = _pool()
    v = _Venue(state=None)
    st = _tick(p, v)
    assert _census(st, "venue_halted") == 0 and _census(st, "no_quote") == 0, st["census"]
    assert st["venue_state"] is None and not st["abandoned"]
    assert p.books and _places(v) and _census(st, "rest_placed") == 1


def test_a_raising_quote_read_is_no_quote_and_the_warning_names_the_exception(caplog, monkeypatch):
    """The raising path of _bbo, driven: ms._paced_bbo itself raises
    (a client that cannot be built, a pacer that fails) rather than
    bbo_read answering with `error` -- the worker names the exception
    on the existing WARNING, counts the read no_quote, and the third
    abandons the tick under that name. No state is read, none is
    published, and nothing is placed."""
    def _boom(pmus, slug):
        raise RuntimeError("client down")
    monkeypatch.setattr(ms, "_paced_bbo", _boom)
    p = _pool(conds=["c1", "c2", "c3"])
    v = _Venue()
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, v)
    assert st["abandoned"] and st["abandon_reason"] == "no_quote" and st["status"] == "degraded"
    assert _census(st, "no_quote") == 3 and _census(st, "venue_halted") == 0, st["census"]
    assert st["reads"] == 3 and st["venue_state"] is None
    ws = _warnings(caplog)
    assert ws.count(f"mirror_live: BBO for {SLUG} unreadable (RuntimeError)") == 3
    assert "mirror_live: tick abandoned (no_quote), backing off 60.0s" in ws
    assert not [c for c in v.calls if c[0] == "bbo"], "the raise sat before the fake's read"
    assert not p.books and not _places(v)


def _vanish_after_the_rest(state, held=300, manual=0.0, **venue_kw):
    """A book of 300 in a vanish whose flatten rest stood its wait and
    was cancelled (the shape test_a_close_row_never_trips_overspend
    drives), under the ADMIN FLATTEN LEVER; the venue's quote read for
    the slug carries `state`."""
    p = _pool(fills=_his(300, sold=300), snap=None)
    p.state["mirror_flatten"] = True
    if manual:
        p.manual_shares[SLUG] = manual
    b = p.add_book(ledger=300, last_plan={"kind": "flatten_vanished", "vanish_since": NOW - 400})
    p.add_order(b, side=SELL, wire=0.32, qty=300, kind="flatten_vanished", state="cancelled",
                placed_ts=NOW - 400, done_at=NOW - 10, order_id=None)
    v = _Venue(held={SLUG: held}, state=state, **venue_kw)
    return p, b, v


@pytest.mark.parametrize("state, quotes", [
    ("MARKET_STATE_CLOSED", dict(bid=0.01, ask=0.20)),      # the probe's settled CFB market, stale rests
    ("MARKET_STATE_HALTED", dict(bid=None, ask=None)),      # the venue-wide halt, empty book
])
def test_the_flattens_slippage_leg_refuses_a_non_open_slug_before_it_reads_a_bid(monkeypatch, state, quotes):
    """THE MONEY-PATH HOLE (review of U9, 2026-09-06). The flatten's
    slippage leg priced its IOC off slug_bid, which reads through
    _bbo_quotes -- no market state -- so the reviewer's probe (a CLOSED
    market with a stale resting book: bid 0.01, ask 0.20) yielded a
    tradeable bid and an IOC SELL went out in the same tick whose quote
    read counted the slug venue_halted; the sole-holder branch sent
    close_position with no quote read at all. Under the admin flatten
    lever, after MIRROR_FLATTEN_REST_S of the rest: no IOC, no
    close_position, no bid read, no position read -- the leg is
    refused `venue_halted` (the read's own count plus the leg's), the
    plan names it, the book keeps its shares. The same fixture with the
    slug OPEN takes the leg as before: close_position for the sole
    holder, the IOC at the bid when co-held."""
    assert rules.MIRROR_FLATTEN_REST_S <= 400
    gone = _gone()
    # SOLE HOLDER: close_position is the order at stake
    p, b, v = _vanish_after_the_rest(state, **quotes)
    st = _tick(p, v, http=gone)
    assert "close" not in _kinds(v) and "slug_bid" not in _kinds(v), v.calls
    assert not [c for c in _places(v) if c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert not _places(v), "nothing rested either: the read was not a quote"
    assert _census(st, "venue_halted") == 2, st["census"]       # the read, then the leg
    assert _census(st, "no_bid_for_flatten") == 0 and _census(st, "flatten_vanished") == 1
    assert b["ledger_net"] == 300 and b["last_reason"] == "venue_halted" and b["state"] == "live"
    assert st["venue_state"] == state and not st["abandoned"]
    assert not [x for x in p.orders.values() if x["state"] in ("placing", "open", "unknown")]
    # CO-HELD: the IOC at sell_limit_price(bid) is the order at stake
    async def _held(slug):
        return 500, 0.31
    monkeypatch.setattr(le, "_pm_held", _held)
    p, b, v = _vanish_after_the_rest(state, held=500, manual=200.0, flatten_bid=0.29, ioc_fill=300.0,
                                     **quotes)
    st = _tick(p, v, http=gone)
    assert "close" not in _kinds(v) and "slug_bid" not in _kinds(v) and not _places(v), v.calls
    assert _census(st, "venue_halted") == 2 and _census(st, "no_bid_for_flatten") == 0, st["census"]
    assert b["ledger_net"] == 300 and b["last_reason"] == "venue_halted"
    # THE CONTROL: the slug OPEN on the same fixture takes the leg
    p, b, v = _vanish_after_the_rest("MARKET_STATE_OPEN", held=500, manual=200.0, flatten_bid=0.29,
                                     ioc_fill=300.0, bid=0.30, ask=0.32)
    st = _tick(p, v, http=gone)
    assert ("slug_bid", SLUG, True) in v.calls and "close" not in _kinds(v)
    ioc = [c for c in _places(v) if c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert len(ioc) == 1 and ioc[0][2] == le.sell_limit_price(0.29) and ioc[0][3] == 300 and ioc[0][4] is True
    assert b["ledger_net"] == 0 and _census(st, "venue_halted") == 0

    async def _held300(slug):
        return 300, 0.31
    monkeypatch.setattr(le, "_pm_held", _held300)
    p, b, v = _vanish_after_the_rest("MARKET_STATE_OPEN", bid=0.30, ask=0.32)
    st = _tick(p, v, http=gone)
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v.calls and b["ledger_net"] == 0
    assert _census(st, "venue_halted") == 0 and st["flattened"] == 1
    # the refusal sits BEFORE the position read and the bid read, by the source
    src = inspect.getsource(ml._flatten_vanished)
    i_refuse = src.index('_mirror_stop("venue_halted", w)')
    assert i_refuse < src.index("le._pm_held(") < src.index("t.pmus.slug_bid")
    assert 'r.venue_state is not None and r.venue_state != _STATE_OPEN' in src


# ------------------------------- 11b. U11: sub-share ledger dust is not an overfill

def _dust_book(p, ledger=414, held=413.76):
    """The 2026-09-06 01:11:57Z book: the standing row holds the venue's
    FRACTIONAL fills (182.76 + 231.0 = 413.76) while the integer ledger
    column read 414."""
    b = p.add_book(ledger=ledger, avg_cost=0.12)
    p.rows[b["standing_row_id"]]["filled_shares"] = held
    return b


def test_a_sale_within_a_lot_of_the_ledger_is_dust_booked_counted_and_never_a_trip(monkeypatch, caplog):
    """The flatten sold 414.0 on a row holding 413.76: 0.24 of rounding
    between a fractional fill and a whole-share ledger, not a short. It
    books to the ledger, counts `ledger_dust`, notes a `dust` entry, and
    the book stays live with the DB switch untouched."""
    monkeypatch.setenv("PMUS_MIRROR", "exits")          # no increase after the flat: the fill alone
    caplog.set_level(logging.INFO)
    p = _pool(fills=_his(300, sold=300), snap={M: 0.0, N: 0.0})
    b = _dust_book(p)
    row = p.rows[b["standing_row_id"]]
    o = p.add_order(b, side=SELL, wire=0.12, qty=414, kind="flatten_paired")
    v = _Venue(held={SLUG: 0}, fills={"oid-1": (414.0, 0.12)})
    v.rest("oid-1", "SELL", 0.12, 414)
    st = _tick(p, v)
    assert _census(st, "ledger_dust") == 1 and _census(st, "overfill") == 0, st["census"]
    assert b["state"] == "live" and b["frozen_reason"] is None and b["ledger_net"] == 0
    assert st["frozen_reasons"] == {} and st["books_frozen"] == 0
    # the DB switch was NOT written, false or otherwise, and no receipt exists
    assert p.state["mirror_live"] is True and "mirror_live_trip" not in p.state
    assert not [a for k, s, a in p.sent if "ml-state-write" in s and a[0] in ("mirror_live", "mirror_live_trip")]
    assert row["filled_shares"] == 0.0 and row["status"] == "filled"
    assert p.orders[o["id"]]["booked_filled"] == 414.0 and p.orders[o["id"]]["state"] == "filled"
    assert p.orders[o["id"]]["realized"] == pytest.approx(0.0)          # sold at the entry
    # the order's cumulative dust rides on its receipt JSON (no column)
    assert p.orders[o["id"]]["receipt"]["dust_total"] == pytest.approx(0.24)
    assert [a[1] for k, s, a in p.sent if "ml-order-dust" in s] == [pytest.approx(0.24)]
    ent = [x for x in ml._RECENT if x["what"] == "dust"]
    assert len(ent) == 1 and ent[0]["book"] == b["id"] and ent[0]["shares"] == pytest.approx(0.24)
    assert ent[0]["held"] == 413.76 and ent[0]["sold"] == 414.0 and ent[0]["ledger"] == 0
    assert ent[0]["dust_total"] == pytest.approx(0.24)
    lines = [r.getMessage() for r in caplog.records]
    sell = [x for x in lines if "booked SELL" in x][-1]
    assert sell == ("mirror row %s booked SELL 413.76 @ 0.12 (entry 0.12, pnl +0.0000, "
                    "DUST: venue sold 414.0, ledger held 413.76): now 0.0 shares" % b["standing_row_id"])
    assert not any("TRIPPED OFF" in x or "OVERFILL" in x for x in lines)
    assert ("mirror_live: book %s SELL on order %s is 0.24 shares past the ledger (venue sold 414.0, "
            "row held 413.76): dust, booked to the ledger, no trip; dust_total 0.24 on the order"
            % (b["id"], o["id"])) in lines
    # and the count is on the served surface
    from sportsassets.api import app as api_app
    assert api_app._sanitize_detail(st)["integ"]["ledger_dust"] == 1


def test_a_sale_more_than_a_lot_past_the_ledger_is_still_the_overfill_with_held_on_the_receipt(monkeypatch):
    monkeypatch.setenv("PMUS_MIRROR", "exits")
    p = _pool(fills=_his(300, sold=300), snap={M: 0.0, N: 0.0})
    b = _dust_book(p)
    o = p.add_order(b, side=SELL, wire=0.12, qty=416, kind="flatten_paired")
    v = _Venue(held={SLUG: 0}, fills={"oid-1": (416.0, 0.12)})
    v.rest("oid-1", "SELL", 0.12, 416)
    st = _tick(p, v)
    assert _census(st, "overfill") == 1 and _census(st, "ledger_dust") == 0, st["census"]
    assert b["state"] == "frozen" and b["frozen_reason"] == "overfill" and b["ledger_net"] == 0
    assert p.state["mirror_live"] is False
    rec = p.state["mirror_live_trip"]
    assert rec["why"] == "overfill" and rec["book"] == b["id"] and rec["order"] == o["id"]
    assert (rec["sold"], rec["ledger"], rec["held"], rec["dust_total"]) == (416.0, 0, 413.76, 0.0)
    assert not [x for x in ml._RECENT if x["what"] == "dust"]
    assert p.rows[b["standing_row_id"]]["filled_shares"] == 0.0, "the row never goes negative"


def test_the_incident_book_flattens_at_the_ledger_books_the_dust_and_reaches_the_flat_close():
    """(a) The incident book: ledger 414, row 413.76. The flatten's SELL
    is sized min(plan, ledger, ceil(held)) = 414 -- NOT floored to 413,
    which left ledger 1 / row 0.76 that nothing could sell (under_one_share
    on every tick, never a flat close: the withdrawn first cut). The venue
    fills 414: dust 0.24 booked, ledger 0, row 0.0, the book takes its
    flat_since on the next tick and closes cashed_out after
    MIRROR_FLAT_CLOSE_S. NO under_one_share, ever."""
    p = _pool()
    p.state["mirror_flatten"] = True
    b = _dust_book(p)
    row = p.rows[b["standing_row_id"]]
    v = _Venue(held={SLUG: 413.76})
    st = _tick(p, v)
    assert _census(st, "flatten_vanished") >= 1 and _census(st, "under_one_share") == 0
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][3] == 414
    o = [o for o in p.orders.values() if o["book_id"] == b["id"]][0]
    assert o["qty"] == 414 and o["side"] == SELL and o["kind"] == "flatten_vanished"
    assert b["ledger_net"] == 414, "the ledger is not touched by the sizing"
    # the venue fills the whole 414 on a row of 413.76: dust, booked, flat
    v2 = _Venue(held={SLUG: 0}, fills={"oid-1": (414.0, pl[0][2])})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30)
    assert _census(st2, "ledger_dust") == 1 and _census(st2, "overfill") == 0, st2["census"]
    assert _census(st2, "under_one_share") == 0 and _census(st2, "partial_fill") == 0
    assert b["ledger_net"] == 0 and row["filled_shares"] == 0.0 and row["status"] == "filled"
    assert b["state"] == "live" and p.state["mirror_live"] is True and "mirror_live_trip" not in p.state
    assert o["state"] == "filled" and o["booked_filled"] == 414.0 and o["receipt"]["dust_total"] == pytest.approx(0.24)
    assert b["last_plan"]["flat_since"] == NOW + 30 and b["last_plan"]["close"] == "not_due"
    # the flat close, on its clock
    st3 = _tick(p, _Venue(held={SLUG: 0}), now=NOW + 30 + rules.MIRROR_FLAT_CLOSE_S + 1)
    assert b["state"] == "closed" and row["status"] == "cashed_out" and _census(st3, "closed_cashed_out") == 1
    assert _census(st3, "under_one_share") == 0 and not _places(v2)
    # the column unreadable: the ledger sizes it (fail closed on the trip, which stands)
    p2 = _pool()
    p2.state["mirror_flatten"] = True
    _dust_book(p2, held=None)
    v4 = _Venue(held={SLUG: 414})
    _tick(p2, v4)
    assert _places(v4)[0][3] == 414
    # the rule itself, on the book dict the tick carries
    assert ml._sell_qty({"ledger_net": 414, "_held": 413.76}, 414) == 414
    assert ml._sell_qty({"ledger_net": 414, "_held": 413.76}, 200) == 200
    assert ml._sell_qty({"ledger_net": 414}, 414) == 414
    assert ml._sell_qty({"ledger_net": 414, "_held": None}, 414) == 414
    assert ml._sell_qty({"ledger_net": 414, "_held": "413.76"}, 414) == 414     # a string is no reading
    assert ml._sell_qty({"ledger_net": 414, "_held": 500.0}, 414) == 414       # never above the ledger
    assert ml._sell_qty({"ledger_net": 413, "_held": 413.76}, 414) == 413      # nor above the ledger's read
    assert ml._sell_qty({"ledger_net": 414, "_held": 412.5}, 414) == 413       # a row a lot under: its ceil
    assert ml._sell_qty({"ledger_net": 1, "_held": 0.76}, 1) == 1              # the sub-share row sells its lot
    assert ml._sell_qty({"ledger_net": 1, "_held": 0.0}, 1) == 0               # a row at zero sells nothing
    assert ml._sell_qty({"ledger_net": 1, "_held": -3.0}, 1) == 0
    # the stash is the row read _tick_book already makes, not a second
    # query, and it is written ONCE: _book_fill's upkeep of it is gone
    # (the sizing reads the standing row as the tick read it)
    src = inspect.getsource(ml._tick_book)
    assert src.count("_SQL_STANDING_READ") == 1
    assert src.index("_SQL_STANDING_READ") < src.index('book["_held"] = _num(standing.get("filled_shares"))')
    assert "_held" not in inspect.getsource(ml._book_fill)
    assert inspect.getsource(ml).count('book["_held"] =') == 1
    act = inspect.getsource(ml._act)
    assert act.count("_sell_qty(book, p.qty)") == 2 and 'int(book.get("ledger_net") or 0)) if p.side == SELL' not in act
    assert "math.floor" not in inspect.getsource(ml._sell_qty)


def _sub_share_vanish(held_venue, pm_held, monkeypatch, manual=0.0, **venue_kw):
    """A sole or co-held book at ledger 1 / row 0.76 on a vanished market,
    its flatten rest stood and cancelled, the slippage leg due."""
    async def _held(slug):
        return pm_held, 0.12
    monkeypatch.setattr(le, "_pm_held", _held)
    p = _pool(fills=_his(300, sold=300), snap=None)
    if manual:
        p.manual_shares[SLUG] = manual
    b = p.add_book(ledger=1, avg_cost=0.12, last_plan={"kind": "flatten_vanished", "vanish_since": NOW - 400})
    p.rows[b["standing_row_id"]]["filled_shares"] = 0.76
    p.add_order(b, side=SELL, wire=0.32, qty=1, kind="flatten_vanished", state="cancelled",
                placed_ts=NOW - 400, done_at=NOW - 10, order_id=None)
    v = _Venue(held={SLUG: held_venue}, **venue_kw)
    return p, b, v


def test_a_sole_holder_at_ledger_one_row_a_fraction_sends_close_position_unclamped(monkeypatch):
    """(b) ledger 1 / row 0.76, the venue holds our 0.76 and nobody
    else's: close_position, the whole slug, fraction included -- never
    sized to the row, never under_one_share. The row's qty is the
    ledger (1) so a lost close reconstructs `sold = qty - int(held)` as
    the whole ledger."""
    close = {"ok": True, "order_id": "close-1", "status": "filled", "fill_price": 0.29,
             "filled_shares": 0.76, "raw": {}}
    p, b, v = _sub_share_vanish(0.76, 0, monkeypatch, close=close)
    row = p.rows[b["standing_row_id"]]
    st = _tick(p, v, http=_gone())
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v.calls and "slug_bid" not in _kinds(v)
    assert _census(st, "under_one_share") == 0 and _census(st, "flatten_vanished") == 1, st["census"]
    assert _census(st, "overfill") == 0 and _census(st, "ledger_dust") == 0
    o = next(x for x in p.orders.values() if x["tif"] == "CLOSE")
    assert o["qty"] == 1 and o["wire"] == 0.0 and o["booked_filled"] == 0.76
    assert row["filled_shares"] == 0.0 and b["ledger_net"] == 0 and b["state"] != "frozen"
    assert p.state["mirror_live"] is True
    # the gate sits BELOW the sole decision and inside the co-held arm only
    src = inspect.getsource(ml._flatten_vanished)
    i_sole = src.index("sole = sole_walk and sole_read")
    i_gate = src.index('_mirror_stop("under_one_share", w)')
    assert i_sole < src.index("qty = _sell_qty(book, ledger)") < i_gate < src.index("t.pmus.slug_bid")
    assert src.count('_mirror_stop("under_one_share", w)') == 1
    # merged with P2 rung S0: the co-held arm refuses a SHORT book by name
    # first (short_reduce_unproven), then sizes the long book's IOC
    assert "qty = ledger\n    if not sole:\n        if short:" in src
    assert 'return "short_reduce_unproven"\n        qty = _sell_qty(book, ledger)' in src


def test_a_coheld_ioc_at_ledger_one_row_a_fraction_sends_one_share(monkeypatch):
    """(c) the same numbers co-held (the desk's 200 beside our 0.76):
    the IOC sells min(ledger 1, ceil(0.76)) = 1, the venue fills 1.0 on
    a row of 0.76 -- dust 0.24, booked, flat, no trip."""
    p, b, v = _sub_share_vanish(200.76, 200, monkeypatch, manual=200.0, flatten_bid=0.29, ioc_fill=1.0)
    row = p.rows[b["standing_row_id"]]
    st = _tick(p, v, http=_gone())
    assert "close" not in _kinds(v) and ("slug_bid", SLUG, True) in v.calls
    ioc = [c for c in _places(v) if c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert len(ioc) == 1 and ioc[0][3] == 1 and ioc[0][4] is True and ioc[0][2] == le.sell_limit_price(0.29)
    assert _census(st, "under_one_share") == 0 and _census(st, "ledger_dust") == 1, st["census"]
    assert _census(st, "overfill") == 0 and b["ledger_net"] == 0 and row["filled_shares"] == 0.0
    o = next(x for x in p.orders.values() if x["tif"] == "IOC")
    assert o["qty"] == 1 and o["state"] == "filled" and o["receipt"]["dust_total"] == pytest.approx(0.24)
    assert p.state["mirror_live"] is True and b["state"] != "frozen"
    # a row at ZERO under a ledger of 1 is the one co-held case the gate still refuses
    p2, b2, v2 = _sub_share_vanish(200.0, 200, monkeypatch, manual=200.0, flatten_bid=0.29, ioc_fill=1.0)
    p2.rows[b2["standing_row_id"]]["filled_shares"] = 0.0
    st2 = _tick(p2, v2, http=_gone())
    assert _census(st2, "under_one_share") == 1 and not _places(v2) and "slug_bid" not in _kinds(v2)
    assert b2["ledger_net"] == 1


def test_a_lost_close_on_a_fractional_row_books_the_row_with_sold_the_whole_ledger(monkeypatch):
    """(d) the CLOSE row of a sole close kept qty = ledger (414) on a row
    of 413.76; the response was lost. The venue's position went to 0:
    sold = 414 - 0 = 414, the trade log's one seller sold 413.76, and
    that is what books: row 0.0, ledger 0, no trip."""
    async def _never(slug):
        raise AssertionError("a second positions walk")
    monkeypatch.setattr(le, "_pm_held", _never)
    gone = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0}])
    sell = {"side": "SELL", "ts": NOW - 80, "order_id": "close-9", "order_qty": None, "order_price": None}
    p = _pool(fills=_his(300, sold=300), snap=None)
    b = p.add_book(ledger=414, avg_cost=0.12, state="frozen", frozen_reason="placement_lost",
                   frozen_ts=NOW - 100)
    row = p.rows[b["standing_row_id"]]
    row["filled_shares"] = 413.76
    o = p.add_order(b, side=SELL, wire=0.0, qty=414, kind="flatten_vanished", tif="CLOSE",
                    order_id=None, state="placing", placed_ts=NOW - 90)
    v = _Venue(held={SLUG: 0}, trades=[{**sell, "qty": 413.76, "price": 0.29}])
    st = _tick(p, v, http=gone)
    ent = [x for x in ml._RECENT if x["what"] == "adopted"]
    assert len(ent) == 1 and ent[0]["sold"] == 414 and ent[0]["order"] == "close-9"
    assert o["order_id"] == "close-9" and o["booked_filled"] == pytest.approx(413.76)
    assert row["filled_shares"] == 0.0 and b["ledger_net"] == 0
    assert _census(st, "overfill") == 0 and _census(st, "ledger_dust") == 0 and _census(st, "book_error") == 0
    assert p.state["mirror_live"] is True and b["state"] == "closed" and _census(st, "closed_cashed_out") == 1
    # the log naming the whole 414 (the venue rounds the close up): dust, booked, no trip
    p2 = _pool(fills=_his(300, sold=300), snap=None)
    b2 = p2.add_book(ledger=414, avg_cost=0.12, state="frozen", frozen_reason="placement_lost",
                     frozen_ts=NOW - 100)
    p2.rows[b2["standing_row_id"]]["filled_shares"] = 413.76
    o2 = p2.add_order(b2, side=SELL, wire=0.0, qty=414, kind="flatten_vanished", tif="CLOSE",
                      order_id=None, state="placing", placed_ts=NOW - 90)
    st2 = _tick(p2, _Venue(held={SLUG: 0}, trades=[{**sell, "qty": 414.0, "price": 0.29}]), http=gone)
    assert o2["booked_filled"] == 414.0 and o2["state"] == "filled" and o2["receipt"]["dust_total"] == pytest.approx(0.24)
    assert _census(st2, "ledger_dust") == 1 and _census(st2, "overfill") == 0 and b2["ledger_net"] == 0
    assert p2.rows[b2["standing_row_id"]]["filled_shares"] == 0.0 and p2.state["mirror_live"] is True


@pytest.mark.parametrize("deltas, trips_on", [
    ([1.0, 1.0], 2),        # the reviewer's scenario: a flat row taking one-lot deltas per poll
    ([0.6, 0.6], 2),        # two sub-lot deltas summing past a lot
    ([0.24], None),         # the incident's single 0.24: never
    ([0.3, 0.3, 0.3], None),  # under a lot in total: never
])
def test_dust_accumulates_per_order_and_trips_when_the_total_passes_a_lot(deltas, trips_on, monkeypatch, caplog):
    """A flat row (ledger 0, row 0.0) with a resting SELL the venue fills
    a delta at a time: each delta on its own is within SELL_DUST_SHARES,
    so a per-delta reading would pass a short of any size one lot at a
    time. The order's dust_total is what is judged, kept on the receipt
    JSON so it survives the poll (the worker re-reads the row each
    tick): the delta that takes it past a lot is the overfill -- frozen,
    tripped, dust_total on the receipt."""
    caplog.set_level(logging.INFO)
    p = _pool()
    b = p.add_book(ledger=0, gross_buy=50.0, avg_cost=0.12)
    row = p.rows[b["standing_row_id"]]
    assert row["filled_shares"] == 0.0
    o = p.add_order(b, side=SELL, wire=0.12, qty=3, kind="reduce")
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    monkeypatch.setattr(ml, "_current_stats", t.stats)       # the census the tick would carry
    bk = dict(b)                                             # the tick's own copy of the row, as the read makes it
    filled = 0.0
    for i, d in enumerate(deltas, start=1):
        filled += d
        # the row as the NEXT poll reads it: a fresh dict off the table,
        # the tick's own dust_total gone, the receipt's kept
        o_poll = dict(p.orders[o["id"]])
        assert "dust_total" not in o_poll
        out = _run(ml._book_delta(t, o_poll, bk, {"state": "open", "filled_shares": filled, "avg_px": 0.12},
                                  maker=False))
        total = round(sum(deltas[:i]), 6)
        assert p.orders[o["id"]]["receipt"]["dust_total"] == pytest.approx(total)
        assert o_poll["dust_total"] == pytest.approx(total) and p.orders[o["id"]]["booked_filled"] == pytest.approx(filled)
        if trips_on is not None and i >= trips_on:
            assert out == "overfill" and bk["state"] == "frozen" and bk["frozen_reason"] == "overfill"
            assert b["state"] == "frozen" and b["frozen_reason"] == "overfill"
            assert p.state["mirror_live"] is False
            rec = p.state["mirror_live_trip"]
            assert rec["why"] == "overfill" and rec["dust_total"] == pytest.approx(total)
            assert (rec["sold"], rec["ledger"], rec["held"], rec["order"]) == (pytest.approx(d), 0, 0.0, o["id"])
            assert t.stats["census"]["overfill"] == 1
            assert ("mirror_live: book %s SELL on order %s: dust_total %s on the order is past "
                    "SELL_DUST_SHARES (1.0) -- the overfill, not dust" % (b["id"], o["id"], total)
                    in [r.getMessage() for r in caplog.records])
            assert "MIRROR LIVE TRIPPED OFF: overfill" in caplog.text and "'dust_total': %s" % total in caplog.text
            break
        assert out == "booked" and bk["state"] == "live" and b["state"] == "live" and p.state["mirror_live"] is True
        assert t.stats["census"]["ledger_dust"] == i and t.stats["census"]["overfill"] == 0
        assert ml._RECENT[-1]["what"] == "dust" and ml._RECENT[-1]["dust_total"] == pytest.approx(total)
        assert "TRIPPED OFF" not in caplog.text
    else:
        assert trips_on is None and "mirror_live_trip" not in p.state and b["state"] == "live"
    assert row["filled_shares"] == 0.0 and b["ledger_net"] == 0, "the row never goes negative"
    # the receipt is the place, and the open-orders read carries it back
    assert "receipt" in ml._SQL_ORDERS_OPEN and "ml-order-dust" in ml._SQL_ORDER_DUST
    assert "jsonb_build_object('dust_total', $2::float8)" in ml._SQL_ORDER_DUST
    assert ml._dust_total({"receipt": json.dumps({"dust_total": 0.7})}) == 0.7
    assert ml._dust_total({"receipt": {"dust_total": 0.7}, "dust_total": 0.9}) == 0.9
    assert ml._dust_total({"receipt": None}) == 0.0 and ml._dust_total({"receipt": "nope"}) == 0.0
    assert ml._dust_total({"receipt": {"dust_total": -1.0}}) == 0.0


def test_ledger_dust_is_the_last_census_key_and_no_served_index_moved():
    """The served census is the first 40 names of CENSUS_KEYS (the
    sanitizer's cap); a name added anywhere but the END moves every index
    after it. `venue_halted` (24) and `side_band` (40) are pinned as the
    U9 cap test pinned them, and the whole served prefix is spelled out."""
    from sportsassets.api import app as api_app
    keys = ml.CENSUS_KEYS
    # merged with P2 rung S0: the short side's names are appended AFTER
    # ledger_dust, so ledger_dust keeps its index (right after book_error)
    # and everything before it keeps its own
    assert keys.count("ledger_dust") == 1
    assert keys.index("ledger_dust") == keys.index("book_error") + 1
    # U12: `books_unreadable` appended after the short side's names; the
    # U12c review's two names after it, LAST
    assert keys[keys.index("ledger_dust") + 1] == "short_open"
    assert keys[-4:] == ("books_unreadable", "ratio_stepped", "under_min_notional", "shadow_check_skipped")
    assert keys[-5] == "short_share_cap" and keys.count("books_unreadable") == 1
    assert keys.index("venue_halted") == 24 and keys.index("side_band") == 40
    assert keys.index("overfill") < keys.index("ledger_dust")
    assert keys[:api_app._DETAIL_MAX_KEYS] == (
        "mode_env_off", "mode_db_off", "mode_db_unreadable", "whales_unreadable",
        "tables_absent", "no_venue", "probe_disabled", "halted", "paused",
        "overspend_halt", "mirror_overspend", "overspend_uncheckable",
        "loss_breaker", "loss_breaker_unreadable", "no_budget_room",
        "mirror_day_cap", "mirror_loss_stop", "positions_unreadable",
        "open_orders_unreadable", "protected_ids_unreadable", "tick_abandoned",
        "no_ratio", "no_mark", "no_quote", "venue_halted", "unmapped", "family", "per_side_unsupported",
        "market_closed", "market_unreadable", "game_too_far_out", "mapping", "edge_gate", "cell_gate",
        "clip_zero", "legacy_row", "slug_recent_copy", "underdog_coholds",
        "venue_already_holds", "kalshi_claimed")
    # past the cap by construction, so it rides on the served `integ` block
    ik = ml._INTEG_CENSUS_KEYS
    assert ik.count("ledger_dust") == 1 and ik[ik.index("venue_halted") + 1] == "ledger_dust"
    assert ik[ik.index("ledger_dust") + 1] == "short_reduce_unproven" and ik[-5] == "short_share_cap"
    assert ik[-4:] == ("books_unreadable", "ratio_stepped", "under_min_notional",
                       "shadow_check_skipped"), "served on integ"
    # and `integ` itself stays inside the served 40-key top level
    from sportsassets.api import app as api_app
    order = list(ml._new_stats())
    assert order.index("integ") < api_app._DETAIL_MAX_KEYS and len(order) <= api_app._DETAIL_MAX_KEYS
    assert ml._new_stats()["census"]["ledger_dust"] == 0 and ml._new_stats()["integ"]["ledger_dust"] == 0


# ------------------------------------------ 17. P2 rung S0: the SHORT side
#
# The live lane follows his SHORT side (owner order 2026-09-05, "we need
# to make sure we are mirroring shorts") behind ONE knob,
# rules.MIRROR_SHORTS -- off in this fixture world (the autouse rails),
# ON by default in the code since 2026-09-06 (U12c, "I want shorts live
# as well"; the rules tests pin the default): every section above ran
# with it off and is byte-identical. These drive the knob ON through
# the same fakes: a short book opens by a BUY_SHORT rest at his level, the ledger
# is signed, the day cap and the room read the collateral, a fill above
# the wire's collateral trips, a reduce before rung S4 is refused by name,
# the whole book flattens by close_position when sole, the wrong sign
# trips on either book, the gate and the model stand in front of every
# short open, the 050 column absent keeps the knob off, and the knob off
# on an open short book is the reversal path.

SHORT = "ORDER_INTENT_BUY_SHORT"


def _short_world(**kw):
    """His net NEGATIVE on the fixture market: 100 of the long token
    against 400 of the other, his last move the other token at 0.72 --
    so his level for our short is 1 - 0.72 = 0.28 in long space, the
    ask 0.32 is above it, and the plan rests at the ask: a BUY_SHORT
    whose contract price is 0.32 and whose collateral is 0.68 a share.
    ratio 1.0 x -300 at mark 0.31 caps at $2,500 / 0.69 = 3,623 of
    collateral (U12b; 362 at the $250 it was), so the target is -300
    uncapped."""
    kw.setdefault("fills", _his(100, other_size=400, other_px=0.72))
    kw.setdefault("snap", {M: 100.0, N: 400.0})
    return _pool(**kw)


def _short_http():
    return _mkt(100.0, 400.0)


def _shorts_on(monkeypatch, max_shares=10 ** 6):
    """The knob on, and the short share cap (ONE until rung S5) lifted
    so the sizing tests read the ratio's figure; the cap's own tests
    pass their own."""
    monkeypatch.setattr(rules, "MIRROR_SHORTS", True)
    monkeypatch.setattr(rules, "MIRROR_SHORT_MAX_SHARES", int(max_shares))
    while le._SHORT_LOCK.locked():
        le._SHORT_LOCK.release()


def _short_book(p, ledger=-300, avg=0.32, **over):
    """An open short book of |ledger| shares at contract price `avg`:
    the standing row claims the OTHER token, names BUY_SHORT in
    raw.preview.intent, and holds the leg as a magnitude."""
    leg = abs(ledger)
    b = p.add_book(ledger=ledger, avg_cost=avg, intent=SHORT, gross_buy=round(leg * (1 - avg), 4),
                   peak_exposure_usd=round(leg * (1 - avg), 4), **over)
    row = p.rows[b["standing_row_id"]]
    row.update(asset=N, filled_shares=float(leg), orig_shares=float(leg), fill_price=avg)
    row["raw"]["preview"]["intent"] = SHORT
    return b


def test_with_the_knob_off_his_short_side_is_refused_by_the_p1_name_and_nothing_opens():
    assert rules.MIRROR_SHORTS is False
    p = _short_world()
    v = _Venue()
    st = _tick(p, v, http=_short_http())
    assert st["short"]["on"] is False
    assert _census(st, "short_side_refused") == 1 and not p.books and not _places(v)
    assert _census(st, "short_open") == 0 and _census(st, "sign_flip") == 0


def test_with_the_knob_on_a_short_book_opens_by_a_buy_short_rest_at_his_level(monkeypatch):
    _shorts_on(monkeypatch)
    p = _short_world()
    v = _Venue()
    st = _tick(p, v, http=_short_http())
    assert st["short"]["on"] is True
    b = next(iter(p.books.values()))
    assert b["intent"] == SHORT and b["target"] == -300 and b["his_level"] == pytest.approx(0.28)
    row = p.rows[b["standing_row_id"]]
    assert row["asset"] == N and row["raw"]["preview"]["intent"] == SHORT
    assert row["requested_shares"] == 300.0
    # ONE BUY_SHORT rest, sell=False, post-only, at the contract price
    # ceil(1 - 0.68) = 0.32 -- his 0.28 is under the ask, so the ask
    pl = _places(v)
    assert len(pl) == 1
    assert pl[0][1:] == (SLUG, 0.32, 300, False, "TIME_IN_FORCE_GOOD_TILL_CANCEL", SHORT, True, None)
    o = next(iter(p.orders.values()))
    assert (o["side"], o["intent"], o["kind"], o["tif"]) == (SELL, SHORT, "increase", "GTC")
    assert o["wire"] == 0.32 and o["state"] == "open"
    assert _census(st, "short_open") == 1 and _census(st, "rest_placed") == 1
    assert _census(st, "short_side_refused") == 0
    # the reserve was the collateral and came back off: nothing leaks
    assert le._REST_RESERVED_USD == 0.0
    # the day read prices the resting remainder at 1 - wire
    assert p._run("fetchrow", ml._SQL_MIRROR_DAY, ()) == {"filled": 0.0, "open": pytest.approx(300 * 0.68)}
    # the venue's rest is a SELL of the contract, and the fingerprint agrees
    assert v.orders["oid-1"]["side"] == "SELL"
    assert ml._on_book_matches(o, v.orders["oid-1"], 0.32, 300)


def test_a_short_fill_books_a_signed_ledger_the_collateral_and_the_sign_proof(monkeypatch):
    _shorts_on(monkeypatch)
    p = _short_world()
    v = _Venue()
    _tick(p, v, http=_short_http())
    b = next(iter(p.books.values()))
    # the rest fills at its own cent; the venue now shows -300 on the slug
    v2 = _Venue(held={SLUG: -300}, fills={"oid-1": (300.0, 0.32)})
    v2.orders = v.orders
    st = _tick(p, v2, now=NOW + 30, http=_short_http())
    assert b["ledger_net"] == -300 and b["avg_cost"] == pytest.approx(0.32)
    assert b["gross_buy_usd"] == pytest.approx(204.0) and b["peak_exposure_usd"] == pytest.approx(204.0)
    row = p.rows[b["standing_row_id"]]
    assert row["filled_shares"] == 300.0 and row["fill_price"] == pytest.approx(0.32)
    assert row["filled_usd"] == pytest.approx(le.fill_cash(300, 0.32, SHORT)) == pytest.approx(204.0)
    assert row["requested_usd"] == pytest.approx(300 * 0.68)
    o = next(iter(p.orders.values()))
    assert o["state"] == "filled" and o["cash_usd"] == pytest.approx(204.0)
    assert _census(st, "filled_rest") == 1 and b["state"] == "live"
    assert _census(st, "wrong_sign_trip") == 0 and _census(st, "venue_ledger_disagree") == 0
    # the short leg's at_or_better producer, with its denominator
    assert (st["short"]["fills"], st["short"]["at_or_better"], st["short"]["uncheckable"]) == (1, 1, 0)
    assert st["integ"]["short_fills"] == 1
    # THE SIGN PROOF: venue == ledger, both negative, nothing of the
    # desk's beside them -- recorded ONCE into the gate's own tally
    assert p.state["short_side_proof"]["ok"] == 1 and p.state["short_side_proof"]["mismatch"] == 0
    assert b["last_plan"]["short_proof"] == "ok"
    st3 = _tick(p, _Venue(held={SLUG: -300}), now=NOW + 60, http=_short_http())
    assert _census(st3, "on_target") == 1 and p.state["short_side_proof"]["ok"] == 1
    assert b["last_plan"]["short_proof"] == "ok"
    # and the shadow's reading of the same inputs agrees with the signed target
    assert _census(st3, "shadow_live_disagree") == 0


def test_a_short_fill_that_cost_more_than_the_wires_collateral_trips_overspend(monkeypatch):
    _shorts_on(monkeypatch)
    p = _short_world()
    v = _Venue()
    _tick(p, v, http=_short_http())
    b = next(iter(p.books.values()))
    # filled at contract price 0.30 against a 0.32 wire: we paid 0.70 a
    # share for a 0.68 authorisation -- a whole cent over
    v2 = _Venue(held={SLUG: -300}, fills={"oid-1": (300.0, 0.30)})
    v2.orders = v.orders
    st = _tick(p, v2, now=NOW + 30, http=_short_http())
    assert _census(st, "mirror_overspend") >= 1 and p.state["mirror_live"] is False
    assert b["frozen_reason"] == "mirror_overspend" and b["ledger_net"] == -300
    assert st["short"]["fills"] == 1 and st["short"]["at_or_better"] == 0
    # the predicate itself, in cost space on a short row
    row = {"side": SELL, "tif": "GTC", "wire": 0.32}
    assert ml._overspend_of(row, {"avg_px": 0.32}, SHORT) is False
    assert ml._overspend_of(row, {"avg_px": 0.315}, SHORT) is False, "the half-cent grid"
    assert ml._overspend_of(row, {"avg_px": 0.33}, SHORT) is False, "a better sale"
    assert ml._overspend_of(row, {"avg_px": 0.31}, SHORT) is True
    assert ml._overspend_of(row, {"avg_px": None}, SHORT) is None
    # a short book's cover rows and its CLOSE never trip; a long row reads as before
    assert ml._overspend_of({"side": BUY, "tif": "GTC", "wire": 0.30}, {"avg_px": 0.20}, SHORT) is False
    assert ml._overspend_of({"side": BUY, "tif": "CLOSE", "wire": 0.0}, {"avg_px": 0.29}, SHORT) is False
    assert ml._overspend_of({"side": BUY, "tif": "GTC", "wire": 0.30}, {"avg_px": 0.31}) is True
    assert ml._overspend_of({"side": BUY, "tif": "GTC", "wire": 0.30}, {"avg_px": 0.31}, INTENT) is True


def test_a_partial_reduce_of_a_short_is_refused_by_name_before_rung_s4_and_nothing_is_sent(monkeypatch):
    _shorts_on(monkeypatch)
    # his net moved up from -300 to -100: 300 long against 400 other
    p = _short_world(fills=_his(300, other_size=400, other_px=0.72), snap={M: 300.0, N: 400.0})
    b = _short_book(p, ledger=-300)
    v = _Venue(held={SLUG: -300})
    st = _tick(p, v, http=_mkt(300.0, 400.0))
    assert b["target"] == -100 and b["last_plan"]["side"] == BUY and b["last_plan"]["qty"] == 200
    assert _census(st, "short_reduce_unproven") == 1
    assert not _places(v) and "close" not in _kinds(v) and not p.orders
    assert b["state"] == "live" and b["ledger_net"] == -300
    assert b["last_plan"]["short_reduce"] == "unproven" and st["integ"]["short_reduce_unproven"] == 1
    # his level for a short REDUCE is his long-token BUY at p (0.31),
    # so the cover the lane would rest -- unsent -- is priced off it
    assert b["last_plan"]["his_level"] == pytest.approx(0.31)


def test_a_sole_held_short_flattens_by_close_position_and_a_co_held_one_is_refused_by_name(monkeypatch):
    _shorts_on(monkeypatch)
    # his net gone to zero while he still holds both tokens: the paired flatten
    p = _short_world(fills=_his(400, other_size=400, other_px=0.72), snap={M: 400.0, N: 400.0})
    b = _short_book(p, ledger=-300)
    v = _Venue(held={SLUG: -300})
    st = _tick(p, v, http=_mkt(400.0, 400.0))
    assert b["target"] == 0 and b["last_plan"]["kind"] == "flatten_paired"
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v.calls and "place" not in _kinds(v)
    assert "slug_bid" not in _kinds(v)
    o = next(iter(p.orders.values()))
    assert (o["kind"], o["side"], o["tif"], o["intent"]) == ("flatten_paired", BUY, "CLOSE",
                                                              "ORDER_INTENT_SELL_SHORT")
    assert o["qty"] == 300 and o["state"] == "filled"
    # the close filled 300 @ 0.29: the leg covers to zero, realized (0.32 - 0.29) x 300
    assert b["ledger_net"] == 0 and b["realized_pnl"] == pytest.approx(9.0)
    assert b["gross_sell_usd"] == pytest.approx(300 * 0.71)
    assert p.rows[b["standing_row_id"]]["filled_shares"] == 0.0
    assert p.rows[b["standing_row_id"]]["pnl"] == pytest.approx(9.0)
    assert _census(st, "short_flatten_close") == 1 and st["flattened"] == 1
    assert _census(st, "overfill") == 0 and _census(st, "short_reduce_unproven") == 0
    # flat at target 0 on a live market: the row stays 'filled' at 0 and
    # the episode waits its flat hour like any other
    assert p.rows[b["standing_row_id"]]["status"] == "filled" and b["last_plan"]["close"] == "not_due"

    # CO-HELD: someone else's half share beside our 300 (the walk sees
    # -300.5, the fresh read floors to 300, the two readings disagree:
    # NOT sole, section 14's rule) -- the whole-slug close would take a
    # stranger's leg with ours, and the co-held IOC is a SELL_SHORT
    # nobody has read back: refused by name, held, nothing sent. A
    # same-sign co-hold the ledger cannot explain freezes the book
    # `venue_ledger_disagree` first, as it does a long book (R7)
    p2 = _short_world(fills=_his(400, other_size=400, other_px=0.72), snap={M: 400.0, N: 400.0})
    b2 = _short_book(p2, ledger=-300)
    v2 = _Venue(held={SLUG: -300.5})
    st2 = _tick(p2, v2, http=_mkt(400.0, 400.0))
    assert "close" not in _kinds(v2) and not _places(v2) and "slug_bid" not in _kinds(v2)
    assert _census(st2, "flatten_holding_disagrees") == 1
    assert _census(st2, "short_reduce_unproven") == 1 and b2["ledger_net"] == -300
    assert b2["state"] == "live" and not p2.orders
    p3 = _short_world(fills=_his(400, other_size=400, other_px=0.72), snap={M: 400.0, N: 400.0})
    b3 = _short_book(p3, ledger=-300)
    st3 = _tick(p3, _Venue(held={SLUG: -500}), http=_mkt(400.0, 400.0))
    assert b3["frozen_reason"] == "venue_ledger_disagree" and _census(st3, "wrong_sign_trip") == 0


def test_a_confirmed_vanish_on_a_short_closes_at_once_by_close_position_with_no_rest_first(monkeypatch):
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
             _fill(N, "SELL", 400, 0.70, NOW - 2000), _fill(M, "SELL", 100, 0.31, NOW - 1000)]
    p = _short_world(fills=fills, snap=None)
    b = _short_book(p, ledger=-300)
    v = _Venue(held={SLUG: -300})
    http = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0},
                       {"conditionId": CID, "asset": N, "size": 0}])
    st = _tick(p, v, http=http)
    # the vanish was confirmed on the token carrying his net, the OTHER one
    assert any(c[1].get("market") == CID for c in http.calls)
    assert _census(st, "flatten_vanished") == 1 and _census(st, "flatten_rested") == 0
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v.calls and "place" not in _kinds(v)
    o = next(iter(p.orders.values()))
    assert o["kind"] == "flatten_vanished" and o["tif"] == "CLOSE"
    assert b["ledger_net"] == 0 and _census(st, "short_flatten_close") == 1
    # flat on a confirmed vanish: the episode closes without the flat hour
    assert b["state"] == "closed"


def test_the_wrong_sign_trip_is_symmetric_and_records_the_mismatch_on_a_short(monkeypatch):
    _shorts_on(monkeypatch)
    p = _short_world()
    b = _short_book(p, ledger=-300)
    p.add_order(b, side=SELL, wire=0.32, qty=100, kind="increase")
    v = _Venue(held={SLUG: 300})            # the venue holds the LONG side of what we booked short
    v.rest("oid-1", "SELL", 0.32, 100)
    st = _tick(p, v, http=_short_http())
    assert p.state["mirror_live"] is False and p.state["mirror_live_trip"]["why"] == "wrong_sign_trip"
    assert b["state"] == "frozen" and b["frozen_reason"] == "wrong_sign_trip"
    assert _cancels(v) and not _places(v) and _census(st, "wrong_sign_trip") == 1
    assert p.state["short_side_proof"]["mismatch"] == 1 and b["last_plan"]["short_proof"] == "mismatch"
    # and the gate is shut for every lane from here
    assert _run(le._short_gate(p))[0] is False
    # the long direction is what it was (section 5 pins it); a zero on
    # either side is a disagreement, never a sign
    p2 = _short_world()
    b2 = _short_book(p2, ledger=-300)
    st2 = _tick(p2, _Venue(held={}), http=_short_http())
    assert b2["frozen_reason"] == "venue_ledger_disagree" and _census(st2, "wrong_sign_trip") == 0


def test_net_for_reads_the_smaller_reading_toward_zero_only_with_the_knob_on():
    dr = rules.DriftRule(False, "smaller", "drift", 0.2)

    def r_(his_long, his_other, mkt_long, mkt_other):
        return _reading(his_long=his_long, his_other=his_other, snap_market_fresh=True,
                        mkt_long=mkt_long, mkt_other=mkt_other,
                        mkt_net=ms.mi.his_net(mkt_long, mkt_other))
    # derived -100 vs snapshot -50: toward zero is -50 either way round
    assert ml._net_for(r_(0, 100, 0, 50), dr, short=True)[0] == -50.0
    assert ml._net_for(r_(0, 50, 0, 100), dr, short=True)[0] == -50.0
    # readings that disagree on his SIDE justify holding neither
    assert ml._net_for(r_(100, 0, 0, 50), dr, short=True)[0] == 0.0
    # the long side is unchanged, and so is min() with the knob off
    assert ml._net_for(r_(100, 0, 50, 0), dr, short=True)[0] == 50.0
    assert ml._net_for(r_(0, 100, 0, 50), dr)[0] == -100.0
    assert ml._net_for(r_(100, 0, 0, 50), dr)[0] == -50.0
    assert ml._net_for(r_(100, 0, 50, 0), dr)[0] == 50.0


def test_the_kalshi_claim_reads_the_token_we_are_on_for_a_short_candidate(monkeypatch):
    _shorts_on(monkeypatch)
    p = _short_world()
    p.kalshi = {N}
    st = _tick(p, _Venue(), http=_short_http())
    assert _census(st, "kalshi_claimed") == 1 and not p.books
    p2 = _short_world()
    p2.kalshi = {M}                          # the long token claimed does not bind the short book
    st2 = _tick(p2, _Venue(), http=_short_http())
    assert _census(st2, "kalshi_claimed") == 0 and p2.books
    # a per-fill row of EITHER sign on the slug refuses admission (Q12 (c))
    for asset, intent in ((M, INTENT), (N, SHORT)):
        p3 = _short_world()
        p3.add_row(asset=asset, us_market_slug=SLUG, status="filled",
                   raw={"preview": {"intent": intent}})
        st3 = _tick(p3, _Venue(), http=_short_http())
        assert _census(st3, "legacy_row") == 1 and not p3.books, intent


def test_the_two_doors_stand_in_front_of_every_short_open(monkeypatch):
    _shorts_on(monkeypatch)
    # H2: the short cost model disarmed refuses the open by name
    monkeypatch.setattr(le, "short_model_confirmed", lambda: False)
    p = _short_world()
    st = _tick(p, _Venue(), http=_short_http())
    assert _census(st, "short_model_disarmed") == 1 and not p.books
    # and an ADD to an open short book: the resting add is cancelled under the name
    p2 = _short_world()
    b2 = _short_book(p2, ledger=-100)
    p2.add_order(b2, side=SELL, wire=0.32, qty=200, kind="increase")
    v2 = _Venue(held={SLUG: -100})
    v2.rest("oid-1", "SELL", 0.32, 200)
    st2 = _tick(p2, v2, http=_short_http())
    assert _census(st2, "short_model_disarmed") >= 1 and _cancels(v2) and not _places(v2)
    assert p2.orders[next(iter(p2.orders))]["reason"] == "short_model_disarmed"
    monkeypatch.setattr(le, "short_model_confirmed", lambda: True)
    # H1: one mismatch in the tally shuts the gate for the mirror too
    p3 = _short_world()
    p3.state["short_side_proof"] = {"ok": 5, "mismatch": 1}
    st3 = _tick(p3, _Venue(), http=_short_http())
    assert _census(st3, "short_gate_refused") == 1 and not p3.books
    # a probation short in flight on the per-fill lane holds the mirror's too
    p4 = _short_world()
    _run(le._SHORT_LOCK.acquire())
    try:
        st4 = _tick(p4, _Venue(), http=_short_http())
    finally:
        le._SHORT_LOCK.release()
    assert _census(st4, "short_gate_refused") == 1 and not p4.books
    # the mirror NEVER takes the lock: it has no echo to release it
    assert "_SHORT_LOCK" not in inspect.getsource(ml)
    # a take on a short add passes the same doors: the arm alone never places
    p5 = _short_world()
    p5.state["short_side_proof"] = {"ok": 0, "mismatch": 1}
    b5 = _short_book(p5, ledger=-100, take_armed_ts=NOW - 130)
    st5 = _tick(p5, _Venue(bid=0.33, ask=0.35, held={SLUG: -100}), http=_short_http())
    assert not _places(_Venue()) and _census(st5, "short_gate_refused") >= 1 and b5["state"] == "live"


def test_the_050_column_absent_keeps_the_knob_off_by_name_and_sends_the_047_statements(monkeypatch):
    """The database BEFORE 050 (the honest fake: every statement naming
    mirror_orders.intent is UndefinedColumnError, not the guard alone).
    The knob is off by name, every statement sent is the 047 shape --
    the DAY READ included: the 050 text against that database raised
    on every tick and read `mirror_day_cap` for good, so no long book
    opened (review, migration lens) -- and a long book still rests."""
    _shorts_on(monkeypatch)
    p = _short_world()
    p.no_intent_column = True
    v = _Venue()
    st = _tick(p, v, http=_short_http())
    assert st["short"]["on"] is False and _census(st, "short_column_absent") == 1
    assert st["short_column_absent"] == "UndefinedColumnError" and st["status"] == "ok"
    assert _census(st, "short_side_refused") == 1 and not p.books and not _places(v)
    assert st["integ"]["short_column_absent"] == 1
    # the 047-shaped statements went out: no intent column named anywhere,
    # the day read included, and the day rail read its real room
    opens = [s for k, s, a in p.sent if "ml-orders-open" in s]
    assert opens and all("intent" not in s for s in opens)
    days = [s for k, s, a in p.sent if "ml-mirror-day" in s]
    assert days and all("intent" not in s for s in days)
    assert _census(st, "mirror_day_cap") == 0 and st["mirror_day_room"] == pytest.approx(rules.MIRROR_DAY_USD)
    # a long book on the same database opens and rests through the 047
    # INSERT (18 parameters), with the day read answering
    p2 = _pool()
    p2.no_intent_column = True
    v2 = _Venue()
    st2 = _tick(p2, v2)
    assert _places(v2) and p2.books and _census(st2, "short_column_absent") == 1
    assert _census(st2, "mirror_day_cap") == 0 and _census(st2, "rest_placed") == 1
    ins = [a for k, s, a in p2.sent if "ml-order-insert" in s]
    assert ins and all(len(a) == 18 for a in ins)
    assert all("intent" not in s for k, s, a in p2.sent if "ml-mirror-day" in s)
    assert next(iter(p2.orders.values()))["intent"] == INTENT
    # the rows the 047 open-orders read hands back carry no intent key,
    # and the tick reconciles them: the rest is kept, its intent derived
    p5 = _pool()
    p5.no_intent_column = True
    b5 = p5.add_book(ledger=0)
    o5 = p5.add_order(b5, wire=0.30, qty=300)
    assert all("intent" not in row for row in p5._run("fetch", ml._SQL_ORDERS_OPEN_047, ()))
    v5 = _Venue()
    v5.rest("oid-1", "BUY", 0.30, 300)
    st5 = _tick(p5, v5)
    assert _census(st5, "open_order_pending") == 1 and not _cancels(v5) and not _places(v5)
    assert p5.orders[o5["id"]]["state"] == "open" and _census(st5, "book_error") == 0
    # the driver's TEXT for a missing column reads as absence too
    p6 = _pool()
    p6.raise_on.append(("ml-intent-guard", _Undefined('column "intent" does not exist')))
    st6 = _tick(p6, _Venue())
    assert _census(st6, "short_column_absent") == 1 and st6["short_column_absent"] == "UndefinedTableError"
    # with the knob off the absence is silent: nothing named, the same statements
    monkeypatch.setattr(rules, "MIRROR_SHORTS", False)
    p3 = _pool()
    p3.no_intent_column = True
    st3 = _tick(p3, _Venue())
    assert _census(st3, "short_column_absent") == 0 and "short_column_absent" not in st3
    assert p3.books and _census(st3, "mirror_day_cap") == 0
    # present: the 050 INSERT carries the wire intent as its nineteenth parameter
    p4 = _pool()
    _tick(p4, _Venue())
    ins4 = [a for k, s, a in p4.sent if "ml-order-insert" in s]
    assert ins4 and all(len(a) == 19 and a[18] == INTENT for a in ins4)
    # the guard is the shadow's statement: both lanes probe with one text
    assert ml._SQL_INTENT_GUARD == ms.INTENT_GUARD_SQL and "ml-intent-guard" in ms.INTENT_GUARD_SQL


@pytest.mark.parametrize("exc", [RuntimeError("connection reset by peer"),
                                 TimeoutError("statement timeout")])
def test_a_transient_intent_guard_error_refuses_the_tick_and_never_flattens_a_short(monkeypatch, exc):
    """The guard once swallowed EVERY exception as 'column absent': a
    blip on that one SELECT with the knob on read the knob off for the
    tick and market-closed every sole short book at EXIT_SLIPPAGE_BIPS,
    recording the cover through the 047 INSERT as BUY_LONG (review,
    migration and sign/money lenses). Only a genuine undefined-column
    error is absence; anything else refuses the tick like the table
    guard."""
    _shorts_on(monkeypatch)
    p = _short_world()
    b = _short_book(p, ledger=-300)
    p.raise_on.append(("ml-intent-guard", exc))
    v = _Venue(held={SLUG: -300})
    st = _tick(p, v, http=_short_http())
    assert st["status"] == "degraded" and st["intent_guard_unreadable"] == type(exc).__name__
    assert _census(st, "intent_guard_unreadable") == 1 and st["integ"]["intent_guard_unreadable"] == 1
    assert _census(st, "short_column_absent") == 0 and "short_column_absent" not in st
    assert "close" not in _kinds(v) and not _places(v) and not p.orders
    assert b["ledger_net"] == -300 and b["state"] == "live" and b["target"] is None
    assert _census(st, "short_flatten_close") == 0 and _census(st, "short_side_refused") == 0
    assert st["mirror_day_room"] is None, "the tick stopped at the guard"
    # a genuine absence keeps the degrade: the knob off by name, and the
    # short book HELD -- its close row names SELL_SHORT, which only the
    # 050 INSERT can carry, so the flatten is refused by name rather
    # than written through the 047 statement as a BUY_LONG
    p2 = _short_world()
    b2 = _short_book(p2, ledger=-300)
    p2.no_intent_column = True
    v2 = _Venue(held={SLUG: -300})
    st2 = _tick(p2, v2, http=_short_http())
    assert st2["status"] == "ok" and st2["short"]["on"] is False
    assert _census(st2, "short_column_absent") >= 2 and _census(st2, "short_side_refused") == 1
    assert "close" not in _kinds(v2) and not _places(v2) and not p2.orders
    assert b2["ledger_net"] == -300 and b2["target"] == 0 and b2["last_plan"]["short_column"] == "absent"
    assert _census(st2, "short_flatten_close") == 0 and _census(st2, "intent_guard_unreadable") == 0
    # the column back and the knob off (the reversal path): the same book
    # flattens by close_position, its close row through the 050 INSERT
    p2.no_intent_column = False
    monkeypatch.setattr(rules, "MIRROR_SHORTS", False)
    v3 = _Venue(held={SLUG: -300})
    st3 = _tick(p2, v3, now=NOW + 30, http=_short_http())
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v3.calls and b2["ledger_net"] == 0
    ins = [a for k, s, a in p2.sent if "ml-order-insert" in s]
    assert ins and all(len(a) == 19 and a[18] == "ORDER_INTENT_SELL_SHORT" for a in ins)
    assert _census(st3, "short_flatten_close") == 1


@pytest.mark.parametrize("prices,payout_long,own", [([0, 1], 1.0, -204.0), ([1, 0], 0.0, 96.0)])
def test_a_short_books_own_settlement_figure_is_the_legs_and_agrees_with_the_venue(
        monkeypatch, prices, payout_long, own):
    """_close_settled on a short book (brief E3): own = realized +
    shares x (payout - avg) with the ledger SIGNED, i.e. leg x (avg -
    payout_long). A short of 300 @ 0.32: the long token paying 1 costs
    the short 300 x 0.68 = -204; paying 0 earns 300 x 0.32 = +96. The
    venue's own figure on the standing row agrees, so
    book_settle_disagree stays 0 (mutation lens, mutant g)."""
    _shorts_on(monkeypatch)
    p = _short_world()
    b = _short_book(p, ledger=-300, avg=0.32)
    row = p.rows[b["standing_row_id"]]
    row.update(status="settled", pnl=300 * (0.32 - payout_long))
    p.markets[CID] = {"closed": True, "resolved": True, "resolved_prices": prices}
    st = _tick(p, _Venue(held={SLUG: -300}), http=_short_http())
    assert b["state"] == "closed"
    assert b["own_book_pnl"] == pytest.approx(own)
    assert b["settled_pnl"] == pytest.approx(own) and b["settle_disagree"] is False
    assert _census(st, "book_settle_disagree") == 0 and st["closed_books"] == 1


def test_a_pre_050_sell_rest_back_filled_buy_long_is_kept_not_replaced():
    """050's DEFAULT back-fills BUY_LONG onto every pre-050 row, a
    resting SELL_LONG included; a reader that trusted the column would
    compare it to the plan's SELL_LONG and REPLACE every such rest on
    the first tick after the migration (review, migration lens). On a
    long book the plan side decides the wire intent alone."""
    # control: the same rest with the intent the 050 INSERT writes
    p0 = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    b0 = p0.add_book(ledger=300)
    p0.add_order(b0, side=SELL, wire=0.32, qty=200, kind="reduce")
    v0 = _Venue(held={SLUG: 300})
    v0.rest("oid-1", "SELL", 0.32, 200)
    st0 = _tick(p0, v0)
    assert not _cancels(v0) and _census(st0, "open_order_pending") == 1
    # the pre-050 row after the ALTER's back-fill: kept, not replaced
    p = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    b = p.add_book(ledger=300)
    o = p.add_order(b, side=SELL, wire=0.32, qty=200, kind="reduce", intent="ORDER_INTENT_BUY_LONG")
    v = _Venue(held={SLUG: 300})
    v.rest("oid-1", "SELL", 0.32, 200)
    st = _tick(p, v)
    assert not _cancels(v) and not _places(v) and _census(st, "open_order_pending") == 1
    assert p.orders[o["id"]]["state"] == "open" and _census(st, "requote") == 0
    assert ml._order_intent(o, b) == "ORDER_INTENT_SELL_LONG"
    assert ml._order_intent({"side": BUY, "intent": "ORDER_INTENT_SELL_SHORT"}, b) == INTENT
    # on a short book the column is read only for the two short spellings
    sb = {"intent": SHORT}
    assert ml._order_intent({"side": SELL, "intent": "ORDER_INTENT_BUY_LONG"}, sb) == SHORT
    assert ml._order_intent({"side": SELL}, sb) == SHORT
    assert ml._order_intent({"side": BUY, "intent": "ORDER_INTENT_SELL_SHORT"}, sb) == "ORDER_INTENT_SELL_SHORT"
    assert ml._order_intent({"side": BUY, "intent": "ORDER_INTENT_BUY_LONG"}, sb) == "ORDER_INTENT_SELL_SHORT"


def test_a_mixed_sign_co_hold_on_a_short_is_never_sole_and_nothing_closes(monkeypatch):
    """The desk long 100 (explained by _SQL_MANUAL_SHARES) beside our
    short of 300: the venue nets -200 and both sole readings compare
    magnitudes (300 >= 200), so close_position would close the NET --
    200 of ours covered, the desk's long netted away, the book left at
    -100 against a venue of 0 with nothing named (review, sign lens).
    A short is sole only with nothing of the desk's beside it."""
    _shorts_on(monkeypatch)
    p = _short_world(fills=_his(400, other_size=400, other_px=0.72), snap={M: 400.0, N: 400.0})
    b = _short_book(p, ledger=-300)
    p.manual_shares[SLUG] = 100.0

    async def _held(slug):
        return 200, 0.31
    monkeypatch.setattr(le, "_pm_held", _held)
    v = _Venue(held={SLUG: -200},
               close={"ok": True, "order_id": "close-1", "status": "filled", "fill_price": 0.29,
                      "filled_shares": 200.0, "raw": {}})
    st = _tick(p, v, http=_mkt(400.0, 400.0))
    assert b["target"] == 0 and b["last_plan"]["kind"] == "flatten_paired"
    assert "close" not in _kinds(v) and not _places(v) and not p.orders
    assert _census(st, "short_reduce_unproven") == 1 and _census(st, "short_flatten_close") == 0
    assert _census(st, "venue_ledger_disagree") == 0 and _census(st, "flatten_holding_disagrees") == 0
    assert b["ledger_net"] == -300 and b["state"] == "live" and b["frozen_reason"] is None
    # and no sign proof is read off a mixed-sign co-hold either
    assert p.state.get("short_side_proof") is None and (b["last_plan"] or {}).get("short_proof") is None


def test_the_sign_proof_mismatch_is_recorded_only_on_a_magnitude_match(monkeypatch):
    """wrong_sign_trip on a short book records short_side_proof's
    mismatch -- the tally that shuts le._short_gate for EVERY lane --
    only on the genuine inversion, the venue's magnitude at the leg's;
    any other sign disagreement keeps the trip and the freeze and never
    touches the shared tally (review, sign lens)."""
    _shorts_on(monkeypatch)
    p = _short_world()
    b = _short_book(p, ledger=-300)
    st = _tick(p, _Venue(held={SLUG: 300}), http=_short_http())
    assert _census(st, "wrong_sign_trip") == 1 and b["frozen_reason"] == "wrong_sign_trip"
    assert p.state["short_side_proof"]["mismatch"] == 1 and b["last_plan"]["short_proof"] == "mismatch"
    assert _run(le._short_gate(p))[0] is False
    # a foreign long of 800 beside our short of 300: venue +500, not our inversion
    p2 = _short_world()
    b2 = _short_book(p2, ledger=-300)
    st2 = _tick(p2, _Venue(held={SLUG: 500}), http=_short_http())
    assert _census(st2, "wrong_sign_trip") == 1 and b2["frozen_reason"] == "wrong_sign_trip"
    assert p2.state["mirror_live"] is False and not _places(_Venue())
    assert "short_side_proof" not in p2.state and (b2["last_plan"] or {}).get("short_proof") is None
    # the tolerance is the plan's own, one share
    p3 = _short_world()
    _short_book(p3, ledger=-300)
    _tick(p3, _Venue(held={SLUG: 301}), http=_short_http())
    assert p3.state["short_side_proof"]["mismatch"] == 1


def _shadow_row_from(p, at_ts):
    """The row the shadow's tick_once just wrote, as the live lane's
    ml-shadow-latest read hands it back."""
    ins = [a for k, s, a in p.sent if "INSERT INTO mirror_shadow" in s]
    assert len(ins) == 1, len(ins)
    a = ins[-1]
    return {"whale": a[0], "condition_id": a[1], "his_net": a[7], "ratio": a[10], "target": a[11],
            "target_raw": a[12], "capped": a[13], "at_ts": at_ts}


def test_the_shadow_reads_the_effective_knob_so_env_on_and_050_unapplied_never_disagree(monkeypatch):
    """E5: the shadow's tick_once once read rules.MIRROR_SHORTS alone
    while the live lane read the knob AND the 050 column; env on with
    050 unapplied wrote a negative shadow target against a live target
    of 0 and shadow_live_disagree tripped by construction on every
    negative-net book (all three lenses). Both lanes now probe the
    column with one statement and read one effective knob."""
    _shorts_on(monkeypatch)
    p = _short_world()
    b = p.add_book(ledger=300)
    p.no_intent_column = True
    v = _Venue(held={SLUG: 300})
    sh = _run(ms.tick_once(p, v, now_ts=NOW))
    assert sh["rows"] == 1
    row = _shadow_row_from(p, NOW)
    assert row["target"] == 0 and row["his_net"] == pytest.approx(-300.0) and row["ratio"] == 1.0
    p.shadow.append(row)
    st = _tick(p, _Venue(held={SLUG: 300}), http=_short_http())
    assert _census(st, "short_column_absent") == 1 and b["target"] == 0
    assert _census(st, "short_side_refused") == 1 and _census(st, "sign_flip") == 0
    assert _census(st, "shadow_live_disagree") == 0
    # the column present, same env: the shadow writes the signed target
    # and the live lane compares its unclamped figure -- no disagreement
    p2 = _short_world()
    b2 = p2.add_book(ledger=300)
    v2 = _Venue(held={SLUG: 300})
    _run(ms.tick_once(p2, v2, now_ts=NOW))
    row2 = _shadow_row_from(p2, NOW)
    assert row2["target"] == -300
    p2.shadow.append(row2)
    st2 = _tick(p2, _Venue(held={SLUG: 300}), http=_short_http())
    assert _census(st2, "sign_flip") == 1 and b2["target"] == 0
    assert _census(st2, "shadow_live_disagree") == 0
    # the probe is one text for both lanes, and a probe failing for any
    # other reason is a blip on the shadow's side, not a schema fact: the
    # live-compared target is written NULL (never the long-only 0 this
    # lane, whose probe answered, would disagree with) and the shadow's
    # tick is degraded under the live lane's own name (P2 re-review)
    assert any("ml-intent-guard" in s for k, s, a in p.sent)
    p3 = _short_world()
    p3.raise_on.append(("ml-intent-guard", RuntimeError("connection reset")))
    sh3 = _run(ms.tick_once(p3, _Venue(held={SLUG: 300}), now_ts=NOW))
    assert _shadow_row_from(p3, NOW)["target"] is None
    assert sh3["status"] == "degraded" and sh3["intent_guard_unreadable"] == "RuntimeError"


# (m4) the candidate's own model pre-check is kept and pinned: the
# model is named BEFORE the referees are read
def test_a_disarmed_model_is_named_before_the_candidates_referees_are_read(monkeypatch):
    _shorts_on(monkeypatch)
    monkeypatch.setattr(le, "short_model_confirmed", lambda: False)
    p = _short_world()
    p.kalshi = {N}                       # the token we would be on is claimed
    st = _tick(p, _Venue(), http=_short_http())
    assert _census(st, "short_model_disarmed") == 1 and not p.books
    assert _census(st, "kalshi_claimed") == 0, "the model is read before the referees"
    assert not [s for k, s, a in p.sent if "ml-kalshi" in s], "no referee read on a disarmed model"


# (ad) a short REDUCE take is never sent before rung S4, even with the arm live
def test_a_short_reduce_take_is_refused_by_name_and_no_sell_short_ioc_goes_out(monkeypatch):
    _shorts_on(monkeypatch)
    # his net moved up from -300 to -100: the plan is a BUY_LONG cover of 200
    p = _short_world(fills=_his(300, other_size=400, other_px=0.72), snap={M: 300.0, N: 400.0})
    b = _short_book(p, ledger=-300, take_armed_ts=NOW - 130)
    # a locked book at the cover's wire (his long BUY 0.31 floors to the 0.30 bid): the take would fire
    v = _Venue(bid=0.30, ask=0.30, held={SLUG: -300}, ioc_fill=300.0)
    st = _tick(p, v, http=_mkt(300.0, 400.0))
    assert b["target"] == -100 and b["last_plan"]["side"] == BUY
    assert not _places(v) and "close" not in _kinds(v), _places(v)
    assert _census(st, "take_placed") == 0 and _census(st, "short_reduce_unproven") == 1
    assert b["ledger_net"] == -300 and not p.orders


# (h3) the reserve a short rest commits while in flight is its collateral
def test_the_reserve_held_during_a_short_placement_is_the_collateral(monkeypatch):
    _shorts_on(monkeypatch)
    seen = {}

    def _place(venue, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        seen["reserved"] = float(le._REST_RESERVED_USD or 0.0)
        seen["order"] = (price, qty, sell, intent)
        venue.rest(oid, "SELL", price, qty, slug)
        return {"ok": False, "order_id": oid, "status": "new", "fill_price": None,
                "filled_shares": 0.0, "raw": {"response": {"id": oid}}}

    p = _short_world()
    v = _Venue(place=_place)
    _tick(p, v, http=_short_http())
    assert seen["order"] == (0.32, 300, False, SHORT)
    assert seen["reserved"] == pytest.approx(300 * 0.68), "the reserve is 1 - wire a share on a short"
    assert le._REST_RESERVED_USD == 0.0


# (af) a lost close on a short book is sized off the LEG of the venue's negative position
def test_a_lost_close_on_a_short_reads_the_venues_negative_position_as_the_leg(monkeypatch):
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
             _fill(N, "SELL", 400, 0.70, NOW - 2000), _fill(M, "SELL", 100, 0.31, NOW - 1000)]
    gone = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0},
                       {"conditionId": CID, "asset": N, "size": 0}])

    class _V(_Venue):
        def close_position(self, slug, *, slippage_bips):
            self.calls.append(("close", slug, slippage_bips))
            raise TimeoutError("read timed out")

    p = _short_world(fills=fills, snap=None)
    b = _short_book(p, ledger=-300)
    v = _V(held={SLUG: -300})
    st = _tick(p, v, http=gone)
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v.calls
    row = next(x for x in p.orders.values() if x["tif"] == "CLOSE")
    assert row["state"] == "placing" and b["frozen_reason"] == "placement_lost"
    assert _census(st, "placement_lost") == 1
    # nothing left the account: the venue still shows -300, the leg is 300, nothing sold
    v2 = _Venue(held={SLUG: -300})
    st2 = _tick(p, v2, now=NOW + 90, http=gone)
    assert "trades" not in _kinds(v2), "a leg read as -300 would look like 600 sold"
    assert row["state"] == "placing" and b["ledger_net"] == -300 and _census(st2, "book_error") == 0
    # the position went to 0: the close executed, 300 covered from the trade log (BUYs of the contract)

    async def _held0(slug):
        return 0, None
    monkeypatch.setattr(le, "_pm_held", _held0)
    buy = {"side": "BUY", "ts": NOW + 1, "order_id": "close-9", "order_qty": None, "order_price": None}
    v3 = _Venue(held={SLUG: 0}, trades=[{**buy, "qty": 300.0, "price": 0.29}])
    st3 = _tick(p, v3, now=NOW + 120, http=gone)
    assert row["state"] == "filled" and row["booked_filled"] == 300.0 and b["ledger_net"] == 0
    assert b["realized_pnl"] == pytest.approx((0.32 - 0.29) * 300)
    assert _census(st3, "book_error") == 0


# (ah) the sign proof is recorded only with nothing of the desk's beside our short
def test_no_sign_proof_is_recorded_while_the_desk_holds_shares_on_the_slug(monkeypatch):
    _shorts_on(monkeypatch)
    p = _short_world()
    b = _short_book(p, ledger=-300)
    p.manual_shares = {SLUG: 100.0}            # the desk's 100 long beside our 300 short
    st = _tick(p, _Venue(held={SLUG: -200}), http=_short_http())
    assert _census(st, "venue_ledger_disagree") == 0 and b["state"] == "live"
    assert p.state.get("short_side_proof") is None, "a mixed-sign co-hold is not a sign read"
    assert (b["last_plan"] or {}).get("short_proof") is None


# (au) a short's flatten cancels its resting BUY_SHORT add BEFORE the whole-slug close
def test_a_confirmed_vanish_on_a_short_cancels_the_resting_add_then_closes(monkeypatch):
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
             _fill(N, "SELL", 400, 0.70, NOW - 2000), _fill(M, "SELL", 100, 0.31, NOW - 1000)]
    p = _short_world(fills=fills, snap=None)
    b = _short_book(p, ledger=-300)
    o = p.add_order(b, side=SELL, wire=0.32, qty=100, kind="increase")
    v = _Venue(held={SLUG: -300})
    v.rest("oid-1", "SELL", 0.32, 100)
    http = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0},
                       {"conditionId": CID, "asset": N, "size": 0}])
    st = _tick(p, v, http=http)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    assert p.orders[o["id"]]["state"] == "cancelled"
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v.calls, "the close follows the cancel in one tick"
    assert b["ledger_net"] == 0 and _census(st, "short_flatten_close") == 1
    assert _census(st, "open_order_pending") == 0


def test_the_short_share_cap_lowered_to_one_bites_by_name(monkeypatch):
    """S3 expressibility: rules.MIRROR_SHORT_MAX_SHARES caps what a
    SHORT book may target -- UNBOUNDED by default since 2026-09-06
    (U12c; ONE before), lowered only from the environment, and the
    1-share probe of rung S3/S4 runs at 1. A capped target is the whole
    probe: the 1-share book opens and rests one share; 0 refuses every
    short by the cap's name; a long book never reads it; and at the
    unbounded default the same short is not clamped at all."""
    _shorts_on(monkeypatch, max_shares=1)
    assert rules.MIRROR_SHORT_MAX_SHARES == 1
    # THE PROBE RUNS UNDER THE MINIMUM NOTIONAL: one share of a 0.32
    # contract is $0.68 of collateral, under MIRROR_MIN_ORDER_USD ($1,
    # U12c review FIX-2), so the 1-share probe of rung S3/S4 runs with
    # MIRROR_MIN_ORDER_USD=0 as well -- pinned here the way the rung
    # will run it, and said in the docs
    monkeypatch.setattr(rules, "MIRROR_MIN_ORDER_USD", 0.0)
    # the default first: math.inf through _bounded is no clamp, no name
    monkeypatch.setattr(rules, "MIRROR_SHORT_MAX_SHARES", math.inf)
    p0 = _short_world()
    v0 = _Venue()
    st0 = _tick(p0, v0, http=_short_http())
    b0 = next(iter(p0.books.values()))
    assert b0["target"] == -300 and _census(st0, "short_share_cap") == 0
    assert _places(v0)[0][3] == 300 and "short_share_cap" not in (b0["last_plan"] or {})
    monkeypatch.setattr(rules, "MIRROR_SHORT_MAX_SHARES", 1)
    p = _short_world()
    v = _Venue()
    st = _tick(p, v, http=_short_http())
    b = next(iter(p.books.values()))
    assert b["intent"] == SHORT and b["target"] == -1 and b["target_raw"] == pytest.approx(-300.0)
    assert _census(st, "short_share_cap") >= 1 and st["integ"]["short_share_cap"] >= 1
    assert b["last_plan"]["short_share_cap"] == 1 and _census(st, "dead_band") == 0
    pl = _places(v)
    assert len(pl) == 1 and pl[0][1:] == (SLUG, 0.32, 1, False, "TIME_IN_FORCE_GOOD_TILL_CANCEL",
                                         SHORT, True, None)
    assert _census(st, "short_open") == 1
    # the shadow is compared against the UNCAPPED figure: no disagreement by construction
    p.shadow.append(_shadow_row(-300, 1.0, -300.0, at_ts=NOW))
    v2 = _Venue(held={SLUG: -1}, fills={"oid-1": (1.0, 0.32)})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=_short_http())
    assert b["ledger_net"] == -1 and _census(st2, "shadow_live_disagree") == 0
    st3 = _tick(p, _Venue(held={SLUG: -1}), now=NOW + 60, http=_short_http())
    assert _census(st3, "on_target") == 1 and _census(st3, "short_share_cap") == 1
    # an open short book above the cap is clamped toward zero: the
    # partial cover it would take is short_reduce_unproven, nothing sent
    p4 = _short_world()
    b4 = _short_book(p4, ledger=-300)
    v4 = _Venue(held={SLUG: -300})
    st4 = _tick(p4, v4, http=_short_http())
    assert b4["target"] == -1 and _census(st4, "short_share_cap") == 1
    assert _census(st4, "short_reduce_unproven") == 1 and not _places(v4) and "close" not in _kinds(v4)
    # a cap of 0 refuses every short by name: no book, nothing placed
    monkeypatch.setattr(rules, "MIRROR_SHORT_MAX_SHARES", 0)
    p5 = _short_world()
    v5 = _Venue()
    st5 = _tick(p5, v5, http=_short_http())
    assert _census(st5, "short_share_cap") == 1 and not p5.books and not _places(v5)
    assert _census(st5, "short_side_refused") == 0
    # a long book never reads the cap
    p6 = _pool()
    v6 = _Venue()
    st6 = _tick(p6, v6)
    assert _places(v6)[0][3] == 300 and _census(st6, "short_share_cap") == 0
    # the cap is read through the rules module at call time, never restated
    src = inspect.getsource(ml)
    assert "rules.MIRROR_SHORT_MAX_SHARES" in src and "MIRROR_SHORT_MAX_SHARES =" not in src


def test_his_level_on_a_short_reads_the_other_tokens_sell_only_on_a_short_book():
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2000),
             _fill(N, "SELL", 100, 0.70, NOW - 1000)]
    # a short INCREASE (his net moving down) reads what a long reduce reads: his other-token BUY
    assert ml._his_level(fills, M, N, reducing=True, short=True) == pytest.approx(1 - 0.72)
    assert ml._his_level(fills, M, N, reducing=True) == pytest.approx(1 - 0.72)
    # a short REDUCE (his net moving up): his other-token SELL at 1 - p, the latest move up
    assert ml._his_level(fills, M, N, reducing=False, short=True) == pytest.approx(1 - 0.70)
    # a long book's increase never reads that clause: byte-identical
    assert ml._his_level(fills, M, N, reducing=False) == pytest.approx(0.31)
    # the shadow's figure agrees to four places on the short entry
    assert round(ml._his_level(fills, M, N, reducing=True, short=True), 4) == ms.his_level(fills, M, N, True)


def test_the_short_wire_is_the_executors_and_is_pinned_against_sell_price_at_the_19_cents():
    disagree = [0.18, 0.41, 0.42, 0.43, 0.57, 0.58, 0.59, 0.69, 0.7, 0.71, 0.82, 0.83, 0.84, 0.85,
                0.94, 0.95, 0.96, 0.97, 0.98]
    off = []
    for c in range(1, 99):
        q = round(c / 100.0, 2)              # his other-token price, an exact cent
        his = 1.0 - q                        # his level in long space, as _his_level hands it over
        wire = ml._short_wire(his, 0.01)
        assert wire == le.rest_tick(le.wire_limit(q, SHORT), SHORT), q
        assert wire == round(his, 2) and 0.01 <= wire <= 0.99, q
        if wire != rules.sell_price(his, 0.01):
            off.append(q)
    assert off == disagree
    # the ask is honoured: a rest never sits under the long book's ask
    assert ml._short_wire(0.28, 0.32) == 0.32 and ml._short_wire(0.60, 0.32) == 0.60
    assert ml._short_wire(None, 0.32) == 0.32
    # the collateral the lane reads off that wire
    assert le.cost_per_share(0.32, SHORT) == pytest.approx(0.68)
    # no ask, an ask off the ladder, or a disarmed model is no price
    assert ml._short_wire(0.28, None) is None and ml._short_wire(0.28, 1.0) is None
    assert ml._short_wire(0.995, 0.999) is None


def test_the_short_wire_is_none_when_the_model_is_disarmed(monkeypatch):
    monkeypatch.setattr(le, "short_model_confirmed", lambda: False)
    assert ml._short_wire(0.28, 0.32) is None


def test_the_g4_census_keys_are_declared_and_served():
    for k in ("short_open", "short_add", "short_flatten_close", "short_reduce_unproven", "sign_flip",
              "short_model_disarmed", "short_gate_refused", "short_column_absent"):
        assert k in ml.CENSUS_KEYS, k
    for k in ("short_reduce_unproven", "sign_flip", "short_model_disarmed", "short_gate_refused",
              "short_column_absent"):
        assert k in ml._INTEG_CENSUS_KEYS, k
    # the counters live in the ONE nested `short` block (the top level is
    # capped at 40 keys by the health endpoint's sanitizer; the P2 fold
    # onto U9's `venue_state` filled the base block to the cap) and are
    # served flat on `integ` under the names the gate lines read
    for k in ("short_fills", "short_fills_at_or_better", "short_fills_uncheckable"):
        assert k in ml._INTEG_STAT_KEYS and k not in ml._new_stats(), k
        assert ml._INTEG_SHORT_STATS[k] in ml._new_stats()["short"] and ml._new_stats()["integ"][k] == 0
    assert ml._new_stats()["short"]["on"] is False
    assert ml._integ_block({"short": {"fills": 3, "at_or_better": 2, "uncheckable": 1}})["short_fills"] == 3
    assert ml._integ_block({"short": {"fills": 3, "at_or_better": 2, "uncheckable": 1}})["short_fills_at_or_better"] == 2
    assert ml._integ_block({})["short_fills_uncheckable"] == 0
    assert len(ml._INTEG_CENSUS_KEYS) + len(ml._INTEG_STAT_KEYS) < 40


def test_the_knob_off_on_an_open_short_book_is_the_reversal_path(monkeypatch):
    """Rung plan, reversal at any rung: knob off restores short_side_refused
    alone; an open short book flattens by close_position when sole."""
    monkeypatch.setattr(rules, "MIRROR_SHORTS", False)
    p = _short_world()
    b = _short_book(p, ledger=-300)
    v = _Venue(held={SLUG: -300})
    st = _tick(p, v, http=_short_http())
    assert _census(st, "short_side_refused") == 1 and b["target"] == 0
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v.calls and "place" not in _kinds(v)
    assert b["ledger_net"] == 0 and _census(st, "short_flatten_close") == 1
    # co-held (a stranger's fraction beside ours): held and paged, never a SELL_SHORT
    p2 = _short_world()
    b2 = _short_book(p2, ledger=-300)
    v2 = _Venue(held={SLUG: -300.5})
    st2 = _tick(p2, v2, http=_short_http())
    assert _census(st2, "short_reduce_unproven") == 1 and b2["ledger_net"] == -300 and not _places(v2)
    assert "close" not in _kinds(v2)


def test_a_sign_flip_on_a_short_book_flattens_by_close_position_under_its_name(monkeypatch):
    """B8, owner default Q5 (a): his net crossed to the LONG side while
    our short book is open. The book flattens under the name sign_flip
    -- the one proven short exit when sole -- and the long side opens
    as a NEW episode only after the flat close (test_mirror_short_sign_flip
    drives both directions through the close)."""
    _shorts_on(monkeypatch)
    p = _pool()                                   # his net +300: the default fixture
    b = _short_book(p, ledger=-300)
    v = _Venue(held={SLUG: -300})
    st = _tick(p, v)
    assert _census(st, "sign_flip") == 1 and b["target"] == 0 and b["last_plan"]["sign_flip"] is True
    assert _census(st, "short_side_refused") == 0
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v.calls and "place" not in _kinds(v)
    assert b["ledger_net"] == 0 and _census(st, "short_flatten_close") == 1
    assert b["state"] == "live" and len(p.books) == 1
    # the shadow is compared against the UNCLAMPED signed target: no disagreement by construction
    assert _census(st, "shadow_live_disagree") == 0


def test_an_add_to_an_open_short_book_is_named_short_add_and_sized_by_collateral(monkeypatch):
    _shorts_on(monkeypatch)
    # his net -600 against our -300: add 300 more
    p = _short_world(fills=_his(100, other_size=700, other_px=0.72), snap={M: 100.0, N: 700.0})
    b = _short_book(p, ledger=-300)
    v = _Venue(held={SLUG: -300})
    st = _tick(p, v, http=_mkt(100.0, 700.0))
    # -600 raw: $414 of collateral on the SHORT leg's price 1 - 0.31,
    # under the $1,000 per-side cap (U12; at the $250 it was, this
    # capped at -362), so the target is his -600 whole
    assert b["target"] == -600 and b["target_raw"] == pytest.approx(-600.0, abs=1e-3)
    assert -int(rules.MIRROR_NET_CAP_USD / 0.69) < -600, "the cap sits past the target"
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is False and pl[0][6] == SHORT and pl[0][3] == 300
    assert _census(st, "short_add") == 1 and _census(st, "short_open") == 0
    o = next(iter(p.orders.values()))
    assert (o["side"], o["intent"], o["kind"]) == (SELL, SHORT, "increase")
    # the room is read in collateral: $25 of mirror day room at a 0.32
    # contract wire buys 36 shares of a 0.68 leg, not 78
    p2 = _short_world(fills=_his(100, other_size=700, other_px=0.72), snap={M: 100.0, N: 700.0})
    b2 = _short_book(p2, ledger=-300)
    p2.add_order(b2, side=SELL, wire=0.32, qty=1000, kind="increase", state="filled",
                 booked=1000.0, cash_usd=1225.0, placed_ts=NOW - 100, done_at=NOW - 90)
    v2 = _Venue(held={SLUG: -300})
    st2 = _tick(p2, v2, http=_mkt(100.0, 700.0))
    pl2 = _places(v2)
    assert len(pl2) == 1 and pl2[0][3] == int(25.0 / 0.68) == 36, pl2
    assert st2["mirror_day_room"] == pytest.approx(25.0)


# ------------------------------ 18. the rails of 2026-09-06 (U12b, U12c)
#
# Owner orders ~14:00Z ("Let's remove those caps so we start copying his
# actual book. Just trade 10% of what he puts on everything he takes
# (with a hard cap of no single event having more than $2.5k on it) this
# limitation should never force us to decline any of the possible
# copies.") and ~14:10Z ("Bets under $10, take the full position (exact
# copy)"; "I want shorts live as well"). The fixture rails above pin the
# old world; each test here sets the new rule it exercises.

def _rails_2026_09_06(monkeypatch):
    """The code defaults, restored over the fixture world's rails."""
    monkeypatch.setattr(rules, "MIRROR_RATIO", 0.10)
    monkeypatch.setattr(rules, "MIRROR_SMALL_BET_USD", 10.0)
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", math.inf)
    monkeypatch.setattr(rules, "MIRROR_CLIP_USD", 2500.0)


def test_no_day_cap_a_fifty_thousand_dollar_day_still_opens_and_a_lowered_cap_bites(monkeypatch, caplog):
    """U12b item 4: MIRROR_DAY_USD unbounded by default. $50,000 of
    filled BUYs in the window and the candidate still opens; the room
    is published as null (JSON), never 1e12, and the mode line prints
    `day=none`; a cap lowered from the environment bites by name; an
    unreadable spend read bites by name under the unbounded cap too."""
    import logging

    from sportsassets.api import app as api_app
    _rails_2026_09_06(monkeypatch)
    monkeypatch.setattr(rules, "MIRROR_RATIO", 1.0)      # the fixture's 300-share book
    p = _pool()
    other = p.add_book(ledger=0, us_market_slug="aec-atp-other-2026-09-02", condition_id="0xother",
                       long_asset="tokO", other_asset="tokP")
    p.add_order(other, state="filled", cash_usd=50000.0, order_id="old", placed_ts=NOW - 100,
                us_market_slug="aec-atp-other-2026-09-02")
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "mirror_day_cap") == 0 and st["mirror_day_room"] is None
    assert [c[1] for c in _places(v)] == [SLUG] and len(p.books) == 2, "$50,000 spent, still opens"
    assert api_app._sanitize_detail(st)["mirror_day_room"] is None
    assert "1e12" not in json.dumps(api_app._sanitize_detail(st))
    # the mode line: day=none under no cap; day=None on a tick that
    # never read the room under a finite cap, as before
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(st, ml.MODE_LINE_EVERY_TICKS)
    lines = [rec.getMessage() for rec in caplog.records if rec.getMessage().startswith("mirror_live mode=")]
    assert lines and " day=none " in lines[0], lines
    caplog.clear()
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1250.0)
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(st, mirror_day_room=None), ml.MODE_LINE_EVERY_TICKS)
    lines2 = [rec.getMessage() for rec in caplog.records if rec.getMessage().startswith("mirror_live mode=")]
    assert lines2 and " day=None " in lines2[0], lines2
    # a lowered cap bites on what filled
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 100.0)
    p2 = _pool()
    o2 = p2.add_book(ledger=0, us_market_slug="aec-atp-other-2026-09-02", condition_id="0xother",
                     long_asset="tokO", other_asset="tokP")
    p2.add_order(o2, state="filled", cash_usd=2000.0, order_id="old", placed_ts=NOW - 100,
                 us_market_slug="aec-atp-other-2026-09-02")
    v2 = _Venue()
    st2 = _tick(p2, v2)
    assert _census(st2, "mirror_day_cap") >= 1 and not _places(v2) and len(p2.books) == 1
    assert st2["mirror_day_room"] == pytest.approx(100.0 - 2000.0)
    # an unreadable spend read refuses by name, cap or no cap
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", math.inf)
    p3 = _pool()
    p3.raise_on.append(("ml-mirror-day", RuntimeError("db")))
    v3 = _Venue()
    st3 = _tick(p3, v3)
    assert _census(st3, "mirror_day_cap") >= 1 and not _places(v3) and not p3.books
    assert st3["mirror_day_room"] is None
    # the worker reads the cap through the rules module at call time
    src = inspect.getsource(ml)
    assert "MIRROR_DAY_USD =" not in src and "math.isfinite(day_cap)" in src


def test_the_copy_sleeves_daily_room_does_not_bind_the_mirror_but_its_total_room_does(monkeypatch):
    """U12b item 4, the day cap by another road: le._copy_day_room's
    day figure is the copy lane's live_max_daily_usd. The mirror reads
    the tuple and sets the day aside; the TOTAL room still binds, and
    the rest lane's reservations now come off the total."""
    async def _no_day(pool, cfg):
        return 0.0, 1e12
    monkeypatch.setattr(le, "_copy_day_room", _no_day)
    p = _pool()
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "no_budget_room") == 0 and _places(v) and p.books, "no sleeve day room: opens"

    async def _no_total(pool, cfg):
        return 1e12, 0.5
    monkeypatch.setattr(le, "_copy_day_room", _no_total)
    p2 = _pool()
    v2 = _Venue()
    st2 = _tick(p2, v2)
    assert _census(st2, "no_budget_room") >= 1 and not _places(v2) and not p2.books
    # the reservations come off the total room
    async def _plenty(pool, cfg):
        return 1e12, 1000.0
    monkeypatch.setattr(le, "_copy_day_room", _plenty)
    monkeypatch.setattr(le, "_REST_RESERVED_USD", 999.5)
    p3 = _pool()
    v3 = _Venue()
    st3 = _tick(p3, v3)
    assert _census(st3, "no_budget_room") >= 1 and not _places(v3)
    monkeypatch.setattr(le, "_REST_RESERVED_USD", 910.0)
    p4 = _pool()
    v4 = _Venue()
    _tick(p4, v4)
    assert _places(v4)[0][3] == 300, "$90 of total room at 0.30 is the whole 300"
    src = inspect.getsource(ml._global_guards)
    assert "t.day_room = 1e12" in src and "_day_unused" in src and "total -= float(le._REST_RESERVED_USD" in src


def test_a_four_thousand_share_target_rests_as_one_order_under_the_mirror_clip(monkeypatch):
    """U12b item 7: the mirror lane's own per-order clip ($2,500), not
    the copy lane's $250. 10% of his 40,000 sh @ 0.60 is 4,000 shares,
    $2,400 -- under the event cap -- and rests as ONE order of 4,000
    (416 under the copy lane's clip at the 0.59 wire)."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=_his(40000, long_px=0.60), snap={M: 40000.0, N: 0.0})
    v = _Venue(bid=0.59, ask=0.61)
    st = _tick(p, v, http=_mkt(40000.0, 0.0))
    b = next(iter(p.books.values()))
    assert b["target"] == 4000 and b["ratio"] == 0.10 and _census(st, "over_room") == 0
    pl = _places(v)
    assert len(pl) == 1 and pl[0][3] == 4000 and pl[0][2] == 0.59
    assert int(le.LIVE_MAX_CLIP_USD / 0.59) == 423, "what the copy lane's clip would have rested"
    # the copy lane's clip is not read for size anywhere in the worker
    src = inspect.getsource(ml._room_qty)
    assert "rules.MIRROR_CLIP_USD" in src and "LIVE_MAX_CLIP_USD" not in src
    assert "rules.MIRROR_CLIP_USD" in inspect.getsource(ml._tick_candidate)
    # a lowered mirror clip scales the rest and names over_room by the room rule
    monkeypatch.setattr(rules, "MIRROR_CLIP_USD", 250.0)
    p2 = _pool(fills=_his(40000, long_px=0.60), snap={M: 40000.0, N: 0.0})
    v2 = _Venue(bid=0.59, ask=0.61)
    _tick(p2, v2, http=_mkt(40000.0, 0.0))
    assert _places(v2)[0][3] == 423


def test_small_bets_copy_whole_and_the_ratio_is_stored_at_open_for_the_books_life(monkeypatch):
    """U12c item 10: his $8 bet (16 sh @ 0.50) opens at ratio 1.0 and
    targets 16; his $12 bet (24 sh @ 0.50) opens at 0.10 and targets 2;
    a book opened at 1.0 whose position grew to $30 stays at 1.0 and
    targets his whole net; env MIRROR_SMALL_BET_USD=5 makes the $8 bet
    a 10% book of one share. The copy lane's per-whale clip stays the
    admission gate (a demoted whale opens nothing) and sizes nothing."""
    _rails_2026_09_06(monkeypatch)
    v = _Venue(bid=0.49, ask=0.51)
    p = _pool(fills=_his(16, long_px=0.50), snap={M: 16.0, N: 0.0})
    st = _tick(p, v, http=_mkt(16.0, 0.0))
    b = next(iter(p.books.values()))
    assert (b["target"], b["ratio"]) == (16, 1.0) and _places(v)[0][3] == 16
    assert _census(st, "dead_band") == 0 and _census(st, "under_one_share") == 0
    # his $15 bet (30 sh @ 0.50): 10%, target 3 ($1.47 at the wire, sent)
    p2 = _pool(fills=_his(30, long_px=0.50), snap={M: 30.0, N: 0.0})
    v2 = _Venue(bid=0.49, ask=0.51)
    _tick(p2, v2, http=_mkt(30.0, 0.0))
    b2 = next(iter(p2.books.values()))
    assert (b2["target"], b2["ratio"]) == (3, 0.10) and _places(v2)[0][3] == 3
    # his $12 bet (24 sh @ 0.50): 10%, target 2 -- $0.98 at the wire,
    # under the $1 minimum notional (FIX-2): the book opens and is held
    p2b = _pool(fills=_his(24, long_px=0.50), snap={M: 24.0, N: 0.0})
    v2b = _Venue(bid=0.49, ask=0.51)
    st2b = _tick(p2b, v2b, http=_mkt(24.0, 0.0))
    b2b = next(iter(p2b.books.values()))
    assert (b2b["target"], b2b["ratio"]) == (2, 0.10) and not _places(v2b)
    assert _census(st2b, "under_min_notional") == 1
    # an open book sizes on its STORED ratio: opened at 1.0 on $8, his
    # position now 30 sh ($15, past the $10 line but under the $20 step
    # line of the U12c review) -> target 30, an increase of 14, never 3;
    # past $20 the one-way step applies (its own test below)
    p3 = _pool(fills=_his(30, long_px=0.50), snap={M: 30.0, N: 0.0})
    b3 = p3.add_book(ledger=16, ratio=1.0, avg_cost=0.50)
    v3 = _Venue(bid=0.49, ask=0.51, held={SLUG: 16})
    _tick(p3, v3, http=_mkt(30.0, 0.0))
    assert b3["target"] == 30 and b3["ratio"] == 1.0 and _places(v3)[0][3] == 14
    # and the other way: opened at 0.10, his position shrunk under $10
    # -- still 0.10 (a position that crosses $10 never flips)
    p4 = _pool(fills=_his(16, long_px=0.50), snap={M: 16.0, N: 0.0})
    b4 = p4.add_book(ledger=2, ratio=0.10, avg_cost=0.50)
    v4 = _Venue(bid=0.49, ask=0.51, held={SLUG: 2})
    st4 = _tick(p4, v4, http=_mkt(16.0, 0.0))
    assert b4["target"] == 1 and b4["ratio"] == 0.10 and _census(st4, "on_target") == 0
    # the line lowered from the environment: the $8 bet is a 10% book of
    # one share -- $0.49 at the wire, held under the minimum notional
    monkeypatch.setattr(rules, "MIRROR_SMALL_BET_USD", 5.0)
    p5 = _pool(fills=_his(16, long_px=0.50), snap={M: 16.0, N: 0.0})
    v5 = _Venue(bid=0.49, ask=0.51)
    st5 = _tick(p5, v5, http=_mkt(16.0, 0.0))
    b5 = next(iter(p5.books.values()))
    assert (b5["target"], b5["ratio"]) == (1, 0.10) and not _places(v5)
    assert _census(st5, "under_min_notional") == 1
    # a demoted whale (the copy lane's clip at $0) still opens no book
    monkeypatch.setattr(rules, "MIRROR_SMALL_BET_USD", 10.0)
    monkeypatch.setattr(le, "per_fill_usd", lambda *a, **k: 0.0)
    p6 = _pool(fills=_his(16, long_px=0.50), snap={M: 16.0, N: 0.0})
    v6 = _Venue(bid=0.49, ask=0.51)
    st6 = _tick(p6, v6, http=_mkt(16.0, 0.0))
    assert _census(st6, "clip_zero") >= 1 and not p6.books and not _places(v6)
    src = inspect.getsource(ml._tick_candidate)
    assert "rules.open_ratio(net, r.mark)" in src and "le.per_fill_usd(w, slug)" in src
    assert "rules.open_ratio" not in inspect.getsource(ml._tick_book), "never re-decided on an open book"


def test_shorts_are_on_by_default_and_a_short_sizes_by_the_same_rule(monkeypatch):
    """U12c item 11: the knob's code default is True (pinned in the
    rules tests through env_switch); with it on and the share cap
    unbounded, his -3,000 sh at mark 0.31 opens a short book at 10%:
    target -300, collateral 0.69 x 300 = $207, one BUY_SHORT rest at
    the contract-price rule already built. His -12 sh ($8.28 of
    collateral) is an exact copy, -12. The pre-S4 exit safety is
    untouched: a partial reduce is `short_reduce_unproven` and nothing
    is sent; the flatten when he leaves is close_position."""
    _rails_2026_09_06(monkeypatch)
    monkeypatch.setattr(rules, "MIRROR_SHORTS", True)
    monkeypatch.setattr(rules, "MIRROR_SHORT_MAX_SHARES", math.inf)
    while le._SHORT_LOCK.locked():
        le._SHORT_LOCK.release()
    assert le.short_model_confirmed() is True, "by construction (live_executor)"
    p = _pool(fills=_his(100, other_size=3100, other_px=0.72), snap={M: 100.0, N: 3100.0})
    v = _Venue()
    st = _tick(p, v, http=_mkt(100.0, 3100.0))
    b = next(iter(p.books.values()))
    assert b["intent"] == SHORT and (b["target"], b["ratio"]) == (-300, 0.10)
    assert _census(st, "short_share_cap") == 0 and _census(st, "short_side_refused") == 0
    pl = _places(v)
    assert len(pl) == 1 and pl[0][3] == 300 and pl[0][6] == SHORT and pl[0][2] == 0.32
    assert _census(st, "short_open") == 1
    # the small short copies whole
    p2 = _pool(fills=_his(100, other_size=112, other_px=0.72), snap={M: 100.0, N: 112.0})
    v2 = _Venue()
    _tick(p2, v2, http=_mkt(100.0, 112.0))
    b2 = next(iter(p2.books.values()))
    assert (b2["target"], b2["ratio"]) == (-12, 1.0) and _places(v2)[0][3] == 12
    # the exit safety: a partial reduce on the open short is held by name
    p3 = _pool(fills=_his(100, other_size=1600, other_px=0.72), snap={M: 100.0, N: 1600.0})
    b3 = _short_book(p3, ledger=-300, ratio=0.10)
    v3 = _Venue(held={SLUG: -300})
    st3 = _tick(p3, v3, http=_mkt(100.0, 1600.0))
    assert b3["target"] == -150 and _census(st3, "short_reduce_unproven") == 1
    assert not _places(v3) and "close" not in _kinds(v3)
    # MIRROR_SHORTS=off is the P1 door, as before
    monkeypatch.setattr(rules, "MIRROR_SHORTS", False)
    p4 = _pool(fills=_his(100, other_size=3100, other_px=0.72), snap={M: 100.0, N: 3100.0})
    v4 = _Venue()
    st4 = _tick(p4, v4, http=_mkt(100.0, 3100.0))
    assert _census(st4, "short_side_refused") == 1 and not p4.books and not _places(v4)
    # the worker reads the cap through _bounded: unreadable is shut
    monkeypatch.setattr(rules, "MIRROR_SHORTS", True)
    monkeypatch.setattr(rules, "MIRROR_SHORT_MAX_SHARES", None)
    p5 = _pool(fills=_his(100, other_size=3100, other_px=0.72), snap={M: 100.0, N: 3100.0})
    v5 = _Venue()
    st5 = _tick(p5, v5, http=_mkt(100.0, 3100.0))
    assert _census(st5, "short_share_cap") >= 1 and not p5.books and not _places(v5)
    assert "rules._bounded(rules.MIRROR_SHORT_MAX_SHARES)" in inspect.getsource(ml._short_capped)


def test_an_exact_copy_book_steps_down_once_when_his_position_passes_twice_the_line(monkeypatch):
    """Review of U12c, FIX-1. A book opened at ratio 1.0 on his $8 (16
    sh @ 0.50) follows him at 100% -- so when his position grows to 60
    sh ($30, past the $20 step line) the stored ratio steps to 0.10 for
    life, is named `ratio_stepped`, the target becomes 6 and the reduce
    path sells the 10 excess. A 1.0 book at $15 does not step; a 0.10
    book never steps; the step persists (read back from the row)."""
    _rails_2026_09_06(monkeypatch)
    v = _Venue(bid=0.49, ask=0.51, held={SLUG: 16})
    p = _pool(fills=_his(60, long_px=0.50), snap={M: 60.0, N: 0.0})
    b = p.add_book(ledger=16, ratio=1.0, avg_cost=0.50)
    st = _tick(p, v, http=_mkt(60.0, 0.0))
    assert _census(st, "ratio_stepped") == 1 and st["integ"]["ratio_stepped"] == 1
    assert b["ratio"] == 0.10 and b["target"] == 6 and b["last_plan"]["ratio_stepped"] == 0.10
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][3] == 10, "SELL_LONG reduce of the excess"
    assert any(("ml-book-ratio" in s and a == (b["id"], 0.10)) for k, s, a in p.sent)
    # persists: the next tick reads 0.10 off the row and steps nothing
    v2 = _Venue(bid=0.49, ask=0.51, held={SLUG: 16})
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(60.0, 0.0))
    assert _census(st2, "ratio_stepped") == 0 and b["ratio"] == 0.10 and b["target"] == 6
    # a 1.0 book whose position stays at $15 does not step: target 30, an add of 14
    p3 = _pool(fills=_his(30, long_px=0.50), snap={M: 30.0, N: 0.0})
    b3 = p3.add_book(ledger=16, ratio=1.0, avg_cost=0.50)
    v3 = _Venue(bid=0.49, ask=0.51, held={SLUG: 16})
    st3 = _tick(p3, v3, http=_mkt(30.0, 0.0))
    assert _census(st3, "ratio_stepped") == 0 and b3["ratio"] == 1.0 and b3["target"] == 30
    assert _places(v3)[0][3] == 14 and _places(v3)[0][4] is False
    # exactly on the step line is not past it
    p4 = _pool(fills=_his(40, long_px=0.50), snap={M: 40.0, N: 0.0})
    b4 = p4.add_book(ledger=16, ratio=1.0, avg_cost=0.50)
    _tick(p4, _Venue(bid=0.49, ask=0.51, held={SLUG: 16}), http=_mkt(40.0, 0.0))
    assert b4["ratio"] == 1.0 and b4["target"] == 40
    # a 0.10 book never steps, whatever his position does
    p5 = _pool(fills=_his(600, long_px=0.50), snap={M: 600.0, N: 0.0})
    b5 = p5.add_book(ledger=60, ratio=0.10, avg_cost=0.50)
    st5 = _tick(p5, _Venue(bid=0.49, ask=0.51, held={SLUG: 60}), http=_mkt(600.0, 0.0))
    assert _census(st5, "ratio_stepped") == 0 and b5["ratio"] == 0.10 and b5["target"] == 60
    # nothing ever steps UP: a 0.10 book whose position shrinks under $10 stays 0.10
    p6 = _pool(fills=_his(16, long_px=0.50), snap={M: 16.0, N: 0.0})
    b6 = p6.add_book(ledger=2, ratio=0.10, avg_cost=0.50)
    _tick(p6, _Venue(bid=0.49, ask=0.51, held={SLUG: 2}), http=_mkt(16.0, 0.0))
    assert b6["ratio"] == 0.10 and b6["target"] == 1
    # a failed write steps nothing in memory: the book is its own error, the ratio stands
    p7 = _pool(fills=_his(60, long_px=0.50), snap={M: 60.0, N: 0.0})
    b7 = p7.add_book(ledger=16, ratio=1.0, avg_cost=0.50)
    p7.raise_on.append(("ml-book-ratio", RuntimeError("db")))
    st7 = _tick(p7, _Venue(bid=0.49, ask=0.51, held={SLUG: 16}), http=_mkt(60.0, 0.0))
    assert _census(st7, "book_error") == 1 and _census(st7, "ratio_stepped") == 0 and b7["ratio"] == 1.0
    # the rule at its own level: one way, derived line, unreadable steps nothing
    assert rules.step_ratio(1.0, 60.0, 0.50) == 0.10 and rules.step_ratio(1.0, 40.0, 0.50) is None
    assert rules.step_ratio(1.0, 40.01, 0.50) == 0.10 and rules.step_ratio(0.10, 6000.0, 0.50) is None
    assert rules.step_ratio(1.0, -30.0, 0.30) == 0.10, "a short's dollars are collateral: 30 x 0.70 = $21"
    assert rules.step_ratio(1.0, -28.0, 0.30) is None
    for bad in ((None, 60.0, 0.5), (1.0, None, 0.5), (1.0, 60.0, None), ("1.0", 60.0, 0.5), (1.0, 60.0, 1e-320)):
        assert rules.step_ratio(*bad) is None, bad
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rules, "MIRROR_SMALL_BET_USD", 5.0)
        assert rules.step_ratio(1.0, 21.0, 0.50) == 0.10 and rules.step_ratio(1.0, 20.0, 0.50) is None
    assert "MIRROR_SMALL_BET_STEP" not in inspect.getsource(rules), "derived, never a second knob"


def test_an_order_under_the_minimum_notional_is_not_sent_and_the_book_is_held(monkeypatch):
    """Review of U12c, FIX-2. His 1 sh @ 0.05 is an exact copy whose
    rest would be a $0.04 BUY at the 0.04 bid: not sent, named
    `under_min_notional`, the book held with no op spent. 2 sh @ 0.60
    ($1.18 at the 0.59 wire) is sent; 1 sh @ 0.60 ($0.59) is not; the
    environment may lower the line to 0 and then everything is sent."""
    _rails_2026_09_06(monkeypatch)
    assert rules.MIRROR_MIN_ORDER_USD == 1.0
    p = _pool(fills=_his(1, long_px=0.05), snap={M: 1.0, N: 0.0})
    v = _Venue(bid=0.04, ask=0.06)
    st = _tick(p, v, http=_mkt(1.0, 0.0))
    b = next(iter(p.books.values()))
    assert b["target"] == 1 and b["ratio"] == 1.0
    assert _census(st, "under_min_notional") == 1 and st["integ"]["under_min_notional"] == 1
    assert not _places(v) and not p.orders and st["ops"] == 0 and b["state"] == "live"
    p2 = _pool(fills=_his(2, long_px=0.60), snap={M: 2.0, N: 0.0})
    v2 = _Venue(bid=0.59, ask=0.61)
    st2 = _tick(p2, v2, http=_mkt(2.0, 0.0))
    assert _places(v2)[0][3] == 2 and _census(st2, "under_min_notional") == 0
    p3 = _pool(fills=_his(1, long_px=0.60), snap={M: 1.0, N: 0.0})
    v3 = _Venue(bid=0.59, ask=0.61)
    st3 = _tick(p3, v3, http=_mkt(1.0, 0.0))
    assert not _places(v3) and _census(st3, "under_min_notional") == 1
    monkeypatch.setattr(rules, "MIRROR_MIN_ORDER_USD", 0.0)
    p4 = _pool(fills=_his(1, long_px=0.05), snap={M: 1.0, N: 0.0})
    v4 = _Venue(bid=0.04, ask=0.06)
    st4 = _tick(p4, v4, http=_mkt(1.0, 0.0))
    assert _places(v4)[0][3] == 1 and _census(st4, "under_min_notional") == 0
    # a short reads its collateral: 1 sh of a 0.32 contract is $0.68, not sent; 2 are $1.36, sent
    monkeypatch.setattr(rules, "MIRROR_MIN_ORDER_USD", 1.0)
    monkeypatch.setattr(rules, "MIRROR_SHORTS", True)
    monkeypatch.setattr(rules, "MIRROR_SHORT_MAX_SHARES", math.inf)
    while le._SHORT_LOCK.locked():
        le._SHORT_LOCK.release()
    p5 = _pool(fills=_his(100, other_size=101, other_px=0.72), snap={M: 100.0, N: 101.0})
    v5 = _Venue()
    st5 = _tick(p5, v5, http=_mkt(100.0, 101.0))
    assert next(iter(p5.books.values()))["target"] == -1 and not _places(v5)
    assert _census(st5, "under_min_notional") == 1
    p6 = _pool(fills=_his(100, other_size=102, other_px=0.72), snap={M: 100.0, N: 102.0})
    v6 = _Venue()
    st6 = _tick(p6, v6, http=_mkt(100.0, 102.0))
    assert _places(v6)[0][3] == 2 and _census(st6, "under_min_notional") == 0
    src = inspect.getsource(ml._place)
    assert "rules.MIRROR_MIN_ORDER_USD" in src and src.index("under_min_notional") < src.index("_read_open(t)")


def test_a_sub_dollar_flatten_still_leaves_and_reaches_close_position(monkeypatch):
    """FIX-2b: a position that is leaving must leave at any size. A
    2-share book @ 0.30 whose owner is gone rests its flatten ($0.64 at
    the 0.32 ask, under the $1 minimum), and after MIRROR_FLATTEN_REST_S
    reaches close_position when sole and the IOC when co-held -- with
    the minimum notional the rest was refused before the row INSERT,
    _flatten_vanished never found the row its clock keys on, and the
    rest was re-attempted and re-refused every tick. The increase path
    still refuses under $1."""
    _rails_2026_09_06(monkeypatch)
    assert rules.MIRROR_MIN_ORDER_USD == 1.0

    async def _ours(slug):
        return 2, 0.30                       # the venue holds our 2 and nobody else's
    monkeypatch.setattr(le, "_pm_held", _ours)
    p = _pool(fills=_his(2, long_px=0.30, sold=2), snap=None)
    b = p.add_book(ledger=2, avg_cost=0.30)
    v = _Venue(held={SLUG: 2})
    st = _tick(p, v, http=_gone())
    assert _census(st, "flatten_vanished") == 1 and _census(st, "flatten_rested") == 1
    assert _census(st, "under_min_notional") == 0
    pl = _places(v)
    assert len(pl) == 1 and pl[0][3] == 2 and pl[0][4] is True and pl[0][2] == 0.32
    assert next(iter(p.orders.values()))["kind"] == "flatten_vanished"
    # sole holder after the wait: cancel, then close_position
    v2 = _Venue(held={SLUG: 2})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + rules.MIRROR_FLATTEN_REST_S + 1, http=_gone())
    assert ("cancel", "oid-1", SLUG) in v2.calls and ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v2.calls
    assert b["ledger_net"] == 0 and st2["flattened"] == 1
    # co-held: one IOC for our 2 shares at the slippage bound, $0.58, sent
    async def _held(slug):
        return 202, 0.30
    monkeypatch.setattr(le, "_pm_held", _held)
    p3 = _pool(fills=_his(2, long_px=0.30, sold=2), snap=None)
    p3.manual_shares[SLUG] = 200.0
    b3 = p3.add_book(ledger=2, avg_cost=0.30)
    p3.add_order(b3, side=SELL, wire=0.32, qty=2, kind="flatten_vanished",
                 placed_ts=NOW - rules.MIRROR_FLATTEN_REST_S - 1)
    v3 = _Venue(held={SLUG: 202}, flatten_bid=0.29, ioc_fill=2.0)
    v3.rest("oid-1", "SELL", 0.32, 2, created=NOW - 400)
    st3 = _tick(p3, v3, http=_gone())
    ioc = [c for c in _places(v3) if c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert len(ioc) == 1 and ioc[0][3] == 2 and ioc[0][4] is True and b3["ledger_net"] == 0
    assert _census(st3, "under_min_notional") == 0
    # the paired flatten (his net to zero, both legs held) rests too
    p4 = _pool(fills=_his(2, long_px=0.30, other_size=2, other_px=0.70), snap={M: 2.0, N: 2.0})
    b4 = p4.add_book(ledger=2, avg_cost=0.30)
    v4 = _Venue(held={SLUG: 2})
    st4 = _tick(p4, v4, http=_mkt(2.0, 2.0))
    assert b4["target"] == 0 and _places(v4) and _census(st4, "under_min_notional") == 0
    # the increase path still refuses under $1: his 2 sh @ 0.30 is a $0.58 BUY
    p5 = _pool(fills=_his(2, long_px=0.30), snap={M: 2.0, N: 0.0})
    v5 = _Venue()
    st5 = _tick(p5, v5, http=_mkt(2.0, 0.0))
    assert _census(st5, "under_min_notional") == 1 and not _places(v5)
    src = inspect.getsource(ml._place)
    assert 'kind in ("flatten_paired", "flatten_vanished")' in src and "not flattening and" in src


# ------------------------------------------------ 12. the census coverage

def test_every_census_key_was_emitted_at_least_once_across_this_file():
    """Runs last. One name is declared for the reader and structurally
    unreachable at the shipped constants, so it is excluded here by
    name: `under_one_share` is mi.plan's name for a delta under a
    share, which whole-share targets and ledgers never produce (delta 0
    is `on_target`). `dead_band` (the dollar band, $0 since 2026-09-06
    -- U12c, small bets copy whole) is driven under an explicit $5, and
    `hysteresis` is reachable at the $0 band (a 1-share move on a
    301-share target)."""
    if _RAN["n"] < 40:
        pytest.skip("the coverage read needs the whole file")
    unreachable = {"under_one_share"}
    missing = set(ml.CENSUS_KEYS) - SEEN - unreachable
    assert not missing, sorted(missing)
