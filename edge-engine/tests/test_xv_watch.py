"""The 24/7 cross-venue watcher: fires on real gaps, never twice, never
past its daily cap, and never on pairs the sweep hasn't proven."""

import tempfile
import time

from edge.execution.arbitrage import XVLeg
from edge.ledger.service import Ledger
from edge.shadow.xv_watch import XVWatch
from edge.venues.base import BookLevel, MarketBook


class _Kalshi:
    name = "kalshi"

    def __init__(self, ask):
        self.ask = ask
        self.orders = []

    def taker_fee(self, price):
        return 0.07 * price * (1 - price)

    def get_book(self, market_id, ticker):
        return MarketBook(venue=self.name, market_id=market_id,
                          outcome_id=ticker, bids=[],
                          asks=[BookLevel(self.ask, 50)], ts=time.time())

    def place_order(self, token, price, count, **kw):
        self.orders.append((token, price, count))
        return {"ok": True, "count": count, "price": price, "status": "filled"}


class _Pmus:
    name = "polymarket-us"

    def __init__(self, ask):
        self.ask = ask
        self.orders = []

    def taker_fee(self, price):
        return 0.0

    def peek_book(self, slug):
        return MarketBook(venue=self.name, market_id=slug, outcome_id=slug,
                          bids=[], asks=[BookLevel(self.ask, 50)],
                          ts=time.time())

    def place_order(self, token, price, count, **kw):
        self.orders.append((token, price, count))
        return {"ok": True, "count": count, "price": price, "status": "filled"}


def _watch(kalshi_ask, pmus_ask, live=True, day_usd=200.0):
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka, pm = _Kalshi(kalshi_ask), _Pmus(pmus_ask)
    w = XVWatch(ledger=led, is_live=lambda: live, day_usd=day_usd)
    pool = {"Yankees": {"kalshi": XVLeg(ka, "KXT1", "Yankees", 0.5, 50),
                        "polymarket-us": XVLeg(pm, "pm-t1", "Yankees", 0.5, 50)},
            "Orioles": {"kalshi": XVLeg(ka, "KXT2", "Orioles", 0.5, 50),
                        "polymarket-us": XVLeg(pm, "pm-t2", "Orioles", 0.5, 50)}}
    w.publish("ev1", "NYY v BAL", "mlb", pool)
    return w, led, ka, pm


def test_fires_on_a_real_fee_loaded_gap_and_records_fills():
    # kalshi 0.45 (fee ~0.017) + pmus 0.50 = ~0.967 -> ~3.3c/set: fires.
    w, led, ka, pm = _watch(0.45, 0.50)
    w._tick()
    assert w.stats["fired"] == 1
    assert ka.orders and pm.orders, "both venues ordered"
    assert led.get_state("xv_tried:ev1"), "claim written after money moved"
    day = led.get_state("xv_watch_day")
    assert day and day["spent"] > 0


def test_a_deep_pair_is_sized_down_to_the_ten_dollar_total_cap():
    """Owner directive 2026-08-05: guaranteed arbitrage may take up to $5
    PER SIDE — $10 TOTAL locked-pair cost. max_usd bounds the COMBINED
    fee-loaded cost of both legs, so a book offering 50 sets (~$48 of
    pair cost) is taken only up to 10 sets: int($10 / $0.967-per-set),
    ~$4.67 on one venue plus $5.00 on the other."""
    w, led, ka, pm = _watch(0.45, 0.50)
    w._tick()
    assert w.stats["fired"] == 1
    (_, ka_px, ka_n), = ka.orders
    (_, pm_px, pm_n), = pm.orders
    assert ka_n == pm_n == 10, "depth must not override the $10 pair cap"
    total = ka_n * (ka_px + ka.taker_fee(ka_px)) + pm_n * pm_px
    assert total <= 10.0 + 1e-9
    assert ka_n * ka_px <= 5.0 + 1e-9 and pm_n * pm_px <= 5.0 + 1e-9


def test_never_fires_twice_on_one_event():
    w, led, ka, pm = _watch(0.45, 0.50)
    w._tick()
    w._tick()
    assert w.stats["fired"] == 1, "the shared claim blocks the second pass"


def test_a_fee_eaten_gap_does_not_fire():
    # 0.49 + 0.50 = 0.99 raw, but the kalshi fee pushes it past $1: no arb.
    w, led, ka, pm = _watch(0.49, 0.50)
    w._tick()
    assert w.stats["fired"] == 0
    assert not ka.orders and not pm.orders
    assert w.stats["pairs"] >= 1, "the pair was scanned and measured"


def test_day_cap_stops_the_class():
    w, led, ka, pm = _watch(0.45, 0.50, day_usd=200.0)
    from datetime import datetime, timezone

    led.set_state("xv_watch_day",
                  {"day": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
                   "spent": 200.0})
    w._tick()
    assert w.stats["fired"] == 0
    assert w.stats["skipped_day_cap"] == 1


def test_paper_mode_scans_but_places_nothing():
    w, led, ka, pm = _watch(0.45, 0.50, live=False)
    w._tick()
    assert w.stats["fired"] == 1, "dry-run hits are telemetry"
    assert not ka.orders and not pm.orders
    assert not led.get_state("xv_tried:ev1"), "no claim burned on paper"


def test_one_sided_pools_are_never_registered():
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(0.45)
    w = XVWatch(ledger=led, is_live=lambda: True)
    w.publish("ev2", "solo", "mlb",
              {"A": {"kalshi": XVLeg(ka, "K1", "A", 0.5, 9)},
               "B": {"kalshi": XVLeg(ka, "K2", "B", 0.5, 9)}})
    assert w.registered() == 0
