"""The crypto cross-venue scanner: identity join (matching, expiry, strike,
direction — including mismatch refusals), the 3c index-basis buffer,
would_fire measurement counting, live gating off by default, and the
atomic-claim live path copied from xv_watch."""

import time

from edge.ledger.service import Ledger
from edge.shadow.xv_crypto import (
    CryptoBinary,
    XVCryptoWatch,
    join_pairs,
    joinable,
    pair_key,
    parse_kalshi_market,
    parse_pmus_market,
)
from edge.venues.base import BookLevel, MarketBook

EXP = "2026-08-05T16:15:00+00:00"


# ── venue fakes (shape-matched to the real adapters) ────────────────────

class _Kalshi:
    name = "kalshi"

    def __init__(self, asks):          # token -> ask price
        self.asks = dict(asks)
        self.orders = []
        self.fill = True

    def taker_fee(self, price):
        return 0.07 * price * (1 - price)

    def get_book(self, market_id, ticker):
        px = self.asks.get(ticker)
        if px is None:
            return None
        return MarketBook(venue=self.name, market_id=market_id,
                          outcome_id=ticker, bids=[],
                          asks=[BookLevel(px, 50)], ts=time.time())

    def place_order(self, token, price, count, **kw):
        self.orders.append((token, price, count))
        n = count if self.fill else 0
        return {"ok": self.fill, "count": n, "price": price,
                "status": "filled" if self.fill else "unfilled",
                "order_id": f"ko{len(self.orders)}"}


class _Pmus:
    name = "polymarket-us"

    def __init__(self, asks):
        self.asks = dict(asks)
        self.orders = []

    def taker_fee(self, price):
        return 0.0

    def peek_book(self, slug):
        px = self.asks.get(slug)
        if px is None:
            return None
        return MarketBook(venue=self.name, market_id=slug, outcome_id=slug,
                          bids=[], asks=[BookLevel(px, 50)], ts=time.time())

    def place_order(self, token, price, count, **kw):
        self.orders.append((token, price, count))
        return {"ok": True, "count": count, "price": price,
                "status": "filled", "order_id": f"po{len(self.orders)}"}


def _cb(venue, token, strike=114250.0, side="above", exp=None):
    return CryptoBinary(venue=venue, token=token, underlying="BTC",
                        expiry_ts=exp or int(time.time()) + 600,
                        strike=strike, side=side, title=token)


def _watch(tmp_path, kalshi_ask, pmus_ask, live=False, **kw):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    exp = int(time.time()) + 600
    kcb = _cb("kalshi", "KXBTC-T1", side="above", exp=exp)
    pcb = _cb("polymarket-us", "btc-below-114250", side="below", exp=exp)
    ka = _Kalshi({"KXBTC-T1": kalshi_ask})
    pm = _Pmus({"btc-below-114250": pmus_ask})
    w = XVCryptoWatch(ledger=led, kalshi=ka, pmus=pm,
                      is_live=lambda: live, pmus_pub=False, **kw)
    w._discover_kalshi = lambda now: [kcb]
    w._discover_pmus = lambda now: [pcb]
    return w, led, ka, pm, (kcb, pcb)


# ── identity: parsing ───────────────────────────────────────────────────

def test_kalshi_parse_maps_strike_type_to_explicit_direction():
    above = parse_kalshi_market({
        "ticker": "KXBTC-26AUG051615-T114250", "strike_type": "greater",
        "floor_strike": 114249.99, "close_time": EXP})
    assert above is not None
    assert (above.side, above.strike, above.underlying) == \
        ("above", 114249.99, "BTC")
    below = parse_kalshi_market({
        "ticker": "KXBTC-26AUG051615-B114250", "strike_type": "less",
        "cap_strike": 114250.0, "close_time": EXP})
    assert below is not None and below.side == "below"
    assert above.expiry_min == below.expiry_min


def test_kalshi_parse_refuses_ranges_missing_fields_and_other_coins():
    # "between" is a two-strike range, not a binary at one strike.
    assert parse_kalshi_market({
        "ticker": "KXBTC-X", "strike_type": "between",
        "floor_strike": 1.0, "cap_strike": 2.0, "close_time": EXP}) is None
    # No close time -> no expiry identity -> refused.
    assert parse_kalshi_market({
        "ticker": "KXBTC-X", "strike_type": "greater",
        "floor_strike": 1.0}) is None
    # No strike number -> refused.
    assert parse_kalshi_market({
        "ticker": "KXBTC-X", "strike_type": "greater",
        "close_time": EXP}) is None
    # Different underlying is out of scope entirely.
    assert parse_kalshi_market({
        "ticker": "KXETH-X", "strike_type": "greater",
        "floor_strike": 3500.0, "close_time": EXP}) is None


def test_pmus_parse_yes_no_complement_inverts_the_event_direction():
    ev = {"title": "Bitcoin above $114,250 at 4:15 PM ET?", "endDate": EXP}
    yes = parse_pmus_market(ev, {"slug": "s-yes", "title": "Yes"})
    no = parse_pmus_market(ev, {"slug": "s-no", "title": "No"})
    assert yes is not None and (yes.side, yes.strike) == ("above", 114250.0)
    assert no is not None and no.side == "below"
    assert yes.expiry_min == no.expiry_min


def test_pmus_parse_explicit_market_direction_beats_event_text():
    ev = {"title": "Bitcoin price at 4:15 PM ET", "endDate": EXP}
    m = parse_pmus_market(ev, {"slug": "s1", "title": "Below $114,250"})
    assert m is not None and (m.side, m.strike) == ("below", 114250.0)


def test_pmus_parse_refuses_updown_ambiguity_and_non_btc():
    census = {}
    # Up/down carries an IMPLICIT strike (the interval's opening print):
    # no numeric strike, no join. Refused, and counted as such.
    ev = {"title": "Bitcoin Up or Down — 4:15 PM ET", "endDate": EXP}
    assert parse_pmus_market(ev, {"slug": "s-up", "title": "Up"},
                             census) is None
    assert census.get("no_strike") == 1
    # "above or below" in one proposition is ambiguous — never guessed.
    ev2 = {"title": "Bitcoin above or below $114,250?", "endDate": EXP}
    assert parse_pmus_market(ev2, {"slug": "s2", "title": "Yes"},
                             census) is None
    assert census.get("no_direction") == 1
    # Not bitcoin at all.
    ev3 = {"title": "Ethereum above $3,500 at 4:15 PM ET?", "endDate": EXP}
    assert parse_pmus_market(ev3, {"slug": "s3", "title": "Yes"},
                             census) is None
    assert census.get("not_btc") == 1
    # No expiry timestamp anywhere -> no identity.
    ev4 = {"title": "Bitcoin above $114,250?"}
    assert parse_pmus_market(ev4, {"slug": "s4", "title": "Yes"},
                             census) is None
    assert census.get("no_expiry") == 1


# ── identity: the join ──────────────────────────────────────────────────

def test_join_requires_underlying_expiry_minute_strike_and_complement():
    exp = int(time.time()) + 600
    k = _cb("kalshi", "K1", 114250.0, "above", exp)
    assert joinable(k, _cb("polymarket-us", "P1", 114250.0, "below", exp))
    # The venues' "$114,249.99 or above" convention is inside the 1c tol.
    k99 = _cb("kalshi", "K1", 114249.99, "above", exp)
    assert joinable(k99, _cb("polymarket-us", "P1", 114250.0, "below", exp))
    # Strike mismatch beyond the tolerance: refused.
    assert not joinable(k, _cb("polymarket-us", "P1", 114500.0, "below", exp))
    assert not joinable(k, _cb("polymarket-us", "P1", 114250.05, "below", exp))
    # Expiry a different minute: refused (same minute, different second: ok).
    assert not joinable(
        k, _cb("polymarket-us", "P1", 114250.0, "below", exp + 120))
    assert joinable(k, _cb("polymarket-us", "P1", 114250.0, "below",
                           (exp // 60) * 60 + 59))
    # Same direction on both venues is not a set: refused.
    assert not joinable(k, _cb("polymarket-us", "P1", 114250.0, "above", exp))
    # Same venue: refused.
    assert not joinable(k, _cb("kalshi", "K2", 114250.0, "below", exp))
    # Different underlying: refused.
    eth = CryptoBinary("polymarket-us", "P1", "ETH", exp, 114250.0, "below")
    assert not joinable(k, eth)


def test_join_pairs_returns_only_valid_pairs():
    exp = int(time.time()) + 600
    kalshi = [_cb("kalshi", "K1", 114250.0, "above", exp),
              _cb("kalshi", "K2", 115000.0, "above", exp)]
    pmus = [_cb("polymarket-us", "P1", 114250.0, "below", exp),
            _cb("polymarket-us", "P2", 116000.0, "below", exp)]
    pairs = join_pairs(kalshi, pmus)
    assert len(pairs) == 1
    assert pairs[0][0].token == "K1" and pairs[0][1].token == "P1"


# ── the basis buffer and measurement counters ───────────────────────────

def test_min_profit_absorbs_index_basis_a_2c_gap_never_fires(tmp_path):
    # kalshi 0.46 (fee ~1.67c) + pmus 0.50 => ~2.3c/set. xv_watch's sports
    # bar (2c) would fire; the crypto bar must NOT — the venues settle on
    # different indexes and 2.3c does not pay for the double-loss tail.
    w, led, ka, pm, _ = _watch(tmp_path, 0.46, 0.50, live=True)
    w._tick()
    assert w.stats["pairs_seen"] == 1
    assert 2.0 < w.stats["best_gap_c"] < 3.0
    assert w.stats["would_fire"] == 0
    assert w.stats["fired"] == 0
    assert not ka.orders and not pm.orders


def test_would_fire_counts_the_dislocation_without_placing_orders(tmp_path):
    # kalshi 0.45 (fee ~1.73c) + pmus 0.50 => ~3.3c/set: clears the bar.
    # Engine live, but EDGE_XV_CRYPTO_LIVE unset: MEASURE-ONLY.
    w, led, ka, pm, pair = _watch(tmp_path, 0.45, 0.50, live=True)
    w._tick()
    w._tick()
    assert w.stats["would_fire"] == 2, "every scan of the open gap counts"
    assert w.stats["fired"] == 0
    assert not ka.orders and not pm.orders
    assert w.stats["last_hit"]["mode"] == "measure"
    assert w.stats["last_hit"]["profit_per_set"] >= 0.03
    key = pair_key(*pair)
    assert not led.event_traded(f"xv_crypto_tried:{key}"), \
        "no claim burned while measuring"


def test_implausible_gap_is_refused_not_celebrated(tmp_path):
    # 0.20 + 0.50 = 30c "profit" — a mapping error wearing a costume.
    w, led, ka, pm, _ = _watch(tmp_path, 0.20, 0.50, live=True)
    w._tick()
    assert w.stats["would_fire"] == 0 and w.stats["fired"] == 0


def test_joined_and_scans_counters(tmp_path):
    w, led, ka, pm, _ = _watch(tmp_path, 0.46, 0.50)
    w._tick()
    w._tick()
    assert w.stats["scans"] == 2
    assert w.stats["joined"] == 1, "distinct identity pairs, not rescans"
    assert w.stats["kalshi_mkts"] == 1 and w.stats["pmus_mkts"] == 1


# ── live gating ─────────────────────────────────────────────────────────

def test_live_needs_both_the_env_flag_and_a_live_engine(tmp_path,
                                                        monkeypatch):
    # env flag alone, engine not live: measure-only.
    monkeypatch.setenv("EDGE_XV_CRYPTO_LIVE", "1")
    # Live-path tests must lift the owner arb pause (2026-08-08).
    monkeypatch.setenv("EDGE_ARB_PAUSE", "0")
    w, led, ka, pm, _ = _watch(tmp_path, 0.45, 0.50, live=False)
    w._tick()
    assert w.stats["would_fire"] == 1 and w.stats["fired"] == 0
    assert not ka.orders and not pm.orders


def test_live_fires_through_execute_cross_venue_with_atomic_claim(
        tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_XV_CRYPTO_LIVE", "1")
    # Live-path tests must lift the owner arb pause (2026-08-08).
    monkeypatch.setenv("EDGE_ARB_PAUSE", "0")
    w, led, ka, pm, pair = _watch(tmp_path, 0.45, 0.50, live=True)
    w._tick()
    assert w.stats["fired"] == 1
    assert ka.orders and pm.orders, "both venues ordered"
    claim = f"xv_crypto_tried:{pair_key(*pair)}"
    assert led.event_traded(claim), "claim held after money moved"
    assert led.get_state(claim), "state records how the claim resolved"
    day = led.get_state("xv_crypto_day")
    assert day and day["spent"] > 0
    assert w.stats["spent"] == day["spent"]
    # Second pass: the claim blocks a double fire.
    w._tick()
    assert w.stats["fired"] == 1


def test_clean_miss_releases_the_claim_so_the_pair_stays_retryable(
        tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_XV_CRYPTO_LIVE", "1")
    # Live-path tests must lift the owner arb pause (2026-08-08).
    monkeypatch.setenv("EDGE_ARB_PAUSE", "0")
    w, led, ka, pm, pair = _watch(tmp_path, 0.45, 0.50, live=True)
    ka.fill = False          # first (non-atomic) leg misses: flat, no fills
    w._tick()
    assert w.stats["fired"] == 1
    claim = f"xv_crypto_tried:{pair_key(*pair)}"
    assert not led.event_traded(claim), "clean miss gives the claim back"
    assert not pm.orders, "closer never attempted after a first-leg miss"
    # Books recover: the SAME pair may fire now.
    ka.fill = True
    w._tick()
    assert w.stats["fired"] == 2
    assert led.event_traded(claim)


def test_day_cap_stops_the_class(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setenv("EDGE_XV_CRYPTO_LIVE", "1")
    # Live-path tests must lift the owner arb pause (2026-08-08).
    monkeypatch.setenv("EDGE_ARB_PAUSE", "0")
    w, led, ka, pm, _ = _watch(tmp_path, 0.45, 0.50, live=True)
    led.set_state("xv_crypto_day",
                  {"day": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
                   "spent": w.day_usd})
    w._tick()
    assert w.stats["fired"] == 0
    assert w.stats["skipped_day_cap"] == 1
    assert not ka.orders and not pm.orders


def test_account_level_stops_block_the_live_path(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_XV_CRYPTO_LIVE", "1")
    # Live-path tests must lift the owner arb pause (2026-08-08).
    monkeypatch.setenv("EDGE_ARB_PAUSE", "0")
    w, led, ka, pm, _ = _watch(tmp_path, 0.45, 0.50, live=True)
    led.set_state("kill_switch", True)
    w._tick()
    assert w.stats["fired"] == 0
    assert w.stats["blocked_kill_switch"] == 1
    assert not ka.orders and not pm.orders


def test_default_env_knobs(monkeypatch):
    for var in ("EDGE_XV_CRYPTO_MIN_PROFIT", "EDGE_XV_CRYPTO_DAY_USD",
                "EDGE_XV_CRYPTO_LIVE"):
        monkeypatch.delenv(var, raising=False)
    w = XVCryptoWatch(ledger=None, kalshi=None, pmus=None,
                      is_live=lambda: False, pmus_pub=False)
    assert w.min_profit == 0.03, "3c basis buffer is the default"
    assert w.day_usd == 100.0
    # The live flag defaults off — asserted behaviorally in the gating
    # tests above; here we just pin the documented knob values.
    assert w.max_usd == 10.0 and w.strike_tol == 0.01


def test_same_game_guard_passes_the_pair_naturally():
    # The pair holds exactly ONE Kalshi side; its complement lives on
    # Polymarket US, so kalshi_guard's one-position-per-game rule sees no
    # opposite Kalshi side and imposes nothing.
    from edge.shadow.kalshi_guard import cross_side_cap, open_kalshi_sides

    assert cross_side_cap({}, "KXBTC-26AUG051615-T114250", 0.45, 7) == 7
    sides = {"26AUG051615": [{"ticker": "KXBTC-26AUG051615-T114250",
                              "shares": 3, "avg_cost": 0.45}]}
    # Re-buying the same ticker is governed by the claim, not the guard.
    assert cross_side_cap(sides, "KXBTC-26AUG051615-T114250", 0.45, 7) == 7
    # And a PMUS fill never enters the Kalshi sides map at all.
    class _L:
        @staticmethod
        def open_positions(live_only=True):
            return [{"market_key": "polymarket-us:btc-below-114250",
                     "shares": 3, "avg_cost": 0.5}]
    assert open_kalshi_sides(_L) == {}
