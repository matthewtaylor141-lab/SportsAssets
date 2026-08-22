"""Desk relay tickets (migration 027): rows carry action 'buy'|'sell'.
Sells FAIL CLOSED — clamped to the venue's own held count, refused when
nothing is held or the venue won't answer — and a filled sell is booked
exactly once (inline record + kalshi_inline marker, honored by
sync_kalshi_fills' sell branch the way its buy branch always has)."""

from edge.execution.executor import sync_kalshi_fills
from edge.shadow.runner import desk_execute_order


class _Ledger:
    def __init__(self):
        self.state = {"kalshi_sync_cutover": {"ts": 1000.0}}
        self.fills = []

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value

    def record_fill(self, **kw):
        self.fills.append(kw)
        return {"applied": True}


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Kalshi:
    name = "kalshi"

    def __init__(self, held=None, fills=None,
                 order=("ord-1", 5)):
        self._held = held          # None = venue won't answer (fetch fail)
        self._order = order
        self.placed = []
        self._sess = self
        self._fills = fills or []

    # venue truth for the clamp
    def open_ticker_map(self):
        if self._held is None:
            return None
        return {"positions": set(self._held),
                "resting_buys": {}, "position_costs": {},
                "position_qty": dict(self._held)}

    def place_order(self, ticker, price, count, client_order_id,
                    taker, sell=False, rest_s=900):
        self.placed.append({"ticker": ticker, "price": price,
                            "count": count, "coid": client_order_id,
                            "taker": taker, "sell": sell})
        oid, filled = self._order
        return {"ok": True, "order_id": oid, "status": "executed",
                "price": price, "count": min(filled, count),
                "taker": taker, "sell": sell, "raw": {}}

    # the sync path's venue surface
    def get(self, url, params=None, headers=None, timeout=None):
        return _Resp({"fills": self._fills})

    def _auth_headers(self, *a, **k):
        return {}

    def taker_fee(self, price):
        return 0.0


def test_sell_refuses_when_nothing_held():
    k = _Kalshi(held={})
    led = _Ledger()
    res = desk_execute_order(k, led, {
        "id": 7, "ticker": "KXNBA-A", "limit_price": 0.40,
        "count": 3, "action": "sell"})
    assert res["status"] == "error"
    assert res["error"] == "nothing held"
    assert k.placed == [], "a refusal places nothing"
    assert led.fills == []


def test_sell_refuses_when_venue_positions_unavailable():
    k = _Kalshi(held=None)                 # open_ticker_map -> None
    res = desk_execute_order(k, _Ledger(), {
        "id": 8, "ticker": "KXNBA-A", "limit_price": 0.40,
        "count": 3, "action": "sell"})
    assert res["status"] == "error"
    assert "unavailable" in res["error"]
    assert k.placed == []


def test_sell_clamps_to_held_and_records_once():
    k = _Kalshi(held={"KXNBA-A": 5}, order=("ord-1", 5))
    led = _Ledger()
    res = desk_execute_order(k, led, {
        "id": 9, "ticker": "KXNBA-A", "limit_price": 0.40,
        "count": 12, "action": "sell"})
    assert res["status"] == "filled"
    assert k.placed == [{"ticker": "KXNBA-A", "price": 0.40, "count": 5,
                         "coid": "desk-9", "taker": True, "sell": True}]
    assert len(led.fills) == 1
    f = led.fills[0]
    assert (f["side"], f["qty"], f["fill_uid"]) == ("SELL", 5.0, "desk-9")
    assert led.state["kalshi_inline:ord-1"]["uid"] == "desk-9", \
        "marker parked BEFORE the record, same as desk buys"


def test_sell_count_omitted_sells_all_held():
    k = _Kalshi(held={"KXNBA-A": 4}, order=("ord-2", 4))
    res = desk_execute_order(k, _Ledger(), {
        "id": 10, "ticker": "KXNBA-A", "limit_price": 0.40,
        "action": "sell"})
    assert res["status"] == "filled"
    assert k.placed[0]["count"] == 4


def test_row_without_action_is_a_buy():
    k = _Kalshi(order=("ord-3", 2))
    led = _Ledger()
    res = desk_execute_order(k, led, {
        "id": 11, "ticker": "KXNBA-A", "limit_price": 0.40, "count": 2})
    assert res["status"] == "filled"
    assert k.placed[0]["sell"] is False, \
        "pre-027 rows carry no action and stay buys"
    assert led.fills[0]["side"] == "BUY"


def test_kill_switch_still_stops_the_desk():
    k = _Kalshi(held={"KXNBA-A": 5})
    led = _Ledger()
    led.state["kill_switch"] = True
    res = desk_execute_order(k, led, {
        "id": 12, "ticker": "KXNBA-A", "limit_price": 0.40,
        "count": 1, "action": "sell"})
    assert res["status"] == "error"
    assert "kill_switch" in res["error"]
    assert k.placed == []


def test_sync_never_rebooks_a_marked_desk_sell():
    sell_fill = {"action": "sell", "side": "yes", "ticker": "KXNBA-A",
                 "order_id": "ord-1", "trade_id": "t1",
                 "count_fp": "5.00", "yes_price_dollars": "0.400000",
                 "created_time_ts": 2000.0}
    k = _Kalshi(held={"KXNBA-A": 5}, fills=[sell_fill],
                order=("ord-1", 5))
    led = _Ledger()
    desk_execute_order(k, led, {
        "id": 9, "ticker": "KXNBA-A", "limit_price": 0.40,
        "count": 5, "action": "sell"})
    assert len(led.fills) == 1
    n = sync_kalshi_fills(k, led, "LIVE_BETA")
    assert n == 0
    assert len(led.fills) == 1, \
        "the kalshi_inline marker keeps the sale booked exactly once"


def test_sync_still_books_the_underdog_exit_sell():
    """Regression guard: the marker skip must not swallow the sleeve's
    resting-exit accounting (executor.py's original sell branch)."""
    sell_fill = {"action": "sell", "side": "yes", "ticker": "KXNBA-A",
                 "order_id": "kud-ord", "trade_id": "t2",
                 "count_fp": "3.00", "yes_price_dollars": "0.600000",
                 "created_time_ts": 2000.0}
    k = _Kalshi(fills=[sell_fill])
    led = _Ledger()
    led.state["kalshi_order:kud-ord"] = {
        "market_key": "kalshi:KXNBA-A",
        "category": "kalshi_underdog_exit", "task_id": 1, "entry": 0.5}
    n = sync_kalshi_fills(k, led, "LIVE_BETA")
    assert n == 1
    assert led.fills[0]["side"] == "SELL"
    assert led.state["kud:KXNBA-A"]["status"] == "cashed_out"
