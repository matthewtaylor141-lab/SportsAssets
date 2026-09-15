"""pmus.recent_trades reads the venue's OWN row shape, driven for real.

Round eight (2026-09-02): the first version parsed the fill quantity as
an Amount object; the venue's Trade.qty is a bare string (the SDK's own
TypedDict, and the repo's fixture in test_pmus_account.py), so it raised
on exactly the rows that mattered -- and every reaper pass read that as
"unreadable" and left the lost fill unbooked forever. The top-level side
is always None in the feed; the definitive side lives on the nested
execution order. This file drives the real function over a fake SDK
client with the venue's shape.
"""
import pytest

from sportsassets import pmus


class _Portfolio:
    def __init__(self, pages):
        self.pages = list(pages)
        self.params: list[dict] = []

    def activities(self, params):
        self.params.append(dict(params))
        i = len(self.params) - 1
        if i >= len(self.pages):
            return {"activities": [], "eof": True}
        page = self.pages[i]
        return {"activities": page,
                "nextCursor": f"c{i + 1}" if i + 1 < len(self.pages) else "",
                "eof": i + 1 >= len(self.pages)}


class _Orders:
    """The venue's order record, for the reaper's reconcile-by-id."""

    def __init__(self, records):
        self.records = dict(records)
        self.cancelled: list[str] = []

    def retrieve(self, oid):
        o = self.records.get(oid)
        return {"order": o} if o else {}

    def cancel(self, oid, params=None):
        self.cancelled.append(oid)
        return {}


class _Client:
    def __init__(self, pages, orders=None):
        self.portfolio = _Portfolio(pages)
        self.orders = _Orders(orders or {})


def _trade(slug, ts_iso, qty="100", price="0.47", side=None, exec_side=None,
           rp="0", order=None):
    t = {"createTime": ts_iso, "marketSlug": slug, "qty": qty,
         "price": {"value": price, "currency": "USD"},
         "realizedPnl": {"value": rp, "currency": "USD"}}
    if side:
        t["side"] = side
    if exec_side or order:
        o = dict(order or {})
        if exec_side:
            o["side"] = exec_side
        t["aggressorExecution"] = {"order": o}
    return {"type": "ACTIVITY_TYPE_TRADE", "trade": t}


@pytest.fixture
def client(monkeypatch):
    def _bind(pages):
        c = _Client(pages)
        monkeypatch.setattr(pmus, "_client", c)
        return c
    return _bind


SINCE = 1_785_000_000.0
T = "2026-07-26T00:00:00Z"          # epoch 1785024000, inside the window


def test_the_venues_string_qty_and_nested_side_are_read(client):
    c = client([[_trade("s", T, qty="100", exec_side="TRADE_SIDE_BUY"),
                 _trade("s", T, qty="40", exec_side="TRADE_SIDE_SELL", rp="0"),
                 _trade("other", T, qty="7", exec_side="TRADE_SIDE_BUY"),
                 {"type": "ACTIVITY_TYPE_ACCOUNT_DEPOSIT"}]])
    out = pmus.recent_trades("s", SINCE)
    assert [(f["qty"], f["price"], f["side"]) for f in out] == [
        (100.0, 0.47, "TRADE_SIDE_BUY"), (40.0, 0.47, "TRADE_SIDE_SELL")]
    assert all(f["ts"] > 0 for f in out)
    assert c.portfolio.params[0]["marketSlug"] == "s"
    assert c.portfolio.params[0]["types"] == ["ACTIVITY_TYPE_TRADE"]


def test_a_row_naming_no_side_reads_as_no_side(client):
    client([[_trade("s", T)]])
    assert pmus.recent_trades("s", SINCE)[0]["side"] == ""


def test_a_top_level_side_still_wins_when_present(client):
    client([[_trade("s", T, side="SIDE_SELL", exec_side="TRADE_SIDE_BUY")]])
    assert pmus.recent_trades("s", SINCE)[0]["side"] == "SIDE_SELL"


def test_paging_stops_at_the_window_and_truncation_raises(client):
    old = "2026-07-01T00:00:00Z"
    c = client([[_trade("s", T)], [_trade("s", old)], [_trade("s", old)]])
    out = pmus.recent_trades("s", SINCE)
    assert len(out) == 1 and len(c.portfolio.params) == 2   # stopped once past since
    c = client([[_trade("s", T)]] * 5)
    with pytest.raises(RuntimeError):
        pmus.recent_trades("s", SINCE, max_pages=3)         # never reached since


def test_a_venue_error_propagates_rather_than_reading_as_no_fills(monkeypatch):
    class _Boom:
        class portfolio:
            @staticmethod
            def activities(params):
                raise RuntimeError("503")
    monkeypatch.setattr(pmus, "_client", _Boom())
    with pytest.raises(RuntimeError):
        pmus.recent_trades("s", SINCE)


def test_the_ledger_reaper_books_from_the_real_parser(monkeypatch):
    """End to end through _adopt_lost_bid with the REAL recent_trades over
    the venue's shape: string qty, nested BUY side, createTime in the
    trade object."""
    import asyncio
    import datetime as _dt

    from sportsassets import live_executor as le

    now = _dt.datetime.now(tz=_dt.timezone.utc)
    ts_iso = (now - _dt.timedelta(seconds=700)).isoformat().replace("+00:00", "Z")
    amt = {"value": "0.48", "currency": "USD"}
    c = _Client([[_trade("aec-x-y-2026-09-01", ts_iso, qty="100", price="0.48",
                         exec_side="ORDER_SIDE_BUY",
                         order={"id": "o-9", "quantity": 100, "price": amt})]],
                orders={"o-9": {"id": "o-9", "marketSlug": "aec-x-y-2026-09-01",
                                "state": "ORDER_STATE_FILLED", "price": amt,
                                "quantity": 100, "cumQuantity": 100,
                                "leavesQuantity": 0, "avgPx": amt,
                                "intent": "ORDER_INTENT_BUY_LONG"}})
    monkeypatch.setattr(pmus, "_client", c)
    monkeypatch.setattr(pmus, "open_orders", lambda slugs=None: [])

    async def _held(_slug):
        return 100, 0.48

    monkeypatch.setattr(le, "_pm_held", _held)

    class _Row(dict):
        def keys(self):
            return list(super().keys())

    class _Pool:
        def __init__(self):
            self.queries = []

        async def fetchval(self, sql, *a):
            return 0.0

        async def fetch(self, sql, *a):
            return []

        async def execute(self, sql, *a):
            self.queries.append((sql, a))

    pool = _Pool()
    row = _Row(id=11, order_id=None, us_market_slug="aec-x-y-2026-09-01",
               requested_shares=100.0, intent="ORDER_INTENT_BUY_LONG",
               his_price=0.48, age_s=1200.0)
    out = asyncio.run(le._adopt_lost_bid(pool, pmus, row, 1200.0))
    assert out == "booked"
    # the row took the order's id and was written from the venue's
    # ORDER record (the real pmus.order_status over the fake client)
    adopt = [q for q in pool.queries if "SET order_id=$2" in q[0]]
    assert adopt and adopt[0][1] == (11, "o-9")
    fill = [q for q in pool.queries if "status='filled'" in q[0]]
    assert fill and fill[0][1][1] == 100.0 and fill[0][1][2] == 0.48
    assert c.orders.cancelled == ["o-9"]        # cancel is a no-op on a done order


def test_a_fill_whose_order_is_not_our_size_is_the_owners(monkeypatch):
    """The same feed shape, his 500-share order partially filled for
    100 at our cent inside our window: named, never booked."""
    import asyncio
    import datetime as _dt

    from sportsassets import live_executor as le

    now = _dt.datetime.now(tz=_dt.timezone.utc)
    ts_iso = (now - _dt.timedelta(seconds=700)).isoformat().replace("+00:00", "Z")
    amt = {"value": "0.48", "currency": "USD"}
    c = _Client([[_trade("aec-x-y-2026-09-01", ts_iso, qty="100", price="0.48",
                         exec_side="ORDER_SIDE_BUY",
                         order={"id": "his", "quantity": 500, "price": amt})]])
    monkeypatch.setattr(pmus, "_client", c)
    monkeypatch.setattr(pmus, "open_orders", lambda slugs=None: [])

    async def _held(_slug):
        return 100, 0.48

    monkeypatch.setattr(le, "_pm_held", _held)

    class _Row(dict):
        def keys(self):
            return list(super().keys())

    class _Pool:
        def __init__(self):
            self.queries = []

        async def fetchval(self, sql, *a):
            return 0.0

        async def fetch(self, sql, *a):
            return []

        async def execute(self, sql, *a):
            self.queries.append((sql, a))

    pool = _Pool()
    row = _Row(id=11, order_id=None, us_market_slug="aec-x-y-2026-09-01",
               requested_shares=100.0, intent="ORDER_INTENT_BUY_LONG",
               his_price=0.48, age_s=1200.0)
    out = asyncio.run(le._adopt_lost_bid(pool, pmus, row, 1200.0))
    assert out == "position"
    assert not [q for q in pool.queries if "status='filled'" in q[0]]
    assert not c.orders.cancelled
