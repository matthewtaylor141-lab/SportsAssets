"""Pins for the 2026-08-04 audit fixes: even-count median interpolation,
overround sanity band, and the FOK limit placed at the worst price that
still clears the bar (not the observed ask)."""

import tempfile

import pytest

from edge.fairvalue.feed import _weighted_median


def test_even_count_median_interpolates_instead_of_taking_the_short_side():
    # Two equally-weighted anchors: the old code returned 1.90 (the shorter
    # odds) on every outcome, inflating implied probabilities on both sides.
    assert _weighted_median([(1.90, 3.0), (2.00, 3.0)]) == pytest.approx(1.95)
    # Odd counts and dominant weights keep plain median behaviour.
    assert _weighted_median([(1.90, 3.0), (2.00, 3.0), (2.10, 3.0)]) == 2.00
    assert _weighted_median([(1.90, 1.0), (2.00, 5.0)]) == 2.00


def test_underround_pairs_are_refused_before_devig():
    """sum(1/odds) < 0.99 across mixed books is an artifact, not a market —
    power de-vig would push the exponent above 1 and exaggerate favourites."""
    import pathlib
    import time

    from edge.fairvalue.feed import FeedEvent
    from tests.test_run_cycle_e2e import POLICY, StubFeed, StubVenue, _rig
    from edge.shadow.runner import run_cycle

    ledger, risk = _rig(pathlib.Path(tempfile.mkdtemp()))
    ev = FeedEvent(
        sport_key="soccer_epl", league_code="epl", home="Arsenal",
        away="Chelsea", commence_ts=time.time() + 3600,
        h2h={"Arsenal": 2.30, "Chelsea": 2.30},   # sum(1/o) = 0.87
        fetched_at=time.time(), anchors=1)
    funnel = run_cycle([StubVenue(ask_price=0.30)], StubFeed([ev]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert ledger.summary()["fills"] == 0
    assert funnel.get("overround_rejected", {}).get("2way", 0) >= 1


def test_fok_limit_is_the_worst_price_that_still_clears_the_bar():
    from edge.execution.executor import execute
    from edge.ledger.service import Ledger

    class _Adapter:
        name = "polymarket-us"

        def __init__(self):
            self.orders = []

        def taker_fee(self, price):
            return 0.0

        def place_order(self, slug, price, qty, **kw):
            self.orders.append({"slug": slug, "price": price, "qty": qty})
            return {"ok": True, "count": qty, "price": price,
                    "order_id": "o1", "status": "filled"}

    a = _Adapter()
    led = Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")
    res = execute(adapter=a, ledger=led, mode="LIVE_BETA",
                  mkey="polymarket-us:tok", league="mlb",
                  ask_price=0.50, ask_size=100, size_usd=2.0,
                  edge=0.05, threshold=0.02, decision={}, ts=1.0,
                  entry_price=0.50, taker=True)
    assert res["placed"]
    # entry 0.50 with 3c of slack above the bar -> limit 0.53, never 0.50.
    assert a.orders[0]["price"] == pytest.approx(0.53)
    # A book uptick to 0.52 now fills (venue matches at the real ask); a
    # move past 0.53 kills. Either way we never pay above the bar.


def test_consensus_depth_is_per_outcome_not_per_event():
    """A deep alt rung quoted by one soft book must not inherit the
    moneyline's Pinnacle anchor (the winner's-curse hole)."""
    from edge.fairvalue.feed import _sample_depth

    ml_sample = [(1.95, 3.0, "pinnacle"), (1.96, 3.0, "betfair_ex_eu"),
                 (1.94, 1.0, "lowvig")]
    thin_rung = [(4.10, 1.0, "lowvig")]
    assert _sample_depth(ml_sample) == (3, 2)
    assert _sample_depth(thin_rung) == (1, 0)
    # Legacy two-element samples (no book tag) count zero rather than lie.
    assert _sample_depth([(1.95, 3.0)]) == (0, 0)


def test_live_orders_route_to_the_venue_with_the_better_price(monkeypatch):
    """Owner 2026-08-04: same bet on both venues -> take the better price,
    never both. Two venues list Arsenal ML; the cheaper ask wins the one
    live order and the other listing is refused as routed_better_price."""
    import pathlib
    import time as _t

    from edge.fairvalue.feed import FeedEvent
    from edge.execution.risk import RiskManager
    from edge.ledger.service import Ledger
    from edge.shadow.runner import run_cycle
    from tests.test_run_cycle_e2e import POLICY, StubFeed, StubVenue

    class LiveVenue(StubVenue):
        def __init__(self, name, ask_price):
            super().__init__(ask_price)
            self.name = name
            self.orders = []

        def get_book(self, market_id, token):
            import time as _tt

            from edge.venues.base import BookLevel, MarketBook

            # Chelsea priced ABOVE fair on both venues: no edge there and
            # no sub-$1 outcome set, so the dutch-book path stays quiet
            # and the only order is the routed Arsenal one.
            px = self._ask.price if token == "T-ARS" else 0.56
            return MarketBook(venue=self.name, market_id=market_id,
                              outcome_id=token,
                              bids=[BookLevel(px - 0.02, 500)],
                              asks=[BookLevel(px, 1000)], ts=_tt.time())

        def has_credentials(self):
            return True

        def plan_maker_order(self, limit_price, best_ask, edge, threshold):
            return round(best_ask, 2), True   # cross; fee is zero in stubs

        def place_order(self, token, price, qty, **kw):
            self.orders.append({"token": token, "price": price, "qty": qty})
            return {"ok": True, "count": qty, "price": price,
                    "order_id": f"{self.name}-{len(self.orders)}",
                    "status": "filled"}

    monkeypatch.setenv("EDGE_STRATEGY_LIVE", "1")
    monkeypatch.setenv("EDGE_LIVE_VENUES", "kalshi,polymarket-us")
    tmp = pathlib.Path(__import__("tempfile").mkdtemp())
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp))
    ledger = Ledger(db_path=str(tmp / "l.sqlite3"))
    risk = RiskManager(ledger, {**POLICY.risk, "mode": "LIVE_BETA"})
    ev = FeedEvent(
        sport_key="soccer_epl", league_code="epl", home="Arsenal",
        away="Chelsea", commence_ts=_t.time() + 3600,
        h2h={"Arsenal": 1.90, "Chelsea": 1.90},   # fair 0.50 each
        fetched_at=_t.time(), anchors=1)
    rich = LiveVenue("polymarket-us", ask_price=0.47)
    cheap = LiveVenue("kalshi", ask_price=0.45)
    funnel = run_cycle([rich, cheap], StubFeed([ev]), POLICY, risk,
                       ledger, ["soccer_epl"])
    placed = [(v.name, o) for v in (rich, cheap) for o in v.orders]
    assert len(placed) >= 1, "the routed winner must actually order"
    assert all(name == "kalshi" for name, _ in placed), \
        "every order must go to the better-priced venue"
    rt = funnel.get("routing") or {}
    assert rt.get("contested", 0) >= 1
    assert rt.get("to", {}).get("kalshi", 0) >= 1
    assert funnel["rejects"].get("routed_better_price", 0) >= 1


def test_quarantined_band_paper_logs_instead_of_paying(monkeypatch):
    """10-15c measured -55% on 113 settled: live entries there demote to
    PAPER (study continues, money stops) until a fresh cohort clears it."""
    import pathlib
    import tempfile
    import time as _t

    from edge.fairvalue.feed import FeedEvent
    from edge.execution.risk import RiskManager
    from edge.ledger.service import Ledger
    from edge.shadow.runner import run_cycle
    from tests.test_run_cycle_e2e import POLICY, StubFeed, StubVenue

    class LiveVenue(StubVenue):
        name = "polymarket-us"

        def __init__(self, ask):
            super().__init__(ask)
            self.orders = []

        def has_credentials(self):
            return True

        def place_order(self, token, price, qty, **kw):
            self.orders.append((token, price, qty))
            return {"ok": True, "count": qty, "price": price,
                    "order_id": "o", "status": "filled"}

    monkeypatch.setenv("EDGE_STRATEGY_LIVE", "1")
    monkeypatch.setenv("EDGE_LIVE_VENUES", "polymarket-us")
    tmp = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp))
    ledger = Ledger(db_path=str(tmp / "l.sqlite3"))
    policy = POLICY
    policy.risk = {**policy.risk, "band_quarantine": [[0.10, 0.15]]}
    risk = RiskManager(ledger, {**policy.risk, "mode": "LIVE_BETA"})
    # fair ~0.185 (5.4 odds two-way with 1.9) -> ask 0.12 clears the bar
    ev = FeedEvent(
        sport_key="soccer_epl", league_code="epl", home="Arsenal",
        away="Chelsea", commence_ts=_t.time() + 3600,
        h2h={"Arsenal": 6.20, "Chelsea": 1.20},
        fetched_at=_t.time(), anchors=2)
    venue = LiveVenue(0.12)
    funnel = run_cycle([venue], StubFeed([ev]), policy, risk,
                       ledger, ["soccer_epl"])
    assert not venue.orders, "no live order inside a quarantined band"
    assert funnel.get("band_quarantined", {}).get("0.10-0.15", 0) >= 1


def test_kalshi_city_dialect_matches_at_the_bar():
    """Census 2026-08-04: Kalshi speaks city + disambiguation letter."""
    from edge.venues.mapper import team_score

    # City-only (already worked) and the disambiguation-letter forms:
    assert team_score("Baltimore", "Baltimore Orioles") >= 0.95
    assert team_score("Golden State", "Golden State Valkyries") >= 0.95
    assert team_score("Los Angeles A", "Los Angeles Angels") >= 0.95
    assert team_score("New York Y", "New York Yankees") >= 0.95
    assert team_score("A's", "Athletics") >= 0.95
    # The letter must DISCRIMINATE — wrong team of a shared city refuses.
    assert team_score("Los Angeles A", "Los Angeles Dodgers") < 0.95
    assert team_score("New York Y", "New York Mets") < 0.95
    # A lone letter with no shared anchor never matches.
    assert team_score("A", "Athletics") < 0.95
