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
        # Two-sided by default (the sleeve refuses one-sided books
        # since the TONGEE incident); tests force a one-sided book by
        # setting self.no_bids.
        bids = [] if getattr(self, "no_bids", False) else             [BookLevel(round(max(px - 0.02, 0.01), 2), size)]
        return MarketBook(venue=self.name, market_id=market_id,
                          outcome_id=ticker, bids=bids,
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
    # fav_px is the MID of the two-sided book (0.62 ask / 0.60 bid).
    assert saved["fav_ticker"] == T_FAV and saved["fav_px"] == 0.61
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


# ── Score verification (owner 2026-08-17 night) ─────────────────────


def _srows(fav_sets, opp_sets, fresh=True, completed=False):
    return [{"names": ["Andreeva", "Pliskova"],
             "sets": {"Andreeva": fav_sets, "Pliskova": opp_sets},
             "completed": completed,
             "last_update_ts": time.time() - (60 if fresh else 7200)}]


def _with_scores(monkeypatch, rows):
    from edge.shadow import fsc_scores
    monkeypatch.setattr(fsc_scores, "poll", lambda active_hint=True:
                        (rows, {"rows": len(rows)}))


def test_score_confirmed_set_loss_enters_without_price_confirmation(
        monkeypatch, fast_gates):
    """Feed says 0-1 in sets: fact. Entry fires on the SAME sweep, no
    second-sweep wait, even though the price never hit the band."""
    _with_scores(monkeypatch, _srows(0, 1))
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)          # arm at 0.62
    ka.asks[T_FAV] = (0.55, 500.0)   # above TRIG_HI — price path silent
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st.get("score_confirmed") == 1 and st["entered"] == 1
    assert ka.orders and ka.orders[0][0] == T_FAV


def test_score_zero_zero_vetoes_a_price_collapse(monkeypatch, fast_gates):
    """0-0 fresh: the first set is still being played — a price
    collapse into the band is news, not a set loss. No entry."""
    _with_scores(monkeypatch, _srows(0, 0))
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.33, 500.0)
    s1 = sweep(kalshi=ka, ledger=led, live=True)
    s2 = sweep(kalshi=ka, ledger=led, live=True)
    assert s1.get("score_veto") == 1 and s2.get("score_veto") == 1
    assert not ka.orders


def test_score_set1_won_disqualifies_the_match_forever(
        monkeypatch, fast_gates):
    """Favorite won set 1: 'loses the first set' can never happen —
    the match is closed out even if the price later collapses."""
    _with_scores(monkeypatch, _srows(1, 0))
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.33, 500.0)
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st.get("score_set1_won") == 1
    assert (led.get_state(KEY) or {}).get("set1_won") is True
    _with_scores(monkeypatch, [])    # feed goes quiet; price collapses
    s2 = sweep(kalshi=ka, ledger=led, live=True)
    s3 = sweep(kalshi=ka, ledger=led, live=True)
    assert not ka.orders and s3["entered"] == 0


def test_stale_or_absent_scores_leave_the_price_path_untouched(
        monkeypatch, fast_gates):
    _with_scores(monkeypatch, _srows(0, 1, fresh=False))
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.33, 500.0)
    sweep(kalshi=ka, ledger=led, live=True)          # pend (price path)
    st = sweep(kalshi=ka, ledger=led, live=True)     # confirm: enter
    assert st["entered"] == 1 and not st.get("score_confirmed")


def test_one_sided_book_never_arms_the_dog(fast_gates):
    """TONGEE incident regression (owner screenshot 2026-08-17 night):
    a lone 60c offer with NO BIDS on an empty ITF book must not read as
    'the favorite trades at 60c' — the real favorite was the OTHER
    player. One-sided books neither arm nor trigger."""
    ka = _Kalshi({T_FAV: (0.60, 40.0), T_DOG: (0.95, 12.0)})
    ka.no_bids = True
    led = _led()
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["armed"] == 0 and st.get("skipped_one_sided", 0) >= 1
    assert led.get_state(KEY) is None
    # Liquidity arrives and the phantom 'collapse' prints — still no
    # armed state, so nothing can ever trigger.
    ka.asks[T_FAV] = (0.28, 300.0)
    sweep(kalshi=ka, ledger=led, live=True)
    st3 = sweep(kalshi=ka, ledger=led, live=True)
    assert st3["entered"] == 0 and not ka.orders


def test_wide_spread_is_not_a_price(fast_gates):
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)          # arm two-sided
    # The 'collapse' is a 0.28 ask over a 0.05 bid — a 23c-wide book,
    # not a set loss.
    ka.asks[T_FAV] = (0.28, 500.0)
    orig = ka.get_book

    def wide(market_id, ticker):
        b = orig(market_id, ticker)
        if b and ticker == T_FAV and b.asks[0].price == 0.28:
            b.bids[0] = type(b.bids[0])(0.05, 500.0)
        return b
    ka.get_book = wide
    s1 = sweep(kalshi=ka, ledger=led, live=True)
    s2 = sweep(kalshi=ka, ledger=led, live=True)
    assert s2["entered"] == 0 and not ka.orders
    assert (s1.get("skipped_wide_spread", 0)
            + s2.get("skipped_wide_spread", 0)) >= 1


def test_contradicted_arm_is_refused(fast_gates):
    """Both mids can't be favorites: if the opponent's two-sided book
    says THEY are above 50%, the arm is book noise and is refused."""
    ka = _Kalshi({T_FAV: (0.60, 500.0), T_DOG: (0.72, 500.0)})
    led = _led()
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["armed"] == 0 and st.get("arm_contradicted", 0) >= 1


# ── Pre-match odds verification + $100 top-up (owner 2026-08-17:
# "use the odds verification... they are traded for $100 (even the
# fills need to be close to $100 not $37)") ──────────────────────────


def _with_feed(monkeypatch, fav_name, prob=0.65, commence_delta=-3600.0):
    from edge.shadow import fsc_scores
    monkeypatch.setattr(
        fsc_scores, "prematch_favorite",
        lambda a, b: {"fav_name": fav_name, "prob": prob,
                      "commence_ts": time.time() + commence_delta,
                      "frozen": True})


def test_feed_favorite_is_authoritative(monkeypatch, fast_gates):
    """Covered tour: the sportsbook consensus names the favorite. The
    old FAV band no longer bounds it — a 0.93 heavy is still 'every
    single favorite' — and the feed probability lands in state."""
    _with_feed(monkeypatch, "Andreeva", prob=0.93)
    ka = _Kalshi({T_FAV: (0.94, 500.0), T_DOG: (0.07, 500.0)})
    led = _led()
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st["armed"] == 1 and st.get("armed_feed") == 1
    saved = led.get_state(KEY)
    assert saved["fav_ticker"] == T_FAV
    assert saved["feed_prob"] == 0.93 and saved["commence_ts"]


def test_feed_names_the_true_favorite_not_kalshis_board(
        monkeypatch, fast_gates):
    """TONGEE-class refusal, feed edition: Kalshi's board prints the
    WRONG player as favorite; the feed overrules it. The feed's dog can
    never arm and the feed's favorite arms despite the board."""
    _with_feed(monkeypatch, "Pliskova", prob=0.70)
    ka = _Kalshi({T_FAV: (0.60, 500.0), T_DOG: (0.72, 500.0)})
    led = _led()
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st.get("arm_feed_dog") == 1 and st["armed"] == 1
    saved = led.get_state(KEY)
    assert saved["fav_ticker"] == T_DOG and saved["feed_prob"] == 0.70


def test_feed_coinflip_has_no_favorite(monkeypatch, fast_gates):
    _with_feed(monkeypatch, "Andreeva", prob=0.505)
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    st = sweep(kalshi=ka, ledger=_led(), live=True)
    assert st["armed"] == 0 and st.get("arm_feed_coinflip", 0) >= 1


def test_commence_anchor_blocks_a_prematch_collapse(
        monkeypatch, fast_gates):
    """Feed-covered matches anchor the trigger to the REAL start time:
    before commence + START_MIN_S no first set can be over, so a
    pre-match price collapse can never buy — the residual the price-only
    design documented is closed wherever the feed reaches."""
    _with_feed(monkeypatch, "Andreeva", commence_delta=600.0)
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)          # arm (feed)
    ka.asks[T_FAV] = (0.33, 500.0)                   # pre-match dump
    s1 = sweep(kalshi=ka, ledger=led, live=True)
    s2 = sweep(kalshi=ka, ledger=led, live=True)
    assert s2["entered"] == 0 and not ka.orders
    assert (s1.get("skipped_prestart", 0)
            + s2.get("skipped_prestart", 0)) >= 1


def test_commence_anchor_lets_a_midmatch_deploy_enter(monkeypatch):
    """A deploy landing mid-match arms with commence already in the
    past — entry no longer waits out the ARMED_AGE_S proxy (default
    25 min) because the match's own clock says a set has had time to
    finish."""
    monkeypatch.setattr(_fsc, "CONFIRM_MIN_S", 0.0)
    _with_feed(monkeypatch, "Andreeva", commence_delta=-3600.0)
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)          # arm (feed)
    ka.asks[T_FAV] = (0.33, 500.0)
    sweep(kalshi=ka, ledger=led, live=True)          # pend
    st = sweep(kalshi=ka, ledger=led, live=True)     # confirm: enter
    assert st["entered"] == 1 and len(ka.orders) == 1


def test_partial_fill_tops_up_to_the_hundred(fast_gates):
    """Owner: fills 'need to be close to $100 not $37'. The thin-book
    partial takes what shows; the next sweep buys the remainder when
    liquidity returns, and buying stops once less than the $5 floor is
    left to spend."""
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)          # arm
    ka.asks[T_FAV] = (0.33, 114.0)      # $37.62 showing < $100 want
    sweep(kalshi=ka, ledger=led, live=True)          # pend
    st = sweep(kalshi=ka, ledger=led, live=True)     # partial entry
    assert st["entered"] == 1 and ka.orders == [(T_FAV, 0.33, 114)]
    ka.asks[T_FAV] = (0.34, 500.0)      # liquidity returns
    st2 = sweep(kalshi=ka, ledger=led, live=True)
    assert st2.get("topup_fills") == 1
    # $100 - $37.62 = $62.38 remaining -> floor(62.38/0.34) = 183
    assert ka.orders[1] == (T_FAV, 0.34, 183)
    saved = led.get_state(KEY)
    assert saved["filled"] == 114 + 183
    assert saved["spent_usd"] == pytest.approx(37.62 + 183 * 0.34)
    day = led.get_state("fsc_day")
    assert day["entries"] == 1      # a top-up is not a new entry
    assert day["spent"] == pytest.approx(99.84)
    # ~$99.84 in: the remainder is under the floor — no further buys.
    s3 = sweep(kalshi=ka, ledger=led, live=True)
    assert not s3.get("topup_fills") and len(ka.orders) == 2
    with led._conn() as conn:
        rows = conn.execute(
            "SELECT qty, price FROM fills ORDER BY id").fetchall()
    assert [(r["qty"], r["price"]) for r in rows] == \
        [(114.0, 0.33), (183.0, 0.34)]


def test_topup_window_closes(fast_gates):
    """A remainder left after the window (default 30 min from first
    fill) stays unbought — a top-up an hour later would be a different
    trade at a different match state, not the owner's $100 entry."""
    ka = _Kalshi({T_FAV: (0.62, 500.0), T_DOG: (0.40, 500.0)})
    led = _led()
    sweep(kalshi=ka, ledger=led, live=True)
    ka.asks[T_FAV] = (0.33, 114.0)
    sweep(kalshi=ka, ledger=led, live=True)
    sweep(kalshi=ka, ledger=led, live=True)          # partial entry
    st = led.get_state(KEY)
    st["ts"] = time.time() - 7200
    led.set_state(KEY, st)
    ka.asks[T_FAV] = (0.34, 500.0)
    s = sweep(kalshi=ka, ledger=led, live=True)
    assert not s.get("topup_fills") and len(ka.orders) == 1


def test_pre_fix_armed_state_is_cleared_not_trusted(fast_gates):
    led = _led()
    led.set_state(KEY, {"fav_ticker": T_FAV, "fav_px": 0.60,
                        "armed_ts": time.time() - 3600})   # no "v": 2
    ka = _Kalshi({T_FAV: (0.28, 500.0), T_DOG: (0.75, 500.0)})
    st = sweep(kalshi=ka, ledger=led, live=True)
    assert st.get("rearmed_v1") == 1 and not ka.orders
    st2 = led.get_state(KEY) or {}
    assert st2.get("v") == 2 and st2.get("fav_ticker") == T_DOG, \
        "the re-arm must be v2 and on the side that actually trades " \
        "as the favorite — not the phantom the old state named"
