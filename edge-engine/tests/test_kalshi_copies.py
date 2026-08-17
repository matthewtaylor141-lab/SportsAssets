"""The Kalshi copy leg: his price +2%, once per position (ACROSS venues),
day-capped, strict name join, $3 per copy uniform (owner directive
2026-08-05) — and routed to the best-priced venue at placement."""

import tempfile
import time

import pytest

from edge.ledger.service import Ledger
from edge.shadow import kalshi_copies as _kc
from edge.shadow.kalshi_copies import _limit_for, sweep
from edge.venues.base import BookLevel, MarketBook
from edge.venues.mapper import VenueMarket


@pytest.fixture(autouse=True)
def _fresh_discovery_cache():
    """League discovery is cached ACROSS sweeps in production; between
    tests a recycled fake-adapter id must never serve another test's
    market list. Same for the venue-guard cache and the once-per-boot
    orphan reconcile flag."""
    _kc._DISCO_CACHE.clear()
    _kc._VENUE_GUARD_CACHE.clear()
    _kc._ORPHANS_RECONCILED["done"] = False
    yield
    _kc._DISCO_CACHE.clear()
    _kc._VENUE_GUARD_CACHE.clear()
    _kc._ORPHANS_RECONCILED["done"] = False


class _Kalshi:
    name = "kalshi"

    def __init__(self, ask, outcomes=None, ask_size=500.0):
        self.ask = ask
        self.ask_size = ask_size
        self.orders = []
        self._outcomes = outcomes or {"Dallas Wings": "T-DAL",
                                      "Chicago Sky": "T-CHI"}

    def taker_fee(self, price):
        return 0.07 * price * (1 - price)

    def discover_markets(self, league_codes):
        return [VenueMarket(market_id="KXWNBAGAME-26AUG04DALCHI",
                            title="Wings at Sky", league_code="wnba",
                            outcome_tokens=dict(self._outcomes))]

    def get_book(self, market_id, ticker):
        return MarketBook(venue=self.name, market_id=market_id,
                          outcome_id=ticker, bids=[],
                          asks=[BookLevel(self.ask, self.ask_size)],
                          ts=time.time())

    def place_order(self, ticker, price, count, **kw):
        self.orders.append((ticker, price, count))
        return {"ok": True, "count": count, "price": price,
                "status": "filled"}


# Fixture whale is the promoted 0x2c33 wallet: unrestricted, $50 default
# clip, no band or fee-floor special-casing. (It was HomeRunHazard until
# the owner paused him 2026-08-11 — a paused whale copies nothing, which
# is the pause test's job to prove, not every test's.)
_ROW = {"slug": "wnba-dal-chi-2026-08-04", "outcome": "Dallas Wings",
        "price": 0.50, "whale": "0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563-1759935795465",
        "entered_ts": time.time() - 60}


def _run(ask, rows=None, live=True, led=None):
    led = led or Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(ask)
    st = sweep(kalshi=ka, ledger=led, identities=rows or [dict(_ROW)],
               live=live)
    return st, ka, led


def test_limit_is_his_price_same_or_better():
    """Owner order 2026-08-12: a copy may only cost his price or less
    (fee included) — the +2% chase allowance is retired."""
    assert _limit_for(0.50) == 0.50
    assert _limit_for(0.30) == 0.30
    assert _limit_for(0.29) == 0.29      # float-floor regression guard
    assert _limit_for(0.985) == 0.98
    assert _limit_for(0.997) == 0.99


def test_copies_when_kalshi_is_inside_his_tolerance():
    st, ka, led = _run(0.48)   # eff = 0.48 + fee(0.0175) <= his 0.50
    assert st["matched"] == 1 and st["copied"] == 1
    assert ka.orders == [("T-DAL", 0.48, 312)]  # $150 -> 312 contracts
    assert led.get_state("kcopy:wnba-dal-chi-2026-08-04:Dallas Wings")


def test_outside_tolerance_is_not_a_copy():
    st, ka, _ = _run(0.505)  # his 0.50: eff 0.505+fee > his price
    assert st["matched"] == 1 and st["copied"] == 0
    assert not ka.orders


def test_sport_assignments_gate_the_sweep():
    """Owner directive 2026-08-06: cell-gated whales copy ONLY their
    assigned sport(s) — swisstony is soccer-only, so his WNBA identity
    is refused. RN1 and the 0x2c33 wallet are UNRESTRICTED (owner
    decisions 2026-08-06/10, each on his own live record) and copy
    anywhere; the paused HomeRunHazard copies nowhere at all."""
    row = {**_ROW, "whale": "swisstony"}
    st, ka, _ = _run(0.48, rows=[row])
    assert st.get("skipped_sport") == 1
    assert not ka.orders
    st2, ka2, _ = _run(0.48)     # 0x2c33 + wnba ML: unrestricted
    assert ka2.orders == [("T-DAL", 0.48, 312)]  # $150 -> 312 contracts
    hrh = {**_ROW, "whale": "HomeRunHazard"}
    sth, kah, _ = _run(0.48, rows=[hrh])
    assert sth.get("skipped_sport") == 1 and not kah.orders, \
        "paused whale refused even in his formerly-assigned cell"
    row3 = {**_ROW, "whale": "RN1"}
    st3, ka3, _ = _run(0.48, rows=[row3])
    assert st3["copied"] == 1
    # RN1 clips at $150 on Kalshi (owner approval 2026-08-11 round 4):
    # floor(150/0.48) = 312 contracts.
    assert ka3.orders == [("T-DAL", 0.48, 312)]


def test_swisstony_resumes_behind_the_soccer_price_floor():
    """Owner order 2026-08-12: soccer resumed (halt of 2026-08-11
    lifted) behind the 40c price floor. swisstony keeps his $200 whale
    clip but soccer limits at $100 per event (same order) via the
    (whale, sport) override. A qualifying moneyline fires at the $100
    soccer clip; a ladder's improbable sub-40c rung still refuses."""
    from edge.shadow.kalshi_copies import (PER_COPY_USD,
                                           PER_COPY_USD_SPORT,
                                           _per_copy_usd)
    assert PER_COPY_USD["swisstony"] == 200.00
    assert PER_COPY_USD_SPORT[("swisstony", "soccer")] == 100.00
    assert _per_copy_usd("SwissTony", "epl-ars-che-2026-08-15") == 100.00
    assert _per_copy_usd("SwissTony", "aec-atp-a-b-2026-08-15") == 200.00
    assert _per_copy_usd("rn1", "epl-ars-che-2026-08-15") == 200.00  # soccer cell (2026-08-17)
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(0.72, outcomes={"Arsenal": "T-ARS", "Chelsea": "T-CHE"})
    row = {"slug": "epl-ars-che-2026-08-15", "outcome": "Arsenal",
           "price": 0.74, "whale": "swisstony",
           "entered_ts": time.time() - 60}
    st = sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert st["copied"] == 1
    assert ka.orders == [("T-ARS", 0.72, 138)]   # floor($100 / 0.72)
    # The improbable rung (his price 22c) refuses at the policy gate:
    led2 = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka2 = _Kalshi(0.20, outcomes={"Arsenal": "T-ARS", "Chelsea": "T-CHE"})
    st2 = sweep(kalshi=ka2, ledger=led2,
                identities=[{**row, "price": 0.22}], live=True)
    assert st2.get("skipped_sport") == 1
    assert not ka2.orders


def test_promoted_whale_0x2c33_copies_at_the_default_clip():
    """Owner approval 2026-08-10: the vetted wallet copies UNRESTRICTED
    (like RN1) at the $150 base (owner maximize order 2026-08-17) — floor(150/0.48) = 312 contracts on
    the standard fixture."""
    row = {**_ROW,
           "whale": "0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563"
                    "-1759935795465"}
    st, ka, _ = _run(0.48, rows=[row])
    assert st["copied"] == 1
    assert ka.orders == [("T-DAL", 0.48, 312)]


def test_held_ticker_refuses_a_second_buy_from_any_identity():
    """2026-08-10 evening, the $204 challenger position: the same whale
    outcome reached the sweep as TWO identities (two PM asset ids, two
    slugs, two distinct kcopy claims) and bought the same Kalshi ticker
    twice. Venue-side never-add: an open position on the ticker refuses
    every later buy, whatever identity proposes it."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    st1, ka1, _ = _run(0.48, led=led)
    assert st1["copied"] == 1 and ka1.orders
    # Same outcome again under a DIFFERENT slug (fresh claim key) —
    # the whale feed lists the same game with the teams flipped.
    row2 = {**_ROW, "slug": "wnba-chi-dal-2026-08-04",
            "entered_ts": time.time() - 30}
    st2, ka2, _ = _run(0.45, rows=[row2], led=led)
    assert st2.get("skipped_held_ticker") == 1
    assert not ka2.orders, "a held ticker must never be bought again"


def test_one_copy_per_position_ever():
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    st1, _, _ = _run(0.48, led=led)
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
    st, ka, led = _run(0.48, live=False)
    assert st["copied"] == 1
    assert not ka.orders
    assert not led.get_state("kcopy:wnba-dal-chi-2026-08-04:Dallas Wings")


def test_smoke_order_fires_exactly_once_ever(monkeypatch):
    # Smoke is engine-class, and the engine class is RETIRED by default
    # (owner 2026-08-12). monkeypatch, not os.environ: setting it raw
    # leaked an armed strategy into every test that ran afterwards.
    monkeypatch.setenv("EDGE_STRATEGY_LIVE", "1")
    monkeypatch.setenv("EDGE_STRATEGY_RETIRED", "0")
    monkeypatch.setenv("EDGE_ENGINE_TRADES", "1")  # 08-17 hard-off open
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

    ka = _ZeroFill(0.48)
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True)
    assert st["copied"] == 0 and st.get("ioc_zero_fill") == 1
    assert not led.get_state("kcopy:wnba-dal-chi-2026-08-04:Dallas Wings")
    # next sweep retries: no claim recorded
    st2 = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True)
    assert st2.get("skipped_claimed", 0) == 0


def test_smoke_zero_fill_is_not_done(monkeypatch):
    monkeypatch.setenv("EDGE_STRATEGY_LIVE", "1")
    monkeypatch.setenv("EDGE_STRATEGY_RETIRED", "0")
    monkeypatch.setenv("EDGE_ENGINE_TRADES", "1")  # 08-17 hard-off open
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


def test_stale_positions_are_never_copied():
    """A days-old whale entry has no copy edge left — and an identity
    with no timestamp is stale by definition, not grandfathered."""
    old = {**_ROW, "entered_ts": time.time() - 86_400}
    st, ka, _ = _run(0.48, rows=[old])
    assert st.get("skipped_stale") == 1 and not ka.orders
    untimed = {k: v for k, v in _ROW.items() if k != "entered_ts"}
    st2, ka2, _ = _run(0.48, rows=[untimed])
    assert st2.get("skipped_stale") == 1 and not ka2.orders


def test_collapsed_price_is_information_not_a_discount():
    """His entry 0.50, ask 0.20: the market learned something. The old
    upper-bound-only tolerance bought exactly these losers-in-progress."""
    st, ka, _ = _run(0.20)
    assert st.get("skipped_collapsed") == 1
    assert st["copied"] == 0 and not ka.orders


# ── One copy per position ACROSS venues + best-venue routing
#    (owner directive 2026-08-05) ──────────────────────────────────────


class _Pmus:
    """Stream-cache-only PMUS stub: ask=None models a cache miss."""

    name = "polymarket-us"

    def __init__(self, ask=None):
        self.ask = ask
        self.peeked = []

    def peek_book(self, slug):
        self.peeked.append(slug)
        if self.ask is None:
            return None
        return MarketBook(venue=self.name, market_id=slug, outcome_id=slug,
                          bids=[], asks=[BookLevel(self.ask, 50)],
                          ts=time.time())


def test_pmus_filled_copy_is_never_duplicated_on_kalshi():
    """A position the fast PMUS leg already copied (pmus_copied from the
    platform's live_orders) must be skipped no matter how good the
    Kalshi book looks — one copy per whale position across venues."""
    row = {**_ROW, "pmus_copied": True}
    st, ka, _ = _run(0.48, rows=[row])
    assert st.get("skipped_already_copied") == 1
    assert st["copied"] == 0 and not ka.orders


def test_pmus_better_priced_routes_the_copy_away_from_kalshi():
    """DEFAULT routing (owner 2026-08-10 night: best price, every copy):
    Kalshi ask 0.48 -> eff ~0.4975 fee-loaded; PMUS shows 0.49. PMUS is
    the better venue at placement, so the copy defers to the reclaim."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka, pm = _Kalshi(0.48), _Pmus(0.49)
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True,
               pmus=pm)
    assert st.get("routed_pmus_better") == 1
    assert st["copied"] == 0 and not ka.orders
    # The peek resolved his side structurally: atc grammar, 'bal' code.
    assert pm.peeked == ["atc-wnba-dal-chi-2026-08-04-dal"]


def test_kalshi_keeps_the_copy_when_prefer_kalshi_is_pinned(monkeypatch):
    """EDGE_KCOPY_PREFER_KALSHI=1 restores the 2026-08-09 volume
    routing: a copy that clears every Kalshi gate places on Kalshi even
    when PMUS shows a marginally better ask — no deference, no peek."""
    monkeypatch.setenv("EDGE_KCOPY_PREFER_KALSHI", "1")
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka, pm = _Kalshi(0.48), _Pmus(0.49)
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True,
               pmus=pm)
    assert st.get("routed_pmus_better") is None
    assert st["copied"] == 1 and ka.orders == [("T-DAL", 0.48, 312)]


def test_kalshi_places_when_it_is_cheaper_fee_loaded():
    """PMUS 0.55 vs Kalshi eff ~0.4975: Kalshi is the best-priced venue
    at placement, so the $3 copy lands there exactly as before."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka, pm = _Kalshi(0.48), _Pmus(0.55)
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True,
               pmus=pm)
    assert st.get("routed_pmus_better") is None
    assert st["copied"] == 1
    assert ka.orders == [("T-DAL", 0.48, 312)]


def test_no_pmus_book_falls_through_to_kalshi(monkeypatch):
    """peek_book cache miss (returns None): no PMUS price is visible
    cheaply, so Kalshi places — PMUS couldn't fill/map. (Legacy routing
    so the peek path is exercised at all.)"""
    monkeypatch.setenv("EDGE_KCOPY_PREFER_KALSHI", "0")
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka, pm = _Kalshi(0.48), _Pmus(None)
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True,
               pmus=pm)
    assert pm.peeked, "the peek must have been attempted"
    assert st["copied"] == 1 and ka.orders == [("T-DAL", 0.48, 312)]


# ── Venue split (owner directive 2026-08-07): Kalshi first claim on
#    half the flow; filled copies claimed back to the platform ────────


# The venue-split mechanics below need assets on BOTH sides of the
# split; the production default is now 100% Kalshi-first (owner
# 2026-08-09), so pin the historical 50/50 for these tests.
import os as _os
_os.environ["KALSHI_FIRST_PCT"] = "50"


def _asset(want_kalshi_first: bool) -> str:
    from edge.shadow.copy_sports import kalshi_first
    return next(str(i) for i in range(200)
                if kalshi_first(str(i)) is want_kalshi_first)


def test_kalshi_first_assets_also_route_to_the_better_venue():
    """Owner 2026-08-10 night ("best price"): the peek prices EVERY
    copy now, including assets the venue split handed to Kalshi. PMUS
    0.49 beats Kalshi's fee-loaded ~0.4975, so even a Kalshi-first
    asset defers to the 2-minute PMUS reclaim."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka, pm = _Kalshi(0.48), _Pmus(0.49)
    st = sweep(kalshi=ka, ledger=led,
               identities=[{**_ROW, "asset": _asset(True)}],
               live=True, pmus=pm)
    assert pm.peeked, "best-price routing must consult PMUS"
    assert st.get("routed_pmus_better") == 1
    assert st["copied"] == 0 and not ka.orders


def test_pm_first_assets_still_defer_to_a_better_pmus(monkeypatch):
    monkeypatch.setenv("EDGE_KCOPY_PREFER_KALSHI", "0")
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka, pm = _Kalshi(0.48), _Pmus(0.49)
    st = sweep(kalshi=ka, ledger=led,
               identities=[{**_ROW, "asset": _asset(False)}],
               live=True, pmus=pm)
    assert st.get("routed_pmus_better") == 1
    assert st["copied"] == 0 and not ka.orders


def test_filled_copy_claims_the_position_back():
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(0.48)
    claims = []
    sweep(kalshi=ka, ledger=led,
          identities=[{**_ROW, "asset": "777"}], live=True,
          on_copied=lambda a, t, w: claims.append((a, t, w)))
    assert claims == [("777", "T-DAL", "0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563-1759935795465")]


def test_claim_post_failure_never_blocks_the_copy():
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(0.48)

    def boom(a, t, w):
        raise RuntimeError("api down")

    st = sweep(kalshi=ka, ledger=led,
               identities=[{**_ROW, "asset": "9"}], live=True,
               on_copied=boom)
    assert st["copied"] == 1 and st.get("claim_post_fail") == 1
    assert ka.orders, "the order itself must have been placed"


# ── Maker-first copies (owner directive 2026-08-08: "more fires on
#    Kalshi"): when the taker fee prices a copy out of his+2%, rest a
#    fee-free maker bid instead of walking away ─────────────────────────


class _MakerKalshi(_Kalshi):
    """Adds the maker planning surface the real adapter exposes."""

    def plan_maker_order(self, limit_price, best_ask, edge, threshold,
                         market_ticker=None, edge_is_fee_net=False):
        px = round(min(limit_price, best_ask - 0.01), 2)
        if px >= 0.01 and px < best_ask:
            return px, False
        return None

    def place_order(self, ticker, price, count, client_order_id="",
                    taker=True):
        self.orders.append((ticker, price, count, taker))
        return {"ok": True, "count": count if taker else 0, "price": price,
                "status": "filled" if taker else "resting",
                "order_id": f"ord-{len(self.orders)}"}


def test_taker_priced_out_rests_a_fee_free_maker_on_kalshi_first(monkeypatch):
    """At his 0.50 the limit is 0.51; ask 0.51 + fee ≈ 0.527 > limit, so
    the taker path refuses — the maker path must rest at the limit (one
    tick inside the ask), GTC, without burning the once-ever claim."""
    monkeypatch.setenv("EDGE_KCOPY_MAKER", "1")   # taker-only default (2026-08-12)
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _MakerKalshi(0.51)
    row = {**_ROW, "asset": _asset(True)}
    st = sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert st["copied"] == 0                       # nothing filled yet
    assert st.get("maker_rested") == 1
    # 0x2c33 fixture clips $150 (2026-08-17): floor(150/0.50) = 300, maker.
    assert ka.orders == [("T-DAL", 0.50, 300, False)]
    claim = "kcopy:wnba-dal-chi-2026-08-04:Dallas Wings"
    assert not led.get_state(claim), "claim burns on FILL, not on rest"
    # Second sweep while the rest is live: no duplicate order.
    st2 = sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert st2.get("maker_resting") == 1
    assert len(ka.orders) == 1


def test_stale_rest_reprices_only_on_confirmed_cancel(monkeypatch):
    """The book ran away from a resting bid (ask moved >2c above our
    price): cancel and requote at the fresh book. 'gone'/'error' cancel
    results must NOT repost — a filled-then-reposted rest is a doubled
    copy, an ambiguous one is a stacked order."""
    monkeypatch.setenv("EDGE_KCOPY_MAKER", "1")   # taker-only default (2026-08-12)
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _MakerKalshi(0.51)
    ka.cancel_results = ["cancelled"]
    ka.cancels = []
    ka.cancel_order = lambda oid: (ka.cancels.append(oid) or
                                   ka.cancel_results.pop(0))
    row = {**_ROW, "asset": _asset(True)}
    sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert len(ka.orders) == 1                     # initial rest at 0.50
    # Book moves away (ask 0.53): under same-or-better (owner order
    # 2026-08-12) the limit IS his 0.50 — the stale rest is cancelled
    # and there is nothing competitive to requote (dead-rest rule).
    ka.ask = 0.53
    st = sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert st.get("reprice_cancelled") == 1 and ka.cancels
    assert st.get("skipped_dead_rest") == 1
    assert len(ka.orders) == 1                     # never chases past him
    # Ambiguous cancel: no repost, rest context cleared for next look.
    ka2 = _MakerKalshi(0.51)
    ka2.cancel_order = lambda oid: "error"
    led2 = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    sweep(kalshi=ka2, ledger=led2, identities=[row], live=True)
    ka2.ask = 0.53
    st2 = sweep(kalshi=ka2, ledger=led2, identities=[row], live=True)
    assert st2.get("reprice_error") == 1
    assert len(ka2.orders) == 1                    # no repost


def test_pm_first_assets_do_not_rest_makers():
    """The venue split still governs: a PM-first asset priced out as
    taker walks away — the PMUS leg owns it."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _MakerKalshi(0.51)
    st = sweep(kalshi=ka, ledger=led,
               identities=[{**_ROW, "asset": _asset(False)}], live=True)
    assert not ka.orders and not st.get("maker_rested")


def test_positions_near_the_sweep_reclaim_never_rest():
    """A rest whose 15-minute window could overlap the PMUS sweep's
    one-hour reclaim risks a cross-venue double copy — refused."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _MakerKalshi(0.51)
    old = {**_ROW, "asset": _asset(True),
           "entered_ts": time.time() - (45 * 60 - 300)}   # 40 min old
    st = sweep(kalshi=ka, ledger=led, identities=[old], live=True)
    assert not ka.orders and not st.get("maker_rested")


def test_async_maker_fill_is_drained_with_claim_and_spend():
    """sync_kalshi_fills enqueues the fill event; the next sweep drains
    it: copied counted, day spend recorded, position claimed back."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    led.set_state("kcopy_fill_events", {"events": [
        {"ticker": "KXWNBAGAME-26AUG04DALCHI-DAL", "qty": 20.0,
         "price": 0.50, "asset": "42", "whale": "RN1"}]})
    claims = []
    ka = _MakerKalshi(0.51)
    st = sweep(kalshi=ka, ledger=led, identities=[], live=True,
               on_copied=lambda a, t, w: claims.append((a, t, w)))
    assert st["copied"] == 1 and st.get("copied_maker") == 1
    assert st["spent"] == 10.0
    assert claims == [("42", "KXWNBAGAME-26AUG04DALCHI-DAL", "RN1")]
    assert (led.get_state("kcopy_fill_events") or {}).get("events") == []


def test_rn1_is_exempt_from_the_day_budget_and_does_not_eat_it():
    """Owner directive 2026-08-10: no day cap for RN1 on Kalshi. RN1
    copies past the budget, AND his spend must not consume the budget
    the capped whales are governed by."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(0.48)
    rn1 = {**_ROW, "whale": "RN1"}
    # Budget of $50: one $150 RN1 clip blows straight past it.
    st = sweep(kalshi=ka, ledger=led, identities=[rn1], live=True,
               day_usd=150.0)
    assert st["copied"] == 1 and st.get("skipped_day_cap") is None
    assert ka.orders[0][2] == 312          # full $150 clip placed
    # A capped whale on a FRESH matchup still has the whole $150 budget:
    # RN1's ~$99.84 spend is excluded from the cap arithmetic.
    ka2 = _Kalshi(0.48, outcomes={"Atlanta Dream": "T-ATL",
                                  "Indiana Fever": "T-IND"})
    hrh = {**_ROW, "slug": "wnba-atl-ind-2026-08-04",
           "outcome": "Atlanta Dream"}
    st2 = sweep(kalshi=ka2, ledger=led, identities=[hrh], live=True,
                day_usd=150.0)
    assert st2["copied"] == 1 and st2.get("skipped_day_cap") is None


def test_day_budget_zero_means_no_cap_at_all():
    """Owner 2026-08-10: EDGE_KCOPY_DAY_USD=0 disables the day cap for
    every whale — validated signals are never skipped on budget."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(0.48, outcomes={"Atlanta Dream": "T-ATL",
                                 "Indiana Fever": "T-IND"})
    hrh = {**_ROW, "slug": "wnba-atl-ind-2026-08-04",
           "outcome": "Atlanta Dream"}
    st = sweep(kalshi=ka, ledger=led, identities=[hrh], live=True,
               day_usd=0.0)
    assert st["copied"] == 1 and st.get("skipped_day_cap") is None


class _GuardedKalshi(_Kalshi):
    """Fake with the venue-portfolio surface (the real adapter's
    open_ticker_map): what the VENUE says we hold and have resting."""

    def __init__(self, ask, positions=None, resting_buys=None,
                 unavailable=False, **kw):
        super().__init__(ask, **kw)
        self.positions = set(positions or [])
        self.resting_buys = dict(resting_buys or {})
        self.unavailable = unavailable
        self.cancelled = []

    def open_ticker_map(self):
        if self.unavailable:
            return None
        return {"positions": set(self.positions),
                "resting_buys": {t: list(v)
                                 for t, v in self.resting_buys.items()}}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return "cancelled"


def test_venue_held_position_blocks_the_copy_after_ledger_amnesia():
    """Incident 2026-08-11: deploys wiped the sqlite ledger, so claims
    and the ledger-side never-add went blind while the venue still held
    the position — the same dog was re-copied every boot to $606. The
    VENUE's portfolio must refuse the buy even with an empty ledger."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")  # fresh = post-deploy
    ka = _GuardedKalshi(0.48, positions={"T-DAL"})
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True)
    assert st.get("skipped_held_event") == 1
    assert not ka.orders, "venue-held ticker re-bought after ledger wipe"


def test_venue_resting_buy_blocks_the_copy_too():
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _GuardedKalshi(0.48, resting_buys={"T-DAL": ["o-orphan"]})
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True)
    assert st.get("skipped_held_event") == 1
    assert not ka.orders


def test_unreadable_venue_portfolio_refuses_all_buys():
    """Fail CLOSED: if the venue's answer cannot be read, the sweep
    places no buys at all rather than trusting the amnesiac ledger."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _GuardedKalshi(0.48, unavailable=True)
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True)
    assert st.get("venue_guard_unavailable") == 1
    assert st.get("skipped_no_guard") == 1
    assert st["copied"] == 0 and not ka.orders


def test_orphan_resting_buys_are_cancelled_once_per_boot():
    """A resting BUY the venue holds with no ledger context is a
    pre-deploy orphan: cancel it before it fills as an unclaimed,
    unattributed position. An order THIS boot placed keeps resting."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    led.set_state("kalshi_order:o-mine", {"market_key": "kalshi:T-X"})
    ka = _GuardedKalshi(
        0.48, resting_buys={"T-X": ["o-mine"], "T-ORPHAN": ["o-orphan"]})
    sweep(kalshi=ka, ledger=led, identities=[], live=True)
    assert ka.cancelled == ["o-orphan"]
    # Second sweep same process: reconcile must not re-run.
    sweep(kalshi=ka, ledger=led, identities=[], live=True)
    assert ka.cancelled == ["o-orphan"]


def test_venue_guard_lets_a_fresh_copy_through():
    """The guard blocks re-buys, not first buys: clean venue portfolio,
    clean ledger -> the copy places normally."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _GuardedKalshi(0.48)
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True)
    assert st["copied"] == 1
    assert ka.orders == [("T-DAL", 0.48, 312)]


def test_thin_book_is_never_taken():
    """Liquidity floor (owner approval 2026-08-11): a top-of-book showing
    less than HALF our size is a market our own taker order would move —
    the overnight thin-book bleed. $150 at 48c wants 312 contracts; a
    book showing 130 is skipped, one showing 170 trades."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(0.48, ask_size=130)
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True)
    assert st.get("skipped_thin_book") == 1
    assert not ka.orders
    led2 = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka2 = _Kalshi(0.48, ask_size=170)
    st2 = sweep(kalshi=ka2, ledger=led2, identities=[dict(_ROW)], live=True)
    assert st2["copied"] == 1


def test_opposite_side_of_a_held_event_is_refused():
    """Owner directive 2026-08-11 evening (the Halys+Kwon guaranteed-
    loss pairs): copies NEVER take an offsetting position. A venue-held
    position on the OTHER side of the event refuses the copy outright —
    no pair-completion math, no local-ledger dependence. Locked pairs
    are the arb sleeve's job."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")  # amnesiac
    ka = _GuardedKalshi(
        0.30,
        outcomes={"Dallas Wings": "KXWNBAGAME-26AUG04DALCHI-DAL",
                  "Chicago Sky": "KXWNBAGAME-26AUG04DALCHI-CHI"},
        positions={"KXWNBAGAME-26AUG04DALCHI-CHI"})  # we hold Chicago
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True)
    assert st.get("skipped_held_event") == 1
    assert not ka.orders, "bought the opposite side of a held event"
