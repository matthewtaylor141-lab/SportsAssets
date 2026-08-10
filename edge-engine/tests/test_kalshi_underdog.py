"""Kalshi leg of the $1 underdog sleeve: platform queue -> dog entry ->
resting +20% maker exit -> async cash-out bookkeeping."""

import tempfile
import time

import requests

from edge.ledger.service import Ledger
from edge.shadow.kalshi_underdog import contracts_for, sweep, threshold_price
from edge.venues.base import BookLevel, MarketBook
from edge.venues.mapper import VenueMarket

_EV = "KXWNBAGAME-26AUG08DALCHI"
_OUTCOMES = {"Dallas Wings": f"{_EV}-DAL", "Chicago Sky": f"{_EV}-CHI"}


class _Kalshi:
    name = "kalshi"

    def __init__(self, ask, fav_ask=0.70):
        # The dog is picked from the venue's own book now, so both
        # sides quote: `ask` is the task's nominal dog (CHI), `fav_ask`
        # the other side. Pass a dict to control tickers directly.
        if isinstance(ask, dict):
            self.asks = ask
        else:
            self.asks = {f"{_EV}-CHI": ask, f"{_EV}-DAL": fav_ask}
        self.orders = []

    def taker_fee(self, price):
        return 0.07 * price * (1 - price)

    def discover_markets(self, league_codes):
        return [VenueMarket(market_id=_EV, title="Wings at Sky",
                            league_code="wnba",
                            outcome_tokens=dict(_OUTCOMES))]

    def get_book(self, market_id, ticker):
        a = self.asks.get(ticker)
        return MarketBook(venue=self.name, market_id=market_id,
                          outcome_id=ticker, bids=[],
                          asks=([BookLevel(a, 60)] if a is not None else []),
                          ts=time.time())

    def place_order(self, ticker, price, count, client_order_id="",
                    taker=True, sell=False, rest_s=900):
        self.orders.append((ticker, price, count, taker, sell))
        return {"ok": True, "count": count, "price": price,
                "order_id": f"ord-{len(self.orders)}", "status": "filled"}


class _Sess:
    """Captures the platform round-trips the sweep makes."""

    def __init__(self, tasks):
        self.tasks = tasks
        self.posts = []

    def get(self, url, **kw):
        class R:
            status_code = 200

            def json(inner):
                return {"tasks": self.tasks}
        return R()

    def post(self, url, json=None, **kw):
        self.posts.append((url.rsplit("/", 1)[-1], json))
        class R:
            status_code = 200
        return R()


_TASK = {"id": 7, "game_slug": "wnba-dal-chi-2026-08-08", "league": "wnba",
         "dog_outcome": "Chicago Sky", "other_outcome": "Dallas Wings",
         "per_fill_usd": 1.0, "take_profit": 0.20}


def _run(ask, tasks=None, led=None, monkeypatch=None):
    led = led or Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(ask)
    sess = _Sess([dict(_TASK)] if tasks is None else tasks)
    monkeypatch.setattr(requests, "Session", lambda: sess)
    st = sweep(kalshi=ka, ledger=led, base="http://api", token="t",
               live=True)
    return st, ka, led, sess


def test_threshold_is_twenty_percent_capped_at_99c():
    assert threshold_price(0.30) == 0.36
    assert threshold_price(0.25) == 0.30
    assert threshold_price(0.32) == 0.38     # 0.384 rounds down
    assert threshold_price(0.90) == 0.99     # cap
    # A cheap dog's +20% rounds back to its own entry (0.02*1.2=0.024);
    # the floor keeps the exit a real tick above it.
    assert threshold_price(0.02) == 0.03


def test_contracts_never_exceed_the_dollar():
    assert contracts_for(1.00, 0.25) == 4
    assert contracts_for(1.00, 0.30) == 3
    assert contracts_for(1.00, 0.48) == 2
    assert contracts_for(1.00, 0.0) == 0


def test_entry_buys_the_dog_and_rests_the_exit(monkeypatch):
    st, ka, led, sess = _run(0.30, monkeypatch=monkeypatch)
    assert st["placed"] == 1
    # Taker buy LIMIT at ask+2c (crash protection; the IOC fills at the
    # book price), then the maker sell rests at +20% ON THE ASK — basing
    # the target on the limit parked every exit ~7% too high.
    assert ka.orders[0] == (f"{_EV}-CHI", 0.32, 3, True, False)
    assert ka.orders[1] == (f"{_EV}-CHI", 0.36, 3, False, True)
    assert st["resting"] == 1
    kud = led.get_state(f"kud:{_EV}-CHI")
    assert kud["status"] == "open" and kud["thr"] == 0.36
    statuses = [j["status"] for _, j in sess.posts]
    assert statuses == ["filled"]
    # The parked exit context carries the accounting the fill sync needs.
    rest = led.get_state(f"kud_rest:{_EV}-CHI")
    ctx = led.get_state(f"kalshi_order:{rest['order_id']}")
    assert ctx["category"] == "kalshi_underdog_exit" and ctx["entry"] == 0.30


def test_band_gate_refuses_favorites_and_junk(monkeypatch):
    for ask in (0.55, 0.02):
        st, ka, _, sess = _run(ask, monkeypatch=monkeypatch)
        assert st.get("band_fail") == 1 and not ka.orders
        assert sess.posts[0][1]["status"] == "band_fail"


def test_dog_is_the_venues_cheaper_side_not_the_tasks(monkeypatch):
    """The queue's dog_outcome is Polymarket's opinion; Kalshi's book
    decides. When DAL is the cheaper side on Kalshi, DAL is the dog."""
    ka_asks = {f"{_EV}-CHI": 0.60, f"{_EV}-DAL": 0.35}
    st, ka, _, sess = _run(ka_asks, monkeypatch=monkeypatch)
    assert st["placed"] == 1
    assert ka.orders[0][0] == f"{_EV}-DAL"
    assert ka.orders[0][1] == 0.37            # ask + 2c crash protection


def test_fifty_fifty_book_has_no_dog(monkeypatch):
    ka_asks = {f"{_EV}-CHI": 0.50, f"{_EV}-DAL": 0.50}
    st, ka, _, sess = _run(ka_asks, monkeypatch=monkeypatch)
    assert st.get("band_fail") == 1 and not ka.orders


def test_window_waits_fires_and_expires(monkeypatch):
    now = time.time()
    # Too early: stays queued, nothing reported.
    early = {**_TASK, "start_ts": now + 3600}
    st, ka, _, sess = _run(0.30, tasks=[early], monkeypatch=monkeypatch)
    assert st.get("waiting") == 1 and not ka.orders and not sess.posts
    # Inside T-minus-5: fires.
    open_w = {**_TASK, "start_ts": now + 120}
    st, ka, _, sess = _run(0.30, tasks=[open_w], monkeypatch=monkeypatch)
    assert st["placed"] == 1
    # Window long closed: reported missed, never entered in-play.
    # 4h after start clears the widened 60-minute grace (2026-08-10)
    # with headroom — keep this fixture beyond any future grace bump.
    late = {**_TASK, "start_ts": now - 4 * 3600}
    st, ka, _, sess = _run(0.30, tasks=[late], monkeypatch=monkeypatch)
    assert st.get("missed") == 1 and not ka.orders
    assert sess.posts[0][1]["status"] == "missed"


def test_in_window_miss_retries_instead_of_reporting(monkeypatch):
    """A timed task that fails inside its window stays queued for the
    next sweep — no terminal report, no permanent skip. Only a task
    with no start (single shot) reports its failure immediately."""
    now = time.time()
    timed = {**_TASK, "start_ts": now + 60}
    ka_asks = {f"{_EV}-CHI": 0.50, f"{_EV}-DAL": 0.50}  # no dog yet
    st, ka, _, sess = _run(ka_asks, tasks=[timed], monkeypatch=monkeypatch)
    assert st.get("retry_band_fail") == 1 and not sess.posts
    # Same book, no start_ts: one shot, terminal report.
    st, ka, _, sess = _run(ka_asks, monkeypatch=monkeypatch)
    assert sess.posts[0][1]["status"] == "band_fail"


def test_oversized_queue_row_is_clamped_to_one_dollar(monkeypatch):
    """2026-08-10 evening: settled entries averaged ~$11.66 against the
    owner's $1 directive — queue rows carried oversized per_fill_usd.
    The engine clamps at the point of order: a $10 row buys exactly what
    a $1 row buys."""
    now = time.time()
    big = {**_TASK, "per_fill_usd": 10.0, "start_ts": now + 60}
    st, ka, _, sess = _run(0.30, tasks=[big], monkeypatch=monkeypatch)
    assert st["placed"] == 1
    # Entry is a taker limit at ask+2c = 0.32: $1 buys 3 contracts —
    # NOT the 31 a $10 budget would.
    buy = [o for o in ka.orders if not o[4]][0]
    assert buy[2] == 3, f"clamp failed: bought {buy[2]} contracts"


def test_discovery_failure_is_retryable_even_for_oneshot(monkeypatch):
    """The boot stagger is gone (2026-08-10), so the first sweep can run
    against a cold adapter — a DISCOVERY failure must never burn a
    no-start_ts task's single shot as 'no_market'. Only a successful
    discovery that finds no matching market stays terminal for oneshots."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(0.30)

    def _boom(league_codes):
        raise RuntimeError("venue 500")

    ka.discover_markets = _boom
    sess = _Sess([dict(_TASK)])
    monkeypatch.setattr(requests, "Session", lambda: sess)
    st = sweep(kalshi=ka, ledger=led, base="http://api", token="t",
               live=True)
    assert st.get("retry_discovery") == 1
    assert not sess.posts, "nothing terminal may be reported"
    assert not ka.orders


def test_existing_position_on_the_game_vetoes_entry(monkeypatch):
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    # A copy already holds the OTHER team of the same game.
    led.record_fill(fill_uid="c1", venue="kalshi",
                    market_key=f"kalshi:{_EV}-DAL", side="BUY", qty=5,
                    price=0.60, mode="LIVE_BETA", category="kalshi_copy")
    st, ka, _, sess = _run(0.30, led=led, monkeypatch=monkeypatch)
    assert st.get("held") == 1 and not ka.orders
    assert sess.posts[0][1]["status"] == "held"


def test_exit_events_report_the_cash_out(monkeypatch):
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    led.set_state("kud_exit_events", {"events": [
        {"task_id": 7, "ticker": f"{_EV}-CHI", "qty": 3,
         "price": 0.38, "entry": 0.32}]})
    st, _, led, sess = _run(0.30, tasks=[], led=led,
                            monkeypatch=monkeypatch)
    assert st["cashed_out"] == 1
    kind, body = sess.posts[0]
    assert kind == "kud-result" and body["status"] == "cashed_out"
    assert body["pnl"] == 0.18                       # (0.38-0.32)*3
    assert led.get_state("kud_exit_events") == {"events": []}


def test_sell_fill_sync_books_the_cash_out(monkeypatch):
    """The resting sell fills asynchronously: sync must record the SELL
    (realizing against avg cost), flip the sleeve state, and enqueue the
    report event — and must ignore sells it did not place."""
    from edge.execution.executor import sync_kalshi_fills

    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    tk = f"{_EV}-CHI"
    led.record_fill(fill_uid="kud-7", venue="kalshi",
                    market_key=f"kalshi:{tk}", side="BUY", qty=3,
                    price=0.32, mode="LIVE_BETA",
                    category="kalshi_underdog")
    led.set_state(f"kud:{tk}", {"task_id": 7, "qty": 3, "entry": 0.32,
                                "thr": 0.38, "status": "open"})
    led.set_state("kalshi_order:x9", {
        "market_key": f"kalshi:{tk}", "category": "kalshi_underdog_exit",
        "task_id": 7, "entry": 0.32, "qty": 3, "thr": 0.38})

    class _Adapter:
        def _auth_headers(self, *a):
            return {}

        class _S:
            def get(self, url, **kw):
                class R:
                    status_code = 200

                    def json(inner):
                        return {"fills": [
                            {"action": "sell", "side": "yes",
                             "ticker": tk, "order_id": "x9",
                             "count": 3, "yes_price": 38,
                             "trade_id": "t1"},
                            # A sell this process never placed: ignored.
                            {"action": "sell", "side": "yes",
                             "ticker": "KXOTHER-1-A", "order_id": "zz",
                             "count": 2, "yes_price": 50,
                             "trade_id": "t2"},
                        ]}
                return R()
        _sess = _S()

        def taker_fee(self, price):
            return 0.07 * price * (1 - price)

    n = sync_kalshi_fills(_Adapter(), led, "LIVE_BETA")
    assert n == 1
    assert led.get_state(f"kud:{tk}")["status"] == "cashed_out"
    evs = led.get_state("kud_exit_events")["events"]
    assert evs == [{"task_id": 7, "ticker": tk, "qty": 3.0,
                    "price": 0.38, "entry": 0.32}]
    # The ledger realized the +20%: (0.38 - 0.32) * 3 = $0.18.
    pos = {p["market_key"]: p for p in led.open_positions(live_only=True)}
    assert f"kalshi:{tk}" not in pos or \
        float(pos[f"kalshi:{tk}"].get("shares") or 0) == 0
