"""Better-price adds: strict match or nothing, fee-loaded improvement,
once per position, day-capped, separately graded."""

import tempfile
import time

from edge.ledger.service import Ledger
from edge.shadow.kalshi_adds import sweep
from edge.venues.base import BookLevel, MarketBook
from edge.venues.mapper import VenueMarket


class _Pmus:
    name = "polymarket-us"

    def __init__(self, positions):
        self._positions = positions

    def open_positions_map(self):
        return self._positions


class _Kalshi:
    name = "kalshi"

    def __init__(self, ask, event_ticker="KXMLBGAME-26AUG04BALTEX"):
        self.ask = ask
        self.orders = []
        self._event_ticker = event_ticker

    def taker_fee(self, price):
        return 0.07 * price * (1 - price)

    def discover_markets(self, league_codes):
        if "mlb" not in league_codes:
            return []
        return [VenueMarket(
            market_id=self._event_ticker, title="Orioles at Rangers",
            league_code="mlb",
            outcome_tokens={"Baltimore Orioles": "T-BAL",
                            "Texas Rangers": "T-TEX"})]

    def get_book(self, market_id, ticker):
        return MarketBook(venue=self.name, market_id=market_id,
                          outcome_id=ticker, bids=[],
                          asks=[BookLevel(self.ask, 50)], ts=time.time())

    def place_order(self, ticker, price, count, **kw):
        self.orders.append((ticker, price, count))
        return {"ok": True, "count": count, "price": price,
                "status": "filled"}


_POS = {"aec-mlb-bal-tex-2026-08-04":
        {"qty": 4.0, "cost": 2.00, "outcome": "Baltimore Orioles",
         "title": "Orioles at Rangers"}}


def _run(ask, positions=_POS, live=True, led=None, **kw):
    led = led or Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(ask, **kw)
    st = sweep(pmus=_Pmus(positions), kalshi=ka, ledger=led, live=live)
    return st, ka, led


def test_a_meaningfully_cheaper_kalshi_listing_gets_the_add():
    # entry 0.50; kalshi 0.45 + 1.7c fee = 0.467 -> 3.3c better: fires.
    st, ka, led = _run(0.45)
    assert st["matched"] == 1 and st["added"] == 1
    assert ka.orders == [("T-BAL", 0.45, 2)]   # $1 -> 2 contracts at 45c
    assert led.get_state("kadd:aec-mlb-bal-tex-2026-08-04")
    assert (led.get_state("kadd_day") or {}).get("spent", 0) > 0


def test_fee_loaded_improvement_under_2c_does_not_fire():
    # entry 0.50; kalshi 0.48 + fee = ~0.497 -> 0.3c better: not enough.
    st, ka, _ = _run(0.48)
    assert st["matched"] == 1 and st["added"] == 0
    assert not ka.orders
    assert st["best_improve_c"] < 2.0


def test_wrong_date_never_matches():
    st, ka, _ = _run(0.40, event_ticker="KXMLBGAME-26AUG09BALTEX")
    assert st["matched"] == 0
    assert not ka.orders


def test_each_position_adds_at_most_once():
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    st1, ka1, _ = _run(0.45, led=led)
    st2, ka2, _ = _run(0.40, led=led)
    assert st1["added"] == 1
    assert st2["added"] == 0 and st2["skipped_claimed"] == 1
    assert not ka2.orders


def test_unlisted_league_positions_are_ignored():
    pos = {"aec-itfwo-andgar-chlpaq-2026-08-04":
           {"qty": 2.0, "cost": 0.5, "outcome": "Chloe Paquet",
            "title": "ITF"}}
    st, ka, _ = _run(0.10, positions=pos)
    assert st["positions"] == 1 and st["league_listed"] == 0
    assert not ka.orders


def test_paper_counts_but_never_orders():
    st, ka, led = _run(0.45, live=False)
    assert st["added"] == 1
    assert not ka.orders
    assert not led.get_state("kadd:aec-mlb-bal-tex-2026-08-04")
