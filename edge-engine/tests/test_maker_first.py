"""Maker-first execution on Polymarket US: rest inside the spread instead of
crossing it, reconcile the fill from the activity feed, and make sure an
order that never fills gives its event back."""

import time

import pytest

from edge.execution.executor import (
    PMUS_ORDER_PREFIX,
    execute,
    market_key,
    reap_pmus_makers,
    sync_pmus_fills,
)
from edge.ledger.service import Ledger
from edge.venues.base import BookLevel, MarketBook
from edge.venues.polymarket_us import PolymarketUSAdapter


def _book(bid, ask, size=500):
    return MarketBook(venue="polymarket-us", market_id="evt", outcome_id="slug-x",
                      bids=[BookLevel(bid, size)] if bid else [],
                      asks=[BookLevel(ask, size)], ts=time.time())


def _adapter(maker_first=True, monkeypatch=None):
    a = PolymarketUSAdapter.__new__(PolymarketUSAdapter)
    a.book_errors = {}
    a._taker_fee = 0.0
    a._maker_fee_rate = 0.0
    a._maker_first = maker_first
    a._force_taker = {}
    a._stream = None
    a._auth = None
    return a


# ── entry pricing ───────────────────────────────────────────────────────

def test_rests_one_tick_inside_a_wide_spread():
    px, taker = _adapter().plan_entry(_book(0.40, 0.50))
    assert (px, taker) == (0.49, False)


def test_never_queues_behind_the_book():
    """With a two-tick spread the best resting price IS the best bid."""
    px, taker = _adapter().plan_entry(_book(0.45, 0.46))
    assert (px, taker) == (0.45, False)


def test_one_tick_market_falls_back_to_crossing():
    # ask - tick == 0.44 < bid 0.45 -> clamps to the bid, which equals... the
    # bid; but a 1c-wide book leaves no room to improve, so we take.
    px, taker = _adapter().plan_entry(_book(0.45, 0.45))
    assert taker is True and px == 0.45


def test_penny_ask_has_no_room_to_rest():
    px, taker = _adapter().plan_entry(_book(0.0, 0.01))
    assert (px, taker) == (0.01, True)


def test_maker_first_can_be_switched_off():
    px, taker = _adapter(maker_first=False).plan_entry(_book(0.40, 0.50))
    assert (px, taker) == (0.50, True)


def test_resting_price_is_strictly_better_than_the_ask():
    """The volume claim in one assertion: every book with room to rest gives
    an entry price below the ask, so the edge at entry is strictly larger."""
    a = _adapter()
    for bid, ask in [(0.10, 0.20), (0.33, 0.40), (0.60, 0.75), (0.80, 0.90)]:
        px, taker = a.plan_entry(_book(bid, ask))
        assert taker is False and px < ask


def test_default_adapter_contract_crosses_the_spread():
    """Venues that can't rest must keep paying the ask — the base class is
    what guarantees maker pricing never leaks into an adapter that lacks it."""
    from edge.venues.kalshi import KalshiAdapter

    px, taker = KalshiAdapter.plan_entry(KalshiAdapter.__new__(KalshiAdapter),
                                         _book(0.40, 0.50))
    assert (px, taker) == (0.50, True)


# ── order placement ─────────────────────────────────────────────────────

class _FakeOrders:
    def __init__(self, resp):
        self.resp = resp
        self.sent = []
        self.cancelled = []
        self.open: list[dict] = []

    def create(self, params):
        self.sent.append(params)
        return self.resp

    def preview(self, params):
        raise AssertionError("maker orders must not spend a preview round-trip")

    def cancel(self, order_id, params):
        self.cancelled.append((order_id, params))

    def list(self, params=None):
        return {"orders": self.open}


def _wire(adapter, orders):
    import types

    adapter._auth = types.SimpleNamespace(orders=orders)
    return adapter


def test_maker_order_is_gtc_and_post_only():
    a = _adapter()
    orders = _FakeOrders({"id": "o1", "executions": [
        {"order": {"state": "ORDER_STATE_NEW"}, "type": "EXECUTION_TYPE_NEW"}]})
    _wire(a, orders)
    r = a.place_order("slug-x", 0.49, 2, preview=False,
                      tif="TIME_IN_FORCE_GOOD_TILL_CANCEL", post_only=True)
    sent = orders.sent[0]
    assert sent["tif"] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
    assert sent["participateDontInitiate"] is True   # can never cross
    assert sent["synchronousExecution"] is False
    assert r["ok"] and r["resting"] and r["count"] == 0 and r["taker"] is False


def test_rejected_maker_order_is_not_treated_as_resting():
    a = _adapter()
    _wire(a, _FakeOrders({"id": "o1", "executions": [
        {"order": {"state": "ORDER_STATE_REJECTED"},
         "type": "EXECUTION_TYPE_REJECTED"}]}))
    r = a.place_order("slug-x", 0.49, 2, preview=False,
                      tif="TIME_IN_FORCE_GOOD_TILL_CANCEL", post_only=True)
    assert r["ok"] is False and r["resting"] is False


def test_taker_order_keeps_fok_semantics():
    a = _adapter()
    orders = _FakeOrders({"id": "o2", "executions": [
        {"order": {"state": "ORDER_STATE_FILLED"}, "type": "EXECUTION_TYPE_FILL",
         "lastPx": {"value": "0.47"}, "lastShares": "3"}]})
    _wire(a, orders)
    r = a.place_order("slug-x", 0.47, 3, preview=False)
    assert orders.sent[0]["tif"] == "TIME_IN_FORCE_FILL_OR_KILL"
    assert orders.sent[0]["synchronousExecution"] is True
    assert "participateDontInitiate" not in orders.sent[0]
    assert r["ok"] and r["count"] == 3 and r["taker"] is True


# ── executor: resting is not a fill ─────────────────────────────────────

def _exec_maker(ledger, adapter, size_usd=1.0):
    return execute(adapter=adapter, ledger=ledger, mode="LIVE_BETA",
                   mkey=market_key("polymarket-us", "slug-x"), league="nba",
                   ask_price=0.50, ask_size=500, size_usd=size_usd,
                   edge=0.03, threshold=0.02, decision={"fair_value": 0.53},
                   ts=1000.0, entry_price=0.49, taker=False, event_key="ev1")


def test_resting_order_writes_no_ledger_fill_but_parks_its_context(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    a = _adapter()
    _wire(a, _FakeOrders({"id": "o1", "executions": [
        {"order": {"state": "ORDER_STATE_NEW"}, "type": "EXECUTION_TYPE_NEW"}]}))
    r = _exec_maker(led, a, size_usd=2.0)
    assert r["status"] == "resting_maker" and r["filled_usd"] == 0.0
    assert led.summary()["fills"] == 0            # nothing has traded yet
    ctx = led.get_state(f"{PMUS_ORDER_PREFIX}slug-x")
    assert ctx["order_id"] == "o1" and ctx["event_key"] == "ev1"
    assert ctx["px"] == 0.49 and ctx["count"] == 4  # $2 / 49c


def test_taker_execution_clears_a_stale_maker_context(tmp_path):
    """Otherwise the reconciler would find the context and record the taker
    fill a second time from the activity feed."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.set_state(f"{PMUS_ORDER_PREFIX}slug-x", {"order_id": "old"})
    a = _adapter()
    _wire(a, _FakeOrders({"id": "o9", "executions": [
        {"order": {"state": "ORDER_STATE_FILLED"}, "type": "EXECUTION_TYPE_FILL",
         "lastPx": {"value": "0.50"}, "lastShares": "2"}]}))
    execute(adapter=a, ledger=led, mode="LIVE_BETA",
            mkey=market_key("polymarket-us", "slug-x"), league="nba",
            ask_price=0.50, ask_size=500, size_usd=1.0, edge=0.03,
            threshold=0.02, decision={}, ts=1000.0, entry_price=0.50, taker=True)
    assert led.get_state(f"{PMUS_ORDER_PREFIX}slug-x") is None
    assert led.summary()["fills"] == 1


def test_paper_fill_uses_the_planned_entry_price(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    a = _adapter()
    r = execute(adapter=a, ledger=led, mode="PAPER",
                mkey="polymarket-us:slug-x", league="nba", ask_price=0.50,
                ask_size=500, size_usd=10.0, edge=0.03, threshold=0.02,
                decision={}, ts=1000.0, entry_price=0.49, taker=False)
    assert r["placed"]
    pos = led.position("polymarket-us:slug-x")
    assert pos["avg_cost"] == pytest.approx(0.49)
    rec = led.decision_record("paper-polymarket-us:slug-x-1000")["decision"]
    assert rec["entry_taker"] is False and rec["ask_price"] == 0.50


# ── reconciliation ──────────────────────────────────────────────────────

class _TradeAdapter:
    name = "polymarket-us"

    def __init__(self, trades, open_orders=()):
        self._trades = trades
        self._open = list(open_orders)
        self.cancelled = []

    def recent_trades(self, limit=100):
        return self._trades

    def open_orders(self):
        return self._open

    def cancel_order(self, order_id, slug):
        self.cancelled.append((order_id, slug))
        return True

    def taker_fee(self, price):
        return 0.0

    def maker_fee(self, price):
        return 0.0


def _ctx(**kw):
    return {"order_id": "o1", "px": 0.49, "count": 2, "market_key":
            "polymarket-us:slug-x", "league": "nba", "event_key": "ev1",
            "mode": "LIVE_BETA", "ts": time.time(), **kw}


def test_maker_fill_lands_in_the_ledger(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.set_state(f"{PMUS_ORDER_PREFIX}slug-x", _ctx())
    a = _TradeAdapter([{"id": "t1", "marketSlug": "slug-x", "qty": "2",
                        "price": {"value": "0.49"}, "isAggressor": False}])
    assert sync_pmus_fills(a, led, "LIVE_BETA") == 1
    pos = led.position("polymarket-us:slug-x")
    assert pos["shares"] == 2 and pos["avg_cost"] == pytest.approx(0.49)


def test_sync_is_idempotent(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.set_state(f"{PMUS_ORDER_PREFIX}slug-x", _ctx())
    a = _TradeAdapter([{"id": "t1", "marketSlug": "slug-x", "qty": "2",
                        "price": {"value": "0.49"}}])
    sync_pmus_fills(a, led, "LIVE_BETA")
    assert sync_pmus_fills(a, led, "LIVE_BETA") == 0
    assert led.summary()["fills"] == 1


def test_trades_without_a_parked_order_are_ignored(tmp_path):
    """Manual trades and already-recorded taker fills must not be re-imported."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    a = _TradeAdapter([{"id": "t9", "marketSlug": "some-other-market",
                        "qty": "5", "price": {"value": "0.30"}}])
    assert sync_pmus_fills(a, led, "LIVE_BETA") == 0
    assert led.summary()["fills"] == 0


# ── the reaper ──────────────────────────────────────────────────────────

def test_stale_order_is_cancelled_and_the_event_released(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    assert led.claim_event("ev1", "polymarket-us:slug-x", "polymarket-us")
    led.set_state(f"{PMUS_ORDER_PREFIX}slug-x", _ctx(ts=time.time() - 300))
    a = _TradeAdapter([], open_orders=[{"id": "o1"}])
    out = reap_pmus_makers(a, led, ttl_s=90)
    assert out == {"cancelled": 1, "closed": 0, "released": 1, "held": 0}
    assert a.cancelled == [("o1", "slug-x")]
    assert not led.event_traded("ev1")        # the game is tradeable again
    assert led.get_state(f"{PMUS_ORDER_PREFIX}slug-x") is None


def test_young_order_is_left_alone(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.claim_event("ev1", "polymarket-us:slug-x", "polymarket-us")
    led.set_state(f"{PMUS_ORDER_PREFIX}slug-x", _ctx())
    a = _TradeAdapter([], open_orders=[{"id": "o1"}])
    assert reap_pmus_makers(a, led, ttl_s=90) == {"cancelled": 0, "closed": 0,
                                                  "released": 0, "held": 0}
    assert a.cancelled == [] and led.event_traded("ev1")


def test_a_filled_order_keeps_its_event_claim(tmp_path):
    """Releasing here would let the engine add to a position it already
    holds — the one hard rule the beta profile exists to enforce."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.claim_event("ev1", "polymarket-us:slug-x", "polymarket-us")
    led.set_state(f"{PMUS_ORDER_PREFIX}slug-x", _ctx(ts=time.time() - 300))
    a = _TradeAdapter([{"id": "t1", "marketSlug": "slug-x", "qty": "2",
                        "price": {"value": "0.49"}}])
    sync_pmus_fills(a, led, "LIVE_BETA")      # the order filled...
    out = reap_pmus_makers(a, led, ttl_s=90)  # ...then aged out
    assert out["released"] == 0
    assert led.event_traded("ev1")


def test_order_gone_from_the_venue_is_held_not_released(tmp_path):
    """An order that vanished without a confirmed cancel most likely FILLED.

    This is the exact shape of the 2026-08-02 runaway. The venue's activity
    feed lags its matching engine, so for a few seconds a real fill is
    invisible: the order is off the book, `recent_trades()` does not list it
    yet, and the ledger therefore shows no position. Treating that as "never
    filled" released the claim and re-opened the market for another buy — on
    a cap computed from a ledger that did not know we already owned it.
    """
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.claim_event("ev1", "polymarket-us:slug-x", "polymarket-us")
    led.set_state(f"{PMUS_ORDER_PREFIX}slug-x", _ctx())   # young, but not open
    a = _TradeAdapter([], open_orders=[])
    out = reap_pmus_makers(a, led, ttl_s=90)
    assert out == {"cancelled": 0, "closed": 1, "released": 0, "held": 1}
    assert a.cancelled == []
    assert led.event_traded("ev1")            # claim retained — fail closed
    # The context MUST survive, or sync_pmus_fills can never attribute the
    # trade when it finally shows up: it only considers parked markets.
    assert led.get_state(f"{PMUS_ORDER_PREFIX}slug-x") is not None


def test_a_vanished_order_is_released_once_the_grace_window_expires(tmp_path):
    """Held is not held forever — an order that truly never filled has to
    give the market back, or one silent rejection retires it for the day."""
    from edge.execution.executor import REAP_GRACE_S

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.claim_event("ev1", "polymarket-us:slug-x", "polymarket-us")
    led.set_state(f"{PMUS_ORDER_PREFIX}slug-x", _ctx())
    a = _TradeAdapter([], open_orders=[])
    reap_pmus_makers(a, led, ttl_s=90)                    # starts the clock
    assert led.event_traded("ev1")
    out = reap_pmus_makers(a, led, ttl_s=90,
                           now=time.time() + REAP_GRACE_S + 1)
    assert out["released"] == 1
    assert not led.event_traded("ev1")


def test_a_fill_landing_after_the_reap_still_blocks_a_second_entry(tmp_path):
    """The regression test for the runaway itself.

    Sequence: order rests, leaves the book, the reaper runs BEFORE the trade
    appears in the activity feed, and only then does the fill surface. The
    old reaper cleared the context and released the claim at step three,
    which both stranded the fill (unattributable forever) and re-opened the
    market. Repeated once per cycle, that is how a $1.50 per-market cap
    became a $26.60 position.

    What must hold: the claim never comes back, and the fill lands.
    """
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.claim_event("ev1", "polymarket-us:slug-x", "polymarket-us")
    led.set_state(f"{PMUS_ORDER_PREFIX}slug-x", _ctx())

    # 1. the order is off the book, but the venue is not reporting the trade.
    a = _TradeAdapter([], open_orders=[])
    reap_pmus_makers(a, led, ttl_s=90)
    assert led.event_traded("ev1"), "claim released on an unproven fill"

    # 2. the trade surfaces a moment later.
    a = _TradeAdapter([{"id": "t1", "marketSlug": "slug-x", "qty": "2",
                        "price": {"value": "0.49"}}], open_orders=[])
    assert sync_pmus_fills(a, led, "LIVE_BETA") == 1, \
        "context was dropped, so the fill could never be attributed"

    # 3. we now hold the market, and it stays claimed however often we reap.
    for _ in range(3):
        reap_pmus_makers(a, led, ttl_s=90)
    assert led.event_traded("ev1")
    pos = led.position("polymarket-us:slug-x")
    assert pos and float(pos["shares"]) == 2.0


def test_the_two_fill_paths_cannot_double_count_one_trade(tmp_path):
    """sync_pmus_fills and reconcile_positions both read the venue's trades.

    They used different fill_uid prefixes for the same trade id, so a trade
    seen by both was recorded twice — doubling the position and, with it, the
    per-market exposure every cap is measured against.
    """
    from edge.execution.executor import reconcile_positions

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.set_state(f"{PMUS_ORDER_PREFIX}slug-x", _ctx())
    trade = {"id": "t1", "marketSlug": "slug-x", "qty": "2",
             "price": {"value": "0.49"}}
    a = _TradeAdapter([trade], open_orders=[])

    sync_pmus_fills(a, led, "LIVE_BETA")
    reconcile_positions(a, led, "LIVE_BETA")
    reconcile_positions(a, led, "LIVE_BETA")      # idempotent under repetition

    pos = led.position("polymarket-us:slug-x")
    assert float(pos["shares"]) == 2.0, "one venue trade became two fills"


def test_reaper_is_a_no_op_with_nothing_resting(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))

    class Exploding(_TradeAdapter):
        def open_orders(self):
            raise AssertionError("no resting orders: must not call the venue")

    assert reap_pmus_makers(Exploding([]), led, ttl_s=90)["cancelled"] == 0


# ── the volume claim, end to end ────────────────────────────────────────

def test_maker_pricing_qualifies_a_trade_the_ask_would_have_killed():
    """fair 0.50, ask 0.49 -> 1c edge against a 2c bar: no trade. Resting one
    tick inside at 0.48 makes it a 2c edge, which clears. This is where the
    extra volume comes from, and it comes from a better price, not a looser
    rule."""
    from edge.execution.engine import Policy, strategy_filter

    policy = Policy.load()
    # mechanics test: lift the measured moneyline quarantine
    policy.leagues = {**policy.leagues, "blocked_categories": []}
    at_ask = strategy_filter(policy, "epl", 0.49, 0.50)
    at_maker = strategy_filter(policy, "epl", 0.48, 0.50)
    assert at_ask.ok is False and "edge" in at_ask.reason
    assert at_maker.ok is True


class _PmusStub:
    """Minimal live Polymarket US double: the executor's maker branch calls
    exactly these."""

    name = "polymarket-us"

    def __init__(self, ask=0.49, bid=0.40):
        self.book_errors = {}
        self._ask, self._bid = ask, bid
        self.orders = []

    def discover_markets(self, league_codes):
        from edge.venues.mapper import VenueMarket

        return [VenueMarket(market_id="EVT", title="Arsenal vs. Chelsea",
                            league_code="epl",
                            outcome_tokens={"Arsenal": "slug-ars"})]

    def get_book(self, market_id, token):
        return MarketBook(venue=self.name, market_id=market_id, outcome_id=token,
                          bids=[BookLevel(self._bid, 500)],
                          asks=[BookLevel(self._ask, 500)], ts=time.time())

    def taker_fee(self, price):
        return 0.0

    def maker_fee(self, price):
        return 0.0

    def plan_entry(self, book):
        return PolymarketUSAdapter.plan_entry(_adapter(), book)

    def place_order(self, slug, price, qty, preview=True,
                    tif="TIME_IN_FORCE_FILL_OR_KILL", post_only=False):
        self.orders.append({"slug": slug, "price": price, "qty": qty,
                            "tif": tif, "post_only": post_only})
        return {"ok": True, "order_id": "o1", "status": "resting",
                "resting": True, "price": price, "count": 0, "taker": False}


def test_live_cycle_rests_inside_the_spread_instead_of_crossing(tmp_path, monkeypatch):
    """Whole loop, live mode: the decision is judged at the resting price and
    the order that reaches the venue is GTC post-only, not FOK at the ask."""
    from edge.execution.engine import Policy
    from edge.execution.risk import RiskManager
    from edge.shadow.runner import run_cycle
    from tests.test_run_cycle_e2e import StubFeed, _event

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EDGE_LIVE_VENUES", "polymarket-us")
    policy = Policy.load()
    # mechanics test: lift the measured moneyline quarantine
    policy.leagues = {**policy.leagues, "blocked_categories": []}
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(led, {**policy.risk, "mode": "LIVE_BETA"})
    venue = _PmusStub(ask=0.49, bid=0.40)

    funnel = run_cycle([venue], StubFeed([_event()]), policy, risk, led,
                       ["soccer_epl"])

    assert funnel["logged"] == 1                      # an order went out...
    assert led.summary()["fills"] == 0                # ...but nothing traded yet
    [order] = venue.orders
    assert order["tif"] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
    assert order["post_only"] is True and order["price"] == 0.48
    ctx = led.get_state(f"{PMUS_ORDER_PREFIX}slug-ars")
    assert ctx["event_key"] and ctx["px"] == 0.48


def test_paper_never_invents_a_maker_fill(tmp_path, monkeypatch):
    """Paper evidence must stay conservative: a shadow fill is only recorded
    at a price we could certainly have got — the ask."""
    from edge.execution.engine import Policy
    from edge.execution.risk import RiskManager
    from edge.shadow.runner import run_cycle
    from tests.test_run_cycle_e2e import StubFeed, _event

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    policy = Policy.load()
    # mechanics test: lift the measured moneyline quarantine
    policy.leagues = {**policy.leagues, "blocked_categories": []}
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(led, {**policy.risk, "mode": "PAPER"})
    venue = _PmusStub(ask=0.47, bid=0.40)   # 3c edge: clears either way

    run_cycle([venue], StubFeed([_event()]), policy, risk, led, ["soccer_epl"])
    assert venue.orders == []                            # no venue contact
    assert led.position("polymarket-us:slug-ars")["avg_cost"] == pytest.approx(0.47)


def test_an_unfilled_market_goes_back_to_crossing(tmp_path):
    """A quote that never fills must not retire the market: after the reaper
    pulls it, the next look crosses the spread."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    a = _adapter()
    led.claim_event("ev1", "polymarket-us:slug-x", "polymarket-us")
    led.set_state(f"{PMUS_ORDER_PREFIX}slug-x", _ctx(ts=time.time() - 300))
    a.open_orders = lambda: [{"id": "o1"}]
    a.cancel_order = lambda oid, slug: True

    assert a.plan_entry(_book(0.40, 0.50)) == (0.49, False)
    reap_pmus_makers(a, led, ttl_s=90)
    assert a.plan_entry(_book(0.40, 0.50)) == (0.50, True)


def test_the_cross_only_lasts_for_the_cool_off(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_PMUS_FORCE_TAKER_S", "0")
    a = _adapter()
    a.mark_force_taker("slug-x")
    assert a.plan_entry(_book(0.40, 0.50)) == (0.49, False)   # already expired


# ── ledger primitives ───────────────────────────────────────────────────

def test_release_event_is_honest_about_what_it_did(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.claim_event("ev1", "m", "v")
    assert led.release_event("ev1") is True
    assert led.release_event("ev1") is False   # nothing left to release
    assert led.claim_event("ev1", "m", "v")    # and the event is free again


def test_list_state_survives_a_restart(tmp_path):
    """The reaper finds its parked orders from disk, not from memory — a
    redeploy must not orphan a resting order."""
    path = str(tmp_path / "l.sqlite3")
    Ledger(db_path=path).set_state(f"{PMUS_ORDER_PREFIX}slug-x", {"order_id": "o1"})
    reopened = Ledger(db_path=path)
    assert list(reopened.list_state(PMUS_ORDER_PREFIX)) == [
        f"{PMUS_ORDER_PREFIX}slug-x"]
    reopened.clear_state(f"{PMUS_ORDER_PREFIX}slug-x")
    assert reopened.list_state(PMUS_ORDER_PREFIX) == {}


# ── the orphan sweep: no order survives untracked ───────────────────────

def test_orphan_orders_are_cancelled_and_tracked_ones_spared(tmp_path):
    """An untracked resting order is a standing instruction to buy at a
    stale price forever. Contexts die with the ledger on deploy (observed
    live 2026-08-02: four deploys orphaned every resting GTC order, and
    they filled through a PAPER halt). Anything the ledger does not track
    gets cancelled; anything it does track is left to the reaper."""
    from edge.execution.executor import cancel_orphan_orders

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.set_state(f"{PMUS_ORDER_PREFIX}slug-x", _ctx())   # tracked: o1
    a = _TradeAdapter([], open_orders=[
        {"id": "o1", "marketSlug": "slug-x"},
        {"id": "o-orphan-1", "marketSlug": "slug-dead"},
        {"id": "o-orphan-2", "marketSlug": "slug-gone"},
    ])
    out = cancel_orphan_orders(a, led)
    assert out == {"open": 3, "orphans_cancelled": 2}
    assert ("o-orphan-1", "slug-dead") in a.cancelled
    assert ("o-orphan-2", "slug-gone") in a.cancelled
    assert ("o1", "slug-x") not in a.cancelled


def test_a_fresh_ledger_means_cancel_everything(tmp_path):
    """Cancel-on-restart. A wiped ledger tracks nothing, so everything
    open at the venue is an orphan by definition — which is exactly the
    deploy scenario that caused the incident."""
    from edge.execution.executor import cancel_orphan_orders

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))     # fresh: no state
    a = _TradeAdapter([], open_orders=[{"id": "a", "marketSlug": "s1"},
                                       {"id": "b", "marketSlug": "s2"}])
    out = cancel_orphan_orders(a, led)
    assert out["orphans_cancelled"] == 2


def test_the_sweep_survives_a_venue_error(tmp_path):
    """The sweep must never kill the loop that runs it."""
    from edge.execution.executor import cancel_orphan_orders

    class Exploding(_TradeAdapter):
        def open_orders(self):
            raise RuntimeError("venue down")

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    out = cancel_orphan_orders(Exploding([]), led)
    assert out == {"open": 0, "orphans_cancelled": 0}


def test_maker_first_is_hard_off_even_with_the_env_set(monkeypatch):
    """The deployed service may still carry EDGE_PMUS_MAKER_FIRST=1 from
    the earlier experiment, and a code DEFAULT cannot beat a set env var.
    After the orphaned-GTC incident the switch is code-level off: resting
    orders return only when order state survives restarts."""
    monkeypatch.setenv("EDGE_PMUS_MAKER_FIRST", "1")
    from edge.venues.polymarket_us import PolymarketUSAdapter

    a = PolymarketUSAdapter.__new__(PolymarketUSAdapter)
    PolymarketUSAdapter.__init__(a)
    assert a._maker_first is False
