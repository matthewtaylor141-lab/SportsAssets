"""Kalshi crypto copy leg (owner order 2026-08-21): the math rules.

The fee-loaded limit is proven over the entire cent grid, the parser
over the real market families observed in the census, and the sweep
over stubbed venue/ledger — including the fail-closed refusals, which
are the actual safety surface."""

import time

import pytest

from edge.shadow import kalshi_crypto as kc


# ── Fee-loaded same-or-better: the core guarantee ────────────────────

def test_fee_loaded_limit_never_exceeds_his_price_anywhere():
    """For EVERY his_price cent in the tradeable band: our limit plus
    the taker fee at that limit is <= his price (a filled copy can
    never cost more per contract than the source paid), and the limit
    is MAXIMAL (one more cent would break the bound — we never leave
    fill probability on the table)."""
    for cents in range(10, 96):
        p = cents / 100.0
        limit = kc.fee_loaded_limit(p)
        assert limit is not None, p
        assert limit + kc.taker_fee(limit) <= p + 1e-9, p
        nxt = round(limit + 0.01, 2)
        assert nxt + kc.taker_fee(nxt) > p, (p, limit)


def test_taker_fee_shape():
    assert kc.taker_fee(0.5) == pytest.approx(0.0175)
    assert kc.taker_fee(0.9) == pytest.approx(0.0063)
    assert kc.taker_fee(0.1) == pytest.approx(0.0063)


# ── Parser: real market families from the census ─────────────────────

def test_parse_range_daily():
    c = kc.parse_candidate(
        "will-the-price-of-bitcoin-be-between-66000-68000-on-august-21",
        "")
    assert c is not None
    assert c["asset"] == "BTC" and c["kind"] == "range"
    assert c["strikes"] == (66000.0, 68000.0)
    assert c["klass"] == "daily-noon"


def test_parse_threshold_daily_and_explicit_hour():
    c = kc.parse_candidate("solana-above-90-on-august-21-2026", "")
    assert c is not None
    assert c["asset"] == "SOL" and c["kind"] == "above"
    assert c["strikes"] == (90.0,)
    e = kc.parse_candidate(
        "will-the-price-of-bitcoin-be-above-67500-at-5pm-et-on-august-21-2026",
        "")
    assert e is not None and e["klass"] == "explicit-hour"
    # 5pm ET is five hours after the daily-noon default.
    assert e["deadline_ts"] - c_noon_ts("august", 21) == 5 * 3600


def c_noon_ts(month, day):
    c = kc.parse_candidate(f"bitcoin-above-1-on-{month}-{day}-2026", "")
    return c["deadline_ts"]


def test_path_dependent_and_exotic_all_refused():
    for slug in (
        "will-solana-reach-100-on-august-21-2026",
        "will-ethereum-dip-to-1500-by-december-31-2026",
        "will-bitcoin-reach-82pt5k-in-august-2026",
        "will-april-be-the-best-month-for-bitcoin-in-2026",
        "btc-updown-august-21-3pm",
        "extended-fdv-above-500m-one-day-after-launch",
    ):
        assert kc.parse_candidate(slug, "") is None, slug


def test_below_refused_never_shorted():
    assert kc.parse_candidate(
        "will-the-price-of-bitcoin-be-below-60000-on-august-21", "") is None


def test_no_deadline_refused():
    assert kc.parse_candidate("bitcoin-above-67500", "") is None


def test_kilo_and_decimal_number_grammar():
    c = kc.parse_candidate("bitcoin-above-82pt5k-on-september-2-2026", "")
    assert c["strikes"] == (82500.0,)
    c = kc.parse_candidate("xrp-above-1pt6-on-september-2-2026", "")
    assert c["asset"] == "XRP" and c["strikes"] == (1.6,)


# ── Strike equality: cent conventions in, neighboring bands out ──────

def test_strike_tolerance():
    assert kc._strike_eq(67499.99, 67500.0)
    assert not kc._strike_eq(67250.0, 67500.0)
    assert kc._strike_eq(89.99, 90.0)
    assert not kc._strike_eq(88.0, 90.0)


def test_match_requires_deadline_within_5_minutes():
    cand = {"asset": "BTC", "kind": "above", "strikes": (67500.0,),
            "deadline_ts": 1_000_000.0}
    mk = {"ticker": "KXBTC-X-T67499.99", "asset": "BTC", "kind": "above",
          "strikes": (67499.99,), "close_ts": 1_000_000.0 + 200}
    assert kc.match_market(cand, [mk]) is mk
    mk_far = dict(mk, close_ts=1_000_000.0 + 3600)
    assert kc.match_market(cand, [mk_far]) is None


# ── Sizing: one zero off, banded, whole contracts ────────────────────

def test_plan_takes_a_zero_off():
    limit, contracts = kc.plan(0.60, 100.0)
    assert limit == 0.58
    assert contracts == int(10.0 / 0.58) == 17


def test_plan_caps_and_bands():
    assert kc.plan(0.05, 100.0) is None       # below band
    assert kc.plan(0.97, 100.0) is None       # above band
    assert kc.plan(0.60, 3.0) is None         # $0.30 buys no contract
    limit, contracts = kc.plan(0.50, 100000.0)
    assert limit * contracts <= kc.MAX_USD + limit


# ── Sweep integration over stubs ─────────────────────────────────────

class _Client:
    def __init__(self):
        self.orders = []

    def place_order(self, ticker, price, count, client_order_id, taker):
        self.orders.append((ticker, price, count, client_order_id, taker))
        return {"ok": True, "filled_count": count,
                "order_id": f"ord-{client_order_id}"}


class _Ledger:
    def __init__(self):
        self.fills = []
        self.positions = {}
        self.state = {}

    def set_state(self, key, value):
        self.state[key] = value

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def realized_pnl_since_for_category(self, ts, category):
        return 0.0

    def position(self, key):
        return self.positions.get(key)

    def open_positions(self, live_only=False):
        return [{"market_key": k} for k in self.positions]

    def record_fill(self, **kw):
        self.fills.append(kw)
        self.positions[kw["market_key"]] = kw
        return {"applied": True, "realized": 0.0}


class _Risk:
    is_live = True


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    kc.funnel.update(seen=0, placed=0, filled=0, contracts=0,
                     deployed_usd=0.0, fees_usd=0.0, refused={},
                     by_whale={}, last_error=None, halted=False)
    monkeypatch.setattr(kc, "kalshi_crypto_markets", lambda client: [
        {"ticker": "KXBTC-26AUG2112-T67499.99", "asset": "BTC",
         "kind": "above", "strikes": (67499.99,),
         "close_ts": kc.parse_candidate(
             "bitcoin-above-67500-on-august-21-2026", "")["deadline_ts"]},
    ])
    import edge.shadow.kalshi_guard as g
    monkeypatch.setattr(g, "live_blocked", lambda ledger, scope: None)


def _cand(**over):
    c = {"id": 7, "username": "0xf705fa04",
         "market_slug": "bitcoin-above-67500-on-august-21-2026",
         "market_title": "", "side": "BUY", "price": 0.60,
         "notional": 100.0, "ts_epoch": time.time()}
    c.update(over)
    return c


def test_sweep_places_the_tenth_sized_fee_loaded_order():
    cl, led = _Client(), _Ledger()
    kc.sweep(cl, led, _Risk(), [_cand()])
    assert cl.orders == [("KXBTC-26AUG2112-T67499.99", 0.58, 17,
                          "kcr-7", True)]
    assert led.fills and led.fills[0]["category"] == "kalshi_crypto"
    assert led.fills[0]["decision"]["whale"] == "0xf705fa04"
    assert kc.funnel["filled"] == 1


def test_sweep_refuses_stale_unknown_and_never_add():
    cl, led = _Client(), _Ledger()
    kc.sweep(cl, led, _Risk(), [
        _cand(id=1, ts_epoch=time.time() - 500),
        _cand(id=2, username="somebody"),
    ])
    assert cl.orders == []
    assert kc.funnel["refused"]["stale"] == 1
    assert kc.funnel["refused"]["unknown-whale"] == 1
    # First fill takes the market; the second candidate on the same
    # market must refuse as never-add.
    kc.sweep(cl, led, _Risk(), [_cand(id=3)])
    kc.sweep(cl, led, _Risk(), [_cand(id=4)])
    assert len(cl.orders) == 1
    assert kc.funnel["refused"]["never-add"] == 1


def test_leg_breaker_halts_all_orders():
    cl, led = _Client(), _Ledger()
    led.realized_pnl_since_for_category = lambda ts, cat: -kc.HALT_USD
    kc.sweep(cl, led, _Risk(), [_cand()])
    assert cl.orders == []
    assert "leg breaker" in str(kc.funnel["halted"])


def test_sweep_holds_when_engine_not_live():
    """Audit 2026-08-21: the sweep placed REAL orders regardless of
    engine mode and labeled the fills PAPER — invisible to the leg
    breaker. A demoted engine must mean a silent leg."""
    class _Paper:
        is_live = False

    cl, led = _Client(), _Ledger()
    kc.sweep(cl, led, _Paper(), [_cand()])
    assert cl.orders == []
    assert "not live" in str(kc.funnel["halted"])


def test_sweep_refuses_sells_and_missing_id():
    """Direction and identity are fail-closed (audit 2026-08-21): a
    SELL copied as our BUY is the opposite bet, and a missing id would
    collapse every candidate onto fill_uid 'kcr-None'."""
    cl, led = _Client(), _Ledger()
    kc.sweep(cl, led, _Risk(), [
        _cand(id=1, side="SELL"),
        _cand(id=2, side=""),
        _cand(id=None),
    ])
    assert cl.orders == []
    assert kc.funnel["refused"]["not-a-buy"] == 2
    assert kc.funnel["refused"]["no-id"] == 1


def test_fill_marks_kalshi_inline_before_recording():
    """The inline marker is what stops sync_kalshi_fills re-recording
    the same contracts under kalshi-fill-{trade_id} — the doubled-
    position class from the 2026-08-04 engine audit."""
    cl, led = _Client(), _Ledger()
    kc.sweep(cl, led, _Risk(), [_cand()])
    assert led.fills, "fill should have recorded"
    assert led.get_state("kalshi_inline:ord-kcr-7")["uid"] == "kcr-7"


def test_strike_tolerance_is_relative_at_every_scale():
    """The old $0.02 absolute floor matched DOGE $0.12 against $0.13 —
    a strictly different bet. Purely relative 2bp covers every cent
    convention and never spans a neighboring band."""
    assert not kc._strike_eq(0.12, 0.13)          # DOGE neighbor bands
    assert not kc._strike_eq(1.60, 1.62)          # XRP neighbor bands
    assert kc._strike_eq(0.12999, 0.13)           # sub-dollar convention
    assert kc._strike_eq(2.9999, 3.00)            # XRP convention
    assert kc._strike_eq(67499.99, 67500.0)       # BTC convention


def test_unparsed_clock_time_refused_not_defaulted():
    """A slug naming a clock time the parser did not understand must
    refuse, never default to daily-noon (audit 2026-08-21: the 8pm bet
    was matching the noon market)."""
    for slug in ("bitcoin-above-67500-at-8pm-on-august-21-2026",
                 "bitcoin-above-67500-at-8pm-est-on-august-21-2026"):
        assert kc.parse_candidate(slug, "") is None
    assert kc.funnel["refused"]["deadline:unparsed-hour"] == 2
    # Minutes in the explicit form parse exactly, not approximately.
    c = kc.parse_candidate(
        "bitcoin-above-67500-at-4-30pm-et-on-august-21-2026", "")
    assert c is not None and c["klass"] == "explicit-hour"
    half_past = kc.parse_candidate(
        "bitcoin-above-67500-at-4pm-et-on-august-21-2026", "")
    assert c["deadline_ts"] - half_past["deadline_ts"] == 30 * 60


def test_ambiguous_strike_match_refused():
    """Two listed strikes inside tolerance = ambiguity = refusal;
    otherwise listing order silently decides which bet we copy."""
    close = kc.parse_candidate(
        "bitcoin-above-67500-on-august-21-2026", "")["deadline_ts"]
    twin = {"asset": "BTC", "kind": "above", "close_ts": close}
    ms = [{**twin, "ticker": "A", "strikes": (67499.99,)},
          {**twin, "ticker": "B", "strikes": (67500.01,)}]
    cand = {"asset": "BTC", "kind": "above", "strikes": (67500.0,),
            "deadline_ts": close}
    assert kc.match_market(cand, ms) is None
    assert kc.match_market(cand, ms[:1])["ticker"] == "A"
