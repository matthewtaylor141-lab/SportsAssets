"""Attributing a lost fill in the venue's trade log (review round nine).

The account is SHARED with the owner. A fill in the trade log at the
whale's cent inside a stranded row's window used to be booked as ours on
price, side and time alone -- and the owner's own buy at that cent,
sized in tens-to-hundreds of dollars, was then sold out from under him
on the whale's exit. The venue's execution record names the ORDER the
fill belongs to (id, size, limit price); when it is there, a fill is
ours only if that order is exactly our size at our cent, and the row is
reconciled from the venue's order record by id. When it is absent the
old rule stands. Also: the side of a short is what the venue names it,
a manual-desk ticket is never cancelled by the ledger reaper, and a
venue-reaper adopt that fails for any reason still records the fill.
"""
import asyncio
import datetime as _dt
import time

import pytest

from sportsassets import live_executor as le
from sportsassets import pmus

SLUG = "aec-x-y-2026-09-01"


def _iso(secs_ago: float) -> str:
    return (_dt.datetime.now(tz=_dt.timezone.utc)
            - _dt.timedelta(seconds=secs_ago)).isoformat().replace("+00:00", "Z")


def _venue_trade(qty="100", price="0.48", exec_side="ORDER_SIDE_BUY",
                 order=None, secs_ago=700.0, rp="0", aggressor=False,
                 slug=SLUG):
    t = {"createTime": _iso(secs_ago), "marketSlug": slug, "qty": qty,
         "price": {"value": price, "currency": "USD"},
         "realizedPnl": {"value": rp, "currency": "USD"},
         "isAggressor": aggressor}
    o = {"side": exec_side}
    if order:
        o.update(order)
    t["aggressorExecution" if aggressor else "passiveExecution"] = {"order": o}
    return {"type": "ACTIVITY_TYPE_TRADE", "trade": t}


class _Portfolio:
    def __init__(self, acts):
        self.acts = list(acts)

    def activities(self, params):
        return {"activities": self.acts, "eof": True}


class _Client:
    def __init__(self, acts):
        self.portfolio = _Portfolio(acts)


def _parsed(monkeypatch, acts, since=None):
    monkeypatch.setattr(pmus, "_client", _Client(acts))
    return pmus.recent_trades(SLUG, since if since is not None else time.time() - 3600)


# ---------------------------------------------------------------- parser

def test_the_parser_carries_the_execution_order_when_the_venue_names_it(monkeypatch):
    out = _parsed(monkeypatch, [
        _venue_trade(order={"id": "o-1", "quantity": 100,
                            "price": {"value": "0.48", "currency": "USD"}}),
        _venue_trade(qty="40", price="0.46", aggressor=True),
    ])
    assert out[0]["order_id"] == "o-1"
    assert out[0]["order_qty"] == 100.0 and out[0]["order_price"] == 0.48
    assert out[0]["aggressor"] is False
    assert out[1]["order_id"] is None and out[1]["order_qty"] is None
    assert out[1]["order_price"] is None and out[1]["aggressor"] is True


# ------------------------------------------------------ the attribution

@pytest.mark.parametrize("intent", ["ORDER_INTENT_BUY_LONG", None])
def test_a_fill_whose_order_is_not_our_size_is_not_ours(intent):
    """The owner's 500-share order at the whale's cent, partially filled
    for 100 inside our window: on price/side/time alone it was ours."""
    now = time.time()
    f = {"qty": 100.0, "price": 0.48, "side": "ORDER_SIDE_BUY", "ts": now - 60,
         "realized_pnl": 0.0, "order_id": "his", "order_qty": 500.0,
         "order_price": 0.48, "aggressor": False}
    assert not le._lost_fill_is_ours(f, 0.48, intent, 100.0, now - 100, now)
    f["order_qty"] = 100.0
    assert le._lost_fill_is_ours(f, 0.48, intent, 100.0, now - 100, now)


def test_a_fill_whose_order_the_venue_did_not_name_is_unknown_never_ours():
    """The account is shared: with no order to attribute it to, a fill
    at our cent inside our window is as likely the owner's as ours."""
    now = time.time()
    f = {"qty": 60.0, "price": 0.48, "side": "ORDER_SIDE_BUY", "ts": now - 60,
         "realized_pnl": 0.0, "order_id": None, "order_qty": None,
         "order_price": None, "aggressor": None}
    assert not le._lost_fill_is_ours(f, 0.48, "ORDER_INTENT_BUY_LONG", 100.0, now - 100, now)
    f["order_qty"] = 100.0
    assert le._lost_fill_is_ours(f, 0.48, "ORDER_INTENT_BUY_LONG", 100.0, now - 100, now)


def test_a_better_priced_fill_is_ours_when_its_order_sits_at_our_cent():
    """A lost IOC fills at the ask, which can be below our limit; the
    order record still carries our cent and our size."""
    now = time.time()
    f = {"qty": 100.0, "price": 0.46, "side": "ORDER_SIDE_BUY", "ts": now - 60,
         "realized_pnl": 0.0, "order_id": "ours", "order_qty": 100.0,
         "order_price": 0.48, "aggressor": True}
    assert le._lost_fill_is_ours(f, 0.48, "ORDER_INTENT_BUY_LONG", 100.0, now - 100, now)


def test_the_ioc_cent_is_inside_the_rows_authorised_band():
    """The IOC goes out at his price plus the authorised slippage (the
    row's limit_price), one cent above the rest cent. An orphaned IOC
    that filled AT its limit was named, never booked (round nine)."""
    now = time.time()
    lo, hi = le._leg_cents(0.47, 0.48, "ORDER_INTENT_BUY_LONG")
    assert (lo, hi) == (0.47, 0.48)
    f = {"qty": 100.0, "price": 0.48, "side": "ORDER_SIDE_BUY", "ts": now - 60,
         "realized_pnl": 0.0, "order_id": "ours", "order_qty": 100.0,
         "order_price": 0.48, "aggressor": True}
    assert not le._lost_fill_is_ours(f, 0.47, "ORDER_INTENT_BUY_LONG", 100.0, now - 100, now)
    assert le._lost_fill_is_ours(f, 0.47, "ORDER_INTENT_BUY_LONG", 100.0, now - 100, now,
                                 limit_price=0.48)
    f["order_price"] = 0.50          # above anything the row authorised
    assert not le._lost_fill_is_ours(f, 0.47, "ORDER_INTENT_BUY_LONG", 100.0, now - 100, now,
                                     limit_price=0.48)


def test_the_side_of_a_short_is_what_the_venue_names():
    """The venue books a BUY_SHORT as a SELL of the long; a short row's
    fill is a SELL at the short cent, and a long row's never is."""
    now = time.time()
    short_cent = next(iter(le._fingerprint_cents(0.48, "ORDER_INTENT_BUY_SHORT")))
    long_cent = next(iter(le._fingerprint_cents(0.48, "ORDER_INTENT_BUY_LONG")))
    sell = {"qty": 100.0, "price": short_cent, "side": "ORDER_SIDE_SELL",
            "ts": now - 60, "realized_pnl": 0.0, "order_id": "s",
            "order_qty": 100.0, "order_price": short_cent, "aggressor": False}
    assert le._lost_fill_is_ours(sell, 0.48, "ORDER_INTENT_BUY_SHORT", 100.0, now - 100, now)
    assert not le._lost_fill_is_ours(sell, 0.48, "ORDER_INTENT_BUY_LONG", 100.0, now - 100, now)
    assert not le._lost_fill_is_ours(sell, 0.48, None, 100.0, now - 100, now)
    sell_long = dict(sell, price=long_cent, order_price=long_cent)
    assert not le._lost_fill_is_ours(sell_long, 0.48, "ORDER_INTENT_BUY_SHORT", 100.0, now - 100, now)
    buy_short = dict(sell, side="ORDER_SIDE_BUY")     # the owner's long at 0.52
    assert not le._lost_fill_is_ours(buy_short, 0.48, "ORDER_INTENT_BUY_SHORT", 100.0, now - 100, now)


def test_a_fill_outside_the_window_or_with_realized_pnl_is_not_ours():
    now = time.time()
    f = {"qty": 100.0, "price": 0.48, "side": "ORDER_SIDE_BUY", "ts": now - 60,
         "realized_pnl": 0.0, "order_id": "ours", "order_qty": 100.0,
         "order_price": 0.48, "aggressor": False}
    assert not le._lost_fill_is_ours(f, 0.48, None, 100.0, now - 50, now)
    assert not le._lost_fill_is_ours(dict(f, realized_pnl=1.5), 0.48, None, 100.0, now - 100, now)


# --------------------------------------------- the reaper, end to end

class _Row(dict):
    def keys(self):
        return list(super().keys())


class _Pool:
    def __init__(self, explained=0.0, adopt_raises=None):
        self.queries: list[tuple[str, tuple]] = []
        self.explained = explained
        self.adopt_raises = adopt_raises

    async def fetchval(self, sql, *a):
        return self.explained

    async def fetch(self, sql, *a):
        return []

    async def execute(self, sql, *a):
        if self.adopt_raises and "SET order_id=$2" in sql:
            raise self.adopt_raises
        self.queries.append((sql, a))


class _Pmus:
    def __init__(self, fills, status=None, open_orders=()):
        self.fills = list(fills)
        self.status = status
        self.cancelled: list[str] = []
        self.status_reads: list[str] = []
        self._open = list(open_orders)

    def recent_trades(self, slug, since_ts, max_pages=3):
        return list(self.fills)

    def open_orders(self, slugs=None):
        return list(self._open)

    def cancel_order(self, oid, slug):
        self.cancelled.append(oid)
        return {"ok": True}

    def order_status(self, oid):
        self.status_reads.append(oid)
        return self.status


def _row(**kw):
    base = dict(id=11, order_id=None, us_market_slug=SLUG, requested_shares=100.0,
                intent="ORDER_INTENT_BUY_LONG", his_price=0.48, age_s=1200.0,
                status="submitting", error=None, whale_username="rn1")
    base.update(kw)
    return _Row(**base)


def _fill(**kw):
    now = time.time()
    f = {"qty": 100.0, "price": 0.48, "side": "ORDER_SIDE_BUY", "ts": now - 700,
         "realized_pnl": 0.0, "order_id": None, "order_qty": None,
         "order_price": None, "aggressor": False}
    f.update(kw)
    return f


@pytest.fixture
def held(monkeypatch):
    def _bind(shares, avg=0.48):
        async def _h(_slug):
            return shares, avg
        monkeypatch.setattr(le, "_pm_held", _h)
    return _bind


def test_the_owners_fill_at_our_cent_is_not_booked_as_ours(held):
    """Round nine, the blocker: his 100 of a 500-share order at the
    whale's cent inside our window. Not ours -> the position is NAMED,
    not booked, and mirror_exit never sells his shares."""
    held(100)
    pmus_ = _Pmus([_fill(order_id="his", order_qty=500.0, order_price=0.48)])
    pool = _Pool()
    out = asyncio.run(le._adopt_lost_bid(pool, pmus_, _row(), 1200.0))
    assert out == "position"
    assert not [q for q in pool.queries if "status='filled'" in q[0]]
    assert not pmus_.cancelled


def test_our_lost_gtc_is_reconciled_by_its_order_id_from_the_venue_record(held):
    """The trade log names our order (our size, our cent): the row takes
    the id and is written from the venue's ORDER record -- the definitive
    cumQuantity / avgPx -- not from a sum of trade rows."""
    held(100)
    pmus_ = _Pmus([_fill(qty=60.0, order_id="o-77", order_qty=100.0, order_price=0.48),
                   _fill(qty=40.0, order_id="o-77", order_qty=100.0, order_price=0.48)],
                  status={"order_id": "o-77", "state": "filled", "filled_shares": 100.0,
                          "avg_px": 0.48, "price": 0.48, "intent": "ORDER_INTENT_BUY_LONG"})
    pool = _Pool()
    out = asyncio.run(le._adopt_lost_bid(pool, pmus_, _row(), 1200.0))
    assert out == "booked"
    adopt = [q for q in pool.queries if "SET order_id=$2" in q[0]]
    assert adopt and adopt[0][1] == (11, "o-77")
    assert pmus_.status_reads == ["o-77"]
    fill = [q for q in pool.queries if "status='filled'" in q[0]]
    assert fill and fill[0][1][1] == 100.0 and fill[0][1][2] == 0.48


def test_two_orders_of_our_size_at_our_cent_is_ambiguous_and_names_the_row(held):
    held(200)
    pmus_ = _Pmus([_fill(order_id="a", order_qty=100.0, order_price=0.48),
                   _fill(order_id="b", order_qty=100.0, order_price=0.48)])
    pool = _Pool()
    out = asyncio.run(le._adopt_lost_bid(pool, pmus_, _row(), 1200.0))
    assert out == "position"
    assert not [q for q in pool.queries if "status='filled'" in q[0]]
    assert not pmus_.status_reads


def test_a_fill_with_no_execution_order_names_the_row_instead_of_booking(held):
    held(100)
    pmus_ = _Pmus([_fill()])
    pool = _Pool()
    out = asyncio.run(le._adopt_lost_bid(pool, pmus_, _row(), 1200.0))
    assert out == "position"
    assert not [q for q in pool.queries if "status='filled'" in q[0]]
    named = [q for q in pool.queries if "venue holds a POSITION" in str(q[1])]
    assert named


def test_a_fill_whose_order_another_ledger_row_holds_is_never_ours(held):
    """The desk GTC's id is on a manual row; its fill of exactly our
    size at our cent is his, and the ledger already knew."""
    held(100)

    class _P(_Pool):
        async def fetch(self, sql, *a):
            if "order_id IS NOT NULL" in sql:
                return [_Row(order_id="desk-77")]
            return []

    pmus_ = _Pmus([_fill(order_id="desk-77", order_qty=100.0, order_price=0.48)])
    pool = _P()
    out = asyncio.run(le._adopt_lost_bid(pool, pmus_, _row(), 1200.0))
    assert out == "position"
    assert not pmus_.status_reads
    assert not [q for q in pool.queries if "status='filled'" in q[0]]


def test_the_orders_fill_below_its_limit_is_booked_from_the_order_record(held):
    """A lost IOC at limit 0.48 (his 0.47 + 1c) that filled at 0.48:
    outside the rest cent, inside the row's authorised band, booked by
    id from the venue's order record and stamped as an IOC fill."""
    held(100)
    pmus_ = _Pmus([_fill(price=0.48, order_id="ioc-1", order_qty=100.0,
                         order_price=0.48, aggressor=True)],
                  status={"order_id": "ioc-1", "state": "filled",
                          "filled_shares": 100.0, "avg_px": 0.48,
                          "price": 0.48, "intent": "ORDER_INTENT_BUY_LONG"})
    pool = _Pool()
    out = asyncio.run(le._adopt_lost_bid(pool, pmus_, _row(his_price=0.47, limit_price=0.48), 1200.0))
    assert out == "booked"
    lane = [q for q in pool.queries if "SET lane=$2" in q[0]]
    assert lane and lane[-1][1] == (11, "ioc")


def test_a_short_rows_sell_at_the_short_cent_is_booked(held):
    held(100)
    short_cent = next(iter(le._fingerprint_cents(0.22, "ORDER_INTENT_BUY_SHORT")))
    pmus_ = _Pmus([_fill(price=short_cent, side="ORDER_SIDE_SELL", order_id="s-1",
                         order_qty=100.0, order_price=short_cent)],
                  status={"order_id": "s-1", "state": "filled",
                          "filled_shares": 100.0, "avg_px": short_cent,
                          "price": short_cent, "intent": "ORDER_INTENT_BUY_SHORT"})
    pool = _Pool()
    out = asyncio.run(le._adopt_lost_bid(
        pool, pmus_, _row(his_price=0.22, intent="ORDER_INTENT_BUY_SHORT"), 1200.0))
    assert out == "booked"
    fill = [q for q in pool.queries if "status='filled'" in q[0]]
    assert fill and fill[0][1][1] == 100.0


def test_the_ledger_reaper_never_cancels_a_manual_ticket(monkeypatch):
    """A desk ticket whose row died between INSERT and the 'open' write
    is adopted from the book and set 'open' for sync_open_manual_orders;
    the copy lane's cancel-and-read is never applied to the owner's
    order."""
    now = time.time()
    open_bid = {"order_id": "m-1", "us_market_slug": SLUG, "side": "BUY",
                "price": 0.48, "quantity": 100.0,
                "created_at": _iso(1195.0), "intent": "ORDER_INTENT_BUY_LONG"}
    pmus_ = _Pmus([], open_orders=[open_bid])
    pool = _Pool()
    r = _row(whale_username="manual")
    asyncio.run(le._reap_one_submitting_row(pool, pmus_, r))
    assert not pmus_.cancelled
    opened = [q for q in pool.queries if "status='open'" in q[0]]
    assert opened and opened[0][1][:2] == (11, "m-1")
    # and one that already carries its id is likewise handed to the desk
    pool2, pmus2 = _Pool(), _Pmus([])
    asyncio.run(le._reap_one_submitting_row(pool2, pmus2, _row(order_id="m-2", whale_username="manual")))
    assert not pmus2.cancelled and not pmus2.status_reads
    assert [q for q in pool2.queries if "status='open'" in q[0]]
    del now


def test_the_ledger_reaper_still_cancels_and_reads_a_copy_rows_order():
    pmus_ = _Pmus([], status={"order_id": "c-1", "state": "canceled",
                              "filled_shares": 0.0, "avg_px": None})
    pool = _Pool()
    asyncio.run(le._reap_one_submitting_row(pool, pmus_, _row(order_id="c-1")))
    assert pmus_.cancelled == ["c-1"]
    assert [q for q in pool.queries if "status='unfilled'" in q[0]]


def test_a_venue_reaper_adopt_that_fails_for_any_reason_still_records_the_fill(monkeypatch):
    """Round nine (minor, upheld): a non-unique DB error on the adopt
    UPDATE cancelled the bid and walked away. The reconcile now runs
    with the status kept, so a partial fill on the cancelled bid lands
    on the row as ORPHAN FILL RECORDED, never in no row at all."""
    placed = time.time() - (le.REST_BID_TTL_S + 120.0)
    bid = {"order_id": "v-1", "us_market_slug": SLUG, "side": "BUY",
           "price": 0.48, "quantity": 100.0,
           "created_at": _iso(time.time() - placed - 5.0),
           "intent": "ORDER_INTENT_BUY_LONG"}
    monkeypatch.setattr(pmus, "open_orders", lambda slugs=None: [bid])
    cancelled = []
    monkeypatch.setattr(pmus, "cancel_order", lambda oid, slug: cancelled.append(oid) or {"ok": True})
    calls = []

    async def _rec(pool, pmus_, row_id, oid, slug, intent, age, keep_status=False):
        calls.append((row_id, oid, keep_status))
        return "filled"

    monkeypatch.setattr(le, "_reconcile_row_by_id", _rec)

    class _P(_Pool):
        async def fetch(self, sql, *a):
            if "whale_username,'') = 'manual'" in sql:
                return []
            return [_Row(id=11, us_market_slug=SLUG, placed_ts=placed,
                         his_price=0.48, requested_shares=100.0,
                         intent="ORDER_INTENT_BUY_LONG")]

    pool = _P(adopt_raises=RuntimeError("connection reset by peer"))
    n = asyncio.run(le._reap_stale_resting_bids(pool))
    assert n == 1 and cancelled == ["v-1"]
    assert calls == [(11, "v-1", True)]


# ------------------------------------------------- the clock and the IOC

def test_the_sweeps_reclaim_restarts_the_rows_clock():
    """Every window the lane keys off placed_at read a reclaimed row as
    hours old (round nine, reproduced on a migrated database): the
    in-flight horizon, the fill window, the venue reaper's time match."""
    import inspect
    import re

    src = inspect.getsource(le.maybe_execute)
    m = re.search(r"ON CONFLICT \(trade_id\) DO UPDATE(.*?)WHERE live_orders\.status IN",
                  src, re.S)
    assert m and "placed_at=now()" in m.group(1)


def test_a_bid_that_was_on_the_book_before_we_placed_is_never_adopted():
    older = {"order_id": "pre-1", "us_market_slug": SLUG, "side": "BUY",
             "price": 0.48, "quantity": 100.0, "created_at": _iso(3.0)}
    pmus_ = _Pmus([], open_orders=[older])
    found, readable = asyncio.run(le._find_lost_rest_bid(
        pmus_, SLUG, 100.0, {0.48}, window=(time.time() - 90, time.time() + 5)))
    assert readable and found is not None
    found, readable = asyncio.run(le._find_lost_rest_bid(
        pmus_, SLUG, 100.0, {0.48}, window=(time.time() - 90, time.time() + 5),
        exclude={"pre-1"}))
    assert readable and found is None


def test_the_rest_cycle_snapshots_the_book_before_it_places():
    import inspect

    src = inspect.getsource(le._rest_cycle)
    assert src.index("pmus.open_orders, [us_slug]") < src.index('"TIME_IN_FORCE_GOOD_TILL_CANCEL"')
    assert "exclude=pre_ids" in src


def test_an_ioc_cut_off_by_our_own_cancellation_still_records_its_order():
    """The sweep's per-row timeout cancels the copy inside the venue
    call; the thread completes the fill. The order id lands on the row
    for the reaper to reconcile by id (round nine)."""
    import threading

    gate = threading.Event()
    calls = []

    def _slow_ioc(*args):
        gate.wait(5.0)
        calls.append(args)
        return {"ok": True, "order_id": "ioc-9", "filled_shares": 100.0,
                "fill_price": 0.48, "status": "filled"}

    pool = _Pool()

    async def _drive():
        task = asyncio.ensure_future(le._ioc_guarded(pool, 11, _slow_ioc, "a", 0.48))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        gate.set()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if pool.queries:
                break
        return list(pool.queries)

    queries = asyncio.run(_drive())
    assert calls, "the venue call itself was never cut short"
    assert queries and "SET order_id=$2" in queries[0][0]
    assert queries[0][1] == (11, "ioc-9")
    assert "order_id IS NULL" in queries[0][0] and "status='submitting'" in queries[0][0]


def test_an_uncancelled_ioc_returns_its_result_untouched():
    pool = _Pool()
    out = asyncio.run(le._ioc_guarded(pool, 11, lambda *a: {"order_id": "x"}, "a"))
    assert out == {"order_id": "x"} and not pool.queries


def test_maybe_execute_places_the_ioc_through_the_guard():
    import inspect

    src = inspect.getsource(le.maybe_execute)
    assert "await _ioc_guarded(" in src
    assert "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" in src.split("await _ioc_guarded(", 1)[1][:400]


# ------------------------------------------------------- round ten

class _RestPmus:
    """A venue where the owner's bid X of exactly our size at our cent
    rests BEFORE our GTC goes out, and the placement's response is
    lost."""

    def __init__(self, x):
        self.x = x
        self.calls: list[tuple] = []

    def open_orders(self, slugs=None):
        self.calls.append(("open_orders",))
        return [self.x]

    def submit_fok(self, *a):
        self.calls.append(("place",))
        raise RuntimeError("read timed out")

    def cancel_order(self, oid, slug):
        self.calls.append(("cancel", oid))
        return {"ok": True}

    def order_status(self, oid):
        return {"order_id": oid, "state": "canceled", "filled_shares": 0.0}

    def recent_trades(self, slug, since_ts, max_pages=3):
        return []


def test_the_snapshot_travels_with_the_row_and_the_reapers_honour_it(monkeypatch, held):
    """Round ten (major): the lane excluded the owner's pre-existing
    bid in-process, then held the row 'submitting' with a raw that did
    not carry the snapshot; ten minutes on, the ledger reaper adopted
    that same bid and CANCELLED it. The snapshot is now persisted on
    the row and every look-up reads it."""
    monkeypatch.setattr(le, "_copy_stop", lambda *a, **k: None)
    x = {"order_id": "pre-X", "us_market_slug": SLUG, "side": "BUY",
         "price": 0.48, "quantity": 100.0, "created_at": _iso(20.0)}
    pmus_ = _RestPmus(x)
    out = asyncio.run(le._rest_cycle(_Pool(), pmus_, 11, SLUG, 0.48, 100.0,
                                     "ORDER_INTENT_BUY_LONG", "rn1", 0.48))
    assert out is not None and out["status"] == "rest_unknown"
    assert out["raw"]["pre_ids"] == ["pre-X"]
    assert ("cancel", "pre-X") not in pmus_.calls

    # the ledger reaper, reading raw->'pre_ids' as asyncpg hands it over
    held(0)
    pool = _Pool()
    row = _row(pre_ids='["pre-X"]')
    x["created_at"] = _iso(1195.0)                  # inside the row's window
    out = asyncio.run(le._adopt_lost_bid(pool, pmus_, row, 1200.0))
    assert out is None                               # nothing of ours anywhere
    assert ("cancel", "pre-X") not in pmus_.calls
    assert not [q for q in pool.queries if "SET order_id=$2" in q[0]]

    # the venue reaper, same row in scope
    placed = time.time() - (le.REST_BID_TTL_S + 120.0)
    x["created_at"] = _iso(time.time() - placed - 5.0)
    monkeypatch.setattr(pmus, "open_orders", lambda slugs=None: [x])
    cancelled = []
    monkeypatch.setattr(pmus, "cancel_order", lambda oid, slug: cancelled.append(oid) or {"ok": True})

    class _P(_Pool):
        async def fetch(self, sql, *a):
            if "whale_username,'') = 'manual'" in sql:
                return []
            return [_Row(id=11, us_market_slug=SLUG, placed_ts=placed,
                         his_price=0.48, requested_shares=100.0,
                         intent="ORDER_INTENT_BUY_LONG", pre_ids='["pre-X"]')]

    assert asyncio.run(le._reap_stale_resting_bids(_P())) == 0
    assert cancelled == []


def test_pre_ids_are_read_in_every_shape_asyncpg_hands_over():
    assert le._pre_ids_of(None) == set()
    assert le._pre_ids_of('["a", "b"]') == {"a", "b"}
    assert le._pre_ids_of(["a", None]) == {"a"}
    assert le._pre_ids_of("not json") == set()
    assert le._pre_ids_of(7) == set()


def test_a_still_live_order_is_left_for_the_next_pass_not_booked_from_a_sum(held):
    """Round ten: the venue names our order in its log but reports it
    non-terminal after the cancel and three reads. The row keeps the
    adopted id and nothing is booked; the next pass reconciles by id."""
    held(100)
    pmus_ = _Pmus([_fill(qty=40.0, order_id="o-77", order_qty=100.0, order_price=0.48)],
                  status={"order_id": "o-77", "state": "partially_filled",
                          "filled_shares": 40.0, "avg_px": 0.48, "price": 0.48})
    pool = _Pool()
    out = asyncio.run(le._adopt_lost_bid(pool, pmus_, _row(), 1200.0))
    assert out == "unreadable"
    adopt = [q for q in pool.queries if "SET order_id=$2" in q[0]]
    assert adopt and adopt[0][1] == (11, "o-77")
    assert not [q for q in pool.queries if "status='filled'" in q[0]]
    assert not [q for q in pool.queries if "venue holds a POSITION" in str(q[1])]


def test_only_the_manual_desk_is_a_desk():
    assert le._DESK_SLEEVES == frozenset({"manual"})


def test_an_underdog_row_is_reconciled_like_a_copy(held):
    """The underdog sleeve places FOKs at ask+2c: a lost fill at its
    limit is inside the row's band and is booked by its order."""
    held(4)
    pmus_ = _Pmus([_fill(qty=4.0, price=0.50, order_id="ud-1", order_qty=4.0,
                         order_price=0.50, aggressor=True)],
                  status={"order_id": "ud-1", "state": "filled", "filled_shares": 4.0,
                          "avg_px": 0.50, "price": 0.50, "tif": "IMMEDIATE_OR_CANCEL"})
    pool = _Pool()
    r = _row(whale_username="underdog", his_price=0.48, limit_price=0.50,
             requested_shares=4.0, intent=None)
    asyncio.run(le._reap_one_submitting_row(pool, pmus_, r))
    fill = [q for q in pool.queries if "status='filled'" in q[0]]
    assert fill and fill[0][1][1] == 4.0
    assert not [q for q in pool.queries if "status='open'" in q[0]]


def test_the_reclaim_clears_the_price_path_of_the_first_attempt():
    import inspect

    src = inspect.getsource(le.maybe_execute)
    i = src.index("RETURNING id")
    assert "DELETE FROM price_path WHERE row_id = $1" in src[i:i + 1500]


def test_an_ioc_record_is_stamped_ioc_by_the_reaper_and_by_the_guard():
    pmus_ = _Pmus([], status={"order_id": "i-1", "state": "filled", "filled_shares": 100.0,
                              "avg_px": 0.48, "price": 0.48, "tif": "IMMEDIATE_OR_CANCEL"})
    pool = _Pool()
    out = asyncio.run(le._reconcile_row_by_id(pool, pmus_, 11, "i-1", SLUG,
                                              "ORDER_INTENT_BUY_LONG", 700.0))
    assert out == "filled"
    lane = [q for q in pool.queries if "SET lane=COALESCE($2, lane, 'rest')" in q[0]]
    assert lane and lane[0][1] == (11, "ioc")
    pmus_ = _Pmus([], status={"order_id": "g-1", "state": "filled", "filled_shares": 100.0,
                              "avg_px": 0.48, "price": 0.48, "tif": "GOOD_TILL_CANCEL"})
    pool = _Pool()
    asyncio.run(le._reconcile_row_by_id(pool, pmus_, 11, "g-1", SLUG, None, 700.0))
    lane = [q for q in pool.queries if "SET lane=COALESCE($2, lane, 'rest')" in q[0]]
    assert lane and lane[0][1] == (11, None)

    async def _done():
        fut = asyncio.get_running_loop().create_future()
        fut.set_result({"order_id": "ioc-late", "filled_shares": 100.0})
        pool = _Pool()
        await le._record_orphan_ioc(pool, 11, fut)
        return pool.queries

    queries = asyncio.run(_done())
    assert any("SET lane='ioc'" in q[0] and q[1] == (11,) for q in queries)


# ------------------------------------------------------- round eleven

def test_a_log_named_fill_whose_order_has_no_record_yet_is_booked_from_the_log(held):
    """Round eleven (major, reproduced on a real database): the log named
    order X, the order endpoint had no record of it, and the round-ten
    'left' turned that into an hour of waiting followed by an
    unprotected 'error' row -- the fill unbooked, the trade re-buyable."""
    held(100)
    pmus_ = _Pmus([_fill(qty=100.0, order_id="o-77", order_qty=100.0, order_price=0.48)],
                  status=None)
    pool = _Pool()
    out = asyncio.run(le._adopt_lost_bid(pool, pmus_, _row(), 1200.0))
    assert out == "booked"
    fill = [q for q in pool.queries if "status='filled'" in q[0]]
    assert fill and fill[0][1][1] == 100.0 and fill[0][1][2] == 0.48
    assert pmus_.status_reads == ["o-77"] * 3


def test_a_live_order_is_still_left_alone(held):
    held(100)
    pmus_ = _Pmus([_fill(qty=40.0, order_id="o-77", order_qty=100.0, order_price=0.48)],
                  status={"order_id": "o-77", "state": "partially_filled",
                          "filled_shares": 40.0, "avg_px": 0.48, "price": 0.48})
    pool = _Pool()
    assert asyncio.run(le._adopt_lost_bid(pool, pmus_, _row(), 1200.0)) == "unreadable"
    assert not [q for q in pool.queries if "status='filled'" in q[0]]


def test_a_row_with_an_id_the_venue_forgot_is_booked_from_its_log_by_that_id():
    """By-id branch: the venue handed out o-77, has no record of it an
    hour later, and its trade log shows a 60-share BUY for exactly that
    order. Another order of our size on the same market is not ours."""
    ts = time.time() - 3500                      # inside [placed-30s, placed+20min]
    pmus_ = _Pmus([_fill(qty=60.0, order_id="o-77", order_qty=100.0, order_price=0.48, ts=ts),
                   _fill(qty=100.0, order_id="owner-9", order_qty=100.0, order_price=0.48, ts=ts)],
                  status=None)
    pool = _Pool()
    asyncio.run(le._reap_one_submitting_row(pool, pmus_, _row(order_id="o-77", age_s=4000.0)))
    assert [q for q in pool.queries if "venue has no record of order" in str(q[1])]
    fill = [q for q in pool.queries if "status='filled'" in q[0]]
    assert fill and fill[0][1][1] == 60.0 and "o-77" in fill[0][1][4]


def test_a_named_no_record_row_is_revisited_and_books_when_the_log_names_the_fill():
    import inspect

    pmus_ = _Pmus([_fill(qty=100.0, order_id="o-77", order_qty=100.0, order_price=0.48,
                         ts=time.time() - 4500)],
                  status=None)
    pool = _Pool()
    r = _row(order_id="o-77", age_s=5000.0, status="error",
             error="venue has no record of order o-77 after 3660s — reconcile "
                   "against the venue account")
    asyncio.run(le._reap_one_submitting_row(pool, pmus_, r))
    fill = [q for q in pool.queries if "status='filled'" in q[0]]
    assert fill and fill[0][1][1] == 100.0
    assert "status IN ('submitting', 'error')" in fill[0][0]
    # and while it stands it is a NAMED row everywhere a named row matters
    from sportsassets.workers import copy_sweep
    from sportsassets.api import app as app_mod
    assert "venue has no record of order%" in inspect.getsource(le._reap_stale_submitting)
    assert "venue has no record of order%" in inspect.getsource(le._entry_in_flight)
    assert inspect.getsource(le.maybe_execute).count("venue has no record of order%") >= 2
    assert inspect.getsource(copy_sweep).count("venue has no record of order%") >= 2
    assert inspect.getsource(app_mod._rest_lane_gate).count("venue has no record of order%") >= 2


def test_an_unreadable_book_means_no_rest(monkeypatch):
    """Round eleven (major, reproduced): an unreadable book plus a lost
    placement response -- one venue fault -- adopted, cancelled and
    BOOKED the owner's own bid. A lane that cannot see the book does
    not place on it."""
    stops = []
    monkeypatch.setattr(le, "_copy_stop", lambda *a, **k: stops.append(a))

    class _Blind(_RestPmus):
        def open_orders(self, slugs=None):
            self.calls.append(("open_orders",))
            raise RuntimeError("502")

    p = _Blind({})
    out = asyncio.run(le._rest_cycle(_Pool(), p, 11, SLUG, 0.48, 100.0,
                                     "ORDER_INTENT_BUY_LONG", "rn1", 0.48))
    assert out is None
    assert ("place",) not in p.calls
    assert p.calls.count(("open_orders",)) == 2
    assert ("rest_book_unreadable", "rn1") in stops


def test_the_snapshot_is_persisted_before_the_order_goes_out(monkeypatch):
    monkeypatch.setattr(le, "_copy_stop", lambda *a, **k: None)
    x = {"order_id": "pre-X", "us_market_slug": SLUG, "side": "BUY",
         "price": 0.48, "quantity": 100.0, "created_at": _iso(20.0)}
    p = _RestPmus(x)
    pool = _Pool()
    asyncio.run(le._rest_cycle(pool, p, 11, SLUG, 0.48, 100.0,
                               "ORDER_INTENT_BUY_LONG", "rn1", 0.48))
    pre = [q for q in pool.queries if "COALESCE(raw, '{}'::jsonb) || $2::jsonb" in q[0]]
    assert pre and pre[0][1] == (11, '{"pre_ids": ["pre-X"]}')
    assert p.calls.index(("open_orders",)) < p.calls.index(("place",))


def test_only_a_fresh_detection_clears_the_price_path():
    import inspect

    src = inspect.getsource(le.maybe_execute)
    i = src.index("DELETE FROM price_path WHERE row_id = $1")
    assert "if reaction is not None:" in src[i - 400:i]
