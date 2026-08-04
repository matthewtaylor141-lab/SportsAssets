"""The Kalshi copy leg: his price +2%, once per position, day-capped,
strict name join — and RN1's $3 clip carries over."""

import tempfile
import time

from edge.ledger.service import Ledger
from edge.shadow.kalshi_copies import _limit_for, sweep
from edge.venues.base import BookLevel, MarketBook
from edge.venues.mapper import VenueMarket


class _Kalshi:
    name = "kalshi"

    def __init__(self, ask, outcomes=None):
        self.ask = ask
        self.orders = []
        self._outcomes = outcomes or {"Baltimore Orioles": "T-BAL",
                                      "Texas Rangers": "T-TEX"}

    def taker_fee(self, price):
        return 0.07 * price * (1 - price)

    def discover_markets(self, league_codes):
        return [VenueMarket(market_id="KXMLBGAME-26AUG04BALTEX",
                            title="Orioles at Rangers", league_code="mlb",
                            outcome_tokens=dict(self._outcomes))]

    def get_book(self, market_id, ticker):
        return MarketBook(venue=self.name, market_id=market_id,
                          outcome_id=ticker, bids=[],
                          asks=[BookLevel(self.ask, 60)], ts=time.time())

    def place_order(self, ticker, price, count, **kw):
        self.orders.append((ticker, price, count))
        return {"ok": True, "count": count, "price": price,
                "status": "filled"}


_ROW = {"slug": "mlb-bal-tex-2026-08-04", "outcome": "Baltimore Orioles",
        "price": 0.50, "whale": "swisstony"}


def _run(ask, rows=None, live=True, led=None):
    led = led or Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(ask)
    st = sweep(kalshi=ka, ledger=led, identities=rows or [dict(_ROW)],
               live=live)
    return st, ka, led


def test_limit_is_his_price_plus_two_percent_floored():
    assert _limit_for(0.50) == 0.51
    assert _limit_for(0.97) == 0.98
    assert _limit_for(0.985) == 0.99


def test_copies_when_kalshi_is_inside_his_tolerance():
    st, ka, led = _run(0.50)
    assert st["matched"] == 1 and st["copied"] == 1
    assert ka.orders == [("T-BAL", 0.50, 4)]   # $2 -> 4 contracts
    assert led.get_state("kcopy:mlb-bal-tex-2026-08-04:Baltimore Orioles")


def test_outside_tolerance_is_not_a_copy():
    st, ka, _ = _run(0.55)   # his 0.50 -> limit 0.51 < ask
    assert st["matched"] == 1 and st["copied"] == 0
    assert not ka.orders


def test_rn1_gets_the_three_dollar_clip():
    row = {**_ROW, "whale": "RN1"}
    st, ka, _ = _run(0.50, rows=[row])
    assert ka.orders == [("T-BAL", 0.50, 6)]   # $3 -> 6 contracts


def test_one_copy_per_position_ever():
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    st1, _, _ = _run(0.50, led=led)
    st2, ka2, _ = _run(0.45, led=led)
    assert st1["copied"] == 1
    assert st2["copied"] == 0 and st2["skipped_claimed"] == 1
    assert not ka2.orders


def test_unlisted_league_never_reaches_kalshi():
    row = {"slug": "itf-pietri-porras-2026-08-04",
           "outcome": "Julio Cesar Porras", "price": 0.6,
           "whale": "swisstony"}
    st, ka, _ = _run(0.5, rows=[row])
    assert st["league_listed"] == 0
    assert not ka.orders


def test_paper_counts_but_never_orders():
    st, ka, led = _run(0.50, live=False)
    assert st["copied"] == 1
    assert not ka.orders
    assert not led.get_state("kcopy:mlb-bal-tex-2026-08-04:Baltimore Orioles")


def test_smoke_order_fires_exactly_once_ever():
    from edge.shadow.runner import _kalshi_smoke

    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(0.45)
    _kalshi_smoke(ka, led, lambda: True)
    _kalshi_smoke(ka, led, lambda: True)
    assert len(ka.orders) == 1, "the claim must survive re-runs"
    assert ka.orders[0][2] == 1, "one contract only"
    assert ka.orders[0][1] <= 0.55
    done = led.get_state("kalshi_smoke_done")
    assert done and done["ok"]


def test_smoke_order_never_fires_in_paper():
    from edge.shadow.runner import _kalshi_smoke

    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(0.45)
    _kalshi_smoke(ka, led, lambda: False)
    assert not ka.orders
    assert not led.get_state("kalshi_smoke_done")


def test_accepted_ioc_with_zero_fill_does_not_burn_the_claim():
    """V2 can accept an IOC and fill nothing (book moved). The copy's
    once-ever claim must survive that for a retry at the fresh book."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")

    class _ZeroFill(_Kalshi):
        def place_order(self, ticker, price, count, **kw):
            self.orders.append((ticker, price, count))
            return {"ok": True, "count": 0, "price": price,
                    "status": "http_201"}

    ka = _ZeroFill(0.50)
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True)
    assert st["copied"] == 0 and st.get("ioc_zero_fill") == 1
    assert not led.get_state("kcopy:mlb-bal-tex-2026-08-04:Baltimore Orioles")
    # next sweep retries: no claim recorded
    st2 = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True)
    assert st2.get("skipped_claimed", 0) == 0


def test_smoke_zero_fill_is_not_done():
    from edge.shadow.runner import _kalshi_smoke

    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")

    class _ZeroFill(_Kalshi):
        def place_order(self, ticker, price, count, **kw):
            self.orders.append((ticker, price, count))
            return {"ok": True, "count": 0, "price": price,
                    "status": "http_201"}

    ka = _ZeroFill(0.45)
    _kalshi_smoke(ka, led, lambda: True)
    assert not led.get_state("kalshi_smoke_done")
    last = led.get_state("kalshi_smoke_last")
    assert last and last["ok"] and last["filled"] == 0
    _kalshi_smoke(ka, led, lambda: True)     # retries while not done
    assert len(ka.orders) == 2
