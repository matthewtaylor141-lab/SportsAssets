"""E22 (FILL lane 22) adversarial review: the six pins the mutation run
found missing in tests/test_e22_lost_fill_adopt.py (review findings
HIGH-2, MEDIUM-1 .. MEDIUM-4, LOW-1), on the lane's own fixtures, plus
the fold's pin for LOW-4 (a legacy CLOSE row); the Postgres one runs
the lost-rows statement on the scratch database (the E12 fixture's
shape) and skips, never fakes, without one."""
from __future__ import annotations

import pytest

from sportsassets import live_executor as le
from sportsassets.workers import mirror_live as ml
from tests.test_e22_lost_fill_adopt import (  # noqa: F401 -- the lane's fixtures
    FILLED_AT, NEW_NAMES, PLACED, _book_863, _census, _log_fill, _lost, _mkt, _plan_exit, _pool, _shorts_on,
    _tick, _trades_reads, _venue,
)
from tests.test_mirror_live_worker import BUY, NOW, _armed, _short_book  # noqa: F401 -- the autouse rails


def test_e22_review_a_legacy_close_row_reads_no_log_and_counts_nothing(monkeypatch):
    """Review LOW-4: a lost CLOSE row (tif 'CLOSE', wire 0.0 -- E5 / P3's
    `close: unattributed`) on a frozen placement_lost short whose venue
    reads 0 against the ledger's leg: the delta IS the row's size on its
    side, but a CLOSE row has no cent for the by-order match (the read
    would require order_price == 0.0 and never match) -> nothing read,
    nothing counted, nothing on the plan, the row left `lost`."""
    _shorts_on(monkeypatch)
    p = _pool()
    b = _short_book(p, ledger=-28, avg=0.29, state="frozen", frozen_reason="placement_lost", frozen_ts=NOW - 3900)
    o = p.add_order(b, side=BUY, wire=0.0, qty=28, order_id=None, state="lost", placed_ts=PLACED, kind="reduce",
                    tif="CLOSE", done_at=PLACED + le._LOST_FILL_WINDOW_S, reason="order_lost")
    v = _venue(held=0, trades=[_log_fill(qty=28.0, order_price=0.0, order_qty=28.0)])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    assert ml._lost_fill_delta(p.orders[o["id"]], 0, -28, 0.0) == (28, 28), "the delta alone would have read"
    assert not _trades_reads(v) and all(_census(st, k) == 0 for k in NEW_NAMES)
    assert "lost_fill" not in b["last_plan"] and "lost_fill_at" not in b["last_plan"]
    assert p.orders[o["id"]]["state"] == "lost" and p.orders[o["id"]]["order_id"] is None
    assert b["ledger_net"] == -28 and b["state"] == "frozen" and b["frozen_reason"] == "placement_lost"


def test_e22_review_the_log_naming_nothing_names_the_miss_in_one_warning_line(monkeypatch, caplog):
    """Review HIGH-1 (c): the log naming no order for a surplus that
    matches the row logs ONE warning naming the row, the book, the
    surplus, the size, the cent and the window read (so the live run
    names the miss, not a bare count); inside the wait nothing is
    logged again."""
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p)
    v = _venue(trades=[])
    with caplog.at_level("WARNING"):
        st = _tick(p, v, http=_mkt(100.0, 400.0))
        _tick(p, v, now=NOW + 40, http=_mkt(100.0, 400.0))
    lines = [r.message for r in caplog.records if "trade log names no order" in r.message]
    assert len(lines) == 1 and len(_trades_reads(v)) == 1
    assert (f"lost row {o['id']} of book {b['id']}" in lines[0] and "surplus -28" in lines[0]
            and "no order of 28 @ 0.3" in lines[0] and f"[{PLACED - 30.0:.0f}, {NOW:.0f}]" in lines[0]
            and "E5's register is the road" in lines[0])
    assert _census(st, "lost_fill_unexplained") == 1 and _census(st, "lost_fill_adopted") == 0


def test_e22_review_a_delta_one_share_off_the_row_reads_no_log_and_books_nothing(monkeypatch):
    """Review M11: venue -29 (delta -27) and venue -31 (delta -29) against
    the lost SELL of 28 -> lost_fill_unexplained, NO log read, nothing
    booked. VENUE_LEDGER_TOL_SHARES is 1.0, so a one-share tolerance on
    this gate would book 28 on a venue that moved 27 and let the next
    tick's thaw hide the share inside the tolerance."""
    _shorts_on(monkeypatch)
    for held, delta in ((-29, -27), (-31, -29)):
        ml._lost_fill_read_at.clear()
        p = _pool()
        b, o = _book_863(p)
        v = _venue(held=held, trades=[_log_fill(qty=28.0)])
        st = _tick(p, v, http=_mkt(100.0, 400.0))
        assert not _trades_reads(v), held
        assert _census(st, "lost_fill_unexplained") == 1 and _census(st, "lost_fill_adopted") == 0
        assert _lost(b)["delta"] == delta and _lost(b)["verdict"] == "unexplained"
        assert b["ledger_net"] == -2 and p.orders[o["id"]]["state"] == "lost" and p.orders[o["id"]]["order_id"] is None


def test_e22_review_the_fill_is_booked_at_the_fill_weighted_price_not_the_wire(monkeypatch):
    """Review M12: the log's fills 20 @0.31 and 8 @0.30 on the 28 @0.30
    order (the order's price is the wire; the fills may be better) ->
    booked at round(8.6 / 28, 6), the recent entry and the plan carrying
    that price, never the wire."""
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p)
    v = _venue(trades=[_log_fill(qty=20.0, px=0.31), _log_fill(qty=8.0, px=0.30, ts=FILLED_AT + 5)])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    px = round((20.0 * 0.31 + 8.0 * 0.30) / 28.0, 6)
    assert px == pytest.approx(0.307143) and px != 0.30
    oo = p.orders[o["id"]]
    assert (oo["state"], oo["booked_filled"], oo["avg_px"]) == ("filled", 28.0, px)
    assert _census(st, "lost_fill_adopted") == 1 and b["ledger_net"] == -30
    rec = [x for x in ml._RECENT if x["what"] == "lost_fill_adopted"][-1]
    assert rec["px"] == px and rec["shares"] == 28.0


def test_e22_review_a_booking_that_fails_after_the_adoption_is_not_counted_adopted(monkeypatch):
    """Review M08: the ledger write raising inside _book_fill -> _book_delta's
    write_failed (counted, the book frozen under it), _finish_order leaves
    the row 'unknown' WITH the venue's id, the plan reads adopt_write_failed
    with the order, and lost_fill_adopted is NOT counted and NOT on the
    recent list -- step O's re-read books it next tick off the cursor."""
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p)
    p.raise_on.append(("ml-book-ledger-", RuntimeError("connection reset")))
    v = _venue(trades=[_log_fill(qty=28.0)])
    st = _tick(p, v, http=_mkt(100.0, 400.0))
    oo = p.orders[o["id"]]
    assert oo["order_id"] == "venue-x" and oo["state"] == "unknown"
    assert _census(st, "write_failed") >= 1 and _census(st, "lost_fill_adopted") == 0
    assert all(_census(st, k) == 0 for k in NEW_NAMES)
    assert _lost(b)["verdict"] == "adopt_write_failed" and _lost(b)["order"] == "venue-x"
    assert b["ledger_net"] == -2 and b["state"] == "frozen"
    assert not [x for x in ml._RECENT if x["what"] == "lost_fill_adopted"]


def test_e22_review_a_cached_read_reads_nothing_even_with_no_memo(monkeypatch):
    """Review M02b: the lane's unit call hands a prior plan whose
    lost_fill_at is 10 s old, so the MEMO (not the cached-read guard)
    is what keeps that call from reading; with an empty prior plan and an
    empty process memo, t.walk_at None alone must read no rows' log and
    write nothing on the plan."""
    import asyncio
    from tests.test_mirror_live_worker import CID, M, N, NOW, SLUG
    _shorts_on(monkeypatch)
    ml._lost_fill_read_at.clear()
    p = _pool()
    b, o = _book_863(p)
    t = ml._Tick(pool=p, pmus=_venue(trades=[_log_fill(qty=28.0)]), http=None, now=NOW, stats=ml._new_stats())
    t.walk_at = None
    r = ml._Reading("rn1", CID, SLUG, M, N, [], 100.0, 400.0, {}, None, False, False, False, None, None,
                    0.13, 0.14, 0.135, -30.0, 0.0, None, True)
    plan = {}
    out = asyncio.run(ml._lost_fill_adopt(t, b, r, -2, 0.0, {}, plan))
    assert out is None and plan == {} and not t.pmus.calls
    assert p.orders[o["id"]]["state"] == "lost" and p.orders[o["id"]]["order_id"] is None
    assert not ml._lost_fill_read_at and all(t.stats["census"][k] == 0 for k in NEW_NAMES)


def test_e22_review_the_adopted_fill_is_booked_maker(monkeypatch):
    """Review M18b: the adoption books the lost rest as a MAKER fill (the
    recent `fill` entry's maker True), as _reconcile_placing's branch does."""
    _shorts_on(monkeypatch)
    p = _pool()
    b, o = _book_863(p)
    v = _venue(trades=[_log_fill(qty=28.0)])
    _tick(p, v, http=_mkt(100.0, 400.0))
    fills = [x for x in ml._RECENT if x["what"] == "fill" and x["book"] == b["id"]]
    assert fills and fills[-1]["maker"] is True and fills[-1]["shares"] == 28.0 and fills[-1]["px"] == 0.30


def test_e22_review_the_lost_rows_statement_on_a_real_postgres():
    """Review M19 / M27: the worker fixture re-implements the predicate,
    so the statement's WHERE is pinned by text alone there. On the
    scratch Postgres (047 + 050, the E12 fixture's shape): of six rows
    only the book's 'lost' row WITHOUT an id whose done_at is BEFORE the
    clock is returned -- not a lost row with an id, not one marked lost
    after the clock, not one without a done_at, not a 'placing' row, not
    another book's -- on both shapes, in the open-orders projection."""
    import asyncio
    import datetime as dt
    import time
    from tests.test_e12_flow_only import MIG_DIR, _drop, _scratch
    marked = dt.datetime(2026, 9, 9, 2, 7, 51, tzinfo=dt.timezone.utc)   # 4965's done 02:07:51 (book_863_0257 row 12)
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)   # marked on THIS tick, after its clock

    async def _go():
        admin, conn, name = await _scratch()
        try:
            await conn.execute("CREATE TABLE live_orders (id bigserial PRIMARY KEY, us_market_slug text, lane text)")
            await conn.execute((MIG_DIR / "047_mirror_live.sql").read_text())
            bid = await conn.fetchval(
                "INSERT INTO mirror_books (whale, condition_id, us_market_slug, long_asset) "
                "VALUES ('rn1', 'c', 's', 'a') RETURNING id")
            other = await conn.fetchval(
                "INSERT INTO mirror_books (whale, condition_id, us_market_slug, long_asset) "
                "VALUES ('rn1', 'c2', 's2', 'a2') RETURNING id")
            ins = ("INSERT INTO mirror_orders (book_id, whale, us_market_slug, kind, side, tif, price, wire, qty, "
                   "state, order_id, done_at, placed_at) VALUES ($1, 'rn1', 's', 'increase', 'SELL_LONG', 'GTC', "
                   "0.3, 0.3, 28, $2, $3, $4, now() - interval '2 hours') RETURNING id")
            a = await conn.fetchval(ins, bid, "lost", None, marked)
            await conn.fetchval(ins, bid, "lost", "had-an-id", marked)
            await conn.fetchval(ins, bid, "lost", None, later)         # marked 60 s after the clock: this tick's mark
            await conn.fetchval(ins, bid, "lost", None, None)                              # no done_at: not read
            await conn.fetchval(ins, bid, "placing", None, None)
            await conn.fetchval(ins, other, "lost", None, marked)
            now = time.time()
            rows = await conn.fetch(ml._SQL_LOST_ROWS_047, bid, now)
            assert [r["id"] for r in rows] == [a]
            assert set(rows[0].keys()) >= {"id", "book_id", "qty", "wire", "side", "tif", "state", "order_id",
                                           "booked_filled", "placed_ts", "us_market_slug", "reason", "receipt"}
            assert "intent" not in rows[0].keys()
            assert await conn.fetch(ml._SQL_LOST_ROWS_047, other, now) != [] and \
                [r["id"] for r in await conn.fetch(ml._SQL_LOST_ROWS_047, bid, now - 86400.0 * 400)] == []
            await conn.execute((MIG_DIR / "050_mirror_shorts.sql").read_text())
            rows = await conn.fetch(ml._SQL_LOST_ROWS, bid, now)
            assert [r["id"] for r in rows] == [a] and rows[0]["intent"] == "ORDER_INTENT_BUY_LONG"
        finally:
            await _drop(admin, conn, name)

    asyncio.run(_go())
