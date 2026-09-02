"""MIRROR LIVE, PHASE P1, step 5: the ledger primitives (owner order
2026-09-02, "go for it, let's get this working"). No database: a fake
pool APPLIES each statement's arithmetic to a standing row it holds --
a model of the SQL, checked against the SQL's own text -- so a replay,
a flat-then-rebuy, an overfill and a crash mid-transaction can be
driven and their outcomes read back. test_mirror_shadow's _Pool is
extended with acquire()/transaction() context managers that stage
writes and commit only on a clean exit (the addendum's rule for the
one multi-statement open).

What is pinned and why (P1 spec sections 1b, 1c, 1e and 8.5):

  * the open is ONE transaction -- book row, standing row, back-fill --
    and any raise inside it leaves nothing behind, named
  * the standing INSERT satisfies every 007 NOT NULL, sets venue, lane,
    status 'filled' and raw.preview.intent, and its `error` stays NULL
  * the BUY is _merge_add_leg's standing half to the character and is
    idempotent on (order_id, seq); its raw.adds carry exactly the keys
    _caps_room's leg clause reads
  * the SELL is mirror_exit's partial statement with the lane guard,
    never cashes out, never goes negative, books an overfill down to
    the ledger and flags it
  * the close moves the row by gross buys only, and only when flat
  * no mirror write can set 'exiting', 'merged' or 'submitting', and
    every write carries the lane guard
"""
import asyncio
import inspect
import json
import pathlib
import re
import time

import pytest

from sportsassets import live_executor as le
from sportsassets.scripts import migrate
from tests.test_mirror_live_migration import _table_columns
from tests.test_mirror_shadow import _Pool as _ShadowPool

MIG = pathlib.Path(migrate.MIGRATIONS_DIR)
SRC = pathlib.Path(le.__file__).resolve().parent
SLUG = "aec-atp-branak-alemic-2026-09-02"
INTENT = "ORDER_INTENT_BUY_LONG"


def _flat(s: str) -> str:
    """One space between tokens, SQL comments dropped first so a
    commented statement still reads (and parses) as one line."""
    return " ".join("\n".join(ln.split("--", 1)[0] for ln in s.splitlines()).split())


def _run(coro):
    return asyncio.run(coro)


class _Unique(Exception):
    """asyncpg's UniqueViolationError as the reaper tests fake it: the
    constraint is in the message."""


_Unique.__name__ = "UniqueViolationError"


def _row(**over) -> dict:
    """A standing row as the open INSERT writes it."""
    r = {"id": 900, "status": "filled", "lane": "mirror", "whale_username": "rn1",
         "asset": "tok-long", "condition_id": "0xcond", "us_market_slug": SLUG,
         "his_price": 0.31, "limit_price": 0.31, "requested_usd": 0.0,
         "requested_shares": 30.0, "filled_shares": 0.0, "fill_price": None,
         "filled_usd": 0.0, "orig_shares": 0.0, "pnl": None, "error": None,
         "settled_at": None,
         "raw": {"lane": "mirror", "preview": {"intent": INTENT},
                 "mirror": {"book_id": 41, "episode": 1}, "adds": []}}
    r.update(over)
    return r


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.pool.tx_events.append("begin")
        return self

    async def __aexit__(self, et, ev, tb):
        pool = self.conn.pool
        if et is None:
            pool.tx_events.append("commit")
            for s, a in self.conn.stage:
                pool._commit(s, a)
        else:
            pool.tx_events.append("rollback")
        self.conn.stage = []
        return False


class _Conn:
    """One acquired connection: statements are STAGED and land on the
    pool only when the transaction exits cleanly; a statement naming a
    `raise_on` needle raises, which rolls the stage back."""

    def __init__(self, pool):
        self.pool, self.stage = pool, []

    def transaction(self):
        return _Tx(self)

    def _send(self, sql, *a):
        s = _flat(sql)
        self.pool.sent.append(("tx", s, a))
        for needle, exc in self.pool.raise_on:
            if needle in s:
                raise exc
        self.stage.append((s, a))
        return s

    async def fetchrow(self, sql, *a):
        s = self._send(sql, *a)
        if "INSERT INTO mirror_books" in s:
            return {"id": self.pool.next_book_id, "episode": self.pool.episode}
        return None

    async def fetchval(self, sql, *a):
        s = self._send(sql, *a)
        if "INSERT INTO live_orders" in s:
            return self.pool.next_row_id
        return None

    async def execute(self, sql, *a):
        self._send(sql, *a)
        return "UPDATE 1"


class _Acquire:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        self.pool.acquired += 1
        return _Conn(self.pool)

    async def __aexit__(self, *exc):
        self.pool.released += 1
        return False


class _Ledger(_ShadowPool):
    """test_mirror_shadow's pool, extended: standing rows the primitives'
    statements are APPLIED to (each statement's arithmetic modelled so
    a replay can be read back), plus acquire()/transaction() for the
    one multi-statement open. `raise_on` is [(sql needle, exception)]."""

    def __init__(self, row=None, raise_on=(), next_book_id=41, next_row_id=900,
                 episode=1):
        super().__init__()
        self.rows = {} if row is None else {row["id"]: row}
        self.books = {}
        self.raise_on = list(raise_on)
        self.next_book_id, self.next_row_id, self.episode = next_book_id, next_row_id, episode
        self.committed, self.tx_events, self.sent = [], [], []
        self.acquired = self.released = 0

    def acquire(self):
        return _Acquire(self)

    def _commit(self, s, a):
        self.committed.append((s, a))
        if "INSERT INTO mirror_books" in s:
            self.books[self.next_book_id] = {
                "whale": a[0], "condition_id": a[1], "us_market_slug": a[2],
                "game_key": a[3], "long_asset": a[4], "other_asset": a[5],
                "intent": a[6], "map_source": a[7], "ratio": a[8], "anchor_usd": a[9],
                "his_level": a[10], "target": a[11], "episode": self.episode,
                "standing_row_id": None}
        elif "INSERT INTO live_orders" in s:
            self.rows[self.next_row_id] = _row(
                id=self.next_row_id, whale_username=a[0], asset=a[1], condition_id=a[2],
                us_market_slug=a[3], his_price=a[4], limit_price=a[4],
                requested_shares=float(a[5]), raw=json.loads(a[6]))
        elif "SET standing_row_id = $2" in s:
            self.books[a[0]]["standing_row_id"] = a[1]

    # -- the statements, applied ---------------------------------------
    def _live(self, rid):
        r = self.rows.get(rid)
        if r is None or r["status"] != "filled" or r["lane"] != "mirror":
            return None
        return r

    def _apply_buy(self, a):
        rid, q, px, usd, wire_usd, adds_json, oid, seq = a
        r = self._live(rid)
        if r is None:
            return None
        adds = list(r["raw"].get("adds") or [])
        # jsonb containment: some element carries both keys at these values
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

    def _apply_sell(self, a):
        rid, booked, pnl = a
        r = self._live(rid)
        if r is None:
            return None
        if r["orig_shares"] is None:
            r["orig_shares"] = float(r["filled_shares"])
        r["filled_shares"] = max(float(r["filled_shares"]) - booked, 0.0)
        r["pnl"] = (r["pnl"] or 0.0) + pnl
        return {"filled_shares": r["filled_shares"], "pnl": r["pnl"]}

    def _apply_close(self, a, status):
        r = self._live(a[0])
        if r is None or float(r["filled_shares"]) != 0.0:
            return None
        r["status"] = status
        if status == "cashed_out":
            r["pnl"] = (r["pnl"] or 0.0) + a[1]
            r["settled_at"] = time.time()
        else:
            r["error"] = a[1]
        return {"id": a[0]}

    async def fetchrow(self, sql, *a):
        s = _flat(sql)
        self.sent.append(("fetchrow", s, a))
        for needle, exc in self.raise_on:
            if needle in s:
                raise exc
        if "/* mirror-sell */" in s:
            r = self._live(a[0])
            return None if r is None else {"fill_price": r["fill_price"],
                                           "filled_shares": r["filled_shares"]}
        if "SET fill_price = CASE" in s:
            return self._apply_buy(a)
        if "GREATEST(filled_shares - $2::float8, 0)" in s:
            return self._apply_sell(a)
        if "SET status='cashed_out'" in s:
            return self._apply_close(a, "cashed_out")
        if "SET status='cancelled'" in s:
            return self._apply_close(a, "cancelled")
        return await super().fetchrow(sql, *a)

    async def execute(self, sql, *a):
        self.sent.append(("execute", _flat(sql), a))
        return await super().execute(sql, *a)

    def statements(self, kind=None):
        return [s for k, s, _ in self.sent if kind is None or k == kind]


OPEN_KW = dict(whale="RN1", cid="0xcond", slug=SLUG, long_asset="tok-long",
               other_asset="tok-other", ratio=0.058, anchor_usd=50.0, his_level=0.31,
               target=30, map_source="ledger", game_key="atp-branak-alemic-2026-09-02")


def _open(pool, **over):
    return _run(le._open_mirror_book(pool, **{**OPEN_KW, **over}))


def _buy(pool, oid, seq, shares, px, wire=None, rid=900, his=0.31, maker=True):
    usd = le.fill_cash(shares, px, INTENT)
    return _run(le._book_mirror_buy(pool, rid, oid, seq, shares, px, usd,
                                    shares * (wire if wire is not None else px), his, maker))


def _sell(pool, shares, px, ledger, rid=900):
    return _run(le._book_mirror_sell(pool, rid, shares, px, ledger))


def _close(pool, gross, rid=900):
    return _run(le._close_mirror_episode(pool, rid, gross))


def _booked(pool=None):
    """A pool whose book has bought 10 @0.30 and 10 @0.40 (average 0.35)."""
    p = pool or _Ledger()
    assert _open(p)["ok"]
    assert _buy(p, "o1", 0, 10, 0.30) is not None
    assert _buy(p, "o2", 0, 10, 0.40) is not None
    return p


# ------------------------------------------------------------------ open

def test_open_is_one_transaction_book_then_standing_row_then_backfill():
    p = _Ledger()
    out = _open(p)
    assert out == {"ok": True, "book_id": 41, "standing_row_id": 900, "refusal": None}
    assert (p.acquired, p.released, p.tx_events) == (1, 1, ["begin", "commit"])
    heads = [s.split(" (")[0] for s, _ in p.committed]
    assert heads == ["INSERT INTO mirror_books", "INSERT INTO live_orders",
                     "UPDATE mirror_books SET standing_row_id = $2, updated_at = now() "
                     "WHERE id = $1 AND standing_row_id IS NULL"]
    assert p.books[41]["standing_row_id"] == 900
    # nothing went through the pool outside the transaction
    assert p.writes == [] and p.statements("fetchrow") == [] and p.statements("execute") == []
    row = p.rows[900]
    # the standing row, section 1b to the letter
    assert row["status"] == "filled" and row["lane"] == "mirror"
    assert row["whale_username"] == "rn1"                       # lower-cased
    assert row["asset"] == "tok-long" and row["condition_id"] == "0xcond"
    assert row["us_market_slug"] == SLUG
    assert row["his_price"] == 0.31 == row["limit_price"]
    assert row["requested_shares"] == 30.0 and row["filled_shares"] == 0.0
    assert row["fill_price"] is None and row["pnl"] is None and row["error"] is None
    assert row["raw"] == {
        "lane": "mirror", "preview": {"intent": INTENT},
        "mirror": {"book_id": 41, "episode": 1, "ratio": 0.058,
                   "opened_at": row["raw"]["mirror"]["opened_at"]},
        "adds": []}
    assert abs(row["raw"]["mirror"]["opened_at"] - time.time()) < 5
    # the book row: lower-cased whale, the market, the long token, fixed ratio
    b = p.books[41]
    assert (b["whale"], b["us_market_slug"], b["long_asset"], b["other_asset"]) == \
        ("rn1", SLUG, "tok-long", "tok-other")
    assert (b["intent"], b["map_source"], b["ratio"], b["anchor_usd"], b["his_level"],
            b["target"]) == (INTENT, "ledger", 0.058, 50.0, 0.31, 30)


def test_the_standing_insert_names_every_007_not_null_and_the_claim_columns():
    """Parsed from the migrations, not from memory: every NOT NULL without
    a default in 007 is a column the INSERT binds to a non-NULL value;
    venue (008), lane (041), status and side are literals."""
    cols007 = _table_columns(MIG.joinpath("007_live_orders.sql").read_text(), "live_orders")
    required = {n for n, d in cols007.items()
                if "NOT NULL" in d and "DEFAULT" not in d and "PRIMARY KEY" not in d}
    assert required == {"asset", "side", "his_price", "limit_price", "requested_usd",
                        "requested_shares"}
    p = _Ledger()
    assert _open(p)["ok"]
    (s, a), = [(s, a) for s, a in p.committed if s.startswith("INSERT INTO live_orders")]
    m = re.fullmatch(r"INSERT INTO live_orders \((.*?)\) VALUES \((.*?)\) RETURNING id", s)
    assert m, s
    cols = [c.strip() for c in m.group(1).split(",")]
    vals = [v.strip() for v in m.group(2).split(",")]
    assert len(cols) == len(vals)
    bound = dict(zip(cols, vals))
    assert required <= set(cols), required - set(cols)
    for c in required:
        v = bound[c]
        assert v != "NULL", c
        if v.startswith("$"):
            assert a[int(v[1:].split("::")[0]) - 1] is not None, c
    assert bound["status"] == "'filled'" and bound["lane"] == "'mirror'"
    assert bound["venue"] == "'polymarket-us'" and bound["side"] == "'BUY'"
    assert bound["trade_id"] == "NULL"                          # joins on trades drop it
    assert bound["his_price"] == bound["limit_price"]           # his level at open, both
    assert bound["requested_usd"] == "0" and bound["filled_shares"] == "0"
    assert bound["filled_usd"] == "0" and bound["orig_shares"] == "0"
    assert bound["fill_price"] == "NULL" and bound["pnl"] == "NULL" and bound["error"] == "NULL"
    assert bound["raw"] == "$7::jsonb"
    # the statuses the row will ever carry exist in the live constraint (045)
    chk = re.search(r"live_orders_status_check\s+CHECK \(status IN \((.*?)\)\)",
                    MIG.joinpath("045_add_legs.sql").read_text(), re.S).group(1)
    allowed = {v.strip().strip("'") for v in chk.split(",")}
    assert {"filled", "cashed_out", "cancelled"} <= allowed


def test_open_numbers_the_episode_from_the_markets_earlier_books():
    sql = _flat(le._MIRROR_BOOK_INSERT_SQL)
    assert ("COALESCE((SELECT max(b.episode) FROM mirror_books b WHERE b.whale = $1 "
            "AND b.us_market_slug = $3), 0) + 1") in sql
    p = _Ledger(episode=3)
    assert _open(p)["ok"]
    assert p.rows[900]["raw"]["mirror"]["episode"] == 3


def test_open_refuses_by_name_and_rolls_the_book_back():
    dup = 'duplicate key value violates unique constraint "%s"'
    # the asset claim (045) on the standing row: no book survives it
    p = _Ledger(raise_on=[("INSERT INTO live_orders", _Unique(dup % "live_orders_one_fill_per_asset"))])
    out = _open(p)
    assert out == {"ok": False, "book_id": None, "standing_row_id": None,
                   "refusal": "asset_claimed"}
    assert p.committed == [] and p.books == {} and p.rows == {}
    assert p.tx_events == ["begin", "rollback"] and p.released == 1
    # the mirror_books INSERT was sent, then undone
    assert [s for k, s, _ in p.sent if s.startswith("INSERT INTO mirror_books")]
    # a second open book on the market (047)
    p = _Ledger(raise_on=[("INSERT INTO mirror_books",
                           _Unique(dup % "mirror_books_one_open_per_market"))])
    assert _open(p)["refusal"] == "book_exists" and p.committed == []
    # asyncpg's own shape: the constraint on the attribute, the message bare
    class _Named(Exception):
        constraint_name = "live_orders_one_fill_per_asset"
    p = _Ledger(raise_on=[("INSERT INTO live_orders", _Named("dup"))])
    assert _open(p)["refusal"] == "asset_claimed" and p.committed == []


def test_a_crash_between_the_two_inserts_or_before_the_backfill_writes_nothing():
    for needle in ("INSERT INTO live_orders", "SET standing_row_id = $2"):
        p = _Ledger(raise_on=[(needle, RuntimeError("connection lost"))])
        out = _open(p)
        assert out["refusal"] == "open_failed:RuntimeError" and out["ok"] is False
        assert out["book_id"] is None and out["standing_row_id"] is None
        assert p.committed == [] and p.books == {} and p.rows == {}
        assert p.tx_events == ["begin", "rollback"]
    # an unreadable pool: acquire itself raises
    class _Dead(_Ledger):
        def acquire(self):
            raise ConnectionError("pool closed")
    out = _open(_Dead())
    assert out["refusal"] == "open_failed:ConnectionError" and out["ok"] is False


def test_open_refuses_before_touching_the_pool_on_unusable_inputs():
    cases = [({"intent": "ORDER_INTENT_BUY_SHORT"}, "short_side_refused"),
             ({"his_level": None}, "open_failed:TypeError"),
             ({"his_level": 1.2}, "open_failed:ValueError"),
             ({"his_level": 0.0}, "open_failed:ValueError"),
             ({"whale": ""}, "open_failed:ValueError"),
             ({"long_asset": None}, "open_failed:ValueError"),
             ({"slug": " "}, "open_failed:ValueError"),
             ({"target": -1}, "open_failed:ValueError"),
             ({"target": "thirty"}, "open_failed:ValueError")]
    for over, refusal in cases:
        p = _Ledger()
        out = _open(p, **over)
        assert out["refusal"] == refusal and out["ok"] is False, over
        assert p.acquired == 0 and p.sent == [] and p.committed == [], over
    assert le.MIRROR_INTENT == INTENT


# ------------------------------------------------------------------- buy

def test_buy_is_idempotent_on_order_id_and_seq():
    p = _Ledger()
    assert _open(p)["ok"]
    first = _buy(p, "o1", 0, 10, 0.30)
    assert first == {"filled_shares": 10.0, "fill_price": 0.30, "filled_usd": 3.0}
    # the same fill replayed -- a lost reply, a second pass -- books nothing
    assert _buy(p, "o1", 0, 10, 0.30) is None
    row = p.rows[900]
    assert row["filled_shares"] == 10.0 and len(row["raw"]["adds"]) == 1
    assert row["filled_usd"] == 3.0 and row["orig_shares"] == 10.0
    # the next partial on the same order is a new seq and books
    assert _buy(p, "o1", 1, 5, 0.32) is not None
    assert p.rows[900]["filled_shares"] == 15.0 and len(p.rows[900]["raw"]["adds"]) == 2
    # the predicate is on the row, not in Python: (order_id, seq) as jsonb containment
    s = _flat(le._MIRROR_BUY_SQL)
    assert ("AND NOT (COALESCE(raw->'adds', '[]'::jsonb) @> jsonb_build_array("
            "jsonb_build_object('order_id', $7::text, 'seq', $8::int)))") in s
    _, args = [(s, a) for k, s, a in p.sent if "SET fill_price = CASE" in s][0]
    assert args[6] == "o1" and args[7] == 0
    assert json.loads(args[5])[0]["order_id"] == "o1" and json.loads(args[5])[0]["seq"] == 0


def test_buy_weighted_average_including_flat_then_rebuy():
    p = _Ledger()
    assert _open(p)["ok"]
    assert p.rows[900]["fill_price"] is None
    assert _buy(p, "o1", 0, 10, 0.30)["fill_price"] == pytest.approx(0.30)
    assert _buy(p, "o2", 0, 10, 0.40)["fill_price"] == pytest.approx(0.35)
    assert _buy(p, "o3", 0, 20, 0.50)["fill_price"] == pytest.approx(0.425)
    # sold to zero on a live market: the row stays 'filled' at 0 and keeps
    # its last average
    out = _sell(p, 40, 0.45, 40)
    assert out["booked"] == 40.0 and p.rows[900]["filled_shares"] == 0.0
    assert p.rows[900]["status"] == "filled" and p.rows[900]["fill_price"] == pytest.approx(0.425)
    # a re-buy onto the flat row starts the average afresh: 0 x stale + px x q / q
    assert _buy(p, "o4", 0, 4, 0.20)["fill_price"] == pytest.approx(0.20)
    # and the same with a NULL fill_price on the flat row (the open's state)
    _sell(p, 4, 0.25, 4)
    assert p.rows[900]["filled_shares"] == 0.0
    p.rows[900]["fill_price"] = None
    assert _buy(p, "o5", 0, 6, 0.60)["fill_price"] == pytest.approx(0.60)


def test_buy_grows_orig_shares_requested_usd_and_filled_usd():
    p = _Ledger()
    assert _open(p)["ok"]
    _buy(p, "o1", 0, 10, 0.30, wire=0.31)
    _buy(p, "o2", 0, 10, 0.40, wire=0.41)
    row = p.rows[900]
    assert row["orig_shares"] == 20.0
    assert row["filled_usd"] == pytest.approx(3.0 + 4.0)          # fill_cash, BUY_LONG
    assert row["requested_usd"] == pytest.approx(3.1 + 4.1)       # shares x wire
    # a sale never shrinks orig_shares; the next buy grows it from there
    _sell(p, 5, 0.50, 20)
    assert row["orig_shares"] == 20.0 and row["filled_shares"] == 15.0
    _buy(p, "o3", 0, 5, 0.45)
    assert row["orig_shares"] == 25.0 and row["filled_shares"] == 20.0
    # the fill's own cash and the wire cash are the statement's $4 and $5
    _, a = [(s, a) for k, s, a in p.sent if "SET fill_price = CASE" in s][0]
    assert a[3] == pytest.approx(3.0) and a[4] == pytest.approx(3.1)
    assert le.fill_cash(10, 0.30, INTENT) == pytest.approx(3.0)


def test_buy_statement_is_merge_add_legs_standing_half_to_the_character():
    """The arithmetic is not re-derived: the SET clause and the guard are
    the standing-row half of _merge_add_leg with the parameters
    renumbered one to one."""
    merge = _flat(inspect.getsource(le._merge_add_leg))
    mine = _flat(le._MIRROR_BUY_SQL)
    m0, m1 = merge.index("SET fill_price = CASE"), merge.index("WHERE id = $2 AND status = 'filled'")
    b0, b1 = mine.index("SET fill_price = CASE"), mine.index("WHERE id = $1 AND status = 'filled'")
    seg_merge, seg_mine = merge[m0:m1], mine[b0:b1]
    assert re.sub(r"\$\d+", "$_", seg_merge) == re.sub(r"\$\d+", "$_", seg_mine)
    pairs = list(zip(re.findall(r"\$\d+", seg_merge), re.findall(r"\$\d+", seg_mine)))
    mapping = {}
    for a, b in pairs:
        assert mapping.setdefault(a, b) == b, (a, b)     # one merge param -> one of ours
    assert len(set(mapping.values())) == len(mapping)     # and never two -> one
    assert mapping == {"$3": "$2", "$4": "$3", "$5": "$4", "$6": "$5", "$7": "$6"}
    # the guard: the row, 'filled', AND the lane
    assert "WHERE id = $1 AND status = 'filled' AND lane = 'mirror'" in mine


def test_buy_adds_carry_exactly_the_keys_caps_rooms_leg_clause_reads():
    p = _Ledger()
    assert _open(p)["ok"]
    _buy(p, "o1", 3, 10, 0.30, wire=0.31, his=0.305, maker=False)
    add, = p.rows[900]["raw"]["adds"]
    assert set(add) == {"order_id", "seq", "ts", "shares", "price", "usd", "his_price",
                        "lane", "maker"}
    assert add["order_id"] == "o1" and add["seq"] == 3 and add["lane"] == "mirror"
    assert add["shares"] == 10.0 and add["price"] == 0.30 and add["usd"] == pytest.approx(3.0)
    assert add["his_price"] == 0.305 and add["maker"] is False
    # _caps_room sums (a->>'usd') for legs whose (a->>'ts') is inside the
    # day window, in EPOCH SECONDS, on rows the window would otherwise
    # miss; a mirror whale is not one of the excluded sleeves
    src = inspect.getsource(le._caps_room)
    assert "sum((a->>'usd')::float8)" in src
    assert "(a->>'ts')::float8 > extract(epoch FROM now() - interval '24 hours')" in src
    assert "jsonb_array_elements(COALESCE(lo2.raw->'adds', '[]'::jsonb)) a" in src
    assert "COALESCE(lo2.whale_username, '') NOT IN ('manual', 'underdog')" in src
    assert abs(add["ts"] - time.time()) < 5
    # adds_census sums raw.adds only over 'merged' rows: a mirror row's
    # adds are not add legs and are not counted there
    census = pathlib.Path(SRC, "analytics", "lane_exec.py").read_text()
    assert "FILTER (WHERE status = 'merged')" in census


def test_buy_refuses_a_fill_it_cannot_book_without_a_write():
    p = _Ledger()
    assert _open(p)["ok"]
    before = len(p.sent)
    bad = [dict(shares=0), dict(shares=-1), dict(shares=float("nan")),
           dict(shares=float("inf")), dict(px=None), dict(px=0.0), dict(px=1.0),
           dict(px="x"), dict(oid=""), dict(oid=None), dict(seq=-1), dict(usd=-1.0),
           dict(wire_usd=-1.0), dict(rid="row")]
    for over in bad:
        kw = dict(rid=900, oid="o1", seq=0, shares=10, px=0.30, usd=3.0, wire_usd=3.1)
        kw.update(over)
        out = _run(le._book_mirror_buy(p, kw["rid"], kw["oid"], kw["seq"], kw["shares"],
                                       kw["px"], kw["usd"], kw["wire_usd"], 0.31, True))
        assert out is None, over
        assert len(p.sent) == before, over
    assert p.rows[900]["filled_shares"] == 0.0 and p.rows[900]["raw"]["adds"] == []
    # a row that is no longer a live mirror row books nothing
    p.rows[900]["status"] = "cashed_out"
    assert _buy(p, "o1", 0, 10, 0.30) is None
    assert p.rows[900]["filled_shares"] == 0.0


# ------------------------------------------------------------------ sell

def test_sell_never_writes_cashed_out_and_never_goes_negative():
    p = _booked()
    out = _sell(p, 20, 0.35, 20)
    assert out["booked"] == 20.0 and out["written"] is True and out["overfill"] is False
    row = p.rows[900]
    assert row["status"] == "filled" and row["filled_shares"] == 0.0
    sells = [s for k, s, _ in p.sent if "GREATEST(filled_shares" in s]
    assert sells and all("cashed_out" not in s and "settled_at" not in s for s in sells)
    # the flat row on a live market: another sale books nothing, flags, writes nothing
    n = len(p.sent)
    out = _sell(p, 5, 0.35, 0)
    assert out == {"booked": 0.0, "pnl": None, "overfill": True, "written": False,
                   "refusal": "nothing_to_book"}
    assert [s for k, s, _ in p.sent[n:] if "UPDATE" in s] == []
    assert row["filled_shares"] == 0.0 and row["status"] == "filled"
    # the belt in the statement itself
    assert "GREATEST(filled_shares - $2::float8, 0)" in le._MIRROR_SELL_SQL


def test_sell_overfill_books_the_ledger_only_and_flags_it():
    p = _Ledger()
    assert _open(p)["ok"]
    _buy(p, "o1", 0, 10, 0.30)
    out = _sell(p, 15, 0.35, 10)
    assert out["booked"] == 10.0 and out["overfill"] is True and out["written"] is True
    assert out["pnl"] == pytest.approx(le.realized_pnl(0.30, 0.35, 10, INTENT)) == pytest.approx(0.5)
    _, a = [(s, a) for k, s, a in p.sent if "GREATEST(filled_shares" in s][-1]
    assert a[1] == 10.0                     # the statement is handed the ledger, never the venue's 15
    assert p.rows[900]["filled_shares"] == 0.0 and p.rows[900]["pnl"] == pytest.approx(0.5)
    # the book's ledger and the row disagree: the SMALLER holding bounds the booking
    p = _Ledger()
    assert _open(p)["ok"]
    _buy(p, "o1", 0, 10, 0.30)
    out = _sell(p, 12, 0.35, 12)            # book says 12, the row holds 10
    assert out["booked"] == 10.0 and out["overfill"] is True
    assert p.rows[900]["filled_shares"] == 0.0


def test_sell_pnl_is_the_long_formula_against_the_rows_average_accumulated():
    p = _booked()                           # average 0.35
    row = p.rows[900]
    out = _sell(p, 4, 0.45, 20)
    assert out["pnl"] == pytest.approx(le.realized_pnl(0.35, 0.45, 4, INTENT)) == pytest.approx(0.4)
    assert row["pnl"] == pytest.approx(0.4) and row["filled_shares"] == 16.0
    out = _sell(p, 6, 0.25, 16)
    assert out["pnl"] == pytest.approx(-0.6)
    assert row["pnl"] == pytest.approx(-0.2) and row["filled_shares"] == 10.0
    assert row["orig_shares"] == 20.0 and row["fill_price"] == pytest.approx(0.35)
    # the entry is read off the row under the same guard, then the write
    kinds = [s for k, s, _ in p.sent if "/* mirror-sell */" in s or "GREATEST(" in s]
    assert kinds[0].startswith("SELECT fill_price::float8 AS fill_price")
    assert "WHERE id = $1 AND status = 'filled' AND lane = 'mirror'" in kinds[0]
    assert "GREATEST(" in kinds[1]
    # long-only: the intent handed to realized_pnl is the P1 constant
    src = inspect.getsource(le._book_mirror_sell)
    assert "realized_pnl(entry, px, booked, MIRROR_INTENT)" in src


def test_sell_fragments_equal_mirror_exits():
    """The partial statement is mirror_exit's (its relative write and the
    orig_shares capture, both reviewed) with the lane guard appended."""
    exit_src = inspect.getsource(le.mirror_exit)
    sell = le._MIRROR_SELL_SQL
    for frag in ("UPDATE live_orders SET status='filled', ",
                 "orig_shares=COALESCE(orig_shares, filled_shares::float8), ",
                 "filled_shares=GREATEST(filled_shares - $2::float8, 0), ",
                 "pnl=COALESCE(pnl,0)+$3 WHERE id=$1"):
        assert frag in exit_src, frag
        assert frag in sell, frag
    assert "WHERE id=$1 AND status='filled' AND lane='mirror'" in sell
    # and the close is mirror_exit's cashed_out shape, $2 = 0, with the guard
    for frag in ("UPDATE live_orders SET status='cashed_out', ",
                 "pnl=COALESCE(pnl,0)+$2, settled_at=now() WHERE id=$1"):
        assert frag in exit_src, frag
        assert frag in le._MIRROR_CLOSE_CASHED_OUT_SQL, frag


def test_sell_refuses_when_the_row_is_not_live_or_cannot_price_its_entry():
    p = _booked()
    n = len(p.sent)
    # a bad fill: nothing read, nothing written
    for shares, px in ((0, 0.35), (-1, 0.35), (10, None), (10, 1.0), ("x", 0.35)):
        out = _sell(p, shares, px, 20)
        assert out["refusal"] == "bad_fill" and out["written"] is False, (shares, px)
    assert len(p.sent) == n
    # the row is no longer a live mirror row: read, no write
    p.rows[900]["status"] = "cashed_out"
    out = _sell(p, 5, 0.35, 20)
    assert out["refusal"] == "row_not_live" and out["booked"] == 0.0
    assert [s for k, s, _ in p.sent[n:] if "UPDATE" in s] == []
    # a row holding shares with no entry price: the ledger cannot price
    # the sale, so it books nothing rather than a $0 pnl
    p.rows[900]["status"] = "filled"
    p.rows[900]["fill_price"] = None
    n = len(p.sent)
    out = _sell(p, 5, 0.35, 20)
    assert out["refusal"] == "no_entry_price" and out["written"] is False
    assert [s for k, s, _ in p.sent[n:] if "UPDATE" in s] == []
    assert p.rows[900]["filled_shares"] == 20.0 and p.rows[900]["pnl"] is None
    # an unreadable ledger row is a raise the caller's transaction sees,
    # never a booking against a guess
    p = _Ledger(raise_on=[("/* mirror-sell */", RuntimeError("db gone"))])
    assert _open(p)["ok"]
    with pytest.raises(RuntimeError):
        _sell(p, 5, 0.35, 20)


# ----------------------------------------------------------------- close

def test_close_moves_the_row_by_gross_buys_only_when_flat():
    # bought and sold to zero: cashed_out, pnl + 0, settled_at stamped
    p = _booked()
    _sell(p, 20, 0.35, 20)
    row = p.rows[900]
    pnl_before = row["pnl"]
    assert _close(p, 7.0) == "cashed_out"
    assert row["status"] == "cashed_out" and row["settled_at"] is not None
    assert row["pnl"] == pytest.approx(pnl_before) and row["error"] is None
    _, a = [(s, a) for k, s, a in p.sent if "SET status='cashed_out'" in s][0]
    assert a == (900, 0.0)
    # never bought: cancelled with the one mirror error text, claim freed
    p = _Ledger()
    assert _open(p)["ok"]
    assert _close(p, 0.0) == "cancelled"
    assert p.rows[900]["status"] == "cancelled"
    assert p.rows[900]["error"] == le.MIRROR_NO_FILL_CLOSE_TEXT == "mirror book: closed with no fill"
    assert p.rows[900]["settled_at"] is None
    # shares still held: neither branch closes (settlement from the venue does)
    p = _booked()
    for gross in (7.0, 0.0):
        assert _close(p, gross) is None
        assert p.rows[900]["status"] == "filled" and p.rows[900]["filled_shares"] == 20.0
    assert "AND filled_shares = 0" in le._MIRROR_CLOSE_CASHED_OUT_SQL
    assert "AND filled_shares = 0" in le._MIRROR_CLOSE_CANCELLED_SQL
    # a figure the book cannot vouch for closes nothing and sends nothing
    p = _booked()
    _sell(p, 20, 0.35, 20)
    n = len(p.sent)
    for gross in (None, -1.0, float("nan"), "seven"):
        assert _close(p, gross) is None, gross
    assert len(p.sent) == n and p.rows[900]["status"] == "filled"
    # a row that is not a live mirror row
    p.rows[900]["lane"] = None
    assert _close(p, 7.0) is None and p.rows[900]["status"] == "filled"


def test_a_closed_row_is_terminal_to_every_primitive():
    p = _booked()
    _sell(p, 20, 0.35, 20)
    assert _close(p, 7.0) == "cashed_out"
    assert _buy(p, "o9", 0, 5, 0.30) is None
    assert _sell(p, 5, 0.35, 5)["refusal"] == "row_not_live"
    assert _close(p, 7.0) is None
    assert p.rows[900]["status"] == "cashed_out" and p.rows[900]["filled_shares"] == 0.0


# ------------------------------------------------------ the invariants

def _named_row_patterns() -> list[str]:
    """Every `error LIKE '...'` pattern in the money path, as regexes."""
    pats = []
    for rel in ("live_executor.py", "workers/copy_sweep.py", "api/app.py",
                "analytics/lane_exec.py"):
        pats += re.findall(r"error LIKE '([^']*)'", pathlib.Path(SRC, rel).read_text())
    out = []
    for p in pats:
        rx = ".*".join(re.escape(part) for part in p.split("%")).replace("_", ".")
        out.append(rx)
    return out


def test_the_standing_rows_error_stays_null_and_never_looks_named():
    pats = _named_row_patterns()
    assert len(pats) >= 10
    # the whole life of a book: error is never written before the close
    p = _booked()
    _sell(p, 20, 0.35, 20)
    _buy(p, "o3", 0, 4, 0.20)
    _sell(p, 4, 0.25, 4)
    assert p.rows[900]["error"] is None
    for k, s, a in p.sent:
        if s.startswith("UPDATE live_orders") and "SET status='cancelled'" not in s:
            assert "error" not in s.split("WHERE")[0], s
    # the one text a mirror row ever carries matches no named-row pattern
    for rx in pats:
        assert not re.fullmatch(rx, le.MIRROR_NO_FILL_CLOSE_TEXT), rx
    # _reap_stale_submitting's scope is 'submitting' rows and named
    # 'error' rows: a 'filled' standing row with a NULL error is outside it
    src = inspect.getsource(le._reap_stale_submitting)
    scope = src[src.index("FROM live_orders WHERE"):src.index("for r in rows")]
    assert set(re.findall(r"status = '(\w+)'", scope)) == {"submitting", "error"}
    assert "filled" not in scope


def test_a_mirror_write_never_sets_exiting_merged_or_submitting():
    stmts = [le._MIRROR_ROW_INSERT_SQL, le._MIRROR_BUY_SQL, le._MIRROR_SELL_SQL,
             le._MIRROR_CLOSE_CASHED_OUT_SQL, le._MIRROR_CLOSE_CANCELLED_SQL]
    srcs = [inspect.getsource(f) for f in (le._open_mirror_book, le._book_mirror_buy,
                                           le._book_mirror_sell, le._close_mirror_episode)]
    # a whole lifecycle's statements as actually sent
    p = _booked()
    _sell(p, 20, 0.35, 20)
    _close(p, 7.0)
    p2 = _Ledger()
    _open(p2)
    _close(p2, 0.0)
    sent = [s for k, s, _ in p.sent + p2.sent + [("tx", s, a) for s, a in p.committed]]
    statuses = set()
    for text in stmts + srcs + sent:
        statuses |= set(re.findall(r"status\s*=\s*'(\w+)'", text))
        statuses |= set(re.findall(r"'filled'|'exiting'|'merged'|'submitting'", text))
    statuses = {s.strip("'") for s in statuses}
    assert statuses == {"filled", "cashed_out", "cancelled"}, statuses
    for bad in ("exiting", "merged", "submitting", "open", "unfilled", "rejected", "error"):
        assert bad not in statuses


def test_every_ledger_write_carries_the_lane_guard():
    p = _booked()
    _sell(p, 20, 0.35, 20)
    _close(p, 7.0)
    p2 = _Ledger()
    _open(p2)
    _close(p2, 0.0)
    updates = [s for k, s, _ in p.sent + p2.sent if s.startswith("UPDATE live_orders")]
    assert len(updates) >= 5
    for s in updates:
        assert ("lane = 'mirror'" in s or "lane='mirror'" in s) and "status" in s.split("WHERE")[1], s
    inserts = [s for s, _ in p.committed if s.startswith("INSERT INTO live_orders")]
    assert inserts and all("'mirror'" in s for s in inserts)
    # the switches are nobody's business here: the primitives read none
    for f in (le._open_mirror_book, le._book_mirror_buy, le._book_mirror_sell,
              le._close_mirror_episode):
        src = inspect.getsource(f)
        assert "ingestion_state" not in src and "os.environ" not in src and "getenv" not in src


def test_the_statements_parse_as_postgres():
    pglast = pytest.importorskip("pglast")
    p = _booked()
    _sell(p, 20, 0.35, 20)
    _close(p, 7.0)
    p2 = _Ledger()
    _open(p2)
    _close(p2, 0.0)
    sent = {s for k, s, _ in p.sent + p2.sent}
    assert len(sent) >= 8
    for s in sent:
        pglast.parse_sql(s)
    for s in (le._MIRROR_BOOK_INSERT_SQL, le._MIRROR_ROW_INSERT_SQL,
              le._MIRROR_BOOK_BACKFILL_SQL, le._MIRROR_BUY_SQL, le._MIRROR_SELL_SQL,
              le._MIRROR_SELL_READ_SQL, le._MIRROR_CLOSE_CASHED_OUT_SQL,
              le._MIRROR_CLOSE_CANCELLED_SQL):
        pglast.parse_sql(s)


def test_the_primitives_sit_beside_merge_add_leg_and_touch_no_existing_function():
    src = pathlib.Path(le.__file__).read_text()
    i_merge = src.index("async def _merge_add_leg(")
    i_open = src.index("async def _open_mirror_book(")
    i_tx = src.index("async def _tx_hash_of(")
    assert i_merge < i_open < i_tx
    for name in ("_book_mirror_buy", "_book_mirror_sell", "_close_mirror_episode"):
        assert i_open < src.index(f"async def {name}(") < i_tx
