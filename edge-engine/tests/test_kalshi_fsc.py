"""First-set comeback sleeve (owner order 2026-08-17): match-day tennis
favorites are snapshotted from Kalshi's own book; a favorite whose price
falls into the set-lost band, holds it to a second-sweep confirmation,
places one $100 entry, once per match ever, held to settlement. Isolated
from the copy sleeves by claims, category, kill switch and day cap."""

import tempfile
import time

import pytest

from edge.ledger.service import Ledger
from edge.shadow import kalshi_copies as _kc
from edge.shadow import kalshi_fsc as _fsc
from edge.shadow.kalshi_fsc import _date_token, sweep
from edge.venues.base import BookLevel, MarketBook
from edge.venues.mapper import VenueMarket

# The match-day gate reads the date embedded in the ticker, so fixtures
# must carry TODAY'S token or every test dies of skipped_offday tomorrow.
TOK = _date_token(time.time())
T_FAV = f"KXWTAMATCH-{TOK}ANDPLI-AND"
T_DOG = f"KXWTAMATCH-{TOK}ANDPLI-PLI"
KEY = f"fsc:{TOK}ANDPLI"

T2_FAV = f"KXWTAMATCH-{TOK}SWIGAU-SWI"
T2_DOG = f"KXWTAMATCH-{TOK}SWIGAU-GAU"


def _mkt(market_id, outcomes):
    return VenueMarket(market_id=market_id, title=market_id,
                       league_code="wta", outcome_tokens=outcomes)


_M1 = _mkt(f"KXWTAMATCH-{TOK}ANDPLI", {"Andreeva": T_FAV,
                                       "Pliskova": T_DOG})
_M2 = _mkt(f"KXWTAMATCH-{TOK}SWIGAU", {"Swiatek": T2_FAV,
                                       "Gauff": T2_DOG})


@pytest.fixture(autouse=True)
def _fresh_discovery_cache():
    """FSC rides kalshi_copies' league-discovery cache; a recycled fake
    adapter id must never serve another test's market list."""
    _kc._DISCO_CACHE.clear()
    yield
    _kc._DISCO_CACHE.clear()


@pytest.fixture
def fast_gates(monkeypatch):
    """Collapse the two wall-clock gates so a trigger can complete in
    three back-to-back sweeps: arm, pend, enter."""
    monkeypatch.setattr(_fsc, "ARMED_AGE_S", 0.0)
    monkeypatch.setattr(_fsc, "CONFIRM_MIN_S", 0.0)


class _Kalshi:
    name = "kalshi"

    def __init__(self, asks, markets=None, held=(), guard_ok=True):
        self.asks = dict(asks)          # {ticker: (price, size)}
        self.markets = list(markets or [_M1])
        self.held = set(held)
        self.guard_ok = guard_ok
        self.orders = []
        self.fill_count = None          # None -> fill what was asked
        self.order_ok = True

    def taker_fee(self, price):
        return 0.07 * price * (1 - price)

    def discover_markets(self, league_codes):
        # One league answers so the 3-league loop can't triple-serve
        # the same match through the per-league cache.
        return list(self.markets) if "wta" in league_codes else []

    def open_ticker_map(self):
        if not self.guard_ok:
            return None
        return {"positions": set(self.held), "resting_buys": {}}

    def get_book(self, market_id, ticker):
        px, size = self.asks.get(ticker, (None, 0.0))
        if px is None:
            return None
        return MarketBook(venue=self.name, market_id=market_id,
                          outcome_id=ticker, bids=[],
                          asks=[BookLevel(px, size)], ts=time.time())

    def place_order(self, ticker, price, count, **kw):
        self.orders.append((ticker, price, count))
        if not self.order_ok:
            return {"ok": False, "status": "insufficient_balance",
                    "raw": {"error": "insufficient_balance"}}
        n = count if self.fill_count is None else self.fill_count
        return {"ok": True, "count": n, "order_id": f"ord-{len(self.orders)}",
                "status": "filled"}


def _led():
    return Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")


def test_arms_the_match_day_favorite():
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["armed"] == 1 and st["entered"] == 0
    saved = led.get_state(KEY)
    assert saved["fav_ticker"] == T_FAV and saved["fav_px"] == 0.62
    assert not ka.orders


def test_future_dated_listing_never_arms():
    """Kalshi lists tennis days ahead; a Tuesday news drop on a Thursday
    match must not read as a set loss. Off-day tickers are refused
    before any state or book read."""
    tomorrow = _date_token(time.time() + 86400.0)
    tf, td = (f"KXWTAMATCH-{tomorrow}ANDPLI-AND",
              f"KXWTAMATCH-{tomorrow}ANDPLI-PLI")
    ka = _Kalshi({tf: (0.62, 500.0), td: (0.40, 500.0)},
                 markets=[_mkt(f"KXWTAMATCH-{tomorrow}ANDPLI",
                               {"Andreeva": tf, "Pliskova": td})])
    led = _led()
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["armed"] == 0 and st.get("skipped_offday") == 2
    assert led.get_state(f"fsc:{tomorrow}ANDPLI") is None


def test_yesterday_dated_match_stays_eligible():
    """A night match live past midnight ET keeps its monitor."""
    yday = _date_token(time.time() - 86400.0)
    tf, td = (f"KXWTAMATCH-{yday}ANDPLI-AND",
              f"KXWTAMATCH-{yday}ANDPLI-PLI")
    ka = _Kalshi({tf: (0.62, 500.0), td: (0.40, 500.0)},
                 markets=[_mkt(f"KXWTAMATCH-{yday}ANDPLI",
                               {"Andreeva": tf, "Pliskova": td})])
    st = sweep(kalshi=ka, ledger=_led(), live=True)
    assert st["armed"] == 1


def test_no_arm_outside_the_favorite_band():
    """A 0.95 heavy favorite is skipped (a lost set won't reach the
    trigger band) and a 0.52/0.50 coin flip has no favorite at all."""
    ka = _Kalshi({T_FAV: (0.95, 500.0), T_DOG: (0.07, 500.0)})
    st = sweep(kalshi=ka, ledger=_led(), live=True)
    assert st["armed"] == 0

    ka2 = _Kalshi({T_FAV: (0.52, 500.0), T_DOG: (0.50, 500.0)})
    st2 = sweep(kalshi=ka2, ledger=_led(), live=True)
    assert st2["armed"] == 0


def test_confirmed_set_loss_places_the_hundred_dollar_entry(fast_gates):
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)          # arm at 0.62
    ka.asks[T_FAV] = (0.33, 500.0)                   # set-1 loss print
    st1 = sweep(kalshi=ka, ledger=led, live=True)    # pend, no money
    assert st1.get("pending_confirm") == 1 and not ka.orders
    st2 = sweep(kalshi=ka, ledger=led, live=True)    # confirmed: enter
    assert st2["entered"] == 1
    assert ka.orders == [(T_FAV, 0.33, 303)]         # floor(100/0.33)
    saved = led.get_state(KEY)
    assert saved["entered"] is True and saved["filled"] == 303
    day = led.get_state("fsc_day")
    assert day["entries"] == 1 and day["spent"] == pytest.approx(99.99)
    pos = led.open_positions(live_only=True)
    assert len(pos) == 1
    p = pos[0]
    assert p["market_key"] == f"kalshi:{T_FAV}"
    assert p["shares"] == 303 and p["mode"] == "LIVE_BETA"
    with led._conn() as conn:
        rows = conn.execute("SELECT category, qty, price FROM fills").fetchall()
    assert [(r["category"], r["qty"], r["price"]) for r in rows] == \
        [("kalshi_fsc", 303.0, 0.33)]
    # sync_kalshi_fills must see this order as already-recorded-inline,
    # or the venue fill pull doubles the position (audit 2026-08-04).
    assert led.get_state("kalshi_inline:ord-1")


def test_one_qualifying_print_is_not_a_set(monkeypatch):
    """Default CONFIRM_MIN_S: two back-to-back sweeps are too fast —
    a single book blip must never move money."""
    monkeypatch.setattr(_fsc, "ARMED_AGE_S", 0.0)
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.33, 500.0)
    sweep(kalshi=ka, ledger=led, live=True)          # pend
    st = sweep(kalshi=ka, ledger=led, live=True)     # < 60s later
    assert st["entered"] == 0 and not ka.orders


def test_blip_that_leaves_the_band_resets_confirmation(fast_gates):
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.33, 500.0)
    sweep(kalshi=ka, ledger=led, live=True)          # pend
    ka.asks[T_FAV] = (0.58, 500.0)                   # blip recovers
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st.get("confirm_reset") == 1 and not ka.orders
    assert not (led.get_state(KEY) or {}).get("pend_ts")


def test_armed_age_gate_blocks_an_instant_drop(monkeypatch):
    """A price that collapses minutes after arming is not a lost set —
    it is news (injury, walkover) and the age gate refuses it."""
    monkeypatch.setattr(_fsc, "CONFIRM_MIN_S", 0.0)
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.33, 500.0)                   # immediate drop
    sweep(kalshi=ka, ledger=led, live=True)          # default 1500s gate
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["entered"] == 0 and not ka.orders


def test_shallow_or_high_price_is_not_a_lost_set(fast_gates):
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.53, 500.0)      # above TRIG_HI: set not decided
    sweep(kalshi=ka, ledger=led, live=True)
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["entered"] == 0 and not ka.orders


def test_heavy_favorite_set_loss_lands_at_fifty_cents(fast_gates):
    """TRIG_HI=0.50 exists for this cohort: a 0.85 favorite who drops
    set 1 reprices to ~0.50, and 0.45 would have refused the entry."""
    ka = _Kalshi({T_FAV: (0.85, 500.0), T_DOG: (0.17, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)          # arm at 0.85
    ka.asks[T_FAV] = (0.50, 500.0)
    sweep(kalshi=ka, ledger=led, live=True)          # pend
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["entered"] == 1
    assert ka.orders == [(T_FAV, 0.50, 200)]         # floor(100/0.50)


def test_deep_collapse_is_worse_than_one_set(fast_gates):
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.15, 500.0)      # below TRIG_LO
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["entered"] == 0 and st.get("skipped_deep") == 1
    assert not ka.orders


def test_one_entry_per_match_ever(fast_gates):
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.33, 500.0)
    sweep(kalshi=ka, ledger=led, live=True)
    sweep(kalshi=ka, ledger=led, live=True)          # enters
    st = sweep(kalshi=ka, ledger=led, live=True)     # still at 0.33
    assert st["entered"] == 0 and len(ka.orders) == 1


def test_day_cap_holds_across_matches(fast_gates, monkeypatch):
    monkeypatch.setattr(_fsc, "DAY_USD", 150.0)
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0),
                  T2_FAV: (0.70, 500.0), T2_DOG: (0.32, 500.0)},
                 markets=[_M1, _M2])
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)          # arm both
    ka.asks[T_FAV] = (0.33, 500.0)
    ka.asks[T2_FAV] = (0.40, 500.0)
    sweep(kalshi=ka, ledger=led, live=True)          # pend both
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["entered"] == 1 and st.get("skipped_day_cap") == 1
    assert len(ka.orders) == 1


def test_venue_held_event_is_left_alone(fast_gates):
    """A match the account already holds (a copy position, either side)
    is never touched — the sleeve must not stack exposure onto the copy
    book (owner: 'should not impact anything in our copy sleeves')."""
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)},
                 held={T_DOG})
    led = _led()
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["armed"] == 0 and st.get("skipped_held") == 2
    assert led.get_state(KEY) is None


def test_guard_down_scans_but_places_nothing(fast_gates):
    """open_ticker_map is fail-CLOSED: on None the sweep keeps arming
    (order-free state) but refuses entries rather than trusting an
    amnesiac 'nothing held' view."""
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)},
                 guard_ok=False)
    led = _led()
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["armed"] == 1 and st.get("venue_guard_down") is True
    ka.asks[T_FAV] = (0.33, 500.0)
    sweep(kalshi=ka, ledger=led, live=True)          # pend
    st2 = sweep(kalshi=ka, ledger=led, live=True)
    assert st2["entered"] == 0 and st2.get("skipped_guard_down") == 1
    assert not ka.orders


def test_kill_switches(monkeypatch):
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    led.set_state("kill_switch", True)
    assert sweep(kalshi=ka, ledger=led, live=True) == {
        "blocked": "kill_switch"}

    monkeypatch.setenv("EDGE_FSC", "0")
    assert sweep(kalshi=ka, ledger=_led(), live=True) == {"disabled": True}


def test_engine_halt_does_not_pause_the_sleeve(fast_gates):
    """Fixed-dollar experiment class: the engine's daily-loss halt must
    not pause it (kalshi_guard scope='fsc'); only global stops do."""
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    led.set_state("halt_until", {"until": time.time() + 3600})
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.33, 500.0)
    sweep(kalshi=ka, ledger=led, live=True)
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["entered"] == 1 and len(ka.orders) == 1


def test_dry_run_places_no_orders(fast_gates):
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=False)
    ka.asks[T_FAV] = (0.33, 500.0)
    sweep(kalshi=ka, ledger=led, live=False)
    st = sweep(kalshi=ka, ledger=led, live=False)
    assert st["entered"] == 1 and not ka.orders
    assert not (led.get_state(KEY) or {}).get("entered")


def test_thin_book_is_not_entered(fast_gates):
    """Same liquidity floor philosophy as the copy sleeve: a 303-lot
    entry needs 303 showing at the ask, or the IOC would part-fill into
    a worse effective price."""
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.33, 50.0)
    sweep(kalshi=ka, ledger=led, live=True)          # pend
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["entered"] == 0 and st.get("skipped_thin") == 1
    assert not ka.orders


def test_ioc_zero_fill_keeps_the_match_armed(fast_gates):
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    ka.fill_count = 0
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.33, 500.0)
    sweep(kalshi=ka, ledger=led, live=True)          # pend
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["entered"] == 0 and st.get("ioc_zero_fill") == 1
    assert not (led.get_state(KEY) or {}).get("entered"), \
        "an unfilled IOC must leave the match armed for the next pass"
    assert (led.get_state("fsc_day") or {}).get("entries") in (None, 0)


def test_order_rejects_are_not_zero_fills(fast_gates):
    """An order the venue REFUSED (auth, balance, 4xx) must be visible
    as its own failure class in the funnel, not disguised as an
    accepted-but-unmatched IOC."""
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    ka.order_ok = False
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.33, 500.0)
    sweep(kalshi=ka, ledger=led, live=True)          # pend
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st.get("order_err") == 1 and "ioc_zero_fill" not in st
    assert st["last_order_error"]["status"] == "insufficient_balance"


def test_thin_book_takes_a_partial_entry(fast_gates):
    """Owner 2026-08-17 late night ("never miss a favorite who loses
    the first set"): a book showing less than the full $100 takes a
    partial entry down to the $25 floor instead of skipping."""
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)          # arm
    ka.asks[T_FAV] = (0.33, 150.0)      # $49.50 showing < $100 want
    sweep(kalshi=ka, ledger=led, live=True)          # pend
    st = sweep(kalshi=ka, ledger=led, live=True)     # confirmed: enter
    assert st["entered"] == 1 and st.get("partial_entries") == 1
    assert ka.orders == [(T_FAV, 0.33, 150)]
