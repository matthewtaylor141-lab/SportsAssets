"""The Kalshi copy leg: his price +2%, once per position (ACROSS venues),
day-capped, strict name join, $3 per copy uniform (owner directive
2026-08-05) — and routed to the best-priced venue at placement."""

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
                          asks=[BookLevel(self.ask, 60)], ts=time.time())

    def place_order(self, ticker, price, count, **kw):
        self.orders.append((ticker, price, count))
        return {"ok": True, "count": count, "price": price,
                "status": "filled"}


_ROW = {"slug": "wnba-dal-chi-2026-08-04", "outcome": "Dallas Wings",
        "price": 0.50, "whale": "HomeRunHazard", "entered_ts": time.time() - 60}


def _run(ask, rows=None, live=True, led=None):
    led = led or Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _Kalshi(ask)
    st = sweep(kalshi=ka, ledger=led, identities=rows or [dict(_ROW)],
               live=live)
    return st, ka, led


def test_limit_is_his_price_plus_two_percent_min_one_tick():
    assert _limit_for(0.50) == 0.51
    assert _limit_for(0.30) == 0.31, "sub-50c copies get a full tick"
    assert _limit_for(0.97) == 0.99
    assert _limit_for(0.985) == 0.99


def test_copies_when_kalshi_is_inside_his_tolerance():
    st, ka, led = _run(0.48)   # eff = 0.48 + fee(0.0175) <= limit 0.51
    assert st["matched"] == 1 and st["copied"] == 1
    assert ka.orders == [("T-DAL", 0.48, 10)]   # $5 -> 10 contracts
    assert led.get_state("kcopy:wnba-dal-chi-2026-08-04:Dallas Wings")


def test_outside_tolerance_is_not_a_copy():
    st, ka, _ = _run(0.505)  # his 0.50: eff 0.505+fee > limit 0.51
    assert st["matched"] == 1 and st["copied"] == 0
    assert not ka.orders


def test_sport_assignments_gate_the_sweep():
    """Owner directive 2026-08-06: cell-gated whales copy ONLY their
    assigned sport(s) — swisstony is soccer-only, so his WNBA identity
    is refused; the WNBA assignee (HomeRunHazard, the default fixture
    whale) trades. RN1 is UNRESTRICTED (owner decision the same
    evening, on the live sleeve's own +$170.95 record) and copies
    anywhere."""
    row = {**_ROW, "whale": "swisstony"}
    st, ka, _ = _run(0.48, rows=[row])
    assert st.get("skipped_sport") == 1
    assert not ka.orders
    st2, ka2, _ = _run(0.48)     # HomeRunHazard + wnba ML: assigned cell
    assert ka2.orders == [("T-DAL", 0.48, 10)]   # $5 -> 10 contracts
    row3 = {**_ROW, "whale": "RN1"}
    st3, ka3, _ = _run(0.48, rows=[row3])
    assert st3["copied"] == 1
    # RN1 clips at $100 on Kalshi (owner directive 2026-08-10):
    # floor(100/0.48) = 208 contracts.
    assert ka3.orders == [("T-DAL", 0.48, 208)]


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


def test_smoke_order_fires_exactly_once_ever():
    import os
    os.environ["EDGE_STRATEGY_LIVE"] = "1"   # smoke is engine-class
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


def test_smoke_zero_fill_is_not_done():
    import os
    os.environ["EDGE_STRATEGY_LIVE"] = "1"   # smoke is engine-class
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


def test_pmus_better_priced_routes_the_copy_away_from_kalshi(monkeypatch):
    """LEGACY routing (EDGE_KCOPY_PREFER_KALSHI=0): Kalshi ask 0.48 ->
    eff ~0.4975 fee-loaded; PMUS shows 0.49. PMUS is the better venue
    at placement, so the fast leg owns it."""
    monkeypatch.setenv("EDGE_KCOPY_PREFER_KALSHI", "0")
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka, pm = _Kalshi(0.48), _Pmus(0.49)
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True,
               pmus=pm)
    assert st.get("routed_pmus_better") == 1
    assert st["copied"] == 0 and not ka.orders
    # The peek resolved his side structurally: atc grammar, 'bal' code.
    assert pm.peeked == ["atc-wnba-dal-chi-2026-08-04-dal"]


def test_kalshi_keeps_the_copy_by_default_when_it_qualifies():
    """DEFAULT routing (owner 2026-08-09: "I need there to be volume on
    Kalshi"): a copy that clears every Kalshi gate places on Kalshi even
    when PMUS shows a marginally better ask — no deference, no peek."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka, pm = _Kalshi(0.48), _Pmus(0.49)
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True,
               pmus=pm)
    assert st.get("routed_pmus_better") is None
    assert st["copied"] == 1 and ka.orders == [("T-DAL", 0.48, 10)]


def test_kalshi_places_when_it_is_cheaper_fee_loaded():
    """PMUS 0.55 vs Kalshi eff ~0.4975: Kalshi is the best-priced venue
    at placement, so the $3 copy lands there exactly as before."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka, pm = _Kalshi(0.48), _Pmus(0.55)
    st = sweep(kalshi=ka, ledger=led, identities=[dict(_ROW)], live=True,
               pmus=pm)
    assert st.get("routed_pmus_better") is None
    assert st["copied"] == 1
    assert ka.orders == [("T-DAL", 0.48, 10)]


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
    assert st["copied"] == 1 and ka.orders == [("T-DAL", 0.48, 10)]


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


def test_kalshi_first_assets_skip_the_pmus_deference():
    """The PMUS executor deferred this asset here by the venue split —
    a marginally better PMUS ask must not bounce it back. The price
    gates (his+2% fee-loaded, floors, collapse) are the pricing bar."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka, pm = _Kalshi(0.48), _Pmus(0.49)
    st = sweep(kalshi=ka, ledger=led,
               identities=[{**_ROW, "asset": _asset(True)}],
               live=True, pmus=pm)
    assert not pm.peeked, "kalshi-first assets must not consult PMUS"
    assert st["copied"] == 1 and ka.orders == [("T-DAL", 0.48, 10)]


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
    assert claims == [("777", "T-DAL", "HomeRunHazard")]


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


def test_taker_priced_out_rests_a_fee_free_maker_on_kalshi_first():
    """At his 0.50 the limit is 0.51; ask 0.51 + fee ≈ 0.527 > limit, so
    the taker path refuses — the maker path must rest at the limit (one
    tick inside the ask), GTC, without burning the once-ever claim."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _MakerKalshi(0.51)
    row = {**_ROW, "asset": _asset(True)}
    st = sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert st["copied"] == 0                       # nothing filled yet
    assert st.get("maker_rested") == 1
    # HRH clips $5 (default tier): floor(5/0.50) = 10 contracts, maker.
    assert ka.orders == [("T-DAL", 0.50, 10, False)]
    claim = "kcopy:wnba-dal-chi-2026-08-04:Dallas Wings"
    assert not led.get_state(claim), "claim burns on FILL, not on rest"
    # Second sweep while the rest is live: no duplicate order.
    st2 = sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert st2.get("maker_resting") == 1
    assert len(ka.orders) == 1


def test_stale_rest_reprices_only_on_confirmed_cancel():
    """The book ran away from a resting bid (ask moved >2c above our
    price): cancel and requote at the fresh book. 'gone'/'error' cancel
    results must NOT repost — a filled-then-reposted rest is a doubled
    copy, an ambiguous one is a stacked order."""
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    ka = _MakerKalshi(0.51)
    ka.cancel_results = ["cancelled"]
    ka.cancels = []
    ka.cancel_order = lambda oid: (ka.cancels.append(oid) or
                                   ka.cancel_results.pop(0))
    row = {**_ROW, "asset": _asset(True)}
    sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert len(ka.orders) == 1                     # initial rest at 0.50
    # Book moves a step: ask 0.53, our 0.50 bid is >2c behind but the
    # his+2% limit (0.51) can still sit within 2c — cancel and requote.
    ka.ask = 0.53
    st = sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert st.get("reprice_cancelled") == 1 and ka.cancels
    assert len(ka.orders) == 2                     # requoted fresh
    assert ka.orders[1][1] == 0.51                 # his 0.50 + 2% limit
    # Book RUNS AWAY (0.56): cancel the stale rest but never chase — the
    # limit can't sit competitively, so no repost (dead-rest rule).
    ka.cancel_results.append("cancelled")
    ka.ask = 0.56
    st_run = sweep(kalshi=ka, ledger=led, identities=[row], live=True)
    assert st_run.get("reprice_cancelled") == 1
    assert st_run.get("skipped_dead_rest") == 1
    assert len(ka.orders) == 2                     # no chase
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
