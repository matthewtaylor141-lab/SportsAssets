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
from collections import Counter

import pytest

from sportsassets import copy_sports, edge_gate, pmus, ratelimit, venue_pace
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
# A PRICED short cover at its ceiling closes with ONE bip of slippage (E4
# review, MEDIUM-2): the close may take min(EXIT_SLIPPAGE_BIPS, max(1,
# floor((ceiling / ask - 1) x 10000))) past the ask read just before it;
# at the ceiling that is 1 -- a hundredth of a cent the ladder cannot
# express -- so the fill is at the ceiling cent. An unpriced cover keeps
# le.EXIT_SLIPPAGE_BIPS.
COVER_AT_CEILING_BIPS = 1


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


def _unpriced():
    """A vanish HE GAVE NO PRICE FOR (E4): no fill of his inside
    ms.his_fills' lookback (a book opened hours before he left; the
    data API then confirms him gone). The exit rule cannot price it
    (`exit_px_src: 'none'`), so the flatten keeps the C16 slippage leg
    -- rest at the ask, then close_position / the co-held IOC after
    MIRROR_FLATTEN_REST_S -- which the pins below were written for. A
    vanish WITH his SELL fill (his price known) takes within a cent of
    him on the tick the bid is there and never slips (section 21)."""
    return []


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
        # S4: the fixture's default is the read-back proof PASSED (the
        # steady state once the probe has run in production), so the
        # short tests exercise the cover; the probe's own tests and the
        # unproven refusals delete the key
        self.state = {"mirror_live": True, "side_echo_last": {"ok": 1},
                      "mirror_s4_proof": {"proved": True, "at": NOW - 100.0, "slug": SLUG, "fixture": True}}
        self.markets = {CID: {"closed": False, "resolved": False, "resolved_prices": None}}
        self.token_index = {M: 1, N: 0}
        self.token_cid = {M: CID, N: CID}
        self.whale_address = {"rn1": "0xabc"}
        self.kalshi = set()
        self.manual_shares = {}
        self.shadow = []
        self.reaper_touched = 0
        self.cand_refusals = []       # W2 / P2: mirror_candidate_refusals, as written
        # T2 (FILL lane 4): mirror_fill_answers (migration 060) as written,
        # keyed (whale, fill_id) with the FIRST row kept (the conflict
        # clause), the rows per INSERT statement, and the database before
        # 060 (the guard and the INSERT are UndefinedTableError)
        self.fill_answers = {}
        self.fill_writes = []
        self.no_fill_answers_table = False
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
        # THE DATABASE BEFORE MIGRATION 057 (E12): every statement that
        # names mirror_books.flow_base -- the guard, the book reads, the
        # flow INSERT, the ratchet's write -- is UndefinedColumnError,
        # the way Postgres answers it
        self.no_flow_column = False
        # THE DATABASE BETWEEN MIGRATIONS 057 AND 058 (E12b): every
        # statement that names mirror_books.flow_last_at -- the clock
        # guard, the 058-shaped book reads, the clock INSERT, the clock
        # UPDATE -- is UndefinedColumnError, the way Postgres answers it
        self.no_flow_clock_column = False
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
                # E12 (migration 057): NULL on every fixture book = the old rule;
                # E12b (058): the reference's clock, NULL = the landed rule for
                # a book with a block until its first reference write
                "flow_base": None, "flow_last_net": None, "flow_last_at": None,
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
        if self.no_flow_column and "flow_base" in s:
            raise _UndefinedColumn('column "flow_base" does not exist')
        if self.no_flow_clock_column and "flow_last_at" in s:
            raise _UndefinedColumn('column "flow_last_at" does not exist')
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
            rows = [dict(b) for b in sorted(self.books.values(), key=lambda b: (b["updated_ts"], b["id"]))
                    if b["state"] != "closed"]
            # the projection the statement asks for: the 056 shape names
            # no flow column, so the rows it hands back carry no such key
            if "flow_base" not in s:
                for b in rows:
                    b.pop("flow_base", None)
                    b.pop("flow_last_net", None)
            if "flow_last_at" not in s:                  # the 057 shape names no clock (E12b)
                for b in rows:
                    b.pop("flow_last_at", None)
            return rows
        if "ml-book-read" in s:
            b = self.books.get(a[0])
            if not b:
                return None
            row = dict(b)
            if "flow_base" not in s:
                row.pop("flow_base", None)
                row.pop("flow_last_net", None)
            if "flow_last_at" not in s:
                row.pop("flow_last_at", None)
            return row
        if "ml-book-flow" in s:
            # E12: the ratchet's write -- the block and the net it was read
            # at; E12b: the reference's clock with them on the 058 shape
            self.books[a[0]].update(flow_base=a[1], flow_last_net=a[2])
            if "flow_last_at" in s:
                self.books[a[0]]["flow_last_at"] = a[3]
            return "UPDATE 1"
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
            # A DOLLAR COUNTS ONCE (E3, the day reconciliation of
            # 2026-09-06): settled_pnl is the venue's WHOLE-position
            # figure and already holds the realized part, so a closed
            # book with one counts by it alone -- when its close is in
            # the window -- and every other book updated in the window
            # counts by realized_pnl. The window is the statement's
            # own, and the exclusion clause must be in its text: a
            # statement without it is the double count the
            # reconciliation found, and this fake refuses to model it
            # rather than quietly sum both, as its first cut did
            assert "AND NOT (state = 'closed' AND settled_pnl IS NOT NULL)" in s, \
                f"ml-loss-sum: a shape this fake does not model: {s}"
            # the settled branch's clock: closed_at, or updated_at where a
            # (hand-edited) row has none -- modelled only if the text says so
            assert "COALESCE(closed_at, updated_at) > $1::timestamptz" in s, \
                f"ml-loss-sum: a settled clock this fake does not model: {s}"
            # L1: the window's START is the statement's one parameter (the
            # worker's GREATEST(now - 24 h, the newest re-arm)), clocking
            # both arms and the count -- read from the call, never from
            # this fake's clock. A text with an interval of its own, or
            # with the parameter anywhere but those three places, is a
            # shape this fake refuses to model
            assert s.count("> $1::timestamptz") == 3 and s.count("$1") == 3 and "interval" not in s, \
                f"ml-loss-sum: one window, the parameter: {s}"
            assert len(a) == 1 and a[0].tzinfo is not None, f"ml-loss-sum: the window start: {a}"
            since = a[0].timestamp()

            def _settled(b):
                return b["state"] == "closed" and b["settled_pnl"] is not None

            def _closed_ts(b):
                return b["updated_ts"] if b["closed_at"] is None else b["closed_at"]
            lost = (sum(b["settled_pnl"] for b in self.books.values()
                        if _settled(b) and _closed_ts(b) > since)
                    + sum(b["realized_pnl"] for b in self.books.values()
                          if not _settled(b) and b["updated_ts"] > since))
            return {"lost": lost,
                    "books": sum(1 for b in self.books.values() if b["updated_ts"] > since)}
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
        if "ml-order-refused" in s:
            # the refused placement's row (U13): rejected, done, and the
            # adapter's raw kept as the receipt, parsed as the column would
            o = self.orders[a[0]]
            o.update(state="rejected", venue_state=a[1], reason=a[2],
                     receipt=json.loads(a[3]), done_at=NOW)
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
        if "ml-shadow-planned" in s:
            # W2 / P3: the newest shadow row per condition for the whale,
            # its plan columns as the rows carry them (absent: no plan)
            newest: dict = {}
            for r in self.shadow:
                if r["whale"] == a[0] and (r["condition_id"] not in newest
                                           or r["at_ts"] > newest[r["condition_id"]]["at_ts"]):
                    newest[r["condition_id"]] = r
            return [{"condition_id": c, "would_side": r.get("would_side"), "target": r.get("target")}
                    for c, r in newest.items()]
        if "ml-cand-refusals" in s:
            # W2 / P2: the refusal rows, parsed the way the statement's
            # jsonb_to_recordset would read them; kept in arrival order
            for r in json.loads(a[0]):
                self.cand_refusals.append(dict(r))
            return "INSERT 0 %d" % len(json.loads(a[0]))
        if "ml-fill-answers-guard" in s:
            # T2 (FILL lane 4): the 060 table probe, absent as Postgres answers it
            if self.no_fill_answers_table:
                raise _Undefined('relation "mirror_fill_answers" does not exist')
            return []
        if "ml-fill-answers" in s:
            # T2: the per-fill rows the way json_populate_recordset reads
            # them, ON CONFLICT (whale, fill_id) DO NOTHING -- the FIRST
            # row for a key stays; `fill_writes` counts the statements
            if self.no_fill_answers_table:
                raise _Undefined('relation "mirror_fill_answers" does not exist')
            n = 0
            for r in json.loads(a[0]):
                k = (r["whale"], r["fill_id"])
                if k not in self.fill_answers:
                    self.fill_answers[k] = dict(r)
                    n += 1
            self.fill_writes.append(len(json.loads(a[0])))
            return "INSERT 0 %d" % n
        if "ml-reaper-touched" in s:
            return self.reaper_touched
        if "ml-book-plan" in s:
            b = self.books[a[0]]
            b.update(target=a[1], target_raw=a[2], his_net=a[3], his_long=a[4], his_other=a[5],
                     snap_long=a[6], snap_other=a[7], drift=a[8], his_level=a[9], venue_net=a[10],
                     last_reason=a[11], last_plan=json.loads(a[12]), updated_ts=NOW)
            return "UPDATE 1"
        if "ml-book-skip" in s:
            # E6 review LOW-1: the quiet skip's own write -- the name and
            # the plan; every other column of the row stands
            b = self.books[a[0]]
            b.update(last_reason=a[1], last_plan=json.loads(a[2]), updated_ts=NOW)
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
        if "ml-book-flip" in s:
            # E15: the last CLOSED book's plan on the market (the flip's witness)
            closed = [b for b in self.books.values()
                      if b["whale"] == a[0] and b["condition_id"] == a[1] and b["state"] == "closed"]
            if not closed:
                return None
            b = max(closed, key=lambda b: b["id"])
            return {"last_plan": json.dumps(b["last_plan"]) if isinstance(b["last_plan"], dict) else b["last_plan"]}
        if "ml-book-turn" in s:
            # FILL lane 5: the same row with its id (the turn's record)
            closed = [b for b in self.books.values()
                      if b["whale"] == a[0] and b["condition_id"] == a[1] and b["state"] == "closed"]
            if not closed:
                return None
            b = max(closed, key=lambda b: b["id"])
            return {"id": b["id"],
                    "last_plan": json.dumps(b["last_plan"]) if isinstance(b["last_plan"], dict) else b["last_plan"]}
        if "ml-book-reopen-refused" in s:
            # FILL lane 5: the closed book's plan MERGED with the entry; the
            # statement names no updated_at, so updated_ts stands (pinned)
            b = self.books.get(a[0])
            if b is None or b["state"] != "closed":
                return "UPDATE 0"
            prior = b["last_plan"] if isinstance(b["last_plan"], dict) else json.loads(b["last_plan"] or "{}")
            b["last_plan"] = {**prior, **json.loads(a[1])}
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
                     updated_ts=NOW,
                     # E12: the flow-carrying INSERT's two parameters, else
                     # the columns' NULL (the 056-shaped statement)
                     flow_base=a[12] if len(a) > 12 else None,
                     flow_last_net=a[13] if len(a) > 13 else None,
                     flow_last_at=a[14] if len(a) > 14 else None)      # E12b: the clock INSERT's third
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
    pages = 1                      # the walk answers in one page

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
             filled=0.0, avg=None, intent=None):
        # `intent`: the order's intent as the venue's open-orders read
        # reports it (pmus._norm_order carries `intent`); the S4 probe
        # reads it back. A fixture rest placed by hand carries none
        # unless the test names it. `side` is the venue's contract
        # side in the desk's spelling; `venue_side` is the venue's own
        # field for it (pmus._norm_order carries the SDK's Order.side
        # verbatim: ORDER_SIDE_BUY / ORDER_SIDE_SELL), the field the S4
        # proof compares (S4 review, F1)
        self.orders[oid] = {"order_id": oid, "us_market_slug": slug, "side": side, "price": price,
                            "quantity": float(qty), "filled_shares": filled, "avg_px": avg,
                            "state": state, "created_at": NOW if created is None else created,
                            "tif": "GOOD_TILL_CANCEL", "intent": intent,
                            "venue_side": (f"ORDER_SIDE_{str(side).upper()}" if side else None)}
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
                   intent=None, post_only=False, good_till=None, paced_pair=False):
        self.calls.append(("place", slug, price, qty, sell, tif, intent, post_only, good_till))
        self.n += 1
        oid = f"oid-{self.n}"
        # the intent the adapter puts on the wire (pmus.submit_fok:
        # _exit_intent for a sell, the caller's BUY intent else)
        wire_intent = (("ORDER_INTENT_SELL_SHORT" if intent == "ORDER_INTENT_BUY_SHORT"
                        else "ORDER_INTENT_SELL_LONG") if sell else (intent or "ORDER_INTENT_BUY_LONG"))
        # THE VENUE'S OWN SIDE, derived by the venue from the wire
        # intent: the CONTRACT side. A BUY_SHORT is ORDER_SIDE_SELL of
        # the contract (short-truth 6/6, 50/50) and so is a SELL_LONG;
        # a SELL_SHORT that buys the contract back is ORDER_SIDE_BUY,
        # as a BUY_LONG is (S4 review, F1: the fixture used to model the
        # adapter's derivation -- SELL for any sell=True -- which is the
        # desk's `side`, not the venue's). The resting order's side is
        # what the open-orders read hands back. Every existing caller's
        # shape -- no intent, or BUY_LONG -- rests as it did
        venue_side = "SELL" if wire_intent in ("ORDER_INTENT_BUY_SHORT", "ORDER_INTENT_SELL_LONG") else "BUY"
        if self.place_raises is not None:
            if self.rest_on_raise:
                self.rest(oid, venue_side, price, qty, slug, intent=wire_intent)
            raise self.place_raises
        if self.place is not None:
            return self.place(self, oid, slug, price, qty, sell, tif, intent, post_only, good_till)
        if tif == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL":
            f = min(float(self.ioc_fill), float(qty))
            return {"ok": f > 0, "order_id": oid, "status": "filled" if f >= qty else "canceled",
                    "fill_price": price if f > 0 else None, "filled_shares": f,
                    "raw": {"response": {"id": oid}}}
        self.rest(oid, venue_side, price, qty, slug, intent=wire_intent)
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
    """ratelimit.Throttle with the wait taken out (both lanes: E10's
    priority lane is the mirror's per-market read)."""

    async def wait(self):
        return None

    async def acquire(self, priority=False):
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
    monkeypatch.setattr(ml, "pace", lambda s=ms.READ_PACING_S, slots=1: 0.0)     # slots: the write claim (E2)
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
    # E14 (2026-09-08, FILL lane 2): the entry band OFF in this fixture
    # world -- its default quote (his 0.31, bid 0.30 / ask 0.32) sits
    # exactly one cent above his cent, so at the code default of 0.01
    # every first-sight entry here would band-take before it rests and
    # 58 pins of E4's rest-first world would read the IOC first. At 0
    # the band is today's behaviour byte for byte (rules.band_cent None,
    # verdict `off`); tests/test_e14_take_band.py switches it ON (0.01,
    # the code default, pinned there against the environment) for every
    # test of the band, the convention this paragraph names
    monkeypatch.setattr(rules, "MIRROR_TAKE_BAND", 0.0)
    monkeypatch.setitem(edge_gate._cache, "err", None)     # conftest seeds the rest
    monkeypatch.setattr(ml, "_POST_ONLY_OK", True)
    monkeypatch.setattr(ml, "_backoff_until", 0.0)
    monkeypatch.setattr(ml, "_last_tick_at", 0.0)
    # the mode the worker holds for a backed-off tick: module globals
    # that outlive a test, reset so no test inherits another's mode
    monkeypatch.setattr(ml, "_last_mode", None)
    # the pacer's 429 circuit (E2 review round 2): never inherited from a
    # test that read a 429, and every gate measured at its plain gap
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)
    monkeypatch.setattr(ml, "_last_whales", [])
    monkeypatch.setattr(ml, "_unmapped_until", {})
    monkeypatch.setattr(ml, "_terminal_until", {})     # D1: the terminal memo, same shape
    monkeypatch.setattr(ml, "_terminal_book_until", {})    # W1 / R4: the book's own terminal memo
    monkeypatch.setattr(ml, "_terminal_book_state", {})
    # E13: the book memo's confirmation (the first terminal read's
    # instant, the confirmed state) -- never inherited across tests
    monkeypatch.setattr(ml, "_terminal_book_seen", {}, raising=False)
    monkeypatch.setattr(ml, "_terminal_book_confirmed", {}, raising=False)
    monkeypatch.setattr(ml, "_game_full_until", {})    # E1: the full-game memo, by game key
    monkeypatch.setattr(ml, "_cand_cursor", {})        # W2 / P3: the walk's rotation cursor
    monkeypatch.setattr(ml, "_cand_trail", {}, raising=False)   # W2 review: the cursor's trail (the fix)
    monkeypatch.setattr(ml, "_cand_refusal_last", {})  # W2 / P2: the refusal rows' memo
    monkeypatch.setattr(ml, "_cand_write_logged", False)
    # T2 (FILL lane 4): the per-fill record's memos (the rows a failed
    # flush kept, the high-water mark per book id -- ids repeat across
    # this file's pools) and its two once-per-process log latches
    monkeypatch.setattr(ml, "_fill_pending", {}, raising=False)
    monkeypatch.setattr(ml, "_fill_hwm", {}, raising=False)
    monkeypatch.setattr(ml, "_fill_write_logged", False, raising=False)
    monkeypatch.setattr(ml, "_fill_answers_absent_logged", False, raising=False)
    monkeypatch.setattr(ml, "_reopen_write_logged", False, raising=False)   # FILL lane 5: the reopen record's one line
    # E6: the quiet rotation's clock and memos (book ids repeat across
    # this file's pools), and the terminal memos' boot read, already made
    # (test_e6_tick_budget drives the read itself)
    monkeypatch.setattr(ml, "_tick_seq", 0, raising=False)
    monkeypatch.setattr(ml, "_quiet_memo", {}, raising=False)
    monkeypatch.setattr(ml, "_quiet_deferred", {}, raising=False)
    monkeypatch.setattr(ml, "_terminal_memo_loaded", True, raising=False)
    monkeypatch.setattr(ml, "_terminal_memo_last", {"at": 0.0, "sig": None}, raising=False)
    # E7: the candidate memos' `at` beside _unmapped_until, the no_mark
    # memo and its `at`, and the persisted key's last write
    monkeypatch.setattr(ml, "_unmapped_memo", {}, raising=False)
    monkeypatch.setattr(ml, "_no_mark_until", {}, raising=False)
    monkeypatch.setattr(ml, "_no_mark_memo", {}, raising=False)
    monkeypatch.setattr(ml, "_cand_memo_last", {"at": 0.0, "sig": None}, raising=False)
    monkeypatch.setattr(ml, "_rearm_malformed_logged", False)   # L1: the malformed re-arm key's one line
    monkeypatch.setattr(ml, "_last_loss", None)                  # L1: the mode line's loss window
    monkeypatch.setattr(ml, "_last_sleeve", None)                # L2: its sleeve reading
    monkeypatch.setattr(ml, "_BOOK_LOCKS", {})
    # E9 fold (the review's serialisation): a fast tick holds _TICK_LOCK
    # and a full tick WAITS on that hold, and a contended acquire binds
    # an asyncio.Lock to the loop it waited on (asyncio.run makes one per
    # test) -- both locks fresh per test, the hold flag down
    monkeypatch.setattr(ml, "_TICK_LOCK", asyncio.Lock())
    monkeypatch.setattr(ml, "_FAST_LOCK", asyncio.Lock(), raising=False)
    monkeypatch.setattr(ml, "_fast_holding", False, raising=False)
    # E9: the fast path's state -- the woken set, the loop's context (a
    # wake schedules nothing unless a test arms it), the pending run, the
    # last fast tick's clock and what the fast ticks accumulated; the
    # full tick in flight and the last walk / filled books it left
    monkeypatch.setattr(ml, "_FAST_WOKEN", {}, raising=False)
    monkeypatch.setattr(ml, "_fast_ctx", None, raising=False)
    monkeypatch.setattr(ml, "_fast_task", None, raising=False)
    monkeypatch.setattr(ml, "_fast_last_at", 0.0, raising=False)
    monkeypatch.setattr(ml, "_fast_acc", Counter(), raising=False)
    monkeypatch.setattr(ml, "_fast_seconds", {"s": 0.0}, raising=False)
    # E10: the fast ticks' wait / work split and the books stage's wall
    # clock (process-wide counters, read as deltas): fresh per test
    monkeypatch.setattr(ml, "_fast_wall", {"wait": 0.0, "work": 0.0}, raising=False)
    monkeypatch.setattr(ml, "_WALL", ml._WallClock(), raising=False)
    monkeypatch.setattr(ml, "_fast_census", Counter(), raising=False)
    monkeypatch.setattr(ml, "_fast_calls", 0, raising=False)
    monkeypatch.setattr(ml, "_fast_guard_calls", 0, raising=False)
    monkeypatch.setattr(ml, "_fast_ops", 0, raising=False)
    monkeypatch.setattr(ml, "_full_tick", None, raising=False)
    monkeypatch.setattr(ml, "_last_walk", None, raising=False)
    monkeypatch.setattr(ml, "_last_filled", set(), raising=False)
    monkeypatch.setattr(ms, "_ratio_cache", {"at": 0.0, "by_whale": {}})
    monkeypatch.setattr(ms, "_unmapped_until", {})
    monkeypatch.setattr(ms, "_terminal_until", {})     # W1 / R2: the shadow's terminal memo
    ml._WOKEN.clear()
    ml._MIRROR_CENSUS.clear()
    ml._RECENT.clear()

    async def _held(t, slug):
        return 300, 0.31
    # the mirror's own paced, counted read of the venue's positions (E2
    # review, MEDIUM-3: ml._pm_held, le._pm_held's reading); the fixture
    # world answers 300 @ 0.31 as it always did, and section 20 restores
    # the real reader where the page count is what is pinned
    monkeypatch.setattr(ml, "_pm_held", _held)
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


def _place_src():
    """The placement's source for the ordering pins: since E2 the ops
    slot is reserved in _place and the body runs in _place_reserved,
    so a pin on the body's order reads both, wrapper first."""
    return inspect.getsource(ml._place) + inspect.getsource(ml._place_reserved)


def _flatten_src():
    """_flatten_vanished and its slippage leg (_flatten_send, on the
    reserved op), the same way."""
    return inspect.getsource(ml._flatten_vanished) + inspect.getsource(ml._flatten_send)


def _lengthened_wait(monkeypatch, wait=20.0):
    """The environment LENGTHENED the take wait. rules.MIRROR_TAKE_AFTER_S
    is 0 since the E4 addendum ("Remove the 20 second wait on entires
    too"; env may only lengthen it): under a positive wait E2's
    rest-first take -- on the rest's own age and on the arm's -- and the
    arm's staleness bound run exactly as they did, and the pins that
    read those bounds run under this."""
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", float(wait))


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
    # the SELL IOC (filled nothing), then E14b's same-tick rest of the 200 at his cent
    assert [c[3:6] for c in pl] == [(200, True, IOC_TIF), (200, True, GTC_TIF)]


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
    # the reduce still goes out: the IOC (filled nothing), then -- E14b
    # (FILL lane 1) -- the unfilled 200 resting at his cent the same tick
    assert [c[3:6] for c in pl] == [(200, True, IOC_TIF), (200, True, GTC_TIF)], pl
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
    """E16 (the freeze reads twice): the first disagreeing read is a
    suspect -- the add rest cancelled by name, nothing placed, the book
    live; the second fresh walk disagreeing the same way freezes it."""
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b)
    v = _Venue(held={SLUG: 50})
    v.rest("oid-1")
    st0 = _tick(p, v)
    assert b["state"] == "live" and b["frozen_reason"] is None and _census(st0, "venue_ledger_disagree") == 0
    assert _cancels(v) and not _places(v) and _census(st0, "venue_ledger_suspect") == 1
    assert b["last_plan"]["venue_ledger_suspect"]["delta"] == 50.0 and b["last_plan"]["reason"] == "venue_suspect_hold"
    v = _Venue(held={SLUG: 50})
    st = _tick(p, v, now=NOW + 15)
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree"
    assert not _places(v) and _census(st, "venue_ledger_disagree") == 1
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
    # E20: the trip is the GENUINE inversion's -- the venue's magnitude is
    # the leg's (10 against -10); a -5 read freezes the book alone
    # (test_e20_wrong_sign_hold.py)
    p = _pool()
    b = p.add_book(ledger=10)
    p.add_order(b)
    v = _Venue(held={SLUG: -10})
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


def test_the_take_fires_at_or_through_at_the_same_wire_ioc_once_and_waits_only_under_a_lengthened_wait(monkeypatch):
    # under a LENGTHENED wait (the environment's; E2's rest-first take)
    # a rest not yet waited is not taken, whatever the book does
    _lengthened_wait(monkeypatch, 20.0)
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, placed_ts=NOW - 10)
    v = _Venue(ask=0.30)
    v.rest("oid-1")
    _tick(p, v)
    assert not _cancels(v) and not _places(v)
    # the default since the E4 addendum: no wait at all
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 0.0)
    # the market never came to him: held under target
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, placed_ts=NOW - 1)
    v = _Venue(ask=0.32)
    v.rest("oid-1")
    st = _tick(p, v)
    assert not _places(v) and _census(st, "resting_above_level") == 1
    # at/through: cancel the rest, ONE IOC at the SAME wire, this tick
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, placed_ts=NOW - 1)
    v = _Venue(ask=0.30, ioc_fill=300.0)
    v.rest("oid-1")
    st = _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    pl = _places(v)
    assert len(pl) == 1
    _, slug, price, qty, sell, tif, intent, post_only, good_till = pl[0]
    # the IOC's limit is HIS cent, 0.31 (E4 review HIGH-1), the rest's wire 0.30
    assert (price, qty, sell, tif, post_only) == (0.31, 300, False, "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", False)
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
    # the armed take fires after the wait, at or through, as ONE IOC at his cent
    v = _Venue(ask=0.30, ioc_fill=300.0)
    st2 = _tick(p, v, now=NOW + rules.MIRROR_TAKE_AFTER_S + 1)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" and pl[0][2] == 0.31
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
    # since review round 3 (MEDIUM-3) the 60 s backoff is skipped while the
    # pacer's circuit holds -- the abandon stays, the next tick runs
    assert _census(st, "backoff_skipped_circuit") == 1
    assert not _tick(_pool(), _Venue(), now=NOW + 1, keep_backoff=True).get("skipped_backoff")


def test_a_refused_placement_keeps_the_adapters_raw_as_its_receipt():
    """U13: mirror_orders 49-63 (the first two short books, refused
    fifteen times over) carried nothing but the status. The row now
    keeps the adapter's raw -- the guard's figures beside the venue's
    preview -- so the next refusal explains itself."""
    raw = {"preview": {"order": {"price": {"value": "0.89"}, "quantity": 92,
                                 "cashOrderQty": {"value": "0.0000"}}},
           "expected_cost": 10.12, "venue_cost": None, "venue_price": 0.89,
           "venue_quantity": 92.0, "venue_side": None, "cost_space": "echo",
           "expected_price": 0.89, "expected_quantity": 92, "why": "test"}

    def _place(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "preview_mismatch", "fill_price": None,
                "filled_shares": 0.0, "raw": raw}
    p = _pool()
    st = _tick(p, _Venue(place=_place))
    assert _census(st, "place_refused") == 1 and not st["abandoned"]
    o = next(iter(p.orders.values()))
    assert o["state"] == "rejected" and o["order_id"] is None and o["done_at"] == NOW
    assert o["venue_state"] == "preview_mismatch" and o["reason"] == "place_refused:preview_mismatch"
    assert o["receipt"] == raw and o["receipt"]["expected_cost"] == 10.12
    # the statement is the refusal's own, with the receipt as the persist's shape
    sent = [(s, a) for k, s, a in p.sent if "ml-order-refused" in s]
    assert len(sent) == 1 and json.loads(sent[0][1][3]) == raw
    # the new short status counts under the family by its prefix
    assert ml._family("place_refused:preview_side_mismatch") == "place_refused"


def test_a_refused_receipt_is_bounded_and_always_valid_json():
    big = {"preview": {"order": {"blob": "x" * 6000}}, "expected_cost": 10.12,
           "venue_cost": None, "cost_space": "echo", "why": "w"}
    s = ml._refusal_receipt(big)
    assert len(s) <= ml._REFUSAL_RECEIPT_MAX
    d = json.loads(s)
    assert d["expected_cost"] == 10.12 and d["cost_space"] == "echo" and d["why"] == "w"
    assert d["truncated"]["preview"] > 6000 and "preview" not in d
    # the scalars alone over the bound: the head of the text, still JSON
    s2 = ml._refusal_receipt({"why": "y" * 5000, "expected_cost": 1.0})
    assert len(s2) <= ml._REFUSAL_RECEIPT_MAX and json.loads(s2)["truncated"] is True
    # under the bound, byte-for-byte the persist's own dump
    small = {"expected_cost": 10.12, "preview": {"a": 1}}
    assert ml._refusal_receipt(small) == json.dumps(small, default=str)
    assert json.loads(ml._refusal_receipt("not a dict")) == {"raw": "not a dict"}
    # a NaN or an infinity in the adapter's raw (a venue quantity that
    # read "NaN") is not JSON: the jsonb cast would reject the whole
    # update and the refusal would never be recorded (U13 review, F3)
    nan_raw = {"expected_cost": 10.12, "venue_quantity": float("nan"),
               "preview": {"order": {"x": float("inf"), "y": [float("-inf"), 1.0]}}}
    s3 = ml._refusal_receipt(nan_raw)
    d3 = json.loads(s3, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    assert d3["venue_quantity"] == "nan" and d3["expected_cost"] == 10.12
    assert d3["preview"]["order"]["x"] == "inf" and d3["preview"]["order"]["y"] == ["-inf", 1.0]


# ---------------------------------------------------------- 8. flattens F

def test_paired_out_target_zero_rests_at_his_cent_and_takes_within_a_cent_of_him_never_markets(monkeypatch):
    """A paired-out target of 0 (he holds both tokens; his equivalent
    1 - q). E4 (owner order "exit when he exits at his price or within
    1c"): the rest goes at HIS cent, ceil(1 - q), never lifted to the
    ask (it was max(1 - q, ask)); the bid within a cent of him takes
    ONE IOC at the lowest cent at or above his price less the
    tolerance, the same tick; the slippage path is never taken; a rest
    past its TTL at the same cent STANDS (`requote_same_wire`), never
    cancelled and re-placed."""
    monkeypatch.setattr(le, "sell_limit_price", lambda *a, **k: pytest.fail("slippage path taken"))
    # his equivalent 0.28 (the other token at 0.72); the bid 0.25, three
    # cents under him: outside the tolerance, the rest at 0.28 -- his
    # cent, not the 0.32 ask -- post-only GTC, nothing taken, nothing closed
    p = _pool(fills=_his(300, other_size=300, other_px=0.72), snap={M: 300.0, N: 300.0})
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300}, bid=0.25, ask=0.32)
    st = _tick(p, v)
    pl = _places(v)
    assert len(pl) == 1
    _, slug, price, qty, sell, tif, intent, post_only, _gt = pl[0]
    assert (price, qty, sell, tif, post_only) == (0.28, 300, True, "TIME_IN_FORCE_GOOD_TILL_CANCEL", True)
    assert price == rules.sell_wire(1 - 0.72) and price < 0.32, "his cent, never the ask"
    assert "close" not in _kinds(v) and "slug_bid" not in _kinds(v)
    o = next(iter(p.orders.values()))
    assert o["kind"] == "flatten_paired" and _census(st, "flatten_rested") == 1
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "exit_take") == 0
    lp = b["last_plan"]
    assert lp["exit_px"] == pytest.approx(0.28) and lp["exit_px_src"] == "his_fill"
    assert lp["exit_floor"] == pytest.approx(0.27) and lp["exit_rest"] == 0.28 and lp["exit_take"] == 0.27
    # (FILL lane 3, 2026-09-08, re-pinned: the held tick records the band bound beside the floor;
    # equal to it at the default band, so nothing else on the plan or the wire moves)
    assert lp["exit_out_of_tol"] == {"bid": 0.25, "ask": 0.32, "floor": 0.27, "band_floor": 0.27, "at": NOW}
    # his equivalent above the ask (0.60 -> 0.40): the rest at his cent, as before
    p2 = _pool(fills=_his(300, other_size=300, other_px=0.60), snap={M: 300.0, N: 300.0})
    p2.add_book(ledger=300)
    v2 = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32)
    _tick(p2, v2)
    assert _places(v2)[0][2] == 0.40
    # the bid within a cent of him (0.27 against his 0.28): ONE IOC at
    # 0.27 that tick, filled, `exit_take`; no rest first
    p3 = _pool(fills=_his(300, other_size=300, other_px=0.72), snap={M: 300.0, N: 300.0})
    b3 = p3.add_book(ledger=300)
    v3 = _Venue(held={SLUG: 300}, bid=0.27, ask=0.32, ioc_fill=300.0)
    st3 = _tick(p3, v3)
    pl3 = _places(v3)
    assert len(pl3) == 1 and pl3[0][2:6] == (0.27, 300, True, "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
    assert _census(st3, "exit_take") == 1 and _census(st3, "take_placed") == 1
    assert _census(st3, "flatten_rested") == 0 and b3["ledger_net"] == 0
    # unfilled at TTL at the SAME cent: NOT cancelled, NOT re-quoted (the
    # no-op is named `requote_same_wire`); still held outside the cent
    v4 = _Venue(held={SLUG: 300}, bid=0.25, ask=0.32)
    v4.orders = v.orders
    st4 = _tick(p, v4, now=NOW + rules.MIRROR_REST_TTL_S + 1)
    assert not _cancels(v4) and not _places(v4) and st4["requotes"] == 0
    assert _census(st4, "requote_same_wire") == 1 and _census(st4, "reduce_unfilled") == 0
    assert _census(st4, "open_order_pending") == 1 and _census(st4, "exit_out_of_tol") == 1
    assert p.orders[o["id"]]["state"] == "open" and b["last_plan"]["requote_same_wire"] is True


def test_a_confirmed_vanish_rests_then_follows_the_sole_and_coheld_rules(monkeypatch):
    """A vanish he gave NO PRICE for (E4: `_unpriced`, exit_px_src
    'none'): the C16 rest-then-slippage path, as before. A priced
    vanish is section 21's."""
    p = _pool(fills=_unpriced(), snap=None)                  # gone by fills; no snapshot; no price of his
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
    assert b["last_plan"]["exit_px_src"] == "none" and b["last_plan"]["exit_px"] is None
    # the rest stood MIRROR_FLATTEN_REST_S unfilled: sole holder -> close_position with the bound
    v2 = _Venue(held={SLUG: 300})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + rules.MIRROR_FLATTEN_REST_S + 1, http=http)
    assert ("cancel", "oid-1", SLUG) in v2.calls
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v2.calls and "slug_bid" not in _kinds(v2)
    assert b["ledger_net"] == 0 and st2["flattened"] == 1
    # co-held -- the desk's 200 explained shares beside our 300 -- one IOC at
    # sell_limit_price(bid) for OUR quantity, never close_position
    async def _held(t, slug):
        return 500, 0.31
    monkeypatch.setattr(ml, "_pm_held", _held)
    p3 = _pool(fills=_unpriced(), snap=None)
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
    # the bid two cents under his 0.31 (E4): outside the tolerance, so
    # the paired flatten RESTS -- at his cent, 0.31 -- and takes nothing
    v = _Venue(bid=0.29, held={SLUG: 300})
    st = _tick(p, v, http=_Http(status=500))
    assert _census(st, "vanish_unconfirmed") == 1 and _census(st, "flatten_vanished") == 0
    assert next(iter(p.orders.values()))["kind"] == "flatten_paired" and b["ledger_net"] == 300
    assert _places(v)[0][2] == 0.31 and _census(st, "exit_out_of_tol") == 1


def test_an_unreadable_bid_names_no_bid_for_flatten(monkeypatch):
    async def _held(t, slug):
        return 500, 0.31
    monkeypatch.setattr(ml, "_pm_held", _held)
    p = _pool(fills=_unpriced(), snap=None)
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
    # FILL lane 5 (2026-09-08): paired out, he still HOLDS the book's token, so the
    # clock holds the book by name (`he_holds` on a read tick, `he_holds_unread`
    # on a quiet skip) -- the row filled at zero, nothing closed (re-pinned from
    # `cashed_out` on the clock); the close lands once he has LEFT (the vanish)
    assert row["status"] == "filled" and b["state"] == "live"
    assert b["last_plan"]["close"] in ("he_holds", "he_holds_unread") and _census(st3, "closed_cashed_out") == 0
    p.fills, p.snap = [], {M: 0.0, N: 0.0}
    # (the vanish is a READ tick's verdict: the quiet rotation's skips hold `he_holds_unread` until it)
    for i in range(1, int(ml.QUIET_EVERY_TICKS) + 2):
        st3b = _tick(p, _Venue(), now=NOW + rules.MIRROR_FLAT_CLOSE_S + 1 + 30 * i, http=_gone())
        if b["state"] == "closed":
            break
        assert b["last_plan"]["close"] == "he_holds_unread" and row["status"] == "filled", i
    assert row["status"] == "cashed_out" and b["state"] == "closed"
    assert _census(st3b, "closed_cashed_out") == 1 and st3b["closed_books"] == 1
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
    # the SELL IOC, then E14b's same-tick rest of the unfilled 200 (never a BUY)
    assert [c[3:6] for c in pl] == [(200, True, IOC_TIF), (200, True, GTC_TIF)]
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
        # FILL lane 0a: the rail's default (the env no longer reaches the worker; the constant does)
        monkeypatch.setattr(rules, "LIVE_SIDE_PRICE_BAND_MAX", 0.15)
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


def test_the_read_budget_is_the_candidates_own_so_live_books_never_cap_new_ones(monkeypatch):
    """U12 (owner order 2026-09-06: "I don't want to cap books opened at
    all"). Every live book is walked first and each quote read charged
    `t.reads`; candidates then ran only while `t.reads` was under
    MAX_MARKETS_PER_TICK (20). With 20+ live books no candidate was
    ever read: a count cap by another road. Twenty-five live books,
    one candidate: every book is read (exits must be managed), the
    candidate is read too, a twenty-sixth book opens, and the tick is
    not `capped_tick`. `t.reads` stays the total for the stats line;
    `tick_s` is the wall time an operator watches as books grow."""
    assert ml.MAX_MARKETS_PER_TICK == 40 and ms.MAX_MARKETS_PER_TICK == 20   # the live lane's own 40 (E2); the shadow keeps 20
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
    # the candidates' OWN budget still binds, under the same name: 40
    # candidates read behind the 25 books, the forty-first not -- at the
    # DEFAULT venue-call guard (60), which the books' reads never count
    # against (E2 review, MEDIUM-5)
    assert rules.MIRROR_VENUE_CALLS_PER_TICK == 80
    p2 = _pool(conds=[f"c{i}" for i in range(41)])
    _many_books(p2, 25)
    v2 = _Venue()
    st2 = _tick(p2, v2)
    # E6: the candidate stage takes what the tick's venue-call budget
    # (60) leaves after the positions walk, the open-orders read and the
    # 25 book reads -- 33 here, under the cap of 40 and over the floor
    # of 10 -- so the books' reads are no longer free to the candidates
    assert st2["capped_tick"] is True and st2["reads"] == 25 + (ml.VENUE_CALLS_PER_TICK - 27) == 25 + 33
    # the tick's wall time is published, 1 dp, on every tick
    assert isinstance(st["tick_s"], float) and st["tick_s"] >= 0.0 and st["tick_s"] == round(st["tick_s"], 1)
    assert "tick_s" in ml._new_stats() and ml._new_stats()["tick_s"] is None
    # the tick's own counters say what was charged where
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW, stats=ml._new_stats())
    _run(ml._bbo(t, SLUG, book=True))
    _run(ml._bbo(t, SLUG))
    assert (t.reads, t.cand_reads) == (2, 1), "a book's read is never charged to the candidates"
    src = inspect.getsource(ml._tick)
    assert "t.cand_reads >= MAX_MARKETS_PER_TICK" in src and "t.reads >= MAX_MARKETS_PER_TICK" not in src
    assert "ms.MAX_MARKETS_PER_TICK" not in src, "the live lane reads its OWN budget (review round 2)"
    assert ml.MAX_MARKETS_PER_TICK == 40, "the budget is 40 since E2 and still capped_env"


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
    v = _Venue(held={_ZZ["us_market_slug"]: -10})    # E20: the genuine inversion (magnitude the leg's)
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
    p.fills[-1]["detected_at"] = NOW + 10      # E15: his sale, late-known, is the reduce's witness
    v2 = _Venue(held={SLUG: 300})
    _tick(p, v2, now=NOW + 30)
    pl = _places(v2)
    # the SELL IOC, then E14b's same-tick rest of the unfilled 200
    assert b["state"] == "live" and [c[3:6] for c in pl] == [(200, True, IOC_TIF), (200, True, GTC_TIF)]
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
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 20)
    v2 = _Venue()
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30)
    assert ("cancel", "oid-b", _ZZ["us_market_slug"]) in v2.calls
    assert p.orders[ob["id"]]["state"] == "cancelled" and b["state"] == "closed" and st2["closed_books"] == 1


def test_a_stale_take_arm_never_takes_a_fresh_rest_and_is_cleared_by_a_rest_or_a_finish(monkeypatch):
    """minor 2. An hour-old arm and a rest placed 5 s ago with the ask
    at the wire: no IOC before MIRROR_TAKE_AFTER_S of the REST; a rest
    the venue accepts, and an order that finishes, clear the arm. Under
    a lengthened wait (E4 addendum: the default is 0)."""
    _lengthened_wait(monkeypatch, 20.0)
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


def test_the_first_post_only_refusal_starts_the_take_clock_under_the_thirty_second_poll(monkeypatch):
    """re-review minor 2. A book that keeps crossing at the 30 s poll:
    every post-only 400 once re-stamped the arm, and _act reads the
    arm before it re-places, so the arm was always 30 s old and the
    take never fired. The FIRST refusal starts the clock: no IOC
    before MIRROR_TAKE_AFTER_S of it, then exactly one, at the same
    wire, and the book is on target. Under a lengthened wait (E4
    addendum: at the default of 0 the first tick takes)."""
    _lengthened_wait(monkeypatch, 20.0)

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
    assert at - NOW >= wait and c[2] == 0.31 and c[3] == 300      # the IOC at his cent (HIGH-1)
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
    the slippage path runs at +301 s, not +501 s. An UNPRICED vanish
    (E4): the slippage leg runs only when he gave no exit price."""
    p = _pool(fills=_unpriced(), snap=None)
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
    # FILL lane 5 (2026-09-08): paired out, he still holds the book's token -- the clock
    # holds by name and the flat clock is CARRIED, not dropped (re-pinned from `closed`)
    assert b["state"] == "live" and b["last_plan"]["close"] in ("he_holds", "he_holds_unread")
    assert b["last_plan"]["flat_since"] == NOW + 30 and _census(st4, "closed_cashed_out") == 0
    p5 = _pool(fills=_his(300, other_size=300), snap={M: 300.0, N: 300.0})
    b5 = p5.add_book(ledger=300, last_plan={"flat_since": NOW - 3 * 3600})
    _tick(p5, _Venue(held={SLUG: 300}), http=_mkt(300.0, 300.0))
    assert "flat_since" not in b5["last_plan"] and b5["state"] == "live" and b5["last_plan"]["target"] == 0


def test_the_sell_wire_is_priced_off_his_unrounded_equivalent():
    """minor 5. His other-token BUY at 0.47996 is 0.52004 to him: the
    SELL rests at 0.53, never the 4-place 0.52 a cent under him; the
    worker's level selection agrees with the shadow's -- exactly, since
    E4 review round 3 (L-3) rounds the equivalent the same way in both
    (round(1 - p, 6); the shadow's figure was 4-place before)."""
    p = _pool(fills=_his(300, other_size=300, other_px=0.47996), snap={M: 300.0, N: 300.0})
    p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300}, bid=0.30, ask=0.32)
    _tick(p, v)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True and pl[0][2] == 0.53
    o = next(iter(p.orders.values()))
    assert o["his_level"] == pytest.approx(0.52004) and o["wire"] == 0.53 and o["kind"] == "flatten_paired"
    assert ms.his_level(p.fills, M, N, reducing=True) == 0.52004, "the shadow's figure is the live one's (L-3)"
    assert rules.sell_price(0.52, 0.32) == 0.52 and rules.sell_price(0.52004, 0.32) == 0.53
    for fills in (_his(), _his(300, sold=200), _his(300, other_size=300, other_px=0.6),
                  _his(300, other_size=300, other_px=0.47996, sold=100),
                  [_fill(M, "BUY", 300.0, 0.0, NOW - 3000)], []):
        for reducing in (False, True):
            ours, theirs = ml._his_level(fills, M, N, reducing), ms.his_level(fills, M, N, reducing)
            assert (ours is None) == (theirs is None) and ours == theirs, (fills, reducing)


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
        p = _pool(fills=_unpriced(), snap=None)      # an unpriced vanish: the slippage leg (E4)
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
    async def _held0(t, slug):
        return 0, None
    monkeypatch.setattr(ml, "_pm_held", _held0)
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
    async def _held300(t, slug):
        return 300, 0.31
    monkeypatch.setattr(ml, "_pm_held", _held300)
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
    monkeypatch.setattr(ml, "pace", lambda s=ms.READ_PACING_S, slots=1: paced.extend([s] * int(slots)))
    reads = ("open_orders", "status", "trades", "slug_bid")

    def _write_gaps(calls):
        # the WRITES pace too since E2 (review HIGH-1): one claim in
        # _paced per write call; a BUY's create claims its own inside
        # the adapter (pmus.submit_fok paced_pair, not the fake's)
        return sum(1 for c in calls if c[0] in ("place", "cancel", "close"))
    p = _pool()
    v = _Venue(place_raises=TimeoutError("t"), rest_on_raise=True)
    _tick(p, v)
    assert ("open_orders", [SLUG]) in v.calls
    assert len([c for c in v.calls if c[0] in reads]) == 2   # the account's list, then the slug's
    assert len(paced) == 2 + _write_gaps(v.calls) == 3, "two reads and the BUY's claim"
    paced.clear()

    async def _held(t, slug):
        return 500, 0.31
    monkeypatch.setattr(ml, "_pm_held", _held)
    p3 = _pool(fills=_unpriced(), snap=None)         # an unpriced vanish: the co-held IOC (E4)
    p3.manual_shares[SLUG] = 200.0
    b3 = p3.add_book(ledger=300)
    p3.add_order(b3, side=SELL, wire=0.32, kind="flatten_vanished",
                 placed_ts=NOW - rules.MIRROR_FLATTEN_REST_S - 1)
    v3 = _Venue(held={SLUG: 500}, flatten_bid=0.29, ioc_fill=300.0)
    v3.rest("oid-1", "SELL", 0.32, 300, created=NOW - 400)
    _tick(p3, v3, http=_gone())
    assert ("slug_bid", SLUG, True) in v3.calls and b3["ledger_net"] == 0
    assert len(paced) == len([c for c in v3.calls if c[0] in reads]) + _write_gaps(v3.calls)
    assert _write_gaps(v3.calls) == 2, "the cancel and the SELL IOC, one gap each"
    src = inspect.getsource(ml)
    assert "to_thread(t.pmus.open_orders" not in src and "to_thread(t.pmus.slug_bid" not in src
    # and no write goes to the venue bare (E2 review, HIGH-1)
    assert "to_thread(t.pmus.cancel_order" not in src and "to_thread(fn, *args" not in src
    assert "asyncio.to_thread(_paced, t.pmus.cancel_order, oid, slug)" in src
    assert "asyncio.to_thread(_paced, fn, *args, **kwargs)" in inspect.getsource(ml._guarded)


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

    async def _never(t, slug):
        held_calls.append(slug)
        raise AssertionError("a second positions walk")
    monkeypatch.setattr(ml, "_pm_held", _never)
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


def test_the_venues_200_refusal_shape_arms_the_take_and_the_400_shapes_read_as_before(monkeypatch):
    """The worker seam of the to-a-tee program's Phase 7 rung 1: _place
    hands take_arms the RAW DICT, so the venue's second post-only
    refusal shape (a 200 whose order came back REJECTED with an
    execution of type REJECTED; the adapter's _post_only_cross) arms
    the take, and the rejected row names the order the venue minted.
    Every shape the adapter produced before reads as it did: a 400
    dict arms, a 429 dict does not, an empty raw does not (None did
    not), a 200 dict without the flag does not (the bare 200 did not).
    Under a lengthened wait (E4 addendum): at the default of 0 a
    crossing book takes FIRST and no rest is refused."""
    _lengthened_wait(monkeypatch, 20.0)
    src = _place_src()
    assert "rules.take_arms(raw if isinstance(raw, dict) else code)" in src
    p = _pool()
    b = p.add_book(ledger=0)
    st = _tick(p, _Venue(bid=0.30, ask=0.30, place=_refusal(_SHAPE_200, order_id=True)))
    assert _census(st, "post_only_rejected") == 1 and b["take_armed_ts"] == NOW
    o = next(iter(p.orders.values()))
    assert o["state"] == "rejected" and o["reason"] == "post_only_rejected:200"
    assert o["order_id"] == "oid-1", "the 200 shape minted an order; the row names it"
    assert not st["abandoned"] and _census(st, "take_placed") == 0
    # the armed take fires after the wait, at or through, as ONE IOC at his cent
    v = _Venue(bid=0.30, ask=0.30, ioc_fill=300.0)
    st2 = _tick(p, v, now=NOW + rules.MIRROR_TAKE_AFTER_S + 1)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" and pl[0][2] == 0.31
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
    wire and no IOC is placed. Under a lengthened wait (E4 addendum:
    at the default of 0 the arm never fires the IOC -- the take-first
    does, on the price alone -- and the bound is not read)."""
    _lengthened_wait(monkeypatch, 20.0)
    room = _room_holder(monkeypatch, 1e9)
    p = _pool()
    b = p.add_book(ledger=0)
    st = _tick(p, _Venue(bid=0.30, ask=0.30, place=_refusal({"status_code": 400})))
    assert b["take_armed_ts"] == NOW and _census(st, "post_only_rejected") == 1
    room["day"] = 0.1
    # three ticks inside twice the wait (a quarter, one and a quarter,
    # exactly twice: 5 / 25 / 40 s at E2's 20 s, 30 / 150 / 240 at 120)
    wait = float(rules.MIRROR_TAKE_AFTER_S)
    for dt in (wait / 4, wait + wait / 4, 2 * wait):
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
    at +60 plus the wait. Under a lengthened wait (E4 addendum)."""
    _lengthened_wait(monkeypatch, 20.0)
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
    assert len(ioc) == 1 and ioc[0][2] == 0.31 and _census(st, "take_placed") == 1
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
        p = _pool(fills=_unpriced(), snap=None)      # an unpriced vanish: the slippage leg (E4)
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
    async def _held301(t, slug):
        return 301, 0.31
    monkeypatch.setattr(ml, "_pm_held", _held301)
    p, b = _book()
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v, http=gone)
    assert not [c for c in v.calls if c[0] == "close"]
    assert _census(st, "flatten_holding_disagrees") >= 1

    # both readings at our own ledger: still the sole holder, one walk
    async def _held300(t, slug):
        return 300, 0.31
    monkeypatch.setattr(ml, "_pm_held", _held300)
    p, b = _book()
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v, http=gone)
    assert [c for c in v.calls if c[0] == "close"], "an exact match is sole"
    assert _census(st, "flatten_holding_disagrees") == 0
    # ONE fresh whole-account read on this path, not two: the second was
    # a 50-page walk outside the pacer, immediately before the most
    # dangerous order the worker sends (round-one review)
    src = _flatten_src()
    assert src.count("_pm_held(t, r.slug)") == 1


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
    assert "t.reads >= MAX_MARKETS_PER_TICK" not in inspect.getsource(ml._market_snap)

    # its own budget still binds, under its own name -- for a CANDIDATE
    # (U12: the budget is the candidates'; a book is never refused by
    # it). At the tick level the candidate walk's own quote budget stops
    # a candidate FIRST (a candidate's per-market read follows its quote
    # read one for one, so `t.cand_mkt_reads` never reaches the cap
    # before `t.cand_reads` does): with the budget at 0 no candidate is
    # read at all, by the name `capped_tick`
    monkeypatch.setattr(ml, "MAX_MARKETS_PER_TICK", 0)
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
    t2.cand_mkt_reads = ml.MAX_MARKETS_PER_TICK
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
    assert t2.mkt_reads == 0 and t2.cand_mkt_reads == ml.MAX_MARKETS_PER_TICK
    # and the same tick reads a BOOK's market past it, charging the
    # total and never the candidates' budget
    t3 = ml._Tick(pool=_pool(snap=None), pmus=_Venue(), http=_mkt(300.0, 0.0), now=now,
                  stats=ml._new_stats())
    t3.cand_mkt_reads = ml.MAX_MARKETS_PER_TICK
    ml._current_stats = t3.stats
    try:
        assert _run(ml._market_snap(t3, "rn1", CID, M, N, book=True))[0] is True
    finally:
        ml._current_stats = prior
    assert _pos_calls(t3.http) and t3.stats["snap_market_fresh_reads"] == 1
    assert (t3.mkt_reads, t3.cand_mkt_reads) == (1, ml.MAX_MARKETS_PER_TICK)
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
        monkeypatch.setattr(ml, "MAX_MARKETS_PER_TICK", 20)
        if arm == "no_address":
            p.whale_address = {}
        if arm == "capped":
            # at the tick level the candidate walk's own budget stops a
            # candidate before its per-market read (see the budget test
            # above), so the clause is driven at the function's level
            t = ml._Tick(pool=p, pmus=_Venue(), http=_mkt(300.0, 0.0), now=now,
                         stats=ml._new_stats())
            t.cand_mkt_reads = ml.MAX_MARKETS_PER_TICK
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
    src = _place_src()
    assert src.index("_book_delta") < src.index("_POST_ONLY_OK = False")


def test_a_close_row_never_trips_overspend(monkeypatch):
    """A CLOSE row's wire is deliberately 0.0, not None, so a naive
    `avg_px > wire` is true for EVERY vanish flatten."""
    assert ml._overspend_of({"side": SELL, "tif": "CLOSE", "wire": 0.0},
                            {"avg_px": 0.29}) is False
    assert ml._overspend_of({"side": BUY, "tif": "CLOSE", "wire": 0.0},
                            {"avg_px": 0.29}) is False
    p = _pool(fills=_unpriced(), snap=None)          # an unpriced vanish: the close (E4)
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

    Driven on a tick that really freezes `venue_ledger_disagree` (E16:
    the second disagreeing walk)."""
    from sportsassets.api import app as api_app
    p = _pool()
    b = p.add_book(ledger=100)
    _tick(p, _Venue(held={SLUG: 400}))
    st = _tick(p, _Venue(held={SLUG: 400}), now=NOW + 15)
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
    # (a fresh world: E7's no_mark memo of the OPEN empty read cleared,
    # as the D1 memo is below -- the process remembers the market)
    assert ml._no_mark_until == {("rn1", CID): NOW + ml.NO_MARK_TTL_S}, "an OPEN empty book is memoised (E7)"
    ml._no_mark_until.clear()
    ml._no_mark_memo.clear()
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
    assert ml._terminal_until == {("rn1", CID): NOW + ms.UNMAPPED_TTL_S}, "an ended market is memoised (D1)"
    # a quoted OPEN market on the same fixture DOES open a book: the refusal
    # above was the state (a fresh world: the D1 memo of the CLOSED read cleared)
    ml._terminal_until.clear()
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
    async def _held(t, slug):
        return 500, 0.31
    monkeypatch.setattr(ml, "_pm_held", _held)
    # a fresh world: the CLOSED read above memoised the book (W1 / R4)
    # and a memo-skipped book never reaches the leg at all
    ml._terminal_book_until.clear()
    p, b, v = _vanish_after_the_rest(state, held=500, manual=200.0, flatten_bid=0.29, ioc_fill=300.0,
                                     **quotes)
    st = _tick(p, v, http=gone)
    assert "close" not in _kinds(v) and "slug_bid" not in _kinds(v) and not _places(v), v.calls
    assert _census(st, "venue_halted") == 2 and _census(st, "no_bid_for_flatten") == 0, st["census"]
    assert b["ledger_net"] == 300 and b["last_reason"] == "venue_halted"
    # THE CONTROL: the slug OPEN on the same fixture takes the leg (a
    # fresh world again: the co-held CLOSED read memoised the book)
    ml._terminal_book_until.clear()
    p, b, v = _vanish_after_the_rest("MARKET_STATE_OPEN", held=500, manual=200.0, flatten_bid=0.29,
                                     ioc_fill=300.0, bid=0.30, ask=0.32)
    st = _tick(p, v, http=gone)
    assert ("slug_bid", SLUG, True) in v.calls and "close" not in _kinds(v)
    ioc = [c for c in _places(v) if c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert len(ioc) == 1 and ioc[0][2] == le.sell_limit_price(0.29) and ioc[0][3] == 300 and ioc[0][4] is True
    assert b["ledger_net"] == 0 and _census(st, "venue_halted") == 0

    async def _held300(t, slug):
        return 300, 0.31
    monkeypatch.setattr(ml, "_pm_held", _held300)
    p, b, v = _vanish_after_the_rest("MARKET_STATE_OPEN", bid=0.30, ask=0.32)
    st = _tick(p, v, http=gone)
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v.calls and b["ledger_net"] == 0
    assert _census(st, "venue_halted") == 0 and st["flattened"] == 1
    # the refusal sits BEFORE the position read and the bid read, by the source
    src = _flatten_src()
    i_refuse = src.index('_mirror_stop("venue_halted", w)')
    assert i_refuse < src.index("_pm_held(t, r.slug)") < src.index("t.pmus.slug_bid")
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
    # the flat clock: FILL lane 5 (2026-09-08) -- his 300 still on the book's side
    # (the fixture's fills) hold the flat book by name under the operator's flatten
    # too (re-pinned from `closed_cashed_out` on the clock); the close lands once
    # he has left (the vanish confirmed), the row cashed out, still no under_one_share
    st3 = _tick(p, _Venue(held={SLUG: 0}), now=NOW + 30 + rules.MIRROR_FLAT_CLOSE_S + 1)
    assert b["state"] == "live" and row["status"] == "filled" and _census(st3, "closed_cashed_out") == 0
    assert b["last_plan"]["close"] in ("he_holds", "he_holds_unread") and _census(st3, "under_one_share") == 0
    p.fills, p.snap = [], {M: 0.0, N: 0.0}
    for i in range(1, int(ml.QUIET_EVERY_TICKS) + 2):     # the vanish is a read tick's verdict
        st3b = _tick(p, _Venue(held={SLUG: 0}), now=NOW + 30 + rules.MIRROR_FLAT_CLOSE_S + 1 + 30 * i, http=_gone())
        if b["state"] == "closed":
            break
    assert b["state"] == "closed" and row["status"] == "cashed_out" and _census(st3b, "closed_cashed_out") == 1
    assert _census(st3b, "under_one_share") == 0 and not _places(v2)
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
    # three SELL legs size through it: the exit's take within a cent of
    # him (E4), the take at the wire with no price of his, the rest
    # (FILL lane 3, 2026-09-08: the no-rest long exit's band site sizes the same way, 3 -> 4)
    assert act.count("_sell_qty(book, p.qty)") == 4 and 'int(book.get("ledger_net") or 0)) if p.side == SELL' not in act
    assert "math.floor" not in inspect.getsource(ml._sell_qty)


def _sub_share_vanish(held_venue, pm_held, monkeypatch, manual=0.0, **venue_kw):
    """A sole or co-held book at ledger 1 / row 0.76 on a vanished market,
    its flatten rest stood and cancelled, the slippage leg due."""
    async def _held(t, slug):
        return pm_held, 0.12
    monkeypatch.setattr(ml, "_pm_held", _held)
    p = _pool(fills=_unpriced(), snap=None)          # an unpriced vanish: the slippage leg (E4)
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
    # (of the slippage leg itself: since S4 _flatten_vanished carries the
    # short cover's own under_one_share gate beside it)
    src = inspect.getsource(ml._flatten_send)
    i_sole = src.index("sole = sole_walk and sole_read")
    i_gate = src.index('_mirror_stop("under_one_share", w)')
    assert i_sole < src.index("qty = _sell_qty(book, ledger)") < i_gate < src.index("t.pmus.slug_bid")
    assert src.count('_mirror_stop("under_one_share", w)') == 1
    # S4: the slippage leg is the LONG book's alone -- a short book is
    # refused at its head (`book_error`) and its cover is _act's priced
    # order; the co-held arm sizes the long book's IOC directly
    assert "qty = ledger\n    if not sole:\n        qty = _sell_qty(book, ledger)" in src
    assert "short_reduce_unproven" not in inspect.getsource(ml._flatten_send)


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
    # U12c review's two names after it; C1's four mapping-lane names
    # after those, LAST
    assert keys[keys.index("ledger_dust") + 1] == "short_open"
    # (E16 moved the tail by its four names, E18 by its six, E17 by its eight, E19 by its one, L7 by its one: -69 -> -89;
    # E20 by its one, E14b (FILL lane 1) by its one and E14 (FILL lane 2) by its one `take_in_band`: -89 -> -92;
    # FILL lane 3 by its three `exit_take_in_band` / `cover_in_band` / `order_open_his_exit`: -92 -> -95; T2 (FILL lane 4) by its two: -95 -> -97; FILL lane 5 by its three: -97 -> -100)
    assert keys[-100:] == ("books_unreadable", "ratio_stepped", "under_min_notional",
                          "shadow_check_skipped", "map_reads_capped", "map_source_unverified",
                          "map_venue_read", "map_cache_hit",
                          # C1 round 2: the grammar class's certification names
                          "grammar_echo_unreadable", "grammar_tripped", "grammar_probation",
                          "grammar_echo_unverified", "grammar_echo_ok", "side_echo_mismatch",
                          # the copy lane's soccer floor, lifted for the mirror (owner order)
                          "soccer_floor_lifted",
                          # E1: the per-game cap's names -- scaled, full, the game
                          # unreadable, a candidate skipped by the full-game memo --
                          # inserted before the pinned last key (all past the served prefix)
                          "game_cap_scaled", "game_cap_full", "game_unreadable", "cand_game_full_skipped",
                          # E2: the take's two verdicts and the venue-call
                          # counter with its soft guard, before the last key
                          "take_at_his_level", "take_refused_price", "venue_calls",
                          "venue_calls_capped",
                          # E2 review: the in-flight refusal under an abandon
                          "abandoned_in_flight",
                          # E2 review round 2: the walk-order judge raised (named, the walk goes on)
                          "walk_error",
                          # E2 review round 3: the placement-429 abandon that skipped the backoff
                          "backoff_skipped_circuit",
                          # E4: the exit at his price (its take, its hold, the
                          # same-wire re-quote skipped) and the addendum's
                          # entry take-first, before the last key
                          "exit_take", "exit_out_of_tol", "requote_same_wire", "take_first",
                          # S4: the probe placed / proved, the cover refused unproven, the
                          # probe that filled at create and was booked (v3), the
                          # cover's rest / take / hold outside the cent
                          "s4_probe_placed", "s4_proved", "s4_unproven", "s4_probe_filled",
                          "short_cover_rest", "short_cover_take", "short_cover_out_of_tol",
                          # W1 / R4: an open book's reads skipped on the book memo
                          "book_terminal_skipped",
                          # W2 / P2: the four once-silent candidate exits and the
                          # refusal table's write failure, before the last key
                          "long_token_unknown", "target_zero", "book_row_unreadable",
                          "cand_unread_capped", "refusal_write_failed",
                          # E5: the frozen exit's placement, its six refusals and
                          # the excess it sells past the ledger; the register's
                          # three names -- before the last key
                          "frozen_reduce", "frozen_exits_off", "frozen_venue_unread",
                          "frozen_coheld", "frozen_venue_flat", "frozen_no_his_exit",
                          "frozen_reduce_only", "frozen_excess_sold",
                          "registered_books", "registered_sign_refused", "registered_unreadable",
                          # E5 review: the fill-this-tick refusal, the unexplained surplus, the
                          # registered book's no-increase -- before the last key
                          "frozen_fill_this_tick", "frozen_venue_unexplained",
                          # E16: the freeze's suspect read and its increase hold, the
                          # frozen reduce on his witnessed sale, the thaw held by name
                          "venue_ledger_suspect", "venue_suspect_hold", "frozen_reduce_on_fill", "thaw_held",
                          # L7: a candidate refused on its slug's own date more than a
                          # day past with no fill of his in a day -- before E13's key
                          "event_stale",
                          # E13: a flat book made 'closing' on the venue's own confirmed
                          # terminal state -- before `registered_no_increase` (keys[-12])
                          "venue_market_ended",
                          # E18 (PNL lane 6): the rest-life floor's keep, the IOC withheld on
                          # the quote re-read (a BUY's ask, a SELL's bid), the re-read refused
                          # by the budget or unreadable, the 059 probe failing -- before
                          # `registered_no_increase` (keys[-12])
                          "kept_min_life", "ask_moved", "bid_moved", "ioc_reread_capped",
                          "ioc_quote_unread", "order_cols_guard_unreadable",
                          # E17 (PNL lane 5): the standing row re-anchored / ambiguous / the
                          # re-anchor's write failed; the prior episode adopted / unreadable /
                          # no fill since the close / the venue's own settle; the fold
                          # (review HIGH-1): the mirror's own sub-share dust admitted --
                          # before `registered_no_increase` (keys[-12])
                          "standing_row_reanchored", "standing_row_ambiguous", "standing_row_reanchor_failed",
                          "adopted_prior_episode", "venue_dust_ours", "adopt_prior_unreadable",
                          "adopt_no_fill_since_close", "adopt_prior_venue_settled",
                          # E19 (PNL lane 8): a book opened on the smaller of two
                          # disagreeing readings of one sign -- before
                          # `registered_no_increase` (keys[-12])
                          "wrong_sign_hold",    # E20
                          # E14b (FILL lane 1): the same-tick rest of a long exit IOC's
                          # withheld or unfilled quantity -- before E19's key (keys[-13])
                          "exit_take_rested",
                          # E14 (FILL lane 2): an entry's one IOC sent at the band cent
                          # (the ask a cent above his, at first sight on a long book) --
                          # before E19's name and `registered_no_increase` (keys[-12]),
                          # after E20's and E14b's, which landed first (keys[-14])
                          "take_in_band",
                          # FILL lane 3: the exit's band IOC on a long book / the cover's on a
                          # short (both inert at the default band) and the fast gate's
                          # order-open split on his reducing fill -- before E19's name and
                          # `registered_no_increase` (keys[-12]), after E14's (keys[-16:-13])
                          "exit_take_in_band", "cover_in_band", "order_open_his_exit",
                          # T2 (FILL lane 4): the per-fill record's one INSERT failed or
                          # timed out (the rows kept for the next tick); the 060 table
                          # absent this tick (nothing queued) -- before E19's name and
                          # `registered_no_increase` (keys[-12]), after E14's (keys[-15:-13])
                          "fill_answer_write_failed", "fill_answers_absent",
                          # FILL lane 5: a flat book held open on the clock while he
                          # holds / while his sizes could not be read; a candidate
                          # refused on a turned market -- before E19's name (keys[-13])
                          # and `registered_no_increase` (keys[-12]), after E14's
                          # (keys[-16:-13])
                          "he_holds", "he_holds_unread", "reopen_refused",
                          "drift_smaller_open",
                          "registered_no_increase",
                          # E12: a book opened on his flow (the block never bought), one
                          # opened on his whole net (the block admitted), the 057 probe
                          # failing for any reason but absence -- before E9's four
                          "open_flow_only", "open_catchup", "flow_guard_unreadable",
                          # E7: the no_mark memo's skip and a candidate memo released
                          # by his fill, before E6's key (which the E6 pins hold at -2)
                          # E9: the wake fast path's four names, before E7's pair (whose
                          # pin holds the last four keys exactly)
                          "fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed",
                          "cand_no_mark_skipped", "cand_memo_released",
                          # E6: a quiet book's read skipped under the tick's budget
                          "book_quiet_skipped",
                          # D1: the terminal memo's skip, LAST
                          "cand_terminal_skipped")
    assert keys[-101] == "short_share_cap" and keys.count("books_unreadable") == 1    # E16's four, E18's six, E17's eight, E19's one, L7's one, E20's one, E14b's one, E14's one and FILL lane 3's three and T2's two and FILL lane 5's three before the tail
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
    assert ik[ik.index("ledger_dust") + 1] == "short_reduce_unproven" and ik[-8] == "short_share_cap"
    assert ik[-7:] == ("books_unreadable", "ratio_stepped", "under_min_notional",
                       "shadow_check_skipped",
                       # C1 round 2 (review D, (4)): three names, the block stays under 40
                       "map_source_unverified", "map_reads_capped", "side_echo_mismatch"), \
        "served on integ"
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


def _s4_unproven(p, why="wrong_price", at=NOW - 10.0):
    """The S4 read-back proof NOT passed: a mismatch recorded inside the
    hour (so no probe runs this tick either). Every cover is refused by
    name until the key reads proved."""
    p.state["mirror_s4_proof"] = {"proved": False, "at": at, "slug": SLUG, "why": why,
                                  "echo": {"price": 0.24, "quantity": 1.0, "intent": "ORDER_INTENT_SELL_SHORT"}}


def _s4_proved(p, at=NOW - 100.0):
    p.state["mirror_s4_proof"] = {"proved": True, "at": at, "slug": SLUG, "fixture": True}


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
    # E6: on target with nothing open and no fill of his inside HOT_S the
    # book is QUIET on the next tick: skipped by the rotation, its proof
    # carried on the skip's plan (recorded once: the tally does not move);
    # read again after the memo's clear (a deploy) and still recorded once
    st3 = _tick(p, _Venue(held={SLUG: -300}), now=NOW + 60, http=_short_http())
    assert _census(st3, "book_quiet_skipped") == 1 and p.state["short_side_proof"]["ok"] == 1
    assert b["last_plan"]["short_proof"] == "ok" and b["last_reason"] == "book_quiet_skipped"
    ml._quiet_memo.clear()
    st3 = _tick(p, _Venue(held={SLUG: -300}), now=NOW + 90, http=_short_http())
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


def test_a_partial_reduce_of_a_short_is_refused_by_name_until_the_read_back_proof_then_covered(monkeypatch):
    """S4: the partial short reduce (his partial buy-back) stays
    `short_reduce_unproven` while the read-back proof has not passed;
    once it has, it is the priced cover (section 22)."""
    _shorts_on(monkeypatch)
    # his net moved up from -300 to -100: 300 long against 400 other
    p = _short_world(fills=_his(300, other_size=400, other_px=0.72), snap={M: 300.0, N: 400.0})
    _s4_unproven(p)
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


def test_a_short_flattens_by_its_priced_cover_sole_or_co_held_never_close_position(monkeypatch):
    """S4: the paired flatten of a short is a BUY of the long token with
    the closing intent (SELL_SHORT on the wire) through _place -- here
    the IOC at the ceiling cent (his buy-back 0.31 + 0.01 = 0.32, the
    ask there) -- never close_position, and for OUR quantity: a
    co-holder beside us changes nothing (no sole reading, no refusal)."""
    _shorts_on(monkeypatch)
    # his net gone to zero while he still holds both tokens: the paired flatten
    p = _short_world(fills=_his(400, other_size=400, other_px=0.72), snap={M: 400.0, N: 400.0})
    b = _short_book(p, ledger=-300)
    v = _Venue(held={SLUG: -300}, ioc_fill=300.0)
    st = _tick(p, v, http=_mkt(400.0, 400.0))
    assert b["target"] == 0 and b["last_plan"]["kind"] == "flatten_paired"
    assert "close" not in _kinds(v) and "slug_bid" not in _kinds(v)
    assert [c[1:] for c in _places(v)] == [(SLUG, 0.32, 300, True, IOC_TIF, SHORT, False, None)]
    o = next(iter(p.orders.values()))
    assert (o["kind"], o["side"], o["tif"], o["intent"]) == ("take", BUY, "IOC", "ORDER_INTENT_SELL_SHORT")
    assert o["qty"] == 300 and o["state"] == "filled" and o["wire"] == 0.32
    # the cover filled 300 @ 0.32 against a 0.32 short: the leg covers to zero, realized 0
    assert b["ledger_net"] == 0 and b["realized_pnl"] == pytest.approx(0.0)
    assert p.rows[b["standing_row_id"]]["filled_shares"] == 0.0
    assert _census(st, "short_flatten_close") == 1
    assert _census(st, "short_cover_take") == 1 and _census(st, "exit_take") == 1
    assert _census(st, "overfill") == 0 and _census(st, "short_reduce_unproven") == 0
    lp = b["last_plan"]
    assert lp["exit_px"] == pytest.approx(0.31) and lp["exit_ceiling"] == pytest.approx(0.32)
    assert lp["exit_cover"] == 0.32 and lp["exit_rest"] == 0.31 and lp["exit_px_src"] == "his_fill"
    # flat at target 0 on a live market: the row stays 'filled' at 0 and
    # the episode waits its flat hour like any other
    assert p.rows[b["standing_row_id"]]["status"] == "filled" and b["last_plan"]["close"] == "not_due"

    # CO-HELD: someone else's half share beside our 300 (the walk sees
    # -300.5): the cover is a clamped order of OUR 300, so the stranger's
    # leg is never touched and nothing is refused (S4). A same-sign
    # co-hold the ledger cannot explain freezes the book
    # `venue_ledger_disagree` first, as it does a long book (R7)
    p2 = _short_world(fills=_his(400, other_size=400, other_px=0.72), snap={M: 400.0, N: 400.0})
    b2 = _short_book(p2, ledger=-300)
    v2 = _Venue(held={SLUG: -300.5}, ioc_fill=300.0)
    st2 = _tick(p2, v2, http=_mkt(400.0, 400.0))
    assert "close" not in _kinds(v2) and "slug_bid" not in _kinds(v2)
    assert [c[2:6] for c in _places(v2)] == [(0.32, 300, True, IOC_TIF)]
    assert _census(st2, "flatten_holding_disagrees") == 0 and _census(st2, "short_reduce_unproven") == 0
    assert b2["ledger_net"] == 0 and b2["state"] == "live" and _census(st2, "short_flatten_close") == 1
    p3 = _short_world(fills=_his(400, other_size=400, other_px=0.72), snap={M: 400.0, N: 400.0})
    b3 = _short_book(p3, ledger=-300)
    st3 = _tick(p3, _Venue(held={SLUG: -500}), http=_mkt(400.0, 400.0))
    assert b3["state"] == "live" and _census(st3, "venue_ledger_suspect") == 1     # E16: the first read
    st3 = _tick(p3, _Venue(held={SLUG: -500}), now=NOW + 15, http=_mkt(400.0, 400.0))
    assert b3["frozen_reason"] == "venue_ledger_disagree" and _census(st3, "wrong_sign_trip") == 0


def test_a_confirmed_vanish_on_a_short_covers_at_once_by_the_priced_ioc_with_no_rest_first(monkeypatch):
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
             _fill(N, "SELL", 400, 0.70, NOW - 2000), _fill(M, "SELL", 100, 0.31, NOW - 1000)]
    p = _short_world(fills=fills, snap=None)
    b = _short_book(p, ledger=-300)
    # his buy-back is 0.30 in long space (his SELL of the other at 0.70,
    # his newest): the cover's ceiling is 0.31 (E4), so the ask sits there
    # and the cover is ONE IOC at 0.31 this tick (S4), no rest first
    v = _Venue(ask=0.31, held={SLUG: -300}, ioc_fill=300.0)
    http = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0},
                       {"conditionId": CID, "asset": N, "size": 0}])
    st = _tick(p, v, http=http)
    # the vanish was confirmed on the token carrying his net, the OTHER one
    assert any(c[1].get("market") == CID for c in http.calls)
    assert _census(st, "flatten_vanished") == 1 and _census(st, "flatten_rested") == 0
    assert "close" not in _kinds(v) and [c[2:6] for c in _places(v)] == [(0.31, 300, True, IOC_TIF)]
    o = next(iter(p.orders.values()))
    assert o["kind"] == "take" and o["tif"] == "IOC" and o["intent"] == "ORDER_INTENT_SELL_SHORT"
    assert b["ledger_net"] == 0 and _census(st, "short_flatten_close") == 1 and _census(st, "short_cover_take") == 1
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
    assert b2["state"] == "live" and _census(st2, "wrong_sign_trip") == 0     # E16: a suspect first
    st2 = _tick(p2, _Venue(held={}), now=NOW + 15, http=_short_http())
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
    # E18 (migration 059, the fixture's database carries it): the 050
    # INSERT's nineteenth parameter stands, the send record after it
    assert ins4 and all(len(a) == 22 and a[18] == INTENT for a in ins4)
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
    # flattens by its priced cover (S4), the row through the 050 INSERT
    p2.no_intent_column = False
    monkeypatch.setattr(rules, "MIRROR_SHORTS", False)
    v3 = _Venue(held={SLUG: -300}, ioc_fill=300.0)
    st3 = _tick(p2, v3, now=NOW + 30, http=_short_http())
    assert "close" not in _kinds(v3) and [c[2:6] for c in _places(v3)] == [(0.32, 300, True, IOC_TIF)]
    assert b2["ledger_net"] == 0
    ins = [a for k, s, a in p2.sent if "ml-order-insert" in s]
    assert ins and all(len(a) == 22 and a[18] == "ORDER_INTENT_SELL_SHORT" for a in ins)    # E18: 059's three after
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
    long book the plan side decides the wire intent alone. The rest
    sits at his cent (0.31, E4) with the bid two cents under him, so
    nothing but the intent reading could move it."""
    # control: the same rest with the intent the 050 INSERT writes
    p0 = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    b0 = p0.add_book(ledger=300)
    p0.add_order(b0, side=SELL, wire=0.31, qty=200, kind="reduce")
    v0 = _Venue(bid=0.29, held={SLUG: 300})
    v0.rest("oid-1", "SELL", 0.31, 200)
    st0 = _tick(p0, v0)
    assert not _cancels(v0) and _census(st0, "open_order_pending") == 1
    # the pre-050 row after the ALTER's back-fill: kept, not replaced
    p = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    b = p.add_book(ledger=300)
    o = p.add_order(b, side=SELL, wire=0.31, qty=200, kind="reduce", intent="ORDER_INTENT_BUY_LONG")
    v = _Venue(bid=0.29, held={SLUG: 300})
    v.rest("oid-1", "SELL", 0.31, 200)
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
    short of 300: the venue nets -200. Before S4 close_position would
    have closed the NET (200 of ours covered, the desk's long netted
    away); since S4 the cover is an ordinary order of our own 300 and
    the sole question is never asked."""
    _shorts_on(monkeypatch)
    p = _short_world(fills=_his(400, other_size=400, other_px=0.72), snap={M: 400.0, N: 400.0})
    b = _short_book(p, ledger=-300)
    p.manual_shares[SLUG] = 100.0

    async def _held(t, slug):
        return 200, 0.31
    monkeypatch.setattr(ml, "_pm_held", _held)
    v = _Venue(held={SLUG: -200},
               close={"ok": True, "order_id": "close-1", "status": "filled", "fill_price": 0.29,
                      "filled_shares": 200.0, "raw": {}})
    st = _tick(p, v, http=_mkt(400.0, 400.0))
    assert b["target"] == 0 and b["last_plan"]["kind"] == "flatten_paired"
    # S4: the cover is a clamped order of OUR 300 (never the net, never the
    # desk's long): one IOC at the ceiling cent, close_position never called
    assert "close" not in _kinds(v) and [c[2:6] for c in _places(v)] == [(0.32, 300, True, IOC_TIF)]
    assert _census(st, "short_reduce_unproven") == 0 and _census(st, "short_cover_take") == 1
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
    # a foreign long of 800 beside our short of 300: venue +500, not our
    # inversion. E20 (2026-09-08, book 663): a sign disagreement that is
    # not the leg's freezes THIS book under `wrong_sign_hold` and never
    # trips the desk (before E20 it kept the trip and the freeze); the
    # shared tally is still never touched
    p2 = _short_world()
    b2 = _short_book(p2, ledger=-300)
    st2 = _tick(p2, _Venue(held={SLUG: 500}), http=_short_http())
    assert _census(st2, "wrong_sign_hold") == 1 and b2["frozen_reason"] == "wrong_sign_hold"
    assert _census(st2, "wrong_sign_trip") == 0 and "mirror_live_trip" not in p2.state
    assert p2.state.get("mirror_live") is not False and not _places(_Venue())
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


# (ad) a short REDUCE take is never sent while the read-back proof is unproven, even with the arm live
def test_a_short_reduce_take_is_refused_by_name_while_unproven_and_is_the_cover_ioc_once_proved(monkeypatch):
    _shorts_on(monkeypatch)
    # his net moved up from -300 to -100: the plan is a BUY_LONG cover of 200
    p = _short_world(fills=_his(300, other_size=400, other_px=0.72), snap={M: 300.0, N: 400.0})
    _s4_unproven(p)
    b = _short_book(p, ledger=-300, take_armed_ts=NOW - 130)
    # a locked book at the cover's wire (his long BUY 0.31 floors to the 0.30 bid): the take would fire
    v = _Venue(bid=0.30, ask=0.30, held={SLUG: -300}, ioc_fill=300.0)
    st = _tick(p, v, http=_mkt(300.0, 400.0))
    assert b["target"] == -100 and b["last_plan"]["side"] == BUY
    assert not [c for c in _places(v) if c[3] != 1] and "close" not in _kinds(v), _places(v)
    assert _census(st, "take_placed") == 0 and _census(st, "short_reduce_unproven") == 1
    assert b["ledger_net"] == -300 and not p.orders and b["last_plan"]["s4"] == {"unproven": "wrong_price"}
    # (a locked book is not a two-sided quote: no probe ran on it)
    assert _census(st, "s4_probe_placed") == 0
    # the proof passed (the probe on another book, or by hand): the cover IOC
    _s4_proved(p)
    v2 = _Venue(bid=0.30, ask=0.30, held={SLUG: -300}, ioc_fill=300.0)
    st2 = _tick(p, v2, now=NOW + 30, http=_mkt(300.0, 400.0))
    assert [c[2:6] for c in _places(v2)] == [(0.32, 200, True, IOC_TIF)] and _census(st2, "short_cover_take") == 1
    assert b["ledger_net"] == -100 and _census(st2, "short_reduce_unproven") == 0


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


# (af) a lost LEGACY close on a short book is sized off the LEG of the venue's negative position
def test_a_lost_close_on_a_short_reads_the_venues_negative_position_as_the_leg(monkeypatch):
    """_reconcile_lost_close stays for the CLOSE rows on file from before
    S4 (a short sends no close_position since): the row is seeded as the
    lost close left it -- 'placing', no id, tif CLOSE -- and walked by
    step O."""
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
             _fill(N, "SELL", 400, 0.70, NOW - 2000), _fill(M, "SELL", 100, 0.31, NOW - 1000)]
    gone = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0},
                       {"conditionId": CID, "asset": N, "size": 0}])
    p = _short_world(fills=fills, snap=None)
    b = _short_book(p, ledger=-300)
    row = p.add_order(b, side=BUY, wire=0.0, qty=300, kind="flatten_vanished", tif="CLOSE", order_id=None,
                      state="placing", placed_ts=NOW - 90, intent="ORDER_INTENT_SELL_SHORT")
    v = _Venue(ask=0.31, held={SLUG: -300})
    st = _tick(p, v, http=gone)
    assert "close" not in _kinds(v) and not _places(v)
    assert row["state"] == "placing" and b["frozen_reason"] == "placement_lost"
    assert _census(st, "placement_lost") == 1
    # nothing left the account: the venue still shows -300, the leg is 300, nothing sold
    v2 = _Venue(held={SLUG: -300})
    st2 = _tick(p, v2, now=NOW + 90, http=gone)
    assert "trades" not in _kinds(v2), "a leg read as -300 would look like 600 sold"
    assert row["state"] == "placing" and b["ledger_net"] == -300 and _census(st2, "book_error") == 0
    # the position went to 0: the close executed, 300 covered from the trade log (BUYs of the contract)

    async def _held0(t, slug):
        return 0, None
    monkeypatch.setattr(ml, "_pm_held", _held0)
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


# (au) a short's flatten replaces its resting BUY_SHORT add with the cover, in one tick
def test_a_confirmed_vanish_on_a_short_cancels_the_resting_add_then_covers(monkeypatch):
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
             _fill(N, "SELL", 400, 0.70, NOW - 2000), _fill(M, "SELL", 100, 0.31, NOW - 1000)]
    p = _short_world(fills=fills, snap=None)
    b = _short_book(p, ledger=-300)
    o = p.add_order(b, side=SELL, wire=0.32, qty=100, kind="increase")
    v = _Venue(ask=0.31, held={SLUG: -300}, ioc_fill=300.0)      # the ask inside the cover's ceiling (E4)
    v.rest("oid-1", "SELL", 0.32, 100)
    http = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0},
                       {"conditionId": CID, "asset": N, "size": 0}])
    st = _tick(p, v, http=http)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    assert p.orders[o["id"]]["state"] == "cancelled" and p.orders[o["id"]]["reason"] == "replace"
    assert "close" not in _kinds(v)
    assert [c[2:6] for c in _places(v)] == [(0.31, 300, True, IOC_TIF)], "the cover follows the cancel in one tick"
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
    # E6: the filled book is on target with nothing open -- quiet on the
    # next tick (skipped by the rotation); read again after the memo's
    # clear, on target at the cap as before
    st3 = _tick(p, _Venue(held={SLUG: -1}), now=NOW + 60, http=_short_http())
    assert _census(st3, "book_quiet_skipped") == 1 and _census(st3, "on_target") == 0
    ml._quiet_memo.clear()
    st3 = _tick(p, _Venue(held={SLUG: -1}), now=NOW + 90, http=_short_http())
    assert _census(st3, "on_target") == 1 and _census(st3, "short_share_cap") == 1
    # an open short book above the cap is clamped toward zero: the
    # partial cover it would take is short_reduce_unproven while the
    # read-back proof is unproven (S4), nothing sent
    p4 = _short_world()
    _s4_unproven(p4)
    b4 = _short_book(p4, ledger=-300)
    v4 = _Venue(held={SLUG: -300})
    st4 = _tick(p4, v4, http=_short_http())
    assert b4["target"] == -1 and _census(st4, "short_share_cap") == 1
    assert _census(st4, "short_reduce_unproven") == 1 and "close" not in _kinds(v4) and not _places(v4)
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
    alone; an open short book flattens by its priced cover (S4)."""
    monkeypatch.setattr(rules, "MIRROR_SHORTS", False)
    p = _short_world()
    b = _short_book(p, ledger=-300)
    v = _Venue(held={SLUG: -300}, ioc_fill=300.0)
    st = _tick(p, v, http=_short_http())
    assert _census(st, "short_side_refused") == 1 and b["target"] == 0
    assert "close" not in _kinds(v) and [c[2:6] for c in _places(v)] == [(0.32, 300, True, IOC_TIF)]
    assert b["ledger_net"] == 0 and _census(st, "short_flatten_close") == 1
    # co-held (a stranger's fraction beside ours): the cover of OUR 300, as any other
    p2 = _short_world()
    b2 = _short_book(p2, ledger=-300)
    v2 = _Venue(held={SLUG: -300.5}, ioc_fill=300.0)
    st2 = _tick(p2, v2, http=_short_http())
    assert _census(st2, "short_reduce_unproven") == 0 and b2["ledger_net"] == 0
    assert [c[2:6] for c in _places(v2)] == [(0.32, 300, True, IOC_TIF)] and "close" not in _kinds(v2)


def test_a_sign_flip_on_a_short_book_flattens_by_its_priced_cover_under_its_name(monkeypatch):
    """B8, owner default Q5 (a): his net crossed to the LONG side while
    our short book is open. The book flattens under the name sign_flip
    -- by its priced cover since S4 (his newest long BUY at 0.31: the
    ceiling 0.32, the ask there, one IOC) -- and, flat with nothing
    open and the venue read at 0, the flip IS the close (2026-09-06):
    the episode closes on the next tick's venue read and the long side
    opens as a NEW episode after it (test_mirror_short_sign_flip drives
    both directions through it)."""
    _shorts_on(monkeypatch)
    p = _pool()                                   # his net +300: the default fixture
    b = _short_book(p, ledger=-300)
    v = _Venue(held={SLUG: -300}, ioc_fill=300.0)
    st = _tick(p, v)
    assert _census(st, "sign_flip") == 1 and b["target"] == 0 and b["last_plan"]["sign_flip"] is True
    assert _census(st, "short_side_refused") == 0
    assert "close" not in _kinds(v) and [c[2:6] for c in _places(v)] == [(0.32, 300, True, IOC_TIF)]
    assert b["ledger_net"] == 0 and _census(st, "short_flatten_close") == 1
    # the venue was read at -300 before the cover: the close waits for
    # the venue's own 0 (next tick), never the fill report alone
    assert b["state"] == "live" and _census(st, "closed_cashed_out") == 0 and len(p.books) == 1
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
    untouched while the S4 read-back proof is unproven: a partial
    reduce is `short_reduce_unproven` and nothing is sent."""
    _rails_2026_09_06(monkeypatch)
    monkeypatch.setattr(rules, "MIRROR_SHORTS", True)
    monkeypatch.setattr(rules, "MIRROR_SHORT_MAX_SHARES", math.inf)
    while le._SHORT_LOCK.locked():
        le._SHORT_LOCK.release()
    assert le.short_model_confirmed() is True, "by construction (live_executor)"
    # E12: his other-token BUYs at 0.70 (0.30 on the axis, a cent from
    # the 0.31 mark) so the block he built before first sight is admitted
    # and the book opens on his whole net, as this pin was written; at
    # 0.72 (three cents) it opens on his flow alone (test_e12_flow_only)
    p = _pool(fills=_his(100, other_size=3100, other_px=0.70), snap={M: 100.0, N: 3100.0})
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
    # while the read-back proof is unproven (S4)
    p3 = _pool(fills=_his(100, other_size=1600, other_px=0.72), snap={M: 100.0, N: 1600.0})
    _s4_unproven(p3)
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
    src = _place_src()
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

    async def _ours(t, slug):
        return 2, 0.30                       # the venue holds our 2 and nobody else's
    monkeypatch.setattr(ml, "_pm_held", _ours)
    p = _pool(fills=_unpriced(), snap=None)  # an unpriced vanish: the slippage leg (E4)
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
    async def _held(t, slug):
        return 202, 0.30
    monkeypatch.setattr(ml, "_pm_held", _held)
    p3 = _pool(fills=_unpriced(), snap=None)
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
    src = _place_src()
    assert 'kind in ("flatten_paired", "flatten_vanished")' in src and "not flattening and" in src


def _settled_book(p, realized, settled, closed_ago=3600, **slug):
    """A book closed-settled `closed_ago` seconds before the fixture
    clock, opened 30 h ago so it is nobody's book-of-the-day: its
    updated_at is its closed_at, as ml-book-settled stamps both."""
    return p.add_book(ledger=0, state="closed", realized_pnl=realized, settled_pnl=settled,
                      closed_at=NOW - closed_ago, updated_ts=NOW - closed_ago,
                      opened_ts=NOW - 30 * 3600, **slug)


# the loss sum's one parameter (L1): the window's start, the full 24 h
# back from the fixture clock -- what the worker hands the statement
# when no re-arm stands
_WINDOW = (ml._utc(NOW - ml.LOSS_WINDOW_S),)


def test_e3_the_loss_sum_counts_a_settled_books_dollars_once():
    """E3 (the day reconciliation of 2026-09-06 23:10Z). _SQL_LOSS_SUM
    summed realized_pnl over every book updated in 24 h PLUS
    settled_pnl over every book closed-settled in 24 h, but settled_pnl
    is the venue's WHOLE-position figure -- what _close_settled
    cross-checks `own = realized + shares x (payout - avg)` against
    under book_settle_disagree -- so a settled book's realized part
    was counted twice. Tonight: book 16 (realized -244.75, settled
    -315.40 = sales 349.25 - cost 664.65 + 157 x 0), 3 (+2.48 /
    +156.17), 19 (-2.11 / -41.46), 22 (+14.64 / +19.74); the 22:22Z
    reading of -2,445 was about -2,215 in truth. The rule now: settled
    over the closed-settled books in the window, realized over every
    OTHER book updated in the window -- open, frozen and closing books,
    and closes cashed out / cancelled with settled_pnl NULL -- and a
    settlement outside the window brings nothing back in. The `books`
    count is as it was. Pinned by the statement's text and through the
    fake, which reads the exclusion from the text and refuses the
    double-counting shape; tests/test_mirror_loss_sum_real_pg executes
    the same rows against Postgres."""
    tonight = [(16, -244.75, -315.40), (3, 2.48, 156.17), (19, -2.11, -41.46), (22, 14.64, 19.74)]
    p = _pool()
    for bid, realized, settled in tonight:
        _settled_book(p, realized, settled, closed_ago=7200, us_market_slug=f"aec-set-{bid}-2026-09-06",
                      condition_id=f"0xset{bid}", long_asset=f"tok-s{bid}", other_asset=f"tok-t{bid}")
    p.add_book(ledger=300, realized_pnl=-100.0)                           # open: realized only
    # a cashed-out close (settled_pnl NULL): its P&L lives in realized_pnl
    p.add_book(ledger=0, state="closed", realized_pnl=-50.0, settled_pnl=None,
               closed_at=NOW - 1800, updated_ts=NOW - 1800, **_OTHER)
    # a settlement 25 h old is outside the window entirely: neither its
    # settled figure nor its realized part comes back through updated_at
    _settled_book(p, -999.0, -1500.0, closed_ago=25 * 3600, **_ZZ)
    row = p._run("fetchrow", ml._SQL_LOSS_SUM, _WINDOW)
    assert row["lost"] == pytest.approx(sum(s for _, _, s in tonight) - 100.0 - 50.0)
    assert row["lost"] == pytest.approx(-330.95)
    assert row["books"] == 6, "every book updated in 24 h, the 25 h settlement not among them"
    # the double count would have read the four books' realized part on
    # top: -229.74 more, the same gap as tonight's -2,445 vs -2,215
    assert row["lost"] - sum(r for _, r, _ in tonight) == pytest.approx(-101.21)
    # a closed-settled row with NO closed_at (only a hand-edited row: both
    # close statements stamp it) is clocked by updated_at and counted
    # ONCE, by its settled figure -- it vanished from both sums before
    # (E3 review, minor 2)
    nul = p.add_book(ledger=0, state="closed", realized_pnl=-7.0, settled_pnl=-20.0,
                     closed_at=None, updated_ts=NOW - 900, opened_ts=NOW - 30 * 3600,
                     us_market_slug="aec-set-null-2026-09-06", condition_id="0xsetnull",
                     long_asset="tok-sn", other_asset="tok-tn")
    row2 = p._run("fetchrow", ml._SQL_LOSS_SUM, _WINDOW)
    assert row2["lost"] == pytest.approx(-350.95) and row2["books"] == 7
    nul["realized_pnl"] = -1e6                     # its realized part never leaks in
    assert p._run("fetchrow", ml._SQL_LOSS_SUM, _WINDOW)["lost"] == pytest.approx(-350.95)
    nul["updated_ts"] = NOW - 25 * 3600            # and out of the window it is nothing
    assert p._run("fetchrow", ml._SQL_LOSS_SUM, _WINDOW)["lost"] == pytest.approx(-330.95)
    # the statement's text: settled over closed-settled-in-window (clocked
    # by closed_at, else updated_at), realized over updated-in-window
    # EXCLUDING closed-settled, one window, the tag -- and the ARITHMETIC
    # between them: the two sums ADDED, each unsigned and unscaled, the
    # sum the `lost` column. The fake computes the rule itself, so a
    # mutant that flips a sign or halves the figure passes through it
    # unseen; only the text catches it here (E3 review, minor 1)
    sql = _flat(ml._SQL_LOSS_SUM)
    # L1: the window's start is the statement's one parameter, in all
    # three places; the text carries no interval and no now() of its own
    assert "ml-loss-sum" in sql and sql.count("> $1::timestamptz") == 3
    assert "interval" not in sql and "now()" not in sql
    assert ("(SELECT sum(settled_pnl) FROM mirror_books WHERE state = 'closed' AND settled_pnl IS NOT NULL "
            "AND COALESCE(closed_at, updated_at) > $1::timestamptz)") in sql
    assert ("(SELECT sum(realized_pnl) FROM mirror_books WHERE updated_at > $1::timestamptz "
            "AND NOT (state = 'closed' AND settled_pnl IS NOT NULL))") in sql
    assert "(SELECT count(*) FROM mirror_books WHERE updated_at > $1::timestamptz) AS books" in sql
    assert sql.startswith("SELECT COALESCE((SELECT sum(settled_pnl)")
    assert ")::float8 + COALESCE((SELECT sum(realized_pnl)" in sql
    assert "), 0)::float8 AS lost," in sql
    # no other sign or factor anywhere: `count(*)` and the tag are the
    # only '-' and '*' the text may hold (the window's `now() - interval`
    # left the text with L1: the start is the parameter)
    bare = sql.replace("count(*)", "").replace("/* ml-loss-sum */", "")
    assert sql.count("COALESCE((SELECT sum(") == 2 and "-" not in bare and "*" not in bare, bare
    # the fake refuses the shape the reconciliation found rather than
    # modelling it: a test against a double-counting statement fails here
    double = ml._SQL_LOSS_SUM.replace("AND NOT (state = 'closed' AND settled_pnl IS NOT NULL)", "")
    with pytest.raises(AssertionError, match="a shape this fake does not model"):
        p._run("fetchrow", double, _WINDOW)


def test_e3_the_stop_trips_on_the_once_counted_sum_and_not_on_the_double_count():
    """The same rule through the tick. A settlement of -0.8 x stop with
    a realized part of -0.4 x stop read -1.2 x stop under the double
    count and tripped; in truth it is -0.8 x stop and the candidate
    still opens. With a cashed-out close of -0.3 x stop beside it the
    truth is -1.1 x stop: the stop trips, its receipt carries the
    once-counted sum and the 24 h book count, and nothing is placed."""
    stop = float(rules.MIRROR_LOSS_STOP_USD)
    p = _pool()
    _settled_book(p, -0.4 * stop, -0.8 * stop, **_ZZ)
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "mirror_loss_stop") == 0 and "mirror_loss_stop" not in p.state
    assert [c[1] for c in _places(v)] == [SLUG], "the truth is under the stop: the candidate opens"
    p2 = _pool()
    _settled_book(p2, -0.4 * stop, -0.8 * stop, **_ZZ)
    p2.add_book(ledger=0, state="closed", realized_pnl=-0.3 * stop, settled_pnl=None,
                closed_at=NOW - 1800, updated_ts=NOW - 1800, opened_ts=NOW - 30 * 3600, **_OTHER)
    v2 = _Venue()
    st2 = _tick(p2, v2)
    assert _census(st2, "mirror_loss_stop") >= 1 and not _places(v2)
    receipt = p2.state["mirror_loss_stop"]
    assert receipt["sum"] == pytest.approx(-1.1 * stop) and receipt["books"] == 2
    assert receipt["limit"] == stop


# ------------------------------------------ 11d. the mapping lane (C1)

def test_the_candidate_names_the_mapping_lanes_four_events(monkeypatch):
    """C1: the shadow's mapper is called with the tick's venue budget and
    what it reports is counted by name -- its venue reads and cache
    answers as events, a source the live worker has not certified
    (`grammar`) refused before admission and TTL-skipped, a candidate
    past the mapping budget refused with NO verdict and never
    TTL-skipped. Driven through _tick_candidate with the mapper faked
    (tests/test_mirror_maps_the_copy_lane.py drives the mapper itself)."""
    seen = []

    async def _grammar(pool, fills, pmus=None, **kw):
        seen.append(kw)
        kw["out"]["venue_reads"] = 2
        kw["out"]["cache_hit"] = 1
        return {"us_slug": SLUG, "long_asset": M, "other_asset": N, "source": "grammar"}

    monkeypatch.setattr(ms, "map_market", _grammar)
    monkeypatch.setattr(ml, "_unmapped_until", {})
    ml._current_stats = ml._new_stats()
    t = ml._Tick(pool=_pool(), pmus=_Venue(), http=_Http(), now=NOW, stats=ml._current_stats)
    _run(ml._tick_candidate(t, "rn1", CID))
    c = ml._current_stats["census"]
    # round 2: a grammar map goes to the class's own certification, which
    # this map (no side facts) cannot pass -- refused by that name
    assert c["grammar_echo_unverified"] == 1 and c["map_venue_read"] == 2 and c["map_cache_hit"] == 1
    assert c["map_source_unverified"] == 0
    assert ("rn1", CID) in ml._unmapped_until
    assert seen and seen[0]["budget"] is t.map_budget and seen[0]["whale"] == "rn1"
    assert seen[0]["condition_id"] == CID and isinstance(t.map_budget, ms.MapBudget)

    async def _unknown(pool, fills, pmus=None, **kw):
        return {"us_slug": SLUG, "long_asset": M, "other_asset": N, "source": "fuzzy"}

    monkeypatch.setattr(ms, "map_market", _unknown)
    monkeypatch.setattr(ml, "_unmapped_until", {})
    ml._current_stats = ml._new_stats()
    t = ml._Tick(pool=_pool(), pmus=_Venue(), http=_Http(), now=NOW, stats=ml._current_stats)
    _run(ml._tick_candidate(t, "rn1", CID))
    assert ml._current_stats["census"]["map_source_unverified"] == 1
    assert ("rn1", CID) in ml._unmapped_until

    async def _capped(pool, fills, pmus=None, **kw):
        kw["out"]["refusal"] = "map_reads_capped"
        return None

    monkeypatch.setattr(ms, "map_market", _capped)
    monkeypatch.setattr(ml, "_unmapped_until", {})
    ml._current_stats = ml._new_stats()
    t = ml._Tick(pool=_pool(), pmus=_Venue(), http=_Http(), now=NOW, stats=ml._current_stats)
    _run(ml._tick_candidate(t, "rn1", CID))
    assert ml._current_stats["census"]["map_reads_capped"] == 1
    assert ml._unmapped_until == {}, "no verdict: read again next tick"
    ml._current_stats = None


def test_the_grammar_class_names_its_certification(monkeypatch):
    """C1 round 2: every gate of the grammar class's own certification is
    a census name (the venue-truth check unreadable / unverified / ok, the
    trip, the probation, the first-fill mismatch); the mapper itself is
    driven in tests/test_mirror_maps_the_copy_lane.py."""
    g = {"his_slug": "cfb-bayl-aubrn-2026-09-05", "side_index": 0, "outcome_desc": "Bears",
         "intent": INTENT, "slug": "aec-cfb-bayl-aubrn-2026-09-05", "asset": M}
    aec = {"slug": g["slug"], "closed": False, "question": "Baylor vs. Auburn",
           "marketSides": [{"identifier": g["slug"], "description": "Bears", "long": True},
                           {"identifier": g["slug"], "description": "Tigers", "long": False}]}
    con = {"slug": "atc-cfb-bayl-aubrn-2026-09-05-bayl", "outcome": "Bears", "title": "Bears"}

    async def _grammar(pool, fills, pmus=None, **kw):
        kw["out"]["grammar"] = dict(g)
        return {"us_slug": g["slug"], "long_asset": M, "other_asset": N, "source": "grammar"}

    monkeypatch.setattr(ms, "map_market", _grammar)
    ml._current_stats = ml._new_stats()
    p = _pool()

    def _cand():
        monkeypatch.setattr(ml, "_unmapped_until", {})
        t = ml._Tick(pool=p, pmus=_Venue(), http=_Http(), now=NOW, stats=ml._current_stats)
        _run(ml._tick_candidate(t, "rn1", CID))
        return t

    c = ml._current_stats["census"]
    # unreadable state: refused by name, TTL-skipped
    p.raise_on.append(("SELECT value FROM ingestion_state", RuntimeError("db down")))
    _cand()
    assert c["grammar_echo_unreadable"] == 1 and ("rn1", CID) in ml._unmapped_until
    p.raise_on.clear()
    # no contract listed: unverified
    monkeypatch.setattr(ml, "_market_read", lambda pmus, slug: aec if slug == g["slug"] else None)
    _cand()
    assert c["grammar_echo_unverified"] == 1
    # the contract names the side: ok, pending recorded, the candidate
    # goes on to admission
    monkeypatch.setattr(ml, "_market_read", lambda pmus, slug: aec if slug == g["slug"] else con)
    t = _cand()
    assert c["grammar_echo_ok"] == 1
    assert p.state["mirror_grammar_echo"]["pending"][g["slug"]]["outcome_desc"] == "Bears"
    # a grammar book awaiting its first fill: the next one waits, and is
    # never TTL-skipped for it
    b = dict(p.add_book(ledger=0, map_source="grammar", us_market_slug=g["slug"]))
    _cand()
    assert c["grammar_probation"] == 1 and ml._unmapped_until == {}
    # its first fill echoes the other side: frozen, the class tripped
    b["ledger_net"] = 40
    monkeypatch.setattr(ml, "_position_echo", lambda pmus, slug: ({"net": 40.0, "outcome": "Tigers"}, 1))
    assert _run(ml._grammar_fill_check(t, b)) == "frozen"
    assert b["state"] == "frozen" and b["frozen_reason"] == "side_echo_mismatch"
    _cand()
    assert c["grammar_tripped"] == 1 and c["side_echo_mismatch"] == 1
    for k in ("grammar_echo_unreadable", "grammar_echo_unverified", "grammar_echo_ok",
              "grammar_probation", "side_echo_mismatch", "grammar_tripped"):
        assert c[k] >= 1, k
    for k in ("map_source_unverified", "map_reads_capped", "side_echo_mismatch"):
        assert k in ml._INTEG_CENSUS_KEYS
    assert len(ml._integ_block(ml._new_stats())) < 40
    ml._current_stats = None


def test_the_soccer_floor_is_lifted_for_a_mirror_book_and_counted(monkeypatch):
    """The copy lane's soccer/esports price floor does not bind the mirror
    (owner order 2026-09-06: everything he takes): lifted by name, counted."""
    c = ml._current_stats["census"] if ml._current_stats else None
    ml._current_stats = ml._current_stats or {"census": {}}
    c = ml._current_stats["census"]
    monkeypatch.setattr(copy_sports, "copy_verdict", lambda *a, **k: "soccer_price_floor")
    assert ml._mirror_cell("rn1", "sea-juv-mil-2026-09-06-juv", 0.2) is None
    assert c.get("soccer_floor_lifted") == 1
    monkeypatch.setattr(copy_sports, "copy_verdict", lambda *a, **k: "sport_halted")
    assert ml._mirror_cell("rn1", "sea-juv-mil-2026-09-06-juv", 0.2) == "sport_halted"
    ml._current_stats = None


# ------------------------------------ 19. the cap is PER GAME (E1, 2026-09-06)
#
# Owner, ~14:00Z: "Just trade 10% of what he puts on everything he takes
# (with a hard cap of no single event having more than $2.5k on it) this
# limitation should never force us to decline any of the possible
# copies"; 22:3xZ: "I just want to make sure the per game cap is at 2500
# per game (never more)". Two books of the fixture GAME: A on another
# market of it (its own tokens, its own fills, on target at its ratio so
# it holds still), B on the fixture market. The venue quotes 0.49/0.51
# on every slug, so the mark is 0.50 everywhere.

GAME_SLUG_A = "tsc-atp-branak-alemic-2026-09-02-o22pt5"     # the same game as SLUG
OTHER_GAME_SLUG = "aec-atp-branak-alemic-2026-09-03"         # another date: another game
CID_A = "0xgame-a"
LA, OA = "tokLA", "tokOA"


def _game_world(monkeypatch, his_b=30000.0, b_ledger=0, b_ratio=0.10, a_ledger=3600, a_avg=0.50,
                a_net=None, a_ratio=0.5, a_slug=GAME_SLUG_A, a_short=False, b_first=False, **a_over):
    """(pool, book A, book B, venue, http). A holds `a_ledger` at
    `a_avg` and his net on A's market is `a_net` (default: the ledger
    over A's ratio, so A is on target and places nothing; the ratio is
    0.5, not 1.0, so the small-bet step never re-rates it); B holds
    `b_ledger` against his `his_b` shares of M at 0.50. A gets the
    lower id (walked first) unless `b_first`."""
    _rails_2026_09_06(monkeypatch)
    a_net = float(a_ledger) / a_ratio if a_net is None else float(a_net)
    fills = _his(his_b, long_px=0.50)
    if a_short:
        _shorts_on(monkeypatch)
        fills += [_fill(LA, "BUY", 100.0, 0.28, NOW - 2500), _fill(OA, "BUY", 100.0 - a_net, 0.72, NOW - 2400)]
        snap = {M: his_b, N: 0.0, LA: 100.0, OA: 100.0 - a_net}
    else:
        fills.append(_fill(LA, "BUY", a_net, 0.50, NOW - 2500))
        snap = {M: his_b, N: 0.0, LA: a_net, OA: 0.0}
    p = _pool(fills=fills, snap=snap)
    p.markets[CID_A] = {"closed": False, "resolved": False, "resolved_prices": None}
    p.token_index.update({LA: 1, OA: 0})
    p.token_cid.update({LA: CID_A, OA: CID_A})
    over = {"condition_id": CID_A, "us_market_slug": a_slug, "long_asset": LA, "other_asset": OA,
            "game_key": le._us_game_key(a_slug), **a_over}
    b = p.add_book(ledger=b_ledger, ratio=b_ratio, avg_cost=0.50) if b_first else None
    if a_short:
        a = _short_book(p, ledger=a_ledger, avg=a_avg, ratio=a_ratio, **over)
        p.rows[a["standing_row_id"]].update(asset=OA, us_market_slug=a_slug, condition_id=CID_A)
    else:
        a = p.add_book(ledger=a_ledger, ratio=a_ratio, avg_cost=a_avg, **over)
    if b is None:
        b = p.add_book(ledger=b_ledger, ratio=b_ratio, avg_cost=0.50)
    rows = [{"conditionId": CID, "asset": M, "size": his_b}, {"conditionId": CID, "asset": N, "size": 0},
            {"conditionId": CID_A, "asset": LA, "size": snap[LA]},
            {"conditionId": CID_A, "asset": OA, "size": snap[OA]}]
    v = _Venue(bid=0.49, ask=0.51, held={a_slug: a_ledger, SLUG: b_ledger})
    return p, a, b, v, _Http(rows=rows)


def test_the_cap_is_per_game_a_second_market_is_scaled_to_what_the_game_has_left(monkeypatch):
    """(a) A holds 3,600 @ 0.50 = $1,800 at cost on the total line; B's
    10% target on the moneyline is 3,000 sh = $1,500 at the mark. The
    game has $700 left, so B is sized at $700 / 0.50 = 1,400 sh --
    scaled, never refused -- named `game_cap_scaled`, the plan carrying
    the room and the exposure it read; A (walked first, id order) read
    the whole $2,500 with B flat. The shadow, at the same net, is not
    a disagreement: the check compares at the cap B was sized at."""
    p, a, b, v, http = _game_world(monkeypatch)
    p.shadow.append(_shadow_row(3000, 0.10, 30000.0))
    st = _tick(p, v, http=http)
    assert a["target"] == 3600 and a["last_plan"]["game_room"] == 2500.0
    assert a["last_plan"]["game_exposure"] == 0.0 and "game_cap" not in a["last_plan"]
    assert b["target"] == 1400 and b["last_plan"]["game_cap"] == "game_cap_scaled"
    assert b["last_plan"]["game_room"] == 700.0 and b["last_plan"]["game_exposure"] == 1800.0
    assert b["last_plan"]["target_raw"] == 3000.0, "E5: the plan keeps the unclamped arithmetic"
    pl = _places(v)
    assert len(pl) == 1 and pl[0][1:5] == (SLUG, 0.49, 1400, False), pl
    assert _census(st, "game_cap_scaled") == 1 and _census(st, "game_cap_full") == 0
    assert _census(st, "shadow_live_disagree") == 0 and _census(st, "shadow_check_skipped") == 0
    assert "game_cap_scaled" in ml.CENSUS_KEYS and "game_cap_full" in ml.CENSUS_KEYS
    assert ml.CENSUS_KEYS[-1] == "cand_terminal_skipped"


def test_a_short_books_collateral_counts_against_its_game(monkeypatch):
    """(b) A is SHORT 2,500 of a 0.72 contract: $0.28 a share of
    collateral, $700 at cost. B's 10% of 40,000 is 4,000 sh = $2,000 at
    the mark; the game has $1,800 left: 3,600 sh."""
    p, a, b, v, http = _game_world(monkeypatch, his_b=40000.0, a_ledger=-2500, a_avg=0.72, a_short=True)
    st = _tick(p, v, http=http)
    assert a["target"] == -2500 and a["ledger_net"] == -2500, "A holds still"
    assert b["last_plan"]["game_exposure"] == pytest.approx(700.0)
    assert b["last_plan"]["game_room"] == pytest.approx(1800.0) and b["target"] == 3600
    assert _places(v)[-1][1:5] == (SLUG, 0.49, 3600, False)
    assert _census(st, "game_cap_scaled") == 1
    # the reading itself: a short's exposure is its collateral
    assert rules.book_exposure(-2500, 0.72, 0.50, SHORT) == pytest.approx(700.0)
    assert rules.book_exposure(-2500, 0.72, 0.50, INTENT) == pytest.approx(700.0), "a negative ledger IS the short leg"


def test_no_room_holds_the_book_at_what_it_has_and_never_sells(monkeypatch):
    """(c) A holds 5,000 @ 0.50 = the whole $2,500. B holds 200 and his
    net says 4,000: the target is 200 -- the increase is 0, named
    `game_cap_full` -- not 0 and not a reduce; nothing is placed."""
    p, a, b, v, http = _game_world(monkeypatch, his_b=40000.0, b_ledger=200, a_ledger=5000)
    st = _tick(p, v, http=http)
    assert b["target"] == 200 and b["last_plan"]["game_cap"] == "game_cap_full"
    assert b["last_plan"]["game_room"] == 0.0 and b["last_plan"]["game_exposure"] == 2500.0
    assert b["last_plan"]["target_raw"] == 4000.0
    assert not _places(v) and b["last_plan"]["reason"] == "on target"
    assert _census(st, "game_cap_full") == 1 and _census(st, "game_cap_scaled") == 0
    assert _census(st, "shadow_live_disagree") == 0


def test_a_reduce_on_a_game_over_the_cap_still_runs(monkeypatch):
    """(d) A holds the whole $2,500; B holds 500 and his net says 300.
    The cap is not a reason to sell, and not a reason to keep either:
    the reduce of 200 rests as it always did, unnamed."""
    p, a, b, v, http = _game_world(monkeypatch, his_b=3000.0, b_ledger=500, a_ledger=5000)
    st = _tick(p, v, http=http)
    assert b["target"] == 300 and "game_cap" not in b["last_plan"]
    assert b["last_plan"]["game_room"] == 0.0
    pl = _places(v)
    assert len(pl) == 1 and pl[0][1] == SLUG and pl[0][3] == 200 and pl[0][4] is True, pl
    assert _census(st, "game_cap_full") == 0 and _census(st, "game_cap_scaled") == 0
    # and a flatten: his net gone to 0 on B with the game full
    p2, a2, b2, v2, http2 = _game_world(monkeypatch, his_b=0.0, b_ledger=500, a_ledger=5000)
    _tick(p2, v2, http=http2)
    assert b2["target"] == 0 and "game_cap" not in b2["last_plan"]
    assert any(c[1] == SLUG and c[3] == 500 and c[4] is True for c in _places(v2))


def test_a_resting_buy_counts_its_unfilled_notional_against_the_game(monkeypatch):
    """(e) A is flat with a 3,600-share BUY resting at 0.49 that its
    plan keeps: $1,764 that can fill. B, walked FIRST (A not yet sized
    this tick), reads it off the rest: $736 of room, 1,472 sh -- and A,
    walked next, reads B's $736 and is itself re-sized to the $1,764
    left, 3,528 sh (the game never over $2,500 at the mark). Walked
    after A, B reads A's sized figure instead -- the larger of the rest
    and A's increase at the mark, $1,800 -- and gets 1,400."""
    p, a, b, v, http = _game_world(monkeypatch, a_ledger=0, a_net=7200.0, b_first=True)
    p.add_order(a, side=BUY, wire=0.49, qty=3600, order_id="oid-a", us_market_slug=GAME_SLUG_A,
                his_level=0.50)
    v.rest("oid-a", "BUY", 0.49, 3600, slug=GAME_SLUG_A)
    st = _tick(p, v, http=http)
    assert b["last_plan"]["game_exposure"] == 1764.0 and b["last_plan"]["game_room"] == 736.0
    assert b["target"] == 1472
    assert a["target"] == 3528 and a["last_plan"]["game_room"] == 1764.0
    assert _census(st, "game_cap_scaled") == 2
    # a partial fill counts its booked part at cost and only the remainder
    # as resting; a resting reduce counts nothing (test (d) rests one)
    p2, a2, b2, v2, http2 = _game_world(monkeypatch, a_ledger=1600, a_avg=0.49, a_net=7200.0, b_first=True)
    p2.add_order(a2, side=BUY, wire=0.49, qty=3600, order_id="oid-a", us_market_slug=GAME_SLUG_A,
                 booked=1600.0, his_level=0.50)
    v2.rest("oid-a", "BUY", 0.49, 3600, slug=GAME_SLUG_A, filled=1600.0, avg=0.49)
    _tick(p2, v2, http=http2)
    assert b2["last_plan"]["game_exposure"] == pytest.approx(1764.0), "1,600 held + 2,000 resting, at 0.49"
    # A, walked after B, reads B's $736 and is re-sized to what the
    # $1,764 left admits AT COST (MEDIUM-1): $784 held at 0.49, so $980
    # more at the 0.50 mark = 1,960 sh -> 3,560, the game at $2,500
    # exactly (the at-the-mark reading gave 3,528: A's 1,600 valued at
    # the mark, not at its cost)
    assert b2["target"] == 1472 and a2["target"] == 3560 and a2["last_plan"]["game_room"] == 1764.0
    assert 784.0 + (3560 - 1600) * 0.50 == pytest.approx(1764.0)
    # A walked first: its sized figure, the increase at the mark
    p3, a3, b3, v3, http3 = _game_world(monkeypatch, a_ledger=0, a_net=7200.0)
    p3.add_order(a3, side=BUY, wire=0.49, qty=3600, order_id="oid-a", us_market_slug=GAME_SLUG_A,
                 his_level=0.50)
    v3.rest("oid-a", "BUY", 0.49, 3600, slug=GAME_SLUG_A)
    _tick(p3, v3, http=http3)
    assert a3["target"] == 3600 and v3.orders["oid-a"]["state"] == "new"
    assert b3["last_plan"]["game_exposure"] == 1800.0 and b3["target"] == 1400


def test_two_games_are_independent_and_a_book_without_a_game_key_is_its_own_game(monkeypatch):
    """(f) A's $2,500 on ANOTHER game leaves B the whole cap; (h) a
    book with no game_key is its own game and counts for nothing
    against the fixture game."""
    p, a, b, v, http = _game_world(monkeypatch, a_ledger=5000, a_slug=OTHER_GAME_SLUG)
    assert a["game_key"] != b["game_key"]
    st = _tick(p, v, http=http)
    assert b["target"] == 3000 and b["last_plan"]["game_room"] == 2500.0
    assert b["last_plan"]["game_exposure"] == 0.0 and _census(st, "game_cap_scaled") == 0
    p2, a2, b2, v2, http2 = _game_world(monkeypatch, a_ledger=5000, game_key=None)
    assert a2["game_key"] is None
    st2 = _tick(p2, v2, http=http2)
    assert b2["target"] == 3000 and b2["last_plan"]["game_room"] == 2500.0
    assert _census(st2, "game_cap_scaled") == 0
    assert ml._game_key_of(a2) == ("book", str(a2["id"])) and ml._game_key_of(b2) == ("game", GAME_KEY)


def test_the_cap_is_per_event_two_whales_books_of_one_game_share_it(monkeypatch):
    """Owner: "no single EVENT having more than $2.5k on it" -- the game
    is the key, not (whale, game). A is ANOTHER whale's book on the
    total line, 3,600 @ 0.50 = $1,800 at cost and on target; B (rn1)
    on the moneyline gets the $700 the game has left, 1,400 sh."""
    p, a, b, v, http = _game_world(monkeypatch, whale="rn2")
    p.whale_address["rn2"] = "0xdef"
    assert a["whale"] == "rn2" and b["whale"] == "rn1"
    assert ml._game_key_of(a) == ml._game_key_of(b) == ("game", GAME_KEY)
    st = _tick(p, v, http=http)
    assert a["target"] == 3600 and a["ledger_net"] == 3600, "A holds still"
    assert b["target"] == 1400 and b["last_plan"]["game_cap"] == "game_cap_scaled"
    assert b["last_plan"]["game_exposure"] == 1800.0 and b["last_plan"]["game_room"] == 700.0
    pl = _places(v)
    assert len(pl) == 1 and pl[0][1:5] == (SLUG, 0.49, 1400, False), pl
    assert _census(st, "game_cap_scaled") == 1


def test_the_walk_order_is_games_oldest_touched_first_and_book_id_within_a_game(monkeypatch):
    """(g) WITHIN a game the order is book id ascending, whatever
    updated_at says: both books want an increase, A (the lower id) is
    sized first and its new target counts against B -- not just what A
    holds -- so A takes $1,500 and B gets the $1,000 left, 2,000 sh. A
    wake on B's market does not reorder the game's books; nor does A's
    newer updated_at. ACROSS games the order is the oldest updated_at
    among a game's books (the abandon round-robin the pre-E1 walk had
    from `ORDER BY updated_at, id`), a NULL last, ties by lowest id; a
    wake brings its whole game to the front, in that order."""
    for wake in (False, True):
        p, a, b, v, http = _game_world(monkeypatch, a_ledger=0, a_net=30000.0, a_ratio=0.10)
        a["updated_ts"] = NOW - 1        # A touched LAST: updated_at order would walk B first
        if wake:
            ml.notify(CID)
        st = _tick(p, v, http=http)
        assert a["target"] == 3000 and a["last_plan"]["game_room"] == 2500.0
        assert b["target"] == 2000 and b["last_plan"]["game_exposure"] == 1500.0, wake
        assert b["last_plan"]["game_room"] == 1000.0 and _census(st, "game_cap_scaled") == 1
        assert [c[3] for c in _places(v)] == [3000, 2000]
    rows = [{"id": 3, "condition_id": "x", "game_key": "g", "updated_ts": 100.0},
            {"id": 1, "condition_id": "y", "game_key": "h", "updated_ts": 200.0},
            {"id": 2, "condition_id": "z", "game_key": "g", "updated_ts": 300.0}]
    assert [r["id"] for r in ml._woken_first(rows, [])] == [2, 3, 1], "g's oldest book is older than h's"
    assert [r["id"] for r in ml._woken_first(rows, ["y"])] == [1, 2, 3], "the woken GAME first"
    assert [r["id"] for r in ml._woken_first(rows, ["x"])] == [2, 3, 1], "a woken game keeps id order inside"
    for r in rows:
        r["updated_ts"] = None
    assert [r["id"] for r in ml._woken_first(rows, [])] == [1, 2, 3], "no timestamps: the lowest id"
    rows[1]["updated_ts"] = 5.0
    assert [r["id"] for r in ml._woken_first(rows, [])] == [1, 2, 3], "a NULL updated_at sorts last"
    rows[0]["updated_ts"] = 1.0
    assert [r["id"] for r in ml._woken_first(rows, [])] == [2, 3, 1], "one dated book dates its game"


class _SlugDownVenue(_Venue):
    """The venue with ONE slug's quote read failing."""

    def __init__(self, down, **kw):
        super().__init__(**kw)
        self.down = down

    def bbo_read(self, client, slug):
        if slug == self.down:
            self.calls.append(("bbo", slug))
            return {"bid": None, "ask": None, "state": None, "error": "RuntimeError"}
        return super().bbo_read(client, slug)


def test_an_abandoned_ticks_unreached_games_are_walked_first_on_the_next(monkeypatch):
    """Three games, one book each, all touched at the same time. The
    tick abandons at the second book's quote read (`no_quote`; no plan
    written for it): the first book's plan bumped its updated_at, the
    second and third were left. The next tick walks the two unreached
    games first, then the one that was read -- the round-robin the
    id-only walk of the first cut had dropped."""
    monkeypatch.setattr(ms, "MISS_STREAK_ABANDON", 1)
    # the exact read list is the SEQUENTIAL walk's (E2's parallel walk
    # has the third book in flight when the second abandons; a book in
    # flight writes no plan either, so the round-robin below holds at
    # any concurrency -- section 20 pins that)
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 1)
    p = _pool()
    p.add_book(ledger=0)
    p.add_book(ledger=0, game_key=le._us_game_key(_OTHER["us_market_slug"]), **_OTHER)
    p.add_book(ledger=0, game_key=le._us_game_key(_ZZ["us_market_slug"]), **_ZZ)
    p.markets["0xother"] = dict(_LIVE)
    p.markets["0xzz"] = dict(_LIVE)
    assert len({ml._game_key_of(b) for b in p.books.values()}) == 3
    assert len({b["updated_ts"] for b in p.books.values()}) == 1, "all touched at once"
    v = _SlugDownVenue(_OTHER["us_market_slug"])
    st = _tick(p, v)
    assert st["abandoned"] and st["abandon_reason"] == "no_quote"
    assert [c[1] for c in v.calls if c[0] == "bbo"] == [SLUG, _OTHER["us_market_slug"]], "abandoned at book 2"
    ts = {b["us_market_slug"]: b["updated_ts"] for b in p.books.values()}
    assert ts[SLUG] > ts[_OTHER["us_market_slug"]] == ts[_ZZ["us_market_slug"]], "only book 1's plan was written"
    v2 = _Venue()
    st2 = _tick(p, v2, now=NOW + 30)
    assert not st2["abandoned"]
    assert [c[1] for c in v2.calls if c[0] == "bbo"] == [_OTHER["us_market_slug"], _ZZ["us_market_slug"], SLUG]


def test_a_candidate_on_a_full_game_opens_nothing_and_one_on_a_part_full_game_opens_scaled(monkeypatch):
    """A new market of a game the books already fill to $2,500 opens no
    book this tick (`game_cap_full`, read again next tick); with $700
    left the candidate opens at $700 and the new book's own tick reads
    the same room."""
    p, a, b, v, http = _game_world(monkeypatch, his_b=40000.0, a_ledger=5000)
    del p.books[b["id"]], p.rows[b["standing_row_id"]]
    st = _tick(p, v, http=http)
    assert len(p.books) == 1 and _census(st, "game_cap_full") == 1 and not _places(v)
    assert _census(st, "game_unreadable") == 0, "a full game is not an unreadable one"
    ml._game_full_until.clear()         # the memo the full game wrote (pinned below, on its own)
    p2, a2, b2, v2, http2 = _game_world(monkeypatch)
    del p2.books[b2["id"]], p2.rows[b2["standing_row_id"]]
    st2 = _tick(p2, v2, http=http2)
    new = [bk for bk in p2.books.values() if bk["us_market_slug"] == SLUG]
    assert len(new) == 1 and new[0]["target"] == 1400 and new[0]["last_plan"]["game_room"] == 700.0
    assert _census(st2, "game_cap_scaled") == 2, "named at open and on the new book's tick"
    assert _places(v2)[-1][1:5] == (SLUG, 0.49, 1400, False)


def test_an_unreadable_exposure_is_no_room_and_the_rules_are_pure(monkeypatch):
    """A held book whose avg_cost AND mark cannot be read is an
    unreadable figure: the game has no room (fail closed), the plan
    says the exposure was unreadable, and nothing sells."""
    p, a, b, v, http = _game_world(monkeypatch, his_b=40000.0, b_ledger=200, a_ledger=100, b_first=True)
    a["avg_cost"] = None
    a["last_plan"] = None
    assert b["id"] < a["id"], "B is walked first: A's mark has not been read when B is sized"
    st = _tick(p, v, http=http)
    assert b["last_plan"]["game_exposure"] is None and b["last_plan"]["game_room"] == 0.0
    assert b["target"] == 200 and b["last_plan"]["game_cap"] == "game_unreadable" and not _places(v)
    assert _census(st, "game_unreadable") == 1 and _census(st, "game_cap_full") == 0, "told apart"
    # the readings, pure
    assert rules.book_exposure(3600, 0.50, 0.31) == 1800.0
    assert rules.book_exposure(3600, None, 0.31) == pytest.approx(1116.0), "no cost: the mark"
    assert rules.book_exposure(3600, None, None) is None and rules.book_exposure(None, 0.5, 0.5) is None
    assert rules.book_exposure(0, None, None) == 0.0 and rules.book_exposure(0, None, None, resting_usd=12.5) == 12.5
    assert rules.book_exposure(100, 0.5, 0.5, resting_usd=None) is None
    assert rules.book_exposure(100, 1.0, 0.5) is None and rules.book_exposure(100, 0.0, 0.5) is None
    assert rules.game_room(1800.0) == 700.0 and rules.game_room(0.0) == 2500.0
    assert rules.game_room(2500.0) == 0.0 and rules.game_room(9999.0) == 0.0
    assert rules.game_room(None) == 0.0 and rules.game_room(-1.0) == 0.0
    assert rules.game_room(100.0, cap_usd=0.0) == 0.0 and rules.game_room(100.0, cap_usd=1000.0) == 900.0
    # game_capped: only an increase is touched, never below the ledger
    assert rules.game_capped(3000, 1400, 0, False) == (1400, "game_cap_scaled")
    assert rules.game_capped(3000, 3000, 0, False) == (3000, None)
    assert rules.game_capped(4000, None, 200, False) == (200, "game_cap_full")
    assert rules.game_capped(4000, 150, 200, False) == (200, "game_cap_full"), "a room under the ledger is no reduce"
    assert rules.game_capped(300, None, 500, False) == (300, None) and rules.game_capped(0, None, 500, False) == (0, None)
    assert rules.game_capped(-3000, -1400, 0, True) == (-1400, "game_cap_scaled")
    assert rules.game_capped(-4000, None, -200, True) == (-200, "game_cap_full")
    assert rules.game_capped(-4000, -150, -200, True) == (-200, "game_cap_full")
    assert rules.game_capped(-300, None, -500, True) == (-300, None)
    assert rules.game_capped(300, None, -500, True) == (300, None), "a sign flip is not an increase"


def test_room_whose_target_is_under_the_ledger_holds_the_book_and_never_sells(monkeypatch):
    """A holds 4,900 @ 0.50 = $2,450 at cost; B holds 200 and his net
    says 4,000. The game has $50 left: a room target of 100 sh, UNDER
    B's 200 -- the book stays at 200 (an increase of 0, `game_cap_full`),
    never a sale of the 100 over; nothing is placed."""
    p, a, b, v, http = _game_world(monkeypatch, his_b=40000.0, b_ledger=200, a_ledger=4900)
    st = _tick(p, v, http=http)
    assert b["target"] == 200 and b["last_plan"]["game_cap"] == "game_cap_full"
    assert b["last_plan"]["game_room"] == 50.0 and b["last_plan"]["game_exposure"] == 2450.0
    assert b["last_plan"]["target_raw"] == 4000.0 and b["ledger_net"] == 200
    assert not _places(v) and b["last_plan"]["reason"] == "on target"
    assert _census(st, "game_cap_full") == 1 and _census(st, "game_cap_scaled") == 0


def test_a_rest_whose_cancel_did_not_land_is_no_room_for_its_siblings(monkeypatch):
    """The adversarial review's repro (HIGH, fail-open). A (id 2) is
    flat with a 3,600 @ 0.49 BUY rest past the TTL; the cancel fails
    twice and the venue still shows it resting: the row is 'unknown',
    popped from open_by_book, A frozen cancel_pending -- and the rest
    can still fill for $1,764. B (id 1), walked first, must NOT read A
    at $0 and take the whole $2,500 ($3,724 at cost on the game): a
    figure nobody can read is no figure, the game has no room, B's
    increase is 0 -- named `game_unreadable`, not `game_cap_full`
    (re-review LOW-3: an unreadable game is told apart from a full
    one) -- nothing is placed."""
    p, a, b, v, http = _game_world(monkeypatch, his_b=40000.0, a_ledger=0, a_net=7200.0, b_first=True)
    p.add_order(a, side=BUY, wire=0.49, qty=3600, order_id="oid-a", us_market_slug=GAME_SLUG_A,
                his_level=0.50, placed_ts=NOW - 700)
    v.rest("oid-a", "BUY", 0.49, 3600, slug=GAME_SLUG_A)
    v.cancel_ok = False
    assert b["id"] < a["id"], "B is walked first"
    st = _tick(p, v, http=http)
    assert v.orders["oid-a"]["state"] == "new", "the rest still stands on the venue"
    assert a["state"] == "frozen" and a["frozen_reason"] == "cancel_pending"
    assert next(iter(p.orders.values()))["state"] == "unknown"
    assert b["last_plan"]["game_exposure"] is None and b["last_plan"]["game_room"] == 0.0
    assert b["target"] == 0 and b["last_plan"]["game_cap"] == "game_unreadable"
    assert not _places(v) and _census(st, "game_unreadable") == 1
    assert _census(st, "game_cap_full") == 0 and _census(st, "game_cap_scaled") == 0, "told apart"
    assert _census(st, "cancel_pending") == 1
    assert a["last_plan"]["game_exposure"] == 0.0 and "game_cap" not in a["last_plan"], "A's own room ignores itself"
    # the candidate path reads the same nothing: A's OWN sized figure is
    # None (not $0 plus its target), so a new market of the game opens
    # no book this tick, named `game_unreadable` there too
    p2, a2, b2, v2, http2 = _game_world(monkeypatch, his_b=40000.0, a_ledger=0, a_net=7200.0)
    del p2.books[b2["id"]], p2.rows[b2["standing_row_id"]]
    p2.add_order(a2, side=BUY, wire=0.49, qty=3600, order_id="oid-a", us_market_slug=GAME_SLUG_A,
                 his_level=0.50, placed_ts=NOW - 700)
    v2.rest("oid-a", "BUY", 0.49, 3600, slug=GAME_SLUG_A)
    v2.cancel_ok = False
    st2 = _tick(p2, v2, http=http2)
    assert a2["frozen_reason"] == "cancel_pending" and v2.orders["oid-a"]["state"] == "new"
    assert len(p2.books) == 1 and not _places(v2), "no book opened on a game nobody can read"
    assert _census(st2, "game_unreadable") == 1 and _census(st2, "game_cap_full") == 0
    assert _census(st2, "game_cap_scaled") == 0
    assert ml._game_full_until == {}, "an unreadable game writes no memo (only a full one does)"
    # a cancel the PLAN sent -- a rest kept by step O, then cancelled
    # under the plan's own refusal (no ratio: `no_plan`) -- that did not
    # land is the same unreadable figure for the book walked after it
    p3, a3, b3, v3, http3 = _game_world(monkeypatch, his_b=40000.0, a_ledger=0, a_net=7200.0, a_ratio=None)
    p3.add_order(a3, side=BUY, wire=0.49, qty=3600, order_id="oid-a", us_market_slug=GAME_SLUG_A,
                 his_level=0.50, placed_ts=NOW - 30)
    v3.rest("oid-a", "BUY", 0.49, 3600, slug=GAME_SLUG_A)
    v3.cancel_ok = False
    assert a3["id"] < b3["id"], "A is walked first, B reads it after its plan"
    st3 = _tick(p3, v3, http=http3)
    assert a3["last_reason"] == "no_ratio" and a3["last_plan"]["kind"] == "no_plan"
    assert a3["frozen_reason"] == "cancel_pending" and v3.orders["oid-a"]["state"] == "new"
    assert ("cancel", "oid-a", GAME_SLUG_A) in v3.calls, "the plan's cancel went out"
    assert b3["last_plan"]["game_exposure"] is None and b3["last_plan"]["game_room"] == 0.0
    assert b3["target"] == 0 and not _places(v3) and _census(st3, "game_unreadable") == 1
    assert _census(st3, "game_cap_full") == 0
    # the reading itself: a non-terminal order the tick could not read
    # is None, nothing resting is 0, a resting reduce is 0
    t = ml._Tick(pool=p, pmus=v, http=http, now=NOW, stats=ml._new_stats())
    t.nonterminal.add(a["id"])
    assert ml._resting_add_usd(t, a) is None and ml._held_exposure(t, a) is None
    t.nonterminal.discard(a["id"])
    assert ml._resting_add_usd(t, a) == 0.0 and ml._held_exposure(t, a) == 0.0


def test_the_game_room_is_applied_at_cost_so_a_fallen_mark_never_averages_down_past_the_cap(monkeypatch):
    """The re-review's X1 (MEDIUM-1). A holds 3,600 @ 0.49 = $1,764 at
    cost; B holds 1,400 @ 0.50 = $700; the mark falls to 0.30 and his
    net on B says 3,000. B's room is $736 -- right -- but the first
    cut handed mi.target_shares cap_usd=736 AT THE MARK: 736 / 0.30 =
    2,453 shares in total, a BUY of 1,053 @ 0.29 = $305, and the game
    held $2,769 at cost. The room is dollars at cost: the cap handed
    over is B's 1,400 at the mark ($420) plus the $36 the room leaves
    after B's own $700 -- $456, 1,520 shares, a BUY of 120 @ 0.29 =
    $34.80 -- and the game stays at $2,498.80: mirror-pnl's
    `open_cost` reading of the game, held at cost plus the resting
    BUY, never over $2,500. The per-market cap keeps its at-the-mark
    reading (cap / mark shares in total), unchanged."""
    p, a, b, v, http = _game_world(monkeypatch, his_b=30000.0, b_ledger=1400, a_ledger=3600, a_avg=0.49)
    v.bid, v.ask = 0.29, 0.31
    st = _tick(p, v, http=http)
    assert a["target"] == 3600 and a["last_plan"]["game_room"] == pytest.approx(1800.0), "A reads B's $700"
    assert b["last_plan"]["game_room"] == pytest.approx(736.0)
    assert b["last_plan"]["game_exposure"] == pytest.approx(1764.0)
    assert b["target"] == 1520 and b["last_plan"]["game_cap"] == "game_cap_scaled"
    assert b["last_plan"]["target_raw"] == 3000.0
    # the ask 0.31 is through his 0.50, so the IOC at his cent goes first
    # (E4 addendum; unfilled on this fake) and the 120 rest at 0.29 is the
    # standing order the game's room is read against
    pl = [c for c in _places(v) if c[1] == SLUG and c[5] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"]
    assert len(pl) == 1 and pl[0][2:5] == (0.29, 120, False), pl
    assert [c[2:4] for c in _places(v) if c[1] == SLUG and c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"] == [(0.50, 120)]
    held = sum(rules.book_exposure(bk["ledger_net"], bk["avg_cost"], 0.30, bk["intent"])
               for bk in (a, b))
    resting = pl[0][3] * pl[0][2]
    assert held == pytest.approx(2464.0) and resting == pytest.approx(34.8)
    assert held + resting <= 2500.0 and held + resting == pytest.approx(2498.8)
    assert _census(st, "game_cap_scaled") == 1 and _census(st, "shadow_live_disagree") == 0
    # the shadow, at the same net, agrees with the at-cost figure: the
    # check is handed the cap B was sized at ($456), not the room
    p2, a2, b2, v2, http2 = _game_world(monkeypatch, his_b=30000.0, b_ledger=1400, a_ledger=3600, a_avg=0.49)
    v2.bid, v2.ask = 0.29, 0.31
    p2.shadow.append(_shadow_row(3000, 0.10, 30000.0))
    st2 = _tick(p2, v2, http=http2)
    assert b2["target"] == 1520 and _census(st2, "shadow_live_disagree") == 0
    assert _census(st2, "shadow_check_skipped") == 0
    # a mark ABOVE the cost: the increase still costs the room's $36 at
    # the mark -- 1,400 @ 0.60 ($840) + $36 = $876, 1,460 shares, 60 more
    p3, a3, b3, v3, http3 = _game_world(monkeypatch, his_b=30000.0, b_ledger=1400, a_ledger=3600, a_avg=0.49)
    v3.bid, v3.ask = 0.59, 0.61
    _tick(p3, v3, http=http3)
    assert b3["target"] == 1460 and b3["last_plan"]["game_room"] == pytest.approx(736.0)
    pl3 = [c for c in _places(v3) if c[1] == SLUG]
    assert len(pl3) == 1 and pl3[0][3] == 60 and pl3[0][3] * pl3[0][2] <= 36.0 + 1e-9


def test_a_full_games_unopened_markets_are_skipped_for_the_memo_then_read_again(monkeypatch):
    """Re-review LOW-2. A holds the whole $2,500; the fixture market is
    a candidate of the same game. Tick 1 reads it and opens nothing
    (`game_cap_full`) and writes the memo; tick 2, a second later,
    skips the candidate BEFORE its venue read -- no quote read on its
    slug, no candidate slot -- under `cand_game_full_skipped`; tick 3,
    past GAME_FULL_MEMO_S, reads it again. An unreadable game is
    memoised the same way."""
    assert ml.GAME_FULL_MEMO_S == 60.0
    p, a, b, v, http = _game_world(monkeypatch, his_b=40000.0, a_ledger=5000)
    del p.books[b["id"]], p.rows[b["standing_row_id"]]
    st = _tick(p, v, http=http)
    assert len(p.books) == 1 and _census(st, "game_cap_full") == 1
    assert ("bbo", SLUG) in v.calls and st["reads"] == 2, "A's read and the candidate's"
    assert ml._game_full_until == {("game", GAME_KEY): NOW + ml.GAME_FULL_MEMO_S}
    v2 = _Venue(bid=0.49, ask=0.51, held={GAME_SLUG_A: 5000, SLUG: 0})
    st2 = _tick(p, v2, now=NOW + 1, http=http)
    assert _census(st2, "cand_game_full_skipped") == 1 and _census(st2, "game_cap_full") == 0
    # E6: A read on target with nothing open a tick ago is a QUIET book,
    # skipped by the rotation this tick (`book_quiet_skipped`), so no
    # quote read at all lands on the venue
    assert [c[1] for c in v2.calls if c[0] == "bbo"] == [], "no read on the skipped candidate"
    assert _census(st2, "book_quiet_skipped") == 1 and st2["reads"] == 0 and len(p.books) == 1, \
        "A quiet, the candidate memo-skipped: no venue read, no candidate slot"
    v3 = _Venue(bid=0.49, ask=0.51, held={GAME_SLUG_A: 5000, SLUG: 0})
    st3 = _tick(p, v3, now=NOW + ml.GAME_FULL_MEMO_S + 1, http=http)
    assert _census(st3, "cand_game_full_skipped") == 0 and _census(st3, "game_cap_full") == 1
    # E6: A's turn is the third tick after its read (QUIET_EVERY_TICKS);
    # this is the second, so the candidate's read is the tick's one
    assert ("bbo", SLUG) in v3.calls and st3["reads"] == 1 and _census(st3, "book_quiet_skipped") == 1
    # the game freed up inside the memo -- he cut A's market to 4,000
    # and A followed him down to 2,000 @ 0.50, $1,000 at cost: still
    # skipped until the memo runs (a minute at most), then read and
    # opened at the $1,500 left, 3,000 sh
    for f in p.fills:
        if f["asset"] == LA:
            f["size"] = 4000.0
    p.snap[LA] = 4000.0
    for row in http.rows:
        if row["asset"] == LA:
            row["size"] = 4000.0
    a["ledger_net"] = 2000
    p.rows[a["standing_row_id"]]["filled_shares"] = 2000.0
    v4 = _Venue(bid=0.49, ask=0.51, held={GAME_SLUG_A: 2000, SLUG: 0})
    # E6: this tick is A's turn in the quiet rotation (read on tick 1,
    # due on tick 1 + QUIET_EVERY_TICKS); E11 widened the rotation 3 -> 9,
    # so the rotation's clock is moved to that tick -- the tick after
    # this one keeps the same clock-based memo story (the same number
    # read off the constant, nothing else moved)
    ml._tick_seq = ml._quiet_memo[a["id"]]["seq"] + ml.QUIET_EVERY_TICKS - 1
    st4 = _tick(p, v4, now=NOW + ml.GAME_FULL_MEMO_S + 2, http=http)
    assert a["target"] == 2000 and not _places(v4), "A on target at $1,000"
    assert _census(st4, "cand_game_full_skipped") == 1 and len(p.books) == 1
    v5 = _Venue(bid=0.49, ask=0.51, held={GAME_SLUG_A: 2000, SLUG: 0})
    st5 = _tick(p, v5, now=NOW + 2 * ml.GAME_FULL_MEMO_S + 2, http=http)
    assert _census(st5, "cand_game_full_skipped") == 0 and len(p.books) == 2
    new = [bk for bk in p.books.values() if bk["us_market_slug"] == SLUG][0]
    assert new["target"] == 3000 and new["last_plan"]["game_room"] == 1500.0
    assert _census(st5, "game_cap_scaled") == 2 and _places(v5)[-1][1:5] == (SLUG, 0.49, 3000, False)
    assert ml._game_full_until == {("game", GAME_KEY): NOW + 2 * ml.GAME_FULL_MEMO_S + 1}, \
        "a game with room writes no new memo: tick 3's, expired, is all that stands"
    # an UNREADABLE game writes no memo: its cause (a sibling's order
    # nobody could read) is retried by step O every tick, so the market
    # is read again next tick -- and opens the moment the cancel lands
    p6, a6, b6, v6, http6 = _game_world(monkeypatch, his_b=40000.0, a_ledger=0, a_net=7200.0)
    del p6.books[b6["id"]], p6.rows[b6["standing_row_id"]]
    p6.add_order(a6, side=BUY, wire=0.49, qty=3600, order_id="oid-a", us_market_slug=GAME_SLUG_A,
                 his_level=0.50, placed_ts=NOW - 700)
    v6.rest("oid-a", "BUY", 0.49, 3600, slug=GAME_SLUG_A)
    v6.cancel_ok = False
    ml._game_full_until.clear()
    st6 = _tick(p6, v6, http=http6)
    assert _census(st6, "game_unreadable") == 1 and len(p6.books) == 1
    assert ml._game_full_until == {}, "unreadable is a per-tick reading, never memoised"
    v7 = _Venue(bid=0.49, ask=0.51, held={GAME_SLUG_A: 0, SLUG: 0})
    v7.orders = v6.orders
    v7.cancel_ok = False
    st7 = _tick(p6, v7, now=NOW + 1, http=http6)
    assert _census(st7, "cand_game_full_skipped") == 0 and ("bbo", SLUG) in v7.calls, "read again"
    assert _census(st7, "game_unreadable") == 1 and len(p6.books) == 1
    v8 = _Venue(bid=0.49, ask=0.51, held={GAME_SLUG_A: 0, SLUG: 0})
    v8.orders = v6.orders                 # the cancel lands this tick: the rest is gone
    st8 = _tick(p6, v8, now=NOW + 2, http=http6)
    assert v8.orders["oid-a"]["state"] == "cancelled" and _census(st8, "game_unreadable") == 0
    assert len(p6.books) == 2, "readable again: the candidate opens"
    # a candidate with no game key is never memoised: the memo's key is the game
    assert ml._game_key_of({"id": 9, "game_key": None}) == ("book", "9")


def test_two_candidates_on_one_game_in_one_tick_share_the_room(monkeypatch):
    """The re-review's A4: no book on the game, two of its markets are
    candidates in one tick (his 40,000 on each, 10%). The first opens
    at 4,000 sh = $2,000 and joins the game's index; the second reads
    that $2,000 and opens at the $500 left, 1,000 sh."""
    p, a, b, v, http = _game_world(monkeypatch, his_b=40000.0, a_ledger=0, a_net=40000.0, a_ratio=0.10)
    del p.books[a["id"]], p.rows[a["standing_row_id"]]
    del p.books[b["id"]], p.rows[b["standing_row_id"]]
    p.conds = [CID, CID_A]
    orig_fetch = p.fetch

    async def fetch(sql, *args):
        s = " ".join(sql.split())
        if "AS market_title, t.event_slug" in s:          # his_fills, per condition
            return [x for x in p.fills if p.token_cid.get(x["asset"]) == args[1]]
        if "FROM live_orders WHERE asset = ANY($1::text[])" in s:
            rows = [{"asset": M, "us_market_slug": SLUG, "intent": "ORDER_INTENT_BUY_LONG"},
                    {"asset": LA, "us_market_slug": GAME_SLUG_A, "intent": "ORDER_INTENT_BUY_LONG"}]
            return [r for r in rows if r["asset"] in set(args[0])]
        return await orig_fetch(sql, *args)
    p.fetch = fetch
    st = _tick(p, v, http=http)
    books = sorted(p.books.values(), key=lambda bk: bk["id"])
    assert [bk["target"] for bk in books] == [4000, 1000]
    assert books[1]["last_plan"]["game_exposure"] == 2000.0 and books[1]["last_plan"]["game_room"] == 500.0
    assert sum(abs(bk["target"]) * 0.5 for bk in books) <= 2500.0
    assert [x[3] for x in _places(v)] == [4000, 1000] and _census(st, "game_cap_scaled") >= 1



def _game_cost(books, mark, places):
    """Held at cost (mirror-pnl's open_cost) + the resting BUY-side
    notional of THIS tick's placements, as the venue would hold them."""
    held = sum(rules.book_exposure(b["ledger_net"], b["avg_cost"], mark, b["intent"]) for b in books)
    resting = 0.0
    for c in places:
        _k, slug, px, qty, sell = c[:5]
        if sell:
            continue
        # a BUY_SHORT placement: the intent rides in the call (index 6); its collateral is 1 - px
        intent = c[6] if len(c) > 6 else None
        resting += qty * ((1.0 - px) if intent == SHORT else px)
    return held, resting


def _short_b_world(monkeypatch, b_ledger=-1000, b_avg=0.72, his_other=30100.0, a_ledger=3000, a_avg=0.60):
    """B is a SHORT book on the fixture market: his 100 long vs
    `his_other` other -> net -(his_other - 100); ratio 0.10. A is a long
    book on the game's other market, on target. One quote everywhere:
    0.69/0.71 -> mark 0.70, the short leg's price 0.30."""
    _rails_2026_09_06(monkeypatch)
    _shorts_on(monkeypatch)
    a_ratio = 0.5
    a_net = float(a_ledger) / a_ratio
    fills = _his(100.0, long_px=0.31, other_size=his_other, other_px=0.72)
    fills.append(_fill(LA, "BUY", a_net, 0.50, NOW - 2500))
    snap = {M: 100.0, N: his_other, LA: a_net, OA: 0.0}
    p = _pool(fills=fills, snap=snap)
    p.markets[CID_A] = {"closed": False, "resolved": False, "resolved_prices": None}
    p.token_index.update({LA: 1, OA: 0})
    p.token_cid.update({LA: CID_A, OA: CID_A})
    a = p.add_book(ledger=a_ledger, ratio=a_ratio, avg_cost=a_avg, condition_id=CID_A,
                   us_market_slug=GAME_SLUG_A, long_asset=LA, other_asset=OA,
                   game_key=le._us_game_key(GAME_SLUG_A))
    b = _short_book(p, ledger=b_ledger, avg=b_avg, ratio=0.10)
    rows = [{"conditionId": CID, "asset": M, "size": 100.0}, {"conditionId": CID, "asset": N, "size": his_other},
            {"conditionId": CID_A, "asset": LA, "size": a_net}, {"conditionId": CID_A, "asset": OA, "size": 0}]
    v = _Venue(bid=0.69, ask=0.71, held={GAME_SLUG_A: a_ledger, SLUG: b_ledger})
    return p, a, b, v, _Http(rows=rows)


def test_a_short_books_increase_under_a_bound_room_is_sized_in_collateral(monkeypatch):
    """E1 re-review LOW-4 (a test gap of E1 v3, folded here). A long
    3,000 @ 0.60 = $1,800 (on target). B SHORT -1,000 @ 0.72:
    collateral $280. Room $700; room - held = $420 at the short leg's
    mark price 1 - 0.70 = 0.30 -> 1,400 more shorts -> target -2,400
    (px_mark = 0.70 would give 1000 x 0.70 + 420 = 1,120 -> -3,000,
    $600 of new collateral, the game at $2,680)."""
    p, a, b, v, http = _short_b_world(monkeypatch)
    st = _tick(p, v, http=http)
    pl = [c for c in _places(v) if c[1] == SLUG]
    held, resting = _game_cost((a, b), 0.70, pl)
    assert a["target"] == 3000 and not [c for c in _places(v) if c[1] == GAME_SLUG_A], "A holds still"
    assert b["last_plan"]["game_room"] == pytest.approx(700.0)
    assert b["last_plan"]["game_exposure"] == pytest.approx(1800.0)
    # 720 / 0.30 = 2399.9999999999995 -> int -> 2,399: one share UNDER, the safe side
    assert b["target"] in (-2400, -2399) and b["last_plan"]["game_cap"] == "game_cap_scaled"
    assert b["last_plan"]["target_raw"] == pytest.approx(-3000.0)
    assert len(pl) == 1 and pl[0][3] in (1399, 1400) and pl[0][4] is False and pl[0][6] == SHORT, pl
    assert (1.0 - pl[0][2]) * pl[0][3] <= 420.0 + 1e-9, "new collateral at most room - held"
    assert held + resting <= 2500.0
    assert _census(st, "shadow_live_disagree") == 0


# ------------------------ 20. latency: the parallel walk, the budgets, the take (E2, 2026-09-06)
#
# Owner, ~22:50Z: "We need to be mapping and mirroring a larger
# percentage of his orders and positions, I want this firing as
# frequently as his. Make the latency as low as possible". The book
# walk was sequential (~3.5 s a book, 162 s with 46 books); it now runs
# rules.MIRROR_BOOK_CONCURRENCY books at once, one GAME at a time in id
# order, with the tick's shared counters made take-first. Pinned on a
# fake venue whose quote read sleeps 0.05 s (the real pacer is patched
# out by the fixture, as everywhere in this file) and a fake pool whose
# every statement yields, so the awaits between a check and its write
# really interleave.

_REAL_SLEEP = time.sleep          # the fixture patches time.sleep; the slow venue wants the real one
_REAL_PM_HELD = ml._pm_held       # the fixture patches ml._pm_held; the page-count pin wants the real one


class _SlowVenue(_Venue):
    """A venue whose quote read takes 0.05 s of wall time (in the worker
    thread the read runs on), so a 50-book walk is 2.5 s sequential."""

    def __init__(self, *a, delay=0.05, **kw):
        super().__init__(*a, **kw)
        self.delay = delay

    def bbo_read(self, client, slug):
        _REAL_SLEEP(self.delay)
        return super().bbo_read(client, slug)


class _PerSlugVenue(_SlowVenue):
    """A quote read that fails on some slugs (instantly) and succeeds on
    the rest after `delay`: under the parallel walk the failures land
    first, in a row (the reviewer's shape for the miss streak)."""

    def __init__(self, *a, bad=(), **kw):
        super().__init__(*a, **kw)
        self.bad = set(bad)

    def bbo_read(self, client, slug):
        self.calls.append(("bbo", slug))
        if slug in self.bad:
            return {"bid": None, "ask": None, "state": None, "error": "RuntimeError"}
        _REAL_SLEEP(self.delay)
        return {"bid": self.bid, "ask": self.ask, "state": self.states.get(slug, self.state), "error": None}


def _expected_calls(v):
    """What the census must count for the fake venue's recorded calls:
    every call it recorded, the positions walk's pages (one, in the
    fake), and a second request for every BUY placement (the preview)."""
    return len(v.calls) + int(getattr(v.portfolio, "pages", 1)) + sum(
        1 for c in v.calls if c[0] == "place" and not c[4])


class _YieldingPool(_Pool):
    """The worker's pool with every statement yielding to the loop
    once, the way a real driver's round trip does: the awaits between
    a budget check and its write are real interleaving points."""

    async def fetch(self, sql, *a):
        await asyncio.sleep(0)
        return await super().fetch(sql, *a)

    async def fetchval(self, sql, *a):
        await asyncio.sleep(0)
        return await super().fetchval(sql, *a)

    async def fetchrow(self, sql, *a):
        await asyncio.sleep(0)
        return await super().fetchrow(sql, *a)

    async def execute(self, sql, *a):
        await asyncio.sleep(0)
        return await super().execute(sql, *a)


class _HttpByCid(_Http):
    """The data API answering per market: the rows whose conditionId
    is the one asked for (market_positions refuses any other)."""

    async def get(self, path, params=None):
        self.calls.append((path, params))
        want = (params or {}).get("market")
        return await _Http(rows=[r for r in self.rows if r.get("conditionId") == want],
                           status=self.status).get(path, params)


def _increase_world(n, his=300.0, px=0.30, yielding=True, **pool_kw):
    """`n` live FLAT books on `n` markets (each its own game) with him
    holding `his` of each long token at `px`: every book plans a rest
    of `his` shares (ratio 1.0 on the fixture rails) at the bid. No
    candidate. Returns (pool, slugs, http)."""
    fills = [_fill(f"tokL{i}", "BUY", his, px, NOW - 3000 - i) for i in range(n)]
    snap = {}
    rows = []
    for i in range(n):
        snap[f"tokL{i}"], snap[f"tokO{i}"] = his, 0.0
        rows += [{"conditionId": f"0xbook{i}", "asset": f"tokL{i}", "size": his},
                 {"conditionId": f"0xbook{i}", "asset": f"tokO{i}", "size": 0}]
    cls = _YieldingPool if yielding else _Pool
    pool_kw.setdefault("conds", [])
    p = cls(fills=fills, snap=snap, snap_at=NOW - 40, ratio_fills=_ratio_fills(), **pool_kw)
    slugs = _many_books(p, n)
    for i in range(n):
        p.token_index.update({f"tokL{i}": 1, f"tokO{i}": 0})
        p.token_cid.update({f"tokL{i}": f"0xbook{i}", f"tokO{i}": f"0xbook{i}"})
    return p, slugs, _HttpByCid(rows=rows)


def _tick_wall(p, v, **kw):
    t0 = time.monotonic()
    st = _tick(p, v, **kw)
    return st, time.monotonic() - t0


def _comparable(st):
    """The tick's stats less what wall time and interleaving order move
    (E6: the timing block inside `short` is wall time too; E7: so is
    the data-API block beside it)."""
    out = {k: v for k, v in st.items() if k not in ("recent", "tick_s")}
    if isinstance(out.get("short"), dict):
        # E9: and the fast ticks' block beside them (seconds); E10: the wall block too
        out["short"] = {k: v for k, v in out["short"].items()
                        if k not in ("timing", "data_api", "fast", "wall", "gate")}
    return out


def test_e2_constants_the_walk_concurrency_the_ops_budget_and_the_call_guard(monkeypatch):
    """The knobs, and their directions: the concurrency and the call
    guard are caps (env lowers only, floors 1 and 0); the ops budget is
    20 (was 6); the take wait is 0 since the E4 addendum (20 s under
    E2, 120 s before) and still only lengthens; the candidate budget is
    40 (was 20); POLL_S stays 30 s -- the tick's floor is the pacer's,
    not under 10 s (see mirror_live POLL_S and the report)."""
    import importlib
    assert rules.MIRROR_BOOK_CONCURRENCY == 6 and rules.MIRROR_VENUE_CALLS_PER_TICK == 80
    assert rules.MIRROR_MAX_ORDER_OPS_PER_TICK == 20 and rules.MIRROR_TAKE_AFTER_S == 0.0
    assert ml.MAX_MARKETS_PER_TICK == 40 and ms.MAX_MARKETS_PER_TICK == 20 and ml.POLL_S == 30.0 and ms.POLL_S == 30.0
    for env, attr, cases in (("MIRROR_BOOK_CONCURRENCY", "MIRROR_BOOK_CONCURRENCY",
                              (("12", 6), ("2", 2), ("0", 1), ("-4", 1), ("junk", 6))),
                             ("MIRROR_VENUE_CALLS_PER_TICK", "MIRROR_VENUE_CALLS_PER_TICK",
                              (("999", 80), ("10", 10), ("0", 0), ("-1", 0), ("inf", 80))),
                             ("MIRROR_MAX_ORDER_OPS_PER_TICK", "MIRROR_MAX_ORDER_OPS_PER_TICK",
                              (("99", 20), ("6", 6), ("0", 1))),
                             ("MIRROR_TAKE_AFTER_S", "MIRROR_TAKE_AFTER_S",
                              (("5", 5.0), ("120", 120.0), ("junk", 0.0), ("-1", 0.0)))):
        for raw, want in cases:
            monkeypatch.setenv(env, raw)
            try:
                assert getattr(importlib.reload(rules), attr) == want, (env, raw)
            finally:
                monkeypatch.delenv(env)
    importlib.reload(rules)
    assert "MIRROR_BOOK_CONCURRENCY" in rules.__all__ and "MIRROR_VENUE_CALLS_PER_TICK" in rules.__all__
    src = inspect.getsource(ml)
    for restated in ("MIRROR_BOOK_CONCURRENCY =", "MIRROR_VENUE_CALLS_PER_TICK ="):
        assert restated not in src, restated
    for k in ("take_at_his_level", "take_refused_price", "venue_calls", "venue_calls_capped",
              "abandoned_in_flight", "walk_error", "backoff_skipped_circuit"):
        assert k in ml.CENSUS_KEYS and k in ml._new_stats()["census"], k
    assert ml.CENSUS_KEYS[-1] == "cand_terminal_skipped"
    # the stale-arm floor (review MEDIUM-2b): a multiplier of the wait with
    # a 60 s floor, so a 0 s wait does not make every arm stale at once
    assert ml.TAKE_ARM_STALE_WAITS == 2 and ml.TAKE_ARM_STALE_MIN_S == 60.0
    assert "max(float(TAKE_ARM_STALE_WAITS) * float(rules.MIRROR_TAKE_AFTER_S)" in inspect.getsource(ml._act)


def test_fifty_books_tick_in_parallel_under_a_second_and_a_half_with_every_stat_the_sequential_ones(monkeypatch):
    """Step 1's pin. Fifty live books on a venue whose quote read sleeps
    0.05 s: sequential (N=1) the walk is >= 2.5 s; at N=6 it is under
    1.5 s, every book is read, and every stat -- the census, the reads,
    the ops, the books, the venue state -- equals the sequential run's.
    At the DEFAULT call guard: the books' reads never count against it.
    And with a MIXED venue -- every other book's quote read failing
    instantly, the good ones slow -- the miss streak is judged in walk
    order (review MEDIUM-4), so the parallel run neither abandons nor
    differs from the sequential one; three bad books IN A ROW abandon
    both, under the same name."""
    p = _pool(conds=[])
    slugs = _many_books(p, 50)
    v = _SlowVenue()
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 1)
    seq, seq_wall = _tick_wall(p, v)
    assert seq_wall >= 2.4, seq_wall
    assert seq["books_live"] == 50 and seq["reads"] == 50 and not seq["abandoned"]
    p2 = _pool(conds=[])
    _many_books(p2, 50)
    v2 = _SlowVenue()
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 6)
    par, par_wall = _tick_wall(p2, v2)
    assert par_wall < 1.5, par_wall
    bbos = [c[1] for c in v2.calls if c[0] == "bbo"]
    assert sorted(bbos) == sorted(slugs) and len(bbos) == 50, "every book read once"
    assert _comparable(par) == _comparable(seq)
    assert par["census"]["venue_calls"] == seq["census"]["venue_calls"] == 52   # 50 quotes, the positions walk, the open-orders read
    assert _census(par, "on_target") == 50 and par["ops"] == 0
    assert _census(par, "venue_calls_capped") == 0 and rules.MIRROR_VENUE_CALLS_PER_TICK == 80
    # the wall time is published on both, 1 dp
    assert par["tick_s"] <= 1.5 and seq["tick_s"] >= 2.4
    # the mixed venue: good/bad alternating, 25 misses, never three in a row
    bad = slugs[1::2]
    p3 = _pool(conds=[])
    _many_books(p3, 50)
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 1)
    seq3 = _tick(p3, _PerSlugVenue(delay=0.02, bad=bad))
    assert not seq3["abandoned"] and _census(seq3, "no_quote") == 25 and seq3["books_live"] == 50
    p4 = _pool(conds=[])
    _many_books(p4, 50)
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 6)
    par4 = _tick(p4, _PerSlugVenue(delay=0.02, bad=bad))
    assert not par4["abandoned"], par4["census"]
    assert _comparable(par4) == _comparable(seq3)
    assert ml._backoff_until == 0.0
    # three in a row (walk order) abandon both, under the same name
    bad3 = slugs[10:13]
    outs = []
    for n in (1, 6):
        p5 = _pool(conds=[])
        _many_books(p5, 50)
        monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", n)
        outs.append(_tick(p5, _PerSlugVenue(delay=0.02, bad=bad3)))
    assert all(o["abandoned"] and o["abandon_reason"] == "no_quote" for o in outs), [o["census"] for o in outs]
    assert all(_census(o, "no_quote") == 3 and o["ops"] == 0 for o in outs)
    # the walk is a function of its own: the loop delegates to it
    assert "await _walk_books(t, _woken_first(books, woken))" in inspect.getsource(ml._tick)
    assert "asyncio.Semaphore(max(1, int(rules.MIRROR_BOOK_CONCURRENCY)))" in inspect.getsource(ml._walk_books)


def test_the_woken_game_starts_first_and_a_games_books_stay_in_id_order(monkeypatch):
    """The woken-first order is the walk's PRIORITY under the semaphore
    (the woken game's book takes the first slot), and two books of one
    game are never in flight together: B is sized to what A was sized
    to this tick even with every statement yielding and the quote read
    slow.

    The order is read where it is DECIDED -- the order the walk enters
    _tick_book, on the event loop, which is the order the tasks were
    created in and the semaphore hands slots out in -- never off the
    fake venue's call log, which two worker threads append to after a
    wall-clock sleep each (the first slot's read landed second under
    load; E2 review round 4, LOW-6). Every book carries an explicit
    updated_ts, so the whole expected order is a function of the wake
    and those values: the woken game first, then the games oldest
    updated_at first, not id order."""
    p = _pool(conds=[])
    slugs = _many_books(p, 8)
    books = sorted(p.books.values(), key=lambda b: b["id"])
    for i, b in enumerate(books):
        b["updated_ts"] = NOW - 200 + ((i * 3) % 8) * 10      # a permutation of the id order
    woken = books[5]
    assert woken["us_market_slug"] == slugs[5]
    # the expected order, read off the values BEFORE the tick (a plan
    # write bumps updated_ts as the real statement bumps updated_at)
    rest = sorted((b for b in books if b is not woken), key=lambda b: b["updated_ts"])
    expected = [slugs[5]] + [b["us_market_slug"] for b in rest]
    ml.notify(woken["condition_id"])
    entered = []
    orig_tick_book = ml._tick_book

    async def _recording(t, book):
        entered.append(book["us_market_slug"])
        return await orig_tick_book(t, book)
    monkeypatch.setattr(ml, "_tick_book", _recording)
    v = _SlowVenue(delay=0.01)
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 2)
    st = _tick(p, v)
    bbos = [c[1] for c in v.calls if c[0] == "bbo"]
    assert entered == expected, (entered, expected)
    assert expected[1:] != [s for s in slugs if s != slugs[5]], "the pin is on updated_ts, not id order"
    assert st["woken"] == [woken["condition_id"]] and sorted(bbos) == sorted(slugs) and len(bbos) == 8
    # two flat books of ONE game both wanting an increase (E1's world
    # with A flat): sequentially A takes $1,500 of the game's $2,500 and
    # B is scaled to $1,000 / 0.50 = 2,000 sh. Under the parallel walk
    # the game is one unit: the same figures, never two full sizings
    p, a, b, v, http = _game_world(monkeypatch, a_ledger=0, a_net=6000.0)
    yp = _YieldingPool(fills=p.fills, snap=p.snap, snap_at=p.snap_at, ratio_fills=p.ratio_fills)
    for attr in ("books", "orders", "rows", "state", "markets", "token_index", "token_cid"):
        setattr(yp, attr, getattr(p, attr))
    yp.ids = p.ids
    sv = _SlowVenue(bid=0.49, ask=0.51, held={GAME_SLUG_A: 0, SLUG: 0}, delay=0.02)
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 6)
    st = _tick(yp, sv, http=http)
    assert a["target"] == 3000 and b["target"] == 2000, (a["last_plan"], b["last_plan"])
    assert b["last_plan"]["game_cap"] == "game_cap_scaled" and b["last_plan"]["game_room"] == 1000.0
    assert _census(st, "game_cap_scaled") == 1 and st["ops"] == 2
    assert sorted(c[3] for c in _places(sv)) == [2000, 3000]
    # the walk groups by _game_key_of in walk order and runs a game's
    # books one after another under the per-book lock
    src = inspect.getsource(ml._walk_books)
    assert "groups.setdefault(_game_key_of(book), []).append(book)" in src
    assert src.index("for book in gbooks:") < src.index("async with sem:") < src.index("async with _lock_for(book[\"id\"]):")


def test_the_ops_budget_is_take_first_so_twenty_five_books_in_flight_place_exactly_twenty(monkeypatch):
    """The budget check sat at the top of _place and the increment after
    the row INSERT; under the parallel walk two books could pass the
    check on the same last op. The op is reserved at the check
    (_op_slot), committed at the write, released on a refusal before it.
    Twenty-five books each wanting a rest, every statement yielding:
    exactly 20 placements, `ops` 20, `ops_capped` 5 -- and a refusal
    before the write spends nothing. At the DEFAULT call guard: 20 BUY
    placements are 40 guarded requests, under 60."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1e9)
    p, slugs, http = _increase_world(25)
    v = _SlowVenue(delay=0.005)
    st = _tick(p, v, http=http)
    assert len(_places(v)) == 20 and st["ops"] == 20 and _census(st, "ops_capped") == 5, st["census"]
    assert _census(st, "rest_placed") == 20 and _census(st, "book_error") == 0
    assert len([o for o in p.orders.values() if o["state"] == "open"]) == 20
    # the primitive: reserved counts against the budget, released does not
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW, stats=ml._new_stats())
    monkeypatch.setattr(ml, "_current_stats", t.stats)       # the census _mirror_stop writes
    slots = [ml._op_slot(t, "rn1") for _ in range(rules.MIRROR_MAX_ORDER_OPS_PER_TICK)]
    assert all(slots) and t.ops_pending == 20 and t.ops == 0
    assert ml._op_slot(t, "rn1") is None and _census(t.stats, "ops_capped") == 1
    slots[0].release()
    assert t.ops_pending == 19 and ml._op_slot(t, "rn1") is not None
    slots[1].commit()
    slots[1].commit()                       # idempotent
    slots[1].release()                      # a committed slot is not given back
    assert (t.ops, t.ops_pending) == (1, 19)
    # a refusal BEFORE the write releases its slot: under_min_notional
    # on a one-share order leaves the budget whole
    p2 = _pool(fills=_his(1.0, long_px=0.05), snap={M: 1.0, N: 0.0})
    b2 = p2.add_book(ledger=0)
    v2 = _Venue(bid=0.05, ask=0.06)
    st2 = _tick(p2, v2, http=_mkt(1.0))
    assert _census(st2, "under_min_notional") == 1 and st2["ops"] == 0 and b2["ledger_net"] == 0
    # cancels reserve and commit in one step, as before: 25 TTL cancels
    # under the parallel walk are exactly 20 cancels and 5 ops_capped
    p3 = _pool(conds=[])
    v3 = _SlowVenue(delay=0.005)
    for i, slug in enumerate(_many_books(p3, 25)):
        bk = [x for x in p3.books.values() if x["us_market_slug"] == slug][0]
        p3.add_order(bk, order_id=f"o{i}", us_market_slug=slug, placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)
        v3.rest(f"o{i}", slug=slug)
    st3 = _tick(p3, v3)
    assert len(_cancels(v3)) == 20 and st3["ops"] == 20 and _census(st3, "ops_capped") == 5


def test_the_room_is_taken_before_the_venue_call_so_two_books_cannot_each_spend_the_last_clip(monkeypatch):
    """The room was read in _room_qty and taken off after the venue
    call; three books in flight all read the same room. Now the add is
    re-scaled on the room as it stands and the room is taken with no
    await between (_room_take), before the INSERT and the call. Three
    $90 rests against a $200 day room: 300 + 300 + 66 shares, $199.80,
    whatever the arrival order; a refused create gives its room back."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 200.0)
    p, slugs, http = _increase_world(3)
    v = _SlowVenue(delay=0.005)
    st = _tick(p, v, http=http)
    qtys = sorted(c[3] for c in _places(v))
    assert qtys == [66, 300, 300], qtys
    assert sum(q * 0.30 for q in qtys) <= 200.0 and st["ops"] == 3 and _census(st, "over_room") == 0
    assert st["mirror_day_room"] == 200.0, "the room as _global_guards read it"
    # sequentially the same figures (room_scale is idempotent on its answer)
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 1)
    p1, _s, http1 = _increase_world(3, yielding=False)
    v1 = _Venue()
    st1 = _tick(p1, v1, http=http1)
    assert [c[3] for c in _places(v1)] == [300, 300, 66] and _comparable(st1)["census"] == _comparable(st)["census"]
    # a refused create gives the room back: the first book's post-only
    # refusal leaves the whole $200 to the second and third
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 1)
    p4, s4, http4 = _increase_world(3, yielding=False)
    first = s4[0]

    def _refuse_first(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        if slug == first:
            return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                    "filled_shares": 0.0, "raw": {"status_code": 400, "error": "400 crossing"}}
        v.rest(oid, "BUY", price, qty, slug)
        return {"ok": False, "order_id": oid, "status": "new", "fill_price": None,
                "filled_shares": 0.0, "raw": {}}
    v4 = _Venue(place=_refuse_first)
    st4 = _tick(p4, v4, http=http4)
    rested = [c for c in _places(v4) if c[1] != first]
    assert [c[3] for c in rested] == [300, 300] and _census(st4, "post_only_rejected") == 1
    src = _place_src()
    assert src.index("_room_take(t, est)") < src.index("_SQL_ORDER_INSERT") < src.index("_guarded(t, o[\"id\"], t.pmus.submit_fok")
    assert src.count("_room_give(t, est)") == 3


def test_the_read_once_caches_are_read_once_under_the_parallel_walk_and_a_held_lock_is_never_dropped(monkeypatch):
    """_read_open, _read_protected, _snapshot and _whale_address are
    'first read on need' caches; the second caller used to read "tried,
    still None" mid-read. Under their locks, ten concurrent callers make
    ONE venue read and all read the same answer. _lock_for's garbage
    sweep never drops a lock that is held."""
    p = _pool()
    v = _SlowVenue(delay=0.02)
    v.rest("oid-x", price=0.28)
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW, stats=ml._new_stats())
    monkeypatch.setattr(ml, "_current_stats", t.stats)

    async def _all():
        opens = await asyncio.gather(*[ml._read_open(t) for _ in range(10)])
        snaps = await asyncio.gather(*[ml._snapshot(t, "rn1") for _ in range(10)])
        addrs = await asyncio.gather(*[ml._whale_address(t, "rn1") for _ in range(10)])
        prots = await asyncio.gather(*[ml._read_protected(t) for _ in range(10)])
        return opens, snaps, addrs, prots
    opens, snaps, addrs, prots = _run(_all())
    assert [c for c in v.calls if c[0] == "open_orders"] == [("open_orders", None)]
    assert all(o is opens[0] for o in opens) and len(opens[0]) == 1 and opens[0][0]["order_id"] == "oid-x"
    assert all(s is snaps[0] for s in snaps) and snaps[0][0] == {M: 300.0, N: 0.0}
    assert all(a == "0xabc" for a in addrs) and all(pr is prots[0] for pr in prots)
    assert t.venue_calls == 1 and _census(t.stats, "venue_calls") == 1
    # the lock sweep
    ml._BOOK_LOCKS.clear()
    held = ml._lock_for(7)

    async def _hold():
        async with held:
            for i in range(1, 300):
                ml._lock_for(1000 + i)
            assert ml._lock_for(7) is held, "a held lock survives the sweep"
    _run(_hold())
    assert 7 in ml._BOOK_LOCKS and len(ml._BOOK_LOCKS) <= 202


def test_venue_calls_are_counted_on_the_census_and_the_soft_guard_stops_candidates_never_exits(monkeypatch):
    """Step 2's counter and guard. Every venue REQUEST the tick makes is
    one `venue_calls` event: the fake venue records each of its calls;
    a BUY placement is two requests (preview, create) and the positions
    walk is counted per page (review MEDIUM-3). The GUARD counts the
    writes and the candidates' reads alone (review MEDIUM-5): at
    rules.MIRROR_VENUE_CALLS_PER_TICK the CANDIDATE walk stops
    (`venue_calls_capped`, `capped_tick`); every open book is still
    read and a TTL cancel still goes out past the guard."""
    p = _pool()
    v = _Venue()
    st = _tick(p, v)
    assert _places(v) and len(p.books) == 1
    assert _census(st, "venue_calls") == _expected_calls(v) == len(v.calls) + 2, (st["census"]["venue_calls"], v.calls)
    assert _census(st, "venue_calls_capped") == 0 and st.get("capped_tick") is not True
    # the guard at 3: five books (their reads are not counted) and five
    # candidates. The first candidate's read (1) and its open (a BUY:
    # 2) spend the three, so the second candidate is not read; every
    # book's rest (a TTL-expired one among them) is still cancelled
    monkeypatch.setattr(rules, "MIRROR_VENUE_CALLS_PER_TICK", 3)
    p2 = _pool(conds=[CID] + [f"c{i}" for i in range(1, 5)])
    slugs = _many_books(p2, 5)
    bk = [x for x in p2.books.values() if x["us_market_slug"] == slugs[2]][0]
    p2.add_order(bk, order_id="o-ttl", us_market_slug=slugs[2], placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)
    v2 = _Venue()
    v2.rest("o-ttl", slug=slugs[2])
    st2 = _tick(p2, v2)
    bbos = [c[1] for c in v2.calls if c[0] == "bbo"]
    assert all(s in bbos for s in slugs), "every live book read past the guard"
    assert bbos.count(SLUG) == 2 and len(p2.books) == 6, "one candidate read and opened (then ticked); the rest not"
    assert _census(st2, "venue_calls_capped") == 1 and st2["capped_tick"] is True
    assert _cancels(v2) == [("cancel", "o-ttl", slugs[2])] and st2["ops"] == 2, "the exit is never bounded by it"
    assert _census(st2, "venue_calls") == _expected_calls(v2)
    # a guard of 0 is a tick that opens no book and still manages every
    # exit: he reduced to 100 while we hold 300, the SELL of 200 goes out
    monkeypatch.setattr(rules, "MIRROR_VENUE_CALLS_PER_TICK", 0)
    p3 = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    b3 = p3.add_book(ledger=300)
    v3 = _Venue(held={SLUG: 300})
    st3 = _tick(p3, v3, http=_mkt(100.0))
    pl3 = _places(v3)
    # the SELL IOC, then E14b's same-tick rest of the unfilled 200: two exit ops, neither guarded
    assert [c[3:6] for c in pl3] == [(200, True, IOC_TIF), (200, True, GTC_TIF)] and st3["ops"] == 2
    assert _census(st3, "venue_calls_capped") == 0 and b3["state"] == "live", st3["census"]
    src = inspect.getsource(ml._tick)
    assert src.index("t.guard_calls >= rules.MIRROR_VENUE_CALLS_PER_TICK") > src.index("_walk_books(")
    # the books' reads are never guarded; a candidate's read and every write are
    bbo = inspect.getsource(ml._bbo)
    assert "_venue_call(t, guard=not book)" in bbo
    assert "_venue_call(t, slots, guard=True)" in inspect.getsource(ml._guarded)
    assert "_venue_call(t, guard=True)" in inspect.getsource(ml._cancel_and_settle)


def test_a_rest_at_his_level_takes_one_ioc_on_its_first_tick_and_three_ticks_above_does_not(monkeypatch):
    """Step 4's pin under the E4 addendum ("Remove the 20 second wait
    on entires too"): rules.MIRROR_TAKE_AFTER_S is 0. A rest of ANY age
    with the ask at or under his level: the rest is cancelled and ONE
    IOC goes out at the SAME wire (`take_at_his_level`, `take_placed`);
    the ask three ticks above his level: no IOC, the rest stands
    (`take_refused_price`, `resting_above_level`); the take never fires
    twice for one rest; a plan with NO rest and the ask at his level
    sends the IOC FIRST (`take_first`, tif IOC, nothing cancelled); the
    ask above rests as before and the take fires the tick the ask
    arrives. The entry PRICE rule is unchanged: at or through the wire,
    never above him, no tolerance."""
    assert float(rules.MIRROR_TAKE_AFTER_S) == 0.0
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, placed_ts=NOW - 1)                  # a one-second-old rest at the 0.30 wire
    v = _Venue(ask=0.30, ioc_fill=300.0)
    v.rest("oid-1")
    st = _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    pl = _places(v)
    # the IOC's limit is HIS cent, 0.31 (review HIGH-1), never the rest's wire
    assert len(pl) == 1 and pl[0][2:6] == (0.31, 300, False, "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
    assert _census(st, "take_at_his_level") == 1 and _census(st, "take_placed") == 1
    assert _census(st, "take_first") == 0, "a rest was cancelled: not the take-first"
    assert _census(st, "take_refused_price") == 0 and b["ledger_net"] == 300
    # once: on target, no second take
    v2 = _Venue(ask=0.30, ioc_fill=300.0, held={SLUG: 300})
    st2 = _tick(p, v2, now=NOW + 30)
    assert not _places(v2) and not _cancels(v2) and _census(st2, "take_at_his_level") == 0
    # three ticks above: refused on price, the rest stands
    p3 = _pool()
    b3 = p3.add_book(ledger=0)
    o3 = p3.add_order(b3, placed_ts=NOW - 1)
    v3 = _Venue(ask=0.33, ioc_fill=300.0)
    v3.rest("oid-1")
    st3 = _tick(p3, v3)
    assert not _places(v3) and not _cancels(v3) and p3.orders[o3["id"]]["state"] == "open"
    assert _census(st3, "take_refused_price") == 1 and _census(st3, "resting_above_level") == 1
    assert _census(st3, "take_at_his_level") == 0 and b3["ledger_net"] == 0
    # NO rest, the ask at his level: the IOC goes FIRST -- one IOC at
    # the wire, tif IOC, no rest before it and none cancelled, the book
    # filled on its first tick
    p4 = _pool()
    b4 = p4.add_book(ledger=0)
    v4 = _Venue(ask=0.30, ioc_fill=300.0)
    st4 = _tick(p4, v4)
    pl4 = _places(v4)
    assert len(pl4) == 1 and pl4[0][2:6] == (0.31, 300, False, "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
    assert pl4[0][7] is False and not _cancels(v4)
    assert _census(st4, "take_first") == 1 and _census(st4, "take_at_his_level") == 1
    assert _census(st4, "take_placed") == 1 and _census(st4, "rest_placed") == 0 and b4["ledger_net"] == 300
    o4 = next(iter(p4.orders.values()))
    assert o4["kind"] == "take" and o4["tif"] == "IOC" and o4["state"] == "filled" and o4["maker"] is False
    # NO rest, the ask AT his cent (0.31; the rest's wire is 0.30): the
    # IOC first, at 0.31 -- never a cent above him
    p4b = _pool()
    b4b = p4b.add_book(ledger=0)
    v4b = _Venue(bid=0.30, ask=0.31, ioc_fill=300.0)
    st4b = _tick(p4b, v4b)
    assert [c[2:6] for c in _places(v4b)] == [(0.31, 300, False, "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")]
    assert _census(st4b, "take_first") == 1 and b4b["ledger_net"] == 300 and b4b["avg_cost"] <= 0.31
    # NO rest, the ask ABOVE his cent: a post-only rest at the wire, no
    # IOC, nothing refused by price (the entry never gets a tolerance:
    # the ask a cent over his 0.31 rests)
    for ask in (0.33, 0.32):
        p5 = _pool()
        b5 = p5.add_book(ledger=0)
        v5 = _Venue(ask=ask, ioc_fill=300.0)
        st5 = _tick(p5, v5)
        assert [c[5] for c in _places(v5)] == ["TIME_IN_FORCE_GOOD_TILL_CANCEL"], ask
        assert _places(v5)[0][2] == 0.30 and _places(v5)[0][7] is True and b5["ledger_net"] == 0, ask
        assert _census(st5, "take_first") == 0 and _census(st5, "take_placed") == 0, ask
    # ... and the take fires the tick the ask arrives at his cent: the
    # same rest, cancelled and taken at 0.31
    v6 = _Venue(ask=0.31, ioc_fill=300.0)
    v6.orders = v5.orders
    st6 = _tick(p5, v6, now=NOW + 30)
    assert _cancels(v6) == [("cancel", "oid-1", SLUG)]
    assert [c[5] for c in _places(v6)] == ["TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"] and _places(v6)[0][2] == 0.31
    assert _census(st6, "take_at_his_level") == 1 and b5["ledger_net"] == 300
    # the armed take's price verdict: the book away, the arm cleared,
    # the book rests at the wire and never chases
    p7 = _pool()
    b7 = p7.add_book(ledger=0, take_armed_ts=NOW - 5)
    v7 = _Venue(bid=0.30, ask=0.33, ioc_fill=300.0)
    st7 = _tick(p7, v7)
    assert _census(st7, "take_refused_price") == 1 and b7["take_armed_ts"] is None
    assert [c[5] for c in _places(v7)] == ["TIME_IN_FORCE_GOOD_TILL_CANCEL"], "rests at the wire, never chases"


def test_an_abandon_or_a_trip_on_one_book_stops_the_books_not_yet_started_and_no_in_flight_book_places(monkeypatch):
    """Under the parallel walk a book already in flight when another
    book abandoned the tick (a 429 on its placement) finishes under
    its own checks: _place refuses `tick_abandoned`, and no book not
    yet started is read."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1e9)
    p, slugs, http = _increase_world(12)

    def _rate_limit(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                "filled_shares": 0.0, "raw": {"status_code": 429, "error": "429 slow down"}}
    v = _SlowVenue(delay=0.01, place=_rate_limit)
    st = _tick(p, v, http=http)
    assert st["abandoned"] and st["abandon_reason"] == "rate_limited"
    assert len(_places(v)) <= rules.MIRROR_BOOK_CONCURRENCY, "only the books in flight reached the venue"
    assert len([c for c in v.calls if c[0] == "bbo"]) < 12, "the books not yet started were not read"
    assert all(o["state"] == "rejected" for o in p.orders.values())
    assert "if t.abandoned:" in inspect.getsource(ml._place) and 'return "tick_abandoned"' in _flatten_src()



# ------------------------ 20b. the E2 review's findings, pinned (2026-09-06, review round 1)
#
# HIGH-1 the writes bursting past the pacer under the parallel walk;
# MEDIUM-2 the take's cancel spending the replace budget and holding
# an EXIT behind it, the stale-arm window at short waits; MEDIUM-3 the
# venue-call census missing the preview, the flatten's positions walk
# and the positions pages; MEDIUM-4 the miss streak judged in arrival
# order; MEDIUM-5 forty candidate reads unreachable at the live book
# count under the guard; LOW-6 the in-flight refusal unnamed and
# writing a plan; LOW-7 the room kept on a lost response, behaviourally.
# The reviewer's reproductions, ported with the fixed expectations.

def _take_cycle_world():
    """A book at 100 against his 300 (an increase of 200) with a fresh
    rest of 200 at his level and a venue whose IOCs find nothing: every
    other tick the rest is 20 s old, at/through, and takes for 0 --
    the reviewer's rest -> take -> rest cycle."""
    p = _pool()
    b = p.add_book(ledger=100, avg_cost=0.31)
    p.add_order(b, qty=200, placed_ts=NOW)
    v = _Venue(ask=0.30, ioc_fill=0.0, held={SLUG: 100})
    v.rest("oid-1", qty=200)
    return p, b, v


def test_r_six_books_in_flight_send_their_writes_one_pacer_gap_apart(monkeypatch):
    """HIGH-1. The writes were unpaced (pmus has no pace() on
    submit_fok, cancel_order, close_position) and the walk overlapped
    them: six books finishing their reads together placed together,
    twelve HTTP inside one 0.35 s gap. Every write of this lane now
    claims a gap on the process-wide pacer before it goes (_paced; the
    adapter claims another before a BUY's create, pinned in section
    20c on the real gate). With a real 20 ms gate installed for the
    writes alone, six BUY placements land >= 20 ms apart, one after
    another."""
    import threading
    import time as _time
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1e9)
    # venue_pace.pace's own shape with a 20 ms gap and the REAL sleep
    # (the fixture patches time.sleep to a recorder): the writes' gate
    lock, last = threading.Lock(), [0.0]

    def _gate(s=0.0, slots=1):
        with lock:
            wait = max(0.0, last[0] + 0.02 - _time.monotonic())
            if wait > 0:
                _REAL_SLEEP(wait)
            last[0] = _time.monotonic()
            return wait
    monkeypatch.setattr(ml, "pace", _gate)
    p, slugs, http = _increase_world(6)
    stamps = []

    def _stamp(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        stamps.append(_time.monotonic())
        v.rest(oid, "BUY", price, qty, slug)
        return {"ok": False, "order_id": oid, "status": "new", "fill_price": None,
                "filled_shares": 0.0, "raw": {}}
    v = _SlowVenue(delay=0.005, place=_stamp)
    st = _tick(p, v, http=http)
    assert len(stamps) == 6 and st["ops"] == 6
    gaps = [b - a for a, b in zip(sorted(stamps), sorted(stamps)[1:])]
    assert min(gaps) >= 0.018, gaps                     # a gap a write, never a burst
    assert max(stamps) - min(stamps) >= 5 * 0.018
    # the request count per write, read off the adapter's call shape
    assert ml._write_slots(v.submit_fok, (SLUG, 0.3, 10, False)) == 2
    assert ml._write_slots(v.submit_fok, (SLUG, 0.3, 10, True)) == 1
    assert ml._write_slots(v.cancel_order, ("oid", SLUG)) == 1 and ml._write_slots(v.close_position, (SLUG,)) == 1
    src = inspect.getsource(ml._paced)
    assert "pace(ms.READ_PACING_S)" in src and "slots" not in src, "one claim per request (review round 3, HIGH-1)"


def test_r_take_cycles_burn_the_entry_budget_but_an_exit_or_a_side_change_is_never_gated(monkeypatch):
    """MEDIUM-2a. At the 20 s wait a book that cycles rest -> take
    (IOC, 0 fill) -> rest spends the hour's 12 replaces in ~13 minutes
    and is `take_capped` (the ENTRY churn the budget exists to bound:
    kept). Then he reduces: a SELL of 50 against the standing BUY rest
    of 200. That is a side change and an exit -- it was `replace_capped`
    until the BUY rest's TTL (up to 600 s of an exit held behind an
    entry budget); now the BUY rest is cancelled and the SELL goes out
    the same tick."""
    p, b, v = _take_cycle_world()
    takes, capped_at, t = 0, None, NOW
    for k in range(1, 40):
        t = NOW + 30 * k
        vk = _Venue(ask=0.30, ioc_fill=0.0, held={SLUG: 100})
        vk.orders = v.orders
        st = _tick(p, vk, now=t)
        takes += _census(st, "take_placed")
        if _census(st, "take_capped"):
            capped_at = k
            break
        for c in _places(vk):
            if c[5] == "TIME_IN_FORCE_GOOD_TILL_CANCEL":
                vk.rest(f"oid-{vk.n}", qty=c[3])
        v = vk
    assert takes == rules.MIRROR_MAX_REPLACES_PER_HOUR == 12, takes
    assert capped_at is not None and capped_at <= 26, capped_at
    vc = _Venue(ask=0.30, ioc_fill=0.0, held={SLUG: 100})
    vc.orders = v.orders
    st = _tick(p, vc, now=t + 30)
    assert _census(st, "take_capped") == 1 and not _cancels(vc) and not _places(vc), "an entry take, still gated"
    v = vc
    # HE REDUCES: his net 50, we hold 100 -> a SELL of 50 over a BUY rest
    p.fills = _his(300, sold=250)
    p.snap = {M: 50.0, N: 0.0}
    p.snap_at = t + 30
    v2 = _Venue(ask=0.30, ioc_fill=0.0, held={SLUG: 100})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=t + 60, http=_mkt(50.0))
    assert b["target"] == 50 and b["last_plan"]["side"] == "SELL_LONG", b["last_plan"]
    assert _census(st2, "replace_capped") == 0 and len(_cancels(v2)) == 1
    sells = [c for c in _places(v2) if c[4] is True and c[3] == 50]
    # the SELL IOC (filled nothing), then E14b's same-tick rest of the 50 at his cent
    assert [c[5] for c in sells] == [IOC_TIF, GTC_TIF], (st2["census"], _places(v2))
    assert not [o for o in p.orders.values() if o["state"] == "open" and o["side"] == "BUY_LONG"]
    # the predicate, at the unit
    from sportsassets.analytics.mirror import Plan
    assert ml._exit_or_flip(b, Plan(SELL, 50, 0.30, "reduce"), {"side": "BUY_LONG"}) is True
    assert ml._exit_or_flip(b, Plan(BUY, 50, 0.30, "rest"), {"side": "SELL_LONG"}) is True
    assert ml._exit_or_flip(b, Plan(BUY, 50, 0.30, "rest"), {"side": "BUY_LONG"}) is False
    assert ml._exit_or_flip(b, None, {"side": "BUY_LONG"}) is False


def test_r_the_armed_take_path_is_not_gated_and_an_ioc_row_is_never_a_replace():
    """MEDIUM-2c. A rest that has waited is refused `take_capped` at 12
    replaces (an entry); a post-only-armed take (no rest to cancel)
    still fires past 12; and 12 cancelled IOC 'take' rows count for
    nothing -- _SQL_REPLACES filters tif GTC/GTD, so an IOC-first entry
    with no rest cancelled is not a replace (E4 builds on this)."""
    p = _pool()
    b = p.add_book(ledger=0)
    for _ in range(rules.MIRROR_MAX_REPLACES_PER_HOUR):
        p.add_order(b, state="cancelled", reason="take", done_at=NOW - 100, order_id=None)
    p.add_order(b, placed_ts=NOW - rules.MIRROR_TAKE_AFTER_S)
    v = _Venue(ask=0.30, ioc_fill=300.0)
    v.rest("oid-1")
    st = _tick(p, v)
    assert _census(st, "take_capped") == 1 and not _places(v) and not _cancels(v)
    assert _census(st, "take_at_his_level") == 0, "the census does not name a take the budget refused"
    # the armed path, same budget spent
    p2 = _pool()
    b2 = p2.add_book(ledger=0, take_armed_ts=NOW - rules.MIRROR_TAKE_AFTER_S)
    for _ in range(rules.MIRROR_MAX_REPLACES_PER_HOUR):
        p2.add_order(b2, state="cancelled", reason="take", done_at=NOW - 100, order_id=None)
    v2 = _Venue(bid=0.30, ask=0.30, ioc_fill=300.0)
    st2 = _tick(p2, v2)
    pl = _places(v2)
    assert len(pl) == 1 and pl[0][5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" and _census(st2, "take_placed") == 1
    assert b2["ledger_net"] == 300
    # twelve cancelled IOC rows: not replaces, the rest-path take fires
    p3 = _pool()
    b3 = p3.add_book(ledger=0)
    for _ in range(rules.MIRROR_MAX_REPLACES_PER_HOUR):
        p3.add_order(b3, state="cancelled", reason="take", tif="IOC", done_at=NOW - 100, order_id=None)
    p3.add_order(b3, placed_ts=NOW - rules.MIRROR_TAKE_AFTER_S)
    v3 = _Venue(ask=0.30, ioc_fill=300.0)
    v3.rest("oid-1")
    st3 = _tick(p3, v3)
    assert _census(st3, "take_placed") == 1 and _census(st3, "take_capped") == 0 and b3["ledger_net"] == 300
    assert "tif IN ('GTC', 'GTD')" in _flat(ml._SQL_REPLACES)


def test_r_the_stale_arm_window_has_a_sixty_second_floor(monkeypatch):
    """MEDIUM-2b. Twice the 20 s wait is 40 s; at E4's 0 s wait it would
    be 0 and every armed take stale on the next tick. The window is
    max(2 x wait, 60 s): an arm 50 s old still takes, one 61 s old is
    stale by name. Under a LENGTHENED wait the stale arm's book rests
    first (E2's rule); at the default 0 s wait (the E4 addendum: an
    entry takes on the price alone, never through the arm) the stale
    arm clears itself by name and the crossing book still takes FIRST."""
    assert 2 * rules.MIRROR_TAKE_AFTER_S < ml.TAKE_ARM_STALE_MIN_S == 60.0
    p = _pool()
    b = p.add_book(ledger=0, take_armed_ts=NOW - 50)
    v = _Venue(bid=0.30, ask=0.30, ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[5] for c in _places(v)] == ["TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert _census(st, "take_arm_stale") == 0 and _census(st, "take_placed") == 1 and b["ledger_net"] == 300
    # the default wait: the stale arm is named and cleared, the IOC goes on the price
    p2 = _pool()
    b2 = p2.add_book(ledger=0, take_armed_ts=NOW - 61)
    v2 = _Venue(bid=0.30, ask=0.30, ioc_fill=300.0)
    st2 = _tick(p2, v2)
    assert [c[5] for c in _places(v2)] == ["TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert _census(st2, "take_arm_stale") == 1 and b2["take_armed_ts"] is None and b2["ledger_net"] == 300
    assert _census(st2, "take_first") == 1
    # a lengthened wait: the stale arm's book rests first, as E2 has it
    _lengthened_wait(monkeypatch, 20.0)
    p3 = _pool()
    b3 = p3.add_book(ledger=0, take_armed_ts=NOW - 61)
    v3 = _Venue(bid=0.30, ask=0.30, ioc_fill=300.0)
    st3 = _tick(p3, v3)
    assert [c[5] for c in _places(v3)] == ["TIME_IN_FORCE_GOOD_TILL_CANCEL"]
    assert _census(st3, "take_arm_stale") == 1 and b3["take_armed_ts"] is None and b3["ledger_net"] == 0
    p4 = _pool()
    b4 = p4.add_book(ledger=0, take_armed_ts=NOW - 50)
    v4 = _Venue(bid=0.30, ask=0.30, ioc_fill=300.0)
    st4 = _tick(p4, v4)
    assert [c[5] for c in _places(v4)] == ["TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"] and b4["ledger_net"] == 300
    # the armed take with no rest standing IS a take-first (the arm's
    # age is the wait; nothing was cancelled), whatever the wait
    assert _census(st4, "take_arm_stale") == 0 and _census(st4, "take_first") == 1


def test_r_a_partial_ioc_books_the_fill_and_the_remainder_rests_in_the_same_tick():
    """C(iii), under the E4 addendum (item 4: the post-only rest for the
    unfilled part is the only standing order the plan leaves). A take
    that fills 100 of 300: the fill is booked, the IOC's own remainder
    is the venue's cancel and never rests, and the 200 rests as a GTC
    at the wire IN THE SAME TICK (`_entry_take`; it waited a tick under
    E2) -- three ops: the cancel, the IOC, the rest. The next tick
    keeps that rest."""
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, placed_ts=NOW - rules.MIRROR_TAKE_AFTER_S)
    v = _Venue(ask=0.30, ioc_fill=100.0)
    v.rest("oid-1")
    st = _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    # the IOC at his cent 0.31 (review HIGH-1), the remainder's rest at the 0.30 wire
    assert [c[2:6] for c in _places(v)] == [(0.31, 300, False, "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"),
                                            (0.30, 200, False, "TIME_IN_FORCE_GOOD_TILL_CANCEL")]
    take = [x for x in p.orders.values() if x["kind"] == "take"][0]
    assert take["state"] == "cancelled" and take["booked_filled"] == 100.0 and take["filled"] == 100.0
    rest = [x for x in p.orders.values() if x["state"] == "open"]
    assert len(rest) == 1 and rest[0]["qty"] == 200 and rest[0]["kind"] == "increase"
    assert b["ledger_net"] == 100 and b["open_order_id"] == rest[0]["id"]
    assert _census(st, "partial_fill") == 1 and _census(st, "take_placed") == 1 and _census(st, "rest_placed") == 1
    assert st["ops"] == 3, "the cancel, the IOC and the remainder's rest"
    v2 = _Venue(ask=0.32, held={SLUG: 100})
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30)
    assert not _places(v2) and not _cancels(v2) and _census(st2, "open_order_pending") == 1


def test_r_an_insert_that_raises_releases_its_op_slot_so_the_next_book_gets_the_op(monkeypatch):
    """A. A raise BEFORE the write (the row INSERT blips) hands the op
    back: with a budget of one, the second book still places."""
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 1)
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1e9)
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 1)
    p, slugs, http = _increase_world(2)
    first = slugs[0]
    orig = p._run

    def _run_(kind, sql, a):
        if "ml-order-insert" in sql and a and a[2] == first:
            raise RuntimeError("db blip on the first book's INSERT")
        return orig(kind, sql, a)
    p._run = _run_
    v = _SlowVenue(delay=0.005)
    st = _tick(p, v, http=http)
    assert _census(st, "book_error") == 1
    assert len(_places(v)) == 1 and _places(v)[0][1] == slugs[1] and st["ops"] == 1
    assert _census(st, "ops_capped") == 0, "a released slot is not a spent op"


def test_r_a_lost_response_keeps_the_room_taken_for_the_rest_of_the_tick(monkeypatch):
    """LOW-7, behaviourally. Book 1's placement raises after the venue
    rested it (adopted by the lost-response search); book 2 then reads
    the room book 1 took: 33 shares of a $100 room, never 300."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 100.0)
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 1)
    p, slugs, http = _increase_world(2, yielding=False)
    first = slugs[0]

    def _raise_first(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        v.rest(oid, "BUY", price, qty, slug)
        if slug == first:
            raise RuntimeError("socket closed after the venue rested it")
        return {"ok": False, "order_id": oid, "status": "new", "fill_price": None,
                "filled_shares": 0.0, "raw": {}}
    v = _Venue(place=_raise_first)
    st = _tick(p, v, http=http)
    qtys = [(c[1], c[3]) for c in _places(v)]
    assert qtys == [(slugs[0], 300), (slugs[1], 33)], qtys
    assert _census(st, "rest_placed") == 2 and st["ops"] == 2


def test_r_the_miss_streak_is_judged_in_walk_order_not_arrival_order(monkeypatch):
    """MEDIUM-4. Six books, ids interleaved good/bad/good/bad/good/bad,
    the bad reads instant and the good ones slow: under N=6 the three
    failures land first, in a row. The streak is judged in WALK order
    (_walk_streak), so the parallel run does not abandon, exactly like
    the sequential one, and every stat is the same."""
    p = _pool(conds=[])
    slugs = _many_books(p, 6)
    bad = [slugs[1], slugs[3], slugs[5]]
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 1)
    seq = _tick(p, _PerSlugVenue(delay=0.02, bad=bad))
    assert not seq["abandoned"] and _census(seq, "no_quote") == 3 and seq["books_live"] == 6
    p2 = _pool(conds=[])
    _many_books(p2, 6)
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 6)
    par = _tick(p2, _PerSlugVenue(delay=0.02, bad=bad))
    assert not par["abandoned"], par["census"]
    assert _comparable(par) == _comparable(seq) and ml._backoff_until == 0.0
    # the judge at the unit: a run is counted only over judgeable books
    # (read landed or tick done), in `ordered`
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    monkeypatch.setattr(ml, "_current_stats", t.stats)
    books = [{"id": i, "us_market_slug": f"s{i}"} for i in range(6)]
    t.walk_order = books
    t.book_reads = {"s1": ("no_quote", None), "s3": ("no_quote", None), "s5": ("no_quote", None)}
    assert ml._walk_streak(t, books) == 0 and not t.abandoned, "s0 not judgeable yet: nothing counted"
    t.book_reads["s0"] = False
    assert ml._walk_streak(t, books) == 1
    t.book_reads.update({"s2": False, "s4": False})
    assert ml._walk_streak(t, books) == 1 and not t.abandoned
    t.book_reads.update({"s2": ("no_quote", None), "s4": ("no_quote", None)})
    t.walk_done.update({0, 1, 2, 3, 4, 5})
    assert ml._walk_streak(t, books) >= 3 and t.abandoned and t.stats["abandon_reason"] == "no_quote"
    # the walk's trailing run is what the candidates start from
    src = inspect.getsource(ml._walk_books)
    assert "trailing = _judge_walk(t, ordered, None)" in src and "_judge_walk(t, t.walk_order" in inspect.getsource(ml._miss)


def test_r_an_in_flight_exit_refused_under_an_abandon_is_named_and_writes_no_plan(monkeypatch):
    """LOW-6. Book 41's increase 429s (the tick abandons) while book 42's
    SELL of 200 (he reduced 300 -> 100) is in flight: the SELL is
    refused `abandoned_in_flight` (named), NO plan is written for it --
    its updated_at stays, so E1's walk reads it as unreached and walks
    its game first -- and it goes out on the tick after the backoff."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1e9)
    fills = [_fill("tokL0", "BUY", 300.0, 0.30, NOW - 3000)] + _his(300, sold=200)
    snap = {"tokL0": 300.0, "tokO0": 0.0, M: 100.0, N: 0.0}
    p = _YieldingPool(fills=fills, snap=snap, snap_at=NOW - 40, ratio_fills=_ratio_fills(), conds=[])
    slugs = _many_books(p, 1)
    p.token_index.update({"tokL0": 1, "tokO0": 0})
    p.token_cid.update({"tokL0": "0xbook0", "tokO0": "0xbook0"})
    b = p.add_book(ledger=300)
    touched = b["updated_ts"]
    rows = [{"conditionId": "0xbook0", "asset": "tokL0", "size": 300.0},
            {"conditionId": "0xbook0", "asset": "tokO0", "size": 0},
            {"conditionId": CID, "asset": M, "size": 100.0}, {"conditionId": CID, "asset": N, "size": 0}]
    http = _HttpByCid(rows=rows)

    import threading
    rate_limited = threading.Event()

    def _rate_limit(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        if slug == slugs[0]:
            rate_limited.set()
            return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                    "filled_shares": 0.0, "raw": {"status_code": 429, "error": "429 slow down"}}
        v.rest(oid, "SELL" if sell else "BUY", price, qty, slug)
        return {"ok": False, "order_id": oid, "status": "new", "fill_price": None,
                "filled_shares": 0.0, "raw": {}}
    orig_fetchrow = p.fetchrow

    async def _slow_shadow(sql, *a):
        if "ml-shadow-latest" in sql and a and a[1] == CID:
            # the SELL book is past its abandon check (right after its
            # read) and waits here, in flight, until the 429 has landed:
            # its _place then meets t.abandoned -- deterministically
            for _ in range(400):
                if rate_limited.is_set():
                    break
                await asyncio.sleep(0.005)
            assert rate_limited.is_set(), "the 429 never came"
            await asyncio.sleep(0.02)
        return await orig_fetchrow(sql, *a)
    p.fetchrow = _slow_shadow

    class _Delays(_SlowVenue):
        def bbo_read(self, client, slug):
            # the SELL book's read lands first: it passes its post-read
            # abandon check before the increase's placement 429s
            _REAL_SLEEP(0.03 if slug == slugs[0] else 0.005)
            return _Venue.bbo_read(self, client, slug)
    v = _Delays(delay=0.0, place=_rate_limit, held={SLUG: 300})
    st = _tick(p, v, http=http)
    assert st["abandoned"] and st["abandon_reason"] == "rate_limited"
    assert not [c for c in _places(v) if c[1] == SLUG], "the SELL was not sent"
    assert _census(st, "abandoned_in_flight") == 1 and _census(st, "tick_abandoned") == 1
    assert b["last_reason"] is None and b["updated_ts"] == touched, "no plan written: unreached"
    # since review round 3 (MEDIUM-3) the placement-429 abandon skips the
    # 60 s backoff while the pacer's circuit holds: the next tick runs
    # and the held exit goes out 30 s later, not a minute later
    assert _census(st, "backoff_skipped_circuit") == 1 and ml._backoff_until == 0.0
    v3 = _Venue(held={SLUG: 300})
    st3 = _tick(p, v3, now=NOW + 30, http=http, keep_backoff=True)
    assert not st3.get("skipped_backoff")
    pl = [c for c in _places(v3) if c[1] == SLUG]
    # the SELL IOC, then E14b's same-tick rest of the unfilled 200
    assert [c[3:6] for c in pl] == [(200, True, IOC_TIF), (200, True, GTC_TIF)], (st3["census"], _places(v3))
    assert b["last_reason"] is not None and b["updated_ts"] != touched
    src = inspect.getsource(ml._tick_book)
    assert 'if reason != "tick_abandoned":' in src


def test_r_place_and_flatten_send_refuse_under_abandon_without_a_venue_call_and_a_cancel_does_not(monkeypatch):
    """B, at the unit: _place under t.abandoned sends nothing, writes
    no row, names the refusal; _cancel_and_settle has no such check (a
    cancel still goes out on an abandoned tick, as before E2)."""
    from sportsassets.analytics.mirror import Plan
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue()
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW, stats=ml._new_stats())
    monkeypatch.setattr(ml, "_current_stats", t.stats)
    t.abandoned = True
    r = ml._Reading("rn1", b["condition_id"], SLUG, M, N, [], 300.0, 0.0, {}, 10.0, False, True, True,
                    300.0, 0.0, 0.30, 0.32, 0.31, 0.0, 0.0, None, True, None, None, None, None,
                    venue_state="MARKET_STATE_OPEN")
    pl = Plan(BUY, 300, 0.30, "rest")
    out = _run(ml._place(t, b, r, "increase", "BUY", 0.30, 300, 0.31, pl, {"target": 300}))
    assert out == "tick_abandoned" and not v.calls and not p.orders and t.ops == 0 and t.ops_pending == 0
    assert _census(t.stats, "abandoned_in_flight") == 1 and t.venue_calls == 0
    assert "t.abandoned" not in inspect.getsource(ml._cancel_and_settle)


class _PagedPortfolio:
    """A positions walk of `pages` pages, the last carrying `held`."""

    def __init__(self, pages, held):
        self.pages, self.held, self.calls = pages, held, 0

    def positions(self, q):
        self.calls += 1
        i = int(q.get("cursor") or 0)
        last = i + 1 >= self.pages
        return {"positions": ({s: {"netPosition": v, "cost": v * 0.31} for s, v in self.held.items()}
                              if last else {f"page{i}-slug": {"netPosition": 0}}),
                "nextCursor": "" if last else str(i + 1), "eof": last}


def test_r_venue_calls_count_the_preview_the_positions_pages_and_the_flattens_walk(monkeypatch):
    """MEDIUM-3. A BUY placement is two requests (preview, create) and
    counts two; the tick's positions walk counts every page it read
    (ms.account_positions_walk, per call -- review round 2, LOW-b); the
    flatten's own reading of the venue's positions (ml._pm_held,
    le._pm_held's arithmetic) pages through this lane's pacer and
    counts each page."""
    import sportsassets.pmus as pmus
    assert "client.orders.preview(" in inspect.getsource(pmus.submit_fok)
    g = inspect.getsource(ml._guarded)
    assert "slots = _write_slots(fn, args)" in g and "_venue_call(t, slots, guard=True)" in g
    fs = inspect.getsource(ml._flatten_send)
    assert "await _pm_held(t, r.slug)" in fs and "le._pm_held(" not in fs
    tick_src = inspect.getsource(ml._tick)
    assert "t.positions, pages, limited = await ms.account_positions_walk(t.pmus)" in tick_src
    assert "_venue_call(t, pages)" in tick_src and "account_positions_pages" not in inspect.getsource(ml)
    # a BUY counts two, a SELL one: the census against the fake's recorded calls
    p = _pool()
    v = _Venue()
    st = _tick(p, v)
    assert [c[4] for c in _places(v)] == [False] and _census(st, "venue_calls") == _expected_calls(v)
    # the positions walk, three pages: three counts, PER CALL
    v3 = _Venue()
    v3.portfolio = _PagedPortfolio(3, {SLUG: 0})
    assert _run(ms.account_positions(v3)) == {"page0-slug": 0.0, "page1-slug": 0.0, SLUG.lower(): 0.0}
    assert _run(ms.account_positions_walk(v3))[1:] == (3, False)
    st3 = _tick(_pool(), v3)
    assert _census(st3, "venue_calls") == _expected_calls(v3) == len(v3.calls) + 3 + 1
    # the flatten's reading: the real reader on a two-page account, paced
    # (ml.pace, recorded) and counted per page; le._pm_held's arithmetic
    monkeypatch.setattr(ml, "_pm_held", _REAL_PM_HELD)
    paced = []
    monkeypatch.setattr(ml, "pace", lambda s=ms.READ_PACING_S, slots=1: paced.extend([s] * int(slots)))
    v4 = _Venue()
    v4.portfolio = _PagedPortfolio(2, {SLUG: -300})
    t = ml._Tick(pool=p, pmus=v4, http=None, now=NOW, stats=ml._new_stats())
    monkeypatch.setattr(ml, "_current_stats", t.stats)
    assert _run(ml._pm_held(t, SLUG)) == (300, 0.31)
    assert t.venue_calls == 2 and _census(t.stats, "venue_calls") == 2 and paced == [ms.READ_PACING_S] * 2
    assert _run(ml._pm_held(t, "never-held")) == (0, None) and t.venue_calls == 4
    assert "_fetch_all_positions_sync" not in inspect.getsource(ml)


def test_r_forty_candidate_reads_are_reachable_at_forty_six_books_at_the_default_guard():
    """MEDIUM-5. 46 live books and 41 candidates at the DEFAULT guard
    (60): the books' reads do not count, so the candidate walk reads
    its whole budget of 40 (`capped_tick` from its own budget, never
    `venue_calls_capped`)."""
    assert rules.MIRROR_VENUE_CALLS_PER_TICK == 80 and ml.MAX_MARKETS_PER_TICK == 40
    p = _pool(conds=[f"c{i}" for i in range(41)])
    _many_books(p, 46)
    v = _Venue()
    st = _tick(p, v)
    bbos = [c[1] for c in v.calls if c[0] == "bbo"]
    assert st["books_live"] >= 46 and st["reads"] == len(bbos)
    # E6: the soft guard still never counts the books' reads (the whole
    # 40 is reachable under IT), but the tick's venue-call budget (60)
    # leaves the candidate stage 60 - 48 = 12 after the positions walk,
    # the open-orders read and the 46 book reads: 12 candidates, over
    # the floor of 10
    assert len(bbos) - st["books_live"] == 12, (len(bbos), st["books_live"])
    assert st["capped_tick"] is True and _census(st, "venue_calls_capped") == 0
    assert _census(st, "venue_calls") == _expected_calls(v)



# ------------------------ 20c. the E2 review's second round, pinned (2026-09-07)
#
# HIGH-A the two-slot claim was two claims with the gate released
# between (six contending BUYs: twelve requests inside ~five gaps);
# HIGH-B the venue's 429s (five HTML 429s in 1.5 h at ~1 req/s before
# E2 raised the rate): a circuit on the pacer's GAP, every 429 site
# named, the live lane's own candidate budget, the guard at 80;
# LOW-b/d/e/f. The reviewer's reproductions ported with the fixed
# expectations, and the circuit pinned on its own.

def _real_pacer(monkeypatch):
    """The REAL venue_pace with the REAL sleep (the fixture patches
    time.sleep to a recorder) and a clean gate: what the round-3 pins
    drive -- never a fake gate, whose assumptions round 2's hole hid."""
    from sportsassets import venue_pace
    monkeypatch.setattr(venue_pace.time, "sleep", _REAL_SLEEP)
    monkeypatch.setattr(venue_pace, "_last", 0.0)
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)
    return venue_pace


def _pairwise_min(ts):
    ts = sorted(ts)
    return min(b - a for a, b in zip(ts, ts[1:]))


def test_r2_every_request_claims_its_own_gap_so_contending_buys_and_readers_are_pairwise_a_gap_apart(monkeypatch):
    """HIGH-A (round 2) and HIGH-1 (round 3), on the REAL gate. One claim
    per REQUEST, never a reservation: `_paced` claims a gap before the
    preview and the adapter claims its own before the create
    (pmus.submit_fok paced_pair), so with the preview's HTTP SLOWER
    than the gap (80 ms on a 50 ms gap, the live shape: ~0.65 s a
    request against 0.35 s) six contending writers and three readers
    still land every request >= one gap from every other -- the
    reserved-slot design recorded the create at its reserved time and
    put it and the next claimant inside one gap. The pacer lock is not
    held across the HTTP call (a read asking meanwhile is not starved
    by it)."""
    import threading
    venue_pace = _real_pacer(monkeypatch)
    GAP = 0.05
    monkeypatch.setattr(ms, "READ_PACING_S", GAP)
    monkeypatch.setattr(ml, "pace", venue_pace.pace)
    reqs, start = [], threading.Barrier(9)

    def adapter_write(i):
        # the adapter's shape: the preview (its HTTP slower than the gap),
        # then the create behind ITS OWN claim
        reqs.append(("preview", time.monotonic()))
        _REAL_SLEEP(GAP * 1.6)
        venue_pace.pace(GAP)
        reqs.append(("create", time.monotonic()))
        return {}

    def writer(i):
        start.wait()
        ml._paced(adapter_write, i)

    def reader(i):
        start.wait()
        venue_pace.pace(GAP)
        reqs.append(("read", time.monotonic()))
    ths = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    ths += [threading.Thread(target=reader, args=(i,)) for i in range(3)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    assert len(reqs) == 15
    assert _pairwise_min([t for _, t in reqs]) >= GAP * 0.9, sorted(reqs, key=lambda r: r[1])
    # no contention, the same latency: the reader after a late create waits a whole gap
    monkeypatch.setattr(venue_pace, "_last", 0.0)
    t0 = time.monotonic()
    venue_pace.pace(GAP)                                  # the preview's claim
    _REAL_SLEEP(GAP * 1.6)                                # its HTTP
    venue_pace.pace(GAP)                                  # the create's own claim
    create_at = time.monotonic()
    _REAL_SLEEP(0.01)
    venue_pace.pace(GAP)
    read_at = time.monotonic()
    assert create_at - t0 >= GAP * 1.5 and read_at - create_at >= GAP * 0.9, (create_at - t0, read_at - create_at)
    # the lock is released at the claim: a 300 ms 'HTTP call' inside _paced never blocks a reader
    started = threading.Event()
    started_at = [0.0]

    def slow_write(*a, **k):
        started_at[0] = time.monotonic()
        started.set()
        _REAL_SLEEP(0.3)
        return {"ok": True}
    th = threading.Thread(target=lambda: ml._paced(slow_write, "x"))
    th.start()
    assert started.wait(3.0)
    venue_pace.pace(GAP)
    got_at = time.monotonic()
    th.join()
    assert got_at - started_at[0] < 0.1, "the read waited out the write's HTTP call"
    # the reservation is gone from the primitive and from the lane
    assert not hasattr(venue_pace, "pace_reserved") and "slots" not in inspect.signature(venue_pace.pace).parameters
    assert "pace(ms.READ_PACING_S)" in inspect.getsource(ml._paced) and "slots" not in inspect.signature(ml._paced).parameters


def test_r2_the_adapter_claims_its_own_gap_between_the_preview_and_the_create(monkeypatch):
    """LOW-a (round 2) as round 3 rebuilt it. pmus.submit_fok(paced_pair=True)
    -- what _guarded passes for every submit_fok -- claims a gap on the
    REAL pacer before its create, so the create lands at least
    MIN_GAP_S after the preview whatever the preview's HTTP took; off,
    the pair is back to back and nothing waits (the copy lane's shape,
    byte-identical)."""
    import sportsassets.pmus as pmus
    from tests.test_pmus_post_only import _Orders, _install
    venue_pace = _real_pacer(monkeypatch)
    stamps = []

    def _preview(params):
        stamps.append(("preview", time.monotonic()))
        _REAL_SLEEP(0.01)                                 # the preview's HTTP
        return {"order": {"cashOrderQty": {"value": "30.00", "currency": "USD"}}}

    def _create(params):
        stamps.append(("create", time.monotonic()))
        return {"id": "o1", "executions": []}
    _install(monkeypatch, _Orders(create=_create, preview=_preview))
    venue_pace.pace(venue_pace.MIN_GAP_S)                 # what _paced does before the preview
    pmus.submit_fok(SLUG, 0.30, 100, paced_pair=True)
    (_, t_prev), (_, t_create) = stamps
    assert t_create - t_prev >= venue_pace.MIN_GAP_S * 0.95, (t_create - t_prev)
    assert venue_pace._last >= t_create - 1e-3, "the create's claim is recorded on the gate"
    stamps.clear()
    pmus.submit_fok(SLUG, 0.30, 100)                      # every other caller: back to back
    (_, t_prev), (_, t_create) = stamps
    assert t_create - t_prev < 0.05
    g = inspect.getsource(ml._guarded)
    assert 'kwargs = {**kwargs, "paced_pair": True}' in g and "slots=" not in g
    src = inspect.getsource(pmus.submit_fok)
    assert src.index("client.orders.preview(") < src.index("pace()") < src.index("client.orders.create(")
    assert "pace_reserved" not in src


def test_r2_penalize_never_waits_behind_a_sleeping_pacer(monkeypatch):
    """MEDIUM-2 (round 3). penalize() is one float store under its own
    tiny lock: with six threads queued in the gate (each holding the
    gate's lock through its sleep), a 429 handled on the event loop
    returns in under 5 ms and the loop is not stalled."""
    import threading
    venue_pace = _real_pacer(monkeypatch)
    gap = 0.1
    stop = threading.Event()

    def pacer():
        while not stop.is_set():
            venue_pace.pace(gap)
    ths = [threading.Thread(target=pacer) for _ in range(6)]
    for th in ths:
        th.start()
    _REAL_SLEEP(0.05)

    async def main():
        late = []

        async def ticker():
            for _ in range(20):
                t0 = time.monotonic()
                await asyncio.sleep(0.005)
                late.append(time.monotonic() - t0 - 0.005)
        tk = asyncio.create_task(ticker())
        await asyncio.sleep(0.02)
        t0 = time.monotonic()
        venue_pace.penalize()                             # on the loop, as _rate_limited does
        blocked = time.monotonic() - t0
        await tk
        return blocked, max(late)
    try:
        blocked, worst = asyncio.run(main())
    finally:
        stop.set()
        for th in ths:
            th.join()
    assert blocked < 0.005, blocked
    assert worst < gap * 0.5, worst
    src = inspect.getsource(venue_pace.penalize)
    assert "with _penalty_lock:" in src and "with _lock:" not in src


def test_r2_one_429_doubles_every_lanes_gap_for_ten_minutes_and_the_penalty_expires(monkeypatch):
    """HIGH-B (1). venue_pace.penalize(): the gap is PENALTY_MULT x for
    PENALTY_S from the last 429, for every caller of pace (the live
    lane's reads and writes, the shadow's _paced_bbo and positions
    walk), and lifts on its own."""
    from sportsassets import venue_pace
    assert venue_pace.PENALTY_MULT == 2.0 and venue_pace.PENALTY_S == 600.0
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)
    now = time.monotonic()
    assert venue_pace.penalty_left(now) == 0.0 and venue_pace.effective_gap(0.35) == 0.35
    until = venue_pace.penalize(now)
    assert until == pytest.approx(now + 600.0) and venue_pace.penalty_left(now + 1) == pytest.approx(599.0)
    assert venue_pace._gap(0.35, now + 599) == pytest.approx(0.70) and venue_pace._gap(0.35, now + 601) == 0.35
    # a burst restarts the window from the latest 429
    assert venue_pace.penalize(now + 100) == pytest.approx(now + 700.0)
    # the gate itself honours it: a claim after a claim waits the doubled gap
    slept = []
    monkeypatch.setattr(venue_pace.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(venue_pace, "_last", time.monotonic())
    venue_pace.pace(0.35)
    assert slept and 0.68 <= slept[-1] <= 0.70, slept
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)
    monkeypatch.setattr(venue_pace, "_last", time.monotonic())
    venue_pace.pace(0.35)
    assert 0.33 <= slept[-1] <= 0.35, slept
    # the shadow's pacer is the same gate
    assert ms.pace is venue_pace.pace or ms.pace.__name__ == "<lambda>"       # the fixture patches it
    assert "from ..venue_pace import pace" in pathlib.Path(ms.__file__).read_text()


def test_r2_a_429_on_a_quote_read_is_named_rate_limited_trips_the_circuit_and_is_not_an_outage_miss(monkeypatch):
    """HIGH-B / MEDIUM. bbo_read names a RateLimitError in `error`: the
    read is `rate_limited` (never `no_quote`), the pacer's circuit is
    tripped, the market is refused for the tick and the outage streak
    is untouched -- three in a row (walk order) abandon nothing; the
    good books still tick and place."""
    from sportsassets import venue_pace
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1e9)
    p, slugs, http = _increase_world(6)
    touched = {b["id"]: b["updated_ts"] for b in p.books.values()}

    class _RateLimited(_SlowVenue):
        def bbo_read(self, client, slug):
            self.calls.append(("bbo", slug))
            if slug in slugs[:3]:
                return {"bid": None, "ask": None, "state": None, "error": "RateLimitError"}
            _REAL_SLEEP(0.03)
            return {"bid": self.bid, "ask": self.ask, "state": self.state, "error": None}
    v = _RateLimited(delay=0.0)
    st = _tick(p, v, http=http)
    assert not st["abandoned"] and _census(st, "rate_limited") == 3 and _census(st, "no_quote") == 0
    assert _census(st, "tick_abandoned") == 0 and ml._backoff_until == 0.0
    assert venue_pace.penalty_left() > 590.0, "the circuit is on"
    assert len(_places(v)) == 3 and st["ops"] == 3 and sorted(c[1] for c in _places(v)) == sorted(slugs[3:])
    limited = [b for b in p.books.values() if b["us_market_slug"] in slugs[:3]]
    assert all(b["last_reason"] == "no_mark" and b["updated_ts"] != touched[b["id"]] for b in limited), \
        "refused by name for the tick, planned nothing, walked again next tick"
    # at the unit: neither a miss nor a reset on the live streak
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW, stats=ml._new_stats())
    monkeypatch.setattr(ml, "_current_stats", t.stats)
    t.misses = 2
    assert _run(ml._bbo(t, slugs[0])) == (None, None) and t.misses == 2 and not t.abandoned
    assert _census(t.stats, "rate_limited") == 1 and t.book_reads == {}
    assert ms.is_rate_limit("RateLimitError") and ms.is_rate_limit(RuntimeError("HTTP 429 <!doctype html>"))
    assert not ms.is_rate_limit("timeout") and not ms.is_rate_limit(None) and not ms.is_rate_limit(RuntimeError("502"))


def test_r2_a_429_on_a_cancel_and_on_the_positions_walk_are_named_and_trip_the_circuit(monkeypatch):
    """HIGH-B. cancel_order never raises: a 429 is `ok: False` with the
    error text -- two attempts, both named `rate_limited`, the circuit
    tripped, then the reads as for any failed cancel (no abandon). The
    tick's positions walk failing on a RateLimitError: `rate_limited`
    beside `positions_unreadable`, the circuit tripped."""
    from sportsassets import venue_pace
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)
    v = _Venue()
    v.rest("oid-1")
    v.cancel_order = lambda oid, slug: (v.calls.append(("cancel", oid, slug))
                                        or {"ok": False, "error": "RateLimitError: 429 <!doctype html>"})
    st = _tick(p, v)
    assert len(_cancels(v)) == ml.CANCEL_ATTEMPTS and not st["abandoned"]
    assert _census(st, "rate_limited") == ml.CANCEL_ATTEMPTS and venue_pace.penalty_left() > 590.0
    assert _census(st, "cancel_pending") == 1, "the reads decide the order's state, as before"
    # a cancel that RAISES a RateLimitError is the same fact
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)

    class RateLimitError(Exception):
        pass

    def _boom(oid, slug):
        v2.calls.append(("cancel", oid, slug))
        raise RateLimitError("429")
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    p2.add_order(b2, placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)
    v2 = _Venue()
    v2.rest("oid-1")
    v2.cancel_order = _boom
    st2 = _tick(p2, v2)
    assert _census(st2, "rate_limited") == ml.CANCEL_ATTEMPTS and venue_pace.penalty_left() > 590.0
    # the positions walk
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)
    v3 = _Venue()

    def _limited(q):
        raise RateLimitError("429 Too Many Requests")
    v3.portfolio.positions = _limited
    st3 = _tick(_pool(), v3)
    assert st3["abandoned"] and st3["abandon_reason"] == "positions_unreadable"
    assert _census(st3, "rate_limited") == 1 and venue_pace.penalty_left() > 590.0
    assert _run(ms.account_positions_walk(v3)) == (None, 1, True)
    # the fake's raise_walk raises RuntimeError('429'): a 429 by its text; any
    # other failure trips nothing
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)
    assert _run(ms.account_positions_walk(_Venue(raise_walk=True))) == (None, 1, True)
    v4 = _Venue()
    v4.portfolio.positions = lambda q: (_ for _ in ()).throw(RuntimeError("socket reset"))
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)
    assert _run(ms.account_positions_walk(v4)) == (None, 1, False) and venue_pace.penalty_left() == 0.0


def test_r2_a_placement_429_trips_the_circuit_beside_the_abandon(monkeypatch):
    """The pre-existing placement 429 (`rate_limited`, the tick
    abandoned) now also trips the pacer's circuit."""
    from sportsassets import venue_pace
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)

    def _reject(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                "filled_shares": 0.0, "raw": {"status_code": 429, "error": "429 refused"}}
    p = _pool()
    p.add_book(ledger=0)
    st = _tick(p, _Venue(place=_reject))
    assert st["abandoned"] and _census(st, "rate_limited") == 1 and venue_pace.penalty_left() > 590.0
    # the two sites read the refusal raw by its NAMED fields (round 4,
    # MEDIUM-2: _raw_rate_limit), never '429' as a substring of its text
    src = _place_src()
    assert src.count("if _raw_rate_limit(raw):") == 2 and '"429" in' not in src
    assert src.count('_rate_limited(t, w, f"placement {slug}")') == 2
    # the third placement site: a 429 RAISED by the preview or an IOC
    # take's create (round 4, HIGH-1), pinned in section 20e
    assert inspect.getsource(ml._place_rate_limited).count('_rate_limited(t, w, f"placement {slug}")') == 1


def test_r2_the_live_lane_has_its_own_candidate_budget_and_the_shadow_keeps_twenty(monkeypatch):
    """HIGH-B (2). mirror_live.MAX_MARKETS_PER_TICK (env
    MIRROR_LIVE_MAX_MARKETS, 40, env lowers only) is the live lane's;
    ms.MAX_MARKETS_PER_TICK is back at 20 for the shadow."""
    import importlib
    assert ml.MAX_MARKETS_PER_TICK == 40 and ms.MAX_MARKETS_PER_TICK == 20
    assert 'rules.capped_env("MIRROR_LIVE_MAX_MARKETS", 40.0, floor=0.0)' in inspect.getsource(ml)
    monkeypatch.setenv("MIRROR_LIVE_MAX_MARKETS", "999")
    assert int(rules.capped_env("MIRROR_LIVE_MAX_MARKETS", 40.0, floor=0.0)) == 40
    monkeypatch.setenv("MIRROR_LIVE_MAX_MARKETS", "5")
    assert int(rules.capped_env("MIRROR_LIVE_MAX_MARKETS", 40.0, floor=0.0)) == 5
    monkeypatch.delenv("MIRROR_LIVE_MAX_MARKETS")
    monkeypatch.setenv("MIRROR_MAX_MARKETS", "999")
    try:
        assert importlib.reload(ms).MAX_MARKETS_PER_TICK == 20
    finally:
        monkeypatch.delenv("MIRROR_MAX_MARKETS")
        importlib.reload(ms)
    # the live tick reads its own: a shadow budget of 0 caps nothing here
    monkeypatch.setattr(ms, "MAX_MARKETS_PER_TICK", 0)
    p = _pool()
    v = _Venue()
    st = _tick(p, v)
    assert _places(v) and st.get("capped_tick") is not True


def test_r2_twenty_buys_and_forty_candidate_reads_fit_the_default_guard_of_eighty(monkeypatch):
    """HIGH-B (3). 46 increase books (each rests a BUY), 20 placements
    (the ops budget) = 40 guarded requests, then the candidate walk's
    whole 40 at the DEFAULT guard (80); at 79 the guard bites first."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1e9)
    assert rules.MIRROR_VENUE_CALLS_PER_TICK == 80
    p, slugs, http = _increase_world(46, conds=[f"c{i}" for i in range(41)])
    v = _Venue()
    st = _tick(p, v, http=http)
    assert st["ops"] == 20 and len(_places(v)) == 20 and _census(st, "ops_capped") == 26
    bbos = [c[1] for c in v.calls if c[0] == "bbo"]
    cand = len(bbos) - st["books_live"]
    # E6: 46 book reads and 20 BUYs (40 requests) spend the tick's
    # venue-call budget (60) before the candidates; the stage never
    # starves below CAND_MIN_PER_TICK (10) -- under the guard of 80 the
    # whole 40 would still fit, it is the budget that bounds them now
    assert cand == ml.CAND_MIN_PER_TICK == 10 and _census(st, "venue_calls_capped") == 0 \
        and st["capped_tick"] is True, (cand, st["census"])
    assert _census(st, "venue_calls") == _expected_calls(v)
    monkeypatch.setattr(rules, "MIRROR_VENUE_CALLS_PER_TICK", 79)
    # E6: the guard's own pin -- the tick's budget lifted here so the
    # guard is what bites (test_e6_tick_budget pins the budget itself)
    monkeypatch.setattr(ml, "VENUE_CALLS_PER_TICK", 10 ** 6)
    p2, slugs2, http2 = _increase_world(46, conds=[f"c{i}" for i in range(41)])
    v2 = _Venue()
    st2 = _tick(p2, v2, http=http2)
    cand2 = len([c for c in v2.calls if c[0] == "bbo"]) - st2["books_live"]
    assert cand2 == 39 and _census(st2, "venue_calls_capped") == 1


def test_r2_the_positions_page_count_is_per_call_so_a_concurrent_shadow_walk_cannot_corrupt_it():
    """LOW-b. Two walks on one loop (the live tick's and the shadow's):
    each answers its own page count."""
    slow = _Venue()
    slow.portfolio = _PagedPortfolio(3, {SLUG: 0})
    orig = slow.portfolio.positions

    def _slow(q):
        _REAL_SLEEP(0.03)
        return orig(q)
    slow.portfolio.positions = _slow
    fast = _Venue()
    fast.portfolio = _PagedPortfolio(1, {SLUG: 0})

    async def both():
        a = asyncio.create_task(ms.account_positions_walk(slow))
        await asyncio.sleep(0.07)
        b = asyncio.create_task(ms.account_positions_walk(fast))
        ra = await a
        rb = await b
        return ra[1], rb[1]
    assert _run(both()) == (3, 1)


def test_r2_the_grammar_echo_pages_are_paced_and_counted_per_page(monkeypatch):
    """LOW-d. _position_echo pages the account up to five times: every
    page through the pacer, every page a venue request."""
    paced = []
    monkeypatch.setattr(ml, "pace", lambda s=ms.READ_PACING_S, slots=1: paced.extend([s] * int(slots)))
    v = _Venue()
    v.portfolio = _PagedPortfolio(3, {SLUG: 40})
    echo, pages = ml._position_echo(v, SLUG)
    assert echo == {"net": 40.0, "outcome": None, "title": None} and pages == 3 and len(paced) == 3
    echo2, pages2 = ml._position_echo(v, "never-held")
    assert echo2 == {"net": 0.0, "outcome": None, "title": None} and pages2 == 3
    v.portfolio.positions = lambda q: (_ for _ in ()).throw(RuntimeError("down"))
    assert ml._position_echo(v, SLUG) == (None, 1)
    src = inspect.getsource(ml._grammar_fill_check_locked)
    assert "echo, pages = await asyncio.to_thread(_position_echo" in src and "_venue_call(t, pages)" in src


def test_r2_a_raising_streak_judge_is_named_and_the_games_walk_goes_on(monkeypatch):
    """LOW-f. gather(return_exceptions=True) would swallow a raise from
    _walk_streak in _game's finally and end that game's walk silently:
    it is caught, logged and counted (`walk_error`), and the game's
    remaining books are ticked."""
    p = _pool(conds=[])
    slugs = _many_books(p, 4)
    # two books of ONE game (the same game_key), so the second follows the first in one task
    gk = le._us_game_key(slugs[0])
    for bk in p.books.values():
        bk["game_key"] = gk
    monkeypatch.setattr(ml, "_walk_streak", lambda t, ordered: (_ for _ in ()).throw(RuntimeError("judge")))
    st = _tick(p, _Venue())
    assert st["books_live"] == 4 and _census(st, "walk_error") >= 4 and not st["abandoned"]
    assert "_judge_walk(t, ordered, book)" in inspect.getsource(ml._walk_books)
    assert "_judge_walk(t, t.walk_order" in inspect.getsource(ml._miss)
    assert "walk_error" in ml.CENSUS_KEYS


def test_r2_the_reviewers_walk_order_and_budget_pins_hold(monkeypatch):
    """The re-reviewer's D1/D2/B2/C1 reproductions, verbatim in spirit:
    three consecutive bad books abandon under N=6 exactly as sequentially;
    a hung read holds the verdict until it lands; a reduce take over a
    SELL rest goes out with the hour's budget spent while an entry does
    not; at a 0 s wait an armed take fires once inside sixty seconds."""
    import threading
    p = _pool(conds=[])
    slugs = _many_books(p, 6)
    bad = [slugs[1], slugs[2], slugs[3]]
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 1)
    seq = _tick(p, _PerSlugVenue(delay=0.02, bad=bad))
    assert seq["abandoned"] and seq["abandon_reason"] == "no_quote" and _census(seq, "no_quote") == 3
    p2 = _pool(conds=[])
    _many_books(p2, 6)
    monkeypatch.setattr(rules, "MIRROR_BOOK_CONCURRENCY", 6)
    par = _tick(p2, _PerSlugVenue(delay=0.02, bad=bad))
    assert par["abandoned"] and par["abandon_reason"] == "no_quote" and _census(par, "no_quote") == 3
    assert _census(par, "tick_abandoned") == 1 and ml._backoff_until == NOW + ms.BACKOFF_S
    # a hung read holds the verdict
    p3 = _pool(conds=[])
    slugs3 = _many_books(p3, 6)
    release = threading.Event()
    bad3 = [slugs3[2], slugs3[3], slugs3[4]]

    class _Hung(_PerSlugVenue):
        def bbo_read(self, client, slug):
            if slug == slugs3[1]:
                release.wait(3.0)
            return super().bbo_read(client, slug)
    abandoned_at = []
    orig = ml._abandon

    def _ab(t, why, detail=None):
        abandoned_at.append(time.monotonic())
        return orig(t, why, detail)
    monkeypatch.setattr(ml, "_abandon", _ab)
    threading.Timer(0.3, release.set).start()
    t0 = time.monotonic()
    st3 = _tick(p3, _Hung(delay=0.005, bad=bad3))
    assert st3["abandoned"] and st3["abandon_reason"] == "no_quote"
    assert len(abandoned_at) == 1 and abandoned_at[0] >= t0 + 0.3
    # the reduce take over a SELL rest with the budget spent
    p4 = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    b4 = p4.add_book(ledger=300)
    for _ in range(rules.MIRROR_MAX_REPLACES_PER_HOUR):
        p4.add_order(b4, state="cancelled", reason="replace", done_at=NOW - 100, order_id=None)
    p4.add_order(b4, side=SELL, wire=0.32, qty=200, placed_ts=NOW - rules.MIRROR_TAKE_AFTER_S)
    v4 = _Venue(bid=0.35, ask=0.36, ioc_fill=200.0, held={SLUG: 300})
    v4.rest("oid-1", "SELL", 0.32, 200)
    st4 = _tick(p4, v4, http=_mkt(100.0))
    assert b4["target"] == 100 and _census(st4, "take_capped") == 0 and _census(st4, "replace_capped") == 0
    assert len(_cancels(v4)) == 1 and [c for c in _places(v4) if c[4] is True and c[3] == 200]
    # the zero wait
    monkeypatch.setattr(rules, "MIRROR_TAKE_AFTER_S", 0.0)
    monkeypatch.setattr(rules.take_allowed, "__defaults__", (0.0,))
    p5 = _pool()
    b5 = p5.add_book(ledger=0, take_armed_ts=NOW - 1)
    v5 = _Venue(bid=0.30, ask=0.30, ioc_fill=300.0)
    _tick(p5, v5)
    assert [c[5] for c in _places(v5)] == ["TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"] and b5["take_armed_ts"] is None
    st6 = _tick(p5, _Venue(bid=0.30, ask=0.30, ioc_fill=300.0, held={SLUG: 300}), now=NOW + 30)
    assert _census(st6, "take_placed") == 0
    p7 = _pool()
    b7 = p7.add_book(ledger=0, take_armed_ts=NOW - 61)
    v7 = _Venue(bid=0.30, ask=0.30, ioc_fill=300.0)
    st7 = _tick(p7, v7)
    # E4 addendum: the stale arm is named and cleared, and the crossing
    # book still takes FIRST on the price alone (the IOC at his cent),
    # never resting first at the zero wait (the E4 review's c2)
    assert _census(st7, "take_arm_stale") == 1 and [c[5] for c in _places(v7)] == ["TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert b7["take_armed_ts"] is None and _census(st7, "take_first") == 1 and b7["ledger_net"] == 300
    # the flip's ADD half is exempt on purpose (LOW-e), documented on the predicate
    from sportsassets.analytics.mirror import Plan
    assert ml._exit_or_flip({"intent": rules.ORDER_INTENT}, Plan(BUY, 10, 0.3, "rest"), {"side": SELL}) is True
    assert "THE FLIP'S ADD HALF IS EXEMPT ON PURPOSE" in inspect.getsource(ml._exit_or_flip)



# ------------------------ 20d. the E2 review's third round, pinned (2026-09-07)
#
# HIGH-1 the reserved second slot (dropped: one claim per request, the
# adapter claims its own before the create -- pinned above with the real
# gate); MEDIUM-2 penalize() off the gate's lock (pinned above);
# MEDIUM-3 a placement 429 no longer backs off while the circuit holds;
# LOW-4 the rate-limit match is the SDK's class / status, never a
# substring; LOW-6 a TTL/replace cancel and its re-rest are ONE op.

def test_r3_a_placement_429_abandons_but_skips_the_backoff_while_the_circuit_holds(monkeypatch):
    """MEDIUM-3. The abandon stays (nothing more is placed this tick),
    the circuit halves the rate for ten minutes, and the 60 s backoff
    that left every exit unmanaged is skipped by name
    (`backoff_skipped_circuit`): the next tick runs. Any other abandon
    still backs off."""
    from sportsassets import venue_pace
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)

    def _reject(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                "filled_shares": 0.0, "raw": {"status_code": 429, "error": "429 refused"}}
    p = _pool()
    p.add_book(ledger=0)
    st = _tick(p, _Venue(place=_reject))
    assert st["abandoned"] and st["abandon_reason"] == "rate_limited" and _census(st, "rate_limited") == 1
    assert _census(st, "backoff_skipped_circuit") == 1 and _census(st, "tick_abandoned") == 1
    assert ml._backoff_until == 0.0 and venue_pace.penalty_left() > 590.0
    st2 = _tick(p, _Venue(), now=NOW + 30, keep_backoff=True)
    assert not st2.get("skipped_backoff") and st2["reads"] >= 1, "the next tick runs at the doubled gap"
    # an outage abandon (no 429) still backs off
    p3 = _pool(conds=["c1", "c2", "c3"])
    st3 = _tick(p3, _Venue(raise_bbo=True))
    assert st3["abandoned"] and st3["abandon_reason"] == "no_quote" and _census(st3, "backoff_skipped_circuit") == 0
    assert ml._backoff_until == NOW + ms.BACKOFF_S
    assert "backoff_skipped_circuit" in ml.CENSUS_KEYS


def test_r3_the_rate_limit_match_is_the_sdks_class_or_status_never_a_substring():
    """LOW-4. '429' inside an order id, a slug or a price is not a rate
    limit; the SDK's RateLimitError (any body), a status_code of 429,
    and a text that STARTS with the name / '429' / 'Too Many Requests'
    (the walk's RuntimeError wrapper, the adapter's error strings) are."""
    from sportsassets import venue_pace
    assert not ms.is_rate_limit("NotFoundError: order 7f429c1e-... is not open")
    assert not ms.is_rate_limit(RuntimeError("positions walk carries an unreadable netPosition for nba-x-14290"))
    assert not ms.is_rate_limit("BadRequestError: price 0.4290 not on tick")
    assert not ms.is_rate_limit("NotFoundError: order abc is not open") and not ms.is_rate_limit(None)
    assert not ms.is_rate_limit(RuntimeError("socket reset")) and not ms.is_rate_limit("timeout")
    assert ms.is_rate_limit("RateLimitError") and ms.is_rate_limit("RateLimitError: 429 <!doctype html>")
    assert ms.is_rate_limit(RuntimeError("429")) and ms.is_rate_limit(RuntimeError("429 Too Many Requests"))
    assert ms.is_rate_limit("HTTP 429 Too Many Requests") and ms.is_rate_limit("Too Many Requests")

    class RateLimitError(Exception):
        pass

    class APIStatusError(Exception):
        status_code = 429
    assert ms.is_rate_limit(RateLimitError("<!doctype html>")) and ms.is_rate_limit(APIStatusError("x"))

    class Other(Exception):
        status_code = 502
    assert not ms.is_rate_limit(Other("bad gateway; upstream said 429"))    # a 502 whose body mentions 429 -- not one
    # a cancel refused for an order id carrying '429' trips nothing
    p = _pool()
    b = p.add_book(ledger=0)
    p.add_order(b, order_id="oid-429", placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)
    v = _Venue()
    v.rest("oid-429")
    v.cancel_order = lambda oid, slug: (v.calls.append(("cancel", oid, slug))
                                        or {"ok": False, "error": f"NotFoundError: order {oid} is not open"})
    st = _tick(p, v)
    assert _census(st, "rate_limited") == 0 and venue_pace.penalty_left() == 0.0


def test_r3_a_ttl_cohorts_cancels_and_re_rests_are_one_op_each_so_no_book_is_left_bare(monkeypatch):
    """LOW-6. 46 books whose rests all expired at once (a TTL cohort),
    every one wanting the same rest back: step O's cancels used to eat
    the whole ops budget before any book was planned (20 cancels, 0
    re-rests, 20 books bare for a tick). A TTL or replace cancel and
    its re-rest are ONE op: 20 cancels AND their 20 re-rests in the
    same tick, `ops` 20, the 26 others `ops_capped` (their rests still
    stand -- an exit is never shed: a cancel the budget refused leaves
    the order resting and in open_by_book, as before)."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1e9)
    p, slugs, http = _increase_world(46, conds=[f"c{i}" for i in range(41)])
    v = _Venue()
    for i, bk in enumerate(sorted(p.books.values(), key=lambda b: b["id"])):
        p.add_order(bk, order_id=f"oid-{i}", placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1, us_market_slug=slugs[i])
        v.rest(f"oid-{i}", slug=slugs[i])
    st = _tick(p, v, http=http)
    assert len(_cancels(v)) == 20 and len(_places(v)) == 20 and st["ops"] == 20, (len(_cancels(v)), len(_places(v)), st["ops"])
    assert _census(st, "ops_capped") == 26 and st["requotes"] == 20
    cancelled = {c[2] for c in _cancels(v)}
    assert {c[1] for c in _places(v)} == cancelled, "every re-rest is on a book whose rest was cancelled"
    assert len([o for o in p.orders.values() if o["state"] == "open"]) == 46, "20 new rests, 26 still standing"
    assert _census(st, "venue_calls") == _expected_calls(v)
    # the credit is per book and per tick: a take's cancel (an entry) is not credited
    t = ml._Tick(pool=p, pmus=v, http=None, now=NOW, stats=ml._new_stats())
    assert t.requote_credit == set()
    src = inspect.getsource(ml._cancel_and_settle)
    assert 'if reason in ("ttl", "replace"):' in src and "t.requote_credit.add(book[\"id\"])" in src
    # the credit is never spent by an IOC: pinned by BEHAVIOUR in section
    # 20e (round 4, LOW-3), not by the clause's text
    # the single-book shape: a TTL cancel and its re-rest in one tick, one op
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    p2.add_order(b2, placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)
    v2 = _Venue()
    v2.rest("oid-1")
    st2 = _tick(p2, v2)
    assert len(_cancels(v2)) == 1 and len(_places(v2)) == 1 and st2["ops"] == 1 and st2["requotes"] == 1


def test_r3_forty_six_books_placing_and_forty_one_candidates_are_128_requests(monkeypatch):
    """The reviewer's C1, verbatim: the rate accounting on the 46-book
    world -- 128 requests a tick, 40 candidate reads, 20 placements."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 1e9)
    p, slugs, http = _increase_world(46, conds=[f"c{i}" for i in range(41)])
    v = _Venue()
    st = _tick(p, v, http=http)
    calls = _census(st, "venue_calls")
    cand = len([c for c in v.calls if c[0] == "bbo"]) - st["books_live"]
    # E6: the same world under the tick's venue-call budget (60) -- the
    # books' 46 reads and the 20 BUYs' 40 requests spend it, the
    # candidate stage reads its floor of 10: 128 - 30 = 98 requests
    assert st["ops"] == 20 and len(_places(v)) == 20 and cand == 10 and calls == _expected_calls(v) == 98
    assert rules.MIRROR_MAX_ORDER_OPS_PER_TICK * 2 + ml.MAX_MARKETS_PER_TICK == rules.MIRROR_VENUE_CALLS_PER_TICK


# ------------------------ 20e. the E2 review's fourth round, pinned (2026-09-07)
#
# HIGH-1 a 429 RAISED by the placement -- a BUY's preview, an IOC take's
# create -- is the venue's refusal, never a lost response: named, the
# circuit, the row refused `place_refused:rate_limited`, no freeze, no
# open-orders search, the abandon as on the create's 429; MEDIUM-2 the
# placement sites read the refusal raw by its named fields, never '429'
# as a substring; LOW-3 the re-quote credit clause pinned by behaviour;
# LOW-4/5 the positions walk's 429 skips the backoff too, on an explicit
# flag beside the circuit; LOW-6 the woken-first pin reads the walk's
# entry order (section 20). The 429 is the SDK's own RateLimitError,
# raised through the REAL adapter (pmus.submit_fok on the fake client
# tests.test_pmus_post_only installs).

def _sdk_429(message="<!doctype html><html>Too Many Requests</html>"):
    """The SDK's RateLimitError as its client raises it for a 429: the
    message is the body's `message` (or the raw text of a non-JSON
    page, which the anchored text match never matches), status 429."""
    import httpx
    from polymarket_us import RateLimitError
    resp = httpx.Response(429, request=httpx.Request("POST", "https://venue.invalid/v1/order"))
    return RateLimitError(message, response=resp, body=None)


class _RealAdapterVenue(_Venue):
    """The fixture venue whose submit_fok IS pmus.submit_fok on the fake
    client tests.test_pmus_post_only._install installs: the adapter's
    raise path -- the preview unwrapped, the create wrapped only under
    post_only -- as the worker sees it."""

    def submit_fok(self, slug, price, qty, sell=False, tif="TIME_IN_FORCE_FILL_OR_KILL",
                   intent=None, post_only=False, good_till=None, paced_pair=False):
        from sportsassets import pmus
        self.calls.append(("place", slug, price, qty, sell, tif, intent, post_only, good_till))
        return pmus.submit_fok(slug, price, qty, sell=sell, tif=tif, intent=intent,
                               post_only=post_only, good_till=good_till, paced_pair=paced_pair)


def _refused_row(p, b):
    return [o for o in p.orders.values() if o["book_id"] == b["id"]][0]


def test_r4_a_429_on_the_preview_is_a_refusal_named_and_tripping_the_circuit_never_a_lost_response(monkeypatch):
    """HIGH-1. The FIRST of a BUY's two requests raises the SDK's
    RateLimitError (submit_fok wraps only the create). On v4 it crossed
    _guarded into _place_reserved's except and was a LOST response: no
    `rate_limited`, no circuit, one MORE paced read into the limited
    venue (the open-orders search), the book frozen `placement_lost` on
    a 'placing' row for twenty minutes though nothing was sent. Now: the
    venue REFUSED the request -- `rate_limited` and the circuit, the row
    refused `place_refused:rate_limited` with a receipt naming the
    raise, the room back, the tick abandoned `rate_limited` with the
    backoff skipped while the circuit holds; the book stays live with no
    open order; no search. The next tick places it."""
    from tests.test_pmus_post_only import _Orders, _install

    def _preview(params):
        raise _sdk_429()
    _install(monkeypatch, _Orders(preview=_preview))
    p = _pool()
    b = p.add_book(ledger=0)
    v = _RealAdapterVenue()
    st = _tick(p, v)
    assert _census(st, "rate_limited") == 1 and venue_pace.penalty_left() > 590.0, st["census"]
    assert _census(st, "place_refused") == 1 and _census(st, "placement_lost") == 0
    assert ml._MIRROR_CENSUS.get("place_refused:rate_limited|rn1") == 1, "the family's name on the process census"
    assert st["abandoned"] and st["abandon_reason"] == "rate_limited"
    assert _census(st, "backoff_skipped_circuit") == 1 and ml._backoff_until == 0.0
    assert b["state"] == "live" and b.get("frozen_reason") is None and b.get("open_order_id") is None
    assert st["books_frozen"] == 0
    i = next(k for k, c in enumerate(v.calls) if c[0] == "place")
    assert not [c for c in v.calls[i + 1:] if c[0] == "open_orders"], "no lost-response search after the 429"
    assert len(_places(v)) == 1 and _places(v)[0][7] is True, "the post-only rest whose preview 429'd"
    row = _refused_row(p, b)
    assert row["state"] == "rejected" and row["reason"] == "place_refused:rate_limited" and row["order_id"] is None
    assert row["venue_state"] == "rate_limited"
    assert row["receipt"]["error_type"] == "RateLimitError" and row["receipt"]["status_code"] == 429
    assert row["receipt"]["error"].startswith("<!doctype html>")
    assert [r for r in st["recent"] if r["what"] == "place_refused" and r["status"] == "rate_limited"
            and r["raised"] == "RateLimitError"]
    assert _census(st, "venue_calls") == _expected_calls(v), "the preview's request is counted, and no more"
    # the day's room went back: the same clip goes out on the next tick,
    # which runs (no backoff) and places on a venue that answers
    st2 = _tick(p, _Venue(), now=NOW + 30, keep_backoff=True)
    assert not st2.get("skipped_backoff") and _census(st2, "rest_placed") == 1 and not st2["abandoned"]
    assert b["state"] == "live" and b.get("open_order_id") is not None


def test_r4_a_429_on_an_ioc_takes_create_is_a_refusal_not_a_lost_response(monkeypatch):
    """HIGH-1. A take is sent with post_only False (`post_only = ... and
    not is_take`), so submit_fok's create runs OUTSIDE the 4xx refusal
    wrapper: the SDK's RateLimitError raises through. The same refusal
    road as the preview's: named, the circuit, the row refused, the
    book live, the abandon. The op stays spent (a request went out),
    as on the post-only create's 429."""
    from tests.test_pmus_post_only import _Orders, _install

    def _create(params):
        raise _sdk_429("Too Many Requests")
    _install(monkeypatch, _Orders(create=_create))
    p = _pool()
    b = p.add_book(ledger=0, take_armed_ts=NOW - rules.MIRROR_TAKE_AFTER_S - 5)
    v = _RealAdapterVenue(bid=0.30, ask=0.30)
    st = _tick(p, v)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" and pl[0][7] is False
    assert _census(st, "rate_limited") == 1 and venue_pace.penalty_left() > 590.0, st["census"]
    assert _census(st, "place_refused") == 1 and _census(st, "placement_lost") == 0
    assert ml._MIRROR_CENSUS.get("place_refused:rate_limited|rn1") == 1, "the family's name on the process census"
    assert _census(st, "take_placed") == 0 and st["placed_take"] == 0
    assert st["abandoned"] and st["abandon_reason"] == "rate_limited" and ml._backoff_until == 0.0
    assert b["state"] == "live" and b.get("frozen_reason") is None and b.get("open_order_id") is None
    assert st["ops"] == 1, "the op is spent: a request went out, as on the create's 429"
    row = _refused_row(p, b)
    assert row["state"] == "rejected" and row["reason"] == "place_refused:rate_limited" and row["tif"] == "IOC"
    assert row["receipt"]["error"] == "Too Many Requests" and row["receipt"]["error_type"] == "RateLimitError"
    assert _census(st, "venue_calls") == _expected_calls(v)


def test_r4_any_placement_raise_the_rate_limit_match_names_is_the_refusal_and_any_other_is_still_lost(monkeypatch):
    """HIGH-1, the rule at the seam: the except reads ms.is_rate_limit
    on the raise -- the class, a status_code of 429, a text that STARTS
    with the name / '429' / 'Too Many Requests' -- so a fixture venue
    raising RuntimeError('429 Too Many Requests') is the refusal too;
    a raise that names no rate limit (a socket reset) is what it always
    was: the lost-response search, then the freeze `placement_lost`."""
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue(place_raises=RuntimeError("429 Too Many Requests"))
    st = _tick(p, v)
    assert _census(st, "rate_limited") == 1 and _census(st, "place_refused") == 1
    assert ml._MIRROR_CENSUS.get("place_refused:rate_limited|rn1") == 1
    assert st["abandon_reason"] == "rate_limited" and b["state"] == "live" and _census(st, "placement_lost") == 0
    assert not [c for c in v.calls if c[0] == "open_orders" and c[1] == [SLUG]]
    assert _refused_row(p, b)["receipt"] == {"error": "429 Too Many Requests", "error_type": "RuntimeError",
                                             "status_code": None}
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    v2 = _Venue(place_raises=RuntimeError("socket reset"))
    st2 = _tick(p2, v2)
    assert _census(st2, "rate_limited") == 0 and venue_pace.penalty_left() == 0.0 and not st2["abandoned"]
    assert _census(st2, "placement_lost") == 1 and b2["state"] == "frozen" and b2["frozen_reason"] == "placement_lost"
    assert [c for c in v2.calls if c[0] == "open_orders" and c[1] == [SLUG]], "the lost-response search"
    assert _refused_row(p2, b2)["state"] == "placing"
    # the seam: the rate-limit read sits BEFORE the lost-response path,
    # and _lost_response itself never consults it (it never sees one)
    src = _place_src()
    assert src.index("if ms.is_rate_limit(exc):") < src.index("return await _lost_response(t, o, book, r, exc)")
    assert "is_rate_limit" not in inspect.getsource(ml._lost_response)


def test_r4_the_placement_sites_read_the_refusal_raw_by_its_named_fields_never_a_substring(monkeypatch):
    """MEDIUM-2. v4 read `"429" in raw.error` on a post_only_rejected and
    `"429" in json.dumps(raw)` on any refusal without an id. A
    preview_mismatch whose expected_cost is $429.00 on a quantity of 429
    is a mismatch: no `rate_limited`, no circuit, no abandon (it recurs
    every tick; the whole lane would have run at half rate for it). A
    post-only 400 whose message says "would cross at 0.429" arms the
    take and abandons nothing. A real 429 -- the SDK's status_code
    through the real adapter, an error_type naming RateLimitError, an
    error text that STARTS with the name -- still does."""
    from tests.test_pmus_post_only import _Orders, _install

    def _mismatch(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "preview_mismatch", "fill_price": None,
                "filled_shares": 0.0,
                "raw": {"preview": {"order": {"quantity": 429, "price": {"value": "0.30"},
                                              "id": "7f429c1e-429"}},
                        "expected_cost": 429.0, "venue_cost": 500.0}}
    p = _pool()
    p.add_book(ledger=0)
    st = _tick(p, _Venue(place=_mismatch))
    assert _census(st, "place_refused") == 1 and ml._MIRROR_CENSUS.get("place_refused:preview_mismatch|rn1") == 1
    assert not st["abandoned"] and _census(st, "rate_limited") == 0 and venue_pace.penalty_left() == 0.0

    def _cross(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                "filled_shares": 0.0,
                "raw": {"status_code": 400, "error": "post-only order would cross at 0.429",
                        "error_type": "BadRequestError"}}
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    st2 = _tick(p2, _Venue(place=_cross))
    assert _census(st2, "post_only_rejected") == 1 and b2.get("take_armed_ts") == NOW
    assert not st2["abandoned"] and _census(st2, "rate_limited") == 0 and venue_pace.penalty_left() == 0.0
    # a real 429 through the real adapter: the SDK's status_code on the
    # create's refusal (and the adapter now names the type beside it)
    def _create(params):
        raise _sdk_429()
    _install(monkeypatch, _Orders(create=_create))
    p3 = _pool()
    b3 = p3.add_book(ledger=0)
    st3 = _tick(p3, _RealAdapterVenue())
    assert st3["abandoned"] and st3["abandon_reason"] == "rate_limited" and _census(st3, "rate_limited") == 1
    assert _census(st3, "backoff_skipped_circuit") == 1 and ml._backoff_until == 0.0
    assert venue_pace.penalty_left() > 590.0 and b3.get("take_armed_ts") is None
    row = _refused_row(p3, b3)
    assert row["state"] == "rejected" and row["reason"] == "post_only_rejected:429"
    from sportsassets import pmus
    raw = pmus._post_only_refusal(_sdk_429(), {})["raw"]
    assert raw["status_code"] == 429 and raw["error_type"] == "RateLimitError" and ml._raw_rate_limit(raw)
    # the rule, field by field: the named fields match, nothing else does
    for raw in ({"status_code": 429}, {"error_type": "RateLimitError"}, {"error": "RateLimitError: <!doctype html>"},
                {"error": "429 Too Many Requests"}, {"status_code": None, "error": "Too Many Requests"}):
        assert ml._raw_rate_limit(raw), raw
    # an int status that is not 429 is authoritative over the text (round 5, d5): the 200 shape
    # (_post_only_cross) saying "Too Many Requests" is not a rate limit, nor a 400 beginning "429"
    for raw in ({"expected_cost": 429.0, "venue_cost": 429.0}, {"preview": {"order": {"quantity": 429}}},
                {"status_code": "429"}, {"status_code": 400, "error": "would cross at 0.429"},
                {"status_code": 200, "error": "Too Many Requests"},
                {"status_code": 400, "error": "429 contracts exceeds the maximum order size",
                 "error_type": "BadRequestError"},
                {"error_type": "BadRequestError", "error": "id 7f429c1e is not open"},
                {"status_code": 502, "error": "bad gateway; upstream said 429"}, {}, None, "429", ["429"]):
        assert not ml._raw_rate_limit(raw), raw
    for status in ("preview_unreadable", "preview_mismatch", "short_preview_refused"):
        monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)

        def _named(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till, status=status):
            return {"ok": False, "order_id": None, "status": status, "fill_price": None, "filled_shares": 0.0,
                    "raw": {"error": "RateLimitError: Too Many Requests", "expected_cost": 42.9}}
        p4 = _pool()
        p4.add_book(ledger=0)
        st4 = _tick(p4, _Venue(place=_named))
        assert st4["abandoned"] and st4["abandon_reason"] == "rate_limited" and _census(st4, "rate_limited") == 1


def test_r4_a_credited_book_placing_an_ioc_spends_its_own_op_and_keeps_the_credit(monkeypatch):
    """LOW-3, by behaviour (the `tif != "IOC"` clause used to be pinned
    by its text alone). In flow: a TTL cancel beside an expired take
    arm re-rests GTC on the credit -- ops 1, requotes 1, the arm
    cleared. At the unit: a credited book placing an IOC spends its own
    op (ops 1, not 0) and KEEPS the credit; the GTC rest after it
    spends the credit and no op; the next rest spends an op.

    The flow half runs under a LENGTHENED wait (E4 addendum: at the
    default wait of 0 the entry takes FIRST -- one IOC, its own op, then
    the remainder's rest on the credit; section 21 pins that): E2's
    rest-first behaviour is what the credit pin was written for."""
    from sportsassets.analytics.mirror import Plan
    _lengthened_wait(monkeypatch, 20.0)
    p = _pool()
    b = p.add_book(ledger=0, take_armed_ts=NOW - 25)
    p.add_order(b, placed_ts=NOW - rules.MIRROR_REST_TTL_S - 1)
    v = _Venue(bid=0.30, ask=0.30)
    v.rest("oid-1")
    st = _tick(p, v)
    assert len(_cancels(v)) == 1 and [c[5] for c in _places(v)] == ["TIME_IN_FORCE_GOOD_TILL_CANCEL"]
    assert st["ops"] == 1 and st["requotes"] == 1 and b["take_armed_ts"] is None
    # the unit
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    v2 = _Venue(bid=0.30, ask=0.30, ioc_fill=0.0)
    t = ml._Tick(pool=p2, pmus=v2, http=None, now=NOW, stats=ml._new_stats())
    monkeypatch.setattr(ml, "_current_stats", t.stats)
    t.day_room = t.total_room = 1e9
    t.mirror_day = 1e9
    t.requote_credit.add(b2["id"])
    r = ml._Reading(whale="rn1", cid=b2["condition_id"], slug=SLUG, la=M, oa=N, fills=[], his_long=300.0,
                    his_other=0.0, snap={}, snap_age=10.0, snap_partial=False, fresh_read=True, fresh=True,
                    snap_long=300.0, snap_other=0.0, bid=0.30, ask=0.30, mark=0.30, venue=0.0, manual=0.0,
                    market=None, market_live=True, venue_state="MARKET_STATE_OPEN")
    pl = Plan(BUY, 300, 0.30, "rest")
    out = _run(ml._place(t, b2, r, "take", BUY, 0.30, 300, 0.31, pl, {"target": 300}, tif="IOC"))
    assert out == "take" and t.ops == 1 and b2["id"] in t.requote_credit, (out, t.ops, t.requote_credit)
    assert [c[5] for c in _places(v2)] == ["TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    out2 = _run(ml._place(t, b2, r, "increase", BUY, 0.30, 300, 0.31, pl, {"target": 300}))
    assert out2 == "rest_placed" and t.ops == 1 and b2["id"] not in t.requote_credit, (out2, t.ops)
    b2["open_order_id"] = None                      # a third placement on the same fixture book
    p2.orders.clear()
    out3 = _run(ml._place(t, b2, r, "increase", BUY, 0.30, 300, 0.31, pl, {"target": 300}))
    assert out3 == "rest_placed" and t.ops == 2, (out3, t.ops)


def test_r4_a_positions_walk_429_abandons_positions_unreadable_and_skips_the_backoff_while_the_circuit_holds(monkeypatch):
    """LOW-4. The walk's 429 called _rate_limited (the circuit) and
    abandoned `positions_unreadable`, so the 60 s backoff applied on top
    of the circuit and the next tick was skipped -- while a placement
    429 skipped it. The abandon keeps its name (the walk is what could
    not be read) and skips the backoff on the explicit flag; a walk that
    fails for any other reason still backs off; an outage abandon while
    an EARLIER circuit holds still backs off."""
    v = _Venue()

    def _limited(q):
        raise _sdk_429()
    v.portfolio.positions = _limited
    st = _tick(_pool(), v)
    assert st["abandoned"] and st["abandon_reason"] == "positions_unreadable"
    assert _census(st, "rate_limited") == 1 and _census(st, "positions_unreadable") == 1
    assert venue_pace.penalty_left() > 590.0
    assert _census(st, "backoff_skipped_circuit") == 1 and ml._backoff_until == 0.0
    st2 = _tick(_pool(), _Venue(), now=NOW + 30, keep_backoff=True)
    assert not st2.get("skipped_backoff") and st2["reads"] >= 1, "the next tick runs at the doubled gap"
    # any other failure of the walk: the same name, the backoff
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)
    v3 = _Venue()
    v3.portfolio.positions = lambda q: (_ for _ in ()).throw(RuntimeError("socket reset"))
    st3 = _tick(_pool(), v3)
    assert st3["abandoned"] and st3["abandon_reason"] == "positions_unreadable" and _census(st3, "rate_limited") == 0
    assert _census(st3, "backoff_skipped_circuit") == 0 and ml._backoff_until == NOW + ms.BACKOFF_S
    # an outage abandon while an earlier circuit holds (the reviewer's
    # c3): not a 429 abandon, so it backs off
    venue_pace.penalize()
    p4 = _pool(conds=["c1", "c2", "c3"])
    st4 = _tick(p4, _Venue(raise_bbo=True))
    assert st4["abandoned"] and st4["abandon_reason"] == "no_quote"
    assert _census(st4, "backoff_skipped_circuit") == 0 and ml._backoff_until == NOW + ms.BACKOFF_S


def test_r4_the_backoff_skip_is_the_explicit_flag_beside_the_circuit_never_the_reasons_name(monkeypatch):
    """LOW-4/5. `_abandon(t, why, *, rate_limited=False)`: the skip is
    `rate_limited and penalty_left() > 0.0`. The name alone no longer
    decides (an unflagged `rate_limited` abandon backs off); the flag
    without a circuit backs off (penalize patched out: the guard is
    real, and every site that passes the flag calls _rate_limited
    first); the flag with a circuit skips under any name. Every 429
    site passes it: the three placement sites and the walk."""
    from sportsassets import venue_pace as vp
    for fn in (ml._abandon, ml._abandon_reconciled):
        prm = inspect.signature(fn).parameters["rate_limited"]
        assert prm.kind is inspect.Parameter.KEYWORD_ONLY and prm.default is False
    src = inspect.getsource(ml._abandon)
    assert "if rate_limited and venue_pace.penalty_left() > 0.0:" in src and 'why == "rate_limited"' not in src
    assert 'await _abandon_reconciled(t, "positions_unreadable", rate_limited=bool(limited))' in inspect.getsource(ml._tick)
    assert _place_src().count('_abandon(t, "rate_limited", rate_limited=True)') == 2
    assert '_abandon(t, "rate_limited", rate_limited=True)' in inspect.getsource(ml._place_rate_limited)
    assert '_abandon(t, "rate_limited")' not in _place_src()

    def _fresh():
        t = ml._Tick(pool=_pool(), pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
        monkeypatch.setattr(ml, "_current_stats", t.stats)
        ml._backoff_until = 0.0
        return t
    vp.penalize()
    t = _fresh()
    ml._abandon(t, "positions_unreadable", rate_limited=True)
    assert t.abandoned and t.stats["abandon_reason"] == "positions_unreadable"
    assert _census(t.stats, "backoff_skipped_circuit") == 1 and ml._backoff_until == 0.0
    t = _fresh()
    ml._abandon(t, "rate_limited")                        # the name alone: no skip
    assert _census(t.stats, "backoff_skipped_circuit") == 0 and ml._backoff_until == NOW + ms.BACKOFF_S
    t = _fresh()
    ml._abandon(t, "no_quote", "RuntimeError")
    assert _census(t.stats, "backoff_skipped_circuit") == 0 and ml._backoff_until == NOW + ms.BACKOFF_S
    # the flag with NO circuit: the guard is real -- backs off
    monkeypatch.setattr(vp, "_penalty_until", 0.0)
    monkeypatch.setattr(vp, "penalize", lambda now=None: 0.0)

    def _reject(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                "filled_shares": 0.0, "raw": {"status_code": 429, "error": "429"}}
    p = _pool()
    p.add_book(ledger=0)
    st = _tick(p, _Venue(place=_reject))
    assert st["abandoned"] and st["abandon_reason"] == "rate_limited" and _census(st, "rate_limited") == 1
    assert _census(st, "backoff_skipped_circuit") == 0 and ml._backoff_until == NOW + ms.BACKOFF_S
    for k in ("backoff_skipped_circuit", "rate_limited", "positions_unreadable"):
        assert k in ml.CENSUS_KEYS


# ------------------------ 20f. the E2 review's fifth round, pinned (2026-09-07)
#
# c2 / c3: the flatten's two placements -- the sole-holder close_position
# (the adapter catches the SDK's RateLimitError into a `close_failed` raw
# naming `error_type`) and the co-held IOC's create (raised through
# _guarded: sell=True, no preview, post_only False, no 4xx wrapper) --
# had no rate-limit seam on v5: both went down _lost_response and froze
# the EXIT book `placement_lost` for twenty minutes (the whale gone, our
# shares held) over a request the venue refused before it processed
# anything. Now both are the venue's refusal by name: `rate_limited`, the
# circuit, the row `place_refused:rate_limited`, the abandon that skips
# the backoff, no freeze, no search (_place_rate_limited, the round-4
# road). d5 (LOW): an int `status_code` on the raw / the exception is
# AUTHORITATIVE -- a 400 whose message begins "429 contracts exceeds the
# maximum order size" is a size refusal, not a rate limit; the text is
# read only when no int status is there. The three FINDING tests are the
# reviewer's, verbatim.

def _sdk(cls, code, message="x", body=None):
    """Any SDK status error as client._handle_error_response builds it."""
    import httpx
    resp = httpx.Response(code, request=httpx.Request("POST", "https://venue.invalid/v1/order"))
    return cls(message, response=resp, body=body)


class _RealCloseVenue(_Venue):
    """The fixture venue whose close_position IS pmus.close_position on a
    fake client whose orders.close_position raises."""

    def close_position(self, slug, *, slippage_bips):
        from sportsassets import pmus
        self.calls.append(("close", slug, slippage_bips))
        return pmus.close_position(slug, slippage_bips=slippage_bips)


def _install_close(monkeypatch, exc):
    from sportsassets import pmus

    class _Od:
        def close_position(self, params):
            raise exc
    monkeypatch.setattr(pmus, "_get_client", lambda: type("C", (), {"orders": _Od()})())


def _sole_close_world():
    """He left (fills say sold 300, the data API says the leg is 0); our
    flatten rest of 300 stood its MIRROR_FLATTEN_REST_S: this tick
    cancels it and, sole holder (the fixture _pm_held 300 = venue 300 =
    ledger 300), sends close_position (test 1862's world). Since E4 the
    vanish must be one HE GAVE NO PRICE FOR (`_unpriced`): a vanish with
    his SELL fill is priced off him and never runs the slippage leg."""
    p = _pool(fills=_unpriced(), snap=None)
    b = p.add_book(ledger=300)
    p.add_order(b, side=SELL, wire=0.32, kind="flatten_vanished", placed_ts=NOW - rules.MIRROR_FLATTEN_REST_S - 1)
    v = _RealCloseVenue(held={SLUG: 300})
    v.rest("oid-1", "SELL", 0.32, 300, created=NOW - 400)
    return p, b, v


def test_c2_FINDING_a_429_on_the_sole_close_is_named_trips_the_circuit_and_freezes_nothing(monkeypatch):
    """Attack (2b): pmus.close_position catches EVERY exception and
    returns close_failed with raw {error, slug, error_type}; for the
    SDK's RateLimitError the raw's error_type is 'RateLimitError' and
    ml._raw_rate_limit(raw) is True -- but _flatten_send (mirror_live.py
    :5438) never asks: it wraps the raw's TEXT (an HTML page) into a
    RuntimeError for _lost_response, which never consults is_rate_limit.
    The spec (venue_pace's own note, round 2 HIGH-B: 'every site that
    reads a 429 ... calls penalize()'; round 4 HIGH-1: a 429 is a
    refusal, never a lost response, no freeze): `rate_limited`, the
    circuit, the exit book not frozen."""
    _install_close(monkeypatch, _sdk_429())
    p, b, v = _sole_close_world()
    st = _tick(p, v, http=_gone())
    assert ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v.calls
    assert _census(st, "rate_limited") >= 1, st["census"]
    assert venue_pace.penalty_left() > 590.0
    assert b["state"] != "frozen", (b["state"], b.get("frozen_reason"))


def test_r5_the_sole_closes_429_row_is_refused_by_name_and_the_next_tick_rests_the_flatten_again(monkeypatch):
    """The whole of the c2 road: the CLOSE row `rejected` with the
    adapter's raw as its receipt (error_type RateLimitError, no int
    status), `place_refused:rate_limited` on the process census, the
    abandon `rate_limited` with the backoff skipped, no open-orders
    search after the close, no `placement_lost`, the book live with
    nothing non-terminal -- and the next tick (no backoff) sends the
    close again and, the venue answering, flattens."""
    _install_close(monkeypatch, _sdk_429())
    p, b, v = _sole_close_world()
    st = _tick(p, v, http=_gone())
    assert _census(st, "rate_limited") == 1 and _census(st, "place_refused") == 1
    assert _census(st, "placement_lost") == 0 and st["books_frozen"] == 0
    assert st["abandoned"] and st["abandon_reason"] == "rate_limited"
    assert _census(st, "backoff_skipped_circuit") == 1 and ml._backoff_until == 0.0
    assert ml._MIRROR_CENSUS.get("place_refused:rate_limited|rn1") == 1
    row = next(o for o in p.orders.values() if o["tif"] == "CLOSE")
    assert row["state"] == "rejected" and row["order_id"] is None and row["done_at"] == NOW
    assert row["venue_state"] == "rate_limited" and row["reason"] == "place_refused:rate_limited"
    assert row["receipt"]["error_type"] == "RateLimitError" and row["receipt"]["slug"] == SLUG
    # the adapter's raw, as it came, now with the SDK's int status beside
    # the type (E2 review round 6, LOW a4: an int status is authoritative)
    assert row["receipt"]["status_code"] == 429, row["receipt"]
    i = next(k for k, c in enumerate(v.calls) if c[0] == "close")
    assert not [c for c in v.calls[i + 1:] if c[0] == "open_orders"], "no lost-response search"
    assert b["state"] == "live" and b.get("open_order_id") is None and p._nonterminal(b["id"]) == []
    fr = [r for r in st["recent"] if r["what"] == "frozen"]
    assert not fr and [r for r in st["recent"] if r["what"] == "place_refused"][-1]["raised"] == "RateLimitError"
    assert b["ledger_net"] == 300
    # the next tick runs (no backoff) and the flatten begins again on a
    # live book: the abandoned tick wrote no plan, so the vanish clock
    # restarts and the SELL rests its MIRROR_FLATTEN_REST_S first (the
    # rest-then-slip order, never a bare close) -- nothing frozen
    v2 = _Venue(held={SLUG: 300})
    st2 = _tick(p, v2, now=NOW + 30, http=_gone(), keep_backoff=True)
    assert not st2.get("skipped_backoff") and not st2["abandoned"] and st2["books_frozen"] == 0
    assert [c[2:6] for c in _places(v2)] == [(0.32, 300, True, "TIME_IN_FORCE_GOOD_TILL_CANCEL")] and _census(st2, "flatten_rested") == 1
    assert b["state"] == "live" and b["ledger_net"] == 300 and b["open_order_id"] is not None


def _coheld_ioc_world(monkeypatch):
    """The desk's 200 explained shares beside our 300 (test 1862's third
    world): co-held, so the slippage leg is ONE IOC at
    sell_limit_price(bid) for our quantity, sent through the REAL
    adapter (sell=True: no preview, post_only False, no 4xx wrapper).
    An unpriced vanish since E4 (see _sole_close_world)."""
    async def _held(t, slug):
        return 500, 0.31
    monkeypatch.setattr(ml, "_pm_held", _held)
    p = _pool(fills=_unpriced(), snap=None)
    p.manual_shares[SLUG] = 200.0
    b = p.add_book(ledger=300)
    p.add_order(b, side=SELL, wire=0.32, kind="flatten_vanished", placed_ts=NOW - rules.MIRROR_FLATTEN_REST_S - 1)
    v = _RealAdapterVenue(held={SLUG: 500}, flatten_bid=0.29)
    v.rest("oid-1", "SELL", 0.32, 300, created=NOW - 400)
    return p, b, v


def test_c3_FINDING_a_429_on_the_coheld_flatten_ioc_is_named_trips_the_circuit_and_freezes_nothing(monkeypatch):
    """Attack (2c): the flatten's co-held IOC goes out through
    _flatten_send's OWN except (mirror_live.py:5420) -> _lost_response,
    with no is_rate_limit seam: the round-4 HIGH-1 shape, untouched at
    the sibling placement site. Spec as c2."""
    from tests.test_pmus_post_only import _Orders, _install

    def _create(params):
        raise _sdk_429("Too Many Requests")
    _install(monkeypatch, _Orders(create=_create))
    p, b, v = _coheld_ioc_world(monkeypatch)
    st = _tick(p, v, http=_gone())
    ioc = [c for c in _places(v) if c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"]
    assert len(ioc) == 1 and ioc[0][4] is True and ioc[0][2] == le.sell_limit_price(0.29)
    assert _census(st, "rate_limited") >= 1, st["census"]
    assert venue_pace.penalty_left() > 590.0
    assert b["state"] != "frozen", (b["state"], b.get("frozen_reason"))


def test_r5_the_coheld_iocs_429_row_is_refused_by_name_with_no_search_and_a_socket_reset_is_still_lost(monkeypatch):
    """The c3 road in full, and its boundary: the IOC row `rejected`
    with the raise's receipt, no open-orders read after the IOC, the
    abandon, no freeze; the same world with the create raising a
    socket reset is still the lost-response search and the
    `placement_lost` freeze (a reset is not a refusal the venue named).
    The seam order pinned: is_rate_limit before _lost_response in both
    of _flatten_send's placement excepts."""
    from tests.test_pmus_post_only import _Orders, _install

    def _create(params):
        raise _sdk_429("Too Many Requests")
    _install(monkeypatch, _Orders(create=_create))
    p, b, v = _coheld_ioc_world(monkeypatch)
    st = _tick(p, v, http=_gone())
    assert _census(st, "rate_limited") == 1 and _census(st, "place_refused") == 1
    assert _census(st, "placement_lost") == 0 and st["books_frozen"] == 0
    assert st["abandoned"] and st["abandon_reason"] == "rate_limited" and ml._backoff_until == 0.0
    i = next(k for k, c in enumerate(v.calls) if c[0] == "place" and c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
    assert not [c for c in v.calls[i + 1:] if c[0] == "open_orders"], "no lost-response search"
    row = next(o for o in p.orders.values() if o["tif"] == "IOC")
    assert row["state"] == "rejected" and row["order_id"] is None
    assert row["reason"] == "place_refused:rate_limited" and row["venue_state"] == "rate_limited"
    assert row["receipt"] == {"error": "Too Many Requests", "error_type": "RateLimitError", "status_code": 429}
    assert b["state"] == "live" and b["ledger_net"] == 300 and p._nonterminal(b["id"]) == []
    # the boundary: a socket reset on the same create is still lost
    def _reset(params):
        raise RuntimeError("socket reset")
    _install(monkeypatch, _Orders(create=_reset))
    p2, b2, v2 = _coheld_ioc_world(monkeypatch)
    st2 = _tick(p2, v2, http=_gone())
    assert _census(st2, "rate_limited") == 0 and not st2["abandoned"]
    assert b2["state"] == "frozen" and b2["frozen_reason"] == "placement_lost" and _census(st2, "placement_lost") == 1
    j = next(k for k, c in enumerate(v2.calls) if c[0] == "place" and c[5] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
    assert [c for c in v2.calls[j + 1:] if c[0] == "open_orders" and c[1] == [SLUG]], "the search"
    assert next(o for o in p2.orders.values() if o["tif"] == "IOC")["state"] == "placing"
    src = inspect.getsource(ml._flatten_send)
    assert src.index("if ms.is_rate_limit(exc):") < src.index("return await _place_rate_limited(t, o, book, r, exc, 0.0)") \
        < src.index("return await _lost_response(t, o, book, r, exc)")
    assert src.index("if _raw_rate_limit(raw):") < src.index("_place_rate_limited(t, o, book, r, None, 0.0, raw=raw)") \
        < src.index("return await _lost_response(t, o, book, r, RuntimeError(")


def test_d5_FINDING_LOW_an_int_status_that_is_not_429_is_authoritative_over_the_text():
    """_raw_rate_limit reads status_code OR error_type OR the error's
    head: a 400 (the SDK's own class, its own int status) whose message
    happens to begin with '429' -- '429 contracts exceeds the maximum
    order size' -- reads as a rate limit: the tick abandons, the whole
    lane runs at half rate for ten minutes, and the refusal recurs every
    tick (it is a size refusal). The same for is_rate_limit on an
    InternalServerError(503) whose message begins '429'. Spec: the SDK
    already decided the status; when an int status_code is present and
    is not 429, the text says nothing (round 3 LOW-4's rule, 'never a
    substring over free text', applied at the head too)."""
    from polymarket_us import BadRequestError, InternalServerError
    from sportsassets import pmus
    exc = _sdk(BadRequestError, 400, "429 contracts exceeds the maximum order size")
    raw = pmus._post_only_refusal(exc, {})["raw"]
    assert raw["status_code"] == 400 and raw["error_type"] == "BadRequestError"
    assert not ml._raw_rate_limit(raw), raw
    assert not ms.is_rate_limit(exc)
    assert not ms.is_rate_limit(_sdk(InternalServerError, 503, "429 upstream busy"))


def test_r5_a_400_whose_message_begins_with_429_arms_the_take_and_abandons_nothing_while_a_raw_with_no_status_reads_its_text():
    """The d5 truth table on the worker: the post-only 400 saying "429
    contracts exceeds the maximum order size" arms the take (a 400)
    and trips nothing; the SDK's 429 with the same words still does;
    a raw with NO int status (close_failed's shape, a cancel's error)
    still reads `error_type` and the text's head; a bool or a string
    status is not an int and falls to the text."""
    from polymarket_us import BadRequestError, APIStatusError
    from sportsassets import pmus
    raw = pmus._post_only_refusal(_sdk(BadRequestError, 400, "429 contracts exceeds the maximum order size"), {})["raw"]

    def _reject(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                "filled_shares": 0.0, "raw": raw}
    p = _pool()
    b = p.add_book(ledger=0)
    st = _tick(p, _Venue(place=_reject))
    assert _census(st, "post_only_rejected") == 1 and b.get("take_armed_ts") == NOW, "a 400: the take arms"
    assert not st["abandoned"] and _census(st, "rate_limited") == 0 and venue_pace.penalty_left() == 0.0
    raw429 = pmus._post_only_refusal(_sdk_429("429 contracts exceeds the maximum order size"), {})["raw"]
    assert ml._raw_rate_limit(raw429) and ms.is_rate_limit(_sdk(APIStatusError, 429, "x"))
    for shape, expect in (({"error_type": "RateLimitError", "error": "<html>"}, True),
                          ({"error": "429 Too Many Requests", "slug": SLUG}, True),
                          ({"error": "BadRequestError: 429 contracts exceeds", "error_type": "BadRequestError"}, False),
                          ({"status_code": 400, "error_type": "RateLimitError"}, False),
                          ({"status_code": 503, "error": "429 upstream busy"}, False),
                          ({"status_code": "400", "error": "429 slow down"}, True),
                          ({"status_code": True, "error": "429 slow down"}, True),
                          ({"status_code": 429.0, "error": "size"}, False)):
        assert ml._raw_rate_limit(shape) is expect, shape


# ---------------- 21. exits at his price, within one cent; entries take first (E4, 2026-09-06)
#
# Owner, ~23:24Z, verbatim: "we should exit when he exits at his price or
# within 1c variance (tolerance)". Book 29 (tsc-cfb-washst-wash total
# 46.5, LONG 63 sh): his net flipped short at ~20:30Z; our flatten rest
# sat at max(his 0.4595, ask) while the market fell to 0.01 / 0.02, was
# cancelled and re-quoted 14 times by TTL and never filled; the take
# never fired; the position went to settlement. And the addendum,
# ~23:38Z: "Remove the 20 second wait on entires too".

IOC_TIF = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
GTC_TIF = "TIME_IN_FORCE_GOOD_TILL_CANCEL"


def _reduce_world(**venue_kw):
    """He sold 200 of his 300 at 0.31; we hold 300 (venue 300), the
    target is 100: a SELL of 200 with his exit price 0.31 (floor 0.30,
    rest cent 0.31, take cent 0.30). Returns (pool, book, venue, http)."""
    p = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    b = p.add_book(ledger=300)
    venue_kw.setdefault("held", {SLUG: 300})
    return p, b, _Venue(**venue_kw), _mkt(100.0)


def test_e4_a_long_reduce_takes_within_a_cent_of_his_price_and_rests_at_his_cent_outside_it():
    """(a) the bid at 0.30 = his - 0.01: ONE IOC at 0.30 THAT tick,
    filled at the bid, `exit_take` -- no wait, no rest first, no
    MIN_MOVE_FRAC test, the entry's names untouched. (b) the bid at
    0.29 = his - 0.02: no take; the rest at ceil(his) = 0.31 -- HIS cent,
    not the 0.32 ask -- post-only, `exit_out_of_tol` with bid/ask/floor
    on the plan, the book held live. (c) the bid rises to 0.30 on a
    later tick: the rest is cancelled and the IOC goes THEN, whatever
    the rest's age, the replace budget never read."""
    p, b, v, http = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0)
    st = _tick(p, v, http=http)
    pl = _places(v)
    assert len(pl) == 1 and pl[0][2:8] == (0.30, 200, True, IOC_TIF, INTENT, False)
    assert not _cancels(v) and "close" not in _kinds(v) and "slug_bid" not in _kinds(v)
    assert _census(st, "exit_take") == 1 and _census(st, "take_placed") == 1 and _census(st, "filled_take") == 1
    assert _census(st, "take_at_his_level") == 0 and _census(st, "take_first") == 0, "an exit's names, not an entry's"
    assert _census(st, "exit_out_of_tol") == 0 and _census(st, "rest_placed") == 0 and _census(st, "flatten_rested") == 0
    assert b["ledger_net"] == 100 and b["target"] == 100 and b["state"] == "live"
    o = next(iter(p.orders.values()))
    assert o["kind"] == "take" and o["tif"] == "IOC" and o["state"] == "filled" and o["maker"] is False
    lp = b["last_plan"]
    assert lp["exit_px"] == 0.31 and lp["exit_px_src"] == "his_fill" and lp["exit_floor"] == 0.30
    assert lp["exit_rest"] == 0.31 and lp["exit_take"] == 0.30 and lp["kind"] == "reduce"
    assert "exit_out_of_tol" not in lp
    # (b)
    p2, b2, v2, http2 = _reduce_world(bid=0.29, ask=0.32, ioc_fill=200.0)
    st2 = _tick(p2, v2, http=http2)
    pl2 = _places(v2)
    assert len(pl2) == 1 and pl2[0][2:8] == (0.31, 200, True, GTC_TIF, INTENT, True)
    assert _census(st2, "exit_out_of_tol") == 1 and _census(st2, "exit_take") == 0
    assert _census(st2, "rest_placed") == 1 and _census(st2, "take_placed") == 0
    assert b2["ledger_net"] == 300 and b2["state"] == "live"
    o2 = next(iter(p2.orders.values()))
    assert o2["kind"] == "reduce" and o2["state"] == "open" and o2["wire"] == 0.31 and o2["tif"] == "GTC"
    assert b2["last_plan"]["exit_out_of_tol"] == {"bid": 0.29, "ask": 0.32, "floor": 0.30, "band_floor": 0.30, "at": NOW}    # FILL lane 3: the band bound beside the floor
    assert b2["last_plan"]["exit_floor"] == 0.30 and b2["last_plan"]["exit_px_src"] == "his_fill"
    # (c) five seconds later the bid is at 0.30: cancelled and taken, no wait
    v3 = _Venue(bid=0.30, ask=0.32, held={SLUG: 300}, ioc_fill=200.0)
    v3.orders = v2.orders
    st3 = _tick(p2, v3, now=NOW + 5, http=http2)
    assert _cancels(v3) == [("cancel", "oid-1", SLUG)]
    ioc = [c for c in _places(v3) if c[5] == IOC_TIF]
    assert len(ioc) == 1 and ioc[0][2] == 0.30 and ioc[0][3] == 200 and ioc[0][4] is True
    assert _census(st3, "exit_take") == 1 and _census(st3, "take_capped") == 0 and b2["ledger_net"] == 100
    assert p2.orders[o2["id"]]["state"] == "cancelled"
    assert not [x for x in p2.sent if "ml-replaces" in x[1]], "an exit's take never reads the replace budget"


def test_e4_book_29_replay_the_sign_flip_flatten_rests_at_his_cent_and_stands_five_ttl_periods(monkeypatch):
    """Book 29 replayed: LONG 63 sh at 0.46; he buys the other token and
    sells his long at 0.4595 (his net flips short: the sign-flip
    flatten, a paired one since he holds the other token); the US market
    at bid 0.01 / ask 0.02. The rest goes at his cent 0.46 (floor 0.4495,
    take cent 0.45), nothing is taken at 0.01, and across FIVE TTL
    periods the rest is never cancelled or re-placed
    (`requote_same_wire` each time, `requotes` 0, ONE order row for the
    whole spell), the book held live; the tick the bid comes back to
    0.45 the rest is cancelled and the IOC goes at 0.45."""
    _shorts_on(monkeypatch)                    # a signed target, as live: the flip needs one
    fills = [_fill(M, "BUY", 63, 0.46, NOW - 9000), _fill(N, "BUY", 100, 0.55, NOW - 3100),
             _fill(M, "SELL", 63, 0.4595, NOW - 3000)]
    p = _pool(fills=fills, snap={M: 0.0, N: 100.0})
    b = p.add_book(ledger=63, avg_cost=0.46)
    http = _mkt(0.0, 100.0)
    v = _Venue(bid=0.01, ask=0.02, held={SLUG: 63})
    st = _tick(p, v, http=http)
    lp = b["last_plan"]
    assert lp["sign_flip"] is True and b["target"] == 0 and lp["kind"] == "flatten_paired"
    pl = _places(v)
    assert len(pl) == 1 and pl[0][2:6] == (0.46, 63, True, GTC_TIF) and pl[0][7] is True
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "exit_take") == 0 and _census(st, "sign_flip") == 1
    assert "close" not in _kinds(v) and "slug_bid" not in _kinds(v)
    assert lp["exit_px"] == 0.4595 and lp["exit_floor"] == 0.4495
    assert lp["exit_rest"] == 0.46 and lp["exit_take"] == 0.45 and lp["exit_px_src"] == "his_fill"
    assert lp["exit_out_of_tol"] == {"bid": 0.01, "ask": 0.02, "floor": 0.4495, "band_floor": 0.4495, "at": NOW}    # FILL lane 3: the band bound beside the floor
    o = next(iter(p.orders.values()))
    assert o["kind"] == "flatten_paired" and o["state"] == "open" and o["wire"] == 0.46
    ttl = float(rules.MIRROR_REST_TTL_S)
    for k in range(1, 6):
        now = NOW + k * (ttl + 1)
        p.snap_at = now - 40
        vk = _Venue(bid=0.01, ask=0.02, held={SLUG: 63})
        vk.orders = v.orders
        stk = _tick(p, vk, now=now, http=http)
        assert not _cancels(vk) and not _places(vk) and stk["requotes"] == 0, k
        assert _census(stk, "requote_same_wire") == 1 and _census(stk, "exit_out_of_tol") == 1, k
        assert _census(stk, "reduce_unfilled") == 0 and _census(stk, "replace_capped") == 0, k
        assert _census(stk, "open_order_pending") == 1 and b["last_plan"]["requote_same_wire"] is True, k
        assert p.orders[o["id"]]["state"] == "open" and b["state"] == "live" and b["ledger_net"] == 63, k
    assert len(p.orders) == 1, "one rest for the whole spell, never re-placed"
    # the bid comes back within a cent of him: cancelled, taken at 0.45
    now = NOW + 6 * (ttl + 1)
    p.snap_at = now - 40
    v6 = _Venue(bid=0.45, ask=0.47, held={SLUG: 63}, ioc_fill=63.0)
    v6.orders = v.orders
    st6 = _tick(p, v6, now=now, http=http)
    assert _cancels(v6) == [("cancel", "oid-1", SLUG)]
    ioc = [c for c in _places(v6) if c[5] == IOC_TIF]
    assert len(ioc) == 1 and ioc[0][2] == 0.45 and ioc[0][3] == 63 and ioc[0][4] is True
    assert _census(st6, "exit_take") == 1 and b["ledger_net"] == 0 and _census(st6, "requote_same_wire") == 0


class _NoClose(_Venue):
    """The fixture venue whose close_position RAISES: since S4 no cover
    path may call it (the venue refuses it as an unpriced limit order)."""

    def close_position(self, slug, *, slippage_bips):
        raise AssertionError(f"close_position called on {slug}: the cover is a priced order (S4)")


def test_e4_s4_a_short_cover_rests_at_floor_his_outside_the_ceiling_and_takes_at_the_ceiling_cent_inside_it(monkeypatch):
    """S4 on E4's rule: his buy-back in long space is 0.30 (his SELL of
    the other token at 0.70, his newest fill), so the ceiling is 0.31
    and the cover cent 0.31. The ask at 0.32: the cover RESTS post-only
    at floor(his) = 0.30 (a BUY of the long token with the closing
    intent, SELL_SHORT on the wire, GTC, no good-till), the book held
    `exit_out_of_tol` / `short_cover_out_of_tol` with bid/ask/ceiling on
    the plan, no position read, no freeze. Next tick the ask at 0.31:
    the rest is cancelled and ONE IOC goes at 0.31 for 300, the same
    tick, no wait. close_position is never called."""
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
             _fill(N, "SELL", 400, 0.70, NOW - 2000), _fill(M, "SELL", 100, 0.31, NOW - 1000)]
    reads = []

    async def _held(t, slug):
        reads.append(slug)
        return 300, 0.32
    monkeypatch.setattr(ml, "_pm_held", _held)
    p = _short_world(fills=fills, snap=None)
    b = _short_book(p, ledger=-300)
    gone = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0},
                       {"conditionId": CID, "asset": N, "size": 0}])
    v = _NoClose(bid=0.30, ask=0.32, held={SLUG: -300})
    st = _tick(p, v, http=gone)
    assert "close" not in _kinds(v) and not reads
    assert [c[1:] for c in _places(v)] == [(SLUG, 0.30, 300, True, GTC_TIF, SHORT, True, None)]
    o = next(iter(p.orders.values()))
    assert (o["kind"], o["side"], o["tif"], o["intent"], o["wire"], o["qty"]) == (
        "flatten_vanished", BUY, "GTC", "ORDER_INTENT_SELL_SHORT", 0.30, 300)
    assert o["state"] == "open" and o["good_till"] is None
    assert _census(st, "exit_out_of_tol") == 1 and _census(st, "short_cover_out_of_tol") == 1
    assert _census(st, "short_cover_rest") == 1 and _census(st, "flatten_rested") == 1
    assert _census(st, "short_flatten_close") == 0 and _census(st, "short_reduce_unproven") == 0
    assert _census(st, "s4_unproven") == 0 and _census(st, "flatten_vanished") == 1
    assert b["state"] == "live" and b["ledger_net"] == -300
    lp = b["last_plan"]
    assert lp["exit_px"] == pytest.approx(0.30) and lp["exit_px_src"] == "his_fill"
    assert lp["exit_ceiling"] == pytest.approx(0.31) and lp["exit_cover"] == 0.31 and lp["exit_rest"] == 0.30
    assert "exit_floor" not in lp
    assert lp["exit_out_of_tol"] == {"bid": 0.30, "ask": 0.32, "ceiling": 0.31, "band_ceiling": 0.31, "at": NOW}    # FILL lane 3: the band bound beside the ceiling
    # re-checked next tick with the ask at the ceiling: the rest cancelled
    # under `take`, the one IOC at the cover cent, filled, the book flat
    v2 = _NoClose(bid=0.30, ask=0.31, held={SLUG: -300}, ioc_fill=300.0)
    v2.orders = v.orders
    st2 = _tick(p, v2, now=NOW + 30, http=gone)
    assert _cancels(v2) == [("cancel", "oid-1", SLUG)] and p.orders[o["id"]]["reason"] == "take"
    assert [c[1:] for c in _places(v2)] == [(SLUG, 0.31, 300, True, IOC_TIF, SHORT, False, None)]
    assert b["ledger_net"] == 0 and _census(st2, "short_cover_take") == 1 and _census(st2, "exit_take") == 1
    assert _census(st2, "short_flatten_close") == 1 and _census(st2, "exit_out_of_tol") == 0
    assert not reads and "close" not in _kinds(v2)
    assert b["last_plan"]["exit_cover"] == 0.31 and b["state"] == "closed"
    take = next(x for x in p.orders.values() if x["tif"] == "IOC")
    assert (take["kind"], take["side"], take["intent"], take["state"]) == ("take", BUY, "ORDER_INTENT_SELL_SHORT", "filled")


def test_e4_s4_no_price_of_his_holds_the_cover_by_name_except_a_vanish_or_the_sign_flip_which_cover_by_a_bounded_ioc(monkeypatch):
    """S4, fail closed: with NO buy-back price of his the cover is HELD
    `no_price` under `exit_px_src: 'none'` (a paired flatten while he
    still holds a token; a partial reduce) -- never a rest at the ask.
    The one exception: his position on our side is GONE -- a confirmed
    vanish, the sign flip -- and the cover is ONE IOC at the ask bounded
    by le.buy_limit_price (the ask + MIRROR_FLATTEN_SLIP, the long
    flatten's own bound mirrored), for our quantity. close_position is
    never called."""
    _shorts_on(monkeypatch)
    gone = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0},
                       {"conditionId": CID, "asset": N, "size": 0}])
    # (a) the confirmed vanish, unpriced: the bounded IOC at once
    p = _short_world(fills=_unpriced(), snap=None)
    b = _short_book(p, ledger=-300)
    v = _NoClose(bid=0.30, ask=0.40, held={SLUG: -300}, ioc_fill=300.0)
    st = _tick(p, v, http=gone)
    assert "close" not in _kinds(v) and [c[1:] for c in _places(v)] == [(SLUG, 0.42, 300, True, IOC_TIF, SHORT, False, None)]
    assert le.buy_limit_price(0.40) == 0.42 == round(0.40 + rules.MIRROR_FLATTEN_SLIP, 2)
    lp = b["last_plan"]
    assert lp["exit_px_src"] == "none" and lp["exit_px"] is None and "exit_ceiling" not in lp
    assert lp["cover"] == {"ioc_at_ask": 0.40, "limit": 0.42, "why": "vanished"}
    assert b["ledger_net"] == 0 and _census(st, "short_flatten_close") == 1 and _census(st, "short_cover_take") == 1
    assert _census(st, "exit_out_of_tol") == 0 and _census(st, "no_price") == 0
    o = next(iter(p.orders.values()))
    assert (o["kind"], o["tif"], o["intent"], o["wire"]) == ("flatten_vanished", "IOC", "ORDER_INTENT_SELL_SHORT", 0.42)
    # (b) an UNPRICED sign flip cannot be built from his fills: the fills
    # that carry his net across zero on a short book -- a BUY of the long
    # token, a SELL of the other -- are the cover-side fills _his_level
    # prices the cover off, so a flip is priced by construction; a
    # snapshot that says he flipped while the fills say otherwise is
    # target 0 with no `sign_flip` (fail closed: the paired hold below).
    # The `sign_flip` clause of the bounded IOC is kept for the record
    p2 = _short_world(fills=[_fill(N, "BUY", 400, 0.72, NOW - 2500)], snap={M: 700.0, N: 400.0})
    b2 = _short_book(p2, ledger=-300)
    v2 = _NoClose(bid=0.30, ask=0.35, held={SLUG: -300}, ioc_fill=300.0)
    st2 = _tick(p2, v2, http=_mkt(700.0, 400.0))
    assert _census(st2, "sign_flip") == 0 and b2["target"] == 0 and not _places(v2)
    assert _census(st2, "no_price") == 1 and b2["last_plan"]["cover"] == "unpriced_held" and b2["ledger_net"] == -300
    # (c) the paired flatten, unpriced, his position on our side still
    # there (he holds both tokens, net zero): HELD by name, nothing sent
    p3 = _short_world(fills=[_fill(N, "BUY", 400, 0.72, NOW - 2500)], snap={M: 400.0, N: 400.0})
    b3 = _short_book(p3, ledger=-300)
    v3 = _NoClose(bid=0.30, ask=0.40, held={SLUG: -300}, ioc_fill=300.0)
    st3 = _tick(p3, v3, http=_mkt(400.0, 400.0))
    assert b3["target"] == 0 and b3["last_plan"]["kind"] == "flatten_paired"
    assert not _places(v3) and "close" not in _kinds(v3) and b3["ledger_net"] == -300 and b3["state"] == "live"
    assert _census(st3, "no_price") == 1 and b3["last_plan"]["cover"] == "unpriced_held"
    assert b3["last_plan"]["exit_px_src"] == "none" and _census(st3, "s4_unproven") == 0
    # (d) a partial reduce, unpriced: held by name too
    p4 = _short_world(fills=[_fill(N, "BUY", 400, 0.72, NOW - 2500)], snap={M: 300.0, N: 400.0})
    b4 = _short_book(p4, ledger=-300)
    v4 = _NoClose(held={SLUG: -300}, ioc_fill=300.0)
    st4 = _tick(p4, v4, http=_mkt(300.0, 400.0))
    assert b4["target"] == -100 and not _places(v4) and b4["ledger_net"] == -300
    assert _census(st4, "no_price") == 1 and _census(st4, "short_reduce_unproven") == 0
    # (e) a re-check every tick: the held paired book is read again and, his
    # buy-back now on file, covers
    p3.fills.append(_fill(M, "BUY", 300, 0.31, NOW + 10))
    v5 = _NoClose(bid=0.30, ask=0.32, held={SLUG: -300}, ioc_fill=300.0)
    st5 = _tick(p3, v5, now=NOW + 30, http=_mkt(400.0, 400.0))
    assert [c[2:6] for c in _places(v5)] == [(0.32, 300, True, IOC_TIF)] and b3["ledger_net"] == 0
    assert b3["last_plan"]["exit_px_src"] == "his_fill" and _census(st5, "short_flatten_close") == 1


def test_e4_review_a_priced_vanish_takes_within_the_cent_and_never_runs_the_slippage_leg():
    """MEDIUM-3 (the reviewer's a7, ported). A vanish WITH his SELL at
    0.31: the bid within a cent takes at once; outside the cent the
    rest at his cent stands past MIRROR_FLATTEN_REST_S and past the TTL
    with no close, no co-held IOC, no cancel (`requote_same_wire`)."""
    fills = [_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(M, "SELL", 300, 0.31, NOW - 1000)]
    p = _pool(fills=fills, snap={M: 0.0, N: 0.0})
    b = p.add_book(ledger=300)
    v = _Venue(bid=0.30, ask=0.32, held={SLUG: 300}, ioc_fill=300.0)
    st = _tick(p, v, http=_gone())
    assert b["last_plan"]["kind"] == "flatten_vanished" and b["last_plan"]["exit_px_src"] == "his_fill"
    assert [c[2:6] for c in _places(v)] == [(0.30, 300, True, IOC_TIF)] and _census(st, "exit_take") == 1
    assert "close" not in _kinds(v) and "slug_bid" not in _kinds(v) and b["ledger_net"] == 0
    p2 = _pool(fills=fills, snap={M: 0.0, N: 0.0})
    b2 = p2.add_book(ledger=300)
    v2 = _Venue(bid=0.29, ask=0.32, held={SLUG: 300})
    st2 = _tick(p2, v2, http=_gone())
    assert [c[2:6] for c in _places(v2)] == [(0.31, 300, True, GTC_TIF)] and _census(st2, "flatten_rested") == 1
    o = next(iter(p2.orders.values()))
    for k, dt in enumerate((float(rules.MIRROR_FLATTEN_REST_S) + 100, float(rules.MIRROR_REST_TTL_S) + 100), 1):
        now = NOW + dt
        p2.snap_at = now - 40
        vk = _Venue(bid=0.29, ask=0.32, held={SLUG: 300})
        vk.orders = v2.orders
        stk = _tick(p2, vk, now=now, http=_gone())
        assert not _cancels(vk) and not _places(vk) and "close" not in _kinds(vk) and "slug_bid" not in _kinds(vk), k
        assert _census(stk, "exit_out_of_tol") == 1 and _census(stk, "flatten_vanished") == 1, k
        assert p2.orders[o["id"]]["state"] == "open" and b2["ledger_net"] == 300 and b2["state"] == "live", k
    assert _census(stk, "requote_same_wire") == 1


def test_e4_review_the_entry_take_first_fires_in_a_normal_book_at_his_cent():
    """HIGH-1. His 0.30; the rest's wire is buy_price(0.30, bid) and a
    book with bid < ask is never at or through its own rest, so the
    take must be judged and sent at HIS cent, buy_wire(his) = 0.30:
    bid 0.29 / ask 0.30 (the ask AT his level) -> one IOC at 0.30
    first, the remainder rests at 0.29, `take_first`; ask 0.29
    (through) -> the IOC at 0.30 (it fills at the ask, never above
    him); ask 0.31 -> a rest at 0.29 only, and the ask arriving at
    0.30 on a later tick cancels it and takes; a fill above 0.30 is
    impossible (the limit is his cent)."""
    his = [_fill(M, "BUY", 300, 0.30, NOW - 3000)]
    p = _pool(fills=his)
    b = p.add_book(ledger=0)
    v = _Venue(bid=0.29, ask=0.30, ioc_fill=100.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.30, 300, False, IOC_TIF), (0.29, 200, False, GTC_TIF)]
    assert _census(st, "take_first") == 1 and _census(st, "take_at_his_level") == 1 and not _cancels(v)
    assert b["ledger_net"] == 100 and b["avg_cost"] <= 0.30 and st["ops"] == 2
    rest = [x for x in p.orders.values() if x["state"] == "open"]
    assert len(rest) == 1 and rest[0]["wire"] == 0.29 and rest[0]["qty"] == 200
    # through his level
    p2 = _pool(fills=his)
    b2 = p2.add_book(ledger=0)
    v2 = _Venue(bid=0.28, ask=0.29, ioc_fill=300.0)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.30, 300, False, IOC_TIF)] and _census(st2, "take_first") == 1
    assert b2["ledger_net"] == 300 and all(c[2] <= 0.30 for c in _places(v2))
    # above his level: the rest alone, then the ask arriving at 0.30
    p3 = _pool(fills=his)
    b3 = p3.add_book(ledger=0)
    v3 = _Venue(bid=0.29, ask=0.31, ioc_fill=300.0)
    st3 = _tick(p3, v3)
    assert [c[2:6] for c in _places(v3)] == [(0.29, 300, False, GTC_TIF)] and _census(st3, "take_first") == 0
    assert _census(st3, "take_placed") == 0 and b3["ledger_net"] == 0
    v4 = _Venue(bid=0.29, ask=0.30, ioc_fill=300.0)
    v4.orders = v3.orders
    st4 = _tick(p3, v4, now=NOW + 30)
    assert _cancels(v4) == [("cancel", "oid-1", SLUG)]
    assert [c[2:6] for c in _places(v4)] == [(0.30, 300, False, IOC_TIF)] and _census(st4, "take_at_his_level") == 1
    assert _census(st4, "take_first") == 0 and b3["ledger_net"] == 300 and all(c[2] <= 0.30 for c in _places(v4))
    # his 0.2999: his cent is 0.29 and the ask at 0.30 is above it -- a rest, no IOC
    p5 = _pool(fills=[_fill(M, "BUY", 300, 0.2999, NOW - 3000)])
    b5 = p5.add_book(ledger=0)
    v5 = _Venue(bid=0.30, ask=0.30, ioc_fill=300.0)
    st5 = _tick(p5, v5)
    assert [c[2:6] for c in _places(v5)] == [(0.29, 300, False, GTC_TIF)] and _census(st5, "take_first") == 0
    assert b5["ledger_net"] == 0
    # the exit's take never counts before it is sent: an ops-capped
    # cancel names nothing (LOW-4); the count sits with the placement
    src = inspect.getsource(ml._act)
    assert '_mirror_stop("exit_take", w)' not in src and '_mirror_stop("exit_take", w)' in _place_src()


def test_e4_review_the_worker_reads_the_tolerance_at_call_time(monkeypatch):
    """LOW-6: MIRROR_EXIT_TOL tightened to 0 reaches the worker's exit
    without a reload -- a bid a cent under him is no longer taken."""
    monkeypatch.setattr(rules, "MIRROR_EXIT_TOL", 0.0)
    p, b, v, http = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0)
    st = _tick(p, v, http=http)
    assert [c[2:6] for c in _places(v)] == [(0.31, 200, True, GTC_TIF)] and _census(st, "exit_take") == 0
    assert b["last_plan"]["exit_floor"] == 0.31 and b["last_plan"]["exit_take"] == 0.31
    p2, b2, v2, http2 = _reduce_world(bid=0.31, ask=0.32, ioc_fill=200.0)
    st2 = _tick(p2, v2, http=http2)
    assert [c[2:6] for c in _places(v2)] == [(0.31, 200, True, IOC_TIF)] and _census(st2, "exit_take") == 1


def test_e4_an_exit_is_never_blocked_by_the_replace_budget_or_the_loss_stop():
    """Rule 4: with MIRROR_MAX_REPLACES_PER_HOUR cancels on the book an
    exit's REPLACE (its rest at the pre-E4 cent moving to his cent)
    still goes and an exit's TAKE still goes -- the count is not even
    read -- while an entry's replace at the cap is still refused. Rule
    5: the mirror's loss stop (`increase_block` mirror_loss_stop)
    refuses every increase and no exit: the take goes out under it."""
    cap = rules.MIRROR_MAX_REPLACES_PER_HOUR
    p, b, v, http = _reduce_world(bid=0.29, ask=0.32)
    for _ in range(cap):
        p.add_order(b, state="cancelled", reason="replace", done_at=NOW - 100, order_id=None)
    o = p.add_order(b, side=SELL, wire=0.32, qty=200, kind="reduce")
    v.rest("oid-1", "SELL", 0.32, 200)
    st = _tick(p, v, http=http)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)] and _census(st, "replace_capped") == 0
    assert [c[2:6] for c in _places(v)] == [(0.31, 200, True, GTC_TIF)] and st["requotes"] == 1
    assert p.orders[o["id"]]["state"] == "cancelled" and b["ledger_net"] == 300
    assert not [x for x in p.sent if "ml-replaces" in x[1]], "the count is never read for an exit"
    # the take, at the cap of cancels under reason 'take'
    p2, b2, v2, http2 = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0)
    for _ in range(cap):
        p2.add_order(b2, state="cancelled", reason="take", done_at=NOW - 100, order_id=None)
    p2.add_order(b2, side=SELL, wire=0.31, qty=200, kind="reduce")
    v2.rest("oid-1", "SELL", 0.31, 200)
    st2 = _tick(p2, v2, http=http2)
    assert _census(st2, "take_capped") == 0 and _census(st2, "exit_take") == 1 and b2["ledger_net"] == 100
    assert _cancels(v2) == [("cancel", "oid-1", SLUG)]
    assert not [x for x in p2.sent if "ml-replaces" in x[1]]
    # an entry's replace at the cap: refused, as before
    p3 = _pool()
    b3 = p3.add_book(ledger=0)
    for _ in range(cap):
        p3.add_order(b3, state="cancelled", reason="replace", done_at=NOW - 100, order_id=None)
    p3.add_order(b3, wire=0.28)
    v3 = _Venue()
    v3.rest("oid-1", price=0.28)
    st3 = _tick(p3, v3)
    assert _census(st3, "replace_capped") == 1 and not _cancels(v3) and not _places(v3)
    # the loss stop: another book's resting BUY is cancelled under its
    # name (as section 2 pins it) and the exit's take is sent regardless
    p4, b4, v4, http4 = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0)
    b5 = p4.add_book(ledger=0, us_market_slug="aec-atp-other-2026-09-02", condition_id="0xother")
    o5 = p4.add_order(b5, order_id="oid-9", us_market_slug="aec-atp-other-2026-09-02")
    v4.rest("oid-9", slug="aec-atp-other-2026-09-02")
    p4.state["mirror_loss_stop"] = {"at": "x"}
    st4 = _tick(p4, v4, http=http4)
    assert _census(st4, "mirror_loss_stop") >= 1
    assert p4.orders[o5["id"]]["state"] == "cancelled" and p4.orders[o5["id"]]["reason"] == "mirror_loss_stop"
    assert [c[2:6] for c in _places(v4)] == [(0.30, 200, True, IOC_TIF)]
    assert _census(st4, "exit_take") == 1 and b4["ledger_net"] == 100 and b5["ledger_net"] == 0


def test_e4_an_exit_he_gave_no_price_for_keeps_todays_prices_under_exit_px_src_none():
    """An UNPRICED vanish (no fill of his in the window): the rest at
    the ask, no take within a tolerance of nothing, `exit_px_src:
    'none'`. The admin flatten (mirror_flatten) with his price KNOWN:
    by the brief it keeps today's max(his, ask) rest and the slippage
    leg, and says `'none'` too."""
    p = _pool(fills=_unpriced(), snap=None)
    b = p.add_book(ledger=300)
    v = _Venue(bid=0.30, ask=0.32, held={SLUG: 300})
    st = _tick(p, v, http=_gone())
    assert [c[2:6] for c in _places(v)] == [(0.32, 300, True, GTC_TIF)]
    lp = b["last_plan"]
    assert lp["exit_px_src"] == "none" and lp["exit_px"] is None and "exit_floor" not in lp
    assert _census(st, "exit_take") == 0 and _census(st, "exit_out_of_tol") == 0
    assert _census(st, "flatten_rested") == 1 and lp["kind"] == "flatten_vanished"
    # the admin flatten with his price known (he sold at 0.31, the ask 0.32, the bid 0.30)
    p2, b2, v2, http2 = _reduce_world(bid=0.30, ask=0.32, ioc_fill=300.0)
    p2.state["mirror_flatten"] = True
    st2 = _tick(p2, v2, http=http2)
    assert [c[2:6] for c in _places(v2)] == [(0.32, 300, True, GTC_TIF)]
    assert b2["last_plan"]["exit_px_src"] == "none" and b2["last_plan"]["exit_px"] == 0.31
    assert _census(st2, "mirror_flatten") >= 1 and _census(st2, "exit_take") == 0
    assert _census(st2, "exit_out_of_tol") == 0 and b2["ledger_net"] == 300


def test_e4_the_exit_take_fires_once_per_rest_and_a_partial_ioc_rests_its_remainder_the_same_tick():
    """(i) a rest of 200 at his cent, the bid within a cent, the IOC
    filling 50 of the 200: one cancel, ONE IOC, and -- since E14b (FILL
    lane 1; the pin read "nothing rests after it this tick" before) --
    the unfilled 150 rests at his cent 0.31 on the SAME tick, the only
    standing order, never a second IOC. The next tick with the bid still
    there cancels that rest and takes for what is left (a new plan, not
    a second take on one rest), and rests the remainder again; the bid
    gone, the standing rest at his cent is held (`exit_out_of_tol`),
    nothing placed."""
    p, b, v, http = _reduce_world(bid=0.30, ask=0.32, ioc_fill=50.0)
    o = p.add_order(b, side=SELL, wire=0.31, qty=200, kind="reduce")
    v.rest("oid-1", "SELL", 0.31, 200)
    st = _tick(p, v, http=http)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]
    assert [c[2:6] for c in _places(v)] == [(0.30, 200, True, IOC_TIF), (0.31, 150, True, GTC_TIF)], \
        "one IOC, then the unfilled 150 resting at his cent the same tick (E14b)"
    assert b["ledger_net"] == 250 and _census(st, "exit_take") == 1 and _census(st, "rest_placed") == 1
    assert _census(st, "exit_take_rested") == 1 and _census(st, "ops_capped") == 0
    opens = [x for x in p.orders.values() if x["state"] == "open"]
    assert len(opens) == 1 and opens[0]["kind"] == "reduce" and opens[0]["qty"] == 150 and opens[0]["wire"] == 0.31
    assert b["open_order_id"] == opens[0]["id"]
    take = next(x for x in p.orders.values() if x["kind"] == "take")
    assert take["booked_filled"] == 50.0 and take["state"] != "open" and p.orders[o["id"]]["state"] == "cancelled"
    assert b["last_plan"]["exit_take_rested"] == {"take": 0.30, "rest": 0.31, "qty": 200, "filled": 50.0, "rested": 150}
    # the next tick, the bid still there: the rest is cancelled and taken
    # for the 150 (fills 50), the 100 left rests again
    v.portfolio.held[SLUG] = 250
    st2 = _tick(p, v, now=NOW + 30, http=http)
    assert [c[1] for c in _cancels(v)] == ["oid-1", "oid-2"]
    assert [c[2:6] for c in _places(v)][2:] == [(0.30, 150, True, IOC_TIF), (0.31, 100, True, GTC_TIF)]
    assert b["ledger_net"] == 200 and _census(st2, "exit_take") == 1 and _census(st2, "exit_take_rested") == 1
    # the bid gone: the standing rest at his cent is held, nothing placed
    v.bid, v.portfolio.held[SLUG] = 0.29, 200
    st3 = _tick(p, v, now=NOW + 60, http=http)
    assert len(_places(v)) == 4 and len(_cancels(v)) == 2
    assert _census(st3, "exit_out_of_tol") == 1 and _census(st3, "exit_take") == 0 and _census(st3, "exit_take_rested") == 0
    assert len([x for x in p.orders.values() if x["state"] == "open"]) == 1


def test_e4_an_exit_rest_at_his_cent_carries_no_good_till_and_an_entry_rest_still_does(monkeypatch):
    """Rule 3 under the GTD flag: an exit rest at his cent stands until
    his cent moves, so it is sent GTC with no good-till -- the venue
    never expires it into the re-quote the rule forbids; an entry's
    rest carries the TTL's good-till as before."""
    monkeypatch.setenv("PMUS_MIRROR_GTD", "on")
    p, b, v, http = _reduce_world(bid=0.29, ask=0.32)
    _tick(p, v, http=http)
    o = next(iter(p.orders.values()))
    assert o["tif"] == "GTC" and o["good_till"] is None and _places(v)[0][8] is None
    p2 = _pool()
    p2.add_book(ledger=0)
    v2 = _Venue(ask=0.33)
    _tick(p2, v2)
    o2 = next(iter(p2.orders.values()))
    assert o2["tif"] == "GTD" and o2["good_till"] is not None and _places(v2)[0][8] is not None


def test_e4_addendum_the_entry_take_first_spends_its_room_once_two_ops_and_no_replace(monkeypatch):
    """A flat book, his 300 @ 0.30, the ask at his level, the mirror's
    day room $45 (150 sh @ 0.30): the IOC goes FIRST for the plannable
    150, fills 50, and the unfilled 100 rests post-only at the wire --
    two writes (`ops` 2), the room spent ONCE (the IOC's unfilled part
    goes back before the rest is sized; without it the rest would be
    `over_room`), the rest the ONLY standing order, the IOC row
    terminal, never a second IOC; nothing was cancelled, so nothing
    counts as a replace (an IOC row is never in _SQL_REPLACES' count,
    and `_requotes_this_hour` reads 0)."""
    monkeypatch.setattr(rules, "MIRROR_DAY_USD", 45.0)
    p = _pool(fills=_his(300, long_px=0.30))             # his 0.30: the IOC's cent and the rest's wire agree
    b = p.add_book(ledger=0)
    v = _Venue(bid=0.30, ask=0.30, ioc_fill=50.0)
    st = _tick(p, v)
    pl = _places(v)
    assert [c[2:6] for c in pl] == [(0.30, 150, False, IOC_TIF), (0.30, 100, False, GTC_TIF)], pl
    assert pl[0][7] is False and pl[1][7] is True and not _cancels(v)
    assert st["ops"] == 2 and _census(st, "over_room") == 0 and _census(st, "ops_capped") == 0
    assert _census(st, "take_first") == 1 and _census(st, "take_at_his_level") == 1
    assert _census(st, "take_placed") == 1 and _census(st, "rest_placed") == 1 and st["placed_take"] == 1
    assert b["ledger_net"] == 50
    opens = [x for x in p.orders.values() if x["state"] == "open"]
    assert len(opens) == 1 and opens[0]["kind"] == "increase" and opens[0]["qty"] == 100 and opens[0]["tif"] == "GTC"
    assert b["open_order_id"] == opens[0]["id"]
    take = next(x for x in p.orders.values() if x["kind"] == "take")
    assert take["tif"] == "IOC" and take["state"] != "open" and take["booked_filled"] == 50.0 and take["reason"] == "take"
    assert b["last_plan"]["take_qty"] == 150 and b["last_plan"]["take_filled"] == 50.0
    assert "ml-replaces" not in " ".join(s for _k, s, _a in p.sent), "no rest cancelled: the count never read"
    assert p._run("fetchval", ml._SQL_REPLACES, (b["id"],)) == 0, "an IOC row is never a replace"
    # the room's arithmetic: $45 - $45 taken for the IOC + $30 back for
    # its unfilled 100 = $30 = the 100-share rest at 0.30; the tick's
    # published room is the one _global_guards read
    assert st["mirror_day_room"] == 45.0
    src = _place_src()
    assert src.index("_room_take(t, est)") < src.index('plan["take_qty"], plan["take_filled"]') < src.index("_book_delta")


def test_e4_addendum_a_short_add_takes_first_through_the_short_doors(monkeypatch):
    """The addendum on a SHORT book: his level for our BUY_SHORT is
    0.28 in long space and the plan rests at the 0.32 ask (contract
    0.32); the bid at 0.32 is at or through that wire (the contract
    sold at or above it), so the IOC goes FIRST, through the short
    open's doors, as a BUY_SHORT IOC at 0.32 -- and a bid under the
    wire rests as before."""
    _shorts_on(monkeypatch)
    p = _short_world()
    v = _Venue(bid=0.32, ask=0.32, ioc_fill=300.0)
    st = _tick(p, v, http=_short_http())
    pl = _places(v)
    assert len(pl) == 1 and pl[0][2:7] == (0.32, 300, False, IOC_TIF, SHORT)
    assert _census(st, "take_first") == 1 and _census(st, "short_open") == 1
    b = next(iter(p.books.values()))
    assert b["ledger_net"] == -300 and not _cancels(v)
    p2 = _short_world()
    v2 = _Venue(bid=0.30, ask=0.32)
    st2 = _tick(p2, v2, http=_short_http())
    assert [c[5] for c in _places(v2)] == [GTC_TIF] and _census(st2, "take_first") == 0


def test_e4_the_requote_credit_is_never_granted_on_the_same_wire_path_and_no_ioc_spends_it(monkeypatch):
    """E4 on E2 v4 (review round 3, LOW-6: a TTL or replace cancel and
    the rest that follows it on the same book are ONE op,
    `_Tick.requote_credit`, spent in _place by a non-IOC alone). Where
    the two meet: (1) an exit rest past its TTL at his cent is NEVER
    cancelled -- _reconcile_open's TTL clause leaves a reduce rest to
    the plan and keep_or_replace(stands) keeps it -- so the credit is
    never GRANTED on the same-wire path: nothing to clear, no stale
    credit a later placement on the book could spend, `ops` 0. (2) A
    take never spends it: an entry rest cancelled by its TTL under an
    exit plan within the cent is the cancel's op plus the IOC's own
    (`ops` 2), the credit left unspent and dead with the tick (a
    _Tick is built per tick). (3) The take-first after a TTL cancel:
    the cancel's op, the IOC's own, the remainder's rest on the credit
    -- cancel + IOC + rest is `ops` 2. (4) With the budget gone on the
    cancel the IOC is `ops_capped` and the credited rest STILL goes
    (LOW-6's promise, kept under the take-first): the book is not left
    bare for the tick with its rest cancelled."""
    seen, reasons = [], []
    orig_act, orig_cs = ml._act, ml._cancel_and_settle

    async def _spy(t, *a, **k):
        res = await orig_act(t, *a, **k)
        seen.append(set(t.requote_credit))
        return res

    async def _cs(t, o, book, reason, exit=False, decision=None):
        reasons.append(reason)
        return await orig_cs(t, o, book, reason, exit=exit, decision=decision)
    monkeypatch.setattr(ml, "_act", _spy)
    monkeypatch.setattr(ml, "_cancel_and_settle", _cs)
    ttl = float(rules.MIRROR_REST_TTL_S)
    # (1) the same-wire path: an exit rest at his cent past its TTL, the bid outside the cent
    p, b, v, http = _reduce_world(bid=0.29, ask=0.32)
    o = p.add_order(b, side=SELL, wire=0.31, qty=200, kind="reduce", placed_ts=NOW - ttl - 1)
    v.rest("oid-1", "SELL", 0.31, 200)
    st = _tick(p, v, http=http)
    assert not _cancels(v) and not _places(v) and st["ops"] == 0 and st["requotes"] == 0
    assert _census(st, "requote_same_wire") == 1 and reasons == [] and seen == [set()]
    assert p.orders[o["id"]]["state"] == "open" and b["ledger_net"] == 300
    # (2) an entry rest past its TTL under an exit plan within the cent:
    # the TTL cancel grants the credit, the exit's IOC is its own op
    seen.clear()
    reasons.clear()
    p2, b2, v2, http2 = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0)
    p2.add_order(b2, side=BUY, wire=0.30, qty=100, kind="increase", placed_ts=NOW - ttl - 1)
    v2.rest("oid-1", "BUY", 0.30, 100)
    st2 = _tick(p2, v2, http=http2)
    assert _cancels(v2) == [("cancel", "oid-1", SLUG)] and reasons == ["ttl"] and st2["requotes"] == 1
    assert [c[2:6] for c in _places(v2)] == [(0.30, 200, True, IOC_TIF)]
    assert st2["ops"] == 2 and _census(st2, "exit_take") == 1 and _census(st2, "ops_capped") == 0
    assert b2["ledger_net"] == 100 and seen == [{b2["id"]}], "the IOC never rides the credit"
    # (3) the take-first after a TTL cancel: cancel + IOC + credited rest, two ops
    seen.clear()
    reasons.clear()
    his = [_fill(M, "BUY", 300, 0.30, NOW - 3000)]
    p3 = _pool(fills=his)
    b3 = p3.add_book(ledger=0)
    p3.add_order(b3, side=BUY, wire=0.30, qty=300, kind="increase", placed_ts=NOW - ttl - 1)
    v3 = _Venue(bid=0.29, ask=0.30, ioc_fill=100.0)
    v3.rest("oid-1", "BUY", 0.30, 300)
    st3 = _tick(p3, v3)
    assert _cancels(v3) == [("cancel", "oid-1", SLUG)] and reasons == ["ttl"] and st3["requotes"] == 1
    assert [c[2:6] for c in _places(v3)] == [(0.30, 300, False, IOC_TIF), (0.29, 200, False, GTC_TIF)]
    assert st3["ops"] == 2 and _census(st3, "take_first") == 1 and _census(st3, "ops_capped") == 0
    assert seen == [set()] and b3["ledger_net"] == 100, "the remainder's rest spent the credit"
    assert len([x for x in p3.orders.values() if x["state"] == "open"]) == 1
    # (4) the budget gone on the cancel: the IOC is ops_capped, the credited rest still goes
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 1)
    seen.clear()
    reasons.clear()
    p4 = _pool(fills=his)
    b4 = p4.add_book(ledger=0)
    p4.add_order(b4, side=BUY, wire=0.30, qty=300, kind="increase", placed_ts=NOW - ttl - 1)
    v4 = _Venue(bid=0.29, ask=0.30, ioc_fill=100.0)
    v4.rest("oid-1", "BUY", 0.30, 300)
    st4 = _tick(p4, v4)
    assert _cancels(v4) == [("cancel", "oid-1", SLUG)] and reasons == ["ttl"]
    assert [c[2:6] for c in _places(v4)] == [(0.29, 300, False, GTC_TIF)], "no IOC went; the rest did, on the credit"
    assert st4["ops"] == 1 and _census(st4, "ops_capped") == 1 and _census(st4, "rest_placed") == 1
    assert _census(st4, "take_first") == 0 and _census(st4, "take_placed") == 0 and seen == [set()]
    assert b4["ledger_net"] == 0 and len([x for x in p4.orders.values() if x["state"] == "open"]) == 1
    # the mechanism, by name
    src = inspect.getsource(ml._entry_take)
    assert 'if (res == "ops_capped" and book["id"] in t.requote_credit' in src
    assert 'if book["id"] in t.requote_credit and tif != "IOC":' in inspect.getsource(ml._place)
    assert "and not _priced_exit_rest(o, book)" in inspect.getsource(ml._reconcile_open)


# -- E4 review round 3 fold (2026-09-07): M-1 the ops budget, D-1/D-2 the
# unpriced reduce's TTL, L-3 the shadow's rounding, L-4 a refused cancel's name

def _unpriced_reduce(**venue_kw):
    """A SNAPSHOT-driven reduce he gave no price for: his only fill is
    the BUY at 0.31; the snapshot says 100; we hold 300. `exit_px_src`
    'none', the rest at the ask (0.32). The reviewer's round-3 shape."""
    p = _pool(fills=[_fill(M, "BUY", 300, 0.31, NOW - 3000)], snap={M: 100.0, N: 0.0})
    b = p.add_book(ledger=300)
    venue_kw.setdefault("held", {SLUG: 300})
    return p, b, _Venue(**venue_kw), _mkt(100.0)


def test_e4_r3_an_exit_is_exempt_from_the_ops_budget_so_its_take_at_the_last_op_is_never_shed(monkeypatch):
    """Review round 3, M-1 (MEDIUM). The exit's take off a standing rest
    is a cancel and an IOC, two ops with no credit: at the budget's LAST
    op the cancel went and the IOC was `ops_capped` -- the book had NO
    exit order for the tick, the one thing an exit is never (E2 LOW-6).
    EXITS ARE EXEMPT FROM THE PER-TICK OPS BUDGET: every cancel, IOC,
    rest and close on an exit's path -- the reduce, the paired and the
    vanish flatten, the sign-flip flatten, the short cover -- takes its
    slot whatever the count (`_op_slot(exit=True)`), still counted in
    `ops`; an ENTRY at the same budget is `ops_capped` exactly as
    before. The reviewer's c1 / c1b / c2 / c3 shapes, at budget 1 and
    at budget 0."""
    for budget in (1, 0):
        monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", budget)
        # (c1) the keep path: the rest at his cent, the bid within the cent
        p, b, v, http = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0)
        o = p.add_order(b, side=SELL, wire=0.31, qty=200, kind="reduce")
        v.rest("oid-1", "SELL", 0.31, 200)
        st = _tick(p, v, http=http)
        assert _cancels(v) == [("cancel", "oid-1", SLUG)] and p.orders[o["id"]]["reason"] == "take", budget
        assert [c[2:6] for c in _places(v)] == [(0.30, 200, True, IOC_TIF)], budget
        assert _census(st, "exit_take") == 1 and _census(st, "ops_capped") == 0 and st["ops"] == 2, budget
        assert b["ledger_net"] == 100 and not [x for x in p.orders.values() if x["state"] == "open"], budget
        assert b["state"] == "live" and b["last_reason"] != "ops_capped", budget
        # (c2) the replace path: the rest at the pre-E4 cent 0.32, his cent
        # 0.31, the bid within the cent: the replace's cancel, then the IOC
        p2, b2, v2, http2 = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0)
        o2 = p2.add_order(b2, side=SELL, wire=0.32, qty=200, kind="reduce")
        v2.rest("oid-1", "SELL", 0.32, 200)
        st2 = _tick(p2, v2, http=http2)
        assert p2.orders[o2["id"]]["reason"] == "replace" and st2["requotes"] == 1, budget
        assert [c[2:6] for c in _places(v2)] == [(0.30, 200, True, IOC_TIF)], budget
        assert _census(st2, "exit_take") == 1 and _census(st2, "ops_capped") == 0 and st2["ops"] == 2, budget
        assert b2["ledger_net"] == 100, budget
        # the replace with the bid outside the cent: the cancel, then the
        # re-rest at his cent on the credit (one op for the pair)
        p3, b3, v3, http3 = _reduce_world(bid=0.29, ask=0.32)
        o3 = p3.add_order(b3, side=SELL, wire=0.32, qty=200, kind="reduce")
        v3.rest("oid-1", "SELL", 0.32, 200)
        st3 = _tick(p3, v3, http=http3)
        assert p3.orders[o3["id"]]["reason"] == "replace", budget
        assert [c[2:6] for c in _places(v3)] == [(0.31, 200, True, GTC_TIF)], budget
        assert _census(st3, "exit_out_of_tol") == 1 and _census(st3, "rest_placed") == 1, budget
        assert _census(st3, "ops_capped") == 0 and st3["ops"] == 1, budget
        # no rest standing: the IOC, and the rest at his cent, each one op
        p4, b4, v4, http4 = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0)
        st4 = _tick(p4, v4, http=http4)
        assert [c[2:6] for c in _places(v4)] == [(0.30, 200, True, IOC_TIF)] and b4["ledger_net"] == 100, budget
        assert st4["ops"] == 1 and _census(st4, "ops_capped") == 0 and _census(st4, "exit_take") == 1, budget
        p5, b5, v5, http5 = _reduce_world(bid=0.29, ask=0.32)
        st5 = _tick(p5, v5, http=http5)
        assert [c[2:6] for c in _places(v5)] == [(0.31, 200, True, GTC_TIF)], budget
        assert st5["ops"] == 1 and _census(st5, "ops_capped") == 0 and _census(st5, "rest_placed") == 1, budget
        # an ENTRY at the same budget is bounded exactly as before: its
        # take-first IOC (fills 100 of 300) and the remainder's rest are
        # two ops with no credit -- at budget 1 the rest is `ops_capped`,
        # at budget 0 the IOC is; a plain rest at budget 0 too
        p6 = _pool()
        b6 = p6.add_book(ledger=0)
        v6 = _Venue(bid=0.30, ask=0.31, ioc_fill=100.0)
        st6 = _tick(p6, v6)
        assert [c[2:6] for c in _places(v6)] == [(0.31, 300, False, IOC_TIF)][:budget], budget
        assert _census(st6, "ops_capped") == 1 and st6["ops"] == budget and b6["ledger_net"] == 100 * budget, budget
        assert not [x for x in p6.orders.values() if x["state"] == "open"], budget
        p7 = _pool()
        b7 = p7.add_book(ledger=0)
        v7 = _Venue(bid=0.30, ask=0.32)
        st7 = _tick(p7, v7)
        assert len(_places(v7)) == budget and _census(st7, "ops_capped") == 1 - budget, budget
        assert b7["last_reason"] == ("rest_placed" if budget else "ops_capped"), budget
    # the other exit paths at budget 0: the sign-flip flatten's take, the
    # unpriced vanish's cancel + close, the short cover's close -- every
    # write goes, every one counted in `ops`
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 63, 0.46, NOW - 9000), _fill(N, "BUY", 100, 0.55, NOW - 3100),
             _fill(M, "SELL", 63, 0.4595, NOW - 3000)]
    p8 = _pool(fills=fills, snap={M: 0.0, N: 100.0})
    b8 = p8.add_book(ledger=63, avg_cost=0.46)
    v8 = _Venue(bid=0.45, ask=0.47, held={SLUG: 63}, ioc_fill=63.0)
    st8 = _tick(p8, v8, http=_mkt(0.0, 100.0))
    assert _census(st8, "sign_flip") == 1 and [c[2:6] for c in _places(v8)] == [(0.45, 63, True, IOC_TIF)]
    assert b8["ledger_net"] == 0 and st8["ops"] == 1 and _census(st8, "ops_capped") == 0
    p9 = _pool(fills=_unpriced(), snap=None)
    b9 = p9.add_book(ledger=300)
    p9.add_order(b9, side=SELL, wire=0.32, kind="flatten_vanished",
                 placed_ts=NOW - rules.MIRROR_FLATTEN_REST_S - 1)
    v9 = _Venue(held={SLUG: 300})
    v9.rest("oid-1", "SELL", 0.32, 300, created=NOW - 400)
    st9 = _tick(p9, v9, http=_gone())
    assert ("cancel", "oid-1", SLUG) in v9.calls and ("close", SLUG, le.EXIT_SLIPPAGE_BIPS) in v9.calls
    assert b9["ledger_net"] == 0 and st9["ops"] == 2 and _census(st9, "ops_capped") == 0
    sfills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
              _fill(N, "SELL", 400, 0.70, NOW - 2000), _fill(M, "SELL", 100, 0.31, NOW - 1000)]

    async def _held(t, slug):
        return 300, 0.32
    monkeypatch.setattr(ml, "_pm_held", _held)
    p10 = _short_world(fills=sfills, snap=None)
    b10 = _short_book(p10, ledger=-300)
    v10 = _Venue(bid=0.30, ask=0.31, held={SLUG: -300}, ioc_fill=300.0)
    st10 = _tick(p10, v10, http=_gone())
    assert [c[2:6] for c in _places(v10)] == [(0.31, 300, True, IOC_TIF)] and b10["ledger_net"] == 0
    assert st10["ops"] == 1 and _census(st10, "ops_capped") == 0 and _census(st10, "short_flatten_close") == 1
    # the mechanism, by name: the slot's `exit`, read off the side's leg
    # action in _place, passed by every exit cancel, the flatten's slot
    src = inspect.getsource(ml._op_slot)
    assert "if not exit and t.ops + t.ops_pending >= rules.MIRROR_MAX_ORDER_OPS_PER_TICK:" in src
    assert 'exit=rules.leg_action(book.get("intent"), side) == "reduce"' in inspect.getsource(ml._place)
    act = inspect.getsource(ml._act)
    # (FILL lane 3, 2026-09-08: the two keep-branch band sites cancel under "take" too, 3 -> 5)
    assert act.count('"take", exit=') == 5 and '"replace", exit=is_exit' in act and "decision, exit=is_exit" in act
    assert "slot = _op_slot(t, w, exit=True)" in inspect.getsource(ml._flatten_vanished)


def test_e4_r3_an_unpriced_reduce_rest_keeps_its_ttl_requote_and_a_priced_one_stands(monkeypatch):
    """Review round 3, D-1 / D-2 (LOW). Step O's TTL clause skipped EVERY
    reduce rest, so an UNPRICED reduce's TTL re-quote ran through
    keep_or_replace's age clause under the reason 'replace' -- counted by
    _SQL_REPLACES against the book's ENTRY budget (a 'ttl' never was) --
    and on an abandoned tick (which never plans) the rest stood past
    its TTL for the whole backoff. Narrowed to PRICED exit rests (the
    book's last plan says `exit_px_src: 'his_fill'`, _priced_exit_rest):
    an unpriced reduce rest keeps today's `ttl` re-quote, `requotes` 1,
    never a replace, and is TTL'd through an abandoned tick as before;
    a priced rest still stands (`requote_same_wire`; the book-29 replay
    above is unchanged)."""
    ttl = float(rules.MIRROR_REST_TTL_S)
    # (d1) the unpriced reduce rest past its TTL: cancelled `ttl`, re-rested
    # at the ask on the credit; never a replace, never counted
    p, b, v, http = _unpriced_reduce(bid=0.30, ask=0.32)
    o = p.add_order(b, side=SELL, wire=0.32, qty=200, kind="reduce", placed_ts=NOW - ttl - 1)
    v.rest("oid-1", "SELL", 0.32, 200)
    st = _tick(p, v, http=http)
    assert b["last_plan"]["exit_px_src"] == "none" and b["last_plan"]["exit_px"] is None
    assert _cancels(v) == [("cancel", "oid-1", SLUG)] and [c[2:6] for c in _places(v)] == [(0.32, 200, True, GTC_TIF)]
    assert p.orders[o["id"]]["state"] == "cancelled" and p.orders[o["id"]]["reason"] == "ttl"
    assert st["requotes"] == 1 and st["ops"] == 1 and _census(st, "requote_same_wire") == 0
    assert p._run("fetchval", ml._SQL_REPLACES, (b["id"],)) == 0, "a ttl is never counted against the entry budget"
    assert len([x for x in p.orders.values() if x["state"] == "open"]) == 1 and b["ledger_net"] == 300
    # the same at budget 0: the TTL re-quote of an exit rest is exempt (M-1)
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 0)
    p2, b2, v2, http2 = _unpriced_reduce(bid=0.30, ask=0.32)
    o2 = p2.add_order(b2, side=SELL, wire=0.32, qty=200, kind="reduce", placed_ts=NOW - ttl - 1)
    v2.rest("oid-1", "SELL", 0.32, 200)
    st2 = _tick(p2, v2, http=http2)
    assert p2.orders[o2["id"]]["reason"] == "ttl" and [c[2:6] for c in _places(v2)] == [(0.32, 200, True, GTC_TIF)]
    assert st2["ops"] == 1 and _census(st2, "ops_capped") == 0 and st2["requotes"] == 1
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 20)
    # (d2) an ABANDONED tick: the unpriced rest is TTL'd by _abandon_reconciled
    # (as before E4); a PRICED rest at his cent stands through it (rule 3)
    p3, b3, v3, http3 = _unpriced_reduce(bid=0.30, ask=0.32, raise_walk=True)
    o3 = p3.add_order(b3, side=SELL, wire=0.32, qty=200, kind="reduce", placed_ts=NOW - ttl - 1)
    v3.rest("oid-1", "SELL", 0.32, 200)
    st3 = _tick(p3, v3, http=http3)
    assert st3["abandoned"] and _census(st3, "positions_unreadable") >= 1 and st3["orders_open"] == 1
    assert _cancels(v3) == [("cancel", "oid-1", SLUG)] and p3.orders[o3["id"]]["reason"] == "ttl"
    assert not _places(v3), "an abandoning tick plans nothing: the next tick rests again"
    p4, b4, v4, http4 = _reduce_world(bid=0.29, ask=0.32, raise_walk=True)
    b4["last_plan"] = {"kind": "reduce", "exit_px_src": "his_fill", "exit_rest": 0.31}
    o4 = p4.add_order(b4, side=SELL, wire=0.31, qty=200, kind="reduce", placed_ts=NOW - ttl - 1)
    v4.rest("oid-1", "SELL", 0.31, 200)
    st4 = _tick(p4, v4, http=http4)
    assert st4["abandoned"] and not _cancels(v4) and p4.orders[o4["id"]]["state"] == "open"
    # a priced rest at his cent past its TTL on a normal tick: stands
    p5, b5, v5, http5 = _reduce_world(bid=0.29, ask=0.32)
    # E15: the rest is the reference's own reduce (target 100, witnessed when the reference moved)
    b5["last_plan"] = {"kind": "reduce", "exit_px_src": "his_fill", "exit_rest": 0.31,
                       "reduce_ref": {"target": 100, "at": NOW - 30.0}}
    o5 = p5.add_order(b5, side=SELL, wire=0.31, qty=200, kind="reduce", placed_ts=NOW - ttl - 1)
    v5.rest("oid-1", "SELL", 0.31, 200)
    st5 = _tick(p5, v5, http=http5)
    assert not _cancels(v5) and not _places(v5) and st5["requotes"] == 0 and st5["ops"] == 0
    assert _census(st5, "requote_same_wire") == 1 and _census(st5, "exit_out_of_tol") == 1
    assert p5.orders[o5["id"]]["state"] == "open" and b5["last_plan"]["exit_px_src"] == "his_fill"
    # NO plan on file (a row placed before its plan was written, a book
    # from before E4): the row's own facts decide -- a rest whose wire is
    # the cent of the level it was placed against stands; a rest the ask
    # lifted (0.32 against his 0.31) and a rest with no level of his are
    # TTL'd once, and re-rested at his cent under the plan that then
    # keeps them
    p6, b6, v6, http6 = _reduce_world(bid=0.29, ask=0.32)
    o6 = p6.add_order(b6, side=SELL, wire=0.31, qty=200, kind="reduce", placed_ts=NOW - ttl - 1, his_level=0.31)
    v6.rest("oid-1", "SELL", 0.31, 200)
    st6 = _tick(p6, v6, http=http6)
    assert not _cancels(v6) and not _places(v6) and st6["requotes"] == 0 and p6.orders[o6["id"]]["state"] == "open"
    assert _census(st6, "requote_same_wire") == 1 and b6["last_plan"]["exit_px_src"] == "his_fill"
    for wire, lvl in ((0.32, 0.31), (0.31, None)):
        p7, b7, v7, http7 = _reduce_world(bid=0.29, ask=0.32)
        o7 = p7.add_order(b7, side=SELL, wire=wire, qty=200, kind="reduce", placed_ts=NOW - ttl - 1, his_level=lvl)
        v7.rest("oid-1", "SELL", wire, 200)
        st7 = _tick(p7, v7, http=http7)
        assert p7.orders[o7["id"]]["reason"] == "ttl" and [c[2:6] for c in _places(v7)] == [(0.31, 200, True, GTC_TIF)], (wire, lvl)
        assert st7["requotes"] == 1 and b7["last_plan"]["exit_px_src"] == "his_fill", (wire, lvl)
        now = NOW + ttl + 2
        p7.snap_at = now - 40
        v8 = _Venue(bid=0.29, ask=0.32, held={SLUG: 300})
        v8.orders = v7.orders
        st8 = _tick(p7, v8, now=now, http=http7)
        assert not _cancels(v8) and not _places(v8) and _census(st8, "requote_same_wire") == 1 and st8["requotes"] == 0, (wire, lvl)
    # an ENTRY rest past its TTL is still TTL'd, on a normal and on an
    # abandoned tick (section 3's pins, restated beside the narrowing)
    p9 = _pool()
    b9 = p9.add_book(ledger=0, last_plan={"kind": "increase", "exit_px_src": "his_fill"})
    o9 = p9.add_order(b9, side=BUY, wire=0.30, qty=300, placed_ts=NOW - ttl - 1)
    v9 = _Venue(raise_walk=True)
    v9.rest("oid-1", "BUY", 0.30, 300)
    st9 = _tick(p9, v9)
    assert st9["abandoned"] and _cancels(v9) == [("cancel", "oid-1", SLUG)] and p9.orders[o9["id"]]["reason"] == "ttl"
    # the predicate: a reduce rest under a plan priced off his fill (the
    # plan wins whatever the row says: the admin flatten's rest at his
    # cent is 'none'); with no plan on file the row's wire at the cent of
    # its own level, on a long book alone; never an entry
    lp = {"exit_px_src": "his_fill"}
    at, off = {"side": SELL, "wire": 0.31, "his_level": 0.31}, {"side": SELL, "wire": 0.32, "his_level": 0.31}
    assert ml._priced_exit_rest(off, {"intent": INTENT, "last_plan": lp}) is True
    assert ml._priced_exit_rest(off, {"intent": INTENT, "last_plan": json.dumps(lp)}) is True
    assert ml._priced_exit_rest({**at, "side": BUY}, {"intent": INTENT, "last_plan": lp}) is False
    assert ml._priced_exit_rest(at, {"intent": INTENT, "last_plan": {"exit_px_src": "none"}}) is False
    assert ml._priced_exit_rest(at, {"intent": INTENT, "last_plan": None}) is True
    assert ml._priced_exit_rest(at, {"intent": INTENT, "last_plan": {"kind": "reduce"}}) is True
    assert ml._priced_exit_rest(off, {"intent": INTENT, "last_plan": None}) is False
    assert ml._priced_exit_rest({**at, "his_level": None}, {"intent": INTENT}) is False
    assert ml._priced_exit_rest({**at, "wire": None}, {"intent": INTENT}) is False
    assert ml._priced_exit_rest({"side": SELL, "wire": 0.46, "his_level": 0.4595}, {"intent": INTENT}) is True
    # on a SHORT book the cover-side rest (a BUY) is the reduce: the plan
    # decides; with none on file it is never read as priced
    assert ml._priced_exit_rest({**at, "side": BUY}, {"intent": SHORT, "last_plan": lp}) is True
    assert ml._priced_exit_rest({**at, "side": BUY}, {"intent": SHORT, "last_plan": None}) is False
    assert ml._priced_exit_rest(at, {"intent": SHORT, "last_plan": lp}) is False


def test_e4_r3_the_live_and_shadow_levels_round_his_other_token_equivalent_alike():
    """Review round 3, L-3 (LOW). mirror_live._his_level handed the
    other-token equivalent back unrounded (0.47996 -> 0.5200400000000001)
    and mirror_shadow.his_level to 4 places (0.52): the shadow's exit leg
    then recorded `exit_rest_px` 0.52, a cent under the live rest at
    0.53. Both round the same way now, round(1 - p, 6) -- the executor's
    own precision -- so the shadow's rest cent IS the live rest's, on
    every fixture, and float noise (1 - 0.77) reads as 0.23 in both."""
    fills = [_fill(M, "BUY", 300, 0.31, NOW - 3000), _fill(N, "BUY", 200, 0.47996, NOW - 1000)]
    live, shadow = ml._his_level(fills, M, N, reducing=True), ms.his_level(fills, M, N, reducing=True)
    assert live == shadow == 0.52004
    assert rules.exit_terms(SELL, live)["rest"] == 0.53 == rules.exit_terms(SELL, shadow)["rest"]
    d = ms.exit_leg({"kind": "reduced", "move": "m", "at": 1.0, "size": 1.0, "px_equiv": shadow,
                     "complement_px": 0.48, "net_before": 1.0, "net_after": 0.0},
                    1.0, 1.0, 0, ms.mi.Plan("SELL_LONG", 63, 0.52, "r"), shadow)
    assert d["exit_rest_px"] == 0.53 and d["exit_take_px"] == 0.52 and d["exit_floor"] == pytest.approx(0.51004)
    for q in (0.46, 0.55, 0.70, 0.72, 0.77, 0.47996, 0.4595, 0.1234567, 0.999999):
        fs = [_fill(N, "BUY", 100, q, NOW - 100)]
        assert ml._his_level(fs, M, N, reducing=True) == ms.his_level(fs, M, N, reducing=True) == round(1.0 - q, 6), q
        assert ml._his_level([_fill(N, "SELL", 100, q, NOW - 100)], M, N, reducing=False, short=True) == round(1.0 - q, 6), q
    assert ml._his_level([_fill(N, "BUY", 100, 0.77, NOW - 100)], M, N, reducing=True) == 0.23
    assert "round(1.0 - p, 6)" in inspect.getsource(ms.his_level) and inspect.getsource(ml._his_level).count("round(1.0 - p, 6)") == 2
    # the worker's rest on that fill is 0.53, the plan carries the exact figure
    p = _pool(fills=fills, snap={M: 300.0, N: 200.0})       # his net 100: a reduce of 200 priced off 0.52004
    b = p.add_book(ledger=300)
    v = _Venue(bid=0.50, ask=0.54, held={SLUG: 300})
    st = _tick(p, v, http=_mkt(300.0, 200.0))
    assert [c[2:6] for c in _places(v)] == [(0.53, 200, True, GTC_TIF)] and _census(st, "exit_out_of_tol") == 1
    lp = b["last_plan"]
    assert lp["exit_px"] == 0.52004 and lp["exit_rest"] == 0.53 and lp["exit_take"] == 0.52 and lp["exit_px_src"] == "his_fill"


def test_e4_r3_a_refused_cancel_names_the_plan_cancel_refused_never_take(monkeypatch):
    """Review round 3, L-4 (LOW). A take's cancel the budget refused left
    the rest standing (right) and the plan read 'take' with nothing
    sent. The plan now reads `cancel_refused:<reason>`: reachable on an
    ENTRY's take at the cap and on an entry's replace; unreachable on an
    exit (exempt, M-1) -- pinned by a refusal forced onto the exit path.
    A cancel that went out and left the order unknown reads
    `cancel_pending`, the freeze's own name, on every path."""
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 0)
    p = _pool()                                              # his 0.31: the entry's take cent
    b = p.add_book(ledger=0)
    o = p.add_order(b, side=BUY, wire=0.30, qty=300, kind="increase")
    v = _Venue(bid=0.30, ask=0.31, ioc_fill=300.0)
    v.rest("oid-1", "BUY", 0.30, 300)
    st = _tick(p, v)
    assert not _cancels(v) and not _places(v) and p.orders[o["id"]]["state"] == "open"
    assert _census(st, "ops_capped") == 1 and _census(st, "take_at_his_level") == 1 and _census(st, "take_first") == 0
    assert b["last_reason"] == "cancel_refused:ops_capped" and b["ledger_net"] == 0 and b["state"] == "live"
    # the entry's replace at the cap: the same name, the rest standing
    p2 = _pool()
    b2 = p2.add_book(ledger=0)
    o2 = p2.add_order(b2, wire=0.28)
    v2 = _Venue()
    v2.rest("oid-1", price=0.28)
    st2 = _tick(p2, v2)
    assert not _cancels(v2) and not _places(v2) and p2.orders[o2["id"]]["state"] == "open"
    assert _census(st2, "ops_capped") == 1 and b2["last_reason"] == "cancel_refused:ops_capped"
    # the exit path cannot be refused by the budget (M-1): a refusal forced
    # on it is still named, never 'take', the rest standing, no IOC
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 20)
    orig = ml._cancel_and_settle

    async def _refused(t, o, book, reason, exit=False):
        ml._mirror_stop("ops_capped", o.get("whale"))
        return "ops_capped"
    monkeypatch.setattr(ml, "_cancel_and_settle", _refused)
    p3, b3, v3, http3 = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0)
    o3 = p3.add_order(b3, side=SELL, wire=0.31, qty=200, kind="reduce")
    v3.rest("oid-1", "SELL", 0.31, 200)
    st3 = _tick(p3, v3, http=http3)
    assert not _cancels(v3) and not _places(v3) and p3.orders[o3["id"]]["state"] == "open"
    assert _census(st3, "exit_take") == 0 and b3["last_reason"] == "cancel_refused:ops_capped" and b3["ledger_net"] == 300
    monkeypatch.setattr(ml, "_cancel_and_settle", orig)
    # the cancel sent, the venue's answer inconclusive: frozen cancel_pending, the plan says so
    p4, b4, v4, http4 = _reduce_world(bid=0.30, ask=0.32, ioc_fill=200.0, cancel_ok=False)
    o4 = p4.add_order(b4, side=SELL, wire=0.31, qty=200, kind="reduce")
    v4.rest("oid-1", "SELL", 0.31, 200)
    st4 = _tick(p4, v4, http=http4)
    assert len(_cancels(v4)) == 2 and not _places(v4) and p4.orders[o4["id"]]["state"] == "unknown"
    assert b4["state"] == "frozen" and b4["frozen_reason"] == "cancel_pending" and b4["last_reason"] == "cancel_pending"
    assert _census(st4, "exit_take") == 0 and b4["ledger_net"] == 300
    src = inspect.getsource(ml._cancel_outcome)
    assert 'return f"cancel_refused:{res}"' in src and 'return "cancel_pending"' in src


def test_e4_constants_the_exit_tolerance_tightens_only_the_take_wait_is_zero_and_the_keys(monkeypatch):
    """rules.MIRROR_EXIT_TOL is 0.01 and the environment may only
    TIGHTEN it (to 0); rules.MIRROR_TAKE_AFTER_S is 0 and may only
    lengthen; neither is restated in the worker; the four census keys
    sit before the pinned last key."""
    import importlib
    assert rules.MIRROR_EXIT_TOL == 0.01 and rules.MIRROR_TAKE_AFTER_S == 0.0
    for raw, want in (("0.05", 0.01), ("0.005", 0.005), ("0", 0.0), ("-1", 0.0), ("junk", 0.01), ("inf", 0.01)):
        monkeypatch.setenv("MIRROR_EXIT_TOL", raw)
        try:
            assert importlib.reload(rules).MIRROR_EXIT_TOL == want, raw
        finally:
            monkeypatch.delenv("MIRROR_EXIT_TOL")
    importlib.reload(rules)
    assert rules.MIRROR_EXIT_TOL == 0.01
    assert "MIRROR_EXIT_TOL" in rules.__all__ and "exit_terms" in rules.__all__
    src = inspect.getsource(ml)
    assert "MIRROR_EXIT_TOL =" not in src and "MIRROR_TAKE_AFTER_S =" not in src
    for k in ("exit_take", "exit_out_of_tol", "requote_same_wire", "take_first"):
        assert k in ml.CENSUS_KEYS and k in ml._new_stats()["census"], k
    assert ml.CENSUS_KEYS[-1] == "cand_terminal_skipped"
    assert ml.CENSUS_KEYS.index("take_first") < ml.CENSUS_KEYS.index("cand_terminal_skipped")
    # a PRICED exit rest's TTL is the plan's to decide: the reconcile
    # step's TTL cancel skips a reduce rest at his cent (round 3, D-1:
    # an unpriced one keeps its `ttl`); the exemptions from the replace
    # budget sit at the keep and the replace paths
    rec = inspect.getsource(ml._reconcile_open)
    assert "and not _priced_exit_rest(o, book)" in rec and 'cancel_reason = "ttl"' in rec
    act = inspect.getsource(ml._act)
    assert act.count("not _exit_or_flip(book, p, o)") == 2, "rule 4 is E2's exemption, one mechanism"
    assert "stands=priced_exit" in act and 'return await _entry_take(' in act


# ---------------- 22. S4: the short cover as a priced order; the read-back proof (2026-09-07)
#
# Every short close on 2026-09-07 failed within a second: the venue's
# reason (4a5da1f's logging) reads `pmus: close_position <slug> failed:
# BadRequestError: Price is required for limit order` -- 11 CLOSE rows,
# 0 executed. So the cover is a REAL order through the placement
# machinery the long side uses (a BUY of the long token with the closing
# intent, SELL_SHORT on the wire; E4's exit rule mirrored: the rest at
# floor(his), ONE IOC at the ceiling cent whenever the ask is at or
# under it, held outside), gated by the 1-share read-back proof under
# `mirror_s4_proof`. The census keys: s4_probe_placed, s4_proved,
# s4_unproven, short_cover_rest, short_cover_take, short_cover_out_of_tol.

def _flip_world(**venue_kw):
    """His net +300 (the default fixture: 300 long at 0.31) against our
    short of 300: the sign flip, the cover priced off his long BUY 0.31
    (ceiling 0.32, the ask there: the IOC)."""
    p = _pool()
    b = _short_book(p, ledger=-300)
    venue_kw.setdefault("held", {SLUG: -300})
    return p, b, _NoClose(**venue_kw)


def test_s4_the_read_back_probe_runs_once_on_the_first_live_short_book_with_a_two_sided_quote_proves_and_gates_the_cover(monkeypatch):
    """The key absent: on the first tick with a live short book and a
    two-sided quote ONE 1-share post-only BUY of the long token with the
    closing intent goes at max(0.01, floor(bid - 0.05)) = 0.25, is read
    back through open_orders on the slug (price 0.25, quantity 1,
    intent SELL_SHORT as the venue reports it), cancelled, and the key
    records {proved: true, echo}. The cover goes on the SAME tick. A
    malformed key reads as absent. The probe never runs twice in a tick,
    on a frozen book, on a locked book, or inside an hour of a recorded
    mismatch; a malformed key is unreadable and places nothing."""
    _shorts_on(monkeypatch)
    p, b, v = _flip_world(ioc_fill=300.0)
    p.state.pop("mirror_s4_proof")
    st = _tick(p, v)
    pl = _places(v)
    assert [c[1:] for c in pl] == [(SLUG, 0.25, 1, True, GTC_TIF, SHORT, True, None),
                                   (SLUG, 0.32, 300, True, IOC_TIF, SHORT, False, None)]
    k = _kinds(v)
    i_probe = k.index("place")
    assert k[i_probe + 1:i_probe + 3] == ["open_orders", "cancel"], "read back, then cancelled, before anything else"
    assert ("open_orders", [SLUG]) in v.calls and ("cancel", "oid-1", SLUG) in v.calls
    assert v.orders["oid-1"]["state"] == "cancelled"
    rec = p.state["mirror_s4_proof"]
    assert rec["proved"] is True and rec["why"] is None and rec["at"] == NOW and rec["slug"] == SLUG
    assert rec["order_id"] == "oid-1" and rec["price"] == 0.25 and rec["cancel"] == {"ok": True}
    # the echo carries the venue's OWN side beside the desk's (S4 v2, F1):
    # a SELL_SHORT that buys the contract back reads ORDER_SIDE_BUY
    assert rec["echo"] == {"order_id": "oid-1", "us_market_slug": SLUG, "side": "BUY",
                           "venue_side": "ORDER_SIDE_BUY", "intent": "ORDER_INTENT_SELL_SHORT",
                           "price": 0.25, "quantity": 1.0, "state": "new", "tif": "GOOD_TILL_CANCEL"}
    assert rec["bid"] == 0.30 and rec["ask"] == 0.32, "the quote the probe was placed against (F2)"
    assert _census(st, "s4_probe_placed") == 1 and _census(st, "s4_proved") == 1 and _census(st, "s4_unproven") == 0
    assert _census(st, "short_cover_take") == 1 and b["ledger_net"] == 0 and _census(st, "short_flatten_close") == 1
    assert not [o for o in p.orders.values() if o["qty"] == 1], "no mirror_orders row for the probe"
    pr = [r for r in st["recent"] if r["what"] == "s4_probe"]
    assert pr and pr[0]["proved"] is True and pr[0]["order"] == "oid-1" and pr[0]["price"] == 0.25
    assert st["ops"] == 2 and _census(st, "venue_calls") >= 4, "the probe's place and cancel are counted writes"
    # proved: the next tick probes nothing more
    p2, b2, v2 = _flip_world(ioc_fill=300.0)
    p2.state["mirror_s4_proof"] = dict(rec)
    st2 = _tick(p2, v2)
    assert [c[3] for c in _places(v2)] == [300] and _census(st2, "s4_probe_placed") == 0
    # a malformed key is an UNREADABLE key: nothing is placed on a key we
    # cannot record into -- no probe, the cover refused by name
    p3, b3, v3 = _flip_world(ioc_fill=300.0)
    p3.state["mirror_s4_proof"] = "garbage"
    st3 = _tick(p3, v3)
    assert not _places(v3) and _census(st3, "s4_probe_placed") == 0 and _census(st3, "s4_unproven") == 1
    assert b3["last_plan"]["s4"] == {"unproven": "malformed"} and b3["ledger_net"] == -300
    # inside the hour of a recorded mismatch: no probe, the cover refused by name
    p4, b4, v4 = _flip_world(ioc_fill=300.0)
    _s4_unproven(p4, at=NOW - 3599.0)
    st4 = _tick(p4, v4)
    assert not _places(v4) and _census(st4, "s4_unproven") == 1 and _census(st4, "s4_probe_placed") == 0
    assert b4["ledger_net"] == -300 and b4["state"] == "live" and b4["last_plan"]["s4"] == {"unproven": "wrong_price"}
    # an hour on: the probe again
    v5 = _NoClose(held={SLUG: -300}, ioc_fill=300.0)
    st5 = _tick(p4, v5, now=NOW + 1)
    assert [c[3] for c in _places(v5)] == [1, 300] and _census(st5, "s4_proved") == 1 and b4["ledger_net"] == 0
    # a LOCKED book (bid == ask) is not a two-sided quote: no probe, refused
    p6, b6, v6 = _flip_world(bid=0.30, ask=0.30, ioc_fill=300.0)
    p6.state.pop("mirror_s4_proof")
    st6 = _tick(p6, v6)
    assert not _places(v6) and _census(st6, "s4_probe_placed") == 0 and _census(st6, "s4_unproven") == 1
    # the probe's price never under 0.01 and always floored to the cent
    assert ml._s4_probe_price(0.30) == 0.25 and ml._s4_probe_price(0.03) == 0.01 and ml._s4_probe_price(0.0549) == 0.01
    assert ml._s4_probe_price(0.999) == 0.94 and ml.S4_PROBE_GAP_S == 3600.0 and ml.S4_PROBE_OFFSET == 0.05


def test_s4_a_probe_mismatch_records_why_and_the_echo_and_the_cover_stays_refused_by_name(monkeypatch):
    """Every way the read-back can fail records {proved: false, why,
    echo} and refuses the cover `s4_unproven` (the book held, no
    freeze): the venue resting our order at another price; reporting no
    intent, or another; not listing it; refusing it; raising on it;
    and a cancel the venue refused (the order may still rest: logged,
    unproven, the id kept on the record for the retry -- S4 v3, GAP 2).
    A probe that executed at create is `filled_at_create` AND booked
    (S4 v3, GAP 1): section 22c."""
    _shorts_on(monkeypatch)

    class _WrongPrice(_NoClose):
        def rest(self, oid, side="BUY", price=0.30, qty=300, slug=SLUG, created=None, state="new",
                 filled=0.0, avg=None, intent=None):
            return super().rest(oid, side, round(price + 0.01, 2), qty, slug, created, state, filled, avg, intent)

    class _NoIntent(_NoClose):
        def rest(self, oid, side="BUY", price=0.30, qty=300, slug=SLUG, created=None, state="new",
                 filled=0.0, avg=None, intent=None):
            return super().rest(oid, side, price, qty, slug, created, state, filled, avg, None)

    class _WrongIntent(_NoClose):
        def rest(self, oid, side="BUY", price=0.30, qty=300, slug=SLUG, created=None, state="new",
                 filled=0.0, avg=None, intent=None):
            return super().rest(oid, side, price, qty, slug, created, state, filled, avg, "ORDER_INTENT_SELL_LONG")

    class _NotListed(_NoClose):
        def open_orders(self, slugs=None):
            self.calls.append(("open_orders", slugs))
            return []

    class _ListRaises(_NoClose):
        """The per-slug read-back raises; the tick's own open-orders read (no slugs) answers."""
        def open_orders(self, slugs=None):
            if slugs:
                self.calls.append(("open_orders", slugs))
                raise RuntimeError("list down")
            return super().open_orders(slugs)

    def _refuse(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "rejected", "fill_price": None,
                "filled_shares": 0.0, "raw": {"status_code": 400, "error": "minimum order size"}}

    cases = [
        (lambda: _WrongPrice(held={SLUG: -300}), "wrong_price", {"price": 0.26}),
        (lambda: _NoIntent(held={SLUG: -300}), "intent_missing", {"intent": None}),
        (lambda: _WrongIntent(held={SLUG: -300}), "wrong_intent", {"intent": "ORDER_INTENT_SELL_LONG"}),
        (lambda: _NotListed(held={SLUG: -300}), "not_found", {"open_on_slug": 0}),
        (lambda: _ListRaises(held={SLUG: -300}), "read_back_raised:RuntimeError", None),
        (lambda: _NoClose(held={SLUG: -300}, place=_refuse), "place_refused:rejected", None),
        (lambda: _NoClose(held={SLUG: -300}, place_raises=RuntimeError("socket reset")),
         "place_raised:RuntimeError", {"error": "socket reset"}),
        (lambda: _NoClose(held={SLUG: -300}, cancel_ok=False), "cancel_failed", {"price": 0.25}),
    ]
    for mk, why, echo_part in cases:
        p = _pool()
        b = _short_book(p, ledger=-300)
        p.state.pop("mirror_s4_proof")
        v = mk()
        st = _tick(p, v)
        rec = p.state["mirror_s4_proof"]
        assert rec["proved"] is False and rec["why"] == why and rec["at"] == NOW, (why, rec)
        if echo_part is not None:
            for k2, val in echo_part.items():
                assert (rec["echo"] or {}).get(k2) == val, (why, rec["echo"])
        assert _census(st, "s4_probe_placed") == 1 and _census(st, "s4_proved") == 0, why
        assert _census(st, "s4_unproven") == 1 and b["last_plan"]["s4"] == {"unproven": why}, why
        assert not [c for c in _places(v) if c[3] != 1], (why, "no cover")
        assert b["ledger_net"] == -300 and b["state"] == "live" and "close" not in _kinds(v), why
        if why not in ("place_refused:rejected", "place_raised:RuntimeError"):
            assert ("cancel", "oid-1", SLUG) in v.calls, (why, "the probe order is cancelled whatever the verdict")
        if why == "cancel_failed":
            # the id stays on the record beside the refusal (S4 v3, GAP 2)
            assert rec["cancel"]["ok"] is False and rec["cancel"]["error"] == "boom"
            assert rec["order_id"] == "oid-1" and rec["cancel"]["retries"] == 0
        else:
            assert rec["order_id"] is None or rec["cancel"] == {"ok": True}, why
        assert not [o for o in p.orders.values() if o["qty"] == 1]
    # the recorded mismatch holds the cover on the next tick without a second
    # probe; the held id is retried first and, the venue having no record of
    # it, cleared (S4 v3, GAP 2)
    v2 = _NoClose(held={SLUG: -300})
    st2 = _tick(p, v2, now=NOW + 30)
    assert not _places(v2) and _census(st2, "s4_unproven") == 1 and _census(st2, "s4_probe_placed") == 0
    assert _kinds(v2)[:2] == ["cancel", "status"] and p.state["mirror_s4_proof"]["order_id"] is None
    assert p.state["mirror_s4_proof"]["cancel"]["gone"] == "no_record"


def test_s4_a_frozen_short_book_is_untouched_no_probe_no_cover(monkeypatch):
    _shorts_on(monkeypatch)
    p, b, v = _flip_world(ioc_fill=300.0)
    p.state.pop("mirror_s4_proof")
    b.update(state="frozen", frozen_reason="placement_lost", frozen_ts=NOW - 100)
    # the 'placing' row of the lost placement keeps the book non-terminal: no thaw
    p.add_order(b, side=BUY, wire=0.31, qty=300, kind="flatten_paired", state="placing", order_id=None,
                placed_ts=NOW - 10, intent="ORDER_INTENT_SELL_SHORT")
    st = _tick(p, v)
    assert not _places(v) and "close" not in _kinds(v) and "mirror_s4_proof" not in p.state
    assert _census(st, "s4_probe_placed") == 0 and _census(st, "s4_unproven") == 0
    assert b["state"] == "frozen" and b["ledger_net"] == -300 and b["last_plan"]["kind"] == "frozen"
    # a book frozen THIS tick (the venue disagrees) is not probed either
    # -- nor on the suspect tick before it (E16: the first read)
    p2, b2, v2 = _flip_world(held={SLUG: -100}, ioc_fill=300.0)
    p2.state.pop("mirror_s4_proof")
    st1 = _tick(p2, v2)
    assert b2["state"] == "live" and _census(st1, "venue_ledger_suspect") == 1
    assert not _places(v2) and _census(st1, "s4_probe_placed") == 0
    st2 = _tick(p2, v2, now=NOW + 15)
    assert b2["state"] == "frozen" and b2["frozen_reason"] == "venue_ledger_disagree"
    assert not _places(v2) and _census(st2, "s4_probe_placed") == 0


def test_s4_the_cover_rest_stands_outside_the_cent_past_its_ttl_and_takes_the_tick_the_ask_returns(monkeypatch):
    """Book 29's shape on a short: his buy-back 0.30, the ask far above
    (0.40): the rest at 0.30 stands through five TTL periods with no
    cancel and no re-place (`requote_same_wire`), held by name each
    tick; the ask back at 0.31 takes at once."""
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
             _fill(N, "SELL", 400, 0.70, NOW - 2000)]
    p = _short_world(fills=fills, snap=None)
    b = _short_book(p, ledger=-300)
    v = _NoClose(bid=0.29, ask=0.40, held={SLUG: -300})
    st = _tick(p, v, http=_gone())
    assert [c[2:6] for c in _places(v)] == [(0.30, 300, True, GTC_TIF)] and _census(st, "short_cover_rest") == 1
    o = next(iter(p.orders.values()))
    ttl = float(rules.MIRROR_REST_TTL_S)
    for k in range(1, 6):
        now = NOW + k * (ttl + 1)
        vk = _NoClose(bid=0.29, ask=0.40, held={SLUG: -300})
        vk.orders = v.orders
        stk = _tick(p, vk, now=now, http=_gone())
        assert not _cancels(vk) and not _places(vk) and stk["ops"] == 0, k
        assert _census(stk, "requote_same_wire") == 1 and _census(stk, "short_cover_out_of_tol") == 1, k
        assert _census(stk, "exit_out_of_tol") == 1 and _census(stk, "short_cover_take") == 0, k
    assert o["state"] == "open" and b["ledger_net"] == -300 and b["state"] == "live" and len(p.orders) == 1
    v6 = _NoClose(bid=0.30, ask=0.31, held={SLUG: -300}, ioc_fill=300.0)
    v6.orders = v.orders
    st6 = _tick(p, v6, now=NOW + 6 * (ttl + 1), http=_gone())
    assert _cancels(v6) == [("cancel", "oid-1", SLUG)] and [c[2:6] for c in _places(v6)] == [(0.31, 300, True, IOC_TIF)]
    assert _census(st6, "short_cover_take") == 1 and b["ledger_net"] == 0
    # the plan's fields on the held tick
    assert b["last_plan"]["exit_rest"] == 0.30 and b["last_plan"]["exit_cover"] == 0.31


def test_s4_a_partial_cover_fill_leaves_nothing_resting_and_the_take_never_fires_twice_for_one_rest(monkeypatch):
    _shorts_on(monkeypatch)
    # the IOC fills 100 of 300: nothing rests after it this tick; the next tick plans the remainder
    p, b, v = _flip_world(ioc_fill=100.0)
    st = _tick(p, v)
    assert [c[2:6] for c in _places(v)] == [(0.32, 300, True, IOC_TIF)] and b["ledger_net"] == -200
    assert not [o for o in p.orders.values() if o["state"] == "open"] and b["open_order_id"] is None
    assert _census(st, "short_cover_take") == 1 and _census(st, "short_cover_rest") == 0
    v2 = _NoClose(held={SLUG: -200}, ioc_fill=200.0)
    _tick(p, v2, now=NOW + 30)
    assert [c[2:6] for c in _places(v2)] == [(0.32, 200, True, IOC_TIF)] and b["ledger_net"] == 0
    # a standing cover rest with the ask inside the cent: ONE cancel, ONE IOC, nothing more
    p3, b3, v3 = _flip_world(ioc_fill=0.0)
    p3.add_order(b3, side=BUY, wire=0.31, qty=300, kind="flatten_paired", intent="ORDER_INTENT_SELL_SHORT")
    v3.rest("oid-1", "SELL", 0.31, 300, intent="ORDER_INTENT_SELL_SHORT")
    st3 = _tick(p3, v3)
    assert _cancels(v3) == [("cancel", "oid-1", SLUG)] and [c[5] for c in _places(v3)] == [IOC_TIF]
    assert sum(1 for c in _places(v3) if c[5] == IOC_TIF) == 1 and _census(st3, "short_cover_take") == 1
    assert not [o for o in p3.orders.values() if o["state"] == "open"]


def test_s4_the_cover_is_exempt_from_the_ops_budget_the_replace_budget_and_the_loss_stop(monkeypatch):
    _shorts_on(monkeypatch)
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 0)
    # the IOC at budget 0
    p, b, v = _flip_world(ioc_fill=300.0)
    st = _tick(p, v)
    assert [c[5] for c in _places(v)] == [IOC_TIF] and st["ops"] == 1 and _census(st, "ops_capped") == 0
    assert b["ledger_net"] == 0
    # the rest at budget 0 (the ask above the ceiling)
    p2, b2, v2 = _flip_world(bid=0.30, ask=0.40)
    st2 = _tick(p2, v2)
    assert [c[2:6] for c in _places(v2)] == [(0.31, 300, True, GTC_TIF)] and st2["ops"] == 1
    assert _census(st2, "ops_capped") == 0 and _census(st2, "short_cover_rest") == 1
    # the take off a standing rest at budget 0: the cancel AND the IOC go
    p3, b3, v3 = _flip_world(ioc_fill=300.0)
    p3.add_order(b3, side=BUY, wire=0.31, qty=300, kind="flatten_paired", intent="ORDER_INTENT_SELL_SHORT")
    v3.rest("oid-1", "SELL", 0.31, 300, intent="ORDER_INTENT_SELL_SHORT")
    st3 = _tick(p3, v3)
    assert _cancels(v3) == [("cancel", "oid-1", SLUG)] and [c[5] for c in _places(v3)] == [IOC_TIF]
    assert st3["ops"] == 2 and _census(st3, "ops_capped") == 0 and b3["ledger_net"] == 0
    # the replace budget spent and the loss stop set: the cover still goes
    monkeypatch.setattr(rules, "MIRROR_MAX_ORDER_OPS_PER_TICK", 20)
    p4, b4, v4 = _flip_world(ioc_fill=300.0)
    for _ in range(rules.MIRROR_MAX_REPLACES_PER_HOUR):
        p4.add_order(b4, state="cancelled", reason="replace", done_at=NOW - 100, order_id=None)
    p4.state["mirror_loss_stop"] = {"at": "x"}
    st4 = _tick(p4, v4)
    assert [c[5] for c in _places(v4)] == [IOC_TIF] and b4["ledger_net"] == 0
    assert _census(st4, "take_capped") == 0 and _census(st4, "replace_capped") == 0
    assert _census(st4, "mirror_loss_stop") >= 1 and not [x for x in p4.sent if "ml-replaces" in x[1]]


def test_s4_close_position_is_never_called_on_any_cover_path_and_the_lost_close_reader_stays_for_legacy_rows(monkeypatch):
    """Every cover world under the raising venue: the paired flatten, the
    confirmed vanish (priced and unpriced), the sign flip, the partial
    reduce, the held (out-of-tolerance) cover -- close_position is never
    reached. _reconcile_lost_close is still on the CLOSE rows of old."""
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
             _fill(N, "SELL", 400, 0.70, NOW - 2000), _fill(M, "SELL", 100, 0.31, NOW - 1000)]
    worlds = [
        (_short_world(fills=_his(400, other_size=400, other_px=0.72), snap={M: 400.0, N: 400.0}), _mkt(400.0, 400.0), 0.32, 0.32),
        (_short_world(fills=fills, snap=None), _gone(), 0.31, 0.31),
        (_short_world(fills=_unpriced(), snap=None), _gone(), 0.32, 0.34),
        (_pool(), None, 0.32, 0.32),
        (_short_world(fills=_his(300, other_size=400, other_px=0.72), snap={M: 300.0, N: 400.0}), _mkt(300.0, 400.0), 0.32, 0.32),
    ]
    for p, http, ask, px in worlds:
        b = _short_book(p, ledger=-300)
        v = _NoClose(bid=0.30, ask=ask, held={SLUG: -300}, ioc_fill=300.0)
        st = _tick(p, v, http=http)
        assert "close" not in _kinds(v) and _places(v) and _places(v)[0][2] == px, (px, _places(v))
        assert _census(st, "short_cover_take") == 1 and not st["abandoned"]
    # held outside the cent, and the unproven refusal: nothing touches close_position either
    p, b, v = _flip_world(bid=0.30, ask=0.40)
    _tick(p, v)
    p2, b2, v2 = _flip_world()
    _s4_unproven(p2)
    _tick(p2, v2)
    assert "close" not in _kinds(v) and "close" not in _kinds(v2)
    # the seams by name: _flatten_send refuses a short at its head, _act's short tail never closes
    fs = inspect.getsource(ml._flatten_send)
    assert 'return "book_error"' in fs.split("if r.venue_state is not None")[0] and "close_position" in fs
    act = inspect.getsource(ml._act)
    assert "pmus.close_position" not in act and "_s4_refusal(t, book, kind, plan, w)" in act
    assert "pmus.close_position" not in inspect.getsource(ml._flatten_vanished)
    assert "t.pmus.close_position" in fs
    assert "_reconcile_lost_close" in inspect.getsource(ml._reconcile_placing)


def test_s4_buy_limit_price_mirrors_sell_limit_price_by_the_flatten_slip():
    for px in (0.01, 0.02, 0.30, 0.50, 0.97, 0.98, 0.99):
        assert le.buy_limit_price(px) == min(0.99, round(px + rules.MIRROR_FLATTEN_SLIP, 2))
        assert le.sell_limit_price(px) == max(0.01, round(px - rules.MIRROR_FLATTEN_SLIP, 2))
    assert le.buy_limit_price(0.30, max_price=0.31) == 0.31 and le.buy_limit_price(0.30, max_price=0.50) == 0.32
    assert rules.MIRROR_FLATTEN_SLIP == 0.02 and "MIRROR_FLATTEN_SLIP" in rules.__all__
    for k in ("s4_probe_placed", "s4_proved", "s4_unproven", "short_cover_rest", "short_cover_take",
              "short_cover_out_of_tol"):
        assert k in ml.CENSUS_KEYS and ml.CENSUS_KEYS.index(k) < ml.CENSUS_KEYS.index("cand_terminal_skipped")
    assert ml._STATE_S4 == "mirror_s4_proof"


# ---------------- 22b. S4 v2: the adversarial review folded (2026-09-07)
#
# F1 the proof reads the VENUE'S OWN side (pmus._norm_order's
# `venue_side`, the SDK's Order.side verbatim), never the desk's derived
# `side`: a SELL_SHORT that buys the contract back must read
# ORDER_SIDE_BUY, anything else is `wrong_side` (none reported:
# `side_missing`). F2 the probe is placed in the LONG token's space, the
# book's own quote, and the record keeps that quote; a venue that quotes
# the short token's space rests the probe on a high-priced book with the
# right price and intent and is told apart by its side alone. F3 a 429
# on the probe's placement is E2's rate-limit path, no proof record. F4
# a lost cover placement is adopted by its WIRE INTENT (the fingerprint
# matches intent to intent when both name one), in _lost_response and in
# step O; a rest the search cannot find freezes `placement_lost`. The
# reviewer's four FINDING tests are verbatim below, except F2's two
# cover assertions: with the fold the proof refuses `wrong_side` on that
# venue, so no cover rests (the assertions are inverted and say so).

SELL_SHORT = "ORDER_INTENT_SELL_SHORT"


def _cover_world(fills, snap, http_rows=None, **venue_kw):
    """A proved short book of 300 with his fills as given."""
    p = _short_world(fills=fills, snap=snap)
    b = _short_book(p, ledger=-300)
    venue_kw.setdefault("held", {SLUG: -300})
    return p, b, _NoClose(**venue_kw)


def test_FINDING_the_read_back_never_reads_the_venues_own_side_so_a_sell_side_echo_still_proves(monkeypatch):
    """THE ONE FACT THAT SETTLES THE DENOMINATION IS THE VENUE'S OWN
    `side` (Order.side, derived by the venue from the intent: a
    BUY_SHORT reads ORDER_SIDE_SELL, short-truth 6/6). A SELL_SHORT that
    buys the contract back must read ORDER_SIDE_BUY. pmus._norm_order
    OVERWRITES that field from the intent string ('SELL' for any
    *_SELL_* intent), so the probe's echo `side` is the adapter's
    derivation, not the venue's, and _s4_probe compares only the
    intent. A venue that lists the probe as a SELL at 0.25 -- an order
    in another space -- is recorded PROVED."""
    _shorts_on(monkeypatch)
    # the adapter drops the venue's side: both venue sides normalise to 'SELL'
    for venue_side in ("ORDER_SIDE_BUY", "ORDER_SIDE_SELL"):
        row = pmus._norm_order({"id": "x", "marketSlug": SLUG, "intent": SELL_SHORT, "side": venue_side,
                                "price": {"value": "0.25"}, "quantity": 1, "state": "ORDER_STATE_NEW"})
        assert row["side"] == "SELL", "the adapter's derived side hides the venue's"

    class _SellSideEcho(_NoClose):
        """The venue lists the probe with intent SELL_SHORT and its OWN
        side SELL (the read-back as the worker would see it if
        _norm_order carried the venue's field)."""

        def rest(self, oid, side="BUY", price=0.30, qty=300, slug=SLUG, created=None, state="new",
                 filled=0.0, avg=None, intent=None):
            return super().rest(oid, "SELL", price, qty, slug, created, state, filled, avg, intent)

    p = _pool()
    b = _short_book(p, ledger=-300)
    p.state.pop("mirror_s4_proof")
    v = _SellSideEcho(held={SLUG: -300}, ioc_fill=300.0)
    st = _tick(p, v)
    rec = p.state["mirror_s4_proof"]
    assert rec["echo"]["side"] == "SELL" and rec["echo"]["intent"] == SELL_SHORT
    # the spec's proof: price, quantity, the SIDE and the intent as the venue reports them
    assert rec["proved"] is False and rec["why"] == "wrong_side", rec
    assert _census(st, "s4_unproven") == 1 and b["ledger_net"] == -300


def test_FINDING_under_a_short_token_space_venue_the_probe_rests_and_proves_on_a_high_priced_book_and_the_cover_can_never_fill(monkeypatch):
    """The consequence of the side-blind proof. Model the OTHER reading
    of a SELL_SHORT limit -- 'sell the short token at >= p', i.e. buy the
    long at <= 1 - p (post-only honoured). On a book at 0.30/0.32 the
    probe at 0.25 crosses (0.25 <= 1 - 0.32) and is refused: fail closed.
    On a book at 0.70/0.72 the probe at 0.65 does NOT cross (0.65 > 0.28),
    rests, echoes its price and intent, and is PROVED -- and every cover
    after it rests at floor(his) 0.70 = 'buy the long at <= 0.30' against
    an ask of 0.72: it never fills, the book is held for good with
    `short_cover_rest` counted and nothing wrong named. The proof must
    refuse this venue; only the venue's own side tells it apart.

    WITH THE FOLD (S4 v2): the venue's own side is read, the high book's
    probe is refused `wrong_side`, and NO cover rests after it -- the
    reviewer's two cover assertions are inverted here, everything else
    verbatim."""
    _shorts_on(monkeypatch)

    class _ShortSpace(_NoClose):
        def submit_fok(self, slug, price, qty, sell=False, tif=GTC_TIF, intent=None, post_only=False,
                       good_till=None, paced_pair=False):
            self.calls.append(("place", slug, price, qty, sell, tif, intent, post_only, good_till))
            self.n += 1
            oid = f"oid-{self.n}"
            if sell and intent == SHORT:
                buy_long_at_most = round(1.0 - price, 2)
                if buy_long_at_most >= self.ask:           # marketable
                    if post_only:
                        return {"ok": False, "order_id": None, "status": "post_only_rejected",
                                "fill_price": None, "filled_shares": 0.0,
                                "raw": {"status_code": 400, "error": "would cross"}}
                    if tif == IOC_TIF:
                        return {"ok": True, "order_id": oid, "status": "filled", "fill_price": self.ask,
                                "filled_shares": float(qty), "raw": {}}
                self.rest(oid, "SELL", price, qty, slug, intent=SELL_SHORT)
                if tif == IOC_TIF:
                    self.orders[oid]["state"] = "cancelled"
                    return {"ok": False, "order_id": oid, "status": "canceled", "fill_price": None,
                            "filled_shares": 0.0, "raw": {}}
                return {"ok": False, "order_id": oid, "status": "new", "fill_price": None,
                        "filled_shares": 0.0, "raw": {"response": {"id": oid}}}
            return super().submit_fok(slug, price, qty, sell, tif, intent, post_only, good_till, paced_pair)

    # (a) the low book: the probe crosses under this reading and is refused -- fail closed
    p = _pool()
    b = _short_book(p, ledger=-300)
    p.state.pop("mirror_s4_proof")
    v = _ShortSpace(bid=0.30, ask=0.32, held={SLUG: -300})
    st = _tick(p, v)
    assert p.state["mirror_s4_proof"]["proved"] is False and _census(st, "s4_unproven") == 1
    assert p.state["mirror_s4_proof"]["why"] == "place_refused:post_only_rejected" and b["ledger_net"] == -300
    # (b) the high book: his BUY of the long at 0.70 (the cover level), the market 0.70/0.72
    fills = [_fill(N, "BUY", 400, 0.35, NOW - 2500), _fill(M, "BUY", 400, 0.70, NOW - 2000)]
    p2 = _short_world(fills=fills, snap=None)
    b2 = _short_book(p2, ledger=-300, avg=0.68)
    p2.state.pop("mirror_s4_proof")
    v2 = _ShortSpace(bid=0.70, ask=0.72, held={SLUG: -300})
    st2 = _tick(p2, v2, http=_gone())
    rec = p2.state["mirror_s4_proof"]
    assert [c[2:4] for c in _places(v2)][0] == (0.65, 1)
    # the probe rested and echoed price 0.65 / intent SELL_SHORT: proved by S4's check ...
    assert rec["echo"]["price"] == 0.65 and rec["echo"]["intent"] == SELL_SHORT
    # ... and with the fold NO cover rests after it: the proof refused this venue
    assert [c[2:6] for c in _places(v2)][1:] == []
    assert _census(st2, "short_cover_rest") == 0 and b2["ledger_net"] == -300
    # the spec: this venue must NOT be proved (its side is not a contract BUY)
    assert rec["proved"] is False, rec


def test_FINDING_a_429_on_the_probe_is_recorded_as_a_mismatch_not_routed_to_the_rate_limit_circuit(monkeypatch):
    """E2's rule: a venue 429 is a refusal before anything was processed
    -- `rate_limited`, the pacer's circuit, the tick abandoned; never a
    verdict about the order. The probe's create under post_only=True
    hands a 429 back as the post_only_rejected shape (status_code 429,
    error_type RateLimitError); _s4_probe reads `not oid` and records
    `place_refused:post_only_rejected` -- the cover refused for an HOUR
    on a transient 429, no circuit, no abandon, and the tick goes on
    placing into the limited venue."""
    _shorts_on(monkeypatch)

    def _limited(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                "filled_shares": 0.0,
                "raw": {"preview": {}, "status_code": 429, "error": "RateLimitError: Too Many Requests",
                        "error_type": "RateLimitError", "body": None}}
    p, b, v = _cover_world(_his(), snap={M: 300.0, N: 0.0}, place=_limited, ioc_fill=300.0)
    p.state.pop("mirror_s4_proof")
    st = _tick(p, v)
    rec = p.state.get("mirror_s4_proof") or {}
    assert ml._raw_rate_limit({"status_code": 429}) is True, "the reader exists; the probe does not call it"
    assert _census(st, "rate_limited") >= 1 and st["abandoned"] and st.get("abandon_reason") == "rate_limited", \
        (rec, {k: v_ for k, v_ in st["census"].items() if v_})
    # a 429 is not a proof verdict that should hold the cover for an hour
    assert rec.get("why") not in ("place_refused:post_only_rejected",), rec


def test_FINDING_a_cover_placement_whose_response_is_lost_is_never_adopted_the_rest_stands_unmanaged_on_the_venue(monkeypatch):
    """The lost-close history in a new shape. The cover's create raises
    after the venue rested it (the 30 s SDK timeout class that produced
    tonight's freezes). _lost_response searches the book by fingerprint:
    _on_book_matches compares the venue's normalized side ('SELL', which
    pmus._norm_order derives from the SELL_SHORT intent) against
    _order_side_of(row) = 'BUY' (the plan side) -- never equal, so the
    resting 300-share cover is not adopted, the book is frozen
    `placement_lost`, and the rest stands on the venue with no row: step
    O re-searches by the same fingerprint, then marks the row lost at
    1200 s while the order still rests; when it fills the book lands in
    `venue_ledger_disagree` for good. A short ADD lost the same way IS
    adopted (its row side SELL matches the venue's SELL)."""
    _shorts_on(monkeypatch)
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
             _fill(N, "SELL", 400, 0.70, NOW - 2000)]
    p, b, v = _cover_world(fills, snap=None, bid=0.29, ask=0.40, place_raises=RuntimeError("read timeout"),
                           rest_on_raise=True)
    st = _tick(p, v, http=_gone())
    assert [c[2:6] for c in _places(v)] == [(0.30, 300, True, GTC_TIF)]
    assert v.orders["oid-1"]["state"] == "new" and v.orders["oid-1"]["intent"] == SELL_SHORT
    o = next(iter(p.orders.values()))
    assert ("open_orders", [SLUG]) in v.calls, "the lost-response search ran"
    # the spec: a rest the venue lists at our cent, our quantity, our intent is OURS -- adopted
    assert o["state"] == "open" and o["order_id"] == "oid-1" and b["state"] == "live", (o["state"], b["frozen_reason"])
    assert _census(st, "placement_lost") == 0


def test_s4v2_the_adapter_carries_the_venues_own_side_verbatim_and_the_proof_reads_it_alone(monkeypatch):
    """F1 pinned. pmus._norm_order keeps the desk's derived `side` and
    adds `venue_side`, the SDK's Order.side as it came (None when the
    venue reported none). The proof compares `venue_side` to
    S4_VENUE_BUY: a SELL there is `wrong_side`, none is `side_missing`
    -- both cancelled, both recorded with the echo naming both sides,
    both refusing the cover by name and holding the next tick. The
    fixture venue's model of the venue's side is the CONTRACT side by
    wire intent: BUY_SHORT and SELL_LONG rest ORDER_SIDE_SELL, BUY_LONG
    and the cover's SELL_SHORT rest ORDER_SIDE_BUY."""
    _shorts_on(monkeypatch)
    assert ml.S4_VENUE_BUY == "ORDER_SIDE_BUY"
    raw = {"id": "x", "marketSlug": SLUG, "intent": SELL_SHORT, "price": {"value": "0.25"},
           "quantity": 1, "state": "ORDER_STATE_NEW"}
    assert pmus._norm_order({**raw, "side": "ORDER_SIDE_BUY"})["venue_side"] == "ORDER_SIDE_BUY"
    assert pmus._norm_order({**raw, "side": "ORDER_SIDE_SELL"})["venue_side"] == "ORDER_SIDE_SELL"
    assert pmus._norm_order(raw)["venue_side"] is None and pmus._norm_order(raw)["side"] == "SELL"
    # the fixture's side model, by wire intent
    v = _Venue()
    v.submit_fok(SLUG, 0.30, 10, False, GTC_TIF, INTENT, True, None)                 # BUY_LONG
    v.submit_fok(SLUG, 0.30, 10, True, GTC_TIF, INTENT, True, None)                  # SELL_LONG
    v.submit_fok(SLUG, 0.30, 10, False, GTC_TIF, SHORT, True, None)                  # BUY_SHORT
    v.submit_fok(SLUG, 0.30, 10, True, GTC_TIF, SHORT, True, None)                   # SELL_SHORT
    assert [(o["intent"], o["side"], o["venue_side"]) for o in v.orders.values()] == [
        (INTENT, "BUY", "ORDER_SIDE_BUY"), ("ORDER_INTENT_SELL_LONG", "SELL", "ORDER_SIDE_SELL"),
        (SHORT, "SELL", "ORDER_SIDE_SELL"), (SELL_SHORT, "BUY", "ORDER_SIDE_BUY")]

    class _NoSide(_NoClose):
        def rest(self, oid, side="BUY", price=0.30, qty=300, slug=SLUG, created=None, state="new",
                 filled=0.0, avg=None, intent=None):
            o = super().rest(oid, side, price, qty, slug, created, state, filled, avg, intent)
            o["venue_side"] = None
            return o

    class _SellSide(_NoClose):
        def rest(self, oid, side="BUY", price=0.30, qty=300, slug=SLUG, created=None, state="new",
                 filled=0.0, avg=None, intent=None):
            return super().rest(oid, "SELL", price, qty, slug, created, state, filled, avg, intent)

    for mk, why, vs in ((_NoSide, "side_missing", None), (_SellSide, "wrong_side", "ORDER_SIDE_SELL")):
        p = _pool()
        b = _short_book(p, ledger=-300)
        p.state.pop("mirror_s4_proof")
        v = mk(held={SLUG: -300}, ioc_fill=300.0)
        st = _tick(p, v)
        rec = p.state["mirror_s4_proof"]
        assert rec["proved"] is False and rec["why"] == why and rec["at"] == NOW, (why, rec)
        assert rec["echo"]["venue_side"] == vs and rec["echo"]["intent"] == SELL_SHORT, why
        assert rec["echo"]["price"] == 0.25 and rec["echo"]["quantity"] == 1.0, why
        assert rec["cancel"] == {"ok": True} and v.orders["oid-1"]["state"] == "cancelled", why
        assert _census(st, "s4_probe_placed") == 1 and _census(st, "s4_proved") == 0, why
        assert _census(st, "s4_unproven") == 1 and b["last_plan"]["s4"] == {"unproven": why}, why
        assert [c[3] for c in _places(v)] == [1] and b["ledger_net"] == -300 and b["state"] == "live", why
        assert not p.orders, "no mirror_orders row for the probe"
        # the recorded mismatch holds the next tick, no second probe
        v2 = _NoClose(held={SLUG: -300}, ioc_fill=300.0)
        st2 = _tick(p, v2, now=NOW + 30)
        assert not _places(v2) and _census(st2, "s4_unproven") == 1 and _census(st2, "s4_probe_placed") == 0, why
    # the side is checked before the intent: a SELL-side echo with the
    # right intent is `wrong_side`, never proved by the intent alone
    assert inspect.getsource(ml._s4_probe).index('"wrong_side"') < inspect.getsource(ml._s4_probe).index('"wrong_intent"')


def test_s4v2_the_probe_is_placed_in_the_long_tokens_space_against_the_covers_own_quote(monkeypatch):
    """F2 pinned. The probe's price is _s4_probe_price of the BID the
    book's own _bbo read returned -- the long token's space, the space
    his_px and the cover's rest / take cents live in -- and the record
    keeps that quote (`bid`, `ask`) beside the price it sent; the
    read-back price is held to what was sent in that space. A quote
    the book cannot read two-sided in that space (one side missing,
    or locked) is not probed. Under the reviewer's short-token-space
    venue the high-priced book's probe rests at our price with our
    intent and is refused by its side alone, `wrong_side`; the cover
    is refused `s4_unproven` and the book held, never a rest that can
    never fill."""
    _shorts_on(monkeypatch)
    p, b, v = _flip_world(bid=0.30, ask=0.34, ioc_fill=300.0)
    p.state.pop("mirror_s4_proof")
    st = _tick(p, v)
    rec = p.state["mirror_s4_proof"]
    assert rec["proved"] is True and rec["bid"] == 0.30 and rec["ask"] == 0.34
    assert rec["price"] == ml._s4_probe_price(0.30) == 0.25 == rec["echo"]["price"]
    # the cover's own cents on the same tick, in the same space (his 0.31: ceiling 0.32)
    assert b["last_plan"]["exit_cover"] == 0.32 and _census(st, "s4_proved") == 1
    # one side of the quote missing: no probe, the cover refused by name
    for bid, ask in ((0.30, None), (None, 0.32)):
        p2, b2, v2 = _flip_world(bid=bid, ask=ask, ioc_fill=300.0)
        p2.state.pop("mirror_s4_proof")
        st2 = _tick(p2, v2)
        assert not [c for c in _places(v2) if c[3] == 1] and _census(st2, "s4_probe_placed") == 0, (bid, ask)
        assert "mirror_s4_proof" not in p2.state and b2["ledger_net"] == -300, (bid, ask)


def test_s4v2_a_429_on_the_probe_in_either_shape_goes_to_the_rate_limit_path_and_writes_the_hours_hold(monkeypatch):
    """F3 pinned in both shapes: the create's 429 in the
    post_only_rejected raw (status_code 429), and the SDK's
    RateLimitError raised by the placement. Each: `rate_limited` and
    the circuit, `s4_probe_placed` (the request went out), the tick
    abandoned `rate_limited` with the backoff skipped, the tick's
    `recent` naming it, NO cover this tick -- and (S4 v3, GAP 3) the
    record {"proved": false, "why": "rate_limited", "at": now} written
    over the prior one, so the hour's clock holds the probe: the next
    tick probes nothing (no abandon), an hour on it probes again and
    proves. v2 wrote no record and the probe was the first write of
    every tick while the venue limited creates, abandoning each. A
    post_only_rejected raw whose text merely mentions '429' under an
    int status 400 is a crossing refusal and is recorded as the
    mismatch it is (the anchored rule), the tick not abandoned."""
    _shorts_on(monkeypatch)

    def _limited(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                "filled_shares": 0.0,
                "raw": {"preview": {}, "status_code": 429, "error": "RateLimitError: Too Many Requests",
                        "error_type": "RateLimitError", "body": None}}
    prior = {"proved": False, "at": NOW - 7200.0, "slug": SLUG, "why": "wrong_price", "echo": {"price": 0.24}}
    for label, kw in (("the raw shape", {"place": _limited}), ("the raised shape", {"place_raises": _sdk_429()})):
        p, b, v = _cover_world(_his(), snap={M: 300.0, N: 0.0}, ioc_fill=300.0, **kw)
        p.state["mirror_s4_proof"] = dict(prior)
        st = _tick(p, v)
        rec = p.state["mirror_s4_proof"]
        assert rec["proved"] is False and rec["why"] == "rate_limited" and rec["at"] == NOW, (label, rec)
        assert rec["order_id"] is None and rec["echo"]["raised"] == "RateLimitError", (label, rec)
        assert rec["slug"] == SLUG and rec["price"] == 0.25 and rec["bid"] == 0.30 and rec["ask"] == 0.32, label
        assert "cancel" not in rec and "booked" not in rec, label
        assert _census(st, "rate_limited") == 1 and _census(st, "s4_probe_placed") == 1, label
        assert _census(st, "s4_proved") == 0 and _census(st, "s4_unproven") == 1, label
        assert st["abandoned"] and st["abandon_reason"] == "rate_limited", label
        assert _census(st, "backoff_skipped_circuit") == 1, label
        assert [c[3] for c in _places(v)] == [1] and b["ledger_net"] == -300 and b["state"] == "live", label
        assert not [c for c in v.calls if c[0] == "cancel"] and not p.orders, label
        pr = [r for r in st["recent"] if r["what"] == "s4_probe"]
        assert pr and pr[-1]["why"] == "rate_limited" and pr[-1]["raised"] == "RateLimitError", (label, pr)
        assert b["last_plan"]["s4"] == {"unproven": "rate_limited"}, label
        venue_pace._penalty_until = 0.0
        # the next tick, the venue answering: NO probe (the hour's hold), no
        # abandon, the cover still refused by the 429's name
        v2 = _NoClose(held={SLUG: -300}, ioc_fill=300.0)
        st2 = _tick(p, v2, now=NOW + 1)
        assert not _places(v2) and not st2["abandoned"] and _census(st2, "s4_probe_placed") == 0, label
        assert _census(st2, "s4_unproven") == 1 and b["last_plan"]["s4"] == {"unproven": "rate_limited"}, label
        assert p.state["mirror_s4_proof"] == rec and b["ledger_net"] == -300, label
        # an hour on: the probe again, proved, the cover after it
        v3 = _NoClose(held={SLUG: -300}, ioc_fill=300.0)
        st3 = _tick(p, v3, now=NOW + 3600)
        assert [c[3] for c in _places(v3)] == [1, 300] and _census(st3, "s4_proved") == 1, label
        assert p.state["mirror_s4_proof"]["proved"] is True and b["ledger_net"] == 0, label
    # a 400 whose text mentions 429 is a crossing refusal: the mismatch recorded, no abandon
    def _crossed(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                "filled_shares": 0.0, "raw": {"status_code": 400, "error": "would cross at 0.429"}}
    p3, b3, v3 = _cover_world(_his(), snap={M: 300.0, N: 0.0}, place=_crossed, ioc_fill=300.0)
    p3.state.pop("mirror_s4_proof")
    st3 = _tick(p3, v3)
    assert p3.state["mirror_s4_proof"]["why"] == "place_refused:post_only_rejected"
    assert _census(st3, "rate_limited") == 0 and not st3["abandoned"] and _census(st3, "s4_unproven") == 1
    # the rate-limit reader is the placement's (never '429' in free text)
    src = inspect.getsource(ml._s4_probe)
    assert "_raw_rate_limit(raw)" in src and "ms.is_rate_limit(exc)" in src and "_s4_probe_rate_limited" in src
    assert "_abandon(t, \"rate_limited\", rate_limited=True)" in inspect.getsource(ml._s4_probe_rate_limited)


def test_s4v2_a_lost_cover_is_adopted_by_its_wire_intent_in_step_o_and_an_unfound_cover_rest_freezes_placement_lost(monkeypatch):
    """F4 pinned. The fingerprint matches INTENT to intent when both
    the row and the venue row name one (the production shape:
    _norm_order's derived side is 'SELL' for a SELL_SHORT, the row's
    plan side BUY), else side to side as before. Step O: a 'placing'
    cover row past PLACING_ORPHAN_S with the venue listing our cent /
    quantity / intent is adopted; a SELL_LONG at our cent and quantity
    is not ours (frozen `placement_lost`, the row left 'placing'). A
    lost cover response with NOTHING resting, or a rest at another
    cent, freezes `placement_lost` -- never a live book with a cover
    standing unmanaged."""
    _shorts_on(monkeypatch)
    cover = {"side": BUY, "wire": 0.30, "qty": 300, "intent": SELL_SHORT}
    # the unit: the production shape of a resting cover, and the mismatches
    listed = {"order_id": "v-1", "side": "SELL", "intent": SELL_SHORT, "price": 0.30, "quantity": 300.0}
    assert ml._on_book_matches(cover, listed, 0.30, 300) is True
    assert ml._on_book_matches(cover, {**listed, "intent": "ORDER_INTENT_SELL_LONG"}, 0.30, 300) is False
    assert ml._on_book_matches(cover, {**listed, "intent": "ORDER_INTENT_BUY_LONG", "side": "BUY"}, 0.30, 300) is False
    assert ml._on_book_matches(cover, {**listed, "price": 0.31}, 0.30, 300) is False
    assert ml._on_book_matches(cover, {**listed, "quantity": 299.0}, 0.30, 300) is False
    assert ml._on_book_matches(cover, {**listed, "order_id": None}, 0.30, 300) is False
    # a short ADD's production shape (derived side 'BUY' against the row's SELL): by intent
    add = {"side": SELL, "wire": 0.32, "qty": 300, "intent": SHORT}
    assert ml._on_book_matches(add, {"order_id": "v-2", "side": "BUY", "intent": SHORT, "price": 0.32,
                                     "quantity": 300.0}, 0.32, 300) is True
    # no intent on either: the side comparison as before
    legacy = {"side": BUY, "wire": 0.30, "qty": 300, "intent": None}
    assert ml._on_book_matches(legacy, {**listed, "intent": None, "side": "BUY"}, 0.30, 300) is True
    assert ml._on_book_matches(legacy, {**listed, "intent": None, "side": "SELL"}, 0.30, 300) is False
    assert ml._on_book_matches(cover, {**listed, "intent": None, "side": "BUY"}, 0.30, 300) is True
    # step O: the 'placing' cover row older than a minute (the process died
    # between the create and the persist), the venue listing our order
    fills = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
             _fill(N, "SELL", 400, 0.70, NOW - 2000)]
    p, b, v = _cover_world(fills, snap=None, bid=0.29, ask=0.40)
    o = p.add_order(b, side=BUY, wire=0.30, qty=300, kind="flatten_paired", state="placing", order_id=None,
                    placed_ts=NOW - 90, intent=SELL_SHORT)
    v.rest("venue-7", "BUY", 0.30, 300, created=NOW - 85, intent=SELL_SHORT)
    st = _tick(p, v, http=_gone())
    assert o["order_id"] == "venue-7" and o["state"] == "open" and ("status", "venue-7") in v.calls
    assert not _places(v) and _census(st, "order_lost") == 0 and b["open_order_id"] == o["id"]
    assert [r for r in st["recent"] if r["what"] == "adopted"][0]["order"] == "venue-7"
    assert b["state"] == "live" and _census(st, "short_cover_out_of_tol") == 1, "the adopted rest is managed"
    # step O on the book _lost_response froze: adopted by fingerprint, the
    # rest cancelled under the freeze's name, the book thawed and the cover
    # rested again the same tick -- the frozen road, never a rest unmanaged
    pf, bf, vf = _cover_world(fills, snap=None, bid=0.29, ask=0.40)
    of = pf.add_order(bf, side=BUY, wire=0.30, qty=300, kind="flatten_paired", state="placing", order_id=None,
                      placed_ts=NOW - 90, intent=SELL_SHORT)
    bf.update(state="frozen", frozen_reason="placement_lost", frozen_ts=NOW - 90)
    vf.rest("venue-7", "BUY", 0.30, 300, created=NOW - 85, intent=SELL_SHORT)
    stf = _tick(pf, vf, http=_gone())
    assert of["order_id"] == "venue-7" and of["state"] == "cancelled" and of["reason"] == "placement_lost"
    assert [r["what"] for r in stf["recent"]][-5:] == ["adopted", "cancel", "order_cancelled", "thawed", "placed"]
    assert ("cancel", "venue-7", SLUG) in vf.calls and vf.orders["venue-7"]["state"] == "cancelled"
    assert bf["state"] == "live" and [c[2:6] for c in _places(vf)] == [(0.30, 300, True, GTC_TIF)]
    assert _census(stf, "short_cover_rest") == 1 and _census(stf, "order_lost") == 0
    # step O: a SELL_LONG resting at our cent and quantity is not ours -- frozen, the row left
    p2, b2, v2 = _cover_world(fills, snap=None, bid=0.29, ask=0.40)
    o2 = p2.add_order(b2, side=BUY, wire=0.30, qty=300, kind="flatten_paired", state="placing", order_id=None,
                      placed_ts=NOW - 90, intent=SELL_SHORT)
    v2.rest("venue-8", "SELL", 0.30, 300, created=NOW - 85, intent="ORDER_INTENT_SELL_LONG")
    st2 = _tick(p2, v2, http=_gone())
    assert o2["order_id"] is None and o2["state"] == "placing" and not _places(v2)
    assert b2["state"] == "frozen" and b2["frozen_reason"] == "placement_lost" and _census(st2, "placement_lost") >= 1
    # the lost response with nothing resting: frozen by name, never live
    p3, b3, v3 = _cover_world(fills, snap=None, bid=0.29, ask=0.40, place_raises=RuntimeError("read timeout"))
    st3 = _tick(p3, v3, http=_gone())
    o3 = next(iter(p3.orders.values()))
    assert [c[2:6] for c in _places(v3)] == [(0.30, 300, True, GTC_TIF)] and ("open_orders", [SLUG]) in v3.calls
    assert o3["state"] == "placing" and o3["order_id"] is None and (o3["side"], o3["intent"]) == (BUY, SELL_SHORT)
    assert b3["state"] == "frozen" and b3["frozen_reason"] == "placement_lost" and _census(st3, "placement_lost") >= 1
    assert b3["open_order_id"] == o3["id"] and "close" not in _kinds(v3)

    # the lost response with the venue's rest at ANOTHER cent: not ours, frozen
    class _OffByOne(_NoClose):
        def rest(self, oid, side="BUY", price=0.30, qty=300, slug=SLUG, created=None, state="new",
                 filled=0.0, avg=None, intent=None):
            return super().rest(oid, side, round(price + 0.01, 2), qty, slug, created, state, filled, avg, intent)
    p4 = _short_world(fills=fills, snap=None)
    b4 = _short_book(p4, ledger=-300)
    v4 = _OffByOne(bid=0.29, ask=0.40, held={SLUG: -300}, place_raises=RuntimeError("read timeout"),
                   rest_on_raise=True)
    st4 = _tick(p4, v4, http=_gone())
    o4 = next(iter(p4.orders.values()))
    assert v4.orders["oid-1"]["price"] == 0.31 and o4["state"] == "placing" and o4["order_id"] is None
    assert b4["state"] == "frozen" and b4["frozen_reason"] == "placement_lost" and _census(st4, "placement_lost") >= 1
    # the next tick: the frozen book with its non-terminal row is held, no second cover
    v5 = _NoClose(bid=0.29, ask=0.40, held={SLUG: -300})
    v5.orders = v4.orders
    st5 = _tick(p4, v5, now=NOW + 10, http=_gone())
    assert not _places(v5) and _census(st5, "open_order_pending") >= 1 and b4["state"] == "frozen"


# ------------------------------------------------ 12. the census coverage

def test_a_terminal_candidate_is_memoised_and_skipped_on_the_next_tick(monkeypatch):
    """D1: a candidate whose quote read said the market had ended
    (ms.STATE_TERMINAL) is remembered for ms.UNMAPPED_TTL_S the way an
    unmapped one is, and skipped under `cand_terminal_skipped` -- the
    walk's 20 slots were going to his settled matches. The 25-ahead-of-1
    walk, the halt/book exclusions and the TTL are pinned in
    tests/test_d1_fills_dedup.py."""
    monkeypatch.setattr(ml, "_terminal_until", {})
    p = _pool()
    v = _Venue(state="MARKET_STATE_EXPIRED")
    st = _tick(p, v)
    assert _census(st, "venue_halted") == 1 and not st["abandoned"] and not p.books
    assert ml._terminal_until == {("rn1", CID): NOW + ms.UNMAPPED_TTL_S}
    v.calls.clear()
    st2 = _tick(p, v, now=NOW + 1)
    assert _census(st2, "cand_terminal_skipped") == 1 and st2["reads"] == 0
    assert not [c for c in v.calls if c[0] == "bbo"], "no slot spent on it"


# ---------------- 22c. S4 v3: the review's round 2 folded (2026-09-07)
#
# GAP 1: a probe that FILLS at create (the post-only latch tripped) is
# BOOKED -- an `s4_probe` row, the ledger through _book_delta as a cover
# fill, `s4_probe_filled` -- and the cover after it is sized off the
# ledger the row moved. GAP 2: a probe the venue KEPT (a refused cancel)
# holds its id on the record; the cancel is retried every tick before
# the walk and nothing is probed until the venue reports it gone; a 429
# on the cancel is E2's path, the id kept. GAP 3: a 429 on the placement
# writes the hour's hold. INFO 5: the echo's state must be a standing one.


class _ProbeFills(_NoClose):
    """The 23:16Z shape: the venue fills the 1-share post-only probe at
    create, at the ask."""

    def submit_fok(self, slug, price, qty, sell=False, tif=GTC_TIF, intent=None, post_only=False,
                   good_till=None, paced_pair=False):
        if not (sell and qty == 1):
            return super().submit_fok(slug, price, qty, sell, tif, intent, post_only, good_till, paced_pair)
        self.calls.append(("place", slug, price, qty, sell, tif, intent, post_only, good_till))
        self.n += 1
        oid = f"oid-{self.n}"
        self.rest(oid, "BUY", price, qty, slug, state="filled", filled=1.0, avg=self.ask, intent=SELL_SHORT)
        return {"ok": True, "order_id": oid, "status": "filled", "fill_price": self.ask,
                "filled_shares": 1.0, "raw": {"response": {"id": oid}}}


class _ProbeState(_NoClose):
    """The probe rests in the state given and the venue LISTS it
    whatever the state (the read-back sees it)."""

    def __init__(self, *a, probe_state="new", **kw):
        super().__init__(*a, **kw)
        self.probe_state = probe_state

    def rest(self, oid, side="BUY", price=0.30, qty=300, slug=SLUG, created=None, state="new",
             filled=0.0, avg=None, intent=None):
        return super().rest(oid, side, price, qty, slug, created,
                            self.probe_state if qty == 1 else state, filled, avg, intent)

    def open_orders(self, slugs=None):
        self.calls.append(("open_orders", slugs))
        return [self._norm(o) for o in self.orders.values()]


class _Cancel429(_NoClose):
    """The venue rate-limits the cancel (the adapter never raises: its
    error string leads with the SDK's name)."""

    def cancel_order(self, oid, slug):
        self.calls.append(("cancel", oid, slug))
        return {"ok": False, "error": "RateLimitError: Too Many Requests"}


def _probe_row(p):
    rows = [o for o in p.orders.values() if o["kind"] == "s4_probe"]
    assert len(rows) <= 1
    return rows[0] if rows else None


def test_s4v3_a_probe_that_fills_at_create_is_booked_on_an_s4_probe_row_through_the_ledger_and_the_cover_is_sized_off_it(monkeypatch):
    """GAP 1. The probe fills at create (1 share at the ask 0.32 on avg
    0.32): the record says `filled_at_create` (the cover gated,
    `s4_unproven`, no cancel), and the share is BOOKED -- an ordinary
    mirror_orders row of kind `s4_probe` (side BUY, intent SELL_SHORT,
    qty 1, GTC post-only as sent, wire 0.25, the fill at 0.32, maker
    False, taker at placement, filled) through _book_delta: the ledger
    -300 -> -299, realized (0.32 - 0.32) x 1 = 0, the standing row 299,
    counted `s4_probe_filled`, the record naming the row under
    `booked`. An hour on the venue reads -299 (no disagreement), the
    probe proves and the cover is 299 -- _cover_qty off the ledger the
    row moved -- to ledger 0."""
    _shorts_on(monkeypatch)
    p, b, v = _flip_world(ioc_fill=300.0)
    p.state.pop("mirror_s4_proof")
    v.__class__ = _ProbeFills
    st = _tick(p, v)
    rec = p.state["mirror_s4_proof"]
    assert rec["proved"] is False and rec["why"] == "filled_at_create" and "cancel" not in rec
    assert rec["echo"] == {"filled_shares": 1.0, "fill_price": 0.32, "status": "filled"}
    assert rec["order_id"] == "oid-1" and rec["price"] == 0.25
    o = _probe_row(p)
    assert o is not None and rec["booked"] == {"row": o["id"], "booked": "booked", "ledger": -299}
    assert (o["side"], o["intent"], o["qty"], o["tif"], o["post_only"]) == (BUY, SELL_SHORT, 1, "GTC", True)
    assert (o["wire"], o["price"], o["avg_px"], o["order_id"], o["state"]) == (0.25, 0.25, 0.32, "oid-1", "filled")
    assert o["maker"] is False and o["taker_at_placement"] is True and o["booked_filled"] == 1.0
    assert o["realized"] == pytest.approx(0.0) and o["reason"] == "s4_probe" and o["done_at"] is not None
    assert b["ledger_net"] == -299 and b["avg_cost"] == 0.32 and b["realized_pnl"] == pytest.approx(0.0)
    assert p.rows[b["standing_row_id"]]["filled_shares"] == 299.0
    assert b["state"] == "live" and b["open_order_id"] is None and p._nonterminal(b["id"]) == []
    assert _census(st, "s4_probe_filled") == 1 and _census(st, "s4_unproven") == 1 and _census(st, "s4_proved") == 0
    assert _census(st, "s4_probe_placed") == 1 and _census(st, "short_flatten_close") == 0
    assert not _cancels(v) and [c[3] for c in _places(v)] == [1], "no cover this tick"
    assert b["last_plan"]["s4"] == {"unproven": "filled_at_create"}
    fr = [r for r in st["recent"] if r["what"] == "s4_probe_filled"]
    assert fr and fr[0]["order"] == "oid-1" and fr[0]["row"] == o["id"] and fr[0]["ledger"] == -299
    # inside the hour: held, nothing placed, the ledger where the row left it
    v2 = _NoClose(held={SLUG: -299}, ioc_fill=300.0)
    st2 = _tick(p, v2, now=NOW + 30)
    assert not _places(v2) and b["ledger_net"] == -299 and b["state"] == "live"
    assert _census(st2, "venue_ledger_disagree") == 0 and _census(st2, "s4_unproven") == 1
    # an hour on: the probe proves and the cover is the LEDGER'S 299, never 300
    v3 = _NoClose(held={SLUG: -299}, ioc_fill=300.0)
    st3 = _tick(p, v3, now=NOW + 3600)
    assert [c[2:6] for c in _places(v3)] == [(0.25, 1, True, GTC_TIF), (0.32, 299, True, IOC_TIF)]
    assert p.state["mirror_s4_proof"]["proved"] is True and b["ledger_net"] == 0
    assert _census(st3, "short_flatten_close") == 1 and _census(st3, "s4_probe_filled") == 0
    # the quantity rule, on the book the row moved
    assert ml._cover_qty({"intent": SHORT, "ledger_net": -299, "_held": 299.0}, 300) == 299


def test_s4v3_a_filled_probe_whose_row_cannot_be_written_freezes_the_book_by_name(monkeypatch):
    """GAP 1, the failure road: the INSERT raises -- the share is on the
    venue and not on the ledger, inside the one-share tolerance the
    venue/ledger read would never name -- so the book is frozen
    `s4_probe_unbooked` with the order and the error, counted
    `s4_probe_filled`, the record's `booked` naming the failure, the
    ledger untouched. A probe is never placed on a book with an order
    standing (the row needs the book's open slot)."""
    _shorts_on(monkeypatch)
    p, b, v = _flip_world(ioc_fill=300.0)
    p.state.pop("mirror_s4_proof")
    v.__class__ = _ProbeFills
    p.raise_on.append(("ml-order-insert", RuntimeError("insert down")))
    st = _tick(p, v)
    rec = p.state["mirror_s4_proof"]
    assert rec["why"] == "filled_at_create" and rec["booked"] == {"row": None, "error": "RuntimeError"}
    assert b["state"] == "frozen" and b["frozen_reason"] == "s4_probe_unbooked" and b["ledger_net"] == -300
    assert _probe_row(p) is None and _census(st, "s4_probe_filled") == 1
    assert _census(st, "s4_probe_unbooked") == 1
    # a standing order on the book: no probe (the key untouched), the book's own plan runs
    p2, b2, v2 = _flip_world(ioc_fill=300.0)
    p2.state.pop("mirror_s4_proof")
    p2.add_order(b2, side=SELL, wire=0.32, qty=100, kind="increase", intent="ORDER_INTENT_BUY_SHORT")
    v2.rest("oid-1", "SELL", 0.32, 100, intent="ORDER_INTENT_BUY_SHORT")
    st2 = _tick(p2, v2)
    assert "mirror_s4_proof" not in p2.state and _census(st2, "s4_probe_placed") == 0
    assert not [c for c in _places(v2) if c[3] == 1]


def test_s4v3_a_probe_the_venue_kept_is_cancelled_again_every_tick_before_the_walk_and_never_probed_beside(monkeypatch):
    """GAP 2. The cancel refused: the record keeps the id with
    `cancel.ok` False. Every tick after, BEFORE the walk (the cancel is
    the tick's first venue call), the cancel is retried -- paced,
    through _guarded, an exit op -- and no probe goes out meanwhile
    (inside the hour or past it). Accepted: the id cleared, `at` = now
    (the hour restarts), `cancel` = {ok, order, gone: cancelled,
    retries}; the record's `why` stays `cancel_failed`. An hour after
    THAT the probe runs again and proves. A refused cancel followed by
    a status the venue reads cancelled, or no record of the id, is
    gone too. A 429 on the cancel is E2's path -- `rate_limited`, the
    circuit -- the id kept, no status read, no abandon."""
    _shorts_on(monkeypatch)
    p, b, v = _flip_world(ioc_fill=300.0, cancel_ok=False)
    p.state.pop("mirror_s4_proof")
    st = _tick(p, v)
    rec = p.state["mirror_s4_proof"]
    assert rec["why"] == "cancel_failed" and rec["order_id"] == "oid-1" and rec["cancel"]["ok"] is False
    assert rec["cancel"]["retries"] == 0 and _census(st, "rate_limited") == 0
    assert ml._s4_held_order(rec) == "oid-1"
    # the venue still refuses: retried, counted, the id kept, no probe, inside the hour
    v2 = _NoClose(held={SLUG: -300}, ioc_fill=300.0, cancel_ok=False)
    v2.orders, v2.n = v.orders, v.n
    st2 = _tick(p, v2, now=NOW + 30)
    assert _kinds(v2)[0] == "cancel" and _cancels(v2) == [("cancel", "oid-1", SLUG)]
    assert ("status", "oid-1") in v2.calls and v2.orders["oid-1"]["state"] == "new"
    assert p.state["mirror_s4_proof"]["order_id"] == "oid-1" and p.state["mirror_s4_proof"]["cancel"]["retries"] == 1
    assert not _places(v2) and _census(st2, "s4_probe_placed") == 0 and _census(st2, "s4_unproven") == 1
    assert st2["ops"] == 1 and not st2["abandoned"]
    # an hour on, still refusing: no second probe beside the first
    v3 = _NoClose(held={SLUG: -300}, ioc_fill=300.0, cancel_ok=False)
    v3.orders, v3.n = v.orders, v.n
    st3 = _tick(p, v3, now=NOW + 3600)
    assert not _places(v3) and _cancels(v3) == [("cancel", "oid-1", SLUG)]
    assert p.state["mirror_s4_proof"]["cancel"]["retries"] == 2 and _census(st3, "s4_probe_placed") == 0
    # the venue accepts: cleared, the hour restarts from now, no probe this tick
    v4 = _NoClose(held={SLUG: -300}, ioc_fill=300.0)
    v4.orders, v4.n = v.orders, v.n
    st4 = _tick(p, v4, now=NOW + 3700)
    rec4 = p.state["mirror_s4_proof"]
    assert v4.orders["oid-1"]["state"] == "cancelled" and _kinds(v4)[0] == "cancel"
    assert rec4["order_id"] is None and rec4["at"] == NOW + 3700 and rec4["why"] == "cancel_failed"
    assert rec4["cancel"]["ok"] is True and rec4["cancel"]["order"] == "oid-1"
    assert rec4["cancel"]["gone"] == "cancelled" and rec4["cancel"]["retries"] == 3
    assert ml._s4_held_order(rec4) is None and not _places(v4) and ("status", "oid-1") not in v4.calls
    assert _census(st4, "s4_probe_placed") == 0 and _census(st4, "s4_unproven") == 1
    cr = [r for r in st4["recent"] if r["what"] == "s4_probe_cancel"]
    assert cr and cr[-1]["why"] == "gone" and cr[-1]["order"] == "oid-1"
    # an hour after the clearing: the probe again, proved, the cover
    v5 = _NoClose(held={SLUG: -300}, ioc_fill=300.0)
    v5.orders, v5.n = v.orders, v.n
    st5 = _tick(p, v5, now=NOW + 7300)
    assert [c[2:4] for c in _places(v5)] == [(0.25, 1), (0.32, 300)] and _cancels(v5) == [("cancel", "oid-2", SLUG)]
    assert p.state["mirror_s4_proof"]["proved"] is True and p.state["mirror_s4_proof"]["order_id"] == "oid-2"
    assert b["ledger_net"] == 0 and _census(st5, "s4_proved") == 1
    # refused, then the venue's own record reads it cancelled: gone by status
    for state, gone in (("cancelled", "cancelled"), ("expired", "expired"), (None, "no_record")):
        pa, ba, va = _flip_world(ioc_fill=300.0, cancel_ok=False)
        pa.state.pop("mirror_s4_proof")
        _tick(pa, va)
        vb = _NoClose(held={SLUG: -300}, ioc_fill=300.0)
        if state is not None:
            vb.orders = {"oid-1": {**va.orders["oid-1"], "state": state}}
        stb = _tick(pa, vb, now=NOW + 30)
        assert _kinds(vb)[:2] == ["cancel", "status"] and not _places(vb), state
        assert pa.state["mirror_s4_proof"]["order_id"] is None, state
        assert pa.state["mirror_s4_proof"]["cancel"]["gone"] == gone and _census(stb, "rate_limited") == 0, state
    # the cancel's 429: named and penalized, the id kept, no status read, not abandoned
    pc, bc, vc = _flip_world(ioc_fill=300.0, cancel_ok=False)
    pc.state.pop("mirror_s4_proof")
    _tick(pc, vc)
    vd = _Cancel429(held={SLUG: -300}, ioc_fill=300.0)
    vd.orders, vd.n = vc.orders, vc.n
    std = _tick(pc, vd, now=NOW + 30)
    assert _cancels(vd) == [("cancel", "oid-1", SLUG)] and ("status", "oid-1") not in vd.calls
    assert _census(std, "rate_limited") == 1 and venue_pace.penalty_left() > 0.0 and not std["abandoned"]
    assert pc.state["mirror_s4_proof"]["order_id"] == "oid-1" and pc.state["mirror_s4_proof"]["cancel"]["retries"] == 1
    assert not _places(vd) and vd.orders["oid-1"]["state"] == "new"
    venue_pace._penalty_until = 0.0
    # the 429 on the FIRST cancel (inside _s4_probe) is named the same way
    pe, be, ve = _flip_world(ioc_fill=300.0)
    pe.state.pop("mirror_s4_proof")
    ve.__class__ = _Cancel429
    ste = _tick(pe, ve)
    assert pe.state["mirror_s4_proof"]["why"] == "cancel_failed" and _census(ste, "rate_limited") == 1
    assert pe.state["mirror_s4_proof"]["order_id"] == "oid-1" and not ste["abandoned"]
    venue_pace._penalty_until = 0.0
    # the retry goes through _guarded (paced, counted) and never through a row
    src = inspect.getsource(ml._s4_cancel_retry)
    assert "_guarded(t, 0, t.pmus.cancel_order, oid, slug)" in src and "_abandon(" not in src
    assert "await _s4_cancel_retry(t)" in inspect.getsource(ml._tick)
    # the unit: what holds an id
    assert ml._s4_held_order({"proved": False, "order_id": "x", "cancel": {"ok": False}}) == "x"
    for rec_ in ({"proved": True, "order_id": "x", "cancel": {"ok": False}},
                 {"proved": False, "order_id": "x", "cancel": {"ok": True}},
                 {"proved": False, "order_id": "x"}, {"proved": False, "order_id": None, "cancel": {"ok": False}},
                 "garbage", None):
        assert ml._s4_held_order(rec_) is None, rec_


def test_s4v3_a_kept_probe_that_filled_before_its_cancel_is_booked_on_its_book_and_cleared(monkeypatch):
    """GAP 2 meets GAP 1: the kept order filled (the venue refuses the
    cancel; its status reads filled, 1 share at 0.27). The retry leaves
    the fill on the record (`cancel.filled`), holds the id, and the
    walk books it on the book the share belongs to: the `s4_probe` row,
    the ledger -299, realized (0.32 - 0.27) x 1 = 0.05, counted
    `s4_probe_filled`; then the id is cleared and the hour restarts.
    No probe that tick."""
    _shorts_on(monkeypatch)
    p, b, v = _flip_world(ioc_fill=300.0, cancel_ok=False)
    p.state.pop("mirror_s4_proof")
    _tick(p, v)
    v2 = _NoClose(held={SLUG: -300}, ioc_fill=300.0)
    v2.orders = {"oid-1": {**v.orders["oid-1"], "state": "filled", "filled_shares": 1.0, "avg_px": 0.27}}
    st2 = _tick(p, v2, now=NOW + 30)
    rec = p.state["mirror_s4_proof"]
    assert _kinds(v2)[:2] == ["cancel", "status"] and not _places(v2)
    o = _probe_row(p)
    assert o is not None and (o["qty"], o["avg_px"], o["state"], o["order_id"]) == (1, 0.27, "filled", "oid-1")
    assert o["realized"] == pytest.approx(0.05) and b["ledger_net"] == -299
    assert b["realized_pnl"] == pytest.approx(0.05) and p.rows[b["standing_row_id"]]["filled_shares"] == 299.0
    assert rec["order_id"] is None and rec["at"] == NOW + 30 and rec["cancel"]["gone"] == "filled"
    assert rec["cancel"]["filled"] == 1.0 and rec["booked"]["row"] == o["id"] and rec["booked"]["ledger"] == -299
    assert _census(st2, "s4_probe_filled") == 1 and _census(st2, "s4_probe_placed") == 0
    assert ml._s4_held_order(rec) is None and b["state"] == "live"
    # an hour on: proved, the cover the ledger's 299
    v3 = _NoClose(held={SLUG: -299}, ioc_fill=300.0)
    _tick(p, v3, now=NOW + 3700)
    assert [c[3] for c in _places(v3)] == [1, 299] and b["ledger_net"] == 0


def test_s4v3_a_429_on_the_probe_holds_it_for_the_hour_one_abandon_not_one_per_tick(monkeypatch):
    """GAP 3, the reviewer's four ticks: the venue limits creates; the
    first tick probes, is refused 429, abandons `rate_limited` and
    writes {"proved": false, "why": "rate_limited", "at": now}; the
    three ticks after place nothing and abandon nothing. The record is
    the hold, not a verdict: `rate_limited` counted once."""
    _shorts_on(monkeypatch)
    p, b, v = _flip_world(ioc_fill=300.0, place_raises=_sdk_429())
    p.state.pop("mirror_s4_proof")
    abandoned, placed = 0, 0
    for i in range(4):
        st = _tick(p, v, now=NOW + 5 * i)
        abandoned += int(bool(st["abandoned"]))
        placed += _census(st, "s4_probe_placed")
        venue_pace._penalty_until = 0.0
    assert abandoned == 1 and placed == 1 and len(_places(v)) == 1
    rec = p.state["mirror_s4_proof"]
    assert rec["proved"] is False and rec["why"] == "rate_limited" and rec["at"] == NOW
    assert b["last_plan"]["s4"] == {"unproven": "rate_limited"} and b["ledger_net"] == -300


def test_s4v3_the_proof_requires_a_standing_state_on_the_echo(monkeypatch):
    """INFO 5. The SDK's OrderState enum, as _norm_order spells it:
    new, pending_new and pending_risk are the states a standing 1-share
    limit is listed under (accepted and resting, being written, past
    listing and awaiting risk) and prove; filled, partially_filled,
    canceled, expired, rejected, replaced, pending_cancel and
    pending_replace are not a standing order and are `wrong_state`; no
    state (`unknown`) is `state_missing`. The state is the LAST check
    (a wrong intent in a filled state is `wrong_intent`); every
    verdict cancels the probe."""
    _shorts_on(monkeypatch)
    assert ml.S4_RESTING_STATES == frozenset({"new", "pending_new", "pending_risk"})
    table = ([(s, None) for s in ("new", "pending_new", "pending_risk")]
             + [(s, "wrong_state") for s in ("filled", "partially_filled", "canceled", "cancelled", "expired",
                                             "rejected", "replaced", "pending_cancel", "pending_replace")]
             + [("unknown", "state_missing"), ("", "state_missing")])
    for state, why in table:
        p, b, v = _flip_world(ioc_fill=300.0)
        p.state.pop("mirror_s4_proof")
        v.__class__ = _ProbeState
        v.probe_state = state
        st = _tick(p, v)
        rec = p.state["mirror_s4_proof"]
        assert rec["echo"]["state"] == state and ("cancel", "oid-1", SLUG) in v.calls, state
        if why is None:
            assert rec["proved"] is True and [c[3] for c in _places(v)] == [1, 300] and b["ledger_net"] == 0, state
        else:
            assert rec["proved"] is False and rec["why"] == why and b["ledger_net"] == -300, state
            assert _census(st, "s4_unproven") == 1 and b["last_plan"]["s4"] == {"unproven": why}, state
    # the order of the checks: price, side, intent, then the state
    src = inspect.getsource(ml._s4_probe)
    assert src.index('"wrong_intent"') < src.index('"wrong_state"')

    class _FilledWrongIntent(_ProbeState):
        def rest(self, oid, side="BUY", price=0.30, qty=300, slug=SLUG, created=None, state="new",
                 filled=0.0, avg=None, intent=None):
            return super().rest(oid, side, price, qty, slug, created, state, filled, avg,
                                "ORDER_INTENT_BUY_LONG" if qty == 1 else intent)
    p2, b2, v2 = _flip_world(ioc_fill=300.0)
    p2.state.pop("mirror_s4_proof")
    v2.__class__ = _FilledWrongIntent
    v2.probe_state = "filled"
    _tick(p2, v2)
    assert p2.state["mirror_s4_proof"]["why"] == "wrong_intent"


# ------- 23. W1 / R4 (2026-09-07): the live lane stops re-reading expired books
#
# The 13:44Z tick: 26 of 27 book reads on MARKET_STATE_EXPIRED markets
# whose markets row still read closed=false, two paced venue calls each
# (the quote, then the per-market position read `snap_market_unreadable`
# names), >= 18 s of a 20.4 s tick before a single candidate. A book
# whose OWN read said the market had ended is remembered for
# ms.UNMAPPED_TTL_S (_terminal_book_until) and, inside the memo, skips
# both reads and is held `no_mark` with `venue_terminal` on the plan;
# step M still runs every tick. Never for a halt, never over an open
# order, never from a candidate read. No order path is touched.

EXPIRED_BOOK = "MARKET_STATE_EXPIRED"


def test_an_expired_book_is_read_once_per_ttl_and_still_closes_on_the_markets_row():
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue(state=EXPIRED_BOOK)
    # the first terminal read is today's: venue_halted on the census, the
    # book held no_mark, BOTH reads spent -- and the book memo written
    st = _tick(p, v)
    assert [c[1] for c in v.calls if c[0] == "bbo"] == [SLUG] and st["reads"] == 1
    assert _census(st, "venue_halted") == 1 and _census(st, "no_mark") == 1, st["census"]
    assert _census(st, "book_terminal_skipped") == 0 and st["snap_market_planned"] == 1
    assert b["last_reason"] == "no_mark" and b["state"] == "live"
    assert ml._terminal_book_until == {("rn1", CID): NOW + ms.UNMAPPED_TTL_S}
    assert ml._terminal_book_state == {("rn1", CID): EXPIRED_BOOK}
    assert ml._terminal_until == {}, "the candidate memo (D1) is not the book's"
    # inside the TTL: no quote read, no per-market read, no order sent;
    # the plan names the venue's word, the census counts the skip
    v.calls.clear()
    st2 = _tick(p, v, now=NOW + 30)
    assert "bbo" not in _kinds(v) and st2["reads"] == 0 and not st2.get("snap_market_planned")
    assert _census(st2, "book_terminal_skipped") == 1 and _census(st2, "no_mark") == 1, st2["census"]
    assert _census(st2, "venue_halted") == 0 and _census(st2, "snap_market_unreadable") == 0
    assert b["last_reason"] == "no_mark" and b["state"] == "live" and st2["books_live"] == 1
    # E9 part 1: every plan write carries `his_fills_seen` (his one fill,
    # named by the first tick that held it: the no_mark read); the rest
    # of the memo skip's plan is exactly as W1 / R4 wrote it
    seen = b["last_plan"]["his_fills_seen"]
    assert [e["name"] for e in seen] == ["no_mark"] and seen[0]["at"] == NOW
    # T2 (FILL lane 4): `fills_hwm` rides beside `his_fills_seen` on every
    # plan write once a flush has succeeded (the first tick's), so it is
    # dropped from the comparison as the list is
    assert {k: v for k, v in b["last_plan"].items() if k not in ("his_fills_seen", "fills_hwm")} == {
        "kind": "no_plan", "at": NOW + 30, "venue_terminal": EXPIRED_BOOK}
    assert b["last_plan"]["fills_hwm"] == NOW - 3000, "the record's hwm: the one fill's stamp, written by the first tick"
    assert not _places(v) and not _cancels(v) and not st2["abandoned"]
    # the TTL runs: the book is read again, and memoised again
    v.calls.clear()
    st3 = _tick(p, v, now=NOW + ms.UNMAPPED_TTL_S + 1)
    assert [c[1] for c in v.calls if c[0] == "bbo"] == [SLUG] and st3["reads"] == 1
    assert _census(st3, "book_terminal_skipped") == 0 and _census(st3, "venue_halted") == 1
    assert ml._terminal_book_until == {("rn1", CID): NOW + ms.UNMAPPED_TTL_S + 1 + ms.UNMAPPED_TTL_S}
    # STEP M RUNS EVERY TICK: the tick the markets row reads closed the
    # book closes, inside the memo, with no read spent
    v.calls.clear()
    p.markets[CID] = {"closed": True, "resolved": False, "resolved_prices": None}
    st4 = _tick(p, v, now=NOW + ms.UNMAPPED_TTL_S + 31)
    assert "bbo" not in _kinds(v) and st4["reads"] == 0
    assert _census(st4, "market_closed") == 1 and _census(st4, "book_terminal_skipped") == 0
    assert b["state"] == "closed" and _census(st4, "closed_cancelled") == 1, (b["state"], st4["census"])
    assert "book_terminal_skipped" in ml.CENSUS_KEYS and ml.CENSUS_KEYS[-1] == "cand_terminal_skipped"


def test_a_halted_book_is_read_every_tick():
    """HALTED / SUSPENDED / PREOPEN reopen: the book is read every tick
    as before (venue_halted, held no_mark) and the memo never writes.
    Nor does an unread state -- the SDK-typed empty shape or a failed
    read -- memoise anything."""
    for state in ("MARKET_STATE_HALTED", "MARKET_STATE_SUSPENDED", "MARKET_STATE_PREOPEN"):
        p = _pool()
        b = p.add_book(ledger=300)
        v = _Venue(state=state, bid=None, ask=None)
        for i in range(3):
            st = _tick(p, v, now=NOW + 30 * i)
            assert _census(st, "venue_halted") == 1 and _census(st, "book_terminal_skipped") == 0, state
            assert st["reads"] == 1 and st["snap_market_planned"] == 1 and b["last_reason"] == "no_mark"
            assert not st["abandoned"], "a book's halt never counts toward the streak"
        assert [c[1] for c in v.calls if c[0] == "bbo"] == [SLUG] * 3, state
        assert ml._terminal_book_until == {} and ml._terminal_book_state == {}, state
    for kw in (dict(state=None, bid=None, ask=None), dict(raise_bbo=True)):
        p = _pool()
        p.add_book(ledger=300)
        v = _Venue(**kw)
        st = _tick(p, v)
        assert st["reads"] == 1 and ml._terminal_book_until == {}, kw
        assert _census(st, "book_terminal_skipped") == 0 and _census(st, "no_quote") == 1, kw


def test_a_book_with_an_open_order_is_never_memo_skipped():
    """A rest stands on the book when the market expires. The first
    terminal read cancels it under no_mark (as today) and writes NO
    memo -- the cancel must land first; the next terminal read, with
    nothing open, memoises, and the one after is skipped. A cancel the
    venue refused leaves the order 'unknown' (the book frozen
    cancel_pending, t.nonterminal): read every tick, never memoised."""
    p = _pool()
    b = p.add_book(ledger=300)
    o = p.add_order(b, side=BUY, wire=0.30, qty=300)
    v = _Venue(state=EXPIRED_BOOK)
    v.rest("oid-1", "BUY", 0.30, 300)
    st = _tick(p, v)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)] and p.orders[o["id"]]["reason"] == "no_mark"
    assert p.orders[o["id"]]["state"] == "cancelled" and b["open_order_id"] is None
    assert _census(st, "venue_halted") == 1 and b["last_reason"] == "no_mark" and b["state"] == "live"
    assert ml._terminal_book_until == {} and _census(st, "book_terminal_skipped") == 0
    v.calls.clear()
    st2 = _tick(p, v, now=NOW + 30)
    assert [c[1] for c in v.calls if c[0] == "bbo"] == [SLUG] and _census(st2, "book_terminal_skipped") == 0
    assert ml._terminal_book_until == {("rn1", CID): NOW + 30 + ms.UNMAPPED_TTL_S}
    v.calls.clear()
    st3 = _tick(p, v, now=NOW + 60)
    assert "bbo" not in _kinds(v) and _census(st3, "book_terminal_skipped") == 1
    assert not _places(v) and not _cancels(v)
    # the refused cancel: non-terminal order, frozen book, no memo on any tick
    ml._terminal_book_until.clear()
    ml._terminal_book_state.clear()
    p2 = _pool()
    b2 = p2.add_book(ledger=300)
    p2.add_order(b2, side=BUY, wire=0.30, qty=300)
    v2 = _Venue(state=EXPIRED_BOOK, cancel_ok=False)
    v2.rest("oid-1", "BUY", 0.30, 300)
    for i in range(2):
        st = _tick(p2, v2, now=NOW + 30 * i)
        assert ml._terminal_book_until == {} and _census(st, "book_terminal_skipped") == 0, i
    assert b2["state"] == "frozen" and b2["frozen_reason"] == "cancel_pending"
    assert [c[1] for c in v2.calls if c[0] == "bbo"] == [SLUG] * 2, "read every tick"


def test_the_book_memo_never_sets_from_a_candidate_read():
    p = _pool()
    v = _Venue(state=EXPIRED_BOOK)
    st = _tick(p, v)
    assert _census(st, "venue_halted") == 1 and not p.books and not _places(v)
    assert ml._terminal_until == {("rn1", CID): NOW + ms.UNMAPPED_TTL_S}, "D1's memo, as before"
    assert ml._terminal_book_until == {} and ml._terminal_book_state == {}
    # the memo's one writer is the book's own read, after step M and
    # before the quote read; the candidate path never names it
    src = inspect.getsource(ml._tick_book)
    assert "_memo_terminal_book(t, book, r)" in src and "book=True" in src
    assert src.index("await _market(t, cid)") < src.index("_terminal_book_until.get(") < src.index("_read_market(")
    cand = inspect.getsource(ml._tick_candidate)
    assert "_terminal_book" not in cand and "_memo_terminal_book" not in cand
    assert "_memo_terminal_book" not in inspect.getsource(ml._bbo)
    # the shadow's invariant stands
    from tests.test_mirror_shadow import test_the_shadow_never_touches_an_order
    test_the_shadow_never_touches_an_order()


# ------- 23b. W1 review pins (the adversarial review of R4)
#
# The memo's guards live at WRITE time only (_memo_terminal_book reads
# t.open_by_book / t.nonterminal); the SKIP path at the head of the plan
# reads the memo alone. Step O fills those two sets and `continue`s an
# order whose book row it could not read (mirror_live._reconcile_orders,
# the fetchrow's except), so one transient on the tick the book first
# reads terminal writes the memo over a standing rest, and every tick
# inside the TTL then returns before the cancel the no_mark refusal
# used to send. These pins say what the rule promised: never a memo
# while the book has an order open, and never a skip over one.


def test_the_book_memo_never_writes_over_a_rest_step_o_did_not_see_and_never_skips_its_cancel():
    """Tick 1: a rest stands on the book (the row 'open', the venue
    listing it); step O's read of the order's book row raises, so the
    order is in neither t.open_by_book nor t.nonterminal; the book's own
    read says EXPIRED. The memo must NOT be written (the book row the
    walk read carries `open_order_id`). Tick 2: step O reconciles the
    rest as before; the book is read, gets no mark and cancels the rest
    under no_mark -- as every tick did before W1 -- never memo-skipped
    over it."""
    p = _pool()
    b = p.add_book(ledger=300)
    o = p.add_order(b, side=BUY, wire=0.30, qty=300)
    v = _Venue(state=EXPIRED_BOOK)
    v.rest("oid-1", "BUY", 0.30, 300)
    p.raise_on.append(("ml-book-read", RuntimeError("blip")))
    st = _tick(p, v)
    assert p.orders[o["id"]]["state"] == "open" and not _cancels(v), "step O never saw the order"
    assert _census(st, "venue_halted") == 1 and b["open_order_id"] == o["id"]
    assert ml._terminal_book_until == {} and ml._terminal_book_state == {}, \
        "the memo was written while the book had an order open"
    p.raise_on.clear()
    v.calls.clear()
    st2 = _tick(p, v, now=NOW + 30)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)], "the standing rest was memo-skipped, never cancelled"
    assert p.orders[o["id"]]["state"] == "cancelled" and p.orders[o["id"]]["reason"] == "no_mark"
    assert _census(st2, "book_terminal_skipped") == 0 and b["open_order_id"] is None
    # with the rest gone the next terminal read memoises, and the one
    # after is skipped -- the builder's rule, unchanged
    v.calls.clear()
    _tick(p, v, now=NOW + 60)
    assert ml._terminal_book_until == {("rn1", CID): NOW + 60 + ms.UNMAPPED_TTL_S}
    v.calls.clear()
    st4 = _tick(p, v, now=NOW + 90)
    assert "bbo" not in _kinds(v) and _census(st4, "book_terminal_skipped") == 1


def test_a_rest_that_appears_inside_the_memo_is_cancelled_not_skipped():
    """The memo holds (written with nothing open). A rest then stands
    on the book -- adopted by step O, or placed by a path the memo did
    not see. The tick inside the memo must not return over it: the
    book is read and the rest cancelled under no_mark, as before W1."""
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(state=EXPIRED_BOOK)
    _tick(p, v)
    assert ml._terminal_book_until == {("rn1", CID): NOW + ms.UNMAPPED_TTL_S}
    o = p.add_order(b, side=BUY, wire=0.30, qty=300)
    v.rest("oid-1", "BUY", 0.30, 300)
    v.calls.clear()
    st = _tick(p, v, now=NOW + 30)
    assert _cancels(v) == [("cancel", "oid-1", SLUG)], "a rest inside the memo was never cancelled"
    assert p.orders[o["id"]]["state"] == "cancelled" and p.orders[o["id"]]["reason"] == "no_mark"
    assert _census(st, "book_terminal_skipped") == 0 and b["state"] == "live"


def test_a_memo_skipped_book_still_closes_on_its_standing_row():
    """The standing row read (settled / cashed_out / cancelled) comes
    before the memo, like step M: the tick the row retires, the book
    closes inside the memo with no read spent."""
    p = _pool()
    b = p.add_book(ledger=0)
    v = _Venue(state=EXPIRED_BOOK)
    _tick(p, v)
    assert ml._terminal_book_until == {("rn1", CID): NOW + ms.UNMAPPED_TTL_S}
    v.calls.clear()
    p.rows[b["standing_row_id"]]["status"] = "settled"
    st = _tick(p, v, now=NOW + 30)
    assert "bbo" not in _kinds(v) and st["reads"] == 0
    assert b["state"] == "closed" and st["closed_books"] == 1
    assert _census(st, "book_terminal_skipped") == 0


def test_a_memo_skipped_book_whose_markets_row_is_unreadable_is_named_market_unreadable():
    """An unreadable markets row inside the memo keeps its own name and
    its own cancel (`market_unreadable`, step M's rule): the memo never
    stands in for a reading the tick could not make."""
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(state=EXPIRED_BOOK)
    _tick(p, v)
    assert ml._terminal_book_until != {}
    v.calls.clear()
    del p.markets[CID]
    st = _tick(p, v, now=NOW + 30)
    assert "bbo" not in _kinds(v)
    assert _census(st, "market_unreadable") == 1 and _census(st, "book_terminal_skipped") == 0
    assert b["last_reason"] == "market_unreadable" and b["state"] == "live"


def test_the_book_memo_never_normalises_the_venues_word():
    """A state string that is not one of ms.STATE_TERMINAL's exact
    strings -- the venue's word in another case, or a word the set does
    not name -- is a halt-like read: no memo, read every tick."""
    for state in ("market_state_expired", "MARKET_STATE_EXPIRED ", "MARKET_STATE_SETTLED"):
        p = _pool()
        p.add_book(ledger=300)
        v = _Venue(state=state, bid=None, ask=None)
        for i in range(2):
            st = _tick(p, v, now=NOW + 30 * i)
            assert _census(st, "venue_halted") == 1 and st["reads"] == 1, state
        assert ml._terminal_book_until == {} and ml._terminal_book_state == {}, state


# ------------------------------------------------ 24. W2 (2026-09-07)
#
# gap_planned_unopened.md: 58 mapped tennis and football markets ($218k
# in 24 h) had a shadow plan on an OPEN two-sided book while he traded
# and never a mirror_books row. P1: his fills sit only on the venue's
# SHORT-side token, the mapper names no long token, and the candidate
# left silently (mirror_live.py:6618 at 1c1e1d7) -- the catalogue names
# it by identity and the EXISTING short road runs. P2: every exit of
# _tick_candidate returns its name and one row per (whale, market)
# transition lands on mirror_candidate_refusals (migration 054). P3:
# the candidate walk rotates, the shadow's planned first.

def _short_side_only(**kw):
    """His fills on the venue's SHORT-side token alone (class B: the aec
    tennis family's `<his side>:SHORT`): 400 of the other token at
    0.72, his last move, so his level for our short is 1 - 0.72 = 0.28
    in long space -- _short_world's reading with the long-token leg
    absent (net -400 instead of -300)."""
    kw.setdefault("fills", [_fill(N, "BUY", 400, 0.72, NOW - 2000)])
    kw.setdefault("snap", {M: 0.0, N: 400.0})
    return _pool(**kw)


def _map_short_only(monkeypatch, long_asset=None):
    """The mapper's verdict for those fills, exactly the dict
    _choose_long returns when his only token resolved BUY_SHORT and
    other_of() finds no second token of his: long_asset None (or the
    control's M), other_asset N."""
    seen = []

    async def _m(pool, fills, pmus=None, **kw):
        seen.append(kw.get("condition_id"))
        return {"us_slug": SLUG, "long_asset": long_asset, "other_asset": N, "source": "premap"}
    monkeypatch.setattr(ms, "map_market", _m)
    return seen


def _sibling_reads(p):
    return [a for k, s, a in p.sent if "ml-sibling-token" in s]


def _walked(p):
    """The candidates the walk called, in order: one his_fills read each."""
    return [a[1] for s, a in p.queries if "AS market_title, t.event_slug" in s]


def test_w2_fills_only_on_the_short_side_token_open_a_short_book_through_the_existing_gates(monkeypatch):
    """P1. ONE catalogue read names the long token (the condition's
    sibling of the token in hand), then the short road the file already
    has: target -400 signed, his other-token BUY read at 1 - 0.72, ONE
    BUY_SHORT rest at the ask -- the same book, rest and row
    test_with_the_knob_on_a_short_book_opens_by_a_buy_short_rest_at_his_level
    pins for a short whose long token his fills named. No row on the
    refusal table: the book opened."""
    _shorts_on(monkeypatch)
    _map_short_only(monkeypatch)
    p = _short_side_only()
    v = _Venue()
    st = _tick(p, v, http=_mkt(0.0, 400.0))
    assert _sibling_reads(p) == [(CID, N)], "one catalogue read, for the sibling of his short-side token"
    assert st["short"]["on"] is True and len(p.books) == 1
    b = next(iter(p.books.values()))
    assert (b["long_asset"], b["other_asset"], b["intent"]) == (M, N, SHORT)
    assert b["target"] == -400 and b["his_level"] == round(1.0 - 0.72, 6) == pytest.approx(0.28)
    row = p.rows[b["standing_row_id"]]
    assert row["asset"] == N and row["raw"]["preview"]["intent"] == SHORT and row["requested_shares"] == 400.0
    pl = _places(v)
    assert len(pl) == 1
    assert pl[0][1:] == (SLUG, 0.32, 400, False, "TIME_IN_FORCE_GOOD_TILL_CANCEL", SHORT, True, None)
    o = next(iter(p.orders.values()))
    assert (o["side"], o["intent"], o["kind"], o["tif"], o["wire"]) == (SELL, SHORT, "increase", "GTC", 0.32)
    assert _census(st, "short_open") == 1 and _census(st, "rest_placed") == 1
    assert _census(st, "long_token_unknown") == 0 and _census(st, "short_side_refused") == 0
    assert p.cand_refusals == [], "no row on an opened book"


def test_w2_a_short_side_only_candidate_is_refused_by_the_gates_exactly_as_one_whose_long_token_was_named(monkeypatch):
    """P1. The knob off (`short_side_refused`), the model disarmed
    (`short_model_disarmed`), the S4 proof failing on the short gate
    (`short_gate_refused`): the catalogue-named long token and the
    mapper-named one leave under the same name, with the same row on
    the refusal table, and nothing opens. Never a widened gate."""
    orig = le.short_model_confirmed
    arms = (
        ("short_side_refused", lambda: monkeypatch.setattr(rules, "MIRROR_SHORTS", False)),
        ("short_model_disarmed", lambda: monkeypatch.setattr(le, "short_model_confirmed", lambda: False)),
        ("short_gate_refused", lambda: None),
    )
    for want, arm in arms:
        rows = {}
        for la in (None, M):
            ml._cand_refusal_last.clear()          # a fresh world: the memo is per (whale, cid)
            _shorts_on(monkeypatch)
            monkeypatch.setattr(le, "short_model_confirmed", orig)
            arm()
            _map_short_only(monkeypatch, long_asset=la)
            p = _short_side_only()
            if want == "short_gate_refused":
                p.state["short_side_proof"] = {"ok": 5, "mismatch": 1}
            v = _Venue()
            st = _tick(p, v, http=_mkt(0.0, 400.0))
            assert _census(st, want) == 1 and not p.books and not _places(v), (want, la, st["census"])
            assert _census(st, "long_token_unknown") == 0
            assert _sibling_reads(p) == ([(CID, N)] if la is None else [])
            assert [r["refusal"] for r in p.cand_refusals] == [want], (want, la)
            rows[la] = {k: v_ for k, v_ in p.cand_refusals[0].items() if k not in ("tick_s", "long_from")}
            # the row says where the long token came from (W2 review LOW-1):
            # the catalogue when his fills never touched it, nothing otherwise
            assert p.cand_refusals[0]["long_from"] == ("catalogue" if la is None else None), (want, la)
        assert rows[None] == rows[M], want
        assert rows[None]["long_asset"] == M and rows[None]["his_net"] == -400.0


def test_w2_an_unnamed_long_token_refuses_long_token_unknown_before_any_read(monkeypatch):
    """P1. The catalogue names no other token of the condition (or
    cannot be read, or the mapper named neither side): refused by name
    BEFORE the quote read, memoised for the unmapped TTL, one row on the
    refusal table with the slug and no long token; no venue call, no
    book. Never a token the catalogue does not name."""
    _shorts_on(monkeypatch)
    _map_short_only(monkeypatch)
    p = _short_side_only()
    p.token_cid = {N: CID}                  # the catalogue holds his token alone
    v = _Venue()
    st = _tick(p, v, http=_mkt(0.0, 400.0))
    assert _census(st, "long_token_unknown") == 1 and not p.books and not _places(v)
    assert "bbo" not in _kinds(v), "refused before the quote read"
    assert st["reads"] == 0 and st.get("capped_tick") is not True
    assert _sibling_reads(p) == [(CID, N)]
    assert ml._unmapped_until[("rn1", CID)] == NOW + ms.UNMAPPED_TTL_S
    rows = p.cand_refusals
    assert [(r["refusal"], r["us_slug"], r["long_asset"], r["condition_id"], r["whale"]) for r in rows] == [
        ("long_token_unknown", SLUG, None, CID, "rn1")]
    assert rows[0]["his_net"] is None and rows[0]["target"] is None and rows[0]["mark"] is None
    assert rows[0]["at_ts"] == NOW and rows[0]["active_conditions"] == 1 and rows[0]["cand_reads"] == 0
    # memoised: the next tick spends nothing on it and writes nothing new
    st2 = _tick(p, v, now=NOW + 10, http=_mkt(0.0, 400.0))
    assert _census(st2, "long_token_unknown") == 0 and "bbo" not in _kinds(v) and len(p.cand_refusals) == 1
    # an unreadable catalogue is the same refusal, never a guess (a fresh
    # world: the unmapped memo is per (whale, cid))
    ml._unmapped_until.clear()
    p3 = _short_side_only()
    p3.raise_on.append(("ml-sibling-token", RuntimeError("db down")))
    v3 = _Venue()
    st3 = _tick(p3, v3, http=_mkt(0.0, 400.0))
    assert _census(st3, "long_token_unknown") == 1 and not p3.books and "bbo" not in _kinds(v3)
    # a mapper naming NEITHER token: nothing to look up, the same name, no read

    async def _neither(pool, fills, pmus=None, **kw):
        return {"us_slug": SLUG, "long_asset": None, "other_asset": None, "source": "premap"}
    monkeypatch.setattr(ms, "map_market", _neither)
    ml._unmapped_until.clear()
    p4 = _short_side_only()
    v4 = _Venue()
    st4 = _tick(p4, v4, http=_mkt(0.0, 400.0))
    assert _census(st4, "long_token_unknown") == 1 and not p4.books and "bbo" not in _kinds(v4)
    assert _sibling_reads(p4) == [], "no other token to look the sibling up by"


def test_w2_the_long_path_is_unchanged_when_the_mapper_names_the_long_token(monkeypatch):
    """P1. A long book opens exactly as before: the one catalogue read
    is the OTHER token's (his fills name M alone, as they always did),
    the statement is one text for both lanes, and the long-token read
    sits after the grammar certification and before the book check and
    the quote read. A short-side mapping whose long token the mapper
    named makes no catalogue read at all."""
    p = _pool()
    v = _Venue()
    st = _tick(p, v)
    assert [c[1] for c in _places(v)] == [SLUG] and len(p.books) == 1
    assert _sibling_reads(p) == [(CID, M)], "the other token's read, as before; none for the long"
    assert _census(st, "long_token_unknown") == 0 and p.cand_refusals == []
    # one text for both lanes (== not `is`: a test above reloads ms)
    assert ml._SQL_SIBLING_TOKEN == ms.SIBLING_TOKEN_SQL
    assert _flat(ml._SQL_SIBLING_TOKEN) == ("SELECT token_id FROM market_tokens WHERE condition_id = $1 "
                                            "AND token_id <> $2 ORDER BY outcome_index LIMIT 1 /* ml-sibling-token */")
    src = inspect.getsource(ml._tick_candidate)
    assert (src.index("_grammar_admission(") < src.index('"long_token_unknown"')
            < src.index("in t.books_seen") < src.index("_read_market("))
    assert src.count("_SQL_SIBLING_TOKEN") == 2, "the long token and the other token, each through the one statement"
    _shorts_on(monkeypatch)
    _map_short_only(monkeypatch, long_asset=M)
    p2 = _short_side_only()
    v2 = _Venue()
    _tick(p2, v2, http=_mkt(0.0, 400.0))
    assert _sibling_reads(p2) == [] and len(p2.books) == 1


def test_w2_every_exit_of_the_candidate_returns_a_name_and_every_name_is_a_census_key():
    """P2. No bare return in _tick_candidate; the one None is a book
    opened; every returned literal is a census key (book_seen aside:
    a market with a book, which the walk skips before the call and the
    recorder never writes); the row is built from the tick's numbers
    alone -- no venue call, no read, no venue module."""
    src = inspect.getsource(ml._tick_candidate)
    assert not re.search(r"^\s+return\s*$", src, re.M), "a bare return is a silent exit"
    assert src.rstrip().endswith("return None")
    names = set(re.findall(r'return "([a-z_]+)"', src))
    assert {"long_token_unknown", "target_zero", "book_row_unreadable", "book_seen", "unmapped",
            "map_reads_capped", "map_source_unverified", "cand_game_full_skipped", "tick_abandoned",
            "game_unreadable", "game_cap_full", "short_model_disarmed", "no_price",
            "market_unreadable"} <= names
    for n in names - {"book_seen"}:
        assert n in ml.CENSUS_KEYS, n
    for k in ("long_token_unknown", "target_zero", "book_row_unreadable", "cand_unread_capped",
              "refusal_write_failed"):
        assert k in ml.CENSUS_KEYS and k not in ml._INTEG_CENSUS_KEYS
    assert ml._CAND_NOT_RECORDED == frozenset({"book_seen"})
    for fn in (ml._note_candidate_refusal, ml._flush_candidate_refusals, ml._name_unread,
               ml._candidate_order, ml._shadow_planned):
        s = inspect.getsource(fn)
        for banned in ("_bbo(", "_venue_call(", "pmus", "_read_market(", "_market_snap(", "_paced"):
            assert banned not in s, (fn.__name__, banned)
    # a market with a book: the name, no row, nothing counted
    ml._current_stats = ml._new_stats()
    p = _pool()
    t = ml._Tick(pool=p, pmus=_Venue(), http=_Http(), now=NOW, stats=ml._current_stats)
    t.books_seen.add(("rn1", CID))
    assert _run(ml._tick_candidate(t, "rn1", CID)) == "book_seen"
    assert _run(ml._walk_candidate(t, "rn1", CID)) == "book_seen" and t.cand_rows == []
    ml._current_stats = None
    # the statement writes every column of the 054 table but its id, once, from one JSON array
    cols = ("whale", "condition_id", "us_slug", "refusal", "at", "his_net", "target", "mark", "his_px",
            "ask", "band", "long_asset", "long_from", "books_live", "opened_today", "active_conditions", "cand_reads",
            "tick_s")
    s = _flat(ml._SQL_CAND_REFUSALS)
    assert s.startswith("INSERT INTO mirror_candidate_refusals (" + ", ".join(cols) + ")")
    assert "jsonb_to_recordset($1::jsonb)" in s and "$2" not in s and "ml-cand-refusals" in s


def test_w2_one_row_per_transition_and_a_restamp_after_900_s(monkeypatch):
    """P2. The same name inside 900 s writes nothing; a new name is a
    row; the name coming back is a row; the same name standing 900 s is
    re-stamped, not before. The row carries what the candidate read
    before it left and NULL for what it never reached. The census
    counters are what they were (one name per tick). A candidate that
    opened writes nothing, this tick or while the book stands."""
    assert ml.CAND_REFUSAL_RESTAMP_S == 900.0 == ms.UNMAPPED_TTL_S
    p = _short_world()                       # the knob off: short_side_refused every tick
    v = _Venue()
    st = _tick(p, v, http=_short_http())
    assert _census(st, "short_side_refused") == 1
    assert [r["refusal"] for r in p.cand_refusals] == ["short_side_refused"]
    r = p.cand_refusals[0]
    assert (r["whale"], r["condition_id"], r["us_slug"], r["long_asset"]) == ("rn1", CID, SLUG, M)
    assert r["at_ts"] == NOW and r["his_net"] == -300.0 and r["target"] == 0, "the P1 door's target IS 0"
    assert r["mark"] == 0.31 and r["ask"] == 0.32
    assert r["his_px"] is None and r["band"] is None and r["opened_today"] is None, "never reached"
    assert r["books_live"] == 0 and r["active_conditions"] == 1 and r["cand_reads"] == 1
    assert isinstance(r["tick_s"], float) and r["tick_s"] >= 0.0
    assert ml._cand_refusal_last[("rn1", CID)] == ("short_side_refused", NOW)
    st2 = _tick(p, v, now=NOW + 10, http=_short_http())
    assert _census(st2, "short_side_refused") == 1 and len(p.cand_refusals) == 1, "the same name: no row"
    # a transition: the knob on, the model disarmed
    orig = le.short_model_confirmed
    _shorts_on(monkeypatch)
    monkeypatch.setattr(le, "short_model_confirmed", lambda: False)
    st3 = _tick(p, v, now=NOW + 20, http=_short_http())
    assert _census(st3, "short_model_disarmed") == 1
    assert [x["refusal"] for x in p.cand_refusals] == ["short_side_refused", "short_model_disarmed"]
    assert p.cand_refusals[-1]["target"] == -300 and p.cand_refusals[-1]["his_px"] is None
    assert p.cand_refusals[-1]["at_ts"] == NOW + 20
    # and back: a transition too
    monkeypatch.setattr(rules, "MIRROR_SHORTS", False)
    monkeypatch.setattr(le, "short_model_confirmed", orig)
    _tick(p, v, now=NOW + 30, http=_short_http())
    assert [x["refusal"] for x in p.cand_refusals] == ["short_side_refused", "short_model_disarmed",
                                                       "short_side_refused"]
    # the same name standing: re-stamped at 900 s, not at 899
    st5 = _tick(p, v, now=NOW + 30 + 899, http=_short_http())
    assert _census(st5, "short_side_refused") == 1 and len(p.cand_refusals) == 3
    st6 = _tick(p, v, now=NOW + 30 + 900, http=_short_http())
    assert _census(st6, "short_side_refused") == 1 and len(p.cand_refusals) == 4
    assert p.cand_refusals[-1]["refusal"] == "short_side_refused" and p.cand_refusals[-1]["at_ts"] == NOW + 930
    assert ml._cand_refusal_last[("rn1", CID)] == ("short_side_refused", NOW + 930)
    # no row on an opened book, and none while the book stands; the memo
    # reads `opened`, so a refusal after the book closes is a transition
    # again whatever stood before the open
    p2 = _pool()
    v2 = _Venue()
    _tick(p2, v2, now=NOW + 1000)
    assert p2.books and p2.cand_refusals == [] and ml._cand_refusal_last[("rn1", CID)] == ("opened", NOW + 1000)
    _tick(p2, v2, now=NOW + 1010)
    assert p2.cand_refusals == []
    p3 = _short_world()
    _tick(p3, _Venue(), now=NOW + 1020, http=_short_http())
    assert [r["refusal"] for r in p3.cand_refusals] == ["short_side_refused"]


def test_w2_a_refusal_write_failure_never_blocks_the_tick_and_is_logged_once(caplog):
    """P2, fail-closed. The table absent (054 not applied) or a blip:
    the tick's census and verdicts are what they were, the failure is
    counted `refusal_write_failed` every tick and logged ONCE per
    process, the memo never takes the failed write, so the row lands
    on the next tick that can write it."""
    p = _short_world()
    v = _Venue()
    p.raise_on.append(("ml-cand-refusals", RuntimeError('relation "mirror_candidate_refusals" does not exist')))
    with caplog.at_level(logging.WARNING):
        st = _tick(p, v, http=_short_http())
        st2 = _tick(p, v, now=NOW + 10, http=_short_http())
    for s in (st, st2):
        assert s["status"] == "ok" and not s["abandoned"] and _census(s, "short_side_refused") == 1
        assert _census(s, "refusal_write_failed") == 1
    assert p.cand_refusals == [] and ml._cand_refusal_last == {}
    warns = [x for x in caplog.records if "mirror_candidate_refusals write failed" in x.getMessage()]
    assert len(warns) == 1 and warns[0].levelno == logging.WARNING, "logged once per process"
    assert ml._cand_write_logged is True
    p.raise_on.clear()
    st3 = _tick(p, v, now=NOW + 20, http=_short_http())
    assert _census(st3, "refusal_write_failed") == 0
    assert [r["refusal"] for r in p.cand_refusals] == ["short_side_refused"]
    assert ml._cand_refusal_last[("rn1", CID)] == ("short_side_refused", NOW + 20)
    # the write is the walk's last step before the instruments: one statement, after every whale
    # (T2 / FILL lane 4, 2026-09-08: the per-fill record's one write sits between it and the
    # instruments, the same tail on both tick paths)
    src = inspect.getsource(ml._tick)
    assert src.rstrip().endswith("await _flush_candidate_refusals(t)\n"
                                 "    await _flush_fill_answers(t)            # T2: the fills the tick named, one write\n"
                                 "    await _instruments(t)")
    assert src.count("_flush_candidate_refusals(") == 1 and src.count("_flush_fill_answers(") == 1


def test_w2_review_a_hung_refusal_write_is_bounded_and_counted_as_a_failed_write(monkeypatch):
    """W2 review MEDIUM-2. The pool carries no command_timeout and the
    write runs under the tick lock: a write that never returns is cut at
    `CAND_REFUSAL_WRITE_TIMEOUT_S`, counted `refusal_write_failed`, the
    memo left alone, the tick's verdicts what they were."""
    p = _short_world()
    v = _Venue()
    orig = p.execute

    async def hung(sql, *a):
        if "ml-cand-refusals" in sql:
            await asyncio.sleep(3600)
        return await orig(sql, *a)

    p.execute = hung
    assert ml.CAND_REFUSAL_WRITE_TIMEOUT_S == 5.0
    monkeypatch.setattr(ml, "CAND_REFUSAL_WRITE_TIMEOUT_S", 0.05)
    st = _tick(p, v, http=_short_http())
    assert st["status"] == "ok" and not st["abandoned"] and _census(st, "short_side_refused") == 1
    assert _census(st, "refusal_write_failed") == 1
    assert p.cand_refusals == [] and ml._cand_refusal_last == {}
    src = inspect.getsource(ml._flush_candidate_refusals)
    assert "asyncio.wait_for(t.pool.execute(_SQL_CAND_REFUSALS" in src
    assert "CAND_REFUSAL_WRITE_TIMEOUT_S" in src


def test_w2_a_book_row_that_cannot_be_read_back_is_named_and_the_book_is_walked_next_tick(monkeypatch):
    """P2. The book opened and its row did not come back: named
    `book_row_unreadable` (a silent exit before), one row, nothing
    planned this tick; the next tick walks the book from its row like
    any other."""
    p = _pool()
    v = _Venue()
    orig = ml._SQL_BOOK_READ
    monkeypatch.setattr(ml, "_SQL_BOOK_READ", "SELECT 1 /* ml-gone */")
    st = _tick(p, v)
    assert len(p.books) == 1 and _census(st, "book_row_unreadable") == 1 and not _places(v)
    assert [r["refusal"] for r in p.cand_refusals] == ["book_row_unreadable"]
    assert p.cand_refusals[0]["target"] == 300 and p.cand_refusals[0]["his_px"] == 0.31
    monkeypatch.setattr(ml, "_SQL_BOOK_READ", orig)
    st2 = _tick(p, v, now=NOW + 10)
    assert st2["books_live"] == 1 and _places(v) and _census(st2, "book_row_unreadable") == 0
    assert len(p.cand_refusals) == 1, "a book now: no candidate row"


def test_w2_target_zero_is_named(monkeypatch):
    """P2. ratio x net rounding to nothing left silently; it is
    `target_zero` now, counted and on the row."""
    _rails_2026_09_06(monkeypatch)
    monkeypatch.setattr(rules, "MIRROR_RATIO", 0.001)
    monkeypatch.setattr(rules, "MIRROR_SMALL_BET_USD", 0.0)
    p = _pool()
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "target_zero") == 1 and not p.books and not _places(v)
    assert [(r["refusal"], r["target"], r["his_net"]) for r in p.cand_refusals] == [("target_zero", 0, 300.0)]


def test_e7_a_no_mark_candidate_is_memoised_and_released_by_his_newer_fill():
    """E7 (the pins are test_e7_cand_memo.py's; this one keeps the
    file's every-name census whole): an OPEN empty book memoises the
    candidate (`cand_no_mark_skipped` next tick, no quote read), and a
    stamp newer than the memo's `at` -- the `last_ts` the candidate
    order ranks on -- drops it (`cand_memo_released`) and reads it."""
    from datetime import datetime, timezone

    class _Stamped(_Pool):
        stamp = None

        async def fetch(self, sql, *a):
            if "SELECT t.condition_id, max(t.ts) AS last_ts" in _flat(sql):
                return [{"condition_id": CID, "last_ts": self.stamp}]
            return await super().fetch(sql, *a)

    p = _Stamped(fills=_his(), snap={M: 300.0, N: 0.0}, snap_at=NOW - 40, ratio_fills=_ratio_fills())
    v = _Venue(bid=None, ask=None)
    st = _tick(p, v)
    assert _census(st, "no_mark") == 1 and "bbo" in _kinds(v)
    assert ml._no_mark_until == {("rn1", CID): NOW + ml.NO_MARK_TTL_S} and ml._no_mark_memo == {("rn1", CID): NOW}
    v.calls.clear()
    st2 = _tick(p, v, now=NOW + 30)
    assert _census(st2, "cand_no_mark_skipped") == 1 and "bbo" not in _kinds(v) and _census(st2, "no_mark") == 0
    p.stamp = datetime.fromtimestamp(NOW + 10, tz=timezone.utc)
    st3 = _tick(p, v, now=NOW + 60)
    assert _census(st3, "cand_memo_released") == 1 and "bbo" in _kinds(v) and _census(st3, "no_mark") == 1
    assert ml._no_mark_memo == {("rn1", CID): NOW + 60}, "read again, memoised again on this read"


def test_w2_the_walks_cap_names_every_candidate_it_left_unread(monkeypatch):
    """P2. A cap of 5 over 45 candidates: five read (each under its own
    name), forty `cand_unread_capped` rows with the tick's numbers, none
    for a market a memo or a book already skips. The census counts the
    unread ones too."""
    monkeypatch.setattr(ml, "MAX_MARKETS_PER_TICK", 5)
    conds = [f"c{i}" for i in range(45)]
    p = _pool(conds=conds)
    v = _Venue()
    st = _tick(p, v)
    assert st["capped_tick"] is True and st["reads"] == 5 and _census(st, "cand_unread_capped") == 40
    rows = p.cand_refusals
    assert len(rows) == 45
    read = [r for r in rows if r["refusal"] != "cand_unread_capped"]
    assert [r["condition_id"] for r in read] == conds[:5]
    assert all(r["refusal"] == "market_unreadable" for r in read), "no markets row for c*"
    unread = [r for r in rows if r["refusal"] == "cand_unread_capped"]
    assert [r["condition_id"] for r in unread] == conds[5:]
    assert all(r["active_conditions"] == 45 and r["cand_reads"] == 5 and r["us_slug"] is None
               and r["long_asset"] is None and r["at_ts"] == NOW for r in unread)
    assert _census(st, "market_unreadable") == 5
    # a market a memo or a book already skips is not one the cap cut
    ml._unmapped_until[("rn1", "c44")] = NOW + 900
    ml._terminal_until[("rn1", "c43")] = NOW + 900
    p.cand_refusals.clear()
    ml._cand_refusal_last.clear()
    st2 = _tick(p, v, now=NOW + 1)
    unread2 = [r["condition_id"] for r in p.cand_refusals if r["refusal"] == "cand_unread_capped"]
    assert "c44" not in unread2 and "c43" not in unread2 and len(unread2) == 38
    assert _census(st2, "cand_unread_capped") == 38 and st2["reads"] == 5
    # the soft guard's break names them the same way
    monkeypatch.setattr(ml, "MAX_MARKETS_PER_TICK", 40)
    monkeypatch.setattr(rules, "MIRROR_VENUE_CALLS_PER_TICK", 2)
    ml._cand_refusal_last.clear()
    ml._cand_cursor.clear()
    p3 = _pool(conds=conds[:10])
    st3 = _tick(p3, _Venue())
    assert _census(st3, "venue_calls_capped") == 1 and st3["capped_tick"] is True
    assert _census(st3, "cand_unread_capped") == 8 and st3["reads"] == 2
    assert [r["refusal"] for r in p3.cand_refusals].count("cand_unread_capped") == 8


def test_w2_the_candidate_walk_rotates_planned_first_woken_first_and_the_memos_still_skip(monkeypatch):
    """P3. 100 candidates, the cap 40: every candidate is walked within
    ceil(100 / 40) = 3 ticks, the cap never exceeded, a capped tick
    resuming after the last one it walked; an uncapped walk clears the
    cursor. The shadow's planned candidates (a would_side, or a target
    that is not 0, on the NEWEST row) come first, a woken market before
    them, and the memos (unmapped, terminal, a book) still skip before
    any read, spending no slot."""
    assert ml.MAX_MARKETS_PER_TICK == 40
    conds = [f"c{i}" for i in range(100)]
    p = _pool(conds=conds)
    v = _Venue()
    st1 = _tick(p, v)
    w1 = _walked(p)
    assert w1 == conds[:40] and st1["reads"] == 40 and st1["capped_tick"] is True
    assert ml._cand_cursor == {"rn1": "c39"}
    p.queries.clear()
    st2 = _tick(p, v, now=NOW + 1)
    w2 = _walked(p)
    assert w2 == conds[40:80] and st2["reads"] == 40 and ml._cand_cursor == {"rn1": "c79"}
    p.queries.clear()
    st3 = _tick(p, v, now=NOW + 2)
    w3 = _walked(p)
    assert w3 == conds[80:] + conds[:20] and st3["reads"] == 40
    assert set(w1) | set(w2) | set(w3) == set(conds), "every candidate within ceil(100/40) ticks"
    assert ml._cand_cursor == {"rn1": "c19"}
    # a walk that reaches the end clears the cursor: the next starts at the head
    p.conds = conds[:10]
    p.queries.clear()
    st4 = _tick(p, v, now=NOW + 3)
    assert _walked(p) == conds[:10] and st4.get("capped_tick") is not True and "rn1" not in ml._cand_cursor
    # the shadow's planned first: the NEWEST row decides, a judged market with no plan is not planned
    p.conds = conds
    p.shadow = [
        {"whale": "rn1", "condition_id": "c60", "at_ts": NOW - 5, "would_side": "BUY", "target": 12},
        {"whale": "rn1", "condition_id": "c70", "at_ts": NOW - 5, "would_side": None, "target": -7},
        {"whale": "rn1", "condition_id": "c80", "at_ts": NOW - 5, "would_side": None, "target": 0},
        {"whale": "rn1", "condition_id": "c60", "at_ts": NOW - 50, "would_side": None, "target": 0},
        {"whale": "rn1", "condition_id": "c85", "at_ts": NOW - 5, "would_side": "SELL", "target": 3},
        {"whale": "rn1", "condition_id": "c85", "at_ts": NOW - 2, "would_side": None, "target": 0},
    ]
    p.queries.clear()
    n_plan_reads = len([a for k, s, a in p.sent if "ml-shadow-planned" in s])
    st5 = _tick(p, v, now=NOW + 4)
    w5 = _walked(p)
    assert w5[:2] == ["c60", "c70"] and w5[2:] == conds[:38] and st5["reads"] == 40
    planned = [a for k, s, a in p.sent if "ml-shadow-planned" in s]
    assert len(planned) == n_plan_reads + 1 and planned[-1] == ("rn1", float(ms.LOOKBACK_H)), \
        "one plan read per whale per tick"
    # a woken market before the planned ones
    ml._cand_cursor.clear()
    ml.notify("c90")
    p.queries.clear()
    _tick(p, v, now=NOW + 5)
    assert _walked(p)[:3] == ["c90", "c60", "c70"]
    # the memos skip before a read and spend no slot; a market with a book is skipped too
    ml._cand_cursor.clear()
    ml._unmapped_until[("rn1", "c60")] = NOW + 900
    ml._terminal_until[("rn1", "c0")] = NOW + 900
    p.queries.clear()
    st7 = _tick(p, v, now=NOW + 6)
    w7 = _walked(p)
    assert "c60" not in w7 and "c0" not in w7 and len(w7) == 40 and st7["reads"] == 40
    assert w7[0] == "c70" and _census(st7, "cand_terminal_skipped") == 1
    # the plan read unreadable: no plan known, the rotation stands (never a stop)
    ml._cand_cursor.clear()
    ml._unmapped_until.clear()
    ml._terminal_until.clear()
    p.raise_on.append(("ml-shadow-planned", RuntimeError("db down")))
    p.queries.clear()
    st8 = _tick(p, v, now=NOW + 7)
    assert _walked(p) == conds[:40] and st8["status"] == "ok" and st8["reads"] == 40
    p.raise_on.clear()
    # the order, pure
    assert ml._candidate_order(["a", "b", "c", "d"], set(), set(), None) == ["a", "b", "c", "d"]
    assert ml._candidate_order(["a", "b", "c", "d"], set(), {"c"}, None) == ["c", "a", "b", "d"]
    assert ml._candidate_order(["a", "b", "c", "d"], set(), {"c"}, "a") == ["b", "d", "c", "a"]
    assert ml._candidate_order(["a", "b", "c", "d"], {"d"}, {"c"}, "a") == ["d", "b", "c", "a"]
    assert ml._candidate_order(["a", "b", "c", "d"], set(), set(), "d") == ["a", "b", "c", "d"]
    assert ml._candidate_order(["a", "b", "c", "d"], set(), set(), "zz") == ["a", "b", "c", "d"]
    assert ml._candidate_order([], {"d"}, {"c"}, "a") == []
    src = inspect.getsource(ml._tick)
    assert "t.cand_reads >= MAX_MARKETS_PER_TICK" in src and "ms.MAX_MARKETS_PER_TICK" not in src
    assert src.index("_unmapped_until.get((w, cid)") < src.index("_walk_candidate(t, w, cid)")
    assert src.index("_terminal_until.get((w, cid)") < src.index("_walk_candidate(t, w, cid)")


# ------- 23c. W2 review pins (the adversarial review of P1 / P2 / P3)
#
# P1's money path is the EXISTING short road entered from a long token
# the catalogue named: the pins below take that token onto the LONG road
# (his net positive with no BUY of it), behind the owner's loss stop,
# and behind admission's caps, and show nothing opens on any of them.
# P2: the cap's name never lands on a market with a book, and the rows
# stay bounded under the rotation (the brief's bound: his active
# conditions per 900 s). P3: the rotation's bound holds when memos and
# books interleave, and when the cursor candidate leaves his active
# list between ticks (the tail of a newest-first list is exactly where
# the oldest fills age out, so the cursor is lost where the starvation
# was).

def test_w2r_a_sale_of_the_short_side_token_alone_never_opens_a_long_on_the_catalogue_token(monkeypatch):
    """P1 cannot reach the LONG road. A catalogue-named long token is
    one his fills never touched, so his long from the fills is 0 by
    construction and the derived net is at most 0 (mi.net_positions
    floors a token at 0: a sale of the short-side token he bought
    before the lookback is a position of 0, not a negative one). The
    venue's reading cannot lift it either: an increase reads the
    snapshot only when the two agree, and on disagreement the short
    rule reads TOWARD zero. His only window fills SELL the short-side
    token, the venue holding the long token (first arm) or nothing
    (second): both are `target_zero` with the catalogue's long token on
    the row -- no book, no placement, never a long on a token he never
    bought."""
    _shorts_on(monkeypatch)
    _map_short_only(monkeypatch)
    sold = [_fill(N, "SELL", 400, 0.72, NOW - 2000)]
    for snap, http in (({M: 400.0, N: 0.0}, _mkt(400.0, 0.0)), ({M: 0.0, N: 0.0}, _mkt(0.0, 0.0))):
        ml._cand_refusal_last.clear()
        p = _short_side_only(fills=sold, snap=snap)
        v = _Venue()
        st = _tick(p, v, http=http)
        assert not p.books and not _places(v) and _sibling_reads(p) == [(CID, N)], snap
        assert _census(st, "long_token_unknown") == 0 and _census(st, "target_zero") == 1, st["census"]
        assert _census(st, "short_open") == 0 and _census(st, "rest_placed") == 0
        assert [(r["refusal"], r["long_asset"], r["his_net"], r["target"]) for r in p.cand_refusals] == [
            ("target_zero", M, 0.0, 0)], snap
    assert ml._his_level(sold, M, N, reducing=False, short=False) is None, "and no level for a long anyway"


def test_w2r_the_loss_stop_holds_the_short_side_only_candidate_before_any_read(monkeypatch):
    """P1 behind the owner's stop (gap_planned_unopened class L): the
    walk is refused `mirror_loss_stop` for the whale before any
    candidate -- no catalogue read, no fills read, no quote, no book,
    and no refusal row (the stop is the whale's, not the candidate's;
    the table stays silent on it, as the brief accepted)."""
    stop = float(rules.MIRROR_LOSS_STOP_USD)
    _shorts_on(monkeypatch)
    _map_short_only(monkeypatch)
    p = _short_side_only()
    _settled_book(p, -0.4 * stop, -1.1 * stop, **_ZZ)
    v = _Venue()
    st = _tick(p, v, http=_mkt(0.0, 400.0))
    assert _census(st, "mirror_loss_stop") >= 1 and not _places(v)
    assert all(b["state"] == "closed" for b in p.books.values())
    assert _sibling_reads(p) == [] and _walked(p) == [] and "bbo" not in _kinds(v)
    assert p.cand_refusals == [] and _census(st, "long_token_unknown") == 0


def test_w2r_admissions_caps_hold_the_short_side_only_candidate_by_name(monkeypatch):
    """P1 behind the existing caps: the book count (`max_books`, a
    finite cap lowered from the environment) and the clip (`clip_zero`)
    refuse the catalogue-named candidate exactly as they refuse one
    whose long token his fills named -- one sibling read, no book, the
    row under the cap's name with the catalogue's long token."""
    for arm, name in (("max", "max_books"), ("clip", "clip_zero")):
        ml._cand_refusal_last.clear()
        ml._unmapped_until.clear()
        _shorts_on(monkeypatch)
        _map_short_only(monkeypatch)
        if arm == "max":
            monkeypatch.setattr(rules, "MIRROR_MAX_LIVE_BOOKS", 0.0)
        else:
            monkeypatch.setattr(rules, "MIRROR_MAX_LIVE_BOOKS", math.inf)
            monkeypatch.setattr(le, "per_fill_usd", lambda *a, **k: 0.0)
        p = _short_side_only()
        v = _Venue()
        st = _tick(p, v, http=_mkt(0.0, 400.0))
        assert _census(st, name) == 1 and not p.books and not _places(v), (name, st["census"])
        assert _sibling_reads(p) == [(CID, N)]
        assert [(r["refusal"], r["long_asset"], r["his_net"]) for r in p.cand_refusals] == [(name, M, -400.0)]


def test_w2r_the_cap_never_names_a_market_with_a_book_as_unread(monkeypatch):
    """P2. A market with a book is walked by the book walk and skipped
    by the candidate walk without a slot; the cap's `cand_unread_capped`
    must not land on it (mutant: the books_seen clause dropped from
    _name_unread survives the builder's pins)."""
    monkeypatch.setattr(ml, "MAX_MARKETS_PER_TICK", 5)
    conds = [f"c{i}" for i in range(45)]
    p = _pool(conds=conds)
    p.token_cid.update({"tokL44": "c44", "tokO44": "c44"})
    p.add_book(ledger=300, us_market_slug="aec-atp-c44-2026-09-02", condition_id="c44",
               long_asset="tokL44", other_asset="tokO44")
    v = _Venue()
    st = _tick(p, v)
    assert st["capped_tick"] is True and st["books_live"] == 1
    unread = [r["condition_id"] for r in p.cand_refusals if r["refusal"] == "cand_unread_capped"]
    assert "c44" not in unread and len(unread) == 39 and _census(st, "cand_unread_capped") == 39
    assert all(r["condition_id"] != "c44" for r in p.cand_refusals)


def test_w2r_an_abandoned_tick_keeps_the_rotation_cursor(monkeypatch):
    """P3. A tick abandoned mid-walk (three quote misses -> no_quote)
    neither sets nor clears the cursor: the candidates it did not judge
    are read again from where the last capped tick stood (mutant: the
    `not t.abandoned` clause dropped clears it, restarting at the head)."""
    conds = [f"c{i}" for i in range(100)]
    p = _pool(conds=conds)
    st1 = _tick(p, _Venue())
    assert st1["capped_tick"] is True and ml._cand_cursor == {"rn1": "c39"}
    st2 = _tick(p, _Venue(raise_bbo=True), now=NOW + 1)
    assert st2["abandoned"] and st2["abandon_reason"] == "no_quote"
    assert ml._cand_cursor == {"rn1": "c39"}, "an abandoned walk moves nothing"
    p.queries.clear()
    st3 = _tick(p, _Venue(), now=NOW + 2)
    assert _walked(p) == conds[40:80] and st3["reads"] == 40


def test_w2r_rotation_covers_every_readable_candidate_when_memos_books_and_a_lost_cursor_interleave(monkeypatch):
    """P3's bound, on the shape the builder's fixture does not have:
    100 candidates, 34 under the unmapped memo, 2 with a book (skipped
    without a slot), 64 readable -> every readable one within
    ceil(64 / 40) = 2 ticks. Then the cursor candidate LEAVES his
    active list between ticks (its newest fill aged past the lookback:
    the tail of a newest-first list is exactly where that happens, and
    exactly where the starvation was): the walk must still resume
    where it stood, so the 24 readable candidates the capped tick left
    are read next tick, not the 40 at the head again."""
    assert ml.MAX_MARKETS_PER_TICK == 40
    conds = [f"c{i}" for i in range(100)]
    memo = {c for i, c in enumerate(conds) if i % 3 == 0}
    for c in memo:
        ml._unmapped_until[("rn1", c)] = NOW + 900
    p = _pool(conds=conds)
    for c in ("c1", "c5"):
        p.token_cid.update({f"tokL{c}": c, f"tokO{c}": c})
        p.add_book(ledger=300, us_market_slug=f"aec-atp-{c}-2026-09-02", condition_id=c,
                   long_asset=f"tokL{c}", other_asset=f"tokO{c}")
    readable = [c for c in conds if c not in memo and c not in ("c1", "c5")]
    assert len(readable) == 64

    def walked():
        # the book walk reads the two books' fills too; the candidates' reads alone
        return [c for c in _walked(p) if c not in ("c1", "c5")]
    v = _Venue()
    st1 = _tick(p, v)
    w1 = walked()
    assert w1 == readable[:40] and st1["capped_tick"] is True and st1["books_live"] == 2
    assert ml._cand_cursor == {"rn1": readable[39]}
    p.queries.clear()
    st2 = _tick(p, v, now=NOW + 1)
    w2 = walked()
    assert w2[:24] == readable[40:] and len(w2) == 40 and st2["capped_tick"] is True
    assert set(w1) | set(w2) == set(readable), "every readable candidate within ceil(64/40) ticks"
    # the cursor candidate leaves the list between ticks: the rotation
    # resumes where it stood, the 24 left unread are read first
    ml._cand_cursor.clear()
    p.queries.clear()
    _tick(p, v, now=NOW + 2)
    cursor = ml._cand_cursor["rn1"]
    assert cursor == readable[39]
    left = readable[40:]
    p.conds = [c for c in conds if c != cursor]
    p.queries.clear()
    _tick(p, v, now=NOW + 3)
    w4 = walked()
    assert len(w4) == 40 and set(left) <= set(w4), \
        "the tail the capped tick left is read next tick even though the cursor candidate is gone"


def test_w2r_the_refusal_rows_stay_bounded_when_the_cap_rotates_the_walk(monkeypatch):
    """P2 under P3. With the cap rotating, a candidate is read one tick
    and left unread the next; if the alternation is a transition each
    way the table takes ~2 x cap rows EVERY tick, with no 900 s bound
    (600 conditions at 100 s ticks: ~69k rows a day, and every
    market's path on the preset reads `x@t capped@t+1 x@t+2 ...`).
    The brief's bound is his active conditions per 900 s; the pin
    allows twice that (a first-tick name and one more per pair)."""
    conds = [f"c{i}" for i in range(100)]
    p = _pool(conds=conds)
    v = _Venue()
    for k in range(6):
        _tick(p, v, now=NOW + 30 * k)
    n = len(p.cand_refusals)
    assert n <= 2 * len(conds), f"{n} rows for 100 candidates inside 900 s"
    # the pins the rule must keep: a real name after the cap's is a
    # row, the cap's name after a real one inside 900 s is not
    by = {}
    for r in p.cand_refusals:
        by.setdefault(r["condition_id"], []).append(r["refusal"])
    assert by["c0"][0] == "market_unreadable" and by["c40"][0] == "cand_unread_capped"
    assert "market_unreadable" in by["c40"], "the read that followed the cap is a transition"


def test_e5_the_frozen_exit_and_register_names_are_emitted_here_too(monkeypatch, caplog):
    """E5's names are driven in tests/test_e5_frozen_exits.py (the pool
    there answers the register, co-hold and receipt statements this
    file's fake leaves unmodelled -- against this fake a frozen exit
    refuses `frozen_venue_unread` by name, which is why every older
    frozen pin above still holds). Run here as well so the coverage read
    below sees them when this file runs alone."""
    from tests import test_e5_frozen_exits as e5
    e5.test_e5_a_frozen_long_book_sells_toward_his_exit_sized_on_the_venue_and_names_the_excess()
    e5.test_e5_the_reduce_is_sized_on_the_venue_not_the_ledger_when_the_venue_holds_less()
    e5.test_e5_the_frozen_exit_is_read_only_when_the_venue_or_his_market_read_is_missing()
    e5.test_e5_a_co_held_slug_refuses_the_frozen_exit_by_name()
    e5.test_e5_a_register_row_against_the_books_leg_is_not_read_and_the_book_stays_frozen()
    e5.test_e5_an_unreadable_register_explains_nothing_is_counted_and_logged_once(caplog)
    e5.test_e5_a_placement_lost_book_sells_no_more_than_its_ledger_plus_its_lost_rows_ask_for()
    e5.test_e5_a_live_book_with_a_register_row_reads_it_as_manual_in_its_seat_and_never_increases()
    from tests import test_e5_review_pins as e5r
    e5r.test_e5r_F2_a_standing_frozen_reduce_partly_filled_after_the_walk_is_not_re_sized_past_the_venue()
    # last, the two that monkeypatch for the rest of this test: mi.plan
    # into a BUY, then the knob off
    e5.test_e5_a_frozen_book_never_places_a_buy_that_increases_and_a_non_reduce_plan_is_refused_by_name(monkeypatch)
    e5.test_e5_the_knob_only_lowers_the_rail_off_is_read_only_and_the_default_is_on(monkeypatch)
    for k in ("frozen_reduce", "frozen_exits_off", "frozen_venue_unread", "frozen_coheld",
              "frozen_venue_flat", "frozen_no_his_exit", "frozen_reduce_only", "frozen_excess_sold",
              "registered_books", "registered_sign_refused", "registered_unreadable",
              "frozen_fill_this_tick", "frozen_venue_unexplained", "registered_no_increase"):
        assert k in SEEN, k


def test_e9_the_fast_path_names_are_emitted_here_too(monkeypatch, caplog):
    """E9's four names are driven in tests/test_e9_fast_path.py; run
    here as well so the coverage read below sees them when this file
    runs alone (E7's convention)."""
    from tests import test_e9_fast_path as e9
    e9.test_e9_the_fast_tick_reads_and_places_the_woken_market_inside_the_floor_and_touches_no_other_book(monkeypatch)
    e9.test_e9_the_budget_is_shared_with_the_full_tick()
    e9.test_e9_a_failing_fast_tick_names_fast_tick_failed_and_the_full_tick_reads_the_market(caplog)
    for k in ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed"):
        assert k in SEEN, k


def test_e12_the_flow_names_are_emitted_here_too(monkeypatch, caplog):
    """E12's three names are driven in tests/test_e12_flow_only.py; run
    here as well so the coverage read below sees them when this file
    runs alone (E9's convention)."""
    from tests import test_e12_flow_only as e12
    e12.test_e12_every_name_is_emitted_here(monkeypatch, caplog)
    for k in ("open_flow_only", "open_catchup", "flow_guard_unreadable"):
        assert k in SEEN, k


def test_e13_the_venue_close_name_is_emitted_here_too():
    """E13's one name is driven in tests/test_e13_venue_close.py; run
    here as well so the coverage read below sees it when this file runs
    alone (E12's convention)."""
    from tests import test_e13_venue_close as e13
    e13.test_e13_a_live_flat_book_closes_cancelled_on_the_second_terminal_read_a_ttl_apart_never_on_one()
    assert "venue_market_ended" in SEEN


def test_e18_the_rest_life_names_are_emitted_here_too(monkeypatch, caplog):
    """E18's six names are driven in tests/test_e18_rest_life.py; run
    here as well so the coverage read below sees them when this file
    runs alone (E13's convention)."""
    from tests import test_e18_rest_life as e18
    e18.test_e18_every_name_is_emitted_here(monkeypatch, caplog)
    for k in e18.NEW_NAMES:
        assert k in SEEN, k


def test_e16_the_freeze_names_are_emitted_here_too(monkeypatch):
    """E16's four names are driven in tests/test_e16_freeze_two_reads.py;
    run here as well so the coverage read below sees them when this
    file runs alone (E13's convention)."""
    from tests import test_e16_freeze_two_reads as e16
    e16.test_e16_one_disagreeing_read_is_a_suspect_no_freeze_no_increase_the_exit_still_plans()
    e16.test_e16_frozen_plus_his_witnessed_sale_with_the_walk_unread_reduces_on_the_fills_net_at_his_price(monkeypatch)
    e16.test_e16_a_held_venue_ledger_disagree_book_that_agrees_stays_frozen_with_the_switch_off(monkeypatch)
    for k in ("venue_ledger_suspect", "venue_suspect_hold", "frozen_reduce_on_fill", "thaw_held"):
        assert k in SEEN, k


def test_e17_the_reanchor_and_adoption_names_are_emitted_here_too(monkeypatch):
    """E17's seven names are driven in tests/test_e17_standing_reanchor.py;
    run here as well so the coverage read below sees them when this file
    runs alone (E13's convention)."""
    from tests import test_e17_standing_reanchor as e17
    e17.test_e17_a_settled_row_without_the_venues_marks_on_a_live_open_market_reanchors_and_the_book_lives()
    e17.test_e17_a_reader_that_cannot_tell_closes_as_before_and_is_named_ambiguous("venue_halted")
    e17.test_e17_the_reanchor_write_touching_no_row_is_named_and_the_close_stands()
    e17.test_e17_204s_shape_the_closed_books_13_are_adopted_and_episode_2_sizes_his_flow_since_the_close(monkeypatch)
    e17.test_e17_his_fills_before_the_close_never_size_the_reopen(monkeypatch)
    e17.test_e17_the_priors_row_carrying_the_venues_settle_is_refused_by_name_nothing_reopens(monkeypatch)
    e17.test_e17_a_prior_that_cannot_be_read_refuses_by_name(monkeypatch, "no_close_clock")
    # the fold (2026-09-08, review HIGH-1): the dust event, driven by the
    # review pin the fold inverted
    from tests import test_pnl_l5_review_pins as l5r
    l5r.test_r6_DEFECT_sub_share_venue_dust_after_a_flip_close_is_refused_forever(monkeypatch)
    for name in ("standing_row_reanchored", "standing_row_ambiguous", "standing_row_reanchor_failed",
                 "adopted_prior_episode", "venue_dust_ours", "adopt_prior_unreadable",
                 "adopt_no_fill_since_close", "adopt_prior_venue_settled"):
        assert name in SEEN, name


def test_e19_the_smaller_reading_open_name_is_emitted_here_too(monkeypatch):
    """E19's one name is driven in tests/test_e19_smaller_reading.py; run
    here as well so the coverage read below sees it when this file runs
    alone (E13's convention)."""
    from tests import test_e19_smaller_reading as e19
    e19.test_e19_martinez_shape_a_fresh_read_past_the_max_of_one_sign_opens_on_the_smaller_reading(monkeypatch)
    assert "drift_smaller_open" in SEEN


def test_e14_the_take_in_band_name_is_emitted_here_too(monkeypatch):
    """E14's one name (FILL lane 2) is driven in tests/test_e14_take_band.py;
    run here as well so the coverage read below sees it when this file
    runs alone (E13's convention). The band is ON for it (the fixture
    world's rail above holds it at 0)."""
    from tests import test_e14_take_band as e14
    e14.test_e14_every_name_is_emitted_here(monkeypatch)
    assert "take_in_band" in SEEN


def test_fill_x1_the_exit_band_names_are_emitted_here_too(monkeypatch):
    """FILL lane 3's three names are driven in tests/test_fill_x1_exit_band.py
    (the band at a monkeypatched 0.02 for the two band words -- at the code
    default they can never fire -- and the fast gate's count on his
    reducing fill); run here as well so the coverage read below sees them
    when this file runs alone (E13's convention)."""
    from tests import test_fill_x1_exit_band as x1
    x1.test_x1_every_name_is_emitted_here(monkeypatch)
    for k in ("exit_take_in_band", "cover_in_band", "order_open_his_exit"):
        assert k in SEEN, k
def test_t2_the_fill_answers_names_are_emitted_here_too(caplog):
    """T2's two names (FILL lane 4) are driven in tests/test_fill_t2_record.py;
    run here as well so the coverage read below sees them when this file
    runs alone (E13's convention)."""
    from tests import test_fill_t2_record as t2
    t2.test_t2_every_name_is_emitted_here(caplog)
    for name in ("fill_answer_write_failed", "fill_answers_absent"):
        assert name in SEEN, name
def test_l7_event_stale_is_emitted_on_a_dated_candidate_with_no_fill_of_his_in_a_day():
    """L7 (2026-09-08): a candidate whose slug's own date is more than one
    day past with no fill of his in a day is refused `event_stale` --
    the terminal memo, no venue read. The rule is pinned tick by tick in
    test_c8_tennis_witness; this emits the name for the coverage read
    below (his slug's shape verbatim, cand_refusals_1158 row 686, its
    date three days before the tick's clock)."""
    day = time.strftime("%Y-%m-%d", time.gmtime(NOW - 3 * 86400))
    slug = f"atp-gea-zandsch-{day}"
    p = _pool(fills=[_fill(M, "BUY", 300.0, 0.31, NOW - 3 * 86400, market_slug=slug, event_slug=slug)])
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "event_stale") == 1 and [c for c in v.calls if c[0] == "bbo"] == [] and not p.books
    assert ml._terminal_until.pop(("rn1", CID)) == NOW + ms.UNMAPPED_TTL_S
    # the fold (2026-09-08, review HIGH-1): the read's `at` beside the memo
    assert ml._event_stale_memo.pop(("rn1", CID)) == NOW
    assert [r["refusal"] for r in p.cand_refusals] == ["event_stale"]
    assert "event_stale" in SEEN


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
